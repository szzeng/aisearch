"""Derived-media generation over catalogued assets (P1-03).

The batch is content-addressed and idempotent: a target is an *asset* (a full SHA-256
content identity), the artifact lives at ``<processor_id>/<sha256>`` and an existing
complete artifact is never recomputed. Nothing here writes to the catalog database: the
processing version lives in the path, so a newer version cannot reuse an older file, and
an interrupted run leaves only complete artifacts plus ignorable ``*.tmp`` leftovers.

The original file is only ever opened for reading, and its ``size``/``mtime_ns`` are
checked against the catalog observation before and after the decode, so a thumbnail can
never be published under a content identity the bytes no longer have.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import (
    ACCESS_DENIED,
    ASSET_NOT_FOUND,
    DECODE_FAILED,
    DETERMINISTIC_MEDIA_FAILURES,
    DISK_SPACE_INSUFFICIENT,
    FILE_READ_ERROR,
    FORMAT_UNSUPPORTED,
    INPUT_UNSTABLE,
    SOURCE_FILE_MISSING,
    CatalogError,
)
from photo_search.media.cache import MediaCache
from photo_search.media.contract import MediaSpec
from photo_search.media.decode import load_normalized, render_jpeg

#: Free space a media batch keeps in reserve before it publishes another artifact
#: (architecture §12.4 item 10: stop and report, never delete data to recover space).
MIN_MEDIA_HEADROOM_BYTES = 64 * 1024 * 1024

STATE_PUBLISHED = "PUBLISHED"
STATE_GENERATED = "GENERATED"
STATE_FAILED = "FAILED"
STATE_UNSUPPORTED = "UNSUPPORTED"
STATE_SOURCE_MISSING = "SOURCE_MISSING"
STATE_INPUT_UNSTABLE = "INPUT_UNSTABLE"
STATE_CACHED_FAILURE = "CACHED_FAILURE"

#: Content facts win over read/availability problems when several locations disagree.
_ERROR_PREFERENCE = {
    DECODE_FAILED: 0,
    FORMAT_UNSUPPORTED: 0,
    FILE_READ_ERROR: 1,
    ACCESS_DENIED: 1,
    SOURCE_FILE_MISSING: 2,
    INPUT_UNSTABLE: 2,
}

_LOCATION_SQL = """
SELECT l.location_id, l.source_id, l.relative_path, l.size, l.mtime_ns, l.availability,
       s.root_path
  FROM file_locations AS l
  JOIN source_roots AS s ON s.source_id = l.source_id
 WHERE l.asset_id = ?
 ORDER BY CASE l.availability WHEN 'PRESENT' THEN 0 ELSE 1 END, l.location_id
