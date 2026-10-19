from __future__ import annotations

import pytest
from p3_persons_page_fixtures import seed_library

from photo_search import photo_collections, photo_smart_collections
from photo_search.api.models import PlaceAwareSearchRequestModel


def test_filter_search_creates_dynamic_smart_rule_and_exclusion_is_recoverable(api_env):
    seeded = seed_library(api_env, {"one.jpg": [], "two.jpg": [], "three.jpg": []})
    asset_id = int(next(iter(seeded.values()))["asset_id"])

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        search = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "smart-source-filter", "retrieval_mode": "text",
            "original_query": "", "page_size": 1,
        })
        assert search.status_code == 200, search.text
        source = search.json()
        assert source["photo_collection_source_mode"] == "all_filter_matches"
        saved = await client.post("/api/v1/photo-smart-collections/from-search", headers=headers, json={
            "name": "Dynamic photos", "create_request_id": "smart-create-filter",
            "source_token": source["photo_collection_source_token"],
        })
        assert saved.status_code == 200, saved.text
        collection = saved.json()["collection"]
        assert collection["execution_kind"] == "FILTER"
        detail = await client.get(
            f"/api/v1/photo-smart-collections/{collection['smart_id']}?instance_token={collection['instance_token']}"
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["rule"]["execution_kind"] == "FILTER"
        assert detail.json()["collection"]["freshness"] == "NO_CACHE"
        listed = await client.get("/api/v1/photo-smart-collections?limit=20")
        assert listed.status_code == 200, listed.text
        assert [item["smart_id"] for item in listed.json()["collections"]] == [collection["smart_id"]]
        page = await client.get(
            f"/api/v1/photo-smart-collections/{collection['smart_id']}/assets"
            f"?instance_token={collection['instance_token']}&limit=50"
        )
        assert page.status_code == 200, page.text
        assert len(page.json()["assets"]) == len(seeded)
        excluded = await client.put(
            f"/api/v1/photo-smart-collections/{collection['smart_id']}/exclusions/{asset_id}",
            headers=headers, json={"instance_token": collection["instance_token"], "expected_revision": 1},
        )
        assert excluded.status_code == 200, excluded.text
        rows = await client.get(
            f"/api/v1/photo-smart-collections/{collection['smart_id']}/exclusions"
            f"?instance_token={collection['instance_token']}"
        )
        assert rows.status_code == 200, rows.text
        assert [item["asset_id"] for item in rows.json()["assets"]] == [asset_id]
        after_exclusion = await client.get(
            f"/api/v1/photo-smart-collections/{collection['smart_id']}/assets"
            f"?instance_token={collection['instance_token']}&limit=50"
        )
        assert after_exclusion.status_code == 200, after_exclusion.text
        assert asset_id not in {int(item["asset_id"]) for item in after_exclusion.json()["assets"]}
        restored = await client.request("DELETE",
            f"/api/v1/photo-smart-collections/{collection['smart_id']}/exclusions/{asset_id}",
            headers=headers, json={"instance_token": collection["instance_token"], "expected_revision": 2},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["changed"] is True
        assert api_env.service.catalog.read_one(
            "SELECT rule_json FROM photo_smart_collections WHERE smart_id=?", (collection["smart_id"],)
        )[0].find(source["photo_collection_source_token"]) == -1
        return collection

    api_env.scenario(scenario)


def test_filter_exclusions_are_applied_before_keyset_page_without_counts(api_env):
    seed_library(api_env, {"base.jpg": []})
    total_extra = 200
    with api_env.service.catalog.write() as connection:
        source_id = int(connection.execute("SELECT source_id FROM source_roots LIMIT 1").fetchone()[0])
        connection.executemany(
            "INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) VALUES(?,?,8,?, 'ACTIVE')",
            [(asset_id, f"{asset_id:064x}", asset_id) for asset_id in range(1000, 1000 + total_extra)],
        )
        connection.executemany(
            "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,availability) "
            "VALUES(?,?,?, ?,8,1,'PRESENT')",
            [(source_id, f"{asset_id}.jpg", f"{asset_id}.jpg", asset_id)
             for asset_id in range(1000, 1000 + total_extra)],
        )
        expected = [int(row[0]) for row in connection.execute(
            "SELECT a.asset_id FROM assets a WHERE a.state='ACTIVE' ORDER BY a.asset_id"
        )]
    ratios = (0, 50, 95, 100)
    exclusion_plan = api_env.service.catalog.read_all(
        "EXPLAIN QUERY PLAN SELECT a.asset_id FROM assets AS a WHERE a.asset_id>? "
        "AND NOT EXISTS (SELECT 1 FROM photo_smart_exclusions AS pse "
        "WHERE pse.smart_id=? AND pse.asset_id=a.asset_id) ORDER BY a.asset_id LIMIT ?",
        (0, 1, 51),
    )
    assert any("photo_smart_exclusions" in str(row["detail"]).casefold()
               and "search" in str(row["detail"]).casefold() for row in exclusion_plan)
    # The four scenarios are run in one isolated catalog, changing only exclusions.

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        source = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "smart-many-filter-source", "retrieval_mode": "text",
            "original_query": "", "page_size": 50,
        })
        assert source.status_code == 200, source.text
        saved = await client.post("/api/v1/photo-smart-collections/from-search", headers=headers, json={
            "name": "Many filter photos", "create_request_id": "smart-many-filter-create",
            "source_token": source.json()["photo_collection_source_token"],
        })
        assert saved.status_code == 200, saved.text
        collection = saved.json()["collection"]
        actual_by_ratio = {}
        sql = api_env.service.catalog.connection
        for ratio in ratios:
            count = len(expected) * ratio // 100
            if count:
                with api_env.service.catalog.write() as connection:
                    connection.execute("DELETE FROM photo_smart_exclusions WHERE smart_id=?", (collection["smart_id"],))
                    connection.executemany(
                        "INSERT INTO photo_smart_exclusions(smart_id,asset_id,created_at_ms) VALUES(?,?,1)",
                        [(collection["smart_id"], asset_id) for asset_id in expected[:count]],
                    )
                    connection.execute(
                        "UPDATE photo_smart_collections SET exclusions_revision=exclusions_revision+1,revision=revision+1 "
                        "WHERE smart_id=?", (collection["smart_id"],),
                    )
            elif ratio == 0:
                with api_env.service.catalog.write() as connection:
                    connection.execute("DELETE FROM photo_smart_exclusions WHERE smart_id=?", (collection["smart_id"],))
                    connection.execute(
                        "UPDATE photo_smart_collections SET exclusions_revision=exclusions_revision+1,revision=revision+1 "
                        "WHERE smart_id=?", (collection["smart_id"],),
                    )
            excluded = set(expected[:count])
            seen = []
            cursor = None
            statements = []
            sql.set_trace_callback(statements.append)
            try:
                while True:
                    params = {"instance_token": collection["instance_token"], "limit": 50}
                    if cursor is not None:
                        params["cursor"] = cursor
                    page = await client.get(
                        f"/api/v1/photo-smart-collections/{collection['smart_id']}/assets", params=params)
                    assert page.status_code == 200, page.text
                    payload = page.json()
                    seen.extend(int(item["asset_id"]) for item in payload["assets"])
                    cursor = payload["next_cursor"]
                    if cursor is None:
                        break
            finally:
                sql.set_trace_callback(None)
            expected_page = [asset_id for asset_id in expected if asset_id not in excluded]
            assert seen == expected_page
            assert len(seen) == len(set(seen))
            assert sum("select count(*) as value from assets as a where" in item.casefold()
                       for item in statements) == 0
            actual_by_ratio[ratio] = len(seen)
        return actual_by_ratio

    actual = api_env.scenario(scenario)
    assert actual == {ratio: len(expected) - len(expected) * ratio // 100 for ratio in ratios}


def test_filter_capture_desc_uses_global_trusted_time_order_and_keyset_pages(api_env):
    seed_library(api_env, {"base.jpg": []})
    count = 127
    with api_env.service.catalog.write() as connection:
        source_id = int(connection.execute("SELECT source_id FROM source_roots LIMIT 1").fetchone()[0])
        connection.executemany(
            "INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) VALUES(?,?,8,?, 'ACTIVE')",
            [(asset_id, f"{asset_id:064x}", asset_id) for asset_id in range(2000, 2000 + count)],
        )
        connection.executemany(
            "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,availability) "
            "VALUES(?,?,?, ?,8,1,'PRESENT')",
            [(source_id, f"{asset_id}.jpg", f"{asset_id}.jpg", asset_id)
             for asset_id in range(2000, 2000 + count)],
        )
        asset_ids = [int(row[0]) for row in connection.execute(
            "SELECT asset_id FROM assets WHERE state='ACTIVE' ORDER BY asset_id"
        )]
        metadata = []
        trusted = {}
        for offset, asset_id in enumerate(asset_ids):
            kind = offset % 5
            if kind in (0, 1):
                key = 7000 + offset // 3
                source = "EXIF_ORIGINAL" if kind == 0 else "EXIF_DIGITIZED"
                trusted[asset_id] = key
                metadata.append((asset_id, key, source, "HIGH" if source == "EXIF_ORIGINAL" else "MEDIUM"))
            elif kind == 2:
                # EXIF_ROOT is outside the trusted capture bucket and belongs in the tail.
                metadata.append((asset_id, 300000000000, "EXIF_ROOT", "LOW"))
            elif kind == 3:
                metadata.append((asset_id, None, "FILE_MTIME", "LOW"))
        connection.executemany(
            "INSERT INTO asset_metadata(asset_id,taken_at_sort,time_source,time_confidence) VALUES(?,?,?,?)",
            metadata,
        )
    expected = sorted(trusted, key=lambda asset_id: (trusted[asset_id], asset_id), reverse=True)
    expected.extend(sorted(set(asset_ids) - set(trusted), reverse=True))
    plan = api_env.service.catalog.read_all(
        "EXPLAIN QUERY PLAN SELECT a.asset_id,m.taken_at_sort FROM asset_metadata AS m "
        "INDEXED BY idx_asset_metadata_capture CROSS JOIN assets AS a ON a.asset_id=m.asset_id "
        "WHERE m.time_source=? AND m.taken_at_sort IS NOT NULL "
        "ORDER BY m.taken_at_sort DESC,m.asset_id DESC LIMIT ?", ("EXIF_ORIGINAL", 51))
    assert any("idx_asset_metadata_capture" in str(row["detail"]) for row in plan)

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        source = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "smart-capture-filter-source", "retrieval_mode": "text",
            "original_query": "", "page_size": 50,
        })
        assert source.status_code == 200, source.text
        saved = await client.post("/api/v1/photo-smart-collections/from-search", headers=headers, json={
            "name": "Capture ordered filter", "create_request_id": "smart-capture-filter-create",
            "source_token": source.json()["photo_collection_source_token"],
        })
        assert saved.status_code == 200, saved.text
        collection = saved.json()["collection"]
        seen = []
        cursor = None
        first_cursor = None
        count_queries = []
        api_env.service.catalog.connection.set_trace_callback(count_queries.append)
        try:
            while True:
                params = {"instance_token": collection["instance_token"], "limit": 50,
                          "order": "capture_desc"}
                if cursor is not None:
                    params["cursor"] = cursor
                page = await client.get(
                    f"/api/v1/photo-smart-collections/{collection['smart_id']}/assets", params=params)
                assert page.status_code == 200, page.text
                payload = page.json()
                seen.extend(int(item["asset_id"]) for item in payload["assets"])
                cursor = payload["next_cursor"]
                if first_cursor is None:
                    first_cursor = cursor
                if cursor is None:
                    break
        finally:
            api_env.service.catalog.connection.set_trace_callback(None)
        assert sum("select count(*) as value from assets as a where" in item.casefold()
                   for item in count_queries) == 0
        from photo_search.places.revision import bump_filter_revision

        with api_env.service.catalog.write() as connection:
            bump_filter_revision(connection, cause="SMART_CAPTURE_CURSOR_TEST", now_ms=1)
        stale = await client.get(
            f"/api/v1/photo-smart-collections/{collection['smart_id']}/assets",
            params={"instance_token": collection["instance_token"], "limit": 50,
                    "order": "capture_desc", "cursor": first_cursor},
        )
        return seen, stale.status_code

    seen, stale_status = api_env.scenario(scenario)
    assert seen == expected
    assert stale_status == 409


