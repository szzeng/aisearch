"""Bounded, restart-visible execution of user-authorized retention cleanup runs."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from photo_search.catalog.errors import CatalogError

logger = logging.getLogger("photo_search.api.retention")
MAX_RUN_ASSETS = 20
MAX_VECTOR_EVENTS_PER_ASSET = 100
MAX_VECTOR_DELETE_DRAINS = 5
MAX_ITEM_ATTEMPTS = 3


class RetentionCleanupRunner:
    """Process one persisted cleanup run at a time with bounded batches and retries."""

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
            thread.join()

    def _run(self, run_id: str) -> None:
        service = None
        try:
            service = self._service_factory()
            catalog = service.catalog
            with catalog.write() as connection:
                run = connection.execute(
                    "SELECT mode,state FROM library_retention_runs WHERE run_id=?", (run_id,)
                ).fetchone()
                if run is None:
                    return
                connection.execute(
                    "UPDATE library_retention_runs SET state='RUNNING',stage='资格复核',updated_at_ms=? "
                    "WHERE run_id=? AND state IN ('QUEUED','RUNNING','PARTIAL')",
                    (int(catalog.now_ms()), run_id),
                )

            from photo_search.indexing.store import IndexCoordinator
            from photo_search.library_retention import (
                begin_asset_retention_cleanup,
                finalize_asset_retention_cleanup,
                prepare_library_retention_run_batch,
                settle_library_retention_run_batch,
            )

            coordinator = IndexCoordinator(catalog)
            while not self._stop_requested.is_set():
                batch = prepare_library_retention_run_batch(
                    catalog, run_id=run_id, batch_size=MAX_RUN_ASSETS
                )
                if batch.get("done"):
                    break
                for item in batch["assets"]:
                    if self._stop_requested.is_set():
                        break
                    asset_id = int(item["asset_id"])
                    attempts = int(item["attempt_count"])
                    if str(item["state"]) in {"COMPLETE", "SKIPPED"}:
                        continue
                    if attempts >= MAX_ITEM_ATTEMPTS:
                        continue
                    for attempt in range(attempts, MAX_ITEM_ATTEMPTS):
                        if self._stop_requested.is_set():
                            break
                        stamp = int(catalog.now_ms())
                        with catalog.write() as connection:
                            connection.execute(
                                "UPDATE library_retention_run_items SET state='RUNNING',stage='资格复核',"
                                "attempt_count=attempt_count+1,started_at_ms=COALESCE(started_at_ms,?),reason=NULL "
                                "WHERE run_id=? AND asset_id=?",
                                (stamp, run_id, asset_id),
                            )
                            connection.execute(
                                "UPDATE library_retention_runs SET stage='复核并清理当前批次',updated_at_ms=? WHERE run_id=?",
                                (stamp, run_id),
                            )
                        try:
                            result = begin_asset_retention_cleanup(
                                catalog, asset_id=asset_id, now_ms=stamp,
                                manual_early=str(run["mode"]) == "MANUAL",
                            )
                            if result.get("state") not in {"COMPLETE", "CANCELLED_RESTORED", "BLOCKED_RECHECK"}:
                                remaining_vector_events = MAX_VECTOR_EVENTS_PER_ASSET
                                for _ in range(MAX_VECTOR_DELETE_DRAINS):
                                    if remaining_vector_events <= 0:
                                        break
                                    pending = int(catalog.read_one(
                                        "SELECT COUNT(*) FROM index_outbox WHERE asset_id=? "
                                        "AND event_type='DELETE_VECTOR' AND state<>'CONFIRMED'", (asset_id,)
                                    )[0])
                                    if pending == 0:
                                        break
                                    consumed = coordinator.process_retention_deletes(
                                        asset_id=asset_id,
                                        now_ms=int(catalog.now_ms()),
                                        limit=remaining_vector_events,
                                    )
                                    remaining_vector_events -= consumed
                                    if consumed == 0:
                                        break
                                pending = int(catalog.read_one(
                                    "SELECT COUNT(*) FROM index_outbox WHERE asset_id=? "
                                    "AND event_type='DELETE_VECTOR' AND state<>'CONFIRMED'", (asset_id,)
                                )[0])
                                if pending:
                                    reason = self._unclaimable_delete_reason(catalog, asset_id)
                                    raise CatalogError(
                                        "INDEX_SUBMISSION_FAILED",
                                        f"目标 DELETE_VECTOR 在本次有界 drain 后仍待确认：{reason}",
                                        details={"pending_delete_events": pending, "blocking_reason": reason},
                                    )
                                result = finalize_asset_retention_cleanup(
                                    catalog, asset_id=asset_id, now_ms=int(catalog.now_ms())
                                )

                            state = str(result.get("state"))
                            if state == "COMPLETE":
                                self._item_update(
                                    catalog, run_id, asset_id, state="COMPLETE", stage="逻辑清理完成",
                                    reason="受管缓存需在应用停止后运行 storage clean-retained",
                                )
                            else:
                                reason = "; ".join(str(value) for value in result.get("blockers", [])) or state
                                self._item_update(
                                    catalog, run_id, asset_id, state="SKIPPED", stage=state, reason=reason,
                                    outcome_category=self._outcome_category(reason),
                                )
                            break
                        except CatalogError as error:
                            blockers = error.details.get("blockers", []) if hasattr(error, "details") else []
                            reason = "; ".join(str(value) for value in blockers) or f"{error.code}: {error.message}"
                            if str(error.code) == "ASSET_RETENTION_BLOCKED":
                                self._item_update(
                                    catalog, run_id, asset_id, state="SKIPPED", stage="资格复核阻断",
                                    reason=reason, outcome_category=self._outcome_category(reason),
                                )
                                break
                            self._retry_or_fail(catalog, run_id, asset_id, attempt, reason)
                        except Exception as error:  # noqa: BLE001 - bounded retries preserve later candidates.
                            logger.exception("retention cleanup item failed", extra={"asset_id": asset_id})
                            self._retry_or_fail(catalog, run_id, asset_id, attempt, type(error).__name__)
                if self._stop_requested.is_set():
                    break
                settle_library_retention_run_batch(catalog, run_id=run_id)
        except Exception:  # noqa: BLE001 - leave current batch durable and resumable.
            logger.exception("retention cleanup run failed", extra={"run_id": run_id})
            if service is not None:
                try:
                    with service.catalog.write() as connection:
                        connection.execute(
                            "UPDATE library_retention_runs SET state='PARTIAL',stage='可恢复待继续',"
                            "has_more_candidates=1,last_error='worker failure',updated_at_ms=? WHERE run_id=?",
                            (int(service.catalog.now_ms()), run_id),
                        )
                except Exception:
                    logger.exception("could not persist retention worker failure")
        finally:
            if service is not None:
                service.close()

    @classmethod
    def _retry_or_fail(cls, catalog: Any, run_id: str, asset_id: int, attempt: int, reason: str) -> None:
        if attempt + 1 >= MAX_ITEM_ATTEMPTS:
            cls._item_update(catalog, run_id, asset_id, state="FAILED", stage="可恢复失败", reason=reason)
        else:
            cls._item_update(catalog, run_id, asset_id, state="QUEUED", stage="有限重试", reason=reason)
            time.sleep(0.05 * (attempt + 1))

    @staticmethod
    def _item_update(
        catalog: Any, run_id: str, asset_id: int, *, state: str, stage: str,
        reason: str | None, logical_rows_removed: int = 0,
        outcome_category: str = "OTHER",
    ) -> None:
        stamp = int(catalog.now_ms())
        with catalog.write() as connection:
            connection.execute(
                "UPDATE library_retention_run_items SET state=?,stage=?,reason=?,logical_rows_removed=?,"
                "outcome_category=?,"
                "completed_at_ms=CASE WHEN ? IN ('COMPLETE','SKIPPED','FAILED') THEN ? ELSE completed_at_ms END "
                "WHERE run_id=? AND asset_id=?",
                (state, stage, reason, logical_rows_removed, outcome_category, state, stamp, run_id, asset_id),
            )

    @staticmethod
    def _outcome_category(reason: str) -> str:
        text = str(reason)
        if any(token in text for token in ("人工", "收藏", "备注", "标签", "人物", "纠错", "排除", "合集", "隐藏")):
            return "HUMAN"
        if any(token in text for token in ("来源", "扫描", "复核", "MISSING", "PRESENT", "失效", "异常", "副本", "离线")):
            return "SOURCE"
        return "OTHER"

    @staticmethod
    def _unclaimable_delete_reason(catalog: Any, asset_id: int) -> str:
        row = catalog.read_one(
            "SELECT o.event_id,o.state,o.last_error_code,"
            "EXISTS(SELECT 1 FROM asset_retention_barriers b WHERE b.asset_id=o.asset_id) AS has_barrier,"
            "EXISTS(SELECT 1 FROM artifacts a WHERE a.artifact_id=o.artifact_id) AS has_artifact,"
            "EXISTS(SELECT 1 FROM index_generations g WHERE g.generation_id=o.generation_id) AS has_generation "
            "FROM index_outbox o WHERE o.asset_id=? AND o.event_type='DELETE_VECTOR' "
            "AND o.state<>'CONFIRMED' ORDER BY o.created_at,o.event_id LIMIT 1",
            (int(asset_id),),
        )
        if row is None:
            return "未找到待确认的目标事件，状态已变化"
        if not int(row["has_barrier"]):
            return f"事件 {row['event_id']} 缺少持久清理屏障"
        if not int(row["has_artifact"]) or not int(row["has_generation"]):
            return f"事件 {row['event_id']} 引用的 artifact 或 generation 不存在"
        code = str(row["last_error_code"] or "无")
        return f"事件 {row['event_id']} 当前状态为 {row['state']}，最近错误码：{code}"
