"""The one person business use case of ADR-0006: name, merge, redirect and compensate.

A rename, a same-name save and an explicit single/batch merge are *not* three
implementations. ``rename_person`` resolves the active same-name target and, when that
target is somebody else, calls exactly the same :func:`_apply_merge` that
``merge_persons`` calls. That is the property the task card asks for: the UI can never
produce a second, subtly different merge path.

Every write follows the ADR-0006 §8 order inside one ``BEGIN IMMEDIATE`` transaction:

1. the authoritative person/alias/link/redirect rows and the operation items;
2. the changed-before and changed-after face and ``DISTINCT asset_id`` sets;
3. exactly one ``catalog_filter_revision`` bump with cause ``PERSON_CHANGED`` and the P2-03
   projection refresh for the already confirmed image vectors;
4. the per-asset ``search_projection_queue`` upsert;
5. ``queue_candidate()`` for every affected face, so the independent face candidate index
   degrades to ``PENDING_REPAIR`` and catches up in the background;
6. the operation's final ``catalog_filter_revision``.

A semantic no-op (the same name saved again, or an explicit merge whose sources all resolve
to the target) still writes a ``COMMITTED`` operation so the request is provably answered -
but it moves no revision, writes no outbox row and touches no candidate entry.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from photo_search.catalog.errors import (
    FACE_MAPPING_IDEMPOTENCY_CONFLICT,
    FACE_MAPPING_OPERATION_NOT_UNDOABLE,
    FACE_OPERATION_NOT_UNDOABLE,
    CatalogError,
)
from photo_search.face.contract import FACE_PROCESSOR_ID, FACE_SPACE_ID, FACE_SPEC_REVISION
from photo_search.face.processor import generation_id_for, queue_candidate
from photo_search.persons import bundle as bundle_module
from photo_search.persons import contracts
from photo_search.persons.contracts import (
    FACE_IDEMPOTENCY_CONFLICT,
    PERSON_IDEMPOTENCY_CONFLICT,
    PERSON_NOT_FOUND,
    PERSON_OPERATION_NOT_UNDOABLE,
    PERSON_REVISION_CONFLICT,
    check_expected_revision,
    check_merge_sources,
    check_person_id,
    check_request_id,
    fail,
    normalize_person_name,
    operation_id_for,
    payload_sha256,
)
from photo_search.persons.identity import (
    active_person_id_for_name_key,
    require_person_row,
    resolve_canonical_person_id,
)
from photo_search.places import revision as revision_module

#: Implementation bound for one ``IN (...)`` lookup. SQLite's default variable limit is
#: well above this; chunking keeps a large person group's projection catch-up bounded.
_IN_CHUNK = 400

#: The person-level fields an undo restores on the ``persons`` row itself.
_PERSON_FIELDS = ("name", "name_key", "avatar_face_id", "merged_into")


def _chunks(values: Sequence[Any], size: int = _IN_CHUNK) -> list[list[Any]]:
    return [list(values[index : index + size]) for index in range(0, len(values), size)]


def _placeholders(count: int) -> str:
    return ", ".join("?" for _ in range(int(count)))


def _next_operation_revision(connection: sqlite3.Connection) -> int:
    return int(
        connection.execute(
            "SELECT COALESCE(MAX(operation_revision), 0) + 1 FROM person_operations"
        ).fetchone()[0]
    )


def _operation_columns() -> str:
    return (
        "operation_id, request_id, payload_sha256, type, source_person_ids_json, "
        "target_person_id, state, operation_revision, undo_of_operation_id, undone_by, "
        "catalog_filter_revision, created_at, committed_at"
    )


def operation_row(connection: sqlite3.Connection, operation_id: str) -> Any:
    return connection.execute(
        f"SELECT {_operation_columns()} FROM person_operations WHERE operation_id = ?",
        (str(operation_id),),
    ).fetchone()


def _operation_items(connection: sqlite3.Connection, operation_id: str) -> list[Any]:
    return list(
        connection.execute(
            "SELECT item_index, entity_type, entity_id, entity_key, before_value_json, "
            "after_value_json, after_revision FROM person_operation_items WHERE operation_id = ? "
            "ORDER BY item_index",
            (str(operation_id),),
        ).fetchall()
    )


def _item_projection(row: Any) -> dict[str, Any]:
    if str(row["entity_type"]) == contracts.ENTITY_TYPE_FACE_CONTROL:
        # ADR-0007 §2 keeps the face-level snapshot in the same log; its projection is
        # built by the face module so both the replay and the live answer share one shape.
        from photo_search.persons import face_correction

        return face_correction.recorded_item_projection(row)
    if str(row["entity_type"]) == contracts.ENTITY_TYPE_SPLIT_GROUP:
        # ADR-0008 §6: a group item answers in the split envelope, so a replay of a split is
        # rebuilt by the module that owns that envelope instead of being guessed here.
        from photo_search.persons import split as split_module

        before = split_module.load_group_bundle(row["before_value_json"])
        after = split_module.load_group_bundle(row["after_value_json"])
        return split_module.group_item_answer(str(after.get("role")), before, after)
    if str(row["entity_type"]) == contracts.ENTITY_TYPE_MAPPING_GROUP:
        # ADR-0010 §5.1: a mapping group answers in its own frozen envelope, built by the
        # module that owns that envelope instead of being guessed here. The stored snapshot is
        # a group state, never a name, a box, a vector or a path.
        from photo_search.persons import mapping as mapping_module

        return mapping_module.group_item_projection(row)
    before = bundle_module.load_bundle(row["before_value_json"])
    after = bundle_module.load_bundle(row["after_value_json"])
    return {
        "entity_type": str(row["entity_type"]),
        "entity_id": int(row["entity_id"]),
        "changed": bool(bundle_module.bundle_differences(after, before)),
        "merged_into": after.get("merged_into"),
        "revision": int(after.get("revision") or row["after_revision"]),
    }


def item_projection(row: Any) -> dict[str, Any]:
    """Public alias of the frozen per-item projection.

    The P3-06 read model answers ``changed`` for an already recorded operation through the
    very projection a replay uses, so a page can never disagree with the write path about
    what an operation did (and never has to parse a before/after snapshot itself).
    """
    return _item_projection(row)


def operation_result(connection: sqlite3.Connection, operation: Any) -> dict[str, Any]:
    """Rebuild the documented response of an already committed operation (a replay)."""
    if str(operation["type"]) == "FACE_REGROUP":
        from photo_search.persons import regroup

        return regroup.result(connection, operation)
    if str(operation["type"]) == "CREATE_PERSON_FROM_FACE":
        from photo_search.persons import create_from_face

        return create_from_face.result(connection, operation)
    from photo_search.persons import retention as retention_module

    retained_items = retention_module.replay_receipt(connection, str(operation["operation_id"]))
    items = (
        retained_items
        if retained_items is not None
        else [
            _item_projection(row)
            for row in _operation_items(connection, operation["operation_id"])
        ]
    )
    family = operation_family(connection, operation)
    if family == "mapping":
        # A mapping replay keeps the ADR-0010 envelope (its own ``contract_version`` and the
        # two item shapes) instead of being squeezed into the P3-03 person response.
        from photo_search.persons import mapping as mapping_module

        return mapping_module.mapping_operation_result(connection, operation)
    if family == "split":
        # The shared undo endpoint rebuilds idempotent responses here. A split-family replay
        # keeps its own frozen envelope (requested/canonical source, created person) instead
        # of the P3-03 person shape, and never re-reads live state to do it.
        from photo_search.persons import split as split_module

        return split_module.split_operation_result(operation, items)
    result = {
        "operation_id": str(operation["operation_id"]),
        "operation_type": str(operation["type"]),
        "operation_revision": int(operation["operation_revision"]),
        "status": "UNDONE" if str(operation["state"]) == "UNDONE" else "COMMITTED",
        "undo_of_operation_id": (
            None
            if operation["undo_of_operation_id"] is None
            else str(operation["undo_of_operation_id"])
        ),
        "changed": any(bool(item["changed"]) for item in items),
        "catalog_filter_revision": int(operation["catalog_filter_revision"]),
        "replayed": True,
        "items": items,
    }
    if family == "face":
        # The shared undo endpoint rebuilds idempotent responses here. A face-family replay
        # must retain the frozen face envelope instead of accidentally validating as the
        # looser P3-03 response union and losing its FACE_CONTROL fields.
        from photo_search.persons.face_correction import CONTRACT_VERSION

        result = {"contract_version": CONTRACT_VERSION, **result}
    return result


def _replay(
    connection: sqlite3.Connection,
    *,
    request_id: str,
    digest: str,
    conflict_code: str = PERSON_IDEMPOTENCY_CONFLICT,
) -> dict[str, Any] | None:
    """The idempotency gate. It runs first, before any person or revision is inspected."""
    row = connection.execute(
        f"SELECT {_operation_columns()} FROM person_operations WHERE request_id = ?",
        (str(request_id),),
    ).fetchone()
    if row is None:
        return None
    if str(row["payload_sha256"]) != str(digest):
        if conflict_code == FACE_MAPPING_IDEMPOTENCY_CONFLICT:
            # ADR-0010 §8.2 keeps the mapping details closed to ``asset_id``/``old_face_id``,
            # so the asset is resolved from the *recorded* operation instead of echoing the
            # operation id the person family reports.
            from photo_search.persons import mapping as mapping_module

            asset_id = mapping_module.operation_asset_id(connection, str(row["operation_id"]))
            raise fail(
                conflict_code,
                "this request_id is already bound to a different payload",
                asset_id=int(asset_id),
            )
        raise fail(
            conflict_code,
            "this request_id is already bound to a different payload",
            operation_id=str(row["operation_id"]),
        )
    return operation_result(connection, row)


def _require_revision(
    connection: sqlite3.Connection, *, canonical_person_id: int, person_id: int, expected: int
) -> None:
    row = require_person_row(connection, int(canonical_person_id))
    current = int(row["revision"])
    if current != int(expected):
        raise fail(
            PERSON_REVISION_CONFLICT,
            "the current person revision is not the expected one",
            person_id=int(person_id),
            canonical_person_id=int(canonical_person_id),
            expected_revision=int(expected),
            current_revision=current,
        )


# --------------------------------------------------------------------------- merge core
def _source_alias_texts(
    connection: sqlite3.Connection, *, source: int, formal_name: str | None
) -> list[tuple[str, str]]:
    """The alias texts a source contributes: its aliases plus its formal display name."""
    pairs: list[tuple[str, str]] = []
    for row in connection.execute(
        "SELECT alias, normalized_alias FROM person_aliases WHERE person_id = ? "
        "ORDER BY normalized_alias, alias",
        (int(source),),
    ).fetchall():
        pairs.append((str(row["alias"]), str(row["normalized_alias"])))
    if formal_name is not None:
        pairs.append((str(formal_name), str(formal_name)))
    return pairs


def _add_alias(
    connection: sqlite3.Connection,
    *,
    person_id: int,
    alias: str,
    normalized_alias: str,
    operation_id: str,
    now_ms: int,
    forbidden: str | None,
) -> bool:
    """Add one alias inside the target, deduplicated by its normalised form."""
    if str(normalized_alias) == str(forbidden):
        # ADR-0006 §6: the target's current formal name is never also its own alias.
        return False
    cursor = connection.execute(
        "INSERT INTO person_aliases(person_id, alias, normalized_alias, last_operation_id, "
        "created_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(person_id, normalized_alias) DO NOTHING",
        (int(person_id), str(alias), str(normalized_alias), str(operation_id), int(now_ms)),
    )
    return bool(cursor.rowcount)


def _manual_unassigned_faces(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row["face_id"])
        for row in connection.execute(
            "SELECT face_id FROM face_assignment_controls WHERE mode = 'MANUAL_UNASSIGNED'"
        ).fetchall()
    }


def _active_exclusion_pairs(
    connection: sqlite3.Connection, face_ids: Sequence[str]
) -> set[tuple[str, int]]:
    """Active ``(face, canonical person)`` exclusions of the faces a merge touches."""
    pairs: set[tuple[str, int]] = set()
    for chunk in _chunks(sorted({str(value) for value in face_ids})):
        rows = connection.execute(
            "SELECT face_id, person_id FROM face_exclusions WHERE active = 1 "
            f"AND face_id IN ({_placeholders(len(chunk))})",
            tuple(chunk),
        ).fetchall()
        for row in rows:
            # The excluded person may itself have been merged, so it is canonicalised.
            canonical = resolve_canonical_person_id(connection, int(row["person_id"]))
            pairs.add((str(row["face_id"]), int(canonical)))
    return pairs


def _link_rank(link: Mapping[str, Any]) -> tuple[int, int]:
    """``CONFIRMED`` beats ``SUGGESTED``; for the same decision the newer row wins."""
    return (1 if str(link["decision"]) == "CONFIRMED" else 0, int(link["revision"]))


def _rewrite_links(
    connection: sqlite3.Connection,
    *,
    target: int,
    sources: Sequence[int],
    operation_id: str,
    now_ms: int,
) -> None:
    """Collapse every affected link onto the target in one write-back.

    The rows are collected in memory first, so the partial unique index on the effective
    link never sees a half-moved set: one row per ``(face_id, assignment_kind)`` survives
    (``CONFIRMED`` over ``SUGGESTED``, then the newer revision), and an effective link that
    the human control tables forbid is downgraded to a suggestion with a frozen reason
    instead of being applied.
    """
    affected = [int(target), *(int(value) for value in sources)]
    rows = connection.execute(
        "SELECT face_id, person_id, assignment_kind, decision, confidence, reason, revision, "
        "last_operation_id FROM face_person_links "
        f"WHERE person_id IN ({_placeholders(len(affected))})",
        tuple(affected),
    ).fetchall()
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["face_id"]), str(row["assignment_kind"]))
        candidate = {
            "face_id": str(row["face_id"]),
            "origin_person_id": int(row["person_id"]),
            "assignment_kind": str(row["assignment_kind"]),
            "decision": str(row["decision"]),
            "confidence": float(row["confidence"]),
            "reason": None if row["reason"] is None else str(row["reason"]),
            "revision": int(row["revision"]),
            "last_operation_id": (
                None if row["last_operation_id"] is None else str(row["last_operation_id"])
            ),
        }
        existing = grouped.get(key)
        if existing is None or _link_rank(candidate) > _link_rank(existing):
            grouped[key] = candidate

    locked = _manual_unassigned_faces(connection)
    excluded = _active_exclusion_pairs(connection, [key[0] for key in grouped])
    for link in grouped.values():
        if str(link["decision"]) != "CONFIRMED":
            continue
        if str(link["face_id"]) in locked:
            link["decision"] = "SUGGESTED"
            link["reason"] = contracts.REASON_MANUAL_UNASSIGNED
            link["revision"] = int(link["revision"]) + 1
        elif (str(link["face_id"]), int(target)) in excluded:
            link["decision"] = "SUGGESTED"
            link["reason"] = contracts.REASON_HUMAN_CONFLICT
            link["revision"] = int(link["revision"]) + 1

    connection.execute(
        f"DELETE FROM face_person_links WHERE person_id IN ({_placeholders(len(affected))})",
        tuple(affected),
    )
    for key in sorted(grouped):
        link = grouped[key]
        moved = int(link["origin_person_id"]) != int(target)
        downgraded = str(link["decision"]) != "CONFIRMED"
        last_operation_id = (
            str(operation_id) if (moved or downgraded) else link["last_operation_id"]
        )
        connection.execute(
            "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
            "confidence, reason, revision, last_operation_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(link["face_id"]),
                int(target),
                str(link["assignment_kind"]),
                str(link["decision"]),
                float(link["confidence"]),
                link["reason"],
                int(link["revision"]),
                last_operation_id,
                int(now_ms),
                int(now_ms),
            ),
        )


def _reassign_avatar(connection: sqlite3.Connection, *, target: int, now_ms: int) -> None:
    """Pick a representative only when the target has no avatar at all.

    The candidate is an effective, quality-``OK`` member inside the *current* face
    processor's space, ranked ``face_pixels DESC, face_id ASC``. No vector is recomputed and
    no face from another space can ever be chosen.
    """
    row = connection.execute(
        "SELECT avatar_face_id FROM persons WHERE person_id = ?", (int(target),)
    ).fetchone()
    if row is None or row["avatar_face_id"] is not None:
        return
    best = connection.execute(
        "SELECT f.face_id AS face_id FROM face_person_links AS l "
        "JOIN faces AS f ON f.face_id = l.face_id "
        "JOIN face_embeddings AS e ON e.face_id = f.face_id AND e.processor_id = ? "
        "WHERE l.person_id = ? AND l.decision = 'CONFIRMED' AND f.quality_state = 'OK' "
        "AND f.spec_revision = ? AND e.space_id = ? "
        "ORDER BY f.face_pixels DESC, f.face_id ASC LIMIT 1",
        (str(FACE_PROCESSOR_ID), int(target), str(FACE_SPEC_REVISION), str(FACE_SPACE_ID)),
    ).fetchone()
    if best is None:
        return
    connection.execute(
        "UPDATE persons SET avatar_face_id = ?, updated_at = ? WHERE person_id = ?",
        (str(best["face_id"]), int(now_ms), int(target)),
    )


def _apply_rename(
    connection: sqlite3.Connection,
    *,
    person_id: int,
    display_name: str,
    name_key: str,
    now_ms: int,
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    before = {int(person_id): bundle_module.read_bundle(connection, int(person_id))}
    connection.execute(
        "UPDATE persons SET name = ?, name_key = ?, revision = revision + 1, updated_at = ? "
        "WHERE person_id = ?",
        (str(display_name), str(name_key), int(now_ms), int(person_id)),
    )
    after = {int(person_id): bundle_module.read_bundle(connection, int(person_id))}
    return before, after


def _apply_merge(
    connection: sqlite3.Connection,
    *,
    target: int,
    sources: Sequence[int],
    operation_id: str,
    now_ms: int,
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    affected = [int(target), *(int(value) for value in sources)]
    before = {person: bundle_module.read_bundle(connection, person) for person in affected}
    target_name = before[int(target)]["name"]
    for source in sources:
        for alias, normalized in _source_alias_texts(
            connection, source=int(source), formal_name=before[int(source)]["name"]
        ):
            _add_alias(
                connection,
                person_id=int(target),
                alias=alias,
                normalized_alias=normalized,
                operation_id=str(operation_id),
                now_ms=int(now_ms),
                forbidden=target_name,
            )
        connection.execute("DELETE FROM person_aliases WHERE person_id = ?", (int(source),))
    for source in sources:
        # ADR-0006 §6: a source keeps its id and its name, only the redirect is written.
        connection.execute(
            "UPDATE persons SET merged_into = ?, revision = revision + 1, updated_at = ? "
            "WHERE person_id = ?",
            (int(target), int(now_ms), int(source)),
        )
    connection.execute(
        "UPDATE persons SET revision = revision + 1, updated_at = ? WHERE person_id = ?",
        (int(now_ms), int(target)),
    )
    _rewrite_links(
        connection,
        target=int(target),
        sources=[int(value) for value in sources],
        operation_id=str(operation_id),
        now_ms=int(now_ms),
    )
    _reassign_avatar(connection, target=int(target), now_ms=int(now_ms))
    after = {person: bundle_module.read_bundle(connection, person) for person in affected}
    return before, after


# ------------------------------------------------------------------ derived catch-up
def _affected_faces(
    before: Mapping[int, Mapping[str, Any]], after: Mapping[int, Mapping[str, Any]]
) -> list[str]:
    faces: set[str] = set()
    for bucket in (before, after):
        for bundle in bucket.values():
            for link in bundle.get("links", []):
                faces.add(str(link["face_id"]))
    return sorted(faces)


def _assets_of_faces(connection: sqlite3.Connection, face_ids: Sequence[str]) -> list[int]:
    assets: set[int] = set()
    for chunk in _chunks(sorted({str(value) for value in face_ids})):
        rows = connection.execute(
            "SELECT DISTINCT asset_id FROM faces "
            f"WHERE face_id IN ({_placeholders(len(chunk))})",
            tuple(chunk),
        ).fetchall()
        assets.update(int(row["asset_id"]) for row in rows)
    return sorted(assets)


def _queue_assets(
    connection: sqlite3.Connection, asset_ids: Sequence[int], *, now_ms: int
) -> None:
    for asset_id in sorted({int(value) for value in asset_ids}):
        connection.execute(
            "INSERT INTO search_projection_queue(asset_id, cause, queued_at) "
            "VALUES (?, ?, ?) ON CONFLICT(asset_id) DO UPDATE SET cause = excluded.cause, "
            "queued_at = excluded.queued_at",
            (int(asset_id), contracts.CAUSE_PERSON_CHANGED, int(now_ms)),
        )


def _repair_candidates(
    connection: sqlite3.Connection, face_ids: Sequence[str], *, now_ms: int
) -> None:
    """Degrade the affected faces to ``PENDING_REPAIR`` and republish in the background."""
    for face_id in sorted({str(value) for value in face_ids}):
        face = connection.execute(
            "SELECT artifact_id, spec_revision FROM faces WHERE face_id = ?", (str(face_id),)
        ).fetchone()
        if face is None:
            continue
        generation_id = generation_id_for(
            str(FACE_SPACE_ID), str(FACE_PROCESSOR_ID), spec_revision=str(face["spec_revision"])
        )
        entry = connection.execute(
            "SELECT 1 FROM face_index_entries WHERE generation_id = ? AND face_id = ?",
            (str(generation_id), str(face_id)),
        ).fetchone()
        if entry is None:
            continue
        queue_candidate(
            connection,
            face_id=str(face_id),
            generation_id=str(generation_id),
            artifact_id=str(face["artifact_id"]),
            now_ms=int(now_ms),
        )


def publish_derived(
    connection: sqlite3.Connection,
    *,
    faces: Sequence[str],
    now_ms: int,
    refresh: revision_module.ProjectionRefresh | None,
) -> int:
    """ADR-0006 §8 / ADR-0007 §8 items 2-5: the one authority→derived catch-up.

    Exactly one ``catalog_filter_revision`` bump with the shared cause, one
    ``search_projection_queue`` row per affected asset and a ``queue_candidate()`` call for
    every affected face that is already published in the current face generation. It is a
    separate function because the face-level correction of ADR-0007 must run the same
    sequence, in the same order, inside its own authority transaction - and must not be able
    to invent a second, subtly different one.
    """
    assets = _assets_of_faces(connection, list(faces))
    filter_revision = revision_module.bump_filter_revision(
        connection,
        cause=contracts.CAUSE_PERSON_CHANGED,
        now_ms=int(now_ms),
        asset_ids=assets,
        refresh=refresh,
    )
    _queue_assets(connection, assets, now_ms=int(now_ms))
    _repair_candidates(connection, list(faces), now_ms=int(now_ms))
    return int(filter_revision)


def _open_operation(
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
    """Write the operation row first.

    ADR-0006 §8 item 1 makes the operation and its items part of the *authority* write, and
    the rows the change touches (an alias, for one) reference the operation by id, so the
    operation row exists before anything else in this transaction moves.
    """
    operation_revision = _next_operation_revision(connection)
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
    return int(operation_revision)


def _close_operation(
    connection: sqlite3.Connection,
    *,
    operation_id: str,
    operation_revision: int,
    operation_type: str,
    undo_of_operation_id: str | None,
    before: dict[int, dict[str, Any]],
    after: dict[int, dict[str, Any]],
    now_ms: int,
    refresh: revision_module.ProjectionRefresh | None,
) -> dict[str, Any]:
    """Write the items, queue the derived catch-up and return the operation response."""
    affected = sorted(int(value) for value in before)
    items: list[dict[str, Any]] = []
    changed = False
    for index, person_id in enumerate(affected):
        before_bundle = before[person_id]
        after_bundle = after[person_id]
        item_changed = bool(bundle_module.bundle_differences(after_bundle, before_bundle))
        changed = changed or item_changed
        connection.execute(
            "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
            "entity_id, before_value_json, after_value_json, after_revision) "
            "VALUES (?, ?, 'PERSON_BUNDLE', ?, ?, ?, ?)",
            (
                str(operation_id),
                int(index),
                int(person_id),
                bundle_module.bundle_json(before_bundle),
                bundle_module.bundle_json(after_bundle),
                int(after_bundle["revision"]),
            ),
        )
        items.append(
            {
                "entity_type": "PERSON_BUNDLE",
                "entity_id": int(person_id),
                "changed": bool(item_changed),
                "merged_into": after_bundle["merged_into"],
                "revision": int(after_bundle["revision"]),
            }
        )
    if changed:
        # ADR-0006 §8 items 2-5, in this order, inside the same transaction.
        filter_revision = publish_derived(
            connection,
            faces=_affected_faces(before, after),
            now_ms=int(now_ms),
            refresh=refresh,
        )
    else:
        filter_revision = revision_module.read_filter_revision(connection)
    connection.execute(
        "UPDATE person_operations SET catalog_filter_revision = ? WHERE operation_id = ?",
        (int(filter_revision), str(operation_id)),
    )
    return {
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


# ------------------------------------------------------------------------- public API
def rename_person(
    connection: sqlite3.Connection,
    *,
    person_id: int,
    name: Any,
    request_id: str,
    expected_revision: int,
    now_ms: int,
    refresh: revision_module.ProjectionRefresh | None = None,
    merge_by_name: bool = True,
) -> dict[str, Any]:
    """Save one display name; a same-name save merges through the same use case."""
    request_id = check_request_id(request_id)
    requested = check_person_id(person_id)
    expected = check_expected_revision(expected_revision)
    display_name, name_key = normalize_person_name(name)
    intent = {
        "type": "RENAME",
        "person_id": int(requested),
        "name": display_name,
        "expected_revision": int(expected),
    }
    if not merge_by_name:
        intent["merge_by_name"] = False
    digest = payload_sha256(intent)
    replay = _replay(connection, request_id=request_id, digest=digest)
    if replay is not None:
        return replay
    canonical = resolve_canonical_person_id(connection, requested)
    _require_revision(
        connection, canonical_person_id=canonical, person_id=requested, expected=expected
    )
    operation_id = operation_id_for(request_id)
    target = active_person_id_for_name_key(connection, name_key, exclude_person_id=canonical)
    merging = target is not None
    if merging and not merge_by_name:
        raise CatalogError(
            PERSON_REVISION_CONFLICT,
            "this name belongs to another person; choose an explicit merge",
            details={"reason": "NAME_IN_USE", "target_person_id": int(target)},
        )
    operation_type = "MERGE_BY_NAME" if merging else "RENAME"
    target_person_id = int(target) if merging else int(canonical)
    operation_revision = _open_operation(
        connection,
        operation_id=operation_id,
        request_id=request_id,
        digest=digest,
        operation_type=operation_type,
        # The recorded ids are the ones the caller named, never a canonical id.
        source_person_ids=[int(requested)] if merging else [],
        target_person_id=target_person_id,
        undo_of_operation_id=None,
        now_ms=int(now_ms),
    )
    if merging:
        before, after = _apply_merge(
            connection,
            target=target_person_id,
            sources=[int(canonical)],
            operation_id=operation_id,
            now_ms=int(now_ms),
        )
    elif require_person_row(connection, canonical)["name_key"] == name_key:
        # The same display value saved again: an answered request, not a new state.
        unchanged = {int(canonical): bundle_module.read_bundle(connection, int(canonical))}
        before, after = dict(unchanged), dict(unchanged)
    else:
        before, after = _apply_rename(
            connection,
            person_id=canonical,
            display_name=display_name,
            name_key=name_key,
            now_ms=int(now_ms),
        )
    return _close_operation(
        connection,
        operation_id=operation_id,
        operation_revision=operation_revision,
        operation_type=operation_type,
        undo_of_operation_id=None,
        before=before,
        after=after,
        now_ms=int(now_ms),
        refresh=refresh,
    )


def merge_persons(
    connection: sqlite3.Connection,
    *,
    target_person_id: int,
    target_expected_revision: int,
    sources: Sequence[Mapping[str, Any]],
    request_id: str,
    now_ms: int,
    refresh: revision_module.ProjectionRefresh | None = None,
) -> dict[str, Any]:
    """Explicit single or batch merge into one target (one atomic operation)."""
    request_id = check_request_id(request_id)
    requested_target = check_person_id(target_person_id, field="target_person_id")
    target_expected = check_expected_revision(
        target_expected_revision, field="target_expected_revision"
    )
    normalized = check_merge_sources(sources)
    digest = payload_sha256(
        {
            "type": "MERGE",
            "target_person_id": int(requested_target),
            "target_expected_revision": int(target_expected),
            "sources": normalized,
        }
    )
    replay = _replay(connection, request_id=request_id, digest=digest)
    if replay is not None:
        return replay
    target = resolve_canonical_person_id(connection, requested_target)
    _require_revision(
        connection,
        canonical_person_id=target,
        person_id=requested_target,
        expected=target_expected,
    )
    canonical_sources: list[int] = []
    for item in normalized:
        canonical = resolve_canonical_person_id(connection, item["person_id"])
        _require_revision(
            connection,
            canonical_person_id=canonical,
            person_id=item["person_id"],
            expected=item["expected_revision"],
        )
        if int(canonical) == int(target) or int(canonical) in canonical_sources:
            continue
        canonical_sources.append(int(canonical))
    canonical_sources.sort()
    requested_sources = [int(item["person_id"]) for item in normalized]
    operation_id = operation_id_for(request_id)
    # The type describes the *request shape*: one named source is a merge, several are a batch.
    # It therefore stays stable even when every named source already resolves to the target.
    operation_type = "MERGE" if len(normalized) == 1 else "BATCH_MERGE"
    operation_revision = _open_operation(
        connection,
        operation_id=operation_id,
        request_id=request_id,
        digest=digest,
        operation_type=operation_type,
        source_person_ids=requested_sources,
        target_person_id=int(target),
        undo_of_operation_id=None,
        now_ms=int(now_ms),
    )
    if canonical_sources:
        before, after = _apply_merge(
            connection,
            target=int(target),
            sources=canonical_sources,
            operation_id=operation_id,
            now_ms=int(now_ms),
        )
    else:
        # Every named source already resolves to the target: an answered no-op.
        unchanged = {int(target): bundle_module.read_bundle(connection, int(target))}
        before, after = dict(unchanged), dict(unchanged)
    return _close_operation(
        connection,
        operation_id=operation_id,
        operation_revision=operation_revision,
        operation_type=operation_type,
        undo_of_operation_id=None,
        before=before,
        after=after,
        now_ms=int(now_ms),
        refresh=refresh,
    )


# ------------------------------------------------------------------------- compensation
def operation_family(connection: sqlite3.Connection, operation: Any) -> str:
    """Return the operation-family vocabulary, including permanent create-only history.

    An ``UNDO`` row is family-agnostic, so it inherits the family of the operation it
    compensates. That keeps "undo an UNDO" answered with the frozen code of the family the
    caller was working in, instead of always falling back to the P3-03 vocabulary.
    """
    operation_type = str(operation["type"])
    if operation_type in (contracts.FACE_OPERATION_TYPES + contracts.REGROUP_OPERATION_TYPES):
        return "face"
    if operation_type in contracts.SPLIT_OPERATION_TYPES:
        return "split"
    if operation_type in contracts.MAPPING_OPERATION_TYPES:
        return "mapping"
    if operation_type in contracts.CREATE_PERSON_OPERATION_TYPES:
        return "create-person"
    if operation_type == "UNDO" and operation["undo_of_operation_id"] is not None:
        parent = operation_row(connection, str(operation["undo_of_operation_id"]))
        if parent is not None:
            parent_type = str(parent["type"])
            if parent_type in (contracts.FACE_OPERATION_TYPES + contracts.REGROUP_OPERATION_TYPES):
                return "face"
            if parent_type in contracts.SPLIT_OPERATION_TYPES:
                return "split"
            if parent_type in contracts.MAPPING_OPERATION_TYPES:
                return "mapping"
    return "person"


def _restore_aliases(
    connection: sqlite3.Connection,
    *,
    entity: int,
    live: Mapping[str, Any],
    wanted: Mapping[str, Any],
    operation_id: str,
    now_ms: int,
) -> bool:
    """Restore one person's alias set exactly; returns whether anything was written."""
    live_aliases = sorted(
        (str(alias["normalized_alias"]), str(alias["alias"]))
        for alias in live.get("aliases", [])
    )
    wanted_aliases = sorted(
        (str(alias["normalized_alias"]), str(alias["alias"]))
        for alias in wanted.get("aliases", [])
    )
    if live_aliases == wanted_aliases:
        return False
    connection.execute("DELETE FROM person_aliases WHERE person_id = ?", (int(entity),))
    for normalized, alias in wanted_aliases:
        connection.execute(
            "INSERT INTO person_aliases(person_id, alias, normalized_alias, "
            "last_operation_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (int(entity), str(alias), str(normalized), str(operation_id), int(now_ms)),
        )
    return True


