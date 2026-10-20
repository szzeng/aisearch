"""Conservative preview and bounded execution helpers for unavailable library assets."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import ASSET_RETENTION_BLOCKED, CatalogError
from photo_search.retention import OBSERVATION_MS

DEFAULT_LIMIT = 50
MAX_LIMIT = 50
ACTIVE_JOB_STATES = ("QUEUED", "RUNNING", "RETRY_WAIT")


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    # ``table`` comes only from sqlite_master, not from a request parameter.
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _exists(connection: sqlite3.Connection, table: str, where: str, args: tuple[Any, ...]) -> bool:
    return connection.execute(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1", args).fetchone() is not None


def _unknown_asset_reference_schema(connection: sqlite3.Connection, tables: set[str]) -> bool:
    """Fail closed if a new table introduces asset/face links we do not understand."""
    recognized = {
        "assets", "file_locations", "asset_metadata", "asset_favorites", "photo_collection_members",
        "asset_retention_source_evidence", "asset_retention_state", "retention_source_anomalies",
        "asset_retention_barriers",
        "library_retention_settings", "library_retention_runs", "library_retention_run_items",
        "library_retention_run_candidates",
        # Both smart references are understood: exclusions are human state and
        # protect an asset; cache entries are rebuildable and cascade on asset GC.
        "photo_smart_exclusions", "photo_smart_cache_entries", "user_annotations", "asset_tags",
        "asset_geo_facts", "asset_ocr_text", "asset_ocr_regions", "asset_place_exclusions",
        "cn_admin_resolutions", "place_resolutions", "place_operation_items",
        "face_redetection_groups", "face_redetection_mappings", "face_detail_association_queue",
        "face_detail_auto_assets", "face_detail_batch_assets", "person_hidden",
        "faces", "face_embeddings", "face_person_links", "face_assignment_controls",
        "face_exclusions", "face_association_queue", "face_index_entries",
        "face_index_outbox", "face_candidates", "face_inbox_hidden", "person_operations",
        "person_operation_items", "person_collection_members", "person_collection_face_members",
        "persons", "person_aliases", "asset_geo_facts",
        "asset_place_assignments", "asset_place_assignment_history", "place_operations",
        "place_operation_items", "search_documents", "search_feedback",
        "search_projection_queue", "jobs", "artifacts", "index_outbox", "index_entries",
        "upgrade_targets", "ocr_documents", "ocr_texts", "ocr_results",
    }
    for table in tables - recognized:
        try:
            columns = _columns(connection, table)
        except sqlite3.Error:
            return True
        if columns.intersection({"asset_id", "face_id", "location_id", "person_id", "entity_id"}):
            return True
    return False


def _human_or_protected(
    connection: sqlite3.Connection, tables: set[str], asset_id: int, *, include_barrier: bool = True
) -> tuple[bool, list[str]]:
    """Return conservative human/reference protections for one bounded list row."""
    if include_barrier and "asset_retention_barriers" in tables and _exists(
        connection, "asset_retention_barriers", "asset_id = ?", (asset_id,)
    ):
        return True, ["已有清理屏障"]
    checks: list[tuple[str, str, tuple[Any, ...], str]] = []
    if "asset_favorites" in tables:
        checks.append(("asset_favorites", "asset_id = ?", (asset_id,), "人工收藏"))
    if "photo_smart_exclusions" in tables:
        checks.append(("photo_smart_exclusions", "asset_id = ?", (asset_id,), "智能合集人工排除"))
    if "photo_collection_members" in tables:
        checks.append(("photo_collection_members", "asset_id = ?", (asset_id,), "照片合集收录"))
    if "user_annotations" in tables:
        checks.append(("user_annotations", "asset_id = ?", (asset_id,), "人工备注或收藏"))
    if "asset_tags" in tables:
        cols = _columns(connection, "asset_tags")
        predicate = "asset_id = ?" if "source" not in cols else "asset_id = ? AND upper(source) = 'USER'"
        checks.append(("asset_tags", predicate, (asset_id,), "人工标签"))
    if "asset_place_assignments" in tables:
        checks.append(("asset_place_assignments", "asset_id = ? AND (manual_lock = 1 OR origin = 'MANUAL')", (asset_id,), "人工地点归属"))
    if "asset_place_assignment_history" in tables:
        checks.append(("asset_place_assignment_history", "asset_id = ? AND (manual_lock = 1 OR origin = 'MANUAL')", (asset_id,), "人工地点历史"))
    if "search_feedback" in tables:
        checks.append(("search_feedback", "asset_id = ?", (asset_id,), "搜索反馈历史"))
    if "person_collection_face_members" in tables:
        # The archive survives face replacement and content changes. Its soft
        # asset anchor remains a human reference even without a current face.
        checks.append(("person_collection_face_members", "asset_id = ?", (asset_id,), "人脸合集收录"))

    face_ids = [
        str(row[0]) for row in connection.execute(
            "SELECT face_id FROM faces WHERE asset_id=?", (asset_id,)
        )
    ] if "faces" in tables else []
    has_faces = bool(face_ids)
    if has_faces:
        face_filter = "face_id IN (SELECT face_id FROM faces WHERE asset_id = ?)"
        if "face_person_links" in tables:
            if _exists(connection, "face_person_links", f"{face_filter} AND (assignment_kind = 'MANUAL' OR last_operation_id IS NOT NULL)", (asset_id,)):
                checks.append(("face_person_links", "1=1", (), "人物归属或人工纠错"))
            # An automatically linked face may still point into a user-maintained shared
            # person collection or hidden-person state. Preserve the person and its anchors.
            if "person_collection_members" in tables and _exists(
                connection,
                "person_collection_members",
                "person_id IN (SELECT person_id FROM face_person_links WHERE " + face_filter + ")",
                (asset_id,),
            ):
                checks.append(("person_collection_members", "1=1", (), "人物合集"))
            if "person_hidden" in tables and _exists(
                connection,
                "person_hidden",
                "person_id IN (SELECT person_id FROM face_person_links WHERE " + face_filter + ")",
                (asset_id,),
            ):
                checks.append(("person_hidden", "1=1", (), "隐藏人物记录"))
        if "person_operation_items" in tables and face_ids:
            face_marks = ",".join("?" for _ in face_ids)
            if _exists(
                connection, "person_operation_items",
                f"entity_type IN ('FACE_CONTROL','FACE_MAPPING_GROUP') AND entity_key IN ({face_marks})",
                tuple(face_ids),
            ):
                checks.append(("person_operation_items", "1=1", (), "人物操作历史"))
        if "face_redetection_groups" in tables and _exists(
            connection, "face_redetection_groups",
            "asset_id=? AND (state='OPEN' OR last_operation_id IS NOT NULL)", (asset_id,),
        ):
            checks.append(("face_redetection_groups", "1=1", (), "人脸重检待处理或人工历史"))
        if "face_redetection_mappings" in tables and _exists(
            connection, "face_redetection_mappings",
            "asset_id=? AND (last_operation_id IS NOT NULL OR state IN ('CONFIRMED','REJECTED'))",
            (asset_id,),
        ):
            checks.append(("face_redetection_mappings", "1=1", (), "人脸映射人工操作历史"))
        if "face_assignment_controls" in tables and _exists(
            connection, "face_assignment_controls",
            f"{face_filter} AND (mode <> 'AUTO_ALLOWED' OR last_operation_id IS NOT NULL)", (asset_id,),
        ):
            checks.append(("face_assignment_controls", "1=1", (), "人工人脸控制"))
        if "face_exclusions" in tables and _exists(connection, "face_exclusions", f"{face_filter} AND active = 1", (asset_id,)):
            checks.append(("face_exclusions", "1=1", (), "人物排除记录"))
        if "face_inbox_hidden" in tables and _exists(connection, "face_inbox_hidden", face_filter, (asset_id,)):
            checks.append(("face_inbox_hidden", "1=1", (), "忽略的人脸记录"))

    if "jobs" in tables:
        states = ",".join("?" for _ in ACTIVE_JOB_STATES)
        if _exists(
            connection,
            "jobs",
            f"((subject_type = 'asset' AND subject_id = ?) OR "
            f"(subject_type = 'face' AND subject_id IN (SELECT face_id FROM faces WHERE asset_id=?)) OR "
            f"(subject_type = 'location' AND subject_id IN (SELECT CAST(location_id AS TEXT) FROM file_locations WHERE asset_id=?))) "
            f"AND state IN ({states})",
            (str(asset_id), asset_id, asset_id, *ACTIVE_JOB_STATES),
        ):
            checks.append(("jobs", "1=1", (), "活动任务"))
    for table, where, args, label in checks:
        if where == "1=1" or _exists(connection, table, where, args):
            return True, [label]

    protected_tables = (
        ("asset_place_exclusions", "asset_id = ?", (asset_id,), "地点排除记录"),
        ("place_operation_items", "asset_id = ?", (asset_id,), "地点操作历史"),
    )
    for table, where, args, label in protected_tables:
        if table in tables and _exists(connection, table, where, args):
            return True, [label]
    for table, where, args, label in (
        ("upgrade_targets", "asset_id=? AND state IN ('PLANNED','IN_FLIGHT')", (asset_id,), "模型升级任务进行中"),
        ("face_detail_batch_assets", "asset_id=? AND state IN ('QUEUED','RUNNING')", (asset_id,), "人脸详情批次进行中"),
    ):
        if table in tables and _exists(connection, table, where, args):
            return True, [label]
    return False, []


def _classification(
    connection: sqlite3.Connection,
    tables: set[str],
    row: sqlite3.Row,
    unknown_schema: bool,
    retention_state: str | None = None,
    unresolved_anomaly: bool = False,
) -> dict[str, Any]:
    asset_id = int(row["asset_id"])
    locations = int(row["location_count"] or 0)
    present = int(row["present_count"] or 0)
    missing = int(row["missing_count"] or 0)
    reasons: list[str] = []
    has_refs = False
    reference_query_failed = False
    try:
        has_refs, reasons = _human_or_protected(connection, tables, asset_id)
        if unknown_schema:
            has_refs = True
            reasons = ["发现无法解释的资产引用类型"]
    except Exception:
        has_refs = True
        reference_query_failed = True
        reasons = ["引用查询失败，按受保护处理"]

    if present:
        category, reason = "已有有效副本", "至少一个已登记位置仍为 PRESENT"
    elif reference_query_failed or unknown_schema:
        category, reason = "待核实/受保护", "；".join(reasons)
    elif locations == 0:
        category, reason = "来源已取消登记/零位置", "资产没有位置记录；不能推断原图已被用户永久删除"
    elif has_refs:
        category, reason = "包含人工信息或受保护引用", "；".join(reasons)
    elif str(row["unavailable_source_count"] or 0) != "0":
        category, reason = "全局无可用位置但证据不足", "存在来源状态离线或异常；不能把它解释为永久删除"
    else:
        category, reason = "全局无可用位置但证据不足", "首次确认失效时间未知；不具备清理资格"
    return {
        "asset_id": asset_id,
        "missing_locations": missing,
        "location_count": locations,
        "availability_summary": (
            f"{missing} 个位置标记 MISSING，另有 PRESENT 副本可用"
            if present and missing
            else ("PRESENT 副本可用" if present else (f"{missing} 个位置标记 MISSING" if missing else "没有位置记录"))
        ),
        "reason": reason,
        "classification": category,
        "has_human_or_protected_references": has_refs,
        "retention_state": retention_state or "UNKNOWN",
        "unresolved_source_anomaly": bool(unresolved_anomaly),
        "retention_eligibility": (
            "异常来源待人工核实；需重新成功扫描后开始观察"
            if unresolved_anomaly
            else (
                {"OBSERVING": "30 天观察中；到期后仍需所有来源重新成功扫描",
                 "ELIGIBLE": "观察期已满且来源已复核；执行时仍会逐资产复核",
                 "BLOCKED": "资格已阻断；需重新成功扫描后重新计时"}.get(
                    str(retention_state), "不具备清理资格（观察期起点未知）"
                )
                if not present
                else "受有效副本保护"
            )
        ),
    }


def library_retention_preview(catalog: Catalog, *, after_asset_id: int = 0, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Return aggregate counts and one keyset page. All catalog work is read-only."""
    with catalog.read_snapshot() as connection:
        return _preview_snapshot(connection, catalog.db_path, after_asset_id=after_asset_id, limit=limit)


