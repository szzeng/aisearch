"""Prepare one complete, pinned public album; keep judgments out of encoder inputs.

Album 3 was selected by the smallest official gallery count before inference.
The original ZIP can be obtained from the publisher's Drive/OBOX links. This
tool never scans a personal library or silently drops a failed/extra image.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import stat
import traceback
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from tools.evaluation.prepare_wemm_coco import controlled_output, write_frozen_json, write_json
from tools.evaluation.public_search_quality import sha256

ALBUM = 3
IMAGE_COUNT = 1047
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_PIXELS = 64_000_000
MAX_ARCHIVE_BYTES = 6_000_000_000
FULL_REVISION = "9c3480a560cd650f5484e852d0730504fce4cbf1"
PROTECTED_REVISION = "9293ba6c0a6d87fddf0f25ab528782fc530150c4"
ARCHIVE_SHA256 = "8239608ea1b084c3145b078ce31f5153cf3b5b40cc4f692f72f9c56aefdabfd5"
SOURCE_FILES = {
    "gallery": (
        "protected/album3/images/embeddings/Qwen3-VL-Embedding-2B/metadata.json",
        "1a25bfeb249d4650ca2fb2cf6c5b7ee8531ad664d94ccfc3927b1843194cadb1"),
    "metadata": (
        "protected/album3/images/metadata/metadata.json",
        "254c74147bf670d09f07911ad72e8c1b3451d7b437447269ec07c450823faae0"),
    "validation": (
        "data/validation/album3_validation.json",
        "ccab5c068e757b196b02eec3b04a41684112fc064d191b00b6b87bcb0c21ac53"),
}


def safe_name(value: Any) -> str:
    if (not isinstance(value, str) or not value or value in (".", "..")
            or any(char in value for char in '/\\:\x00') or value.endswith((".", " "))
            or re.match(r"(?i)^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)", value)):
        raise ValueError("invalid gallery filename")
    return value


def validate_gallery(value: dict[str, Any]) -> list[str]:
    names = value.get("filenames")
    if not isinstance(names, list) or not 1 <= len(names) <= 2000:
        raise ValueError("gallery outside bounded album size")
    names = [safe_name(name) for name in names]
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("duplicate or Windows-colliding gallery filename")
    if any(Path(name).suffix.lower() != ".jpg" for name in names):
        raise ValueError("unexpected format in frozen JPEG album")
    return sorted(names)


def asset_id(name: str) -> str:
    return f"photobench-album{ALBUM}:{name}"


def query_group(row: dict[str, Any]) -> str:
    texts = [row.get("query_cn"), row.get("query_en")]
    if any(not isinstance(text, str) or not text.strip() or len(text) > 2000 for text in texts):
        raise ValueError("query requires bounded nonempty Chinese and English text")
    return hashlib.sha256(("photobench-album3-query-v1\0" + "\0".join(texts)).encode()).hexdigest()


def prepare_queries(rows: list[dict], names: list[str], calibration_count: int = 30) -> dict:
    if (not isinstance(rows, list) or not 1 <= len(rows) <= 100
            or type(calibration_count) is not int or not 0 <= calibration_count < len(rows)):
        raise ValueError("invalid query/calibration count")
    groups = [query_group(row) for row in rows]
    if len(set(groups)) != len(groups):
        raise ValueError("duplicate bilingual query")
    calibration = set(sorted(groups)[:calibration_count])
    names_set = set(names)
    queries, judgments, splits = [], [], []
    for group, row in sorted(zip(groups, rows, strict=True)):
        gt = row.get("ground_truth")
        if (not isinstance(gt, list) or any(not isinstance(name, str) for name in gt)
                or len(set(gt)) != len(gt) or not set(gt) <= names_set):
            raise ValueError("ground truth contains duplicate or out-of-gallery members")
        if (type(row.get("ground_truth_count")) is not int
                or row["ground_truth_count"] != len(gt)):
            raise ValueError("ground truth count mismatch")
        source = row.get("Source")
        if source not in ("V", "M", "F", "VM", "VF", "MF", "VMF"):
            raise ValueError("unknown official query source")
        split = "calibration" if group in calibration else "test"
        for language in ("cn", "en"):
            qid = f"pb3-{group}-{language}"
            queries.append({"query_id": qid, "text": row[f"query_{language}"]})
            splits.append({"query_id": qid, "group_id": group, "language": language, "split": split})
            judgments.append({
                "query_id": qid, "group_id": group, "language": language, "split": split,
                "source": source, "ground_truth_asset_ids": [asset_id(name) for name in gt],
                "dimensions": {key: row.get(key) for key in
                               ("Location", "Time", "Person", "Object", "Concept", "Genre")},
            })
    return {"queries": queries, "splits": splits, "judgments": judgments}


def validate_metadata(value: Any, names: list[str]) -> dict:
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError("metadata must cover the complete gallery exactly")
    result = {}
    for name in names:
        row = value[name]
        if not isinstance(row, dict) or set(row) != {"latitude", "longitude", "time"}:
            raise ValueError("unexpected metadata schema")
        for field, bound in (("latitude", 90), ("longitude", 180)):
            number = row[field]
            if number is not None and (type(number) not in (int, float)
                                       or not math.isfinite(number) or not -bound <= number <= bound):
                raise ValueError("invalid coordinate")
        if (row["latitude"] is None) != (row["longitude"] is None):
            raise ValueError("partial coordinate")
        timestamp = None if row["time"] == "" else row["time"]
        if timestamp is not None:
            if not isinstance(timestamp, str):
                raise ValueError("invalid timestamp")
            datetime.fromisoformat(timestamp)
        result[asset_id(name)] = {**row, "time": timestamp}
    return result


def archive_members(archive: zipfile.ZipFile, names: list[str]) -> dict[str, zipfile.ZipInfo]:
    expected = set(names)
    found = {}
    total = 0
    if len(archive.infolist()) > 4000:
        raise ValueError("archive member count exceeds bound")
    for member in archive.infolist():
        path = PurePosixPath(member.filename)
        if ("\\" in member.filename or path.is_absolute() or ".." in path.parts
                or any(":" in part for part in path.parts)
                or stat.S_ISLNK(member.external_attr >> 16)):
            raise ValueError("unsafe archive member")
        if member.is_dir():
            if member.filename != f"album{ALBUM}/":
                raise ValueError("unexpected archive directory")
            continue
        if len(path.parts) != 2 or path.parts[0] != f"album{ALBUM}":
            raise ValueError("archive member is outside selected album")
        name = safe_name(path.name)
        if name not in expected or name in found:
            raise ValueError("unexpected or duplicate archive image")
        if not 1 <= member.file_size <= MAX_IMAGE_BYTES or member.flag_bits & 1:
            raise ValueError("oversized or encrypted archive image")
        total += member.file_size
        if total > MAX_ARCHIVE_BYTES:
            raise ValueError("expanded archive exceeds bound")
        found[name] = member
    if set(found) != expected:
        raise ValueError("archive does not contain complete gallery")
    return found


def image_info(payload: bytes) -> dict[str, int | str]:
    from PIL import Image
    if not 1 <= len(payload) <= MAX_IMAGE_BYTES:
        raise ValueError("image bytes exceed bound")
    with Image.open(io.BytesIO(payload)) as image:
        if image.format not in {"JPEG", "MPO"} or image.width * image.height > MAX_PIXELS:
            raise ValueError("unexpected image format or pixel bound")
        orientation = int(image.getexif().get(274, 1))
        # This album uses 0 for unknown on 305 images. Pillow leaves it unrotated;
        # preserve the raw value rather than guessing an upright direction.
        if orientation not in range(0, 9):
            raise ValueError("invalid EXIF orientation")
        image.load()
        return {"width": image.width, "height": image.height, "exif_orientation": orientation,
                "format": image.format, "selected_frame": 0,
                "frame_count": getattr(image, "n_frames", 1)}


def prepare(archive_path: Path, source_root: Path, output: Path) -> dict:
    archive_path, source_root, output = map(controlled_output, (archive_path, source_root, output))
    if (not archive_path.is_file() or archive_path.stat().st_size > MAX_ARCHIVE_BYTES
            or sha256(archive_path) != ARCHIVE_SHA256):
        raise ValueError("archive differs from frozen OBOX album3 release")
    sources, values = {}, {}
    for key, (relative, digest) in SOURCE_FILES.items():
        path = controlled_output(source_root / relative)
        if not path.is_file() or path.stat().st_size > 1_000_000 or sha256(path) != digest:
            raise ValueError(f"source identity mismatch: {key}")
        values[key] = json.loads(path.read_text(encoding="utf-8"))
        repo = "PhotoBench" if key == "validation" else "PhotoBench-Protected"
        rev = FULL_REVISION if key == "validation" else PROTECTED_REVISION
        sources[key] = {"url": f"https://huggingface.co/datasets/SorrowTea/{repo}/resolve/{rev}/{relative}",
                        "sha256": digest}
    names = validate_gallery(values["gallery"])
    if len(names) != IMAGE_COUNT or len(values["validation"]) != 100:
        raise ValueError("official frozen album/query count changed")
    q = prepare_queries(values["validation"], names)
    metadata = validate_metadata(values["metadata"], names)
    output.mkdir(parents=True, exist_ok=True)
    plan = {
        "schema": "photobench-plan-v1", "album": ALBUM,
        "selection": "smallest complete official album by image count, before inference",
        "official_gallery_counts": {"1": 1069, "2": 1466, "3": 1047},
        "source_identity": sources, "archive_sha256": ARCHIVE_SHA256,
        "gallery_filenames": names, "split_policy": "sort SHA256 bilingual query identity; first 30 calibration, remaining 70 test; languages grouped",
        "splits": q["splits"],
        "rerank_subset": sorted({row["group_id"] for row in q["splits"]
                                 if row["split"] == "test"})[:10],
        "limits": {"images": 2000, "queries": 200, "image_bytes": MAX_IMAGE_BYTES, "pixels": MAX_PIXELS},
        "scope": "complete album3 / official validation; local split, not official held-out leaderboard",
        "metadata_normalization": "official empty timestamp becomes null (unknown); no time/location inference",
        "image_decode": "unmodified original JPEG/MPO bytes; main frame 0; EXIF orientation recorded; model performs standard EXIF/RGB processing",
    }
    # Freeze intent and query inputs before reading pixels or running a model.
    write_frozen_json(output / "dataset-plan.json", plan)
    write_frozen_json(output / "queries.json", q["queries"])
    write_frozen_json(output / "evaluation.json", {"schema": "photobench-evaluation-v1",
                      "judgment_source": "official full validation ground truth", "queries": q["judgments"]})
    write_frozen_json(output / "metadata.json", metadata)
    assets, duplicate_hashes = [], {}
    image_root = controlled_output(output / "images")
    image_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        members = archive_members(archive, names)
        for name in names:
            raw = archive.read(members[name])  # CRC is checked by zipfile.
            digest = hashlib.sha256(raw).hexdigest()
            info = image_info(raw)
            target = controlled_output(image_root / name)
            if target.exists():
                if not target.is_file() or sha256(target) != digest:
                    raise ValueError("existing extracted image content changed")
            else:
                target.write_bytes(raw)
            assets.append({"asset_id": asset_id(name), "path": str(target), "sha256": digest,
                           "bytes": len(raw), **info})
            duplicate_hashes.setdefault(digest, []).append(asset_id(name))
    duplicates = {key: rows for key, rows in duplicate_hashes.items() if len(rows) > 1}
    if duplicates:
        write_json(output / "duplicate-content.json", duplicates)
        raise ValueError("duplicate content requires explicit gallery expansion mapping; no image was dropped")
    sample = {"schema": "wemm-pilot-public-sample-v1", "source_identity": sources,
              "source_roots": [str(image_root)], "assets": assets}
    write_frozen_json(output / "sample.json", sample)
    summary = {"status": "COMPLETE", "images": len(assets), "queries": len(q["queries"]),
               "query_groups": 100, "calibration_groups": 30, "test_groups": 70,
               "no_match_query_groups": sum(not row["ground_truth"] for row in values["validation"]),
               "source_counts": dict(Counter(row["Source"] for row in values["validation"])),
               "archive_sha256": ARCHIVE_SHA256, "plan_sha256": sha256(output / "dataset-plan.json"),
               "sample_sha256": sha256(output / "sample.json"), "source_sha256": sha256(Path(__file__))}
    summary["derived_sha256"] = {name: sha256(output / name) for name in
                                ("dataset-plan.json", "sample.json", "queries.json",
                                 "evaluation.json", "metadata.json")}
    write_json(output / "preparation-summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--junit", type=Path, required=True)
    args = parser.parse_args()
    junit = controlled_output(args.junit)
    if junit.exists():
        raise ValueError("use a fresh JUnit path; preserve historical evidence")
    case = ET.Element("testcase", name="complete_photobench_album3", classname="dataset.preparation")
    failed = False
    try:
        print(json.dumps(prepare(args.archive, args.source_root, args.output), ensure_ascii=False))
        return 0
    except BaseException:
        failed = True
        ET.SubElement(case, "failure", message="album preparation failed").text = traceback.format_exc()
        raise
    finally:
        suite = ET.Element("testsuite", name="photobench", tests="1", failures=str(int(failed)), errors="0")
        suite.append(case)
        junit.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(suite).write(junit, encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    raise SystemExit(main())