def test_place_filter_smart_exclusion_capture_desc_continuation(api_env, monkeypatch):
    seeded = seed_library(api_env, {f"place-{index:02}.jpg": [] for index in range(53)})
    asset_ids = sorted(int(value["asset_id"]) for value in seeded.values())
    from photo_search.api import routes
    from photo_search.places import revision as place_revision

    place_choice = {"kind": "city", "key": "42", "name": "杭州", "parent": "浙江省",
                    "origin": None, "source": "synthetic", "aliases": ["杭州"]}
    monkeypatch.setattr(routes.place_choices, "choices", lambda *args, **kwargs: [place_choice])
    filter_revision = place_revision.read_filter_revision(api_env.service.catalog.connection)
    suggestion_revision = place_revision.read_suggestion_revision(api_env.service.catalog.connection)
    package_revision = routes.place_albums._index(api_env.service.catalog, api_env.data_root)[1]
    monkeypatch.setattr(routes.place_albums, "album_member_ids", lambda *args, **kwargs: (
        [],
        {"albums": [{"key": "42", "name": "杭州", "parent": "浙江省", "origin": None}],
         "catalog_filter_revision": filter_revision,
         "suggestion_revision": suggestion_revision, "package_revision": package_revision},
    ))
    member_sql = "a.asset_id IN (" + ",".join("?" for _ in asset_ids) + ")"
    # The worker imports selected_members_sql locally from the timeline module.
    monkeypatch.setattr(
        "photo_search.places.timeline.selected_members_sql",
        lambda *_args, **_kwargs: (member_sql, tuple(asset_ids), {
            "catalog_filter_revision": filter_revision,
            "suggestion_revision": suggestion_revision,
            "package_revision": package_revision,
        }),
    )
    metadata = []
    trusted: dict[int, int] = {}
    for offset, asset_id in enumerate(asset_ids):
        kind = offset % 5
        if kind in (0, 1):
            captured = 90000 - ((offset * 37) % 23)
            source = "EXIF_ORIGINAL" if kind == 0 else "EXIF_DIGITIZED"
            trusted[asset_id] = captured
            metadata.append((asset_id, captured, source,
                             "HIGH" if source == "EXIF_ORIGINAL" else "MEDIUM"))
        elif kind == 2:
            metadata.append((asset_id, 300000000000, "EXIF_ROOT", "LOW"))
        elif kind == 3:
            metadata.append((asset_id, None, "FILE_MTIME", "LOW"))
    with api_env.service.catalog.write() as connection:
        connection.executemany(
            "INSERT INTO asset_metadata(asset_id,taken_at_sort,time_source,time_confidence) VALUES(?,?,?,?)",
            metadata,
        )
    expected = sorted(trusted, key=lambda asset_id: (trusted[asset_id], asset_id), reverse=True)
    expected.extend(sorted(set(asset_ids) - set(trusted), reverse=True))
    excluded_id = expected[0]
    expected.remove(excluded_id)
    capture_calls = []
    count_reads = []
    original_capture_page = routes._filter_capture_desc_rows
    catalog_type = type(api_env.service.catalog)
    original_read_one = catalog_type.read_one

    def capture_page(catalog, where, parameters, *, limit, position):
        capture_calls.append((where, tuple(parameters), position, limit))
        return original_capture_page(catalog, where, parameters, limit=limit, position=position)

    def read_one(catalog, sql, parameters=()):
        normalized = sql.casefold()
        if "select count(" in normalized and "from assets as a" in normalized:
            count_reads.append(sql)
        return original_read_one(catalog, sql, parameters)

    monkeypatch.setattr(routes, "_filter_capture_desc_rows", capture_page)
    monkeypatch.setattr(catalog_type, "read_one", read_one)

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        source = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "smart-place-capture-source", "original_query": "在杭州拍的照片",
            "page_size": 50,
        })
        assert source.status_code == 200, source.text
        assert source.json()["photo_collection_source_mode"] == "all_filter_matches"
        assert source.json()["place_scope"]["kind"] == "city"
        saved = await client.post("/api/v1/photo-smart-collections/from-search", headers=headers, json={
            "name": "杭州智能筛选", "create_request_id": "smart-place-capture-create",
            "source_token": source.json()["photo_collection_source_token"],
        })
        assert saved.status_code == 200, saved.text
        collection = saved.json()["collection"]
        excluded = await client.put(
            f"/api/v1/photo-smart-collections/{collection['smart_id']}/exclusions/{excluded_id}",
            headers=headers, json={"instance_token": collection["instance_token"], "expected_revision": 1},
        )
        assert excluded.status_code == 200, excluded.text
        count_reads.clear()
        seen = []
        cursor = None
        while True:
            params = {"instance_token": collection["instance_token"], "limit": 50,
                      "order": "capture_desc"}
            if cursor is not None:
                params["cursor"] = cursor
            page = await client.get(
                f"/api/v1/photo-smart-collections/{collection['smart_id']}/assets", params=params)
            assert page.status_code == 200, page.text
            body = page.json()
            seen.extend(int(item["asset_id"]) for item in body["assets"])
            cursor = body["next_cursor"]
            if cursor is None:
                break
        assert len(capture_calls) == 2
        assert capture_calls[0][2] is None and capture_calls[1][2] is not None
        assert count_reads == []
        assert all("photo_smart_exclusions" in where and "a.asset_id in (" in where.casefold()
                   for where, _, _, _ in capture_calls)
        return seen

    assert api_env.scenario(scenario) == expected
    where, parameters, _, limit = capture_calls[0]
    plan = api_env.service.catalog.read_all(
        "EXPLAIN QUERY PLAN SELECT a.asset_id,m.taken_at_sort FROM asset_metadata AS m "
        "INDEXED BY idx_asset_metadata_capture CROSS JOIN assets AS a ON a.asset_id=m.asset_id "
        f"WHERE m.time_source=? AND m.taken_at_sort IS NOT NULL AND ({where}) "
        "ORDER BY m.taken_at_sort DESC,m.asset_id DESC LIMIT ?",
        ("EXIF_ORIGINAL", *parameters, limit + 1),
    )
    details = " ".join(str(row["detail"]) for row in plan).casefold()
    assert "idx_asset_metadata_capture" in details
    assert "photo_smart_exclusions" in details and "search" in details


