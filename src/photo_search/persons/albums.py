"""Bounded personal people/album projections over existing authoritative facts."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from photo_search.api.errors import INVALID_CURSOR, VALIDATION_ERROR, ApiError
from photo_search.api.queries import asset_page, encode_capture_cursor
from photo_search.face.contract import (
    FACE_ARTIFACT_REVISION,
    FACE_DETAIL_SPEC_REVISION,
    FACE_PROCESSOR_ID,
    FACE_SPEC_REVISION,
)
from photo_search.face.detail_policy import superseded_machine_sql
from photo_search.persons.contracts import PERSON_REVISION_CONFLICT, fail
from photo_search.persons.identity import resolve_canonical_person_id
from photo_search.persons.members import MEMBER_PREDICATE
from photo_search.persons.read import person_summary
from photo_search.places.revision import read_filter_revision
from photo_search.taken_time import contract as taken_time_contract
from photo_search.taken_time.read import rules_version_matches


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


def _album_summaries(
    connection: Any, rows: list[Any], *, reuse_asset_counts: bool = False
) -> list[dict[str, Any]]:
    """Project one bounded people page with constant-count member/cover reads."""
    if not rows:
        return []
    values = ", ".join("(?, ?)" for _ in rows)
    parameters = tuple(
        value
        for row in rows
        for value in (int(row["person_id"]), row["avatar_face_id"] or "")
    )
    page = f"WITH page(person_id, stored_avatar) AS (VALUES {values}) "
    asset_count_column = (
        "" if reuse_asset_counts else ", COUNT(DISTINCT f.asset_id) AS asset_count"
    )
    members = connection.execute(
        page
        + "SELECT page.person_id, COUNT(*) AS face_count"
        + asset_count_column
        + " "
        "FROM page JOIN face_person_links AS l ON l.person_id = page.person_id "
        "JOIN faces AS f ON f.face_id = l.face_id WHERE "
        + MEMBER_PREDICATE
        + " GROUP BY page.person_id",
        parameters,
    ).fetchall()
    page_asset_counts = (
        {int(row["person_id"]): int(row["page_asset_count"]) for row in rows}
        if reuse_asset_counts
        else {}
    )
    counts = {
        int(member["person_id"]): (
            int(member["face_count"]),
            page_asset_counts[int(member["person_id"])]
            if reuse_asset_counts
            else int(member["asset_count"]),
        )
        for member in members
    }
    avatars = connection.execute(
        page
        + "SELECT person_id, face_id FROM (SELECT page.person_id, f.face_id, "
        "ROW_NUMBER() OVER (PARTITION BY page.person_id ORDER BY "
        "(f.face_id = page.stored_avatar) DESC, (f.quality_state = 'OK') DESC, "
        "(f.spec_revision = ?) DESC, f.face_pixels DESC, f.face_id) AS position "
        "FROM page JOIN face_person_links AS l ON l.person_id = page.person_id "
        "JOIN faces AS f ON f.face_id = l.face_id JOIN assets AS a ON a.asset_id = f.asset_id "
        "WHERE a.state = 'ACTIVE' AND "
        + MEMBER_PREDICATE
        + ") WHERE position = 1",
        (*parameters, FACE_SPEC_REVISION),
    ).fetchall()
    avatar_ids = {int(avatar["person_id"]): str(avatar["face_id"]) for avatar in avatars}
    summaries = []
    for row in rows:
        person_id = int(row["person_id"])
        face_count, asset_count = counts.get(person_id, (0, 0))
        face_id = avatar_ids.get(person_id)
        summaries.append(
            {
                "person_id": person_id,
                "name": None if row["name"] is None else str(row["name"]),
                "revision": int(row["revision"]),
                "avatar_face_id": face_id,
                "face_count": face_count,
                "asset_count": asset_count,
                "thumbnail_url": None if face_id is None else f"/api/v1/faces/{face_id}/thumbnail",
            }
        )
    return summaries


def set_cover(
    catalog: Any, *, person_id: int, face_id: str, expected_revision: int
) -> dict[str, Any]:
    """Replace display metadata, keeping face ownership and all derived indexes intact."""
    with catalog.write() as connection:
        canonical = resolve_canonical_person_id(connection, person_id)
        row = connection.execute(
            "SELECT * FROM persons WHERE person_id = ?", (canonical,)
        ).fetchone()
        if int(row["revision"]) != expected_revision:
            raise fail(
                PERSON_REVISION_CONFLICT,
                "the current person revision is not the expected one",
                person_id=person_id,
                canonical_person_id=canonical,
                expected_revision=expected_revision,
                current_revision=int(row["revision"]),
            )
        member = connection.execute(
            "SELECT 1 FROM face_person_links AS l JOIN faces AS f ON f.face_id = l.face_id "
            "JOIN assets AS a ON a.asset_id = f.asset_id "
            "WHERE l.person_id = ? AND l.face_id = ? AND a.state = 'ACTIVE' AND "
            + MEMBER_PREDICATE,
            (canonical, face_id),
        ).fetchone()
        if member is None:
            raise ApiError(
                VALIDATION_ERROR, "choose a current face of this person as cover", status=422
            )
        changed = row["avatar_face_id"] != face_id
        if changed:
            connection.execute(
                "UPDATE persons SET avatar_face_id = ?, revision = revision + 1, updated_at = ? "
                "WHERE person_id = ?",
                (face_id, catalog.now_ms(), canonical),
            )
            row = connection.execute(
                "SELECT * FROM persons WHERE person_id = ?", (canonical,)
            ).fetchone()
        return {"changed": changed, "person": _album_summary(connection, row)}


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


def _photo_page_facts(
    connection: Any, *, asset_ids: list[int], person_id: int
) -> tuple[dict[int, dict[str, Any] | None], dict[int, dict[str, Any]]]:
    """Read time and member-face projections for only the current bounded photo page."""
    if not asset_ids:
        return {}, {}
    placeholders = ", ".join("?" for _ in asset_ids)
    time_views: dict[int, dict[str, Any] | None] = {asset_id: None for asset_id in asset_ids}
    if rules_version_matches(connection):
        rows = connection.execute(
            "SELECT asset_id, taken_at_sort, time_source, time_confidence, "
            "timezone_offset, camera FROM asset_metadata "
            f"WHERE asset_id IN ({placeholders})",
            tuple(asset_ids),
        ).fetchall()
        for row in rows:
            sort_key = None if row["taken_at_sort"] is None else int(row["taken_at_sort"])
            source = str(row["time_source"])
            time_views[int(row["asset_id"])] = {
                "taken_at": (
                    None
                    if sort_key is None
                    else taken_time_contract.normalize_bound(sort_key)
                ),
                "taken_at_sort": sort_key,
                "time_source": source,
                "time_confidence": str(row["time_confidence"]),
                "timezone_offset_minutes": (
                    None if row["timezone_offset"] is None else int(row["timezone_offset"])
                ),
                "label": taken_time_contract.label_for(source),
                "camera": None if row["camera"] is None else str(row["camera"]),
            }
    face_rows = connection.execute(
        "SELECT asset_id, face_id, position FROM (SELECT f.asset_id, f.face_id, "
        "ROW_NUMBER() OVER (PARTITION BY f.asset_id ORDER BY "
        "(f.spec_revision = ?) DESC, f.face_pixels DESC, f.face_id) AS position "
        "FROM faces AS f JOIN face_person_links AS l ON l.face_id = f.face_id "
        f"WHERE f.asset_id IN ({placeholders}) AND l.person_id = ? AND "
        + MEMBER_PREDICATE
        + ") WHERE position <= 9 ORDER BY asset_id, position",
        (FACE_SPEC_REVISION, *asset_ids, int(person_id)),
    ).fetchall()
    grouped: dict[int, list[str]] = {}
    for row in face_rows:
        grouped.setdefault(int(row["asset_id"]), []).append(str(row["face_id"]))
    faces = {
        asset_id: {
            "person_faces": [
                {"face_id": face_id, "thumbnail_url": f"/api/v1/faces/{face_id}/thumbnail"}
                for face_id in face_ids[:8]
            ],
            "person_faces_has_more": len(face_ids) > 8,
        }
        for asset_id, face_ids in grouped.items()
    }
    return time_views, faces


def people_page(
    catalog: Any,
    *,
    kind: str,
    q: str,
    limit: int,
    cursor: str | None,
    sort: str = "person_id",
    visibility: str = "all",
) -> dict[str, Any]:
    if (
        kind not in ("all", "named", "unnamed")
        or sort not in ("person_id", "photos_desc")
        or visibility not in ("all", "visible", "hidden")
        or len(q) > 200
    ):
        raise ApiError(VALIDATION_ERROR, "invalid person query", status=422)
    query = q.strip()
    # Keep the legacy person_id cursor binding byte-for-byte compatible. Each sort mode
    # has its own version prefix, so a cursor cannot cross from one ordering to the other.
    binding = [
        "people1" if sort == "person_id" else "people-count1",
        kind,
        query,
        read_filter_revision(catalog.connection),
    ]
    if visibility != "all":
        binding = [binding[0] + "-visibility1", visibility, *binding[1:]]
    after = _position(cursor, binding, 0 if sort == "person_id" else None)
    candidate_conditions = ["p.merged_into IS NULL"]
    candidate_parameters: list[Any] = []
    if visibility == "visible":
        candidate_conditions.append(
            "NOT EXISTS (SELECT 1 FROM person_hidden AS h WHERE h.person_id = p.person_id)"
        )
    elif visibility == "hidden":
        candidate_conditions.append(
            "EXISTS (SELECT 1 FROM person_hidden AS h WHERE h.person_id = p.person_id)"
        )
    if kind == "named":
        candidate_conditions.append("p.name IS NOT NULL")
    elif kind == "unnamed":
        candidate_conditions.append("p.name IS NULL")
        # A correction can move the last face away while retaining the identity
        # for history/undo. Filter empty albums before pagination, without hiding
        # other unnamed faces in a shared photo or deleting those identities.
        candidate_conditions.append(
            "EXISTS (SELECT 1 FROM face_person_links AS l "
            "JOIN faces AS f ON f.face_id = l.face_id "
            "JOIN assets AS member_asset ON member_asset.asset_id = f.asset_id "
            "WHERE l.person_id = p.person_id AND member_asset.state = 'ACTIVE' AND "
            + MEMBER_PREDICATE
            + ")"
        )
    if query:
        # instr is literal, so '%'/'_' have no surprising wildcard meaning.
        candidate_conditions.append(
            "(instr(p.name, ?) > 0 OR EXISTS (SELECT 1 FROM person_aliases AS a WHERE a.person_id = p.person_id AND instr(a.alias, ?) > 0))"
        )
        candidate_parameters.extend([query, query])
    total_row = catalog.read_one(
        "SELECT COUNT(*) AS total FROM persons AS p WHERE "
        + " AND ".join(candidate_conditions),
        tuple(candidate_parameters),
    )
    total = int(total_row["total"])
    if sort == "person_id":
        if type(after) is not int or after < 0:
            raise ApiError(INVALID_CURSOR, "invalid person position", status=422)
        conditions = [*candidate_conditions, "p.person_id > ?"]
        parameters = [*candidate_parameters, after]
        rows = catalog.read_all(
            "SELECT p.* FROM persons AS p WHERE "
            + " AND ".join(conditions)
            + " ORDER BY p.person_id LIMIT ?",
            (*parameters, limit + 1),
        )
    else:
        if after is not None:
            if (
                not isinstance(after, list)
                or len(after) != 2
                or any(type(value) is not int or value < 0 for value in after)
            ):
                raise ApiError(INVALID_CURSOR, "invalid person position", status=422)
            after_count, after_person_id = after
        member_counts = (
            "effective_counts AS (SELECT l.person_id, COUNT(DISTINCT f.asset_id) AS asset_count "
            "FROM candidates AS candidate "
            "JOIN face_person_links AS l ON l.person_id = candidate.person_id "
            "JOIN faces AS f ON f.face_id = l.face_id WHERE "
            + MEMBER_PREDICATE
            + " GROUP BY l.person_id)"
        )
        continuation_conditions: list[str] = []
        continuation_parameters: list[Any] = []
        if after is not None:
            continuation_conditions.append(
                "(COALESCE(ec.asset_count, 0) < ? OR "
                "(COALESCE(ec.asset_count, 0) = ? AND p.person_id > ?))"
            )
            continuation_parameters.extend([after_count, after_count, after_person_id])
        rows = catalog.read_all(
            "WITH candidates AS (SELECT p.* FROM persons AS p WHERE "
            + " AND ".join(candidate_conditions)
            + "), "
            + member_counts
            + " SELECT p.*, COALESCE(ec.asset_count, 0) AS page_asset_count "
            "FROM candidates AS p LEFT JOIN effective_counts AS ec ON ec.person_id = p.person_id "
            + ("WHERE " + " AND ".join(continuation_conditions) + " " if continuation_conditions else "")
            + " ORDER BY COALESCE(ec.asset_count, 0) DESC, p.person_id LIMIT ?",
            (*candidate_parameters, *continuation_parameters, limit + 1),
        )
    has_more = len(rows) > limit
    rows = rows[:limit]
    last_position: Any = None
    if has_more and rows:
        if sort == "person_id":
            last_position = int(rows[-1]["person_id"])
        else:
            last_position = [int(rows[-1]["page_asset_count"]), int(rows[-1]["person_id"])]
    summaries = (
        _album_summaries(catalog.connection, rows, reuse_asset_counts=True)
        if sort == "photos_desc"
        else _album_summaries(catalog.connection, rows)
    )
    return {
        "persons": summaries,
        "total": total,
        "has_more": has_more,
        "next_cursor": _encode(binding, last_position) if has_more else None,
        "sort": sort,
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
    asset_ids = [int(item["asset_id"]) for item in page["assets"]]
    time_views, person_faces = _photo_page_facts(
        catalog.connection, asset_ids=asset_ids, person_id=canonical
    )
    for item in page["assets"]:
        item.pop("current_location", None)
        item_id = int(item["asset_id"])
        item["taken_time"] = time_views.get(item_id)
        item.update(
            person_faces.get(item_id, {"person_faces": [], "person_faces_has_more": False})
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
    return {
        "face_id": face_id,
        "asset_id": int(row["asset_id"]),
        "source_detail": str(row["spec_revision"]) == FACE_DETAIL_SPEC_REVISION,
        "automatic_detail": str(row["spec_revision"]) == FACE_DETAIL_SPEC_REVISION and catalog.read_one(
            "SELECT 1 FROM face_detail_auto_assets WHERE asset_id=? AND input_revision=? AND artifact_id=?",
            (int(row["asset_id"]), row["input_revision"], row["artifact_id"]),
        ) is not None,
        "mode": mode,
        "control_revision": int(row["control_revision"] or 0),
        "assigned_person_id": assigned_id,
        "assigned_person_name": None if assigned is None else assigned["name"],
        "thumbnail_url": f"/api/v1/faces/{face_id}/thumbnail",
        "quality_reason": row["quality_reason"],
        "organization_reason": row["organization_reason"],
        "suggestions": suggestions,
    }


# Only a closed set of reason codes leaves this read projection. Scores and vectors
# remain internal, and the same expression owns list filtering and summary counts.
_ORGANIZATION_REASON = """
CASE
 WHEN c.mode = 'MANUAL_UNASSIGNED' THEN 'MANUAL_UNASSIGNED'
 WHEN f.quality_reason IN ('SMALL', 'BLURRY', 'LOW_DETECTION_SCORE') THEN f.quality_reason
 ELSE COALESCE(
  (SELECT CASE WHEN l.reason IN ('BORDERLINE', 'NEAR_TIE', 'SAME_IMAGE_CONFLICT', 'LOW_QUALITY')
    THEN l.reason ELSE 'OTHER_SUGGESTION' END
   FROM face_person_links AS l JOIN persons AS p ON p.person_id = l.person_id
   WHERE l.face_id = f.face_id AND l.decision = 'SUGGESTED' AND p.merged_into IS NULL
    AND NOT EXISTS (SELECT 1 FROM face_exclusions AS e
     WHERE e.face_id = l.face_id AND e.person_id = l.person_id AND e.active = 1)
   ORDER BY l.confidence DESC, l.person_id LIMIT 1),
  CASE WHEN EXISTS (SELECT 1 FROM face_index_entries AS i
    JOIN face_index_generations AS g ON g.generation_id = i.generation_id
    WHERE i.face_id = f.face_id AND i.artifact_id = f.artifact_id
     AND i.state = 'CONFIRMED' AND g.state = 'READY' AND g.spec_revision = f.spec_revision)
   THEN 'NO_MATCH' ELSE 'WAITING_INDEX' END)
