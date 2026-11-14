"""Helpers for real parent/child ownership tests."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

from photo_search.runtime.machine_model_slot import MachineModelSlot
from photo_search.workers.client import AiWorkerClient


def hold_worker(
    _requests: Any,
    _responses: Any,
    _model_dir: str,
    _manifest_path: str,
    _stderr_path: str,
    _device: str | None,
    _device_policy: str,
    start_gate: Any,
    _parent_pid: int,
) -> None:
    if not start_gate.wait(30):
        return
    slot = MachineModelSlot(os.environ["PHOTO_SEARCH_TEST_CHILD_MUTEX"])
    slot.acquire()
    print("PERMIT_HELD", flush=True)
    while True:
        time.sleep(1)


def exit_worker(
    _requests: Any,
    _responses: Any,
    _model_dir: str,
    _manifest_path: str,
    _stderr_path: str,
    _device: str | None,
    _device_policy: str,
    start_gate: Any,
    _parent_pid: int,
) -> None:
    if start_gate.wait(30):
        os._exit(3)


def run_parent(pid_path: str, root: str) -> None:
    directory = Path(root)
    client = AiWorkerClient(
        directory / "model",
        directory / "manifest.json",
        device="cpu",
        worker_target=hold_worker,
    )
    client.start()
    Path(pid_path).write_text(str(client.pid), encoding="ascii")
    while True:
        time.sleep(1)


if __name__ == "__main__":
    run_parent(sys.argv[1], sys.argv[2])
