"""Orchestration of the taken-time extractor: select, enqueue, run, report.

The extractor is an ordinary P2 scheduler job: this module only *feeds* it and reads back
what happened. It creates no thread and no daemon - one call drains the queue and returns,
which is the "no resident scheduler thread" boundary of this build.

``force`` re-reads an already finished asset by rewinding the same five-field logical job
(ADR-0003 §8) with a fresh attempt budget, never by inserting a second job row; a job under
a live lease is never touched. The work set is every catalogued asset, because the chain
tail (``FILE_MTIME``) is available for an asset whose positions are all ``MISSING`` - an
offline source must not leave the asset un-counted.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.jobs import queue as jobs
from photo_search.scheduler import engine as scheduler_engine
from photo_search.scheduler.contracts import RuntimeCapabilities
from photo_search.taken_time import contract
from photo_search.taken_time import read as read_module
from photo_search.taken_time.extract import (
    PYTHON_RUNTIME_PACKAGE_ID,
    TAKEN_TIME_PROCESSOR_ID,
    TAKEN_TIME_TASK_TYPE,
    input_revision_for,
    observation_digest,
    taken_time_executor,
    taken_time_processor_spec,
)

#: States a forced re-extraction may rewind: everything except a live execution.
REWINDABLE_STATES = ("SUCCEEDED", "FAILED", "CANCELLED", "BLOCKED", "RETRY_WAIT")

#: The device provider of the pure-Python backend. The extractor declares the same
#: ``photo-search-runtime@1`` package the GPS extractor does, so the capability a run records
#: has to describe *that* package: the ONNX Runtime package of the model processors is a
#: different runtime and must never be used to qualify (or block) a non-model processor.
PYTHON_PROVIDER = "python-stdlib"


def python_runtime_capability(*, now_ms: int) -> RuntimeCapabilities:
    """The healthy CPU capability of the pure-Python runtime package of this build."""
    import platform

    return RuntimeCapabilities.seal(
        {
            "runtime_package_id": PYTHON_RUNTIME_PACKAGE_ID,
            "runtime_backend": "python",
            "runtime_package_name": "photo-search-runtime",
            "runtime_package_version": "1",
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "driver_version": None,
            "providers": [
                {
                    "name": PYTHON_PROVIDER,
                    "device": "cpu",
                    "precisions": ["not_applicable"],
                    "available": True,
                    "health": "healthy",
                    "health_checked_at": int(now_ms),
                    "evidence_id": None,
                }
            ],
            "health_status": "healthy",
            "health_checked_at": int(now_ms),
            "measured_at": int(now_ms),
            "evidence_ids": [],
            "failure_code": None,
        }
    )


#: One pass over ``assets`` and their registered observations. The *input revision* of a
#: taken-time job is the content hash **plus** the digest of the catalog's recorded
#: ``(location_id, availability, size, mtime_ns)`` rows, because the fact depends on both
#: (ADR-0011 §2/§4.2): a moved recorded file time is a new input, not a replay.
_TARGET_SQL = (
    "SELECT a.asset_id AS asset_id, a.sha256 AS sha256, l.location_id AS location_id, "
    "l.availability AS availability, l.size AS size, l.mtime_ns AS mtime_ns "
    "FROM assets AS a "
    "LEFT JOIN file_locations AS l ON l.asset_id = a.asset_id "
    "{source_filter}"
    "ORDER BY a.asset_id, l.location_id"
)


def taken_time_targets(
    catalog: Catalog,
    *,
    asset_ids: Sequence[int] | None = None,
    source_id: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Every catalogued asset - the extraction work set, in stable ``asset_id`` order."""
    wanted = None if asset_ids is None else {int(value) for value in asset_ids}
    source_filter = ""
    parameters: tuple[Any, ...] = ()
    if source_id is not None:
        source_filter = (
            "WHERE a.asset_id IN (SELECT asset_id FROM file_locations WHERE source_id = ?) "
        )
        parameters = (int(source_id),)
    rows = catalog.read_all(_TARGET_SQL.format(source_filter=source_filter), parameters)
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        asset_id = int(row["asset_id"])
        entry = grouped.setdefault(
            asset_id, {"sha256": str(row["sha256"]), "locations": []}
        )
        if row["location_id"] is not None:
            entry["locations"].append(row)
    targets = [
        {
            "asset_id": asset_id,
            "input_revision": input_revision_for(
                str(entry["sha256"]), observation_digest(entry["locations"])
            ),
        }
        for asset_id, entry in sorted(grouped.items())
        if wanted is None or asset_id in wanted
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
    """Queue (or force-rewind) one extraction job per target, idempotently."""
    queued = existing = rewound = live = 0
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
            if _exists(connection, asset_id=asset_id, revision=revision):
                # The five-field logical job is already recorded: an idempotent enqueue adds
                # nothing, and the counter says so instead of reporting a phantom queue.
                existing += 1
                continue
            jobs.enqueue(
                connection,
                subject_type="asset",
                subject_id=str(asset_id),
                input_revision=revision,
                task_type=TAKEN_TIME_TASK_TYPE,
                processor_id=TAKEN_TIME_PROCESSOR_ID,
                now_ms=int(now_ms),
            )
            queued += 1
    return {
        "targets": len(targets),
        "queued": queued,
        "existing": existing,
        "rewound": rewound,
        "live": live,
    }


def _exists(connection: sqlite3.Connection, *, asset_id: int, revision: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM jobs WHERE subject_type = 'asset' AND subject_id = ? "
        "AND input_revision = ? AND task_type = ? AND processor_id = ?",
        (str(int(asset_id)), str(revision), TAKEN_TIME_TASK_TYPE, TAKEN_TIME_PROCESSOR_ID),
    ).fetchone()
    return row is not None


def _is_live(connection: sqlite3.Connection, *, asset_id: int, revision: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM jobs WHERE subject_type = 'asset' AND subject_id = ? "
        "AND input_revision = ? AND task_type = ? AND processor_id = ? AND state = 'RUNNING' "
        "AND lease_expires_at IS NOT NULL",
        (str(int(asset_id)), str(revision), TAKEN_TIME_TASK_TYPE, TAKEN_TIME_PROCESSOR_ID),
    ).fetchone()
    return row is not None


def _rewind(connection: sqlite3.Connection, *, asset_id: int, revision: str, now_ms: int) -> bool:
    """Reuse the same logical job for a forced re-extraction (never a second row)."""
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
            TAKEN_TIME_TASK_TYPE,
            TAKEN_TIME_PROCESSOR_ID,
            *REWINDABLE_STATES,
        ),
    )
    return bool(cursor.rowcount)


