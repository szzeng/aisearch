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
import threading
from collections.abc import Callable, Sequence
from functools import wraps
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

BACKGROUND_MODEL_SLOT = threading.RLock()


def _serialize_face_work(function: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Share one inference slot across refreshes, per-photo requests, and batches."""
    @wraps(function)
    def guarded(*args: Any, **kwargs: Any) -> dict[str, Any]:
        with BACKGROUND_MODEL_SLOT:
            return function(*args, **kwargs)

    return guarded

#: States a forced re-detection may rewind: everything except a live execution.
REWINDABLE_STATES = ("SUCCEEDED", "FAILED", "CANCELLED", "BLOCKED", "RETRY_WAIT")

_TARGET_SQL = (
    "SELECT DISTINCT a.asset_id AS asset_id, a.sha256 AS sha256 "
    "FROM assets AS a "
    "JOIN file_locations AS f ON f.asset_id = a.asset_id AND f.availability = 'PRESENT' "
    "WHERE {filters} "
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


def enqueue_targets(
    catalog: Catalog,
    targets: Sequence[dict[str, Any]],
    *,
    now_ms: int,
    force: bool = False,
    device: str = "auto",
    task_type: str = contract.FACE_TASK_TYPE,
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
            if force and _rewind(
                connection, asset_id=asset_id, revision=revision, now_ms=now_ms,
                task_type=str(task_type),
            ):
                rewound += 1
                continue
            if force and _is_live(
                connection, asset_id=asset_id, revision=revision, task_type=str(task_type)
            ):
                live += 1
                continue
            jobs.enqueue(
                connection,
                subject_type="asset",
                subject_id=str(asset_id),
                input_revision=revision,
                task_type=str(task_type),
                processor_id=contract.FACE_PROCESSOR_ID,
                now_ms=int(now_ms),
                requested_device=requested,
            )
            queued += 1
    return {"targets": len(targets), "queued": queued, "rewound": rewound, "live": live}


def enqueue_source_detail(catalog: Catalog, *, asset_id: int, device: str = "auto") -> dict[str, Any]:
    """Queue one opt-in source-detail run, deduplicated by the durable job identity."""
    target = face_targets(catalog, asset_ids=[int(asset_id)], limit=1)
    if not target:
        raise CatalogError("ASSET_NOT_FOUND", "the selected photo has no readable original")
    requested = str(device)
    if requested not in ("auto", "cpu", "gpu"):
        raise CatalogError("USAGE", "the requested device must be auto, cpu or gpu")
    item = target[0]
    now_ms = catalog.now_ms()
    published = catalog.read_one(
        "SELECT 1 FROM artifacts WHERE processor_id = ? AND input_revision = ? "
        "AND artifact_revision = ? AND lifecycle_state = 'PUBLISHED' LIMIT 1",
        (
            contract.FACE_PROCESSOR_ID,
            str(item["input_revision"]),
            contract.FACE_DETAIL_ARTIFACT_REVISION,
        ),
    )
    if published is not None:
        succeeded = catalog.read_one(
            "SELECT job_id FROM jobs WHERE subject_type = 'asset' AND subject_id = ? "
            "AND input_revision = ? AND task_type = ? AND processor_id = ? "
            "AND state = 'SUCCEEDED' ORDER BY job_id DESC LIMIT 1",
            (
                str(item["asset_id"]),
                str(item["input_revision"]),
                contract.FACE_DETAIL_TASK_TYPE,
                contract.FACE_PROCESSOR_ID,
            ),
        )
        return {
            "job_id": None if succeeded is None else int(succeeded["job_id"]),
            "state": "SUCCEEDED",
            "asset_id": int(asset_id),
            "already_available": True,
        }
    with catalog.write() as connection:
        job_id = jobs.enqueue(
            connection,
            subject_type="asset",
            subject_id=str(item["asset_id"]),
            input_revision=str(item["input_revision"]),
            task_type=contract.FACE_DETAIL_TASK_TYPE,
            processor_id=contract.FACE_PROCESSOR_ID,
            now_ms=now_ms,
            requested_device=requested,
        )
        row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise CatalogError("JOB_NOT_FOUND", "the face enhancement task was not stored")
        state_value = str(row["state"])
        if state_value in REWINDABLE_STATES:
            connection.execute(
                "UPDATE jobs SET state = 'QUEUED', attempts = 0, progress = 0.0, "
                "lease_token = NULL, lease_expires_at = NULL, worker_id = NULL, run_after = ?, "
                "last_error_code = NULL, last_error_message = NULL, blocking_reason = NULL, "
                "resolved_device = NULL, runtime_provider = NULL, precision = NULL, "
                "runtime_capability_revision = NULL, dispatch_spec_revision = NULL, updated_at = ? "
                "WHERE job_id = ?",
                (now_ms, now_ms, int(job_id)),
            )
            state_value = "QUEUED"
        return {
            "job_id": int(job_id),
            "state": state_value,
            "asset_id": int(asset_id),
            "already_available": False,
        }


def _is_live(
    connection: sqlite3.Connection, *, asset_id: int, revision: str,
    task_type: str = contract.FACE_TASK_TYPE,
) -> bool:
    row = connection.execute(
        "SELECT 1 FROM jobs WHERE subject_type = 'asset' AND subject_id = ? "
        "AND input_revision = ? AND task_type = ? AND processor_id = ? AND state = 'RUNNING' "
        "AND lease_expires_at IS NOT NULL",
        (str(int(asset_id)), str(revision), str(task_type), contract.FACE_PROCESSOR_ID),
    ).fetchone()
    return row is not None


def _rewind(
    connection: sqlite3.Connection, *, asset_id: int, revision: str, now_ms: int,
    task_type: str = contract.FACE_TASK_TYPE,
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
            str(task_type),
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
        count for state, count in counts.items() if state in ("QUEUED", "RUNNING", "RETRY_WAIT")
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
                "health": ("healthy" if gpu["usable"] else ("unhealthy" if checked else "unknown")),
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
    if probe["cpu"]["usable"] and (probe["gpu"]["usable"] or not bool(probe["cuda_listed"])):
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
    snapshot = seal_snapshot(
        probe, runtime_package_id=str(spec.runtime_package_id), now_ms=int(now_ms)
    )
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
            note=("pinned buffalo_l detection+recognition sessions created for this device"),
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


@_serialize_face_work
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
    source_detail: bool = False,
    automatic_detail: bool = False,
    job_ids: Sequence[int] | None = None,
    claim_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Drain the runnable face jobs; bounded, single-threaded, no sleeping."""
    spec = face_processor_spec(
        source_detail=source_detail, automatic_detail=automatic_detail
    )
    scheduler = build_scheduler(catalog, spec)
    scheduler.job_ids = None if job_ids is None else tuple(int(value) for value in job_ids)
    if (job_ids is not None and not job_ids) or (claim_check is not None and not claim_check()):
        return {"steps": 0, "jobs": job_counts(catalog, task_type=spec.task_type),
                "pending_jobs": pending_job_count(catalog), "reason": "NO_CLAIM_ALLOWED"}
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
        preserve_source_detail=source_detail,
    )
    steps = 0
    while limit is None or steps < max(0, int(limit)):
        if claim_check is not None and not claim_check():
            break
        if cancel is not None and cancel():
            break
        if not scheduler.run_once(executor, worker_id=worker_id, now_ms=catalog.now_ms()):
            break
        steps += 1
    return {
        "steps": steps,
        "jobs": job_counts(
            catalog,
            task_type=(
                contract.FACE_DETAIL_AUTO_TASK_TYPE
                if automatic_detail
                else contract.FACE_DETAIL_TASK_TYPE
                if source_detail
                else contract.FACE_TASK_TYPE
            ),
        ),
        "pending_jobs": pending_job_count(catalog),
        **evidence,
    }


