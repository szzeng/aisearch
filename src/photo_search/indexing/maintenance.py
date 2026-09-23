"""The production maintenance entry of the semantic (vector) outbox - P4-02A.

**Who calls it.** One controlled, operator-facing command shell:
``python -m photo_search.indexing.cli drain`` (and the same function,
:func:`drain_semantic_outbox`, for a maintenance session that already holds a catalog). The
read-only companion is ``python -m photo_search.indexing.cli status``. Nothing here starts a
thread, a daemon, a service or a second vector backend: the drain runs inside the calling
process and returns when the outbox has no claimable event left (or the ``limit`` is reached).

**When it is called.** Whenever embedding artifacts have been published (they queue
``UPSERT_VECTOR`` events in ``index_outbox``) and a semantic index is expected to become
READY: after an AI batch, after a restart that has to finish an interrupted outbox, and
before any capacity or correctness measurement that claims the semantic stage is complete.
``semantic_ready`` (``index_entries.state = 'CONFIRMED'`` plus a READY generation) only ever
moves through this path (P2-03 §7.2).

**Concurrency discipline: multi-maintainer safe, no exclusive lock.** ``process_batch``
claims its events in a short committed transaction and then performs the authority re-read,
the supersede check, the engine write and every confirmation inside one
``BEGIN IMMEDIATE`` transaction (P4-01C focused review A1, second round). Two maintainers may
therefore drain the same catalog at the same time: they serialise on the SQLite
single-writer lock, an event another maintainer already confirmed is skipped instead of being
written or reported as a lost claim, and a *lower* projection revision can never land on top
of a newer one. This entry deliberately adds no second mechanism (no lock file, no
singleton row, no leader election) - the invariant is the write transaction, and it is
verified by the P4-02A injection evidence, not by an argument about this docstring.

**What it reports.** Everything a reviewer needs to judge a maintenance run without reading
logs: events applied, transactions, per-transaction lock-wait and hold-time distributions,
the structured ``busy_timeout`` refusals with their retry outcome, the best-effort scalar
index outcome per generation, and the outbox/entry/generation counts before and after. The
numbers come from the production path itself (``IndexCoordinator`` instrumentation and a
read-only catalog summary), not from a test harness.

A ``busy_timeout`` expiry is a *transient* claim: nothing of the refused transaction was
written, the claimed events stay reclaimable (``PENDING``/``IN_FLIGHT``), so the honest
reaction is a bounded retry. Nothing else is retried here: a protocol failure is raised to
the caller unchanged.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import CATALOG_UNREADABLE, CatalogError
from photo_search.catalog.migrate import inspect_database
from photo_search.indexing.store import IndexCoordinator
from photo_search.scheduler.projection import index_summary

logger = logging.getLogger("photo_search.indexing.maintenance")

#: Events one ``drain`` call applies unless the caller bounds it.
DEFAULT_LIMIT = 10_000
#: How often a ``busy_timeout`` refusal is re-attempted before it is raised.
DEFAULT_RETRY_LIMIT = 2
#: Delay between those attempts. The wait exists to let the other writer commit; it is not a
#: spin loop, and the busy timeout itself already gave the other writer its window.
DEFAULT_RETRY_BACKOFF_MS = 250

#: SQLite reports an expired ``busy_timeout`` through these texts. ``Catalog.write`` wraps it
#: as ``CATALOG_UNREADABLE`` ("cannot start a catalog write transaction: database is locked").
BUSY_MARKERS = ("database is locked", "database table is locked", "database is busy")


def is_busy_rejection(error: BaseException) -> bool:
    """Whether ``error`` is SQLite refusing a write because another writer holds the lock.

    This is the only retryable maintenance outcome: it is raised before the transaction body
    runs, so the caller can prove nothing was written and simply run the drain again.
    """
    if isinstance(error, CatalogError):
        if str(error.code) != CATALOG_UNREADABLE:
            return False
        text = str(error.message)
    elif isinstance(error, sqlite3.OperationalError):
        text = str(error)
    else:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in BUSY_MARKERS)


def summarize_ms(samples: Sequence[float]) -> dict[str, Any]:
    """Count/P50/P95/max of a millisecond sample list (nearest-rank, no interpolation)."""
    values = sorted(float(value) for value in samples)
    if not values:
        return {"count": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}

    def pick(fraction: float) -> float:
        index = min(len(values) - 1, max(0, int(round(fraction * len(values) + 0.5)) - 1))
        return round(values[index], 3)

    return {
        "count": len(values),
        "p50_ms": pick(0.5),
        "p95_ms": pick(0.95),
        "max_ms": round(values[-1], 3),
    }


def drain_semantic_outbox(
    catalog: Catalog,
    *,
    limit: int = DEFAULT_LIMIT,
    now_ms: int | None = None,
    batch_size: int | None = None,
    repair: bool = False,
    retry_limit: int = DEFAULT_RETRY_LIMIT,
    retry_backoff_ms: int = DEFAULT_RETRY_BACKOFF_MS,
    sleep: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Apply the pending semantic outbox events and return the maintenance report.

    ``limit`` bounds how many events one call applies, and the returned ``applied`` is the
    number of events *this call* confirmed: a bounded retry after a ``busy_timeout`` refusal
    adds what the refused attempt had already confirmed instead of replacing it, so ``applied``
    never exceeds ``limit`` and never understates the work the call did (P4-02A review A1).
    ``batch_size`` only decides how many events share one engine write (every event is still
    confirmed individually and a batch is still all-or-nothing), and ``repair`` additionally
    runs the P2-03 repair audit first.
    The audit is *off by default* on purpose: it visits every embedding artifact with one
    engine point query, so on a full library it is a separate, measured decision rather than
    something a routine drain pays for.
    """
    if int(limit) < 0:
        raise CatalogError("USAGE", "limit must not be negative", details={"limit": int(limit)})
    if retry_limit < 0 or retry_backoff_ms < 0:
        raise CatalogError(
            "USAGE",
            "retry limits must not be negative",
            details={"retry_limit": int(retry_limit), "retry_backoff_ms": int(retry_backoff_ms)},
        )
    batch = int(batch_size) if batch_size is not None else int(IndexCoordinator.BATCH_SIZE)
    if batch < 1:
        raise CatalogError("USAGE", "batch size must be a positive number", details={"batch_size": batch})
    waiter = sleep or time.sleep
    timestamp = int(now_ms) if now_ms is not None else catalog.now_ms()

    coordinator = IndexCoordinator(catalog)
    coordinator.BATCH_SIZE = batch
    before = index_summary(catalog.db_path)
    started = time.perf_counter()
    repair_report: dict[str, Any] | None = None
    if repair:
        audit = coordinator.audit_and_schedule_repairs(now_ms=timestamp)
        repair_report = {
            "scheduled": len(audit["scheduled"]),
            "requires_reexecution": len(audit["requires_reexecution"]),
        }

    applied = 0
    retries = 0
    rejected = 0
    while True:
        try:
            # ``applied_events`` is the coordinator's own tally, so a refused attempt's already
            # confirmed batches stay counted even though ``process_all`` raised before returning
            # (P4-02A review A1); the retry then gets the remaining budget only.
            applied = int(coordinator.applied_events)
            coordinator.process_all(now_ms=timestamp, limit=int(limit) - applied)
            break
        except CatalogError as error:
            if not is_busy_rejection(error) or retries >= int(retry_limit):
                raise
            retries += 1
            rejected += 1
            logger.warning(
                "the semantic outbox drain lost the single-writer lock (attempt %s/%s): %s",
                retries,
                int(retry_limit) + 1,
                error.message,
            )
            if retry_backoff_ms:
                waiter(int(retry_backoff_ms) / 1000)
    applied = int(coordinator.applied_events)
    duration_ms = (time.perf_counter() - started) * 1000
    after = index_summary(catalog.db_path)
    #: Counts come from the uncapped per-phase counter; the timing *distribution* comes from a
    #: bounded sample list, whose size is reported separately so a truncated list can never be
    #: read as "this call only ran that many transactions".
    phases = dict(coordinator.write_transaction_counts)
    return {
        "status": "ok",
        "applied": int(applied),
        "confirmed_delta": _outbox_delta(before, after),
        "limit": int(limit),
        "batch_size": batch,
        #: One ``apply`` transaction per engine write; ``claim``/``revert`` are short.
        "batches": int(phases.get("apply", 0)),
        "write_phases": phases,
        "timing_samples": len(coordinator.write_samples),
        "timing_samples_truncated": len(coordinator.write_samples)
        >= IndexCoordinator.MAX_WRITE_SAMPLES,
        "duration_ms": round(duration_ms, 1),
        "repair": repair_report,
        "lock_wait_ms": summarize_ms([row["wait_ms"] for row in coordinator.write_samples]),
        "write_hold_ms": summarize_ms([row["hold_ms"] for row in coordinator.write_samples]),
        "write_transactions": sum(phases.values()),
        "write_rejections": {
            "count": len(coordinator.write_rejections),
            "retries": int(retries),
            "retry_limit": int(retry_limit),
            "retry_backoff_ms": int(retry_backoff_ms),
            "busy_refusals": int(rejected),
            "samples": list(coordinator.write_rejections),
        },
        "scalar_index": dict(coordinator.scalar_index_report),
        "index": {"before": before, "after": after},
    }


def _outbox_delta(before: dict[str, Any] | None, after: dict[str, Any] | None) -> int:
    def confirmed(summary: dict[str, Any] | None) -> int:
        return 0 if summary is None else int(summary.get("confirmed", 0))

    return confirmed(after) - confirmed(before)


def semantic_index_status(db_path: str | Path) -> dict[str, Any]:
    """Read-only semantic index facts; opens nothing for writing and creates nothing."""
    path = Path(db_path)
    return {
        "status": "ok",
        "schema_version": int(inspect_database(path)),
        "index": index_summary(path),
    }


__all__ = [
    "BUSY_MARKERS",
    "DEFAULT_LIMIT",
    "DEFAULT_RETRY_BACKOFF_MS",
    "DEFAULT_RETRY_LIMIT",
    "drain_semantic_outbox",
    "is_busy_rejection",
    "semantic_index_status",
    "summarize_ms",
]
