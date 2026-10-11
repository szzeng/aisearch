"""Read-only projections behind the P3-06 person page (list, faces, operations).

Everything here reads the authoritative SQLite facts and nothing else: no operation is
written, no derived state is created, and no field that ADR-0006/0007/0008 keep out of a
response (a ``name_key``, an alias list, a before/after snapshot, a face box, a landmark, a
vector or a similarity) can reach a caller through these projections.

Each of the three lists pages with its own opaque keyset cursor, so a cursor minted by one
list is refused by another instead of silently resuming at a meaningless position. The
canonical/redirect rules are the ones :mod:`photo_search.persons.identity` already owns: a
merged id is answered with its canonical person/faces instead of a redirect or an error.
"""

from __future__ import annotations

import base64
import binascii
import sqlite3
from typing import Any

from photo_search.api.errors import INVALID_CURSOR, VALIDATION_ERROR, ApiError
from photo_search.catalog.errors import FACE_MAPPING_NOT_FOUND, CatalogError
from photo_search.persons import mapping as mapping_module
from photo_search.persons import operations as operation_module
from photo_search.persons.contracts import ENTITY_TYPE_FACE_CONTROL
from photo_search.persons.identity import resolve_canonical_person_id, resolve_redirect
from photo_search.persons.members import MEMBER_PREDICATE, member_counts

#: One token space per list: ``pp1`` persons, ``pf1`` faces, ``po1`` person operations.
PERSON_CURSOR_PREFIX = "pp1"
FACE_CURSOR_PREFIX = "pf1"
OPERATION_CURSOR_PREFIX = "po1"
#: ADR-0010 §8.1: the mapping review list pages in its own independent cursor space.
MAPPING_CURSOR_PREFIX = "fm1"

#: ADR-0010 §8.1: the mapping list *rejects* an out-of-range limit instead of clamping it,
#: because the caller's bound is part of the frozen surface of that endpoint.
MAPPING_STATES = ("open", "all")

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200

#: ``kind`` of ``GET /api/v1/persons`` and the two mutually exclusive face views.
PERSON_KINDS = ("all", "named", "unnamed")
MANUAL_UNASSIGNED = "manual_unassigned"
VIEW_PERSON = "person"
VIEW_MANUAL_UNASSIGNED = MANUAL_UNASSIGNED

MODE_AUTO_ALLOWED = "AUTO_ALLOWED"
MODE_MANUAL_UNASSIGNED = "MANUAL_UNASSIGNED"

#: The controlled face-avatar URL a list row may carry. It is a relative API path, never a
#: filesystem path, and the endpoint behind it decides what may be decoded.
FACE_THUMBNAIL_TEMPLATE = "/api/v1/faces/{face_id}/thumbnail"

_HEX_DIGITS = frozenset("0123456789abcdef")


# ------------------------------------------------------------------------------- cursors
def _encode(prefix: str, values: tuple[str, ...]) -> str:
    payload = ":".join((str(prefix), *(str(value) for value in values)))
    return base64.urlsafe_b64encode(payload.encode("ascii")).decode("ascii").rstrip("=")


def _decode(text: str | None, *, prefix: str, arity: int) -> list[str] | None:
    """Decode one opaque cursor; ``None`` means "start at the first page"."""
    if text is None or str(text).strip() == "":
        return None
    raw = str(text).strip()
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError) as error:
        raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid") from error
    parts = decoded.split(":")
    if len(parts) != arity + 1 or parts[0] != prefix:
        # A cursor from another list (or another version) is never reinterpreted.
        raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid")
    values = parts[1:]
    if any(value == "" for value in values):
        raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid")
    return values


def encode_person_cursor(person_id: int) -> str:
    return _encode(PERSON_CURSOR_PREFIX, (str(int(person_id)),))


def encode_face_cursor(face_id: str) -> str:
    return _encode(FACE_CURSOR_PREFIX, (str(face_id),))


def encode_operation_cursor(operation_revision: int) -> str:
    return _encode(OPERATION_CURSOR_PREFIX, (str(int(operation_revision)),))


def encode_mapping_cursor(old_face_id: str) -> str:
    return _encode(MAPPING_CURSOR_PREFIX, (str(old_face_id),))


def decode_person_cursor(text: str | None) -> int | None:
    values = _decode(text, prefix=PERSON_CURSOR_PREFIX, arity=1)
    if values is None:
        return None
    try:
        value = int(values[0])
    except ValueError as error:
        raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid") from error
    if value < 0:
        raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid")
    return value


