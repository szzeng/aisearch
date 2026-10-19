from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from photo_search.artifacts.store import generation_id_for
from photo_search.catalog.db import Catalog
from photo_search.config import vectors_dir
from photo_search.indexing.store import _table_name
from photo_search.models.embedding import candidate_task, offline_transition
from photo_search.models.embedding.registry import registry_for
from photo_search.storage_ui import _atomic_json, _data_identity, _read_json, _state_path


def _recovery_fixture(
    tmp_path: Path, *, add_generation_reference: bool = True
) -> tuple[dict, Path, Path, Path, Path]:
    root = tmp_path.resolve()
    with Catalog.open(data_root=root) as catalog:
        entry = registry_for(root).providers()[0]
        generation_id = generation_id_for(entry.identity.space_id, entry.identity.processor_id)
        now = catalog.now_ms()
    table_name = _table_name(generation_id)
    vector_root = Path(vectors_dir(root))
    source = vector_root / f"{table_name}.lance"
    source.mkdir(parents=True)
    (source / "data.bin").write_bytes(b"synthetic-lance-payload")
    request_id = "fixture-orphan-recovery"
    operation = hashlib.sha256(request_id.encode()).hexdigest()[:20]
    base = vector_root / "_isolated" / operation
    backup_root, isolated_root = base / "backup", base / "isolated"
    backup_root.mkdir(parents=True)
    isolated_root.mkdir(parents=True)
    backup = backup_root / source.name
    shutil.copytree(source, backup)
    facts = offline_transition._physical_facts(source, vector_root)
    request = {
        "request_id": request_id,
        "provider_id": entry.provider_id,
        "generation_id": generation_id,
        "table_name": table_name,
        "physical_identity": facts,
        "identity": _data_identity(root),
        "data_root": str(root),
        "kind": "isolate_orphan",
        "status": "consuming",
    }
    journal = {
        "request_id": request_id, "kind": "isolate_orphan", "data_root": str(root),
        "identity": request["identity"], "provider_id": entry.provider_id,
        "physical_identity": facts, "table_name": table_name, "generation_id": generation_id,
        "status": "running", "stage": "BACKUP_VERIFIED",
        "backup_sha256": offline_transition._hash_tree(backup),
    }
    _atomic_json(
        _state_path(root, offline_transition.ORPHAN_STATE_FILE),
        journal,
    )
    if add_generation_reference:
        with Catalog.open(data_root=root) as catalog:
            with catalog.write() as connection:
                connection.execute(
                    "INSERT INTO index_generations "
                    "(generation_id,space_id,processor_id,dimension,state,is_active,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (generation_id, entry.identity.space_id, entry.identity.processor_id, 8, "READY", 0, now, now),
                )
    return request, source, backup, isolated_root / source.name, vector_root


def test_orphan_recovery_refuses_when_generation_became_referenced(
    tmp_path: Path, monkeypatch
) -> None:
    request, source, _backup, _isolated, _vectors = _recovery_fixture(tmp_path)
    monkeypatch.setattr(offline_transition, "_verify_lance_copy", lambda **_kwargs: None)

    result = offline_transition._recover_or_isolate_orphan(tmp_path, request)

    assert result["status"] == "error"
    assert source.exists()


def test_orphan_recovery_refuses_replaced_source_even_when_bytes_match(
    tmp_path: Path, monkeypatch
) -> None:
    request, source, _backup, _isolated, _vectors = _recovery_fixture(
        tmp_path, add_generation_reference=False
    )
    replacement = source.with_name("replacement.lance")
    shutil.copytree(source, replacement)
    shutil.rmtree(source)
    os.replace(replacement, source)
    monkeypatch.setattr(offline_transition, "_verify_lance_copy", lambda **_kwargs: None)

    result = offline_transition._recover_or_isolate_orphan(tmp_path, request)

    assert result["status"] == "error"
    assert source.exists()
    assert not _isolated.exists()


def test_orphan_recovery_refuses_corrupt_backup_without_moving_source(
    tmp_path: Path, monkeypatch
) -> None:
    request, source, backup, isolated, _vectors = _recovery_fixture(
        tmp_path, add_generation_reference=False
    )
    (backup / "data.bin").write_bytes(b"corrupt")
    monkeypatch.setattr(offline_transition, "_verify_lance_copy", lambda **_kwargs: None)

    result = offline_transition._recover_or_isolate_orphan(tmp_path, request)

    assert result["status"] == "error"
    assert source.exists()
    assert not isolated.exists()


