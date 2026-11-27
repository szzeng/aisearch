"""One bounded worker for user-initiated PersonService writes."""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

from photo_search.api.errors import ApiError
from photo_search.ingestion.service import CatalogService
from photo_search.persons.service import PersonService
from photo_search.runtime.resource_lifecycle import close_owned_resource

logger = logging.getLogger("photo_search.api")

MAX_PENDING_PERSON_WRITES = 4
PERSON_WRITE_CLOSE_GRACE_SECONDS = 5.0
_WRITE_OPERATIONS = frozenset({
    "create_person_from_face",
    "decide_face_mappings",
    "edit_face_assignments",
    "merge",
    "regroup_faces",
    "rename",
    "retention_cleanup",
    "set_face_assignment",
    "set_visibility",
    "split",
    "undo",
})


_CATALOG_OPERATIONS = {
    "favorite": ("favorites", "set_favorite"),
    "favorites_batch": ("favorites", "set_favorites_batch"),
    "photo_collection_create": ("photo_collections", "create_collection"),
    "photo_collection_rename": ("photo_collections", "rename_collection"),
    "photo_collection_delete": ("photo_collections", "delete_collection"),
    "photo_collection_members": ("photo_collections", "edit_members"),
    "person_collection_create": ("persons.collections", "create_collection"),
    "person_collection_rename": ("persons.collections", "rename_collection"),
    "person_collection_delete": ("persons.collections", "delete_collection"),
    "person_collection_members": ("persons.collections", "edit_members"),
    "person_collection_faces": ("persons.collections", "edit_face_members"),
    "person_cover": ("persons.albums", "set_cover"),
    "inbox_visibility": ("persons.albums", "set_inbox_visibility"),
    "source_rename": (None, "rename_source"),
}


def catalog_write_busy() -> ApiError:
    return ApiError(
        "CATALOG_WRITE_BUSY", "the catalog write worker is not accepting requests right now",
        status=503, details={"retryable": True}, headers={"Retry-After": "1"},
    )


def _dispatch(service: CatalogService, operation: str, parameters: dict[str, Any]) -> dict[str, Any]:
    if operation in _WRITE_OPERATIONS:
        return getattr(PersonService(service.catalog), operation)(**parameters)
    module, name = _CATALOG_OPERATIONS[operation]
    if module is None:
        return service.rename_source(**parameters)
    from importlib import import_module

    function = getattr(import_module(f"photo_search.{module}"), name)
    return function(service.catalog, **parameters)


def person_write_busy() -> ApiError:
    return ApiError(
        "PERSON_WRITE_BUSY",
        "the person write worker is not accepting requests right now",
        status=503,
        details={"retryable": True},
        headers={"Retry-After": "1"},
    )


class PersonWriteWorker:
    def __init__(self, service_factory: Callable[[], CatalogService]) -> None:
        self._service_factory = service_factory
        self._condition = threading.Condition()
        self._pending: deque[tuple[str, dict[str, Any], Future[dict[str, Any]]]] = deque()
        self._thread: threading.Thread | None = None
        self._stopping = False

    def submit(self, operation: str, **parameters: Any) -> Future[dict[str, Any]]:
        if operation not in _WRITE_OPERATIONS and operation not in _CATALOG_OPERATIONS:
            raise ValueError(f"unsupported person write operation: {operation}")
        with self._condition:
            if self._stopping or len(self._pending) >= MAX_PENDING_PERSON_WRITES:
                raise (person_write_busy() if operation in _WRITE_OPERATIONS else catalog_write_busy())
            if self._thread is None:
                # If the server loop exits after deferred shutdown, accepted writes still
                # need to finish before the process can exit and release its data-root lease.
                thread = threading.Thread(target=self._run, name="photo-search-person-write", daemon=False)
                thread.start()
                self._thread = thread
            result: Future[dict[str, Any]] = Future()
            # Acceptance commits to running this task even if its HTTP waiter is cancelled.
            result.set_running_or_notify_cancel()
            self._pending.append((operation, parameters, result))
            self._condition.notify()
            return result

    def stop(self) -> threading.Thread | None:
        """Refuse further tasks and wake the worker to drain accepted tasks and close."""
        with self._condition:
            self._stopping = True
            self._condition.notify()
            return self._thread

    def close(self, *, timeout: float = PERSON_WRITE_CLOSE_GRACE_SECONDS) -> bool:
        thread = self.stop()
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()

    def _run(self) -> None:
        service: CatalogService | None = None
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._pending or self._stopping)
                    if not self._pending:
                        break
                    operation, parameters, result = self._pending.popleft()
                try:
                    if service is None:
                        service = self._service_factory()
                    answer = _dispatch(service, operation, parameters)
                except BaseException as error:
                    result.set_exception(error)
                else:
                    result.set_result(answer)
        finally:
            if service is not None:
                close_owned_resource(
                    service,
                    on_failure=lambda error: logger.warning(
                        "closing the person write catalog failed; retaining it for retry: %s", error,
                    ),
                )
