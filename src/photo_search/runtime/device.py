"""Runtime device policy and ONNX Runtime capability facts.

ADR-0001 fixes how the product treats AI compute devices: ``onnxruntime-gpu`` is the
single ONNX Runtime distribution in the runtime environment, Chinese-CLIP image/text
and InsightFace detection/recognition default to CUDA, and CPU stays available as a
numerical baseline plus an explicit, reported degradation.

A silent whole-model CPU fallback is never allowed. CUDA sessions therefore disable the
CPU execution provider fallback, and every processor reports the device, execution
provider and (when applicable) the degradation reason it actually resolved.
"""

from __future__ import annotations

import importlib.metadata
import os
import platform
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

import onnxruntime

CUDA_PROVIDER = "CUDAExecutionProvider"
CPU_PROVIDER = "CPUExecutionProvider"
TENSORRT_PROVIDER = "TensorrtExecutionProvider"
DISABLE_CPU_FALLBACK_KEY = "session.disable_cpu_ep_fallback"
TF32_OPTION = "use_tf32"

#: Diagnostic override that masks the reported execution providers. It lets support and
#: the fault-injection tests reproduce "CUDA is unavailable" deterministically, without
#: uninstalling the GPU runtime or disturbing the machine's GPU driver state.
PROVIDER_OVERRIDE_ENV = "PHOTO_SEARCH_AVAILABLE_PROVIDERS"

#: NVIDIA runtime wheels that ``onnxruntime-gpu[cuda,cudnn]`` installs on Windows.
CUDA_DLL_PACKAGES = (
    "nvidia-cuda-runtime",
    "nvidia-cuda-nvrtc",
    "nvidia-cublas",
    "nvidia-cudnn-cu13",
    "nvidia-cufft",
    "nvidia-curand",
    "nvidia-nvjitlink",
)


class DevicePolicy(str, Enum):
    """How a processor resolves the requested device.

    ``GPU_REQUIRED``  default CUDA; an explicit CPU request is refused, so a GPU fault can
                      never turn into an accidental CPU run.
    ``CPU_ALLOWED``   default CUDA; an explicit CPU request is served and reported as a
                      degradation (the Chinese-CLIP, face and OCR processors).
    ``CPU_PREFERRED`` default CPU; an explicit CUDA request is still honoured.
    """

    GPU_REQUIRED = "gpu_required"
    CPU_ALLOWED = "cpu_allowed"
    CPU_PREFERRED = "cpu_preferred"


class DeviceUnavailableError(RuntimeError):
    """The requested device cannot be served and no silent fallback is permitted."""

    def __init__(
        self,
        *,
        requested_device: str | None,
        policy: DevicePolicy,
        available_providers: Sequence[str],
        reason: str,
    ) -> None:
        self.requested_device = requested_device
        self.policy = policy
        self.available_providers = tuple(available_providers)
        self.reason = reason
        super().__init__(
            f"device {requested_device or policy.value} unavailable: {reason}; "
            f"available providers={list(self.available_providers)}; cpu fallback disabled"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested_device": self.requested_device,
            "policy": self.policy.value,
            "reason": self.reason,
            "available_providers": list(self.available_providers),
            "cpu_fallback": "disabled",
        }


@dataclass(frozen=True)
class DeviceResolution:
    """The device/provider a processor actually resolved, with its justification."""

    requested_device: str | None
    device: str
    provider: str
    policy: DevicePolicy
    is_degradation: bool
    reason: str
    available_providers: tuple[str, ...]
    precision: str = "FP32"
    strict_fp32: bool = True
    provider_options: dict[str, str] = field(default_factory=dict)
    cpu_fallback_disabled: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested_device": self.requested_device,
            "device": self.device,
            "provider": self.provider,
            "policy": self.policy.value,
            "is_degradation": self.is_degradation,
            "reason": self.reason,
            "available_providers": list(self.available_providers),
            "precision": self.precision,
            "strict_fp32": self.strict_fp32,
            "provider_options": dict(self.provider_options),
            "cpu_fallback_disabled": self.cpu_fallback_disabled,
        }


@dataclass(frozen=True)
class RuntimeCapabilities:
    """Inspectable facts about the runtime environment handed to the scheduler."""

    runtime_backend: str
    runtime_package: str
    runtime_version: str
    python: str
    platform: str
    providers: tuple[str, ...]
    cuda_available: bool
    cuda_dll_packages: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "runtime_backend": self.runtime_backend,
            "runtime_package": self.runtime_package,
            "runtime_version": self.runtime_version,
            "python": self.python,
            "platform": self.platform,
            "providers": list(self.providers),
            "cuda_available": self.cuda_available,
            "cuda_dll_packages": dict(self.cuda_dll_packages),
        }


