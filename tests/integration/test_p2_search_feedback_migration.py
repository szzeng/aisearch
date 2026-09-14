"""P2-06B: the continuous v7 -> v8 feedback migration and its failure boundaries.

The upgrade backs up first, advances in one transaction and re-verifies the target schema;
a failure leaves the v7 database byte-identical, ``0001``..``0007`` stay frozen, and the
new ``search_feedback`` table can only ever grow: a verdict that could be edited or deleted
would not be an audit chain.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from photo_search.catalog import schema
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
V7 = 7

FEEDBACK_OBJECTS = {
    "search_feedback",
    "idx_search_feedback_session",
    "trg_search_feedback_append_only_update",
    "trg_search_feedback_append_only_delete",
}

FROZEN_V7_ROW_SQL = (
    "SELECT asset_id, sha256, byte_size, created_at, state FROM assets ORDER BY asset_id",
    "SELECT location_id, relative_path, availability FROM file_locations ORDER BY location_id",
    "SELECT revision FROM catalog_filter_revision",
    "SELECT revision FROM place_suggestion_revision",
    "SELECT asset_id, filename_text, place_text, tokenizer_version, fts_generation_id "
    "FROM search_documents ORDER BY asset_id",
    "SELECT generation_id, space_id, processor_id, dimension, state, is_active "
    "FROM index_generations ORDER BY generation_id",
    "SELECT profile_id, embedding_processor_id, space_id, image_generation_id, "
    "reranker_processor_id, fusion_config_json FROM search_profiles ORDER BY profile_id",
    "SELECT state_id, profile_id, updated_at FROM active_search_profile",
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


def build_v7(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a real, populated v7 database using only the frozen 0001..0007 scripts."""
    path = db_path(tmp_path)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, V7))
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V7)
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
            "INSERT INTO catalog_filter_revision(revision_id, revision, committed_at, cause) "
            "VALUES (1, 3, 5, 'PLACE_EFFECTIVE_ASSIGNMENT_CHANGED') "
            "ON CONFLICT(revision_id) DO UPDATE SET revision = 3, committed_at = 5"
        )
        connection.execute(
            "INSERT INTO search_documents(asset_id, filename_text, place_text, "
            "source_fingerprint, tokenizer_version, fts_generation_id, updated_at) "
            "VALUES (1, '照 片 一 jpg', '', ?, 1, 'fts-v1', 5)",
            ("c" * 64,),
        )
        connection.execute(
            "INSERT INTO index_generations(generation_id, space_id, processor_id, dimension, "
            "state, is_active, created_at, updated_at) "
            "VALUES ('image-gen-v7', 'space-v7', 'processor-v7', 3, 'READY', 1, 5, 5)"
        )
        connection.execute(
            "INSERT INTO search_profiles(profile_id, embedding_processor_id, space_id, "
            "image_generation_id, query_preprocess_version, query_parser_version, "
            "fts_tokenizer_version, fts_generation_id, reranker_processor_id, "
            "fusion_config_json, runtime_package_id, created_at) "
            "VALUES ('profile-v7', 'processor-v7', 'space-v7', 'image-gen-v7', 'query-v1', "
            "'parser-v1', '1', 'fts-v1', NULL, '{\"revision\":\"rrf-v1\",\"method\":\"RRF\","
            "\"parameters\":{\"k\":60}}', 'onnxruntime-cpu@test', 7)"
        )
        connection.execute(
            "UPDATE active_search_profile SET profile_id = 'profile-v7', updated_at = 8 "
            "WHERE state_id = 1"
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
            for index, sql in enumerate(FROZEN_V7_ROW_SQL)
        }
    finally:
        connection.close()


def feedback_row(
    connection: sqlite3.Connection,
    *,
    request_id: str = "fb-1",
    asset_id: int = 1,
    relevance: str = "RELEVANT",
    note: str | None = None,
    rank: int = 1,
    created_at: int = 9,
) -> None:
    connection.execute(
        "INSERT INTO search_feedback(request_id, search_session_id, asset_id, rank, "
        "relevance, note, created_at) VALUES (?, 'session-1', ?, ?, ?, ?, ?)",
        (str(request_id), int(asset_id), int(rank), str(relevance), note, int(created_at)),
    )


def test_migration_scripts_are_continuous_and_0008_is_the_feedback_log() -> None:
    versions = [number for number, _ in schema.migration_files()]
    assert versions == list(range(1, SUPPORTED + 1))
    assert versions[7] == 8
    assert (REAL_MIGRATIONS_DIR / "0008_search_feedback.sql").is_file()
    objects = {name for _, name in schema.expected_objects(SUPPORTED)}
    assert FEEDBACK_OBJECTS <= objects