def _restore_link_rows(
    connection: sqlite3.Connection,
    *,
    entity: int,
    live: Mapping[str, Any],
    wanted: Mapping[str, Any],
    operation_id: str,
    now_ms: int,
    forced_keys: set[tuple[str, str]] | None = None,
    revision_hints: Mapping[str, int] | None = None,
) -> bool:
    """Restore one person's link set; every restored revision moves forward, never back.

    ``forced_keys`` marks the ``(face_id, assignment_kind)`` rows that an earlier, *shared*
    delete already removed inside this compensation. Without it the "the live row already
    looks like the wanted one" shortcut would silently drop a row that is no longer there
    (ADR-0010 §10.1: the restoration is order-independent because every effective row of the
    affected faces is deleted once, before any of them is written back).
    """
    live_links = {
        (str(link["face_id"]), str(link["assignment_kind"])): link
        for link in live.get("links", [])
    }
    wanted_links = {
        (str(link["face_id"]), str(link["assignment_kind"])): link
        for link in wanted.get("links", [])
    }
    forced = forced_keys or set()
    hints = revision_hints or {}
    changed = False
    for key in sorted(set(live_links) - set(wanted_links)):
        connection.execute(
            "DELETE FROM face_person_links WHERE face_id = ? AND person_id = ? "
            "AND assignment_kind = ?",
            (str(key[0]), int(entity), str(key[1])),
        )
        changed = True
    for key in sorted(wanted_links):
        target_link = wanted_links[key]
        current = live_links.get(key)
        if key not in forced and current is not None and all(
            current.get(field) == target_link.get(field)
            for field in ("decision", "confidence", "reason", "last_operation_id")
        ):
            continue
        floor = max(
            int(target_link["revision"]),
            0 if current is None else int(current["revision"]),
            int(hints.get(str(key[0]), 0)),
        )
        connection.execute(
            "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
            "confidence, reason, revision, last_operation_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(face_id, person_id, assignment_kind) DO UPDATE SET "
            "decision = excluded.decision, confidence = excluded.confidence, "
            "reason = excluded.reason, revision = excluded.revision, "
            "last_operation_id = excluded.last_operation_id, updated_at = excluded.updated_at",
            (
                str(key[0]),
                int(entity),
                str(key[1]),
                str(target_link["decision"]),
                float(target_link["confidence"]),
                target_link["reason"],
                int(floor) + 1,
                str(operation_id),
                int(now_ms),
                int(now_ms),
            ),
        )
        changed = True
    return changed


