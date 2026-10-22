"""Query-independent detector regions; bounded offline experiment, no GT crops.

Prepare reads only sample and model predictions. Evaluate consumes labels only
after encoding. Complete image membership is retained with max pooling.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

from tools.evaluation.budget_rank_fusion import Reader
from tools.evaluation.budget_search_metrics import (
    detector_from_record,
    embeddings_from_record,
    evaluate_rankings,
)
from tools.evaluation.prepare_wemm_coco import controlled_output, write_json
from tools.evaluation.wemm_pilot_core import deterministic_rank, normalize_prefix
from tools.evaluation.wemm_pilot_runtime import decode_image, decoder_identity, sha256

POLICY = {"score_min": 0.5, "min_area": 0.005, "max_area": 0.8,
          "min_edge": 16, "max_iou": 0.5, "padding": 0.1, "max_regions": 2,
          "selection": "all80classes-score-descending-independent-of-query",
          "pooling": "max-whole-and-regions"}


def iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2]-a[0]) * (a[3]-a[1]) + (b[2]-b[0]) * (b[3]-b[1]) - intersection
    return intersection / union if union > 0 else 0.0


def select_regions(detections: list[dict], width: int, height: int) -> list[dict]:
    if width <= 0 or height <= 0 or len(detections) > 300:
        raise ValueError("invalid detection dimensions/count")
    proposals = []
    for index, row in enumerate(detections):
        box, score = np.asarray(row["xyxy"], dtype=float), float(row["score"])
        if (box.shape != (4,) or not np.isfinite(box).all()
                or not math.isfinite(score) or not 0 <= score <= 1
                or (box[:2] < 0).any() or box[2] > width or box[3] > height
                or (box[2:] < box[:2]).any()):
            raise ValueError("invalid detector box/score")
        w, h = box[2:] - box[:2]
        if (score < POLICY["score_min"] or min(w, h) < POLICY["min_edge"]
                or not POLICY["min_area"] <= w*h/(width*height) <= POLICY["max_area"]):
            continue
        proposals.append((score, index, box))
    selected = []
    for score, index, box in sorted(proposals, key=lambda row: (-row[0], row[1])):
        if any(iou(box, row["box"]) > POLICY["max_iou"] for row in selected):
            continue
        w, h = box[2:] - box[:2]
        crop = [max(0, math.floor(box[0] - .1*w)), max(0, math.floor(box[1] - .1*h)),
                min(width, math.ceil(box[2] + .1*w)), min(height, math.ceil(box[3] + .1*h))]
        selected.append({"box": box.tolist(), "crop": crop,
                         "detector_index": index, "score": score})
        if len(selected) == POLICY["max_regions"]:
            break
    return selected


def prepare(sample_path: Path, detector_path: Path, query_path: Path, out: Path) -> dict:
    reader = Reader()
    sample, queries = reader.json(sample_path), reader.json(query_path)
    detector = json.loads(reader.raw(detector_path, max_bytes=128 * 1024 * 1024))
    identities = [{k: row[k] for k in ("asset_id", "sha256")} for row in sample["assets"]]
    if not 1 <= len(identities) <= 1000:
        raise ValueError("region experiment budget exceeded")
    detector_from_record(detector, identities, reader.hashes[sample_path.resolve()], sample["assets"])
    if detector["spec"]["decoder"] != decoder_identity():
        raise ValueError("detector used a different decoder")
    out = controlled_output(out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "images").mkdir()
    plan = {"schema": "album-regions-v1", "policy": POLICY, "parents": identities,
            "source_sample_sha256": reader.hashes[sample_path.resolve()],
            "detector_sha256": reader.hashes[detector_path.resolve()],
            "regions": [], "preparation_ms": [], "detector_spec": detector["spec"],
            "detector_total_ms": sum(row["decode_ms"] + row["detect_ms"] for row in detector["images"])}
    write_json(out / "frozen-policy.json", {"policy": POLICY, "inputs": reader.verify_unchanged()})
    crop_assets, content = [], {}
    for parent, prediction in zip(sample["assets"], detector["images"], strict=True):
        started = time.perf_counter()
        regions = select_regions(prediction["detections"], prediction["decoded"]["width"],
                                 prediction["decoded"]["height"])
        if regions:
            image, decoded = decode_image(Path(parent["path"]), parent["sha256"])
            try:
                if decoded != prediction["decoded"]:
                    raise ValueError("decoded image differs from detector input")
                for index, region in enumerate(regions):
                    crop = image.crop(region["crop"])
                    asset_id = f"{parent['asset_id']}:region{index}"
                    destination = out / "images" / f"{len(plan['regions']):06d}.png"
                    try:
                        crop.save(destination, format="PNG")
                    finally:
                        crop.close()
                    digest = sha256(destination)
                    if digest in content:
                        destination.unlink()  # only this just-written duplicate experiment file
                        asset_id = content[digest]
                    else:
                        content[digest] = asset_id
                        crop_assets.append({"asset_id": asset_id, "sha256": digest,
                                            "path": str(destination)})
                    plan["regions"].append({"parent_id": parent["asset_id"], "asset_id": asset_id,
                                            "sha256": digest, **region})
            finally:
                image.close()
        plan["preparation_ms"].append((time.perf_counter() - started)*1000)
    plan["source_hashes"] = reader.verify_unchanged()
    plan["tool_sha256"] = sha256(Path(__file__))
    write_json(out / "sample.json", {"schema": "wemm-pilot-public-sample-v1", "assets": crop_assets,
                                     "source_identity": {"region_plan": plan}})
    write_json(out / "queries.json", queries)
    write_json(out / "plan.json", plan)
    return {"parents": len(identities), "regions": len(plan["regions"]), "unique_crops": len(crop_assets)}


def pool_scores(whole, crops, parent_ids, region_parent_ids):
    if (whole.ndim != 2 or crops.ndim != 2 or whole.shape[0] != crops.shape[0]
            or whole.shape[1] != len(parent_ids) or crops.shape[1] != len(region_parent_ids)
            or len(set(parent_ids)) != len(parent_ids)
            or not np.isfinite(whole).all() or not np.isfinite(crops).all()):
        raise ValueError("invalid pooling matrices/membership")
    positions = {asset_id: i for i, asset_id in enumerate(parent_ids)}
    result = whole.copy()
    for index, parent in enumerate(region_parent_ids):
        if parent not in positions:
            raise ValueError("unknown region parent")
        position = positions[parent]
        result[:, position] = np.maximum(result[:, position], crops[:, index])
    return result


def evaluate(whole_dir: Path, crop_dir: Path, plan_path: Path, labels_path: Path, out: Path):
    reader = Reader()
    plan = reader.json(plan_path)
    crop_sample_path = plan_path.with_name("sample.json")
    crop_sample = reader.json(crop_sample_path)
    if plan.get("policy") != POLICY or plan.get("schema") != "album-regions-v1":
        raise ValueError("region policy differs from frozen experiment")
    runs, matrices = [], []
    for directory in (whole_dir, crop_dir):
        record = reader.json(directory / "result.json")
        values = [reader.matrix(directory / name, record["vectors_sha256"][name])
                  for name in ("images.npy", "queries.npy")]
        embeddings_from_record(record, *values)
        runs.append(record)
        matrices.append(values)
    whole, crop = runs
    if whole["model"] not in ("qwen", "fgclip-base-adaptive"):
        raise ValueError("only frozen main models supported")
    for key in ("model", "dtype", "model_lock", "decoder", "packages", "queries", "source_sha256"):
        if whole[key] != crop[key]:
            raise ValueError("incompatible crop/whole feature semantics: " + key)
    settings = [{k: v for k, v in run["settings"].items() if k != "limit"} for run in runs]
    if settings[0] != settings[1]:
        raise ValueError("incompatible crop/whole preprocessing")
    if not np.allclose(matrices[0][1], matrices[1][1], atol=1e-6, rtol=0):
        raise ValueError("query encodings changed between whole and crop runs")
    identities = [{k: row[k] for k in ("asset_id", "sha256")} for row in whole["assets"]]
    if identities != plan["parents"] or whole["sample_sha256"] != plan["source_sample_sha256"]:
        raise ValueError("region plan belongs to different original images")
    validate_plan_binding(plan, crop_sample, crop, reader.hashes[crop_sample_path.resolve()])
    crop_lookup = {row["asset_id"]: (i, row["sha256"]) for i, row in enumerate(crop["assets"])}
    region_indices = []
    for region in plan["regions"]:
        index, digest = crop_lookup[region["asset_id"]]
        if region["sha256"] != digest:
            raise ValueError("crop content changed")
        region_indices.append(index)
    if set(region_indices) != set(range(len(crop_lookup))):
        raise ValueError("unaccounted crop vector")
    dimension = 1024 if whole["model"] == "qwen" else 768
    q = normalize_prefix(matrices[0][1], dimension)
    original = q @ normalize_prefix(matrices[0][0], dimension).T
    local = q @ normalize_prefix(matrices[1][0], dimension)[region_indices].T
    ids = [row["asset_id"] for row in identities]
    pooled = pool_scores(original, local, ids, [row["parent_id"] for row in plan["regions"]])
    rankings = {name: {query["query_id"]: deterministic_rank(scores[i], ids)
                       for i, query in enumerate(whole["queries"])}
                for name, scores in (("whole", original), ("whole_plus_regions", pooled))}
    labels = reader.json(labels_path)
    result = evaluate_rankings(rankings, labels, identities, whole["queries"])
    result.update(status="COMPLETE", dimension=dimension, model=whole["model"], policy=POLICY,
                  cost={"regions": len(plan["regions"]), "unique_crops": len(crop_lookup),
                        "region_vector_bytes": len(plan["regions"])*dimension*4,
                        "detector_total_ms": plan["detector_total_ms"],
                        "crop_preparation_total_ms": sum(plan["preparation_ms"]),
                        "region_encoding_total_ms": sum(crop["images_ms"]),
                        "whole_encoding_total_ms": sum(whole["images_ms"])},
                  inputs_sha256=reader.verify_unchanged(), tool_sha256=sha256(Path(__file__)),
                  source_identities=[run["source_sha256"] for run in runs],
                  limitations=["Max pooling can promote false positives; no fitted per-query threshold.",
                               "Prior public sample reused; this is not a new independent test set.",
                               "Model pretraining overlap is unknown; not a 1660 measurement."])
    out = controlled_output(out)
    if out.exists():
        raise ValueError("fresh result required")
    write_json(out, result)
    return {"status": result["status"], "cost": result["cost"],
            "AP": {name: {query: row["AP"] for query, row in rows.items()}
                   for name, rows in result["results"].items()}}


def validate_plan_binding(plan, sample, run, sample_sha):
    if (sample.get("source_identity", {}).get("region_plan") != plan
            or run.get("assets") != sample.get("assets")
            or run.get("sample_sha256") != sample_sha):
        raise ValueError("crop encoding is not bound to the original region plan")
    counts = {row["asset_id"]: 0 for row in plan["parents"]}
    for region in plan["regions"]:
        if region["parent_id"] not in counts:
            raise ValueError("unknown region parent")
        counts[region["parent_id"]] += 1
    if any(count > POLICY["max_regions"] for count in counts.values()):
        raise ValueError("too many regions for parent")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    p = actions.add_parser("prepare")
    for arg in ("sample", "detector", "queries", "out"):
        p.add_argument("--" + arg, required=True, type=Path)
    p = actions.add_parser("evaluate")
    for arg in ("whole-dir", "crop-dir", "plan", "labels", "out"):
        p.add_argument("--" + arg, required=True, type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        result = prepare(args.sample, args.detector, args.queries, args.out)
    else:
        result = evaluate(args.whole_dir, args.crop_dir, args.plan, args.labels, args.out)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
