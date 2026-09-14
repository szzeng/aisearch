"""Orchestration of the face processor: warn, enqueue, run, publish, group, report.

The face processor is an ordinary P2 scheduler job: this module only *feeds* it, reads back
what happened, then drains the two derived steps (the independent candidate index and the
conservative association). It creates no thread and no daemon - one call does one bounded
piece of work and returns, which is exactly the "no resident scheduler thread" boundary of
this build.

``force`` re-detection reuses the same five-field logical job identity (ADR-0003 §8): a
finished job is rewound to QUEUED with a fresh attempt budget rather than becoming a second
row. A job that is RUNNING under a live lease is never touched.

The device story deserves one paragraph, because it is the ADR-0001/ADR-0003 boundary in
practice. Before the first claim this module *probes* the runtime: it tries to open the
pinned graphs on the resolved device and records what really happened as the capability
snapshot, then records qualification evidence only for the devices that really worked. A
GPU that cannot be opened is therefore a recorded capability fact (``unhealthy``), not a
silent CPU run: an explicit ``--device gpu`` blocks, and the default ``auto`` resolves to CPU
under the documented ``CPU_ALLOWED`` policy (which is why the status projection reports the
degradation instead of hiding it).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import CatalogError
from photo_search.face import contract, weights
from photo_search.face.engine import FaceEngineCache
from photo_search.face.index import FaceIndexCoordinator, reconcile
from photo_search.face.processor import face_executor, face_processor_spec
from photo_search.jobs import queue as jobs
from photo_search.runtime.device import CPU_PROVIDER, CUDA_PROVIDER
from photo_search.scheduler import engine as scheduler_engine
from photo_search.scheduler.contracts import RuntimeCapabilities

#: States a forced re-detection may rewind: everything except a live execution.
REWINDABLE_STATES = ("SUCCEEDED", "FAILED", "CANCELLED", "BLOCKED", "RETRY_WAIT")

_TARGET_SQL = (
    "SELECT DISTINCT a.asset_id AS asset_id, a.sha256 AS sha256 "
    "FROM assets AS a "
    "JOIN file_locations AS f ON f.asset_id = a.asset_id AND f.availability = 'PRESENT' "
    "{source_filter}"
    "ORDER BY a.asset_id"
)


def face_targets(
    catalog: Catalog,
    *,
    asset_ids: Sequence[int] | None = None,
    source_id: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Every asset that currently has a readable original - the face work set."""
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
    device: str = "auto",
) -> dict[str, int]:
    """Queue (or force-requeue) one face job per target, idempotently."""
    requested = str(device)
    if requested not in ("auto", "cpu", "gpu"):
        raise CatalogError(
            "USAGE",
            "the requested device must be auto, cpu or gpu",
            details={"requested_device": requested},
        )
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
                task_type=contract.FACE_TASK_TYPE,
                processor_id=contract.FACE_PROCESSOR_ID,
                now_ms=int(now_ms),
                requested_device=requested,
            )
            queued += 1
    return {"targets": len(targets), "queued": queued, "rewound": rewound, "live": live}


def _is_live(connection: sqlite3.Connection, *, asset_id: int, revision: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM jobs WHERE subject_type = 'asset' AND subject_id = ? "
        "AND input_revision = ? AND task_type = ? AND processor_id = ? AND state = 'RUNNING' "
        "AND lease_expires_at IS NOT NULL",
        (str(int(asset_id)), str(revision), contract.FACE_TASK_TYPE, contract.FACE_PROCESSOR_ID),
    ).fetchone()
    return row is not None


def _rewind(
    connection: sqlite3.Connection, *, asset_id: int, revision: str, now_ms: int
) -> bool:
    """Reuse the same logical job for a forced re-detection (never a second row)."""
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
            contract.FACE_TASK_TYPE,
            contract.FACE_PROCESSOR_ID,
            *REWINDABLE_STATES,
        ),
    )
    return bool(cursor.rowcount)


def job_counts(catalog: Catalog, *, task_type: str = contract.FACE_TASK_TYPE) -> dict[str, int]:
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
        count
        for state, count in counts.items()
        if state in ("QUEUED", "RUNNING", "RETRY_WAIT")
    )


def build_scheduler(catalog: Catalog, spec: Any) -> scheduler_engine.Scheduler:
    return scheduler_engine.Scheduler(catalog, [spec], lease_seconds=catalog.lease_seconds)


def seal_snapshot(
    probe: dict[str, Any], *, runtime_package_id: str, now_ms: int
) -> RuntimeCapabilities:
    """Turn the observed device probe into one sealed capability snapshot.

    An installed-but-broken provider is reported as ``unavailable``/``unhealthy`` with the
    reason it failed, and a provider that was deliberately not probed (an explicit CPU run
    never opens the GPU driver) stays ``available`` with health ``unknown`` - it is listed,
    but this process has no evidence about it.
    """
    capabilities = probe["runtime"]
    entries: list[dict[str, Any]] = []
    if bool(probe["cuda_listed"]):
        gpu = probe["gpu"]
        checked = bool(gpu.get("checked"))
        entries.append(
            {
                "name": CUDA_PROVIDER,
                "device": "gpu",
                "precisions": ["FP32"],
                "available": True if not checked else bool(gpu["usable"]),
                "health": (
                    "healthy" if gpu["usable"] else ("unhealthy" if checked else "unknown")
                ),
                "health_checked_at": int(now_ms),
                "evidence_id": None,
            }
        )
    entries.append(
        {
            "name": CPU_PROVIDER,
            "device": "cpu",
            "precisions": ["FP32"],
            "available": bool(probe["cpu"]["usable"]),
            "health": "healthy" if probe["cpu"]["usable"] else "unhealthy",
            "health_checked_at": int(now_ms),
            "evidence_id": None,
        }
    )
    if probe["cpu"]["usable"] and (
        probe["gpu"]["usable"] or not bool(probe["cuda_listed"])
    ):
        health = "healthy"
    elif probe["cpu"]["usable"]:
        health = "degraded"
    else:
        health = "unhealthy"
    facts = {
        "contract_version": contract.CONTRACT_VERSION,
        "capability_revision": "",
        "runtime_package_id": str(runtime_package_id),
        "runtime_backend": "onnxruntime",
        "runtime_package_name": "onnxruntime-gpu",
        "runtime_package_version": str(capabilities.runtime_version),
        "python_version": str(capabilities.python),
        "platform": str(capabilities.platform),
        "driver_version": None,
        "providers": entries,
        "health_status": health,
        "health_checked_at": int(now_ms),
        "measured_at": int(now_ms),
        "evidence_ids": [],
        "failure_code": None,
    }
    return RuntimeCapabilities.seal(facts)


