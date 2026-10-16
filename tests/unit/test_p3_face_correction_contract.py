"""P3-04B: the implemented face-correction protocol against its frozen ADR-0007 companion.

These are pure unit checks: the payload hash, the bounded snapshot comparison, the read
projections and the closed error vocabulary. They do not claim that a database or an HTTP
endpoint behaved - the lifecycle/migration/API suites are that evidence.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from photo_search.api.errors import status_for_code
from photo_search.catalog import errors as catalog_errors
from photo_search.persons import contracts
from photo_search.persons import face_correction as fc

ROOT = Path(__file__).resolve().parents[2]
COMPANION = ROOT / "docs" / "decisions" / "contracts" / "p3-face-correction-contracts.v1.json"

SCHEMA = json.loads(COMPANION.read_text(encoding="utf-8"))
FACE = "a" * 64

#: ADR-0007 §6 freezes the whole face-family error vocabulary.
EXPECTED_ERROR_CODES = {
    "FACE_NOT_FOUND",
    "PERSON_NOT_FOUND",
    "FACE_ASSIGNMENT_CONFLICT",
    "FACE_IDEMPOTENCY_CONFLICT",
    "FACE_BATCH_TOO_LARGE",
    "FACE_STATE_CONFLICT",
    "FACE_OPERATION_NOT_UNDOABLE",
    "PERSON_REDIRECT_CYCLE",
    "VALIDATION_ERROR",
}

EXPECTED_HTTP = {
    "FACE_NOT_FOUND": 404,
    "PERSON_NOT_FOUND": 404,
    "FACE_ASSIGNMENT_CONFLICT": 409,
    "FACE_IDEMPOTENCY_CONFLICT": 409,
    "FACE_BATCH_TOO_LARGE": 422,
    "FACE_STATE_CONFLICT": 409,
    "FACE_OPERATION_NOT_UNDOABLE": 409,
    "PERSON_REDIRECT_CYCLE": 409,
    "VALIDATION_ERROR": 422,
}


def _catalog() -> sqlite3.Connection:
    """A minimal synthetic schema for the read-only helpers (no real catalog needed)."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE persons(
            person_id INTEGER PRIMARY KEY,
            name TEXT, name_key TEXT, avatar_face_id TEXT,
            merged_into INTEGER REFERENCES persons(person_id),
            revision INTEGER, created_at INTEGER, updated_at INTEGER
        );
        CREATE TABLE face_assignment_controls(
            face_id TEXT PRIMARY KEY, mode TEXT, revision INTEGER,
            last_operation_id TEXT, updated_at INTEGER
        );
        CREATE TABLE face_person_links(
            face_id TEXT, person_id INTEGER, assignment_kind TEXT, decision TEXT,
            confidence REAL, reason TEXT, revision INTEGER, last_operation_id TEXT,
            created_at INTEGER, updated_at INTEGER,
            PRIMARY KEY(face_id, person_id, assignment_kind)
        );
        CREATE TABLE faces(face_id TEXT PRIMARY KEY, asset_id INTEGER);
        CREATE TABLE person_hidden(person_id INTEGER PRIMARY KEY, ignore_state TEXT);
        CREATE TABLE face_inbox_hidden(face_id TEXT PRIMARY KEY, ignore_state TEXT);
        CREATE TABLE face_exclusions(
            face_id TEXT, person_id INTEGER, operation_id TEXT, active INTEGER,
            created_at INTEGER, PRIMARY KEY(face_id, person_id)
        );
        INSERT INTO persons(person_id, merged_into, revision, created_at, updated_at)
            VALUES (7, NULL, 1, 1, 1), (8, 7, 1, 1, 1), (9, NULL, 1, 1, 1);
        """
    )
    connection.execute("INSERT INTO faces(face_id, asset_id) VALUES (?, 1)", (FACE,))
    return connection


def test_the_implementation_matches_the_companion_literals() -> None:
    assert SCHEMA["$defs"]["ContractVersion"]["const"] == fc.CONTRACT_VERSION
    assert set(SCHEMA["$defs"]["OperationType"]["enum"]) == set(
        contracts.FACE_OPERATION_TYPES
    ) | {"UNDO"}
    assert set(SCHEMA["$defs"]["ControlMode"]["enum"]) == {
        fc.MODE_AUTO_ALLOWED,
        fc.MODE_MANUAL_ASSIGNED,
        fc.MODE_MANUAL_UNASSIGNED,
    }
    assert set(SCHEMA["$defs"]["Action"]["enum"]) == set(fc.ACTION_OPERATION_TYPES)
    assert SCHEMA["$defs"]["OperationType"]["enum"][:4] == list(contracts.FACE_OPERATION_TYPES)
    assert contracts.ENTITY_TYPE_FACE_CONTROL == "FACE_CONTROL"
    assert contracts.ENTITY_TYPE_PERSON_BUNDLE == "PERSON_BUNDLE"
    assert contracts.UNDOABLE_OPERATION_TYPES == (
        contracts.WRITE_OPERATION_TYPES + contracts.FACE_OPERATION_TYPES
    )


def test_the_error_vocabulary_is_closed_and_fully_mapped() -> None:
    companion = set(SCHEMA["$defs"]["FaceApiError"]["properties"]["code"]["enum"])
    assert companion == EXPECTED_ERROR_CODES
    for code in EXPECTED_ERROR_CODES:
        assert status_for_code(code) == EXPECTED_HTTP[code]
    # Every face-family code except the shared request-shape one is a catalog constant.
    for code in EXPECTED_ERROR_CODES - {"VALIDATION_ERROR"}:
        assert getattr(catalog_errors, code) == code
    assert not hasattr(catalog_errors, "VALIDATION_ERROR")
    assert catalog_errors.MAX_FACE_BATCH_SIZE == (
        SCHEMA["$defs"]["FaceCorrectionBatchRequest"]["properties"]["items"]["maxItems"]
    )
    # The action/target rule of the document and of the implementation agree.
    with pytest.raises(Exception):
        fc.check_target(fc.ACTION_UNASSIGN, 7)
    assert fc.check_target(fc.ACTION_ASSIGN, 7) == 7
    assert fc.check_target(fc.ACTION_RESTORE_AUTO, None) is None


def test_the_batch_payload_hash_ignores_the_request_order_but_not_the_target() -> None:
    first = {
        "face_id": FACE,
        "action": "assign",
        "target_person_id": 9,
        "expected_revision": 2,
    }
    second = {
        "face_id": "b" * 64,
        "action": "unassign",
        "target_person_id": None,
        "expected_revision": 0,
    }
    forward = [fc._requested_form(item) for item in fc._sorted_items([first, second])]
    backward = [fc._requested_form(item) for item in fc._sorted_items([second, first])]
    assert forward == backward
    assert [item["face_id"] for item in fc._sorted_items([second, first])] == [FACE, "b" * 64]
    changed_target = [
        fc._requested_form(item)
        for item in fc._sorted_items([{**first, "target_person_id": 8}, second])
    ]
    assert changed_target != forward


def test_the_revision_gate_reads_zero_for_a_face_without_a_control_row() -> None:
    connection = _catalog()
    try:
        assert fc.logical_control_revision(connection, FACE) == 0
        assert fc.control_mode(connection, FACE) == fc.MODE_AUTO_ALLOWED
        connection.execute(
            "INSERT INTO face_assignment_controls(face_id, mode, revision, "
            "last_operation_id, updated_at) VALUES (?, 'MANUAL_ASSIGNED', 4, 'op-1', 9)",
            (FACE,),
        )
        assert fc.logical_control_revision(connection, FACE) == 4
        assert fc.control_mode(connection, FACE) == fc.MODE_MANUAL_ASSIGNED
        assert fc.logical_control_revisions(connection, [FACE, "b" * 64]) == {
            FACE: 4,
            "b" * 64: 0,
        }
        assert fc.control_state(connection, FACE)["mode"] == fc.MODE_MANUAL_ASSIGNED
    finally:
        connection.close()


def test_the_snapshot_ignores_clocks_and_reports_semantic_moves() -> None:
    def bundle(
        *,
        mode: str = fc.MODE_MANUAL_ASSIGNED,
        revision: int = 2,
        person: int | None = 7,
        assignment_revision: int = 3,
        exclusions: list[tuple[int, bool]] | None = None,
        updated_at: int = 100,
    ) -> dict:
        return {
            "face_id": FACE,
            "control": {
                "mode": mode,
                "revision": revision,
                "last_operation_id": "op-1",
                "updated_at": updated_at,
            },
            "assignment": (
                None
                if person is None
                else {
                    "person_id": person,
                    "assignment_kind": "MANUAL",
                    "decision": "CONFIRMED",
                    "reason": None,
                    "revision": assignment_revision,
                    "last_operation_id": "op-1",
                    "created_at": 5,
                    "updated_at": updated_at,
                }
            ),
            "exclusions": [
                {
                    "person_id": person_id,
                    "active": active,
                    "operation_id": "op-1",
                    "created_at": 1,
                }
                for person_id, active in (exclusions or [])
            ],
        }

    base = bundle(exclusions=[(9, True)])
    assert fc.control_bundle_differences(
        base, bundle(exclusions=[(9, True)], updated_at=999)
    ) == []
    assert fc.control_bundle_differences(
        bundle(mode=fc.MODE_AUTO_ALLOWED, exclusions=[(9, True)]), base
    ) == ["control.mode"]
    assert fc.control_bundle_differences(bundle(person=None, exclusions=[(9, True)]), base) == [
        "assignment"
    ]
    assert fc.control_bundle_differences(bundle(exclusions=[(9, False)]), base) == ["exclusions"]
    assert fc.control_bundle_differences(base, bundle(revision=3, exclusions=[(9, True)])) == [
        "control.revision"
    ]
    assert fc.control_bundle_differences(
        base, bundle(assignment_revision=4, exclusions=[(9, True)])
    ) == ["assignment.revision"]


def test_the_item_answer_reports_only_the_frozen_fields() -> None:
    before = {
        "face_id": FACE,
        "control": {"mode": fc.MODE_AUTO_ALLOWED, "revision": 0, "last_operation_id": None},
        "assignment": None,
        "exclusions": [{"person_id": 9, "active": False, "operation_id": None}],
    }
    after = {
        "face_id": FACE,
        "control": {
            "mode": fc.MODE_MANUAL_UNASSIGNED,
            "revision": 1,
            "last_operation_id": "op-1",
        },
        "assignment": None,
        "exclusions": [{"person_id": 9, "active": True, "operation_id": "op-1"}],
    }
    answer = fc.item_answer(FACE, before, after)
    assert set(answer) == {
        "entity_type",
        "face_id",
        "changed",
        "mode",
        "revision",
        "assigned_person_id",
        "exclusions",
    }
    assert answer == {
        "entity_type": "FACE_CONTROL",
        "face_id": FACE,
        "changed": True,
        "mode": fc.MODE_MANUAL_UNASSIGNED,
        "revision": 1,
        "assigned_person_id": None,
        "exclusions": {"activated": [9], "deactivated": []},
    }
    assert fc.item_answer(FACE, before, before)["changed"] is False


def test_the_read_projection_canonicalises_denials_and_deduplicates_them() -> None:
    connection = _catalog()
    try:
        connection.execute(
            "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
            "confidence, reason, revision, last_operation_id, created_at, updated_at) "
            "VALUES (?, 8, 'AUTO', 'CONFIRMED', 0.5, NULL, 1, NULL, 1, 1)",
            (FACE,),
        )
        # Person 8 was merged into 7, and both the old and the new id carry a denial.
        for person_id in (7, 8):
            connection.execute(
                "INSERT INTO face_exclusions(face_id, person_id, operation_id, active, "
                "created_at) VALUES (?, ?, 'op-1', 1, 1)",
                (FACE, person_id),
            )
        connection.execute(
            "INSERT INTO face_exclusions(face_id, person_id, operation_id, active, "
            "created_at) VALUES (?, 9, 'op-1', 0, 1)",
            (FACE,),
        )
        state = fc.control_state(connection, FACE)
        assert state["assigned_person_id"] == 7
        # The two spellings of the same denial collapse into one canonical pair.
        assert state["active_exclusions"] == [7]
        assert state["suggestions_available"] is False
        bundle = fc.read_control_bundle(connection, FACE)
        assert bundle["assignment"]["person_id"] == 7
        assert [item["person_id"] for item in bundle["exclusions"]] == [7, 7, 9]
        round_trip = fc.load_control_bundle(fc.control_bundle_json(bundle))
        assert round_trip == bundle
    finally:
        connection.close()
