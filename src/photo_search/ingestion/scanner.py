"""Streaming production scan: enumerate, wait for stability, hash, reconcile once.

P1-02 turns the P1-01 minimal deterministic loop into a scanner that can run a real
library:

* :func:`iter_observations` streams the directory tree, so the file list never has to fit
  in memory (one directory's entries at a time plus the depth-first stack);
* the content pass reads the positions of this execution back from the catalog in bounded
  chunks instead of from an in-memory list;
* a position is only committed once its ``size``/``mtime_ns`` have been quiet for a
  configurable window, and the full SHA-256 has to survive a stat before and after it;
* negative facts (``MISSING`` and ``last_successful_scan``) are still written only by the
  single final success transaction of a complete scan.

The frozen ADR-0002 semantics are untouched: content identity, ``(source_id, path_key)``
uniqueness, lease/submission qualification and "only a complete successful scan may
conclude that something is missing". File I/O never happens inside a SQLite write
transaction and the user's originals are only ever read.
"""

from __future__ import annotations

import hashlib
import os
import stat
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from photo_search.catalog import store
from photo_search.catalog.errors import (
    ACCESS_DENIED,
    DISK_SPACE_INSUFFICIENT,
    FILE_READ_ERROR,
    INPUT_UNSTABLE,
    JOB_NOT_FOUND,
    LEASE_CONFLICT,
    PATH_KEY_COLLISION,
    PROCESSOR_MISMATCH,
    RETRY_EXHAUSTED,
    SOURCE_NOT_FOUND,
    SOURCE_OFFLINE,
    STALE_INPUT_REVISION,
    STALE_LEASE,
    CatalogError,
)
from photo_search.catalog.paths import path_key
from photo_search.jobs import queue as jobs

HASH_CHUNK_BYTES = 1024 * 1024
MIN_SCAN_HEADROOM_BYTES = 64 * 1024 * 1024
#: Inline re-observations allowed for one file before the scan request is re-queued.
UNSTABLE_FILE_ATTEMPTS = 3

#: P1-02 stability window: a file is only bound once ``size``/``mtime_ns`` have been
#: unchanged for this long. 1500 ms is the shipped default (P1-02 report §3).
DEFAULT_STABILITY_WINDOW_MS = 1_500
#: Upper bound on the time one execution spends waiting for one file (or for the whole
#: enumeration to settle) before the scan request is re-queued instead.
DEFAULT_STABILITY_WAIT_LIMIT_MS = 30_000
#: Positions are read back for content confirmation in bounded chunks, not as one list.
LOCATION_CHUNK_SIZE = 256
#: Progress split of the two passes; the values only have to be monotone and bounded.
ENUMERATION_PROGRESS_CEILING = 0.45
ENUMERATION_PROGRESS_SCALE_FILES = 2_000
PROCESSING_PROGRESS_CEILING = 0.99
PROGRESS_EPSILON = 0.001

#: ADR-0002 §6.5: these mean "this execution may no longer write anything". The scan and
#: job state must be left exactly as the current execution owner has it; a late submit
#: never overwrites the newer execution's results or its state.
SUBMISSION_ERROR_CODES = frozenset(
    {
        JOB_NOT_FOUND,
        LEASE_CONFLICT,
        STALE_LEASE,
        STALE_INPUT_REVISION,
        PROCESSOR_MISMATCH,
    }
)
FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

#: Indirection so tests can inject a failing directory enumerator without patching the
#: process-wide ``os`` module.
_scandir = os.scandir


class ScanInterrupted(Exception):
    """The execution stopped early: no negative fact, no SUCCEEDED, lease left in place.

    Raised by the fault-injection hook (``interrupt_after``), by an injected
    ``cancel_check`` and when the process receives ``KeyboardInterrupt`` mid-scan. The
    persistent state stays exactly as the stopped execution left it, so the same logical
    ``scan_run`` can be recovered later under a new lease (ADR-0002 §5.3).
    """


@dataclass(frozen=True)
class StabilityPolicy:
    """How long one execution waits for ``size``/``mtime_ns`` to settle (P1-02 §2)."""

    window_ms: int = DEFAULT_STABILITY_WINDOW_MS
    wait_limit_ms: int = DEFAULT_STABILITY_WAIT_LIMIT_MS

    def __post_init__(self) -> None:
        if self.window_ms < 0:
            raise ValueError("stability window must not be negative")
        if self.wait_limit_ms < 0:
            raise ValueError("stability wait limit must not be negative")


