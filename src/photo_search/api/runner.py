"""The single in-process background executor for catalog scans (architecture §10.2).

``POST /api/v1/sources/{id}/scan`` must answer ``202`` without waiting for the scan, and
one process must never run two scans at once. The runner therefore owns exactly one daemon
worker thread and its own ``CatalogService`` (its own SQLite connection, created *inside*
that thread), and it calls the same production entry the CLI uses, so lease, heartbeat,
late-submission qualification, ``reset_execution_coverage`` and recovery of an interrupted
``scan_run`` behave exactly as they do for ``catalog scan``.

Nothing here implements the P2 scheduler: no worker pool, no resource quotas, no AI task
claims, no persistence beyond the rows the scan itself commits.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from photo_search.catalog.errors import CatalogError
from photo_search.ingestion.scanner import ScanInterrupted

logger = logging.getLogger("photo_search.api.runner")

#: Default time ``close`` waits for the worker to notice the stop flag.
CLOSE_TIMEOUT_SECONDS = 5.0


@dataclass
class ScanRunRecord:
    """What the worker did last; diagnostics only, never a source of business state."""

    source_id: int
    state: str
    code: str | None = None
    message: str | None = None
    started_at_ms: int = 0
    finished_at_ms: int = 0
    outcome: dict[str, Any] | None = None


@dataclass
class RunnerState:
    """Test/diagnostic view of the worker; contains no credentials."""

    pending: list[int] = field(default_factory=list)
    active_source_id: int | None = None
    last: ScanRunRecord | None = None
    submitted: int = 0
    completed: int = 0
    failed: int = 0


class ScanRunner:
    """Serialize scan requests onto one daemon worker thread."""

    def __init__(
        self,
        service_factory: Callable[[], Any],
        *,
        executor: Callable[[Any, int], Any] | None = None,
        on_idle_after_success: Callable[[], None] | None = None,
        worker_id: str | None = None,
        now_ms: Callable[[], int] | None = None,
        name: str = "photo-search-scan",
    ) -> None:
        self._service_factory = service_factory
        self._service: Any | None = None
        self._executor = executor
        self._on_idle_after_success = on_idle_after_success
        self._worker_id = worker_id
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self._condition = threading.Condition()
        self._pending: deque[int] = deque()
        self._active: int | None = None
        self._stop = False
        self._closed = False
        self._last: ScanRunRecord | None = None
        self._submitted = 0
        self._completed = 0
        self._failed = 0
        self._thread = threading.Thread(target=self._loop, name=name, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ public API
    def submit(self, source_id: int) -> bool:
        """Queue one scan request; a duplicate of the active/queued source is coerced."""
        source_id = int(source_id)
        with self._condition:
            if self._closed:
                raise RuntimeError("the scan runner is closed")
            duplicate = source_id == self._active or source_id in self._pending
            if duplicate:
                self._condition.notify_all()
                return False
            self._pending.append(source_id)
            self._submitted += 1
            self._condition.notify_all()
            return True

    def state(self) -> RunnerState:
        with self._condition:
            return RunnerState(
                pending=list(self._pending),
                active_source_id=self._active,
                last=self._last,
                submitted=self._submitted,
                completed=self._completed,
                failed=self._failed,
            )

    def is_idle(self) -> bool:
        with self._condition:
            return self._active is None and not self._pending

    def wait_idle(self, timeout: float = 60.0) -> bool:
        """Block until the worker has drained its queue (used by tests and shutdown)."""
        deadline = time.monotonic() + float(timeout)
        with self._condition:
            while self._active is not None or self._pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def close(self, *, timeout: float = CLOSE_TIMEOUT_SECONDS) -> None:
        """Ask the worker to stop without leaving a half-written scan behind."""
        with self._condition:
            self._closed = True
            self._stop = True
            self._pending.clear()
            self._condition.notify_all()
        self._thread.join(timeout=float(timeout))
        if self._thread.is_alive():
            # A scan in flight must not lose the connection it is still committing on; the
            # worker is a daemon thread, so the process can still exit, and the scan_run
            # stays recoverable through its lease (ADR-0002 §5.3).
            logger.warning(
                "scan worker still active after %.1fs; leaving its catalog connection open",
                float(timeout),
            )
            return
        service = self._service
        self._service = None
        if service is not None:
            try:
                service.close()
            except Exception:  # pragma: no cover - shutdown must not raise
                logger.debug("closing the scan service failed", exc_info=True)

    # ---------------------------------------------------------------------- worker
    def _loop(self) -> None:
        succeeded_while_pending = False
        while True:
            with self._condition:
                while not self._pending and not self._stop:
                    self._condition.wait()
                if self._stop:
                    return
                source_id = self._pending.popleft()
                self._active = source_id
                self._condition.notify_all()
            succeeded_while_pending = self._run_one(source_id) or succeeded_while_pending
            with self._condition:
                self._active = None
                self._completed += 1
                notify_warmer = not self._pending and succeeded_while_pending
                if notify_warmer:
                    succeeded_while_pending = False
                self._condition.notify_all()
            if notify_warmer and self._on_idle_after_success is not None:
                try:
                    self._on_idle_after_success()
                except Exception:  # noqa: BLE001 - warming must not affect future scans
                    logger.exception("scheduling place-cover pre-generation failed")

    def _run_one(self, source_id: int) -> bool:
        started = int(self._now_ms())
        try:
            outcome = self._execute(source_id)
        except CatalogError as error:
            self._record_failure(source_id, error.code, error.message, started)
        except ScanInterrupted as interrupted:
            # A cancelled/killed execution leaves the job RUNNING with its lease in place;
            # the next request recovers the same scan_run (ADR-0002 §5.3). This is a
            # reported interruption, not an unexpected crash.
            logger.info("scan for source_id=%s was interrupted: %s", source_id, interrupted)
            self._record_failure(source_id, "SCAN_INTERRUPTED", str(interrupted), started)
        except Exception as error:  # noqa: BLE001 - one bad scan must not kill the worker
            logger.exception("scan worker failed for source_id=%s", source_id)
            self._record_failure(source_id, type(error).__name__, str(error), started)
        else:
            record = ScanRunRecord(
                source_id=source_id,
                state=str(_outcome_field(outcome, "state", "SUCCEEDED")),
                started_at_ms=started,
                finished_at_ms=int(self._now_ms()),
                outcome=outcome if isinstance(outcome, dict) else None,
            )
            with self._condition:
                self._last = record
                self._condition.notify_all()
            return record.state == "SUCCEEDED"
        return False

    def _record_failure(self, source_id: int, code: str, message: str, started: int) -> None:
        with self._condition:
            self._failed += 1
            self._last = ScanRunRecord(
                source_id=source_id,
                state="ERROR",
                code=str(code),
                message=str(message)[:500],
                started_at_ms=started,
                finished_at_ms=int(self._now_ms()),
            )
            self._condition.notify_all()

    def _execute(self, source_id: int) -> Any:
        service = self._service_for_thread()
        if self._executor is not None:
            return self._executor(service, source_id)
        options: dict[str, Any] = {}
        if self._worker_id is not None:
            options["worker_id"] = self._worker_id
        return service.run_scan(source_id, **options)

    def _service_for_thread(self) -> Any:
        if self._service is None:
            self._service = self._service_factory()
        return self._service


def _outcome_field(outcome: Any, key: str, default: Any) -> Any:
    if isinstance(outcome, dict):
        return outcome.get(key, default)
    return getattr(outcome, key, default)
