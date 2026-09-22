"""The P3-08B face-redetection mapping of ADR-0010.

When a re-detection produces *new* ``face_id`` values, the human knowledge that lived on the
old rows must not silently disappear - and it must not be silently re-invented for the wrong
new face either. ADR-0010 answers that with two additive tables and one review flow:

* :func:`generate_candidates_for_asset` runs inside the *same* transaction that writes a new
  face document. It only ever pairs a **cross-generation** (``old_spec_revision !=
  new_spec_revision``) new row with an old row that still carries human knowledge, computes
  the frozen geometry evidence, refreshes the stable ``mapping_id`` row, invalidates what
  disappeared and recomputes the derived ``candidate_state`` of every affected group.
* :func:`decide_face_mappings` is the one human write: it re-validates the whole 1..200 x
  1..16 request, refuses the batch as a whole on any shape/transfer conflict, and records the
  decision in the *shared* ``person_operations`` log exactly like a rename or a face
  correction. The candidate states are the review machine; the human facts stay where ADR-0007
  put them.
* :func:`undo_face_mapping_operation` is the compensating operation; it restores the recorded
  semantics with strictly forward-only revisions and re-applies the provisional hold.

Two rules are easy to get wrong and are therefore explicit in the code below:

* the server **never auto-confirms** (a lone ``UNIQUE_HIGH`` stays ``OPEN``), and
* *no* ambiguity transfers human knowledge: only a confirmed mapping moves a lock, a person
  or a denial, and a split confirmation keeps at most one inheritor per old *and* per new
  face.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from photo_search.catalog.errors import (
    FACE_MAPPING_BATCH_TOO_LARGE,
    FACE_MAPPING_CONFLICT,
    FACE_MAPPING_IDEMPOTENCY_CONFLICT,
    FACE_MAPPING_NOT_FOUND,
    FACE_MAPPING_OPERATION_NOT_UNDOABLE,
    FACE_MAPPING_STALE,
    FACE_MAPPING_TARGET_CONTROLLED,
    FACE_NOT_FOUND,
    MAX_MAPPING_DECISIONS,
    MAX_MAPPING_TARGETS,
)
from photo_search.persons import contracts
from photo_search.persons.contracts import (
    canonical_json,
    check_expected_revision,
    check_request_id,
    fail,
    operation_id_for,
    payload_sha256,
)
from photo_search.places import revision as revision_module

#: The envelope literal of the companion document ``p3-face-redetection-contracts.v1.json``.
CONTRACT_VERSION = "p3-face-redetection-contracts.v1"

#: The request family the payload hash names, so a mapping decision and a face correction can
#: never share a digest by accident.
ENDPOINT_DECISIONS = "face-mappings.decisions"

#: ADR-0010 §3.2: the five frozen matching constants of v1. Changing any of them is a new
#: contract version, never a local tweak.
MATCH_IOU_MIN = 0.30
MATCH_LANDMARK_MAX = 0.25
MATCH_IOU_HIGH = 0.50
MATCH_LANDMARK_HIGH = 0.12

MATCH_HIGH = "HIGH"
MATCH_WEAK = "WEAK"

STATE_SUGGESTED = "SUGGESTED"
STATE_CONFIRMED = "CONFIRMED"
STATE_REJECTED = "REJECTED"
STATE_STALE = "STALE"
MAPPING_STATES = (STATE_SUGGESTED, STATE_CONFIRMED, STATE_REJECTED, STATE_STALE)

GROUP_OPEN = "OPEN"
GROUP_CONFIRMED = "CONFIRMED"
GROUP_REJECTED = "REJECTED"

CANDIDATE_NONE = "NO_CANDIDATE"
CANDIDATE_UNIQUE_HIGH = "UNIQUE_HIGH"
CANDIDATE_UNIQUE_WEAK = "UNIQUE_WEAK"
CANDIDATE_AMBIGUOUS = "AMBIGUOUS"

DECISION_CONFIRM = "confirm"
DECISION_REJECT = "reject"

#: A decision is recorded with one of the two frozen operation types; a mixed request is a
#: confirmation request that also acknowledged some groups, which the two types cannot spell,
#: so the confirm spelling is used whenever at least one group was confirmed.
OPERATION_CONFIRM = "FACE_MAPPING_CONFIRM"
OPERATION_REJECT = "FACE_MAPPING_REJECT"
MAPPING_OPERATION_TYPES = (OPERATION_CONFIRM, OPERATION_REJECT)

ENTITY_TYPE_MAPPING_GROUP = "FACE_MAPPING_GROUP"

#: The controlled crop URL template of ADR-0010 §8.1 (the same endpoint P3-06 shipped).
FACE_THUMBNAIL_TEMPLATE = "/api/v1/faces/{face_id}/thumbnail"

_HEX_DIGITS = frozenset("0123456789abcdef")

_MAPPING_COLUMNS = (
    "mapping_id, old_face_id, new_face_id, asset_id, old_spec_revision, new_spec_revision, "
    "match_state, state, inherits_person, revision, last_operation_id"
)

_GROUP_COLUMNS = (
    "old_face_id, asset_id, old_spec_revision, candidate_state, state, revision, "
    "last_operation_id"
)


# --------------------------------------------------------------------------- shape checks
def check_contract_version(value: Any) -> str:
    if value != CONTRACT_VERSION:
        raise fail(
            "VALIDATION_ERROR",
            f"contract_version must be {CONTRACT_VERSION}",
            reason="contract_version",
        )
    return CONTRACT_VERSION


def check_face_id(value: Any, *, field: str = "face_id") -> str:
    """A face id is the 64-character lowercase hex content key of ``faces.face_id``."""
    if not isinstance(value, str):
        raise fail("VALIDATION_ERROR", f"{field} must be a string", reason=field)
    text = value.strip()
    if len(text) != 64 or any(character not in _HEX_DIGITS for character in text):
        raise fail(
            "VALIDATION_ERROR",
            f"{field} must be a 64-character lowercase hex id",
            reason=field,
        )
    return text


def check_asset_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or int(value) < 1:
        raise fail("VALIDATION_ERROR", "asset_id must be a positive integer", reason="asset_id")
    return int(value)


def check_review_revision(value: Any, *, field: str = "expected_review_revision") -> int:
    revision = check_expected_revision(value, field=field)
    if int(revision) < 1:
        raise fail("VALIDATION_ERROR", f"{field} must be >= 1", reason=field)
    return int(revision)


def check_decision(value: Any) -> str:
    if value not in (DECISION_CONFIRM, DECISION_REJECT):
        raise fail("VALIDATION_ERROR", "decision must be confirm or reject", reason="decision")
    return str(value)


# ------------------------------------------------------------------- identity and evidence
def mapping_id_for(
    *,
    asset_id: int,
    old_face_id: str,
    new_face_id: str,
    old_spec_revision: str,
    new_spec_revision: str,
) -> str:
    """ADR-0010 §2.1: the stable id of one pair inside one asset and one generation step."""
    payload = canonical_json(
        {
            "asset_id": int(asset_id),
            "old_face_id": str(old_face_id),
            "new_face_id": str(new_face_id),
            "old_spec_revision": str(old_spec_revision),
            "new_spec_revision": str(new_spec_revision),
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bbox_of(face: Mapping[str, Any]) -> tuple[float, float, float, float]:
    raw = json.loads(str(face["bbox_json"]))
    # Both stored shapes are read (see ``media.face_crop.parse_bbox``): the plain four-number
    # list every P3-06 fixture uses and the ``{"bbox": [...]}`` object the processor writes.
    payload = raw.get("bbox") if isinstance(raw, Mapping) else raw
    values = [float(value) for value in payload]
    return values[0], values[1], values[2], values[3]


def _landmarks_of(face: Mapping[str, Any]) -> list[tuple[float, float]]:
    raw = json.loads(str(face["landmarks_json"]))
    payload = raw.get("landmarks") if isinstance(raw, Mapping) else raw
    return [(float(x), float(y)) for x, y in payload]


def intersection_over_union(
    first: tuple[float, float, float, float], second: tuple[float, float, float, float]
) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    first_area = (first[2] - first[0]) * (first[3] - first[1])
    second_area = (second[2] - second[0]) * (second[3] - second[1])
    union = first_area + second_area - intersection
    if union <= 0.0:
        return 0.0
    return float(intersection / union)


def landmark_ratio(
    old_bbox: tuple[float, float, float, float],
    old_landmarks: Sequence[tuple[float, float]],
    new_landmarks: Sequence[tuple[float, float]],
) -> float:
    """``sqrt(mean ||p_old - p_new||^2) / diag(old bbox)``, the frozen v1 definition."""
    total = 0.0
    for (x_old, y_old), (x_new, y_new) in zip(old_landmarks, new_landmarks, strict=True):
        total += (float(x_old) - float(x_new)) ** 2 + (float(y_old) - float(y_new)) ** 2
    mean = total / float(len(old_landmarks))
    diagonal = math.hypot(old_bbox[2] - old_bbox[0], old_bbox[3] - old_bbox[1])
    if diagonal <= 0.0:
        return float("inf")
    return float(math.sqrt(mean) / diagonal)


def match_state_for(
    *,
    old_face: Mapping[str, Any],
    new_face: Mapping[str, Any],
) -> str | None:
    """``HIGH``/``WEAK`` for a qualified pair, ``None`` when the pair is not a candidate.

    A ``LOW`` quality face on either side can never reach ``HIGH`` (ADR-0010 §3.2).
    """
    old_bbox = _bbox_of(old_face)
    new_bbox = _bbox_of(new_face)
    iou = intersection_over_union(old_bbox, new_bbox)
    ratio = landmark_ratio(old_bbox, _landmarks_of(old_face), _landmarks_of(new_face))
    if not math.isfinite(ratio):
        return None
    if iou < MATCH_IOU_MIN or ratio > MATCH_LANDMARK_MAX:
        return None
    low_quality = str(old_face["quality_state"]) != "OK" or str(new_face["quality_state"]) != "OK"
    if iou >= MATCH_IOU_HIGH and ratio <= MATCH_LANDMARK_HIGH and not low_quality:
        return MATCH_HIGH
    return MATCH_WEAK


def candidate_state_for(match_states: Sequence[str]) -> str:
    """The frozen four-state derivation of ADR-0010 §3.3."""
    count = len(match_states)
    if count == 0:
        return CANDIDATE_NONE
    if count == 1:
        return CANDIDATE_UNIQUE_HIGH if match_states[0] == MATCH_HIGH else CANDIDATE_UNIQUE_WEAK
    return CANDIDATE_AMBIGUOUS


# ------------------------------------------------------------------------ shared readers
def _human_knowledge_modes(connection: sqlite3.Connection, face_ids: Sequence[str]) -> dict[str, str]:
    """``face_id -> control mode`` for the faces that carry a ``MANUAL_*`` control row."""
    modes: dict[str, str] = {}
    unique = sorted({str(value) for value in face_ids})
    for index in range(0, len(unique), 400):
        chunk = unique[index : index + 400]
        placeholders = ", ".join("?" for _ in chunk)
        rows = connection.execute(
            f"SELECT face_id, mode FROM face_assignment_controls WHERE face_id IN ({placeholders})",
            tuple(chunk),
        ).fetchall()
        for row in rows:
            if str(row["mode"]) in ("MANUAL_ASSIGNED", "MANUAL_UNASSIGNED"):
                modes[str(row["face_id"])] = str(row["mode"])
    return modes


def _active_exclusion_ids(connection: sqlite3.Connection, face_ids: Sequence[str]) -> set[str]:
    faces: set[str] = set()
    unique = sorted({str(value) for value in face_ids})
    for index in range(0, len(unique), 400):
        chunk = unique[index : index + 400]
        placeholders = ", ".join("?" for _ in chunk)
        rows = connection.execute(
            f"SELECT DISTINCT face_id FROM face_exclusions WHERE active = 1 "
            f"AND face_id IN ({placeholders})",
            tuple(chunk),
        ).fetchall()
        faces.update(str(row["face_id"]) for row in rows)
    return faces


def human_knowledge_face_ids(connection: sqlite3.Connection, *, asset_id: int) -> set[str]:
    """The old faces of one asset whose knowledge a mapping has to review (ADR-0010 §3.1)."""
    rows = connection.execute(
        "SELECT face_id FROM faces WHERE asset_id = ?",
        (int(asset_id),),
    ).fetchall()
    face_ids = [str(row["face_id"]) for row in rows]
    controlled = set(_human_knowledge_modes(connection, face_ids))
    controlled |= _active_exclusion_ids(connection, face_ids)
    return controlled


def group_row(connection: sqlite3.Connection, old_face_id: str) -> Any:
    return connection.execute(
        f"SELECT {_GROUP_COLUMNS} FROM face_redetection_groups WHERE old_face_id = ?",
        (str(old_face_id),),
    ).fetchone()


def mapping_rows(connection: sqlite3.Connection, old_face_id: str) -> list[Any]:
    return list(
        connection.execute(
            f"SELECT {_MAPPING_COLUMNS} FROM face_redetection_mappings "
            "WHERE old_face_id = ? ORDER BY new_face_id",
            (str(old_face_id),),
        ).fetchall()
    )


def live_candidate_rows(connection: sqlite3.Connection, old_face_id: str) -> list[Any]:
    """Candidate rows that still describe a live new face (ADR-0010 §3.4 step ③)."""
    return [
        row
        for row in mapping_rows(connection, old_face_id)
        if str(row["state"]) != STATE_STALE
        and connection.execute(
            "SELECT 1 FROM faces WHERE face_id = ?", (str(row["new_face_id"]),)
        ).fetchone()
        is not None
    ]


def live_candidate_ids(connection: sqlite3.Connection, old_face_id: str) -> set[str]:
    return {str(row["new_face_id"]) for row in live_candidate_rows(connection, old_face_id)}


def _recompute_group(
    connection: sqlite3.Connection, *, old_face_id: str, now_ms: int
) -> None:
    """Re-derive the group's ``candidate_state`` from its live candidates, forward only."""
    group = group_row(connection, str(old_face_id))
    if group is None:
        return
    candidates = live_candidate_rows(connection, str(old_face_id))
    states = [str(row["match_state"]) for row in candidates]
    refreshed = candidate_state_for(states)
    membership = sorted(str(row["new_face_id"]) for row in candidates)
    recorded = sorted(
        str(row["new_face_id"])
        for row in mapping_rows(connection, str(old_face_id))
        if str(row["state"]) != STATE_STALE
    )
    state_changed = str(group["candidate_state"]) != refreshed
    membership_changed = membership != recorded
    if not state_changed and not membership_changed:
        return
    # A decided group keeps its decision (``state``), but its review content really did move,
    # so the revision has to move with it - otherwise ``expected_review_revision`` would be
    # comparing against a stale review.
    connection.execute(
        "UPDATE face_redetection_groups SET candidate_state = ?, revision = revision + 1, "
        "updated_at = ? WHERE old_face_id = ?",
        (str(refreshed), int(now_ms), str(old_face_id)),
    )


