"""Shared helpers for P0-03 Chinese-CLIP preparation tools."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

MODEL_ID = "OFA-Sys/chinese-clip-vit-base-patch16"
MODEL_REVISION = "main"
CONTEXT_LENGTH = 52
IMAGE_SIZE = 224
EMBEDDING_DIMENSION = 512


def default_model_dir() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local_app_data / "PhotoSearchData" / "models" / "chinese_clip" / "vit-b-16"


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
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def l2_normalize(values: Any) -> Any:
    import numpy as np

    array = np.asarray(values, dtype=np.float32)
    norms = np.linalg.norm(array, axis=-1, keepdims=True)
    if np.any(norms == 0):
        raise ValueError("cannot L2-normalize a zero embedding")
    return array / norms