def test_filter_smart_save_static_is_idempotent_and_keeps_member_order(api_env):
    seeded = seed_library(api_env, {"one.jpg": [], "two.jpg": [], "three.jpg": []})
    expected = [int(item["asset_id"]) for item in seeded.values()]

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        source = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "smart-static-filter-source", "retrieval_mode": "text",
            "original_query": "", "page_size": 50,
        })
        assert source.status_code == 200, source.text
        saved = await client.post("/api/v1/photo-smart-collections/from-search", headers=headers, json={
            "name": "Static source", "create_request_id": "smart-static-filter-create",
            "source_token": source.json()["photo_collection_source_token"],
        })
        assert saved.status_code == 200, saved.text
        smart = saved.json()["collection"]
        excluded = await client.put(
            f"/api/v1/photo-smart-collections/{smart['smart_id']}/exclusions/{expected[1]}",
            headers=headers, json={"instance_token": smart["instance_token"], "expected_revision": 1},
        )
        assert excluded.status_code == 200, excluded.text
        result = await client.post(
            f"/api/v1/photo-smart-collections/{smart['smart_id']}/save-static", headers=headers, json={
                "instance_token": smart["instance_token"], "expected_revision": 2,
                "name": "Saved static", "create_request_id": "smart-static-filter-save",
            },
        )
        assert result.status_code == 200, result.text
        static = result.json()["collection"]
        page = await client.get(
            f"/api/v1/photo-collections/{static['collection_id']}/assets"
            f"?instance_token={static['instance_token']}&limit=50"
        )
        assert page.status_code == 200, page.text
        assert [int(item["asset_id"]) for item in page.json()["assets"]] == [expected[0], expected[2]]
        replay = await client.post(
            f"/api/v1/photo-smart-collections/{smart['smart_id']}/save-static", headers=headers, json={
                "instance_token": smart["instance_token"], "expected_revision": 2,
                "name": "Saved static", "create_request_id": "smart-static-filter-save",
            },
        )
        assert replay.status_code == 200 and replay.json()["reused"] is True
        assert replay.json()["collection"]["collection_id"] == static["collection_id"]
        smart_after = await client.get(
            f"/api/v1/photo-smart-collections/{smart['smart_id']}?instance_token={smart['instance_token']}"
        )
        assert smart_after.status_code == 200
        assert smart_after.json()["collection"]["revision"] == 2
        return static["collection_id"]

    assert api_env.scenario(scenario) > 0


