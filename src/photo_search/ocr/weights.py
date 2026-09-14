"""Resolution and verification of the pinned PP-OCRv4 files (P0-04 manifest).

The OCR processor may only ever open the four files recorded in
:mod:`photo_search.ocr.contract`. Each of them is checked for size and SHA-256 before any
ONNX session is created, and a missing or different file is a structured
``OCR_WEIGHTS_MISSING`` / ``OCR_WEIGHTS_HASH_MISMATCH`` failure - the wheel that ships its
own copy of the same three graphs is never used as a fallback, because a build that claims
"PP-OCRv4 mobile, config ``bf94a1da``" must be able to prove it.

Nothing here imports Pillow, numpy or ONNX Runtime, so a diagnostics or API process can ask
"are the pinned files present?" without paying for a runtime.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import (
    OCR_WEIGHTS_HASH_MISMATCH,
    OCR_WEIGHTS_MISSING,
    CatalogError,
)
from photo_search.config import resolve_data_root
from photo_search.ocr import contract

#: Read size for the verification hash: the pinned graphs are 0.5-11 MB, so one megabyte
#: keeps the whole check inside a few milliseconds of I/O per file.
HASH_CHUNK_BYTES = 1024 * 1024


def model_root(data_root: str | Path | None = None) -> Path:
    """``<data_root>/models`` - the P0-04 model root, outside the install directory."""
    return resolve_data_root(data_root) / contract.MODELS_DIRNAME


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            chunk = stream.read(HASH_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class PinnedFile:
    role: str
    relative_path: str
    expected_bytes: int
    expected_sha256: str
    absolute_path: Path

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "path": self.relative_path,
            "bytes": self.expected_bytes,
            "sha256": self.expected_sha256,
        }


@dataclass(frozen=True)
class OcrWeights:
    """The four pinned files, resolved under one model root (not yet verified)."""

    root: Path
    files: tuple[PinnedFile, ...]

    @property
    def fingerprint(self) -> str:
        return contract.MODEL_FINGERPRINT

    @property
    def config_hash(self) -> str:
        return contract.CONFIG_HASH

    def path(self, role: str) -> Path:
        for entry in self.files:
            if entry.role == str(role):
                return entry.absolute_path
        raise KeyError(role)

    @property
    def detection_path(self) -> Path:
        return self.path("detection")

    @property
    def classification_path(self) -> Path:
        return self.path("classification")

    @property
    def recognition_path(self) -> Path:
        return self.path("recognition")

    @property
    def config_path(self) -> Path:
        return self.path("config")

    def __iter__(self) -> Iterator[PinnedFile]:
        return iter(self.files)


def resolve(data_root: str | Path | None = None) -> OcrWeights:
    """The pinned files this data root should carry; nothing is opened or hashed."""
    root = model_root(data_root)
    files = tuple(
        PinnedFile(
            role=str(entry["role"]),
            relative_path=str(entry["path"]),
            expected_bytes=int(entry["bytes"]),
            expected_sha256=str(entry["sha256"]),
            absolute_path=root / str(entry["path"]),
        )
        for entry in contract.PINNED_WEIGHTS
    )
    return OcrWeights(root=root, files=files)


def inspect(weights: OcrWeights) -> list[dict[str, Any]]:
    """Read-only presence/size facts of every pinned file (no hashing, no absolute path)."""
    report: list[dict[str, Any]] = []
    for entry in weights.files:
        present = entry.absolute_path.is_file()
        size = int(entry.absolute_path.stat().st_size) if present else None
        report.append(
            {
                **entry.as_dict(),
                "present": present,
                "actual_bytes": size,
                "size_matches": bool(present and size == entry.expected_bytes),
            }
        )
    return report


def weights_present(data_root: str | Path | None = None) -> bool:
    """Cheap "are all four pinned files there with the expected size?" predicate."""
    return all(item["size_matches"] for item in inspect(resolve(data_root)))


def verify(weights: OcrWeights) -> OcrWeights:
    """Verify size and SHA-256 of every pinned file, or refuse with a structured error."""
    for entry in weights.files:
        try:
            info = entry.absolute_path.stat()
        except FileNotFoundError as error:
            raise CatalogError(
                OCR_WEIGHTS_MISSING,
                "the pinned OCR weight file is not installed in the model root",
                details={
                    "role": entry.role,
                    "relative_path": entry.relative_path,
                    "expected_bytes": entry.expected_bytes,
                },
            ) from error
        except OSError as error:
            raise CatalogError(
                OCR_WEIGHTS_MISSING,
                "the pinned OCR weight file cannot be inspected",
                details={
                    "role": entry.role,
                    "relative_path": entry.relative_path,
                    "reason": type(error).__name__,
                },
            ) from error
        if int(info.st_size) != entry.expected_bytes:
            raise CatalogError(
                OCR_WEIGHTS_HASH_MISMATCH,
                "a pinned OCR file has a different byte size than the P0-04 manifest",
                details={
                    "role": entry.role,
                    "relative_path": entry.relative_path,
                    "expected_bytes": entry.expected_bytes,
                    "actual_bytes": int(info.st_size),
                },
            )
        digest = sha256_file(entry.absolute_path)
        if digest != entry.expected_sha256:
            raise CatalogError(
                OCR_WEIGHTS_HASH_MISMATCH,
                "a pinned OCR file does not match the P0-04 sha256",
                details={
                    "role": entry.role,
                    "relative_path": entry.relative_path,
                    "expected_sha256": entry.expected_sha256,
                    "actual_sha256": digest,
                },
            )
    return weights


def resolve_and_verify(data_root: str | Path | None = None) -> OcrWeights:
    return verify(resolve(data_root))
