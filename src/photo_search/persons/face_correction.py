"""The P3-04B single-face correction use case of ADR-0007.

One human correction - "this face is not 爸爸", "this face is 叔叔", "let the machine
decide again" - is always expressed against an explicit ``face_id``. Nothing in this module
widens a request to the other faces of the same photo, and nothing here touches the
detection facts, the vectors, the candidate generations or the image index.

The authority is the 0011 human-control tables plus the shared operation log:

* ``face_assignment_controls`` carries the logical, face-level revision and the mode;
* ``face_person_links`` carries the one effective link and the machine suggestions;
* ``face_exclusions`` carries the persistent human denials (a row is never deleted);
* ``person_operations``/``person_operation_items`` record the operation, its
  ``FACE_CONTROL`` before/after bundle and therefore everything a compensating undo needs.

Every write follows the ADR-0007 §8 order inside one ``BEGIN IMMEDIATE`` transaction: the
operation row and the authority facts, then the affected faces/assets, then exactly one
``catalog_filter_revision`` bump with the shared ``PERSON_CHANGED`` cause, the per-asset
projection queue rows and the ``queue_candidate()`` degradation of every affected face that
is already published. No file I/O, no model run and no Lance write happens in here.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from photo_search.catalog.errors import (
    FACE_ASSIGNMENT_CONFLICT,
    FACE_BATCH_TOO_LARGE,
    FACE_IDEMPOTENCY_CONFLICT,
    FACE_NOT_FOUND,
    FACE_OPERATION_NOT_UNDOABLE,
    FACE_STATE_CONFLICT,
    MAX_FACE_BATCH_SIZE,
)
from photo_search.persons import contracts
from photo_search.persons.contracts import (
    canonical_json,
    check_expected_revision,
    check_person_id,
    check_request_id,
    fail,
    operation_id_for,
    payload_sha256,
)
from photo_search.persons.identity import resolve_canonical_person_id
from photo_search.places import revision as revision_module

#: The envelope literal of the companion document ``p3-face-correction-contracts.v1.json``.
CONTRACT_VERSION = "p3-face-correction-contracts.v1"

MODE_AUTO_ALLOWED = "AUTO_ALLOWED"
MODE_MANUAL_ASSIGNED = "MANUAL_ASSIGNED"
MODE_MANUAL_UNASSIGNED = "MANUAL_UNASSIGNED"

ACTION_ASSIGN = "assign"
ACTION_UNASSIGN = "unassign"
ACTION_RESTORE_AUTO = "restore_auto"

#: ADR-0007 §2: the single-face type is a function of the action, never of the outcome.
ACTION_OPERATION_TYPES = {
    ACTION_ASSIGN: "FACE_ASSIGN",
    ACTION_UNASSIGN: "FACE_UNASSIGN",
    ACTION_RESTORE_AUTO: "FACE_RESTORE_AUTO",
}
BATCH_OPERATION_TYPE = "BATCH_FACE_EDIT"

#: The two request families the single endpoint and the batch endpoint hash into.
ENDPOINT_SINGLE = "faces.assignment"
ENDPOINT_BATCH = "face-assignments.batch"

_ASSIGNMENT_KIND_MANUAL = "MANUAL"
_ASSIGNMENT_KIND_AUTO = "AUTO"
_DECISION_CONFIRMED = "CONFIRMED"
_DECISION_SUGGESTED = "SUGGESTED"

#: Implementation bound for one ``IN (...)`` lookup, mirroring the person module.
_IN_CHUNK = 400

_ITEM_COLUMNS = (
    "item_index, entity_type, entity_id, entity_key, before_value_json, after_value_json, "
    "after_revision"
)


def _chunks(values: Sequence[Any], size: int = _IN_CHUNK) -> list[list[Any]]:
    return [list(values[index : index + size]) for index in range(0, len(values), size)]


def _placeholders(count: int) -> str:
    return ", ".join("?" for _ in range(int(count)))


# ------------------------------------------------------------------------------- shape
def check_contract_version(value: Any) -> str:
    if value != CONTRACT_VERSION:
        raise fail(
            "VALIDATION_ERROR",
            f"contract_version must be {CONTRACT_VERSION}",
            field="contract_version",
        )
    return CONTRACT_VERSION


def check_face_id(value: Any, *, field: str = "face_id") -> str:
    """A face id is a lowercase hex content key: an identifier, never a path."""
    if not isinstance(value, str):
        raise fail("VALIDATION_ERROR", f"{field} must be a string", field=field)
    text = value.strip()
    if (
        not text
        or len(text) > 128
        or not all(character in "0123456789abcdef" for character in text)
    ):
        raise fail("VALIDATION_ERROR", f"{field} must be a lowercase hex id", field=field)
    return text


def check_action(value: Any, *, field: str = "action") -> str:
    if not isinstance(value, str) or value not in ACTION_OPERATION_TYPES:
        raise fail(
            "VALIDATION_ERROR",
            "action must be assign, unassign or restore_auto",
            field=field,
        )
    return str(value)


def check_target(action: str, value: Any, *, field: str = "target_person_id") -> int | None:
    """ADR-0007 §5/§6: ``target_person_id`` is required by ``assign`` and forbidden otherwise."""
    if action == ACTION_ASSIGN:
        return check_person_id(value, field=field)
    if value is not None:
        raise fail(
            "VALIDATION_ERROR",
            f"{field} is only allowed for the assign action",
            field=field,
        )
    return None


def normalize_item(item: Any, *, index: int) -> dict[str, Any]:
    """The *requested* shape of one item: face, action, requested target and revision."""
    if not isinstance(item, Mapping):
        raise fail("VALIDATION_ERROR", "each item must be an object", field="items", index=index)
    face_id = check_face_id(item.get("face_id"))
    action = check_action(item.get("action"))
    target = check_target(action, item.get("target_person_id"))
    expected = check_expected_revision(item.get("expected_revision"))
    return {
        "face_id": face_id,
        "action": action,
        "target_person_id": target,
        "expected_revision": expected,
    }


def _requested_form(item: Mapping[str, Any]) -> dict[str, Any]:
    """The requested target is what the payload hash records, never a canonical id."""
    return {
        "face_id": str(item["face_id"]),
        "action": str(item["action"]),
        "target_person_id": (
            None if item["target_person_id"] is None else int(item["target_person_id"])
        ),
        "expected_revision": int(item["expected_revision"]),
    }


def _sorted_items(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """ADR-0007 §5: both the hash and the storage order are face-sorted and order-free."""
    return sorted((dict(item) for item in items), key=lambda entry: str(entry["face_id"]))


# --------------------------------------------------------------------------- authority
def require_face(connection: sqlite3.Connection, face_id: str) -> Any:
    face = connection.execute(
        "SELECT face_id, asset_id FROM faces WHERE face_id = ?", (str(face_id),)
    ).fetchone()
    if face is None:
        raise fail(FACE_NOT_FOUND, f"face {face_id} does not exist", face_id=str(face_id))
    return face


def logical_control_revision(connection: sqlite3.Connection, face_id: str) -> int:
    """ADR-0007 §3: no control row is exactly ``AUTO_ALLOWED`` at logical revision 0."""
    row = connection.execute(
        "SELECT revision FROM face_assignment_controls WHERE face_id = ?", (str(face_id),)
    ).fetchone()
    return 0 if row is None else int(row["revision"])


def logical_control_revisions(
    connection: sqlite3.Connection, face_ids: Sequence[str]
) -> dict[str, int]:
    """The observed logical revision of many faces, in one bounded read."""
    revisions = {str(face_id): 0 for face_id in face_ids}
    for chunk in _chunks(sorted({str(value) for value in face_ids})):
        rows = connection.execute(
            "SELECT face_id, revision FROM face_assignment_controls "
            f"WHERE face_id IN ({_placeholders(len(chunk))})",
            tuple(chunk),
        ).fetchall()
        for row in rows:
            revisions[str(row["face_id"])] = int(row["revision"])
    return revisions


def control_mode(connection: sqlite3.Connection, face_id: str) -> str:
    row = connection.execute(
        "SELECT mode FROM face_assignment_controls WHERE face_id = ?", (str(face_id),)
    ).fetchone()
    return MODE_AUTO_ALLOWED if row is None else str(row["mode"])


def _confirmed_links(connection: sqlite3.Connection, face_id: str) -> list[Any]:
    return list(
        connection.execute(
            "SELECT face_id, person_id, assignment_kind, decision, confidence, reason, "
            "revision, last_operation_id, created_at, updated_at FROM face_person_links "
            "WHERE face_id = ? AND decision = 'CONFIRMED' ORDER BY revision DESC",
            (str(face_id),),
        ).fetchall()
    )


def _suggested_exists(connection: sqlite3.Connection, face_id: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM face_person_links WHERE face_id = ? AND decision = 'SUGGESTED' LIMIT 1",
        (str(face_id),),
    ).fetchone()
    return row is not None


def canonical_person(connection: sqlite3.Connection, person_id: int) -> int:
    """A link or denial may still point at a person that a P3-03 merge moved away."""
    return int(resolve_canonical_person_id(connection, int(person_id)))


def _exclusion_rows(connection: sqlite3.Connection, face_id: str) -> list[Any]:
    return list(
        connection.execute(
            "SELECT person_id, operation_id, active, created_at FROM face_exclusions "
            "WHERE face_id = ? ORDER BY person_id",
            (str(face_id),),
        ).fetchall()
    )


def active_exclusion_person_ids(connection: sqlite3.Connection, face_id: str) -> list[int]:
    """The active, canonical, de-duplicated and ascending exclusion ids of one face."""
    active: set[int] = set()
    for row in _exclusion_rows(connection, face_id):
        if int(row["active"]):
            active.add(canonical_person(connection, int(row["person_id"])))
    return sorted(active)


# ---------------------------------------------------------------------------- snapshot
def read_control_bundle(connection: sqlite3.Connection, face_id: str) -> dict[str, Any]:
    """The bounded ``FACE_CONTROL`` snapshot of ADR-0007 §2.

    It carries the logical control, the current effective assignment (or ``None``) and the
    whole exclusion set in canonical ``person_id`` order. Timestamps are recorded for the
    audit trail but excluded from the comparison of :func:`control_bundle_differences` -
    exactly like the P3-03 person bundle - so an undo never fails on a clock.
    """
    control = connection.execute(
        "SELECT mode, revision, last_operation_id, updated_at FROM face_assignment_controls "
        "WHERE face_id = ?",
        (str(face_id),),
    ).fetchone()
    links = _confirmed_links(connection, str(face_id))
    assignment = None
    if links:
        link = links[0]
        assignment = {
            "person_id": canonical_person(connection, int(link["person_id"])),
            "assignment_kind": str(link["assignment_kind"]),
            "decision": str(link["decision"]),
            "reason": None if link["reason"] is None else str(link["reason"]),
            "revision": int(link["revision"]),
            "last_operation_id": (
                None if link["last_operation_id"] is None else str(link["last_operation_id"])
            ),
            "created_at": int(link["created_at"]),
            "updated_at": int(link["updated_at"]),
        }
    exclusions = [
        {
            "person_id": canonical_person(connection, int(row["person_id"])),
            "active": bool(int(row["active"])),
            "operation_id": (
                None if row["operation_id"] is None else str(row["operation_id"])
            ),
            "created_at": int(row["created_at"]),
        }
        for row in _exclusion_rows(connection, str(face_id))
    ]
    return {
        "face_id": str(face_id),
        "control": {
            "mode": MODE_AUTO_ALLOWED if control is None else str(control["mode"]),
            "revision": 0 if control is None else int(control["revision"]),
            "last_operation_id": (
                None
                if control is None or control["last_operation_id"] is None
                else str(control["last_operation_id"])
            ),
            "updated_at": None if control is None else int(control["updated_at"]),
        },
        "assignment": assignment,
        "exclusions": exclusions,
    }


def control_bundle_json(bundle: Mapping[str, Any]) -> str:
    return canonical_json(dict(bundle))


def load_control_bundle(raw: Any) -> dict[str, Any]:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    return json.loads(str(raw))


def control_bundle_differences(
    live: Mapping[str, Any], recorded: Mapping[str, Any]
) -> list[str]:
    """Name every *semantic* field that moved between two face-control bundles.

    ``created_at``/``updated_at`` are bookkeeping: they are recorded for the audit trail but
    never make an undo fail. The mode, the logical revision, the effective assignment and
    the exclusion set are all semantic, per ADR-0007 §7.
    """
    differences: list[str] = []
    live_control = live.get("control") or {}
    recorded_control = recorded.get("control") or {}
    for field in ("mode", "revision", "last_operation_id"):
        if live_control.get(field) != recorded_control.get(field):
            differences.append(f"control.{field}")
    live_assignment = live.get("assignment")
    recorded_assignment = recorded.get("assignment")
    if (live_assignment is None) != (recorded_assignment is None):
        differences.append("assignment")
    elif live_assignment is not None and recorded_assignment is not None:
        for field in (
            "person_id",
            "assignment_kind",
            "decision",
            "reason",
            "revision",
            "last_operation_id",
        ):
            if live_assignment.get(field) != recorded_assignment.get(field):
                differences.append(f"assignment.{field}")
    if _exclusion_keys(live) != _exclusion_keys(recorded):
        differences.append("exclusions")
    return differences


def _exclusion_keys(bundle: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    return sorted(
        (int(item["person_id"]), bool(item["active"]), item.get("operation_id"))
        for item in bundle.get("exclusions", [])
    )


def _exclusion_changes(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> dict[str, list[int]]:
    """The activated/deactivated person ids ADR-0007 §6 requires in every item answer."""

    def state(bundle: Mapping[str, Any]) -> dict[int, bool]:
        return {
            int(item["person_id"]): bool(item["active"])
            for item in bundle.get("exclusions", [])
        }

    was = state(before)
    now = state(after)
    activated = sorted(
        person for person, active in now.items() if active and not was.get(person, False)
    )
    deactivated = sorted(
        person for person, active in was.items() if active and not now.get(person, False)
    )
    return {"activated": activated, "deactivated": deactivated}


def item_answer(
    face_id: str, before: Mapping[str, Any], after: Mapping[str, Any]
) -> dict[str, Any]:
    """One ``FACE_CONTROL`` item of the frozen operation answer (ADR-0007 §6)."""
    control = after.get("control") or {}
    mode = str(control.get("mode") or MODE_AUTO_ALLOWED)
    assignment = after.get("assignment")
    return {
        "entity_type": contracts.ENTITY_TYPE_FACE_CONTROL,
        "face_id": str(face_id),
        "changed": bool(control_bundle_differences(after, before)),
        "mode": mode,
        "revision": int(control.get("revision") or 0),
        "assigned_person_id": (
            None
            if mode == MODE_MANUAL_UNASSIGNED or assignment is None
            else int(assignment["person_id"])
        ),
        "exclusions": _exclusion_changes(before, after),
    }


def recorded_item_projection(row: Any) -> dict[str, Any]:
    """Rebuild one stored ``FACE_CONTROL`` item for a replay (never a live read)."""
    before = load_control_bundle(row["before_value_json"])
    after = load_control_bundle(row["after_value_json"])
    return item_answer(str(row["entity_key"]), before, after)


def stored_items(connection: sqlite3.Connection, operation_id: str) -> list[Any]:
    return list(
        connection.execute(
            f"SELECT {_ITEM_COLUMNS} FROM person_operation_items WHERE operation_id = ? "
            "ORDER BY item_index",
            (str(operation_id),),
        ).fetchall()
    )


# ------------------------------------------------------------------------ read surface
def control_state(connection: sqlite3.Connection, face_id: str) -> dict[str, Any]:
    """``GET /api/v1/faces/{face_id}/assignment``: mode, revision, person, exclusions.

    No similarity, no box, no landmark, no vector and no path leaves this projection.
    """
    face = require_face(connection, str(face_id))
    bundle = read_control_bundle(connection, str(face["face_id"]))
    mode = str(bundle["control"]["mode"])
    assignment = bundle["assignment"]
    return {
        "contract_version": CONTRACT_VERSION,
        "face_id": str(face["face_id"]),
        "mode": mode,
        "revision": int(bundle["control"]["revision"]),
        "assigned_person_id": (
            None
            if mode == MODE_MANUAL_UNASSIGNED or assignment is None
            else int(assignment["person_id"])
        ),
        "active_exclusions": active_exclusion_person_ids(connection, str(face["face_id"])),
        "suggestions_available": _suggested_exists(connection, str(face["face_id"])),
    }


# ------------------------------------------------------------------------- write rules
def _require_state(connection: sqlite3.Connection, face_id: str) -> None:
    """Refuse a live bundle the frozen model cannot describe (ADR-0007 §6)."""
    confirmed = _confirmed_links(connection, str(face_id))
    if len(confirmed) > 1:
        raise fail(
            FACE_STATE_CONFLICT,
            "the face carries more than one effective link",
            face_id=str(face_id),
            reason="multiple_confirmed_links",
        )
    if control_mode(connection, str(face_id)) == MODE_MANUAL_UNASSIGNED and confirmed:
        raise fail(
            FACE_STATE_CONFLICT,
            "a MANUAL_UNASSIGNED face cannot carry an effective link",
            face_id=str(face_id),
            reason="manual_unassigned_with_effective_link",
        )


def require_expected_revision(
    connection: sqlite3.Connection, *, face_id: str, expected: int
) -> None:
    """ADR-0007 §3: the logical, face-level control revision is the optimistic token."""
    current = logical_control_revision(connection, str(face_id))
    if int(current) != int(expected):
        raise fail(
            FACE_ASSIGNMENT_CONFLICT,
            "the current face control revision is not the expected one",
            face_id=str(face_id),
            expected_revision=int(expected),
            current_revision=int(current),
        )


def _write_control(
    connection: sqlite3.Connection,
    *,
    face_id: str,
    mode: str,
    revision: int,
    operation_id: str,
    now_ms: int,
) -> None:
    connection.execute(
        "INSERT INTO face_assignment_controls(face_id, mode, revision, last_operation_id, "
        "updated_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(face_id) DO UPDATE SET mode = excluded.mode, "
        "revision = excluded.revision, last_operation_id = excluded.last_operation_id, "
        "updated_at = excluded.updated_at",
        (str(face_id), str(mode), int(revision), str(operation_id), int(now_ms)),
    )


def _write_exclusion(
    connection: sqlite3.Connection,
    *,
    face_id: str,
    person_id: int,
    active: bool,
    operation_id: str,
    now_ms: int,
) -> None:
    """Activate or lift one ``(face, person)`` denial; the row itself is never deleted."""
    connection.execute(
        "INSERT INTO face_exclusions(face_id, person_id, operation_id, active, created_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(face_id, person_id) DO UPDATE SET active = excluded.active, "
        "operation_id = excluded.operation_id",
        (str(face_id), int(person_id), str(operation_id), 1 if active else 0, int(now_ms)),
    )


def _end_effective_link(
    connection: sqlite3.Connection,
    *,
    link: Any,
    reason: str,
    operation_id: str,
    now_ms: int,
) -> None:
    """End one effective link without ever deleting a detection fact or a vector.

    A human link is removed (the operation snapshot keeps it for the compensation); a
    machine link is *downgraded to a suggestion* with a frozen reason, so the automatic
    knowledge survives as a suggestion and can never masquerade as an assignment.
    """
    if str(link["assignment_kind"]) == _ASSIGNMENT_KIND_MANUAL:
        connection.execute(
            "DELETE FROM face_person_links WHERE face_id = ? AND person_id = ? "
            "AND assignment_kind = ?",
            (str(link["face_id"]), int(link["person_id"]), str(link["assignment_kind"])),
        )
        return
    connection.execute(
        "UPDATE face_person_links SET decision = 'SUGGESTED', reason = ?, "
        "revision = revision + 1, last_operation_id = ?, updated_at = ? "
        "WHERE face_id = ? AND person_id = ? AND assignment_kind = ?",
        (
            str(reason),
            str(operation_id),
            int(now_ms),
            str(link["face_id"]),
            int(link["person_id"]),
            str(link["assignment_kind"]),
        ),
    )


def _establish_link(
    connection: sqlite3.Connection,
    *,
    face_id: str,
    person_id: int,
    assignment_kind: str,
    operation_id: str,
    now_ms: int,
    revision_floor: int = 0,
) -> None:
    """Upsert one effective link; an existing row keeps its similarity as bookkeeping."""
    existing = connection.execute(
        "SELECT confidence, revision FROM face_person_links WHERE face_id = ? AND person_id = ? "
        "AND assignment_kind = ?",
        (str(face_id), int(person_id), str(assignment_kind)),
    ).fetchone()
    confidence = 0.0 if existing is None else float(existing["confidence"])
    revision = max(
        int(revision_floor),
        0 if existing is None else int(existing["revision"]),
    ) + 1
    connection.execute(
        "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
        "confidence, reason, revision, last_operation_id, created_at, updated_at) "
        "VALUES (?, ?, ?, 'CONFIRMED', ?, NULL, ?, ?, ?, ?) "
        "ON CONFLICT(face_id, person_id, assignment_kind) DO UPDATE SET "
        "decision = 'CONFIRMED', reason = NULL, "
        "revision = excluded.revision, "
        "last_operation_id = excluded.last_operation_id, updated_at = excluded.updated_at",
        (
            str(face_id),
            int(person_id),
            str(assignment_kind),
            confidence,
            int(revision),
            str(operation_id),
            int(now_ms),
            int(now_ms),
        ),
    )


def _downgrade_machine_link(
    connection: sqlite3.Connection,
    *,
    face_id: str,
    person_id: int,
    reason: str,
    operation_id: str,
    now_ms: int,
) -> None:
    """Turn an existing ``AUTO/CONFIRMED`` row into the machine suggestion it now is."""
    connection.execute(
        "UPDATE face_person_links SET decision = 'SUGGESTED', reason = ?, "
        "revision = revision + 1, last_operation_id = ?, updated_at = ? "
        "WHERE face_id = ? AND person_id = ? AND assignment_kind = 'AUTO'",
        (str(reason), str(operation_id), int(now_ms), str(face_id), int(person_id)),
    )


def apply_one(
    connection: sqlite3.Connection,
    *,
    item: Mapping[str, Any],
    operation_id: str,
    now_ms: int,
) -> dict[str, Any]:
    """Apply one already-validated item and return its before/after bundles.

    The caller owns the revision gate, the transaction and the derived catch-up, so this
    function is the matrix of ADR-0007 §3 and nothing else.
    """
    face_id = str(item["face_id"])
    action = str(item["action"])
    mode = control_mode(connection, face_id)
    current_revision = logical_control_revision(connection, face_id)
    confirmed = _confirmed_links(connection, face_id)
    effective = confirmed[0] if confirmed else None
    effective_person = (
        None if effective is None else canonical_person(connection, int(effective["person_id"]))
    )
    before = read_control_bundle(connection, face_id)
    requested_target = item["target_person_id"]
    target = (
        None
        if requested_target is None
        else canonical_person(connection, int(requested_target))
    )

    if action == ACTION_UNASSIGN:
        if mode == MODE_MANUAL_UNASSIGNED and effective is None:
            # An answered request, not a new state: the ADR-0007 §3 no-op item.
            pass
        else:
            if effective is not None:
                _end_effective_link(
                    connection,
                    link=effective,
                    reason=contracts.REASON_MANUAL_UNASSIGNED,
                    operation_id=operation_id,
                    now_ms=now_ms,
                )
            _write_control(
                connection,
                face_id=face_id,
                mode=MODE_MANUAL_UNASSIGNED,
                revision=current_revision + 1,
                operation_id=operation_id,
                now_ms=now_ms,
            )
            if effective_person is not None:
                _write_exclusion(
                    connection,
                    face_id=face_id,
                    person_id=int(effective_person),
                    active=True,
                    operation_id=operation_id,
                    now_ms=now_ms,
                )
    elif action == ACTION_ASSIGN:
        owning = int(target or 0)
        excluded = set(active_exclusion_person_ids(connection, face_id))
        already_manual = (
            mode == MODE_MANUAL_ASSIGNED
            and effective is not None
            and int(effective_person or 0) == owning
            and str(effective["assignment_kind"]) == _ASSIGNMENT_KIND_MANUAL
            and owning not in excluded
        )
        if not already_manual:
            if effective is not None and int(effective_person or 0) != owning:
                _end_effective_link(
                    connection,
                    link=effective,
                    reason=contracts.REASON_HUMAN_CONFLICT,
                    operation_id=operation_id,
                    now_ms=now_ms,
                )
                _write_exclusion(
                    connection,
                    face_id=face_id,
                    person_id=int(effective_person),
                    active=True,
                    operation_id=operation_id,
                    now_ms=now_ms,
                )
            elif effective is not None and (
                str(effective["assignment_kind"]) != _ASSIGNMENT_KIND_MANUAL
            ):
                # The machine already linked this person; the human takes ownership, so the
                # AUTO row becomes a suggestion and one MANUAL row becomes effective. The
                # downgrade happens first, so the one-CONFIRMED index never sees two.
                _downgrade_machine_link(
                    connection,
                    face_id=face_id,
                    person_id=int(effective["person_id"]),
                    reason=contracts.REASON_HUMAN_CONFLICT,
                    operation_id=operation_id,
                    now_ms=now_ms,
                )
            _write_control(
                connection,
                face_id=face_id,
                mode=MODE_MANUAL_ASSIGNED,
                revision=current_revision + 1,
                operation_id=operation_id,
                now_ms=now_ms,
            )
            _establish_link(
                connection,
                face_id=face_id,
                person_id=owning,
                assignment_kind=_ASSIGNMENT_KIND_MANUAL,
                operation_id=operation_id,
                now_ms=now_ms,
            )
            if owning in excluded:
                # ADR-0007 §3: naming a previously denied person is a legitimate correction;
                # it lifts *that* denial only and leaves every other one in place.
                _write_exclusion(
                    connection,
                    face_id=face_id,
                    person_id=owning,
                    active=False,
                    operation_id=operation_id,
                    now_ms=now_ms,
                )
    elif mode != MODE_AUTO_ALLOWED:
        if effective is not None:
            _end_effective_link(
                connection,
                link=effective,
                reason=contracts.REASON_HUMAN_CONFLICT,
                operation_id=operation_id,
                now_ms=now_ms,
            )
        # Every exclusion is kept, and no suggestion is adopted (ADR-0007 §3 and §11).
        _write_control(
            connection,
            face_id=face_id,
            mode=MODE_AUTO_ALLOWED,
            revision=current_revision + 1,
            operation_id=operation_id,
            now_ms=now_ms,
        )
    return {
        "face_id": face_id,
        "before": before,
        "after": read_control_bundle(connection, face_id),
    }


# ----------------------------------------------------------------------- derived order
def _close_face_operation(
    connection: sqlite3.Connection,
    *,
    operation_id: str,
    operation_type: str,
    operation_revision: int,
    undo_of_operation_id: str | None,
    results: Sequence[Mapping[str, Any]],
    now_ms: int,
    refresh: revision_module.ProjectionRefresh | None,
) -> dict[str, Any]:
    """Write the ``FACE_CONTROL`` items, then the ADR-0007 §8 derived catch-up."""
    changed = False
    items: list[dict[str, Any]] = []
    for index, result in enumerate(results):
        before = result["before"]
        after = result["after"]
        answer = item_answer(str(result["face_id"]), before, after)
        changed = changed or bool(answer["changed"])
        connection.execute(
            "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
            "entity_id, entity_key, before_value_json, after_value_json, after_revision) "
            "VALUES (?, ?, 'FACE_CONTROL', NULL, ?, ?, ?, ?)",
            (
                str(operation_id),
                int(index),
                str(result["face_id"]),
                control_bundle_json(before),
                control_bundle_json(after),
                int(after["control"]["revision"]),
            ),
        )
        items.append(answer)
    if changed:
        # Imported lazily: the person module imports this one for its item projection.
        from photo_search.persons import operations as operation_module

        filter_revision = operation_module.publish_derived(
            connection,
            faces=[str(result["face_id"]) for result in results],
            now_ms=int(now_ms),
            # ADR-0007 §8 explicitly forbids a face correction from writing the image
            # vector ``index_outbox``. The shared helper still owns the filter revision,
            # search-document queue and face-candidate repair; only the optional P2 image
            # projection refresh is disabled for this operation family.
            refresh=None,
        )
    else:
        filter_revision = revision_module.read_filter_revision(connection)
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
        "changed": bool(changed),
        "catalog_filter_revision": int(filter_revision),
        "replayed": False,
        "items": items,
    }


def _replay_face(
    connection: sqlite3.Connection, *, request_id: str, digest: str
) -> dict[str, Any] | None:
    """ADR-0007 §5: the face idempotency gate, ahead of every revision check."""
    row = connection.execute(
        "SELECT operation_id, payload_sha256, type, state, operation_revision, "
        "undo_of_operation_id, catalog_filter_revision FROM person_operations "
        "WHERE request_id = ?",
        (str(request_id),),
    ).fetchone()
    if row is None:
        return None
    if str(row["payload_sha256"]) != str(digest):
        raise fail(
            FACE_IDEMPOTENCY_CONFLICT,
            "this request_id is already bound to a different payload",
            operation_id=str(row["operation_id"]),
        )
    from photo_search.persons import retention as retention_module

    retained_items = retention_module.replay_receipt(connection, str(row["operation_id"]))
    items = (
        retained_items
        if retained_items is not None
        else [
            recorded_item_projection(item)
            for item in stored_items(connection, str(row["operation_id"]))
        ]
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "operation_id": str(row["operation_id"]),
        "operation_type": str(row["type"]),
        "operation_revision": int(row["operation_revision"]),
        "status": "UNDONE" if str(row["state"]) == "UNDONE" else "COMMITTED",
        "undo_of_operation_id": (
            None if row["undo_of_operation_id"] is None else str(row["undo_of_operation_id"])
        ),
        "changed": any(bool(item["changed"]) for item in items),
        "catalog_filter_revision": int(row["catalog_filter_revision"]),
        "replayed": True,
        "items": items,
    }


def _open_operation(
    connection: sqlite3.Connection,
    *,
    operation_id: str,
    request_id: str,
    digest: str,
    operation_type: str,
    target_person_id: int | None,
    undo_of_operation_id: str | None,
    now_ms: int,
) -> int:
    operation_revision = int(
        connection.execute(
            "SELECT COALESCE(MAX(operation_revision), 0) + 1 FROM person_operations"
        ).fetchone()[0]
    )
    # ADR-0007 §2: a face-level operation never names person sources; only a single
    # FACE_ASSIGN may record its canonical target (the per-face targets live in the items).
    connection.execute(
        "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
        "source_person_ids_json, target_person_id, state, operation_revision, "
        "undo_of_operation_id, undone_by, catalog_filter_revision, created_at, committed_at) "
        "VALUES (?, ?, ?, ?, '[]', ?, 'COMMITTED', ?, ?, NULL, 0, ?, ?)",
        (
            str(operation_id),
            str(request_id),
            str(digest),
            str(operation_type),
            None if target_person_id is None else int(target_person_id),
            int(operation_revision),
            None if undo_of_operation_id is None else str(undo_of_operation_id),
            int(now_ms),
            int(now_ms),
        ),
    )
    return int(operation_revision)


# ---------------------------------------------------------------------- public writers
def apply_single(
    connection: sqlite3.Connection,
    *,
    face_id: Any,
    contract_version: Any,
    action: Any,
    target_person_id: Any,
    expected_revision: Any,
    request_id: Any,
    now_ms: int,
    refresh: revision_module.ProjectionRefresh | None = None,
) -> dict[str, Any]:
    """One explicit ``face_id`` correction: assign, unassign or restore_auto."""
    request_id = check_request_id(request_id)
    check_contract_version(contract_version)
    item = normalize_item(
        {
            "face_id": face_id,
            "action": action,
            "target_person_id": target_person_id,
            "expected_revision": expected_revision,
        },
        index=0,
    )
    digest = payload_sha256({"endpoint": ENDPOINT_SINGLE, **_requested_form(item)})
    replay = _replay_face(connection, request_id=request_id, digest=digest)
    if replay is not None:
        return replay
    face = require_face(connection, str(item["face_id"]))
    target = item["target_person_id"]
    canonical_target = (
        None
        if target is None
        else canonical_person(connection, int(target))
    )
    require_expected_revision(
        connection, face_id=str(face["face_id"]), expected=int(item["expected_revision"])
    )
    _require_state(connection, str(face["face_id"]))
    operation_id = operation_id_for(request_id)
    operation_revision = _open_operation(
        connection,
        operation_id=operation_id,
        request_id=request_id,
        digest=digest,
        operation_type=ACTION_OPERATION_TYPES[str(item["action"])],
        target_person_id=canonical_target,
        undo_of_operation_id=None,
        now_ms=int(now_ms),
    )
    result = apply_one(
        connection, item=item, operation_id=operation_id, now_ms=int(now_ms)
    )
    return _close_face_operation(
        connection,
        operation_id=operation_id,
        operation_type=ACTION_OPERATION_TYPES[str(item["action"])],
        operation_revision=operation_revision,
        undo_of_operation_id=None,
        results=[result],
        now_ms=int(now_ms),
        refresh=refresh,
    )


def apply_batch(
    connection: sqlite3.Connection,
    *,
    items: Any,
    contract_version: Any,
    request_id: Any,
    now_ms: int,
    refresh: revision_module.ProjectionRefresh | None = None,
) -> dict[str, Any]:
    """1..500 explicit, de-duplicated face items, in one transaction or not at all."""
    request_id = check_request_id(request_id)
    check_contract_version(contract_version)
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise fail("VALIDATION_ERROR", "items must be a list", field="items")
    if not items:
        raise fail("VALIDATION_ERROR", "a batch needs at least one item", field="items")
    if len(items) > MAX_FACE_BATCH_SIZE:
        raise fail(
            FACE_BATCH_TOO_LARGE,
            f"a batch carries at most {MAX_FACE_BATCH_SIZE} items",
            field="items",
            max_batch_size=MAX_FACE_BATCH_SIZE,
            item_count=len(items),
        )
    normalized = [normalize_item(item, index=index) for index, item in enumerate(items)]
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in normalized:
        if str(item["face_id"]) in seen:
            duplicates.append(str(item["face_id"]))
        seen.add(str(item["face_id"]))
    if duplicates:
        # ADR-0007 §5: a repeated face is a refusal, never a silent de-duplication.
        raise fail(
            "VALIDATION_ERROR",
            "a batch must not name the same face twice",
            field="items",
            face_ids=sorted(set(duplicates)),
        )
    if len(seen) > MAX_FACE_BATCH_SIZE:
        raise fail(
            FACE_BATCH_TOO_LARGE,
            f"a batch carries at most {MAX_FACE_BATCH_SIZE} distinct faces",
            field="items",
            max_batch_size=MAX_FACE_BATCH_SIZE,
            item_count=len(seen),
        )
    ordered = _sorted_items(normalized)
    digest = payload_sha256(
        {"endpoint": ENDPOINT_BATCH, "items": [_requested_form(item) for item in ordered]}
    )
    replay = _replay_face(connection, request_id=request_id, digest=digest)
    if replay is not None:
        return replay
    # Whole-batch validation before any write: face, canonical target, revision, state.
    for item in ordered:
        face_id = str(item["face_id"])
        require_face(connection, face_id)
        target = item["target_person_id"]
        if target is not None:
            canonical_person(connection, int(target))
        require_expected_revision(
            connection, face_id=face_id, expected=int(item["expected_revision"])
        )
        _require_state(connection, face_id)
    operation_id = operation_id_for(request_id)
    operation_revision = _open_operation(
        connection,
        operation_id=operation_id,
        request_id=request_id,
        digest=digest,
        operation_type=BATCH_OPERATION_TYPE,
        target_person_id=None,
        undo_of_operation_id=None,
        now_ms=int(now_ms),
    )
    results = [
        apply_one(connection, item=item, operation_id=operation_id, now_ms=int(now_ms))
        for item in ordered
    ]
    return _close_face_operation(
        connection,
        operation_id=operation_id,
        operation_type=BATCH_OPERATION_TYPE,
        operation_revision=operation_revision,
        undo_of_operation_id=None,
        results=results,
        now_ms=int(now_ms),
        refresh=refresh,
    )


# ------------------------------------------------------------------------- compensation
def restore_control_bundle(
    connection: sqlite3.Connection,
    *,
    face_id: str,
    live: Mapping[str, Any],
    wanted: Mapping[str, Any],
    operation_id: str,
    now_ms: int,
) -> bool:
    """Restore one face's control, link and exclusion semantics - revisions go forward."""
    live_control = live.get("control") or {}
    wanted_control = wanted.get("control") or {}
    live_mode = str(live_control.get("mode") or MODE_AUTO_ALLOWED)
    wanted_mode = str(wanted_control.get("mode") or MODE_AUTO_ALLOWED)
    live_assignment = live.get("assignment")
    wanted_assignment = wanted.get("assignment")
    live_key = (
        None
        if live_assignment is None
        else (int(live_assignment["person_id"]), str(live_assignment["assignment_kind"]))
    )
    wanted_key = (
        None
        if wanted_assignment is None
        else (int(wanted_assignment["person_id"]), str(wanted_assignment["assignment_kind"]))
    )
    live_exclusions = {
        int(item["person_id"]): bool(item["active"]) for item in live.get("exclusions", [])
    }
    wanted_exclusions = {
        int(item["person_id"]): bool(item["active"])
        for item in wanted.get("exclusions", [])
    }
    pending_exclusions = [
        person_id
        for person_id in sorted(set(live_exclusions) | set(wanted_exclusions))
        if bool(live_exclusions.get(person_id, False))
        != bool(wanted_exclusions.get(person_id, False))
    ]
    if live_mode == wanted_mode and live_key == wanted_key and not pending_exclusions:
        return False
    # ADR-0007 §7: the restored semantics are the *before* ones, but the logical revision
    # always moves forward from the live value - it never rewinds to the recorded one.
    _write_control(
        connection,
        face_id=face_id,
        mode=wanted_mode,
        revision=max(
            int(live_control.get("revision") or 0), int(wanted_control.get("revision") or 0)
        )
        + 1,
        operation_id=operation_id,
        now_ms=now_ms,
    )
    if live_key != wanted_key:
        for link in _confirmed_links(connection, face_id):
            _end_effective_link(
                connection,
                link=link,
                reason=contracts.REASON_HUMAN_CONFLICT,
                operation_id=operation_id,
                now_ms=now_ms,
            )
        if wanted_assignment is not None:
            # The current effective row may have a different key and may just have been
            # deleted (MANUAL) or downgraded (AUTO). Include both its recorded revision and
            # every surviving row in the floor so compensation is strictly forward for the
            # face, not merely forward for the row being re-established.
            live_assignment_revision = (
                0 if live_assignment is None else int(live_assignment["revision"])
            )
            surviving_revision = int(
                connection.execute(
                    "SELECT COALESCE(MAX(revision), 0) FROM face_person_links "
                    "WHERE face_id = ?",
                    (str(face_id),),
                ).fetchone()[0]
            )
            _establish_link(
                connection,
                face_id=face_id,
                person_id=int(wanted_assignment["person_id"]),
                assignment_kind=str(wanted_assignment["assignment_kind"]),
                operation_id=operation_id,
                now_ms=now_ms,
                revision_floor=max(
                    int(wanted_assignment["revision"]),
                    live_assignment_revision,
                    surviving_revision,
                ),
            )
    for person_id in pending_exclusions:
        _write_exclusion(
            connection,
            face_id=face_id,
            person_id=int(person_id),
            active=bool(wanted_exclusions.get(person_id, False)),
            operation_id=operation_id,
            now_ms=now_ms,
        )
    return True


