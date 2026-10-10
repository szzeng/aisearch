"""P3-01: bounded slicing, overlap de-duplication, reading order and the pinned engine."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from photo_search.catalog.errors import (
    OCR_DEADLINE_EXCEEDED,
    OCR_INPUT_TOO_LARGE,
    OCR_RESULT_TOO_LARGE,
    OCR_WEIGHTS_HASH_MISMATCH,
    OCR_WEIGHTS_MISSING,
    CatalogError,
)
from photo_search.ocr import contract, weights
from photo_search.ocr.engine import (
    OcrCancelled,
    OcrEngineCache,
    OcrRegion,
    PinnedOcrEngine,
    box_iou,
    deduplicate,
    plan_slices,
    reading_order,
    recognize_image,
)
from photo_search.runtime import device as runtime_device

SHA = "e" * 64


def region(box: tuple[float, float, float, float], text: str, confidence: float = 0.9) -> OcrRegion:
    left, top, right, bottom = box
    return OcrRegion(
        index=-1,
        polygon=((left, top), (right, top), (right, bottom), (left, bottom)),
        box=box,
        text=text,
        confidence=confidence,
    )


# --------------------------------------------------------------------------- slicing
def test_a_small_image_is_one_slice() -> None:
    tiles = plan_slices(1200, 900)
    assert tiles == ((0.0, 0.0, 1200.0, 900.0),)


def test_a_large_image_is_sliced_with_the_last_slice_flush_to_the_edge() -> None:
    tiles = plan_slices(1800, 1200)
    assert len(tiles) == 2
    assert all(0.0 <= left < right <= 1800.0 for left, _, right, _ in tiles)
    assert max(right for _, _, right, _ in tiles) == 1800.0
    assert all(right - left <= contract.SLICE_SIZE for left, _, right, _ in tiles)
    # The overlap band is real: neighbouring slices share at least the configured overlap of
    # the same row, and the last slice is flush with the right edge.
    assert tiles[0][2] - tiles[1][0] >= contract.SLICE_OVERLAP
    assert tiles[1][2] - tiles[1][0] <= contract.SLICE_SIZE
    # A taller page needs a second row of slices, and every slice is inside the raster.
    tall = plan_slices(2000, 2000)
    assert len(tall) == 4
    assert all(top >= 0 and bottom <= 2000 for _, top, _, bottom in tall)


def test_the_slice_count_is_bounded_and_never_silently_downscaled() -> None:
    with pytest.raises(CatalogError) as error:
        plan_slices(2000, 2000, single_max_pixels=1, max_slices=2)
    assert error.value.code == OCR_INPUT_TOO_LARGE
    with pytest.raises(CatalogError):
        plan_slices(0, 100)


# ------------------------------------------------------------------------ de-duplication
def test_the_overlap_band_copy_is_dropped_in_favour_of_the_better_score() -> None:
    left = region((100.0, 100.0, 200.0, 130.0), "发票", 0.71)
    shifted = region((101.0, 101.0, 201.0, 131.0), "发票", 0.96)
    separate = region((400.0, 100.0, 500.0, 130.0), "发票", 0.8)
    kept = deduplicate([left, shifted, separate])
    assert len(kept) == 2
    assert {item.confidence for item in kept} == {0.96, 0.8}


def test_two_genuinely_different_regions_are_never_merged() -> None:
    first = region((0.0, 0.0, 50.0, 20.0), "编号", 0.9)
    second = region((60.0, 0.0, 120.0, 20.0), "编号", 0.9)
    assert deduplicate([first, second]) == [first, second]
    assert box_iou(first.box, second.box) == 0.0
    assert box_iou(first.box, first.box) == pytest.approx(1.0)


# ------------------------------------------------------------------------- reading order
def test_reading_order_is_top_to_bottom_then_left_to_right() -> None:
    bottom_left = region((10.0, 200.0, 80.0, 220.0), "d")
    top_right = region((300.0, 10.0, 380.0, 30.0), "b")
    top_left = region((10.0, 8.0, 90.0, 28.0), "a")
    same_line_bottom = region((150.0, 202.0, 220.0, 224.0), "e")
    ordered = reading_order([bottom_left, top_right, top_left, same_line_bottom])
    assert [item.text for item in ordered] == ["a", "b", "d", "e"]
    assert reading_order([]) == []


def test_a_slightly_shifted_box_stays_on_its_line() -> None:
    first = region((10.0, 100.0, 100.0, 130.0), "left")
    second = region((120.0, 106.0, 220.0, 136.0), "right")
    ordered = reading_order([second, first])
    assert [item.text for item in ordered] == ["left", "right"]


# ------------------------------------------------------------------------ stub inference
class StubEngine:
    """A deterministic stand-in: one canned result per slice, in that slice's own frame."""

    def __init__(self, per_slice: list[list[Any]]) -> None:
        self.per_slice = per_slice
        self.calls: list[tuple[int, int]] = []

    def read(self, bgr: Any) -> list[Any]:
        self.calls.append((int(bgr.shape[0]), int(bgr.shape[1])))
        return self.per_slice[len(self.calls) - 1]


