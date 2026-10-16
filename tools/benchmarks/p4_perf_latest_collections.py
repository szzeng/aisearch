"""Pair integrated-baseline/current collection reads on the same read-only fixture."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import statistics
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from photo_search.catalog.schema import SUPPORTED_SCHEMA_VERSION
from tools.benchmarks.p4_perf_people_300k import (
    Catalog,
    Oracle,
    Scenario,
    collections,
    database,
    digest,
    file_hash,
    open_readonly,
    save,
    validate,
)

DEPENDENCIES = (
    "src/photo_search/persons/collections.py", "src/photo_search/persons/members.py",
    "src/photo_search/face/detail_policy.py", "src/photo_search/catalog/image_policy.py",
    "tools/benchmarks/p4_perf_people_300k.py", "tools/benchmarks/p4_perf_latest_collections.py",
)


def measure(root: Path, baseline_file: Path, output: Path, samples: int) -> dict[str, Any]:
    start = time.time()
    path = database(root)
    fixture_hash = file_hash(path)
    source_hashes = {name: file_hash(Path(name)) for name in DEPENDENCIES}
    baseline_hash = file_hash(baseline_file)
    spec = importlib.util.spec_from_file_location("photo_search.persons._collection_perf_baseline", baseline_file)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    connection = open_readonly(path, immutable=True)
    catalog = Catalog(connection, db_path=path, data_root=root / "data")
    result: dict[str, Any] = {
        "layer": "list_collections core calls on the same immutable catalog; HTTP/UI/model excluded",
        "baseline_file": str(baseline_file), "baseline_sha256": baseline_hash,
        "source_sha256": source_hashes, "fixture_sha256": fixture_hash,
        "started_unix_seconds": start, "samples_requested": samples, "results": [],
        "order": "Paired baseline then current calls, alternating for every sample within each scenario",
        "first_note": "First call after independent oracle construction and hashing; not disk-cold or OS-cold",
        "timing_note": "Only product call timed; member validation and complete-response hashing are outside timer",
        "baseline_dependencies": "Baseline module uses current unchanged membership/image-policy imports; their hashes are frozen and compared with the saved integrated baseline",
    }
    try:
        assert catalog.schema_version == SUPPORTED_SCHEMA_VERSION
        oracle = Oracle(connection)
        ids = sorted(oracle.collections)
        result["schema_version"] = catalog.schema_version
        result["assets"] = len(oracle.assets)
        result["collections"] = len(ids)
        baseline_root = baseline_file.parents[2]
        result["shared_dependency_matches_baseline"] = {
            name: file_hash(baseline_root / Path(name).relative_to("src")) == source_hashes[name]
            for name in DEPENDENCIES[1:4]
        }
        assert all(result["shared_dependency_matches_baseline"].values())
        for name, offset in (("collections_first", 0), ("collections_deep160", 160)):
            cursor = None if not offset else collections._encode("pc1", ["collections1"], ids[offset - 1])
            case = Scenario(name, lambda: {}, ids, offset, 40, "collections", "collections")
            record: dict[str, Any] = {"name": name, "offset": offset, "limit": 40,
                "expected_ids_sha256": digest(ids[offset:offset + 40]), "errors": [],
                "before": {"samples_ms": [], "response_hashes": []},
                "after": {"samples_ms": [], "response_hashes": []}}
            for number in range(samples + 1):
                for phase, module in (("before", baseline), ("after", collections)):
                    elapsed = None
                    tick = time.perf_counter()
                    try:
                        answer = module.list_collections(catalog, limit=40, cursor=cursor)
                        elapsed = (time.perf_counter() - tick) * 1000
                        validate(answer, case, oracle)
                        response_hash = digest(answer)
                    except Exception as error:
                        if elapsed is None:
                            elapsed = (time.perf_counter() - tick) * 1000
                        response_hash = None
                        record["errors"].append({"phase": phase, "sample": number,
                                                 "type": type(error).__name__, "message": str(error)})
                    if number:
                        record[phase]["samples_ms"].append(elapsed)
                    else:
                        record[phase]["first_ms"] = elapsed
                    record[phase]["response_hashes"].append(response_hash)
                if number and number % 5 == 0:
                    print(json.dumps({"scenario": name, "completed_pairs": number,
                                      "before_last_ms": round(record["before"]["samples_ms"][-1], 2),
                                      "after_last_ms": round(record["after"]["samples_ms"][-1], 2)}), flush=True)
            for phase in ("before", "after"):
                values = record[phase]["samples_ms"]
                record[phase].update(median_ms=statistics.median(values),
                    p95_ms=sorted(values)[math.ceil(len(values) * .95) - 1], max_ms=max(values))
            record["complete_responses_equal"] = (
                record["before"]["response_hashes"] == record["after"]["response_hashes"]
                and len(set(record["before"]["response_hashes"])) == 1
                and None not in record["before"]["response_hashes"]
            )
            record["median_speedup"] = record["before"]["median_ms"] / record["after"]["median_ms"]
            result["results"].append(record)
            save(output, result)
            print(json.dumps({"scenario": name, "before_median_ms": record["before"]["median_ms"],
                              "after_median_ms": record["after"]["median_ms"],
                              "equal": record["complete_responses_equal"], "errors": len(record["errors"])}), flush=True)

        # Untimed real cursor traversal validates all directory rows, including the tail.
        paging = []
        cursor, offset = None, 0
        while True:
            previous = baseline.list_collections(catalog, limit=100, cursor=cursor)
            current = collections.list_collections(catalog, limit=100, cursor=cursor)
            case = Scenario("complete_directory", lambda: current, ids, offset, 100, "collections", "collections")
            validate(previous, case, oracle)
            validate(current, case, oracle)
            assert previous == current
            paging.append({"offset": offset, "count": len(current["collections"]), "response_sha256": digest(current)})
            offset += len(current["collections"])
            if not current["has_more"]:
                break
            cursor = current["next_cursor"]
            assert len(paging) <= math.ceil(len(ids) / 100)
        assert offset == len(ids)
        result["complete_cursor_paging"] = {"pages": paging, "count": offset, "passed": True}
        result["fixture_unchanged"] = file_hash(path) == fixture_hash
        result["sources_unchanged"] = all(file_hash(Path(name)) == sha for name, sha in source_hashes.items())
        result["baseline_unchanged"] = file_hash(baseline_file) == baseline_hash
        result["finished_unix_seconds"] = time.time()
        save(output, result)
        return result
    finally:
        catalog.close()


def verify(path: Path, junit: Path) -> bool:
    payload = json.loads(path.read_text(encoding="utf-8"))
    checks = {
        "fixture_unchanged": payload.get("fixture_unchanged", False),
        "sources_unchanged": payload.get("sources_unchanged", False),
        "baseline_unchanged": payload.get("baseline_unchanged", False),
        "shared_dependencies": all(payload["shared_dependency_matches_baseline"].values()),
        "supported_schema_and_300k": payload["schema_version"] == SUPPORTED_SCHEMA_VERSION and payload["assets"] == 300_000,
        "both_scenarios": {r["name"] for r in payload["results"]} == {"collections_first", "collections_deep160"},
        "full_cursor_traversal": payload.get("complete_cursor_paging", {}).get("passed", False),
    }
    for row in payload["results"]:
        prefix = row["name"] + ":"
        checks[prefix + "no_errors"] = not row["errors"]
        checks[prefix + "complete_responses_equal"] = (
            row["complete_responses_equal"]
            and row["before"]["response_hashes"] == row["after"]["response_hashes"]
            and len(set(row["before"]["response_hashes"])) == 1
            and None not in row["before"]["response_hashes"]
        )
        for phase in ("before", "after"):
            values = row[phase]["samples_ms"]
            checks[prefix + phase + ":30_samples"] = len(values) >= 30 and len(row[phase]["response_hashes"]) == len(values) + 1
            checks[prefix + phase + ":statistics"] = (
                row[phase]["median_ms"] == statistics.median(values)
                and row[phase]["p95_ms"] == sorted(values)[math.ceil(len(values) * .95) - 1]
                and row[phase]["max_ms"] == max(values)
            )
    suite = ET.Element("testsuite", name="latest_collections_measurement_not_pytest", tests=str(len(checks)),
                       failures=str(sum(not value for value in checks.values())), errors="0", skipped="0")
    for name, passed in checks.items():
        case = ET.SubElement(suite, "testcase", classname="collection_paired_measurement", name=name)
        if not passed:
            ET.SubElement(case, "failure", message="measurement evidence invariant failed")
    junit.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(junit, encoding="utf-8", xml_declaration=True)
    return all(checks.values())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("measure", "verify"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--junit", type=Path)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    for name in ("root", "baseline", "output", "junit"):
        path = getattr(args, name)
        if path is None:
            continue
        path = path.resolve()
        parents = (repository / ".tmp", repository / "docs/reports/artifacts") if name in ("output", "junit") else (repository / ".tmp",)
        if not any(path.is_relative_to(parent) for parent in parents):
            parser.error(f"--{name} is outside permitted repository directories")
        setattr(args, name, path)
    if args.action == "measure":
        if args.samples < 1:
            parser.error("--samples must be positive")
        result = measure(args.root, args.baseline, args.output, args.samples)
        raise SystemExit(1 if any(row["errors"] for row in result["results"]) else 0)
    if args.junit is None:
        parser.error("verify requires --junit")
    raise SystemExit(0 if verify(args.output, args.junit) else 1)


if __name__ == "__main__":
    main()
