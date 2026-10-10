"""Synthetic source choice and legacy-assignment bridge coverage."""

from __future__ import annotations

from typing import Any

from p2_search_api_fixtures import build_search_env
from p2_search_fixtures import write_jpeg
from p4_cn_fixtures import geo, install, resolve_batch

from photo_search.places import albums, cn_facets, directory
from photo_search.places import choices as place_choices


def _source_rows(env: Any, name: str) -> list[dict[str, Any]]:
    return place_choices.choices(env.catalog, text=name, data_root=env.data_root)


def test_unified_choices_prioritize_verified_osm_fold_linked_city_and_fallback_to_geonames(
    tmp_path: Any,
) -> None:
    env = build_search_env(tmp_path)
    try:
        install(env.catalog, tmp_path / "places", legacy_jinan=True)
        jinan = _source_rows(env, "济南")
        assert len(jinan) == 1
        assert jinan[0]["source"] == "OSM"
        assert jinan[0]["kind"] == "city"
        assert jinan[0]["source_id"] == "relation/2"
        assert jinan[0]["place_id"] is None
        assert "济南旧称" in jinan[0]["aliases"]

        tokyo = _source_rows(env, "东京")
        assert len(tokyo) == 1
        assert tokyo[0]["source"] == "GEONAMES"
        assert tokyo[0]["kind"] == "city"
        assert tokyo[0]["parent"] == "日本"
        assert tokyo[0]["selection_ref"].startswith("geonames|")
        assert "_parent_key" not in tokyo[0]
        tokyo_with_parent_identity = place_choices.choices(
            env.catalog, text="东京", data_root=env.data_root, include_parent_identity=True,
        )
        parent = env.catalog.read_one(
            "SELECT kind,canonical_name FROM places WHERE place_id=?",
            (int(tokyo_with_parent_identity[0]["_parent_key"]),),
        )
        assert (parent["kind"], parent["canonical_name"]) == ("country", "日本")
        assert _source_rows(env, "不存在的地区") == []
    finally:
        env.context.close()
        env.library.close()


