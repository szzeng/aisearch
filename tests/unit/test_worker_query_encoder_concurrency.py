"""Keep every operation on the shared worker request/reply channel serialized."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from photo_search.catalog.errors import RUNTIME_CAPABILITY_UNAVAILABLE, CatalogError
from photo_search.workers.query_encoder import WorkerQueryEncoder, pinned_embedding_spec

MANIFEST = (
    Path(__file__).resolve().parents[2]
    / "tools/prepare_models/manifests/chinese-clip-vit-b-16.json"
)
VECTOR = [0.01] * 512


class Upload:
    def __init__(self) -> None:
        self.saved: list[Path] = []

    def save(self, path: str, image_format: str) -> None:
        assert image_format == "PNG"
        target = Path(path)
        self.saved.append(target)
        target.write_bytes(b"synthetic-upload")


class ControlledWorker:
    """Hold the first call so another caller reaches the same channel boundary."""

    def __init__(self, *, block_first: bool = True) -> None:
        self.block_first = block_first
        self.first_started = threading.Event()
        self.second_boundary = threading.Event()
        self.release_first = threading.Event()
        self.guard = threading.Lock()
        self.active = 0
        self.maximum_active = 0
        self.actions: list[str] = []

    def _exchange(self, action: str, reply: Any) -> Any:
        with self.guard:
            self.actions.append(action)
            first = len(self.actions) == 1
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        try:
            if first and self.block_first:
                self.first_started.set()
                assert self.release_first.wait(5), "first worker call was not released"
            elif not first:
                self.second_boundary.set()
            return reply
        finally:
            with self.guard:
                self.active -= 1

    def health(self, timeout: float) -> dict[str, Any]:
        return self._exchange("health", {"loaded": True})

    def encode_text(self, text: str) -> dict[str, Any]:
        return self._exchange("text", {"vector": VECTOR})

    def encode_image(self, path: Path) -> dict[str, Any]:
        return self._exchange("image", {"vector": VECTOR})

    def shutdown(self) -> None:
        self._exchange("shutdown", None)


class ObservedLock:
    """Observe a contender without sleeps; mutual exclusion still uses the real lock."""

    def __init__(self, lock: Any, second_boundary: threading.Event) -> None:
        self.lock = lock
        self.second_boundary = second_boundary

    def __enter__(self) -> None:
        if threading.current_thread().name == "worker-second":
            self.second_boundary.set()
        self.lock.acquire()

    def __exit__(self, *_: object) -> None:
        self.lock.release()


def invoke(adapter: WorkerQueryEncoder, operation: str, upload: Upload) -> Any:
    if operation == "text":
        return adapter.encode_queries(["synthetic"])
    if operation == "path":
        return adapter.encode_image(Path("synthetic.png"))
    if operation == "upload":
        return adapter.encode_images([upload])
    return getattr(adapter, operation)()


@pytest.mark.parametrize(
    ("first_operation", "second_operation"),
    [(first, second) for first in ("text", "path", "upload", "health") for second in ("health", "close")]
    + [("text", "path"), ("path", "text")],
)
def test_health_encoding_and_close_share_one_channel(
    first_operation: str, second_operation: str
) -> None:
    worker = ControlledWorker()
    adapter = WorkerQueryEncoder(worker, pinned_embedding_spec(MANIFEST))
    adapter._lock = ObservedLock(adapter._lock, worker.second_boundary)
    upload = Upload()
    outcomes: dict[str, Any] = {}
    errors: list[BaseException] = []

    def run(label: str, operation: str) -> None:
        try:
            outcomes[label] = invoke(adapter, operation, upload)
        except BaseException as error:
            errors.append(error)

    first = threading.Thread(target=run, args=("first", first_operation), name="worker-first", daemon=True)
    second = threading.Thread(target=run, args=("second", second_operation), name="worker-second", daemon=True)
    first.start()
    try:
        assert worker.first_started.wait(5)
        second.start()
        # On the fixed code the second caller is waiting on the observed real lock.
        # On the old code health/close enter the client directly and signal there.
        assert worker.second_boundary.wait(5)
    finally:
        worker.release_first.set()
        first.join(5)
        if second.ident is not None:
            second.join(5)
    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    expected = {"text": [VECTOR], "path": VECTOR, "upload": [VECTOR], "health": {"loaded": True}, "close": None}
    assert outcomes == {"first": expected[first_operation], "second": expected[second_operation]}
    assert worker.maximum_active == 1, "shared request/reply channel was used concurrently"
    assert len(worker.actions) == 2
    assert all(not path.exists() for path in upload.saved)
    adapter.close()


@pytest.mark.parametrize("operation", ["health", "text", "path", "upload"])
def test_closed_encoder_refuses_new_calls_and_close_is_idempotent(operation: str) -> None:
    worker = ControlledWorker(block_first=False)
    adapter = WorkerQueryEncoder(worker, pinned_embedding_spec(MANIFEST))
    upload = Upload()
    adapter.close()
    adapter.close()
    with pytest.raises(CatalogError) as refused:
        invoke(adapter, operation, upload)
    assert refused.value.code == RUNTIME_CAPABILITY_UNAVAILABLE
    assert refused.value.details == {"reason": "WORKER_CLOSED"}
    assert worker.actions == ["shutdown"]
    assert all(not path.exists() for path in upload.saved)


@pytest.mark.parametrize("operation", ["health", "text", "path", "upload", "close"])
def test_calls_waiting_behind_shutdown_cannot_reopen_the_channel(operation: str) -> None:
    worker = ControlledWorker()
    adapter = WorkerQueryEncoder(worker, pinned_embedding_spec(MANIFEST))
    adapter._lock = ObservedLock(adapter._lock, worker.second_boundary)
    upload = Upload()
    errors: list[BaseException] = []

    def run(selected: str) -> None:
        try:
            invoke(adapter, selected, upload)
        except BaseException as error:
            errors.append(error)

    closing = threading.Thread(target=run, args=("close",), name="worker-first", daemon=True)
    waiting = threading.Thread(target=run, args=(operation,), name="worker-second", daemon=True)
    closing.start()
    try:
        assert worker.first_started.wait(5)
        waiting.start()
        assert worker.second_boundary.wait(5)
    finally:
        worker.release_first.set()
        closing.join(5)
        if waiting.ident is not None:
            waiting.join(5)
    assert not closing.is_alive() and not waiting.is_alive()
    if operation == "close":
        assert errors == []
    else:
        assert len(errors) == 1 and isinstance(errors[0], CatalogError)
        assert errors[0].code == RUNTIME_CAPABILITY_UNAVAILABLE
        assert errors[0].details == {"reason": "WORKER_CLOSED"}
    assert worker.actions == ["shutdown"]
    assert all(not path.exists() for path in upload.saved)
