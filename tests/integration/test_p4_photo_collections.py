from __future__ import annotations

import hashlib

import pytest
from p2_search_api_fixtures import build_search_env
from p3_persons_page_fixtures import mark_location_missing, seed_library

from photo_search.backup import create_backup, restore_backup
from photo_search.catalog.db import Catalog
from photo_search.catalog.sqlite_io import open_readonly
from photo_search.config import catalog_db_path
from photo_search.library_retention import library_retention_preview
from photo_search.photo_collections import create_collection, edit_members
from photo_search.search.profile import activate_profile


def test_filter_search_snapshot_and_photo_collection_search_scope(api_env):
    seeded = seed_library(api_env, {"one.jpg": [], "two.jpg": [], "three.jpg": []})
    expected_ids = {int(item["asset_id"]) for item in seeded.values()}

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        search = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "photo-collection-source", "retrieval_mode": "text",
            "original_query": "", "page_size": 1,
        })
        assert search.status_code == 200, search.text
        source = search.json()
        assert source["photo_collection_source_mode"] == "all_filter_matches"
        assert source["photo_collection_source_token"]

        saved = await client.post("/api/v1/photo-collections/from-search", headers=headers, json={
            "name": "All filtered", "create_request_id": "save-filtered-search",
            "source_token": source["photo_collection_source_token"],
            "mode": "all_filter_matches",
        })
        assert saved.status_code == 200, saved.text
        collection = saved.json()["collection"]
        assert collection["member_count"] == len(expected_ids)
        destination = await client.post("/api/v1/photo-collections", headers=headers, json={
            "name": "Append target", "create_request_id": "create-append-target",
        })
        assert destination.status_code == 200, destination.text
        target = destination.json()["collection"]
        appended = await client.post(
            f"/api/v1/photo-collections/{target['collection_id']}/members/from-search",
            headers=headers, json={"request_id":"append-all-filtered-once",
                "source_token":source["photo_collection_source_token"],
                "mode":"all_filter_matches", "instance_token":target["instance_token"],
                "expected_revision":target["revision"]},
        )
        assert appended.status_code == 200, appended.text
        assert appended.json()["changed"] == len(expected_ids)
        api_env.app.state.api._photo_collection_sources.clear()
        replayed_append = await client.post(
            f"/api/v1/photo-collections/{target['collection_id']}/members/from-search",
            headers=headers, json={"request_id":"append-all-filtered-once",
                "source_token":source["photo_collection_source_token"],
                "mode":"all_filter_matches", "instance_token":target["instance_token"],
                "expected_revision":target["revision"]},
        )
        assert replayed_append.status_code == 200, replayed_append.text
        assert replayed_append.json()["reused"] is True
        assert replayed_append.json()["changed"] == len(expected_ids)
        assert api_env.service.catalog.read_one(
            "SELECT member_count FROM photo_collections WHERE collection_id=?", (target["collection_id"],)
        )[0] == len(expected_ids)
        api_env.app.state.api._photo_collection_sources.clear()
        replay = await client.post("/api/v1/photo-collections/from-search", headers=headers, json={
            "name": "All filtered", "create_request_id": "save-filtered-search",
            "source_token": source["photo_collection_source_token"],
            "mode": "all_filter_matches",
        })
        assert replay.status_code == 200, replay.text
        assert replay.json()["reused"] is True
        assert replay.json()["collection"]["collection_id"] == collection["collection_id"]

        page = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "photo-collection-scoped-search", "retrieval_mode": "text",
            "original_query": "", "page_size": 1,
            "search_scope": {"kind": "photo_collection",
                "photo_collection_id": collection["collection_id"],
                "instance_token": collection["instance_token"], "revision": collection["revision"]},
        })
        assert page.status_code == 200, page.text
        scoped = page.json()
        assert scoped["total_candidates"] == len(expected_ids)
        assert int(scoped["hits"][0]["asset_id"]) in expected_ids

        assets = await client.get(
            f"/api/v1/photo-collections/{collection['collection_id']}/assets?limit=50"
            f"&instance_token={collection['instance_token']}"
        )
        assert assets.status_code == 200, assets.text
        assert {int(item["asset_id"]) for item in assets.json()["assets"]} == expected_ids
        assert saved.json()["reused"] is False
        return collection

    api_env.scenario(scenario)


