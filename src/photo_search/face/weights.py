"""Resolution and verification of the pinned ``buffalo_l`` files (P0-04 manifest).

The face processor may only ever open the two files recorded in
:mod:`photo_search.face.contract`: ``det_10g.onnx`` and ``w600k_r50.onnx``. Both are
checked for size and SHA-256 before any ONNX session exists, and a missing or different
file is a structured ``FACE_WEIGHTS_MISSING`` / ``FACE_WEIGHTS_HASH_MISMATCH`` failure.
There is deliberately no fallback to the ``insightface`` package's own model zoo or to a
bundled default pack: a build that claims "SCRFD det_10g + ArcFace w600k_r50" has to be
able to prove which bytes it loaded.

Nothing here imports numpy, Pillow or ONNX Runtime, so an API or diagnostics process can
ask "are the pinned files present?" without paying for a runtime.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import (
    FACE_WEIGHTS_HASH_MISMATCH,
    FACE_WEIGHTS_MISSING,
    CatalogError,
)
from photo_search.config import resolve_data_root
from photo_search.face import contract

#: Read size for the verification hash: the pinned graphs are 17 MB and 174 MB, so one
#: megabyte keeps the check inside a few tens of milliseconds of I/O per file.
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
class FaceWeights:
    """The two pinned files, resolved under one model root (not yet verified)."""

    root: Path
    files: tuple[PinnedFile, ...]

    @property
    def fingerprint(self) -> str:
        return contract.MODEL_FINGERPRINT

    @property
    def config_hash(self) -> str:
        return contract.CONFIG_HASH

    @property
    def space_id(self) -> str:
        return contract.FACE_SPACE_ID

    def path(self, role: str) -> Path:
        for entry in self.files:
            if entry.role == str(role):
                return entry.absolute_path
        raise KeyError(role)

    @property
    def detection_path(self) -> Path:
        return self.path("detection")

    @property
    def recognition_path(self) -> Path:
        return self.path("recognition")

    def __iter__(self) -> Iterator[PinnedFile]:
        return iter(self.files)


def resolve(data_root: str | Path | None = None) -> FaceWeights:
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
    return FaceWeights(root=root, files=files)


def inspect(weights: FaceWeights) -> list[dict[str, Any]]:
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
    """Cheap "are both pinned files there with the expected size?" predicate."""
    return all(item["size_matches"] for item in inspect(resolve(data_root)))


def verify(weights: FaceWeights) -> FaceWeights:
    """Verify size and SHA-256 of every pinned file, or refuse with a structured error."""
    for entry in weights.files:
        try:
            info = entry.absolute_path.stat()
        except FileNotFoundError as error:
            raise CatalogError(
                FACE_WEIGHTS_MISSING,
                "the pinned face weight file is not installed in the model root",
                details={
                    "role": entry.role,
                    "relative_path": entry.relative_path,
                    "expected_bytes": entry.expected_bytes,
                },
            ) from error
        except OSError as error:
            raise CatalogError(
                FACE_WEIGHTS_MISSING,
                "the pinned face weight file cannot be inspected",
                details={
                    "role": entry.role,
                    "relative_path": entry.relative_path,
                    "reason": type(error).__name__,
                },
            ) from error
        if int(info.st_size) != entry.expected_bytes:
            raise CatalogError(
                FACE_WEIGHTS_HASH_MISMATCH,
                "a pinned face file has a different byte size than the P0-04 manifest",
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
                FACE_WEIGHTS_HASH_MISMATCH,
                "a pinned face file does not match the P0-04 sha256",
                details={
                    "role": entry.role,
                    "relative_path": entry.relative_path,
                    "expected_sha256": entry.expected_sha256,
                    "actual_sha256": digest,
                },
            )
    return weights


def resolve_and_verify(data_root: str | Path | None = None) -> FaceWeights:
    return verify(resolve(data_root))
