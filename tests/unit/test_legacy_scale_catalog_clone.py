"""The unpublished v32 compatibility path is explicit, exact and copy-only."""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from photo_search.catalog import schema
from photo_search.catalog.errors import SCHEMA_INCONSISTENT, CatalogError
from photo_search.catalog.migrate import inspect_database, migrate_database
from photo_search.catalog.sqlite_io import connect_rw, open_readonly
from tools.diagnostics import clone_legacy_scale_catalog as clone_tool


def seed(path: Path, profile: str = "legacy") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect_rw(path)
    try:
        scripts = [
            script.read_text(encoding="utf-8")
            for number, script in schema.migration_files()
            if number <= 31 or (number == 32 and profile in {"upstream", "mixed"})
        ]
        if profile != "upstream":
            scripts.append(
                (schema.MIGRATIONS_DIR / "0033_scale_read_indexes.sql").read_text(encoding="utf-8")
            )
        # Fixture construction is one transaction: autocommitting each historical
        # DDL adds hundreds of durable writes without testing migration behavior.
        # Product clone/migrate/rollback paths below still run without any patches.
        connection.executescript("BEGIN IMMEDIATE;\n" + "\n".join(scripts))
        connection.execute("INSERT INTO metadata(key,value) VALUES('schema_version','32')")
        connection.execute("INSERT INTO metadata(key,value) VALUES('fixture_identity','before')")
        connection.execute(
            "INSERT INTO persons(name,name_key,revision,created_at,updated_at) "
            "VALUES('保留人物','保留人物',9,10,11)"
        )
        connection.execute("INSERT INTO person_hidden(person_id,ignore_state) VALUES(1,'EXCLUDED')")
        connection.execute(
            "INSERT INTO person_collections(name,name_key,revision,created_at,updated_at,"
            "instance_token) VALUES('保留合集','nfc1:保留合集',7,12,13,?)", ("c" * 32,)
        )
        connection.execute("INSERT INTO person_collection_members VALUES(1,1)")
        connection.execute(
            "INSERT INTO assets(sha256,byte_size,created_at,state) VALUES(?,42,14,'ACTIVE')",
            ("a" * 64,),
        )
        connection.execute("COMMIT")
    finally:
        connection.close()


@pytest.fixture()
def source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(clone_tool, "_SYNTHETIC_ROOT", tmp_path)
    path = tmp_path / "source/catalog.db"
    seed(path)
    return path


def all_rows(connection: sqlite3.Connection) -> dict[str, list[tuple]]:
    return {
        name: [tuple(row) for row in connection.execute(f'SELECT * FROM "{name}"')]
        for name in schema.business_tables(connection)
        if name != "metadata"
    }


def test_explicit_clone_preserves_source_and_every_old_table(source: Path, tmp_path: Path):
    before = source.read_bytes()
    source_names = sorted(p.name for p in source.parent.iterdir())
    reader = open_readonly(source)
    try:
        facts = all_rows(reader)
    finally:
        reader.close()
    destination_root = tmp_path / "copy"
    result = clone_tool.clone_legacy_scale_catalog(source, destination_root)
    path = Path(result["destination_db"])
    assert path == destination_root / "catalog/catalog.db"
    assert inspect_database(path) == schema.SUPPORTED_SCHEMA_VERSION
    assert migrate_database(path, data_root=destination_root).applied == ()
    reader = open_readonly(path)
    try:
        after = all_rows(reader)
        assert {name: after[name] for name in facts} == facts
        assert after["person_collection_face_members"] == []
        assert reader.execute(
            "SELECT value FROM metadata WHERE key='fixture_identity'"
        ).fetchone()[0] == "before"
        assert list(reader.execute("PRAGMA foreign_key_check")) == []
    finally:
        reader.close()
    assert source.read_bytes() == before
    assert sorted(p.name for p in source.parent.iterdir()) == source_names
    assert sorted(p.name for p in path.parent.iterdir()) == ["catalog.db"]


def test_application_startup_still_rejects_legacy_v32_without_writes(source: Path):
    before = source.read_bytes()
    with pytest.raises(CatalogError) as refused:
        migrate_database(source, data_root=source.parent)
    assert refused.value.code == SCHEMA_INCONSISTENT
    assert source.read_bytes() == before
    assert sorted(p.name for p in source.parent.iterdir()) == ["catalog.db"]


