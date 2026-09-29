"""The pinned face pipeline: SCRFD detection, ArcFace alignment, 512-d recognition.

Everything here is deterministic given the pinned bytes and the bounded working raster:
the detector canvas is a fixed size, the anchor grid and the NMS order are fixed, the
alignment is a 4-parameter similarity transform estimated from the five landmarks, and the
embedding is L2-normalised before it becomes part of any artifact. The same picture
therefore produces the same faces, and a second run of the same content can only reproduce
the first - never a second set of them.

Two boundaries are enforced here rather than documented:

* **The device is resolved, not discovered.** Recognition runs on a strict CUDA session with
  CPU execution-provider fallback disabled.  The pinned SCRFD detector is the deliberate
  exception: its unsupported CUDA nodes run through an explicitly configured CPU EP while
  the session still starts on CUDA.  That mixed state is recorded by the probe; a CUDA request
  that cannot open either required session is a structured ``FACE_DEVICE_UNAVAILABLE``
  failure, never a quieter all-CPU run.
* **The input is bounded.** The working raster is capped by ``WORKING_LONG_EDGE``, an image
  whose *declared* pixels exceed ``MAX_DECODE_PIXELS`` is refused before decoding, and a
  picture with more faces than ``MAX_FACES`` is a bounded-output refusal rather than a
  silently truncated document.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import (
    FACE_DEADLINE_EXCEEDED,
    FACE_DEVICE_UNAVAILABLE,
    FACE_INPUT_TOO_LARGE,
    FACE_RESULT_TOO_LARGE,
    RUNTIME_CAPABILITY_UNAVAILABLE,
    CatalogError,
)
from photo_search.face import contract
from photo_search.face.weights import FaceWeights, verify
from photo_search.runtime.device import (
    CPU_PROVIDER,
    CUDA_PROVIDER,
    DevicePolicy,
    build_session_options,
    preload_runtime_dlls,
    resolve_device,
    runtime_capabilities,
    session_providers,
)

Point = tuple[float, float]
Box = tuple[float, float, float, float]


class FaceCancelled(Exception):
    """The caller asked this execution to stop (the lease no longer exists)."""


@dataclass(frozen=True)
class DetectedFace:
    """One detected face in *working raster* coordinates (the frame that is recognised)."""

    ordinal: int
    bbox: Box
    landmarks: tuple[Point, ...]
    det_score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "ordinal": int(self.ordinal),
            "bbox": [float(value) for value in self.bbox],
            "landmarks": [[float(x), float(y)] for x, y in self.landmarks],
            "det_score": float(self.det_score),
        }


@dataclass(frozen=True)
class FaceRecord:
    """One analysed face: geometry, quality measures and its independent 512-d vector."""

    ordinal: int
    bbox: Box
    landmarks: tuple[Point, ...]
    det_score: float
    face_pixels: float
    blur_variance: float
    quality_state: str
    quality_reason: str | None
    embedding: tuple[float, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ordinal": int(self.ordinal),
            "bbox": [float(value) for value in self.bbox],
            "landmarks": [[float(x), float(y)] for x, y in self.landmarks],
            "det_score": float(self.det_score),
            "face_pixels": float(self.face_pixels),
            "blur_variance": float(self.blur_variance),
            "quality_state": str(self.quality_state),
            "quality_reason": self.quality_reason,
            "embedding": [float(value) for value in self.embedding],
        }


@dataclass(frozen=True)
class FaceDocument:
    """Every face of one image, plus the geometry that produced them."""

    original_size: tuple[int, int]
    working_size: tuple[int, int]
    scale: float
    faces: tuple[FaceRecord, ...]
    elapsed_ms: int

    @property
    def low_quality_count(self) -> int:
        return sum(1 for face in self.faces if face.quality_state != "OK")


# --------------------------------------------------------------------------- geometry
def _round(value: float, decimals: int = contract.COORDINATE_DECIMALS) -> float:
    return round(float(value), int(decimals))


def anchor_centers(size: int, stride: int, *, anchors: int = contract.DETECT_ANCHORS) -> Any:
    """The SCRFD anchor grid of one feature map, in the order the graph emits it.

    ``np.mgrid`` gives an ``(x, y)`` stack; the transposed reshape produces the
    location-major, anchor-minor order the nine outputs use (score, box, keypoints).
    """
    import numpy as np

    grid = np.stack(np.mgrid[:size, :size][::-1], axis=-1).astype(np.float32)
    flat = (grid * int(stride)).reshape(-1, 2)
    return np.stack([flat] * int(anchors), axis=1).reshape(-1, 2)


def distance2bbox(points: Any, distance: Any) -> Any:
    import numpy as np

    x1 = points[:, 0] - distance[:, 0]
    y1 = points[:, 1] - distance[:, 1]
    x2 = points[:, 0] + distance[:, 2]
    y2 = points[:, 1] + distance[:, 3]
    return np.stack([x1, y1, x2, y2], axis=-1)


def distance2kps(points: Any, distance: Any) -> Any:
    import numpy as np

    outputs = []
    for index in range(0, distance.shape[1], 2):
        outputs.append(points[:, index % 2] + distance[:, index])
        outputs.append(points[:, index % 2 + 1] + distance[:, index + 1])
    return np.stack(outputs, axis=-1)


def nms_indices(boxes: Any, scores: Any, *, iou: float = contract.DETECT_NMS_IOU) -> list[int]:
    """Deterministic greedy NMS: score-descending, then by index for equal scores."""
    import numpy as np

    if not len(scores):
        return []
    order = np.lexsort((np.arange(len(scores)), -scores))
    keep: list[int] = []
    while len(order):
        current = int(order[0])
        keep.append(current)
        if len(order) == 1:
            break
        rest = order[1:]
        left = np.maximum(boxes[current, 0], boxes[rest, 0])
        top = np.maximum(boxes[current, 1], boxes[rest, 1])
        right = np.minimum(boxes[current, 2], boxes[rest, 2])
        bottom = np.minimum(boxes[current, 3], boxes[rest, 3])
        inter = np.maximum(0.0, right - left) * np.maximum(0.0, bottom - top)
        area_current = (boxes[current, 2] - boxes[current, 0]) * (
            boxes[current, 3] - boxes[current, 1]
        )
        area_rest = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        union = area_current + area_rest - inter
        values = np.where(union > 0, inter / np.maximum(union, 1e-9), 0.0)
        order = rest[values <= float(iou)]
    return keep


def estimate_norm(landmarks: Sequence[Sequence[float]]) -> Any:
    """The ArcFace 5-point similarity transform (4 parameters, no reflection)."""
    import numpy as np

    src = np.asarray(landmarks, dtype=np.float64).reshape(5, 2)
    dst = np.asarray(contract.ALIGN_TEMPLATE, dtype=np.float64)
    src_mean = src.mean(axis=0)
    dst_mean = dst.mean(axis=0)
    src_demean = src - src_mean
    dst_demean = dst - dst_mean
    covariance = dst_demean.T @ src_demean / len(src)
    u, singular, vt = np.linalg.svd(covariance)
    correction = np.ones(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        correction[1] = -1
    rotation = u @ np.diag(correction) @ vt
    variance = float(src_demean.var(axis=0).sum())
    scale = 1.0 if variance <= 0 else float((singular * correction).sum() / variance)
    matrix = np.zeros((2, 3), dtype=np.float64)
    matrix[:, :2] = scale * rotation
    matrix[:, 2] = dst_mean - scale * rotation @ src_mean
    return matrix


def warp_aligned(image: Any, landmarks: Sequence[Sequence[float]]) -> Any:
    """Warp the source raster into the 112x112 ArcFace frame."""
    import numpy as np
    from PIL import Image

    matrix = estimate_norm(landmarks)
    full = np.vstack([matrix, [0.0, 0.0, 1.0]])
    inverse = np.linalg.inv(full)
    coefficients = tuple(float(value) for value in inverse[:2].reshape(-1))
    warped = Image.fromarray(np.asarray(image, dtype=np.uint8)).transform(
        (contract.ALIGN_SIZE, contract.ALIGN_SIZE),
        Image.AFFINE,
        coefficients,
        resample=Image.BILINEAR,
    )
    return np.asarray(warped, dtype=np.uint8)


def laplacian_variance(aligned: Any) -> float:
    """Variance of the Laplacian of the aligned crop - the sharpness measure of one face."""
    import numpy as np
    from PIL import Image

    gray = np.asarray(Image.fromarray(np.asarray(aligned, dtype=np.uint8)).convert("L"), dtype=np.float64)
    laplacian = (
        -4.0 * gray
        + np.roll(gray, 1, 0)
        + np.roll(gray, -1, 0)
        + np.roll(gray, 1, 1)
        + np.roll(gray, -1, 1)
    )
    return float(laplacian[1:-1, 1:-1].var())


def quality_for(
    *, det_score: float, face_pixels: float, blur_variance: float
) -> tuple[str, str | None]:
    """The frozen quality gate: size, sharpness and detector confidence, in that order."""
    if float(face_pixels) < contract.MIN_FACE_PIXELS:
        return "LOW", contract.QUALITY_SMALL
    if float(blur_variance) < contract.MIN_BLUR_VARIANCE:
        return "LOW", contract.QUALITY_BLURRY
    if float(det_score) < contract.LOW_DETECTION_SCORE:
        return "LOW", contract.QUALITY_LOW_SCORE
    return "OK", None


# -------------------------------------------------------------------------- detection
def build_canvas(image: Any) -> tuple[Any, float, tuple[float, float]]:
    """Place the working raster on the fixed detector canvas.

    A raster larger than the canvas is scaled down to fit; a raster *smaller* than the canvas
    is pasted at its native size instead of being blown up. That second rule matters: the
    detector's training distribution puts a face at a plausible fraction of a 640-pixel photo,
    so scaling a 112-pixel crop up to fill the canvas makes the face far larger than the
    detector expects, and the face is then missed entirely. Keeping the pixels and adding a
    neutral border preserves the size the picture really has.
    """
    import numpy as np
    from PIL import Image

    width, height = image.size
    if max(int(width), int(height)) >= contract.DETECT_CANVAS:
        scale = min(
            contract.DETECT_CANVAS / float(width), contract.DETECT_CANVAS / float(height)
        )
        new_width = max(1, int(round(width * scale)))
        new_height = max(1, int(round(height * scale)))
        resized = np.asarray(
            image.convert("RGB").resize((new_width, new_height), Image.BILINEAR), dtype=np.uint8
        )
    else:
        new_width, new_height = int(width), int(height)
        resized = np.asarray(image.convert("RGB"), dtype=np.uint8)
    canvas = np.full(
        (contract.DETECT_CANVAS, contract.DETECT_CANVAS, 3),
        int(contract.DETECT_PAD_VALUE),
        dtype=np.uint8,
    )
    top = (contract.DETECT_CANVAS - new_height) // 2
    left = (contract.DETECT_CANVAS - new_width) // 2
    canvas[top : top + new_height, left : left + new_width] = resized
    blob = (canvas.astype(np.float32) - contract.DETECT_MEAN) * contract.DETECT_SCALE
    blob = np.ascontiguousarray(blob.transpose(2, 0, 1)[None])
    # ``decode_detections`` divides by ``scale``; the padded case is a true 1:1 mapping, and
    # the padding offset is irrelevant because every detected face lies inside the raster.
    # ``decode_detections`` maps a canvas coordinate back to the raster with
    # ``(coordinate - offset) / scale``: the fit case has a zero offset, the padded case is a
    # true 1:1 mapping whose offset must be removed (a face found in a 112-pixel portrait is
    # not 264 pixels away from the raster origin).
    return blob, float(new_height) / float(height), (float(left), float(top))


def decode_detections(
    outputs: Sequence[Any], *, scale: float, offset: tuple[float, float] = (0.0, 0.0)
) -> list[DetectedFace]:
    """Turn the nine SCRFD outputs into working-raster faces, in deterministic order."""
    import numpy as np

    count = len(contract.DETECT_STRIDES)
    if len(outputs) != count * 3:
        raise CatalogError(
            RUNTIME_CAPABILITY_UNAVAILABLE,
            "the pinned detector did not return the three score/box/keypoint groups",
            details={"outputs": len(outputs)},
        )
    scores_all, boxes_all, kps_all = [], [], []
    for index, stride in enumerate(contract.DETECT_STRIDES):
        size = contract.DETECT_CANVAS // int(stride)
        centers = anchor_centers(size, int(stride))
        scores = np.asarray(outputs[index])[:, 0]
        if len(scores) != len(centers):
            raise CatalogError(
                RUNTIME_CAPABILITY_UNAVAILABLE,
                "the pinned detector returned an unexpected anchor count",
                details={"stride": int(stride), "anchors": int(len(scores))},
            )
        boxes = distance2bbox(centers, np.asarray(outputs[index + count]) * int(stride))
        kps = distance2kps(centers, np.asarray(outputs[index + count * 2]) * int(stride))
        keep = scores >= contract.DETECT_SCORE_THRESHOLD
        scores_all.append(scores[keep])
        boxes_all.append(boxes[keep])
        kps_all.append(kps[keep])
    scores = np.concatenate(scores_all) if scores_all else np.zeros((0,))
    boxes = np.concatenate(boxes_all) if boxes_all else np.zeros((0, 4))
    kps = np.concatenate(kps_all) if kps_all else np.zeros((0, 10))
    if not len(scores):
        return []
    keep = nms_indices(boxes, scores)
    factor = 1.0 / float(scale)
    faces: list[DetectedFace] = []
    for ordinal, index in enumerate(keep):
        shifted = np.asarray(offset, dtype=np.float64)
        box = (
            boxes[index] - np.array([shifted[0], shifted[1], shifted[0], shifted[1]])
        ) * factor
        points = ((kps[index].reshape(5, 2) - shifted) * factor).reshape(5, 2)
        faces.append(
            DetectedFace(
                ordinal=ordinal,
                bbox=(
                    _round(box[0]),
                    _round(box[1]),
                    _round(box[2]),
                    _round(box[3]),
                ),
                landmarks=tuple((_round(x), _round(y)) for x, y in points),
                det_score=_round(float(scores[index]), 6),
            )
        )
    return faces


def working_image(image: Any, *, max_long_edge: int = contract.WORKING_LONG_EDGE) -> tuple[Any, float]:
    """Bound the working raster's long edge; never enlarge."""
    from PIL import Image

    width, height = image.size
    longest = max(int(width), int(height))
    if longest <= int(max_long_edge):
        return image, 1.0
    scale = int(max_long_edge) / float(longest)
    size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    return image.resize(size, Image.LANCZOS), 1.0 / scale


