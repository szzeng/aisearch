"""The visible suggestion set: machine candidates the user may still confirm.

ADR-0005 §4/§10: a machine candidate is never an assignment. The visible set is the
candidates of the latest resolution that was computed against the *currently active*
dataset, minus the places the user excluded for that asset, and it is empty while the asset
carries an effective ``MANUAL_ASSIGNED`` lock. Any change to that set moves the independent
``place_suggestion_revision`` and never the authoritative CatalogFilterRevision.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any

from photo_search.places import datasets as dataset_module_registry
from photo_search.places import revision as revision_module

MANUAL_LOCK_SQL = (
    "SELECT 1 FROM asset_place_assignments WHERE asset_id = ? AND mode = 'MANUAL_ASSIGNED'"
)

#: ``place_state=suggested``: no effective manual assignment, a CANDIDATES resolution from
#: the active dataset, and at least one candidate that is not excluded for this asset.
SUGGESTED_PREDICATE = (
    "NOT EXISTS (SELECT 1 FROM asset_place_assignments AS ap WHERE ap.asset_id = {alias}.asset_id "
    "AND ap.mode = 'MANUAL_ASSIGNED') AND NOT EXISTS (SELECT 1 FROM effective_asset_places ep "
    "WHERE ep.asset_id={alias}.asset_id) AND EXISTS (SELECT 1 FROM place_resolutions AS pr "
    "WHERE pr.asset_id = {alias}.asset_id AND pr.status = 'CANDIDATES' "
    "AND pr.dataset_id = (SELECT dataset_id FROM active_geo_dataset WHERE singleton = 1) "
    "AND pr.resolution_id = (SELECT pr2.resolution_id FROM place_resolutions AS pr2 "
    "WHERE pr2.asset_id = {alias}.asset_id "
    "AND pr2.dataset_id = (SELECT dataset_id FROM active_geo_dataset WHERE singleton = 1) "
    "ORDER BY pr2.created_at DESC, pr2.resolution_id DESC LIMIT 1) "
    "AND EXISTS (SELECT 1 FROM place_resolution_candidates AS c "
    "WHERE c.resolution_id = pr.resolution_id AND (c.local_place_id IS NULL OR NOT EXISTS ("
    "SELECT 1 FROM asset_place_exclusions AS x WHERE x.asset_id = {alias}.asset_id "
    "AND x.place_id = c.local_place_id AND x.active = 1))))"
)

RESOLUTION_FIELDS = (
    "resolution_id, asset_id, geo_fact_id, input_revision, control_revision_seen, processor_id, "
    "dataset_id, dataset_version, config_hash, status, suggestion_revision, created_at"
)

CANDIDATE_FIELDS = (
    "candidate_id, dataset_place_id, local_place_id, kind, display_name, ancestor_names_json, "
    "rank, distance_m, match_basis, confidence_basis, disposition"
)


def latest_resolution_row(
    connection: sqlite3.Connection, asset_id: int
) -> sqlite3.Row | None:
    """The newest resolution of one asset that was computed against the active dataset."""
    return connection.execute(
        f"SELECT {RESOLUTION_FIELDS} FROM place_resolutions WHERE asset_id = ? "
        "AND dataset_id = (SELECT dataset_id FROM active_geo_dataset WHERE singleton = 1) "
        "ORDER BY created_at DESC, resolution_id DESC LIMIT 1",
        (int(asset_id),),
    ).fetchone()


def has_manual_assignment(connection: sqlite3.Connection, asset_id: int) -> bool:
    return connection.execute(MANUAL_LOCK_SQL, (int(asset_id),)).fetchone() is not None


def _candidate_rows(connection: sqlite3.Connection, resolution_id: str) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            f"SELECT {CANDIDATE_FIELDS} FROM place_resolution_candidates "
            "WHERE resolution_id = ? ORDER BY rank",
            (str(resolution_id),),
        ).fetchall()
    )


def excluded_place_ids(connection: sqlite3.Connection, asset_id: int) -> set[int]:
    """The places this asset currently excludes from suggestion."""
    return {
        int(row["place_id"])
        for row in connection.execute(
            "SELECT place_id FROM asset_place_exclusions WHERE asset_id = ? AND active = 1",
            (int(asset_id),),
        ).fetchall()
    }


def visible_candidates(
    connection: sqlite3.Connection, asset_id: int
) -> tuple[sqlite3.Row | None, list[sqlite3.Row], int]:
    """``(resolution, visible candidate rows, excluded candidate count)`` for one asset."""
    resolution = latest_resolution_row(connection, int(asset_id))
    if resolution is None:
        return None, [], 0
    rows = _candidate_rows(connection, str(resolution["resolution_id"]))
    if has_manual_assignment(connection, int(asset_id)) or connection.execute(
        "SELECT 1 FROM effective_asset_places WHERE asset_id=?", (int(asset_id),)
    ).fetchone():
        return resolution, [], len(rows)
    excluded = excluded_place_ids(connection, int(asset_id))
    visible = [
        row
        for row in rows
        if row["local_place_id"] is None or int(row["local_place_id"]) not in excluded
    ]
    return resolution, visible, len(rows) - len(visible)


def visible_signature(connection: sqlite3.Connection, asset_id: int) -> tuple[Any, ...]:
    """A cheap, order-sensitive identity of *what a caller can actually see*.

    It deliberately holds only the visible candidates: "no resolution", "no candidate" and
    "every candidate hidden by a manual lock or an exclusion" are all the empty set, so
    publishing an empty resolution never moves the suggestion revision.
    """
    _resolution, visible, _excluded = visible_candidates(connection, int(asset_id))
    return tuple((int(row["rank"]), str(row["candidate_id"])) for row in visible)


def reconcile_suggestion_visibility(
    connection: sqlite3.Connection,
    *,
    before: Mapping[int, tuple[Any, ...]],
    asset_ids: Iterable[int],
    now_ms: int,
) -> int | None:
    """Move the suggestion revision when any of ``asset_ids`` changed its visible set."""
    changed = [
        int(asset_id)
        for asset_id in asset_ids
        if before.get(int(asset_id)) != visible_signature(connection, int(asset_id))
    ]
    if not changed:
        return None
    return revision_module.bump_suggestion_revision(
        connection, cause=revision_module.CAUSE_SUGGESTION_CHANGED, now_ms=int(now_ms)
    )


def candidate_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": str(row["candidate_id"]),
        "dataset_place_id": str(row["dataset_place_id"]),
        "local_place_id": None if row["local_place_id"] is None else int(row["local_place_id"]),
        "kind": str(row["kind"]),
        "display_name": str(row["display_name"]),
        "ancestor_names": [str(value) for value in json.loads(str(row["ancestor_names_json"]))],
        "rank": int(row["rank"]),
        "distance_m": float(row["distance_m"]),
        "match_basis": str(row["match_basis"]),
        "confidence_basis": str(row["confidence_basis"]),
        "disposition": str(row["disposition"]),
    }


def suggestion_view(connection: sqlite3.Connection, asset_id: int) -> dict[str, Any]:
    """The suggestion half of ``GET /assets/{id}/place`` (ADR-0005 §4/§9)."""
    active = dataset_module_registry.active_row(connection)
    datum_row = connection.execute(
        "SELECT status, datum_status FROM asset_geo_facts WHERE asset_id = ?", (int(asset_id),)
    ).fetchone()
    if active is None:
        return {
            "state": "unavailable",
            "reason": "PLACE_RESOLVER_NOT_AVAILABLE",
            "resolution_id": None,
            "resolution_status": None,
            "dataset_id": None,
            "dataset_version": None,
            "processor_id": None,
            "control_revision_seen": None,
            "datum_status": None if datum_row is None else str(datum_row["datum_status"]),
            "assumed_datum": None,
            "requires_confirmation": True,
            "visible": False,
            "excluded_candidate_count": 0,
            "candidates": [],
        }
    resolution, visible, excluded = visible_candidates(connection, int(asset_id))
    datum_status = None if datum_row is None else str(datum_row["datum_status"])
    if resolution is None:
        return {
            "state": "no_resolution",
            "reason": None,
            "resolution_id": None,
            "resolution_status": None,
            "dataset_id": str(active["dataset_id"]),
            "dataset_version": str(active["package_version"]),
            "processor_id": None,
            "control_revision_seen": None,
            "datum_status": datum_status,
            "assumed_datum": datum_status == "ASSUMED_WGS84",
            "requires_confirmation": True,
            "visible": False,
            "excluded_candidate_count": 0,
            "candidates": [],
        }
    status = str(resolution["status"])
    state = "candidates" if visible else "no_candidate"
    return {
        "state": state,
        "reason": None,
        "resolution_id": str(resolution["resolution_id"]),
        "resolution_status": status,
        "dataset_id": str(resolution["dataset_id"]),
        "dataset_version": str(resolution["dataset_version"]),
        "processor_id": str(resolution["processor_id"]),
        "control_revision_seen": int(resolution["control_revision_seen"]),
        "datum_status": datum_status,
        "assumed_datum": datum_status == "ASSUMED_WGS84",
        # Every candidate is suggestion-only: a human confirmation is always required.
        "requires_confirmation": True,
        "visible": bool(visible),
        "excluded_candidate_count": int(excluded),
        "candidates": [candidate_view(row) for row in visible],
    }


def suggestion_counts(connection: sqlite3.Connection) -> dict[str, int]:
    """Honest counters for ``/places/coverage``."""
    resolved = connection.execute(
        "SELECT COUNT(DISTINCT asset_id) AS value FROM place_resolutions "
        "WHERE dataset_id = (SELECT dataset_id FROM active_geo_dataset WHERE singleton = 1)"
    ).fetchone()
    suggested = connection.execute(
        f"SELECT COUNT(*) AS value FROM assets AS a WHERE {SUGGESTED_PREDICATE.format(alias='a')}"
    ).fetchone()
    return {
        "place_resolved": int(resolved["value"] if resolved is not None else 0),
        "places_suggested": int(suggested["value"] if suggested is not None else 0),
    }