def decode_face_cursor(text: str | None) -> str | None:
    values = _decode(text, prefix=FACE_CURSOR_PREFIX, arity=1)
    if values is None:
        return None
    value = values[0]
    if len(value) != 64 or any(char not in _HEX_DIGITS for char in value):
        raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid")
    return value


def decode_operation_cursor(text: str | None) -> int | None:
    values = _decode(text, prefix=OPERATION_CURSOR_PREFIX, arity=1)
    if values is None:
        return None
    try:
        value = int(values[0])
    except ValueError as error:
        raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid") from error
    if value < 1:
        raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid")
    return value


def decode_mapping_cursor(text: str | None) -> str | None:
    try:
        values = _decode(text, prefix=MAPPING_CURSOR_PREFIX, arity=1)
    except ApiError as error:
        # ADR-0010 §8.2 freezes ``INVALID_CURSOR`` as an *empty object* of a required field, so
        # this list has to hand the envelope an explicit empty closure instead of letting the
        # shared "an empty optional ``details`` is omitted" rule drop it. The other lists keep
        # their historical body.
        if error.code != INVALID_CURSOR:
            raise
        raise ApiError(
            INVALID_CURSOR,
            error.message,
            details={},
            closure_details=True,
        ) from error
    if values is None:
        return None
    value = values[0]
    if len(value) != 64 or any(char not in _HEX_DIGITS for char in value):
        raise ApiError(
            INVALID_CURSOR,
            "the pagination cursor is not valid",
            details={},
            closure_details=True,
        )
    return value


def clamp_page_limit(
    limit: int | None, *, default: int = DEFAULT_PAGE_LIMIT, maximum: int = MAX_PAGE_LIMIT
) -> int:
    """Apply the documented bound: an over-large limit is clamped, never rejected."""
    if limit is None:
        return int(default)
    if int(limit) < 1:
        raise ApiError(VALIDATION_ERROR, "limit must be at least 1", status=422)
    return min(int(limit), int(maximum))


def check_mapping_limit(limit: Any) -> int:
    """ADR-0010 §8.1/§8.2: 1..200 with a default of 50; anything else is a caller error.

    The bound is part of the frozen public surface, so the refusal has to answer in the frozen
    ``VALIDATION_ERROR`` closure: the only key is the required short ``reason``. A non-numeric
    ``limit`` is therefore parsed *here* rather than being handed to the transport validator,
    whose generic ``{"errors": [...]}`` body is outside that closure.
    """
    if limit is None:
        return int(DEFAULT_PAGE_LIMIT)
    try:
        value = int(str(limit).strip())
    except (TypeError, ValueError):
        value = None
    if value is None or value < 1 or value > int(MAX_PAGE_LIMIT):
        raise ApiError(
            VALIDATION_ERROR,
            f"limit must be between 1 and {MAX_PAGE_LIMIT}",
            status=422,
            details={"reason": "limit"},
        )
    return value


# ------------------------------------------------------------------------------- persons
def person_summary(connection: sqlite3.Connection, row: Any) -> dict[str, Any]:
    """One person card: identity, revision, effective counts and a controlled avatar URL."""
    person_id = int(row["person_id"])
    face_count, asset_count = member_counts(connection, person_id)
    avatar = None if row["avatar_face_id"] is None else str(row["avatar_face_id"])
    return {
        "person_id": person_id,
        "name": None if row["name"] is None else str(row["name"]),
        "revision": int(row["revision"]),
        "avatar_face_id": avatar,
        "face_count": face_count,
        "asset_count": asset_count,
        "thumbnail_url": (
            None if avatar is None else FACE_THUMBNAIL_TEMPLATE.format(face_id=avatar)
        ),
    }


