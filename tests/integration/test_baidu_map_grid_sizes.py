from __future__ import annotations

import pytest


@pytest.mark.parametrize("size", [500, 1000, 5000, 5001])
def test_forced_grid_keeps_members_and_default_contract(api_env, size):
    catalog = api_env.service.catalog
    with catalog.write() as conn:
        source = conn.execute(
            "INSERT INTO source_roots(display_name,root_path,status) VALUES('synthetic','synthetic','ONLINE')"
        ).lastrowid
        for index in range(size):
            sha = f"{index + 1:064x}"
            asset_id = conn.execute(
                "INSERT INTO assets(sha256,byte_size,created_at,state) VALUES(?,1,1,'ACTIVE')", (sha,)
            ).lastrowid
            conn.execute(
                "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,availability) "
                "VALUES(?,?,?,?,1,1,'PRESENT')", (source, f"{index}.jpg", f"{index}.jpg", asset_id),
            )
            conn.execute(
                "INSERT INTO asset_geo_facts(asset_id,geo_fact_id,input_revision,fact_revision,status,source,"
                "processor_id,raw_fields_checksum_sha256,raw_gps_json,latitude,longitude,datum_status,created_at,updated_at) "
                "VALUES(?,?,?,1,'VALID','EXIF_GPS','geo-exif-gps@1',?,'{}',?,?,'WGS84',1,1)",
                (asset_id, f"synthetic-{asset_id}", sha, "a" * 64, 22 + (index % 100) * 0.001,
                 113 + (index // 100) * 0.001),
            )

    async def scenario(client):
        await api_env.authenticate(client)
        bounds = {"west": 112, "south": 21, "east": 115, "north": 24, "zoom": 10}
        standard = (await client.get("/api/v1/map/points", params=bounds)).json()
        forced = (await client.get("/api/v1/map/points", params={**bounds, "grid": "true"})).json()
        assert standard["mode"] == ("photos" if size <= 5000 else "grid")
        assert forced["mode"] == "grid" and forced["total"] == size
        assert len(forced["features"]) <= 1000
        assert sum(f["properties"]["asset_count"] for f in forced["features"]) == size
        assert forced["member_version"] == standard["member_version"]
        cell = max(forced["features"], key=lambda f: f["properties"]["asset_count"])["properties"]
        cell_params = {**bounds, "cell_id": cell["cell_id"], "grid_zoom": cell["grid_zoom"], "limit": 48}
        member_ids = []
        while True:
            result = await client.get("/api/v1/map/assets", params=cell_params)
            assert result.status_code == 200
            members = result.json()
            assert members["total"] == cell["asset_count"]
            member_ids.extend(a["asset_id"] for a in members["assets"])
            if not members["next_cursor"]:
                break
            cell_params["cursor"] = members["next_cursor"]
        assert len(member_ids) == len(set(member_ids)) == cell["asset_count"]
        if size == 5001:
            assert len(member_ids) > 48
        page = (await client.get("/api/v1/map/assets", params={**bounds, "limit": 48})).json()
        assert len(page["assets"]) == 48 and page["next_cursor"]
        more = (await client.get("/api/v1/map/assets", params={**bounds, "limit": 48, "cursor": page["next_cursor"]})).json()
        assert len(more["assets"]) == 48
        assert {a["asset_id"] for a in page["assets"]}.isdisjoint(a["asset_id"] for a in more["assets"])

    api_env.scenario(scenario)


def test_world_grid_is_stable_on_pan_and_uses_photo_weighted_centers(api_env):
    catalog = api_env.service.catalog
    coords = [(113.001, 22.001), (113.001, 22.001), (113.020, 22.010), (114.0, 23.0)]
    with catalog.write() as conn:
        source = conn.execute(
            "INSERT INTO source_roots(display_name,root_path,status) VALUES('synthetic','synthetic','ONLINE')"
        ).lastrowid
        for index, (lng, lat) in enumerate(coords):
            sha = f"{index + 1:064x}"
            asset_id = conn.execute(
                "INSERT INTO assets(sha256,byte_size,created_at,state) VALUES(?,1,1,'ACTIVE')", (sha,)
            ).lastrowid
            conn.execute(
                "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,availability) "
                "VALUES(?,?,?,?,1,1,'PRESENT')", (source, f"{index}.jpg", f"{index}.jpg", asset_id),
            )
            conn.execute(
                "INSERT INTO asset_geo_facts(asset_id,geo_fact_id,input_revision,fact_revision,status,source,"
                "processor_id,raw_fields_checksum_sha256,raw_gps_json,latitude,longitude,datum_status,created_at,updated_at) "
                "VALUES(?,?,?,1,'VALID','EXIF_GPS','geo-exif-gps@1',?,'{}',?,?,'WGS84',1,1)",
                (asset_id, f"synthetic-{asset_id}", sha, "a" * 64, lat, lng),
            )

    async def scenario(client):
        await api_env.authenticate(client)
        bounds = {"west": 112, "south": 21, "east": 115, "north": 24, "zoom": 10, "grid": "true"}
        first = (await client.get("/api/v1/map/points", params=bounds)).json()
        shifted = (await client.get("/api/v1/map/points", params={**bounds, "west": 112.1, "east": 115.1})).json()
        assert first["features"] == shifted["features"]
        cluster = next(f for f in first["features"] if f["properties"]["asset_count"] == 3)
        assert cluster["geometry"]["coordinates"] == pytest.approx([(113.001 * 2 + 113.020) / 3, (22.001 * 2 + 22.010) / 3])
        single = next(f for f in first["features"] if f["properties"]["asset_count"] == 1)
        assert single["geometry"]["coordinates"] == pytest.approx([114.0, 23.0])
        props = cluster["properties"]
        assert props["cell_id"].startswith("w:")
        response = await client.get("/api/v1/map/assets", params={**bounds, "cell_id": props["cell_id"], "grid_zoom": props["grid_zoom"]})
        assert response.status_code == 200
        assert response.json()["total"] == 3

    api_env.scenario(scenario)


def test_world_grid_coarsening_preserves_weighted_centers():
    from photo_search.api.map_api import _bounded_grid

    rows = [(x, y, 1, float(x), float(x), float(y), float(y), x * 32 + y + 1, float(x), float(y))
            for x in range(32) for y in range(32)]
    cells, level = _bounded_grid(rows, 5)
    assert level == 4
    assert len(cells) == 256
    assert sum(cell[2] for cell in cells) == 1024
    assert cells[0][2] == 4
    assert cells[0][8] / cells[0][2] == 0.5
    assert cells[0][9] / cells[0][2] == 0.5
