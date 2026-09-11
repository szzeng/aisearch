"""Version discovery, read-only compatibility preflight, backup and atomic migration."""

from __future__ import annotations

import shutil
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from photo_search.catalog import schema
from photo_search.catalog.errors import (
    DISK_SPACE_INSUFFICIENT,
    MIGRATION_FAILED,
    SCHEMA_INCONSISTENT,
    SCHEMA_TOO_NEW,
    CatalogError,
)
from photo_search.catalog.sqlite_io import (
    connect_rw,
    ensure_wal,
    journal_mode_from_header,
    open_readonly,
)
from photo_search.config import backups_dir, catalog_db_path, resolve_data_root

#: Minimum free space a migration needs beyond the current logical database size.
MIN_MIGRATION_HEADROOM_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class MigrationResult:
    db_path: Path
    previous_version: int
    schema_version: int
    applied: tuple[int, ...]
    backup_path: Path | None

    def as_dict(self) -> dict[str, object]:
        return {
            "db_path": str(self.db_path),
            "previous_version": self.previous_version,
            "schema_version": self.schema_version,
            "applied": list(self.applied),
            "backup_path": str(self.backup_path) if self.backup_path else None,
        }


def _supported_version() -> int:
    return int(schema.SUPPORTED_SCHEMA_VERSION)


def inspect_database(db_path: str | Path) -> int:
    """Return the on-disk schema version using a read-only, side-effect-free connection."""
    path = Path(db_path)
    if not path.exists():
        return 0
    connection = open_readonly(path)
    try:
        tables = schema.business_tables(connection)
        if not tables:
            return 0
        version = schema.read_schema_version(connection)
        if version is None:
            raise CatalogError(
                SCHEMA_INCONSISTENT,
                "catalog database has tables but no usable metadata.schema_version",
                details={"db_path": str(path), "tables": tables},
            )
        supported = _supported_version()
        if version > supported:
            raise CatalogError(
                SCHEMA_TOO_NEW,
                f"catalog schema_version {version} is newer than this build supports "
                f"({supported}); refusing to write",
                details={"schema_version": version, "supported_schema_version": supported},
            )
        schema.verify_schema(connection, version)
        return version
    finally:
        connection.close()


def catalog_info(
    db_path: str | Path | None = None,
    *,
    data_root: str | Path | None = None,
) -> dict[str, object]:
    """Read-only schema/health facts; never opens the database for writing."""
    root = resolve_data_root(data_root)
    path = Path(db_path) if db_path is not None else catalog_db_path(root)
    info: dict[str, object] = {
        "db_path": str(path),
        "exists": path.exists(),
        "supported_schema_version": _supported_version(),
        "writable": False,
    }
    if not path.exists():
        info.update(status="uninitialised", schema_version=None)
        return info
    info["journal_mode"] = journal_mode_from_header(path)
    try:
        version = inspect_database(path)
    except CatalogError as error:
        info.update(status=error.code.lower(), schema_version=None, error=error.as_dict())
        return info
    info.update(status="ok", schema_version=version, writable=version == _supported_version())
    return info
def _logical_size(db_path: Path) -> int:
    total = 0
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            total += candidate.stat().st_size
    return total


def _check_disk_space(
    db_path: Path,
    data_root: Path,
    disk_usage: Callable[[str | Path], object],
) -> dict[str, int]:
    required = _logical_size(db_path) + MIN_MIGRATION_HEADROOM_BYTES
    target = data_root if data_root.exists() else data_root.parent
    usage = disk_usage(target)
    free = int(getattr(usage, "free"))
    if free < required:
        raise CatalogError(
            DISK_SPACE_INSUFFICIENT,
            f"catalog migration needs {required} bytes free but only {free} are available",
            details={"required_bytes": required, "free_bytes": free, "path": str(target)},
        )
    return {"required_bytes": required, "free_bytes": free}


def _backup_database(db_path: Path, data_root: Path, *, now_ms: int) -> Path:
    stamp = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = backups_dir(data_root) / f"catalog-{stamp}.db"
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = open_readonly(db_path, immutable=False)
    try:
        target = sqlite3.connect(str(destination), isolation_level=None, timeout=30.0)
        try:
            source.backup(target)
            target.execute("PRAGMA journal_mode = DELETE")
        finally:
            target.close()
    finally:
        source.close()
    return destination


def _apply_migration(connection: sqlite3.Connection, version: int, path: Path) -> None:
    """Apply one migration and its version bump inside a single transaction."""
    script = path.read_text(encoding="utf-8")
    bump = (
        "INSERT INTO metadata(key, value) VALUES('{key}', '{version}') "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value;"
    ).format(key=schema.SCHEMA_VERSION_KEY, version=version)
    try:
        connection.executescript(
            f"BEGIN IMMEDIATE;\n{script}\n{bump}\nCOMMIT;\n"
        )
    except sqlite3.Error as error:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise CatalogError(
            MIGRATION_FAILED,
            f"migration {path.name} failed and was rolled back: {error}",
            details={"migration": path.name, "schema_version": version},
        ) from error


def migrate_database(
    db_path: str | Path | None = None,
    *,
    data_root: str | Path | None = None,
    disk_usage: Callable[[str | Path], object] | None = None,
) -> MigrationResult:
    """Bring the catalog database to the supported schema version."""
    root = resolve_data_root(data_root)
    path = Path(db_path) if db_path is not None else catalog_db_path(root)
    usage = disk_usage or shutil.disk_usage
    previous = inspect_database(path)
    supported = _supported_version()
    if previous == supported and path.exists():
        return MigrationResult(path, previous, supported, (), None)
    path.parent.mkdir(parents=True, exist_ok=True)
    _check_disk_space(path, root, usage)
    backup_path = None
    if previous > 0 and path.exists():
        backup_path = _backup_database(path, root, now_ms=time.time_ns() // 1_000_000)
    connection = connect_rw(path)
    try:
        ensure_wal(connection)
        applied: list[int] = []
        for version, migration_path in schema.migration_files():
            if version <= previous:
                continue
            _apply_migration(connection, version, migration_path)
            applied.append(version)
        schema.verify_schema(connection, supported)
        if str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
            raise CatalogError(MIGRATION_FAILED, "catalog database is not in WAL mode after migration")
    finally:
        connection.close()
    return MigrationResult(path, previous, supported, tuple(applied), backup_path)
