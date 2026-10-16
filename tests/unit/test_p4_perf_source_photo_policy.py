"""Source-scoped image membership keeps every suffix and availability state."""
import sqlite3

from photo_search.catalog.image_policy import IMAGE_EXTENSIONS, is_image_path, photo_asset_sql


def test_source_probe_matches_independent_membership_and_uses_asset_index():
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(
            "CREATE TABLE assets(asset_id INTEGER PRIMARY KEY);"
            "CREATE TABLE file_locations(asset_id INTEGER,source_id INTEGER,relative_path TEXT,"
            "availability TEXT,last_seen_scan_id INTEGER);"
            "CREATE INDEX idx_file_locations_asset ON file_locations(asset_id);"
            "CREATE INDEX idx_file_locations_source_seen "
            "ON file_locations(source_id,last_seen_scan_id);"
        )
        locations = []
        for number, suffix in enumerate(sorted(IMAGE_EXTENSIONS), start=1):
            locations.extend([
                (number, 1, f"照片/{number}{suffix.upper()}", "MISSING", None),
                (number, 2, f"照片/{number}{suffix}", "PRESENT", None),
                (number, 1, f"duplicate/{number}{suffix}", "PRESENT", None),
            ])
        locations.extend([
            (1000, 1, "video.mp4", "PRESENT", None),
            (1000, 2, "photo.jpg", "PRESENT", None),
            (1001, 1, "fake.jpg.txt", "PRESENT", None),
            (1002, 1, "path.jpg/child", "PRESENT", None),
        ])
        ids = sorted({row[0] for row in locations} | {2000})
        connection.executemany("INSERT INTO assets VALUES(?)", [(value,) for value in ids])
        connection.executemany("INSERT INTO file_locations VALUES(?,?,?,?,?)", locations)
        for source_id in (1, 2, 3):
            predicate, parameters = photo_asset_sql(source_id=source_id)
            query = f"SELECT a.asset_id FROM assets a WHERE {predicate} ORDER BY a.asset_id"
            actual = [row[0] for row in connection.execute(query, parameters)]
            expected = sorted({row[0] for row in locations
                               if row[1] == source_id and is_image_path(row[2])})
            assert actual == expected
            plan = " ".join(row[3] for row in connection.execute(
                "EXPLAIN QUERY PLAN " + query, parameters))
            assert "idx_file_locations_asset (asset_id=?)" in plan
            assert "idx_file_locations_source_seen" not in plan
        predicate, parameters = photo_asset_sql()
        assert connection.execute(
            f"SELECT a.asset_id FROM assets a WHERE a.asset_id=2000 AND {predicate}",
            parameters,
        ).fetchone() == (2000,)  # Zero-location legacy inclusion is only global.
    finally:
        connection.close()


def test_source_timeline_http_retains_missing_photos_and_excludes_other_source_images(api_env):
    catalog = api_env.service.catalog
    with catalog.write() as db:
        for source_id in (1, 2):
            db.execute(
                "INSERT INTO source_roots(source_id,display_name,root_path,status) "
                "VALUES(?,?,?,'ONLINE')", (source_id, str(source_id), f"synthetic://{source_id}"))
        for asset_id in (1, 2, 3, 4):
            db.execute(
                "INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) "
                "VALUES(?,?,1,1700000000000,'ACTIVE')", (asset_id, f"{asset_id:064x}"))
        for asset_id, source_id, path, availability in (
            (1, 1, "missing.JPG", "MISSING"), (2, 1, "video.mp4", "PRESENT"),
            (2, 2, "other.jpg", "PRESENT"), (3, 1, "photo.JPEG", "PRESENT"),
            (3, 1, "duplicate.jpeg", "MISSING"),
        ):
            db.execute(
                "INSERT INTO file_locations(asset_id,source_id,relative_path,path_key,size,"
                "mtime_ns,availability) VALUES(?,?,?,?,1,1,?)",
                (asset_id, source_id, path, path.casefold(), availability))

    async def scenario(client):
        await api_env.authenticate(client)
        parameters = {"kind": "source", "source_id": 1, "time_basis": "library"}
        page = await client.get("/api/v1/timeline/assets", params=parameters)
        assert page.status_code == 200, page.text
        assert [row["asset_id"] for row in page.json()["assets"]] == [3, 1]
        summary = await client.get("/api/v1/timeline/periods", params=parameters)
        assert summary.status_code == 200, summary.text
        assert summary.json()["counts"]["effective_total"] == 2

    api_env.scenario(scenario)
