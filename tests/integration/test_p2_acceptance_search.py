"""P2-08 acceptance matrix, part 2: increments, filters and background competition.

Cards cases 6, 7, 8, 10 and 11. Every assertion is read back from the authoritative SQLite
state, and the search side always goes through the real HTTP surface with a real session, a
real revision and the production ranking path.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from p2_acceptance_fixtures import (
    acceptance_env,
    add_gps_photo,
    add_photo,
    asset_for,
    dataset_status,
    filter_revision,
    install_dataset,
    make_source_place,
    place_action,
    rescan,
    run_gps,
    run_resolver,
    suggestion_revision,
)
from p2_dataset_fixtures import HANGZHOU_POSITION
from p2_search_api_fixtures import SearchEnv, fresh_app
from PIL import Image

from photo_search.api import routes
from photo_search.catalog.errors import (
    CATALOG_FILTER_REVISION_STALE,
    INVALID_PLACE_FILTER,
    PLACE_SUGGESTION_REVISION_STALE,
    PROFILE_GENERATION_MISMATCH,
    SEARCH_SESSION_EXPIRED,
    CatalogError,
)
from photo_search.ingestion.scanner import ScanInterrupted
from photo_search.search.profile import activate_profile
from photo_search.search.upload import UploadLimits


# ------------------------------------------------------------------------- API helpers
def _search(env: SearchEnv, payload: dict[str, Any]) -> Any:
    async def scenario(client: Any) -> Any:
        csrf = await env.authenticate(client)
        return await client.post(
            "/api/v1/search", json=payload, headers=SearchEnv.post_headers(csrf)
        )

    return env.scenario(scenario)


def _search_on_app(
    env: SearchEnv, app: Any, payload: dict[str, Any]
) -> Any:
    """One search against an explicitly chosen application (the "restarted process").

    The fresh app has its own session store, so this authenticates with the bootstrap
    credential again instead of reusing the cookie of the running process.
    """

    async def main() -> Any:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://127.0.0.1", follow_redirects=False
        ) as client:
            session = await client.get(
                "/api/v1/session", params={"token": env.session_token}
            )
            assert session.status_code == 200, session.text
            csrf = str(session.json()["csrf_token"])
            return await client.post(
                "/api/v1/search",
                json=payload,
                headers={"X-CSRF-Token": csrf, "Origin": "http://127.0.0.1"},
            )

    return asyncio.run(main())


def _assets(env: SearchEnv, **params: Any) -> Any:
    async def scenario(client: Any) -> Any:
        await env.authenticate(client)
        return await client.get("/api/v1/assets", params=params)

    return env.scenario(scenario)


def _upload(
    env: SearchEnv, payload: bytes, *, fields: dict[str, str] | None = None
) -> Any:
    async def scenario(client: Any) -> Any:
        csrf = await env.authenticate(client)
        data = dict(fields or {})
        if "space_id" not in data:
            data.update(env.explicit_binding())
        return await client.post(
            "/api/v1/search/image",
            data=data,
            files={"image": ("上传 图片.jpg", payload, "image/jpeg")},
            headers=SearchEnv.post_headers(csrf),
        )

    return env.scenario(scenario)


def _asset_ids(response: Any) -> list[int]:
    return [int(row["asset_id"]) for row in response.json()["assets"]]


def _location_rows(catalog: Any) -> dict[str, dict[str, Any]]:
    return {
        str(row["relative_path"]).replace("\\", "/"): dict(row)
        for row in catalog.read_all("SELECT * FROM file_locations ORDER BY location_id")
    }


def _jpeg(tmp_path: Path, *, size: tuple[int, int] = (32, 24)) -> bytes:
    path = Path(tmp_path) / "upload.jpg"
    Image.new("RGB", size, (200, 40, 40)).save(path, "JPEG", quality=90)
    return path.read_bytes()


def _upload_directories() -> set[str]:
    return {str(path) for path in Path(tempfile.gettempdir()).glob("photo-search-upload-*")}


# ------------------------------------------------------------------- 6. incremental
def test_add_rename_move_and_delete_stay_consistent_with_search(tmp_path: Path) -> None:
    """§12.4 item 32 (increment half): one authority version for text, places and search."""
    env = acceptance_env(tmp_path)
    try:
        catalog = env.catalog
        place_id = make_source_place(catalog, "杭州西湖")
        renamed = env.assets["beach sunset.jpg"]
        moved = env.assets["杭州西湖 照片.jpg"]
        deleted = env.assets["IMG_0001.JPG"]
        place_action(
            catalog,
            asset_id=renamed,
            action="ASSIGN",
            target_place_id=place_id,
            request_id="p2-08-case6-assign",
        )
        revision = filter_revision(catalog)

        (env.library.root / "子 目录").mkdir(parents=True, exist_ok=True)
        os.replace(
            env.library.root / "beach sunset.jpg",
            env.library.root / "beach sunset renamed.jpg",
        )
        os.replace(
            env.library.root / "杭州西湖 照片.jpg",
            env.library.root / "子 目录" / "杭州西湖 照片.jpg",
        )
        add_photo(env, "新增 照片.jpg", index=61)
        os.remove(env.library.root / "IMG_0001.JPG")

        outcome = rescan(env)
        assert outcome["state"] == "SUCCEEDED"
        assert outcome["files_seen"] == 3
        # Three positions are now missing - the two old paths and the deleted file - while
        # only the deleted one leaves an asset without any present position at all.
        assert outcome["locations_missing"] == 3
        locations = _location_rows(catalog)
        assert locations["beach sunset.jpg"]["availability"] == "MISSING"
        assert locations["beach sunset.jpg"]["asset_id"] == renamed
        assert locations["beach sunset renamed.jpg"]["availability"] == "PRESENT"
        assert locations["beach sunset renamed.jpg"]["asset_id"] == renamed
        assert locations["子 目录/杭州西湖 照片.jpg"]["asset_id"] == moved
        assert locations["杭州西湖 照片.jpg"]["availability"] == "MISSING"
        assert locations["杭州西湖 照片.jpg"]["asset_id"] == moved
        assert locations["IMG_0001.JPG"]["availability"] == "MISSING"
        assert locations["IMG_0001.JPG"]["asset_id"] == deleted
        # The new asset moves shared membership authority; file renames and the
        # preserved manual assignment do not create extra place-revision events.
        assert filter_revision(catalog) == revision + 1
        assert [str(row["cause"]) for row in catalog.read_all(
            "SELECT cause FROM catalog_filter_revision_events WHERE revision > ? "
            "ORDER BY revision", (revision,)
        )] == ["ASSET_ADDED"]
        revision += 1

        # The first query after the rename already finds the asset (the projection catches up
        # inside the request, and the authoritative path is re-checked).
        text_page = _assets(env, text="renamed")
        assert text_page.status_code == 200, text_page.text
        assert _asset_ids(text_page) == [renamed]
        body = _search(
            env,
            {"request_id": "p2-08-case6-text", "text": "renamed", **env.explicit_binding()},
        ).json()
        assert renamed in [int(row["asset_id"]) for row in body["hits"]]
        assert body["catalog_filter_revision"] == revision

        # The effective place survived the rename and is visible through both read paths.
        place_page = _assets(env, place_ids=[place_id])
        assert _asset_ids(place_page) == [renamed]
        listed = place_page.json()
        assert listed["catalog_filter_revision"] == revision
        filter_only = _search(
            env, {"request_id": "p2-08-case6-place", "place_ids": [place_id]}
        ).json()
        assert [int(row["asset_id"]) for row in filter_only["hits"]] == [renamed]
        assert filter_only["catalog_filter_revision"] == revision
        assert filter_only["hits"][0]["hit_reason"] == "FILTER_ONLY"
        missing_page = _assets(env, availability="missing").json()
        assert [int(row["asset_id"]) for row in missing_page["assets"]] == [deleted]
    finally:
        env.context.close()
        env.library.close()


# ----------------------------------------------------------------------- 7. filters
def test_place_and_source_filters_stay_exact_and_reject_illegal_combinations(
    tmp_path: Path,
) -> None:
    """§12.4 item 32 (filter half): ancestors, homonyms, states and cross-source copies."""
    env = acceptance_env(tmp_path)
    try:
        catalog = env.catalog
        from p2_search_fixtures import install_source_places

        places = install_source_places(catalog)
        country = places["china"]
        city = places["hangzhou"]
        homonym = places["hangzhou_homonym"]
        assigned_place = places["tokyo"]
        under_country = env.assets["杭州西湖 照片.jpg"]
        under_homonym = env.assets["beach sunset.jpg"]
        unassigned = env.assets["IMG_0001.JPG"]
        place_action(
            catalog,
            asset_id=under_country,
            action="ASSIGN",
            target_place_id=city,
            request_id="p2-08-case7-city",
        )
        place_action(
            catalog,
            asset_id=under_homonym,
            action="ASSIGN",
            target_place_id=homonym,
            request_id="p2-08-case7-homonym",
        )
        place_action(
            catalog,
            asset_id=unassigned,
            action="ASSIGN",
            target_place_id=assigned_place,
            request_id="p2-08-case7-tokyo",
        )

        # Ancestor expansion is a default, not an accident: the city is reachable from the
        # country, and switching descendants off really removes it.
        assert _asset_ids(_assets(env, place_ids=[country])) == [under_country]
        assert _asset_ids(
            _assets(env, place_ids=[country], include_descendants="false")
        ) == []
        assert _asset_ids(_assets(env, place_ids=[city])) == [under_country]
        assert _asset_ids(_assets(env, place_ids=[homonym])) == [under_homonym]
        assert _asset_ids(_assets(env, place_ids=[city, homonym])) == sorted(
            [under_country, under_homonym]
        )

        # ``manual_unassigned`` is its own state, and it is never served as "suggested".
        place_action(
            catalog,
            asset_id=unassigned,
            action="UNASSIGN",
            request_id="p2-08-case7-unassign",
        )
        assert _asset_ids(_assets(env, place_state="manual_unassigned")) == [unassigned]
        # ``unassigned`` asks the effective-place question, and a manual lock has no
        # effective place either - both filters agree, and neither invents a suggestion.
        assert _asset_ids(_assets(env, place_state="unassigned")) == [unassigned]
        assert _asset_ids(_assets(env, place_state="assigned")) == sorted(
            [under_country, under_homonym]
        )
        assert _asset_ids(_assets(env, place_state="suggested")) == []

        illegal = _assets(env, place_ids=[city], place_state="suggested")
        assert illegal.status_code == 422
        assert illegal.json()["code"] == INVALID_PLACE_FILTER

        # Cross-source copies share one identity, so they are listed once and the source
        # filter ANDs with the place filter.
        second_root = Path(tmp_path) / "第二个来源"
        second_root.mkdir()
        (second_root / "拷贝.jpg").write_bytes(
            (env.library.root / "IMG_0001.JPG").read_bytes()
        )
        from photo_search.ingestion.service import CatalogService

        second_service = CatalogService.open(
            data_root=env.data_root,
            clock=env.library.clock,
            lease_seconds=60,
            wall_clock_ms=env.library.clock,
            stability_sleep=env.library.clock.advance,
        )
        try:
            registered = second_service.register_source(
                display_name="第二来源", root_path=second_root
            )
            second_source = int(registered["source_id"])
            assert second_service.run_scan(second_source)["state"] == "SUCCEEDED"
            counts = second_service.counts()
            assert counts["assets"] == 3
            assert counts["locations"] == 4
            duplicated = _assets(env, source_id=second_source)
            assert _asset_ids(duplicated) == [unassigned]
            assert _asset_ids(_assets(env, source_id=second_source, place_ids=[city])) == []
            assert _asset_ids(_assets(env, source_id=second_source, availability="present")) == [
                unassigned
            ]
        finally:
            second_service.close()
    finally:
        env.context.close()
        env.library.close()


# ------------------------------------------------------------- 8. background competition
def test_a_live_session_answers_with_identifiable_staleness_codes(tmp_path: Path) -> None:
    """§12.4 item 32 (session half): never an empty page, never a silently new order."""
    env = acceptance_env(tmp_path)
    try:
        catalog = env.catalog
        place_id = make_source_place(catalog, "杭州西湖")
        target = env.assets["beach sunset.jpg"]
        place_action(
            catalog,
            asset_id=target,
            action="ASSIGN",
            target_place_id=place_id,
            request_id="p2-08-case8-assign",
        )

        first = _search(env, {"request_id": "p2-08-case8-page1", "page_size": 1}).json()
        assert first["has_more"] is True
        cursor = first["next_cursor"]
        session = first["search_session_id"]
        second = _search(
            env,
            {
                "request_id": "p2-08-case8-page2",
                "page_size": 1,
                "search_session_id": session,
                "cursor": cursor,
            },
        ).json()
        assert second["page_offset"] == 1
        assert [int(row["asset_id"]) for row in first["hits"]] != [
            int(row["asset_id"]) for row in second["hits"]
        ]
        # A second, independent session sees the same frozen order for the same conditions.
        replay = _search(env, {"request_id": "p2-08-case8-replay", "page_size": 1}).json()
        assert [int(row["asset_id"]) for row in replay["hits"]] == [
            int(row["asset_id"]) for row in first["hits"]
        ]

        # A place edit invalidates the live session instead of silently re-ranking it.
        place_action(
            catalog,
            asset_id=env.assets["IMG_0001.JPG"],
            action="ASSIGN",
            target_place_id=place_id,
            request_id="p2-08-case8-late",
        )
        stale = _search(
            env,
            {
                "request_id": "p2-08-case8-stale",
                "page_size": 1,
                "search_session_id": session,
                "cursor": cursor,
            },
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == CATALOG_FILTER_REVISION_STALE

        # The suggestion revision is independent: a new machine resolution moves it without
        # touching the effective-place revision.
        install_dataset(env, tmp_path=tmp_path)
        gps_names = ("西湖 甲.jpg", "西湖 乙.jpg", "西湖 丙.jpg")
        gps_colours = ((10, 30, 200), (200, 30, 30), (30, 200, 30))
        for name, colour in zip(gps_names, gps_colours, strict=True):
            add_gps_photo(env, name, position=HANGZHOU_POSITION, colour=colour)
        rescan(env)
        first_gps = asset_for(env, "西湖 甲.jpg")
        second_gps = asset_for(env, "西湖 乙.jpg")
        third_gps = asset_for(env, "西湖 丙.jpg")
        # Distinct content, so these are three assets and not one asset with three locations.
        assert len({first_gps, second_gps, third_gps}) == 3
        run_gps(env, [first_gps, second_gps, third_gps])
        assert run_resolver(env, [first_gps, second_gps]) == 2
        assert [
            int(row["asset_id"])
            for row in catalog.read_all(
                "SELECT asset_id FROM place_resolutions ORDER BY asset_id"
            )
        ], "resolutions do not cover the two seeded assets"
        assert dataset_status(env)["state"] == "active"
        before_suggestion = suggestion_revision(catalog)
        suggestion_page = _search(
            env,
            {"request_id": "p2-08-case8-suggested", "place_state": "suggested",
             "page_size": 1},
        ).json()
        assert suggestion_page["suggestion_revision"] == before_suggestion
        assert suggestion_page["total_candidates"] >= 2
        assert suggestion_page["has_more"] is True
        assert run_resolver(env, [third_gps], now_ms=3_000) == 1
        assert suggestion_revision(catalog) > before_suggestion, [
            dict(row)
            for row in catalog.read_all("SELECT * FROM place_resolutions ORDER BY asset_id")
        ]
        assert filter_revision(catalog) == int(suggestion_page["catalog_filter_revision"])
        suggestion_stale = _search(
            env,
            {
                "request_id": "p2-08-case8-suggestion-stale",
                "place_state": "suggested",
                "page_size": 1,
                "search_session_id": suggestion_page["search_session_id"],
                "cursor": suggestion_page["next_cursor"],
            },
        )
        assert suggestion_stale.status_code == 409
        assert suggestion_stale.json()["code"] == PLACE_SUGGESTION_REVISION_STALE

        # A profile switch mid-session is a binding change, not a new ranking.
        env.create_profile("profile-1")
        activate_profile(catalog, profile_id="profile-1", now_ms=1_700_000_001_000)
        bound = _search(
            env,
            {
                "request_id": "p2-08-case8-bound",
                "text": "sunset",
                "page_size": 1,
            },
        ).json()
        assert bound["profile_id"] == "profile-1"
        env.create_profile("profile-2")
        activate_profile(catalog, profile_id="profile-2", now_ms=1_700_000_002_000)
        switched = _search(
            env,
            {
                "request_id": "p2-08-case8-switched",
                "text": "sunset",
                "page_size": 1,
                "search_session_id": bound["search_session_id"],
                "cursor": bound["next_cursor"],
            },
        )
        assert switched.status_code == 409
        assert switched.json()["code"] == PROFILE_GENERATION_MISMATCH

        # A restarted process has no session at all, and says so instead of serving page one.
        restarted = _search_on_app(
            env,
            fresh_app(env),
            {
                "request_id": "p2-08-case8-restarted",
                "search_session_id": session,
                "cursor": cursor,
            },
        )
        assert restarted.status_code == 409, restarted.text
        assert restarted.json()["code"] == SEARCH_SESSION_EXPIRED
    finally:
        env.context.close()
        env.library.close()


# --------------------------------------------------------------- 10. upgrade boundary
def test_an_old_ready_generation_keeps_serving_a_cancelled_upgrade(tmp_path: Path) -> None:
    """§12.4 item 12, limited to what this build implements: old READY stays servable."""
    env = acceptance_env(tmp_path)
    try:
        catalog = env.catalog
        before = catalog.read_one(
            "SELECT state, is_active FROM index_generations WHERE generation_id = ?",
            (env.generation,),
        )
        assert str(before["state"]) == "READY" and bool(before["is_active"])
        env.create_profile("profile-1")
        activate_profile(catalog, profile_id="profile-1", now_ms=1_700_000_001_000)

        # A half-built upgrade target: the generation exists but is not READY.
        upgrade_generation = env.create_generation(
            generation_id="image-gen-upgrade", state="PENDING"
        )
        env.create_profile("profile-upgrade", generation_id=upgrade_generation)
        with pytest.raises(CatalogError) as refused:
            activate_profile(catalog, profile_id="profile-upgrade", now_ms=1_700_000_003_000)
        assert refused.value.code == PROFILE_GENERATION_MISMATCH
        assert refused.value.details["reason"] == "GENERATION_NOT_READY"
        assert env.pointer() == "profile-1"

        # The cancelled upgrade changed nothing about the served version.
        after = catalog.read_one(
            "SELECT state, is_active FROM index_generations WHERE generation_id = ?",
            (env.generation,),
        )
        assert dict(after) == dict(before)
        body = _search(env, {"request_id": "p2-08-case10", "text": "sunset"}).json()
        assert body["profile_id"] == "profile-1"
        assert body["image_generation_id"] == env.generation
        assert [int(row["asset_id"]) for row in body["hits"]]
    finally:
        env.context.close()
        env.library.close()


# ------------------------------------------------------------------------ 11. upload
def test_upload_search_writes_nothing_and_reuses_the_authoritative_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§12.4 items 2/3 for the P2-07 upload path, under a live scan and place edit."""
    env = acceptance_env(tmp_path)
    try:
        catalog = env.catalog
        tracked = (
            "assets",
            "file_locations",
            "artifacts",
            "index_entries",
            "index_outbox",
            "search_documents",
            "search_projection_queue",
            "search_feedback",
        )

        def snapshot() -> dict[str, Any]:
            counts = {
                name: int(catalog.read_one(f"SELECT COUNT(*) AS value FROM {name}")["value"])
                for name in tracked
            }
            return {
                "counts": counts,
                "catalog_filter_revision": filter_revision(catalog),
                "assets": [
                    (int(row["asset_id"]), int(row["created_at"]))
                    for row in catalog.read_all(
                        "SELECT asset_id, created_at FROM assets ORDER BY asset_id"
                    )
                ],
            }

        place_id = make_source_place(catalog, "杭州西湖")
        target = env.assets["beach sunset.jpg"]
        place_action(
            catalog,
            asset_id=target,
            action="ASSIGN",
            target_place_id=place_id,
            request_id="p2-08-case11-assign",
        )

        # A killed scan is in flight while the browser uploads.
        add_photo(env, "并发 扫描 中.jpg", index=71)
        with pytest.raises(ScanInterrupted):
            rescan(env, interrupt_after=1)
        running_scan = catalog.read_one(
            "SELECT scan_id, state, finished_at FROM scan_runs ORDER BY scan_id DESC LIMIT 1"
        )
        assert str(running_scan["state"]) == "RUNNING"

        # The manual edit queued a projection refresh, so the confirmed vector entry of the
        # edited asset is demoted until the coordinator replays it. An image query must name
        # that instead of quietly returning a smaller answer.
        demoted = catalog.read_one(
            "SELECT state FROM index_entries WHERE asset_id = ?", (target,)
        )
        assert str(demoted["state"]) == "PENDING_REPAIR"
        catching_up_response = _upload(
            env, _jpeg(tmp_path), fields={"request_id": "img-catching-up"}
        )
        assert catching_up_response.status_code == 200, catching_up_response.text
        catching_up = catching_up_response.json()
        assert target not in [int(row["asset_id"]) for row in catching_up["hits"]]
        assert target in [
            int(entry["asset_id"]) for entry in catching_up["pending_semantic"]["entries"]
        ]
        assert catching_up["pending_semantic"]["reason"] == "ENTRY_NOT_CONFIRMED"
        assert catching_up["catalog_filter_revision"] == filter_revision(catalog)
        from photo_search.indexing.store import IndexCoordinator

        assert IndexCoordinator(catalog).process_all(now_ms=2_000) >= 1
        assert str(
            catalog.read_one("SELECT state FROM index_entries WHERE asset_id = ?", (target,))[
                "state"
            ]
        ) == "CONFIRMED"

        before = snapshot()
        directories_before = _upload_directories()

        # Failure paths write nothing and leave no temporary directory behind.
        broken = _upload(env, b"this is not an image", fields={"request_id": "img-bad"})
        assert broken.status_code == 409, broken.text
        assert broken.json()["code"] == "DECODE_FAILED"
        monkeypatch.setattr(routes, "DEFAULT_LIMITS", UploadLimits(max_pixels=100))
        too_many_pixels = _upload(env, _jpeg(tmp_path), fields={"request_id": "img-pixels"})
        assert too_many_pixels.status_code == 422, too_many_pixels.text
        assert too_many_pixels.json()["code"] == "UPLOAD_TOO_MANY_PIXELS"
        monkeypatch.setattr(routes, "DEFAULT_LIMITS", UploadLimits(max_bytes=16))
        too_large = _upload(env, _jpeg(tmp_path), fields={"request_id": "img-large"})
        assert too_large.status_code == 413, too_large.text
        assert too_large.json()["code"] == "UPLOAD_TOO_LARGE"
        monkeypatch.undo()

        assert snapshot() == before
        assert _upload_directories() == directories_before
        assert str(
            catalog.read_one(
                "SELECT state FROM scan_runs WHERE scan_id = ?", (int(running_scan["scan_id"]),)
            )["state"]
        ) == "RUNNING"

        # The success path reuses the same ranking and the same authoritative re-check.
        payload = _jpeg(tmp_path)
        uploaded = _upload(
            env,
            payload,
            fields={"request_id": "img-ok", "place_ids": str(place_id), "page_size": "1"},
        )
        assert uploaded.status_code == 200, uploaded.text
        image_body = uploaded.json()
        assert image_body["image_query"]["digest"] == hashlib.sha256(payload).hexdigest()
        assert [int(row["asset_id"]) for row in image_body["hits"]] == [target], image_body
        assert image_body["hits"][0]["hit_reason"] == "SEMANTIC"
        # The successful upload wrote nothing either - the image never becomes an asset.
        assert snapshot() == before

        text_response = _search(
            env,
            {
                "request_id": "img-ok-text",
                "text": "sunset",
                "place_ids": [place_id],
                **env.explicit_binding(),
            },
        )
        assert text_response.status_code == 200, text_response.text
        text_image = text_response.json()
        assert target in [int(row["asset_id"]) for row in text_image["hits"]]
        assert all(
            int(row["asset_id"]) == target for row in text_image["hits"]
        )

        # A session cursor is bound to the image bytes and to the authority revision.
        session_body = _upload(
            env,
            payload,
            fields={"request_id": "img-session", "page_size": "1"},
        ).json()
        assert session_body["has_more"] is True
        place_action(
            catalog,
            asset_id=target,
            action="UNASSIGN",
            request_id="p2-08-case11-unassign",
        )
        continued = _upload(
            env,
            payload,
            fields={
                "request_id": "img-session-next",
                "page_size": "1",
                "search_session_id": session_body["search_session_id"],
                "cursor": session_body["next_cursor"],
            },
        )
        assert continued.status_code == 409, continued.text
        assert continued.json()["code"] == CATALOG_FILTER_REVISION_STALE

        # Both channels answer the *same* authority question after the edit, with no hits.
        rerun_image = _upload(
            env,
            payload,
            fields={"request_id": "img-after", "place_ids": str(place_id)},
        ).json()
        rerun_text = _search(
            env,
            {
                "request_id": "img-after-text",
                "text": "sunset",
                "place_ids": [place_id],
                **env.explicit_binding(),
            },
        ).json()
        assert rerun_image["hits"] == []
        assert rerun_text["hits"] == []
        assert rerun_image["catalog_filter_revision"] == rerun_text["catalog_filter_revision"]
    finally:
        env.context.close()
        env.library.close()
