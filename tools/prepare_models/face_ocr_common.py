"""Shared helpers for the P0-04 face and OCR weight preparation tools."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

RAPIDOCR_PACKAGE = "rapidocr-onnxruntime"
RAPIDOCR_MODEL_FILES = (
    "ch_PP-OCRv4_det_infer.onnx",
    "ch_ppocr_mobile_v2.0_cls_infer.onnx",
    "ch_PP-OCRv4_rec_infer.onnx",
)
RAPIDOCR_CONFIG_FILE = "config.yaml"
FACE_PACK_NAME = "buffalo_l"
FACE_PACK_URL = (
    "https://github.com/deepinsight/insightface/releases/download/model-zoo/buffalo_l.zip"
)
FACE_PACK_LEGACY_URL = (
    "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
)
FACE_MODEL_FILES = {
    "detection": "det_10g.onnx",
    "recognition": "w600k_r50.onnx",
}


def default_data_root() -> Path:
    """Return the development data root.

    The packaged desktop app redirects `LOCALAPPDATA` into its own LocalCache
    directory, so the profile path is preferred while still resolving to the
    same `PhotoSearchData` root that already holds the P0-03 weights.
    """
    profile = os.environ.get("USERPROFILE")
    candidate = Path(profile) / "AppData" / "Local" if profile else None
    if candidate is None or not candidate.is_dir():
        candidate = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    # The packaged desktop app may alias LocalAppData inside its LocalCache
    # directory, so the canonical path is recorded instead of the alias.
    return Path(os.path.realpath(candidate)) / "PhotoSearchData"


def default_model_root() -> Path:
    return default_data_root() / "models"


def ocr_model_dir(root: Path) -> Path:
    return root / "ocr" / "ppocr-v4-mobile"


def face_model_dir(root: Path) -> Path:
    return root / "face" / FACE_PACK_NAME


def download_cache_dir(root: Path) -> Path:
    return root.parent / "cache"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_record(path: Path, root: Path, source_url: str) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "source_url": source_url,
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