@dataclass
class _Execution:
    """The live execution identity: token, revision, processor and current lease."""

    job: jobs.Job

    def maybe_extend(
        self,
        connection: Any,
        *,
        worker: str,
        lease_seconds: int,
        now_ms: int,
    ) -> None:
        """Renew the lease once less than half of it remains (time-based, not file-based)."""
        if self.job.lease_expires_at is None:
            return
        half_lease_ms = (int(lease_seconds) * 1000) // 2
        if now_ms + half_lease_ms >= self.job.lease_expires_at:
            self.job = jobs.heartbeat(
                connection,
                job_id=self.job.job_id,
                lease_token=self.job.lease_token or "",
                worker_id=worker,
                now_ms=now_ms,
                lease_seconds=lease_seconds,
            )

    def require(self, connection: Any, *, now_ms: int) -> None:
        _require_submission(connection, self.job, now_ms=now_ms)


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
    unstable_retries: int
    counts: dict[str, Any]
    reused_scan_request: bool
    enumerated: int
    stability_waits: int
    stability_wait_ms: int
    prepare_ms: int
    enumeration_ms: int
    processing_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "scan_id": self.scan_id,
            "source_id": self.source_id,
            "job_id": self.job_id,
            "state": self.state,
            "files_seen": self.files_seen,
            "locations_missing": self.locations_missing,
            "locations_pending_content": self.locations_pending_content,
            "unstable_retries": self.unstable_retries,
            "counts": self.counts,
            "reused_scan_request": self.reused_scan_request,
            "enumerated": self.enumerated,
            "stability_waits": self.stability_waits,
            "stability_wait_ms": self.stability_wait_ms,
            "prepare_ms": self.prepare_ms,
            "enumeration_ms": self.enumeration_ms,
            "processing_ms": self.processing_ms,
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


def iter_observations(root: Path) -> Iterator[FileObservation]:
    """Stream one source tree depth-first, without ever following junctions or symlinks.

    Memory stays bounded by one directory's entries plus the stack of subdirectories that
    are still to be visited; the file list of the whole library is never materialised.
    """
    stack: list[tuple[str, Path]] = [("", root)]
    while stack:
        relative_dir, absolute_dir = stack.pop()
        try:
            with _scandir(absolute_dir) as iterator:
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
                yield observe_file(relative, Path(entry.path))
            except CatalogError:
                raise
            except OSError as error:
                raise _wrap_os_error(error, target=Path(entry.path), directory=False) from error
        stack.extend(subdirectories)


