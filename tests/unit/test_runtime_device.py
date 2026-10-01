from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from photo_search.models.embedding.chinese_clip_onnx import _summarise_profile
from photo_search.runtime import device as runtime_device
from photo_search.runtime.device import (
    CPU_PROVIDER,
    CUDA_PROVIDER,
    NVIDIA_SITE_PACKAGES_ONLY,
    PROVIDER_OVERRIDE_ENV,
    DevicePolicy,
    DeviceUnavailableError,
    available_providers,
    build_session_options,
    normalize_device,
    preload_runtime_dlls,
    resolve_device,
    runtime_capabilities,
    session_providers,
)

CUDA_PROVIDERS = (CUDA_PROVIDER, CPU_PROVIDER)
CPU_ONLY_PROVIDERS = (CPU_PROVIDER,)
CLIP_MANIFEST = (
    Path(__file__).resolve().parents[2]
    / "tools"
    / "prepare_models"
    / "manifests"
    / "chinese-clip-vit-b-16.json"
)
QWEN_MANIFEST = (
    Path(__file__).resolve().parents[2]
    / "tools"
    / "prepare_models"
    / "manifests"
    / "qwen3-vl-embedding-2b.json"
)


def test_normalize_device_accepts_user_and_provider_spellings() -> None:
    assert normalize_device("cuda") == "cuda"
    assert normalize_device("GPU") == "cuda"
    assert normalize_device("CUDAExecutionProvider") == "cuda"
    assert normalize_device("cpu") == "cpu"
    assert normalize_device("CPUExecutionProvider") == "cpu"
    with pytest.raises(ValueError, match="unsupported device"):
        normalize_device("directml")


def test_default_device_is_cuda_when_the_policy_allows_it() -> None:
    resolution = resolve_device(None, policy=DevicePolicy.CPU_ALLOWED, providers=CUDA_PROVIDERS)
    assert resolution.device == "cuda"
    assert resolution.provider == CUDA_PROVIDER
    assert resolution.is_degradation is False
    assert resolution.cpu_fallback_disabled is True
    assert resolution.as_dict()["reason"] == "default for cpu_allowed"


def test_missing_cuda_provider_fails_loudly_without_a_cpu_fallback() -> None:
    with pytest.raises(DeviceUnavailableError) as captured:
        resolve_device(None, policy=DevicePolicy.GPU_REQUIRED, providers=CPU_ONLY_PROVIDERS)
    error = captured.value
    assert error.as_dict()["available_providers"] == [CPU_PROVIDER]
    assert error.as_dict()["cpu_fallback"] == "disabled"
    assert "cpu fallback disabled" in str(error)


def test_gpu_required_policy_refuses_an_explicit_cpu_request() -> None:
    with pytest.raises(DeviceUnavailableError, match="gpu_required"):
        resolve_device("cpu", policy=DevicePolicy.GPU_REQUIRED, providers=CUDA_PROVIDERS)


def test_explicit_cpu_request_is_recorded_as_a_degradation() -> None:
    resolution = resolve_device("cpu", policy=DevicePolicy.CPU_ALLOWED, providers=CUDA_PROVIDERS)
    assert resolution.device == "cpu"
    assert resolution.provider == CPU_PROVIDER
    assert resolution.is_degradation is True
    assert resolution.reason == "explicit CPU request"
    assert resolution.cpu_fallback_disabled is False


def test_cpu_preferred_policy_defaults_to_cpu_and_still_honours_cuda() -> None:
    default = resolve_device(None, policy=DevicePolicy.CPU_PREFERRED, providers=CUDA_PROVIDERS)
    assert default.device == "cpu"
    assert default.is_degradation is False
    requested = resolve_device("cuda", policy=DevicePolicy.CPU_PREFERRED, providers=CUDA_PROVIDERS)
    assert requested.device == "cuda"
    assert requested.is_degradation is False


