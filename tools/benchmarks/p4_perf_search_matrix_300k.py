"""Read-only search matrix and exclusive stage timings on an isolated 300k catalog.

Preparation uses a SQLite backup and the product text-projection builder. Vector
payloads are read from the previous synthetic benchmark; no model or real photo
library is accessed. Stage timings include wrapper overhead and are diagnostic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import numpy as np

from photo_search.catalog.db import Catalog
from photo_search.catalog.schema import SUPPORTED_SCHEMA_VERSION
from photo_search.catalog.sqlite_io import open_readonly
from photo_search.indexing.store import LanceVectorIndex
from photo_search.places import cn_facets
from photo_search.search import documents, retrieval, service
from photo_search.search.tokens import build_match
from tools.benchmarks.p4_perf_vectors_300k import DIM, GENERATION, SPACE, memory


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def stats(values):
    return {"n": len(values), "median_ms": statistics.median(values),
            "p95_ms": sorted(values)[math.ceil(len(values) * .95) - 1],
            "max_ms": max(values)}


def exhaustive_reference(index, vector, ids):
    """Independent all-row sort, using the same engine's distance arithmetic.

    It intentionally does not call vector_channel, its membership SQL, bounded
    candidate method, RRF or bounded merge. Different 10000-row blocks collect
    every candidate distance before one full sort. NumPy checks distances too.
    """
    rows = []
    for start in range(0, len(ids), 10_000):
        block = ids[start:start + 10_000]
        got = index.search_candidates(generation_id=GENERATION, space_id=SPACE,
                                      dimension=DIM, query_vector=vector.tolist(),
                                      asset_ids=block, limit=len(block))
        assert {int(r["asset_id"]) for r in got} == set(block)
        rows.extend((int(r["asset_id"]), float(r["distance"])) for r in got)
    assert len(rows) == len(ids)
    return sorted(rows, key=lambda r: (r[1], r[0]))[:5000]


def prepare(root: Path, vector_root: Path):
    target = root / "data/catalog/catalog.db"
    if target.exists():
        raise ValueError("Choose a fresh root; preparation never overwrites a fixture")
    target.parent.mkdir(parents=True)
    started = time.perf_counter()
    source = vector_root / "data/catalog/catalog.db"
    with sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)
    print("catalog copied; migrating and deriving text projection", flush=True)
    with Catalog.open(data_root=root / "data") as catalog:
        # Fixed filename labels, one label for each asset; duplicate locations use
        # the same basename. These are synthetic paths, never opened or scanned.
        with catalog.write() as conn:
            conn.execute("UPDATE file_locations SET relative_path = CASE "
                         "WHEN asset_id % 100 = 0 THEN '深圳 订单 20261015 ' "
                         "WHEN asset_id % 10 = 0 THEN '蓝色 汽车 ' "
                         "ELSE '普通 照片 ' END || asset_id || '.jpg'")
            conn.execute("UPDATE file_locations SET path_key=relative_path")
        before = time.perf_counter()
        projection = documents.rebuild_all(catalog, batch_size=1000)
        projection_ms = (time.perf_counter() - before) * 1000
        documents.verify_index(catalog.connection)
        assert documents.queue_depth(catalog.connection) == 0
        assert not documents.stale_document_asset_ids(catalog.connection, limit=30)
        count = catalog.read_one("SELECT COUNT(*) FROM assets")[0]
        assert count == 300_000
        assert catalog.read_one("SELECT COUNT(*) FROM search_documents")[0] == count
        schema = catalog.schema_version
        catalog.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    original = json.loads((vector_root / "fixture.json").read_text(encoding="utf-8"))
    result = {"assets": count, "schema": schema, "vector_root": str(vector_root.resolve()),
              "catalog": str(target.resolve()), "shenzhen_key": original["shenzhen_key"],
              "projection": projection, "projection_ms": projection_ms,
              "preparation_ms": (time.perf_counter() - started) * 1000,
              "synthetic_filename_rules": {"asset_id%100==0": "深圳 订单 20261015",
                                            "else asset_id%10==0": "蓝色 汽车",
                                            "otherwise": "普通 照片"},
              "limits": "No media files, real encoder, HTTP or browser; same prior 512D vectors"}
    (root / "fixture.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


class Recorder:
    def __init__(self):
        self.enabled = False
        self.stack = []
        self.exclusive = defaultdict(float)
        self.calls = Counter()
        self.originals = []

    def reset(self):
        assert not self.stack
        self.exclusive.clear()
        self.calls.clear()

    def wrap(self, owner, name, label):
        original = getattr(owner, name)

        @wraps(original)
        def timed(*args, **kwargs):
            if not self.enabled:
                return original(*args, **kwargs)
            actual = (f"{self.stack[-1][0]}/catalog_sql" if label == "sql" and self.stack else label)
            frame = [actual, 0.0]
            self.stack.append(frame)
            start = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                elapsed = (time.perf_counter() - start) * 1000
                self.stack.pop()
                self.exclusive[actual] += elapsed - frame[1]
                self.calls[actual] += 1
                if self.stack:
                    self.stack[-1][1] += elapsed

        self.originals.append((owner, name, original))
        setattr(owner, name, timed)

    def close(self):
        for owner, name, original in reversed(self.originals):
            setattr(owner, name, original)


def measure(root: Path, output: Path, samples: int, group: str):
    started_utc = datetime.now(timezone.utc).isoformat()
    started_clock = time.perf_counter()
    fixture = json.loads((root / "fixture.json").read_text(encoding="utf-8"))
    db = root / "data/catalog/catalog.db"
    catalog = Catalog(open_readonly(db, immutable=False), db_path=db, data_root=root / "data")
    assert catalog.schema_version == SUPPORTED_SCHEMA_VERSION
    index = LanceVectorIndex(Path(fixture["vector_root"]) / "data/vectors")
    rng = np.random.default_rng(20261015)
    vector = rng.standard_normal(DIM).astype(np.float32)
    vector /= np.linalg.norm(vector)
    binding = dict(space_id=SPACE, generation_id=GENERATION, dimension=DIM,
                   vector_index=index, query_vector=vector.tolist())
    member_sql, parameters = cn_facets.members_sql("city", key=fixture["shenzhen_key"])
    shenzhen = dict(additional_condition_sql=f"a.asset_id IN (SELECT asset_id FROM ({member_sql}))",
                    additional_condition_parameters=parameters)
    scenarios = {
        "filter_global": {}, "filter_source": {"source_id": 1},
        "filter_person": {"person_ids": [1]}, "filter_collection": {"collection_id": 1},
        "filter_source_person": {"source_id": 1, "person_ids": [1]},
        "fts_chinese": {"text": "订单"}, "fts_digits": {"text": "20261015"},
        "fts_no_match": {"text": "不存在的火星动物"},
        "vector_global": binding, "vector_shenzhen": {**binding, **shenzhen},
        "hybrid_filename_vector": {**binding, "text": "订单"},
    }
    names = [n for n in scenarios if (n.startswith(("vector_", "hybrid_"))) == (group == "vector")]
    all_ids = list(range(1, 300_001))
    source_ids = {int(r[0]) for r in catalog.read_all(
        "SELECT DISTINCT asset_id FROM file_locations WHERE source_id=1 AND asset_id IS NOT NULL")}
    # Fixture membership is independent of the query implementation: source1 via
    # stored locations, person1 has ids 1..150000, collection1 contains person1.
    person_ids = {int(r[0]) for r in catalog.read_all(
        "SELECT DISTINCT f.asset_id FROM faces f JOIN face_person_links pf ON pf.face_id=f.face_id "
        "WHERE pf.person_id=1 AND pf.decision='CONFIRMED'")}
    expected = {"filter_global": all_ids, "filter_source": sorted(source_ids),
                "filter_person": sorted(person_ids), "filter_collection": sorted(person_ids),
                "filter_source_person": sorted(source_ids & person_ids)}
    assert len(person_ids) == 150_000
    assert person_ids == set(range(1, 150_001))
    assert [int(r[0]) for r in catalog.read_all(
        "SELECT person_id FROM person_collection_members WHERE collection_id=1 ORDER BY person_id")
    ] == [1]
    references = {}
    reference_evidence = {}
    if group == "vector":
        city_id = int(fixture["shenzhen_key"].split(":")[1])
        city_ids = [int(r[0]) for r in catalog.read_all(
            "SELECT DISTINCT asset_id FROM cn_admin_resolutions WHERE city_id=? ORDER BY asset_id",
            (city_id,))]
        assert len(city_ids) == 179_890
        matrix = np.load(Path(fixture["vector_root"]) / "vectors.npy", mmap_mode="r")
        for label, ids in (("vector_global", all_ids), ("vector_shenzhen", city_ids)):
            before = time.perf_counter()
            reference = exhaustive_reference(index, vector, ids)
            references[label] = reference
            # The frozen product uses Lance's default squared-L2 distance.
            # Compare that distance on independent numpy float64 arithmetic, not order
            # across float32 near-ties; exact ordering is checked against the
            # exhaustive engine-distance sort above, with no tolerance there.
            selected = np.asarray(matrix[[r[0] - 1 for r in reference]], dtype=np.float64)
            q = vector.astype(np.float64)
            l2_squared = np.sum((selected - q) ** 2, axis=1)
            error = float(np.max(np.abs(l2_squared - np.array([r[1] for r in reference]))))
            assert error < 1e-5, error
            reference_evidence[label] = {"candidate_count": len(ids), "top_k": len(reference),
                                         "ordered_distance_sha256": digest(reference),
                                         "numpy_l2_squared_max_abs_error": error,
                                         "elapsed_ms_outside_samples": (time.perf_counter() - before) * 1000}
        references["hybrid_filename_vector"] = references["vector_global"]
        reference_evidence["method"] = "all candidates, exhaustive engine distances, independent global sort; numpy float64 distance check; fixture-derived city membership, no manual overrides"
        del matrix
    rec = Recorder()
    rec.wrap(catalog, "read_all", "sql")
    rec.wrap(catalog, "read_one", "sql")
    for name, label in (("vector_channel", "vector_python"),
                        ("authoritative_page", "authority"), ("_verified_ids", "final_recheck"),
                        ("pending_projection_page", "pending"), ("_projection_block", "projection_summary"),
                        ("fts_channel", "fts"), ("text_column_matches", "text_columns"),
                        ("reciprocal_rank_fusion", "rrf"), ("scope_view", "scope")):
        rec.wrap(service, name, label)
    for name in ("search_candidates_bounded", "search_candidates"):
        rec.wrap(index, name, "vector_engine")
    rec.wrap(service, "search_assets", "service_other")
    result = {"fixture": fixture, "samples_requested": samples, "group": group,
              "started_utc": started_utc, "python": sys.version, "command": sys.argv,
              "layer": "search_assets; real product entry, synthetic vector; no HTTP/model/UI",
              "instrumentation": "exclusive nested perf_counter wrappers; SQL includes fetch; vector_python includes candidate conversion/merge/loop; direct connection reads remain in owning stage",
              "first_call": "first call per scenario, no OS cache purge",
              "control_note": "first and up to three interleaved controls run through disabled wrappers; overhead is diagnostic, not corrected away",
              "independent_vector_reference": reference_evidence,
              "code_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (
                  Path(service.__file__), Path(retrieval.__file__), Path(__file__))},
              "scenarios": {}, "memory_before": memory()}
    output.parent.mkdir(parents=True, exist_ok=True)
    suite = ET.Element("testsuite", name="search_matrix_measurement_checks_not_pytest")
    failures = 0
    try:
        for name in names:
            item = {"samples_ms": [], "stages_ms": [], "calls": [], "hashes": [],
                    "controls_ms": [], "errors": []}
            result["scenarios"][name] = item
            case = ET.SubElement(suite, "testcase", classname="search_matrix", name=name)
            try:
                rec.enabled = False
                start = time.perf_counter()
                first = service.search_assets(catalog, top_k=5000, **scenarios[name])
                item["first_ms"] = (time.perf_counter() - start) * 1000
                item["first_hash"] = digest(first)
                item["observation"] = {"total": first["authoritative"]["total"],
                                       "hits": len(first["hits"]), "vector_scan": first["vector_scan"]}
                assert first["state"] == "OK"
                ids = [h["asset_id"] for h in first["hits"]]
                assert len(ids) == len(set(ids))
                if name in expected:
                    assert first["authoritative"]["total"] == len(expected[name])
                    assert len(ids) == min(retrieval.AUTHORITY_PAGE_LIMIT, len(expected[name]))
                    assert ids == expected[name][:len(ids)]
                elif name.startswith("fts_"):
                    assert set(ids) == (set(range(100, 300001, 100)) if name != "fts_no_match" else set())
                if name == "hybrid_filename_vector":
                    assert {r["asset_id"] for r in first["channels"]["fts"]} == set(range(100, 300001, 100))
                if name in references:
                    vector_rows = first["channels"]["vector"]
                    assert [(r["asset_id"], r["distance"]) for r in vector_rows] == references[name]
                    total = 179_890 if name == "vector_shenzhen" else 300_000
                    assert first["authoritative"]["total"] == total
                    assert first["vector_scan"]["scanned_assets"] == total
                    assert not first["vector_scan"]["truncated"]
                    assert not first["pending_semantic"]["entries"]
                    expected_ranks = defaultdict(dict)
                    for rank, row in enumerate(references[name], 1):
                        expected_ranks[row[0]]["vector"] = rank
                    if name == "hybrid_filename_vector":
                        text_rows = catalog.read_all(
                            "SELECT rowid,bm25(search_documents_fts) score FROM search_documents_fts "
                            "WHERE search_documents_fts MATCH ? ORDER BY score,rowid",
                            (build_match("订单").expression,))
                        assert {int(row[0]) for row in text_rows} == set(range(100, 300001, 100))
                        for rank, row in enumerate(text_rows, 1):
                            expected_ranks[int(row[0])]["fts"] = rank
                    scores = {key: sum(1/(60+rank) for rank in ranks.values())
                              for key, ranks in expected_ranks.items()}
                    expected_order = sorted(scores, key=lambda key: (-scores[key], -len(expected_ranks[key]), key))
                    assert ids == expected_order
                    for hit in first["hits"]:
                        assert abs(hit["score"] - scores[hit["asset_id"]]) < 1e-14
                        assert hit["matched_by"] == expected_ranks[hit["asset_id"]]
                for repeat in range(samples):
                    if repeat in (0, samples // 2, samples - 1):
                        rec.enabled = False
                        start = time.perf_counter()
                        control = service.search_assets(catalog, top_k=5000, **scenarios[name])
                        item["controls_ms"].append((time.perf_counter() - start) * 1000)
                        assert digest(control) == item["first_hash"]
                    rec.reset()
                    rec.enabled = True
                    start = time.perf_counter()
                    payload = service.search_assets(catalog, top_k=5000, **scenarios[name])
                    elapsed = (time.perf_counter() - start) * 1000
                    rec.enabled = False
                    item["samples_ms"].append(elapsed)
                    item["stages_ms"].append(dict(rec.exclusive))
                    item["calls"].append(dict(rec.calls))
                    item["hashes"].append(digest(payload))
                    assert item["hashes"][-1] == item["first_hash"]
                    assert 0 <= elapsed - sum(rec.exclusive.values()) < max(2, elapsed * .01)
                    if repeat % 10 == 0:
                        print(f"{name}: {repeat+1}/{samples} {elapsed:.1f}ms", flush=True)
                item["summary"] = stats(item["samples_ms"])
                labels = sorted({label for row in item["stages_ms"] for label in row})
                item["stage_medians_ms"] = {label: statistics.median(r.get(label, 0) for r in item["stages_ms"]) for label in labels}
                item["passed"] = True
            except Exception as error:
                rec.enabled = False
                item["passed"] = False
                item["errors"].append({"type": type(error).__name__, "message": str(error)})
                ET.SubElement(case, "failure", message=str(error)).text = repr(error)
                failures += 1
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["memory_after"] = memory()
    finally:
        rec.close()
        catalog.close()
        result["finished_utc"] = datetime.now(timezone.utc).isoformat()
        result["elapsed_seconds_including_oracles"] = time.perf_counter() - started_clock
        suite.set("tests", str(len(suite)))
        suite.set("failures", str(failures))
        suite.set("errors", "0")
        suite.set("skipped", "0")
        ET.ElementTree(suite).write(output.with_suffix(".xml"), encoding="utf-8", xml_declaration=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if failures:
        raise RuntimeError(f"{failures} scenarios failed; see retained JSON and XML")
    return {name: v["summary"] for name, v in result["scenarios"].items()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "measure"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--vector-root", type=Path)
    parser.add_argument("--group", choices=("basic", "vector"), default="basic")
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    work = (repo / ".tmp").resolve()
    args.root = args.root.resolve()
    if not args.root.is_relative_to(work):
        parser.error("--root must remain inside the project-local .tmp directory")
    if args.samples < 1:
        parser.error("--samples must be positive")
    if args.mode == "prepare":
        if args.vector_root is None:
            parser.error("prepare requires --vector-root")
        args.vector_root = args.vector_root.resolve()
        if not args.vector_root.is_relative_to(work):
            parser.error("--vector-root must be the prior isolated synthetic fixture")
        result = prepare(args.root, args.vector_root)
    else:
        if args.output is None:
            parser.error("measure requires --output")
        args.output = args.output.resolve()
        artifacts = (repo / "docs/reports/artifacts").resolve()
        if not any(args.output.is_relative_to(base) for base in (work, artifacts)):
            parser.error("--output must remain inside .tmp or docs/reports/artifacts")
        result = measure(args.root, args.output, args.samples, args.group)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
