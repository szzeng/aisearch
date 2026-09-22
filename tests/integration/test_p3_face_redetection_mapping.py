"""Cross-generation candidate generation of ADR-0010 §2/§3 (P3-08B).

Every case writes the *new* generation through the real ``persist_face_document()`` entry
point, so what is under test is the production commit transaction - candidate generation,
invalidation and the ``OPEN`` review - not a test-only helper.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from p3_redetection_fixtures import (
    RedetectionHarness,
    make_record,
    reject_decision,
    shifted_bbox,
    shifted_landmarks,
)

from photo_search.catalog.errors import (
    FACE_MAPPING_NOT_FOUND,
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


def test_cross_generation_redetection_creates_open_group(harness: RedetectionHarness) -> None:
    """MAP-001: an upgraded re-detection reviews the human anchor instead of dropping it."""
    asset_id = harness.add_asset("爸爸.jpg")
    dad = harness.add_person(name="爸爸")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    new_face = harness.write_pair(asset_id)

    group = harness.group(anchor)
    assert group is not None
    assert str(group["state"]) == mapping_module.GROUP_OPEN
    assert str(group["candidate_state"]) == mapping_module.CANDIDATE_UNIQUE_HIGH
    assert str(group["old_spec_revision"]) == "face-buffalo-l-scrfd-arcface-v0"
    rows = harness.mappings(anchor)
    assert len(rows) == 1
    assert str(rows[0]["new_face_id"]) == new_face
    assert str(rows[0]["state"]) == mapping_module.STATE_SUGGESTED
    assert int(rows[0]["revision"]) == 1

    # The old anchor keeps every human fact it had, and the new face inherited nothing.
    assert harness.control(anchor) is not None
    assert int(harness.control(anchor)["revision"]) == 1
    link = harness.effective_link(anchor)
    assert link is not None and str(link["assignment_kind"]) == "MANUAL"
    assert harness.effective_link(new_face) is None
    assert harness.control(new_face) is None

    # The stable identity is reproducible: the same pair always hashes to the same id.
    assert str(rows[0]["mapping_id"]) == mapping_module.mapping_id_for(
        asset_id=asset_id,
        old_face_id=anchor,
        new_face_id=new_face,
        old_spec_revision="face-buffalo-l-scrfd-arcface-v0",
        new_spec_revision=mapping_module._current_spec_revision(),
    )


def test_high_confidence_candidate_is_never_auto_confirmed(harness: RedetectionHarness) -> None:
    """MAP-002/MAP-029: a lone ``UNIQUE_HIGH`` stays ``OPEN`` and blocks the write gate."""
    asset_id = harness.add_asset("爸爸.jpg")
    dad = harness.add_person(name="爸爸")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    new_face = harness.write_pair(asset_id)

    decisions = harness.reconcile_asset(asset_id)
    actions = {str(item["face_id"]): str(item["action"]) for item in decisions}
    assert actions.get(new_face) == "PROVISIONAL_HOLD"
    assert harness.effective_link(new_face) is None
    assert harness.control(new_face) is None
    group = harness.group(anchor)
    assert group is not None and str(group["state"]) == mapping_module.GROUP_OPEN
    assert harness.queue_row(asset_id) is not None


def test_cross_asset_pairs_are_never_generated(harness: RedetectionHarness) -> None:
    """MAP-005: identical geometry on another photo never becomes a candidate."""
    first_asset = harness.add_asset("一.jpg")
    second_asset = harness.add_asset("二.jpg")
    first_anchor = harness.seed_anchor(first_asset)
    second_anchor = harness.seed_anchor(second_asset)
    first_new = harness.write_pair(first_asset)
    second_new = harness.write_pair(second_asset)

    assert {str(row["old_face_id"]) for row in harness.mappings(first_anchor)} == {first_anchor}
    assert {str(row["old_face_id"]) for row in harness.mappings(second_anchor)} == {second_anchor}
    assert {str(row["new_face_id"]) for row in harness.mappings(first_anchor)} == {first_new}
    assert {str(row["new_face_id"]) for row in harness.mappings(second_anchor)} == {second_new}

    revision = harness.group_revision(first_anchor)
    with pytest.raises(CatalogError) as error:
        harness.decide(
            first_asset,
            [
                {
                    "old_face_id": first_anchor,
                    "expected_review_revision": revision,
                    "decision": "confirm",
                    "targets": [
                        {"new_face_id": second_new, "inherits_person": True},
                    ],
                }
            ],
        )
    assert error.value.code == FACE_MAPPING_NOT_FOUND
    assert set(error.value.details) <= {"old_face_id", "new_face_id", "asset_id"}
    assert harness.effective_link(first_new) is None
    assert harness.group(first_anchor)["state"] == mapping_module.GROUP_OPEN


def test_removed_candidate_becomes_stale(harness: RedetectionHarness) -> None:
    """MAP-013/MAP-031: a candidate the next detection drops is ``STALE`` before any hold."""
    asset_id = harness.add_asset("消失.jpg")
    dad = harness.add_person(name="爸爸")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    new_face = harness.write_pair(asset_id)
    before = harness.mapping_of(anchor, new_face)
    assert before is not None and str(before["state"]) == mapping_module.STATE_SUGGESTED

    # The next document of the same asset contains no face at all: the candidate is retired.
    harness.write_face_document(asset_id, [])
    after = harness.mapping_of(anchor, new_face)
    assert after is not None
    assert str(after["state"]) == mapping_module.STATE_STALE
    assert int(after["revision"]) == int(before["revision"]) + 1
    assert harness.catalog.read_one(
        "SELECT 1 AS present FROM faces WHERE face_id = ?", (str(new_face),)
    ) is None
    group = harness.group(anchor)
    assert group is not None
    assert str(group["candidate_state"]) == mapping_module.CANDIDATE_NONE
    assert str(group["state"]) == mapping_module.GROUP_OPEN

    # A decision that still names the removed candidate is a stale review, not a shape error.
    with pytest.raises(CatalogError) as error:
        harness.decide(
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
    assert error.value.code == "FACE_MAPPING_STALE"

    # Re-writing the same geometry later is a *live* + *qualified* pair again, so an ``OPEN``
    # review counts it (ADR-0010 §3.3 "no matching face *in this detection*") instead of
    # reporting ``NO_CANDIDATE`` forever: the row returns to ``SUGGESTED`` with a forward-only
    # revision and the group is reviewable again. Nothing is confirmed on the way back.
    rebuilt = harness.write_pair(asset_id)
    assert rebuilt == new_face
    revived = harness.mapping_of(anchor, new_face)
    assert revived is not None
    assert str(revived["state"]) == mapping_module.STATE_SUGGESTED
    assert int(revived["revision"]) == int(after["revision"]) + 1
    assert str(revived["match_state"]) == "HIGH"
    group = harness.group(anchor)
    assert str(group["state"]) == mapping_module.GROUP_OPEN
    assert str(group["candidate_state"]) == mapping_module.CANDIDATE_UNIQUE_HIGH
    assert harness.effective_link(new_face) is None
    assert harness.control(new_face) is None
    detail = harness.mapping_detail(anchor)
    assert [row["new_face_id"] for row in detail["candidates"]] == [new_face]


def test_a_decided_group_is_not_reopened_by_a_reappearing_pair(
    harness: RedetectionHarness,
) -> None:
    """ADR-0010 §7: re-detection refreshes evidence but never rewrites a human decision.

    The revival of §3.3 is therefore confined to ``OPEN`` reviews - a decided group keeps its
    decision and its recorded candidate set, even when the same pair comes back.
    """
    asset_id = harness.add_asset("已决定.jpg")
    dad = harness.add_person(name="爸爸")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    new_face = harness.write_pair(asset_id)
    harness.decide(
        asset_id,
        [reject_decision(anchor, revision=harness.group_revision(anchor))],
    )
    decided = harness.group(anchor)
    assert str(decided["state"]) == mapping_module.GROUP_REJECTED
    assert str(harness.mapping_of(anchor, new_face)["state"]) == mapping_module.STATE_REJECTED

    # A rejection transfers no human fact, so the next document really does retire the face and
    # the row is invalidated - and then the very same pair is written again.
    harness.write_face_document(asset_id, [])
    assert str(harness.mapping_of(anchor, new_face)["state"]) == mapping_module.STATE_STALE
    assert harness.write_pair(asset_id) == new_face

    row = harness.mapping_of(anchor, new_face)
    assert str(row["state"]) == mapping_module.STATE_STALE
    group = harness.group(anchor)
    assert str(group["state"]) == mapping_module.GROUP_REJECTED
    assert str(group["candidate_state"]) == mapping_module.CANDIDATE_NONE
    assert harness.mapping_detail(anchor)["candidates"] == []

    # The decision itself still stands: the group is refused as already decided (ADR-0010
    # §5.3), never silently reopened by the re-detection.
    with pytest.raises(CatalogError) as error:
        harness.decide(
            asset_id,
            [reject_decision(anchor, revision=harness.group_revision(anchor))],
        )
    assert error.value.code == "FACE_MAPPING_CONFLICT"
    assert error.value.details["reason"] == "review_already_decided"


def test_a_zero_face_document_keeps_the_anchor_and_its_group(harness: RedetectionHarness) -> None:
    """A photo that now has no face at all must not lose the human anchor either."""
    asset_id = harness.add_asset("空.jpg")
    dad = harness.add_person(name="爸爸")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    harness.write_face_document(asset_id, [])
    assert harness.catalog.read_one(
        "SELECT 1 AS present FROM faces WHERE face_id = ?", (anchor,)
    ) is not None
    group = harness.group(anchor)
    assert group is not None
    assert str(group["candidate_state"]) == mapping_module.CANDIDATE_NONE
    assert _record_one(harness, anchor) is not None


def _record_one(harness: RedetectionHarness, old_face_id: str) -> dict[str, object] | None:
    detail = harness.mapping_detail(old_face_id)
    return detail if detail["old_face_id"] == old_face_id else None


def test_a_shifted_but_qualified_pair_is_weak(harness: RedetectionHarness) -> None:
    """§3.2: qualification is a band, not a point; the weaker band is recorded as ``WEAK``."""
    asset_id = harness.add_asset("弱.jpg")
    dad = harness.add_person(name="爸爸")
    anchor = harness.seed_anchor(asset_id, person_id=dad)
    record = make_record(
        ordinal=0, bbox=shifted_bbox(10.0), landmarks=shifted_landmarks(18.0)
    )
    harness.write_face_document(asset_id, [record])
    group = harness.group(anchor)
    assert group is not None
    assert str(group["candidate_state"]) == mapping_module.CANDIDATE_UNIQUE_WEAK
    rows = harness.mappings(anchor)
    assert [str(row["match_state"]) for row in rows] == [mapping_module.MATCH_WEAK]
