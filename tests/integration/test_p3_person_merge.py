"""P3-03: naming, same-name merging, explicit batch merging, redirects and compensation.

The authority rows are seeded directly (see :mod:`p3_person_fixtures`), because this file is
about the *person* semantics: one business use case behind a rename and a merge, no second
active person with one name, a preserved source kind, a bounded before/after snapshot and an
all-or-nothing undo. Every case runs inside a ``%TEMP%`` catalog.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from p3_person_fixtures import PersonHarness

from photo_search.catalog.errors import CatalogError
from photo_search.persons.contracts import name_key_for


@pytest.fixture()
def persons_env(tmp_path: Path) -> PersonHarness:
    harness = PersonHarness(tmp_path)
    try:
        yield harness
    finally:
        harness.close()


def two_assets(h: PersonHarness) -> tuple[int, int]:
    return h.add_asset("a.jpg"), h.add_asset("b.jpg")


def members(h: PersonHarness, person_id: int) -> list[str]:
    return sorted(
        str(row["face_id"])
        for row in h.catalog.read_all(
            "SELECT face_id FROM face_person_links WHERE person_id = ? "
            "AND decision = 'CONFIRMED'",
            (int(person_id),),
        )
    )


# --------------------------------------------------------------------------- naming
def test_a_rename_keeps_the_person_id_and_writes_the_versioned_key(
    persons_env: PersonHarness,
) -> None:
    h = persons_env
    asset = h.add_asset()
    face = h.add_face(asset)
    person = h.add_person()
    h.add_link(face, person)
    before_revision = h.filter_revision()

    result = h.rename(person, "  \u5f20\u4e09\t", "req-rename", h.revision_of(person))

    row = h.person(person)
    assert int(row["person_id"]) == person
    assert str(row["name"]) == "\u5f20\u4e09"
    assert str(row["name_key"]) == name_key_for("\u5f20\u4e09")
    assert int(row["merged_into"] is None) == 1
    assert result["operation_type"] == "RENAME"
    assert result["changed"] is True
    assert result["replayed"] is False
    assert len(result["items"]) == 1
    assert h.filter_revision() == before_revision + 1
    assert h.filter_causes() == ["PERSON_CHANGED"]
    assert [(int(row["asset_id"]), str(row["cause"])) for row in h.queue_rows()] == [
        (asset, "PERSON_CHANGED")
    ]
    # The name is a label, never an identity: the face link did not move.
    assert members(h, person) == [face]


def test_a_name_another_active_group_owns_merges_instead_of_duplicating_it(
    persons_env: PersonHarness,
) -> None:
    h = persons_env
    first_asset, second_asset = two_assets(h)
    dad_face = h.add_face(first_asset)
    other_face = h.add_face(second_asset)
    dad = h.add_person(name="\u7238\u7238")
    other = h.add_person()
    h.add_link(dad_face, dad)
    h.add_link(other_face, other)
    dad_revision = h.revision_of(dad)

    result = h.rename(other, "\u7238\u7238 ", "req-merge-by-name", h.revision_of(other))

    assert result["operation_type"] == "MERGE_BY_NAME"
    assert str(h.person(other)["merged_into"]) == str(dad)
    # The source row is never deleted and keeps its own revision history.
    assert h.person(other) is not None
    assert h.revision_of(other) == 2
    assert h.revision_of(dad) == dad_revision + 1
    assert members(h, dad) == sorted([dad_face, other_face])
    active = h.catalog.read_all(
        "SELECT person_id FROM persons WHERE name_key = ? AND merged_into IS NULL",
        (name_key_for("\u7238\u7238"),),
    )
    assert [int(row["person_id"]) for row in active] == [dad]
    assert h.view(other)["canonical_person_id"] == dad


def test_saving_the_same_name_again_is_an_answered_noop(persons_env: PersonHarness) -> None:
    h = persons_env
    asset = h.add_asset()
    person = h.add_person()
    h.add_link(h.add_face(asset), person)
    h.rename(person, "\u5988\u5988", "req-1", h.revision_of(person))
    revision = h.revision_of(person)
    filter_revision = h.filter_revision()
    queue = [tuple(row) for row in h.queue_rows()]

    replay = h.rename(person, "\u5988\u5988", "req-2", revision)

    assert replay["changed"] is False
    assert h.revision_of(person) == revision
    assert h.filter_revision() == filter_revision
    assert [tuple(row) for row in h.queue_rows()] == queue
    # A no-op still records the request, so the caller's retry is provably answered.
    assert [str(row["type"]) for row in h.operations()] == ["RENAME", "RENAME"]


# --------------------------------------------------------------------------- merging
def test_an_explicit_merge_of_one_source_uses_the_single_merge_type(
    persons_env: PersonHarness,
) -> None:
    h = persons_env
    asset = h.add_asset()
    target = h.add_person(name="\u7238\u7238")
    source = h.add_person()
    h.add_link(h.add_face(asset), source)
    target_revision = h.revision_of(target)

    result = h.merge(
        target,
        [(source, h.revision_of(source))],
        "req-merge",
        target_revision=target_revision,
    )

    assert result["operation_type"] == "MERGE"
    assert result["changed"] is True
    assert h.revision_of(target) == target_revision + 1
    assert str(h.person(source)["merged_into"]) == str(target)


def test_a_batch_merge_is_one_operation_and_one_filter_revision(
    persons_env: PersonHarness,
) -> None:
    h = persons_env
    target = h.add_person(name="\u7238\u7238")
    sources = []
    for index in range(3):
        asset = h.add_asset(f"photo-{index}.jpg")
        person = h.add_person()
        h.add_link(h.add_face(asset), person)
        sources.append(person)
    before = h.filter_revision()

    result = h.merge(
        target,
        [(person, h.revision_of(person)) for person in sources],
        "req-batch",
        target_revision=h.revision_of(target),
    )

    assert result["operation_type"] == "BATCH_MERGE"
    assert len(result["items"]) == 4
    assert h.filter_revision() == before + 1
    assert h.filter_causes() == ["PERSON_CHANGED"]
    assert [str(row["type"]) for row in h.operations()] == ["BATCH_MERGE"]
    assert len(members(h, target)) == 3
    assert all(str(h.person(person)["merged_into"]) == str(target) for person in sources)


def test_a_batch_merge_with_a_stale_source_is_all_or_nothing(
    persons_env: PersonHarness,
) -> None:
    h = persons_env
    target = h.add_person(name="\u7238\u7238")
    first = h.add_person()
    second = h.add_person()
    for index, person in enumerate((first, second)):
        asset = h.add_asset(f"shot-{index}.jpg")
        h.add_link(h.add_face(asset), person)
    before = h.bundle(target)
    before_filter = h.filter_revision()

    with pytest.raises(CatalogError) as error:
        h.merge(
            target,
            [(first, h.revision_of(first)), (second, h.revision_of(second) + 5)],
            "req-bad-batch",
            target_revision=h.revision_of(target),
        )

    assert error.value.code == "PERSON_REVISION_CONFLICT"
    assert h.operations() == []
    assert h.bundle(target) == before
    assert h.person(first)["merged_into"] is None
    assert h.filter_revision() == before_filter


def test_a_merge_deduplicates_the_links_and_keeps_the_source_kind(
    persons_env: PersonHarness,
) -> None:
    h = persons_env
    asset = h.add_asset()
    face = h.add_face(asset)
    target = h.add_person(name="\u7238\u7238")
    source = h.add_person()
    # The same face is a suggestion for the target and an effective link of the source.
    h.add_link(face, target, decision="SUGGESTED", reason="BORDERLINE", revision=4)
    h.add_link(face, source, decision="CONFIRMED", assignment_kind="AUTO", revision=2)

    h.merge(
        target,
        [(source, h.revision_of(source))],
        "req-dedupe",
        target_revision=h.revision_of(target),
    )

    rows = [
        row
        for row in h.links()
        if str(row["face_id"]) == face and int(row["person_id"]) == target
    ]
    assert len(rows) == 1
    assert str(rows[0]["decision"]) == "CONFIRMED"
    # A batch merge never promotes an automatic link into a human one.
    assert str(rows[0]["assignment_kind"]) == "AUTO"
    assert float(rows[0]["confidence"]) == pytest.approx(0.9)
    confirmed = h.catalog.read_all(
        "SELECT face_id FROM face_person_links WHERE decision = 'CONFIRMED' "
        "AND face_id = ?",
        (face,),
    )
    assert len(confirmed) == 1


def test_a_manual_link_keeps_its_manual_origin_through_a_merge(
    persons_env: PersonHarness,
) -> None:
    h = persons_env
    asset = h.add_asset()
    manual_face = h.add_face(asset, ordinal=0)
    auto_face = h.add_face(asset, ordinal=1)
    target = h.add_person()
    source = h.add_person()
    h.add_link(auto_face, source, decision="CONFIRMED", assignment_kind="AUTO")
    h.add_link(manual_face, source, decision="CONFIRMED", assignment_kind="MANUAL")

    h.merge(
        target,
        [(source, h.revision_of(source))],
        "req-kind",
        target_revision=h.revision_of(target),
    )

    kinds = {
        str(row["face_id"]): str(row["assignment_kind"])
        for row in h.links()
        if int(row["person_id"]) == target
    }
    assert kinds == {manual_face: "MANUAL", auto_face: "AUTO"}


def test_aliases_move_to_the_target_and_are_deduplicated_inside_it(
    persons_env: PersonHarness,
) -> None:
    h = persons_env
    target = h.add_person(name="\u7238\u7238")
    first = h.add_person(name="\u8001\u7238")
    second = h.add_person(name="\u7238\u7238\u5b9d\u5b9d")
    # First merge *second* into *first*: "爸爸宝宝" becomes both the alias of `first` and the
    # formal name of the now-inactive `second`. Merging `first` into the target afterwards
    # therefore offers the same alias twice and must keep exactly one row.
    h.merge(
        first,
        [(second, h.revision_of(second))],
        "req-inner",
        target_revision=h.revision_of(first),
    )
    assert {
        str(row["normalized_alias"])
        for row in h.catalog.read_all(
            "SELECT normalized_alias FROM person_aliases WHERE person_id = ?", (first,)
        )
    } == {"\u7238\u7238\u5b9d\u5b9d"}

    h.merge(
        target,
        [(first, h.revision_of(first))],
        "req-alias",
        target_revision=h.revision_of(target),
    )

    target_aliases = sorted(
        (str(row["normalized_alias"]), str(row["alias"]))
        for row in h.catalog.read_all(
            "SELECT alias, normalized_alias FROM person_aliases WHERE person_id = ?", (target,)
        )
    )
    # Each source contributes its formal name once; the target's own name is never an alias.
    assert target_aliases == [
        ("\u7238\u7238\u5b9d\u5b9d", "\u7238\u7238\u5b9d\u5b9d"),
        ("\u8001\u7238", "\u8001\u7238"),
    ]
    assert all(
        int(row["person_id"]) == target
        for row in h.catalog.read_all("SELECT person_id FROM person_aliases")
    )
