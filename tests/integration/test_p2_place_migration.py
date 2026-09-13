"""P2-03A: the continuous v3 -> v4 place-core migration and its failure boundaries.

The upgrade must be a backup, one transaction and a target-schema re-verification; a
failure has to leave the v3 database byte-identical, and the frozen 0001/0002/0003 files
are never edited.
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

PLACE_TABLES = {
    "active_geo_dataset",
    "asset_geo_facts",
    "asset_place_assignment_history",
    "asset_place_assignments",
    "asset_place_exclusions",
    "catalog_filter_revision",
    "catalog_filter_revision_events",
    "geo_dataset_files",
    "geo_datasets",
    "place_aliases",
    "place_external_refs",
    "place_operation_items",
    "place_operations",
    "place_resolution_candidates",
    "place_resolutions",
    "place_suggestion_revision",
    "places",
}

REAL_MIGRATIONS_DIR = Path(schema.__file__).resolve().parent / "migrations"
SUPPORTED = int(schema.SUPPORTED_SCHEMA_VERSION)

V3_TABLES = {
    "artifacts",
    "assets",
    "file_locations",
    "index_entries",
    "index_generations",
    "index_outbox",
    "jobs",
    "metadata",
    "runtime_capabilities",
    "runtime_qualification_evidence",
    "scan_runs",
    "scheduler_state",
    "source_roots",
}

V3_ROWS = (
    "SELECT asset_id, sha256, byte_size, created_at, state FROM assets ORDER BY asset_id"
)
V3_JOB_ROWS = (
    "SELECT job_id, subject_type, subject_id, input_revision, task_type, processor_id, state, "
    "priority, attempts, max_attempts, run_after, lease_token, lease_expires_at, worker_id, "
    "progress, last_error_code, last_error_message, created_at, updated_at FROM jobs "
    "ORDER BY job_id"
)


def data_root(tmp_path: Path) -> Path:
    return tmp_path / "PhotoSearchData"


def db_path(tmp_path: Path) -> Path:
    return data_root(tmp_path) / "catalog" / "catalog.db"


def migrations_upto(tmp_path: Path, version: int) -> Path:
    """A migration directory holding exactly ``0001..version`` from the real scripts."""
    directory = tmp_path / f"migrations-{version}"
    directory.mkdir(parents=True, exist_ok=True)
    for number, source in schema.migration_files():
        if number <= int(version):
            (directory / source.name).write_text(
                source.read_text(encoding="utf-8"), encoding="utf-8"
            )
    return directory


def build_v3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a real, populated v3 database using only the frozen 0001..0003 scripts."""
    path = db_path(tmp_path)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, 3))
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 3)
    migrate_database(path, data_root=data_root(tmp_path))
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)
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
            "VALUES (1, '2024 年/照 片 一.jpg', '2024 年/照 片 一.jpg', 1, 4096, 7, NULL, 'PRESENT')"
        )
        connection.execute(
            "INSERT INTO jobs(subject_type, subject_id, input_revision, task_type, processor_id, "
            "state, priority, attempts, max_attempts, run_after, progress, created_at, updated_at, "
            "requested_device) "
            "VALUES ('asset', '1', 'rev-1', 'image_embedding', 'clip@1', 'RUNNING', 3, 1, 3, 0, 0.5, "
            "10, 20, 'auto')"
        )
        connection.execute(
            "INSERT INTO jobs(subject_type, subject_id, input_revision, task_type, processor_id, "
            "state, priority, attempts, max_attempts, run_after, lease_token, lease_expires_at, "
            "worker_id, progress, created_at, updated_at) "
            "VALUES ('asset', '1', 'rev-2', 'image_embedding', 'clip@1', 'RUNNING', 0, 0, 3, 0, "
            "'legacy-lease-token', 999999, 'legacy-worker', 0.0, 30, 40)"
        )
        connection.execute(
            "INSERT INTO index_generations(generation_id, space_id, processor_id, dimension, "
            "state, is_active, created_at, updated_at) "
            "VALUES ('gen-1', 'space-1', 'clip@1', 4, 'READY', 1, 1, 1)"
        )
        job_id = int(connection.execute("SELECT MAX(job_id) FROM jobs").fetchone()[0])
        connection.execute(
            "INSERT INTO artifacts(artifact_id, processor_id, input_revision, artifact_type, "
            "artifact_revision, media_type, checksum_sha256, byte_size, storage_uri, space_id, "
            "dimension, job_id, lifecycle_state, created_at, updated_at) "
            "VALUES ('art-x', 'clip@1', 'rev-1', 'embedding', 'v1', 'application/json', ?, 4, "
            "'artifacts/pending/aa/bb/cc.json', 'space-1', 4, ?, 'PUBLISHED', 1, 1)",
            ("c" * 64, job_id),
        )
        connection.execute(
            "INSERT INTO index_outbox(event_id, event_type, artifact_id, space_id, asset_id, "
            "generation_id, projection_revision, payload_json, payload_checksum, state, attempts, "
            "created_at, updated_at) "
            "VALUES ('evt-1', 'UPSERT_VECTOR', 'art-x', 'space-1', 1, 'gen-1', 0, '{}', ?, "
            "'PENDING', 0, 1, 1)",
            ("b" * 64,),
        )
        connection.execute("COMMIT")
    finally:
        connection.close()
    return path


