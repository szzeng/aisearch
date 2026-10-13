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
import math
import sqlite3
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import (
    CATALOG_UNREADABLE,
    RUNTIME_RESOLUTION_MISMATCH,
    CatalogError,
)
from photo_search.catalog.migrate import inspect_database
from photo_search.indexing.store import IndexCoordinator
from photo_search.media import raw_cr2
from photo_search.scheduler.projection import index_summary

logger = logging.getLogger("photo_search.indexing.maintenance")

#: Events one ``drain`` call applies unless the caller bounds it.
DEFAULT_LIMIT = 10_000
#: How often a ``busy_timeout`` refusal is re-attempted before it is raised.
DEFAULT_RETRY_LIMIT = 2
#: Delay between those attempts. The wait exists to let the other writer commit; it is not a
#: spin loop, and the busy timeout itself already gave the other writer its window.
DEFAULT_RETRY_BACKOFF_MS = 250

#: Re-execution targets one ``reexecute_reclaimed_artifacts`` call republishes unless the
#: caller bounds it. Each target costs one image encoding, so the bound is small on purpose.
DEFAULT_REEXECUTION_LIMIT = 100

#: How many isolated events :func:`awaiting_reexecution_summary` describes individually.
AWAITING_SAMPLE_LIMIT = 10

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
        #: Events whose artifact is waiting for re-execution. They are unconfirmed and
        #: deliberately not claimable, so a drain can finish with work left over; reporting it
        #: here is what keeps that visible instead of looking like a silent stop (P4-02C A2).
        "awaiting_reexecution": awaiting_reexecution_summary(catalog),
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


# ------------------------------------------------------------------ recovery (P4-02C)

#: The source location a re-execution reads from. Only a ``PRESENT`` location is usable: a
#: ``MISSING`` one would make the re-executed vector a different observation of a different
#: file, which ``prepare`` would refuse anyway (and it has to be refused *before* the model is
#: asked to encode anything).
_PRESENT_SOURCE_SQL = (
    "SELECT l.location_id, l.source_id, l.relative_path, s.root_path "
    "FROM file_locations AS l JOIN source_roots AS s ON s.source_id = l.source_id "
    "WHERE l.asset_id = ? AND l.availability = 'PRESENT' ORDER BY l.location_id LIMIT 1"
)


@runtime_checkable
class ImageEncoder(Protocol):
    """The image half of the ADR-0003 §7 embedding adapter boundary.

    Production supplies the pinned Chinese-CLIP adapter the AI worker already drives - the
    worker's request surface is exactly ``adapter.encode_image(path)`` on the asset's current
    file, and a re-execution is the same call on the same input revision. Diagnostics and tests
    supply a deterministic stand-in through this protocol; nothing here loads a model, chooses a
    device or falls back to another precision. A re-execution is *deterministic* by contract:
    :meth:`ArtifactStore.republish_reclaimed` re-derives the canonical payload bytes and compares
    them with the artifact's recorded checksum and size, so a vector that is not byte-identical
    to the published one is refused instead of silently redefining the asset's embedding.
    """

    def encode_image(self, path: str) -> Sequence[float]:
        """One source image file -> one finite vector of the artifact's dimension."""


