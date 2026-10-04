"""One bounded 300k synthetic product-API timing sample for the MAP acceptance card."""

from __future__ import annotations

import json
import time
from pathlib import Path


def _seed_300k(api_env) -> int:
    catalog = api_env.service.catalog
    with catalog.write() as conn:
        conn.execute(
            "INSERT INTO source_roots(display_name,root_path,status) VALUES('MAP synthetic fixture','synthetic://map','ONLINE')"
        )
        source_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        for start in range(0, 300_000, 10_000):
            assets, locations, facts = [], [], []
            for i in range(start, min(start + 10_000, 300_000)):
                asset_id = i + 1
                sha = f"{asset_id:064x}"
                if i < 5_000:
                    lat, lon = 30.0, 120.0
                elif i < 100_000:
                    lat, lon = 30.0 + (i % 1000) * 0.0001, 120.0 + (i % 2000) * 0.0001
                elif i < 200_000:
                    lat, lon = -70 + (i % 14000) / 100, -179 + (i % 35800) / 100
                else:
                    lat, lon = 35.0 + (i % 200) * 0.001, 179.5 if i % 2 else -179.5
                assets.append((asset_id, sha, 1024, 32, 24, "JPEG", 1, "ACTIVE"))
                locations.append(
                    (
                        asset_id,
                        source_id,
                        f"photo-{asset_id}.jpg",
                        f"photo-{asset_id}",
                        asset_id,
                        1024,
                        1,
                        None,
                        "PRESENT",
                    )
                )
                facts.append(
                    (
                        asset_id,
                        f"gps-{asset_id}",
                        asset_id,
                        sha,
                        1,
                        "VALID",
                        "EXIF_GPS",
                        "geo-exif-gps@1",
                        "a" * 64,
                        '{"synthetic":true}',
                        lat,
                        lon,
                        "WGS84",
                        1,
                        1,
                    )
                )
            conn.executemany(
                "INSERT INTO assets(asset_id,sha256,byte_size,width,height,format,created_at,state) VALUES(?,?,?,?,?,?,?,?)",
                assets,
            )
            conn.executemany(
                "INSERT INTO file_locations(location_id,source_id,relative_path,path_key,asset_id,size,mtime_ns,last_seen_scan_id,availability) VALUES(?,?,?,?,?,?,?,?,?)",
                locations,
            )
            conn.executemany(
                """INSERT INTO asset_geo_facts(asset_id,geo_fact_id,location_id,input_revision,fact_revision,status,source,processor_id,
                   raw_fields_checksum_sha256,raw_gps_json,latitude,longitude,datum_status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                facts,
            )
    return 300_000


def test_map_product_api_300k_summary_scopes_and_paging(api_env) -> None:
    assert _seed_300k(api_env) == 300_000
    plan = [
        str(row["detail"])
        for row in api_env.service.catalog.read_all(
            "EXPLAIN QUERY PLAN SELECT a.asset_id FROM assets a JOIN asset_geo_facts g USING(asset_id) "
            "WHERE g.status='VALID' AND g.datum_status IN ('WGS84','ASSUMED_WGS84') "
            "AND g.input_revision=a.sha256 AND g.latitude BETWEEN 29 AND 32 "
            "AND g.longitude BETWEEN 119 AND 122 AND EXISTS "
            "(SELECT 1 FROM file_locations l WHERE l.asset_id=a.asset_id AND l.availability='PRESENT')"
        )
    ]
    assert "idx_asset_geo_map_bbox" in " ".join(plan), plan
    scopes = {
        "world": {"west": -180, "south": -90, "east": 180, "north": 90},
        "city": {"west": 119, "south": 29, "east": 122, "north": 32},
        "small": {"west": 120, "south": 30, "east": 120.05, "north": 30.05},
    }
    report = {
        "synthetic_assets": 300_000,
        "effective_gps": 300_000,
        "city_bbox_query_plan": plan,
        "scopes": {},
    }

    async def scenario(client):
        await api_env.authenticate(client)
        summary = (await client.get("/api/v1/map/summary")).json()
        assert summary["total"] == 300_000
        for name, bbox in scopes.items():
            samples = []
            for repeat in range(4):
                started = time.perf_counter()
                points_response = await client.get("/api/v1/map/points", params={**bbox, "zoom": 4})
                points_ms = round((time.perf_counter() - started) * 1000, 2)
                assert points_response.status_code == 200, points_response.text
                points = points_response.json()
                assigned = (
                    sum(int(f["properties"]["asset_count"]) for f in points["features"])
                    if points["mode"] == "grid"
                    else len(points["features"])
                )
                assert assigned == points["total"]
                if points["mode"] == "grid":
                    assert len(points["features"]) <= 1000
                started = time.perf_counter()
                page_response = await client.get("/api/v1/map/assets", params={**bbox, "limit": 48})
                page_ms = round((time.perf_counter() - started) * 1000, 2)
                page = page_response.json()
                assert page_response.status_code == 200 and len(page["assets"]) <= 48
                page2 = None
                if page["next_cursor"]:
                    page2_response = await client.get(
                        "/api/v1/map/assets",
                        params={**bbox, "limit": 48, "cursor": page["next_cursor"]},
                    )
                    assert page2_response.status_code == 200
                    page2 = page2_response.json()
                    assert not (
                        {a["asset_id"] for a in page["assets"]}
                        & {a["asset_id"] for a in page2["assets"]}
                    )
                if name == "city" and repeat == 0 and points["mode"] == "grid":
                    feature = points["features"][0]
                    cell = await client.get(
                        "/api/v1/map/assets",
                        params={
                            **bbox,
                            "limit": 48,
                            "cell_id": feature["properties"]["cell_id"],
                            "grid_zoom": feature["properties"]["grid_zoom"],
                        },
                    )
                    assert cell.status_code == 200
                    assert cell.json()["total"] == feature["properties"]["asset_count"]
                samples.append(
                    {
                        "repeat": repeat,
                        "total": points["total"],
                        "mode": points["mode"],
                        "features": len(points["features"]),
                        "count_sum": assigned,
                        "points_ms": points_ms,
                        "points_bytes": len(points_response.content),
                        "assets_ms": page_ms,
                        "assets_bytes": len(page_response.content),
                        "first_page": len(page["assets"]),
                        "has_next": bool(page["next_cursor"]),
                        "second_page": 0 if page2 is None else len(page2["assets"]),
                    }
                )
            report["scopes"][name] = samples

    api_env.scenario(scenario)
    target = Path("test-results/p4-place-map-300k-timings.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
