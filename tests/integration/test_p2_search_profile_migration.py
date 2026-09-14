"""P2-06A: the continuous v6 -> v7 search-profile migration and its failure boundaries.

The upgrade backs up first, advances in one transaction and re-verifies the target schema;
a failure leaves the v6 database byte-identical, ``0001``..``0006`` stay frozen, and the new
``search_profiles`` row can never be rewritten after it is created (ADR-0003 §8).
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

PROFILE_TABLES = {"search_profiles", "active_search_profile"}

FROZEN_V6_ROW_SQL = (
    "SELECT asset_id, sha256, byte_size, created_at, state FROM assets ORDER BY asset_id",
    "SELECT location_id, relative_path, availability FROM file_locations ORDER BY location_id",
    "SELECT place_id, kind, canonical_name, parent_place_id, lifecycle, revision FROM places "
    "ORDER BY place_id",
    "SELECT asset_id, mode, origin, manual_lock, revision FROM asset_place_assignments "
    "ORDER BY asset_id",
    "SELECT revision FROM catalog_filter_revision",
    "SELECT revision FROM place_suggestion_revision",
    "SELECT asset_id, filename_text, place_text, tokenizer_version, fts_generation_id "
    "FROM search_documents ORDER BY asset_id",
    "SELECT generation_id, space_id, processor_id, dimension, state, is_active "
    "FROM index_generations ORDER BY generation_id",
)


def data_root(tmp_path: Path) -> Path:
    return tmp_path / "PhotoSearchData"


def db_path(tmp_path: Path) -> Path:
    return data_root(tmp_path) / "catalog" / "catalog.db"


def migrations_upto(tmp_path: Path, version: int) -> Path:
    directory = tmp_path / f"migrations-{version}"
    directory.mkdir(parents=True, exist_ok=True)
    # Read the real migration set directly: a case that pins SUPPORTED_SCHEMA_VERSION to an
    # older version must still be able to copy that version's frozen scripts.
    for source in sorted(REAL_MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        if int(source.name.split("_", 1)[0]) <= int(version):
            (directory / source.name).write_text(
                source.read_text(encoding="utf-8"), encoding="utf-8"
            )
    return directory


def build_v6(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a real, populated v6 database using only the frozen 0001..0006 scripts."""
    path = db_path(tmp_path)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, 6))
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 6)
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
            "VALUES (1, '2024 年/照 片 一.jpg', '2024 年/照 片 一.jpg', 1, 4096, 7, NULL, "
            "'PRESENT')"
        )
        connection.execute(
            "INSERT INTO places(kind, canonical_name, normalized_name, parent_place_id, origin, "
            "lifecycle, revision, created_at, updated_at) "
            "VALUES ('city', '杭州', '杭州', NULL, 'USER', 'ACTIVE', 1, 5, 5)"
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
        connection.execute(
            "INSERT INTO search_documents(asset_id, filename_text, place_text, "
            "source_fingerprint, tokenizer_version, fts_generation_id, updated_at) "
            "VALUES (1, '照 片 一 jpg', '杭 州', ?, 1, 'fts-v1', 5)",
            ("c" * 64,),
        )
        connection.execute(
            "INSERT INTO index_generations(generation_id, space_id, processor_id, dimension, "
            "state, is_active, created_at, updated_at) "
            "VALUES ('image-gen-v6', 'space-v6', 'processor-v6', 3, 'READY', 1, 5, 5)"
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
            for index, sql in enumerate(FROZEN_V6_ROW_SQL)
        }
    finally:
        connection.close()


def profile_row(connection: sqlite3.Connection, profile_id: str = "profile-1") -> None:
    connection.execute(
        "INSERT INTO search_profiles(profile_id, embedding_processor_id, space_id, "
        "image_generation_id, query_preprocess_version, query_parser_version, "
        "fts_tokenizer_version, fts_generation_id, reranker_processor_id, "
        "fusion_config_json, runtime_package_id, created_at) "
        "VALUES (?, 'processor-v6', 'space-v6', 'image-gen-v6', 'query-v1', 'parser-v1', "
        "'1', 'fts-v1', NULL, '{\"revision\":\"rrf-v1\",\"method\":\"RRF\","
        "\"parameters\":{\"k\":60}}', 'onnxruntime-cpu@test', 7)",
        (str(profile_id),),
    )


def test_migration_scripts_are_continuous_and_0007_is_the_profile_layer() -> None:
    versions = [number for number, _ in schema.migration_files()]
    assert versions == list(range(1, SUPPORTED + 1))
    assert versions[6] == 7
    assert (REAL_MIGRATIONS_DIR / "0007_search_profiles.sql").is_file()
    objects = {name for _, name in schema.expected_objects(SUPPORTED)}
    assert PROFILE_TABLES <= objects
    # The immutability trigger is part of the frozen schema, not a convention.
    assert "trg_search_profiles_immutable" in objects


def test_v6_to_v7_is_continuous_backed_up_and_lossless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v6(tmp_path, monkeypatch)
    assert inspect_database(path) == 6
    before = snapshot(path)
    before_bytes = path.read_bytes()

    result = migrate_database(path, data_root=data_root(tmp_path))

    assert result.previous_version == 6 and result.schema_version == SUPPORTED
    # This file is about the 0007 step; later additive migrations (0008+) append to the
    # same chain, so the expectation is "everything from 7 to the supported version".
    assert result.applied == tuple(range(7, SUPPORTED + 1))
    assert result.backup_path is not None
    backup = open_readonly(result.backup_path)
    try:
        assert schema.read_schema_version(backup) == 6
        assert PROFILE_TABLES.isdisjoint(schema.business_tables(backup))
    finally:
        backup.close()

    live = open_readonly(path)
    try:
        assert schema.read_schema_version(live) == SUPPORTED
        assert PROFILE_TABLES <= set(schema.business_tables(live))
        schema.verify_schema(live, SUPPORTED)
        # The pointer starts empty: no configuration is silently presented as active.
        rows = [tuple(row) for row in live.execute("SELECT * FROM active_search_profile")]
        assert rows == [(1, None, 0)]
        assert live.execute("SELECT COUNT(*) FROM search_profiles").fetchone()[0] == 0
    finally:
        live.close()
    assert snapshot(path) == before
    assert path.read_bytes() != before_bytes

    again = migrate_database(path, data_root=data_root(tmp_path))
    assert again.applied == () and again.backup_path is None
    assert again.previous_version == SUPPORTED


def test_the_profile_tables_refuse_illegal_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v6(tmp_path, monkeypatch)
    migrate_database(path, data_root=data_root(tmp_path))
    writer = connect_rw(path)
    try:
        writer.execute("BEGIN IMMEDIATE")
        profile_row(writer)
        # ADR-0003 §8: a created profile is immutable.
        with pytest.raises(sqlite3.IntegrityError):
            writer.execute("UPDATE search_profiles SET space_id = 'other' WHERE profile_id = 'profile-1'")
        with pytest.raises(sqlite3.IntegrityError):
            writer.execute("UPDATE search_profiles SET created_at = 9 WHERE profile_id = 'profile-1'")
        # The frozen shape of ``fusion_config`` and the closed field set are DDL-enforced.
        with pytest.raises(sqlite3.IntegrityError):
            writer.execute(
                "INSERT INTO search_profiles(profile_id, embedding_processor_id, space_id, "
                "image_generation_id, query_preprocess_version, query_parser_version, "
                "fts_tokenizer_version, fts_generation_id, reranker_processor_id, "
                "fusion_config_json, runtime_package_id, created_at) "
                "VALUES ('p-bad-json', 'processor-v6', 'space-v6', 'image-gen-v6', 'q', 'p', "
                "'1', 'fts-v1', NULL, 'not json', 'onnxruntime-cpu@test', 7)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            writer.execute(
                "INSERT INTO search_profiles(profile_id, embedding_processor_id, space_id, "
                "image_generation_id, query_preprocess_version, query_parser_version, "
                "fts_tokenizer_version, fts_generation_id, reranker_processor_id, "
                "fusion_config_json, runtime_package_id, created_at) "
                "VALUES ('p-bad-shape', 'processor-v6', 'space-v6', 'image-gen-v6', 'q', 'p', "
                "'1', 'fts-v1', NULL, '{\"method\":\"RRF\"}', 'onnxruntime-cpu@test', 7)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            writer.execute(
                "INSERT INTO search_profiles(profile_id, embedding_processor_id, space_id, "
                "image_generation_id, query_preprocess_version, query_parser_version, "
                "fts_tokenizer_version, fts_generation_id, reranker_processor_id, "
                "fusion_config_json, runtime_package_id, created_at) "
                "VALUES ('p-no-gen', 'processor-v6', 'space-v6', 'image-gen-missing', 'q', 'p', "
                "'1', 'fts-v1', NULL, '{\"revision\":\"r\",\"method\":\"RRF\","
                "\"parameters\":{}}', 'onnxruntime-cpu@test', 7)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            writer.execute(
                "INSERT INTO active_search_profile(state_id, profile_id, updated_at) "
                "VALUES (2, NULL, 0)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            writer.execute(
                "UPDATE active_search_profile SET profile_id = 'profile-missing' WHERE state_id = 1"
            )
        writer.execute(
            "UPDATE active_search_profile SET profile_id = 'profile-1', updated_at = 9 "
            "WHERE state_id = 1"
        )
        # A referenced profile cannot be deleted (ON DELETE RESTRICT).
        with pytest.raises(sqlite3.IntegrityError):
            writer.execute("DELETE FROM search_profiles WHERE profile_id = 'profile-1'")
        # A generation referenced by a profile cannot be deleted either.
        with pytest.raises(sqlite3.IntegrityError):
            writer.execute("DELETE FROM index_generations WHERE generation_id = 'image-gen-v6'")
        writer.execute("COMMIT")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE active_search_profile SET profile_id = NULL WHERE state_id = 1")
        writer.execute("DELETE FROM search_profiles WHERE profile_id = 'profile-1'")
        writer.execute("COMMIT")
        assert writer.execute("SELECT COUNT(*) FROM search_profiles").fetchone()[0] == 0
    finally:
        writer.close()


def test_an_interrupted_0007_rolls_back_to_v6(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v6(tmp_path, monkeypatch)
    before = snapshot(path)
    directory = migrations_upto(tmp_path, 6)
    (directory / "0007_broken.sql").write_text(
        "CREATE TABLE search_profiles_half(profile_id TEXT PRIMARY KEY);\n"
        "INSERT INTO metadata(key, value) VALUES('profile_probe', 'yes');\n"
        "SELECT * FROM definitely_missing_table;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", directory)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 7)

    with pytest.raises(CatalogError) as failure:
        migrate_database(path, data_root=data_root(tmp_path))

    assert failure.value.code == MIGRATION_FAILED
    assert inspect_database(path) == 6
    assert snapshot(path) == before
    connection = open_readonly(path)
    try:
        tables = set(schema.business_tables(connection))
        assert "search_profiles_half" not in tables and PROFILE_TABLES.isdisjoint(tables)
        assert connection.execute(
            "SELECT COUNT(*) FROM metadata WHERE key = 'profile_probe'"
        ).fetchone()[0] == 0
        assert schema.read_schema_version(connection) == 6
    finally:
        connection.close()


def test_a_v7_database_missing_a_profile_object_is_inconsistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v6(tmp_path, monkeypatch)
    migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    try:
        connection.execute("DROP TABLE active_search_profile")
    finally:
        connection.close()

    with pytest.raises(CatalogError) as error:
        inspect_database(path)

    assert error.value.code == SCHEMA_INCONSISTENT
    assert any("active_search_profile" in name for name in error.value.details["missing_objects"])


def test_a_newer_schema_is_refused_without_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v6(tmp_path, monkeypatch)
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
