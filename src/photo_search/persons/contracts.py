"""The frozen P3-03 person protocol: names, keys, envelopes and limits (ADR-0006).

Everything a caller can observe about a person operation is derived here and nowhere else:

* the versioned ``name_key`` (``nfc1:`` plus the NFC/whitespace-normalised display text),
* the deterministic ``operation_id`` of an idempotency key,
* the canonical JSON form used for both the payload hash and the stored before/after
  bundles, and
* the frozen error codes of ADR-0006 §9.

The display name is *not* an identifier: it never replaces ``person_id`` and it is never a
foreign key. Case is preserved and never folded - ``Alice`` and ``alice`` are two different
names on purpose (ADR-0006 §2) - and the placeholder text the UI shows for an unnamed group
("未命名人物 001") is never written to this database and never takes part in a match.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from photo_search.catalog.errors import (
    FACE_IDEMPOTENCY_CONFLICT,
    MAX_PERSON_BATCH_SIZE,
    PERSON_BATCH_TOO_LARGE,
    PERSON_IDEMPOTENCY_CONFLICT,
    PERSON_NAME_INVALID,
    PERSON_NOT_FOUND,
    PERSON_OPERATION_NOT_UNDOABLE,
    PERSON_REDIRECT_CYCLE,
    PERSON_REVISION_CONFLICT,
    CatalogError,
)

#: The person envelope version. ADR-0006 freezes the shapes, not a companion JSON schema,
#: so this literal is the whole machine-readable surface of the person protocol.
CONTRACT_VERSION = "p3-person-contracts.v1"

#: ADR-0006 §2. A name_key generation bump is a deliberate, reviewable act: the prefix is
#: part of the stored key, so an old key can never be mistaken for a new one.
NAME_KEY_VERSION = 1
NAME_KEY_PREFIX = f"nfc{NAME_KEY_VERSION}:"

#: Implementation bound, not a semantic rule: a display name is a short label, and the
#: column has to stay indexable. It mirrors the place-name bound so the two local curations
#: behave alike; it is documented as a bound this build chose (ADR-0006 fixes no length).
MAX_PERSON_NAME_CHARS = 200
MAX_REQUEST_ID_CHARS = 64
MAX_MERGE_SOURCES = int(MAX_PERSON_BATCH_SIZE)

OPERATION_TYPES = ("RENAME", "MERGE_BY_NAME", "MERGE", "BATCH_MERGE", "UNDO")
#: The operation types this build can perform, i.e. everything an undo may compensate.
WRITE_OPERATION_TYPES = ("RENAME", "MERGE_BY_NAME", "MERGE", "BATCH_MERGE")

#: ADR-0007 §2/§7: the face-level types of the *same* operation log. A single-face request
#: derives its type from the action; a batch is always ``BATCH_FACE_EDIT`` even when it
#: carries one item, because the type describes the request entry point. ``UNDO`` stays the
#: shared compensation type.
FACE_OPERATION_TYPES = (
    "FACE_ASSIGN",
    "FACE_UNASSIGN",
    "FACE_RESTORE_AUTO",
    "BATCH_FACE_EDIT",
)
#: ADR-0008 §5: splitting a person group is the third writer of the *same* operation log.
#: It brings one operation type and one item shape, so it gets its own tuple exactly like
#: the face family does - ``OPERATION_TYPES`` stays the frozen P3-03 person vocabulary.
SPLIT_OPERATION_TYPES = ("SPLIT",)
#: ADR-0010 §5.1: a decision on a face-redetection mapping is the fourth writer of the same
#: log. It brings the two ``FACE_MAPPING_*`` types and the ``FACE_MAPPING_GROUP`` item shape,
#: so it gets its own tuple for exactly the same reason the face and split families do.
MAPPING_OPERATION_TYPES = ("FACE_MAPPING_CONFIRM", "FACE_MAPPING_REJECT")
# P4 person inbox creation has history and request identity, but is never undoable.
CREATE_PERSON_OPERATION_TYPES = ("CREATE_PERSON_FROM_FACE",)
#: Everything the shared ``/person-operations/{id}/undo`` endpoint may compensate.
OPERATION_TYPE_SPLIT = SPLIT_OPERATION_TYPES[0]
UNDOABLE_OPERATION_TYPES = WRITE_OPERATION_TYPES + FACE_OPERATION_TYPES

#: ADR-0007 §2: the two mutually exclusive shapes of one operation item. A person bundle is
#: keyed by an integer ``person_id``; a face control is keyed by its ``face_id`` text.
ENTITY_TYPE_PERSON_BUNDLE = "PERSON_BUNDLE"
ENTITY_TYPE_FACE_CONTROL = "FACE_CONTROL"
#: ADR-0008 §5: a split group *reads* like a person bundle (a positive integer ``entity_id``)
#: but records a group that a transaction may have just created, so it is its own type.
ENTITY_TYPE_SPLIT_GROUP = "SPLIT_GROUP"
#: ADR-0010 §5.1: a mapping group is keyed by the old ``face_id`` (text ``entity_key``), the
#: same shape a ``FACE_CONTROL`` item uses and mutually exclusive with the integer key.
ENTITY_TYPE_MAPPING_GROUP = "FACE_MAPPING_GROUP"

CONTROL_MODES = ("AUTO_ALLOWED", "MANUAL_ASSIGNED", "MANUAL_UNASSIGNED")

#: A SUGGESTED link that a merge refused to make effective. They are exactly the two
#: reasons ``face_summary.pending_confirmation`` counts (ADR-0006 §6/§9).
REASON_MANUAL_UNASSIGNED = "MANUAL_UNASSIGNED"
REASON_HUMAN_CONFLICT = "HUMAN_CONFLICT"
PENDING_CONFIRMATION_REASONS = (REASON_MANUAL_UNASSIGNED, REASON_HUMAN_CONFLICT)

#: The CatalogFilterRevision cause of every person authority change (ADR-0006 §8).
CAUSE_PERSON_CHANGED = "PERSON_CHANGED"

_WHITESPACE = re.compile(r"\s+")


def fail(code: str, message: str, **details: Any) -> CatalogError:
    """One place to build a structured person failure."""
    return CatalogError(
        code, message, details={key: value for key, value in details.items()} or None
    )


def canonical_json(payload: Mapping[str, Any]) -> str:
    """Deterministic rendering used for payload hashes and stored before/after bundles."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def operation_id_for(request_id: str) -> str:
    """ADR-0006 §7: the operation id is derived from its idempotency key."""
    value = f"person-operation\0{str(request_id)}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def normalize_person_name(value: Any) -> tuple[str, str]:
    """Return ``(display_name, name_key)`` for one submitted name.

    The order is fixed by ADR-0006 §2: the value must be a string, it is normalised to
    Unicode NFC, every run of Unicode whitespace collapses to one ASCII space, and the
    result is trimmed. The display text keeps its case (no casefold, no NFKC, no pinyin, no
    simplified/traditional folding, no synonym or alias matching) and the key is the prefix
    plus exactly that text.
    """
    if not isinstance(value, str):
        raise fail(PERSON_NAME_INVALID, "a person name must be a string", field="name")
    text = _WHITESPACE.sub(" ", unicodedata.normalize("NFC", value)).strip()
    if not text:
        raise fail(PERSON_NAME_INVALID, "a person name must not be empty", field="name")
    if len(text) > MAX_PERSON_NAME_CHARS:
        raise fail(
            PERSON_NAME_INVALID,
            f"a person name must be at most {MAX_PERSON_NAME_CHARS} characters",
            field="name",
            length=len(text),
        )
    return text, f"{NAME_KEY_PREFIX}{text}"


