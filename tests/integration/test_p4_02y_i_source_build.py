"""P4-02Y-I §6: 开始建库 / 来源级进度 / 删除来源, all through the real product entries.

The harness is the P4-02Y one (a ``%TEMP%`` library, the production scanner, the production
semantic build and the real HTTP application with one deterministic stand-in adapter for the
ADR-0003 §7 model boundary). Everything else - the refresh entry, the build mode, the durable
progress projection and the source deletion - is the shipped code path.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from conftest import write_jpeg
from httpx import AsyncClient

from integration.test_p4_02y_library_refresh import (
    MANIFEST,
    RefreshEnv,
    StubPinnedAdapter,
    asset_of,
    query_vector_for,
)
from photo_search.face import contract as face_contract
from photo_search.jobs import queue as jobs
from photo_search.refresh import pipeline
from photo_search.refresh.progress import source_progress


# ------------------------------------------------------------------------------- helpers
def register_source(env: RefreshEnv, display_name: str, root: Path) -> int:
    async def scenario(client: AsyncClient) -> dict:
        csrf = await env.authenticate(client)
        response = await client.post(
            "/api/v1/sources",
            json={"display_name": display_name, "root_path": str(root)},
            headers=env.post_headers(csrf),
        )
        assert response.status_code == 201, response.text
        return response.json()

    return int(env.scenario(scenario)["source_id"])


def start_run(env: RefreshEnv, *, mode: str, source_ids: list[int] | None = None) -> dict:
    async def scenario(client: AsyncClient) -> dict:
        csrf = await env.authenticate(client)
        body: dict[str, Any] = {"mode": mode}
        if source_ids is not None:
            body["source_ids"] = list(source_ids)
        response = await client.post(
            "/api/v1/library/refresh", json=body, headers=env.post_headers(csrf)
        )
        assert response.status_code == 202, response.text
        return response.json()

    return env.scenario(scenario)


def run(
    env: RefreshEnv,
    *,
    mode: str,
    source_ids: list[int] | None = None,
    timeout: float = 180.0,
) -> dict:
    started = start_run(env, mode=mode, source_ids=source_ids)
    refresher = env.context.refresher()
    assert refresher.wait_idle(timeout=timeout), "the run did not finish in time"
    status = refresher.status()
    assert status["refresh_id"] == started["refresh_id"]
    assert status["state"] in ("COMPLETE", "PARTIAL_FAILED", "FAILED"), status
    return status


def source_payload(env: RefreshEnv, source_id: int) -> dict:
    async def scenario(client: AsyncClient) -> dict:
        await env.authenticate(client)
        response = await client.get(f"/api/v1/sources/{source_id}")
        assert response.status_code == 200, response.text
        return response.json()

    return env.scenario(scenario)


def delete_source(env: RefreshEnv, source_id: int) -> tuple[int, dict]:
    async def scenario(client: AsyncClient) -> tuple[int, dict]:
        csrf = await env.authenticate(client)
        response = await client.delete(
            f"/api/v1/sources/{source_id}", headers=env.post_headers(csrf)
        )
        return response.status_code, response.json()

    return env.scenario(scenario)


def directory_summary(root: Path) -> dict[str, tuple[int, int]]:
    """``relative path -> (size, mtime_ns)`` of every file, for the "0 修改" acceptance."""
    summary: dict[str, tuple[int, int]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        info = path.stat()
        summary[str(path.relative_to(root))] = (int(info.st_size), int(info.st_mtime_ns))
    return summary


def scan_rows(env: RefreshEnv) -> list[dict]:
    return [
        dict(row)
        for row in env.service().catalog.read_all(
            "SELECT scan_id, source_id, state FROM scan_runs ORDER BY scan_id"
        )
    ]


def processor_jobs(env: RefreshEnv, task_type: str) -> int:
    row = env.service().catalog.read_one(
        "SELECT COUNT(*) AS n FROM jobs WHERE task_type = ?", (str(task_type),)
    )
    return int(row["n"])


# ------------------------------------------------------------- §1/§2 one click builds a source
def test_global_build_request_builds_every_registered_synthetic_source(tmp_path: Path) -> None:
    env = RefreshEnv.build(tmp_path, photos=1, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        for source_index in (2, 3):
            root = tmp_path / f"library-{source_index}"
            write_jpeg(root / f"photo-{source_index}.jpg", noise=source_index + 40)
            register_source(env, f"library-{source_index}", root)

        # No source_ids are sent: the product executes the all-sources build scope.
        status = run(env, mode="build")

        assert status["counters"]["sources_requested"] == 3
        assert status["counters"]["sources_scanned"] == 3
        assert {item["source_id"] for item in status["sources"]} == {1, 2, 3}
    finally:
        env.close()


def test_one_build_click_on_a_new_source_scans_indexes_and_searches(tmp_path: Path) -> None:
    env = RefreshEnv.build(tmp_path, photos=2, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        fresh = tmp_path / "fresh-library"
        for index in range(3):
            write_jpeg(fresh / f"fresh-{index:02d}.jpg", noise=300 + index)
        source_id = register_source(env, "fresh", fresh)

        # §1: a freshly registered source shows "未建库" - no successful scan and no classified
        # image - so the page offers "开始建库" instead of a progress bar.
        before = source_payload(env, source_id)
        assert before["last_successful_scan"] is None
        assert before["progress"]["built"] is False
        assert before["progress"]["stages"]["semantic"]["state"] == "UNBUILT"
        assert before["progress"]["scan"]["files"] == 0

        env.adapter.vectors["fresh-00"] = query_vector_for(fresh / "fresh-00.jpg")
        status = run(env, mode="build", source_ids=[source_id])

        # One click: the build's fast scan confirmed every file (there is no separate full scan
        # in front of it), the semantic index caught up, and the new photo is searchable.
        assert status["mode"] == "build"
        assert status["state"] == "COMPLETE"
        assert status["search_ready"] is True
        assert status["search_ready_basis"] == "semantic-backlog-empty"
        assert status["counters"]["sources_requested"] == 1
        assert status["counters"]["hash_targets"] == 3
        assert status["counters"]["new_assets"] == 3
        assert status["stages"]["semantic"]["classified"] == 3
        assert status["stages"]["semantic"]["backlog_total"] == 3
        assert status["stages"]["semantic"]["backlog_confirmed"] == 3
        assert status["stages"]["semantic"]["backlog_pending"] == 0
        per_source = [row["source_id"] for row in scan_rows(env)]
        assert per_source.count(source_id) == 1, per_source
        found = env.search("fresh-00")
        assert [int(item["asset_id"]) for item in found["hits"]][:1] == [
            asset_of(env, "fresh-00.jpg")
        ]

        after = source_payload(env, source_id)
        assert after["last_successful_scan"] is not None
        assert after["progress"]["built"] is True
        assert after["progress"]["search_ready"] is True
        assert after["progress"]["stages"]["semantic"]["state"] == "SEARCHABLE"
        assert after["progress"]["scan"] == {"files": 3, "present": 3, "missing": 0}
    finally:
        env.close()


def test_the_source_card_counts_only_images_in_the_semantic_denominator(tmp_path: Path) -> None:
    """§3: the durable denominator is the classified image set, so documents cannot stall it."""
    env = RefreshEnv.build(tmp_path, photos=2, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        notes = env.library / "notes.txt"
        notes.write_text("not a photo", encoding="utf-8")
        os.utime(notes, ns=((1_700_000_000_000 - 3_600_000) * 1_000_000,) * 2)

        status = run(env, mode="build", source_ids=[1])

        assert status["search_ready"] is True
        assert status["stages"]["semantic"]["classified"] == 2
        assert status["stages"]["semantic"]["out_of_scope"]["labels"] == {"UNIDENTIFIED": 1}
        card = source_payload(env, 1)["progress"]
        # The scan denominator counts every file; the semantic one counts the two images only.
        assert card["scan"]["files"] == 3
        assert card["stages"]["semantic"]["total"] == 2
        assert card["stages"]["semantic"]["confirmed"] == 2
        assert card["stages"]["semantic"]["pending"] == 0
        assert card["stages"]["semantic"]["state"] == "SEARCHABLE"
    finally:
        env.close()


def test_the_progress_of_an_unavailable_processor_is_reported_not_guessed(tmp_path: Path) -> None:
    """§3: a machine without face/OCR weights must say "不可用" instead of "处理中" for ever."""
    env = RefreshEnv.build(tmp_path, photos=2, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        status = run(env, mode="build", source_ids=[1])
        assert status["state"] == "COMPLETE"

        stages = source_payload(env, 1)["progress"]["stages"]
        assert stages["face"]["state"] == "UNAVAILABLE"
        assert stages["ocr"]["state"] == "UNAVAILABLE"
        assert stages["place"]["state"] == "UNAVAILABLE"
        # The two standard-library readers ran over the whole scope and finished it.
        assert stages["taken_time"]["state"] == "COMPLETE"
        assert stages["gps"]["state"] == "COMPLETE"
        assert stages["taken_time"]["handled"] == 2
    finally:
        env.close()


def test_a_new_source_never_reports_a_finished_analysis_stage(tmp_path: Path) -> None:
    """§3: a source that has run no build task must show 未开始 - never 完成.

    A freshly registered source has nothing in scope, and "0 of 0 assets handled" used to be
    rendered as ``COMPLETE``; a source that had only been scanned showed ``IN_PROGRESS`` even
    though no analysis task existed. Both are now derived from the durable evidence of what
    really ran: never started / waiting / running / finished / unavailable.
    """
    (tmp_path / "library").mkdir(parents=True, exist_ok=True)
    env = RefreshEnv.build(tmp_path, photos=0, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        fresh = tmp_path / "fresh"
        write_jpeg(fresh / "fresh-0.jpg", noise=810)
        source_id = register_source(env, "fresh", fresh)

        # 1. registered only: nothing was ever scanned, and no stage has any recorded work.
        stages = source_payload(env, source_id)["progress"]["stages"]
        assert stages["semantic"]["state"] == "UNBUILT"
        for name in ("taken_time", "gps"):
            assert stages[name]["state"] == "NOT_STARTED", (name, stages[name])
            assert stages[name]["handled"] == 0
            assert stages[name]["attempted"] == 0
        for name in ("place", "face", "ocr"):
            assert stages[name]["state"] == "UNAVAILABLE"
        assert "COMPLETE" not in {stage["state"] for stage in stages.values()}

        # 2. a complete-verification scan alone is still not analysis work.
        env.service().run_scan(source_id)
        scanned = source_payload(env, source_id)["progress"]
        assert scanned["scan"]["files"] == 1
        assert scanned["built"] is False
        assert scanned["stages"]["taken_time"]["state"] == "NOT_STARTED"
        assert scanned["stages"]["gps"]["state"] == "NOT_STARTED"
        assert scanned["stages"]["taken_time"]["total"] == 1

        # 3. one "开始建库" runs every available stage over the scope: now they may finish.
        assert run(env, mode="build", source_ids=[source_id])["search_ready"] is True
        built = source_payload(env, source_id)["progress"]["stages"]
        assert built["taken_time"]["state"] == "COMPLETE"
        assert built["gps"]["state"] == "COMPLETE"
        assert built["taken_time"]["handled"] == 1

        # 4. content added afterwards is real outstanding work: neither "处理中" (nothing is
        #    queued) nor "完成" (it has not run for this revision).
        write_jpeg(fresh / "later.jpg", noise=811)
        env.service().run_scan(source_id)
        later = source_payload(env, source_id)["progress"]["stages"]
        assert later["taken_time"]["total"] == 2
        assert later["taken_time"]["handled"] == 1
        assert later["taken_time"]["state"] == "PENDING"
        assert later["gps"]["state"] == "PENDING"
    finally:
        env.close()


def test_face_progress_includes_auto_detail_jobs_without_double_counting_assets(
    tmp_path: Path,
) -> None:
    env = RefreshEnv.build(tmp_path, photos=3, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        service = env.service()
        catalog = service.catalog
        assets = {
            int(row["asset_id"]): str(row["sha256"])
            for row in catalog.read_all(
                "SELECT a.asset_id, a.sha256 FROM assets AS a "
                "JOIN file_locations AS f ON f.asset_id=a.asset_id "
                "WHERE f.source_id=1 AND f.availability='PRESENT' ORDER BY a.asset_id"
            )
        }
        assert len(assets) == 3

        def add_job(asset_id: int, task_type: str, revision: str, state: str) -> int:
            with catalog.write() as connection:
                job_id = jobs.enqueue(
                    connection,
                    subject_type="asset",
                    subject_id=str(asset_id),
                    input_revision=revision,
                    task_type=task_type,
                    processor_id=face_contract.FACE_PROCESSOR_ID,
                    now_ms=catalog.now_ms(),
                )
                connection.execute("UPDATE jobs SET state=? WHERE job_id=?", (state, job_id))
            return job_id

        first, second, third = assets
        add_job(first, face_contract.FACE_TASK_TYPE, assets[first], "SUCCEEDED")
        first_auto = add_job(
            first, face_contract.FACE_DETAIL_AUTO_TASK_TYPE, assets[first], "QUEUED"
        )
        add_job(second, face_contract.FACE_DETAIL_AUTO_TASK_TYPE, assets[second], "SUCCEEDED")
        add_job(
            third,
            face_contract.FACE_DETAIL_AUTO_TASK_TYPE,
            assets[third] + "-old-revision",
            "SUCCEEDED",
        )

        progress = source_progress(
            catalog, source_id=1, binding=None, available={"face": True}
        )["stages"]["face"]
        assert progress == {
            "state": "IN_PROGRESS",
            "handled": 2,
            "pending": 1,
            "attempted": 3,
            "total": 3,
        }

        with catalog.write() as connection:
            connection.execute("UPDATE jobs SET state='SUCCEEDED' WHERE job_id=?", (first_auto,))
        progress = source_progress(
            catalog, source_id=1, binding=None, available={"face": True}
        )["stages"]["face"]
        assert progress["state"] == "PENDING"
        assert progress["handled"] == 2
        assert progress["attempted"] == 3

        third_auto = add_job(
            third, face_contract.FACE_DETAIL_AUTO_TASK_TYPE, assets[third], "QUEUED"
        )
        progress = source_progress(
            catalog, source_id=1, binding=None, available={"face": True}
        )["stages"]["face"]
        assert progress["state"] == "IN_PROGRESS"
        assert progress["pending"] == 1

        with catalog.write() as connection:
            connection.execute("UPDATE jobs SET state='SUCCEEDED' WHERE job_id=?", (third_auto,))
        progress = source_progress(
            catalog, source_id=1, binding=None, available={"face": True}
        )["stages"]["face"]
        assert progress["state"] == "COMPLETE"
        assert progress["handled"] == progress["total"] == 3
    finally:
        env.close()


# ------------------------------------------------------------- §2 bounded build / backlog
def test_a_build_drains_the_backlog_beyond_the_batch_and_delta_ceilings(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """§2/§6.3: a first build must not stop at one batch, nor lose the enrichment delta.

    The ceilings are squeezed to 1-2 items so the *mechanism* is visible on a tiny fixture: the
    semantic batch, the build page and the refresh's in-memory delta bound (which overflows on
    this source and is discarded). The build still finishes the semantic index and every
    enrichment stage, exactly because it works from durable state.
    """
    env = RefreshEnv.build(tmp_path, photos=6, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        monkeypatch.setattr(pipeline, "SEMANTIC_BATCH_LIMIT", 2)
        monkeypatch.setattr(pipeline, "BUILD_PAGE_SIZE", 2)
        monkeypatch.setattr(pipeline, "PROJECTION_BATCH_SIZE", 2)
        monkeypatch.setattr(pipeline, "DELTA_LIMIT", 1)

        status = run(env, mode="build", source_ids=[1])

        assert status["state"] == "COMPLETE"
        assert status["search_ready"] is True
        assert status["stages"]["semantic"]["batches"] >= 3
        assert status["stages"]["semantic"]["encoded"] == 6
        assert status["stages"]["semantic"]["backlog_confirmed"] == 6
        assert status["stages"]["semantic"]["backlog_pending"] == 0
        # Every image was encoded exactly once, however many bounded batches that took.
        assert len(env.adapter.image_calls) == 6
        # The in-memory delta of this run really did overflow its bound and was discarded; the
        # enrichment stages still finished the whole scope from durable state.
        assert status["stages"]["taken_time"]["new_jobs"] == 6
        assert status["stages"]["taken_time"]["pages"] >= 3
        assert status["stages"]["thumbnail"]["generated"] == 6
        assert status["stages"]["gps"]["pages"] >= 3
    finally:
        env.close()


def test_an_interrupted_build_continues_without_re_encoding_confirmed_assets(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """§6.4/§6.6: bounded rounds may stop early; the next build continues and repeats nothing."""
    env = RefreshEnv.build(tmp_path, photos=6, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        monkeypatch.setattr(pipeline, "SEMANTIC_BATCH_LIMIT", 2)
        monkeypatch.setattr(pipeline, "BUILD_ROUND_CAP", 2)

        first = run(env, mode="build", source_ids=[1])

        assert first["state"] == "FAILED"
        assert first["search_ready"] is False
        assert first["search_ready_basis"] == "semantic-backlog"
        assert first["failed_stages"] == ["semantic-backlog"]
        assert first["stages"]["semantic"]["backlog_confirmed"] == 4
        assert first["stages"]["semantic"]["backlog_pending"] == 2
        encoded_first = len(env.adapter.image_calls)
        assert encoded_first == 4
        card = source_payload(env, 1)["progress"]
        assert card["built"] is True
        assert card["search_ready"] is False
        assert card["stages"]["semantic"]["state"] == "BUILDING"
        assert card["stages"]["semantic"]["pending"] == 2

        monkeypatch.setattr(pipeline, "BUILD_ROUND_CAP", 10_000)
        second = run(env, mode="build", source_ids=[1])

        # Only the two remaining images were encoded; the four confirmed ones were reused.
        assert second["state"] == "COMPLETE"
        assert second["search_ready"] is True
        assert second["stages"]["semantic"]["encoded"] == 2
        assert len(env.adapter.image_calls) == encoded_first + 2
        assert source_payload(env, 1)["progress"]["stages"]["semantic"]["state"] == "SEARCHABLE"
    finally:
        env.close()


def test_a_build_catches_up_the_enrichment_backlog_even_with_no_delta(tmp_path: Path) -> None:
    """§2/§6.5: the first build catches up historical stages, not only this round's changes.

    The source was already completely scanned, so the build's fast scan reports an empty content
    delta; the enrichment stages must still consume their durable scope.
    """
    env = RefreshEnv.build(tmp_path, photos=3, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        assert processor_jobs(env, "taken_time_extract") == 0
        status = run(env, mode="build", source_ids=[1])

        assert status["state"] == "COMPLETE"
        assert status["counters"]["content_delta_count"] == 0
        assert status["counters"]["hash_targets"] == 0
        assert status["stages"]["taken_time"]["new_jobs"] == 3
        assert status["stages"]["taken_time"]["assets"] >= 3
        assert status["stages"]["gps"]["new_jobs"] == 3
        assert processor_jobs(env, "taken_time_extract") == 3
        assert processor_jobs(env, "geo_extract") == 3

        # A second build finds nothing left to do: no new jobs, no new work.
        again = run(env, mode="build", source_ids=[1])
        assert again["stages"]["semantic"]["encoded"] == 0
        assert again["stages"]["taken_time"]["new_jobs"] == 0
        assert again["stages"]["gps"]["new_jobs"] == 0
        assert again["stages"]["thumbnail"]["generated"] == 0
    finally:
        env.close()


# ------------------------------------------------------------- §4 the daily action is unchanged
def test_the_daily_source_refresh_still_only_processes_the_change(tmp_path: Path) -> None:
    """§6.7: after the build, the daily button stays a delta pass."""
    env = RefreshEnv.build(tmp_path, photos=3, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        assert run(env, mode="build", source_ids=[1])["search_ready"] is True
        encoded_after_build = len(env.adapter.image_calls)
        write_jpeg(env.library / "one-more.jpg", noise=91)

        daily = run(env, mode="refresh", source_ids=[1])

        assert daily["mode"] == "refresh"
        assert daily["state"] == "COMPLETE"
        assert daily["search_ready"] is True
        assert daily["counters"]["hash_targets"] == 1
        assert daily["counters"]["content_delta_count"] == 1
        assert daily["stages"]["semantic"]["encoded"] == 1
        assert len(env.adapter.image_calls) == encoded_after_build + 1
        assert daily["stages"]["taken_time"]["new_jobs"] == 1
    finally:
        env.close()


# --------------------------------------------------------------------- §5 deleting a source
def test_an_unbuilt_and_a_built_source_can_both_be_deleted(tmp_path: Path) -> None:
    """§6.8: deletion works before and after a build, and the source disappears immediately."""
    env = RefreshEnv.build(tmp_path, photos=3, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        unbuilt_root = tmp_path / "unbuilt"
        write_jpeg(unbuilt_root / "never-built.jpg", noise=41)
        unbuilt = register_source(env, "unbuilt", unbuilt_root)
        assert run(env, mode="build", source_ids=[1])["search_ready"] is True

        status, body = delete_source(env, unbuilt)
        assert status == 200
        assert body["deleted"] is True
        assert body["originals_touched"] == 0
        assert body["locations_removed"] == 0
        assert body["scans_removed"] == 0

        async def listed(client: AsyncClient) -> list[int]:
            await env.authenticate(client)
            response = await client.get("/api/v1/sources")
            return [int(item["source_id"]) for item in response.json()["sources"]]

        assert unbuilt not in env.scenario(listed)

        status, body = delete_source(env, 1)
        assert status == 200
        assert body["locations_removed"] == 3
        # The fixture's own complete scan plus the one the build ran (there is no third one).
        assert body["scans_removed"] == 2
        assert body["assets_orphaned"] == 3
        assert body["originals_touched"] == 0
        assert env.scenario(listed) == []
        assert env.service().catalog.read_one("SELECT COUNT(*) AS n FROM file_locations")["n"] == 0
        assert env.service().catalog.read_one("SELECT COUNT(*) AS n FROM scan_runs")["n"] == 0
        # Assets, vectors and manual facts are deliberately *not* garbage-collected here.
        assert env.service().catalog.read_one("SELECT COUNT(*) AS n FROM assets")["n"] == 3
    finally:
        env.close()


def test_deleting_a_source_keeps_the_asset_shared_with_another_source(tmp_path: Path) -> None:
    """§6.9: the same content in two sources survives one of them being removed."""
    env = RefreshEnv.build(tmp_path, photos=2, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        second = tmp_path / "second-library"
        second.mkdir()
        (second / "copy-a.jpg").write_bytes((env.library / "photo-000.jpg").read_bytes())
        (second / "copy-b.jpg").write_bytes((env.library / "photo-001.jpg").read_bytes())
        for name in ("copy-a.jpg", "copy-b.jpg"):
            os.utime(second / name, ns=((1_700_000_000_000 - 3_600_000) * 1_000_000,) * 2)
        second_id = register_source(env, "second", second)
        built = run(env, mode="build", source_ids=[1, second_id])
        assert built["search_ready"] is True
        # Both sources share the same two contents: the run's backlog counts the *union*, so
        # the shared assets are not counted twice (review observation on `_backlog_report`).
        assert built["stages"]["semantic"]["backlog_total"] == 2
        assert built["stages"]["semantic"]["backlog_confirmed"] == 2
        shared_asset = asset_of(env, "photo-000.jpg")

        status, body = delete_source(env, 1)

        assert status == 200
        assert body["assets_orphaned"] == 0  # both assets still live in the second source
        location = env.service().catalog.read_one(
            "SELECT relative_path, availability, asset_id FROM file_locations WHERE asset_id = ?",
            (shared_asset,),
        )
        assert location is not None
        assert str(location["relative_path"]) == "copy-a.jpg"
        assert str(location["availability"]) == "PRESENT"
        assert int(location["asset_id"]) == shared_asset
        # The asset is still bound, still in the index and still reachable through its source.
        assert (
            env.service().catalog.read_one(
                "SELECT COUNT(*) AS n FROM index_entries "
                "WHERE asset_id = ? AND state = 'CONFIRMED'",
                (shared_asset,),
            )["n"]
            == 1
        )
        env.adapter.vectors["shared"] = query_vector_for(env.library / "photo-000.jpg")
        found = env.search_payload(
            {
                "request_id": "p4-02y-i-shared",
                "text": "shared",
                "source_id": second_id,
                "page_size": 5,
            }
        )
        assert shared_asset in [int(item["asset_id"]) for item in found["hits"]]
    finally:
        env.close()


def test_deleting_a_source_never_touches_a_single_original_photo(tmp_path: Path) -> None:
    """§6.10: the whole deletion is records only - 0 modified, 0 deleted files."""
    env = RefreshEnv.build(tmp_path, photos=4, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        assert run(env, mode="build", source_ids=[1])["search_ready"] is True
        before = directory_summary(env.library)
        assert len(before) == 4

        status, body = delete_source(env, 1)

        assert status == 200
        assert body["originals_touched"] == 0
        assert directory_summary(env.library) == before
        assert len(list(env.library.iterdir())) == 4
    finally:
        env.close()


def test_deleting_a_source_keeps_the_derived_facts_of_its_assets(tmp_path: Path) -> None:
    """§5: the GPS/OCR/face rows keep their identity; only their provenance is cleared.

    ``file_locations`` may not simply be deleted while such a row points at one of its
    positions (``ON DELETE RESTRICT``), so the removal clears the reference instead - and this
    is the assertion that the *fact* really survives it (the review's independent probe made
    the same check by hand).
    """
    env = RefreshEnv.build(tmp_path, photos=2, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        assert run(env, mode="build", source_ids=[1])["search_ready"] is True
        catalog = env.service().catalog
        before = [
            dict(row)
            for row in catalog.read_all(
                "SELECT asset_id, status, location_id FROM asset_geo_facts ORDER BY asset_id"
            )
        ]
        assert before and all(row["location_id"] is not None for row in before)

        status, body = delete_source(env, 1)

        after = [
            dict(row)
            for row in catalog.read_all(
                "SELECT asset_id, status, location_id FROM asset_geo_facts ORDER BY asset_id"
            )
        ]
        assert status == 200
        assert body["originals_touched"] == 0
        assert [row["asset_id"] for row in after] == [row["asset_id"] for row in before]
        assert [row["status"] for row in after] == [row["status"] for row in before]
        assert all(row["location_id"] is None for row in after)
    finally:
        env.close()


def test_deleting_a_source_is_refused_while_a_run_is_active(tmp_path: Path) -> None:
    """§5: the refresh is a second writer, so a removal waits for it (structured 409)."""
    gate = threading.Event()
    entered = threading.Event()
    adapter = StubPinnedAdapter()

    def executor(service: Any, status: Any, source_ids: Any) -> dict:
        entered.set()
        assert gate.wait(timeout=60), "the test never released the refresh"
        from photo_search.refresh.pipeline import refresh_library

        return refresh_library(
            service,
            status,
            source_ids=source_ids,
            encoder_factory=lambda: adapter,
            manifest_path=MANIFEST,
            mode="build",
        )

    env = RefreshEnv.build(
        tmp_path, photos=2, adapter=adapter, manifest=MANIFEST, refresher_executor=executor
    )
    try:
        async def scenario(client: AsyncClient) -> dict:
            csrf = await env.authenticate(client)
            started = await client.post(
                "/api/v1/library/refresh",
                json={"mode": "build", "source_ids": [1]},
                headers=env.post_headers(csrf),
            )
            assert started.status_code == 202
            assert entered.wait(timeout=30), "the refresh worker never started"
            refused = await client.delete("/api/v1/sources/1", headers=env.post_headers(csrf))
            return {"refused": refused}

        result = env.scenario(scenario)
        assert result["refused"].status_code == 409
        assert result["refused"].json()["code"] == "INVALID_STATE_TRANSITION"
        assert result["refused"].json()["details"]["reason"] == "REFRESH_IN_PROGRESS"

        async def anonymous(client: AsyncClient) -> int:
            # A fresh client carries no session cookie: the removal is refused before any work.
            response = await client.delete("/api/v1/sources/1")
            return response.status_code

        assert env.scenario(anonymous) == 401
    finally:
        gate.set()
        assert env.context.refresher().wait_idle(timeout=180)
        # After the run finished the same removal succeeds: the refusal was a guard, not a
        # permanent state.
        status, body = delete_source(env, 1)
        assert status == 200
        assert body["deleted"] is True
        env.close()


def test_deleting_a_source_is_refused_while_its_scan_is_running(tmp_path: Path) -> None:
    """§5: a live scan of the source must finish first."""
    env = RefreshEnv.build(tmp_path, photos=1, adapter=None, manifest=None)
    try:
        write_jpeg(env.library / "second.jpg", noise=17)
        env.clock.advance(1000)
        env.wall.advance(1000)
        requested = env.service().start_scan(1)
        assert requested["state"] == "QUEUED"

        status, body = delete_source(env, 1)

        assert status == 409
        assert body["code"] == "INVALID_STATE_TRANSITION"
        assert body["details"]["reason"] == "SCAN_IN_PROGRESS"
        assert body["details"]["scan_id"] == requested["scan_id"]
        # Nothing was removed by the refused attempt.
        assert env.service().catalog.read_one("SELECT COUNT(*) AS n FROM source_roots")["n"] == 1
    finally:
        env.close()


def test_a_source_that_became_offline_can_still_be_deleted(tmp_path: Path) -> None:
    """§5/§6.8: an OFFLINE source (a BLOCKED scan) must be deletable, not stuck for ever."""
    env = RefreshEnv.build(tmp_path, photos=2, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        assert run(env, mode="build", source_ids=[1])["search_ready"] is True
        away = tmp_path / "library-away"
        os.rename(env.library, away)
        try:
            blocked = run(env, mode="refresh", source_ids=[1])
            assert blocked["counters"]["sources_failed"] == 1
            assert [entry["code"] for entry in blocked["errors"]] == ["SOURCE_OFFLINE"]
            row = env.service().catalog.read_one(
                "SELECT state FROM scan_runs ORDER BY scan_id DESC LIMIT 1"
            )
            assert str(row["state"]) == "BLOCKED"

            status, body = delete_source(env, 1)
        finally:
            os.rename(away, env.library)

        assert status == 200
        assert body["deleted"] is True
        # The fixture scan, the build's fast scan and the offline one (BLOCKED).
        assert body["scans_removed"] == 3
        assert body["originals_touched"] == 0
        assert env.service().catalog.read_one("SELECT COUNT(*) AS n FROM source_roots")["n"] == 0
    finally:
        env.close()


# ----------------------------------- B1/F2: nothing confirmable must be reported, never crash
def test_a_documents_only_source_builds_once_and_says_what_it_saw(tmp_path: Path) -> None:
    """B1 case A / F2: a source without a single indexable image is explained, not crashed.

    The build classifies the scope, finds nothing the image-text index is about, and therefore
    has no generation row for a search profile to point at. That used to raise an uncaught
    ``sqlite3.IntegrityError`` (the ``search_profiles.image_generation_id`` foreign key), which
    the run reported as a bare ``FAILED`` with no reason and no way forward.
    """
    # ``RefreshEnv.build(photos=0)`` needs the (empty) library root to exist already.
    (tmp_path / "library").mkdir(parents=True, exist_ok=True)
    env = RefreshEnv.build(tmp_path, photos=0, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        documents = tmp_path / "documents"
        documents.mkdir()
        (documents / "notes.txt").write_text("not a photo", encoding="utf-8")
        os.utime(documents / "notes.txt", ns=((1_700_000_000_000 - 3_600_000) * 1_000_000,) * 2)
        source_id = register_source(env, "documents", documents)

        status = run(env, mode="build", source_ids=[source_id])

        assert status["state"] == "COMPLETE"
        assert status["search_ready"] is True
        assert status["search_ready_basis"] == "semantic-nothing-to-index"
        assert status["failed_stages"] == []
        assert status["errors"] == []
        assert status["stages"]["semantic"]["state"] == "SKIPPED_NO_TARGETS"
        assert status["stages"]["semantic"]["reason"] == "NO_INDEXABLE_CONTENT"
        assert status["stages"]["semantic"]["classified"] == 0
        assert status["stages"]["semantic"]["out_of_scope"]["labels"] == {"UNIDENTIFIED": 1}
        # Nothing was ever published into this space, so there is no generation and no profile.
        assert env.service().catalog.read_all("SELECT generation_id FROM index_generations") == []
        assert env.service().catalog.read_all("SELECT profile_id FROM search_profiles") == []

        card = source_payload(env, source_id)["progress"]
        assert card["stages"]["semantic"]["state"] == "NO_IMAGES"
        assert card["stages"]["semantic"]["total"] == 0
        assert card["built"] is True
        assert card["search_ready"] is True
        assert card["scan"] == {"files": 1, "present": 1, "missing": 0}
        # Durable, not process-local: the marker is what tells "never looked at" from "looked
        # at, nothing to index" after a restart.
        marker = env.service().catalog.read_one(
            "SELECT value FROM metadata WHERE key = ?", (f"source_build:{source_id}",)
        )
        assert marker is not None

        # Pressing the button again is a no-op, not a second empty sweep with a new failure.
        again = run(env, mode="build", source_ids=[source_id])
        assert again["state"] == "COMPLETE"
        assert again["errors"] == []
        assert source_payload(env, source_id)["progress"]["stages"]["semantic"]["state"] == (
            "NO_IMAGES"
        )
    finally:
        env.close()


def test_a_source_whose_images_are_all_refused_is_reported_and_not_a_loop(
    tmp_path: Path,
) -> None:
    """B1 case B/D/C: refused content must not crash, must be named, and must not re-encode."""
    (tmp_path / "library").mkdir(parents=True, exist_ok=True)
    env = RefreshEnv.build(tmp_path, photos=0, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        broken = tmp_path / "broken"
        for index in range(2):
            write_jpeg(broken / f"broken-{index}.jpg", noise=500 + index)
        source_id = register_source(env, "broken", broken)

        first = run(env, mode="build", source_ids=[source_id])

        assert first["state"] == "FAILED"
        assert first["search_ready"] is False
        assert first["search_ready_basis"] == "semantic-confirmed-nothing"
        assert "semantic-confirmed-nothing" in first["failed_stages"]
        assert first["errors"] == []
        assert len(env.adapter.image_calls) == 2

        card = source_payload(env, source_id)["progress"]
        assert card["built"] is True
        assert card["search_ready"] is False
        assert card["stages"]["semantic"]["state"] == "UNINDEXABLE"
        assert card["stages"]["semantic"]["total"] == 2
        assert card["stages"]["semantic"]["confirmed"] == 0
        assert card["stages"]["semantic"]["failed"] == 2
        assert card["stages"]["semantic"]["pending"] == 0

        # "继续建库" again: same honest answer, and it does not hand anything to the encoder a
        # second time (the refused jobs are terminal; there is no confirmed content to repeat).
        second = run(env, mode="build", source_ids=[source_id])
        assert second["state"] == "FAILED"
        assert second["search_ready_basis"] == "semantic-confirmed-nothing"
        assert second["errors"] == []
        assert len(env.adapter.image_calls) == 2
        assert source_payload(env, source_id)["progress"]["stages"]["semantic"]["state"] == (
            "UNINDEXABLE"
        )

        # The P4-02Y daily entry on the same catalog is honest about having no index at all
        # instead of claiming "semantic-confirmed" for a library with no generation row.
        daily = run(env, mode="refresh", source_ids=[source_id])
        assert daily["errors"] == []
        assert daily["search_ready"] is False
        assert daily["search_ready_basis"] == "GENERATION_NOT_PUBLISHED"
        assert env.service().catalog.read_all("SELECT generation_id FROM index_generations") == []
    finally:
        env.close()


def test_a_refused_image_is_counted_on_the_card_without_hiding_the_rest(tmp_path: Path) -> None:
    """F1: "可以搜索" must carry the number of images it could not index.

    The run itself ends ``PARTIAL_FAILED`` with ``semantic-content`` (P4-02Y's frozen rule for
    content the encoder refused while the rest stays searchable); what F1 added is that the
    *card* now reports the refused count instead of a bare "✓ 可以语义搜索".
    """
    (tmp_path / "library").mkdir(parents=True, exist_ok=True)
    env = RefreshEnv.build(tmp_path, photos=0, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        mixed = tmp_path / "mixed"
        write_jpeg(mixed / "good.jpg", noise=97)
        write_jpeg(mixed / "broken.jpg", noise=98)
        source_id = register_source(env, "mixed", mixed)

        status = run(env, mode="build", source_ids=[source_id])

        assert status["state"] == "PARTIAL_FAILED"
        assert status["search_ready"] is True
        assert status["search_ready_basis"] == "semantic-backlog-empty"
        assert status["errors"] == []
        assert "semantic-content" in status["failed_stages"]
        semantic = source_payload(env, source_id)["progress"]["stages"]["semantic"]
        assert semantic["state"] == "SEARCHABLE"
        assert semantic["total"] == 2
        assert semantic["confirmed"] == 1
        assert semantic["failed"] == 1
        assert semantic["pending"] == 0

        # The one image that could be encoded is searchable; the refused one did not hide it.
        env.adapter.vectors["good"] = query_vector_for(mixed / "good.jpg")
        found = env.search_payload(
            {"request_id": "p4-02y-i-refused", "text": "good", "page_size": 5}
        )
        assert [int(item["asset_id"]) for item in found["hits"]][:1] == [
            asset_of(env, "good.jpg")
        ]

        # A second build re-encodes nothing that is already confirmed.
        again = run(env, mode="build", source_ids=[source_id])
        assert again["errors"] == []
        good_calls = [
            str(path) for path in env.adapter.image_calls if Path(path).name == "good.jpg"
        ]
        assert good_calls == [str(mixed / "good.jpg")]
    finally:
        env.close()


# --------------------------------------------------------------------------- Web wiring
def test_the_sources_page_carries_the_unified_build_and_sync_entries(tmp_path: Path) -> None:
    """§1/§3/§5 at asset level: the served shell and script really carry the new entries."""
    env = RefreshEnv.build(tmp_path, photos=2, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        async def scenario(client: AsyncClient) -> dict[str, str]:
            await env.authenticate(client)
            shell = await client.get("/")
            script = await client.get("/web/app.js")
            assert shell.status_code == 200 and script.status_code == 200
            return {"shell": shell.text, "script": script.text}

        served = env.scenario(scenario)
        for phrase in ("继续全部建库", "同步全部变化", "开始建库", "建库/补齐", "同步变化"):
            assert phrase in served["shell"] or phrase in served["script"], phrase
        assert 'id="build-all-library" disabled' in served["shell"]
        assert 'id="refresh-library" disabled' in served["shell"]
        assert "source_ids" in served["script"]
        assert "pending_all_build" in served["script"]
        assert "完整校验扫描" in served["script"]
        assert "删除来源" in served["script"]
        assert "删除来源只会移除 PhotoSearch 中的记录，不会删除文件夹中的照片" in served["script"]
        assert "可以语义搜索" in served["script"]
        assert '"build"' in served["script"]
        assert "继续建库" in served["script"]
        assert "semantic-backlog" in served["script"]
        assert "semantic-confirmed-nothing" in served["script"]
        assert "已扫描，无可索引图片" in served["script"]
        assert "无法建立语义索引" in served["script"]
        assert "张无法编码" in served["script"]
        # A stage that has run no task is rendered 未开始, and outstanding-but-idle work is
        # 待处理 - the two words the review follow-up asked for.
        assert "NOT_STARTED: \"未开始\"" in served["script"]
        assert "PENDING: \"待处理\"" in served["script"]
        # A run that is not search ready names the gate it stopped at (re-review F3).
        assert "SEARCH_GATE_TEXT" in served["script"]
        assert "GENERATION_NOT_PUBLISHED: \"该向量空间还没有任何已发布的图像索引\"" in served["script"]
        assert 'method: "DELETE"' in served["script"]
    finally:
        env.close()
