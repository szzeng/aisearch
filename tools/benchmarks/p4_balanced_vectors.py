"""Read-only vector alternatives, isolated synthetic fixtures, three samples.

Original measured source: .tmp/balanced-opt-20261016/vector/experiment.py.
This maintained copy was reorganized after measurement; original evidence retains
the original source hash. --phase all runs three samples per method and scope.

The NumPy path is an experimental in-memory index loaded from the existing Lance
table, not a proposed second persistent backend. Float32 sum-of-squares is an
exhaustive calculation, but its reduction order need not equal Lance's distances.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import numpy as np

from photo_search.catalog.db import Catalog
from photo_search.catalog.sqlite_io import open_readonly
from photo_search.indexing import store
from photo_search.places import cn_facets
from photo_search.search import retrieval, service
from tools.benchmarks.p4_core_candidate_filters import digest, file_digest, original_in
from tools.benchmarks.p4_perf_vectors_300k import DIM, GENERATION, SPACE, memory

REPO = Path(__file__).resolve().parents[2]
AREA = REPO / ".tmp/balanced-vector-reproduction"
ROOT = REPO / ".tmp/perf-300k-all-20261015/search"
VECTOR_ROOT = REPO / ".tmp/perf-300k-cn-20261015/vectors-bench"
DATABASE = ROOT / "data/catalog/catalog.db"
TOP_K = 5000


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def report(name, fn):
    out = AREA / (name + ".json")
    if out.exists() or out.with_suffix(".xml").exists():
        raise RuntimeError("Refusing to overwrite earlier evidence: " + str(out))
    suite = ET.Element("testsuite", name=name)
    result = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "argv": sys.argv,
        "python": sys.version,
        "checks": [],
        "failures": 0,
    }

    def check(label, condition, details=None):
        case = ET.SubElement(suite, "testcase", name=label)
        result["checks"].append({"name": label, "passed": bool(condition), "details": details})
        if not condition:
            result["failures"] += 1
            ET.SubElement(case, "failure", message=label).text = str(details)

    start = time.perf_counter()
    try:
        fn(result, check)
    except BaseException as error:
        result["error"] = traceback.format_exc()
        check("unhandled_exception", False, repr(error))
    finally:
        result["elapsed_ms"] = 1000 * (time.perf_counter() - start)
        result["finished_utc"] = datetime.now(timezone.utc).isoformat()
        result["memory_final"] = memory()
        result["status"] = "FAIL" if result["failures"] else "PASS"
        suite.attrib.update(
            tests=str(len(suite)), failures=str(result["failures"]), errors="0", skipped="0"
        )
        ET.ElementTree(suite).write(out.with_suffix(".xml"), encoding="utf-8", xml_declaration=True)
        dump(out, result)
    print(
        json.dumps(
            {
                "name": name,
                "status": result["status"],
                "elapsed_ms": result["elapsed_ms"],
                "error": result.get("error"),
            }
        ),
        flush=True,
    )
    return int(bool(result["failures"]))


def sources():
    paths = [
        Path(__file__),
        REPO / "src/photo_search/indexing/store.py",
        REPO / "src/photo_search/search/retrieval.py",
        REPO / "src/photo_search/search/service.py",
        REPO / "src/photo_search/catalog/db.py",
        REPO / "src/photo_search/catalog/sqlite_io.py",
        REPO / "src/photo_search/places/cn_facets.py",
        REPO / "pyproject.toml",
        REPO / "uv.lock",
    ]
    return {str(p): file_digest(p) for p in paths}


def inputs():
    paths = [
        ROOT / "fixture.json",
        DATABASE,
        VECTOR_ROOT / "fixture.json",
        VECTOR_ROOT / "vectors.npy",
        *sorted(p for p in (VECTOR_ROOT / "data/vectors").rglob("*") if p.is_file()),
    ]
    wal = DATABASE.with_name(DATABASE.name + "-wal")
    if wal.exists():
        paths.append(wal)
    return {str(p): file_digest(p) for p in paths}


def query():
    q = np.random.default_rng(20261015).standard_normal(DIM).astype(np.float32)
    return q / np.linalg.norm(q)


def open_catalog():
    return Catalog(
        open_readonly(DATABASE, immutable=False), db_path=DATABASE, data_root=ROOT / "data"
    )


def options(name):
    if name == "global":
        return {}
    sql, params = cn_facets.members_sql("city", key="cn:314")
    return {
        "additional_condition_sql": f"a.asset_id IN (SELECT asset_id FROM ({sql}))",
        "additional_condition_parameters": params,
    }


class MatrixIndex:
    def __init__(self, index, generation, space, dimension):
        self.generation, self.space, self.dimension = generation, space, dimension
        before = time.perf_counter()
        table = index._table(generation_id=generation, dimension=dimension, create=False)
        assert table is not None
        # Retain scalar identities/revisions as Arrow columns. Convert the vector
        # column once to contiguous float32; never reload this on timed samples.
        columns = [c for c in store.CANDIDATE_ROW_COLUMNS if c != "_distance"] + ["vector"]
        self.arrow = (
            table.search().select(columns).limit(table.count_rows()).to_arrow().combine_chunks()
        )
        raw = (
            self.arrow["vector"]
            .chunk(0)
            .values.to_numpy(zero_copy_only=False)
            .reshape(-1, dimension)
        )
        raw_ids = self.arrow["asset_id"].to_numpy()
        self.order = np.argsort(raw_ids, kind="stable")
        self.ids = raw_ids[self.order]
        self.matrix = np.ascontiguousarray(raw[self.order], dtype=np.float32)
        self.spaces = np.array(self.arrow["space_id"].to_pylist(), dtype=object)[self.order]
        self.generations = np.array(self.arrow["generation_id"].to_pylist(), dtype=object)[
            self.order
        ]
        self.arrow = self.arrow.drop(["vector"])
        self.load_ms = 1000 * (time.perf_counter() - before)
        self.memory_loaded = memory()

    def rank(self, ids, vector, top_k):
        if not ids:
            return []
        candidates = np.array(ids, dtype=np.int64)
        positions = np.searchsorted(self.ids, candidates)
        valid = positions < len(self.ids)
        positions, candidates = positions[valid], candidates[valid]
        valid = self.ids[positions] == candidates
        positions, candidates = positions[valid], candidates[valid]
        valid = (self.spaces[positions] == self.space) & (
            self.generations[positions] == self.generation
        )
        positions, candidates = positions[valid], candidates[valid]
        # This scratch is the sole candidate-by-dimension allocation per query.
        scratch = np.take(self.matrix, positions, axis=0)
        scratch -= np.asarray(vector, dtype=np.float32)
        distances = np.einsum("ij,ij->i", scratch, scratch, dtype=np.float32, optimize=False)
        order = np.lexsort((candidates, distances))[:top_k]
        hits = []
        for offset in order:
            row = self.arrow.slice(int(self.order[positions[offset]]), 1).to_pylist()[0]
            hits.append(
                {
                    "record_id": row["record_id"],
                    "asset_id": int(row["asset_id"]),
                    "space_id": row["space_id"],
                    "generation_id": row["generation_id"],
                    "projection_revision": int(row["projection_revision"]),
                    "distance": float(distances[offset]),
                    "visible": bool(row["visible"]),
                }
            )
        return hits

    def channel(self, catalog, *, index, binding, query_vector, scope, top_k=TOP_K, **kwargs):
        assert (binding.generation_id, binding.space_id, binding.dimension) == (
            self.generation,
            self.space,
            self.dimension,
        )
        where, params = retrieval.scope_condition(catalog.connection, scope)
        cursor = retrieval._confirmed_candidate_cursor(catalog, binding, where, params)
        try:
            ids = [int(r["asset_id"]) for r in cursor.fetchall()]
        finally:
            cursor.close()
        return {
            "hits": self.rank(ids, query_vector, int(top_k)),
            "blocks": int(bool(ids)),
            "scanned_assets": len(ids),
            "truncated": False,
        }


def blocked_rank(index, ids, q, top_k, block_size, generation, space, dimension):
    rows = []
    fallbacks = 0
    for offset in range(0, len(ids), block_size):
        block = ids[offset : offset + block_size]
        found, resolved = index.search_candidates_bounded(
            generation_id=generation,
            space_id=space,
            dimension=dimension,
            query_vector=q,
            asset_ids=block,
            top_k=top_k,
        )
        if not resolved:
            fallbacks += 1
            found = index.search_candidates(
                generation_id=generation,
                space_id=space,
                dimension=dimension,
                query_vector=q,
                asset_ids=block,
                limit=top_k,
            )
        rows.extend(found)
    return sorted(rows, key=lambda r: (r["distance"], r["asset_id"]))[:top_k], fallbacks


def small(result, check):
    index = store.LanceVectorIndex(AREA / "small-fixture/vectors")
    generation, space, dimension = "small", "small-space", 4
    records = [
        {
            "record_id": f"small:{i}",
            "asset_id": i,
            "space_id": space,
            "generation_id": generation,
            "artifact_revision": "test",
            "projection_revision": 0,
            "vector": [0.0, 0.0, 0.0, 0.0] if i in (1, 211) else [1.0, 0.0, 0.0, 0.0],
            "source_ids": [],
            "visible": True,
        }
        for i in reversed(range(1, 212))
    ]
    index.upsert_batch(
        generation={"generation_id": generation, "space_id": space, "dimension": dimension},
        records=records,
    )
    index._table(generation_id=generation, dimension=dimension, create=False).update(
        where="asset_id = 211", values={"space_id": "foreign"}
    )
    numpy_index = MatrixIndex(index, generation, space, dimension)
    q = [0.0, 0.0, 0.0, 0.0]
    result["scenarios"] = {}
    for name, ids in [
        ("global", list(range(1, 212))),
        ("sparse", list(range(2, 212, 2))),
        ("empty", []),
    ]:
        expected = sorted(
            [(i, 0.0 if i == 1 else 1.0) for i in ids if i <= 210], key=lambda r: (r[1], r[0])
        )[:7]
        item = result["scenarios"][name] = {}
        for method, size in [("blocked", 13), ("full", 1000)]:
            rows, fallbacks = blocked_rank(index, ids, q, 7, size, generation, space, dimension)
            pairs = [(r["asset_id"], r["distance"]) for r in rows]
            item[method] = {"rows": pairs, "fallbacks": fallbacks}
            check(f"{name}_{method}_oracle", pairs == expected)
            if ids:
                check(f"{name}_{method}_cut_tie_fallback", fallbacks > 0)
        rows = numpy_index.rank(ids, q, 7)
        item["numpy"] = [(r["asset_id"], r["distance"]) for r in rows]
        check(f"{name}_numpy_oracle", item["numpy"] == expected)
    check("fixture_retained", index.root.exists())


def prepare(result, check):
    result["source_before"] = sources()
    result["input_before"] = inputs()
    catalog = open_catalog()
    index = store.LanceVectorIndex(VECTOR_ROOT / "data/vectors")
    q = query()
    all_ids = [int(r[0]) for r in catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id")]
    city_ids = [
        int(r[0])
        for r in catalog.read_all(
            "SELECT DISTINCT asset_id FROM cn_admin_resolutions WHERE city_id=? ORDER BY asset_id",
            (314,),
        )
    ]
    check("fixture_cardinality", all_ids == list(range(1, 300001)) and len(city_ids) == 179890)
    rows = []
    for start in range(0, len(all_ids), 10000):
        ids = all_ids[start : start + 10000]
        found = index.search(
            generation_id=GENERATION,
            space_id=SPACE,
            dimension=DIM,
            query_vector=q.tolist(),
            limit=len(ids),
            columns=store.CANDIDATE_ROW_COLUMNS,
            where=original_in(ids) + f" AND space_id = '{SPACE}'",
        )
        assert sorted(r["asset_id"] for r in found) == ids
        rows.extend((r["asset_id"], r["distance"]) for r in found)
    rows.sort(key=lambda r: r[0])
    distances = np.array([r[1] for r in rows], dtype=np.float32)
    matrix = np.load(VECTOR_ROOT / "vectors.npy", mmap_mode="r")
    error = 0.0
    for offset in range(0, len(matrix), 8192):
        delta = matrix[offset : offset + 8192].astype(np.float64) - q.astype(np.float64)
        expected = np.einsum("ij,ij->i", delta, delta)
        error = max(error, float(np.max(np.abs(expected - distances[offset : offset + 8192]))))
    result["numpy_float64_l2_max_abs_error"] = error
    check("all_lance_distances_independent_float64_l2", error < 1e-5)
    np.savez(
        AREA / "oracle.npz",
        ids=np.asarray(all_ids, dtype=np.int64),
        distances=distances,
        city_ids=np.asarray(city_ids, dtype=np.int64),
        query=q,
    )
    table = index._table(generation_id=GENERATION, dimension=DIM, create=False)
    result["scenarios"] = {}
    for name, ids in [("global", all_ids), ("shenzhen", city_ids)]:
        predicate = store._candidate_id_condition(ids)
        got = (
            table.search()
            .where(predicate + f" AND space_id = '{SPACE}'")
            .select(["asset_id"])
            .limit(len(ids) + 1)
            .to_arrow()["asset_id"]
            .to_pylist()
        )
        check(name + "_complete_filter_membership", sorted(got) == ids)
        candidate_d = distances[np.array(ids) - 1]
        order = np.lexsort((ids, candidate_d))
        ranked = [(ids[i], float(candidate_d[i])) for i in order]
        expected_hash = digest(ranked[:TOP_K])
        result["scenarios"][name] = {
            "count": len(ids),
            "predicate_chars": len(predicate),
            "predicate_uses_ranges": "BETWEEN" in predicate,
            "top_k_hash": expected_hash,
            "all_ordered_distance_sha256": digest(ranked),
            "equal_distance_adjacent_pairs": sum(a[1] == b[1] for a, b in zip(ranked, ranked[1:])),
            "boundary_tie_total": sum(r[1] == ranked[TOP_K - 1][1] for r in ranked),
        }
    catalog.close()
    result["oracle_sha256"] = file_digest(AREA / "oracle.npz")
    result["source_after"] = sources()
    result["input_after"] = inputs()
    check("source_identity", result["source_before"] == result["source_after"])
    check("input_identity", result["input_before"] == result["input_after"])


def worker(result, check, method, scenario):
    result.update(
        method=method,
        scenario=scenario,
        source_before=sources(),
        memory_before=memory(),
        runtime_versions={p: version(p) for p in ("lancedb", "numpy", "pyarrow")},
        oracle_sha256=file_digest(AREA / "oracle.npz"),
        layer="service.search_assets, synthetic 512D query; no model/HTTP/UI",
        cache_note="fresh worker; fixture hashing/oracle warmed OS cache; first call is not cold OS startup",
    )
    oracle = np.load(AREA / "oracle.npz")
    ids = oracle["ids"] if scenario == "global" else oracle["city_ids"]
    d = oracle["distances"][ids - 1]
    order = np.lexsort((ids, d))[:TOP_K]
    expected = [(int(ids[i]), float(d[i])) for i in order]
    expected_ids = [r[0] for r in expected]
    allowed = set(ids.tolist())
    catalog = open_catalog()
    index = store.LanceVectorIndex(VECTOR_ROOT / "data/vectors")
    original = service.vector_channel
    matrix_index = None
    if method == "full":

        def full(catalog, **kw):
            return retrieval.vector_channel(catalog, block_size=300000, **kw)

        service.vector_channel = full
    elif method == "reuse":

        def reuse(catalog, **kw):
            bound = kw["binding"]
            original_table = index._table
            table = original_table(
                generation_id=bound.generation_id, dimension=bound.dimension, create=False
            )

            def cached_table(*, generation_id, dimension, create=False):
                if (
                    not create
                    and generation_id == bound.generation_id
                    and dimension == bound.dimension
                ):
                    return table
                return original_table(
                    generation_id=generation_id, dimension=dimension, create=create
                )

            index._table = cached_table
            try:
                return retrieval.vector_channel(catalog, **kw)
            finally:
                index._table = original_table

        service.vector_channel = reuse
    elif method == "numpy":
        matrix_index = MatrixIndex(index, GENERATION, SPACE, DIM)
        result["matrix_load_ms"] = matrix_index.load_ms
        result["matrix_nbytes"] = matrix_index.matrix.nbytes
        result["memory_matrix_loaded"] = matrix_index.memory_loaded
        service.vector_channel = matrix_index.channel
    result["samples"] = []
    result["payload_without_vector_scan_hashes"] = []
    try:
        for repeat in range(-1, 3):
            before = time.perf_counter()
            payload = service.search_assets(
                catalog,
                space_id=SPACE,
                generation_id=GENERATION,
                dimension=DIM,
                vector_index=index,
                query_vector=oracle["query"].tolist(),
                top_k=TOP_K,
                **options(scenario),
            )
            elapsed = 1000 * (time.perf_counter() - before)
            observed = [
                (int(r["asset_id"]), float(r["distance"])) for r in payload["channels"]["vector"]
            ]
            observed_ids = [r[0] for r in observed]
            differences = sum(a != b for a, b in zip(observed_ids, expected_ids))
            observed_map = dict(observed)
            error = max(abs(observed_map[a] - dist) for a, dist in expected if a in observed_map)
            normalized = {k: v for k, v in payload.items() if k != "vector_scan"}
            item = {
                "repeat": repeat,
                "elapsed_ms": elapsed,
                "vector_scan": payload["vector_scan"],
                "ordered_ids_equal": observed_ids == expected_ids,
                "distance_pairs_equal": observed == expected,
                "positions_differing": differences,
                "top_k_symmetric_difference": len(set(observed_ids) ^ set(expected_ids)),
                "max_common_topk_distance_error": error,
                "pairs_sha256": digest(observed),
                "payload_without_vector_scan_sha256": digest(normalized),
                "memory": memory(),
            }
            if repeat < 0:
                result["first"] = item
            else:
                result["samples"].append(item)
            check(
                f"sample_{repeat}_scope_and_cardinality",
                len(observed) == TOP_K
                and set(observed_ids) <= allowed
                and payload["authoritative"]["total"] == len(ids)
                and payload["vector_scan"]["scanned_assets"] == len(ids)
                and not payload["vector_scan"]["truncated"]
                and not payload["pending_semantic"]["entries"],
            )
            check(
                f"sample_{repeat}_deterministic_order",
                observed == sorted(observed, key=lambda r: (r[1], r[0])),
            )
            if method != "numpy":
                check(f"sample_{repeat}_lance_exact_oracle", observed == expected)
            else:
                # Record expected numerical disagreements as experiment results;
                # this PASS means bounded numeric sanity, never Lance equivalence.
                check(f"sample_{repeat}_numpy_numeric_sanity", error < 1e-5)
            result["payload_without_vector_scan_hashes"].append(digest(normalized))
            print(
                f"{method}/{scenario}: sample {repeat}, {elapsed:.2f} ms, different positions {differences}",
                flush=True,
            )
        values = [s["elapsed_ms"] for s in result["samples"]]
        result["summary"] = {
            "n": 3,
            "median_ms": statistics.median(values),
            "min_ms": min(values),
            "max_ms": max(values),
        }
        result["lance_equivalent"] = all(
            s["distance_pairs_equal"] for s in [result["first"], *result["samples"]]
        )
        check(
            "stable_repeated_payload", len(set(result["payload_without_vector_scan_hashes"])) == 1
        )
    finally:
        service.vector_channel = original
        catalog.close()
        result["source_after"] = sources()
        check("source_identity", result["source_before"] == result["source_after"])


def run_all(result, check):
    result["source_before"] = sources()
    result["input_before"] = inputs()
    result["processes"] = []
    for phase, method, scenario in [("small", None, None), ("prepare", None, None)] + [
        ("worker", m, s)
        for s in ("global", "shenzhen")
        for m in ("blocked", "reuse", "full", "numpy")
    ]:
        name = phase if phase != "worker" else f"{method}-{scenario}"
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--phase",
            phase,
            "--output-dir",
            str(AREA),
            "--root",
            str(ROOT),
            "--vector-root",
            str(VECTOR_ROOT),
        ]
        if method:
            command += ["--method", method, "--scenario", scenario]
        before = time.perf_counter()
        completed = subprocess.run(
            command,
            cwd=REPO,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        (AREA / (name + ".log")).write_text(completed.stdout, encoding="utf-8")
        result["processes"].append(
            {
                "name": name,
                "command": command,
                "actual_exit_code": completed.returncode,
                "wall_ms": 1000 * (time.perf_counter() - before),
                "log": str(AREA / (name + ".log")),
            }
        )
        print(completed.stdout[-4000:], flush=True)
        check(name + "_actual_exit", completed.returncode == 0, completed.returncode)
        if completed.returncode and phase != "worker":
            return
    result["comparison"] = {}
    for scenario in ("global", "shenzhen"):
        records = {
            m: json.loads((AREA / f"{m}-{scenario}.json").read_text(encoding="utf-8"))
            for m in ("blocked", "reuse", "full", "numpy")
        }
        base = records["blocked"]["summary"]["median_ms"]
        result["comparison"][scenario] = {
            m: {
                "median_ms": r["summary"]["median_ms"],
                "first_ms": r["first"]["elapsed_ms"],
                "speedup_over_blocked": base / r["summary"]["median_ms"],
                "peak_memory": r["memory_final"],
                "lance_equivalent": r["lance_equivalent"],
                "ordered_ids_equal": r["first"]["ordered_ids_equal"],
                "positions_differing": r["first"]["positions_differing"],
                "top_k_symmetric_difference": r["first"]["top_k_symmetric_difference"],
                "matrix_load_ms": r.get("matrix_load_ms"),
            }
            for m, r in records.items()
        }
        check(
            scenario + "_lance_complete_payload_equivalence_except_scan_blocks",
            records["blocked"]["payload_without_vector_scan_hashes"]
            == records["full"]["payload_without_vector_scan_hashes"],
        )
        check(
            scenario + "_lance_reused_table_complete_payload_equivalence",
            records["blocked"]["payload_without_vector_scan_hashes"]
            == records["reuse"]["payload_without_vector_scan_hashes"],
        )
    result["source_after"] = sources()
    result["input_after"] = inputs()
    check("source_identity", result["source_before"] == result["source_after"])
    check("input_identity", result["input_before"] == result["input_after"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("small", "prepare", "worker", "all"), required=True)
    parser.add_argument("--method", choices=("blocked", "reuse", "full", "numpy"))
    parser.add_argument("--scenario", choices=("global", "shenzhen"))
    parser.add_argument("--output-dir", type=Path, default=AREA)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--vector-root", type=Path, default=VECTOR_ROOT)
    args = parser.parse_args()
    AREA, ROOT, VECTOR_ROOT = (p.resolve() for p in (args.output_dir, args.root, args.vector_root))
    if not all(p.is_relative_to((REPO / ".tmp").resolve()) for p in (AREA, ROOT, VECTOR_ROOT)):
        parser.error("output and synthetic fixture roots must stay in the project .tmp directory")
    DATABASE = ROOT / "data/catalog/catalog.db"
    AREA.mkdir(parents=True, exist_ok=True)
    if args.phase == "worker":
        if not args.method or not args.scenario:
            parser.error("worker requires method and scenario")
        sys.exit(
            report(
                f"{args.method}-{args.scenario}",
                lambda result, check: worker(result, check, args.method, args.scenario),
            )
        )
    sys.exit(report(args.phase, {"small": small, "prepare": prepare, "all": run_all}[args.phase]))
