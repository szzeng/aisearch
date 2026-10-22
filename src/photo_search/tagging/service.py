"""Bounded discovery and ordinary scheduler jobs for optional RAM labels."""
from __future__ import annotations

import hashlib
import platform
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import CatalogError
from photo_search.config import runtime_dir
from photo_search.jobs import queue as jobs
from photo_search.scheduler.capability import snapshot_from_runtime
from photo_search.scheduler.contracts import (
    CONTRACT_VERSION,
    JobResult,
    ProcessorSpec,
    canonical_json,
)
from photo_search.scheduler.engine import Scheduler
from photo_search.tagging import store
from photo_search.tagging.model import PREPROCESS, RUNTIME, TaggerModel
from photo_search.tagging.policy import MAX_SCORES, POLICY_ID, sparse_scores
from photo_search.tagging.runtime import RamTagger

BATCH_LIMIT = 128
MAX_INPUT_BYTES = 256 * 1024 * 1024
RUNTIME_PROVIDER = "torch.cuda"
RUNTIME_PACKAGE = "torch@" + RUNTIME["torch"]
LOCAL_INPUT_ERRORS = frozenset({
    "SOURCE_FILE_MISSING", "SOURCE_OFFLINE", "FILE_READ_ERROR", "INPUT_UNSTABLE", "ACCESS_DENIED",
    "DECODE_FAILED", "FORMAT_UNSUPPORTED", "TAGGER_INPUT_TOO_LARGE", "FACE_INPUT_TOO_LARGE",
    "TAGGER_INPUT_INVALID", "ASSET_NOT_FOUND", "STALE_INPUT_REVISION",
})


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def processor_spec(info: TaggerModel) -> ProcessorSpec:
    config = {"model_id": info.model_id, "policy_id": POLICY_ID, "preprocess": PREPROCESS}
    return ProcessorSpec.from_envelope({
        "contract_version": CONTRACT_VERSION, "spec_revision": POLICY_ID,
        "processor_id": store.processor_id_for(info.model_id, POLICY_ID),
        "processor_kind": "structured_model", "task_type": store.TASK_TYPE,
        "config_hash": hashlib.sha256(canonical_json(config).encode("utf-8")).hexdigest(),
        "input_type": "asset_original", "output_type": store.PAYLOAD_SCHEMA,
        "device_policy": "GPU_REQUIRED", "runtime_backend": "pytorch", "runtime_package_id": RUNTIME_PACKAGE,
        "supported_devices": ["gpu"], "supported_precisions": ["FP16"], "max_batch_size": 1,
        "cancellation_supported": False, "resource_measurements": [], "model_id": info.model_id,
        "model_revision": info.model_sha256,
        "weights": [{"name": "ram-plus", "revision": info.model_id, "sha256": info.model_sha256}],
    })


def register_model(catalog: Catalog, info: TaggerModel) -> dict[str, Any]:
    with catalog.write() as connection:
        return store.register_model(connection, model_id=info.model_id, policy_id=POLICY_ID,
            manifest_sha256=info.manifest_sha256, class_count=len(info.classes), max_scores=MAX_SCORES, now_ms=now_ms())


def discover_targets(catalog: Catalog, *, processor_id: str, after_id: int, through_id: int,
                     limit: int = BATCH_LIMIT) -> list[dict[str, Any]]:
    if not 1 <= limit <= BATCH_LIMIT or after_id < 0 or through_id < 0:
        raise ValueError("invalid bounded tag discovery")
    # Primary-key keyset scan; present positions and completed results use asset indexes.
    # No photos are decoded in this short read and no list of the whole library is built.
    rows = catalog.read_all(
        "SELECT a.asset_id,a.sha256 FROM assets a "
        "WHERE a.asset_id>? AND a.asset_id<=? "
        "AND EXISTS(SELECT 1 FROM file_locations f JOIN source_roots s ON s.source_id=f.source_id "
        "WHERE f.asset_id=a.asset_id AND f.availability='PRESENT' AND s.status='ONLINE') "
        "AND NOT EXISTS(SELECT 1 FROM asset_retention_barriers b WHERE b.asset_id=a.asset_id) "
        "AND NOT EXISTS(SELECT 1 FROM machine_tag_results r JOIN machine_tag_models m ON m.model_key=r.model_key "
        "WHERE r.asset_id=a.asset_id AND r.input_revision=a.sha256 AND m.processor_id=?) "
        "ORDER BY a.asset_id LIMIT ?", (after_id, through_id, processor_id, limit))
    return [{"asset_id": int(row["asset_id"]), "input_revision": str(row["sha256"])} for row in rows]