def generate_candidates_for_asset(
    connection: sqlite3.Connection,
    *,
    asset_id: int,
    new_face_ids: Sequence[str],
    now_ms: int,
) -> None:
    """Refresh the mapping evidence of one asset inside the face-write transaction.

    ADR-0010 §3.1/§3.4: only a *cross-generation* pair of a just-written new row and a
    surviving human-owned old row is a candidate, invalidation is decided before the hold is
    computed elsewhere, and a pair keeps exactly one row.
    """
    from photo_search.face.contract import FACE_SPEC_REVISION

    spec_revision = str(FACE_SPEC_REVISION)
    new_ids = sorted({str(value) for value in new_face_ids})
    if new_ids:
        placeholders = ", ".join("?" for _ in new_ids)
        old_rows = list(
            connection.execute(
                "SELECT * FROM faces WHERE asset_id = ? AND spec_revision <> ? "
                f"AND face_id NOT IN ({placeholders})",
                (int(asset_id), spec_revision, *new_ids),
            ).fetchall()
        )
        new_rows = list(
            connection.execute(
                f"SELECT * FROM faces WHERE face_id IN ({placeholders})", tuple(new_ids)
            ).fetchall()
        )
    else:
        # ADR-0010 §3.3: "the face disappeared" is itself a reviewable outcome. A document
        # with no face at all therefore still has to keep the human anchor's group (with
        # ``NO_CANDIDATE``); it just has no pair to evaluate.
        old_rows = list(
            connection.execute(
                "SELECT * FROM faces WHERE asset_id = ? AND spec_revision <> ?",
                (int(asset_id), spec_revision),
            ).fetchall()
        )
        new_rows = []
    if not old_rows:
        # A brand-new generation with no human-owned ancestor: nothing to review. Any
        # *existing* group of this asset has to be re-evaluated all the same, because its
        # candidates may have vanished with this document.
        _refresh_existing_groups(connection, asset_id=int(asset_id), now_ms=int(now_ms))
        return
    knowledge = human_knowledge_face_ids(connection, asset_id=int(asset_id))
    anchors = [row for row in old_rows if str(row["face_id"]) in knowledge]
    qualified: dict[str, set[str]] = {str(row["face_id"]): set() for row in anchors}
    for old_row in anchors:
        old_face_id = str(old_row["face_id"])
        for new_row in new_rows:
            new_face_id = str(new_row["face_id"])
            if old_face_id == new_face_id:
                # ADR-0010 §2.1: a self-mapping is never generated.
                continue
            match_state = match_state_for(old_face=old_row, new_face=new_row)
            if match_state is None:
                continue
            qualified[old_face_id].add(new_face_id)
            _upsert_mapping_row(
                connection,
                asset_id=int(asset_id),
                old_row=old_row,
                new_row=new_row,
                spec_revision=spec_revision,
                match_state=match_state,
                now_ms=int(now_ms),
            )
    _invalidate(connection, asset_id=int(asset_id), qualified=qualified, now_ms=int(now_ms))
    for old_row in anchors:
        _ensure_group(connection, old_row=old_row, now_ms=int(now_ms))
    _refresh_existing_groups(connection, asset_id=int(asset_id), now_ms=int(now_ms))


