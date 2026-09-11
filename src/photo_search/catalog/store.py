"""Catalog queries for sources, scan runs, locations and assets (ADR-0002 §3).

Every function takes the catalog connection and runs inside a caller-owned transaction.
Cross-row semantics the frozen contract cannot express with a plain foreign key (same
source for ``last_seen_scan_id``, ``SUCCEEDED`` for ``last_successful_scan``, scan/job
state linkage) are enforced by the callers in ``photo_search.ingestion`` under the
single-writer transaction.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from photo_search.catalog.errors import (
    ASSET_SIZE_MISMATCH,
    INVALID_STATE_TRANSITION,
    SOURCE_NOT_FOUND,
    CatalogError,
)

SCAN_SUBJECT_TYPE = "scan_run"
SCAN_INPUT_REVISION = "1"
SCAN_TASK_TYPE = "catalog_scan"
SCAN_PROCESSOR_ID = "catalog-scan@1"

SOURCE_FIELDS = (
    "source_id",
    "display_name",
    "root_path",
    "volume_hint",
    "status",
    "last_successful_scan",
)
LOCATION_FIELDS = (
    "location_id",
    "source_id",
    "relative_path",
    "path_key",
    "asset_id",
    "size",
    "mtime_ns",
    "last_seen_scan_id",
    "availability",
)
ASSET_FIELDS = ("asset_id", "sha256", "byte_size", "width", "height", "format", "created_at", "state")
SCAN_FIELDS = ("scan_id", "source_id", "started_at", "finished_at", "state", "error")

#: Legal scan_run transitions. ``RUNNING -> RUNNING`` is the documented recovery path
#: where a new lease takes over an expired one without restarting the logical scan.
ALLOWED_SCAN_TRANSITIONS: dict[str, frozenset[str]] = {
    "QUEUED": frozenset({"RUNNING", "CANCELLED"}),
    "RUNNING": frozenset({"RUNNING", "SUCCEEDED", "BLOCKED", "FAILED", "CANCELLED"}),
    "BLOCKED": frozenset({"QUEUED", "CANCELLED"}),
    "SUCCEEDED": frozenset(),
    "FAILED": frozenset(),
    "CANCELLED": frozenset(),
}


def row_dict(row: sqlite3.Row, fields: tuple[str, ...]) -> dict[str, Any]:
    return {name: row[name] for name in fields}


def insert_source(
    connection: sqlite3.Connection,
    *,
    display_name: str,
    root_path: str,
    volume_hint: str | None,
    status: str,
) -> int:
    cursor = connection.execute(
        "INSERT INTO source_roots (display_name, root_path, volume_hint, status) VALUES (?, ?, ?, ?)",
        (display_name, root_path, volume_hint, status),
    )
    return int(cursor.lastrowid)


def get_source_row(connection: sqlite3.Connection, source_id: int) -> sqlite3.Row:
    row = connection.execute(
        f"SELECT {', '.join(SOURCE_FIELDS)} FROM source_roots WHERE source_id = ?", (source_id,)
    ).fetchone()
    if row is None:
        raise CatalogError(
            SOURCE_NOT_FOUND,
            f"source {source_id} does not exist",
            details={"source_id": source_id},
        )
    return row


def list_source_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            f"SELECT {', '.join(SOURCE_FIELDS)} FROM source_roots ORDER BY source_id"
        ).fetchall()
    )


def set_source_status(connection: sqlite3.Connection, *, source_id: int, status: str) -> None:
    connection.execute("UPDATE source_roots SET status = ? WHERE source_id = ?", (status, source_id))


def set_last_successful_scan(
    connection: sqlite3.Connection,
    *,
    source_id: int,
    scan_id: int,
    status: str = "ONLINE",
) -> None:
    row = connection.execute(
        "SELECT source_id, state FROM scan_runs WHERE scan_id = ?", (scan_id,)
    ).fetchone()
    if row is None or int(row["source_id"]) != int(source_id) or row["state"] != "SUCCEEDED":
        raise CatalogError(
            INVALID_STATE_TRANSITION,
            "last_successful_scan must point at a SUCCEEDED scan of the same source",
            details={"source_id": source_id, "scan_id": scan_id},
        )
    connection.execute(
        "UPDATE source_roots SET last_successful_scan = ?, status = ? WHERE source_id = ?",
        (scan_id, status, source_id),
    )


def find_active_scan(connection: sqlite3.Connection, source_id: int) -> sqlite3.Row | None:
    return connection.execute(
        f"SELECT {', '.join(SCAN_FIELDS)} FROM scan_runs "
        "WHERE source_id = ? AND state IN ('QUEUED', 'RUNNING', 'BLOCKED') "
        "ORDER BY scan_id LIMIT 1",
        (source_id,),
    ).fetchone()


def insert_scan_run(connection: sqlite3.Connection, *, source_id: int, state: str = "QUEUED") -> int:
    cursor = connection.execute(
        "INSERT INTO scan_runs (source_id, state) VALUES (?, ?)", (source_id, state)
    )
    return int(cursor.lastrowid)


def get_scan_row(connection: sqlite3.Connection, scan_id: int) -> sqlite3.Row:
    row = connection.execute(
        f"SELECT {', '.join(SCAN_FIELDS)} FROM scan_runs WHERE scan_id = ?", (scan_id,)
    ).fetchone()
    if row is None:
        raise CatalogError(
            INVALID_STATE_TRANSITION,
            f"scan_run {scan_id} does not exist",
            details={"scan_id": scan_id},
        )
    return row


def set_scan_state(
    connection: sqlite3.Connection,
    *,
    scan_id: int,
    state: str,
    now_ms: int,
    error_text: str | None = None,
) -> sqlite3.Row:
    row = get_scan_row(connection, scan_id)
    current = str(row["state"])
    if state not in ALLOWED_SCAN_TRANSITIONS[current]:
        raise CatalogError(
            INVALID_STATE_TRANSITION,
            f"scan_run {scan_id} cannot move from {current} to {state}",
            details={"scan_id": scan_id, "from": current, "to": state},
        )
    if state == "RUNNING":
        connection.execute(
            "UPDATE scan_runs SET state = ?, started_at = COALESCE(started_at, ?) WHERE scan_id = ?",
            (state, now_ms, scan_id),
        )
    elif state in ("SUCCEEDED", "FAILED", "CANCELLED"):
        connection.execute(
            "UPDATE scan_runs SET state = ?, finished_at = ?, error = ? WHERE scan_id = ?",
            (state, now_ms, error_text, scan_id),
        )
    else:
        connection.execute(
            "UPDATE scan_runs SET state = ?, error = ? WHERE scan_id = ?",
            (state, error_text, scan_id),
        )
    return get_scan_row(connection, scan_id)


def upsert_asset(
    connection: sqlite3.Connection,
    *,
    sha256: str,
    byte_size: int,
    now_ms: int,
) -> int:
    """Return the asset for a full content hash, creating it only for a new hash."""
    row = connection.execute(
        "SELECT asset_id, byte_size FROM assets WHERE sha256 = ?", (sha256,)
    ).fetchone()
    if row is not None:
        if int(row["byte_size"]) != int(byte_size):
            raise CatalogError(
                ASSET_SIZE_MISMATCH,
                "the same sha256 was observed with a different byte_size",
                details={"sha256": sha256, "stored_byte_size": int(row["byte_size"]), "byte_size": byte_size},
            )
        return int(row["asset_id"])
    cursor = connection.execute(
        "INSERT INTO assets (sha256, byte_size, created_at, state) VALUES (?, ?, ?, 'ACTIVE')",
        (sha256, byte_size, now_ms),
    )
    return int(cursor.lastrowid)


def observe_location(
    connection: sqlite3.Connection,
    *,
    source_id: int,
    scan_id: int,
    relative_path: str,
    path_key: str,
    size: int,
    mtime_ns: int,
) -> tuple[int, bool]:
    """Record a positive observation; a changed size/mtime clears the content binding."""
    row = connection.execute(
        "SELECT location_id, size, mtime_ns, asset_id FROM file_locations "
        "WHERE source_id = ? AND path_key = ?",
        (source_id, path_key),
    ).fetchone()
    if row is None:
        cursor = connection.execute(
            "INSERT INTO file_locations "
            "(source_id, relative_path, path_key, asset_id, size, mtime_ns, last_seen_scan_id, availability) "
            "VALUES (?, ?, ?, NULL, ?, ?, ?, 'PRESENT')",
            (source_id, relative_path, path_key, size, mtime_ns, scan_id),
        )
        return int(cursor.lastrowid), True
    location_id = int(row["location_id"])
    changed = int(row["size"]) != int(size) or int(row["mtime_ns"]) != int(mtime_ns)
    if changed:
        connection.execute(
            "UPDATE file_locations SET relative_path = ?, size = ?, mtime_ns = ?, asset_id = NULL, "
            "last_seen_scan_id = ?, availability = 'PRESENT' WHERE location_id = ?",
            (relative_path, size, mtime_ns, scan_id, location_id),
        )
        return location_id, True
    connection.execute(
        "UPDATE file_locations SET relative_path = ?, last_seen_scan_id = ?, availability = 'PRESENT' "
        "WHERE location_id = ?",
        (relative_path, scan_id, location_id),
    )
    return location_id, False


def bind_location_content(
    connection: sqlite3.Connection,
    *,
    location_id: int,
    observed_size: int,
    observed_mtime_ns: int,
    sha256: str,
    byte_size: int,
    now_ms: int,
) -> bool:
    """Bind a confirmed full hash to a location; a stale observation binds nothing."""
    row = connection.execute(
        "SELECT size, mtime_ns FROM file_locations WHERE location_id = ?", (location_id,)
    ).fetchone()
    if row is None:
        raise CatalogError(
            INVALID_STATE_TRANSITION,
            f"location {location_id} no longer exists",
            details={"location_id": location_id},
        )
    if int(row["size"]) != int(observed_size) or int(row["mtime_ns"]) != int(observed_mtime_ns):
        return False
    asset_id = upsert_asset(connection, sha256=sha256, byte_size=byte_size, now_ms=now_ms)
    connection.execute(
        "UPDATE file_locations SET asset_id = ? WHERE location_id = ?", (asset_id, location_id)
    )
    return True


def reconcile_missing(connection: sqlite3.Connection, *, source_id: int, scan_id: int) -> int:
    """Mark locations this complete scan did not see; only the final commit calls this."""
    cursor = connection.execute(
        "UPDATE file_locations SET availability = 'MISSING' "
        "WHERE source_id = ? AND availability <> 'MISSING' "
        "AND (last_seen_scan_id IS NULL OR last_seen_scan_id <> ?)",
        (source_id, scan_id),
    )
    return int(cursor.rowcount or 0)


def scan_job_id(connection: sqlite3.Connection, *, scan_id: int) -> int | None:
    row = connection.execute(
        "SELECT job_id FROM jobs WHERE subject_type = ? AND subject_id = ? AND input_revision = ? "
        "AND task_type = ? AND processor_id = ?",
        (SCAN_SUBJECT_TYPE, str(scan_id), SCAN_INPUT_REVISION, SCAN_TASK_TYPE, SCAN_PROCESSOR_ID),
    ).fetchone()
    return int(row["job_id"]) if row is not None else None


def catalog_counts(connection: sqlite3.Connection, *, source_id: int | None = None) -> dict[str, Any]:
    def scalar(sql: str, params: tuple = ()) -> int:
        return int(connection.execute(sql, params).fetchone()[0])

    assets_total = scalar("SELECT COUNT(*) FROM assets")
    if source_id is None:
        counts: dict[str, Any] = {
            "sources": scalar("SELECT COUNT(*) FROM source_roots"),
            "assets": assets_total,
            "assets_total": assets_total,
            "locations": scalar("SELECT COUNT(*) FROM file_locations"),
            "locations_present": scalar(
                "SELECT COUNT(*) FROM file_locations WHERE availability = 'PRESENT'"
            ),
            "locations_missing": scalar(
                "SELECT COUNT(*) FROM file_locations WHERE availability = 'MISSING'"
            ),
            "locations_pending_content": scalar(
                "SELECT COUNT(*) FROM file_locations WHERE asset_id IS NULL"
            ),
            "scans": scalar("SELECT COUNT(*) FROM scan_runs"),
            "jobs": scalar("SELECT COUNT(*) FROM jobs"),
        }
        return counts
    parameters = (int(source_id),)
    counts = {
        "sources": 1,
        "assets": scalar(
            "SELECT COUNT(DISTINCT asset_id) FROM file_locations "
            "WHERE source_id = ? AND asset_id IS NOT NULL",
            parameters,
        ),
        "assets_total": assets_total,
        "locations": scalar("SELECT COUNT(*) FROM file_locations WHERE source_id = ?", parameters),
        "locations_present": scalar(
            "SELECT COUNT(*) FROM file_locations WHERE source_id = ? AND availability = 'PRESENT'",
            parameters,
        ),
        "locations_missing": scalar(
            "SELECT COUNT(*) FROM file_locations WHERE source_id = ? AND availability = 'MISSING'",
            parameters,
        ),
        "locations_pending_content": scalar(
            "SELECT COUNT(*) FROM file_locations WHERE source_id = ? AND asset_id IS NULL",
            parameters,
        ),
        "scans": scalar("SELECT COUNT(*) FROM scan_runs WHERE source_id = ?", parameters),
        "jobs": scalar(
            "SELECT COUNT(*) FROM jobs WHERE subject_type = ? AND subject_id IN "
            "(SELECT CAST(scan_id AS TEXT) FROM scan_runs WHERE source_id = ?)",
            (SCAN_SUBJECT_TYPE, int(source_id)),
        ),
    }
    return counts