def test_a_sliced_image_maps_every_box_back_to_source_coordinates() -> None:
    stub = StubEngine(
        [
            [[[(10, 10), (110, 10), (110, 40), (10, 40)], "第一行 1280", 0.9]],
            [[[(5, 5), (95, 5), (95, 35), (5, 35)], "第二行 20260914", 0.8]],
        ]
    )
    image = Image.new("RGB", (1800, 1200), (255, 255, 255))
    document = recognize_image(stub, image)
    assert document.slice_count == 2
    assert document.original_size == (1800, 1200)
    assert document.scale == 1.0
    assert len(stub.calls) == 2
    assert [item.text for item in document.regions] == ["第一行 1280", "第二行 20260914"]
    # The second slice starts at x=520, so its boxes must be offset by exactly that much.
    assert document.regions[1].box[0] == pytest.approx(525.0)
    assert document.original_text == "第一行 1280\n第二行 20260914"


def test_a_large_image_is_downscaled_to_the_bounded_long_edge() -> None:
    stub = StubEngine([[[[(0, 0), (10, 0), (10, 10), (0, 10)], "tiny", 0.9]]])
    image = Image.new("RGB", (4000, 600), (255, 255, 255))
    document = recognize_image(stub, image)
    assert max(document.working_size) == contract.MAX_LONG_EDGE
    assert document.working_size == (2000, 300)
    assert document.scale == pytest.approx(contract.MAX_LONG_EDGE / 4000)
    assert document.regions[0].box[2] == pytest.approx(20.0)  # 10 px at scale 0.5


def test_the_cancellation_hook_stops_the_execution_between_slices() -> None:
    stub = StubEngine([[], []])
    image = Image.new("RGB", (1800, 1200), (255, 255, 255))
    with pytest.raises(OcrCancelled):
        recognize_image(stub, image, cancel=lambda: True)


def test_the_wall_clock_budget_is_enforced() -> None:
    stub = StubEngine([[[[(0, 0), (10, 0), (10, 10), (0, 10)], "x", 0.9]]])
    moments = iter([0.0, 0.0, 5.0, 5.0, 5.0, 5.0])
    image = Image.new("RGB", (600, 400), (255, 255, 255))
    with pytest.raises(CatalogError) as error:
        recognize_image(stub, image, deadline_ms=1000, clock=lambda: next(moments))
    assert error.value.code == OCR_DEADLINE_EXCEEDED


def test_an_oversized_declared_raster_is_refused_before_decoding(monkeypatch) -> None:
    monkeypatch.setattr(contract, "MAX_DECODE_PIXELS", 100)
    with pytest.raises(CatalogError) as error:
        recognize_image(StubEngine([[]]), Image.new("RGB", (20, 20)))
    assert error.value.code == OCR_INPUT_TOO_LARGE


def test_too_many_regions_are_refused(monkeypatch) -> None:
    monkeypatch.setattr(contract, "MAX_REGIONS", 2)
    boxes = [
        [[(index * 30, 0), (index * 30 + 20, 0), (index * 30 + 20, 15), (index * 30, 15)],
         f"t{index}", 0.9]
        for index in range(3)
    ]
    with pytest.raises(CatalogError) as error:
        recognize_image(StubEngine([boxes]), Image.new("RGB", (600, 400)))
    assert error.value.code == OCR_RESULT_TOO_LARGE


