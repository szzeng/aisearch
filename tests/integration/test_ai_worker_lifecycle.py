"""Managed AI worker lifecycle: CPU baseline, explicit degradation and device failure.

The default cases run the worker on CPU so the suite stays deterministic and never
depends on a healthy GPU driver; the CUDA cases are opt-in through
``PHOTO_SEARCH_GPU_TESTS=1`` because a cold CUDA start can take minutes on a power-gated
laptop GPU and killing a worker mid-initialisation leaves the driver in a bad state.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from photo_search.runtime.device import PROVIDER_OVERRIDE_ENV
from photo_search.workers.client import AiWorkerClient, WorkerError

COLD_START_TIMEOUT_SECONDS = 900.0
CPU_WORKER_TIMEOUT_SECONDS = 600.0
GPU_TESTS_ENABLED = os.environ.get("PHOTO_SEARCH_GPU_TESTS") == "1"


@pytest.fixture()
def model_dir() -> Path:
    return (
        Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        / "PhotoSearchData" / "models" / "chinese_clip" / "vit-b-16"
    )


@pytest.fixture()
def manifest() -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / "tools" / "prepare_models" / "manifests" / "chinese-clip-vit-b-16.json"


def _require_models(model_dir: Path) -> None:
    if not model_dir.is_dir():
        pytest.skip("P0-03 model artifacts are not available")


def test_spawn_worker_reports_spec_and_error_then_exits(
    model_dir: Path, manifest: Path
) -> None:
    _require_models(model_dir)
    worker = AiWorkerClient(model_dir, manifest, device="cpu")
    stderr_path = worker._stderr_file
    with worker:
        health = worker.health(timeout=CPU_WORKER_TIMEOUT_SECONDS)
        assert health["loaded"] is True
        assert health["spec"]["dimension"] == 512
        with pytest.raises(WorkerError) as captured:
            worker._call({"action": "not-a-real-action"}, timeout=CPU_WORKER_TIMEOUT_SECONDS)
        assert captured.value.payload["stage"] == "not-a-real-action"
        assert captured.value.payload["exception_type"] == "ProtocolError"
        pid = worker.pid
    assert pid is not None
    assert not worker._process.is_alive()
    assert not stderr_path.exists()


def test_timeout_terminates_spawned_worker(model_dir: Path, manifest: Path) -> None:
    _require_models(model_dir)
    worker = AiWorkerClient(model_dir, manifest, device="cpu")
    stderr_path = worker._stderr_file
    with pytest.raises(TimeoutError):
        worker.health(timeout=0.001)
    assert not worker._process.is_alive()
    worker.shutdown()
    assert not stderr_path.exists()


def test_startup_error_contains_stderr_tail(model_dir: Path, tmp_path: Path) -> None:
    bad_manifest = tmp_path / "missing-manifest.json"
    worker = AiWorkerClient(model_dir, bad_manifest, device="cpu")
    stderr_path = worker._stderr_file
    with pytest.raises(WorkerError) as captured:
        worker.health(timeout=CPU_WORKER_TIMEOUT_SECONDS)
    assert captured.value.payload["stage"] == "startup"
    assert "FileNotFoundError" in captured.value.stderr_tail
    worker.shutdown()
    assert not stderr_path.exists()


def test_shutdown_without_start_removes_stderr_file(tmp_path: Path) -> None:
    worker = AiWorkerClient(tmp_path / "model", tmp_path / "manifest.json", device="cpu")
    stderr_path = worker._stderr_file
    assert stderr_path.exists()
    worker.shutdown()
    assert not stderr_path.exists()


def test_explicit_cpu_worker_reports_the_degradation(model_dir: Path, manifest: Path) -> None:
    """An explicit CPU request must stay reproducible and must be labelled as such."""
    _require_models(model_dir)
    with AiWorkerClient(model_dir, manifest, device="cpu") as worker:
        health = worker.health(timeout=CPU_WORKER_TIMEOUT_SECONDS)
        device = health["device"]
        assert device["device"] == "cpu"
        assert device["provider"] == "CPUExecutionProvider"
        assert device["is_degradation"] is True
        assert device["reason"] == "explicit CPU request"
        assert device["cpu_fallback_disabled"] is False
        assert health["spec"]["device_policy"] == "cpu_allowed"
        encoded = worker.encode_text("红色的图片", timeout=CPU_WORKER_TIMEOUT_SECONDS)
        assert len(encoded["vector"]) == 512
        assert encoded["device"]["device"] == "cpu"
        demo = worker.demo_search(timeout=CPU_WORKER_TIMEOUT_SECONDS)
        assert [row["id"] for row in demo["top_k"]][0] == "red"
        assert demo["device"]["device"] == "cpu"


def test_explicit_cpu_under_gpu_required_policy_is_refused(
    model_dir: Path, manifest: Path
) -> None:
    """``gpu_required`` must refuse CPU instead of quietly degrading."""
    _require_models(model_dir)
    worker = AiWorkerClient(
        model_dir, manifest, device="cpu", device_policy="gpu_required"
    )
    with pytest.raises(WorkerError) as captured:
        worker.health(timeout=CPU_WORKER_TIMEOUT_SECONDS)
    assert captured.value.payload["stage"] == "startup"
    assert captured.value.payload["exception_type"] == "DeviceUnavailableError"
    assert captured.value.details["policy"] == "gpu_required"
    assert captured.value.details["cpu_fallback"] == "disabled"
    worker.shutdown()


def test_cuda_unavailable_fails_instead_of_falling_back_to_cpu(
    model_dir: Path, manifest: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fault injection: CUDA masked away while the default device is CUDA."""
    _require_models(model_dir)
    monkeypatch.setenv(PROVIDER_OVERRIDE_ENV, "CPUExecutionProvider")
    worker = AiWorkerClient(model_dir, manifest, device="cuda")
    with pytest.raises(WorkerError) as captured:
        worker.health(timeout=CPU_WORKER_TIMEOUT_SECONDS)
    payload = captured.value.payload
    assert payload["stage"] == "startup"
    assert payload["exception_type"] == "DeviceUnavailableError"
    assert "CUDAExecutionProvider" in payload["message"]
    details = captured.value.details
    assert details["requested_device"] == "cuda"
    assert details["available_providers"] == ["CPUExecutionProvider"]
    assert details["cpu_fallback"] == "disabled"
    worker.shutdown()


