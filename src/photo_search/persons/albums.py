"""Bounded personal people/album projections over existing authoritative facts."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from photo_search.api.errors import INVALID_CURSOR, VALIDATION_ERROR, ApiError
from photo_search.api.queries import asset_page, encode_capture_cursor
from photo_search.face.contract import FACE_ARTIFACT_REVISION, FACE_PROCESSOR_ID, FACE_SPEC_REVISION
from photo_search.persons.identity import resolve_canonical_person_id
from photo_search.persons.members import MEMBER_PREDICATE
from photo_search.persons.read import person_summary
from photo_search.places.revision import read_filter_revision
from photo_search.taken_time.read import asset_view


def _encode(binding: list[Any], position: Any) -> str:
    data = json.dumps([*binding, position], ensure_ascii=False, separators=(",", ":"))
    return base64.urlsafe_b64encode(data.encode()).decode().rstrip("=")


def _position(cursor: str | None, binding: list[Any], default: Any) -> Any:
    if not cursor:
        return default
    try:
        if len(cursor) > 2048:
            raise ValueError
        values = json.loads(
            base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        )
        if not isinstance(values, list) or values[:-1] != binding:
            raise ValueError
        return values[-1]
    except (ValueError, TypeError, UnicodeError, binascii.Error) as error:
        raise ApiError(INVALID_CURSOR, "the people list changed; reload it", status=409) from error


def _album_summary(connection: Any, row: Any) -> dict[str, Any]:
    """Choose a display avatar from effective members without changing stored human facts."""
    summary = person_summary(connection, row)
    avatar = connection.execute(
        "SELECT f.face_id FROM face_person_links AS l JOIN faces AS f ON f.face_id = l.face_id "
        "JOIN assets AS a ON a.asset_id = f.asset_id WHERE l.person_id = ? "
        "AND a.state = 'ACTIVE' AND "
        + MEMBER_PREDICATE
        + " ORDER BY (f.face_id = ?) DESC, (f.quality_state = 'OK') DESC, "
        "(f.spec_revision = ?) DESC, f.face_pixels DESC, f.face_id LIMIT 1",
        (summary["person_id"], row["avatar_face_id"] or "", FACE_SPEC_REVISION),
    ).fetchone()
    face_id = None if avatar is None else str(avatar["face_id"])
    summary["avatar_face_id"] = face_id
    summary["thumbnail_url"] = None if face_id is None else f"/api/v1/faces/{face_id}/thumbnail"
    return summary


def _photo_person_faces(connection: Any, *, asset_id: int, person_id: int) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT f.face_id, f.bbox_json FROM faces AS f JOIN face_person_links AS l ON l.face_id = f.face_id "
        "WHERE f.asset_id = ? AND l.person_id = ? AND "
        + MEMBER_PREDICATE
        + " ORDER BY (f.spec_revision = ?) DESC, f.face_pixels DESC, f.face_id LIMIT 9",
        (asset_id, person_id, FACE_SPEC_REVISION),
    ).fetchall()
    return {
        "person_faces": [
            {
                "face_id": str(r["face_id"]),
                "thumbnail_url": f"/api/v1/faces/{r['face_id']}/thumbnail",
            }
            for r in rows[:8]
        ],
        "person_faces_has_more": len(rows) > 8,
    }


def people_page(
    catalog: Any, *, kind: str, q: str, limit: int, cursor: str | None
) -> dict[str, Any]:
    if kind not in ("all", "named", "unnamed") or len(q) > 200:
        raise ApiError(VALIDATION_ERROR, "invalid person query", status=422)
    query = q.strip()
    binding = ["people1", kind, query, read_filter_revision(catalog.connection)]
    after = _position(cursor, binding, 0)
    if type(after) is not int or after < 0:
        raise ApiError(INVALID_CURSOR, "invalid person position", status=422)
    conditions = ["p.merged_into IS NULL", "p.person_id > ?"]
    parameters: list[Any] = [after]
    if kind == "named":
        conditions.append("p.name IS NOT NULL")
    elif kind == "unnamed":
        conditions.append("p.name IS NULL")
    if query:
        # instr is literal, so '%'/'_' have no surprising wildcard meaning.
        conditions.append(
            "(instr(p.name, ?) > 0 OR EXISTS (SELECT 1 FROM person_aliases AS a WHERE a.person_id = p.person_id AND instr(a.alias, ?) > 0))"
        )
        parameters.extend([query, query])
    rows = catalog.read_all(
        "SELECT p.* FROM persons AS p WHERE "
        + " AND ".join(conditions)
        + " ORDER BY p.person_id LIMIT ?",
        (*parameters, limit + 1),
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "persons": [_album_summary(catalog.connection, row) for row in rows],
        "has_more": has_more,
        "next_cursor": _encode(binding, int(rows[-1]["person_id"])) if has_more else None,
    }


def photo_page(
    catalog: Any, cache: Any, *, person_id: int, limit: int, cursor: str | None
) -> dict[str, Any]:
    canonical = resolve_canonical_person_id(catalog.connection, person_id)
    binding = ["personphotos1", canonical]
    inner = _position(cursor, binding, None)
    if inner is not None and not isinstance(inner, str):
        raise ApiError(INVALID_CURSOR, "invalid photo position", status=422)
    page = asset_page(
        catalog, cache, limit=limit + 1, cursor=inner, person_ids=(canonical,), order="capture_desc"
    )
    # The legacy assets endpoint permits a final empty continuation. A personal album
    # reads one bounded lookahead instead, so "more photos" always means a real next photo.
    page["has_more"] = len(page["assets"]) > limit
    page["assets"] = page["assets"][:limit]
    page["limit"] = limit
    page["next_cursor"] = None
    if page["has_more"]:
        last_id = int(page["assets"][-1]["asset_id"])
        metadata = catalog.read_one(
            "SELECT taken_at_sort FROM asset_metadata WHERE asset_id = ?", (last_id,)
        )
        sort_key = None if metadata is None else metadata["taken_at_sort"]
        continuation = encode_capture_cursor(
            int(sort_key is not None), int(sort_key or 0), last_id, page["catalog_filter_revision"]
        )
        page["next_cursor"] = _encode(binding, continuation)
    for item in page["assets"]:
        item.pop("current_location", None)
        item["taken_time"] = asset_view(catalog.connection, int(item["asset_id"]))
        item.update(
            _photo_person_faces(
                catalog.connection, asset_id=int(item["asset_id"]), person_id=canonical
            )
        )
    row = catalog.read_one("SELECT * FROM persons WHERE person_id = ?", (canonical,))
    page["person"] = _album_summary(catalog.connection, row)
    return page


def _face_view(catalog: Any, row: Any) -> dict[str, Any]:
    face_id = str(row["face_id"])
    mode = row["mode"] or "AUTO_ALLOWED"
    effective = catalog.read_one(
        "SELECT l.person_id FROM face_person_links AS l WHERE l.face_id = ? AND "
        + MEMBER_PREDICATE,
        (face_id,),
    )
    suggested = catalog.read_all(
        "SELECT l.person_id FROM face_person_links AS l JOIN persons AS p ON p.person_id = l.person_id WHERE l.face_id = ? AND l.decision = 'SUGGESTED' AND p.merged_into IS NULL AND NOT EXISTS (SELECT 1 FROM face_exclusions AS e WHERE e.face_id = l.face_id AND e.person_id = l.person_id AND e.active = 1) ORDER BY l.confidence DESC, l.person_id LIMIT 3",
        (face_id,),
    )
    suggestions = [
        _album_summary(
            catalog.connection,
            catalog.read_one("SELECT * FROM persons WHERE person_id = ?", (int(r["person_id"]),)),
        )
        for r in suggested
    ]
    assigned_id = (
        None
        if effective is None
        else resolve_canonical_person_id(catalog.connection, int(effective["person_id"]))
    )
    assigned = (
        None
        if assigned_id is None
        else catalog.read_one("SELECT name FROM persons WHERE person_id = ?", (assigned_id,))
    )
    box = json.loads(str(row["bbox_json"]))
    return {
        "face_id": face_id,
        "asset_id": int(row["asset_id"]),
        "mode": mode,
        "control_revision": int(row["control_revision"] or 0),
        "assigned_person_id": assigned_id,
        "assigned_person_name": None if assigned is None else assigned["name"],
        "thumbnail_url": f"/api/v1/faces/{face_id}/thumbnail",
        "box": [float(value) for value in box],
        "quality_reason": row["quality_reason"],
        "suggestions": suggestions,
    }


_SELECT_FACES = "SELECT f.face_id, f.asset_id, f.bbox_json, f.quality_reason, c.mode, c.revision AS control_revision FROM faces AS f LEFT JOIN face_assignment_controls AS c ON c.face_id = f.face_id "
_NOT_HELD = "NOT EXISTS (SELECT 1 FROM face_redetection_groups AS g WHERE g.state = 'OPEN' AND (g.old_face_id = f.face_id OR EXISTS (SELECT 1 FROM face_redetection_mappings AS m WHERE m.old_face_id = g.old_face_id AND m.new_face_id = f.face_id AND m.state = 'SUGGESTED')))"
# An untouched v1 library remains browsable. After a v2 publication (even NO_FACE),
# obsolete machine faces leave this work queue; human-controlled anchors remain protected.
_VISIBLE = "(f.spec_revision = ? OR c.mode IN ('MANUAL_ASSIGNED', 'MANUAL_UNASSIGNED') OR EXISTS (SELECT 1 FROM face_exclusions AS e WHERE e.face_id = f.face_id AND e.active = 1) OR (NOT EXISTS (SELECT 1 FROM faces AS n WHERE n.asset_id = f.asset_id AND n.spec_revision = ?) AND NOT EXISTS (SELECT 1 FROM artifacts AS a WHERE a.processor_id = ? AND a.input_revision = f.input_revision AND a.artifact_revision = ? AND a.lifecycle_state = 'PUBLISHED')))"
_VISIBILITY_PARAMETERS = (
    FACE_SPEC_REVISION,
    FACE_SPEC_REVISION,
    FACE_PROCESSOR_ID,
    FACE_ARTIFACT_REVISION,
)


def inbox_page(catalog: Any, *, limit: int, cursor: str | None) -> dict[str, Any]:
    binding = ["faceinbox1", read_filter_revision(catalog.connection)]
    after = _position(cursor, binding, "")
    if not isinstance(after, str) or (
        after and (len(after) != 64 or any(c not in "0123456789abcdef" for c in after))
    ):
        raise ApiError(INVALID_CURSOR, "invalid face position", status=422)
    rows = catalog.read_all(
        _SELECT_FACES
        + "WHERE f.face_id > ? AND "
        + _VISIBLE
        + " AND NOT EXISTS (SELECT 1 FROM face_person_links AS l WHERE l.face_id = f.face_id AND "
        + MEMBER_PREDICATE
        + ") AND "
        + _NOT_HELD
        + " ORDER BY f.face_id LIMIT ?",
        (after, *_VISIBILITY_PARAMETERS, limit + 1),
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "faces": [_face_view(catalog, row) for row in rows],
        "has_more": has_more,
        "next_cursor": _encode(binding, str(rows[-1]["face_id"])) if has_more else None,
    }


def asset_faces(catalog: Any, asset_id: int) -> dict[str, Any]:
    from photo_search.catalog.errors import ASSET_NOT_FOUND, CatalogError

    if catalog.read_one("SELECT 1 FROM assets WHERE asset_id = ?", (asset_id,)) is None:
        raise CatalogError(ASSET_NOT_FOUND, "the photo does not exist")
    rows = catalog.read_all(
        _SELECT_FACES
        + "WHERE f.asset_id = ? AND "
        + _VISIBLE
        + " AND "
        + _NOT_HELD
        + " ORDER BY f.face_id LIMIT 201",
        (asset_id, *_VISIBILITY_PARAMETERS),
    )
    return {"faces": [_face_view(catalog, row) for row in rows[:200]], "has_more": len(rows) > 200}
