"""P3-02: the conservative association policy as a pure function."""

from __future__ import annotations

import pytest

from photo_search.face import contract
from photo_search.face.index import Candidate, blob_to_vector, cosine, decide_for_face
from photo_search.face.processor import embedding_blob


def vector(*values: float) -> tuple[float, ...]:
    """A deterministic L2-normalised 512-d vector from a short seed."""
    values = values or (1.0,)
    filled = [0.0] * contract.FACE_DIMENSION
    for index, value in enumerate(values):
        filled[index] = float(value)
    norm = sum(value * value for value in filled) ** 0.5
    if norm == 0:
        filled[0] = 1.0
        norm = 1.0
    return tuple(value / norm for value in filled)


def blend(base: tuple[float, ...], other: tuple[float, ...], *, weight: float) -> tuple[float, ...]:
    mixed = [
        (1.0 - weight) * left + weight * right for left, right in zip(base, other, strict=True)
    ]
    norm = sum(value * value for value in mixed) ** 0.5
    return tuple(value / norm for value in mixed)


def candidate(person_id: int, vec: tuple[float, ...], *, face_id: str = "f") -> Candidate:
    return Candidate(face_id=face_id, person_id=int(person_id), asset_id=1, vector=vec)


@pytest.mark.parametrize(
    ("similarity", "action"), [(0.4999, "SUGGESTED"), (0.50, "MERGE"), (0.53, "MERGE")]
)
def test_personal_merge_threshold_boundary(similarity: float, action: str) -> None:
    probe = vector(similarity, (1 - similarity * similarity) ** 0.5)
    decision = decide_for_face(
        quality_state="OK",
        vector=probe,
        candidates=[candidate(7, vector(1))],
        person_in_asset=set(),
    )
    assert decision.action == action


def test_blob_round_trip_keeps_the_dimension_contract() -> None:
    vec = vector(3.0, 4.0)
    restored = blob_to_vector(embedding_blob(vec), dimension=contract.FACE_DIMENSION)
    assert len(restored) == contract.FACE_DIMENSION
    assert cosine(restored, vec) == pytest.approx(1.0, abs=1e-6)


def test_a_face_without_any_candidate_starts_an_unnamed_group() -> None:
    decision = decide_for_face(
        quality_state="OK", vector=vector(1.0), candidates=[], person_in_asset=set()
    )
    assert decision.action == "NEW_GROUP"
    assert decision.person_id is None


def test_a_near_identical_face_merges_into_the_existing_group() -> None:
    base = vector(1.0)
    close = blend(base, vector(0.0, 1.0), weight=0.05)
    decision = decide_for_face(
        quality_state="OK", vector=close, candidates=[candidate(7, base)], person_in_asset=set()
    )
    assert decision.action == "MERGE"
    assert decision.person_id == 7
    assert decision.similarity is not None and decision.similarity >= contract.MERGE_THRESHOLD


def test_a_borderline_face_is_only_suggested() -> None:
    base = vector(1.0)
    # A similarity inside [suggest, merge) must never become an assignment.
    target = (contract.SUGGEST_THRESHOLD + contract.MERGE_THRESHOLD) / 2
    mixed = [
        target * left + (1.0 - target * target) ** 0.5 * right
        for left, right in zip(base, vector(0.0, 1.0), strict=True)
    ]
    norm = sum(value * value for value in mixed) ** 0.5
    mixed = tuple(value / norm for value in mixed)
    decision = decide_for_face(
        quality_state="OK", vector=mixed, candidates=[candidate(7, base)], person_in_asset=set()
    )
    assert decision.action == "SUGGESTED"
    assert decision.reason == contract.REASON_BORDERLINE
    assert decision.person_id == 7


def test_a_stranger_does_not_join_an_existing_group() -> None:
    decision = decide_for_face(
        quality_state="OK",
        vector=vector(0.0, 0.0, 1.0),
        candidates=[candidate(7, vector(1.0))],
        person_in_asset=set(),
    )
    assert decision.action == "NEW_GROUP"


