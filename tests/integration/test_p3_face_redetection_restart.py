"""Restart, repeat detection and backup restore of the mapping state (ADR-0010 §7).

The review is a durable fact: it lives in SQLite with the operation log, so a restart cannot
turn ``OPEN`` into "auto-assigned", a same-content re-run cannot clear a decision, and a
backup/restore cannot downgrade or renumber anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from p3_redetection_fixtures import (
    CURRENT_SPEC,
    RedetectionHarness,
    confirm_decision,
    make_record,
    shifted_bbox,
    shifted_landmarks,
)

from photo_search.persons import mapping as mapping_module


@pytest.fixture()
def harness(tmp_path: Path) -> RedetectionHarness:
    instance = RedetectionHarness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def test_open_review_survives_restart(harness: RedetectionHarness) -> None:
    """MAP-017: the open review and its hold are read from the authority after a restart."""
    asset_id = harness.add_asset("重启.jpg")
    dad = harness.add_person(name="爸爸")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    new_face = harness.write_pair(asset_id)
    harness.seed_candidate_index([anchor, new_face])
    before = harness.library_snapshot()

    harness.reopen()

    assert harness.library_snapshot() == before
    group = harness.group(anchor)
    assert group is not None and str(group["state"]) == mapping_module.GROUP_OPEN
    assert mapping_module.hold_face_ids(harness.catalog.connection, asset_id=asset_id) == {
        new_face
    }
    decisions = harness.reconcile_asset(asset_id)
    actions = {str(item["face_id"]): str(item["action"]) for item in decisions}
    assert actions.get(new_face) == "PROVISIONAL_HOLD"
    assert harness.effective_link(new_face) is None


def test_repeated_detection_is_idempotent_for_mappings(harness: RedetectionHarness) -> None:
    """MAP-018: the same pair keeps its id and a decided group is never re-opened."""
    asset_id = harness.add_asset("重跑.jpg")
    dad = harness.add_person(name="爸爸")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    new_face = harness.write_pair(asset_id)
    confirmed = harness.decide(
        asset_id,
        [confirm_decision(anchor, [(new_face, True)], revision=harness.group_revision(anchor))],
    )
    assert confirmed["changed"] is True
    row_before = harness.mapping_of(anchor, new_face)

    # The very same document again (same content, same generation): zero new rows, zero new
    # groups, and the human decision stands.
    harness.write_face_document(asset_id, [make_record(ordinal=0, bbox=shifted_bbox(0.0), landmarks=shifted_landmarks(0.0))])
    rows = harness.mappings(anchor)
    assert len(rows) == 1
    row_after = rows[0]
    assert str(row_after["mapping_id"]) == str(row_before["mapping_id"])
    assert str(row_after["state"]) == mapping_module.STATE_CONFIRMED
    assert int(row_after["inherits_person"]) == 1
    assert str(harness.group(anchor)["state"]) == mapping_module.GROUP_CONFIRMED
    assert len(harness.groups()) == 1
    assert int(harness.effective_link(new_face)["person_id"]) == dad


def test_unchanged_rerun_creates_no_mapping_rows(harness: RedetectionHarness) -> None:
    """MAP-027: a same-generation re-run is an upsert, never a new review."""
    asset_id = harness.add_asset("同代.jpg")
    face = harness.add_face(asset_id, ordinal=0)
    harness.add_embedding(face)
    harness.set_control(face, "MANUAL_UNASSIGNED")
    harness.write_face_document(
        asset_id,
        [make_record(ordinal=0, bbox=shifted_bbox(0.0), landmarks=shifted_landmarks(0.0))],
    )
    assert harness.groups() == []
    assert harness.mappings() == []
    # A second identical run is still a no-op for the mapping layer.
    harness.write_face_document(
        asset_id,
        [make_record(ordinal=0, bbox=shifted_bbox(0.0), landmarks=shifted_landmarks(0.0))],
    )
    current_before = {
        str(row["face_id"])
        for row in harness.catalog.read_all(
            "SELECT face_id FROM faces WHERE asset_id = ? AND spec_revision = ?",
            (asset_id, CURRENT_SPEC),
        )
    }
    assert harness.groups() == []
    assert harness.mappings() == []
    # The same-generation rows really are the same rows: the deterministic ``face_id`` is
    # upserted, so the second run adds no fourth row and no review.
    current_after = {
        str(row["face_id"])
        for row in harness.catalog.read_all(
            "SELECT face_id FROM faces WHERE asset_id = ? AND spec_revision = ?",
            (asset_id, CURRENT_SPEC),
        )
    }
    assert current_after == current_before
    assert len(current_after) == 2  # the preserved human anchor plus the detected face


def test_backup_restore_keeps_mapping_states(harness: RedetectionHarness, tmp_path: Path) -> None:
    """MAP-023: a restored authority keeps the decision, the hold and every revision."""
    asset_id = harness.add_asset("备份.jpg")
    dad = harness.add_person(name="爸爸")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    new_face = harness.write_pair(asset_id)
    harness.decide(
        asset_id,
        [confirm_decision(anchor, [(new_face, True)], revision=harness.group_revision(anchor))],
    )
    expected_group = dict(harness.group(anchor))
    expected_row = dict(harness.mapping_of(anchor, new_face))
    expected_control = dict(harness.control(new_face))

    restored_root = tmp_path / "Restored"
    harness.backup_to(restored_root)
    harness.reopen()
    # The restored database is a different data root: open it the same way the app would.
    from photo_search.catalog.db import Catalog

    restored = Catalog.open(data_root=restored_root, clock=harness.clock, lease_seconds=60)
    try:
        group = restored.read_one(
            "SELECT * FROM face_redetection_groups WHERE old_face_id = ?", (anchor,)
        )
        row = restored.read_one(
            "SELECT * FROM face_redetection_mappings WHERE old_face_id = ? AND new_face_id = ?",
            (anchor, new_face),
        )
        control = restored.read_one(
            "SELECT * FROM face_assignment_controls WHERE face_id = ?", (new_face,)
        )
        assert dict(group) == expected_group
        assert dict(row) == expected_row
        assert dict(control) == expected_control
        assert str(group["state"]) == mapping_module.GROUP_CONFIRMED
        assert int(group["revision"]) >= 2
    finally:
        restored.close()
