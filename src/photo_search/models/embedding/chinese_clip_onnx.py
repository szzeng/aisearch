"""Manifest-driven Chinese-CLIP ONNX adapter with an explicit device policy.

The module owns ONNX, tokenization and image preprocessing.  Its public results are
ordinary metadata plus vectors, so callers do not depend on tensor details.

The adapter never guesses a device.  It resolves the requested device against the
runtime's providers (default CUDA under ADR-0001), creates the ONNX session with the CPU
execution-provider fallback disabled so a CUDA fault cannot turn into a silent whole-model
CPU run, and reports the resolved device/provider plus any degradation reason.

This adapter only needs ``transformers`` for its tokenizer, and that import pulls PyTorch in. The
AI worker therefore runs an ONNX-serving process with ``USE_TORCH=0`` (see
:func:`photo_search.workers.ai_worker.prepare_framework_stack`), so such a process carries exactly
one CUDA stack - ONNX Runtime's own - and never trips ONNX Runtime's PyTorch-mismatch notice.
"""

from __future__ import annotations

import json
import tempfile
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime
from PIL import Image
from transformers import BertTokenizerFast

from photo_search.models.embedding.base import analyze_manifest
from photo_search.runtime.device import (
    DevicePolicy,
    DeviceResolution,
    RuntimeCapabilities,
    build_session_options,
    preload_runtime_dlls,
    resolve_device,
    runtime_capabilities,
    session_providers,
)


@dataclass(frozen=True)
class ProcessorSpec:
    """Immutable metadata identifying an embedding representation space."""

    processor_id: str
    space_id: str
    dimension: int
    metric: str
    normalization: str
    model_revision: str
    backend: str
    device: str
    image_size: int
    token_limit: int
    provider_id: str = "chinese-clip-vit-b-16"
    adapter: str = "chinese_clip_onnx"
    device_policy: str = DevicePolicy.CPU_ALLOWED.value
    runtime_provider: str = "unresolved"
    runtime_version: str = ""
    precision: str = "FP32"
    cpu_fallback_disabled: bool = True

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class RuntimeConfig:
    """Per-processor runtime choices; the device stays explicit and reportable."""

    requested_device: str | None = None
    device_policy: DevicePolicy = DevicePolicy.CPU_ALLOWED
    precision: str = "FP32"
    strict_fp32: bool = True
    disable_cpu_fallback: bool = True
    enable_profiling: bool = False
    profile_directory: Path | None = None
    cuda_memory_limit_bytes: int | None = None
    preload_cuda_dlls: bool = True
    dll_directory: Path | None = None


