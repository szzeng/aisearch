"""Observable internal service for source registration, minimal scans and counts."""

from __future__ import annotations

import ntpath
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from photo_search.catalog import store
from photo_search.catalog.db import DEFAULT_LEASE_SECONDS, Catalog
from photo_search.catalog.errors import (
    DATA_ROOT_INSIDE_SOURCE,
    INVALID_PATH,
    INVALID_STATE_TRANSITION,
    SOURCE_OVERLAP,
    CatalogError,
)
from photo_search.catalog.migrate import catalog_info
from photo_search.catalog.paths import is_same_or_ancestor, normalize_root
from photo_search.config import resolve_data_root
from photo_search.jobs import queue as jobs


class CatalogService:
    """Thin, testable facade over the catalog, the job queue and the scan loop."""

    def __init__(
        self,
        catalog: Catalog,
        *,
        data_root: Path,
        disk_usage: Callable[[str | Path], Any] | None = None,
    ) -> None:
        self.catalog = catalog
        self.data_root = Path(data_root)
        self.disk_usage = disk_usage or shutil.disk_usage

    @classmethod
    def open(
        cls,
        *,
        data_root: str | Path | None = None,
        db_path: str | Path | None = None,
        clock: Callable[[], int] | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        disk_usage: Callable[[str | Path], Any] | None = None,
        migrate: bool = True,
    ) -> CatalogService:
        root = resolve_data_root(data_root)
        catalog = Catalog.open(
            db_path,
            data_root=root,
            clock=clock,
            lease_seconds=lease_seconds,
            disk_usage=disk_usage,
            migrate=migrate,
        )
        return cls(catalog, data_root=root, disk_usage=disk_usage)

    # ------------------------------------------------------------------ reads
    def info(self) -> dict[str, Any]:
        """Read-only schema facts; safe even when this build cannot write the database."""
        payload = catalog_info(self.catalog.db_path, data_root=self.data_root)
        payload["journal_mode"] = str(
            self.catalog.read_one("PRAGMA journal_mode")[0]
        ).lower()
        return payload

    def list_sources(self) -> list[dict[str, Any]]:
        return [
            store.row_dict(row, store.SOURCE_FIELDS) for row in store.list_source_rows(self.catalog.connection)
        ]

    def get_source(self, source_id: int) -> dict[str, Any]:
        row = store.get_source_row(self.catalog.connection, int(source_id))
        payload = store.row_dict(row, store.SOURCE_FIELDS)
        payload["counts"] = store.catalog_counts(self.catalog.connection, source_id=int(source_id))
        return payload

    def counts(self, *, source_id: int | None = None) -> dict[str, Any]:
        if source_id is not None:
            store.get_source_row(self.catalog.connection, int(source_id))
        return store.catalog_counts(
            self.catalog.connection, source_id=None if source_id is None else int(source_id)
        )

    # ----------------------------------------------------------------- writes
    def register_source(
        self,
        *,
        display_name: str,
        root_path: str | Path,
        volume_hint: str | None = None,
    ) -> dict[str, Any]:
        """Register a source identity; the root path is a mapping, not the identity."""
        root = normalize_root(root_path)
        if not str(display_name).strip():
            raise CatalogError(INVALID_PATH, "source display name must not be empty")
        if os.path.exists(root) and not os.path.isdir(root):
            raise CatalogError(
                INVALID_PATH,
                "source root exists but is not a directory",
                details={"root_path": root},
            )
        if is_same_or_ancestor(root, self.data_root):
            raise CatalogError(
                DATA_ROOT_INSIDE_SOURCE,
                "the application data directory is inside this source root",
                details={"root_path": root, "data_root": str(self.data_root)},
            )
        status = "ONLINE" if os.path.isdir(root) else "OFFLINE"
        hint = volume_hint if volume_hint is not None else (ntpath.splitdrive(root)[0] or None)
        with self.catalog.write() as connection:
            for row in store.list_source_rows(connection):
                existing = str(row["root_path"])
                if is_same_or_ancestor(root, existing) or is_same_or_ancestor(existing, root):
                    raise CatalogError(
                        SOURCE_OVERLAP,
                        f"source root overlaps the existing source {int(row['source_id'])}",
                        details={
                            "existing_source_id": int(row["source_id"]),
                            "existing_root_path": existing,
                            "root_path": root,
                            "hint": "merge into the existing source or re-map it instead of adding a "
                            "second overlapping source",
                        },
                    )
            source_id = store.insert_source(
                connection,
                display_name=str(display_name).strip(),
                root_path=root,
                volume_hint=hint,
                status=status,
            )
        return self.get_source(source_id)

    def start_scan(self, source_id: int) -> dict[str, Any]:
        """Create or reuse the single non-terminal scan request of one source."""
        source_id = int(source_id)
        now_ms = self.catalog.now_ms()
        with self.catalog.write() as connection:
            store.get_source_row(connection, source_id)
            active = store.find_active_scan(connection, source_id)
            if active is not None:
                scan_id = int(active["scan_id"])
                job_id = store.scan_job_id(connection, scan_id=scan_id)
                if job_id is None:
                    raise CatalogError(
                        INVALID_STATE_TRANSITION,
                        f"scan_run {scan_id} has no catalog_scan job",
                        details={"scan_id": scan_id},
                    )
                return {
                    "source_id": source_id,
                    "scan_id": scan_id,
                    "job_id": job_id,
                    "state": str(active["state"]),
                    "reused": True,
                }
            scan_id = store.insert_scan_run(connection, source_id=source_id)
            job_id = jobs.enqueue(
                connection,
                subject_type=store.SCAN_SUBJECT_TYPE,
                subject_id=str(scan_id),
                input_revision=store.SCAN_INPUT_REVISION,
                task_type=store.SCAN_TASK_TYPE,
                processor_id=store.SCAN_PROCESSOR_ID,
                now_ms=now_ms,
            )
        return {
            "source_id": source_id,
            "scan_id": scan_id,
            "job_id": job_id,
            "state": "QUEUED",
            "reused": False,
        }

    def run_scan(self, source_id: int, **options: Any) -> dict[str, Any]:
        from photo_search.ingestion.scanner import execute_scan

        return execute_scan(self, int(source_id), **options).as_dict()

    def close(self) -> None:
        self.catalog.close()

    def __enter__(self) -> CatalogService:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
