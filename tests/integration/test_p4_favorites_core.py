from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path

import pytest
from httpx import AsyncClient
from p4_time_fixtures import REAL_MIGRATIONS_DIR, migrations_upto

from photo_search.catalog import schema
from photo_search.catalog.errors import MIGRATION_FAILED, SCHEMA_TOO_NEW, CatalogError
from photo_search.catalog.migrate import migrate_database
from photo_search.catalog.sqlite_io import connect_rw, open_readonly


def _seed(catalog, source_id: int, asset_id: int, *, path: str, state: str = "ACTIVE", availability: str = "PRESENT") -> None:
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) VALUES(?,?,?,?,?)",
            (asset_id, f"{asset_id:064x}", 8, asset_id, state),
        )
        connection.execute(
            "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,last_seen_scan_id,availability) "
            "VALUES(?,?,?,?,8,1,NULL,?)",
            (source_id, path, path.casefold(), asset_id, availability),
        )


def test_favorite_api_is_idempotent_revision_bound_and_index_driven(api_env, monkeypatch) -> None:
    source_path = api_env.tmp_path / "source"
    source_path.mkdir()
    source_id = int(api_env.register_source("favorites", source_path)["source_id"])
    _seed(api_env.service.catalog, source_id, 1, path="one.jpg")
    _seed(api_env.service.catalog, source_id, 2, path="two.jpg", availability="MISSING")
    _seed(api_env.service.catalog, source_id, 3, path="notes.txt")
    _seed(api_env.service.catalog, source_id, 5, path="retention.jpg", availability="MISSING")
    # Two locations still represent one content-addressed asset and one favorite.
    with api_env.service.catalog.write() as connection:
        connection.execute(
            "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,last_seen_scan_id,availability) "
            "VALUES(?,?,?,1,8,1,NULL,'PRESENT')",
            (source_id, "one-copy.jpg", "one-copy.jpg"),
        )

    import photo_search.favorites as favorites

    calls = []
    original_locations = favorites.locations_by_asset

    def counted_locations(catalog, ids):
        calls.append(tuple(ids))
        return original_locations(catalog, ids)

    monkeypatch.setattr(favorites, "locations_by_asset", counted_locations)

    async def scenario(client: AsyncClient):
        denied = await client.get("/api/v1/favorites")
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        csrf_denied = await client.put("/api/v1/assets/1/favorite", headers={"Origin": "http://127.0.0.1"})
        first_put = await client.put("/api/v1/assets/1/favorite", headers=headers)
        repeated_put = await client.put("/api/v1/assets/1/favorite", headers=headers)
        offline_put = await client.put("/api/v1/assets/2/favorite", headers=headers)
        text_put = await client.put("/api/v1/assets/3/favorite", headers=headers)
        inactive_put = await client.put("/api/v1/assets/999/favorite", headers=headers)
        states = await client.get("/api/v1/favorites/states", params=[("asset_ids", "1"), ("asset_ids", "2"), ("asset_ids", "999")])
        first_page = await client.get("/api/v1/favorites", params={"limit": 1})
        stale = await client.put("/api/v1/assets/5/favorite", headers=headers)
        stale_page = await client.get("/api/v1/favorites", params={"limit": 1, "cursor": first_page.json()["next_cursor"]})
        removed = await client.delete("/api/v1/assets/2/favorite", headers=headers)
        repeated_delete = await client.delete("/api/v1/assets/2/favorite", headers=headers)
        invalid_states = await client.get("/api/v1/favorites/states", params=[("asset_ids", "0")])
        oversized_states = await client.get("/api/v1/favorites/states", params=[("asset_ids", str(i)) for i in range(1, 102)])
        return locals()

    result = api_env.scenario(scenario)
    assert result["denied"].status_code == 401
    assert result["csrf_denied"].status_code == 403
    assert result["first_put"].json() == {"asset_id": 1, "is_favorite": True, "changed": True, "favorites_revision": 1}
    assert result["repeated_put"].json() == {"asset_id": 1, "is_favorite": True, "changed": False, "favorites_revision": 1}
    assert result["offline_put"].json()["changed"] is True
    assert result["text_put"].status_code == 404
    assert result["inactive_put"].status_code == 404
    assert result["states"].json() == {"favorite_asset_ids": [1, 2], "favorites_revision": 2}
    first = result["first_page"].json()
    assert first["assets"][0]["asset_id"] == 2  # same millisecond: stable asset_id tie-break
    assert first["assets"][0]["availability"] == "missing"
    assert first["assets"][0]["favorited_at_ms"] == api_env.clock.ms
    assert first["has_more"] is True and first["next_cursor"]
    assert result["stale"].json()["favorites_revision"] == 3
    assert result["stale_page"].status_code == 409
    assert result["stale_page"].json()["code"] == "FAVORITES_REVISION_STALE"
    assert result["removed"].json()["changed"] is True
    assert result["repeated_delete"].json()["changed"] is False
    assert result["invalid_states"].status_code == 422
    assert result["oversized_states"].status_code == 422
    assert calls and all(len(ids) <= 1 for ids in calls)

    connection = api_env.service.catalog.connection
    plan = connection.execute(
        "EXPLAIN QUERY PLAN SELECT f.asset_id FROM asset_favorites AS f INDEXED BY idx_asset_favorites_recent "
        "JOIN assets AS a ON a.asset_id=f.asset_id WHERE a.state='ACTIVE' ORDER BY f.added_at_ms DESC, f.asset_id DESC LIMIT 51"
    ).fetchall()
    assert any("idx_asset_favorites_recent" in str(row["detail"]) for row in plan)

    from photo_search.library_retention import library_retention_preview
    retained = library_retention_preview(api_env.service.catalog, after_asset_id=4, limit=5)
    retention_item = next(item for item in retained["items"] if item["asset_id"] == 5)
    assert retention_item["has_human_or_protected_references"] is True
    assert "人工收藏" in retention_item["reason"]