def enqueue_targets(catalog: Catalog, targets: Sequence[dict[str, Any]], *, processor_id: str) -> list[int]:
    if len(targets) > BATCH_LIMIT:
        raise ValueError("tag enqueue batch exceeds128")
    result = []
    with catalog.write() as connection:
        if store.get_model(connection, processor_id) is None:
            raise CatalogError("TAGGER_MODEL_INVALID", "register the tag model before enqueue")
        for target in targets:
            current = connection.execute("SELECT sha256 FROM assets WHERE asset_id=?", (int(target["asset_id"]),)).fetchone()
            if current is None or str(current[0]) != str(target["input_revision"]):
                continue  # A concurrent rescan will be picked up by the next build.
            result.append(jobs.enqueue(connection, subject_type="asset", subject_id=str(target["asset_id"]),
                input_revision=str(target["input_revision"]), task_type=store.TASK_TYPE, processor_id=processor_id,
                now_ms=now_ms(), requested_device="gpu"))
    return result


def _payload(catalog: Catalog, request: dict[str, Any], runtime: RamTagger) -> dict[str, Any]:
    from photo_search.api.errors import ApiError
    from photo_search.api.files import resolve_present_location
    from photo_search.media.decode import load_normalized

    asset_id = int(request["subject_id"])
    expected = str(request["input_revision"])
    row = catalog.read_one("SELECT sha256 FROM assets WHERE asset_id=?", (asset_id,))
    if row is None or str(row[0]) != expected:
        raise CatalogError("STALE_INPUT_REVISION", "tag source revision no longer matches the queued asset")
    try:
        location = resolve_present_location(catalog, asset_id)
    except ApiError as error:
        raise CatalogError(error.code, "registered tag source is unavailable") from error
    if location.size > MAX_INPUT_BYTES:
        raise CatalogError("TAGGER_INPUT_TOO_LARGE", "tag source exceeds the bounded file size")
    temporary_root = runtime_dir(catalog.data_root) / "tagging-input"
    temporary_root.mkdir(parents=True, exist_ok=True)
    # Decode the exact hashed bytes, not a second unbound read of a changing source.
    with tempfile.TemporaryDirectory(prefix="image-", dir=temporary_root) as temporary:
        path = Path(temporary) / ("input" + location.absolute_path.suffix)
        hashed, size = hashlib.sha256(), 0
        try:
            with location.absolute_path.open("rb") as source, path.open("xb") as target:
                while block := source.read(1024 * 1024):
                    size += len(block)
                    if size > MAX_INPUT_BYTES:
                        raise CatalogError("TAGGER_INPUT_TOO_LARGE", "tag source grew beyond its byte limit")
                    target.write(block)
                    hashed.update(block)
        except OSError as error:
            raise CatalogError("FILE_READ_ERROR", "cannot snapshot the registered tag source") from error
        if size != location.size or hashed.hexdigest() != expected:
            raise CatalogError("INPUT_UNSTABLE", "tag source bytes differ from the catalogued revision")
        decoded = load_normalized(path, long_edge=384, jpeg_draft=False, max_pixels=PREPROCESS["max_pixels"])
        try:
            image = decoded.image.convert("RGB")
            try:
                prediction = runtime.predict(image)
            finally:
                image.close()
        finally:
            decoded.image.close()
    if prediction.model_id != runtime.info.model_id:
        raise CatalogError("TAGGER_OUTPUT_INVALID", "tag prediction model identity differs")
    return {"schema": store.PAYLOAD_SCHEMA, "model_id": runtime.info.model_id, "policy_id": POLICY_ID,
            "input_revision": expected, "scores": sparse_scores(prediction)}


def tag_executor(catalog: Catalog, runtime: RamTagger, *, clock: Callable[[], int] = now_ms) -> Callable:
    spec = processor_spec(runtime.info)

    def execute(request: dict[str, Any]) -> dict[str, Any]:
        result = {"contract_version": CONTRACT_VERSION,
            **{key: request[key] for key in ("job_id", "lease_token", "input_revision", "task_type", "processor_id",
                                           "resolved_device", "runtime_provider", "precision")},
            "state": "SUCCEEDED", "progress": 1., "artifacts": [], "error": None, "completed_at": clock()}
        try:
            if (request["processor_id"] != spec.processor_id or request["task_type"] != store.TASK_TYPE
                    or request["subject_type"] != "asset" or request["resolved_device"] != "gpu"
                    or request["runtime_provider"] != RUNTIME_PROVIDER or request["precision"] != "FP16"):
                raise CatalogError("RUNTIME_RESOLUTION_MISMATCH", "tag job must use its qualified CUDA FP16 runtime")
            payload = _payload(catalog, request, runtime)
            raw = canonical_json(payload).encode("utf-8")
            result["artifacts"] = [{"artifact_id": store.artifact_id_for(request["input_revision"], spec.processor_id),
                "artifact_type": "structured", "artifact_revision": store.ARTIFACT_REVISION,
                "media_type": "application/json", "checksum_sha256": hashlib.sha256(raw).hexdigest(),
                "byte_size": len(raw), "storage_uri": None, "payload": payload, "space_id": None, "dimension": None}]
        except Exception as error:  # noqa: BLE001 - publish a bounded, explicit job failure.
            code = error.code if isinstance(error, CatalogError) else "TAGGER_EXECUTION_FAILED"
            blocking = code in {"SOURCE_FILE_MISSING", "SOURCE_OFFLINE", "FILE_READ_ERROR", "INPUT_UNSTABLE", "ACCESS_DENIED"}
            result.update(state="BLOCKED" if blocking else "FAILED", progress=0., artifacts=[],
                error={"code": code, "message": "image tagging did not complete: " + code,
                       "retryable": False, "blocking_reason": code if blocking else None})
        result["completed_at"] = clock()
        return result

    return execute


