"""Reproducible synthetic SQLite cost of the bounded, packed machine-tag projection.

This does not run inference, create 300k image/artifact files or measure HTTP latency.
The output separately reports the database increment and illustrative artifact byte costs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import sqlite3
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from photo_search.catalog.db import Catalog
from photo_search.catalog.sqlite_io import open_readonly
from photo_search.scheduler.contracts import canonical_json
from photo_search.tagging import store


def _source_identity() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    paths = (Path(store.__file__).resolve(), Path(__file__).resolve(),
             root / "src/photo_search/catalog/schema.py",
             root / "src/photo_search/catalog/migrations/0043_machine_image_tags.sql")
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


def _measure(catalog: Catalog, processor: str, count: int) -> dict:
    result = {}
    state = store.status(catalog.connection, processor)
    model = state
    assert state["completed_count"] == count
    assert state["score_count"] == count * 32
    assert state["job_counts"]["SUCCEEDED"] == count
    rng = random.Random(20261021)
    candidate_ids = rng.sample(range(1, count + 1), 5000)
    timings = []
    for _ in range(21):
        t0 = time.perf_counter()
        with catalog.read_snapshot() as db:
            state = store.status(db, processor)
            scores, covered = store.scores_for_assets(
                db, processor_id=processor, asset_ids=candidate_ids, tag_id=2)
        timings.append((time.perf_counter() - t0) * 1000)
        assert covered == set(candidate_ids)
        expected = {i: 1 - ((2 - i) % 4585) / 64 for i in candidate_ids
                    if (2 - i) % 4585 < 32}
        assert scores == expected
    result["raw_candidate_lookup_ms"] = timings
    result["first_candidate_lookup_ms"] = timings[0]
    result["warm_candidate_lookup_p50_ms"] = statistics.median(timings[1:])
    result["warm_candidate_lookup_p95_ms"] = sorted(timings[1:])[18]
    poll = []
    for _ in range(100):
        t0 = time.perf_counter()
        store.status(catalog.connection, processor)
        poll.append((time.perf_counter() - t0) * 1000)
    result["raw_status_ms"] = poll
    result["status_p50_ms"] = statistics.median(poll)
    result["status_p95_ms"] = sorted(poll)[94]
    result["lookup_query_plan"] = [str(row[3]) for row in catalog.connection.execute(
        "EXPLAIN QUERY PLAN SELECT r.scores FROM machine_tag_results r "
        "JOIN assets a ON a.asset_id=r.asset_id AND a.sha256=r.input_revision "
        "WHERE r.model_key=? AND r.asset_id IN (1,2,3)", (model["model_key"],))]
    result["measurement_note"] = "21 local reads (first reported separately), 100 polls; concurrent development host, not a latency guarantee."
    return result


def run(output: Path, count: int) -> dict:
    if output.exists():
        raise ValueError("use a new output directory to preserve previous evidence")
    if not 5000 <= count <= 300_000:
        raise ValueError("synthetic asset count must be between 5000 and 300000")
    output.mkdir(parents=True)
    root = output / "data"
    result = {"schema": "photo-search-tag-store-benchmark-v1", "assets": count,
              "candidate_limit": 5000, "tags_per_asset": 32,
              "scope": "synthetic SQLite only; no inference, files, real photos or HTTP"}
    started = time.perf_counter()
    with Catalog.open(data_root=root) as catalog:
        with catalog.write() as db:
            model = store.register_model(db, model_id="benchmark-ram-model", policy_id="top32-fp16",
                                         manifest_sha256="b" * 64, class_count=4585,
                                         max_scores=32, now_ms=1)
        processor = model["processor_id"]
        for first in range(1, count + 1, 1000):
            batch = range(first, min(first + 1000, count + 1))
            with catalog.write() as db:
                db.executemany("INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) "
                               "VALUES(?,?,1,1,'ACTIVE')", [(i, f"{i:064x}") for i in batch])
                db.executemany("INSERT INTO jobs(job_id,subject_type,subject_id,input_revision,"
                               "task_type,processor_id,state,priority,attempts,max_attempts,"
                               "run_after,progress,created_at,updated_at) "
                               "VALUES(?,'asset',?,?,?,?,'SUCCEEDED',0,0,3,0,1,1,1)",
                               [(i, str(i), f"{i:064x}", store.TASK_TYPE, processor) for i in batch])
        catalog.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        base_size = catalog.db_path.stat().st_size
        result["base_catalog_bytes"] = base_size
        result["base_build_seconds"] = time.perf_counter() - started
        started = time.perf_counter()
        for first in range(1, count + 1, 1000):
            batch = range(first, min(first + 1000, count + 1))
            packed_rows = []
            for i in batch:
                entries = [((i + tag) % 4585, 1 - tag / 64, tag < 16) for tag in range(32)]
                packed = b"".join(store.SCORE_ENTRY.pack(*entry) for entry in entries)
                packed_rows.append((model["model_key"], i, f"{i:064x}", i,
                                    store.artifact_id_for(f"{i:064x}", processor),
                                    hashlib.sha256(packed).hexdigest(), 32, packed, 2))
            with catalog.write() as db:
                db.executemany("INSERT INTO machine_tag_results(model_key,asset_id,input_revision,"
                               "job_id,artifact_id,payload_sha256,score_count,scores,updated_at) "
                               "VALUES(?,?,?,?,?,?,?,?,?)", packed_rows)
        result["projection_build_seconds"] = time.perf_counter() - started
        result["wal_bytes_before_checkpoint"] = Path(str(catalog.db_path) + "-wal").stat().st_size
        catalog.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        result["catalog_bytes"] = catalog.db_path.stat().st_size
        result["projection_database_increment_bytes"] = result["catalog_bytes"] - base_size
        result.update(_measure(catalog, processor, count))
        sample = {"schema": store.PAYLOAD_SCHEMA, "model_id": "benchmark-ram-model",
                  "policy_id": "top32-fp16", "input_revision": "1" * 64,
                  "scores": [{"tag_id": tag, "score": 1 - tag / 64,
                              "above_threshold": tag < 16} for tag in range(32)]}
        bytes_per_artifact = len(canonical_json(sample).encode("utf-8"))
        result["illustrative_artifact_json_bytes_each"] = bytes_per_artifact
        result["illustrative_artifact_json_bytes_total"] = bytes_per_artifact * count
        result["illustrative_4k_allocation_bytes_total"] = (
            (bytes_per_artifact + 4095) // 4096 * 4096 * count)
        result["artifact_cost_note"] = "Estimates exclude artifact SQL metadata/NTFS metadata; files were not created."
    return result


def remeasure(output: Path, db_path: Path, count: int) -> dict:
    """Reuse a synthetic benchmark catalog with a strictly read-only connection."""
    if output.exists():
        raise ValueError("use a new output directory to preserve previous evidence")
    output.mkdir(parents=True)
    processor = store.processor_id_for("benchmark-ram-model", "top32-fp16")
    with Catalog(open_readonly(db_path), db_path=db_path) as catalog:
        result = _measure(catalog, processor, count)
    return {"schema": "photo-search-tag-store-readonly-benchmark-v1", "assets": count,
            "catalog": str(db_path), "scope": "read-only existing synthetic catalog", **result}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assets", type=int, default=300_000)
    parser.add_argument("--reuse-catalog", type=Path)
    args = parser.parse_args()
    started = time.perf_counter()
    suite = ET.Element("testsuite", name="tag-store-capacity", tests="1", failures="0")
    case = ET.SubElement(suite, "testcase", name="synthetic_packed_projection")
    try:
        identity = _source_identity()
        result = (remeasure(args.output, args.reuse_catalog, args.assets)
                  if args.reuse_catalog else run(args.output, args.assets))
        result["environment"] = {"python": sys.version, "sqlite": sqlite3.sqlite_version,
            "platform": platform.platform(), "machine": platform.machine(),
            "processor": platform.processor(), "cpu_count": os.cpu_count()}
        if _source_identity() != identity:
            raise RuntimeError("benchmark source changed during measurement; success refused")
        result["source_sha256"] = identity
        result["source_unchanged"] = True
        result["implementation_sha256"] = identity["src/photo_search/tagging/store.py"]
        (args.output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in result.items()
                          if key not in ("lookup_query_plan", "scope", "raw_candidate_lookup_ms", "raw_status_ms", "source_sha256")}, indent=2))
    except BaseException as error:
        suite.set("failures", "1")
        ET.SubElement(case, "failure", message=str(error)).text = repr(error)
        raise
    finally:
        case.set("time", str(time.perf_counter() - started))
        if args.output.exists():
            ET.ElementTree(suite).write(args.output / "junit.xml", encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    main()
