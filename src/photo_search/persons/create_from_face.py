"""Create one stable unnamed person from one explicitly selected inbox face."""

from __future__ import annotations

import sqlite3
from typing import Any

from photo_search.catalog.errors import (
    FACE_ASSIGNMENT_CONFLICT,
    FACE_IDEMPOTENCY_CONFLICT,
    FACE_STATE_CONFLICT,
)
from photo_search.persons import albums, contracts, face_correction, operations, split

CONTRACT_VERSION = "p4-person-create-from-face.v1"
ENDPOINT = "POST /api/v1/faces/{face_id}/create-person"
OPERATION_TYPE = "CREATE_PERSON_FROM_FACE"


def check_contract_version(value: Any) -> str:
    if value != CONTRACT_VERSION:
        raise contracts.fail(
            "VALIDATION_ERROR", "unsupported contract_version", field="contract_version"
        )
    return CONTRACT_VERSION


def result(connection: sqlite3.Connection, operation: Any) -> dict[str, Any]:
    from photo_search.persons import retention

    items = retention.replay_receipt(connection, str(operation["operation_id"]))
    if items is None:
        items = [
            face_correction.recorded_item_projection(row)
            for row in face_correction.stored_items(connection, str(operation["operation_id"]))
        ]
    return {
        "contract_version": CONTRACT_VERSION,
        "new_person_id": int(operation["target_person_id"]),
        "face_id": str(items[0]["face_id"]),
        "operation_id": str(operation["operation_id"]),
        "operation_type": OPERATION_TYPE,
        "operation_revision": int(operation["operation_revision"]),
        "status": "COMMITTED",
        "changed": any(bool(item["changed"]) for item in items),
        "catalog_filter_revision": int(operation["catalog_filter_revision"]),
        "replayed": True,
        "items": items,
    }


def _replay(connection: sqlite3.Connection, request_id: str, digest: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM person_operations WHERE request_id = ?", (request_id,)
    ).fetchone()
    if row is None:
        return None
    if str(row["payload_sha256"]) != digest:
        raise contracts.fail(
            FACE_IDEMPOTENCY_CONFLICT,
            "this request_id is already bound to a different payload",
            operation_id=str(row["operation_id"]),
        )
    return result(connection, row)


def _require_eligible(connection: sqlite3.Connection, face_id: str) -> None:
    face_correction.require_face(connection, face_id)
    # Keep the exact inbox visibility/member/hold/hidden rules authoritative. The "all"
    # quality view permits explicit user selection of low-quality detections.
    eligible = connection.execute(
        "SELECT f.face_id FROM faces AS f JOIN assets AS a ON a.asset_id=f.asset_id "
        "LEFT JOIN face_assignment_controls AS c ON c.face_id=f.face_id "
        "WHERE f.face_id=? AND a.state='ACTIVE' AND EXISTS (SELECT 1 FROM file_locations "
        "AS loc WHERE loc.asset_id=a.asset_id AND loc.availability='PRESENT') AND "
        + albums._INBOX_WHERE,
        (face_id, *albums._VISIBILITY_PARAMETERS),
    ).fetchone()
    if eligible is None:
        raise contracts.fail(
            FACE_ASSIGNMENT_CONFLICT,
            "the face is no longer available for inbox organization",
            face_id=face_id,
        )


def create_person(
    connection: sqlite3.Connection,
    *,
    face_id: Any,
    contract_version: Any,
    request_id: Any,
    expected_control_revision: Any,
    now_ms: int,
) -> dict[str, Any]:
    request_id = contracts.check_request_id(request_id)
    check_contract_version(contract_version)
    face_id = str(face_id)
    if len(face_id) != 64 or any(char not in "0123456789abcdef" for char in face_id):
        raise contracts.fail("VALIDATION_ERROR", "face_id is invalid", field="face_id")
    expected = contracts.check_expected_revision(
        expected_control_revision, field="expected_control_revision"
    )
    digest = contracts.payload_sha256(
        {
            "endpoint": ENDPOINT,
            "face_id": face_id,
            "expected_control_revision": expected,
            "contract_version": CONTRACT_VERSION,
        }
    )
    replay = _replay(connection, request_id, digest)
    if replay is not None:
        return replay

    _require_eligible(connection, face_id)
    face_correction.require_expected_revision(
        connection, face_id=face_id, expected=expected
    )
    face_correction._require_state(connection, face_id)

    operation_id = contracts.operation_id_for(request_id)
    person_id = split._create_person(
        connection, now_ms=int(now_ms), avatar_face_id=face_id
    )
    operation_revision = int(
        connection.execute(
            "SELECT COALESCE(MAX(operation_revision), 0) + 1 FROM person_operations"
        ).fetchone()[0]
    )
    connection.execute(
        "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
        "source_person_ids_json, target_person_id, state, operation_revision, "
        "undo_of_operation_id, undone_by, catalog_filter_revision, created_at, committed_at) "
        "VALUES (?, ?, ?, ?, '[]', ?, 'COMMITTED', ?, NULL, NULL, 0, ?, ?)",
        (
            operation_id,
            request_id,
            digest,
            OPERATION_TYPE,
            person_id,
            operation_revision,
            int(now_ms),
            int(now_ms),
        ),
    )
    applied = face_correction.apply_one(
        connection,
        item={
            "face_id": face_id,
            "action": face_correction.ACTION_ASSIGN,
            "target_person_id": person_id,
            "expected_revision": expected,
        },
        operation_id=operation_id,
        now_ms=int(now_ms),
    )
    before, after = applied["before"], applied["after"]
    item = face_correction.item_answer(face_id, before, after)
    connection.execute(
        "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
        "entity_id, entity_key, before_value_json, after_value_json, after_revision) "
        "VALUES (?, 0, 'FACE_CONTROL', NULL, ?, ?, ?, ?)",
        (
            operation_id,
            face_id,
            face_correction.control_bundle_json(before),
            face_correction.control_bundle_json(after),
            int(after["control"]["revision"]),
        ),
    )
    if not item["changed"] or item["assigned_person_id"] != person_id:
        raise contracts.fail(FACE_STATE_CONFLICT, "the face assignment did not commit")
    filter_revision = operations.publish_derived(
        connection, faces=[face_id], now_ms=int(now_ms), refresh=None
    )
    connection.execute(
        "UPDATE person_operations SET catalog_filter_revision=? WHERE operation_id=?",
        (int(filter_revision), operation_id),
    )
    row = connection.execute(
        "SELECT * FROM person_operations WHERE operation_id=?", (operation_id,)
    ).fetchone()
    return {**result(connection, row), "replayed": False}


__all__ = ["CONTRACT_VERSION", "OPERATION_TYPE", "create_person", "result"]