def record_runtime_evidence(
    scheduler: scheduler_engine.Scheduler,
    spec: Any,
    cache: FaceEngineCache,
    *,
    data_root: str | Path | None,
    device: str,
    now_ms: int,
) -> dict[str, Any]:
    """Probe the devices, record the capability snapshot and the real qualifications."""
    probe = cache.probe(data_root, include_gpu=str(device) != "cpu")
    snapshot = seal_snapshot(probe, runtime_package_id=str(spec.runtime_package_id), now_ms=int(now_ms))
    scheduler.record_capability(snapshot, now_ms=int(now_ms))
    qualified: list[str] = []
    for name, entry in (("cpu", probe["cpu"]), ("gpu", probe["gpu"])):
        if not entry.get("usable"):
            continue
        scheduler.record_qualification(
            processor_id=str(spec.processor_id),
            spec_revision=str(spec.spec_revision),
            device=str(name),
            precision=str(spec.supported_precisions[0]),
            provider_name=str(entry["provider"]),
            runtime_package_id=str(spec.runtime_package_id),
            now_ms=int(now_ms),
            note=(
                "pinned buffalo_l detection+recognition sessions created for this device"
            ),
        )
        qualified.append(str(name))
    return {
        "capability_revision": str(snapshot.capability_revision),
        "runtime_package_id": str(snapshot.runtime_package_id),
        "providers": probe["reported_providers"],
        "qualified_devices": qualified,
        "cpu": probe["cpu"],
        "gpu": probe["gpu"],
    }


def run_queue(
    catalog: Catalog,
    *,
    data_root: str | Path | None = None,
    device: str = "auto",
    worker_id: str = "face-cli",
    limit: int | None = None,
    engine_cache: FaceEngineCache | None = None,
    cancel: Callable[[], bool] | None = None,
    deadline_ms: int | None = None,
    clock: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Drain the runnable face jobs; bounded, single-threaded, no sleeping."""
    spec = face_processor_spec()
    scheduler = build_scheduler(catalog, spec)
    cache = engine_cache or FaceEngineCache()
    evidence = record_runtime_evidence(
        scheduler,
        spec,
        cache,
        data_root=data_root if data_root is not None else catalog.data_root,
        device=str(device),
        now_ms=catalog.now_ms(),
    )
    executor = face_executor(
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


def catch_up_index(
    catalog: Catalog, *, coordinator: FaceIndexCoordinator | None = None
) -> dict[str, Any]:
    """Publish and confirm every pending face candidate event."""
    index = coordinator or FaceIndexCoordinator(catalog)
    published = index.process_all(now_ms=catalog.now_ms())
    return {"published": published, "index": index.index_state()}


def catch_up_association(
    catalog: Catalog, *, coordinator: FaceIndexCoordinator | None = None, limit: int = 100_000
) -> dict[str, Any]:
    """Group the faces whose facts are already published and confirmed."""
    return reconcile(catalog, coordinator=coordinator, limit=int(limit))


def detect(
    catalog: Catalog,
    *,
    data_root: str | Path | None = None,
    device: str = "auto",
    force: bool = False,
    limit: int | None = None,
    asset_ids: Sequence[int] | None = None,
    engine_cache: FaceEngineCache | None = None,
) -> dict[str, Any]:
    """The whole closed loop: queue, detect, publish candidates, group conservatively."""
    targets = face_targets(catalog, asset_ids=asset_ids, limit=limit)
    queued = enqueue_targets(
        catalog, targets, now_ms=catalog.now_ms(), force=bool(force), device=str(device)
    )
    run = run_queue(
        catalog,
        data_root=data_root,
        device=str(device),
        limit=limit,
        engine_cache=engine_cache,
    )
    index = FaceIndexCoordinator(catalog)
    published = catch_up_index(catalog, coordinator=index)
    association = catch_up_association(catalog, coordinator=index)
    published_again = catch_up_index(catalog, coordinator=index)
    return {
        "enqueue": queued,
        "run": run,
        "index": published,
        "association": association,
        "index_after_association": published_again,
        "jobs": job_counts(catalog),
    }


def weights_report(data_root: str | Path | None = None) -> dict[str, Any]:
    """Verify the pinned files and describe them without leaking a local path."""
    resolved = weights.resolve_and_verify(data_root)
    return {
        "status": "ok",
        "model_fingerprint": resolved.fingerprint,
        "config_hash": resolved.config_hash,
        "space_id": resolved.space_id,
        "files": weights.inspect(resolved),
    }
