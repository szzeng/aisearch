"""Single-image RAM++ inference using the existing, lazy-loaded PyTorch runtime.

Only locally exported, verified packages are accepted. The caller owns input revision,
orientation-normalized decoding, GPU scheduling and persistent result publication.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from photo_search.catalog.errors import CatalogError
from photo_search.tagging.model import CLASS_COUNT, PREPROCESS, RUNTIME, TaggerModel


@dataclass(frozen=True)
class TagPrediction:
    model_id: str
    scores: list[float]
    selected_indices: tuple[int, ...]


def preprocess_image(image: Any, torch: Any) -> Any:
    """Match official PIL Resize/ToTensor/Normalize, without importing torchvision."""
    import numpy as np
    from PIL import Image

    if (not isinstance(image, Image.Image) or image.mode != "RGB"
            or min(image.size) < 1 or image.width * image.height > PREPROCESS["max_pixels"]):
        raise CatalogError("TAGGER_INPUT_INVALID", "tagger requires bounded, normalized RGB pixels")
    resized = image.resize((384, 384), resample=Image.Resampling.BILINEAR)
    try:
        array = np.array(resized, dtype=np.uint8, copy=True)
    finally:
        resized.close()
    tensor = torch.from_numpy(array).permute((2, 0, 1)).contiguous().to(dtype=torch.float32)
    tensor = tensor.div(255)
    mean = torch.tensor(PREPROCESS["mean"], dtype=torch.float32).view(-1, 1, 1)
    std = torch.tensor(PREPROCESS["std"], dtype=torch.float32).view(-1, 1, 1)
    return tensor.sub_(mean).div_(std).unsqueeze(0)


class RamTagger:
    def __init__(self, info: TaggerModel, torch: Any, model: Any):
        self.info, self._torch, self._model = info, torch, model

    @classmethod
    def load(cls, info: TaggerModel, *, device: str = "cuda:0") -> RamTagger:
        if device != "cuda:0":
            raise CatalogError("TAGGER_DEVICE_UNAVAILABLE", "this tagger is qualified only for CUDA 0")
        info.verify()
        model, torch = None, None
        loaded = False
        try:
            import torch

            if torch.__version__ != RUNTIME["torch"] or not torch.cuda.is_available():
                raise CatalogError("TAGGER_DEVICE_UNAVAILABLE",
                                   "RAM++ requires the locked PyTorch CUDA runtime")
            model = torch.jit.load(str(info.model_path), map_location="cuda:0")
            model = model.eval()
            # Refuse a changed file rather than accepting a half-updated model package.
            info.verify()
            result = cls(info, torch, model)
            loaded = True
            return result
        except CatalogError:
            raise
        except (ImportError, RuntimeError, OSError) as error:
            raise CatalogError("TAGGER_MODEL_LOAD_FAILED", "cannot load local RAM++ graph") from error
        finally:
            if not loaded and torch is not None:
                # Exception tracebacks retain locals. Drop the graph before the caller
                # releases its GPU gate, including a package change detected after load.
                model = None
                if torch.cuda.is_available():
                    try:
                        torch.cuda.empty_cache()
                    except RuntimeError:
                        pass  # Preserve the original load/identity error.

    def predict(self, image: Any) -> TagPrediction:
        if self._model is None:
            raise CatalogError("TAGGER_MODEL_CLOSED", "tagger runtime has been closed")
        torch = self._torch
        try:
            tensor = preprocess_image(image, torch).to(device="cuda:0", dtype=torch.float16)
            with torch.inference_mode(), torch.jit.optimized_execution(False):
                output = self._model(tensor)
            if (tuple(output.shape) != (1, CLASS_COUNT) or output.device.type != "cuda"
                    or output.dtype != torch.float16):
                raise CatalogError("TAGGER_OUTPUT_INVALID", "tagger returned an unexpected tensor")
            scores = output.detach().float().cpu().numpy()[0]
            import numpy as np

            if not np.isfinite(scores).all() or np.any((scores < 0) | (scores > 1)):
                raise CatalogError("TAGGER_OUTPUT_INVALID", "tagger returned invalid scores")
            selected = tuple(item.index for item in self.info.classes
                             if scores[item.index] > item.threshold)
            return TagPrediction(self.info.model_id, scores.tolist(), selected)
        except CatalogError:
            raise
        except (RuntimeError, ValueError, MemoryError) as error:
            raise CatalogError("TAGGER_INFERENCE_FAILED", "RAM++ inference failed; CPU fallback disabled") from error

    def close(self) -> None:
        self._model = None
        if self._torch.cuda.is_available():
            self._torch.cuda.empty_cache()