def test_filter_search_save_avoids_recounting_each_page(api_env, monkeypatch):
    seeded = seed_library(api_env, {f"filter-page-{index:03}.jpg": [] for index in range(120)})
    expected_ids = sorted(int(item["asset_id"]) for item in seeded.values())
    original_read_one = Catalog.read_one
    full_counts: list[str] = []
    offset_counts: list[str] = []

    def count_reads(self, sql, parameters=()):
        normalized = " ".join(sql.lower().split())
        if normalized.startswith("select count(*) as value from assets as a where"):
            (offset_counts if "a.asset_id<=?" in normalized else full_counts).append(normalized)
        return original_read_one(self, sql, parameters)

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        source = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "filter-page-source", "retrieval_mode": "text",
            "original_query": "", "page_size": 50,
        })
        assert source.status_code == 200, source.text
        token = source.json()["photo_collection_source_token"]

        monkeypatch.setattr(Catalog, "read_one", count_reads)
        saved = await client.post("/api/v1/photo-collections/from-search", headers=headers, json={
            "name": "Paged filter result", "create_request_id": "save-filter-pages",
            "source_token": token, "mode": "all_filter_matches",
        })
        assert saved.status_code == 200, saved.text
        assert len(full_counts) == 1
        assert offset_counts == []
        collection = saved.json()["collection"]
        members = api_env.service.catalog.read_all(
            "SELECT asset_id FROM photo_collection_members WHERE collection_id=? ORDER BY position",
            (collection["collection_id"],),
        )
        assert [int(row["asset_id"]) for row in members] == expected_ids

        full_counts.clear()
        offset_counts.clear()
        target = await client.post("/api/v1/photo-collections", headers=headers, json={
            "name": "Paged append target", "create_request_id": "create-filter-page-target",
        })
        assert target.status_code == 200, target.text
        target_collection = target.json()["collection"]
        appended = await client.post(
            f"/api/v1/photo-collections/{target_collection['collection_id']}/members/from-search",
            headers=headers, json={"request_id": "append-filter-pages", "source_token": token,
                "mode": "all_filter_matches", "instance_token": target_collection["instance_token"],
                "expected_revision": target_collection["revision"]},
        )
        assert appended.status_code == 200, appended.text
        assert appended.json()["changed"] == len(expected_ids)
        assert len(full_counts) == 1
        assert offset_counts == []

    api_env.scenario(scenario)


def test_ranked_search_save_refuses_a_changed_photo_collection_scope(api_env):
    seeded = seed_library(api_env, {"sunset-one.jpg": [], "sunset-two.jpg": []})
    first_id = int(seeded["sunset-one.jpg"]["asset_id"])
    second_id = int(seeded["sunset-two.jpg"]["asset_id"])
    collection = create_collection(api_env.service.catalog, "Scoped source", "scoped-source",
                                   asset_ids=[first_id])["collection"]

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        response = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id":"ranked-source", "retrieval_mode":"text", "original_query":"sunset",
            "page_size":1, "search_scope":{"kind":"photo_collection",
                "photo_collection_id":collection["collection_id"],
                "instance_token":collection["instance_token"], "revision":collection["revision"]},
        })
        assert response.status_code == 200, response.text
        source = response.json()
        assert source["photo_collection_source_mode"] == "ranked_candidates"
        edit_members(api_env.service.catalog, collection["collection_id"], [second_id], action="add",
                     instance_token=collection["instance_token"], expected_revision=collection["revision"])
        saved = await client.post("/api/v1/photo-collections/from-search", headers=headers, json={
            "name":"Stale result", "create_request_id":"stale-ranked-save",
            "source_token":source["photo_collection_source_token"], "mode":"ranked_candidates",
        })
        assert saved.status_code == 409, saved.text
        assert saved.json()["code"] == "PHOTO_COLLECTION_REVISION_STALE"
        assert api_env.service.catalog.read_one(
            "SELECT COUNT(*) FROM photo_collections WHERE name='Stale result'")[0] == 0

    api_env.scenario(scenario)