def collect_observations(root: Path) -> list[FileObservation]:
    """Materialise the streaming walk; kept for diagnostics and small injected fixtures."""
    return list(iter_observations(root))


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
            details={
                "required_bytes": MIN_SCAN_HEADROOM_BYTES,
                "free_bytes": free,
                "path": str(target),
            },
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
    stability: StabilityPolicy | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> ScanOutcome:
    """Run one logical scan request to a terminal state, or raise with it persisted."""
    catalog = service.catalog
    requested = service.start_scan(source_id)
    scan_id = int(requested["scan_id"])
    job_id = int(requested["job_id"])
    worker = worker_id or f"catalog-scan:{os.getpid()}"
    lease = int(lease_seconds if lease_seconds is not None else catalog.lease_seconds)
    policy = stability if stability is not None else service.stability_policy()

    with catalog.write() as connection:
        job = jobs.get_job(connection, job_id)
        if job.state == jobs.STATE_BLOCKED:
            jobs.resume_blocked(connection, job_id=job_id, now_ms=catalog.now_ms())
            store.set_scan_state(connection, scan_id=scan_id, state="QUEUED", now_ms=catalog.now_ms())

    # The claim starts exactly one execution of this logical scan. Exhaustion has to be
    # persisted inside the transaction and re-raised only after it commits, otherwise the
    # rollback would undo the terminal job/scan_run state (P1-01 C2 F1).
    prepare_started = time.perf_counter()
    exhausted: CatalogError | None = None
    with catalog.write() as connection:
        try:
            job = jobs.claim_job(
                connection,
                job_id=job_id,
                worker_id=worker,
                now_ms=catalog.now_ms(),
                lease_seconds=lease,
            )
        except CatalogError as error:
            if error.code != RETRY_EXHAUSTED:
                raise
            store.ensure_scan_failed(
                connection,
                scan_id=scan_id,
                now_ms=catalog.now_ms(),
                error_text=error.error_json,
            )
            exhausted = error
        else:
            # Coverage belongs to the current execution: a killed execution's last_seen
            # markers must not hide a deleted position from this complete re-enumeration
            # and must not leave an unbound row blocking the final success guard (P1-01 F2).
            store.reset_execution_coverage(connection, source_id=source_id, scan_id=scan_id)
            if store.get_scan_row(connection, scan_id)["state"] != "RUNNING":
                store.set_scan_state(
                    connection, scan_id=scan_id, state="RUNNING", now_ms=catalog.now_ms()
                )
    if exhausted is not None:
        raise exhausted

    run = _ScanRun(
        service,
        execution=_Execution(job),
        scan_id=scan_id,
        source_id=source_id,
        worker=worker,
        lease=lease,
        policy=policy,
        interrupt_after=interrupt_after,
        cancel_check=cancel_check,
    )
    run.prepare_ms = _elapsed_ms(prepare_started)

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
            job=run.execution.job,
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
        enumeration_started = time.perf_counter()
        run.enumerate(root=root, observations=observations)
        run.enumeration_ms = _elapsed_ms(enumeration_started)
        run.soak()
        processing_started = time.perf_counter()
        run.process(
            root=root,
            total=store.count_seen_locations(
                catalog.connection, source_id=source_id, scan_id=scan_id
            ),
        )
        run.processing_ms = _elapsed_ms(processing_started)
        require_free_space(service.disk_usage, service.data_root)
    except KeyboardInterrupt as error:
        raise ScanInterrupted(f"scan {scan_id} was interrupted by the user") from error
    except CatalogError as error:
        classified = run.classify(error)
        if classified is error:
            raise
        raise classified from error

    now_ms = catalog.now_ms()
    with catalog.write() as connection:
        run.execution.require(connection, now_ms=now_ms)
        pending = store.count_unbound_locations(connection, source_id=source_id, scan_id=scan_id)
        if pending:
            # Defensive invariant: a scan may only succeed once every position it saw has a
            # confirmed content identity (ADR-0002 §5.2).
            raise CatalogError(
                INPUT_UNSTABLE,
                "scan cannot succeed while a seen position still needs content confirmation",
                details={"source_id": source_id, "scan_id": scan_id, "pending_content": pending},
            )
        missing = store.reconcile_missing(connection, source_id=source_id, scan_id=scan_id)
        store.set_scan_state(connection, scan_id=scan_id, state="SUCCEEDED", now_ms=now_ms)
        store.set_last_successful_scan(
            connection, source_id=source_id, scan_id=scan_id, status="ONLINE"
        )
        jobs.finish(
            connection,
            job_id=job_id,
            lease_token=run.execution.job.lease_token or "",
            state=jobs.STATE_SUCCEEDED,
            input_revision=run.execution.job.input_revision,
            processor_id=run.execution.job.processor_id,
            now_ms=now_ms,
            progress=1.0,
        )
    return ScanOutcome(
        scan_id=scan_id,
        source_id=source_id,
        job_id=job_id,
        state="SUCCEEDED",
        files_seen=run.processed,
        locations_missing=missing,
        locations_pending_content=0,
        unstable_retries=run.unstable_retries,
        counts=service.counts(source_id=source_id),
        reused_scan_request=bool(requested["reused"]),
        enumerated=run.enumerated,
        stability_waits=run.stability_waits,
        stability_wait_ms=run.stability_wait_ms,
        prepare_ms=run.prepare_ms,
        enumeration_ms=run.enumeration_ms,
        processing_ms=run.processing_ms,
    )


