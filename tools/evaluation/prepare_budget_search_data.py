"""Freeze a bounded Open Images validation pilot with explicit unknown labels.

Public metadata is hash-pinned. A missing class label is never a negative;
human-verified negative ancestors may imply a negative descendant. Ground-truth
boxes are evaluation-only and are not included in the encoder input manifest.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import re
import time
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from tools.evaluation.prepare_wemm_coco import controlled_output, write_frozen_json, write_json
from tools.evaluation.public_search_quality import sha256

SEED = 20261021
QUERIES = [{"query_id": "cat", "text": "猫"}, {"query_id": "dog", "text": "狗"}]
CLASSES = {"cat": "/m/01yrx", "dog": "/m/0bt9lr"}
QUOTAS = {"cat_positive": 100, "dog_positive": 100, "shared_background": 120,
          "cat_explicit_negative": 40, "dog_explicit_negative": 40}
METADATA = {
    "metadata.csv": (
        "https://storage.googleapis.com/openimages/2018_04/validation/validation-images-with-rotation.csv",
        "ed93a0e121fe345effdfc7359b848dbc64a1ff6778c8c73563157cb500b33a17"),
    "labels.csv": (
        "https://storage.googleapis.com/openimages/v5/validation-annotations-human-imagelabels-boxable.csv",
        "d5ff236d3cdbba36dac1ee6f7fdf9a26934205cbec5b933b0872722e2b6d3417"),
    "classes.csv": (
        "https://storage.googleapis.com/openimages/v7/oidv7-class-descriptions-boxable.csv",
        "1839e0e7e84130ae281f7f67413768601b031581c0c42e7fc17527b8e2a99aa9"),
    "boxes.csv": (
        "https://storage.googleapis.com/openimages/v5/validation-annotations-bbox.csv",
        "d8bbd59410af14835d7733165a7bb8a3f0213981b22dd5077b0b9f7878991ff2"),
    "hierarchy.json": (
        "https://storage.googleapis.com/openimages/2018_04/bbox_labels_600_hierarchy.json",
        "a28f06e83c8378c4285d92d768ceda7568a48cac3c02994b6c346df804e7d612"),
}
IMAGE_URL = "https://open-images-dataset.s3.amazonaws.com/validation/{image_id}.jpg"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_DECODED_PIXELS = 64_000_000


def fetch_bytes(url: str, cap: int) -> bytes:
    """Bound response size, including servers that omit Content-Length."""
    with urllib.request.urlopen(url, timeout=45) as response:
        declared = response.headers.get("Content-Length")
        if response.status != 200 or (declared is not None and int(declared) > cap):
            raise ValueError("unexpected or oversized public response")
        raw = response.read(cap + 1)
    if not raw or len(raw) > cap or (declared is not None and len(raw) != int(declared)):
        raise ValueError("empty, truncated or oversized public response")
    return raw


def fetch_metadata(directory: Path) -> dict[str, Any]:
    directory = controlled_output(directory)
    directory.mkdir(parents=True, exist_ok=True)
    identity = {}
    for name, (url, expected) in METADATA.items():
        path = controlled_output(directory / name)
        if not path.exists():
            raw = fetch_bytes(url, 64 * 1024 * 1024)
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError(f"official metadata differs from fixed revision: {name}")
            path.write_bytes(raw)
        if path.stat().st_size > 64 * 1024 * 1024 or sha256(path) != expected:
            raise ValueError(f"cached metadata changed: {name}")
        identity[name] = {"url": url, "sha256": expected, "bytes": path.stat().st_size}
    return identity


def ancestors_of(hierarchy: dict[str, Any], target: str) -> set[str]:
    found: set[str] = set()
    occurrences = 0

    def visit(node: dict[str, Any], ancestors: tuple[str, ...]) -> None:
        nonlocal occurrences
        label = node["LabelName"]
        if label in ancestors:
            raise ValueError("class hierarchy cycle")
        if label == target:
            occurrences += 1
            found.update(ancestors)
        for child in node.get("Subcategory", []):
            visit(child, (*ancestors, label))

    visit(hierarchy, ())
    if not occurrences:
        raise ValueError(f"class missing from hierarchy: {target}")
    return found


def resolve_label(labels: dict[str, int], target: str, ancestors: set[str]) -> dict[str, Any]:
    """Unknown remains None; negative propagation follows the official hierarchy."""
    direct = labels.get(target)
    if direct is not None and (type(direct) is not int or direct not in (0, 1)):
        raise ValueError("invalid human confidence")
    negative_ancestors = sorted(key for key in ancestors if labels.get(key) == 0)
    if direct == 1 and negative_ancestors:
        raise ValueError("conflicting direct positive and ancestor negative")
    if direct is not None:
        return {"relevant": bool(direct), "basis": "direct_human_label", "label_ids": [target]}
    if negative_ancestors:
        return {"relevant": False, "basis": "negative_ancestor", "label_ids": negative_ancestors}
    return {"relevant": None, "basis": "unknown", "label_ids": []}


def load_population(directory: Path) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    hierarchy = json.loads((directory / "hierarchy.json").read_text(encoding="utf-8"))
    ancestors = {name: ancestors_of(hierarchy, key) for name, key in CLASSES.items()}
    needed = set(CLASSES.values()).union(*ancestors.values())
    labels: dict[str, dict[str, int]] = defaultdict(dict)
    with (directory / "labels.csv").open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["LabelName"] not in needed:
                continue
            if row["Source"] not in {"verification", "crowdsource-verification"}:
                raise ValueError("unexpected nonhuman label source")
            value = int(row["Confidence"])
            if value not in (0, 1):
                raise ValueError("nonbinary human confidence")
            target = labels[row["ImageID"]]
            if row["LabelName"] in target and target[row["LabelName"]] != value:
                raise ValueError("conflicting human labels")
            target[row["LabelName"]] = value
    population = []
    seen = set()
    with (directory / "metadata.csv").open(encoding="utf-8", newline="") as stream:
        for meta in csv.DictReader(stream):
            asset_id = meta["ImageID"]
            if not re.fullmatch(r"[0-9a-f]{16}", asset_id) or asset_id in seen:
                raise ValueError("invalid or duplicate Open Images ID")
            seen.add(asset_id)
            if not re.fullmatch(r"https?://creativecommons\.org/licenses/(?:by|by-sa)/[0-9.]+/?",
                                meta["License"]):
                continue
            judgments = {name: resolve_label(labels[asset_id], key, ancestors[name])
                         for name, key in CLASSES.items()}
            population.append({"asset_id": asset_id, "attribution": meta, "judgments": judgments})
    return sorted(population, key=lambda row: row["asset_id"]), ancestors


def freeze_selection(population: list[dict[str, Any]], seed: int = SEED,
                     quotas: dict[str, int] | None = None) -> list[dict[str, Any]]:
    quotas = QUOTAS if quotas is None else quotas
    if (set(quotas) != set(QUOTAS) or any(type(n) is not int or n < 0 for n in quotas.values())
            or not 1 <= sum(quotas.values()) <= 400):
        raise ValueError("invalid bounded selection quotas")
    rows = sorted(population, key=lambda row: row["asset_id"])
    if len({row["asset_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate population IDs")
    rng = random.Random(seed)
    selected: set[str] = set()
    output = []
    for stratum in QUOTAS:
        if stratum == "shared_background":
            eligible = lambda row: all(row["judgments"][name]["relevant"] is False for name in CLASSES)
        else:
            category = stratum.split("_")[0]
            positive = stratum.endswith("_positive")

            def eligible(row: dict[str, Any], category: str = category,
                         positive: bool = positive) -> bool:
                judgment = row["judgments"][category]
                return (judgment["relevant"] is positive
                        and judgment["basis"] == "direct_human_label")

        candidates = [row for row in rows if row["asset_id"] not in selected and eligible(row)]
        if len(candidates) < quotas[stratum]:
            raise ValueError(f"insufficient eligible images for {stratum}")
        chosen = rng.sample(candidates, quotas[stratum])
        for row in chosen:
            output.append({**row, "selection_stratum": stratum})
            selected.add(row["asset_id"])
    return output


def box_evidence(directory: Path, asset_ids: set[str]) -> dict[str, Any]:
    """Evaluation-only area/occlusion summaries; never encoder crops."""
    output: dict[str, dict[str, Any]] = defaultdict(dict)
    reverse = {key: name for name, key in CLASSES.items()}
    with (directory / "boxes.csv").open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["ImageID"] not in asset_ids or row["LabelName"] not in reverse:
                continue
            name = reverse[row["LabelName"]]
            area = (float(row["XMax"]) - float(row["XMin"])) * (
                float(row["YMax"]) - float(row["YMin"]))
            if not 0 < area <= 1:
                raise ValueError("invalid normalized ground-truth box")
            value = output[row["ImageID"]].setdefault(name, {
                "max_box_area_fraction": 0.0, "count": 0, "has_occluded": False,
                "has_depiction": False, "has_group": False})
            value["max_box_area_fraction"] = max(value["max_box_area_fraction"], area)
            value["count"] += 1
            for source, dest in [("IsOccluded", "has_occluded"), ("IsDepiction", "has_depiction"),
                                 ("IsGroupOf", "has_group")]:
                value[dest] |= row[source] == "1"
    return dict(output)


def image_info(raw: bytes) -> dict[str, Any]:
    from PIL import Image

    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("image bytes exceed bound")
    with Image.open(io.BytesIO(raw)) as image:
        if image.format != "JPEG" or image.width * image.height > MAX_DECODED_PIXELS:
            raise ValueError("unexpected format or oversized image")
        image.load()
        return {"width": image.width, "height": image.height,
                "exif_orientation": image.getexif().get(274, 1)}


def prepare(out_dir: Path, metadata_dir: Path) -> dict[str, Any]:
    out_dir = controlled_output(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    source_identity = fetch_metadata(metadata_dir)
    population, ancestors = load_population(metadata_dir)
    chosen = freeze_selection(population)
    plan = {"schema": "budget-search-openimages-plan-v1", "seed": SEED,
            "source": "Open Images validation; official V7 download links",
            "source_identity": source_identity, "population_count": len(population),
            "quotas": QUOTAS, "selected": chosen, "query_definitions": QUERIES,
            "negative_ancestors": {name: sorted(values) for name, values in ancestors.items()},
            "metrics": {"scope": "separate judged-only subset for each query; unknown is excluded",
                        "rank_metrics": ["AP", "P20", "P50", "Recall50", "Recall100"],
                        "required": ["judged coverage", "direct versus ancestor negative counts",
                                     "small/occluded object rank observations", "raw full rankings"]},
            "bounds": {"images": 400, "download_workers": 4,
                       "per_image_bytes": MAX_IMAGE_BYTES, "gpu_work": False},
            "limitations": [
                "Cat/dog enriched diagnostic data; precision is not personal-library prevalence.",
                "Unknown is not negative; full-gallery precision must not be claimed.",
                "Background is random among confirmed negatives, not the full validation population.",
                "No proof of zero overlap with foundation-model pretraining; not a private-photo holdout.",
                "Boxes are evaluation-only; neither input crops nor model selection uses ground-truth boxes.",
                "Download/decode failures stay in the frozen sample; no replacement by successful images.",
            ]}
    plan_path = out_dir / "dataset-plan.json"
    write_frozen_json(plan_path, plan)
    write_frozen_json(out_dir / "queries.json", QUERIES)
    box_metadata = box_evidence(metadata_dir, {row["asset_id"] for row in chosen})
    write_frozen_json(out_dir / "evaluation-boxes.json", {
        "source_sha256": source_identity["boxes.csv"]["sha256"], "assets": box_metadata})
    print(json.dumps({"frozen_images": len(chosen), "plan_sha256": sha256(plan_path)}), flush=True)
    image_dir = controlled_output(out_dir / "images")
    image_dir.mkdir(exist_ok=True)
    previous = {}
    if (out_dir / "sample.json").exists():
        previous = {row["asset_id"]: row for row in
                    json.loads((out_dir / "sample.json").read_text(encoding="utf-8"))["assets"]}
        for row in previous.values():
            path = controlled_output(Path(row["path"]))
            if row.get("sha256") and (not path.is_file() or sha256(path) != row["sha256"]):
                raise ValueError("previously frozen image bytes missing or changed")

    def materialize(row: dict[str, Any]) -> dict[str, Any]:
        asset_id = row["asset_id"]
        path = controlled_output(image_dir / f"{asset_id}.jpg")
        result = {"asset_id": asset_id, "path": str(path),
                  "selection_stratum": row["selection_stratum"],
                  "attribution": row["attribution"], "url": IMAGE_URL.format(image_id=asset_id)}
        failures = list(previous.get(asset_id, {}).get("failure_history", []))
        try:
            if path.exists():
                raw = path.read_bytes()
                info = image_info(raw)
            else:
                for attempt in range(3):
                    try:
                        raw = fetch_bytes(result["url"], MAX_IMAGE_BYTES)
                        info = image_info(raw)
                        path.write_bytes(raw)
                        break
                    except (OSError, ValueError) as exc:
                        failures.append({"attempt": attempt + 1, "error": str(exc)})
                        if attempt == 2:
                            raise
                        time.sleep(attempt + 1)
            result.update(info, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
                          input_status="READY_FOR_HASH_CHECK")
        except (OSError, ValueError) as exc:
            result.update(input_status="FAILED", error=str(exc))
        if failures:
            result["failure_history"] = failures
        return result

    with ThreadPoolExecutor(max_workers=4) as pool:
        assets = list(pool.map(materialize, chosen))
    hashes: dict[str, list[str]] = defaultdict(list)
    for row in assets:
        if row.get("sha256"):
            hashes[row["sha256"]].append(row["asset_id"])
    duplicates = {digest: ids for digest, ids in hashes.items() if len(ids) > 1}
    sample = {"schema": "wemm-pilot-public-sample-v1", "source_identity": source_identity,
              "source_roots": [str(image_dir)], "assets": assets,
              "plan_sha256": sha256(plan_path), "content_duplicates": duplicates}
    write_json(out_dir / "sample.json", sample)
    ready = all(row.get("sha256") for row in assets) and not duplicates
    label_summary = {}
    for name in CLASSES:
        label_summary[name] = dict(Counter(
            f"{row['judgments'][name]['relevant']}:{row['judgments'][name]['basis']}" for row in chosen))
    if ready:
        labels = {"provenance": {"kind": "Open Images human labels with negative hierarchy closure",
                                 "source_identity": source_identity, "limitations": plan["limitations"]},
                  "input_identities": [{"asset_id": row["asset_id"], "sha256": row["sha256"]}
                                       for row in assets], "query_definitions": QUERIES,
                  "queries": {name: [{"asset_id": row["asset_id"], **row["judgments"][name]}
                                     for row in chosen] for name in CLASSES}}
        write_frozen_json(out_dir / "labels.json", labels)
    summary = {"status": "READY" if ready else "INCOMPLETE_OR_DUPLICATE_INPUTS",
               "assets": len(assets), "sample_sha256": sha256(out_dir / "sample.json"),
               "plan_sha256": sha256(plan_path), "labels": label_summary,
               "image_bytes": sum(row.get("bytes", 0) for row in assets),
               "failures": [{"asset_id": row["asset_id"], "error": row.get("error")}
                            for row in assets if not row.get("sha256")],
               "content_duplicates": duplicates,
               "observed_failures": sum(len(row.get("failure_history", [])) for row in assets)}
    write_json(out_dir / "preparation-summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--metadata-dir", type=Path, required=True)
    args = parser.parse_args()
    summary = prepare(args.out_dir, args.metadata_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["status"] != "READY":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
