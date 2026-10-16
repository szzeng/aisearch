"""Static face archives retain the bounded browse and snapshot guarantees after merge."""

from __future__ import annotations

import asyncio
import threading

import pytest
from p3_person_fixtures import PersonHarness

from photo_search.api.errors import ApiError
from photo_search.catalog.db import Catalog
from photo_search.catalog.sqlite_io import connect_rw
from photo_search.catalog.video import photo_asset_sql
from photo_search.persons import collections
from photo_search.persons.collection_sql import collection_condition
from photo_search.places import revision


@pytest.fixture
def library(tmp_path):
    harness = PersonHarness(tmp_path)
    try:
        yield harness
    finally:
        harness.close()


def _face(harness, asset_id, *, ordinal=0):
    face_id = harness.add_face(asset_id, ordinal=ordinal)
    with harness.catalog.write() as connection:
        connection.execute(
            "UPDATE faces SET input_revision=(SELECT sha256 FROM assets WHERE asset_id=?) "
            "WHERE face_id=?", (asset_id, face_id),
        )
    return face_id


def _archive(harness, collection, face_ids):
    catalog = harness.catalog
    current = catalog.read_one(
        "SELECT revision FROM person_collections WHERE collection_id=?",
        (collection["collection_id"],),
    )
    return collections.edit_face_members(
        catalog, collection["collection_id"], instance_token=collection["instance_token"],
        action="add", face_ids=face_ids, expected_revision=current["revision"],
    )


def test_archive_rejects_missing_source_atomically_before_hiding(library):
    catalog = library.catalog
    selected = collections.create_collection(catalog, "Source changed")["collection"]
    assets = [library.add_asset(name + ".jpg") for name in ("present", "missing")]
    faces = [_face(library, asset) for asset in assets]
    with catalog.write() as connection:
        connection.execute(
            "UPDATE file_locations SET availability='MISSING' WHERE asset_id=?", (assets[1],)
        )
        connection.execute(
            "INSERT INTO face_inbox_hidden(face_id,ignore_state) VALUES (?,'EXCLUDED')", (faces[0],)
        )
    tables = ("person_collection_face_members", "person_collections", "face_inbox_hidden",
              "face_person_links", "face_assignment_controls", "metadata",
              "catalog_filter_revision", "catalog_filter_revision_events")
    before = {name: [tuple(row) for row in catalog.read_all(f"SELECT * FROM {name}")]
              for name in tables}
    with pytest.raises(ApiError) as refused:
        collections.edit_face_members(
            catalog, selected["collection_id"], instance_token=selected["instance_token"],
            action="add", face_ids=faces, expected_revision=selected["revision"],
            hide_after_add=True,
        )
    assert refused.value.status == 409
    assert {name: [tuple(row) for row in catalog.read_all(f"SELECT * FROM {name}")]
            for name in tables} == before


def test_mixed_collection_counts_union_overlap_and_exclude_stale_sources(library):
    harness, catalog = library, library.catalog
    person = harness.add_person(name="Dynamic")
    selected = collections.create_collection(catalog, "Mixed")["collection"]
    other = collections.create_collection(catalog, "Other")["collection"]
    collections.edit_members(
        catalog, selected["collection_id"], instance_token=selected["instance_token"],
        action="add", person_ids=[person],
    )
    names = ("overlap", "dynamic", "static", "missing-dynamic", "missing-static", "changed", "video", "legacy", "other")
    assets = {name: harness.add_asset(name + ".jpg") for name in names}
    faces = {name: _face(harness, asset) for name, asset in assets.items()}
    second_overlap = _face(harness, assets["overlap"], ordinal=1)
    for name in ("overlap", "dynamic", "missing-dynamic"):
        harness.add_link(faces[name], person)
    archive_names = ("overlap", "static", "missing-static", "changed", "video", "legacy")
    _archive(harness, selected, [faces[name] for name in archive_names] + [second_overlap])
    _archive(harness, other, [faces["other"]])
    with catalog.write() as connection:
        connection.executemany(
            "UPDATE file_locations SET availability='MISSING' WHERE asset_id=?",
            [(assets["missing-dynamic"],), (assets["missing-static"],)],
        )
        connection.execute("UPDATE assets SET sha256=? WHERE asset_id=?", ("d" * 64, assets["changed"]))
        connection.execute("UPDATE file_locations SET relative_path='video.mp4' WHERE asset_id=?", (assets["video"],))
        connection.execute("DELETE FROM file_locations WHERE asset_id=?", (assets["legacy"],))
        # Hidden inbox state cannot remove explicit collection membership.
        connection.execute("INSERT INTO person_hidden(person_id) VALUES (?)", (person,))
        connection.execute("INSERT INTO face_inbox_hidden(face_id,ignore_state) VALUES (?,'EXCLUDED')", (faces["static"],))

    summaries = {row["collection_id"]: row for row in collections.list_collections(catalog)["collections"]}
    selected_id = selected["collection_id"]
    assert (summaries[selected_id]["member_count"], summaries[selected_id]["face_member_count"]) == (1, 7)
    assert summaries[selected_id]["photo_count"] == 4
    assert summaries[other["collection_id"]]["photo_count"] == 1
    detail = collections.collection_detail(
        catalog, selected_id, instance_token=selected["instance_token"]
    )["collection"]
    assert detail == summaries[selected_id]
    condition, parameters = collection_condition(selected_id)
    photo_sql, photo_parameters = photo_asset_sql()
    matched = catalog.read_all(
        "SELECT a.asset_id FROM assets AS a WHERE " + condition + " AND " + photo_sql,
        (*parameters, *photo_parameters),
    )
    expected = {assets[name] for name in ("overlap", "dynamic", "static", "legacy")}
    assert {row["asset_id"] for row in matched} == expected
    projected = collections._collection_photo_faces(catalog.connection, selected_id, sorted(expected))
    assert [item["face_id"] for item in projected[assets["overlap"]]["person_faces"]] == [faces["overlap"]]
    assert [item["face_id"] for item in projected[assets["overlap"]]["archived_faces"]] == [second_overlap]
    assert [item["face_id"] for item in projected[assets["static"]]["archived_faces"]] == [faces["static"]]
    assert [item["face_id"] for item in projected[assets["legacy"]]["archived_faces"]] == [faces["legacy"]]