class _ScanRun:
    """Mutable state of one execution: credential, progress, waits and timing."""

    def __init__(
        self,
        service: Any,
        *,
        execution: _Execution,
        scan_id: int,
        source_id: int,
        worker: str,
        lease: int,
        policy: StabilityPolicy,
        interrupt_after: int | None,
        cancel_check: Callable[[], bool] | None,
    ) -> None:
        self.service = service
        self.catalog = service.catalog
        self.execution = execution
        self.scan_id = scan_id
        self.source_id = source_id
        self.worker = worker
        self.lease = lease
        self.policy = policy
        self.interrupt_after = interrupt_after
        self.cancel_check = cancel_check
        self.enumerated = 0
        self.processed = 0
        self.unstable_retries = 0
        self.stability_waits = 0
        self.stability_wait_ms = 0
        self.progress_written = 0.0
        self.newest_recent_mtime_ms: int | None = None
        self.prepare_ms = 0
        self.enumeration_ms = 0
        self.processing_ms = 0

    # ------------------------------------------------------------- time and progress
    def wall_now_ms(self) -> int:
        reader = getattr(self.service, "wall_clock_ms", None)
        return int(reader()) if reader is not None else time.time_ns() // 1_000_000

    def sleep(self, milliseconds: int) -> None:
        if milliseconds <= 0:
            return
        sleeper = getattr(self.service, "stability_sleep", None)
        if sleeper is None:
            time.sleep(milliseconds / 1000.0)
        else:
            sleeper(milliseconds)

    def _check_cancel(self) -> None:
        if self.cancel_check is not None and self.cancel_check():
            raise ScanInterrupted(
                f"scan {self.scan_id} was cancelled after {self.processed} confirmed files"
            )

    def _write_progress(
        self, connection: Any, value: float, now_ms: int, *, force: bool = False
    ) -> None:
        """Store monotone progress inside an already submission-qualified transaction."""
        value = min(max(float(value), 0.0), 1.0)
        # The first update always lands so even a tiny scan is observable; later updates are
        # throttled so a large library does not write one row per file just for progress.
        if (
            not force
            and self.progress_written > 0.0
            and value - self.progress_written < PROGRESS_EPSILON
        ):
            return
        self.progress_written = jobs.set_progress(
            connection,
            job_id=self.execution.job.job_id,
            lease_token=self.execution.job.lease_token or "",
            now_ms=now_ms,
            progress=value,
        )

    # ------------------------------------------------------------------ enumeration
    def enumerate(self, *, root: Path, observations: Sequence[FileObservation] | None) -> None:
        """Stream the source tree and commit one positive observation per file."""
        if observations is not None:
            # Injected listings are already in memory, so the canonical collision check
            # can run before anything is written (P1-01 contract, unchanged).
            listing = list(observations)
            check_path_key_collisions(listing)
            iterator: Iterator[FileObservation] = iter(listing)
        else:
            iterator = iter_observations(root)
        for observation in iterator:
            self._check_cancel()
            now_ms = self.catalog.now_ms()
            with self.catalog.write() as connection:
                self.execution.maybe_extend(
                    connection, worker=self.worker, lease_seconds=self.lease, now_ms=now_ms
                )
                self.execution.require(connection, now_ms=now_ms)
                store.observe_location_detailed(
                    connection,
                    source_id=self.source_id,
                    scan_id=self.scan_id,
                    relative_path=observation.relative_path,
                    path_key=observation.path_key,
                    size=observation.size,
                    mtime_ns=observation.mtime_ns,
                )
                self._write_progress(
                    connection, _enumeration_progress(self.enumerated + 1), now_ms
                )
            self.enumerated += 1
            recent = _recent_mtime_ms(
                observation.mtime_ns, self.wall_now_ms(), self.policy.window_ms
            )
            if recent is not None:
                self.newest_recent_mtime_ms = max(self.newest_recent_mtime_ms or recent, recent)

    def soak(self) -> None:
        """Wait once for the newest still-fresh file instead of once per file.

        The enumeration already produced the first observation of every file, so a single
        bounded lead-in wait covers a whole batch that was just written and keeps the
        per-file wait rare.
        """
        self._check_cancel()
        window = self.policy.window_ms
        if window <= 0 or self.newest_recent_mtime_ms is None:
            return
        needed = self.newest_recent_mtime_ms + window - self.wall_now_ms()
        if needed <= 0:
            return
        wait = min(needed, self.policy.wait_limit_ms)
        if wait <= 0:
            return
        self.stability_waits += 1
        self.stability_wait_ms += wait
        self.sleep(wait)

    # ------------------------------------------------------------------- processing
    def process(self, *, root: Path, total: int) -> None:
        """Confirm content for every position this execution saw, in bounded chunks."""
        for row in store.iter_seen_locations(
            self.catalog.connection,
            source_id=self.source_id,
            scan_id=self.scan_id,
            chunk_size=LOCATION_CHUNK_SIZE,
        ):
            self._check_cancel()
            if self.interrupt_after is not None and self.processed >= self.interrupt_after:
                raise ScanInterrupted(
                    f"scan {self.scan_id} interrupted after {self.processed} of {total} files"
                )
            relative = str(row["relative_path"])
            self._process_file(
                row=row, relative=relative, absolute=Path(root, relative), total=total
            )
            self.processed += 1

    def _process_file(self, *, row: Any, relative: str, absolute: Path, total: int) -> None:
        """Commit one file's positive facts, or re-queue the scan when it stays unstable."""
        current = FileObservation(
            relative_path=relative,
            absolute_path=absolute,
            size=int(row["size"]),
            mtime_ns=int(row["mtime_ns"]),
            path_key=str(row["path_key"]),
        )
        attempts = 0
        while True:
            current = self._await_stability(relative, absolute, current)
            now_ms = self.catalog.now_ms()
            with self.catalog.write() as connection:
                self.execution.maybe_extend(
                    connection, worker=self.worker, lease_seconds=self.lease, now_ms=now_ms
                )
                self.execution.require(connection, now_ms=now_ms)
                location_id = store.observe_location_detailed(
                    connection,
                    source_id=self.source_id,
                    scan_id=self.scan_id,
                    relative_path=current.relative_path,
                    path_key=current.path_key,
                    size=current.size,
                    mtime_ns=current.mtime_ns,
                ).location_id
                self._write_progress(
                    connection, _processing_progress(self.processed, total), now_ms
                )
            digest = sha256_file(current.absolute_path)
            confirmed = observe_file(current.relative_path, current.absolute_path)
            if confirmed.size != current.size or confirmed.mtime_ns != current.mtime_ns:
                attempts += 1
                if attempts >= UNSTABLE_FILE_ATTEMPTS:
                    raise _requeue(
                        self.catalog,
                        job=self.execution.job,
                        scan_id=self.scan_id,
                        source_id=self.source_id,
                        backoff_ms=self.service.retry_backoff_ms,
                        error=_unstable_error(current, confirmed),
                    )
                self.unstable_retries += 1
                current = confirmed
                continue
            now_ms = self.catalog.now_ms()
            with self.catalog.write() as connection:
                self.execution.maybe_extend(
                    connection, worker=self.worker, lease_seconds=self.lease, now_ms=now_ms
                )
                self.execution.require(connection, now_ms=now_ms)
                bound = store.bind_location_content(
                    connection,
                    location_id=location_id,
                    observed_size=current.size,
                    observed_mtime_ns=current.mtime_ns,
                    sha256=digest,
                    byte_size=current.size,
                    now_ms=now_ms,
                )
            if bound:
                return
            attempts += 1
            if attempts >= UNSTABLE_FILE_ATTEMPTS:
                raise _requeue(
                    self.catalog,
                    job=self.execution.job,
                    scan_id=self.scan_id,
                    source_id=self.source_id,
                    backoff_ms=self.service.retry_backoff_ms,
                    error=CatalogError(
                        INPUT_UNSTABLE,
                        "the position changed before its confirmed content could be bound",
                        details={"relative_path": relative},
                    ),
                )
            self.unstable_retries += 1
            current = observe_file(current.relative_path, current.absolute_path)

    def _await_stability(
        self, relative: str, absolute: Path, observation: FileObservation
    ) -> FileObservation:
        """Return the first observation that has been quiet for the whole window.

        ``mtime`` is compared against the wall clock, not against the lease clock. A file
        whose timestamp is newer than that clock (clock skew, or a camera date in the
        future) cannot be judged this way; it stays protected by the stat before and after
        the hash. A file that keeps changing past the wait budget is not committed at all:
        the scan request goes back to ``RETRY_WAIT`` (P1-02 §2).
        """
        window = self.policy.window_ms
        if window <= 0:
            return observation
        current = observation
        waited_ms = 0
        while True:
            self._check_cancel()
            age_ms = self.wall_now_ms() - (current.mtime_ns // 1_000_000)
            if age_ms < 0 or age_ms >= window:
                return current
            needed = window - age_ms
            if waited_ms + needed > self.policy.wait_limit_ms:
                raise _requeue(
                    self.catalog,
                    job=self.execution.job,
                    scan_id=self.scan_id,
                    source_id=self.source_id,
                    backoff_ms=self.service.retry_backoff_ms,
                    error=CatalogError(
                        INPUT_UNSTABLE,
                        "the file did not stay unchanged for the stability window",
                        details={
                            "relative_path": relative,
                            "size": current.size,
                            "mtime_ns": current.mtime_ns,
                            "waited_ms": waited_ms + needed,
                            "window_ms": window,
                        },
                    ),
                )
            self.stability_waits += 1
            self.stability_wait_ms += needed
            waited_ms += needed
            self.sleep(needed)
            current = observe_file(relative, absolute)

    # ------------------------------------------------------------ error classification
    def classify(self, error: CatalogError) -> CatalogError:
        """Map one execution failure onto the frozen P1-01 classification."""
        if error.code in SUBMISSION_ERROR_CODES:
            # Late/expired execution: no state change, no persistent result.
            return error
        if error.code in (INPUT_UNSTABLE, RETRY_EXHAUSTED):
            # Already persisted as a retryable re-queue or a terminal failure.
            return error
        if error.code in (PATH_KEY_COLLISION, DISK_SPACE_INSUFFICIENT):
            return _fail(
                self.catalog,
                job=self.execution.job,
                scan_id=self.scan_id,
                source_id=self.source_id,
                error=error,
            )
        if error.code in (SOURCE_OFFLINE, ACCESS_DENIED, FILE_READ_ERROR):
            status = "OFFLINE" if error.code == SOURCE_OFFLINE else "ERROR"
            return _block(
                self.catalog,
                job=self.execution.job,
                scan_id=self.scan_id,
                source_id=self.source_id,
                error=error,
                source_status=status,
            )
        return _fail(
            self.catalog,
            job=self.execution.job,
            scan_id=self.scan_id,
            source_id=self.source_id,
            error=error,
        )


def _elapsed_ms(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))