def analyze_image(
    engine: "FaceEngine",
    image: Any,
    *,
    cancel: Callable[[], bool] | None = None,
    deadline_ms: int | None = None,
    clock: Callable[[], float] | None = None,
) -> FaceDocument:
    """Detect, align, embed and score every face of one already-decoded image."""
    now = clock or time.monotonic
    started = now()
    budget_ms = contract.DEFAULT_DEADLINE_MS if deadline_ms is None else int(deadline_ms)
    original_size = (int(image.size[0]), int(image.size[1]))
    if original_size[0] * original_size[1] > contract.MAX_DECODE_PIXELS:
        raise CatalogError(
            FACE_INPUT_TOO_LARGE,
            "the image declares more pixels than this bounded face path decodes",
            details={
                "pixels": original_size[0] * original_size[1],
                "max_pixels": contract.MAX_DECODE_PIXELS,
            },
        )

    def check() -> None:
        if cancel is not None and cancel():
            raise FaceCancelled("the execution was cancelled before it finished")
        if (now() - started) * 1000.0 > budget_ms:
            raise CatalogError(
                FACE_DEADLINE_EXCEEDED,
                "the face detection/recognition budget was exhausted",
                details={"deadline_ms": budget_ms},
            )

    working, inverse_scale = working_image(image)
    working_scale = 1.0 / float(inverse_scale)
    blob, detect_scale, detect_offset = build_canvas(working)
    check()
    outputs = engine.detect(blob)
    detected = decode_detections(outputs, scale=detect_scale, offset=detect_offset)
    if len(detected) > contract.MAX_FACES:
        raise CatalogError(
            FACE_RESULT_TOO_LARGE,
            "the image contains more faces than one artifact may carry",
            details={"faces": len(detected), "max_faces": contract.MAX_FACES},
        )
    raster = working
    records: list[FaceRecord] = []
    for face in detected:
        check()
        aligned = warp_aligned(raster, face.landmarks)
        vector = engine.recognize(aligned)
        blur = laplacian_variance(aligned)
        # The geometry is stored in the *source* (orientation-normalised) frame, while the
        # quality measures describe the pixels the recogniser really saw: the working raster
        # is bounded by ``WORKING_LONG_EDGE``, so a 6000-pixel photo cannot pretend that a
        # face was recognised at its original resolution.
        pixels = min(
            face.bbox[2] - face.bbox[0],
            face.bbox[3] - face.bbox[1],
        )
        source_box = tuple(_round(value * working_scale) for value in face.bbox)
        source_landmarks = tuple(
            (_round(x * working_scale), _round(y * working_scale)) for x, y in face.landmarks
        )
        state, reason = quality_for(
            det_score=face.det_score, face_pixels=pixels, blur_variance=blur
        )
        records.append(
            FaceRecord(
                ordinal=face.ordinal,
                bbox=source_box,
                landmarks=source_landmarks,
                det_score=face.det_score,
                face_pixels=_round(pixels),
                blur_variance=_round(blur, 3),
                quality_state=state,
                quality_reason=reason,
                embedding=tuple(
                    _round(value, contract.EMBEDDING_DECIMALS) for value in vector
                ),
            )
        )
    check()
    return FaceDocument(
        original_size=original_size,
        working_size=(int(raster.size[0]), int(raster.size[1])),
        scale=float(inverse_scale),
        faces=tuple(records),
        elapsed_ms=int(round((now() - started) * 1000.0)),
    )


