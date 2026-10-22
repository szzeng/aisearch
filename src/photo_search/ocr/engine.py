"""The pinned PP-OCRv4 pipeline: bounded high-resolution input, slicing and geometry.

Three decisions live here, and each of them is deliberate:

* **Never the thumbnail size.** The working raster is bounded by ``MAX_LONG_EDGE`` (2000 px)
  and then, if it is still too large to hand to one detector pass, cut into overlapping
  slices. A 320/448 px raster is the *grid* contract of P1-03 and would erase exactly the
  small text P3-01 exists to find.
* **Native confidence filter.** Active v4 uses RapidOCR 1.4.4's ``text_score=0.5`` filter;
  surviving regions retain their raw score, while ``0.6`` is used only for statistics and
  orientation retry.
* **Conservative overlap handling.** Slice boxes are mapped back to source coordinates.
  Only compatible text on the same physical line is de-duplicated; an uncertain overlap
  keeps both readings. A fixed ordering makes postprocessing independent of tile order.

Nothing in this module writes state: it turns one already-decoded image into one
``OcrDocument``, and every failure is a structured exception the executor translates.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from math import hypot
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


def _aligned_text_boxes(first: OcrRegion, second: OcrRegion) -> tuple[Box, Box] | None:
    """Project both polygons onto the full line's longest edge and its normal.

    Axis-aligned bounds alone can substantially overlap for separate tilted lines.
    Using polygon edges also makes the comparison independent of vertex order/winding.
    """
    if len(first.polygon) < 4 or len(second.polygon) < 4:
        return None
    edges = []
    for start, end in zip(first.polygon, first.polygon[1:] + first.polygon[:1]):
        dx, dy = end[0] - start[0], end[1] - start[1]
        if dx < 0 or (dx == 0 and dy < 0):
            dx, dy = -dx, -dy
        edges.append((hypot(dx, dy), dx, dy))
    length, dx, dy = max(edges)
    if length <= 0:
        return None
    ux, uy = dx / length, dy / length

    def project(region: OcrRegion) -> Box:
        along = [x * ux + y * uy for x, y in region.polygon]
        normal = [-x * uy + y * ux for x, y in region.polygon]
        return min(along), min(normal), max(along), max(normal)

    return project(first), project(second)


def _same_text_line(first: Box, second: Box) -> bool:
    """Require similar thickness and aligned centers on the projected minor axis."""
    policy = contract.DEDUPLICATION_CONFIG
    first_size = first[3] - first[1]
    second_size = second[3] - second[1]
    small, large = min(first_size, second_size), max(first_size, second_size)
    if small <= 0 or large > float(policy["line_max_thickness_ratio"]) * small:
        return False
    overlap = min(first[3], second[3]) - max(first[1], second[1])
    center_delta = abs(first[1] + first[3] - second[1] - second[3]) / 2
    return (
        overlap >= float(policy["line_overlap"]) * small
        and center_delta <= float(policy["line_center_tolerance"]) * large
    )


def _covered_fraction(part: Box, whole: Box) -> float:
    area = max(0.0, part[2] - part[0]) * max(0.0, part[3] - part[1])
    intersection = (
        max(0.0, min(part[2], whole[2]) - max(part[0], whole[0]))
        * max(0.0, min(part[3], whole[3]) - max(part[1], whole[1]))
    )
    return intersection / area if area else 0.0


def _clipped_text(part: Box, whole: Box, *, prefix: bool, suffix: bool) -> bool:
    policy = contract.DEDUPLICATION_CONFIG
    thickness = whole[3] - whole[1]
    extension = (whole[2] - whole[0]) - (part[2] - part[0])
    # "1280" and "12800" in the same box are competing recognitions, not truncation.
    if extension < thickness * float(policy["truncation_min_extension"]):
        return False
    tolerance = thickness * float(policy["truncation_edge_tolerance"])
    aligned = (
        (prefix and abs(part[0] - whole[0]) <= tolerance)
        or (suffix and abs(part[2] - whole[2]) <= tolerance)
    )
    return aligned and _covered_fraction(part, whole) >= float(policy["substring_coverage"])


def _comparison_text(text: str) -> str:
    """Ignore spacing only when doing so preserves every searchable token in order."""
    from photo_search.search.tokens import scan_tokens

    trimmed = text.strip()
    compact = "".join(trimmed.split())
    if compact != trimmed and scan_tokens(compact) == scan_tokens(trimmed):
        return compact
    return trimmed


def deduplicate(regions: Iterable[OcrRegion], *, iou: float = contract.DUPLICATE_IOU) -> list[OcrRegion]:
    """Keep complete, trustworthy lines instead of higher-scoring clipped fragments.

    Longer text is visited first, but suppresses a shorter reading only with direct
    text and geometry evidence. An eliminated intermediate candidate never suppresses
    another candidate. Whitespace that separates English/numeric FTS tokens is retained.
    """
    policy = contract.DEDUPLICATION_CONFIG
    candidates = sorted(
        ((region, _comparison_text(region.text)) for region in regions),
        key=lambda item: (
            -len(item[1]), -item[0].confidence, item[0].box,
            item[0].text, item[0].polygon,
        ),
    )
    kept: list[tuple[OcrRegion, str]] = []
    for region, text in candidates:
        duplicate = False
        for other, other_text in kept:
            if not text:
                continue
            equal = text == other_text
            prefix, suffix = other_text.startswith(text), other_text.endswith(text)
            clipped = (
                len(other_text) > len(text)
                and (prefix or suffix)
                and other.confidence > float(policy["full_text_confidence_gt"])
            )
            if not equal and not clipped:
                continue
            aligned = _aligned_text_boxes(other, region)
            if aligned is None or not _same_text_line(*aligned):
                continue
            whole, part = aligned
            if equal:
                duplicate = box_iou(part, whole) >= float(iou)
            else:
                duplicate = _clipped_text(part, whole, prefix=prefix, suffix=suffix)
            if duplicate:
                break
        if not duplicate:
            kept.append((region, text))
    return [region for region, _text in kept]


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
        orientation_retry: bool = False,
        engine_factory: Callable[..., Any] | None = None,
    ) -> None:
        factory = engine_factory or _rapidocr_factory()
        self.weights = weights
        self.orientation_retry = bool(orientation_retry)
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
        try:
            self._engine = factory(
                config_path=str(weights.config_path),
                det_model_path=str(weights.detection_path),
                cls_model_path=str(weights.classification_path),
                rec_model_path=str(weights.recognition_path),
                text_score=float(contract.MIN_TEXT_SCORE),
                print_verbose=False,
                max_side_len=int(contract.MAX_LONG_EDGE),
                det_use_cuda=self.resolved_device == "gpu",
                cls_use_cuda=self.resolved_device == "gpu",
                rec_use_cuda=self.resolved_device == "gpu",
            )
        except Exception as error:
            if self.resolved_device == "gpu" and _is_onnx_cuda_failure(error):
                raise CatalogError(
                    "OCR_GPU_INITIALIZATION_FAILED",
                    "the dispatched CUDA OCR sessions failed during initialization",
                    details={"exception_type": type(error).__name__},
                ) from error
            raise
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
            if self.resolved_device == "gpu":
                raise CatalogError(
                    "OCR_GPU_INITIALIZATION_FAILED",
                    "RapidOCR did not create all sessions on the dispatched CUDA provider",
                    details={
                        "resolved_device": self.resolved_device,
                        "expected_provider": expected_provider,
                        "reported_provider": self.runtime_provider,
                        "session_providers": self.session_providers,
                        "mismatched_roles": sorted(mismatches),
                    },
                )
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
            "det": ("text_det", "infer", "session"),
            "cls": ("text_cls", "infer", "session"),
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
                "OCR_SESSION_PROVIDER_UNVERIFIED",
                "RapidOCR session providers could not be verified",
                details={"reason": type(error).__name__},
            ) from error
        return observed

    def read(self, bgr: Any) -> Any:
        """Run detection + direction classification + recognition on one BGR array."""
        try:
            engine = self._orientation_retry_engine() if self.orientation_retry else self._engine
            raw, _elapsed = engine(bgr)
            return raw
        except Exception as error:
            if self.resolved_device == "gpu" and _is_onnx_cuda_failure(error):
                raise CatalogError(
                    "OCR_GPU_EXECUTION_FAILED",
                    "the dispatched CUDA OCR session failed during inference",
                    details={"exception_type": type(error).__name__},
                ) from error
            raise

    def _orientation_retry_engine(self) -> Any:
        """A per-call pipeline shell with one bounded original-orientation retry.

        RapidOCR's classifier can confidently rotate a correctly oriented long line.
        Only a low-confidence/empty recognition after an actual 180-degree rotation
        is reconsidered. The original crop must then produce a confident result with
        a material score improvement. Both alternatives remain raw model outputs.

        The shallow shell shares immutable configuration and inference sessions, but
        its callbacks and captured crops belong only to this call. Concurrent reads
        cannot replace callbacks or consume crops belonging to another image.
        """
        import copy

        engine = copy.copy(self._engine)
        classifier = self._engine.text_cls
        recognizer = self._engine.text_rec
        config = contract.ORIENTATION_RETRY_CONFIG
        original_crops: list[Any] = []
        rotated: list[bool] = []

        def classify(crops: Any) -> Any:
            output, labels, elapsed = classifier(crops)
            if len(output) != len(crops) or len(labels) != len(crops):
                raise RuntimeError("OCR classifier changed the crop count")
            original_crops[:] = crops
            # This is exactly the pinned classifier's rotation condition; a 180
            # label below its threshold has not actually rotated the crop.
            rotated[:] = [
                "180" in str(label) and float(score) > float(classifier.cls_thresh)
                for label, score in labels
            ]
            return output, labels, elapsed

        def recognize(crops: Any, return_word_box: bool = False) -> Any:
            results, elapsed = recognizer(crops, return_word_box)
            # The product uses line boxes. Do not reuse rotated word coordinates
            # if a diagnostic caller explicitly requests a different output shape.
            if return_word_box or not original_crops:
                return results, elapsed
            if len(results) != len(original_crops):
                raise RuntimeError("OCR recognizer changed the crop count")
            retry = [
                index for index, result in enumerate(results)
                if rotated[index] and (
                    not str(result[0])
                    or float(result[1]) <= float(config["trigger_confidence_threshold"])
                )
            ]
            if not retry:
                return results, elapsed
            alternatives, extra_elapsed = recognizer(
                [original_crops[index] for index in retry], False
            )
            selected = list(results)
            for index, alternative in zip(retry, alternatives, strict=True):
                if (
                    float(alternative[1]) > float(config["retry_min_confidence"])
                    and float(alternative[1]) >= (
                        float(results[index][1]) + float(config["retry_min_gain"])
                    )
                ):
                    selected[index] = alternative
            return selected, elapsed + extra_elapsed

        engine.text_cls = classify
        engine.text_rec = recognize
        return engine


def _rapidocr_factory() -> Callable[..., Any]:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception as error:  # pragma: no cover - a broken install
        raise CatalogError(
            "OCR_RUNTIME_UNAVAILABLE",
            "this process has no rapidocr-onnxruntime runtime",
            details={"reason": type(error).__name__},
        ) from error
    return RapidOCR


def _is_onnx_cuda_failure(error: BaseException) -> bool:
    """Recognize CUDA faults from ORT itself or RapidOCR's known wrapper and cause."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        module = type(current).__module__
        name = type(current).__name__
        if module.startswith("onnxruntime") and name in {"RuntimeException", "EngineError", "Fail"}:
            message = str(current).lower()
            if any(marker in message for marker in ("cuda", "cudnn", "cublas", "executionprovider")):
                return True
        # RapidOCR 1.4.x wraps ORT session.run failures in this concrete exception.
        if not (
            module == "rapidocr_onnxruntime.utils.infer_engine"
            and name == "ONNXRuntimeError"
        ):
            return False
        current = current.__cause__
    return False


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
        orientation_retry: bool = False,
    ) -> PinnedOcrEngine:
        verified = self.verified_weights(data_root)
        key = (*self._signature(verified), str(resolved_device), runtime_provider,
               str(precision), bool(orientation_retry))
        with self._lock:
            cached = self._engines.get(key)
            if cached is not None:
                return cached
        engine = PinnedOcrEngine(
            verified,
            resolved_device=resolved_device,
            runtime_provider=runtime_provider,
            precision=precision,
            orientation_retry=orientation_retry,
            engine_factory=self._factory,
        )
        with self._lock:
            self._engines[key] = engine
        return engine