def test_catalog_backup_restore_preserves_favorite_row_and_revision(api_env) -> None:
    source_path = api_env.tmp_path / "source"
    source_path.mkdir()
    source_id = int(api_env.register_source("favorites", source_path)["source_id"])
    _seed(api_env.service.catalog, source_id, 1, path="one.jpg")
    from photo_search.favorites import set_favorite

    set_favorite(api_env.service.catalog, 1, favorite=True)
    revision = api_env.service.catalog.read_one(
        "SELECT value FROM metadata WHERE key='favorites_revision'"
    )["value"]
    root = api_env.data_root
    bundle, restored_root = api_env.tmp_path / "bundle", api_env.tmp_path / "restored"
    api_env.close()
    from photo_search.backup import create_backup, restore_backup

    create_backup(data_root=root, destination=bundle, stopped=True)
    restore_backup(bundle=bundle, data_root=restored_root)
    from photo_search.catalog.db import Catalog

    with Catalog.open(data_root=restored_root) as restored:
        assert restored.read_one("SELECT asset_id,added_at_ms FROM asset_favorites")["asset_id"] == 1
        assert restored.read_one("SELECT value FROM metadata WHERE key='favorites_revision'")["value"] == revision


def test_favorite_write_rolls_back_if_revision_update_fails(api_env) -> None:
    source_path = api_env.tmp_path / "source"
    source_path.mkdir()
    source_id = int(api_env.register_source("favorites", source_path)["source_id"])
    _seed(api_env.service.catalog, source_id, 1, path="one.jpg")
    catalog = api_env.service.catalog
    with catalog.write() as connection:
        connection.execute(
            "CREATE TRIGGER fail_favorite_revision BEFORE UPDATE OF value ON metadata "
            "WHEN OLD.key='favorites_revision' BEGIN SELECT RAISE(ABORT,'injected revision failure'); END"
        )
    from photo_search.favorites import set_favorite

    with pytest.raises(sqlite3.IntegrityError, match="injected revision failure"):
        set_favorite(catalog, 1, favorite=True)
    assert catalog.read_one("SELECT 1 FROM asset_favorites WHERE asset_id=1") is None
    assert catalog.read_one("SELECT value FROM metadata WHERE key='favorites_revision'")["value"] == "0"


def test_0034_migration_is_additive_backed_up_and_refuses_older_build(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "data"
    path = root / "catalog" / "catalog.db"
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 33)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, 33))
    migrate_database(path, data_root=root)
    connection = connect_rw(path)
    try:
        connection.execute(
            "INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) VALUES(9,?,9,9,'ACTIVE')",
            ("9" * 64,),
        )
    finally:
        connection.close()

    old = open_readonly(path)
    try:
        before_assets = [tuple(row) for row in old.execute("SELECT * FROM assets")]
        before_metadata = {str(row[0]): str(row[1]) for row in old.execute("SELECT key,value FROM metadata") if row[0] != "schema_version"}
    finally:
        old.close()

    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 34)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    migrated = migrate_database(path, data_root=root)
    assert migrated.applied == (34,) and migrated.backup_path is not None
    backup = open_readonly(migrated.backup_path)
    try:
        assert int(backup.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]) == 33
        assert backup.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='asset_favorites'").fetchone() is None
    finally:
        backup.close()
    current = open_readonly(path)
    try:
        assert [tuple(row) for row in current.execute("SELECT * FROM assets")] == before_assets
        metadata = {str(row[0]): str(row[1]) for row in current.execute("SELECT key,value FROM metadata")}
        assert {key: metadata[key] for key in before_metadata} == before_metadata
        assert metadata["favorites_revision"] == "0"
        assert current.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        current.close()
    assert migrate_database(path, data_root=root).applied == ()

    digest_before = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 33)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, 33))
    with pytest.raises(CatalogError) as error:
        migrate_database(path, data_root=root)
    assert error.value.code == SCHEMA_TOO_NEW
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest_before


def test_0034_migration_failure_rolls_back(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "data"
    path = root / "catalog" / "catalog.db"
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 33)
    v33 = migrations_upto(tmp_path, 33)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", v33)
    migrate_database(path, data_root=root)
    broken = tmp_path / "broken-migrations"
    shutil.copytree(REAL_MIGRATIONS_DIR, broken)
    (broken / "0034_asset_favorites.sql").write_text("CREATE TABLE broken (", encoding="utf-8")
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 34)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", broken)
    with pytest.raises(CatalogError) as error:
        migrate_database(path, data_root=root)
    assert error.value.code == MIGRATION_FAILED
    connection = open_readonly(path)
    try:
        assert int(connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]) == 33
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='asset_favorites'").fetchone() is None
    finally:
        connection.close()