def _validated_image_vector(
    encoder: ImageEncoder,
    *,
    path: Path,
    dimension: int,
    artifact_id: str,
    temp_root: str | Path,
) -> list[float]:
    """One re-executed vector, validated exactly like a query vector (no padding, no trimming)."""
    try:
        with raw_cr2.encoder_input(path, temp_root=temp_root) as encoder_path:
            raw = encoder.encode_image(str(encoder_path))
    except CatalogError:
        raise
    except Exception as error:  # a broken adapter is a structured refusal, not a crash
        raise CatalogError(
            RUNTIME_RESOLUTION_MISMATCH,
            "the image encoder failed",
            details={"artifact_id": str(artifact_id), "reason": type(error).__name__},
        ) from error
    if not isinstance(raw, (list, tuple)) or len(raw) != int(dimension):
        raise CatalogError(
            RUNTIME_RESOLUTION_MISMATCH,
            "the re-executed vector does not match the artifact dimension",
            details={
                "artifact_id": str(artifact_id),
                "expected_dimension": int(dimension),
                "actual_dimension": None if not isinstance(raw, (list, tuple)) else len(raw),
            },
        )
    values: list[float] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CatalogError(
                RUNTIME_RESOLUTION_MISMATCH,
                "the re-executed vector contains a value that is not a finite number",
                details={"artifact_id": str(artifact_id)},
            )
        number = float(value)
        if not math.isfinite(number):
            raise CatalogError(
                RUNTIME_RESOLUTION_MISMATCH,
                "the re-executed vector contains a value that is not a finite number",
                details={"artifact_id": str(artifact_id)},
            )
        values.append(number)
    return values


def awaiting_reexecution_summary(
    catalog: Catalog, *, sample_limit: int = AWAITING_SAMPLE_LIMIT
) -> dict[str, Any]:
    """Read-only: which unconfirmed outbox events are waiting for a re-executed artifact.

    These rows are the *visible* half of the isolation rule in
    :data:`photo_search.indexing.store.CLAIMABLE_EVENT_PREDICATE`: the drain may finish while
    they are still ``PENDING``, so every report says how many there are, why (their stored
    ``last_error_code``) and which artifact has to be republished. Nothing is written and no
    path is reported.
    """
    rows = catalog.read_all(
        "SELECT o.event_id, o.artifact_id, o.asset_id, o.space_id, o.generation_id, "
        "o.projection_revision, o.attempts, o.last_error_code "
        "FROM index_outbox AS o JOIN artifacts AS a ON a.artifact_id = o.artifact_id "
        "WHERE o.state <> 'CONFIRMED' AND a.lifecycle_state = 'RECLAIMED' "
        "ORDER BY o.created_at, o.event_id"
    )
    sample = [dict(row) for row in rows[: int(sample_limit)]]
    return {
        "count": len(rows),
        "sample": sample,
        "sample_truncated": len(rows) > len(sample),
    }


def reclaimed_artifacts(
    catalog: Catalog, *, limit: int = DEFAULT_REEXECUTION_LIMIT
) -> list[dict[str, Any]]:
    """Read-only: the embedding artifacts whose published bytes were reclaimed.

    This is the same population :meth:`IndexCoordinator.audit_and_schedule_repairs` reports as
    ``requires_reexecution``, read here with the facts a re-execution needs (the subject asset
    and the processor that produced it). No path is returned: the operator-facing JSON stays
    free of library layout.
    """
    rows = catalog.read_all(
        "SELECT a.artifact_id, a.space_id, a.dimension, a.artifact_revision, a.job_id, "
        "j.subject_type, j.subject_id, j.processor_id, j.input_revision "
        "FROM artifacts AS a JOIN jobs AS j ON j.job_id = a.job_id "
        "WHERE a.artifact_type = 'embedding' AND a.lifecycle_state = 'RECLAIMED' "
        "ORDER BY a.artifact_id LIMIT ?",
        (int(limit),),
    )
    return [dict(row) for row in rows]