def test_cuda_sessions_disable_tf32_and_the_cpu_fallback() -> None:
    resolution = resolve_device("cuda", policy=DevicePolicy.CPU_ALLOWED, providers=CUDA_PROVIDERS)
    providers = session_providers(resolution)
    assert providers == [(CUDA_PROVIDER, {"use_tf32": "0"})]
    options = build_session_options(resolution)
    assert options.get_session_config_entry("session.disable_cpu_ep_fallback") == "1"
    cpu_options = build_session_options(
        resolve_device("cpu", policy=DevicePolicy.CPU_ALLOWED, providers=CUDA_PROVIDERS)
    )
    with pytest.raises(RuntimeError, match="does not have configuration"):
        cpu_options.get_session_config_entry("session.disable_cpu_ep_fallback")


def test_cuda_memory_limit_is_forwarded_to_the_provider() -> None:
    resolution = resolve_device(
        "cuda",
        policy=DevicePolicy.CPU_ALLOWED,
        providers=CUDA_PROVIDERS,
        cuda_memory_limit_bytes=1024 * 1024,
    )
    assert session_providers(resolution)[0][1]["gpu_mem_limit"] == str(1024 * 1024)


def test_provider_override_masks_the_reported_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PROVIDER_OVERRIDE_ENV, "CPUExecutionProvider")
    assert available_providers() == (CPU_PROVIDER,)
    monkeypatch.setenv(PROVIDER_OVERRIDE_ENV, "CUDAExecutionProvider, CPUExecutionProvider")
    assert available_providers() == (CUDA_PROVIDER, CPU_PROVIDER)
    monkeypatch.setenv(PROVIDER_OVERRIDE_ENV, "")
    assert available_providers() == ()


def test_runtime_capabilities_reports_the_packaged_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PROVIDER_OVERRIDE_ENV, "CPUExecutionProvider")
    capabilities = runtime_capabilities()
    assert capabilities.runtime_package == "onnxruntime-gpu"
    assert capabilities.runtime_backend == "onnxruntime"
    assert capabilities.cuda_available is False
    assert capabilities.providers == (CPU_PROVIDER,)
    assert capabilities.as_dict()["providers"] == [CPU_PROVIDER]


def test_profile_summary_counts_the_provider_of_every_node(tmp_path: Path) -> None:
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            [
                {"cat": "Node", "args": {"provider": CUDA_PROVIDER, "op_name": "MatMul"}},
                {"cat": "Node", "args": {"provider": CUDA_PROVIDER, "op_name": "Add"}},
                {"cat": "Node", "args": {"provider": CPU_PROVIDER, "op_name": "Shape"}},
                {"cat": "Session", "args": {"provider": CUDA_PROVIDER}},
                {"cat": "Node", "args": {}},
            ]
        ),
        encoding="utf-8",
    )
    summary = _summarise_profile(str(profile))
    assert summary["node_events"] == 3
    assert summary["node_providers"] == {CUDA_PROVIDER: 2, CPU_PROVIDER: 1}
    assert not profile.exists()


def test_profile_summary_handles_a_missing_profile() -> None:
    assert _summarise_profile(None) == {"available": False}
    assert _summarise_profile("Z:/definitely/missing.json")["available"] is False


def test_the_ort_preload_never_probes_pytorch(monkeypatch: pytest.MonkeyPatch) -> None:
    """P4-Embedding-Arch: two CUDA stacks in one environment must not share a search path.

    ``onnxruntime-gpu`` is the CUDA 13 build while PyTorch is the measured cu128 build. ONNX
    Runtime's default probe prefers "the lib directory of a compatible PyTorch", which warns
    about the version mismatch and puts ``torch\\lib`` first - where the cuDNN 9 files have the
    same names as ONNX Runtime's but are linked against CUDA 12. The product therefore selects
    ONNX Runtime's documented "nvidia site packages only" mode (an empty string) instead.
    """
    calls: list[Any] = []
    monkeypatch.setattr(
        runtime_device.onnxruntime, "preload_dlls", lambda **kwargs: calls.append(kwargs)
    )
    report = preload_runtime_dlls()
    assert calls == [{"directory": NVIDIA_SITE_PACKAGES_ONLY}]
    assert NVIDIA_SITE_PACKAGES_ONLY == ""
    assert report["mode"] == "nvidia-site-packages"
    assert report["loaded"] is True
    assert report["directory"] is None  # nothing was added to the search path by hand
    assert report["search_path_added"] is False
    assert report["error"] is None