def test_orphan_recovery_confirms_a_completed_same_filesystem_move(
    tmp_path: Path, monkeypatch
) -> None:
    request, source, _backup, isolated, _vectors = _recovery_fixture(
        tmp_path, add_generation_reference=False
    )
    journal_path = _state_path(tmp_path, offline_transition.ORPHAN_STATE_FILE)
    persisted = _read_json(journal_path)
    assert persisted is not None
    persisted["stage"] = "MOVE_STARTED"
    _atomic_json(journal_path, persisted)
    os.replace(source, isolated)
    monkeypatch.setattr(offline_transition, "_verify_lance_copy", lambda **_kwargs: None)

    result = offline_transition._recover_or_isolate_orphan(tmp_path, request)

    assert result["status"] == "ok"
    assert not source.exists()
    assert isolated.exists()


def test_orphan_recovery_refuses_dangling_source_reparse_point(
    tmp_path: Path, monkeypatch
) -> None:
    request, source, _backup, isolated, _vectors = _recovery_fixture(
        tmp_path, add_generation_reference=False
    )
    journal_path = _state_path(tmp_path, offline_transition.ORPHAN_STATE_FILE)
    persisted = _read_json(journal_path)
    assert persisted is not None
    persisted["stage"] = "MOVE_STARTED"
    _atomic_json(journal_path, persisted)
    os.replace(source, isolated)
    try:
        source.symlink_to(tmp_path / "missing-lance-target", target_is_directory=True)
    except OSError as error:
        pytest.skip(f"creating a synthetic symlink is unavailable: {error}")
    monkeypatch.setattr(offline_transition, "_verify_lance_copy", lambda **_kwargs: None)

    result = offline_transition._recover_or_isolate_orphan(tmp_path, request)

    assert result["status"] == "error"
    assert source.is_symlink()
    assert isolated.exists()


def test_orphan_normal_isolation_uses_safe_preflight(tmp_path: Path, monkeypatch) -> None:
    request, source, _backup, _isolated, _vectors = _recovery_fixture(
        tmp_path, add_generation_reference=False
    )
    shutil.rmtree(
        source.parent / "_isolated" / hashlib.sha256(request["request_id"].encode()).hexdigest()[:20]
    )
    _state_path(tmp_path, offline_transition.ORPHAN_STATE_FILE).unlink()
    monkeypatch.setattr(offline_transition, "_verify_lance_copy", lambda **_kwargs: None)

    result = offline_transition._isolate_orphan(tmp_path, request)

    assert result["status"] == "ok"
    assert not source.exists()
    assert Path(result["operation"]["isolated_path"]).exists()


def test_deep_provider_check_runs_off_loop_with_a_thread_owned_connection(
    api_env, monkeypatch
) -> None:
    import asyncio

    from photo_search.artifacts.store import generation_id_for

    entry = SimpleNamespace(
        provider_id="synthetic-provider",
        installed=True,
        identity=SimpleNamespace(space_id="check-space", processor_id="check-processor", dimension=8),
    )
    registry = SimpleNamespace(require_installed=lambda provider_id: entry)
    monkeypatch.setattr("photo_search.models.embedding.registry.registry_for", lambda _root: registry)
    generation_id = generation_id_for(entry.identity.space_id, entry.identity.processor_id)
    now = api_env.service.catalog.now_ms()
    with api_env.service.catalog.write() as connection:
        connection.execute(
            "INSERT INTO index_generations "
            "(generation_id,space_id,processor_id,dimension,state,is_active,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (generation_id, entry.identity.space_id, entry.identity.processor_id, 8, "READY", 0, now, now),
        )

    started = threading.Event()
    worker_threads: list[int] = []
    report_calls: list[int] = []

    def delayed_report(connection, _store, **_kwargs):
        assert isinstance(connection, sqlite3.Connection)
        assert connection is not api_env.service.catalog.connection
        report_calls.append(1)
        worker_threads.append(threading.get_ident())
        started.set()
        time.sleep(0.3)
        return SimpleNamespace(to_dict=lambda: {"required": 0, "covered": 0}), {"state": "healthy"}

    monkeypatch.setattr("photo_search.indexing.selection.selection_report", delayed_report)
    monkeypatch.setattr("photo_search.indexing.selection.require_selection_ready", lambda *_args, **_kwargs: None)

    async def scenario() -> None:
        async with api_env.client() as client:
            csrf = await api_env.authenticate(client)
            headers = {"X-CSRF-Token": csrf, "Origin": "http://127.0.0.1"}
            url = "/api/v1/embedding-management/providers/synthetic-provider/check"
            checks = [
                asyncio.create_task(client.post(url, headers=headers)),
                asyncio.create_task(client.post(url, headers=headers)),
            ]
            await asyncio.to_thread(started.wait, 1.0)
            assert started.is_set()
            assert not all(task.done() for task in checks)
            health = await asyncio.wait_for(client.get("/api/v1/health"), timeout=0.15)
            assert health.status_code == 200
            responses = await asyncio.gather(*checks)
            assert all(response.status_code == 200 for response in responses)

    asyncio.run(scenario())

    assert report_calls == [1]
    assert worker_threads and worker_threads[0] != threading.main_thread().ident


