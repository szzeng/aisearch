"""Paired original-IN/current-filter service measurements on the existing synthetic 300k root.

The candidate predicate is the only switched behavior. Oracles read every candidate
through the original IN filter, independently sort all engine distances, and verify
the compact predicate's complete membership in Lance before timed product calls.
No encoder, photograph, HTTP service or OS cache purge is involved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np

from photo_search.catalog.db import Catalog
from photo_search.catalog.sqlite_io import open_readonly
from photo_search.indexing import store
from photo_search.places import cn_facets
from photo_search.search import retrieval, service
from tools.benchmarks.p4_perf_vectors_300k import DIM, GENERATION, SPACE, memory


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def file_digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def original_in(ids: list[int]) -> str:
    return "asset_id IN (" + ", ".join(map(str, ids)) + ")"


def stats(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values), "median_ms": statistics.median(values),
        "p95_ms": sorted(values)[math.ceil(len(values) * .95) - 1], "max_ms": max(values),
    }


def reference(index, ids, vector, matrix, compact):
    """Full original-IN engine-distance oracle and exact compact membership, outside timing."""
    rows = []
    membership = []
    table = index._table(generation_id=GENERATION, dimension=DIM, create=False)
    assert table is not None
    # Deliberately different from the product's 15000 candidate block size.
    for start in range(0, len(ids), 10000):
        block = ids[start:start + 10000]
        found = index.search(
            generation_id=GENERATION, space_id=SPACE, dimension=DIM,
            query_vector=vector.tolist(), limit=len(block), columns=store.CANDIDATE_ROW_COLUMNS,
            where=original_in(block) + f" AND space_id = '{SPACE}'",
        )
        assert sorted(row["asset_id"] for row in found) == block
        rows.extend((row["asset_id"], row["distance"]) for row in found)
    rows.sort(key=lambda row: (row[1], row[0]))
    assert len(rows) == len(ids)
    for start in range(0, len(ids), retrieval.SCOPE_BLOCK_SIZE):
        block = ids[start:start + retrieval.SCOPE_BLOCK_SIZE]
        predicate = compact(block)
        got = table.search().where(predicate + f" AND space_id = '{SPACE}'").select(
            ["asset_id"]
        ).limit(len(block) + 1).to_arrow()["asset_id"].to_pylist()
        assert sorted(got) == block, "compact filter widened or dropped authoritative candidates"
        membership.append({
            "count": len(block), "ids_sha256": digest(block),
            "in_chars": len(original_in(block)), "compact_chars": len(predicate),
            "uses_ranges": "BETWEEN" in predicate,
        })
    top = rows[:5000]
    selected = np.asarray(matrix[[row[0] - 1 for row in top]], dtype=np.float64)
    expected = np.sum((selected - vector.astype(np.float64)) ** 2, axis=1)
    error = float(np.max(np.abs(expected - np.asarray([row[1] for row in top]))))
    assert error < 1e-5
    boundary = top[-1][1]
    return top, {
        "candidate_count": len(ids), "candidate_ids_sha256": digest(ids),
        "all_ordered_distance_sha256": digest(rows), "top_k": len(top),
        "top_k_ordered_distance_sha256": digest(top), "numpy_l2_max_abs_error": error,
        "equal_distance_adjacent_pairs": sum(a[1] == b[1] for a, b in zip(rows, rows[1:])),
        "boundary_distance": boundary,
        "boundary_tie_total": sum(row[1] == boundary for row in rows),
        "boundary_tie_in_top_k": sum(row[1] == boundary for row in top),
        "compact_membership_blocks": membership,
    }


def measure(root: Path, output: Path, samples: int) -> dict[str, Any]:
    repo = Path(__file__).resolve().parents[2]
    fixture = json.loads((root / "fixture.json").read_text(encoding="utf-8"))
    assert fixture["assets"] == 300000 and fixture["schema"] == 32
    vector_root = Path(fixture["vector_root"]).resolve()
    assert vector_root.is_relative_to((repo / ".tmp").resolve())
    database = root / "data/catalog/catalog.db"
    catalog = Catalog(open_readonly(database, immutable=False), db_path=database,
                      data_root=root / "data")
    index = store.LanceVectorIndex(vector_root / "data/vectors")
    current = store._candidate_id_condition
    methods = {"original_in": original_in, "compact": current}
    def input_paths():
        paths = [root / "fixture.json", database, vector_root / "fixture.json",
                 vector_root / "vectors.npy",
                 *sorted(p for p in index.root.rglob("*") if p.is_file())]
        wal = database.with_name(database.name + "-wal")
        if wal.exists():
            paths.append(wal)
        return paths

    paths = input_paths()
    inputs = {str(path): file_digest(path) for path in paths}
    def source_paths():
        return [
            *sorted((repo / "src/photo_search").rglob("*.py")), Path(__file__).resolve(),
            repo / "tools/benchmarks/p4_perf_vectors_300k.py",
            repo / "tools/diagnostics/ann_scale_benchmark.py",
            repo / "pyproject.toml", repo / "uv.lock",
        ]

    sources = {str(path): file_digest(path) for path in source_paths()}
    vector = np.random.default_rng(20261015).standard_normal(DIM).astype(np.float32)
    vector /= np.linalg.norm(vector)
    city_id = int(fixture["shenzhen_key"].split(":")[1])
    all_ids = [int(row[0]) for row in catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id")]
    city_ids = [int(row[0]) for row in catalog.read_all(
        "SELECT DISTINCT asset_id FROM cn_admin_resolutions WHERE city_id=? ORDER BY asset_id",
        (city_id,),
    )]
    assert all_ids == list(range(1, 300001)) and len(city_ids) == 179890
    membership_sql, parameters = cn_facets.members_sql("city", key=fixture["shenzhen_key"])
    scenarios = {
        "vector_global": (all_ids, {}),
        "vector_shenzhen": (city_ids, {
            "additional_condition_sql": f"a.asset_id IN (SELECT asset_id FROM ({membership_sql}))",
            "additional_condition_parameters": parameters,
        }),
    }
    result = {
        "kind": "paired_exact_candidate_filter_300k", "python": sys.version,
        "runtime_versions": {package: version(package) for package in ("lancedb", "numpy", "pyarrow")},
        "command": sys.argv, "started_utc": datetime.now(timezone.utc).isoformat(),
        "layer": "real service.search_assets; synthetic 512D query; no HTTP/model/UI",
        "method": "only store._candidate_id_condition changes; alternate AB/BA for paired samples",
        "cache_note": "oracles and file hashing precede samples and warm file caches; first call is "
                      "the first product call per strategy/scenario, not cold process/OS startup",
        "fixture": fixture, "input_sha256": inputs, "source_sha256": sources,
        "samples_requested_per_strategy": samples, "memory_before": memory(), "scenarios": {},
        "status": "IN_PROGRESS",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    suite = ET.Element("testsuite", name="paired_300k_candidate_oracles_not_pytest")
    failures = 0
    started = time.perf_counter()
    matrix = np.load(vector_root / "vectors.npy", mmap_mode="r")
    try:
        for scenario_index, (name, (ids, options)) in enumerate(scenarios.items()):
            case = ET.SubElement(suite, "testcase", name=name)
            item = {"strategies": {mode: {"samples_ms": [], "hashes": []} for mode in methods}}
            result["scenarios"][name] = item
            try:
                oracle_start = time.perf_counter()
                expected, oracle = reference(index, ids, vector, matrix, current)
                oracle["elapsed_ms_outside_samples"] = (time.perf_counter() - oracle_start) * 1000
                item["oracle"] = oracle
                expected_payload_hash = None
                for repeat in range(-1, samples):
                    order = list(methods)
                    if (repeat + scenario_index) % 2:
                        order.reverse()
                    for mode in order:
                        store._candidate_id_condition = methods[mode]
                        before = time.perf_counter()
                        payload = service.search_assets(
                            catalog, space_id=SPACE, generation_id=GENERATION, dimension=DIM,
                            vector_index=index, query_vector=vector.tolist(), top_k=5000, **options,
                        )
                        elapsed = (time.perf_counter() - before) * 1000
                        observed = [(row["asset_id"], row["distance"])
                                    for row in payload["channels"]["vector"]]
                        assert observed == expected
                        assert payload["state"] == "OK"
                        assert payload["authoritative"]["total"] == len(ids)
                        assert payload["vector_scan"]["scanned_assets"] == len(ids)
                        assert not payload["vector_scan"]["truncated"]
                        assert not payload["pending_semantic"]["entries"]
                        assert [row["asset_id"] for row in payload["hits"]] == [row[0] for row in expected]
                        payload_hash = digest(payload)
                        if expected_payload_hash is None:
                            expected_payload_hash = payload_hash
                        assert payload_hash == expected_payload_hash
                        sample = item["strategies"][mode]
                        if repeat < 0:
                            sample.update(first_ms=elapsed, first_hash=payload_hash)
                        else:
                            sample["samples_ms"].append(elapsed)
                            sample["hashes"].append(payload_hash)
                    if repeat >= 0 and (repeat % 5 == 0 or repeat == samples - 1):
                        print(f"{name}: paired {repeat + 1}/{samples}", flush=True)
                    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                for sample in item["strategies"].values():
                    sample["summary"] = stats(sample["samples_ms"])
                item["median_ratio_original_over_compact"] = (
                    item["strategies"]["original_in"]["summary"]["median_ms"]
                    / item["strategies"]["compact"]["summary"]["median_ms"]
                )
                item["passed"] = True
            except Exception as error:
                failures += 1
                item.update(passed=False, error=repr(error))
                ET.SubElement(case, "failure", message=str(error)).text = repr(error)
        integrity = ET.SubElement(suite, "testcase", name="source_and_input_identity")
        result["inputs_unchanged"] = (
            {str(path) for path in input_paths()} == set(inputs)
            and all(file_digest(Path(p)) == sha for p, sha in inputs.items())
        )
        result["sources_unchanged"] = (
            {str(path) for path in source_paths()} == set(sources)
            and all(file_digest(Path(p)) == sha for p, sha in sources.items())
        )
        if not result["inputs_unchanged"] or not result["sources_unchanged"]:
            failures += 1
            ET.SubElement(integrity, "failure", message="input or source changed during samples")
    except BaseException as error:
        failures += 1
        result["aborted"] = repr(error)
        ET.SubElement(
            ET.SubElement(suite, "testcase", name="run_aborted"),
            "failure", message=str(error),
        ).text = repr(error)
        raise
    finally:
        store._candidate_id_condition = current
        del matrix
        catalog.close()
        result["memory_after"] = memory()
        result["elapsed_seconds_including_oracles"] = time.perf_counter() - started
        result["finished_utc"] = datetime.now(timezone.utc).isoformat()
        result["status"] = "FAIL_OR_INTERRUPTED" if failures else "PASS"
        suite.attrib.update(tests=str(len(suite)), failures=str(failures), errors="0", skipped="0")
        ET.ElementTree(suite).write(output.with_suffix(".xml"), encoding="utf-8", xml_declaration=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if failures:
        raise RuntimeError(f"{failures} paired benchmark checks failed; retained {output}")
    return {name: row["median_ratio_original_over_compact"]
            for name, row in result["scenarios"].items()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=30)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    root, output = args.root.resolve(), args.output.resolve()
    if not root.is_relative_to((repo / ".tmp").resolve()):
        parser.error("root must be the project-local synthetic fixture")
    if not any(output.is_relative_to((repo / area).resolve())
               for area in (".tmp", "docs/reports/artifacts")):
        parser.error("output must remain in project-local evidence directories")
    if args.samples < 1:
        parser.error("samples must be positive")
    if output.exists() or output.with_suffix(".xml").exists():
        parser.error("choose unused output paths to preserve prior evidence")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        summary = measure(root, output, args.samples)
    except BaseException as error:
        # Setup precedes the first timed scenario. A missing/bad fixture or an
        # interrupted hash must still leave explicit failed evidence, never a
        # silent absence that could be mistaken for a successful zero-test run.
        if not output.with_suffix(".xml").exists():
            evidence = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
            evidence.update(
                status="FAIL_OR_INTERRUPTED", unhandled_error=repr(error),
                phase="setup_or_unfinalized_run", command=sys.argv, python=sys.version,
                finished_utc=datetime.now(timezone.utc).isoformat(),
            )
            output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            suite = ET.Element(
                "testsuite", name="paired_candidate_setup_or_run_failure", tests="1",
                failures="1", errors="0", skipped="0",
            )
            ET.SubElement(
                ET.SubElement(suite, "testcase", name="setup_or_run_finalization"),
                "failure", message=str(error),
            ).text = repr(error)
            ET.ElementTree(suite).write(
                output.with_suffix(".xml"), encoding="utf-8", xml_declaration=True,
            )
        raise
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
