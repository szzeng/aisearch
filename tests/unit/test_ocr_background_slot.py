"""Deterministic probes of the existing model slot, without real models or photos."""

import threading

import pytest

from photo_search.face import service as face_service
from photo_search.runtime.background_slot import (
    BackgroundSlotBusy,
    BackgroundSlotCancelled,
    background_model_slot,
)


def test_shared_slot_reentrant_and_released_on_exception(monkeypatch):
    lock = threading.RLock()
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", lock)
    with pytest.raises(ValueError):
        with background_model_slot(timeout=0):
            with background_model_slot(timeout=0):
                raise ValueError("injected")
    assert lock.acquire(blocking=False)
    lock.release()


def test_shared_slot_wait_cancel_and_timeout_do_not_release_holder(monkeypatch):
    lock = threading.RLock()
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", lock)
    held = threading.Event()
    release = threading.Event()
    cancel = threading.Event()
    attempted = threading.Event()
    outcomes = []

    def holder():
        with lock:
            held.set()
            assert release.wait(3)

    def waiter():
        attempted.set()
        try:
            with background_model_slot(timeout=2, cancel_check=cancel.is_set):
                outcomes.append("acquired")
        except BackgroundSlotCancelled:
            outcomes.append("cancelled")

    first = threading.Thread(target=holder, daemon=True)
    second = threading.Thread(target=waiter, daemon=True)
    first.start()
    try:
        assert held.wait(2)
        with pytest.raises(BackgroundSlotBusy):
            with background_model_slot(timeout=0):
                pass
        second.start()
        assert attempted.wait(2)
        cancel.set()
        second.join(timeout=2)
        assert not second.is_alive()
        assert outcomes == ["cancelled"]
        assert not lock.acquire(blocking=False)
    finally:
        release.set()
        first.join(timeout=2)
        if second.ident is not None:
            second.join(timeout=2)
    assert lock.acquire(blocking=False)
    lock.release()


def test_gpu_engine_read_waits_for_shared_slot_but_cpu_read_does_not(monkeypatch):
    from photo_search.ocr.engine import PinnedOcrEngine

    lock = threading.RLock()
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", lock)
    held = threading.Event()
    release = threading.Event()
    entered = threading.Event()
    finished = threading.Event()

    def holder():
        with lock:
            held.set()
            assert release.wait(3)

    def make_engine(device, signal):
        engine = PinnedOcrEngine.__new__(PinnedOcrEngine)
        engine.resolved_device = device
        engine.orientation_retry = False
        engine.verify_runtime_provider = lambda: None
        def execute(_image):
            signal.set()
            return (["result"], 0)
        engine._engine = execute
        return engine

    gpu = make_engine("gpu", entered)
    cpu_ran = threading.Event()
    cpu = make_engine("cpu", cpu_ran)
    first = threading.Thread(target=holder, daemon=True)
    second = threading.Thread(target=lambda: (gpu.read(None), finished.set()), daemon=True)
    first.start()
    try:
        assert held.wait(2)
        second.start()
        assert cpu.read(None) == ["result"]
        assert cpu_ran.is_set()
        assert not entered.wait(0.2)
        release.set()
        assert finished.wait(2)
        assert entered.is_set()
    finally:
        release.set()
        first.join(timeout=2)
        if second.ident is not None:
            second.join(timeout=2)


def test_gpu_cache_concurrent_misses_create_one_session(monkeypatch):
    from photo_search.ocr import engine as ocr_engine

    lock = threading.RLock()
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", lock)
    entered = threading.Event()
    release = threading.Event()
    calls = []
    results = []
    errors = []
    class StubEngine:
        def __init__(self, *_args, **_kwargs):
            calls.append(1)
            entered.set()
            assert release.wait(3)

    monkeypatch.setattr(ocr_engine, "PinnedOcrEngine", StubEngine)
    cache = ocr_engine.OcrEngineCache()
    monkeypatch.setattr(cache, "verified_weights", lambda _root: object())
    monkeypatch.setattr(cache, "_signature", lambda _weights: ("fixture",))
    def invoke():
        try:
            results.append(cache.engine("fixture", resolved_device="gpu"))
        except Exception as error:
            errors.append(error)
    first = threading.Thread(target=invoke, daemon=True)
    second = threading.Thread(target=invoke, daemon=True)
    first.start()
    try:
        assert entered.wait(2)
        second.start()
        release.set()
        first.join(timeout=3)
        second.join(timeout=3)
        assert not first.is_alive() and not second.is_alive()
        assert not errors
        assert len(calls) == 1
        assert len(results) == 2 and results[0] is results[1]
    finally:
        release.set()
        first.join(timeout=2)
        if second.ident is not None:
            second.join(timeout=2)


