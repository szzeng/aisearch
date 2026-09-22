"""The human-knowledge transfer matrix and its compensation (ADR-0010 §4～§6, P3-08B).

Each case seeds a cross-generation pair through the production commit path, then submits a
real decision request. What is asserted is always the *authority*: the controls, the effective
links and the denials of the affected faces, the review states, and the one ``PERSON_CHANGED``
bump that may or may not happen.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from p3_redetection_fixtures import (
    RedetectionHarness,
    confirm_decision,
    make_record,
    shifted_bbox,
    shifted_landmarks,
)

from photo_search.catalog.errors import (
    FACE_MAPPING_CONFLICT,
    FACE_MAPPING_OPERATION_NOT_UNDOABLE,
    FACE_MAPPING_TARGET_CONTROLLED,
    CatalogError,
)
from photo_search.persons import mapping as mapping_module


@pytest.fixture()
def harness(tmp_path: Path) -> RedetectionHarness:
    instance = RedetectionHarness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def _two_targets(harness: RedetectionHarness, asset_id: int) -> tuple[str, str]:
    records = [
        make_record(ordinal=0, bbox=shifted_bbox(0.0), landmarks=shifted_landmarks(0.0)),
        make_record(
            ordinal=1,
            bbox=shifted_bbox(20.0),
            landmarks=shifted_landmarks(15.0, 15.0),
            seed=0.5,
        ),
    ]
    harness.write_face_document(asset_id, records)
    return (
        harness.new_face_id(asset_id, 0, shifted_bbox(0.0)),
        harness.new_face_id(asset_id, 1, shifted_bbox(20.0)),
    )


def test_confirmation_transfers_anchor_knowledge(harness: RedetectionHarness) -> None:
    """PERS-020: a confirmed mapping moves the lock, the person and the denials, once."""
    asset_id = harness.add_asset("爸爸.jpg")
    dad = harness.add_person(name="爸爸")
    uncle = harness.add_person(name="叔叔")
    anchor = harness.seed_anchor(asset_id, person_id=dad, excluded=[uncle])
    new_face = harness.write_pair(asset_id)
    filter_before = harness.filter_revision()

    result = harness.decide(
        asset_id,
        [
            confirm_decision(
                anchor, [(new_face, True)], revision=harness.group_revision(anchor)
            )
        ],
    )
    assert result["operation_type"] == mapping_module.OPERATION_CONFIRM
    assert result["changed"] is True
    assert result["catalog_filter_revision"] == filter_before + 1
    assert harness.filter_revision() == filter_before + 1
    assert harness.filter_causes()[-1] == "PERSON_CHANGED"

    control = harness.control(new_face)
    assert control is not None and str(control["mode"]) == "MANUAL_ASSIGNED"
    link = harness.effective_link(new_face)
    assert link is not None and str(link["assignment_kind"]) == "MANUAL"
    assert int(link["person_id"]) == dad
    assert harness.active_exclusions(new_face) == [uncle]
    # The audit anchor keeps everything it had.
    assert harness.control(anchor) is not None
    assert int(harness.effective_link(anchor)["person_id"]) == dad
    assert harness.active_exclusions(anchor) == [uncle]
    group = harness.group(anchor)
    assert str(group["state"]) == mapping_module.GROUP_CONFIRMED
    row = harness.mapping_of(anchor, new_face)
    assert str(row["state"]) == mapping_module.STATE_CONFIRMED
    assert int(row["inherits_person"]) == 1
    # A face correction never writes the image vector outbox (ADR-0010 §6).
    assert harness.index_outbox_rows() == 0


def test_split_mapping_keeps_one_inheritor_and_copies_exclusions(
    harness: RedetectionHarness,
) -> None:
    """MAP-008: one old face, two new faces, exactly one inheritor."""
    asset_id = harness.add_asset("拆开.jpg")
    dad = harness.add_person(name="爸爸")
    uncle = harness.add_person(name="叔叔")
    anchor = harness.seed_anchor(asset_id, person_id=dad, excluded=[uncle])
    first, second = _two_targets(harness, asset_id)
    result = harness.decide(
        asset_id,
        [
            confirm_decision(
                anchor,
                [(first, True), (second, False)],
                revision=harness.group_revision(anchor),
            )
        ],
    )
    assert result["changed"] is True
    assert str(harness.control(first)["mode"]) == "MANUAL_ASSIGNED"
    assert int(harness.effective_link(first)["person_id"]) == dad
    assert harness.active_exclusions(first) == [uncle]
    assert str(harness.control(second)["mode"]) == "MANUAL_UNASSIGNED"
    assert harness.effective_link(second) is None
    assert harness.active_exclusions(second) == [uncle]
    live = [
        row
        for row in harness.face_links(first) + harness.face_links(second)
        if str(row["decision"]) == "CONFIRMED"
    ]
    assert len(live) == 1
    # Both named targets are *confirmed as the successor face*; only one of them carries the
    # positive person knowledge, and the other is the conservative ``MANUAL_UNASSIGNED``
    # branch of ADR-0010 §4.3.
    rows = {str(row["new_face_id"]): row for row in harness.mappings(anchor)}
    assert {str(row["state"]) for row in rows.values()} == {mapping_module.STATE_CONFIRMED}
    assert int(rows[first]["inherits_person"]) == 1
    assert int(rows[second]["inherits_person"]) == 0


def test_many_to_one_mapping_merges_exclusions_once(harness: RedetectionHarness) -> None:
    """MAP-009: two old faces onto one new face, one inheritor, one union of denials."""
    asset_id = harness.add_asset("合脸.jpg")
    dad = harness.add_person(name="爸爸")
    uncle = harness.add_person(name="叔叔")
    aunt = harness.add_person(name="姑姑")
    first_anchor = harness.seed_anchor(
        asset_id, person_id=dad, excluded=[uncle], ordinal=0
    )
    second_anchor = harness.seed_anchor(
        asset_id,
        mode="MANUAL_UNASSIGNED",
        excluded=[aunt],
        ordinal=1,
        bbox=shifted_bbox(0.0, 0.0),
    )
    new_face = harness.write_pair(asset_id)
    filter_before = harness.filter_revision()
    result = harness.decide(
        asset_id,
        [
            confirm_decision(
                first_anchor,
                [(new_face, True)],
                revision=harness.group_revision(first_anchor),
            ),
            confirm_decision(
                second_anchor,
                [(new_face, False)],
                revision=harness.group_revision(second_anchor),
            ),
        ],
    )
    assert result["changed"] is True
    # One transfer, one bump - never one per group.
    assert result["catalog_filter_revision"] == filter_before + 1
    assert str(harness.control(new_face)["mode"]) == "MANUAL_ASSIGNED"
    assert int(harness.effective_link(new_face)["person_id"]) == dad
    assert harness.active_exclusions(new_face) == sorted([uncle, aunt])
    assert len(
        [
            row
            for row in harness.face_links(new_face)
            if str(row["decision"]) == "CONFIRMED"
        ]
    ) == 1
    assert str(harness.group(first_anchor)["state"]) == mapping_module.GROUP_CONFIRMED
    assert str(harness.group(second_anchor)["state"]) == mapping_module.GROUP_CONFIRMED


def test_human_edited_target_is_never_overwritten(harness: RedetectionHarness) -> None:
    """MAP-010: a new face a human already decided on is refused, not overwritten."""
    asset_id = harness.add_asset("已编辑.jpg")
    dad = harness.add_person(name="爸爸")
    uncle = harness.add_person(name="叔叔")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    new_face = harness.write_pair(asset_id)
    # The human edits the new face first (a different, later decision in the UI).
    harness.edit(new_face, "assign", expected_revision=0, target_person_id=uncle)
    before_snapshot = harness.library_snapshot()
    revision = harness.group_revision(anchor)

    with pytest.raises(CatalogError) as error:
        harness.decide(
            asset_id,
            [confirm_decision(anchor, [(new_face, True)], revision=revision)],
        )
    assert error.value.code == FACE_MAPPING_TARGET_CONTROLLED
    assert error.value.details["new_face_id"] == new_face
    assert set(error.value.details) <= {"new_face_id", "old_face_id"}
    assert harness.group_revision(anchor) == revision
    assert harness.library_snapshot() == before_snapshot
    assert int(harness.effective_link(new_face)["person_id"]) == uncle


def test_affirm_deny_conflict_refuses_the_whole_request(harness: RedetectionHarness) -> None:
    """MAP-011: affirming and denying one person in one request is a whole-request refusal."""
    asset_id = harness.add_asset("冲突.jpg")
    dad = harness.add_person(name="爸爸")
    denying_anchor = harness.seed_anchor(
        asset_id, mode="MANUAL_UNASSIGNED", excluded=[dad], ordinal=0
    )
    affirming_anchor = harness.seed_anchor(asset_id, person_id=dad, ordinal=1)
    new_face = harness.write_pair(asset_id)
    before_snapshot = harness.library_snapshot()

    with pytest.raises(CatalogError) as error:
        harness.decide(
            asset_id,
            [
                confirm_decision(
                    affirming_anchor,
                    [(new_face, True)],
                    revision=harness.group_revision(affirming_anchor),
                ),
                confirm_decision(
                    denying_anchor,
                    [(new_face, False)],
                    revision=harness.group_revision(denying_anchor),
                ),
            ],
        )
    assert error.value.code == FACE_MAPPING_CONFLICT
    assert error.value.details["reason"] == "human_affirm_deny_conflict"
    assert harness.library_snapshot() == before_snapshot
    assert harness.effective_link(new_face) is None
    assert harness.control(new_face) is None


def test_mapping_undo_restores_state_forward_only(harness: RedetectionHarness) -> None:
    """MAP-016: undo restores the semantics, moves every revision forward, and requeues."""
    asset_id = harness.add_asset("撤销.jpg")
    dad = harness.add_person(name="爸爸")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    new_face = harness.write_pair(asset_id)
    control_before = harness.control_revision(new_face)
    link_before = harness.effective_link(new_face)
    assert link_before is None and control_before == 0

    confirmed = harness.decide(
        asset_id,
        [confirm_decision(anchor, [(new_face, True)], revision=harness.group_revision(anchor))],
    )
    group_after_confirm = harness.group(anchor)
    assert str(group_after_confirm["state"]) == mapping_module.GROUP_CONFIRMED

    undo = harness.undo(
        confirmed["operation_id"],
        harness.next_mapping_request(),
        int(confirmed["operation_revision"]),
    )
    assert undo["operation_type"] == "UNDO"
    assert undo["undo_of_operation_id"] == confirmed["operation_id"]
    assert undo["changed"] is True
    group_after_undo = harness.group(anchor)
    assert str(group_after_undo["state"]) == mapping_module.GROUP_OPEN
    assert int(group_after_undo["revision"]) > int(group_after_confirm["revision"])
    row = harness.mapping_of(anchor, new_face)
    assert str(row["state"]) == mapping_module.STATE_SUGGESTED
    assert int(row["inherits_person"]) == 0
    assert int(row["revision"]) > 1
    # The new face is back to its pre-decision facts, with a *forward* control revision.
    assert harness.effective_link(new_face) is None
    assert harness.control_revision(new_face) > control_before
    assert str(harness.control(new_face)["mode"]) == "AUTO_ALLOWED"
    original = harness.operation(confirmed["operation_id"])
    assert str(original["state"]) == "UNDONE"
    assert str(original["undone_by"]) == undo["operation_id"]
    # ADR-0010 §5.3: ``undo -> OPEN`` reapplies the hold and keeps the asset queued.
    assert harness.queue_row(asset_id) is not None


def test_later_human_edit_blocks_mapping_undo(harness: RedetectionHarness) -> None:
    """MAP-015: a later human edit makes the recorded snapshot non-compensable."""
    asset_id = harness.add_asset("后来编辑.jpg")
    dad = harness.add_person(name="爸爸")
    uncle = harness.add_person(name="叔叔")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    new_face = harness.write_pair(asset_id)
    confirmed = harness.decide(
        asset_id,
        [confirm_decision(anchor, [(new_face, True)], revision=harness.group_revision(anchor))],
    )
    harness.edit(
        new_face,
        "assign",
        expected_revision=harness.control_revision(new_face),
        target_person_id=uncle,
    )
    before_snapshot = harness.library_snapshot()

    with pytest.raises(CatalogError) as error:
        harness.undo(
            confirmed["operation_id"],
            harness.next_mapping_request(),
            int(confirmed["operation_revision"]),
        )
    assert error.value.code == FACE_MAPPING_OPERATION_NOT_UNDOABLE
    conflicts = error.value.details["conflicts"]
    assert conflicts and all(
        set(item) == {"entity_type", "entity_key", "reason"} for item in conflicts
    )
    assert harness.library_snapshot() == before_snapshot
    original = harness.operation(confirmed["operation_id"])
    assert str(original["state"]) == "COMMITTED"


def test_mapping_undo_conflicts_with_later_face_edit(harness: RedetectionHarness) -> None:
    """MAP-X-004: the same rule seen from the cross-family angle (face correction after)."""
    asset_id = harness.add_asset("交错撤销.jpg")
    dad = harness.add_person(name="爸爸")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    new_face = harness.write_pair(asset_id)
    confirmed = harness.decide(
        asset_id,
        [confirm_decision(anchor, [(new_face, True)], revision=harness.group_revision(anchor))],
    )
    correction = harness.edit(
        new_face, "unassign", expected_revision=harness.control_revision(new_face)
    )
    assert correction["changed"] is True
    with pytest.raises(CatalogError) as error:
        harness.undo(
            confirmed["operation_id"],
            harness.next_mapping_request(),
            int(confirmed["operation_revision"]),
        )
    assert error.value.code == FACE_MAPPING_OPERATION_NOT_UNDOABLE
    assert str(harness.control(new_face)["mode"]) == "MANUAL_UNASSIGNED"


def test_reject_releases_the_hold_and_keeps_the_anchor(harness: RedetectionHarness) -> None:
    """MAP-004/§5.3: a rejection changes the review only, never the anchor's facts."""
    asset_id = harness.add_asset("拒绝.jpg")
    dad = harness.add_person(name="爸爸")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    new_face = harness.write_pair(asset_id)
    filter_before = harness.filter_revision()
    result = harness.decide(
        asset_id,
        [
            {
                "old_face_id": anchor,
                "expected_review_revision": harness.group_revision(anchor),
                "decision": "reject",
                "targets": [],
            }
        ],
    )
    assert result["catalog_filter_revision"] == filter_before
    assert str(harness.group(anchor)["state"]) == mapping_module.GROUP_REJECTED
    row = harness.mapping_of(anchor, new_face)
    assert str(row["state"]) == mapping_module.STATE_REJECTED
    assert int(harness.effective_link(anchor)["person_id"]) == dad
    assert harness.effective_link(new_face) is None
    assert harness.control(new_face) is None
    assert harness.queue_row(asset_id) is not None
