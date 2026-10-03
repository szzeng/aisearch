"""P2-08 acceptance matrix, part 1: scan/restart recovery and late results.

These are the cards' cases 1-5. Each one drives a *named* fault through an injection point
that already exists in production (``run_scan(interrupt_after=...)``,
``Scheduler`` leases, ``IndexCoordinator.process_next(failpoint="after_lance")``,
``scheduler.artifact_store.persist_result``, real file replacement) and then reads the
authoritative SQLite facts back. No production semantics are changed by this file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from p2_acceptance_fixtures import (
    acceptance_env,
    add_gps_photo,
    add_photo,
    asset_for,
    assignment_row,
    filter_revision,
    install_dataset,
    latest_job,
    latest_scan,
    make_place,
    place_action,
    reopen_service,
    replace_photo,
    rescan,
    run_gps,
    run_resolver,
    sha_of,
)
from p2_dataset_fixtures import HANGZHOU_POSITION
from p2_search_api_fixtures import SearchEnv
from p2_search_fixtures import (
    DIMENSION,
    SPACE,
    configured_scheduler,
    confirm_pending,
    embedding_artifact,
    submit_vector,
)

from photo_search.artifacts.store import generation_id_for
from photo_search.catalog.errors import SOURCE_OFFLINE, STALE_LEASE, CatalogError
from photo_search.indexing.store import IndexCoordinator
from photo_search.ingestion.scanner import ScanInterrupted
from photo_search.jobs import queue as jobs
from photo_search.places import suggestions as suggestions_module
from photo_search.scheduler.contracts import CONTRACT_VERSION, JobResult
from photo_search.search import documents as documents_module


def _api_search(
    env: SearchEnv, payload: dict[str, Any], *, query: dict[str, str] | None = None
) -> Any:
    """One authenticated ``POST /api/v1/search`` against this environment's app."""

    async def scenario(client: Any) -> Any:
        csrf = await env.authenticate(client)
        return await client.post(
            "/api/v1/search",
            json=payload,
            params=query or {},
            headers=SearchEnv.post_headers(csrf),
        )

    return env.scenario(scenario)


def _claim_embedding_job(
    catalog: Any, scheduler: Any, spec: Any, *, asset_id: int
) -> tuple[Any, JobResult]:
    """Enqueue and claim one embedding job, and build the result the worker would send."""
    with catalog.write() as connection:
        jobs.enqueue(
            connection,
            subject_type="asset",
            subject_id=str(int(asset_id)),
            input_revision=str(sha_of(catalog, int(asset_id))),
            task_type=spec.task_type,
            processor_id=spec.processor_id,
            now_ms=1_000,
        )
    request = scheduler.claim_next(worker_id="w-acceptance", now_ms=1_100)
    assert request is not None, "the embedding job was not claimable"
    result = JobResult.from_envelope(
        {
            "contract_version": CONTRACT_VERSION,
            "job_id": request.job_id,
            "lease_token": request.lease_token,
            "input_revision": request.input_revision,
            "task_type": request.task_type,
            "processor_id": request.processor_id,
            "state": "SUCCEEDED",
            "resolved_device": request.resolved_device,
            "runtime_provider": request.runtime_provider,
            "precision": request.precision,
            "progress": 1,
            "artifacts": [
                embedding_artifact(
                    f"acceptance-{int(asset_id)}",
                    [1.0, 0.0, 0.0],
                    space_id=SPACE,
                    dimension=DIMENSION,
                )
            ],
            "error": None,
            "completed_at": 1_200,
        }
    )
    return request, result


def _outbox_rows(catalog: Any, asset_id: int) -> int:
    return int(
        catalog.read_one(
            "SELECT COUNT(*) AS value FROM index_outbox WHERE asset_id = ?",
            (int(asset_id),),
        )["value"]
    )


def _artifact_rows(catalog: Any, input_revision: str) -> int:
    return int(
        catalog.read_one(
            "SELECT COUNT(*) AS value FROM artifacts WHERE input_revision = ?",
            (str(input_revision),),
        )["value"]
    )


