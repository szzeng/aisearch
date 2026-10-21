"""Local-only model runtime for the WeMM pilot; never a production provider."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from tools.evaluation.wemm_pilot_core import normalize_prefix

ROOT = Path(__file__).resolve().parents[2]
WEMM_LOCK = Path(__file__).with_name("wemm_model_lock.json")
QWEN_MANIFEST = ROOT / "tools/prepare_models/manifests/qwen3-vl-embedding-2b-exif-v2.json"
DECODE_PATHS = sorted((ROOT / "src/photo_search/media").glob("*.py"))


class PilotError(ValueError):
    """Stable reason codes safe to print without disclosing private input paths."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def package_versions() -> dict[str, str]:
    result = {"python": platform.python_version()}
    for name in ("torch", "torchvision", "transformers", "qwen-vl-utils", "numpy", "pillow",
                 "pillow-heif", "rawpy", "safetensors", "tokenizers", "huggingface-hub"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = "MISSING"
    return result


def decoder_identity() -> dict[str, Any]:
    versions = package_versions()
    return {"policy": "load_normalized-full-exif-rgb-v1",
            "files": {path.name: sha256(path) for path in DECODE_PATHS},
            "packages": {key: versions[key] for key in ("pillow", "pillow-heif", "rawpy")}}


def source_identity() -> dict[str, str]:
    paths = [*Path(__file__).parent.glob("wemm_pilot*.py"),
             Path(__file__).with_name("wemm_quality_pilot.py"),
             ROOT / "src/photo_search/models/embedding/qwen3_vl.py",
             ROOT / "src/photo_search/models/embedding/base.py"]
    return {str(path.relative_to(ROOT)).replace("\\", "/"): sha256(path)
            for path in sorted(set(paths))}


def configure_offline(cache_dir: Path) -> None:
    """Call before importing Torch/Transformers; the caller validates cache_dir."""
    for variable, child in {"HF_HOME": "hf", "HF_MODULES_CACHE": "hf-modules",
                            "TORCH_HOME": "torch", "TEMP": "tmp", "TMP": "tmp"}.items():
        destination = cache_dir / child
        if (destination.is_symlink() or destination.is_junction()
                or not destination.resolve().is_relative_to(cache_dir.resolve())):
            raise PilotError("CACHE_DIRECTORY_LINK_REFUSED")
        destination.mkdir(parents=True, exist_ok=True)
        os.environ[variable] = str(destination)
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
    os.environ["UV_CACHE_DIR"] = str(ROOT / ".uv-cache")


def verify_model(model: str, model_dir: Path) -> dict[str, Any]:
    """Hash every required file before any remote-code or model import."""
    if not model_dir.is_dir():
        raise PilotError("MODEL_DIRECTORY_MISSING")
    if model == "wemm":
        lock = json.loads(WEMM_LOCK.read_text(encoding="utf-8"))
        expected = lock["files"]
    elif model == "qwen":
        manifest = json.loads(QWEN_MANIFEST.read_text(encoding="utf-8"))
        expected = {item["name"]: item for item in manifest["weights"]}
        lock = {"model_id": manifest["model"]["id"],
                "revision": manifest["model"]["resolved_revision"], "manifest": manifest}
    else:
        raise PilotError("UNKNOWN_MODEL")
    actual = {}
    # Fail missing/size checks before hashing multiple GB of already-present weights.
    for name, record in expected.items():
        if Path(name).name != name or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]):
            raise PilotError("MODEL_LOCK_INVALID")
        path = model_dir / name
        if not path.is_file():
            raise PilotError("MODEL_FILE_MISSING:" + name)
        if path.stat().st_size != int(record["bytes"]):
            raise PilotError("MODEL_FILE_SIZE_MISMATCH:" + name)
    for path in sorted(model_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name not in expected:
            if (model == "wemm" or path.suffix not in {".json", ".jinja", ".txt", ".model"}
                    or path.stat().st_size > 64 * 1024 * 1024):
                raise PilotError("UNPINNED_MODEL_FILE:" + path.name)
        digest = sha256(path)
        if path.name in expected and digest != expected[path.name]["sha256"]:
            raise PilotError("MODEL_FILE_HASH_MISMATCH:" + path.name)
        actual[path.name] = {"sha256": digest, "bytes": path.stat().st_size}
    return {**lock, "verified_files": actual}


def decode_image(path: Path, expected_sha: str) -> tuple[Any, dict[str, Any]]:
    """Read the same original bytes and full EXIF-normalized RGB input for both models."""
    if "://" in str(path) or not path.is_file():
        raise PilotError("IMAGE_MISSING_OR_REMOTE")
    before = path.stat()
    if sha256(path) != expected_sha:
        raise PilotError("IMAGE_CONTENT_CHANGED")
    from photo_search.media.decode import load_normalized

    decoded = load_normalized(path, long_edge=2 ** 31 - 1, jpeg_draft=False)
    try:
        rgb = decoded.image.convert("RGB")
    finally:
        decoded.image.close()
    after = path.stat()
    if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
            (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or sha256(path) != expected_sha):
        rgb.close()
        raise PilotError("IMAGE_CHANGED_DURING_DECODE")
    return rgb, {"width": rgb.width, "height": rgb.height,
                 "source_format": decoded.source_format, "exif_orientation": decoded.orientation,
                 "rgb_sha256": hashlib.sha256(rgb.tobytes()).hexdigest()}


def peak_ram_bytes() -> int | None:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "peak_working_set", "working_set", "peak_paged", "paged", "peak_nonpaged",
                    "nonpaged", "pagefile", "peak_pagefile")]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
        memory_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if memory_info(
                kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return int(counters.peak_working_set)
        return None
    import resource
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if platform.system() == "Darwin" else value * 1024)