def _partial_retention_unsettled_predicate(run_alias: str = "r") -> str:
    """Cursor-bounded durable work required to resume a PARTIAL run."""
    return (
        "EXISTS (SELECT 1 FROM library_retention_run_items i WHERE i.run_id="
        f"{run_alias}.run_id AND i.asset_id>{run_alias}.selection_after_asset_id) OR "
        "EXISTS (SELECT 1 FROM library_retention_run_candidates c WHERE c.run_id="
        f"{run_alias}.run_id AND c.asset_id>{run_alias}.selection_after_asset_id "
        f"AND c.asset_id<={run_alias}.selection_max_asset_id)"
    )


def _unsettled_retention_predicate(run_alias: str = "r") -> str:
    """The single authoritative active/resumable run predicate."""
    return (
        f"({run_alias}.state IN ('QUEUED','RUNNING') OR "
        f"({run_alias}.state='PARTIAL' AND ({_partial_retention_unsettled_predicate(run_alias)})))"
    )


def _unsettled_retention_run(connection: sqlite3.Connection) -> sqlite3.Row | None:
    """Find the one resumable run using cursor-bounded durable work, not stale summaries."""
    active = connection.execute(
        "SELECT * FROM library_retention_runs WHERE state IN ('QUEUED','RUNNING') "
        "ORDER BY updated_at_ms DESC LIMIT 1"
    ).fetchone()
    if active is not None:
        return active
    return connection.execute(
        "SELECT r.* FROM library_retention_runs r WHERE r.state='PARTIAL' AND "
        + _partial_retention_unsettled_predicate()
        + " LIMIT 1"
    ).fetchone()


