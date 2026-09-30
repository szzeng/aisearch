"""Parent-side lifecycle management for the spawned AI worker."""

from __future__ import annotations

import multiprocessing
import os
import queue
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from photo_search.runtime.device import DevicePolicy
from photo_search.workers.ai_worker import run_worker
from photo_search.workers.protocol import checked_message


class WorkerError(RuntimeError):
    """A worker returned an error envelope or exited unexpectedly.

    ``payload["details"]`` carries the device/provider facts when the failure came from
    an unavailable or failing execution device, so callers never parse error text.
    """

    def __init__(self, payload: dict[str, Any], stderr_tail: str) -> None:
        super().__init__(f"{payload.get('exception_type')}: {payload.get('message')}")
        self.payload = payload
        self.stderr_tail = stderr_tail

    @property
    def details(self) -> dict[str, Any]:
        return dict(self.payload.get("details") or {})


class WorkerTimeoutError(TimeoutError):
    """A request exceeded its deadline; the worker has already been stopped."""


class AiWorkerClient:
    """A single-worker client using spawn and queues bounded to eight messages."""

    def __init__(
        self,
        model_dir: Path,
        manifest_path: Path,
        *,
        device: str | None = None,
        device_policy: DevicePolicy | str = DevicePolicy.CPU_ALLOWED,
        queue_size: int = 8,
    ) -> None:
        self._ctx = multiprocessing.get_context("spawn")
        self._requests = self._ctx.Queue(maxsize=queue_size)
        self._responses = self._ctx.Queue(maxsize=queue_size)
        self.device = device
        self.device_policy = DevicePolicy(device_policy)
        stderr_fd, stderr_path = tempfile.mkstemp(prefix="photo-search-ai-", suffix=".stderr")
        os.close(stderr_fd)
        self._stderr_file = Path(stderr_path)
        self._queue_size = queue_size
        self._worker_args = (str(model_dir), str(manifest_path), str(self._stderr_file),
                             device, self.device_policy.value)
        self._process = self._new_process()
        self._started = False
        self._closed = False
        self._lock = threading.RLock()

    def _new_process(self):
        return self._ctx.Process(target=run_worker,
                                 args=(self._requests, self._responses, *self._worker_args),
                                 name="photo-search-ai")

    @property
    def pid(self) -> int | None:
        return self._process.pid

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise WorkerError({"message": "AI worker is closed"}, self.stderr_tail())
            if self._started and not self._process.is_alive():
                self._close_queues()
                self._requests = self._ctx.Queue(maxsize=self._queue_size)
                self._responses = self._ctx.Queue(maxsize=self._queue_size)
                self._process = self._new_process()
                self._started = False
            if not self._started:
                self._process.start()
                self._started = True

    def health(self, timeout: float = 60.0) -> dict[str, Any]:
        return self._call({"action": "health"}, timeout)

    def encode_text(self, text: str, timeout: float = 30.0) -> dict[str, Any]:
        return self._call({"action": "encode_text", "text": text}, timeout)

    def encode_image(self, image_path: Path, timeout: float = 60.0) -> dict[str, Any]:
        return self._call({"action": "encode_image", "image_path": str(image_path)}, timeout)

    def demo_search(self, timeout: float = 180.0) -> dict[str, Any]:
        return self._call({"action": "demo_search"}, timeout)

    def provider_usage(self, timeout: float = 60.0) -> dict[str, Any]:
        """Per-node execution providers recorded by ONNX Runtime profiling."""
        return self._call({"action": "provider_usage"}, timeout)

    def shutdown(self, timeout: float = 10.0) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._shutdown(timeout)
            finally:
                self._closed = True

    def _shutdown(self, timeout: float) -> None:
        if self._started and self._process.is_alive():
            try:
                self._call({"action": "shutdown"}, timeout)
            except (WorkerError, WorkerTimeoutError):
                self._stop()
            self._process.join(timeout)
            if self._process.is_alive():
                self._stop()
        self._close_queues()
        self._cleanup_stderr_file()

    def _call(self, request: dict[str, Any], timeout: float) -> dict[str, Any]:
        # The lock covers the complete exchange, including health and shutdown. Locking
        # only encode calls allows one caller to consume another caller's response.
        with self._lock:
            return self._exchange(request, timeout)

    def _exchange(self, request: dict[str, Any], timeout: float) -> dict[str, Any]:
        self.start()
        checked = checked_message(request)
        try:
            self._requests.put(checked, timeout=min(timeout, 1.0))
        except queue.Full as error:
            raise WorkerError(
                {"exception_type": "QueueFull", "message": "worker request queue is full"},
                self.stderr_tail(),
            ) from error
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._stop()
                raise WorkerTimeoutError(f"worker timed out after {timeout} seconds")
            try:
                response = self._responses.get(timeout=min(remaining, 0.1))
                break
            except queue.Empty:
                if not self._process.is_alive():
                    raise WorkerError(
                        {"message": "AI worker exited before replying"}, self.stderr_tail()
                    ) from None
        if response.get("status") != "ok":
            raise WorkerError(response, self.stderr_tail())
        return dict(response["result"])

    def _stop(self) -> None:
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(2)
        if self._process.is_alive():
            self._process.kill()
            self._process.join(2)

    def stderr_tail(self, limit: int = 4096) -> str:
        try:
            return self._stderr_file.read_text(encoding="utf-8")[-limit:]
        except OSError:
            return ""

    def _close_queues(self) -> None:
        for channel in (self._requests, self._responses):
            channel.cancel_join_thread()
            channel.close()

    def _cleanup_stderr_file(self) -> None:
        try:
            self._stderr_file.unlink(missing_ok=True)
        except OSError:
            pass

    def __enter__(self) -> AiWorkerClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.shutdown()