"""


class MediaInterrupted(Exception):
    """The batch stopped early (kill, cancellation hook or Ctrl-C).

    Everything already published stays usable, no temporary file is ever promoted to a
    ready artifact, and re-running the same command finishes the remaining assets.
    """

    def __init__(self, message: str, *, summary: dict[str, Any] | None = None) -> None:
        self.summary = summary or {}
        super().__init__(message)


@dataclass(frozen=True)
class MediaTarget:
    asset_id: int
    sha256: str
    byte_size: int


@dataclass(frozen=True)
class MediaAttempt:
    """Outcome of one asset in one media kind."""

    asset_id: int
    sha256: str
    kind: str
    state: str
    code: str | None = None
    message: str | None = None
    source_format: str | None = None
    width: int | None = None
    height: int | None = None
    orientation: int | None = None
    locations_tried: int = 0
    artifact_path: str | None = None
    bytes_written: int = 0
    duration_ms: int = 0
    failure_recorded: bool = False

    @property
    def ok(self) -> bool:
        return self.state in (STATE_PUBLISHED, STATE_GENERATED)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "asset_id": self.asset_id,
            "sha256": self.sha256,
            "kind": self.kind,
            "state": self.state,
            "duration_ms": self.duration_ms,
        }
        if self.code is not None:
            payload["code"] = self.code
        if self.message is not None:
            payload["message"] = self.message
        if self.source_format is not None:
            payload["source_format"] = self.source_format
        if self.width is not None and self.height is not None:
            payload["normalized_width"] = self.width
            payload["normalized_height"] = self.height
        if self.orientation is not None:
            payload["exif_orientation"] = self.orientation
        if self.artifact_path is not None:
            payload["artifact_path"] = self.artifact_path
        if self.bytes_written:
            payload["bytes_written"] = self.bytes_written
        if self.locations_tried:
            payload["locations_tried"] = self.locations_tried
        if self.failure_recorded:
            payload["failure_recorded"] = True
        return payload


def select_targets(
    catalog: Any,
    *,
    asset_ids: Sequence[int] | None = None,
    source_id: int | None = None,
    limit: int | None = None,
) -> list[MediaTarget]:
    """Resolve the assets a batch must consider, in stable ``asset_id`` order.

    Without an explicit list, only assets with at least one ``PRESENT`` position are
    enumerated: an asset whose every position is offline is reported by ``media-status``
    rather than re-listed on every batch run. An explicit ``--asset-id`` is honoured
    whatever the availability is, so a single asset can still be requested (and answered)
    while its source is offline.
    """
    conditions: list[str] = []
    parameters: list[Any] = []
    if asset_ids:
        ids = sorted({int(value) for value in asset_ids})
        placeholders = ", ".join("?" for _ in ids)
        missing = _missing_assets(catalog, ids)
        if missing:
            raise CatalogError(
                ASSET_NOT_FOUND,
                f"asset(s) {missing} do not exist",
                details={"asset_ids": missing},
            )
        conditions.append(f"a.asset_id IN ({placeholders})")
        parameters.extend(ids)
    else:
        conditions.append(
            "EXISTS (SELECT 1 FROM file_locations AS l "
            "WHERE l.asset_id = a.asset_id AND l.availability = 'PRESENT')"
        )
    if source_id is not None:
        conditions.append(
            "EXISTS (SELECT 1 FROM file_locations AS l "
            "WHERE l.asset_id = a.asset_id AND l.source_id = ?)"
        )
        parameters.append(int(source_id))
    sql = "SELECT a.asset_id, a.sha256, a.byte_size FROM assets AS a"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY a.asset_id"
    if limit is not None:
        sql += " LIMIT ?"
        parameters.append(max(int(limit), 0))
    return [
        MediaTarget(
            asset_id=int(row["asset_id"]),
            sha256=str(row["sha256"]),
            byte_size=int(row["byte_size"]),
        )
        for row in catalog.read_all(sql, tuple(parameters))
    ]


class MediaGenerator:
    """Read the catalog, decode originals read-only and publish derived artifacts."""

    def __init__(
        self,
        catalog: Any,
        *,
        cache: MediaCache,
        data_root: str | Path,
        disk_usage: Callable[[str | Path], Any] | None = None,
        min_free_bytes: int = MIN_MEDIA_HEADROOM_BYTES,
        wall_clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.catalog = catalog
        self.cache = cache
        self.data_root = Path(data_root)
        self.disk_usage = disk_usage or shutil.disk_usage
        self.min_free_bytes = int(min_free_bytes)
        self.wall_clock_ms = wall_clock_ms or (lambda: time.time_ns() // 1_000_000)

    # -------------------------------------------------------------------- one asset
    def build(
        self, spec: MediaSpec, target: MediaTarget, *, retry_failed: bool = False
    ) -> MediaAttempt:
        started = time.perf_counter()
        artifact = self.cache.artifact_path(spec=spec, sha256=target.sha256)
        if self.cache.is_published(artifact):
            return self._attempt(
                spec, target, STATE_PUBLISHED, started, artifact_path=str(artifact)
            )

        rows = self.catalog.read_all(_LOCATION_SQL, (target.asset_id,))
        present = [row for row in rows if str(row["availability"]) == "PRESENT"]
        cached = None if retry_failed else self.cache.read_failure(spec=spec, sha256=target.sha256)
        if cached is not None and _cached_failure_applies(cached, present):
            return self._attempt(
                spec,
                target,
                STATE_CACHED_FAILURE,
                started,
                code=_optional_text(cached.get("code")),
                message=_optional_text(cached.get("message")),
                source_format=_optional_text(cached.get("format")),
            )
        if not present:
            return self._attempt(
                spec,
                target,
                STATE_SOURCE_MISSING,
                started,
                code=SOURCE_FILE_MISSING,
                message="no available location holds this content",
            )

        errors: list[CatalogError] = []
        for row in present:
            self._require_free_space()
            absolute = Path(str(row["root_path"])) / str(row["relative_path"])
            try:
                _require_unchanged(absolute, row)
                decoded = load_normalized(absolute, long_edge=spec.long_edge)
                _require_unchanged(absolute, row)
                payload = render_jpeg(
                    decoded.image, long_edge=spec.long_edge, quality=spec.jpeg_quality
                )
            except CatalogError as error:
                errors.append(error)
                continue
            except Exception as error:  # isolation: one bad input never fails the batch
                errors.append(
                    CatalogError(
                        DECODE_FAILED,
                        f"media pipeline failed for this input ({type(error).__name__}): {error}",
                        details={"path": str(absolute)},
                    )
                )
                continue
            self._require_free_space()
            published = self.cache.publish(artifact, payload)
            self.cache.clear_failure(spec=spec, sha256=target.sha256)
            return self._attempt(
                spec,
                target,
                STATE_GENERATED,
                started,
                source_format=decoded.source_format,
                width=decoded.width,
                height=decoded.height,
                orientation=decoded.orientation,
                artifact_path=str(published.path),
                bytes_written=published.bytes_written,
                locations_tried=len(errors) + 1,
            )

        error = _preferred_error(errors)
        state = _failure_state(error)
        recorded = False
        if state in (STATE_FAILED, STATE_UNSUPPORTED):
            recorded = self._record_failure(spec, target, error, present, state)
        return self._attempt(
            spec,
            target,
            state,
            started,
            code=error.code,
            message=error.message,
            source_format=_optional_text(error.details.get("format")),
            locations_tried=len(errors),
            failure_recorded=recorded,
        )

    # ------------------------------------------------------------------- whole batch
    def run(
        self,
        spec: MediaSpec,
        targets: Sequence[MediaTarget],
        *,
        retry_failed: bool = False,
        interrupt_after: int | None = None,
        cancel_check: Callable[[], bool] | None = None,
        failure_limit: int = 20,
    ) -> dict[str, Any]:
        """Process every target; per-asset failures are isolated and counted."""
        summary = self._empty_summary(spec, considered=len(targets))
        started = time.perf_counter()
        try:
            for processed, target in enumerate(targets):
                if interrupt_after is not None and processed >= int(interrupt_after):
                    raise MediaInterrupted(
                        f"{spec.kind} batch interrupted after {processed} of {len(targets)} assets",
                        summary=summary,
                    )
                if cancel_check is not None and cancel_check():
                    raise MediaInterrupted(
                        f"{spec.kind} batch cancelled after {processed} assets", summary=summary
                    )
                attempt = self.build(spec, target, retry_failed=retry_failed)
                _accumulate(summary, attempt, failure_limit=failure_limit)
        except KeyboardInterrupt as error:
            raise MediaInterrupted(
                f"{spec.kind} batch interrupted by the user", summary=summary
            ) from error
        summary["elapsed_ms"] = _elapsed_ms(started)
        summary["disk_free_bytes"] = int(
            getattr(self.disk_usage(_existing(self.data_root)), "free", 0)
        )
        return summary

    # ---------------------------------------------------------------------- helpers
    def _empty_summary(self, spec: MediaSpec, *, considered: int) -> dict[str, Any]:
        return {
            "kind": spec.kind,
            "processor_id": spec.processor_id,
            "long_edge": spec.long_edge,
            "output_format": "JPEG",
            "artifact_root": str(self.cache.artifact_root(spec)),
            "assets_considered": int(considered),
            "generated": 0,
            "already_published": 0,
            "failed": 0,
            "unsupported": 0,
            "source_missing": 0,
            "input_unstable": 0,
            "skipped_cached_failures": 0,
            "artifacts_written": 0,
            "bytes_written": 0,
            "failures": [],
            "failures_truncated": 0,
            "elapsed_ms": 0,
            "disk_free_bytes": 0,
        }

    def _attempt(
        self,
        spec: MediaSpec,
        target: MediaTarget,
        state: str,
        started: float,
        **fields: Any,
    ) -> MediaAttempt:
        return MediaAttempt(
            asset_id=target.asset_id,
            sha256=target.sha256,
            kind=spec.kind,
            state=state,
            duration_ms=_elapsed_ms(started),
            **fields,
        )

    def _record_failure(
        self,
        spec: MediaSpec,
        target: MediaTarget,
        error: CatalogError,
        present: Sequence[Any],
        state: str,
    ) -> bool:
        first = present[0] if present else None
        record: dict[str, Any] = {
            "schema": 1,
            "kind": spec.kind,
            "processor_id": spec.processor_id,
            "sha256": target.sha256,
            "byte_size": target.byte_size,
            "state": state,
            "code": error.code,
            "message": error.message[:500],
            "format": error.details.get("format"),
            "locations_tried": len(present),
            "created_at": int(self.wall_clock_ms()),
        }
        if first is not None:
            record["input_size"] = int(first["size"])
            record["input_mtime_ns"] = int(first["mtime_ns"])
            record["source_id"] = int(first["source_id"])
        # A persisted record keeps only the projection that is already in the catalog (a
        # source-relative path at most). No absolute personal path, and never any image
        # content or decoded text, is written to the diagnostic record.
        if first is not None:
            root = str(first["root_path"])
            record["message"] = str(record["message"]).replace(root, "<source_root>")[:500]
        try:
            self.cache.write_failure(spec=spec, sha256=target.sha256, record=record)
        except CatalogError:
            # A missing failure note never blocks the batch nor turns a failure into a
            # success; the run summary still reports the reason.
            return False
        return True

    def _require_free_space(self) -> int:
        target = _existing(self.data_root)
        usage = self.disk_usage(target)
        free = int(getattr(usage, "free"))
        if free < self.min_free_bytes:
            raise CatalogError(
                DISK_SPACE_INSUFFICIENT,
                f"media generation needs {self.min_free_bytes} bytes free but only "
                f"{free} are available",
                details={
                    "required_bytes": self.min_free_bytes,
                    "free_bytes": free,
                    "path": str(target),
                },
            )
        return free


def _accumulate(
    summary: dict[str, Any], attempt: MediaAttempt, *, failure_limit: int
) -> None:
    if attempt.state == STATE_PUBLISHED:
        summary["already_published"] += 1
    elif attempt.state == STATE_GENERATED:
        summary["generated"] += 1
        summary["artifacts_written"] += 1
        summary["bytes_written"] += attempt.bytes_written
    elif attempt.state == STATE_FAILED:
        summary["failed"] += 1
    elif attempt.state == STATE_UNSUPPORTED:
        summary["unsupported"] += 1
    elif attempt.state == STATE_SOURCE_MISSING:
        summary["source_missing"] += 1
    elif attempt.state == STATE_INPUT_UNSTABLE:
        summary["input_unstable"] += 1
    elif attempt.state == STATE_CACHED_FAILURE:
        summary["skipped_cached_failures"] += 1
    if attempt.state in (
        STATE_FAILED,
        STATE_UNSUPPORTED,
        STATE_SOURCE_MISSING,
        STATE_INPUT_UNSTABLE,
        STATE_CACHED_FAILURE,
    ):
        if len(summary["failures"]) < int(failure_limit):
            summary["failures"].append(attempt.as_dict())
        else:
            summary["failures_truncated"] += 1


def _missing_assets(catalog: Any, asset_ids: Iterable[int]) -> list[int]:
    ids = sorted({int(value) for value in asset_ids})
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    rows = catalog.read_all(
        f"SELECT asset_id FROM assets WHERE asset_id IN ({placeholders})", tuple(ids)
    )
    found = {int(row["asset_id"]) for row in rows}
    return [value for value in ids if value not in found]


def _require_unchanged(path: Path, row: Any) -> tuple[int, int]:
    """The position must still be exactly what the catalog recorded for this content."""
    try:
        info = path.stat()
    except PermissionError as error:
        raise CatalogError(
            ACCESS_DENIED,
            f"access denied while reading {path}",
            details={"path": str(path), "location_id": int(row["location_id"])},
        ) from error
    except FileNotFoundError as error:
        raise CatalogError(
            SOURCE_FILE_MISSING,
            f"the source file is not reachable: {path}",
            details={"path": str(path), "location_id": int(row["location_id"])},
        ) from error
    except OSError as error:
        raise CatalogError(
            FILE_READ_ERROR,
            f"cannot read {path}: {error}",
            details={"path": str(path), "location_id": int(row["location_id"])},
        ) from error
    observed = (int(info.st_size), int(info.st_mtime_ns))
    if observed != (int(row["size"]), int(row["mtime_ns"])):
        raise CatalogError(
            INPUT_UNSTABLE,
            "the source file changed after the catalog observation",
            details={
                "path": str(path),
                "location_id": int(row["location_id"]),
                "observed_size": observed[0],
                "observed_mtime_ns": observed[1],
                "catalog_size": int(row["size"]),
                "catalog_mtime_ns": int(row["mtime_ns"]),
            },
        )
    return observed


#: Public alias: the controlled file-access layer reuses this exact stat guard, so a
#: thumbnail, a preview and an original can never disagree about "still unchanged".
require_unchanged = _require_unchanged


def _failure_state(error: CatalogError) -> str:
    if error.code == FORMAT_UNSUPPORTED:
        return STATE_UNSUPPORTED
    if error.code == SOURCE_FILE_MISSING:
        return STATE_SOURCE_MISSING
    if error.code == INPUT_UNSTABLE:
        return STATE_INPUT_UNSTABLE
    return STATE_FAILED


def _preferred_error(errors: Sequence[CatalogError]) -> CatalogError:
    return min(errors, key=lambda error: _ERROR_PREFERENCE.get(error.code, 3))


def _cached_failure_applies(record: dict[str, Any], present: Sequence[Any]) -> bool:
    """Skip re-decoding only for a deterministic failure of exactly the same bytes."""
    if str(record.get("code")) not in DETERMINISTIC_MEDIA_FAILURES:
        return False
    size = record.get("input_size")
    mtime_ns = record.get("input_mtime_ns")
    if size is None or mtime_ns is None:
        return False
    return any(
        int(row["size"]) == int(size) and int(row["mtime_ns"]) == int(mtime_ns)
        for row in present
    )


def _existing(path: Path) -> Path:
    return path if path.exists() else path.parent


def _elapsed_ms(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)
