from __future__ import annotations

from pathlib import Path

import pytest

from photo_search.evaluation.dataset import DatasetError, parse_dataset
from photo_search.evaluation.metrics import (
    best_rank,
    ndcg_at_k,
    recall_at_k,
    score_query,
    summarise,
)


def dataset_payload() -> dict:
    return {
        "schema_version": 1,
        "name": "script-generated fixture",
        "authorization": "test fixture only; no real photo library",
        "annotation_rules": "grade 2 = exact, 1 = related",
        "images": [
            {"id": "t1", "path": "tuning/a.png", "split": "tuning"},
            {"id": "t2", "path": "tuning/b.png", "split": "tuning"},
            {"id": "h1", "path": "holdout/c.png", "split": "holdout"},
            {"id": "h2", "path": "holdout/d.png", "split": "holdout"},
        ],
        "queries": [
            {
                "id": "qt",
                "text": "红色的图片",
                "split": "tuning",
                "relevant": {"t1": 2, "t2": 1},
            },
            {
                "id": "qh",
                "text": "海边的日落",
                "split": "holdout",
                "relevant": {"h1": 2},
            },
        ],
    }


def test_dataset_parses_and_summarises_splits(tmp_path: Path) -> None:
    dataset = parse_dataset(dataset_payload(), base_directory=tmp_path)
    assert dataset.summary()["images"] == {"tuning": 2, "holdout": 2}
    assert dataset.summary()["queries"] == {"tuning": 1, "holdout": 1}
    assert [query.id for query in dataset.queries_for_split("holdout")] == ["qh"]


def test_dataset_requires_recorded_authorization(tmp_path: Path) -> None:
    payload = dataset_payload()
    payload["authorization"] = ""
    with pytest.raises(DatasetError, match="authorization"):
        parse_dataset(payload, base_directory=tmp_path)


def test_dataset_rejects_split_leakage(tmp_path: Path) -> None:
    payload = dataset_payload()
    payload["queries"][0]["relevant"] = {"h1": 2}
    with pytest.raises(DatasetError, match="holdout split"):
        parse_dataset(payload, base_directory=tmp_path)


def test_dataset_rejects_duplicate_ids_and_unlabelled_queries(tmp_path: Path) -> None:
    payload = dataset_payload()
    payload["images"].append({"id": "t1", "path": "tuning/again.png", "split": "tuning"})
    with pytest.raises(DatasetError, match="duplicate image id"):
        parse_dataset(payload, base_directory=tmp_path)

    payload = dataset_payload()
    payload["queries"][0]["relevant"] = {}
    with pytest.raises(DatasetError, match="no graded relevance"):
        parse_dataset(payload, base_directory=tmp_path)


def test_shared_split_policy_allows_one_corpus_with_query_splits(tmp_path: Path) -> None:
    payload = dataset_payload()
    payload["image_split_policy"] = "shared"
    for image in payload["images"]:
        image["split"] = "shared"
    payload["queries"][0]["relevant"] = {"t1": 2, "h1": 1}
    dataset = parse_dataset(payload, base_directory=tmp_path)
    assert dataset.image_split_policy == "shared"
    assert dataset.summary()["image_split_policy"] == "shared"
    assert [query.id for query in dataset.queries_for_split("holdout")] == ["qh"]


def test_unknown_split_policy_is_rejected(tmp_path: Path) -> None:
    payload = dataset_payload()
    payload["image_split_policy"] = "whatever"
    with pytest.raises(DatasetError, match="image_split_policy"):
        parse_dataset(payload, base_directory=tmp_path)


def test_dataset_rejects_non_positive_grades(tmp_path: Path) -> None:
    payload = dataset_payload()
    payload["queries"][0]["relevant"] = {"t1": 0}
    with pytest.raises(DatasetError, match="non-positive relevance grade"):
        parse_dataset(payload, base_directory=tmp_path)


def test_metrics_reward_the_perfect_and_penalise_a_bad_ranking() -> None:
    relevant = {"a": 2, "b": 1, "c": 1}
    assert ndcg_at_k(["a", "b", "c"], relevant) == pytest.approx(1.0)
    assert recall_at_k(["a", "b"], relevant) == pytest.approx(2 / 3)
    assert best_rank(["x", "b"], relevant) == 2
    assert best_rank(["x", "y"], relevant) is None
    assert ndcg_at_k(["x", "a"], relevant) < 1.0


def test_metrics_reject_unlabelled_ground_truth() -> None:
    with pytest.raises(ValueError, match="at least one relevant"):
        ndcg_at_k(["a"], {})


def test_summary_keeps_splits_separate_and_lists_failures() -> None:
    good = score_query("q1", "tuning", ["a", "b"], {"a": 2, "b": 1})
    bad = score_query("q2", "holdout", ["x", "y"], {"h1": 2})
    summary = summarise([good, bad])
    assert summary["overall"]["queries"] == 2
    assert summary["tuning"]["ndcg_at_20_mean"] == pytest.approx(1.0)
    assert summary["holdout"]["queries"] == 1
    assert summary["holdout"]["queries_with_hit"] == 0
    assert [row["query_id"] for row in summary["failure_queries"]] == ["q2"]
