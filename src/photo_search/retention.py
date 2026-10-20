"""Persistent evidence for conservative missing-asset retention eligibility.

This module records facts from complete successful source scans. It does not delete catalog
rows or authorize a cleanup by itself.
"""

from __future__ import annotations

import secrets
import sqlite3
from typing import Any

from photo_search.catalog.errors import (
    ASSET_NOT_FOUND,
    ASSET_RETENTION_BLOCKED,
    CatalogError,
)

OBSERVATION_MS = 30 * 24 * 60 * 60 * 1000


def asset_retention_barrier(
    connection: sqlite3.Connection, *, asset_id: int
) -> sqlite3.Row | None:
    """Return the durable tombstone for one asset, whether active or complete."""
    return connection.execute(
        "SELECT barrier_id, state FROM asset_retention_barriers WHERE asset_id=?",
        (int(asset_id),),
    ).fetchone()


def assert_asset_retention_unblocked(
    connection: sqlite3.Connection, *, asset_id: int
) -> None:
    """Refuse any derived publication while the asset has a persistent tombstone."""
    barrier = asset_retention_barrier(connection, asset_id=int(asset_id))
    if barrier is not None:
        raise CatalogError(
            ASSET_RETENTION_BLOCKED,
            "the asset is behind a persistent retention cleanup barrier",
            details={
                "asset_id": int(asset_id),
                "barrier_id": str(barrier["barrier_id"]),
                "barrier_state": str(barrier["state"]),
            },
        )


def asset_id_for_retention_subject(
    connection: sqlite3.Connection, *, subject_type: str, subject_id: str
) -> int | None:
    """Resolve asset, face, or location jobs to their owning asset, if catalog-backed."""
    kind = str(subject_type)
    value = str(subject_id)
    if kind == "asset":
        try:
            asset_id = int(value)
        except (TypeError, ValueError):
            return None
        return asset_id if asset_id > 0 and str(asset_id) == value else None
    if kind == "face":
        row = connection.execute("SELECT asset_id FROM faces WHERE face_id=?", (value,)).fetchone()
    elif kind == "location":
        if not value.isdecimal() or str(int(value)) != value:
            return None
        row = connection.execute(
            "SELECT asset_id FROM file_locations WHERE location_id=?", (int(value),)
        ).fetchone()
    else:
        return None
    return int(row["asset_id"]) if row is not None and row["asset_id"] is not None else None


def assert_subject_retention_unblocked(
    connection: sqlite3.Connection, *, subject_type: str, subject_id: str
) -> None:
    asset_id = asset_id_for_retention_subject(
        connection, subject_type=subject_type, subject_id=subject_id
    )
    if asset_id is not None:
        assert_asset_retention_unblocked(connection, asset_id=asset_id)


def activate_asset_retention_barrier(
    connection: sqlite3.Connection, *, asset_id: int, now_ms: int
) -> str:
    """Persist an idempotent tombstone and cancel queued/running asset jobs.

    Call inside the transaction that has just revalidated cleanup eligibility. The barrier
    intentionally has no foreign key to ``assets`` so the row remains after asset deletion.
    """
    asset_id = int(asset_id)
    existing = asset_retention_barrier(connection, asset_id=asset_id)
    if existing is not None:
        return str(existing["barrier_id"])
    if connection.execute(
        "SELECT 1 FROM assets WHERE asset_id=?", (asset_id,)
    ).fetchone() is None:
        raise CatalogError(
            ASSET_NOT_FOUND,
            f"asset {asset_id} does not exist for retention cleanup",
            details={"asset_id": asset_id},
        )
    barrier_id = secrets.token_hex(16)
    connection.execute(
        "INSERT INTO asset_retention_barriers(asset_id,barrier_id,state,created_at_ms,updated_at_ms) "
        "VALUES(?,?,'ACTIVE',?,?)",
        (asset_id, barrier_id, int(now_ms), int(now_ms)),
    )
    connection.execute(
        "UPDATE jobs SET state='CANCELLED',lease_token=NULL,lease_expires_at=NULL,worker_id=NULL,"
        "last_error_code=?,last_error_message=?,updated_at=? "
        "WHERE ((subject_type='asset' AND subject_id=?) "
        "OR (subject_type='face' AND subject_id IN "
        "(SELECT face_id FROM faces WHERE asset_id=?)) "
        "OR (subject_type='location' AND subject_id IN "
        "(SELECT CAST(location_id AS TEXT) FROM file_locations WHERE asset_id=?))) "
        "AND state IN ('QUEUED','RETRY_WAIT','BLOCKED','RUNNING')",
        (
            ASSET_RETENTION_BLOCKED,
            "cancelled by persistent retention cleanup barrier",
            int(now_ms),
            str(asset_id),
            asset_id,
            asset_id,
        ),
    )
    return barrier_id


