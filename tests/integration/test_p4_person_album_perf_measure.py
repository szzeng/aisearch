"""Three-pair synthetic before/after timing and SQL-count evidence for album reads."""

from __future__ import annotations

import json
import time
from pathlib import Path

from conftest import ApiEnv
from p3_persons_page_fixtures import add_link, add_person, seed_library

from photo_search.api import queries
from photo_search.persons import albums as album_reads
from photo_search.taken_time.read import asset_view


def test_person_album_bounded_before_after_measurement(api_env: ApiEnv) -> None:
    photos = seed_library(
        api_env,
        {f"perf-{index:02d}.jpg": [(20, 20, 100, 100)] for index in range(61)},
    )
    catalog = api_env.service.catalog
    person_ids = [add_person(catalog, name=f"人物 {index:02d}") for index in range(42)]
    for photo in photos.values():
        add_link(catalog, photo["faces"][0], person_ids[0])

    original_summaries = album_reads._album_summaries
    original_facts = album_reads._photo_page_facts
    original_asset_chunk = queries._asset_chunk
    main_query_samples: list[float] = []
    main_query: dict[str, object] = {}

    def scalar_summaries(connection, rows):
        return [album_reads._album_summary(connection, row) for row in rows]

    def scalar_facts(connection, *, asset_ids, person_id):
        return (
            {asset_id: asset_view(connection, asset_id) for asset_id in asset_ids},
            {
                asset_id: album_reads._photo_person_faces(
                    connection, asset_id=asset_id, person_id=person_id
                )
                for asset_id in asset_ids
            },
        )

    def timed_asset_chunk(*args, **kwargs):
        statements: list[str] = []
        args[0].connection.set_trace_callback(statements.append)
        start = time.perf_counter()
        result = original_asset_chunk(*args, **kwargs)
        elapsed_ms = (time.perf_counter() - start) * 1000
        args[0].connection.set_trace_callback(None)
        main_query["calls"] = int(main_query.get("calls", 0)) + 1
        main_query_samples.append(elapsed_ms)
        main_query["elapsed_ms"] = elapsed_ms
        main_query["expanded_sql"] = next(
            (sql for sql in statements if "FROM assets AS a" in sql and "ORDER BY" in sql),
            None,
        )
        return result

    queries._asset_chunk = timed_asset_chunk

    async def scenario(client):
        await api_env.authenticate(client)
        result = {"people": {"baseline_ms": [], "optimized_ms": []},
                  "photos": {"baseline_ms": [], "optimized_ms": []}}
        try:
            for _ in range(3):
                for version in ("baseline", "optimized"):
                    if version == "baseline":
                        album_reads._album_summaries = scalar_summaries
                        album_reads._photo_page_facts = scalar_facts
                    else:
                        album_reads._album_summaries = original_summaries
                        album_reads._photo_page_facts = original_facts
                    for endpoint, url in (
                        ("people", "/api/v1/persons/browse?kind=all&limit=40"),
                        ("photos", f"/api/v1/persons/{person_ids[0]}/photos?limit=60"),
                    ):
                        start = time.perf_counter()
                        response = await client.get(url)
                        elapsed_ms = (time.perf_counter() - start) * 1000
                        assert response.status_code == 200
                        body = response.json()
                        if endpoint == "people":
                            assert len(body["persons"]) == 40 and body["has_more"]
                        else:
                            assert len(body["assets"]) == 60 and body["has_more"]
                        result[endpoint][f"{version}_ms"].append(elapsed_ms)
            plan = catalog.connection.execute(
                "EXPLAIN QUERY PLAN " + str(main_query["expanded_sql"])
            ).fetchall()
            result["main_sql"] = {
                "calls_per_photo_request": int(main_query["calls"]) // 6,
                "chunk_sql_ms_samples": main_query_samples,
                "query_plan": [str(row["detail"]) for row in plan],
            }
            result["projection_sql_counts"] = {
                "people": {"baseline_per_page": 40 * 3, "optimized_per_page": 2},
                "photos": {"baseline_per_page": 60 * 3, "optimized_per_page": 3},
                "count_basis": "baseline scalar projection call graph; optimized statements asserted by focused SQL-trace tests",
            }
            result["notes"] = {
                "fixture": "61 synthetic photos, 42 named people; no private catalog or model access",
                "images": "cold/hot media visible time NOT MEASURED; no real browser image decoding",
                "pairs": 3,
            }
            output = Path("test-results/p4-person-album-perf-measure.json")
            output.parent.mkdir(exist_ok=True)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            album_reads._album_summaries = original_summaries
            album_reads._photo_page_facts = original_facts
            queries._asset_chunk = original_asset_chunk

    api_env.scenario(scenario)