def _upsert_mapping_row(
    connection: sqlite3.Connection,
    *,
    asset_id: int,
    old_row: Mapping[str, Any],
    new_row: Mapping[str, Any],
    spec_revision: str,
    match_state: str,
    now_ms: int,
) -> None:
    mapping_id = mapping_id_for(
        asset_id=int(asset_id),
        old_face_id=str(old_row["face_id"]),
        new_face_id=str(new_row["face_id"]),
        old_spec_revision=str(old_row["spec_revision"]),
        new_spec_revision=spec_revision,
    )
    existing = connection.execute(
        "SELECT mapping_id, match_state, state, revision FROM face_redetection_mappings "
        "WHERE old_face_id = ? AND new_face_id = ?",
        (str(old_row["face_id"]), str(new_row["face_id"])),
    ).fetchone()
    if existing is None:
        connection.execute(
            "INSERT INTO face_redetection_mappings(mapping_id, old_face_id, new_face_id, "
            "asset_id, old_spec_revision, new_spec_revision, match_state, state, "
            "inherits_person, revision, last_operation_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'SUGGESTED', 0, 1, NULL, ?, ?)",
            (
                str(mapping_id),
                str(old_row["face_id"]),
                str(new_row["face_id"]),
                int(asset_id),
                str(old_row["spec_revision"]),
                str(spec_revision),
                str(match_state),
                int(now_ms),
                int(now_ms),
            ),
        )
        return
    if str(existing["state"]) == STATE_STALE:
        # ADR-0010 §3.3/§7: this pair is *live* and *qualified again* - it came back through
        # the very detection that is running now - so an ``OPEN`` group must count it as a
        # candidate instead of reporting ``NO_CANDIDATE`` ("this detection found no matching
        # face") for a face that is alive and eligible, which would leave the human able to
        # reject but never to confirm. A *decided* group is the exception: §7 keeps a human
        # decision out of a re-detection's reach, so its review set is not reopened either.
        group = group_row(connection, str(old_row["face_id"]))
        if group is not None and str(group["state"]) == GROUP_OPEN:
            connection.execute(
                "UPDATE face_redetection_mappings SET state = 'SUGGESTED', match_state = ?, "
                "inherits_person = 0, revision = revision + 1, updated_at = ? "
                "WHERE mapping_id = ?",
                (str(match_state), int(now_ms), str(existing["mapping_id"])),
            )
            return
    # The pair already exists: refresh the evidence in place. A re-detection never inserts a
    # second row and never rewinds a decision; revision only ever moves forward (ADR-0010
    # §2.1, §7).
    if str(existing["match_state"]) == str(match_state):
        connection.execute(
            "UPDATE face_redetection_mappings SET updated_at = ? WHERE old_face_id = ? "
            "AND new_face_id = ?",
            (int(now_ms), str(old_row["face_id"]), str(new_row["face_id"])),
        )
        return
    connection.execute(
        "UPDATE face_redetection_mappings SET match_state = ?, revision = revision + 1, "
        "updated_at = ? WHERE old_face_id = ? AND new_face_id = ?",
        (
            str(match_state),
            int(now_ms),
            str(old_row["face_id"]),
            str(new_row["face_id"]),
        ),
    )


def _invalidate(
    connection: sqlite3.Connection,
    *,
    asset_id: int,
    qualified: Mapping[str, set[str]],
    now_ms: int,
) -> None:
    """Supersede every candidate this detection no longer produces (ADR-0010 §3.4/§7)."""
    rows = list(
        connection.execute(
            f"SELECT {_MAPPING_COLUMNS} FROM face_redetection_mappings WHERE asset_id = ?",
            (int(asset_id),),
        ).fetchall()
    )
    for row in rows:
        old_face_id = str(row["old_face_id"])
        new_face_id = str(row["new_face_id"])
        alive = (
            connection.execute(
                "SELECT 1 FROM faces WHERE face_id = ?", (new_face_id,)
            ).fetchone()
            is not None
        )
        if not alive:
            # An invalidated pair is ``STALE`` whatever its state was: it must never be read
            # as "this face is still the confirmed successor".
            if str(row["state"]) == STATE_STALE:
                continue
            connection.execute(
                "UPDATE face_redetection_mappings SET state = 'STALE', inherits_person = 0, "
                "revision = revision + 1, updated_at = ? WHERE mapping_id = ?",
                (int(now_ms), str(row["mapping_id"])),
            )
            continue
        if old_face_id not in qualified:
            # This generation did not evaluate that old face (it carries no human knowledge
            # any more), so its existing review is left exactly as it is.
            continue
        if new_face_id in qualified[old_face_id]:
            continue
        if str(row["state"]) != STATE_SUGGESTED:
            # A human decision that is still backed by a live face stands; only the review
            # row of an undecided pair is superseded when the evidence disappears.
            continue
        connection.execute(
            "UPDATE face_redetection_mappings SET state = 'STALE', inherits_person = 0, "
            "revision = revision + 1, updated_at = ? WHERE mapping_id = ?",
            (int(now_ms), str(row["mapping_id"])),
        )


def _ensure_group(
    connection: sqlite3.Connection, *, old_row: Mapping[str, Any], now_ms: int
) -> None:
    old_face_id = str(old_row["face_id"])
    if group_row(connection, old_face_id) is not None:
        return
    candidates = live_candidate_rows(connection, old_face_id)
    connection.execute(
        "INSERT INTO face_redetection_groups(old_face_id, asset_id, old_spec_revision, "
        "candidate_state, state, revision, last_operation_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'OPEN', 1, NULL, ?, ?)",
        (
            old_face_id,
            int(old_row["asset_id"]),
            str(old_row["spec_revision"]),
            candidate_state_for([str(row["match_state"]) for row in candidates]),
            int(now_ms),
            int(now_ms),
        ),
    )


def _refresh_existing_groups(
    connection: sqlite3.Connection, *, asset_id: int, now_ms: int
) -> None:
    rows = connection.execute(
        "SELECT old_face_id FROM face_redetection_groups WHERE asset_id = ? ORDER BY old_face_id",
        (int(asset_id),),
    ).fetchall()
    for row in rows:
        _recompute_group(connection, old_face_id=str(row["old_face_id"]), now_ms=int(now_ms))


def retire_mapping_faces(
    connection: sqlite3.Connection, *, face_ids: Sequence[str], now_ms: int
) -> None:
    """Keep the mapping tables consistent with a face deletion inside the same transaction.

    A removed *candidate* leaves its ``STALE`` audit row behind (ADR-0010 §7); a removed *old
    anchor* has no reviewable pair left, so its group and rows go with it before the face row
    itself is deleted (the tables carry a real foreign key on ``old_face_id``).
    """
    for face_id in sorted({str(value) for value in face_ids}):
        connection.execute(
            "UPDATE face_redetection_mappings SET state = 'STALE', inherits_person = 0, "
            "revision = revision + 1, updated_at = ? WHERE new_face_id = ? "
            "AND state <> 'STALE'",
            (int(now_ms), str(face_id)),
        )
        connection.execute(
            "DELETE FROM face_redetection_mappings WHERE old_face_id = ?", (str(face_id),)
        )
        connection.execute(
            "DELETE FROM face_redetection_groups WHERE old_face_id = ?", (str(face_id),)
        )
    _refresh_after_retirement(connection, face_ids=face_ids, now_ms=int(now_ms))


def _refresh_after_retirement(
    connection: sqlite3.Connection, *, face_ids: Sequence[str], now_ms: int
) -> None:
    rows = connection.execute(
        "SELECT DISTINCT old_face_id FROM face_redetection_mappings WHERE new_face_id IN "
        f"({', '.join('?' for _ in face_ids)})",
        tuple(str(value) for value in face_ids),
    ).fetchall()
    for row in rows:
        _recompute_group(connection, old_face_id=str(row["old_face_id"]), now_ms=int(now_ms))


def hold_face_ids(connection: sqlite3.Connection, *, asset_id: int) -> set[str]:
    """ADR-0010 §3.4: the new faces an ``OPEN`` review forbids the automatic path to write."""
    rows = connection.execute(
        "SELECT m.new_face_id AS new_face_id FROM face_redetection_mappings AS m "
        "JOIN face_redetection_groups AS g ON g.old_face_id = m.old_face_id "
        "JOIN faces AS f ON f.face_id = m.new_face_id "
        "WHERE g.asset_id = ? AND g.state = 'OPEN' AND m.state <> 'STALE' "
        "AND f.spec_revision = ?",
        (int(asset_id), str(_current_spec_revision())),
    ).fetchall()
    return {str(row["new_face_id"]) for row in rows}