def snapshot(path: Path) -> dict[str, list[tuple]]:
    connection = open_readonly(path)
    try:
        return {
            "assets": [tuple(row) for row in connection.execute(V3_ROWS)],
            "jobs": [tuple(row) for row in connection.execute(V3_JOB_ROWS)],
            "locations": [
                tuple(row)
                for row in connection.execute(
                    "SELECT location_id, source_id, relative_path, path_key, asset_id, size, "
                    "mtime_ns, last_seen_scan_id, availability FROM file_locations ORDER BY location_id"
                )
            ],
            "outbox": [
                tuple(row)
                for row in connection.execute(
                    "SELECT event_id, state, attempts, payload_json FROM index_outbox ORDER BY event_id"
                )
            ],
        }
    finally:
        connection.close()


def test_v3_to_v4_is_continuous_backed_up_and_lossless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v3(tmp_path, monkeypatch)
    assert inspect_database(path) == 3
    before = snapshot(path)
    before_bytes = path.read_bytes()

    result = migrate_database(path, data_root=data_root(tmp_path))

    assert result.previous_version == 3 and result.schema_version == SUPPORTED
    assert result.applied == tuple(range(4, SUPPORTED + 1))
    assert result.backup_path is not None
    backup = open_readonly(result.backup_path)
    try:
        assert schema.read_schema_version(backup) == 3
        assert set(schema.business_tables(backup)) == V3_TABLES
        assert PLACE_TABLES.isdisjoint(schema.business_tables(backup))
    finally:
        backup.close()
    live = open_readonly(path)
    try:
        assert schema.read_schema_version(live) == SUPPORTED
        assert PLACE_TABLES <= set(schema.business_tables(live))
        schema.verify_schema(live, SUPPORTED)
        assert connection_scalar(live, "SELECT revision FROM catalog_filter_revision") == 0
        assert connection_scalar(live, "SELECT revision FROM place_suggestion_revision") == 0
    finally:
        live.close()
    assert snapshot(path) == before
    assert path.read_bytes() != before_bytes  # the schema itself did change

    # A second migration is a no-op with no backup.
    again = migrate_database(path, data_root=data_root(tmp_path))
    assert (
        again.applied == ()
        and again.backup_path is None
        and again.previous_version == SUPPORTED
    )


def connection_scalar(connection: sqlite3.Connection, sql: str) -> int:
    return int(connection.execute(sql).fetchone()[0])


def test_an_interrupted_0004_rolls_back_to_v3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v3(tmp_path, monkeypatch)
    before = snapshot(path)
    directory = migrations_upto(tmp_path, 3)
    (directory / "0004_broken.sql").write_text(
        "CREATE TABLE places_half(place_id INTEGER PRIMARY KEY);\n"
        "INSERT INTO metadata(key, value) VALUES('place_probe', 'yes');\n"
        "SELECT * FROM definitely_missing_table;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", directory)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 4)

    with pytest.raises(CatalogError) as failure:
        migrate_database(path, data_root=data_root(tmp_path))

    assert failure.value.code == MIGRATION_FAILED
    assert inspect_database(path) == 3
    assert snapshot(path) == before
    connection = open_readonly(path)
    try:
        tables = set(schema.business_tables(connection))
        assert "places_half" not in tables and PLACE_TABLES.isdisjoint(tables)
        assert connection.execute(
            "SELECT COUNT(*) FROM metadata WHERE key = 'place_probe'"
        ).fetchone()[0] == 0
        assert schema.read_schema_version(connection) == 3
    finally:
        connection.close()


def test_a_migrated_database_missing_a_place_object_is_inconsistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v3(tmp_path, monkeypatch)
    migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    try:
        connection.execute("DROP TABLE place_operation_items")
    finally:
        connection.close()

    with pytest.raises(CatalogError) as error:
        inspect_database(path)

    assert error.value.code == SCHEMA_INCONSISTENT
    assert any(
        "place_operation_items" in name for name in error.value.details["missing_objects"]
    )


def test_a_newer_schema_is_refused_without_a_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v3(tmp_path, monkeypatch)
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
    before = path.read_bytes()
    names = sorted(item.name for item in path.parent.iterdir())

    with pytest.raises(CatalogError) as too_new:
        migrate_database(path, data_root=data_root(tmp_path))
    assert too_new.value.code == SCHEMA_TOO_NEW
    with pytest.raises(CatalogError) as refused_open:
        Catalog.open(path, data_root=data_root(tmp_path))
    assert refused_open.value.code == SCHEMA_TOO_NEW
    assert path.read_bytes() == before
    assert sorted(item.name for item in path.parent.iterdir()) == names


def test_place_identity_and_history_cannot_be_physically_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v3(tmp_path, monkeypatch)
    migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO places(kind, canonical_name, normalized_name, origin, lifecycle, "
            "revision, created_at, updated_at) "
            "VALUES ('custom', '外婆家', '外婆家', 'USER', 'ACTIVE', 1, 1, 1)"
        )
        connection.execute("COMMIT")
    finally:
        connection.close()

    writer = connect_rw(path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            writer.execute("DELETE FROM places")
    finally:
        writer.close()
    check = open_readonly(path)
    try:
        assert check.execute("SELECT COUNT(*) FROM places").fetchone()[0] == 1
    finally:
        check.close()