def test_probe_and_dispatch_provider_use_same_cached_session(monkeypatch):
    from photo_search.ocr import engine as ocr_engine

    created = []
    class StubEngine:
        def __init__(self, *_args, **_kwargs):
            created.append(self)

    monkeypatch.setattr(ocr_engine, "PinnedOcrEngine", StubEngine)
    cache = ocr_engine.OcrEngineCache()
    monkeypatch.setattr(cache, "verified_weights", lambda _root: object())
    monkeypatch.setattr(cache, "_signature", lambda _weights: ("fixture",))
    probed = cache.engine("fixture", resolved_device="gpu", orientation_retry=True)
    dispatched = cache.engine(
        "fixture", resolved_device="gpu", runtime_provider="CUDAExecutionProvider",
        orientation_retry=True,
    )
    assert probed is dispatched
    assert len(created) == 1


def test_gpu_cached_session_provider_change_is_not_reported_healthy():
    from photo_search.catalog.errors import CatalogError
    from photo_search.ocr.engine import PinnedOcrEngine

    engine = PinnedOcrEngine.__new__(PinnedOcrEngine)
    engine.resolved_device = "gpu"
    engine.runtime_provider = "CUDAExecutionProvider"
    engine._engine = object()
    engine._read_session_providers = lambda _engine: {
        "det": ("CUDAExecutionProvider",), "cls": ("CPUExecutionProvider",),
        "rec": ("CUDAExecutionProvider",),
    }
    with pytest.raises(CatalogError) as error:
        engine.verify_runtime_provider()
    assert error.value.code == "OCR_GPU_INITIALIZATION_FAILED"


def test_gpu_session_initialization_waits_for_shared_slot(monkeypatch):
    from types import SimpleNamespace

    from photo_search.ocr import engine as ocr_engine

    lock = threading.RLock()
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", lock)
    monkeypatch.setattr(ocr_engine.runtime_device, "resolve_device", lambda **_kwargs: SimpleNamespace(
        device="cuda", provider="CUDAExecutionProvider",
    ))
    monkeypatch.setattr(ocr_engine.runtime_device, "preload_runtime_dlls", lambda: None)
    monkeypatch.setattr(ocr_engine.PinnedOcrEngine, "_read_session_providers", staticmethod(lambda _engine: {
        "det": ("CUDAExecutionProvider",), "cls": ("CUDAExecutionProvider",),
        "rec": ("CUDAExecutionProvider",),
    }))
    weights = SimpleNamespace(config_path="config", detection_path="det", classification_path="cls",
                              recognition_path="rec")
    held = threading.Event()
    release = threading.Event()
    factory_entered = threading.Event()
    finished = threading.Event()
    errors = []
    def holder():
        with lock:
            held.set()
            assert release.wait(3)
    def factory(**_kwargs):
        factory_entered.set()
        return object()
    def init():
        try:
            ocr_engine.PinnedOcrEngine(weights, resolved_device="gpu", engine_factory=factory)
            finished.set()
        except Exception as error:
            errors.append(error)
            finished.set()
    first = threading.Thread(target=holder, daemon=True)
    second = threading.Thread(target=init, daemon=True)
    first.start()
    try:
        assert held.wait(2)
        second.start()
        assert not factory_entered.wait(0.2)
        release.set()
        assert finished.wait(2)
        assert factory_entered.is_set() and not errors
    finally:
        release.set()
        first.join(timeout=2)
        if second.ident is not None:
            second.join(timeout=2)


def test_machine_slot_serializes_other_process_on_windows():
    import os
    import subprocess
    import sys
    import uuid

    from photo_search.runtime.machine_model_slot import MachineModelSlot

    if os.name != "nt":
        pytest.skip("Windows named mutex is the supported cross-process implementation")
    name = "Global\\PhotoSearch.Test." + uuid.uuid4().hex
    slot = MachineModelSlot(name)
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", "from photo_search.runtime.machine_model_slot import MachineModelSlot; import sys; s=MachineModelSlot(sys.argv[1]); s.acquire(); print('HELD', flush=True); sys.stdin.readline(); s.release()", name],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    ready = threading.Event()
    lines = []
    def read_ready():
        lines.append(child.stdout.readline().strip())
        ready.set()
    reader = threading.Thread(target=read_ready, daemon=True)
    reader.start()
    try:
        assert ready.wait(5), "child did not acquire the named slot"
        assert lines == ["HELD"]
        assert not slot.acquire(timeout=0.2)
        child.stdin.write("release\n")
        child.stdin.flush()
        assert child.wait(timeout=5) == 0
        assert slot.acquire(timeout=2)
        slot.release()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        reader.join(timeout=2)
        child.stdin.close()
        child.stdout.close()
        child.stderr.close()


