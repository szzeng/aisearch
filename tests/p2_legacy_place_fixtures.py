"""Explicit pre-source-gate manual state, loaded before restarting current services.

These are historical USER assignments, never valid inputs to today's ASSIGN endpoint.
Keep the old operation/history as well as the control row so cancellation and undo can
be tested without bypassing or monkeypatching the current source qualification gate.
"""

import hashlib
import json
import sqlite3
from pathlib import Path

from photo_search.config import catalog_db_path


def seed_legacy_assignment(data_root: Path, *, asset_id: int, place_id: int) -> None:
    operation_id = f"legacy-place-operation-{asset_id}"
    assignment_id = f"legacy-place-assignment-{asset_id}"
    payload = {"items": [{
        "asset_id": asset_id, "action": "ASSIGN", "target_place_id": place_id,
        "expected_revision": 0, "origin": None,
    }]}
    payload_digest = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    snapshot = {
        "contract_version": "p2-place-contracts.v1",
        "assignment_id": assignment_id,
        "asset_id": asset_id,
        "place_id": place_id,
        "mode": "MANUAL_ASSIGNED",
        "origin": "MANUAL",
        "manual_lock": True,
        "resolution_id": None,
        "revision": 1,
        "record_state": "ACTIVE",
        "actor": "LOCAL_USER",
        "operation_id": operation_id,
        "updated_at": 1,
    }
    connection = sqlite3.connect(catalog_db_path(data_root))
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        with connection:
            assert connection.execute(
                "SELECT origin FROM places WHERE place_id=?", (place_id,)
            ).fetchone() == ("USER",)
            assert connection.execute(
                "SELECT COUNT(*) FROM asset_place_assignments WHERE asset_id=?", (asset_id,)
            ).fetchone() == (0,)
            connection.execute(
                "INSERT INTO place_operations(operation_id, request_id, payload_sha256, "
                "operation_type, operation_revision, actor, atomic, status, created_at, "
                "committed_at) VALUES (?, ?, ?, 'ASSIGN', 1, 'LOCAL_USER', 1, 'COMMITTED', 1, 1)",
                (operation_id, operation_id, payload_digest),
            )
            connection.execute(
                "INSERT INTO asset_place_assignments(asset_id, assignment_id, place_id, mode, "
                "origin, manual_lock, revision, record_state, actor, operation_id, created_at, "
                "updated_at) VALUES (?, ?, ?, 'MANUAL_ASSIGNED', 'MANUAL', 1, 1, 'ACTIVE', "
                "'LOCAL_USER', ?, 1, 1)",
                (asset_id, assignment_id, place_id, operation_id),
            )
            connection.execute(
                "INSERT INTO asset_place_assignment_history SELECT asset_id, revision, "
                "assignment_id, place_id, mode, origin, manual_lock, resolution_id, record_state, "
                "actor, operation_id, created_at, updated_at FROM asset_place_assignments "
                "WHERE asset_id=?", (asset_id,),
            )
            connection.execute(
                "INSERT INTO place_operation_items(operation_id, item_index, asset_id, action, "
                "target_place_id, expected_revision, after_assignment_json) "
                "VALUES (?, 0, ?, 'ASSIGN', ?, 0, ?)",
                (operation_id, asset_id, place_id, json.dumps(snapshot, sort_keys=True)),
            )
    finally:
        connection.close()
