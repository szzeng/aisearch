"""Install the pinned embedding/OCR files under application data, never source code."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from photo_search.config import resolve_data_root
from photo_search.face import contract as face_contract
from photo_search.models.embedding.manifest import DEFAULT_MANIFEST, MODEL_SUBDIR
from photo_search.ocr import contract


def matches(path: Path, entry: dict) -> bool:
    if not path.is_file() or path.stat().st_size != entry["bytes"]:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == entry["sha256"]


def install(path: Path, entry: dict, url: str | None = None, source: Path | None = None) -> None:
    if matches(path, entry):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".download")
    try:
        if source is not None:
            shutil.copyfile(source, temporary)
        else:
            with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output, 1024 * 1024)
        if not matches(temporary, entry):
            raise ValueError(f"download checksum/size mismatch: {path.name}")
        temporary.replace(path)
        print(f"Installed {path.name}", flush=True)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--models", nargs="+", choices=("embedding", "ocr", "face"), default=["embedding", "ocr", "face"])
    args = parser.parse_args()
    data = resolve_data_root(args.data_root).resolve()
    repository = Path(__file__).resolve().parents[2]
    if data == repository or repository in data.parents:
        parser.error("model data must be outside the source/install directory")
    if "embedding" in args.models:
        manifest = json.loads(DEFAULT_MANIFEST.read_text())
        model = manifest["model"]
        base = f"https://huggingface.co/{model['id']}/resolve/{model['resolved_revision']}/"
        for entry in manifest["files"]:
            install(data.joinpath(*MODEL_SUBDIR) / entry["path"], entry, base + entry["path"])
    if "ocr" in args.models:
        config = Path(contract.__file__).with_name("rapidocr.yaml")
        for entry in contract.PINNED_WEIGHTS:
            install(data / "models" / entry["path"], entry, entry.get("url"),
                    config if entry["role"] == "config" else None)
    if "face" in args.models:
        entries = face_contract.PINNED_WEIGHTS
        if all(matches(data / "models" / entry["path"], entry) for entry in entries):
            return
        temporary_root = data / "tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as raw:
            archive = Path(raw) / "buffalo_l.zip"
            url = "https://github.com/deepinsight/insightface/releases/download/model-zoo/buffalo_l.zip"
            with urllib.request.urlopen(url, timeout=60) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output, 1024 * 1024)
            with zipfile.ZipFile(archive) as bundle:
                for entry in entries:
                    name = Path(entry["path"]).name
                    member = next(m for m in bundle.infolist() if Path(m.filename).name == name)
                    if member.file_size != entry["bytes"]:
                        raise ValueError(f"unexpected model size: {name}")
                    source = Path(raw) / name
                    with bundle.open(member) as inp, source.open("wb") as output:
                        shutil.copyfileobj(inp, output)
                    install(data / "models" / entry["path"], entry, source=source)


if __name__ == "__main__":
    main()
