"""Sharded, rebuildable artifact cache with atomic publication (architecture §5.4, §11.1).

Layout (two shard levels under one processing version, so an older version is never
reused or mixed with a newer one)::

    <data_root>/thumbnails/<processor_id>/<ab>/<cd>/<sha256>.jpg
    <data_root>/previews/<processor_id>/<ab>/<cd>/<sha256>.jpg
    <data_root>/cache/media/<processor_id>/<ab>/<cd>/<sha256>.json   (failure reason)

The artifact file *is* the ready marker: it only ever appears at its final name through an
atomic replace of a fully written, flushed temporary file in the same shard directory. A
half-written file therefore cannot be mistaken for a usable thumbnail, and deleting the
whole cache directory loses nothing that cannot be regenerated.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import ARTIFACT_WRITE_FAILED, CatalogError
from photo_search.config import (
    media_cache_dir,
    previews_dir,
    resolve_data_root,
    thumbnails_dir,
)
from photo_search.media.contract import (
    FACE_CROP_PROCESSOR_ID,
    FAILURE_SUFFIX,
    JPEG_SUFFIX,
    PREVIEW,
    TEMP_SUFFIX,
    THUMBNAIL,
    MediaSpec,
)

SHA256_LENGTH = 64
_HEX_DIGITS = frozenset("0123456789abcdef")
FAILURE_RECORD_SCHEMA = 1
#: Rebuildable face-avatar crops live beside the failure records, under ``cache/``: they are
#: derived from the published preview and can be deleted at any time without losing a fact.
FACE_CROPS_DIRNAME = "face-crops"


def require_sha256(value: str) -> str:
    """Validate the frozen content identity before it is used as a file name."""
    text = str(value)
    if len(text) != SHA256_LENGTH or not set(text) <= _HEX_DIGITS:
        raise ValueError(f"not a 64-character lowercase sha256: {value!r}")
    return text


@dataclass(frozen=True)
class PublishResult:
    path: Path
    bytes_written: int
    replaced_existing: bool


class MediaCache:
    """Path resolution, atomic publication and rebuildable failure records."""

    def __init__(
        self,
        *,
        thumbnails_root: Path,
        previews_root: Path,
        failures_root: Path,
        face_crops_root: Path | None = None,
    ) -> None:
        self.thumbnails_root = Path(thumbnails_root)
        self.previews_root = Path(previews_root)
        self.failures_root = Path(failures_root)
        self.face_crops_root = (
            Path(face_crops_root)
            if face_crops_root is not None
            else Path(failures_root).parent / FACE_CROPS_DIRNAME
        )

    @classmethod
    def open(cls, data_root: str | Path | None = None) -> MediaCache:
        root = resolve_data_root(data_root)
        return cls(
            thumbnails_root=thumbnails_dir(root),
            previews_root=previews_dir(root),
            failures_root=media_cache_dir(root),
            face_crops_root=media_cache_dir(root).parent / FACE_CROPS_DIRNAME,
        )

    # ------------------------------------------------------------------- locations
    def artifact_root(self, spec: MediaSpec) -> Path:
        if spec.kind == THUMBNAIL:
            return self.thumbnails_root
        if spec.kind == PREVIEW:
            return self.previews_root
        raise ValueError(f"unknown media kind: {spec.kind!r}")

    def shard_dir(self, *, spec: MediaSpec, sha256: str) -> Path:
        digest = require_sha256(sha256)
        return self.artifact_root(spec) / spec.processor_id / digest[:2] / digest[2:4]

    def failure_shard_dir(self, *, spec: MediaSpec, sha256: str) -> Path:
        digest = require_sha256(sha256)
        return self.failures_root / spec.processor_id / digest[:2] / digest[2:4]

    def artifact_path(self, *, spec: MediaSpec, sha256: str) -> Path:
        return self.shard_dir(spec=spec, sha256=sha256) / f"{require_sha256(sha256)}{JPEG_SUFFIX}"

    def failure_path(self, *, spec: MediaSpec, sha256: str) -> Path:
        return (
            self.failure_shard_dir(spec=spec, sha256=sha256)
            / f"{require_sha256(sha256)}{FAILURE_SUFFIX}"
        )

    def face_crop_path(self, *, key: str) -> Path:
        """Published face-avatar crop for one (face, geometry, content) identity."""
        digest = require_sha256(key)
        return (
            self.face_crops_root
            / FACE_CROP_PROCESSOR_ID
            / digest[:2]
            / digest[2:4]
            / f"{digest}{JPEG_SUFFIX}"
        )

    # -------------------------------------------------------------------- artifacts
    def is_published(self, path: str | Path) -> bool:
        """True when a complete artifact exists at its final name."""
        candidate = Path(path)
        try:
            return candidate.is_file() and candidate.stat().st_size > 0
        except OSError:
            return False

    def publish(self, path: str | Path, payload: bytes) -> PublishResult:
        """Write ``payload`` next to ``path`` and atomically replace the final name."""
        target = Path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise _write_failed(target, error) from error
        replaced_existing = target.exists()
        temporary = target.with_name(
            f"{target.stem}.{os.getpid()}-{secrets.token_hex(6)}{TEMP_SUFFIX}"
        )
        try:
            with temporary.open("wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except OSError as error:
            _quiet_unlink(temporary)
            raise _write_failed(target, error) from error
        except BaseException:
            _quiet_unlink(temporary)
            raise
        try:
            size = target.stat().st_size
        except OSError as error:
            raise _write_failed(target, error) from error
        if size != len(payload):
            raise CatalogError(
                ARTIFACT_WRITE_FAILED,
                "the published artifact does not have the size that was written",
                details={"path": str(target), "written_bytes": len(payload), "size": size},
            )
        return PublishResult(path=target, bytes_written=size, replaced_existing=replaced_existing)

    # --------------------------------------------------------------------- failures
    def read_failure(self, *, spec: MediaSpec, sha256: str) -> dict[str, Any] | None:
        path = self.failure_path(spec=spec, sha256=sha256)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def write_failure(
        self, *, spec: MediaSpec, sha256: str, record: dict[str, Any]
    ) -> Path:
        path = self.failure_path(spec=spec, sha256=sha256)
        payload = json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return self.publish(path, payload).path

    def clear_failure(self, *, spec: MediaSpec, sha256: str) -> bool:
        path = self.failure_path(spec=spec, sha256=sha256)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError:
            # A stale record only costs one skipped re-decode; it never blocks a publish.
            return False
        return True

    def iter_failures(self, *, spec: MediaSpec) -> Iterator[Path]:
        root = self.failures_root / spec.processor_id
        if not root.is_dir():
            return
        for path in root.rglob(f"*{FAILURE_SUFFIX}"):
            if path.is_file():
                yield path

    # ------------------------------------------------------------------ temp files
    def iter_temp_files(self, *, spec: MediaSpec) -> Iterator[Path]:
        root = self.artifact_root(spec) / spec.processor_id
        if not root.is_dir():
            return
        for path in root.rglob(f"*{TEMP_SUFFIX}"):
            if path.is_file():
                yield path

    def prune_temp_files(
        self, *, spec: MediaSpec, min_age_ms: int, now_ms: int | None = None
    ) -> list[Path]:
        """Remove leftover temporary files from interrupted runs; artifacts stay intact."""
        now = int(time.time_ns() // 1_000_000) if now_ms is None else int(now_ms)
        removed: list[Path] = []
        for path in list(self.iter_temp_files(spec=spec)):
            try:
                if now - int(path.stat().st_mtime * 1000) < int(min_age_ms):
                    continue
                path.unlink()
            except OSError:
                continue
            removed.append(path)
        return removed

    # -------------------------------------------------------------------- inventory
    def version_dirs(self, *, kind: str) -> list[str]:
        """Processing versions present on disk, so a stale version stays visible."""
        root = self.thumbnails_root if kind == THUMBNAIL else self.previews_root
        if not root.is_dir():
            return []
        return sorted(entry.name for entry in root.iterdir() if entry.is_dir())

    def cache_inventory(self, *, spec: MediaSpec) -> dict[str, int]:
        """Count and size of published artifacts and leftover temp files for one version."""
        root = self.artifact_root(spec) / spec.processor_id
        published = 0
        published_bytes = 0
        temporary = 0
        if root.is_dir():
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if path.name.endswith(TEMP_SUFFIX):
                    temporary += 1
                    continue
                published += 1
                published_bytes += size
        return {
            "published_files": published,
            "published_bytes": published_bytes,
            "temp_files": temporary,
        }


def _write_failed(target: Path, error: OSError) -> CatalogError:
    return CatalogError(
        ARTIFACT_WRITE_FAILED,
        f"cannot publish the media artifact {target}: {error}",
        details={"path": str(target), "reason": type(error).__name__},
    )


def _quiet_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
