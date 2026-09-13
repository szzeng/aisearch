"""The authoritative CatalogFilterRevision and the independent suggestion revision.

ADR-0005 §11 fixes the trigger matrix: the filter revision moves **at most once** in the
same SQLite transaction that changed a filterable fact, an event row records why, and the
already confirmed vectors of the affected assets are queued for projection refresh through
the P2-03 entry point. GPS facts, no-ops and suggestion-only changes never move it.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable

from photo_search.catalog.errors import CATALOG_UNREADABLE, CatalogError

FILTER_REVISION_ID = 1
SUGGESTION_REVISION_ID = 1

CAUSE_EFFECTIVE_ASSIGNMENT_CHANGED = "PLACE_EFFECTIVE_ASSIGNMENT_CHANGED"
CAUSE_NAME_CHANGED = "PLACE_NAME_CHANGED"
CAUSE_HIERARCHY_CHANGED = "PLACE_HIERARCHY_CHANGED"
#: The independent suggestion revision moved because the visible candidate set changed.
CAUSE_SUGGESTION_CHANGED = "PLACE_SUGGESTION_CANDIDATES_CHANGED"

#: ``refresh(connection, asset_ids=..., projection_revision=..., now_ms=...)`` - the P2-03
#: ``IndexCoordinator.schedule_projection_refresh`` signature, injected so this module
#: never has to construct a vector backend.
ProjectionRefresh = Callable[..., int]


def read_filter_revision(connection: sqlite3.Connection) -> int:
    return _read_singleton(connection, "catalog_filter_revision")


def read_suggestion_revision(connection: sqlite3.Connection) -> int:
    return _read_singleton(connection, "place_suggestion_revision")


def _read_singleton(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT revision FROM {table} WHERE revision_id = 1").fetchone()
    if row is None:
        raise CatalogError(
            CATALOG_UNREADABLE,
            f"the catalog has no {table} row; the schema is not usable",
            details={"table": table},
        )
    return int(row[0] if not isinstance(row, sqlite3.Row) else row["revision"])


def bump_filter_revision(
    connection: sqlite3.Connection,
    *,
    cause: str,
    now_ms: int,
    asset_ids: Iterable[int] = (),
    all_assets: bool = False,
    refresh: ProjectionRefresh | None = None,
) -> int:
    """Move the filter revision once and queue the affected confirmed vectors.

    ``all_assets`` means "the affected set is unknown and must be treated as everything";
    the event keeps the flag so a later rebuild can tell the two cases apart. Only assets
    with a confirmed vector produce an outbox event (P2-03 contract), so this stays cheap
    for a library that has not been indexed yet.
    """
    affected = sorted({int(value) for value in asset_ids})
    current = read_filter_revision(connection)
    revision = current + 1
    connection.execute(
        "UPDATE catalog_filter_revision SET revision = ?, committed_at = ?, cause = ? "
        "WHERE revision_id = ?",
        (revision, int(now_ms), str(cause), FILTER_REVISION_ID),
    )
    connection.execute(
        "INSERT INTO catalog_filter_revision_events"
        "(revision, cause, changed_count, all_assets, asset_ids_json, committed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            revision,
            str(cause),
            len(affected),
            1 if all_assets else 0,
            json.dumps(affected, separators=(",", ":")),
            int(now_ms),
        ),
    )
    if refresh is not None and affected:
        refresh(
            connection,
            asset_ids=affected,
            projection_revision=revision,
            now_ms=int(now_ms),
        )
    return revision


def bump_suggestion_revision(connection: sqlite3.Connection, *, cause: str, now_ms: int) -> int:
    """Move the independent suggestion revision exactly once.

    ADR-0005 §4/§11: a candidate-visibility change is the only thing that moves this
    counter, and it must never move the authoritative CatalogFilterRevision.
    """
    current = read_suggestion_revision(connection)
    revision = current + 1
    connection.execute(
        "UPDATE place_suggestion_revision SET revision = ?, committed_at = ?, cause = ? "
        "WHERE revision_id = ?",
        (revision, int(now_ms), str(cause), SUGGESTION_REVISION_ID),
    )
    return revision


def projection_refresher(catalog: object) -> ProjectionRefresh:
    """Build the P2-03 refresh callback for one catalog (imported lazily, no cycle)."""
    from photo_search.indexing.store import IndexCoordinator

    coordinator = IndexCoordinator(catalog)  # type: ignore[arg-type]

    def refresh(
        connection: sqlite3.Connection,
        *,
        asset_ids: list[int],
        projection_revision: int,
        now_ms: int,
    ) -> int:
        return coordinator.schedule_projection_refresh(
            connection,
            asset_ids=[int(value) for value in asset_ids],
            projection_revision=int(projection_revision),
            now_ms=int(now_ms),
        )

    return refresh