def build_scheduler(catalog: Catalog, runtime: RamTagger) -> Scheduler:
    """Only a successfully loaded CUDA runtime may qualify this structured processor."""
    spec = processor_spec(runtime.info)
    scheduler = Scheduler(catalog, [spec], lease_seconds=300)
    clock = now_ms()
    facts = SimpleNamespace(providers=(RUNTIME_PROVIDER,), runtime_package_id=RUNTIME_PACKAGE,
        runtime_backend="pytorch", runtime_package="torch", runtime_version=RUNTIME["torch"],
        python=platform.python_version(), platform=platform.platform())
    snapshot = snapshot_from_runtime(facts, now_ms=clock, health="healthy",
        provider_health={RUNTIME_PROVIDER: "healthy"}, provider_precisions={RUNTIME_PROVIDER: ["FP16"]})
    scheduler.record_capability(snapshot, now_ms=clock)
    scheduler.record_qualification(processor_id=spec.processor_id, spec_revision=spec.spec_revision,
        device="gpu", precision="FP16", provider_name=RUNTIME_PROVIDER, runtime_package_id=RUNTIME_PACKAGE,
        now_ms=clock, note="local RAM++ TorchScript package verified and loaded on CUDA0")
    return scheduler


def run_one(scheduler: Scheduler, executor: Callable, *, worker_id: str,
            clock: Callable[[], int] = now_ms) -> jobs.Job | None:
    try:
        request = scheduler.claim_next(worker_id=worker_id, now_ms=clock())
    except CatalogError as error:
        if error.code != "SCHEDULER_PAUSED":
            raise
        return None
    if request is None:
        return None
    result = JobResult.from_envelope(executor(request.to_envelope()))
    # Use the time after decoding/inference, so an expired lease cannot publish.
    return scheduler.submit(result, now_ms=clock())


def retry_waiting(catalog: Catalog, *, processor_id: str) -> int:
    """Explicit continuation retries at most128 current-content failures per commit."""
    with catalog.write() as connection:
        cursor = connection.execute(
            "UPDATE jobs SET state='QUEUED',attempts=0,run_after=?,lease_token=NULL,lease_expires_at=NULL,"
            "worker_id=NULL,last_error_code=NULL,last_error_message=NULL,blocking_reason=NULL,updated_at=? "
            "WHERE job_id IN(SELECT j.job_id FROM jobs j WHERE processor_id=? AND task_type=? "
            "AND state IN ('FAILED','BLOCKED','RETRY_WAIT') "
            "AND EXISTS(SELECT 1 FROM assets a WHERE a.asset_id=CAST(j.subject_id AS INTEGER) "
            "AND a.sha256=j.input_revision) LIMIT ?)",
            (now_ms(), now_ms(), processor_id, store.TASK_TYPE, BATCH_LIMIT))
    return int(cursor.rowcount)


def cancel_stale_waiting(catalog: Catalog, *, processor_id: str) -> int:
    """Cancel one bounded batch of obsolete content jobs before retry/discovery."""
    with catalog.write() as connection:
        rows = connection.execute(
            "SELECT j.job_id FROM jobs j WHERE j.processor_id=? AND j.task_type=? "
            "AND j.state IN ('QUEUED','FAILED','BLOCKED','RETRY_WAIT') "
            "AND NOT EXISTS(SELECT 1 FROM assets a WHERE a.asset_id=CAST(j.subject_id AS INTEGER) "
            "AND a.sha256=j.input_revision) LIMIT ?",
            (processor_id, store.TASK_TYPE, BATCH_LIMIT)).fetchall()
        for row in rows:
            connection.execute(
                "UPDATE jobs SET state='CANCELLED',lease_token=NULL,lease_expires_at=NULL,worker_id=NULL,"
                "last_error_code='STALE_INPUT_REVISION',last_error_message='tag input was superseded',updated_at=? "
                "WHERE job_id=?", (now_ms(), int(row[0])))
    return len(rows)


def recover_cli_leases(catalog: Catalog, *, processor_id: str) -> int:
    """The OS-lease owner may recover departed CLI work; never another worker's lease.

    Call only after the process acquired the exclusive data-root lease. All callers of
    the standalone tagging CLI hold that lease until their runtime has closed.
    """
    clock = now_ms()
    with catalog.write() as connection:
        cursor = connection.execute(
            "UPDATE jobs SET lease_expires_at=?,updated_at=? WHERE job_id IN ("
            "SELECT job_id FROM jobs WHERE processor_id=? AND task_type=? AND state='RUNNING' "
            "AND worker_id='image-tags-cli' AND lease_expires_at>? LIMIT ?)",
            (clock, clock, processor_id, store.TASK_TYPE, clock, BATCH_LIMIT))
    return int(cursor.rowcount)
