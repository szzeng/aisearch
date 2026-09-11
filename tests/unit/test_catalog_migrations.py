"""P1-01: version discovery, atomic migration, backup and WAL (ADR-0002 §7)."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from photo_search.catalog import schema
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import (
    DISK_SPACE_INSUFFICIENT,
    MIGRATION_FAILED,
    SCHEMA_INCONSISTENT,
    SCHEMA_TOO_NEW,
    CatalogError,
)
from photo_search.catalog.migrate import catalog_info, inspect_database, migrate_database
from photo_search.catalog.sqlite_io import connect_rw, open_readonly

CORE_TABLES = {
    "assets",
    "file_locations",
    "jobs",
    "metadata",
    "scan_runs",
    "source_roots",
}


def _data_root(tmp_path: Path) -> Path:
    return tmp_path / "PhotoSearchData"


def _db_path(tmp_path: Path) -> Path:
    return _data_root(tmp_path) / "catalog" / "catalog.db"


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _Usage:
    def __init__(self, free: int) -> None:
        self.total = 10**12
        self.used = 0
        self.free = free


def test_fresh_database_migrates_to_the_supported_version(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)

    result = migrate_database(db_path, data_root=_data_root(tmp_path))

    assert result.previous_version == 0
    assert result.schema_version == schema.SUPPORTED_SCHEMA_VERSION
    assert result.applied == (1,)
    assert result.backup_path is None
    assert inspect_database(db_path) == 1
    connection = open_readonly(db_path)
    try:
        assert set(schema.business_tables(connection)) == CORE_TABLES
        schema.verify_schema(connection, 1)
    finally:
        connection.close()
    writable = connect_rw(db_path)
    try:
        assert str(writable.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
        assert str(writable.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower() == "wal"
    finally:
        writable.close()
    assert sorted(path.name for path in db_path.parent.iterdir()) == ["catalog.db"]


def test_repeated_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)
    migrate_database(db_path, data_root=_data_root(tmp_path))
    before = _file_digest(db_path)

    second = migrate_database(db_path, data_root=_data_root(tmp_path))

    assert second.previous_version == 1
    assert second.applied == ()
    assert second.backup_path is None
    assert _file_digest(db_path) == before


def test_versioned_database_with_missing_core_object_is_inconsistent(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)
    migrate_database(db_path, data_root=_data_root(tmp_path))
    connection = connect_rw(db_path)
    connection.execute("DROP INDEX idx_file_locations_asset")
    connection.close()

    with pytest.raises(CatalogError) as error:
        inspect_database(db_path)

    assert error.value.code == SCHEMA_INCONSISTENT
    assert "idx_file_locations_asset" in error.value.details["missing_objects"][0]


def test_tables_without_a_usable_version_are_inconsistent(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE legacy_photos (photo_id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()

    with pytest.raises(CatalogError) as error:
        inspect_database(db_path)

    assert error.value.code == SCHEMA_INCONSISTENT
    with pytest.raises(CatalogError) as open_error:
        migrate_database(db_path, data_root=_data_root(tmp_path))
    assert open_error.value.code == SCHEMA_INCONSISTENT


def test_too_new_schema_is_refused_before_any_write_side_effect(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)
    migrate_database(db_path, data_root=_data_root(tmp_path))
    connection = connect_rw(db_path)
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("UPDATE metadata SET value = '2' WHERE key = 'schema_version'")
    connection.execute("COMMIT")
    connection.close()
    before_bytes = db_path.read_bytes()
    before_names = sorted(path.name for path in db_path.parent.iterdir())

    with pytest.raises(CatalogError) as error:
        inspect_database(db_path)
    assert error.value.code == SCHEMA_TOO_NEW
    assert error.value.details == {"schema_version": 2, "supported_schema_version": 1}

    with pytest.raises(CatalogError) as migrate_error:
        migrate_database(db_path, data_root=_data_root(tmp_path))
    assert migrate_error.value.code == SCHEMA_TOO_NEW

    with pytest.raises(CatalogError) as open_error:
        Catalog.open(db_path, data_root=_data_root(tmp_path))
    assert open_error.value.code == SCHEMA_TOO_NEW

    assert db_path.read_bytes() == before_bytes
    assert sorted(path.name for path in db_path.parent.iterdir()) == before_names
    assert before_names == ["catalog.db"]
    info = catalog_info(db_path, data_root=_data_root(tmp_path))
    assert info["status"] == "schema_too_new"
    assert info["writable"] is False
    assert info["schema_version"] is None


def _fake_migrations(tmp_path: Path, extra: dict[str, str]) -> Path:
    directory = tmp_path / "migrations"
    directory.mkdir(parents=True, exist_ok=True)
    for version, path in schema.migration_files():
        (directory / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    for name, text in extra.items():
        (directory / name).write_text(text, encoding="utf-8")
    return directory


def test_failed_migration_rolls_back_and_keeps_the_old_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _db_path(tmp_path)
    migrate_database(db_path, data_root=_data_root(tmp_path))
    directory = _fake_migrations(
        tmp_path,
        {
            "0002_broken.sql": (
                "CREATE TABLE probe_half (probe_id INTEGER PRIMARY KEY);\n"
                "INSERT INTO metadata(key, value) VALUES('probe', 'yes');\n"
                "SELECT * FROM table_that_does_not_exist;\n"
            )
        },
    )
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", directory)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 2)

    with pytest.raises(CatalogError) as error:
        migrate_database(db_path, data_root=_data_root(tmp_path))

    assert error.value.code == MIGRATION_FAILED
    assert inspect_database(db_path) == 1
    connection = open_readonly(db_path)
    try:
        assert "probe_half" not in schema.business_tables(connection)
        assert connection.execute("SELECT COUNT(*) FROM metadata WHERE key = 'probe'").fetchone()[0] == 0
    finally:
        connection.close()


def test_upgrade_creates_a_consistent_backup_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _db_path(tmp_path)
    migrate_database(db_path, data_root=_data_root(tmp_path))
    directory = _fake_migrations(
        tmp_path,
        {"0002_add_probe.sql": "CREATE TABLE probe_table (probe_id INTEGER PRIMARY KEY);\n"},
    )
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", directory)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 2)

    result = migrate_database(db_path, data_root=_data_root(tmp_path))

    assert result.previous_version == 1
    assert result.schema_version == 2
    assert result.applied == (2,)
    assert result.backup_path is not None
    assert result.backup_path.parent == _data_root(tmp_path) / "backups" / "catalog"
    backup = open_readonly(result.backup_path)
    try:
        assert schema.read_schema_version(backup) == 1
        assert "probe_table" not in schema.business_tables(backup)
        assert set(schema.business_tables(backup)) == CORE_TABLES
    finally:
        backup.close()
    live = open_readonly(db_path)
    try:
        assert schema.read_schema_version(live) == 2
        assert "probe_table" in schema.business_tables(live)
    finally:
        live.close()


def test_insufficient_disk_space_blocks_a_new_database(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)

    with pytest.raises(CatalogError) as error:
        migrate_database(
            db_path,
            data_root=_data_root(tmp_path),
            disk_usage=lambda path: _Usage(free=1024),
        )

    assert error.value.code == DISK_SPACE_INSUFFICIENT
    assert not db_path.exists()


def test_preflight_reads_a_live_wal_database_without_changing_it(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)
    migrate_database(db_path, data_root=_data_root(tmp_path))
    writer = connect_rw(db_path)
    try:
        writer.execute("INSERT INTO metadata (key, value) VALUES ('probe', 'live')")
        assert (db_path.parent / "catalog.db-wal").exists()
        before = db_path.read_bytes()

        info = catalog_info(db_path, data_root=_data_root(tmp_path))
        assert info["status"] == "ok"
        assert info["schema_version"] == 1
        assert info["journal_mode"] == "wal"
        assert inspect_database(db_path) == 1
        assert db_path.read_bytes() == before
        assert writer.execute("SELECT value FROM metadata WHERE key = 'probe'").fetchone()[0] == "live"
    finally:
        writer.close()
