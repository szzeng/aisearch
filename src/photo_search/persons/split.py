"""The P3-05B person-split use case of ADR-0008.

One user action - "these explicit ``face_id``s do not belong to that group" - creates
exactly one new *unnamed* person and makes every selected face a human member of it, inside
one ``BEGIN IMMEDIATE`` transaction. Nothing here widens the request to the other faces of
a photo, re-runs a model, rewrites a detection ``face_id``, an embedding space or a
generation, or touches the image vector index.

Split adds no second log and no new fact table: ``0013`` only widens the two *closed* CHECK
vocabularies of the shared operation tables (``SPLIT`` and ``SPLIT_GROUP``). The request
therefore reuses the global ``person_operations.request_id`` idempotency key, the
monotone ``operation_revision`` counter and the single
``POST /api/v1/person-operations/{operation_id}/undo`` endpoint of ADR-0006/ADR-0007.

Two properties are deliberately *not* re-implemented here:

* the per-face result ("the old link ends, the only ``CONFIRMED`` link becomes a
  ``MANUAL`` one to the new person, the human control advances and the canonical source
  gains an exclusion") is the ADR-0007 ``assign`` matrix. ADR-0008 §4 requires the *same*
  rules, so this module calls the face module's own writers instead of copying the matrix
  and letting the two drift apart. What ADR-0008 adds on top is the strictly-forward link
  revision: the new link sits above every link the face had before the split.
* the derived catch-up (one filter-revision bump, one ``search_projection_queue`` row per
  affected asset, ``PENDING_REPAIR`` for every already-published selected face) is the
  shared :func:`photo_search.persons.operations.publish_derived`. The image-vector
  projection refresh stays *disabled* for this family: a split changes who a face belongs
  to, never what a photo looks like.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from photo_search.catalog.errors import (
    FACE_ASSIGNMENT_CONFLICT,
    MAX_PERSON_BATCH_SIZE,
    PERSON_BATCH_TOO_LARGE,
    PERSON_OPERATION_NOT_UNDOABLE,
    PERSON_REDIRECT_CYCLE,
    PERSON_REVISION_CONFLICT,
    PERSON_SPLIT_CONFLICT,
    CatalogError,
)
from photo_search.persons import contracts
from photo_search.persons import face_correction as face_module
from photo_search.persons import operations as operation_module
from photo_search.persons.contracts import (
    canonical_json,
    check_expected_revision,
    check_person_id,
    check_request_id,
    fail,
    operation_id_for,
    payload_sha256,
)
from photo_search.persons.identity import require_person_row, resolve_canonical_person_id
from photo_search.places import revision as revision_module

#: The envelope literal of ``docs/decisions/contracts/p3-person-split-contracts.v1.json``.
CONTRACT_VERSION = "p3-person-split-contracts.v1"

#: ADR-0008 §5: the endpoint family the payload digest records, so the same face selection
#: sent to another endpoint can never be mistaken for the same request.
ENDPOINT_SPLIT = "persons.split"

#: The item type of one group snapshot. It shares the ``PERSON_BUNDLE`` *shape* (a positive
#: integer ``entity_id``) but not its semantics: a created group may legitimately not exist
#: before the transaction.
ENTITY_TYPE_SPLIT_GROUP = contracts.ENTITY_TYPE_SPLIT_GROUP

ROLE_SOURCE = "SOURCE"
ROLE_CREATED = "CREATED"

#: ADR-0008 §2: 1..500 explicit, de-duplicated faces. The bound is the shared person batch
#: bound, so the frozen ``PERSON_BATCH_TOO_LARGE`` code is what a caller sees.
MAX_SPLIT_FACES = int(MAX_PERSON_BATCH_SIZE)

_ITEM_COLUMNS = (
    "item_index, entity_type, entity_id, entity_key, before_value_json, after_value_json, "
    "after_revision"
)

#: The ``SELECT`` the replay path reads. It is the frozen operation row, nothing else.
_OPERATION_COLUMNS = (
    "operation_id, request_id, payload_sha256, type, source_person_ids_json, "
    "target_person_id, state, operation_revision, undo_of_operation_id, undone_by, "
    "catalog_filter_revision, created_at, committed_at"
)

#: The one effective-member predicate of ADR-0006 §5/§9, reused verbatim: a ``CONFIRMED``
#: link that neither a ``MANUAL_UNASSIGNED`` lock nor an active denial against *that* person
#: forbids. Suggestions are never members.
_MEMBER_SQL = (
    "SELECT l.face_id AS face_id, l.person_id AS person_id, "
    "l.assignment_kind AS assignment_kind, l.revision AS revision, "
    "l.last_operation_id AS last_operation_id "
    "FROM face_person_links AS l JOIN faces AS f ON f.face_id = l.face_id "
    "WHERE l.person_id = ? AND l.decision = 'CONFIRMED' "
    "AND NOT EXISTS (SELECT 1 FROM face_assignment_controls AS c "
    " WHERE c.face_id = l.face_id AND c.mode = 'MANUAL_UNASSIGNED') "
    "AND NOT EXISTS (SELECT 1 FROM face_exclusions AS e "
    " WHERE e.face_id = l.face_id AND e.person_id = l.person_id AND e.active = 1) "
    "ORDER BY l.face_id"
)


# ------------------------------------------------------------------------------- shape
def check_contract_version(value: Any) -> str:
    if value != CONTRACT_VERSION:
        raise fail(
            "VALIDATION_ERROR",
            f"contract_version must be {CONTRACT_VERSION}",
            field="contract_version",
        )
    return CONTRACT_VERSION


def check_source_revision(value: Any) -> int:
    """ADR-0008 §2: the source revision is a stored revision, so it starts at 1."""
    expected = check_expected_revision(value, field="source_expected_revision")
    if expected < 1:
        raise fail(
            "VALIDATION_ERROR",
            "source_expected_revision must be an integer >= 1",
            field="source_expected_revision",
        )
    return int(expected)


def normalize_selection(item: Any, *, index: int) -> dict[str, Any]:
    """One requested face: its id plus *both* revisions the user confirmed."""
    if not isinstance(item, Mapping):
        raise fail("VALIDATION_ERROR", "each face must be an object", field="faces", index=index)
    face_id = face_module.check_face_id(item.get("face_id"))
    control = check_expected_revision(
        item.get("expected_control_revision"), field="expected_control_revision"
    )
    assignment = check_expected_revision(
        item.get("expected_assignment_revision"), field="expected_assignment_revision"
    )
    if assignment < 1:
        # The effective link is a stored row, so its revision is never 0.
        raise fail(
            "VALIDATION_ERROR",
            "expected_assignment_revision must be an integer >= 1",
            field="expected_assignment_revision",
        )
    return {
        "face_id": face_id,
        "expected_control_revision": int(control),
        "expected_assignment_revision": int(assignment),
    }


def check_faces(value: Any) -> list[dict[str, Any]]:
    """Normalise the explicit face list of one split (ADR-0008 §2).

    The list is 1..500 entries, every entry names a distinct ``face_id``, and the result is
    sorted by that id so the payload hash of the same client intent never depends on the
    order the UI happened to send. An empty list, a repeated face and an oversized list are
    three different refusals; none of them is ever silently repaired.
    """
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise fail("VALIDATION_ERROR", "faces must be a list", field="faces")
    if not value:
        raise fail("VALIDATION_ERROR", "a split needs at least one face", field="faces")
    if len(value) > MAX_SPLIT_FACES:
        raise fail(
            PERSON_BATCH_TOO_LARGE,
            f"a split carries at most {MAX_SPLIT_FACES} faces",
            field="faces",
            max_batch_size=MAX_SPLIT_FACES,
            item_count=len(value),
        )
    normalized = [normalize_selection(item, index=index) for index, item in enumerate(value)]
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in normalized:
        if str(item["face_id"]) in seen:
            duplicates.append(str(item["face_id"]))
        seen.add(str(item["face_id"]))
    if duplicates:
        raise fail(
            "VALIDATION_ERROR",
            "a split must not name the same face twice",
            field="faces",
            face_ids=sorted(set(duplicates)),
        )
    return sorted(normalized, key=lambda entry: str(entry["face_id"]))


def requested_form(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "face_id": str(item["face_id"]),
        "expected_control_revision": int(item["expected_control_revision"]),
        "expected_assignment_revision": int(item["expected_assignment_revision"]),
    }


def split_digest(
    *, requested_source_person_id: int, source_expected_revision: int, faces: Sequence[Mapping[str, Any]]
) -> str:
    """ADR-0008 §5: the payload identity of one split.

    It uses the *requested* source id, never a canonical id that a later merge would move:
    a replay of the same client intent has to hash to the same value even after the
    redirect chain changed underneath it.
    """
    return payload_sha256(
        {
            "contract_version": CONTRACT_VERSION,
            "endpoint": ENDPOINT_SPLIT,
            "source_person_id": int(requested_source_person_id),
            "source_expected_revision": int(source_expected_revision),
            "faces": [requested_form(item) for item in faces],
        }
    )


# ------------------------------------------------------------------------- group snapshot
def effective_member_face_ids(connection: sqlite3.Connection, person_id: int) -> set[str]:
    """The effective member set of one person, as the rest of the product counts it."""
    return {
        str(row["face_id"])
        for row in connection.execute(_MEMBER_SQL, (int(person_id),)).fetchall()
    }


def _member_records(connection: sqlite3.Connection, person_id: int) -> list[dict[str, Any]]:
    return [
        {
            "face_id": str(row["face_id"]),
            "person_id": int(row["person_id"]),
            "assignment_kind": str(row["assignment_kind"]),
            "revision": int(row["revision"]),
            "last_operation_id": (
                None if row["last_operation_id"] is None else str(row["last_operation_id"])
            ),
        }
        for row in connection.execute(_MEMBER_SQL, (int(person_id),)).fetchall()
    ]


def read_group_bundle(
    connection: sqlite3.Connection, person_id: int, *, role: str
) -> dict[str, Any]:
    """The bounded ``SPLIT_GROUP`` snapshot of ADR-0008 §5.

    It carries the person fields a split or its undo may have to restore, the alias set and
    the *effective* members only. Suggestions are deliberately absent: a merge of two
    auto-links that no human confirmed must never make an otherwise unambiguous undo fail.
    Vectors, boxes, landmarks, paths and coordinates have no place in this record.
    """
    row = require_person_row(connection, int(person_id))
    aliases = [
        {"alias": str(alias["alias"]), "normalized_alias": str(alias["normalized_alias"])}
        for alias in connection.execute(
            "SELECT alias, normalized_alias FROM person_aliases WHERE person_id = ? "
            "ORDER BY normalized_alias, alias",
            (int(person_id),),
        ).fetchall()
    ]
    return {
        "role": str(role),
        "exists": True,
        "person_id": int(row["person_id"]),
        "name": None if row["name"] is None else str(row["name"]),
        "name_key": None if row["name_key"] is None else str(row["name_key"]),
        "avatar_face_id": (
            None if row["avatar_face_id"] is None else str(row["avatar_face_id"])
        ),
        "merged_into": None if row["merged_into"] is None else int(row["merged_into"]),
        "revision": int(row["revision"]),
        "aliases": aliases,
        "members": _member_records(connection, int(person_id)),
    }


def created_placeholder(person_id: int) -> dict[str, Any]:
    """ADR-0008 §5: the created group *before* the transaction, which does not exist."""
    return {"role": ROLE_CREATED, "exists": False, "person_id": int(person_id)}


def group_bundle_json(bundle: Mapping[str, Any]) -> str:
    return canonical_json(dict(bundle))


def load_group_bundle(raw: Any) -> dict[str, Any]:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    return json.loads(str(raw))


def _alias_pairs(bundle: Mapping[str, Any]) -> list[tuple[str, str]]:
    return sorted(
        (str(item["normalized_alias"]), str(item["alias"]))
        for item in bundle.get("aliases", [])
    )


def _member_keys(bundle: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    return sorted(
        (
            str(item["face_id"]),
            int(item["person_id"]),
            str(item["assignment_kind"]),
            int(item["revision"]),
            item.get("last_operation_id"),
        )
        for item in bundle.get("members", [])
    )


def group_bundle_differences(
    live: Mapping[str, Any], recorded: Mapping[str, Any]
) -> list[str]:
    """Name every semantic field that moved between two group bundles."""
    if bool(live.get("exists", True)) != bool(recorded.get("exists", True)):
        return ["exists"]
    differences: list[str] = []
    for field in ("name", "name_key", "avatar_face_id", "merged_into", "revision"):
        if live.get(field) != recorded.get(field):
            differences.append(field)
    if _alias_pairs(live) != _alias_pairs(recorded):
        differences.append("aliases")
    if _member_keys(live) != _member_keys(recorded):
        differences.append("members")
    return differences


def group_conflict_reason(
    live: Mapping[str, Any], recorded: Mapping[str, Any]
) -> str | None:
    """The frozen conflict reason of one group, or ``None`` when nothing relevant moved.

    The order is the ADR-0008 §7 table: a changed redirect is reported before anything else
    (a merge both moves ``merged_into`` and advances the revision), a person-level edit is
    reported as ``group_changed``, and a pure member-set movement - which the automatic
    association can produce without touching the person revision - is reported honestly as
    ``effective_members_changed``.
    """
    if live.get("merged_into") != recorded.get("merged_into"):
        return "group_redirected"
    for field in ("name", "name_key", "avatar_face_id", "revision"):
        if live.get(field) != recorded.get(field):
            return "group_changed"
    if _alias_pairs(live) != _alias_pairs(recorded):
        return "group_changed"
    if _member_keys(live) != _member_keys(recorded):
        return "effective_members_changed"
    return None


def group_item_answer(
    role: str, before: Mapping[str, Any], after: Mapping[str, Any]
) -> dict[str, Any]:
    """One ``SPLIT_GROUP`` item of the frozen split answer (ADR-0008 §6)."""
    return {
        "entity_type": ENTITY_TYPE_SPLIT_GROUP,
        "entity_id": int(after.get("person_id") or before.get("person_id") or 0),
        "role": str(role),
        "changed": bool(group_bundle_differences(after, before)),
        "revision": int(after.get("revision") or 0),
        "merged_into": after.get("merged_into"),
    }


# --------------------------------------------------------------------------- eligibility
def _split_conflict(
    *,
    reason: str,
    requested_source_person_id: int,
    canonical_person_id: int,
    face_id: str | None = None,
) -> Exception:
    details: dict[str, Any] = {
        "person_id": int(requested_source_person_id),
        "canonical_person_id": int(canonical_person_id),
        "reason": str(reason),
    }
    if face_id is not None:
        details["face_id"] = str(face_id)
    return fail(PERSON_SPLIT_CONFLICT, "a selected face cannot be split", **details)


def _require_source_revision(
    connection: sqlite3.Connection,
    *,
    canonical_person_id: int,
    requested_person_id: int,
    expected: int,
) -> None:
    row = require_person_row(connection, int(canonical_person_id))
    current = int(row["revision"])
    if current != int(expected):
        raise fail(
            PERSON_REVISION_CONFLICT,
            "the current person revision is not the expected one",
            person_id=int(requested_person_id),
            canonical_person_id=int(canonical_person_id),
            expected_revision=int(expected),
            current_revision=current,
        )


def _plan_face(
    connection: sqlite3.Connection,
    selection: Mapping[str, Any],
    *,
    canonical_source_person_id: int,
    requested_source_person_id: int,
) -> dict[str, Any]:
    """Validate one selected face and capture its control bundle before any write.

    ADR-0008 §3 fixes the order and the closed ``details.reason`` vocabulary. Every refusal
    happens before the first row of the transaction is written, so a rejected split leaves
    the catalog byte-identical.
    """
    face_id = str(selection["face_id"])
    face = face_module.require_face(connection, face_id)
    current_control = face_module.logical_control_revision(connection, str(face["face_id"]))
    if int(current_control) != int(selection["expected_control_revision"]):
        raise fail(
            FACE_ASSIGNMENT_CONFLICT,
            "the current face control revision is not the expected one",
            face_id=face_id,
            expected_revision=int(selection["expected_control_revision"]),
            current_revision=int(current_control),
        )
    mode = face_module.control_mode(connection, face_id)
    confirmed = face_module._confirmed_links(connection, face_id)
    if mode == face_module.MODE_MANUAL_UNASSIGNED:
        raise _split_conflict(
            reason="face_manual_unassigned",
            requested_source_person_id=requested_source_person_id,
            canonical_person_id=canonical_source_person_id,
            face_id=face_id,
        )
    if not confirmed:
        raise _split_conflict(
            reason="face_not_confirmed",
            requested_source_person_id=requested_source_person_id,
            canonical_person_id=canonical_source_person_id,
            face_id=face_id,
        )
    if len(confirmed) > 1:
        raise _split_conflict(
            reason="face_state_inconsistent",
            requested_source_person_id=requested_source_person_id,
            canonical_person_id=canonical_source_person_id,
            face_id=face_id,
        )
    if int(canonical_source_person_id) in face_module.active_exclusion_person_ids(
        connection, face_id
    ):
        # A face the human denied for this very person can never be "moved back" by a split.
        raise _split_conflict(
            reason="face_state_inconsistent",
            requested_source_person_id=requested_source_person_id,
            canonical_person_id=canonical_source_person_id,
            face_id=face_id,
        )
    link = confirmed[0]
    owner = face_module.canonical_person(connection, int(link["person_id"]))
    if int(owner) != int(canonical_source_person_id):
        raise _split_conflict(
            reason="face_owned_by_other_person",
            requested_source_person_id=requested_source_person_id,
            canonical_person_id=canonical_source_person_id,
            face_id=face_id,
        )
    if int(link["revision"]) != int(selection["expected_assignment_revision"]):
        raise _split_conflict(
            reason="assignment_revision",
            requested_source_person_id=requested_source_person_id,
            canonical_person_id=canonical_source_person_id,
            face_id=face_id,
        )
    return {
        "face_id": face_id,
        "before": face_module.read_control_bundle(connection, face_id),
    }


# ------------------------------------------------------------------------------ writing
def _insert_operation(
    connection: sqlite3.Connection,
    *,
    operation_id: str,
    request_id: str,
    digest: str,
    operation_type: str,
    source_person_ids: Sequence[int],
    target_person_id: int | None,
    undo_of_operation_id: str | None,
    now_ms: int,
) -> int:
    """Write the operation row first (ADR-0006 §8 item 1)."""
    operation_revision = int(
        connection.execute(
            "SELECT COALESCE(MAX(operation_revision), 0) + 1 FROM person_operations"
        ).fetchone()[0]
    )
    connection.execute(
        "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
        "source_person_ids_json, target_person_id, state, operation_revision, "
        "undo_of_operation_id, undone_by, catalog_filter_revision, created_at, committed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'COMMITTED', ?, ?, NULL, 0, ?, ?)",
        (
            str(operation_id),
            str(request_id),
            str(digest),
            str(operation_type),
            json.dumps(
                sorted({int(value) for value in source_person_ids}), separators=(",", ":")
            ),
            None if target_person_id is None else int(target_person_id),
            int(operation_revision),
            None if undo_of_operation_id is None else str(undo_of_operation_id),
            int(now_ms),
            int(now_ms),
        ),
    )
    return operation_revision


def _create_person(
    connection: sqlite3.Connection,
    *,
    now_ms: int,
    avatar_face_id: str | None = None,
) -> int:
    """ADR-0008 §3: an unnamed, alias-free, active person at revision 1.

    The source's name, ``name_key`` and aliases are deliberately *not* copied: an identical
    ``name_key`` would immediately collide with ``ux_persons_active_name_key`` (or, worse,
    silently re-merge what the user just split).
    """
    cursor = connection.execute(
        "INSERT INTO persons(name, name_key, avatar_face_id, merged_into, revision, "
        "created_at, updated_at) VALUES (NULL, NULL, ?, NULL, 1, ?, ?)",
        (
            None if avatar_face_id is None else str(avatar_face_id),
            int(now_ms),
            int(now_ms),
        ),
    )
    return int(cursor.lastrowid)


def _best_member_face(connection: sqlite3.Connection, person_id: int) -> str | None:
    """The representative of one person: same processor/space, quality first (ADR-0006 §5).

    Reused by both the created group and a source that lost its avatar to the split; no
    vector is recomputed and a face from another space can never be chosen.
    """
    from photo_search.face.contract import (
        FACE_PROCESSOR_ID,
        FACE_SPACE_ID,
        FACE_SPEC_REVISION,
    )

    row = connection.execute(
        "SELECT f.face_id AS face_id FROM face_person_links AS l "
        "JOIN faces AS f ON f.face_id = l.face_id "
        "JOIN face_embeddings AS e ON e.face_id = f.face_id AND e.processor_id = ? "
        "WHERE l.person_id = ? AND l.decision = 'CONFIRMED' AND f.quality_state = 'OK' "
        "AND f.spec_revision = ? AND e.space_id = ? "
        "AND NOT EXISTS (SELECT 1 FROM face_assignment_controls AS c "
        " WHERE c.face_id = l.face_id AND c.mode = 'MANUAL_UNASSIGNED') "
        "AND NOT EXISTS (SELECT 1 FROM face_exclusions AS x "
        " WHERE x.face_id = l.face_id AND x.person_id = l.person_id AND x.active = 1) "
        "ORDER BY f.face_pixels DESC, f.face_id ASC LIMIT 1",
        (str(FACE_PROCESSOR_ID), int(person_id), str(FACE_SPEC_REVISION), str(FACE_SPACE_ID)),
    ).fetchone()
    return None if row is None else str(row["face_id"])


def _move_face(
    connection: sqlite3.Connection,
    *,
    face_id: str,
    new_person_id: int,
    source_person_id: int,
    operation_id: str,
    now_ms: int,
) -> None:
    """ADR-0008 §4: move one selected face onto the new person.

    The link matrix is the ADR-0007 ``assign`` one, called rather than copied: a ``MANUAL``
    link is deleted, an ``AUTO`` link is downgraded to a suggestion with the frozen
    ``HUMAN_CONFLICT`` reason (the detection fact and the vector are never deleted), the
    canonical source gains an active denial and every other denial of the face survives.

    The one rule this function adds is the ADR-0008 §4 revision floor: the new ``MANUAL``
    link is numbered strictly above *every* link the face still carries, so a client that
    confirmed against an older assignment revision can never see its own token come back.
    """
    for link in face_module._confirmed_links(connection, str(face_id)):
        face_module._end_effective_link(
            connection,
            link=link,
            reason=contracts.REASON_HUMAN_CONFLICT,
            operation_id=str(operation_id),
            now_ms=int(now_ms),
        )
    floor = int(
        connection.execute(
            "SELECT COALESCE(MAX(revision), 0) FROM face_person_links WHERE face_id = ?",
            (str(face_id),),
        ).fetchone()[0]
    )
    face_module._write_exclusion(
        connection,
        face_id=str(face_id),
        person_id=int(source_person_id),
        active=True,
        operation_id=str(operation_id),
        now_ms=int(now_ms),
    )
    face_module._write_control(
        connection,
        face_id=str(face_id),
        mode=face_module.MODE_MANUAL_ASSIGNED,
        revision=face_module.logical_control_revision(connection, str(face_id)) + 1,
        operation_id=str(operation_id),
        now_ms=int(now_ms),
    )
    face_module._establish_link(
        connection,
        face_id=str(face_id),
        person_id=int(new_person_id),
        assignment_kind="MANUAL",
        operation_id=str(operation_id),
        now_ms=int(now_ms),
        revision_floor=floor,
    )


def _close_split_operation(
    connection: sqlite3.Connection,
    *,
    operation_id: str,
    operation_revision: int,
    operation_type: str,
    undo_of_operation_id: str | None,
    requested_source_person_id: int,
    canonical_source_person_id: int,
    new_person_id: int,
    source_before: Mapping[str, Any],
    source_after: Mapping[str, Any],
    created_before: Mapping[str, Any],
    created_after: Mapping[str, Any],
    face_results: Sequence[Mapping[str, Any]],
    now_ms: int,
) -> dict[str, Any]:
    """Write the fixed item order, the derived catch-up and the frozen envelope."""
    items: list[dict[str, Any]] = []
    groups: tuple[tuple[int, str, Mapping[str, Any], Mapping[str, Any]], ...] = (
        (0, ROLE_SOURCE, source_before, source_after),
        (1, ROLE_CREATED, created_before, created_after),
    )
    for index, role, before, after in groups:
        answer = group_item_answer(role, before, after)
        connection.execute(
            "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
            "entity_id, entity_key, before_value_json, after_value_json, after_revision) "
            "VALUES (?, ?, ?, ?, NULL, ?, ?, ?)",
            (
                str(operation_id),
                int(index),
                str(ENTITY_TYPE_SPLIT_GROUP),
                int(answer["entity_id"]),
                group_bundle_json(before),
                group_bundle_json(after),
                int(answer["revision"]),
            ),
        )
        items.append(answer)
    for offset, result in enumerate(face_results):
        face_id = str(result["face_id"])
        answer = face_module.item_answer(face_id, result["before"], result["after"])
        connection.execute(
            "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
            "entity_id, entity_key, before_value_json, after_value_json, after_revision) "
            "VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
            (
                str(operation_id),
                int(2 + offset),
                str(contracts.ENTITY_TYPE_FACE_CONTROL),
                face_id,
                face_module.control_bundle_json(result["before"]),
                face_module.control_bundle_json(result["after"]),
                int(answer["revision"]),
            ),
        )
        items.append(answer)
    # ADR-0008 §8: exactly one filter-revision bump with the shared cause, the per-asset
    # projection queue rows and the candidate repair of the already-published faces - and
    # explicitly no image-vector projection refresh, because a split changes an assignment,
    # never a picture.
    filter_revision = operation_module.publish_derived(
        connection,
        faces=[str(result["face_id"]) for result in face_results],
        now_ms=int(now_ms),
        refresh=None,
    )
    connection.execute(
        "UPDATE person_operations SET catalog_filter_revision = ? WHERE operation_id = ?",
        (int(filter_revision), str(operation_id)),
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "operation_id": str(operation_id),
        "operation_type": str(operation_type),
        "operation_revision": int(operation_revision),
        "status": "COMMITTED",
        "undo_of_operation_id": (
            None if undo_of_operation_id is None else str(undo_of_operation_id)
        ),
        "requested_source_person_id": int(requested_source_person_id),
        "canonical_source_person_id": int(canonical_source_person_id),
        "new_person_id": int(new_person_id),
        "changed": True,
        "catalog_filter_revision": int(filter_revision),
        "replayed": False,
        "items": items,
    }


def split_person(
    connection: sqlite3.Connection,
    *,
    source_person_id: int,
    contract_version: Any,
    source_expected_revision: Any,
    faces: Any,
    request_id: Any,
    now_ms: int,
    refresh: revision_module.ProjectionRefresh | None = None,
) -> dict[str, Any]:
    """Create one new unnamed person from 1..500 explicit faces of one source person.

    ``refresh`` is accepted for signature symmetry with the other person writers and is
    deliberately unused: ADR-0008 §8 forbids a split from re-projecting an image vector, so
    the shared derived step always runs with the P2-03 image refresh disabled.
    """
    request_id = check_request_id(request_id)
    check_contract_version(contract_version)
    requested = check_person_id(source_person_id, field="source_person_id")
    expected = check_source_revision(source_expected_revision)
    selections = check_faces(faces)
    digest = split_digest(
        requested_source_person_id=requested,
        source_expected_revision=expected,
        faces=selections,
    )
    replay = replay_split(connection, request_id=request_id, digest=digest)
    if replay is not None:
        return replay
    canonical = resolve_canonical_person_id(connection, requested)
    _require_source_revision(
        connection,
        canonical_person_id=canonical,
        requested_person_id=requested,
        expected=expected,
    )
    planned = [
        _plan_face(
            connection,
            selection,
            canonical_source_person_id=canonical,
            requested_source_person_id=requested,
        )
        for selection in selections
    ]
    selected = {str(plan["face_id"]) for plan in planned}
    remaining = effective_member_face_ids(connection, canonical) - selected
    if not remaining:
        # A split that would leave the source empty is a refusal, not an implicit delete.
        raise _split_conflict(
            reason="source_would_be_empty",
            requested_source_person_id=requested,
            canonical_person_id=canonical,
        )
    source_before = read_group_bundle(connection, canonical, role=ROLE_SOURCE)
    operation_id = operation_id_for(request_id)
    operation_revision = _insert_operation(
        connection,
        operation_id=operation_id,
        request_id=request_id,
        digest=digest,
        operation_type=contracts.OPERATION_TYPE_SPLIT,
        source_person_ids=[requested],
        target_person_id=None,
        undo_of_operation_id=None,
        now_ms=int(now_ms),
    )
    new_person_id = _create_person(connection, now_ms=int(now_ms))
    connection.execute(
        "UPDATE person_operations SET target_person_id = ? WHERE operation_id = ?",
        (int(new_person_id), str(operation_id)),
    )
    created_before = created_placeholder(new_person_id)
    for plan in planned:
        _move_face(
            connection,
            face_id=str(plan["face_id"]),
            new_person_id=int(new_person_id),
            source_person_id=int(canonical),
            operation_id=str(operation_id),
            now_ms=int(now_ms),
        )
    avatar = _best_member_face(connection, int(new_person_id))
    if avatar is not None:
        connection.execute(
            "UPDATE persons SET avatar_face_id = ? WHERE person_id = ?",
            (str(avatar), int(new_person_id)),
        )
    source_row = require_person_row(connection, int(canonical))
    current_avatar = (
        None if source_row["avatar_face_id"] is None else str(source_row["avatar_face_id"])
    )
    if current_avatar is None or current_avatar in selected:
        # The source's representative left with the split: re-pick inside its own space, or
        # stay empty when nothing usable is left.
        replacement = _best_member_face(connection, int(canonical))
        connection.execute(
            "UPDATE persons SET avatar_face_id = ? WHERE person_id = ?",
            (None if replacement is None else str(replacement), int(canonical)),
        )
    connection.execute(
        "UPDATE persons SET revision = revision + 1, updated_at = ? WHERE person_id = ?",
        (int(now_ms), int(canonical)),
    )
    source_after = read_group_bundle(connection, canonical, role=ROLE_SOURCE)
    created_after = read_group_bundle(connection, new_person_id, role=ROLE_CREATED)
    face_results = [
        {
            "face_id": str(plan["face_id"]),
            "before": plan["before"],
            "after": face_module.read_control_bundle(connection, str(plan["face_id"])),
        }
        for plan in planned
    ]
    return _close_split_operation(
        connection,
        operation_id=str(operation_id),
        operation_revision=int(operation_revision),
        operation_type=contracts.OPERATION_TYPE_SPLIT,
        undo_of_operation_id=None,
        requested_source_person_id=requested,
        canonical_source_person_id=int(canonical),
        new_person_id=int(new_person_id),
        source_before=source_before,
        source_after=source_after,
        created_before=created_before,
        created_after=created_after,
        face_results=face_results,
        now_ms=int(now_ms),
    )


# ------------------------------------------------------------------------ replay reading
def replay_split(
    connection: sqlite3.Connection, *, request_id: str, digest: str
) -> dict[str, Any] | None:
    """ADR-0008 §5: the global idempotency gate, ahead of every entity/revision check."""
    row = connection.execute(
        f"SELECT {_OPERATION_COLUMNS} FROM person_operations WHERE request_id = ?",
        (str(request_id),),
    ).fetchone()
    if row is None:
        return None
    if str(row["payload_sha256"]) != str(digest):
        raise fail(
            contracts.PERSON_IDEMPOTENCY_CONFLICT,
            "this request_id is already bound to a different payload",
            operation_id=str(row["operation_id"]),
        )
    return operation_module.operation_result(connection, row)


def stored_items(connection: sqlite3.Connection, operation_id: str) -> list[Any]:
    return list(
        connection.execute(
            f"SELECT {_ITEM_COLUMNS} FROM person_operation_items WHERE operation_id = ? "
            "ORDER BY item_index",
            (str(operation_id),),
        ).fetchall()
    )


def split_operation_result(
    operation: Any, items: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Rebuild the frozen split envelope of an already committed operation (a replay)."""
    try:
        requested = int(json.loads(str(operation["source_person_ids_json"]))[0])
    except (ValueError, IndexError, TypeError):
        raise fail(
            PERSON_OPERATION_NOT_UNDOABLE,
            "this split operation does not record its requested source person",
            operation_id=str(operation["operation_id"]),
        ) from None
    groups = [
        item for item in items if str(item["entity_type"]) == str(ENTITY_TYPE_SPLIT_GROUP)
    ]
    if len(groups) != 2 or operation["target_person_id"] is None:
        raise fail(
            PERSON_OPERATION_NOT_UNDOABLE,
            "this split operation does not carry its two group snapshots",
            operation_id=str(operation["operation_id"]),
        )
    return {
        "contract_version": CONTRACT_VERSION,
        "operation_id": str(operation["operation_id"]),
        "operation_type": str(operation["type"]),
        "operation_revision": int(operation["operation_revision"]),
        "status": "UNDONE" if str(operation["state"]) == "UNDONE" else "COMMITTED",
        "undo_of_operation_id": (
            None
            if operation["undo_of_operation_id"] is None
            else str(operation["undo_of_operation_id"])
        ),
        "requested_source_person_id": int(requested),
        "canonical_source_person_id": int(groups[0]["entity_id"]),
        "new_person_id": int(operation["target_person_id"]),
        "changed": True,
        "catalog_filter_revision": int(operation["catalog_filter_revision"]),
        "replayed": True,
        "items": [dict(item) for item in items],
    }