def _faces_needing_link_restore(
    pending: Sequence[tuple[int, dict[str, Any], dict[str, Any]]],
) -> list[str]:
    """Only the faces whose recorded link set really differs from the live one.

    The shared clear below is what makes the restoration order-independent (ADR-0010 §10.1),
    but it must stay *narrow*: a semantic no-op operation has to be compensated by moving
    nothing at all, exactly as it was before this task. Faces whose live and recorded bundles
    already agree are therefore left completely alone.
    """
    faces: set[str] = set()
    for _entity, live, recorded_before in pending:
        live_links = {
            (str(link["face_id"]), str(link["assignment_kind"])): (
                str(link["decision"]),
                float(link["confidence"]),
                link["reason"],
                link["last_operation_id"],
            )
            for link in live.get("links", [])
        }
        wanted_links = {
            (str(link["face_id"]), str(link["assignment_kind"])): (
                str(link["decision"]),
                float(link["confidence"]),
                link["reason"],
                link["last_operation_id"],
            )
            for link in recorded_before.get("links", [])
        }
        for key in set(live_links) | set(wanted_links):
            if live_links.get(key) != wanted_links.get(key):
                faces.add(str(key[0]))
    return sorted(faces)


def _clear_effective_links(
    connection: sqlite3.Connection, face_ids: Sequence[str]
) -> dict[str, int]:
    """Remove every ``CONFIRMED`` row of the affected faces once; return the revision floor.

    The returned per-face floor is the highest revision the effective rows had *before* the
    delete, so the rows written back immediately afterwards can only ever move forward
    (ADR-0010 §10.3).
    """
    floor: dict[str, int] = {}
    unique = sorted({str(value) for value in face_ids})
    for chunk in _chunks(unique):
        placeholders = _placeholders(len(chunk))
        rows = connection.execute(
            f"SELECT face_id, MAX(revision) AS revision FROM face_person_links "
            f"WHERE decision = 'CONFIRMED' AND face_id IN ({placeholders}) GROUP BY face_id",
            tuple(chunk),
        ).fetchall()
        for row in rows:
            floor[str(row["face_id"])] = int(row["revision"])
        connection.execute(
            f"DELETE FROM face_person_links WHERE decision = 'CONFIRMED' "
            f"AND face_id IN ({placeholders})",
            tuple(chunk),
        )
    return floor


