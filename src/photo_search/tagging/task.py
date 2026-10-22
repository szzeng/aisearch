"""Durable optional tagging window with one model and one owned catalog writer."""
from __future__ import annotations

import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import CatalogError
from photo_search.config import runtime_dir
from photo_search.models.embedding.candidate_task import _atomic_json, _catalog_identity
from photo_search.tagging import service, store
from photo_search.tagging.model import read_model
from photo_search.tagging.policy import POLICY_ID
from photo_search.tagging.runtime import RamTagger

TASK_FILENAME = "tag-build-task.json"
TASK_STATES = {"RUNNING", "PAUSE_REQUESTED", "PAUSED", "INTERRUPTED", "COMPLETED", "FAILED"}


def read_state(root: Path) -> dict[str, Any] | None:
    path = runtime_dir(root) / TASK_FILENAME
    try:
        with path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("tag task state exceeds bound")
        value = json.loads(raw)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise CatalogError("TAG_TASK_STATE_INVALID", "tag task state is unreadable; preserve it for inspection") from error
    if (not isinstance(value, dict) or value.get("schema") != "tag-build-v1"
            or not isinstance(value.get("status"), str) or value["status"] not in TASK_STATES
            or not isinstance(value.get("catalog_identity"), dict)
            or any(not isinstance(value.get(key), str) or not 1 <= len(value[key]) <= maximum
                   for key, maximum in (("task_id", 32), ("request_id", 128), ("model_id", 256),
                                        ("policy_id", 128), ("processor_id", 256), ("manifest_sha256", 64)))
            or not re.fullmatch("[0-9a-f]{32}", value["task_id"])
            or not re.fullmatch("[0-9a-f]{64}", value["manifest_sha256"])
            or value["processor_id"] != store.processor_id_for(value["model_id"], value["policy_id"])
            or any(type(value.get(key)) is not int or value[key] < 0
                   for key in ("discovery_cursor", "highest_asset_id", "processed_this_task", "started_at_ms"))
            or value["discovery_cursor"] > value["highest_asset_id"]
            or (value.get("last_error") is not None and not isinstance(value["last_error"], dict))):
        raise CatalogError("TAG_TASK_STATE_INVALID", "tag task state identity is invalid")
    return value