def activate_asset_retention_cleanup(
    connection: sqlite3.Connection, *, asset_id: int, now_ms: int
) -> tuple[str, list[str]]:
    """Atomically establish the tombstone and enqueue every known vector deletion.

    The caller must perform the current eligibility and human-reference checks in this same
    transaction before calling. Keeping barrier creation and DELETE_VECTOR production together
    makes a crash roll both back, while a repeated call reuses the tombstone and delete events.
    """
    barrier_id = activate_asset_retention_barrier(
        connection, asset_id=int(asset_id), now_ms=int(now_ms)
    )
    # Imported lazily to avoid coupling the retention evidence helpers to the index module at
    # import time. The same operation is used by the cleanup request path and its recovery.
    from photo_search.indexing.store import IndexCoordinator

    event_ids = IndexCoordinator.schedule_retention_vector_deletes(
        connection, asset_id=int(asset_id), now_ms=int(now_ms)
    )
    return barrier_id, event_ids


def complete_asset_retention_barrier(
    connection: sqlite3.Connection, *, asset_id: int, now_ms: int
) -> bool:
    """Mark cleanup complete while retaining the tombstone against later stale replays."""
    cursor = connection.execute(
        "UPDATE asset_retention_barriers SET state='COMPLETE',updated_at_ms=? "
        "WHERE asset_id=? AND state='ACTIVE'",
        (int(now_ms), int(asset_id)),
    )
    return bool(cursor.rowcount)


def scan_missing_counts(
    connection: sqlite3.Connection, *, source_id: int, scan_id: int
) -> tuple[int, int]:
    """Return the pre-reconciliation unique PRESENT baseline and newly missing assets."""
    row = connection.execute(
        """
        SELECT
          COUNT(DISTINCT CASE WHEN availability='PRESENT' THEN asset_id END) AS baseline,
          COUNT(DISTINCT CASE WHEN availability='PRESENT'
                                AND (last_seen_scan_id IS NULL OR last_seen_scan_id <> ?)
                             THEN asset_id END) AS newly_missing
        FROM file_locations
        WHERE source_id=? AND asset_id IS NOT NULL
        """,
        (int(scan_id), int(source_id)),
    ).fetchone()
    return int(row["baseline"] or 0), int(row["newly_missing"] or 0)


def is_bulk_missing_anomaly(*, baseline_present_assets: int, newly_missing_assets: int) -> bool:
    baseline = max(0, int(baseline_present_assets))
    missing = max(0, int(newly_missing_assets))
    return (missing >= 100 and missing / max(1, baseline) > 0.30) or (
        missing >= 10 and missing / max(1, baseline) > 0.80
    )