class ChineseClipOnnxAdapter:
    """Load P0-03's frozen ONNX artifacts without importing PyTorch."""

    def __init__(
        self,
        model_dir: Path,
        manifest_path: Path,
        runtime_config: RuntimeConfig | None = None,
    ) -> None:
        self.model_dir = model_dir
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self._config = runtime_config or RuntimeConfig()
        self._provider, self._identity = analyze_manifest(self.manifest)
        embedding = self.manifest["embedding"]
        model = self.manifest["model"]
        runtime = self.manifest["runtime"]
        preprocessing = self.manifest["preprocessing"]
        tokenizer = self.manifest["tokenizer"]
        digest = self.manifest["artifacts"]["onnx"]["image"]["sha256"][:16]
        self._spec = ProcessorSpec(
            processor_id=f"{self._identity.provider_id}@{model['resolved_revision'][:12]}",
            space_id=f"{self._identity.provider_id}:{digest}",
            dimension=int(embedding["dimension"]),
            metric=str(embedding["metric"]),
            normalization=str(embedding["normalization"]),
            model_revision=str(model["resolved_revision"]),
            backend=str(runtime["backend"]),
            device="unresolved",
            image_size=int(preprocessing["image_size"]),
            token_limit=int(tokenizer["max_length"]),
            provider_id=str(self._provider["provider_id"]),
            adapter=str(self._provider["adapter"]),
            device_policy=DevicePolicy(self._config.device_policy).value,
            runtime_version=onnxruntime.__version__,
            precision=self._config.precision,
            cpu_fallback_disabled=self._config.disable_cpu_fallback,
        )
        self._mean = np.asarray(preprocessing["mean"], dtype=np.float32).reshape(3, 1, 1)
        self._std = np.asarray(preprocessing["std"], dtype=np.float32).reshape(3, 1, 1)
        self._image_path = model_dir / self.manifest["artifacts"]["onnx"]["image"]["path"]
        self._text_path = model_dir / self.manifest["artifacts"]["onnx"]["text"]["path"]
        self._tokenizer_path = model_dir / self.manifest["tokenizer"]["vocab_file"]
        self._image_session: onnxruntime.InferenceSession | None = None
        self._text_session: onnxruntime.InferenceSession | None = None
        self._tokenizer: Any | None = None
        self._resolution: DeviceResolution | None = None
        self._capabilities: RuntimeCapabilities | None = None
        self._dll_report: dict[str, Any] | None = None
        self._profile_prefix = f"photo-search-{uuid.uuid4().hex[:8]}-"

    def spec(self) -> ProcessorSpec:
        return self._spec

    @property
    def resolution(self) -> DeviceResolution:
        if self._resolution is None:
            raise RuntimeError("adapter is not loaded")
        return self._resolution

    def load(self) -> None:
        for path in (self._image_path, self._text_path, self._tokenizer_path):
            if not path.is_file():
                raise FileNotFoundError(f"required model artifact not found: {path}")
        self._capabilities = runtime_capabilities()
        self._resolution = resolve_device(
            self._config.requested_device,
            policy=self._config.device_policy,
            providers=self._capabilities.providers,
            precision=self._config.precision,
            strict_fp32=self._config.strict_fp32,
            disable_cpu_fallback=self._config.disable_cpu_fallback,
            cuda_memory_limit_bytes=self._config.cuda_memory_limit_bytes,
        )
        # The CUDA/cuDNN DLLs are only preloaded for a CUDA session; an explicit CPU run
        # must not touch the GPU driver at all.
        if self._resolution.device == "cuda" and self._config.preload_cuda_dlls:
            self._dll_report = preload_runtime_dlls(self._config.dll_directory)
        session_options = build_session_options(
            self._resolution,
            enable_profiling=self._config.enable_profiling,
            profile_file_prefix=self._profile_prefix_value(),
        )
        providers = session_providers(self._resolution)
        self._image_session = self._create_session(self._image_path, providers, session_options, "image")
        self._text_session = self._create_session(self._text_path, providers, session_options, "text")
        self._tokenizer = BertTokenizerFast(vocab_file=str(self._tokenizer_path), do_lower_case=True)
        self._spec = replace(
            self._spec,
            device=self._resolution.device,
            runtime_provider=self._resolution.provider,
        )

    def _create_session(
        self,
        model_path: Path,
        providers: list[Any],
        session_options: onnxruntime.SessionOptions,
        role: str,
    ) -> onnxruntime.InferenceSession:
        try:
            return onnxruntime.InferenceSession(
                str(model_path), sess_options=session_options, providers=providers
            )
        except Exception as error:
            if self._resolution is not None and self._resolution.device == "cuda":
                raise RuntimeError(
                    "failed to create the {role} CUDA session without a silent CPU fallback "
                    "(provider={provider}, model={model}): {error}".format(
                        role=role,
                        provider=self._resolution.provider,
                        model=model_path.name,
                        error=error,
                    )
                ) from error
            raise

    def _profile_prefix_value(self) -> str | None:
        if not self._config.enable_profiling:
            return None
        directory = self._config.profile_directory or Path(tempfile.gettempdir())
        return str(directory / self._profile_prefix)

    def health(self) -> dict[str, Any]:
        return {
            "loaded": self._image_session is not None and self._text_session is not None,
            "spec": self.spec().as_dict(),
            "device": self._resolution.as_dict() if self._resolution else None,
            "runtime": self._capabilities.as_dict() if self._capabilities else None,
            "cuda_dlls": self._dll_report,
        }

    def provider_usage(self) -> dict[str, Any]:
        """Actual per-node execution providers, read from ONNX Runtime profiling."""
        usage: dict[str, Any] = {}
        for role, session in (("image", self._image_session), ("text", self._text_session)):
            if session is None:
                continue
            try:
                profile_path = session.end_profiling()
            except Exception as error:
                usage[role] = {"error": f"{type(error).__name__}: {error}"}
                continue
            usage[role] = _summarise_profile(profile_path)
        return usage

    def encode_text(self, text: str) -> list[float]:
        if not isinstance(text, str) or not text:
            raise ValueError("text must be a non-empty string")
        self._require_loaded()
        assert self._tokenizer is not None and self._text_session is not None
        inputs = self._tokenizer(
            text,
            padding="max_length",
            truncation=True,
            max_length=self._spec.token_limit,
            return_tensors="np",
        )
        result = self._text_session.run(None, dict(inputs))[0]
        return self._normalize(result[0]).tolist()

    def encode_image(self, image_path: Path) -> list[float]:
        self._require_loaded()
        assert self._image_session is not None
        pixels = self._preprocess(image_path)
        result = self._image_session.run(None, {"pixel_values": pixels})[0]
        return self._normalize(result[0]).tolist()

    def _require_loaded(self) -> None:
        if self._image_session is None or self._text_session is None:
            raise RuntimeError("adapter is not loaded")

    def _preprocess(self, image_path: Path) -> np.ndarray:
        with Image.open(image_path) as source:
            image = source.convert("RGB")
            width, height = image.size
            size = self._spec.image_size
            scale = size / min(width, height)
            resized = image.resize((round(width * scale), round(height * scale)), Image.Resampling.BICUBIC)
            left = (resized.width - size) // 2
            top = (resized.height - size) // 2
            cropped = resized.crop((left, top, left + size, top + size))
        values = np.asarray(cropped, dtype=np.float32).transpose(2, 0, 1) / 255.0
        return ((values - self._mean) / self._std)[None, ...].astype(np.float32)

    def _normalize(self, vector: np.ndarray) -> np.ndarray:
        value = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(value))
        if norm == 0:
            raise ValueError("model returned a zero embedding")
        normalized = value / norm
        if normalized.shape != (self._spec.dimension,):
            raise ValueError(f"unexpected embedding shape: {normalized.shape}")
        return normalized


def _summarise_profile(profile_path: str | None) -> dict[str, Any]:
    """Count the execution provider of every node event in an ORT profile file."""
    if not profile_path:
        return {"available": False}
    path = Path(profile_path)
    if not path.is_file():
        return {"available": False, "profile_path": str(path)}
    try:
        events = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return {"available": False, "profile_path": str(path), "error": str(error)}
    counts: dict[str, int] = {}
    nodes = 0
    for event in events:
        if event.get("cat") not in {"Node", "Kernel"}:
            continue
        provider = (event.get("args") or {}).get("provider")
        if provider is None:
            continue
        nodes += 1
        counts[str(provider)] = counts.get(str(provider), 0) + 1
    summary: dict[str, Any] = {
        "available": True,
        "profile_path": str(path),
        "node_events": nodes,
        "node_providers": counts,
    }
    try:
        path.unlink()
    except OSError:
        pass
    return summary
