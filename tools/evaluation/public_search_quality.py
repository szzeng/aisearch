"""Small, reproducible Chinese image-retrieval experiments without a product index.

``prepare`` downloads the official COCO-CN annotation archive and selected CC-BY or
CC-BY-SA COCO originals. ``run`` uses already installed model weights, writes vectors
and rankings under the explicitly chosen result directory, and never opens a catalog.
Image-disjoint tuning/holdout galleries avoid tuning on a held-out image as a negative.
These are paired-caption retrieval scores, not exhaustive human relevance judgments.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import random
import re
import statistics
import struct
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CN_URL = "https://huggingface.co/datasets/AIMClab-RUC/COCO-CN/resolve/main/coco-cn-version1805v1.1.tar.gz"
CN_SHA = "6c126cd8455363a404806e452ec75066a8fc96d73922d9357d993fcdd1d40b8a"
COCO_URL = "http://images.cocodataset.org/annotations/annotations_trainval2014.zip"
ALLOWED_LICENSES = {4, 5}  # Attribution / Attribution-ShareAlike, from COCO's license table.


class ExistingRunOutputError(FileExistsError):
    """Refuse reuse without writing even a failure record into earlier evidence."""


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url: str, *, cap: int, headers: dict[str, str] | None = None) -> bytes:
    """Bound every download, including when a server ignores a Range header."""
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=30) as response:
        if headers and "Range" in headers and response.status != 206:
            raise ValueError("server ignored the bounded range request")
        data = response.read(cap + 1)
    if len(data) > cap:
        raise ValueError("download exceeds its explicit size budget")
    return data


class RemoteZip(io.RawIOBase):
    """Read only a ZIP directory and specifically requested compressed members."""

    def __init__(self, url: str):
        self.url = url
        self.position = 0
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"Range": "bytes=0-0"}), timeout=30
        ) as response:
            if response.status != 206:
                raise ValueError("remote ZIP requires HTTP range support")
            self.size = int(response.headers["Content-Range"].rsplit("/", 1)[1])

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = 0) -> int:
        base = {0: 0, 1: self.position, 2: self.size}[whence]
        self.position = base + offset
        if not 0 <= self.position <= self.size:
            raise ValueError("remote ZIP seek outside bounds")
        return self.position

    def read(self, size: int = -1) -> bytes:
        end = self.size if size < 0 else min(self.size, self.position + size)
        if end == self.position:
            return b""
        length = end - self.position
        if length > 32 * 1024 * 1024:
            raise ValueError("remote ZIP member exceeds experiment budget")
        data = fetch(
            self.url, cap=length,
            headers={"Range": f"bytes={self.position}-{end - 1}"},
        )
        if len(data) != length:
            raise ValueError("truncated remote ZIP range")
        self.position = end
        return data


def zip_member(reader: RemoteZip, entry: zipfile.ZipInfo) -> bytes:
    reader.seek(entry.header_offset)
    header = reader.read(30)
    if header[:4] != b"PK\x03\x04":
        raise ValueError("invalid ZIP local header")
    filename_size, extra_size = struct.unpack_from("<HH", header, 26)
    reader.seek(filename_size + extra_size, 1)
    compressed = reader.read(entry.compress_size)
    if entry.compress_type != zipfile.ZIP_DEFLATED or entry.file_size > 64 * 1024 * 1024:
        raise ValueError("unexpected caption member encoding or size")
    decoder = zlib.decompressobj(-15)
    data = decoder.decompress(compressed, entry.file_size + 1)
    if (
        len(data) != entry.file_size or not decoder.eof
        or decoder.unconsumed_tail or decoder.unused_data
        or zlib.crc32(data) != entry.CRC
    ):
        raise ValueError("caption member size/CRC mismatch")
    return data


def public_image_path(directory: Path, filename: str, image_id: str) -> Path:
    if not re.fullmatch(r"COCO_(train2014|val2014)_\d{12}\.jpg", filename):
        raise ValueError("invalid public image filename")
    if filename != image_id + ".jpg":
        raise ValueError("image filename does not match its selected ID")
    destination = (directory / filename).resolve()
    if destination.parent != directory.resolve():
        raise ValueError("public image path escapes the output directory")
    return destination


def load_annotations(
    directory: Path, source_split: str = "test",
) -> tuple[list[str], dict[str, list[str]], dict[str, Any]]:
    if source_split not in {"test", "train", "val"}:
        raise ValueError("unknown official COCO-CN source split")
    archive = directory / "coco-cn-version1805v1.1.tar.gz"
    if not archive.exists():
        archive.write_bytes(fetch(CN_URL, cap=17 * 1024 * 1024))
    if sha256(archive) != CN_SHA:
        raise ValueError("official COCO-CN archive hash does not match")
    prefix = "coco-cn-version1805v1.1/"
    with tarfile.open(archive, "r:gz") as handle:
        # Read exact known regular members; never extract supplied paths or code.
        selected_ids = handle.extractfile(prefix + f"coco-cn_{source_split}.txt").read().decode("utf-8").splitlines()
        caption_text = handle.extractfile(prefix + "imageid.human-written-caption.txt").read().decode("utf-8")
    captions: dict[str, list[str]] = {}
    for line in caption_text.splitlines():
        key, caption = line.split("\t", 1)
        captions.setdefault(key.split("#", 1)[0], []).append(caption.strip())
    selected_id_set = set(selected_ids)
    cache_identity = {
        "source_split": source_split, "annotations_sha256": CN_SHA,
        "ids_sha256": hashlib.sha256("\n".join(sorted(selected_id_set)).encode()).hexdigest(),
    }
    # The old unqualified cache only contained official test IDs. Never reuse it
    # for train/val, and bind even a named cache to the actual annotation IDs.
    metadata_path = directory / f"coco-image-license-metadata-{source_split}.json"
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if any(metadata.get(key) != value for key, value in cache_identity.items()):
            raise ValueError("public metadata cache does not match its source split")
    else:
        reader = RemoteZip(COCO_URL)
        with zipfile.ZipFile(reader) as handle:
            entries = {entry.filename: entry for entry in handle.infolist()}
        images: dict[str, Any] = {}
        licenses: dict[str, Any] = {}
        for split in ("train2014", "val2014"):
            member = f"annotations/captions_{split}.json"
            decoded = json.loads(zip_member(reader, entries[member]))
            licenses.update({str(item["id"]): item for item in decoded["licenses"]})
            for item in decoded["images"]:
                key = Path(item["file_name"]).stem
                if key in selected_id_set:
                    images[key] = item
        metadata = {"source": COCO_URL, **cache_identity, "licenses": licenses, "images": images}
        write_json(metadata_path, metadata)
    return selected_ids, captions, metadata


def select_images(
    test_ids: list[str], captions: dict[str, list[str]], metadata: dict[str, Any],
    *, count: int, tuning: int, seed: int, excluded_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    if not 0 < tuning < count:
        raise ValueError("tuning size must leave a nonempty holdout")
    eligible = sorted({
        key for key in test_ids
        if key not in (excluded_ids or ()) and key in captions and key in metadata["images"]
        and int(metadata["images"][key]["license"]) in ALLOWED_LICENSES
    })
    if len(eligible) < count:
        raise ValueError(f"only {len(eligible)} eligible licensed images for requested {count}")
    selected = random.Random(seed).sample(eligible, count)
    return [
        {
            "id": key,
            "split": "tuning" if index < tuning else "holdout",
            "captions": captions[key],
            "coco": metadata["images"][key],
            "license": metadata["licenses"][str(metadata["images"][key]["license"])],
        }
        for index, key in enumerate(selected)
    ]


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    directory = args.output.resolve()
    if (directory / "dataset.json").exists():
        raise ExistingRunOutputError(f"preserve existing dataset; choose another output directory: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    source_split = getattr(args, "source_split", "test")
    excluded_ids: set[str] = set()
    excluded_hashes: set[str] = set()
    exclusion = None
    excluded_dataset = getattr(args, "exclude_dataset", None)
    if excluded_dataset is not None:
        excluded_path = Path(excluded_dataset).resolve()
        excluded_bytes = excluded_path.read_bytes()
        previous = json.loads(excluded_bytes)
        if previous.get("schema") != "public-search-quality-dataset-v1" or not isinstance(previous.get("images"), list):
            raise ValueError("exclude dataset must be a prepared public dataset")
        for row in previous["images"]:
            key, digest = row.get("id"), row.get("sha256")
            if not isinstance(key, str) or not key or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("exclude dataset needs an image ID and SHA256 for every image")
            excluded_ids.add(key)
            excluded_hashes.add(digest)
        exclusion = {
            "path": str(excluded_path), "sha256": hashlib.sha256(excluded_bytes).hexdigest(),
            "images": len(previous["images"]), "unique_ids": len(excluded_ids),
            "unique_content_hashes": len(excluded_hashes),
        }
    test_ids, captions, metadata = load_annotations(directory, source_split)
    selected = select_images(
        test_ids, captions, metadata, count=args.count, tuning=args.tuning, seed=args.seed,
        excluded_ids=excluded_ids,
    )
    images_dir = directory / "images"
    images_dir.mkdir(exist_ok=True)

    def download(row: dict[str, Any]) -> dict[str, Any]:
        path = public_image_path(images_dir, row["coco"]["file_name"], row["id"])
        url = str(row["coco"]["coco_url"])
        if not url.startswith("http://images.cocodataset.org/"):
            raise ValueError("unexpected image download origin")
        if not path.exists():
            path.write_bytes(fetch(url, cap=4 * 1024 * 1024))
        from PIL import Image
        with Image.open(path) as image:
            image.verify()
        digest = sha256(path)
        if digest in excluded_hashes:
            raise ValueError(f"public image content overlaps the excluded dataset: {row['id']}")
        return {**row, "path": str(path), "sha256": digest, "bytes": path.stat().st_size}

    with ThreadPoolExecutor(max_workers=6) as pool:
        images = list(pool.map(download, selected))
    if len({row["sha256"] for row in images}) != len(images):
        raise ValueError("selected public image IDs contain duplicate content")
    result = {
        "schema": "public-search-quality-dataset-v1", "seed": args.seed,
        "source_split": source_split, "excluded_dataset": exclusion,
        "selection": f"fixed random subset of official COCO-CN {source_split} IDs after license eligibility and exclusion",
        "split_policy": "image-disjoint; each split uses its own image gallery",
        "annotations_source": CN_URL, "annotations_sha256": CN_SHA,
        "annotations_license": "MIT as declared by the official COCO-CN HF card",
        "image_license_policy": "Only COCO license IDs 4 (CC-BY) and 5 (CC-BY-SA); original source/metadata retained, no redistribution in Git.",
        "terms": "https://github.com/cocodataset/cocodataset.github.io/blob/master/dataset/termsofuse.htm",
        "limitations": [
            "Known paired image is the positive; other images are not exhaustively judged irrelevant.",
            "Subset scores are not official full COCO-CN scores or a 300000-image personal-library guarantee.",
            "Public benchmark overlap with model training cannot be ruled out.",
        ],
        "images": images,
    }
    write_json(directory / "dataset.json", result)
    return {"status": "ok", "dataset": str(directory / "dataset.json"), "images": len(images), "bytes": sum(row["bytes"] for row in images)}


def ranked_metrics(scores: Any, queries: list[dict[str, Any]], image_ids: list[str]) -> dict[str, Any]:
    import numpy as np
    values = np.asarray(scores)
    if values.shape != (len(queries), len(image_ids)) or not np.isfinite(values).all():
        raise ValueError("invalid score matrix")
    rows = []
    for query, scores_row in zip(queries, values, strict=True):
        ordered = np.argsort(-scores_row, kind="stable")
        ranked = [image_ids[index] for index in ordered]
        rank = ranked.index(query["image_id"]) + 1
        rows.append({**query, "rank": rank, "top20": ranked[:20]})
    return {
        "queries": len(rows), "gallery_images": len(image_ids),
        **{f"recall_at_{k}": sum(row["rank"] <= k for row in rows) / len(rows) for k in (1, 5, 10, 50)},
        "mrr": sum(1 / row["rank"] for row in rows) / len(rows),
        "median_rank": statistics.median(row["rank"] for row in rows),
        "per_query": rows,
    }


def normalized(values: Any) -> Any:
    import numpy as np
    vectors = np.asarray(values, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    if not np.isfinite(vectors).all() or np.any(norms == 0):
        raise ValueError("invalid embedding")
    return vectors / norms


def resource_facts() -> dict[str, Any]:
    import importlib.metadata
    result: dict[str, Any] = {"python": sys.version, "pid": os.getpid()}
    result["packages"] = {
        name: importlib.metadata.version(name)
        for name in ("numpy", "pillow", "onnxruntime-gpu", "torch", "transformers")
    }
    gpu = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version", "--format=csv,noheader"],
        capture_output=True, text=True, check=False, timeout=15,
    )
    result["gpu"] = gpu.stdout.strip()
    try:
        import psutil
        result["process_rss_bytes"] = psutil.Process().memory_info().rss
    except ImportError:
        pass
    return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    directory = args.output.resolve()
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise ExistingRunOutputError(f"preserve existing evidence; choose an empty output directory: {directory}")
    import numpy as np
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    directory.mkdir(parents=True, exist_ok=True)
    data = json.loads(args.dataset.read_text(encoding="utf-8"))
    if data.get("schema") != "public-search-quality-dataset-v1":
        raise ValueError("expected an explicitly prepared public dataset")
    images = data["images"]
    for row in images:
        if sha256(Path(row["path"])) != row["sha256"]:
            raise ValueError("public image changed after dataset preparation")
    queries = [
        {"id": f"{row['id']}#{index}", "image_id": row["id"], "split": row["split"], "text": caption}
        for row in images for index, caption in enumerate(row["captions"])
    ]
    facts_before = resource_facts()
    from photo_search.models.embedding.factory import build_adapter
    manifest = ROOT / "tools/prepare_models/manifests" / (
        "chinese-clip-vit-b-16-exif-v2.json" if args.model == "clip"
        else "qwen3-vl-embedding-2b-exif-v2.json"
    )
    if args.model == "qwen":
        native = json.loads(manifest.read_text(encoding="utf-8"))
        native["provider"]["provider_id"] = "qwen3-vl-embedding-2b-quality-native2048"
        native["embedding"]["dimension"] = 2048
        manifest = directory / "experimental-native-manifest.json"
        write_json(manifest, native)
    adapter = build_adapter(args.model_dir, manifest, device=args.device)
    started = time.perf_counter()
    adapter.load()
    load_seconds = time.perf_counter() - started
    facts_loaded = resource_facts()
    text_vectors = []
    text_times = []
    for query in queries:
        started = time.perf_counter()
        text_vectors.append(adapter.encode_text(query["text"]))
        text_times.append(time.perf_counter() - started)
    text_vectors = normalized(text_vectors)
    image_vectors: dict[str, list[Any]] = {"center" if args.model == "clip" else "native": []}
    image_times: dict[str, list[float]] = {key: [] for key in image_vectors}
    if args.model == "clip":
        image_vectors["letterbox"] = []
        image_times["letterbox"] = []
    for index, row in enumerate(images):
        started = time.perf_counter()
        key = "center" if args.model == "clip" else "native"
        image_vectors[key].append(adapter.encode_image(Path(row["path"])))
        image_times[key].append(time.perf_counter() - started)
        if args.model == "clip":
            from PIL import Image, ImageOps
            started = time.perf_counter()
            with Image.open(row["path"]) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                contained = ImageOps.contain(image, (224, 224), Image.Resampling.BICUBIC)
                background = tuple(int(round(float(value) * 255)) for value in adapter._mean[:, 0, 0])
                letterbox = Image.new("RGB", (224, 224), background)
                letterbox.paste(contained, ((224 - contained.width) // 2, (224 - contained.height) // 2))
            pixels = np.asarray(letterbox, dtype=np.float32).transpose(2, 0, 1) / 255
            pixels = ((pixels - adapter._mean) / adapter._std)[None, ...]
            encoded = adapter._image_session.run(None, {"pixel_values": pixels})[0][0]
            image_vectors["letterbox"].append(normalized(encoded))
            image_times["letterbox"].append(time.perf_counter() - started)
        if (index + 1) % 32 == 0:
            print(f"encoded {index + 1}/{len(images)} {args.model}", flush=True)
    vectors = {key: normalized(value) for key, value in image_vectors.items()}
    np.savez_compressed(directory / "vectors.npz", queries=text_vectors, **vectors)
    variants = ("center", "letterbox", "two_view_max") if args.model == "clip" else ("512", "1024", "2048")
    result: dict[str, Any] = {
        "status": "ok", "model": args.model, "model_spec": adapter.spec().as_dict(),
        "dataset_sha256": sha256(args.dataset), "manifest_sha256": sha256(manifest),
        "resources_before": facts_before, "resources_loaded": facts_loaded,
        "resources_after": resource_facts(), "load_seconds": load_seconds,
        "query_seconds": sum(text_times), "image_seconds": {key: sum(times) for key, times in image_times.items()},
        "query_latency_ms": {"p50": 1000 * statistics.median(text_times), "p95": 1000 * sorted(text_times)[math.ceil(.95 * len(text_times)) - 1]},
        "variants": {}, "limits": data["limitations"],
        "experiment_note": "No catalog or production index opened. All model weights already installed. CUDA results are on the reported machine, not a GTX1660 claim.",
    }
    for variant in variants:
        split_results = {}
        for split in ("tuning", "holdout"):
            q_indices = [index for index, row in enumerate(queries) if row["split"] == split]
            i_indices = [index for index, row in enumerate(images) if row["split"] == split]
            q = text_vectors[q_indices]
            if args.model == "qwen":
                dimension = int(variant)
                matrix = normalized(q[:, :dimension]) @ normalized(vectors["native"][i_indices, :dimension]).T
            elif variant == "two_view_max":
                matrix = np.maximum(q @ vectors["center"][i_indices].T, q @ vectors["letterbox"][i_indices].T)
            else:
                matrix = q @ vectors[variant][i_indices].T
            split_results[split] = ranked_metrics(matrix, [queries[index] for index in q_indices], [images[index]["id"] for index in i_indices])
        result["variants"][variant] = split_results
    write_json(directory / "result.json", result)
    return {"status": "ok", "result": str(directory / "result.json"), "variants": {key: {split: {k: v for k, v in values.items() if k != "per_query"} for split, values in splits.items()} for key, splits in result["variants"].items()}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("prepare")
    setup.add_argument("--output", type=Path, required=True)
    setup.add_argument("--count", type=int, default=240)
    setup.add_argument("--tuning", type=int, default=60)
    setup.add_argument("--seed", type=int, default=20261016)
    setup.add_argument("--source-split", choices=("test", "train", "val"), default="test")
    setup.add_argument("--exclude-dataset", type=Path, help="Previously inspected dataset to exclude by image ID and content SHA256")
    experiment = commands.add_parser("run")
    experiment.add_argument("--dataset", type=Path, required=True)
    experiment.add_argument("--output", type=Path, required=True)
    experiment.add_argument("--model", choices=("clip", "qwen"), required=True)
    experiment.add_argument("--model-dir", type=Path, required=True)
    experiment.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    args = parser.parse_args()
    started = time.perf_counter()
    exit_code = 0
    preserve_existing_output = False
    try:
        result = prepare(args) if args.command == "prepare" else run(args)
    except Exception as error:
        import traceback
        traceback.print_exc()
        result = {"status": "failed", "error_type": type(error).__name__, "message": str(error)}
        exit_code = 1
        preserve_existing_output = isinstance(error, ExistingRunOutputError)
    result.update({"exit_code": exit_code, "elapsed_seconds": time.perf_counter() - started})
    if not preserve_existing_output:
        write_json(args.output / "execution.json", result)
    print(json.dumps(result, ensure_ascii=False), file=sys.stderr if preserve_existing_output else sys.stdout, flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
