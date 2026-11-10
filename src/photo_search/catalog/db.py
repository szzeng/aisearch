"""The migrated catalog database and its single-writer transaction boundary."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from photo_search.catalog.errors import CATALOG_UNREADABLE, CatalogError
from photo_search.catalog.migrate import migrate_database
from photo_search.catalog.sqlite_io import connect_rw, ensure_wal, system_now_ms
from photo_search.config import catalog_db_path, resolve_data_root

#: Default lease length. Tests and the P1-02 scheduler may use other values.
DEFAULT_LEASE_SECONDS = 120
BEGIN_BUSY_RETRIES = 2
BEGIN_RETRY_BACKOFF_SECONDS = 0.05


def _is_sqlite_busy(error: sqlite3.Error) -> bool:
    """Return true only for SQLite BUSY outcomes safe to retry before BEGIN succeeds."""
    code = getattr(error, "sqlite_errorcode", None)
    return isinstance(code, int) and (code & 0xFF) == sqlite3.SQLITE_BUSY


class Catalog:
    """A migrated catalog database plus the single writer that serialises commits."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        db_path: Path,
        data_root: Path | None = None,
        clock: Callable[[], int] | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        self.connection = connection
        self.db_path = db_path
        #: ``<data_root>/catalog/catalog.db`` is the documented layout; the default keeps
        #: direct constructions (tests, diagnostics) working without an extra argument.
        self.data_root = Path(data_root) if data_root is not None else Path(db_path).parent.parent
        self.clock = clock or system_now_ms
        self.lease_seconds = lease_seconds
        self._lock = threading.RLock()
        self._in_write = False
        self._in_read_snapshot = False
        self._closed = False
        self._close_callbacks: list[Callable[[], None]] = []

    @classmethod
    def open(
        cls,
        db_path: str | Path | None = None,
        *,
        data_root: str | Path | None = None,
        clock: Callable[[], int] | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        disk_usage: Callable[[str | Path], object] | None = None,
    ) -> Catalog:
        """Open the catalog for business use.

        There is deliberately no way to skip the read-only compatibility preflight: the
        database is always inspected read-only first, so a higher ``schema_version`` is
        rejected with ``SCHEMA_TOO_NEW`` before any read-write/WAL initialisation.
        """
        data_root = resolve_data_root(data_root)
        path = Path(db_path) if db_path is not None else catalog_db_path(data_root)
        migrate_database(path, data_root=data_root, disk_usage=disk_usage)
        connection = connect_rw(path)
        try:
            ensure_wal(connection)
        except BaseException:
            connection.close()
            raise
        return cls(
            connection,
            db_path=path,
            data_root=data_root,
            clock=clock,
            lease_seconds=lease_seconds,
        )

    @property
    def schema_version(self) -> int:
        from photo_search.catalog.schema import SCHEMA_VERSION_KEY

        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = ?", (SCHEMA_VERSION_KEY,)
        ).fetchone()
        return int(row[0])

    def now_ms(self) -> int:
        return int(self.clock())

    def read_one(self, sql: str, parameters: tuple = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, parameters).fetchone()

    def read_all(self, sql: str, parameters: tuple = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, parameters).fetchall())

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """Run one short ``BEGIN IMMEDIATE`` transaction under the single-writer lock."""
        with self._lock:
            if self._closed:
                raise CatalogError(CATALOG_UNREADABLE, "catalog connection is closed")
            if self._in_write:
                raise RuntimeError("catalog write transactions must not be nested")
            if self._in_read_snapshot:
                raise RuntimeError("a catalog write must not run inside a read snapshot")
            self._in_write = True
            try:
                begin_started = time.perf_counter()
                attempts = 0
                while True:
                    attempts += 1
                    try:
                        self.connection.execute("BEGIN IMMEDIATE")
                        break
                    except sqlite3.Error as error:
                        if not _is_sqlite_busy(error) or attempts > BEGIN_BUSY_RETRIES:
                            thread = threading.current_thread()
                            raise CatalogError(
                                CATALOG_UNREADABLE,
                                f"cannot start a catalog write transaction: {error}",
                                details={
                                    "phase": "begin_immediate",
                                    "attempts": attempts,
                                    "waited_ms": round(
                                        (time.perf_counter() - begin_started) * 1000, 1
                                    ),
                                    "process_id": os.getpid(),
                                    "thread_id": threading.get_ident(),
                                    "thread_name": thread.name,
                                    "sqlite_errorcode": getattr(error, "sqlite_errorcode", None),
                                    "sqlite_errorname": getattr(error, "sqlite_errorname", None),
                                },
                            ) from error
                        time.sleep(BEGIN_RETRY_BACKOFF_SECONDS * attempts)
                try:
                    yield self.connection
                except BaseException:
                    self._rollback_quietly()
                    raise
                try:
                    self.connection.execute("COMMIT")
                except sqlite3.Error as error:
                    self._rollback_quietly()
                    raise CatalogError(
                        CATALOG_UNREADABLE,
                        f"catalog write transaction failed to commit: {error}",
                        details={
                            "phase": "commit",
                            "sqlite_errorcode": getattr(error, "sqlite_errorcode", None),
                            "sqlite_errorname": getattr(error, "sqlite_errorname", None),
                        },
                    ) from error
            finally:
                self._in_write = False

    @contextmanager
    def read_snapshot(self) -> Iterator[sqlite3.Connection]:
        """Run several reads against ONE SQLite snapshot (a WAL read transaction).

        The connection is in autocommit mode (``isolation_level=None``), so every bare
        ``SELECT`` is its own implicit transaction. That is fine for a single lookup, but two
        reads that must agree - resolving several canonical person ids, or reading the
        ``catalog_filter_revision`` those resolutions are anchored to - can otherwise straddle
        a commit from another connection and produce a set no single state ever contained.

        ``BEGIN DEFERRED`` fixes the snapshot at the first read and holds it until the block
        exits. Nothing here writes: the read transaction only pins WAL pages, other processes
        keep writing (readers never block a WAL writer), and this process's own writers are
        kept out by the single-writer lock for the (short) duration of the block.
        """
        with self._lock:
            if self._closed:
                raise CatalogError(CATALOG_UNREADABLE, "catalog connection is closed")
            if self._in_write:
                raise RuntimeError("a catalog read snapshot must not nest inside a write")
            if self._in_read_snapshot:
                raise RuntimeError("catalog read snapshots must not be nested")
            try:
                self.connection.execute("BEGIN DEFERRED")
            except sqlite3.Error as error:
                raise CatalogError(
                    CATALOG_UNREADABLE,
                    f"cannot start a catalog read snapshot: {error}",
                ) from error
            self._in_read_snapshot = True
            try:
                yield self.connection
            finally:
                self._in_read_snapshot = False
                try:
                    self.connection.execute("COMMIT")
                except sqlite3.Error:
                    self._rollback_quietly()

    def _rollback_quietly(self) -> None:
        try:
            self.connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def add_close_callback(self, callback: Callable[[], None]) -> None:
        """Register an owned resource to drain before this connection is closed.

        Callbacks must not use the owner's connection or lock from another thread.
        Registration and close share the owner lock, so resources cannot attach
        after shutdown has started.
        """
        with self._lock:
            if self._closed:
                raise CatalogError(CATALOG_UNREADABLE, "catalog connection is closed")
            self._close_callbacks.append(callback)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                callbacks, self._close_callbacks = self._close_callbacks, []
                try:
                    for callback in reversed(callbacks):
                        callback()
                finally:
                    self.connection.close()

    def __enter__(self) -> Catalog:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