def test_filter_smart_save_static_rejects_more_than_5000(api_env):
    seed_library(api_env, {"base.jpg": []})
    with api_env.service.catalog.write() as connection:
        source_id = int(connection.execute("SELECT source_id FROM source_roots LIMIT 1").fetchone()[0])
        connection.executemany(
            "INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) VALUES(?,?,8,?, 'ACTIVE')",
            [(asset_id, f"{asset_id:064x}", asset_id) for asset_id in range(20_000, 25_000)],
        )
        connection.executemany(
            "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,availability) "
            "VALUES(?,?,?, ?,8,1,'PRESENT')",
            [(source_id, f"{asset_id}.jpg", f"{asset_id}.jpg", asset_id)
             for asset_id in range(20_000, 25_000)],
        )

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        source = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "smart-static-too-many-source", "retrieval_mode": "text",
            "original_query": "", "page_size": 50,
        })
        saved = await client.post("/api/v1/photo-smart-collections/from-search", headers=headers, json={
            "name": "Too many filter", "create_request_id": "smart-static-too-many-create",
            "source_token": source.json()["photo_collection_source_token"],
        })
        smart = saved.json()["collection"]
        before = int(api_env.service.catalog.read_one("SELECT COUNT(*) FROM photo_collections")[0])
        response = await client.post(
            f"/api/v1/photo-smart-collections/{smart['smart_id']}/save-static", headers=headers, json={
                "instance_token": smart["instance_token"], "expected_revision": smart["revision"],
                "name": "Should reject", "create_request_id": "smart-static-too-many-save",
            },
        )
        assert response.status_code == 409
        assert response.json()["code"] == "PHOTO_SMART_STATIC_TOO_LARGE"
        assert int(api_env.service.catalog.read_one("SELECT COUNT(*) FROM photo_collections")[0]) == before

    api_env.scenario(scenario)


