"""The provisional hold, its release and its persistence (ADR-0010 §3.4, P3-08B).

The hold is the whole point of the task: an undecided redetection review must stop the
automatic association from filling the new face back into a person, and releasing it must
leave the asset queued so the normal pass can run again.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from p3_redetection_fixtures import RedetectionHarness, reject_decision

from photo_search.persons import mapping as mapping_module


@pytest.fixture()
def harness(tmp_path: Path) -> RedetectionHarness:
    instance = RedetectionHarness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def _open_review(harness: RedetectionHarness) -> dict[str, object]:
    asset_id = harness.add_asset("待确认.jpg")
    dad = harness.add_person(name="爸爸")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    new_face = harness.write_pair(asset_id)
    harness.seed_candidate_index([anchor, new_face])
    return {"asset_id": asset_id, "anchor": anchor, "new_face": new_face, "dad": dad}


def test_hold_blocks_the_association_write_gate(harness: RedetectionHarness) -> None:
    """MAP-029: inside the association transaction the held face is never written."""
    seeded = _open_review(harness)
    asset_id = int(seeded["asset_id"])
    new_face = str(seeded["new_face"])
    decisions = harness.reconcile_asset(asset_id)
    assert [item["action"] for item in decisions] == ["PROVISIONAL_HOLD"]
    assert harness.effective_link(new_face) is None
    assert harness.control(new_face) is None
    assert harness.face_links(new_face) == []
    # The durable queue row survives the pass, so the release can still be picked up.
    row = harness.queue_row(asset_id)
    assert row is not None
    # The hold set is exactly the live candidate of the open group.
    held = mapping_module.hold_face_ids(harness.catalog.connection, asset_id=asset_id)
    assert held == {new_face}


def test_release_requeues_the_released_faces(harness: RedetectionHarness) -> None:
    """MAP-030: after a rejection the face is processed by the *next* normal pass."""
    seeded = _open_review(harness)
    asset_id = int(seeded["asset_id"])
    anchor = str(seeded["anchor"])
    new_face = str(seeded["new_face"])
    harness.decide(asset_id, [reject_decision(anchor, revision=harness.group_revision(anchor))])
    # The decision itself must not pick a person for the released face...
    assert harness.effective_link(new_face) is None
    # ...but the asset is queued again and the ordinary pass now handles it.
    assert harness.queue_row(asset_id) is not None
    assert mapping_module.hold_face_ids(harness.catalog.connection, asset_id=asset_id) == set()
    decisions = harness.reconcile_asset(asset_id)
    actions = {str(item["face_id"]): str(item["action"]) for item in decisions}
    assert actions.get(new_face) == "NEW_GROUP"
    assert harness.effective_link(new_face) is not None


def test_stale_is_decided_before_the_hold_set(harness: RedetectionHarness) -> None:
    """MAP-031: an invalidated candidate leaves the hold instead of pinning it."""
    seeded = _open_review(harness)
    asset_id = int(seeded["asset_id"])
    anchor = str(seeded["anchor"])
    new_face = str(seeded["new_face"])
    assert mapping_module.hold_face_ids(harness.catalog.connection, asset_id=asset_id) == {
        new_face
    }

    harness.write_face_document(asset_id, [])
    assert mapping_module.hold_face_ids(harness.catalog.connection, asset_id=asset_id) == set()
    row = harness.mapping_of(anchor, new_face)
    assert str(row["state"]) == mapping_module.STATE_STALE
    # A candidate the hold must not keep alive is really gone.
    assert harness.catalog.read_one(
        "SELECT 1 AS present FROM faces WHERE face_id = ?", (new_face,)
    ) is None
    assert str(harness.group(anchor)["candidate_state"]) == mapping_module.CANDIDATE_NONE


def test_undo_reapplies_the_hold_and_requeues(harness: RedetectionHarness) -> None:
    """MAP-032: ``undo -> OPEN`` restores the hold; it is never a release."""
    seeded = _open_review(harness)
    asset_id = int(seeded["asset_id"])
    anchor = str(seeded["anchor"])
    new_face = str(seeded["new_face"])
    confirmed = harness.decide(
        asset_id,
        [
            {
                "old_face_id": anchor,
                "expected_review_revision": harness.group_revision(anchor),
                "decision": "confirm",
                "targets": [{"new_face_id": new_face, "inherits_person": True}],
            }
        ],
    )
    assert str(harness.control(new_face)["mode"]) == "MANUAL_ASSIGNED"
    undo = harness.undo(
        confirmed["operation_id"],
        harness.next_mapping_request(),
        int(confirmed["operation_revision"]),
    )
    assert harness.group(anchor)["state"] == mapping_module.GROUP_OPEN
    assert mapping_module.hold_face_ids(harness.catalog.connection, asset_id=asset_id) == {
        new_face
    }
    assert harness.queue_row(asset_id) is not None
    # The very next association pass must skip the restored candidate again.
    decisions = harness.reconcile_asset(asset_id)
    actions = {str(item["face_id"]): str(item["action"]) for item in decisions}
    assert actions.get(new_face) == "PROVISIONAL_HOLD"
    assert harness.effective_link(new_face) is None
    assert undo["catalog_filter_revision"] >= confirmed["catalog_filter_revision"]


def test_an_open_review_keeps_its_hold_across_a_second_pass(harness: RedetectionHarness) -> None:
    """Running the association twice cannot erode the hold either."""
    seeded = _open_review(harness)
    asset_id = int(seeded["asset_id"])
    new_face = str(seeded["new_face"])
    for _ in range(2):
        harness.reconcile_asset(asset_id)
        assert harness.effective_link(new_face) is None
        assert harness.control(new_face) is None
        assert harness.queue_row(asset_id) is not None