def _run_has_unsettled_work(connection: sqlite3.Connection, run: sqlite3.Row) -> bool:
    result = connection.execute(
        "SELECT " + _unsettled_retention_predicate() +
        " AS has_unsettled_work FROM library_retention_runs r WHERE r.run_id=?",
        (str(run["run_id"]),),
    ).fetchone()
    return bool(result and result["has_unsettled_work"])


def create_library_retention_run(catalog: Catalog, *, mode: str) -> dict[str, Any]:
    """Persist a click-time-fenced run; the worker selects at most 20 rows per batch."""
    normalized = str(mode).upper()
    if normalized not in {"MANUAL", "AUTOMATIC"}:
        raise ValueError("mode must be MANUAL or AUTOMATIC")
    now = int(catalog.now_ms())
    with catalog.write() as connection:
        active = _unsettled_retention_run(connection)
        if active is not None:
            if str(active["mode"]) != normalized:
                return {
                    "conflict": True,
                    "code": "RUN_IN_PROGRESS",
                    "run_id": str(active["run_id"]),
                    "mode": str(active["mode"]),
                    "state": str(active["state"]),
                }
            run_id = str(active["run_id"])
            if str(active["state"]) == "PARTIAL":
                connection.execute(
                    "UPDATE library_retention_runs SET state='QUEUED',stage='等待安全续做',updated_at_ms=? "
                    "WHERE run_id=?", (now, run_id),
                )
                connection.execute(
                    "UPDATE library_retention_run_items SET state='QUEUED',stage='等待安全重试',reason=NULL "
                    "WHERE run_id=? AND state='RUNNING'", (run_id,),
                )
            return {"run_id": run_id, "state": "QUEUED" if str(active["state"]) == "PARTIAL" else str(active["state"]), "reused": True}

        states = ("ELIGIBLE",) if normalized == "AUTOMATIC" else ("OBSERVING", "ELIGIBLE")
        marks = ",".join("?" for _ in states)
        run_id = uuid.uuid4().hex
        connection.execute(
            "INSERT INTO library_retention_runs(run_id,mode,state,stage,total_assets,created_at_ms,updated_at_ms,"
            "selection_cutoff_at_ms) VALUES(?,?, 'QUEUED','候选范围快照',0,?,?,?)",
            (run_id, normalized, now, now, now),
        )
        connection.execute(
            f"INSERT INTO library_retention_run_candidates(run_id,asset_id) "
            f"SELECT ?,r.asset_id FROM asset_retention_state r JOIN assets a USING(asset_id) "
            f"WHERE r.state IN ({marks}) AND r.updated_at_ms<=? ORDER BY r.asset_id",
            (run_id, *states, now),
        )
        selection_max = int(connection.execute(
            "SELECT COALESCE(MAX(asset_id),0) FROM library_retention_run_candidates WHERE run_id=?",
            (run_id,),
        ).fetchone()[0])
        initial_state = "QUEUED" if selection_max else "COMPLETE"
        initial_stage = "等待执行" if selection_max else "没有符合条件的候选"
        connection.execute(
            "UPDATE library_retention_runs SET state=?,stage=?,selection_max_asset_id=?,"
            "selection_cutoff_at_ms=?,has_more_candidates=? WHERE run_id=?",
            (initial_state, initial_stage, selection_max, now, int(bool(selection_max)), run_id),
        )
        return {"run_id": run_id, "state": initial_state,
                "reused": False, "total_assets": 0, "has_more_candidates": bool(selection_max)}