def test_ranked_search_save_preserves_text_candidate_order(api_env):
    seed_library(api_env, {"sunset-one.jpg": [], "sunset-two.jpg": [], "sunset-three.jpg": []})

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        search = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "text-ranked-source", "retrieval_mode": "text",
            "original_query": "sunset", "page_size": 1,
        })
        assert search.status_code == 200, search.text
        source = search.json()
        assert source["retrieval_mode"] == "text"
        assert source["photo_collection_source_mode"] == "ranked_candidates"
        assert source["photo_collection_source_token"]
        session = api_env.context.search_sessions.get(source["search_session_id"])
        assert session is not None
        expected = [int(entry.asset_id) for entry in session.entries]
        assert expected
        saved = await client.post("/api/v1/photo-collections/from-search", headers=headers, json={
            "name": "Saved text", "create_request_id": "save-text",
            "source_token": source["photo_collection_source_token"], "mode": "ranked_candidates",
        })
        assert saved.status_code == 200, saved.text
        collection_id = int(saved.json()["collection"]["collection_id"])
        stored = [int(row[0]) for row in api_env.service.catalog.read_all(
            "SELECT asset_id FROM photo_collection_members WHERE collection_id=? ORDER BY position",
            (collection_id,),
        )]
        assert stored == expected

    api_env.scenario(scenario)


def test_combined_ranked_search_save_preserves_candidate_order(tmp_path):
    env = build_search_env(tmp_path)
    try:
        profile_id = env.create_profile("photo-collection-combined")
        activate_profile(env.catalog, profile_id=profile_id)

        async def scenario(client):
            csrf = await env.authenticate(client)
            headers = env.post_headers(csrf)
            search = await client.post("/api/v1/search/place-aware", headers=headers, json={
                "request_id": "combined-ranked-source", "original_query": "sunset",
                "page_size": 1,
            })
            assert search.status_code == 200, search.text
            source = search.json()
            assert source["retrieval_mode"] == "combined"
            assert source["photo_collection_source_mode"] == "ranked_candidates"
            session = env.context.search_sessions.get(source["search_session_id"])
            assert session is not None
            expected = [int(entry.asset_id) for entry in session.entries]
            assert expected
            saved = await client.post("/api/v1/photo-collections/from-search", headers=headers, json={
                "name": "Saved combined", "create_request_id": "save-combined",
                "source_token": source["photo_collection_source_token"], "mode": "ranked_candidates",
            })
            assert saved.status_code == 200, repr(saved.json())
            collection_id = int(saved.json()["collection"]["collection_id"])
            stored = [int(row[0]) for row in env.catalog.read_all(
                "SELECT asset_id FROM photo_collection_members WHERE collection_id=? ORDER BY position",
                (collection_id,),
            )]
            assert stored == expected

        env.scenario(scenario)
    finally:
        env.context.close()
        env.library.close()


