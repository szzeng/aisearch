"""One reusable 300k synthetic membership fixture, through the product API.

No photos or models are read. Memberships are seeded for SQL timing; the bounded
actual geometry throughput is measured separately with the real package.
"""

import json
import time
from pathlib import Path

from p4_cn_fixtures import install

from photo_search.geo.cn_admin import PROCESSOR
from tests.integration.test_p4_place_map_300k import _seed_300k


def test_cn_300k_counts_and_keyset_pages(api_env):
    catalog = api_env.service.catalog
    dataset_id, _ = install(catalog, api_env.tmp_path)
    assert _seed_300k(api_env) == 300000
    mapping = {
        r["external_id"]: int(r["admin_id"]) for r in catalog.read_all("SELECT * FROM cn_admin_ids")
    }
    province, city, district = (mapping[f"relation/{n}"] for n in (1, 2, 3))
    with catalog.write() as connection:
        connection.execute("UPDATE asset_geo_facts SET latitude=30.273,longitude=120.09")
        connection.execute(
            """INSERT INTO cn_admin_resolutions
            SELECT gf.asset_id,?,gf.geo_fact_id,gf.input_revision,?,?,?,?,'MATCHED',1
              FROM asset_geo_facts gf""",
            (dataset_id, PROCESSOR, province, city, district),
        )
        # Same assets at a second source: no duplicated membership/count.
        connection.execute(
            "INSERT INTO source_roots(display_name,root_path,status) VALUES('CN copy fixture','synthetic://cn-copy','ONLINE')"
        )
        source = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.execute(
            """INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,availability)
            SELECT ?,relative_path,path_key,asset_id,size,mtime_ns,'PRESENT'
              FROM file_locations WHERE asset_id<=10000""",
            (source,),
        )
    report = {
        "assets": 300000,
        "duplicate_locations": 10000,
        "fixture": "synthetic membership rows; no claim of 300k polygon resolution",
        "scopes": {},
    }

    async def scenario(client):
        await api_env.authenticate(client)
        for kind, key in [("province", province), ("city", city), ("district", district)]:
            samples = []
            for repeat in range(4):
                before = time.perf_counter()
                response = await client.get("/api/v1/places/albums", params={"kind": kind})
                listing_ms = (time.perf_counter() - before) * 1000
                assert response.status_code == 200, response.text
                albums = response.json()["albums"]
                assert len(albums) == 1 and albums[0]["total"] == 300000
                before = time.perf_counter()
                response = await client.get(
                    f"/api/v1/places/albums/{kind}/cn:{key}/assets", params={"limit": 48}
                )
                first_ms = (time.perf_counter() - before) * 1000
                assert response.status_code == 200, response.text
                first = response.json()
                before = time.perf_counter()
                response = await client.get(
                    f"/api/v1/places/albums/{kind}/cn:{key}/assets",
                    params={"limit": 48, "cursor": first["next_cursor"]},
                )
                next_ms = (time.perf_counter() - before) * 1000
                assert response.status_code == 200, response.text
                second = response.json()
                assert first["total"] == second["total"] == 300000
                assert [r["asset_id"] for r in first["assets"]] == list(range(1, 49))
                assert [r["asset_id"] for r in second["assets"]] == list(range(49, 97))
                samples.append(
                    {
                        "repeat": repeat,
                        "count_ms": round(listing_ms, 2),
                        "first_ms": round(first_ms, 2),
                        "next_ms": round(next_ms, 2),
                        "first_rows": 48,
                        "next_rows": 48,
                        "total": 300000,
                    }
                )
            report["scopes"][kind] = samples
        # Pure CN search must use SQL keyset pages without building all 300k IDs.
        csrf = client.cookies.get("ps_csrf")
        response = await client.post(
            "/api/v1/search/place-aware",
            json={"request_id": "cn-300k-pure", "original_query": "西湖区", "page_size": 48},
            headers=api_env.post_headers(csrf),
        )
        assert response.status_code == 200, response.text
        assert response.json()["total_candidates"] == 300000
        assert len(response.json()["hits"]) == 48

    api_env.scenario(scenario)
    target = Path("test-results/p4-cn-300k-timings.json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
