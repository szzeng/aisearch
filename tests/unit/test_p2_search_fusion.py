"""P2-05: the RRF fusion is a pure function with an exactly reproducible order."""

from __future__ import annotations

import pytest

from photo_search.catalog.errors import CatalogError
from photo_search.search.fusion import (
    DEFAULT_CHANNEL_WEIGHTS,
    RRF_K,
    channel_ranks,
    reciprocal_rank_fusion,
)


def test_the_first_version_constant_and_weights_are_the_frozen_ones() -> None:
    assert RRF_K == 60
    assert DEFAULT_CHANNEL_WEIGHTS == {"fts": 1.0, "vector": 1.0}


def test_scores_are_the_hand_computed_reciprocal_rank_sum() -> None:
    hits = reciprocal_rank_fusion({"fts": [10, 20, 30], "vector": [20, 40]})
    by_id = {hit.asset_id: hit for hit in hits}
    assert by_id[10].score == pytest.approx(1 / 61)
    assert by_id[20].score == pytest.approx(1 / 62 + 1 / 61)
    assert by_id[30].score == pytest.approx(1 / 63)
    assert by_id[40].score == pytest.approx(1 / 62)
    # Score descends; the asset found by both channels leads despite its worse FTS rank.
    assert [hit.asset_id for hit in hits] == [20, 10, 40, 30]
    assert by_id[20].matched_by == {"fts": 2, "vector": 1}
    assert by_id[20].channels == ("fts", "vector")
    assert by_id[10].matched_by == {"fts": 1}


def test_the_tie_rules_are_score_then_channel_count_then_asset_id() -> None:
    channels = {
        "fts": [11, 12, 13, 14, 15, 16, 17],
        "vector": [21, 22, 23, 24, 25, 26, 17],
    }
    hits = reciprocal_rank_fusion(channels, k=1)
    order = [hit.asset_id for hit in hits]
    by_id = {hit.asset_id: hit for hit in hits}

    # 11 and 21 have the same score and one channel each: the smaller asset_id leads.
    assert by_id[11].score == by_id[21].score == 0.5
    assert order.index(11) < order.index(21)
    # 17 (1/8 + 1/8) and 13 (1/4) tie exactly, but 17 was found by both channels.
    assert by_id[17].score == by_id[13].score == 0.25
    assert len(by_id[17].matched_by) == 2
    assert len(by_id[13].matched_by) == 1
    assert order.index(17) < order.index(13)


def test_a_repeat_inside_one_channel_keeps_the_best_rank() -> None:
    assert channel_ranks([5, 7, 5]) == {5: 1, 7: 2}
    hits = reciprocal_rank_fusion({"fts": [5, 7, 5]})
    assert [hit.asset_id for hit in hits] == [5, 7]
    scores = {hit.asset_id: hit.score for hit in hits}
    assert scores[5] == pytest.approx(1 / 61)
    assert scores[7] == pytest.approx(1 / 62)


def test_an_unusable_constant_or_weight_is_a_structured_refusal() -> None:
    with pytest.raises(CatalogError) as too_small:
        reciprocal_rank_fusion({"fts": [1]}, k=0)
    assert too_small.value.code == "VALIDATION_ERROR"
    for weight in (-0.5, float("nan"), float("inf")):
        with pytest.raises(CatalogError) as bad_weight:
            reciprocal_rank_fusion({"fts": [1]}, weights={"fts": weight})
        assert bad_weight.value.code == "VALIDATION_ERROR"


def test_the_fused_row_carries_order_evidence_and_no_similarity_claim() -> None:
    hits = reciprocal_rank_fusion({"fts": [1], "vector": [1]})
    payload = hits[0].to_dict()
    assert set(payload) == {"asset_id", "score", "matched_by", "channels"}
    assert payload["score"] == pytest.approx(2 / 61)
    assert payload["channels"] == ["fts", "vector"]
    # A weighted sum of BM25, a vector distance and (later) a face score is forbidden:
    # only the two rank positions reach this row.
    assert payload["matched_by"] == {"fts": 1, "vector": 1}