def person_page(
    catalog: Any, *, limit: int, cursor: str | None = None, kind: str = "all"
) -> dict[str, Any]:
    """One bounded page of *active canonical* persons in stable ``person_id`` order.

    A merged id never appears twice: the row is filtered out by ``merged_into IS NULL``, so
    the list can only ever show the person an id now resolves to.
    """
    if str(kind) not in PERSON_KINDS:
        raise ApiError(
            VALIDATION_ERROR,
            "kind must be all, named or unnamed",
            status=422,
            details={"kind": str(kind)},
        )
    after = decode_person_cursor(cursor)
    conditions = ["p.merged_into IS NULL", "p.person_id > ?"]
    parameters: list[Any] = [0 if after is None else int(after)]
    if str(kind) == "named":
        conditions.append("p.name IS NOT NULL")
    elif str(kind) == "unnamed":
        conditions.append("p.name IS NULL")
    else:
        conditions.append(
            "(p.name IS NOT NULL OR EXISTS (SELECT 1 FROM face_person_links AS l "
            "JOIN faces AS f ON f.face_id=l.face_id WHERE l.person_id=p.person_id AND "
            + MEMBER_PREDICATE
            + "))"
        )
    parameters.append(int(limit) + 1)
    rows = list(
        catalog.read_all(
            "SELECT p.person_id, p.name, p.avatar_face_id, p.revision FROM persons AS p "
            f"WHERE {' AND '.join(conditions)} ORDER BY p.person_id LIMIT ?",
            tuple(parameters),
        )
    )
    has_more = len(rows) > int(limit)
    rows = rows[: int(limit)]
    return {
        "persons": [person_summary(catalog.connection, row) for row in rows],
        "limit": int(limit),
        "next_cursor": (
            encode_person_cursor(int(rows[-1]["person_id"])) if has_more and rows else None
        ),
        "has_more": has_more,
        "kind": str(kind),
    }


# --------------------------------------------------------------------------------- faces
_FACE_PERSON_SQL = (
    "SELECT l.face_id AS face_id, f.asset_id AS asset_id, "
    "       c.mode AS control_mode, c.revision AS control_revision, "
    "       l.person_id AS link_person_id, l.revision AS link_revision "
    "  FROM face_person_links AS l "
    "  JOIN faces AS f ON f.face_id = l.face_id "
    "  LEFT JOIN face_assignment_controls AS c ON c.face_id = l.face_id "
    " WHERE l.person_id = ? AND l.face_id > ? AND " + MEMBER_PREDICATE + " "
    " ORDER BY l.face_id LIMIT ?"
)

_FACE_MANUAL_UNASSIGNED_SQL = (
    "SELECT c.face_id AS face_id, f.asset_id AS asset_id, "
    "       c.mode AS control_mode, c.revision AS control_revision, "
    "       l.person_id AS link_person_id, l.revision AS link_revision "
    "  FROM face_assignment_controls AS c "
    "  JOIN faces AS f ON f.face_id = c.face_id "
    "  LEFT JOIN face_person_links AS l "
    "    ON l.face_id = c.face_id AND l.decision = 'CONFIRMED' "
    " WHERE c.mode = 'MANUAL_UNASSIGNED' AND c.face_id > ? "
    " ORDER BY c.face_id LIMIT ?"
)


def _suggested_faces(connection: sqlite3.Connection, face_ids: list[str]) -> set[str]:
    """Which of these faces carry at least one machine suggestion right now."""
    if not face_ids:
        return set()
    placeholders = ", ".join("?" for _ in face_ids)
    rows = connection.execute(
        "SELECT DISTINCT face_id FROM face_person_links "
        f"WHERE decision = 'SUGGESTED' AND face_id IN ({placeholders})",
        tuple(str(value) for value in face_ids),
    ).fetchall()
    return {str(row["face_id"]) for row in rows}


def face_summary(
    connection: sqlite3.Connection, row: Any, *, suggestions: set[str]
) -> dict[str, Any]:
    """One face row of the page: control state, effective member and a crop URL.

    The control projection matches ``GET /api/v1/faces/{face_id}/assignment``: a face with
    no control row is ``AUTO_ALLOWED`` at logical revision 0, and a
    ``MANUAL_UNASSIGNED`` face reports no effective person.
    """
    face_id = str(row["face_id"])
    mode = MODE_AUTO_ALLOWED if row["control_mode"] is None else str(row["control_mode"])
    control_revision = 0 if row["control_mode"] is None else int(row["control_revision"])
    assigned: int | None = None
    assignment_revision: int | None = None
    if row["link_person_id"] is not None and mode != MODE_MANUAL_UNASSIGNED:
        assigned = resolve_canonical_person_id(connection, int(row["link_person_id"]))
        assignment_revision = int(row["link_revision"])
    return {
        "face_id": face_id,
        "asset_id": int(row["asset_id"]),
        "mode": mode,
        "control_revision": control_revision,
        "assigned_person_id": assigned,
        "assignment_revision": assignment_revision,
        "suggestions_available": face_id in suggestions,
        "thumbnail_url": FACE_THUMBNAIL_TEMPLATE.format(face_id=face_id),
    }


