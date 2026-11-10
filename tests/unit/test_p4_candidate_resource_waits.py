from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

from photo_search.models.embedding import candidate_task
from photo_search.models.embedding.candidate_task import CandidateBuildManager
from photo_search.runtime.model_resource_gate import ModelResourceGate


def _manager(root: Path, gate: ModelResourceGate) -> CandidateBuildManager:
    context = SimpleNamespace(
        settings=SimpleNamespace(data_root=root),
        model_resource_gate=gate,
        release_query_encoder_for_candidate=lambda: None,
    )
    return CandidateBuildManager(context)


def _state(root: Path) -> dict:
    return {
        "task_id": "synthetic-task",
        "catalog_identity": candidate_task._catalog_identity(root),
        "provider_id": "synthetic-provider",
        "space_id": "synthetic-space",
        "processor_id": "synthetic-processor",
        "generation_id": "synthetic-generation",
        "status": "RUNNING",
        "owner_id": "synthetic-owner",
    }


def test_candidate_waits_longer_for_face_slot_and_reports_its_source(
    tmp_path: Path, monkeypatch
) -> None:
    slot = threading.Lock()
    slot.acquire()
    monkeypatch.setattr("photo_search.face.service.BACKGROUND_MODEL_SLOT", slot)
    monkeypatch.setattr(candidate_task, "FACE_SLOT_WAIT_SECONDS", 0.08)
    monkeypatch.setattr(candidate_task, "MODEL_WAIT_POLL_SECONDS", 0.01)
    manager = _manager(tmp_path, ModelResourceGate())
    try:
        manager._run(_state(tmp_path), retry_failed=False)
        state = manager.status(refresh_counts=False)
        assert state is not None
        assert state["status"] == "PAUSED"
        assert state["last_error"]["code"] == "RESOURCE_BUSY"
        assert state["last_error"]["reason"] == "FACE_TASK_ACTIVE"
        assert "face inference" in state["last_error"]["message"]
        assert slot.locked()
    finally:
        slot.release()


def test_pause_during_face_slot_wait_is_prompt_and_recoverable(tmp_path: Path, monkeypatch) -> None:
    slot = threading.Lock()
    slot.acquire()
    monkeypatch.setattr("photo_search.face.service.BACKGROUND_MODEL_SLOT", slot)
    monkeypatch.setattr(candidate_task, "FACE_SLOT_WAIT_SECONDS", 2.0)
    monkeypatch.setattr(candidate_task, "MODEL_WAIT_POLL_SECONDS", 0.01)
    manager = _manager(tmp_path, ModelResourceGate())
    worker = threading.Thread(target=manager._run, args=(_state(tmp_path),), kwargs={"retry_failed": False})
    try:
        worker.start()
        time.sleep(0.03)
        manager._pause.set()
        worker.join(timeout=1)
        assert not worker.is_alive()
        state = manager.status(refresh_counts=False)
        assert state is not None
        assert state["status"] == "PAUSED"
        assert state["last_error"] == {"code": "PAUSE_REQUESTED"}
        assert slot.locked()
    finally:
        slot.release()
        if worker.is_alive():
            worker.join(timeout=1)


