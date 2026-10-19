from __future__ import annotations

import hashlib

import pytest

from photo_search.api.errors import ApiError
from photo_search.api.timeline import timeline_page, timeline_periods
from photo_search.catalog.db import Catalog
from photo_search.photo_collections import (
    create_collection,
    delete_collection,
    edit_members,
    list_collections,
    rename_collection,
)


@pytest.fixture
def catalog(tmp_path):
    instance = Catalog.open(data_root=tmp_path / "data")
    with instance.write() as connection:
        connection.execute("INSERT INTO source_roots(display_name,root_path,status) VALUES('s','/s','ONLINE')")
        for i in range(3):
            digest = hashlib.sha256(str(i).encode()).hexdigest()
            connection.execute("INSERT INTO assets(sha256,byte_size,created_at) VALUES(?,?,?)", (digest, 1, 1_700_000_000_000 + i))
    yield instance
    instance.close()


def test_collection_create_add_remove_positions_and_noop(catalog):
    made = create_collection(catalog, "  Travel  ", "create-a")
    collection = made["collection"]
    cid, token = collection["collection_id"], collection["instance_token"]
    assert create_collection(catalog, "Travel", "create-a")["reused"] is True
    first = edit_members(catalog, cid, [1, 2], action="add", instance_token=token,
                         expected_revision=1)
    assert first["changed"] == 2
    assert [r["position"] for r in catalog.read_all(
        "SELECT position FROM photo_collection_members ORDER BY position")] == [1, 2]
    assert edit_members(catalog, cid, [2], action="add", instance_token=token,
                        expected_revision=2)["changed"] == 0
    assert edit_members(catalog, cid, [1], action="remove", instance_token=token,
                        expected_revision=2)["changed"] == 1
    edit_members(catalog, cid, [3], action="add", instance_token=token,
                 expected_revision=3)
    rows = catalog.read_all("SELECT asset_id,position FROM photo_collection_members ORDER BY position")
    assert [(int(r["asset_id"]), int(r["position"])) for r in rows] == [(2, 2), (3, 3)]
    assert catalog.read_one("SELECT member_count FROM photo_collections WHERE collection_id=?", (cid,))[0] == 2


def test_photo_collection_rejects_invalid_add_without_partial_write(catalog):
    collection = create_collection(catalog, "Travel", "create-a")["collection"]
    with pytest.raises(ApiError):
        edit_members(catalog, collection["collection_id"], [1, 999], action="add",
                     instance_token=collection["instance_token"], expected_revision=1)
    assert catalog.read_one("SELECT COUNT(*) FROM photo_collection_members")[0] == 0
    assert catalog.read_one("SELECT revision,member_count FROM photo_collections")[0:2] == (1, 0)


def test_photo_collection_rename_delete_identity_and_list_cursor(catalog):
    created = create_collection(catalog, "Travel", "create-a")["collection"]
    renamed = rename_collection(catalog, created["collection_id"], instance_token=created["instance_token"],
                                expected_revision=1, name="Summer")
    assert renamed["collection"]["revision"] == 2
    with pytest.raises(ApiError):
        rename_collection(catalog, created["collection_id"], instance_token="wrong",
                          expected_revision=2, name="Oops")
    delete_collection(catalog, created["collection_id"], instance_token=created["instance_token"],
                      expected_revision=2)
    assert catalog.read_one("SELECT COUNT(*) FROM assets")[0] == 3
    assert list_collections(catalog)["collections"] == []


def test_photo_collection_timeline_is_scope_bound_and_stale_refuses(catalog):
    collection = create_collection(catalog, "Travel", "create-a", asset_ids=[1, 3])["collection"]
    common = dict(kind="photo_collection", photo_collection_id=collection["collection_id"],
                  photo_collection_instance=collection["instance_token"],
                  photo_collection_revision=collection["revision"], time_basis="library", limit=1)
    page = timeline_page(catalog, **common)
    assert [row["asset_id"] for row in page["assets"]] == [3]
    assert page["has_more"] is True
    periods = timeline_periods(catalog, **{k: v for k, v in common.items() if k != "limit"})
    assert periods["scope"]["kind"] == "photo_collection"
    edit_members(catalog, collection["collection_id"], [2], action="add",
                 instance_token=collection["instance_token"], expected_revision=collection["revision"])
    with pytest.raises(ApiError):
        timeline_page(catalog, **common)



def test_photo_collection_search_save_rechecks_place_revisions_inside_write(catalog):
    from photo_search.places import cn_facets

    current = cn_facets.revisions(catalog)
    expected = (current[1], current[2])
    made = create_collection(catalog, "Stable place scope", "place-scope-ok",
                             expected_catalog_filter_revision=current[0], expected_place_scope=expected)
    assert made["reused"] is False

    with catalog.write() as connection:
        connection.execute("UPDATE place_suggestion_revision SET revision=revision+1 WHERE revision_id=1")
    with pytest.raises(ApiError) as error:
        create_collection(catalog, "Stale place scope", "place-scope-stale",
                          expected_catalog_filter_revision=current[0], expected_place_scope=expected)
    assert error.value.code == "CATALOG_FILTER_REVISION_STALE"
    assert catalog.read_one("SELECT COUNT(*) FROM photo_collections WHERE name='Stale place scope'")[0] == 0
