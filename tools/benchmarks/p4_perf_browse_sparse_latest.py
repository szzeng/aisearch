"""Paired sparse person/collection browse; raw-row oracle, synthetic data only."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import statistics
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from photo_search.catalog.schema import SUPPORTED_SCHEMA_VERSION

# The frozen people oracle initializes persons before queries, matching app bootstrap.
# isort: off
from tools.benchmarks.p4_perf_people_300k import (
    Catalog,
    MediaCache,
    Oracle,
    digest,
    file_hash,
    open_readonly,
)

from photo_search.api import queries
# isort: on


def measure(data: Path, baseline_path: Path, output: Path, samples: int):
    spec = importlib.util.spec_from_file_location("photo_search.api._sparse_baseline", baseline_path)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    db = data / "catalog/catalog.db"
    connection = open_readonly(db, immutable=True)
    catalog = Catalog(connection, db_path=db, data_root=data)
    cache = MediaCache.open(data)
    sources = (Path(queries.__file__), baseline_path, Path(__file__),
               Path("src/photo_search/search/retrieval.py"),
               Path("src/photo_search/persons/collection_sql.py"),
               Path("src/photo_search/persons/members.py"),
               Path("src/photo_search/catalog/image_policy.py"))
    result = {"layer": "real asset_page core on unchanged synthetic people fixture; no HTTP/UI",
              "samples_requested": samples, "started_unix_seconds": time.time(),
              "fixture_sha256": file_hash(db),
              "source_sha256": {str(p): file_hash(p) for p in sources},
              "order": "baseline then current for each pair; oracle/hash excluded from timer",
              "first_note": "first observed after raw-row oracle, not disk-cold", "scenarios": []}
    suite = ET.Element("testsuite", name="sparse_browse_measurement_not_pytest")
    failures = 0
    try:
        oracle = Oracle(connection)
        assert catalog.schema_version == SUPPORTED_SCHEMA_VERSION
        revision = int(connection.execute("SELECT revision FROM catalog_filter_revision").fetchone()[0])
        for owner, members, filters in (
            ("person1_150k", oracle.members[1], {"person_ids": (1,)}),
            ("person21", oracle.members[21], {"person_ids": (21,)}),
            ("collection2_all", oracle.collection_assets[2], {"collection_id": 2}),
            ("collection3", oracle.collection_assets[3], {"collection_id": 3}),
        ):
            ids = oracle.ordered_photos(members)
            assert ids
            for label, offset in (("first", 0), ("deep80pct", len(ids) * 4 // 5)):
                name = owner + "_" + label
                cursor = None
                if offset:
                    last = ids[offset - 1]
                    key = oracle.assets[last][1]
                    cursor = baseline.encode_capture_cursor(int(key is not None), key or 0, last, revision)
                parameters = dict(limit=60, order="capture_desc", cursor=cursor, **filters)
                row = {"name": name, "member_count": len(ids), "offset": offset,
                       "expected_ids": ids[offset:offset + 60], "errors": [],
                       "before": {"samples_ms": [], "hashes": []},
                       "after": {"samples_ms": [], "hashes": []}}
                for sample in range(samples + 1):
                    for phase, module in (("before", baseline), ("after", queries)):
                        tick = time.perf_counter()
                        elapsed = None
                        hashed = None
                        try:
                            payload = module.asset_page(catalog, cache, **parameters)
                            elapsed = (time.perf_counter() - tick) * 1000
                            assert [r["asset_id"] for r in payload["assets"]] == row["expected_ids"]
                            hashed = digest(payload)
                        except Exception as error:
                            row["errors"].append({"phase": phase, "sample": sample, "error": repr(error)})
                        if elapsed is None:
                            elapsed = (time.perf_counter() - tick) * 1000
                        row[phase]["hashes"].append(hashed)
                        if sample:
                            row[phase]["samples_ms"].append(elapsed)
                        else:
                            row[phase]["first_ms"] = elapsed
                for phase in ("before", "after"):
                    values = row[phase]["samples_ms"]
                    row[phase].update(median_ms=statistics.median(values),
                        p95_ms=sorted(values)[math.ceil(len(values) * .95) - 1], max_ms=max(values))
                row["passed"] = (not row["errors"]
                    and row["before"]["hashes"] == row["after"]["hashes"]
                    and len(set(row["after"]["hashes"])) == 1
                    and None not in row["after"]["hashes"])
                case = ET.SubElement(suite, "testcase", name=name)
                if not row["passed"]:
                    failures += 1
                    ET.SubElement(case, "failure", message="oracle or full-response comparison failed")
                result["scenarios"].append(row)
                output.write_text(json.dumps(result, indent=2), encoding="utf-8")
                print(f"{name}: {row['before']['median_ms']:.2f} -> {row['after']['median_ms']:.2f}ms", flush=True)
        result["fixture_unchanged"] = file_hash(db) == result["fixture_sha256"]
        result["sources_unchanged"] = all(file_hash(Path(p)) == h for p, h in result["source_sha256"].items())
        assert result["fixture_unchanged"] and result["sources_unchanged"]
    except BaseException as error:
        failures += 1
        result["fatal_error"] = repr(error)
        ET.SubElement(ET.SubElement(suite, "testcase", name="integrity"), "failure", message=repr(error))
        raise
    finally:
        catalog.close()
        result["finished_unix_seconds"] = time.time()
        result["passed"] = failures == 0
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        suite.attrib.update(tests=str(len(suite)), failures=str(failures), errors="0", skipped="0")
        ET.ElementTree(suite).write(output.with_suffix(".xml"), encoding="utf-8", xml_declaration=True)
    if failures:
        raise RuntimeError("sparse browse evidence failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=1)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    for path in (args.data, args.baseline):
        if not path.resolve().is_relative_to(repo / ".tmp"):
            parser.error("only project-local synthetic fixtures permitted")
    if args.samples < 1:
        parser.error("positive samples required; fewer than 30 is only a pilot")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    measure(args.data.resolve(), args.baseline.resolve(), args.output.resolve(), args.samples)
