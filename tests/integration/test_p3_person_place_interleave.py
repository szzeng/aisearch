"""Person and place corrections interleaved on one library (P3-08B cross-family slice).

Architecture §12.4 items 29～33 ask for the crossing of the two curations: each keeps its own
authority, both share the one ``catalog_filter_revision`` counter, and a later search session
opened before either edit has to be answered as stale instead of mixing versions.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from p3_redetection_fixtures import RedetectionHarness, confirm_decision

from photo_search.persons import mapping as mapping_module
from photo_search.places.service import PlaceService


@pytest.fixture()
def harness(tmp_path: Path) -> RedetectionHarness:
    instance = RedetectionHarness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def _place_service(harness: RedetectionHarness) -> PlaceService:
    return PlaceService(harness.catalog, clock=harness.clock)


def test_authorities_do_not_overwrite_each_other(harness: RedetectionHarness) -> None:
    """X-001: person merge → place assign → face correction."""
    places = _place_service(harness)
    place = places.create_place(kind="city", canonical_name="杭州")
    asset_id = harness.add_asset("交错.jpg")
    target = harness.add_person(name="爸爸")
    source = harness.add_person(name="老爸")
    face = harness.add_face(asset_id, ordinal=0)
    harness.add_embedding(face)
    harness.add_link(face, target, assignment_kind="MANUAL", revision=2)
    harness.set_control(face, "MANUAL_ASSIGNED")
    other_asset = harness.add_asset("另一个人.jpg")
    other_face = harness.add_face(other_asset, ordinal=0)
    harness.add_embedding(other_face)
    harness.add_link(other_face, source, revision=2)

    revisions: list[int] = []
    merge = harness.merge(target, [(source, 1)], "interleave-merge", target_revision=1)
    revisions.append(harness.filter_revision())
    assert merge["changed"] is True

    placed = places.write_assignment(
        asset_id=asset_id,
        action="ASSIGN",
        target_place_id=int(place["place_id"]),
        request_id="interleave-place",
        expected_revision=0,
    )
    revisions.append(harness.filter_revision())
    assert placed["changed"] is True

    corrected = harness.edit(
        other_face,
        "assign",
        expected_revision=harness.control_revision(other_face),
        target_person_id=target,
    )
    revisions.append(harness.filter_revision())
    assert corrected["changed"] is True

    # Each authority kept its own fact and the one filter counter only ever moved forward.
    assert revisions == sorted(revisions)
    assert len(set(revisions)) == 3
    assert int(harness.effective_link(other_face)["person_id"]) == target
    assert str(harness.control(other_face)["mode"]) == "MANUAL_ASSIGNED"
    state = places.asset_place(asset_id)
    assert state["assignment"] is not None
    assert int(state["assignment"]["place_id"]) == int(place["place_id"])
    assert str(harness.person(source)["merged_into"]) == str(target)
    assert str(harness.control(face)["mode"]) == "MANUAL_ASSIGNED"


def test_undo_across_families_is_independent(harness: RedetectionHarness) -> None:
    """X-003: a person undo and a place undo do not touch each other's facts."""
    places = _place_service(harness)
    place = places.create_place(kind="city", canonical_name="苏州")
    asset_id = harness.add_asset("两个撤销.jpg")
    person = harness.add_person(name=None)
    face = harness.add_face(asset_id, ordinal=0)
    harness.add_embedding(face)
    harness.add_link(face, person, revision=2)

    rename = harness.rename(person, "妈妈", "interleave-rename", 1)
    placed = places.write_assignment(
        asset_id=asset_id,
        action="ASSIGN",
        target_place_id=int(place["place_id"]),
        request_id="interleave-place-undo",
        expected_revision=0,
    )
    person_undo = harness.undo(
        rename["operation_id"], "interleave-undo-person", rename["operation_revision"]
    )
    place_undo = places.undo(
        operation_id=str(placed["operation_id"]),
        request_id="interleave-undo-place",
        expected_operation_revision=int(placed["operation_revision"]),
    )
    assert person_undo["changed"] is True
    assert place_undo["changed"] is True
    # The person went back to unnamed and the asset back to unassigned; neither family's
    # compensation touched the other's rows.
    assert harness.person(person)["name"] is None
    restored_place = places.asset_place(asset_id)["assignment"]
    assert restored_place is None or int(restored_place["place_id"] or 0) == 0
    assert not bool(restored_place and restored_place["manual_lock"])
    assert int(harness.effective_link(face)["revision"]) >= 2


def test_a_mapping_confirmation_moves_the_shared_filter_revision(
    harness: RedetectionHarness,
) -> None:
    """X-005 (P3-08B flavour): the mapping confirmation is one ``PERSON_CHANGED`` step.

    The search session of ADR-0009 §6 compares exactly this counter, so a page opened before
    the confirmation can no longer be continued as if nothing had happened. The mechanism is
    the one the existing P3-07C session suite pins
    (``test_every_person_edit_makes_the_old_session_stale_and_researching_works`` and
    ``test_a_catalog_that_keeps_moving_the_revision_refuses_instead_of_mixing``), which this
    task re-runs in full.
    """
    places = _place_service(harness)
    place = places.create_place(kind="city", canonical_name="南京")
    asset_id = harness.add_asset("会话.jpg")
    dad = harness.add_person(name="爸爸")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    new_face = harness.write_pair(asset_id)
    places.write_assignment(
        asset_id=asset_id,
        action="ASSIGN",
        target_place_id=int(place["place_id"]),
        request_id="session-place",
        expected_revision=0,
    )
    session_revision = harness.filter_revision()

    confirmed = harness.decide(
        asset_id,
        [confirm_decision(anchor, [(new_face, True)], revision=harness.group_revision(anchor))],
    )
    assert confirmed["catalog_filter_revision"] == session_revision + 1
    assert harness.filter_revision() == session_revision + 1
    assert harness.filter_causes()[-1] == "PERSON_CHANGED"

    # Restoring the automatic place does not undo the person fact, and the counter keeps
    # moving forward - a session opened before either edit is stale either way.
    restored = places.write_assignment(
        asset_id=asset_id,
        action="RESTORE_AUTO",
        target_place_id=None,
        request_id="session-place-restore",
        expected_revision=1,
    )
    assert restored["changed"] is True
    assert harness.filter_revision() > session_revision + 1
    assert str(harness.control(new_face)["mode"]) == "MANUAL_ASSIGNED"
    restored_place = places.asset_place(asset_id)["assignment"]
    assert restored_place is None or int(restored_place["place_id"] or 0) == 0
    assert not bool(restored_place and restored_place["manual_lock"])
    assert str(harness.group(anchor)["state"]) == mapping_module.GROUP_CONFIRMED
