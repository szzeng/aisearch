from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from photo_search.artifacts.store import generation_id_for
from photo_search.catalog.db import Catalog
from photo_search.config import catalog_db_path
from photo_search.models.embedding import management
from photo_search.models.embedding.candidate_task import CandidateBuildManager
from photo_search.models.embedding.offline_transition import _store_request
from photo_search.storage_ui import _read_json, _state_path, queue_maintenance


class _Settings:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root


class _Context:
    def __init__(self, data_root: Path) -> None:
        self.settings = _Settings(data_root)
        self.data_root_lease_held = True
        self.model_resource_gate = _Gate()

    def release_query_encoder_for_candidate(self) -> None:
        return None


class _Gate:
    @contextmanager
    def exclusive(self, *, timeout: float):
        yield


def test_candidate_manager_joins_terminal_worker_before_relaunch(tmp_path: Path) -> None:
    manager = CandidateBuildManager(_Context(tmp_path))
    worker_release = threading.Event()
    worker = threading.Thread(target=worker_release.wait)
    worker.start()
    manager._thread = worker

    releaser = threading.Timer(0.05, worker_release.set)
    releaser.start()
    manager._reap_finished_worker({"status": "PAUSED"})
    releaser.join()

    assert manager._thread is None
    assert not worker.is_alive()


def test_concurrent_offline_requests_do_not_overwrite_each_other(tmp_path: Path) -> None:
    ready = threading.Barrier(2)

    def schedule(request_id: str) -> str:
        ready.wait()
        try:
            _store_request(tmp_path, {"request_id": request_id, "expires_at_ms": 9_999_999_999_999})
            return "scheduled"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(schedule, ("switch", "isolate")))

    assert sorted(outcomes) == ["rejected", "scheduled"]
    persisted = _read_json(_state_path(tmp_path, "embedding-offline-transition.json"))
    assert persisted is not None
    assert persisted["request_id"] in {"switch", "isolate"}


def test_storage_and_model_requests_share_the_same_exclusive_slot(tmp_path: Path) -> None:
    ready = threading.Barrier(2)

    def schedule_model() -> str:
        ready.wait()
        try:
            _store_request(tmp_path, {"request_id": "model", "expires_at_ms": 9_999_999_999_999})
            return "model"
        except ValueError:
            return "rejected"

    def schedule_storage() -> str:
        ready.wait()
        try:
            queue_maintenance(tmp_path, ["vacuum"], confirmed=True)
            return "storage"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(schedule_model), pool.submit(schedule_storage)]
        outcomes = [future.result() for future in results]

    assert sorted(outcomes).count("rejected") == 1
    assert sorted(outcomes).count("model") + sorted(outcomes).count("storage") == 1
    assert bool(_read_json(_state_path(tmp_path, "embedding-offline-transition.json"))) != bool(
        _read_json(_state_path(tmp_path, "storage-maintenance-request.json"))
    )


def test_historical_failed_job_does_not_mark_current_generation_as_error(
    tmp_path: Path, monkeypatch
) -> None:
    entry = SimpleNamespace(
        provider_id="synthetic-provider",
        display_name="Synthetic provider",
        adapter="synthetic",
        installed=True,
        reason=None,
        identity=SimpleNamespace(space_id="synthetic-space", processor_id="synthetic-processor", dimension=8),
    )
    monkeypatch.setattr(management, "registry_for", lambda _root: SimpleNamespace(providers=lambda: [entry]))
    generation_id = generation_id_for(entry.identity.space_id, entry.identity.processor_id)
    database = Path(catalog_db_path(tmp_path))
    database.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3

    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE index_generations(generation_id TEXT,space_id TEXT,processor_id TEXT,state TEXT,dimension INTEGER);"
            "CREATE TABLE search_profiles(profile_id TEXT,image_generation_id TEXT,created_at INTEGER);"
            "CREATE TABLE index_entries(generation_id TEXT,state TEXT);"
            "CREATE TABLE index_outbox(generation_id TEXT,state TEXT);"
            "CREATE TABLE jobs(processor_id TEXT,state TEXT);"
        )
        connection.execute(
            "INSERT INTO index_generations VALUES(?,?,?,?,?)",
            (generation_id, entry.identity.space_id, entry.identity.processor_id, "READY", 8),
        )
        connection.execute(
            "INSERT INTO jobs VALUES(?, 'FAILED')", (entry.identity.processor_id,)
        )

    provider = management.embedding_management_status(tmp_path)["providers"][0]

    assert provider["index_state"] == "CHECK_REQUIRED"
    assert "failed_jobs" not in provider


def test_candidate_build_uses_a_bounded_batch_without_activating_profile(
    tmp_path: Path, monkeypatch
) -> None:
    with Catalog.open(data_root=tmp_path):
        pass
    identity = SimpleNamespace(space_id="candidate-space", processor_id="candidate-processor", dimension=8)
    entry = SimpleNamespace(
        provider_id="candidate-provider",
        display_name="Candidate provider",
        adapter="synthetic",
        installed=True,
        reason=None,
        identity=identity,
        model_dir=tmp_path / "model",
        manifest_path=tmp_path / "manifest.json",
        device_policy="cpu",
    )
    registry = SimpleNamespace(
        providers=lambda: [entry],
        require_installed=lambda provider_id: entry if provider_id == entry.provider_id else None,
    )
    fake_registry = lambda _root: registry
    monkeypatch.setattr("photo_search.models.embedding.registry.registry_for", fake_registry)
    monkeypatch.setattr("photo_search.models.embedding.candidate_task.registry_for", fake_registry)
    calls: dict[str, object] = {}

    class _Encoder:
        def spec(self):
            return SimpleNamespace(envelope={"space_id": identity.space_id})

        def health(self):
            return {"status": "ok"}

        def close(self):
            return None

    monkeypatch.setattr(
        "photo_search.workers.query_encoder.build_worker_query_encoder",
        lambda **_kwargs: _Encoder(),
    )
    monkeypatch.setattr("photo_search.models.embedding.cli._record_candidate_runtime", lambda *_args: None)
    monkeypatch.setattr("photo_search.indexing.semantic.adapter_runtime_facts", lambda _encoder: {})
    monkeypatch.setattr("photo_search.indexing.semantic.semantic_spec", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        "photo_search.indexing.semantic.ensure_search_profile",
        lambda *_args, **kwargs: calls.setdefault("activate_if_unset", kwargs["activate_if_unset"]) or {"profile_id": "candidate"},
    )

    def build_index(*_args, **kwargs):
        calls.update(kwargs)
        return {
            "generation": {"entries": {"CONFIRMED": 0}, "jobs": {}},
            "targets": 0,
            "checked": 0,
            "coverage_complete": False,
            "run": {"jobs": {}},
        }

    monkeypatch.setattr("photo_search.indexing.semantic.build_index", build_index)
    manager = CandidateBuildManager(_Context(tmp_path))
    started = manager.start(entry.provider_id, "request-1", exclusive_confirmed=True)
    worker = manager._thread
    assert worker is not None
    worker.join(timeout=5)

    assert manager.status()["status"] == "COMPLETED"
    assert calls["limit"] == 100
    assert calls["batch_size"] == 100
    assert calls["retry_failed"] is False
    assert calls["activate_if_unset"] is False
    assert started["status"] == "RUNNING"