def undo_person_operation(
    connection: sqlite3.Connection,
    *,
    operation_id: str,
    request_id: str,
    expected_operation_revision: int,
    now_ms: int,
    refresh: revision_module.ProjectionRefresh | None = None,
) -> dict[str, Any]:
    """Compensate one committed operation, or refuse the whole request with zero writes."""
    request_id = check_request_id(request_id)
    original_id = str(operation_id)
    expected = check_expected_revision(
        expected_operation_revision, field="expected_operation_revision"
    )
    digest = payload_sha256(
        {
            "undo_of_operation_id": original_id,
            "expected_operation_revision": int(expected),
        }
    )
    # ADR-0006 §7 and ADR-0007 §7 both put the idempotency gate ahead of *every* revision
    # check, including the one on the operation being compensated. Reading the operation row
    # is not a revision check - it only decides which frozen vocabulary a mismatch uses, so
    # a key already owned by the face family answers with FACE_IDEMPOTENCY_CONFLICT.
    original = operation_row(connection, original_id)
    family = "person" if original is None else operation_family(connection, original)
    is_face = family == "face"
    if family == "mapping":
        conflict_code = FACE_MAPPING_IDEMPOTENCY_CONFLICT
    elif is_face:
        conflict_code = FACE_IDEMPOTENCY_CONFLICT
    else:
        conflict_code = PERSON_IDEMPOTENCY_CONFLICT
    replay = _replay(
        connection, request_id=request_id, digest=digest, conflict_code=conflict_code
    )
    if replay is not None:
        return replay
    if original is not None and str(original["type"]) in contracts.CREATE_PERSON_OPERATION_TYPES:
        raise fail(
            PERSON_OPERATION_NOT_UNDOABLE,
            "this operation is permanently non-undoable",
            operation_id=original_id,
            reason="operation_type_not_undoable",
        )
    from photo_search.persons import retention as retention_module

    if retention_module.is_expired(connection, original_id):
        original_type = str(original["type"]) if original is not None else ""
        if original_type in (contracts.FACE_OPERATION_TYPES + contracts.REGROUP_OPERATION_TYPES):
            expired_code = FACE_OPERATION_NOT_UNDOABLE
        elif original_type in contracts.MAPPING_OPERATION_TYPES:
            expired_code = FACE_MAPPING_OPERATION_NOT_UNDOABLE
        else:
            expired_code = PERSON_OPERATION_NOT_UNDOABLE
        raise fail(
            expired_code,
            "this operation has expired and can no longer be undone",
            operation_id=original_id,
            reason="operation_expired",
        )
    if original is None:
        raise fail(
            PERSON_NOT_FOUND,
            f"operation {original_id} does not exist",
            operation_id=original_id,
        )
    if is_face:
        # The face family owns its own eligibility and snapshot checks, so a refused
        # compensation of a face operation answers in the ADR-0007 vocabulary.
        from photo_search.persons import face_correction

        return face_correction.undo_face_operation(
            connection,
            original=original,
            request_id=request_id,
            digest=digest,
            expected_operation_revision=int(expected),
            now_ms=int(now_ms),
            refresh=refresh,
        )
    if family == "mapping":
        # ADR-0010 §5.4 keeps the *same* compensation endpoint. The mapping family owns its
        # own eligibility, snapshot comparison and hold restoration, and answers in the
        # ADR-0010 envelope (``FACE_MAPPING_OPERATION_NOT_UNDOABLE`` on a conflict).
        from photo_search.persons import mapping as mapping_module

        return mapping_module.undo_face_mapping_operation(
            connection,
            original=original,
            request_id=request_id,
            digest=digest,
            expected_operation_revision=int(expected),
            now_ms=int(now_ms),
            refresh=refresh,
        )
    if family == "split":
        # ADR-0008 §7 keeps the *same* endpoint: the split family owns its own eligibility,
        # snapshot comparison and compensation, and answers in the split envelope.
        from photo_search.persons import split as split_module

        return split_module.undo_split_operation(
            connection,
            original=original,
            request_id=request_id,
            digest=digest,
            expected_operation_revision=int(expected),
            now_ms=int(now_ms),
            refresh=refresh,
        )
    if (
        str(original["type"]) not in contracts.WRITE_OPERATION_TYPES
        or str(original["state"]) != "COMMITTED"
        or original["undone_by"] is not None
        or int(original["operation_revision"]) != int(expected)
    ):
        raise fail(
            PERSON_OPERATION_NOT_UNDOABLE,
            "this operation is already reversed, is an undo itself, or its revision changed",
            operation_id=original_id,
            expected_revision=int(expected),
            current_revision=int(original["operation_revision"]),
            status=str(original["state"]),
        )
    pending: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    conflicts: list[dict[str, Any]] = []
    for row in _operation_items(connection, original_id):
        entity = int(row["entity_id"])
        recorded_after = bundle_module.load_bundle(row["after_value_json"])
        recorded_before = bundle_module.load_bundle(row["before_value_json"])
        try:
            live = bundle_module.read_bundle(connection, entity)
        except CatalogError:
            conflicts.append(
                {
                    "entity_type": str(row["entity_type"]),
                    "entity_id": entity,
                    "reason": "person_missing",
                }
            )
            continue
        differences = bundle_module.bundle_differences(live, recorded_after)
        if differences:
            conflicts.append(
                {
                    "entity_type": str(row["entity_type"]),
                    "entity_id": entity,
                    "reason": ",".join(differences),
                }
            )
            continue
        pending.append((entity, live, recorded_before))
    if conflicts:
        raise fail(
            PERSON_OPERATION_NOT_UNDOABLE,
            "a target of this operation was edited afterwards",
            operation_id=original_id,
            conflicts=conflicts,
        )

    # ADR-0010 §10.1: restoring the recorded link sets must not depend on the person id order.
    # Every effective row of the faces this operation touched is therefore removed **once**,
    # before any of them is written back, so no statement boundary can ever observe two
    # ``CONFIRMED`` rows for one face (``ux_face_person_links_effective``). The affected set
    # is exactly the one the recorded before/after snapshots name - nothing else is touched.
    restoring_faces = set(_faces_needing_link_restore(pending))
    revision_hints = (
        _clear_effective_links(connection, sorted(restoring_faces)) if restoring_faces else {}
    )

    new_operation_id = operation_id_for(request_id)
    new_operation_revision = _next_operation_revision(connection)
    connection.execute(
        "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
        "source_person_ids_json, target_person_id, state, operation_revision, "
        "undo_of_operation_id, undone_by, catalog_filter_revision, created_at, committed_at) "
        "VALUES (?, ?, ?, 'UNDO', ?, ?, 'COMMITTED', ?, ?, NULL, 0, ?, ?)",
        (
            str(new_operation_id),
            str(request_id),
            str(digest),
            str(original["source_person_ids_json"]),
            None if original["target_person_id"] is None else int(original["target_person_id"]),
            int(new_operation_revision),
            str(original_id),
            int(now_ms),
            int(now_ms),
        ),
    )
    live_snapshots: dict[int, dict[str, Any]] = {}
    restored_snapshots: dict[int, dict[str, Any]] = {}
    changed = False
    for entity, live, recorded_before in pending:
        live_snapshots[entity] = live
        row = require_person_row(connection, entity)
        person_written = any(
            live.get(field) != recorded_before.get(field) for field in _PERSON_FIELDS
        )
        if person_written:
            connection.execute(
                "UPDATE persons SET name = ?, name_key = ?, avatar_face_id = ?, "
                "merged_into = ?, revision = ?, updated_at = ? WHERE person_id = ?",
                (
                    recorded_before["name"],
                    recorded_before["name_key"],
                    recorded_before["avatar_face_id"],
                    recorded_before["merged_into"],
                    int(row["revision"]) + 1,
                    int(now_ms),
                    int(entity),
                ),
            )
            changed = True
        if _restore_aliases(
            connection,
            entity=int(entity),
            live=live,
            wanted=recorded_before,
            operation_id=new_operation_id,
            now_ms=int(now_ms),
        ):
            changed = True
    # Links are restored after every alias decision, and each set is written as one
    # delete-then-insert, so the partial unique index on the effective link never observes a
    # half-restored set.
    for entity, live, recorded_before in pending:
        wanted_links = {
            (str(link["face_id"]), str(link["assignment_kind"])): link
            for link in recorded_before.get("links", [])
        }
        if _restore_link_rows(
            connection,
            entity=int(entity),
            live=live,
            wanted=recorded_before,
            operation_id=new_operation_id,
            now_ms=int(now_ms),
            forced_keys={
                key
                for key, link in wanted_links.items()
                if str(link["decision"]) == "CONFIRMED"
                and str(key[0]) in restoring_faces
            },
            revision_hints=revision_hints,
        ):
            changed = True
        restored_snapshots[entity] = bundle_module.read_bundle(connection, int(entity))

    for index, (entity, _live, _recorded) in enumerate(pending):
        connection.execute(
            "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
            "entity_id, before_value_json, after_value_json, after_revision) "
            "VALUES (?, ?, 'PERSON_BUNDLE', ?, ?, ?, ?)",
            (
                str(new_operation_id),
                int(index),
                int(entity),
                bundle_module.bundle_json(live_snapshots[entity]),
                bundle_module.bundle_json(restored_snapshots[entity]),
                int(restored_snapshots[entity]["revision"]),
            ),
        )
    connection.execute(
        "UPDATE person_operations SET state = 'UNDONE', undone_by = ? WHERE operation_id = ?",
        (str(new_operation_id), str(original_id)),
    )
    if changed:
        filter_revision = publish_derived(
            connection,
            faces=_affected_faces(live_snapshots, restored_snapshots),
            now_ms=int(now_ms),
            refresh=refresh,
        )
    else:
        filter_revision = revision_module.read_filter_revision(connection)
    connection.execute(
        "UPDATE person_operations SET catalog_filter_revision = ? WHERE operation_id = ?",
        (int(filter_revision), str(new_operation_id)),
    )
    return {
        "operation_id": str(new_operation_id),
        "operation_type": "UNDO",
        "operation_revision": int(new_operation_revision),
        "status": "COMMITTED",
        "undo_of_operation_id": str(original_id),
        "changed": bool(changed),
        "catalog_filter_revision": int(filter_revision),
        "replayed": False,
        "items": [
            {
                "entity_type": "PERSON_BUNDLE",
                "entity_id": int(entity),
                "changed": bool(
                    bundle_module.bundle_differences(
                        restored_snapshots[entity], live_snapshots[entity]
                    )
                ),
                "merged_into": restored_snapshots[entity]["merged_into"],
                "revision": int(restored_snapshots[entity]["revision"]),
            }
            for entity, _live, _recorded in pending
        ],
    }


__all__ = [
    "item_projection",
    "merge_persons",
    "operation_family",
    "operation_result",
    "operation_row",
    "publish_derived",
    "rename_person",
    "undo_person_operation",
]