def name_key_for(display_name: str) -> str:
    """The stored key of an already-normalised display name."""
    return f"{NAME_KEY_PREFIX}{str(display_name)}"


def check_request_id(value: Any) -> str:
    """The caller's idempotency key: bounded printable ASCII, never a path."""
    if not isinstance(value, str):
        raise fail("VALIDATION_ERROR", "request_id must be a string", field="request_id")
    text = value.strip()
    if (
        not text
        or len(text) > MAX_REQUEST_ID_CHARS
        or not all(0x21 <= ord(char) <= 0x7E for char in text)
    ):
        raise fail(
            "VALIDATION_ERROR",
            f"request_id must be 1..{MAX_REQUEST_ID_CHARS} printable ASCII characters",
            field="request_id",
        )
    return text


def check_person_id(value: Any, *, field: str = "person_id") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or int(value) < 1:
        raise fail("VALIDATION_ERROR", f"{field} must be a positive integer", field=field)
    return int(value)


def check_expected_revision(value: Any, *, field: str = "expected_revision") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or int(value) < 0:
        raise fail("VALIDATION_ERROR", f"{field} must be an integer >= 0", field=field)
    return int(value)


def check_merge_sources(value: Any) -> list[dict[str, int]]:
    """Normalise the explicit ``sources`` list of one merge (ADR-0006 §5).

    The list is 1..500 entries, every entry names a distinct *requested* person id and its
    expected revision, and the result is sorted by that requested id so the payload hash of
    the same client intent never depends on the order the UI happened to send.
    """
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise fail("VALIDATION_ERROR", "sources must be a list", field="sources")
    if not value:
        raise fail("VALIDATION_ERROR", "a merge needs at least one source", field="sources")
    if len(value) > MAX_MERGE_SOURCES:
        raise fail(
            PERSON_BATCH_TOO_LARGE,
            f"a merge carries at most {MAX_MERGE_SOURCES} sources",
            field="sources",
            max_batch_size=MAX_MERGE_SOURCES,
        )
    normalized: list[dict[str, int]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise fail("VALIDATION_ERROR", "each source must be an object", index=index)
        person_id = check_person_id(item.get("person_id"), field="person_id")
        expected = check_expected_revision(
            item.get("expected_revision"), field="expected_revision"
        )
        normalized.append({"person_id": person_id, "expected_revision": expected})
    seen: set[int] = set()
    duplicates: list[int] = []
    for item in normalized:
        if item["person_id"] in seen:
            duplicates.append(int(item["person_id"]))
        seen.add(int(item["person_id"]))
    if duplicates:
        raise fail(
            "VALIDATION_ERROR",
            "a merge must not name the same source person twice",
            field="sources",
            person_ids=sorted(set(duplicates)),
        )
    return sorted(normalized, key=lambda entry: int(entry["person_id"]))


def read_request_state(operation: Mapping[str, Any]) -> dict[str, Any]:
    """The state projection every operation response carries."""
    return {
        "operation_id": str(operation["operation_id"]),
        "operation_type": str(operation["type"]),
        "operation_revision": int(operation["operation_revision"]),
        "status": "UNDONE" if str(operation["state"]) == "UNDONE" else "COMMITTED",
        "undo_of_operation_id": (
            None
            if operation["undo_of_operation_id"] is None
            else str(operation["undo_of_operation_id"])
        ),
        "catalog_filter_revision": int(operation["catalog_filter_revision"]),
    }


def person_view(
    row: Mapping[str, Any],
    *,
    requested_person_id: int,
    canonical_person_id: int,
    aliases: Sequence[Mapping[str, Any]] = (),
    face_count: int = 0,
    asset_count: int = 0,
) -> dict[str, Any]:
    """The frozen read projection of one canonical person (ADR-0006 §5).

    An old id is answered with ``requested_person_id``/``canonical_person_id``/``redirected``
    instead of an HTTP redirect, and nothing here is a path, a face box, a landmark, a vector
    or a similarity of an individual pair.
    """
    canonical = int(canonical_person_id)
    requested = int(requested_person_id)
    return {
        "contract_version": CONTRACT_VERSION,
        "requested_person_id": requested,
        "canonical_person_id": canonical,
        "redirected": canonical != requested,
        "person_id": canonical,
        "name": None if row["name"] is None else str(row["name"]),
        "avatar_face_id": (
            None if row["avatar_face_id"] is None else str(row["avatar_face_id"])
        ),
        "revision": int(row["revision"]),
        "aliases": [
            {
                "alias": str(alias["alias"]),
                "normalized_alias": str(alias["normalized_alias"]),
            }
            for alias in aliases
        ],
        "face_count": int(face_count),
        "asset_count": int(asset_count),
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
    }


#: Re-exported so a caller of this module never has to import the error module for the codes
#: ADR-0006 §9 freezes. Keeping them in one place makes the mapping auditable in one diff.
__all__ = [
    "CONTRACT_VERSION",
    "CAUSE_PERSON_CHANGED",
    "CONTROL_MODES",
    "CREATE_PERSON_OPERATION_TYPES",
    "ENTITY_TYPE_FACE_CONTROL",
    "ENTITY_TYPE_MAPPING_GROUP",
    "ENTITY_TYPE_PERSON_BUNDLE",
    "ENTITY_TYPE_SPLIT_GROUP",
    "FACE_IDEMPOTENCY_CONFLICT",
    "FACE_OPERATION_TYPES",
    "MAX_MERGE_SOURCES",
    "MAX_PERSON_NAME_CHARS",
    "MAX_REQUEST_ID_CHARS",
    "MAPPING_OPERATION_TYPES",
    "NAME_KEY_PREFIX",
    "NAME_KEY_VERSION",
    "OPERATION_TYPES",
    "OPERATION_TYPE_SPLIT",
    "PENDING_CONFIRMATION_REASONS",
    "PERSON_BATCH_TOO_LARGE",
    "PERSON_IDEMPOTENCY_CONFLICT",
    "PERSON_NAME_INVALID",
    "PERSON_NOT_FOUND",
    "PERSON_OPERATION_NOT_UNDOABLE",
    "PERSON_REDIRECT_CYCLE",
    "PERSON_REVISION_CONFLICT",
    "REASON_HUMAN_CONFLICT",
    "REASON_MANUAL_UNASSIGNED",
    "SPLIT_OPERATION_TYPES",
    "UNDOABLE_OPERATION_TYPES",
    "WRITE_OPERATION_TYPES",
    "canonical_json",
    "check_expected_revision",
    "check_merge_sources",
    "check_person_id",
    "check_request_id",
    "fail",
    "name_key_for",
    "normalize_person_name",
    "operation_id_for",
    "payload_sha256",
    "person_view",
    "read_request_state",
]