def face_page(
    catalog: Any,
    *,
    limit: int,
    cursor: str | None = None,
    person_id: int | None = None,
    assignment_state: str | None = None,
) -> dict[str, Any]:
    """One bounded page of faces in one of the two mutually exclusive views.

    ``person_id`` resolves to the canonical person first (an old id is answered with the
    faces the id now owns), while ``assignment_state=manual_unassigned`` is the queue of
    faces a human deliberately took out of every automatic assignment.
    """
    if person_id is not None and assignment_state is not None:
        raise ApiError(
            VALIDATION_ERROR,
            "person_id and assignment_state are mutually exclusive",
            status=422,
        )
    if person_id is None and assignment_state is None:
        raise ApiError(
            VALIDATION_ERROR,
            "the face list needs person_id or assignment_state=manual_unassigned",
            status=422,
        )
    if assignment_state is not None and str(assignment_state) != MANUAL_UNASSIGNED:
        raise ApiError(
            VALIDATION_ERROR,
            "assignment_state must be manual_unassigned",
            status=422,
            details={"assignment_state": str(assignment_state)},
        )
    after = decode_face_cursor(cursor)
    payload: dict[str, Any] = {
        "limit": int(limit),
        "next_cursor": None,
        "has_more": False,
        "view": VIEW_PERSON if person_id is not None else VIEW_MANUAL_UNASSIGNED,
        "requested_person_id": None if person_id is None else int(person_id),
        "canonical_person_id": None,
        "redirected": False,
        "person_revision": None,
    }
    if person_id is not None:
        redirect = resolve_redirect(catalog.connection, int(person_id))
        canonical = int(redirect["canonical_person_id"])
        row = catalog.read_one("SELECT revision FROM persons WHERE person_id = ?", (canonical,))
        payload["canonical_person_id"] = canonical
        payload["redirected"] = bool(redirect["redirected"])
        payload["person_revision"] = None if row is None else int(row["revision"])
        rows = list(
            catalog.read_all(
                _FACE_PERSON_SQL,
                (canonical, "" if after is None else str(after), int(limit) + 1),
            )
        )
    else:
        rows = list(
            catalog.read_all(
                _FACE_MANUAL_UNASSIGNED_SQL,
                ("" if after is None else str(after), int(limit) + 1),
            )
        )
    has_more = len(rows) > int(limit)
    rows = rows[: int(limit)]
    suggestions = _suggested_faces(
        catalog.connection, [str(row["face_id"]) for row in rows]
    )
    payload["faces"] = [
        face_summary(catalog.connection, row, suggestions=suggestions) for row in rows
    ]
    payload["next_cursor"] = (
        encode_face_cursor(str(rows[-1]["face_id"])) if has_more and rows else None
    )
    payload["has_more"] = has_more
    return payload


# --------------------------------------------------------------- photo-scoped reviews
# ------------------------------------------------------------------ mapping review list
def mapping_page(
    catalog: Any, *, limit: Any, cursor: str | None = None, state: Any = "open"
) -> dict[str, Any]:
    """One bounded page of the face-redetection review list (ADR-0010 §8.1).

    The ordering is the canonical ``old_face_id`` order of the companion, the cursor is the
    independent ``fm1`` space, and every row is a *group summary*: ids, states, the existing
    human-control projection of the old face, a candidate count and a controlled crop URL. No
    box, landmark, IoU, vector, similarity or path can reach a caller through this projection.
    """
    if str(state) not in MAPPING_STATES:
        raise ApiError(
            VALIDATION_ERROR,
            "state must be open or all",
            status=422,
            details={"reason": "state"},
        )
    # The bound is part of the frozen surface, so it is enforced here as well as at the
    # transport: an over-large limit must never be silently clamped into a valid answer.
    limit = check_mapping_limit(limit)
    after = decode_mapping_cursor(cursor)
    conditions = ["g.old_face_id > ?"]
    parameters: list[Any] = ["" if after is None else str(after)]
    if str(state) == "open":
        conditions.append("g.state = 'OPEN'")
    parameters.append(int(limit) + 1)
    rows = list(
        catalog.read_all(
            "SELECT g.old_face_id AS old_face_id, g.asset_id AS asset_id, "
            "g.old_spec_revision AS old_spec_revision, g.candidate_state AS candidate_state, "
            "g.state AS state, g.revision AS revision FROM face_redetection_groups AS g "
            f"WHERE {' AND '.join(conditions)} ORDER BY g.old_face_id LIMIT ?",
            tuple(parameters),
        )
    )
    has_more = len(rows) > int(limit)
    rows = rows[: int(limit)]
    return {
        "contract_version": mapping_module.CONTRACT_VERSION,
        "limit": int(limit),
        "next_cursor": (
            encode_mapping_cursor(str(rows[-1]["old_face_id"])) if has_more and rows else None
        ),
        "has_more": has_more,
        "groups": [mapping_module.group_summary(catalog.connection, row) for row in rows],
    }