def _recent_mtime_ms(mtime_ns: int, now_ms: int, window_ms: int) -> int | None:
    """Return the mtime of a file that is still inside its stability window."""
    if window_ms <= 0:
        return None
    mtime_ms = int(mtime_ns) // 1_000_000
    if mtime_ms > now_ms:
        return None
    return mtime_ms if now_ms - mtime_ms < window_ms else None


def _enumeration_progress(files: int) -> float:
    """Saturating, strictly monotone enumeration progress inside its own budget."""
    return ENUMERATION_PROGRESS_CEILING * (files / (files + ENUMERATION_PROGRESS_SCALE_FILES))


def _processing_progress(processed: int, total: int) -> float:
    if total <= 0:
        return PROCESSING_PROGRESS_CEILING
    span = PROCESSING_PROGRESS_CEILING - ENUMERATION_PROGRESS_CEILING
    return ENUMERATION_PROGRESS_CEILING + span * (processed / total)


def _require_submission(connection: Any, job: jobs.Job, *, now_ms: int) -> None:
    """ADR-0002 §6.5 gate: no persistent result without a live, matching execution."""
    jobs.validate_submission(
        connection,
        job_id=job.job_id,
        lease_token=job.lease_token or "",
        now_ms=now_ms,
        input_revision=job.input_revision,
        processor_id=job.processor_id,
    )