def test_capabilities_reports_busy_and_recovers_after_candidate_gate(api_env, monkeypatch) -> None:
    from photo_search.api import routes

    context = api_env.context
    encoders: list[SimpleNamespace] = []

    def encoder_factory():
        encoder = SimpleNamespace(closed=False)
        encoder.close = lambda: setattr(encoder, "closed", True)
        encoders.append(encoder)
        return encoder

    context._encoder_factory = encoder_factory
    monkeypatch.setattr(routes, "search_projection_summary", lambda _path: {"active_profile_id": "profile"})
    monkeypatch.setattr(routes, "search_profile_summary", lambda _path: {})
    monkeypatch.setattr(routes, "active_embedding_model", lambda _context, _profile: None)
    gate = context.model_resource_gate
    context.query_encoder()

    with gate.exclusive(timeout=0):
        context.release_query_encoder_for_candidate()
        assert encoders[0].closed is True
        busy = routes._search_capabilities(context)
        assert busy is not None
        assert busy["query_encoder_available"] is False
        assert busy["query_encoder_state"] == "BUSY"

    available = routes._search_capabilities(context)
    assert available is not None
    assert available["query_encoder_available"] is True
    assert available["query_encoder_state"] == "AVAILABLE"
    assert len(encoders) == 2


def test_capabilities_distinguishes_missing_model_from_load_failure(api_env, monkeypatch) -> None:
    from photo_search.api import routes
    from photo_search.api.context import ApiContext

    monkeypatch.setattr(routes, "search_projection_summary", lambda _path: {"active_profile_id": "profile"})
    monkeypatch.setattr(routes, "search_profile_summary", lambda _path: {})
    monkeypatch.setattr(routes, "active_embedding_model", lambda _context, _profile: {"installed": False})
    missing = ApiContext(api_env.settings)
    report = routes._search_capabilities(missing)
    assert report is not None
    assert report["query_encoder_state"] == "MODEL_MISSING"

    class BrokenModelError(RuntimeError):
        code = "sensitive-path-like-value"

    def broken_factory():
        raise BrokenModelError("synthetic load failure")

    damaged = ApiContext(api_env.settings, encoder_factory=broken_factory)
    monkeypatch.setattr(routes, "active_embedding_model", lambda _context, _profile: {"installed": True})
    report = routes._search_capabilities(damaged)
    assert report is not None
    assert report["query_encoder_state"] == "DAMAGED"
    assert report["query_encoder_failure"] == "BrokenModelError"


def test_candidate_progress_poll_uses_persisted_batch_state_without_sql(api_env, monkeypatch) -> None:
    manager = candidate_task.CandidateBuildManager(api_env.context)
    state = {
        "task_id": "synthetic-task", "status": "RUNNING", "generation_id": "synthetic-generation",
        "catalog_identity": candidate_task._catalog_identity(api_env.data_root), "confirmed_entries": 12,
    }
    candidate_task._atomic_json(manager.path, state)
    monkeypatch.setattr(candidate_task.sqlite3, "connect", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("progress poll opened SQLite")))

    result = manager.status(refresh_counts=False)

    assert result is not None
    assert result["confirmed_entries"] == 12
    assert result["status"] == "RUNNING"
