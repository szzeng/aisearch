from __future__ import annotations

import pytest
from conftest import ApiEnv
from p3_persons_page_fixtures import add_link, add_person, seed_library

from photo_search.api.errors import ApiError
from photo_search.persons import albums, collections
from photo_search.persons.service import PersonService


def test_collection_labels_and_unassigned_filter_are_canonical_paged_and_versioned(
    api_env: ApiEnv,
) -> None:
    catalog = api_env.service.catalog
    people = [add_person(catalog, name=f"person-{index:03}") for index in range(85)]
    group = collections.create_collection(catalog, "日常合集")["collection"]
    collection_id, token = group["collection_id"], group["instance_token"]
    collections.edit_members(
        catalog, collection_id, instance_token=token, action="add", person_ids=people[:3]
    )

    all_people = albums.people_page(
        catalog, kind="all", q="", limit=100, cursor=None, sort="photos_desc"
    )
    labels = {person["person_id"]: person["collections"] for person in all_people["persons"]}
    assert all(labels[person_id] == [{"collection_id": collection_id, "name": "日常合集"}]
               for person_id in people[:3])
    assert labels[people[4]] == []

    page = albums.people_page(
        catalog, kind="named", q="", limit=40, cursor=None, sort="photos_desc", collection_state="none"
    )
    assert page["total"] == 82
    found = [person["person_id"] for person in page["persons"]]
    while page["has_more"]:
        page = albums.people_page(
            catalog, kind="named", q="", limit=40, cursor=page["next_cursor"],
            sort="photos_desc", collection_state="none",
        )
        found.extend(person["person_id"] for person in page["persons"])
    assert found == people[3:]
    assert len(set(found)) == 82

    named_search = albums.people_page(
        catalog, kind="named", q="person-00", limit=40, cursor=None,
        sort="photos_desc", collection_state="none",
    )
    assert named_search["total"] == 7
    PersonService.open(catalog).set_visibility(person_ids=[people[4]], hidden=True)
    hidden = albums.people_page(
        catalog, kind="named", q="", limit=40, cursor=None, sort="photos_desc",
        visibility="hidden", collection_state="none",
    )
    assert [person["person_id"] for person in hidden["persons"]] == [people[4]]

    source, target = people[:2]
    service = PersonService.open(catalog)
    merged = service.merge(
        target_person_id=target,
        target_expected_revision=1,
        sources=[{"person_id": source, "expected_revision": 1}],
        request_id="collection-manage-merge",
    )
    canonical_labels = albums.people_page(
        catalog, kind="all", q="", limit=100, cursor=None, collection_state="all"
    )["persons"]
    target_row = next(person for person in canonical_labels if person["person_id"] == target)
    assert target_row["collections"] == [{"collection_id": collection_id, "name": "日常合集"}]
    merged_none = albums.people_page(
        catalog, kind="named", q="", limit=100, cursor=None, collection_state="none"
    )
    assert target not in {person["person_id"] for person in merged_none["persons"]}
    service.undo(
        operation_id=merged["operation_id"],
        request_id="collection-manage-undo",
        expected_operation_revision=merged["operation_revision"],
    )
    undone_none = albums.people_page(
        catalog, kind="named", q="", limit=100, cursor=None, collection_state="none"
    )
    undone_unassigned = {person["person_id"] for person in undone_none["persons"]}
    assert source not in undone_unassigned and target not in undone_unassigned

    stale = albums.people_page(
        catalog, kind="named", q="", limit=1, cursor=None, collection_state="none"
    )
    collections.edit_members(
        catalog, collection_id, instance_token=token, action="add", person_ids=[people[3]]
    )
    with pytest.raises(ApiError) as rejected:
        albums.people_page(
            catalog, kind="named", q="", limit=1, cursor=stale["next_cursor"], collection_state="none"
        )
    assert rejected.value.status == 409

    photo = seed_library(api_env, {"unnamed.jpg": [(20, 20, 90, 90)]})["unnamed.jpg"]
    unnamed = add_person(catalog)
    add_link(catalog, photo["faces"][0], unnamed)
    assert albums.people_page(
        catalog, kind="unnamed", q="", limit=40, cursor=None, collection_state="none"
    )["total"] == 1
    assert albums.people_page(
        catalog, kind="unnamed", q="", limit=40, cursor=None,
        quality_view="clean", collection_state="none",
    )["total"] == 1
    collections.edit_members(
        catalog, collection_id, instance_token=token, action="add", person_ids=[unnamed]
    )
    assert albums.people_page(
        catalog, kind="unnamed", q="", limit=40, cursor=None, collection_state="none"
    )["total"] == 0
    before_remove = albums.people_page(
        catalog, kind="named", q="", limit=1, cursor=None, collection_state="none"
    )
    collections.edit_members(
        catalog, collection_id, instance_token=token, action="remove", person_ids=[unnamed]
    )
    with pytest.raises(ApiError) as rejected_remove:
        albums.people_page(
            catalog, kind="named", q="", limit=1, cursor=before_remove["next_cursor"],
            collection_state="none",
        )
    assert rejected_remove.value.status == 409
    collections.rename_collection(catalog, collection_id, "同学", instance_token=token)
    renamed = albums.people_page(
        catalog, kind="all", q="", limit=100, cursor=None, collection_state="all"
    )
    renamed_target = next(person for person in renamed["persons"] if person["person_id"] == target)
    assert renamed_target["collections"] == [{"collection_id": collection_id, "name": "同学"}]
    before_delete = albums.people_page(
        catalog, kind="named", q="", limit=1, cursor=None, collection_state="none"
    )
    collections.delete_collection(catalog, collection_id, instance_token=token)
    with pytest.raises(ApiError) as rejected_delete:
        albums.people_page(
            catalog, kind="named", q="", limit=1, cursor=before_delete["next_cursor"],
            collection_state="none",
        )
    assert rejected_delete.value.status == 409
