"""Conservative, read-only preview of unavailable library assets.

This module intentionally does not delete or update catalog rows. Missing timestamps
are not inferred from asset creation, EXIF, file mtime, or scan timestamps.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from photo_search.catalog.db import Catalog

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
        "assets", "file_locations", "asset_metadata", "user_annotations", "asset_tags",
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


def _human_or_protected(connection: sqlite3.Connection, tables: set[str], asset_id: int) -> tuple[bool, list[str]]:
    """Return conservative human/reference protections for one bounded list row."""
    checks: list[tuple[str, str, tuple[Any, ...], str]] = []
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

    has_faces = "faces" in tables and _exists(connection, "faces", "asset_id = ?", (asset_id,))
    if has_faces:
        face_filter = "face_id IN (SELECT face_id FROM faces WHERE asset_id = ?)"
        if "face_person_links" in tables:
            if _exists(connection, "face_person_links", f"{face_filter} AND (assignment_kind = 'MANUAL' OR decision = 'CONFIRMED')", (asset_id,)):
                checks.append(("face_person_links", "1=1", (), "人物归属或人工纠错"))
                # A mapped person may belong to an explicit user collection.
                if "person_collection_members" in tables and _exists(
                    connection,
                    "person_collection_members",
                    "person_id IN (SELECT person_id FROM face_person_links WHERE " + face_filter + ")",
                    (asset_id,),
                ):
                    checks.append(("person_collection_members", "1=1", (), "人物合集"))
                if "person_operations" in tables:
                    checks.append(("person_operations", "1=1", (), "人物操作历史"))
                if "person_hidden" in tables and _exists(
                    connection,
                    "person_hidden",
                    "person_id IN (SELECT person_id FROM face_person_links WHERE " + face_filter + ")",
                    (asset_id,),
                ):
                    checks.append(("person_hidden", "1=1", (), "隐藏人物记录"))
        if "face_assignment_controls" in tables and _exists(connection, "face_assignment_controls", f"{face_filter} AND mode <> 'AUTO_ALLOWED'", (asset_id,)):
            checks.append(("face_assignment_controls", "1=1", (), "人工人脸控制"))
        if "face_exclusions" in tables and _exists(connection, "face_exclusions", f"{face_filter} AND active = 1", (asset_id,)):
            checks.append(("face_exclusions", "1=1", (), "人物排除记录"))
        if "face_inbox_hidden" in tables and _exists(connection, "face_inbox_hidden", face_filter, (asset_id,)):
            checks.append(("face_inbox_hidden", "1=1", (), "忽略的人脸记录"))
        checks.append(("faces", "asset_id = ?", (asset_id,), "人脸识别记录"))

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
    if "artifacts" in tables:
        # Even RECLAIMED rows may still be referenced by an index or recovery path.
        checks.append(("artifacts", "job_id IN (SELECT job_id FROM jobs WHERE "
                       "(subject_type='asset' AND subject_id=?) OR "
                       "(subject_type='face' AND subject_id IN (SELECT face_id FROM faces WHERE asset_id=?)) OR "
                       "(subject_type='location' AND subject_id IN (SELECT CAST(location_id AS TEXT) FROM file_locations WHERE asset_id=?)))",
                       (str(asset_id), asset_id, asset_id), "处理产物引用"))
    for table, where, args, label in checks:
        if where == "1=1" or _exists(connection, table, where, args):
            return True, [label]

    protected_tables = (
        ("index_outbox", "asset_id = ?", (asset_id,), "索引待办/历史引用"),
        ("index_entries", "asset_id = ?", (asset_id,), "索引世代引用"),
        ("upgrade_targets", "asset_id = ?", (asset_id,), "升级目标引用"),
        ("face_index_outbox", "face_id IN (SELECT face_id FROM faces WHERE asset_id = ?)", (asset_id,), "人脸索引待办"),
        ("face_index_entries", "face_id IN (SELECT face_id FROM faces WHERE asset_id = ?)", (asset_id,), "人脸索引世代引用"),
        ("face_candidates", "face_id IN (SELECT face_id FROM faces WHERE asset_id = ?)", (asset_id,), "人脸候选索引引用"),
        ("face_association_queue", "asset_id = ?", (asset_id,), "人脸关联任务"),
        ("face_detail_association_queue", "asset_id = ?", (asset_id,), "人脸详情关联任务"),
        ("face_detail_auto_assets", "asset_id = ?", (asset_id,), "人脸详情处理引用"),
        ("face_detail_batch_assets", "asset_id = ?", (asset_id,), "人脸详情批次引用"),
        ("asset_geo_facts", "asset_id = ?", (asset_id,), "GPS 事实引用"),
        ("asset_ocr_text", "asset_id = ?", (asset_id,), "OCR 文本引用"),
        ("asset_ocr_regions", "asset_id = ?", (asset_id,), "OCR 区域引用"),
        ("asset_place_exclusions", "asset_id = ?", (asset_id,), "地点排除记录"),
        ("cn_admin_resolutions", "asset_id = ?", (asset_id,), "地点解析记录"),
        ("place_resolutions", "asset_id = ?", (asset_id,), "地点候选引用"),
        ("place_operation_items", "asset_id = ?", (asset_id,), "地点操作历史"),
        ("face_redetection_groups", "asset_id = ?", (asset_id,), "人脸重检映射引用"),
        ("face_redetection_mappings", "asset_id = ?", (asset_id,), "人脸重检映射历史"),
        ("asset_metadata", "asset_id = ?", (asset_id,), "元数据事实引用"),
        ("search_projection_queue", "asset_id = ?", (asset_id,), "搜索投影待办"),
        ("ocr_documents", "asset_id = ?", (asset_id,), "OCR 记录"),
    )
    for table, where, args, label in protected_tables:
        if table in tables and _exists(connection, table, where, args):
            return True, [label]
    return False, []


def _classification(
    connection: sqlite3.Connection,
    tables: set[str],
    row: sqlite3.Row,
    unknown_schema: bool,
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
        "retention_eligibility": "不具备清理资格（观察期起点未知）" if not present else "受有效副本保护",
    }


def library_retention_preview(catalog: Catalog, *, after_asset_id: int = 0, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Return aggregate counts and one keyset page. All catalog work is read-only."""
    with catalog.read_snapshot() as connection:
        return _preview_snapshot(connection, catalog.db_path, after_asset_id=after_asset_id, limit=limit)


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
    items = [_classification(connection, tables, row, unknown_schema) for row in page_rows]
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
    return {
        "counts": {key: int(counts[key] or 0) for key in (
            "missing_locations", "assets_without_present", "zero_location_assets", "assets_with_present_copy"
        )},
        "items": items,
        "limit": bounded_limit,
        "next_cursor": next_cursor,
        "observation_period_days": 30,
        "retention_time_evidence": "首次确认 MISSING 时间在现有数据中不可可靠推导；历史记录均为时间未知，不具备清理资格。",
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
