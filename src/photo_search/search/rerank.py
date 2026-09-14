"""The minimal query-side reranker boundary and its candidate-score block (ADR-0003 §4.2/§7).

Architecture §9.2 asks for the optional VLM rerank as a **separate request with its own
timeout, after the base results are already returned**, and §13.1 keeps the frozen
``RerankerAdapter`` surface to ``spec()`` + ``score()``. This module implements exactly that
boundary: it takes the fused candidates of one search, asks one adapter for candidate
scores, and reports the outcome as its own structured block.

Four rules are enforced here rather than left to a caller:

1. **The score stays separate.** A row carries ``reranker_score`` and nothing else; the base
   ``score``/RRF value is never rewritten, added to or turned into a percentage. The two
   numbers are never comparable and a caller cannot accidentally fuse them.
2. **Base results first.** A missing adapter, a mismatched processor, a raising adapter or a
   timeout all return an outcome with **no rows**, and the already-computed hits are never
   passed in for mutation.
3. **A bounded wait.** ``score()`` runs on a daemon thread and is awaited for at most
   ``timeout_ms``. A timeout is reported as ``RERANK_TIMEOUT`` instead of blocking the
   request. The adapter also receives a :class:`Cancellation` with the same deadline so a
   well-behaved adapter stops by itself (ADR-0003 §7); a pre-emptive kill of a running
   adapter is *not* implemented and is not claimed.
4. **No heavy framework here.** Nothing in the service layer imports ONNX/PyTorch: an
   adapter is supplied by the deployment (or a test) exactly like the query encoder.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

#: One adapter may rerank at most this many candidates of one search. The fused list can hold
#: up to ``MAX_SESSION_IDS`` entries; a reranker scores the head of that order and the bound
#: is reported instead of being applied silently.
DEFAULT_RERANK_CANDIDATES = 50
MAX_RERANK_CANDIDATES = 100
#: The per-request rerank budget. It is deliberately short: the base page is already
#: answered, and a slow optional model must not hold the request open.
DEFAULT_RERANK_TIMEOUT_MS = 2_000

#: ``rerank_state`` of one answered page. ``NOT_REQUESTED`` means the caller did not ask for
#: a rerank at all; ``UNAVAILABLE`` means one was asked for but this process/profile cannot
#: run it (never "the reranker ran and agreed"); ``FAILED`` means it was attempted and did
#: not produce usable scores. Only ``OK`` carries rows.
RERANK_STATE_NOT_REQUESTED = "NOT_REQUESTED"
RERANK_STATE_OK = "OK"
RERANK_STATE_UNAVAILABLE = "UNAVAILABLE"
RERANK_STATE_FAILED = "FAILED"
RERANK_STATES = (
    RERANK_STATE_NOT_REQUESTED,
    RERANK_STATE_OK,
    RERANK_STATE_UNAVAILABLE,
    RERANK_STATE_FAILED,
)

#: Stable reasons. They are the honest "why" behind an ``UNAVAILABLE``/``FAILED`` state and
#: never contain a path, a coordinate or a query vector.
REASON_NO_PROFILE_RERANKER = "PROFILE_HAS_NO_RERANKER"
REASON_NO_QUERY_TEXT = "NO_QUERY_TEXT"
REASON_ADAPTER_UNAVAILABLE = "RERANKER_ADAPTER_UNAVAILABLE"
REASON_PROCESSOR_MISMATCH = "RERANKER_PROCESSOR_MISMATCH"
REASON_QUALIFICATION_MISSING = "RERANKER_QUALIFICATION_MISSING"
REASON_SPEC_INVALID = "RERANKER_SPEC_INVALID"
REASON_TIMEOUT = "RERANK_TIMEOUT"
REASON_FAILED = "RERANKER_FAILED"
REASON_RESULT_INVALID = "RERANKER_RESULT_INVALID"


@dataclass(frozen=True)
class RerankCandidate:
    """One candidate handed to the adapter: the asset and its position in the fused list."""

    asset_id: int
    rank: int


@dataclass(frozen=True)
class RerankRow:
    """One reranked candidate: a candidate score, never a probability and never a RRF value."""

    asset_id: int
    rank: int
    reranker_score: float
    reranker_processor_id: str

    def to_envelope(self) -> dict[str, Any]:
        return {
            "asset_id": int(self.asset_id),
            "rank": int(self.rank),
            "reranker_score": float(self.reranker_score),
            "reranker_processor_id": str(self.reranker_processor_id),
        }


@dataclass(frozen=True)
class Cancellation:
    """A deadline an adapter may poll; this module's own wait uses the same instant."""

    deadline: float
    clock: Callable[[], float] = time.monotonic

    def remaining_ms(self) -> int:
        return max(0, int(round((self.deadline - self.clock()) * 1000)))

    @property
    def expired(self) -> bool:
        return self.clock() >= self.deadline