def record_successful_source_scan(
    connection: sqlite3.Connection,
    *,
    source_id: int,
    scan_id: int,
    now_ms: int,
    baseline_present_assets: int,
    newly_missing_assets: int,
) -> dict[str, Any]:
    """Record one successful scan's missing proof and refresh affected eligibility.

    Call in the same final transaction that marks ``scan_runs`` and ``source_roots`` successful.
    All work is set based; no per-asset transaction or filesystem access occurs here.
    """
    source_id = int(source_id)
    scan_id = int(scan_id)
    now_ms = int(now_ms)
    baseline = max(0, int(baseline_present_assets))
    missing = max(0, int(newly_missing_assets))
    anomaly = is_bulk_missing_anomaly(
        baseline_present_assets=baseline, newly_missing_assets=missing
    )

    if anomaly:
        connection.execute(
            "INSERT INTO retention_source_anomalies(source_id, scan_id, baseline_present_assets, "
            "newly_missing_assets, state, created_at_ms) VALUES (?, ?, ?, ?, 'NEEDS_REVIEW', ?) "
            "ON CONFLICT(source_id, scan_id) DO NOTHING",
            (source_id, scan_id, baseline, missing, now_ms),
        )
        connection.execute(
            "DELETE FROM asset_retention_source_evidence WHERE asset_id IN "
            "(SELECT asset_id FROM file_locations WHERE source_id=? AND availability='MISSING')",
            (source_id,),
        )
        connection.execute(
            "INSERT INTO asset_retention_state(asset_id, first_missing_at_ms, last_qualified_at_ms, state, updated_at_ms) "
            "SELECT DISTINCT asset_id, NULL, NULL, 'BLOCKED', ? FROM file_locations "
            "WHERE source_id=? AND availability='MISSING' AND asset_id IS NOT NULL "
            "ON CONFLICT(asset_id) DO UPDATE SET state='BLOCKED', last_qualified_at_ms=NULL, updated_at_ms=excluded.updated_at_ms",
            (now_ms, source_id),
        )
        return {"anomaly": True, "baseline_present_assets": baseline, "newly_missing_assets": missing}

    # If a location was rebound or removed, old per-source evidence no longer describes the
    # asset's current set of locations. Drop that evidence and restart any remaining clock.
    connection.execute(
        "UPDATE asset_retention_state SET first_missing_at_ms=NULL, last_qualified_at_ms=NULL, "
        "state='BLOCKED', updated_at_ms=? WHERE asset_id IN ("
        "SELECT e.asset_id FROM asset_retention_source_evidence e WHERE e.source_id=? "
        "AND NOT EXISTS (SELECT 1 FROM file_locations l WHERE l.asset_id=e.asset_id "
        "AND l.source_id=e.source_id AND l.availability='MISSING'))",
        (now_ms, source_id),
    )
    connection.execute(
        "DELETE FROM asset_retention_source_evidence WHERE source_id=? AND NOT EXISTS "
        "(SELECT 1 FROM file_locations l WHERE l.asset_id=asset_retention_source_evidence.asset_id "
        "AND l.source_id=asset_retention_source_evidence.source_id AND l.availability='MISSING')",
        (source_id,),
    )

    # A restored copy anywhere cancels all prior evidence and the observation clock.
    connection.execute(
        "DELETE FROM asset_retention_source_evidence WHERE asset_id IN "
        "(SELECT asset_id FROM file_locations WHERE asset_id IS NOT NULL AND availability='PRESENT')"
    )
    connection.execute(
        "DELETE FROM asset_retention_state WHERE asset_id IN "
        "(SELECT asset_id FROM file_locations WHERE asset_id IS NOT NULL AND availability='PRESENT')"
    )

    # Successful complete scans confirm old MISSING rows again, even when no location changed
    # state during this run. The unresolved anomaly check is intentionally separate from the
    # location's availability flag.
    connection.execute(
        """
        INSERT INTO asset_retention_source_evidence(asset_id, source_id, scan_id, missing_confirmed_at_ms)
        SELECT DISTINCT l.asset_id, ?, ?, ?
          FROM file_locations AS l
         WHERE l.source_id=? AND l.availability='MISSING' AND l.asset_id IS NOT NULL
           AND NOT EXISTS (
               SELECT 1 FROM file_locations p
                WHERE p.asset_id=l.asset_id AND p.availability='PRESENT'
           )
           AND NOT EXISTS (
               SELECT 1 FROM retention_source_anomalies x
                WHERE x.source_id=l.source_id AND x.state='NEEDS_REVIEW'
           )
        ON CONFLICT(asset_id, source_id) DO UPDATE SET
            scan_id=excluded.scan_id,
            missing_confirmed_at_ms=excluded.missing_confirmed_at_ms
        """,
        (source_id, scan_id, now_ms, source_id),
    )

    # A complete observation requires fresh successful evidence from every currently registered
    # source with a location for the asset, all sources ONLINE, and no unresolved source hold.
    connection.execute(
        """
        INSERT INTO asset_retention_state(asset_id, first_missing_at_ms, last_qualified_at_ms, state, updated_at_ms)
        SELECT e.asset_id, MAX(e.missing_confirmed_at_ms), NULL, 'OBSERVING', ?
          FROM asset_retention_source_evidence e
          JOIN assets a ON a.asset_id=e.asset_id
         WHERE NOT EXISTS (
             SELECT 1 FROM file_locations p
              WHERE p.asset_id=e.asset_id AND p.availability='PRESENT'
         )
           AND (SELECT COUNT(DISTINCT l.source_id) FROM file_locations l WHERE l.asset_id=e.asset_id) =
               (SELECT COUNT(DISTINCT ev.source_id) FROM asset_retention_source_evidence ev WHERE ev.asset_id=e.asset_id)
           AND NOT EXISTS (
               SELECT 1 FROM file_locations l JOIN source_roots s USING(source_id)
                WHERE l.asset_id=e.asset_id AND s.status <> 'ONLINE'
           )
           AND NOT EXISTS (
               SELECT 1 FROM file_locations l JOIN retention_source_anomalies x USING(source_id)
                WHERE l.asset_id=e.asset_id AND x.state='NEEDS_REVIEW'
           )
         GROUP BY e.asset_id
        ON CONFLICT(asset_id) DO UPDATE SET
            first_missing_at_ms=CASE
                WHEN asset_retention_state.state IN ('UNKNOWN','BLOCKED')
                    OR asset_retention_state.first_missing_at_ms IS NULL
                THEN excluded.first_missing_at_ms
                ELSE asset_retention_state.first_missing_at_ms END,
            state='OBSERVING', last_qualified_at_ms=NULL, updated_at_ms=excluded.updated_at_ms
        """,
        (now_ms,),
    )

    # The 30-day period is a minimum hold. ELIGIBLE additionally requires that every related
    # source completed a successful scan after expiry; updating evidence above makes this a
    # per-source minimum, not a max timestamp shortcut.
    connection.execute(
        """
        UPDATE asset_retention_state AS r
           SET state='ELIGIBLE', last_qualified_at_ms=(
                   ?
               ), updated_at_ms=?
         WHERE r.state='OBSERVING' AND r.first_missing_at_ms IS NOT NULL
           AND NOT EXISTS (
               SELECT 1 FROM file_locations p WHERE p.asset_id=r.asset_id AND p.availability='PRESENT'
           )
           AND NOT EXISTS (
               SELECT 1 FROM file_locations l JOIN source_roots s USING(source_id)
                WHERE l.asset_id=r.asset_id AND s.status <> 'ONLINE'
           )
           AND NOT EXISTS (
               SELECT 1 FROM file_locations l JOIN retention_source_anomalies x USING(source_id)
                WHERE l.asset_id=r.asset_id AND x.state='NEEDS_REVIEW'
           )
           AND (SELECT COUNT(DISTINCT l.source_id) FROM file_locations l WHERE l.asset_id=r.asset_id) =
               (SELECT COUNT(DISTINCT e.source_id) FROM asset_retention_source_evidence e WHERE e.asset_id=r.asset_id)
           AND (SELECT MIN(e.missing_confirmed_at_ms) FROM asset_retention_source_evidence e WHERE e.asset_id=r.asset_id)
               >= r.first_missing_at_ms + ?
        """,
        (now_ms, now_ms, OBSERVATION_MS),
    )
    return {"anomaly": False, "baseline_present_assets": baseline, "newly_missing_assets": missing}


