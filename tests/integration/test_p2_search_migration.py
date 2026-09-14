"""P2-04: the continuous v5 -> v6 search-projection migration and its failure boundaries.

The upgrade must back up first, advance in a single transaction and re-verify the target
schema; a failure must leave the v5 database byte-identical, and 0001..0005 stay frozen.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from photo_search.catalog import schema
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import (
    MIGRATION_FAILED,
    SCHEMA_INCONSISTENT,
    SCHEMA_TOO_NEW,
    CatalogError,
)
from photo_search.catalog.migrate import inspect_database, migrate_database
from photo_search.catalog.sqlite_io import connect_rw, open_readonly

REAL_MIGRATIONS_DIR = Path(schema.__file__).resolve().parent / "migrations"
SUPPORTED = int(schema.SUPPORTED_SCHEMA_VERSION)

SEARCH_TABLES = {
    "search_documents",
    "search_documents_fts",
    "search_documents_fts_config",
    "search_documents_fts_data",
    "search_documents_fts_docsize",
    "search_documents_fts_idx",
    "search_projection_queue",
}

FROZEN_V5_ROW_SQL = (
    "SELECT asset_id, sha256, byte_size, created_at, state FROM assets ORDER BY asset_id",
    "SELECT location_id, relative_path, availability FROM file_locations ORDER BY location_id",
    "SELECT place_id, kind, canonical_name, parent_place_id, lifecycle, revision FROM places "
    "ORDER BY place_id",
    "SELECT asset_id, mode, origin, manual_lock, revision FROM asset_place_assignments "
    "ORDER BY asset_id",
    "SELECT revision FROM catalog_filter_revision",
    "SELECT revision FROM place_suggestion_revision",
)


def data_root(tmp_path: Path) -> Path:
    return tmp_path / "PhotoSearchData"


def db_path(tmp_path: Path) -> Path:
    return data_root(tmp_path) / "catalog" / "catalog.db"


def migrations_upto(tmp_path: Path, version: int) -> Path:
    directory = tmp_path / f"migrations-{version}"
    directory.mkdir(parents=True, exist_ok=True)
    for number, source in schema.migration_files():
        if number <= int(version):
            (directory / source.name).write_text(
                source.read_text(encoding="utf-8"), encoding="utf-8"
            )
    return directory


def build_v5(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a real, populated v5 database using only the frozen 0001..0005 scripts."""
    path = db_path(tmp_path)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, 5))
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 5)
    migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO source_roots(display_name, root_path, volume_hint, status) "
            "VALUES ('照片 库', 'C:\\photos 中文', 'C:', 'ONLINE')"
        )
        connection.execute(
            "INSERT INTO assets(sha256, byte_size, created_at, state) "
            "VALUES (?, 4096, 1000, 'ACTIVE')",
            ("a" * 64,),
        )
        connection.execute(
            "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, size, "
            "mtime_ns, last_seen_scan_id, availability) "
            "VALUES (1, '2024 年/照 片 一.jpg', '2024 年/照 片 一.jpg', 1, 4096, 7, NULL, 'PRESENT')"
        )
        connection.execute(
            "INSERT INTO places(kind, canonical_name, normalized_name, parent_place_id, origin, "
            "lifecycle, revision, created_at, updated_at) "
            "VALUES ('city', '杭州', '杭州', NULL, 'USER', 'ACTIVE', 1, 5, 5)"
        )
        connection.execute(
            "INSERT INTO place_aliases(place_id, text, normalized, language, source, lifecycle, "
            "revision, created_at, updated_at) "
            "VALUES (1, 'Hangzhou', 'hangzhou', 'en', 'USER', 'ACTIVE', 1, 5, 5)"
        )
        connection.execute(
            "INSERT INTO place_operations(operation_id, request_id, payload_sha256, "
            "operation_type, operation_revision, actor, atomic, status, "
            "catalog_filter_revision, created_at, committed_at) "
            "VALUES ('op-1', 'req-1', ?, 'ASSIGN', 1, 'LOCAL_USER', 1, 'COMMITTED', 0, 5, 5)",
            ("b" * 64,),
        )
        connection.execute(
            "INSERT INTO asset_place_assignments(asset_id, assignment_id, place_id, mode, "
            "origin, manual_lock, resolution_id, revision, record_state, actor, operation_id, "
            "created_at, updated_at) "
            "VALUES (1, 'asg-1-1', 1, 'MANUAL_ASSIGNED', 'MANUAL', 1, NULL, 1, 'ACTIVE', "
            "'LOCAL_USER', 'op-1', 5, 5)"
        )
        connection.execute(
            "INSERT INTO catalog_filter_revision(revision_id, revision, committed_at, cause) "
            "VALUES (1, 3, 5, 'PLACE_EFFECTIVE_ASSIGNMENT_CHANGED') "
            "ON CONFLICT(revision_id) DO UPDATE SET revision = 3, committed_at = 5"
        )
        connection.execute("COMMIT")
    finally:
        connection.close()
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)
    return path


