"""Prepare disjoint COCO train/calibration/test data before feature extraction.

This is a category-presence experiment, not an exhaustive personal-photo truth
set. Category absence is negative only within COCO's annotation scope. All
ground-truth boxes stay in evaluation-only files, outside encoder manifests.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import struct
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tools.evaluation import prepare_wemm_coco as coco
from tools.evaluation.public_search_quality import RemoteZip, sha256

SEED = 20261021
QUERIES = coco.QUERIES[:8]
CATEGORIES = coco.CATEGORY_QUERIES
SPLITS = {"train": (400, 50), "calibration": (160, 20), "test": (240, 30)}
VAL_MEMBER = "annotations/instances_val2014.json"
VAL_SHA256 = "e1b4d1bc54906827b1a029981b1c77f2088bd4462569b7615db7929070c3ab47"
VAL_BYTES = 160682675
VAL_COMPRESSED_BYTES = 51955093
VAL_CRC32 = 642745279
MAX_IMAGE_BYTES = 8 * 1024 * 1024
LIMITATIONS = [
    "Negatives mean no instance of this COCO category was annotated; missing annotations can be false negatives.",
    "Half random and half enriched data are not the prevalence of a personal library; report both strata.",
    "Category presence includes small, occluded and crowd objects, not only the main photographic subject.",
    "COCO can overlap foundation-model pretraining; separation applies to this experiment's fitted heads only.",
    "SHA/Flickr identity checks do not prove absence of visually near-duplicate photographs.",
    "Frozen download failures are retained and never replaced by successful or better-scoring images.",
    "Boxes are evaluation-only and must never define encoder crops or model input features.",
]


def flickr_id(row: dict[str, Any]) -> str | None:
    """Normalize photo identity across original, resized and landing URLs."""
    urls = [row.get("coco", {}).get("flickr_url"),
            row.get("attribution", {}).get("OriginalURL"),
            row.get("attribution", {}).get("OriginalLandingURL")]
    found = set()
    for url in urls:
        if not url:
            continue
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host.endswith(".staticflickr.com") or host.endswith(".static.flickr.com"):
            match = re.search(r"/(\d+)_[^/]+$", parsed.path)
        elif host in {"flickr.com", "www.flickr.com"}:
            match = re.search(r"/photos/[^/]+/(\d+)(?:/|$)", parsed.path)
        else:
            match = None
        if match:
            found.add(match[1])
    if len(found) > 1:
        raise ValueError("conflicting Flickr source identities")
    return next(iter(found), None)


def source_identity(path: Path, expected: str, member: str) -> dict[str, Any]:
    path = coco.controlled_output(path)
    if not path.is_file() or sha256(path) != expected:
        raise ValueError(f"annotation source hash differs: {member}")
    return {"url": coco.ANNOTATIONS_URL, "member": member,
            "sha256": expected, "bytes": path.stat().st_size,
            "annotation_license": "CC BY 4.0: https://cocodataset.org/#termsofuse"}


def download_validation_annotations(directory: Path) -> Path:
    """Range-download one pinned member, enforcing compressed and expanded bounds."""
    directory = coco.controlled_output(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination = coco.controlled_output(directory / "instances_val2014.json")
    if destination.exists():
        source_identity(destination, VAL_SHA256, VAL_MEMBER)
        return destination
    temporary = coco.controlled_output(destination.with_suffix(".part"))
    with RemoteZip(coco.ANNOTATIONS_URL) as remote, zipfile.ZipFile(remote) as archive:
        entry = archive.getinfo(VAL_MEMBER)
        if (entry.file_size, entry.compress_size, entry.CRC, entry.compress_type) != (
                VAL_BYTES, VAL_COMPRESSED_BYTES, VAL_CRC32, zipfile.ZIP_DEFLATED):
            raise ValueError("validation annotation ZIP identity differs")
        remote.seek(entry.header_offset)
        header = remote.read(30)
        if header[:4] != b"PK\x03\x04":
            raise ValueError("invalid annotation ZIP header")
        name_length, extra_length = struct.unpack_from("<HH", header, 26)
        remote.seek(name_length + extra_length, 1)
        decoder = zlib.decompressobj(-15)
        remaining, total, crc = entry.compress_size, 0, 0
        digest = hashlib.sha256()
        with temporary.open("wb") as stream:
            while remaining:
                start = remote.tell()
                for attempt in range(3):
                    try:
                        compressed = remote.read(min(8 * 1024 * 1024, remaining))
                        break
                    except (OSError, ValueError):
                        remote.seek(start)
                        if attempt == 2:
                            raise
                        time.sleep(attempt + 1)
                if not compressed:
                    raise ValueError("short annotation stream")
                remaining -= len(compressed)
                raw = decoder.decompress(compressed, entry.file_size - total + 1)
                total += len(raw)
                if total > entry.file_size or decoder.unconsumed_tail:
                    raise ValueError("annotation expansion exceeds size bound")
                crc = zlib.crc32(raw, crc)
                digest.update(raw)
                stream.write(raw)
        if (total != entry.file_size or crc != entry.CRC or not decoder.eof
                or decoder.unused_data or digest.hexdigest() != VAL_SHA256):
            raise ValueError("validation annotation size/CRC/SHA mismatch")
    temporary.replace(destination)
    return destination


def load_prior_samples(paths: list[Path]) -> tuple[dict[str, list[dict]], list[dict]]:
    groups, identities = {}, []
    for index, supplied in enumerate(paths):
        path = coco.controlled_output(supplied)
        sample = json.loads(path.read_text(encoding="utf-8"))
        rows = sample["assets"]
        if sample.get("schema") != "wemm-pilot-public-sample-v1" or not rows:
            raise ValueError("unexpected or empty prior sample")
        if len({str(row["asset_id"]) for row in rows}) != len(rows):
            raise ValueError("duplicate prior sample asset ID")
        for row in rows:
            original = coco.controlled_output(Path(row["path"]))
            if not row.get("sha256") or not original.is_file() or sha256(original) != row["sha256"]:
                raise ValueError("prior sample image missing or changed")
            flickr_id(row)
        name = f"prior_{index}"
        groups[name] = rows
        identities.append({"name": name, "path": str(path), "sha256": sha256(path), "assets": len(rows)})
    return groups, identities


def freeze_partitions(train: list[dict], validation: list[dict], previous: list[dict],
                      budgets: dict[str, tuple[int, int]] | None = None) -> dict[str, list[dict]]:
    budgets = SPLITS if budgets is None else budgets
    if set(budgets) != set(SPLITS) or any(
            len(values) != 2 or any(type(n) is not int or n < 1 for n in values)
            or values[0] != 8 * values[1] or sum((values[0], 8 * values[1])) > sum((SPLITS[key][0], 8 * SPLITS[key][1]))
            for key, values in budgets.items()):
        raise ValueError("invalid bounded partition budgets")
    excluded_ids = {row["coco"]["id"] for row in previous if "coco" in row}
    excluded_flickr = {fid for row in previous if (fid := flickr_id(row))}
    output = {}
    for number, name in enumerate(SPLITS):
        random_count, per_category = budgets[name]
        population = validation if name == "test" else train
        eligible = [row for row in population if row["asset_id"] not in excluded_ids
                    and (not flickr_id(row) or flickr_id(row) not in excluded_flickr)]
        chosen = coco.freeze_selection(eligible, seed=SEED + number, random_count=random_count,
                                       quotas={category: per_category for category in CATEGORIES.values()})
        output[name] = chosen
        excluded_ids.update(row["asset_id"] for row in chosen)
        excluded_flickr.update(fid for row in chosen if (fid := flickr_id(row)))
    return output


def overlap_report(groups: dict[str, list[dict]]) -> dict[str, Any]:
    """Report rather than repair cross-partition or within-partition identities."""
    collisions = {}
    for kind in ("coco_id", "flickr_id", "sha256"):
        keys = defaultdict(list)
        for name, rows in groups.items():
            for row in rows:
                key = (row.get("coco", {}).get("id") if kind == "coco_id" else
                       flickr_id(row) if kind == "flickr_id" else row.get("sha256"))
                if key is not None:
                    keys[str(key)].append({"partition": name, "asset_id": row["asset_id"]})
        collisions[kind] = {key: refs for key, refs in keys.items() if len(refs) > 1
                            and any(not ref["partition"].startswith("prior_") for ref in refs)}
    return {"status": "DISJOINT" if not any(collisions.values()) else "OVERLAP_REJECTED",
            "asset_counts": {key: len(rows) for key, rows in groups.items()}, "collisions": collisions,
            "limitations": "Byte SHA and Flickr identity cannot exclude visually near-duplicate photos."}


def evaluation_boxes(annotation: dict, selected: list[dict]) -> dict[str, Any]:
    by_id = {row["asset_id"]: row for row in selected}
    category_names = {row["id"]: row["name"] for row in annotation["categories"]}
    reverse = {name: key for key, name in CATEGORIES.items()}
    output = defaultdict(dict)
    for instance in annotation["annotations"]:
        row = by_id.get(instance["image_id"])
        query = reverse.get(category_names[instance["category_id"]])
        if row is None or query is None:
            continue
        x, y, width, height = instance["bbox"]
        area = instance["area"]
        if not all(math.isfinite(v) for v in (x, y, width, height, area)) or min(x, y, width, height, area) < 0:
            raise ValueError("invalid COCO evaluation box")
        image_area = row["coco"]["width"] * row["coco"]["height"]
        value = output[str(row["asset_id"])].setdefault(query, {
            "max_box_area_fraction": 0.0, "max_mask_area_fraction": 0.0, "instances": []})
        value["max_box_area_fraction"] = max(value["max_box_area_fraction"], width * height / image_area)
        value["max_mask_area_fraction"] = max(value["max_mask_area_fraction"], area / image_area)
        value["instances"].append({"bbox": instance["bbox"], "area": area, "iscrowd": instance["iscrowd"]})
    return dict(output)


def image_metadata(raw: bytes, expected: tuple[int, int]) -> dict[str, Any]:
    from PIL import Image
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("image byte limit exceeded")
    with Image.open(io.BytesIO(raw)) as image:
        if image.format != "JPEG" or image.size != expected or image.width * image.height > 64_000_000:
            raise ValueError("image format/dimensions differ from official metadata")
        image.load()
        return {"width": image.width, "height": image.height,
                "exif_orientation": image.getexif().get(274, 1)}


def materialize(row: dict, directory: Path, source_split: str, previous: dict | None = None) -> dict:
    """A failed fixed asset stays failed; retries never draw replacement images."""
    previous = previous or {}
    filename = f"COCO_{source_split}_{row['asset_id']:012d}.jpg"
    if filename != row["coco"]["file_name"]:
        raise ValueError("COCO source filename differs from selected identity")
    path = coco.controlled_output(directory / filename)
    result = {"asset_id": row["asset_id"], "path": str(path), "coco": row["coco"],
              "license": row["license"], "flickr_photo_id": flickr_id(row),
              "selection_stratum": row["selection_stratum"], "source_split": source_split}
    failures = list(previous.get("failure_history", []))
    if previous.get("sha256") and (not path.is_file() or sha256(path) != previous["sha256"]):
        raise ValueError("previously frozen original is missing or changed")
    try:
        if path.exists():
            if path.stat().st_size > MAX_IMAGE_BYTES:
                raise ValueError("cached image byte limit exceeded")
            raw = path.read_bytes()
            info = image_metadata(raw, (row["coco"]["width"], row["coco"]["height"]))
        else:
            for attempt in range(3):
                try:
                    raw = coco.fetch_complete_image(coco.SOURCE + "/" + source_split + "/" + filename,
                                                    row["coco"]["width"], row["coco"]["height"])
                    info = image_metadata(raw, (row["coco"]["width"], row["coco"]["height"]))
                    temporary = coco.controlled_output(path.with_suffix(".part"))
                    temporary.write_bytes(raw)
                    temporary.replace(path)
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


def bound_labels(rows: list[dict], assets: list[dict], identity: dict) -> dict:
    if ([row["asset_id"] for row in rows] != [row["asset_id"] for row in assets]
            or any(not row.get("sha256") for row in assets)):
        raise ValueError("label binding requires complete matching asset identities")
    return {"provenance": {"kind": "COCO instance-category presence", "source_identity": identity,
                           "limitations": LIMITATIONS}, "query_definitions": QUERIES,
            "input_identities": [{"asset_id": row["asset_id"], "sha256": row["sha256"]} for row in assets],
            "queries": {query: [{"asset_id": row["asset_id"], "relevant": name in row["coco_categories"],
                                  "basis": "COCO_INSTANCE_PRESENT" if name in row["coco_categories"]
                                  else "COCO_CATEGORY_ABSENT"} for row in rows]
                        for query, name in CATEGORIES.items()}}


def prepare(output: Path, train_path: Path, prior_paths: list[Path], plan_only: bool = False) -> dict:
    output = coco.controlled_output(output)
    output.mkdir(parents=True, exist_ok=True)
    train_identity = source_identity(train_path, coco.ANNOTATION_SHA256, coco.MEMBER)
    validation_path = download_validation_annotations(output / "metadata")
    val_identity = source_identity(validation_path, VAL_SHA256, VAL_MEMBER)
    sources = {"train2014": train_identity, "val2014": val_identity}
    coco.write_frozen_json(output / "source-lock.json", sources)
    previous, prior_identities = load_prior_samples(prior_paths)
    train = json.loads(train_path.read_text(encoding="utf-8"))
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    partitions = freeze_partitions(coco.load_population(train), coco.load_population(validation),
                                   [row for rows in previous.values() for row in rows])
    plan = {"schema": "album-coco-partitions-v1", "seed": SEED, "sources": sources,
            "prior_samples": prior_identities, "queries": QUERIES,
            "split_budgets": {name: {"random": values[0], "each_category": values[1]}
                              for name, values in SPLITS.items()},
            "selected": {name: [{"asset_id": row["asset_id"], "flickr_photo_id": flickr_id(row),
                                  "selection_stratum": row["selection_stratum"]} for row in rows]
                         for name, rows in partitions.items()},
            "limitations": LIMITATIONS, "bounds": {"images": 1600, "workers": 4,
                                                   "image_bytes": MAX_IMAGE_BYTES, "attempts": 3}}
    coco.write_frozen_json(output / "partition-plan.json", plan)
    preliminary = overlap_report({**previous, **partitions})
    coco.write_json(output / "source-overlap.json", preliminary)
    if preliminary["status"] != "DISJOINT":
        raise ValueError("frozen source overlap rejected; no replacement sampling")
    for name, rows in partitions.items():
        directory = output / name
        directory.mkdir(exist_ok=True)
        source_split = "val2014" if name == "test" else "train2014"
        coco.write_frozen_json(directory / "dataset-plan.json", {
            "partition_plan_sha256": sha256(output / "partition-plan.json"),
            "partition": name, "source_split": source_split, "selected": plan["selected"][name]})
        coco.write_frozen_json(directory / "queries.json", QUERIES)
        coco.write_frozen_json(directory / "evaluation-boxes-frozen.json", {
            "purpose": "evaluation only; not encoder input", "source_identity": sources[source_split],
            "partition_plan_sha256": sha256(output / "partition-plan.json"),
            "assets": evaluation_boxes(validation if name == "test" else train, rows)})
    print(json.dumps({"frozen_partitions": {name: len(rows) for name, rows in partitions.items()},
                      "plan_sha256": sha256(output / "partition-plan.json")}), flush=True)
    if plan_only:
        return {"status": "PLAN_FROZEN", "plan_sha256": sha256(output / "partition-plan.json")}
    all_assets = {}
    for name, rows in partitions.items():
        directory = output / name
        image_dir = coco.controlled_output(directory / "images")
        image_dir.mkdir(exist_ok=True)
        sample_path = directory / "sample.json"
        cached = json.loads(sample_path.read_text(encoding="utf-8"))["assets"] if sample_path.exists() else []
        if cached and [row["asset_id"] for row in cached] != [row["asset_id"] for row in rows]:
            raise ValueError("cached sample membership differs from frozen plan")
        previous_assets = {row["asset_id"]: row for row in cached}
        source_split = "val2014" if name == "test" else "train2014"
        def download(row: dict) -> dict:
            return materialize(row, image_dir, source_split, previous_assets.get(row["asset_id"]))
        with ThreadPoolExecutor(max_workers=4) as pool:
            assets = []
            for asset in pool.map(download, rows):
                assets.append(asset)
                if len(assets) % 100 == 0:
                    print(json.dumps({"partition": name, "processed": len(assets)}), flush=True)
        all_assets[name] = assets
        coco.write_json(sample_path, {"schema": "wemm-pilot-public-sample-v1", "assets": assets,
                                     "source_roots": [str(image_dir)], "source_identity": sources[source_split],
                                     "plan_sha256": sha256(directory / "dataset-plan.json")})
    overlap = overlap_report({**previous, **all_assets})
    coco.write_json(output / "content-overlap.json", overlap)
    failures = [{"partition": name, "asset_id": row["asset_id"], "error": row.get("error")}
                for name, rows in all_assets.items() for row in rows if not row.get("sha256")]
    ready = not failures and overlap["status"] == "DISJOINT"
    if ready:
        for name, rows in partitions.items():
            source_split = "val2014" if name == "test" else "train2014"
            labels = bound_labels(rows, all_assets[name], sources[source_split])
            coco.write_frozen_json(output / name / "labels.json", labels)
            frozen_boxes = json.loads((output / name / "evaluation-boxes-frozen.json").read_text(encoding="utf-8"))
            coco.write_frozen_json(output / name / "evaluation-boxes.json", {
                **frozen_boxes, "input_identities": labels["input_identities"], "query_definitions": QUERIES})
    summary = {"status": "READY" if ready else "INCOMPLETE_OR_OVERLAP", "failures": failures,
               "overlap_status": overlap["status"], "partition_plan_sha256": sha256(output / "partition-plan.json"),
               "partitions": {name: {"assets": len(rows), "bytes": sum(r.get("bytes", 0) for r in rows),
                                     "strata": dict(Counter(r["selection_stratum"] for r in rows)),
                                     "sample_sha256": sha256(output / name / "sample.json"),
                                     "observed_failures": sum(len(r.get("failure_history", [])) for r in rows)}
                              for name, rows in all_assets.items()}}
    coco.write_json(output / "preparation-summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--train-annotations", type=Path, required=True)
    parser.add_argument("--prior-sample", type=Path, action="append", required=True)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    evidence = coco.controlled_output(args.evidence)
    if evidence.exists() or evidence.with_suffix(".xml").exists():
        raise ValueError("refusing to overwrite previous execution evidence")
    started = time.perf_counter()
    error = None
    result = None
    before = sha256(Path(__file__))
    try:
        result = prepare(args.out_dir, args.train_annotations, args.prior_sample, args.plan_only)
        if result["status"] not in {"READY", "PLAN_FROZEN"}:
            raise ValueError("dataset incomplete or overlapping; labels not published")
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        elapsed = time.perf_counter() - started
        coco.write_json(evidence, {"status": "FAILED" if error else "COMPLETE", "error": error,
                                   "elapsed_seconds": elapsed, "summary": result,
                                   "source_sha256_before": before, "source_sha256_after": sha256(Path(__file__))})
        suite = ET.Element("testsuite", name="album_coco_preparation", tests="1", failures=str(int(error is not None)),
                           errors="0", time=str(elapsed))
        case = ET.SubElement(suite, "testcase", name="prepare_frozen_partitions", time=str(elapsed))
        if error:
            ET.SubElement(case, "failure", message=error).text = error
        ET.ElementTree(suite).write(evidence.with_suffix(".xml"), encoding="utf-8", xml_declaration=True)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
