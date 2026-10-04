"""Group-photo evidence must compete; bounded representatives must cover variation."""

import math

import pytest

from photo_search.face import contract
from photo_search.face.index import Candidate, decide_for_face, diverse_representatives


def vector(score, axis=1):
    values = [0.0] * 512
    values[0], values[axis] = score, math.sqrt(1 - score * score)
    return tuple(values)


def candidate(person, score, label=None):
    return Candidate(label or str(person), person, person, vector(score))


@pytest.mark.parametrize("local", [0.50, 0.53, 0.70])
def test_stronger_person_wins_over_an_occupied_same_photo_person(local):
    decision = decide_for_face(
        quality_state="OK",
        vector=vector(1),
        candidates=[candidate(2, 0.90)],
        person_in_asset={1},
        in_asset=[(1, vector(local))],
    )
    assert (decision.action, decision.person_id) == ("MERGE", 2)


@pytest.mark.parametrize(
    ("local", "other", "reason"),
    [(0.90, 0.65, "SAME_IMAGE_CONFLICT"), (0.90, 0.85, "NEAR_TIE")],
)
def test_occupied_best_is_not_replaced_by_a_weaker_person_to_fill_the_photo(local, other, reason):
    decision = decide_for_face(
        quality_state="OK",
        vector=vector(1),
        candidates=[candidate(2, other)],
        person_in_asset=set(),
        in_asset=[(1, vector(local))],
    )
    assert (decision.action, decision.person_id, decision.reason) == ("SUGGESTED", 1, reason)


def test_diversity_keeps_a_smaller_variation_instead_of_eight_repeated_portraits():
    pool = [candidate(1, 1, f"large-{i}") for i in range(10)]
    pool.append(candidate(1, 0.65, "variation"))
    assert [c.face_id for c in diverse_representatives(pool)] == ["large-0", "variation"]


def test_pool_and_final_representative_bounds_and_ties_are_deterministic(monkeypatch):
    monkeypatch.setattr(contract, "REPRESENTATIVE_POOL_SIZE", 3)
    monkeypatch.setattr(contract, "MAX_REPRESENTATIVES_PER_PERSON", 2)
    pool = [candidate(1, 1, "best"), candidate(1, 0.8, "first"), candidate(1, 0.8, "tie")]
    pool.append(candidate(1, 0, "outside-pool"))
    assert [c.face_id for c in diverse_representatives(pool)] == ["best", "first"]