@dataclass(frozen=True)
class RerankOutcome:
    """The whole rerank block of one page, including why it carries no rows."""

    state: str
    reason: str | None
    processor_id: str | None
    candidates: int
    limit: int
    timeout_ms: int
    elapsed_ms: int
    rows: tuple[RerankRow, ...] = ()
    error: dict[str, str] | None = None

    def to_envelope(self) -> dict[str, Any]:
        return {
            "state": str(self.state),
            "reason": self.reason,
            "processor_id": self.processor_id,
            "candidates": int(self.candidates),
            "limit": int(self.limit),
            "timeout_ms": int(self.timeout_ms),
            "elapsed_ms": int(self.elapsed_ms),
            "rows": [row.to_envelope() for row in self.rows],
            "error": None if self.error is None else dict(self.error),
        }


def not_requested_outcome() -> RerankOutcome:
    """The block of a page nobody asked to rerank; the envelope then carries ``None``."""
    return RerankOutcome(
        state=RERANK_STATE_NOT_REQUESTED,
        reason=None,
        processor_id=None,
        candidates=0,
        limit=0,
        timeout_ms=0,
        elapsed_ms=0,
        rows=(),
        error=None,
    )


def unavailable_outcome(
    reason: str,
    *,
    processor_id: str | None = None,
    candidates: int = 0,
    limit: int = 0,
    timeout_ms: int = DEFAULT_RERANK_TIMEOUT_MS,
    elapsed_ms: int = 0,
) -> RerankOutcome:
    """A rerank that was asked for but cannot run here - with no rows, ever."""
    return RerankOutcome(
        state=RERANK_STATE_UNAVAILABLE,
        reason=str(reason),
        processor_id=None if processor_id is None else str(processor_id),
        candidates=int(candidates),
        limit=int(limit),
        timeout_ms=int(timeout_ms),
        elapsed_ms=int(elapsed_ms),
        rows=(),
        error=None,
    )


def failed_outcome(
    reason: str,
    *,
    processor_id: str | None = None,
    candidates: int = 0,
    limit: int = 0,
    timeout_ms: int = DEFAULT_RERANK_TIMEOUT_MS,
    elapsed_ms: int = 0,
    error: Mapping[str, str] | None = None,
) -> RerankOutcome:
    """A rerank that was attempted and produced nothing usable; the base page is unaffected."""
    return RerankOutcome(
        state=RERANK_STATE_FAILED,
        reason=str(reason),
        processor_id=None if processor_id is None else str(processor_id),
        candidates=int(candidates),
        limit=int(limit),
        timeout_ms=int(timeout_ms),
        elapsed_ms=int(elapsed_ms),
        rows=(),
        error=None if error is None else {str(key): str(value) for key, value in error.items()},
    )


