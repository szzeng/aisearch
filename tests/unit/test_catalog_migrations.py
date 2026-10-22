"""P1-01: version discovery, atomic migration, backup and WAL (ADR-0002 §7)."""

from __future__ import annotations

import hashlib
import inspect
import shutil
import sqlite3
from pathlib import Path

import pytest

from photo_search.catalog import schema
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import (
    DISK_SPACE_INSUFFICIENT,
    MIGRATION_FAILED,
    SCHEMA_INCONSISTENT,
    SCHEMA_TOO_NEW,
    CatalogError,
)
from photo_search.catalog.migrate import catalog_info, inspect_database, migrate_database
from photo_search.catalog.sqlite_io import connect_rw, open_readonly
from photo_search.ingestion.service import CatalogService

CORE_TABLES = {
    "active_geo_dataset",
    "active_search_profile",
    "assets",
    "asset_geo_facts",
    # P4-00B: the derived taken-time fact of every already-processed asset (ADR-0011 §2), plus
    # the library-level ``taken_time_rules_version`` key inside ``metadata``.
    "asset_metadata",
    "asset_favorites",
    "asset_retention_source_evidence",
    "asset_retention_state",
    "asset_retention_barriers",
    "library_retention_settings",
    "library_retention_runs",
    "library_retention_run_items",
    "library_retention_run_candidates",
    "cr2_job_recoveries",
    # P3-01: the current OCR document of every asset and its per-region metadata.
    "asset_ocr_regions",
    "asset_ocr_text",
    "asset_place_assignment_history",
    "asset_place_assignments",
    "asset_place_exclusions",
    "artifacts",
    "catalog_filter_revision",
    "catalog_filter_revision_events",
    # P4-PLACE-UNIFIED-A (0021): package-proven GeoNames references and the
    # FK carrier bridge for OSM administrative identities.
    "cn_admin_place_refs",
    "geo_dataset_place_refs",
    # P3-02: the face layer. ``faces``/``face_embeddings`` carry the current detection facts
    # and the canonical vectors, ``persons``/``face_person_links`` the unnamed groups with
    # their automatic links and suggestions, and the ``face_index_*``/``face_candidates``
    # family is the independent face candidate index (its own generations, never the
    # asset-keyed image index).
    "face_association_queue",
    "face_candidates",
    # P4-DETAIL source-coordinate and batch artifacts (0019/0020).
    "face_detail_association_queue",
    "face_detail_auto_assets",
    "face_detail_batch_assets",
    "face_detail_batches",
    "face_detail_coordinate_frames",
    "face_embeddings",
    "face_index_entries",
    "face_index_generations",
    "face_index_outbox",
    "face_person_links",
    # P3-08B: the reviewable old/new face mapping of one asset (ADR-0010). The group row is
    # the review unit keyed by the surviving old face, the mapping row one stable candidate
    # pair; both are additive to the frozen 0010/0011 tables.
    "face_redetection_groups",
    "face_redetection_mappings",
    "faces",
    # P4-PERSON-INBOX-HIDE-A: visibility is independent for each detected face.
    "face_inbox_hidden",
    "file_locations",
    # P4-PLACE-MAP-A materialized membership revision (0017).
    "map_member_revision",
    # P4-PLACE-CN validated admin identities, package rows, and source links (0018).
    "cn_admin_ids",
    "cn_admin_resolutions",
    "cn_base_china_places",
    "cn_dataset_regions",
    "cn_legacy_links",
    "cn_update_runs",
    "geo_dataset_files",
    "geo_datasets",
    "jobs",
    "machine_tag_models",
    "machine_tag_results",
    "machine_tag_job_counts",
    "index_entries",
    "index_generations",
    "index_outbox",
    "metadata",
    # P3-03: the person-knowledge layer. ``person_operations``/``person_operation_items`` are
    # the append-only operation log with its bounded before/after bundles, ``person_aliases``
    # the search aliases of a named person, and ``face_assignment_controls``/
    # ``face_exclusions`` the human locks and denials P3-02 already reads.
    # P3-04B (0012) adds no table at all: it rebuilds ``person_operations`` and
    # ``person_operation_items`` in place to widen their closed CHECK vocabulary, so this set
    # is deliberately unchanged by that migration.
    "face_assignment_controls",
    "face_exclusions",
    "person_aliases",
    "person_hidden",
    # P4-PERSON-COLLECTION-A: stable person anchors and collection metadata.
    "person_collections",
    "person_collection_members",
    "person_collection_face_members",
    # P4-PHOTO-COLLECTION-A (0036): ordinary photo collection directory and static membership.
    "photo_collections",
    "photo_collection_members",
    "photo_collection_search_appends",
    # P4-PHOTO-SMART-COLLECTION-A (0037): durable rules, user exclusions,
    # bounded published candidates, idempotency receipts, and publication revisions.
    "photo_smart_collections",
    "photo_smart_exclusions",
    "photo_smart_cache_state",
    "photo_smart_cache_entries",
    "photo_smart_rule_replacements",
    "photo_smart_vector_publish_revisions",
    "person_operation_items",
    "person_operations",
    "person_operation_retention",
    "place_aliases",
    "place_external_refs",
    "place_operation_items",
    "place_operations",
    "place_resolution_candidates",
    "place_resolutions",
    "place_suggestion_revision",
    "places",
    "persons",
    "runtime_capabilities",
    "runtime_qualification_evidence",
    "retention_source_anomalies",
    "scan_runs",
    "scheduler_state",
    "source_roots",
    # P2-04: the derived search layer. ``search_documents_fts`` is an FTS5 virtual table
    # and the ``_data``/``_idx``/``_docsize``/``_config`` rows are the shadow tables SQLite
    # creates for it; they are part of the frozen schema, which is why they are listed.
    "search_documents",
    "search_documents_fts",
    "search_documents_fts_config",
    "search_documents_fts_data",
    "search_documents_fts_docsize",
    "search_documents_fts_idx",
    "search_projection_queue",
    # P2-06A: the immutable search profile and its single active pointer.
    "search_profiles",
    # P2-06B: the append-only explicit-relevance feedback log.
    "search_feedback",
}

