"""Decision protocol of ADR-0010 §5 (P3-08B): shape, revision gate and idempotency.

The transfer matrix itself is exercised by the integration suite; these cases pin the frozen
*protocol* answers - which decision a zero-candidate group may take, that one old face can
never hand its person to two targets, that a stale review revision is refused with the two
safe integers, and that the idempotency gate runs before any revision check.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from p3_redetection_fixtures import (
    RedetectionHarness,
    confirm_decision,
    make_record,
    reject_decision,
    shifted_bbox,
    shifted_landmarks,
)

from photo_search.api.errors import VALIDATION_ERROR
from photo_search.catalog.errors import (
    FACE_MAPPING_IDEMPOTENCY_CONFLICT,
    FACE_MAPPING_STALE,
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


def _no_candidate(harness: RedetectionHarness) -> dict[str, object]:
    """An anchor whose re-detection produced a face that is nowhere near it."""
    asset_id = harness.add_asset("没有候选.jpg")
    dad = harness.add_person(name="爸爸")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    record = make_record(
        ordinal=0, bbox=(400.0, 400.0, 500.0, 500.0), landmarks=shifted_landmarks(400.0, 400.0)
    )
    harness.write_face_document(asset_id, [record])
    group = harness.group(anchor)
    assert group is not None
    assert str(group["candidate_state"]) == mapping_module.CANDIDATE_NONE
    return {"asset_id": asset_id, "anchor": anchor, "dad": dad, "group": group}


def test_no_candidate_group_accepts_only_reject(harness: RedetectionHarness) -> None:
    """MAP-004: zero candidates can be acknowledged, never confirmed."""
    seeded = _no_candidate(harness)
    asset_id = int(seeded["asset_id"])
    anchor = str(seeded["anchor"])
    revision = harness.group_revision(anchor)
    operations_before = len(harness.operations())
    filter_before = harness.filter_revision()

    with pytest.raises(CatalogError) as error:
        harness.decide(
            asset_id,
            [
                {
                    "old_face_id": anchor,
                    "expected_review_revision": revision,
                    "decision": "confirm",
                    "targets": [],
                }
            ],
        )
    assert error.value.code == VALIDATION_ERROR
    assert error.value.details["reason"] == "confirm_requires_targets"
    assert len(harness.operations()) == operations_before
    assert harness.group_revision(anchor) == revision

    result = harness.decide(asset_id, [reject_decision(anchor, revision=revision)])
    # ``changed`` is the review-state fact (the group left ``OPEN``); the *human* fact did
    # not move, so the filter revision below must stay exactly where it was.
    assert result["changed"] is True
    assert result["items"][0]["changed"] is True
    assert result["items"][0]["state"] == mapping_module.GROUP_REJECTED
    assert result["operation_type"] == mapping_module.OPERATION_REJECT
    group = harness.group(anchor)
    assert str(group["state"]) == mapping_module.GROUP_REJECTED
    assert int(group["revision"]) == revision + 1
    # A pure acknowledgement moves no human fact and therefore no filter revision.
    assert harness.filter_revision() == filter_before
    assert harness.effective_link(anchor) is not None
    assert harness.control(anchor) is not None


def test_two_inheritors_for_one_target_is_refused(harness: RedetectionHarness) -> None:
    """MAP-007: one old face cannot give its person to two new faces."""
    asset_id = harness.add_asset("两个目标.jpg")
    dad = harness.add_person(name="爸爸")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    first = make_record(ordinal=0, bbox=shifted_bbox(0.0), landmarks=shifted_landmarks(0.0))
    second = make_record(
        ordinal=1, bbox=shifted_bbox(20.0), landmarks=shifted_landmarks(15.0, 15.0), seed=0.4
    )
    harness.write_face_document(asset_id, [first, second])
    first_id = harness.new_face_id(asset_id, 0, shifted_bbox(0.0))
    second_id = harness.new_face_id(asset_id, 1, shifted_bbox(20.0))
    revision = harness.group_revision(anchor)

    with pytest.raises(CatalogError) as error:
        harness.decide(
            asset_id,
            [confirm_decision(anchor, [(first_id, True), (second_id, True)], revision=revision)],
        )
    assert error.value.code == VALIDATION_ERROR
    assert error.value.details["reason"] == "multiple_targets_without_single_inheritor"
    assert harness.group_revision(anchor) == revision
    assert harness.effective_link(first_id) is None
    assert harness.effective_link(second_id) is None
    for row in harness.mappings(anchor):
        assert str(row["state"]) == mapping_module.STATE_SUGGESTED

    # The same request with exactly one inheritor is accepted and the second target is
    # handled conservatively (ADR-0010 §4.1/§4.3).
    accepted = harness.decide(
        asset_id,
        [confirm_decision(anchor, [(first_id, True), (second_id, False)], revision=revision)],
    )
    assert accepted["changed"] is True
    assert str(harness.effective_link(first_id)["person_id"]) == str(dad)
    assert harness.effective_link(second_id) is None
    assert harness.control(second_id)["mode"] == "MANUAL_UNASSIGNED"


def test_stale_review_revision_is_refused(harness: RedetectionHarness) -> None:
    """MAP-012: an outdated ``expected_review_revision`` answers with two safe integers."""
    seeded = _no_candidate(harness)
    asset_id = int(seeded["asset_id"])
    anchor = str(seeded["anchor"])
    fresh = harness.group_revision(anchor)
    harness.decide(asset_id, [reject_decision(anchor, revision=fresh)])
    current = harness.group_revision(anchor)
    assert current == fresh + 1

    with pytest.raises(CatalogError) as error:
        harness.decide(asset_id, [reject_decision(anchor, revision=fresh)])
    assert error.value.code == FACE_MAPPING_STALE
    assert error.value.details == {
        "old_face_id": anchor,
        "expected_review_revision": fresh,
        "current_review_revision": current,
    }
    assert harness.group_revision(anchor) == current


def test_decision_idempotency_gate_precedes_revision_checks(harness: RedetectionHarness) -> None:
    """MAP-014: same key + same payload replays first; same key + new payload is a conflict."""
    seeded = _no_candidate(harness)
    asset_id = int(seeded["asset_id"])
    anchor = str(seeded["anchor"])
    revision = harness.group_revision(anchor)
    body = {
        "contract_version": "p3-face-redetection-contracts.v1",
        "request_id": "map-idem-1",
        "asset_id": asset_id,
        "decisions": [reject_decision(anchor, revision=revision)],
    }
    first = harness.decide_body(body)
    assert first["replayed"] is False
    assert first["operation_type"] == mapping_module.OPERATION_REJECT

    # The recorded key answers with the original operation *before* it re-reads the revision,
    # which has moved on by now - a stale revision therefore cannot mask the replay.
    replay = harness.decide_body(body)
    assert replay["replayed"] is True
    assert replay["operation_id"] == first["operation_id"]
    assert replay["catalog_filter_revision"] == first["catalog_filter_revision"]
    assert len(harness.operations()) == 1

    # A *shape-valid* second payload under the same key: the hash differs, so the gate refuses
    # it instead of executing a second decision with a different intent.
    conflicting = {
        **body,
        "decisions": [reject_decision(anchor, revision=revision + 1)],
    }
    with pytest.raises(CatalogError) as error:
        harness.decide_body(conflicting)
    assert error.value.code == FACE_MAPPING_IDEMPOTENCY_CONFLICT
    assert error.value.details == {"asset_id": asset_id}
    assert len(harness.operations()) == 1
