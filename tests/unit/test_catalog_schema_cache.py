"""Cached expected DDL must never weaken live schema or migration-file checks."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from photo_search.catalog import schema
from photo_search.catalog.errors import SCHEMA_INCONSISTENT, CatalogError


@pytest.fixture()
def migration_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    directory = tmp_path / "migrations"
    directory.mkdir()
    script = directory / "0001_initial.sql"
    script.write_text("CREATE TABLE first (value TEXT);", encoding="utf-8")
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", directory)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 1)
    schema._compiled_expected_objects.cache_clear()
    try:
        yield script
    finally:
        schema._compiled_expected_objects.cache_clear()


def test_repeated_expectation_avoids_replay_and_isolates_returned_maps(
    migration_script: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_connect = sqlite3.connect
    opened = []

    def counted_connect(*args, **kwargs):
        opened.append(args[0])
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(schema.sqlite3, "connect", counted_connect)
    expected = schema.expected_objects(1)
    assert ("table", "first") in expected
    expected.clear()
    assert ("table", "first") in schema.expected_objects(1)
    assert opened == [":memory:"]


def test_same_size_same_mtime_script_edit_invalidates_expectation(
    migration_script: Path,
) -> None:
    before = migration_script.stat()
    assert ("table", "first") in schema.expected_objects(1)
    migration_script.write_text("CREATE TABLE other (value TEXT);", encoding="utf-8")
    os.utime(migration_script, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = migration_script.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    actual = schema.expected_objects(1)
    assert ("table", "other") in actual
    assert ("table", "first") not in actual


def test_missing_script_is_rejected_after_warming_cache(migration_script: Path) -> None:
    schema.expected_objects(1)
    migration_script.unlink()
    with pytest.raises(CatalogError) as error:
        schema.expected_objects(1)
    assert error.value.code == SCHEMA_INCONSISTENT


def test_versions_remain_distinct_and_old_reads_require_complete_scripts(
    migration_script: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    second = migration_script.parent / "0002_next.sql"
    second.write_text("CREATE TABLE second (value TEXT);", encoding="utf-8")
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 2)
    first = schema.expected_objects(1)
    latest = schema.expected_objects(2)
    assert ("table", "second") not in first
    assert ("table", "second") in latest
    assert schema.expected_objects(1) == first
    second.unlink()
    with pytest.raises(CatalogError) as error:
        schema.expected_objects(1)
    assert error.value.code == SCHEMA_INCONSISTENT


def test_invalid_script_is_rejected_and_can_be_repaired(migration_script: Path) -> None:
    original = schema.expected_objects(1)
    migration_script.write_text("not valid SQL;", encoding="utf-8")
    with pytest.raises(sqlite3.OperationalError):
        schema.expected_objects(1)
    migration_script.write_text("CREATE TABLE first (value TEXT);", encoding="utf-8")
    assert schema.expected_objects(1) == original


def test_actual_database_is_rechecked_after_warming_cache(migration_script: Path) -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(migration_script.read_text(encoding="utf-8"))
        schema.verify_schema(connection, 1)
        connection.execute("DROP TABLE first")
        connection.execute("CREATE TABLE other (value TEXT)")
        with pytest.raises(CatalogError) as error:
            schema.verify_schema(connection, 1)
        assert error.value.code == SCHEMA_INCONSISTENT
        assert "table:first" in error.value.details["missing_objects"]
        assert "table:other" in error.value.details["unexpected_objects"]
    finally:
        connection.close()
