"""Candidate generation and evidence rules of ADR-0010 §3 (P3-08B).

These cases prove the *derivation*: which pair becomes a candidate at all, which of the two
states it gets, when a group splits into ``AMBIGUOUS`` without a preselected target, that a
face without human knowledge is never queued, and that a self pair is not a mapping. Nothing
here writes a person fact.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from p3_redetection_fixtures import (
    BASE_LANDMARKS,
    RedetectionHarness,
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


def _face_row(
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 100.0, 100.0),
    landmarks: tuple[tuple[float, float], ...] = BASE_LANDMARKS,
    *,
    quality_state: str = "OK",
) -> dict[str, object]:
    return {
        "bbox_json": json.dumps({"bbox": list(bbox)}),
        "landmarks_json": json.dumps({"landmarks": [list(point) for point in landmarks]}),
        "quality_state": quality_state,
    }


def test_ambiguous_group_has_no_default_target(harness: RedetectionHarness) -> None:
    """MAP-003: two qualified candidates -> ``AMBIGUOUS`` with no preselected row."""
    asset_id = harness.add_asset("ambiguous.jpg")
    body = harness.add_person(name="爸爸")
    anchor = harness.seed_anchor(asset_id, person_id=body)
    # Two faces that both sit on the same old face: one clean (HIGH), one shifted but still
    # inside both frozen bounds (iou 0.67, landmark ratio 0.15 -> WEAK).
    first = make_record(
        ordinal=0, bbox=shifted_bbox(0.0), landmarks=shifted_landmarks(0.0)
    )
    second = make_record(
        ordinal=1,
        bbox=shifted_bbox(20.0),
        landmarks=shifted_landmarks(15.0, 15.0),
        seed=0.3,
    )
    harness.write_face_document(asset_id, [first, second])
    first_id = harness.new_face_id(asset_id, 0, shifted_bbox(0.0))
    second_id = harness.new_face_id(asset_id, 1, shifted_bbox(20.0))

    group = harness.group(anchor)
    assert group is not None
    assert str(group["candidate_state"]) == mapping_module.CANDIDATE_AMBIGUOUS
    assert str(group["state"]) == mapping_module.GROUP_OPEN
    rows = harness.mappings(anchor)
    assert {str(row["new_face_id"]) for row in rows} == {first_id, second_id}
    assert all(str(row["state"]) == mapping_module.STATE_SUGGESTED for row in rows)
    assert all(int(row["inherits_person"]) == 0 for row in rows)
    # The read projection carries no preselection either: it lists both candidates as
    # ``SUGGESTED`` and everything a human needs to choose.
    detail = harness.mapping_detail(anchor)
    assert detail["candidate_state"] == mapping_module.CANDIDATE_AMBIGUOUS
    assert detail["candidate_count"] == 2
    assert sorted(item["state"] for item in detail["candidates"]) == ["SUGGESTED", "SUGGESTED"]


def test_low_quality_never_reaches_high_match_state() -> None:
    """MAP-006: a ``LOW`` face on either side caps the evidence at ``WEAK``."""
    high = mapping_module.match_state_for(
        old_face=_face_row(), new_face=_face_row()
    )
    assert high == mapping_module.MATCH_HIGH
    low_old = mapping_module.match_state_for(
        old_face=_face_row(quality_state="LOW"), new_face=_face_row()
    )
    low_new = mapping_module.match_state_for(
        old_face=_face_row(), new_face=_face_row(quality_state="LOW")
    )
    assert low_old == mapping_module.MATCH_WEAK
    assert low_new == mapping_module.MATCH_WEAK
    assert mapping_module.candidate_state_for([mapping_module.MATCH_WEAK]) == (
        mapping_module.CANDIDATE_UNIQUE_WEAK
    )
    # An unqualified pair is not a candidate at all, in either direction.
    assert (
        mapping_module.match_state_for(
            old_face=_face_row(), new_face=_face_row(bbox=(400.0, 400.0, 500.0, 500.0))
        )
        is None
    )


def test_faces_without_human_knowledge_are_not_queued(harness: RedetectionHarness) -> None:
    """MAP-026: an ordinary old row is re-derived automatically, never queued for review."""
    asset_id = harness.add_asset("plain.jpg")
    harness.add_face(asset_id, ordinal=0, spec_revision="face-buffalo-l-scrfd-arcface-v0")
    harness.write_face_document(
        asset_id, [make_record(ordinal=0, bbox=shifted_bbox(0.0), landmarks=shifted_landmarks(0.0))]
    )
    assert harness.groups() == []
    assert harness.mappings() == []
    # A human-owned anchor on the very same asset does get its review group.
    anchor = harness.seed_anchor(asset_id, ordinal=1)
    harness.write_face_document(
        asset_id,
        [
            make_record(ordinal=0, bbox=shifted_bbox(0.0), landmarks=shifted_landmarks(0.0)),
            make_record(
                ordinal=1,
                bbox=shifted_bbox(0.0, 200.0),
                landmarks=shifted_landmarks(0.0, 200.0),
            ),
        ],
    )
    assert [str(row["old_face_id"]) for row in harness.groups()] == [anchor]


def test_self_mapping_is_never_generated(harness: RedetectionHarness) -> None:
    """MAP-028: a face is never paired with itself, and the database refuses such a row."""
    asset_id = harness.add_asset("same.jpg")
    anchor = harness.seed_anchor(asset_id)
    harness.write_face_document(
        asset_id, [make_record(ordinal=0, bbox=shifted_bbox(0.0), landmarks=shifted_landmarks(0.0))]
    )
    row = harness.group(anchor)
    assert row is not None
    for mapping_row in harness.mappings(anchor):
        assert str(mapping_row["old_face_id"]) != str(mapping_row["new_face_id"])
    # The generator's identity function refuses a self pair by construction, and the frozen
    # CHECK refuses a hand-written one.
    with pytest.raises(sqlite3.IntegrityError):
        with harness.catalog.write() as connection:
            connection.execute(
                "INSERT INTO face_redetection_mappings(mapping_id, old_face_id, new_face_id, "
                "asset_id, old_spec_revision, new_spec_revision, match_state, state, "
                "inherits_person, revision, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'a', 'b', 'HIGH', 'SUGGESTED', 0, 1, 1, 1)",
                ("a" * 64, anchor, anchor, int(asset_id)),
            )
