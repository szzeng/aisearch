"""Rate-limited OCR job heartbeats on a worker-owned SQLite connection."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.catalog.sqlite_io import connect_rw
from photo_search.jobs import queue as jobs

logger = logging.getLogger("photo_search.ocr.heartbeat")


class OcrLeaseHeartbeat:
    """One lazy thread per drain; no SQLite writes for jobs below the interval."""

    def __init__(self, catalog: Catalog, *, lease_seconds: int) -> None:
        self._catalog = catalog
        self._lease_seconds = max(1, int(lease_seconds))
        self._condition = threading.Condition()
        self._active: tuple[int, str, str] | None = None
        self._stopping = False
        self._thread: threading.Thread | None = None

    @contextmanager
    def track(self, request: Any, worker_id: str) -> Iterator[None]:
        identity = (int(request.job_id), str(request.lease_token), str(worker_id))
        with self._condition:
            if self._stopping or self._active is not None:
                raise RuntimeError("OCR heartbeat is not available for another task")
            self._active = identity
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, name="ocr-lease-heartbeat", daemon=True)
                self._thread.start()
            self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                if self._active == identity:
                    self._active = None
                self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._stopping = True
            self._active = None
            self._condition.notify_all()
            thread = self._thread
        if thread is not None:
            # The enclosing OCR worker remains alive, retaining the data-root lease,
            # until an in-flight OS/SQLite operation actually returns.
            thread.join()

    def _loop(self) -> None:
        catalog = None
        try:
            while True:
                with self._condition:
                    while self._active is None and not self._stopping:
                        self._condition.wait()
                    if self._stopping:
                        return
                    identity = self._active
                    self._condition.wait(timeout=max(0.1, self._lease_seconds / 3.0))
                    if self._stopping:
                        return
                    if identity is None or self._active != identity:
                        continue
                try:
                    if catalog is None:
                        connection = connect_rw(self._catalog.db_path, create=False)
                        catalog = Catalog(
                            connection, db_path=self._catalog.db_path,
                            data_root=self._catalog.data_root, clock=self._catalog.clock,
                            lease_seconds=self._lease_seconds,
                        )
                    with catalog.write() as connection:
                        jobs.heartbeat(
                            connection, job_id=identity[0], lease_token=identity[1],
                            worker_id=identity[2], now_ms=catalog.now_ms(),
                            lease_seconds=self._lease_seconds,
                        )
                except Exception:
                    # A stale owner is never revived; final submission rechecks the token
                    # and time. Transient writer contention is retried on the next interval.
                    logger.warning("OCR lease heartbeat failed; submission will revalidate", exc_info=True)
        finally:
            if catalog is not None:
                catalog.close()
