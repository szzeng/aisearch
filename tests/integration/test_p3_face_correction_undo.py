"""P3-04B: compensating undo of one face operation (ADR-0007 §7 and §11.7~§11.9).

The compensation is a *new* ``UNDO`` operation over the recorded ``FACE_CONTROL`` before
snapshot, with every restored revision moving forward, and a whole-request refusal - zero
writes - as soon as one face carries a later human edit.
"""

from __future__ import annotations

import pytest
from p3_face_correction_fixtures import FaceCorrectionHarness

from photo_search.catalog.errors import (
    FACE_IDEMPOTENCY_CONFLICT,
    FACE_OPERATION_NOT_UNDOABLE,
    PERSON_OPERATION_NOT_UNDOABLE,
    CatalogError,
)


@pytest.fixture()
def env(tmp_path) -> FaceCorrectionHarness:
    harness = FaceCorrectionHarness(tmp_path)
    try:
        yield harness
    finally:
        harness.close()


def test_undoing_an_unassign_restores_the_automatic_link_and_lifts_the_denial(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    original = env.edit(seeded["face"], "unassign", expected_revision=0)
    assert env.effective_link(seeded["face"]) is None

    outcome = env.undo(original["operation_id"], env.next_request_id("undo"), 1)
    assert outcome["contract_version"] == "p3-face-correction-contracts.v1"
    assert outcome["operation_type"] == "UNDO"
    assert outcome["undo_of_operation_id"] == original["operation_id"]
    assert outcome["changed"] is True
    item = outcome["items"][0]
    assert item["entity_type"] == "FACE_CONTROL"
    assert item["face_id"] == seeded["face"]
    assert item["mode"] == "AUTO_ALLOWED"
    # The restored logical revision moves *forward* from the live one, never back to 0.
    assert item["revision"] == 2
    assert item["assigned_person_id"] == seeded["dad"]
    assert item["exclusions"] == {"activated": [], "deactivated": [seeded["dad"]]}
    link = env.effective_link(seeded["face"])
    assert link["assignment_kind"] == "AUTO" and link["person_id"] == seeded["dad"]
    assert env.active_exclusions(seeded["face"]) == []
    # The denial row is kept for the audit trail with active = 0.
    rows = {int(row["person_id"]): int(row["active"]) for row in env.exclusion_rows(seeded["face"])}
    assert rows[seeded["dad"]] == 0
    assert env.operation(original["operation_id"])["state"] == "UNDONE"
    assert env.operation(original["operation_id"])["undone_by"] == outcome["operation_id"]


def test_undoing_an_assign_restores_the_replaced_link_and_the_removed_denial(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    original = env.edit(
        seeded["face"], "assign", expected_revision=0, target_person_id=seeded["other"]
    )
    outcome = env.undo(original["operation_id"], env.next_request_id("undo"), 1)
    item = outcome["items"][0]
    assert item["mode"] == "AUTO_ALLOWED"
    assert item["revision"] == 2
    assert item["assigned_person_id"] == seeded["dad"]
    assert item["exclusions"] == {"activated": [], "deactivated": [seeded["dad"]]}
    links = env.face_links(seeded["face"])
    assert [(row["assignment_kind"], row["decision"]) for row in links if row["person_id"] == seeded["other"]] == []
    effective = env.effective_link(seeded["face"])
    assert effective["assignment_kind"] == "AUTO"
    assert effective["person_id"] == seeded["dad"]


def test_undoing_a_restore_auto_puts_the_manual_lock_back(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    env.edit(seeded["face"], "assign", expected_revision=0, target_person_id=seeded["other"])
    restore = env.edit(seeded["face"], "restore_auto", expected_revision=1)
    outcome = env.undo(restore["operation_id"], env.next_request_id("undo"), 2)
    item = outcome["items"][0]
    assert item["mode"] == "MANUAL_ASSIGNED"
    assert item["revision"] == 3
    assert item["assigned_person_id"] == seeded["other"]
    assert env.active_exclusions(seeded["face"]) == [seeded["dad"]]
    link = env.effective_link(seeded["face"])
    assert link["assignment_kind"] == "MANUAL" and link["person_id"] == seeded["other"]


def test_undo_refuses_the_whole_request_when_a_face_was_edited_afterwards(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    original = env.edit(seeded["face"], "unassign", expected_revision=0)
    env.edit(seeded["face"], "assign", expected_revision=1, target_person_id=seeded["other"])
    before = env.library_snapshot()
    operations = env.operations()
    with pytest.raises(CatalogError) as refused:
        env.undo(original["operation_id"], env.next_request_id("undo"), 1)
    assert refused.value.code == FACE_OPERATION_NOT_UNDOABLE
    conflicts = refused.value.details["conflicts"]
    assert conflicts[0]["entity_type"] == "FACE_CONTROL"
    assert conflicts[0]["face_id"] == seeded["face"]
    assert "control.mode" in conflicts[0]["reason"]
    assert env.library_snapshot() == before
    assert env.operations() == operations


def test_a_batch_undo_is_all_or_nothing(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    third_asset = env.add_asset("另一张.jpg")
    third = env.add_face(third_asset)
    env.add_person(name="奶奶")
    batch = env.batch(
        [
            {
                "face_id": seeded["face"],
                "action": "unassign",
                "target_person_id": None,
                "expected_revision": 0,
            },
            {
                "face_id": third,
                "action": "unassign",
                "target_person_id": None,
                "expected_revision": 0,
            },
        ]
    )
    # Only the second face moves on; the first one is untouched and must still be undoable.
    env.edit(third, "assign", expected_revision=1, target_person_id=seeded["other"])
    before = env.library_snapshot()
    batch_revision = int(env.operation(batch["operation_id"])["operation_revision"])
    with pytest.raises(CatalogError) as refused:
        env.undo(batch["operation_id"], env.next_request_id("undo"), batch_revision)
    assert refused.value.code == FACE_OPERATION_NOT_UNDOABLE
    assert [item["face_id"] for item in refused.value.details["conflicts"]] == [third]
    assert env.library_snapshot() == before
    assert env.operation(batch["operation_id"])["state"] == "COMMITTED"


def test_a_face_undo_of_a_no_op_changes_nothing_and_bumps_no_revision(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    noop = env.edit(seeded["face"], "restore_auto", expected_revision=0)
    assert noop["changed"] is False
    before_filter = env.filter_revision()
    outcome = env.undo(noop["operation_id"], env.next_request_id("undo"), 1)
    assert outcome["changed"] is False
    assert outcome["items"][0]["changed"] is False
    assert outcome["catalog_filter_revision"] == before_filter
    assert env.control(seeded["face"]) is None
    assert env.operation(noop["operation_id"])["state"] == "UNDONE"


def test_undoing_an_undo_and_a_stale_operation_revision_are_refused(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    original = env.edit(seeded["face"], "unassign", expected_revision=0)
    undo = env.undo(original["operation_id"], env.next_request_id("undo"), 1)
    with pytest.raises(CatalogError) as again:
        env.undo(undo["operation_id"], env.next_request_id("undo"), 2)
    assert again.value.code == FACE_OPERATION_NOT_UNDOABLE
    with pytest.raises(CatalogError) as stale:
        env.undo(original["operation_id"], env.next_request_id("undo"), 9)
    assert stale.value.code == FACE_OPERATION_NOT_UNDOABLE
    assert stale.value.details["expected_revision"] == 9
    with pytest.raises(CatalogError) as already:
        env.undo(original["operation_id"], env.next_request_id("undo"), 1)
    assert already.value.code == FACE_OPERATION_NOT_UNDOABLE
    assert already.value.details["status"] == "UNDONE"


def test_an_undo_replay_answers_with_the_recorded_operation(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    original = env.edit(seeded["face"], "unassign", expected_revision=0)
    request_id = env.next_request_id("undo")
    first = env.undo(original["operation_id"], request_id, 1)
    operations = len(env.operations())
    replay = env.undo(original["operation_id"], request_id, 1)
    assert replay["replayed"] is True
    assert replay["operation_id"] == first["operation_id"]
    assert replay["items"] == first["items"]
    assert len(env.operations()) == operations
    # A *person* operation was never involved; the mismatch still answers with a 409 code.
    with pytest.raises(CatalogError) as conflict:
        env.undo(original["operation_id"], request_id, 2)
    assert conflict.value.code == FACE_IDEMPOTENCY_CONFLICT


def test_a_p3_03_merge_after_a_face_edit_makes_the_older_face_undo_conflict(
    env: FaceCorrectionHarness,
) -> None:
    """ADR-0007 §11.9: the older operation is refused, not silently re-applied."""
    seeded = env.seeded_pair()
    face_op = env.edit(seeded["face"], "unassign", expected_revision=0)
    assert env.active_exclusions(seeded["face"]) == [seeded["dad"]]
    # A person-level merge moves 爸爸 into 叔叔; the denial row keeps its old id but now
    # resolves to the merged target, so the recorded face bundle is no longer the live one.
    env.merge(
        seeded["other"],
        [(seeded["dad"], env.revision_of(seeded["dad"]))],
        "m1",
        target_revision=env.revision_of(seeded["other"]),
    )
    before = env.library_snapshot()
    with pytest.raises(CatalogError) as refused:
        env.undo(face_op["operation_id"], env.next_request_id("undo"), 1)
    assert refused.value.code == FACE_OPERATION_NOT_UNDOABLE
    assert "exclusions" in refused.value.details["conflicts"][0]["reason"]
    assert env.library_snapshot() == before


def test_undoing_a_person_operation_still_answers_in_the_person_vocabulary(
    env: FaceCorrectionHarness,
) -> None:
    """The shared endpoint keeps the P3-03 behaviour for a P3-03 operation."""
    seeded = env.seeded_pair()
    rename = env.rename(seeded["dad"], "老爸", "r1", 1)
    assert rename["operation_type"] == "RENAME"
    with pytest.raises(CatalogError) as refused:
        env.undo(rename["operation_id"], env.next_request_id("undo"), 9)
    assert refused.value.code == PERSON_OPERATION_NOT_UNDOABLE


def test_an_operation_item_pointing_at_a_missing_face_is_a_named_conflict(
    env: FaceCorrectionHarness,
) -> None:
    from photo_search.persons.contracts import operation_id_for, payload_sha256

    request_id = "ghost-1"
    ghost = "a" * 64
    digest = payload_sha256({"endpoint": "faces.assignment", "face_id": ghost})
    with env.catalog.write() as connection:
        connection.execute(
            "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
            "source_person_ids_json, target_person_id, state, operation_revision, "
            "catalog_filter_revision, created_at, committed_at) "
            "VALUES (?, ?, ?, 'FACE_UNASSIGN', '[]', NULL, 'COMMITTED', 1, 0, 1, 1)",
            (operation_id_for(request_id), request_id, digest),
        )
        connection.execute(
            "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
            "entity_key, before_value_json, after_value_json, after_revision) "
            "VALUES (?, 0, 'FACE_CONTROL', ?, '{}', '{}', 0)",
            (operation_id_for(request_id), ghost),
        )
    with pytest.raises(CatalogError) as refused:
        env.undo(operation_id_for(request_id), env.next_request_id("undo"), 1)
    assert refused.value.code == FACE_OPERATION_NOT_UNDOABLE
    assert refused.value.details["conflicts"][0]["reason"] == "face_missing"
    assert env.operation(operation_id_for(request_id))["state"] == "COMMITTED"
