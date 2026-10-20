"""The single in-process background library refresh (P4-02Y §5).

``POST /api/v1/library/refresh`` must answer ``202`` without waiting for the scan, the
embeddings or the enrichment stages, and one process must never run two refreshes at once.
This is the same shape as :class:`photo_search.api.runner.ScanRunner`: one daemon worker
thread, its own ``CatalogService`` (its own SQLite connection, created *inside* the thread),
and no state beyond the in-memory status/build queue plus whatever each run commits.

Nothing here keeps a progress table: a killed process loses the *display* of the refresh,
and waiting requests, while durable catalog state stays exactly what completed steps wrote, so pressing
the button again safely continues through the underlying idempotency (P4-02Y §5/§12).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import CatalogError
from photo_search.ingestion.scanner import DEFAULT_OBSERVATION_BATCH
from photo_search.refresh.pipeline import (
    REFRESH_MODE_DAILY,
    REFRESH_MODES,
    refresh_library,
)
from photo_search.refresh.state import RefreshStatus, idle_status

logger = logging.getLogger("photo_search.api.refresher")

#: Default time ``close`` waits for the active run; waiting builds are discarded.
CLOSE_TIMEOUT_SECONDS = 5.0


def _normalize_mode(mode: str) -> str:
    """Validate the run mode; the HTTP layer already refuses an unknown spelling."""
    value = str(mode or REFRESH_MODE_DAILY)
    if value not in REFRESH_MODES:
        raise ValueError(f"unknown refresh mode {mode!r}")
    return value


class LibraryRefresher:
    """Run one library refresh at a time on one daemon worker thread."""

    def __init__(
        self,
        service_factory: Callable[[], Any],
        *,
        encoder_provider: Callable[[], Any] | None = None,
        manifest_path: str | Path | None = None,
        device: str | None = None,
        device_policy: str = "cpu_allowed",
        batch_size: int = DEFAULT_OBSERVATION_BATCH,
        enrich: bool = True,
        executor: Callable[[Any, RefreshStatus, Sequence[int] | None], dict[str, Any]]
        | None = None,
        now_ms: Callable[[], int] | None = None,
        on_success: Callable[[], None] | None = None,
        name: str = "photo-search-refresh",
    ) -> None:
        self._service_factory = service_factory
        self._service: Any | None = None
        self._encoder_provider = encoder_provider
        self._manifest_path = manifest_path
        self._device = device
        self._device_policy = str(device_policy)
        self._batch_size = int(batch_size)
        self._enrich = bool(enrich)
        self._executor = executor
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self._on_success = on_success
        self._name = str(name)
        self._lock = threading.RLock()
        self._status: RefreshStatus | None = None
        self._running = False
        self._closed = False
        self._thread: threading.Thread | None = None
        # ``None`` is an all-sources scope; concrete lists remain source-scoped.
        self._pending_builds: deque[tuple[list[int] | None, str | None]] = deque()

    # ------------------------------------------------------------------ public API
    def start(
        self, source_ids: Sequence[int] | None = None, *, mode: str = REFRESH_MODE_DAILY,
        face_profile: str | None = None,
    ) -> dict[str, Any]:
        """Start a run or enqueue a distinct source build behind the active run.

        Daily refresh clicks retain their existing reuse behavior. Scoped builds wait in a
        process-local FIFO; duplicates reuse the active or pending build. Only one worker
        executes, and each run opens/closes its own service on that worker thread.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("the library refresher is closed")
            requested = None if not source_ids else [int(value) for value in source_ids]
            requested_mode = _normalize_mode(mode)
            requested_profile = None if face_profile is None else str(face_profile)
            if requested_profile not in (None, "source_detail", "lightweight"):
                raise ValueError("unknown face profile")
            if requested_mode == "build" and requested is not None:
                requested = sorted(set(requested))
            if self._running:
                if requested_mode != "build":
                    return self._snapshot_locked(accepted=False)
                active = self._status.snapshot() if self._status is not None else {}
                if active.get("mode") == "build" and (
                    active.get("source_ids") == requested
                ) and (
                    requested_profile is None or active.get("face_profile") == requested_profile
                ):
                    return self._snapshot_locked(accepted=False)
                pending_item = (requested, requested_profile)
                if pending_item in self._pending_builds:
                    return self._snapshot_locked(accepted=False, queued=True)
                self._pending_builds.append(pending_item)
                return self._snapshot_locked(accepted=True, queued=True)
            status = self._new_status(requested, requested_mode, requested_profile)
            self._status = status
            self._running = True
            self._thread = threading.Thread(
                target=self._loop,
                args=(status, requested, requested_mode, requested_profile),
                name=self._name,
                daemon=True,
            )
            self._thread.start()
            return self._snapshot_locked(accepted=True)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked(accepted=False)

    def is_running(self) -> bool:
        with self._lock:
            return bool(self._running)

    def wait_idle(self, timeout: float = 600.0) -> bool:
        """Block until the worker finished (tests and shutdown); ``False`` on timeout."""
        deadline = time.monotonic() + float(timeout)
        with self._lock:
            thread = self._thread
        while True:
            with self._lock:
                running = self._running
            if not running:
                return True
            if time.monotonic() >= deadline:
                return False
            if thread is not None:
                thread.join(timeout=min(0.1, max(deadline - time.monotonic(), 0.001)))
            else:  # pragma: no cover - defensive
                time.sleep(0.01)

    def close(self, *, timeout: float = CLOSE_TIMEOUT_SECONDS) -> None:
        """Discard waiting builds and wait briefly for the active run to finish.

        One refresh run opens its service *inside its own thread* and closes it there again,
        so a later run can never reuse a connection another thread created (SQLite's own
        thread affinity). ``close`` therefore only waits for the worker to finish.
        """
        with self._lock:
            self._closed = True
            self._pending_builds.clear()
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=float(timeout))
            if thread.is_alive():
                logger.warning(
                    "the library refresh is still running after %.1fs; leaving its catalog "
                    "connection open",
                    float(timeout),
                )

    # ---------------------------------------------------------------------- worker
    def _loop(
        self, status: RefreshStatus, source_ids: Sequence[int] | None, mode: str,
        face_profile: str | None,
    ) -> None:
        while True:
            self._run(status, source_ids, mode, face_profile)
            with self._lock:
                if self._closed or not self._pending_builds:
                    self._running = False
                    self._thread = None
                    return
                source_ids, face_profile = self._pending_builds.popleft()
                mode = "build"
                status = self._new_status(source_ids, mode, face_profile)
                self._status = status

    def _run(
        self, status: RefreshStatus, source_ids: Sequence[int] | None, mode: str,
        face_profile: str | None,
    ) -> None:
        service: Any | None = None
        try:
            service = self._service_factory()
            if mode == "build" and source_ids is None and hasattr(service, "list_sources"):
                if not service.list_sources():
                    status.note_error(
                        stage="build", code="EMPTY_LIBRARY",
                        message="No registered sources were available when the build started.",
                    )
                    status.finish(state="FAILED", elapsed_ms=0)
                    return
            if self._executor is not None:
                self._executor(service, status, source_ids)
            else:
                from photo_search.face.service import BACKGROUND_MODEL_SLOT

                with BACKGROUND_MODEL_SLOT:
                    self._refresh(service, status, source_ids, mode, face_profile)
        except CatalogError as error:
            logger.info("library refresh failed: %s", error.code)
            status.note_error(stage="refresh", code=str(error.code), message=str(error.message)[:200])
            status.finish(state="FAILED", elapsed_ms=0)
        except Exception as error:  # noqa: BLE001 - one bad refresh must not kill the process
            logger.exception("library refresh raised")
            status.note_error(
                stage="refresh", code=type(error).__name__, message=str(error)[:200]
            )
            status.finish(state="FAILED", elapsed_ms=0)
        finally:
            if service is not None:
                try:
                    service.close()
                except Exception:  # pragma: no cover - shutdown must not raise
                    logger.debug("closing the refresh service failed", exc_info=True)
            if self._on_success is not None and str(status.snapshot().get("state")) == "COMPLETE":
                try:
                    self._on_success()
                except Exception:  # noqa: BLE001 - optional maintenance cannot change refresh result.
                    logger.exception("post-refresh retention check failed")

    def _refresh(
        self, service: Any, status: RefreshStatus, source_ids: Sequence[int] | None,
        mode: str, face_profile: str | None,
    ) -> None:
        refresh_library(
            service,
            status,
            source_ids=source_ids,
            batch_size=self._batch_size,
            encoder_factory=self._encoder_provider,
            manifest_path=self._manifest_path,
            device=self._device or "auto",
            device_policy=self._device_policy,
            enrich=self._enrich,
            mode=mode,
            face_profile=face_profile,
        )

    # -------------------------------------------------------------------- internals
    def _new_status(
        self, source_ids: Sequence[int] | None, mode: str, face_profile: str | None = None
    ) -> RefreshStatus:
        return RefreshStatus(
            refresh_id=uuid.uuid4().hex,
            source_ids=source_ids,
            batch_size=self._batch_size,
            started_at_ms=int(self._now_ms()),
            mode=mode,
            face_profile=face_profile or "source_detail",
            now_ms=self._now_ms,
        )

    def _snapshot_locked(self, *, accepted: bool, queued: bool = False) -> dict[str, Any]:
        status = self._status
        payload = idle_status(batch_size=self._batch_size) if status is None else status.snapshot()
        payload["accepted"] = bool(accepted)
        payload["running"] = bool(self._running)
        payload["queued"] = bool(queued)
        payload["pending_builds"] = [
            list(ids) for ids, _profile in self._pending_builds if ids is not None
        ]
        payload["pending_all_build"] = any(
            ids is None for ids, _profile in self._pending_builds
        )
        return payload


__all__ = ["CLOSE_TIMEOUT_SECONDS", "LibraryRefresher"]
