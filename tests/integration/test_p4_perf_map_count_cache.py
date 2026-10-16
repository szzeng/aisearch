"""Exact map totals follow snapshot authority, not elapsed time or a database path."""

import asyncio
import threading

import pytest
from fastapi import HTTPException

from photo_search.api import catalog_reads, map_api
from photo_search.api.catalog_reads import map_count_cache, run_catalog_read
from photo_search.catalog.db import Catalog
from photo_search.catalog.sqlite_io import open_readonly

BBOX = dict(west=0, south=0, east=2, north=1)


def seed(catalog, *, longitude_offset=0):
    """Four metadata-only assets; three have valid GPS, no image files are opened."""
    with catalog.write() as conn:
        source_id = conn.execute(
            "INSERT INTO source_roots(display_name,root_path,status) VALUES('synthetic','synthetic','ONLINE')"
        ).lastrowid
        ids = []
        for number in range(1, 5):
            sha = f"{number:064x}"
            asset_id = conn.execute(
                "INSERT INTO assets(sha256,byte_size,created_at,state) VALUES(?,1,1,'ACTIVE')", (sha,)
            ).lastrowid
            ids.append(asset_id)
            conn.execute(
                "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,availability) "
                "VALUES(?,?,?,?,1,1,'PRESENT')", (source_id, f"{number}.jpg", f"{number}.jpg", asset_id)
            )
            if number < 4:
                insert_geo(conn, asset_id, sha, longitude_offset + (0.2, 0.7, 1.4)[number - 1])
    return ids


def insert_geo(conn, asset_id, sha, longitude):
    conn.execute(
        "INSERT INTO asset_geo_facts(asset_id,geo_fact_id,input_revision,fact_revision,status,source,"
        "processor_id,raw_fields_checksum_sha256,raw_gps_json,latitude,longitude,datum_status,created_at,updated_at) "
        "VALUES(?,?,?,1,'VALID','EXIF_GPS','geo-exif-gps@1',?,'{}',0.2,?,'WGS84',1,1)",
        (asset_id, f"synthetic-{asset_id}", sha, "a" * 64, longitude),
    )


@pytest.fixture
def count_queries(monkeypatch):
    original = catalog_reads.open_readonly
    statements, lock = [], threading.Lock()

    def traced(*args, **kwargs):
        conn = original(*args, **kwargs)

        def trace(sql):
            if sql.startswith("SELECT COUNT(*) FROM assets a JOIN asset_geo_facts"):
                with lock:
                    statements.append(sql)

        conn.set_trace_callback(trace)
        return conn

    monkeypatch.setattr(catalog_reads, "open_readonly", traced)
    return statements


def read(catalog, query=map_api._map_points, **options):
    return asyncio.run(run_catalog_read(catalog, query, **BBOX, **options))


def test_points_assets_and_cursor_share_count_but_filters_are_distinct(api_env, count_queries):
    catalog = api_env.service.catalog
    ids = seed(catalog)
    assert read(catalog)["total"] == 3
    first = read(catalog, map_api._map_assets, limit=1)
    second = read(catalog, map_api._map_assets, limit=1, cursor=first["next_cursor"])
    assert first["total"] == second["total"] == 3
    assert first["assets"][0]["asset_id"] != second["assets"][0]["asset_id"]
    assert len(count_queries) == 1
    assert read(catalog, map_api._map_assets, asset_id=ids[0])["total"] == 1
    assert read(catalog, map_api._map_assets, asset_id=999)["total"] == 0
    assert read(catalog, map_api._map_assets, asset_id=999)["total"] == 0
    assert read(catalog, map_api._map_assets, cell_id="0:0", grid_zoom=1)["total"] == 2
    assert read(catalog, map_api._map_assets, cell_id="1:0", grid_zoom=1)["total"] == 1
    assert len(count_queries) == 5


