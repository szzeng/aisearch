"""P2-06B: the adapter boundary, the bounded wait and the score-separation guarantees.

Everything here is deterministic and model-free: the adapter is a local stand-in, the clock
is injected, and the assertions are about *protocol* behaviour - rows carry candidate
scores, a refusal carries no rows, and a slow or broken adapter can never change the base
page or block the request for longer than its budget.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pytest

from photo_search.search.rerank import (
    DEFAULT_RERANK_CANDIDATES,
    MAX_RERANK_CANDIDATES,
    REASON_ADAPTER_UNAVAILABLE,
    REASON_FAILED,
    REASON_NO_QUERY_TEXT,
    REASON_PROCESSOR_MISMATCH,
    REASON_RESULT_INVALID,
    REASON_SPEC_INVALID,
    REASON_TIMEOUT,
    RERANK_STATE_FAILED,
    RERANK_STATE_OK,
    RERANK_STATE_UNAVAILABLE,
    Cancellation,
    RerankCandidate,
    RerankRow,
    rerank_hits,
)


@dataclass(frozen=True)
class StubSpec:
    """The two fields the boundary reads; the real ``ProcessorSpec`` is used end to end."""

    processor_id: str
    processor_kind: str = "reranker_model"


class StubReranker:
    """A controllable adapter: fixed scores, an optional delay and an optional failure."""

    def __init__(
        self,
        *,
        processor_id: str = "stub-rerank@1",
        kind: str = "reranker_model",
        scores: dict[int, float] | None = None,
        rows: list[Any] | None = None,
        delay_s: float = 0.0,
        failure: BaseException | None = None,
    ) -> None:
        self._spec = StubSpec(processor_id=processor_id, processor_kind=kind)
        self._scores = {int(key): float(value) for key, value in (scores or {}).items()}
        self._rows = rows
        self.delay_s = float(delay_s)
        self.failure = failure
        self.calls: list[tuple[str, tuple[int, ...]]] = []
        self.cancellations: list[Any] = []

    def spec(self) -> StubSpec:
        return self._spec

    def score(
        self, query: str, candidates: Any, cancellation: Any = None
    ) -> list[dict[str, Any]]:
        self.calls.append((str(query), tuple(int(item.asset_id) for item in candidates)))
        self.cancellations.append(cancellation)
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.failure is not None:
            raise self.failure
        if self._rows is not None:
            return list(self._rows)
        return [
            {
                "asset_id": int(item.asset_id),
                "reranker_score": float(self._scores.get(int(item.asset_id), 0.0)),
            }
            for item in candidates
        ]


def candidates(count: int = 3) -> tuple[RerankCandidate, ...]:
    return tuple(RerankCandidate(asset_id=10 + index, rank=index + 1) for index in range(count))


def test_scored_rows_are_deterministic_candidate_scores_only() -> None:
    adapter = StubReranker(scores={10: 0.2, 11: 0.9, 12: 0.5})
    first = rerank_hits(adapter, query="sunset", candidates=candidates())
    second = rerank_hits(adapter, query="sunset", candidates=candidates())
    assert first.state == RERANK_STATE_OK
    assert first.to_envelope() == second.to_envelope()
    assert [row.asset_id for row in first.rows] == [11, 12, 10]
    assert [row.rank for row in first.rows] == [2, 3, 1]
    assert first.rows[0].reranker_processor_id == "stub-rerank@1"
    rows = first.to_envelope()["rows"]
    assert set(rows[0]) == {"asset_id", "rank", "reranker_score", "reranker_processor_id"}
    assert "score" not in rows[0] and "%" not in str(rows)
    assert adapter.calls == [("sunset", (10, 11, 12)), ("sunset", (10, 11, 12))]


def test_a_missing_adapter_mismatched_processor_or_empty_query_is_unavailable() -> None:
    missing = rerank_hits(None, query="sunset", candidates=candidates())
    assert (missing.state, missing.reason, missing.rows) == (
        RERANK_STATE_UNAVAILABLE,
        REASON_ADAPTER_UNAVAILABLE,
        (),
    )

    adapter = StubReranker(processor_id="other-rerank@1")
    mismatched = rerank_hits(
        adapter, query="sunset", candidates=candidates(), expected_processor_id="wanted@1"
    )
    assert mismatched.state == RERANK_STATE_UNAVAILABLE
    assert mismatched.reason == REASON_PROCESSOR_MISMATCH
    assert mismatched.rows == () and adapter.calls == []

    no_text = rerank_hits(StubReranker(scores={10: 1.0}), query=None, candidates=candidates())
    assert (no_text.state, no_text.reason, no_text.rows) == (
        RERANK_STATE_UNAVAILABLE,
        REASON_NO_QUERY_TEXT,
        (),
    )


def test_an_empty_candidate_list_is_an_honest_empty_result() -> None:
    outcome = rerank_hits(StubReranker(), query="sunset", candidates=())
    assert outcome.state == RERANK_STATE_OK
    assert outcome.rows == () and outcome.candidates == 0


def test_an_invalid_or_non_reranker_spec_is_a_structured_failure() -> None:
    embedding = StubReranker(kind="embedding_model")
    invalid = rerank_hits(embedding, query="sunset", candidates=candidates())
    assert (invalid.state, invalid.reason) == (RERANK_STATE_FAILED, REASON_SPEC_INVALID)
    assert invalid.rows == () and embedding.calls == []

    class Exploding:
        def spec(self) -> Any:
            raise RuntimeError("C:\\photos\\secret\\reranker.onnx")

    broken = rerank_hits(Exploding(), query="sunset", candidates=candidates())
    assert (broken.state, broken.reason) == (RERANK_STATE_FAILED, REASON_SPEC_INVALID)
    assert broken.error == {"code": REASON_SPEC_INVALID, "type": "RuntimeError"}
    assert "secret" not in str(broken.to_envelope())


def test_an_adapter_exception_is_reported_without_its_message() -> None:
    adapter = StubReranker(failure=RuntimeError("C:\\photos\\secret\\model.onnx"))
    outcome = rerank_hits(adapter, query="sunset", candidates=candidates())
    assert (outcome.state, outcome.reason) == (RERANK_STATE_FAILED, REASON_FAILED)
    assert outcome.error == {"code": REASON_FAILED, "type": "RuntimeError"}
    assert outcome.rows == ()
    assert "secret" not in str(outcome.to_envelope())


def test_a_slow_adapter_is_bounded_by_its_budget_and_never_returns_rows() -> None:
    adapter = StubReranker(scores={10: 1.0}, delay_s=0.5)
    started = time.monotonic()
    outcome = rerank_hits(adapter, query="sunset", candidates=candidates(), timeout_ms=100)
    elapsed = time.monotonic() - started
    assert (outcome.state, outcome.reason) == (RERANK_STATE_FAILED, REASON_TIMEOUT)
    assert outcome.error == {"code": REASON_TIMEOUT, "type": "TimeoutExpired"}
    assert outcome.rows == () and outcome.timeout_ms == 100
    # The request waited for its budget, not for the adapter.
    assert elapsed < 0.45, elapsed
    # The adapter was handed a deadline it can poll (ADR-0003 §7's cancellation contract).
    cancellation = adapter.cancellations[0]
    assert isinstance(cancellation, Cancellation)
    assert cancellation.remaining_ms() <= 100
    time.sleep(0.02)
    assert Cancellation(deadline=time.monotonic() - 1).expired is True


@pytest.mark.parametrize(
    "rows",
    [
        [{"asset_id": 99, "reranker_score": 1.0}],
        [{"asset_id": 10, "reranker_score": 1.0}, {"asset_id": 10, "reranker_score": 0.5}],
        [{"asset_id": 10, "reranker_score": float("nan")}],
        [{"asset_id": 10}],
        [{"asset_id": 10, "reranker_score": "high"}],
        ["not-a-mapping"],
        [None],
    ],
)
def test_rows_outside_the_protocol_are_refused(rows: list[Any]) -> None:
    adapter = StubReranker(rows=rows)
    outcome = rerank_hits(adapter, query="sunset", candidates=candidates())
    assert (outcome.state, outcome.reason) == (RERANK_STATE_FAILED, REASON_RESULT_INVALID)
    assert outcome.rows == ()


def test_the_candidate_head_is_bounded_and_reported() -> None:
    adapter = StubReranker(scores={10: 1.0, 11: 0.8, 12: 0.6, 13: 0.4})
    outcome = rerank_hits(adapter, query="sunset", candidates=candidates(4), limit=2)
    assert outcome.candidates == 2 and outcome.limit == 2
    assert adapter.calls[0][1] == (10, 11)
    assert DEFAULT_RERANK_CANDIDATES == 50 and MAX_RERANK_CANDIDATES == 100
    huge = rerank_hits(
        StubReranker(), query="sunset", candidates=candidates(150), limit=10_000
    )
    assert huge.limit == MAX_RERANK_CANDIDATES and huge.candidates == MAX_RERANK_CANDIDATES


def test_the_row_dataclass_carries_no_base_score() -> None:
    row = RerankRow(asset_id=1, rank=1, reranker_score=0.5, reranker_processor_id="p@1")
    assert set(row.to_envelope()) == {
        "asset_id",
        "rank",
        "reranker_score",
        "reranker_processor_id",
    }
