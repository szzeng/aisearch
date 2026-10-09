"""Manual, conservative retention for expired person-operation undo snapshots."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from photo_search.persons import operations as operation_module
from photo_search.persons import read as read_module

RETENTION_DAYS = 30
RECENT_OPERATION_COUNT = 200
MAX_BATCH_SIZE = 100
_DAY_MS = 24 * 60 * 60 * 1000


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _candidate_rows(
    connection: sqlite3.Connection,
    *,
    now_ms: int,
    after_operation_revision: int | None = None,
    limit: int | None = None,
) -> list[Any]:
    cutoff = int(now_ms) - RETENTION_DAYS * _DAY_MS
    sql = (
        "SELECT o.operation_id, o.request_id, o.payload_sha256, o.type, "
        "o.source_person_ids_json, o.target_person_id, o.state, o.operation_revision, "
        "o.undo_of_operation_id, o.undone_by, o.catalog_filter_revision, "
        "o.created_at, o.committed_at "
        "FROM person_operations AS o "
        "LEFT JOIN person_operation_retention AS r USING(operation_id) "
        "WHERE r.operation_id IS NULL AND o.committed_at < ? "
        "AND o.operation_revision NOT IN (SELECT operation_revision FROM person_operations "
        "ORDER BY operation_revision DESC LIMIT ?) "
    )
    params: list[Any] = [cutoff, RECENT_OPERATION_COUNT]
    if after_operation_revision is not None:
        sql += "AND o.operation_revision > ? "
        params.append(int(after_operation_revision))
    sql += "ORDER BY o.operation_revision ASC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(max(1, int(limit)))
    return list(connection.execute(sql, tuple(params)).fetchall())


def _receipt_for(
    connection: sqlite3.Connection, operation: Any
) -> tuple[dict[str, Any], list[dict[str, Any]], int, int]:
    operation_id = str(operation["operation_id"])
    if _unsupported_reason(connection, operation) is not None:
        raise ValueError(_unsupported_reason(connection, operation))
    rows = operation_module._operation_items(connection, operation_id)
    if not rows:
        raise ValueError("no_replaceable_snapshots")
    summary = read_module.operation_summary(connection, operation, rows)
    response = operation_module.operation_result(connection, operation)
    replay_items = [dict(item) for item in response["items"]]
    snapshot_bytes = sum(
        len(str(row["before_value_json"]).encode("utf-8"))
        + len(str(row["after_value_json"]).encode("utf-8"))
        for row in rows
    )
    receipt_bytes = len(_json_bytes(_compact_summary(summary))) + len(_json_bytes(replay_items))
    return summary, replay_items, snapshot_bytes, receipt_bytes


def _unsupported_reason(connection: sqlite3.Connection, operation: Any) -> str | None:
    operation_type = str(operation["type"])
    if operation_type in {"SPLIT", "FACE_MAPPING_CONFIRM", "FACE_MAPPING_REJECT"}:
        return "operation_family_not_enabled_for_cleanup"
    if operation_type == "UNDO" and operation["undo_of_operation_id"] is not None:
        parent = connection.execute(
            "SELECT type FROM person_operations WHERE operation_id = ?",
            (str(operation["undo_of_operation_id"]),),
        ).fetchone()
        if parent is not None and str(parent["type"]) in {
            "SPLIT",
            "FACE_MAPPING_CONFIRM",
            "FACE_MAPPING_REJECT",
        }:
            return "related_operation_family_not_enabled_for_cleanup"
    return None


def _expired_projection(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {**dict(summary), "expired": True}


def _compact_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Persist only the history fields absent from the immutable operation header."""
    return {
        "c": bool(summary["changed"]),
        "n": int(summary["item_count"]),
        "p": list(summary["person_ids"]),
        "f": list(summary["face_ids"]),
    }


