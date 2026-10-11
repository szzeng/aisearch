"""Small synthetic end-to-end coverage of the read-only personal place albums."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from typing import Any

from httpx import AsyncClient
from p2_dataset_fixtures import build_package, mini_spec, write_gps_jpeg

from photo_search.geo import extract as geo_extract
from photo_search.geo import resolver
from photo_search.places import albums, datasets, revision


def install_fixture(api_env: Any) -> int:
    spec = mini_spec()
    spec["index"] = {
        "schema_version": 1,
        "provider": spec["source"]["provider"],
        "source_snapshot": spec["source"]["snapshot"],
        "city_to_admin1": {
            "city-hangzhou": {"id": "CN.33", "name": "浙江", "aliases": ["Zhejiang"]},
            "city-shanghai": {"id": "CN.31", "name": "上海", "aliases": ["Shanghai"]},
        },
    }
    source = api_env.tmp_path / "album-package"
    build_package(source, spec=spec)
    staged = datasets.stage_package(api_env.service.catalog, source)
    validated = datasets.validate_dataset(api_env.service.catalog, staged["dataset_id"])
    datasets.activate_dataset(api_env.service.catalog, validated["dataset_id"])
    return int(
        api_env.service.catalog.read_one(
            "SELECT place_id FROM place_external_refs WHERE provider = ? AND external_id = ?",
            (spec["source"]["provider"], "city-hangzhou"),
        )["place_id"]
    )


def seed_current_geo_and_resolution(api_env: Any, asset_id: int, *, first_distance: float | None = None, tie: bool = False) -> None:
    catalog = api_env.service.catalog
    asset = catalog.read_one("SELECT sha256 FROM assets WHERE asset_id = ?", (asset_id,))
    location = catalog.read_one(
        "SELECT location_id FROM file_locations WHERE asset_id = ? AND availability = 'PRESENT' ORDER BY location_id LIMIT 1",
        (asset_id,),
    )
    geo_id = f"album-geo-{asset_id}"
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO asset_geo_facts(asset_id, geo_fact_id, location_id, input_revision, fact_revision, status, source, processor_id, raw_fields_checksum_sha256, raw_gps_json, latitude, longitude, datum_status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 1, 'VALID', 'EXIF_GPS', ?, ?, ?, 30.2741, 120.1551, 'WGS84', 1, 1)",
            (asset_id, geo_id, int(location["location_id"]), str(asset["sha256"]), geo_extract.GPS_PROCESSOR_ID, hashlib.sha256(b"gps").hexdigest(), json.dumps({"latitude": 30.2741, "longitude": 120.1551})),
        )
        active = connection.execute("SELECT dataset_id FROM active_geo_dataset WHERE singleton = 1").fetchone()
        dataset_id = str(active["dataset_id"])
        content = resolver.load_active_content(connection, data_root=api_env.data_root)
        payload = resolver.evaluate(catalog, asset_id=asset_id, input_revision=str(asset["sha256"]), data_root=api_env.data_root)
        if first_distance is not None:
            payload["candidates"][0]["distance_m"] = float(first_distance)
        if tie and len(payload["candidates"]) > 1:
            payload["candidates"][1]["distance_m"] = float(payload["candidates"][0]["distance_m"])
        resolution_id = resolver.resolution_id_for(asset_id=asset_id, geo_fact_id=geo_id, processor_id=payload["processor_id"], dataset_id=dataset_id, config_hash=payload["config_hash"])
        connection.execute(
            "INSERT INTO place_resolutions(resolution_id, asset_id, geo_fact_id, input_revision, control_revision_seen, processor_id, dataset_id, dataset_version, config_hash, status, suggestion_revision, created_at) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, 1)",
            (resolution_id, asset_id, geo_id, str(asset["sha256"]), payload["processor_id"], dataset_id, content.package_version, payload["config_hash"], payload["status"], revision.read_suggestion_revision(connection)),
        )
        for candidate in payload["candidates"]:
            local_id = connection.execute("SELECT place_id FROM place_external_refs WHERE provider = ? AND external_id = ?", (content.provider, candidate["dataset_place_id"])).fetchone()
            connection.execute(
                "INSERT INTO place_resolution_candidates(resolution_id, rank, candidate_id, dataset_place_id, local_place_id, kind, display_name, ancestor_names_json, distance_m, match_basis, confidence_basis, disposition, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (resolution_id, candidate["rank"], candidate["candidate_id"], candidate["dataset_place_id"], None if local_id is None else int(local_id["place_id"]), candidate["kind"], candidate["display_name"], json.dumps(candidate["ancestor_names"], ensure_ascii=False), candidate["distance_m"], candidate["match_basis"], candidate["confidence_basis"], candidate["disposition"]),
            )


def test_place_album_flow_groups_deduplicates_pages_and_respects_manual_control(api_env: Any) -> None:
    city_id = install_fixture(api_env)
    for index in range(24):
        write_gps_jpeg(api_env.library / f"album-{index:02}.jpg", position=(30.2741 + index * 0.000001, 120.1551), color=(index + 1, 80, 140))
    shutil.copyfile(api_env.library / "album-00.jpg", api_env.library / "album-00-copy.jpg")
    source = api_env.register_source("album-library")
    api_env.scan(int(source["source_id"]))
    asset_ids = [int(row["asset_id"]) for row in api_env.service.catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id")]
    for index, asset_id in enumerate(asset_ids):
        seed_current_geo_and_resolution(
            api_env,
            asset_id,
            first_distance={21: 12_000.0, 22: 50_000.0, 23: 50_000.001}.get(index),
            tie=index == 21,
        )
    catalog = api_env.service.catalog
    with catalog.write() as connection:
        connection.execute("UPDATE asset_geo_facts SET status = 'ABSENT', raw_gps_json = NULL, latitude = NULL, longitude = NULL, datum_status = 'ABSENT' WHERE asset_id = ?", (asset_ids[19],))
        connection.execute("UPDATE asset_geo_facts SET status = 'INVALID', latitude = NULL, longitude = NULL, datum_status = 'UNSUPPORTED' WHERE asset_id = ?", (asset_ids[20],))

    async def scenario(client: AsyncClient) -> None:
        csrf = await api_env.authenticate(client)
        catalog = api_env.service.catalog
        albums._INDEX_CACHE.clear()
        sql_trace: list[str] = []
        catalog.connection.set_trace_callback(sql_trace.append)
        started = time.perf_counter()
        direct = albums.albums(catalog, api_env.data_root, kind="city")
        cold_ms = (time.perf_counter() - started) * 1000
        cold_sql_count = len(sql_trace)
        cold_trace = list(sql_trace)
        sql_trace.clear()
        started = time.perf_counter()
        albums.albums(catalog, api_env.data_root, kind="city")
        warm_ms = (time.perf_counter() - started) * 1000
        warm_sql_count = len(sql_trace)
        sql_trace.clear()
        started = time.perf_counter()
        datasets.dataset_status(catalog.connection, data_root=api_env.data_root)
        datasets_ms = (time.perf_counter() - started) * 1000
        datasets_sql_count = len(sql_trace)
        sql_trace.clear()
        started = time.perf_counter()
        first_page = albums.album_assets(
            catalog, api_env.data_root, kind="city", key=direct["albums"][0]["key"], limit=7
        )
        page_ms = (time.perf_counter() - started) * 1000
        page_sql_count = len(sql_trace)
        catalog.connection.set_trace_callback(None)
        label_reads = [sql for sql in cold_trace if "SELECT place_id, canonical_name FROM places WHERE place_id IN" in sql]
        alias_reads = [sql for sql in cold_trace if "SELECT place_id, text FROM place_aliases WHERE place_id IN" in sql]
        assert len(label_reads) <= 1 and len(alias_reads) <= 1
        print(
            f"synthetic city albums: cold={cold_ms:.2f}ms/{cold_sql_count} SQL, "
            f"warm={warm_ms:.2f}ms/{warm_sql_count} SQL; datasets={datasets_ms:.2f}ms/{datasets_sql_count} SQL; "
            f"page={page_ms:.2f}ms/{page_sql_count} SQL; batched labels={len(label_reads)}, aliases={len(alias_reads)}"
        )
        assert len(first_page["assets"]) == 7 and first_page["next_cursor"]
        assert sum(sql.lstrip().upper().startswith("SELECT A.ASSET_ID, A.SHA256") for sql in sql_trace) == 1
        unfiltered = (await client.get("/api/v1/places/albums", params={"kind": "city"})).json()
        assert direct["albums"] == unfiltered["albums"]
        assert unfiltered["albums"] and unfiltered["albums"][0]["total"] == 20
        city = (await client.get("/api/v1/places/albums", params={"kind": "city", "name": "Hangzhou"})).json()
        assert len(city["albums"]) == 1
        assert city["albums"][0]["total"] == 20
        city_members = (await client.get(f"/api/v1/places/albums/city/{city_id}/assets", params={"limit": 100})).json()
        place_search = await client.post(
            "/api/v1/search/place-aware",
            json={"request_id": "gps-place-natural-search", "original_query": "在杭州拍的照片", "page_size": 48},
            headers=api_env.post_headers(csrf),
        )
        assert place_search.status_code == 200, place_search.text
        place_search_body = place_search.json()
        assert place_search_body["place_scope"]["origin"] == "GPS_NEARBY_CITY"
        assert place_search_body["total_candidates"] == city["albums"][0]["total"]
        assert [int(hit["asset_id"]) for hit in place_search_body["hits"]] == [
            int(item["asset_id"]) for item in city_members["assets"]
        ]
        first = (await client.get(f"/api/v1/places/albums/city/{city_id}/assets", params={"limit": 7})).json()
        second = (await client.get(f"/api/v1/places/albums/city/{city_id}/assets", params={"limit": 7, "cursor": first["next_cursor"]})).json()
        third = (await client.get(f"/api/v1/places/albums/city/{city_id}/assets", params={"limit": 7, "cursor": second["next_cursor"]})).json()
        assert len(first["assets"] + second["assets"] + third["assets"]) == 20
        assert len({item["asset_id"] for item in first["assets"] + second["assets"] + third["assets"]}) == 20
        province = (await client.get("/api/v1/places/albums", params={"kind": "province", "name": "Zhejiang"})).json()
        assert province["albums"][0]["total"] == 20
        country = (await client.get("/api/v1/places/albums", params={"kind": "country", "name": "中国"})).json()
        assert country["albums"][0]["total"] == 20

        asset_id = asset_ids[0]
        assigned = await client.put(
            f"/api/v1/assets/{asset_id}/place-assignment",
            json={"action": "assign", "target_place_id": city_id, "expected_revision": 0, "request_id": "album-manual-assign"},
            headers=api_env.post_headers(csrf),
        )
        assert assigned.status_code == 200, assigned.text
        state = (await client.get(f"/api/v1/assets/{asset_id}/place")).json()
        unassigned = await client.put(
            f"/api/v1/assets/{asset_id}/place-assignment",
            json={"action": "unassign", "expected_revision": state["assignment"]["revision"], "request_id": "album-manual-unassign"},
            headers=api_env.post_headers(csrf),
        )
        assert unassigned.status_code == 200, unassigned.text
        city_after = (await client.get("/api/v1/places/albums", params={"kind": "city", "name": "杭州"})).json()
        assert city_after["albums"][0]["total"] == 19
        state = (await client.get(f"/api/v1/assets/{asset_id}/place")).json()
        restored = await client.put(
            f"/api/v1/assets/{asset_id}/place-assignment",
            json={"action": "restore_auto", "expected_revision": state["assignment"]["revision"], "request_id": "album-restore-auto"},
            headers=api_env.post_headers(csrf),
        )
        assert restored.status_code == 200, restored.text
        city_restored = (await client.get("/api/v1/places/albums", params={"kind": "city", "name": "杭州"})).json()
        assert city_restored["albums"][0]["total"] == 19

    api_env.scenario(scenario)


def test_non_cn_album_keeps_48_plus_1_offset_cursor_and_rejects_stale_revision(api_env: Any) -> None:
    city_id = install_fixture(api_env)
    for index in range(50):
        api_env.add_photo(f"page-{index:02}.jpg", noise=index + 1)
    source = api_env.register_source("album-page-library")
    api_env.scan(int(source["source_id"]))
    asset_ids = [
        int(row["asset_id"])
        for row in api_env.service.catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id")
    ]

    async def scenario(client: AsyncClient) -> None:
        csrf = await api_env.authenticate(client)
        for asset_id in asset_ids[:49]:
            response = await client.put(
                f"/api/v1/assets/{asset_id}/place-assignment",
                json={
                    "action": "assign",
                    "target_place_id": city_id,
                    "expected_revision": 0,
                    "request_id": f"album-page-{asset_id}",
                },
                headers=api_env.post_headers(csrf),
            )
            assert response.status_code == 200, response.text
        first = await client.get(
            f"/api/v1/places/albums/city/{city_id}/assets", params={"limit": 48}
        )
        assert first.status_code == 200, first.text
        page_one = first.json()
        assert page_one["total"] == 49 and len(page_one["assets"]) == 48
        second = await client.get(
            f"/api/v1/places/albums/city/{city_id}/assets",
            params={"limit": 48, "cursor": page_one["next_cursor"]},
        )
        assert second.status_code == 200, second.text
        page_two = second.json()
        all_ids = [item["asset_id"] for item in page_one["assets"] + page_two["assets"]]
        assert len(all_ids) == 49 and len(set(all_ids)) == 49
        added = await client.put(
            f"/api/v1/assets/{asset_ids[49]}/place-assignment",
            json={
                "action": "assign",
                "target_place_id": city_id,
                "expected_revision": 0,
                "request_id": "album-page-revision-change",
            },
            headers=api_env.post_headers(csrf),
        )
        assert added.status_code == 200, added.text
        stale = await client.get(
            f"/api/v1/places/albums/city/{city_id}/assets",
            params={"limit": 48, "cursor": page_one["next_cursor"]},
        )
        assert stale.status_code == 409

    api_env.scenario(scenario)


def test_place_backfill_reuses_gps_and_resolver_jobs_without_duplicate_results(api_env: Any) -> None:
    install_fixture(api_env)
    write_gps_jpeg(api_env.library / "needs-place.jpg")
    source = api_env.register_source("backfill-library")
    api_env.scan(int(source["source_id"]))

    async def scenario(client: AsyncClient) -> None:
        csrf = await api_env.authenticate(client)
        first = await client.post("/api/v1/places/albums/backfill", headers=api_env.post_headers(csrf))
        assert first.status_code == 200, first.text
        result = first.json()
        assert result["gps"]["targets"] == 1
        assert result["place_resolution"]["targets"] == 1
        assert result["complete"] is True
        second = await client.post("/api/v1/places/albums/backfill", headers=api_env.post_headers(csrf))
        assert second.status_code == 200, second.text
        assert second.json()["gps"]["targets"] == 0
        assert second.json()["place_resolution"]["targets"] == 0
        album = (await client.get("/api/v1/places/albums", params={"kind": "city"})).json()
        assert sum(item["total"] for item in album["albums"]) == 1

    api_env.scenario(scenario)


def test_place_album_sort_prefers_count_then_latest_catalog_addition() -> None:
    class CatalogStub:
        def read_all(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
            return []

    rows = []
    for place_id, name, count, created_at in (
        (1, "Older", 3, 10),
        (2, "Smaller", 4, 100),
        (3, "Newer", 3, 20),
    ):
        for offset in range(count):
            rows.append(
                {
                    "place_id": place_id,
                    "asset_id": place_id * 10 + offset,
                    "kind": "city",
                    "name": name,
                    "parent_place_id": None,
                    "origin": "MANUAL",
                    "created_at": created_at + offset,
                }
            )
    values = list(albums._group_rows(CatalogStub(), "city", rows).values())
    for value in values:
        value["total"] = len(value.pop("asset_ids"))
        value.pop("asset_id_set")
    albums._sort_albums(values)
    assert [value["name"] for value in values] == ["Smaller", "Newer", "Older"]
    assert all("_latest_added_at" not in value for value in values)
