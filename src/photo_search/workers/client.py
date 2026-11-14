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
from photo_search.runtime.process_ownership import WindowsJobObject
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


class WorkerShutdownError(RuntimeError):
    """The child, queues or diagnostic file could not all be released."""


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
        worker_target: Any | None = None,
    ) -> None:
        self._ctx = multiprocessing.get_context("spawn")
        self._requests = self._ctx.Queue(maxsize=queue_size)
        self._responses = self._ctx.Queue(maxsize=queue_size)
        self._start_gate = self._ctx.Event()
        self.device = device
        self.device_policy = DevicePolicy(device_policy)
        stderr_fd, stderr_path = tempfile.mkstemp(prefix="photo-search-ai-", suffix=".stderr")
        os.close(stderr_fd)
        self._stderr_file = Path(stderr_path)
        self._process = self._ctx.Process(
            target=worker_target or run_worker,
            args=(
                self._requests,
                self._responses,
                str(model_dir),
                str(manifest_path),
                str(self._stderr_file),
                device,
                self.device_policy.value,
                self._start_gate,
                os.getpid(),
            ),
            name="photo-search-ai",
        )
        self._started = False
        self._job: WindowsJobObject | None = None
        self._shutdown_lock = threading.RLock()
        self._shutdown_complete = False
        self._closed_queues: set[str] = set()

    @property
    def pid(self) -> int | None:
        return self._process.pid

    def start(self) -> None:
        if not self._started:
            self._process.start()
            try:
                job = WindowsJobObject()
                job.assign(int(self._process.pid))
                self._job = job
            except BaseException:
                self._stop()
                self._close_job(raise_errors=True)
                self._start_gate.set()
                raise
            self._started = True
            self._start_gate.set()

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
        """Release every child-owned resource, retaining failed handles for a retry."""
        with self._shutdown_lock:
            if self._shutdown_complete:
                return
            errors: list[BaseException] = []
            try:
                if self._started and self._process.is_alive():
                    try:
                        self._call({"action": "shutdown"}, timeout)
                    except (WorkerError, WorkerTimeoutError):
                        self._stop()
                    self._process.join(timeout)
                    if self._process.is_alive():
                        self._stop()
                if self._started and self._process.is_alive():
                    raise WorkerShutdownError(
                        "the AI worker process remained alive after terminate/kill"
                    )
            except BaseException as error:
                errors.append(error)
            errors.extend(self._close_queues())
            errors.extend(self._close_job())
            try:
                self._cleanup_stderr_file()
            except BaseException as error:
                errors.append(error)
            if errors:
                raise WorkerShutdownError(
                    "AI worker shutdown did not release every resource"
                ) from errors[0]
            if self._started:
                try:
                    self._process.close()
                except BaseException as error:
                    raise WorkerShutdownError(
                        "the AI worker process handle could not be closed"
                    ) from error
            self._shutdown_complete = True

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
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            remaining = deadline - time.monotonic()
            try:
                response = self._responses.get(timeout=max(0.0, min(0.1, remaining)))
                break
            except queue.Empty:
                if not self._process.is_alive():
                    try:
                        response = self._responses.get(timeout=0.0)
                        break
                    except queue.Empty as error:
                        raise WorkerError(
                            {
                                "exception_type": "WorkerExited",
                                "message": "the AI worker exited before replying",
                                "stage": request.get("action", "request"),
                                "exitcode": self._process.exitcode,
                            },
                            self.stderr_tail(),
                        ) from error
                if remaining <= 0:
                    self._stop()
                    raise WorkerTimeoutError(
                        f"worker timed out after {timeout} seconds"
                    )
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

    def _close_queues(self) -> list[BaseException]:
        errors: list[BaseException] = []
        for name, channel in (
            ("requests", self._requests),
            ("responses", self._responses),
        ):
            if name in self._closed_queues:
                continue
            try:
                channel.close()
                channel.join_thread()
                self._closed_queues.add(name)
            except BaseException as error:
                errors.append(error)
        return errors

    def _close_job(self, *, raise_errors: bool = False) -> list[BaseException]:
        job = self._job
        if job is None:
            return []
        try:
            job.close()
            self._job = None
            return []
        except BaseException as error:
            if raise_errors:
                raise
            return [error]

    def _cleanup_stderr_file(self) -> None:
        try:
            self._stderr_file.unlink(missing_ok=True)
        except OSError as error:
            if self._stderr_file.exists():
                raise WorkerShutdownError(
                    "the AI worker diagnostic file could not be removed"
                ) from error

    def __enter__(self) -> AiWorkerClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.shutdown()