# ---------------------------------------------------------------------------- weights
def fake_pinned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, list[dict]]:
    """A tiny synthetic pinned set: the verification logic, without the real weights."""
    target = tmp_path / "models"
    entries: list[dict] = []
    for index, role in enumerate(contract.WEIGHT_ROLES):
        relative = f"ocr/tiny/{role}.bin"
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
    return target, entries


def test_verification_accepts_the_pinned_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pinned(tmp_path, monkeypatch)
    resolved = weights.resolve(tmp_path)
    assert weights.verify(resolved) is resolved
    assert weights.weights_present(tmp_path) is True
    report = weights.inspect(resolved)
    assert {item["role"] for item in report} == set(contract.WEIGHT_ROLES)
    assert all(item["size_matches"] for item in report)
    assert str(tmp_path) not in repr(report)  # no absolute path in a diagnostic record


def test_a_missing_pinned_file_is_reported_as_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, entries = fake_pinned(tmp_path, monkeypatch)
    (tmp_path / "models" / entries[2]["path"]).unlink()
    with pytest.raises(CatalogError) as error:
        weights.verify(weights.resolve(tmp_path))
    assert error.value.code == OCR_WEIGHTS_MISSING
    assert error.value.details["role"] == entries[2]["role"]
    assert weights.weights_present(tmp_path) is False


def test_a_file_with_the_wrong_size_is_a_hash_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, entries = fake_pinned(tmp_path, monkeypatch)
    (tmp_path / "models" / entries[0]["path"]).write_bytes(b"short")
    with pytest.raises(CatalogError) as error:
        weights.verify(weights.resolve(tmp_path))
    assert error.value.code == OCR_WEIGHTS_HASH_MISMATCH
    assert error.value.details["actual_bytes"] == 5


def test_a_same_size_different_byte_file_is_a_hash_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, entries = fake_pinned(tmp_path, monkeypatch)
    path = tmp_path / "models" / entries[1]["path"]
    body = bytearray(path.read_bytes())
    body[0] ^= 0xFF
    path.write_bytes(bytes(body))
    assert path.stat().st_size == entries[1]["bytes"]
    with pytest.raises(CatalogError) as error:
        weights.verify(weights.resolve(tmp_path))
    assert error.value.code == OCR_WEIGHTS_HASH_MISMATCH
    assert error.value.details["role"] == entries[1]["role"]


# ----------------------------------------------------------------------------- engine
def test_the_engine_is_built_only_from_the_pinned_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pinned(tmp_path, monkeypatch)
    resolved = weights.verify(weights.resolve(tmp_path))
    captured: dict[str, Any] = {}

    class Recorder:
        def __init__(self, **options: Any) -> None:
            captured.update(options)
            self.options = options
            provider = (
                runtime_device.CUDA_PROVIDER
                if options.get("det_use_cuda")
                else runtime_device.CPU_PROVIDER
            )
            def session() -> Any:
                return SimpleNamespace(get_providers=lambda: [provider])
            self.text_det = SimpleNamespace(infer=SimpleNamespace(session=session()))
            self.text_cls = SimpleNamespace(infer=SimpleNamespace(session=session()))
            self.text_rec = SimpleNamespace(session=SimpleNamespace(session=session()))

        def __call__(self, image: Any) -> Any:  # pragma: no cover - no inference here
            return None, None

    engine = PinnedOcrEngine(resolved, engine_factory=Recorder)
    assert captured["config_path"] == str(resolved.config_path)
    assert captured["det_model_path"] == str(resolved.detection_path)
    assert captured["cls_model_path"] == str(resolved.classification_path)
    assert captured["rec_model_path"] == str(resolved.recognition_path)
    # The low-confidence override travels into the engine, and the wheel's own copies are
    # never referenced by any option.
    assert captured["text_score"] == contract.MIN_TEXT_SCORE
    assert "site-packages" not in json_dump(captured)
    assert engine.read(None) is None


def json_dump(payload: Any) -> str:
    import json

    return json.dumps(payload)