def snapshot(path: Path) -> dict[str, list[tuple]]:
    connection = open_readonly(path)
    try:
        return {
            f"q{index}": [tuple(row) for row in connection.execute(sql)]
            for index, sql in enumerate(FROZEN_V5_ROW_SQL)
        }
    finally:
        connection.close()


def test_migration_scripts_are_continuous_and_0006_is_the_new_one() -> None:
    versions = [number for number, _ in schema.migration_files()]
    assert versions == list(range(1, SUPPORTED + 1))
    assert versions[-1] == 6
    assert (REAL_MIGRATIONS_DIR / "0006_search_fts.sql").is_file()


def test_v5_to_v6_is_continuous_backed_up_and_lossless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v5(tmp_path, monkeypatch)
    assert inspect_database(path) == 5
    before = snapshot(path)
    before_bytes = path.read_bytes()

    result = migrate_database(path, data_root=data_root(tmp_path))

    assert result.previous_version == 5 and result.schema_version == SUPPORTED
    assert result.applied == (6,)
    assert result.backup_path is not None
    backup = open_readonly(result.backup_path)
    try:
        assert schema.read_schema_version(backup) == 5
        assert SEARCH_TABLES.isdisjoint(schema.business_tables(backup))
    finally:
        backup.close()

    live = open_readonly(path)
    try:
        assert schema.read_schema_version(live) == SUPPORTED
        assert SEARCH_TABLES <= set(schema.business_tables(live))
        schema.verify_schema(live, SUPPORTED)
        # Nothing is fabricated: the derived layer starts empty and the queue is empty.
        assert live.execute("SELECT COUNT(*) FROM search_documents").fetchone()[0] == 0
        assert live.execute("SELECT COUNT(*) FROM search_projection_queue").fetchone()[0] == 0
    finally:
        live.close()
    assert snapshot(path) == before
    assert path.read_bytes() != before_bytes  # the schema itself did change

    again = migrate_database(path, data_root=data_root(tmp_path))
    assert again.applied == () and again.backup_path is None
    assert again.previous_version == SUPPORTED


def test_the_new_tables_are_an_external_content_fts_with_a_clean_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v5(tmp_path, monkeypatch)
    migrate_database(path, data_root=data_root(tmp_path))
    writer = connect_rw(path)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO search_documents(asset_id, filename_text, place_text, "
            "source_fingerprint, tokenizer_version, fts_generation_id, updated_at) "
            "VALUES (1, '杭 州 湖', '浙 江', ?, 1, 'fts-v1', 5)",
            ("c" * 64,),
        )
        # External content: the FTS table reads the values of the content table itself.
        assert writer.execute(
            "SELECT filename_text FROM search_documents_fts WHERE rowid = 1"
        ).fetchone()[0] == "杭 州 湖"
        assert writer.execute(
            "SELECT rowid FROM search_documents_fts WHERE search_documents_fts MATCH ?",
            ('"杭 州"',),
        ).fetchall()[0]["rowid"] == 1
        writer.execute(
            "INSERT INTO search_documents_fts(search_documents_fts) VALUES('integrity-check')"
        )
        writer.execute("COMMIT")

        # The index is maintained by the content table's own triggers, including updates.
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "UPDATE search_documents SET filename_text = '苏 州 湖', updated_at = 6 "
            "WHERE asset_id = 1"
        )
        assert writer.execute(
            "SELECT COUNT(*) FROM search_documents_fts WHERE search_documents_fts MATCH ?",
            ('"杭 州"',),
        ).fetchone()[0] == 0
        assert writer.execute(
            "SELECT COUNT(*) FROM search_documents_fts WHERE search_documents_fts MATCH ?",
            ('"苏 州"',),
        ).fetchone()[0] == 1
        # Deleting the asset cascades the document away *and* removes its index entry.
        writer.execute("DELETE FROM file_locations WHERE location_id = 1")
        writer.execute("DELETE FROM asset_place_assignments WHERE asset_id = 1")
        writer.execute("DELETE FROM assets WHERE asset_id = 1")
        writer.execute(
            "INSERT INTO search_documents_fts(search_documents_fts) VALUES('integrity-check')"
        )
        assert writer.execute("SELECT COUNT(*) FROM search_documents").fetchone()[0] == 0
        assert [row["rowid"] for row in writer.execute("SELECT rowid FROM search_documents_fts")] == []
        writer.execute("COMMIT")
    finally:
        writer.close()


