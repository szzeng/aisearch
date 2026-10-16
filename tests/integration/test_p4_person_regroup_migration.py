"""Populated v28 upgrade, rollback and old-build rejection; synthetic catalog only."""

from pathlib import Path

import pytest

from photo_search.catalog import schema
from photo_search.catalog.errors import CatalogError
from photo_search.catalog.migrate import inspect_database, migrate_database
from photo_search.catalog.sqlite_io import connect_rw, open_readonly
from tests.integration.test_p3_face_correction_migration import (
    REAL_MIGRATIONS_DIR,
    build_v11,
    data_root,
    migrations_upto,
)


def rows(path):
    connection = open_readonly(path)
    try:
        return {
            table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
            for table in (
                "person_operations",
                "person_operation_items",
                "person_operation_retention",
                "person_aliases",
                "face_assignment_controls",
                "face_exclusions",
            )
        }
    finally:
        connection.close()


@pytest.mark.parametrize("interrupt", [False, True])
def test_v28_upgrade_is_lossless_and_atomic(tmp_path, monkeypatch, interrupt):
    path = build_v11(tmp_path, monkeypatch)
    with monkeypatch.context() as old:
        old.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 28)
        old.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, 28))
        migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    try:
        connection.execute(
            "INSERT INTO person_operation_retention VALUES ('op-1',99,'{}','[]',1,100,20)"
        )
    finally:
        connection.close()
    before = rows(path)
    # Pin both migration discovery and support to this historical 28 -> 29 contract.
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 29)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, 29))
    if interrupt:
        directory = migrations_upto(tmp_path, 29)
        script = next(directory.glob("0029_*.sql"))
        script.write_text(
            script.read_text(encoding="utf-8") + "\nSELECT missing_fault_column;", encoding="utf-8"
        )
        monkeypatch.setattr(schema, "MIGRATIONS_DIR", directory)
        with pytest.raises(CatalogError) as error:
            migrate_database(path, data_root=data_root(tmp_path))
        assert error.value.code == "MIGRATION_FAILED"
        assert inspect_database(path) == 28 and rows(path) == before
        return
    result = migrate_database(path, data_root=data_root(tmp_path))
    assert result.previous_version == 28 and result.applied == (29,)
    assert result.schema_version == 29 and rows(path) == before
    assert result.backup_path and rows(result.backup_path) == before
    connection = open_readonly(path)
    try:
        assert list(connection.execute("PRAGMA foreign_key_check")) == []
    finally:
        connection.close()
    with monkeypatch.context() as old:
        old.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 28)
        old.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
        with pytest.raises(CatalogError) as error:
            inspect_database(path)
        assert error.value.code == "SCHEMA_TOO_NEW"
    assert rows(path) == before


def test_existing_migrations_are_untouched():
    import subprocess

    root = Path(__file__).resolve().parents[2]
    changed = subprocess.run(
        ["git", "diff", "--name-only", "--", "src/photo_search/catalog/migrations"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert changed.stdout.strip() == ""
