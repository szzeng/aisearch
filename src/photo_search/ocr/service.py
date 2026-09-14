"""Orchestration of the OCR processor: select, enqueue, re-record, run, catch up, report.

The OCR processor is an ordinary P2 scheduler job: this module only *feeds* it and reads
back what happened. It creates no thread and no daemon - one call drains the queue and
returns, which is exactly the "no resident scheduler thread" boundary of this build.

``force`` re-recognition reuses the same five-field logical job identity (ADR-0003 §8): a
SUCCEEDED/FAILED/BLOCKED/CANCELLED job is rewound to QUEUED with a fresh attempt budget,
rather than a second job row being created. A job that is RUNNING under a live lease is
never touched.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.jobs import queue as jobs
from photo_search.ocr import contract
from photo_search.ocr.engine import OcrEngineCache
from photo_search.ocr.processor import ocr_executor, ocr_processor_spec
from photo_search.scheduler import engine as scheduler_engine
from photo_search.scheduler.capability import snapshot_from_runtime
from photo_search.search import documents as search_documents

#: States a forced re-recognition may rewind: everything except a live execution.
REWINDABLE_STATES = ("SUCCEEDED", "FAILED", "CANCELLED", "BLOCKED", "RETRY_WAIT")

_TARGET_SQL = (
    "SELECT DISTINCT a.asset_id AS asset_id, a.sha256 AS sha256 "
    "FROM assets AS a "
    "JOIN file_locations AS f ON f.asset_id = a.asset_id AND f.availability = 'PRESENT' "
    "{source_filter}"
    "ORDER BY a.asset_id"
)


def ocr_targets(
    catalog: Catalog,
    *,
    asset_ids: Sequence[int] | None = None,
    source_id: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Every asset that currently has a readable original - the OCR work set.

    An asset whose only positions are ``MISSING``/``SOURCE_OFFLINE`` is deliberately not a
    target: it cannot be recognised today, and queuing it would only add a blocked job.
    """
    wanted = None if asset_ids is None else {int(value) for value in asset_ids}
    source_filter = ""
    parameters: tuple[Any, ...] = ()
    if source_id is not None:
        source_filter = "WHERE f.source_id = ? "
        parameters = (int(source_id),)
    rows = catalog.read_all(_TARGET_SQL.format(source_filter=source_filter), parameters)
    targets = [
        {"asset_id": int(row["asset_id"]), "input_revision": str(row["sha256"])}
        for row in rows
        if wanted is None or int(row["asset_id"]) in wanted
    ]
    if limit is not None:
        targets = targets[: max(0, int(limit))]
    return targets


def enqueue_targets(
    catalog: Catalog,
    targets: Sequence[dict[str, Any]],
    *,
    now_ms: int,
    force: bool = False,
) -> dict[str, int]:
    """Queue (or force-requeue) one OCR job per target, idempotently."""
    queued = rewound = live = 0
    with catalog.write() as connection:
        for target in targets:
            asset_id = int(target["asset_id"])
            revision = str(target["input_revision"])
            if force and _rewind(connection, asset_id=asset_id, revision=revision, now_ms=now_ms):
                rewound += 1
                continue
            if force and _is_live(connection, asset_id=asset_id, revision=revision):
                live += 1
                continue
            jobs.enqueue(
                connection,
                subject_type="asset",
                subject_id=str(asset_id),
                input_revision=revision,
                task_type=contract.OCR_TASK_TYPE,
                processor_id=contract.OCR_PROCESSOR_ID,
                now_ms=int(now_ms),
            )
            queued += 1
    return {"targets": len(targets), "queued": queued, "rewound": rewound, "live": live}


def _is_live(connection: sqlite3.Connection, *, asset_id: int, revision: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM jobs WHERE subject_type = 'asset' AND subject_id = ? "
        "AND input_revision = ? AND task_type = ? AND processor_id = ? AND state = 'RUNNING' "
        "AND lease_expires_at IS NOT NULL",
        (str(int(asset_id)), str(revision), contract.OCR_TASK_TYPE, contract.OCR_PROCESSOR_ID),
    ).fetchone()
    return row is not None