def test_stale_ranked_page_is_sql_qualified_and_requires_confirmation_to_save(api_env, monkeypatch):
    def no_retrieval(*_args, **_kwargs):
        raise AssertionError("stale candidate verification must not run FTS or vector retrieval")

    monkeypatch.setattr("photo_search.api.routes.search_request", no_retrieval)
    seeded = seed_library(api_env, {"one.jpg": [], "two.jpg": [], "three.jpg": []})
    ids = [int(item["asset_id"]) for item in seeded.values()]
    payload = PlaceAwareSearchRequestModel.model_validate({
        "request_id": "smart-stale-rule", "retrieval_mode": "text",
        "original_query": "needle", "text": "needle", "content_query": "needle",
    })
    rule = {"version": 1, "execution_kind": "TEXT", "request": payload.model_dump(
        exclude={"request_id", "search_session_id", "cursor"}), "display_query": "needle",
        "content_query": "needle", "confirmed_execution_mode": "ranked_candidates",
        "confirmed_scope": None, "confirmed_place_scope": None, "confirmed_person_scope": None,
        "confirmed_filter_revision": None, "confirmed_suggestion_revision": None,
        "confirmed_favorites_revision": None, "search_binding": None}
    with api_env.service.catalog.read_snapshot() as connection:
        signature = photo_smart_collections.dependency_signature(connection, rule)[0]
    smart = photo_smart_collections.create_collection(
        api_env.service.catalog, "Stale ranked", "smart-stale-ranked-create", rule,
        request_hash="stale-rank", auth_hash="test-auth", source_hash="stale-source",
    )["collection"]
    photo_smart_collections.publish_candidates(api_env.service.catalog, smart["smart_id"], ids,
                                                signature=signature, truncated=True)
    with api_env.service.catalog.write() as connection:
        connection.execute(
            "UPDATE photo_smart_cache_state SET dependency_signature='changed-dependency',refresh_status='FAILED' "
            "WHERE smart_id=?", (smart["smart_id"],),
        )

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        excluded = await client.put(
            f"/api/v1/photo-smart-collections/{smart['smart_id']}/exclusions/{ids[0]}", headers=headers,
            json={"instance_token": smart["instance_token"], "expected_revision": 1},
        )
        assert excluded.status_code == 200, excluded.text
        page = await client.get(
            f"/api/v1/photo-smart-collections/{smart['smart_id']}/assets"
            f"?instance_token={smart['instance_token']}&limit=1&order=rank"
        )
        assert page.status_code == 200, page.text
        body = page.json()
        assert body["freshness"] == "STALE"
        assert body["stale_label"] == "上次结果·待更新"
        assert [int(item["asset_id"]) for item in body["assets"]] == [ids[1]]
        assert body["has_more"] is True
        unconfirmed = await client.post(
            f"/api/v1/photo-smart-collections/{smart['smart_id']}/save-static", headers=headers, json={
                "instance_token": smart["instance_token"], "expected_revision": 2,
                "name": "Stale saved", "create_request_id": "smart-stale-static-save",
            },
        )
        assert unconfirmed.status_code == 409
        assert unconfirmed.json()["details"]["required_confirmations"]["confirm_stale"] is True
        assert unconfirmed.json()["details"]["required_confirmations"]["confirm_truncated"] is True
        confirmed = await client.post(
            f"/api/v1/photo-smart-collections/{smart['smart_id']}/save-static", headers=headers, json={
                "instance_token": smart["instance_token"], "expected_revision": 2,
                "name": "Stale saved", "create_request_id": "smart-stale-static-save",
                "confirm_stale": True, "confirm_indexing": True, "confirm_truncated": True,
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        saved = await client.get(
            f"/api/v1/photo-collections/{confirmed.json()['collection']['collection_id']}/assets"
            f"?instance_token={confirmed.json()['collection']['instance_token']}&limit=50"
        )
        assert saved.status_code == 200, saved.text
        assert [int(item["asset_id"]) for item in saved.json()["assets"]] == ids[1:]
        photo_smart_collections.publish_candidates(api_env.service.catalog, smart["smart_id"], ids,
                                                    signature=signature)
        old_cursor = await client.get(
            f"/api/v1/photo-smart-collections/{smart['smart_id']}/assets"
            f"?instance_token={smart['instance_token']}&limit=1&order=rank&cursor={body['next_cursor']}"
        )
        assert old_cursor.status_code == 409

    api_env.scenario(scenario)


@pytest.mark.parametrize("count", [51, 120, 5000])
@pytest.mark.parametrize("order", ["rank", "capture_desc"])
@pytest.mark.parametrize("execution_kind", ["TEXT", "COMBINED"])
def test_ranked_save_static_copies_all_candidates_in_order(api_env, count, order, execution_kind):
    seed_library(api_env, {"ranked-base.jpg": []})
    ids = list(range(10_000, 10_000 + count))
    with api_env.service.catalog.write() as connection:
        source_id = int(connection.execute("SELECT source_id FROM source_roots LIMIT 1").fetchone()[0])
        connection.executemany(
            "INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) VALUES(?,?,8,?, 'ACTIVE')",
            [(asset_id, f"{asset_id:064x}", asset_id) for asset_id in ids],
        )
        connection.executemany(
            "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,availability) "
            "VALUES(?,?,?, ?,8,1,'PRESENT')",
            [(source_id, f"{asset_id}.jpg", f"{asset_id}.jpg", asset_id) for asset_id in ids],
        )
        connection.executemany(
            "INSERT INTO asset_metadata(asset_id,taken_at_sort,time_source,time_confidence) VALUES(?,?,?,?)",
            [(asset_id, 1_700_000_000_000 + (offset // 3), "EXIF_ORIGINAL", "HIGH")
             for offset, asset_id in enumerate(ids) if offset % 3 != 2],
        )
    payload = PlaceAwareSearchRequestModel.model_validate({
        "request_id": f"smart-static-{count}-{order}-{execution_kind}", "retrieval_mode": "text",
        "original_query": "needle", "text": "needle", "content_query": "needle",
    })
    rule = {"version": 1, "execution_kind": execution_kind, "request": payload.model_dump(
        exclude={"request_id", "search_session_id", "cursor"}), "display_query": "needle",
        "content_query": "needle", "confirmed_execution_mode": "ranked_candidates",
        "confirmed_scope": None, "confirmed_place_scope": None, "confirmed_person_scope": None,
        "confirmed_filter_revision": None, "confirmed_suggestion_revision": None,
        "confirmed_favorites_revision": None, "search_binding": None}
    with api_env.service.catalog.read_snapshot() as connection:
        signature = photo_smart_collections.dependency_signature(connection, rule)[0]
    smart = photo_smart_collections.create_collection(
        api_env.service.catalog, f"Ranked {count} {order} {execution_kind}",
        f"smart-static-{count}-{order}-{execution_kind}-create", rule,
        request_hash=f"ranked-{count}-{order}-{execution_kind}", auth_hash="test-auth",
        source_hash=f"ranked-{count}-{order}-{execution_kind}-source",
    )["collection"]
    photo_smart_collections.publish_candidates(api_env.service.catalog, smart["smart_id"], ids,
                                                signature=signature)
    excluded_id = ids[count // 2]
    photo_smart_collections.set_exclusion(api_env.service.catalog, smart["smart_id"], excluded_id,
        instance_token=smart["instance_token"], expected_revision=1, excluded=True)
    expected_revision = 2
    ineligible_id = ids[0]
    with api_env.service.catalog.write() as connection:
        connection.execute(
            "UPDATE file_locations SET relative_path='ineligible.mp4',path_key='ineligible.mp4' "
            "WHERE asset_id=?", (ineligible_id,)
        )
    expected = list(ids)
    if order == "capture_desc":
        known = {asset_id: 1_700_000_000_000 + (offset // 3)
                 for offset, asset_id in enumerate(ids) if offset % 3 != 2}
        expected = sorted(known, key=lambda asset_id: (known[asset_id], asset_id), reverse=True)
        expected.extend(sorted(set(ids) - set(known), reverse=True))
    expected.remove(excluded_id)
    expected.remove(ineligible_id)

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        ui_wide_page = await client.get(
            f"/api/v1/photo-smart-collections/{smart['smart_id']}/assets"
            f"?instance_token={smart['instance_token']}&limit=5000&order={order}"
        )
        assert ui_wide_page.status_code == 422
        response = await client.post(
            f"/api/v1/photo-smart-collections/{smart['smart_id']}/save-static",
            headers=api_env.post_headers(csrf), json={
                "instance_token": smart["instance_token"], "expected_revision": expected_revision,
                "name": f"Ranked {count} saved {order} {execution_kind}",
                "create_request_id": f"smart-static-{count}-{order}-{execution_kind}-save",
                "order": order,
                "confirm_indexing": True, "confirm_stale": True,
            },
        )
        assert response.status_code == 200, response.text
        collection = response.json()["collection"]
        saved = await client.get(
            f"/api/v1/photo-collections/{collection['collection_id']}/assets"
            f"?instance_token={collection['instance_token']}&limit=50"
        )
        assert saved.status_code == 200, saved.text
        actual = [int(item["asset_id"]) for item in saved.json()["assets"]]
        cursor = saved.json()["next_cursor"]
        assert len(actual) <= 50
        while cursor:
            saved = await client.get(
                f"/api/v1/photo-collections/{collection['collection_id']}/assets"
                f"?instance_token={collection['instance_token']}&limit=50&cursor={cursor}"
            )
            assert saved.status_code == 200, saved.text
            assert len(saved.json()["assets"]) <= 50
            actual.extend(int(item["asset_id"]) for item in saved.json()["assets"])
            cursor = saved.json()["next_cursor"]
        assert actual == expected, f"expected {len(expected)} ordered candidates, saved {len(actual)}"
        retry = await client.post(
            f"/api/v1/photo-smart-collections/{smart['smart_id']}/save-static",
            headers=api_env.post_headers(csrf), json={
                "instance_token": smart["instance_token"], "expected_revision": expected_revision,
                "name": f"Ranked {count} saved {order} {execution_kind}",
                "create_request_id": f"smart-static-{count}-{order}-{execution_kind}-save",
                "order": order, "confirm_indexing": True, "confirm_stale": True,
            },
        )
        assert retry.status_code == 200, retry.text
        assert retry.json()["collection"]["collection_id"] == collection["collection_id"]

    api_env.scenario(scenario)


def test_ranked_save_static_rejects_concurrent_smart_revision_change(api_env, monkeypatch):
    seeded = seed_library(api_env, {"ranked-concurrent.jpg": []})
    asset_id = int(next(iter(seeded.values()))["asset_id"])
    payload = PlaceAwareSearchRequestModel.model_validate({
        "request_id": "smart-static-concurrent", "retrieval_mode": "text",
        "original_query": "needle", "text": "needle", "content_query": "needle",
    })
    rule = {"version": 1, "execution_kind": "TEXT", "request": payload.model_dump(
        exclude={"request_id", "search_session_id", "cursor"}), "display_query": "needle",
        "content_query": "needle", "confirmed_execution_mode": "ranked_candidates",
        "confirmed_scope": None, "confirmed_place_scope": None, "confirmed_person_scope": None,
        "confirmed_filter_revision": None, "confirmed_suggestion_revision": None,
        "confirmed_favorites_revision": None, "search_binding": None}
    with api_env.service.catalog.read_snapshot() as connection:
        signature = photo_smart_collections.dependency_signature(connection, rule)[0]
    smart = photo_smart_collections.create_collection(
        api_env.service.catalog, "Concurrent ranked", "smart-static-concurrent-create", rule,
        request_hash="ranked-concurrent", auth_hash="test-auth", source_hash="ranked-concurrent-source",
    )["collection"]
    photo_smart_collections.publish_candidates(api_env.service.catalog, smart["smart_id"], [asset_id],
                                                signature=signature)
    original = photo_collections.create_collection

    def mutate_before_transaction(*args, **kwargs):
        with api_env.service.catalog.write() as connection:
            connection.execute(
                "UPDATE photo_smart_collections SET revision=revision+1 WHERE smart_id=?",
                (smart["smart_id"],),
            )
        return original(*args, **kwargs)

    monkeypatch.setattr("photo_search.api.routes.photo_collections.create_collection", mutate_before_transaction)
    before = int(api_env.service.catalog.read_one("SELECT COUNT(*) FROM photo_collections")[0])

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        response = await client.post(
            f"/api/v1/photo-smart-collections/{smart['smart_id']}/save-static",
            headers=api_env.post_headers(csrf), json={
                "instance_token": smart["instance_token"], "expected_revision": smart["revision"],
                "name": "Concurrent save", "create_request_id": "smart-static-concurrent-save",
                "confirm_indexing": True,
            },
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "PHOTO_SMART_REVISION_CONFLICT"
        assert int(api_env.service.catalog.read_one("SELECT COUNT(*) FROM photo_collections")[0]) == before
        assert int(api_env.service.catalog.read_one(
            "SELECT COUNT(*) FROM photo_collection_members WHERE collection_id IN "
            "(SELECT collection_id FROM photo_collections WHERE name='Concurrent save')"
        )[0]) == 0

    api_env.scenario(scenario)


def test_current_ranked_cache_reopen_does_not_run_search_or_encoder(api_env, monkeypatch):
    def no_retrieval(*_args, **_kwargs):
        raise AssertionError("opening a current smart cache must not rerun search or encoding")

    monkeypatch.setattr("photo_search.api.routes.search_request", no_retrieval)
    seeded = seed_library(api_env, {"cached-one.jpg": [], "cached-two.jpg": []})
    ids = [int(item["asset_id"]) for item in seeded.values()]
    payload = PlaceAwareSearchRequestModel.model_validate({
        "request_id": "smart-current-cache", "retrieval_mode": "text",
        "original_query": "cached", "text": "cached", "content_query": "cached",
    })
    rule = {"version": 1, "execution_kind": "TEXT", "request": payload.model_dump(
        exclude={"request_id", "search_session_id", "cursor"}), "display_query": "cached",
        "content_query": "cached", "confirmed_execution_mode": "ranked_candidates",
        "confirmed_scope": None, "confirmed_place_scope": None, "confirmed_person_scope": None,
        "confirmed_filter_revision": None, "confirmed_suggestion_revision": None,
        "confirmed_favorites_revision": None, "search_binding": None}
    with api_env.service.catalog.read_snapshot() as connection:
        signature = photo_smart_collections.dependency_signature(connection, rule)[0]
    smart = photo_smart_collections.create_collection(
        api_env.service.catalog, "Current ranked", "smart-current-cache-create", rule,
        request_hash="current-cache", auth_hash="test-auth", source_hash="current-source",
    )["collection"]
    photo_smart_collections.publish_candidates(
        api_env.service.catalog, smart["smart_id"], ids, signature=signature,
    )

    async def scenario(client):
        await api_env.authenticate(client)
        response = await client.get(
            f"/api/v1/photo-smart-collections/{smart['smart_id']}/assets"
            f"?instance_token={smart['instance_token']}&limit=50",
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["freshness"] == "CURRENT"
        assert [int(item["asset_id"]) for item in body["assets"]] == ids

    api_env.scenario(scenario)


def test_ordinary_members_smart_rule_exclusions_and_favorites_are_isolated(api_env):
    from photo_search.favorites import set_favorite
    from photo_search.photo_collections import create_collection as create_photo_collection

    seeded = seed_library(api_env, {"ordinary.jpg": [], "favorite.jpg": []})
    ordinary_id = int(seeded["ordinary.jpg"]["asset_id"])
    favorite_id = int(seeded["favorite.jpg"]["asset_id"])
    ordinary = create_photo_collection(
        api_env.service.catalog, "Isolation ordinary", "isolation-ordinary",
        asset_ids=[ordinary_id],
    )["collection"]
    set_favorite(api_env.service.catalog, favorite_id, favorite=True)
    request = PlaceAwareSearchRequestModel.model_validate({
        "request_id": "smart-isolation", "retrieval_mode": "text",
        "original_query": "synthetic", "text": "synthetic", "content_query": "synthetic",
    })
    rule = {"version": 1, "execution_kind": "TEXT", "request": request.model_dump(
        exclude={"request_id", "search_session_id", "cursor"}), "display_query": "synthetic",
        "content_query": "synthetic", "confirmed_execution_mode": "ranked_candidates",
        "confirmed_scope": None, "confirmed_place_scope": None, "confirmed_person_scope": None,
        "confirmed_filter_revision": None, "confirmed_suggestion_revision": None,
        "confirmed_favorites_revision": None, "search_binding": None}
    with api_env.service.catalog.read_snapshot() as connection:
        signature = photo_smart_collections.dependency_signature(connection, rule)[0]
    smart = photo_smart_collections.create_collection(
        api_env.service.catalog, "Isolation smart", "isolation-smart", rule,
        request_hash="isolation-smart", auth_hash="test-auth", source_hash="isolation-source",
    )["collection"]
    rule_before = api_env.service.catalog.read_one(
        "SELECT rule_json FROM photo_smart_collections WHERE smart_id=?", (smart["smart_id"],),
    )[0]
    photo_smart_collections.publish_candidates(
        api_env.service.catalog, smart["smart_id"], [ordinary_id, favorite_id], signature=signature,
    )
    photo_smart_collections.set_exclusion(
        api_env.service.catalog, smart["smart_id"], ordinary_id,
        instance_token=smart["instance_token"], expected_revision=1, excluded=True,
    )

    assert [int(row["asset_id"]) for row in api_env.service.catalog.read_all(
        "SELECT asset_id FROM photo_collection_members WHERE collection_id=? ORDER BY position",
        (ordinary["collection_id"],),
    )] == [ordinary_id]
    assert [int(row["asset_id"]) for row in api_env.service.catalog.read_all(
        "SELECT asset_id FROM asset_favorites ORDER BY asset_id",
    )] == [favorite_id]
    assert [int(row["asset_id"]) for row in api_env.service.catalog.read_all(
        "SELECT asset_id FROM photo_smart_exclusions WHERE smart_id=?", (smart["smart_id"],),
    )] == [ordinary_id]
    assert api_env.service.catalog.read_one(
        "SELECT rule_json FROM photo_smart_collections WHERE smart_id=?", (smart["smart_id"],),
    )[0] == rule_before
    assert [int(row["asset_id"]) for row in api_env.service.catalog.read_all(
        "SELECT asset_id FROM photo_smart_cache_entries WHERE smart_id=? AND build_id=("
        "SELECT build_id FROM photo_smart_cache_state WHERE smart_id=?) ORDER BY rank",
        (smart["smart_id"], smart["smart_id"]),
    )] == [ordinary_id, favorite_id]
