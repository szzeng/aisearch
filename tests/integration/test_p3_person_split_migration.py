"""P3-05B: the 0013 step of the migration chain, on a real populated v12 database.

0013 widens two *closed* CHECK vocabularies a second time, which SQLite can only do by
rebuilding the tables. This file is the evidence that the rebuild is lossless for every
historical operation, item, undo link, face-control item and alias reference - and that an
interrupted 0013 leaves a complete v12 database behind.
"""

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
#: This file is about the 0013 step itself, so the target is pinned.
V13 = 13

PRESERVED_ROWS = {
    "person_operations": None,
    "person_aliases": None,
    "face_assignment_controls": None,
    "face_exclusions": None,
}


def data_root(tmp_path: Path) -> Path:
    return tmp_path / "PhotoSearchData"


def db_path(tmp_path: Path) -> Path:
    return data_root(tmp_path) / "catalog" / "catalog.db"


def migrations_upto(tmp_path: Path, version: int) -> Path:
    directory = tmp_path / f"migrations-{version}-{hashlib.sha256(str(version).encode()).hexdigest()[:8]}"
    directory.mkdir(parents=True, exist_ok=True)
    for source in sorted(REAL_MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        if int(source.name.split("_", 1)[0]) <= int(version):
            shutil.copy2(source, directory / source.name)
    return directory


def build_v12(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real v12 catalog with P3-03/P3-04 operations, items, denials and alias references."""
    path = db_path(tmp_path)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, 12))
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 12)
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
            "VALUES (1, '2024 年/合影 一.jpg', 'k1', 1, 4096, 7, NULL, 'PRESENT')"
        )
        for person_id, name in ((7, "爸爸"), (8, "妈妈")):
            connection.execute(
                "INSERT INTO persons(person_id, name, name_key, revision, created_at, "
                "updated_at) VALUES (?, ?, ?, 3, 1, 1)",
                (person_id, name, f"nfc1:{name}"),
            )
        for face_id, ordinal in (("f" * 64, 0), ("e" * 64, 1)):
            connection.execute(
                "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
                "input_revision, location_id, bbox_json, landmarks_json, det_score, face_pixels, "
                "blur_variance, quality_state, quality_reason, artifact_id, face_revision, "
                "created_at, updated_at) VALUES (?, 1, 'face-rev', 'det', ?, ?, 1, "
                "'[0,0,10,10]', '[[1,2],[2,3],[3,4],[4,5],[5,6]]', 0.9, 66.0, 210.0, 'OK', "
                "NULL, 'artifact', 1, 1, 1)",
                (face_id, int(ordinal), "a" * 64),
            )
        # A P3-03 rename, its undo, a merge, and one P3-04 face correction.
        connection.execute(
            "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
            "source_person_ids_json, target_person_id, state, operation_revision, "
            "undone_by, catalog_filter_revision, created_at, committed_at) "
            "VALUES ('op-1', 'req-1', ?, 'RENAME', '[]', 7, 'COMMITTED', 1, NULL, 4, 10, 10)",
            ("c" * 64,),
        )
        connection.execute(
            "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
            "source_person_ids_json, target_person_id, state, operation_revision, "
            "undo_of_operation_id, undone_by, catalog_filter_revision, created_at, "
            "committed_at) VALUES ('op-2', 'req-2', ?, 'UNDO', '[]', 7, 'COMMITTED', 2, "
            "'op-1', NULL, 5, 11, 11)",
            ("d" * 64,),
        )
        connection.execute(
            "UPDATE person_operations SET state = 'UNDONE', undone_by = 'op-2' "
            "WHERE operation_id = 'op-1'"
        )
        connection.execute(
            "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
            "source_person_ids_json, target_person_id, state, operation_revision, "
            "catalog_filter_revision, created_at, committed_at) "
            "VALUES ('op-3', 'req-3', ?, 'MERGE_BY_NAME', '[8]', 7, 'COMMITTED', 3, 6, 12, 12)",
            ("e" * 64,),
        )
        connection.execute(
            "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
            "source_person_ids_json, target_person_id, state, operation_revision, "
            "catalog_filter_revision, created_at, committed_at) "
            "VALUES ('op-4', 'req-4', ?, 'BATCH_FACE_EDIT', '[]', NULL, 'COMMITTED', 4, 7, 13, 13)",
            ("1" * 64,),
        )
        for operation_id, entity_id, before, after, revision in (
            ("op-1", 7, '{"a":1}', '{"a":2}', 3),
            ("op-2", 7, '{"a":2}', '{"a":1}', 4),
            ("op-3", 7, '{"b":1}', '{"b":2}', 4),
            ("op-3", 8, '{"c":1}', '{"c":2}', 4),
        ):
            index = 0 if operation_id != "op-3" or entity_id == 7 else 1
            connection.execute(
                "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
                "entity_id, entity_key, before_value_json, after_value_json, after_revision) "
                "VALUES (?, ?, 'PERSON_BUNDLE', ?, NULL, ?, ?, ?)",
                (operation_id, index, entity_id, before, after, revision),
            )
        connection.execute(
            "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
            "entity_id, entity_key, before_value_json, after_value_json, after_revision) "
            "VALUES ('op-4', 0, 'FACE_CONTROL', NULL, ?, '{\"f\":1}', '{\"f\":2}', 2)",
            ("f" * 64,),
        )
        for person_id, alias, normalized, operation_id in (
            (7, "老爸", "老爸", "op-3"),
            (7, "爸爸大人", "爸爸大人", None),
            (8, "老妈", "老妈", "op-1"),
        ):
            connection.execute(
                "INSERT INTO person_aliases(person_id, alias, normalized_alias, "
                "last_operation_id, created_at) VALUES (?, ?, ?, ?, 1)",
                (person_id, alias, normalized, operation_id),
            )
        connection.execute(
            "INSERT INTO face_assignment_controls(face_id, mode, revision, last_operation_id, "
            "updated_at) VALUES (?, 'MANUAL_ASSIGNED', 2, 'op-4', 1)",
            ("f" * 64,),
        )
        connection.execute(
            "INSERT INTO face_exclusions(face_id, person_id, operation_id, active, created_at) "
            "VALUES (?, 8, 'op-1', 1, 1)",
            ("f" * 64,),
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
            table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
            for table in (
                "person_operations",
                "person_aliases",
                "face_assignment_controls",
                "face_exclusions",
            )
        }
    finally:
        connection.close()


def item_rows(path: Path) -> list[tuple]:
    connection = open_readonly(path)
    try:
        return [
            tuple(row)
            for row in connection.execute(
                "SELECT operation_id, item_index, entity_type, entity_id, entity_key, "
                "before_value_json, after_value_json, after_revision "
                "FROM person_operation_items ORDER BY operation_id, item_index"
            )
        ]
    finally:
        connection.close()


def test_migration_scripts_are_continuous_and_0013_is_the_split_layer() -> None:
    versions = [number for number, _ in schema.migration_files()]
    assert versions == list(range(1, SUPPORTED + 1))
    assert versions[12] == 13
    assert (REAL_MIGRATIONS_DIR / "0013_person_split_operations.sql").is_file()
    assert SUPPORTED >= V13


def test_the_frozen_scripts_0001_to_0012_are_byte_identical() -> None:
    names = sorted(
        path.name
        for path in REAL_MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")
        if int(path.name.split("_", 1)[0]) <= 12
    )
    assert names[:12] == [
        "0001_initial.sql",
        "0002_scheduler_runtime.sql",
        "0003_artifact_outbox_index.sql",
        "0004_place_core.sql",
        "0005_place_dataset_resolution.sql",
        "0006_search_fts.sql",
        "0007_search_profiles.sql",
        "0008_search_feedback.sql",
        "0009_ocr_documents.sql",
        "0010_face_core.sql",
        "0011_person_knowledge.sql",
        "0012_face_correction_operations.sql",
    ]
    digests = {
        name: hashlib.sha256((REAL_MIGRATIONS_DIR / name).read_bytes()).hexdigest()
        for name in names
    }
    assert len(set(digests.values())) == len(digests)


def test_v12_to_v13_is_continuous_backed_up_and_lossless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v12(tmp_path, monkeypatch)
    assert inspect_database(path) == 12
    before = snapshot(path)
    before_items = item_rows(path)
    before_bytes = path.read_bytes()

    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V13)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, V13))
    result = migrate_database(path, data_root=data_root(tmp_path))
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)

    assert result.previous_version == 12 and result.schema_version == V13
    assert result.applied == (13,)
    assert result.backup_path is not None
    backup = open_readonly(result.backup_path)
    try:
        assert schema.read_schema_version(backup) == 12
        # The v12 backup still refuses the split vocabulary.
        with pytest.raises(Exception):
            backup.execute(
                "INSERT INTO person_operations(operation_id, request_id, payload_sha256, "
                "type, target_person_id, state, operation_revision, created_at, committed_at) "
                "VALUES ('op-backup', 'req-backup', ?, 'SPLIT', 7, 'COMMITTED', 9, 1, 1)",
                ("f" * 64,),
            )
    finally:
        backup.close()

    live = open_readonly(path)
    try:
        assert schema.read_schema_version(live) == V13
        schema.verify_schema(live, V13)
        assert item_rows(path) == before_items
        operations_sql = str(
            live.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'person_operations'"
            ).fetchone()[0]
        )
        items_sql = str(
            live.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'person_operation_items'"
            ).fetchone()[0]
        )
        assert "SPLIT" in operations_sql
        assert "SPLIT_GROUP" in items_sql
        assert "FACE_ASSIGN" in operations_sql
        assert list(live.execute("PRAGMA foreign_key_check")) == []
    finally:
        live.close()
    assert snapshot(path) == before
    assert item_rows(path) == before_items
    assert path.read_bytes() != before_bytes
    assert [
        tuple(row)
        for row in open_readonly(path).execute(
            "SELECT alias_id, last_operation_id FROM person_aliases ORDER BY alias_id"
        )
    ] == [(1, "op-3"), (2, None), (3, "op-1")]

    # The chain continues from the frozen v13 tip to whatever this build supports (P3-08B
    # added ``0014``); once there, a repeated migrate applies nothing and rewrites nothing.
    again = migrate_database(path, data_root=data_root(tmp_path))
    assert again.applied == tuple(range(V13 + 1, SUPPORTED + 1))
    assert again.previous_version == V13 and again.schema_version == SUPPORTED
    settled = migrate_database(path, data_root=data_root(tmp_path))
    assert settled.applied == () and settled.previous_version == SUPPORTED


def test_the_rebuilt_tables_enforce_the_three_item_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v12(tmp_path, monkeypatch)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V13)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, V13))
    migrate_database(path, data_root=data_root(tmp_path))
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)
    connection = connect_rw(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
            "source_person_ids_json, target_person_id, state, operation_revision, "
            "catalog_filter_revision, created_at, committed_at) "
            "VALUES ('op-split', 'req-split', ?, 'SPLIT', '[7]', 8, 'COMMITTED', 9, 0, 1, 1)",
            ("2" * 64,),
        )
        connection.execute(
            "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
            "entity_id, entity_key, before_value_json, after_value_json, after_revision) "
            "VALUES ('op-split', 0, 'SPLIT_GROUP', 7, NULL, '{}', '{}', 4)"
        )
        with pytest.raises(Exception):
            # A group item that is neither a bundle nor a face must carry an integer id.
            connection.execute(
                "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
                "entity_id, entity_key, before_value_json, after_value_json, after_revision) "
                "VALUES ('op-split', 1, 'SPLIT_GROUP', NULL, NULL, '{}', '{}', 1)"
            )
        with pytest.raises(Exception):
            # ... and never a text key.
            connection.execute(
                "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
                "entity_id, entity_key, before_value_json, after_value_json, after_revision) "
                "VALUES ('op-split', 2, 'SPLIT_GROUP', 7, ?, '{}', '{}', 1)",
                ("f" * 64,),
            )
        with pytest.raises(Exception):
            # A face control still owns the text key and nothing else.
            connection.execute(
                "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
                "entity_id, entity_key, before_value_json, after_value_json, after_revision) "
                "VALUES ('op-split', 3, 'FACE_CONTROL', 7, ?, '{}', '{}', 0)",
                ("f" * 64,),
            )
        with pytest.raises(Exception):
            # A person bundle keeps the P3-03 revision floor.
            connection.execute(
                "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
                "entity_id, entity_key, before_value_json, after_value_json, after_revision) "
                "VALUES ('op-split', 4, 'PERSON_BUNDLE', 7, NULL, '{}', '{}', 0)"
            )
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
                "target_person_id, state, operation_revision, created_at, committed_at) "
                "VALUES ('op-bad', 'req-bad', ?, 'MOVE_FACE', 7, 'COMMITTED', 10, 1, 1)",
                ("3" * 64,),
            )
        connection.execute("ROLLBACK")
    finally:
        connection.close()


def test_an_interrupted_0013_rolls_back_completely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v12(tmp_path, monkeypatch)
    before = snapshot(path)
    before_items = item_rows(path)
    directory = migrations_upto(tmp_path, V13)
    broken = directory / "0013_person_split_operations.sql"
    broken.write_text(
        broken.read_text(encoding="utf-8") + "\nSELECT * FROM table_that_does_not_exist;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V13)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", directory)

    with pytest.raises(CatalogError) as error:
        migrate_database(path, data_root=data_root(tmp_path))
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)

    assert error.value.code == MIGRATION_FAILED
    assert inspect_database(path) == 12
    assert snapshot(path) == before
    assert item_rows(path) == before_items
    connection = open_readonly(path)
    try:
        assert not any(
            name.endswith("_backup") for name in schema.business_tables(connection)
        )
    finally:
        connection.close()


def test_a_missing_item_table_is_inconsistent_and_a_newer_schema_is_refused(
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
