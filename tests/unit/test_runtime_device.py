from __future__ import annotations

import pytest

from photo_search.runtime.device import (
    CPU_PROVIDER,
    CUDA_PROVIDER,
    PROVIDER_OVERRIDE_ENV,
    DevicePolicy,
    DeviceUnavailableError,
    available_providers,
    build_session_options,
    normalize_device,
    resolve_device,
    runtime_capabilities,
    session_providers,
)

CUDA_PROVIDERS = (CUDA_PROVIDER, CPU_PROVIDER)
CPU_ONLY_PROVIDERS = (CPU_PROVIDER,)


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
