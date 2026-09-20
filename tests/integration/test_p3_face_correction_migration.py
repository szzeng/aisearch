"""P3-04B: the 0012 step of the migration chain, on a real populated v11 database.

0012 has to widen two *closed* CHECK vocabularies, which SQLite can only do by rebuilding
the tables. This file is the evidence that the rebuild is lossless for every historical
operation, item, undo link and alias reference - and that an interrupted 0012 leaves a
complete v11 database behind.
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
#: This file is about the 0012 step itself, so the target is pinned.
V12 = 12

#: The tables whose rows this migration must preserve. ``person_operation_items`` gains a
#: column, so its comparison names the columns explicitly instead of using ``SELECT *``.
PRESERVED_ROWS = {
    "person_operations": list(range(13)),
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


def build_v11(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real v11 catalog with a rename, a merge, an undo and their alias references."""
    path = db_path(tmp_path)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, 11))
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 11)
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
        connection.execute(
            "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
            "input_revision, location_id, bbox_json, landmarks_json, det_score, face_pixels, "
            "blur_variance, quality_state, quality_reason, artifact_id, face_revision, "
            "created_at, updated_at) VALUES (?, 1, 'face-rev', 'det', 0, ?, 1, "
            "'[0,0,10,10]', '[[1,2],[2,3],[3,4],[4,5],[5,6]]', 0.9, 66.0, 210.0, 'OK', "
            "NULL, 'artifact', 1, 1, 1)",
            ("f" * 64, "a" * 64),
        )
        connection.execute(
            "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
            "confidence, reason, revision, last_operation_id, created_at, updated_at) "
            "VALUES (?, 7, 'AUTO', 'CONFIRMED', 0.0, NULL, 5, NULL, 1, 1)",
            ("f" * 64,),
        )
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
        for operation_id, entity_id, before, after, revision in (
            ("op-1", 7, '{"a":1}', '{"a":2}', 3),
            ("op-2", 7, '{"a":2}', '{"a":1}', 4),
            ("op-3", 7, '{"b":1}', '{"b":2}', 4),
            ("op-3", 8, '{"c":1}', '{"c":2}', 4),
        ):
            index = 0 if operation_id != "op-3" or entity_id == 7 else 1
            connection.execute(
                "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
                "entity_id, before_value_json, after_value_json, after_revision) "
                "VALUES (?, ?, 'PERSON_BUNDLE', ?, ?, ?, ?)",
                (operation_id, index, entity_id, before, after, revision),
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
            "updated_at) VALUES (?, 'AUTO_ALLOWED', 1, NULL, 1)",
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
            for table in ("person_operations", "person_aliases", "face_assignment_controls")
        }
    finally:
        connection.close()


def item_rows(path: Path) -> list[tuple]:
    connection = open_readonly(path)
    try:
        return [
            tuple(row)
            for row in connection.execute(
                "SELECT operation_id, item_index, entity_type, entity_id, before_value_json, "
                "after_value_json, after_revision FROM person_operation_items "
                "ORDER BY operation_id, item_index"
            )
        ]
    finally:
        connection.close()


def test_migration_scripts_are_continuous_and_0012_is_the_face_correction_layer() -> None:
    versions = [number for number, _ in schema.migration_files()]
    assert versions == list(range(1, SUPPORTED + 1))
    assert versions[11] == 12
    assert (REAL_MIGRATIONS_DIR / "0012_face_correction_operations.sql").is_file()
    assert SUPPORTED >= V12


def test_the_frozen_scripts_0001_to_0011_are_byte_identical() -> None:
    """0012 rebuilds tables; it must not rewrite a single earlier migration."""
    names = sorted(
        path.name
        for path in REAL_MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")
        if int(path.name.split("_", 1)[0]) <= 11
    )
    assert names[:11] == [
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
    ]
    digests = {
        name: hashlib.sha256((REAL_MIGRATIONS_DIR / name).read_bytes()).hexdigest()
        for name in names
    }
    assert len(set(digests.values())) == len(digests)


