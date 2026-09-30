"""Manual place control: one current row per asset, a lock, exclusions and compensations.

ADR-0005 §5/§6 in one place: ``AUTO_ALLOWED`` / ``MANUAL_ASSIGNED`` / ``MANUAL_UNASSIGNED``
are the only modes. Automatic boundary results live separately (ADR-0014); manual
control and exclusions govern the effective-place view. Every write is one operation with a
``request_id``, a canonical payload hash and expected revisions, so a replay is provably
the same request and a batch is all-or-nothing inside one short transaction.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from photo_search.catalog.errors import (
    MAX_PLACE_BATCH_SIZE,
    PLACE_ASSIGNMENT_CONFLICT,
    PLACE_BATCH_TOO_LARGE,
    PLACE_IDEMPOTENCY_CONFLICT,
    PLACE_NOT_FOUND,
    PLACE_OPERATION_NOT_UNDOABLE,
    PLACE_REFERENCED,
)
from photo_search.places import directory
from photo_search.places import revision as revision_module
from photo_search.places import suggestions as suggestion_module
from photo_search.places.contracts import (
    ASSIGNMENT_ACTIONS,
    OPERATION_BY_ACTION,
    assignment_id_for,
    assignment_view,
    check_choice,
    check_expected_revision,
    check_request_id,
    fail,
    operation_id_for,
    payload_sha256,
)

ASSIGNMENT_FIELDS = (
    "asset_id",
    "assignment_id",
    "place_id",
    "mode",
    "origin",
    "manual_lock",
    "resolution_id",
    "revision",
    "record_state",
    "actor",
    "operation_id",
    "created_at",
    "updated_at",
)

OPERATION_FIELDS = (
    "operation_id",
    "request_id",
    "payload_sha256",
    "operation_type",
    "operation_revision",
    "actor",
    "atomic",
    "status",
    "undo_of_operation_id",
    "catalog_filter_revision",
    "created_at",
    "committed_at",
)

ITEM_FIELDS = (
    "operation_id",
    "item_index",
    "asset_id",
    "place_id",
    "action",
    "target_place_id",
    "expected_revision",
    "before_assignment_json",
    "after_assignment_json",
    "exclusion_deltas_json",
)


def current_row(connection: sqlite3.Connection, asset_id: int) -> sqlite3.Row | None:
    return connection.execute(
        f"SELECT {', '.join(ASSIGNMENT_FIELDS)} FROM asset_place_assignments WHERE asset_id = ?",
        (int(asset_id),),
    ).fetchone()


def read_current_view(connection: sqlite3.Connection, asset_id: int) -> dict[str, Any] | None:
    return assignment_view(current_row(connection, asset_id))


def _require_asset(connection: sqlite3.Connection, asset_id: int) -> None:
    row = connection.execute("SELECT 1 FROM assets WHERE asset_id = ?", (int(asset_id),)).fetchone()
    if row is None:
        raise fail(
            PLACE_NOT_FOUND,
            f"asset {int(asset_id)} does not exist",
            asset_ids=[int(asset_id)],
        )


def _require_active_place(connection: sqlite3.Connection, place_id: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT place_id, kind, lifecycle FROM places WHERE place_id = ?", (int(place_id),)
    ).fetchone()
    if row is None:
        raise fail(PLACE_NOT_FOUND, f"place {int(place_id)} does not exist", place_id=int(place_id))
    if str(row["lifecycle"]) != "ACTIVE":
        raise fail(
            PLACE_REFERENCED,
            "this place is retired and cannot receive new assignments",
            place_id=int(place_id),
        )
    return row


def _exclusion_row(
    connection: sqlite3.Connection, *, asset_id: int, place_id: int
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT active, revision FROM asset_place_exclusions WHERE asset_id = ? AND place_id = ?",
        (int(asset_id), int(place_id)),
    ).fetchone()


ACTIVE_EXCLUSIONS_SQL = """
SELECT e.place_id, p.canonical_name, e.revision, e.updated_at
  FROM asset_place_exclusions AS e
  JOIN places AS p ON p.place_id = e.place_id
 WHERE e.asset_id = ? AND e.active = 1
 ORDER BY e.place_id
