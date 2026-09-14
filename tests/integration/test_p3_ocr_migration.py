"""P3-01: the 0009 step of the migration chain, on a real populated v8 database."""

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
#: This file is about the 0009 step itself. Pinning the target keeps "v8 -> v9" meaning the
#: same thing after later additive migrations append to the chain.
V9 = 9

OCR_TABLES = {"asset_ocr_text", "asset_ocr_regions"}
OCR_TRIGGERS = {
    "trg_asset_ocr_text_search_queue_insert",
    "trg_asset_ocr_text_search_queue_update",
}

FROZEN_V8_ROW_SQL = (
    "SELECT asset_id, sha256, byte_size, created_at, state FROM assets ORDER BY asset_id",
    "SELECT location_id, relative_path, availability, asset_id FROM file_locations "
    "ORDER BY location_id",
    "SELECT asset_id, filename_text, ocr_text, place_text, tokenizer_version, fts_generation_id "
    "FROM search_documents ORDER BY asset_id",
    "SELECT asset_id, cause, queued_at FROM search_projection_queue ORDER BY asset_id",
    # The version key is the one row a migration is allowed to change.
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


def build_v8(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a real, populated v8 database using only the frozen 0001..0008 scripts."""
    path = db_path(tmp_path)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, 8))
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 8)
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
            "VALUES (1, '2024 年/发票 一.jpg', '2024 年/发票 一.jpg', 1, 4096, 7, NULL, 'PRESENT')"
        )
        connection.execute(
            "INSERT INTO search_documents(asset_id, filename_text, user_text, ocr_text, "
            "caption_text, tag_text, place_text, source_fingerprint, tokenizer_version, "
            "fts_generation_id, updated_at) VALUES (1, '发 票 一 jpg', '', '', '', '', '', ?, 1, "
            "'fts-v1', 1500)",
            (hashlib.sha256(b"frozen-v8").hexdigest(),),
        )
        # A second, hand-made queue row with a 0006 cause proves the rebuild keeps rows.
        connection.execute(
            "INSERT INTO search_projection_queue(asset_id, cause, queued_at) VALUES (1, "
            "'PLACE_ALIAS_CHANGED', 1234) ON CONFLICT(asset_id) DO UPDATE SET "
            "cause = excluded.cause, queued_at = excluded.queued_at"
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
            for index, sql in enumerate(FROZEN_V8_ROW_SQL)
        }
    finally:
        connection.close()


def test_migration_scripts_are_continuous_and_0009_is_the_ocr_layer() -> None:
    versions = [number for number, _ in schema.migration_files()]
    assert versions == list(range(1, SUPPORTED + 1))
    assert versions[8] == 9
    assert (REAL_MIGRATIONS_DIR / "0009_ocr_documents.sql").is_file()
    # Later additive migrations append to the chain, so the supported version only grows.
    assert SUPPORTED >= V9


def test_v8_to_v9_is_continuous_backed_up_and_lossless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v8(tmp_path, monkeypatch)
    assert inspect_database(path) == 8
    before = snapshot(path)
    before_bytes = path.read_bytes()

    only_v9 = migrations_upto(tmp_path, V9)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V9)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", only_v9)
    result = migrate_database(path, data_root=data_root(tmp_path))
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)

    assert result.previous_version == 8 and result.schema_version == V9
    assert result.applied == (9,)
    assert result.backup_path is not None
    backup = open_readonly(result.backup_path)
    try:
        assert schema.read_schema_version(backup) == 8
        assert OCR_TABLES.isdisjoint(schema.business_tables(backup))
        assert "trg_asset_ocr_text_search_queue_insert" not in {
            name for (kind, name) in schema.schema_objects(backup) if kind == "trigger"
        }
    finally:
        backup.close()

    live = open_readonly(path)
    try:
        assert schema.read_schema_version(live) == V9
        assert OCR_TABLES <= set(schema.business_tables(live))
        schema.verify_schema(live, V9)
        triggers = {name for (kind, name) in schema.schema_objects(live) if kind == "trigger"}
        assert OCR_TRIGGERS <= triggers
        # The OCR layer starts empty: nothing is fabricated for the existing assets.
        assert live.execute("SELECT COUNT(*) FROM asset_ocr_text").fetchone()[0] == 0
        assert live.execute("SELECT COUNT(*) FROM asset_ocr_regions").fetchone()[0] == 0
    finally:
        live.close()
    assert snapshot(path) == before
    assert path.read_bytes() != before_bytes

    again = migrate_database(path, data_root=data_root(tmp_path))
    assert again.applied == tuple(range(V9 + 1, SUPPORTED + 1))
    assert again.previous_version == V9


def test_the_rebuilt_queue_keeps_its_rows_and_gains_exactly_one_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v8(tmp_path, monkeypatch)
    queued_before = snapshot(path)["q3"]
    assert queued_before, "the v8 fixture must have queued at least one asset"
    migrate_database(path, data_root=data_root(tmp_path))

    connection = connect_rw(path)
    try:
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT asset_id, cause, queued_at FROM search_projection_queue ORDER BY asset_id"
            )
        ] == queued_before
        connection.execute(
            "INSERT INTO search_projection_queue(asset_id, cause, queued_at) VALUES (1, "
            "'OCR_TEXT_CHANGED', 9000) ON CONFLICT(asset_id) DO UPDATE SET "
            "cause = excluded.cause, queued_at = excluded.queued_at"
        )
        row = connection.execute(
            "SELECT cause, queued_at FROM search_projection_queue WHERE asset_id = 1"
        ).fetchone()
        assert (row["cause"], row["queued_at"]) == ("OCR_TEXT_CHANGED", 9000)
        for bad in ("BOGUS", "OCR_TEXT_CHANGE", ""):
            with pytest.raises(Exception):
                connection.execute(
                    "INSERT INTO search_projection_queue(asset_id, cause, queued_at) "
                    "VALUES (999, ?, 1)",
                    (bad,),
                )
        # The four frozen 0006 causes still work, so the rebuild did not narrow the contract.
        for cause in (
            "FILENAME_CHANGED",
            "PLACE_ASSIGNMENT_CHANGED",
            "PLACE_NAME_CHANGED",
            "PLACE_ALIAS_CHANGED",
        ):
            connection.execute(
                "INSERT INTO search_projection_queue(asset_id, cause, queued_at) VALUES (1, ?, 1) "
                "ON CONFLICT(asset_id) DO UPDATE SET cause = excluded.cause, queued_at = 1",
                (cause,),
            )
    finally:
        connection.close()


def test_the_ocr_tables_enforce_the_p3_01_invariants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v8(tmp_path, monkeypatch)
    migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO asset_ocr_text(asset_id, ocr_document_id, input_revision, location_id, "
            "text_revision, processor_id, spec_revision, model_fingerprint, artifact_id, "
            "region_count, min_confidence, mean_confidence, max_confidence, search_text, "
            "tokenizer_version, created_at, updated_at) "
            "VALUES (1, 'ocr:1', ?, 1, 1, 'ocr-text@1', 'rev', ?, 'artifact', 0, NULL, NULL, "
            "NULL, '发 票', 1, 5, 5)",
            ("a" * 64, "b" * 64),
        )
        # An OCR document change queues the asset through the existing projection path.
        row = connection.execute(
            "SELECT cause FROM search_projection_queue WHERE asset_id = 1"
        ).fetchone()
        assert str(row["cause"]) == "OCR_TEXT_CHANGED"
        # The confidence stats have to be consistent when regions exist.
        with pytest.raises(Exception):
            connection.execute(
                "UPDATE asset_ocr_text SET region_count = 2 WHERE asset_id = 1"
            )
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO asset_ocr_regions(asset_id, region_index, text_revision, box_json, "
                "confidence) VALUES (1, 0, 1, 'not json', 0.5)"
            )
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO asset_ocr_regions(asset_id, region_index, text_revision, box_json, "
                "confidence) VALUES (1, 0, 1, '[1,2,3,4]', 1.5)"
            )
        connection.execute(
            "INSERT INTO asset_ocr_regions(asset_id, region_index, text_revision, box_json, "
            "confidence) VALUES (1, 0, 1, '[1,2,3,4]', 0.87)"
        )
    finally:
        connection.close()


def test_an_interrupted_0009_rolls_back_completely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v8(tmp_path, monkeypatch)
    before = snapshot(path)
    directory = migrations_upto(tmp_path, V9)
    broken = directory / "0009_ocr_documents.sql"
    broken.write_text(
        broken.read_text(encoding="utf-8")
        + "\nSELECT * FROM table_that_does_not_exist;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V9)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", directory)

    with pytest.raises(CatalogError) as error:
        migrate_database(path, data_root=data_root(tmp_path))
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)

    assert error.value.code == MIGRATION_FAILED
    assert inspect_database(path) == 8
    assert snapshot(path) == before
    connection = open_readonly(path)
    try:
        assert OCR_TABLES.isdisjoint(schema.business_tables(connection))
        assert "search_projection_queue_snapshot" not in schema.business_tables(connection)
    finally:
        connection.close()


def test_a_missing_ocr_table_is_inconsistent_and_a_newer_schema_is_refused(
    tmp_path: Path,
) -> None:
    path = db_path(tmp_path)
    migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    connection.execute("DROP TABLE asset_ocr_regions")
    connection.close()
    with pytest.raises(CatalogError) as error:
        inspect_database(path)
    assert error.value.code == SCHEMA_INCONSISTENT
    assert "asset_ocr_regions" in error.value.details["missing_objects"][0]

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
