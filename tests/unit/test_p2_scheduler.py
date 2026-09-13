"""P2-02: device qualification, leases, pause, backoff and fairness (ADR-0003 §2/§5/§6).

The fake executor is a deterministic non-model CPU processor: it never reads a file, never
produces coordinates and never writes a vector. Its only job is to prove that the scheduler
closed loop (claim -> resolve -> dispatch -> submit -> recover) is real and persistent.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import (
    DEVICE_POLICY_CONFLICT,
    INVALID_JOB_RESULT,
    INVALID_STATE_TRANSITION,
    LEASE_CONFLICT,
    RUNTIME_CAPABILITY_REVISION_CONFLICT,
    RUNTIME_CAPABILITY_UNAVAILABLE,
    RUNTIME_RESOLUTION_MISMATCH,
    SCHEDULER_EXECUTOR_ERROR,
    SCHEDULER_PAUSED,
    STALE_LEASE,
    CatalogError,
)
from photo_search.jobs import queue as jobs
from photo_search.runtime.device import (
    CPU_PROVIDER,
    CUDA_PROVIDER,
)
from photo_search.runtime.device import (
    RuntimeCapabilities as ProbeCapabilities,
)
from photo_search.scheduler import capability as capability_module
from photo_search.scheduler import eligibility, engine, state
from photo_search.scheduler.contracts import (
    CONTRACT_VERSION,
    ProcessorSpec,
    RuntimeCapabilities,
)

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "docs" / "decisions" / "contracts" / "p2-contracts.v1.json"
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
SHA = "a" * 64

PY_PACKAGE = "photo-search-runtime@1"
ORT_PACKAGE = "onnxruntime-gpu@1.30.0"
GPU_PROCESSOR = "clip-image@36e679e6-fp32-v1"
CPU_PROCESSOR = "geo-extract@1"


# --------------------------------------------------------------------------- fixtures
def non_model_spec(**overrides: Any) -> ProcessorSpec:
    envelope: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "spec_revision": "geo-extract-v1",
        "processor_id": CPU_PROCESSOR,
        "processor_kind": "non_model",
        "task_type": "geo_extract",
        "config_hash": SHA,
        "input_type": "asset_original",
        "output_type": "geo_fact.v1",
        "device_policy": "CPU_PREFERRED",
        "runtime_backend": "python",
        "runtime_package_id": PY_PACKAGE,
        "supported_devices": ["cpu"],
        "supported_precisions": ["not_applicable"],
        "max_batch_size": 32,
        "cancellation_supported": True,
        "resource_measurements": [],
    }
    envelope.update(overrides)
    return ProcessorSpec.from_envelope(envelope)


def embedding_spec(
    *, processor_id: str = GPU_PROCESSOR, device_policy: str = "GPU_REQUIRED"
) -> ProcessorSpec:
    envelope: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "spec_revision": "chinese-clip-vit-b-16-fp32-v1",
        "processor_id": processor_id,
        "processor_kind": "embedding_model",
        "task_type": "image_embedding",
        "config_hash": SHA,
        "input_type": "decoded_rgb_image",
        "output_type": "embedding",
        "device_policy": device_policy,
        "runtime_backend": "onnxruntime",
        "runtime_package_id": ORT_PACKAGE,
        "supported_devices": ["cpu", "gpu"],
        "supported_precisions": ["FP32"],
        "max_batch_size": 1,
        "cancellation_supported": True,
        "resource_measurements": [],
        "model_id": "OFA-Sys/chinese-clip-vit-base-patch16",
        "model_revision": "36e679e65c2a2fead755ae21162091293ad37834",
        "weights": [
            {
                "name": "image_tower.fp32.onnx",
                "revision": "36e679e65c2a2fead755ae21162091293ad37834",
                "sha256": SHA,
            }
        ],
        "image_preprocess_version": "chinese-clip-image-224-rgb-v1",
        "query_preprocess_version": "chinese-clip-query-52-v1",
        "tokenizer_version": "bert-vocab-45bbac6b-v1",
        "instruction_template_version": "none-v1",
        "normalization_version": "L2-v1",
        "dimension": 512,
        "metric": "cosine",
        "space_id": "chinese-clip-vit-b-16:c8ac42d3907c57b0",
        "max_pixels": 224 * 224,
        "max_tokens": 52,
    }
    return ProcessorSpec.from_envelope(envelope)


def provider(
    name: str,
    device: str,
    *,
    health: str = "healthy",
    available: bool = True,
    precisions: tuple[str, ...] = ("FP32",),
) -> dict[str, Any]:
    return {
        "name": name,
        "device": device,
        "precisions": list(precisions),
        "available": available,
        "health": health,
        "health_checked_at": 1_000,
        "evidence_id": None,
    }


def python_capability(*, now_ms: int = 1_000, health: str = "healthy") -> RuntimeCapabilities:
    return RuntimeCapabilities.seal(
        {
            "runtime_package_id": PY_PACKAGE,
            "runtime_backend": "python",
            "runtime_package_name": "photo-search-runtime",
            "runtime_package_version": "1",
            "python_version": "3.12.10",
            "platform": "windows-x86_64",
            "driver_version": None,
            "providers": [
                provider("python-stdlib", "cpu", health=health, precisions=("not_applicable",))
            ],
            "health_status": health,
            "health_checked_at": now_ms,
            "measured_at": now_ms,
            "evidence_ids": [],
            "failure_code": None,
        }
    )


def onnx_capability(
    *,
    now_ms: int = 1_000,
    cpu_health: str = "healthy",
    gpu_health: str | None = "healthy",
    cpu_available: bool = True,
    gpu_available: bool = True,
) -> RuntimeCapabilities:
    providers = [provider("CPUExecutionProvider", "cpu", health=cpu_health, available=cpu_available)]
    if gpu_health is not None:
        providers.append(
            provider("CUDAExecutionProvider", "gpu", health=gpu_health, available=gpu_available)
        )
    overall = "healthy" if all(item["health"] == "healthy" for item in providers) else "unhealthy"
    return RuntimeCapabilities.seal(
        {
            "runtime_package_id": ORT_PACKAGE,
            "runtime_backend": "onnxruntime",
            "runtime_package_name": "onnxruntime-gpu",
            "runtime_package_version": "1.30.0",
            "python_version": "3.12.10",
            "platform": "windows-x86_64",
            "driver_version": "616.56",
            "providers": providers,
            "health_status": overall,
            "health_checked_at": now_ms,
            "measured_at": now_ms,
            "evidence_ids": [],
            "failure_code": None,
        }
    )


@pytest.fixture()
def catalog(tmp_path: Path) -> Catalog:
    instance = Catalog.open(tmp_path / "data" / "catalog" / "catalog.db", lease_seconds=60)
    try:
        yield instance
    finally:
        instance.close()


def enqueue(
    catalog: Catalog,
    *,
    processor_id: str = CPU_PROCESSOR,
    task_type: str = "geo_extract",
    subject_id: str = "1",
    priority: int = 0,
    requested_device: str = "auto",
    now_ms: int = 1_000,
    max_attempts: int = 3,
) -> int:
    with catalog.write() as connection:
        return jobs.enqueue(
            connection,
            subject_type="asset",
            subject_id=subject_id,
            input_revision="rev-1",
            task_type=task_type,
            processor_id=processor_id,
            now_ms=now_ms,
            priority=priority,
            max_attempts=max_attempts,
            requested_device=requested_device,
        )


def qualify(
    scheduler: engine.Scheduler,
    spec: ProcessorSpec,
    *,
    device: str = "cpu",
    provider_name: str = "python-stdlib",
    precision: str | None = None,
    package: str | None = None,
    now_ms: int = 1_000,
) -> None:
    scheduler.record_qualification(
        processor_id=spec.processor_id,
        spec_revision=spec.spec_revision,
        device=device,
        precision=precision or spec.supported_precisions[0],
        provider_name=provider_name,
        runtime_package_id=package or spec.runtime_package_id,
        now_ms=now_ms,
    )


def executor(
    *,
    result_state: str = "SUCCEEDED",
    artifacts: list[dict[str, Any]] | None = None,
    device: str | None = None,
    provider_name: str | None = None,
    precision: str | None = None,
    error: dict[str, Any] | None = None,
    progress: float | None = None,
    calls: list[dict[str, Any]] | None = None,
):
    """A deterministic non-model CPU executor; it never touches a real file."""

    def run(request: dict[str, Any]) -> dict[str, Any]:
        if calls is not None:
            calls.append(request)
        succeeded = result_state == "SUCCEEDED"
        return {
            "contract_version": CONTRACT_VERSION,
            "job_id": request["job_id"],
            "lease_token": request["lease_token"],
            "input_revision": request["input_revision"],
            "task_type": request["task_type"],
            "processor_id": request["processor_id"],
            "state": result_state,
            "resolved_device": device or request["resolved_device"],
            "runtime_provider": provider_name or request["runtime_provider"],
            "precision": precision or request["precision"],
            "progress": (1 if succeeded else 0.0) if progress is None else progress,
            "artifacts": list(artifacts or []),
            "error": None if succeeded else (error or _error_envelope(result_state)),
            "completed_at": 2_000,
        }

    return run


def _error_envelope(result_state: str) -> dict[str, Any]:
    return {
        "code": "SOURCE_FILE_MISSING" if result_state == "RETRY_WAIT" else "DECODE_FAILED",
        "message": "a deterministic fake failure",
        "retryable": result_state == "RETRY_WAIT",
        "blocking_reason": "RUNTIME_CAPABILITY_UNAVAILABLE" if result_state == "BLOCKED" else None,
    }


def structured_artifact() -> dict[str, Any]:
    return {
        "artifact_id": "geo-fact:asset-1:geo-extract@1",
        "artifact_type": "structured",
        "artifact_revision": "geo-fact.v1",
        "media_type": "application/json",
        "checksum_sha256": SHA,
        "byte_size": 2,
        "storage_uri": None,
        "payload": {"state": "ABSENT"},
        "space_id": None,
        "dimension": None,
    }


# --------------------------------------------------------------------- eligibility
def test_gpu_required_blocks_when_no_qualified_gpu_is_available() -> None:
    spec = embedding_spec()
    gpu_evidence = [
        capability_module.QualificationEvidence(
            evidence_id="e1",
            processor_id=spec.processor_id,
            spec_revision=spec.spec_revision,
            device="gpu",
            precision="FP32",
            provider_name="CUDAExecutionProvider",
            runtime_package_id=ORT_PACKAGE,
            verified_at=1,
        )
    ]
    healthy = eligibility.resolve(
        spec, requested_device="auto", capability=onnx_capability(), evidence=gpu_evidence
    )
    assert healthy.eligible and healthy.device == "gpu"
    assert healthy.provider == "CUDAExecutionProvider" and healthy.precision == "FP32"

    cases = {
        "no CUDA provider at all": (onnx_capability(gpu_health=None), gpu_evidence),
        "unhealthy CUDA provider": (onnx_capability(gpu_health="unhealthy"), gpu_evidence),
        "unavailable CUDA provider": (onnx_capability(gpu_available=False), gpu_evidence),
        "healthy CUDA without evidence": (onnx_capability(), []),
    }
    for label, (capability, evidence) in cases.items():
        decision = eligibility.resolve(
            spec, requested_device="auto", capability=capability, evidence=evidence
        )
        assert not decision.eligible, label
        assert decision.reason_code == RUNTIME_CAPABILITY_UNAVAILABLE, label

    explicit = eligibility.resolve(
        spec, requested_device="gpu", capability=onnx_capability(gpu_health=None), evidence=gpu_evidence
    )
    assert not explicit.eligible and explicit.reason_code == RUNTIME_CAPABILITY_UNAVAILABLE


def test_explicit_cpu_on_gpu_required_is_a_policy_conflict() -> None:
    decision = eligibility.resolve(
        embedding_spec(),
        requested_device="cpu",
        capability=onnx_capability(),
        evidence=[],
    )
    assert not decision.eligible
    assert decision.reason_code == DEVICE_POLICY_CONFLICT


def test_cpu_allowed_auto_prefers_gpu_and_never_silently_falls_back() -> None:
    spec = embedding_spec(processor_id="clip-cpu-allowed@1", device_policy="CPU_ALLOWED")
    evidence = [
        capability_module.QualificationEvidence(
            evidence_id=f"{device}-evidence",
            processor_id=spec.processor_id,
            spec_revision=spec.spec_revision,
            device=device,
            precision="FP32",
            provider_name=provider_name,
            runtime_package_id=ORT_PACKAGE,
            verified_at=1,
        )
        for device, provider_name in (
            ("cpu", "CPUExecutionProvider"),
            ("gpu", "CUDAExecutionProvider"),
        )
    ]
    auto = eligibility.resolve(
        spec, requested_device="auto", capability=onnx_capability(), evidence=evidence
    )
    assert auto.eligible and auto.device == "gpu"

    degraded = eligibility.resolve(
        spec,
        requested_device="auto",
        capability=onnx_capability(gpu_health="unhealthy"),
        evidence=evidence,
    )
    assert degraded.eligible and degraded.device == "cpu"
    assert "python" not in (degraded.provider or "")

    explicit = eligibility.resolve(
        spec,
        requested_device="gpu",
        capability=onnx_capability(gpu_health="unhealthy"),
        evidence=evidence,
    )
    assert not explicit.eligible
    assert explicit.reason_code == RUNTIME_CAPABILITY_UNAVAILABLE


def test_cpu_preferred_needs_gpu_evidence_for_an_explicit_gpu_request() -> None:
    spec = non_model_spec()
    auto = eligibility.resolve(
        spec,
        requested_device="auto",
        capability=python_capability(),
        evidence=[
            capability_module.QualificationEvidence(
                "cpu-offline",
                spec.processor_id,
                spec.spec_revision,
                "cpu",
                "not_applicable",
                "python-stdlib",
                PY_PACKAGE,
                1,
            )
        ],
    )
    assert auto.eligible and auto.device == "cpu"

    explicit = eligibility.resolve(
        spec,
        requested_device="gpu",
        capability=onnx_capability(),
        evidence=[],
    )
    assert not explicit.eligible
    assert explicit.reason_code == RUNTIME_CAPABILITY_UNAVAILABLE


def test_a_healthy_provider_or_another_processors_evidence_is_not_enough() -> None:
    spec = non_model_spec()
    healthy_without_evidence = eligibility.resolve(
        spec, requested_device="auto", capability=python_capability(), evidence=[]
    )
    assert not healthy_without_evidence.eligible

    other_processor = capability_module.QualificationEvidence(
        "other",
        "someone-else@1",
        spec.spec_revision,
        "cpu",
        "not_applicable",
        "python-stdlib",
        PY_PACKAGE,
        1,
    )
    foreign = eligibility.resolve(
        spec, requested_device="auto", capability=python_capability(), evidence=[other_processor]
    )
    assert not foreign.eligible

    stale_spec = capability_module.QualificationEvidence(
        "stale",
        spec.processor_id,
        "geo-extract-v0",
        "cpu",
        "not_applicable",
        "python-stdlib",
        PY_PACKAGE,
        1,
    )
    assert not eligibility.resolve(
        spec, requested_device="auto", capability=python_capability(), evidence=[stale_spec]
    ).eligible


def test_runtime_package_mismatch_blocks_the_dispatch() -> None:
    facts = {
        key: value
        for key, value in python_capability().envelope.items()
        if key != "capability_revision"
    }
    other_package = RuntimeCapabilities.seal(
        {**facts, "runtime_package_id": "photo-search-runtime@2"}
    )
    decision = eligibility.resolve(
        non_model_spec(), requested_device="auto", capability=other_package, evidence=[]
    )
    assert not decision.eligible
    assert "does not match" in decision.detail


# ------------------------------------------------------------------ claim & block
def test_blocked_capability_does_not_consume_a_retry_attempt(catalog: Catalog) -> None:
    spec = non_model_spec()
    scheduler = engine.Scheduler(catalog, [spec])
    job_id = enqueue(catalog, max_attempts=1)

    assert scheduler.claim_next(worker_id="w1", now_ms=1_100) is None
    job = catalog.read_one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    assert job["state"] == jobs.STATE_BLOCKED
    assert job["blocking_reason"] == RUNTIME_CAPABILITY_UNAVAILABLE
    assert job["attempts"] == 0
    assert job["lease_token"] is None and job["lease_expires_at"] is None
    assert job["resolved_device"] is None and job["runtime_provider"] is None

    # Even with zero remaining budget it stays BLOCKED and is never failed by the ceiling.
    assert scheduler.expire_leases(now_ms=999_999) == []
    assert catalog.read_one("SELECT state FROM jobs WHERE job_id = ?", (job_id,))[0] == "BLOCKED"


def test_an_unhealthy_gpu_does_not_stop_a_cpu_preferred_job(catalog: Catalog) -> None:
    gpu_spec = embedding_spec()
    cpu_spec = non_model_spec()
    scheduler = engine.Scheduler(catalog, [gpu_spec, cpu_spec])
    scheduler.record_capability(onnx_capability(gpu_health="unhealthy"), now_ms=1_000)
    scheduler.record_capability(python_capability(), now_ms=1_000)
    qualify(scheduler, cpu_spec)
    qualify(
        scheduler,
        gpu_spec,
        device="gpu",
        provider_name="CUDAExecutionProvider",
        precision="FP32",
    )

    gpu_job = enqueue(catalog, processor_id=GPU_PROCESSOR, task_type="image_embedding", subject_id="a")
    cpu_job = enqueue(catalog, subject_id="b")
    with catalog.write() as connection:
        connection.execute("UPDATE jobs SET priority = 100 WHERE job_id = ?", (gpu_job,))

    assert scheduler.run_until_idle(executor(), worker_id="w1", now_ms=1_100) == 1
    rows = {
        int(row["job_id"]): str(row["state"])
        for row in catalog.read_all("SELECT job_id, state FROM jobs")
    }
    assert rows[gpu_job] == jobs.STATE_BLOCKED
    assert rows[cpu_job] == jobs.STATE_SUCCEEDED
    assert scheduler.counts() == {"BLOCKED": 1, "SUCCEEDED": 1}


def test_a_new_healthy_revision_recovers_only_the_qualified_jobs(catalog: Catalog) -> None:
    gpu_spec = embedding_spec()
    cpu_spec = non_model_spec()
    scheduler = engine.Scheduler(catalog, [gpu_spec, cpu_spec])
    scheduler.record_capability(onnx_capability(gpu_health=None), now_ms=1_000)
    scheduler.record_capability(python_capability(), now_ms=1_000)
    qualify(
        scheduler,
        gpu_spec,
        device="gpu",
        provider_name="CUDAExecutionProvider",
        precision="FP32",
    )
    qualify(scheduler, cpu_spec)
    gpu_job = enqueue(catalog, processor_id=GPU_PROCESSOR, task_type="image_embedding")
    enqueue(catalog, subject_id="cpu")
    assert scheduler.run_until_idle(executor(), worker_id="w1", now_ms=1_100) == 1
    assert scheduler.counts() == {"BLOCKED": 1, "SUCCEEDED": 1}

    # GPU comes back: a brand new capability revision, then re-qualification.
    recovered = scheduler.record_capability(onnx_capability(now_ms=2_000), now_ms=2_000)
    assert recovered.health_status == "healthy"
    assert scheduler.recover_blocked(now_ms=2_100) == [gpu_job]
    assert scheduler.run_until_idle(executor(), worker_id="w1", now_ms=2_200) == 1
    assert scheduler.counts() == {"SUCCEEDED": 2}
    assert catalog.read_one("SELECT blocking_reason FROM jobs WHERE job_id = ?", (gpu_job,))[0] is None

    # A DEVICE_POLICY_CONFLICT is a permanent configuration refusal, never capability recoverable.
    conflict_job = enqueue(
        catalog,
        processor_id=GPU_PROCESSOR,
        task_type="image_embedding",
        subject_id="conflict",
        requested_device="cpu",
    )
    assert scheduler.run_until_idle(executor(), worker_id="w1", now_ms=2_300) == 0
    assert (
        catalog.read_one("SELECT blocking_reason FROM jobs WHERE job_id = ?", (conflict_job,))[0]
        == DEVICE_POLICY_CONFLICT
    )
    assert scheduler.recover_blocked(now_ms=2_400) == []


# -------------------------------------------------------------------- retry/backoff
def test_retry_backoff_is_exponential_capped_and_clock_driven(catalog: Catalog) -> None:
    spec = non_model_spec()
    scheduler = engine.Scheduler(
        catalog,
        [spec],
        retry_base_ms=1_000,
        retry_multiplier=2,
        retry_max_ms=4_000,
    )
    scheduler.record_capability(python_capability(), now_ms=1_000)
    qualify(scheduler, spec)
    job_id = enqueue(catalog, max_attempts=6)

    assert scheduler.run_once(executor(result_state="RETRY_WAIT"), worker_id="w1", now_ms=10_000)
    row = catalog.read_one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    assert row["state"] == jobs.STATE_RETRY_WAIT
    assert row["attempts"] == 1
    assert row["run_after"] == 11_000  # base * multiplier ** 0

    # Backoff is a persisted deadline, so an early scheduler simply finds nothing runnable.
    assert scheduler.claim_next(worker_id="w1", now_ms=10_999) is None
    expected = {1: 11_000, 2: 13_000, 3: 17_000, 4: 21_000}
    for attempt in (1, 2, 3, 4):
        deadline = expected[attempt]
        request = scheduler.claim_next(worker_id="w1", now_ms=deadline)
        assert request is not None and request.job_id == job_id
        with catalog.write() as connection:
            jobs.set_progress(
                connection,
                job_id=job_id,
                lease_token=request.lease_token,
                now_ms=deadline,
                progress=0.5,
            )
        finished = scheduler.submit(
            engine.JobResult.from_envelope(
                executor(result_state="RETRY_WAIT", progress=0.0)(request.to_envelope())
            ),
            now_ms=deadline,
        )
        # A retry envelope reporting 0.0 never erases the progress already reached.
        assert finished.progress == 0.5
        if attempt < 4:
            assert finished.state == jobs.STATE_RETRY_WAIT
            assert finished.attempts == attempt + 1
            assert finished.run_after == expected[attempt + 1]
        else:
            # base * 2 ** 3 = 8000 is capped at 4000, so the delay never grows without bound.
            assert finished.run_after == 25_000
    assert (
        catalog.read_one("SELECT last_error_code FROM jobs WHERE job_id = ?", (job_id,))[0]
        == "SOURCE_FILE_MISSING"
    )


def test_backoff_helper_caps_and_is_monotone() -> None:
    delays = [
        state.backoff_delay_ms(attempts, base_ms=100, multiplier=3, max_ms=1_000)
        for attempts in range(1, 6)
    ]
    assert delays == [100, 300, 900, 1_000, 1_000]


def test_retry_ceiling_still_terminates_the_job(catalog: Catalog) -> None:
    spec = non_model_spec()
    scheduler = engine.Scheduler(catalog, [spec], retry_base_ms=1, retry_multiplier=1, retry_max_ms=1)
    scheduler.record_capability(python_capability(), now_ms=1_000)
    qualify(scheduler, spec)
    job_id = enqueue(catalog, max_attempts=2)
    step = executor(result_state="RETRY_WAIT")
    assert scheduler.run_once(step, worker_id="w1", now_ms=2_000) is True
    assert scheduler.run_once(step, worker_id="w1", now_ms=3_000) is True
    row = catalog.read_one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    assert row["state"] == jobs.STATE_FAILED
    assert row["attempts"] == 2
    assert row["lease_token"] is None


# ------------------------------------------------------------------------- pause
def test_pause_blocks_new_claims_but_lets_a_live_lease_finish(catalog: Catalog) -> None:
    spec = non_model_spec()
    scheduler = engine.Scheduler(catalog, [spec])
    scheduler.record_capability(python_capability(), now_ms=1_000)
    qualify(scheduler, spec)
    first = enqueue(catalog, subject_id="a")
    second = enqueue(catalog, subject_id="b")

    request = scheduler.claim_next(worker_id="w1", now_ms=1_100)
    assert request is not None and request.job_id == first
    scheduler.pause(now_ms=1_200)

    with pytest.raises(CatalogError) as paused:
        scheduler.claim_next(worker_id="w1", now_ms=1_300)
    assert paused.value.code == SCHEDULER_PAUSED
    assert scheduler.run_once(executor(), worker_id="w1", now_ms=1_300) is False

    # The running execution may still heartbeat and commit while paused.
    scheduler.heartbeat(
        job_id=first, lease_token=request.lease_token, worker_id="w1", now_ms=1_400
    )
    assert scheduler.submit(
        engine.JobResult.from_envelope(executor()(request.to_envelope())), now_ms=1_500
    ).state == jobs.STATE_SUCCEEDED

    assert catalog.read_one("SELECT state FROM jobs WHERE job_id = ?", (second,))[0] == "QUEUED"
    scheduler.resume(now_ms=1_600)
    assert scheduler.run_until_idle(executor(), worker_id="w1", now_ms=1_700) == 1
    assert scheduler.counts() == {"SUCCEEDED": 2}


def test_pause_and_resume_are_idempotent_and_persist_across_a_restart(catalog: Catalog) -> None:
    spec = non_model_spec()
    scheduler = engine.Scheduler(catalog, [spec])
    first = scheduler.pause(now_ms=1_000)
    assert first["state"] == "paused" and first["changed"] is True
    second = scheduler.pause(now_ms=1_100)
    assert second["changed"] is False and second["revision"] == first["revision"]

    reopened = Catalog.open(catalog.db_path, lease_seconds=60)
    try:
        restarted = engine.Scheduler(reopened, [spec])
        persisted = restarted.read_state()
        assert persisted.paused is True and persisted.revision == first["revision"]
        with pytest.raises(CatalogError) as paused:
            restarted.claim_next(worker_id="w1", now_ms=1_200)
        assert paused.value.code == SCHEDULER_PAUSED
        assert restarted.resume(now_ms=1_300)["state"] == "running"
        assert restarted.resume(now_ms=1_400)["changed"] is False
    finally:
        reopened.close()


# ------------------------------------------------------------------------ fairness
def test_aging_gives_a_starved_backlog_a_bounded_turn(catalog: Catalog) -> None:
    spec = non_model_spec()
    scheduler = engine.Scheduler(catalog, [spec])
    scheduler.record_capability(python_capability(), now_ms=1_000)
    qualify(scheduler, spec)
    scheduler.configure_aging_window(aging_window_ms=1_000, now_ms=0)
    backlog = enqueue(catalog, subject_id="old", priority=0, now_ms=0)

    for step in range(10):
        now_ms = step * 1_000
        enqueue(catalog, subject_id=f"new-{step}", priority=10, now_ms=now_ms)
        claimed = scheduler.claim_next(worker_id="w1", now_ms=now_ms)
        assert claimed is not None
        assert claimed.job_id != backlog, "the backlog must not win before its window accrues"

    # Exactly ten windows later the backlog's effective priority ties the fresh arrival and
    # the stable tie-break (created_at, then job_id) finally serves it — bounded starvation.
    enqueue(catalog, subject_id="new-10", priority=10, now_ms=10_000)
    final = scheduler.claim_next(worker_id="w1", now_ms=10_000)
    assert final is not None and final.job_id == backlog


def test_equal_priority_is_stable_by_created_at_then_job_id(catalog: Catalog) -> None:
    spec = non_model_spec()
    scheduler = engine.Scheduler(catalog, [spec])
    scheduler.record_capability(python_capability(), now_ms=1_000)
    qualify(scheduler, spec)
    first = enqueue(catalog, subject_id="a", now_ms=5_000)
    second = enqueue(catalog, subject_id="b", now_ms=5_000)
    third = enqueue(catalog, subject_id="c", now_ms=6_000)

    order = []
    for _ in range(3):
        request = scheduler.claim_next(worker_id="w1", now_ms=7_000)
        assert request is not None
        order.append(request.job_id)
    assert order == [first, second, third]


# ------------------------------------------------------------ result qualification
def test_result_mismatch_writes_nothing(catalog: Catalog) -> None:
    spec = non_model_spec()
    scheduler = engine.Scheduler(catalog, [spec])
    scheduler.record_capability(python_capability(), now_ms=1_000)
    qualify(scheduler, spec)
    job_id = enqueue(catalog)
    request = scheduler.claim_next(worker_id="w1", now_ms=1_100)
    assert request is not None
    before = dict(catalog.read_one("SELECT * FROM jobs WHERE job_id = ?", (job_id,)))

    bad = executor(device="gpu", provider_name="CUDAExecutionProvider")
    result = engine.JobResult.from_envelope(bad(request.to_envelope()))
    with pytest.raises(CatalogError) as mismatch:
        scheduler.submit(result, now_ms=1_200)
    assert mismatch.value.code == RUNTIME_RESOLUTION_MISMATCH
    assert dict(catalog.read_one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))) == before


def test_a_late_execution_cannot_commit(catalog: Catalog) -> None:
    spec = non_model_spec()
    scheduler = engine.Scheduler(catalog, [spec])
    scheduler.record_capability(python_capability(), now_ms=1_000)
    qualify(scheduler, spec)
    job_id = enqueue(catalog, max_attempts=3)
    first = scheduler.claim_next(worker_id="w1", now_ms=1_000)
    assert first is not None

    second = scheduler.claim_next(worker_id="w2", now_ms=1_000 + 60_000 + 1)
    assert second is not None and second.lease_token != first.lease_token
    assert catalog.read_one("SELECT attempts FROM jobs WHERE job_id = ?", (job_id,))[0] == 1

    stale = engine.JobResult.from_envelope(executor()(first.to_envelope()))
    with pytest.raises(CatalogError) as refused:
        scheduler.submit(stale, now_ms=1_000 + 60_000 + 2)
    assert refused.value.code == STALE_LEASE
    with pytest.raises(CatalogError) as heartbeat:
        scheduler.heartbeat(
            job_id=job_id, lease_token=first.lease_token, worker_id="w1", now_ms=61_500
        )
    assert heartbeat.value.code == STALE_LEASE

    assert scheduler.submit(
        engine.JobResult.from_envelope(executor()(second.to_envelope())), now_ms=62_000
    ).state == jobs.STATE_SUCCEEDED


def test_an_expired_lease_takeover_respects_the_attempt_ceiling(catalog: Catalog) -> None:
    spec = non_model_spec()
    scheduler = engine.Scheduler(catalog, [spec])
    job_id = enqueue(catalog, max_attempts=1)
    # Without any capability fact the job is blocked rather than claimed.
    assert scheduler.claim_next(worker_id="w1", now_ms=1_000) is None
    assert scheduler.counts() == {"BLOCKED": 1}

    scheduler.record_capability(python_capability(), now_ms=1_000)
    qualify(scheduler, spec)
    assert scheduler.recover_blocked(now_ms=1_100) == [job_id]
    claimed = scheduler.claim_next(worker_id="w1", now_ms=1_200)
    assert claimed is not None
    # The takeover itself is the one lost execution, and max_attempts=1 has no budget left.
    assert scheduler.claim_next(worker_id="w2", now_ms=1_200 + 60_000 + 1) is None
    assert scheduler.counts() == {"FAILED": 1}
    failed = catalog.read_one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    assert failed["attempts"] == 1 and failed["last_error_code"] == "LEASE_EXPIRED"


def test_success_with_no_artifacts_is_normal_and_writes_no_vector(catalog: Catalog) -> None:
    spec = non_model_spec()
    scheduler = engine.Scheduler(catalog, [spec])
    scheduler.record_capability(python_capability(), now_ms=1_000)
    qualify(scheduler, spec)
    assert _storage_tables(catalog) == set()
    enqueue(catalog)
    assert scheduler.run_until_idle(executor(artifacts=[]), worker_id="w1", now_ms=1_100) == 1
    assert scheduler.counts() == {"SUCCEEDED": 1}
    # P2-02 must not create an artifact, outbox or vector store ahead of P2-03.
    assert _storage_tables(catalog) == set()


def test_a_structured_non_vector_result_is_accepted(catalog: Catalog) -> None:
    spec = non_model_spec()
    scheduler = engine.Scheduler(catalog, [spec])
    scheduler.record_capability(python_capability(), now_ms=1_000)
    qualify(scheduler, spec)
    job_id = enqueue(catalog)
    assert scheduler.run_once(
        executor(artifacts=[structured_artifact()]), worker_id="w1", now_ms=1_100
    )
    assert catalog.read_one("SELECT state FROM jobs WHERE job_id = ?", (job_id,))[0] == "SUCCEEDED"


def test_a_malformed_result_fails_deterministically(catalog: Catalog) -> None:
    spec = non_model_spec()
    scheduler = engine.Scheduler(catalog, [spec])
    scheduler.record_capability(python_capability(), now_ms=1_000)
    qualify(scheduler, spec)
    job_id = enqueue(catalog, max_attempts=3)

    def broken(request: dict[str, Any]) -> dict[str, Any]:
        return {"contract_version": CONTRACT_VERSION, "job_id": request["job_id"]}

    assert scheduler.run_once(broken, worker_id="w1", now_ms=1_100) is True
    row = catalog.read_one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    assert row["state"] == jobs.STATE_FAILED
    assert row["last_error_code"] == INVALID_JOB_RESULT
    # ADR-0002 §6.2: leaving RUNNING through a failure consumes one attempt, but the job is
    # terminal immediately instead of waiting for the ceiling to be reached.
    assert row["attempts"] == 1
    assert row["run_after"] == 1_000


def test_an_executor_crash_is_a_retryable_execution_failure(catalog: Catalog) -> None:
    spec = non_model_spec()
    scheduler = engine.Scheduler(catalog, [spec], retry_base_ms=10, retry_multiplier=2, retry_max_ms=100)
    scheduler.record_capability(python_capability(), now_ms=1_000)
    qualify(scheduler, spec)
    job_id = enqueue(catalog, max_attempts=2)

    def boom(_: dict[str, Any]) -> dict[str, Any]:
        raise ValueError(r"unexpected C:\Users\someone\Pictures\a.jpg")

    assert scheduler.run_once(boom, worker_id="w1", now_ms=1_100) is True
    row = catalog.read_one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    assert row["state"] == jobs.STATE_RETRY_WAIT
    assert row["last_error_code"] == SCHEDULER_EXECUTOR_ERROR
    assert row["attempts"] == 1
    assert "C:\\" not in str(row["last_error_message"])  # only the exception type is recorded


def test_cancel_is_terminal_and_refuses_a_late_result(catalog: Catalog) -> None:
    spec = non_model_spec()
    scheduler = engine.Scheduler(catalog, [spec])
    scheduler.record_capability(python_capability(), now_ms=1_000)
    qualify(scheduler, spec)
    job_id = enqueue(catalog)
    request = scheduler.claim_next(worker_id="w1", now_ms=1_100)
    assert request is not None
    cancelled = scheduler.cancel(job_id=job_id, now_ms=1_200, reason="user cancelled")
    assert cancelled.state == jobs.STATE_CANCELLED
    assert cancelled.lease_token is None

    late = engine.JobResult.from_envelope(executor()(request.to_envelope()))
    with pytest.raises(CatalogError) as refused:
        scheduler.submit(late, now_ms=1_300)
    assert refused.value.code == LEASE_CONFLICT
    with pytest.raises(CatalogError) as again:
        scheduler.cancel(job_id=job_id, now_ms=1_400)
    assert again.value.code == INVALID_STATE_TRANSITION


def test_cancel_works_from_queued_and_blocked(catalog: Catalog) -> None:
    unqualified = non_model_spec(processor_id="geo-extract@2", task_type="place_resolve")
    # Only the unqualified processor is registered, so the other job stays untouched in
    # QUEUED: the scheduler never claims a processor it was not given a spec for.
    scheduler = engine.Scheduler(catalog, [unqualified])
    scheduler.record_capability(python_capability(), now_ms=1_000)
    queued = enqueue(catalog, subject_id="a")
    blocked = enqueue(
        catalog, subject_id="b", processor_id=unqualified.processor_id, task_type="place_resolve"
    )
    assert scheduler.claim_next(worker_id="w1", now_ms=1_100) is None
    assert catalog.read_one("SELECT state FROM jobs WHERE job_id = ?", (queued,))[0] == "QUEUED"
    assert catalog.read_one("SELECT state FROM jobs WHERE job_id = ?", (blocked,))[0] == "BLOCKED"
    assert scheduler.cancel(job_id=blocked, now_ms=1_200).state == jobs.STATE_CANCELLED
    assert scheduler.cancel(job_id=queued, now_ms=1_200).state == jobs.STATE_CANCELLED


def test_a_bad_requested_device_is_refused_at_enqueue(catalog: Catalog) -> None:
    with pytest.raises(CatalogError):
        enqueue(catalog, requested_device="tpu")


# ------------------------------------------------------------------- capability DB
def test_capability_snapshots_are_immutable_and_revision_checked(catalog: Catalog) -> None:
    spec = non_model_spec()
    scheduler = engine.Scheduler(catalog, [spec])
    snapshot = python_capability()
    scheduler.record_capability(snapshot, now_ms=1_000)
    assert scheduler.record_capability(snapshot, now_ms=1_500).capability_revision == (
        snapshot.capability_revision
    )
    assert catalog.read_one("SELECT COUNT(*) FROM runtime_capabilities")[0] == 1

    forged = RuntimeCapabilities.from_envelope(
        {**snapshot.envelope, "capability_revision": "cap-forged"}
    )
    with pytest.raises(CatalogError) as conflict:
        scheduler.record_capability(forged, now_ms=1_600)
    assert conflict.value.code == RUNTIME_CAPABILITY_REVISION_CONFLICT

    # The database itself refuses to rewrite or delete an accepted snapshot.
    with pytest.raises(sqlite3.IntegrityError):
        with catalog.write() as connection:
            connection.execute(
                "UPDATE runtime_capabilities SET health_status = 'unhealthy' "
                "WHERE capability_revision = ?",
                (snapshot.capability_revision,),
            )
    with pytest.raises(sqlite3.IntegrityError):
        with catalog.write() as connection:
            connection.execute("DELETE FROM runtime_capabilities")
    assert catalog.read_one("SELECT health_status FROM runtime_capabilities")[0] == "healthy"


def test_the_job_request_and_result_match_the_frozen_schema(catalog: Catalog) -> None:
    spec = non_model_spec()
    scheduler = engine.Scheduler(catalog, [spec])
    scheduler.record_capability(python_capability(), now_ms=1_000)
    qualify(scheduler, spec)
    enqueue(catalog)
    request = scheduler.claim_next(worker_id="w1", now_ms=1_100)
    assert request is not None
    envelope = request.to_envelope()
    assert set(envelope) == set(CONTRACT["$defs"]["JobRequest"]["required"])
    assert set(envelope["input_snapshot"]) == set(
        CONTRACT["$defs"]["InputSnapshot"]["required"]
    )
    result = executor()(envelope)
    assert set(result) == set(CONTRACT["$defs"]["JobResult"]["required"])
    assert set(CONTRACT["$defs"]["RuntimeCapabilities"]["required"]) == set(
        python_capability().envelope
    )


def test_the_p0_runtime_probe_is_only_a_snapshot_source_not_a_qualification() -> None:
    """The P0 probe API stays usable, but "the provider exists" never qualifies a job."""
    probe = ProbeCapabilities(
        runtime_backend="onnxruntime",
        runtime_package="onnxruntime-gpu",
        runtime_version="1.30.0",
        python="3.12.10",
        platform="windows-x86_64",
        providers=(CUDA_PROVIDER, CPU_PROVIDER),
        cuda_available=True,
        cuda_dll_packages={},
    )
    snapshot = capability_module.snapshot_from_runtime(
        probe,
        now_ms=1_000,
        health="healthy",
        provider_health={CUDA_PROVIDER: "healthy", CPU_PROVIDER: "healthy"},
        driver_version="616.56",
    )
    assert snapshot.runtime_package_id == "onnxruntime-gpu@1.30.0"
    assert snapshot.health_status == "healthy"
    assert [item.device for item in snapshot.providers] == ["gpu", "cpu"]
    assert snapshot.capability_revision == snapshot.expected_revision

    # A fully healthy environment still qualifies nothing without processor evidence.
    spec = embedding_spec()
    decision = eligibility.resolve(
        spec, requested_device="auto", capability=snapshot, evidence=[]
    )
    assert not decision.eligible
    assert decision.reason_code == RUNTIME_CAPABILITY_UNAVAILABLE


def _storage_tables(catalog: Catalog) -> set[str]:
    from photo_search.catalog import schema

    tokens = ("vector", "embedding", "artifact", "outbox", "lance", "index")
    return {
        name
        for name in schema.business_tables(catalog.connection)
        if any(token in name for token in tokens)
    }
