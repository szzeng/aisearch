"""Browse SQL equivalence, snapshot publication and authenticated concurrency."""
import asyncio
import threading

import pytest

from photo_search.api import queries
from photo_search.api.errors import ApiError
from photo_search.catalog.db import Catalog
from photo_search.search import documents
from photo_search.taken_time.contract import RULES_VERSION_KEY


class Cache:
    def artifact_path(self, *, spec, sha256):
        return int(sha256, 16)

    def is_published(self, path):
        return path % 139 == 0


def seed(catalog, count=420):
    facts = {}
    with catalog.write() as db:
        for source in (1, 2):
            db.execute("INSERT INTO source_roots(source_id,display_name,root_path,status) "
                       "VALUES(?,?,?,'ONLINE')", (source, str(source), f"synthetic://{source}"))
        for asset_id in range(1, count + 1):
            db.execute("INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) "
                       "VALUES(?,?,1,1,'ACTIVE')", (asset_id, f"{asset_id:064x}"))
            kind = asset_id % 5
            key = 1000 + asset_id // 6 if kind < 3 else None
            facts[asset_id] = key
            if kind != 4:
                source = ("EXIF_ORIGINAL", "EXIF_DIGITIZED", "EXIF_ROOT", "FILE_MTIME")[kind]
                confidence = "MEDIUM" if kind < 2 else "LOW"
                db.execute("INSERT INTO asset_metadata(asset_id,taken_at_sort,time_source,time_confidence) "
                           "VALUES(?,?,?,?)", (asset_id, key, source, confidence))
            if asset_id != count:  # Legacy zero-location asset remains globally visible.
                name = f"照片 {asset_id}.JPG" if asset_id % 7 else f"video {asset_id}.mp4"
                db.execute("INSERT INTO file_locations(asset_id,source_id,relative_path,path_key,size,mtime_ns,availability) "
                           "VALUES(?,1,?,?,1,1,?)", (asset_id, name, name, "MISSING" if asset_id % 11 else "PRESENT"))
                if asset_id % 7 == 0:
                    name = f"other {asset_id}.jpg"
                    db.execute("INSERT INTO file_locations(asset_id,source_id,relative_path,path_key,size,mtime_ns,availability) "
                               "VALUES(?,2,?,?,1,1,'PRESENT')", (asset_id, name, name))
    return facts


@pytest.mark.parametrize("source_id", [None, 1, 2])
@pytest.mark.parametrize("thumbnail", ["any", "true", "false"])
def test_capture_pages_equal_full_independent_sort_with_tail_ties_and_cache_gaps(api_env, source_id, thumbnail):
    catalog = api_env.service.catalog
    facts = seed(catalog)
    expected = [asset_id for asset_id in facts
                if source_id is None or (source_id == 1 and asset_id != 420 and asset_id % 7)
                or (source_id == 2 and asset_id != 420 and not asset_id % 7)]
    if thumbnail != "any":
        expected = [n for n in expected if (n % 139 == 0) == (thumbnail == "true")]
    expected.sort(key=lambda n: (facts[n] is not None, facts[n] or 0, n), reverse=True)
    found, cursor = [], None
    for _ in range(100):
        page = queries.asset_page(catalog, Cache(), limit=7, cursor=cursor,
                                  source_id=source_id, has_thumbnail=thumbnail, order="capture_desc")
        found.extend(item["asset_id"] for item in page["assets"])
        cursor = page["next_cursor"]
        if not page["has_more"]:
            break
    assert found == expected
    assert len(found) == len(set(found))
    # An untrusted rules version does not change the frozen legacy ordering;
    # capture filters still become empty and explicitly report PENDING.
    with catalog.write() as db:
        db.execute("UPDATE metadata SET value='999' WHERE key=?", (RULES_VERSION_KEY,))
    page = queries.asset_page(catalog, Cache(), limit=5, capture_from="2020-01-01", order="capture_desc")
    assert page["assets"] == [] and page["taken_time"]["state"] == "PENDING"


