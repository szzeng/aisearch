"""Cross-process ownership evidence for the managed AI worker."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from photo_search.runtime.machine_model_slot import MachineModelSlot
from photo_search.runtime.process_ownership import process_is_alive
from photo_search.workers import client as client_module
from photo_search.workers.client import AiWorkerClient, WorkerError, WorkerShutdownError
from tests.integration.worker_process_fixture import exit_worker, hold_worker

ROOT = Path(__file__).resolve().parents[2]


def _wait_for_file(path: Path, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and path.read_text(encoding="ascii").strip():
            return path.read_text(encoding="ascii").strip()
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {path.name}")


def _wait_dead(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_is_alive(pid):
            return True
        time.sleep(0.02)
    return False


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object acceptance")
def test_parent_forced_exit_kills_managed_child_and_releases_gpu_permit(tmp_path: Path) -> None:
    slot_name = "Global\\PhotoSearch.Test.ChildOwnership." + uuid.uuid4().hex
    pid_path = tmp_path / "child.pid"
    env = dict(os.environ)
    env["PHOTO_SEARCH_TEST_CHILD_MUTEX"] = slot_name
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), str(ROOT / "tests" / "integration"), env.get("PYTHONPATH", "")]
    )
    parent = subprocess.Popen(
        [sys.executable, "-u", "-m", "worker_process_fixture", str(pid_path), str(tmp_path)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    child_pid = 0
    try:
        child_pid = int(_wait_for_file(pid_path))
        line = parent.stdout.readline().strip() if parent.stdout is not None else ""
        assert line == "PERMIT_HELD", (line, parent.stderr.read() if parent.stderr else "")
        contender = MachineModelSlot(slot_name)
        assert contender.acquire(blocking=False) is False

        parent.kill()
        parent.wait(timeout=10)
        assert _wait_dead(child_pid), "AI worker survived its parent's forced exit"
        assert contender.acquire(timeout=5), "child death did not release the GPU permit"
        contender.release()
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if child_pid and process_is_alive(child_pid):
            subprocess.run(
                ["taskkill", "/PID", str(child_pid), "/T", "/F"],
                check=False,
                capture_output=True,
            )


def test_child_abnormal_exit_is_detected_without_waiting_for_full_timeout(
    tmp_path: Path,
) -> None:
    worker = AiWorkerClient(
        tmp_path / "model",
        tmp_path / "manifest.json",
        device="cpu",
        worker_target=exit_worker,
    )
    started = time.monotonic()
    with pytest.raises(WorkerError) as error:
        worker.health(timeout=30)
    assert time.monotonic() - started < 5
    assert error.value.payload["exception_type"] == "WorkerExited"
    assert error.value.payload["exitcode"] == 3
    worker.shutdown()
    assert worker._shutdown_complete is True
    assert not worker._stderr_file.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object acceptance")
def test_normal_close_waits_for_child_exit_and_releases_resources(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv(
        "PHOTO_SEARCH_TEST_CHILD_MUTEX",
        "Global\\PhotoSearch.Test.NormalClose." + uuid.uuid4().hex,
    )
    worker = AiWorkerClient(
        tmp_path / "model",
        tmp_path / "manifest.json",
        device="cpu",
        worker_target=hold_worker,
    )
    worker.start()
    child_pid = int(worker.pid or 0)
    assert child_pid and process_is_alive(child_pid)

    worker.shutdown(timeout=0.2)

    assert worker._shutdown_complete is True
    assert _wait_dead(child_pid)
    assert worker._job is None
    assert worker._closed_queues == {"requests", "responses"}
    assert not worker._stderr_file.exists()


def test_job_assignment_failure_cleans_child_job_process_and_queues(
    tmp_path: Path, monkeypatch,
) -> None:
    created: list[FailingJob] = []

    class FailingJob:
        def __init__(self) -> None:
            self.closed = False
            created.append(self)

        def assign(self, _pid: int) -> None:
            raise OSError("synthetic AssignProcessToJobObject failure")

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(client_module, "WindowsJobObject", FailingJob)
    worker = AiWorkerClient(
        tmp_path / "model",
        tmp_path / "manifest.json",
        device="cpu",
        worker_target=hold_worker,
    )

    with pytest.raises(OSError, match="AssignProcessToJobObject"):
        worker.start()

    assert worker._process_closed is True
    assert worker._job is None
    assert created and created[0].closed is True
    assert worker._closed_queues == {"requests", "responses"}
    assert not worker._stderr_file.exists()
    worker.shutdown()
    assert worker._shutdown_complete is True


def test_job_assignment_failure_retains_a_failed_job_close_for_shutdown(
    tmp_path: Path, monkeypatch,
) -> None:
    created: list[RetryingJob] = []

    class RetryingJob:
        def __init__(self) -> None:
            self.close_calls = 0
            created.append(self)

        def assign(self, _pid: int) -> None:
            raise OSError("synthetic AssignProcessToJobObject failure")

        def close(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                raise OSError("synthetic CloseHandle failure")

    monkeypatch.setattr(client_module, "WindowsJobObject", RetryingJob)
    worker = AiWorkerClient(
        tmp_path / "model",
        tmp_path / "manifest.json",
        device="cpu",
        worker_target=hold_worker,
    )

    with pytest.raises(WorkerShutdownError):
        worker.start()
    assert worker._job is created[0]
    assert created[0].close_calls == 1

    worker.shutdown()
    assert created[0].close_calls == 2
    assert worker._job is None
    assert worker._process_closed is True
    assert worker._shutdown_complete is True