def resolve_source_anomaly(
    connection: sqlite3.Connection, *, source_id: int, scan_id: int, now_ms: int
) -> bool:
    """Acknowledge a reviewed batch; a new successful scan is still needed for eligibility."""
    cursor = connection.execute(
        "UPDATE retention_source_anomalies SET state='CLEARED', resolved_at_ms=? "
        "WHERE source_id=? AND scan_id=? AND state='NEEDS_REVIEW'",
        (int(now_ms), int(source_id), int(scan_id)),
    )
    if cursor.rowcount:
        connection.execute(
            "DELETE FROM asset_retention_source_evidence WHERE asset_id IN "
            "(SELECT asset_id FROM file_locations WHERE source_id=? AND availability='MISSING')",
            (int(source_id),),
        )
        connection.execute(
            "UPDATE asset_retention_state SET state='BLOCKED', first_missing_at_ms=NULL, "
            "last_qualified_at_ms=NULL, updated_at_ms=? WHERE asset_id IN "
            "(SELECT asset_id FROM file_locations WHERE source_id=? AND availability='MISSING')",
            (int(now_ms), int(source_id)),
        )
        return True
    return False


def invalidate_source_retention_evidence(
    connection: sqlite3.Connection, *, source_id: int, now_ms: int
) -> int:
    """Reset affected observations when a source scan does not finish successfully."""
    source_id = int(source_id)
    affected = int(connection.execute(
        "SELECT COUNT(DISTINCT asset_id) FROM file_locations "
        "WHERE source_id=? AND asset_id IS NOT NULL",
        (source_id,),
    ).fetchone()[0])
    connection.execute(
        "DELETE FROM asset_retention_source_evidence WHERE asset_id IN "
        "(SELECT asset_id FROM file_locations WHERE source_id=? AND asset_id IS NOT NULL)",
        (source_id,),
    )
    connection.execute(
        "INSERT INTO asset_retention_state(asset_id, first_missing_at_ms, last_qualified_at_ms, state, updated_at_ms) "
        "SELECT DISTINCT asset_id, NULL, NULL, 'BLOCKED', ? FROM file_locations "
        "WHERE source_id=? AND asset_id IS NOT NULL "
        "ON CONFLICT(asset_id) DO UPDATE SET first_missing_at_ms=NULL, last_qualified_at_ms=NULL, "
        "state='BLOCKED', updated_at_ms=excluded.updated_at_ms",
        (int(now_ms), source_id),
    )
    return affected