# ------------------------------------------------------------------ 1. scan + restart
def test_an_interrupted_scan_resumes_after_a_restart_without_false_missing(
    tmp_path: Path,
) -> None:
    """§12.4 items 1 and 6: a half scan, a killed process and an unreachable volume."""
    env = acceptance_env(tmp_path)
    try:
        catalog = env.catalog
        place_id = make_place(catalog, "杭州西湖")
        locked_asset = env.assets["杭州西湖 照片.jpg"]
        place_action(
            catalog,
            asset_id=locked_asset,
            action="ASSIGN",
            target_place_id=place_id,
            request_id="p2-08-case1-lock",
        )
        revision_before = filter_revision(catalog)
        assets_before = int(catalog.read_one("SELECT COUNT(*) AS value FROM assets")["value"])

        for index, name in enumerate(("新增 一.jpg", "新增 二.jpg", "新增 三.jpg"), start=10):
            add_photo(env, name, index=index)
        with pytest.raises(ScanInterrupted):
            rescan(env, interrupt_after=2)

        scan = latest_scan(catalog)
        job = latest_job(catalog)
        assert scan["state"] == "RUNNING" and scan["finished_at"] is None
        assert job["state"] == "RUNNING" and job["lease_token"] is not None
        counts = env.library.service.counts()
        assert counts["assets"] == assets_before
        assert counts["locations_missing"] == 0
        # The three extra files were enumerated but never confirmed, so exactly those three
        # positions have no content identity yet - and no position was declared missing.
        assert counts["locations_pending_content"] == 3
        assert filter_revision(catalog) == revision_before

        # The process is killed and the volume is unreachable while it is down.
        moved = env.library.root.with_name("library 停机中")
        os.replace(env.library.root, moved)
        service = reopen_service(env, advance_ms=120_000)
        catalog = service.catalog

        with pytest.raises(CatalogError) as offline:
            service.run_scan(env.library.source_id)
        assert offline.value.code == SOURCE_OFFLINE
        blocked = service.counts()
        assert blocked["locations_missing"] == 0
        assert blocked["assets"] == assets_before
        assert service.get_source(env.library.source_id)["status"] == "OFFLINE"
        # The manual place lock and the asset identity survive the offline window.
        row = assignment_row(catalog, locked_asset)
        assert str(row["mode"]) == "MANUAL_ASSIGNED"
        assert int(row["place_id"]) == place_id
        assert filter_revision(catalog) == revision_before

        # The volume comes back: the same logical scan_run is taken over and completes.
        os.replace(moved, env.library.root)
        outcome = service.run_scan(env.library.source_id)
        assert outcome["state"] == "SUCCEEDED"
        assert outcome["scan_id"] == int(scan["scan_id"])
        assert outcome["files_seen"] == 6
        after = service.counts()
        assert after["assets"] == assets_before + 3
        assert after["locations"] == 6
        assert after["locations_missing"] == 0
        # Exactly two scan_runs exist: the initial full scan and the one that was interrupted,
        # blocked by the offline volume and then taken over. No third scan appeared.
        runs = [
            dict(row)
            for row in catalog.read_all("SELECT scan_id, state FROM scan_runs ORDER BY scan_id")
        ]
        assert [row["state"] for row in runs] == ["SUCCEEDED", "SUCCEEDED"]
        assert int(outcome["scan_id"]) == int(scan["scan_id"]) == int(runs[1]["scan_id"])
        assert latest_job(catalog)["attempts"] >= 1
        assert assignment_row(catalog, locked_asset)["place_id"] == place_id
        assert filter_revision(catalog) == revision_before
    finally:
        env.context.close()
        env.library.close()


