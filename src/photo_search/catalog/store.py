"""Catalog queries for sources, scan runs, locations and assets (ADR-0002 §3).

Every function takes the catalog connection and runs inside a caller-owned transaction.
Cross-row semantics the frozen contract cannot express with a plain foreign key (same
source for ``last_seen_scan_id``, ``SUCCEEDED`` for ``last_successful_scan``, scan/job
state linkage) are enforced by the callers in ``photo_search.ingestion`` under the
single-writer transaction.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from photo_search.catalog.errors import (
    ASSET_SIZE_MISMATCH,
    INVALID_STATE_TRANSITION,
    PATH_KEY_COLLISION,
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

#: Legal scan_run transitions for the normal state machine (ADR-0002 §3.3/§6.1).
#: A RUNNING scan_run keeps its state while a new lease takes over an expired one; that
#: special recovery is handled in the scanner and is deliberately not a self-loop here.
ALLOWED_SCAN_TRANSITIONS: dict[str, frozenset[str]] = {
    # A job may reach a terminal state before its scan_run ever ran, and ADR-0002 §3.3
    # requires the scan_run to follow the job into the same terminal state in the same
    # business commit, so FAILED is legal from every non-terminal state.
    "QUEUED": frozenset({"RUNNING", "FAILED", "CANCELLED"}),
    # A RUNNING scan_run keeps its state while a new lease takes over the expired one
    # (ADR-0002 §6.1 special recovery); it is not a generic self-loop, so callers never
    # transition RUNNING -> RUNNING through this helper.
    "RUNNING": frozenset({"SUCCEEDED", "BLOCKED", "FAILED", "CANCELLED"}),
    "BLOCKED": frozenset({"QUEUED", "FAILED", "CANCELLED"}),
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


def rename_source(
    connection: sqlite3.Connection, *, source_id: int, display_name: str
) -> sqlite3.Row:
    """Change the display label only; identity, root and scan state stay intact."""
    row = get_source_row(connection, source_id)
    if row["display_name"] != display_name:
        connection.execute(
            "UPDATE source_roots SET display_name = ? WHERE source_id = ?",
            (display_name, source_id),
        )
    return get_source_row(connection, source_id)


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
    """Record a positive observation and return ``(location_id, changed)``."""
    observed = observe_location_detailed(
        connection,
        source_id=source_id,
        scan_id=scan_id,
        relative_path=relative_path,
        path_key=path_key,
        size=size,
        mtime_ns=mtime_ns,
    )
    return observed.location_id, observed.changed


@dataclass(frozen=True)
class LocationObservation:
    """Result of one positive position observation (P1-02 streaming scans).

    ``created``/``changed`` keep the frozen P1-02 meaning: *this* observation either created
    the location or saw a different ``size``/``mtime_ns`` than the stored one. P4-02Y adds
    two purely additive facts about the same observation so a fast refresh can tell a
    rename and a ``MISSING`` -> ``PRESENT`` recovery from an ordinary re-observation:

    * ``renamed`` - the stored ``relative_path`` of this ``path_key`` changed to a new
      spelling while the stored content binding was kept;
    * ``restored`` - the location was ``MISSING`` before this observation.

    Neither flag is a content identity: they only decide whether the fast scan has to
    re-confirm the position's full SHA-256 (ADR-0002 §5.1/§5.2, P4-02Y §2).
    """

    location_id: int
    created: bool
    changed: bool
    renamed: bool = False
    restored: bool = False
    #: The content binding stored on the location *after* this observation: ``None`` for a
    #: created position and for one whose changed stat cleared the binding. Purely additive -
    #: the frozen P1-02 contract only reads ``location_id``/``created``/``changed``.
    asset_id: int | None = None


def observe_location_detailed(
    connection: sqlite3.Connection,
    *,
    source_id: int,
    scan_id: int,
    relative_path: str,
    path_key: str,
    size: int,
    mtime_ns: int,
    now_ms: int | None = None,
) -> LocationObservation:
    """Record a positive observation; a changed size/mtime clears the content binding.

    The streaming scanner cannot hold the whole enumeration in memory, so two distinct
    paths that collapse into one ``path_key`` are detected here instead of by an eager
    in-memory pass: a row already seen by *this* execution under a different display path
    is ``PATH_KEY_COLLISION`` and the scan must not reach ``SUCCEEDED`` (ADR-0002 §4.2).
    """
    scan = connection.execute(
        "SELECT source_id FROM scan_runs WHERE scan_id = ?", (scan_id,)
    ).fetchone()
    if scan is None or int(scan["source_id"]) != int(source_id):
        raise CatalogError(
            INVALID_STATE_TRANSITION,
            "last_seen_scan_id must belong to the same source as the location",
            details={"source_id": source_id, "scan_id": scan_id},
        )
    row = connection.execute(
        "SELECT location_id, relative_path, size, mtime_ns, asset_id, last_seen_scan_id, "
        "availability "
        "FROM file_locations "
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
        return LocationObservation(location_id=int(cursor.lastrowid), created=True, changed=True)
    if (
        str(row["relative_path"]) != relative_path
        and row["last_seen_scan_id"] is not None
        and int(row["last_seen_scan_id"]) == int(scan_id)
    ):
        raise CatalogError(
            PATH_KEY_COLLISION,
            "two different paths in this source produce the same comparison key",
            details={
                "path_key": path_key,
                "first_path": str(row["relative_path"]),
                "second_path": relative_path,
                "source_id": source_id,
                "scan_id": scan_id,
            },
        )
    location_id = int(row["location_id"])
    renamed = str(row["relative_path"]) != relative_path
    restored = str(row["availability"]) == "MISSING"
    kept_asset_id = None if row["asset_id"] is None else int(row["asset_id"])
    changed = int(row["size"]) != int(size) or int(row["mtime_ns"]) != int(mtime_ns)
    if changed:
        connection.execute(
            "UPDATE file_locations SET relative_path = ?, size = ?, mtime_ns = ?, asset_id = NULL, "
            "last_seen_scan_id = ?, availability = 'PRESENT' WHERE location_id = ?",
            (relative_path, size, mtime_ns, scan_id, location_id),
        )
        if kept_asset_id is not None:
            # The changed stat clears this source location's asset binding in the
            # transaction that commits the changed observation. Only the last
            # qualifying image location in this source changes its Timeline EXISTS
            # result; duplicate locations keep the asset visible and are a no-op.
            from photo_search.catalog.image_policy import image_location_sql, is_image_path

            removes_source_visibility = is_image_path(str(row["relative_path"]))
            if removes_source_visibility:
                remaining_image = connection.execute(
                    "SELECT 1 FROM file_locations AS remaining_location "
                    "WHERE remaining_location.asset_id = ? AND remaining_location.source_id = ? "
                    "AND remaining_location.location_id != ? "
                    f"AND {image_location_sql('remaining_location')} LIMIT 1",
                    (kept_asset_id, int(source_id), int(row["location_id"])),
                ).fetchone()
                removes_source_visibility = remaining_image is None
            if removes_source_visibility:
                # Source-scoped Timeline results can change before the later
                # hash-confirmation transaction runs, so publish atomically here.
                from photo_search.places import revision

                revision.bump_filter_revision(
                    connection,
                    cause=revision.CAUSE_ASSET_CONTENT_DETACHED,
                    now_ms=int(now_ms if now_ms is not None else time.time_ns() // 1_000_000),
                    asset_ids=[kept_asset_id],
                )
        return LocationObservation(
            location_id=location_id,
            created=False,
            changed=True,
            renamed=renamed,
            restored=restored,
            asset_id=None,
        )
    connection.execute(
        "UPDATE file_locations SET relative_path = ?, last_seen_scan_id = ?, availability = 'PRESENT' "
        "WHERE location_id = ?",
        (relative_path, scan_id, location_id),
    )
    return LocationObservation(
        location_id=location_id,
        created=False,
        changed=False,
        renamed=renamed,
        restored=restored,
        asset_id=kept_asset_id,
    )


SEEN_LOCATION_FIELDS = ("location_id", "relative_path", "path_key", "size", "mtime_ns")


def count_seen_locations(
    connection: sqlite3.Connection, *, source_id: int, scan_id: int
) -> int:
    """How many positions the current execution has already observed (progress basis)."""
    return int(
        connection.execute(
            "SELECT COUNT(*) FROM file_locations WHERE source_id = ? AND last_seen_scan_id = ?",
            (source_id, scan_id),
        ).fetchone()[0]
    )


def iter_seen_locations(
    connection: sqlite3.Connection,
    *,
    source_id: int,
    scan_id: int,
    chunk_size: int = 256,
) -> Iterator[sqlite3.Row]:
    """Stream the positions of this execution in bounded chunks (P1-02 §1).

    Keyset pagination keeps the memory footprint independent of the library size and
    never leaves a live cursor open while the scanner commits the next short write
    transaction on the same connection.
    """
    size = max(int(chunk_size), 1)
    last_id = 0
    while True:
        rows = connection.execute(
            f"SELECT {', '.join(SEEN_LOCATION_FIELDS)} FROM file_locations "
            "WHERE source_id = ? AND last_seen_scan_id = ? AND location_id > ? "
            "ORDER BY location_id LIMIT ?",
            (source_id, scan_id, last_id, size),
        ).fetchall()
        if not rows:
            return
        yield from rows
        last_id = int(rows[-1]["location_id"])


def iter_unbound_seen_locations(
    connection: sqlite3.Connection,
    *,
    source_id: int,
    scan_id: int,
    also_location_ids: Iterable[int] = (),
    chunk_size: int = 256,
) -> Iterator[sqlite3.Row]:
    """Stream only the positions this execution still has to confirm content for (P4-02Y §3).

    A fast refresh may reuse a content binding that a previous *complete* scan has already
    confirmed with a full SHA-256, so the content pass must not walk every position it saw
    again. Two kinds of position still need the hash pass:

    * a seen position with ``asset_id IS NULL`` - a new one, or one whose observation
      cleared its binding;
    * the positions the enumeration explicitly reported as ``MISSING`` -> ``PRESENT``
      recoveries (``also_location_ids``), whose binding must be re-confirmed even though it
      still points at a previously hashed asset.

    The stream stays bounded exactly like :func:`iter_seen_locations`: keyset pagination on
    ``location_id``, never a materialised list of the library.
    """
    extra = sorted({int(value) for value in also_location_ids})
    size = max(int(chunk_size), 1)
    for start in range(0, len(extra), size):
        batch = extra[start : start + size]
        placeholders = ", ".join("?" for _ in batch)
        rows = connection.execute(
            f"SELECT {', '.join(SEEN_LOCATION_FIELDS)} FROM file_locations "
            f"WHERE source_id = ? AND last_seen_scan_id = ? AND location_id IN ({placeholders}) "
            "ORDER BY location_id",
            (source_id, scan_id, *batch),
        ).fetchall()
        yield from rows
    last_id = 0
    while True:
        rows = connection.execute(
            f"SELECT {', '.join(SEEN_LOCATION_FIELDS)} FROM file_locations "
            "WHERE source_id = ? AND last_seen_scan_id = ? AND asset_id IS NULL AND location_id > ? "
            "ORDER BY location_id LIMIT ?",
            (source_id, scan_id, last_id, size),
        ).fetchall()
        if not rows:
            return
        yield from rows
        last_id = int(rows[-1]["location_id"])


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
        "SELECT source_id, size, mtime_ns, asset_id FROM file_locations WHERE location_id = ?",
        (location_id,),
    ).fetchone()
    if row is None:
        raise CatalogError(
            INVALID_STATE_TRANSITION,
            f"location {location_id} no longer exists",
            details={"location_id": location_id},
        )
    if int(row["size"]) != int(observed_size) or int(row["mtime_ns"]) != int(observed_mtime_ns):
        return False
    existing = connection.execute(
        "SELECT asset_id FROM assets WHERE sha256 = ?", (sha256,)
    ).fetchone()
    previous_asset_id = None if row["asset_id"] is None else int(row["asset_id"])
    adds_visibility = existing is None
    if existing is not None and previous_asset_id != int(existing["asset_id"]):
        # Timeline's global selector keeps zero-location legacy assets but excludes
        # assets whose only registered locations are non-images. A source-scoped
        # selector also needs its first qualifying image location. Check both before
        # binding so duplicate content in an already-visible source remains a no-op.
        from photo_search.catalog.image_policy import image_location_sql

        asset_id_before = int(existing["asset_id"])
        any_location = connection.execute(
            "SELECT 1 FROM file_locations WHERE asset_id = ? LIMIT 1", (asset_id_before,)
        ).fetchone()
        image_predicate = image_location_sql("existing_location")
        globally_visible = any_location is None or connection.execute(
            "SELECT 1 FROM file_locations AS existing_location "
            f"WHERE existing_location.asset_id = ? AND {image_predicate} LIMIT 1",
            (asset_id_before,),
        ).fetchone() is not None
        source_has_image = connection.execute(
            "SELECT 1 FROM file_locations AS existing_location "
            f"WHERE existing_location.asset_id = ? AND existing_location.source_id = ? "
            f"AND {image_predicate} LIMIT 1",
            (asset_id_before, int(row["source_id"])),
        ).fetchone() is not None
        adds_visibility = not globally_visible or not source_has_image
    asset_id = upsert_asset(connection, sha256=sha256, byte_size=byte_size, now_ms=now_ms)
    connection.execute(
        "UPDATE file_locations SET asset_id = ? WHERE location_id = ?", (asset_id, location_id)
    )
    if adds_visibility:
        # The newly confirmed asset becomes visible to Timeline in this same authority
        # transaction, or a changed binding adds it to this source's Timeline. Keep the
        # shared cursor revision atomic with that visibility change.
        from photo_search.places import revision

        revision.bump_filter_revision(
            connection,
            cause=(
                revision.CAUSE_ASSET_ADDED
                if existing is None
                else revision.CAUSE_ASSET_LOCATION_BOUND
            ),
            now_ms=int(now_ms),
            asset_ids=(
                [asset_id]
                if previous_asset_id is None
                else sorted({previous_asset_id, asset_id})
            ),
        )
    return True


def reconcile_missing(connection: sqlite3.Connection, *, source_id: int, scan_id: int) -> int:
    """Mark locations this complete scan did not see; only the final commit calls this."""
    return int(
        reconcile_missing_detail(connection, source_id=source_id, scan_id=scan_id)["count"]
    )


def reconcile_missing_detail(
    connection: sqlite3.Connection,
    *,
    source_id: int,
    scan_id: int,
    sample_limit: int = 4096,
) -> dict[str, Any]:
    """The same single atomic reconciliation, plus the assets it just marked missing.

    The ``UPDATE`` predicate is byte-for-byte the frozen one; P4-02Y only adds
    ``RETURNING`` so the refresh can hand the newly unavailable assets to its
    location-delta consumers (taken-time revision, search projection). The returned
    ``asset_ids`` are a bounded sample - the count is always complete, the sample is only
    a routing hint and a larger overflow simply falls back to the full selector.
    """
    scan = connection.execute(
        "SELECT source_id FROM scan_runs WHERE scan_id = ?", (scan_id,)
    ).fetchone()
    if scan is None or int(scan["source_id"]) != int(source_id):
        raise CatalogError(
            INVALID_STATE_TRANSITION,
            "missing reconciliation must use a scan of the same source",
            details={"source_id": source_id, "scan_id": scan_id},
        )
    rows = connection.execute(
        "UPDATE file_locations SET availability = 'MISSING' "
        "WHERE source_id = ? AND availability <> 'MISSING' "
        "AND (last_seen_scan_id IS NULL OR last_seen_scan_id <> ?) "
        "RETURNING asset_id",
        (source_id, scan_id),
    ).fetchall()
    limit = max(int(sample_limit), 0)
    asset_ids: list[int] = []
    truncated = False
    for row in rows:
        if row["asset_id"] is None:
            continue
        if len(asset_ids) >= limit:
            truncated = True
            break
        asset_ids.append(int(row["asset_id"]))
    return {"count": len(rows), "asset_ids": asset_ids, "truncated": truncated}


def count_unbound_locations(
    connection: sqlite3.Connection, *, source_id: int, scan_id: int
) -> int:
    """Positions this scan still has to confirm content for (ADR-0002 §5.2)."""
    return int(
        connection.execute(
            "SELECT COUNT(*) FROM file_locations "
            "WHERE source_id = ? AND last_seen_scan_id = ? AND asset_id IS NULL",
            (source_id, scan_id),
        ).fetchone()[0]
    )


def delete_source(connection: sqlite3.Connection, *, source_id: int) -> dict[str, Any]:
    """Cancel the management of one source root inside the caller's transaction.

    This is *registration* deletion, never file deletion (P4-02Y-I §5): the registered root,
    its locations and its scan history go away, while ``assets``, artifacts, vectors, manual
    knowledge and - above everything else - the user's original photos are untouched. An
    asset that also has a location in another source keeps every fact it has; an asset that
    only lived here becomes an orphan (no location at all), which this card deliberately does
    not garbage-collect (``orphan asset/artifact/vector GC`` is explicitly out of scope).

    A source with a scan in flight (``QUEUED``/``RUNNING``) is refused: the scan worker would
    otherwise keep writing locations for a source that no longer exists. A ``BLOCKED`` scan
    (an offline root) is not a live execution, so it is simply removed together with the
    rest of the source's scan state.
    """
    source_id = int(source_id)
    row = get_source_row(connection, source_id)
    live = connection.execute(
        "SELECT scan_id, state FROM scan_runs WHERE source_id = ? "
        "AND state IN ('QUEUED', 'RUNNING') ORDER BY scan_id LIMIT 1",
        (source_id,),
    ).fetchone()
    if live is not None:
        raise CatalogError(
            INVALID_STATE_TRANSITION,
            f"source {source_id} has a scan in flight",
            details={
                "source_id": source_id,
                "scan_id": int(live["scan_id"]),
                "state": str(live["state"]),
                "reason": "SCAN_IN_PROGRESS",
            },
        )
    unresolved_retention = connection.execute(
        "SELECT scan_id FROM retention_source_anomalies "
        "WHERE source_id=? AND state='NEEDS_REVIEW' LIMIT 1",
        (source_id,),
    ).fetchone() if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='retention_source_anomalies'"
    ).fetchone() else None
    if unresolved_retention is not None:
        raise CatalogError(
            INVALID_STATE_TRANSITION,
            f"source {source_id} has an unresolved bulk-missing retention review",
            details={"source_id": source_id, "scan_id": int(unresolved_retention["scan_id"]),
                     "reason": "RETENTION_ANOMALY_NEEDS_REVIEW"},
        )
    scan_ids = [
        int(item["scan_id"])
        for item in connection.execute(
            "SELECT scan_id FROM scan_runs WHERE source_id = ? ORDER BY scan_id", (source_id,)
        ).fetchall()
    ]
    orphaned = int(
        connection.execute(
            "SELECT COUNT(DISTINCT l.asset_id) FROM file_locations AS l "
            "WHERE l.source_id = ? AND l.asset_id IS NOT NULL AND NOT EXISTS ("
            "SELECT 1 FROM file_locations AS other "
            "WHERE other.asset_id = l.asset_id AND other.source_id <> ?)",
            (source_id, source_id),
        ).fetchone()[0]
    )
    locations = int(
        connection.execute(
            "SELECT COUNT(*) FROM file_locations WHERE source_id = ?", (source_id,)
        ).fetchone()[0]
    )
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='asset_retention_state'"
    ).fetchone():
        connection.execute(
            "DELETE FROM asset_retention_state WHERE asset_id IN "
            "(SELECT asset_id FROM file_locations WHERE source_id=? AND asset_id IS NOT NULL)",
            (source_id,),
        )
        connection.execute(
            "DELETE FROM asset_retention_source_evidence WHERE source_id=? OR scan_id IN "
            "(SELECT scan_id FROM scan_runs WHERE source_id=?) OR asset_id IN "
            "(SELECT asset_id FROM file_locations WHERE source_id=? AND asset_id IS NOT NULL)",
            (source_id, source_id, source_id),
        )
        connection.execute(
            "DELETE FROM retention_source_anomalies WHERE source_id=? AND state='CLEARED'",
            (source_id,),
        )
    # Three derived fact tables point at the *observation* they were produced from with
    # ``ON DELETE RESTRICT`` (ADR-0005/ADR-0009/ADR-0010): the GPS fact of an asset, its OCR
    # document and its faces. Losing the position must not destroy a fact that may belong to
    # an asset still reachable through another source, so the reference is cleared instead of
    # the row: the fact keeps its asset identity (and any manual knowledge bound to it) and
    # only loses the position it was originally read from, which no longer exists.
    for table in ("asset_geo_facts", "asset_ocr_text", "faces"):
        connection.execute(
            f"UPDATE {table} SET location_id = NULL WHERE location_id IN ("
            "SELECT location_id FROM file_locations WHERE source_id = ?)",
            (source_id,),
        )
    # The locations go first: their ``AFTER DELETE`` trigger re-queues the filename
    # projection of every asset that was bound here, which is what makes a removed source's
    # filenames stop being searchable (P4-02Y-I §5).
    connection.execute("DELETE FROM file_locations WHERE source_id = ?", (source_id,))
    jobs_removed = 0
    if scan_ids:
        placeholders = ", ".join("?" for _ in scan_ids)
        cursor = connection.execute(
            f"DELETE FROM jobs WHERE subject_type = ? AND subject_id IN ({placeholders})",
            (SCAN_SUBJECT_TYPE, *(str(value) for value in scan_ids)),
        )
        jobs_removed = int(cursor.rowcount or 0)
    # ``source_roots.last_successful_scan`` points at a ``scan_runs`` row, so the pointer has
    # to be cleared before the history can be deleted (both are ``ON DELETE RESTRICT``).
    connection.execute(
        "UPDATE source_roots SET last_successful_scan = NULL WHERE source_id = ?", (source_id,)
    )
    connection.execute("DELETE FROM scan_runs WHERE source_id = ?", (source_id,))
    connection.execute("DELETE FROM source_roots WHERE source_id = ?", (source_id,))
    return {
        "source_id": source_id,
        "display_name": str(row["display_name"]),
        "root_path": str(row["root_path"]),
        "locations_removed": locations,
        "scans_removed": len(scan_ids),
        "scan_jobs_removed": jobs_removed,
        "assets_orphaned": orphaned,
        "originals_touched": 0,
    }


def reset_execution_coverage(
    connection: sqlite3.Connection, *, source_id: int, scan_id: int
) -> int:
    """Start a new execution of one logical scan with no inherited coverage.

    ``scan_runs`` may be resumed by a later execution under the same ``scan_id`` (ADR-0002
    §5.3). Coverage must be per execution, otherwise a position merely observed by the
    killed execution would look "seen" to the recovery execution and would never be
    reconciled. Only the scan-attribution marker is cleared: the location row, its stat
    values, its availability and its content binding are preserved positive facts.
    """
    cursor = connection.execute(
        "UPDATE file_locations SET last_seen_scan_id = NULL WHERE source_id = ? AND last_seen_scan_id = ?",
        (source_id, scan_id),
    )
    return int(cursor.rowcount or 0)


def ensure_scan_failed(
    connection: sqlite3.Connection,
    *,
    scan_id: int,
    now_ms: int,
    error_text: str | None = None,
) -> sqlite3.Row:
    """Put a scan_run into FAILED once; used where a job reached a terminal failure."""
    row = get_scan_row(connection, scan_id)
    if str(row["state"]) == "FAILED":
        return row
    return set_scan_state(
        connection, scan_id=scan_id, state="FAILED", now_ms=now_ms, error_text=error_text
    )


def scan_job_id(connection: sqlite3.Connection, *, scan_id: int) -> int | None:
    row = connection.execute(
        "SELECT job_id FROM jobs WHERE subject_type = ? AND subject_id = ? AND input_revision = ? "
        "AND task_type = ? AND processor_id = ?",
        (SCAN_SUBJECT_TYPE, str(scan_id), SCAN_INPUT_REVISION, SCAN_TASK_TYPE, SCAN_PROCESSOR_ID),
    ).fetchone()
    return int(row["job_id"]) if row is not None else None


def catalog_counts(connection: sqlite3.Connection, *, source_id: int | None = None) -> dict[str, Any]:
    """Counts for status output.

    ``locations_pending_content`` counts available positions whose content identity is not
    confirmed yet. A ``MISSING`` position keeps its last known binding (or none, if its
    content was never confirmed) and is reported through ``locations_missing`` instead.
    """

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
                "SELECT COUNT(*) FROM file_locations WHERE availability = 'PRESENT' AND asset_id IS NULL"
            ),
            "scans": scalar("SELECT COUNT(*) FROM scan_runs"),
            "jobs": scalar("SELECT COUNT(*) FROM jobs"),
        }
        return counts
    parameters = (int(source_id),)
    # These fields describe the same source-location snapshot. Count the
    # conditional subsets together instead of seeking that source five times.
    locations = connection.execute(
        "SELECT COUNT(*) AS locations, COUNT(DISTINCT asset_id) AS assets, "
        "COUNT(CASE WHEN availability = 'PRESENT' THEN 1 END) AS locations_present, "
        "COUNT(CASE WHEN availability = 'MISSING' THEN 1 END) AS locations_missing, "
        "COUNT(CASE WHEN availability = 'PRESENT' AND asset_id IS NULL THEN 1 END) "
        "AS locations_pending_content FROM file_locations WHERE source_id = ?",
        parameters,
    ).fetchone()
    location_counts = {key: int(locations[key]) for key in locations.keys()}
    counts = {
        "sources": 1,
        "assets_total": assets_total,
        **location_counts,
        "scans": scalar("SELECT COUNT(*) FROM scan_runs WHERE source_id = ?", parameters),
        "jobs": scalar(
            "SELECT COUNT(*) FROM jobs WHERE subject_type = ? AND subject_id IN "
            "(SELECT CAST(scan_id AS TEXT) FROM scan_runs WHERE source_id = ?)",
            (SCAN_SUBJECT_TYPE, int(source_id)),
        ),
    }
    return counts