def job_counts(catalog: Catalog, *, task_type: str = TAKEN_TIME_TASK_TYPE) -> dict[str, int]:
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


def coverage(catalog: Catalog) -> dict[str, Any]:
    """The read-only taken-time readiness summary (counts + the rules-version gate)."""
    connection = catalog.connection
    assets_total, extracted, captured = read_module.counts(connection)
    return {
        "rules_version": read_module.rules_version(connection),
        "rules_version_expected": contract.RULES_VERSION,
        "rules_version_matches": read_module.rules_version_matches(connection),
        "assets_total": assets_total,
        "extracted_assets": extracted,
        "capture_time_assets": captured,
        "block": read_module.state_block(connection),
        "jobs": job_counts(catalog),
    }


def build_scheduler(catalog: Catalog, spec: Any) -> scheduler_engine.Scheduler:
    return scheduler_engine.Scheduler(catalog, [spec], lease_seconds=catalog.lease_seconds)


def record_runtime_evidence(
    scheduler: scheduler_engine.Scheduler, spec: Any, *, now_ms: int, note: str
) -> dict[str, Any]:
    """Record the capability snapshot and this processor's qualification evidence."""
    snapshot = python_runtime_capability(now_ms=int(now_ms))
    scheduler.record_capability(snapshot, now_ms=int(now_ms))
    scheduler.record_qualification(
        processor_id=str(spec.processor_id),
        spec_revision=str(spec.spec_revision),
        device="cpu",
        precision=str(spec.supported_precisions[0]),
        provider_name=PYTHON_PROVIDER,
        runtime_package_id=str(spec.runtime_package_id),
        now_ms=int(now_ms),
        note=str(note),
    )
    return {
        "capability_revision": str(snapshot.capability_revision),
        "runtime_package_id": str(snapshot.runtime_package_id),
        "provider": PYTHON_PROVIDER,
        "precision": str(spec.supported_precisions[0]),
    }


def run_queue(
    catalog: Catalog,
    *,
    worker_id: str = "taken-time-cli",
    limit: int | None = None,
    clock: Callable[[], int] | None = None,
) -> dict[str, Any]:
    """Drain the runnable extraction jobs; bounded, single-threaded, no sleeping."""
    spec = taken_time_processor_spec()
    scheduler = build_scheduler(catalog, spec)
    evidence = record_runtime_evidence(
        scheduler,
        spec,
        now_ms=catalog.now_ms(),
        note="pure-Python EXIF header reader, no model weights, CPU only",
    )
    executor = taken_time_executor(catalog, clock=clock)
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


__all__ = [
    "REWINDABLE_STATES",
    "build_scheduler",
    "coverage",
    "enqueue_targets",
    "job_counts",
    "pending_job_count",
    "python_runtime_capability",
    "record_runtime_evidence",
    "run_queue",
    "taken_time_targets",
]