def _current_spec_revision() -> str:
    from photo_search.face.contract import FACE_SPEC_REVISION

    return str(FACE_SPEC_REVISION)


def requeue_asset(connection: sqlite3.Connection, *, asset_id: int, now_ms: int) -> None:
    """Put one asset back into the durable association queue (ADR-0010 §3.4/§5.3)."""
    spec_revision = _current_spec_revision()
    row = connection.execute(
        "SELECT COALESCE(MAX(face_revision), 1) AS revision FROM faces "
        "WHERE asset_id = ? AND spec_revision = ?",
        (int(asset_id), spec_revision),
    ).fetchone()
    face_revision = int(row["revision"]) + 1
    connection.execute(
        "INSERT INTO face_association_queue(asset_id, spec_revision, face_revision, queued_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(asset_id) DO UPDATE SET "
        "spec_revision = excluded.spec_revision, face_revision = excluded.face_revision, "
        "queued_at = excluded.queued_at",
        (int(asset_id), spec_revision, int(face_revision), int(now_ms)),
    )


# -------------------------------------------------------------------------- snapshots
def group_bundle(connection: sqlite3.Connection, old_face_id: str) -> dict[str, Any]:
    """The bounded ``FACE_MAPPING_GROUP`` snapshot (ids, states and revisions only)."""
    group = group_row(connection, str(old_face_id))
    if group is None:
        raise fail(
            FACE_MAPPING_NOT_FOUND,
            f"face {old_face_id} has no reviewable redetection group",
            old_face_id=str(old_face_id),
        )
    return {
        "old_face_id": str(group["old_face_id"]),
        "asset_id": int(group["asset_id"]),
        "old_spec_revision": str(group["old_spec_revision"]),
        "state": str(group["state"]),
        "revision": int(group["revision"]),
        "candidate_state": str(group["candidate_state"]),
        "candidates": [
            {
                "mapping_id": str(row["mapping_id"]),
                "new_face_id": str(row["new_face_id"]),
                "match_state": str(row["match_state"]),
                "state": str(row["state"]),
                "revision": int(row["revision"]),
                "inherits_person": bool(int(row["inherits_person"])),
            }
            for row in mapping_rows(connection, str(old_face_id))
        ],
    }


def group_bundle_json(bundle: Mapping[str, Any]) -> str:
    return canonical_json(dict(bundle))


def load_group_bundle(raw: Any) -> dict[str, Any]:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    return json.loads(str(raw))


def group_item_projection(row: Any) -> dict[str, Any]:
    """The frozen ``MappingOperationItem`` shape of one stored group snapshot pair."""
    before = load_group_bundle(row["before_value_json"])
    after = load_group_bundle(row["after_value_json"])
    return {
        "entity_type": ENTITY_TYPE_MAPPING_GROUP,
        "old_face_id": str(after.get("old_face_id") or before.get("old_face_id") or row["entity_key"]),
        "changed": bool(_group_differences(before, after)),
        "state": str(after.get("state")),
        "revision": int(after.get("revision") or row["after_revision"]),
        "candidate_state": str(after.get("candidate_state")),
        "candidates": [
            {
                "new_face_id": str(item["new_face_id"]),
                "state": str(item["state"]),
                "revision": int(item["revision"]),
                "inherits_person": bool(item["inherits_person"]),
            }
            for item in after.get("candidates", [])
        ],
    }