def normalize_device(value: str) -> str:
    """Map user-facing device spellings onto ``cuda``/``cpu``."""
    key = value.strip().lower()
    if key.endswith("executionprovider"):
        key = key[: -len("executionprovider")]
    key = key.strip("_ ")
    if key in {"cuda", "gpu"}:
        return "cuda"
    if key in {"cpu", "host"}:
        return "cpu"
    raise ValueError(f"unsupported device: {value!r}")


def available_providers() -> tuple[str, ...]:
    """Providers this ONNX Runtime build reports, independent of device availability.

    ``PHOTO_SEARCH_AVAILABLE_PROVIDERS`` (comma separated) overrides the list for
    diagnostics and device-failure tests; it never enables a device that ONNX Runtime
    cannot run, because the override is only consulted where providers are resolved.
    """
    override = os.environ.get(PROVIDER_OVERRIDE_ENV)
    if override is not None:
        return tuple(name.strip() for name in override.split(",") if name.strip())
    return tuple(onnxruntime.get_available_providers())


def _installed_version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def runtime_capabilities() -> RuntimeCapabilities:
    """Collect the runtime facts recorded for every GPU validation run."""
    providers = available_providers()
    packages = {name: version for name in CUDA_DLL_PACKAGES if (version := _installed_version(name))}
    return RuntimeCapabilities(
        runtime_backend="onnxruntime",
        runtime_package="onnxruntime-gpu",
        runtime_version=onnxruntime.__version__,
        python=platform.python_version(),
        platform=platform.platform(),
        providers=providers,
        cuda_available=CUDA_PROVIDER in providers,
        cuda_dll_packages=packages,
    )


def resolve_device(
    requested_device: str | None = None,
    *,
    policy: DevicePolicy = DevicePolicy.CPU_ALLOWED,
    providers: Sequence[str] | None = None,
    precision: str = "FP32",
    strict_fp32: bool = True,
    disable_cpu_fallback: bool = True,
    cuda_memory_limit_bytes: int | None = None,
) -> DeviceResolution:
    """Resolve the execution device, or fail loudly instead of falling back to CPU."""
    policy = DevicePolicy(policy)
    provider_list = tuple(providers) if providers is not None else available_providers()
    explicit = requested_device is not None
    target = normalize_device(requested_device) if explicit else _default_device(policy)

    if target == "cuda":
        if CUDA_PROVIDER not in provider_list:
            raise DeviceUnavailableError(
                requested_device=requested_device,
                policy=policy,
                available_providers=provider_list,
                reason="CUDAExecutionProvider is not offered by this ONNX Runtime build",
            )
        options: dict[str, str] = {}
        if strict_fp32:
            options[TF32_OPTION] = "0"
        if cuda_memory_limit_bytes is not None:
            options["gpu_mem_limit"] = str(int(cuda_memory_limit_bytes))
        return DeviceResolution(
            requested_device=requested_device,
            device="cuda",
            provider=CUDA_PROVIDER,
            policy=policy,
            is_degradation=False,
            reason="explicit CUDA request" if explicit else f"default for {policy.value}",
            available_providers=provider_list,
            precision=precision,
            strict_fp32=strict_fp32,
            provider_options=options,
            cpu_fallback_disabled=disable_cpu_fallback,
        )

    if explicit and policy is DevicePolicy.GPU_REQUIRED:
        raise DeviceUnavailableError(
            requested_device=requested_device,
            policy=policy,
            available_providers=provider_list,
            reason="device policy gpu_required refuses an explicit CPU request",
        )
    return DeviceResolution(
        requested_device=requested_device,
        device="cpu",
        provider=CPU_PROVIDER,
        policy=policy,
        is_degradation=policy is not DevicePolicy.CPU_PREFERRED,
        reason="explicit CPU request" if explicit else f"default for {policy.value}",
        available_providers=provider_list,
        precision=precision,
        strict_fp32=strict_fp32,
        provider_options={},
        cpu_fallback_disabled=False,
    )


def _default_device(policy: DevicePolicy) -> str:
    return "cpu" if policy is DevicePolicy.CPU_PREFERRED else "cuda"


def session_providers(resolution: DeviceResolution) -> list[Any]:
    """Build the ONNX Runtime provider list for an already resolved device."""
    if resolution.device == "cuda":
        return [(CUDA_PROVIDER, dict(resolution.provider_options))]
    return [CPU_PROVIDER]