@runtime_checkable
class RerankerAdapter(Protocol):
    """ADR-0003 §7, query side: an immutable spec and one candidate-scoring call."""

    def spec(self) -> Any:
        """The immutable ``ProcessorSpec`` this adapter implements."""

    def score(
        self, query: str, candidates: Sequence[RerankCandidate], cancellation: Any = None
    ) -> Sequence[Mapping[str, Any]]:
        """Score the given candidates; each row carries ``asset_id`` + ``reranker_score``."""


def rerank_hits(
    adapter: Any,
    *,
    query: str | None,
    candidates: Sequence[RerankCandidate],
    expected_processor_id: str | None = None,
    timeout_ms: int = DEFAULT_RERANK_TIMEOUT_MS,
    limit: int = DEFAULT_RERANK_CANDIDATES,
    clock: Callable[[], float] = time.monotonic,
) -> RerankOutcome:
    """Score the head of one fused candidate list, or explain why no scores exist.

    ``expected_processor_id`` is the profile's ``reranker_processor_id``: an adapter for a
    different processor is reported as ``UNAVAILABLE``/``RERANKER_PROCESSOR_MISMATCH``
    instead of silently reranking with a model the active profile did not name.
    """
    budget = max(0, int(timeout_ms))
    cap = max(1, min(int(limit), MAX_RERANK_CANDIDATES))
    head = tuple(
        RerankCandidate(asset_id=int(item.asset_id), rank=int(item.rank))
        for item in list(candidates)[:cap]
    )
    if query is None:
        return unavailable_outcome(
            REASON_NO_QUERY_TEXT,
            processor_id=expected_processor_id,
            candidates=len(head),
            limit=cap,
            timeout_ms=budget,
        )
    if adapter is None:
        return unavailable_outcome(
            REASON_ADAPTER_UNAVAILABLE,
            processor_id=expected_processor_id,
            candidates=len(head),
            limit=cap,
            timeout_ms=budget,
        )
    try:
        spec = adapter.spec()
    except BaseException as error:  # noqa: BLE001 - reported, never raised into a request
        return failed_outcome(
            REASON_SPEC_INVALID,
            processor_id=expected_processor_id,
            candidates=len(head),
            limit=cap,
            timeout_ms=budget,
            error={"code": REASON_SPEC_INVALID, "type": type(error).__name__},
        )
    processor_id = str(getattr(spec, "processor_id", "") or "")
    kind = str(getattr(spec, "processor_kind", "") or "")
    if not processor_id or kind != "reranker_model":
        return failed_outcome(
            REASON_SPEC_INVALID,
            processor_id=processor_id or expected_processor_id,
            candidates=len(head),
            limit=cap,
            timeout_ms=budget,
            error={"code": REASON_SPEC_INVALID, "type": "ProcessorSpec"},
        )
    if expected_processor_id is not None and processor_id != str(expected_processor_id):
        return unavailable_outcome(
            REASON_PROCESSOR_MISMATCH,
            processor_id=processor_id,
            candidates=len(head),
            limit=cap,
            timeout_ms=budget,
        )
    if not head:
        # Nothing to score is not a failure: an empty fused list answers with an honest
        # empty reranked block instead of pretending a model ran.
        return RerankOutcome(
            state=RERANK_STATE_OK,
            reason=None,
            processor_id=processor_id,
            candidates=0,
            limit=cap,
            timeout_ms=budget,
            elapsed_ms=0,
            rows=(),
            error=None,
        )

    started = float(clock())
    cancellation = Cancellation(deadline=started + budget / 1000.0, clock=clock)
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["rows"] = list(adapter.score(str(query), head, cancellation))
        except BaseException as error:  # noqa: BLE001 - classified below, never propagated
            box["error"] = error

    worker = threading.Thread(target=target, name="photo-search-rerank", daemon=True)
    worker.start()
    worker.join(budget / 1000.0)
    elapsed_ms = max(0, int(round((float(clock()) - started) * 1000)))
    if worker.is_alive():
        # The adapter ignored the deadline: report the timeout and answer with base results.
        return failed_outcome(
            REASON_TIMEOUT,
            processor_id=processor_id,
            candidates=len(head),
            limit=cap,
            timeout_ms=budget,
            elapsed_ms=elapsed_ms,
            error={"code": REASON_TIMEOUT, "type": "TimeoutExpired"},
        )
    error = box.get("error")
    if error is not None:
        return failed_outcome(
            REASON_FAILED,
            processor_id=processor_id,
            candidates=len(head),
            limit=cap,
            timeout_ms=budget,
            elapsed_ms=elapsed_ms,
            error={"code": REASON_FAILED, "type": type(error).__name__},
        )
    try:
        rows = _validated_rows(box.get("rows"), head=head, processor_id=processor_id)
    except _InvalidRerankResult as invalid:
        return failed_outcome(
            REASON_RESULT_INVALID,
            processor_id=processor_id,
            candidates=len(head),
            limit=cap,
            timeout_ms=budget,
            elapsed_ms=elapsed_ms,
            error={"code": REASON_RESULT_INVALID, "type": str(invalid)},
        )
    return RerankOutcome(
        state=RERANK_STATE_OK,
        reason=None,
        processor_id=processor_id,
        candidates=len(head),
        limit=cap,
        timeout_ms=budget,
        elapsed_ms=elapsed_ms,
        rows=rows,
        error=None,
    )


