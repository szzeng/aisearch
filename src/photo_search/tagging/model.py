"""Strict local RAM++ package identity, independent of the inference runtime."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import CatalogError
from photo_search.config import resolve_data_root

SCHEMA = "photo-search-ram-tagger-v1"
CLASS_COUNT = 4585
PACKAGE_NAME = "ram-plus-v1"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_MODEL_BYTES = 768 * 1024 * 1024
PREPROCESS = {
    "version": "ram-rgb-exif-v1",
    "input": "orientation-normalized-rgb",
    "size": [384, 384],
    "resize": "PIL-bilinear-square",
    "mean": [0.485, 0.456, 0.406],
    "std": [0.229, 0.224, 0.225],
    "max_pixels": 80_000_000,
}
OUTPUT = {"count": CLASS_COUNT, "type": "sigmoid_scores", "selection": "score>threshold"}
RUNTIME = {"backend": "torchscript", "torch": "2.8.0+cu128", "device": "cuda:0",
           "precision": "float16", "batch": 1, "optimized_execution": False}
_SHA = re.compile(r"[0-9a-f]{64}")


def invalid(message: str) -> CatalogError:
    return CatalogError("TAGGER_MODEL_INVALID", message)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def content_id(manifest: dict[str, Any]) -> str:
    payload = {key: value for key, value in manifest.items() if key != "model_id"}
    return PACKAGE_NAME + ":" + hashlib.sha256(canonical_bytes(payload)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _unlinked(path: Path) -> None:
    if any(part.is_symlink() or part.is_junction() for part in (path, *path.parents)):
        raise invalid("linked tagger package paths are refused")


@dataclass(frozen=True)
class TagClass:
    index: int
    english: str
    chinese: str
    threshold: float


@dataclass(frozen=True)
class TaggerModel:
    model_id: str
    classes: tuple[TagClass, ...]
    manifest_sha256: str
    model_sha256: str
    model_bytes: int
    directory: Path

    @property
    def model_path(self) -> Path:
        return self.directory / "model.ts"

    def verify(self) -> None:
        """Hash once before model loading, not on each image or search request."""
        current = read_model_directory(self.directory)
        if current != self or file_sha256(self.model_path) != self.model_sha256:
            raise invalid("tagger package content changed or failed its checksum")


def _parse(raw: bytes, directory: Path) -> TaggerModel:
    try:
        data = json.loads(raw)
        if (not isinstance(data, dict) or set(data) != {
                "schema", "model_id", "model", "classes", "preprocess", "output",
                "runtime", "source"} or data["schema"] != SCHEMA
                or data["preprocess"] != PREPROCESS or data["output"] != OUTPUT
                or data["runtime"] != RUNTIME or data["model_id"] != content_id(data)):
            raise ValueError("unsupported tagger manifest identity or inference contract")
        model = data["model"]
        if (set(model) != {"file", "bytes", "sha256"} or model["file"] != "model.ts"
                or type(model["bytes"]) is not int or not 1 <= model["bytes"] <= MAX_MODEL_BYTES
                or not isinstance(model["sha256"], str) or not _SHA.fullmatch(model["sha256"])):
            raise ValueError("invalid bounded model file")
        source = data["source"]
        if (not isinstance(source, dict) or set(source) != {
                "repository", "revision", "checkpoint_sha256", "export_lock_sha256"}
                or source["repository"] != "https://github.com/xinyu1205/recognize-anything"
                or source["revision"] != "7cb804a8609e9f4b1a50b7f31436d2df40bb9481"
                or source["checkpoint_sha256"] !=
                "497c178836ba66698ca226c7895317e6e800034be986452dbd2593298d50e87d"
                or not isinstance(source["export_lock_sha256"], str)
                or not _SHA.fullmatch(source["export_lock_sha256"])):
            raise ValueError("tagger source is not the qualified RAM++ revision")
        rows = data["classes"]
        if not isinstance(rows, list) or len(rows) != CLASS_COUNT:
            raise ValueError("tagger vocabulary must contain exactly 4585 classes")
        classes = []
        for index, row in enumerate(rows):
            if (not isinstance(row, dict) or set(row) != {
                    "index", "english", "chinese", "threshold"}
                    or type(row["index"]) is not int or row["index"] != index
                    or any(not isinstance(row[key], str) or not row[key].strip()
                           or len(row[key]) > 128 for key in ("english", "chinese"))
                    or type(row["threshold"]) not in (int, float)
                    or not math.isfinite(row["threshold"])
                    or not 0 <= row["threshold"] <= 1):
                raise ValueError("invalid tagger class, threshold or vocabulary order")
            classes.append(TagClass(index, row["english"], row["chinese"],
                                    float(row["threshold"])))
        return TaggerModel(data["model_id"], tuple(classes), hashlib.sha256(raw).hexdigest(),
                           model["sha256"], model["bytes"], directory)
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError) as error:
        raise invalid("invalid tagger manifest: " + str(error)) from error


def read_model_directory(directory: Path) -> TaggerModel:
    """Read bounded metadata; weights are fully checked only when loading inference."""
    directory = Path(directory).absolute()
    manifest_path, graph_path = directory / "manifest.json", directory / "model.ts"
    for path in (directory, manifest_path, graph_path):
        _unlinked(path)
    try:
        with manifest_path.open("rb") as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise invalid("tagger manifest exceeds its byte limit")
        result = _parse(raw, directory)
        if not graph_path.is_file() or graph_path.stat().st_size != result.model_bytes:
            raise invalid("tagger graph is missing or has an unexpected size")
        return result
    except FileNotFoundError as error:
        raise CatalogError("TAGGER_MODEL_MISSING", "local RAM++ package is not installed") from error
    except OSError as error:
        raise invalid("cannot read local tagger package") from error


def read_model(data_root: str | Path | None = None) -> TaggerModel:
    return read_model_directory(resolve_data_root(data_root) / "models" / "taggers" / PACKAGE_NAME)