def undo_face_operation(
    connection: sqlite3.Connection,
    *,
    original: Any,
    request_id: str,
    digest: str,
    expected_operation_revision: int,
    now_ms: int,
    refresh: revision_module.ProjectionRefresh | None = None,
) -> dict[str, Any]:
    """Compensate one committed face operation, or refuse the whole request with zero writes."""
    original_id = str(original["operation_id"])
    if (
        str(original["type"]) not in contracts.FACE_OPERATION_TYPES
        or str(original["state"]) != "COMMITTED"
        or original["undone_by"] is not None
        or int(original["operation_revision"]) != int(expected_operation_revision)
    ):
        raise fail(
            FACE_OPERATION_NOT_UNDOABLE,
            "this operation is already reversed, is an undo itself, or its revision changed",
            operation_id=original_id,
            expected_revision=int(expected_operation_revision),
            current_revision=int(original["operation_revision"]),
            status=str(original["state"]),
        )
    pending: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    conflicts: list[dict[str, Any]] = []
    for row in stored_items(connection, original_id):
        if str(row["entity_type"]) != contracts.ENTITY_TYPE_FACE_CONTROL:
            raise fail(
                FACE_OPERATION_NOT_UNDOABLE,
                "this operation mixes person and face items",
                operation_id=original_id,
            )
        face_id = str(row["entity_key"])
        recorded_after = load_control_bundle(row["after_value_json"])
        recorded_before = load_control_bundle(row["before_value_json"])
        if connection.execute(
            "SELECT 1 FROM faces WHERE face_id = ?", (face_id,)
        ).fetchone() is None:
            conflicts.append(
                {
                    "entity_type": contracts.ENTITY_TYPE_FACE_CONTROL,
                    "face_id": face_id,
                    "reason": "face_missing",
                }
            )
            continue
        live = read_control_bundle(connection, face_id)
        differences = control_bundle_differences(live, recorded_after)
        if differences:
            conflicts.append(
                {
                    "entity_type": contracts.ENTITY_TYPE_FACE_CONTROL,
                    "face_id": face_id,
                    "reason": ",".join(differences),
                }
            )
            continue
        pending.append((face_id, live, recorded_before))
    if conflicts:
        raise fail(
            FACE_OPERATION_NOT_UNDOABLE,
            "a target of this operation was edited afterwards",
            operation_id=original_id,
            conflicts=conflicts,
        )
    new_operation_id = operation_id_for(request_id)
    new_operation_revision = _open_operation(
        connection,
        operation_id=new_operation_id,
        request_id=request_id,
        digest=digest,
        operation_type="UNDO",
        target_person_id=(
            None if original["target_person_id"] is None else int(original["target_person_id"])
        ),
        undo_of_operation_id=original_id,
        now_ms=int(now_ms),
    )
    results: list[dict[str, Any]] = []
    for face_id, live, recorded_before in pending:
        restore_control_bundle(
            connection,
            face_id=face_id,
            live=live,
            wanted=recorded_before,
            operation_id=new_operation_id,
            now_ms=int(now_ms),
        )
        results.append(
            {
                "face_id": face_id,
                "before": live,
                "after": read_control_bundle(connection, face_id),
            }
        )
    connection.execute(
        "UPDATE person_operations SET state = 'UNDONE', undone_by = ? WHERE operation_id = ?",
        (str(new_operation_id), str(original_id)),
    )
    return _close_face_operation(
        connection,
        operation_id=new_operation_id,
        operation_type="UNDO",
        operation_revision=new_operation_revision,
        undo_of_operation_id=original_id,
        results=results,
        now_ms=int(now_ms),
        refresh=refresh,
    )


__all__ = [
    "ACTION_ASSIGN",
    "ACTION_OPERATION_TYPES",
    "ACTION_RESTORE_AUTO",
    "ACTION_UNASSIGN",
    "BATCH_OPERATION_TYPE",
    "CONTRACT_VERSION",
    "MODE_AUTO_ALLOWED",
    "MODE_MANUAL_ASSIGNED",
    "MODE_MANUAL_UNASSIGNED",
    "active_exclusion_person_ids",
    "apply_batch",
    "apply_one",
    "apply_single",
    "canonical_person",
    "check_action",
    "check_contract_version",
    "check_face_id",
    "check_target",
    "control_bundle_differences",
    "control_bundle_json",
    "control_mode",
    "control_state",
    "item_answer",
    "load_control_bundle",
    "logical_control_revision",
    "logical_control_revisions",
    "normalize_item",
    "read_control_bundle",
    "recorded_item_projection",
    "require_expected_revision",
    "require_face",
    "restore_control_bundle",
    "stored_items",
    "undo_face_operation",
]
