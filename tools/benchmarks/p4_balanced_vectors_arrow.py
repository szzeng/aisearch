"""Additional pilot: exhaustive Lance distances, Arrow authority selection.

Original measured source: .tmp/balanced-opt-20261016/vector/arrow_experiment.py.
Run p4_balanced_vectors.py --phase all first with the same --output-dir to prepare
the distance oracle. This maintained copy was reorganized after measurement;
original evidence retains the original source hash.

No production changes or persistent cache. Count and exhaustive search use one
explicitly checked-out read version. Three warm samples, process-local timings.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from tools.benchmarks import p4_balanced_vectors as shared

AREA = shared.AREA


def arrow_rank(
    index,
    *,
    generation_id,
    space_id,
    dimension,
    query_vector,
    asset_ids,
    top_k,
    stages,
    after_count=None,
):
    if not asset_ids:
        return []
    before = time.perf_counter()
    vector = index._query_vector(dimension=dimension, query_vector=query_vector)
    table = index._table(generation_id=generation_id, dimension=dimension, create=False)
    if table is None:
        return []
    snapshot_version = table.version
    table.checkout(snapshot_version)
    stages["open_snapshot_ms"] = 1000 * (time.perf_counter() - before)
    before = time.perf_counter()
    condition = f"space_id = {shared.store._lance_literal(space_id)}"
    physical_count = table.count_rows()
    space_count = table.count_rows(condition)
    stages.update(
        physical_rows=physical_count,
        space_rows=space_count,
        version=snapshot_version,
        count_ms=1000 * (time.perf_counter() - before),
    )
    if not space_count:
        return []
    if after_count:
        after_count()
    before = time.perf_counter()
    all_rows = (
        table.search(vector)
        .bypass_vector_index()
        .where(condition)
        .select(list(shared.store.CANDIDATE_ROW_COLUMNS))
        .limit(physical_count)
        .to_arrow()
    )
    stages["exhaustive_arrow_ms"] = 1000 * (time.perf_counter() - before)
    stages["arrow_nbytes"] = all_rows.nbytes
    if table.version != snapshot_version or all_rows.num_rows != space_count:
        raise RuntimeError("Exhaustive distance projection did not cover its pinned version")
    before = time.perf_counter()
    selected = all_rows.filter(
        pc.is_in(all_rows["asset_id"], value_set=pa.array(asset_ids, type=pa.int64()))
    )
    stages["selected_rows"] = selected.num_rows
    for field, expected in [("generation_id", generation_id), ("space_id", space_id)]:
        if (
            selected.num_rows
            and not pc.all(pc.fill_null(pc.equal(selected[field], expected), False)).as_py()
        ):
            raise RuntimeError("Foreign identity in selected Arrow rows: " + field)
    if (
        selected.num_rows
        and not pc.all(pc.fill_null(pc.is_finite(selected["_distance"]), False)).as_py()
    ):
        raise RuntimeError("Non-finite selected distance")
    ranked = selected.sort_by([("_distance", "ascending"), ("asset_id", "ascending")]).slice(
        0, top_k
    )
    stages["authority_selection_sort_ms"] = 1000 * (time.perf_counter() - before)
    before = time.perf_counter()
    hits = [
        {
            "record_id": str(r["record_id"]),
            "asset_id": int(r["asset_id"]),
            "space_id": str(r["space_id"]),
            "generation_id": str(r["generation_id"]),
            "projection_revision": int(r["projection_revision"]),
            "distance": float(r["_distance"]),
            "visible": bool(r["visible"]),
        }
        for r in ranked.to_pylist()
    ]
    stages["python_topk_ms"] = 1000 * (time.perf_counter() - before)
    stages["python_rows"] = len(hits)
    return hits


def small(result, check):
    index = shared.store.LanceVectorIndex(AREA / "arrow-small-fixture/vectors")
    generation, space, dimension = "arrow-small", "small-space", 4

    def record(i):
        return {
            "record_id": f"small:{i}",
            "asset_id": i,
            "generation_id": generation,
            "space_id": space,
            "artifact_revision": "test",
            "projection_revision": 0,
            "vector": [0.0, 0.0, 0.0, 0.0] if i == 1 else [1.0, 0.0, 0.0, 0.0],
            "source_ids": [],
            "visible": True,
        }

    generation_row = {"generation_id": generation, "space_id": space, "dimension": dimension}
    index.upsert_batch(
        generation=generation_row, records=[record(i) for i in reversed(range(1, 212))]
    )
    index._table(generation_id=generation, dimension=dimension, create=False).update(
        where="asset_id = 211", values={"space_id": "foreign"}
    )
    result["scenarios"] = {}
    q = [0.0, 0.0, 0.0, 0.0]
    for name, ids in [
        ("all", list(range(1, 212))),
        ("sparse", list(range(2, 212, 2))),
        ("empty", []),
    ]:
        stages = {}
        rows = arrow_rank(
            index,
            generation_id=generation,
            space_id=space,
            dimension=dimension,
            query_vector=q,
            asset_ids=ids,
            top_k=7,
            stages=stages,
        )
        expected = sorted(
            [(i, 0.0 if i == 1 else 1.0) for i in ids if i <= 210], key=lambda r: (r[1], r[0])
        )[:7]
        observed = [(r["asset_id"], r["distance"]) for r in rows]
        result["scenarios"][name] = {"stages": stages, "rows": observed}
        check(name + "_ties_membership_space_oracle", observed == expected)
    stages = {}

    def append_during_query():
        index.upsert_batch(generation=generation_row, records=[record(212)])

    rows = arrow_rank(
        index,
        generation_id=generation,
        space_id=space,
        dimension=dimension,
        query_vector=q,
        asset_ids=[212],
        top_k=7,
        stages=stages,
        after_count=append_during_query,
    )
    check("concurrent_append_does_not_change_pinned_count_or_result", rows == [])
    rows = arrow_rank(
        index,
        generation_id=generation,
        space_id=space,
        dimension=dimension,
        query_vector=q,
        asset_ids=[212],
        top_k=7,
        stages={},
    )
    check("next_request_observes_new_version", [r["asset_id"] for r in rows] == [212])
    index._table(generation_id=generation, dimension=dimension, create=False).update(
        where="asset_id = 212", values={"generation_id": "foreign-generation"}
    )
    refused = False
    try:
        arrow_rank(
            index,
            generation_id=generation,
            space_id=space,
            dimension=dimension,
            query_vector=q,
            asset_ids=[212],
            top_k=7,
            stages={},
        )
    except RuntimeError as error:
        refused = "Foreign identity" in str(error)
    check("selected_foreign_generation_refused", refused)


def sources():
    result = shared.sources()
    for p in [Path(__file__), shared.REPO / "src/photo_search/search/documents.py"]:
        result[str(p)] = shared.file_digest(p)
    return result


def worker(result, check, method, scenario):
    result.update(
        method=method,
        scenario=scenario,
        source_before=sources(),
        memory_before=shared.memory(),
        oracle_sha256=shared.file_digest(AREA / "oracle.npz"),
        limitations="3-sample pilot, warm OS cache, no HTTP/model/UI; no production implementation",
    )
    oracle = np.load(AREA / "oracle.npz")
    ids = oracle["ids"] if scenario == "global" else oracle["city_ids"]
    distances = oracle["distances"][ids - 1]
    order = np.lexsort((ids, distances))[:5000]
    expected = [(int(ids[i]), float(distances[i])) for i in order]
    catalog = shared.open_catalog()
    index = shared.store.LanceVectorIndex(shared.VECTOR_ROOT / "data/vectors")
    original = shared.service.vector_channel
    last_stages = {}

    def channel(catalog, **kw):
        nonlocal last_stages
        last_stages = {}
        before = time.perf_counter()
        if method == "blocked":
            answer = original(catalog, **kw)
        else:
            binding = kw["binding"]
            where, params = shared.retrieval.scope_condition(catalog.connection, kw["scope"])
            cursor = shared.retrieval._confirmed_candidate_cursor(catalog, binding, where, params)
            try:
                candidate_ids = [int(r["asset_id"]) for r in cursor.fetchall()]
            finally:
                cursor.close()
            last_stages["authority_cursor_ms"] = 1000 * (time.perf_counter() - before)
            rows = arrow_rank(
                index,
                generation_id=binding.generation_id,
                space_id=binding.space_id,
                dimension=binding.dimension,
                query_vector=kw["query_vector"],
                asset_ids=candidate_ids,
                top_k=kw["top_k"],
                stages=last_stages,
            )
            answer = {
                "hits": rows,
                "blocks": int(bool(candidate_ids)),
                "scanned_assets": len(candidate_ids),
                "truncated": False,
            }
        last_stages["vector_channel_ms"] = 1000 * (time.perf_counter() - before)
        return answer

    shared.service.vector_channel = channel
    result["samples"] = []
    try:
        for repeat in range(-1, 3):
            before = time.perf_counter()
            payload = shared.service.search_assets(
                catalog,
                space_id=shared.SPACE,
                generation_id=shared.GENERATION,
                dimension=shared.DIM,
                vector_index=index,
                query_vector=oracle["query"].tolist(),
                top_k=5000,
                **shared.options(scenario),
            )
            elapsed = 1000 * (time.perf_counter() - before)
            observed = [(r["asset_id"], r["distance"]) for r in payload["channels"]["vector"]]
            normalized = {k: v for k, v in payload.items() if k != "vector_scan"}
            item = {
                "repeat": repeat,
                "elapsed_ms": elapsed,
                "stages": last_stages,
                "remaining_service_ms": elapsed - last_stages["vector_channel_ms"],
                "payload_without_vector_scan_sha256": shared.digest(normalized),
                "memory": shared.memory(),
            }
            if repeat < 0:
                result["first"] = item
            else:
                result["samples"].append(item)
            check(f"sample_{repeat}_full_distance_and_order_oracle", observed == expected)
            check(
                f"sample_{repeat}_authority_pending_scan",
                payload["authoritative"]["total"] == len(ids)
                and payload["vector_scan"]["scanned_assets"] == len(ids)
                and not payload["vector_scan"]["truncated"]
                and not payload["pending_semantic"]["entries"],
            )
            print(
                f"{method}/{scenario} {repeat}: service={elapsed:.1f}, channel={last_stages['vector_channel_ms']:.1f}",
                flush=True,
            )
        result["summary"] = {
            name: statistics.median([s[name] for s in result["samples"]])
            for name in ("elapsed_ms", "remaining_service_ms")
        }
        result["summary"]["vector_channel_ms"] = statistics.median(
            [s["stages"]["vector_channel_ms"] for s in result["samples"]]
        )
    finally:
        shared.service.vector_channel = original
        catalog.close()
        result["source_after"] = sources()
        check("source_identity", result["source_before"] == result["source_after"])


def run_all(result, check):
    result["source_before"] = sources()
    result["input_before"] = shared.inputs()
    result["processes"] = []
    for mode, scenario in [("small", None)] + [
        (m, s) for s in ("global", "shenzhen") for m in ("blocked", "arrow")
    ]:
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--mode",
            mode,
            "--output-dir",
            str(AREA),
            "--root",
            str(shared.ROOT),
            "--vector-root",
            str(shared.VECTOR_ROOT),
        ]
        name = mode
        if scenario:
            command += ["--scenario", scenario]
            name += "-" + scenario
        before = time.perf_counter()
        completed = subprocess.run(
            command,
            cwd=shared.REPO,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        (AREA / f"arrow-pilot-{name}.log").write_text(completed.stdout, encoding="utf-8")
        result["processes"].append(
            {
                "mode": mode,
                "scenario": scenario,
                "command": command,
                "actual_exit": completed.returncode,
                "wall_ms": 1000 * (time.perf_counter() - before),
            }
        )
        print(completed.stdout[-4000:], flush=True)
        check(name + "_actual_exit", completed.returncode == 0)
        if completed.returncode:
            return
    result["comparison"] = {}
    for scenario in ("global", "shenzhen"):
        base = json.loads(
            (AREA / f"arrow-pilot-blocked-{scenario}.json").read_text(encoding="utf-8")
        )
        new = json.loads((AREA / f"arrow-pilot-arrow-{scenario}.json").read_text(encoding="utf-8"))
        check(
            scenario + "_complete_payload_except_scan_blocks_equal",
            [s["payload_without_vector_scan_sha256"] for s in base["samples"]]
            == [s["payload_without_vector_scan_sha256"] for s in new["samples"]],
        )
        result["comparison"][scenario] = {
            "blocked": base["summary"],
            "arrow": new["summary"],
            "blocked_peak": base["memory_final"],
            "arrow_peak": new["memory_final"],
        }
    result["source_after"] = sources()
    result["input_after"] = shared.inputs()
    check("source_identity", result["source_before"] == result["source_after"])
    check("input_identity", result["input_before"] == result["input_after"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("small", "blocked", "arrow", "all"), required=True)
    parser.add_argument("--scenario", choices=("global", "shenzhen"))
    parser.add_argument("--output-dir", type=Path, default=AREA)
    parser.add_argument("--root", type=Path, default=shared.ROOT)
    parser.add_argument("--vector-root", type=Path, default=shared.VECTOR_ROOT)
    args = parser.parse_args()
    AREA = args.output_dir.resolve()
    shared.AREA = AREA
    shared.ROOT = args.root.resolve()
    shared.VECTOR_ROOT = args.vector_root.resolve()
    shared.DATABASE = shared.ROOT / "data/catalog/catalog.db"
    if not all(
        p.is_relative_to((shared.REPO / ".tmp").resolve())
        for p in (AREA, shared.ROOT, shared.VECTOR_ROOT)
    ):
        parser.error("output and synthetic fixture roots must stay in the project .tmp directory")
    AREA.mkdir(parents=True, exist_ok=True)
    if args.mode in ("blocked", "arrow") and args.scenario is None:
        parser.error("a worker needs --scenario")
    action = (
        small
        if args.mode == "small"
        else run_all
        if args.mode == "all"
        else lambda r, c: worker(r, c, args.mode, args.scenario)
    )
    name = args.mode + ("-" + args.scenario if args.scenario else "")
    sys.exit(shared.report("arrow-pilot-" + name, action))
