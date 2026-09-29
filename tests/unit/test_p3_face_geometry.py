"""P3-02: the pinned detector geometry, the canvas mapping and the alignment."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from photo_search.catalog.errors import (
    FACE_DEADLINE_EXCEEDED,
    FACE_INPUT_TOO_LARGE,
    FACE_WEIGHTS_HASH_MISMATCH,
    FACE_WEIGHTS_MISSING,
    CatalogError,
)
from photo_search.face import contract, weights
from photo_search.face.engine import (
    FaceCancelled,
    FaceEngine,
    FaceEngineCache,
    analyze_image,
    anchor_centers,
    build_canvas,
    decode_detections,
    distance2bbox,
    distance2kps,
    estimate_norm,
    laplacian_variance,
    nms_indices,
    quality_for,
    warp_aligned,
    working_image,
)


# ----------------------------------------------------------------------- geometry
def test_the_anchor_grid_is_location_major_and_anchor_minor() -> None:
    grid = anchor_centers(2, 8)
    assert grid.shape == (2 * 2 * 2, 2)
    assert grid.tolist() == [
        [0.0, 0.0],
        [0.0, 0.0],
        [8.0, 0.0],
        [8.0, 0.0],
        [0.0, 8.0],
        [0.0, 8.0],
        [8.0, 8.0],
        [8.0, 8.0],
    ]


def test_boxes_and_keypoints_decode_around_their_anchor_center() -> None:
    centers = np.array([[100.0, 200.0]])
    boxes = distance2bbox(centers, np.array([[10.0, 20.0, 30.0, 40.0]]))
    assert boxes.tolist() == [[90.0, 180.0, 130.0, 240.0]]
    points = distance2kps(centers, np.array([[1.0, 2.0, -3.0, -4.0]]))
    assert points.tolist() == [[101.0, 202.0, 97.0, 196.0]]


def test_nms_keeps_the_best_of_two_overlapping_boxes_and_is_deterministic() -> None:
    boxes = np.array([[0.0, 0.0, 100.0, 100.0], [5.0, 5.0, 105.0, 105.0], [300.0, 0.0, 400.0, 100.0]])
    scores = np.array([0.9, 0.8, 0.7])
    assert nms_indices(boxes, scores) == [0, 2]
    # Equal scores fall back to the index order, so the result never depends on the sort.
    assert nms_indices(boxes, np.array([0.8, 0.8, 0.8])) == [0, 2]
    assert nms_indices(np.zeros((0, 4)), np.zeros(0)) == []


def test_decoding_maps_the_canvas_back_to_the_raster_and_drops_weak_anchors() -> None:
    # One stride-8 slot with a high score and a modest box; everything else stays quiet.
    outputs: list[Any] = []
    for group in range(3):
        arrays = []
        for stride_index, stride in enumerate(contract.DETECT_STRIDES):
            size = contract.DETECT_CANVAS // int(stride)
            rows = size * size * contract.DETECT_ANCHORS
            if group == 0:
                values = np.zeros((rows, 1), dtype=np.float32)
                if stride_index == 0:
                    values[0, 0] = 0.9
            elif group == 1:
                values = np.zeros((rows, 4), dtype=np.float32)
                if stride_index == 0:
                    values[0] = [10.0, 20.0, 30.0, 40.0]
            else:
                values = np.zeros((rows, 10), dtype=np.float32)
            arrays.append(values)
        outputs.extend(arrays)
    faces = decode_detections(outputs, scale=0.5, offset=(20.0, 30.0))
    assert len(faces) == 1
    box = faces[0].bbox
    # The raw distances are multiplied by the stride (8), offsets around the anchor at (0,0),
    # then the padding offset is removed and the fit scale is undone.
    expected = [
        (0.0 - 10.0 * 8 - 20.0) / 0.5,
        (0.0 - 20.0 * 8 - 30.0) / 0.5,
        (0.0 + 30.0 * 8 - 20.0) / 0.5,
        (0.0 + 40.0 * 8 - 30.0) / 0.5,
    ]
    assert list(box) == pytest.approx(expected)
    assert faces[0].ordinal == 0


# ------------------------------------------------------------------------- canvas
def test_a_large_raster_is_centred_while_it_is_fitted_into_the_canvas() -> None:
    image = Image.new("RGB", (1280, 886), (200, 10, 10))
    blob, scale, offset = build_canvas(image)
    assert blob.shape == (1, 3, contract.DETECT_CANVAS, contract.DETECT_CANVAS)
    assert scale == pytest.approx(0.5)
    # A 640x443 letterbox is centred vertically; the returned offset is what the decoder
    # removes, so a face found at the top of the picture lands back at the top of the raster.
    assert offset == (0.0, float((contract.DETECT_CANVAS - 443) // 2))


def test_a_small_raster_is_pasted_at_native_size_instead_of_being_blown_up() -> None:
    """A 112-pixel portrait must reach the detector at 112 pixels, not at 640."""
    image = Image.new("RGB", (112, 112), (10, 200, 10))
    blob, scale, offset = build_canvas(image)
    assert scale == pytest.approx(1.0)
    assert offset == (
        float((contract.DETECT_CANVAS - 112) // 2),
        float((contract.DETECT_CANVAS - 112) // 2),
    )
    # The picture occupies its original pixels; the border is the neutral pad value.
    left, top = int(offset[0]), int(offset[1])
    body = blob[0, :, top + 10, left + 10]
    expected = np.asarray(image, dtype=np.float32).transpose(2, 0, 1)[:, 10, 10]
    expected = (expected - contract.DETECT_MEAN) * contract.DETECT_SCALE
    assert np.allclose(body, expected, atol=1e-5)
    border = blob[0, :, 5, 5]
    pad = (contract.DETECT_PAD_VALUE - contract.DETECT_MEAN) * contract.DETECT_SCALE
    assert np.allclose(border, pad, atol=1e-5)


def test_the_working_raster_is_capped_but_never_enlarged() -> None:
    small = Image.new("RGB", (640, 480))
    unchanged, inverse = working_image(small)
    assert unchanged.size == (640, 480)
    assert inverse == 1.0
    large = Image.new("RGB", (2560, 1920))
    capped, inverse = working_image(large)
    assert capped.size == (contract.WORKING_LONG_EDGE, 960)
    assert pytest.approx(capped.size[0] / 2560) == 1.0 / inverse


# ----------------------------------------------------------------------- alignment
def test_the_alignment_of_the_template_landmarks_is_the_identity() -> None:
    matrix = estimate_norm([list(point) for point in contract.ALIGN_TEMPLATE])
    assert matrix[:, :2] == pytest.approx(np.eye(2), abs=1e-6)
    assert matrix[:, 2] == pytest.approx([0.0, 0.0], abs=1e-6)


def test_the_alignment_warps_a_shifted_landmark_set_back_onto_the_template() -> None:
    shifted = [(x + 40.0, y + 25.0) for x, y in contract.ALIGN_TEMPLATE]
    matrix = estimate_norm(shifted)
    # A pure translation must be undone exactly by the estimated transform.
    assert matrix[:, :2] == pytest.approx(np.eye(2), abs=1e-6)
    assert matrix[:, 2] == pytest.approx([-40.0, -25.0], abs=1e-6)
    # A scaled and rotated landmark set is recovered as a similarity transform (no shear).
    scaled = [(x * 2.0, y * 2.0) for x, y in contract.ALIGN_TEMPLATE]
    matrix = estimate_norm(scaled)
    assert matrix[:, :2] == pytest.approx(0.5 * np.eye(2), abs=1e-6)


def test_the_warped_crop_matches_the_template_frame() -> None:
    image = Image.new("RGB", (200, 200), (0, 0, 0))
    # Paint a bright square exactly where the template landmarks sit after a +30/+30 shift.
    painter = image.load()
    for x in range(30 + 30, 30 + 85):
        for y in range(30 + 40, 30 + 105):
            painter[x, y] = (255, 255, 255)
    shifted = [(x + 30.0, y + 30.0) for x, y in contract.ALIGN_TEMPLATE]
    aligned = warp_aligned(image, shifted)
    assert aligned.shape == (contract.ALIGN_SIZE, contract.ALIGN_SIZE, 3)
    center = aligned[contract.ALIGN_SIZE // 2, contract.ALIGN_SIZE // 2]
    assert int(center[0]) > 200


def test_sharpness_and_the_quality_gate() -> None:
    flat = np.full((112, 112, 3), 120, dtype=np.uint8)
    assert laplacian_variance(flat) == pytest.approx(0.0)
    checker = np.indices((112, 112)).sum(axis=0) % 2
    noisy = np.repeat((checker * 255).astype(np.uint8)[:, :, None], 3, axis=2)
    assert laplacian_variance(noisy) > contract.MIN_BLUR_VARIANCE
    assert quality_for(det_score=0.9, face_pixels=100.0, blur_variance=300.0) == ("OK", None)
    assert quality_for(det_score=0.9, face_pixels=10.0, blur_variance=300.0) == (
        "LOW",
        contract.QUALITY_SMALL,
    )
    assert quality_for(det_score=0.9, face_pixels=100.0, blur_variance=1.0) == (
        "LOW",
        contract.QUALITY_BLURRY,
    )
    assert quality_for(det_score=0.51, face_pixels=100.0, blur_variance=300.0) == (
        "LOW",
        contract.QUALITY_LOW_SCORE,
    )


# -------------------------------------------------------------------------- limits
class _StubImage:
    """A declared-size raster that is never decoded (the bounded-input check only)."""

    def __init__(self, size: tuple[int, int]) -> None:
        self.size = size


def test_an_oversized_declared_raster_is_refused_before_decoding() -> None:
    class Engine:
        def detect(self, blob: Any) -> Any:  # pragma: no cover - never reached
            raise AssertionError("the detector must not be called for a refused input")

    with pytest.raises(CatalogError) as error:
        analyze_image(Engine(), _StubImage((9000, 9000)))  # type: ignore[arg-type]
    assert error.value.code == FACE_INPUT_TOO_LARGE


def test_the_cancellation_hook_and_the_wall_clock_budget_are_enforced() -> None:
    class Engine:
        def detect(self, blob: Any) -> Any:
            return []

    with pytest.raises(FaceCancelled):
        analyze_image(
            Engine(),  # type: ignore[arg-type]
            Image.new("RGB", (200, 200)),
            cancel=lambda: True,
        )
    ticks = {"value": 0.0}

    def clock() -> float:
        ticks["value"] += 100.0
        return ticks["value"]

    with pytest.raises(CatalogError) as error:
        analyze_image(
            Engine(),  # type: ignore[arg-type]
            Image.new("RGB", (200, 200)),
            deadline_ms=1,
            clock=clock,
        )
    assert error.value.code == FACE_DEADLINE_EXCEEDED


# ------------------------------------------------------------------------- weights
def fake_pinned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """A tiny synthetic pinned set: the verification logic, without the real weights."""
    target = tmp_path / "models"
    entries: list[dict] = []
    for index, role in enumerate(contract.WEIGHT_ROLES):
        relative = f"face/tiny/{role}.bin"
        body = f"pinned-{role}-{index}\n".encode() * (index + 1)
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        entries.append(
            {
                "role": role,
                "path": relative,
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        )
    monkeypatch.setattr(contract, "PINNED_WEIGHTS", tuple(entries))
    return entries


def test_verification_accepts_the_pinned_files_and_hides_absolute_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_pinned(tmp_path, monkeypatch)
    resolved = weights.resolve(tmp_path)
    assert weights.verify(resolved) is resolved
    assert weights.weights_present(tmp_path) is True
    report = weights.inspect(resolved)
    assert {item["role"] for item in report} == set(contract.WEIGHT_ROLES)
    assert all(item["size_matches"] for item in report)
    assert str(tmp_path) not in repr(report)


def test_a_missing_pinned_file_is_reported_as_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = fake_pinned(tmp_path, monkeypatch)
    (tmp_path / "models" / entries[1]["path"]).unlink()
    with pytest.raises(CatalogError) as error:
        weights.verify(weights.resolve(tmp_path))
    assert error.value.code == FACE_WEIGHTS_MISSING
    assert error.value.details["role"] == entries[1]["role"]
    assert weights.weights_present(tmp_path) is False


def test_a_same_size_different_byte_file_is_a_hash_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = fake_pinned(tmp_path, monkeypatch)
    path = tmp_path / "models" / entries[0]["path"]
    body = bytearray(path.read_bytes())
    body[0] ^= 0xFF
    path.write_bytes(bytes(body))
    assert path.stat().st_size == entries[0]["bytes"]
    with pytest.raises(CatalogError) as error:
        weights.verify(weights.resolve(tmp_path))
    assert error.value.code == FACE_WEIGHTS_HASH_MISMATCH


def test_the_engine_cache_refuses_a_broken_weight_set(tmp_path: Path) -> None:
    cache = FaceEngineCache()
    with pytest.raises(CatalogError) as error:
        cache.verified_weights(tmp_path)
    assert error.value.code == FACE_WEIGHTS_MISSING


def test_cuda_detector_explicitly_uses_mixed_providers_while_recognition_stays_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SCRFD may use CPU nodes, but the ArcFace graph must retain strict CUDA semantics."""
    from photo_search.runtime.device import CPU_PROVIDER, CUDA_PROVIDER, DISABLE_CPU_FALLBACK_KEY

    fake_pinned(tmp_path, monkeypatch)
    resolved = weights.verify(weights.resolve(tmp_path))
    calls: list[dict[str, Any]] = []

    class Session:
        def __init__(self, providers: list[Any]) -> None:
            self._providers = tuple(
                item[0] if isinstance(item, tuple) else item for item in providers
            )

        def get_providers(self) -> tuple[str, ...]:
            return self._providers

    def factory(path: str, *, sess_options: Any, providers: list[Any]) -> Session:
        calls.append({"path": path, "options": sess_options, "providers": providers})
        return Session(providers)

    engine = FaceEngine(
        resolved,
        device="gpu",
        providers=(CUDA_PROVIDER, CPU_PROVIDER),
        session_factory=factory,
    )

    detector, recognizer = calls
    assert detector["providers"] == [(CUDA_PROVIDER, {"use_tf32": "0"}), CPU_PROVIDER]
    with pytest.raises(RuntimeError):
        detector["options"].get_session_config_entry(DISABLE_CPU_FALLBACK_KEY)
    assert recognizer["providers"] == [(CUDA_PROVIDER, {"use_tf32": "0"})]
    assert recognizer["options"].get_session_config_entry(DISABLE_CPU_FALLBACK_KEY) == "1"
    assert engine.detector_providers == (CUDA_PROVIDER, CPU_PROVIDER)
    assert engine.recognizer_providers == (CUDA_PROVIDER,)
    assert engine.mixed_cuda_detection is True
    assert engine.recognition_cpu_fallback_disabled is True