def mapping_group(catalog: Any, old_face_id: str) -> dict[str, Any]:
    """One review group with its live candidate array (ADR-0010 §8.1)."""
    try:
        checked = mapping_module.check_face_id(old_face_id, field="old_face_id")
    except CatalogError as error:
        if str(error.code) != VALIDATION_ERROR:
            raise
        # §8.2 freezes ``reason`` as the *required* key of a ``VALIDATION_ERROR``, so a shape
        # failure of the path parameter has to carry it instead of answering with a bare message.
        raise ApiError(
            VALIDATION_ERROR,
            error.message,
            status=422,
            details={"reason": str(error.details.get("reason") or "old_face_id")},
        ) from error
    row = mapping_module.group_row(catalog.connection, checked)
    if row is None:
        raise ApiError(
            FACE_MAPPING_NOT_FOUND,
            "this old face has no reviewable redetection group",
            status=404,
            details={"old_face_id": checked},
        )
    return mapping_module.group_detail(catalog.connection, row)


# ---------------------------------------------------------------------- operations log
def asset_mapping_page(
    catalog: Any, asset_id: int, *, limit: Any = None, cursor: str | None = None
) -> dict[str, Any]:
    """Read OPEN reviews of this photo using an asset-bound keyset cursor.

    Reuse the frozen summary projection and start from the indexed asset's faces.
    Candidates are fetched through the existing detail endpoint only when reviewed.
    """
    from photo_search.catalog.errors import ASSET_NOT_FOUND

    limit_value = check_mapping_limit(limit)
    values = _decode(cursor, prefix="fa1", arity=2)
    after = ""
    if values is not None:
        if values[0] != str(asset_id) or len(values[1]) != 64 or any(
            char not in "0123456789abcdef" for char in values[1]
        ):
            raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid")
        after = values[1]
    if catalog.read_one("SELECT 1 FROM assets WHERE asset_id=?", (asset_id,)) is None:
        raise CatalogError(ASSET_NOT_FOUND, "the photo does not exist")
    rows = list(catalog.read_all(
        "SELECT g.old_face_id, g.asset_id, g.old_spec_revision, g.candidate_state, "
        "g.state, g.revision FROM faces AS f INDEXED BY idx_faces_asset "
        "JOIN face_redetection_groups AS g ON g.old_face_id=f.face_id "
        "WHERE f.asset_id=? AND g.asset_id=? AND g.state='OPEN' AND g.old_face_id>? "
        "ORDER BY g.old_face_id LIMIT ?",
        (asset_id, asset_id, after, limit_value + 1),
    ))
    has_more = len(rows) > limit_value
    rows = rows[:limit_value]
    groups = []
    for row in rows:
        summary = mapping_module.group_summary(catalog.connection, row)
        summary["has_active_exclusions"] = catalog.read_one(
            "SELECT 1 FROM face_exclusions WHERE face_id=? AND active=1 LIMIT 1",
            (str(row["old_face_id"]),),
        ) is not None
        groups.append(summary)
    return {
        "contract_version": mapping_module.CONTRACT_VERSION,
        "limit": limit_value,
        "has_more": has_more,
        "next_cursor": _encode("fa1", (str(asset_id), str(rows[-1]["old_face_id"])))
        if has_more and rows else None,
        "groups": groups,
    }


# ---------------------------------------------------------------------- operations log
_OPERATION_COLUMNS = (
    "operation_id, type, state, operation_revision, undo_of_operation_id, undone_by, "
    "target_person_id, catalog_filter_revision, created_at, committed_at"
)

_ITEM_COLUMNS = (
    "operation_id, item_index, entity_type, entity_id, entity_key, before_value_json, "
    "after_value_json, after_revision"
)


def _items_of(
    connection: sqlite3.Connection, operation_ids: list[str]
) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {str(value): [] for value in operation_ids}
    if not operation_ids:
        return grouped
    placeholders = ", ".join("?" for _ in operation_ids)
    rows = connection.execute(
        f"SELECT {_ITEM_COLUMNS} FROM person_operation_items "
        f"WHERE operation_id IN ({placeholders}) ORDER BY operation_id, item_index",
        tuple(str(value) for value in operation_ids),
    ).fetchall()
    for row in rows:
        grouped.setdefault(str(row["operation_id"]), []).append(row)
    return grouped