def test_v7_to_v8_is_continuous_backed_up_and_lossless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v7(tmp_path, monkeypatch)
    assert inspect_database(path) == V7
    before = snapshot(path)
    before_bytes = path.read_bytes()

    result = migrate_database(path, data_root=data_root(tmp_path))

    assert result.previous_version == V7 and result.schema_version == SUPPORTED
    # The 0008 step is this file's subject; later additive migrations (0009+) append to the
    # same continuous chain, so the applied list reaches the current supported version.
    assert result.applied == tuple(range(8, SUPPORTED + 1))
    assert result.backup_path is not None
    backup = open_readonly(result.backup_path)
    try:
        assert schema.read_schema_version(backup) == V7
        assert "search_feedback" not in set(schema.business_tables(backup))
    finally:
        backup.close()

    live = open_readonly(path)
    try:
        assert schema.read_schema_version(live) == SUPPORTED
        schema.verify_schema(live, SUPPORTED)
        assert live.execute("SELECT COUNT(*) FROM search_feedback").fetchone()[0] == 0
        # The frozen v7 facts (including the activated profile) are untouched.
        assert [
            tuple(row) for row in live.execute("SELECT state_id, profile_id, updated_at "
                                               "FROM active_search_profile")
        ] == [(1, "profile-v7", 8)]
    finally:
        live.close()
    assert snapshot(path) == before
    assert path.read_bytes() != before_bytes

    again = migrate_database(path, data_root=data_root(tmp_path))
    assert again.applied == () and again.backup_path is None
    assert again.previous_version == SUPPORTED


def test_the_feedback_table_refuses_illegal_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v7(tmp_path, monkeypatch)
    migrate_database(path, data_root=data_root(tmp_path))
    writer = connect_rw(path)
    try:
        writer.execute("BEGIN IMMEDIATE")
        feedback_row(writer, request_id="fb-1", note="x" * 500)
        # Append-only: neither an edit nor a delete is a legal statement.
        with pytest.raises(sqlite3.IntegrityError):
            writer.execute("UPDATE search_feedback SET relevance = 'NOT_RELEVANT' WHERE feedback_id = 1")
        with pytest.raises(sqlite3.IntegrityError):
            writer.execute("UPDATE search_feedback SET note = 'changed' WHERE feedback_id = 1")
        with pytest.raises(sqlite3.IntegrityError):
            writer.execute("DELETE FROM search_feedback WHERE feedback_id = 1")
        # The idempotency key is unique at the storage layer as well as in the service.
        with pytest.raises(sqlite3.IntegrityError):
            feedback_row(writer, request_id="fb-1")
        # ... and every shape rule of the contract is enforced by the DDL too.
        with pytest.raises(sqlite3.IntegrityError):
            feedback_row(writer, request_id="fb-bad-asset", asset_id=999)
        with pytest.raises(sqlite3.IntegrityError):
            feedback_row(writer, request_id="fb-bad-verdict", relevance="MAYBE")
        with pytest.raises(sqlite3.IntegrityError):
            feedback_row(writer, request_id="fb-bad-rank", rank=0)
        with pytest.raises(sqlite3.IntegrityError):
            feedback_row(writer, request_id="fb-long-note", note="x" * 501)
        with pytest.raises(sqlite3.IntegrityError):
            feedback_row(writer, request_id="", created_at=0)
        with pytest.raises(sqlite3.IntegrityError):
            feedback_row(writer, request_id="fb-bad-time", created_at=-1)
        writer.execute("COMMIT")
        assert writer.execute("SELECT COUNT(*) FROM search_feedback").fetchone()[0] == 1
        rows = [tuple(row) for row in writer.execute(
            "SELECT request_id, relevance, note, created_at FROM search_feedback"
        )]
        assert rows == [("fb-1", "RELEVANT", "x" * 500, 9)]
    finally:
        writer.close()


def test_an_interrupted_0008_rolls_back_to_v7(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v7(tmp_path, monkeypatch)
    before = snapshot(path)
    directory = migrations_upto(tmp_path, V7)
    (directory / "0008_broken.sql").write_text(
        "CREATE TABLE search_feedback_half(feedback_id INTEGER PRIMARY KEY);\n"
        "INSERT INTO metadata(key, value) VALUES('feedback_probe', 'yes');\n"
        "SELECT * FROM definitely_missing_table;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", directory)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 8)

    with pytest.raises(CatalogError) as failure:
        migrate_database(path, data_root=data_root(tmp_path))

    assert failure.value.code == MIGRATION_FAILED
    assert inspect_database(path) == V7
    assert snapshot(path) == before
    connection = open_readonly(path)
    try:
        tables = set(schema.business_tables(connection))
        assert "search_feedback_half" not in tables and "search_feedback" not in tables
        assert connection.execute(
            "SELECT COUNT(*) FROM metadata WHERE key = 'feedback_probe'"
        ).fetchone()[0] == 0
        assert schema.read_schema_version(connection) == V7
    finally:
        connection.close()


def test_a_v8_database_missing_a_feedback_object_is_inconsistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v7(tmp_path, monkeypatch)
    migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    try:
        connection.execute("DROP TRIGGER trg_search_feedback_append_only_delete")
    finally:
        connection.close()

    with pytest.raises(CatalogError) as error:
        inspect_database(path)

    assert error.value.code == SCHEMA_INCONSISTENT
    assert any(
        "trg_search_feedback_append_only_delete" in name
        for name in error.value.details["missing_objects"]
    )


def test_a_newer_schema_is_refused_without_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v7(tmp_path, monkeypatch)
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
    assert path.read_bytes() == before_bytes
    assert sorted(item.name for item in path.parent.iterdir()) == names