def library_retention_run_progress(catalog: Catalog, *, run_id: str | None = None) -> dict[str, Any] | None:
    """Return persisted run summary and at most 20 item details."""
    with catalog.read_snapshot() as connection:
        if run_id is None:
            row = connection.execute(
                "SELECT * FROM library_retention_runs ORDER BY created_at_ms DESC,run_id DESC LIMIT 1"
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM library_retention_runs WHERE run_id=?", (str(run_id),)
            ).fetchone()
        if row is None:
            return None
        has_unsettled_work = _run_has_unsettled_work(connection, row)
        items = connection.execute(
            "SELECT asset_id,state,stage,reason,logical_rows_removed,attempt_count,outcome_category "
            "FROM library_retention_run_items "
            "WHERE run_id=? ORDER BY asset_id LIMIT 20", (str(row["run_id"]),)
        ).fetchall()
        return {"run": dict(row), "items": [dict(item) for item in items],
                "durable": True, "has_unsettled_work": has_unsettled_work}


def _retention_run_candidate_ids(connection: sqlite3.Connection, run: sqlite3.Row, *, limit: int) -> list[int]:
    rows = connection.execute(
        "SELECT asset_id FROM library_retention_run_candidates "
        "WHERE run_id=? AND asset_id>? AND asset_id<=? ORDER BY asset_id LIMIT ?",
        (str(run["run_id"]), int(run["selection_after_asset_id"]),
         int(run["selection_max_asset_id"]), int(limit)),
    ).fetchall()
    return [int(row["asset_id"]) for row in rows]


def prepare_library_retention_run_batch(catalog: Catalog, *, run_id: str, batch_size: int = 20) -> dict[str, Any]:
    """Load/prepare one bounded keyset batch, or durably finish an exhausted run."""
    limit = max(1, min(int(batch_size), 20))
    now = int(catalog.now_ms())
    with catalog.write() as connection:
        run = connection.execute(
            "SELECT * FROM library_retention_runs WHERE run_id=?", (str(run_id),)
        ).fetchone()
        if run is None:
            return {"assets": [], "done": True}
        cursor = int(run["selection_after_asset_id"])
        current = connection.execute(
            "SELECT asset_id,state,attempt_count FROM library_retention_run_items "
            "WHERE run_id=? AND asset_id>? ORDER BY asset_id LIMIT 21",
            (str(run_id), cursor),
        ).fetchall()
        if current:
            return {"assets": [dict(row) for row in current], "done": False,
                    "has_more": bool(run["has_more_candidates"])}

        selected = _retention_run_candidate_ids(connection, run, limit=limit + 1)
        if not selected:
            terminal = "PARTIAL" if int(run["failed_assets"]) or int(run["skipped_assets"]) else "COMPLETE"
            stage = "已检查全部候选；含失败或跳过" if terminal == "PARTIAL" else "已检查全部候选"
            connection.execute(
                "UPDATE library_retention_runs SET state=?,stage=?,has_more_candidates=0,updated_at_ms=? "
                "WHERE run_id=?", (terminal, stage, now, str(run_id)),
            )
            return {"assets": [], "done": True, "state": terminal}
        # Keep the most recent bounded batch visible for progress/recovery. Replace it
        # only when another batch is actually available.
        connection.execute("DELETE FROM library_retention_run_items WHERE run_id=?", (str(run_id),))
        has_more = len(selected) > limit
        batch = selected[:limit]
        connection.executemany(
            "INSERT INTO library_retention_run_items(run_id,asset_id,state,stage,outcome_category) "
            "VALUES(?,?,'QUEUED','排队','OTHER')",
            ((str(run_id), asset_id) for asset_id in batch),
        )
        connection.execute(
            "UPDATE library_retention_runs SET state='RUNNING',stage='检查当前批次',"
            "has_more_candidates=?,updated_at_ms=? WHERE run_id=?",
            (int(has_more), now, str(run_id)),
        )
        rows = connection.execute(
            "SELECT asset_id,state,attempt_count FROM library_retention_run_items "
            "WHERE run_id=? ORDER BY asset_id LIMIT ?", (str(run_id), limit),
        ).fetchall()
        return {"assets": [dict(row) for row in rows], "done": False, "has_more": has_more}


def settle_library_retention_run_batch(catalog: Catalog, *, run_id: str) -> bool:
    """Atomically advance the cursor and aggregate one fully terminal batch exactly once."""
    now = int(catalog.now_ms())
    with catalog.write() as connection:
        run = connection.execute(
            "SELECT selection_after_asset_id FROM library_retention_runs WHERE run_id=?",
            (str(run_id),),
        ).fetchone()
        if run is None:
            return False
        rows = connection.execute(
            "SELECT asset_id,state,outcome_category FROM library_retention_run_items "
            "WHERE run_id=? AND asset_id>? ORDER BY asset_id",
            (str(run_id), int(run["selection_after_asset_id"])),
        ).fetchall()
        if not rows or any(str(row["state"]) in {"QUEUED", "RUNNING"} for row in rows):
            return False
        counts = {state: sum(str(row["state"]) == state for row in rows)
                  for state in ("COMPLETE", "SKIPPED", "FAILED")}
        skipped_human = sum(str(row["state"]) == "SKIPPED" and str(row["outcome_category"]) == "HUMAN" for row in rows)
        skipped_source = sum(str(row["state"]) == "SKIPPED" and str(row["outcome_category"]) == "SOURCE" for row in rows)
        skipped_other = counts["SKIPPED"] - skipped_human - skipped_source
        after = max(int(row["asset_id"]) for row in rows)
        connection.execute(
            "UPDATE library_retention_runs SET selection_after_asset_id=?,checked_assets=checked_assets+?,"
            "total_assets=total_assets+?,completed_assets=completed_assets+?,skipped_assets=skipped_assets+?,"
            "failed_assets=failed_assets+?,skipped_human_assets=skipped_human_assets+?,"
            "skipped_source_assets=skipped_source_assets+?,skipped_other_assets=skipped_other_assets+?,"
            "stage='准备下一批',updated_at_ms=? WHERE run_id=?",
            (after, len(rows), len(rows), counts["COMPLETE"], counts["SKIPPED"], counts["FAILED"],
             skipped_human, skipped_source, skipped_other, now, str(run_id)),
        )
        connection.execute(
            "DELETE FROM library_retention_run_candidates WHERE run_id=? AND asset_id<=?",
            (str(run_id), after),
        )
        return True


