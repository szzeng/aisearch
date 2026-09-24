"""P4-00B: the ``0015`` step of the migration chain on a real populated v14 database.

0015 is additive - one derived table plus one library-level rule-version key - and it
backfills nothing, because the extraction is its own idempotent, resumable task. This file
is the evidence that the step is continuous, backed up, lossless for every pre-existing row
and completely rolled back when it fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from p4_time_fixtures import (
    REAL_MIGRATIONS_DIR,
    SUPPORTED_SCHEMA_VERSION,
    build_v14_catalog,
    migrations_upto,
    snapshot_tables,
    v14_data_root,
)

from photo_search.catalog import schema
from photo_search.catalog.errors import (
    MIGRATION_FAILED,
    SCHEMA_INCONSISTENT,
    SCHEMA_TOO_NEW,
    CatalogError,
)
from photo_search.catalog.migrate import inspect_database, migrate_database
from photo_search.catalog.sqlite_io import connect_rw, open_readonly
from photo_search.taken_time import contract

SUPPORTED = SUPPORTED_SCHEMA_VERSION
#: This file is about the 0015 step itself, so the target is pinned.
V15 = 15
V14 = 14


def data_root(tmp_path: Path) -> Path:
    return v14_data_root(tmp_path)


def db_path(tmp_path: Path) -> Path:
    return v14_data_root(tmp_path) / "catalog" / "catalog.db"


def build_v14(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real v14 catalog with assets, positions, a revision and an operation log row."""
    return build_v14_catalog(tmp_path, monkeypatch)


PRESERVED_TABLES = (
    "assets",
    "file_locations",
    "source_roots",
    "catalog_filter_revision",
    "catalog_filter_revision_events",
    "persons",
    "person_operations",
    "person_operation_items",
)


def snapshot(path: Path) -> dict[str, list[tuple]]:
    return snapshot_tables(path, PRESERVED_TABLES)


def test_the_migration_chain_is_continuous_and_0015_is_the_time_layer() -> None:
    versions = [number for number, _ in schema.migration_files()]
    assert versions == list(range(1, SUPPORTED + 1))
    assert versions[14] == 15
    # P4-02E raised the moving tip with an index-only additive (0016). This probe is about the
    # 0015 step itself, so it only requires that the chain reaches 0015 and continues from it.
    assert SUPPORTED >= V15
    names = sorted(
        path.name for path in REAL_MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")
    )
    assert names[14] == "0015_taken_time.sql"
    assert names[:14] == [
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
        "0013_person_split_operations.sql",
        "0014_face_redetection_mapping.sql",
    ]


def test_v14_to_v15_is_continuous_backed_up_and_lossless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v14(tmp_path, monkeypatch)
    assert inspect_database(path) == V14
    before = snapshot(path)

    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V15)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, V15))
    result = migrate_database(path, data_root=data_root(tmp_path))
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)

    assert result.previous_version == V14 and result.schema_version == V15
    assert result.applied == (15,)
    assert result.backup_path is not None
    backup = open_readonly(result.backup_path)
    try:
        assert schema.read_schema_version(backup) == V14
        assert not any(
            row[0] == "asset_metadata"
            for row in backup.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        )
    finally:
        backup.close()

    live = open_readonly(path)
    try:
        assert schema.read_schema_version(live) == V15
        schema.verify_schema(live, V15)
        tables = set(schema.business_tables(live))
        assert "asset_metadata" in tables
        assert list(live.execute("PRAGMA foreign_key_check")) == []
        # One rule-version key, and no backfilled row anywhere: the extraction is a task.
        version_key = live.execute(
            "SELECT value FROM metadata WHERE key = ?", (contract.RULES_VERSION_KEY,)
        ).fetchone()[0]
        assert int(str(version_key)) == contract.RULES_VERSION
        assert live.execute("SELECT COUNT(*) FROM asset_metadata").fetchone()[0] == 0
        columns = [
            (str(row[1]), str(row[2]), int(row[3]))
            for row in live.execute("PRAGMA table_info(asset_metadata)")
        ]
        assert [name for name, _type, _notnull in columns] == [
            "asset_id",
            "taken_at_raw",
            "taken_at_sort",
            "time_source",
            "time_confidence",
            "timezone_offset",
            "camera",
        ]
        indexes = {
            str(row[0])
            for row in live.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = "
                "'asset_metadata'"
            )
        }
        assert "idx_asset_metadata_capture" in indexes
    finally:
        live.close()

    assert snapshot(path) == before
    # The chain continues from 15 to whatever this build supports, then settles.
    again = migrate_database(path, data_root=data_root(tmp_path))
    assert again.previous_version == V15
    assert again.schema_version == SUPPORTED
    assert again.applied == tuple(range(V15 + 1, SUPPORTED + 1))
    settled = migrate_database(path, data_root=data_root(tmp_path))
    assert settled.applied == () and settled.previous_version == SUPPORTED


