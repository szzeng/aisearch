"""Dense map pages and sparse fallback retain complete spatial membership."""
import sqlite3

import pytest

from photo_search.api.map_api import _asset_id_page, _from_sql, _where


@pytest.mark.parametrize("distribution", ["newest", "oldest", "scattered", "empty", "all"])
def test_id_probe_matches_full_spatial_sort_across_pages(distribution):
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE assets(asset_id INTEGER PRIMARY KEY,created_at INTEGER,sha256 TEXT,state TEXT);
        CREATE TABLE file_locations(asset_id INTEGER,availability TEXT);
        CREATE INDEX location_asset ON file_locations(asset_id);
        CREATE TABLE asset_geo_facts(asset_id INTEGER PRIMARY KEY,status TEXT,datum_status TEXT,
                                    input_revision TEXT,latitude REAL,longitude REAL);
        CREATE INDEX geo_bbox ON asset_geo_facts(status,datum_status,latitude,longitude);
    """)
    try:
        assets, locations, facts = [], [], []
        for asset_id in range(1, 16001):
            inside = {"newest": asset_id > 9000, "oldest": asset_id <= 7000,
                      "scattered": asset_id % 97 == 0, "empty": False, "all": True}[distribution]
            assets.append((asset_id, asset_id, str(asset_id), "ACTIVE"))
            locations.append((asset_id, "MISSING" if asset_id % 101 == 0 else "PRESENT"))
            facts.append((asset_id, "VALID", "WGS84" if asset_id % 2 else "ASSUMED_WGS84",
                          "stale" if asset_id % 103 == 0 else str(asset_id),
                          22.6, 114.0 if inside else 121.0))
        conn.executemany("INSERT INTO assets VALUES(?,?,?,?)", assets)
        conn.executemany("INSERT INTO file_locations VALUES(?,?)", locations)
        conn.executemany("INSERT INTO asset_geo_facts VALUES(?,?,?,?,?,?)", facts)
        bbox = (113.8, 22.4, 114.6, 22.9)
        where, args = _where(bbox)
        source = _from_sql(bbox)
        expected = conn.execute(
            f"SELECT a.asset_id,a.created_at,g.latitude,g.longitude FROM {source} "
            f"WHERE {where} ORDER BY a.asset_id DESC", args,
        ).fetchall()
        # Check start, deep-page, end and beyond-end cursors against an independent
        # complete spatial result; eligibility includes stale facts and absence.
        cursors = [2**63 - 1, 15960, 10000, 7050, 4000, 50, 1]
        for before in cursors:
            actual = _asset_id_page(conn, source=source, where=where, args=args,
                                    total=len(expected), before=before, size=49)
            assert actual == [row for row in expected if row[0] < before][:49]
        first = _asset_id_page(conn, source=source, where=where, args=args,
                               total=len(expected), before=2**63 - 1, size=49)
        if first:
            second = _asset_id_page(conn, source=source, where=where, args=args,
                                    total=len(expected), before=first[47][0], size=49)
            assert first[:48] + second[:48] == expected[:96]
    finally:
        conn.close()