def test_a_packaged_build_still_preloads_its_own_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The frozen-build path is unchanged: an explicit flat directory plus a search path entry."""
    calls: list[Any] = []
    monkeypatch.setattr(runtime_device, "bundled_dll_directory", lambda: tmp_path)
    monkeypatch.setattr(
        runtime_device.onnxruntime, "preload_dlls", lambda **kwargs: calls.append(kwargs)
    )
    report = preload_runtime_dlls()
    assert calls == [{"directory": str(tmp_path)}]
    assert report["mode"] == "packaged"
    assert report["loaded"] is True
    assert report["directory"] == str(tmp_path)
    if os.name == "nt":
        assert report["search_path_added"] is True


def test_an_explicit_directory_argument_is_honoured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(
        runtime_device.onnxruntime, "preload_dlls", lambda **kwargs: calls.append(kwargs)
    )
    report = preload_runtime_dlls(tmp_path)
    assert calls == [{"directory": str(tmp_path)}]
    assert report["mode"] == "explicit"
    assert report["directory"] == str(tmp_path)


def test_a_preload_failure_is_reported_and_never_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(**_: Any) -> None:
        raise OSError("dll not found")

    monkeypatch.setattr(runtime_device.onnxruntime, "preload_dlls", explode)
    report = preload_runtime_dlls()
    assert report["loaded"] is False
    assert report["error"] == "OSError: dll not found"


def test_the_onnx_worker_disables_torch_but_the_qwen_worker_keeps_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One CUDA stack per ONNX worker; PyTorch stays available where it is needed.

    ONNX Runtime prints its CUDA-major mismatch notice whenever ``torch`` is in ``sys.modules``
    (``onnxruntime/__init__.py``), and the cuDNN 9 files share names across the two stacks. An
    ONNX-serving worker therefore turns transformers' PyTorch support off; a ``qwen3_vl`` worker
    must not, and an operator-provided ``USE_TORCH`` is respected.

    The decision has to be observed in a **fresh interpreter** - in this test process torch is
    already imported by the other cases in this file, which is exactly the situation the product
    avoids by calling this before the adapter (and therefore transformers) is imported.
    """
    import json
    import os
    import subprocess
    import sys

    code = (
        "import json, os, sys;"
        "from photo_search.workers.ai_worker import prepare_framework_stack as p;"
        "os.environ.pop('USE_TORCH', None);"
        "clip = p(sys.argv[1]);"
        "os.environ.pop('USE_TORCH', None);"
        "qwen = p(sys.argv[2]);"
        "os.environ['USE_TORCH'] = '1';"
        "respected = p(sys.argv[1]);"
        "print(json.dumps([clip, qwen, respected]))"
    )
    environment = {key: value for key, value in os.environ.items() if key != "USE_TORCH"}
    completed = subprocess.run(
        [sys.executable, "-c", code, str(CLIP_MANIFEST), str(QWEN_MANIFEST)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr[-500:]
    clip, qwen, respected = json.loads(completed.stdout.strip().splitlines()[-1])
    assert clip == {
        "adapter": "chinese_clip_onnx",
        "use_torch": "0",
        "applied": True,
        "torch_imported": False,
    }
    assert qwen["adapter"] == "qwen3_vl" and qwen["applied"] is False
    assert qwen["use_torch"] == "unset" and qwen["torch_imported"] is False
    assert respected == {
        "adapter": "chinese_clip_onnx",
        "use_torch": "1",
        "applied": False,
        "torch_imported": False,
    }