def test_combined_search_save_rejects_changed_active_profile(tmp_path):
    env = build_search_env(tmp_path)
    try:
        first = env.create_profile("photo-collection-profile-before")
        activate_profile(env.catalog, profile_id=first)

        async def scenario(client):
            csrf = await env.authenticate(client)
            headers = env.post_headers(csrf)
            search = await client.post("/api/v1/search/place-aware", headers=headers, json={
                "request_id": "combined-profile-source", "original_query": "sunset",
                "page_size": 1,
            })
            assert search.status_code == 200, search.text
            second = env.create_profile("photo-collection-profile-after")
            activate_profile(env.catalog, profile_id=second)
            saved = await client.post("/api/v1/photo-collections/from-search", headers=headers, json={
                "name": "Stale profile", "create_request_id": "save-stale-profile",
                "source_token": search.json()["photo_collection_source_token"],
                "mode": "ranked_candidates",
            })
            assert saved.status_code == 409
            assert saved.json()["code"] in {
                "SEARCH_SESSION_STALE", "SEARCH_PROFILE_CHANGED", "PROFILE_GENERATION_MISMATCH",
            }
            assert env.catalog.read_one(
                "SELECT COUNT(*) FROM photo_collections WHERE name='Stale profile'"
            )[0] == 0

        env.scenario(scenario)
    finally:
        env.context.close()
        env.library.close()


def test_capture_range_filter_save_keeps_the_frozen_capture_scope(api_env):
    seeded = seed_library(api_env, {"inside.jpg": [], "outside.jpg": [], "file-time.jpg": []})
    from photo_search.taken_time.contract import civil_key

    with api_env.service.catalog.write() as connection:
        for name, source, sort_key in (
            ("inside.jpg", "EXIF_ORIGINAL", civil_key(2020, 6, 1)),
            ("outside.jpg", "EXIF_DIGITIZED", civil_key(2022, 6, 1)),
            ("file-time.jpg", "EXIF_ROOT", civil_key(2020, 6, 1)),
        ):
            confidence = "HIGH" if source == "EXIF_ORIGINAL" else ("MEDIUM" if source == "EXIF_DIGITIZED" else "LOW")
            offset = 0 if source == "EXIF_ORIGINAL" else None
            connection.execute(
                "INSERT INTO asset_metadata(asset_id,taken_at_raw,taken_at_sort,time_source,"
                "time_confidence,timezone_offset,camera) VALUES(?,?,?,?,?,?,?)",
                (seeded[name]["asset_id"], "2020:06:01 00:00:00", sort_key,
                 source, confidence, offset, None),
            )

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        request = {
            "request_id": "capture-filter-source", "retrieval_mode": "text",
            "original_query": "", "page_size": 1,
            "capture_from": "2020-01-01", "capture_to_exclusive": "2021-01-01",
        }
        search = await client.post("/api/v1/search/place-aware", headers=headers, json=request)
        assert search.status_code == 200, search.text
        source = search.json()
        assert source["photo_collection_source_mode"] == "all_filter_matches"
        assert [int(hit["asset_id"]) for hit in source["hits"]] == [seeded["inside.jpg"]["asset_id"]]
        saved = await client.post("/api/v1/photo-collections/from-search", headers=headers, json={
            "name": "Capture scoped", "create_request_id": "save-capture-scoped",
            "source_token": source["photo_collection_source_token"], "mode": "all_filter_matches",
        })
        assert saved.status_code == 200, saved.text
        members = api_env.service.catalog.read_all(
            "SELECT asset_id FROM photo_collection_members WHERE collection_id=? ORDER BY position",
            (saved.json()["collection"]["collection_id"],),
        )
        assert [int(row["asset_id"]) for row in members] == [seeded["inside.jpg"]["asset_id"]]

    api_env.scenario(scenario)


def test_search_source_token_rejects_wrong_mode_and_tampering(api_env):
    seed_library(api_env, {"one.jpg": [], "two.jpg": []})

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        response = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "token-filter-source", "retrieval_mode": "text",
            "original_query": "", "page_size": 1,
        })
        assert response.status_code == 200, response.text
        token = response.json()["photo_collection_source_token"]
        wrong_mode = await client.post("/api/v1/photo-collections/from-search", headers=headers, json={
            "name": "Wrong mode", "create_request_id": "wrong-mode-save",
            "source_token": token, "mode": "ranked_candidates",
        })
        altered = await client.post("/api/v1/photo-collections/from-search", headers=headers, json={
            "name": "Altered", "create_request_id": "altered-token-save",
            "source_token": token + "tampered", "mode": "all_filter_matches",
        })
        assert wrong_mode.status_code == 409
        assert altered.status_code == 409
        assert api_env.service.catalog.read_one(
            "SELECT COUNT(*) FROM photo_collections WHERE name IN ('Wrong mode','Altered')"
        )[0] == 0

    api_env.scenario(scenario)