def test_v11_to_v12_is_continuous_backed_up_and_lossless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v11(tmp_path, monkeypatch)
    assert inspect_database(path) == 11
    before = snapshot(path)
    before_items = item_rows(path)
    before_bytes = path.read_bytes()

    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V12)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, V12))
    result = migrate_database(path, data_root=data_root(tmp_path))
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)

    assert result.previous_version == 11 and result.schema_version == V12
    assert result.applied == (12,)
    assert result.backup_path is not None
    backup = open_readonly(result.backup_path)
    try:
        assert schema.read_schema_version(backup) == 11
        assert "entity_key" not in str(
            backup.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'person_operation_items'"
            ).fetchone()[0]
        )
        # The v11 backup still refuses a face-level operation type.
        with pytest.raises(Exception):
            backup.execute(
                "INSERT INTO person_operations(operation_id, request_id, payload_sha256, "
                "type, target_person_id, state, operation_revision, created_at, committed_at) "
                "VALUES ('op-backup', 'req-backup', ?, 'FACE_UNASSIGN', 7, 'COMMITTED', 9, 1, 1)",
                ("f" * 64,),
            )
    finally:
        backup.close()

    live = open_readonly(path)
    try:
        assert schema.read_schema_version(live) == V12
        schema.verify_schema(live, V12)
        assert [
            tuple(row)
            for row in live.execute(
                "SELECT operation_id, item_index, entity_type, entity_id, before_value_json, "
                "after_value_json, after_revision FROM person_operation_items "
                "ORDER BY operation_id, item_index"
            )
        ] == before_items
        # The new column exists and is NULL for every historical person-bundle row.
        assert {
            row[0] for row in live.execute("SELECT entity_key FROM person_operation_items")
        } == {None}
        assert "FACE_ASSIGN" in str(
            live.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'person_operations'"
            ).fetchone()[0]
        )
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

    # Still pinned to the 0012 target: a later migration (0013) is a separate step and must
    # not be pulled in by this case's "already at the target" idempotency check.
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V12)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, V12))
    again = migrate_database(path, data_root=data_root(tmp_path))
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)
    assert again.applied == () and again.previous_version == V12


def test_the_rebuilt_item_table_enforces_the_two_entity_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v11(tmp_path, monkeypatch)
    # Still pinned to the 0012 target: this case is about the closed vocabulary *0012*
    # installs, so the later 0013 step (which legitimately widens it again with SPLIT/
    # SPLIT_GROUP) is a separate migration and its own file.
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V12)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, V12))
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
            "VALUES ('op-face', 'req-face', ?, 'FACE_UNASSIGN', '[]', NULL, 'COMMITTED', 9, 0, 1, 1)",
            ("1" * 64,),
        )
        connection.execute(
            "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
            "entity_key, before_value_json, after_value_json, after_revision) "
            "VALUES ('op-face', 0, 'FACE_CONTROL', ?, '{}', '{}', 0)",
            ("f" * 64,),
        )
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
                "entity_id, entity_key, before_value_json, after_value_json, after_revision) "
                "VALUES ('op-face', 1, 'FACE_CONTROL', 9, ?, '{}', '{}', 0)",
                ("f" * 64,),
            )
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
                "entity_key, before_value_json, after_value_json, after_revision) "
                "VALUES ('op-face', 2, 'PERSON_BUNDLE', ?, '{}', '{}', 1)",
                ("f" * 64,),
            )
        with pytest.raises(Exception):
            # A person bundle keeps the P3-03 revision floor.
            connection.execute(
                "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
                "entity_id, before_value_json, after_value_json, after_revision) "
                "VALUES ('op-face', 3, 'PERSON_BUNDLE', 7, '{}', '{}', 0)"
            )
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
                "target_person_id, state, operation_revision, created_at, committed_at) "
                "VALUES ('op-bad', 'req-bad', ?, 'SPLIT', 7, 'COMMITTED', 10, 1, 1)",
                ("2" * 64,),
            )
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
                "entity_id, before_value_json, after_value_json, after_revision) "
                "VALUES ('op-bad', 4, 'PERSON_BUNDLE', 0, '{}', '{}', 1)"
            )
        connection.execute("ROLLBACK")
    finally:
        connection.close()


def test_an_interrupted_0012_rolls_back_completely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v11(tmp_path, monkeypatch)
    before = snapshot(path)
    before_items = item_rows(path)
    directory = migrations_upto(tmp_path, V12)
    broken = directory / "0012_face_correction_operations.sql"
    broken.write_text(
        broken.read_text(encoding="utf-8") + "\nSELECT * FROM table_that_does_not_exist;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V12)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", directory)

    with pytest.raises(CatalogError) as error:
        migrate_database(path, data_root=data_root(tmp_path))
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)

    assert error.value.code == MIGRATION_FAILED
    assert inspect_database(path) == 11
    assert snapshot(path) == before
    assert item_rows(path) == before_items
    connection = open_readonly(path)
    try:
        # The parked backup tables of the failed rebuild are gone with the transaction.
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
