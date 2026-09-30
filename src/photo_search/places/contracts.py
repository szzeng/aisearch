"""Closed enums, shapes and small helpers of the frozen ADR-0005 place protocol.

ADR-0005 is authoritative; the companion JSON Schema is its derivative. Nothing here
invents a status: every tuple below is exactly the enumeration the ADR freezes for the
``p2-place-contracts.v1`` envelope, so a drift is a one-line diff instead of a silent
new value in a response body.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

from photo_search.catalog.errors import INVALID_PLACE_FILTER, CatalogError

CONTRACT_VERSION = "p2-place-contracts.v1"
DIRECTORY_CONTRACT_VERSION = "place-geography.v2"

PLACE_KINDS = ("country", "province", "city", "district", "attraction", "custom")
PLACE_LIFECYCLE = ("ACTIVE", "RETIRED")
PLACE_ORIGINS = ("USER", "DATASET")
ALIAS_SOURCES = ("USER", "DATASET")

ASSIGNMENT_MODES = ("AUTO_ALLOWED", "MANUAL_ASSIGNED", "MANUAL_UNASSIGNED")
ASSIGNMENT_ORIGINS = ("NONE", "MANUAL", "CONFIRMED_SUGGESTION")
ASSIGNMENT_ACTIONS = ("ASSIGN", "UNASSIGN", "RESTORE_AUTO")
ASSIGNMENT_RECORD_STATES = ("ACTIVE", "SUPERSEDED")
ACTION_BY_BATCH_OPERATION = {
    "BATCH_ASSIGN": "ASSIGN",
    "BATCH_UNASSIGN": "UNASSIGN",
    "BATCH_RESTORE_AUTO": "RESTORE_AUTO",
}
OPERATION_BY_ACTION = {
    "ASSIGN": "ASSIGN",
    "UNASSIGN": "UNASSIGN",
    "RESTORE_AUTO": "RESTORE_AUTO",
}

PLACE_STATES = ("any", "assigned", "unassigned", "manual_unassigned", "suggested")

GEO_STATUSES = ("VALID", "ABSENT", "INVALID", "UNSUPPORTED_CRS", "SOURCE_UNAVAILABLE")
DATUM_STATUSES = ("WGS84", "ASSUMED_WGS84", "UNSUPPORTED", "ABSENT")

MAX_PLACE_NAME_CHARS = 200
MAX_ALIASES_PER_PLACE = 32
MAX_REQUEST_ID_CHARS = 64

#: The API envelope code for a well-formed request that is not acceptable. It is the same
#: literal the transport layer uses, so a place validation failure never needs a new code.
VALIDATION_ERROR = "VALIDATION_ERROR"

_WHITESPACE = re.compile(r"\s+")


def fail(code: str, message: str, **details: Any) -> CatalogError:
    """One place to build a structured place failure."""
    return CatalogError(code, message, details={key: value for key, value in details.items()} or None)


def invalid(
    message: str,
    *,
    code: str = INVALID_PLACE_FILTER,
    **details: Any,
) -> CatalogError:
    return fail(code, message, **details)


def canonical_json(payload: Mapping[str, Any]) -> str:
    """Deterministic rendering used for request payload hashes and stored snapshots."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def normalize_name(value: Any, *, field: str = "name") -> str:
    """The comparison form of a name/alias: NFKC, collapsed whitespace, case-folded.

    The display spelling is preserved separately; only this key is used for alias
    uniqueness and name search, so ``  West   Lake `` and ``west lake`` collide while
    ``西湖`` keeps its own identity.
    """
    if not isinstance(value, str):
        raise invalid(f"{field} must be a string", code=VALIDATION_ERROR)
    text = unicodedata.normalize("NFKC", value).strip()
    text = _WHITESPACE.sub(" ", text)
    if not text:
        raise invalid(f"{field} must not be empty", code=VALIDATION_ERROR)
    if len(text) > MAX_PLACE_NAME_CHARS:
        raise invalid(
            f"{field} must be at most {MAX_PLACE_NAME_CHARS} characters",
            code=VALIDATION_ERROR,
            length=len(text),
        )
    return text.casefold()