def test_frontend_semantic_query_timeout_is_distinguished(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(candidate_task, "SEMANTIC_QUERY_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(candidate_task, "MODEL_WAIT_POLL_SECONDS", 0.01)
    gate = ModelResourceGate()
    manager = _manager(tmp_path, gate)
    with gate.query():
        manager._run(_state(tmp_path), retry_failed=False)
        state = manager.status(refresh_counts=False)
        assert state is not None
        assert state["status"] == "PAUSED"
        assert state["last_error"]["code"] == "RESOURCE_BUSY"
        assert state["last_error"]["reason"] == "SEMANTIC_QUERY_ACTIVE"
        assert "foreground semantic queries" in state["last_error"]["message"]
        assert gate.reserved is False


def test_pause_during_semantic_query_wait_releases_gate_reservation(tmp_path: Path) -> None:
    gate = ModelResourceGate()
    manager = _manager(tmp_path, gate)
    query_entered = threading.Event()
    release_query = threading.Event()

    def hold_query() -> None:
        with gate.query():
            query_entered.set()
            release_query.wait(timeout=2)

    query_thread = threading.Thread(target=hold_query)
    worker = threading.Thread(target=manager._run, args=(_state(tmp_path),), kwargs={"retry_failed": False})
    query_thread.start()
    assert query_entered.wait(timeout=1)
    worker.start()
    try:
        deadline = time.monotonic() + 1
        while not gate.reserved and time.monotonic() < deadline:
            time.sleep(0.005)
        assert gate.reserved
        manager._pause.set()
        worker.join(timeout=1)
        assert not worker.is_alive()
        state = manager.status(refresh_counts=False)
        assert state is not None
        assert state["status"] == "PAUSED"
        assert state["last_error"] == {"code": "PAUSE_REQUESTED"}
        assert gate.reserved is False
    finally:
        release_query.set()
        query_thread.join(timeout=1)
        worker.join(timeout=1)


def test_stop_during_semantic_query_wait_is_interrupted_and_releases_gate(tmp_path: Path) -> None:
    gate = ModelResourceGate()
    manager = _manager(tmp_path, gate)
    query_entered = threading.Event()
    release_query = threading.Event()

    def hold_query() -> None:
        with gate.query():
            query_entered.set()
            release_query.wait(timeout=2)

    query_thread = threading.Thread(target=hold_query)
    worker = threading.Thread(target=manager._run, args=(_state(tmp_path),), kwargs={"retry_failed": False})
    query_thread.start()
    assert query_entered.wait(timeout=1)
    worker.start()
    try:
        deadline = time.monotonic() + 1
        while not gate.reserved and time.monotonic() < deadline:
            time.sleep(0.005)
        assert gate.reserved
        manager._stop.set()
        worker.join(timeout=1)
        assert not worker.is_alive()
        state = manager.status(refresh_counts=False)
        assert state is not None
        assert state["status"] == "INTERRUPTED"
        assert state["last_error"] == {"code": "PROCESS_STOPPING"}
        assert gate.reserved is False
    finally:
        release_query.set()
        query_thread.join(timeout=1)
        worker.join(timeout=1)


def test_model_load_failure_releases_both_exclusive_resources(tmp_path: Path, monkeypatch) -> None:
    from photo_search.models.embedding import registry as registry_module

    gate = ModelResourceGate()
    slot = threading.Lock()
    monkeypatch.setattr("photo_search.face.service.BACKGROUND_MODEL_SLOT", slot)
    monkeypatch.setattr(candidate_task, "registry_for", lambda _root: SimpleNamespace(
        require_installed=lambda _provider_id: SimpleNamespace(
            model_dir=tmp_path / "model",
            manifest_path=tmp_path / "manifest.json",
            device_policy="cpu",
        )
    ))
    monkeypatch.setattr(registry_module, "registry_for", candidate_task.registry_for)

    def fail_to_load(**_kwargs):
        raise RuntimeError("synthetic model load failure")

    monkeypatch.setattr(
        "photo_search.workers.query_encoder.build_worker_query_encoder", fail_to_load
    )
    manager = _manager(tmp_path, gate)
    manager._run(_state(tmp_path), retry_failed=False)

    state = manager.status(refresh_counts=False)
    assert state is not None
    assert state["status"] == "FAILED"
    assert state["last_error"]["code"] == "RuntimeError"
    assert gate.reserved is False
    assert slot.acquire(blocking=False)
    slot.release()


def test_semantic_waiter_proceeds_after_active_query_finishes() -> None:
    gate = ModelResourceGate()
    acquired = threading.Event()
    with gate.query():
        worker = threading.Thread(
            target=lambda: _acquire_exclusive(gate, acquired), name="candidate-waiter"
        )
        worker.start()
        deadline = time.monotonic() + 1
        while not gate.reserved and time.monotonic() < deadline:
            time.sleep(0.005)
        assert gate.reserved
        assert not acquired.is_set()
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert acquired.is_set()
    assert gate.reserved is False


def _acquire_exclusive(gate: ModelResourceGate, acquired: threading.Event) -> None:
    with gate.exclusive(timeout=0.5):
        acquired.set()