def test_face_archive_page_keeps_identity_revision_and_rows_in_one_snapshot(library, monkeypatch):
    harness, catalog = library, library.catalog
    collection = collections.create_collection(catalog, "Snapshot")["collection"]
    faces = [_face(harness, harness.add_asset(f"snapshot-{index}.jpg")) for index in range(3)]
    _archive(harness, collection, faces)
    writer = Catalog(connect_rw(catalog.db_path, create=False), db_path=catalog.db_path, data_root=harness.data_root)
    original = collections._require_collection_instance
    committed = []

    def browse():
        return collections.face_members_page(
            catalog, collection["collection_id"], instance_token=collection["instance_token"], limit=1,
        )

    expected = browse()

    def interleave(*args, **kwargs):
        result = original(*args, **kwargs)
        if not committed:
            with writer.write() as connection:
                connection.execute(
                    "DELETE FROM person_collection_face_members WHERE collection_id=? AND source_face_id=?",
                    (collection["collection_id"], min(faces)),
                )
                connection.execute(
                    "UPDATE person_collections SET revision=revision+1 WHERE collection_id=?", (collection["collection_id"],),
                )
                revision.bump_filter_revision(
                    connection, cause=revision.CAUSE_PERSON_COLLECTION_CHANGED, now_ms=writer.now_ms(),
                )
            committed.append(True)
        return result

    try:
        monkeypatch.setattr(collections, "_require_collection_instance", interleave)
        assert browse() == expected
        assert committed == [True]
        assert not catalog.connection.in_transaction
        monkeypatch.setattr(collections, "_require_collection_instance", original)
        assert browse() != expected
        with pytest.raises(ApiError):
            collections.face_members_page(
                catalog, collection["collection_id"], instance_token=collection["instance_token"],
                limit=1, cursor=expected["next_cursor"],
            )
        assert not catalog.connection.in_transaction
    finally:
        writer.close()


def test_face_archive_pagination_has_explicit_final_order(library):
    harness, catalog = library, library.catalog
    collection = collections.create_collection(catalog, "Ordered")["collection"]
    candidate = harness.add_person(name="Candidate")
    faces = [_face(harness, harness.add_asset(f"ordered-{index}.jpg")) for index in range(7)]
    for face in faces[::2]:
        harness.add_link(face, candidate, decision="SUGGESTED")
    _archive(harness, collection, list(reversed(faces)))
    seen, cursor = [], None
    catalog.connection.execute("PRAGMA reverse_unordered_selects=ON")
    try:
        for _ in range(4):
            page = collections.face_members_page(
                catalog, collection["collection_id"], instance_token=collection["instance_token"], limit=2, cursor=cursor,
            )
            ids = [row["face_id"] for row in page["faces"]]
            assert ids == sorted(ids)
            seen.extend(ids)
            if not page["has_more"]:
                assert page["next_cursor"] is None
                break
            cursor = page["next_cursor"]
        assert not page["has_more"]
        assert seen == sorted(faces)
    finally:
        catalog.connection.execute("PRAGMA reverse_unordered_selects=OFF")


def test_collection_face_get_does_not_block_authenticated_health(api_env, monkeypatch):
    collection = collections.create_collection(api_env.service.catalog, "Responsive")["collection"]
    started, release, watchdog = threading.Event(), threading.Event(), threading.Event()
    original = collections.face_members_page

    def slow(*args, **kwargs):
        started.set()
        if not release.wait(2):
            watchdog.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(collections, "face_members_page", slow)

    async def scenario(client):
        await api_env.authenticate(client)
        task = asyncio.create_task(client.get(
            f"/api/v1/persons/collections/{collection['collection_id']}/{collection['instance_token']}/faces",
        ))
        try:
            assert await asyncio.to_thread(started.wait, 4), "archive query was not entered"
            health = await asyncio.wait_for(client.get("/api/v1/health"), 1)
            assert health.status_code == 200
            assert not watchdog.is_set(), "archive GET blocked the HTTP event loop"
            assert not task.done(), "health must finish while the archive query is still held"
        finally:
            release.set()
            response = await task
        assert response.status_code == 200, response.text
        assert response.json()["faces"] == []

    api_env.scenario(scenario)