# ------------------------------------------------------- 2. crash before the commit
def test_a_crash_before_the_sqlite_commit_recomputes_and_stays_consistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§12.4 item 2: inference finished, the transaction never committed."""
    env = acceptance_env(tmp_path)
    try:
        catalog = env.catalog
        add_photo(env, "sunset 崩溃窗口.jpg", index=21)
        rescan(env)
        new_asset = asset_for(env, "sunset 崩溃窗口.jpg")
        scheduler = configured_scheduler(catalog, [env.spec])
        _request, payload = _claim_embedding_job(catalog, scheduler, env.spec, asset_id=new_asset)
        generation = generation_id_for(SPACE, env.spec.processor_id)
        assert IndexCoordinator(catalog).semantic_ready(
            space_id=SPACE, asset_id=new_asset, generation_id=generation
        ) is False

        original = scheduler.artifact_store.persist_result

        def crash(connection: Any, **options: Any) -> Any:
            original(connection, **options)
            raise RuntimeError("injected before commit")

        monkeypatch.setattr(scheduler.artifact_store, "persist_result", crash)
        with pytest.raises(RuntimeError, match="injected before commit"):
            scheduler.submit(payload, now_ms=1_300)

        assert _artifact_rows(catalog, payload.input_revision) == 0
        assert _outbox_rows(catalog, new_asset) == 0
        assert int(catalog.read_one("SELECT COUNT(*) AS value FROM index_entries")["value"]) == 3
        assert not list((catalog.db_path.parent.parent / "artifacts").rglob("*.tmp"))

        monkeypatch.setattr(scheduler.artifact_store, "persist_result", original)
        finished = scheduler.submit(payload, now_ms=1_400)
        assert finished.state == "SUCCEEDED"
        assert _artifact_rows(catalog, payload.input_revision) == 1
        assert _outbox_rows(catalog, new_asset) == 1
        entry = catalog.read_one(
            "SELECT state FROM index_entries WHERE space_id = ? AND asset_id = ? "
            "AND generation_id = ?",
            (SPACE, new_asset, generation),
        )
        assert str(entry["state"]) == "PENDING_REPAIR"

        # A restart replays the outbox; only then do the text and vector channels agree.
        assert IndexCoordinator(catalog).process_all(now_ms=2_000) == 1
        assert IndexCoordinator(catalog).semantic_ready(
            space_id=SPACE, asset_id=new_asset, generation_id=generation
        ) is True
        body = _api_search(
            env,
            {
                "request_id": "p2-08-case2",
                "text": "sunset",
                **env.explicit_binding(),
            },
        ).json()
        hit_ids = [int(row["asset_id"]) for row in body["hits"]]
        assert new_asset in hit_ids
        row = next(row for row in body["hits"] if int(row["asset_id"]) == new_asset)
        assert row["semantic_ready"] is True
        assert "vector" in row["matched_by"] and "fts" in row["matched_by"]
        assert body["pending_semantic"]["count"] == 0
    finally:
        env.context.close()
        env.library.close()


# --------------------------------------------- 3. LanceDB written, outbox unconfirmed
def test_a_lance_write_without_confirmation_is_named_and_replays_once(
    tmp_path: Path,
) -> None:
    """§12.4 item 3, joined with P2-05 §6: the crash window is visible, not silent."""
    env = acceptance_env(tmp_path)
    try:
        catalog = env.catalog
        add_photo(env, "sunset 待确认.jpg", index=31)
        rescan(env)
        pending_asset = asset_for(env, "sunset 待确认.jpg")
        scheduler = configured_scheduler(catalog, [env.spec])
        submit_vector(
            catalog,
            scheduler,
            env.spec,
            asset_id=pending_asset,
            vector=[1.0, 0.0, 0.0],
            artifact_id="acceptance-crash-after-lance",
        )
        generation = generation_id_for(SPACE, env.spec.processor_id)
        with pytest.raises(RuntimeError, match="after_lance"):
            IndexCoordinator(catalog).process_next(now_ms=2_000, failpoint="after_lance")
        assert str(
            catalog.read_one(
                "SELECT state FROM index_outbox WHERE asset_id = ?", (pending_asset,)
            )["state"]
        ) == "IN_FLIGHT"
        assert IndexCoordinator(catalog).semantic_ready(
            space_id=SPACE, asset_id=pending_asset, generation_id=generation
        ) is False

        body = _api_search(
            env,
            {"request_id": "p2-08-case3", "text": "sunset", **env.explicit_binding()},
        ).json()
        assert body["pending_semantic"] is not None
        pending_entry = next(
            entry
            for entry in body["pending_semantic"]["entries"]
            if int(entry["asset_id"]) == pending_asset
        )
        assert pending_entry["entry_state"] == "PENDING_REPAIR"
        assert int(pending_entry["pending_events"]) == 1
        pending_hit = next(
            row for row in body["hits"] if int(row["asset_id"]) == pending_asset
        )
        assert list(pending_hit["matched_by"]) == ["fts"]
        assert pending_hit["semantic_ready"] is False

        restarted = IndexCoordinator(catalog)
        assert restarted.process_next(now_ms=2_100) is True
        assert restarted.process_next(now_ms=2_200) is False
        assert restarted.index.count(
            generation_id=generation, dimension=DIMENSION, space_id=SPACE
        ) == 4
        assert restarted.semantic_ready(
            space_id=SPACE, asset_id=pending_asset, generation_id=generation
        ) is True
        after = _api_search(
            env,
            {"request_id": "p2-08-case3b", "text": "sunset", **env.explicit_binding()},
        ).json()
        assert after["pending_semantic"]["count"] == 0
        after_hit = next(row for row in after["hits"] if int(row["asset_id"]) == pending_asset)
        assert "vector" in after_hit["matched_by"]
        assert after_hit["semantic_ready"] is True
    finally:
        env.context.close()
        env.library.close()


# ------------------------------------------------------- 4. late lease and late place
def test_an_expired_lease_and_a_late_resolution_cannot_change_locked_facts(
    tmp_path: Path,
) -> None:
    """§12.4 items 4 and 31: a late writer is refused, not merged."""
    env = acceptance_env(tmp_path)
    try:
        catalog = env.catalog
        add_photo(env, "sunset 迟到结果.jpg", index=41)
        rescan(env)
        late_asset = asset_for(env, "sunset 迟到结果.jpg")
        scheduler = configured_scheduler(catalog, [env.spec])
        request, payload = _claim_embedding_job(
            catalog, scheduler, env.spec, asset_id=late_asset
        )
        before_job = tuple(
            catalog.read_one("SELECT * FROM jobs WHERE job_id = ?", (int(request.job_id),))
        )

        # The worker comes back long after its lease was taken away.
        with pytest.raises(CatalogError) as stale:
            scheduler.submit(payload, now_ms=1_100 + 10 * 60_000)
        assert stale.value.code == STALE_LEASE
        assert _artifact_rows(catalog, payload.input_revision) == 0
        assert _outbox_rows(catalog, late_asset) == 0
        assert tuple(
            catalog.read_one("SELECT * FROM jobs WHERE job_id = ?", (int(request.job_id),))
        ) == before_job

        # A live takeover of the same logical job still succeeds exactly once.
        reclaim = scheduler.claim_next(worker_id="w-second", now_ms=1_100 + 10 * 60_000)
        assert reclaim is not None and reclaim.job_id == request.job_id
        fresh = JobResult.from_envelope(
            {**payload.to_envelope(), "lease_token": reclaim.lease_token}
        )
        assert scheduler.submit(fresh, now_ms=1_100 + 10 * 60_000 + 50).state == "SUCCEEDED"
        assert _artifact_rows(catalog, payload.input_revision) == 1
        assert _outbox_rows(catalog, late_asset) == 1
        confirm_pending(catalog)

        # A machine resolution that arrives after the user locked the asset must not win.
        install_dataset(env, tmp_path=tmp_path)
        add_gps_photo(env, "西湖 GPS.jpg", position=HANGZHOU_POSITION)
        rescan(env)
        gps_asset = asset_for(env, "西湖 GPS.jpg")
        run_gps(env, [gps_asset])
        run_resolver(env, [gps_asset])
        geo_place = int(
            catalog.read_one(
                "SELECT place_id FROM place_external_refs WHERE external_id = 'city-hangzhou'"
            )["place_id"]
        )
        candidates = suggestions_module.suggestion_view(catalog.connection, gps_asset)[
            "candidates"
        ]
        assert candidates and int(candidates[0]["rank"]) == 1
        assert int(candidates[0]["local_place_id"]) == geo_place

        place_action(
            catalog,
            asset_id=gps_asset,
            action="ASSIGN",
            target_place_id=geo_place,
            request_id="p2-08-case4-assign",
        )
        place_action(
            catalog,
            asset_id=gps_asset,
            action="UNASSIGN",
            request_id="p2-08-case4-unassign",
        )
        locked = assignment_row(catalog, gps_asset)
        assert str(locked["mode"]) == "MANUAL_UNASSIGNED"
        assert locked["place_id"] is None

        # The resolver runs again (same input, a late re-execution): nothing may move.
        before_assignments = tuple(locked)
        run_resolver(env, [gps_asset], now_ms=9_000)
        assert tuple(assignment_row(catalog, gps_asset)) == before_assignments
        view = suggestions_module.suggestion_view(catalog.connection, gps_asset)
        # The manual lock keeps the candidate set usable: the excluded place stays hidden while
        # the other nearby cities remain suggestion-only candidates.
        assert view["state"] == "candidates"
        assert view["excluded_candidate_count"] == 1
        assert geo_place not in [
            int(candidate["local_place_id"]) for candidate in view["candidates"]
        ]
        assert catalog.read_one(
            "SELECT active FROM asset_place_exclusions WHERE asset_id = ? AND place_id = ?",
            (gps_asset, geo_place),
        )["active"] == 1
    finally:
        env.context.close()
        env.library.close()


# ------------------------------------------------- 5. content changes mid-processing
def test_replaced_content_never_inherits_the_old_results(tmp_path: Path) -> None:
    """§12.4 item 5: old text/vector/media results stay attached to the old asset."""
    env = acceptance_env(tmp_path)
    try:
        catalog = env.catalog
        documents_module.rebuild_all(catalog)
        replaced = env.assets["beach sunset.jpg"]
        generation = generation_id_for(SPACE, env.spec.processor_id)
        before_document = dict(
            catalog.read_one("SELECT * FROM search_documents WHERE asset_id = ?", (replaced,))
        )
        before_entry = tuple(
            catalog.read_one(
                "SELECT * FROM index_entries WHERE asset_id = ? AND space_id = ? "
                "AND generation_id = ?",
                (replaced, SPACE, generation),
            )
        )
        assert IndexCoordinator(catalog).semantic_ready(
            space_id=SPACE, asset_id=replaced, generation_id=generation
        )

        # Publish the old content's thumbnail first: it is content-addressed, so the
        # replacement must leave those bytes alone and an on-demand request for the old asset
        # must still be served from the cache (P1-03/P1-05).
        published = env.library.service.generate_thumbnail()
        assert published["generated"] == 3
        digest = sha_of(catalog, replaced)
        thumbnail_path = (
            env.data_root
            / "thumbnails"
            / "catalog-thumbnail@2"
            / digest[:2]
            / digest[2:4]
            / f"{digest}.jpg"
        )
        assert thumbnail_path.is_file()
        before_thumbnail = thumbnail_path.read_bytes()

        original_size = (env.library.root / "beach sunset.jpg").stat().st_size
        replacement = replace_photo(env, "beach sunset.jpg")
        assert replacement.stat().st_size != original_size
        with pytest.raises(ScanInterrupted):
            rescan(env, interrupt_after=0)

        counts = env.library.service.counts()
        assert counts["locations_pending_content"] == 1
        assert counts["locations_missing"] == 0
        unconfirmed = catalog.read_one(
            "SELECT asset_id, availability FROM file_locations WHERE relative_path = ?",
            ("beach sunset.jpg",),
        )
        assert unconfirmed["availability"] == "PRESENT"
        assert unconfirmed["asset_id"] is None
        assert dict(
            catalog.read_one("SELECT * FROM search_documents WHERE asset_id = ?", (replaced,))
        ) == before_document
        assert tuple(
            catalog.read_one(
                "SELECT * FROM index_entries WHERE asset_id = ? AND space_id = ? "
                "AND generation_id = ?",
                (replaced, SPACE, generation),
            )
        ) == before_entry

        # The killed execution left an unexpired lease, so the resume needs a real expiry.
        env.library.clock.advance(120_000)
        outcome = rescan(env)
        assert outcome["state"] == "SUCCEEDED"
        assert outcome["locations_missing"] == 0
        new_asset = asset_for(env, "beach sunset.jpg")
        assert new_asset != replaced
        assert sha_of(catalog, new_asset) != sha_of(catalog, replaced)
        assert IndexCoordinator(catalog).semantic_ready(
            space_id=SPACE, asset_id=replaced, generation_id=generation
        )
        assert IndexCoordinator(catalog).semantic_ready(
            space_id=SPACE, asset_id=new_asset, generation_id=generation
        ) is False
        assert catalog.read_one(
            "SELECT asset_id FROM search_documents WHERE asset_id = ?", (replaced,)
        ) is not None

        # The text projection is derived from authoritative rows: once it catches up, the new
        # position owns the filename and the old asset never gains the new content's text.
        documents_module.rebuild_all(catalog)
        new_document = catalog.read_one(
            "SELECT * FROM search_documents WHERE asset_id = ?", (new_asset,)
        )
        assert new_document is not None
        assert str(new_document["filename_text"]) == str(before_document["filename_text"])
        old_document = catalog.read_one(
            "SELECT * FROM search_documents WHERE asset_id = ?", (replaced,)
        )
        if old_document is not None:
            # P2-04 derives filename text from ``PRESENT`` positions only; the replaced file no
            # longer belongs to the old asset, so its projection is empty, never the new text.
            assert str(old_document["filename_text"]) == ""
            assert str(old_document["source_fingerprint"]) != str(
                new_document["source_fingerprint"]
            )

        # The published thumbnail of the old content is untouched and still served, while the
        # new content gets its own artifact - the old reference never follows the new bytes.
        assert thumbnail_path.read_bytes() == before_thumbnail
        old_response = _asset_thumbnail(env, replaced)
        assert old_response.status_code == 200
        assert old_response.content == before_thumbnail
        new_response = _asset_thumbnail(env, new_asset)
        assert new_response.status_code == 200
        assert new_response.content != before_thumbnail
    finally:
        env.context.close()
        env.library.close()


def _asset_thumbnail(env: SearchEnv, asset_id: int) -> Any:
    async def scenario(client: Any) -> Any:
        await env.authenticate(client)
        return await client.get(f"/api/v1/assets/{int(asset_id)}/thumbnail")

    return env.scenario(scenario)
