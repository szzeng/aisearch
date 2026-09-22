"""Executable drift checks for the P4-00A ADR-0011 companion.

P4-00A is a *contract only* task: it adds no production code, no migration and no UI. This
file therefore (a) validates the draft-2020-12 subset the checked-in JSON companion actually
uses, (b) pins the cross-item rules JSON Schema cannot express (the source mapping table, the
``state`` identity of the honesty block, the civil-key/label coupling), (c) freezes the two
existing cursor spaces with the *real* codec functions, and (d) proves with a synthetic JPEG
that the EXIF reading path the contract depends on exists in this environment.

The reference models below are the ADR text in executable form; they are not an
implementation of P4-00B and never claim that any production behaviour already exists.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from photo_search.api.errors import INVALID_CURSOR, ApiError
from photo_search.api.queries import (
    CAPTURE_CURSOR_PREFIX as LIVE_CAPTURE_CURSOR_PREFIX,
)
from photo_search.api.queries import (
    CURSOR_PREFIX,
    ORDER_ASSET_ID,
    ORDER_CAPTURE_DESC,
    ORDER_TIME_DESC,
    ORDERS,
    TIME_CURSOR_PREFIX,
    decode_cursor,
    decode_time_cursor,
    encode_cursor,
    encode_time_cursor,
)
from photo_search.api.queries import (
    decode_capture_cursor as live_decode_capture_cursor,
)
from photo_search.api.queries import (
    encode_capture_cursor as live_encode_capture_cursor,
)
from photo_search.search.sessions import query_hash

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "docs" / "decisions" / "contracts"
SCHEMA_PATH = CONTRACTS / "p4-time-contracts.v1.json"
ADR_PATH = ROOT / "docs" / "decisions" / "ADR-0011-taken-time-timezone-and-order-contract.md"

CONTRACT_VERSION = "p4-time-contracts.v1"
SEARCH_CONTRACT_VERSION = "p2-contracts.v1"
PERSON_SEARCH_CONTRACT_VERSION = "p3-person-search-contracts.v1"
RULES_VERSION = 1
MIN_OFFSET_MINUTES = -720
MAX_OFFSET_MINUTES = 840
MAX_SORT_KEY = 315537897599999
CAPTURE_ORDER = "capture_desc"
CAPTURE_CURSOR_PREFIX = "c1"
CAPTURE_CURSOR_ARITY = 4

SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
ADR = ADR_PATH.read_text(encoding="utf-8")

#: Every companion that existed before P4-00A. ADR-0011 declares exactly one additive
#: supersession (the live ``filters`` echo becomes ``SearchFiltersEchoV2``) and edits none
#: of these files, so all seven keep their bytes.
FROZEN_COMPANIONS = {
    "p2-contracts.v1.json": (
        "ec7de5f0cbe6d4c2ef2bf357326fa79abc3b427e6133e42186d61778ef0e4a57"
    ),
    "p2-place-contracts.v1.json": (
        "192db8ca53a9aaef835872187c4afbbfdbe2ad7f0fffa56a28061f86f84221fc"
    ),
    "p3-acceptance-matrix.v1.json": (
        "53b469029ca253b790c1809760edf6b7fd441707b609c3bf3994430585365049"
    ),
    "p3-face-correction-contracts.v1.json": (
        "d6d2e9f2e27bf9d7312cef6b5c4c1f24b7bf33e7003289d506d64c4723235a73"
    ),
    "p3-face-redetection-contracts.v1.json": (
        "76ead42d67ed724edc16f4637b061752ce77b01e6231e30d077f9abc0622067f"
    ),
    "p3-person-search-contracts.v1.json": (
        "0b49b786fcba08e099cc57b09b53918b9eb91373217a2efd485bb21cf0bdcfbe"
    ),
    "p3-person-split-contracts.v1.json": (
        "462f09358a7088ad1634b096c50fe297cfb85a9c87d8bff7f0725451af89b2ff"
    ),
}

PUBLIC_OBJECTS = (
    "TimeSourceRule",
    "CaptureBoundNormalized",
    "CaptureCondition",
    "CaptureCursor",
    "SearchFiltersEchoV2",
    "TakenTimeBlock",
    "AssetTakenTimeBlock",
    "TimeApiError",
)
#: The subset of the public surface that is a closed object (``CaptureBoundNormalized`` is
#: the one string-typed entry, so it carries no ``additionalProperties`` keyword).
CLOSED_OBJECTS = tuple(name for name in PUBLIC_OBJECTS if name != "CaptureBoundNormalized")

#: ADR-0009 §7.1 / p3-person-search-contracts.v1.json, unchanged.
P3_FILTERS_ECHO_KEYS = (
    "source_id",
    "availability",
    "place_ids",
    "include_descendants",
    "place_state",
    "expanded_place_ids",
    "catalog_filter_revision",
    "suggestion_revision",
    "binding",
    "person_ids",
    "person_match",
)
CAPTURE_ECHO_KEYS = ("capture_from", "capture_to_exclusive")

#: ADR-0009 §6.2 continuation digest fields, in the order the ADR freezes them.
P3_DIGEST_FIELDS = (
    "text",
    "source_id",
    "availability",
    "place_ids",
    "include_descendants",
    "place_state",
    "top_k",
    "page_size",
    "person_ids",
    "person_match",
)
CAPTURE_DIGEST_FIELDS = ("capture_from", "capture_to_exclusive")

#: ADR-0011 §4.1, the one frozen source mapping table.
TIME_SOURCE_RULES_ORDER = ("EXIF_ORIGINAL", "EXIF_DIGITIZED", "EXIF_ROOT", "FILE_MTIME")
TIME_SOURCE_RULES = {
    "EXIF_ORIGINAL": {
        "time_source": "EXIF_ORIGINAL",
        "label": "capture",
        "capture_confidence": "HIGH",
        "participates_in_capture_filter": True,
        "sort_bucket": "ordered",
        "fallback_rank": 1,
    },
    "EXIF_DIGITIZED": {
        "time_source": "EXIF_DIGITIZED",
        "label": "capture",
        "capture_confidence": "MEDIUM",
        "participates_in_capture_filter": True,
        "sort_bucket": "ordered",
        "fallback_rank": 2,
    },
    "EXIF_ROOT": {
        "time_source": "EXIF_ROOT",
        "label": "file_time",
        "capture_confidence": "LOW",
        "participates_in_capture_filter": False,
        "sort_bucket": "ordered",
        "fallback_rank": 3,
    },
    "FILE_MTIME": {
        "time_source": "FILE_MTIME",
        "label": "file_time",
        "capture_confidence": "LOW",
        "participates_in_capture_filter": False,
        "sort_bucket": "tail",
        "fallback_rank": 4,
    },
}

#: The five ``(time_source, time_confidence)`` pairs ADR-0011 §2 allows.
ALLOWED_SOURCE_CONFIDENCE = (
    ("EXIF_ORIGINAL", "HIGH"),
    ("EXIF_ORIGINAL", "MEDIUM"),
    ("EXIF_DIGITIZED", "MEDIUM"),
    ("EXIF_ROOT", "LOW"),
    ("FILE_MTIME", "LOW"),
)

CAPTURE_TIME_REASONS = (
    "capture_bound_malformed",
    "capture_bound_has_offset",
    "capture_bound_out_of_range",
    "capture_range_not_ascending",
)


class ContractValidationError(AssertionError):
    """A fixture does not satisfy the checked schema and semantic subset."""


class BoundRefused(AssertionError):
    """A capture bound or range the contract refuses; ``reason`` is the frozen reason."""

    def __init__(self, reason: str, field: str = "capture_from") -> None:
        super().__init__(reason)
        self.reason = reason
        self.field = field


def _resolve(schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if ref is None:
        return schema
    prefix = "#/$defs/"
    if not isinstance(ref, str) or not ref.startswith(prefix):
        raise ContractValidationError(f"unsupported ref: {ref!r}")
    return SCHEMA["$defs"][ref.removeprefix(prefix)]


def _is_type(instance: Any, expected: str) -> bool:
    if expected == "null":
        return instance is None
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "object":
        return isinstance(instance, dict)
    raise ContractValidationError(f"unsupported type: {expected!r}")


def _errors(instance: Any, raw_schema: dict[str, Any], path: str = "$") -> list[str]:
    schema = _resolve(raw_schema)
    errors: list[str] = []
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: const")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: enum")
    if "oneOf" in schema:
        matches = sum(not _errors(instance, branch, path) for branch in schema["oneOf"])
        if matches != 1:
            errors.append(f"{path}: oneOf matched {matches}")
    for branch in schema.get("allOf", []):
        errors.extend(_errors(instance, branch, path))
    if "if" in schema and not _errors(instance, schema["if"], path):
        errors.extend(_errors(instance, schema.get("then", {}), path))

    expected_type = schema.get("type")
    if expected_type is not None:
        choices = [expected_type] if isinstance(expected_type, str) else expected_type
        if not any(_is_type(instance, item) for item in choices):
            return errors + [f"{path}: type"]

    if isinstance(instance, dict):
        for name in schema.get("required", []):
            if name not in instance:
                errors.append(f"{path}: missing {name}")
        properties = schema.get("properties", {})
        for name, value in instance.items():
            if name in properties:
                errors.extend(_errors(value, properties[name], f"{path}.{name}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected {name}")
    if isinstance(instance, list):
        prefix_items = schema.get("prefixItems")
        if prefix_items is not None:
            for index, item_schema in enumerate(prefix_items):
                if index >= len(instance):
                    break
                errors.extend(_errors(instance[index], item_schema, f"{path}[{index}]"))
        if len(instance) < schema.get("minItems", 0):
            errors.append(f"{path}: too few")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: too many")
        if schema.get("uniqueItems"):
            rendered = [json.dumps(item, sort_keys=True) for item in instance]
            if len(rendered) != len(set(rendered)):
                errors.append(f"{path}: duplicate")
        if "items" in schema:
            for index, item in enumerate(instance):
                errors.extend(_errors(item, schema["items"], f"{path}[{index}]"))
    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: too short")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: too long")
        if "pattern" in schema and re.fullmatch(schema["pattern"], instance) is None:
            errors.append(f"{path}: pattern")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if not math.isfinite(instance):
            errors.append(f"{path}: finite")
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: maximum")
    return errors


def contract_errors(instance: Any, definition: str) -> list[str]:
    """The schema subset plus the cross-item rules the JSON cannot express."""
    errors = _errors(instance, {"$ref": f"#/$defs/{definition}"})
    if definition == "CaptureCondition":
        bounds = [instance.get("capture_from"), instance.get("capture_to_exclusive")]
        if all(bound is None for bound in bounds):
            errors.append("condition: at least one bound must be present")
        if all(bound is not None for bound in bounds):
            if str(bounds[0]) >= str(bounds[1]):
                errors.append("condition: a canonical range must ascend")
    if definition == "SearchFiltersEchoV2":
        expected = set(P3_FILTERS_ECHO_KEYS) | set(CAPTURE_ECHO_KEYS)
        if set(instance) != expected:
            errors.append("echo: the V2 key set is exactly the p3 keys plus the two capture keys")
        ids = instance.get("person_ids", [])
        if ids != sorted(ids):
            errors.append("echo: person_ids must be canonical ascending")
    if definition == "TakenTimeBlock":
        total = instance.get("assets_total")
        extracted = instance.get("extracted_assets")
        captured = instance.get("capture_time_assets")
        if None not in (total, extracted, captured):
            if extracted > total or captured > extracted:
                errors.append("taken_time: counts must be nested total >= extracted >= captured")
            if instance.get("state") != taken_time_state(total, extracted, captured):
                errors.append("taken_time: state must be the pure function of the three counts")
    if definition == "AssetTakenTimeBlock":
        errors.extend(_asset_taken_time_errors(instance))
    if definition == "TimeSourceRule":
        rule = instance.get("time_source")
        if rule is not None and TIME_SOURCE_RULES.get(str(rule)) != dict(instance):
            errors.append("rule: must match the frozen mapping table")
    if definition == "TimeApiError":
        code = instance.get("code")
        details = instance.get("details") or {}
        if code == "INVALID_TIME_RANGE":
            if details.get("reason") not in CAPTURE_TIME_REASONS:
                errors.append("error: INVALID_TIME_RANGE needs a frozen reason")
            if details.get("field") not in ("capture_from", "capture_to_exclusive"):
                errors.append("error: INVALID_TIME_RANGE must name the offending bound")
        if code == "CATALOG_FILTER_REVISION_STALE":
            if details.get("reason") != "CATALOG_FILTER_REVISION_CHANGED":
                errors.append("error: the stale revision keeps its historical reason")
            if "current_catalog_filter_revision" not in details:
                errors.append("error: a stale continuation must publish the current revision")
            if "session_catalog_filter_revision" in details and "search_session_id" not in details:
                errors.append("error: only a search session may publish its own session revision")
    return errors


def _asset_taken_time_errors(instance: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    source = instance.get("time_source")
    if source is None:
        if any(instance.get(key) is not None for key in instance):
            errors.append("detail: an empty block is all null")
        return errors
    rule = TIME_SOURCE_RULES[str(source)]
    if (rule["label"] == "capture") != (instance.get("label") == "capture"):
        errors.append("detail: the label must be the frozen label of the source")
    has_civil = instance.get("taken_at_sort") is not None
    if has_civil != (instance.get("taken_at") is not None):
        errors.append("detail: taken_at and taken_at_sort appear together")
    if has_civil != (rule["sort_bucket"] == "ordered"):
        errors.append("detail: only an ordered source carries a civil key")
    offset = instance.get("timezone_offset_minutes")
    if offset is not None and source == "FILE_MTIME":
        errors.append("detail: a file mtime has no timezone offset")
    confidence = instance.get("time_confidence")
    if confidence == "HIGH" and (source != "EXIF_ORIGINAL" or offset is None):
        errors.append("detail: HIGH requires the original field plus a recorded offset")
    if source == "EXIF_ORIGINAL" and offset is None and confidence != "MEDIUM":
        errors.append("detail: a local time without an offset is never HIGH")
    allowed_grades = {rule["capture_confidence"]}
    if source == "EXIF_ORIGINAL":
        allowed_grades.add("MEDIUM")
    if confidence not in allowed_grades:
        errors.append("detail: confidence must be the frozen grade or its bounded downgrade")
    if rule["label"] == "file_time" and instance.get("label") != "file_time":
        errors.append("detail: unreliable sources are shown as file time")
    return errors


def assert_valid(instance: Any, definition: str) -> None:
    errors = contract_errors(instance, definition)
    if errors:
        raise ContractValidationError("\n".join(errors))


def assert_invalid(instance: Any, definition: str) -> None:
    assert contract_errors(instance, definition)


# ------------------------------------------------------------------ reference semantics
#: The civil origin ADR-0011 §3.1 freezes; it is a contract convention, not an instant.
CIVIL_EPOCH_ORDINAL = dt.date(1, 1, 1).toordinal()
BOUND_PATTERN = re.compile(
    r"^(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    r"(?:T(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?:\.(?P<fraction>[0-9]{1,3}))?)?$"
)


def civil_key(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0,
    millis: int = 0,
) -> int:
    """ADR-0011 §3.1: the wall clock read as if those calendar fields were UTC."""
    ordinal = dt.date(year, month, day).toordinal()
    return (
        (ordinal - CIVIL_EPOCH_ORDINAL) * 86_400_000
        + (hour * 3600 + minute * 60 + second) * 1000
        + millis
    )


def parse_capture_bound(raw: Any, *, field: str = "capture_from") -> int:
    """The frozen bound grammar: date-only or a wall-clock date-time, never a zone."""
    if not isinstance(raw, str) or not raw:
        raise BoundRefused("capture_bound_malformed", field)
    if raw != raw.strip():
        raise BoundRefused("capture_bound_malformed", field)
    if raw.endswith("Z") or raw.endswith("z") or re.search(r"[+-][0-9]{2}:?[0-9]{2}$", raw):
        raise BoundRefused("capture_bound_has_offset", field)
    match = BOUND_PATTERN.fullmatch(raw)
    if match is None:
        raise BoundRefused("capture_bound_malformed", field)
    year = int(match.group("year"))
    if year < 1 or year > 9999:
        raise BoundRefused("capture_bound_out_of_range", field)
    fraction = match.group("fraction") or ""
    millis = int(fraction.ljust(3, "0")) if fraction else 0
    hour = int(match.group("hour") or 0)
    minute = int(match.group("minute") or 0)
    second = int(match.group("second") or 0)
    if hour > 23 or minute > 59 or second > 59:
        raise BoundRefused("capture_bound_malformed", field)
    try:
        return civil_key(
            year,
            int(match.group("month")),
            int(match.group("day")),
            hour,
            minute,
            second,
            millis,
        )
    except ValueError as error:
        raise BoundRefused("capture_bound_malformed", field) from error


def normalize_capture_bound(raw: str, *, field: str = "capture_from") -> str:
    value = parse_capture_bound(raw, field=field)
    days, remainder = divmod(value, 86_400_000)
    date = dt.date.fromordinal(CIVIL_EPOCH_ORDINAL + days)
    hour, remainder = divmod(remainder, 3_600_000)
    minute, remainder = divmod(remainder, 60_000)
    second, millis = divmod(remainder, 1000)
    return (
        f"{date.year:04d}-{date.month:02d}-{date.day:02d}"
        f"T{int(hour):02d}:{int(minute):02d}:{int(second):02d}.{int(millis):03d}"
    )


def capture_range(
    capture_from: Any = None, capture_to_exclusive: Any = None
) -> tuple[int | None, int | None]:
    start = (
        None if capture_from is None else parse_capture_bound(capture_from, field="capture_from")
    )
    end = (
        None
        if capture_to_exclusive is None
        else parse_capture_bound(capture_to_exclusive, field="capture_to_exclusive")
    )
    if start is not None and end is not None and start >= end:
        raise BoundRefused("capture_range_not_ascending", "capture_from")
    return start, end


def in_capture_range(
    sort_key: int | None,
    *,
    capture_from: Any = None,
    capture_to_exclusive: Any = None,
    participates: bool = True,
) -> bool:
    """ADR-0011 §6.4: half-open, civil-to-civil, capture rows only."""
    if sort_key is None or not participates:
        return False
    start, end = capture_range(capture_from, capture_to_exclusive)
    if start is not None and sort_key < start:
        return False
    if end is not None and sort_key >= end:
        return False
    return True


def resolve_time_source(
    *,
    exif_original: str | None = None,
    exif_digitized: str | None = None,
    exif_root: str | None = None,
    file_mtime_ns: int | None = None,
) -> tuple[str, int | None]:
    """ADR-0011 §4.1: the frozen priority chain, bad values fall through."""
    for source, raw in (
        ("EXIF_ORIGINAL", exif_original),
        ("EXIF_DIGITIZED", exif_digitized),
        ("EXIF_ROOT", exif_root),
    ):
        if raw is None:
            continue
        try:
            value = parse_capture_bound(str(raw).replace(":", "-", 2).replace(" ", "T"))
        except BoundRefused:
            continue
        return source, value
    assert file_mtime_ns is not None, "FILE_MTIME is always available"
    return "FILE_MTIME", None


def taken_time_state(assets_total: int, extracted_assets: int, capture_time_assets: int) -> str:
    """ADR-0011 §8.1: the pure state function of the honesty block."""
    if assets_total == 0:
        return "READY"
    if extracted_assets == 0:
        return "PENDING"
    if extracted_assets < assets_total:
        return "PARTIAL"
    return "READY" if capture_time_assets > 0 else "NO_CAPTURE_TIME"


def normalize_taken_time_block(
    *,
    assets_total: int,
    extracted_assets: int,
    capture_time_assets: int,
    rules_version_matches: bool,
) -> dict[str, Any]:
    """ADR-0011 §8.1: a mismatched rules version normalizes both counts to 0.

    The rows stay in the catalog, but they are not trustworthy under this build's rules, so
    the block reports them as unextracted. That is what keeps ``state`` a pure function of
    the three counts the closed block carries.
    """
    if rules_version_matches:
        trusted_extracted = extracted_assets
        trusted_captured = capture_time_assets
    else:
        trusted_extracted = 0
        trusted_captured = 0
    return {
        "state": taken_time_state(assets_total, trusted_extracted, trusted_captured),
        "assets_total": assets_total,
        "extracted_assets": trusted_extracted,
        "capture_time_assets": trusted_captured,
    }


def capture_digest(
    *,
    text: str | None,
    source_id: int | None,
    availability: str,
    place_ids: list[int],
    include_descendants: bool,
    place_state: str,
    top_k: int,
    page_size: int,
    person_ids: list[int],
    person_match: str,
    capture_from: str | None,
    capture_to_exclusive: str | None,
) -> str:
    """ADR-0011 §6.5: the p3 digest payload plus the two canonical capture bounds."""
    canonical_from = (
        None if capture_from is None else normalize_capture_bound(capture_from, field="capture_from")
    )
    canonical_to = (
        None
        if capture_to_exclusive is None
        else normalize_capture_bound(capture_to_exclusive, field="capture_to_exclusive")
    )
    if canonical_from is not None and canonical_to is not None:
        if canonical_from >= canonical_to:
            raise BoundRefused("capture_range_not_ascending", "capture_from")
    payload = {
        "text": text,
        "source_id": source_id,
        "availability": availability,
        "place_ids": list(place_ids),
        "include_descendants": include_descendants,
        "place_state": place_state,
        "top_k": top_k,
        "page_size": page_size,
        "person_ids": list(person_ids),
        "person_match": person_match,
        "capture_from": canonical_from,
        "capture_to_exclusive": canonical_to,
    }
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def encode_capture_cursor(known: int, sort_key: int, asset_id: int, revision: int) -> str:
    import base64

    payload = ":".join(
        (CAPTURE_CURSOR_PREFIX, str(known), str(sort_key), str(asset_id), str(revision))
    )
    return base64.urlsafe_b64encode(payload.encode("ascii")).decode("ascii").rstrip("=")


def decode_capture_cursor(text: str) -> tuple[int, int, int, int]:
    import base64
    import binascii

    try:
        decoded = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError) as error:
        raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid") from error
    parts = decoded.split(":")
    if len(parts) != CAPTURE_CURSOR_ARITY + 1 or parts[0] != CAPTURE_CURSOR_PREFIX:
        raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid")
    values: list[int] = []
    for part in parts[1:]:
        try:
            number = int(part)
        except ValueError as error:
            raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid") from error
        if number < 0:
            raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid")
        values.append(number)
    if values[0] not in (0, 1):
        raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid")
    # ADR-0011 §5.2: the tail bucket writes 0 in the sort element, and an asset id of 0 is
    # never a position.
    if values[0] == 0 and values[1] != 0:
        raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid")
    if values[2] < 1:
        raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid")
    return (values[0], values[1], values[2], values[3])


def capture_cursor(known: int, sort_key: int, asset_id: int, revision: int) -> dict[str, Any]:
    """The frozen ``CaptureCursor`` object: three fields, the revision is ``values[3]``."""
    return {
        "prefix": CAPTURE_CURSOR_PREFIX,
        "arity": CAPTURE_CURSOR_ARITY,
        "values": [known, sort_key, asset_id, revision],
    }


def asset_taken_time(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "taken_at": None,
        "taken_at_sort": None,
        "time_source": None,
        "time_confidence": None,
        "timezone_offset_minutes": None,
        "label": None,
        "camera": None,
    }
    payload.update(overrides)
    return payload


# ------------------------------------------------------------------- schema identity
def test_companion_identity_and_closed_public_surface() -> None:
    assert SCHEMA["$schema"].endswith("2020-12/schema")
    assert SCHEMA["$defs"]["ContractVersion"]["const"] == CONTRACT_VERSION
    assert SCHEMA["$defs"]["SearchContractVersion"]["const"] == SEARCH_CONTRACT_VERSION
    assert (
        SCHEMA["$defs"]["PersonSearchContractVersion"]["const"]
        == PERSON_SEARCH_CONTRACT_VERSION
    )
    assert SCHEMA["$defs"]["RulesVersion"]["const"] == RULES_VERSION
    assert SCHEMA["$defs"]["MinOffsetMinutes"]["const"] == MIN_OFFSET_MINUTES
    assert SCHEMA["$defs"]["MaxOffsetMinutes"]["const"] == MAX_OFFSET_MINUTES
    assert SCHEMA["$defs"]["CaptureOrderValue"]["const"] == CAPTURE_ORDER
    assert SCHEMA["$defs"]["CaptureCursorPrefix"]["const"] == CAPTURE_CURSOR_PREFIX
    assert SCHEMA["$defs"]["CaptureCursorArity"]["const"] == CAPTURE_CURSOR_ARITY
    assert SCHEMA["$defs"]["AssetOrder"]["enum"] == ["asset_id", "time_desc", CAPTURE_ORDER]
    object_defs = {
        name: defn for name, defn in SCHEMA["$defs"].items() if defn.get("type") == "object"
    }
    assert set(CLOSED_OBJECTS) <= set(object_defs)
    assert "CaptureBoundNormalized" in PUBLIC_OBJECTS
    assert "CaptureBoundNormalized" not in object_defs
    for name, defn in object_defs.items():
        assert defn["additionalProperties"] is False, name
    assert [entry["$ref"].rsplit("/", 1)[-1] for entry in SCHEMA["oneOf"]] == list(
        PUBLIC_OBJECTS
    )
    assert SCHEMA["$defs"]["CivilSortKeyEpoch"]["const"] == "0001-01-01T00:00:00.000"
    assert SCHEMA["$defs"]["AssetTakenTimeBlock"]["properties"]["taken_at_sort"]["oneOf"][0][
        "maximum"
    ] == MAX_SORT_KEY
    assert set(SCHEMA["$defs"]["TimeSource"]["enum"]) == set(TIME_SOURCE_RULES_ORDER)


def test_adr_records_the_companion_hash_and_every_frozen_companion_is_byte_identical() -> None:
    recorded = re.findall(r"SHA-256：`([0-9a-f]{64})`", ADR)
    assert len(recorded) == 1
    assert recorded[0] == hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()
    for name, expected in FROZEN_COMPANIONS.items():
        assert hashlib.sha256((CONTRACTS / name).read_bytes()).hexdigest() == expected, name
    raw = SCHEMA_PATH.read_bytes()
    assert b"\r\n" not in raw and raw.endswith(b"\n")


# --------------------------------------------------------- source and confidence closure
def test_the_source_mapping_table_is_frozen_and_fully_ordered() -> None:
    table = SCHEMA["$defs"]["TimeSourceRules"]
    assert table["additionalProperties"] is False
    assert set(table["required"]) == set(TIME_SOURCE_RULES_ORDER)
    for source in TIME_SOURCE_RULES_ORDER:
        branch = table["properties"][source]
        assert branch == {"const": TIME_SOURCE_RULES[source]}, source
        assert_valid(TIME_SOURCE_RULES[source], "TimeSourceRule")
        assert TIME_SOURCE_RULES[source]["fallback_rank"] == (
            TIME_SOURCE_RULES_ORDER.index(source) + 1
        )
    ranks = sorted(rule["fallback_rank"] for rule in TIME_SOURCE_RULES.values())
    assert ranks == [1, 2, 3, 4]


def test_unknown_or_bad_sources_and_confidences_are_refused() -> None:
    assert_invalid(
        {**TIME_SOURCE_RULES["EXIF_ORIGINAL"], "time_source": "EXIF_GPS"}, "TimeSourceRule"
    )
    assert_invalid(
        {**TIME_SOURCE_RULES["FILE_MTIME"], "time_source": "SCAN_TIME"}, "TimeSourceRule"
    )
    assert_invalid(
        {**TIME_SOURCE_RULES["FILE_MTIME"], "time_source": "NOW"}, "TimeSourceRule"
    )
    assert_invalid(
        {**TIME_SOURCE_RULES["EXIF_ROOT"], "capture_confidence": "CRITICAL"}, "TimeSourceRule"
    )
    assert_invalid(
        {**TIME_SOURCE_RULES["EXIF_ROOT"], "capture_confidence": None}, "TimeSourceRule"
    )
    assert_invalid(
        {**TIME_SOURCE_RULES["EXIF_ORIGINAL"], "fallback_rank": 5}, "TimeSourceRule"
    )
    assert_invalid({**TIME_SOURCE_RULES["EXIF_ORIGINAL"], "surprise": 1}, "TimeSourceRule")
    allowed = {
        (rule["time_source"], rule["capture_confidence"])
        for rule in TIME_SOURCE_RULES.values()
        if rule["time_source"] != "EXIF_ORIGINAL"
    } | {
        ("EXIF_ORIGINAL", "HIGH"),
        ("EXIF_ORIGINAL", "MEDIUM"),
    }
    assert allowed == set(ALLOWED_SOURCE_CONFIDENCE)
    assert TIME_SOURCE_RULES["EXIF_ORIGINAL"]["capture_confidence"] == "HIGH"
    assert TIME_SOURCE_RULES["EXIF_ROOT"]["label"] == "file_time"
    assert TIME_SOURCE_RULES["EXIF_ROOT"]["participates_in_capture_filter"] is False


def test_only_capture_rows_participate_in_the_range_and_file_time_is_the_tail() -> None:
    for source, rule in TIME_SOURCE_RULES.items():
        participates = rule["participates_in_capture_filter"]
        assert participates == (rule["label"] == "capture"), source
        assert (rule["sort_bucket"] == "tail") == (source == "FILE_MTIME"), source
    assert not TIME_SOURCE_RULES["EXIF_ROOT"]["participates_in_capture_filter"]
    assert not TIME_SOURCE_RULES["FILE_MTIME"]["participates_in_capture_filter"]


# ------------------------------------------------------------------- timezone / civil key
def test_timezone_offset_bounds_defaults_and_refusals() -> None:
    for value in (None, MIN_OFFSET_MINUTES, MAX_OFFSET_MINUTES, 0, 480, -330):
        assert_valid(value, "TimezoneOffsetMinutes")
    for value in (MIN_OFFSET_MINUTES - 1, MAX_OFFSET_MINUTES + 1, "480", 1.5, True):
        assert_invalid(value, "TimezoneOffsetMinutes")


def test_a_local_time_without_an_offset_is_never_treated_as_utc() -> None:
    wall = civil_key(2022, 7, 5, 18, 31, 7, 0)
    with_offset = asset_taken_time(
        taken_at="2022-07-05T18:31:07.000",
        taken_at_sort=wall,
        time_source="EXIF_ORIGINAL",
        time_confidence="HIGH",
        timezone_offset_minutes=480,
        label="capture",
    )
    without_offset = dict(with_offset)
    without_offset["time_confidence"] = "MEDIUM"
    without_offset["timezone_offset_minutes"] = None
    assert_valid(with_offset, "AssetTakenTimeBlock")
    assert_valid(without_offset, "AssetTakenTimeBlock")
    assert with_offset["taken_at_sort"] == without_offset["taken_at_sort"] == wall
    # Dropping the offset downgrades the grade and nothing else: no instant is fabricated.
    assert "instant" not in json.dumps(SCHEMA, ensure_ascii=False).lower()
    assert "instant" not in json.dumps(without_offset, ensure_ascii=False).lower()
    assert "**不是** instant" in ADR
    assert "永不参与" in ADR


def test_the_civil_key_is_comparable_and_matches_the_displayed_wall_clock() -> None:
    early = civil_key(2022, 7, 5, 18, 31, 7, 0)
    later = civil_key(2022, 7, 5, 18, 31, 8, 0)
    next_day = civil_key(2022, 7, 6, 0, 0, 0, 0)
    assert early < later < next_day
    assert normalize_capture_bound("2022-07-05T18:31:07") == "2022-07-05T18:31:07.000"
    assert normalize_capture_bound("2022-07-05T18:31:07.9") == "2022-07-05T18:31:07.900"
    assert parse_capture_bound("2022-07-05T18:31:07.9") == civil_key(
        2022, 7, 5, 18, 31, 7, 900
    )
    assert parse_capture_bound("0001-01-01") == 0
    assert parse_capture_bound("9999-12-31T23:59:59.999") == MAX_SORT_KEY
    for value in ("0001-01-01", "2022-02-28T23:59:59.999"):
        assert_valid(normalize_capture_bound(value), "CaptureBoundNormalized")


# --------------------------------------------------------------------------- bound grammar
def test_bound_grammar_accepts_only_the_frozen_spellings() -> None:
    for raw in (
        "2022-01-01",
        "2022-01-01T00:00:00",
        "2022-01-01T00:00:00.5",
        "2022-01-01T00:00:00.50",
        "2022-01-01T00:00:00.500",
        "9999-12-31T23:59:59.999",
    ):
        assert re.fullmatch(SCHEMA["$defs"]["CaptureBoundRaw"]["pattern"], raw), raw
        assert_valid(normalize_capture_bound(raw), "CaptureBoundNormalized")


@pytest.mark.parametrize(
    "raw,reason",
    [
        ("2022-01-01T00:00:00Z", "capture_bound_has_offset"),
        ("2022-01-01T00:00:00+08:00", "capture_bound_has_offset"),
        ("2022-01-01T00:00:00-0500", "capture_bound_has_offset"),
        ("2022/01/01", "capture_bound_malformed"),
        ("20220101", "capture_bound_malformed"),
        ("2022-1-1", "capture_bound_malformed"),
        ("2022-02-30", "capture_bound_malformed"),
        ("2022-13-01", "capture_bound_malformed"),
        ("2022-01-01T24:00:00", "capture_bound_malformed"),
        (" 2022-01-01", "capture_bound_malformed"),
        ("", "capture_bound_malformed"),
        ("2022-01-01T00:00:00.1234", "capture_bound_malformed"),
        ("0000-01-01", "capture_bound_out_of_range"),
    ],
    ids=[
        "zulu",
        "east-offset",
        "compact-offset",
        "slashes",
        "compact-date",
        "loose-digits",
        "feb-30",
        "month-13",
        "hour-24",
        "surrounding-space",
        "empty",
        "four-fraction-digits",
        "year-zero",
    ],
)
def test_refused_bounds_carry_the_frozen_reason(raw: str, reason: str) -> None:
    with pytest.raises(BoundRefused) as error:
        parse_capture_bound(raw)
    assert error.value.reason == reason
    assert contract_errors(
        {
            "code": "INVALID_TIME_RANGE",
            "message": "the capture bound is not usable",
            "request_id": "r-1",
            "details": {"reason": error.value.reason, "field": error.value.field},
        },
        "TimeApiError",
    ) == []


def test_the_range_is_left_closed_and_right_open() -> None:
    day = civil_key(2022, 7, 5)
    next_day = civil_key(2022, 7, 6)
    assert in_capture_range(day, capture_from="2022-07-05", capture_to_exclusive="2022-07-06")
    assert not in_capture_range(
        next_day, capture_from="2022-07-05", capture_to_exclusive="2022-07-06"
    )
    assert in_capture_range(
        civil_key(2022, 7, 5, 23, 59, 59, 999),
        capture_from="2022-07-05",
        capture_to_exclusive="2022-07-06",
    )
    assert not in_capture_range(civil_key(2022, 7, 5), capture_to_exclusive="2022-07-05")
    assert in_capture_range(
        civil_key(2022, 7, 5, 23, 59, 59, 999),
        capture_from="2022-07-05T23:59:59.999",
        capture_to_exclusive="2022-07-06T00:00:00.000",
    )
    assert not in_capture_range(
        civil_key(2022, 7, 6),
        capture_from="2022-07-05T23:59:59.999",
        capture_to_exclusive="2022-07-06T00:00:00.000",
    )
    # Unreliable rows never match, whatever the window is.
    assert not in_capture_range(day, capture_from="2022-01-01", participates=False)
    assert not in_capture_range(None, capture_from="2022-01-01")


def test_an_empty_or_inverted_range_is_refused() -> None:
    with pytest.raises(BoundRefused) as equal:
        capture_range("2022-07-05", "2022-07-05T00:00:00.000")
    assert equal.value.reason == "capture_range_not_ascending"
    with pytest.raises(BoundRefused) as inverted:
        capture_range("2022-07-06", "2022-07-05")
    assert inverted.value.reason == "capture_range_not_ascending"
    assert contract_errors({"capture_from": None, "capture_to_exclusive": None}, "CaptureCondition")
    assert contract_errors(
        {
            "capture_from": "2022-07-06T00:00:00.000",
            "capture_to_exclusive": "2022-07-05T00:00:00.000",
        },
        "CaptureCondition",
    )
    assert not contract_errors(
        {"capture_from": "2022-07-05T00:00:00.000", "capture_to_exclusive": None},
        "CaptureCondition",
    )


# ------------------------------------------------------------------ fallback chain purity
def test_the_fallback_chain_skips_bad_values_and_never_invents_a_capture_time() -> None:
    assert resolve_time_source(exif_original="2022:07:05 18:31:07") == (
        "EXIF_ORIGINAL",
        civil_key(2022, 7, 5, 18, 31, 7),
    )
    assert resolve_time_source(
        exif_original="2022:02:30 18:31:07", exif_digitized="2022:07:05 18:31:08"
    ) == ("EXIF_DIGITIZED", civil_key(2022, 7, 5, 18, 31, 8))
    assert resolve_time_source(
        exif_original="not-a-date",
        exif_digitized="2022:13:05 18:31:08",
        exif_root="2022:07:05 18:31:09",
    ) == ("EXIF_ROOT", civil_key(2022, 7, 5, 18, 31, 9))
    assert resolve_time_source(
        exif_original="", exif_digitized="2022:13:05 18:31:08", file_mtime_ns=1
    ) == ("FILE_MTIME", None)
    assert resolve_time_source(file_mtime_ns=0) == ("FILE_MTIME", None)
    # A file mtime is never a capture time, and no scan/now source exists at all.
    assert "scan" not in json.dumps(SCHEMA["$defs"]["TimeSource"], ensure_ascii=False).lower()
    assert "now" not in SCHEMA["$defs"]["TimeSource"]["enum"]
    mtime_row = asset_taken_time(
        time_source="FILE_MTIME", time_confidence="LOW", label="file_time"
    )
    assert_valid(mtime_row, "AssetTakenTimeBlock")
    assert contract_errors(
        asset_taken_time(
            taken_at="2022-07-05T18:31:07.000",
            taken_at_sort=civil_key(2022, 7, 5, 18, 31, 7),
            time_source="FILE_MTIME",
            time_confidence="LOW",
            label="file_time",
        ),
        "AssetTakenTimeBlock",
    )
    # An EXIF root date is orderable but still shown, and filtered, as file time.
    root_row = asset_taken_time(
        taken_at="2022-07-05T18:31:07.000",
        taken_at_sort=civil_key(2022, 7, 5, 18, 31, 7),
        time_source="EXIF_ROOT",
        time_confidence="LOW",
        label="file_time",
    )
    assert_valid(root_row, "AssetTakenTimeBlock")
    assert not TIME_SOURCE_RULES["EXIF_ROOT"]["participates_in_capture_filter"]
    assert not in_capture_range(
        root_row["taken_at_sort"],
        capture_from="2022-01-01",
        participates=False,
    )
    assert contract_errors(
        asset_taken_time(
            taken_at="2022-07-05T18:31:07.000",
            taken_at_sort=civil_key(2022, 7, 5, 18, 31, 7),
            time_source="EXIF_ROOT",
            time_confidence="LOW",
            label="capture",
        ),
        "AssetTakenTimeBlock",
    )


def test_an_empty_detail_block_is_all_null_and_a_high_grade_needs_an_offset() -> None:
    assert_valid(asset_taken_time(), "AssetTakenTimeBlock")
    assert contract_errors(
        asset_taken_time(time_source="EXIF_ORIGINAL"), "AssetTakenTimeBlock"
    )
    assert contract_errors(
        asset_taken_time(
            taken_at="2022-07-05T18:31:07.000",
            taken_at_sort=civil_key(2022, 7, 5, 18, 31, 7),
            time_source="EXIF_ORIGINAL",
            time_confidence="HIGH",
            label="capture",
        ),
        "AssetTakenTimeBlock",
    )
    assert_valid(
        asset_taken_time(
            taken_at="2022-07-05T18:31:07.000",
            taken_at_sort=civil_key(2022, 7, 5, 18, 31, 7),
            time_source="EXIF_ORIGINAL",
            time_confidence="MEDIUM",
            timezone_offset_minutes=480,
            label="capture",
            camera="TestMake TestModel",
        ),
        "AssetTakenTimeBlock",
    )


# --------------------------------------------------------- order and cursor space isolation
def test_the_order_value_set_is_closed_and_the_two_existing_values_survive() -> None:
    assert SCHEMA["$defs"]["AssetOrder"]["enum"] == ["asset_id", "time_desc", CAPTURE_ORDER]
    assert ORDER_ASSET_ID == "asset_id"
    assert ORDER_TIME_DESC == "time_desc"
    # P4-00B implemented ADR-0011 §5.1: the listing's closed set is now the frozen enum, and
    # the two pre-existing orders keep their exact spellings.
    assert ORDER_CAPTURE_DESC == CAPTURE_ORDER
    assert LIVE_CAPTURE_CURSOR_PREFIX == CAPTURE_CURSOR_PREFIX
    assert set(ORDERS) == {"asset_id", "time_desc", CAPTURE_ORDER}
    assert set(ORDERS) == set(SCHEMA["$defs"]["AssetOrder"]["enum"])
    assert set(ORDERS) <= set(SCHEMA["$defs"]["AssetOrder"]["enum"])
    for unknown in ("captured_at", "taken_desc", "capture_asc"):
        assert unknown not in SCHEMA["$defs"]["AssetOrder"]["enum"]


def test_the_three_cursor_spaces_never_interpret_each_other() -> None:
    assert CURSOR_PREFIX == "v1"
    assert TIME_CURSOR_PREFIX == "t1"
    assert len({CURSOR_PREFIX, TIME_CURSOR_PREFIX, CAPTURE_CURSOR_PREFIX}) == 3
    assert decode_cursor(encode_cursor(5)) == 5
    assert decode_time_cursor(encode_time_cursor(1_700_000_000_000, 7)) == (
        1_700_000_000_000,
        7,
    )
    assert decode_capture_cursor(encode_capture_cursor(1, 1_700_000_000_000, 5, 12)) == (
        1,
        1_700_000_000_000,
        5,
        12,
    )
    assert decode_capture_cursor(encode_capture_cursor(0, 0, 5, 12)) == (0, 0, 5, 12)
    with pytest.raises(ApiError) as crossed_one:
        decode_cursor(encode_time_cursor(1_700_000_000_000, 7))
    assert crossed_one.value.code == INVALID_CURSOR
    with pytest.raises(ApiError) as crossed_two:
        decode_time_cursor(encode_cursor(5))
    assert crossed_two.value.code == INVALID_CURSOR
    with pytest.raises(ApiError) as crossed_three:
        decode_cursor(encode_capture_cursor(1, 1_700_000_000_000, 5, 12))
    assert crossed_three.value.code == INVALID_CURSOR
    with pytest.raises(ApiError) as crossed_four:
        decode_time_cursor(encode_capture_cursor(1, 1_700_000_000_000, 5, 12))
    assert crossed_four.value.code == INVALID_CURSOR
    with pytest.raises(ApiError) as crossed_five:
        decode_capture_cursor(encode_cursor(5))
    assert crossed_five.value.code == INVALID_CURSOR
    with pytest.raises(ApiError) as crossed_six:
        decode_capture_cursor(encode_time_cursor(1_700_000_000_000, 7))
    assert crossed_six.value.code == INVALID_CURSOR
    with pytest.raises(ApiError) as bad_known:
        decode_capture_cursor(encode_capture_cursor(2, 0, 5, 12))
    assert bad_known.value.code == INVALID_CURSOR
    assert_valid(capture_cursor(1, 1_700_000_000_000, 5, 12), "CaptureCursor")
    assert_valid(capture_cursor(0, 0, 5, 12), "CaptureCursor")
    assert_invalid(
        {**capture_cursor(1, 1_700_000_000_000, 5, 12), "prefix": TIME_CURSOR_PREFIX},
        "CaptureCursor",
    )
    with pytest.raises(ApiError) as tail_with_sort:
        decode_capture_cursor(encode_capture_cursor(0, 99, 5, 12))
    assert tail_with_sort.value.code == INVALID_CURSOR
    with pytest.raises(ApiError) as zero_asset:
        decode_capture_cursor(encode_capture_cursor(1, 0, 0, 12))
    assert zero_asset.value.code == INVALID_CURSOR

    # P4-00B: the *production* codec is the same frozen shape, and it refuses the same three
    # malformed positions plus a cursor minted by either of the other two orders.
    assert live_decode_capture_cursor(
        live_encode_capture_cursor(1, 1_700_000_000_000, 5, 12)
    ) == (1, 1_700_000_000_000, 5, 12)
    assert live_decode_capture_cursor(live_encode_capture_cursor(0, 0, 5, 12)) == (0, 0, 5, 12)
    for probe in ((2, 0, 5, 12), (0, 99, 5, 12), (1, 0, 0, 12)):
        with pytest.raises(ApiError) as refused:
            live_decode_capture_cursor(live_encode_capture_cursor(*probe))
        assert refused.value.code == INVALID_CURSOR
    with pytest.raises(ApiError) as crossed_live_one:
        live_decode_capture_cursor(encode_cursor(5))
    assert crossed_live_one.value.code == INVALID_CURSOR
    with pytest.raises(ApiError) as crossed_live_two:
        live_decode_capture_cursor(encode_time_cursor(1_700_000_000_000, 7))
    assert crossed_live_two.value.code == INVALID_CURSOR
    with pytest.raises(ApiError) as crossed_live_three:
        decode_cursor(live_encode_capture_cursor(1, 1_700_000_000_000, 5, 12))
    assert crossed_live_three.value.code == INVALID_CURSOR
    with pytest.raises(ApiError) as crossed_live_four:
        decode_time_cursor(live_encode_capture_cursor(1, 1_700_000_000_000, 5, 12))
    assert crossed_live_four.value.code == INVALID_CURSOR


def test_the_capture_cursor_freezes_the_three_probed_invariants() -> None:
    """The three objects the P4-00A stage B probe found acceptable, now all refused."""
    assert SCHEMA["$defs"]["CaptureCursor"]["required"] == ["prefix", "arity", "values"]
    assert SCHEMA["$defs"]["CaptureCursor"]["additionalProperties"] is False
    assert SCHEMA["$defs"]["CaptureCursorKnown"]["enum"] == [0, 1]
    values_schema = SCHEMA["$defs"]["CaptureCursor"]["properties"]["values"]
    assert values_schema["minItems"] == values_schema["maxItems"] == CAPTURE_CURSOR_ARITY
    assert len(values_schema["prefixItems"]) == CAPTURE_CURSOR_ARITY
    # 1. known outside {0, 1}.
    assert_invalid(capture_cursor(2, 0, 5, 12), "CaptureCursor")
    # 2. the tail bucket must write 0 in the sort element.
    assert_invalid(capture_cursor(0, 99, 5, 12), "CaptureCursor")
    # 3. an asset id of 0 is not a position.
    assert_invalid(capture_cursor(1, 0, 0, 12), "CaptureCursor")
    # 4. the removed duplicate: a second revision field can never disagree with values[3].
    assert_invalid(
        {**capture_cursor(1, 1_700_000_000_000, 5, 12), "catalog_filter_revision": 13},
        "CaptureCursor",
    )
    # A wrong arity, a wrong prefix and a negative element stay refused as well.
    assert_invalid(capture_cursor(1, 1_700_000_000_000, 5, 12) | {"arity": 3}, "CaptureCursor")
    assert_invalid(
        {**capture_cursor(1, 1_700_000_000_000, 5, 12), "values": [1, 1_700_000_000_000, 5]},
        "CaptureCursor",
    )
    assert_invalid(capture_cursor(1, 1_700_000_000_000, 5, 12) | {"values": [1, -1, 5, 12]},
                   "CaptureCursor")


def test_the_frozen_time_desc_ordering_is_untouched() -> None:
    source = (ROOT / "src" / "photo_search" / "api" / "queries.py").read_text(encoding="utf-8")
    assert "a.created_at DESC, a.asset_id DESC" in source
    assert 'TIME_CURSOR_PREFIX = "t1"' in source
    assert 'CURSOR_PREFIX = "v1"' in source
    assert "ORDER_TIME_DESC = \"time_desc\"" in source
    assert decode_time_cursor(encode_time_cursor(5, 9)) == (5, 9)
    assert decode_cursor(encode_cursor(9)) == 9
    assert "`order=time_desc`（加入时间）" in ADR
    assert "逐字不变" in ADR


# ------------------------------------------------- single AND set, single revision, digest
def test_the_capture_condition_joins_the_one_and_set_and_the_one_revision() -> None:
    echo = {
        "source_id": None,
        "availability": "any",
        "place_ids": [4],
        "include_descendants": True,
        "place_state": "any",
        "expanded_place_ids": [4],
        "catalog_filter_revision": 12,
        "suggestion_revision": 3,
        "binding": None,
        "person_ids": [3],
        "person_match": "any",
        "capture_from": "2022-01-01T00:00:00.000",
        "capture_to_exclusive": None,
    }
    assert_valid(echo, "SearchFiltersEchoV2")
    assert set(SCHEMA["$defs"]["SearchFiltersEchoV2"]["required"]) == set(
        P3_FILTERS_ECHO_KEYS
    ) | set(CAPTURE_ECHO_KEYS)
    assert set(P3_FILTERS_ECHO_KEYS) - set(echo) == set()
    assert_invalid({key: echo[key] for key in P3_FILTERS_ECHO_KEYS}, "SearchFiltersEchoV2")
    assert_invalid({**echo, "capture_filter_revision": 1}, "SearchFiltersEchoV2")
    assert_invalid({**echo, "time_revision": 1}, "SearchFiltersEchoV2")
    assert_invalid({**echo, "capture_from": "2022/01/01"}, "SearchFiltersEchoV2")
    assert_echo_carries_exactly_two_revisions(echo)


def assert_echo_carries_exactly_two_revisions(echo: dict[str, Any]) -> None:
    revisions = [name for name in echo if "revision" in name]
    assert revisions == ["catalog_filter_revision", "suggestion_revision"]
    blob = json.dumps(SCHEMA, ensure_ascii=False).lower()
    for forbidden in ("capture_revision", "time_revision", "capture_filter_revision"):
        assert forbidden not in blob


def test_the_continuation_digest_adds_exactly_the_two_capture_bounds() -> None:
    import inspect

    live = tuple(inspect.signature(query_hash).parameters)
    for name in P3_DIGEST_FIELDS:
        assert name in live, name
    for name in CAPTURE_DIGEST_FIELDS:
        # P4-00B implemented ADR-0011 §6.5: the two canonical bounds are live parameters now,
        # and they are carried by the very same payload the reference below builds.
        assert name in live
        assert name in ADR
    assert "**加上**两个规范化 capture 字段" in ADR
    base: dict[str, Any] = {
        "text": "海边",
        "source_id": None,
        "availability": "any",
        "place_ids": [4],
        "include_descendants": True,
        "place_state": "any",
        "top_k": 100,
        "page_size": 20,
        "person_ids": [3],
        "person_match": "any",
    }
    year_2022 = capture_digest(**base, capture_from="2022-01-01", capture_to_exclusive="2023-01-01")
    normalized = capture_digest(
        **base,
        capture_from=normalize_capture_bound("2022-01-01"),
        capture_to_exclusive=normalize_capture_bound("2023-01-01"),
    )
    year_2021 = capture_digest(**base, capture_from="2021-01-01", capture_to_exclusive="2022-01-01")
    no_range = capture_digest(**base, capture_from=None, capture_to_exclusive=None)
    open_left = capture_digest(**base, capture_from=None, capture_to_exclusive="2023-01-01")
    assert year_2022 == normalized
    assert len({year_2022, year_2021, no_range, open_left}) == 4
    # The live digest is byte-for-byte the frozen payload: the reference (which normalizes its
    # own bounds) and the production function (which is handed the canonical spellings) agree,
    # and two spellings of one interval still collapse into one digest.
    live_year_2022 = query_hash(
        **base,
        capture_from=normalize_capture_bound("2022-01-01"),
        capture_to_exclusive=normalize_capture_bound("2023-01-01"),
    )
    assert live_year_2022 == year_2022
    assert (
        query_hash(
            **base,
            capture_from=normalize_capture_bound("2022-01-01T00:00:00"),
            capture_to_exclusive=normalize_capture_bound("2023-01-01"),
        )
        == live_year_2022
    )
    assert len(
        {
            live_year_2022,
            query_hash(**base, capture_from=None, capture_to_exclusive=None),
            query_hash(
                **base,
                capture_from=normalize_capture_bound("2021-01-01"),
                capture_to_exclusive=normalize_capture_bound("2022-01-01"),
            ),
            query_hash(
                **base,
                capture_from=None,
                capture_to_exclusive=normalize_capture_bound("2023-01-01"),
            ),
        }
    ) == 4


def test_the_time_error_details_add_no_key_to_the_frozen_vocabulary() -> None:
    p3_details = json.loads(
        (CONTRACTS / "p3-person-search-contracts.v1.json").read_text(encoding="utf-8")
    )["$defs"]["PersonSearchErrorDetails"]["properties"]
    p4_details = SCHEMA["$defs"]["TimeErrorDetails"]["properties"]
    assert set(p4_details) <= set(p3_details)
    assert "INVALID_TIME_RANGE" in SCHEMA["$defs"]["TimeApiError"]["properties"]["code"]["enum"]
    assert "INVALID_TIME_RANGE" not in json.loads(
        (CONTRACTS / "p3-person-search-contracts.v1.json").read_text(encoding="utf-8")
    )["$defs"]["PersonSearchApiError"]["properties"]["code"]["enum"]
    assert_valid(
        {
            "code": "CATALOG_FILTER_REVISION_STALE",
            "message": "the capture ordering moved",
            "request_id": "r-1",
            "details": {
                "reason": "CATALOG_FILTER_REVISION_CHANGED",
                "current_catalog_filter_revision": 13,
            },
        },
        "TimeApiError",
    )
    assert_invalid(
        {
            "code": "INVALID_TIME_RANGE",
            "message": "bad",
            "request_id": "r-1",
            "details": {"reason": "something_else", "field": "capture_from"},
        },
        "TimeApiError",
    )
    for private in ("path", "taken_at_raw", "utc", "latitude", "serial_number"):
        assert_invalid(
            {
                "code": "INVALID_TIME_RANGE",
                "message": "bad",
                "request_id": "r-1",
                "details": {"reason": "capture_bound_malformed", private: "x"},
            },
            "TimeApiError",
        )


def test_the_honesty_block_state_is_the_pure_function_of_the_three_counts() -> None:
    cases = [
        (0, 0, 0, "READY"),
        (1000, 0, 0, "PENDING"),
        (1000, 400, 200, "PARTIAL"),
        (1000, 1000, 10, "READY"),
        (1000, 1000, 0, "NO_CAPTURE_TIME"),
    ]
    for total, extracted, captured, expected in cases:
        block = normalize_taken_time_block(
            assets_total=total,
            extracted_assets=extracted,
            capture_time_assets=captured,
            rules_version_matches=True,
        )
        assert block["state"] == expected
        assert_valid(block, "TakenTimeBlock")
        assert taken_time_state(total, extracted, captured) == expected
    # Stage B boundary 7: the rule-version gate is expressed as count normalization, so the
    # same three counts can never yield two different states.
    mismatched = normalize_taken_time_block(
        assets_total=10,
        extracted_assets=10,
        capture_time_assets=1,
        rules_version_matches=False,
    )
    assert mismatched == {
        "state": "PENDING",
        "assets_total": 10,
        "extracted_assets": 0,
        "capture_time_assets": 0,
    }
    assert_valid(mismatched, "TakenTimeBlock")
    assert mismatched["state"] == taken_time_state(10, 0, 0)
    assert normalize_taken_time_block(
        assets_total=0,
        extracted_assets=0,
        capture_time_assets=0,
        rules_version_matches=False,
    ) == {"state": "READY", "assets_total": 0, "extracted_assets": 0, "capture_time_assets": 0}
    assert set(SCHEMA["$defs"]["TakenTimeBlock"]["properties"]) == {
        "state",
        "assets_total",
        "extracted_assets",
        "capture_time_assets",
    }
    assert "规范化为 `0`" in ADR
    assert_invalid(
        {
            "state": "READY",
            "assets_total": 1000,
            "extracted_assets": 1000,
            "capture_time_assets": 0,
        },
        "TakenTimeBlock",
    )
    assert_invalid(
        {
            "state": "PARTIAL",
            "assets_total": 10,
            "extracted_assets": 20,
            "capture_time_assets": 5,
        },
        "TakenTimeBlock",
    )
    assert_invalid(
        {"state": "PENDING", "assets_total": 10, "extracted_assets": 0}, "TakenTimeBlock"
    )


# ------------------------------------------------------------------------- privacy closure
def test_the_companion_names_no_private_property_anywhere() -> None:
    forbidden = (
        "taken_at_raw",
        "raw_exif",
        "exif_blob",
        "instant",
        "utc_time",
        "path",
        "latitude",
        "longitude",
        "coordinate",
        "gps",
        "serial",
        "owner",
        "artist",
        "copyright",
        "user_comment",
    )
    names: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                names.append(str(key))
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(SCHEMA)
    for word in forbidden:
        assert not [name for name in names if word in name.lower()], word
    detail_keys = set(SCHEMA["$defs"]["AssetTakenTimeBlock"]["properties"])
    assert detail_keys == {
        "taken_at",
        "taken_at_sort",
        "time_source",
        "time_confidence",
        "timezone_offset_minutes",
        "label",
        "camera",
    }
    assert "taken_at_raw" not in detail_keys
    assert SCHEMA["$defs"]["AssetTakenTimeBlock"]["properties"]["camera"]["oneOf"][0][
        "maxLength"
    ] == 128
    for phrase in (
        "`taken_at_raw`",
        "BodySerialNumber",
        "LensSerialNumber",
        "OwnerName",
        "Copyright",
        "`source_roots.root_path`",
    ):
        assert phrase in ADR, phrase


def test_the_companion_does_not_restate_the_frozen_p3_blocks() -> None:
    definitions = set(SCHEMA["$defs"])
    for forbidden in (
        "SearchRequest",
        "SearchResult",
        "SearchProjectionBlock",
        "PendingSemanticPage",
        "PersonCondition",
        "JobRequest",
        "ProcessorSpec",
        "SearchProfile",
    ):
        assert forbidden not in definitions
    assert "SearchFiltersEchoV2" in definitions
    assert definitions & {"SearchFiltersEcho"} == set()


# ----------------------------------------------------------------- ADR text and evidence
def test_the_adr_freezes_each_contract_decision() -> None:
    for phrase in (
        "civil key",
        "**不是** instant",
        "`taken_at_sort`",
        "`taken_at_raw`",
        "`CAPTURE_TIME_CHANGED`",
        "`INVALID_TIME_RANGE`",
        "`capture_bound_has_offset`",
        "`capture_range_not_ascending`",
        "`capture_desc`",
        "前缀 `c1`，arity 4",
        "前缀 `t1` 二元",
        "左闭右开",
        "不得伪装成拍摄时间",
        "`SearchFiltersEchoV2`",
        "加性取代",
        "test_the_frozen_echo_keys_are_the_implemented_echo_keys",
        "`taken_time_rules_version`",
        "`0015_taken_time.sql`",
        "`SUPPORTED_SCHEMA_VERSION`",
        "只允许一套 revision",
        "`EXIF_ROOT`",
        "`FILE_MTIME`",
        "get_ifd(0x8769)",
        "无偏移的本地时间不得当 UTC",
        "SOL FREEZE PENDING",
    ):
        assert phrase in ADR, phrase
    assert "不修改任何 `src/photo_search/**` 生产代码" in ADR
    assert "`order=time_desc`（加入时间）" in ADR


def test_pillow_exif_time_reading_paths_are_the_frozen_ones(tmp_path: Path) -> None:
    target = tmp_path / "synthetic-exif-time.jpg"
    image = Image.new("RGB", (16, 16), (10, 20, 30))
    exif = Image.Exif()
    exif[0x0132] = "2022:07:05 18:31:07"
    exif[0x010F] = "TestMake"
    exif[0x0110] = "TestModel"
    exif[0x8769] = {
        0x9003: "2022:07:05 18:31:07",
        0x9004: "2022:07:05 18:31:08",
        0x9011: "+08:00",
        0x9291: "123",
    }
    image.save(target, format="JPEG", exif=exif)

    with Image.open(target) as source:
        loaded = source.getexif()
        root = dict(loaded)
        sub = dict(loaded.get_ifd(0x8769))

    assert loaded.get(0x0132) == "2022:07:05 18:31:07"
    assert (loaded.get(0x010F), loaded.get(0x0110)) == ("TestMake", "TestModel")
    # The trap the ADR documents: the capture fields live in the Exif sub-IFD, so a root
    # ``get`` finds nothing and a naive reader would silently fall back to the file time.
    assert loaded.get(0x9003) is None
    assert "0x8769" in {hex(key) for key in root}
    assert sub[0x9003] == "2022:07:05 18:31:07"
    assert sub[0x9004] == "2022:07:05 18:31:08"
    assert sub[0x9011] == "+08:00"
    assert sub[0x9291] == "123"
    for tag in ("0x9003", "0x9004", "0x9011", "0x9012", "0x9010", "0x9291", "0x9292"):
        assert tag in ADR, tag
    assert "只需文件头、无需解码像素" in ADR


def test_the_contract_lists_the_p4_00b_acceptance_prerequisites() -> None:
    for phrase in (
        "`0014 → 0015`",
        "`CAPTURE_TIME_CHANGED`",
        "`taken_time_rules_version`",
        "记录式真实浏览器",
        "collect_data_files('photo_search')",
        "C 级必须执行的打包验收",
        "`0001`～`0015`",
        "`SCHEMA_INCONSISTENT`",
        "test-results/p4-00b-full.xml",
    ):
        assert phrase in ADR, phrase
    assert "P4-00B" in ADR and "P4-00A" in ADR
    assert "只冻结规则" in ADR
    assert "D + C + 浏览器" in ADR
    assert "不触发 C 级" not in ADR