@pytest.mark.skipif(
    not GPU_TESTS_ENABLED, reason="set PHOTO_SEARCH_GPU_TESTS=1 to run the CUDA worker path"
)
def test_gpu_worker_encodes_and_searches_on_cuda(model_dir: Path, manifest: Path) -> None:
    _require_models(model_dir)
    with AiWorkerClient(model_dir, manifest, device="cuda") as worker:
        health = worker.health(timeout=COLD_START_TIMEOUT_SECONDS)
        assert health["device"]["device"] == "cuda"
        assert health["device"]["provider"] == "CUDAExecutionProvider"
        assert health["device"]["cpu_fallback_disabled"] is True
        encoded = worker.encode_text("红色的图片", timeout=COLD_START_TIMEOUT_SECONDS)
        assert encoded["device"]["provider"] == "CUDAExecutionProvider"
        assert len(encoded["vector"]) == 512
        demo = worker.demo_search(timeout=COLD_START_TIMEOUT_SECONDS)
        assert [row["id"] for row in demo["top_k"]][0] == "red"
        usage = worker.provider_usage(timeout=COLD_START_TIMEOUT_SECONDS)
        providers = {
            provider
            for entry in usage["sessions"].values()
            for provider in (entry.get("node_providers") or {})
        }
        assert providers == {"CUDAExecutionProvider"}
