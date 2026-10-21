"""Freeze a bounded COCO category-retrieval pilot before any model inference.

Only the explicit CLI downloads public annotations/images. Relevance is COCO
instance-category presence, not a claim of exhaustive human photo relevance.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import random
import shutil
import struct
import time
import urllib.request
import zipfile
import zlib
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from tools.evaluation.public_search_quality import RemoteZip, sha256
from tools.evaluation.public_search_quality import write_json as _write_json

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "https://s3.amazonaws.com/images.cocodataset.org"
ANNOTATIONS_URL = SOURCE + "/annotations/annotations_trainval2014.zip"
MEMBER = "annotations/instances_train2014.json"
ANNOTATION_SHA256 = "777ef504abfeba349930485bb32ecd5b6529129a3e010f7852e1437cb049b732"
SEED = 20261020
LICENSE_IDS = {4, 5}
QUOTAS = {"cat": 70, "dog": 35, "bird": 20, "car": 20, "bicycle": 15,
          "person": 15, "chair": 15, "teddy bear": 20, "sheep": 15,
          "cow": 15, "horse": 10}
QUERIES = [
    {"query_id": "cat", "text": "猫"},
    {"query_id": "dog", "text": "狗"},
    {"query_id": "bird", "text": "鸟"},
    {"query_id": "car", "text": "汽车"},
    {"query_id": "bicycle", "text": "自行车"},
    {"query_id": "person", "text": "人"},
    {"query_id": "chair", "text": "椅子"},
    {"query_id": "teddy_bear", "text": "玩具熊"},
    {"query_id": "two_cats", "text": "两只猫"},
    {"query_id": "black_cat", "text": "黑色的猫"},
    {"query_id": "cat_on_sofa", "text": "猫趴在沙发上"},
    {"query_id": "person_with_cat", "text": "人与猫在一起"},
]
CATEGORY_QUERIES = {"cat": "cat", "dog": "dog", "bird": "bird", "car": "car",
                    "bicycle": "bicycle", "person": "person", "chair": "chair",
                    "teddy_bear": "teddy bear"}


def controlled_output(path: Path) -> Path:
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError("public dataset output links are refused")
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT) or not any(
            resolved.is_relative_to((ROOT / name).resolve()) for name in (".tmp", "test-results")):
        raise ValueError("public dataset output must be under project .tmp or test-results")
    return resolved


def write_json(path: Path, value: Any) -> None:
    _write_json(controlled_output(path), value)


def write_frozen_json(path: Path, value: Any) -> None:
    path = controlled_output(path)
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != value:
            raise ValueError(f"refusing to change frozen input: {path.name}")
        return
    write_json(path, value)


def load_population(annotation: dict[str, Any]) -> list[dict[str, Any]]:
    """Aggregate only category/count/area evidence, never caption absence."""
    categories = {row["id"]: row["name"] for row in annotation["categories"]}
    licenses = {row["id"]: row for row in annotation["licenses"]}
    by_id = {}
    for row in annotation["images"]:
        if row["license"] in LICENSE_IDS:
            by_id[row["id"]] = {
                "asset_id": row["id"], "coco": row, "license": licenses[row["license"]],
                "category_counts": Counter(), "category_max_area_fraction": {},
            }
    for instance in annotation["annotations"]:
        row = by_id.get(instance["image_id"])
        if row is None:
            continue
        name = categories[instance["category_id"]]
        row["category_counts"][name] += 1
        fraction = instance["area"] / (row["coco"]["width"] * row["coco"]["height"])
        areas = row["category_max_area_fraction"]
        areas[name] = max(areas.get(name, 0.0), fraction)
    for row in by_id.values():
        row["coco_categories"] = sorted(row["category_counts"])
        row["category_counts"] = dict(sorted(row["category_counts"].items()))
    return sorted(by_id.values(), key=lambda row: row["asset_id"])


def freeze_selection(
    population: list[dict[str, Any]], seed: int = SEED, random_count: int = 250,
    quotas: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Select random half first, then category strata without model scores."""
    quotas = QUOTAS if quotas is None else quotas
    if random_count < sum(quotas.values()) or random_count < 1:
        raise ValueError("at least half the sample must be independently random")
    if random_count + sum(quotas.values()) > 1000 or any(n < 1 for n in quotas.values()):
        raise ValueError("sample/strata exceed the bounded experiment")
    rows = sorted(population, key=lambda row: row["asset_id"])
    if len({row["asset_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate population IDs")
    rng = random.Random(seed)
    chosen = [{**row, "selection_stratum": "random"}
              for row in rng.sample(rows, random_count)]
    selected = {row["asset_id"] for row in chosen}
    for category, count in quotas.items():
        candidates = [row for row in rows if row["asset_id"] not in selected
                      and category in row["coco_categories"]]
        if len(candidates) < count:
            raise ValueError(f"insufficient category population: {category}")
        for row in rng.sample(candidates, count):
            chosen.append({**row, "selection_stratum": f"category:{category}"})
            selected.add(row["asset_id"])
    return chosen


def freeze_smoke(rows: list[dict[str, Any]], seed: int = SEED) -> list[int]:
    """Take a frozen 40-image diagnostic subset, independent of all model scores."""
    rng = random.Random(seed + 1)
    selected = []
    for category, count in {"cat": 10, "dog": 8, "teddy bear": 5,
                            "sheep": 3, "cow": 3, "person": 3}.items():
        pool = [row for row in rows if row["asset_id"] not in selected
                and category in row["coco_categories"]]
        if len(pool) < count:
            raise ValueError(f"insufficient smoke category: {category}")
        landscape = [row for row in pool if row["coco"]["width"] >= row["coco"]["height"]]
        portrait = [row for row in pool if row["coco"]["width"] < row["coco"]["height"]]
        rng.shuffle(landscape)
        rng.shuffle(portrait)
        for index in range(count):
            primary, backup = (landscape, portrait) if index % 2 == 0 else (portrait, landscape)
            selected.append((primary or backup).pop()["asset_id"])
    remaining = [row["asset_id"] for row in rows if row["asset_id"] not in selected]
    selected.extend(rng.sample(remaining, 40 - len(selected)))
    return selected


def make_labels(rows: list[dict[str, Any]], source_sha: str) -> dict[str, Any]:
    return {
        "provenance": {
            "kind": "COCO instance labels",
            "scope": "presence of the named COCO category, including crowd annotations",
            "source_revision": source_sha,
            "source": ANNOTATIONS_URL + "#" + MEMBER,
            "limitations": [
                "Missing instance annotations can create false negative labels.",
                "An annotated small/occluded object is still relevant; this differs from prominent-subject preference.",
                "Quantity, color, action and relation queries have no relevance judgments here.",
            ],
        },
        "queries": {
            query["query_id"]: [
                {"asset_id": row["asset_id"],
                 "relevant": (CATEGORY_QUERIES[query["query_id"]] in row["coco_categories"])
                 if query["query_id"] in CATEGORY_QUERIES else None}
                for row in rows
            ] for query in QUERIES
        },
    }


def bind_labels(labels: dict[str, Any], assets: list[dict[str, Any]]) -> dict[str, Any]:
    if any(not row.get("sha256") for row in assets):
        raise ValueError("cannot publish bound labels before all original bytes are known")
    return {**labels,
            "input_identities": [{"asset_id": row["asset_id"], "sha256": row["sha256"]}
                                 for row in assets],
            "query_definitions": QUERIES}


def fetch_complete_image(url: str, width: int, height: int) -> bytes:
    """Reject truncated responses/JPEGs before publishing a cached original."""
    from PIL import Image

    cap = 8 * 1024 * 1024
    with urllib.request.urlopen(url, timeout=30) as response:
        length = response.headers.get("Content-Length")
        if response.status != 200 or (length is not None and int(length) > cap):
            raise ValueError("unexpected or oversized public image response")
        raw = response.read(cap + 1)
        if not raw or len(raw) > cap or (length is not None and len(raw) != int(length)):
            raise ValueError("truncated or oversized public image response")
    with Image.open(io.BytesIO(raw)) as image:
        image.load()
        if image.format != "JPEG" or image.size != (width, height):
            raise ValueError("public image differs from its official dimensions/format")
    return raw


def download_annotations(directory: Path) -> tuple[Path, dict[str, Any]]:
    directory = controlled_output(directory)
    destination = controlled_output(directory / "instances_train2014.json")
    identity_file = controlled_output(directory / "annotation-identity.json")
    if destination.exists():
        identity = json.loads(identity_file.read_text(encoding="utf-8"))
        if sha256(destination) != identity["sha256"] or identity["sha256"] != ANNOTATION_SHA256:
            raise ValueError("cached annotations differ from their frozen identity")
        return destination, identity
    directory.mkdir(parents=True, exist_ok=True)
    temporary = controlled_output(destination.with_suffix(".part"))
    with RemoteZip(ANNOTATIONS_URL) as remote, zipfile.ZipFile(remote) as archive:
        entry = archive.getinfo(MEMBER)
        if entry.compress_size > 128 * 1024 * 1024 or entry.file_size > 512 * 1024 * 1024:
            raise ValueError("instance annotations exceed explicit size bounds")
        if entry.compress_type != zipfile.ZIP_DEFLATED:
            raise ValueError("unexpected annotations compression")
        remote.seek(entry.header_offset)
        header = remote.read(30)
        if header[:4] != b"PK\x03\x04":
            raise ValueError("invalid local ZIP header")
        name_size, extra_size = struct.unpack_from("<HH", header, 26)
        remote.seek(name_size + extra_size, 1)
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
                remaining -= len(compressed)
                raw = decoder.decompress(compressed, entry.file_size - total + 1)
                total += len(raw)
                if total > entry.file_size or decoder.unconsumed_tail:
                    raise ValueError("annotation expansion exceeded the declared size")
                crc = zlib.crc32(raw, crc)
                digest.update(raw)
                stream.write(raw)
                print(json.dumps({"annotation_compressed_remaining": remaining}), flush=True)
        if total != entry.file_size or crc != entry.CRC or not decoder.eof or decoder.unused_data:
            raise ValueError("annotation size/CRC/decompression mismatch")
        if digest.hexdigest() != ANNOTATION_SHA256:
            raise ValueError("official annotation bytes differ from the fixed source revision")
        identity = {"url": ANNOTATIONS_URL, "archive_bytes": remote.size,
                    "member": MEMBER, "bytes": total, "compressed_bytes": entry.compress_size,
                    "zip_crc32": crc, "sha256": digest.hexdigest(),
                    "annotation_license": "CC BY 4.0: https://cocodataset.org/#termsofuse"}
    temporary.replace(destination)
    write_json(identity_file, identity)
    return destination, identity


def prepare(out_dir: Path, existing_dataset: Path | None = None) -> dict[str, Any]:
    out_dir = controlled_output(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    annotation_path, source_identity = download_annotations(out_dir)
    population = load_population(json.loads(annotation_path.read_text(encoding="utf-8")))
    rows = freeze_selection(population)
    smoke_ids = freeze_smoke(rows)
    plan = {
        "schema": "wemm-coco-plan-v1", "seed": SEED, "sample_count": len(rows),
        "population_count": len(population), "allowed_license_ids": sorted(LICENSE_IDS),
        "source_identity": source_identity, "random_count": 250, "category_quotas": QUOTAS,
        "selected_ids": [row["asset_id"] for row in rows], "smoke_ids": smoke_ids,
        "queries": QUERIES, "selection_strata": dict(Counter(row["selection_stratum"] for row in rows)),
        "category_positive_counts": dict(Counter(name for row in rows for name in row["coco_categories"])),
        "limitations": [
            "Population is COCO train2014 CC-BY/CC-BY-SA images, not a personal photo library.",
            "Half random, half category-enriched: report random/enriched strata separately; precision depends on prevalence.",
            "Public COCO overlap with model training cannot be ruled out; this is an exploratory known-benchmark pilot.",
            "Frozen IDs are never replaced after missing files, decode failures or model results.",
            "SHA duplicates found after download are reported, not silently replaced or treated as independent evidence.",
        ],
        "performance_bounds": {"max_images": 500, "parallel_downloads": 4,
                               "max_image_bytes": 8 * 1024 * 1024, "gpu_work": False},
    }
    plan_path = out_dir / "dataset-plan.json"
    write_frozen_json(plan_path, plan)
    write_frozen_json(out_dir / "queries.json", QUERIES)
    frozen_labels = make_labels(rows, source_identity["sha256"])
    write_frozen_json(out_dir / "category-labels-frozen.json", frozen_labels)
    print(json.dumps({"frozen_count": len(rows), "random": 250,
                      "cat_count": plan["category_positive_counts"].get("cat", 0),
                      "plan_sha256": sha256(plan_path)}), flush=True)
    existing = {}
    if existing_dataset:
        for row in json.loads(existing_dataset.read_text(encoding="utf-8"))["images"]:
            existing[row["coco"]["id"]] = row
    image_dir = controlled_output(out_dir / "images")
    image_dir.mkdir(exist_ok=True)
    previous = {}
    if (out_dir / "sample.json").exists():
        previous = {row["asset_id"]: row for row in
                    json.loads((out_dir / "sample.json").read_text(encoding="utf-8"))["assets"]}
        for row in previous.values():
            if row.get("sha256") and (not Path(row["path"]).is_file()
                                      or sha256(Path(row["path"])) != row["sha256"]):
                raise ValueError("previously frozen original is missing or changed")

    def materialize(row: dict[str, Any]) -> dict[str, Any]:
        from PIL import Image
        row = dict(row)
        filename = f"COCO_train2014_{row['asset_id']:012d}.jpg"
        if filename != row["coco"]["file_name"]:
            raise ValueError("official image filename does not match ID")
        path = controlled_output(image_dir / filename)
        try:
            if not path.exists() or previous.get(row["asset_id"], {}).get("input_status") == "FAILED":
                reusable = existing.get(row["asset_id"])
                if reusable and Path(reusable["path"]).is_file():
                    if sha256(Path(reusable["path"])) != reusable["sha256"]:
                        raise ValueError("existing image SHA differs from recorded source")
                    shutil.copyfile(reusable["path"], path)
                    row["materialization"] = "reused_verified_local_original"
                else:
                    url = SOURCE + "/train2014/" + filename
                    last_error = None
                    for attempt in range(3):
                        try:
                            raw = fetch_complete_image(url, row["coco"]["width"], row["coco"]["height"])
                            temporary = controlled_output(path.with_suffix(".part"))
                            temporary.write_bytes(raw)
                            temporary.replace(path)
                            last_error = None
                            break
                        except (OSError, ValueError) as exc:
                            last_error = exc
                            if attempt < 2:
                                time.sleep(attempt + 1)
                    if last_error is not None:
                        raise last_error
                    row["materialization"] = "downloaded_official_original"
            else:
                row["materialization"] = previous.get(row["asset_id"], {}).get(
                    "materialization", "existing_local_original")
            with Image.open(path) as image:
                image.load()
                if image.format != "JPEG" or image.size != (row["coco"]["width"], row["coco"]["height"]):
                    raise ValueError("image format/dimensions differ from official metadata")
                row.update(format=image.format, width=image.width, height=image.height,
                           exif_orientation=image.getexif().get(274, 1))
            row.update(path=str(path.resolve()), sha256=sha256(path), bytes=path.stat().st_size,
                       input_status="READY_FOR_HASH_CHECK")
        except (OSError, ValueError) as exc:
            row.update(path=str(path.resolve()), input_status="FAILED", error=str(exc))
        return row

    with ThreadPoolExecutor(max_workers=4) as pool:
        assets = list(pool.map(materialize, rows))
    hashes = defaultdict(list)
    for row in assets:
        if row.get("sha256"):
            hashes[row["sha256"]].append(row["asset_id"])
    duplicates = {digest: ids for digest, ids in hashes.items() if len(ids) > 1}
    sample = {"schema": "wemm-pilot-public-sample-v1", "source_identity": source_identity,
              "source_roots": [str(image_dir.resolve())], "assets": assets,
              "plan_sha256": sha256(plan_path), "content_duplicates": duplicates}
    write_json(out_dir / "sample.json", sample)
    by_id = {row["asset_id"]: row for row in assets}
    write_json(out_dir / "smoke40.json", {**sample, "assets": [by_id[i] for i in smoke_ids]})
    if all(row.get("sha256") for row in assets):
        write_frozen_json(out_dir / "labels.json", bind_labels(frozen_labels, assets))
        smoke_assets = [by_id[i] for i in smoke_ids]
        smoke_labels = make_labels(smoke_assets, source_identity["sha256"])
        write_frozen_json(out_dir / "labels-smoke40.json", bind_labels(smoke_labels, smoke_assets))
    summary = {"status": "READY" if all(row["input_status"] == "READY_FOR_HASH_CHECK" for row in assets)
               and not duplicates else "INCOMPLETE_OR_DUPLICATE_INPUTS",
               "assets": len(assets), "smoke_assets": len(smoke_ids),
               "source": source_identity, "sample_sha256": sha256(out_dir / "sample.json"),
               "strata": plan["selection_strata"], "positive_counts": plan["category_positive_counts"],
               "downloads": dict(Counter(row.get("materialization", "failed") for row in assets)),
               "failures": [{"asset_id": row["asset_id"], "error": row.get("error")}
                            for row in assets if row["input_status"] != "READY_FOR_HASH_CHECK"],
               "content_duplicates": duplicates}
    write_json(out_dir / "preparation-summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--reuse-dataset", type=Path)
    args = parser.parse_args()
    summary = prepare(args.out_dir, args.reuse_dataset)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["status"] != "READY":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
