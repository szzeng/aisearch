"""Durable, single-owner candidate-model build control for the local application."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import SELECTION_WRITER_ACTIVE, CatalogError
from photo_search.config import catalog_db_path, runtime_dir
from photo_search.models.embedding.registry import registry_for

TASK_FILENAME = "embedding-candidate-task.json"
BATCH_LIMIT = 100
TASK_STATES = {"RUNNING", "PAUSE_REQUESTED", "PAUSED", "INTERRUPTED", "COMPLETED", "FAILED"}


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_state(root: Path) -> dict[str, Any] | None:
    path = runtime_dir(root) / TASK_FILENAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) and value.get("status") in TASK_STATES else None


def _catalog_identity(root: Path) -> dict[str, Any]:
    path = Path(catalog_db_path(root)).resolve(strict=False)
    try:
        info = path.stat()
    except OSError:
        return {"path": str(path), "exists": False}
    return {
        "path": str(path),
        "exists": True,
        "device": int(info.st_dev),
        "file_id": int(info.st_ino),
        "created_ns": int(getattr(info, "st_birthtime_ns", info.st_ctime_ns)),
    }


class CandidateBuildManager:
    """One thread per app instance; JSON is control state, catalog rows are progress authority."""

    def __init__(self, context: Any) -> None:
        self.context = context
        self.root = Path(context.settings.data_root).resolve()
        self._lock = threading.RLock()
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._owner_id = uuid.uuid4().hex

    @property
    def path(self) -> Path:
        return runtime_dir(self.root) / TASK_FILENAME

    def _save(self, state: dict[str, Any]) -> dict[str, Any]:
        state["updated_at_ms"] = time.time_ns() // 1_000_000
        _atomic_json(self.path, state)
        return dict(state)

    def status(self) -> dict[str, Any] | None:
        state = _read_state(self.root)
        if state is None:
            return None
        if state.get("catalog_identity") != _catalog_identity(self.root):
            state["status"] = "FAILED"
            state["last_error"] = {"code": "CATALOG_IDENTITY_CHANGED"}
            return state
        path = Path(catalog_db_path(self.root))
        try:
            connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            rows = connection.execute(
                "SELECT state,COUNT(*) FROM index_entries WHERE generation_id=? GROUP BY state",
                (str(state.get("generation_id") or ""),),
            ).fetchall()
            connection.close()
            counts = {str(row[0]): int(row[1]) for row in rows}
            state["confirmed_entries"] = counts.get("CONFIRMED", 0)
            state["entry_states"] = counts
        except sqlite3.Error:
            state["progress_state"] = "CHECK_REQUIRED"
        return state

    def recover_after_lock(self) -> None:
        """A RUNNING record from a previous owner is reconciled only after the OS lease."""
        if not self.context.data_root_lease_held:
            raise RuntimeError("the data-root lease must be held before recovery")
        with self._lock:
            state = _read_state(self.root)
            if state is None or state.get("status") not in {"RUNNING", "PAUSE_REQUESTED"}:
                return
            if state.get("catalog_identity") != _catalog_identity(self.root):
                state["status"] = "FAILED"
                state["last_error"] = {"code": "CATALOG_IDENTITY_CHANGED"}
            else:
                state["status"] = "INTERRUPTED"
                state["last_error"] = {"code": "PROCESS_INTERRUPTED"}
            state["owner_id"] = None
            self._save(state)

    def start(self, provider_id: str, request_id: str, *, exclusive_confirmed: bool) -> dict[str, Any]:
        if not self.context.data_root_lease_held:
            raise CatalogError(SELECTION_WRITER_ACTIVE, "candidate indexing requires the running service lease")
        if not exclusive_confirmed:
            raise CatalogError("USAGE", "confirm temporary exclusive model-resource use before indexing")
        from photo_search.storage_ui import _read_json, _state_path

        maintenance = _read_json(_state_path(self.root, "storage-maintenance-request.json"))
        if maintenance and int(maintenance.get("expires_at_ms", 0)) > time.time_ns() // 1_000_000:
            raise CatalogError("SELECTION_WRITER_ACTIVE", "pending offline storage maintenance blocks candidate indexing")
        provider_id = str(provider_id)
        request_id = str(request_id).strip()
        if not request_id or len(request_id) > 128:
            raise CatalogError("USAGE", "request_id must contain 1 to 128 characters")
        entry = registry_for(self.root).require_installed(provider_id)
        from photo_search.artifacts.store import generation_id_for
        from photo_search.models.embedding.management import embedding_management_status

        generation_id = generation_id_for(entry.identity.space_id, entry.identity.processor_id)
        provider_status = next(
            (item for item in embedding_management_status(self.root)["providers"]
             if item["provider_id"] == provider_id),
            None,
        )
        if provider_status and provider_status["index_state"] == "ORPHAN_TABLE":
            raise CatalogError(
                "PROFILE_GENERATION_MISMATCH",
                "an orphan vector table must be safely isolated before candidate indexing",
                details={"reason": "ORPHAN_TABLE", "generation_id": generation_id},
            )
        identity = _catalog_identity(self.root)
        with self._lock:
            previous = _read_state(self.root)
            if previous and previous.get("catalog_identity") == identity:
                if previous.get("request_id") == request_id and previous.get("provider_id") == provider_id:
                    return previous
                if previous.get("status") in {"RUNNING", "PAUSE_REQUESTED", "PAUSED", "INTERRUPTED"}:
                    raise CatalogError(SELECTION_WRITER_ACTIVE, "a candidate build already exists for this data root")
            self._reap_finished_worker(previous)
            state = {
                "task_id": uuid.uuid4().hex,
                "request_id": request_id,
                "catalog_identity": identity,
                "provider_id": provider_id,
                "processor_id": str(entry.identity.processor_id),
                "space_id": str(entry.identity.space_id),
                "generation_id": str(generation_id),
                "status": "RUNNING",
                "owner_id": self._owner_id,
                "batch_limit": BATCH_LIMIT,
                "confirmed_entries": 0,
                "last_error": None,
                "started_at_ms": time.time_ns() // 1_000_000,
            }
            self._save(state)
            try:
                self._launch(state, retry_failed=False)
            except Exception as error:
                state.update({"status": "FAILED", "owner_id": None,
                              "last_error": {"code": "WORKER_START_FAILED", "message": str(error)[:400]}})
                self._save(state)
                raise
            return state

    def pause(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            state = self.status()
            if not state or state.get("task_id") != str(task_id) or state.get("status") != "RUNNING":
                raise CatalogError("USAGE", "there is no running candidate build to pause")
            self._pause.set()
            state["status"] = "PAUSE_REQUESTED"
            return self._save(state)

    def resume(self, task_id: str) -> dict[str, Any]:
        if not self.context.data_root_lease_held:
            raise CatalogError(SELECTION_WRITER_ACTIVE, "candidate indexing requires the running service lease")
        with self._lock:
            state = self.status()
            if not state or state.get("task_id") != str(task_id):
                raise CatalogError("USAGE", "the candidate task identity is no longer available")
            if state.get("status") not in {"PAUSED", "INTERRUPTED", "FAILED"}:
                raise CatalogError("USAGE", "the candidate task is not waiting for continuation")
            if state.get("catalog_identity") != _catalog_identity(self.root):
                raise CatalogError("CATALOG_UNREADABLE", "the catalog was replaced; recheck the candidate task")
            self._reap_finished_worker(state)
            self._pause.clear()
            self._stop.clear()
            retry = bool((state.get("last_error") or {}).get("failed_jobs"))
            state["status"] = "RUNNING"
            state["owner_id"] = self._owner_id
            state["last_error"] = None
            self._save(state)
            try:
                self._launch(state, retry_failed=retry)
            except Exception as error:
                state.update({"status": "FAILED", "owner_id": None,
                              "last_error": {"code": "WORKER_START_FAILED", "message": str(error)[:400]}})
                self._save(state)
                raise
            return state

    def _reap_finished_worker(self, state: dict[str, Any] | None) -> None:
        thread = self._thread
        if thread is None:
            return
        if thread.is_alive():
            if not state or state.get("status") not in {"PAUSED", "INTERRUPTED", "COMPLETED", "FAILED"}:
                raise CatalogError(SELECTION_WRITER_ACTIVE, "a candidate worker is still active")
            # Terminal state is saved as the worker's final action. Join the small window
            # between that write and Python thread exit before allowing a new RUNNING record.
            thread.join()
        if not thread.is_alive():
            self._thread = None

    def _launch(self, state: dict[str, Any], *, retry_failed: bool) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise CatalogError(SELECTION_WRITER_ACTIVE, "a candidate worker is already active")
        thread = threading.Thread(
            target=self._run,
            args=(dict(state),),
            kwargs={"retry_failed": bool(retry_failed)},
            name="embedding-candidate-build",
            daemon=True,
        )
        self._thread = thread
        try:
            thread.start()
        except Exception:
            self._thread = None
            raise

    def _run(self, state: dict[str, Any], *, retry_failed: bool) -> None:
        from photo_search.face.service import BACKGROUND_MODEL_SLOT
        from photo_search.indexing import semantic
        from photo_search.models.embedding.cli import _record_candidate_runtime
        from photo_search.models.embedding.registry import registry_for
        from photo_search.workers.query_encoder import build_worker_query_encoder

        encoder = None
        try:
            if not BACKGROUND_MODEL_SLOT.acquire(timeout=5):
                raise TimeoutError("background model work is active; continue after it reaches a safe boundary")
            try:
                with self.context.model_resource_gate.exclusive(timeout=15):
                    self.context.release_query_encoder_for_candidate()
                    entry = registry_for(self.root).require_installed(str(state["provider_id"]))
                    encoder = build_worker_query_encoder(
                        model_dir=entry.model_dir,
                        manifest_path=entry.manifest_path,
                        device_policy=entry.device_policy,
                    )
                    with Catalog.open(data_root=self.root) as catalog:
                        if str(encoder.spec().envelope["space_id"]) != str(state["space_id"]):
                            raise CatalogError("SPACE_MISMATCH", "the loaded provider no longer matches the candidate task")
                        _record_candidate_runtime(
                            catalog, entry, encoder.health()
                        )
                        first = True
                        while True:
                            if self._stop.is_set():
                                state["status"] = "INTERRUPTED"
                                state["last_error"] = {"code": "PROCESS_STOPPING"}
                                break
                            payload = semantic.build_index(
                                catalog,
                                encoder=encoder,
                                manifest_path=entry.manifest_path,
                                expected_space_id=str(state["space_id"]),
                                expected_processor_id=str(state["processor_id"]),
                                limit=BATCH_LIMIT,
                                batch_size=BATCH_LIMIT,
                                device="auto",
                                device_policy=str(entry.device_policy),
                                worker_id=f"embedding-candidate-{state['task_id']}",
                                retry_failed=retry_failed and first,
                            )
                            first = False
                            profile = semantic.ensure_search_profile(
                                catalog,
                                semantic.semantic_spec(
                                    entry.manifest_path,
                                    semantic.adapter_runtime_facts(encoder),
                                    device_policy=str(entry.device_policy),
                                ),
                                space_id=str(state["space_id"]),
                                generation_id=str(state["generation_id"]),
                                activate_if_unset=False,
                            )
                            summary = payload["generation"]
                            state["profile_id"] = profile.get("profile_id")
                            state["confirmed_entries"] = int(
                                (summary.get("entries") or {}).get("CONFIRMED", 0)
                            )
                            state["last_batch"] = {
                                "targets": int(payload.get("targets", 0)),
                                "checked": int(payload.get("checked", 0)),
                                "coverage_complete": bool(payload.get("coverage_complete")),
                                "out_of_scope": payload.get("out_of_scope", {}),
                            }
                            self._save(state)
                            if self._pause.is_set():
                                state["status"] = "PAUSED"
                                state["owner_id"] = None
                                break
                            jobs = summary.get("jobs") or payload.get("run", {}).get("jobs") or {}
                            failed = int(jobs.get("FAILED", 0))
                            if failed:
                                state["status"] = "FAILED"
                                state["last_error"] = {"code": "FAILED_JOBS", "failed_jobs": failed}
                                break
                            if int(payload.get("targets", 0)) == 0 or bool(payload.get("coverage_complete")):
                                state["status"] = "COMPLETED"
                                state["completed_at_ms"] = time.time_ns() // 1_000_000
                                break
                    state["owner_id"] = None if state.get("status") != "RUNNING" else self._owner_id
            finally:
                if encoder is not None:
                    stop = getattr(encoder, "close", None)
                    if callable(stop):
                        stop()
                    encoder = None
                BACKGROUND_MODEL_SLOT.release()
            if state.get("status") == "RUNNING":
                state["status"] = "INTERRUPTED"
                state["last_error"] = {"code": "WORKER_STOPPED"}
        except TimeoutError as error:
            state["status"] = "PAUSED"
            state["last_error"] = {"code": "RESOURCE_BUSY", "message": str(error)}
            state["owner_id"] = None
        except Exception as error:  # noqa: BLE001 - persisted status is the operator's recovery record.
            state["status"] = "FAILED"
            state["last_error"] = {"code": type(error).__name__, "message": str(error)[:500]}
            state["owner_id"] = None
        finally:
            try:
                self._save(state)
            except OSError:
                pass

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join()
        state = _read_state(self.root)
        if state and state.get("owner_id") == self._owner_id and state.get("status") in {"RUNNING", "PAUSE_REQUESTED"}:
            state["status"] = "INTERRUPTED"
            state["owner_id"] = None
            state["last_error"] = {"code": "PROCESS_STOPPING"}
            self._save(state)


__all__ = ["BATCH_LIMIT", "CandidateBuildManager", "TASK_FILENAME", "TASK_STATES"]
