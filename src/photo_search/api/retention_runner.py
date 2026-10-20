"""Bounded, restart-visible execution of user-authorized retention cleanup runs."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from photo_search.catalog.errors import CatalogError

logger = logging.getLogger("photo_search.api.retention")
MAX_RUN_ASSETS = 20
MAX_VECTOR_EVENTS_PER_ASSET = 100


class RetentionCleanupRunner:
    """Process at most one persisted run at a time, reopening service inside its worker."""

    def __init__(self, service_factory: Callable[[], Any]) -> None:
        self._service_factory = service_factory
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._stop_requested = threading.Event()

    def start(self, run_id: str) -> bool:
        with self._lock:
            if self._closed or (self._thread is not None and self._thread.is_alive()):
                return False
            self._thread = threading.Thread(
                target=self._run, args=(str(run_id),), name="photo-search-retention", daemon=True
            )
            self._stop_requested.clear()
            self._thread.start()
            return True

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._stop_requested.set()
            thread = self._thread
        if thread is not None:
            # The app releases the data-root lease after close() returns. Wait for any
            # in-flight catalog/vector operation to finish before another process can
            # acquire that lease and operate on the same data root.
            thread.join()

    def _run(self, run_id: str) -> None:
        service = None
        try:
            service = self._service_factory()
            catalog = service.catalog
            now_ms = int(catalog.now_ms())
            with catalog.write() as connection:
                run = connection.execute(
                    "SELECT mode,state FROM library_retention_runs WHERE run_id=?", (run_id,)
                ).fetchone()
                if run is None:
                    return
                connection.execute(
                    "UPDATE library_retention_runs SET state='RUNNING',stage='资格复核',updated_at_ms=? "
                    "WHERE run_id=? AND state IN ('QUEUED','RUNNING','PARTIAL')",
                    (now_ms, run_id),
                )
            from photo_search.indexing.store import IndexCoordinator
            from photo_search.library_retention import (
                begin_asset_retention_cleanup,
                finalize_asset_retention_cleanup,
            )

            rows = catalog.read_all(
                "SELECT asset_id FROM library_retention_run_items WHERE run_id=? "
                "AND state IN ('QUEUED','RUNNING') ORDER BY asset_id LIMIT ?",
                (run_id, MAX_RUN_ASSETS),
            )
            for row in rows:
                if self._stop_requested.is_set():
                    break
                asset_id = int(row["asset_id"])
                stamp = int(catalog.now_ms())
                with catalog.write() as connection:
                    connection.execute(
                        "UPDATE library_retention_run_items SET state='RUNNING',stage='资格复核',"
                        "started_at_ms=COALESCE(started_at_ms,?) WHERE run_id=? AND asset_id=?",
                        (stamp, run_id, asset_id),
                    )
                    connection.execute(
                        "UPDATE library_retention_runs SET stage='资格复核',updated_at_ms=? WHERE run_id=?",
                        (stamp, run_id),
                    )
                try:
                    result = begin_asset_retention_cleanup(
                        catalog, asset_id=asset_id, now_ms=stamp,
                        manual_early=str(run["mode"]) == "MANUAL",
                    )
                    if result.get("state") != "COMPLETE":
                        coordinator = IndexCoordinator(catalog)
                        coordinator.process_all(
                            now_ms=int(catalog.now_ms()), limit=MAX_VECTOR_EVENTS_PER_ASSET
                        )
                        pending = int(catalog.read_one(
                            "SELECT COUNT(*) FROM index_outbox WHERE asset_id=? "
                            "AND event_type='DELETE_VECTOR' AND state<>'CONFIRMED'", (asset_id,)
                        )[0])
                        if pending:
                            self._item_update(
                                catalog, run_id, asset_id, state="QUEUED",
                                stage="等待向量删除回执", reason="DELETE_VECTOR 尚未全部确认",
                            )
                            break
                        result = finalize_asset_retention_cleanup(
                            catalog, asset_id=asset_id, now_ms=int(catalog.now_ms())
                        )
                    if result.get("state") == "COMPLETE":
                        self._item_update(
                            catalog, run_id, asset_id, state="COMPLETE", stage="逻辑清理完成",
                            reason="受管缓存需在应用停止后运行 storage clean-retained",
                            logical_rows_removed=int(result.get("machine_rows_removed", 1)),
                        )
                    else:
                        self._item_update(
                            catalog, run_id, asset_id, state="SKIPPED",
                            stage=str(result.get("state", "已跳过")),
                            reason="; ".join(str(value) for value in result.get("blockers", [])) or str(result.get("state")),
                        )
                except CatalogError as error:
                    blocked = str(error.code) == "ASSET_RETENTION_BLOCKED"
                    blockers = error.details.get("blockers", []) if hasattr(error, "details") else []
                    reason = "; ".join(str(value) for value in blockers)
                    if not reason:
                        reason = f"{error.code}: {error.message}"
                    self._item_update(
                        catalog, run_id, asset_id,
                        state="SKIPPED" if blocked else "FAILED",
                        stage="资格复核阻断" if blocked else "可恢复失败",
                        reason=reason,
                    )
                except Exception as error:  # noqa: BLE001 - durable run remains retryable.
                    logger.exception("retention cleanup item failed", extra={"asset_id": asset_id})
                    self._item_update(
                        catalog, run_id, asset_id, state="FAILED", stage="可恢复失败",
                        reason=type(error).__name__,
                    )
            self._finish(catalog, run_id)
        except Exception:  # noqa: BLE001 - worker failure must remain visible and resumable.
            logger.exception("retention cleanup run failed", extra={"run_id": run_id})
            if service is not None:
                try:
                    with service.catalog.write() as connection:
                        connection.execute(
                            "UPDATE library_retention_runs SET state='FAILED',stage='可恢复失败',"
                            "last_error='worker failure',updated_at_ms=? WHERE run_id=?",
                            (int(service.catalog.now_ms()), run_id),
                        )
                except Exception:
                    logger.exception("could not persist retention worker failure")
        finally:
            if service is not None:
                service.close()

    @staticmethod
    def _item_update(
        catalog: Any, run_id: str, asset_id: int, *, state: str, stage: str,
        reason: str | None, logical_rows_removed: int = 0,
    ) -> None:
        stamp = int(catalog.now_ms())
        with catalog.write() as connection:
            connection.execute(
                "UPDATE library_retention_run_items SET state=?,stage=?,reason=?,logical_rows_removed=?,"
                "completed_at_ms=CASE WHEN ? IN ('COMPLETE','SKIPPED','FAILED') THEN ? ELSE completed_at_ms END "
                "WHERE run_id=? AND asset_id=?",
                (state, stage, reason, logical_rows_removed, state, stamp, run_id, asset_id),
            )
            connection.execute(
                "UPDATE library_retention_runs SET updated_at_ms=?,stage=? WHERE run_id=?",
                (stamp, stage, run_id),
            )

    @staticmethod
    def _finish(catalog: Any, run_id: str) -> None:
        stamp = int(catalog.now_ms())
        with catalog.write() as connection:
            counts = connection.execute(
                "SELECT state,COUNT(*) AS n,COALESCE(SUM(logical_rows_removed),0) AS logical "
                "FROM library_retention_run_items WHERE run_id=? GROUP BY state", (run_id,)
            ).fetchall()
            by_state = {str(row["state"]): int(row["n"]) for row in counts}
            logical = sum(int(row["logical"]) for row in counts)
            unfinished = by_state.get("QUEUED", 0) + by_state.get("RUNNING", 0)
            failed = by_state.get("FAILED", 0)
            skipped = by_state.get("SKIPPED", 0)
            state = "PARTIAL" if unfinished or failed or skipped else "COMPLETE"
            stage = "可恢复待继续" if unfinished else ("部分完成/有跳过" if state == "PARTIAL" else "逻辑清理完成")
            connection.execute(
                "UPDATE library_retention_runs SET state=?,stage=?,total_assets=(SELECT COUNT(*) FROM "
                "library_retention_run_items WHERE run_id=?),completed_assets=?,skipped_assets=?,"
                "failed_assets=?,logical_rows_removed=?,updated_at_ms=? WHERE run_id=?",
                (state, stage, run_id, by_state.get("COMPLETE", 0), skipped, failed, logical, stamp, run_id),
            )
