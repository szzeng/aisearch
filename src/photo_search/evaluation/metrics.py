"""Retrieval metrics for the authorized evaluation set (architecture §12.3)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

NDCG_K = 20
RECALL_K = 50


@dataclass(frozen=True)
class QueryScore:
    """One query's retrieval result plus the numbers reported for it."""

    query_id: str
    split: str
    ranked_image_ids: tuple[str, ...]
    ndcg_at_20: float
    recall_at_50: float
    best_rank: int | None
    relevant_in_top_20: int
    relevant_total: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "split": self.split,
            "ndcg_at_20": round(self.ndcg_at_20, 6),
            "recall_at_50": round(self.recall_at_50, 6),
            "best_rank": self.best_rank,
            "relevant_in_top_20": self.relevant_in_top_20,
            "relevant_total": self.relevant_total,
        }


def dcg(grades: Sequence[float]) -> float:
    return sum((2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(grades, start=1))


def ndcg_at_k(ranked_ids: Sequence[str], relevant: dict[str, int], k: int = NDCG_K) -> float:
    """Graded nDCG@k; unlabelled results contribute zero."""
    if not relevant:
        raise ValueError("nDCG needs at least one relevant image")
    grades = [relevant.get(image_id, 0) for image_id in list(ranked_ids)[:k]]
    ideal = dcg(sorted((grade for grade in relevant.values() if grade > 0), reverse=True)[:k])
    if ideal == 0:
        raise ValueError("nDCG needs at least one positive relevance grade")
    return dcg(grades) / ideal


def recall_at_k(ranked_ids: Sequence[str], relevant: dict[str, int], k: int = RECALL_K) -> float:
    """Fraction of labelled images that appear in the first ``k`` results."""
    if not relevant:
        raise ValueError("Recall needs at least one relevant image")
    positive = {image_id for image_id, grade in relevant.items() if grade > 0}
    hits = positive.intersection(list(ranked_ids)[:k])
    return len(hits) / len(positive)


def best_rank(ranked_ids: Sequence[str], relevant: dict[str, int]) -> int | None:
    """One-based rank of the best labelled image, or ``None`` when nothing was retrieved."""
    positive = {image_id for image_id, grade in relevant.items() if grade > 0}
    for rank, image_id in enumerate(ranked_ids, start=1):
        if image_id in positive:
            return rank
    return None


def score_query(
    query_id: str,
    split: str,
    ranked_ids: Sequence[str],
    relevant: dict[str, int],
) -> QueryScore:
    return QueryScore(
        query_id=query_id,
        split=split,
        ranked_image_ids=tuple(ranked_ids),
        ndcg_at_20=ndcg_at_k(ranked_ids, relevant),
        recall_at_50=recall_at_k(ranked_ids, relevant),
        best_rank=best_rank(ranked_ids, relevant),
        relevant_in_top_20=len(
            {image_id for image_id, grade in relevant.items() if grade > 0}.intersection(
                list(ranked_ids)[:NDCG_K]
            )
        ),
        relevant_total=len(relevant),
    )


def summarise(scores: Sequence[QueryScore]) -> dict[str, Any]:
    """Aggregate per-split so tuning and holdout numbers are never mixed."""
    summary: dict[str, Any] = {"overall": _aggregate(scores)}
    for split in sorted({score.split for score in scores}):
        summary[split] = _aggregate([score for score in scores if score.split == split])
    failures = [
        score.as_dict()
        for score in scores
        if score.best_rank is None or score.ndcg_at_20 < 0.5
    ]
    summary["failure_queries"] = sorted(
        failures, key=lambda row: (row["split"], row["ndcg_at_20"])
    )
    return summary


def _aggregate(scores: Sequence[QueryScore]) -> dict[str, Any]:
    if not scores:
        return {"queries": 0}
    return {
        "queries": len(scores),
        "ndcg_at_20_mean": round(sum(score.ndcg_at_20 for score in scores) / len(scores), 6),
        "recall_at_50_mean": round(
            sum(score.recall_at_50 for score in scores) / len(scores), 6
        ),
        "queries_with_hit": sum(1 for score in scores if score.best_rank is not None),
    }