def _unstable_error(previous: FileObservation, confirmed: FileObservation) -> CatalogError:
    return CatalogError(
        INPUT_UNSTABLE,
        "the file changed between observation and content confirmation",
        details={
            "relative_path": previous.relative_path,
            "observed_size": previous.size,
            "confirmed_size": confirmed.size,
        },
    )


def _requeue(
    catalog: Any,
    *,
    job: jobs.Job,
    scan_id: int,
    source_id: int,
    backoff_ms: int,
    error: CatalogError,
) -> CatalogError:
    """Return the scan request to RETRY_WAIT; the scan_run keeps RUNNING (ADR-0002 §3.3)."""
    now_ms = catalog.now_ms()
    terminal: CatalogError | None = None
    with catalog.write() as connection:
        finished = jobs.finish(
            connection,
            job_id=job.job_id,
            lease_token=job.lease_token or "",
            state=jobs.STATE_RETRY_WAIT,
            input_revision=job.input_revision,
            processor_id=job.processor_id,
            now_ms=now_ms,
            error=error,
            run_after_ms=now_ms + int(backoff_ms),
        )
        if finished.state != jobs.STATE_RETRY_WAIT:
            # The retry ceiling turned this into a terminal failure; the scan_run must
            # reach the same terminal state in the same commit (ADR-0002 §3.3).
            store.ensure_scan_failed(
                connection, scan_id=scan_id, now_ms=now_ms, error_text=error.error_json
            )
            terminal = CatalogError(
                RETRY_EXHAUSTED,
                f"scan {scan_id} exhausted its retry budget after an unstable input",
                details={
                    "scan_id": scan_id,
                    "reason_code": error.code,
                    "attempts": finished.attempts,
                    "max_attempts": finished.max_attempts,
                },
            )
    return terminal if terminal is not None else error


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
