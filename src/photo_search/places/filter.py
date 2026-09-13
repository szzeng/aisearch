"""Pure-SQLite place filtering, coverage and the protected per-asset projections.

ADR-0005 §10: ``place_ids`` is an OR that expands to descendants by default and ANDs with
every other condition, the several ``place_state`` values are not interchangeable, and the
counts are always de-duplicated by ``asset_id`` so two file locations of one asset never
count twice. Nothing here needs a GPU or a name dataset.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from typing import Any

from photo_search.catalog.errors import INVALID_PLACE_FILTER
from photo_search.places import assignments as assignment_module
from photo_search.places import revision as revision_module
from photo_search.places.contracts import CONTRACT_VERSION, PLACE_STATES, fail, privacy_flags
from photo_search.places.directory import ancestor_views, descendant_ids, get_place_row

ASSIGNED_EXISTS = (
    "EXISTS (SELECT 1 FROM asset_place_assignments AS ap WHERE ap.asset_id = {alias}.asset_id "
    "AND ap.mode = 'MANUAL_ASSIGNED'{place_clause})"
)
UNASSIGNED_NOT_EXISTS = (
    "NOT EXISTS (SELECT 1 FROM asset_place_assignments AS ap WHERE ap.asset_id = {alias}.asset_id "
    "AND ap.mode = 'MANUAL_ASSIGNED')"
)
MANUAL_UNASSIGNED_EXISTS = (
    "EXISTS (SELECT 1 FROM asset_place_assignments AS ap WHERE ap.asset_id = {alias}.asset_id "
    "AND ap.mode = 'MANUAL_UNASSIGNED')"
)

#: No resolver exists before P2-03B, so "has a suggestion" is deterministically empty. It is
#: never silently widened into an effective assignment.
SUGGESTED_EMPTY = "0"


def place_condition(
    connection: sqlite3.Connection,
    *,
    place_ids: Sequence[int] | None = None,
    include_descendants: bool = True,
    place_state: str = "any",
    alias: str = "a",
) -> tuple[str, list[Any]]:
    """One SQL predicate over ``<alias>.asset_id`` plus its bound parameters."""
    state = str(place_state or "any")
    if state not in PLACE_STATES:
        raise fail(INVALID_PLACE_FILTER, "place_state is not one of the documented values")
    ids = [int(value) for value in (place_ids or ())]
    if ids:
        for place_id in ids:
            get_place_row(connection, place_id)
        if state not in ("any", "assigned"):
            raise fail(
                INVALID_PLACE_FILTER,
                "place_ids may only be combined with place_state any or assigned",
            )
    if state == "suggested":
        # A suggestion-only filter must be bound to its own revision; with no resolver the
        # visible candidate set is empty rather than "everything unassigned".
        return SUGGESTED_EMPTY, []
    if ids:
        # "any" and "assigned" are the same predicate here: a place only ever reaches an
        # asset through its one effective control row, so an unassigned asset cannot match
        # a place id at all.
        scope = descendant_ids(connection, ids) if include_descendants else ids
        if not scope:
            return "0", []
        placeholders = ", ".join("?" for _ in scope)
        return (
            ASSIGNED_EXISTS.format(
                alias=alias, place_clause=f" AND ap.place_id IN ({placeholders})"
            ),
            list(scope),
        )
    if state == "assigned":
        return ASSIGNED_EXISTS.format(alias=alias, place_clause=""), []
    if state == "unassigned":
        return UNASSIGNED_NOT_EXISTS.format(alias=alias), []
    if state == "manual_unassigned":
        return MANUAL_UNASSIGNED_EXISTS.format(alias=alias), []
    return "1", []


def place_filter_summary(
    connection: sqlite3.Connection,
    *,
    place_ids: Sequence[int] | None = None,
    include_descendants: bool = True,
    place_state: str = "any",
) -> dict[str, Any]:
    """The echo of an applied filter, including the revisions a session must bind."""
    ids = [int(value) for value in (place_ids or ())]
    scope: list[int] = []
    if ids:
        scope = descendant_ids(connection, ids) if include_descendants else ids
    return {
        "place_ids": ids,
        "include_descendants": bool(include_descendants),
        "place_state": str(place_state or "any"),
        "expanded_place_ids": scope,
        "catalog_filter_revision": revision_module.read_filter_revision(connection),
        "suggestion_revision": revision_module.read_suggestion_revision(connection),
    }


def geo_fact_view(connection: sqlite3.Connection, asset_id: int) -> dict[str, Any] | None:
    """The frozen ``GeoFact`` projection, exact coordinates included (detail endpoint)."""
    row = connection.execute(
        "SELECT * FROM asset_geo_facts WHERE asset_id = ?", (int(asset_id),)
    ).fetchone()
    if row is None:
        return None
    raw = None if row["raw_gps_json"] is None else json.loads(str(row["raw_gps_json"]))
    normalized = None
    if row["latitude"] is not None and row["longitude"] is not None:
        normalized = {
            "datum": "WGS84",
            "latitude": float(row["latitude"]),
            "longitude": float(row["longitude"]),
            "altitude_m": None if raw is None else raw.get("altitude_m"),
            "direction_deg": None if raw is None else raw.get("direction_deg"),
            "accuracy_m": None if raw is None else raw.get("accuracy_m"),
        }
    return {
        "contract_version": CONTRACT_VERSION,
        "geo_fact_id": str(row["geo_fact_id"]),
        "asset_id": int(row["asset_id"]),
        "location_id": None if row["location_id"] is None else int(row["location_id"]),
        "input_revision": str(row["input_revision"]),
        "fact_revision": int(row["fact_revision"]),
        "status": str(row["status"]),
        "source": str(row["source"]),
        "processor_id": str(row["processor_id"]),
        "raw_fields_checksum_sha256": str(row["raw_fields_checksum_sha256"]),
        "raw_gps": raw,
        "normalized_wgs84": normalized,
        "datum_status": str(row["datum_status"]),
        "privacy": privacy_flags(),
        "created_at": int(row["created_at"]),
    }


def gps_state(connection: sqlite3.Connection, asset_id: int) -> str:
    """Coarse, non-coordinate GPS state for a general-purpose asset response."""
    row = connection.execute(
        "SELECT status FROM asset_geo_facts WHERE asset_id = ?", (int(asset_id),)
    ).fetchone()
    return "NOT_EXTRACTED" if row is None else str(row["status"])


def asset_place_view(
    connection: sqlite3.Connection,
    asset_id: int,
    *,
    include_geo_state: bool = True,
) -> dict[str, Any]:
    """``GET /assets/{id}/place``: control, lock, exclusions, suggestion state, revisions."""
    assignment = assignment_module.read_current_view(connection, int(asset_id))
    place = None
    if assignment is not None and assignment["place_id"] is not None:
        row = get_place_row(connection, int(assignment["place_id"]))
        place = {
            "place_id": int(row["place_id"]),
            "kind": str(row["kind"]),
            "canonical_name": str(row["canonical_name"]),
            "lifecycle": str(row["lifecycle"]),
            "ancestors": ancestor_views(connection, int(row["place_id"])),
        }
    payload: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "asset_id": int(asset_id),
        "assignment": assignment,
        "place": place,
        "exclusions": assignment_module.exclusion_summary(connection, int(asset_id)),
        "suggested": {
            "state": "unavailable",
            "reason": "PLACE_RESOLVER_NOT_AVAILABLE",
            "resolution_status": None,
            "candidates": [],
        },
        "catalog_filter_revision": revision_module.read_filter_revision(connection),
        "suggestion_revision": revision_module.read_suggestion_revision(connection),
    }
    if include_geo_state:
        payload["gps_state"] = gps_state(connection, int(asset_id))
    return payload


def coverage(connection: sqlite3.Connection) -> dict[str, Any]:
    """Coverage counts with honest denominators (ADR-0005 §9 ``/places/coverage``)."""

    def scalar(sql: str, parameters: tuple = ()) -> int:
        row = connection.execute(sql, parameters).fetchone()
        return int(row[0] if row is not None else 0)

    assets_total = scalar("SELECT COUNT(*) FROM assets")
    assigned = scalar(
        "SELECT COUNT(*) FROM asset_place_assignments WHERE mode = 'MANUAL_ASSIGNED'"
    )
    manual_unassigned = scalar(
        "SELECT COUNT(*) FROM asset_place_assignments WHERE mode = 'MANUAL_UNASSIGNED'"
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "assets_total": assets_total,
        "gps_extracted": scalar("SELECT COUNT(*) FROM asset_geo_facts"),
        "gps_valid": scalar("SELECT COUNT(*) FROM asset_geo_facts WHERE status = 'VALID'"),
        "gps_absent": scalar("SELECT COUNT(*) FROM asset_geo_facts WHERE status = 'ABSENT'"),
        "gps_invalid": scalar(
            "SELECT COUNT(*) FROM asset_geo_facts WHERE status IN ('INVALID', 'UNSUPPORTED_CRS')"
        ),
        # P2-03B owns the resolver; reporting a placeholder here would claim a capability
        # this build does not have.
        "resolver_available": False,
        "place_resolved": 0,
        "places_assigned": assigned,
        "places_manual_unassigned": manual_unassigned,
        "places_suggested": 0,
        "places_unclassified": assets_total - assigned,
        "catalog_filter_revision": revision_module.read_filter_revision(connection),
        "suggestion_revision": revision_module.read_suggestion_revision(connection),
    }
