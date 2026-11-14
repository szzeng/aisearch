"""Orchestration of the OCR processor: select, enqueue, re-record, run, catch up, report.

The OCR processor is an ordinary P2 scheduler job: this module only *feeds* it and reads
back what happened. It creates no thread and no daemon - one call drains the queue and
returns, which is exactly the "no resident scheduler thread" boundary of this build.

``force`` re-recognition reuses the selected single-device profile's five-field logical
identity. The two profiles are coordinated per asset/input so only one can run at a time.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import RUNTIME_CAPABILITY_UNAVAILABLE, CatalogError
from photo_search.jobs import queue as jobs
from photo_search.ocr import contract
from photo_search.ocr.engine import OcrEngineCache
from photo_search.ocr.lease_heartbeat import OcrLeaseHeartbeat
from photo_search.ocr.processor import ocr_executor, ocr_processor_spec
from photo_search.runtime.background_slot import (
    BackgroundSlotBusy,
    background_model_slot,
    foreground_model_waiting,
)
from photo_search.scheduler import capability as capability_module
from photo_search.scheduler import engine as scheduler_engine
from photo_search.scheduler.capability import snapshot_from_runtime
from photo_search.scheduler.contracts import RuntimeCapabilities
from photo_search.search import documents as search_documents

#: States a forced re-recognition may rewind: everything except a live execution.
REWINDABLE_STATES = ("SUCCEEDED", "FAILED", "CANCELLED", "BLOCKED", "RETRY_WAIT")
_LIVE_PROCESSOR_IDS = (
    *contract.OCR_RETIRED_PROCESSOR_IDS,
    *contract.OCR_ACTIVE_PROCESSOR_IDS,
)
_RETIRED_PENDING_STATES = (
    jobs.STATE_QUEUED,
    jobs.STATE_RETRY_WAIT,
    jobs.STATE_BLOCKED,
    jobs.STATE_RUNNING,
)
_RETIRED_REASON = "OCR_PROCESSOR_RETIRED"
DEFAULT_RECONCILE_BATCH_SIZE = 100
OCR_GPU_WAIT_SECONDS = 60.0
OCR_GPU_BATCH_LIMIT = 8


def processor_id_for_request(requested_device: str) -> str:
    if requested_device == "cpu":
        return contract.OCR_CPU_PROCESSOR_ID
    if requested_device in ("auto", "gpu"):
        return contract.OCR_GPU_PROCESSOR_ID
    raise ValueError("requested_device must be auto, cpu, or gpu")

_TARGET_SQL = (
    "SELECT DISTINCT a.asset_id AS asset_id, a.sha256 AS sha256 "
    "FROM assets AS a "
    "JOIN file_locations AS f ON f.asset_id = a.asset_id AND f.availability = 'PRESENT' "
    "JOIN source_roots AS s ON s.source_id = f.source_id AND s.status = 'ONLINE' "
    "WHERE {filters} "
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
    wanted = None if asset_ids is None else sorted({int(value) for value in asset_ids})
    if wanted == []:
        return []
    filters = ["f.availability = 'PRESENT'"]
    parameters: list[Any] = []
    if wanted is not None:
        filters.append("a.asset_id IN (" + ",".join("?" for _ in wanted) + ")")
        parameters.extend(wanted)
    if source_id is not None:
        filters.append("f.source_id = ?")
        parameters.append(int(source_id))
    sql = _TARGET_SQL.format(filters=" AND ".join(filters))
    if limit is not None:
        sql += " LIMIT ?"
        parameters.append(max(0, int(limit)))
    rows = catalog.read_all(sql, tuple(parameters))
    targets = [
        {"asset_id": int(row["asset_id"]), "input_revision": str(row["sha256"])}
        for row in rows
    ]
    return targets


def reconcile_retired_ocr_jobs(
    catalog: Catalog,
    *,
    now_ms: int | None = None,
    batch_size: int = DEFAULT_RECONCILE_BATCH_SIZE,
    dry_run: bool = False,
) -> dict[str, int | bool]:
    """Safely cancel one bounded batch of jobs for explicitly retired OCR processors.

    A RUNNING row is eligible only when its lease is structurally complete and expired.
    The write is a short state-and-lease CAS; a worker that renewed or replaced its lease
    wins. Unknown processor IDs and all historical terminal rows are left untouched.
    """
    limit = max(0, int(batch_size))
    if limit == 0 or not contract.OCR_RETIRED_PROCESSOR_IDS:
        return {
            "examined": 0,
            "would_cancel": 0,
            "cancelled": 0,
            "live_leases": 0,
            "lease_anomalies": 0,
            "cas_conflicts": 0,
            "remaining": _retired_pending_count(catalog),
            "has_more": _retired_pending_count(catalog) > 0,
        }
    now = int(catalog.now_ms() if now_ms is None else now_ms)
    processors = tuple(contract.OCR_RETIRED_PROCESSOR_IDS)
    processor_slots = ", ".join("?" for _ in processors)
    state_slots = ", ".join("?" for _ in _RETIRED_PENDING_STATES)
    select_sql = (
        "SELECT job_id, subject_type, subject_id, input_revision, task_type, processor_id, "
        "state, lease_token, lease_expires_at, worker_id FROM jobs "
        f"WHERE task_type = ? AND processor_id IN ({processor_slots}) "
        f"AND state IN ({state_slots}) "
        "ORDER BY CASE WHEN state = 'RUNNING' THEN 1 ELSE 0 END, job_id LIMIT ?"
    )
    parameters = (contract.OCR_TASK_TYPE, *processors, *_RETIRED_PENDING_STATES, limit)
    counters: dict[str, int | bool] = {
        "examined": 0,
        "would_cancel": 0,
        "cancelled": 0,
        "live_leases": 0,
        "lease_anomalies": 0,
        "cas_conflicts": 0,
    }

    def visit(connection: sqlite3.Connection, *, apply: bool) -> None:
        rows = connection.execute(select_sql, parameters).fetchall()
        counters["examined"] = len(rows)
        for row in rows:
            state = str(row["state"])
            if state == jobs.STATE_RUNNING:
                token = row["lease_token"]
                worker = row["worker_id"]
                expires_raw = row["lease_expires_at"]
                try:
                    expires = None if expires_raw is None else int(expires_raw)
                except (TypeError, ValueError, OverflowError):
                    expires = None
                if not token or not worker or expires is None:
                    counters["lease_anomalies"] = int(counters["lease_anomalies"]) + 1
                    continue
                if expires > now:
                    counters["live_leases"] = int(counters["live_leases"]) + 1
                    continue
            if jobs.STATE_CANCELLED not in jobs.ALLOWED_TRANSITIONS.get(state, frozenset()):
                continue
            counters["would_cancel"] = int(counters["would_cancel"]) + 1
            if not apply:
                continue
            cursor = connection.execute(
                "UPDATE jobs SET state = 'CANCELLED', lease_token = NULL, "
                "lease_expires_at = NULL, worker_id = NULL, last_error_code = ?, "
                "last_error_message = ?, blocking_reason = NULL, updated_at = ? "
                "WHERE job_id = ? AND subject_type = ? AND subject_id = ? "
                "AND input_revision = ? AND task_type = ? AND processor_id = ? AND state = ? "
                "AND lease_token IS ? AND lease_expires_at IS ? AND worker_id IS ?",
                (
                    _RETIRED_REASON,
                    f"OCR processor {row['processor_id']} is retired; no new work will be claimed",
                    now,
                    int(row["job_id"]),
                    str(row["subject_type"]),
                    str(row["subject_id"]),
                    str(row["input_revision"]),
                    str(row["task_type"]),
                    str(row["processor_id"]),
                    state,
                    row["lease_token"],
                    row["lease_expires_at"],
                    row["worker_id"],
                ),
            )
            if cursor.rowcount:
                counters["cancelled"] = int(counters["cancelled"]) + 1
            else:
                counters["cas_conflicts"] = int(counters["cas_conflicts"]) + 1

    if dry_run:
        visit(catalog.connection, apply=False)
    else:
        with catalog.write() as connection:
            visit(connection, apply=True)
    remaining = _retired_pending_count(catalog)
    counters["remaining"] = remaining
    counters["has_more"] = remaining > 0
    return counters


def _retired_pending_count(catalog: Catalog) -> int:
    processors = tuple(contract.OCR_RETIRED_PROCESSOR_IDS)
    if not processors:
        return 0
    processor_slots = ", ".join("?" for _ in processors)
    state_slots = ", ".join("?" for _ in _RETIRED_PENDING_STATES)
    row = catalog.read_one(
        "SELECT COUNT(*) AS total FROM jobs WHERE task_type = ? "
        f"AND processor_id IN ({processor_slots}) AND state IN ({state_slots})",
        (contract.OCR_TASK_TYPE, *processors, *_RETIRED_PENDING_STATES),
    )
    return int(row["total"])


def enqueue_targets(
    catalog: Catalog,
    targets: Sequence[dict[str, Any]],
    *,
    now_ms: int,
    force: bool = False,
    requested_device: str = "auto",
) -> dict[str, int]:
    """Queue (or force-requeue) one OCR job per target, idempotently."""
    reconcile_retired_ocr_jobs(catalog, now_ms=now_ms)
    queued = rewound = live = 0
    processor_id = processor_id_for_request(str(requested_device))
    with catalog.write() as connection:
        for target in targets:
            asset_id = int(target["asset_id"])
            revision = str(target["input_revision"])
            if _is_live(connection, asset_id=asset_id, revision=revision, now_ms=now_ms):
                live += 1
                continue
            _cancel_competing_pending(
                connection,
                asset_id=asset_id,
                revision=revision,
                selected_processor_id=processor_id,
                now_ms=now_ms,
                requested_device=str(requested_device),
            )
            connection.execute(
                "UPDATE jobs SET requested_device = ?, updated_at = ? "
                "WHERE subject_type = 'asset' AND subject_id = ? AND input_revision = ? "
                "AND task_type = ? AND processor_id = ?",
                (str(requested_device), int(now_ms), str(asset_id), revision,
                 contract.OCR_TASK_TYPE, processor_id),
            )
            other_ids = tuple(
                value for value in contract.OCR_PROCESSOR_IDS if value != processor_id
            )
            other_placeholders = ", ".join("?" for _ in other_ids)
            connection.execute(
                "UPDATE jobs SET requested_device = ?, updated_at = ? "
                "WHERE subject_type = 'asset' AND subject_id = ? AND input_revision = ? "
                "AND task_type = ? AND processor_id IN (" + other_placeholders + ") "
                "AND state IN ('CANCELLED', 'FAILED')",
                (str(requested_device), int(now_ms), str(asset_id), revision,
                 contract.OCR_TASK_TYPE, *other_ids),
            )
            if force and _rewind(
                connection,
                asset_id=asset_id,
                revision=revision,
                now_ms=now_ms,
                requested_device=requested_device,
                processor_id=processor_id,
            ):
                rewound += 1
                continue
            existing = connection.execute(
                "SELECT 1 FROM jobs WHERE subject_type = 'asset' AND subject_id = ? "
                "AND input_revision = ? AND task_type = ? AND processor_id = ?",
                (str(asset_id), revision, contract.OCR_TASK_TYPE, processor_id),
            ).fetchone()
            jobs.enqueue(
                connection,
                subject_type="asset",
                subject_id=str(asset_id),
                input_revision=revision,
                task_type=contract.OCR_TASK_TYPE,
                processor_id=processor_id,
                now_ms=int(now_ms),
                requested_device=str(requested_device),
            )
            queued += int(existing is None)
    return {"targets": len(targets), "queued": queued, "rewound": rewound, "live": live}


def _is_live(
    connection: sqlite3.Connection, *, asset_id: int, revision: str, now_ms: int
) -> bool:
    placeholders = ", ".join("?" for _ in _LIVE_PROCESSOR_IDS)
    row = connection.execute(
        "SELECT 1 FROM jobs WHERE subject_type = 'asset' AND subject_id = ? "
        "AND input_revision = ? AND task_type = ? AND processor_id IN (" + placeholders + ") "
        "AND state = 'RUNNING' AND (lease_expires_at > ? OR lease_expires_at IS NULL "
        "OR lease_token IS NULL OR lease_token = '' OR worker_id IS NULL OR worker_id = '')",
        (str(int(asset_id)), str(revision), contract.OCR_TASK_TYPE, *_LIVE_PROCESSOR_IDS, int(now_ms)),
    ).fetchone()
    return row is not None


def _cancel_competing_pending(
    connection: sqlite3.Connection,
    *,
    asset_id: int,
    revision: str,
    selected_processor_id: str,
    now_ms: int,
    requested_device: str,
) -> None:
    other_ids = tuple(
        value for value in contract.OCR_PROCESSOR_IDS if value != selected_processor_id
    )
    placeholders = ", ".join("?" for _ in other_ids)
    rows = connection.execute(
        "SELECT job_id, state FROM jobs WHERE subject_type = 'asset' AND subject_id = ? "
        "AND input_revision = ? AND task_type = ? AND processor_id IN (" + placeholders + ") "
        "AND (state IN ('QUEUED', 'RETRY_WAIT', 'BLOCKED') OR "
        "(state = 'RUNNING' AND lease_expires_at <= ?))",
        (str(int(asset_id)), str(revision), contract.OCR_TASK_TYPE, *other_ids, int(now_ms)),
    ).fetchall()
    for row in rows:
        state = str(row["state"])
        if jobs.STATE_CANCELLED not in jobs.ALLOWED_TRANSITIONS[state]:
            continue
        connection.execute(
            "UPDATE jobs SET state = 'CANCELLED', requested_device = ?, lease_token = NULL, "
            "lease_expires_at = NULL, worker_id = NULL, updated_at = ? WHERE job_id = ?",
            (str(requested_device), int(now_ms), int(row["job_id"])),
        )


def _rewind(
    connection: sqlite3.Connection,
    *,
    asset_id: int,
    revision: str,
    now_ms: int,
    requested_device: str,
    processor_id: str,
) -> bool:
    """Reuse the same logical job for a forced re-recognition (never a second row)."""
    placeholders = ", ".join("?" for _ in REWINDABLE_STATES)
    cursor = connection.execute(
        "UPDATE jobs SET state = ?, attempts = 0, progress = 0.0, lease_token = NULL, "
        "lease_expires_at = NULL, worker_id = NULL, run_after = ?, last_error_code = NULL, "
        "last_error_message = NULL, blocking_reason = NULL, resolved_device = NULL, "
        "runtime_provider = NULL, precision = NULL, runtime_capability_revision = NULL, "
        "dispatch_spec_revision = NULL, requested_device = ?, updated_at = ? "
        "WHERE subject_type = 'asset' AND subject_id = ? AND input_revision = ? "
        "AND task_type = ? AND processor_id = ? "
        f"AND state IN ({placeholders})",
        (
            jobs.STATE_QUEUED,
            int(now_ms),
            str(requested_device),
            int(now_ms),
            str(int(asset_id)),
            str(revision),
            contract.OCR_TASK_TYPE,
            str(processor_id),
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
    processors = tuple(contract.OCR_ACTIVE_PROCESSOR_IDS)
    slots = ", ".join("?" for _ in processors)
    row = catalog.read_one(
        "SELECT COUNT(DISTINCT j.subject_id) AS total FROM jobs AS j "
        "JOIN assets AS a ON a.asset_id = CAST(j.subject_id AS INTEGER) "
        "WHERE j.subject_type = 'asset' AND j.task_type = ? "
        f"AND j.processor_id IN ({slots}) AND j.state IN ('QUEUED', 'RUNNING', 'RETRY_WAIT') "
        "AND j.input_revision = a.sha256",
        (contract.OCR_TASK_TYPE, *processors),
    )
    return int(row["total"])


def record_runtime_evidence(
    scheduler: scheduler_engine.Scheduler,
    spec: Any,
    cache: OcrEngineCache,
    *,
    data_root: str | Path,
    now_ms: int,
    note: str,
    devices: Sequence[str] = ("cpu", "gpu"),
    observation_clock: Callable[[], int] | None = None,
) -> dict[str, Any]:
    """Record the capability snapshot and this processor's qualification evidence.

    The caller must have already created the ONNX sessions from the verified pinned files:
    ``note`` is the record of that evidence, and a provider is only reported healthy once
    those sessions really exist.
    """
    from photo_search.runtime.device import (
        CPU_PROVIDER,
        CUDA_PROVIDER,
        runtime_capabilities,
    )

    requested_devices = tuple(dict.fromkeys(str(device) for device in devices))
    if not requested_devices or any(device not in ("cpu", "gpu") for device in requested_devices):
        raise ValueError("OCR evidence requires cpu and/or gpu")
    engines_by_device: dict[str, Any] = {}
    probe_errors: dict[str, Exception] = {}
    sample_clock = observation_clock or (lambda: int(now_ms))
    device_observed_at: dict[str, int] = {}
    shared_failure_codes = {
        "OCR_WEIGHTS_MISSING", "OCR_WEIGHTS_HASH_MISMATCH", "OCR_RUNTIME_UNAVAILABLE"
    }
    accepted_probe_codes = {
        RUNTIME_CAPABILITY_UNAVAILABLE,
        "OCR_GPU_INITIALIZATION_FAILED",
        "OCR_SESSION_PROVIDER_UNVERIFIED",
        "RUNTIME_RESOLUTION_MISMATCH",
    }
    for device_name in requested_devices:
        try:
            engine = cache.engine(data_root, resolved_device=device_name, orientation_retry=True)
            verify_provider = getattr(engine, "verify_runtime_provider", None)
            if callable(verify_provider):
                verify_provider()
            engines_by_device[device_name] = engine
        except Exception as error:  # each device contributes independent qualification evidence
            code = str(getattr(error, "code", ""))
            if code in shared_failure_codes:
                raise
            if code not in accepted_probe_codes:
                raise
            if device_name == "gpu" and code == "RUNTIME_RESOLUTION_MISMATCH":
                error = CatalogError(
                    "OCR_GPU_INITIALIZATION_FAILED",
                    "the CUDA OCR session initialized with a different provider",
                    details={"cause_code": code},
                )
            probe_errors[device_name] = error
        finally:
            device_observed_at[device_name] = int(sample_clock())
    cpu_engine = engines_by_device.get("cpu")
    gpu_engine = engines_by_device.get("gpu")
    runtime = runtime_capabilities()
    enumerated_at = int(sample_clock())
    provider_health: dict[str, str] = {}
    provider_precisions: dict[str, list[str]] = {}
    if CPU_PROVIDER in runtime.providers and "cpu" in requested_devices:
        provider_health[CPU_PROVIDER] = "healthy" if cpu_engine is not None else "unhealthy"
        provider_precisions[CPU_PROVIDER] = list(spec.supported_precisions)
    if CUDA_PROVIDER in runtime.providers and "gpu" in requested_devices:
        provider_health[CUDA_PROVIDER] = "healthy" if gpu_engine is not None else "unhealthy"
        provider_precisions[CUDA_PROVIDER] = list(spec.supported_precisions)
    representative_error = probe_errors.get("gpu") or probe_errors.get("cpu")
    snapshot = snapshot_from_runtime(
        runtime,
        now_ms=enumerated_at,
        health=("healthy" if not probe_errors else "degraded"),
        provider_health=provider_health,
        provider_precisions=provider_precisions,
        failure_code=(
            str(getattr(representative_error, "code", ""))
            if representative_error is not None
            else None
        ),
    )
    facts = dict(snapshot.envelope)
    facts["providers"] = [dict(provider) for provider in facts["providers"]]
    for provider in facts["providers"]:
        device_name = provider["device"]
        if device_name in device_observed_at:
            provider["health_checked_at"] = device_observed_at[device_name]
    snapshot = scheduler.record_observation(
        RuntimeCapabilities.seal(facts), observed_devices=requested_devices,
        now_ms=int(scheduler.catalog.now_ms()),
    )
    engines = [
        ("cpu", cpu_engine, CPU_PROVIDER),
        ("gpu", gpu_engine, CUDA_PROVIDER),
    ]
    specs = {item.processor_id: item for item in scheduler.specs}
    qualified: list[tuple[str, Any, str]] = []
    for device_name, engine, expected_provider in engines:
        if engine is None:
            continue
        profile_spec = specs.get(
            contract.OCR_CPU_PROCESSOR_ID if device_name == "cpu" else contract.OCR_GPU_PROCESSOR_ID
        )
        if (
            profile_spec is None
            or expected_provider not in runtime.providers
            or engine.runtime_provider != expected_provider
        ):
            continue
        scheduler.record_qualification(
            processor_id=str(profile_spec.processor_id),
            spec_revision=str(profile_spec.spec_revision),
            device=device_name,
            precision=str(profile_spec.supported_precisions[0]),
            provider_name=expected_provider,
            runtime_package_id=str(profile_spec.runtime_package_id),
            now_ms=device_observed_at[device_name],
            note=str(note),
        )
        qualified.append((device_name, engine, expected_provider))
    return {
        "capability_revision": str(snapshot.capability_revision),
        "runtime_package_id": str(snapshot.runtime_package_id),
        "qualified_devices": [name for name, _, _ in qualified],
        "cpu_provider": None if cpu_engine is None else cpu_engine.runtime_provider,
        "gpu_provider": None if gpu_engine is None else gpu_engine.runtime_provider,
        "cpu_probe_error": None if "cpu" not in probe_errors else {
            "type": type(probe_errors["cpu"]).__name__,
            "code": getattr(probe_errors["cpu"], "code", None),
        },
        "gpu_probe_error": None if "gpu" not in probe_errors else {
            "type": type(probe_errors["gpu"]).__name__,
            "code": getattr(probe_errors["gpu"], "code", None),
        },
    }


def build_scheduler(catalog: Catalog, spec: Any = None) -> scheduler_engine.Scheduler:
    del spec  # kept as a source-compatible optional argument for existing callers
    specs = (ocr_processor_spec("cpu"), ocr_processor_spec("gpu"))
    return scheduler_engine.Scheduler(
        catalog,
        specs,
        lease_seconds=catalog.lease_seconds,
        lease_clock=catalog.lease_now_ms,
    )


def _eligible_gpu_fallback_code(code: str, state: str, blocking_reason: str | None) -> bool:
    if state == jobs.STATE_FAILED:
        return code in (
            "OCR_GPU_INITIALIZATION_FAILED",
            "OCR_GPU_EXECUTION_FAILED",
        )
    return (
        state == jobs.STATE_BLOCKED
        and str(blocking_reason or code)
        in (RUNTIME_CAPABILITY_UNAVAILABLE, "OCR_GPU_INITIALIZATION_FAILED")
    )


def _requeue_auto_fallback_cpu(
    connection: sqlite3.Connection, *, row: sqlite3.Row, now_ms: int
) -> bool:
    """Restore only this OCR auto handoff's cancelled/failed CPU row under a CAS."""
    prior_state = str(row["state"])
    if prior_state not in (jobs.STATE_CANCELLED, jobs.STATE_FAILED):
        return False
    cursor = connection.execute(
        "UPDATE jobs SET state = ?, attempts = 0, progress = 0.0, lease_token = NULL, "
        "lease_expires_at = NULL, worker_id = NULL, run_after = ?, requested_device = 'cpu', "
        "last_error_code = NULL, last_error_message = NULL, blocking_reason = NULL, "
        "resolved_device = NULL, runtime_provider = NULL, precision = NULL, "
        "runtime_capability_revision = NULL, dispatch_spec_revision = NULL, updated_at = ? "
        "WHERE job_id = ? AND subject_type = 'asset' AND task_type = ? "
        "AND processor_id = ? AND state = ? "
        "AND requested_device = 'auto' AND lease_token IS NULL "
        "AND lease_expires_at IS NULL AND worker_id IS NULL",
        (
            jobs.STATE_QUEUED,
            int(now_ms),
            int(now_ms),
            int(row["job_id"]),
            contract.OCR_TASK_TYPE,
            contract.OCR_CPU_PROCESSOR_ID,
            prior_state,
        ),
    )
    return bool(cursor.rowcount)