def reexecute_reclaimed_artifacts(
    catalog: Catalog,
    *,
    encoder: ImageEncoder | None = None,
    limit: int = DEFAULT_REEXECUTION_LIMIT,
    now_ms: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """The supported production recovery path for artifacts awaiting re-execution.

    One target at a time, and every target is one of: ``republished`` (the deterministic
    re-execution output passed :meth:`ArtifactStore.prepare`'s checksum and byte_size check and
    the artifact is ``PUBLISHED`` again), ``refused`` (a structured ``CatalogError``: the vector
    was not byte-identical, the artifact state moved, the source is gone) or ``skipped`` (no
    usable subject/location at all). A refusal never stops the remaining targets and never
    leaves a partially published artifact behind, because the publication itself is
    ``ArtifactStore.republish_reclaimed``'s transaction.

    What happens next is deliberately *not* part of this call: after a successful republish the
    event that was waiting (still ``PENDING`` with its ``last_error_code``) becomes claimable
    again, so ``drain`` confirms it - and when the audit had to recreate the confirmation, one
    ``drain --repair`` schedules it first. Both follow-up commands are the existing entry.
    ``dry_run`` lists the targets without encoding anything and without touching the catalog.
    """
    if int(limit) < 0:
        raise CatalogError("USAGE", "limit must not be negative", details={"limit": int(limit)})
    if encoder is None and not dry_run:
        raise CatalogError(
            "USAGE",
            "an image encoder is required to re-execute; only --dry-run may omit it",
        )
    coordinator = IndexCoordinator(catalog)
    timestamp = int(now_ms) if now_ms is not None else catalog.now_ms()
    targets = reclaimed_artifacts(catalog, limit=int(limit))
    results: list[dict[str, Any]] = []
    counts = {"republished": 0, "refused": 0, "skipped": 0, "planned": 0}
    for row in targets:
        outcome = _reexecute_one(
            catalog,
            artifacts=coordinator.artifacts,
            encoder=encoder,
            row=row,
            now_ms=timestamp,
            dry_run=bool(dry_run),
        )
        counts[str(outcome["outcome"])] += 1
        results.append(outcome)
    return {
        "status": "ok",
        "dry_run": bool(dry_run),
        "limit": int(limit),
        "targets": len(targets),
        **counts,
        "results": results,
    }


def _reexecute_one(
    catalog: Catalog,
    *,
    artifacts: Any,
    encoder: ImageEncoder,
    row: dict[str, Any],
    now_ms: int,
    dry_run: bool,
) -> dict[str, Any]:
    """One target of :func:`reexecute_reclaimed_artifacts`; never raises for its own target."""
    result: dict[str, Any] = {
        "artifact_id": str(row["artifact_id"]),
        "space_id": str(row["space_id"]),
        "dimension": int(row["dimension"]),
        "processor_id": str(row["processor_id"]),
        "input_revision": str(row["input_revision"]),
    }
    if str(row["subject_type"]) != "asset":
        return {**result, "outcome": "skipped", "reason": "SUBJECT_NOT_ASSET"}
    asset_id = int(row["subject_id"])
    result["asset_id"] = asset_id
    source = catalog.read_one(_PRESENT_SOURCE_SQL, (asset_id,))
    if source is None:
        return {**result, "outcome": "skipped", "reason": "NO_PRESENT_LOCATION"}
    result["source_id"] = int(source["source_id"])
    path = Path(str(source["root_path"])) / str(source["relative_path"])
    if not path.is_file():
        return {**result, "outcome": "skipped", "reason": "SOURCE_FILE_MISSING"}
    if dry_run:
        return {**result, "outcome": "planned"}
    try:
        vector = _validated_image_vector(
            encoder,
            path=path,
            dimension=int(row["dimension"]),
            artifact_id=str(row["artifact_id"]),
            temp_root=catalog.data_root,
        )
        artifacts.republish_reclaimed(
            catalog,
            artifact_id=str(row["artifact_id"]),
            payload={"vector": vector},
            now_ms=int(now_ms),
        )
    except CatalogError as error:
        return {
            **result,
            "outcome": "refused",
            "code": str(error.code),
            "message": str(error.message),
        }
    return {**result, "outcome": "republished"}


__all__ = [
    "BUSY_MARKERS",
    "AWAITING_SAMPLE_LIMIT",
    "DEFAULT_LIMIT",
    "DEFAULT_REEXECUTION_LIMIT",
    "DEFAULT_RETRY_BACKOFF_MS",
    "DEFAULT_RETRY_LIMIT",
    "ImageEncoder",
    "awaiting_reexecution_summary",
    "drain_semantic_outbox",
    "is_busy_rejection",
    "reclaimed_artifacts",
    "reexecute_reclaimed_artifacts",
    "semantic_index_status",
    "summarize_ms",
]
