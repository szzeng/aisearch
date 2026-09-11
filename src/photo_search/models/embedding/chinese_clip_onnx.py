"""Manifest-driven, CPU-only Chinese-CLIP ONNX adapter.

The module intentionally owns ONNX, tokenization and image preprocessing.  Its public
results are ordinary metadata plus vectors, so callers do not depend on tensor details.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime
from PIL import Image
from transformers import BertTokenizerFast


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

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class ChineseClipOnnxAdapter:
    """Load P0-03's frozen ONNX artifacts without importing PyTorch."""

    def __init__(self, model_dir: Path, manifest_path: Path) -> None:
        self.model_dir = model_dir
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        embedding = self.manifest["embedding"]
        model = self.manifest["model"]
        runtime = self.manifest["runtime"]
        preprocessing = self.manifest["preprocessing"]
        tokenizer = self.manifest["tokenizer"]
        digest = self.manifest["artifacts"]["onnx"]["image"]["sha256"][:16]
        self._spec = ProcessorSpec(
            processor_id=f"chinese-clip-vit-b-16@{model['resolved_revision'][:12]}",
            space_id=f"chinese-clip-vit-b-16:{digest}",
            dimension=int(embedding["dimension"]),
            metric=str(embedding["metric"]),
            normalization=str(embedding["normalization"]),
            model_revision=str(model["resolved_revision"]),
            backend=str(runtime["backend"]),
            device="CPU",
            image_size=int(preprocessing["image_size"]),
            token_limit=int(tokenizer["max_length"]),
        )
        self._mean = np.asarray(preprocessing["mean"], dtype=np.float32).reshape(3, 1, 1)
        self._std = np.asarray(preprocessing["std"], dtype=np.float32).reshape(3, 1, 1)
        self._image_path = model_dir / self.manifest["artifacts"]["onnx"]["image"]["path"]
        self._text_path = model_dir / self.manifest["artifacts"]["onnx"]["text"]["path"]
        self._tokenizer_path = model_dir / self.manifest["tokenizer"]["vocab_file"]
        self._image_session: onnxruntime.InferenceSession | None = None
        self._text_session: onnxruntime.InferenceSession | None = None
        self._tokenizer: BertTokenizerFast | None = None

    def spec(self) -> ProcessorSpec:
        return self._spec

    def load(self) -> None:
        for path in (self._image_path, self._text_path, self._tokenizer_path):
            if not path.is_file():
                raise FileNotFoundError(f"required model artifact not found: {path}")
        self._image_session = onnxruntime.InferenceSession(
            str(self._image_path), providers=["CPUExecutionProvider"]
        )
        self._text_session = onnxruntime.InferenceSession(
            str(self._text_path), providers=["CPUExecutionProvider"]
        )
        self._tokenizer = BertTokenizerFast(vocab_file=str(self._tokenizer_path), do_lower_case=True)

    def health(self) -> dict[str, Any]:
        return {"loaded": self._image_session is not None, "spec": self.spec().as_dict()}

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