def test_the_table_enforces_the_frozen_closed_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v14(tmp_path, monkeypatch)
    migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO asset_metadata(asset_id, taken_at_raw, taken_at_sort, time_source, "
            "time_confidence, timezone_offset, camera) "
            "VALUES (1, '2022:07:05 18:31:07', ?, 'EXIF_ORIGINAL', 'HIGH', 480, "
            "'Canon EOS R6')",
            (contract.civil_key(2022, 7, 5, 18, 31, 7),),
        )
        connection.execute(
            "INSERT INTO asset_metadata(asset_id, taken_at_raw, taken_at_sort, time_source, "
            "time_confidence, timezone_offset, camera) "
            "VALUES (2, '2022-07-05T18:31:07.000000+00:00', NULL, 'FILE_MTIME', 'LOW', NULL, "
            "NULL)"
        )
        for statement, parameters in (
            # An unknown source or confidence is outside the closed vocabulary.
            (
                "INSERT INTO asset_metadata(asset_id, taken_at_raw, taken_at_sort, "
                "time_source, time_confidence) VALUES (1, 'x', 1, 'EXIF_GPS', 'HIGH')",
                (),
            ),
            (
                "INSERT INTO asset_metadata(asset_id, taken_at_raw, taken_at_sort, "
                "time_source, time_confidence) VALUES (1, 'x', 1, 'EXIF_ROOT', 'HIGH')",
                (),
            ),
            # An EXIF family row must carry a civil key; a file time must not.
            (
                "INSERT INTO asset_metadata(asset_id, taken_at_raw, taken_at_sort, "
                "time_source, time_confidence) VALUES (1, 'x', NULL, 'EXIF_ROOT', 'LOW')",
                (),
            ),
            (
                "INSERT INTO asset_metadata(asset_id, taken_at_raw, taken_at_sort, "
                "time_source, time_confidence) VALUES (1, 'x', 1, 'FILE_MTIME', 'LOW')",
                (),
            ),
            # A file time never claims a timezone, and an offset stays inside -720..840.
            (
                "INSERT INTO asset_metadata(asset_id, taken_at_raw, taken_at_sort, "
                "time_source, time_confidence, timezone_offset) "
                "VALUES (1, 'x', NULL, 'FILE_MTIME', 'LOW', 0)",
                (),
            ),
            (
                "INSERT INTO asset_metadata(asset_id, taken_at_raw, taken_at_sort, "
                "time_source, time_confidence, timezone_offset) "
                "VALUES (1, 'x', 1, 'EXIF_ROOT', 'LOW', 900)",
                (),
            ),
            # The civil key is non-negative and inside the frozen range.
            (
                "INSERT INTO asset_metadata(asset_id, taken_at_raw, taken_at_sort, "
                "time_source, time_confidence) VALUES (1, 'x', -1, 'EXIF_ROOT', 'LOW')",
                (),
            ),
            (
                "INSERT INTO asset_metadata(asset_id, taken_at_raw, taken_at_sort, "
                "time_source, time_confidence) VALUES (1, 'x', ?, 'EXIF_ROOT', 'LOW')",
                (contract.MAX_SORT_KEY + 1,),
            ),
            # The camera is 1..128 characters or NULL.
            (
                "INSERT INTO asset_metadata(asset_id, taken_at_raw, taken_at_sort, "
                "time_source, time_confidence, camera) "
                "VALUES (1, 'x', 1, 'EXIF_ROOT', 'LOW', '')",
                (),
            ),
            (
                "INSERT INTO asset_metadata(asset_id, taken_at_raw, taken_at_sort, "
                "time_source, time_confidence, camera) "
                "VALUES (1, 'x', 1, 'EXIF_ROOT', 'LOW', ?)",
                ("x" * 129,),
            ),
        ):
            with pytest.raises(Exception):
                connection.execute(statement, parameters)
        # A row for an asset that does not exist is refused by the real foreign key.
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO asset_metadata(asset_id, taken_at_raw, taken_at_sort, "
                "time_source, time_confidence) VALUES (99, 'x', 1, 'EXIF_ROOT', 'LOW')"
            )
        connection.execute("ROLLBACK")
    finally:
        connection.close()


def test_an_interrupted_0015_rolls_back_completely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v14(tmp_path, monkeypatch)
    before = snapshot(path)
    directory = migrations_upto(tmp_path, V15)
    broken = directory / "0015_taken_time.sql"
    broken.write_text(
        broken.read_text(encoding="utf-8") + "\nSELECT * FROM table_that_does_not_exist;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V15)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", directory)

    with pytest.raises(CatalogError) as error:
        migrate_database(path, data_root=data_root(tmp_path))
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)

    assert error.value.code == MIGRATION_FAILED
    assert inspect_database(path) == V14
    assert snapshot(path) == before
    connection = open_readonly(path)
    try:
        assert "asset_metadata" not in set(schema.business_tables(connection))
        assert not any(
            name.endswith("_backup") for name in schema.business_tables(connection)
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM metadata WHERE key = ?", (contract.RULES_VERSION_KEY,)
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_an_older_build_refuses_the_newer_catalog_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v14(tmp_path, monkeypatch)
    # Pin the 0015 step: this probe is about a v15 catalog, and P4-02E raised the moving tip
    # to 16 with an index-only additive migration.
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, V15))
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V15)
    migrate_database(path, data_root=data_root(tmp_path))
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)
    assert inspect_database(path) == V15
    before_bytes = path.read_bytes()
    before = snapshot(path)

    # The P4-00A build (which supported 14) must refuse a 15 catalog without writing.
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V14)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, V14))
    with pytest.raises(CatalogError) as too_new:
        inspect_database(path)
    assert too_new.value.code == SCHEMA_TOO_NEW
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)

    assert path.read_bytes() == before_bytes
    assert snapshot(path) == before
    # And a catalog that claims 15 but really lacks the table is inconsistent, not silently
    # "migrated": the object list comes from the migration scripts, never from a counter.
    connection = connect_rw(path)
    connection.execute("DROP TABLE asset_metadata")
    connection.close()
    with pytest.raises(CatalogError) as inconsistent:
        inspect_database(path)
    assert inconsistent.value.code == SCHEMA_INCONSISTENT
    assert "asset_metadata" in inconsistent.value.details["missing_objects"][0]