_PROBE_NOT_GIVEN = object()


def _coordinate_gpu_fallbacks(
    catalog: Catalog, *, now_ms: int, gpu_probe_error: Any = _PROBE_NOT_GIVEN,
    job_ids: Sequence[int] | None = None,
) -> int:
    """Atomically stop a recoverable auto GPU branch and create its CPU counterpart."""
    created = 0
    if job_ids is not None and not job_ids:
        return 0
    scope = "" if job_ids is None else " AND job_id IN (" + ",".join("?" for _ in job_ids) + ")"
    with catalog.write() as connection:
        rows = connection.execute(
            "SELECT job_id, subject_id, input_revision, state, requested_device, "
            "last_error_code, blocking_reason FROM jobs WHERE task_type = ? AND processor_id = ? "
            "AND state IN ('BLOCKED', 'FAILED')" + scope + " ORDER BY job_id",
            (contract.OCR_TASK_TYPE, contract.OCR_GPU_PROCESSOR_ID, *(job_ids or ())),
        ).fetchall()
        for row in rows:
            if str(row["requested_device"]) != "auto":
                continue
            code = str(row["last_error_code"] or "")
            if code == "OCR_SESSION_PROVIDER_UNVERIFIED" or str(
                row["blocking_reason"] or ""
            ) == "OCR_SESSION_PROVIDER_UNVERIFIED":
                continue
            if str(row["state"]) == jobs.STATE_BLOCKED and code == RUNTIME_CAPABILITY_UNAVAILABLE:
                if gpu_probe_error is _PROBE_NOT_GIVEN:
                    snapshot = capability_module.latest_capability(
                        connection, runtime_package_id=contract.OCR_RUNTIME_PACKAGE_ID
                    )
                    observed_code = None if snapshot is None else snapshot.failure_code
                else:
                    observed_code = gpu_probe_error
                    if observed_code is None:
                        continue
                if observed_code == "OCR_SESSION_PROVIDER_UNVERIFIED":
                    connection.execute(
                        "UPDATE jobs SET last_error_code = ? WHERE job_id = ? "
                        "AND state = 'BLOCKED' AND last_error_code = ?",
                        (
                            "OCR_SESSION_PROVIDER_UNVERIFIED",
                            int(row["job_id"]),
                            RUNTIME_CAPABILITY_UNAVAILABLE,
                        ),
                    )
                    continue
                if observed_code in (
                    RUNTIME_CAPABILITY_UNAVAILABLE,
                    "OCR_GPU_INITIALIZATION_FAILED",
                ):
                    code = str(observed_code)
                    connection.execute(
                        "UPDATE jobs SET last_error_code = ?, blocking_reason = ? WHERE job_id = ?",
                        (code, code, int(row["job_id"])),
                    )
            if not _eligible_gpu_fallback_code(code, str(row["state"]), row["blocking_reason"]):
                continue
            if str(row["state"]) == jobs.STATE_BLOCKED:
                if jobs.STATE_CANCELLED not in jobs.ALLOWED_TRANSITIONS[jobs.STATE_BLOCKED]:
                    continue
                # Keep the GPU failure code/message for diagnosis while preventing a later
                # health recovery from reviving and overtaking the CPU result.
                connection.execute(
                    "UPDATE jobs SET state = 'CANCELLED', blocking_reason = ?, updated_at = ? "
                    "WHERE job_id = ? AND state = 'BLOCKED'",
                    (code, int(now_ms), int(row["job_id"])),
                )
            before = connection.execute(
                "SELECT job_id, state, requested_device, lease_token, lease_expires_at, worker_id "
                "FROM jobs WHERE subject_type = 'asset' AND subject_id = ? "
                "AND input_revision = ? AND task_type = ? AND processor_id = ?",
                (
                    str(row["subject_id"]),
                    str(row["input_revision"]),
                    contract.OCR_TASK_TYPE,
                    contract.OCR_CPU_PROCESSOR_ID,
                ),
            ).fetchone()
            jobs.enqueue(
                connection,
                subject_type="asset",
                subject_id=str(row["subject_id"]),
                input_revision=str(row["input_revision"]),
                task_type=contract.OCR_TASK_TYPE,
                processor_id=contract.OCR_CPU_PROCESSOR_ID,
                now_ms=int(now_ms),
                requested_device="cpu",
            )
            if before is None:
                created += 1
            elif str(before["state"]) in (jobs.STATE_CANCELLED, jobs.STATE_FAILED):
                # Reopen only the terminal OCR CPU row authorized by this auto GPU branch;
                # task/profile/marker/lease predicates are enforced by the scoped CAS.
                if (
                    str(before["requested_device"]) == "auto"
                    and before["lease_token"] is None
                    and before["lease_expires_at"] is None
                    and before["worker_id"] is None
                ):
                    created += int(
                        _requeue_auto_fallback_cpu(
                            connection, row=before, now_ms=int(now_ms)
                        )
                    )
    return created