def replay_receipt(connection: sqlite3.Connection, operation_id: str) -> list[dict[str, Any]] | None:
    row = connection.execute(
        "SELECT replay_json FROM person_operation_retention WHERE operation_id = ?",
        (str(operation_id),),
    ).fetchone()
    if row is None:
        return None
    value = json.loads(str(row["replay_json"]))
    return [dict(item) for item in value]


def history_projection(
    connection: sqlite3.Connection, operation_id: str, operation: Any | None = None
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT summary_json FROM person_operation_retention WHERE operation_id = ?",
        (str(operation_id),),
    ).fetchone()
    if row is None:
        return None
    if operation is None:
        operation = connection.execute(
            "SELECT operation_id, type, operation_revision, state, undo_of_operation_id, undone_by, "
            "target_person_id, catalog_filter_revision, created_at, committed_at "
            "FROM person_operations WHERE operation_id = ?",
            (str(operation_id),),
        ).fetchone()
    compact = json.loads(str(row["summary_json"]))
    summary = {
        "operation_id": str(operation["operation_id"]),
        "operation_type": str(operation["type"]),
        "operation_revision": int(operation["operation_revision"]),
        "status": "UNDONE" if str(operation["state"]) == "UNDONE" else "COMMITTED",
        "undo_of_operation_id": (
            None if operation["undo_of_operation_id"] is None else str(operation["undo_of_operation_id"])
        ),
        "undone_by_operation_id": (
            None if operation["undone_by"] is None else str(operation["undone_by"])
        ),
        "target_person_id": (
            None if operation["target_person_id"] is None else int(operation["target_person_id"])
        ),
        "changed": bool(compact["c"]),
        "item_count": int(compact["n"]),
        "person_ids": [int(value) for value in compact["p"]],
        "face_ids": [str(value) for value in compact["f"]],
        "catalog_filter_revision": int(operation["catalog_filter_revision"]),
        "created_at": int(operation["created_at"]),
        "committed_at": int(operation["committed_at"]),
    }
    return _expired_projection(summary)


def is_expired(connection: sqlite3.Connection, operation_id: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM person_operation_retention WHERE operation_id = ?",
        (str(operation_id),),
    ).fetchone() is not None


def _skip_reason(error: Exception) -> str:
    reason = str(error).split(":", maxsplit=1)[0]
    if reason in {
        "no_net_space_saving",
        "no_replaceable_snapshots",
        "operation_family_not_enabled_for_cleanup",
        "related_operation_family_not_enabled_for_cleanup",
    }:
        return reason
    return "replay_receipt_unavailable"


def mapping_asset_id(connection: sqlite3.Connection, operation_id: str) -> int | None:
    projection = history_projection(connection, operation_id)
    if projection is None:
        return None
    face_ids = projection.get("face_ids") or []
    if not face_ids:
        return None
    row = connection.execute(
        "SELECT MIN(asset_id) FROM faces WHERE face_id IN ("
        + ",".join("?" for _ in face_ids)
        + ")",
        tuple(str(face_id) for face_id in face_ids),
    ).fetchone()
    return None if row is None or row[0] is None else int(row[0])


def preview(connection: sqlite3.Connection, *, now_ms: int) -> dict[str, Any]:
    candidates = _candidate_rows(connection, now_ms=int(now_ms))
    safe_count = skipped_count = item_count = snapshot_bytes = receipt_bytes = 0
    skipped: dict[str, int] = {}
    for operation in candidates:
        try:
            _, _, old_bytes, new_bytes = _receipt_for(connection, operation)
            if new_bytes >= old_bytes:
                raise ValueError(f"no_net_space_saving:{old_bytes}:{new_bytes}")
            safe_count += 1
            item_count += len(operation_module._operation_items(connection, operation["operation_id"]))
            snapshot_bytes += old_bytes
            receipt_bytes += new_bytes
        except sqlite3.Error:
            raise
        except Exception as error:
            skipped_count += 1
            reason = _skip_reason(error)
            skipped[reason] = skipped.get(reason, 0) + 1
    return {
        "candidate_operation_count": len(candidates),
        "safe_operation_count": safe_count,
        "skipped_operation_count": skipped_count,
        "skipped_reasons": skipped,
        "item_count": item_count,
        "snapshot_json_bytes": snapshot_bytes,
        "receipt_json_bytes": receipt_bytes,
        "estimated_net_savings_bytes": max(0, snapshot_bytes - receipt_bytes),
        "retention_days": RETENTION_DAYS,
        "recent_operation_count": RECENT_OPERATION_COUNT,
        "max_batch_size": MAX_BATCH_SIZE,
    }


