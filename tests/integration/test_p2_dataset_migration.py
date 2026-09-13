"""P2-03B: the continuous v4 -> v5 dataset/resolution migration and its failure boundaries.

The upgrade must back up first, advance in a single transaction and re-verify the target
schema; a failure must leave the v4 database byte-identical, and 0001..0004 stay frozen.
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

V5_TABLES = {
    "active_geo_dataset",
    "geo_dataset_files",
    "geo_datasets",
    "place_resolution_candidates",
    "place_resolutions",
}

FROZEN_V4_ROW_SQL = (
    "SELECT asset_id, sha256, byte_size, created_at, state FROM assets ORDER BY asset_id",
    "SELECT place_id, kind, canonical_name, normalized_name, parent_place_id, origin, "
    "lifecycle, revision FROM places ORDER BY place_id",
    "SELECT asset_id, mode, origin, manual_lock, revision, record_state FROM "
    "asset_place_assignments ORDER BY asset_id",
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


def build_v4(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a real, populated v4 database using only the frozen 0001..0004 scripts."""
    path = db_path(tmp_path)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, 4))
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 4)
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
            "VALUES ('custom', '外婆家', '外婆家', NULL, 'USER', 'ACTIVE', 1, 5, 5)"
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
            for index, sql in enumerate(FROZEN_V4_ROW_SQL)
        }
    finally:
        connection.close()


def test_v4_to_v5_is_continuous_backed_up_and_lossless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v4(tmp_path, monkeypatch)
    assert inspect_database(path) == 4
    before = snapshot(path)
    before_bytes = path.read_bytes()

    result = migrate_database(path, data_root=data_root(tmp_path))

    assert result.previous_version == 4 and result.schema_version == 5
    assert result.applied == (5,)
    assert result.backup_path is not None
    backup = open_readonly(result.backup_path)
    try:
        assert schema.read_schema_version(backup) == 4
        assert V5_TABLES.isdisjoint(schema.business_tables(backup))
    finally:
        backup.close()
    live = open_readonly(path)
    try:
        assert schema.read_schema_version(live) == 5
        assert V5_TABLES <= set(schema.business_tables(live))
        schema.verify_schema(live, 5)
        # No package is installed and nothing was fabricated.
        pointer = live.execute(
            "SELECT dataset_id, revision FROM active_geo_dataset WHERE singleton = 1"
        ).fetchone()
        assert pointer[0] is None and int(pointer[1]) == 0
        assert live.execute("SELECT COUNT(*) FROM geo_datasets").fetchone()[0] == 0
        assert live.execute("SELECT COUNT(*) FROM place_resolutions").fetchone()[0] == 0
    finally:
        live.close()
    assert snapshot(path) == before
    assert path.read_bytes() != before_bytes

    again = migrate_database(path, data_root=data_root(tmp_path))
    assert again.applied == () and again.backup_path is None and again.previous_version == 5


def test_an_interrupted_0005_rolls_back_to_v4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v4(tmp_path, monkeypatch)
    before = snapshot(path)
    directory = migrations_upto(tmp_path, 4)
    (directory / "0005_broken.sql").write_text(
        "CREATE TABLE geo_datasets_half(dataset_id TEXT PRIMARY KEY);\n"
        "INSERT INTO metadata(key, value) VALUES('dataset_probe', 'yes');\n"
        "SELECT * FROM definitely_missing_table;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", directory)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 5)

    with pytest.raises(CatalogError) as failure:
        migrate_database(path, data_root=data_root(tmp_path))

    assert failure.value.code == MIGRATION_FAILED
    assert inspect_database(path) == 4
    assert snapshot(path) == before
    connection = open_readonly(path)
    try:
        tables = set(schema.business_tables(connection))
        assert "geo_datasets_half" not in tables and V5_TABLES.isdisjoint(tables)
        assert connection.execute(
            "SELECT COUNT(*) FROM metadata WHERE key = 'dataset_probe'"
        ).fetchone()[0] == 0
        assert schema.read_schema_version(connection) == 4
    finally:
        connection.close()


