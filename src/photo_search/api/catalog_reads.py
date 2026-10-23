"""Owner-scoped catalog workers: read-only queries and explicit FTS preparation."""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import CATALOG_UNREADABLE, CatalogError
from photo_search.catalog.sqlite_io import connect_rw, open_readonly
from photo_search.search import perf_diagnostics

_CACHE_INIT_LOCK = threading.Lock()
_POOL_INIT_LOCK = threading.Lock()
CATALOG_WORKERS = 2
MAP_COUNT_CACHE_SIZE = 64


class MapCountCache:
    """Only exact integer totals, bounded and owned by one live Catalog object.

    Keys include the map revision read inside the query's SQLite snapshot, not a
    wall-clock lifetime. Concurrent old snapshots may retain their own totals;
    they cannot replace a different snapshot's answer or retain member records.
    """

    def __init__(self) -> None:
        self._values: OrderedDict[tuple[Any, ...], int] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple[Any, ...]) -> int | None:
        with self._lock:
            value = self._values.get(key)
            if value is not None:
                self._values.move_to_end(key)
            return value

    def put(self, key: tuple[Any, ...], total: int) -> None:
        with self._lock:
            self._values[key] = int(total)
            self._values.move_to_end(key)
            while len(self._values) > MAP_COUNT_CACHE_SIZE:
                self._values.popitem(last=False)


def map_count_cache(catalog: Catalog) -> MapCountCache:
    # No path-keyed/process-global cache: another Catalog at the same revision,
    # even after restoring a different DB at the same path, starts independently.
    with _CACHE_INIT_LOCK:
        cache = getattr(catalog, "_map_count_cache", None)
        if cache is None:
            cache = MapCountCache()
            catalog._map_count_cache = cache
        return cache


class _Request:
    def __init__(self) -> None:
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.connection: sqlite3.Connection | None = None

    def cancel(self) -> None:
        self.cancelled.set()
        # SQLite explicitly permits interrupt from another thread. The lock keeps
        # interruption from racing connection.close on its owning worker thread.
        with self.lock:
            if self.connection is not None:
                self.connection.interrupt()


class _CatalogWorkers:
    def __init__(self, owner: Catalog) -> None:
        self.path, self.data_root = owner.db_path, owner.data_root
        self.clock, self.lease_seconds = owner.clock, owner.lease_seconds
        self.count_cache = map_count_cache(owner)
        self._executor = ThreadPoolExecutor(
            max_workers=CATALOG_WORKERS, thread_name_prefix="catalog-worker",
        )
        self._lock = threading.RLock()
        self._activity = threading.Condition()
        self._closed = False
        self._requests: dict[Future[Any], _Request] = {}

    def submit(self, query: Callable[..., Any], args: tuple[Any, ...],
               kwargs: dict[str, Any], *, projection: bool) -> tuple[Future[Any], _Request]:
        request = _Request()
        with self._lock:
            if self._closed:
                raise CatalogError(CATALOG_UNREADABLE, "catalog workers are closed")
            metrics = kwargs.get("perf_metrics")
            submitted_at = time.perf_counter() if isinstance(metrics, dict) else None
            future = self._executor.submit(
                self._run, request, query, args, kwargs, projection, submitted_at,
            )
            self._requests[future] = request
            future.add_done_callback(self._finished)
        with self._activity:
            self._activity.notify_all()
        return future, request

    def _finished(self, future: Future[Any]) -> None:
        with self._lock:
            self._requests.pop(future, None)
        with self._activity:
            self._activity.notify_all()

    def foreground_busy(self) -> bool:
        with self._lock:
            return bool(self._requests)

    def wait_foreground_idle(self, cancelled: threading.Event) -> bool:
        """Wait for owned foreground reads to drain; new work wakes low-priority callers."""
        with self._activity:
            while not cancelled.is_set():
                with self._lock:
                    if not self._requests:
                        return True
                self._activity.wait(timeout=0.25)
            return False

    def wake_activity_waiters(self) -> None:
        with self._activity:
            self._activity.notify_all()

    def _run(self, request: _Request, query: Callable[..., Any], args: tuple[Any, ...],
             kwargs: dict[str, Any], projection: bool,
             submitted_at: float | None = None) -> Any:
        if request.cancelled.is_set():
            raise CancelledError()
        metrics = kwargs.get("perf_metrics")
        if isinstance(metrics, dict) and submitted_at is not None:
            perf_diagnostics.add_duration(
                metrics, "catalog_queue_ms", (time.perf_counter() - submitted_at) * 1000,
            )
        # No migration or journal-mode change in a request worker. Live readers
        # must observe WAL commits even if no WAL existed at submission time.
        connection = None
        worker = None
        open_started = time.perf_counter() if isinstance(metrics, dict) else 0.0
        try:
            connection = (connect_rw(self.path, create=False) if projection
                          else open_readonly(self.path, immutable=False))
            with request.lock:
                request.connection = connection
                if request.cancelled.is_set():
                    raise CancelledError()
            connection.isolation_level = None
            connection.set_progress_handler(lambda: int(request.cancelled.is_set()), 10_000)
            worker = Catalog(
                connection, db_path=self.path, data_root=self.data_root,
                clock=self.clock, lease_seconds=self.lease_seconds,
            )
            worker._map_count_cache = self.count_cache
            if isinstance(metrics, dict):
                connection.set_trace_callback(perf_diagnostics.sql_trace_callback(metrics))
                perf_diagnostics.add_duration(
                    metrics, "catalog_open_ms", (time.perf_counter() - open_started) * 1000,
                )
            return query(worker, *args, **kwargs)
        finally:
            if isinstance(metrics, dict):
                if connection is not None:
                    metrics["catalog_connection_count"] = int(
                        metrics.get("catalog_connection_count", 0)
                    ) + 1
            close_started = time.perf_counter() if isinstance(metrics, dict) else 0.0
            with request.lock:
                try:
                    # Closing also rolls back an unfinished projection transaction.
                    if worker is None and connection is not None:
                        connection.close()
                    elif worker is not None:
                        worker.close()
                finally:
                    request.connection = None
            if isinstance(metrics, dict):
                perf_diagnostics.add_duration(
                    metrics, "catalog_close_ms", (time.perf_counter() - close_started) * 1000,
                )

    def close(self) -> None:
        with self._lock:
            self._closed = True
            pending = list(self._requests.items())
        for future, request in pending:
            request.cancel()
            future.cancel()
        # This must finish before Catalog.close returns and the app releases its
        # data-root lease. It does not shut down another Catalog's worker pool.
        self._executor.shutdown(wait=True, cancel_futures=True)


