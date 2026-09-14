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


def index_summary(db_path: str | Path) -> dict[str, Any] | None:
    """Safe artifact/outbox/index counts; never returns paths, payloads or checksums."""
    path = Path(db_path)
    if not path.exists():
        return None
    connection = open_readonly(path)
    try:
        tables = _table_names(connection)
        required = {"artifacts", "index_outbox", "index_entries", "index_generations"}
        if not required <= tables:
            return None

        def counts(table: str, column: str) -> dict[str, int]:
            return {
                str(row["state"]): int(row["count"])
                for row in connection.execute(
                    f"SELECT {column} AS state, COUNT(*) AS count FROM {table} GROUP BY {column}"
                )
            }

        generations = counts("index_generations", "state")
        active = [
            {
                "generation_id": str(row["generation_id"]),
                "space_id": str(row["space_id"]),
                "state": str(row["state"]),
            }
            for row in connection.execute(
                "SELECT generation_id, space_id, state FROM index_generations "
                "WHERE is_active = 1 ORDER BY generation_id"
            )
        ]
        return {
            "artifacts": counts("artifacts", "lifecycle_state"),
            "outbox": counts("index_outbox", "state"),
            "entries": counts("index_entries", "state"),
            "generations": generations,
            "active_generations": active,
            "semantic_ready": int(
                connection.execute(
                    "SELECT COUNT(*) FROM index_entries WHERE state = 'CONFIRMED'"
                ).fetchone()[0]
            ),
            "pending": int(
                connection.execute(
                    "SELECT COUNT(*) FROM index_outbox WHERE state <> 'CONFIRMED'"
                ).fetchone()[0]
            ),
            "confirmed": int(
                connection.execute(
                    "SELECT COUNT(*) FROM index_outbox WHERE state = 'CONFIRMED'"
                ).fetchone()[0]
            ),
            "pending_repair": int(
                connection.execute(
                    "SELECT COUNT(*) FROM index_entries WHERE state = 'PENDING_REPAIR'"
                ).fetchone()[0]
            ),
        }
    finally:
        connection.close()


def search_projection_summary(db_path: str | Path) -> dict[str, Any] | None:
    """Counts of the derived P2-04 search layer, and the lag a caller can see.

    Read-only and text-free: no search text, no place name and no path is returned - only
    how many documents exist, how many assets are still queued for re-derivation and which
    tokenizer generation wrote them. ``None`` means this catalog predates migration 0006.
    """
    path = Path(db_path)
    if not path.exists():
        return None
    connection = open_readonly(path)
    try:
        tables = _table_names(connection)
        if "search_documents" not in tables or "search_projection_queue" not in tables:
            return None
        row = connection.execute(
            "SELECT COUNT(*) AS documents, "
            "COUNT(DISTINCT tokenizer_version || ':' || fts_generation_id) AS generations "
            "FROM search_documents"
        ).fetchone()
        queued = int(
            connection.execute("SELECT COUNT(*) FROM search_projection_queue").fetchone()[0]
        )
        generation = connection.execute(
            "SELECT tokenizer_version, fts_generation_id FROM search_documents "
            "ORDER BY updated_at DESC, asset_id DESC LIMIT 1"
        ).fetchone()
        return {
            "documents": int(row["documents"]),
            "queued_assets": queued,
            "generations": int(row["generations"]),
            "tokenizer_version": (
                None if generation is None else int(generation["tokenizer_version"])
            ),
            "fts_generation_id": None if generation is None else str(generation["fts_generation_id"]),
            "state": "SYNCED" if queued == 0 else "LAGGING",
        }
    finally:
        connection.close()


