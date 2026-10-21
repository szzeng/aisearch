"""Numeric, sampling and metric honesty for the isolated WeMM pilot."""

from __future__ import annotations

import copy
import hashlib
import json

import numpy as np
import pytest

from tools.evaluation.wemm_pilot_core import (
    annotated_metrics,
    cache_identity,
    compare_rankings,
    deterministic_rank,
    normalize_prefix,
    precision_at_k,
    select_sample,
)


def _rows(count: int) -> list[dict]:
    return [{"asset_id": i, "sha256": hashlib.sha256(str(i).encode()).hexdigest()}
            for i in range(count)]


def test_prefix_is_truncated_then_normalized_without_mutating_source():
    source = np.array([[3.0, 4.0, 120.0], [0.0, -8.0, 999.0]])
    before = source.copy()
    result = normalize_prefix(source, 2)
    np.testing.assert_allclose(result, [[.6, .8], [0, -1]])
    np.testing.assert_array_equal(source, before)
    assert result.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(result, axis=1), 1)


def test_prefix_handles_finite_extremes_without_norm_underflow_or_overflow():
    result = normalize_prefix(np.array([[1e300, 1e300], [1e-300, 0.0]]), 2)
    np.testing.assert_allclose(result, [[2 ** -.5, 2 ** -.5], [1, 0]], rtol=1e-6)


@pytest.mark.parametrize(("values", "dimension"), [
    ([1.0, 2.0], 2), ([[1, 2]], 2), ([[1j, 2j]], 2),
    ([[1.0, float("nan")]], 1), ([[1.0, float("inf")]], 1),
    ([[0.0, 0.0, 1.0]], 2), ([[1.0]], 2), ([[1.0]], 0),
    ([[1.0]], True), (np.zeros((0, 2)), 2),
])
def test_prefix_rejects_invalid_source_or_zero_prefix(values, dimension):
    with pytest.raises(ValueError):
        normalize_prefix(values, dimension)


def test_rank_uses_numeric_id_ties_and_refuses_invented_order():
    assert deterministic_rank([.5, .5, .8], [10, 2, 3]) == [3, 2, 10]
    assert deterministic_rank([.5, .5], ["b", "a"]) == ["a", "b"]
    assert deterministic_rank(np.array([1, 2], dtype=np.uint64), [1, 2]) == [2, 1]
    for scores, ids in [([.1, .2], [2, 2]), ([np.nan], [1]), ([np.inf], [1]),
                        ([.1], [1, 2]), ([.1, .2], [1, "2"]), ([[.2]], [1])]:
        with pytest.raises(ValueError):
            deterministic_rank(scores, ids)


def test_sample_is_deterministic_order_independent_and_at_least_half_unbiased():
    rows = _rows(40)
    original = copy.deepcopy(rows)
    groups = {"high": list(range(10)), "middle": list(range(10, 20))}
    sample = select_sample(rows, 13, 20261020, groups)
    assert sample == select_sample(list(reversed(rows)), 13, 20261020, groups)
    assert sample != select_sample(rows, 13, 20261021, groups)
    assert rows == original
    assert len(sample) == 13
    assert sum(row["selection_stratum"] == "random" for row in sample) == 7
    assert {row["selection_stratum"] for row in sample} == {
        "random", "problem:high", "problem:middle",
    }
    # Random quota must be identical even when problem IDs are changed entirely.
    random_only = select_sample(rows, 13, 20261020)
    assert sample[:7] == random_only[:7]


def test_sample_sha_dedup_keeps_canonical_id_and_preserves_failure_metadata():
    rows = _rows(5)
    rows.append({**rows[1], "asset_id": 50})
    rows[2]["decode_status"] = "missing"
    sample = select_sample(rows, 20, 17, [50])
    assert len(sample) == len({row["sha256"] for row in sample}) == 5
    assert {row["asset_id"] for row in sample} == set(range(5))
    assert next(row for row in sample if row["asset_id"] == 2)["decode_status"] == "missing"
    assert all(row["selection_stratum"] in {"random", "problem:selected"} for row in sample)


def test_sample_overlapping_problem_strata_do_not_duplicate_or_drop_assets():
    sample = select_sample(_rows(12), 10, 9, {"a": list(range(12)), "b": list(range(12))})
    assert len(sample) == len({row["asset_id"] for row in sample}) == 10
    assert sum(row["selection_stratum"] == "random" for row in sample) == 5


def test_sample_rejects_unknown_problem_ids_bad_hashes_and_unbounded_requests():
    for rows, maximum, seed, problems in [
        (_rows(2), 1001, 1, ()), (_rows(2), 0, 1, ()),
        (_rows(2), 2, True, ()), (_rows(2), 2, 1, [42]),
        ([{"asset_id": 1, "sha256": "path-or-mtime"}], 1, 1, ()),
        ([_rows(1)[0], _rows(1)[0]], 2, 1, ()),
    ]:
        with pytest.raises(ValueError):
            select_sample(rows, maximum, seed, problems)


