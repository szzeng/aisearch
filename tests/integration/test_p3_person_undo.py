"""P3-03: compensation — a merge and a rename are undoable, a later edit is not.

The compensating undo is a *new* operation: every restored person and link revision moves
forward, the original flips to ``UNDONE`` with an author, and a request whose target was
edited afterwards is refused whole and leaves the database untouched.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from p3_person_fixtures import PersonHarness

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import CatalogError
from photo_search.persons.service import PersonService


@pytest.fixture()
def persons_env(tmp_path: Path) -> PersonHarness:
    harness = PersonHarness(tmp_path)
    try:
        yield harness
    finally:
        harness.close()


def seed_group(h: PersonHarness, name: str | None, *, ordinal: int = 0) -> tuple[int, str]:
    person = h.add_person(name=name)
    asset = h.add_asset(f"{name or 'unnamed'}-{ordinal}.jpg")
    face = h.add_face(asset)
    h.add_link(face, person, revision=2)
    return person, face


def test_undoing_a_rename_restores_the_previous_name_and_moves_the_revision_forward(
    persons_env: PersonHarness,
) -> None:
    h = persons_env
    person, _ = seed_group(h, None)
    before_filter = h.filter_revision()
    rename = h.rename(person, "\u5988\u5988", "req-rename", 1)
    after_rename = h.revision_of(person)

    undo = h.undo(rename["operation_id"], "req-undo", rename["operation_revision"])

    assert undo["operation_type"] == "UNDO"
    assert undo["undo_of_operation_id"] == rename["operation_id"]
    assert undo["changed"] is True
    assert h.person(person)["name"] is None
    assert h.person(person)["name_key"] is None
    # Never a rewind: the restored row is a new revision.
    assert h.revision_of(person) == after_rename + 1
    original = h.catalog.read_one(
        "SELECT state, undone_by FROM person_operations WHERE operation_id = ?",
        (rename["operation_id"],),
    )
    assert str(original["state"]) == "UNDONE"
    assert str(original["undone_by"]) == undo["operation_id"]
    assert h.filter_revision() == before_filter + 2
    assert h.filter_causes() == ["PERSON_CHANGED", "PERSON_CHANGED"]


def test_undoing_a_merge_restores_the_source_group_and_its_links(
    persons_env: PersonHarness,
) -> None:
    h = persons_env
    target, target_face = seed_group(h, "\u7238\u7238")
    source, source_face = seed_group(h, "\u8001\u7238")
    merge = h.merge(
        target, [(source, 1)], "req-merge", target_revision=h.revision_of(target)
    )
    assert str(h.person(source)["merged_into"]) == str(target)
    assert {
        str(row["normalized_alias"])
        for row in h.catalog.read_all("SELECT normalized_alias FROM person_aliases")
    } == {"\u8001\u7238"}

    undo = h.undo(merge["operation_id"], "req-undo", merge["operation_revision"])

    assert undo["changed"] is True
    assert h.person(source)["merged_into"] is None
    assert str(h.person(source)["name"]) == "\u8001\u7238"
    # The alias moved back to its owner; the target no longer carries it.
    assert h.catalog.read_all("SELECT 1 FROM person_aliases WHERE person_id = ?", (target,)) == []
    source_links = h.catalog.read_all(
        "SELECT face_id, decision, revision FROM face_person_links WHERE person_id = ?",
        (source,),
    )
    assert [str(row["face_id"]) for row in source_links] == [source_face]
    assert int(source_links[0]["revision"]) > 2
    target_links = h.catalog.read_all(
        "SELECT face_id FROM face_person_links WHERE person_id = ?", (target,)
    )
    assert [str(row["face_id"]) for row in target_links] == [target_face]
    assert h.view(source)["redirected"] is False
    assert h.person(target)["avatar_face_id"] is None


def test_an_undo_is_refused_when_the_target_gained_members_afterwards(
    persons_env: PersonHarness,
) -> None:
    h = persons_env
    target, _ = seed_group(h, "\u7238\u7238")
    first, _ = seed_group(h, None, ordinal=1)
    second, _ = seed_group(h, None, ordinal=2)
    merge = h.merge(
        target, [(first, 1)], "req-merge-1", target_revision=h.revision_of(target)
    )
    h.merge(target, [(second, 1)], "req-merge-2", target_revision=h.revision_of(target))
    before = h.bundle(target)
    operation_count = len(h.operations())

    with pytest.raises(CatalogError) as error:
        h.undo(merge["operation_id"], "req-undo", merge["operation_revision"])

    assert error.value.code == "PERSON_OPERATION_NOT_UNDOABLE"
    conflicts = error.value.details["conflicts"]
    assert conflicts == [
        {"entity_type": "PERSON_BUNDLE", "entity_id": target, "reason": "revision,links"}
    ]
    assert h.bundle(target) == before
    assert len(h.operations()) == operation_count
    assert str(h.person(first)["merged_into"]) == str(target)


def test_an_undo_of_an_undo_is_refused(persons_env: PersonHarness) -> None:
    h = persons_env
    person, _ = seed_group(h, None)
    rename = h.rename(person, "\u5988\u5988", "req-rename", 1)
    undo = h.undo(rename["operation_id"], "req-undo", rename["operation_revision"])
    before = h.bundle(person)

    with pytest.raises(CatalogError) as error:
        h.undo(undo["operation_id"], "req-undo-2", undo["operation_revision"])

    assert error.value.code == "PERSON_OPERATION_NOT_UNDOABLE"
    assert h.bundle(person) == before


def test_undoing_an_unknown_operation_is_a_not_found(persons_env: PersonHarness) -> None:
    h = persons_env
    with pytest.raises(CatalogError) as error:
        h.undo("f" * 64, "req-unknown", 1)
    assert error.value.code == "PERSON_NOT_FOUND"
    assert error.value.details == {"operation_id": "f" * 64}


def test_a_replayed_undo_returns_the_same_compensating_operation(
    persons_env: PersonHarness,
) -> None:
    h = persons_env
    person, _ = seed_group(h, None)
    rename = h.rename(person, "\u5988\u5988", "req-rename", 1)
    undo = h.undo(rename["operation_id"], "req-undo", rename["operation_revision"])
    revision = h.revision_of(person)

    replay = h.undo(rename["operation_id"], "req-undo", rename["operation_revision"])

    assert replay["replayed"] is True
    assert replay["operation_id"] == undo["operation_id"]
    assert h.revision_of(person) == revision


def test_a_second_undo_of_the_same_operation_is_refused(persons_env: PersonHarness) -> None:
    h = persons_env
    person, _ = seed_group(h, None)
    rename = h.rename(person, "\u5988\u5988", "req-rename", 1)
    h.undo(rename["operation_id"], "req-undo", rename["operation_revision"])

    with pytest.raises(CatalogError) as error:
        h.undo(rename["operation_id"], "req-undo-2", rename["operation_revision"])

    assert error.value.code == "PERSON_OPERATION_NOT_UNDOABLE"
    assert error.value.details["status"] == "UNDONE"


def test_a_wrong_expected_operation_revision_is_refused(persons_env: PersonHarness) -> None:
    h = persons_env
    person, _ = seed_group(h, None)
    rename = h.rename(person, "\u5988\u5988", "req-rename", 1)

    with pytest.raises(CatalogError) as error:
        h.undo(rename["operation_id"], "req-undo", rename["operation_revision"] + 4)

    assert error.value.code == "PERSON_OPERATION_NOT_UNDOABLE"
    assert error.value.details["current_revision"] == rename["operation_revision"]


def test_a_semantic_noop_is_undoable_and_moves_nothing(persons_env: PersonHarness) -> None:
    h = persons_env
    person, _ = seed_group(h, "\u5988\u5988")
    noop = h.rename(person, "\u5988\u5988", "req-noop", h.revision_of(person))
    assert noop["changed"] is False
    before_filter = h.filter_revision()
    before_revision = h.revision_of(person)

    undo = h.undo(noop["operation_id"], "req-undo", noop["operation_revision"])

    assert undo["changed"] is False
    assert h.revision_of(person) == before_revision
    assert h.filter_revision() == before_filter


def test_an_undo_survives_a_process_restart(persons_env: PersonHarness) -> None:
    """The operation log is durable: a fresh process can still compensate."""
    h = persons_env
    person, _ = seed_group(h, None)
    rename = h.rename(person, "\u5988\u5988", "req-rename", 1)
    h.close()

    reopened = Catalog.open(data_root=h.data_root, clock=h.clock, lease_seconds=60)
    try:
        service = PersonService(reopened, clock=h.clock)
        undo = service.undo(
            operation_id=rename["operation_id"],
            request_id="req-undo",
            expected_operation_revision=rename["operation_revision"],
        )
        assert undo["changed"] is True
        row = reopened.read_one(
            "SELECT name FROM persons WHERE person_id = ?", (person,)
        )
        assert row["name"] is None
    finally:
        reopened.close()