def _rewind(
    connection: sqlite3.Connection, *, asset_id: int, revision: str, now_ms: int
) -> bool:
    """Reuse the same logical job for a forced re-recognition (never a second row)."""
    placeholders = ", ".join("?" for _ in REWINDABLE_STATES)
    cursor = connection.execute(
        "UPDATE jobs SET state = ?, attempts = 0, progress = 0.0, lease_token = NULL, "
        "lease_expires_at = NULL, worker_id = NULL, run_after = ?, last_error_code = NULL, "
        "last_error_message = NULL, blocking_reason = NULL, resolved_device = NULL, "
        "runtime_provider = NULL, precision = NULL, runtime_capability_revision = NULL, "
        "dispatch_spec_revision = NULL, updated_at = ? "
        "WHERE subject_type = 'asset' AND subject_id = ? AND input_revision = ? "
        "AND task_type = ? AND processor_id = ? "
        f"AND state IN ({placeholders})",
        (
            jobs.STATE_QUEUED,
            int(now_ms),
            int(now_ms),
            str(int(asset_id)),
            str(revision),
            contract.OCR_TASK_TYPE,
            contract.OCR_PROCESSOR_ID,
            *REWINDABLE_STATES,
        ),
    )
    return bool(cursor.rowcount)


def job_counts(catalog: Catalog, *, task_type: str = contract.OCR_TASK_TYPE) -> dict[str, int]:
    return {
        str(row["state"]): int(row["count"])
        for row in catalog.read_all(
            "SELECT state, COUNT(*) AS count FROM jobs WHERE task_type = ? GROUP BY state "
            "ORDER BY state",
            (str(task_type),),
        )
    }


def pending_job_count(catalog: Catalog) -> int:
    counts = job_counts(catalog)
    return sum(
        count for state, count in counts.items() if state in ("QUEUED", "RUNNING", "RETRY_WAIT")
    )


def record_runtime_evidence(
    scheduler: scheduler_engine.Scheduler,
    spec: Any,
    *,
    now_ms: int,
    note: str,
) -> dict[str, Any]:
    """Record the capability snapshot and this processor's qualification evidence.

    The caller must have already created the ONNX sessions from the verified pinned files:
    ``note`` is the record of that evidence, and a provider is only reported healthy once
    those sessions really exist.
    """
    from photo_search.runtime.device import CPU_PROVIDER, runtime_capabilities

    runtime = runtime_capabilities()
    snapshot = snapshot_from_runtime(
        runtime,
        now_ms=int(now_ms),
        health="healthy",
        provider_health={CPU_PROVIDER: "healthy"},
        provider_precisions={CPU_PROVIDER: list(spec.supported_precisions)},
    )
    scheduler.record_capability(snapshot, now_ms=int(now_ms))
    scheduler.record_qualification(
        processor_id=str(spec.processor_id),
        spec_revision=str(spec.spec_revision),
        device="cpu",
        precision=str(spec.supported_precisions[0]),
        provider_name=CPU_PROVIDER,
        runtime_package_id=str(spec.runtime_package_id),
        now_ms=int(now_ms),
        note=str(note),
    )
    return {
        "capability_revision": str(snapshot.capability_revision),
        "runtime_package_id": str(snapshot.runtime_package_id),
        "provider": CPU_PROVIDER,
        "precision": str(spec.supported_precisions[0]),
    }


def build_scheduler(catalog: Catalog, spec: Any) -> scheduler_engine.Scheduler:
    return scheduler_engine.Scheduler(catalog, [spec], lease_seconds=catalog.lease_seconds)


def run_queue(
    catalog: Catalog,
    *,
    data_root: str | Path | None = None,
    worker_id: str = "ocr-cli",
    limit: int | None = None,
    engine_cache: OcrEngineCache | None = None,
    cancel: Callable[[], bool] | None = None,
    deadline_ms: int | None = None,
    clock: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Drain the runnable OCR jobs; bounded, single-threaded, no sleeping."""
    spec = ocr_processor_spec()
    scheduler = build_scheduler(catalog, spec)
    cache = engine_cache or OcrEngineCache()
    # Creating the pinned sessions is the evidence for the "healthy" capability record.
    cache.engine(data_root if data_root is not None else catalog.data_root)
    evidence = record_runtime_evidence(
        scheduler,
        spec,
        now_ms=catalog.now_ms(),
        note="pinned PP-OCRv4 ONNX sessions created on CPUExecutionProvider",
    )
    executor = ocr_executor(
        catalog,
        data_root=data_root,
        engine_cache=cache,
        cancel=cancel,
        deadline_ms=deadline_ms,
        clock=clock,
    )
    steps = 0
    while limit is None or steps < max(0, int(limit)):
        if not scheduler.run_once(executor, worker_id=worker_id, now_ms=catalog.now_ms()):
            break
        steps += 1
    return {
        "steps": steps,
        "jobs": job_counts(catalog),
        "pending_jobs": pending_job_count(catalog),
        **evidence,
    }


def catch_up_projection(catalog: Catalog) -> dict[str, int | str]:
    """Drain the durable reprojection queue in bounded batches (P2-04's own entry point)."""
    return search_documents.catch_up(catalog)
