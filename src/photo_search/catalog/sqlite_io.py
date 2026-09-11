"""Low-level SQLite access: read-only preflight, read-write connections and WAL."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

from photo_search.catalog.errors import CATALOG_UNREADABLE, CatalogError

BUSY_TIMEOUT_SECONDS = 5.0


def system_now_ms() -> int:
    """Current UTC time in epoch milliseconds (ADR-0002 §2.1)."""
    return time.time_ns() // 1_000_000


def _readonly_uri(path: Path, *, immutable: bool) -> str:
    text = Path(path).absolute().as_posix()
    query = "mode=ro&immutable=1" if immutable else "mode=ro"
    return f"file:{quote(text, safe='/:')}?{query}"


def wal_sidecar_exists(path: str | Path) -> bool:
    return Path(f"{path}-wal").exists()


def open_readonly(path: str | Path, *, immutable: bool | None = None) -> sqlite3.Connection:
    """Open a genuinely read-only connection that creates no write side effects.

    A checkpointed WAL database (no ``-wal`` sidecar) is read with ``immutable=1``: the
    main database file is authoritative then, and SQLite would otherwise create empty
    ``-wal``/``-shm`` sidecars merely to read the schema. When a ``-wal`` file exists the
    WAL must be read for correctness, so plain read-only mode is used and SQLite may
    create its sidecars in that already-dirty state.
    """
    path = Path(path)
    if immutable is None:
        immutable = not wal_sidecar_exists(path)
    try:
        connection = sqlite3.connect(
            _readonly_uri(path, immutable=immutable),
            uri=True,
            timeout=BUSY_TIMEOUT_SECONDS,
        )
    except sqlite3.Error as error:
        raise CatalogError(
            CATALOG_UNREADABLE,
            f"cannot open the catalog database read-only: {error}",
            details={"path": str(path)},
        ) from error
    connection.row_factory = sqlite3.Row
    return connection


def connect_rw(path: str | Path) -> sqlite3.Connection:
    """Open a read-write connection with foreign keys on and manual transactions."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        connection = sqlite3.connect(str(path), isolation_level=None, timeout=BUSY_TIMEOUT_SECONDS)
    except sqlite3.Error as error:
        raise CatalogError(
            CATALOG_UNREADABLE,
            f"cannot open the catalog database: {error}",
            details={"path": str(path)},
        ) from error
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_SECONDS * 1000)}")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def journal_mode(connection: sqlite3.Connection) -> str:
    row = connection.execute("PRAGMA journal_mode").fetchone()
    return str(row[0]).lower() if row is not None else "unknown"


def journal_mode_from_header(path: str | Path) -> str | None:
    """Read the persisted journal mode from the SQLite header, opening nothing.

    A read-only connection cannot always report WAL accurately (an ``immutable``
    connection reports the default mode), so diagnostics use the file header bytes: 2 in
    either format-version byte means the database is in WAL mode.
    """
    path = Path(path)
    try:
        with path.open("rb") as stream:
            header = stream.read(20)
    except OSError:
        return None
    if len(header) < 20 or not header.startswith(b"SQLite format 3\x00"):
        return None
    return "wal" if 2 in (header[18], header[19]) else "delete"


def ensure_wal(connection: sqlite3.Connection) -> str:
    """Enable and verify WAL; a database stuck in rollback mode is a hard failure."""
    mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower()
    if mode != "wal":
        raise CatalogError(
            CATALOG_UNREADABLE,
            f"catalog database did not enter WAL mode (journal_mode={mode})",
        )
    return mode