class TagBuildManager:
    """JSON controls one task; SQLite jobs and tag results remain progress authority."""

    def __init__(self, context: Any) -> None:
        self.context = context
        self.root = Path(context.settings.data_root).resolve()
        self._lock = threading.RLock()
        self._pause, self._stop = threading.Event(), threading.Event()
        self._thread: threading.Thread | None = None
        self._owner_id = uuid.uuid4().hex

    def _save(self, state: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            state["updated_at_ms"] = service.now_ms()
            if state["status"] == "RUNNING" and self._pause.is_set():
                state["status"] = "PAUSE_REQUESTED"
            _atomic_json(runtime_dir(self.root) / TASK_FILENAME, state)
            return dict(state)

    def status(self) -> dict[str, Any] | None:
        try:
            state = read_state(self.root)
        except CatalogError as error:
            return {"status": "FAILED", "task_id": None, "last_error": {"code": error.code}, "recoverable": False}
        if state is not None and state.get("catalog_identity") != _catalog_identity(self.root):
            return {**state, "status": "FAILED", "last_error": {"code": "CATALOG_IDENTITY_CHANGED"}, "recoverable": False}
        return state

    def _require_owner(self) -> None:
        if not self.context.data_root_lease_held:
            raise CatalogError("SELECTION_WRITER_ACTIVE", "tag build requires the running data-root lease")

    def _check_maintenance(self) -> None:
        from photo_search.storage_ui import _read_json, _state_path

        maintenance = _read_json(_state_path(self.root, "storage-maintenance-request.json"))
        if maintenance and int(maintenance.get("expires_at_ms", 0)) > service.now_ms():
            raise CatalogError("SELECTION_WRITER_ACTIVE", "pending offline maintenance blocks tag building")
        refresher = getattr(self.context, "_refresher", None)
        if refresher is not None and refresher.is_running():
            raise CatalogError("MODEL_RESOURCE_BUSY", "finish library refresh before starting the tag model")

    def recover_after_lock(self) -> None:
        """Only a new OS-lease owner can invalidate its predecessor's worker lease."""
        self._require_owner()
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            try:
                state = read_state(self.root)
            except CatalogError:
                # An optional control file must not prevent ordinary app startup.
                # Keep the bytes untouched; status reports the error and writes refuse it.
                return
            if state is None or state["status"] not in {"RUNNING", "PAUSE_REQUESTED"}:
                return
            if state.get("catalog_identity") != _catalog_identity(self.root):
                state.update(status="FAILED", last_error={"code": "CATALOG_IDENTITY_CHANGED"})
            else:
                with Catalog.open(data_root=self.root) as catalog, catalog.write() as connection:
                    connection.execute(
                        "UPDATE jobs SET lease_expires_at=MIN(lease_expires_at,?),updated_at=? "
                        "WHERE processor_id=? AND task_type=? AND state='RUNNING' AND worker_id=?",
                        (service.now_ms(), service.now_ms(), state["processor_id"], store.TASK_TYPE,
                         "image-tags-" + state["task_id"]))
                state.update(status="INTERRUPTED", last_error={"code": "PROCESS_INTERRUPTED"})
            state["owner_id"] = None
            self._save(state)

    def _reap(self) -> None:
        if self._thread is not None:
            if self._thread.is_alive():
                raise CatalogError("SELECTION_WRITER_ACTIVE", "the tag worker is still releasing its resources")
            self._thread = None

    def start(self, request_id: str, *, exclusive_confirmed: bool,
              replace_task_id: str | None = None) -> dict[str, Any]:
        self._require_owner()
        self._check_maintenance()
        if exclusive_confirmed is not True:
            raise CatalogError("USAGE", "confirm the temporary exclusive model window before tagging")
        request_id = str(request_id).strip()
        if not request_id or len(request_id) > 128:
            raise CatalogError("USAGE", "request_id must contain1 to128 characters")
        if replace_task_id is not None and (
                not isinstance(replace_task_id, str) or not re.fullmatch("[0-9a-f]{32}", replace_task_id)):
            raise CatalogError("USAGE", "replace_task_id must identify the stopped task")
        info = read_model(self.root)
        identity = _catalog_identity(self.root)
        with self._lock:
            previous = read_state(self.root)
            if previous and previous.get("catalog_identity") == identity:
                if previous.get("request_id") == request_id:
                    return previous
            if replace_task_id is not None:
                if (previous is None or previous.get("catalog_identity") != identity
                        or previous["task_id"] != replace_task_id
                        or previous["status"] not in {"PAUSED", "INTERRUPTED", "FAILED"}):
                    raise CatalogError("SELECTION_WRITER_ACTIVE", "the stopped tag task changed; refresh before replacing it")
            if previous and previous.get("catalog_identity") == identity:
                if previous["status"] in {"RUNNING", "PAUSE_REQUESTED"} or (
                        previous["status"] in {"PAUSED", "INTERRUPTED", "FAILED"} and replace_task_id is None):
                    raise CatalogError("SELECTION_WRITER_ACTIVE", "continue the existing tag task before starting another")
            self._reap()
            with Catalog.open(data_root=self.root) as catalog:
                upper = int(catalog.read_one("SELECT COALESCE(MAX(asset_id),0) FROM assets")[0])
            self._pause.clear()
            self._stop.clear()
            state = {"schema": "tag-build-v1", "task_id": uuid.uuid4().hex, "request_id": request_id,
                "catalog_identity": identity, "model_id": info.model_id, "policy_id": POLICY_ID,
                "manifest_sha256": info.manifest_sha256, "processor_id": store.processor_id_for(info.model_id, POLICY_ID),
                "status": "RUNNING", "owner_id": self._owner_id, "highest_asset_id": upper,
                "discovery_cursor": 0, "processed_this_task": 0, "last_error": None,
                "started_at_ms": service.now_ms(), "batch_limit": service.BATCH_LIMIT}
            self._save(state)
            self._launch(state, retry=True)
            return dict(state)

    def pause(self, task_id: str) -> dict[str, Any]:
        self._require_owner()
        with self._lock:
            state = self.status()
            if state is None or state["task_id"] != task_id or state["status"] not in {"RUNNING", "PAUSE_REQUESTED"}:
                raise CatalogError("USAGE", "there is no matching running tag task")
            self._pause.set()
            state["status"] = "PAUSE_REQUESTED"
            return self._save(state)

    def resume(self, task_id: str) -> dict[str, Any]:
        self._require_owner()
        self._check_maintenance()
        with self._lock:
            state = self.status()
            if state is None or state["task_id"] != task_id or state["status"] not in {"PAUSED", "INTERRUPTED", "FAILED"}:
                raise CatalogError("USAGE", "there is no matching tag task awaiting continuation")
            if state.get("catalog_identity") != _catalog_identity(self.root):
                raise CatalogError("CATALOG_IDENTITY_CHANGED", "tag task belongs to a different catalog")
            info = read_model(self.root)
            if (info.model_id != state["model_id"] or info.manifest_sha256 != state["manifest_sha256"]
                    or state["policy_id"] != POLICY_ID):
                raise CatalogError("TAGGER_MODEL_INVALID", "tag model changed; the previous task cannot mix identities")
            self._reap()
            self._pause.clear()
            self._stop.clear()
            state.update(status="RUNNING", owner_id=self._owner_id, last_error=None)
            self._save(state)
            self._launch(state, retry=True)
            return dict(state)

    def _launch(self, state: dict[str, Any], *, retry: bool) -> None:
        self._thread = threading.Thread(target=self._run, args=(dict(state),), kwargs={"retry": retry},
                                        name="image-tag-build", daemon=True)
        try:
            self._thread.start()
        except Exception:
            self._thread = None
            state.update(status="FAILED", owner_id=None, last_error={"code": "WORKER_START_FAILED"})
            self._save(state)
            raise

    def _drive(self, state: dict[str, Any], runtime: RamTagger, *, retry: bool) -> None:
        with Catalog.open(data_root=self.root) as catalog:
            service.register_model(catalog, runtime.info)
            scheduler = service.build_scheduler(catalog, runtime)
            executor = service.tag_executor(catalog, runtime)
            while service.recover_cli_leases(catalog, processor_id=state["processor_id"]):
                if self._stop.is_set() or self._pause.is_set():
                    state.update(status="INTERRUPTED" if self._stop.is_set() else "PAUSED")
                    return
            while service.cancel_stale_waiting(catalog, processor_id=state["processor_id"]):
                if self._stop.is_set() or self._pause.is_set():
                    state.update(status="INTERRUPTED" if self._stop.is_set() else "PAUSED")
                    return
            if retry:
                while service.retry_waiting(catalog, processor_id=state["processor_id"]):
                    if self._stop.is_set() or self._pause.is_set():
                        state.update(status="INTERRUPTED" if self._stop.is_set() else "PAUSED")
                        return
            while True:
                if self._stop.is_set() or self._pause.is_set():
                    state.update(status="INTERRUPTED" if self._stop.is_set() else "PAUSED")
                    return
                if state["catalog_identity"] != _catalog_identity(self.root):
                    raise CatalogError("CATALOG_IDENTITY_CHANGED", "catalog identity changed during tag building")
                job = service.run_one(scheduler, executor, worker_id="image-tags-" + state["task_id"])
                if job is not None:
                    state["processed_this_task"] += 1
                    if job.state != "SUCCEEDED":
                        if job.last_error_code in service.LOCAL_INPUT_ERRORS:
                            state["skipped_this_task"] = int(state.get("skipped_this_task", 0)) + 1
                            self._save(state)
                            continue
                        state.update(status="PAUSED" if job.state == "BLOCKED" else "FAILED",
                            last_error={"code": job.last_error_code or "TAG_JOB_FAILED", "job_id": job.job_id})
                        return
                    self._save(state)
                    continue
                if scheduler.read_state().paused:
                    state.update(status="PAUSED", last_error={"code": "SCHEDULER_PAUSED"})
                    return
                targets = service.discover_targets(catalog, processor_id=state["processor_id"],
                    after_id=state["discovery_cursor"], through_id=state["highest_asset_id"])
                if targets:
                    service.enqueue_targets(catalog, targets, processor_id=state["processor_id"])
                    state["discovery_cursor"] = targets[-1]["asset_id"]
                    self._save(state)
                    continue
                counts = (store.status(catalog.connection, state["processor_id"]) or {}).get("job_counts", {})
                unfinished = {key: value for key, value in counts.items() if value and key in {"QUEUED", "RUNNING", "RETRY_WAIT"}}
                if unfinished:
                    state.update(status="PAUSED", last_error={"code": "TAG_JOBS_PENDING", "jobs": unfinished})
                else:
                    skipped = {key: value for key, value in counts.items() if value and key in {"FAILED", "BLOCKED"}}
                    state.update(status="COMPLETED", completed_at_ms=service.now_ms(),
                        last_error={"code": "IMAGES_SKIPPED", "jobs": skipped} if skipped else None)
                return

    def _run(self, state: dict[str, Any], *, retry: bool) -> None:
        from photo_search.face.service import BACKGROUND_MODEL_SLOT

        acquired = False
        try:
            acquired = BACKGROUND_MODEL_SLOT.acquire(timeout=5)
            if not acquired:
                raise TimeoutError("another background model task is active")
            with self.context.model_resource_gate.exclusive(timeout=15):
                self._check_maintenance()
                self.context.release_query_encoder_for_candidate()
                info = read_model(self.root)
                if info.model_id != state["model_id"] or info.manifest_sha256 != state["manifest_sha256"]:
                    raise CatalogError("TAGGER_MODEL_INVALID", "model package changed before tag task loading")
                runtime = RamTagger.load(info)
                try:
                    self._drive(state, runtime, retry=retry)
                finally:
                    # CUDA graph is gone before foreground queries can reload Qwen.
                    runtime.close()
        except TimeoutError:
            state.update(status="PAUSED", last_error={"code": "MODEL_RESOURCE_BUSY"})
        except Exception as error:  # noqa: BLE001 - durable task failure must survive thread exit.
            code = getattr(error, "code", type(error).__name__)
            state.update(status="PAUSED" if code in {"MODEL_RESOURCE_BUSY", "SELECTION_WRITER_ACTIVE"} else "FAILED",
                         last_error={"code": code})
        finally:
            if acquired:
                BACKGROUND_MODEL_SLOT.release()
            state["owner_id"] = None
            if state["status"] in {"RUNNING", "PAUSE_REQUESTED"}:
                state.update(status="INTERRUPTED", last_error={"code": "WORKER_STOPPED"})
            self._save(state)

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join()  # Owner must retain the data-root lease until CUDA is released.


__all__ = ["TagBuildManager", "TASK_FILENAME", "read_state"]
