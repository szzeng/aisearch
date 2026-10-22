"""CPU-only vector capacity experiment; not a production ANN implementation.

Reuse frozen Qwen native vectors and the existing synthetic Shenzhen membership.
The 500 real vectors plus seeded noisy clusters are a search-engine stress shape,
not 300,000 real photographs or evidence of semantic quality. Execute each mode
in a fresh process. Output JSON and JUnit preserve failures and refuse overwrite.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sqlite3
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from importlib.metadata import version
from pathlib import Path

import numpy as np

from photo_search.indexing import store
from photo_search.places import cn_facets
from tools.benchmarks.p4_balanced_vectors import blocked_rank
from tools.benchmarks.p4_perf_vectors_300k import memory
from tools.evaluation.vector_quality_metrics import prefix, read_vectors

REPO = Path(__file__).resolve().parents[2]
GENERATION = "capacity-experiment"
SPACE = "capacity-native-prefix"
SEED = 20261021
SCOPES = ("global", "shenzhen", "sparse")
ANN_CONFIG = {"type": "IVF_PQ", "partitions": 256, "sub_vectors": 64,
              "max_iterations": 20, "sample_rate": 64, "metric": "l2"}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, payload):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)


def local_root(path):
    path = Path(path).resolve()
    if not path.is_relative_to(REPO / ".tmp"):
        raise ValueError("experiment output must stay below the workspace .tmp")
    return path


def synthetic_batches(heads, rows, dimension, seed=SEED, batch=5000):
    """Same native-coordinate draw for every dimension; never use class labels."""
    if dimension not in (512, 1024, 2048) or rows < len(heads) or batch < 1:
        raise ValueError("invalid dimension, row count or batch size")
    rng = np.random.default_rng(seed)
    native = prefix(heads, 2048)
    for first in range(0, rows, batch):
        count = min(batch, rows - first)
        noise = rng.standard_normal((count, 2048), dtype=np.float32)
        noise /= np.linalg.norm(noise, axis=1, keepdims=True)
        anchors = rng.integers(0, len(native), count)
        mix = rng.random(count) < 0.75
        noise[mix] = native[anchors[mix]] + 0.35 * noise[mix]
        if first < len(native):
            stop = min(count, len(native) - first)
            noise[:stop] = native[first:first + stop]
        yield first, prefix(noise, dimension)


def scope_ids(root, scope):
    fixture = load(root / "fixture.json")
    if scope not in SCOPES:
        raise ValueError("unknown scope")
    ids = np.load(root / f"{scope}-ids.npy", allow_pickle=False)
    if ids.dtype != np.int64 or ids.ndim != 1 or np.any(np.diff(ids) <= 0):
        raise ValueError("membership must be unique ascending int64 IDs")
    if len(ids) and (ids[0] < 1 or ids[-1] > fixture["rows"]):
        raise ValueError("membership outside fixture")
    if digest(root / f"{scope}-ids.npy") != fixture["files"][f"{scope}-ids.npy"]:
        raise ValueError("membership changed")
    return ids


def disk_bytes(path):
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def validate_fixture(root):
    fixture = load(root / "fixture.json")
    for filename, expected in fixture["files"].items():
        if Path(filename).name != filename or digest(root / filename) != expected:
            raise ValueError("fixture vectors or membership changed")
    return fixture


def prepare(args, result):
    import lancedb
    import pyarrow as pa

    root = args.root
    if (root / "fixture.json").exists() or (root / "vectors").exists():
        raise ValueError("fixture already exists; choose a fresh root")
    run = load(args.source_run / "result.json")
    progress = load(args.source_run / "progress.json")
    if run["status"] != "COMPLETE" or run["cache_identity"] != progress["cache_identity"]:
        raise ValueError("source run incomplete or identity mismatch")
    heads = read_vectors(args.source_run, progress["images"], 500)
    queries = read_vectors(args.source_run, progress["queries"], 12)[:8]
    queries = prefix(queries, args.dimension)
    source_hash = digest(args.catalog)
    with sqlite3.connect(args.catalog.resolve().as_uri() + "?mode=ro", uri=True) as catalog:
        count = catalog.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        if count != 300000:
            raise ValueError("expected the existing 300000-row synthetic CN catalog")
        sql, parameters = cn_facets.members_sql("city", key="cn:314")
        city = sorted({int(row[0]) for row in catalog.execute(sql, parameters)
                       if int(row[0]) <= args.rows})
    if digest(args.catalog) != source_hash:
        raise ValueError("synthetic source catalog changed")
    ids = np.arange(1, args.rows + 1, dtype=np.int64)
    memberships = {"global": ids, "shenzhen": np.array(city, dtype=np.int64),
                   "sparse": ids[ids % 97 == 0]}
    for name, values in memberships.items():
        np.save(root / f"{name}-ids.npy", values)
    np.save(root / "queries.npy", queries)
    schema = store.LanceVectorIndex._schema(pa, args.dimension)
    schema = schema.append(pa.field("shenzhen", pa.bool_(), nullable=False))
    schema = schema.append(pa.field("sparse", pa.bool_(), nullable=False))
    db = lancedb.connect(str(root / "vectors"))
    table = db.create_table(store._table_name(GENERATION), schema=schema)
    matrix = np.lib.format.open_memmap(root / "vectors.npy", mode="w+", dtype=np.float32,
                                       shape=(args.rows, args.dimension))
    before = time.perf_counter()
    for first, block in synthetic_batches(heads, args.rows, args.dimension):
        count = len(block)
        batch_ids = np.arange(first + 1, first + count + 1, dtype=np.int64)
        matrix[first:first + count] = block
        arrays = [pa.array([f"capacity:{i}" for i in batch_ids]), pa.array(batch_ids),
                  pa.array([GENERATION] * count), pa.array([SPACE] * count),
                  pa.array(["synthetic-v1"] * count), pa.array(np.zeros(count, dtype=np.int64)),
                  pa.FixedSizeListArray.from_arrays(pa.array(block.reshape(-1)), args.dimension),
                  pa.array([[]] * count, type=pa.list_(pa.int64())),
                  pa.array(np.ones(count, dtype=bool)),
                  pa.array(np.isin(batch_ids, memberships["shenzhen"])),
                  pa.array(batch_ids % 97 == 0)]
        table.add(pa.Table.from_arrays(arrays, schema=schema))
    matrix.flush()
    del matrix
    table.create_scalar_index("asset_id", index_type="BTREE")
    fixture = {
        "schema": "budget-vector-capacity-v1", "rows": args.rows, "dimension": args.dimension,
        "seed": SEED, "batch_size": 5000, "real_heads": 500,
        "distribution": "75% unit-real-anchor + 0.35 unit-noise; 25% unit-noise; prefix L2",
        "source_run": str(args.source_run), "source_identity": run["cache_identity"],
        "source_result_sha256": digest(args.source_run / "result.json"),
        "source_progress_sha256": digest(args.source_run / "progress.json"),
        "membership_catalog": str(args.catalog), "membership_catalog_sha256": source_hash,
        "member_counts": {name: len(values) for name, values in memberships.items()},
        "generation": GENERATION, "space": SPACE, "lance_version": table.version,
        "build_ms": 1000 * (time.perf_counter() - before),
        "lance_bytes": disk_bytes(root / "vectors"),
        "raw_vector_bytes": args.rows * args.dimension * 4,
        "files": {p.name: digest(p) for p in root.glob("*.npy")},
    }
    save(root / "fixture.json", fixture)
    result["fixture"] = fixture


def inspect_rows(rows, ids, matrix, query):
    allowed = set(int(i) for i in ids)
    selected = [int(r["asset_id"]) for r in rows]
    if len(set(selected)) != len(selected) or not set(selected) <= allowed:
        raise ValueError("duplicate or unauthorized result")
    if any(r["generation_id"] != GENERATION or r["space_id"] != SPACE for r in rows):
        raise ValueError("foreign vector space/generation")
    distances = np.array([float(r.get("distance", r.get("_distance"))) for r in rows])
    if not np.isfinite(distances).all():
        raise ValueError("non-finite distance")
    if selected:
        differences = matrix[np.array(selected) - 1] - query
        expected = np.einsum("ij,ij->i", differences, differences)
        if not np.allclose(distances, expected, atol=2e-6, rtol=2e-6):
            raise ValueError("distance not from original vectors")
    return selected, distances


def stable(rows):
    return sorted(rows, key=lambda r: (float(r.get("distance", r.get("_distance"))),
                                       int(r["asset_id"])))


def exact_once(table, query, scope, wanted, eligible_count):
    """Exhaustive distance scan with bounded output, preserving boundary ties."""
    condition = f"space_id = '{SPACE}' AND generation_id = '{GENERATION}'"
    if scope != "global":
        condition += f" AND {scope} = true"
    build = lambda limit: (table.search(query).distance_type("l2").bypass_vector_index()
                            .where(condition, prefilter=True)
                            .select(list(store.CANDIDATE_ROW_COLUMNS)).limit(limit))
    rows = stable(build(wanted + 1).to_arrow().to_pylist())
    tie = len(rows) > wanted and rows[wanted]["_distance"] == rows[wanted - 1]["_distance"]
    if tie:
        rows = stable(build(eligible_count).to_arrow().to_pylist())
    return rows[:wanted], tie


def summarize(samples):
    times = [row["elapsed_ms"] for row in samples]
    return {"samples": len(times), "median_ms": float(np.median(times)),
            "min_ms": min(times), "max_ms": max(times)}


def measure_exact(args, result):
    fixture = validate_fixture(args.root)
    dimension = fixture["dimension"]
    matrix = np.load(args.root / "vectors.npy", mmap_mode="r", allow_pickle=False)
    queries = np.load(args.root / "queries.npy", allow_pickle=False)
    index = store.LanceVectorIndex(args.root / "vectors")
    table = index._table(generation_id=GENERATION, dimension=dimension, create=False)
    if any("vector" in x.columns for x in table.list_indices()):
        raise ValueError("production exact baseline must precede ANN construction")
    if table.version != fixture["lance_version"] or table.count_rows() != fixture["rows"]:
        raise ValueError("Lance fixture changed")
    table.checkout(table.version)
    result["fixture_sha256"] = digest(args.root / "fixture.json")
    query_indices = [0, 3, 5, 1, 2, 4, 6, 7][:args.query_count]
    result["query_indices"] = query_indices
    result["scenarios"] = {}
    oracle = {}
    for scope in SCOPES:
        ids = scope_ids(args.root, scope)
        rows = result["scenarios"][scope] = {"candidates": len(ids), "single": [], "blocked": []}
        for i in query_indices:
            query = queries[i]
            started = time.perf_counter()
            direct, tie = exact_once(table, query, scope, args.top_k, len(ids))
            elapsed = 1000 * (time.perf_counter() - started)
            ordered, distances = inspect_rows(direct, ids, matrix, query)
            if len(ordered) != min(args.top_k, len(ids)):
                raise ValueError("exact query returned incomplete membership")
            rows["single"].append({"query": i, "elapsed_ms": elapsed, "tie_fallback": tie})
            oracle[f"{scope}:{i}:ids"] = np.array(ordered, dtype=np.int64)
            oracle[f"{scope}:{i}:distances"] = distances
            started = time.perf_counter()
            blocked, fallbacks = blocked_rank(index, ids.tolist(), query.tolist(), args.top_k,
                                              15000, GENERATION, SPACE, dimension)
            elapsed = 1000 * (time.perf_counter() - started)
            got, actual = inspect_rows(blocked, ids, matrix, query)
            if got != ordered or not np.array_equal(actual, distances):
                raise ValueError("single-scan/production-block full TopK differs")
            rows["blocked"].append({"query": i, "elapsed_ms": elapsed,
                                    "tie_fallbacks": fallbacks})
            print(f"exact {dimension}/{scope}/{i}: single {rows['single'][-1]['elapsed_ms']:.1f}ms",
                  flush=True)
        rows["summary"] = {name: summarize(rows[name]) for name in ("single", "blocked")}
    if (args.root / "exact-oracle.npz").exists():
        raise ValueError("oracle already exists")
    np.savez(args.root / "exact-oracle.npz", **oracle)
    result["oracle_sha256"] = digest(args.root / "exact-oracle.npz")
    result["lance_bytes"] = disk_bytes(args.root / "vectors")


def ann_snapshot(root, table, fixture):
    """Bind comparisons to source bytes, one table version, and its physical index."""
    indexes = [index for index in table.list_indices() if index.name == "vector_idx"]
    if len(indexes) != 1 or indexes[0].columns != ["vector"]:
        raise ValueError("required vector index is missing or targets another column")
    index = indexes[0]
    stats = table.index_stats("vector_idx")
    if (index.index_type != "IvfPq" or stats.index_type != "IVF_PQ"
            or stats.distance_type != "l2" or stats.num_indexed_rows != fixture["rows"]
            or stats.num_unindexed_rows != 0 or table.count_rows() != fixture["rows"]):
        raise ValueError("ANN index type, metric or complete fixture coverage differs")
    return {
        "fixture_sha256": digest(root / "fixture.json"),
        "exact_sha256": digest(root / "exact.json"),
        "oracle_sha256": digest(root / "exact-oracle.npz"),
        "table_version": table.version, "table_rows": table.count_rows(),
        "index": {"name": index.name, "type": index.index_type, "columns": index.columns,
                  "uuid": str(index.index_uuid), "details": index.index_details,
                  "indexed_rows": stats.num_indexed_rows,
                  "unindexed_rows": stats.num_unindexed_rows, "metric": stats.distance_type},
    }


def validate_ann_origin(previous, current_snapshot, *, top_k, query_indices):
    if (previous.get("status") != "PASS" or previous.get("top_k") != top_k
            or previous.get("query_indices") != query_indices
            or previous.get("config") != ANN_CONFIG):
        raise ValueError("valid matching original ANN execution required")
    if not previous.get("snapshot"):
        raise ValueError("original ANN run lacks snapshot identity; legacy evidence needs separate review")
    if previous["snapshot"] != current_snapshot:
        raise ValueError("ANN fixture, oracle, table version or index identity changed")


def measure_ann(args, result):
    fixture = validate_fixture(args.root)
    dimension = fixture["dimension"]
    if dimension != 1024:
        raise ValueError("bounded ANN pilot is restricted to 1024 dimensions")
    matrix = np.load(args.root / "vectors.npy", mmap_mode="r", allow_pickle=False)
    queries = np.load(args.root / "queries.npy", allow_pickle=False)
    baseline = load(args.root / "exact.json")
    if baseline["status"] != "PASS" or baseline["fixture_sha256"] != digest(args.root / "fixture.json"):
        raise ValueError("exact reference incomplete or mismatched")
    if baseline["top_k"] != args.top_k:
        raise ValueError("ANN and exact reference top-k differ")
    result["query_indices"] = baseline["query_indices"]
    if baseline["oracle_sha256"] != digest(args.root / "exact-oracle.npz"):
        raise ValueError("exact oracle changed")
    oracle = np.load(args.root / "exact-oracle.npz", allow_pickle=False)
    index = store.LanceVectorIndex(args.root / "vectors")
    table = index._table(generation_id=GENERATION, dimension=dimension, create=False)
    if args.mode == "ann-diagnose":
        previous = load(args.root / "ann.json")
        snapshot = ann_snapshot(args.root, table, fixture)
        validate_ann_origin(previous, snapshot, top_k=args.top_k,
                            query_indices=baseline["query_indices"])
        result["original_ann_sha256"] = digest(args.root / "ann.json")
        result["build_ms"] = None
    else:
        if any("vector" in x.columns for x in table.list_indices()):
            raise ValueError("ANN already exists; do not silently rebuild evidence")
        if table.version != fixture["lance_version"] or table.count_rows() != fixture["rows"]:
            raise ValueError("Lance fixture changed")
        result["lance_bytes_before"] = disk_bytes(args.root / "vectors")
        started = time.perf_counter()
        table.create_index(metric="l2", index_type="IVF_PQ", num_partitions=256,
                           num_sub_vectors=64, max_iterations=20, sample_rate=64)
        result["build_ms"] = 1000 * (time.perf_counter() - started)
        snapshot = ann_snapshot(args.root, table, fixture)
    result["config"] = dict(ANN_CONFIG)
    result["snapshot"] = snapshot
    table.checkout(snapshot["table_version"])
    result["index_stats"] = str(table.index_stats("vector_idx"))
    result["lance_bytes_after"] = disk_bytes(args.root / "vectors")
    result["scenarios"] = {}
    for probes in ((256,) if args.mode == "ann-diagnose" else (16, 64)):
        for scope in SCOPES:
            ids = scope_ids(args.root, scope)
            condition = f"space_id = '{SPACE}' AND generation_id = '{GENERATION}'"
            if scope != "global":
                condition += f" AND {scope} = true"
            samples = result["scenarios"][f"{scope}:nprobe{probes}"] = []
            for i in baseline["query_indices"]:
                query = queries[i]
                started = time.perf_counter()
                builder = (table.search(query).distance_type("l2").nprobes(probes).refine_factor(4)
                           .where(condition, prefilter=True).select(list(store.CANDIDATE_ROW_COLUMNS))
                           .limit(args.top_k))
                found = stable(builder.to_arrow().to_pylist())
                elapsed = 1000 * (time.perf_counter() - started)
                query_object = builder.to_query_object()
                effective = {name: getattr(query_object, name, None) for name in
                             ("minimum_nprobes", "maximum_nprobes", "refine_factor")}
                if effective != {"minimum_nprobes": probes, "maximum_nprobes": probes,
                                 "refine_factor": 4}:
                    raise ValueError("engine ignored configured ANN work bounds")
                got, _ = inspect_rows(found, ids, matrix, query)
                expected = oracle[f"{scope}:{i}:ids"].tolist()
                samples.append({"query": i, "elapsed_ms": elapsed, "returned": len(got),
                                "effective_query_settings": effective,
                                "recall100": len(set(got[:100]) & set(expected[:100]))
                                / min(100, len(expected)),
                                "recall_topk": len(set(got) & set(expected)) / len(expected)})
                print(f"ann {scope}/{probes}/{i}: {elapsed:.1f}ms R100={samples[-1]['recall100']:.3f}",
                      flush=True)
    result["summary"] = {key: {**summarize(values),
                                     "mean_recall100": float(np.mean([x["recall100"] for x in values])),
                                     "min_recall100": min(x["recall100"] for x in values),
                                     "mean_recall_topk": float(np.mean([x["recall_topk"] for x in values]))}
                         for key, values in result["scenarios"].items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "exact", "ann", "ann-diagnose"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-run", type=Path, default=REPO / ".tmp/wemm-pilot/runs/full/qwen")
    parser.add_argument("--catalog", type=Path, default=REPO /
                        ".tmp/perf-300k-cn-20261015/vectors-bench/data/catalog/catalog.db")
    parser.add_argument("--dimension", type=int, choices=(512, 1024, 2048), default=1024)
    parser.add_argument("--rows", type=int, default=300000)
    parser.add_argument("--top-k", type=int, default=5000)
    parser.add_argument("--query-count", type=int, choices=range(1, 9), default=3,
                        help="bounded order: cat, car, person, dog, bird, bicycle, chair, teddy")
    args = parser.parse_args(argv)
    args.root = local_root(args.root)
    if not 500 <= args.rows <= 300000 or not 1 <= args.top_k <= 5000:
        parser.error("rows must be 500..300000 and top-k 1..5000")
    args.root.mkdir(parents=True, exist_ok=True)
    temporary = args.root / "tmp"
    temporary.mkdir(exist_ok=True)
    os.environ["TEMP"] = os.environ["TMP"] = str(temporary)
    target = args.root / f"{args.mode}.json"
    if target.exists() or target.with_suffix(".xml").exists():
        raise ValueError("refusing to overwrite execution evidence")
    result = {"mode": args.mode, "argv": sys.argv, "python": sys.version,
              "host": platform.platform(), "numpy": version("numpy"),
              "lancedb": version("lancedb"), "pyarrow": version("pyarrow"),
              "source_sha256": digest(__file__), "memory_before": memory(),
              "timing_scope": "CPU vector-engine only, first calls not disk cold; no model/API/UI",
              "limitations": "synthetic noisy clusters; no semantic-quality or GTX1660 certification",
              "top_k": args.top_k, "status": "FAIL",
              "thread_env": {key: os.environ.get(key) for key in
                             ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "RAYON_NUM_THREADS")},
              "related_source_sha256": {str(path.relative_to(REPO)): digest(path) for path in
                  [REPO / "src/photo_search/indexing/store.py",
                   REPO / "tools/benchmarks/p4_balanced_vectors.py",
                   REPO / "tools/benchmarks/p4_perf_vectors_300k.py",
                   REPO / "tools/evaluation/vector_quality_metrics.py"]}}
    started = time.perf_counter()
    try:
        {"prepare": prepare, "exact": measure_exact, "ann": measure_ann,
         "ann-diagnose": measure_ann}[args.mode](args, result)
        if result["source_sha256"] != digest(__file__):
            raise ValueError("source changed while running")
        if any(digest(REPO / path) != value
               for path, value in result["related_source_sha256"].items()):
            raise ValueError("related implementation changed while running")
        result["status"] = "PASS"
    except Exception:
        result["error"] = traceback.format_exc()
    result.update(elapsed_ms=1000 * (time.perf_counter() - started), memory_after=memory(),
                  actual_exit=0 if result["status"] == "PASS" else 1)
    save(target, result)
    suite = ET.Element("testsuite", name=f"budget-vector-{args.mode}", tests="1",
                       failures=str(result["actual_exit"]), errors="0", skipped="0")
    case = ET.SubElement(suite, "testcase", name="execution_and_invariants")
    if result["actual_exit"]:
        ET.SubElement(case, "failure", message="execution failed").text = result["error"]
    ET.ElementTree(suite).write(target.with_suffix(".xml"), encoding="utf-8", xml_declaration=True)
    print(json.dumps({k: result[k] for k in ("status", "elapsed_ms", "actual_exit")}))
    if result.get("error"):
        print(result["error"], file=sys.stderr)
    return result["actual_exit"]


if __name__ == "__main__":
    raise SystemExit(main())
