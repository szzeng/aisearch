"""One browse response cannot mix authority rows across a concurrent WAL commit."""

from __future__ import annotations

import pytest
from p3_person_fixtures import PersonHarness

from photo_search.api import queries
from photo_search.api.errors import ApiError
from photo_search.catalog.db import Catalog
from photo_search.catalog.sqlite_io import connect_rw
from photo_search.media.cache import MediaCache
from photo_search.persons import albums, collections
from photo_search.places import revision
from photo_search.taken_time import contract


@pytest.fixture
def browse_library(tmp_path):
    harness = PersonHarness(tmp_path)
    first, second = harness.add_person(name="甲"), harness.add_person(name="乙")
    assets = []
    for index, person in enumerate((first, second, first)):
        asset = harness.add_asset(f"synthetic-{index}.jpg")
        assets.append(asset)
        harness.add_link(harness.add_face(asset), person)
        with harness.catalog.write() as connection:
            connection.execute(
                "INSERT INTO asset_metadata(asset_id,taken_at_sort,time_source,time_confidence) "
                "VALUES(?,?,'EXIF_ORIGINAL','HIGH')",
                (asset, 20250101000000 + index * 1000000),
            )
    with harness.catalog.write() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)",
            (contract.RULES_VERSION_KEY, str(contract.RULES_VERSION)),
        )
    collection = collections.create_collection(harness.catalog, "同行")['collection']
    collections.edit_members(
        harness.catalog, collection['collection_id'], instance_token=collection['instance_token'],
        action="add", person_ids=[first, second],
    )
    writer = Catalog(
        connect_rw(harness.catalog.db_path, create=False),
        db_path=harness.catalog.db_path, data_root=harness.data_root,
    )
    try:
        yield harness, writer, first, assets, collection
    finally:
        writer.close()
        harness.close()


@pytest.mark.parametrize("kind", [
    "collection_list", "collection_detail", "collection_people",
    "people", "collection_photos", "person_photos",
])
def test_browse_keeps_one_snapshot_through_members_cursor_and_faces(browse_library, monkeypatch, kind):
    harness, writer, first, assets, collection = browse_library
    catalog = harness.catalog
    cache = MediaCache.open(harness.data_root)
    identity = {"instance_token": collection["instance_token"]}
    collection_id = collection["collection_id"]

    def browse():
        if kind == "collection_list":
            return collections.list_collections(catalog)
        if kind == "collection_detail":
            return collections.collection_detail(catalog, collection_id, **identity)
        if kind == "collection_people":
            return collections.people_page(catalog, collection_id, **identity, limit=1)
        if kind == "people":
            return albums.people_page(catalog, kind="all", q="", limit=1, cursor=None)
        if kind == "collection_photos":
            return collections.photo_page(catalog, cache, collection_id, **identity, limit=1)
        return albums.photo_page(catalog, cache, person_id=first, limit=1, cursor=None)

    expected = browse()
    committed = []

    def commit_change():
        if committed:
            return
        with writer.write() as connection:
            connection.execute(
                "DELETE FROM person_collection_members WHERE collection_id=? AND person_id=?",
                (collection_id, first),
            )
            connection.execute(
                "UPDATE person_collections SET revision=revision+1 WHERE collection_id=?", (collection_id,),
            )
            connection.execute("DELETE FROM face_person_links WHERE person_id=?", (first,))
            connection.execute(
                "UPDATE asset_metadata SET taken_at_sort=20240101000000 WHERE asset_id=?", (assets[-1],),
            )
            revision.bump_filter_revision(
                connection, cause=revision.CAUSE_PERSON_COLLECTION_CHANGED, now_ms=writer.now_ms(),
            )
        committed.append(True)

    if kind == "collection_list":
        target, name, after = collections, "_collection_photo_counts", False
    elif kind in ("collection_detail", "collection_people"):
        target, name, after = collections, "_require_collection_instance", True
    elif kind == "people":
        target, name, after = albums, "_album_summaries", False
    else:
        # Commit after the asset query returns its lookahead rows, before the caller
        # constructs the trimmed cursor and projects capture-time/face facts.
        target, name, after = queries, "_asset_page", True
    original = getattr(target, name)

    def interleave(*args, **kwargs):
        if not after:
            commit_change()
        result = original(*args, **kwargs)
        if after:
            commit_change()
        return result

    monkeypatch.setattr(target, name, interleave)
    observed = browse()
    assert committed == [True], "second connection must commit during the browse"
    assert observed == expected, "response mixed rows from before and after the commit"
    assert not catalog.connection.in_transaction
    # A fresh request sees the committed state; this is snapshot consistency, not a cache.
    monkeypatch.setattr(target, name, original)
    assert browse() != expected


def test_collection_rename_uses_its_existing_write_transaction(browse_library):
    harness, _writer, _first, _assets, collection = browse_library
    result = collections.rename_collection(
        harness.catalog, collection["collection_id"], "新名称",
        instance_token=collection["instance_token"],
    )
    assert result["collection"]["name"] == "新名称"
    assert result["collection"]["member_count"] == 2
    assert not harness.catalog.connection.in_transaction


def test_asset_snapshot_entry_requires_read_transaction_and_keeps_nested_refusal(browse_library):
    harness, _writer, _first, _assets, _collection = browse_library
    catalog = harness.catalog
    cache = MediaCache.open(harness.data_root)
    with pytest.raises(RuntimeError, match="existing catalog read snapshot"):
        queries.read_asset_page_in_snapshot(catalog, cache, limit=1)
    with catalog.write():
        with pytest.raises(RuntimeError, match="existing catalog read snapshot"):
            queries.read_asset_page_in_snapshot(catalog, cache, limit=1)
    with catalog.read_snapshot():
        with pytest.raises(RuntimeError, match="must not be nested"):
            with catalog.read_snapshot():
                pytest.fail("nested snapshot was admitted")
        page = queries.read_asset_page_in_snapshot(catalog, cache, limit=1)
        assert len(page["assets"]) == 1
        assert catalog.connection.in_transaction
    assert not catalog.connection.in_transaction


@pytest.mark.parametrize("kind", ["person", "collection"])
def test_invalid_photo_cursor_releases_snapshot(browse_library, kind):
    harness, _writer, first, _assets, collection = browse_library
    catalog = harness.catalog
    cache = MediaCache.open(harness.data_root)
    with pytest.raises(ApiError):
        if kind == "person":
            albums.photo_page(catalog, cache, person_id=first, limit=1, cursor="broken")
        else:
            collections.photo_page(
                catalog, cache, collection["collection_id"],
                instance_token=collection["instance_token"], cursor="broken",
            )
    assert not catalog.connection.in_transaction
    with catalog.write() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1