def test_machine_slot_reentrant_and_cross_thread_timeout():
    import uuid

    from photo_search.runtime.machine_model_slot import MachineModelSlot

    slot = MachineModelSlot("Global\\PhotoSearch.Test." + uuid.uuid4().hex)
    result = []
    assert slot.acquire(timeout=1)
    try:
        assert slot.acquire(timeout=1)
        try:
            thread = threading.Thread(target=lambda: result.append(slot.acquire(timeout=0.1)), daemon=True)
            thread.start()
            thread.join(timeout=2)
            assert not thread.is_alive() and result == [False]
        finally:
            slot.release()
    finally:
        slot.release()
    assert slot.acquire(timeout=1)
    slot.release()


def test_machine_slot_recovers_after_process_exits_while_holding_mutex():
    import os
    import subprocess
    import sys
    import uuid

    from photo_search.runtime.machine_model_slot import MachineModelSlot

    if os.name != "nt":
        pytest.skip("Windows named mutex is the supported cross-process implementation")
    name = "Global\\PhotoSearch.Test." + uuid.uuid4().hex
    slot = MachineModelSlot(name)
    child = subprocess.run(
        [sys.executable, "-u", "-c", "from photo_search.runtime.machine_model_slot import MachineModelSlot; import os,sys; s=MachineModelSlot(sys.argv[1]); s.acquire(); os._exit(0)", name],
        capture_output=True, timeout=5, check=False,
    )
    assert child.returncode == 0
    assert slot.acquire(timeout=2)
    slot.release()


def test_cross_process_foreground_waiter_visible_and_crash_recovers(monkeypatch):
    import os
    import subprocess
    import sys
    import time
    import uuid

    from photo_search.runtime.background_slot import foreground_model_waiting
    from photo_search.runtime.machine_model_slot import MachineModelSlot

    if os.name != "nt":
        pytest.skip("Windows named mutex is required")
    name = "Global\\PhotoSearch.Test." + uuid.uuid4().hex
    slot = MachineModelSlot(name)
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", slot)
    script = (
        "from photo_search.face import service; "
        "from photo_search.runtime.machine_model_slot import MachineModelSlot; "
        "from photo_search.runtime.background_slot import foreground_model_slot; "
        "import os,sys; service.BACKGROUND_MODEL_SLOT=MachineModelSlot(sys.argv[1]); "
        "print(os.getpid(),flush=True); context=foreground_model_slot(); context.__enter__(); "
        "print('ENTERED',flush=True); sys.stdin.readline(); context.__exit__(None,None,None)"
    )
    assert slot.acquire(timeout=1)
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", script, name],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    real_pid = None
    try:
        pid_ready = threading.Event()
        pid_line = []
        def read_pid():
            pid_line.append(child.stdout.readline().strip())
            pid_ready.set()
        reader = threading.Thread(target=read_pid, daemon=True)
        reader.start()
        assert pid_ready.wait(5) and pid_line[0].isdigit()
        real_pid = int(pid_line[0])
        deadline = time.monotonic() + 5
        while not foreground_model_waiting() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert foreground_model_waiting(), "other process never advertised its wait"
        import signal
        os.kill(real_pid, signal.SIGTERM)
        assert child.wait(timeout=5) != 0
        real_pid = None
        assert not foreground_model_waiting(), "abandoned foreground marker was not recovered"
    finally:
        if real_pid is not None:
            import signal
            try:
                os.kill(real_pid, signal.SIGTERM)
            except OSError:
                pass
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        slot.release()
        child.stdin.close()
        child.stdout.close()
        child.stderr.close()