def pending_projection_summary(db_path: str | Path) -> dict[str, Any] | None:
    """Read-only view of the P2-03 vector projection: what is confirmed, what is late.

    P2-05 needs a truthful answer to "is the image-text index caught up?" before it can
    claim that a photo is (or is not) in semantic recall. Counts only: no artifact path,
    payload, checksum, vector or asset id leaves this function. ``None`` means the catalog
    predates migration 0003, so the facts do not exist yet.
    """
    path = Path(db_path)
    if not path.exists():
        return None
    connection = open_readonly(path)
    try:
        tables = _table_names(connection)
        required = {"index_outbox", "index_entries", "index_generations"}
        if not required <= tables:
            return None

        def counts(table: str, column: str) -> dict[str, int]:
            return {
                str(row["state"]): int(row["count"])
                for row in connection.execute(
                    f"SELECT {column} AS state, COUNT(*) AS count FROM {table} GROUP BY {column}"
                )
            }

        outbox = counts("index_outbox", "state")
        entries = counts("index_entries", "state")
        generations = counts("index_generations", "state")
        active = [
            {
                "generation_id": str(row["generation_id"]),
                "space_id": str(row["space_id"]),
                "dimension": int(row["dimension"]),
                "processor_id": str(row["processor_id"]),
                "state": str(row["state"]),
            }
            for row in connection.execute(
                "SELECT generation_id, space_id, dimension, processor_id, state "
                "FROM index_generations WHERE is_active = 1 ORDER BY generation_id"
            )
        ]
        pending_events = sum(
            count for state, count in outbox.items() if state != "CONFIRMED"
        )
        return {
            "generations": generations,
            "entries": entries,
            "outbox": outbox,
            "active_generations": active,
            "pending_events": int(pending_events),
            "pending_entries": int(entries.get("PENDING_REPAIR", 0)),
            "confirmed_entries": int(entries.get("CONFIRMED", 0)),
            "state": "SYNCED" if pending_events == 0 else "LAGGING",
        }
    finally:
        connection.close()


def search_profile_summary(db_path: str | Path) -> dict[str, Any] | None:
    """The active search profile of this catalog, read-only (P2-06A, architecture §11.3.5).

    ``GET /api/v1/capabilities`` may report which profile is served without opening a
    write path and without leaking a path or a token; ``active_profile_id`` is ``None``
    both when nothing is activated and when this catalog predates migration 0007, and only
    ``None`` for the second case when the whole block is absent. The pending vector
    projection counter is included so a caller can tell a caught-up index from a lagging
    one before it runs a query.
    """
    path = Path(db_path)
    if not path.exists():
        return None
    connection = open_readonly(path)
    try:
        tables = _table_names(connection)
        if "search_profiles" not in tables or "active_search_profile" not in tables:
            return None
        row = connection.execute(
            "SELECT profile_id FROM active_search_profile WHERE state_id = 1"
        ).fetchone()
        profile = None
        if row is not None and row["profile_id"] is not None:
            profile = connection.execute(
                "SELECT profile_id, space_id, image_generation_id, fts_generation_id, "
                "runtime_package_id FROM search_profiles WHERE profile_id = ?",
                (str(row["profile_id"]),),
            ).fetchone()
        active = []
        if "index_generations" in tables:
            active = [
                {
                    "generation_id": str(item["generation_id"]),
                    "space_id": str(item["space_id"]),
                    "state": str(item["state"]),
                }
                for item in connection.execute(
                    "SELECT generation_id, space_id, state FROM index_generations "
                    "WHERE is_active = 1 ORDER BY generation_id"
                )
            ]
        # The ``active_`` prefix keeps these facts apart from the derived projection's own
        # ``fts_generation_id``: that one is the generation the stored documents were
        # actually written with, while this one is what the served profile declares.
        return {
            "active_profile_id": None if profile is None else str(profile["profile_id"]),
            "active_image_generation_id": (
                None if profile is None else str(profile["image_generation_id"])
            ),
            "active_fts_generation_id": (
                None if profile is None else str(profile["fts_generation_id"])
            ),
            "active_runtime_package_id": (
                None if profile is None else str(profile["runtime_package_id"])
            ),
            "active_generations": active,
            "pending_projection": pending_projection_summary(path),
        }
    finally:
        connection.close()