def test_search_source_token_is_session_bound_and_expires_at_deadline(api_env, monkeypatch):
    context = api_env.context
    now_ms = [1_000_000]
    monkeypatch.setattr("photo_search.api.context.time.time_ns", lambda: now_ms[0] * 1_000_000)
    token = context.register_photo_collection_source(
        auth_session_id="session-a",
        payload={"request_id": "token-expiry", "retrieval_mode": "combined"},
        result={"search_session_id": "ranked-session"},
        mode="ranked_candidates",
    )
    assert token is not None
    assert context.resolve_photo_collection_source(token, auth_session_id="session-a")["mode"] == "ranked_candidates"
    with pytest.raises(ValueError, match="不属于当前应用会话"):
        context.resolve_photo_collection_source(token, auth_session_id="session-b")
    with pytest.raises(ValueError):
        context.resolve_photo_collection_source(token + "tampered", auth_session_id="session-a")
    now_ms[0] += 600_000
    with pytest.raises(ValueError, match="已过期"):
        context.resolve_photo_collection_source(token, auth_session_id="session-a")


def test_ranked_search_save_rejects_an_expired_candidate_session(api_env):
    seed_library(api_env, {"sunset-one.jpg": [], "sunset-two.jpg": []})

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        search = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "expired-ranked-source", "retrieval_mode": "text",
            "original_query": "sunset", "page_size": 1,
        })
        assert search.status_code == 200, search.text
        api_env.context.search_sessions.clear()
        saved = await client.post("/api/v1/photo-collections/from-search", headers=headers, json={
            "name": "Expired candidates", "create_request_id": "save-expired-ranked",
            "source_token": search.json()["photo_collection_source_token"],
            "mode": "ranked_candidates",
        })
        assert saved.status_code == 409
        assert saved.json()["code"] == "SEARCH_SESSION_EXPIRED"
        assert api_env.service.catalog.read_one(
            "SELECT COUNT(*) FROM photo_collections WHERE name='Expired candidates'"
        )[0] == 0

    api_env.scenario(scenario)


def test_filter_save_rechecks_catalog_revision_before_create(api_env):
    seed_library(api_env, {"one.jpg": [], "two.jpg": []})

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        response = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "filter-revision-source", "retrieval_mode": "text",
            "original_query": "", "page_size": 1,
        })
        assert response.status_code == 200, response.text
        from photo_search.places import revision

        with api_env.service.catalog.write() as connection:
            revision.bump_filter_revision(connection, cause="SOURCE_PHOTOS_REMOVED", now_ms=api_env.clock())
        saved = await client.post("/api/v1/photo-collections/from-search", headers=headers, json={
            "name": "Stale filter", "create_request_id": "stale-filter-save",
            "source_token": response.json()["photo_collection_source_token"],
            "mode": "all_filter_matches",
        })
        assert saved.status_code == 409, saved.text
        assert api_env.service.catalog.read_one(
            "SELECT COUNT(*) FROM photo_collections WHERE name='Stale filter'"
        )[0] == 0

    api_env.scenario(scenario)


