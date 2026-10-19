from __future__ import annotations

import base64
import sqlite3

import pytest
from httpx import AsyncClient


def _seed(catalog, source_id: int, asset_id: int, *, path: str) -> None:
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) VALUES(?,?,?,?,?)",
            (asset_id, f"{asset_id:064x}", 8, asset_id, "ACTIVE"),
        )
        connection.execute(
            "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,last_seen_scan_id,availability) "
            "VALUES(?,?,?,?,8,1,NULL,'PRESENT')",
            (source_id, path, path.casefold(), asset_id),
        )


def test_favorite_batch_is_atomic_idempotent_and_capture_pages_are_truthful(api_env, monkeypatch) -> None:
    source_path = api_env.tmp_path / "source"
    source_path.mkdir()
    source_id = int(api_env.register_source("favorites-ux-b", source_path)["source_id"])
    for asset_id, name in ((1, "a.jpg"), (2, "b.jpg"), (3, "c.jpg"), (4, "note.txt"), (5, "root.jpg"), (6, "tie.jpg")):
        _seed(api_env.service.catalog, source_id, asset_id, path=name)
    with api_env.service.catalog.write() as connection:
        connection.executemany(
            "INSERT INTO asset_metadata(asset_id,taken_at_sort,time_source,time_confidence) VALUES(?,?,?,?)",
            [(1, 20240101000000, "EXIF_ORIGINAL", "MEDIUM"),
             (2, 20250101000000, "EXIF_DIGITIZED", "MEDIUM"),
             (3, None, "FILE_MTIME", "LOW"),
             (5, 20200101000000, "EXIF_ROOT", "LOW"), (6, 20240101000000, "EXIF_ORIGINAL", "MEDIUM")],
        )
        connection.executemany(
            "INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) VALUES(?,?,8,?,'ACTIVE')",
            [(asset_id, f"{asset_id:064x}", asset_id) for asset_id in range(100, 600)],
        )
        connection.executemany(
            "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,last_seen_scan_id,availability) "
            "VALUES(?,?,?,?,8,1,NULL,'PRESENT')",
            [(source_id, f"bulk-{asset_id}.jpg", f"bulk-{asset_id}.jpg", asset_id) for asset_id in range(100, 600)],
        )

    import photo_search.favorites as favorites
    location_reads = []
    original_locations = favorites.locations_by_asset

    def counted_locations(catalog, ids):
        location_reads.append(tuple(ids))
        return original_locations(catalog, ids)

    monkeypatch.setattr(favorites, "locations_by_asset", counted_locations)

    async def scenario(client: AsyncClient):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        invalid = await client.post("/api/v1/favorites/batch", json={"asset_ids": [1, 4], "is_favorite": True}, headers=headers)
        none_after_invalid = await client.get("/api/v1/favorites/states", params=[("asset_ids", "1")])
        first = await client.post("/api/v1/favorites/batch", json={"asset_ids": [1, 2, 3, 5, 6], "is_favorite": True}, headers=headers)
        repeat = await client.post("/api/v1/favorites/batch", json={"asset_ids": [1, 2, 3, 5, 6], "is_favorite": True}, headers=headers)
        first_capture = await client.get("/api/v1/favorites", params={"order": "capture_desc", "limit": 3})
        second_capture = await client.get("/api/v1/favorites", params={"order": "capture_desc", "limit": 3, "cursor": first_capture.json()["next_cursor"]})
        wrong_order_cursor = await client.get("/api/v1/favorites", params={"order": "recent", "limit": 3, "cursor": first_capture.json()["next_cursor"]})
        short_cursor = await client.get("/api/v1/favorites", params={"order": "recent", "limit": 2, "cursor": "ZmF2MQ"})
        oversized_recent_cursor = base64.urlsafe_b64encode(b"fav1:recent:1:9223372036854775808:1:0").decode().rstrip("=")
        oversized_capture_cursor = base64.urlsafe_b64encode(b"fav1:capture_desc:1:9223372036854775808:1:1:1").decode().rstrip("=")
        oversized_recent = await client.get("/api/v1/favorites", params={"order": "recent", "cursor": oversized_recent_cursor})
        oversized_capture = await client.get("/api/v1/favorites", params={"order": "capture_desc", "cursor": oversized_capture_cursor})
        no_op_remove = await client.post("/api/v1/favorites/batch", json={"asset_ids": [1], "is_favorite": True}, headers=headers)
        removed = await client.post("/api/v1/favorites/batch", json={"asset_ids": [1, 2], "is_favorite": False}, headers=headers)
        stale_capture_cursor = await client.get("/api/v1/favorites", params={"order": "capture_desc", "limit": 3, "cursor": first_capture.json()["next_cursor"]})
        invalid_shape = await client.post("/api/v1/favorites/batch", json={"asset_ids": [True], "is_favorite": True}, headers=headers)
        duplicate = await client.post("/api/v1/favorites/batch", json={"asset_ids": [3, 3], "is_favorite": True}, headers=headers)
        maximum = await client.post("/api/v1/favorites/batch", json={"asset_ids": list(range(100, 600)), "is_favorite": True}, headers=headers)
        over_maximum = await client.post("/api/v1/favorites/batch", json={"asset_ids": list(range(100, 601)), "is_favorite": True}, headers=headers)
        return locals()

    result = api_env.scenario(scenario)
    assert result["invalid"].status_code == 422
    assert result["none_after_invalid"].json()["favorite_asset_ids"] == []
    assert result["first"].json() == {"is_favorite": True, "requested": 5, "changed": 5, "unchanged": 0, "favorites_revision": 1}
    assert result["repeat"].json() == {"is_favorite": True, "requested": 5, "changed": 0, "unchanged": 5, "favorites_revision": 1}
    capture = result["first_capture"].json()
    assert [item["asset_id"] for item in capture["assets"]] == [2, 6, 1]
    assert capture["order"] == "capture_desc" and capture["has_more"] is True
    assert [item["asset_id"] for item in result["second_capture"].json()["assets"]] == [5, 3]
    assert result["wrong_order_cursor"].status_code == 409
    assert result["stale_capture_cursor"].status_code == 409
    assert result["short_cursor"].status_code == 422
    assert result["short_cursor"].json()["code"] == "INVALID_CURSOR"
    assert result["oversized_recent"].status_code == 422
    assert result["oversized_recent"].json()["code"] == "INVALID_CURSOR"
    assert result["oversized_capture"].status_code == 422
    assert result["oversized_capture"].json()["code"] == "INVALID_CURSOR"
    assert result["no_op_remove"].json()["favorites_revision"] == 1
    assert result["removed"].json() == {"is_favorite": False, "requested": 2, "changed": 2, "unchanged": 0, "favorites_revision": 2}
    assert result["invalid_shape"].status_code == 422
    assert result["duplicate"].status_code == 422
    assert api_env.service.catalog.read_one("SELECT COUNT(*) AS n FROM asset_favorites")["n"] == 503
    assert result["maximum"].json() == {"is_favorite": True, "requested": 500, "changed": 500, "unchanged": 0, "favorites_revision": 3}
    assert result["over_maximum"].status_code == 422
    assert location_reads == [(2, 6, 1), (5, 3)]
    plan = api_env.service.catalog.connection.execute(
        "EXPLAIN QUERY PLAN SELECT f.asset_id,m.taken_at_sort FROM asset_favorites AS f INDEXED BY idx_asset_favorites_recent "
        "JOIN assets AS a ON a.asset_id=f.asset_id LEFT JOIN asset_metadata AS m ON m.asset_id=f.asset_id "
        "WHERE a.state='ACTIVE' ORDER BY (NOT (m.time_source IN ('EXIF_ORIGINAL','EXIF_DIGITIZED') AND m.taken_at_sort IS NOT NULL)), "
        "CASE WHEN m.time_source IN ('EXIF_ORIGINAL','EXIF_DIGITIZED') THEN m.taken_at_sort END DESC,f.asset_id DESC LIMIT 51"
    ).fetchall()
    plan_text = " ".join(str(row["detail"]) for row in plan)
    assert "idx_asset_favorites_recent" in plan_text
    assert "SEARCH a USING INTEGER PRIMARY KEY" in plan_text



def test_favorite_batch_rolls_back_all_rows_when_revision_update_fails(api_env) -> None:
    source_path = api_env.tmp_path / "source"
    source_path.mkdir()
    source_id = int(api_env.register_source("favorites-ux-b-fault", source_path)["source_id"])
    _seed(api_env.service.catalog, source_id, 1, path="one.jpg")
    _seed(api_env.service.catalog, source_id, 2, path="two.jpg")
    catalog = api_env.service.catalog
    with catalog.write() as connection:
        connection.execute("CREATE TRIGGER fail_favorites_revision BEFORE UPDATE OF value ON metadata WHEN OLD.key='favorites_revision' BEGIN SELECT RAISE(ABORT,'injected batch revision failure'); END")
    from photo_search.favorites import set_favorites_batch

    with pytest.raises(sqlite3.IntegrityError, match="injected batch revision failure"):
        set_favorites_batch(catalog, [1, 2], favorite=True)
    assert catalog.read_one("SELECT COUNT(*) AS n FROM asset_favorites")["n"] == 0
    assert catalog.read_one("SELECT value FROM metadata WHERE key='favorites_revision'")["value"] == "0"
