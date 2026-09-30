"""Download only public COCO-CN/Flickr8k test images, with provenance and checksums."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import shutil
import tarfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from photo_search.config import resolve_data_root


def fetch(url: str, path: Path) -> None:
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".download")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as out:
                shutil.copyfileobj(response, out, 1024 * 1024)
            temporary.replace(path)
            return
        except Exception:
            temporary.unlink(missing_ok=True)
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def revision(repo: str) -> str:
    with urllib.request.urlopen(f"https://huggingface.co/api/datasets/{repo}", timeout=30) as inp:
        return str(json.load(inp)["sha"])


def prepare(root: Path, name: str) -> None:
    directory = root / name
    manifest = directory / "dataset.json"
    if manifest.exists():
        data = json.loads(manifest.read_text())
    elif name == "coco-cn":
        repo = "AIMClab/COCO-CN"
        rev = revision(repo)
        url = f"https://huggingface.co/datasets/{repo}/resolve/{rev}/coco-cn-version1805v1.1.tar.gz"
        archive = directory / "annotations.tar.gz"
        fetch(url, archive)
        with tarfile.open(archive) as bundle:
            prefix = "coco-cn-version1805v1.1/"
            ids = bundle.extractfile(prefix + "coco-cn_test.txt").read().decode().splitlines()
            captions = bundle.extractfile(prefix + "imageid.human-written-caption.txt").read().decode()
        wanted = set(ids)
        queries = []
        for line in captions.splitlines():
            key, text = line.split("\t", 1)
            image_id = key.split("#")[0]
            if image_id in wanted:
                queries.append({"id": key, "text": text, "image": image_id + ".jpg"})
        data = {"name": name, "language": "zh", "split": "official test",
                "source": "https://github.com/li-xirong/coco-cn", "revision": rev,
                "annotation_url": url, "annotation_kind": "human-written (no machine translation)",
                "images": [{"file": key + ".jpg", "url":
                    f"https://s3.amazonaws.com/images.cocodataset.org/{key.split('_')[1]}/{key}.jpg"}
                           for key in ids], "queries": queries}
    else:
        repo = "intro/flickr8k"
        rev = revision(repo)
        base = f"https://huggingface.co/datasets/{repo}/resolve/{rev}/test/"
        metadata = directory / "metadata.csv"
        fetch(base + "metadata.csv", metadata)
        rows = list(csv.DictReader(io.StringIO(metadata.read_text())))
        queries = [{"id": f"{r['file_name']}#{i}", "text": r[f"caption_{i}"],
                    "image": r["file_name"]} for r in rows for i in range(5)]
        data = {"name": name, "language": "en", "split": "mirror test (1000 images)",
                "source": f"https://huggingface.co/datasets/{repo}", "revision": rev,
                "annotation_kind": "five English captions per image",
                "images": [{"file": r["file_name"], "url": base + r["file_name"]} for r in rows],
                "queries": queries}
    # Persist the frozen source list before downloading so a restart uses the same revision.
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    def download(entry):
        name = entry["file"]
        if Path(name).name != name or not name.endswith(".jpg"):
            raise ValueError(f"unsafe image name: {name!r}")
        path = directory / "images" / name
        fetch(entry["url"], path)
        with Image.open(path) as image:
            image.verify()
        with path.open("rb") as inp:
            digest = hashlib.file_digest(inp, "sha256").hexdigest()
        if entry.get("sha256") and digest != entry["sha256"]:
            raise ValueError(f"image checksum changed: {name}")
        entry.update(sha256=digest, bytes=path.stat().st_size)
        return entry
    with ThreadPoolExecutor(max_workers=8) as pool:
        for count, _ in enumerate(pool.map(download, data["images"]), 1):
            if count % 100 == 0:
                print(f"{name}: {count}/{len(data['images'])} images", flush=True)
    assert len(data["images"]) == 1000
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"{name}: ready, {len(data['images'])} images, {len(data['queries'])} queries", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--datasets", nargs="+", choices=("coco-cn", "flickr8k"),
                        default=["coco-cn", "flickr8k"])
    args = parser.parse_args()
    root = resolve_data_root(args.data_root) / "benchmarks"
    for name in args.datasets:
        prepare(root, name)


if __name__ == "__main__":
    main()
