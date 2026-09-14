"""The bounded ``PERSON_BUNDLE`` snapshot of ADR-0006 §7.

One bundle is the *smallest* faithful record of what a person operation must be able to
compensate, and it is deliberately not a copy of the database:

* the person row's ``name``/``name_key``/``avatar_face_id``/``merged_into``/``revision``;
* the alias set of that person (display spelling plus its de-duplication key);
* the ``face_person_links`` rows of that person (face, kind, decision, confidence, reason,
  revision, last operation) - both the effective link and every suggestion, because a merge
  moves and downgrades both.

Created/updated timestamps are excluded on purpose: they are bookkeeping, so an undo decides
"has anything semantic moved since?" without failing on a clock. Vectors, bboxes, landmarks,
paths and coordinates are excluded because the compensation never touches them.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from photo_search.persons.contracts import canonical_json

#: The per-row fields a bundle records. ``created_at``/``updated_at`` are intentionally absent.
LINK_FIELDS = (
    "face_id",
    "person_id",
    "assignment_kind",
    "decision",
    "confidence",
    "reason",
    "revision",
    "last_operation_id",
)


def _render_person(row: Any) -> dict[str, Any]:
    return {
        "person_id": int(row["person_id"]),
        "name": None if row["name"] is None else str(row["name"]),
        "name_key": None if row["name_key"] is None else str(row["name_key"]),
        "avatar_face_id": (
            None if row["avatar_face_id"] is None else str(row["avatar_face_id"])
        ),
        "merged_into": None if row["merged_into"] is None else int(row["merged_into"]),
        "revision": int(row["revision"]),
    }


def _render_link(row: Any) -> dict[str, Any]:
    return {
        "face_id": str(row["face_id"]),
        "person_id": int(row["person_id"]),
        "assignment_kind": str(row["assignment_kind"]),
        "decision": str(row["decision"]),
        "confidence": float(row["confidence"]),
        "reason": None if row["reason"] is None else str(row["reason"]),
        "revision": int(row["revision"]),
        "last_operation_id": (
            None if row["last_operation_id"] is None else str(row["last_operation_id"])
        ),
    }


def read_bundle(connection: sqlite3.Connection, person_id: int) -> dict[str, Any]:
    """The live semantic bundle of one person (already canonical by convention)."""
    row = connection.execute(
        "SELECT person_id, name, name_key, avatar_face_id, merged_into, revision "
        "FROM persons WHERE person_id = ?",
        (int(person_id),),
    ).fetchone()
    if row is None:
        from photo_search.persons.contracts import fail

        raise fail(
            "PERSON_NOT_FOUND",
            f"person {int(person_id)} does not exist",
            person_id=int(person_id),
        )
    aliases = [
        {"alias": str(alias["alias"]), "normalized_alias": str(alias["normalized_alias"])}
        for alias in connection.execute(
            "SELECT alias, normalized_alias FROM person_aliases WHERE person_id = ? "
            "ORDER BY normalized_alias, alias",
            (int(person_id),),
        ).fetchall()
    ]
    links = [
        _render_link(link)
        for link in connection.execute(
            f"SELECT {', '.join(LINK_FIELDS)} FROM face_person_links WHERE person_id = ? "
            "ORDER BY face_id, assignment_kind, decision, revision",
            (int(person_id),),
        ).fetchall()
    ]
    return {**_render_person(row), "aliases": aliases, "links": links}


def bundle_json(bundle: dict[str, Any]) -> str:
    return canonical_json(bundle)


def load_bundle(raw: Any) -> dict[str, Any]:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    return json.loads(str(raw))


def bundle_differences(live: dict[str, Any], recorded: dict[str, Any]) -> list[str]:
    """Name every semantic field that moved between two bundles.

    The result is what an undo reports instead of a bare refusal: ``details`` lists the
    entity type, its id and the field names, so a caller can see *what* changed without the
    response ever carrying a name, a face box or a similarity.
    """
    differences: list[str] = []
    for field in ("name", "name_key", "avatar_face_id", "merged_into", "revision"):
        if live.get(field) != recorded.get(field):
            differences.append(field)
    live_aliases = sorted(
        (str(item["normalized_alias"]), str(item["alias"])) for item in live.get("aliases", [])
    )
    recorded_aliases = sorted(
        (str(item["normalized_alias"]), str(item["alias"])) for item in recorded.get("aliases", [])
    )
    if live_aliases != recorded_aliases:
        differences.append("aliases")
    if _link_keys(live) != _link_keys(recorded):
        differences.append("links")
    return differences


def _link_keys(bundle: dict[str, Any]) -> list[tuple[Any, ...]]:
    fields = (
        "face_id",
        "person_id",
        "assignment_kind",
        "decision",
        "confidence",
        "reason",
        "revision",
        "last_operation_id",
    )
    return sorted(
        tuple(item.get(field) for field in fields) for item in bundle.get("links", [])
    )
