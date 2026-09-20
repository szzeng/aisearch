"""P3-05B: the pure rules of ADR-0008 (shape, digest, group snapshot).

These cases never need a catalog: they pin the request shape, the payload identity and the
group conflict vocabulary, so a drift in a bound or in a conflict reason shows up as a
one-line failure instead of hiding inside an integration fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from photo_search.catalog.errors import (
    PERSON_BATCH_TOO_LARGE,
    PERSON_SPLIT_CONFLICT,
    CatalogError,
)
from photo_search.persons import contracts
from photo_search.persons import split as split_module

ROOT = Path(__file__).resolve().parents[2]
COMPANION_PATH = (
    ROOT / "docs" / "decisions" / "contracts" / "p3-person-split-contracts.v1.json"
)
COMPANION = json.loads(COMPANION_PATH.read_text(encoding="utf-8"))


def selection(face_id: str, *, control: int = 0, assignment: int = 1) -> dict:
    return {
        "face_id": face_id,
        "expected_control_revision": control,
        "expected_assignment_revision": assignment,
    }


def test_the_module_constants_match_the_frozen_companion() -> None:
    assert split_module.CONTRACT_VERSION == "p3-person-split-contracts.v1"
    assert (
        COMPANION["$defs"]["ContractVersion"]["const"] == split_module.CONTRACT_VERSION
    )
    assert (
        COMPANION["$defs"]["SplitGroupItem"]["properties"]["entity_type"]["const"]
        == split_module.ENTITY_TYPE_SPLIT_GROUP
        == contracts.ENTITY_TYPE_SPLIT_GROUP
    )
    assert contracts.OPERATION_TYPE_SPLIT == "SPLIT"
    assert contracts.SPLIT_OPERATION_TYPES == ("SPLIT",)
    assert split_module.MAX_SPLIT_FACES == 500
    assert (
        COMPANION["$defs"]["PersonSplitRequest"]["properties"]["faces"]["maxItems"]
        == split_module.MAX_SPLIT_FACES
    )
    assert PERSON_SPLIT_CONFLICT in COMPANION["$defs"]["PersonSplitApiError"]["properties"][
        "code"
    ]["enum"]
    # The frozen P3-03 vocabulary is untouched by the split family.
    assert contracts.OPERATION_TYPES == (
        "RENAME",
        "MERGE_BY_NAME",
        "MERGE",
        "BATCH_MERGE",
        "UNDO",
    )
    assert contracts.WRITE_OPERATION_TYPES == (
        "RENAME",
        "MERGE_BY_NAME",
        "MERGE",
        "BATCH_MERGE",
    )
    assert contracts.UNDOABLE_OPERATION_TYPES == (
        contracts.WRITE_OPERATION_TYPES + contracts.FACE_OPERATION_TYPES
    )


def test_the_contract_version_and_the_source_revision_are_checked_exactly() -> None:
    assert split_module.check_contract_version(split_module.CONTRACT_VERSION) == (
        split_module.CONTRACT_VERSION
    )
    with pytest.raises(CatalogError) as version:
        split_module.check_contract_version("p3-person-contracts.v1")
    assert version.value.code == "VALIDATION_ERROR"
    assert version.value.details["field"] == "contract_version"

    assert split_module.check_source_revision(7) == 7
    for bad in (0, -1, "7", None, True):
        with pytest.raises(CatalogError) as error:
            split_module.check_source_revision(bad)
        assert error.value.code == "VALIDATION_ERROR"
        assert error.value.details["field"] == "source_expected_revision"


def test_the_face_list_is_sorted_and_every_duplicate_or_oversize_is_refused() -> None:
    ordered = split_module.check_faces(
        [selection("b" * 64, assignment=2), selection("a" * 64)]
    )
    assert [item["face_id"] for item in ordered] == ["a" * 64, "b" * 64]
    assert ordered[0]["expected_assignment_revision"] == 1

    with pytest.raises(CatalogError) as empty:
        split_module.check_faces([])
    assert empty.value.code == "VALIDATION_ERROR"
    with pytest.raises(CatalogError) as duplicate:
        split_module.check_faces([selection("a" * 64), selection("a" * 64)])
    assert duplicate.value.code == "VALIDATION_ERROR"
    assert duplicate.value.details["face_ids"] == ["a" * 64]
    with pytest.raises(CatalogError) as oversized:
        split_module.check_faces([selection(f"{index:064x}") for index in range(501)])
    assert oversized.value.code == PERSON_BATCH_TOO_LARGE
    assert oversized.value.details["max_batch_size"] == 500
    with pytest.raises(CatalogError) as zero_link:
        split_module.check_faces([selection("a" * 64, assignment=0)])
    assert zero_link.value.code == "VALIDATION_ERROR"
    assert zero_link.value.details["field"] == "expected_assignment_revision"
    with pytest.raises(CatalogError) as bad_face:
        split_module.check_faces([selection("NOT-HEX")])
    assert bad_face.value.code == "VALIDATION_ERROR"


def test_the_payload_digest_is_order_free_and_names_the_requested_source() -> None:
    first = split_module.check_faces([selection("a" * 64), selection("b" * 64, assignment=2)])
    second = split_module.check_faces([selection("b" * 64, assignment=2), selection("a" * 64)])
    digest = split_module.split_digest(
        requested_source_person_id=4, source_expected_revision=7, faces=first
    )
    assert digest == split_module.split_digest(
        requested_source_person_id=4, source_expected_revision=7, faces=second
    )
    assert digest != split_module.split_digest(
        requested_source_person_id=5, source_expected_revision=7, faces=first
    )
    assert digest != split_module.split_digest(
        requested_source_person_id=4, source_expected_revision=8, faces=first
    )
    assert digest != split_module.split_digest(
        requested_source_person_id=4, source_expected_revision=7, faces=first[:1]
    )
    # The endpoint family and the contract version are part of the identity.
    assert split_module.ENDPOINT_SPLIT == "persons.split"
    assert len(digest) == 64


def group_bundle(
    person_id: int = 7,
    *,
    role: str = "SOURCE",
    revision: int = 2,
    merged_into: int | None = None,
    members: list[tuple[str, int, str, int]] | None = None,
) -> dict:
    return {
        "role": role,
        "exists": True,
        "person_id": person_id,
        "name": None,
        "name_key": None,
        "avatar_face_id": None,
        "merged_into": merged_into,
        "revision": revision,
        "aliases": [],
        "members": [
            {
                "face_id": face_id,
                "person_id": person,
                "assignment_kind": kind,
                "revision": link_revision,
                "last_operation_id": None,
            }
            for face_id, person, kind, link_revision in (members or [])
        ],
    }


def test_the_group_conflict_reason_follows_the_frozen_order() -> None:
    recorded = group_bundle(members=[("a", 7, "AUTO", 1)])
    assert split_module.group_conflict_reason(recorded, recorded) is None

    redirected = group_bundle(merged_into=9, revision=3, members=[("a", 7, "AUTO", 1)])
    assert split_module.group_conflict_reason(redirected, recorded) == "group_redirected"

    renamed = group_bundle(revision=3, members=[("a", 7, "AUTO", 1)])
    renamed["name"] = "爸爸"
    assert split_module.group_conflict_reason(renamed, recorded) == "group_changed"

    moved_member = group_bundle(members=[("a", 7, "AUTO", 2)])
    assert (
        split_module.group_conflict_reason(moved_member, recorded)
        == "effective_members_changed"
    )
    added_member = group_bundle(members=[("a", 7, "AUTO", 1), ("b", 7, "AUTO", 1)])
    assert (
        split_module.group_conflict_reason(added_member, recorded)
        == "effective_members_changed"
    )
    # A suggestion-only movement is not a member movement at all.
    assert split_module.group_conflict_reason(recorded, recorded) is None


def test_a_created_group_is_recorded_as_not_existing_before_the_transaction() -> None:
    placeholder = split_module.created_placeholder(12)
    assert placeholder == {"role": "CREATED", "exists": False, "person_id": 12}
    after = group_bundle(12, role="CREATED", revision=1, members=[("a", 12, "MANUAL", 1)])
    answer = split_module.group_item_answer("CREATED", placeholder, after)
    assert answer == {
        "entity_type": "SPLIT_GROUP",
        "entity_id": 12,
        "role": "CREATED",
        "changed": True,
        "revision": 1,
        "merged_into": None,
    }
    assert split_module.group_bundle_differences(after, placeholder) == ["exists"]
    restored = group_bundle(12, role="CREATED", revision=2, merged_into=7)
    assert split_module.group_item_answer("CREATED", after, restored)["merged_into"] == 7


def test_the_group_snapshot_is_canonical_json_without_private_facts() -> None:
    bundle = group_bundle(members=[("a", 7, "AUTO", 1)])
    rendered = split_module.group_bundle_json(bundle)
    assert rendered == split_module.group_bundle_json(
        split_module.load_group_bundle(rendered)
    )
    assert split_module.load_group_bundle(rendered) == bundle
    for fragment in ("bbox", "landmark", "vector", "confidence", "similarity", "path"):
        assert fragment not in rendered
