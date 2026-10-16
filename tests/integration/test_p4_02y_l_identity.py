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
    scheduler = ocr_service.build_scheduler(harness.catalog)
    scheduler.claim_next(worker_id="synthetic", now_ms=harness.clock())
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
    scheduler = ocr_service.build_scheduler(harness.catalog)
    ocr_service.record_runtime_evidence(
        scheduler,
        scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID),
        FakeEngineCache(),
        data_root=harness.data_root,
        now_ms=harness.clock(),
        note="expired result test",
    )
    request = scheduler.claim_next(worker_id="synthetic", now_ms=harness.clock())
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
    scheduler = ocr_service.build_scheduler(harness.catalog)
    ocr_service.record_runtime_evidence(
        scheduler,
        scheduler.spec_for(contract.OCR_GPU_PROCESSOR_ID),
        FakeEngineCache(),
        data_root=harness.data_root,
        now_ms=harness.clock(),
        note="live lease test",
    )
    scheduler.claim_next(worker_id="synthetic", now_ms=harness.clock())
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
