"""Freeze broad object judgments and album queries from already downloaded public data.

No images are changed, no model runs, and missing object annotations remain unknown.
Open Images human negatives (including sound hierarchy implications) are the only
object negatives. COCO is a positive-recall diagnostic, not a clean pretraining holdout.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "broad-album-data-v1"
SEED = "broad-album-objects-v1"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class Inputs:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.hashes: dict[str, str] = {}

    def path(self, path: Path | str) -> Path:
        path = Path(path)
        path = path if path.is_absolute() else self.root / path
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("input outside workspace")
        return resolved

    def raw(self, path: Path | str, *, maximum: int = 512 * 1024 * 1024) -> bytes:
        path = self.path(path)
        if path.stat().st_size > maximum:
            raise ValueError("input exceeds bound")
        with path.open("rb") as stream:
            raw = stream.read(maximum + 1)
        if len(raw) > maximum:
            raise ValueError("input changed beyond bound")
        key = self.key(path)
        hashed = digest(raw)
        if key in self.hashes and self.hashes[key] != hashed:
            raise ValueError("input changed during preparation")
        self.hashes[key] = hashed
        return raw

    def key(self, path: Path | str) -> str:
        return self.path(path).relative_to(self.root).as_posix()

    def locked(self, path: Path | str, identity: dict[str, Any]) -> None:
        raw = self.raw(path)
        if digest(raw) != identity["sha256"] or len(raw) != identity["bytes"]:
            raise ValueError("annotation source differs from original frozen lock")

    def unchanged(self) -> dict[str, str]:
        for path in list(self.hashes):
            self.raw(path)
        return dict(self.hashes)

    def json(self, path: Path | str) -> Any:
        return json.loads(self.raw(path))

    def csv(self, path: Path | str) -> Any:
        return csv.DictReader(io.StringIO(self.raw(path).decode("utf-8")))


def hierarchy_relations(tree: dict[str, Any]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """A parent may have several occurrences in the official Open Images DAG."""
    parents: dict[str, set[str]] = defaultdict(set)

    def visit(node: dict[str, Any], path: tuple[str, ...]) -> None:
        label = node["LabelName"]
        if label in path:
            raise ValueError("cyclic label hierarchy")
        parents[label].update(path)
        for child in node.get("Subcategory", []):
            visit(child, (*path, label))

    visit(tree, ())
    # Repeated ancestors can acquire further ancestors through another DAG branch.
    for _ in range(len(parents)):
        changed = False
        for label, values in parents.items():
            expanded = set(values).union(*(parents[parent] for parent in tuple(values)))
            if label in expanded:
                raise ValueError("cyclic label hierarchy")
            if expanded != values:
                parents[label] = expanded
                changed = True
        if not changed:
            break
    children: dict[str, set[str]] = defaultdict(set)
    for label, ancestors in parents.items():
        for ancestor in ancestors:
            children[ancestor].add(label)
    return dict(parents), dict(children)


def object_judgments(
    image_ids: set[str], names: dict[str, str], human_rows: Any, box_rows: Any,
    parents: dict[str, set[str]], children: dict[str, set[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence: dict[tuple[str, str], set[tuple[bool, str, str]]] = defaultdict(set)
    for row in human_rows:
        if row["ImageID"] not in image_ids:
            continue
        if row["Confidence"] not in {"0", "1"}:
            raise ValueError("unknown human confidence")
        positive = row["Confidence"] == "1"
        label = row["LabelName"]
        evidence[row["ImageID"], label].add((positive, "human-direct", label))
        implied = parents.get(label, set()) if positive else children.get(label, set())
        for target in implied & names.keys():
            evidence[row["ImageID"], target].add((positive, "positive-ancestor" if positive else "negative-descendant", label))
    for row in box_rows:
        if row["ImageID"] not in image_ids:
            continue
        label = row["LabelName"]
        for target in {label} | parents.get(label, set()):
            if target in names:
                evidence[row["ImageID"], target].add((True, "official-box" if target == label else "box-ancestor", label))
    judgments, conflicts = [], []
    for (image_id, label), values in sorted(evidence.items()):
        if label not in names:
            continue
        unique = {value[0] for value in values}
        row = {"asset_id": image_id, "class_id": label,
               "relevant": next(iter(unique)) if len(unique) == 1 else None,
               "basis": [{"positive": v, "rule": rule, "source_class": source}
                         for v, rule, source in sorted(values)]}
        judgments.append(row)
        if len(unique) > 1:
            conflicts.append(row)
    return judgments, conflicts


def summarize_queries(names: dict[str, str], assets: list[dict[str, Any]], judgments: list[dict[str, Any]],
                      split_by_asset: dict[str, str] | None = None) -> list[dict[str, Any]]:
    by_class: dict[str, dict[Any, bool | None]] = defaultdict(dict)
    for row in judgments:
        by_class[row["class_id"]][row["asset_id"]] = row["relevant"]
    output = []
    for class_id, name in sorted(names.items()):
        known = by_class[class_id]
        positive = [row["asset_id"] for row in assets if known.get(row["asset_id"]) is True]
        negative = [row["asset_id"] for row in assets if known.get(row["asset_id"]) is False]
        support = {}
        for split in sorted(set((split_by_asset or {}).values())):
            selected = {row["asset_id"] for row in assets if split_by_asset[str(row["asset_id"])] == split}
            support[split] = {"positive": len(selected & set(positive)), "negative": len(selected & set(negative)),
                              "unknown": len(selected - set(positive) - set(negative))}
        output.append({"query_id": class_id, "text": name.lower(), "language": "en", "support_by_split": support,
                       "positive_asset_ids": positive, "negative_asset_ids": negative,
                       "unknown_count": len(assets) - len(positive) - len(negative),
                       "diagnostic_supported": len(positive) >= 3 and len(negative) >= 5})
    return output


def check_sample(reader: Inputs, path: str, count: int) -> list[dict[str, Any]]:
    document = reader.json(path)
    assets = document["assets"]
    if len(assets) != count or len({row["asset_id"] for row in assets}) != count:
        raise ValueError("sample population changed")
    result = []
    for row in assets:
        image = reader.path(row["path"])
        raw = reader.raw(image, maximum=128 * 1024 * 1024)
        if digest(raw) != row["sha256"]:
            raise ValueError("public image bytes changed")
        result.append({"asset_id": row["asset_id"], "path": str(image), "sha256": row["sha256"]})
    return result


def cache_reference(reader: Inputs, directory: str, sample_path: str, assets: list[dict[str, Any]]) -> dict[str, Any]:
    record = reader.json(directory + "/result.json")
    if record["sample_sha256"] != reader.hashes[reader.key(sample_path)]:
        raise ValueError("embedding cache sample identity changed")
    cached = record["assets"]
    if [(r["asset_id"], r["sha256"]) for r in cached] != [(r["asset_id"], r["sha256"]) for r in assets]:
        raise ValueError("embedding cache rows differ")
    matrix_path = directory + "/images.npy"
    raw = reader.raw(matrix_path)
    if digest(raw) != record["vectors_sha256"]["images.npy"]:
        raise ValueError("embedding cache checksum mismatch")
    matrix = np.load(io.BytesIO(raw), allow_pickle=False)
    if (matrix.dtype.kind != "f" or matrix.shape != (len(assets), record["native_dimension"])
            or not np.isfinite(matrix).all()):
        raise ValueError("embedding cache shape or values invalid")
    return {"directory": str(reader.path(directory)), "image_vectors": str(reader.path(matrix_path)),
            "image_vectors_sha256": digest(raw), "native_dimension": record["native_dimension"],
            "model": record["model"], "identity": record["identity"],
            "query_cache_scope": "existing query definitions only; encode new category texts separately",
            "row_by_asset": [{"asset_id": row["asset_id"], "row": i} for i, row in enumerate(assets)]}


def prepare(root: Path, output: Path) -> dict[str, Any]:
    reader = Inputs(root)
    output = reader.path(output)
    if output.exists():
        raise ValueError("output already exists; preserve previous frozen plan")
    dataset_specs = {
        "openimages400": (".tmp/gtx1660-search/datasets/openimages400/sample.json", 400, "oi400-whole"),
        "coco480": (".tmp/album-pareto/coco/test/sample.json", 480, "coco-test"),
        "photobench1047": (".tmp/album-pareto/datasets/photobench/album3-v2/sample.json", 1047, "photobench"),
    }
    datasets: dict[str, dict[str, Any]] = {}
    for name, (sample_path, count, cache_suffix) in dataset_specs.items():
        assets = check_sample(reader, sample_path, count)
        datasets[name] = {"schema": SCHEMA, "dataset": name, "assets": assets,
                          "sample_sha256": reader.hashes[reader.key(sample_path)],
                          "embedding_caches": {model: cache_reference(reader, f".tmp/album-pareto/runs/{model}-{cache_suffix}", sample_path, assets)
                                               for model in ("qwen", "fg")}}
    oi = datasets["openimages400"]
    source_root = ".tmp/gtx1660-search/openimages-metadata/"
    oi_source = reader.json(dataset_specs["openimages400"][0])["source_identity"]
    for filename in ("classes.csv", "hierarchy.json", "labels.csv", "boxes.csv"):
        reader.locked(source_root + filename, oi_source[filename])
    names = {row["LabelName"]: row["DisplayName"] for row in reader.csv(source_root + "classes.csv")}
    parents, children = hierarchy_relations(reader.json(source_root + "hierarchy.json"))
    judgments, conflicts = object_judgments({row["asset_id"] for row in oi["assets"]}, names,
        reader.csv(source_root + "labels.csv"), reader.csv(source_root + "boxes.csv"), parents, children)
    split_by_asset = {row["asset_id"]: ("calibration" if int(digest((SEED + row["asset_id"]).encode())[:8], 16) % 10 < 3 else "test")
                      for row in oi["assets"]}
    oi.update(queries=summarize_queries(names, oi["assets"], judgments, split_by_asset), judgments=judgments,
              conflicts=conflicts, missing_judgment="unknown",
              split_by_asset=split_by_asset,
              ancestors={key: sorted(values & names.keys()) for key, values in parents.items() if key in names},
              category_names=names, source_identity=oi_source)
    coco = datasets["coco480"]
    coco_source = reader.json(".tmp/album-pareto/coco/source-lock.json")["val2014"]
    if coco_source != reader.json(dataset_specs["coco480"][0])["source_identity"]:
        raise ValueError("COCO sample source differs from annotation source lock")
    coco_annotation = ".tmp/album-pareto/coco/metadata/instances_val2014.json"
    reader.locked(coco_annotation, coco_source)
    annotations = reader.json(coco_annotation)
    coco_names = {str(row["id"]): row["name"] for row in annotations["categories"]}
    wanted = {row["asset_id"] for row in coco["assets"]}
    dimensions = {row["id"]: row["width"] * row["height"] for row in annotations["images"] if row["id"] in wanted}
    instances = defaultdict(list)
    for row in annotations["annotations"]:
        if row["image_id"] in wanted:
            instances[row["image_id"], str(row["category_id"])].append({
                "bbox": row["bbox"], "iscrowd": row["iscrowd"],
                "box_area_fraction": row["bbox"][2] * row["bbox"][3] / dimensions[row["image_id"]]})
    coco_judgments = [{"asset_id": image_id, "class_id": class_id, "relevant": True,
                       "instances": boxes} for (image_id, class_id), boxes in sorted(instances.items())]
    coco.update(queries=summarize_queries(coco_names, coco["assets"], coco_judgments), judgments=coco_judgments,
                missing_judgment="unknown", source_identity=coco_source,
                role="positive-recall diagnostic; no confirmed negatives or pretraining-holdout claim")
    pb = datasets["photobench1047"]
    pbroot = ".tmp/album-pareto/datasets/photobench/album3-v2/"
    pb.update(queries=reader.json(pbroot + "queries.json"), evaluation=reader.json(pbroot + "evaluation.json"),
              role="official complete-album query relevance; not independent object labels",
              missing_object_judgment="unknown")
    plan = {"schema": SCHEMA, "status": "FROZEN_BEFORE_NEW_MODEL_SCORES", "seed": SEED,
            "datasets": {name: {"images": len(data["assets"]), "queries": len(data["queries"])} for name, data in datasets.items()},
            "openimages_categories_with_positive": sum(bool(q["positive_asset_ids"]) for q in oi["queries"]),
            "openimages_supported_diagnostic_categories": sum(q["diagnostic_supported"] for q in oi["queries"]),
            "openimages_conflicting_judgments": len(conflicts),
            "openimages_split_counts": {split: list(split_by_asset.values()).count(split) for split in ("calibration", "test")},
            "object_query_language": "English official class names; Chinese generalization must be measured separately, not inferred",
            "protocol": ["No training or threshold selection on test images or PhotoBench test query groups.",
                         "Rank the complete gallery; report judged-only AP and precision together with unknown coverage for Open Images.",
                         "COCO missing boxes remain unknown: report known-positive Recall@20/50 and small-subject slices, never precision or clean generalization.",
                         "PhotoBench uses its existing 30 calibration / 70 test bilingual query split and official judgments.",
                         "Freeze exact query-to-tag mapping before inspecting new model scores; unmapped queries fall back to baseline.",
                         "Compare baseline, tags-only, fixed conservative rank fusion; no automatic machine-tag FTS writes or hard filtering.",
                         "Report every category, support count, hierarchy family and regressions; do not let many body-part tags dominate a macro score.",
                         "Measure added offline encoding time, peak own VRAM/RAM, bytes/image, warm query and candidate costs separately.",
                         "These datasets were previously used for diagnostics and may overlap model pretraining; no untouched private-album claim.",
                         "PhotoBench license inconsistencies mean local noncommercial research; no pixel redistribution."],
            "input_sha256": reader.hashes}
    output.mkdir(parents=True)
    for name, data in datasets.items():
        (output / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return {key: value for key, value in plan.items() if key != "input_sha256"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.out_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
