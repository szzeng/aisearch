"""P2-03 v2->v3 migration and cleanup fault boundaries."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from photo_search.artifacts.store import ArtifactStore
from photo_search.catalog import schema
from photo_search.catalog.errors import MIGRATION_FAILED, CatalogError
from photo_search.catalog.migrate import inspect_database, migrate_database
from photo_search.catalog.sqlite_io import open_readonly


def build_v2(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            (schema.MIGRATIONS_DIR / "0001_initial.sql").read_text(encoding="utf-8")
        )
        connection.execute("INSERT INTO metadata(key, value) VALUES('schema_version', '1')")
        connection.executescript(
            (schema.MIGRATIONS_DIR / "0002_scheduler_runtime.sql").read_text(encoding="utf-8")
        )
        connection.execute("UPDATE metadata SET value = '2' WHERE key = 'schema_version'")
        connection.execute(
            "INSERT INTO assets(sha256, byte_size, created_at, state) "
            "VALUES (?, 7, 1000, 'ACTIVE')",
            ("a" * 64,),
        )
        connection.execute(
            "INSERT INTO jobs(subject_type, subject_id, input_revision, task_type, processor_id, "
            "state, priority, attempts, max_attempts, run_after, progress, created_at, updated_at) "
            "VALUES ('asset', '1', 'r1', 'legacy', 'legacy@1', 'SUCCEEDED', 0, 1, 3, 0, 1, 1, 2)"
        )
        connection.commit()
    finally:
        connection.close()


def rows(path: Path) -> tuple[tuple, tuple]:
    connection = open_readonly(path)
    try:
        return (
            tuple(connection.execute("SELECT * FROM assets").fetchone()),
            tuple(connection.execute("SELECT * FROM jobs").fetchone()),
        )
    finally:
        connection.close()


def test_v2_to_v3_backup_and_migration_preserve_every_old_column(tmp_path: Path) -> None:
    root = tmp_path / "PhotoSearchData"
    path = root / "catalog" / "catalog.db"
    build_v2(path)
    before = rows(path)

    result = migrate_database(path, data_root=root)

    assert result.previous_version == 2 and result.schema_version == 3
    assert result.applied == (3,)
    assert result.backup_path is not None
    backup = open_readonly(result.backup_path)
    try:
        assert schema.read_schema_version(backup) == 2
        assert "artifacts" not in schema.business_tables(backup)
    finally:
        backup.close()
    assert rows(path) == before
    assert inspect_database(path) == 3
    live = open_readonly(path)
    try:
        assert {"artifacts", "index_outbox", "index_entries", "index_generations"} <= set(
            schema.business_tables(live)
        )
    finally:
        live.close()


def test_interrupted_0003_rolls_back_to_v2_without_half_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "PhotoSearchData"
    path = root / "catalog" / "catalog.db"
    build_v2(path)
    before = rows(path)
    directory = tmp_path / "broken-migrations"
    directory.mkdir()
    for version in (1, 2):
        source = next(path for number, path in schema.migration_files() if number == version)
        (directory / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (directory / "0003_broken.sql").write_text(
        "CREATE TABLE artifacts_half(id INTEGER PRIMARY KEY);\n"
        "SELECT * FROM definitely_missing_table;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", directory)

    with pytest.raises(CatalogError) as failure:
        migrate_database(path, data_root=root)

    assert failure.value.code == MIGRATION_FAILED
    assert inspect_database(path) == 2
    assert rows(path) == before
    connection = open_readonly(path)
    try:
        assert "artifacts_half" not in schema.business_tables(connection)
    finally:
        connection.close()


def test_orphan_cleanup_keeps_fresh_temporary_and_referenced_files(tmp_path: Path) -> None:
    # Reference protection is exercised end-to-end in test_p2_index_submission; this case
    # fixes the filesystem grace semantics for a genuinely unreferenced final file.
    root = tmp_path / "PhotoSearchData"
    store = ArtifactStore.open(root)
    old = store.root / "pending" / "orphan.bin"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"orphan")
    os.utime(old, (0, 0))

    class EmptyCatalog:
        @staticmethod
        def read_all(*args, **kwargs):
            return []

    assert store.prune_orphans(EmptyCatalog(), min_age_ms=10_000, now_ms=20_000) == [old]
    fresh = store.root / "pending" / "fresh.tmp"
    fresh.write_bytes(b"partial")
    assert store.prune_temporary(min_age_ms=10_000, now_ms=20_000) == []