def display_name(value: Any, *, field: str = "name") -> str:
    """The display spelling of a name: NFC and whitespace-collapsed, never case-folded.

    NFC (not NFKC) keeps a name the user typed - full-width parentheses, half-width kana -
    exactly as written; the compatibility-folded form exists only in ``normalize_name``.
    """
    if not isinstance(value, str):
        raise invalid(f"{field} must be a string", code=VALIDATION_ERROR)
    text = unicodedata.normalize("NFC", value).strip()
    text = _WHITESPACE.sub(" ", text)
    if not text:
        raise invalid(f"{field} must not be empty", code=VALIDATION_ERROR)
    if len(text) > MAX_PLACE_NAME_CHARS:
        raise invalid(
            f"{field} must be at most {MAX_PLACE_NAME_CHARS} characters",
            code=VALIDATION_ERROR,
            length=len(text),
        )
    return text


def check_choice(value: Any, allowed: Iterable[str], *, field: str) -> str:
    allowed_values = tuple(allowed)
    if value not in allowed_values:
        raise invalid(
            f"{field} must be one of {list(allowed_values)}",
            code=VALIDATION_ERROR,
            field=field,
        )
    return str(value)


def check_request_id(value: Any) -> str:
    """A caller-supplied idempotency key: bounded, printable ASCII, never a path."""
    if not isinstance(value, str):
        raise invalid("request_id must be a string", code=VALIDATION_ERROR)
    text = value.strip()
    if not text or len(text) > MAX_REQUEST_ID_CHARS or not all(0x21 <= ord(char) <= 0x7E for char in text):
        raise invalid(
            f"request_id must be 1..{MAX_REQUEST_ID_CHARS} printable ASCII characters",
            code=VALIDATION_ERROR,
        )
    return text


def check_expected_revision(value: Any, *, field: str = "expected_revision") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or int(value) < 0:
        raise invalid(f"{field} must be an integer >= 0", code=VALIDATION_ERROR)
    return int(value)


def assignment_id_for(asset_id: int, revision: int) -> str:
    """Deterministic assignment identity: one id per (asset, control revision)."""
    return f"asg-{int(asset_id)}-{int(revision)}"


def operation_id_for(request_id: str) -> str:
    """The operation id is derived from its idempotency key, so a replay is provable."""
    return f"op-{hashlib.sha256(request_id.encode('utf-8')).hexdigest()[:32]}"


def geo_fact_id_for(input_revision: str, fact_revision: int) -> str:
    return f"geofact-{str(input_revision)[:24]}-{int(fact_revision)}"


def place_view(
    row: Mapping[str, Any],
    *,
    aliases: Iterable[Mapping[str, Any]] = (),
    external_refs: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """The frozen ``Place`` projection of one ``places`` row."""
    return {
        "contract_version": DIRECTORY_CONTRACT_VERSION,
        "place_id": int(row["place_id"]),
        "kind": str(row["kind"]),
        "canonical_name": str(row["canonical_name"]),
        "aliases": [alias_view(alias) for alias in aliases],
        "parent_place_id": None if row["parent_place_id"] is None else int(row["parent_place_id"]),
        "origin": str(row["origin"]),
        "external_refs": [
            {"provider": str(ref["provider"]), "external_id": str(ref["external_id"])}
            for ref in external_refs
        ],
        "lifecycle": str(row["lifecycle"]),
        "revision": int(row["revision"]),
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
    }


def alias_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "text": str(row["text"]),
        "normalized": str(row["normalized"]),
        "language": None if row["language"] is None else str(row["language"]),
        "source": str(row["source"]),
        "lifecycle": str(row["lifecycle"]),
    }


def assignment_view(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The frozen ``PlaceAssignment`` projection, or ``None`` for "no control row"."""
    if row is None:
        return None
    return {
        "contract_version": CONTRACT_VERSION,
        "assignment_id": str(row["assignment_id"]),
        "asset_id": int(row["asset_id"]),
        "place_id": None if row["place_id"] is None else int(row["place_id"]),
        "mode": str(row["mode"]),
        "origin": str(row["origin"]),
        "manual_lock": bool(int(row["manual_lock"])),
        "resolution_id": None if row["resolution_id"] is None else str(row["resolution_id"]),
        "revision": int(row["revision"]),
        "record_state": str(row["record_state"]),
        "actor": str(row["actor"]),
        "operation_id": str(row["operation_id"]),
        "updated_at": int(row["updated_at"]),
    }


def privacy_flags() -> dict[str, bool]:
    """ADR-0005 §3: the fixed privacy statement every GeoFact carries."""
    return {
        "detail_endpoint_only": True,
        "loggable": False,
        "url_serializable": False,
        "feedback_serializable": False,
    }
