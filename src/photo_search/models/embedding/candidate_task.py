"""Durable, single-owner candidate-model build control for the local application."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import ExitStack, closing
from pathlib import Path
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import SELECTION_WRITER_ACTIVE, CatalogError
from photo_search.config import catalog_db_path, runtime_dir
from photo_search.models.embedding.registry import registry_for
from photo_search.runtime.resource_lifecycle import close_owned_resource

logger = logging.getLogger("photo_search.models.embedding.candidate_task")

TASK_FILENAME = "embedding-candidate-task.json"
BATCH_LIMIT = 100
FACE_SLOT_WAIT_SECONDS = 60.0
SEMANTIC_QUERY_WAIT_SECONDS = 15.0
MODEL_WAIT_POLL_SECONDS = 0.1
TASK_STATES = {"RUNNING", "PAUSE_REQUESTED", "PAUSED", "INTERRUPTED", "COMPLETED", "FAILED"}


class _CandidateWaitCancelled(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _FaceSlotWaitTimeout(TimeoutError):
    pass


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
        self._unreleased_encoder: Any | None = None

    @property
    def path(self) -> Path:
        return runtime_dir(self.root) / TASK_FILENAME

    def _save(self, state: dict[str, Any]) -> dict[str, Any]:
        state["updated_at_ms"] = time.time_ns() // 1_000_000
        _atomic_json(self.path, state)
        return dict(state)

    def status(self, *, refresh_counts: bool = True) -> dict[str, Any] | None:
        state = _read_state(self.root)
        if state is None:
            return None
        if state.get("catalog_identity") != _catalog_identity(self.root):
            state["status"] = "FAILED"
            state["last_error"] = {"code": "CATALOG_IDENTITY_CHANGED"}
            return state
        if not refresh_counts:
            return state
        path = Path(catalog_db_path(self.root))
        try:
            with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT state,COUNT(*) FROM index_entries WHERE generation_id=? GROUP BY state",
                    (str(state.get("generation_id") or ""),),
                ).fetchall()
                if state["status"] not in {"RUNNING", "PAUSE_REQUESTED"} and (
                    state.get("space_id") and state.get("processor_id")
                ):
                    from photo_search.indexing.semantic import candidate_job_progress

                    state["job_progress"] = candidate_job_progress(
                        connection,
                        space_id=str(state["space_id"]), processor_id=str(state["processor_id"]),
                    )
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
                self._launch(state, retry_failed=True)
            except Exception as error:
                state.update({"status": "FAILED", "owner_id": None,
                              "last_error": {"code": "WORKER_START_FAILED", "message": str(error)[:400]}})
                self._save(state)
                raise
            return state

    def pause(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            state = self.status(refresh_counts=False)
            if not state or state.get("task_id") != str(task_id) or state.get("status") != "RUNNING":
                raise CatalogError("USAGE", "there is no running candidate build to pause")
            self._pause.set()
            state["status"] = "PAUSE_REQUESTED"
            return self._save(state)

    def resume(self, task_id: str) -> dict[str, Any]:
        if not self.context.data_root_lease_held:
            raise CatalogError(SELECTION_WRITER_ACTIVE, "candidate indexing requires the running service lease")
        with self._lock:
            state = self.status(refresh_counts=False)
            if not state or state.get("task_id") != str(task_id):
                raise CatalogError("USAGE", "the candidate task identity is no longer available")
            if state.get("status") not in {"PAUSED", "INTERRUPTED", "FAILED"}:
                raise CatalogError("USAGE", "the candidate task is not waiting for continuation")
            if state.get("catalog_identity") != _catalog_identity(self.root):
                raise CatalogError("CATALOG_UNREADABLE", "the catalog was replaced; recheck the candidate task")
            self._reap_finished_worker(state)
            self._pause.clear()
            self._stop.clear()
            state["status"] = "RUNNING"
            state["owner_id"] = self._owner_id
            state["last_error"] = None
            self._save(state)
            try:
                self._launch(state, retry_failed=True)
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
        if self._unreleased_encoder is not None:
            self._release_unreleased_encoder()
            if self._unreleased_encoder is not None:
                raise CatalogError(
                    SELECTION_WRITER_ACTIVE,
                    "the previous candidate model worker is still being released",
                )
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
        from photo_search.runtime.model_resource_gate import (
            ModelResourceWaitCancelled,
            ModelResourceWaitTimeout,
        )
        from photo_search.workers.query_encoder import build_worker_query_encoder

        encoder = None
        background_slot_acquired = False

        def close_candidate_encoder() -> None:
            nonlocal encoder
            if encoder is None:
                return
            try:
                stop = getattr(encoder, "close", None)
                if callable(stop):
                    stop()
            except BaseException:
                self._unreleased_encoder = encoder
                raise
            else:
                self._unreleased_encoder = None
                encoder = None

        try:
            self._acquire_background_model_slot(BACKGROUND_MODEL_SLOT)
            background_slot_acquired = True
            with ExitStack() as resource_stack:
                resource_stack.enter_context(
                    self.context.model_resource_gate.exclusive(
                        timeout=SEMANTIC_QUERY_WAIT_SECONDS,
                        cancel_check=self._wait_cancel_reason,
                        poll_interval=MODEL_WAIT_POLL_SECONDS,
                    )
                )
                resource_stack.callback(close_candidate_encoder)
                cancelled = self._wait_cancel_reason()
                if cancelled:
                    raise _CandidateWaitCancelled(cancelled)
                self.context.release_query_encoder_for_candidate()
                entry = registry_for(self.root).require_installed(str(state["provider_id"]))
                encoder = build_worker_query_encoder(
                    model_dir=entry.model_dir,
                    manifest_path=entry.manifest_path,
                    device_policy=entry.device_policy,
                )
                catalog = Catalog.open(data_root=self.root)
                try:
                    if str(encoder.spec().envelope["space_id"]) != str(state["space_id"]):
                        raise CatalogError("SPACE_MISMATCH", "the loaded provider no longer matches the candidate task")
                    _record_candidate_runtime(catalog, entry, encoder.health())
                    cursor = 0
                    # The candidate owns this connection. Its own job/outbox writes leave
                    # data_version alone, while a scan on another connection advances it
                    # even when an unchanged file merely becomes PRESENT again.
                    scope_revision = catalog.connection.execute("PRAGMA data_version").fetchone()[0]
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
                            retry_failed=retry_failed,
                            after_asset_id=cursor,
                            scan_limit=BATCH_LIMIT,
                        )
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
                        if not payload["coverage_complete"]:
                            cursor = int(payload["after_asset_id"])
                            continue
                        with catalog.read_snapshot() as connection:
                            progress = semantic.candidate_job_progress(
                                connection, space_id=str(state["space_id"]),
                                processor_id=str(state["processor_id"]),
                            )
                            scope_changed = (
                                connection.execute("PRAGMA data_version").fetchone()[0] != scope_revision
                            )
                        state["job_progress"] = progress
                        if scope_changed:
                            state["status"] = "PAUSED"
                            state["last_error"] = {"code": "LIBRARY_SCOPE_CHANGED"}
                        elif progress["failed"]:
                            state["status"] = "FAILED"
                            state["last_error"] = {
                                "code": "FAILED_JOBS", "failed_jobs": progress["failed"],
                            }
                        elif progress["pending"]:
                            state["status"] = "PAUSED"
                            state["last_error"] = {"code": "SEMANTIC_WORK_PENDING"}
                        else:
                            state["status"] = "COMPLETED"
                            state["completed_at_ms"] = time.time_ns() // 1_000_000
                        break
                finally:
                    close_owned_resource(
                        catalog,
                        on_failure=lambda error: logger.warning(
                            "closing the candidate catalog failed; retaining it for retry: %s",
                            error,
                        ),
                    )
                state["owner_id"] = None if state.get("status") != "RUNNING" else self._owner_id
            if state.get("status") == "RUNNING":
                state["status"] = "INTERRUPTED"
                state["last_error"] = {"code": "WORKER_STOPPED"}
        except ModelResourceWaitCancelled as error:
            self._record_wait_cancellation(state, str(error))
        except _CandidateWaitCancelled as error:
            self._record_wait_cancellation(state, error.reason)
        except ModelResourceWaitTimeout as error:
            state["status"] = "PAUSED"
            state["last_error"] = {
                "code": "RESOURCE_BUSY",
                "reason": error.reason,
                "message": str(error),
            }
            state["owner_id"] = None
        except _FaceSlotWaitTimeout as error:
            state["status"] = "PAUSED"
            state["last_error"] = {
                "code": "RESOURCE_BUSY",
                "reason": "FACE_TASK_ACTIVE",
                "message": str(error),
            }
            state["owner_id"] = None
        except CatalogError as error:
            state["status"] = "PAUSED" if error.details.get("retryable") is True else "FAILED"
            state["last_error"] = {"code": str(error.code), "message": str(error.message)[:500]}
            state["owner_id"] = None
        except Exception as error:  # noqa: BLE001 - persisted status is the operator's recovery record.
            state["status"] = "FAILED"
            state["last_error"] = {"code": type(error).__name__, "message": str(error)[:500]}
            state["owner_id"] = None
        finally:
            if background_slot_acquired:
                BACKGROUND_MODEL_SLOT.release()
            try:
                self._save(state)
            except OSError:
                pass

    def _wait_cancel_reason(self) -> str | None:
        if self._stop.is_set():
            return "PROCESS_STOPPING"
        if self._pause.is_set():
            return "PAUSE_REQUESTED"
        return None

    def _acquire_background_model_slot(self, slot: Any) -> None:
        deadline = time.monotonic() + FACE_SLOT_WAIT_SECONDS
        while True:
            cancelled = self._wait_cancel_reason()
            if cancelled:
                raise _CandidateWaitCancelled(cancelled)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _FaceSlotWaitTimeout(
                    "face inference is still using the shared background model slot "
                    f"after {FACE_SLOT_WAIT_SECONDS:.0f} seconds"
                )
            if slot.acquire(timeout=min(remaining, MODEL_WAIT_POLL_SECONDS)):
                return

    @staticmethod
    def _record_wait_cancellation(state: dict[str, Any], reason: str) -> None:
        if reason == "PROCESS_STOPPING":
            state["status"] = "INTERRUPTED"
            state["last_error"] = {"code": "PROCESS_STOPPING"}
        else:
            state["status"] = "PAUSED"
            state["last_error"] = {"code": "PAUSE_REQUESTED"}
        state["owner_id"] = None

    def close(self, *, timeout: float = 5.0) -> bool:
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout)))
            if thread.is_alive():
                return False
        self._release_unreleased_encoder()
        if self._unreleased_encoder is not None:
            return False
        state = _read_state(self.root)
        if state and state.get("owner_id") == self._owner_id and state.get("status") in {"RUNNING", "PAUSE_REQUESTED"}:
            state["status"] = "INTERRUPTED"
            state["owner_id"] = None
            state["last_error"] = {"code": "PROCESS_STOPPING"}
            self._save(state)
        return True

    def _release_unreleased_encoder(self) -> None:
        encoder = self._unreleased_encoder
        if encoder is None:
            return
        try:
            stop = getattr(encoder, "close", None)
            if callable(stop):
                stop()
        except BaseException:
            return
        self._unreleased_encoder = None


__all__ = ["BATCH_LIMIT", "CandidateBuildManager", "TASK_FILENAME", "TASK_STATES"]