def test_manual_osm_district_assignment_uses_existing_lock_membership_undo_and_rejects_bypass(
    tmp_path: Any,
) -> None:
    env = build_search_env(tmp_path)
    try:
        dataset_id, _ = install(env.catalog, tmp_path / "places", legacy_jinan=True)
        write_jpeg(env.library.root / "no-gps.jpg", index=91)
        write_jpeg(env.library.root / "gps-jinan.jpg", index=92)
        env.library.service.run_scan(env.library.source_id)
        asset_id = int(
            env.catalog.read_one(
                "SELECT asset_id FROM file_locations WHERE relative_path='no-gps.jpg'"
            )["asset_id"]
        )
        gps_asset_id = int(
            env.catalog.read_one(
                "SELECT asset_id FROM file_locations WHERE relative_path='gps-jinan.jpg'"
            )["asset_id"]
        )
        geo(env.catalog, gps_asset_id, lat=36.65, lon=117.0)
        resolve_batch(env.catalog, env.data_root)
        district = _source_rows(env, "历下区")
        assert len(district) == 1
        assert district[0]["source"] == "OSM" and district[0]["kind"] == "district"
        assert district[0]["parent"] == "山东省 · 济南市"
        assert district[0]["dataset_id"] == dataset_id
        tokyo = _source_rows(env, "东京")
        assert len(tokyo) == 1

        async def scenario(client):
            csrf = await env.authenticate(client)
            headers = env.post_headers(csrf)
            choice_response = await client.get(
                "/api/v1/places/choices", params={"name": "历下区"}
            )
            assert choice_response.status_code == 200
            assert choice_response.json()["choices"][0]["selection_ref"] == district[0]["selection_ref"]
            response = await client.put(
                f"/api/v1/assets/{asset_id}/place-assignment",
                json={
                    "action": "assign",
                    "target_place_ref": district[0]["selection_ref"],
                    "expected_revision": 0,
                    "request_id": "unified-osm-district",
                },
                headers=headers,
            )
            assert response.status_code == 200, response.text
            operation = response.json()
            proxy = env.catalog.read_one(
                "SELECT place_id FROM cn_admin_place_refs m JOIN cn_admin_ids i USING(admin_id) "
                "WHERE i.external_id='relation/3'"
            )
            assert proxy is not None
            proxy_id = int(proxy["place_id"])
            for kind, admin_id in (("district", 3), ("city", 2), ("province", 1)):
                sql, params = cn_facets.members_sql(kind, key=f"cn:{admin_id}")
                ids = [int(row["asset_id"]) for row in env.catalog.read_all(
                    f"SELECT asset_id FROM ({sql}) ORDER BY asset_id", params
                )]
                assert ids == sorted([asset_id, gps_asset_id])

            selected_search = await client.post(
                "/api/v1/search/place-aware",
                json={"request_id": "unified-osm-search", "original_query": "历下区照片"},
                headers=headers,
            )
            assert selected_search.status_code == 200, selected_search.text
            assert selected_search.json()["place_scope"]["source"] == "OSM"
            assert selected_search.json()["total_candidates"] == 2

            switch = await client.put(
                f"/api/v1/assets/{gps_asset_id}/place-assignment",
                json={
                    "action": "assign",
                    "target_place_ref": tokyo[0]["selection_ref"],
                    "expected_revision": 0,
                    "request_id": "unified-manual-switch-to-tokyo",
                },
                headers=headers,
            )
            assert switch.status_code == 200, switch.text
            district_sql, district_params = cn_facets.members_sql("district", key="cn:3")
            district_ids = [int(row["asset_id"]) for row in env.catalog.read_all(
                f"SELECT asset_id FROM ({district_sql}) ORDER BY asset_id", district_params
            )]
            assert district_ids == [asset_id]
            tokyo_ids, _ = albums.album_member_ids(
                env.catalog, env.data_root, kind="city", key=tokyo[0]["key"]
            )
            assert tokyo_ids == [gps_asset_id]

            batch_response = await client.post(
                "/api/v1/place-assignments/batch",
                json={
                    "request_id": "unified-batch-district",
                    "items": [
                        {
                            "asset_id": gps_asset_id,
                            "action": "assign",
                            "target_place_ref": district[0]["selection_ref"],
                            "expected_revision": 1,
                        }
                    ],
                },
                headers=headers,
            )
            assert batch_response.status_code == 200, batch_response.text
            batch_operation = batch_response.json()
            district_sql, district_params = cn_facets.members_sql("district", key="cn:3")
            district_ids = [int(row["asset_id"]) for row in env.catalog.read_all(
                f"SELECT asset_id FROM ({district_sql}) ORDER BY asset_id", district_params
            )]
            assert district_ids == sorted([asset_id, gps_asset_id])
            batch_undo = await client.post(
                f"/api/v1/place-operations/{batch_operation['operation_id']}/undo",
                json={"request_id": "unified-batch-undo", "expected_operation_revision": batch_operation["operation_revision"]},
                headers=headers,
            )
            assert batch_undo.status_code == 200, batch_undo.text

            # A legacy place_id request cannot bypass source provenance.
            with env.catalog.write() as connection:
                user_place_id = directory.create_place(
                    connection, kind="custom", canonical_name="Forged custom place", now_ms=1
                )
            blocked_single = await client.put(
                f"/api/v1/assets/{asset_id}/place-assignment",
                json={
                    "action": "assign",
                    "target_place_id": user_place_id,
                    "expected_revision": 1,
                    "request_id": "unified-forged-single",
                },
                headers=headers,
            )
            blocked_batch = await client.post(
                "/api/v1/place-assignments/batch",
                json={
                    "request_id": "unified-forged-batch",
                    "items": [
                        {
                            "asset_id": asset_id,
                            "action": "assign",
                            "target_place_id": user_place_id,
                            "expected_revision": 1,
                        }
                    ],
                },
                headers=headers,
            )
            assert blocked_single.status_code == 404
            assert blocked_batch.status_code == 404
            undone = await client.post(
                f"/api/v1/place-operations/{operation['operation_id']}/undo",
                json={"request_id": "unified-osm-undo", "expected_operation_revision": operation["operation_revision"]},
                headers=headers,
            )
            assert undone.status_code == 200, undone.text
            return operation, undone.json(), proxy_id

        operation, undone, proxy_id = env.scenario(scenario)
        assert operation["changed"] is True
        assignment = env.catalog.read_one(
            "SELECT place_id,mode,revision FROM asset_place_assignments WHERE asset_id=?",
            (asset_id,),
        )
        assert assignment["mode"] == "AUTO_ALLOWED" and assignment["place_id"] is None
        assert undone["changed"] is True

        # The operation used the frozen Place row only as an FK carrier; OSM region IDs
        # determine province, city, and district membership during the assignment.
        for kind, admin_id in (("district", 3), ("city", 2), ("province", 1)):
            sql, params = cn_facets.members_sql(kind, key=f"cn:{admin_id}")
            ids = [int(row["asset_id"]) for row in env.catalog.read_all(
                f"SELECT asset_id FROM ({sql}) ORDER BY asset_id", params
            )]
            assert ids == []
        proxy_count = env.catalog.read_one("SELECT COUNT(*) AS n FROM cn_admin_place_refs")["n"]
        assert int(proxy_count) == 1
        assert albums.albums(env.catalog, env.data_root, kind="district")["albums"] == []
        visible = directory.list_places(env.catalog.connection, limit=200)
        assert all(int(item["place_id"]) != proxy_id for item in visible)
    finally:
        env.context.close()
        env.library.close()


def test_duplicate_osm_names_keep_full_parent_disambiguation_and_paris_style_is_not_a_place(
    tmp_path: Any,
) -> None:
    env = build_search_env(tmp_path)
    try:
        install(env.catalog, tmp_path / "places", duplicate=True)
        candidates = _source_rows(env, "西湖区")
        assert len(candidates) == 2
        assert {item["parent"] for item in candidates} == {"浙江省 · 杭州市", "上海市"}

        from photo_search.places.natural_search import parse_place_query

        plan = parse_place_query("西湖区的夜景", _source_rows(env, "西湖区的夜景"))
        assert plan["state"] == "ambiguous"
        assert {item["parent"] for item in plan["choices"]} == {"浙江省 · 杭州市", "上海市"}
        ordinary = parse_place_query("巴黎风格", _source_rows(env, "巴黎风格"))
        assert ordinary["state"] is None
        missing = parse_place_query("火星区的照片", _source_rows(env, "火星区的照片"))
        assert missing["state"] == "unresolved"
        assert missing["choices"] == []
        assert "没有可确认的地点" in missing["notice"]
    finally:
        env.context.close()
        env.library.close()
