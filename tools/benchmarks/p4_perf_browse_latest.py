"""Paired latest/integrated-baseline browse calls with independent page oracles."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import statistics
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from photo_search.catalog.schema import SUPPORTED_SCHEMA_VERSION
from tools.benchmarks.p4_perf_jobs_300k import Catalog, MediaCache, open_readonly


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def measure(data, baseline_path, output, samples):
    # The benchmark bootstrap initializes the person package before queries, as
    # the application does (their existing albums/queries imports are cyclic).
    from photo_search.api import queries

    spec = importlib.util.spec_from_file_location("photo_search.api._browse_baseline", baseline_path)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    db_path = data / "catalog/catalog.db"
    connection = open_readonly(db_path, immutable=True)
    connection.isolation_level = None
    catalog = Catalog(connection, db_path=db_path, data_root=data)
    cache = MediaCache.open(data)
    source_paths = [*Path("src").rglob("*.py"), baseline_path, Path(__file__),
                    Path("tools/benchmarks/p4_perf_jobs_300k.py")]
    source_hashes = {str(p): sha(p) for p in source_paths}
    result = {"layer": "real asset_page core, no HTTP/model/browser", "samples_requested": samples,
              "first_note": "First observed after fixture oracle construction, not disk-cold",
              "order": "Each observation calls baseline then latest; oracle/hash excluded from timer",
              "fixture_sha256": sha(db_path), "source_sha256": source_hashes,
              "started_unix_seconds": time.time(), "scenarios": []}
    suite = ET.Element("testsuite", name="browse_measurement_not_pytest")
    failures = 0
    try:
        assert catalog.schema_version == SUPPORTED_SCHEMA_VERSION
        metadata = {int(r[0]): (int(r[1]), r[2]) for r in connection.execute(
            "SELECT a.asset_id,a.created_at,m.taken_at_sort FROM assets a "
            "LEFT JOIN asset_metadata m ON m.asset_id=a.asset_id")}
        assert len(metadata) == 300_000
        # This fixed fixture contains synthetic .jpg locations only. Check that
        # precondition explicitly instead of borrowing the product image predicate.
        locations = list(connection.execute("SELECT asset_id,source_id,relative_path FROM file_locations"))
        assert all(str(r[2]).lower().endswith('.jpg') for r in locations)
        source_ids = {int(r[0]) for r in locations if r[1] == 1 and r[0] is not None}
        ordered = {
            "asset_id": sorted(metadata),
            "time_desc": sorted(metadata, key=lambda n: (metadata[n][0], n), reverse=True),
            "capture_desc": sorted(metadata, key=lambda n: (metadata[n][1] is not None,
                                                              metadata[n][1] or 0, n), reverse=True),
        }
        revision = int(connection.execute("SELECT revision FROM catalog_filter_revision").fetchone()[0])
        known_count = sum(value[1] is not None for value in metadata.values())
        cases = [(order + suffix, order, offset, None) for order in ordered
                 for suffix, offset in (("_first", 0), ("_second", 50))]
        cases.extend([("source1_first", "asset_id", 0, 1),
                      ("source1_capture", "capture_desc", 0, 1),
                      ("capture_deep", "capture_desc", 200_000, None),
                      ("capture_null_tail", "capture_desc", known_count, None)])
        for name, order, offset, source in cases:
            ids = [n for n in ordered[order] if source is None or n in source_ids]
            cursor = None
            if offset:
                last = ids[offset - 1]
                if order == "asset_id":
                    cursor = baseline.encode_cursor(last)
                elif order == "time_desc":
                    cursor = baseline.encode_time_cursor(metadata[last][0], last)
                else:
                    key = metadata[last][1]
                    cursor = baseline.encode_capture_cursor(int(key is not None), key or 0, last, revision)
            params = dict(limit=50, order=order, cursor=cursor, source_id=source)
            row = {"name": name, "expected_ids": ids[offset:offset+50], "errors": [],
                   "before": {"samples_ms": [], "hashes": []}, "after": {"samples_ms": [], "hashes": []}}
            # The pilot's old source+capture cross-product did not complete and
            # was stopped. Keep that limitation explicit; validate the optimized
            # path independently without manufacturing a paired speedup.
            phases = (("before", baseline), ("after", queries))
            if name == "source1_capture":
                phases = (("after", queries),)
                row["baseline_status"] = "NOT_MEASURED: interrupted non-completing baseline pilot; no speedup claim"
            row["paired"] = len(phases) == 2
            for number in range(samples + 1):
                for phase, module in phases:
                    tick = time.perf_counter()
                    try:
                        payload = module.asset_page(catalog, cache, **params)
                        elapsed = (time.perf_counter() - tick) * 1000
                        assert [a["asset_id"] for a in payload["assets"]] == row["expected_ids"]
                        hashed = digest(payload)
                    except Exception as error:
                        elapsed = (time.perf_counter() - tick) * 1000
                        hashed = None
                        row["errors"].append({"phase": phase, "sample": number, "error": repr(error)})
                    row[phase]["hashes"].append(hashed)
                    if number:
                        row[phase]["samples_ms"].append(elapsed)
                    else:
                        row[phase]["first_ms"] = elapsed
                if number and number % 10 == 0:
                    print(f"{name}: {number}/{samples}", flush=True)
            for phase, _module in phases:
                values = row[phase]["samples_ms"]
                row[phase].update(median_ms=statistics.median(values),
                                  p95_ms=sorted(values)[math.ceil(len(values)*.95)-1], max_ms=max(values))
            row["passed"] = (not row["errors"] and (not row["paired"] or row["before"]["hashes"] == row["after"]["hashes"])
                             and len(set(row["after"]["hashes"])) == 1 and None not in row["after"]["hashes"])
            case = ET.SubElement(suite, "testcase", classname="browse_pair", name=name)
            if not row["passed"]:
                failures += 1
                ET.SubElement(case, "failure", message="oracle or full-response equality failed")
            result["scenarios"].append(row)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            before_ms = row["before"].get("median_ms")
            print(f"{name}: {before_ms} -> {row['after']['median_ms']:.2f}ms; verified={row['passed']}", flush=True)
        result["fixture_unchanged"] = sha(db_path) == result["fixture_sha256"]
        result["sources_unchanged"] = all(sha(Path(p)) == hashed for p, hashed in source_hashes.items())
        assert result["fixture_unchanged"] and result["sources_unchanged"]
    except BaseException as error:
        failures += 1
        result["fatal_error"] = repr(error)
        ET.SubElement(ET.SubElement(suite, "testcase", name="run_integrity"), "failure", message=repr(error))
        raise
    finally:
        catalog.close()
        result["finished_unix_seconds"] = time.time()
        result["passed"] = failures == 0
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        suite.attrib.update(tests=str(len(suite)), failures=str(failures), errors="0", skipped="0")
        ET.ElementTree(suite).write(output.with_suffix('.xml'), encoding='utf-8', xml_declaration=True)
    if failures:
        raise RuntimeError("browse evidence failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=30)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    for path in (args.data, args.baseline):
        if not path.resolve().is_relative_to(repository / '.tmp'):
            parser.error('only project-local synthetic fixtures permitted')
    if args.samples < 1:
        parser.error('positive samples required; fewer than 30 is only a pilot')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    measure(args.data.resolve(), args.baseline.resolve(), args.output.resolve(), args.samples)
