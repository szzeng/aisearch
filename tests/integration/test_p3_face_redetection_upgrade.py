"""Model upgrade, failed upgrade, rollback and late results (ADR-0010 §7, P3-08B).

The mapping layer has to behave the same in all four: an upgrade creates a new review for the
human anchor, a failed or late attempt changes nothing at all, and a rollback reverses the
direction of the pairing without rewinding a single revision or human fact.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from p3_redetection_fixtures import (
    CURRENT_SPEC,
    OLD_SPEC,
    RedetectionHarness,
    confirm_decision,
    make_record,
    shifted_bbox,
    shifted_landmarks,
)

from photo_search.catalog.errors import ARTIFACT_INVALID, STALE_INPUT_REVISION, CatalogError
from photo_search.persons import mapping as mapping_module


@pytest.fixture()
def harness(tmp_path: Path) -> RedetectionHarness:
    instance = RedetectionHarness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def test_model_upgrade_creates_reviewable_groups(harness: RedetectionHarness) -> None:
    """MAP-019: the old generation stays queryable and the new one becomes reviewable."""
    asset_id = harness.add_asset("升级.jpg")
    dad = harness.add_person(name="爸爸")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    new_face = harness.write_pair(asset_id)

    old_row = harness.catalog.read_one(
        "SELECT * FROM faces WHERE face_id = ?", (anchor,)
    )
    assert old_row is not None and str(old_row["spec_revision"]) == OLD_SPEC
    assert harness.effective_link(anchor) is not None
    assert harness.control(anchor) is not None
    group = harness.group(anchor)
    assert group is not None
    assert str(group["old_spec_revision"]) == OLD_SPEC
    assert str(group["state"]) == mapping_module.GROUP_OPEN
    rows = harness.mappings(anchor)
    assert [str(row["new_face_id"]) for row in rows] == [new_face]
    assert str(rows[0]["new_spec_revision"]) == CURRENT_SPEC
    # The new face is held, so the upgrade cannot silently make it a member of the person.
    harness.seed_candidate_index([anchor, new_face])
    harness.reconcile_asset(asset_id)
    assert harness.effective_link(new_face) is None


def test_failed_upgrade_leaves_mappings_untouched(harness: RedetectionHarness) -> None:
    """MAP-020: an invalid document is refused before anything about the review moves."""
    asset_id = harness.add_asset("失败.jpg")
    dad = harness.add_person(name="爸爸")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    new_face = harness.write_pair(asset_id)
    harness.decide(
        asset_id,
        [confirm_decision(anchor, [(new_face, True)], revision=harness.group_revision(anchor))],
    )
    before = harness.library_snapshot()
    mapping_before = [dict(row) for row in harness.mappings(anchor)]
    group_before = dict(harness.group(anchor))

    def corrupt(payload: dict[str, object]) -> None:
        payload["processor_id"] = "another-face-processor"

    with pytest.raises(CatalogError) as error:
        harness.write_face_document(
            asset_id,
            [make_record(ordinal=0, bbox=shifted_bbox(0.0), landmarks=shifted_landmarks(0.0))],
            mutate=corrupt,
        )
    assert error.value.code == ARTIFACT_INVALID
    assert harness.library_snapshot() == before
    assert [dict(row) for row in harness.mappings(anchor)] == mapping_before
    assert dict(harness.group(anchor)) == group_before


def test_rollback_reverses_direction_without_rewinding_revisions(
    harness: RedetectionHarness,
) -> None:
    """MAP-021: after a rollback the pairing points the other way; nothing is rewound.

    The rollback is exercised at the mapping layer: P3-02's candidate generation row is keyed
    by ``(space_id, processor_id)`` and carries one ``spec_revision``, so a *real* rollback
    replaces the whole face layer (a packaging/weights concern outside this task). What this
    case pins is ADR-0010 §7: the generator of the reverted revision pairs the surviving rows
    of the newer generation with the freshly written ones, and every human fact and revision
    of the already confirmed mapping survives unchanged.
    """
    asset_id = harness.add_asset("回退.jpg")
    dad = harness.add_person(name="爸爸")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    new_face = harness.write_pair(asset_id)
    harness.decide(
        asset_id,
        [confirm_decision(anchor, [(new_face, True)], revision=harness.group_revision(anchor))],
    )
    confirmed_row = dict(harness.mapping_of(anchor, new_face))
    confirmed_group = dict(harness.group(anchor))
    assert str(confirmed_row["state"]) == mapping_module.STATE_CONFIRMED

    # The reverted revision writes its own face for the same geometry: the direction of
    # "old x new" is simply reversed.
    rolled_back_face = harness.add_face(
        asset_id, ordinal=7, spec_revision=OLD_SPEC
    )
    from photo_search.face import contract as face_contract

    previous = face_contract.FACE_SPEC_REVISION
    face_contract.FACE_SPEC_REVISION = OLD_SPEC
    try:
        with harness.catalog.write() as connection:
            mapping_module.generate_candidates_for_asset(
                connection,
                asset_id=asset_id,
                new_face_ids=[rolled_back_face],
                now_ms=int(harness.clock()),
            )
    finally:
        face_contract.FACE_SPEC_REVISION = previous

    # The already confirmed mapping is untouched, and no revision was rewound.
    row_after = dict(harness.mapping_of(anchor, new_face))
    assert str(row_after["state"]) == mapping_module.STATE_CONFIRMED
    assert int(row_after["revision"]) >= int(confirmed_row["revision"])
    group_after = dict(harness.group(anchor))
    assert str(group_after["state"]) == mapping_module.GROUP_CONFIRMED
    assert int(group_after["revision"]) >= int(confirmed_group["revision"])
    assert harness.effective_link(anchor) is not None
    assert int(harness.effective_link(new_face)["person_id"]) == dad

    # The transferred knowledge itself becomes an anchor of the reversed review.
    reversed_group = harness.group(new_face)
    assert reversed_group is not None
    assert str(reversed_group["state"]) == mapping_module.GROUP_OPEN
    reversed_rows = harness.mappings(new_face)
    assert [str(row["new_face_id"]) for row in reversed_rows] == [rolled_back_face]
    assert str(reversed_rows[0]["old_spec_revision"]) == CURRENT_SPEC
    assert str(reversed_rows[0]["new_spec_revision"]) == OLD_SPEC


def test_late_worker_document_is_refused(harness: RedetectionHarness) -> None:
    """MAP-022: a document produced for older content is refused and changes nothing."""
    asset_id = harness.add_asset("晚到.jpg")
    dad = harness.add_person(name="爸爸")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    new_face = harness.write_pair(asset_id)
    before = harness.library_snapshot()
    mapping_before = [dict(row) for row in harness.mappings(anchor)]
    stale_revision = hashlib.sha256(b"an older content revision").hexdigest()

    with pytest.raises(CatalogError) as error:
        harness.write_face_document(
            asset_id,
            [make_record(ordinal=0, bbox=shifted_bbox(0.0), landmarks=shifted_landmarks(0.0))],
            input_revision=stale_revision,
            sync_asset=False,
        )
    assert error.value.code == STALE_INPUT_REVISION
    assert harness.library_snapshot() == before
    assert [dict(row) for row in harness.mappings(anchor)] == mapping_before
    assert harness.effective_link(new_face) is None
    assert str(harness.group(anchor)["state"]) == mapping_module.GROUP_OPEN