class _InvalidRerankResult(ValueError):
    """The adapter returned a row the frozen protocol does not define."""


def _validated_rows(
    raw: Any, *, head: Sequence[RerankCandidate], processor_id: str
) -> tuple[RerankRow, ...]:
    """One row per scored candidate: known asset, finite number, deterministic order.

    A score for an asset that was not a candidate, a missing/blank score, a non-finite
    number or a duplicate candidate is a protocol violation of the adapter: reporting it as
    ``RERANKER_RESULT_INVALID`` is the only honest answer, because nothing here may invent a
    ranking input the model did not produce.
    """
    if raw is None or isinstance(raw, (str, bytes, Mapping)):
        raise _InvalidRerankResult("Mapping")
    ranks = {int(item.asset_id): int(item.rank) for item in head}
    rows: list[RerankRow] = []
    seen: set[int] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise _InvalidRerankResult("Mapping")
        try:
            asset_id = int(item["asset_id"])
            score = float(item["reranker_score"])
        except (KeyError, TypeError, ValueError) as error:
            raise _InvalidRerankResult("ValueError") from error
        if asset_id not in ranks or asset_id in seen or not math.isfinite(score):
            raise _InvalidRerankResult("ValueError")
        seen.add(asset_id)
        rows.append(
            RerankRow(
                asset_id=asset_id,
                rank=ranks[asset_id],
                reranker_score=score,
                reranker_processor_id=str(processor_id),
            )
        )
    rows.sort(key=lambda row: (-row.reranker_score, row.asset_id))
    return tuple(rows)


__all__ = [
    "Cancellation",
    "DEFAULT_RERANK_CANDIDATES",
    "DEFAULT_RERANK_TIMEOUT_MS",
    "MAX_RERANK_CANDIDATES",
    "REASON_ADAPTER_UNAVAILABLE",
    "REASON_FAILED",
    "REASON_NO_PROFILE_RERANKER",
    "REASON_NO_QUERY_TEXT",
    "REASON_PROCESSOR_MISMATCH",
    "REASON_QUALIFICATION_MISSING",
    "REASON_RESULT_INVALID",
    "REASON_SPEC_INVALID",
    "REASON_TIMEOUT",
    "RERANK_STATE_FAILED",
    "RERANK_STATE_NOT_REQUESTED",
    "RERANK_STATE_OK",
    "RERANK_STATE_UNAVAILABLE",
    "RERANK_STATES",
    "RerankCandidate",
    "RerankOutcome",
    "RerankRow",
    "RerankerAdapter",
    "failed_outcome",
    "not_requested_outcome",
    "rerank_hits",
    "unavailable_outcome",
]