def operation_summary(
    connection: sqlite3.Connection, row: Any, items: list[Any]
) -> dict[str, Any]:
    """One operation line: identity, status and the *ids* the operation touched.

    The before/after snapshots are read only to answer ``changed`` through the same
    projections the write path already uses; a name, an alias, a face box or a similarity
    never leaves this function. ``request_id`` and ``payload_sha256`` are deliberately not
    part of the projection either.
    """
    from photo_search.persons import retention as retention_module

    retained = retention_module.history_projection(
        connection, str(row["operation_id"]), row
    )
    if retained is not None:
        return retained
    person_ids: set[int] = set()
    face_ids: set[str] = set()
    changed = False
    for item in items:
        projection = operation_module.item_projection(item)
        if bool(projection.get("changed")):
            changed = True
        if str(item["entity_type"]) == ENTITY_TYPE_FACE_CONTROL:
            if item["entity_key"] is not None:
                face_ids.add(str(item["entity_key"]))
        elif item["entity_id"] is not None:
            person_ids.add(int(item["entity_id"]))
    return {
        "operation_id": str(row["operation_id"]),
        "operation_type": str(row["type"]),
        "operation_revision": int(row["operation_revision"]),
        "status": "UNDONE" if str(row["state"]) == "UNDONE" else "COMMITTED",
        "undo_of_operation_id": (
            None if row["undo_of_operation_id"] is None else str(row["undo_of_operation_id"])
        ),
        "undone_by_operation_id": None if row["undone_by"] is None else str(row["undone_by"]),
        "target_person_id": (
            None if row["target_person_id"] is None else int(row["target_person_id"])
        ),
        "changed": changed,
        "item_count": len(items),
        "person_ids": sorted(person_ids),
        "face_ids": sorted(face_ids),
        "catalog_filter_revision": int(row["catalog_filter_revision"]),
        "created_at": int(row["created_at"]),
        "committed_at": int(row["committed_at"]),
        "expired": False,
    }


def operation_page(
    catalog: Any, *, limit: int, cursor: str | None = None
) -> dict[str, Any]:
    """One bounded page of the append-only person operation log, newest first.

    ``next_cursor`` carries the ``operation_revision`` of the last row of the page, so a
    continuation can only ever move to *older* operations and can never repeat one.
    """
    after = decode_operation_cursor(cursor)
    conditions: list[str] = []
    parameters: list[Any] = []
    if after is not None:
        conditions.append("operation_revision < ?")
        parameters.append(int(after))
    parameters.append(int(limit) + 1)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = list(
        catalog.read_all(
            f"SELECT {_OPERATION_COLUMNS} FROM person_operations{where} "
            "ORDER BY operation_revision DESC LIMIT ?",
            tuple(parameters),
        )
    )
    has_more = len(rows) > int(limit)
    rows = rows[: int(limit)]
    items = _items_of(catalog.connection, [str(row["operation_id"]) for row in rows])
    return {
        "operations": [
            operation_summary(catalog.connection, row, items.get(str(row["operation_id"]), []))
            for row in rows
        ],
        "limit": int(limit),
        "next_cursor": (
            encode_operation_cursor(int(rows[-1]["operation_revision"]))
            if has_more and rows
            else None
        ),
        "has_more": has_more,
    }


__all__ = [
    "DEFAULT_PAGE_LIMIT",
    "FACE_CURSOR_PREFIX",
    "FACE_THUMBNAIL_TEMPLATE",
    "MANUAL_UNASSIGNED",
    "MAPPING_CURSOR_PREFIX",
    "MAPPING_STATES",
    "MAX_PAGE_LIMIT",
    "OPERATION_CURSOR_PREFIX",
    "PERSON_CURSOR_PREFIX",
    "PERSON_KINDS",
    "VIEW_MANUAL_UNASSIGNED",
    "VIEW_PERSON",
    "clamp_page_limit",
    "check_mapping_limit",
    "decode_mapping_cursor",
    "decode_face_cursor",
    "decode_operation_cursor",
    "decode_person_cursor",
    "encode_mapping_cursor",
    "encode_face_cursor",
    "encode_operation_cursor",
    "encode_person_cursor",
    "face_page",
    "face_summary",
    "mapping_group",
    "mapping_page",
    "operation_page",
    "operation_summary",
    "person_page",
    "person_summary",
]
