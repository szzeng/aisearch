"""Synthetic 512D count/health and complete retrieval-service benchmark.

Preparation copies an isolated catalog and adds synthetic metadata/vector rows. It
does not scan photographs, run encoders, publish artifacts, or exercise HTTP/UI.
Run each measurement in a fresh process so peak working sets remain comparable.
The baseline directory must contain the four saved pre-change source modules.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import inspect
import json
import math
import sqlite3
import statistics
import sys
import time
from pathlib import Path

import numpy as np

from photo_search.catalog import image_policy, video
from photo_search.catalog.db import Catalog
from photo_search.catalog.sqlite_io import open_readonly
from photo_search.indexing import selection, store
from photo_search.places import cn_facets
from photo_search.search import retrieval, service
from tools.diagnostics import ann_scale_benchmark as shared

DIM = shared.DIM
SPACE = shared.SPACE_ID
GENERATION = shared.GENERATION_ID
PROCESSOR = shared.PROCESSOR_ID


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def memory() -> dict:
    if sys.platform != "win32":
        return {"peak_working_set_bytes": shared.process_memory_bytes()}
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    query = kernel.K32GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    query.restype = wintypes.BOOL
    if not query(wintypes.HANDLE(kernel.GetCurrentProcess()), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return {"working_set_bytes": int(counters.WorkingSetSize),
            "peak_working_set_bytes": int(counters.PeakWorkingSetSize),
            "private_commit_bytes": int(counters.PagefileUsage),
            "peak_private_commit_bytes": int(counters.PeakPagefileUsage)}


def prepare(root: Path, source: Path, seed: int) -> dict:
    """Build fixture once, outside every timed read-only measurement."""
    root.mkdir(parents=True, exist_ok=True)
    target = root / "data/catalog/catalog.db"
    if target.exists():
        raise ValueError("fixture exists; use measure, or choose a fresh work directory")
    target.parent.mkdir(parents=True)
    started = time.perf_counter()
    with sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)
    # Preserve the source schema for both baseline/current reads: this benchmark
    # isolates vector changes, not the independently measured catalog migrations.
    connection = sqlite3.connect(target)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    count = int(connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0])
    bounds = connection.execute("SELECT MIN(asset_id),MAX(asset_id) FROM assets").fetchone()
    if tuple(bounds) != (1, count):
        raise ValueError("the synthetic fixture must have contiguous ids 1..count")
    connection.execute(
        "INSERT INTO index_generations VALUES(?,?,?,?,'READY',1,1,1)",
        (GENERATION, SPACE, PROCESSOR, DIM),
    )
    for first in range(1, count + 1, 5000):
        stop = min(first + 5000, count + 1)
        connection.executemany(
            "INSERT INTO jobs(subject_type,subject_id,input_revision,task_type,processor_id,"
            "state,priority,attempts,max_attempts,run_after,created_at,updated_at) "
            "VALUES('asset',?,?,'image_embedding',?,'SUCCEEDED',0,1,3,1,1,1)",
            [(str(i), f"synthetic-{i}", PROCESSOR) for i in range(first, stop)],
        )
        jobs = dict(connection.execute(
            "SELECT CAST(subject_id AS INTEGER),job_id FROM jobs WHERE processor_id=? "
            "AND CAST(subject_id AS INTEGER)>=? AND CAST(subject_id AS INTEGER)<?",
            (PROCESSOR, first, stop),
        ))
        connection.executemany(
            "INSERT INTO artifacts(artifact_id,processor_id,input_revision,artifact_type,"
            "artifact_revision,media_type,checksum_sha256,byte_size,storage_uri,space_id,"
            "dimension,job_id,lifecycle_state,created_at,updated_at) "
            "VALUES(?,?,?,'embedding','synthetic.v1','application/json',?,0,?,?,?,?,'PUBLISHED',1,1)",
            [(f"perf-vector-{i}", PROCESSOR, f"synthetic-{i}", "0" * 64,
              f"synthetic-not-written/{i}.json", SPACE, DIM, jobs[i]) for i in range(first, stop)],
        )
        connection.executemany(
            "INSERT INTO index_entries VALUES(?,?,?,?,0,1,'CONFIRMED')",
            [(SPACE, i, GENERATION, f"perf-vector-{i}") for i in range(first, stop)],
        )
        connection.commit()
    assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    connection.close()
    matrix = shared.synthetic_vectors(count, seed=seed, distribution="clustered")
    np.save(root / "vectors.npy", matrix)
    index = store.LanceVectorIndex(root / "data/vectors")
    table = index._table(generation_id=GENERATION, dimension=DIM, create=True)
    _, pa = index._dependencies()
    schema = index._schema(pa, DIM)
    for start in range(0, count, 25_000):
        table.add(shared.arrow_batch(matrix[start:start + 25_000], first_asset=start + 1,
                                     schema=schema, pa=pa))
    scalar = index.ensure_scalar_index(generation_id=GENERATION, dimension=DIM)
    metadata = {
        "count": count, "dimension": DIM, "seed": seed, "distribution": "clustered",
        "source_catalog": str(source.resolve()), "catalog": str(target.resolve()),
        "vectors_sha256": hashlib.sha256(memoryview(matrix).cast("B")).hexdigest(),
        "generation_id": GENERATION, "space_id": SPACE, "scalar_index": scalar,
        "build_ms": (time.perf_counter() - started) * 1000,
        "limitations": "synthetic vectors and artifact metadata; artifact payload files absent; "
                        "no photo decode, model inference, artifact verification, HTTP or browser",
    }
    source_fixture = source.resolve().parents[2] / "fixture.json"
    if source_fixture.exists():
        metadata["shenzhen_key"] = json.loads(source_fixture.read_text(encoding="utf-8")).get(
            "shenzhen_key"
        )
    (root / "fixture.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def measure(root: Path, baseline: Path, revision: str, scenario: str, samples: int,
            output: Path, *, trace_merge: bool = False) -> dict:
    fixture = json.loads((root / "fixture.json").read_text(encoding="utf-8"))
    index_module, health_module, retrieval_module, policy_module = store, selection, retrieval, image_policy
    if revision == "baseline":
        index_module = load(baseline / "indexing_store.py", "baseline_vector_store")
        health_module = load(baseline / "indexing_selection.py", "baseline_vector_selection")
        retrieval_module = load(baseline / "search_retrieval.py", "baseline_vector_retrieval")
        policy_module = load(baseline / "image_policy.py", "baseline_vector_image_policy")
    index = index_module.LanceVectorIndex(root / "data/vectors")
    db = root / "data/catalog/catalog.db"
    catalog = Catalog(open_readonly(db, immutable=False), db_path=db, data_root=root / "data")
    args = dict(generation_id=GENERATION, space_id=SPACE, dimension=DIM)
    # The old health function imports the adapter inside the call. Both revisions
    # must use their matching saved adapter, even for a small smoke fixture.
    original_index = store.LanceVectorIndex
    original_channel = service.vector_channel
    original_image_location_sql = image_policy.image_location_sql
    original_policy_photo_sql = image_policy.photo_asset_sql
    original_video_photo_sql = video.photo_asset_sql
    original_retrieval_photo_sql = retrieval.photo_asset_sql
    store.LanceVectorIndex = index_module.LanceVectorIndex
    service.vector_channel = retrieval_module.vector_channel
    # photo_asset_sql is already imported by retrieval/service; it resolves this
    # global at call time, so loading an old retrieval module alone is insufficient.
    image_policy.image_location_sql = policy_module.image_location_sql
    image_policy.photo_asset_sql = policy_module.photo_asset_sql
    video.photo_asset_sql = policy_module.photo_asset_sql
    retrieval.photo_asset_sql = policy_module.photo_asset_sql
    retrieval_module.photo_asset_sql = policy_module.photo_asset_sql
    rng = np.random.default_rng(20261015)
    query = rng.standard_normal(DIM).astype(np.float32)
    query /= np.linalg.norm(query)
    query_list = query.tolist()
    if scenario == "count":
        call = lambda: index.count(**args)
    elif scenario == "health":
        call = lambda: health_module.lance_health(index.root, **args, expected_rows=fixture["count"])
    elif scenario in {"service", "service_shenzhen"}:
        scope_args = {}
        if scenario == "service_shenzhen":
            if not fixture.get("shenzhen_key"):
                raise ValueError("fixture has no Shenzhen administrative key")
            membership, parameters = cn_facets.members_sql("city", key=fixture["shenzhen_key"])
            scope_args = {
                "additional_condition_sql": f"a.asset_id IN (SELECT asset_id FROM ({membership}))",
                "additional_condition_parameters": parameters,
            }
        call = lambda: service.search_assets(catalog, **args, vector_index=index,
                                             query_vector=query_list, top_k=5000, **scope_args)
    else:
        raise ValueError(scenario)
    result = {
        "revision": revision, "scenario": scenario, "fixture": fixture,
        "layer": "count/health adapter or complete search_assets service; "
                 "precomputed synthetic image-query vector; no encoder, HTTP or UI",
        "first_call_note": "first observed call in this process; OS cache is not purged; not disk-cold",
        "background_load": "other isolated map/timeline read-only benchmarks may run concurrently; "
                           "diagnostic measurements, not an isolated SLA acceptance",
        "catalog_schema_version": catalog.schema_version,
        "code_sha256": {
            label: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for label, module in (("store", index_module), ("selection", health_module),
                                  ("retrieval", retrieval_module), ("service", service),
                                  ("image_policy", policy_module))
        },
        "machine": shared.machine_facts(), "memory_before": memory(),
        "samples_requested": samples, "errors": [], "samples_ms": [], "hashes": [],
        "sample_started_unix_ms": [],
    }
    previous_trace = sys.gettrace()
    if trace_merge:
        lines, start_line = inspect.getsourcelines(retrieval_module.vector_channel)
        boundary = next(start_line + offset for offset, line in enumerate(lines)
                        if line.strip() == "if len(ids) < int(block_size):")
        merge_code = retrieval_module.vector_channel.__code__
        metrics = {"max_merge_entries": 0, "max_retained_after_block": 0, "max_block_hits": 0}

        def trace(frame, event, arg):
            if frame.f_code is not merge_code:
                return None
            if event == "line":
                state = frame.f_locals
                metrics["max_merge_entries"] = max(metrics["max_merge_entries"],
                                                     len(state.get("merged", {})))
                if frame.f_lineno == boundary:
                    metrics["max_retained_after_block"] = max(
                        metrics["max_retained_after_block"], len(state.get("merged", {})))
                    metrics["max_block_hits"] = max(metrics["max_block_hits"],
                                                     len(state.get("hits", [])))
            return trace

        result["merge_instrumentation"] = metrics
        result["instrumentation_note"] = "Python line tracing; timings are NOT performance evidence"
        sys.settrace(trace)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        for repeat in range(samples + 1):
            result["sample_started_unix_ms"].append(time.time_ns() // 1_000_000)
            started = time.perf_counter()
            try:
                value = call()
                elapsed = (time.perf_counter() - started) * 1000
                digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
                if repeat == 0:
                    result["first_ms"] = elapsed
                    result["first_hash"] = digest
                    result["observation"] = (
                        {"vector_scan": value["vector_scan"], "hits": len(value["hits"]),
                         "state": value["state"]} if scenario.startswith("service") else value
                    )
                else:
                    result["samples_ms"].append(elapsed)
                    result["hashes"].append(digest)
                del value
            except Exception as error:
                result["errors"].append({"repeat": repeat, "type": type(error).__name__,
                                         "message": str(error)})
                break
            result["memory_after"] = memory()
            output.write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(f"{revision}/{scenario}: {repeat}/{samples} {elapsed:.1f} ms", flush=True)
        timings = result["samples_ms"]
        if timings:
            result["summary"] = {
                "n": len(timings), "median_ms": statistics.median(timings),
                "p95_ms": sorted(timings)[math.ceil(len(timings) * 0.95) - 1],
                "max_ms": max(timings), "stable": len(set(result["hashes"])) == 1,
                "all_match_first": all(h == result["first_hash"] for h in result["hashes"]),
            }
        return result
    finally:
        sys.settrace(previous_trace)
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        service.vector_channel = original_channel
        store.LanceVectorIndex = original_index
        image_policy.image_location_sql = original_image_location_sql
        image_policy.photo_asset_sql = original_policy_photo_sql
        video.photo_asset_sql = original_video_photo_sql
        retrieval.photo_asset_sql = original_retrieval_photo_sql
        catalog.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "measure"))
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--source-catalog", type=Path)
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--seed", type=int, default=20261015)
    parser.add_argument("--revision", choices=("baseline", "current"), default="current")
    parser.add_argument("--scenario", choices=("count", "health", "service", "service_shenzhen"),
                        default="service")
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--trace-merge", action="store_true")
    args = parser.parse_args()
    if args.mode == "prepare":
        if args.source_catalog is None:
            parser.error("prepare requires --source-catalog")
        result = prepare(args.work_dir, args.source_catalog, args.seed)
    else:
        if args.samples < 1 or args.output is None or args.baseline_dir is None:
            parser.error("measure requires --samples >=1, --output and --baseline-dir")
        result = measure(args.work_dir, args.baseline_dir, args.revision, args.scenario,
                         args.samples, args.output, trace_merge=args.trace_merge)
    print(json.dumps({key: value for key, value in result.items()
                      if key not in {"samples_ms", "hashes", "fixture"}}, indent=2))
    if result.get("errors"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