def build_session_options(
    resolution: DeviceResolution | None = None,
    *,
    disable_cpu_fallback: bool | None = None,
    enable_profiling: bool = False,
    profile_file_prefix: str | None = None,
    intra_op_threads: int | None = None,
    inter_op_threads: int | None = None,
) -> onnxruntime.SessionOptions:
    """Create session options that keep the resolved device honest."""
    options = onnxruntime.SessionOptions()
    if disable_cpu_fallback is None:
        disable_cpu_fallback = True if resolution is None else resolution.cpu_fallback_disabled
    if disable_cpu_fallback:
        options.add_session_config_entry(DISABLE_CPU_FALLBACK_KEY, "1")
    if enable_profiling:
        options.enable_profiling = True
        if profile_file_prefix:
            options.profile_file_prefix = profile_file_prefix
    if intra_op_threads:
        options.intra_op_num_threads = int(intra_op_threads)
    if inter_op_threads:
        options.inter_op_num_threads = int(inter_op_threads)
    return options


#: ONNX Runtime's documented "nvidia site packages only" selector. ``preload_dlls`` reads an
#: empty *string* as "search the ``nvidia-*`` site packages and the default DLL paths", i.e. it
#: skips the "compatible PyTorch lib directory" step entirely.
NVIDIA_SITE_PACKAGES_ONLY = ""


def preload_runtime_dlls(directory: str | Path | None = None) -> dict[str, Any]:
    """Load ONNX Runtime's own CUDA/cuDNN DLLs before CUDA initialises.

    ``onnxruntime-gpu`` is built against CUDA 13 and ships its runtime as the ``nvidia-*``
    wheels, while the Qwen provider needs the measured PyTorch cu128 build - so this one
    environment deliberately carries two CUDA stacks. ONNX Runtime's *default* probe prefers
    "the lib directory of a compatible PyTorch": with matching CUDA majors it would put
    ``torch\\lib`` first on the DLL search path, where the cuDNN 9 files carry the *same names*
    as ONNX Runtime's cuDNN 9 but are linked against CUDA 12. That cross-major mix is not a
    supported configuration, so the DLL source is pinned here explicitly instead of being
    decided by whatever PyTorch happens to be installed.

    ``directory=""`` is ONNX Runtime's own documented selector for "``nvidia`` site packages,
    then the default DLL paths". Note what this does *not* do: ONNX Runtime prints its
    "installed PyTorch uses CUDA 12.x / this build uses CUDA 13.x" line whenever ``torch`` is in
    ``sys.modules`` of the same process (``onnxruntime/__init__.py``), independent of this
    argument. That message is silenced by not importing torch in an ONNX-serving process at all
    (see the ONNX adapter's lazy tokenizer import), not by this function.

    A PyInstaller build still passes the flat directory it ships, and that path keeps its
    previous behaviour (explicit ``directory`` plus ``add_dll_directory``); an explicit
    ``directory`` argument from a caller is honoured unchanged.
    """
    mode = "nvidia-site-packages"
    if directory is None:
        bundled = bundled_dll_directory()
        if bundled is not None:
            directory = bundled
            mode = "packaged"
        else:
            directory = NVIDIA_SITE_PACKAGES_ONLY
    else:
        mode = "explicit"
    report: dict[str, Any] = {
        "mode": mode,
        "directory": str(directory) if str(directory) else None,
        "ort_directory_argument": str(directory),
        "search_path_added": False,
        "loaded": False,
        "error": None,
    }
    try:
        if str(directory):
            path = Path(directory)
            if os.name == "nt" and hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(path))
                report["search_path_added"] = True
            onnxruntime.preload_dlls(directory=str(path))
        else:
            onnxruntime.preload_dlls(directory=NVIDIA_SITE_PACKAGES_ONLY)
        report["loaded"] = True
    except Exception as error:  # DLL preloading must never mask the real provider error
        report["error"] = f"{type(error).__name__}: {error}"
    return report


def bundled_dll_directory() -> Path | None:
    """Locate the stacked NVIDIA DLL folder shipped inside a PyInstaller build."""
    if not getattr(sys, "frozen", False):
        return None
    roots = [getattr(sys, "_MEIPASS", None), Path(sys.executable).parent]
    for root in roots:
        if root is None:
            continue
        for relative in (Path("nvidia_dlls"), Path("_internal") / "nvidia_dlls"):
            candidate = Path(root) / relative
            if candidate.is_dir():
                return candidate
    return None


def add_dll_search_paths(directories: Sequence[str | Path]) -> list[str]:
    """Register extra DLL directories (packaged NVIDIA runtime) on Windows."""
    added: list[str] = []
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return added
    for directory in directories:
        path = Path(directory)
        if path.is_dir():
            os.add_dll_directory(str(path))
            added.append(str(path))
    return added
