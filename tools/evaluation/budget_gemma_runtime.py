"""Isolated EmbeddingGemma 2 text/image adapter; FP32 for pre-BF16 GPUs.

Requires Transformers 5.20.2 and SentenceTransformers 6.1.0 in a separate local
environment. Audio is disabled explicitly; no changes to production dependencies.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import numpy as np

from tools.evaluation.wemm_pilot_runtime import peak_ram_bytes, sha256
from tools.evaluation.wemm_quality_pilot import read_json

MODEL_ID = "google/embeddinggemma-2"
REVISION = "914f7f89142e33e77833254d9c9b90c3cef7303b"
LOCK = Path(__file__).with_name("budget_gemma_model_lock.json")


def verify_gemma(directory: Path) -> dict:
    lock = read_json(LOCK)
    if lock["model_id"] != MODEL_ID or lock["revision"] != REVISION:
        raise ValueError("fixed EmbeddingGemma identity mismatch")
    for name, record in lock["files"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or Path(name).is_absolute():
            raise ValueError("model lock path escapes model directory")
        if (not path.is_file() or path.stat().st_size != record["bytes"]
                or sha256(path) != record["sha256"]):
            raise ValueError("model content differs: " + name)
    found = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}
    if found != set(lock["files"]):
        raise ValueError("unverified model files")
    config = read_json(directory / "config.json")
    if config["model_type"] != "embedding_gemma2" or config["vision_soft_tokens_per_image"] != 280:
        raise ValueError("EmbeddingGemma architecture/image budget mismatch")
    return lock


class GemmaRuntime:
    def __init__(self, directory: Path, dtype: str):
        if dtype != "fp32":
            # Official model guidance: FP16 may silently corrupt embeddings.
            raise ValueError("EmbeddingGemma2 probe requires FP32; FP16 is unsafe")
        self.directory = directory
        self.torch = self.model = None

    def load(self) -> dict:
        verify_gemma(self.directory)
        import sentence_transformers
        import torch
        import transformers
        from sentence_transformers import SentenceTransformer

        if (transformers.__version__ != "5.20.2"
                or sentence_transformers.__version__ != "6.1.0"):
            raise ValueError("use the isolated Transformers5.20.2/ST6.1.0 environment")
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA required; CPU/offload is not implicit")
        self.torch = torch
        torch.cuda.set_device(0)
        torch.cuda.reset_peak_memory_stats()
        self.model = SentenceTransformer(
            str(self.directory), device="cuda:0", local_files_only=True, trust_remote_code=False,
            config_kwargs={"audio_config": None},
            model_kwargs={"torch_dtype": torch.float32, "attn_implementation": "eager"},
        ).eval()
        if any(p.device != torch.device("cuda:0") or p.dtype != torch.float32
               for p in self.model.parameters()):
            raise ValueError("implicit dtype/offload refused")
        native = self.model[0].auto_model
        count = sum(p.numel() for p in self.model.parameters())
        if (native.config.audio_config is not None or native.config.vision_config is None
                or not 400_000_000 <= count <= 500_000_000):
            raise ValueError("text/image-only model configuration not honored")
        self.sync()
        props = torch.cuda.get_device_properties(0)
        return {"torch_version": torch.__version__, "transformers": transformers.__version__,
                "sentence_transformers": sentence_transformers.__version__,
                "cuda_version": torch.version.cuda, "gpu_name": props.name,
                "compute_capability": [props.major, props.minor],
                "total_vram_bytes": props.total_memory, "device": "cuda:0", "dtype": "float32",
                "parameter_count": count, "model_class": type(native).__name__,
                "model_source_sha256": sha256(Path(inspect.getfile(type(native)))),
                "audio_encoder": False, "vision_soft_tokens_per_image": 280,
                "query_prompt_name": "SearchQuery", "image_prompt": None}

    def sync(self):
        self.torch.cuda.synchronize()

    def encode(self, *, text: str | None = None, image=None) -> np.ndarray:
        if (text is None) == (image is None):
            raise ValueError("one input required")
        with self.torch.inference_mode():
            if text is not None:
                vector = self.model.encode([text], prompt_name="SearchQuery", batch_size=1,
                                           show_progress_bar=False, convert_to_numpy=True,
                                           normalize_embeddings=True)
            else:
                vector = self.model.encode([{"image": image}], prompt=None, batch_size=1,
                                           show_progress_bar=False, convert_to_numpy=True,
                                           normalize_embeddings=True)
        return np.asarray(vector, dtype=np.float32)

    def resources(self):
        return {"peak_ram_bytes": peak_ram_bytes(),
                "gpu_peak_allocated_bytes": self.torch.cuda.max_memory_allocated(),
                "gpu_peak_reserved_bytes": self.torch.cuda.max_memory_reserved()}

    def close(self):
        self.model = None
        if self.torch is not None:
            self.torch.cuda.empty_cache()