def _workers(catalog: Catalog) -> _CatalogWorkers:
    with _POOL_INIT_LOCK:
        pool = getattr(catalog, "_catalog_workers", None)
        if pool is None:
            pool = _CatalogWorkers(catalog)
            try:
                catalog.add_close_callback(pool.close)
            except BaseException:
                pool.close()
                raise
            catalog._catalog_workers = pool
        return pool


def foreground_busy(catalog: Catalog) -> bool:
    """Whether a foreground catalog request is active for this app-owned catalog."""
    with _POOL_INIT_LOCK:
        pool = getattr(catalog, "_catalog_workers", None)
    return False if pool is None else pool.foreground_busy()


def wait_foreground_idle(catalog: Catalog, cancelled: threading.Event) -> bool:
    """Event-driven foreground priority gate for bounded background catalog work."""
    with _POOL_INIT_LOCK:
        pool = getattr(catalog, "_catalog_workers", None)
    return True if pool is None else pool.wait_foreground_idle(cancelled)


def wake_foreground_idle(catalog: Catalog) -> None:
    with _POOL_INIT_LOCK:
        pool = getattr(catalog, "_catalog_workers", None)
    if pool is not None:
        pool.wake_activity_waiters()


async def _run_catalog_work(catalog: Catalog, query: Callable[..., Any], args: tuple[Any, ...],
                            kwargs: dict[str, Any], *, projection: bool) -> Any:
    future, request = _workers(catalog).submit(query, args, kwargs, projection=projection)
    try:
        return await asyncio.wrap_future(future)
    except asyncio.CancelledError:
        request.cancel()
        future.cancel()
        raise


async def run_catalog_read(catalog: Catalog, query: Callable[..., Any], *args: Any,
                           **kwargs: Any) -> Any:
    """Run a query on a thread-owned, genuinely read-only connection."""
    return await _run_catalog_work(catalog, query, args, kwargs, projection=False)


async def run_catalog_projection(catalog: Catalog, prepare: Callable[..., Any], *args: Any,
                                 **kwargs: Any) -> Any:
    """Run controlled FTS catch-up on an existing DB, separate from read queries.

    The callable must validate before writing and use explicit short Catalog.write
    transactions. A subsequent run_catalog_read opens a fresh validated snapshot.
    """
    return await _run_catalog_work(catalog, prepare, args, kwargs, projection=True)


async def run_catalog_search(catalog: Catalog, query: Callable[..., Any], *args: Any,
                             **kwargs: Any) -> Any:
    """Run a search with its own connection, bounded workers and shutdown ownership.

    Search services may catch up FTS in short Catalog.write transactions before their
    own read snapshots. They never share the request thread's SQLite connection. The
    owner drains running model calls and closes this connection before releasing its lease.
    """
    return await _run_catalog_work(catalog, query, args, kwargs, projection=True)