# ----------------------------------------------------------------------------- engine
class FaceEngine:
    """The two pinned graphs, opened once per process for one resolved device."""

    def __init__(
        self,
        weights: FaceWeights,
        *,
        device: str,
        providers: Sequence[str] | None = None,
        session_factory: Callable[..., Any] | None = None,
    ) -> None:
        resolution = resolve_device(
            None if str(device) == "auto" else str(device),
            policy=DevicePolicy.CPU_ALLOWED,
            providers=providers,
        )
        self.weights = weights
        self.device = resolution.device
        self.provider = resolution.provider
        self.is_degradation = resolution.is_degradation
        self.reason = resolution.reason
        # The accepted P0-05/P0-06 path: a packaged build ships the CUDA/cuDNN DLLs beside the
        # executable, so they are preloaded before CUDA initialises. An explicit CPU run must
        # not touch the GPU driver at all, which is why this is inside the CUDA branch only.
        self.dll_report: dict[str, Any] | None = None
        if resolution.device == "cuda":
            self.dll_report = preload_runtime_dlls()
        strict_options = build_session_options(resolution)
        strict_providers = session_providers(resolution)
        factory = session_factory or _onnx_session
        detector_options = strict_options
        detector_providers = strict_providers
        detector_requires_cpu_provider = False
        if resolution.device == "cuda":
            # ``det_10g`` has a small set of nodes that the pinned CUDA EP cannot execute on
            # this supported runtime.  Keep CUDA first, but permit those nodes to run on the
            # CPU EP.  Recognition remains the strict-CUDA path below.
            detector_options = build_session_options(resolution, disable_cpu_fallback=False)
            detector_providers = [*strict_providers, CPU_PROVIDER]
            detector_requires_cpu_provider = True
        self.detector = self._open(
            factory,
            path=weights.detection_path,
            providers=detector_providers,
            options=detector_options,
            expected=resolution.provider,
            role="detection",
            require_cpu_provider=detector_requires_cpu_provider,
        )
        self.recognizer = self._open(
            factory,
            path=weights.recognition_path,
            providers=strict_providers,
            options=strict_options,
            expected=resolution.provider,
            role="recognition",
        )
        self.detector_providers = tuple(self.detector.get_providers())
        self.recognizer_providers = tuple(self.recognizer.get_providers())
        self.mixed_cuda_detection = bool(
            resolution.device == "cuda" and CPU_PROVIDER in self.detector_providers
        )
        # ONNX Runtime may still list CPU in ``get_providers()`` for a strict CUDA session.
        # The configured fallback gate, not that inventory, is the authority for ArcFace.
        self.recognition_cpu_fallback_disabled = bool(resolution.cpu_fallback_disabled)

    @staticmethod
    def _open(
        factory: Callable[..., Any],
        *,
        path: Path,
        providers: Sequence[Any],
        options: Any,
        expected: str,
        role: str,
        require_cpu_provider: bool = False,
    ) -> Any:
        try:
            session = factory(str(path), sess_options=options, providers=list(providers))
        except Exception as error:
            raise CatalogError(
                FACE_DEVICE_UNAVAILABLE,
                "cannot open the pinned face graph on the resolved execution provider",
                details={
                    "role": str(role),
                    "provider": str(expected),
                    "reason": type(error).__name__,
                },
            ) from error
        actual = tuple(session.get_providers())
        if not actual or str(actual[0]) != str(expected):
            raise CatalogError(
                FACE_DEVICE_UNAVAILABLE,
                "the face session did not land on the resolved execution provider",
                details={"role": str(role), "expected": str(expected), "actual": list(actual)},
            )
        if require_cpu_provider and CPU_PROVIDER not in actual:
            raise CatalogError(
                FACE_DEVICE_UNAVAILABLE,
                "the face detection session did not retain its required CPU execution provider",
                details={"role": str(role), "expected": [str(expected), CPU_PROVIDER], "actual": list(actual)},
            )
        return session

    def detect(self, blob: Any) -> list[Any]:
        return list(self.detector.run(None, {self.detector.get_inputs()[0].name: blob}))

    def recognize(self, aligned: Any) -> Any:
        import numpy as np

        values = (np.asarray(aligned, dtype=np.float32) - contract.RECOGNITION_MEAN) * (
            contract.RECOGNITION_SCALE
        )
        blob = np.ascontiguousarray(values.transpose(2, 0, 1)[None])
        output = self.recognizer.run(None, {self.recognizer.get_inputs()[0].name: blob})[0]
        vector = np.asarray(output, dtype=np.float32).reshape(-1)
        if vector.shape != (contract.FACE_DIMENSION,):
            raise CatalogError(
                RUNTIME_CAPABILITY_UNAVAILABLE,
                "the pinned recognition graph returned an unexpected dimension",
                details={"dimension": int(vector.size)},
            )
        norm = float(np.linalg.norm(vector))
        if norm <= 0.0:
            raise CatalogError(
                RUNTIME_CAPABILITY_UNAVAILABLE,
                "the pinned recognition graph returned a zero embedding",
            )
        return vector / norm


