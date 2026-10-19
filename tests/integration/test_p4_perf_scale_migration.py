"""Official v31/v32 -> current schema preserves facts and applies migrations atomically."""
from pathlib import Path

import pytest
from p4_time_fixtures import REAL_MIGRATIONS_DIR, migrations_upto

from photo_search.catalog import schema
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import MIGRATION_FAILED, SCHEMA_TOO_NEW, CatalogError
from photo_search.catalog.migrate import inspect_database, migrate_database
from photo_search.catalog.sqlite_io import open_readonly


def prepare(tmp_path, monkeypatch, version=32):
    root = tmp_path / "data"
    with monkeypatch.context() as old:
        old.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, version))
        old.setattr(schema, "SUPPORTED_SCHEMA_VERSION", version)
        with Catalog.open(data_root=root) as catalog:
            with catalog.write() as conn:
                conn.execute(
                    "INSERT INTO source_roots(display_name,root_path,status) "
                    "VALUES('fixture','synthetic://fixture','ONLINE')"
                )
                conn.execute(
                    "INSERT INTO assets(sha256,byte_size,created_at,state) "
                    "VALUES(?,42,123,'ACTIVE')", ("a" * 64,)
                )
                conn.execute(
                    "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,"
                    "size,mtime_ns,availability) VALUES(1,'a.jpg','a.jpg',1,42,1,'PRESENT')"
                )
    return root, root / "catalog/catalog.db"


def rows(path: Path):
    conn = open_readonly(path)
    try:
        return {
            r[0]: list(map(tuple, conn.execute(f'SELECT * FROM "{r[0]}"')))
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name<>'metadata' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        conn.close()


@pytest.mark.parametrize("previous", [31, 32])
def test_scale_indexes_preserve_rows_backup_and_old_build_refusal(
    tmp_path, monkeypatch, previous,
):
    root, path = prepare(tmp_path, monkeypatch, previous)
    before = rows(path)
    result = migrate_database(path, data_root=root)
    assert result.applied == tuple(range(previous + 1, schema.SUPPORTED_SCHEMA_VERSION + 1))
    assert result.previous_version == previous
    assert result.backup_path is not None
    assert inspect_database(result.backup_path) == previous
    after = rows(path)
    assert rows(result.backup_path) == before
    assert {name: after[name] for name in before} == before
    assert after["person_collection_face_members"] == []
    with Catalog.open(data_root=root) as catalog:
        schema.verify_schema(catalog.connection, schema.SUPPORTED_SCHEMA_VERSION)
        assert catalog.read_all("PRAGMA foreign_key_check") == []
        statements = [
            (
                "SELECT asset_id FROM assets ORDER BY created_at DESC,asset_id DESC LIMIT 50",
                "idx_assets_created_id",
            ),
            (
                "SELECT DISTINCT asset_id FROM file_locations WHERE source_id=1 "
                "AND availability='PRESENT' AND asset_id>0 ORDER BY asset_id LIMIT 50",
                "idx_file_locations_present_source_asset",
            ),
            (
                "SELECT 1 FROM index_outbox WHERE generation_id=1 AND state<>'CONFIRMED' LIMIT 1",
                "idx_index_outbox_unconfirmed_generation",
            ),
        ]
        for sql, expected in statements:
            plan = " ".join(str(r[3]) for r in catalog.read_all("EXPLAIN QUERY PLAN " + sql))
            assert expected in plan and "TEMP B-TREE" not in plan
    before_bytes = path.read_bytes()
    with monkeypatch.context() as old:
        old.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 32)
        with pytest.raises(CatalogError) as error:
            migrate_database(path, data_root=root)
        assert error.value.code == SCHEMA_TOO_NEW
    assert path.read_bytes() == before_bytes
    repeated = migrate_database(path, data_root=root)
    assert repeated.applied == () and repeated.backup_path is None


def test_partial_scale_index_migration_rolls_back(tmp_path, monkeypatch):
    root, path = prepare(tmp_path, monkeypatch)
    before = rows(path)
    directory = migrations_upto(tmp_path, schema.SUPPORTED_SCHEMA_VERSION)
    script = directory / "0033_scale_read_indexes.sql"
    script.write_text(
        script.read_text(encoding="utf-8") + "\nSELECT * FROM missing_fixture_table;\n",
        encoding="utf-8",
    )
    with monkeypatch.context() as broken:
        broken.setattr(schema, "MIGRATIONS_DIR", directory)
        with pytest.raises(CatalogError) as error:
            migrate_database(path, data_root=root)
        assert error.value.code == MIGRATION_FAILED
    assert schema.MIGRATIONS_DIR == REAL_MIGRATIONS_DIR
    assert inspect_database(path) == 32 and rows(path) == before
    assert migrate_database(path, data_root=root).applied == tuple(
        range(33, schema.SUPPORTED_SCHEMA_VERSION + 1)
    )