def test_the_engine_cache_reuses_a_verified_engine_until_the_files_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_pinned(tmp_path, monkeypatch)
    builds = {"count": 0}

    class Recorder:
        def __init__(self, **options: Any) -> None:
            builds["count"] += 1
            self.options = options
            session = SimpleNamespace(get_providers=lambda: [runtime_device.CPU_PROVIDER])
            self.text_det = SimpleNamespace(infer=SimpleNamespace(session=session))
            self.text_cls = SimpleNamespace(infer=SimpleNamespace(session=session))
            self.text_rec = SimpleNamespace(session=SimpleNamespace(session=session))

        def __call__(self, image: Any) -> Any:  # pragma: no cover
            return None, None

    cache = OcrEngineCache(engine_factory=Recorder)
    first = cache.engine(tmp_path)
    second = cache.engine(tmp_path)
    assert first is second
    assert builds["count"] == 1
    # A changed pinned file invalidates the signature: verification and the session are redone.
    target = weights.resolve(tmp_path).files[0].absolute_path
    target.write_bytes(target.read_bytes() + b"x")
    with pytest.raises(CatalogError):
        cache.engine(tmp_path)


def test_the_engine_cache_separates_cpu_and_cuda_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_pinned(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runtime_device,
        "available_providers",
        lambda: (runtime_device.CUDA_PROVIDER, runtime_device.CPU_PROVIDER),
    )
    monkeypatch.setattr(runtime_device, "preload_runtime_dlls", lambda: {"loaded": True})

    class Recorder:
        def __init__(self, **options: Any) -> None:
            provider = (
                runtime_device.CUDA_PROVIDER
                if options.get("det_use_cuda")
                else runtime_device.CPU_PROVIDER
            )
            session = SimpleNamespace(get_providers=lambda: [provider])
            self.text_det = SimpleNamespace(infer=SimpleNamespace(session=session))
            self.text_cls = SimpleNamespace(infer=SimpleNamespace(session=session))
            self.text_rec = SimpleNamespace(session=SimpleNamespace(session=session))

    cache = OcrEngineCache(engine_factory=Recorder)
    cpu = cache.engine(tmp_path, resolved_device="cpu")
    gpu = cache.engine(tmp_path, resolved_device="gpu")
    assert cpu is not gpu
    assert cpu.runtime_provider == runtime_device.CPU_PROVIDER
    assert gpu.runtime_provider == runtime_device.CUDA_PROVIDER
    assert all(
        providers[0] == runtime_device.CUDA_PROVIDER
        for providers in gpu.session_providers.values()
    )


def test_a_gpu_request_refuses_a_cpu_only_rapidocr_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_pinned(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runtime_device,
        "available_providers",
        lambda: (runtime_device.CUDA_PROVIDER, runtime_device.CPU_PROVIDER),
    )
    monkeypatch.setattr(runtime_device, "preload_runtime_dlls", lambda: {"loaded": True})

    class CpuOnlyRecorder:
        def __init__(self, **options: Any) -> None:
            session = SimpleNamespace(
                get_providers=lambda: [runtime_device.CPU_PROVIDER]
            )
            self.text_det = SimpleNamespace(infer=SimpleNamespace(session=session))
            self.text_cls = SimpleNamespace(infer=SimpleNamespace(session=session))
            self.text_rec = SimpleNamespace(session=SimpleNamespace(session=session))

    cache = OcrEngineCache(engine_factory=CpuOnlyRecorder)
    with pytest.raises(CatalogError) as error:
        cache.engine(tmp_path, resolved_device="gpu")
    # A requested GPU session that reports CPU providers is a GPU initialization failure.
    assert error.value.code == "OCR_GPU_INITIALIZATION_FAILED"
    assert error.value.details["mismatched_roles"] == ["cls", "det", "rec"]


def test_a_gpu_request_is_blocked_when_cuda_provider_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_pinned(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runtime_device,
        "available_providers",
        lambda: (runtime_device.CPU_PROVIDER,),
    )
    builds = {"count": 0}

    def factory(**options: Any) -> Any:
        builds["count"] += 1
        raise AssertionError("the engine must not be constructed without CUDA")

    cache = OcrEngineCache(engine_factory=factory)
    with pytest.raises(CatalogError) as error:
        cache.engine(tmp_path, resolved_device="gpu")
    assert error.value.code == "RUNTIME_CAPABILITY_UNAVAILABLE"
    assert builds["count"] == 0


def test_the_engine_cache_never_serves_a_missing_weight_set(tmp_path: Path) -> None:
    cache = OcrEngineCache(engine_factory=lambda **options: None)
    with pytest.raises(CatalogError) as error:
        cache.engine(tmp_path)
    assert error.value.code == OCR_WEIGHTS_MISSING