def cleanup_batch(
    connection: sqlite3.Connection,
    *,
    now_ms: int,
    after_operation_revision: int | None = None,
    limit: int = MAX_BATCH_SIZE,
) -> dict[str, Any]:
    """Clean one atomic batch; the caller owns the surrounding BEGIN IMMEDIATE."""
    if not connection.in_transaction:
        raise RuntimeError("retention cleanup requires the catalog write transaction")
    batch_limit = min(MAX_BATCH_SIZE, max(1, int(limit)))
    candidates_to_check = _candidate_rows(
        connection,
        now_ms=int(now_ms),
        after_operation_revision=after_operation_revision,
        limit=batch_limit,
    )
    candidates: list[tuple[Any, dict[str, Any], list[dict[str, Any]], int, int, int]] = []
    skipped = 0
    for operation in candidates_to_check:
        if _unsupported_reason(connection, operation) is not None:
            skipped += 1
            continue
        operation_id = str(operation["operation_id"])
        try:
            # Re-read the authoritative item set and verify the receipt before any deletion.
            summary, replay_items, old_bytes, new_bytes = _receipt_for(connection, operation)
            if new_bytes >= old_bytes:
                skipped += 1
                continue
            item_count = len(operation_module._operation_items(connection, operation_id))
            candidates.append((operation, summary, replay_items, old_bytes, new_bytes, item_count))
        except sqlite3.Error:
            raise
        except Exception:
            skipped += 1
    cleaned = items_deleted = 0
    for operation, summary, replay_items, old_bytes, new_bytes, item_count in candidates:
        operation_id = str(operation["operation_id"])
        try:
            connection.execute(
                "INSERT INTO person_operation_retention(operation_id, expired_at, summary_json, "
                "replay_json, item_count, snapshot_bytes, receipt_bytes) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    operation_id,
                    int(now_ms),
                    _json_bytes(_compact_summary(summary)).decode("utf-8"),
                    _json_bytes(replay_items).decode("utf-8"),
                    item_count,
                    old_bytes,
                    new_bytes,
                ),
            )
            deleted = connection.execute(
                "DELETE FROM person_operation_items WHERE operation_id = ?", (operation_id,)
            )
            if int(deleted.rowcount) <= 0:
                raise RuntimeError("snapshot deletion did not remove any items")
            cleaned += 1
            items_deleted += int(deleted.rowcount)
        except sqlite3.Error:
            raise
    last_revision = (
        int(candidates_to_check[-1]["operation_revision"])
        if candidates_to_check
        else after_operation_revision
    )
    has_more = False
    if last_revision is not None and candidates_to_check:
        has_more = bool(
            _candidate_rows(
                connection,
                now_ms=int(now_ms),
                after_operation_revision=last_revision,
                limit=1,
            )
        )
    return {
        "cleaned_operations": cleaned,
        "skipped_operations": skipped,
        "deleted_items": items_deleted,
        "has_more": has_more,
        "next_after_operation_revision": last_revision if has_more else None,
    }


__all__ = [
    "MAX_BATCH_SIZE",
    "RECENT_OPERATION_COUNT",
    "RETENTION_DAYS",
    "cleanup_batch",
    "history_projection",
    "is_expired",
    "mapping_asset_id",
    "preview",
    "replay_receipt",
]
