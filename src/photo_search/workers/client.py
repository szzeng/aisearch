"""Parent-side lifecycle management for the spawned AI worker."""

from __future__ import annotations

import multiprocessing
import os
import queue
import tempfile
from pathlib import Path
from typing import Any

from photo_search.workers.ai_worker import run_worker
from photo_search.workers.protocol import checked_message


class WorkerError(RuntimeError):
    """A worker returned an error envelope or exited unexpectedly."""

    def __init__(self, payload: dict[str, Any], stderr_tail: str) -> None:
        super().__init__(f"{payload.get('exception_type')}: {payload.get('message')}")
        self.payload = payload
        self.stderr_tail = stderr_tail


class WorkerTimeoutError(TimeoutError):
    """A request exceeded its deadline; the worker has already been stopped."""


class AiWorkerClient:
    """A single-worker client using spawn and queues bounded to eight messages."""

    def __init__(self, model_dir: Path, manifest_path: Path, *, queue_size: int = 8) -> None:
        self._ctx = multiprocessing.get_context("spawn")
        self._requests = self._ctx.Queue(maxsize=queue_size)
        self._responses = self._ctx.Queue(maxsize=queue_size)
        stderr_fd, stderr_path = tempfile.mkstemp(prefix="photo-search-ai-", suffix=".stderr")
        os.close(stderr_fd)
        self._stderr_file = Path(stderr_path)
        self._process = self._ctx.Process(
            target=run_worker,
            args=(
                self._requests,
                self._responses,
                str(model_dir),
                str(manifest_path),
                str(self._stderr_file),
            ),
            name="photo-search-ai",
        )
        self._started = False

    @property
    def pid(self) -> int | None:
        return self._process.pid

    def start(self) -> None:
        if not self._started:
            self._process.start()
            self._started = True

    def health(self, timeout: float = 30.0) -> dict[str, Any]:
        return self._call({"action": "health"}, timeout)

    def encode_text(self, text: str, timeout: float = 30.0) -> dict[str, Any]:
        return self._call({"action": "encode_text", "text": text}, timeout)

    def encode_image(self, image_path: Path, timeout: float = 30.0) -> dict[str, Any]:
        return self._call({"action": "encode_image", "image_path": str(image_path)}, timeout)

    def shutdown(self, timeout: float = 10.0) -> None:
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
        self.start()
        checked = checked_message(request)
        try:
            self._requests.put(checked, timeout=min(timeout, 1.0))
        except queue.Full as error:
            raise WorkerError(
                {"exception_type": "QueueFull", "message": "worker request queue is full"},
                self.stderr_tail(),
            ) from error
        try:
            response = self._responses.get(timeout=timeout)
        except queue.Empty as error:
            self._stop()
            raise WorkerTimeoutError(f"worker timed out after {timeout} seconds") from error
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
            channel.close()
            channel.join_thread()

    def _cleanup_stderr_file(self) -> None:
        try:
            self._stderr_file.unlink(missing_ok=True)
        except OSError:
            pass

    def __enter__(self) -> AiWorkerClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.shutdown()