END
"""
_FACE_FROM = "FROM faces AS f LEFT JOIN face_assignment_controls AS c ON c.face_id = f.face_id "
_SELECT_FACES = (
    "SELECT f.face_id, f.asset_id, f.spec_revision, f.input_revision, f.artifact_id, f.quality_reason, c.mode, c.revision AS control_revision, "
    + _ORGANIZATION_REASON
    + " AS organization_reason "
    + _FACE_FROM
)
_NOT_HELD = "NOT EXISTS (SELECT 1 FROM face_redetection_groups AS g WHERE g.state = 'OPEN' AND (g.old_face_id = f.face_id OR EXISTS (SELECT 1 FROM face_redetection_mappings AS m WHERE m.old_face_id = g.old_face_id AND m.new_face_id = f.face_id AND m.state = 'SUGGESTED')))"
# An untouched v1 library remains browsable. After a v2 publication (even NO_FACE),
# obsolete machine faces leave this work queue; human-controlled anchors remain protected.
_VISIBLE = f"(f.spec_revision = '{FACE_DETAIL_SPEC_REVISION}' OR f.spec_revision = ? OR c.mode IN ('MANUAL_ASSIGNED', 'MANUAL_UNASSIGNED') OR EXISTS (SELECT 1 FROM face_exclusions AS e WHERE e.face_id = f.face_id AND e.active = 1) OR (NOT EXISTS (SELECT 1 FROM faces AS n WHERE n.asset_id = f.asset_id AND n.spec_revision = ?) AND NOT EXISTS (SELECT 1 FROM artifacts AS a WHERE a.processor_id = ? AND a.input_revision = f.input_revision AND a.artifact_revision = ? AND a.lifecycle_state = 'PUBLISHED')))"
_VISIBILITY_PARAMETERS = (
    FACE_SPEC_REVISION,
    FACE_SPEC_REVISION,
    FACE_PROCESSOR_ID,
    FACE_ARTIFACT_REVISION,
)
_VISIBLE += " AND NOT " + superseded_machine_sql()
_NO_MEMBER = (
    "NOT EXISTS (SELECT 1 FROM face_person_links AS l WHERE l.face_id = f.face_id AND "
    + MEMBER_PREDICATE
    + ")"
)
_INBOX_WHERE = _VISIBLE + " AND " + _NO_MEMBER + " AND " + _NOT_HELD
_INBOX_ELIGIBLE_WHERE = _INBOX_WHERE
_INBOX_WHERE += " AND NOT EXISTS (SELECT 1 FROM face_inbox_hidden AS h WHERE h.face_id = f.face_id)"
_INBOX_KINDS = {
    "all": "1 = 1",
    "suggested": "organization_reason IN ('BORDERLINE', 'NEAR_TIE', 'SAME_IMAGE_CONFLICT', 'OTHER_SUGGESTION')",
    "quality": "organization_reason IN ('SMALL', 'BLURRY', 'LOW_DETECTION_SCORE', 'LOW_QUALITY')",
    "manual": "organization_reason = 'MANUAL_UNASSIGNED'",
    "pending": "organization_reason IN ('NO_MATCH', 'WAITING_INDEX')",
}


def inbox_summary(catalog: Any) -> dict[str, Any]:
    rows = catalog.read_all(
        "SELECT organization_reason, COUNT(*) AS total FROM (SELECT CASE WHEN "
        + _NOT_HELD
        + " THEN "
        + _ORGANIZATION_REASON
        + " ELSE 'REDETECTION_HOLD' END AS organization_reason "
        + _FACE_FROM
        + "WHERE "
        + _VISIBLE
        + " AND "
        + _NO_MEMBER
        + " AND (NOT EXISTS (SELECT 1 FROM face_inbox_hidden AS h WHERE h.face_id=f.face_id) OR NOT ("
        + _NOT_HELD
        + "))"
        + ") GROUP BY organization_reason",
        _VISIBILITY_PARAMETERS,
    )
    counts = {str(row["organization_reason"]): int(row["total"]) for row in rows}
    held = counts.pop("REDETECTION_HOLD", 0)
    return {"total": sum(counts.values()), "reasons": counts, "held": held}


def inbox_page(
    catalog: Any, *, limit: int, cursor: str | None, kind: str = "all"
) -> dict[str, Any]:
    if kind not in _INBOX_KINDS:
        raise ApiError(VALIDATION_ERROR, "invalid face organization filter", status=422)
    binding = ["faceinbox2", read_filter_revision(catalog.connection), kind]
    after = _position(cursor, binding, "")
    if not isinstance(after, str) or (
        after and (len(after) != 64 or any(c not in "0123456789abcdef" for c in after))
    ):
        raise ApiError(INVALID_CURSOR, "invalid face position", status=422)
    rows = catalog.read_all(
        "SELECT * FROM ("
        + _SELECT_FACES
        + "WHERE f.face_id > ? AND "
        + _INBOX_WHERE
        + ") WHERE "
        + _INBOX_KINDS[kind]
        + " ORDER BY face_id LIMIT ?",
        (after, *_VISIBILITY_PARAMETERS, limit + 1),
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "faces": [_face_view(catalog, row) for row in rows],
        "has_more": has_more,
        "next_cursor": _encode(binding, str(rows[-1]["face_id"])) if has_more else None,
    }


def inbox_hidden_page(catalog: Any, *, limit: int, cursor: str | None) -> dict[str, Any]:
    binding = ["faceinboxhidden1"]
    after = _position(cursor, binding, "")
    if not isinstance(after, str) or (
        after and (len(after) != 64 or any(c not in "0123456789abcdef" for c in after))
    ):
        raise ApiError(INVALID_CURSOR, "invalid face position", status=422)
    rows = catalog.read_all(
        _SELECT_FACES
        + "JOIN face_inbox_hidden AS h ON h.face_id=f.face_id WHERE f.face_id>? "
        + "ORDER BY f.face_id LIMIT ?",
        (after, limit + 1),
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "faces": [_face_view(catalog, row) for row in rows],
        "has_more": has_more,
        "next_cursor": _encode(binding, str(rows[-1]["face_id"])) if has_more else None,
    }


def set_inbox_visibility(catalog: Any, *, face_ids: list[str], hidden: bool) -> dict[str, Any]:
    """Hide or restore explicit face detections atomically without changing their ownership."""
    if not 1 <= len(face_ids) <= 500 or len(set(face_ids)) != len(face_ids):
        raise ApiError(VALIDATION_ERROR, "face_ids must contain 1 to 500 distinct faces", status=422)
    with catalog.write() as connection:
        for face_id in face_ids:
            exists = connection.execute("SELECT 1 FROM faces WHERE face_id=?", (face_id,)).fetchone()
            if exists is None:
                raise ApiError(VALIDATION_ERROR, "face does not exist", status=422, details={"face_id": face_id})
            marked = connection.execute(
                "SELECT 1 FROM face_inbox_hidden WHERE face_id=?", (face_id,)
            ).fetchone() is not None
            if hidden and not marked:
                eligible = connection.execute(
                    _SELECT_FACES + "WHERE f.face_id=? AND " + _INBOX_ELIGIBLE_WHERE,
                    (face_id, *_VISIBILITY_PARAMETERS),
                ).fetchone()
                if eligible is None:
                    raise ApiError(
                        VALIDATION_ERROR,
                        "only faces currently in the organization inbox can be hidden",
                        status=422,
                        details={"face_id": face_id},
                    )
        changed = 0
        for face_id in face_ids:
            if hidden:
                result = connection.execute(
                    "INSERT OR IGNORE INTO face_inbox_hidden(face_id) VALUES (?)", (face_id,)
                )
            else:
                result = connection.execute(
                    "DELETE FROM face_inbox_hidden WHERE face_id=?", (face_id,)
                )
            changed += max(0, int(result.rowcount))
    return {"hidden": bool(hidden), "face_count": len(face_ids), "changed": changed}


def _inbox_group_cte(kind: str) -> tuple[str, tuple[Any, ...]]:
    if kind not in _INBOX_KINDS:
        raise ApiError(VALIDATION_ERROR, "invalid face organization filter", status=422)
    eligible = (
        "SELECT inbox.*, (SELECT p.person_id FROM face_person_links AS l "
        "JOIN persons AS p ON p.person_id = l.person_id WHERE l.face_id = inbox.face_id "
        "AND l.decision = 'SUGGESTED' AND p.merged_into IS NULL "
        "AND inbox.organization_reason IN ('BORDERLINE','NEAR_TIE','SAME_IMAGE_CONFLICT','OTHER_SUGGESTION') "
        "AND NOT EXISTS (SELECT 1 FROM face_exclusions AS e WHERE e.face_id=l.face_id "
        "AND e.person_id=l.person_id AND e.active=1) "
        "ORDER BY l.confidence DESC, p.person_id ASC LIMIT 1) AS candidate_person_id "
        "FROM (" + _SELECT_FACES + "WHERE " + _INBOX_WHERE + ") AS inbox WHERE " + _INBOX_KINDS[kind]
    )
    return "WITH eligible AS (" + eligible + "), group_counts AS (" \
        "SELECT candidate_person_id, COUNT(DISTINCT asset_id) AS photo_count, " \
        "MIN(face_id) AS representative_face_id FROM eligible WHERE candidate_person_id IS NOT NULL " \
        "GROUP BY candidate_person_id HAVING COUNT(DISTINCT asset_id) >= 2), cards AS (" \
        "SELECT 'candidate_group' AS card_type, 'g:' || printf('%020d', g.candidate_person_id) AS stable_key, " \
        "g.photo_count, g.candidate_person_id, g.representative_face_id, p.name AS candidate_name, NULL AS face_id " \
        "FROM group_counts AS g JOIN persons AS p ON p.person_id=g.candidate_person_id " \
        "UNION ALL SELECT 'single_face', 'f:' || e.face_id, 1, NULL, e.face_id, NULL, e.face_id " \
        "FROM eligible AS e LEFT JOIN group_counts AS g ON g.candidate_person_id=e.candidate_person_id " \
        "WHERE e.candidate_person_id IS NULL OR g.candidate_person_id IS NULL) ", _VISIBILITY_PARAMETERS


def inbox_group_page(
    catalog: Any, *, limit: int, cursor: str | None, kind: str = "all"
) -> dict[str, Any]:
    cte, visibility_parameters = _inbox_group_cte(kind)
    binding = ["faceinboxgroups1", read_filter_revision(catalog.connection), kind]
    after = _position(cursor, binding, None)
    if after is not None and (
        not isinstance(after, list) or len(after) != 2
        or not isinstance(after[0], int) or after[0] < 1
        or not isinstance(after[1], str) or len(after[1]) > 100
    ):
        raise ApiError(INVALID_CURSOR, "invalid inbox group position", status=422)
    predicate = "" if after is None else "WHERE photo_count < ? OR (photo_count = ? AND stable_key > ?)"
    params = visibility_parameters + (() if after is None else (after[0], after[0], after[1])) + (limit + 1,)
    rows = catalog.read_all(
        cte + "SELECT * FROM cards " + predicate + " ORDER BY photo_count DESC, stable_key ASC LIMIT ?",
        params,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    cards: list[dict[str, Any]] = []
    for row in rows:
        if row["card_type"] == "candidate_group":
            face = catalog.read_one(
                _SELECT_FACES + "WHERE f.face_id = ? AND " + _INBOX_WHERE,
                (row["representative_face_id"], *_VISIBILITY_PARAMETERS),
            )
            cards.append({
                "type": "candidate_group",
                "group_key": f"candidate:{int(row['candidate_person_id'])}",
                "candidate_name": row["candidate_name"],
                "photo_count": int(row["photo_count"]),
                "face": _face_view(catalog, face),
            })
        else:
            face = catalog.read_one(
                _SELECT_FACES + "WHERE f.face_id = ? AND " + _INBOX_WHERE,
                (row["face_id"], *_VISIBILITY_PARAMETERS),
            )
            cards.append({"type": "single_face", "photo_count": 1, "face": _face_view(catalog, face)})
    return {
        "cards": cards,
        "has_more": has_more,
        "next_cursor": _encode(binding, [int(rows[-1]["photo_count"]), str(rows[-1]["stable_key"])])
        if has_more else None,
    }


def inbox_group_faces(
    catalog: Any, *, group_key: str, limit: int, cursor: str | None, kind: str = "all"
) -> dict[str, Any]:
    if not group_key.startswith("candidate:") or not group_key[10:].isdigit():
        raise ApiError(VALIDATION_ERROR, "invalid candidate group", status=422)
    person_id = int(group_key[10:])
    cte, visibility_parameters = _inbox_group_cte(kind)
    binding = ["faceinboxgroupfaces1", read_filter_revision(catalog.connection), kind, person_id]
    after = _position(cursor, binding, "")
    if not isinstance(after, str) or (after and (len(after) != 64 or any(c not in "0123456789abcdef" for c in after))):
        raise ApiError(INVALID_CURSOR, "invalid candidate group position", status=422)
    rows = catalog.read_all(
        cte + "SELECT e.* FROM eligible AS e JOIN group_counts AS g "
        "ON g.candidate_person_id=e.candidate_person_id WHERE e.candidate_person_id=? AND e.face_id>? "
        "ORDER BY e.face_id LIMIT ?",
        visibility_parameters + (person_id, after, limit + 1),
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "group_key": group_key,
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
        + f" AND ({_NOT_HELD} OR f.spec_revision = '{FACE_DETAIL_SPEC_REVISION}')"
        + " ORDER BY f.face_id LIMIT 201",
        (asset_id, *_VISIBILITY_PARAMETERS),
    )
    return {"faces": [_face_view(catalog, row) for row in rows[:200]], "has_more": len(rows) > 200}