def catch_up_index(
    catalog: Catalog, *, coordinator: FaceIndexCoordinator | None = None, limit: int = 100_000
) -> dict[str, Any]:
    """Publish and confirm every pending face candidate event."""
    index = coordinator or FaceIndexCoordinator(catalog)
    published = index.process_all(now_ms=catalog.now_ms(), limit=int(limit))
    return {"published": published, "index": index.index_state()}


def catch_up_association(
    catalog: Catalog, *, coordinator: FaceIndexCoordinator | None = None,
    limit: int = 100_000, spec_revision: str = contract.FACE_SPEC_REVISION,
) -> dict[str, Any]:
    """Group the faces whose facts are already published and confirmed."""
    return reconcile(
        catalog, coordinator=coordinator, limit=int(limit), spec_revision=str(spec_revision)
    )


def rematch_unassigned(
    catalog: Catalog,
    *,
    limit: int = 100_000,
    after_asset_id: int = 0,
    publication_limit: int = 100_000,
) -> dict[str, Any]:
    """Reconsider stored, unassigned faces without detection or resetting human knowledge.

    Only the explicitly compatible buffalo_l v1/v2 generations are eligible. Old faces
    superseded by a v2 publication (including a zero-face result) are not resurrected.
    The existing durable queue/index/control gates still own publication and assignment.
    """
    legacy = "face-buffalo-l-scrfd-arcface-v1"
    current = contract.FACE_SPEC_REVISION
    limit = max(0, int(limit))
    with catalog.write() as connection:
        targets = connection.execute(
            "SELECT f.asset_id, f.spec_revision, MAX(f.face_revision) AS face_revision "
            "FROM faces AS f JOIN assets AS a ON a.asset_id = f.asset_id "
            "JOIN face_embeddings AS e ON e.face_id = f.face_id "
            "WHERE a.state = 'ACTIVE' AND f.input_revision = a.sha256 AND f.asset_id > ? "
            "AND f.spec_revision IN (?, ?) AND e.processor_id = ? AND e.space_id = ? "
            "AND e.dimension = ? AND e.metric = 'cosine' AND e.normalization = 'L2' "
            "AND NOT EXISTS (SELECT 1 FROM face_person_links AS l "
            " WHERE l.face_id = f.face_id AND l.decision = 'CONFIRMED') "
            "AND NOT EXISTS (SELECT 1 FROM face_assignment_controls AS c "
            " WHERE c.face_id = f.face_id AND c.mode IN ('MANUAL_ASSIGNED', 'MANUAL_UNASSIGNED')) "
            "AND (f.spec_revision = ? OR ("
            " NOT EXISTS (SELECT 1 FROM faces AS n WHERE n.asset_id = f.asset_id "
            "  AND n.spec_revision = ?) AND NOT EXISTS (SELECT 1 FROM artifacts AS ar "
            "  WHERE ar.processor_id = ? AND ar.input_revision = f.input_revision "
            "  AND ar.artifact_revision = ? AND ar.lifecycle_state = 'PUBLISHED'))) "
            "GROUP BY f.asset_id, f.spec_revision ORDER BY f.asset_id LIMIT ?",
            (
                max(0, int(after_asset_id)),
                legacy,
                current,
                contract.FACE_PROCESSOR_ID,
                contract.FACE_SPACE_ID,
                contract.FACE_DIMENSION,
                current,
                current,
                contract.FACE_PROCESSOR_ID,
                contract.FACE_ARTIFACT_REVISION,
                limit + 1 if limit else 0,
            ),
        ).fetchall()
        has_more = len(targets) > limit
        targets = targets[:limit]
        for row in targets:
            connection.execute(
                "INSERT INTO face_association_queue(asset_id, spec_revision, face_revision, "
                "queued_at) VALUES (?, ?, ?, ?) ON CONFLICT(asset_id) DO NOTHING",
                (
                    int(row["asset_id"]),
                    str(row["spec_revision"]),
                    int(row["face_revision"]),
                    catalog.now_ms(),
                ),
            )
    # Candidate publication needs no model files or ONNX session. It also resumes a
    # previous interrupted rematch before computing any new assignments.
    index = FaceIndexCoordinator(catalog)
    published = (
        catch_up_index(catalog, coordinator=index, limit=publication_limit) if limit else None
    )
    results: dict[str, Any] = {}
    remaining = limit
    for revision in (legacy, current):
        if remaining <= 0:
            break
        result = reconcile(
            catalog,
            coordinator=FaceIndexCoordinator(catalog, spec_revision=revision),
            spec_revision=revision,
            limit=remaining,
            asset_ids=[
                int(row["asset_id"]) for row in targets if str(row["spec_revision"]) == revision
            ],
            publication_limit=publication_limit,
        )
        results[revision] = result
        remaining -= int(result["processed"])
    return {
        "policy_revision": contract.ASSOCIATION_POLICY_REVISION,
        "merge_threshold": contract.MERGE_THRESHOLD,
        "selected_assets": len(targets),
        "has_more": has_more,
        "next_after_asset_id": int(targets[-1]["asset_id"]) if has_more and targets else None,
        "index": published,
        "association": results,
        "processed": sum(int(r["processed"]) for r in results.values()),
        "merged": sum(int(r["merged"]) for r in results.values()),
        "new_groups": sum(int(r["new_groups"]) for r in results.values()),
        "suggested": sum(int(r["suggested"]) for r in results.values()),
        "deferred": sum(int(r["deferred"]) for r in results.values()),
    }