def test_capture_response_is_one_snapshot_when_writer_changes_metadata_mid_page(api_env, monkeypatch):
    catalog = api_env.service.catalog
    seed(catalog, 10)
    writer = Catalog.open(data_root=api_env.data_root)
    before = int(catalog.read_one("SELECT revision FROM catalog_filter_revision")[0])
    original = queries.summarize_asset
    changed = False

    def change_after_first(**kwargs):
        nonlocal changed
        if not changed:
            changed = True
            with writer.write() as db:
                db.execute("UPDATE asset_metadata SET taken_at_sort=9999 WHERE asset_id=1")
                db.execute("UPDATE catalog_filter_revision SET revision=revision+1")
        return original(**kwargs)

    def hooked(cache, **kwargs):
        return change_after_first(cache=cache, **kwargs)

    monkeypatch.setattr(queries, "summarize_asset", hooked)
    try:
        first = queries.asset_page(catalog, Cache(), limit=3, order="capture_desc")
        assert first["catalog_filter_revision"] == before
        assert 1 not in [item["asset_id"] for item in first["assets"]]
        with pytest.raises(ApiError) as error:
            queries.asset_page(catalog, Cache(), limit=3, order="capture_desc", cursor=first["next_cursor"])
        assert error.value.code == "CATALOG_FILTER_REVISION_STALE"
    finally:
        writer.close()


@pytest.mark.parametrize("path,target", [
    ("/api/v1/assets", "read_asset_page"),
    ("/api/v1/sources", "source_list"),
    ("/api/v1/assets?text=照片", "prepare_asset_page"),
])
def test_slow_browse_and_projection_leave_authenticated_health_responsive(api_env, monkeypatch, path, target):
    from photo_search.api import routes

    seed(api_env.service.catalog, 20)
    started, release = threading.Event(), threading.Event()
    original = getattr(routes, target)
    threads = []

    def slow(*args, **kwargs):
        threads.append(threading.get_ident())
        started.set()
        if not release.wait(5):
            raise RuntimeError("health did not release query")
        return original(*args, **kwargs)

    monkeypatch.setattr(routes, target, slow)

    async def scenario(client):
        owner = threading.get_ident()
        await api_env.authenticate(client)
        task = asyncio.create_task(client.get(path))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            response = await asyncio.wait_for(client.get("/api/v1/health"), timeout=2)
            assert response.status_code == 200
        finally:
            release.set()
            result = await task
        assert result.status_code == 200, result.text
        assert threads and all(thread != owner for thread in threads)
        if "text=" in path:
            assert result.json()["assets"]
            assert documents.queue_depth(api_env.context.service().catalog.connection) == 0

    api_env.scenario(scenario)


def test_invalid_filter_does_not_write_projection_and_read_phase_cannot_write(api_env, monkeypatch):
    from photo_search.api import routes

    catalog = api_env.service.catalog
    seed(catalog, 10)
    queued = documents.queue_depth(catalog.connection)
    original = routes.read_asset_page

    def readonly(reader, *args, **kwargs):
        import sqlite3
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.connection.execute("UPDATE metadata SET value=value")
        return original(reader, *args, **kwargs)

    monkeypatch.setattr(routes, "read_asset_page", readonly)

    async def scenario(client):
        await api_env.authenticate(client)
        invalid = await client.get("/api/v1/assets", params={"text": "照片", "cursor": "invalid"})
        assert invalid.status_code == 422
        assert documents.queue_depth(catalog.connection) == queued
        valid = await client.get("/api/v1/assets", params={"text": "照片"})
        assert valid.status_code == 200, valid.text
        assert documents.queue_depth(catalog.connection) == 0

    api_env.scenario(scenario)


