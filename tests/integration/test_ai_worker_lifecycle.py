from __future__ import annotations

import os
from pathlib import Path

import pytest

from photo_search.workers.client import AiWorkerClient, WorkerError


@pytest.fixture()
def model_dir() -> Path:
    return (
        Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        / "PhotoSearchData" / "models" / "chinese_clip" / "vit-b-16"
    )


def test_spawn_worker_reports_spec_and_error_then_exits(model_dir: Path) -> None:
    if not model_dir.is_dir():
        pytest.skip("P0-03 model artifacts are not available")
    root = Path(__file__).resolve().parents[2]
    manifest = root / "tools" / "prepare_models" / "manifests" / "chinese-clip-vit-b-16.json"
    with AiWorkerClient(model_dir, manifest) as worker:
        health = worker.health(timeout=120)
        assert health["loaded"] is True
        assert health["spec"]["dimension"] == 512
        with pytest.raises(WorkerError) as captured:
            worker._call({"action": "not-a-real-action"}, timeout=30)
        assert captured.value.payload["stage"] == "not-a-real-action"
        assert captured.value.payload["exception_type"] == "ProtocolError"
        pid = worker.pid
    assert pid is not None
    assert not worker._process.is_alive()

def test_timeout_terminates_spawned_worker(model_dir: Path) -> None:
    if not model_dir.is_dir():
        pytest.skip("P0-03 model artifacts are not available")
    root = Path(__file__).resolve().parents[2]
    manifest = root / "tools" / "prepare_models" / "manifests" / "chinese-clip-vit-b-16.json"
    worker = AiWorkerClient(model_dir, manifest)
    with pytest.raises(TimeoutError):
        worker.health(timeout=0.001)
    assert not worker._process.is_alive()
    worker.shutdown()


def test_startup_error_contains_stderr_tail(model_dir: Path, tmp_path: Path) -> None:
    bad_manifest = tmp_path / "missing-manifest.json"
    worker = AiWorkerClient(model_dir, bad_manifest)
    with pytest.raises(WorkerError) as captured:
        worker.health(timeout=30)
    assert captured.value.payload["stage"] == "startup"
    assert "FileNotFoundError" in captured.value.stderr_tail
    worker.shutdown()
