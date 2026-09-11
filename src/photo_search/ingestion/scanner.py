"""Minimal deterministic scan: enumerate, hash, commit positive facts, reconcile once.

P1-01 deliberately keeps this loop small: it is strong enough to prove the frozen
identity, transaction and lease rules, and it is not the P1-02 production scanner (no
stability window, no file-id move acceleration, no directory cursor). Every file I/O
happens outside SQLite write transactions and never writes to the user's originals.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from photo_search.catalog import store
from photo_search.catalog.errors import (
    ACCESS_DENIED,
    DISK_SPACE_INSUFFICIENT,
    FILE_READ_ERROR,
    PATH_KEY_COLLISION,
    SOURCE_NOT_FOUND,
    SOURCE_OFFLINE,
    CatalogError,
)
from photo_search.catalog.paths import path_key
from photo_search.jobs import queue as jobs

HASH_CHUNK_BYTES = 1024 * 1024
HEARTBEAT_EVERY_FILES = 25
MIN_SCAN_HEADROOM_BYTES = 64 * 1024 * 1024
FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class ScanInterrupted(Exception):
    """Fault-injection signal: the process "died" mid-scan, leaving the lease in place."""


@dataclass(frozen=True)
class FileObservation:
    relative_path: str
    absolute_path: Path
    size: int
    mtime_ns: int
    path_key: str


@dataclass(frozen=True)
class ScanOutcome:
    scan_id: int
    source_id: int
    job_id: int
    state: str
    files_seen: int
    locations_missing: int
    locations_pending_content: int
    counts: dict[str, Any]
    reused_scan_request: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "scan_id": self.scan_id,
            "source_id": self.source_id,
            "job_id": self.job_id,
            "state": self.state,
            "files_seen": self.files_seen,
            "locations_missing": self.locations_missing,
            "locations_pending_content": self.locations_pending_content,
            "counts": self.counts,
            "reused_scan_request": self.reused_scan_request,
        }


def _is_reparse_point(entry: os.DirEntry[str]) -> bool:
    """True for junctions, symlinks and other reparse points.

    On Windows a directory junction reports ``is_dir() == True`` and ``is_symlink() ==
    False``; only the reparse-point file attribute identifies it, and the frozen contract
    forbids following it by default.
    """
    if entry.is_symlink():
        return True
    try:
        attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        # Let the normal per-entry handling report the real access problem.
        return False
    return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def _wrap_os_error(error: OSError, *, target: Path, directory: bool) -> CatalogError:
    if isinstance(error, PermissionError):
        return CatalogError(
            ACCESS_DENIED,
            f"access denied while reading {target}",
            details={"path": str(target), "directory": directory},
        )
    return CatalogError(
        FILE_READ_ERROR,
        f"cannot read {target}: {error}",
        details={"path": str(target), "directory": directory},
    )


def observe_file(relative_path: str, absolute_path: Path) -> FileObservation:
    try:
        info = os.stat(absolute_path)
    except OSError as error:
        raise _wrap_os_error(error, target=absolute_path, directory=False) from error
    return FileObservation(
        relative_path=relative_path,
        absolute_path=absolute_path,
        size=int(info.st_size),
        mtime_ns=int(info.st_mtime_ns),
        path_key=path_key(relative_path),
    )


def collect_observations(root: Path) -> list[FileObservation]:
    """Walk one source tree without ever following junctions or symbolic links."""
    observations: list[FileObservation] = []
    stack: list[tuple[str, Path]] = [("", root)]
    while stack:
        relative_dir, absolute_dir = stack.pop()
        try:
            with os.scandir(absolute_dir) as iterator:
                entries = sorted(iterator, key=lambda item: (item.name.casefold(), item.name))
        except OSError as error:
            raise _wrap_os_error(error, target=absolute_dir, directory=True) from error
        subdirectories: list[tuple[str, Path]] = []
        for entry in entries:
            try:
                if _is_reparse_point(entry):
                    continue
                relative = f"{relative_dir}\\{entry.name}" if relative_dir else entry.name
                if entry.is_dir(follow_symlinks=False):
                    subdirectories.append((relative, Path(entry.path)))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                observations.append(observe_file(relative, Path(entry.path)))
            except CatalogError:
                raise
            except OSError as error:
                raise _wrap_os_error(error, target=Path(entry.path), directory=False) from error
        stack.extend(subdirectories)
    observations.sort(key=lambda item: item.path_key)
    return observations


def check_path_key_collisions(observations: Sequence[FileObservation]) -> None:
    """Two different path spellings must never collapse into one location key."""
    seen: dict[str, str] = {}
    for observation in observations:
        existing = seen.get(observation.path_key)
        if existing is not None and existing != observation.relative_path:
            raise CatalogError(
                PATH_KEY_COLLISION,
                "two different paths in this source produce the same comparison key",
                details={
                    "path_key": observation.path_key,
                    "first_path": existing,
                    "second_path": observation.relative_path,
                },
            )
        seen[observation.path_key] = observation.relative_path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(HASH_CHUNK_BYTES), b""):
                digest.update(chunk)
    except OSError as error:
        raise _wrap_os_error(error, target=path, directory=False) from error
    return digest.hexdigest()


def require_free_space(disk_usage: Callable[[str | Path], Any], target: Path) -> int:
    usage = disk_usage(target if target.exists() else target.parent)
    free = int(getattr(usage, "free"))
    if free < MIN_SCAN_HEADROOM_BYTES:
        raise CatalogError(
            DISK_SPACE_INSUFFICIENT,
            f"catalog scan needs {MIN_SCAN_HEADROOM_BYTES} bytes free but only {free} are available",
            details={"required_bytes": MIN_SCAN_HEADROOM_BYTES, "free_bytes": free, "path": str(target)},
        )
    return free


def execute_scan(
    service: Any,
    source_id: int,
    *,
    worker_id: str | None = None,
    lease_seconds: int | None = None,
    interrupt_after: int | None = None,
    observations: Sequence[FileObservation] | None = None,
) -> ScanOutcome:
    """Run one logical scan request to a terminal state, or raise with it persisted."""
    catalog = service.catalog
    requested = service.start_scan(source_id)
    scan_id = int(requested["scan_id"])
    job_id = int(requested["job_id"])
    worker = worker_id or f"catalog-scan:{os.getpid()}"
    lease = int(lease_seconds if lease_seconds is not None else catalog.lease_seconds)

    with catalog.write() as connection:
        job = jobs.get_job(connection, job_id)
        if job.state == jobs.STATE_BLOCKED:
            jobs.resume_blocked(connection, job_id=job_id, now_ms=catalog.now_ms())
            store.set_scan_state(connection, scan_id=scan_id, state="QUEUED", now_ms=catalog.now_ms())

    with catalog.write() as connection:
        job = jobs.claim_job(
            connection,
            job_id=job_id,
            worker_id=worker,
            now_ms=catalog.now_ms(),
            lease_seconds=lease,
        )
        if store.get_scan_row(connection, scan_id)["state"] != "RUNNING":
            store.set_scan_state(connection, scan_id=scan_id, state="RUNNING", now_ms=catalog.now_ms())

    source = catalog.read_one(
        "SELECT source_id, root_path, status FROM source_roots WHERE source_id = ?", (source_id,)
    )
    if source is None:
        raise CatalogError(
            SOURCE_NOT_FOUND, f"source {source_id} does not exist", details={"source_id": source_id}
        )
    root = Path(str(source["root_path"]))

    if not root.is_dir():
        raise _block(
            catalog,
            job=job,
            scan_id=scan_id,
            source_id=source_id,
            error=CatalogError(
                SOURCE_OFFLINE,
                f"source root is not reachable: {root}",
                details={"source_id": source_id, "root_path": str(root)},
            ),
            source_status="OFFLINE",
        )

    try:
        require_free_space(service.disk_usage, service.data_root)
        listing = (
            list(observations) if observations is not None else collect_observations(root)
        )
        check_path_key_collisions(listing)
    except CatalogError as error:
        if error.code == PATH_KEY_COLLISION:
            raise _fail(
                catalog, job=job, scan_id=scan_id, source_id=source_id, error=error
            ) from error
        if error.code == DISK_SPACE_INSUFFICIENT:
            raise _fail(
                catalog, job=job, scan_id=scan_id, source_id=source_id, error=error
            ) from error
        status = "OFFLINE" if error.code == SOURCE_OFFLINE else (
            "ERROR" if error.code == ACCESS_DENIED else "ONLINE"
        )
        raise _block(
            catalog, job=job, scan_id=scan_id, source_id=source_id, error=error, source_status=status
        ) from error

    processed = 0
    pending_content = 0
    for observation in listing:
        if interrupt_after is not None and processed >= interrupt_after:
            raise ScanInterrupted(
                f"scan {scan_id} interrupted after {processed} of {len(listing)} files"
            )
        with catalog.write() as connection:
            location_id, _ = store.observe_location(
                connection,
                source_id=source_id,
                scan_id=scan_id,
                relative_path=observation.relative_path,
                path_key=observation.path_key,
                size=observation.size,
                mtime_ns=observation.mtime_ns,
            )
            if processed and processed % HEARTBEAT_EVERY_FILES == 0:
                jobs.heartbeat(
                    connection,
                    job_id=job_id,
                    lease_token=job.lease_token or "",
                    worker_id=worker,
                    now_ms=catalog.now_ms(),
                    lease_seconds=lease,
                )
        digest = sha256_file(observation.absolute_path)
        with catalog.write() as connection:
            bound = store.bind_location_content(
                connection,
                location_id=location_id,
                observed_size=observation.size,
                observed_mtime_ns=observation.mtime_ns,
                sha256=digest,
                byte_size=observation.size,
                now_ms=catalog.now_ms(),
            )
        if not bound:
            # The input changed between hashing and commit: keep the positive observation
            # and leave the location unbound instead of attaching a stale hash (ADR-0002 §5.4).
            pending_content += 1
        processed += 1

    try:
        require_free_space(service.disk_usage, service.data_root)
    except CatalogError as error:
        raise _fail(catalog, job=job, scan_id=scan_id, source_id=source_id, error=error) from error

    now_ms = catalog.now_ms()
    with catalog.write() as connection:
        jobs.validate_submission(
            connection,
            job_id=job_id,
            lease_token=job.lease_token or "",
            now_ms=now_ms,
            input_revision=job.input_revision,
            processor_id=job.processor_id,
        )
        missing = store.reconcile_missing(connection, source_id=source_id, scan_id=scan_id)
        store.set_scan_state(connection, scan_id=scan_id, state="SUCCEEDED", now_ms=now_ms)
        store.set_last_successful_scan(
            connection, source_id=source_id, scan_id=scan_id, status="ONLINE"
        )
        jobs.finish(
            connection,
            job_id=job_id,
            lease_token=job.lease_token or "",
            state=jobs.STATE_SUCCEEDED,
            input_revision=job.input_revision,
            processor_id=job.processor_id,
            now_ms=now_ms,
            progress=1.0,
        )
    return ScanOutcome(
        scan_id=scan_id,
        source_id=source_id,
        job_id=job_id,
        state="SUCCEEDED",
        files_seen=processed,
        locations_missing=missing,
        locations_pending_content=pending_content,
        counts=service.counts(source_id=source_id),
        reused_scan_request=bool(requested["reused"]),
    )


def _block(
    catalog: Any,
    *,
    job: jobs.Job,
    scan_id: int,
    source_id: int,
    error: CatalogError,
    source_status: str,
) -> CatalogError:
    """Persist a BLOCKED scan/job pair (no negative facts, no finished_at)."""
    now_ms = catalog.now_ms()
    with catalog.write() as connection:
        store.set_source_status(connection, source_id=source_id, status=source_status)
        store.set_scan_state(
            connection, scan_id=scan_id, state="BLOCKED", now_ms=now_ms, error_text=error.error_json
        )
        jobs.finish(
            connection,
            job_id=job.job_id,
            lease_token=job.lease_token or "",
            state=jobs.STATE_BLOCKED,
            input_revision=job.input_revision,
            processor_id=job.processor_id,
            now_ms=now_ms,
            error=error,
        )
    return error


def _fail(
    catalog: Any,
    *,
    job: jobs.Job,
    scan_id: int,
    source_id: int,
    error: CatalogError,
) -> CatalogError:
    """Persist a FAILED scan/job pair; existing identities and annotations stay untouched."""
    now_ms = catalog.now_ms()
    with catalog.write() as connection:
        store.set_scan_state(
            connection, scan_id=scan_id, state="FAILED", now_ms=now_ms, error_text=error.error_json
        )
        jobs.finish(
            connection,
            job_id=job.job_id,
            lease_token=job.lease_token or "",
            state=jobs.STATE_FAILED,
            input_revision=job.input_revision,
            processor_id=job.processor_id,
            now_ms=now_ms,
            error=error,
        )
    return error


def scan_job_state(service: Any, *, scan_id: int) -> Mapping[str, Any]:
    """Diagnostic helper for tests/reports: the job row bound to one scan_run."""
    job_id = service.catalog.read_one(
        "SELECT job_id FROM jobs WHERE subject_type = ? AND subject_id = ?",
        (store.SCAN_SUBJECT_TYPE, str(scan_id)),
    )
    if job_id is None:
        return {}
    return jobs.get_job(service.catalog.connection, int(job_id["job_id"])).as_dict()