"""


def exclusion_summary(connection: sqlite3.Connection, asset_id: int) -> list[dict[str, Any]]:
    return [
        {
            "place_id": int(row["place_id"]),
            "canonical_name": str(row["canonical_name"]),
            "revision": int(row["revision"]),
            "updated_at": int(row["updated_at"]),
        }
        for row in connection.execute(ACTIVE_EXCLUSIONS_SQL, (int(asset_id),)).fetchall()
    ]


# ------------------------------------------------------------------ request payloads
def _normalized_item(item: Mapping[str, Any], *, index: int) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise fail("VALIDATION_ERROR", "each batch item must be an object", index=index)
    asset_id = item.get("asset_id")
    if isinstance(asset_id, bool) or not isinstance(asset_id, int) or int(asset_id) < 1:
        raise fail("VALIDATION_ERROR", "asset_id must be a positive integer", index=index)
    action = check_choice(item.get("action"), ASSIGNMENT_ACTIONS, field="action")
    target = item.get("target_place_id")
    if target is not None and (isinstance(target, bool) or not isinstance(target, int)):
        raise fail("VALIDATION_ERROR", "target_place_id must be an integer or null", index=index)
    if action == "ASSIGN" and target is None:
        raise fail("VALIDATION_ERROR", "ASSIGN requires target_place_id", index=index)
    if action != "ASSIGN" and target is not None:
        raise fail("VALIDATION_ERROR", f"{action} must not carry target_place_id", index=index)
    # ADR-0005 §5: the only alternative origin is a confirmed machine suggestion. It is
    # optional, it is only meaningful for ASSIGN, and a manual edit always wins.
    origin = item.get("origin")
    if origin is not None and origin != "CONFIRMED_SUGGESTION":
        raise fail(
            "VALIDATION_ERROR",
            "origin must be CONFIRMED_SUGGESTION when it is supplied",
            index=index,
        )
    if origin is not None and action != "ASSIGN":
        raise fail("VALIDATION_ERROR", "only ASSIGN may confirm a suggestion", index=index)
    return {
        "asset_id": int(asset_id),
        "action": action,
        "target_place_id": None if target is None else int(target),
        "expected_revision": check_expected_revision(item.get("expected_revision")),
        "origin": None if origin is None else str(origin),
    }


def _check_batch_size(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not items:
        raise fail("VALIDATION_ERROR", "a place operation needs at least one item")
    if len(items) > MAX_PLACE_BATCH_SIZE:
        raise fail(
            PLACE_BATCH_TOO_LARGE,
            f"a place batch carries at most {MAX_PLACE_BATCH_SIZE} items",
            max_batch_size=MAX_PLACE_BATCH_SIZE,
        )
    normalized = [_normalized_item(item, index=index) for index, item in enumerate(items)]
    seen: set[int] = set()
    duplicates: list[int] = []
    for item in normalized:
        if item["asset_id"] in seen:
            duplicates.append(item["asset_id"])
        seen.add(item["asset_id"])
    if duplicates:
        raise fail(
            "VALIDATION_ERROR",
            "a batch must not name the same asset twice",
            asset_ids=sorted(set(duplicates)),
        )
    return sorted(normalized, key=lambda value: value["asset_id"])


def _request_payload(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "items": [
            {
                "asset_id": int(item["asset_id"]),
                "action": str(item["action"]),
                "target_place_id": item["target_place_id"],
                "expected_revision": int(item["expected_revision"]),
                "origin": item.get("origin"),
            }
            for item in items
        ]
    }


# ----------------------------------------------------------------------- operations
def _next_operation_revision(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT COALESCE(MAX(operation_revision), 0) + 1 FROM place_operations").fetchone()
    return int(row[0])


def _existing_operation(connection: sqlite3.Connection, request_id: str) -> sqlite3.Row | None:
    return connection.execute(
        f"SELECT {', '.join(OPERATION_FIELDS)} FROM place_operations WHERE request_id = ?",
        (str(request_id),),
    ).fetchone()


def _operation_items(connection: sqlite3.Connection, operation_id: str) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            f"SELECT {', '.join(ITEM_FIELDS)} FROM place_operation_items "
            "WHERE operation_id = ? ORDER BY item_index",
            (str(operation_id),),
        ).fetchall()
    )


def _snapshot(value: Mapping[str, Any] | None) -> str | None:
    return None if value is None else json.dumps(dict(value), sort_keys=True, separators=(",", ":"))


def _snapshot_load(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    return json.loads(str(raw))


def _exclusion_deltas(raw: Any) -> list[dict[str, int]]:
    if raw is None:
        return []
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    return [dict(item) for item in json.loads(str(raw))]


def _mode_key(view: Mapping[str, Any] | None) -> tuple[Any, Any, Any]:
    """The effective control triple; "no control row" is exactly ``AUTO_ALLOWED``."""
    if view is None:
        return ("AUTO_ALLOWED", None, "NONE")
    return (
        str(view["mode"]),
        None if view["place_id"] is None else int(view["place_id"]),
        str(view["origin"]),
    )


def _revision_state_key(view: Mapping[str, Any] | None) -> tuple[Any, Any, Any, Any]:
    """Revision plus effective state - what "still equals the recorded after" means."""
    revision = 0 if view is None else int(view["revision"])
    return (revision, *_mode_key(view))


def operation_result(connection: sqlite3.Connection, operation: sqlite3.Row) -> dict[str, Any]:
    """Rebuild the documented response of an already committed operation (replay)."""
    items = _operation_items(connection, str(operation["operation_id"]))
    rendered: list[dict[str, Any]] = []
    changed = False
    for row in items:
        before = _snapshot_load(row["before_assignment_json"])
        after = _snapshot_load(row["after_assignment_json"])
        deltas = _exclusion_deltas(row["exclusion_deltas_json"])
        cleared = [
            int(delta["place_id"])
            for delta in deltas
            if int(delta["before"]) == 1 and int(delta["after"]) == 0
        ]
        item_changed = (
            _revision_state_key(before) != _revision_state_key(after)
            or bool(deltas)
        )
        changed = changed or item_changed
        rendered.append(
            {
                "asset_id": None if row["asset_id"] is None else int(row["asset_id"]),
                "action": str(row["action"]),
                "assignment": after,
                "changed": item_changed,
                "cleared_exclusions": cleared,
            }
        )
    return {
        "operation_id": str(operation["operation_id"]),
        "operation_type": str(operation["operation_type"]),
        "operation_revision": int(operation["operation_revision"]),
        "status": str(operation["status"]),
        "undo_of_operation_id": (
            None
            if operation["undo_of_operation_id"] is None
            else str(operation["undo_of_operation_id"])
        ),
        "changed": changed,
        "catalog_filter_revision": int(operation["catalog_filter_revision"]),
        "replayed": True,
        "items": rendered,
    }


def _target_state(
    action: str, target_place_id: int | None, origin: str | None = None
) -> dict[str, Any]:
    if action == "ASSIGN":
        return {
            "mode": "MANUAL_ASSIGNED",
            "place_id": int(target_place_id),
            "origin": "CONFIRMED_SUGGESTION" if origin == "CONFIRMED_SUGGESTION" else "MANUAL",
            "manual_lock": True,
            "resolution_id": None,
        }
    if action == "UNASSIGN":
        return {
            "mode": "MANUAL_UNASSIGNED",
            "place_id": None,
            "origin": "MANUAL",
            "manual_lock": True,
            "resolution_id": None,
        }
    return {
        "mode": "AUTO_ALLOWED",
        "place_id": None,
        "origin": "NONE",
        "manual_lock": False,
        "resolution_id": None,
    }


def _write_assignment(
    connection: sqlite3.Connection,
    *,
    asset_id: int,
    state: Mapping[str, Any],
    revision: int,
    operation_id: str,
    now_ms: int,
    created_at: int,
) -> dict[str, Any]:
    connection.execute(
        "INSERT INTO asset_place_assignments(asset_id, assignment_id, place_id, mode, origin, "
        "manual_lock, resolution_id, revision, record_state, actor, operation_id, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', 'LOCAL_USER', ?, ?, ?) "
        "ON CONFLICT(asset_id) DO UPDATE SET assignment_id = excluded.assignment_id, "
        "place_id = excluded.place_id, mode = excluded.mode, origin = excluded.origin, "
        "manual_lock = excluded.manual_lock, resolution_id = excluded.resolution_id, "
        "revision = excluded.revision, record_state = 'ACTIVE', operation_id = excluded.operation_id, "
        "updated_at = excluded.updated_at",
        (
            int(asset_id),
            assignment_id_for(asset_id, revision),
            state["place_id"],
            str(state["mode"]),
            str(state["origin"]),
            1 if state["manual_lock"] else 0,
            state["resolution_id"],
            int(revision),
            str(operation_id),
            int(created_at),
            int(now_ms),
        ),
    )
    connection.execute(
        # Superseding is the one legal history update (ADR-0005 §5): a row that is already
        # SUPERSEDED is left untouched so the append-only guard never fires on a no-op.
        "UPDATE asset_place_assignment_history SET record_state = 'SUPERSEDED' "
        "WHERE asset_id = ? AND revision < ? AND record_state = 'ACTIVE'",
        (int(asset_id), int(revision)),
    )
    connection.execute(
        "INSERT INTO asset_place_assignment_history(asset_id, revision, assignment_id, place_id, "
        "mode, origin, manual_lock, resolution_id, record_state, actor, operation_id, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', 'LOCAL_USER', ?, ?, ?)",
        (
            int(asset_id),
            int(revision),
            assignment_id_for(asset_id, revision),
            state["place_id"],
            str(state["mode"]),
            str(state["origin"]),
            1 if state["manual_lock"] else 0,
            state["resolution_id"],
            str(operation_id),
            int(created_at),
            int(now_ms),
        ),
    )
    return {
        "contract_version": "p2-place-contracts.v1",
        "assignment_id": assignment_id_for(asset_id, revision),
        "asset_id": int(asset_id),
        "place_id": state["place_id"],
        "mode": str(state["mode"]),
        "origin": str(state["origin"]),
        "manual_lock": bool(state["manual_lock"]),
        "resolution_id": state["resolution_id"],
        "revision": int(revision),
        "record_state": "ACTIVE",
        "actor": "LOCAL_USER",
        "operation_id": str(operation_id),
        "updated_at": int(now_ms),
    }


def _set_exclusion(
    connection: sqlite3.Connection,
    *,
    asset_id: int,
    place_id: int,
    active: bool,
    operation_id: str,
    now_ms: int,
) -> bool:
    """Set the exclusion state; returns True when the stored state actually changed."""
    row = _exclusion_row(connection, asset_id=asset_id, place_id=place_id)
    wanted = 1 if active else 0
    if row is None:
        connection.execute(
            "INSERT INTO asset_place_exclusions(asset_id, place_id, active, revision, "
            "operation_id, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?, ?)",
            (int(asset_id), int(place_id), wanted, str(operation_id), int(now_ms), int(now_ms)),
        )
        return True
    if int(row["active"]) == wanted:
        return False
    connection.execute(
        "UPDATE asset_place_exclusions SET active = ?, revision = revision + 1, operation_id = ?, "
        "updated_at = ? WHERE asset_id = ? AND place_id = ?",
        (wanted, str(operation_id), int(now_ms), int(asset_id), int(place_id)),
    )
    return True


def apply_request(
    connection: sqlite3.Connection,
    *,
    items: Sequence[Mapping[str, Any]],
    request_id: str,
    now_ms: int,
    refresh: revision_module.ProjectionRefresh | None = None,
    batch: bool = False,
) -> dict[str, Any]:
    """One ASSIGN/UNASSIGN/RESTORE_AUTO request (single item or one atomic batch)."""
    request_id = check_request_id(request_id)
    normalized = _check_batch_size(items)
    payload = _request_payload(normalized)
    digest = payload_sha256(payload)
    existing = _existing_operation(connection, request_id)
    if existing is not None:
        if str(existing["payload_sha256"]) != digest:
            raise fail(
                PLACE_IDEMPOTENCY_CONFLICT,
                "this request_id is already bound to a different payload",
                operation_id=str(existing["operation_id"]),
            )
        return operation_result(connection, existing)

    actions = {item["action"] for item in normalized}
    if len(actions) != 1:
        raise fail("VALIDATION_ERROR", "one operation must use a single action", actions=sorted(actions))
    action = next(iter(actions))
    operation_type = (
        f"BATCH_{action}" if batch else OPERATION_BY_ACTION[action]
    )

    prepared: list[dict[str, Any]] = []
    for item in normalized:
        asset_id = int(item["asset_id"])
        _require_asset(connection, asset_id)
        row = current_row(connection, asset_id)
        current_revision = 0 if row is None else int(row["revision"])
        if current_revision != int(item["expected_revision"]):
            raise fail(
                PLACE_ASSIGNMENT_CONFLICT,
                "the current assignment revision is not the expected one",
                asset_ids=[asset_id],
                expected_revision=int(item["expected_revision"]),
                current_revision=current_revision,
            )
        target = item["target_place_id"]
        if action == "ASSIGN":
            _require_active_place(connection, int(target))
        before = assignment_view(row)
        previous_place = None if before is None or before["place_id"] is None else int(before["place_id"])
        effective_before = [int(r[0]) for r in connection.execute(
            "SELECT DISTINCT place_id FROM effective_asset_places WHERE asset_id=?", (asset_id,)
        )]
        # ADR-0005 §5: assign A->B excludes A, and an explicit assign of B also clears B's
        # own exclusion. Both may happen in one item, so the item carries a bounded list.
        wanted_exclusions: dict[int, bool] = {}
        if action == "ASSIGN":
            if previous_place is not None and previous_place != int(target):
                wanted_exclusions[previous_place] = True
            row_exclusion = _exclusion_row(connection, asset_id=asset_id, place_id=int(target))
            if row_exclusion is not None and int(row_exclusion["active"]) == 1:
                wanted_exclusions[int(target)] = False
        elif action == "UNASSIGN" and previous_place is not None:
            wanted_exclusions[previous_place] = True
        if action in ("ASSIGN", "UNASSIGN"):
            for previous in effective_before:
                if action == "UNASSIGN" or previous != int(target):
                    wanted_exclusions[previous] = True
        if action == "ASSIGN":
            # An explicit correction may clear an ancestor exclusion blocking this target.
            for ancestor in directory.ancestor_rows(connection, int(target)):
                exclusion = _exclusion_row(connection, asset_id=asset_id, place_id=int(ancestor["place_id"]))
                if exclusion is not None and exclusion["active"]:
                    wanted_exclusions[int(ancestor["place_id"])] = False
        deltas: list[dict[str, int]] = []
        for place_id, wanted in sorted(wanted_exclusions.items()):
            row_exclusion = _exclusion_row(connection, asset_id=asset_id, place_id=place_id)
            current_active = 0 if row_exclusion is None else int(row_exclusion["active"])
            if current_active != (1 if wanted else 0):
                deltas.append(
                    {"place_id": place_id, "before": current_active, "after": 1 if wanted else 0}
                )
        prepared.append(
            {
                "asset_id": asset_id,
                "action": action,
                "target_place_id": target,
                "expected_revision": current_revision,
                "previous_place": previous_place,
                "before": before,
                "after_state": _target_state(action, target, item.get("origin")),
                "exclusion_deltas": deltas,
                "cleared": [
                    delta["place_id"]
                    for delta in deltas
                    if delta["before"] == 1 and delta["after"] == 0
                ],
                "changed": _mode_key(before)
                != _mode_key(_target_state(action, target, item.get("origin"))),
            }
        )

    # ADR-0005 §11: a lock or an exclusion change can change which machine candidates are
    # still visible, so the independent suggestion revision is reconciled in the same
    # transaction. Assets whose effective state cannot move are not even inspected.
    affected = sorted(
        {
            int(item["asset_id"])
            for item in prepared
            if item["changed"] or item["exclusion_deltas"]
        }
    )
    suggestion_before = {
        asset_id: suggestion_module.visible_signature(connection, asset_id)
        for asset_id in affected
    }

    operation_id = operation_id_for(request_id)
    operation_revision = _next_operation_revision(connection)
    connection.execute(
        "INSERT INTO place_operations(operation_id, request_id, payload_sha256, operation_type, "
        "operation_revision, actor, atomic, status, undo_of_operation_id, "
        "catalog_filter_revision, created_at, committed_at) "
        "VALUES (?, ?, ?, ?, ?, 'LOCAL_USER', 1, 'COMMITTED', NULL, 0, ?, ?)",
        (
            operation_id,
            request_id,
            digest,
            operation_type,
            operation_revision,
            int(now_ms),
            int(now_ms),
        ),
    )

    rendered: list[dict[str, Any]] = []
    changed_assets: list[int] = []
    for index, item in enumerate(prepared):
        asset_id = int(item["asset_id"])
        changed = bool(item["changed"])
        if changed:
            after_view = _write_assignment(
                connection,
                asset_id=asset_id,
                state=item["after_state"],
                revision=int(item["expected_revision"]) + 1,
                operation_id=operation_id,
                now_ms=int(now_ms),
                created_at=int(now_ms),
            )
            changed_assets.append(asset_id)
        else:
            after_view = item["before"]
        for delta in item["exclusion_deltas"]:
            _set_exclusion(
                connection,
                asset_id=asset_id,
                place_id=int(delta["place_id"]),
                active=bool(delta["after"]),
                operation_id=operation_id,
                now_ms=int(now_ms),
            )
            if asset_id not in changed_assets:
                changed_assets.append(asset_id)
        connection.execute(
            "INSERT INTO place_operation_items(operation_id, item_index, asset_id, place_id, "
            "action, target_place_id, expected_revision, before_assignment_json, "
            "after_assignment_json, exclusion_deltas_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                operation_id,
                index,
                asset_id,
                item["previous_place"],
                str(item["action"]),
                item["target_place_id"],
                int(item["expected_revision"]),
                _snapshot(item["before"]),
                _snapshot(after_view),
                json.dumps(item["exclusion_deltas"], separators=(",", ":")),
            ),
        )
        rendered.append(
            {
                "asset_id": asset_id,
                "action": str(item["action"]),
                "assignment": after_view,
                "changed": changed or bool(item["exclusion_deltas"]),
                "cleared_exclusions": list(item["cleared"]),
            }
        )

    filter_revision = (
        revision_module.bump_filter_revision(
            connection,
            cause=revision_module.CAUSE_EFFECTIVE_ASSIGNMENT_CHANGED,
            now_ms=int(now_ms),
            asset_ids=sorted(set(changed_assets)),
            refresh=refresh,
        )
        if changed_assets
        else revision_module.read_filter_revision(connection)
    )
    if affected:
        suggestion_module.reconcile_suggestion_visibility(
            connection,
            before=suggestion_before,
            asset_ids=affected,
            now_ms=int(now_ms),
        )
    connection.execute(
        "UPDATE place_operations SET catalog_filter_revision = ? WHERE operation_id = ?",
        (int(filter_revision), operation_id),
    )
    return {
        "operation_id": operation_id,
        "operation_type": operation_type,
        "operation_revision": int(operation_revision),
        "status": "COMMITTED",
        "undo_of_operation_id": None,
        "changed": bool(changed_assets),
        "catalog_filter_revision": int(filter_revision),
        "replayed": False,
        "items": rendered,
    }


def undo_operation(
    connection: sqlite3.Connection,
    *,
    operation_id: str,
    request_id: str,
    expected_operation_revision: int,
    now_ms: int,
    refresh: revision_module.ProjectionRefresh | None = None,
) -> dict[str, Any]:
    """Compensate one committed operation, or refuse the whole request."""
    request_id = check_request_id(request_id)
    payload = {
        "undo_of_operation_id": str(operation_id),
        "expected_operation_revision": int(expected_operation_revision),
    }
    digest = payload_sha256(payload)
    existing = _existing_operation(connection, request_id)
    if existing is not None:
        if str(existing["payload_sha256"]) != digest:
            raise fail(
                PLACE_IDEMPOTENCY_CONFLICT,
                "this request_id is already bound to a different payload",
                operation_id=str(existing["operation_id"]),
            )
        return operation_result(connection, existing)

    original = connection.execute(
        f"SELECT {', '.join(OPERATION_FIELDS)} FROM place_operations WHERE operation_id = ?",
        (str(operation_id),),
    ).fetchone()
    if original is None:
        raise fail(
            PLACE_NOT_FOUND,
            f"operation {operation_id} does not exist",
            operation_id=str(operation_id),
        )
    if str(original["status"]) != "COMMITTED" or int(original["operation_revision"]) != int(
        expected_operation_revision
    ):
        raise fail(
            PLACE_OPERATION_NOT_UNDOABLE,
            "this operation is already reversed or its revision changed",
            operation_id=str(operation_id),
            expected_revision=int(expected_operation_revision),
            current_revision=int(original["operation_revision"]),
            status=str(original["status"]),
        )

    items = _operation_items(connection, str(operation_id))
    conflicts: list[int] = []
    for row in items:
        asset_id = row["asset_id"]
        if asset_id is None:
            continue
        after = _snapshot_load(row["after_assignment_json"])
        current = assignment_view(current_row(connection, int(asset_id)))
        if _revision_state_key(current) != _revision_state_key(after):
            conflicts.append(int(asset_id))
            continue
        for delta in _exclusion_deltas(row["exclusion_deltas_json"]):
            exclusion = _exclusion_row(
                connection, asset_id=int(asset_id), place_id=int(delta["place_id"])
            )
            current_active = 0 if exclusion is None else int(exclusion["active"])
            if current_active != int(delta["after"]):
                conflicts.append(int(asset_id))
                break
    if conflicts:
        raise fail(
            PLACE_OPERATION_NOT_UNDOABLE,
            "a target of this operation was edited afterwards",
            operation_id=str(operation_id),
            asset_ids=sorted(set(conflicts)),
        )

    new_operation_id = operation_id_for(request_id)
    operation_revision = _next_operation_revision(connection)
    # ADR-0005 §11: an undo that restores a lock or an exclusion can put a candidate back
    # (or take it away), so the visible suggestion set is reconciled in this transaction too.
    undo_affected = sorted(
        {int(row["asset_id"]) for row in items if row["asset_id"] is not None}
    )
    undo_suggestion_before = {
        asset_id: suggestion_module.visible_signature(connection, asset_id)
        for asset_id in undo_affected
    }
    connection.execute(
        "INSERT INTO place_operations(operation_id, request_id, payload_sha256, operation_type, "
        "operation_revision, actor, atomic, status, undo_of_operation_id, "
        "catalog_filter_revision, created_at, committed_at) "
        "VALUES (?, ?, ?, 'UNDO', ?, 'LOCAL_USER', 1, 'COMMITTED', ?, 0, ?, ?)",
        (
            new_operation_id,
            request_id,
            digest,
            operation_revision,
            str(operation_id),
            int(now_ms),
            int(now_ms),
        ),
    )
    rendered: list[dict[str, Any]] = []
    changed_assets: list[int] = []
    for index, row in enumerate(items):
        asset_id = row["asset_id"]
        before_snapshot = _snapshot_load(row["before_assignment_json"])
        after_snapshot = _snapshot_load(row["after_assignment_json"])
        restored: dict[str, Any] | None = after_snapshot
        item_changed = False
        if asset_id is not None:
            current = assignment_view(current_row(connection, int(asset_id)))
            revision = int(current["revision"]) if current else 0
            restore_state = _restore_state(before_snapshot)
            if _mode_key(current) != _mode_key(restore_state):
                restored = _write_assignment(
                    connection,
                    asset_id=int(asset_id),
                    state=restore_state,
                    revision=revision + 1,
                    operation_id=new_operation_id,
                    now_ms=int(now_ms),
                    created_at=int(now_ms),
                )
                changed_assets.append(int(asset_id))
                item_changed = True
            else:
                restored = current
        restore_deltas: list[dict[str, int]] = []
        if asset_id is not None:
            for delta in _exclusion_deltas(row["exclusion_deltas_json"]):
                place_id = int(delta["place_id"])
                restore_to = bool(int(delta["before"]))
                exclusion = _exclusion_row(
                    connection, asset_id=int(asset_id), place_id=place_id
                )
                current_active = 0 if exclusion is None else int(exclusion["active"])
                _set_exclusion(
                    connection,
                    asset_id=int(asset_id),
                    place_id=place_id,
                    active=restore_to,
                    operation_id=new_operation_id,
                    now_ms=int(now_ms),
                )
                restore_deltas.append(
                    {
                        "place_id": place_id,
                        "before": current_active,
                        "after": 1 if restore_to else 0,
                    }
                )
                if int(asset_id) not in changed_assets:
                    changed_assets.append(int(asset_id))
                item_changed = True
        connection.execute(
            "INSERT INTO place_operation_items(operation_id, item_index, asset_id, place_id, "
            "action, target_place_id, expected_revision, before_assignment_json, "
            "after_assignment_json, exclusion_deltas_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_operation_id,
                index,
                asset_id,
                row["place_id"],
                str(row["action"]),
                row["target_place_id"],
                int(row["expected_revision"]),
                _snapshot(after_snapshot),
                _snapshot(restored),
                json.dumps(restore_deltas, separators=(",", ":")),
            ),
        )
        rendered.append(
            {
                "asset_id": None if asset_id is None else int(asset_id),
                "action": str(row["action"]),
                "assignment": restored,
                "changed": item_changed,
                "cleared_exclusions": [],
            }
        )

    connection.execute(
        "UPDATE place_operations SET status = 'REVERSED' WHERE operation_id = ?",
        (str(operation_id),),
    )
    filter_revision = (
        revision_module.bump_filter_revision(
            connection,
            cause=revision_module.CAUSE_EFFECTIVE_ASSIGNMENT_CHANGED,
            now_ms=int(now_ms),
            asset_ids=sorted(set(changed_assets)),
            refresh=refresh,
        )
        if changed_assets
        else revision_module.read_filter_revision(connection)
    )
    if undo_affected:
        suggestion_module.reconcile_suggestion_visibility(
            connection,
            before=undo_suggestion_before,
            asset_ids=undo_affected,
            now_ms=int(now_ms),
        )
    connection.execute(
        "UPDATE place_operations SET catalog_filter_revision = ? WHERE operation_id = ?",
        (int(filter_revision), new_operation_id),
    )
    return {
        "operation_id": new_operation_id,
        "operation_type": "UNDO",
        "operation_revision": int(operation_revision),
        "status": "COMMITTED",
        "undo_of_operation_id": str(operation_id),
        "changed": bool(changed_assets),
        "catalog_filter_revision": int(filter_revision),
        "replayed": False,
        "items": rendered,
    }


def _restore_state(snapshot: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """The state an undo must write for one item.

    ``None`` means "this asset had no control row before the original operation". The row
    itself stays (revisions are monotonic and the history is append-only), so the
    equivalent ``AUTO_ALLOWED`` state is written instead: the filter semantics are
    identical and no revision is ever reused.
    """
    if snapshot is None:
        return _target_state("RESTORE_AUTO", None)
    return {
        "mode": str(snapshot["mode"]),
        "place_id": None if snapshot["place_id"] is None else int(snapshot["place_id"]),
        "origin": str(snapshot["origin"]),
        "manual_lock": bool(snapshot["manual_lock"]),
        "resolution_id": (
            None if snapshot["resolution_id"] is None else str(snapshot["resolution_id"])
        ),
    }


def list_operations(
    connection: sqlite3.Connection, *, limit: int, after_revision: int = 0
) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            f"SELECT {', '.join(OPERATION_FIELDS)} FROM place_operations "
            "WHERE operation_revision > ? ORDER BY operation_revision LIMIT ?",
            (int(after_revision), int(limit)),
        ).fetchall()
    )