def run_queue(
    catalog: Catalog,
    *,
    data_root: str | Path | None = None,
    worker_id: str = "ocr-cli",
    limit: int | None = None,
    engine_cache: OcrEngineCache | None = None,
    cancel: Callable[[], bool] | None = None,
    stop_check: Callable[[], bool] | None = None,
    deadline_ms: int | None = None,
    clock: Callable[[], float] | None = None,
    asset_ids: Sequence[int] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Drain the runnable OCR jobs; bounded, single-threaded, no sleeping."""
    reconcile_retired_ocr_jobs(catalog, now_ms=catalog.now_ms())
    cpu_spec, gpu_spec = ocr_processor_spec("cpu"), ocr_processor_spec("gpu")
    cpu_scheduler = scheduler_engine.Scheduler(
        catalog,
        [cpu_spec],
        lease_seconds=catalog.lease_seconds,
        lease_clock=catalog.lease_now_ms,
    )
    gpu_scheduler = scheduler_engine.Scheduler(
        catalog,
        [gpu_spec],
        lease_seconds=catalog.lease_seconds,
        lease_clock=catalog.lease_now_ms,
    )
    selected_assets = None if asset_ids is None else tuple(sorted({int(value) for value in asset_ids}))
    cache = engine_cache or OcrEngineCache()
    resolved_data_root = data_root if data_root is not None else catalog.data_root
    executor = ocr_executor(
        catalog, data_root=data_root, engine_cache=cache, cancel=cancel,
        deadline_ms=deadline_ms, clock=clock,
    )
    evidence: dict[str, Any] = {}
    gpu_evidence: dict[str, Any] = {}
    cpu_probed = False
    stopped = False
    gpu_exhausted = False
    gpu_wait_started: float | None = None
    waiting_gpu = False
    steps = 0
    remaining = None if limit is None else max(0, int(limit))

    def report_progress(*, waiting_reason: str | None = None) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(
                {
                    "steps": int(steps),
                    "detail": "recognizing_text",
                    "waiting_reason": waiting_reason,
                }
            )
        except Exception:
            pass

    report_progress()
    heartbeat = OcrLeaseHeartbeat(catalog, lease_seconds=catalog.lease_seconds)
    try:
        while remaining is None or steps < remaining:
            if stop_check is not None and stop_check():
                stopped = True
                break
            scoped_jobs = None if selected_assets is None else _job_ids_for_assets(catalog, selected_assets)
            cpu_scheduler.job_ids = scoped_jobs
            gpu_scheduler.job_ids = scoped_jobs
            cpu_ran = False
            if _processor_has_work(catalog, contract.OCR_CPU_PROCESSOR_ID, scoped_jobs):
                if not cpu_probed:
                    evidence = record_runtime_evidence(
                        cpu_scheduler, cpu_spec, cache, data_root=resolved_data_root,
                        now_ms=catalog.now_ms(), note="OCR CPU session inspected", devices=("cpu",),
                        observation_clock=catalog.now_ms,
                    )
                    cpu_scheduler.capability_revision = str(evidence["capability_revision"])
                    cpu_probed = True
                if (stop_check is not None and stop_check()) or (
                    steps > 0 and cancel is not None and cancel()
                ):
                    stopped = True
                    break
                if cpu_scheduler.run_once(
                    executor, worker_id=worker_id, now_ms=catalog.now_ms(),
                    completion_clock=catalog.now_ms,
                    lease_monitor=lambda request: heartbeat.track(request, worker_id),
                ):
                    steps += 1
                    cpu_ran = True
                    report_progress()
                    if cancel is not None and cancel():
                        stopped = True
                        break
                    if remaining is not None and steps >= remaining:
                        break
            if not gpu_exhausted and _processor_has_work(
                catalog, contract.OCR_GPU_PROCESSOR_ID, scoped_jobs, include_blocked=True,
            ):
                try:
                    # Acquire before probing or claiming, outside any SQLite transaction.
                    with background_model_slot(timeout=0):
                        report_progress()
                        if foreground_model_waiting():
                            raise BackgroundSlotBusy("foreground model request is waiting")
                        had_waited = gpu_wait_started is not None
                        waiting_gpu = False
                        gpu_wait_started = None
                        if (stop_check is not None and stop_check()) or (
                            (had_waited or steps > 0) and cancel is not None and cancel()
                        ):
                            stopped = True
                            break
                        evidence = record_runtime_evidence(
                            gpu_scheduler, gpu_spec, cache, data_root=resolved_data_root,
                            now_ms=catalog.now_ms(), note="OCR GPU session inspected", devices=("gpu",),
                            observation_clock=catalog.now_ms,
                        )
                        gpu_evidence = dict(evidence)
                        gpu_scheduler.capability_revision = str(evidence["capability_revision"])
                        if (stop_check is not None and stop_check()) or (
                            (had_waited or steps > 0) and cancel is not None and cancel()
                        ):
                            stopped = True
                            break
                        probe_error = gpu_evidence.get("gpu_probe_error")
                        probe_code = None if probe_error is None else probe_error.get("code")
                        if probe_code is None and "gpu" not in gpu_evidence.get("qualified_devices", []):
                            probe_code = RUNTIME_CAPABILITY_UNAVAILABLE
                        if probe_code is None:
                            gpu_scheduler.recover_blocked(
                                now_ms=catalog.now_ms(), capability_revision=gpu_scheduler.capability_revision,
                            )
                        batch_progress = False
                        for _ in range(OCR_GPU_BATCH_LIMIT):
                            if foreground_model_waiting():
                                if batch_progress:
                                    break
                                raise BackgroundSlotBusy("foreground model request is waiting")
                            if stop_check is not None and stop_check():
                                stopped = True
                                break
                            if (had_waited or steps > 0) and cancel is not None and cancel():
                                stopped = True
                                break
                            scoped_jobs = None if selected_assets is None else _job_ids_for_assets(catalog, selected_assets)
                            gpu_scheduler.job_ids = scoped_jobs
                            ran = gpu_scheduler.run_once(
                                executor, worker_id=worker_id, now_ms=catalog.now_ms(),
                                completion_clock=catalog.now_ms,
                                lease_monitor=lambda request: heartbeat.track(request, worker_id),
                            )
                            if ran:
                                steps += 1
                                batch_progress = True
                                report_progress()
                            if cancel is not None and cancel():
                                stopped = True
                                break
                            fallback = _coordinate_gpu_fallbacks(
                                catalog, now_ms=catalog.now_ms(), gpu_probe_error=probe_code,
                                job_ids=scoped_jobs,
                            )
                            batch_progress = batch_progress or bool(fallback)
                            if not ran:
                                gpu_exhausted = True
                            if not ran or fallback or (remaining is not None and steps >= remaining):
                                break
                            if _processor_has_work(catalog, contract.OCR_CPU_PROCESSOR_ID, scoped_jobs):
                                break
                        if stopped:
                            break
                        if batch_progress:
                            continue
                except BackgroundSlotBusy:
                    waiting_gpu = True
                    report_progress(waiting_reason="MODEL_RESOURCE")
                    if cancel is not None and cancel():
                        stopped = True
                        break
                    if gpu_wait_started is None:
                        gpu_wait_started = time.monotonic()
                    if cpu_ran:
                        continue
                    if time.monotonic() - gpu_wait_started >= OCR_GPU_WAIT_SECONDS:
                        break
                    time.sleep(0.1)
                    continue
            if not cpu_ran:
                break
    finally:
        heartbeat.close()
    fallback_scope = "" if selected_assets is None else (
        " AND c.subject_id IN (" + ",".join("?" for _ in selected_assets) + ")"
        if selected_assets else " AND 0"
    )
    fallbacks = catalog.read_all(
        "SELECT c.job_id, c.subject_id, c.input_revision, g.last_error_code AS fallback_reason "
        "FROM jobs AS c JOIN jobs AS g ON g.subject_type = c.subject_type "
        "AND g.subject_id = c.subject_id AND g.input_revision = c.input_revision "
        "AND g.task_type = c.task_type AND g.processor_id = ? "
        "WHERE c.task_type = ? AND c.processor_id = ? "
        "AND g.last_error_code IN (?, ?, ?)" + fallback_scope + " ORDER BY c.job_id",
        (
            contract.OCR_GPU_PROCESSOR_ID,
            contract.OCR_TASK_TYPE,
            contract.OCR_CPU_PROCESSOR_ID,
            RUNTIME_CAPABILITY_UNAVAILABLE,
            "OCR_GPU_INITIALIZATION_FAILED",
            "OCR_GPU_EXECUTION_FAILED",
            *(str(asset_id) for asset_id in (selected_assets or ())),
        ),
    )
    if gpu_evidence:
        evidence = {**evidence, **gpu_evidence}
    return {
        "steps": steps,
        "jobs": job_counts(catalog),
        "pending_jobs": pending_job_count(catalog),
        "cpu_fallbacks": [dict(row) for row in fallbacks],
        "waiting_gpu": waiting_gpu,
        "stopped": stopped,
        **evidence,
    }


def _processor_has_work(
    catalog: Catalog, processor_id: str, scoped_jobs: Sequence[int] | None,
    *, include_blocked: bool = False,
) -> bool:
    if scoped_jobs is not None and not scoped_jobs:
        return False
    runnable = "state IN ('QUEUED','RETRY_WAIT','RUNNING')"
    if include_blocked:
        runnable = (
            "(" + runnable + " OR (state IN ('BLOCKED','FAILED') AND requested_device='auto' "
            "AND last_error_code IN ('RUNTIME_CAPABILITY_UNAVAILABLE',"
            "'OCR_GPU_INITIALIZATION_FAILED','OCR_GPU_EXECUTION_FAILED')))"
        )
    scope = "" if scoped_jobs is None else " AND job_id IN (" + ",".join("?" for _ in scoped_jobs) + ")"
    row = catalog.read_one(
        "SELECT 1 FROM jobs WHERE task_type=? AND processor_id=? AND "
        + runnable + scope + " LIMIT 1",
        (contract.OCR_TASK_TYPE, processor_id, *(scoped_jobs or ())),
    )
    return row is not None


def _job_ids_for_assets(catalog: Catalog, asset_ids: Sequence[int]) -> tuple[int, ...]:
    """Limit a scoped build drain to selected assets while allowing same-asset CPU fallback."""
    if not asset_ids:
        return ()
    asset_slots = ",".join("?" for _ in asset_ids)
    processor_slots = ",".join("?" for _ in contract.OCR_ACTIVE_PROCESSOR_IDS)
    rows = catalog.read_all(
        "SELECT job_id FROM jobs WHERE subject_type='asset' AND subject_id IN ("
        "SELECT CAST(asset_id AS TEXT) FROM assets WHERE asset_id IN (" + asset_slots + ")) "
        "AND task_type=? AND processor_id IN (" + processor_slots + ") "
        "AND state IN ('QUEUED','RETRY_WAIT','BLOCKED','RUNNING') ORDER BY job_id",
        (*tuple(int(value) for value in asset_ids), contract.OCR_TASK_TYPE,
         *contract.OCR_ACTIVE_PROCESSOR_IDS),
    )
    return tuple(int(row["job_id"]) for row in rows)


def catch_up_projection(catalog: Catalog) -> dict[str, int | str]:
    """Drain the durable reprojection queue in bounded batches (P2-04's own entry point)."""
    return search_documents.catch_up(catalog)
