"""Compare map API-core queries on the same isolated CN catalog (no HTTP/tiles)."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import math
import statistics
import time
from pathlib import Path
from types import SimpleNamespace

from photo_search.api import map_api
from photo_search.catalog.db import Catalog
from photo_search.catalog.sqlite_io import open_readonly
from photo_search.places import albums, cn_facets


def load(path: Path):
    spec = importlib.util.spec_from_file_location("baseline_map_api", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def measure(root: Path, module, album_module, samples: int, output: Path, selected: set[str]):
    data = root / "data"
    catalog = Catalog(open_readonly(data / "catalog/catalog.db", immutable=False),
                      db_path=data / "catalog/catalog.db", data_root=data)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(api=SimpleNamespace(
        service=lambda: SimpleNamespace(catalog=catalog)))))
    china = dict(west=73, south=18, east=135, north=54)
    sz = dict(west=113.75, south=22.40, east=114.65, north=22.90)
    empty = dict(west=120, south=22.40, east=120.1, north=22.90)
    fixture = json.loads((root / "fixture.json").read_text(encoding="utf-8"))

    async def invoke(name, **kwargs):
        core = getattr(module, "_" + name, None)
        if core is not None:
            return core(catalog, **kwargs)
        return await getattr(module, name)(request, **kwargs)

    scenarios = {
        "points_china_z12": lambda: invoke("map_points", **china, zoom=12),
        "points_shenzhen_z12": lambda: invoke("map_points", **sz, zoom=12),
        "points_empty_same_latitude": lambda: invoke("map_points", **empty, zoom=12),
        "assets_china": lambda: invoke("map_assets", **china),
        "assets_shenzhen_capture": lambda: invoke("map_assets", **sz, order="capture_time_desc"),
    }
    print("prefetch Shenzhen cursor", flush=True)
    first = await invoke("map_assets", **sz)
    print("prefetch done", flush=True)
    scenarios["assets_shenzhen_page2"] = lambda: invoke("map_assets", **sz, cursor=first["next_cursor"])

    async def album_call():
        return album_module.albums(catalog, data, kind="city")

    async def cn_call():
        return cn_facets.page(catalog, kind="city", key=fixture["shenzhen_key"], limit=48, cursor=None)

    scenarios.update(albums_city=album_call, album_shenzhen=cn_call)
    report = {"layer": "product API-core; excludes HTTP serialization, browser, tiles, actual files and models",
              "samples_per_scenario": samples, "fixture": str(root), "results": []}
    try:
        for name, call in scenarios.items():
            if selected and name not in selected:
                continue
            timings, hashes = [], []
            print(f"{name}: first call", flush=True)
            started = time.perf_counter()
            cold = await call()
            first_ms = (time.perf_counter() - started) * 1000
            print(f"{name}: first={first_ms:.2f} ms", flush=True)
            plans = []
            for repeat in range(samples):
                traces = []
                if repeat == 0:
                    catalog.connection.set_trace_callback(traces.append)
                started = time.perf_counter()
                result = await call()
                timings.append((time.perf_counter() - started) * 1000)
                catalog.connection.set_trace_callback(None)
                hashes.append(hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
                if (repeat + 1) % 5 == 0:
                    print(f"{name}: {repeat + 1}/{samples}, last={timings[-1]:.2f} ms", flush=True)
                if repeat == 0:
                    for sql in traces:
                        if sql.lstrip().upper().startswith("SELECT") and "asset_geo_facts" in sql:
                            plans.append([r[3] for r in catalog.connection.execute("EXPLAIN QUERY PLAN " + sql)])
            # SQLite aggregation time and Python allocation instrumentation are
            # measured separately; tracing every fine cell can distort the workload.
            peak = None
            assert len(set(hashes)) == 1
            if "features" in cold:
                assert sum(int(f["properties"].get("asset_count", 1)) for f in cold["features"]) == cold["total"]
            row = {"scenario": name, "first_call_ms": first_ms, "median_ms": statistics.median(timings),
                   "p95_ms": sorted(timings)[math.ceil(samples * .95) - 1], "max_ms": max(timings),
                   "samples_ms": timings, "errors": 0, "sha256": hashes[0], "python_peak_mib": peak,
                   "total": cold.get("total"), "features": len(cold.get("features", [])),
                   "grid_zoom": cold.get("grid_zoom"), "plans": plans}
            report["results"].append(row)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{name}: median={row['median_ms']:.2f} p95={row['p95_ms']:.2f} ms", flush=True)
    finally:
        catalog.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--scenarios", default="")
    args = parser.parse_args()
    if args.samples < 30:
        parser.error("at least 30 samples are required")
    if args.baseline:
        from photo_search.catalog import image_policy
        image_policy.image_location_sql = load(args.baseline.parent / "image_policy.py").image_location_sql
    asyncio.run(measure(args.root, load(args.baseline) if args.baseline else map_api,
                        load(args.baseline.parent / "albums.py") if args.baseline else albums,
                        args.samples, args.output, set(filter(None, args.scenarios.split(",")))))
