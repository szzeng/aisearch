"""P3-03: the 0011 step of the migration chain, on a real populated v10 database."""

from __future__ import annotations

import hashlib
import shutil
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
#: This file is about the 0011 step itself, so the target is pinned.
V11 = 11

PERSON_TABLES = {
    "person_aliases",
    "person_operations",
    "person_operation_items",
    "face_assignment_controls",
    "face_exclusions",
}

FROZEN_V10_ROW_SQL = (
    "SELECT asset_id, sha256, byte_size, created_at, state FROM assets ORDER BY asset_id",
    "SELECT location_id, relative_path, availability, asset_id FROM file_locations "
    "ORDER BY location_id",
    "SELECT asset_id, filename_text, ocr_text, place_text, tokenizer_version, fts_generation_id "
    "FROM search_documents ORDER BY asset_id",
    "SELECT asset_id, cause, queued_at FROM search_projection_queue ORDER BY asset_id",
    "SELECT person_id, name, name_key, avatar_face_id, merged_into, revision FROM persons "
    "ORDER BY person_id",
    "SELECT face_id, asset_id, spec_revision, ordinal, quality_state FROM faces ORDER BY face_id",
    "SELECT face_id, person_id, assignment_kind, decision, confidence, reason, revision "
    "FROM face_person_links ORDER BY face_id, person_id",
    "SELECT asset_id, region_index, confidence FROM asset_ocr_regions ORDER BY asset_id, region_index",
    "SELECT key, value FROM metadata WHERE key <> 'schema_version' ORDER BY key",
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
            shutil.copy2(source, directory / source.name)
    return directory


def build_v10(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a real, populated v10 database using only the frozen 0001..0010 scripts."""
    path = db_path(tmp_path)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, 10))
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 10)
    migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO source_roots(display_name, root_path, volume_hint, status) "
            "VALUES ('照片 库', 'C:\\photos 中文', 'C:', 'ONLINE')"
        )
        connection.execute(
            "INSERT INTO assets(sha256, byte_size, created_at, state) VALUES (?, 4096, 1000, 'ACTIVE')",
            ("a" * 64,),
        )
        connection.execute(
            "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, size, "
            "mtime_ns, last_seen_scan_id, availability) "
            "VALUES (1, '2024 年/合影 一.jpg', '2024 年/合影 一.jpg', 1, 4096, 7, NULL, 'PRESENT')"
        )
        connection.execute(
            "INSERT INTO search_documents(asset_id, filename_text, user_text, ocr_text, "
            "caption_text, tag_text, place_text, source_fingerprint, tokenizer_version, "
            "fts_generation_id, updated_at) VALUES (1, '合 影 一 jpg', '', '', '', '', '', ?, 1, "
            "'fts-v1', 1500)",
            (hashlib.sha256(b"frozen-v10").hexdigest(),),
        )
        connection.execute(
            "INSERT INTO search_projection_queue(asset_id, cause, queued_at) "
            "VALUES (1, 'OCR_TEXT_CHANGED', 1234) ON CONFLICT(asset_id) DO UPDATE SET "
            "cause = excluded.cause, queued_at = excluded.queued_at"
        )
        connection.execute(
            "INSERT INTO persons(person_id, revision, created_at, updated_at) VALUES (3, 2, 1, 1)"
        )
        connection.execute(
            "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
            "input_revision, location_id, bbox_json, landmarks_json, det_score, face_pixels, "
            "blur_variance, quality_state, quality_reason, artifact_id, face_revision, "
            "created_at, updated_at) VALUES (?, 1, 'face-rev', 'det', 0, ?, 1, "
            "'[0,0,10,10]', '[[1,2],[2,3],[3,4],[4,5],[5,6]]', 0.9, 66.0, 210.0, 'OK', NULL, "
            "'artifact', 1, 1, 1)",
            ("f" * 64, "a" * 64),
        )
        connection.execute(
            "INSERT INTO face_embeddings(face_id, processor_id, space_id, dimension, metric, "
            "normalization, vector_blob, vector_sha256, created_at, updated_at) "
            "VALUES (?, 'face-model@1', 'face:1', 512, 'cosine', 'L2', x'00', ?, 1, 1)",
            ("f" * 64, "b" * 64),
        )
        connection.execute(
            "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
            "confidence, reason, revision, last_operation_id, created_at, updated_at) "
            "VALUES (?, 3, 'AUTO', 'CONFIRMED', 0.0, NULL, 5, NULL, 1, 1)",
            ("f" * 64,),
        )
        connection.execute(
            "INSERT INTO asset_ocr_regions(asset_id, region_index, text_revision, box_json, "
            "confidence) VALUES (1, 0, 1, '[1,2,3,4]', 0.9)"
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
            for index, sql in enumerate(FROZEN_V10_ROW_SQL)
        }
    finally:
        connection.close()


def test_migration_scripts_are_continuous_and_0011_is_the_person_layer() -> None:
    versions = [number for number, _ in schema.migration_files()]
    assert versions == list(range(1, SUPPORTED + 1))
    assert versions[10] == 11
    assert (REAL_MIGRATIONS_DIR / "0011_person_knowledge.sql").is_file()
    assert SUPPORTED == 11


def test_v10_to_v11_is_continuous_backed_up_and_lossless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v10(tmp_path, monkeypatch)
    assert inspect_database(path) == 10
    before = snapshot(path)
    before_bytes = path.read_bytes()

    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V11)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, V11))
    result = migrate_database(path, data_root=data_root(tmp_path))
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)

    assert result.previous_version == 10 and result.schema_version == V11
    assert result.applied == (11,)
    assert result.backup_path is not None
    backup = open_readonly(result.backup_path)
    try:
        assert schema.read_schema_version(backup) == 10
        assert PERSON_TABLES.isdisjoint(schema.business_tables(backup))
        # The backup still carries the five-cause queue.
        assert backup.execute(
            "SELECT COUNT(*) FROM search_projection_queue WHERE cause = 'OCR_TEXT_CHANGED'"
        ).fetchone()[0] == 1
    finally:
        backup.close()

    live = open_readonly(path)
    try:
        assert schema.read_schema_version(live) == V11
        assert PERSON_TABLES <= set(schema.business_tables(live))
        schema.verify_schema(live, V11)
        # The person layer starts empty; the existing unnamed group is not fabricated into.
        for table in sorted(PERSON_TABLES):
            assert live.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        # The rebuilt queue keeps every pre-existing row and gains the sixth cause.
        assert [
            tuple(row)
            for row in live.execute(
                "SELECT asset_id, cause, queued_at FROM search_projection_queue ORDER BY asset_id"
            )
        ] == [(1, "OCR_TEXT_CHANGED", 1234)]
        assert "PERSON_CHANGED" in str(
            live.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' "
                "AND name = 'search_projection_queue'"
            ).fetchone()[0]
        )
    finally:
        live.close()
    assert snapshot(path) == before
    assert path.read_bytes() != before_bytes

    again = migrate_database(path, data_root=data_root(tmp_path))
    assert again.applied == () and again.previous_version == V11


def test_the_person_tables_enforce_the_p3_03_invariants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v10(tmp_path, monkeypatch)
    migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO persons(person_id, name, name_key, revision, created_at, updated_at) "
            "VALUES (7, '爸爸', 'nfc1:爸爸', 1, 1, 1)"
        )
        connection.execute(
            "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
            "target_person_id, state, operation_revision, created_at, committed_at) "
            "VALUES ('op-1', 'req-1', ?, 'RENAME', 7, 'COMMITTED', 1, 1, 1)",
            ("c" * 64,),
        )
        # An unknown operation type, a duplicate request_id and a reused revision are refused.
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
                "target_person_id, state, operation_revision, created_at, committed_at) "
                "VALUES ('op-2', 'req-2', ?, 'SPLIT', 7, 'COMMITTED', 2, 1, 1)",
                ("c" * 64,),
            )
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
                "target_person_id, state, operation_revision, created_at, committed_at) "
                "VALUES ('op-3', 'req-1', ?, 'RENAME', 7, 'COMMITTED', 2, 1, 1)",
                ("c" * 64,),
            )
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
                "target_person_id, state, operation_revision, created_at, committed_at) "
                "VALUES ('op-4', 'req-4', ?, 'RENAME', 7, 'COMMITTED', 1, 1, 1)",
                ("c" * 64,),
            )
        # An UNDONE row must name the operation that compensated it.
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
                "target_person_id, state, operation_revision, created_at, committed_at) "
                "VALUES ('op-5', 'req-5', ?, 'RENAME', 7, 'UNDONE', 5, 1, 1)",
                ("c" * 64,),
            )
        connection.execute(
            "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
            "target_person_id, state, operation_revision, undo_of_operation_id, undone_by, "
            "created_at, committed_at) "
            "VALUES ('op-6', 'req-6', ?, 'UNDO', 7, 'COMMITTED', 6, 'op-1', NULL, 1, 1)",
            ("c" * 64,),
        )
        connection.execute(
            "UPDATE person_operations SET state = 'UNDONE', undone_by = 'op-6' "
            "WHERE operation_id = 'op-1'"
        )
        # An operation is never its own compensation.
        with pytest.raises(Exception):
            connection.execute(
                "UPDATE person_operations SET undo_of_operation_id = 'op-1' "
                "WHERE operation_id = 'op-1'"
            )
        # Alias uniqueness is scoped to the person, and a blank alias is refused.
        connection.execute(
            "INSERT INTO person_aliases(person_id, alias, normalized_alias, last_operation_id, "
            "created_at) VALUES (7, '老爸', '老爸', 'op-1', 1)"
        )
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO person_aliases(person_id, alias, normalized_alias, created_at) "
                "VALUES (7, '老爸', '老爸', 1)"
            )
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO person_aliases(person_id, alias, normalized_alias, created_at) "
                "VALUES (7, '', '', 1)"
            )
        # Recording an alias against an operation that does not exist is refused.
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO person_aliases(person_id, alias, normalized_alias, "
                "last_operation_id, created_at) VALUES (7, '奶奶', '奶奶', 'missing', 1)"
            )
        connection.execute(
            "INSERT INTO face_assignment_controls(face_id, mode, revision, last_operation_id, "
            "updated_at) VALUES (?, 'AUTO_ALLOWED', 1, NULL, 1)",
            ("f" * 64,),
        )
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO face_assignment_controls(face_id, mode, revision, updated_at) "
                "VALUES (?, 'MANUAL_MAYBE', 1, 1)",
                ("f" * 64,),
            )
        connection.execute(
            "INSERT INTO face_exclusions(face_id, person_id, operation_id, active, created_at) "
            "VALUES (?, 7, 'op-1', 1, 1)",
            ("f" * 64,),
        )
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO face_exclusions(face_id, person_id, operation_id, active, "
                "created_at) VALUES (?, 7, 'op-1', 1, 1)",
                ("f" * 64,),
            )
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO face_exclusions(face_id, person_id, operation_id, active, "
                "created_at) VALUES (?, 7, 'op-1', 2, 1)",
                ("f" * 64,),
            )
        # A person referenced by the person layer cannot be deleted.
        with pytest.raises(Exception):
            connection.execute("DELETE FROM persons WHERE person_id = 7")
        connection.execute("ROLLBACK")
    finally:
        connection.close()


def test_the_rebuilt_queue_refuses_an_unknown_cause_and_accepts_the_person_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v10(tmp_path, monkeypatch)
    migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO search_projection_queue(asset_id, cause, queued_at) "
            "VALUES (1, 'PERSON_CHANGED', 2000) ON CONFLICT(asset_id) DO UPDATE SET "
            "cause = excluded.cause, queued_at = excluded.queued_at"
        )
        assert connection.execute(
            "SELECT cause FROM search_projection_queue WHERE asset_id = 1"
        ).fetchone()[0] == "PERSON_CHANGED"
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO search_projection_queue(asset_id, cause, queued_at) "
                "VALUES (1, 'SOMETHING_ELSE', 1)"
            )
        connection.execute("ROLLBACK")
    finally:
        connection.close()


def test_an_interrupted_0011_rolls_back_completely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v10(tmp_path, monkeypatch)
    before = snapshot(path)
    directory = migrations_upto(tmp_path, V11)
    broken = directory / "0011_person_knowledge.sql"
    broken.write_text(
        broken.read_text(encoding="utf-8") + "\nSELECT * FROM table_that_does_not_exist;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V11)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", directory)

    with pytest.raises(CatalogError) as error:
        migrate_database(path, data_root=data_root(tmp_path))
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)

    assert error.value.code == MIGRATION_FAILED
    assert inspect_database(path) == 10
    assert snapshot(path) == before
    connection = open_readonly(path)
    try:
        assert PERSON_TABLES.isdisjoint(schema.business_tables(connection))
    finally:
        connection.close()


def test_a_missing_person_table_is_inconsistent_and_a_newer_schema_is_refused(
    tmp_path: Path,
) -> None:
    path = db_path(tmp_path)
    migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    connection.execute("DROP TABLE person_operation_items")
    connection.close()
    with pytest.raises(CatalogError) as error:
        inspect_database(path)
    assert error.value.code == SCHEMA_INCONSISTENT
    assert "person_operation_items" in error.value.details["missing_objects"][0]

    connection = connect_rw(path)
    connection.execute(
        "INSERT INTO metadata(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SUPPORTED + 1),),
    )
    connection.close()
    before = path.read_bytes()
    with pytest.raises(CatalogError) as too_new:
        migrate_database(path, data_root=data_root(tmp_path))
    assert too_new.value.code == SCHEMA_TOO_NEW
    assert path.read_bytes() == before