def detect(
    catalog: Catalog,
    *,
    data_root: str | Path | None = None,
    device: str = "auto",
    force: bool = False,
    limit: int | None = None,
    asset_ids: Sequence[int] | None = None,
    engine_cache: FaceEngineCache | None = None,
    source_detail: bool = False,
) -> dict[str, Any]:
    """The whole closed loop: queue, detect, publish candidates, group conservatively."""
    targets = face_targets(catalog, asset_ids=asset_ids, limit=limit)
    all_targets = list(targets)
    if source_detail:
        from photo_search.face.detail_policy import restore
        targets = [target for target in targets if not restore(
            catalog, asset_id=int(target["asset_id"]), input_revision=str(target["input_revision"])
        )]
    task_type = contract.FACE_DETAIL_AUTO_TASK_TYPE if source_detail else contract.FACE_TASK_TYPE
    queued = enqueue_targets(
        catalog, targets, now_ms=catalog.now_ms(), force=bool(force), device=str(device),
        task_type=task_type,
    )
    selected_jobs = _job_ids_for_targets(
        catalog, targets, task_type=task_type, processor_id=contract.FACE_PROCESSOR_ID
    )
    run = run_queue(
        catalog,
        data_root=data_root,
        device=str(device),
        limit=limit,
        engine_cache=engine_cache,
        source_detail=bool(source_detail),
        automatic_detail=bool(source_detail),
        job_ids=selected_jobs,
    ) if selected_jobs else {"steps": 0, "jobs": job_counts(catalog, task_type=task_type)}
    spec_revision = (
        contract.FACE_DETAIL_SPEC_REVISION if source_detail else contract.FACE_SPEC_REVISION
    )
    index = FaceIndexCoordinator(catalog, spec_revision=spec_revision)
    published = catch_up_index(catalog, coordinator=index)
    association = reconcile(
        catalog, coordinator=index, spec_revision=spec_revision,
        asset_ids=[int(target["asset_id"]) for target in all_targets],
    )
    published_again = catch_up_index(catalog, coordinator=index)
    return {
        "enqueue": queued,
        "run": run,
        "index": published,
        "association": association,
        "index_after_association": published_again,
        "jobs": job_counts(catalog),
    }