@pytest.mark.parametrize("variant", ["upstream", "mixed", "missing", "extra", "old", "future"])
def test_unknown_or_official_schemas_are_not_repaired(
    source: Path, tmp_path: Path, variant: str,
):
    if variant in {"upstream", "mixed"}:
        source = tmp_path / variant / "catalog.db"
        seed(source, variant)
    else:
        connection = connect_rw(source)
        try:
            statements = {
                "missing": "DROP INDEX idx_assets_created_id",
                "extra": "CREATE TABLE unknown_fact(value TEXT)",
                "old": "UPDATE metadata SET value='31' WHERE key='schema_version'",
                "future": "UPDATE metadata SET value='34' WHERE key='schema_version'",
            }
            connection.execute(statements[variant])
        finally:
            connection.close()
    before = source.read_bytes()
    destination = tmp_path / "refused-copy"
    with pytest.raises(CatalogError) as refused:
        clone_tool.clone_legacy_scale_catalog(source, destination)
    assert refused.value.code == SCHEMA_INCONSISTENT
    assert source.read_bytes() == before
    assert not destination.exists()


def test_refuses_existing_destination_or_outside_synthetic_directory(source: Path, tmp_path: Path):
    existing = tmp_path / "existing"
    existing.mkdir()
    sentinel = existing / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        clone_tool.clone_legacy_scale_catalog(source, existing)
    assert sentinel.read_text(encoding="utf-8") == "keep"
    with pytest.raises(ValueError, match="synthetic"):
        clone_tool.clone_legacy_scale_catalog(source, tmp_path.parent / "outside")
    with pytest.raises(ValueError, match="synthetic"):
        clone_tool.clone_legacy_scale_catalog(tmp_path.parent / "outside.db", tmp_path / "copy")


def test_failed_sql_rolls_back_copy_and_does_not_publish(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    before = source.read_bytes()
    scripts = tmp_path / "broken-migrations"
    shutil.copytree(schema.MIGRATIONS_DIR, scripts)
    script = scripts / "0032_person_collection_face_members.sql"
    script.write_text(
        script.read_text(encoding="utf-8") + "\nSELECT * FROM injected_missing_table;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", scripts)
    original_upgrade = clone_tool._upgrade_copy
    rollback_observed = []

    def observe_rollback(connection):
        try:
            original_upgrade(connection)
        except sqlite3.Error:
            rollback_observed.append(schema.read_schema_version(connection) == 32)
            assert "person_collection_face_members" not in schema.business_tables(connection)
            assert not connection.in_transaction
            raise

    monkeypatch.setattr(clone_tool, "_upgrade_copy", observe_rollback)
    destination = tmp_path / "failed-copy"
    with pytest.raises(sqlite3.OperationalError, match="injected_missing_table"):
        clone_tool.clone_legacy_scale_catalog(source, destination)
    assert rollback_observed == [True]
    assert source.read_bytes() == before
    assert not destination.exists()


def test_readonly_wal_copy_uses_same_snapshot_for_preflight_and_backup(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    writer = connect_rw(source)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("UPDATE metadata SET value='wal-before' WHERE key='fixture_identity'")
    assert Path(f"{source}-wal").exists()
    original_verify = clone_tool._verify_legacy
    reads = 0

    def concurrent_commit_after_preflight(connection):
        nonlocal reads
        original_verify(connection)
        reads += 1
        if reads == 1:
            writer.execute("UPDATE metadata SET value='wal-after' WHERE key='fixture_identity'")

    monkeypatch.setattr(clone_tool, "_verify_legacy", concurrent_commit_after_preflight)
    try:
        result = clone_tool.clone_legacy_scale_catalog(source, tmp_path / "wal-copy")
        assert writer.execute(
            "SELECT value FROM metadata WHERE key='fixture_identity'"
        ).fetchone()[0] == "wal-after"
        reader = open_readonly(Path(result["destination_db"]))
        try:
            assert reader.execute(
                "SELECT value FROM metadata WHERE key='fixture_identity'"
            ).fetchone()[0] == "wal-before"
        finally:
            reader.close()
    finally:
        writer.close()
