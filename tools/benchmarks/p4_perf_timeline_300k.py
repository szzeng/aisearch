"""Read-only timeline API-core benchmark on the isolated 300k CN catalog.

Measures first observed call plus >=30 repetitions; first calls are not disk-cold
because other scenarios and cursor setup share the same process/OS page cache.
No image decoding, HTTP serialization, browser work, or model inference is measured.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any

from photo_search.api import timeline
from photo_search.catalog import image_policy, video
from photo_search.catalog.db import Catalog
from photo_search.catalog.sqlite_io import open_readonly


def _load(path: Path, *, restore_policy: bool = True):
    shared_policy = path.parent / "image_policy.py"
    if restore_policy and shared_policy.exists():
        # The frozen timeline calls the shared photo predicate through its module
        # globals. Restore that dependency too when rerunning an old baseline.
        policy = _load(shared_policy, restore_policy=False)
        image_policy.image_location_sql = policy.image_location_sql
        image_policy.photo_asset_sql = policy.photo_asset_sql
        video.photo_asset_sql = policy.photo_asset_sql
    spec = importlib.util.spec_from_file_location("baseline_timeline", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load timeline implementation: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _fixture_identity(catalog: Catalog) -> dict[str, Any]:
    digest = hashlib.sha256()
    # Full logical metadata and location identity survives additive index migrations.
    for query in (
        "SELECT a.asset_id,a.sha256,a.created_at,a.state,m.taken_at_sort,m.time_source,"
        "m.time_confidence,m.timezone_offset FROM assets a LEFT JOIN asset_metadata m "
        "USING(asset_id) ORDER BY a.asset_id",
        "SELECT location_id,asset_id,source_id,relative_path,availability "
        "FROM file_locations ORDER BY location_id",
        "SELECT source_id,root_path FROM source_roots ORDER BY source_id",
    ):
        for row in catalog.connection.execute(query):
            digest.update(json.dumps(list(row), ensure_ascii=False).encode("utf-8"))
            digest.update(b"\n")
    counts = {table: int(catalog.read_one(f"SELECT COUNT(*) FROM {table}")[0])
              for table in ("assets", "asset_metadata", "file_locations")}
    return {"logical_sha256": digest.hexdigest(), "counts": counts}


def _scenarios(catalog: Catalog, module):
    result = {}
    for basis in ("library", "capture"):
        params = {"time_basis": basis, "limit": 50}
        result[f"{basis}_page1"] = lambda params=params: module.timeline_page(catalog, **params)
        first = module.timeline_page(catalog, **params)
        if first["next_cursor"] is None:
            raise RuntimeError(f"fixture has no second {basis} page")
        page2 = {**params, "cursor": first["next_cursor"]}
        result[f"{basis}_page2"] = lambda page2=page2: module.timeline_page(catalog, **page2)
        seek = {**params, "seek_year": 2023 if basis == "library" else 2012, "seek_month": 6}
        result[f"{basis}_seek"] = lambda seek=seek: module.timeline_page(catalog, **seek)
        periods = {"time_basis": basis}
        result[f"periods_{basis}"] = lambda periods=periods: module.timeline_periods(
            catalog, **periods)
    result["capture_unknown"] = lambda: module.timeline_page(catalog, bucket="unknown")
    result["capture_pending"] = lambda: module.timeline_page(catalog, bucket="pending")
    result["capture_source"] = lambda: module.timeline_page(catalog, kind="source", source_id=1)
    result["periods_capture_months"] = lambda: module.timeline_periods(catalog, year=2012)
    return result


def _measure(catalog: Catalog, call, samples: int) -> dict[str, Any]:
    started = time.perf_counter()
    first = call()
    first_ms = (time.perf_counter() - started) * 1000
    digest = _hash(first)
    durations = []
    for repeat in range(samples):
        started = time.perf_counter()
        result = call()
        durations.append((time.perf_counter() - started) * 1000)
        if _hash(result) != digest:
            raise AssertionError("result changed during read-only repeated benchmark")
        if (repeat + 1) % 5 == 0:
            print(f"samples_complete={repeat + 1}/{samples}", flush=True)
    traces = []
    catalog.connection.set_trace_callback(traces.append)
    try:
        traced = call()  # Query tracing is deliberately outside the timing samples.
    finally:
        catalog.connection.set_trace_callback(None)
    if _hash(traced) != digest:
        raise AssertionError("traced result differs from benchmark result")
    plans = []
    for sql in dict.fromkeys(traces):
        if sql.lstrip().upper().startswith("SELECT") and "assets AS a" in sql:
            plans.append({"sql": sql, "detail": [str(row[3]) for row in
                          catalog.connection.execute("EXPLAIN QUERY PLAN " + sql)]})
    rows = first.get("assets", [])
    return {
        "first_call_ms": first_ms, "samples": samples, "samples_ms": durations,
        "median_ms": statistics.median(durations),
        "p95_ms": sorted(durations)[math.ceil(samples * .95) - 1],
        "max_ms": max(durations), "errors": 0, "sha256": digest,
        "rows": len(rows), "asset_ids": [row["asset_id"] for row in rows],
        "display_times": [row["display_time"] for row in rows],
        "counts": first.get("counts"), "years": first.get("years"),
        "months": first.get("months"), "plans": plans,
    }


def measure(root: Path, module, samples: int, output: Path,
            selected: set[str], compare: Path | None, measurement_context: str = "") -> bool:
    data = root / "data"
    dbpath = data / "catalog/catalog.db"
    catalog = Catalog(open_readonly(dbpath), db_path=dbpath, data_root=data)
    implementation = Path(module.__file__).resolve()
    prior = json.loads(compare.read_text(encoding="utf-8")) if compare else None
    report = {
        "layer": "timeline API-core; excludes HTTP/browser/image decoding/model inference",
        "first_call_note": "first observed scenario call, shared process/cache; not disk-cold",
        "measurement_context": measurement_context,
        "fixture_root": str(root), "fixture": _fixture_identity(catalog),
        "schema_version": catalog.schema_version, "samples_per_scenario": samples,
        "sqlite_version": catalog.read_one("SELECT sqlite_version()")[0],
        "implementation": str(implementation),
        "implementation_sha256": hashlib.sha256(implementation.read_bytes()).hexdigest(),
        "indexes": [row[0] for row in catalog.read_all(
            "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name")],
        "results": [],
    }
    failed = False
    try:
        scenarios = _scenarios(catalog, module)
        if unknown := selected - scenarios.keys():
            raise ValueError(f"unknown scenarios: {sorted(unknown)}")
        if prior and prior["fixture"] != report["fixture"]:
            raise AssertionError("baseline and current logical fixture identities differ")
        previous = {row["scenario"]: row for row in prior["results"]} if prior else {}
        for name, call in scenarios.items():
            if selected and name not in selected:
                continue
            print(f"scenario_start={name} samples={samples}", flush=True)
            try:
                row = {"scenario": name, **_measure(catalog, call, samples)}
                if name in previous:
                    row["baseline_sha256"] = previous[name]["sha256"]
                    row["baseline_matches"] = row["sha256"] == row["baseline_sha256"]
                    failed |= not row["baseline_matches"]
                print(f"{name}: median={row['median_ms']:.2f} "
                      f"p95={row['p95_ms']:.2f} ms", flush=True)
            except Exception as error:
                row = {"scenario": name, "errors": 1,
                       "error": f"{type(error).__name__}: {error}"}
                failed = True
                print(f"{name}: {row['error']}", flush=True)
            report["results"].append(row)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        catalog.close()
    return not failed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--implementation", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--scenarios", default="")
    parser.add_argument("--measurement-context", default="",
                        help="hardware and observed concurrent workload for the report")
    args = parser.parse_args()
    if args.samples < 30:
        parser.error("at least 30 samples are required")
    passed = measure(args.root.resolve(), _load(args.implementation) if args.implementation
                     else timeline, args.samples, args.output.resolve(),
                     set(filter(None, args.scenarios.split(","))), args.compare,
                     args.measurement_context)
    raise SystemExit(0 if passed else 1)