#: This build's supported version, and the next one a synthetic migration may use.
SUPPORTED = int(schema.SUPPORTED_SCHEMA_VERSION)
NEXT_VERSION = SUPPORTED + 1


def _data_root(tmp_path: Path) -> Path:
    return tmp_path / "PhotoSearchData"


def _db_path(tmp_path: Path) -> Path:
    return _data_root(tmp_path) / "catalog" / "catalog.db"


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _create_historical_catalog(path: Path, version: int) -> None:
    """Build the actual migration prefix, without future objects in an old fixture."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        for number, script in schema.migration_files():
            if number > version:
                break
            connection.executescript(script.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?)",
            (schema.SCHEMA_VERSION_KEY, str(version)),
        )
        connection.commit()
        schema.verify_schema(connection, version)
    finally:
        connection.close()


def _copy_migration_prefix(target: Path, version: int) -> None:
    """Pin a historical build's real script prefix without assuming today's last version."""
    target.mkdir()
    source = Path(__file__).resolve().parents[2] / "src/photo_search/catalog/migrations"
    for path in source.glob("[0-9][0-9][0-9][0-9]_*.sql"):
        if int(path.name.split("_", 1)[0]) <= version:
            shutil.copy2(path, target / path.name)


class _Usage:
    def __init__(self, free: int) -> None:
        self.total = 10**12
        self.used = 0
        self.free = free


def test_fresh_database_migrates_to_the_supported_version(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)

    result = migrate_database(db_path, data_root=_data_root(tmp_path))

    assert result.previous_version == 0
    assert result.schema_version == schema.SUPPORTED_SCHEMA_VERSION
    assert result.applied == tuple(range(1, SUPPORTED + 1))
    assert result.backup_path is None
    assert inspect_database(db_path) == SUPPORTED
    connection = open_readonly(db_path)
    try:
        assert set(schema.business_tables(connection)) == CORE_TABLES
        barrier_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(asset_retention_barriers)")
        }
        assert {"media_sha256", "face_crop_keys_json", "media_cleaned_at_ms"} <= barrier_columns
        schema.verify_schema(connection, SUPPORTED)
    finally:
        connection.close()
    writable = connect_rw(db_path)
    try:
        assert str(writable.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
        assert str(writable.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower() == "wal"
    finally:
        writable.close()
    assert sorted(path.name for path in db_path.parent.iterdir()) == ["catalog.db"]


def test_repeated_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)
    migrate_database(db_path, data_root=_data_root(tmp_path))
    before = _file_digest(db_path)

    second = migrate_database(db_path, data_root=_data_root(tmp_path))

    assert second.previous_version == SUPPORTED
    assert second.applied == ()
    assert second.backup_path is None
    assert _file_digest(db_path) == before


def test_face_inbox_ignore_state_migration_preserves_legacy_rows_as_temporary() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE face_inbox_hidden(face_id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO face_inbox_hidden(face_id) VALUES ('legacy-face')")
        connection.executescript(
            (schema.MIGRATIONS_DIR / "0031_face_inbox_ignore_state.sql").read_text(encoding="utf-8")
        )
        row = connection.execute(
            "SELECT face_id, ignore_state FROM face_inbox_hidden"
        ).fetchone()
        assert row == ("legacy-face", "TEMPORARY")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE face_inbox_hidden SET ignore_state='INVALID' WHERE face_id='legacy-face'"
            )
    finally:
        connection.close()


def test_schema_31_upgrade_adds_empty_face_members_without_changing_existing_facts(tmp_path: Path, monkeypatch) -> None:
    source_migrations = schema.MIGRATIONS_DIR
    old_migrations = tmp_path / "migrations-v31"
    old_migrations.mkdir()
    for migration in source_migrations.glob("[0-9][0-9][0-9][0-9]_*.sql"):
        if int(migration.name[:4]) <= 31:
            shutil.copy2(migration, old_migrations / migration.name)
    db_path = _db_path(tmp_path)
    with monkeypatch.context() as old:
        old.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 31)
        old.setattr(schema, "MIGRATIONS_DIR", old_migrations)
        migrate_database(db_path, data_root=_data_root(tmp_path))
        connection = connect_rw(db_path)
        try:
            person = int(connection.execute(
                "INSERT INTO persons(name,name_key,avatar_face_id,merged_into,revision,created_at,updated_at) "
                "VALUES ('保留人物','保留人物',NULL,NULL,1,1,1)"
            ).lastrowid)
            collection = int(connection.execute(
                "INSERT INTO person_collections(name,name_key,revision,created_at,updated_at,instance_token) "
                "VALUES ('既有合集','nfc1:既有合集',7,10,20,?)",
                ("a" * 32,),
            ).lastrowid)
            connection.execute(
                "INSERT INTO person_collection_members(collection_id,person_id) VALUES (?,?)", (collection, person)
            )
            connection.execute("INSERT INTO person_hidden(person_id,ignore_state) VALUES (?,'EXCLUDED')", (person,))
            connection.commit()
            person_rows = [tuple(row) for row in connection.execute("SELECT * FROM persons")]
            person_members = [tuple(row) for row in connection.execute("SELECT * FROM person_collection_members")]
            person_hidden = [tuple(row) for row in connection.execute("SELECT * FROM person_hidden")]
        finally:
            connection.close()

    with monkeypatch.context() as current:
        current.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)
        current.setattr(schema, "MIGRATIONS_DIR", source_migrations)
        broken_migrations = tmp_path / "migrations-v32-broken"
        shutil.copytree(source_migrations, broken_migrations)
        face_migration = broken_migrations / "0032_person_collection_face_members.sql"
        face_migration.write_text(face_migration.read_text(encoding="utf-8") + "\nSELECT missing_fault_column;\n", encoding="utf-8")
        current.setattr(schema, "MIGRATIONS_DIR", broken_migrations)
        with pytest.raises(CatalogError) as failed:
            migrate_database(db_path, data_root=_data_root(tmp_path))
        assert failed.value.code == MIGRATION_FAILED
        assert inspect_database(db_path) == 31
        after_rollback = open_readonly(db_path)
        try:
            assert after_rollback.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='person_collection_face_members'"
            ).fetchone() is None
        finally:
            after_rollback.close()

        current.setattr(schema, "MIGRATIONS_DIR", source_migrations)
        result = migrate_database(db_path, data_root=_data_root(tmp_path))
    assert result.previous_version == 31
    assert result.applied == tuple(range(32, SUPPORTED + 1))
    assert result.backup_path is not None
    repeated = migrate_database(db_path, data_root=_data_root(tmp_path))
    assert repeated.schema_version == SUPPORTED
    assert repeated.applied == ()
    connection = open_readonly(db_path)
    try:
        schema.verify_schema(connection, SUPPORTED)
        assert [tuple(row) for row in connection.execute("SELECT * FROM persons")] == person_rows
        assert [tuple(row) for row in connection.execute("SELECT * FROM person_collection_members")] == person_members
        assert [tuple(row) for row in connection.execute("SELECT * FROM person_hidden")] == person_hidden
        assert connection.execute("SELECT COUNT(*) FROM person_collection_face_members").fetchone()[0] == 0
        foreign_keys = [tuple(row) for row in connection.execute("PRAGMA foreign_key_list(person_collection_face_members)")]
        assert len(foreign_keys) == 1
        assert foreign_keys[0][2] == "person_collections"
        assert list(connection.execute("PRAGMA foreign_key_check")) == []
    finally:
        connection.close()
    restore_root = tmp_path / "isolated-restore"
    restore_root.mkdir()
    restored_path = restore_root / "catalog.sqlite3"
    shutil.copy2(result.backup_path, restored_path)
    restored = open_readonly(restored_path)
    try:
        assert schema.read_schema_version(restored) == 31
        assert [tuple(row) for row in restored.execute("SELECT * FROM persons")] == person_rows
        assert [tuple(row) for row in restored.execute("SELECT * FROM person_collection_members")] == person_members
        assert [tuple(row) for row in restored.execute("SELECT * FROM person_hidden")] == person_hidden
        assert "person_collection_face_members" not in schema.business_tables(restored)
    finally:
        restored.close()


def test_schema_21_people_remain_visible_after_visibility_migration(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)
    _create_historical_catalog(db_path, 21)
    connection = connect_rw(db_path)
    try:
        connection.execute(
            "INSERT INTO persons(name, name_key, avatar_face_id, merged_into, revision, "
            "created_at, updated_at) VALUES (NULL, NULL, NULL, NULL, 1, 1, 1)"
        )
        connection.commit()
    finally:
        connection.close()

    result = migrate_database(db_path, data_root=_data_root(tmp_path))

    assert result.previous_version == 21
    assert result.schema_version == SUPPORTED
    assert result.applied == tuple(range(22, SUPPORTED + 1))
    connection = open_readonly(db_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM person_hidden").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM person_collections").fetchone()[0] == 0
        schema.verify_schema(connection, SUPPORTED)
    finally:
        connection.close()


def test_schema_24_upgrade_protects_stale_instance_when_deleted_id_is_reused(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)
    _create_historical_catalog(db_path, 24)
    connection = connect_rw(db_path)
    try:
        person_id = int(connection.execute(
            "INSERT INTO persons(name, name_key, avatar_face_id, merged_into, revision, "
            "created_at, updated_at) VALUES (NULL, NULL, NULL, NULL, 1, 1, 1)"
        ).lastrowid)
        collection_id = 41
        connection.execute(
            "INSERT INTO person_collections(collection_id, name, name_key, revision, created_at, updated_at) "
            "VALUES (?, '保留合集', 'nfc1:保留合集', 7, 10, 20)", (collection_id,)
        )
        deleted_id = collection_id + 1
        connection.execute(
            "INSERT INTO person_collections(collection_id, name, name_key, revision, created_at, updated_at) "
            "VALUES (?, '升级前已删除', 'nfc1:升级前已删除', 1, 10, 10)", (deleted_id,)
        )
        connection.execute("DELETE FROM person_collections WHERE collection_id = ?", (deleted_id,))
        connection.execute(
            "INSERT INTO person_collection_members(collection_id, person_id) VALUES (?, ?)",
            (collection_id, person_id),
        )
        connection.commit()
    finally:
        connection.close()

    result = migrate_database(db_path, data_root=_data_root(tmp_path))
    assert result.previous_version == 24
    assert result.schema_version == SUPPORTED
    assert result.applied == tuple(range(25, SUPPORTED + 1))
    assert result.backup_path is not None
    backup = open_readonly(result.backup_path)
    try:
        schema.verify_schema(backup, 24)
        assert backup.execute(
            "SELECT person_id FROM person_collection_members WHERE collection_id = ?",
            (collection_id,),
        ).fetchone()["person_id"] == person_id
    finally:
        backup.close()

    catalog = Catalog.open(db_path, data_root=_data_root(tmp_path))
    try:
        assert catalog.schema_version == SUPPORTED
        row = catalog.read_one(
            "SELECT name, revision, created_at, updated_at FROM person_collections WHERE collection_id = ?",
            (collection_id,),
        )
        assert tuple(row) == ("保留合集", 7, 10, 20)
        assert catalog.read_one(
            "SELECT person_id FROM person_collection_members WHERE collection_id = ?",
            (collection_id,),
        )["person_id"] == person_id
        from photo_search.persons import collections

        collection_token = str(catalog.read_one(
            "SELECT instance_token FROM person_collections WHERE collection_id = ?", (collection_id,)
        )["instance_token"])
        collections.delete_collection(catalog, collection_id, instance_token=collection_token)
        replacement_record = collections.create_collection(catalog, "迁移后新合集")["collection"]
        replacement = replacement_record["collection_id"]
        # AUTOINCREMENT cannot recover a v24 deleted high-water ID. The durable instance
        # token protects stale operations and cursors even when that integer is reused.
        assert replacement == deleted_id
        assert len(replacement_record["instance_token"]) == 32
        from photo_search.api.errors import ApiError
        from photo_search.persons.collections import _encode, people_page
        from photo_search.places.revision import read_filter_revision

        stale_v24_cursor = _encode(
            "pcp1", [deleted_id, 1, read_filter_revision(catalog.connection)], 1
        )
        with pytest.raises(ApiError) as stale_cursor_error:
            people_page(
                catalog, replacement, instance_token=replacement_record["instance_token"],
                cursor=stale_v24_cursor,
            )
        assert stale_cursor_error.value.status == 409
        with pytest.raises(ApiError) as stale_instance_error:
            collections.delete_collection(catalog, replacement, instance_token="")
        assert stale_instance_error.value.status == 404
        assert catalog.read_one(
            "SELECT collection_id FROM person_collections WHERE collection_id = ?", (replacement,)
        ) is not None
        schema.verify_schema(catalog.connection, SUPPORTED)
        assert catalog.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        catalog.close()


def test_versioned_database_with_missing_core_object_is_inconsistent(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)
    migrate_database(db_path, data_root=_data_root(tmp_path))
    connection = connect_rw(db_path)
    connection.execute("DROP INDEX idx_file_locations_asset")
    connection.close()

    with pytest.raises(CatalogError) as error:
        inspect_database(db_path)

    assert error.value.code == SCHEMA_INCONSISTENT
    assert "idx_file_locations_asset" in error.value.details["missing_objects"][0]


def test_tables_without_a_usable_version_are_inconsistent(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE legacy_photos (photo_id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()

    with pytest.raises(CatalogError) as error:
        inspect_database(db_path)

    assert error.value.code == SCHEMA_INCONSISTENT
    with pytest.raises(CatalogError) as open_error:
        migrate_database(db_path, data_root=_data_root(tmp_path))
    assert open_error.value.code == SCHEMA_INCONSISTENT


def test_too_new_schema_is_refused_before_any_write_side_effect(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)
    migrate_database(db_path, data_root=_data_root(tmp_path))
    connection = connect_rw(db_path)
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "UPDATE metadata SET value = ? WHERE key = 'schema_version'", (str(NEXT_VERSION),)
    )
    connection.execute("COMMIT")
    connection.close()
    before_bytes = db_path.read_bytes()
    before_names = sorted(path.name for path in db_path.parent.iterdir())

    with pytest.raises(CatalogError) as error:
        inspect_database(db_path)
    assert error.value.code == SCHEMA_TOO_NEW
    assert error.value.details == {
        "schema_version": NEXT_VERSION,
        "supported_schema_version": SUPPORTED,
    }

    with pytest.raises(CatalogError) as migrate_error:
        migrate_database(db_path, data_root=_data_root(tmp_path))
    assert migrate_error.value.code == SCHEMA_TOO_NEW

    with pytest.raises(CatalogError) as open_error:
        Catalog.open(db_path, data_root=_data_root(tmp_path))
    assert open_error.value.code == SCHEMA_TOO_NEW

    assert db_path.read_bytes() == before_bytes
    assert sorted(path.name for path in db_path.parent.iterdir()) == before_names
    assert before_names == ["catalog.db"]
    info = catalog_info(db_path, data_root=_data_root(tmp_path))
    assert info["status"] == "schema_too_new"
    assert info["writable"] is False
    assert info["schema_version"] is None


def _fake_migrations(tmp_path: Path, extra: dict[str, str]) -> Path:
    directory = tmp_path / "migrations"
    directory.mkdir(parents=True, exist_ok=True)
    for version, path in schema.migration_files():
        (directory / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    for name, text in extra.items():
        (directory / name).write_text(text, encoding="utf-8")
    return directory


def test_failed_migration_rolls_back_and_keeps_the_old_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _db_path(tmp_path)
    migrate_database(db_path, data_root=_data_root(tmp_path))
    directory = _fake_migrations(
        tmp_path,
        {
            f"{NEXT_VERSION:04d}_broken.sql": (
                "CREATE TABLE probe_half (probe_id INTEGER PRIMARY KEY);\n"
                "INSERT INTO metadata(key, value) VALUES('probe', 'yes');\n"
                "SELECT * FROM table_that_does_not_exist;\n"
            )
        },
    )
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", directory)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", NEXT_VERSION)

    with pytest.raises(CatalogError) as error:
        migrate_database(db_path, data_root=_data_root(tmp_path))

    assert error.value.code == MIGRATION_FAILED
    assert inspect_database(db_path) == SUPPORTED
    connection = open_readonly(db_path)
    try:
        assert "probe_half" not in schema.business_tables(connection)
        assert connection.execute("SELECT COUNT(*) FROM metadata WHERE key = 'probe'").fetchone()[0] == 0
    finally:
        connection.close()


def test_photo_collection_0036_failure_rolls_back_and_old_build_refuses_new_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _db_path(tmp_path)
    _create_historical_catalog(db_path, 35)
    broken_migrations = tmp_path / "migrations-0036-broken"
    shutil.copytree(schema.MIGRATIONS_DIR, broken_migrations)
    migration = broken_migrations / "0036_photo_collections.sql"
    migration.write_text(
        migration.read_text(encoding="utf-8") + "\nSELECT missing_photo_collection_fault;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", broken_migrations)
    with pytest.raises(CatalogError) as failed:
        migrate_database(db_path, data_root=_data_root(tmp_path))
    assert failed.value.code == MIGRATION_FAILED
    assert inspect_database(db_path) == 35
    rolled_back = open_readonly(db_path)
    try:
        tables = set(schema.business_tables(rolled_back))
        assert not {"photo_collections", "photo_collection_members", "photo_collection_search_appends"} & tables
        assert rolled_back.execute(
            "SELECT COUNT(*) FROM metadata WHERE key='photo_collections_revision'"
        ).fetchone()[0] == 0
        assert list(rolled_back.execute("PRAGMA foreign_key_check")) == []
    finally:
        rolled_back.close()

    monkeypatch.setattr(schema, "MIGRATIONS_DIR", Path(__file__).resolve().parents[2] /
                        "src/photo_search/catalog/migrations")
    migrated = migrate_database(db_path, data_root=_data_root(tmp_path))
    assert migrated.previous_version == 35
    assert migrated.applied == tuple(range(36, SUPPORTED + 1))
    assert migrated.backup_path is not None
    old_build_backup = open_readonly(migrated.backup_path)
    try:
        assert schema.read_schema_version(old_build_backup) == 35
        assert "photo_collections" not in schema.business_tables(old_build_backup)
        assert "photo_smart_collections" not in schema.business_tables(old_build_backup)
    finally:
        old_build_backup.close()

    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 35)
    with pytest.raises(CatalogError) as refused:
        inspect_database(db_path)
    assert refused.value.code == SCHEMA_TOO_NEW


def test_retention_0038_migration_failure_rolls_back_without_losing_human_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _db_path(tmp_path)
    _create_historical_catalog(db_path, 37)
    connection = connect_rw(db_path)
    try:
        connection.execute(
            "INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) VALUES(1,?,1,1,'ACTIVE')",
            ("a" * 64,),
        )
        connection.execute("INSERT INTO asset_favorites(asset_id,added_at_ms) VALUES(1,10)")
    finally:
        connection.close()
    broken_migrations = tmp_path / "migrations-0038-broken"
    shutil.copytree(schema.MIGRATIONS_DIR, broken_migrations)
    migration = broken_migrations / "0038_library_retention_evidence.sql"
    migration.write_text(
        migration.read_text(encoding="utf-8") + "\nSELECT retention_migration_fault;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", broken_migrations)
    with pytest.raises(CatalogError) as failed:
        migrate_database(db_path, data_root=_data_root(tmp_path))
    assert failed.value.code == MIGRATION_FAILED
    assert inspect_database(db_path) == 37
    rolled_back = open_readonly(db_path)
    try:
        tables = set(schema.business_tables(rolled_back))
        assert not {
            "asset_retention_source_evidence", "asset_retention_state", "retention_source_anomalies"
        } & tables
        assert rolled_back.execute("SELECT COUNT(*) FROM asset_favorites WHERE asset_id=1").fetchone()[0] == 1
        assert rolled_back.execute("SELECT COUNT(*) FROM assets WHERE asset_id=1").fetchone()[0] == 1
        assert list(rolled_back.execute("PRAGMA foreign_key_check")) == []
    finally:
        rolled_back.close()


def test_retention_0039_barrier_migration_failure_rolls_back_and_old_upgrade_preserves_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _db_path(tmp_path)
    _create_historical_catalog(db_path, 38)
    connection = connect_rw(db_path)
    try:
        connection.execute(
            "INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) VALUES(7,?,1,1,'ACTIVE')",
            ("b" * 64,),
        )
        connection.execute("INSERT INTO asset_favorites(asset_id,added_at_ms) VALUES(7,10)")
    finally:
        connection.close()

    broken_migrations = tmp_path / "migrations-0039-broken"
    shutil.copytree(schema.MIGRATIONS_DIR, broken_migrations)
    migration = broken_migrations / "0039_library_retention_barrier.sql"
    migration.write_text(
        migration.read_text(encoding="utf-8") + "\nSELECT retention_barrier_migration_fault;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", broken_migrations)
    with pytest.raises(CatalogError) as failed:
        migrate_database(db_path, data_root=_data_root(tmp_path))
    assert failed.value.code == MIGRATION_FAILED
    assert inspect_database(db_path) == 38
    rolled_back = open_readonly(db_path)
    try:
        assert "asset_retention_barriers" not in schema.business_tables(rolled_back)
        assert rolled_back.execute("SELECT COUNT(*) FROM asset_favorites WHERE asset_id=7").fetchone()[0] == 1
        assert rolled_back.execute("SELECT COUNT(*) FROM assets WHERE asset_id=7").fetchone()[0] == 1
        assert list(rolled_back.execute("PRAGMA foreign_key_check")) == []
    finally:
        rolled_back.close()

    monkeypatch.setattr(
        schema,
        "MIGRATIONS_DIR",
        Path(__file__).resolve().parents[2] / "src/photo_search/catalog/migrations",
    )
    upgraded = migrate_database(db_path, data_root=_data_root(tmp_path))
    assert upgraded.previous_version == 38
    assert upgraded.applied == tuple(range(39, SUPPORTED + 1))
    assert upgraded.backup_path is not None
    backup = open_readonly(upgraded.backup_path)
    try:
        assert schema.read_schema_version(backup) == 38
        assert "asset_retention_barriers" not in schema.business_tables(backup)
        assert backup.execute("SELECT COUNT(*) FROM asset_favorites WHERE asset_id=7").fetchone()[0] == 1
    finally:
        backup.close()
    live = open_readonly(db_path)
    try:
        assert schema.read_schema_version(live) == SUPPORTED
        assert "asset_retention_barriers" in schema.business_tables(live)
        assert live.execute("SELECT COUNT(*) FROM asset_favorites WHERE asset_id=7").fetchone()[0] == 1
    finally:
        live.close()


def test_retention_0040_media_identity_migration_new_upgrade_and_failure_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _db_path(tmp_path)
    _create_historical_catalog(db_path, 39)
    connection = connect_rw(db_path)
    try:
        connection.execute(
            "INSERT INTO asset_retention_barriers(asset_id,barrier_id,state,created_at_ms,updated_at_ms) "
            "VALUES(7,?,'COMPLETE',10,10)",
            ("7" * 32,),
        )
        connection.execute(
            "INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) VALUES(7,?,1,1,'ACTIVE')",
            ("c" * 64,),
        )
        connection.execute("INSERT INTO asset_favorites(asset_id,added_at_ms) VALUES(7,10)")
    finally:
        connection.close()

    broken_migrations = tmp_path / "migrations-0040-broken"
    shutil.copytree(schema.MIGRATIONS_DIR, broken_migrations)
    migration = broken_migrations / "0040_retention_media_identity.sql"
    migration.write_text(
        migration.read_text(encoding="utf-8") + "\nSELECT retention_media_identity_migration_fault;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", broken_migrations)
    with pytest.raises(CatalogError) as failed:
        migrate_database(db_path, data_root=_data_root(tmp_path))
    assert failed.value.code == MIGRATION_FAILED
    assert inspect_database(db_path) == 39
    rolled_back = open_readonly(db_path)
    try:
        columns = {str(row[1]) for row in rolled_back.execute("PRAGMA table_info(asset_retention_barriers)")}
        assert "media_sha256" not in columns
        assert rolled_back.execute("SELECT COUNT(*) FROM asset_favorites WHERE asset_id=7").fetchone()[0] == 1
        assert list(rolled_back.execute("PRAGMA foreign_key_check")) == []
    finally:
        rolled_back.close()

    monkeypatch.setattr(
        schema,
        "MIGRATIONS_DIR",
        Path(__file__).resolve().parents[2] / "src/photo_search/catalog/migrations",
    )
    upgraded = migrate_database(db_path, data_root=_data_root(tmp_path))
    assert upgraded.previous_version == 39
    assert upgraded.applied == tuple(range(40, SUPPORTED + 1))
    assert upgraded.backup_path is not None
    backup = open_readonly(upgraded.backup_path)
    try:
        assert schema.read_schema_version(backup) == 39
        old_columns = {str(row[1]) for row in backup.execute("PRAGMA table_info(asset_retention_barriers)")}
        assert "media_sha256" not in old_columns
    finally:
        backup.close()
    live = open_readonly(db_path)
    try:
        assert schema.read_schema_version(live) == SUPPORTED
        columns = {str(row[1]) for row in live.execute("PRAGMA table_info(asset_retention_barriers)")}
        assert {"media_sha256", "face_crop_keys_json", "media_cleaned_at_ms"} <= columns
        row = live.execute(
            "SELECT media_sha256,face_crop_keys_json,media_cleaned_at_ms FROM asset_retention_barriers WHERE asset_id=7"
        ).fetchone()
        assert tuple(row) == (None, "[]", None)
        assert live.execute("SELECT COUNT(*) FROM asset_favorites WHERE asset_id=7").fetchone()[0] == 1
        assert list(live.execute("PRAGMA foreign_key_check")) == []
    finally:
        live.close()


def test_retention_0041_progress_migration_upgrade_and_failed_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _db_path(tmp_path)
    _create_historical_catalog(db_path, 40)
    connection = connect_rw(db_path)
    try:
        connection.execute(
            "INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) VALUES(7,?,1,1,'ACTIVE')",
            ("d" * 64,),
        )
        connection.execute("INSERT INTO asset_favorites(asset_id,added_at_ms) VALUES(7,10)")
    finally:
        connection.close()

    broken = tmp_path / "migrations-0041-broken"
    _copy_migration_prefix(broken, 41)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 41)
    migration = broken / "0041_retention_cleanup_runs.sql"
    migration.write_text(
        migration.read_text(encoding="utf-8") + "\nSELECT retention_runs_migration_fault;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", broken)
    with pytest.raises(CatalogError) as failed:
        migrate_database(db_path, data_root=_data_root(tmp_path))
    assert failed.value.code == MIGRATION_FAILED
    assert inspect_database(db_path) == 40
    rolled_back = open_readonly(db_path)
    try:
        tables = set(schema.business_tables(rolled_back))
        assert not {"library_retention_settings", "library_retention_runs", "library_retention_run_items"} & tables
        assert rolled_back.execute("SELECT COUNT(*) FROM asset_favorites WHERE asset_id=7").fetchone()[0] == 1
        assert list(rolled_back.execute("PRAGMA foreign_key_check")) == []
    finally:
        rolled_back.close()

    migrations_41 = tmp_path / "migrations-through-0041"
    _copy_migration_prefix(migrations_41, 41)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_41)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 41)
    upgraded = migrate_database(db_path, data_root=_data_root(tmp_path))
    assert upgraded.previous_version == 40 and upgraded.applied == (41,)
    live = open_readonly(db_path)
    try:
        assert schema.read_schema_version(live) == 41
        assert live.execute(
            "SELECT automatic_enabled FROM library_retention_settings WHERE singleton=1"
        ).fetchone()[0] == 0
        assert live.execute("SELECT COUNT(*) FROM asset_favorites WHERE asset_id=7").fetchone()[0] == 1
        assert list(live.execute("PRAGMA foreign_key_check")) == []
    finally:
        live.close()


def test_retention_0042_run_fence_migration_upgrade_and_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _db_path(tmp_path)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 41)
    migrations_41 = tmp_path / "migrations-through-0041"
    _copy_migration_prefix(migrations_41, 41)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_41)
    migrate_database(db_path, data_root=_data_root(tmp_path))
    seeded = connect_rw(db_path)
    try:
        seeded.execute(
            "INSERT INTO library_retention_runs(run_id,mode,state,stage,total_assets,completed_assets,"
            "skipped_assets,failed_assets,created_at_ms,updated_at_ms) "
            "VALUES('legacy-active','MANUAL','PARTIAL','旧批次',2,1,0,1,100,200)"
        )
        seeded.executemany(
            "INSERT INTO library_retention_run_items(run_id,asset_id,state,stage) VALUES('legacy-active',?,?,?)",
            [(123, "COMPLETE", "已清理"), (124, "FAILED", "可恢复失败")],
        )
        seeded.commit()
    finally:
        seeded.close()
    broken = tmp_path / "migrations-0042-broken"
    _copy_migration_prefix(broken, 42)
    migration = broken / "0042_retention_cleanup_run_fences.sql"
    migration.write_text(
        migration.read_text(encoding="utf-8") + "\nSELECT retention_0042_fault;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", broken)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 42)
    with pytest.raises(CatalogError) as failed:
        migrate_database(db_path, data_root=_data_root(tmp_path))
    assert failed.value.code == MIGRATION_FAILED
    assert inspect_database(db_path) == 41
    rolled_back = open_readonly(db_path)
    try:
        run_columns = {str(row[1]) for row in rolled_back.execute("PRAGMA table_info(library_retention_runs)")}
        assert "selection_max_asset_id" not in run_columns
        assert list(rolled_back.execute("PRAGMA foreign_key_check")) == []
    finally:
        rolled_back.close()
    migrations_42 = tmp_path / "migrations-through-0042"
    _copy_migration_prefix(migrations_42, 42)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_42)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 42)
    upgraded = migrate_database(db_path, data_root=_data_root(tmp_path))
    assert upgraded.previous_version == 41 and upgraded.applied == (42,)
    live = open_readonly(db_path)
    try:
        assert schema.read_schema_version(live) == 42
        columns = {str(row[1]) for row in live.execute("PRAGMA table_info(library_retention_runs)")}
        assert {"selection_max_asset_id", "selection_cutoff_at_ms", "selection_after_asset_id", "checked_assets"} <= columns
        legacy = live.execute(
            "SELECT selection_max_asset_id,selection_after_asset_id,total_assets,completed_assets,"
            "failed_assets,has_more_candidates FROM library_retention_runs WHERE run_id='legacy-active'"
        ).fetchone()
        assert tuple(legacy) == (124, 0, 0, 0, 0, 1)
        assert [int(row[0]) for row in live.execute(
            "SELECT asset_id FROM library_retention_run_candidates WHERE run_id='legacy-active' ORDER BY asset_id"
        )] == [123, 124]
        assert list(live.execute("PRAGMA foreign_key_check")) == []
    finally:
        live.close()


def test_upgrade_creates_a_consistent_backup_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _db_path(tmp_path)
    migrate_database(db_path, data_root=_data_root(tmp_path))
    directory = _fake_migrations(
        tmp_path,
        {
            f"{NEXT_VERSION:04d}_add_probe.sql": (
                "CREATE TABLE probe_table (probe_id INTEGER PRIMARY KEY);\n"
            )
        },
    )
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", directory)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", NEXT_VERSION)

    result = migrate_database(db_path, data_root=_data_root(tmp_path))

    assert result.previous_version == SUPPORTED
    assert result.schema_version == NEXT_VERSION
    assert result.applied == (NEXT_VERSION,)
    assert result.backup_path is not None
    assert result.backup_path.parent == _data_root(tmp_path) / "backups" / "catalog"
    backup = open_readonly(result.backup_path)
    try:
        assert schema.read_schema_version(backup) == SUPPORTED
        assert "probe_table" not in schema.business_tables(backup)
        assert set(schema.business_tables(backup)) == CORE_TABLES
    finally:
        backup.close()
    live = open_readonly(db_path)
    try:
        assert schema.read_schema_version(live) == NEXT_VERSION
        assert "probe_table" in schema.business_tables(live)
    finally:
        live.close()


def test_insufficient_disk_space_blocks_a_new_database(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)

    with pytest.raises(CatalogError) as error:
        migrate_database(
            db_path,
            data_root=_data_root(tmp_path),
            disk_usage=lambda path: _Usage(free=1024),
        )

    assert error.value.code == DISK_SPACE_INSUFFICIENT
    assert not db_path.exists()


def test_preflight_reads_a_live_wal_database_without_changing_it(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)
    migrate_database(db_path, data_root=_data_root(tmp_path))
    writer = connect_rw(db_path)
    try:
        writer.execute("INSERT INTO metadata (key, value) VALUES ('probe', 'live')")
        assert (db_path.parent / "catalog.db-wal").exists()
        before = db_path.read_bytes()

        info = catalog_info(db_path, data_root=_data_root(tmp_path))
        assert info["status"] == "ok"
        assert info["schema_version"] == SUPPORTED
        assert info["journal_mode"] == "wal"
        assert inspect_database(db_path) == SUPPORTED
        assert db_path.read_bytes() == before
        assert writer.execute("SELECT value FROM metadata WHERE key = 'probe'").fetchone()[0] == "live"
    finally:
        writer.close()


def test_catalog_open_exposes_no_migration_bypass() -> None:
    """ADR-0002 §7.1/§7.4: every business write path starts with the read-only preflight."""
    assert "migrate" not in inspect.signature(Catalog.open).parameters
    assert "migrate" not in inspect.signature(CatalogService.open).parameters


def test_a_build_without_migration_scripts_fails_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frozen build must not silently create an empty catalog (ADR-0002 §7.1 item 7).

    P1-04's packaged smoke test found exactly this: the ``.sql`` migrations were not
    collected into the bundle, ``migration_files()`` returned an empty list, the
    "expected vs actual" comparison became vacuous and the first business query failed with
    ``no such table``. The set must therefore cover 0001..supported.
    """
    empty = tmp_path / "migrations"
    empty.mkdir()
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", empty)

    with pytest.raises(CatalogError) as failure:
        schema.migration_files()
    assert failure.value.code == SCHEMA_INCONSISTENT
    assert failure.value.details["versions"] == []
    assert failure.value.details["expected_versions"] == list(range(1, SUPPORTED + 1))

    fresh = tmp_path / "catalog" / "catalog.db"
    with pytest.raises(CatalogError) as open_failure:
        Catalog.open(fresh, data_root=tmp_path)
    assert open_failure.value.code == SCHEMA_INCONSISTENT
    # Nothing usable was created: the file, if any, still has no business tables.
    if fresh.exists():
        connection = open_readonly(fresh)
        try:
            assert schema.business_tables(connection) == []
        finally:
            connection.close()