class ModelRuntime:
    """One explicitly selected CUDA model per process. Instantiation loads nothing."""

    def __init__(self, model: str, model_dir: Path, device: str, dtype: str,
                 manifest_path: Path | None = None):
        self.model_name, self.model_dir, self.device, self.dtype = model, model_dir, device, dtype
        self.manifest_path = manifest_path
        self.torch = self.model = self.processor = self.adapter = None

    def load(self) -> dict[str, Any]:
        expected = "5.2.0" if self.model_name == "wemm" else "4.57.0"
        versions = package_versions()
        if (versions["python"] != "3.12.10" or versions["transformers"] != expected
                or versions["qwen-vl-utils"] != "0.0.14"):
            raise PilotError("EXPERIMENT_RUNTIME_VERSION_MISMATCH")
        if not re.fullmatch(r"cuda(?::\d+)?", self.device):
            raise PilotError("CUDA_REQUIRED")
        if self.model_name == "qwen" and self.dtype != "fp16":
            raise PilotError("QWEN_REQUIRES_FP16")
        import torch

        self.torch = torch
        if not torch.cuda.is_available():
            raise PilotError("CUDA_UNAVAILABLE")
        index = torch.device(self.device).index
        self.device = f"cuda:{0 if index is None else index}"
        torch.cuda.set_device(self.device)
        if self.dtype == "bf16" and not torch.cuda.is_bf16_supported():
            raise PilotError("BF16_UNSUPPORTED_USE_EXPLICIT_FP16_EXPERIMENT")
        torch.cuda.reset_peak_memory_stats(self.device)
        if self.model_name == "qwen":
            from photo_search.models.embedding.qwen3_vl import (
                Qwen3VLEmbeddingAdapter,
                QwenRuntimeConfig,
            )
            self.adapter = Qwen3VLEmbeddingAdapter(
                self.model_dir, self.manifest_path,
                QwenRuntimeConfig(requested_device=self.device, cuda_index=int(self.device[5:]),
                                  precision="FP16", verify_hashes=True),
            )
            self.adapter.load()
            self.model = self.adapter._model
        else:
            from transformers import AutoModel, AutoProcessor

            self.processor = AutoProcessor.from_pretrained(
                str(self.model_dir), trust_remote_code=True, local_files_only=True,
            )
            self.model = AutoModel.from_pretrained(
                str(self.model_dir), trust_remote_code=True, local_files_only=True,
                dtype=torch.bfloat16 if self.dtype == "bf16" else torch.float16,
            ).to(self.device).eval()
        counts = Counter()
        for parameter in self.model.parameters():
            if str(parameter.device) != self.device:
                raise PilotError("CPU_OR_OFFLOAD_REFUSED")
            counts[str(parameter.dtype)] += parameter.numel()
        requested_dtype = str(torch.bfloat16 if self.dtype == "bf16" else torch.float16)
        # The official recurrent-attention implementation may retain FP32 state
        # parameters. Record that mixture; refuse a silently all-FP32 model.
        if requested_dtype not in counts or set(counts) - {requested_dtype, "torch.float32"}:
            raise PilotError("MODEL_DTYPE_MISMATCH")
        self.sync()
        return {"device": self.device, "gpu": torch.cuda.get_device_name(self.device),
                "vram_total_bytes": torch.cuda.get_device_properties(self.device).total_memory,
                "requested_dtype": self.dtype, "parameter_dtypes": dict(counts),
                "cuda_version": torch.version.cuda, "packages": versions}

    def sync(self) -> None:
        if self.torch is not None:
            self.torch.cuda.synchronize(self.device)

    def encode(self, *, text: str | None = None, image: Any = None) -> np.ndarray:
        if (text is None) == (image is None):
            raise PilotError("EXACTLY_ONE_INPUT_REQUIRED")
        if self.model_name == "qwen":
            if text is not None:
                value = self.adapter.encode_text(text)
            else:
                value = self.adapter._encode(
                    content=[{"type": "image", "image": image}], images=[image],
                    instruction=self.adapter._instruction["image"],
                )
            return normalize_prefix(np.asarray([value], dtype=np.float32), 2048)
        from qwen_vl_utils import process_vision_info

        content = {"type": "text", "text": text} if text is not None else {
            "type": "image", "image": image}
        messages = [{"role": "user", "content": [content]}]
        prompt = self.processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=False,
        )
        images, videos, kwargs = process_vision_info(
            messages, image_patch_size=16, return_video_kwargs=True, return_video_metadata=True,
        )
        if videos is not None:
            raise PilotError("VIDEO_OUTSIDE_PILOT_SCOPE")
        inputs = self.processor(text=prompt, images=images, videos=None, video_metadata=None,
                                return_tensors="pt", **kwargs).to(self.device)
        last = int(inputs["attention_mask"][0].sum().item()) - 1
        if last < 0 or int(inputs["input_ids"][0, last].item()) != 248077:
            raise PilotError("WEMM_EMBEDDING_TOKEN_MISSING")
        with self.torch.inference_mode():
            result = self.model.embedding(**inputs).float().cpu().numpy()
        if result.shape != (1, 2048):
            raise PilotError("NATIVE_DIMENSION_MISMATCH")
        return normalize_prefix(result, 2048)

    def measured_encode(self, **kwargs: Any) -> tuple[np.ndarray, float]:
        self.sync()
        start = time.perf_counter()
        value = self.encode(**kwargs)
        self.sync()
        return value, (time.perf_counter() - start) * 1000

    def resources(self) -> dict[str, Any]:
        result = {"peak_ram_bytes": peak_ram_bytes(), "gpu_peak_allocated_bytes": None,
                  "gpu_peak_reserved_bytes": None}
        try:
            if self.torch is not None and self.torch.cuda.is_available():
                result.update(gpu_peak_allocated_bytes=int(
                    self.torch.cuda.max_memory_allocated(self.device)),
                    gpu_peak_reserved_bytes=int(self.torch.cuda.max_memory_reserved(self.device)))
        except Exception:
            result["gpu_counters_status"] = "UNAVAILABLE"
        return result

    def close(self) -> None:
        if self.adapter is not None:
            self.adapter.close()
        self.adapter = self.model = self.processor = None
        if self.torch is not None:
            try:
                self.torch.cuda.empty_cache()
            except Exception:
                pass  # Preserve the original load/encode error, including invalid CUDA index.