def test_cache_identity_covers_each_material_identity_and_preserves_row_order():
    spec = {"model": "WeMM", "revision": "abc", "dimension": 512,
            "dtype": "bf16", "processor": {"max_pixels": 100, "template": "official"}}
    inputs = _rows(2)
    queries = ["猫", "两个人吃饭"]
    identity = cache_identity(spec, inputs, queries)
    assert identity == cache_identity(dict(reversed(list(spec.items()))), inputs, queries)
    assert len(identity) == 64
    mutations = []
    for key, value in [("revision", "def"), ("dimension", 2048), ("dtype", "fp16")]:
        mutations.append(cache_identity({**spec, key: value}, inputs, queries))
    mutations.append(cache_identity({**spec, "processor": {"max_pixels": 50}}, inputs, queries))
    mutations.append(cache_identity(spec, list(reversed(inputs)), queries))
    mutations.append(cache_identity(spec, [{**inputs[0], "sha256": "0" * 64}], queries))
    mutations.append(cache_identity(spec, inputs, list(reversed(queries))))
    assert all(changed != identity for changed in mutations)


def test_cache_identity_rejects_ambiguous_or_nonfinite_values():
    for spec in [{}, {"revision": float("nan")}, {"path": object()}, {1: "value"}]:
        with pytest.raises(ValueError):
            cache_identity(spec, _rows(1), ["猫"])


def _rankings():
    return {"qwen512": {"cat": [1, 2, 3, 4]},
            "qwen2048": {"cat": [2, 1, 3, 4]},
            "wemm512": {"cat": [4, 3, 2, 1]}}


def test_compare_reports_overlap_and_shift_never_unlabelled_precision():
    result = compare_rankings(_rankings(), ks=(2, 20, 50))
    assert result["status"] == "UNVERIFIED / RELATIVE RANKING ONLY"
    assert result["asset_count"] == 4
    assert len(result["pairwise"]) == 3
    pair = next(pair for pair in result["pairwise"]
                if (pair["left"], pair["right"]) == ("qwen512", "wemm512"))
    observation = pair["queries"]["cat"]
    assert observation["topk"]["2"]["overlap_fraction"] == 0
    assert observation["topk"]["50"] == {
        "requested_k": 50, "available_k": 4, "overlap_count": 4, "overlap_fraction": 1,
    }
    assert observation["rank_shifts"][0] == {
        "asset_id": 1, "left_rank": 1, "right_rank": 4, "right_rank_minus_left": 3,
    }
    encoded = json.dumps(result)
    assert "precision" not in encoded and "recall" not in encoded


@pytest.mark.parametrize("replacement", [{"cat": [1, 2, 3]}, {"cat": [1, 1, 2, 3]},
                                          {"dog": [1, 2, 3, 4]}])
def test_compare_refuses_inconsistent_population_queries_or_duplicate_assets(replacement):
    rankings = _rankings()
    rankings["wemm512"] = replacement
    with pytest.raises(ValueError):
        compare_rankings(rankings)


def test_precision_requires_exactly_all_requested_topk_judgments():
    assert precision_at_k([1, 2, 3], {1: True, 2: False, 3: None}, 2)["precision"] == .5
    missing = precision_at_k([1, 2, 3], {1: True}, 2)
    assert missing["status"] == "INCOMPLETE_JUDGMENTS"
    assert missing["judgment_coverage"] == .5
    assert "precision" not in missing
    unknown = precision_at_k([1, 2, 3], {1: True, 2: None}, 2)
    assert "precision" not in unknown
    short = precision_at_k([1, 2], {1: True, 2: True}, 20)
    assert short["status"] == "INSUFFICIENT_CANDIDATES"
    assert "precision" not in short
    with pytest.raises(ValueError):
        precision_at_k([1, 2], {1: True, 2: "probably"}, 2)


def test_annotated_recall_ap_require_complete_labels_and_state_positive_ceiling():
    ranking = [1, 2, 3, 4]
    labels = {1: False, 2: True, 3: False, 4: True}
    result = annotated_metrics(ranking, labels, (2, 4),
                               strata={1: "random", 2: "random", 3: "targeted", 4: "targeted"})
    assert result["annotated_positives"] == 2
    assert result["average_precision"] == .5  # (1/2 + 2/4) / 2 positives
    assert result["topk"]["2"]["annotated_recall"] == .5
    assert result["topk"]["4"]["maximum_possible_precision"] == .5
    assert result["strata"]["random"]["annotated_positives"] == 1
    del labels[4]
    partial = annotated_metrics(ranking, labels)
    assert partial["status"] == "INCOMPLETE_POPULATION_JUDGMENTS"
    assert "average_precision" not in partial and "topk" not in partial
    empty = annotated_metrics(ranking, dict.fromkeys(ranking, False))
    assert empty["average_precision"] is None
    assert empty["topk"]["20"]["annotated_recall"] is None