@pytest.mark.parametrize("change,expected", [
    ("new_geo", 4), ("coordinates", 2), ("geo_delete", 2), ("geo_input_revision", 2),
    ("geo_status", 2), ("asset_sha", 2), ("asset_state", 3), ("asset_delete", 3), ("location_missing", 2),
    ("location_unbound", 2), ("location_delete", 2), ("location_insert", 3),
])
def test_authority_changes_invalidate_cached_count_and_old_cursor(api_env, count_queries, change,
                                                                 expected):
    catalog = api_env.service.catalog
    ids = seed(catalog)
    if change == "asset_delete":
        with catalog.write() as conn:
            conn.execute("DELETE FROM file_locations WHERE asset_id=?", (ids[3],))
            conn.execute("DELETE FROM search_projection_queue WHERE asset_id=?", (ids[3],))
    first = read(catalog, map_api._map_assets, limit=1)
    assert first["total"] == 3
    assert len(count_queries) == 1
    with catalog.write() as conn:
        if change == "new_geo":
            insert_geo(conn, ids[3], f"{4:064x}", 0.4)
        elif change == "coordinates":
            conn.execute("UPDATE asset_geo_facts SET longitude=10 WHERE asset_id=?", (ids[0],))
        elif change == "geo_delete":
            conn.execute("DELETE FROM asset_geo_facts WHERE asset_id=?", (ids[0],))
        elif change == "geo_input_revision":
            conn.execute("UPDATE asset_geo_facts SET input_revision=? WHERE asset_id=?", ("f" * 64, ids[0]))
        elif change == "geo_status":
            conn.execute("UPDATE asset_geo_facts SET status='INVALID',latitude=NULL,longitude=NULL WHERE asset_id=?",
                         (ids[0],))
        elif change == "asset_sha":
            conn.execute("UPDATE assets SET sha256=? WHERE asset_id=?", ("f" * 64, ids[0]))
        elif change == "asset_state":
            # ACTIVE is the only currently legal asset state. Even this no-op must
            # invalidate via the existing UPDATE OF state trigger, not a TTL.
            conn.execute("UPDATE assets SET state='ACTIVE' WHERE asset_id=?", (ids[0],))
        elif change == "asset_delete":
            conn.execute("DELETE FROM assets WHERE asset_id=?", (ids[3],))
        elif change == "location_missing":
            conn.execute("UPDATE file_locations SET availability='MISSING' WHERE asset_id=?", (ids[0],))
        elif change == "location_unbound":
            conn.execute("UPDATE file_locations SET asset_id=NULL WHERE asset_id=?", (ids[0],))
        elif change == "location_delete":
            conn.execute("DELETE FROM file_locations WHERE asset_id=?", (ids[0],))
        elif change == "location_insert":
            conn.execute(
                "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,availability) "
                "SELECT source_id,'copy.jpg','copy.jpg',asset_id,1,1,'PRESENT' FROM file_locations WHERE asset_id=?",
                (ids[0],),
            )
    updated = read(catalog)
    assert updated["member_version"] > first["member_version"]
    assert updated["total"] == len(updated["features"]) == expected
    assert len(count_queries) == 2
    with pytest.raises(HTTPException) as stale:
        read(catalog, map_api._map_assets, limit=1, cursor=first["next_cursor"])
    assert stale.value.status_code == 422
    assert len(count_queries) == 2


def test_same_revision_in_other_catalog_or_reopened_owner_has_independent_cache(api_env, tmp_path,
                                                                              count_queries):
    catalog = api_env.service.catalog
    seed(catalog)
    with Catalog.open(data_root=tmp_path / "other-catalog") as other:
        seed(other, longitude_offset=10)
        left = read(catalog)
        right = read(other)
        assert left["member_version"] == right["member_version"]
        assert (left["total"], right["total"]) == (3, 0)
        assert read(catalog)["total"] == 3
        assert map_count_cache(catalog) is not map_count_cache(other)
        assert len(count_queries) == 2
    with Catalog(open_readonly(catalog.db_path, immutable=False), db_path=catalog.db_path) as reopened:
        assert map_count_cache(catalog) is not map_count_cache(reopened)
        assert read(reopened)["total"] == 3
        assert len(count_queries) == 3


def test_cached_old_snapshot_cannot_replace_new_snapshot_answer(api_env):
    catalog = api_env.service.catalog
    ids = seed(catalog)
    assert read(catalog)["total"] == 3
    pinned, release = threading.Event(), threading.Event()

    def old_snapshot(reader):
        with reader.read_snapshot() as conn:
            version = map_api._revision(conn)
            where, args = map_api._where(tuple(BBOX.values()))
            source = map_api._from_sql(tuple(BBOX.values()))
            pinned.set()
            if not release.wait(5):
                raise RuntimeError("owner did not release old map snapshot")
            cached = map_api._exact_total(reader, conn, version=version, source=source,
                                          where=where, args=args)
            actual = conn.execute(f"SELECT COUNT(*) FROM {source} WHERE {where}", args).fetchone()[0]
            return cached, actual, version

    async def scenario():
        old = asyncio.create_task(run_catalog_read(catalog, old_snapshot))
        try:
            assert await asyncio.to_thread(pinned.wait, 3)
            with catalog.write() as conn:
                conn.execute("UPDATE file_locations SET availability='MISSING' WHERE asset_id=?", (ids[0],))
            latest = await run_catalog_read(catalog, map_api._map_points, **BBOX)
            assert latest["total"] == 2
        finally:
            release.set()
            cached, actual, version = await old
        assert cached == actual == 3
        assert latest["member_version"] > version
        assert (await run_catalog_read(catalog, map_api._map_points, **BBOX))["total"] == 2

    asyncio.run(scenario())