# ------------------------------------------------------------------------- compensation
def _validate_split_items(
    connection: sqlite3.Connection, operation_id: str
) -> tuple[Any, Any, list[Any]]:
    """The frozen item shape of a split: source group, created group, sorted face items."""
    rows = stored_items(connection, operation_id)

    def refuse(reason: str) -> Exception:
        return fail(
            PERSON_OPERATION_NOT_UNDOABLE,
            "this operation does not carry the frozen split snapshot",
            operation_id=str(operation_id),
            reason=str(reason),
        )

    if len(rows) < 3 or len(rows) > MAX_SPLIT_FACES + 2:
        raise refuse("item_count")
    source_row, created_row = rows[0], rows[1]
    if (
        str(source_row["entity_type"]) != ENTITY_TYPE_SPLIT_GROUP
        or str(created_row["entity_type"]) != ENTITY_TYPE_SPLIT_GROUP
        or source_row["entity_id"] is None
        or created_row["entity_id"] is None
    ):
        raise refuse("group_items")
    source_role = str(load_group_bundle(source_row["after_value_json"]).get("role"))
    created_role = str(load_group_bundle(created_row["after_value_json"]).get("role"))
    if source_role != ROLE_SOURCE or created_role != ROLE_CREATED:
        raise refuse("group_roles")
    faces = list(rows[2:])
    face_ids = [str(row["entity_key"]) for row in faces]
    if any(str(row["entity_type"]) != str(contracts.ENTITY_TYPE_FACE_CONTROL) for row in faces):
        raise refuse("face_items")
    if face_ids != sorted(face_ids) or len(set(face_ids)) != len(face_ids):
        raise refuse("face_order")
    return source_row, created_row, faces


