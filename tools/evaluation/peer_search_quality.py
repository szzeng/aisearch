"""CPU replay of predeclared peer-inspired retrieval alternatives.

Fit on tuning, freeze identities and calibration, then evaluate holdout in a separate
invocation. Existing public-search-quality runs supply vectors: no model is loaded and
no product catalog is opened. Paired-image retrieval and threshold diagnostics do not
measure exhaustive semantic precision. The previously observed 240-image corpus can
only be exploratory; confirmation also requires an explicitly excluded prior dataset.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from tools.evaluation import public_search_quality as public

VARIANTS = (
    "clip_center", "clip_letterbox", "clip_two_view_max", "clip_mean_vector",
    "qwen512", "equal_rrf", "equal_zscore",
)
CHANNELS = ("clip_center", "qwen512")
OBSERVED_DATASET_SHA = "bd6e53421452b9b6652822d4c265ce173e25c41f5be6e0752151b01f11a543dc"
LIMITS = [
    "Known paired image is the positive; other images are not exhaustively judged.",
    "Threshold results are target retention and candidate counts, not semantic precision.",
    "Image-disjoint galleries are small; results are not a 300000-image guarantee.",
    "Public benchmark overlap with model training cannot be ruled out.",
    "CPU vector replay does not measure model inference or product end-to-end latency.",
    "Bootstrap intervals are marginal; no multiple-comparison correction is applied.",
]


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value


def write_new_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def validate_plan(plan: dict[str, Any]) -> None:
    required = {
        "schema": "peer-search-plan-v1", "variants": list(VARIANTS),
        "fusion_inputs": list(CHANNELS), "rrf_k": 60,
        "threshold_channels": list(CHANNELS), "target_retention": .95,
        "borrowed_threshold": .175, "bootstrap_repeats": 10000,
        "seed": 20261016, "wide_aspect_ratio": 1.5,
        "zscore_fit": (
            "global mean and population standard deviation of each channel's "
            "tuning-only query-gallery score matrix"
        ),
    }
    if any(plan.get(key) != value for key, value in required.items()):
        raise ValueError("plan differs from the predeclared comparison")


def validate_dataset(data: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    if data.get("schema") != "public-search-quality-dataset-v1":
        raise ValueError("expected public-search-quality dataset schema")
    images = data.get("images", [])
    ids, hashes = set(), set()
    for row in images:
        key = row.get("id")
        digest = row.get("sha256", "")
        if not isinstance(key, str) or not key or key in ids:
            raise ValueError("duplicate or invalid image ID")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or digest in hashes:
            raise ValueError("duplicate or invalid image content hash")
        if row.get("split") not in {"tuning", "holdout"}:
            raise ValueError("invalid image split")
        captions = row.get("captions", [])
        if (not isinstance(captions, list) or not captions
                or any(not isinstance(text, str) or not text.strip() for text in captions)):
            raise ValueError("image needs nonempty captions")
        for dimension in ("width", "height"):
            if type(row.get("coco", {}).get(dimension)) is not int or row["coco"][dimension] <= 0:
                raise ValueError("image needs positive integer dimensions")
        ids.add(key)
        hashes.add(digest)
    if {row["split"] for row in images} != {"tuning", "holdout"}:
        raise ValueError("both nonempty image-disjoint splits are required")
    queries = [
        {"id": f"{row['id']}#{index}", "image_id": row["id"],
         "split": row["split"], "text": caption}
        for row in images for index, caption in enumerate(row["captions"])
    ]
    return images, queries


def validate_vectors(vectors: dict[str, np.ndarray], shapes: dict[str, tuple[int, int]]) -> None:
    if set(vectors) != set(shapes):
        raise ValueError("unexpected vector array keys")
    for key, shape in shapes.items():
        value = vectors[key]
        if (
            value.shape != shape or value.dtype != np.float32
            or not np.isfinite(value).all()
            or not np.allclose(np.linalg.norm(value, axis=1), 1, atol=1e-5, rtol=0)
        ):
            raise ValueError(f"invalid shape/dtype/normalization for {key}")


def load_inputs(
    dataset_path: Path, clip_run: Path, qwen_run: Path, plan_path: Path,
    *, mode: str, exclude_dataset: Path | None = None,
) -> dict[str, Any]:
    plan = read_json(plan_path)
    validate_plan(plan)
    data = read_json(dataset_path)
    images, queries = validate_dataset(data)
    dataset_hash = public.sha256(dataset_path)
    if mode not in {"exploratory", "confirmation"}:
        raise ValueError("unknown evidence mode")
    if mode == "confirmation" and (dataset_hash == OBSERVED_DATASET_SHA or not exclude_dataset):
        raise ValueError("confirmation needs a new dataset and explicit prior-dataset exclusion")
    files = {"dataset": dataset_path, "plan": plan_path, "tool": Path(__file__),
             "public_tool": Path(public.__file__)}
    if exclude_dataset:
        prior = read_json(exclude_dataset)
        prior_images, _ = validate_dataset(prior)
        if (
            {row["id"] for row in images} & {row["id"] for row in prior_images}
            or {row["sha256"] for row in images} & {row["sha256"] for row in prior_images}
        ):
            raise ValueError("dataset overlaps the excluded prior images")
        files["excluded_dataset"] = exclude_dataset
    runs = {}
    for model, directory in (("clip", clip_run), ("qwen", qwen_run)):
        result = read_json(directory / "result.json")
        execution = read_json(directory / "execution.json")
        if (
            result.get("status") != "ok" or result.get("model") != model
            or execution.get("status") != "ok" or execution.get("exit_code") != 0
            or result.get("dataset_sha256") != dataset_hash
        ):
            raise ValueError(f"{model} run status or dataset fingerprint mismatch")
        dimension = 512 if model == "clip" else 2048
        if result.get("model_spec", {}).get("dimension") != dimension:
            raise ValueError(f"{model} run has the wrong vector-space dimension")
        shapes = {"queries": (len(queries), dimension)}
        for name in (("center", "letterbox") if model == "clip" else ("native",)):
            shapes[name] = (len(images), dimension)
        with np.load(directory / "vectors.npz", allow_pickle=False) as archive:
            vectors = {key: archive[key] for key in archive.files}
        validate_vectors(vectors, shapes)
        expected_variants = ("center", "letterbox", "two_view_max") if model == "clip" else (
            "512", "1024", "2048",
        )
        for variant in expected_variants:
            for split in ("tuning", "holdout"):
                expected = [query for query in queries if query["split"] == split]
                recorded = result.get("variants", {}).get(variant, {}).get(split, {})
                rows = recorded.get("per_query", [])
                if len(rows) != len(expected) or any(
                    any(row.get(key) != query[key] for key in ("id", "image_id", "split", "text"))
                    for row, query in zip(rows, expected, strict=True)
                ):
                    raise ValueError(f"{model}/{variant}/{split} query order or identity mismatch")
                if recorded.get("gallery_images") != sum(row["split"] == split for row in images):
                    raise ValueError("recorded gallery does not match the image-disjoint split")
        runs[model] = {"vectors": vectors, "result": result}
        for name in ("result.json", "execution.json", "vectors.npz"):
            files[f"{model}/{name}"] = directory / name
    identities = {key: public.sha256(path) for key, path in sorted(files.items())}
    identities.update({"runtime/python_version": sys.version, "runtime/numpy_version": np.__version__})
    return {"images": images, "queries": queries, "runs": runs, "plan": plan, "mode": mode,
            "identities": identities}


def base_scores(inputs: dict[str, Any], split: str) -> tuple[dict[str, np.ndarray], list, list]:
    if split not in {"tuning", "holdout"}:
        raise ValueError("unknown split")
    queries = inputs["queries"]
    images = inputs["images"]
    qi = [index for index, row in enumerate(queries) if row["split"] == split]
    ii = [index for index, row in enumerate(images) if row["split"] == split]
    clip = inputs["runs"]["clip"]["vectors"]
    qwen = inputs["runs"]["qwen"]["vectors"]
    q = clip["queries"][qi]
    center = q @ clip["center"][ii].T
    letterbox = q @ clip["letterbox"][ii].T
    scores = {
        "clip_center": center, "clip_letterbox": letterbox,
        "clip_two_view_max": np.maximum(center, letterbox),
        "clip_mean_vector": q @ public.normalized(clip["center"][ii] + clip["letterbox"][ii]).T,
        "qwen512": public.normalized(qwen["queries"][qi, :512])
        @ public.normalized(qwen["native"][ii, :512]).T,
    }
    return scores, [queries[index] for index in qi], [images[index] for index in ii]


def verify_baselines(inputs: dict, scores: dict, queries: list, images: list, split: str) -> None:
    for name, model, variant in (
        ("clip_center", "clip", "center"), ("clip_letterbox", "clip", "letterbox"),
        ("clip_two_view_max", "clip", "two_view_max"), ("qwen512", "qwen", "512"),
    ):
        actual = public.ranked_metrics(scores[name], queries, [row["id"] for row in images])
        recorded = inputs["runs"][model]["result"]["variants"][variant][split]
        if any(
            row["rank"] != old["rank"] or row["top20"] != old["top20"]
            for row, old in zip(actual["per_query"], recorded["per_query"], strict=True)
        ) or any(
            not math.isclose(actual[key], recorded[key], abs_tol=1e-9)
            for key in (
                "queries", "gallery_images", "recall_at_1", "recall_at_5",
                "recall_at_10", "recall_at_50", "mrr", "median_rank",
            )
        ):
            raise ValueError(f"saved vectors do not reproduce {model}/{variant}/{split} baseline")


def paired_target_scores(scores: np.ndarray, queries: list, images: list) -> np.ndarray:
    columns = {row["id"]: index for index, row in enumerate(images)}
    return scores[np.arange(len(queries)), [columns[row["image_id"]] for row in queries]]


def retained_threshold(values: np.ndarray, target: float = .95) -> float:
    values = np.asarray(values, dtype=np.float64)
    if values.ndim != 1 or not len(values) or not np.isfinite(values).all() or not 0 < target <= 1:
        raise ValueError("invalid threshold fitting targets")
    # Largest observed cutoff retaining at least ceil(target*n) known positive pairs.
    index = len(values) - math.ceil(target * len(values))
    return float(np.sort(values)[index])


def fit_calibration(scores: dict, queries: list, images: list) -> dict[str, Any]:
    scales, thresholds = {}, {}
    for name in CHANNELS:
        values = scores[name].astype(np.float64)
        mean, std = float(values.mean()), float(values.std())
        if not math.isfinite(std) or std <= 0:
            raise ValueError("cannot calibrate a constant or invalid score channel")
        scales[name] = {"mean": mean, "std": std, "pairs": int(values.size)}
        thresholds[name] = retained_threshold(paired_target_scores(values, queries, images))
    return {"selected_on": "tuning", "zscore": scales, "thresholds": thresholds}


def stable_ranks(scores: np.ndarray) -> np.ndarray:
    order = np.argsort(-scores, axis=1, kind="stable")
    ranks = np.empty_like(order)
    np.put_along_axis(ranks, order, np.arange(1, scores.shape[1] + 1)[None, :], axis=1)
    return ranks


def add_fusions(scores: dict[str, np.ndarray], calibration: dict) -> dict[str, np.ndarray]:
    result = dict(scores)
    result["equal_rrf"] = sum(1 / (60.0 + stable_ranks(scores[name])) for name in CHANNELS)
    result["equal_zscore"] = sum(
        (scores[name].astype(np.float64) - calibration["zscore"][name]["mean"])
        / calibration["zscore"][name]["std"] / 2 for name in CHANNELS
    )
    return result


def threshold_diagnostic(scores: np.ndarray, queries: list, images: list, cutoff: float) -> dict:
    counts = (scores >= cutoff).sum(axis=1)
    targets = paired_target_scores(scores, queries, images)
    return {
        "cosine_cutoff": cutoff, "queries": len(queries), "gallery_images": len(images),
        "known_pair_target_retention": float((targets >= cutoff).mean()),
        "candidate_count_mean": float(counts.mean()),
        "candidate_count_median": float(np.median(counts)),
        "candidate_count_p95": float(np.quantile(counts, .95)),
        "candidate_count_min": int(counts.min()), "candidate_count_max": int(counts.max()),
        "limits": "Other candidates are not labelled irrelevant; these are not precision scores.",
    }


def paired_bootstrap(before: list[dict], after: list[dict], *, repeats: int = 10000) -> dict:
    groups: dict[str, list] = {}
    gained = lost = 0
    for old, new in zip(before, after, strict=True):
        if old["id"] != new["id"] or old["image_id"] != new["image_id"]:
            raise ValueError("paired comparison query identity mismatch")
        delta = int(new["rank"] == 1) - int(old["rank"] == 1)
        gained += delta == 1
        lost += delta == -1
        group = groups.setdefault(old["image_id"], [0., 0., 0])
        group[0] += delta
        group[1] += 1 / new["rank"] - 1 / old["rank"]
        group[2] += 1
    values = np.asarray(list(groups.values()))
    if len(values) == 0:
        raise ValueError("cannot bootstrap an empty stratum")
    rng = np.random.default_rng(20261016)
    # Bounded batches avoid a repeat*gallery*3 temporary at larger gallery sizes.
    draws = []
    for start in range(0, repeats, 256):
        sample = values[rng.integers(0, len(values), size=(min(256, repeats - start), len(values)))]
        totals = sample.sum(axis=1)
        draws.append(totals[:, :2] / totals[:, 2, None])
    sampled = np.concatenate(draws)
    return {
        "image_groups": len(groups), "queries": len(before), "repeats": repeats,
        "unit": "image; all captions for a sampled image stay together", "seed": 20261016,
        "gained_top1_queries": int(gained), "lost_top1_queries": int(lost),
        "delta_top1_percentage_points": float(values[:, 0].sum() / values[:, 2].sum() * 100),
        "top1_95_percent_interval_pp": (np.quantile(sampled[:, 0], [.025, .975]) * 100).tolist(),
        "delta_mrr": float(values[:, 1].sum() / values[:, 2].sum()),
        "mrr_95_percent_interval": np.quantile(sampled[:, 1], [.025, .975]).tolist(),
    }


def evaluate_split(inputs: dict, calibration: dict, split: str) -> dict:
    scores, queries, images = base_scores(inputs, split)
    verify_baselines(inputs, scores, queries, images, split)
    scores = add_fusions(scores, calibration)
    variants = {
        name: public.ranked_metrics(matrix, queries, [row["id"] for row in images])
        for name, matrix in scores.items()
    }
    wide_ids = {
        row["id"] for row in images
        if max(row["coco"]["width"], row["coco"]["height"])
        / min(row["coco"]["width"], row["coco"]["height"]) >= 1.5
    }
    comparisons = {}
    for name in VARIANTS[1:]:
        reference = "qwen512" if name.startswith("equal_") else "clip_center"
        comparisons[name] = {"reference": reference, "strata": {}}
        for stratum in ("all", "wide_or_tall", "other_aspect"):
            def included(row: dict) -> bool:
                return (stratum == "all" or (row["image_id"] in wide_ids)
                        == (stratum == "wide_or_tall"))
            before = [row for row in variants[reference]["per_query"] if included(row)]
            after = [row for row in variants[name]["per_query"] if included(row)]
            comparisons[name]["strata"][stratum] = (
                paired_bootstrap(before, after) if before else {"queries": 0, "status": "empty"}
            )
    diagnostics = {
        name: {
            "tuning_fitted_95_percent": threshold_diagnostic(
                scores[name], queries, images, calibration["thresholds"][name],
            ),
            "borrowed_ente_0_175": threshold_diagnostic(scores[name], queries, images, .175),
        }
        for name in CHANNELS
    }
    clip_rows = variants["clip_center"]["per_query"]
    qwen_rows = variants["qwen512"]["per_query"]
    complementarity = {
        "both_top1_correct": sum(a["rank"] == 1 and b["rank"] == 1
                                 for a, b in zip(clip_rows, qwen_rows, strict=True)),
        "only_clip_top1_correct": sum(a["rank"] == 1 and b["rank"] != 1
                                      for a, b in zip(clip_rows, qwen_rows, strict=True)),
        "only_qwen_top1_correct": sum(a["rank"] != 1 and b["rank"] == 1
                                      for a, b in zip(clip_rows, qwen_rows, strict=True)),
        "neither_top1_correct": sum(a["rank"] != 1 and b["rank"] != 1
                                    for a, b in zip(clip_rows, qwen_rows, strict=True)),
    }
    return {"variants": variants, "comparisons": comparisons, "thresholds": diagnostics,
            "complementarity": complementarity, "baseline_replay": "exact rankings matched"}


def fit(inputs: dict, output: Path) -> dict:
    scores, queries, images = base_scores(inputs, "tuning")
    verify_baselines(inputs, scores, queries, images, "tuning")
    frozen = {
        "schema": "peer-search-frozen-v1", "mode": inputs["mode"],
        "identities": inputs["identities"], "calibration": fit_calibration(scores, queries, images),
        "fit_scope": "tuning query-gallery scores only; no holdout metrics evaluated",
        "plan": inputs["plan"], "limits": LIMITS,
    }
    write_new_json(output, frozen)
    return {"status": "ok", "frozen": str(output), "mode": inputs["mode"]}


def evaluate(inputs: dict, frozen_path: Path, output: Path) -> dict:
    frozen = read_json(frozen_path)
    if (frozen.get("schema") != "peer-search-frozen-v1"
            or frozen.get("identities") != inputs["identities"]
            or frozen.get("mode") != inputs["mode"]
            or frozen.get("plan") != inputs["plan"]):
        raise ValueError("frozen input/code/plan identities changed; do not silently refit")
    tuning, queries, images = base_scores(inputs, "tuning")
    if frozen.get("calibration") != fit_calibration(tuning, queries, images):
        raise ValueError("frozen calibration was modified or does not match tuning")
    result = {
        "schema": "peer-search-comparison-v1", "status": "ok", "mode": inputs["mode"],
        "identities": inputs["identities"], "frozen_sha256": public.sha256(frozen_path),
        "calibration": frozen["calibration"], "limits": LIMITS,
        "splits": {split: evaluate_split(inputs, frozen["calibration"], split)
                   for split in ("tuning", "holdout")},
    }
    write_new_json(output, result)
    return {"status": "ok", "result": str(output), "mode": inputs["mode"],
            "holdout_top1": {name: row["recall_at_1"]
                             for name, row in result["splits"]["holdout"]["variants"].items()}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fit", "evaluate"))
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--clip-run", type=Path, required=True)
    parser.add_argument("--qwen-run", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--mode", choices=("exploratory", "confirmation"), required=True)
    parser.add_argument("--exclude-dataset", type=Path)
    parser.add_argument("--frozen", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = time.perf_counter()
    try:
        if args.output.exists():
            raise FileExistsError("preserve existing evidence; choose a new output path")
        if (args.command == "evaluate") != (args.frozen is not None):
            raise ValueError("--frozen is required only for evaluate")
        inputs = load_inputs(args.dataset, args.clip_run, args.qwen_run, args.plan,
                             mode=args.mode, exclude_dataset=args.exclude_dataset)
        result = fit(inputs, args.output) if args.command == "fit" else evaluate(
            inputs, args.frozen, args.output,
        )
    except Exception as error:
        print(json.dumps({"status": "failed", "exit_code": 1,
                          "error_type": type(error).__name__, "message": str(error)},
                         ensure_ascii=False), file=sys.stderr)
        return 1
    result.update({"exit_code": 0, "elapsed_seconds": time.perf_counter() - started})
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
