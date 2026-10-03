"""Observable internal service for source registration, minimal scans and counts."""

from __future__ import annotations

import ntpath
import os
import shutil
import time
from collections.abc import Callable, Sequence
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
from photo_search.ingestion.scanner import (
    DEFAULT_STABILITY_WAIT_LIMIT_MS,
    DEFAULT_STABILITY_WINDOW_MS,
    StabilityPolicy,
)
from photo_search.jobs import queue as jobs
from photo_search.media.cache import MediaCache
from photo_search.media.contract import KINDS, spec_for
from photo_search.media.pipeline import MediaGenerator, select_targets
from photo_search.media.status import media_status


def system_wall_clock_ms() -> int:
    """Wall-clock milliseconds; compared against file ``mtime``, never against leases."""
    return time.time_ns() // 1_000_000


def _default_stability_sleep(milliseconds: int) -> None:
    time.sleep(milliseconds / 1000.0)


class CatalogService:
    """Thin, testable facade over the catalog, the job queue and the scan loop."""

    def __init__(
        self,
        catalog: Catalog,
        *,
        data_root: Path,
        disk_usage: Callable[[str | Path], Any] | None = None,
        retry_backoff_ms: int = jobs.DEFAULT_RETRY_BACKOFF_MS,
        stability_window_ms: int | None = None,
        stability_wait_limit_ms: int | None = None,
        wall_clock_ms: Callable[[], int] | None = None,
        stability_sleep: Callable[[int], None] | None = None,
    ) -> None:
        self.catalog = catalog
        self.data_root = Path(data_root)
        self.disk_usage = disk_usage or shutil.disk_usage
        self.retry_backoff_ms = int(retry_backoff_ms)
        self.stability_window_ms = int(
            DEFAULT_STABILITY_WINDOW_MS if stability_window_ms is None else stability_window_ms
        )
        self.stability_wait_limit_ms = int(
            DEFAULT_STABILITY_WAIT_LIMIT_MS
            if stability_wait_limit_ms is None
            else stability_wait_limit_ms
        )
        self.wall_clock_ms = wall_clock_ms or system_wall_clock_ms
        self.stability_sleep = stability_sleep or _default_stability_sleep

    @classmethod
    def open(
        cls,
        *,
        data_root: str | Path | None = None,
        db_path: str | Path | None = None,
        clock: Callable[[], int] | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        disk_usage: Callable[[str | Path], Any] | None = None,
        retry_backoff_ms: int = jobs.DEFAULT_RETRY_BACKOFF_MS,
        stability_window_ms: int | None = None,
        stability_wait_limit_ms: int | None = None,
        wall_clock_ms: Callable[[], int] | None = None,
        stability_sleep: Callable[[int], None] | None = None,
    ) -> CatalogService:
        root = resolve_data_root(data_root)
        catalog = Catalog.open(
            db_path,
            data_root=root,
            clock=clock,
            lease_seconds=lease_seconds,
            disk_usage=disk_usage,
        )
        try:
            from photo_search.places.datasets import restore_surviving_dataset

            restore_surviving_dataset(catalog)
        except BaseException:
            catalog.close()
            raise
        return cls(
            catalog,
            data_root=root,
            disk_usage=disk_usage,
            retry_backoff_ms=retry_backoff_ms,
            stability_window_ms=stability_window_ms,
            stability_wait_limit_ms=stability_wait_limit_ms,
            wall_clock_ms=wall_clock_ms,
            stability_sleep=stability_sleep,
        )

    def stability_policy(self, overrides: StabilityPolicy | None = None) -> StabilityPolicy:
        if overrides is not None:
            return overrides
        return StabilityPolicy(
            window_ms=self.stability_window_ms,
            wait_limit_ms=self.stability_wait_limit_ms,
        )

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

    def delete_source(self, source_id: int) -> dict[str, Any]:
        """Stop managing a source root; never touches a file (P4-02Y-I §5).

        The whole removal happens in one transaction, so a reader either still sees the
        registered source with all of its locations or sees it completely gone - never a
        half-deleted root. A scan in flight is refused by :func:`catalog.store.delete_source`
        with a structured state conflict; the API layer additionally refuses a removal while
        a library refresh (a second writer over the same catalog) is running.
        """
        with self.catalog.write() as connection:
            return store.delete_source(connection, source_id=int(source_id))

    def run_scan(self, source_id: int, **options: Any) -> dict[str, Any]:
        from photo_search.ingestion.scanner import execute_scan

        options.setdefault("stability", self.stability_policy())
        return execute_scan(self, int(source_id), **options).as_dict()

    # ------------------------------------------------------------ derived media
    def media_cache(self) -> MediaCache:
        """The rebuildable artifact cache rooted in this service's data directory."""
        return MediaCache.open(self.data_root)

    def generate_media(
        self,
        kind: str,
        *,
        asset_ids: Sequence[int] | None = None,
        source_id: int | None = None,
        limit: int | None = None,
        retry_failed: bool = False,
        interrupt_after: int | None = None,
        cancel_check: Callable[[], bool] | None = None,
        failure_limit: int = 20,
        min_free_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Generate one media kind for the requested assets, isolating bad inputs."""
        spec = spec_for(kind)
        cache = self.media_cache()
        targets = select_targets(
            self.catalog,
            asset_ids=asset_ids,
            source_id=source_id,
            limit=limit,
        )
        generator_options: dict[str, Any] = {
            "cache": cache,
            "data_root": self.data_root,
            "disk_usage": self.disk_usage,
            "wall_clock_ms": self.wall_clock_ms,
        }
        if min_free_bytes is not None:
            generator_options["min_free_bytes"] = int(min_free_bytes)
        generator = MediaGenerator(
            self.catalog,
            **generator_options,
        )
        return generator.run(
            spec,
            targets,
            retry_failed=retry_failed,
            interrupt_after=interrupt_after,
            cancel_check=cancel_check,
            failure_limit=failure_limit,
        )

    def generate_thumbnail(self, **options: Any) -> dict[str, Any]:
        return self.generate_media("thumbnail", **options)

    def generate_preview(self, **options: Any) -> dict[str, Any]:
        return self.generate_media("preview", **options)

    def media_report(
        self,
        *,
        kinds: Sequence[str] = KINDS,
        source_id: int | None = None,
        asset_limit: int | None = None,
        include_failures: bool = False,
        failure_limit: int = 20,
        prune_temp: bool = False,
        min_temp_age_ms: int = 600_000,
    ) -> dict[str, Any]:
        """Read-only derived-media status; creates nothing and prunes only temp files."""
        return media_status(
            self.catalog,
            self.media_cache(),
            kinds=tuple(kinds),
            source_id=source_id,
            asset_limit=asset_limit,
            include_failures=include_failures,
            failure_limit=failure_limit,
            prune_temp=prune_temp,
            min_temp_age_ms=min_temp_age_ms,
        )

    def close(self) -> None:
        self.catalog.close()

    def __enter__(self) -> CatalogService:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