def test_a_low_quality_face_is_never_a_confirmed_member() -> None:
    base = vector(1.0)
    decision = decide_for_face(
        quality_state="LOW",
        vector=base,
        candidates=[candidate(7, base)],
        person_in_asset=set(),
    )
    assert decision.action == "SUGGESTED"
    assert decision.reason == contract.REASON_LOW_QUALITY
    unassigned = decide_for_face(
        quality_state="LOW",
        vector=vector(0.0, 0.0, 1.0),
        candidates=[candidate(7, base)],
        person_in_asset=set(),
    )
    assert unassigned.action == "LOW_QUALITY_UNASSIGNED"
    assert unassigned.person_id is None


def test_a_near_tie_between_two_people_is_not_merged() -> None:
    # Two groups that are almost equally close to the new face: the association must not pick
    # one of them silently.
    left = vector(1.0, 0.2)
    right = vector(1.0, -0.2)
    probe = vector(1.0)
    decision = decide_for_face(
        quality_state="OK",
        vector=probe,
        candidates=[candidate(7, left), candidate(8, right)],
        person_in_asset=set(),
    )
    assert decision.action == "SUGGESTED"
    assert decision.reason == contract.REASON_NEAR_TIE


def test_a_second_face_of_the_same_person_in_one_picture_is_a_conflict() -> None:
    base = vector(1.0)
    # No candidate index entry yet: the only evidence is the earlier face of this picture.
    decision = decide_for_face(
        quality_state="OK",
        vector=blend(base, vector(0.0, 1.0), weight=0.02),
        candidates=[],
        person_in_asset=set(),
        in_asset=[(11, base)],
    )
    assert decision.action == "SUGGESTED"
    assert decision.reason == contract.REASON_SAME_IMAGE_CONFLICT
    assert decision.person_id == 11


def test_a_same_image_conflict_does_not_fire_for_a_stranger() -> None:
    decision = decide_for_face(
        quality_state="OK",
        vector=vector(0.0, 0.0, 1.0),
        candidates=[],
        person_in_asset=set(),
        in_asset=[(11, vector(1.0))],
    )
    assert decision.action == "NEW_GROUP"


def test_a_duplicate_inside_one_picture_really_reaches_the_conflict_check() -> None:
    """Both faces of a composite picture end up on one person as one member plus one note."""
    first = decide_for_face(
        quality_state="OK", vector=vector(1.0), candidates=[], person_in_asset=set()
    )
    assert first.action == "NEW_GROUP"
    second = decide_for_face(
        quality_state="OK",
        vector=vector(1.0),
        candidates=[],
        person_in_asset=set(),
        in_asset=[(42, vector(1.0))],
    )
    assert second.action == "SUGGESTED"
    assert second.reason == contract.REASON_SAME_IMAGE_CONFLICT
    assert second.person_id == 42


def test_a_candidate_of_another_person_is_never_confused_with_a_near_tie() -> None:
    # The runner-up must be the best *other* person, so two faces of the same person do not
    # turn a confident merge into a near tie.
    base = vector(1.0)
    decision = decide_for_face(
        quality_state="OK",
        vector=blend(base, vector(0.0, 1.0), weight=0.03),
        candidates=[
            candidate(7, base, face_id="a"),
            candidate(7, blend(base, vector(0.0, 1.0), weight=0.05), face_id="b"),
        ],
        person_in_asset=set(),
    )
    assert decision.action == "MERGE"
    assert decision.person_id == 7


def test_a_person_already_present_in_the_picture_forces_a_conflict_record() -> None:
    base = vector(1.0)
    decision = decide_for_face(
        quality_state="OK",
        vector=blend(base, vector(0.0, 1.0), weight=0.03),
        candidates=[candidate(7, base)],
        person_in_asset={7},
    )
    assert decision.action == "SUGGESTED"
    assert decision.reason == contract.REASON_SAME_IMAGE_CONFLICT
