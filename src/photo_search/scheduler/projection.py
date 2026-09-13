"""Read-only scheduler/runtime summaries for ``GET /api/v1/capabilities``.

The projection opens the catalog strictly read-only, so a build that cannot use the
database (too new, inconsistent) still reports facts without any write side effect, and a
database that predates the P2-02 migration simply reports "unavailable".
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from photo_search.catalog import schema
from photo_search.catalog.sqlite_io import open_readonly


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return set(schema.business_tables(connection))


def scheduler_summary(db_path: str | Path) -> dict[str, Any] | None:
    """Pause policy and the current capability health, or ``None`` when not yet present."""
    path = Path(db_path)
    if not path.exists():
        return None
    connection = open_readonly(path)
    try:
        tables = _table_names(connection)
        summary: dict[str, Any] = {
            "scheduler": None,
            "runtime": None,
            "qualification_evidence": 0,
        }
        if "scheduler_state" in tables:
            row = connection.execute(
                "SELECT paused, revision, aging_window_ms, updated_at FROM scheduler_state "
                "WHERE state_id = 1"
            ).fetchone()
            if row is not None:
                summary["scheduler"] = {
                    "state": "paused" if int(row["paused"]) else "running",
                    "revision": int(row["revision"]),
                    "aging_window_ms": int(row["aging_window_ms"]),
                    "updated_at": int(row["updated_at"]),
                }
        if "runtime_capabilities" in tables:
            row = connection.execute(
                "SELECT capability_revision, runtime_package_id, runtime_backend, health_status, "
                "health_checked_at, measured_at FROM runtime_capabilities "
                "ORDER BY recorded_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                summary["runtime"] = {
                    "capability_revision": str(row["capability_revision"]),
                    "runtime_package_id": str(row["runtime_package_id"]),
                    "runtime_backend": str(row["runtime_backend"]),
                    "health_status": str(row["health_status"]),
                    "health_checked_at": int(row["health_checked_at"]),
                    "measured_at": int(row["measured_at"]),
                }
        if "runtime_qualification_evidence" in tables:
            summary["qualification_evidence"] = int(
                connection.execute(
                    "SELECT COUNT(*) FROM runtime_qualification_evidence"
                ).fetchone()[0]
            )
        if summary["scheduler"] is None and summary["runtime"] is None:
            return None
        return summary
    finally:
        connection.close()
