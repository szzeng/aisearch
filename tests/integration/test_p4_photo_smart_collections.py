from __future__ import annotations

from p3_persons_page_fixtures import seed_library


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
