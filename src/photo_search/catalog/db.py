"""The migrated catalog database and its single-writer transaction boundary."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from photo_search.catalog.errors import CATALOG_UNREADABLE, CatalogError
from photo_search.catalog.migrate import migrate_database
from photo_search.catalog.sqlite_io import connect_rw, ensure_wal, system_now_ms
from photo_search.config import catalog_db_path, resolve_data_root

#: Default lease length. Tests and the P1-02 scheduler may use other values.
DEFAULT_LEASE_SECONDS = 120


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
                try:
                    self.connection.execute("BEGIN IMMEDIATE")
                except sqlite3.Error as error:
                    raise CatalogError(
                        CATALOG_UNREADABLE,
                        f"cannot start a catalog write transaction: {error}",
                    ) from error
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

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self.connection.close()
                self._closed = True

    def __enter__(self) -> Catalog:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
