"""Pinned FG-CLIP2 Base experiment adapter; not a production provider.

The upstream Python implementation is loaded only after complete local checksum
verification. Its eager attention, native 768D output and short-text pooling are
kept intact. Models, tokenizer caches and remote-module cache stay project-local.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import numpy as np

from tools.evaluation.wemm_pilot_runtime import peak_ram_bytes, sha256
from tools.evaluation.wemm_quality_pilot import read_json

MODEL_ID = "qihoo360/fg-clip2-base"
REVISION = "430fbc8a912c86fd4de601381b6245a0edab22f0"
LOCK = Path(__file__).with_name("budget_fgclip_model_lock.json")


def official_patch_budget(image) -> int:
    """Pinned model README's determine_max_value; use decoded size without crops."""
    width, height = image.size
    patches = (width // 16) * (height // 16)
    if patches > 784:
        return 1024
    if patches > 576:
        return 784
    if patches > 256:
        return 576
    if patches > 128:
        return 256
    return 128


def verify_fgclip(directory: Path) -> dict:
    lock = read_json(LOCK)
    if lock["model_id"] != MODEL_ID or lock["revision"] != REVISION:
        raise ValueError("fixed FG-CLIP2 identity mismatch")
    for basename, record in lock["files"].items():
        if Path(basename).name != basename:
            raise ValueError("invalid model lock filename")
        path = directory / basename
        if (not path.is_file() or path.stat().st_size != record["bytes"]
                or sha256(path) != record["sha256"]):
            raise ValueError("model content differs: " + basename)
    if any(path.name not in lock["files"] for path in directory.iterdir()):
        raise ValueError("unverified file beside executable model code")
    config = read_json(directory / "config.json")
    if (config["model_type"] != "fgclip2" or config["text_config"]["hidden_size"] != 768
            or config["vision_config"]["hidden_size"] != 768
            or config["text_config"]["max_position_embeddings"] != 64):
        raise ValueError("FG-CLIP2 architecture mismatch")
    return lock


class FgclipRuntime:
    def __init__(self, directory: Path, dtype: str, *, adaptive: bool = False):
        if dtype not in ("fp16", "fp32"):
            raise ValueError("explicit fp16/fp32 required")
        self.directory, self.dtype = directory, dtype
        self.adaptive = adaptive
        self.torch = self.model = self.processor = self.tokenizer = None

    def patch_budget(self, image) -> int:
        return official_patch_budget(image) if self.adaptive else 256

    def load(self) -> dict:
        # Never execute unverified Python from the model directory.
        verify_fgclip(self.directory)
        import torch
        from transformers import AutoImageProcessor, AutoModelForCausalLM, AutoTokenizer

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA required; CPU/offload is not implicit")
        self.torch = torch
        torch.cuda.set_device(0)
        torch.cuda.reset_peak_memory_stats()
        self.processor = AutoImageProcessor.from_pretrained(
            self.directory, local_files_only=True, trust_remote_code=False, use_fast=True)
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.directory, local_files_only=True, trust_remote_code=False)
        expected = torch.float16 if self.dtype == "fp16" else torch.float32
        self.model = AutoModelForCausalLM.from_pretrained(
            self.directory, local_files_only=True, trust_remote_code=True,
            use_safetensors=True, torch_dtype=expected, attn_implementation="eager",
        ).to("cuda:0").eval()
        if any(p.device != torch.device("cuda:0") or p.dtype != expected
               for p in self.model.parameters()):
            raise ValueError("implicit dtype/offload refused")
        if any(b.device != torch.device("cuda:0") for b in self.model.buffers()):
            raise ValueError("buffer device mismatch")
        self.sync()
        props = torch.cuda.get_device_properties(0)
        return {"torch_version": torch.__version__, "cuda_version": torch.version.cuda,
                "gpu_name": props.name, "compute_capability": [props.major, props.minor],
                "total_vram_bytes": props.total_memory, "device": "cuda:0",
                "dtype": str(expected), "parameter_count": sum(p.numel()
                                                                       for p in self.model.parameters()),
                "model_class": type(self.model).__name__,
                "image_processor_class": type(self.processor).__name__,
                "tokenizer_class": type(self.tokenizer).__name__,
                "processor_source_sha256": sha256(Path(inspect.getfile(type(self.processor)))),
                "model_source_sha256": sha256(Path(inspect.getfile(type(self.model)))),
                "tokenizer_model_input_names": self.tokenizer.model_input_names,
                "attention_mask_policy": "omit_text_mask_official_tokenizer_default"}

    def sync(self):
        self.torch.cuda.synchronize()

    def encode(self, *, text: str | None = None, image=None) -> np.ndarray:
        if (text is None) == (image is None):
            raise ValueError("one input required")
        if text is not None:
            inputs = self.tokenizer([text.lower()], padding="max_length", max_length=64,
                                    truncation=True, return_attention_mask=False, return_tensors="pt")
        else:
            inputs = self.processor(images=image, max_num_patches=self.patch_budget(image), patch_size=16,
                                    return_tensors="pt")
        dtype = next(self.model.parameters()).dtype
        inputs = {key: value.to(device="cuda:0", dtype=dtype if value.is_floating_point()
                                else value.dtype) for key, value in inputs.items()}
        with self.torch.inference_mode():
            if text is not None:
                vector = self.model.get_text_features(**inputs, walk_type="short")
            else:
                vector = self.model.get_image_features(**inputs)
        return vector.detach().float().cpu().numpy()

    def resources(self):
        return {"peak_ram_bytes": peak_ram_bytes(),
                "gpu_peak_allocated_bytes": self.torch.cuda.max_memory_allocated(),
                "gpu_peak_reserved_bytes": self.torch.cuda.max_memory_reserved()}

    def close(self):
        self.model = self.processor = self.tokenizer = None
        if self.torch is not None:
            self.torch.cuda.empty_cache()
