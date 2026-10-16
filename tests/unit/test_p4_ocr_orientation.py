"""Versioned crop-only orientation retry: raw output, bounds and call isolation."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from photo_search.catalog.errors import CatalogError
from photo_search.ocr import engine as module
from photo_search.ocr.engine import OcrCancelled, OcrEngineCache, PinnedOcrEngine, recognize_image


def crop(value: int) -> np.ndarray:
    return np.arange(value, value + 12, dtype=np.uint8).reshape(2, 2, 3)


def signature(value: np.ndarray) -> bytes:
    return value.tobytes()


def rotated(value: np.ndarray) -> np.ndarray:
    return value[::-1, ::-1].copy()


class Classifier:
    cls_thresh = 0.9

    def __init__(self, labels=None, barrier=None):
        self.labels = labels
        self.barrier = barrier

    def __call__(self, crops):
        if self.barrier is not None:
            self.barrier.wait(timeout=5)
        labels = self.labels or [("180", 0.99)] * len(crops)
        result = [rotated(image) if "180" in label and score > self.cls_thresh
                  else image.copy() for image, (label, score) in zip(crops, labels, strict=True)]
        return result, labels, 0.2


class Recognizer:
    def __init__(self, values, failure=None):
        self.values = values
        self.failure = failure
        self.calls = []

    def __call__(self, crops, return_word_box=False):
        keys = [signature(image) for image in crops]
        self.calls.append((keys, return_word_box))
        if self.failure is not None and self.failure in keys:
            raise ValueError("retry inference failed")
        return [self.values[key] for key in keys], 0.3


class Pipeline:
    def __init__(self, classifier, recognizer):
        self.text_cls = classifier
        self.text_rec = recognizer
        self.detections = []
        self.elapsed = []
        self.return_word_box = False

    def __call__(self, crops):
        self.detections.append(len(crops))
        if not crops:
            return None, None
        images, _, _ = self.text_cls(crops)
        results, elapsed = self.text_rec(images, self.return_word_box)
        self.elapsed.append(elapsed)
        return [([index, 0, index + 1, 1], *result) for index, result in enumerate(results)], elapsed


def pinned(pipeline, *, enabled=True):
    result = object.__new__(PinnedOcrEngine)
    result._engine = pipeline
    result.orientation_retry = enabled
    result.resolved_device = "cpu"
    return result


def pair(original, baseline=("-", 0.3), alternative=("旅行记录 AB-12.03", 0.99)):
    return {signature(original): alternative, signature(rotated(original)): baseline}


def test_false_rotation_retries_only_recognition_and_preserves_raw_values():
    original = crop(1)
    before = original.copy()
    classifier = Classifier()
    recognizer = Recognizer(pair(original))
    pipeline = Pipeline(classifier, recognizer)
    assert pinned(pipeline).read([original]) == [([0, 0, 1, 1], "旅行记录 AB-12.03", 0.99)]
    assert pipeline.detections == [1]
    assert recognizer.calls == [([signature(rotated(original))], False), ([signature(original)], False)]
    assert pipeline.elapsed == [0.6]
    assert np.array_equal(original, before)
    assert pipeline.text_cls is classifier and pipeline.text_rec is recognizer


def test_legacy_mode_preserves_the_old_output_and_never_retries():
    original = crop(1)
    recognizer = Recognizer(pair(original))
    result = pinned(Pipeline(Classifier(), recognizer), enabled=False).read([original])
    assert result[0][1:] == ("-", 0.3)
    assert len(recognizer.calls) == 1


@pytest.mark.parametrize("baseline,alternative,replace,retry", [
    (("-", 0.6), ("完整行", 0.7), True, True),
    (("-", 0.6000001), ("完整行", 0.99), False, False),
    (("-", 0.4), ("低置信", 0.6), False, True),
    (("-", 0.55), ("差距太小", 0.64), False, True),
    (("-", 0.3), ("也不可靠", 0.4), False, True),
    (("-", 0.3), ("相同分数", 0.3), False, True),
    (("", 0.0), ("完整行", 0.99), True, True),
])
def test_confidence_boundaries_keep_uncertain_original_output(baseline, alternative, replace, retry):
    original = crop(1)
    recognizer = Recognizer(pair(original, baseline, alternative))
    result = pinned(Pipeline(Classifier(), recognizer)).read([original])
    assert result[0][1:] == (alternative if replace else baseline)
    assert len(recognizer.calls) == 1 + int(retry)


@pytest.mark.parametrize("label", [("0", 0.99), ("180", 0.9), ("180", 0.89)])
def test_only_an_actually_applied_rotation_can_trigger_retry(label):
    original = crop(1)
    recognizer = Recognizer(pair(original, alternative=("原始低置信", 0.2)))
    result = pinned(Pipeline(Classifier([label]), recognizer)).read([original])
    assert result[0][1:] == ("原始低置信", 0.2)
    assert len(recognizer.calls) == 1


def test_inverted_input_still_uses_the_classifier_and_skips_retry_when_good():
    upright = crop(1)
    recognizer = Recognizer(pair(upright))
    result = pinned(Pipeline(Classifier(), recognizer)).read([rotated(upright)])
    assert result[0][1:] == ("旅行记录 AB-12.03", 0.99)
    assert len(recognizer.calls) == 1


def test_retry_is_one_batch_for_only_the_eligible_crops():
    first, second, third = crop(1), crop(31), crop(61)
    values = {**pair(first), **pair(second, baseline=("已正确", 0.99)), **pair(third)}
    recognizer = Recognizer(values)
    result = pinned(Pipeline(Classifier(), recognizer)).read([first, second, third])
    assert [row[1] for row in result] == ["旅行记录 AB-12.03", "已正确", "旅行记录 AB-12.03"]
    assert len(recognizer.calls) == 2
    assert recognizer.calls[1] == ([signature(first), signature(third)], False)


def test_concurrent_reads_never_share_captured_original_crops():
    first, second = crop(1), crop(31)
    recognizer = Recognizer({**pair(first, alternative=("第一张", 0.99)),
                             **pair(second, alternative=("第二张", 0.99))})
    classifier = Classifier(barrier=threading.Barrier(2))
    pipeline = Pipeline(classifier, recognizer)
    engine = pinned(pipeline)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(engine.read, [[first], [second]]))
    assert [result[0][1] for result in results] == ["第一张", "第二张"]
    assert pipeline.text_cls is classifier and pipeline.text_rec is recognizer


def test_empty_detection_and_failed_retry_do_not_leak_into_next_call():
    first, second = crop(1), crop(31)
    recognizer = Recognizer({**pair(first), **pair(second, alternative=("下一张", 0.99))},
                            failure=signature(first))
    engine = pinned(Pipeline(Classifier(), recognizer))
    assert engine.read([]) is None
    with pytest.raises(ValueError, match="retry inference failed"):
        engine.read([first])
    assert engine.read([second])[0][1:] == ("下一张", 0.99)
    assert engine.read([]) is None


def test_explicit_word_boxes_are_not_replaced_with_wrong_orientation_geometry():
    original = crop(1)
    words = {"box": "classified geometry"}
    recognizer = Recognizer(pair(original, baseline=("-", 0.3, words)))
    pipeline = Pipeline(Classifier(), recognizer)
    pipeline.return_word_box = True
    result = pinned(pipeline).read([original])
    assert result[0][1:] == ("-", 0.3, words)
    assert recognizer.calls == [([signature(rotated(original))], True)]


def test_cache_separates_legacy_and_retry_engines(monkeypatch, tmp_path):
    cache = OcrEngineCache()
    weights = SimpleNamespace(root=tmp_path, files=())
    monkeypatch.setattr(cache, "verified_weights", lambda _: weights)
    builds = []

    def build(weights, **options):
        result = SimpleNamespace(options=options)
        builds.append(result)
        return result

    monkeypatch.setattr(module, "PinnedOcrEngine", build)
    legacy = cache.engine(tmp_path)
    current = cache.engine(tmp_path, orientation_retry=True)
    assert current is not legacy
    assert cache.engine(tmp_path) is legacy
    assert cache.engine(tmp_path, orientation_retry=True) is current
    assert len(builds) == 2
    assert legacy.options["orientation_retry"] is False
    assert current.options["orientation_retry"] is True


def test_original_session_provider_handles_survive_an_orientation_retry():
    original = crop(1)
    classifier = Classifier()
    recognizer = Recognizer(pair(original))
    session = SimpleNamespace(get_providers=lambda: ["CPUExecutionProvider"])
    classifier.infer = SimpleNamespace(session=session)
    recognizer.session = SimpleNamespace(session=session)
    pipeline = Pipeline(classifier, recognizer)
    pipeline.text_det = SimpleNamespace(infer=SimpleNamespace(session=session))
    before = PinnedOcrEngine._read_session_providers(pipeline)
    pinned(pipeline).read([original])
    assert PinnedOcrEngine._read_session_providers(pipeline) == before
    assert set(before) == {"det", "cls", "rec"}


@pytest.mark.parametrize("boundary", ["cancel", "deadline"])
def test_extra_recognition_is_followed_by_existing_cancellation_and_deadline_checks(boundary):
    original = crop(1)
    recognizer = Recognizer(pair(original))

    class ImagePipeline(Pipeline):
        def __call__(self, _image):
            raw, elapsed = super().__call__([original])
            return [[[(0, 0), (10, 0), (10, 10), (0, 10)], row[1], row[2]]
                    for row in raw], elapsed

    engine = pinned(ImagePipeline(Classifier(), recognizer))
    if boundary == "cancel":
        checks = iter([False, True])
        with pytest.raises(OcrCancelled):
            recognize_image(engine, Image.new("RGB", (20, 20)), cancel=lambda: next(checks))
    else:
        moments = iter([0.0, 0.0, 1.0])
        with pytest.raises(CatalogError) as error:
            recognize_image(engine, Image.new("RGB", (20, 20)),
                            deadline_ms=500, clock=lambda: next(moments))
        assert error.value.code == "OCR_DEADLINE_EXCEEDED"
    assert len(recognizer.calls) == 2


def test_gpu_failure_in_retry_keeps_the_existing_structured_failure_boundary():
    original = crop(1)
    runtime_error = type("RuntimeException", (Exception,),
                         {"__module__": "onnxruntime.capi.onnxruntime_pybind11_state"})

    class FailingRecognizer(Recognizer):
        def __call__(self, crops, return_word_box=False):
            if any(signature(image) == signature(original) for image in crops):
                raise runtime_error("CUDA execution failed")
            return super().__call__(crops, return_word_box)

    engine = pinned(Pipeline(Classifier(), FailingRecognizer(pair(original))))
    engine.resolved_device = "gpu"
    with pytest.raises(CatalogError) as error:
        engine.read([original])
    assert error.value.code == "OCR_GPU_EXECUTION_FAILED"
