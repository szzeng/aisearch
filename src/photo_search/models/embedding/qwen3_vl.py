"""Qwen3-VL-Embedding-2B adapter: PyTorch + Transformers, CUDA only, FP16, batch 1.

The module owns the PyTorch runtime, the chat-template prompt, the pixel budget, the
last-token pooling and the MRL prefix truncation, and it never guesses any of them. Three rules
decide what is written here:

* **The device is real, not requested.** ``torch.cuda.is_available()`` is not evidence: the
  adapter allocates on the target device, moves the whole model there and then re-reads every
  parameter's device and dtype. A CPU/offload result is a structured refusal, never a quieter
  but still working run.
* **Nothing is downloaded and nothing is imported from the data root.** Weights load with
  ``local_files_only=True`` from the pinned data-root directory after a size/hash check, and the
  model shell is implemented here instead of importing a script that could be replaced under the
  data root (the reference implementation is attributed in the manifest and reimplemented).
* **One space, exactly as published.** The output is the leading ``dimension`` components of the
  native vector, converted to ``float32`` and L2-normalised, for text, catalog images and uploaded
  images alike. Truncation only happens when the manifest declares the MRL method.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image, ImageOps

from photo_search.catalog.errors import (
    EMBEDDING_DEVICE_UNAVAILABLE,
    EMBEDDING_MANIFEST_INVALID,
    EMBEDDING_RUNTIME_MISSING,
    EMBEDDING_WEIGHTS_HASH_MISMATCH,
    EMBEDDING_WEIGHTS_MISSING,
    CatalogError,
)
from photo_search.models.embedding.base import (
    QWEN_ADAPTER,
    analyze_manifest,
    effective_device_policy,
    read_manifest,
)
from photo_search.runtime.device import DevicePolicy, DeviceResolution

PYTORCH_CUDA_PROVIDER = "PyTorchCUDA"
PYTORCH_CPU_PROVIDER = "PyTorchCPU"
REFERENCE_REPOSITORY = "https://github.com/QwenLM/Qwen3-VL-Embedding"


class QwenRuntimeConfig:
    """Per-process runtime choices of the Qwen adapter; the device stays explicit."""

    def __init__(
        self,
        *,
        requested_device: str | None = None,
        device_policy: str | DevicePolicy = DevicePolicy.GPU_REQUIRED,
        precision: str = "FP16",
        attention: str = "eager",
        cuda_index: int = 0,
        verify_hashes: bool = True,
    ) -> None:
        self.requested_device = requested_device
        self.device_policy = DevicePolicy(device_policy)
        self.precision = str(precision)
        self.attention = str(attention)
        self.cuda_index = int(cuda_index)
        self.verify_hashes = bool(verify_hashes)


@dataclass(frozen=True)
class AdapterSpec:
    """The adapter's own transient description, reported through ``health``."""

    processor_id: str
    space_id: str
    dimension: int
    native_dimension: int
    metric: str
    normalization: str
    model_revision: str
    backend: str
    device: str
    precision: str
    max_pixels: int
    token_limit: int
    provider_id: str
    adapter: str

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def verify_pinned_files(
    model_dir: Path, manifest: dict[str, Any], *, verify_hashes: bool = True
) -> dict[str, Any]:
    """Size/hash check of every pinned file before any framework import.

    A missing file, a changed size or a changed digest is a structured configuration refusal: the
    product never repairs itself by downloading, and it never loads "the same model" from another
    copy that happens to be on the machine.
    """
    checked = 0
    total_bytes = 0
    for item in manifest["weights"]:
        name = str(item["name"])
        path = Path(model_dir) / name
        if not path.is_file():
            raise CatalogError(
                EMBEDDING_WEIGHTS_MISSING,
                "a pinned Qwen model file is not installed",
                details={"file": name, "model_dir": str(model_dir)},
            )
        size = path.stat().st_size
        total_bytes += size
        if "bytes" in item and int(item["bytes"]) != size:
            raise CatalogError(
                EMBEDDING_WEIGHTS_HASH_MISMATCH,
                "a pinned Qwen model file has a different size",
                details={"file": name, "expected_bytes": int(item["bytes"]), "actual_bytes": size},
            )
        if verify_hashes and not _sha256_matches(path, str(item["sha256"])):
            raise CatalogError(
                EMBEDDING_WEIGHTS_HASH_MISMATCH,
                "a pinned Qwen model file has a different checksum",
                details={"file": name},
            )
        checked += 1
    return {"files": checked, "bytes": total_bytes, "hashes_verified": bool(verify_hashes)}


