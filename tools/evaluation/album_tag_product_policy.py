"""CPU-only sparse RAM policy study using the previously frozen public scores.

This is further exploration of inspected data, not an untouched holdout or a
production query benchmark. Freeze the two storage policies before evaluation;
do not tune thresholds, class mappings, or weights using their test results.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from tools.evaluation import album_tag_fusion as fusion
from tools.evaluation.prepare_broad_album_data import Inputs, digest

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(".tmp/search-quality-product")
DATASETS = ("openimages400", "coco480", "photobench1047")
METHODS = {key: value for key, value in fusion.METHODS.items() if key != "tags_only"}
REPRESENTATIONS = ("dense_float32_reference", "top32_fp16", "official_threshold_top32_fp16")
POLICY = {
    "schema": "album-tag-product-policy-v1",
    "disclosure": "New exploration of previously inspected datasets/scores, not untouched test; frozen before sparse results.",
    "representations": list(REPRESENTATIONS),
    "top_k": 32,
    "sparse_order": "descending original float32 score; class index ascending for ties; convert retained score to fp16",
    "official_threshold": "original float32 score > official class threshold; top32 cap among passing classes; never filter search results",
    "threshold_flags": "top32 retains official hit bits computed before fp16 rounding; diagnostic only",
    "missing": "zero score and tied midrank; absent labels never imply human-negative truth",
    "exact_route": "official English or Chinese name, strip/casefold/whitespace collapse; ambiguous name maps to none; no substrings, extra aliases or partial concepts",
    "query_modes": "semantic text category only; images/text-only/complex phrases baseline exactly",
    "methods": METHODS,
    "rrf": "k60, float64 midranks over full comparison population; baseline order wins final ties; constant tag scores baseline exactly",
    "selection": "separately per representation maximize OI calibration family-balanced AP over fixed methods; baseline wins ties; never choose based on test",
    "metrics": ["judged_ap", "known_precision_at_20", "unknown_fraction_at_20", "negative_retrieval_fraction_at_20",
                "recall_at_20", "small_recall_at_20", "small_recall_at_50"],
    "candidate_bound": 5000,
    "bounded_candidates": "functional checks only; available galleries smaller than5000 cannot establish300k candidate coverage or query latency",
    "storage": "payload estimate IDsuint16+scoresfp16+bitflags+uint8count; exclude SQLite/index/identity overhead and report separately",
    "decision": "default off; no hard filter; consider precision and descriptive-query stability as well as AP and small-target recall",
    "performance": ["Offline CPU transformation of1927 existing4585-class score rows; no GPU/model inference.",
                    "At most3 representations and4 ranking methods; reuse fixed splits/vectors/queries.",
                    "Unit invariants and one frozen evaluation; preserve failures and stop without extra tuning."],
}


def freeze(output: Path, root: Path = ROOT) -> dict:
    reader = Inputs(root)
    output = reader.path(output)
    if output.exists():
        raise ValueError("policy exists; preserve frozen policy")
    paths = [BASE / "broad-tags-fusion-r2" / name for name in ("plan.json", "queries.json", "evaluation-policy.json")]
    paths += [Path("tools/evaluation/album_tagging_lock.json"), BASE / "compatibility-final/compatibility.json"]
    paths += [BASE / "broad-query-baseline-r2" / name for name in ("result.json", "queries.npy")]
    for dataset in DATASETS:
        paths += [BASE / "broad-tags-data" / (dataset + ".json")]
        paths += [BASE / "broad-tags/runs" / dataset / name for name in ("result.json", "scores.npy")]
    paths += [BASE / "broad-tags/official/ram/data" / name for name in (
        "ram_tag_list.txt", "ram_tag_list_chinese.txt", "ram_tag_list_threshold.txt")]
    for path in paths:
        reader.raw(path)
    record = {**POLICY, "frozen_at": dt.datetime.now(dt.timezone.utc).isoformat(), "input_sha256": reader.unchanged()}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
    return {"policy": str(output), "sha256": digest(output.read_bytes())}


def verify_policy(reader: Inputs, path: Path) -> dict:
    policy = reader.json(path)
    if {key: policy.get(key) for key in POLICY} != POLICY:
        raise ValueError("evaluation differs from frozen policy")
    for name, expected in policy["input_sha256"].items():
        if digest(reader.raw(name)) != expected:
            raise ValueError("frozen policy input changed")
    return policy


def name_key(value: str) -> str:
    return " ".join(value.casefold().split())


def exact_names(english: list[str], chinese: list[str]) -> dict[str, int]:
    if len(english) != len(chinese):
        raise ValueError("vocabulary lengths differ")
    groups: dict[str, set[int]] = {}
    for index, names in enumerate(zip(english, chinese, strict=True)):
        for name in names:
            key = name_key(name)
            if key:
                groups.setdefault(key, set()).add(index)
    return {key: next(iter(indices)) for key, indices in groups.items() if len(indices) == 1}


def route(text: str, names: dict[str, int], *, mode: str = "semantic", has_image: bool = False) -> int | None:
    return names.get(name_key(text)) if mode == "semantic" and not has_image else None


@dataclass(frozen=True)
class SparseScores:
    indices: np.ndarray
    values: np.ndarray
    counts: np.ndarray
    threshold_bits: np.ndarray

    def column(self, class_id: int) -> np.ndarray:
        return np.where(self.indices == class_id, self.values, 0).max(axis=1).astype(np.float64)

    def storage(self) -> dict:
        # A compact variable-count record needs ceil(count/8) threshold bytes.
        counts = self.counts.astype(np.int64)
        payload = int((4 * counts + (counts + 7) // 8 + 1).sum())
        average = payload / len(counts) if len(counts) else 0.
        return {"images": len(counts), "retained_entries": int(counts.sum()),
                "entries_mean": float(counts.mean()) if len(counts) else 0.,
                "entries_max": int(counts.max()) if len(counts) else 0,
                "compact_payload_bytes": payload, "mean_payload_bytes_per_image": average,
                "estimated_300000_payload_bytes": int(round(average * 300000)),
                "array_payload_bytes": sum(a.nbytes for a in (self.indices, self.values, self.counts, self.threshold_bits)),
                "excluded": "SQLite records/indexes, asset/content identities, model bundle and snapshot overhead"}


def sparsify(scores: np.ndarray, thresholds: np.ndarray, representation: str) -> SparseScores:
    if representation not in REPRESENTATIONS[1:]:
        raise ValueError("unplanned sparse representation")
    if (scores.ndim != 2 or not 1 <= scores.shape[1] <= 65535 or thresholds.shape != (scores.shape[1],)
            or not np.isfinite(scores).all() or not np.isfinite(thresholds).all()
            or np.any((scores < 0) | (scores > 1)) or np.any((thresholds < 0) | (thresholds > 1))):
        raise ValueError("invalid score/threshold matrix")
    ids = np.full((len(scores), 32), 65535, dtype=np.uint16)
    values = np.zeros((len(scores), 32), dtype=np.float16)
    counts = np.zeros(len(scores), dtype=np.uint8)
    bits = np.zeros(len(scores), dtype=np.uint32)
    for index, row in enumerate(scores):
        ranked = np.argsort(-row, kind="stable")
        if representation == "official_threshold_top32_fp16":
            ranked = ranked[row[ranked] > thresholds[ranked]]
        chosen = ranked[:32]
        counts[index] = len(chosen)
        ids[index, :len(chosen)] = chosen
        values[index, :len(chosen)] = row[chosen].astype(np.float16)
        bits[index] = sum(1 << position for position, class_id in enumerate(chosen) if row[class_id] > thresholds[class_id])
    return SparseScores(ids, values, counts, bits)


def order(baseline: np.ndarray, tags: np.ndarray | None, method: str, *, limit: int | None = None) -> np.ndarray:
    """Full-population diagnostic; an optional cap never changes truth to negatives."""
    if method not in METHODS or (limit is not None and not 1 <= limit <= 5000):
        raise ValueError("unplanned method or result bound")
    if baseline.ndim != 1 or not len(baseline) or not np.isfinite(baseline).all():
        raise ValueError("invalid baseline scores")
    if tags is not None and (tags.shape != baseline.shape or not np.isfinite(tags).all()):
        raise ValueError("invalid auxiliary scores")
    ranked = fusion.fused_order(baseline, tags, method)
    return ranked if limit is None else ranked[:limit]


def load_vectors(reader: Inputs, plan: dict, data: dict, compatibility: dict) -> tuple[np.ndarray, dict[str, int], list[dict]]:
    cache = data["embedding_caches"]["qwen"]
    image_record = reader.json(Path(cache["directory"]) / "result.json")
    fusion.verify_compatibility(compatibility, image_record)
    images = fusion.normalize(fusion.array(reader, Path(cache["image_vectors"]), cache["image_vectors_sha256"]), 512)
    if len(images) != len(data["assets"]):
        raise ValueError("embedding rows differ from frozen assets")
    if data["dataset"] == "photobench1047":
        query_run, query_record = Path(cache["directory"]), image_record
        expected_sha = query_record["vectors_sha256"]["queries.npy"]
    else:
        query_run = BASE / "broad-query-baseline-r2"
        query_record = reader.json(query_run / "result.json")
        if (query_record.get("schema") != "album-query-baseline-v1" or query_record.get("status") != "PASS"
                or query_record.get("dimension") != 512):
            raise ValueError("formal query baseline identity incomplete")
        fusion.verify_compatibility(compatibility, image_record, query_record)
        frozen_queries = reader.json(BASE / "broad-tags-fusion-r2/queries.json")["queries"]
        if query_record["queries"] != frozen_queries:
            raise ValueError("query rows differ from frozen plan")
        expected_sha = query_record["vectors_sha256"]
        if isinstance(expected_sha, dict):
            expected_sha = expected_sha["queries.npy"]
    queries = query_record["queries"]
    matrix = fusion.normalize(fusion.array(reader, query_run / "queries.npy", expected_sha), 512)
    positions = {row["query_id"]: i for i, row in enumerate(queries)}
    if len(matrix) != len(queries) or len(positions) != len(queries):
        raise ValueError("query row identity differs")
    return images @ matrix.T, positions, queries


def evaluate_dataset(reader: Inputs, plan: dict, dataset: str, compatibility: dict,
                     names: dict[str, int], thresholds: np.ndarray, output: Path,
                     production: tuple[Path, object] | None = None) -> tuple[list[dict], dict]:
    entry = plan["datasets"][dataset]
    data = reader.json(entry["manifest"])
    if digest(reader.raw(entry["manifest"])) != entry["manifest_sha256"]:
        raise ValueError("dataset changed from original plan")
    if production is None:
        _, dense, _ = fusion.tag_inputs(reader, BASE / "broad-tags/runs" / dataset, "ram", plan, data)
    else:
        dense = production_scores(reader, production[0] / dataset, data, production[1])
    sparse = {name: sparsify(dense, thresholds, name) for name in REPRESENTATIONS[1:]}
    baseline, positions, vector_queries = load_vectors(reader, plan, data, compatibility)
    rows = []
    for query in entry["queries"]:
        position = positions[query["baseline_query_id"]]
        if vector_queries[position]["text"] != query["text"]:
            raise ValueError("query text differs from original vectors")
        class_id = route(query["text"], names)
        truth, small, family, complex_query = fusion.truths(data, query)
        tags = {name: None if class_id is None else dense[:, class_id] if name == REPRESENTATIONS[0] else sparse[name].column(class_id)
                for name in REPRESENTATIONS}
        for split in (("calibration", "test") if dataset == "openimages400" else ("all",)):
            selected = np.array([i for i, asset in enumerate(data["assets"]) if split == "all"
                                 or data["split_by_asset"][str(asset["asset_id"])] == split])
            row_split = split
            if dataset == "photobench1047":
                row_split = next(r for r in data["evaluation"]["queries"] if r["query_id"] == query["source_query_id"])["split"]
            metrics, diagnostics = {}, {}
            reference_orders = {method: order(baseline[selected, position], None if class_id is None else tags[REPRESENTATIONS[0]][selected], method)
                                for method in METHODS}
            for name in REPRESENTATIONS:
                column = tags[name]
                orders = {method: order(baseline[selected, position], None if column is None else column[selected], method) for method in METHODS}
                metrics[name] = {method: fusion.measured(ranked, truth[selected], None if small is None else small[selected])
                                 for method, ranked in orders.items()}
                diagnostics[name] = {method: {
                    "baseline_exact": bool(np.array_equal(ranked, reference_orders["baseline"])),
                    "dense_exact": bool(np.array_equal(ranked, reference_orders[method])),
                    "dense_top20_overlap": len(set(ranked[:20]) & set(reference_orders[method][:20])) / min(20, len(ranked)),
                } for method, ranked in orders.items()}
                if class_id is None and not all(item["baseline_exact"] for item in diagnostics[name].values()):
                    raise AssertionError("unrouted query changed baseline")
            rows.append({**{key: query[key] for key in ("query_id", "source_query_id", "group_id", "language", "text")},
                "split": row_split, "family": family, "complex_query": complex_query, "class_id": class_id,
                "diagnostic_supported": (next(r for r in data["queries"] if r["query_id"] == query["source_query_id"]).get("diagnostic_supported", False)
                                         if dataset != "photobench1047" else False),
                "metrics_by_representation": metrics, "diagnostics": diagnostics})
    storage = {REPRESENTATIONS[0]: {"images": len(dense), "bytes_per_image": dense.shape[1] * 4,
        "payload_bytes": dense.size * 4, "estimated_300000_payload_bytes": dense.shape[1] * 4 * 300000}}
    for name, packed in sparse.items():
        path = output / (dataset + "-" + name + ".npz")
        with path.open("xb") as stream:
            np.savez(stream, indices=packed.indices, values=packed.values, counts=packed.counts, threshold_bits=packed.threshold_bits)
        storage[name] = {**packed.storage(), "artifact": str(path), "sha256": digest(path.read_bytes())}
    return rows, storage


def represented(rows: list[dict], name: str) -> list[dict]:
    return [{**row, "metrics": row["metrics_by_representation"][name]} for row in rows]


def production_scores(reader: Inputs, directory: Path, data: dict, model: object) -> np.ndarray:
    """Refuse production scores without their exact content/model/vocabulary identity."""
    record = reader.json(directory / "result.json")
    expected_assets = [(str(row["asset_id"]), row["sha256"]) for row in data["assets"]]
    actual_assets = [(str(row["asset_id"]), row["sha256"]) for row in record.get("assets", [])]
    if (record.get("schema") != "ram-tagger-production-run-v1"
            or record.get("model_id") != model.model_id
            or record.get("manifest_sha256") != model.manifest_sha256
            or record.get("score_type") != "sigmoid_scores"
            or record.get("classes") != [vars(item) for item in model.classes]
            or actual_assets != expected_assets):
        raise ValueError("production score model/asset/class identity differs")
    values = fusion.array(reader, directory / "scores.npy", record["scores_sha256"])
    if (values.shape != (len(expected_assets), len(model.classes)) or not np.isfinite(values).all()
            or np.any((values < 0) | (values > 1))):
        raise ValueError("invalid production scores")
    return values


def verify_production(policy_path: Path, output: Path, runs: Path, package: Path, root: Path = ROOT) -> dict:
    """Evaluate the final saved runtime at the already chosen weight. Never select again."""
    from photo_search.tagging.model import read_model_directory
    from photo_search.tagging.policy import FUSION_WEIGHT, POLICY_ID

    if POLICY_ID != "ram-top32-fp16-rrf1-v1" or FUSION_WEIGHT != 1.0:
        raise ValueError("production policy changed after sparse calibration")
    reader = Inputs(root)
    sources = (Path(__file__).resolve(), ROOT / "tools/evaluation/album_tag_fusion.py",
               ROOT / "tools/evaluation/prepare_broad_album_data.py",
               ROOT / "tools/evaluation/album_linear_probe.py",
               ROOT / "tools/evaluation/album_light_tagger.py",
               ROOT / "src/photo_search/tagging/policy.py",
               ROOT / "src/photo_search/tagging/model.py",
               ROOT / "src/photo_search/tagging/runtime.py")
    source_hashes = {reader.key(path): digest(reader.raw(path)) for path in sources}
    frozen = verify_policy(reader, policy_path)
    output = reader.path(output)
    if output.exists():
        raise ValueError("preserve earlier production evidence")
    model = read_model_directory(reader.path(package))
    model.verify()
    plan = reader.json(BASE / "broad-tags-fusion-r2/plan.json")
    compatibility = reader.json(BASE / "compatibility-final/compatibility.json")
    if [item.english for item in model.classes] != plan["vocabularies"]["ram"]:
        raise ValueError("production class order differs from the frozen study")
    names = exact_names([item.english for item in model.classes], [item.chinese for item in model.classes])
    thresholds = np.array([item.threshold for item in model.classes], dtype=np.float32)
    output.mkdir(parents=True)
    datasets, storage = {}, {}
    for dataset in DATASETS:
        rows, storage[dataset] = evaluate_dataset(reader, plan, dataset, compatibility, names, thresholds,
                                                  output, production=(reader.path(runs), model))
        selected = [row for row in rows if row["split"] != "calibration"]
        slices = {"all_test": selected,
                  "supported_test": [row for row in selected if row["diagnostic_supported"]],
                  "routed_test": [row for row in selected if row["class_id"] is not None]}
        summaries = {name: {metric: fusion.paired_summary(represented(items, "top32_fp16"), "rrf_equal", metric)
                            for metric in POLICY["metrics"]} for name, items in slices.items()}
        datasets[dataset] = {"rows": rows, "summary": summaries, "routing": {
            "query_variants": len(rows), "routed_variants": sum(row["class_id"] is not None for row in rows),
            "unrouted_exact_baseline": all(row["diagnostics"]["top32_fp16"]["rrf_equal"]["baseline_exact"]
                                          for row in rows if row["class_id"] is None)}}
    result = {"schema": "album-tag-production-quality-v1", "status": "PASS", "model_id": model.model_id,
              "policy_id": POLICY_ID, "fixed_method": "rrf_equal", "no_reselection": True,
              "prior_policy": frozen, "datasets": datasets, "storage": storage,
              "input_sha256": reader.unchanged(), "source_sha256": source_hashes,
              "limits": ["previously inspected public datasets", "fully covered galleries below5000",
                         "partial coverage and FTS mixture quality not established", "no1660 certification"]}
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "PASS", "report": str(output / "result.json"), "model_id": model.model_id}


def evaluate(policy_path: Path, output: Path, root: Path = ROOT) -> dict:
    reader = Inputs(root)
    output = reader.path(output)
    if output.exists():
        raise ValueError("evaluation output exists; preserve original evidence")
    sources = (Path(__file__).resolve(), ROOT / "tools/evaluation/album_tag_fusion.py",
               ROOT / "tools/evaluation/prepare_broad_album_data.py", ROOT / "tools/evaluation/album_linear_probe.py",
               ROOT / "tools/evaluation/album_light_tagger.py")
    source_hashes = {reader.key(path): digest(reader.raw(path)) for path in sources}
    policy = verify_policy(reader, policy_path)
    plan = reader.json(BASE / "broad-tags-fusion-r2/plan.json")
    compatibility = reader.json(BASE / "compatibility-final/compatibility.json")
    official = BASE / "broad-tags/official/ram/data"
    lock = reader.json("tools/evaluation/album_tagging_lock.json")
    lists = []
    for filename in ("ram_tag_list.txt", "ram_tag_list_chinese.txt", "ram_tag_list_threshold.txt"):
        path = official / filename
        reader.locked(path, lock["source"]["files"]["ram/data/" + filename])
        lists.append(reader.raw(path).decode("utf-8").strip().splitlines())
    english, chinese, threshold_lines = lists
    if english != plan["vocabularies"]["ram"] or len(english) != 4585:
        raise ValueError("official class order differs from score plan")
    thresholds = np.array([float(value) for value in threshold_lines], dtype=np.float32)
    names = exact_names(english, chinese)
    output.mkdir(parents=True)
    datasets, storage, selections = {}, {}, {}
    for dataset in DATASETS:  # OI calibration is always processed before other datasets.
        rows, storage[dataset] = evaluate_dataset(reader, plan, dataset, compatibility, names, thresholds, output)
        if dataset == "openimages400":
            selections = {name: fusion.select_method(represented([row for row in rows if row["split"] == "calibration"], name))
                          for name in REPRESENTATIONS}
        summaries = {}
        for name in REPRESENTATIONS:
            selected_method = selections[name]["chosen_method"]
            slices = {"all_test": [row for row in rows if row["split"] != "calibration"],
                      "routed_test": [row for row in rows if row["split"] != "calibration" and row["class_id"] is not None],
                      "supported_test": [row for row in rows if row["split"] != "calibration" and row["diagnostic_supported"]],
                      "unrouted_test": [row for row in rows if row["split"] != "calibration" and row["class_id"] is None]}
            summaries[name] = {slice_name: {metric: fusion.paired_summary(represented(selected, name), selected_method, metric)
                                          for metric in POLICY["metrics"]} for slice_name, selected in slices.items()}
            diagnostic_rows = [row for row in rows if row["split"] != "calibration" and row["class_id"] is not None]
            summaries[name]["dense_comparison_same_weight"] = {
                "method": selected_method, "routed_query_variants": len(diagnostic_rows),
                "exact_rankings": sum(row["diagnostics"][name][selected_method]["dense_exact"] for row in diagnostic_rows),
                "mean_top20_overlap": float(np.mean([row["diagnostics"][name][selected_method]["dense_top20_overlap"] for row in diagnostic_rows])) if diagnostic_rows else None,
                "dense_metrics_same_weight": {metric: fusion.paired_summary(represented(slices["all_test"], REPRESENTATIONS[0]), selected_method, metric)
                                              for metric in POLICY["metrics"]}}
        datasets[dataset] = {"rows": rows, "summary": summaries,
            "routing": {"query_variants": len(rows), "routed_variants": sum(row["class_id"] is not None for row in rows),
                        "unrouted_exact_baseline": all(row["diagnostics"][name][method]["baseline_exact"]
                            for row in rows if row["class_id"] is None for name in REPRESENTATIONS for method in METHODS)}}
    record = {"schema": "album-tag-product-policy-result-v1", "status": "PASS", "policy": policy,
              "policy_sha256": digest(reader.raw(policy_path)), "selection": selections, "datasets": datasets,
              "storage": storage, "source_sha256": source_hashes, "input_sha256": reader.unchanged(),
              "limits": ["Previously inspected data; no untouched holdout claim", "No real library or new model inference",
                         "Positive-only COCO cannot measure precision", "Unknown OpenImages labels are not negatives",
                         "Whole-gallery offline ranking below5000; candidate-union truncation at300k not validated",
                         "No GTX1660 or300k latency/inference certification", "Sparse payload excludes database/index overhead"]}
    (output / "result.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "PASS", "selection": selections, "report": str(output / "result.json"),
            "routing": {name: item["routing"] for name, item in datasets.items()}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "evaluate", "verify-production"))
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--production-runs", type=Path)
    parser.add_argument("--package-dir", type=Path)
    args = parser.parse_args()
    if args.command != "freeze" and args.out_dir is None:
        parser.error("evaluate requires --out-dir")
    if args.command == "verify-production":
        if args.production_runs is None or args.package_dir is None:
            parser.error("verify-production requires --production-runs and --package-dir")
        print(json.dumps(verify_production(args.policy, args.out_dir, args.production_runs, args.package_dir)))
        return
    print(json.dumps(freeze(args.policy) if args.command == "freeze" else evaluate(args.policy, args.out_dir),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