@pytest.mark.parametrize("asset_count", [5000, 5001])
def test_all_filter_save_enforces_5000_candidate_limit_atomically(api_env, asset_count):
    with api_env.service.catalog.write() as connection:
        connection.executemany(
            "INSERT INTO assets(sha256,byte_size,created_at,state) VALUES(?,1,1700000000000,'ACTIVE')",
            [(hashlib.sha256(f"photo-collection-{index}".encode()).hexdigest(),)
             for index in range(asset_count)],
        )

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        response = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": f"filter-limit-{asset_count}", "retrieval_mode": "text",
            "original_query": "", "page_size": 50,
        })
        assert response.status_code == 200, response.text
        token = response.json()["photo_collection_source_token"]
        target = await client.post("/api/v1/photo-collections", headers=headers, json={
            "name": f"Append limit target {asset_count}",
            "create_request_id": f"append-target-{asset_count}",
        })
        assert target.status_code == 200, target.text
        appended = await client.post(
            f"/api/v1/photo-collections/{target.json()['collection']['collection_id']}/members/from-search",
            headers=headers, json={
                "request_id": f"append-limit-{asset_count}", "source_token": token,
                "mode": "all_filter_matches",
                "instance_token": target.json()["collection"]["instance_token"],
                "expected_revision": target.json()["collection"]["revision"],
            },
        )
        saved = await client.post("/api/v1/photo-collections/from-search", headers=headers, json={
            "name": f"Filter limit {asset_count}", "create_request_id": f"filter-save-{asset_count}",
            "source_token": token, "mode": "all_filter_matches",
        })
        if asset_count == 5000:
            assert appended.status_code == 200, appended.text
            assert appended.json()["changed"] == 5000
            assert saved.status_code == 200, saved.text
            assert saved.json()["collection"]["member_count"] == 5000
        else:
            assert appended.status_code == 409, appended.text
            assert appended.json()["code"] == "PHOTO_COLLECTION_FILTER_RESULT_TOO_LARGE"
            assert api_env.service.catalog.read_one(
                "SELECT member_count FROM photo_collections WHERE collection_id=?",
                (target.json()["collection"]["collection_id"],),
            )[0] == 0
            assert saved.status_code == 409, saved.text
            assert saved.json()["code"] == "PHOTO_COLLECTION_FILTER_RESULT_TOO_LARGE"
            assert api_env.service.catalog.read_one(
                "SELECT COUNT(*) FROM photo_collections WHERE name='Filter limit 5001'"
            )[0] == 0

    api_env.scenario(scenario)


def test_manual_backup_and_retention_keep_photo_collection_members(api_env, tmp_path):
    seeded = seed_library(api_env, {"retained.jpg": []})
    asset_id = int(seeded["retained.jpg"]["asset_id"])
    collection = create_collection(api_env.service.catalog, "Human retained", "retention-create",
                                   asset_ids=[asset_id])["collection"]
    mark_location_missing(api_env.service.catalog, asset_id)
    preview = library_retention_preview(api_env.service.catalog)
    item = next(row for row in preview["items"] if int(row["asset_id"]) == asset_id)
    assert item["has_human_or_protected_references"] is True
    assert "照片合集收录" in item["reason"]

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        backup = await client.post("/api/v1/storage/backup", json={}, headers=api_env.post_headers(csrf))
        assert backup.status_code == 200, backup.text
        assert backup.json()["status"] == "ok", backup.text
        api_env.close()
        bundle = tmp_path / "photo-collection-backup"
        create_backup(data_root=api_env.data_root, destination=bundle, stopped=True)
        restored_root = tmp_path / "restored-photo-collection-library"
        restored = restore_backup(bundle=bundle, data_root=restored_root)
        assert restored["status"] == "ok"
        saved = open_readonly(catalog_db_path(restored_root))
        try:
            assert saved.execute("PRAGMA foreign_key_check").fetchall() == []
            assert tuple(saved.execute(
                "SELECT collection_id,asset_id FROM photo_collection_members WHERE collection_id=?",
                (collection["collection_id"],),
            ).fetchone()) == (collection["collection_id"], asset_id)
        finally:
            saved.close()

    api_env.scenario(scenario)