def _onnx_session(*args: Any, **kwargs: Any) -> Any:
    try:
        import onnxruntime
    except Exception as error:  # pragma: no cover - a broken install
        raise CatalogError(
            RUNTIME_CAPABILITY_UNAVAILABLE,
            "this process has no onnxruntime runtime",
            details={"reason": type(error).__name__},
        ) from error
    return onnxruntime.InferenceSession(*args, **kwargs)


class FaceEngineCache:
    """Process-local cache of the verified weights and their ONNX sessions, per device.

    The cache key includes the size/mtime signature of the two pinned files, so a replaced
    or truncated file forces a fresh SHA-256 verification and a fresh session. It never
    turns a content swap with an identical size *and* mtime into a silent reuse beyond the
    process that first verified it: every new process starts unverified.
    """

    def __init__(self, *, session_factory: Callable[..., Any] | None = None) -> None:
        self._lock = threading.Lock()
        self._factory = session_factory
        self._weights: dict[tuple[Any, ...], FaceWeights] = {}
        self._engines: dict[tuple[Any, ...], FaceEngine] = {}
        self._attempts: dict[tuple[Any, ...], dict[str, Any]] = {}

    @staticmethod
    def _signature(weights: FaceWeights) -> tuple[Any, ...]:
        entries: list[tuple[str, int, int]] = []
        for item in weights.files:
            try:
                info = item.absolute_path.stat()
            except OSError:
                entries.append((item.relative_path, -1, -1))
                continue
            entries.append((item.relative_path, int(info.st_size), int(info.st_mtime_ns)))
        return (str(weights.root), *entries)

    def verified_weights(self, data_root: str | Path | None = None) -> FaceWeights:
        from photo_search.face import weights as weights_module

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

    def engine(self, data_root: str | Path | None = None, *, device: str = "auto") -> FaceEngine:
        verified = self.verified_weights(data_root)
        key = self._signature(verified)
        cache_key = (*key, str(device))
        with self._lock:
            cached = self._engines.get(cache_key)
            if cached is not None:
                return cached
        engine = FaceEngine(verified, device=str(device), session_factory=self._factory)
        with self._lock:
            self._engines[cache_key] = engine
        return engine

    def attempt(self, data_root: str | Path | None, device: str) -> dict[str, Any]:
        """Try one device and remember both outcomes, so a probe never doubles the work."""
        from photo_search.face import weights as weights_module

        # The key deliberately comes from the *unverified* resolution: a broken weight set is
        # a recorded capability outcome, not an exception that escapes the probe.
        cache_key = (*self._signature(weights_module.resolve(data_root)), str(device))
        with self._lock:
            cached = self._attempts.get(cache_key)
        if cached is not None:
            return cached
        record: dict[str, Any]
        try:
            engine = self.engine(data_root, device=str(device))
            record = {
                "device": str(device),
                "usable": True,
                "provider": str(engine.provider),
                "degradation": bool(engine.is_degradation),
                "reason": str(engine.reason),
                "execution": {
                    "detection_providers": list(engine.detector_providers),
                    "recognition_providers": list(engine.recognizer_providers),
                    "mixed_cuda_detection": bool(engine.mixed_cuda_detection),
                    "recognition_cpu_fallback_disabled": bool(
                        engine.recognition_cpu_fallback_disabled
                    ),
                },
                "error": None,
            }
        except CatalogError as error:
            record = {
                "device": str(device),
                "usable": False,
                "provider": None,
                "degradation": False,
                "reason": None,
                "execution": None,
                "error": {"code": str(error.code), **error.details},
            }
        with self._lock:
            record = dict(record)
            self._attempts[cache_key] = record
        return record

    def probe(self, data_root: str | Path | None = None, *, include_gpu: bool = True) -> dict[str, Any]:
        """What this runtime really offers the face processor right now.

        ``include_gpu=False`` (an explicit CPU request) never touches the GPU driver: CUDA
        is then reported as listed-but-unchecked instead of probed and remembered as broken.
        """
        capabilities = runtime_capabilities()
        reported = tuple(capabilities.providers)
        cpu = self.attempt(data_root, "cpu")
        gpu: dict[str, Any] = {
            "device": "gpu",
            "usable": False,
            "provider": None,
            "degradation": False,
            "reason": "not probed",
            "execution": None,
            "error": None,
            "checked": False,
        }
        if CUDA_PROVIDER in reported:
            if include_gpu:
                gpu = {**self.attempt(data_root, "gpu"), "checked": True}
            else:
                gpu = {
                    **gpu,
                    "reason": "an explicit cpu run does not open the gpu driver",
                    "listed": True,
                }
        return {
            "reported_providers": list(reported),
            "cpu_available_provider": CPU_PROVIDER in reported,
            "cuda_listed": CUDA_PROVIDER in reported,
            "cpu": cpu,
            "gpu": gpu,
            "runtime": capabilities,
        }
