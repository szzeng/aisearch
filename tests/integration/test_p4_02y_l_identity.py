"""Focused synthetic D-level coverage for versioned OCR identity and CPU fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from p3_ocr_fixtures import Harness
from PIL import Image

from photo_search.catalog.errors import (
    ARTIFACT_CONFLICT,
    INVALID_STATE_TRANSITION,
    STALE_LEASE,
    CatalogError,
)
from photo_search.ingestion.service import CatalogService
from photo_search.jobs import queue as jobs
from photo_search.ocr import contract
from photo_search.ocr import service as ocr_service
from photo_search.ocr.engine import PinnedOcrEngine, _is_onnx_cuda_failure
from photo_search.ocr.processor import (
    OCR_GPU_EXECUTION_FAILED,
    OCR_GPU_INITIALIZATION_FAILED,
    ocr_executor,
)
from photo_search.refresh.progress import enrichment_counts
from photo_search.scheduler.contracts import JobResult, RuntimeCapabilities
from photo_search.scheduler.projection import ocr_summary


@pytest.fixture()
def harness(tmp_path: Path) -> Any:
    instance = Harness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


class FakeEngine:
    def __init__(
        self, device: str, *, failure: str | None, confidence: float, empty: bool = False
    ) -> None:
        self.device = device
        self.runtime_provider = (
            "CUDAExecutionProvider" if device == "gpu" else "CPUExecutionProvider"
        )
        self.failure = failure
        self.confidence = confidence
        self.empty = empty

    def read(self, _image: Any) -> list[Any]:
        if self.failure is not None:
            if self.failure == "UNKNOWN_PYTHON_EXCEPTION":
                raise RuntimeError("synthetic unknown exception")
            raise CatalogError(self.failure, "synthetic OCR engine fault")
        if self.empty:
            return []
        return [
            [
                [[2.0, 2.0], [100.0, 2.0], [100.0, 24.0], [2.0, 24.0]],
                "合成文字",
                self.confidence,
            ]
        ]


class FakeEngineCache:
    def __init__(
        self,
        *,
        gpu_failure: str | None = None,
        cpu_failure: str | None = None,
        gpu_confidence: float = 0.9,
        cpu_confidence: float = 0.9,
        gpu_empty: bool = False,
        cpu_empty: bool = False,
    ) -> None:
        self.gpu_failure = gpu_failure
        self.cpu_failure = cpu_failure
        self.gpu_confidence = gpu_confidence
        self.cpu_confidence = cpu_confidence
        self.gpu_empty = gpu_empty
        self.cpu_empty = cpu_empty

    def engine(self, _root: Any, *, resolved_device: str, **_kwargs: Any) -> FakeEngine:
        return FakeEngine(
            resolved_device,
            failure=self.gpu_failure if resolved_device == "gpu" else self.cpu_failure,
            confidence=self.gpu_confidence if resolved_device == "gpu" else self.cpu_confidence,
            empty=self.gpu_empty if resolved_device == "gpu" else self.cpu_empty,
        )


def install_runtime_evidence(monkeypatch: Any, *, gpu_qualified: bool = True) -> None:
    def record(scheduler: Any, _spec: Any, _cache: Any, *, now_ms: int, **_kwargs: Any) -> dict[str, Any]:
        providers = [
            {
                "name": "CPUExecutionProvider",
                "device": "cpu",
                "precisions": ["FP32"],
                "available": True,
                "health": "healthy",
                "health_checked_at": int(now_ms),
                "evidence_id": "synthetic-cpu",
            },
            {
                "name": "CUDAExecutionProvider",
                "device": "gpu",
                "precisions": ["FP32"],
                "available": True,
                "health": "healthy",
                "health_checked_at": int(now_ms),
                "evidence_id": "synthetic-cuda",
            },
        ]
        snapshot = RuntimeCapabilities.seal(
            {
                "runtime_package_id": contract.OCR_RUNTIME_PACKAGE_ID,
                "runtime_backend": "onnxruntime",
                "runtime_package_name": "onnxruntime-gpu",
                "runtime_package_version": "1.30.0",
                "python_version": "3.12.10",
                "platform": "Windows",
                "driver_version": "synthetic",
                "providers": providers,
                "health_status": "healthy",
                "health_checked_at": int(now_ms),
                "measured_at": int(now_ms),
                "evidence_ids": ["synthetic-cpu", "synthetic-cuda"],
                "failure_code": None,
            }
        )
        scheduler.record_capability(snapshot, now_ms=int(now_ms))
        for device, profile_id, spec_revision, provider in (
            ("cpu", contract.OCR_CPU_PROCESSOR_ID, contract.OCR_CPU_SPEC_REVISION,
             "CPUExecutionProvider"),
            ("gpu", contract.OCR_GPU_PROCESSOR_ID, contract.OCR_GPU_SPEC_REVISION,
             "CUDAExecutionProvider"),
        ):
            if device == "gpu" and not gpu_qualified:
                continue
            scheduler.record_qualification(
                processor_id=profile_id,
                spec_revision=spec_revision,
                device=device,
                precision="FP32",
                provider_name=provider,
                runtime_package_id=contract.OCR_RUNTIME_PACKAGE_ID,
                now_ms=int(now_ms),
                note="deterministic synthetic capability evidence",
            )
        return {
            "capability_revision": snapshot.capability_revision,
            "runtime_package_id": snapshot.runtime_package_id,
            "qualified_devices": ["cpu"] + (["gpu"] if gpu_qualified else []),
            "cpu_provider": "CPUExecutionProvider",
            "gpu_provider": "CUDAExecutionProvider" if gpu_qualified else None,
            "gpu_probe_error": None,
        }

    monkeypatch.setattr(ocr_service, "record_runtime_evidence", record)


def make_asset(harness: Any, name: str = "synthetic.jpg") -> int:
    Image.new("RGB", (240, 120), "white").save(harness.library / name, "JPEG")
    harness.scan()
    return harness.asset_id(name)


def test_only_explicit_onnx_cuda_errors_qualify_for_device_fallback() -> None:
    onnx_exception = type(
        "RuntimeException",
        (Exception,),
        {"__module__": "onnxruntime.capi.onnxruntime_pybind11_state"},
    )
    onnx_input_error = type(
        "InvalidArgument",
        (Exception,),
        {"__module__": "onnxruntime.capi.onnxruntime_pybind11_state"},
    )
    assert _is_onnx_cuda_failure(onnx_exception("CUDAExecutionProvider failed"))
    assert not _is_onnx_cuda_failure(onnx_exception("unknown runtime failure"))
    assert not _is_onnx_cuda_failure(onnx_input_error("CUDA input shape is invalid"))
    assert not _is_onnx_cuda_failure(RuntimeError("CUDA failure"))


def test_rapidocr_wrapped_cuda_failure_uses_structured_ort_cause() -> None:
    from onnxruntime.capi.onnxruntime_pybind11_state import InvalidArgument, RuntimeException
    from rapidocr_onnxruntime.utils.infer_engine import ONNXRuntimeError, OrtInferSession

    class FakeSession:
        def get_inputs(self):
            return [type("Node", (), {"name": "input"})()]

        def get_outputs(self):
            return [type("Node", (), {"name": "output"})()]

        def run(self, _names: Any, _inputs: Any) -> None:
            try:
                raise RuntimeException("CUDAExecutionProvider CUBLAS failure")
            except RuntimeException as error:
                raise ONNXRuntimeError("inference failed") from error

    infer = OrtInferSession.__new__(OrtInferSession)
    infer.session = FakeSession()
    pinned = PinnedOcrEngine.__new__(PinnedOcrEngine)
    pinned.resolved_device = "gpu"
    pinned.orientation_retry = False
    # This test isolates wrapped execution error classification, not provider inspection.
    pinned.verify_runtime_provider = lambda: None
    pinned._engine = lambda image: (infer(image), 0)
    with pytest.raises(CatalogError) as caught:
        pinned.read(None)
    assert caught.value.code == OCR_GPU_EXECUTION_FAILED

    try:
        raise InvalidArgument("CUDA input shape is invalid")
    except InvalidArgument as cause:
        wrapped = ONNXRuntimeError("inference failed")
        wrapped.__cause__ = cause
    assert not _is_onnx_cuda_failure(wrapped)
    assert not _is_onnx_cuda_failure(RuntimeError("CUDA text only"))
    assert not _is_onnx_cuda_failure(ONNXRuntimeError("unknown wrapper error"))


def run(harness: Any, monkeypatch: Any, *, cache: FakeEngineCache, gpu_qualified: bool = True,
        limit: int | None = None, cancel: Any = None) -> dict[str, Any]:
    install_runtime_evidence(monkeypatch, gpu_qualified=gpu_qualified)
    return ocr_service.run_queue(
        harness.catalog,
        data_root=harness.data_root,
        engine_cache=cache,
        limit=limit,
        cancel=cancel,
    )


def enqueue(harness: Any, asset_id: int, *, device: str = "auto", force: bool = False) -> dict[str, int]:
    return ocr_service.enqueue_targets(
        harness.catalog,
        [{"asset_id": asset_id, "input_revision": harness.sha_of(asset_id)}],
        now_ms=harness.clock(),
        requested_device=device,
        force=force,
    )


def jobs_for(harness: Any, asset_id: int) -> dict[str, Any]:
    return {
        str(row["processor_id"]): row
        for row in harness.catalog.read_all(
            "SELECT * FROM jobs WHERE task_type = ? AND subject_id = ? ORDER BY job_id",
            (contract.OCR_TASK_TYPE, str(asset_id)),
        )
    }


def test_auto_gpu_success_and_empty_document_never_queue_cpu(
    harness: Any, monkeypatch: Any
) -> None:
    asset_id = make_asset(harness)
    enqueue(harness, asset_id)
    result = run(harness, monkeypatch, cache=FakeEngineCache())
    jobs = jobs_for(harness, asset_id)
    assert result["steps"] == 1
    assert set(jobs) == {contract.OCR_GPU_PROCESSOR_ID}
    assert jobs[contract.OCR_GPU_PROCESSOR_ID]["state"] == "SUCCEEDED"
    assert harness.ocr_document(asset_id)["processor_id"] == contract.OCR_GPU_PROCESSOR_ID
    assert result["cpu_fallbacks"] == []


def test_gpu_success_empty_document_never_queues_cpu(harness: Any, monkeypatch: Any) -> None:
    asset_id = make_asset(harness)
    enqueue(harness, asset_id)
    run(harness, monkeypatch, cache=FakeEngineCache(gpu_empty=True))
    jobs = jobs_for(harness, asset_id)
    assert jobs[contract.OCR_GPU_PROCESSOR_ID]["state"] == "SUCCEEDED"
    assert set(jobs) == {contract.OCR_GPU_PROCESSOR_ID}
    assert harness.ocr_document(asset_id)["region_count"] == 0


def test_low_quality_success_does_not_start_cpu_fallback(harness: Any, monkeypatch: Any) -> None:
    asset_id = make_asset(harness)
    enqueue(harness, asset_id)
    run(harness, monkeypatch, cache=FakeEngineCache(gpu_confidence=0.2))
    jobs = jobs_for(harness, asset_id)
    assert set(jobs) == {contract.OCR_GPU_PROCESSOR_ID}
    assert harness.ocr_regions(asset_id)[0]["confidence"] == 0.2


@pytest.mark.parametrize(
    "failure", [None, OCR_GPU_INITIALIZATION_FAILED, OCR_GPU_EXECUTION_FAILED]
)
def test_gpu_ineligibility_or_recognized_device_fault_runs_one_cpu_branch(
    harness: Any, monkeypatch: Any, failure: str | None
) -> None:
    asset_id = make_asset(harness)
    enqueue(harness, asset_id)
    run(
        harness,
        monkeypatch,
        cache=FakeEngineCache(gpu_failure=failure),
        gpu_qualified=failure is not None,
    )
    jobs = jobs_for(harness, asset_id)
    gpu = jobs[contract.OCR_GPU_PROCESSOR_ID]
    cpu = jobs[contract.OCR_CPU_PROCESSOR_ID]
    assert gpu["state"] == ("CANCELLED" if failure is None else "FAILED")
    assert gpu["last_error_code"] == (
        "RUNTIME_CAPABILITY_UNAVAILABLE" if failure is None else failure
    )
    assert cpu["state"] == "SUCCEEDED"
    assert harness.ocr_document(asset_id)["processor_id"] == contract.OCR_CPU_PROCESSOR_ID
    summary = ocr_summary(harness.catalog.db_path)
    assert summary["current_results_by_processor"][contract.OCR_CPU_PROCESSOR_ID] == 1
    assert summary["cpu_fallbacks"]["current_results"] == 1
    progress = enrichment_counts(
        harness.catalog,
        task_type=contract.OCR_TASK_TYPE,
        processor_id=contract.OCR_PROCESSOR_IDS,
        source_id=harness.source_id,
    )
    assert progress["handled"] == 1
    run(harness, monkeypatch, cache=FakeEngineCache(), gpu_qualified=False)
    assert len(jobs_for(harness, asset_id)) == 2


def test_restart_resumes_persisted_cpu_branch_without_reviving_blocked_gpu(
    harness: Any, monkeypatch: Any
) -> None:
    asset_id = make_asset(harness)
    enqueue(harness, asset_id)
    install_runtime_evidence(monkeypatch, gpu_qualified=False)
    # This isolated fault setup represents an already admitted GPU caller.
    monkeypatch.setattr(
        "photo_search.runtime.background_slot.background_slot_owned_by_current_thread",
        lambda: True,
    )
    scheduler = ocr_service.build_scheduler(harness.catalog)
    scheduler.claim_next(
        worker_id="synthetic", now_ms=harness.clock(), _ocr_gpu_managed=True,
    )
    # Qualification failure is durable and the next process has no in-memory fallback state.
    gpu = jobs_for(harness, asset_id)[contract.OCR_GPU_PROCESSOR_ID]
    assert gpu["state"] == "BLOCKED"
    result = run(harness, monkeypatch, cache=FakeEngineCache(), gpu_qualified=False)
    jobs = jobs_for(harness, asset_id)
    assert jobs[contract.OCR_GPU_PROCESSOR_ID]["state"] == "CANCELLED"
    assert jobs[contract.OCR_CPU_PROCESSOR_ID]["state"] == "SUCCEEDED"
    assert result["steps"] == 1


def test_auto_handoff_requeues_cancelled_cpu_once_across_catalog_restart(
    harness: Any, monkeypatch: Any
) -> None:
    asset_id = make_asset(harness, "restart-handoff.jpg")
    enqueue(harness, asset_id, device="cpu")
    enqueue(harness, asset_id, device="auto")
    rows = jobs_for(harness, asset_id)
    cpu_id = int(rows[contract.OCR_CPU_PROCESSOR_ID]["job_id"])
    assert rows[contract.OCR_CPU_PROCESSOR_ID]["state"] == "CANCELLED"
    assert rows[contract.OCR_CPU_PROCESSOR_ID]["requested_device"] == "auto"
    with harness.catalog.write() as connection:
        connection.execute(
            "UPDATE jobs SET state = 'FAILED', last_error_code = ?, updated_at = ? WHERE job_id = ?",
            (OCR_GPU_EXECUTION_FAILED, harness.clock(),
             int(rows[contract.OCR_GPU_PROCESSOR_ID]["job_id"])),
        )
    harness.service.close()
    harness.service = CatalogService.open(
        data_root=harness.data_root,
        clock=harness.clock,
        lease_seconds=60,
        disk_usage=harness.usage,
    )
    assert ocr_service._coordinate_gpu_fallbacks(
        harness.catalog, now_ms=harness.clock()
    ) == 1
    assert ocr_service._coordinate_gpu_fallbacks(
        harness.catalog, now_ms=harness.clock()
    ) == 0
    assert int(jobs_for(harness, asset_id)[contract.OCR_CPU_PROCESSOR_ID]["job_id"]) == cpu_id
    assert run(harness, monkeypatch, cache=FakeEngineCache()) ["steps"] == 1
    rows = jobs_for(harness, asset_id)
    assert rows[contract.OCR_CPU_PROCESSOR_ID]["state"] == "SUCCEEDED"
    assert int(rows[contract.OCR_CPU_PROCESSOR_ID]["job_id"]) == cpu_id


def test_auto_handoff_requeues_prior_failed_cpu_job(harness: Any, monkeypatch: Any) -> None:
    asset_id = make_asset(harness, "failed-cpu-handoff.jpg")
    enqueue(harness, asset_id, device="cpu")
    cpu = jobs_for(harness, asset_id)[contract.OCR_CPU_PROCESSOR_ID]
    with harness.catalog.write() as connection:
        connection.execute(
            "UPDATE jobs SET state = 'FAILED', last_error_code = 'OCR_CPU_OLD_FAILURE', "
            "lease_token = NULL, lease_expires_at = NULL, worker_id = NULL WHERE job_id = ?",
            (int(cpu["job_id"]),),
        )
    enqueue(harness, asset_id, device="auto")
    assert jobs_for(harness, asset_id)[contract.OCR_CPU_PROCESSOR_ID]["requested_device"] == "auto"
    result = run(
        harness,
        monkeypatch,
        cache=FakeEngineCache(gpu_failure=OCR_GPU_EXECUTION_FAILED),
        limit=2,
    )
    rows = jobs_for(harness, asset_id)
    assert result["steps"] == 2
    assert rows[contract.OCR_CPU_PROCESSOR_ID]["state"] == "SUCCEEDED"
    assert int(rows[contract.OCR_CPU_PROCESSOR_ID]["job_id"]) == int(cpu["job_id"])


def test_old_auto_gpu_failure_does_not_reopen_later_manual_cpu_choice(
    harness: Any,
) -> None:
    asset_id = make_asset(harness, "manual-choice.jpg")
    enqueue(harness, asset_id, device="cpu")
    enqueue(harness, asset_id, device="auto")
    rows = jobs_for(harness, asset_id)
    cpu_id = int(rows[contract.OCR_CPU_PROCESSOR_ID]["job_id"])
    with harness.catalog.write() as connection:
        connection.execute(
            "UPDATE jobs SET state = 'FAILED', last_error_code = ?, updated_at = ? WHERE job_id = ?",
            (OCR_GPU_EXECUTION_FAILED, harness.clock(),
             int(rows[contract.OCR_GPU_PROCESSOR_ID]["job_id"])),
        )
    # A later explicit CPU request supersedes the stale auto branch in the durable row.
    ocr_service.enqueue_targets(
        harness.catalog,
        [{"asset_id": asset_id, "input_revision": harness.sha_of(asset_id)}],
        now_ms=harness.clock(),
        requested_device="cpu",
    )
    assert ocr_service._coordinate_gpu_fallbacks(
        harness.catalog, now_ms=harness.clock()
    ) == 0
    cpu = jobs_for(harness, asset_id)[contract.OCR_CPU_PROCESSOR_ID]
    assert int(cpu["job_id"]) == cpu_id
    assert cpu["state"] == "CANCELLED"
    assert cpu["requested_device"] == "cpu"
    enqueue(harness, asset_id, device="gpu", force=True)
    gpu = jobs_for(harness, asset_id)[contract.OCR_GPU_PROCESSOR_ID]
    assert gpu["state"] == "QUEUED"
    assert gpu["requested_device"] == "gpu"
    assert ocr_service._coordinate_gpu_fallbacks(
        harness.catalog, now_ms=harness.clock()
    ) == 0


def test_two_job_limit_counts_executions_once(harness: Any, monkeypatch: Any) -> None:
    for name, width in (("budget-one.jpg", 240), ("budget-two.jpg", 260)):
        Image.new("RGB", (width, 120), "white").save(harness.library / name, "JPEG")
    harness.scan()
    for name in ("budget-one.jpg", "budget-two.jpg"):
        enqueue(harness, harness.asset_id(name))
    result = run(harness, monkeypatch, cache=FakeEngineCache(), limit=2)
    assert result["steps"] == 2
    assert result["pending_jobs"] == 0


def test_two_job_limit_includes_gpu_failure_and_cpu_fallback(
    harness: Any, monkeypatch: Any
) -> None:
    asset_id = make_asset(harness, "budget-fallback.jpg")
    enqueue(harness, asset_id)
    result = run(
        harness,
        monkeypatch,
        cache=FakeEngineCache(gpu_failure=OCR_GPU_EXECUTION_FAILED),
        limit=2,
    )
    rows = jobs_for(harness, asset_id)
    assert result["steps"] == 2
    assert rows[contract.OCR_GPU_PROCESSOR_ID]["state"] == "FAILED"
    assert rows[contract.OCR_CPU_PROCESSOR_ID]["state"] == "SUCCEEDED"
    assert result["pending_jobs"] == 0

    Image.new("RGB", (242, 120), "white").save(
        harness.library / "budget-fallback-pending.jpg", "JPEG"
    )
    harness.scan()
    pending_id = harness.asset_id("budget-fallback-pending.jpg")
    enqueue(harness, pending_id)
    exhausted = run(
        harness,
        monkeypatch,
        cache=FakeEngineCache(gpu_failure=OCR_GPU_EXECUTION_FAILED),
        limit=1,
    )
    pending_rows = jobs_for(harness, pending_id)
    assert exhausted["steps"] == 1
    assert exhausted["pending_jobs"] == 1
    assert pending_rows[contract.OCR_CPU_PROCESSOR_ID]["state"] == "QUEUED"


def test_gpu_provider_mismatch_probe_auto_falls_back_but_explicit_gpu_stays_strict(
    harness: Any, monkeypatch: Any
) -> None:
    class ProbeCache:
        def engine(self, _root: Any, *, resolved_device: str, **_kwargs: Any) -> Any:
            if resolved_device == "gpu":
                raise CatalogError("RUNTIME_RESOLUTION_MISMATCH", "CUDA session resolved CPU")
            return FakeEngine("cpu", failure=None, confidence=0.9)

    asset_id = make_asset(harness, "provider-mismatch.jpg")
    enqueue(harness, asset_id, device="auto")
    result = ocr_service.run_queue(
        harness.catalog, data_root=harness.data_root, engine_cache=ProbeCache()
    )
    rows = jobs_for(harness, asset_id)
    assert rows[contract.OCR_GPU_PROCESSOR_ID]["state"] == "CANCELLED"
    assert rows[contract.OCR_GPU_PROCESSOR_ID]["blocking_reason"] == "OCR_GPU_INITIALIZATION_FAILED"
    assert rows[contract.OCR_CPU_PROCESSOR_ID]["state"] == "SUCCEEDED"
    assert result["gpu_probe_error"]["code"] == "OCR_GPU_INITIALIZATION_FAILED"

    Image.new("RGB", (241, 120), "white").save(
        harness.library / "provider-mismatch-strict.jpg", "JPEG"
    )
    harness.scan()
    strict_id = harness.asset_id("provider-mismatch-strict.jpg")
    enqueue(harness, strict_id, device="gpu")
    assert jobs_for(harness, strict_id)[contract.OCR_GPU_PROCESSOR_ID]["requested_device"] == "gpu"
    ocr_service.run_queue(
        harness.catalog, data_root=harness.data_root, engine_cache=ProbeCache()
    )
    strict_rows = jobs_for(harness, strict_id)
    assert strict_rows[contract.OCR_GPU_PROCESSOR_ID]["requested_device"] == "gpu", dict(
        strict_rows[contract.OCR_GPU_PROCESSOR_ID]
    )
    assert strict_rows[contract.OCR_GPU_PROCESSOR_ID]["state"] in ("BLOCKED", "FAILED")
    assert contract.OCR_CPU_PROCESSOR_ID not in strict_rows


def test_cpu_probe_failure_does_not_skip_gpu_probe(harness: Any, monkeypatch: Any) -> None:
    from dataclasses import replace

    from photo_search.runtime import device as runtime_device

    actual_runtime = runtime_device.runtime_capabilities()
    monkeypatch.setattr(
        runtime_device,
        "runtime_capabilities",
        lambda: replace(
            actual_runtime,
            providers=("CPUExecutionProvider", "CUDAExecutionProvider"),
        ),
    )
    calls: list[str] = []

    class ProbeCache:
        def engine(self, _root: Any, *, resolved_device: str, **_kwargs: Any) -> Any:
            calls.append(resolved_device)
            if resolved_device == "cpu":
                raise CatalogError("RUNTIME_CAPABILITY_UNAVAILABLE", "CPU probe failed")
            return FakeEngine("gpu", failure=None, confidence=0.9)

    scheduler = ocr_service.build_scheduler(harness.catalog)
    evidence = ocr_service.record_runtime_evidence(
        scheduler,
        scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID),
        ProbeCache(),
        data_root=harness.data_root,
        now_ms=harness.clock(),
        note="independent qualification probes",
    )
    assert calls == ["cpu", "gpu"]
    assert evidence["qualified_devices"] == ["gpu"]


def test_shared_missing_weights_probe_error_rejects_both_devices(harness: Any) -> None:
    calls: list[str] = []

    class ProbeCache:
        def engine(self, _root: Any, *, resolved_device: str, **_kwargs: Any) -> Any:
            calls.append(resolved_device)
            raise CatalogError("OCR_WEIGHTS_MISSING", "pinned weights unavailable")

    scheduler = ocr_service.build_scheduler(harness.catalog)
    with pytest.raises(CatalogError) as caught:
        ocr_service.record_runtime_evidence(
            scheduler,
            scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID),
            ProbeCache(),
            data_root=harness.data_root,
            now_ms=harness.clock(),
            note="shared weights failure",
        )
    assert caught.value.code == "OCR_WEIGHTS_MISSING"
    assert calls == ["cpu"]


@pytest.mark.parametrize("terminal_state", ["CANCELLED", "FAILED"])
def test_generic_resume_rejects_terminal_non_ocr_job(
    harness: Any, terminal_state: str
) -> None:
    with harness.catalog.write() as connection:
        job_id = jobs.enqueue(
            connection,
            subject_type="asset",
            subject_id="non-ocr-terminal",
            input_revision="b" * 64,
            task_type="geo_extract",
            processor_id="geo-exif-gps@1",
            now_ms=harness.clock(),
        )
        connection.execute(
            "UPDATE jobs SET state = ? WHERE job_id = ?", (terminal_state, job_id)
        )
        with pytest.raises(CatalogError) as caught:
            jobs.resume_blocked(connection, job_id=job_id, now_ms=harness.clock())
    assert caught.value.code == INVALID_STATE_TRANSITION


def test_generic_resume_still_accepts_blocked_job(harness: Any) -> None:
    with harness.catalog.write() as connection:
        job_id = jobs.enqueue(
            connection,
            subject_type="asset",
            subject_id="blocked-job",
            input_revision="c" * 64,
            task_type="geo_extract",
            processor_id="geo-exif-gps@1",
            now_ms=harness.clock(),
        )
        connection.execute(
            "UPDATE jobs SET state = 'BLOCKED' WHERE job_id = ?", (job_id,)
        )
        resumed = jobs.resume_blocked(connection, job_id=job_id, now_ms=harness.clock())
    assert resumed.state == jobs.STATE_QUEUED


def test_unverified_gpu_provider_never_authorizes_auto_cpu_fallback(
    harness: Any,
) -> None:
    class ProbeCache:
        fail_gpu = True

        def engine(self, _root: Any, *, resolved_device: str, **_kwargs: Any) -> Any:
            if resolved_device == "gpu" and self.fail_gpu:
                raise CatalogError(
                    "OCR_SESSION_PROVIDER_UNVERIFIED", "session provider inspection failed"
                )
            return FakeEngine(resolved_device, failure=None, confidence=0.9)

    asset_id = make_asset(harness, "provider-unverified.jpg")
    enqueue(harness, asset_id, device="auto")
    cache = ProbeCache()
    result = ocr_service.run_queue(
        harness.catalog, data_root=harness.data_root, engine_cache=cache
    )
    rows = jobs_for(harness, asset_id)
    assert result["gpu_probe_error"]["code"] == "OCR_SESSION_PROVIDER_UNVERIFIED"
    assert contract.OCR_CPU_PROCESSOR_ID not in rows
    assert rows[contract.OCR_GPU_PROCESSOR_ID]["state"] == "BLOCKED"
    assert rows[contract.OCR_GPU_PROCESSOR_ID]["last_error_code"] == (
        "OCR_SESSION_PROVIDER_UNVERIFIED"
    )

    # A later healthy capability snapshot must not erase the concrete failed probe fact.
    cache.fail_gpu = False
    scheduler = ocr_service.build_scheduler(harness.catalog)
    ocr_service.record_runtime_evidence(
        scheduler,
        scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID),
        cache,
        data_root=harness.data_root,
        now_ms=harness.clock() + 1,
        note="provider verification retry",
    )
    assert ocr_service._coordinate_gpu_fallbacks(
        harness.catalog, now_ms=harness.clock() + 1
    ) == 0
    rows = jobs_for(harness, asset_id)
    assert contract.OCR_CPU_PROCESSOR_ID not in rows
    assert rows[contract.OCR_GPU_PROCESSOR_ID]["state"] == "BLOCKED"


def test_explicit_devices_force_profiles_without_rewriting_job_identity(
    harness: Any, monkeypatch: Any
) -> None:
    asset_id = make_asset(harness)
    enqueue(harness, asset_id, device="cpu")
    run(harness, monkeypatch, cache=FakeEngineCache())
    cpu_job_id = jobs_for(harness, asset_id)[contract.OCR_CPU_PROCESSOR_ID]["job_id"]
    assert harness.ocr_document(asset_id)["processor_id"] == contract.OCR_CPU_PROCESSOR_ID

    enqueue(harness, asset_id, device="gpu", force=True)
    run(harness, monkeypatch, cache=FakeEngineCache())
    after_gpu = jobs_for(harness, asset_id)
    assert after_gpu[contract.OCR_CPU_PROCESSOR_ID]["job_id"] == cpu_job_id
    assert after_gpu[contract.OCR_GPU_PROCESSOR_ID]["state"] == "SUCCEEDED"
    assert harness.ocr_document(asset_id)["processor_id"] == contract.OCR_GPU_PROCESSOR_ID
    assert harness.catalog.read_one("SELECT COUNT(*) AS n FROM artifacts")["n"] == 2

    enqueue(harness, asset_id, device="cpu", force=True)
    run(harness, monkeypatch, cache=FakeEngineCache())
    assert jobs_for(harness, asset_id)[contract.OCR_CPU_PROCESSOR_ID]["job_id"] == cpu_job_id
    assert harness.ocr_document(asset_id)["processor_id"] == contract.OCR_CPU_PROCESSOR_ID


def test_same_config_byte_replay_reuses_artifact_and_keeps_raw_confidence(
    harness: Any, monkeypatch: Any
) -> None:
    asset_id = make_asset(harness)
    enqueue(harness, asset_id)
    run(harness, monkeypatch, cache=FakeEngineCache(gpu_confidence=0.600001))
    first = dict(harness.ocr_document(asset_id))
    region = harness.ocr_regions(asset_id)[0]
    assert region["confidence"] == 0.600001
    assert first["region_count"] == 1
    enqueue(harness, asset_id, device="gpu", force=True)
    run(harness, monkeypatch, cache=FakeEngineCache(gpu_confidence=0.600001))
    assert harness.catalog.read_one("SELECT COUNT(*) AS n FROM artifacts")["n"] == 1
    assert harness.ocr_document(asset_id)["text_revision"] == first["text_revision"] + 1


def test_same_profile_different_bytes_are_rejected_without_projection_writes(
    harness: Any, monkeypatch: Any
) -> None:
    asset_id = make_asset(harness)
    enqueue(harness, asset_id)
    run(harness, monkeypatch, cache=FakeEngineCache(gpu_confidence=0.8))
    before = dict(harness.ocr_document(asset_id))
    queue_before = harness.catalog.read_one(
        "SELECT COUNT(*) AS n FROM search_projection_queue WHERE cause = 'OCR_TEXT_CHANGED'"
    )["n"]
    enqueue(harness, asset_id, device="gpu", force=True)
    install_runtime_evidence(monkeypatch)
    # The test exercises artifact conflict after simulated GPU admission.
    monkeypatch.setattr(
        "photo_search.runtime.background_slot.background_slot_owned_by_current_thread",
        lambda: True,
    )
    scheduler = ocr_service.build_scheduler(harness.catalog)
    with pytest.raises(CatalogError) as error:
        scheduler.run_once(
            ocr_executor(harness.catalog, engine_cache=FakeEngineCache(gpu_confidence=0.81)),
            worker_id="synthetic",
            now_ms=harness.clock(),
        )
    assert error.value.code == ARTIFACT_CONFLICT
    assert dict(harness.ocr_document(asset_id)) == before
    assert harness.catalog.read_one(
        "SELECT COUNT(*) AS n FROM search_projection_queue WHERE cause = 'OCR_TEXT_CHANGED'"
    )["n"] == queue_before


def test_cpu_failure_and_cancellation_preserve_the_previous_current_result(
    harness: Any, monkeypatch: Any
) -> None:
    asset_id = make_asset(harness)
    enqueue(harness, asset_id)
    run(harness, monkeypatch, cache=FakeEngineCache())
    previous = dict(harness.ocr_document(asset_id))

    enqueue(harness, asset_id, device="cpu", force=True)
    run(harness, monkeypatch, cache=FakeEngineCache(cpu_failure="OCR_CPU_SYNTHETIC_FAILURE"))
    assert dict(harness.ocr_document(asset_id)) == previous
    assert jobs_for(harness, asset_id)[contract.OCR_CPU_PROCESSOR_ID]["state"] == "FAILED"

    enqueue(harness, asset_id, device="cpu", force=True)
    run(harness, monkeypatch, cache=FakeEngineCache(), cancel=lambda: True)
    assert dict(harness.ocr_document(asset_id)) == previous
    assert jobs_for(harness, asset_id)[contract.OCR_CPU_PROCESSOR_ID]["state"] == "CANCELLED"


def test_expired_gpu_submission_preserves_the_previous_current_result(
    harness: Any, monkeypatch: Any
) -> None:
    asset_id = make_asset(harness)
    enqueue(harness, asset_id)
    run(harness, monkeypatch, cache=FakeEngineCache())
    previous = dict(harness.ocr_document(asset_id))
    enqueue(harness, asset_id, device="gpu", force=True)
    install_runtime_evidence(monkeypatch)
    # This isolated fault setup represents an already admitted GPU caller.
    monkeypatch.setattr(
        "photo_search.runtime.background_slot.background_slot_owned_by_current_thread",
        lambda: True,
    )
    scheduler = ocr_service.build_scheduler(harness.catalog)
    ocr_service.record_runtime_evidence(
        scheduler,
        scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID),
        FakeEngineCache(),
        data_root=harness.data_root,
        now_ms=harness.clock(),
        note="expired result test",
    )
    request = scheduler.claim_next(
        worker_id="synthetic", now_ms=harness.clock(), _ocr_gpu_managed=True,
    )
    assert request is not None
    raw = ocr_executor(harness.catalog, engine_cache=FakeEngineCache())(request.to_envelope())
    with pytest.raises(CatalogError) as error:
        scheduler.submit(
            JobResult.from_envelope(raw),
            now_ms=harness.clock() + 61_000,
        )
    assert error.value.code == STALE_LEASE
    assert dict(harness.ocr_document(asset_id)) == previous


def test_live_profile_lease_blocks_switch_and_unknown_or_input_failures_do_not_fallback(
    harness: Any, monkeypatch: Any
) -> None:
    asset_id = make_asset(harness)
    enqueue(harness, asset_id, force=True)
    install_runtime_evidence(monkeypatch)
    # This isolated fault setup represents an already admitted GPU caller.
    monkeypatch.setattr(
        "photo_search.runtime.background_slot.background_slot_owned_by_current_thread",
        lambda: True,
    )
    scheduler = ocr_service.build_scheduler(harness.catalog)
    ocr_service.record_runtime_evidence(
        scheduler,
        scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID),
        FakeEngineCache(),
        data_root=harness.data_root,
        now_ms=harness.clock(),
        note="live lease test",
    )
    scheduler.claim_next(
        worker_id="synthetic", now_ms=harness.clock(), _ocr_gpu_managed=True,
    )
    assert enqueue(harness, asset_id, device="cpu")["live"] == 1
    assert contract.OCR_CPU_PROCESSOR_ID not in jobs_for(harness, asset_id)

    # An unknown executor exception is a scheduler error, not an OCR device-fault code.
    scheduler.cancel(
        job_id=int(jobs_for(harness, asset_id)[contract.OCR_GPU_PROCESSOR_ID]["job_id"]),
        now_ms=harness.clock(),
    )
    enqueue(harness, asset_id, force=True)
    run(harness, monkeypatch, cache=FakeEngineCache(gpu_failure="UNKNOWN_PYTHON_EXCEPTION"))
    jobs = jobs_for(harness, asset_id)
    assert contract.OCR_CPU_PROCESSOR_ID not in jobs


def test_changed_input_does_not_create_cpu_fallback(harness: Any, monkeypatch: Any) -> None:
    asset_id = make_asset(harness, "changed.jpg")
    enqueue(harness, asset_id)
    (harness.library / "changed.jpg").write_bytes(b"changed after scan")
    run(harness, monkeypatch, cache=FakeEngineCache())
    jobs = jobs_for(harness, asset_id)
    assert jobs[contract.OCR_GPU_PROCESSOR_ID]["state"] == "BLOCKED"
    assert contract.OCR_CPU_PROCESSOR_ID not in jobs


def test_stop_between_ocr_jobs_preserves_unclaimed_work_for_resume(
    harness: Any, monkeypatch: Any
) -> None:
    first = make_asset(harness, "stop-first.jpg")
    Image.new("RGB", (241, 120), "white").save(harness.library / "stop-second.jpg", "JPEG")
    harness.scan()
    second = harness.asset_id("stop-second.jpg")
    enqueue(harness, first, device="cpu")
    enqueue(harness, second, device="cpu")
    install_runtime_evidence(monkeypatch)
    def stop_after_first():
        row = harness.catalog.read_one(
            "SELECT COUNT(*) AS n FROM jobs WHERE task_type=? AND state='SUCCEEDED'",
            (contract.OCR_TASK_TYPE,),
        )
        return int(row["n"]) >= 1

    partial = ocr_service.run_queue(
        harness.catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
        stop_check=stop_after_first,
    )
    assert partial["steps"] == 1
    states = [row["state"] for row in harness.catalog.read_all(
        "SELECT state FROM jobs WHERE task_type=? ORDER BY job_id", (contract.OCR_TASK_TYPE,)
    )]
    assert states == ["SUCCEEDED", "QUEUED"]
    resumed = ocr_service.run_queue(
        harness.catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
    )
    assert resumed["steps"] == 1
    states = [row["state"] for row in harness.catalog.read_all(
        "SELECT state FROM jobs WHERE task_type=? ORDER BY job_id", (contract.OCR_TASK_TYPE,)
    )]
    assert states == ["SUCCEEDED", "SUCCEEDED"]


def test_ocr_expired_during_execution_does_not_publish(
    harness: Any, monkeypatch: Any
) -> None:
    asset_id = make_asset(harness, "late-execution.jpg")
    enqueue(harness, asset_id, device="cpu")
    install_runtime_evidence(monkeypatch)
    scheduler = ocr_service.build_scheduler(harness.catalog)
    scheduler.lease_seconds = 1
    cache = FakeEngineCache()
    ocr_service.record_runtime_evidence(
        scheduler, scheduler.spec_for(contract.OCR_CPU_PROCESSOR_ID), cache,
        data_root=harness.data_root, now_ms=harness.clock(), note="lease expiry test",
    )
    executor = ocr_executor(harness.catalog, engine_cache=cache)

    def delayed(request):
        output = executor(request)
        harness.clock.advance(1001)
        return output

    with pytest.raises(CatalogError) as error:
        scheduler.run_once(
            delayed, worker_id="late-test", now_ms=harness.clock(),
            completion_clock=harness.clock,
        )
    assert error.value.code == STALE_LEASE
    assert jobs_for(harness, asset_id)[contract.OCR_CPU_PROCESSOR_ID]["state"] == "RUNNING"
    assert harness.ocr_document(asset_id) is None


def test_ocr_lease_expiring_while_waiting_to_publish_is_rejected(
    harness: Any, monkeypatch: Any
) -> None:
    from contextlib import contextmanager

    asset_id = make_asset(harness, "write-wait.jpg")
    enqueue(harness, asset_id, device="cpu")
    install_runtime_evidence(monkeypatch)
    scheduler = ocr_service.build_scheduler(harness.catalog)
    scheduler.lease_seconds = 1
    cache = FakeEngineCache()
    ocr_service.record_runtime_evidence(
        scheduler, scheduler.spec_for(contract.OCR_CPU_PROCESSOR_ID), cache,
        data_root=harness.data_root, now_ms=harness.clock(), note="write wait test",
    )
    executor = ocr_executor(harness.catalog, engine_cache=cache)
    real_write = harness.catalog.write
    ready = False

    def delayed(request):
        nonlocal ready
        output = executor(request)
        ready = True
        return output

    @contextmanager
    def delayed_write():
        nonlocal ready
        with real_write() as connection:
            if ready:
                ready = False
                harness.clock.advance(1001)
            yield connection

    monkeypatch.setattr(harness.catalog, "write", delayed_write)
    with pytest.raises(CatalogError) as error:
        scheduler.run_once(
            delayed, worker_id="late-test", now_ms=harness.clock(),
            completion_clock=harness.clock,
        )
    assert error.value.code == STALE_LEASE
    assert jobs_for(harness, asset_id)[contract.OCR_CPU_PROCESSOR_ID]["state"] == "RUNNING"
    assert harness.ocr_document(asset_id) is None


def test_ocr_queue_uses_live_submission_clock(harness: Any, monkeypatch: Any) -> None:
    asset_id = make_asset(harness, "queue-late.jpg")
    enqueue(harness, asset_id, device="cpu")
    install_runtime_evidence(monkeypatch)
    real_factory = ocr_service.ocr_executor

    def delayed_factory(*args, **kwargs):
        executor = real_factory(*args, **kwargs)
        def delayed(request):
            output = executor(request)
            harness.clock.advance(61_000)
            return output
        return delayed

    monkeypatch.setattr(ocr_service, "ocr_executor", delayed_factory)
    with pytest.raises(CatalogError) as error:
        ocr_service.run_queue(
            harness.catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
        )
    assert error.value.code == STALE_LEASE
    assert jobs_for(harness, asset_id)[contract.OCR_CPU_PROCESSOR_ID]["state"] == "RUNNING"
    assert harness.ocr_document(asset_id) is None


def test_cpu_probe_preserves_other_device_observation_without_loading_gpu(
    harness: Any, monkeypatch: Any
) -> None:
    from dataclasses import replace

    from photo_search.runtime import device as runtime_device

    actual = runtime_device.runtime_capabilities()
    monkeypatch.setattr(runtime_device, "runtime_capabilities", lambda: replace(
        actual, providers=("CPUExecutionProvider", "CUDAExecutionProvider"),
    ))
    calls = []
    class Cache:
        def engine(self, _root, *, resolved_device, **_kwargs):
            calls.append(resolved_device)
            return FakeEngine(resolved_device, failure=None, confidence=0.9)

    scheduler = ocr_service.build_scheduler(harness.catalog)
    spec = scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID)
    ocr_service.record_runtime_evidence(
        scheduler, spec, Cache(), data_root=harness.data_root, now_ms=harness.clock(), note="full",
    )
    previous = scheduler.latest_capability().providers_for("gpu")[0].to_envelope()
    harness.clock.advance(100)
    calls.clear()
    evidence = ocr_service.record_runtime_evidence(
        scheduler, spec, Cache(), data_root=harness.data_root, now_ms=harness.clock(),
        note="cpu", devices=("cpu",),
    )
    assert calls == ["cpu"]
    assert evidence["qualified_devices"] == ["cpu"]
    assert scheduler.latest_capability().providers_for("gpu")[0].to_envelope() == previous



@pytest.mark.parametrize("change", ["provider_missing", "runtime_identity"])
def test_cpu_probe_does_not_reuse_gpu_evidence_after_runtime_change(
    harness: Any, monkeypatch: Any, change: str
) -> None:
    from dataclasses import replace

    from photo_search.runtime import device as runtime_device

    actual = runtime_device.runtime_capabilities()
    current = [replace(actual, providers=("CPUExecutionProvider", "CUDAExecutionProvider"))]
    monkeypatch.setattr(runtime_device, "runtime_capabilities", lambda: current[0])

    class Cache:
        def engine(self, _root, *, resolved_device, **_kwargs):
            return FakeEngine(resolved_device, failure=None, confidence=0.9)

    scheduler = ocr_service.build_scheduler(harness.catalog)
    spec = scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID)
    ocr_service.record_runtime_evidence(
        scheduler, spec, Cache(), data_root=harness.data_root, now_ms=harness.clock(), note="full",
    )
    assert scheduler.latest_capability().providers_for("gpu")[0].health == "healthy"
    harness.clock.advance(1)
    if change == "provider_missing":
        current[0] = replace(current[0], providers=("CPUExecutionProvider",))
    else:
        current[0] = replace(current[0], platform="different-test-platform")
    ocr_service.record_runtime_evidence(
        scheduler, spec, Cache(), data_root=harness.data_root, now_ms=harness.clock(),
        note="cpu", devices=("cpu",),
    )
    gpu = scheduler.latest_capability().providers_for("gpu")
    assert not gpu or all(provider.health != "healthy" for provider in gpu)


def test_partial_probe_rejects_older_observation_for_same_device(
    harness: Any, monkeypatch: Any
) -> None:
    from dataclasses import replace

    from photo_search.runtime import device as runtime_device

    actual = runtime_device.runtime_capabilities()
    monkeypatch.setattr(runtime_device, "runtime_capabilities", lambda: replace(
        actual, providers=("CPUExecutionProvider", "CUDAExecutionProvider"),
    ))
    class Cache:
        def engine(self, _root, *, resolved_device, **_kwargs):
            return FakeEngine(resolved_device, failure=None, confidence=0.9)

    scheduler = ocr_service.build_scheduler(harness.catalog)
    spec = scheduler.spec_for(contract.OCR_CPU_PROCESSOR_ID)
    ocr_service.record_runtime_evidence(
        scheduler, spec, Cache(), data_root=harness.data_root, now_ms=harness.clock(), note="new",
    )
    with pytest.raises(CatalogError) as error:
        ocr_service.record_runtime_evidence(
            scheduler, spec, Cache(), data_root=harness.data_root, now_ms=harness.clock() - 1,
            note="old", devices=("cpu",),
        )
    assert error.value.code == "RUNTIME_CAPABILITY_OBSERVATION_STALE"


def test_device_scheduler_pins_its_own_capability_when_latest_changes(
    harness: Any, monkeypatch: Any
) -> None:
    from photo_search.scheduler.engine import Scheduler

    asset_id = make_asset(harness, "pin-capability.jpg")
    enqueue(harness, asset_id, device="cpu")
    install_runtime_evidence(monkeypatch)
    full = ocr_service.build_scheduler(harness.catalog)
    evidence = ocr_service.record_runtime_evidence(
        full, full.spec_for(contract.OCR_CPU_PROCESSOR_ID), FakeEngineCache(),
        data_root=harness.data_root, now_ms=harness.clock(), note="fresh cpu",
    )
    pinned_revision = evidence["capability_revision"]
    pinned = full.latest_capability()
    changed = dict(pinned.envelope)
    changed["providers"] = [dict(provider) for provider in changed["providers"]]
    for provider in changed["providers"]:
        if provider["device"] == "cpu":
            provider["health"] = "unhealthy"
    harness.clock.advance(1)
    full.record_capability(RuntimeCapabilities.seal(changed), now_ms=harness.clock())
    assert full.latest_capability().capability_revision != pinned_revision
    scheduler = Scheduler(
        harness.catalog, [full.spec_for(contract.OCR_CPU_PROCESSOR_ID)],
        capability_revision=pinned_revision,
    )
    request = scheduler.claim_next(worker_id="pinned", now_ms=harness.clock())
    assert request is not None
    assert request.runtime_capability_revision == pinned_revision
    assert request.resolved_device == "cpu"


def test_cpu_ocr_runs_without_gpu_probe_while_shared_slot_is_held(
    harness: Any, monkeypatch: Any
) -> None:
    import threading
    from dataclasses import replace

    from photo_search.face import service as face_service
    from photo_search.runtime import device as runtime_device

    asset_id = make_asset(harness, "cpu-independent.jpg")
    enqueue(harness, asset_id, device="cpu")
    actual = runtime_device.runtime_capabilities()
    monkeypatch.setattr(runtime_device, "runtime_capabilities", lambda: replace(
        actual, providers=("CPUExecutionProvider", "CUDAExecutionProvider"),
    ))
    calls = []
    class Cache(FakeEngineCache):
        def engine(self, root, *, resolved_device, **kwargs):
            calls.append(resolved_device)
            return super().engine(root, resolved_device=resolved_device, **kwargs)

    lock = threading.RLock()
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", lock)
    held = threading.Event()
    release = threading.Event()
    def holder():
        with lock:
            held.set()
            assert release.wait(5)
    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    try:
        assert held.wait(2)
        result = ocr_service.run_queue(
            harness.catalog, data_root=harness.data_root, engine_cache=Cache(),
        )
        assert result["steps"] == 1
        assert jobs_for(harness, asset_id)[contract.OCR_CPU_PROCESSOR_ID]["state"] == "SUCCEEDED"
        assert calls and set(calls) == {"cpu"}
        assert not lock.acquire(blocking=False)
    finally:
        release.set()
        thread.join(timeout=3)


@pytest.mark.parametrize("cancel_mode", ["stop", "cancel"])
def test_gpu_wait_cancel_does_not_claim_or_hold_sqlite_and_can_resume(
    harness: Any, monkeypatch: Any, cancel_mode: str
) -> None:
    import threading

    from photo_search.catalog.db import Catalog
    from photo_search.face import service as face_service

    asset_id = make_asset(harness, "gpu-wait.jpg")
    enqueue(harness, asset_id, device="gpu")
    install_runtime_evidence(monkeypatch)
    actual_lock = threading.RLock()
    attempted = threading.Event()
    held = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()
    outcomes = []
    class ObservedLock:
        def acquire(self, *args, **kwargs):
            attempted.set()
            return actual_lock.acquire(*args, **kwargs)
        def release(self):
            actual_lock.release()
        def _is_owned(self):
            return actual_lock._is_owned()
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", ObservedLock())

    def holder():
        with actual_lock:
            held.set()
            assert release.wait(5)

    def worker():
        try:
            with Catalog.open(data_root=harness.data_root) as catalog:
                outcomes.append(ocr_service.run_queue(
                    catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
                    **({"stop_check": cancelled.is_set} if cancel_mode == "stop"
                       else {"cancel": cancelled.is_set}),
                ))
        except Exception as error:
            outcomes.append(error)

    first = threading.Thread(target=holder, daemon=True)
    second = threading.Thread(target=worker, daemon=True)
    first.start()
    try:
        assert held.wait(2)
        second.start()
        assert attempted.wait(3)
        with harness.catalog.write() as connection:
            row = connection.execute(
                "SELECT state,attempts,lease_token FROM jobs WHERE task_type=? AND subject_id=?",
                (contract.OCR_TASK_TYPE, str(asset_id)),
            ).fetchone()
            assert (row["state"], row["attempts"], row["lease_token"]) == ("QUEUED", 0, None)
        cancelled.set()
        second.join(timeout=3)
        assert not second.is_alive()
        assert len(outcomes) == 1 and isinstance(outcomes[0], dict)
        assert outcomes[0]["steps"] == 0
        assert outcomes[0]["waiting_gpu"] is True
        assert jobs_for(harness, asset_id)[contract.OCR_GPU_PROCESSOR_ID]["state"] == "QUEUED"
    finally:
        cancelled.set()
        release.set()
        first.join(timeout=3)
        if second.ident is not None:
            second.join(timeout=3)
    resumed = ocr_service.run_queue(
        harness.catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
    )
    assert resumed["steps"] == 1
    assert jobs_for(harness, asset_id)[contract.OCR_GPU_PROCESSOR_ID]["state"] == "SUCCEEDED"


def test_scoped_gpu_recovery_and_fallback_leave_other_assets_unchanged(
    harness: Any, monkeypatch: Any
) -> None:
    first = make_asset(harness, "scope-first.jpg")
    Image.new("RGB", (241, 120), "white").save(harness.library / "scope-second.jpg", "JPEG")
    harness.scan()
    second = harness.asset_id("scope-second.jpg")
    enqueue(harness, first, device="auto")
    enqueue(harness, second, device="auto")
    install_runtime_evidence(monkeypatch, gpu_qualified=False)
    # This isolated fault setup represents an already admitted GPU caller.
    monkeypatch.setattr(
        "photo_search.runtime.background_slot.background_slot_owned_by_current_thread",
        lambda: True,
    )
    scheduler = ocr_service.build_scheduler(harness.catalog)
    ocr_service.record_runtime_evidence(
        scheduler, scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID), FakeEngineCache(),
        data_root=harness.data_root, now_ms=harness.clock(), note="unqualified",
    )
    assert scheduler.claim_next(
        worker_id="setup", now_ms=harness.clock(), _ocr_gpu_managed=True,
    ) is None
    assert jobs_for(harness, first)[contract.OCR_GPU_PROCESSOR_ID]["state"] == "BLOCKED"
    original = dict(jobs_for(harness, second)[contract.OCR_GPU_PROCESSOR_ID])
    result = ocr_service.run_queue(
        harness.catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
        asset_ids=[first],
    )
    assert result["steps"] == 1
    assert {int(row["subject_id"]) for row in result["cpu_fallbacks"]} == {first}
    assert jobs_for(harness, first)[contract.OCR_CPU_PROCESSOR_ID]["state"] == "SUCCEEDED"
    other = jobs_for(harness, second)
    assert dict(other[contract.OCR_GPU_PROCESSOR_ID]) == original
    assert contract.OCR_CPU_PROCESSOR_ID not in other


def test_cancel_after_wait_before_gpu_claim_leaves_job_queued(
    harness: Any, monkeypatch: Any
) -> None:
    import threading

    from photo_search.catalog.db import Catalog
    from photo_search.face import service as face_service

    asset_id = make_asset(harness, "gpu-cancel-race.jpg")
    enqueue(harness, asset_id, device="gpu")
    install_runtime_evidence(monkeypatch)
    actual_lock = threading.RLock()
    attempted = threading.Event()
    held = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()
    outcomes = []
    class RacingLock:
        def acquire(self, *args, **kwargs):
            acquired = actual_lock.acquire(*args, **kwargs)
            if acquired:
                cancelled.set()
            else:
                attempted.set()
            return acquired
        def release(self):
            actual_lock.release()
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", RacingLock())
    def holder():
        with actual_lock:
            held.set()
            assert release.wait(5)
    def worker():
        try:
            with Catalog.open(data_root=harness.data_root) as catalog:
                outcomes.append(ocr_service.run_queue(
                    catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
                    cancel=cancelled.is_set,
                ))
        except Exception as error:
            outcomes.append(error)
    first = threading.Thread(target=holder, daemon=True)
    second = threading.Thread(target=worker, daemon=True)
    first.start()
    try:
        assert held.wait(2)
        second.start()
        assert attempted.wait(3)
        release.set()
        second.join(timeout=3)
        assert not second.is_alive()
        assert len(outcomes) == 1 and isinstance(outcomes[0], dict)
        assert outcomes[0]["stopped"] is True and outcomes[0]["steps"] == 0
        row = jobs_for(harness, asset_id)[contract.OCR_GPU_PROCESSOR_ID]
        assert row["state"] == "QUEUED" and row["attempts"] == 0 and row["lease_token"] is None
    finally:
        release.set()
        first.join(timeout=2)
        if second.ident is not None:
            second.join(timeout=2)


def test_cpu_ocr_ignores_prior_explicit_gpu_failure_without_gpu_probe(
    harness: Any, monkeypatch: Any
) -> None:
    from dataclasses import replace

    from photo_search.runtime import device as runtime_device

    asset_id = make_asset(harness, "old-explicit-gpu-failure.jpg")
    enqueue(harness, asset_id, device="gpu")
    with monkeypatch.context() as temporary:
        install_runtime_evidence(temporary)
        ocr_service.run_queue(
            harness.catalog, data_root=harness.data_root,
            engine_cache=FakeEngineCache(gpu_failure=OCR_GPU_INITIALIZATION_FAILED),
        )
    assert jobs_for(harness, asset_id)[contract.OCR_GPU_PROCESSOR_ID]["state"] == "FAILED"
    enqueue(harness, asset_id, device="cpu")
    # The stubbed first snapshot and real runtime identity differ; observe the
    # replacement later rather than assigning conflicting facts the same instant.
    harness.clock.advance(1)
    actual = runtime_device.runtime_capabilities()
    monkeypatch.setattr(runtime_device, "runtime_capabilities", lambda: replace(
        actual, providers=("CPUExecutionProvider", "CUDAExecutionProvider"),
    ))
    calls = []
    class Cache(FakeEngineCache):
        def engine(self, root, *, resolved_device, **kwargs):
            calls.append(resolved_device)
            return super().engine(root, resolved_device=resolved_device, **kwargs)
    result = ocr_service.run_queue(
        harness.catalog, data_root=harness.data_root, engine_cache=Cache(),
    )
    assert result["steps"] == 1
    assert calls and set(calls) == {"cpu"}
    assert jobs_for(harness, asset_id)[contract.OCR_GPU_PROCESSOR_ID]["state"] == "FAILED"
    assert jobs_for(harness, asset_id)[contract.OCR_CPU_PROCESSOR_ID]["state"] == "SUCCEEDED"


def test_cpu_and_gpu_probes_merge_without_false_stale_on_unprobed_device(
    harness: Any, monkeypatch: Any
) -> None:
    import threading
    from dataclasses import replace

    from photo_search.catalog.db import Catalog
    from photo_search.runtime import device as runtime_device

    actual = runtime_device.runtime_capabilities()
    monkeypatch.setattr(runtime_device, "runtime_capabilities", lambda: replace(
        actual, providers=("CPUExecutionProvider", "CUDAExecutionProvider"),
    ))
    gpu_entered = threading.Event()
    release_gpu = threading.Event()
    outcomes = []
    class Cache:
        def engine(self, _root, *, resolved_device, **_kwargs):
            if resolved_device == "gpu":
                gpu_entered.set()
                assert release_gpu.wait(5)
            return FakeEngine(resolved_device, failure=None, confidence=0.9)

    started_at = harness.clock()
    def gpu_worker():
        try:
            with Catalog.open(data_root=harness.data_root, clock=harness.clock) as catalog:
                scheduler = ocr_service.build_scheduler(catalog)
                outcomes.append(ocr_service.record_runtime_evidence(
                    scheduler, scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID), Cache(),
                    data_root=harness.data_root, now_ms=started_at, note="gpu", devices=("gpu",),
                ))
        except Exception as error:
            outcomes.append(error)
    thread = threading.Thread(target=gpu_worker, daemon=True)
    thread.start()
    try:
        assert gpu_entered.wait(3)
        harness.clock.advance(10)
        cpu_scheduler = ocr_service.build_scheduler(harness.catalog)
        ocr_service.record_runtime_evidence(
            cpu_scheduler, cpu_scheduler.spec_for(contract.OCR_CPU_PROCESSOR_ID), Cache(),
            data_root=harness.data_root, now_ms=harness.clock(), note="cpu", devices=("cpu",),
        )
        assert cpu_scheduler.latest_capability().providers_for("gpu")[0].health == "unknown"
        release_gpu.set()
        thread.join(timeout=3)
        assert not thread.is_alive()
        assert len(outcomes) == 1 and isinstance(outcomes[0], dict)
        current = cpu_scheduler.latest_capability()
        assert current.providers_for("gpu")[0].health == "healthy"
        assert current.providers_for("cpu")[0].health == "healthy"
        assert current.providers_for("gpu")[0].health_checked_at == started_at
        assert current.providers_for("cpu")[0].health_checked_at == harness.clock()
    finally:
        release_gpu.set()
        thread.join(timeout=3)


def test_gpu_wait_timeout_leaves_auto_job_unclaimed_without_fallback(
    harness: Any, monkeypatch: Any
) -> None:
    import threading

    from photo_search.face import service as face_service

    asset_id = make_asset(harness, "gpu-timeout.jpg")
    enqueue(harness, asset_id, device="auto")
    install_runtime_evidence(monkeypatch)
    lock = threading.RLock()
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", lock)
    monkeypatch.setattr(ocr_service, "OCR_GPU_WAIT_SECONDS", 0.05)
    held = threading.Event()
    release = threading.Event()
    def holder():
        with lock:
            held.set()
            assert release.wait(3)
    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    try:
        assert held.wait(2)
        result = ocr_service.run_queue(
            harness.catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
        )
        assert result["waiting_gpu"] is True and result["steps"] == 0
        rows = jobs_for(harness, asset_id)
        gpu = rows[contract.OCR_GPU_PROCESSOR_ID]
        assert gpu["state"] == "QUEUED" and gpu["attempts"] == 0 and gpu["lease_token"] is None
        assert contract.OCR_CPU_PROCESSOR_ID not in rows
    finally:
        release.set()
        thread.join(timeout=2)


def test_long_ocr_execution_renews_lease_and_publishes_once(
    harness: Any, monkeypatch: Any
) -> None:
    import threading
    import time

    from photo_search.catalog.db import Catalog

    asset_id = make_asset(harness, "long-ocr.jpg")
    enqueue(harness, asset_id, device="gpu")
    install_runtime_evidence(monkeypatch)
    real_factory = ocr_service.ocr_executor
    entered = threading.Event()
    release = threading.Event()
    outcomes = []
    def blocking_factory(*args, **kwargs):
        executor = real_factory(*args, **kwargs)
        def execute(request):
            payload = executor(request)
            entered.set()
            assert release.wait(6)
            return payload
        return execute
    monkeypatch.setattr(ocr_service, "ocr_executor", blocking_factory)
    def worker():
        try:
            with Catalog.open(data_root=harness.data_root, clock=harness.clock, lease_seconds=1) as catalog:
                outcomes.append(ocr_service.run_queue(
                    catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
                ))
        except Exception as error:
            outcomes.append(error)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert entered.wait(3)
        first_expiry = int(jobs_for(harness, asset_id)[contract.OCR_GPU_PROCESSOR_ID]["lease_expires_at"])
        for _ in range(3):
            harness.clock.advance(400)
            target = harness.clock() + 1000
            deadline = time.monotonic() + 2
            while True:
                row = jobs_for(harness, asset_id)[contract.OCR_GPU_PROCESSOR_ID]
                if int(row["lease_expires_at"]) >= target:
                    break
                assert time.monotonic() < deadline, "heartbeat did not extend the active lease"
                threading.Event().wait(0.02)
        assert harness.clock() > first_expiry
        release.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert len(outcomes) == 1 and isinstance(outcomes[0], dict)
        assert outcomes[0]["steps"] == 1
        assert jobs_for(harness, asset_id)[contract.OCR_GPU_PROCESSOR_ID]["state"] == "SUCCEEDED"
        assert harness.ocr_document(asset_id) is not None
    finally:
        release.set()
        thread.join(timeout=5)


def test_scoped_claim_does_not_requeue_or_exhaust_other_assets(
    harness: Any, monkeypatch: Any
) -> None:
    from photo_search.scheduler.engine import Scheduler

    first = make_asset(harness, "scope-claim-a.jpg")
    for suffix, width in (("b", 241), ("c", 242)):
        Image.new("RGB", (width, 120), "white").save(harness.library / f"scope-claim-{suffix}.jpg", "JPEG")
    harness.scan()
    second = harness.asset_id("scope-claim-b.jpg")
    third = harness.asset_id("scope-claim-c.jpg")
    for asset in (first, second, third):
        enqueue(harness, asset, device="cpu")
    ids = {asset: int(jobs_for(harness, asset)[contract.OCR_CPU_PROCESSOR_ID]["job_id"])
           for asset in (first, second, third)}
    with harness.catalog.write() as connection:
        connection.execute(
            "UPDATE jobs SET state='RETRY_WAIT', run_after=? WHERE job_id=?",
            (harness.clock() - 1, ids[second]),
        )
        connection.execute(
            "UPDATE jobs SET state='RUNNING', worker_id='old', lease_token='old-token', "
            "lease_expires_at=?, attempts=max_attempts-1 WHERE job_id=?",
            (harness.clock() - 1, ids[third]),
        )
    before_second = dict(jobs_for(harness, second)[contract.OCR_CPU_PROCESSOR_ID])
    before_third = dict(jobs_for(harness, third)[contract.OCR_CPU_PROCESSOR_ID])
    install_runtime_evidence(monkeypatch)
    base = ocr_service.build_scheduler(harness.catalog)
    ocr_service.record_runtime_evidence(
        base, base.spec_for(contract.OCR_CPU_PROCESSOR_ID), FakeEngineCache(),
        data_root=harness.data_root, now_ms=harness.clock(), note="scoped",
    )
    scoped = Scheduler(
        harness.catalog, [base.spec_for(contract.OCR_CPU_PROCESSOR_ID)], job_ids=[ids[first]],
    )
    request = scoped.claim_next(worker_id="scoped", now_ms=harness.clock())
    assert request is not None and request.job_id == ids[first]
    assert dict(jobs_for(harness, second)[contract.OCR_CPU_PROCESSOR_ID]) == before_second
    assert dict(jobs_for(harness, third)[contract.OCR_CPU_PROCESSOR_ID]) == before_third


def test_late_ocr_worker_cannot_overwrite_reclaimed_job(
    harness: Any, monkeypatch: Any
) -> None:
    import threading

    from photo_search.catalog.db import Catalog
    from photo_search.ocr.lease_heartbeat import OcrLeaseHeartbeat

    asset_id = make_asset(harness, "late-takeover.jpg")
    enqueue(harness, asset_id, device="gpu")
    install_runtime_evidence(monkeypatch)
    # Fault injection: the heartbeat worker is lost while inference is in flight.
    monkeypatch.setattr(OcrLeaseHeartbeat, "_loop", lambda _self: None)
    real_factory = ocr_service.ocr_executor
    entered = threading.Event()
    release = threading.Event()
    outcomes = []
    def blocking_factory(*args, **kwargs):
        executor = real_factory(*args, **kwargs)
        def execute(request):
            payload = executor(request)
            entered.set()
            assert release.wait(5)
            return payload
        return execute
    monkeypatch.setattr(ocr_service, "ocr_executor", blocking_factory)
    def worker():
        try:
            with Catalog.open(data_root=harness.data_root, clock=harness.clock, lease_seconds=1) as catalog:
                outcomes.append(ocr_service.run_queue(
                    catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
                ))
        except Exception as error:
            outcomes.append(error)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert entered.wait(3)
        old = jobs_for(harness, asset_id)[contract.OCR_GPU_PROCESSOR_ID]
        old_token = old["lease_token"]
        harness.clock.advance(1001)
        scheduler = ocr_service.build_scheduler(harness.catalog)
        # Simulate a non-cooperating replacement to exercise stale-lease refusal.
        with monkeypatch.context() as fault:
            fault.setattr(
                "photo_search.runtime.background_slot.background_slot_owned_by_current_thread",
                lambda: True,
            )
            request = scheduler.claim_next(
                worker_id="replacement", now_ms=harness.clock(), _ocr_gpu_managed=True,
            )
        assert request is not None and request.lease_token != old_token
        release.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert len(outcomes) == 1 and isinstance(outcomes[0], CatalogError)
        assert outcomes[0].code == STALE_LEASE
        current = jobs_for(harness, asset_id)[contract.OCR_GPU_PROCESSOR_ID]
        assert current["state"] == "RUNNING" and current["lease_token"] == request.lease_token
        assert harness.ocr_document(asset_id) is None
    finally:
        release.set()
        thread.join(timeout=5)


def test_short_ocr_execution_does_not_write_heartbeat_per_job(
    harness: Any, monkeypatch: Any
) -> None:
    asset_id = make_asset(harness, "fast-ocr.jpg")
    enqueue(harness, asset_id, device="cpu")
    install_runtime_evidence(monkeypatch)
    calls = []
    real_heartbeat = jobs.heartbeat
    def counted(*args, **kwargs):
        calls.append(1)
        return real_heartbeat(*args, **kwargs)
    monkeypatch.setattr(jobs, "heartbeat", counted)
    result = ocr_service.run_queue(
        harness.catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
    )
    assert result["steps"] == 1
    assert calls == []
    assert jobs_for(harness, asset_id)[contract.OCR_CPU_PROCESSOR_ID]["state"] == "SUCCEEDED"


def test_heartbeat_delayed_until_after_success_cannot_revive_finished_job(
    harness: Any, monkeypatch: Any
) -> None:
    import threading
    from contextlib import contextmanager

    from photo_search.catalog.db import Catalog

    asset_id = make_asset(harness, "late-heartbeat-success.jpg")
    enqueue(harness, asset_id, device="gpu")
    install_runtime_evidence(monkeypatch)
    real_factory = ocr_service.ocr_executor
    real_write = Catalog.write
    entered = threading.Event()
    release_inference = threading.Event()
    heartbeat_waiting = threading.Event()
    release_heartbeat = threading.Event()
    outcomes = []
    def blocking_factory(*args, **kwargs):
        executor = real_factory(*args, **kwargs)
        def execute(request):
            payload = executor(request)
            entered.set()
            assert release_inference.wait(5)
            return payload
        return execute
    @contextmanager
    def delayed_write(catalog):
        if threading.current_thread().name == "ocr-lease-heartbeat":
            heartbeat_waiting.set()
            assert release_heartbeat.wait(5)
        with real_write(catalog) as connection:
            yield connection
    monkeypatch.setattr(ocr_service, "ocr_executor", blocking_factory)
    monkeypatch.setattr(Catalog, "write", delayed_write)
    def worker():
        try:
            with Catalog.open(data_root=harness.data_root, clock=harness.clock, lease_seconds=1) as catalog:
                outcomes.append(ocr_service.run_queue(
                    catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
                ))
        except Exception as error:
            outcomes.append(error)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert entered.wait(3)
        harness.clock.advance(400)
        assert heartbeat_waiting.wait(2)
        release_inference.set()
        import time
        deadline = time.monotonic() + 3
        while jobs_for(harness, asset_id)[contract.OCR_GPU_PROCESSOR_ID]["state"] != "SUCCEEDED":
            assert time.monotonic() < deadline
            threading.Event().wait(0.02)
        assert thread.is_alive()
        release_heartbeat.set()
        thread.join(timeout=3)
        assert not thread.is_alive() and len(outcomes) == 1 and isinstance(outcomes[0], dict)
        row = jobs_for(harness, asset_id)[contract.OCR_GPU_PROCESSOR_ID]
        assert row["state"] == "SUCCEEDED" and row["lease_token"] is None
    finally:
        release_inference.set()
        release_heartbeat.set()
        thread.join(timeout=5)


def test_ocr_recovers_after_thirty_second_sqlite_writer_lock(
    harness: Any, monkeypatch: Any
) -> None:
    import sqlite3
    import threading
    import time
    import uuid

    from photo_search.catalog.db import Catalog
    from photo_search.face import service as face_service
    from photo_search.runtime.machine_model_slot import MachineModelSlot

    asset_id = make_asset(harness, "ocr-long-writer-lock.jpg")
    enqueue(harness, asset_id, device="gpu")
    install_runtime_evidence(monkeypatch)
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", MachineModelSlot(
        "Global\\PhotoSearch.Test." + uuid.uuid4().hex,
    ))
    real_factory = ocr_service.ocr_executor
    entered = threading.Event()
    release_inference = threading.Event()
    outcomes = []
    def blocking_factory(*args, **kwargs):
        executor = real_factory(*args, **kwargs)
        def execute(request):
            payload = executor(request)
            entered.set()
            assert release_inference.wait(45)
            return payload
        return execute
    monkeypatch.setattr(ocr_service, "ocr_executor", blocking_factory)
    def worker():
        try:
            with Catalog.open(data_root=harness.data_root, clock=harness.clock, lease_seconds=1) as catalog:
                outcomes.append(ocr_service.run_queue(
                    catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
                ))
        except Exception as error:
            outcomes.append(error)
    thread = threading.Thread(target=worker, daemon=True)
    external = sqlite3.connect(harness.catalog.db_path, isolation_level=None, timeout=1)
    thread.start()
    try:
        assert entered.wait(3)
        external.execute("BEGIN IMMEDIATE")
        harness.clock.advance(1001)
        started = time.monotonic()
        threading.Event().wait(30.0)
        external.execute("COMMIT")
        assert time.monotonic() - started >= 30.0
        release_inference.set()
        thread.join(timeout=20)
        assert not thread.is_alive()
        assert len(outcomes) == 1 and isinstance(outcomes[0], CatalogError)
        assert outcomes[0].code == STALE_LEASE
        old = jobs_for(harness, asset_id)[contract.OCR_GPU_PROCESSOR_ID]
        assert old["state"] == "RUNNING"
        assert harness.ocr_document(asset_id) is None
        # Restore the unblocked executor and resume the same persisted job.
        monkeypatch.setattr(ocr_service, "ocr_executor", real_factory)
        resumed = ocr_service.run_queue(
            harness.catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
        )
        assert resumed["steps"] == 1
        current = jobs_for(harness, asset_id)[contract.OCR_GPU_PROCESSOR_ID]
        assert current["job_id"] == old["job_id"] and current["state"] == "SUCCEEDED"
        assert harness.ocr_document(asset_id) is not None
    finally:
        release_inference.set()
        if external.in_transaction:
            external.execute("ROLLBACK")
        external.close()
        thread.join(timeout=20)


def test_delayed_heartbeat_cannot_renew_a_reclaimed_ocr_job(
    harness: Any, monkeypatch: Any
) -> None:
    import threading
    from contextlib import contextmanager

    from photo_search.catalog.db import Catalog

    asset_id = make_asset(harness, "late-heartbeat-takeover.jpg")
    enqueue(harness, asset_id, device="gpu")
    install_runtime_evidence(monkeypatch)
    real_factory = ocr_service.ocr_executor
    real_write = Catalog.write
    entered = threading.Event()
    release_inference = threading.Event()
    heartbeat_waiting = threading.Event()
    release_heartbeat = threading.Event()
    outcomes = []
    def blocking_factory(*args, **kwargs):
        executor = real_factory(*args, **kwargs)
        def execute(request):
            payload = executor(request)
            entered.set()
            assert release_inference.wait(5)
            return payload
        return execute
    @contextmanager
    def delayed_write(catalog):
        if threading.current_thread().name == "ocr-lease-heartbeat":
            heartbeat_waiting.set()
            assert release_heartbeat.wait(5)
        with real_write(catalog) as connection:
            yield connection
    monkeypatch.setattr(ocr_service, "ocr_executor", blocking_factory)
    monkeypatch.setattr(Catalog, "write", delayed_write)
    def worker():
        try:
            with Catalog.open(data_root=harness.data_root, clock=harness.clock, lease_seconds=1) as catalog:
                outcomes.append(ocr_service.run_queue(
                    catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
                ))
        except Exception as error:
            outcomes.append(error)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert entered.wait(3)
        assert heartbeat_waiting.wait(2)
        old = jobs_for(harness, asset_id)[contract.OCR_GPU_PROCESSOR_ID]
        harness.clock.advance(1001)
        scheduler = ocr_service.build_scheduler(harness.catalog)
        # Simulate a non-cooperating replacement to exercise stale-lease refusal.
        with monkeypatch.context() as fault:
            fault.setattr(
                "photo_search.runtime.background_slot.background_slot_owned_by_current_thread",
                lambda: True,
            )
            request = scheduler.claim_next(
                worker_id="replacement", now_ms=harness.clock(), _ocr_gpu_managed=True,
            )
        assert request is not None and request.lease_token != old["lease_token"]
        release_heartbeat.set()
        release_inference.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert len(outcomes) == 1 and isinstance(outcomes[0], CatalogError)
        assert outcomes[0].code == STALE_LEASE
        current = jobs_for(harness, asset_id)[contract.OCR_GPU_PROCESSOR_ID]
        assert current["state"] == "RUNNING" and current["lease_token"] == request.lease_token
        assert harness.ocr_document(asset_id) is None
    finally:
        release_inference.set()
        release_heartbeat.set()
        thread.join(timeout=5)


def test_cpu_job_completes_while_gpu_job_waits_then_cancel_preserves_gpu(
    harness: Any, monkeypatch: Any
) -> None:
    import threading
    import time

    from photo_search.catalog.db import Catalog
    from photo_search.face import service as face_service

    cpu_asset = make_asset(harness, "mixed-cpu.jpg")
    Image.new("RGB", (241, 120), "white").save(harness.library / "mixed-gpu.jpg", "JPEG")
    harness.scan()
    gpu_asset = harness.asset_id("mixed-gpu.jpg")
    enqueue(harness, cpu_asset, device="cpu")
    enqueue(harness, gpu_asset, device="gpu")
    install_runtime_evidence(monkeypatch)
    lock = threading.RLock()
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", lock)
    held = threading.Event()
    release = threading.Event()
    cancel = threading.Event()
    outcomes = []
    def holder():
        with lock:
            held.set()
            assert release.wait(5)
    def worker():
        try:
            with Catalog.open(data_root=harness.data_root) as catalog:
                outcomes.append(ocr_service.run_queue(
                    catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
                    stop_check=cancel.is_set,
                ))
        except Exception as error:
            outcomes.append(error)
    first = threading.Thread(target=holder, daemon=True)
    second = threading.Thread(target=worker, daemon=True)
    first.start()
    try:
        assert held.wait(2)
        second.start()
        deadline = time.monotonic() + 3
        while jobs_for(harness, cpu_asset)[contract.OCR_CPU_PROCESSOR_ID]["state"] != "SUCCEEDED":
            assert time.monotonic() < deadline
            threading.Event().wait(0.02)
        gpu = jobs_for(harness, gpu_asset)[contract.OCR_GPU_PROCESSOR_ID]
        assert gpu["state"] == "QUEUED" and gpu["lease_token"] is None
        assert not lock.acquire(blocking=False)
        cancel.set()
        second.join(timeout=3)
        assert not second.is_alive() and len(outcomes) == 1 and isinstance(outcomes[0], dict)
        assert outcomes[0]["steps"] == 1
        assert jobs_for(harness, gpu_asset)[contract.OCR_GPU_PROCESSOR_ID]["state"] == "QUEUED"
    finally:
        cancel.set()
        release.set()
        first.join(timeout=2)
        if second.ident is not None:
            second.join(timeout=3)


@pytest.mark.parametrize("owner", ["face", "semantic-build"])
def test_gpu_ocr_waits_for_existing_background_model_work(
    harness: Any, monkeypatch: Any, owner: str,
) -> None:
    import threading

    from photo_search.catalog.db import Catalog
    from photo_search.face import service as face_service
    from photo_search.refresh import pipeline

    asset_id = make_asset(harness, f"gpu-vs-{owner}.jpg")
    enqueue(harness, asset_id, device="gpu")
    install_runtime_evidence(monkeypatch)
    lock = threading.RLock()
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", lock)
    owner_entered = threading.Event()
    release_owner = threading.Event()
    ocr_entered = threading.Event()
    outcomes = []
    real_factory = ocr_service.ocr_executor
    def observed_factory(*args, **kwargs):
        executor = real_factory(*args, **kwargs)
        def execute(request):
            ocr_entered.set()
            return executor(request)
        return execute
    monkeypatch.setattr(ocr_service, "ocr_executor", observed_factory)
    @face_service._serialize_face_work
    def face_operation(_catalog):
        owner_entered.set()
        assert release_owner.wait(5)
        return {"steps": 0}
    def hold_owner():
        if owner == "face":
            face_operation(None)
        else:
            with pipeline._background_model_slot():
                owner_entered.set()
                assert release_owner.wait(5)
    def run_ocr():
        try:
            with Catalog.open(data_root=harness.data_root) as catalog:
                outcomes.append(ocr_service.run_queue(
                    catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
                ))
        except Exception as error:
            outcomes.append(error)
    first = threading.Thread(target=hold_owner, daemon=True)
    second = threading.Thread(target=run_ocr, daemon=True)
    first.start()
    try:
        assert owner_entered.wait(2)
        second.start()
        assert not ocr_entered.wait(0.2)
        release_owner.set()
        second.join(timeout=5)
        assert not second.is_alive()
        assert ocr_entered.is_set()
        assert len(outcomes) == 1 and isinstance(outcomes[0], dict)
        assert outcomes[0]["steps"] == 1
    finally:
        release_owner.set()
        first.join(timeout=2)
        if second.ident is not None:
            second.join(timeout=5)


def test_gpu_drain_reuses_probe_for_bounded_batches(harness: Any, monkeypatch: Any) -> None:
    for index in range(9):
        Image.new("RGB", (240 + index, 120), "white").save(
            harness.library / f"gpu-batch-{index}.jpg", "JPEG",
        )
    harness.scan()
    assets = [harness.asset_id(f"gpu-batch-{index}.jpg") for index in range(9)]
    for asset_id in assets:
        enqueue(harness, asset_id, device="gpu")
    install_runtime_evidence(monkeypatch)
    original = ocr_service.record_runtime_evidence
    probes = []
    def recorded(*args, **kwargs):
        probes.append(kwargs.get("devices"))
        return original(*args, **kwargs)
    monkeypatch.setattr(ocr_service, "record_runtime_evidence", recorded)
    result = ocr_service.run_queue(
        harness.catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
    )
    assert result["steps"] == 9
    assert probes == [("gpu",), ("gpu",)]
    assert all(jobs_for(harness, asset)[contract.OCR_GPU_PROCESSOR_ID]["state"] == "SUCCEEDED"
               for asset in assets)


def test_gpu_cancel_after_first_job_does_not_claim_rest_of_batch(
    harness: Any, monkeypatch: Any
) -> None:
    import threading

    for index in range(3):
        Image.new("RGB", (240 + index, 120), "white").save(
            harness.library / f"cancel-batch-{index}.jpg", "JPEG",
        )
    harness.scan()
    assets = [harness.asset_id(f"cancel-batch-{index}.jpg") for index in range(3)]
    for asset_id in assets:
        enqueue(harness, asset_id, device="gpu")
    install_runtime_evidence(monkeypatch)
    real_factory = ocr_service.ocr_executor
    cancelled = threading.Event()
    def cancelling_factory(*args, **kwargs):
        executor = real_factory(*args, **kwargs)
        def execute(request):
            result = executor(request)
            cancelled.set()
            return result
        return execute
    monkeypatch.setattr(ocr_service, "ocr_executor", cancelling_factory)
    result = ocr_service.run_queue(
        harness.catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
        cancel=cancelled.is_set,
    )
    assert result["steps"] == 1 and result["stopped"] is True
    states = [jobs_for(harness, asset)[contract.OCR_GPU_PROCESSOR_ID]["state"] for asset in assets]
    assert states.count("SUCCEEDED") == 1
    assert states.count("QUEUED") == 2
    assert "CANCELLED" not in states


def test_cpu_probe_preserves_legacy_semantic_windows_gpu_observation(
    harness: Any, monkeypatch: Any
) -> None:
    from dataclasses import replace

    from photo_search.runtime import device as runtime_device

    actual = runtime_device.runtime_capabilities()
    monkeypatch.setattr(runtime_device, "runtime_capabilities", lambda: replace(
        actual, providers=("CPUExecutionProvider", "CUDAExecutionProvider"),
        platform="Windows-11-10.0.26300-SP0",
    ))
    from photo_search.scheduler import capability as capability_module

    monkeypatch.setattr(capability_module.sys, "platform", "win32")
    monkeypatch.setattr(capability_module.platform, "machine", lambda: "AMD64")
    scheduler = ocr_service.build_scheduler(harness.catalog)
    spec = scheduler.spec_for(contract.OCR_CPU_PROCESSOR_ID)
    class Cache:
        def engine(self, _root, *, resolved_device, **_kwargs):
            return FakeEngine(resolved_device, failure=None, confidence=0.9)
    ocr_service.record_runtime_evidence(
        scheduler, spec, Cache(), data_root=harness.data_root, now_ms=harness.clock(), note="original",
    )
    old = scheduler.latest_capability()
    legacy = dict(old.envelope)
    legacy["platform"] = "windows-x86_64"
    harness.clock.advance(1)
    scheduler.record_capability(RuntimeCapabilities.seal(legacy), now_ms=harness.clock())
    old_gpu = scheduler.latest_capability().providers_for("gpu")[0].to_envelope()
    harness.clock.advance(1)
    ocr_service.record_runtime_evidence(
        scheduler, spec, Cache(), data_root=harness.data_root,
        now_ms=harness.clock(), note="cpu", devices=("cpu",),
    )
    new = scheduler.latest_capability()
    assert new.providers_for("gpu")[0].to_envelope() == old_gpu
    assert new.health_status == "healthy"


def test_foreground_query_enters_between_gpu_ocr_jobs(harness: Any, monkeypatch: Any) -> None:
    import threading
    import time

    from photo_search.catalog.db import Catalog
    from photo_search.face import service as face_service
    from photo_search.runtime.background_slot import foreground_model_waiting
    from photo_search.runtime.model_resource_gate import GatedEncoder, ModelResourceGate

    for index in range(2):
        Image.new("RGB", (200 + index, 120), "white").save(
            harness.library / f"foreground-boundary-{index}.jpg", "JPEG",
        )
    harness.scan()
    for index in range(2):
        enqueue(harness, harness.asset_id(f"foreground-boundary-{index}.jpg"), device="gpu")
    install_runtime_evidence(monkeypatch)
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", threading.RLock())
    first_entered, release_first = threading.Event(), threading.Event()
    second_entered, query_entered, release_query = threading.Event(), threading.Event(), threading.Event()
    outcomes = []
    real_factory = ocr_service.ocr_executor
    calls = []
    def delayed_factory(*args, **kwargs):
        executor = real_factory(*args, **kwargs)
        def execute(request):
            calls.append(1)
            if len(calls) == 1:
                first_entered.set()
                assert release_first.wait(5)
            else:
                second_entered.set()
            return executor(request)
        return execute
    monkeypatch.setattr(ocr_service, "ocr_executor", delayed_factory)

    class Encoder:
        requires_gpu_slot = True
        def encode_queries(self, texts, cancellation=None):
            query_entered.set()
            assert release_query.wait(5)
            return texts
    query = GatedEncoder(Encoder(), ModelResourceGate())
    def run_ocr():
        try:
            with Catalog.open(data_root=harness.data_root) as catalog:
                outcomes.append(ocr_service.run_queue(
                    catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
                ))
        except Exception as error:
            outcomes.append(error)
    thread = threading.Thread(target=run_ocr, daemon=True)
    query_thread = threading.Thread(target=lambda: query.encode_queries(["x"]), daemon=True)
    thread.start()
    try:
        assert first_entered.wait(3), outcomes
        query_thread.start()
        deadline = time.monotonic() + 3
        while not foreground_model_waiting() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert foreground_model_waiting()
        assert not query_entered.is_set()
        release_first.set()
        assert query_entered.wait(3)
        assert not second_entered.is_set()
        release_query.set()
        query_thread.join(3)
        thread.join(5)
        assert not query_thread.is_alive() and not thread.is_alive()
        assert len(outcomes) == 1 and isinstance(outcomes[0], dict)
        assert outcomes[0]["steps"] == 2
        assert second_entered.is_set()
    finally:
        release_first.set()
        release_query.set()
        thread.join(5)
        if query_thread.ident is not None:
            query_thread.join(5)


def test_foreground_during_gpu_probe_does_not_report_false_completion(
    harness: Any, monkeypatch: Any
) -> None:
    import threading
    import time

    from photo_search.catalog.db import Catalog
    from photo_search.face import service as face_service
    from photo_search.runtime.background_slot import foreground_model_slot, foreground_model_waiting

    asset = make_asset(harness, "query-during-probe.jpg")
    enqueue(harness, asset, device="gpu")
    install_runtime_evidence(monkeypatch)
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", threading.RLock())
    probe_entered, release_probe, query_entered = threading.Event(), threading.Event(), threading.Event()
    outcomes = []
    original = ocr_service.record_runtime_evidence
    calls = []
    def paused_probe(*args, **kwargs):
        evidence = original(*args, **kwargs)
        calls.append(1)
        if len(calls) == 1:
            probe_entered.set()
            assert release_probe.wait(5)
        return evidence
    monkeypatch.setattr(ocr_service, "record_runtime_evidence", paused_probe)
    def run_ocr():
        try:
            with Catalog.open(data_root=harness.data_root) as catalog:
                outcomes.append(ocr_service.run_queue(
                    catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
                ))
        except Exception as error:
            outcomes.append(error)
    def query():
        with foreground_model_slot():
            query_entered.set()
    worker = threading.Thread(target=run_ocr, daemon=True)
    frontend = threading.Thread(target=query, daemon=True)
    worker.start()
    try:
        assert probe_entered.wait(3), outcomes
        frontend.start()
        deadline = time.monotonic() + 3
        while not foreground_model_waiting() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert foreground_model_waiting()
        release_probe.set()
        assert query_entered.wait(3)
        frontend.join(3)
        worker.join(5)
        assert not worker.is_alive()
        assert len(outcomes) == 1 and isinstance(outcomes[0], dict)
        assert outcomes[0]["steps"] == 1
        assert jobs_for(harness, asset)[contract.OCR_GPU_PROCESSOR_ID]["state"] == "SUCCEEDED"
    finally:
        release_probe.set()
        worker.join(5)
        if frontend.ident is not None:
            frontend.join(5)


def test_conflicting_equal_timestamp_gpu_observation_is_refused(harness: Any, monkeypatch: Any) -> None:
    from dataclasses import replace

    from photo_search.runtime import device as runtime_device

    actual = runtime_device.runtime_capabilities()
    monkeypatch.setattr(runtime_device, "runtime_capabilities", lambda: replace(
        actual, providers=("CPUExecutionProvider", "CUDAExecutionProvider"),
    ))
    scheduler = ocr_service.build_scheduler(harness.catalog)
    spec = scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID)
    ocr_service.record_runtime_evidence(
        scheduler, spec, FakeEngineCache(), data_root=harness.data_root,
        now_ms=harness.clock(), note="healthy", devices=("gpu",),
    )
    previous = scheduler.latest_capability()
    facts = dict(previous.envelope)
    facts["providers"] = [dict(provider) for provider in facts["providers"]]
    gpu = next(provider for provider in facts["providers"] if provider["device"] == "gpu")
    gpu["health"] = "unhealthy"
    gpu["available"] = False
    conflicting = RuntimeCapabilities.seal(facts)
    with pytest.raises(CatalogError) as error:
        scheduler.record_observation(
            conflicting, observed_devices=("gpu",), now_ms=harness.clock(),
        )
    assert error.value.code == "RUNTIME_CAPABILITY_OBSERVATION_STALE"
    assert scheduler.latest_capability().capability_revision == previous.capability_revision


def test_older_runtime_identity_cannot_replace_newer_observation(harness: Any, monkeypatch: Any) -> None:
    from dataclasses import replace

    from photo_search.runtime import device as runtime_device

    actual = runtime_device.runtime_capabilities()
    monkeypatch.setattr(runtime_device, "runtime_capabilities", lambda: replace(
        actual, providers=("CPUExecutionProvider", "CUDAExecutionProvider"),
    ))
    scheduler = ocr_service.build_scheduler(harness.catalog)
    spec = scheduler.spec_for(contract.OCR_CPU_PROCESSOR_ID)
    ocr_service.record_runtime_evidence(
        scheduler, spec, FakeEngineCache(), data_root=harness.data_root,
        now_ms=harness.clock(), note="new", devices=("cpu",),
    )
    previous = scheduler.latest_capability()
    facts = dict(previous.envelope)
    facts["platform"] = "different-older-platform"
    facts["measured_at"] -= 1
    older = RuntimeCapabilities.seal(facts)
    with pytest.raises(CatalogError) as error:
        scheduler.record_observation(older, observed_devices=("cpu",), now_ms=harness.clock())
    assert error.value.code == "RUNTIME_CAPABILITY_OBSERVATION_STALE"
    assert scheduler.latest_capability().capability_revision == previous.capability_revision


def test_rolled_back_recording_clock_does_not_hide_new_snapshot(harness: Any, monkeypatch: Any) -> None:
    from dataclasses import replace

    from photo_search.runtime import device as runtime_device

    actual = runtime_device.runtime_capabilities()
    monkeypatch.setattr(runtime_device, "runtime_capabilities", lambda: replace(
        actual, providers=("CPUExecutionProvider", "CUDAExecutionProvider"),
    ))
    scheduler = ocr_service.build_scheduler(harness.catalog)
    spec = scheduler.spec_for(contract.OCR_CPU_PROCESSOR_ID)
    ocr_service.record_runtime_evidence(
        scheduler, spec, FakeEngineCache(), data_root=harness.data_root,
        now_ms=harness.clock(), note="current", devices=("cpu",),
    )
    previous = scheduler.latest_capability()
    facts = dict(previous.envelope)
    facts["measured_at"] += 1
    facts["health_checked_at"] += 1
    facts["providers"] = [dict(provider) for provider in facts["providers"]]
    for provider in facts["providers"]:
        if provider["device"] == "cpu":
            provider["health_checked_at"] += 1
    newer = RuntimeCapabilities.seal(facts)
    with pytest.raises(CatalogError) as error:
        scheduler.record_observation(newer, observed_devices=("cpu",), now_ms=harness.clock() - 1)
    assert error.value.code == "RUNTIME_CAPABILITY_OBSERVATION_STALE"
    assert scheduler.latest_capability().capability_revision == previous.capability_revision


def test_identity_change_cannot_override_newer_preserved_device(
    harness: Any, monkeypatch: Any
) -> None:
    from dataclasses import replace

    from photo_search.runtime import device as runtime_device

    actual = runtime_device.runtime_capabilities()
    monkeypatch.setattr(runtime_device, "runtime_capabilities", lambda: replace(
        actual, providers=("CPUExecutionProvider", "CUDAExecutionProvider"),
    ))
    scheduler = ocr_service.build_scheduler(harness.catalog)
    spec = scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID)
    start = harness.clock()
    ocr_service.record_runtime_evidence(
        scheduler, spec, FakeEngineCache(), data_root=harness.data_root,
        now_ms=start + 20, note="gpu newer", devices=("gpu",),
    )
    ocr_service.record_runtime_evidence(
        scheduler, spec, FakeEngineCache(), data_root=harness.data_root,
        now_ms=start + 10, note="cpu slower", devices=("cpu",),
    )
    previous = scheduler.latest_capability()
    assert previous.measured_at == start + 20
    assert previous.providers_for("gpu")[0].health_checked_at == start + 20
    facts = dict(previous.envelope)
    facts["platform"] = "obsolete-runtime"
    facts["measured_at"] = start + 15
    obsolete = RuntimeCapabilities.seal(facts)
    with pytest.raises(CatalogError) as error:
        scheduler.record_observation(obsolete, observed_devices=("cpu",), now_ms=start + 30)
    assert error.value.code == "RUNTIME_CAPABILITY_OBSERVATION_STALE"
    assert scheduler.latest_capability().capability_revision == previous.capability_revision


def test_equal_timestamp_precision_conflict_rejected_but_identical_snapshot_is_idempotent(
    harness: Any, monkeypatch: Any
) -> None:
    from dataclasses import replace

    from photo_search.runtime import device as runtime_device

    actual = runtime_device.runtime_capabilities()
    monkeypatch.setattr(runtime_device, "runtime_capabilities", lambda: replace(
        actual, providers=("CPUExecutionProvider", "CUDAExecutionProvider"),
    ))
    scheduler = ocr_service.build_scheduler(harness.catalog)
    spec = scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID)
    ocr_service.record_runtime_evidence(
        scheduler, spec, FakeEngineCache(), data_root=harness.data_root,
        now_ms=harness.clock(), note="gpu", devices=("gpu",),
    )
    previous = scheduler.latest_capability()
    same = scheduler.record_observation(previous, observed_devices=("gpu",), now_ms=harness.clock())
    assert same.capability_revision == previous.capability_revision
    facts = dict(previous.envelope)
    facts["providers"] = [dict(provider) for provider in facts["providers"]]
    gpu = next(provider for provider in facts["providers"] if provider["device"] == "gpu")
    gpu["precisions"] = ["FP16"]
    with pytest.raises(CatalogError) as error:
        scheduler.record_observation(
            RuntimeCapabilities.seal(facts), observed_devices=("gpu",), now_ms=harness.clock(),
        )
    assert error.value.code == "RUNTIME_CAPABILITY_OBSERVATION_STALE"
    assert scheduler.latest_capability().capability_revision == previous.capability_revision


@pytest.mark.parametrize(
    ("cpu_health", "gpu_health", "expected"),
    [("healthy", "unknown", "healthy"), ("healthy", "healthy", "healthy"),
     ("healthy", "unhealthy", "degraded"), ("unhealthy", "unhealthy", "unhealthy"),
     ("unknown", "unknown", "unknown")],
)
def test_merged_runtime_health_reflects_provider_facts(
    harness: Any, monkeypatch: Any, cpu_health: str, gpu_health: str, expected: str
) -> None:
    from dataclasses import replace

    from photo_search.runtime import device as runtime_device
    from photo_search.scheduler.capability import snapshot_from_runtime

    actual = replace(runtime_device.runtime_capabilities(), providers=(
        "CPUExecutionProvider", "CUDAExecutionProvider",
    ))
    snapshot = snapshot_from_runtime(
        actual, now_ms=harness.clock(), health="healthy",
        provider_health={"CPUExecutionProvider": cpu_health, "CUDAExecutionProvider": gpu_health},
    )
    scheduler = ocr_service.build_scheduler(harness.catalog)
    recorded = scheduler.record_observation(
        snapshot, observed_devices=("cpu", "gpu"), now_ms=harness.clock(),
    )
    assert recorded.health_status == expected
    assert scheduler.latest_capability().health_status == expected


def test_older_provider_enumeration_cannot_erase_newer_gpu_observation(
    harness: Any, monkeypatch: Any
) -> None:
    from dataclasses import replace

    from photo_search.runtime import device as runtime_device

    actual = runtime_device.runtime_capabilities()
    current = [replace(actual, providers=("CPUExecutionProvider", "CUDAExecutionProvider"))]
    monkeypatch.setattr(runtime_device, "runtime_capabilities", lambda: current[0])
    scheduler = ocr_service.build_scheduler(harness.catalog)
    spec = scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID)
    start = harness.clock()
    ocr_service.record_runtime_evidence(
        scheduler, spec, FakeEngineCache(), data_root=harness.data_root,
        now_ms=start + 20, note="new GPU", devices=("gpu",),
    )
    current[0] = replace(current[0], providers=("CPUExecutionProvider",))
    ocr_service.record_runtime_evidence(
        scheduler, spec, FakeEngineCache(), data_root=harness.data_root,
        now_ms=start + 10, note="older CPU enumeration", devices=("cpu",),
    )
    gpu = scheduler.latest_capability().providers_for("gpu")
    assert len(gpu) == 1 and gpu[0].health == "healthy"
    assert gpu[0].health_checked_at == start + 20
    ocr_service.record_runtime_evidence(
        scheduler, spec, FakeEngineCache(), data_root=harness.data_root,
        now_ms=start + 30, note="newer provider disappearance", devices=("cpu",),
    )
    assert scheduler.latest_capability().providers_for("gpu") == ()


def test_older_gpu_probe_cannot_revive_newer_provider_disappearance(
    harness: Any, monkeypatch: Any
) -> None:
    from dataclasses import replace

    from photo_search.runtime import device as runtime_device

    actual = runtime_device.runtime_capabilities()
    current = [replace(actual, providers=("CPUExecutionProvider",))]
    monkeypatch.setattr(runtime_device, "runtime_capabilities", lambda: current[0])
    scheduler = ocr_service.build_scheduler(harness.catalog)
    spec = scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID)
    start = harness.clock()
    ocr_service.record_runtime_evidence(
        scheduler, spec, FakeEngineCache(), data_root=harness.data_root,
        now_ms=start + 30, note="newer CUDA absent", devices=("cpu",),
    )
    previous = scheduler.latest_capability()
    current[0] = replace(current[0], providers=("CPUExecutionProvider", "CUDAExecutionProvider"))
    with pytest.raises(CatalogError) as error:
        ocr_service.record_runtime_evidence(
            scheduler, spec, FakeEngineCache(), data_root=harness.data_root,
            now_ms=start + 20, note="older GPU probe", devices=("gpu",),
        )
    assert error.value.code == "RUNTIME_CAPABILITY_OBSERVATION_STALE"
    assert scheduler.latest_capability().capability_revision == previous.capability_revision
    assert scheduler.latest_capability().providers_for("gpu") == ()


def test_slow_cpu_probe_uses_actual_provider_enumeration_time(
    harness: Any, monkeypatch: Any
) -> None:
    import threading
    from dataclasses import replace

    from photo_search.catalog.db import Catalog
    from photo_search.runtime import device as runtime_device

    actual = runtime_device.runtime_capabilities()
    runtime = [replace(actual, providers=("CPUExecutionProvider", "CUDAExecutionProvider"))]
    monkeypatch.setattr(runtime_device, "runtime_capabilities", lambda: runtime[0])
    cpu_started, release_cpu = threading.Event(), threading.Event()
    outcomes = []
    class DelayedCache(FakeEngineCache):
        def engine(self, root, *, resolved_device, **kwargs):
            if resolved_device == "cpu":
                cpu_started.set()
                assert release_cpu.wait(5)
            return super().engine(root, resolved_device=resolved_device, **kwargs)
    def cpu_probe():
        try:
            with Catalog.open(data_root=harness.data_root, clock=harness.clock) as catalog:
                scheduler = ocr_service.build_scheduler(catalog)
                outcomes.append(ocr_service.record_runtime_evidence(
                    scheduler, scheduler.spec_for(contract.OCR_CPU_PROCESSOR_ID), DelayedCache(),
                    data_root=harness.data_root, now_ms=catalog.now_ms(), note="slow CPU",
                    devices=("cpu",), observation_clock=catalog.now_ms,
                ))
        except Exception as error:
            outcomes.append(error)
    thread = threading.Thread(target=cpu_probe, daemon=True)
    thread.start()
    try:
        assert cpu_started.wait(3)
        harness.clock.advance(20)
        gpu_scheduler = ocr_service.build_scheduler(harness.catalog)
        ocr_service.record_runtime_evidence(
            gpu_scheduler, gpu_scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID), FakeEngineCache(),
            data_root=harness.data_root, now_ms=harness.clock(), note="GPU visible",
            devices=("gpu",), observation_clock=harness.clock,
        )
        harness.clock.advance(20)
        runtime[0] = replace(runtime[0], providers=("CPUExecutionProvider",))
        release_cpu.set()
        thread.join(5)
        assert not thread.is_alive()
        assert len(outcomes) == 1 and isinstance(outcomes[0], dict), outcomes
        latest = gpu_scheduler.latest_capability()
        assert latest.measured_at == harness.clock()
        assert latest.providers_for("gpu") == ()
    finally:
        release_cpu.set()
        thread.join(5)


def test_cached_semantic_provider_list_does_not_erase_newer_ocr_gpu(
    harness: Any, monkeypatch: Any
) -> None:
    from dataclasses import replace

    from photo_search.indexing import semantic
    from photo_search.runtime import device as runtime_device

    actual = runtime_device.runtime_capabilities()
    monkeypatch.setattr(runtime_device, "runtime_capabilities", lambda: replace(
        actual, providers=("CPUExecutionProvider", "CUDAExecutionProvider"),
    ))
    scheduler = ocr_service.build_scheduler(harness.catalog)
    gpu_spec = scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID)
    ocr_service.record_runtime_evidence(
        scheduler, gpu_spec, FakeEngineCache(), data_root=harness.data_root,
        now_ms=harness.clock(), note="fresh GPU", devices=("gpu",),
    )
    before = scheduler.latest_capability()
    old_gpu = before.providers_for("gpu")[0].to_envelope()
    harness.clock.advance(1)
    class CachedAdapter:
        def health(self):
            return {
                "runtime_observed_at": harness.clock() - 2,
                "runtime": {"runtime_package": "onnxruntime-gpu",
                            "runtime_version": actual.runtime_version,
                            "cuda_available": False, "providers": ["CPUExecutionProvider"]},
                "device": {"device": "cpu", "provider": "CPUExecutionProvider",
                           "precision": "FP32"},
            }
    facts = semantic.adapter_runtime_facts(CachedAdapter())
    cpu_spec = scheduler.spec_for(contract.OCR_CPU_PROCESSOR_ID)
    # Match the semantic producer's legacy platform spelling to the test runtime.
    previous_identity = semantic.RuntimeCapabilities.seal({
        **before.envelope, "platform": "windows-x86_64",
    })
    scheduler.record_capability(previous_identity, now_ms=harness.clock())
    harness.clock.advance(1)
    semantic.record_runtime_evidence(
        scheduler, cpu_spec, facts, now_ms=int(facts.get("observed_at") or 0),
    )
    latest = scheduler.latest_capability()
    assert latest.providers_for("gpu")[0].to_envelope() == old_gpu
    assert latest.measured_at == previous_identity.measured_at


def test_cached_face_gpu_failure_cannot_override_later_ocr_gpu_health(
    harness: Any, monkeypatch: Any
) -> None:
    import threading
    from dataclasses import replace
    from types import SimpleNamespace

    from photo_search.catalog.errors import RUNTIME_CAPABILITY_UNAVAILABLE
    from photo_search.face import engine as face_engine
    from photo_search.face import service as face_service
    from photo_search.face import weights
    from photo_search.runtime import device as runtime_device

    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", threading.RLock())
    actual = replace(runtime_device.runtime_capabilities(), providers=(
        "CPUExecutionProvider", "CUDAExecutionProvider",
    ))
    monkeypatch.setattr(runtime_device, "runtime_capabilities", lambda: actual)
    monkeypatch.setattr(face_engine, "runtime_capabilities", lambda: actual)
    monkeypatch.setattr(weights, "resolve", lambda _root: object())
    cache = face_engine.FaceEngineCache()
    monkeypatch.setattr(cache, "_signature", lambda _weights: ("test",))
    def engine(_root, *, device):
        if device == "gpu":
            raise CatalogError(RUNTIME_CAPABILITY_UNAVAILABLE, "old GPU failure")
        return SimpleNamespace(
            provider="CPUExecutionProvider", is_degradation=False, reason="fixture",
            detector_providers=[], recognizer_providers=[], mixed_cuda_detection=False,
            recognition_cpu_fallback_disabled=True,
        )
    monkeypatch.setattr(cache, "engine", engine)
    face_spec = face_service.face_processor_spec()
    face_scheduler = face_service.build_scheduler(harness.catalog, face_spec)
    face_service.record_runtime_evidence(
        face_scheduler, face_spec, cache, data_root=harness.data_root, device="gpu",
        now_ms=harness.clock(), observation_clock=harness.clock,
    )
    harness.clock.advance(20)
    ocr_scheduler = ocr_service.build_scheduler(harness.catalog)
    ocr_service.record_runtime_evidence(
        ocr_scheduler, ocr_scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID), FakeEngineCache(),
        data_root=harness.data_root, now_ms=harness.clock(), note="new GPU", devices=("gpu",),
        observation_clock=harness.clock,
    )
    previous = ocr_scheduler.latest_capability()
    assert previous.providers_for("gpu")[0].health == "healthy"
    harness.clock.advance(20)
    with pytest.raises(CatalogError) as error:
        face_service.record_runtime_evidence(
            face_scheduler, face_spec, cache, data_root=harness.data_root, device="gpu",
            now_ms=harness.clock(), observation_clock=harness.clock,
        )
    assert error.value.code == "RUNTIME_CAPABILITY_OBSERVATION_STALE"
    assert ocr_scheduler.latest_capability().capability_revision == previous.capability_revision


@pytest.mark.parametrize("newer_state", ["missing", "unhealthy", "identity"])
def test_old_semantic_worker_cannot_revive_newer_gpu_evidence(
    harness: Any, monkeypatch: Any, newer_state: str
) -> None:
    from dataclasses import replace

    from photo_search.indexing import semantic
    from photo_search.runtime import device as runtime_device
    from photo_search.scheduler.capability import snapshot_from_runtime

    actual = replace(runtime_device.runtime_capabilities(), providers=(
        ("CPUExecutionProvider",) if newer_state == "missing"
        else ("CPUExecutionProvider", "CUDAExecutionProvider")
    ), platform="windows-x86_64" if newer_state != "identity" else "new-runtime")
    start = harness.clock()
    snapshot = snapshot_from_runtime(
        actual, now_ms=start + 20, health="degraded",
        provider_health={"CPUExecutionProvider": "healthy", "CUDAExecutionProvider": "unhealthy"},
    )
    scheduler = ocr_service.build_scheduler(harness.catalog)
    scheduler.record_capability(snapshot, now_ms=start + 20)
    harness.clock.advance(30)
    class OldGpuWorker:
        def health(self):
            return {
                "runtime_observed_at": start + 10,
                "runtime": {"runtime_package": "onnxruntime-gpu",
                            "runtime_version": actual.runtime_version, "cuda_available": True,
                            "providers": ["CPUExecutionProvider", "CUDAExecutionProvider"]},
                "device": {"device": "cuda", "provider": "CUDAExecutionProvider",
                           "precision": "FP32"},
            }
    facts = semantic.adapter_runtime_facts(OldGpuWorker())
    spec = scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID)
    with pytest.raises(CatalogError) as error:
        semantic.record_runtime_evidence(scheduler, spec, facts, now_ms=facts["observed_at"])
    assert error.value.code == "RUNTIME_CAPABILITY_OBSERVATION_STALE"
    assert scheduler.latest_capability().capability_revision == snapshot.capability_revision


@pytest.mark.parametrize("has_unknown", [False, True])
def test_semantic_health_without_observation_time_cannot_qualify(
    harness: Any, monkeypatch: Any, has_unknown: bool
) -> None:
    from dataclasses import replace

    from photo_search.indexing import semantic
    from photo_search.runtime import device as runtime_device
    from photo_search.scheduler.capability import snapshot_from_runtime

    actual = replace(runtime_device.runtime_capabilities(), providers=(
        "CPUExecutionProvider", "CUDAExecutionProvider",
    ), platform="windows-x86_64")
    scheduler = ocr_service.build_scheduler(harness.catalog)
    if has_unknown:
        snapshot = snapshot_from_runtime(actual, now_ms=harness.clock())
        scheduler.record_capability(snapshot, now_ms=harness.clock())
    class OldAdapter:
        def health(self):
            return {"runtime": {"cuda_available": True},
                    "device": {"device": "cuda", "provider": "CUDAExecutionProvider",
                               "precision": "FP32"}}
    facts = semantic.adapter_runtime_facts(OldAdapter())
    before = harness.catalog.read_one("SELECT COUNT(*) AS n FROM runtime_qualification_evidence")["n"]
    with pytest.raises(CatalogError) as error:
        semantic.record_runtime_evidence(
            scheduler, scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID), facts, now_ms=harness.clock(),
        )
    assert error.value.details["reason"] == "RUNTIME_OBSERVATION_TIME_UNKNOWN"
    after = harness.catalog.read_one("SELECT COUNT(*) AS n FROM runtime_qualification_evidence")["n"]
    assert after == before


def test_candidate_runtime_does_not_retimestamp_old_worker_health(harness: Any) -> None:
    from dataclasses import replace
    from types import SimpleNamespace

    from photo_search.models.embedding.cli import _record_candidate_runtime
    from photo_search.runtime import device as runtime_device
    from photo_search.scheduler.capability import snapshot_from_runtime

    actual = replace(runtime_device.runtime_capabilities(), providers=("CPUExecutionProvider",),
                     platform="windows-x86_64")
    start = harness.clock()
    snapshot = snapshot_from_runtime(
        actual, now_ms=start + 20, health="healthy",
        provider_health={"CPUExecutionProvider": "healthy"},
    )
    scheduler = ocr_service.build_scheduler(harness.catalog)
    scheduler.record_capability(snapshot, now_ms=start + 20)
    harness.clock.advance(30)
    health = {
        "runtime_observed_at": start + 10,
        "runtime": {"runtime_package": "onnxruntime-gpu", "runtime_version": actual.runtime_version,
                    "providers": ["CPUExecutionProvider", "CUDAExecutionProvider"],
                    "cuda_available": True},
        "device": {"device": "cuda", "provider": "CUDAExecutionProvider", "precision": "FP32"},
    }
    manifest = Path("tools/prepare_models/manifests/chinese-clip-vit-b-16.json").resolve()
    entry = SimpleNamespace(manifest_path=manifest, device_policy="cpu_allowed")
    with pytest.raises(CatalogError) as error:
        _record_candidate_runtime(harness.catalog, entry, health)
    assert error.value.code == "RUNTIME_CAPABILITY_OBSERVATION_STALE"
    assert scheduler.latest_capability().capability_revision == snapshot.capability_revision


def test_cached_semantic_worker_cannot_reuse_another_sessions_newer_gpu_fact(harness: Any) -> None:
    from dataclasses import replace

    from photo_search.indexing import semantic
    from photo_search.runtime import device as runtime_device
    from photo_search.scheduler.capability import snapshot_from_runtime

    actual = replace(runtime_device.runtime_capabilities(),
                     providers=("CPUExecutionProvider", "CUDAExecutionProvider"),
                     platform="windows-x86_64")
    start = harness.clock()
    snapshot = snapshot_from_runtime(
        actual, now_ms=start + 20, health="healthy",
        provider_health={"CUDAExecutionProvider": "healthy"},
    )
    scheduler = ocr_service.build_scheduler(harness.catalog)
    scheduler.record_capability(snapshot, now_ms=start + 20)
    harness.clock.advance(30)
    class CachedWorker:
        def health(self):
            return {"runtime_observed_at": start + 10,
                    "runtime": {"runtime_package": "onnxruntime-gpu",
                                "runtime_version": actual.runtime_version,
                                "providers": ["CPUExecutionProvider", "CUDAExecutionProvider"],
                                "cuda_available": True},
                    "device": {"device": "cuda", "provider": "CUDAExecutionProvider",
                               "precision": "FP32"}}
    facts = semantic.adapter_runtime_facts(CachedWorker())
    spec = scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID)
    with pytest.raises(CatalogError) as error:
        semantic.record_runtime_evidence(scheduler, spec, facts, now_ms=harness.clock())
    assert error.value.code == "RUNTIME_CAPABILITY_OBSERVATION_STALE"
    assert scheduler.latest_capability().capability_revision == snapshot.capability_revision


def test_semantic_session_provider_change_is_not_masked_by_new_ocr_health(
    harness: Any, monkeypatch: Any
) -> None:
    from dataclasses import replace
    from types import SimpleNamespace

    from photo_search.indexing import semantic
    from photo_search.models.embedding import chinese_clip_onnx
    from photo_search.runtime import device as runtime_device

    actual = replace(runtime_device.runtime_capabilities(), providers=(
        "CPUExecutionProvider", "CUDAExecutionProvider",
    ))
    monkeypatch.setattr(runtime_device, "runtime_capabilities", lambda: actual)
    monkeypatch.setattr(chinese_clip_onnx.time, "time_ns", lambda: harness.clock() * 1_000_000)
    class Session:
        def __init__(self):
            self.providers = ["CUDAExecutionProvider"]
        def get_providers(self):
            return self.providers
    adapter = chinese_clip_onnx.ChineseClipOnnxAdapter.__new__(
        chinese_clip_onnx.ChineseClipOnnxAdapter
    )
    adapter._image_session, adapter._text_session = Session(), Session()
    adapter._resolution = SimpleNamespace(
        provider="CUDAExecutionProvider",
        as_dict=lambda: {"device": "cuda", "provider": "CUDAExecutionProvider", "precision": "FP32"},
    )
    adapter._spec = SimpleNamespace(as_dict=lambda: {})
    adapter._capabilities = actual
    adapter._dll_report = None
    adapter._runtime_observed_at = harness.clock()
    assert semantic.adapter_runtime_facts(adapter)["observed_at"] == harness.clock()
    harness.clock.advance(20)
    scheduler = ocr_service.build_scheduler(harness.catalog)
    ocr_service.record_runtime_evidence(
        scheduler, scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID), FakeEngineCache(),
        data_root=harness.data_root, now_ms=harness.clock(), note="new OCR", devices=("gpu",),
    )
    previous = scheduler.latest_capability()
    adapter._text_session.providers = ["CPUExecutionProvider"]
    harness.clock.advance(20)
    with pytest.raises(CatalogError) as error:
        semantic.adapter_runtime_facts(adapter)
    assert error.value.code == "RUNTIME_RESOLUTION_MISMATCH"
    assert scheduler.latest_capability().capability_revision == previous.capability_revision


def test_face_cache_revalidates_after_newer_ocr_before_next_job(
    harness: Any, monkeypatch: Any
) -> None:
    import threading
    from dataclasses import replace

    from photo_search.face import engine as face_engine
    from photo_search.face import service as face_service
    from photo_search.face import weights
    from photo_search.runtime import device as runtime_device

    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", threading.RLock())
    actual = replace(runtime_device.runtime_capabilities(), providers=(
        "CPUExecutionProvider", "CUDAExecutionProvider",
    ))
    monkeypatch.setattr(runtime_device, "runtime_capabilities", lambda: actual)
    monkeypatch.setattr(face_engine, "runtime_capabilities", lambda: actual)
    monkeypatch.setattr(weights, "resolve", lambda _root: object())
    cache = face_engine.FaceEngineCache()
    monkeypatch.setattr(cache, "_signature", lambda _weights: ("test",))
    class Engine:
        def __init__(self, device):
            self.provider = "CUDAExecutionProvider" if device == "gpu" else "CPUExecutionProvider"
            self.is_degradation, self.reason = False, "fixture"
            self.detector_providers, self.recognizer_providers = [self.provider], [self.provider]
            self.mixed_cuda_detection = False
            self.recognition_cpu_fallback_disabled = True
            self.verifications = 0
        def verify_runtime_provider(self):
            self.verifications += 1
    engines = {device: Engine(device) for device in ("cpu", "gpu")}
    for device, engine in engines.items():
        cache._engines[("test", device)] = engine
    monkeypatch.setattr(cache, "engine", lambda _root, *, device: engines[device])
    spec = face_service.face_processor_spec()
    scheduler = face_service.build_scheduler(harness.catalog, spec)
    face_service.record_runtime_evidence(
        scheduler, spec, cache, data_root=harness.data_root, device="gpu",
        now_ms=harness.clock(), observation_clock=harness.clock,
    )
    harness.clock.advance(20)
    ocr = ocr_service.build_scheduler(harness.catalog)
    ocr_service.record_runtime_evidence(
        ocr, ocr.spec_for(contract.OCR_GPU_PROCESSOR_ID), FakeEngineCache(),
        data_root=harness.data_root, now_ms=harness.clock(), note="new OCR", devices=("gpu",),
    )
    harness.clock.advance(20)
    evidence = face_service.record_runtime_evidence(
        scheduler, spec, cache, data_root=harness.data_root, device="gpu",
        now_ms=harness.clock(), observation_clock=harness.clock,
    )
    assert "gpu" in evidence["qualified_devices"]
    assert engines["gpu"].verifications == 1
    assert scheduler.latest_capability().providers_for("gpu")[0].health_checked_at == harness.clock()


def test_real_ocr_pagination_reuses_session_without_skipping_page_verification(
    harness: Any, monkeypatch: Any
) -> None:
    import threading

    from photo_search.face import service as face_service
    from photo_search.ocr import engine as ocr_engine
    from photo_search.ocr import weights as ocr_weights
    from photo_search.refresh import pipeline

    for index in range(2):
        Image.new("RGB", (240 + index, 120), "white").save(
            harness.library / f"ocr-cache-page-{index}.jpg", "JPEG",
        )
    harness.scan()
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", threading.RLock())
    monkeypatch.setattr(pipeline, "BUILD_PAGE_SIZE", 1)
    install_runtime_evidence(monkeypatch)
    calls = {"page_verify": 0, "cache_verify": 0, "create": 0}
    fake_weights = object()
    def verify_page(_root):
        calls["page_verify"] += 1
        return fake_weights
    monkeypatch.setattr(ocr_weights, "resolve_and_verify", verify_page)
    monkeypatch.setattr(ocr_weights, "resolve", lambda _root: fake_weights)
    def verify(_weights):
        calls["cache_verify"] += 1
        return fake_weights
    monkeypatch.setattr(ocr_engine, "verify", verify)
    class CachedFakeEngine(FakeEngine):
        def __init__(self, _weights, *, resolved_device, **_kwargs):
            calls["create"] += 1
            super().__init__(resolved_device, failure=None, confidence=0.9)
        def verify_runtime_provider(self):
            pass
    monkeypatch.setattr(ocr_engine, "PinnedOcrEngine", CachedFakeEngine)
    cache = ocr_engine.OcrEngineCache()
    monkeypatch.setattr(cache, "_signature", lambda _weights: ("fixture",))
    result = pipeline._drain_scope(
        harness.catalog, scopes=[None], cancel_check=None,
        runner=lambda page: pipeline._ocr_stage(
            harness.catalog, harness.data_root, page, engine_cache=cache,
        ),
    )
    assert result["state"] == pipeline.STAGE_OK
    assert result["pages"] == 2 and result["steps"] == 2
    assert calls == {"page_verify": 2, "cache_verify": 1, "create": 1}


def test_cpu_cancel_during_first_job_leaves_next_queued_and_resumable(
    harness: Any, monkeypatch: Any
) -> None:
    import threading

    from photo_search.catalog.db import Catalog

    for index in range(2):
        Image.new("RGB", (240 + index, 120), "white").save(
            harness.library / f"cpu-cancel-{index}.jpg", "JPEG",
        )
    harness.scan()
    assets = [harness.asset_id(f"cpu-cancel-{index}.jpg") for index in range(2)]
    for asset in assets:
        enqueue(harness, asset, device="cpu")
    install_runtime_evidence(monkeypatch)
    started, resume, cancelled = threading.Event(), threading.Event(), threading.Event()
    original = ocr_service.ocr_executor
    outcomes = []
    def delayed(*args, **kwargs):
        executor = original(*args, **kwargs)
        def run(request):
            payload = executor(request)
            started.set()
            assert resume.wait(5)
            return payload
        return run
    monkeypatch.setattr(ocr_service, "ocr_executor", delayed)
    def worker():
        try:
            with Catalog.open(data_root=harness.data_root, clock=harness.clock) as catalog:
                outcomes.append(ocr_service.run_queue(
                    catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
                    cancel=cancelled.is_set,
                ))
        except Exception as error:
            outcomes.append(error)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert started.wait(3), outcomes
        cancelled.set()
        resume.set()
        thread.join(5)
        assert not thread.is_alive() and len(outcomes) == 1 and isinstance(outcomes[0], dict)
        assert outcomes[0]["stopped"] is True and outcomes[0]["steps"] == 1
        states = [jobs_for(harness, asset)[contract.OCR_CPU_PROCESSOR_ID]["state"] for asset in assets]
        assert states.count("SUCCEEDED") == 1 and states.count("QUEUED") == 1
        cancelled.clear()
        monkeypatch.setattr(ocr_service, "ocr_executor", original)
        resumed = ocr_service.run_queue(
            harness.catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
        )
        assert resumed["steps"] == 1
        assert all(jobs_for(harness, asset)[contract.OCR_CPU_PROCESSOR_ID]["state"] == "SUCCEEDED"
                   for asset in assets)
    finally:
        resume.set()
        thread.join(5)


def test_cancel_between_cpu_and_gpu_work_does_not_claim_gpu(
    harness: Any, monkeypatch: Any
) -> None:
    import threading

    for index in range(2):
        Image.new("RGB", (240 + index, 120), "white").save(
            harness.library / f"cross-cancel-{index}.jpg", "JPEG",
        )
    harness.scan()
    cpu_asset = harness.asset_id("cross-cancel-0.jpg")
    gpu_asset = harness.asset_id("cross-cancel-1.jpg")
    enqueue(harness, cpu_asset, device="cpu")
    enqueue(harness, gpu_asset, device="gpu")
    install_runtime_evidence(monkeypatch)
    cancelled = threading.Event()
    original = ocr_service._processor_has_work
    def has_work(catalog, processor_id, *args, **kwargs):
        if processor_id == contract.OCR_GPU_PROCESSOR_ID:
            cancelled.set()
        return original(catalog, processor_id, *args, **kwargs)
    monkeypatch.setattr(ocr_service, "_processor_has_work", has_work)
    result = ocr_service.run_queue(
        harness.catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
        cancel=cancelled.is_set,
    )
    assert result["stopped"] and result["steps"] == 1
    assert jobs_for(harness, cpu_asset)[contract.OCR_CPU_PROCESSOR_ID]["state"] == "SUCCEEDED"
    pending = jobs_for(harness, gpu_asset)[contract.OCR_GPU_PROCESSOR_ID]
    assert pending["state"] == "QUEUED" and pending["attempts"] == 0


def test_cpu_cancel_inside_inference_keeps_later_job_queued(
    harness: Any, monkeypatch: Any
) -> None:
    import threading

    from photo_search.catalog.db import Catalog

    for index in range(2):
        Image.new("RGB", (240 + index, 120), "white").save(
            harness.library / f"cpu-inference-cancel-{index}.jpg", "JPEG",
        )
    harness.scan()
    assets = [harness.asset_id(f"cpu-inference-cancel-{index}.jpg") for index in range(2)]
    for asset in assets:
        enqueue(harness, asset, device="cpu")
    install_runtime_evidence(monkeypatch)
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    outcomes = []
    class DelayedEngine(FakeEngine):
        def read(self, image):
            entered.set()
            assert release.wait(5)
            return super().read(image)
    class Cache(FakeEngineCache):
        def engine(self, _root, *, resolved_device, **_kwargs):
            return DelayedEngine(resolved_device, failure=None, confidence=0.9)
    def worker():
        try:
            with Catalog.open(data_root=harness.data_root, clock=harness.clock) as catalog:
                outcomes.append(ocr_service.run_queue(
                    catalog, data_root=harness.data_root, engine_cache=Cache(),
                    cancel=cancelled.is_set,
                ))
        except Exception as error:
            outcomes.append(error)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert entered.wait(3), outcomes
        cancelled.set()
        release.set()
        thread.join(5)
        assert not thread.is_alive() and len(outcomes) == 1 and isinstance(outcomes[0], dict)
        assert outcomes[0]["stopped"] and outcomes[0]["steps"] == 1
        states = [jobs_for(harness, asset)[contract.OCR_CPU_PROCESSOR_ID]["state"] for asset in assets]
        assert states.count("CANCELLED") == 1 and states.count("QUEUED") == 1
        assert sum(harness.ocr_document(asset) is not None for asset in assets) == 0
        cancelled.clear()
        resumed = ocr_service.run_queue(
            harness.catalog, data_root=harness.data_root, engine_cache=FakeEngineCache(),
        )
        assert resumed["steps"] == 1
        states = [jobs_for(harness, asset)[contract.OCR_CPU_PROCESSOR_ID]["state"] for asset in assets]
        assert states.count("CANCELLED") == 1 and states.count("SUCCEEDED") == 1
    finally:
        release.set()
        thread.join(5)


def test_direct_scheduler_never_claims_gpu_without_permit_but_cpu_proceeds(
    harness: Any, monkeypatch: Any
) -> None:
    import threading

    from photo_search.face import service as face_service

    for index in range(2):
        Image.new("RGB", (240 + index, 120), "white").save(
            harness.library / f"direct-permit-{index}.jpg", "JPEG",
        )
    harness.scan()
    gpu_asset = harness.asset_id("direct-permit-0.jpg")
    cpu_asset = harness.asset_id("direct-permit-1.jpg")
    enqueue(harness, gpu_asset, device="gpu")
    enqueue(harness, cpu_asset, device="cpu")
    install_runtime_evidence(monkeypatch)
    lock = threading.RLock()
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", lock)
    held, release = threading.Event(), threading.Event()
    def holder():
        with lock:
            held.set()
            assert release.wait(5)
    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    try:
        assert held.wait(2)
        scheduler = ocr_service.build_scheduler(harness.catalog)
        ocr_service.record_runtime_evidence(
            scheduler, scheduler.spec_for(contract.OCR_CPU_PROCESSOR_ID), FakeEngineCache(),
            data_root=harness.data_root, now_ms=harness.clock(), note="synthetic",
        )
        executor = ocr_executor(harness.catalog, engine_cache=FakeEngineCache())
        assert scheduler.run_once(executor, worker_id="direct", now_ms=harness.clock())
        assert jobs_for(harness, cpu_asset)[contract.OCR_CPU_PROCESSOR_ID]["state"] == "SUCCEEDED"
        with pytest.raises(CatalogError) as error:
            scheduler.run_once(executor, worker_id="direct", now_ms=harness.clock())
        assert error.value.code == "BACKGROUND_MODEL_SLOT_BUSY"
        assert error.value.details["reason"] == "GPU_PERMIT_REQUIRED"
        pending = jobs_for(harness, gpu_asset)[contract.OCR_GPU_PROCESSOR_ID]
        assert pending["state"] == "QUEUED" and pending["attempts"] == 0
        assert pending["lease_token"] is None
        release.set()
        thread.join(3)
        with lock:
            assert scheduler.run_once(executor, worker_id="direct", now_ms=harness.clock())
        assert jobs_for(harness, gpu_asset)[contract.OCR_GPU_PROCESSOR_ID]["state"] == "SUCCEEDED"
    finally:
        release.set()
        thread.join(3)


def test_gpu_backlog_over_candidate_limit_does_not_starve_cpu(
    harness: Any, monkeypatch: Any
) -> None:
    asset = make_asset(harness, "cpu-after-gpu-backlog.jpg")
    with harness.catalog.write() as connection:
        for index in range(270):
            jobs.enqueue(
                connection, subject_type="asset", subject_id=str(100_000 + index),
                input_revision="a" * 64, task_type=contract.OCR_TASK_TYPE,
                processor_id=contract.OCR_GPU_PROCESSOR_ID, now_ms=harness.clock(),
                requested_device="gpu",
            )
    enqueue(harness, asset, device="cpu")
    install_runtime_evidence(monkeypatch)
    scheduler = ocr_service.build_scheduler(harness.catalog)
    ocr_service.record_runtime_evidence(
        scheduler, scheduler.spec_for(contract.OCR_CPU_PROCESSOR_ID), FakeEngineCache(),
        data_root=harness.data_root, now_ms=harness.clock(), note="synthetic",
    )
    executor = ocr_executor(harness.catalog, engine_cache=FakeEngineCache())
    assert scheduler.run_once(executor, worker_id="cpu-only", now_ms=harness.clock())
    assert jobs_for(harness, asset)[contract.OCR_CPU_PROCESSOR_ID]["state"] == "SUCCEEDED"
    rows = harness.catalog.read_all(
        "SELECT state, attempts, lease_token FROM jobs WHERE processor_id=?",
        (contract.OCR_GPU_PROCESSOR_ID,),
    )
    assert len(rows) == 270
    assert all((row["state"], row["attempts"], row["lease_token"]) == ("QUEUED", 0, None)
               for row in rows)


def test_unpermitted_gpu_maintenance_and_manual_claim_leave_jobs_unchanged(
    harness: Any, monkeypatch: Any
) -> None:
    import threading

    from photo_search.face import service as face_service

    lock = threading.RLock()
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", lock)
    with harness.catalog.write() as connection:
        ids = [jobs.enqueue(
            connection, subject_type="asset", subject_id=str(200_000 + index),
            input_revision="b" * 64, task_type=contract.OCR_TASK_TYPE,
            processor_id=contract.OCR_GPU_PROCESSOR_ID, now_ms=harness.clock(),
            requested_device="gpu",
        ) for index in range(2)]
        connection.execute(
            "UPDATE jobs SET state='RETRY_WAIT', run_after=? WHERE job_id=?",
            (harness.clock(), ids[0]),
        )
        connection.execute(
            "UPDATE jobs SET state='RUNNING', attempts=max_attempts-1, lease_token=?, "
            "lease_expires_at=?, worker_id=? WHERE job_id=?",
            ("synthetic-token", harness.clock() - 1, "old", ids[1]),
        )
    def snapshot():
        return [tuple(row) for row in harness.catalog.read_all(
            "SELECT job_id,state,attempts,lease_token,lease_expires_at,worker_id FROM jobs "
            "WHERE job_id IN (?,?) ORDER BY job_id", tuple(ids),
        )]
    before = snapshot()
    scheduler = ocr_service.build_scheduler(harness.catalog)
    with pytest.raises(CatalogError) as error:
        scheduler.run_once(lambda _request: pytest.fail("unexpected GPU execution"),
                           worker_id="direct", now_ms=harness.clock())
    assert error.value.code == "BACKGROUND_MODEL_SLOT_BUSY"
    assert snapshot() == before
    with lock, pytest.raises(CatalogError) as error:
        scheduler.claim_next(worker_id="manual", now_ms=harness.clock())
    assert error.value.code == "GPU_OCR_MANAGED_DISPATCH_REQUIRED"
    assert snapshot() == before


def test_direct_cpu_scheduler_rechecks_lease_after_execution(
    harness: Any, monkeypatch: Any
) -> None:
    asset = make_asset(harness, "direct-cpu-lease.jpg")
    enqueue(harness, asset, device="cpu")
    install_runtime_evidence(monkeypatch)
    scheduler = ocr_service.build_scheduler(harness.catalog)
    ocr_service.record_runtime_evidence(
        scheduler, scheduler.spec_for(contract.OCR_CPU_PROCESSOR_ID), FakeEngineCache(),
        data_root=harness.data_root, now_ms=harness.clock(), note="synthetic",
    )
    real = ocr_executor(harness.catalog, engine_cache=FakeEngineCache())
    def late(request):
        result = real(request)
        harness.clock.advance((scheduler.lease_seconds + 1) * 1000)
        return result
    with pytest.raises(CatalogError) as error:
        scheduler.run_once(late, worker_id="direct", now_ms=harness.clock())
    assert error.value.code == STALE_LEASE
    assert harness.ocr_document(asset) is None


def test_direct_gpu_run_once_renews_lease_during_long_execution(
    harness: Any, monkeypatch: Any
) -> None:
    import threading
    import time

    from photo_search.catalog.db import Catalog
    from photo_search.face import service as face_service
    from photo_search.runtime.background_slot import background_model_slot
    from photo_search.scheduler.engine import Scheduler

    asset = make_asset(harness, "direct-gpu-heartbeat.jpg")
    enqueue(harness, asset, device="gpu")
    install_runtime_evidence(monkeypatch)
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", threading.RLock())
    started, release = threading.Event(), threading.Event()
    outcomes = []
    def worker():
        try:
            with Catalog.open(data_root=harness.data_root, clock=harness.clock, lease_seconds=1) as catalog:
                spec = ocr_service.ocr_processor_spec("gpu")
                scheduler = Scheduler(catalog, [spec], lease_seconds=1)
                ocr_service.record_runtime_evidence(
                    scheduler, spec, FakeEngineCache(), data_root=harness.data_root,
                    now_ms=catalog.now_ms(), note="synthetic",
                )
                executor = ocr_executor(catalog, engine_cache=FakeEngineCache())
                def delayed(request):
                    result = executor(request)
                    started.set()
                    assert release.wait(5)
                    return result
                with background_model_slot():
                    outcomes.append(scheduler.run_once(
                        delayed, worker_id="direct-gpu", now_ms=catalog.now_ms(),
                    ))
        except Exception as error:
            outcomes.append(error)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert started.wait(3), outcomes
        initial = jobs_for(harness, asset)[contract.OCR_GPU_PROCESSOR_ID]["lease_expires_at"]
        harness.clock.advance(400)
        deadline = time.monotonic() + 3
        while jobs_for(harness, asset)[contract.OCR_GPU_PROCESSOR_ID]["lease_expires_at"] <= initial:
            assert time.monotonic() < deadline
            threading.Event().wait(0.02)
        harness.clock.advance(400)
        release.set()
        thread.join(5)
        assert not thread.is_alive() and outcomes == [True]
        assert jobs_for(harness, asset)[contract.OCR_GPU_PROCESSOR_ID]["state"] == "SUCCEEDED"
    finally:
        release.set()
        thread.join(5)


def test_direct_ocr_drain_refreshes_claim_clock_between_jobs(
    harness: Any, monkeypatch: Any
) -> None:
    for index in range(2):
        Image.new("RGB", (240 + index, 120), "white").save(
            harness.library / f"direct-clock-{index}.jpg", "JPEG",
        )
    harness.scan()
    assets = [harness.asset_id(f"direct-clock-{index}.jpg") for index in range(2)]
    for asset in assets:
        enqueue(harness, asset, device="cpu")
    install_runtime_evidence(monkeypatch)
    scheduler = ocr_service.build_scheduler(harness.catalog)
    ocr_service.record_runtime_evidence(
        scheduler, scheduler.spec_for(contract.OCR_CPU_PROCESSOR_ID), FakeEngineCache(),
        data_root=harness.data_root, now_ms=harness.clock(), note="synthetic",
    )
    executor = ocr_executor(harness.catalog, engine_cache=FakeEngineCache())
    initial = harness.clock()
    original_submit = scheduler.submit
    calls = []
    def submit_then_advance(result, **kwargs):
        result = original_submit(result, **kwargs)
        calls.append(1)
        if len(calls) == 1:
            harness.clock.advance((scheduler.lease_seconds + 1) * 1000)
        return result
    monkeypatch.setattr(scheduler, "submit", submit_then_advance)
    assert scheduler.run_until_idle(executor, worker_id="direct", now_ms=initial) == 2
    assert all(jobs_for(harness, asset)[contract.OCR_CPU_PROCESSOR_ID]["state"] == "SUCCEEDED"
               for asset in assets)
