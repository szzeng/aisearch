"""Thread-owned media generation shared by on-demand requests and cover warming."""

from __future__ import annotations

import sqlite3
import threading
from typing import Any, Callable

from photo_search.api.errors import ApiError
from photo_search.catalog.errors import CatalogError
from photo_search.catalog.sqlite_io import open_readonly
from photo_search.ingestion.service import CatalogService
from photo_search.media.pipeline import MediaInterrupted

_MEDIA_GENERATION_SLOTS = threading.BoundedSemaphore(2)
_MEDIA_GENERATION_CONDITION = threading.Condition()
_MEDIA_GENERATION_INFLIGHT: set[tuple[str, int]] = set()


class _MediaCatalogReader:
    """Minimal thread-owned catalog view used by read-only media generation."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def read_all(self, sql: str, parameters: tuple = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, parameters).fetchall())


def generate_one(service: Any, asset_id: int, kind: str) -> None:
    """Run the frozen P1-03 single-asset pipeline; per-asset failures become 409s."""
    try:
        service.generate_media(kind, asset_ids=[int(asset_id)], limit=1)
    except MediaInterrupted as interrupted:  # pragma: no cover - no cancel hook here
        raise ApiError("MEDIA_INTERRUPTED", str(interrupted)) from interrupted
    except CatalogError as error:
        raise ApiError(error.code, error.message, details=error.details) from error


def generate_one_in_worker(
    service: Any,
    asset_id: int,
    kind: str,
    *,
    generator: Callable[[Any, int, str], None] = generate_one,
) -> None:
    """Generate with a thread-owned RO catalog connection and bounded single-flight."""
    key = (str(kind), int(asset_id))
    with _MEDIA_GENERATION_CONDITION:
        while key in _MEDIA_GENERATION_INFLIGHT:
            _MEDIA_GENERATION_CONDITION.wait()
        _MEDIA_GENERATION_INFLIGHT.add(key)
    try:
        with _MEDIA_GENERATION_SLOTS:
            try:
                connection = open_readonly(service.catalog.db_path)
            except CatalogError as error:
                raise ApiError(error.code, error.message, details=error.details) from error
            try:
                reader = _MediaCatalogReader(connection)
                worker_service = CatalogService(
                    reader,  # type: ignore[arg-type] -- media generation only reads this view
                    data_root=service.data_root,
                    disk_usage=service.disk_usage,
                    wall_clock_ms=service.wall_clock_ms,
                )
                generator(worker_service, int(asset_id), str(kind))
            finally:
                connection.close()
    finally:
        with _MEDIA_GENERATION_CONDITION:
            _MEDIA_GENERATION_INFLIGHT.discard(key)
            _MEDIA_GENERATION_CONDITION.notify_all()