def _job_ids_for_targets(
    catalog: Catalog,
    targets: Sequence[dict[str, Any]],
    *,
    task_type: str,
    processor_id: str,
) -> list[int]:
    """Fetch the exact current job identities for a target batch in bounded SQL calls."""
    target_revisions = {
        (str(int(target["asset_id"])), str(target["input_revision"])) for target in targets
    }
    if not target_revisions:
        return []
    asset_ids = sorted({int(asset_id) for asset_id, _ in target_revisions})
    job_ids: dict[tuple[str, str], int] = {}
    # Stay below SQLite's portable bind parameter ceiling while keeping one query per chunk.
    for start in range(0, len(asset_ids), 800):
        batch = asset_ids[start : start + 800]
        slots = ",".join("?" for _ in batch)
        rows = catalog.read_all(
            "SELECT job_id,subject_id,input_revision FROM jobs WHERE subject_type='asset' "
            f"AND subject_id IN ({slots}) AND task_type=? AND processor_id=? ORDER BY job_id",
            (*tuple(str(asset_id) for asset_id in batch), str(task_type), str(processor_id)),
        )
        for row in rows:
            key = (str(row["subject_id"]), str(row["input_revision"]))
            if key in target_revisions:
                job_ids[key] = int(row["job_id"])
    return [job_ids[key] for key in sorted(target_revisions) if key in job_ids]


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