def test_the_new_tables_refuse_illegal_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v5(tmp_path, monkeypatch)
    migrate_database(path, data_root=data_root(tmp_path))
    writer = connect_rw(path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            writer.execute(
                "INSERT INTO search_documents(asset_id, source_fingerprint, tokenizer_version, "
                "fts_generation_id, updated_at) VALUES (1, 'not-a-digest', 1, 'fts-v1', 5)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            writer.execute(
                "INSERT INTO search_documents(asset_id, source_fingerprint, tokenizer_version, "
                "fts_generation_id, updated_at) VALUES (1, ?, 0, 'fts-v1', 5)",
                ("c" * 64,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            writer.execute(
                "INSERT INTO search_projection_queue(asset_id, cause, queued_at) "
                "VALUES (1, 'SOMETHING_ELSE', 5)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            writer.execute(
                "INSERT INTO search_documents(asset_id, source_fingerprint, tokenizer_version, "
                "fts_generation_id, updated_at) VALUES (999, ?, 1, 'fts-v1', 5)",
                ("c" * 64,),
            )
    finally:
        writer.close()


def test_an_interrupted_0006_rolls_back_to_v5(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v5(tmp_path, monkeypatch)
    before = snapshot(path)
    directory = migrations_upto(tmp_path, 5)
    (directory / "0006_broken.sql").write_text(
        "CREATE TABLE search_documents_half(asset_id INTEGER PRIMARY KEY);\n"
        "INSERT INTO metadata(key, value) VALUES('search_probe', 'yes');\n"
        "SELECT * FROM definitely_missing_table;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", directory)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 6)

    with pytest.raises(CatalogError) as failure:
        migrate_database(path, data_root=data_root(tmp_path))

    assert failure.value.code == MIGRATION_FAILED
    assert inspect_database(path) == 5
    assert snapshot(path) == before
    connection = open_readonly(path)
    try:
        tables = set(schema.business_tables(connection))
        assert "search_documents_half" not in tables and SEARCH_TABLES.isdisjoint(tables)
        assert connection.execute(
            "SELECT COUNT(*) FROM metadata WHERE key = 'search_probe'"
        ).fetchone()[0] == 0
        assert schema.read_schema_version(connection) == 5
    finally:
        connection.close()


def test_a_v6_database_missing_a_search_object_is_inconsistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v5(tmp_path, monkeypatch)
    migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    try:
        connection.execute("DROP TABLE search_projection_queue")
    finally:
        connection.close()

    with pytest.raises(CatalogError) as error:
        inspect_database(path)

    assert error.value.code == SCHEMA_INCONSISTENT
    assert any(
        "search_projection_queue" in name for name in error.value.details["missing_objects"]
    )


def test_a_newer_schema_is_refused_without_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v5(tmp_path, monkeypatch)
    migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = 'schema_version'",
            (str(SUPPORTED + 1),),
        )
        connection.execute("COMMIT")
    finally:
        connection.close()
    before_bytes = path.read_bytes()
    names = sorted(item.name for item in path.parent.iterdir())

    with pytest.raises(CatalogError) as too_new:
        migrate_database(path, data_root=data_root(tmp_path))
    assert too_new.value.code == SCHEMA_TOO_NEW
    with pytest.raises(CatalogError) as refused_open:
        Catalog.open(path, data_root=data_root(tmp_path))
    assert refused_open.value.code == SCHEMA_TOO_NEW
    assert path.read_bytes() == before_bytes
    assert sorted(item.name for item in path.parent.iterdir()) == names