def _probe_redirect(connection: sqlite3.Connection, person_id: int) -> None:
    """ADR-0008 §7: a broken/looping redirect chain keeps its own frozen code.

    The comparison of a group uses the person row itself, so the chain has to be walked
    explicitly - otherwise a cycle would quietly look like "somebody edited the group" and
    the caller would lose the honest ``PERSON_REDIRECT_CYCLE`` answer.
    """
    try:
        resolve_canonical_person_id(connection, int(person_id))
    except CatalogError as error:
        if str(error.code) == PERSON_REDIRECT_CYCLE:
            raise
        # A missing person is reported as the group conflict below, with zero writes.


def undo_split_operation(
    connection: sqlite3.Connection,
    *,
    original: Any,
    request_id: str,
    digest: str,
    expected_operation_revision: int,
    now_ms: int,
    refresh: revision_module.ProjectionRefresh | None = None,
) -> dict[str, Any]:
    """Compensate one committed split, or refuse the whole request with zero writes."""
    original_id = str(original["operation_id"])
    if (
        str(original["type"]) != contracts.OPERATION_TYPE_SPLIT
        or str(original["state"]) != "COMMITTED"
        or original["undone_by"] is not None
        or int(original["operation_revision"]) != int(expected_operation_revision)
    ):
        raise fail(
            PERSON_OPERATION_NOT_UNDOABLE,
            "this operation is already reversed, is an undo itself, or its revision changed",
            operation_id=original_id,
            expected_revision=int(expected_operation_revision),
            current_revision=int(original["operation_revision"]),
            status=str(original["state"]),
        )
    source_row, created_row, face_rows = _validate_split_items(connection, original_id)
    source_id = int(source_row["entity_id"])
    created_id = int(created_row["entity_id"])
    _probe_redirect(connection, source_id)
    _probe_redirect(connection, created_id)
    source_after = load_group_bundle(source_row["after_value_json"])
    source_before = load_group_bundle(source_row["before_value_json"])
    created_after = load_group_bundle(created_row["after_value_json"])

    conflicts: list[dict[str, Any]] = []

    def live_group(person_id: int, *, role: str) -> dict[str, Any] | None:
        try:
            return read_group_bundle(connection, int(person_id), role=role)
        except CatalogError as error:
            if str(error.code) == PERSON_REDIRECT_CYCLE:
                # A broken redirect chain is a corrupt authority fact, not "somebody edited a
                # group": it keeps its own frozen code instead of becoming a generic refusal.
                raise
            conflicts.append(
                {
                    "entity_type": ENTITY_TYPE_SPLIT_GROUP,
                    "entity_id": int(person_id),
                    "reason": "person_missing",
                }
            )
            return None

    live_source = live_group(source_id, role=ROLE_SOURCE)
    if live_source is not None:
        reason = group_conflict_reason(live_source, source_after)
        if reason is not None:
            conflicts.append(
                {
                    "entity_type": ENTITY_TYPE_SPLIT_GROUP,
                    "entity_id": int(source_id),
                    "reason": reason,
                }
            )
    live_created = live_group(created_id, role=ROLE_CREATED)
    if live_created is not None:
        reason = group_conflict_reason(live_created, created_after)
        if reason is not None:
            conflicts.append(
                {
                    "entity_type": ENTITY_TYPE_SPLIT_GROUP,
                    "entity_id": int(created_id),
                    "reason": reason,
                }
            )
    live_faces: list[dict[str, Any]] = []
    for row in face_rows:
        face_id = str(row["entity_key"])
        recorded_after = face_module.load_control_bundle(row["after_value_json"])
        recorded_before = face_module.load_control_bundle(row["before_value_json"])
        if (
            connection.execute(
                "SELECT 1 FROM faces WHERE face_id = ?", (face_id,)
            ).fetchone()
            is None
        ):
            conflicts.append(
                {
                    "entity_type": str(contracts.ENTITY_TYPE_FACE_CONTROL),
                    "face_id": face_id,
                    "reason": "face_missing",
                }
            )
            continue
        live = face_module.read_control_bundle(connection, face_id)
        differences = face_module.control_bundle_differences(live, recorded_after)
        if differences:
            conflicts.append(
                {
                    "entity_type": str(contracts.ENTITY_TYPE_FACE_CONTROL),
                    "face_id": face_id,
                    "reason": "face_control_changed",
                }
            )
            continue
        live_faces.append(
            {
                "face_id": face_id,
                "live": live,
                "wanted": recorded_before,
            }
        )
    if conflicts:
        raise fail(
            PERSON_OPERATION_NOT_UNDOABLE,
            "a target of this split was edited afterwards",
            operation_id=original_id,
            conflicts=conflicts,
        )

    new_operation_id = operation_id_for(request_id)
    new_operation_revision = _insert_operation(
        connection,
        operation_id=new_operation_id,
        request_id=str(request_id),
        digest=str(digest),
        operation_type="UNDO",
        source_person_ids=[int(json.loads(str(original["source_person_ids_json"]))[0])],
        target_person_id=int(created_id),
        undo_of_operation_id=original_id,
        now_ms=int(now_ms),
    )
    face_results: list[dict[str, Any]] = []
    for entry in live_faces:
        face_module.restore_control_bundle(
            connection,
            face_id=str(entry["face_id"]),
            live=entry["live"],
            wanted=entry["wanted"],
            operation_id=new_operation_id,
            now_ms=int(now_ms),
        )
        face_results.append(
            {
                "face_id": str(entry["face_id"]),
                "before": entry["live"],
                "after": face_module.read_control_bundle(connection, str(entry["face_id"])),
            }
        )
    live_source_row = require_person_row(connection, int(source_id))
    connection.execute(
        "UPDATE persons SET avatar_face_id = ?, revision = ?, updated_at = ? "
        "WHERE person_id = ?",
        (
            source_before["avatar_face_id"],
            int(live_source_row["revision"]) + 1,
            int(now_ms),
            int(source_id),
        ),
    )
    live_created_row = require_person_row(connection, int(created_id))
    # ADR-0008 §7 item 3: the created person is never deleted. Its stable id is kept and
    # redirected to the source, so every id a client may have recorded keeps resolving.
    connection.execute(
        "UPDATE persons SET avatar_face_id = NULL, merged_into = ?, revision = ?, "
        "updated_at = ? WHERE person_id = ?",
        (int(source_id), int(live_created_row["revision"]) + 1, int(now_ms), int(created_id)),
    )
    connection.execute(
        "UPDATE person_operations SET state = 'UNDONE', undone_by = ? WHERE operation_id = ?",
        (str(new_operation_id), original_id),
    )
    source_restored = read_group_bundle(connection, int(source_id), role=ROLE_SOURCE)
    created_restored = read_group_bundle(connection, int(created_id), role=ROLE_CREATED)
    return _close_split_operation(
        connection,
        operation_id=new_operation_id,
        operation_revision=int(new_operation_revision),
        operation_type="UNDO",
        undo_of_operation_id=original_id,
        requested_source_person_id=int(
            json.loads(str(original["source_person_ids_json"]))[0]
        ),
        canonical_source_person_id=int(source_id),
        new_person_id=int(created_id),
        source_before=live_source,
        source_after=source_restored,
        created_before=live_created,
        created_after=created_restored,
        face_results=face_results,
        now_ms=int(now_ms),
    )


__all__ = [
    "CONTRACT_VERSION",
    "ENDPOINT_SPLIT",
    "ENTITY_TYPE_SPLIT_GROUP",
    "MAX_SPLIT_FACES",
    "ROLE_CREATED",
    "ROLE_SOURCE",
    "check_contract_version",
    "check_faces",
    "check_source_revision",
    "created_placeholder",
    "effective_member_face_ids",
    "group_bundle_differences",
    "group_bundle_json",
    "group_conflict_reason",
    "group_item_answer",
    "load_group_bundle",
    "normalize_selection",
    "read_group_bundle",
    "replay_split",
    "requested_form",
    "split_digest",
    "split_operation_result",
    "split_person",
    "stored_items",
    "undo_split_operation",
]
