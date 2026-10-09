"""One bounded in-process runner for explicitly requested source-detail face jobs.

The SQLite jobs table remains the durable queue. This runner only wakes the existing face
scheduler after a user asks for detail work, and owns its catalog connection on its worker
thread just like the other API background runners.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("photo_search.api.face_detail_runner")


class FaceDetailRunner:
    """Serialize opt-in source-detail queue wakes onto one owned worker thread."""

    def __init__(
        self,
        service_factory: Callable[[], Any],
        *,
        executor: Callable[[Any, int], Any] | None = None,
        name: str = "photo-search-face-detail",
    ) -> None:
        self._service_factory = service_factory
        self._executor = executor
        self._service: Any | None = None
        self._engine_cache: Any | None = None
        self._condition = threading.Condition()
        self._pending: deque[tuple[str, str]] = deque()
        self._active: tuple[str, str] | None = None
        self._closed = False
        self._thread: threading.Thread | None = None
        self._name = str(name)

    def submit(self, asset_id: int) -> bool:
        """Wake the durable queue; repeated clicks for an active/pending photo coalesce."""
        requested = ("asset", str(int(asset_id)))
        with self._condition:
            if self._closed:
                raise RuntimeError("the face-detail runner is closed")
            if requested == self._active or requested in self._pending:
                return False
            self._pending.append(requested)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name=self._name, daemon=True)
                self._thread.start()
            self._condition.notify_all()
            return True

    def submit_batch(self, batch_id: str) -> bool:
        requested = ("batch", str(batch_id))
        with self._condition:
            if self._closed:
                raise RuntimeError("the face-detail runner is closed")
            if requested == self._active or requested in self._pending:
                return False
            self._pending.append(requested)
            self._start_locked()
            self._condition.notify_all()
            return True

    def _start_locked(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name=self._name, daemon=True)
            self._thread.start()

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Wait until submitted queue wakes have drained (used by targeted tests)."""
        with self._condition:
            return self._condition.wait_for(
                lambda: self._active is None and not self._pending, timeout=float(timeout)
            )

    def close(self, *, timeout: float = 5.0) -> None:
        with self._condition:
            self._closed = True
            self._pending.clear()
            thread = self._thread
            self._condition.notify_all()
        if thread is not None and thread.is_alive():
            thread.join(timeout=float(timeout))
            if thread.is_alive():
                logger.warning("face-detail worker still active after %.1fs", float(timeout))
                return
        service, self._service = self._service, None
        if service is not None:
            try:
                service.close()
            except Exception:  # pragma: no cover - shutdown must not raise
                logger.debug("closing the face-detail catalog failed", exc_info=True)

    def _loop(self) -> None:
        while True:
            with self._condition:
                while not self._pending and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                kind, identifier = self._pending.popleft()
                self._active = (kind, identifier)
            try:
                service = self._service_for_thread()
                if kind == "batch":
                    try:
                        self._run_batch(service, identifier)
                    except Exception:
                        from photo_search.face.source_detail_batches import pause

                        pause(service.catalog, identifier)
                        raise
                elif self._executor is not None:
                    self._executor(service, int(identifier))
                else:
                    from photo_search.face.engine import FaceEngineCache
                    from photo_search.face.service import run_queue

                    if self._engine_cache is None:
                        self._engine_cache = FaceEngineCache()
                    selected = service.catalog.read_all(
                        "SELECT job_id FROM jobs WHERE subject_type='asset' AND subject_id=? "
                        "AND task_type='image_face_detail' AND processor_id='face-model@1'",
                        (identifier,),
                    )
                    run_queue(
                        service.catalog,
                        data_root=service.data_root,
                        worker_id="face-detail-api",
                        engine_cache=self._engine_cache,
                        source_detail=True,
                        job_ids=[int(row["job_id"]) for row in selected],
                    )
            except Exception:  # noqa: BLE001 - durable job state carries the user-visible result
                logger.exception("source-detail face queue failed for %s=%s", kind, identifier)
            finally:
                remaining_batches = []
                if kind == "batch" and self._service is not None:
                    try:
                        from photo_search.face.source_detail_batches import running_batches

                        remaining_batches = running_batches(self._service.catalog)
                    except Exception:
                        logger.exception("reading remaining face batches failed")
                with self._condition:
                    self._active = None
                    if not self._closed:
                        for remaining in remaining_batches:
                            if ("batch", remaining) not in self._pending:
                                self._pending.append(("batch", remaining))
                    if not self._pending:
                        # A finished/paused batch releases its ONNX sessions and VRAM.
                        # The next batch still reuses one cache across all its photos.
                        self._engine_cache = None
                    self._condition.notify_all()

    def _service_for_thread(self) -> Any:
        if self._service is None:
            self._service = self._service_factory()
        return self._service

    def _run_batch(self, service: Any, batch_id: str) -> None:
        from photo_search.face import contract, detail_policy
        from photo_search.face import service as face_service
        from photo_search.face import source_detail_batches as batches
        from photo_search.face.engine import FaceEngineCache
        from photo_search.face.index import FaceIndexCoordinator, reconcile

        catalog = service.catalog
        if self._engine_cache is None:
            self._engine_cache = FaceEngineCache()
        while not self._closed:
            batches.sync_jobs(catalog, batch_id)
            snapshot = batches.status(catalog, batch_id)
            if snapshot["state"] not in ("QUEUED", "RUNNING"):
                return
            item = batches.next_item(catalog, batch_id)
            if item is None:
                if not batches.next_page(catalog, batch_id):
                    return
                continue
            if batches.skip_if_ineligible(catalog, batch_id, item):
                continue
            reused = detail_policy.restore(
                catalog, asset_id=item["asset_id"], input_revision=item["input_revision"]
            )
            if not reused and item["job_id"] is not None:
                job = catalog.read_one(
                    "SELECT state,lease_expires_at FROM jobs WHERE job_id=?", (item["job_id"],)
                )
                if (
                    job is not None
                    and job["state"] == "RUNNING"
                    and int(job["lease_expires_at"] or 0) > catalog.now_ms()
                ):
                    # A restart waits for the old lease; no takeover of a live execution.
                    with self._condition:
                        self._condition.wait(timeout=0.5)
                    continue
                run = face_service.run_queue(
                    catalog,
                    data_root=service.data_root,
                    worker_id="face-detail-batch-api",
                    limit=1,
                    automatic_detail=True,
                    source_detail=True,
                    job_ids=[item["job_id"]],
                    engine_cache=self._engine_cache,
                    cancel=lambda: self._closed,
                    claim_check=lambda: (
                        batches.status(catalog, batch_id)["state"] in ("QUEUED", "RUNNING")
                    ),
                )
                if int(run["steps"]) == 0:
                    # Global scheduling may be paused; the durable batch remains resumable.
                    if (
                        catalog.read_one(
                            "SELECT state FROM jobs WHERE job_id=?", (item["job_id"],)
                        )["state"]
                        == "QUEUED"
                    ):
                        batches.pause(catalog, batch_id)
                        return
            coordinator = FaceIndexCoordinator(
                catalog, spec_revision=contract.FACE_DETAIL_SPEC_REVISION
            )
            coordinator.process_all(now_ms=catalog.now_ms(), limit=400)
            reconcile(
                catalog,
                coordinator=coordinator,
                spec_revision=contract.FACE_DETAIL_SPEC_REVISION,
                asset_ids=[item["asset_id"]],
                limit=1,
                publication_limit=400,
            )
            batches.sync_jobs(catalog, batch_id)
            remaining = batches.next_item(catalog, batch_id)
            if remaining is not None and remaining["asset_id"] == item["asset_id"]:
                # An incomplete publication/gate needs attention, not another inference.
                batches.pause(catalog, batch_id)
                return