def _sha256_matches(path: Path, expected: str) -> bool:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest() == str(expected)


@dataclass(frozen=True)
class RuntimeFacts:
    """The PyTorch runtime facts the worker and the scheduler consume."""

    backend: str
    package: str
    version: str
    providers: tuple[str, ...]
    cuda_available: bool
    device_name: str | None
    torch_version: str
    torchvision_version: str
    transformers_version: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "runtime_backend": self.backend,
            "runtime_package": self.package,
            "runtime_version": self.version,
            "providers": list(self.providers),
            "cuda_available": bool(self.cuda_available),
            "device_name": self.device_name,
            "versions": {
                "torch": self.torch_version,
                "torchvision": self.torchvision_version,
                "transformers": self.transformers_version,
            },
        }


class Qwen3VLEmbeddingAdapter:
    """The Qwen half of an ADR-0003 ``EmbeddingAdapter``, over one local model directory."""

    def __init__(
        self,
        model_dir: Path,
        manifest_path: Path,
        runtime_config: QwenRuntimeConfig | None = None,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.manifest_path = Path(manifest_path)
        self.manifest = read_manifest(self.manifest_path)
        self._provider, self._identity = analyze_manifest(self.manifest)
        self._config = runtime_config or QwenRuntimeConfig(
            device_policy=str(self._provider["device_policy"])
        )
        embedding = self.manifest["embedding"]
        native = int(embedding["native_dimension"])
        dimension = int(embedding["dimension"])
        if dimension != native and not str(embedding.get("mrl") or "").strip():
            raise CatalogError(
                EMBEDDING_MANIFEST_INVALID,
                "a smaller output dimension needs a declared MRL method",
                details={"dimension": dimension, "native_dimension": native},
            )
        preprocessing = self.manifest["preprocessing"]
        self._spec = AdapterSpec(
            processor_id=str(self._identity.processor_id),
            space_id=str(self._identity.space_id),
            dimension=dimension,
            native_dimension=native,
            metric=str(embedding["metric"]),
            normalization=str(embedding["normalization"]),
            model_revision=str(self._identity.model_revision),
            backend="pytorch",
            device="unresolved",
            precision=str(self._config.precision),
            max_pixels=int(preprocessing["max_pixels"]),
            token_limit=int(self.manifest["tokenizer"]["max_length"]),
            provider_id=str(self._provider["provider_id"]),
            adapter=QWEN_ADAPTER,
        )
        self._min_pixels = int(preprocessing["min_pixels"])
        self._max_pixels = int(preprocessing["max_pixels"])
        self._instruction = {
            key: str(self.manifest["instruction"][key]) for key in ("text", "image", "query")
        }
        self._model: Any | None = None
        self._processor: Any | None = None
        self._torch: Any | None = None
        self._resolution: DeviceResolution | None = None
        self._runtime: RuntimeFacts | None = None
        self._verification: dict[str, Any] | None = None

    # ----------------------------------------------------------------- established surface
    def spec(self) -> AdapterSpec:
        return self._spec

    @property
    def resolution(self) -> DeviceResolution:
        if self._resolution is None:
            raise RuntimeError("adapter is not loaded")
        return self._resolution

    @property
    def identity(self) -> Any:
        return self._identity

    def load(self) -> None:
        """Verify the pinned files, resolve a real CUDA device and load the model once."""
        self._verification = verify_pinned_files(
            self.model_dir, self.manifest, verify_hashes=self._config.verify_hashes
        )
        torch = _import_torch()
        transformers = _import_transformers()
        self._torch = torch
        cuda_available = bool(torch.cuda.is_available())
        self._runtime = RuntimeFacts(
            backend="pytorch",
            package="torch",
            version=str(torch.__version__),
            providers=(PYTORCH_CUDA_PROVIDER,),
            cuda_available=cuda_available,
            device_name=(
                str(torch.cuda.get_device_name(self._config.cuda_index)) if cuda_available else None
            ),
            torch_version=str(torch.__version__),
            torchvision_version=_package_version("torchvision"),
            transformers_version=str(transformers.__version__),
        )
        self._resolution = self._resolve_device(torch)
        model_shell = _model_shell()
        try:
            self._model = model_shell.from_pretrained(
                str(self.model_dir),
                dtype=torch.float16 if self._resolution.precision == "FP16" else torch.float32,
                attn_implementation=self._config.attention,
                local_files_only=True,
                low_cpu_mem_usage=True,
            )
            self._model = self._model.to(self._device_target()).eval()
            self._processor = transformers.AutoProcessor.from_pretrained(
                str(self.model_dir),
                padding_side=str(self.manifest["tokenizer"]["padding_side"]),
                local_files_only=True,
            )
        except CatalogError:
            raise
        except BaseException as error:
            if _is_out_of_memory(error):
                raise CatalogError(
                    EMBEDDING_DEVICE_UNAVAILABLE,
                    "the Qwen model does not fit on the resolved CUDA device",
                    details={"reason": "CUDA_OUT_OF_MEMORY_FOR_MODEL", "device": "cuda"},
                ) from error
            raise CatalogError(
                EMBEDDING_RUNTIME_MISSING,
                "the Qwen model could not be loaded in this environment",
                details={"reason": type(error).__name__},
            ) from error
        self._assert_placement()
        self._spec = replace(
            self._spec, device=self._resolution.device, precision=self._resolution.precision
        )

    def _resolve_device(self, torch: Any) -> DeviceResolution:
        policy = effective_device_policy(
            str(self._provider["device_policy"]), self._config.device_policy
        )
        requested = self._config.requested_device
        target = "cuda"
        if requested is not None and str(requested).strip().lower() in ("cpu", "host"):
            if policy is DevicePolicy.GPU_REQUIRED:
                raise CatalogError(
                    EMBEDDING_DEVICE_UNAVAILABLE,
                    "this model requires CUDA and refuses an explicit CPU request",
                    details={"requested_device": str(requested), "policy": policy.value},
                )
            target = "cpu"
        available = (PYTORCH_CUDA_PROVIDER, PYTORCH_CPU_PROVIDER)
        if target == "cpu":
            return DeviceResolution(
                requested_device=requested,
                device="cpu",
                provider=PYTORCH_CPU_PROVIDER,
                policy=policy,
                is_degradation=policy is not DevicePolicy.CPU_PREFERRED,
                reason="explicit CPU request",
                available_providers=available,
                precision=self._config.precision,
                strict_fp32=False,
                cpu_fallback_disabled=False,
            )
        if not bool(torch.cuda.is_available()):
            raise CatalogError(
                EMBEDDING_DEVICE_UNAVAILABLE,
                "the Qwen provider needs a CUDA device and none is available",
                details={
                    "reason": "CUDA_UNAVAILABLE",
                    "requested_device": requested,
                    "policy": policy.value,
                    "cpu_fallback": "disabled",
                },
            )
        try:
            probe = torch.zeros(1, device=self._device_target())
            probe.add_(1.0)
            del probe
        except BaseException as error:
            reason = "CUDA_OUT_OF_MEMORY" if _is_out_of_memory(error) else "CUDA_INIT_FAILED"
            raise CatalogError(
                EMBEDDING_DEVICE_UNAVAILABLE,
                "the CUDA device of the Qwen provider is not usable",
                details={"reason": reason, "cpu_fallback": "disabled"},
            ) from error
        return DeviceResolution(
            requested_device=requested,
            device="cuda",
            provider=PYTORCH_CUDA_PROVIDER,
            policy=policy,
            is_degradation=False,
            reason="explicit CUDA request" if requested else f"default for {policy.value}",
            available_providers=available,
            precision=self._config.precision,
            strict_fp32=False,
            cpu_fallback_disabled=True,
        )

    def _device_target(self) -> str:
        if self._resolution is not None and self._resolution.device == "cpu":
            return "cpu"
        return f"cuda:{int(self._config.cuda_index)}"

    def _assert_placement(self) -> None:
        assert self._model is not None
        expected_dtype = (
            self._torch.float16 if self._spec.precision == "FP16" else self._torch.float32
        )
        device = self._device_target()
        for name, parameter in self._model.named_parameters():
            if str(parameter.device) != device:
                raise CatalogError(
                    EMBEDDING_DEVICE_UNAVAILABLE,
                    "a model parameter is not on the resolved device",
                    details={
                        "parameter": name,
                        "device": str(parameter.device),
                        "expected": device,
                    },
                )
            if self._spec.precision == "FP16" and parameter.dtype.is_floating_point:
                if parameter.dtype != expected_dtype:
                    raise CatalogError(
                        EMBEDDING_DEVICE_UNAVAILABLE,
                        "a model parameter has an unexpected precision",
                        details={"parameter": name, "dtype": str(parameter.dtype)},
                    )

    def health(self) -> dict[str, Any]:
        return {
            "loaded": self._model is not None and self._processor is not None,
            "spec": self._spec.as_dict(),
            "device": self._resolution.as_dict() if self._resolution else None,
            "runtime": self._runtime_document(),
            "verification": self._verification,
            "reference": {
                "repository": REFERENCE_REPOSITORY,
                "attribution": str(self.manifest.get("reference", {}).get("note") or ""),
            },
        }

    def _runtime_document(self) -> dict[str, Any] | None:
        """The runtime facts plus this process's CUDA peak, read from PyTorch itself.

        The peak is measured, not requested: it is ``torch.cuda``'s own ``max_memory_*``
        counters for this worker process, which is what makes an OOM/near-OOM observation
        comparable across runs instead of an estimate.
        """
        if self._runtime is None:
            return None
        document = self._runtime.as_dict()
        torch = self._torch
        if torch is not None:
            try:
                if torch.cuda.is_available():
                    document["peak_vram"] = {
                        "allocated_bytes": int(torch.cuda.max_memory_allocated()),
                        "reserved_bytes": int(torch.cuda.max_memory_reserved()),
                        "device": str(self._device_target()),
                    }
            except Exception:  # pragma: no cover - a counter read must never fail a run
                document["peak_vram"] = None
        return document

    def provider_usage(self) -> dict[str, Any]:
        """Per-role execution facts. PyTorch has no ONNX node profile, so this is explicit."""
        return {
            "image": {"backend": "pytorch", "profiling": "not_applicable"},
            "text": {"backend": "pytorch", "profiling": "not_applicable"},
        }

    # -------------------------------------------------------------------------- encoding
    def encode_text(self, text: str) -> list[float]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")
        return self._encode(
            content=[{"type": "text", "text": text}], instruction=self._instruction["query"]
        )

    def encode_image(self, image_path: Path | str) -> list[float]:
        image = self._read_local_image(image_path)
        return self._encode(
            content=[{"type": "image", "image": image}],
            instruction=self._instruction["image"],
            images=[image],
        )

    def encode_queries(self, texts: Sequence[str], cancellation: Any = None) -> list[list[float]]:
        return [self.encode_text(str(text)) for text in texts]

    def encode_images(
        self, asset_inputs: Sequence[Any], cancellation: Any = None
    ) -> list[list[float]]:
        return [self.encode_image(item) for item in asset_inputs]

    def _read_local_image(self, image_path: Path | str) -> Image.Image:
        """One local, fully decoded, orientation-corrected RGB image; never a remote input."""
        text = str(image_path)
        lowered = text.strip().lower()
        if "://" in lowered:
            raise CatalogError(
                EMBEDDING_MANIFEST_INVALID,
                "this adapter only accepts local image files",
                details={"reason": "REMOTE_INPUT_REJECTED"},
            )
        path = Path(text)
        if not path.is_file():
            raise CatalogError(
                EMBEDDING_WEIGHTS_MISSING,
                "the image to encode does not exist",
                details={"reason": "IMAGE_MISSING"},
            )
        with Image.open(path) as source:
            return ImageOps.exif_transpose(source).convert("RGB")

    def _encode(
        self,
        *,
        content: list[dict[str, Any]],
        instruction: str,
        images: list[Image.Image] | None = None,
    ) -> list[float]:
        if self._model is None or self._processor is None:
            raise RuntimeError("adapter is not loaded")
        torch = self._torch
        conversation = [
            {"role": "system", "content": [{"type": "text", "text": str(instruction)}]},
            {"role": "user", "content": content},
        ]
        text = self._processor.apply_chat_template(
            [conversation], add_generation_prompt=True, tokenize=False
        )
        arguments: dict[str, Any] = {
            "text": text,
            "truncation": True,
            "max_length": int(self._spec.token_limit),
            "padding": True,
            "return_tensors": "pt",
        }
        if images is not None:
            arguments["images"] = images
            arguments["min_pixels"] = self._min_pixels
            arguments["max_pixels"] = self._max_pixels
        try:
            inputs = self._processor(**arguments)
            inputs = {key: value.to(self._device_target()) for key, value in inputs.items()}
            with torch.no_grad():
                outputs = self._model(**inputs)
            pooled = _pool_last(outputs.last_hidden_state, inputs["attention_mask"])
            vector = pooled[0][: int(self._spec.dimension)].to(torch.float32)
        except CatalogError:
            raise
        except BaseException as error:
            if _is_out_of_memory(error):
                raise CatalogError(
                    EMBEDDING_DEVICE_UNAVAILABLE,
                    "the CUDA device ran out of memory while encoding",
                    details={"reason": "CUDA_OUT_OF_MEMORY", "cpu_fallback": "disabled"},
                ) from error
            raise CatalogError(
                EMBEDDING_DEVICE_UNAVAILABLE,
                "the Qwen image-text encoder failed",
                details={"reason": type(error).__name__, "cpu_fallback": "disabled"},
            ) from error
        return _normalized_vector(np.asarray(vector.detach().cpu().numpy(), dtype=np.float32))

    def close(self) -> None:
        """Drop the model and its CUDA memory; safe to call twice and never raises."""
        model, self._model = self._model, None
        self._processor = None
        if model is not None:
            del model
        torch = self._torch
        if torch is not None:
            try:
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:  # pragma: no cover - shutdown must not raise
                pass
        self._resolution = None
        self._runtime = None


def _pool_last(hidden: Any, attention_mask: Any) -> Any:
    """The official last-token pooling: the embedding of the last real token of each row."""
    flipped = attention_mask.flip(dims=[1])
    last_one_positions = flipped.argmax(dim=1)
    columns = attention_mask.shape[1] - last_one_positions - 1
    rows = _arange(hidden.shape[0], hidden)
    return hidden[rows, columns]


def _arange(count: int, hidden: Any) -> Any:
    import torch

    return torch.arange(int(count), device=hidden.device)


def _normalized_vector(value: np.ndarray) -> list[float]:
    vector = np.asarray(value, dtype=np.float32).reshape(-1)
    if not np.all(np.isfinite(vector)):
        raise CatalogError(
            EMBEDDING_DEVICE_UNAVAILABLE,
            "the model returned a vector with a non-finite value",
            details={"reason": "NON_FINITE_VECTOR"},
        )
    norm = float(np.linalg.norm(vector))
    if norm == 0.0:
        raise CatalogError(
            EMBEDDING_DEVICE_UNAVAILABLE,
            "the model returned a zero vector",
            details={"reason": "ZERO_VECTOR"},
        )
    return (vector / norm).astype(np.float32).tolist()


def _import_torch() -> Any:
    try:
        import torch
    except Exception as error:  # noqa: BLE001 - a missing runtime is a structured refusal
        raise CatalogError(
            EMBEDDING_RUNTIME_MISSING,
            "the PyTorch runtime of the Qwen provider is not installed",
            details={"reason": "TORCH_MISSING"},
        ) from error
    return torch


def _import_transformers() -> Any:
    try:
        import transformers
        from transformers.models.qwen3_vl import modeling_qwen3_vl  # noqa: F401
    except Exception as error:  # noqa: BLE001
        raise CatalogError(
            EMBEDDING_RUNTIME_MISSING,
            "this build's Transformers version cannot run Qwen3-VL",
            details={"reason": "TRANSFORMERS_MISSING"},
        ) from error
    return transformers


def _model_shell() -> Any:
    """Our own ``Qwen3VLModel`` shell, mirroring the pinned reference implementation.

    The upstream embedding model is a ``Qwen3VLForConditionalGeneration`` minus the language
    head, and the checkpoint keeps the ``model.`` prefix. Wrapping ``Qwen3VLModel`` in a
    ``Qwen3VLPreTrainedModel`` (whose base prefix is ``model``) therefore loads the published
    weights as-is, exactly like the pinned reference script - without importing that script.
    """
    from transformers.models.qwen3_vl.modeling_qwen3_vl import (
        Qwen3VLModel,
        Qwen3VLPreTrainedModel,
    )

    class Qwen3VLForEmbedding(Qwen3VLPreTrainedModel):  # type: ignore[misc, valid-type]
        _checkpoint_conversion_mapping: dict[str, str] = {}
        accepts_loss_kwargs = False

        def __init__(self, config: Any) -> None:
            super().__init__(config)
            self.model = Qwen3VLModel(config)
            self.post_init()

        def forward(self, **kwargs: Any) -> Any:
            return self.model(**kwargs)

    Qwen3VLForEmbedding.__name__ = "Qwen3VLForEmbedding"
    return Qwen3VLForEmbedding


def _package_version(name: str) -> str:
    import importlib.metadata

    try:
        return str(importlib.metadata.version(name))
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - optional dependency
        return ""


def _is_out_of_memory(error: BaseException) -> bool:
    if type(error).__name__ in ("OutOfMemoryError", "CudaError"):
        return True
    return "out of memory" in str(error).lower()


__all__ = [
    "PYTORCH_CPU_PROVIDER",
    "PYTORCH_CUDA_PROVIDER",
    "REFERENCE_REPOSITORY",
    "AdapterSpec",
    "Qwen3VLEmbeddingAdapter",
    "QwenRuntimeConfig",
    "RuntimeFacts",
    "verify_pinned_files",
]