def test_a_v5_database_missing_a_dataset_object_is_inconsistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v4(tmp_path, monkeypatch)
    migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    try:
        connection.execute("DROP TABLE place_resolution_candidates")
    finally:
        connection.close()

    with pytest.raises(CatalogError) as error:
        inspect_database(path)

    assert error.value.code == SCHEMA_INCONSISTENT
    assert any(
        "place_resolution_candidates" in name
        for name in error.value.details["missing_objects"]
    )


def test_a_newer_schema_is_refused_without_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v4(tmp_path, monkeypatch)
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


def test_the_new_tables_refuse_illegal_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v4(tmp_path, monkeypatch)
    migrate_database(path, data_root=data_root(tmp_path))
    writer = connect_rw(path)
    try:
        writer.execute(
            "INSERT INTO geo_datasets(dataset_id, package_id, package_version, contract_version, "
            "manifest_schema, provider, source_url, source_snapshot, license_id, license_url, "
            "attribution, builder_id, builder_version, parameters_sha256, content_sha256, "
            "coordinate_reference_system, coverage_json, compatible_schema_min, "
            "compatible_schema_max, relative_dir, previous_dataset_id, state, record_count, "
            "file_count, generated_at, validated_at, activated_at, retained_at, created_at, "
            "updated_at) VALUES ('ds-1', 'pkg', '1', 'p2-place-contracts.v1', "
            "'photo-search-place-dataset-manifest.v1', 'p', 'u', 's', 'L', 'lu', 'a', 'b', '1', "
            "?, ?, 'WGS84', '{}', 1, 9, 'places/datasets/ds-1', NULL, 'STAGED', 0, 1, 0, "
            "NULL, NULL, NULL, 1, 1)",
            ("c" * 64, "d" * 64),
        )
    finally:
        writer.close()

    # Content is immutable once imported.
    with pytest.raises(sqlite3.IntegrityError):
        writer = connect_rw(path)
        try:
            writer.execute(
                "UPDATE geo_datasets SET content_sha256 = ? WHERE dataset_id = 'ds-1'",
                ("e" * 64,),
            )
        finally:
            writer.close()

    # STAGED may only move to VALIDATED; jumping straight to ACTIVE is refused.
    with pytest.raises(sqlite3.IntegrityError):
        writer = connect_rw(path)
        try:
            writer.execute(
                "UPDATE geo_datasets SET state = 'ACTIVE', validated_at = 2, activated_at = 2 "
                "WHERE dataset_id = 'ds-1'"
            )
        finally:
            writer.close()

    # A VALIDATED package still needs validated_at.
    with pytest.raises(sqlite3.IntegrityError):
        writer = connect_rw(path)
        try:
            writer.execute(
                "UPDATE geo_datasets SET state = 'VALIDATED' WHERE dataset_id = 'ds-1'"
            )
        finally:
            writer.close()

    writer = connect_rw(path)
    try:
        writer.execute(
            "UPDATE geo_datasets SET state = 'VALIDATED', validated_at = 2, updated_at = 2 "
            "WHERE dataset_id = 'ds-1'"
        )
        writer.execute(
            "INSERT INTO place_resolutions(resolution_id, asset_id, geo_fact_id, input_revision, "
            "control_revision_seen, processor_id, dataset_id, dataset_version, config_hash, "
            "status, suggestion_revision, created_at) VALUES ('res-1', 1, 'gf-1', ?, 0, 'p', "
            "'ds-1', '1', ?, 'NO_CANDIDATE', 0, 5)",
            ("a" * 64, "c" * 64),
        )
    finally:
        writer.close()

    for statement in (
        "DELETE FROM place_resolutions",
        "UPDATE place_resolutions SET status = 'CANDIDATES'",
        "INSERT INTO place_resolution_candidates(resolution_id, rank, candidate_id, "
        "dataset_place_id, local_place_id, kind, display_name, ancestor_names_json, distance_m, "
        "match_basis, confidence_basis, disposition, created_at) VALUES "
        "('res-1', 1, 'c', 'd', NULL, 'city', 'x', '[]', 1.0, 'NEARBY_CITY_POINT', "
        "'DISTANCE_ONLY', 'AUTO_ASSIGN', 5)",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            writer = connect_rw(path)
            try:
                writer.execute(statement)
            finally:
                writer.close()