def _group_differences(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    differences: list[str] = []
    for field in ("state", "revision", "candidate_state"):
        if before.get(field) != after.get(field):
            differences.append(field)
    if _candidate_keys(before) != _candidate_keys(after):
        differences.append("candidates")
    return differences


def _candidate_keys(bundle: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    return sorted(
        (
            str(item["new_face_id"]),
            str(item["state"]),
            int(item["revision"]),
            bool(item["inherits_person"]),
        )
        for item in bundle.get("candidates", [])
    )


# -------------------------------------------------------------------------- read surface
def group_summary(connection: sqlite3.Connection, group: Any) -> dict[str, Any]:
    """One review row: the group plus the *existing* control projection of the old face."""
    from photo_search.persons import face_correction

    old_face_id = str(group["old_face_id"])
    candidates = live_candidate_rows(connection, old_face_id)
    mode = face_correction.control_mode(connection, old_face_id)
    control_revision = face_correction.logical_control_revision(connection, old_face_id)
    assignment = connection.execute(
        "SELECT person_id FROM face_person_links WHERE face_id = ? AND decision = 'CONFIRMED'",
        (old_face_id,),
    ).fetchone()
    assigned: int | None = None
    if assignment is not None and mode != "MANUAL_UNASSIGNED":
        assigned = face_correction.canonical_person(connection, int(assignment["person_id"]))
    return {
        "contract_version": CONTRACT_VERSION,
        "old_face_id": old_face_id,
        "asset_id": int(group["asset_id"]),
        "old_spec_revision": str(group["old_spec_revision"]),
        "candidate_state": str(group["candidate_state"]),
        "state": str(group["state"]),
        "revision": int(group["revision"]),
        "control_mode": mode,
        "control_revision": int(control_revision),
        "assigned_person_id": assigned,
        #: The frozen cross-field rules of ADR-0010 §8.1: ``NO_CANDIDATE`` is 0, a unique
        #: state is exactly 1 and ``AMBIGUOUS`` is at least 2; ``MANUAL_UNASSIGNED`` never
        #: reports a person.
        "candidate_count": len(candidates),
        "thumbnail_url": FACE_THUMBNAIL_TEMPLATE.format(face_id=old_face_id),
    }


def group_detail(connection: sqlite3.Connection, group: Any) -> dict[str, Any]:
    payload = group_summary(connection, group)
    candidates = []
    for row in live_candidate_rows(connection, str(group["old_face_id"])):
        new_face_id = str(row["new_face_id"])
        face = connection.execute(
            "SELECT quality_state FROM faces WHERE face_id = ?", (new_face_id,)
        ).fetchone()
        candidates.append(
            {
                "new_face_id": new_face_id,
                "new_spec_revision": str(row["new_spec_revision"]),
                "match_state": str(row["match_state"]),
                "quality_state": (
                    "OK" if face is None else str(face["quality_state"])
                ),
                "state": str(row["state"]),
                "revision": int(row["revision"]),
                "thumbnail_url": FACE_THUMBNAIL_TEMPLATE.format(face_id=new_face_id),
            }
        )
    payload["candidates"] = candidates
    return payload


# ---------------------------------------------------------------------------- decisions
def _normalize_decision(
    connection: sqlite3.Connection, item: Any, *, asset_id: int
) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise fail("VALIDATION_ERROR", "each decision must be an object", reason="decision")
    unexpected = sorted(
        str(key)
        for key in item
        if str(key)
        not in ("old_face_id", "expected_review_revision", "decision", "targets")
    )
    if unexpected:
        raise fail(
            "VALIDATION_ERROR",
            "a decision carries only the frozen fields",
            reason="unexpected_field",
        )
    old_face_id = check_face_id(item.get("old_face_id"), field="old_face_id")
    expected = check_review_revision(item.get("expected_review_revision"))
    decision = check_decision(item.get("decision"))
    targets = item.get("targets", [])
    if isinstance(targets, (str, bytes)) or not isinstance(targets, Sequence):
        raise fail("VALIDATION_ERROR", "targets must be a list", reason="targets")
    if len(targets) > MAX_MAPPING_TARGETS:
        raise fail(
            FACE_MAPPING_BATCH_TOO_LARGE,
            f"one decision carries at most {MAX_MAPPING_TARGETS} targets",
            max_decisions=MAX_MAPPING_DECISIONS,
            max_targets=MAX_MAPPING_TARGETS,
        )
    if decision == DECISION_CONFIRM and not targets:
        raise fail(
            "VALIDATION_ERROR",
            "a confirm needs at least one target",
            reason="confirm_requires_targets",
            old_face_id=old_face_id,
        )
    if decision == DECISION_REJECT and targets:
        raise fail(
            "VALIDATION_ERROR",
            "a reject carries no targets",
            reason="reject_with_targets",
            old_face_id=old_face_id,
        )
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for target in targets:
        if not isinstance(target, Mapping):
            raise fail("VALIDATION_ERROR", "each target must be an object", reason="targets")
        unexpected = sorted(
            str(key) for key in target if str(key) not in ("new_face_id", "inherits_person")
        )
        if unexpected:
            raise fail(
                "VALIDATION_ERROR",
                "a target carries only the frozen fields",
                reason="unexpected_field",
                old_face_id=old_face_id,
            )
        new_face_id = check_face_id(target.get("new_face_id"), field="new_face_id")
        inherits = target.get("inherits_person")
        if not isinstance(inherits, bool):
            raise fail(
                "VALIDATION_ERROR",
                "inherits_person must be a boolean",
                reason="inherits_person",
                old_face_id=old_face_id,
                new_face_id=new_face_id,
            )
        if new_face_id in seen:
            raise fail(
                "VALIDATION_ERROR",
                "a decision must not name the same new face twice",
                reason="duplicate_target",
                old_face_id=old_face_id,
                new_face_id=new_face_id,
            )
        seen.add(new_face_id)
        normalized.append({"new_face_id": new_face_id, "inherits_person": bool(inherits)})
    if sum(1 for target in normalized if target["inherits_person"]) > 1:
        raise fail(
            "VALIDATION_ERROR",
            "at most one target of one old face may inherit the person",
            reason="multiple_targets_without_single_inheritor",
            old_face_id=old_face_id,
        )
    return {
        "old_face_id": old_face_id,
        "expected_review_revision": int(expected),
        "decision": decision,
        "targets": normalized,
    }


def _check_idempotency(
    connection: sqlite3.Connection, *, request_id: str, digest: str, asset_id: int
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT operation_id, payload_sha256 FROM person_operations WHERE request_id = ?",
        (str(request_id),),
    ).fetchone()
    if row is None:
        return None
    if str(row["payload_sha256"]) != str(digest):
        raise fail(
            FACE_MAPPING_IDEMPOTENCY_CONFLICT,
            "this request_id is already bound to a different payload",
            asset_id=int(asset_id),
        )
    from photo_search.persons import operations as operation_module

    operation = operation_module.operation_row(connection, str(row["operation_id"]))
    assert operation is not None
    return mapping_operation_result(connection, operation)


def _require_group(
    connection: sqlite3.Connection, *, old_face_id: str, asset_id: int
) -> Any:
    face = connection.execute(
        "SELECT face_id, asset_id FROM faces WHERE face_id = ?", (str(old_face_id),)
    ).fetchone()
    if face is None:
        raise fail(
            FACE_NOT_FOUND, f"face {old_face_id} does not exist", old_face_id=str(old_face_id)
        )
    group = group_row(connection, str(old_face_id))
    if group is None or int(group["asset_id"]) != int(asset_id):
        raise fail(
            FACE_MAPPING_NOT_FOUND,
            "this asset has no reviewable redetection group for that old face",
            old_face_id=str(old_face_id),
            asset_id=int(asset_id),
        )
    return group


def decide_face_mappings(
    connection: sqlite3.Connection,
    *,
    payload: Any = None,
    contract_version: Any,
    request_id: Any,
    asset_id: Any,
    decisions: Any,
    now_ms: int,
    refresh: revision_module.ProjectionRefresh | None = None,
) -> dict[str, Any]:
    """Validate, transfer and record one 1..200 x 1..16 mapping decision."""
    from photo_search.persons import face_correction
    from photo_search.persons import operations as operation_module

    if payload is not None:
        if not isinstance(payload, Mapping):
            raise fail("VALIDATION_ERROR", "the request body must be an object", reason="body")
        unexpected = sorted(
            str(key)
            for key in payload
            if str(key)
            not in ("contract_version", "request_id", "asset_id", "decisions")
        )
        if unexpected:
            raise fail(
                "VALIDATION_ERROR",
                "the decision request carries only the frozen fields",
                reason="unexpected_field",
            )
    check_contract_version(contract_version)
    request_id = check_request_id(request_id)
    asset = check_asset_id(asset_id)
    if isinstance(decisions, (str, bytes)) or not isinstance(decisions, Sequence):
        raise fail("VALIDATION_ERROR", "decisions must be a list", reason="decisions")
    if not decisions:
        raise fail("VALIDATION_ERROR", "a decision request needs at least one group", reason="decisions")
    if len(decisions) > MAX_MAPPING_DECISIONS:
        raise fail(
            FACE_MAPPING_BATCH_TOO_LARGE,
            f"a decision request carries at most {MAX_MAPPING_DECISIONS} groups",
            max_decisions=MAX_MAPPING_DECISIONS,
            max_targets=MAX_MAPPING_TARGETS,
        )
    normalized = [
        _normalize_decision(connection, item, asset_id=asset) for item in decisions
    ]
    seen_old: set[str] = set()
    for item in normalized:
        if item["old_face_id"] in seen_old:
            raise fail(
                "VALIDATION_ERROR",
                "a decision request must not name the same old face twice",
                reason="duplicate_old_face_id",
                old_face_id=str(item["old_face_id"]),
            )
        seen_old.add(str(item["old_face_id"]))
    # ADR-0010 §5.2: the payload hash is order-independent, so the same intent always maps to
    # the same idempotency entry no matter how the UI happened to sort the groups.
    digest = payload_sha256(
        {
            "endpoint": ENDPOINT_DECISIONS,
            "asset_id": int(asset),
            "decisions": sorted(
                (
                    {
                        "old_face_id": str(item["old_face_id"]),
                        "expected_review_revision": int(item["expected_review_revision"]),
                        "decision": str(item["decision"]),
                        "targets": sorted(
                            (
                                {
                                    "new_face_id": str(target["new_face_id"]),
                                    "inherits_person": bool(target["inherits_person"]),
                                }
                                for target in item["targets"]
                            ),
                            key=lambda entry: entry["new_face_id"],
                        ),
                    }
                    for item in normalized
                ),
                key=lambda entry: entry["old_face_id"],
            ),
        }
    )
    replay = _check_idempotency(
        connection, request_id=str(request_id), digest=digest, asset_id=int(asset)
    )
    if replay is not None:
        return replay

    groups: dict[str, Any] = {}
    for item in normalized:
        groups[str(item["old_face_id"])] = _require_group(
            connection, old_face_id=str(item["old_face_id"]), asset_id=int(asset)
        )

    # ---- whole-batch validation: nothing below writes until every group is legal ----
    inheritors: dict[str, str] = {}
    plans: list[dict[str, Any]] = []
    for item in normalized:
        old_face_id = str(item["old_face_id"])
        group = groups[old_face_id]
        candidates = live_candidate_ids(connection, old_face_id)
        for target in item["targets"]:
            new_face_id = str(target["new_face_id"])
            if new_face_id == old_face_id:
                # ADR-0010 §2.1: a self pair is not a mapping and this asset never owns it.
                raise fail(
                    FACE_MAPPING_NOT_FOUND,
                    "this asset does not own that old/new pair",
                    old_face_id=old_face_id,
                    new_face_id=new_face_id,
                    asset_id=int(asset),
                )
            superseded = any(
                str(row["state"]) == STATE_STALE
                for row in mapping_rows(connection, old_face_id)
                if str(row["new_face_id"]) == new_face_id
            )
            if superseded:
                # ADR-0010 §5.2/§7: a candidate the next detection dropped is a stale review
                # (the row is still there as the audit anchor), never an unknown target.
                raise fail(
                    FACE_MAPPING_STALE,
                    "the candidate set of this group changed",
                    old_face_id=old_face_id,
                    expected_review_revision=int(item["expected_review_revision"]),
                    current_review_revision=int(group["revision"]),
                )
            face = connection.execute(
                "SELECT face_id, asset_id FROM faces WHERE face_id = ?", (new_face_id,)
            ).fetchone()
            if face is None:
                raise fail(
                    FACE_NOT_FOUND,
                    f"face {new_face_id} does not exist",
                    new_face_id=new_face_id,
                    old_face_id=old_face_id,
                )
            if int(face["asset_id"]) != int(asset):
                # ADR-0010 §2.2: a cross-asset pair is never a candidate.
                raise fail(
                    FACE_MAPPING_NOT_FOUND,
                    "this asset does not own that old/new pair",
                    old_face_id=old_face_id,
                    new_face_id=new_face_id,
                    asset_id=int(asset),
                )
            if new_face_id not in candidates:
                raise fail(
                    "VALIDATION_ERROR",
                    "a target must be one of the group's current candidates",
                    reason="unknown_target",
                    old_face_id=old_face_id,
                    new_face_id=new_face_id,
                )
        if int(item["expected_review_revision"]) != int(group["revision"]):
            raise fail(
                FACE_MAPPING_STALE,
                "the review revision of this group is not the expected one",
                old_face_id=old_face_id,
                expected_review_revision=int(item["expected_review_revision"]),
                current_review_revision=int(group["revision"]),
            )
        if str(group["state"]) != GROUP_OPEN:
            raise fail(
                FACE_MAPPING_CONFLICT,
                "this review has already been decided",
                reason="review_already_decided",
                old_face_id=old_face_id,
            )
        if str(group["candidate_state"]) == CANDIDATE_NONE and item["decision"] == DECISION_CONFIRM:
            raise fail(
                "VALIDATION_ERROR",
                "a group without a candidate can only be rejected",
                reason="confirm_requires_targets",
                old_face_id=old_face_id,
            )
        for target in item["targets"]:
            if not target["inherits_person"]:
                continue
            new_face_id = str(target["new_face_id"])
            if new_face_id in inheritors:
                raise fail(
                    "VALIDATION_ERROR",
                    "at most one source old face may give its person to a new face",
                    reason="multiple_inheritors_for_new_face",
                    new_face_id=new_face_id,
                )
            inheritors[new_face_id] = old_face_id
        plans.append({**item, "group": group, "candidates": candidates})

    # ---- transfer matrix (ADR-0010 §4): computed, never guessed ----
    sources_by_new: dict[str, list[str]] = {}
    for plan in plans:
        if plan["decision"] != DECISION_CONFIRM:
            continue
        for target in plan["targets"]:
            sources_by_new.setdefault(str(target["new_face_id"]), []).append(
                str(plan["old_face_id"])
            )
    transfers: dict[str, dict[str, Any]] = {}
    for new_face_id in sorted(sources_by_new):
        sources = sources_by_new[new_face_id]
        mode_now = face_correction.control_mode(connection, new_face_id)
        denied_now = face_correction.active_exclusion_person_ids(connection, new_face_id)
        if mode_now != "AUTO_ALLOWED" or denied_now:
            raise fail(
                FACE_MAPPING_TARGET_CONTROLLED,
                "the target new face already carries a human decision",
                new_face_id=new_face_id,
                old_face_id=sources[0],
            )
        deny: set[int] = set()
        deny_by_source: dict[str, set[int]] = {}
        manual_source = False
        for source in sources:
            own = set(face_correction.active_exclusion_person_ids(connection, source))
            deny_by_source[source] = own
            deny.update(own)
            if face_correction.control_mode(connection, source) != "AUTO_ALLOWED":
                manual_source = True
        person: int | None = None
        inheritor = inheritors.get(new_face_id)
        if inheritor is not None:
            if face_correction.control_mode(connection, inheritor) != "MANUAL_ASSIGNED":
                raise fail(
                    FACE_MAPPING_CONFLICT,
                    "only a manually assigned old face can hand its person over",
                    reason="anchor_has_no_person",
                    old_face_id=inheritor,
                    new_face_id=new_face_id,
                )
            link = connection.execute(
                "SELECT person_id FROM face_person_links WHERE face_id = ? "
                "AND decision = 'CONFIRMED' AND assignment_kind = 'MANUAL'",
                (inheritor,),
            ).fetchone()
            if link is None:
                raise fail(
                    FACE_MAPPING_CONFLICT,
                    "the inheriting old face has no confirmed person",
                    reason="anchor_has_no_person",
                    old_face_id=inheritor,
                    new_face_id=new_face_id,
                )
            person = face_correction.canonical_person(connection, int(link["person_id"]))
            # ADR-0010 §4.2/§4.5: the inheriting face's own denial of the person it hands over
            # is lifted (never copied), while a denial of the same person recorded on *another*
            # source old face is the affirm/deny conflict the whole request must refuse.
            conflicting = any(
                person in own
                for source, own in deny_by_source.items()
                if source != str(inheritor)
            )
            if conflicting:
                raise fail(
                    FACE_MAPPING_CONFLICT,
                    "the same request affirms and denies one person",
                    reason="human_affirm_deny_conflict",
                    old_face_id=inheritor,
                    new_face_id=new_face_id,
                )
            deny.discard(int(person))
        transfers[new_face_id] = {
            "person": person,
            "deny": sorted(deny),
            "conservative": person is None and manual_source,
        }

    # ---- write: one operation, group rows first, then the transferred human facts ----
    any_confirm = any(plan["decision"] == DECISION_CONFIRM for plan in plans)
    operation_type = OPERATION_CONFIRM if any_confirm else OPERATION_REJECT
    new_operation_id = operation_id_for(str(request_id))
    operation_revision = operation_module._next_operation_revision(connection)
    connection.execute(
        "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
        "source_person_ids_json, target_person_id, state, operation_revision, "
        "undo_of_operation_id, undone_by, catalog_filter_revision, created_at, committed_at) "
        "VALUES (?, ?, ?, ?, '[]', NULL, 'COMMITTED', ?, NULL, NULL, 0, ?, ?)",
        (
            str(new_operation_id),
            str(request_id),
            str(digest),
            str(operation_type),
            int(operation_revision),
            int(now_ms),
            int(now_ms),
        ),
    )
    #: ``changed`` is the review-state fact the envelope reports; ``human_changed`` is the
    #: narrower "a control or a denial really moved" fact that ADR-0010 §6 turns into exactly
    #: one ``PERSON_CHANGED`` projection bump. A pure reject/acknowledgement changes the first
    #: and must not move the second.
    changed = False
    human_changed = False
    items: list[dict[str, Any]] = []
    for index, plan in enumerate(plans):
        old_face_id = str(plan["old_face_id"])
        before = group_bundle(connection, old_face_id)
        selected = (
            {str(target["new_face_id"]) for target in plan["targets"]}
            if plan["decision"] == DECISION_CONFIRM
            else set()
        )
        for row in mapping_rows(connection, old_face_id):
            new_face_id = str(row["new_face_id"])
            if str(row["state"]) == STATE_STALE:
                continue
            if new_face_id not in plan["candidates"]:
                continue
            if plan["decision"] == DECISION_CONFIRM:
                state = STATE_CONFIRMED if new_face_id in selected else STATE_REJECTED
            else:
                state = STATE_REJECTED
            inherits = 1 if new_face_id in selected and inheritors.get(new_face_id) == old_face_id else 0
            connection.execute(
                "UPDATE face_redetection_mappings SET state = ?, inherits_person = ?, "
                "last_operation_id = ?, revision = revision + 1, updated_at = ? "
                "WHERE mapping_id = ?",
                (str(state), int(inherits), str(new_operation_id), int(now_ms), str(row["mapping_id"])),
            )
        group_state = GROUP_CONFIRMED if plan["decision"] == DECISION_CONFIRM else GROUP_REJECTED
        connection.execute(
            "UPDATE face_redetection_groups SET state = ?, revision = revision + 1, "
            "last_operation_id = ?, updated_at = ? WHERE old_face_id = ?",
            (str(group_state), str(new_operation_id), int(now_ms), old_face_id),
        )
        after = group_bundle(connection, old_face_id)
        item_changed = bool(_group_differences(before, after))
        changed = changed or item_changed
        connection.execute(
            "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
            "entity_id, entity_key, before_value_json, after_value_json, after_revision) "
            "VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
            (
                str(new_operation_id),
                int(index),
                ENTITY_TYPE_MAPPING_GROUP,
                old_face_id,
                group_bundle_json(before),
                group_bundle_json(after),
                int(after["revision"]),
            ),
        )
        items.append(group_item_projection_live(before, after))
    control_results: list[dict[str, Any]] = []
    for new_face_id in sorted(transfers):
        transfer = transfers[new_face_id]
        before = face_correction.read_control_bundle(connection, new_face_id)
        revision = face_correction.logical_control_revision(connection, new_face_id)
        if transfer["person"] is not None:
            face_correction.apply_one(
                connection,
                item={
                    "face_id": new_face_id,
                    "action": face_correction.ACTION_ASSIGN,
                    "target_person_id": int(transfer["person"]),
                    "expected_revision": int(revision),
                },
                operation_id=str(new_operation_id),
                now_ms=int(now_ms),
            )
        elif transfer["conservative"]:
            face_correction.apply_one(
                connection,
                item={
                    "face_id": new_face_id,
                    "action": face_correction.ACTION_UNASSIGN,
                    "target_person_id": None,
                    "expected_revision": int(revision),
                },
                operation_id=str(new_operation_id),
                now_ms=int(now_ms),
            )
        for person_id in transfer["deny"]:
            face_correction._write_exclusion(
                connection,
                face_id=new_face_id,
                person_id=int(person_id),
                active=True,
                operation_id=str(new_operation_id),
                now_ms=int(now_ms),
            )
        after = face_correction.read_control_bundle(connection, new_face_id)
        answer = face_correction.item_answer(new_face_id, before, after)
        if answer["changed"]:
            human_changed = True
            control_results.append({"face_id": new_face_id, "before": before, "after": after})
    for offset, result in enumerate(control_results):
        connection.execute(
            "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
            "entity_id, entity_key, before_value_json, after_value_json, after_revision) "
            "VALUES (?, ?, 'FACE_CONTROL', NULL, ?, ?, ?, ?)",
            (
                str(new_operation_id),
                int(len(plans) + offset),
                str(result["face_id"]),
                face_correction.control_bundle_json(result["before"]),
                face_correction.control_bundle_json(result["after"]),
                int(result["after"]["control"]["revision"]),
            ),
        )
        items.append(
            face_correction.item_answer(
                str(result["face_id"]), result["before"], result["after"]
            )
        )
    # ADR-0010 §5.3/§6: releasing a hold puts the asset back in the durable association
    # queue, and only a *real* human fact change moves the filter revision.
    requeue_asset(connection, asset_id=int(asset), now_ms=int(now_ms))
    changed = changed or human_changed
    if human_changed:
        filter_revision = operation_module.publish_derived(
            connection,
            faces=[
                str(plan["old_face_id"]) for plan in plans
            ]
            + [str(result["face_id"]) for result in control_results],
            now_ms=int(now_ms),
            # ADR-0010 §6: a mapping decision never writes the image vector ``index_outbox``.
            refresh=None,
        )
    else:
        filter_revision = revision_module.read_filter_revision(connection)
    connection.execute(
        "UPDATE person_operations SET catalog_filter_revision = ? WHERE operation_id = ?",
        (int(filter_revision), str(new_operation_id)),
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "operation_id": str(new_operation_id),
        "operation_type": str(operation_type),
        "operation_revision": int(operation_revision),
        "status": "COMMITTED",
        "undo_of_operation_id": None,
        "changed": bool(changed),
        "catalog_filter_revision": int(filter_revision),
        "replayed": False,
        "items": items,
    }


def group_item_projection_live(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "entity_type": ENTITY_TYPE_MAPPING_GROUP,
        "old_face_id": str(after.get("old_face_id") or before.get("old_face_id")),
        "changed": bool(_group_differences(before, after)),
        "state": str(after.get("state")),
        "revision": int(after.get("revision") or 0),
        "candidate_state": str(after.get("candidate_state")),
        "candidates": [
            {
                "new_face_id": str(item["new_face_id"]),
                "state": str(item["state"]),
                "revision": int(item["revision"]),
                "inherits_person": bool(item["inherits_person"]),
            }
            for item in after.get("candidates", [])
        ],
    }


# -------------------------------------------------------------------------- compensation
def _eligibility_conflicts(connection: sqlite3.Connection, operation: Any) -> list[dict[str, Any]]:
    """A safe, non-empty ``conflicts`` list for a refusal the frozen shape always carries.

    ``FACE_MAPPING_OPERATION_NOT_UNDOABLE`` requires at least one conflict entry, even when
    the refusal is "already reversed or revision changed". Every mapping operation this build
    writes has a ``FACE_MAPPING_GROUP`` item, so its ``entity_key`` is the honest anchor; the
    ``FACE_CONTROL`` fallback covers a hand-crafted row and the all-zero id the empty case.
    """
    row = connection.execute(
        "SELECT entity_key FROM person_operation_items WHERE operation_id = ? "
        "AND entity_type = ? ORDER BY item_index LIMIT 1",
        (str(operation["operation_id"]), ENTITY_TYPE_MAPPING_GROUP),
    ).fetchone()
    if row is None:
        row = connection.execute(
            "SELECT entity_key FROM person_operation_items WHERE operation_id = ? "
            "AND entity_type = ? ORDER BY item_index LIMIT 1",
            (str(operation["operation_id"]), contracts.ENTITY_TYPE_FACE_CONTROL),
        ).fetchone()
    entity_key = "0" * 64 if row is None or row["entity_key"] is None else str(row["entity_key"])
    return [
        {
            "entity_type": ENTITY_TYPE_MAPPING_GROUP,
            "entity_key": entity_key,
            "reason": "status",
        }
    ]


def undo_face_mapping_operation(
    connection: sqlite3.Connection,
    *,
    original: Any,
    request_id: str,
    digest: str,
    expected_operation_revision: int,
    now_ms: int,
    refresh: revision_module.ProjectionRefresh | None = None,
) -> dict[str, Any]:
    """Compensate one committed mapping decision, or refuse the whole request."""
    from photo_search.persons import face_correction
    from photo_search.persons import operations as operation_module

    original_id = str(original["operation_id"])
    if (
        str(original["type"]) not in MAPPING_OPERATION_TYPES
        or str(original["state"]) != "COMMITTED"
        or original["undone_by"] is not None
        or int(original["operation_revision"]) != int(expected_operation_revision)
    ):
        raise fail(
            FACE_MAPPING_OPERATION_NOT_UNDOABLE,
            "this mapping decision is already reversed or its revision changed",
            operation_id=original_id,
            expected_operation_revision=int(expected_operation_revision),
            current_operation_revision=int(original["operation_revision"]),
            status=str(original["state"]),
            conflicts=_eligibility_conflicts(connection, original),
        )
    rows = list(
        connection.execute(
            "SELECT item_index, entity_type, entity_id, entity_key, before_value_json, "
            "after_value_json, after_revision FROM person_operation_items "
            "WHERE operation_id = ? ORDER BY item_index",
            (original_id,),
        ).fetchall()
    )
    conflicts: list[dict[str, Any]] = []
    pending_groups: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    pending_controls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for row in rows:
        entity_type = str(row["entity_type"])
        if entity_type == ENTITY_TYPE_MAPPING_GROUP:
            old_face_id = str(row["entity_key"])
            recorded_after = load_group_bundle(row["after_value_json"])
            recorded_before = load_group_bundle(row["before_value_json"])
            live = group_row(connection, old_face_id)
            if live is None:
                conflicts.append(
                    {
                        "entity_type": ENTITY_TYPE_MAPPING_GROUP,
                        "entity_key": old_face_id,
                        "reason": "group_missing",
                    }
                )
                continue
            live_bundle = group_bundle(connection, old_face_id)
            differences = _group_differences(live_bundle, recorded_after)
            if differences:
                conflicts.append(
                    {
                        "entity_type": ENTITY_TYPE_MAPPING_GROUP,
                        "entity_key": old_face_id,
                        "reason": ",".join(differences),
                    }
                )
                continue
            pending_groups.append((old_face_id, live_bundle, recorded_before))
        elif entity_type == contracts.ENTITY_TYPE_FACE_CONTROL:
            face_id = str(row["entity_key"])
            recorded_after = face_correction.load_control_bundle(row["after_value_json"])
            recorded_before = face_correction.load_control_bundle(row["before_value_json"])
            if connection.execute(
                "SELECT 1 FROM faces WHERE face_id = ?", (face_id,)
            ).fetchone() is None:
                conflicts.append(
                    {
                        "entity_type": ENTITY_TYPE_MAPPING_GROUP,
                        "entity_key": face_id,
                        "reason": "face_missing",
                    }
                )
                continue
            live_control = face_correction.read_control_bundle(connection, face_id)
            differences = face_correction.control_bundle_differences(
                live_control, recorded_after
            )
            if differences:
                conflicts.append(
                    {
                        "entity_type": ENTITY_TYPE_MAPPING_GROUP,
                        "entity_key": face_id,
                        "reason": ",".join(differences),
                    }
                )
                continue
            pending_controls.append((face_id, live_control, recorded_before))
        else:
            raise fail(
                FACE_MAPPING_OPERATION_NOT_UNDOABLE,
                "this operation mixes mapping and other items",
                operation_id=original_id,
                conflicts=_eligibility_conflicts(connection, original),
            )
    if conflicts:
        raise fail(
            FACE_MAPPING_OPERATION_NOT_UNDOABLE,
            "the mapping was edited after this decision",
            operation_id=original_id,
            conflicts=conflicts[:64],
        )
    new_operation_id = operation_id_for(str(request_id))
    new_operation_revision = operation_module._next_operation_revision(connection)
    connection.execute(
        "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
        "source_person_ids_json, target_person_id, state, operation_revision, "
        "undo_of_operation_id, undone_by, catalog_filter_revision, created_at, committed_at) "
        "VALUES (?, ?, ?, 'UNDO', '[]', NULL, 'COMMITTED', ?, ?, NULL, 0, ?, ?)",
        (
            str(new_operation_id),
            str(request_id),
            str(digest),
            int(new_operation_revision),
            original_id,
            int(now_ms),
            int(now_ms),
        ),
    )
    changed = False
    human_changed = False
    items: list[dict[str, Any]] = []
    for index, (old_face_id, live, recorded_before) in enumerate(pending_groups):
        for row in mapping_rows(connection, old_face_id):
            wanted = next(
                (
                    item
                    for item in recorded_before.get("candidates", [])
                    if str(item["new_face_id"]) == str(row["new_face_id"])
                ),
                None,
            )
            if wanted is None:
                continue
            state = str(wanted["state"])
            inherits = 1 if wanted["inherits_person"] else 0
            revision = max(int(row["revision"]), int(wanted["revision"])) + 1
            if (
                str(row["state"]) == state
                and int(row["inherits_person"]) == inherits
              ):
                connection.execute(
                    "UPDATE face_redetection_mappings SET revision = ?, updated_at = ? "
                    "WHERE mapping_id = ?",
                    (int(revision), int(now_ms), str(row["mapping_id"])),
                )
            else:
                connection.execute(
                    "UPDATE face_redetection_mappings SET state = ?, inherits_person = ?, "
                    "revision = ?, updated_at = ? WHERE mapping_id = ?",
                    (str(state), int(inherits), int(revision), int(now_ms), str(row["mapping_id"])),
                )
        restored_revision = max(
            int(live.get("revision") or 0), int(recorded_before.get("revision") or 0)
        ) + 1
        connection.execute(
            "UPDATE face_redetection_groups SET state = ?, candidate_state = ?, revision = ?, "
            "last_operation_id = ?, updated_at = ? WHERE old_face_id = ?",
            (
                str(recorded_before["state"]),
                str(recorded_before["candidate_state"]),
                int(restored_revision),
                str(new_operation_id),
                int(now_ms),
                str(old_face_id),
            ),
        )
        after = group_bundle(connection, old_face_id)
        item_changed = bool(_group_differences(live, after))
        changed = changed or item_changed
        connection.execute(
            "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
            "entity_id, entity_key, before_value_json, after_value_json, after_revision) "
            "VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
            (
                str(new_operation_id),
                int(index),
                ENTITY_TYPE_MAPPING_GROUP,
                str(old_face_id),
                group_bundle_json(live),
                group_bundle_json(after),
                int(after["revision"]),
            ),
        )
        items.append(group_item_projection_live(live, after))
    control_results: list[dict[str, Any]] = []
    for face_id, live_control, recorded_before in pending_controls:
        if face_correction.restore_control_bundle(
            connection,
            face_id=str(face_id),
            live=live_control,
            wanted=recorded_before,
            operation_id=str(new_operation_id),
            now_ms=int(now_ms),
        ):
            human_changed = True
        after = face_correction.read_control_bundle(connection, str(face_id))
        control_results.append(
            {"face_id": str(face_id), "before": live_control, "after": after}
        )
    for offset, result in enumerate(control_results):
        connection.execute(
            "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
            "entity_id, entity_key, before_value_json, after_value_json, after_revision) "
            "VALUES (?, ?, 'FACE_CONTROL', NULL, ?, ?, ?, ?)",
            (
                str(new_operation_id),
                int(len(pending_groups) + offset),
                str(result["face_id"]),
                face_correction.control_bundle_json(result["before"]),
                face_correction.control_bundle_json(result["after"]),
                int(result["after"]["control"]["revision"]),
            ),
        )
        items.append(
            face_correction.item_answer(
                str(result["face_id"]), result["before"], result["after"]
            )
        )
    connection.execute(
        "UPDATE person_operations SET state = 'UNDONE', undone_by = ? WHERE operation_id = ?",
        (str(new_operation_id), original_id),
    )
    # ADR-0010 §5.3: ``undo -> OPEN`` reapplies the hold and keeps the asset queued, so the
    # very next association pass skips the restored candidate.
    for old_face_id, _live, _before in pending_groups:
        group = group_row(connection, old_face_id)
        if group is not None and str(group["state"]) == GROUP_OPEN:
            requeue_asset(connection, asset_id=int(group["asset_id"]), now_ms=int(now_ms))
    changed = changed or human_changed
    if human_changed:
        filter_revision = operation_module.publish_derived(
            connection,
            faces=[str(entry[0]) for entry in pending_groups]
            + [str(entry[0]) for entry in pending_controls],
            now_ms=int(now_ms),
            refresh=None,
        )
    else:
        filter_revision = revision_module.read_filter_revision(connection)
    connection.execute(
        "UPDATE person_operations SET catalog_filter_revision = ? WHERE operation_id = ?",
        (int(filter_revision), str(new_operation_id)),
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "operation_id": str(new_operation_id),
        "operation_type": "UNDO",
        "operation_revision": int(new_operation_revision),
        "status": "COMMITTED",
        "undo_of_operation_id": original_id,
        "changed": bool(changed),
        "catalog_filter_revision": int(filter_revision),
        "replayed": False,
        "items": items,
    }


def mapping_operation_result(connection: sqlite3.Connection, operation: Any) -> dict[str, Any]:
    """Rebuild a recorded mapping operation (an idempotent replay or a read echo)."""
    rows = list(
        connection.execute(
            "SELECT item_index, entity_type, entity_id, entity_key, before_value_json, "
            "after_value_json, after_revision FROM person_operation_items "
            "WHERE operation_id = ? ORDER BY item_index",
            (str(operation["operation_id"]),),
        ).fetchall()
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        if str(row["entity_type"]) == ENTITY_TYPE_MAPPING_GROUP:
            items.append(group_item_projection(row))
        else:
            from photo_search.persons import face_correction

            items.append(face_correction.recorded_item_projection(row))
    return {
        "contract_version": CONTRACT_VERSION,
        "operation_id": str(operation["operation_id"]),
        "operation_type": str(operation["type"]),
        "operation_revision": int(operation["operation_revision"]),
        "status": "UNDONE" if str(operation["state"]) == "UNDONE" else "COMMITTED",
        "undo_of_operation_id": (
            None if operation["undo_of_operation_id"] is None else str(operation["undo_of_operation_id"])
        ),
        "changed": any(bool(item["changed"]) for item in items),
        "catalog_filter_revision": int(operation["catalog_filter_revision"]),
        "replayed": True,
        "items": items,
    }


def operation_asset_id(connection: sqlite3.Connection, operation_id: str) -> int:
    """The asset a recorded mapping operation belongs to (ADR-0010 §8.2 ``asset_id``).

    A mapping operation always carries at least one ``FACE_MAPPING_GROUP`` item, so its asset
    is resolvable from the recorded row alone; the ``FACE_CONTROL`` fallback covers a
    hand-crafted log and the ``MIN(asset_id)`` last resort keeps the closed ``details`` shape
    well formed instead of echoing an operation id a caller must not see.
    """
    row = connection.execute(
        "SELECT g.asset_id AS asset_id FROM person_operation_items AS i "
        "JOIN face_redetection_groups AS g ON g.old_face_id = i.entity_key "
        "WHERE i.operation_id = ? AND i.entity_type = ? ORDER BY i.item_index LIMIT 1",
        (str(operation_id), ENTITY_TYPE_MAPPING_GROUP),
    ).fetchone()
    if row is not None:
        return int(row["asset_id"])
    row = connection.execute(
        "SELECT f.asset_id AS asset_id FROM person_operation_items AS i "
        "JOIN faces AS f ON f.face_id = i.entity_key "
        "WHERE i.operation_id = ? AND i.entity_type = ? ORDER BY i.item_index LIMIT 1",
        (str(operation_id), contracts.ENTITY_TYPE_FACE_CONTROL),
    ).fetchone()
    if row is not None:
        return int(row["asset_id"])
    operation = connection.execute(
        "SELECT type, undo_of_operation_id FROM person_operations WHERE operation_id = ?",
        (str(operation_id),),
    ).fetchone()
    if operation is not None and str(operation["type"]) == "UNDO" and operation["undo_of_operation_id"]:
        parent = str(operation["undo_of_operation_id"])
        if parent != str(operation_id):
            return operation_asset_id(connection, parent)
    row = connection.execute("SELECT MIN(asset_id) AS asset_id FROM assets").fetchone()
    return 1 if row is None or row["asset_id"] is None else int(row["asset_id"])


__all__ = [
    "CANDIDATE_AMBIGUOUS",
    "CANDIDATE_NONE",
    "CANDIDATE_UNIQUE_HIGH",
    "CANDIDATE_UNIQUE_WEAK",
    "CONTRACT_VERSION",
    "DECISION_CONFIRM",
    "DECISION_REJECT",
    "ENDPOINT_DECISIONS",
    "ENTITY_TYPE_MAPPING_GROUP",
    "FACE_THUMBNAIL_TEMPLATE",
    "GROUP_CONFIRMED",
    "GROUP_OPEN",
    "GROUP_REJECTED",
    "MAPPING_OPERATION_TYPES",
    "MAPPING_STATES",
    "MATCH_HIGH",
    "MATCH_IOU_HIGH",
    "MATCH_IOU_MIN",
    "MATCH_LANDMARK_HIGH",
    "MATCH_LANDMARK_MAX",
    "MATCH_WEAK",
    "OPERATION_CONFIRM",
    "OPERATION_REJECT",
    "STATE_CONFIRMED",
    "STATE_REJECTED",
    "STATE_STALE",
    "STATE_SUGGESTED",
    "candidate_state_for",
    "check_asset_id",
    "check_contract_version",
    "check_decision",
    "check_face_id",
    "check_review_revision",
    "decide_face_mappings",
    "generate_candidates_for_asset",
    "group_bundle",
    "group_bundle_json",
    "group_detail",
    "group_item_projection",
    "group_row",
    "group_summary",
    "hold_face_ids",
    "human_knowledge_face_ids",
    "intersection_over_union",
    "landmark_ratio",
    "live_candidate_ids",
    "live_candidate_rows",
    "load_group_bundle",
    "mapping_id_for",
    "mapping_operation_result",
    "mapping_rows",
    "match_state_for",
    "operation_asset_id",
    "requeue_asset",
    "retire_mapping_faces",
    "undo_face_mapping_operation",
]
