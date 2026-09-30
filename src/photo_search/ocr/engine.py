"""The pinned PP-OCRv6 small pipeline: bounded high-resolution input, slicing and geometry.

Three decisions live here, and each of them is deliberate:

* **Never the thumbnail size.** The working raster is bounded by ``MAX_LONG_EDGE`` (2000 px)
  and then, if it is still too large to hand to one detector pass, cut into overlapping
  slices. A 320/448 px raster is the *grid* contract of P1-03 and would erase exactly the
  small text P3-01 exists to find.
* **Low-confidence regions stay.** The pinned engine's ``text_score`` default (0.5) would
  drop them before anyone could see them; architecture §8.2 requires them to be visible and
  queryable with their score, so the pinned config is overridden to ``0.0`` for this
  processor only.
* **Deterministic geometry.** Slice boxes are mapped back to the source coordinates, the
  overlap band is de-duplicated by IoU, and the survivors are ordered by a deterministic
  line/column rule. The same pixels therefore always produce the same artifact bytes.

Nothing in this module writes state: it turns one already-decoded image into one
``OcrDocument``, and every failure is a structured exception the executor translates.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import (
    OCR_DEADLINE_EXCEEDED,
    OCR_INPUT_TOO_LARGE,
    OCR_RESULT_TOO_LARGE,
    RUNTIME_CAPABILITY_UNAVAILABLE,
    RUNTIME_RESOLUTION_MISMATCH,
    CatalogError,
)
from photo_search.ocr import contract
from photo_search.ocr.weights import OcrWeights, verify
from photo_search.runtime import device as runtime_device

Point = tuple[float, float]
Box = tuple[float, float, float, float]


class OcrCancelled(Exception):
    """The caller asked this execution to stop (the lease no longer exists)."""


@dataclass(frozen=True)
class OcrRegion:
    """One recognised region in *source* coordinates, in reading order once sorted."""

    index: int
    polygon: tuple[Point, ...]
    box: Box
    text: str
    confidence: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": int(self.index),
            "box": [float(value) for value in self.box],
            "polygon": [[float(x), float(y)] for x, y in self.polygon],
            "text": self.text,
            "confidence": float(self.confidence),
        }


@dataclass(frozen=True)
class OcrDocument:
    """The recognised document of one image, plus the geometry that produced it."""

    original_size: tuple[int, int]
    working_size: tuple[int, int]
    scale: float
    slice_count: int
    regions: tuple[OcrRegion, ...]
    elapsed_ms: int

    @property
    def original_text(self) -> str:
        """The uncorrected, reading-order text. Numbers, dates and ids are never rewritten."""
        return "\n".join(region.text for region in self.regions)

    @property
    def low_confidence_count(self) -> int:
        return sum(
            1 for region in self.regions if region.confidence <= contract.LOW_CONFIDENCE_THRESHOLD
        )


# --------------------------------------------------------------------------- geometry
def axis_box(polygon: Sequence[Sequence[float]]) -> Box:
    xs = [float(point[0]) for point in polygon]
    ys = [float(point[1]) for point in polygon]
    return (min(xs), min(ys), max(xs), max(ys))


def box_iou(first: Box, second: Box) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    if intersection <= 0.0:
        return 0.0
    area_first = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    area_second = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    union = area_first + area_second - intersection
    return 0.0 if union <= 0.0 else intersection / union


def plan_slices(
    width: int,
    height: int,
    *,
    size: int = contract.SLICE_SIZE,
    overlap: int = contract.SLICE_OVERLAP,
    single_max_pixels: int = contract.SINGLE_TILE_MAX_PIXELS,
    max_slices: int = contract.MAX_SLICES,
) -> tuple[Box, ...]:
    """The bounded slice plan of one working raster, in row-major order."""
    width = int(width)
    height = int(height)
    if width <= 0 or height <= 0:
        raise CatalogError(OCR_INPUT_TOO_LARGE, "the working image has no pixels")
    if width * height <= int(single_max_pixels):
        return ((0.0, 0.0, float(width), float(height)),)
    size = max(int(size), 1)
    step = max(int(size) - int(overlap), 1)

    def starts(total: int) -> list[int]:
        if total <= size:
            return [0]
        values = list(range(0, total - size + 1, step))
        if values[-1] + size < total:
            values.append(total - size)
        return values

    tiles = tuple(
        (float(x), float(y), float(min(x + size, width)), float(min(y + size, height)))
        for y in starts(height)
        for x in starts(width)
    )
    if len(tiles) > int(max_slices):
        raise CatalogError(
            OCR_INPUT_TOO_LARGE,
            "the image needs more slices than this bounded OCR path allows",
            details={"slices": len(tiles), "max_slices": int(max_slices)},
        )
    return tiles


def deduplicate(regions: Iterable[OcrRegion], *, iou: float = contract.DUPLICATE_IOU) -> list[OcrRegion]:
    """Drop the copy of a region that also fell inside the overlap band of another slice."""
    kept: list[OcrRegion] = []
    for region in sorted(regions, key=lambda item: (-item.confidence, item.box[1], item.box[0])):
        if any(box_iou(region.box, other.box) >= float(iou) for other in kept):
            continue
        kept.append(region)
    return kept


def reading_order(regions: Iterable[OcrRegion]) -> list[OcrRegion]:
    """Top-to-bottom, left-to-right order; one line is a vertical-overlap cluster."""
    lines: list[list[OcrRegion]] = []
    for region in sorted(regions, key=lambda item: (item.box[1], item.box[0])):
        placed = False
        for line in lines:
            line_top = min(item.box[1] for item in line)
            line_bottom = max(item.box[3] for item in line)
            overlap = min(region.box[3], line_bottom) - max(region.box[1], line_top)
            smaller = min(region.box[3] - region.box[1], line_bottom - line_top)
            if smaller > 0 and overlap >= 0.5 * smaller:
                line.append(region)
                placed = True
                break
        if not placed:
            lines.append([region])
    lines.sort(key=lambda line: (min(item.box[1] for item in line), min(item.box[0] for item in line)))
    ordered: list[OcrRegion] = []
    for line in lines:
        ordered.extend(sorted(line, key=lambda item: (item.box[0], item.box[1])))
    return ordered


def _round(value: float) -> float:
    return round(float(value), contract.COORDINATE_DECIMALS)


def _regions_from_raw(raw: Any, *, offset: tuple[float, float], scale: float) -> list[OcrRegion]:
    """Map one engine result back to source coordinates."""
    regions: list[OcrRegion] = []
    for item in raw or []:
        try:
            polygon_raw = item[0]
            text = str(item[1])
            score = float(item[2])
        except (IndexError, TypeError, ValueError):  # pragma: no cover - engine contract
            continue
        if not text:
            continue
        polygon = tuple((float(point[0]), float(point[1])) for point in polygon_raw)
        if not polygon:
            continue
        source_polygon = tuple(
            (
                _round((x + offset[0]) / scale),
                _round((y + offset[1]) / scale),
            )
            for x, y in polygon
        )
        regions.append(
            OcrRegion(
                index=-1,
                polygon=source_polygon,
                box=tuple(_round(value) for value in axis_box(source_polygon)),  # type: ignore[arg-type]
                text=text,
                confidence=max(0.0, min(1.0, score)),
            )
        )
    return regions


def _working_image(image: Any) -> tuple[Any, float]:
    """Bound the long edge of a decoded image; never enlarge."""
    from PIL import Image

    width, height = image.size
    long_edge = max(int(width), int(height))
    if long_edge <= contract.MAX_LONG_EDGE:
        return image, 1.0
    scale = contract.MAX_LONG_EDGE / float(long_edge)
    size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    return image.resize(size, Image.LANCZOS), scale


def _to_bgr(image: Any) -> Any:
    """The pinned engine expects BGR for ndarray input (P0-04 ``channel_order: BGR``)."""
    import numpy as np

    array = np.asarray(image.convert("RGB"))
    return np.ascontiguousarray(array[:, :, ::-1])


def recognize_image(
    engine: "PinnedOcrEngine",
    image: Any,
    *,
    cancel: Callable[[], bool] | None = None,
    deadline_ms: int | None = None,
    clock: Callable[[], float] | None = None,
) -> OcrDocument:
    """Recognise one already-decoded, orientation-normalised image."""
    now = clock or time.monotonic
    started = now()
    budget_ms = contract.DEFAULT_DEADLINE_MS if deadline_ms is None else int(deadline_ms)
    original_size = (int(image.size[0]), int(image.size[1]))
    if original_size[0] * original_size[1] > contract.MAX_DECODE_PIXELS:
        raise CatalogError(
            OCR_INPUT_TOO_LARGE,
            "the image declares more pixels than this bounded OCR path decodes",
            details={"pixels": original_size[0] * original_size[1],
                     "max_pixels": contract.MAX_DECODE_PIXELS},
        )
    working, scale = _working_image(image)
    tiles = plan_slices(working.size[0], working.size[1])

    def check() -> None:
        if cancel is not None and cancel():
            raise OcrCancelled("the execution was cancelled between slices")
        if (now() - started) * 1000.0 > budget_ms:
            raise CatalogError(
                OCR_DEADLINE_EXCEEDED,
                "the OCR decode/recognition budget was exhausted",
                details={"deadline_ms": budget_ms},
            )

    regions: list[OcrRegion] = []
    for left, top, right, bottom in tiles:
        check()
        crop = working.crop((int(left), int(top), int(right), int(bottom)))
        raw = engine.read(_to_bgr(crop))
        regions.extend(_regions_from_raw(raw, offset=(left, top), scale=scale))
    check()

    kept = deduplicate(regions)
    ordered = reading_order(kept)
    if len(ordered) > contract.MAX_REGIONS:
        raise CatalogError(
            OCR_RESULT_TOO_LARGE,
            "the recognised document has more regions than one artifact may carry",
            details={"regions": len(ordered), "max_regions": contract.MAX_REGIONS},
        )
    final = tuple(
        OcrRegion(
            index=index,
            polygon=region.polygon,
            box=region.box,
            text=region.text,
            confidence=region.confidence,
        )
        for index, region in enumerate(ordered)
    )
    elapsed_ms = int(round((now() - started) * 1000.0))
    return OcrDocument(
        original_size=original_size,
        working_size=(int(working.size[0]), int(working.size[1])),
        scale=float(scale),
        slice_count=len(tiles),
        regions=final,
        elapsed_ms=elapsed_ms,
    )

# ----------------------------------------------------------------------------- engine
class PinnedOcrEngine:
    """The three pinned graphs, opened on one explicitly resolved device."""

    def __init__(
        self,
        weights: OcrWeights,
        *,
        resolved_device: str = "cpu",
        runtime_provider: str | None = None,
        precision: str = "FP32",
        engine_factory: Callable[..., Any] | None = None,
    ) -> None:
        factory = engine_factory or _rapidocr_factory()
        self.weights = weights
        try:
            resolution = runtime_device.resolve_device(
                requested_device=str(resolved_device),
                policy=runtime_device.DevicePolicy.CPU_ALLOWED,
                precision=str(precision),
            )
        except runtime_device.DeviceUnavailableError as error:
            raise CatalogError(
                RUNTIME_CAPABILITY_UNAVAILABLE,
                "the requested OCR provider is unavailable",
                details={"resolved_device": str(resolved_device), "reason": error.reason},
            ) from error
        if resolution.device == "cuda":
            runtime_device.preload_runtime_dlls()
        self.resolved_device = "gpu" if resolution.device == "cuda" else "cpu"
        self.runtime_provider = str(runtime_provider or resolution.provider)
        self.precision = str(precision)
        self._engine = factory(config_path=str(weights.config_path), params={
            "Global.model_root_dir": str(weights.root),
            "Global.text_score": float(contract.MIN_TEXT_SCORE),
            "Global.max_side_len": int(contract.MAX_LONG_EDGE),
            "Global.log_level": "error",
            "Det.model_path": str(weights.detection_path),
            "Cls.model_path": str(weights.classification_path),
            "Rec.model_path": str(weights.recognition_path),
            "EngineConfig.onnxruntime.use_cuda": self.resolved_device == "gpu",
        })
        self.session_providers = self._read_session_providers(self._engine)
        expected_provider = (
            runtime_device.CUDA_PROVIDER
            if self.resolved_device == "gpu"
            else runtime_device.CPU_PROVIDER
        )
        mismatches = {
            role: providers
            for role, providers in self.session_providers.items()
            if not providers or providers[0] != expected_provider
        }
        if mismatches or self.runtime_provider != expected_provider:
            raise CatalogError(
                RUNTIME_RESOLUTION_MISMATCH,
                "RapidOCR did not create all sessions on the dispatched provider",
                details={
                    "resolved_device": self.resolved_device,
                    "expected_provider": expected_provider,
                    "reported_provider": self.runtime_provider,
                    "session_providers": self.session_providers,
                    "mismatched_roles": sorted(mismatches),
                },
            )

    @staticmethod
    def _read_session_providers(engine: Any) -> dict[str, tuple[str, ...]]:
        paths = {
            "det": ("text_det", "session", "session"),
            "cls": ("text_cls", "session", "session"),
            "rec": ("text_rec", "session", "session"),
        }
        observed: dict[str, tuple[str, ...]] = {}
        try:
            for role, path in paths.items():
                target = engine
                for attribute in path:
                    target = getattr(target, attribute)
                observed[role] = tuple(str(item) for item in target.get_providers())
        except Exception as error:
            raise CatalogError(
                RUNTIME_CAPABILITY_UNAVAILABLE,
                "RapidOCR session providers could not be verified",
                details={"reason": type(error).__name__},
            ) from error
        return observed

    def read(self, bgr: Any) -> Any:
        """Run detection + direction classification + recognition on one BGR array."""
        output = self._engine(bgr)
        if output.boxes is None:
            return []
        return [[box.tolist(), text, float(score)]
                for box, text, score in zip(output.boxes, output.txts, output.scores, strict=True)]


def _rapidocr_factory() -> Callable[..., Any]:
    try:
        from rapidocr import RapidOCR
    except Exception as error:  # pragma: no cover - a broken install
        raise CatalogError(
            RUNTIME_CAPABILITY_UNAVAILABLE,
            "this process has no RapidOCR runtime",
            details={"reason": type(error).__name__},
        ) from error
    return RapidOCR


class OcrEngineCache:
    """Process-local cache of the verified weights and their ONNX sessions.

    The cache key includes the size/mtime signature of the four pinned files, so a replaced
    or truncated file forces a fresh SHA-256 verification and a fresh session. It never
    turns a content swap with an identical size *and* mtime into a silent reuse beyond the
    process that first verified it: every new process starts unverified.
    """

    def __init__(self, *, engine_factory: Callable[..., Any] | None = None) -> None:
        self._lock = threading.Lock()
        self._factory = engine_factory
        self._weights: dict[tuple[Any, ...], OcrWeights] = {}
        self._engines: dict[tuple[Any, ...], PinnedOcrEngine] = {}

    @staticmethod
    def _signature(weights: OcrWeights) -> tuple[Any, ...]:
        entries: list[tuple[str, int, int]] = []
        for item in weights.files:
            try:
                info = item.absolute_path.stat()
            except OSError:
                entries.append((item.relative_path, -1, -1))
                continue
            entries.append((item.relative_path, int(info.st_size), int(info.st_mtime_ns)))
        return (str(weights.root), *entries)

    def verified_weights(self, data_root: str | Path | None = None) -> OcrWeights:
        from photo_search.ocr import weights as weights_module

        resolved = weights_module.resolve(data_root)
        key = self._signature(resolved)
        with self._lock:
            cached = self._weights.get(key)
            if cached is not None:
                return cached
        verified = verify(resolved)
        with self._lock:
            self._weights[key] = verified
        return verified

    def engine(
        self,
        data_root: str | Path | None = None,
        *,
        resolved_device: str = "cpu",
        runtime_provider: str | None = None,
        precision: str = "FP32",
    ) -> PinnedOcrEngine:
        verified = self.verified_weights(data_root)
        key = (*self._signature(verified), str(resolved_device), runtime_provider, str(precision))
        with self._lock:
            cached = self._engines.get(key)
            if cached is not None:
                return cached
        engine = PinnedOcrEngine(
            verified,
            resolved_device=resolved_device,
            runtime_provider=runtime_provider,
            precision=precision,
            engine_factory=self._factory,
        )
        with self._lock:
            self._engines[key] = engine
        return engine