def set_library_retention_automatic(catalog: Catalog, *, enabled: bool) -> dict[str, Any]:
    now = int(catalog.now_ms())
    with catalog.write() as connection:
        connection.execute(
            "UPDATE library_retention_settings SET automatic_enabled=?,updated_at_ms=? WHERE singleton=1",
            (int(bool(enabled)), now),
        )
        row = connection.execute(
            "SELECT automatic_enabled,updated_at_ms FROM library_retention_settings WHERE singleton=1"
        ).fetchone()
        return {"automatic_enabled": bool(row["automatic_enabled"]), "updated_at_ms": int(row["updated_at_ms"])}


def library_retention_automatic_settings(catalog: Catalog) -> dict[str, Any]:
    with catalog.read_snapshot() as connection:
        row = connection.execute(
            "SELECT automatic_enabled,updated_at_ms FROM library_retention_settings WHERE singleton=1"
        ).fetchone()
        return {"automatic_enabled": bool(row["automatic_enabled"]), "updated_at_ms": int(row["updated_at_ms"])}


def _preview_snapshot(
    connection: sqlite3.Connection,
    db_path: Path,
    *,
    after_asset_id: int,
    limit: int,
) -> dict[str, Any]:
    bounded_limit = max(1, min(int(limit), MAX_LIMIT))
    tables = _tables(connection)
    unknown_schema = _unknown_asset_reference_schema(connection, tables)
    counts = connection.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM file_locations WHERE availability='MISSING') AS missing_locations,
          (SELECT COUNT(*) FROM assets a WHERE NOT EXISTS (
             SELECT 1 FROM file_locations l WHERE l.asset_id=a.asset_id AND l.availability='PRESENT'
           )) AS assets_without_present,
          (SELECT COUNT(*) FROM assets a WHERE NOT EXISTS (
             SELECT 1 FROM file_locations l WHERE l.asset_id=a.asset_id
           )) AS zero_location_assets,
          (SELECT COUNT(*) FROM assets a WHERE EXISTS (
             SELECT 1 FROM file_locations l WHERE l.asset_id=a.asset_id AND l.availability='PRESENT'
           )) AS assets_with_present_copy
        """
    ).fetchone()
    rows = connection.execute(
        """
        SELECT a.asset_id,
               COUNT(l.location_id) AS location_count,
               SUM(CASE WHEN l.availability='MISSING' THEN 1 ELSE 0 END) AS missing_count,
               SUM(CASE WHEN l.availability='PRESENT' THEN 1 ELSE 0 END) AS present_count,
               SUM(CASE WHEN l.availability='MISSING' AND (s.source_id IS NULL OR s.status <> 'ONLINE') THEN 1 ELSE 0 END) AS unavailable_source_count
          FROM assets a
          LEFT JOIN file_locations l ON l.asset_id=a.asset_id
          LEFT JOIN source_roots s ON s.source_id=l.source_id
         WHERE a.asset_id > ?
         GROUP BY a.asset_id
        HAVING missing_count > 0 OR location_count = 0
         ORDER BY a.asset_id
         LIMIT ?
        """,
        (max(0, int(after_asset_id)), bounded_limit + 1),
    ).fetchall()
    has_more = len(rows) > bounded_limit
    page_rows = rows[:bounded_limit]
    page_ids = [int(row["asset_id"]) for row in page_rows]
    retention_by_id: dict[int, tuple[str | None, bool]] = {}
    if page_ids and {"asset_retention_state", "retention_source_anomalies"}.issubset(tables):
        marks = ",".join("?" for _ in page_ids)
        retention_rows = connection.execute(
            "SELECT a.asset_id, r.state, EXISTS(SELECT 1 FROM file_locations l "
            "JOIN retention_source_anomalies x USING(source_id) WHERE l.asset_id=a.asset_id "
            "AND x.state='NEEDS_REVIEW') AS unresolved_anomaly FROM assets a "
            "LEFT JOIN asset_retention_state r USING(asset_id) "
            f"WHERE a.asset_id IN ({marks})",
            tuple(page_ids),
        ).fetchall()
        retention_by_id = {
            int(item["asset_id"]): (
                None if item["state"] is None else str(item["state"]),
                bool(item["unresolved_anomaly"]),
            )
            for item in retention_rows
        }
    items = [
        _classification(
            connection, tables, row, unknown_schema,
            *retention_by_id.get(int(row["asset_id"]), (None, False)),
        )
        for row in page_rows
    ]
    next_cursor = items[-1]["asset_id"] if has_more and items else None
    try:
        page_counts = connection.execute("PRAGMA page_count").fetchone()
        page_size = connection.execute("PRAGMA page_size").fetchone()
        sqlite_logical_bytes = int(page_counts[0]) * int(page_size[0])
    except sqlite3.Error:
        sqlite_logical_bytes = None
    def physical_size(path: Path) -> int | None:
        try:
            return int(path.stat().st_size)
        except OSError:
            return None

    db_bytes = physical_size(db_path)
    wal_bytes = physical_size(Path(str(db_path) + "-wal"))
    retention_time_evidence = connection.execute(
        "SELECT "
        "SUM(CASE WHEN first_missing_at_ms IS NOT NULL AND state IN ('OBSERVING','ELIGIBLE') THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN first_missing_at_ms IS NULL OR state IN ('UNKNOWN','BLOCKED') THEN 1 ELSE 0 END) "
        "FROM asset_retention_state"
    ).fetchone()
    trusted_timed = int(retention_time_evidence[0] or 0)
    unknown_timed = int(retention_time_evidence[1] or 0)
    return {
        "counts": {key: int(counts[key] or 0) for key in (
            "missing_locations", "assets_without_present", "zero_location_assets", "assets_with_present_copy"
        )},
        "retention_counts": {
            str(row["state"]): int(row["count"])
            for row in connection.execute(
                "SELECT state, COUNT(*) AS count FROM asset_retention_state GROUP BY state"
            ).fetchall()
        } if "asset_retention_state" in tables else {},
        "unresolved_source_anomalies": int(connection.execute(
            "SELECT COUNT(*) FROM retention_source_anomalies WHERE state='NEEDS_REVIEW'"
        ).fetchone()[0]) if "retention_source_anomalies" in tables else 0,
        "source_anomalies": [dict(row) for row in connection.execute(
            "SELECT source_id, scan_id, baseline_present_assets, newly_missing_assets, created_at_ms "
            "FROM retention_source_anomalies WHERE state='NEEDS_REVIEW' "
            "ORDER BY created_at_ms, source_id, scan_id LIMIT 50"
        ).fetchall()] if "retention_source_anomalies" in tables else [],
        "items": items,
        "limit": bounded_limit,
        "next_cursor": next_cursor,
        "observation_period_days": 30,
        "retention_time_evidence": (
            f"已有可信观察时间的资产 {trusted_timed} 张；仍有 {unknown_timed} 张历史或受阻资产没有可用资格时间。"
            if trusted_timed or unknown_timed
            else "尚无可信失效观察记录；历史 MISSING 时间不会从文件或数据库旧时间猜测。"
        ),
        "storage": {
            "sqlite_logical_page_bytes": sqlite_logical_bytes,
            "sqlite_catalog_file_bytes": db_bytes,
            "sqlite_wal_file_bytes": wal_bytes,
            "sqlite_reclaimable_bytes": None,
            "sqlite_reclaimable_note": "无法准确估计；逻辑页大小与 VACUUM 后实际回收量不同。",
            "lance_logical_rows": None,
            "lance_file_reclaimable_bytes": None,
            "lance_note": "本预览不读取或遍历 LanceDB；逻辑行数和物理回收量无法准确估计。",
        },
        "unknown_reference_schema": unknown_schema,
        "read_only": True,
    }


def _cleanup_preflight(
    connection: sqlite3.Connection, *, asset_id: int, require_expired: bool
) -> list[str]:
    """Return fail-closed blockers from current authority state, never from a stale preview."""
    tables = _tables(connection)
    blockers: list[str] = []
    asset = connection.execute(
        "SELECT asset_id FROM assets WHERE asset_id=?", (int(asset_id),)
    ).fetchone()
    if asset is None:
        return ["资产已不存在"]
    state = connection.execute(
        "SELECT first_missing_at_ms,state FROM asset_retention_state WHERE asset_id=?",
        (int(asset_id),),
    ).fetchone()
    if state is None or state["first_missing_at_ms"] is None:
        blockers.append("缺少可信的首次失效时间")
    elif require_expired and str(state["state"]) != "ELIGIBLE":
        blockers.append("30 天观察期或到期后来源复核未完成")
    elif not require_expired and str(state["state"]) not in {"OBSERVING", "ELIGIBLE"}:
        blockers.append("失效观察状态未就绪")

    location_rows = connection.execute(
        "SELECT l.source_id,l.availability,s.status AS source_status "
        "FROM file_locations l JOIN source_roots s USING(source_id) WHERE l.asset_id=?",
        (int(asset_id),),
    ).fetchall()
    if not location_rows:
        blockers.append("没有可验证的失效位置")
    if any(str(row["availability"]) == "PRESENT" for row in location_rows):
        blockers.append("发现有效副本")
    if any(str(row["availability"]) != "MISSING" for row in location_rows):
        blockers.append("存在非 MISSING 位置")
    if any(str(row["source_status"]) != "ONLINE" for row in location_rows):
        blockers.append("相关来源未全部在线")
    source_ids = sorted({int(row["source_id"]) for row in location_rows})
    evidence = connection.execute(
        "SELECT e.source_id,e.missing_confirmed_at_ms,r.state AS scan_state "
        "FROM asset_retention_source_evidence e JOIN scan_runs r "
        "ON r.scan_id=e.scan_id AND r.source_id=e.source_id "
        "WHERE e.asset_id=? ORDER BY e.source_id",
        (int(asset_id),),
    ).fetchall()
    evidence_by_source = {int(row["source_id"]): row for row in evidence}
    if source_ids != sorted(evidence_by_source):
        blockers.append("来源缺少匹配的成功失效复核证据")
    if any(str(row["scan_state"]) != "SUCCEEDED" for row in evidence):
        blockers.append("存在非成功扫描证据")
    if state is not None and state["first_missing_at_ms"] is not None:
        threshold = int(state["first_missing_at_ms"]) + (OBSERVATION_MS if require_expired else 0)
        if any(int(row["missing_confirmed_at_ms"]) < threshold for row in evidence):
            blockers.append("来源复核证据早于清理资格时间")
    if connection.execute(
        "SELECT 1 FROM retention_source_anomalies x JOIN file_locations l USING(source_id) "
        "WHERE l.asset_id=? AND x.state='NEEDS_REVIEW' LIMIT 1",
        (int(asset_id),),
    ).fetchone() is not None:
        blockers.append("存在未解决的来源批量异常")
    # One shared human/reference classifier drives both preview and final authority.
    has_human_references, human_reasons = _human_or_protected(
        connection, tables, int(asset_id), include_barrier=False
    )
    if has_human_references:
        blockers.extend(human_reasons)
    if _unknown_asset_reference_schema(connection, tables):
        blockers.append("存在无法解释的资产引用类型")
    return list(dict.fromkeys(blockers))


def begin_asset_retention_cleanup(
    catalog: Catalog, *, asset_id: int, now_ms: int, manual_early: bool = False
) -> dict[str, Any]:
    """Revalidate one asset and atomically activate its barrier plus vector tombstones.

    ``manual_early`` is a future user-confirmed authorization to skip only the age threshold;
    it never skips current scan/source checks or human-reference protections.
    """
    from photo_search.retention import (
        activate_asset_retention_cleanup,
        asset_retention_barrier,
    )

    with catalog.write() as connection:
        barrier = asset_retention_barrier(connection, asset_id=int(asset_id))
        if barrier is not None and str(barrier["state"]) == "COMPLETE":
            return {"asset_id": int(asset_id), "state": "COMPLETE", "delete_event_ids": []}
        blockers = _cleanup_preflight(
            connection, asset_id=int(asset_id), require_expired=not manual_early
        )
        if blockers:
            restored_copy = connection.execute(
                "SELECT 1 FROM file_locations WHERE asset_id=? AND availability='PRESENT' LIMIT 1",
                (int(asset_id),),
            ).fetchone() is not None
            if barrier is not None and restored_copy:
                _abort_restored_asset_cleanup(connection, asset_id=int(asset_id), now_ms=int(now_ms))
                return {"asset_id": int(asset_id), "state": "CANCELLED_RESTORED",
                        "delete_event_ids": [], "blockers": blockers}
            has_human_protection, _ = _human_or_protected(
                connection, _tables(connection), int(asset_id), include_barrier=False
            )
            if barrier is not None and has_human_protection:
                _abort_restored_asset_cleanup(connection, asset_id=int(asset_id), now_ms=int(now_ms))
                return {"asset_id": int(asset_id), "state": "CANCELLED_PROTECTED",
                        "delete_event_ids": [], "blockers": blockers}
            raise CatalogError(
                ASSET_RETENTION_BLOCKED,
                "asset retention cleanup is not currently safe",
                details={"asset_id": int(asset_id), "blockers": blockers,
                         "barrier_state": None if barrier is None else str(barrier["state"])},
            )
        barrier_id, event_ids = activate_asset_retention_cleanup(
            connection, asset_id=int(asset_id), now_ms=int(now_ms)
        )
        return {
            "asset_id": int(asset_id),
            "barrier_id": barrier_id,
            "state": "WAITING_FOR_VECTOR_DELETE" if event_ids else "READY_TO_FINALIZE",
            "delete_event_ids": event_ids,
        }


def _abort_restored_asset_cleanup(
    connection: sqlite3.Connection, *, asset_id: int, now_ms: int
) -> None:
    """Drop the temporary barrier and schedule vector repair after a copy is restored."""
    from photo_search.indexing.store import IndexCoordinator

    coordinator = IndexCoordinator  # static scheduling helpers; no Lance handle is opened.
    connection.execute(
        "DELETE FROM index_outbox WHERE asset_id=? AND event_type='DELETE_VECTOR'",
        (int(asset_id),),
    )
    connection.execute(
        "DELETE FROM asset_retention_barriers WHERE asset_id=? AND state='ACTIVE'",
        (int(asset_id),),
    )
    rows = connection.execute(
        "SELECT e.space_id,e.generation_id,e.artifact_id,a.*,g.generation_id AS g_generation_id "
        "FROM index_entries e JOIN artifacts a USING(artifact_id) "
        "JOIN index_generations g USING(generation_id) "
        "WHERE e.asset_id=? ORDER BY e.space_id,e.generation_id",
        (int(asset_id),),
    ).fetchall()
    for row in rows:
        coordinator._schedule_existing(
            connection,
            artifact=row,
            generation={"generation_id": row["g_generation_id"]},
            asset_id=int(asset_id),
            now_ms=int(now_ms),
        )


def finalize_asset_retention_cleanup(
    catalog: Catalog, *, asset_id: int, now_ms: int
) -> dict[str, Any]:
    """Delete safe machine rows after all image-vector tombstones are confirmed."""
    from photo_search.places import revision as place_revision
    from photo_search.retention import asset_retention_barrier, complete_asset_retention_barrier

    with catalog.write() as connection:
        barrier = asset_retention_barrier(connection, asset_id=int(asset_id))
        if barrier is None:
            raise CatalogError(
                ASSET_RETENTION_BLOCKED, "asset has no active retention cleanup barrier"
            )
        if str(barrier["state"]) == "COMPLETE":
            return {"asset_id": int(asset_id), "state": "COMPLETE", "machine_rows_removed": 0}
        if connection.execute(
            "SELECT 1 FROM file_locations WHERE asset_id=? AND availability='PRESENT' LIMIT 1",
            (int(asset_id),),
        ).fetchone() is not None:
            _abort_restored_asset_cleanup(connection, asset_id=int(asset_id), now_ms=int(now_ms))
            return {"asset_id": int(asset_id), "state": "CANCELLED_RESTORED", "machine_rows_removed": 0}
        pending = connection.execute(
            "SELECT COUNT(*) FROM index_outbox WHERE asset_id=? AND event_type='DELETE_VECTOR' "
            "AND state<>'CONFIRMED'",
            (int(asset_id),),
        ).fetchone()[0]
        if int(pending):
            return {"asset_id": int(asset_id), "state": "WAITING_FOR_VECTOR_DELETE", "machine_rows_removed": 0}
        blockers = _cleanup_preflight(connection, asset_id=int(asset_id), require_expired=False)
        if blockers:
            has_human_protection, _ = _human_or_protected(
                connection, _tables(connection), int(asset_id), include_barrier=False
            )
            if has_human_protection:
                _abort_restored_asset_cleanup(connection, asset_id=int(asset_id), now_ms=int(now_ms))
            return {"asset_id": int(asset_id), "state": "BLOCKED_RECHECK", "blockers": blockers, "machine_rows_removed": 0}

        asset_identity = connection.execute(
            "SELECT sha256 FROM assets WHERE asset_id=?", (int(asset_id),)
        ).fetchone()
        if asset_identity is None:
            raise CatalogError(ASSET_RETENTION_BLOCKED, "asset identity disappeared during cleanup")
        face_rows = connection.execute(
            "SELECT face_id,bbox_json,artifact_id FROM faces WHERE asset_id=?", (int(asset_id),)
        ).fetchall()
        face_ids = [str(row["face_id"]) for row in face_rows]
        face_crop_keys: list[str] = []
        from photo_search.media.face_crop import crop_key, parse_bbox

        for face in face_rows:
            try:
                face_crop_keys.append(
                    crop_key(
                        face_id=str(face["face_id"]),
                        box=parse_bbox(str(face["bbox_json"])),
                        sha256=str(asset_identity["sha256"]),
                    )
                )
            except (CatalogError, TypeError, ValueError):
                # Invalid face geometry cannot establish a cache identity; leave that crop
                # outside the cleanup set rather than guessing which file belongs to it.
                continue
        connection.execute(
            "UPDATE asset_retention_barriers SET media_sha256=?,face_crop_keys_json=? "
            "WHERE asset_id=? AND state='ACTIVE'",
            (str(asset_identity["sha256"]), json.dumps(sorted(set(face_crop_keys))), int(asset_id)),
        )
        for table in (
            "face_redetection_mappings", "face_redetection_groups",
            "face_detail_batch_assets", "face_detail_auto_assets",
        ):
            if table in _tables(connection):
                connection.execute(f"DELETE FROM {table} WHERE asset_id=?", (int(asset_id),))
        if "upgrade_targets" in _tables(connection):
            connection.execute("DELETE FROM upgrade_targets WHERE asset_id=?", (int(asset_id),))
        if face_ids:
            marks = ",".join("?" for _ in face_ids)
            values = tuple(face_ids)
            face_artifact_ids = [str(row["artifact_id"]) for row in face_rows]
            if face_artifact_ids and "face_detail_coordinate_frames" in _tables(connection):
                artifact_marks = ",".join("?" for _ in face_artifact_ids)
                connection.execute(
                    f"DELETE FROM face_detail_coordinate_frames WHERE artifact_id IN ({artifact_marks})",
                    tuple(face_artifact_ids),
                )
            for table in (
                "face_candidates", "face_index_entries", "face_index_outbox", "face_embeddings",
                "face_assignment_controls", "face_exclusions",
            ):
                connection.execute(f"DELETE FROM {table} WHERE face_id IN ({marks})", values)
            connection.execute(f"DELETE FROM face_person_links WHERE face_id IN ({marks})", values)
            connection.execute(f"DELETE FROM faces WHERE face_id IN ({marks})", values)
        for table in (
            "face_association_queue", "face_detail_association_queue",
            "face_detail_auto_assets", "face_detail_batch_assets",
        ):
            connection.execute(f"DELETE FROM {table} WHERE asset_id=?", (int(asset_id),))

        # Delete only known machine-derived rows. Human place history/exclusions and all
        # unsupported asset references were rejected by _cleanup_preflight above.
        for table in (
            "asset_geo_facts", "asset_ocr_text", "asset_ocr_regions", "cn_admin_resolutions",
            "place_resolutions", "asset_metadata", "search_projection_queue",
            "asset_retention_source_evidence", "asset_retention_state",
        ):
            connection.execute(f"DELETE FROM {table} WHERE asset_id=?", (int(asset_id),))
        if "asset_tags" in _tables(connection):
            columns = _columns(connection, "asset_tags")
            predicate = "asset_id=?" if "source" not in columns else "asset_id=? AND upper(source)<>'USER'"
            connection.execute(f"DELETE FROM asset_tags WHERE {predicate}", (int(asset_id),))
        connection.execute(
            "DELETE FROM asset_place_assignments WHERE asset_id=? AND mode='AUTO_ALLOWED' "
            "AND origin='NONE' AND manual_lock=0",
            (int(asset_id),),
        )
        connection.execute(
            "DELETE FROM photo_smart_cache_entries WHERE asset_id=?", (int(asset_id),)
        )
        connection.execute(
            "DELETE FROM search_documents WHERE asset_id=?", (int(asset_id),)
        )
        connection.execute(
            "DELETE FROM file_locations WHERE asset_id=?", (int(asset_id),)
        )
        # Keep processor artifacts/jobs for the later C2-E physical GC pass. Remove their
        # vector references only after every DELETE_VECTOR receipt is durable.
        connection.execute("DELETE FROM index_outbox WHERE asset_id=?", (int(asset_id),))
        connection.execute("DELETE FROM index_entries WHERE asset_id=?", (int(asset_id),))
        connection.execute("DELETE FROM assets WHERE asset_id=?", (int(asset_id),))
        place_revision.bump_filter_revision(
            connection,
            cause=place_revision.CAUSE_ASSET_CONTENT_DETACHED,
            now_ms=int(now_ms),
            asset_ids=[int(asset_id)],
        )
        if not complete_asset_retention_barrier(
            connection, asset_id=int(asset_id), now_ms=int(now_ms)
        ):
            raise CatalogError(ASSET_RETENTION_BLOCKED, "asset retention barrier was lost")
        return {"asset_id": int(asset_id), "state": "COMPLETE", "machine_rows_removed": 1}