def test_slot_owner_can_nest_foreground_while_another_query_waits(monkeypatch):
    import time
    import uuid

    from photo_search.runtime.background_slot import foreground_model_slot, foreground_model_waiting
    from photo_search.runtime.machine_model_slot import MachineModelSlot

    slot = MachineModelSlot("Global\\PhotoSearch.Test." + uuid.uuid4().hex)
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", slot)
    entered = threading.Event()
    def foreground():
        with foreground_model_slot():
            entered.set()
    with slot:
        waiter = threading.Thread(target=foreground, daemon=True)
        waiter.start()
        deadline = time.monotonic() + 3
        while not foreground_model_waiting() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert foreground_model_waiting()
        with foreground_model_slot():
            assert not entered.is_set()
    waiter.join(3)
    assert not waiter.is_alive() and entered.is_set()


def test_face_cached_probe_preserves_original_device_observation_times(monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace

    from photo_search.face import engine as face_engine
    from photo_search.face import weights

    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", threading.RLock())
    actual = face_engine.runtime_capabilities()
    monkeypatch.setattr(face_engine, "runtime_capabilities", lambda: replace(
        actual, providers=("CPUExecutionProvider", "CUDAExecutionProvider"),
    ))
    monkeypatch.setattr(weights, "resolve", lambda _root: object())
    cache = face_engine.FaceEngineCache()
    monkeypatch.setattr(cache, "_signature", lambda _weights: ("test",))
    monkeypatch.setattr(cache, "engine", lambda _root, *, device: SimpleNamespace(
        provider="CUDAExecutionProvider" if device == "gpu" else "CPUExecutionProvider",
        is_degradation=False, reason="fixture", detector_providers=[],
        recognizer_providers=[], mixed_cuda_detection=False,
        recognition_cpu_fallback_disabled=True,
    ))
    now = [100]
    first = cache.probe("fixture", observation_clock=lambda: now[0])
    assert first["observed_at"] == {"enumeration": 100, "cpu": 100, "gpu": 100}
    now[0] = 200
    second = cache.probe("fixture", observation_clock=lambda: now[0])
    assert second["observed_at"] == {"enumeration": 200, "cpu": 100, "gpu": 100}


def test_face_cached_sessions_recheck_own_provider_before_refreshing_time(monkeypatch):
    from dataclasses import replace

    from photo_search.catalog.errors import FACE_DEVICE_UNAVAILABLE, CatalogError
    from photo_search.face import engine as face_engine
    from photo_search.face import weights

    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", threading.RLock())
    actual = face_engine.runtime_capabilities()
    monkeypatch.setattr(face_engine, "runtime_capabilities", lambda: replace(
        actual, providers=("CPUExecutionProvider", "CUDAExecutionProvider"),
    ))
    monkeypatch.setattr(weights, "resolve", lambda _root: object())
    cache = face_engine.FaceEngineCache()
    monkeypatch.setattr(cache, "_signature", lambda _weights: ("test",))
    class Engine:
        def __init__(self, device):
            self.provider = "CUDAExecutionProvider" if device == "gpu" else "CPUExecutionProvider"
            self.is_degradation, self.reason = False, "fixture"
            self.detector_providers, self.recognizer_providers = [self.provider], [self.provider]
            self.mixed_cuda_detection = False
            self.recognition_cpu_fallback_disabled = True
            self.failed = False
        def verify_runtime_provider(self):
            if self.failed:
                raise CatalogError(FACE_DEVICE_UNAVAILABLE, "changed provider")
    engines = {device: Engine(device) for device in ("cpu", "gpu")}
    for device, engine in engines.items():
        cache._engines[("test", device)] = engine
    monkeypatch.setattr(cache, "engine", lambda _root, *, device: engines[device])
    now = [100]
    cache.probe("fixture", observation_clock=lambda: now[0])
    now[0] = 200
    result = cache.probe("fixture", observation_clock=lambda: now[0])
    assert result["observed_at"] == {"enumeration": 200, "cpu": 200, "gpu": 200}
    engines["gpu"].failed = True
    now[0] = 300
    with pytest.raises(CatalogError) as error:
        cache.probe("fixture", observation_clock=lambda: now[0])
    assert error.value.code == FACE_DEVICE_UNAVAILABLE


def test_face_engine_checks_both_live_session_provider_lists():
    from photo_search.catalog.errors import FACE_DEVICE_UNAVAILABLE, CatalogError
    from photo_search.face.engine import FaceEngine

    class Session:
        def __init__(self, providers):
            self.providers = providers
        def get_providers(self):
            return self.providers
    engine = FaceEngine.__new__(FaceEngine)
    engine.device, engine.provider = "cuda", "CUDAExecutionProvider"
    engine.detector = Session(["CUDAExecutionProvider", "CPUExecutionProvider"])
    engine.recognizer = Session(["CUDAExecutionProvider"])
    engine.verify_runtime_provider()
    engine.recognizer.providers = ["CPUExecutionProvider"]
    with pytest.raises(CatalogError) as error:
        engine.verify_runtime_provider()
    assert error.value.code == FACE_DEVICE_UNAVAILABLE
    assert error.value.details["role"] == "recognition"


def test_face_failed_session_retry_is_bounded_and_can_recover(monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace

    from photo_search.catalog.errors import FACE_DEVICE_UNAVAILABLE, CatalogError
    from photo_search.face import engine as face_engine
    from photo_search.face import weights

    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", threading.RLock())
    actual = face_engine.runtime_capabilities()
    monkeypatch.setattr(face_engine, "runtime_capabilities", lambda: replace(
        actual, providers=("CPUExecutionProvider", "CUDAExecutionProvider"),
    ))
    monkeypatch.setattr(weights, "resolve", lambda _root: object())
    cache = face_engine.FaceEngineCache()
    monkeypatch.setattr(cache, "_signature", lambda _weights: ("test",))
    attempts = []
    state = {"gpu_ready": False}
    def open_engine(_root, *, device):
        if device == "gpu":
            attempts.append(device)
            if not state["gpu_ready"]:
                raise CatalogError(FACE_DEVICE_UNAVAILABLE, "synthetic GPU unavailable")
        provider = "CUDAExecutionProvider" if device == "gpu" else "CPUExecutionProvider"
        return SimpleNamespace(
            provider=provider, is_degradation=False, reason="fixture",
            detector_providers=[provider], recognizer_providers=[provider],
            mixed_cuda_detection=False, recognition_cpu_fallback_disabled=True,
        )
    monkeypatch.setattr(cache, "engine", open_engine)
    now = [100]
    first = cache.probe("fixture", observation_clock=lambda: now[0])
    assert not first["gpu"]["usable"] and len(attempts) == 1
    state["gpu_ready"] = True
    now[0] = 101
    second = cache.probe("fixture", observation_clock=lambda: now[0])
    assert not second["gpu"]["usable"] and len(attempts) == 1
    now[0] = 100 + face_engine.FACE_FAILED_PROBE_RETRY_MS
    recovered = cache.probe("fixture", observation_clock=lambda: now[0])
    assert recovered["gpu"]["usable"] and len(attempts) == 2
    assert recovered["observed_at"]["gpu"] == now[0]


@pytest.mark.parametrize(
    ("device", "expected", "detection", "recognition", "valid"),
    [("cuda", "CUDAExecutionProvider", ["CUDAExecutionProvider"], ["CUDAExecutionProvider"], False),
     ("cuda", "CUDAExecutionProvider", ["CPUExecutionProvider", "CUDAExecutionProvider"],
      ["CUDAExecutionProvider"], False),
     ("cpu", "CPUExecutionProvider", ["CPUExecutionProvider"], ["CPUExecutionProvider"], True)],
)
def test_face_revalidation_covers_detector_and_cpu_only(
    device, expected, detection, recognition, valid
):
    from photo_search.catalog.errors import CatalogError
    from photo_search.face.engine import FaceEngine

    class Session:
        def __init__(self, providers):
            self.providers = providers
        def get_providers(self):
            return self.providers
    engine = FaceEngine.__new__(FaceEngine)
    engine.device, engine.provider = device, expected
    engine.detector, engine.recognizer = Session(detection), Session(recognition)
    if valid:
        engine.verify_runtime_provider()
    else:
        with pytest.raises(CatalogError) as error:
            engine.verify_runtime_provider()
        assert error.value.details["role"] == "detection"


def test_face_gpu_cache_concurrent_attempts_create_only_once(monkeypatch):
    from types import SimpleNamespace

    from photo_search.face import engine as face_engine
    from photo_search.face import weights

    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", threading.RLock())
    monkeypatch.setattr(weights, "resolve", lambda _root: object())
    cache = face_engine.FaceEngineCache()
    monkeypatch.setattr(cache, "_signature", lambda _weights: ("test",))
    entered, release = threading.Event(), threading.Event()
    calls, results, errors = [], [], []
    def open_engine(_root, *, device):
        calls.append(device)
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(
            provider="CUDAExecutionProvider", is_degradation=False, reason="fixture",
            detector_providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
            recognizer_providers=["CUDAExecutionProvider"], mixed_cuda_detection=True,
            recognition_cpu_fallback_disabled=True,
        )
    monkeypatch.setattr(cache, "engine", open_engine)
    def attempt():
        try:
            results.append(cache.attempt("fixture", "gpu", observation_clock=lambda: 100))
        except Exception as error:
            errors.append(error)
    first = threading.Thread(target=attempt, daemon=True)
    second = threading.Thread(target=attempt, daemon=True)
    first.start()
    try:
        assert entered.wait(2)
        second.start()
        release.set()
        first.join(3)
        second.join(3)
        assert not first.is_alive() and not second.is_alive()
        assert not errors and len(results) == 2
        assert calls == ["gpu"]
    finally:
        release.set()
        first.join(3)
        if second.ident is not None:
            second.join(3)


def test_face_engine_cache_singleflight_covers_real_factory_path(monkeypatch):
    from photo_search.face import engine as face_engine

    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", threading.RLock())
    cache = face_engine.FaceEngineCache()
    monkeypatch.setattr(cache, "verified_weights", lambda _root: object())
    monkeypatch.setattr(cache, "_signature", lambda _weights: ("test",))
    entered, release = threading.Event(), threading.Event()
    created, results, errors = [], [], []
    def factory(_weights, **_kwargs):
        instance = object()
        created.append(instance)
        entered.set()
        assert release.wait(3)
        return instance
    monkeypatch.setattr(face_engine, "FaceEngine", factory)
    def run():
        try:
            results.append(cache.engine("fixture", device="gpu"))
        except Exception as error:
            errors.append(error)
    first = threading.Thread(target=run, daemon=True)
    second = threading.Thread(target=run, daemon=True)
    first.start()
    try:
        assert entered.wait(2)
        second.start()
        release.set()
        first.join(3)
        second.join(3)
        assert not first.is_alive() and not second.is_alive()
        assert not errors and len(created) == 1 and len(results) == 2
        assert results[0] is results[1] is created[0]
    finally:
        release.set()
        first.join(3)
        if second.ident is not None:
            second.join(3)


def test_face_cache_shares_session_for_equivalent_gpu_device_names(monkeypatch):
    from photo_search.face import engine as face_engine

    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", threading.RLock())
    cache = face_engine.FaceEngineCache()
    monkeypatch.setattr(cache, "verified_weights", lambda _root: object())
    monkeypatch.setattr(cache, "_signature", lambda _weights: ("test",))
    calls = []
    def factory(_weights, *, device, **_kwargs):
        calls.append(device)
        return object()
    monkeypatch.setattr(face_engine, "FaceEngine", factory)
    automatic = cache.engine("fixture", device="auto")
    assert cache.engine("fixture", device="gpu") is automatic
    assert cache.engine("fixture", device="cuda") is automatic
    assert cache.engine("fixture", device="host") is not automatic
    assert calls == ["auto", "host"]


def test_face_sessions_are_released_before_the_ocr_model_family_starts():
    from photo_search.face.engine import FaceEngineCache

    class Engine:
        def __init__(self):
            self.closed = 0

        def close(self):
            self.closed += 1

    engine = Engine()
    cache = FaceEngineCache()
    cache._engines[("weights", "gpu")] = engine
    cache._attempts[("weights", "gpu")] = {"usable": True}

    cache.release_sessions()

    assert engine.closed == 1
    assert cache._engines == {}
    assert cache._attempts == {}


def test_face_session_release_failure_remains_retryable():
    from photo_search.face.engine import FaceEngineCache

    class Engine:
        def __init__(self) -> None:
            self.calls = 0

        def close(self) -> None:
            self.calls += 1
            if self.calls == 1:
                raise OSError("synthetic face session shutdown failure")

    key = ("weights", "gpu")
    engine = Engine()
    cache = FaceEngineCache()
    cache._engines[key] = engine
    cache._attempts[key] = {"usable": True}

    with pytest.raises(RuntimeError, match="could not be released"):
        cache.release_sessions()
    assert cache._engines[key] is engine
    cache.release_sessions()
    assert cache._engines == {}
    assert cache._attempts == {}


@pytest.mark.parametrize("first,second", [("auto", "gpu"), ("gpu", "auto"), ("cpu", "host")])
def test_face_cache_reports_current_request_device_and_reason(monkeypatch, first, second):
    from types import SimpleNamespace

    from photo_search.face import engine as face_engine
    from photo_search.face import weights

    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", threading.RLock())
    monkeypatch.setattr(weights, "resolve", lambda _root: object())
    cache = face_engine.FaceEngineCache()
    monkeypatch.setattr(cache, "_signature", lambda _weights: ("test",))
    calls = []
    def open_engine(_root, *, device):
        calls.append(device)
        gpu = face_engine._face_device_cache_key(device) == "gpu"
        provider = "CUDAExecutionProvider" if gpu else "CPUExecutionProvider"
        return SimpleNamespace(
            provider=provider, is_degradation=not gpu, reason="initial reason",
            detector_providers=[provider], recognizer_providers=[provider],
            mixed_cuda_detection=False, recognition_cpu_fallback_disabled=gpu,
        )
    monkeypatch.setattr(cache, "engine", open_engine)
    first_result = cache.attempt("fixture", first, observation_clock=lambda: 100)
    second_result = cache.attempt("fixture", second, observation_clock=lambda: 100)
    assert first_result["device"] == first and second_result["device"] == second
    expected = "default for cpu_allowed" if second == "auto" else (
        "explicit CPU request" if second == "host" else "explicit CUDA request"
    )
    assert second_result["reason"] == expected
    assert calls == [first]


def test_face_cache_rejects_noncanonical_auto_before_and_after_cache(monkeypatch):
    from photo_search.face import engine as face_engine

    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", threading.RLock())
    cache = face_engine.FaceEngineCache()
    monkeypatch.setattr(cache, "verified_weights", lambda _root: object())
    monkeypatch.setattr(cache, "_signature", lambda _weights: ("test",))
    monkeypatch.setattr(face_engine, "FaceEngine", lambda *_args, **_kwargs: object())
    with pytest.raises(ValueError):
        cache.engine("fixture", device="AUTO")
    cache.engine("fixture", device="auto")
    with pytest.raises(ValueError):
        cache.engine("fixture", device="AUTO")


def test_ocr_weight_cache_reverifies_when_file_signature_changes(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from photo_search.ocr import engine as ocr_engine
    from photo_search.ocr import weights as ocr_weights

    path = tmp_path / "weights.bin"
    path.write_bytes(b"v1")
    weights = SimpleNamespace(root=tmp_path, files=[SimpleNamespace(
        absolute_path=path, relative_path="weights.bin",
    )])
    monkeypatch.setattr(ocr_weights, "resolve", lambda _root: weights)
    checked = []
    def verify(value):
        checked.append(path.read_bytes())
        return value
    monkeypatch.setattr(ocr_engine, "verify", verify)
    cache = ocr_engine.OcrEngineCache()
    assert cache.verified_weights(tmp_path) is weights
    assert cache.verified_weights(tmp_path) is weights
    assert checked == [b"v1"]
    path.write_bytes(b"changed-size")
    assert cache.verified_weights(tmp_path) is weights
    assert checked == [b"v1", b"changed-size"]


def test_ocr_cuda_alias_shares_gpu_cache_and_serializes_creation(monkeypatch):
    from photo_search.ocr import engine as ocr_engine

    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", threading.RLock())
    cache = ocr_engine.OcrEngineCache()
    monkeypatch.setattr(cache, "verified_weights", lambda _root: object())
    monkeypatch.setattr(cache, "_signature", lambda _weights: ("fixture",))
    entered, release = threading.Event(), threading.Event()
    calls, results, errors = [], [], []
    def create(_weights, *, resolved_device, **_kwargs):
        calls.append(resolved_device)
        entered.set()
        assert release.wait(3)
        return object()
    monkeypatch.setattr(ocr_engine, "PinnedOcrEngine", create)
    def run(device):
        try:
            results.append(cache.engine("fixture", resolved_device=device))
        except Exception as error:
            errors.append(error)
    first = threading.Thread(target=run, args=("cuda",), daemon=True)
    second = threading.Thread(target=run, args=("gpu",), daemon=True)
    first.start()
    try:
        assert entered.wait(2)
        second.start()
        release.set()
        first.join(3)
        second.join(3)
        assert not first.is_alive() and not second.is_alive()
        assert not errors and len(calls) == 1 and len(results) == 2
        assert results[0] is results[1]
    finally:
        release.set()
        first.join(3)
        if second.ident is not None:
            second.join(3)
