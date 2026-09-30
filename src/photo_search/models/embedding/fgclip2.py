"""Offline FG-CLIP 2 Base inference; one model instance in the managed worker."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import CatalogError
from photo_search.media.decode import load_normalized
from photo_search.models.embedding.manifest import pinned_embedding_spec, verify_model_files
from photo_search.runtime.device import DevicePolicy, DeviceResolution, DeviceUnavailableError


@dataclass(frozen=True)
class RuntimeConfig:
    requested_device: str | None = None
    device_policy: DevicePolicy = DevicePolicy.CPU_ALLOWED


def resolve_torch_device(torch: Any, requested: str | None, policy: DevicePolicy) -> DeviceResolution:
    """Select a real accelerator; an unavailable explicit request never falls back."""
    policy = DevicePolicy(policy)
    accelerators = []
    if torch.cuda.is_available():
        accelerators.append("cuda")
    if torch.backends.mps.is_available():
        accelerators.append("mps")
    providers = tuple(f"torch-{device}" for device in [*accelerators, "cpu"])
    if requested not in (None, "auto", "cpu", "host", "cuda", "gpu", "mps"):
        raise ValueError(f"unsupported device: {requested}")
    if requested in ("cpu", "host") or (
        requested in (None, "auto") and policy == DevicePolicy.CPU_PREFERRED
    ):
        device = "cpu"
    elif requested in ("cuda", "mps"):
        device = requested
    else:
        device = next(iter(accelerators), "unavailable")
    if (device == "cpu" and policy == DevicePolicy.GPU_REQUIRED) or (
        device != "cpu" and device not in accelerators
    ):
        raise DeviceUnavailableError(
            requested_device=requested, policy=policy, available_providers=providers,
            reason="requested PyTorch device is unavailable",
        )
    return DeviceResolution(
        requested_device=requested, device=device, provider=f"torch-{device}", policy=policy,
        is_degradation=device == "cpu" and policy != DevicePolicy.CPU_PREFERRED,
        reason="explicit CPU request" if device == "cpu" else f"{device.upper()} selected",
        available_providers=providers, precision="FP32" if device == "cpu" else "FP16",
        strict_fp32=device == "cpu",
    )


class FGClip2Adapter:
    def __init__(self, model_dir: Path, manifest_path: Path, config: RuntimeConfig | None = None):
        self.model_dir = Path(model_dir)
        self.manifest_path = Path(manifest_path)
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.config = config or RuntimeConfig()
        self._model = None

    def load(self) -> None:
        os.environ["HF_MODULES_CACHE"] = str(self.model_dir.parent / "hf-modules")
        import torch
        from transformers import AutoImageProcessor, AutoModelForCausalLM, AutoTokenizer

        verify_model_files(self.model_dir, self.manifest)
        policy = DevicePolicy(self.config.device_policy)
        self.resolution = resolve_torch_device(torch, self.config.requested_device, policy)
        self._device = self.resolution.device
        cpu = self._device == "cpu"
        self._dtype = torch.float32 if cpu else torch.float16
        precision = self.resolution.precision
        # Custom model code is pinned and hash-checked above; inference never fetches code.
        local = dict(local_files_only=True, trust_remote_code=True)
        self._model = AutoModelForCausalLM.from_pretrained(
            str(self.model_dir), torch_dtype=self._dtype, attn_implementation="sdpa", **local
        ).eval().to(self._device)
        self._tokenizer = AutoTokenizer.from_pretrained(str(self.model_dir), **local)
        self._processor = AutoImageProcessor.from_pretrained(str(self.model_dir), **local)
        self._spec = pinned_embedding_spec(
            self.manifest_path, device_policy=policy, runtime_package_id=f"torch@{torch.__version__}",
            supported_devices=("cpu",) if cpu else ("gpu", "cpu"),
            supported_precisions=(precision,),
        )

    def spec(self):
        return self._spec

    def health(self) -> dict[str, Any]:
        import torch
        return {"loaded": self._model is not None, "spec": self.spec().to_envelope(),
                "device": self.resolution.as_dict(),
                "runtime": {"runtime_backend": "pytorch", "runtime_package": "torch",
                            "runtime_version": torch.__version__,
                            "providers": list(self.resolution.available_providers),
                            "cuda_available": torch.cuda.is_available(),
                            "mps_available": torch.backends.mps.is_available()}}

    def encode_text(self, text: str) -> list[float]:
        import torch
        maximum = int(self.manifest["tokenizer"]["max_length"])
        text = str(text).lower()
        length = len(self._tokenizer(text, truncation=False)["input_ids"])
        if length > maximum:
            raise CatalogError("VALIDATION_ERROR", "搜索描述过长，请缩短后重试")
        short = int(self.manifest["tokenizer"]["short_max_length"])
        walk = "short" if length <= short else "long"
        tokens = self._tokenizer([text], padding="max_length", max_length=short if walk == "short" else maximum,
                                 truncation=False, return_tensors="pt").to(self._device)
        with torch.inference_mode():
            return self._vector(self._model.get_text_features(**tokens, walk_type=walk))

    def encode_image(self, image_path: Path) -> list[float]:
        import torch
        preprocessing = self.manifest["preprocessing"]
        decoded = load_normalized(Path(image_path), long_edge=preprocessing["decode_long_edge"],
                                  max_pixels=60_000_000)
        try:
            inputs = self._processor(images=decoded.image.convert("RGB"),
                                     max_num_patches=preprocessing["max_num_patches"],
                                     return_tensors="pt").to(self._device)
            # Integer attention masks/spatial shapes must retain their dtype.
            inputs = {key: value.to(dtype=self._dtype) if value.is_floating_point() else value
                      for key, value in inputs.items()}
            with torch.inference_mode():
                return self._vector(self._model.get_image_features(**inputs))
        finally:
            decoded.image.close()

    def _vector(self, features) -> list[float]:
        import torch
        vector = features[0].float()
        norm = torch.linalg.vector_norm(vector)
        dimension = int(self.manifest["embedding"]["dimension"])
        if vector.shape != (dimension,) or not torch.isfinite(vector).all() or norm <= 0:
            raise ValueError("model returned an invalid embedding")
        return (vector / norm).cpu().tolist()

    def provider_usage(self) -> dict:
        import sys

        import torch
        usage = {"device": self.resolution.device, "backend": "pytorch"}
        if self._device == "mps":
            usage["mps_allocated_bytes"] = torch.mps.current_allocated_memory()
            usage["mps_driver_allocated_bytes"] = torch.mps.driver_allocated_memory()
        elif self._device == "cuda":
            usage["cuda_peak_allocated_bytes"] = torch.cuda.max_memory_allocated()
        if sys.platform != "win32":
            import resource
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            usage["peak_process_rss_bytes"] = rss if sys.platform == "darwin" else rss * 1024
        return usage