def test_text_page_reports_snapshot_lag_after_prepare_writer_gap(api_env):
    from photo_search.catalog.sqlite_io import open_readonly

    catalog = api_env.service.catalog
    seed(catalog, 20)
    prepared = queries.prepare_asset_page(catalog, limit=1, text="照片", order="capture_desc")
    assert prepared["state"] == "SYNCED"
    writer = Catalog.open(data_root=api_env.data_root)
    reader = Catalog(open_readonly(catalog.db_path), db_path=catalog.db_path,
                     data_root=api_env.data_root)
    try:
        with writer.write() as db:
            db.execute("UPDATE file_locations SET relative_path='changed.jpg' WHERE asset_id=11")
        page = queries.read_asset_page(reader, Cache(), projection=prepared,
                                       limit=1, text="照片", order="capture_desc")
        assert page["search"]["state"] == "LAGGING"
        assert page["search"]["queued_assets"] == 1
        assert page["search"]["processed_assets"] == prepared["processed"]
        # A subsequent worker can drain it; the same old prepare receipt must not
        # override the fresh snapshot in either direction.
        documents.catch_up(writer)
        cleared = queries.read_asset_page(reader, Cache(), projection={**prepared, "state": "LAGGING"},
                                          limit=1, text="照片")
        assert cleared["search"]["state"] == "SYNCED"
        assert cleared["search"]["queued_assets"] == 0
        with writer.write() as db:
            db.execute("UPDATE search_documents SET tokenizer_version=999 WHERE asset_id=11")
        stale = queries.read_asset_page(reader, Cache(), projection=prepared, limit=1, text="照片")
        assert stale["search"]["state"] == "LAGGING"
        assert stale["search"]["stale_documents"] == 1
        with writer.write() as db:
            db.execute("UPDATE catalog_filter_revision SET revision=revision+1")
        with pytest.raises(ApiError) as error:
            queries.read_asset_page(reader, Cache(), projection=prepared, limit=1,
                                    text="照片", order="capture_desc", cursor=page["next_cursor"])
        assert error.value.code == "CATALOG_FILTER_REVISION_STALE"
    finally:
        reader.close()
        writer.close()


@pytest.mark.parametrize("scope", ["person", "collection"])
def test_small_album_capture_work_does_not_scale_with_unrelated_metadata(tmp_path, scope):
    from p3_person_fixtures import PersonHarness

    from photo_search.persons import collections

    harness = PersonHarness(tmp_path)
    try:
        catalog = harness.catalog
        person = harness.add_person()
        chosen = harness.add_asset("chosen.jpg")
        harness.add_link(harness.add_face(chosen), person)
        with catalog.write() as db:
            db.execute("INSERT INTO asset_metadata(asset_id,taken_at_sort,time_source,time_confidence) "
                       "VALUES(?,1,'EXIF_ORIGINAL','MEDIUM')", (chosen,))
        filters = {"person_ids": (person,)}
        if scope == "collection":
            row = collections.create_collection(catalog, "chosen")["collection"]
            collections.edit_members(catalog, row["collection_id"], instance_token=row["instance_token"],
                                     action="add", person_ids=[person])
            filters = {"collection_id": row["collection_id"]}

        def measure():
            ticks = 0

            def progress():
                nonlocal ticks
                ticks += 1
                return 0

            catalog.connection.set_progress_handler(progress, 100)
            try:
                page = queries.asset_page(catalog, Cache(), limit=1, order="capture_desc", **filters)
            finally:
                catalog.connection.set_progress_handler(None, 0)
            assert [r["asset_id"] for r in page["assets"]] == [chosen]
            return ticks * 100

        before = measure()
        with catalog.write() as db:
            for asset_id in range(10000, 15000):
                db.execute("INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) "
                           "VALUES(?,?,1,1,'ACTIVE')", (asset_id, f"{asset_id:064x}"))
                db.execute("INSERT INTO asset_metadata(asset_id,taken_at_sort,time_source,time_confidence) "
                           "VALUES(?,?,'EXIF_ORIGINAL','MEDIUM')", (asset_id, asset_id))
        after = measure()
        assert after <= max(5000, before * 3), (before, after)
    finally:
        harness.close()
