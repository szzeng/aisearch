"""Executable drift checks for the P3-07A ADR-0009 companion.

These tests validate the draft-2020-12 subset used by the checked-in JSON companion, the
cross-item rules JSON Schema cannot express (canonical de-duplication, the ``SYNCED``
projection identity, the person-filter error reasons) and a reference model of the frozen
semantics.

P3-07A itself added no production code. P3-07B implements the contract, so the last section
below additionally pins the *implementation* to the frozen shapes: the two request fields,
the two error reasons, the bounds and the echoed key set. Those assertions are additive -
they never weaken the P3-07A reference model above them.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from photo_search.catalog.errors import INVALID_PERSON_FILTER, CatalogError
from photo_search.persons.members import MEMBER_PREDICATE, member_counts
from photo_search.search.contracts import MAX_PERSON_IDS as REQUEST_MAX_PERSON_IDS
from photo_search.search.contracts import SearchRequest
from photo_search.search.retrieval import MAX_PERSON_IDS as RETRIEVAL_MAX_PERSON_IDS
from photo_search.search.retrieval import SearchScope, scope_view
from photo_search.search.sessions import SESSION_TTL_MS as SESSION_TTL_MS_IMPL

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = (
    ROOT / "docs" / "decisions" / "contracts" / "p3-person-search-contracts.v1.json"
)
ADR_PATH = ROOT / "docs" / "decisions" / "ADR-0009-person-place-combined-search-contract.md"

#: The four companions this task promises to leave byte-identical (ADR-0009 §1).
FROZEN_COMPANIONS = {
    "p2-contracts.v1.json": (
        "4270997cac64a3c19c80f1d118b5e3d16022fdccb2c0a454c21d43d7730325fa"
    ),
    "p2-place-contracts.v1.json": (
        "192db8ca53a9aaef835872187c4afbbfdbe2ad7f0fffa56a28061f86f84221fc"
    ),
    "p3-face-correction-contracts.v1.json": (
        "d6d2e9f2e27bf9d7312cef6b5c4c1f24b7bf33e7003289d506d64c4723235a73"
    ),
    "p3-person-split-contracts.v1.json": (
        "462f09358a7088ad1634b096c50fe297cfb85a9c87d8bff7f0725451af89b2ff"
    ),
}

CONTRACT_VERSION = "p3-person-search-contracts.v1"
SEARCH_CONTRACT_VERSION = "p2-contracts.v1"
MAX_PERSON_IDS = 100
MAX_TOP_UP = 200
MAX_AUTHORITY_PAGE = 1000
SESSION_TTL_MS = 600000

SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
ADR = ADR_PATH.read_text(encoding="utf-8")


class ContractValidationError(AssertionError):
    """A fixture does not satisfy the checked schema and semantic subset."""


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
    elif "else" in schema:
        errors.extend(_errors(instance, schema["else"], path))

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


def contract_errors(instance: dict[str, Any], definition: str) -> list[str]:
    """The schema subset plus the cross-item rules the JSON cannot express."""
    errors = _errors(instance, {"$ref": f"#/$defs/{definition}"})
    if definition == "PersonCondition":
        ids = instance.get("person_ids")
        if instance.get("person_match") == "all" and not ids:
            errors.append("condition: all requires at least one person id")
    if definition == "SearchFiltersEcho":
        ids = instance.get("person_ids", [])
        if ids != sorted(ids):
            errors.append("echo: person_ids must be canonical ascending")
    if definition == "SearchProjectionBlock":
        queued = instance.get("queued_assets")
        if (instance.get("state") == "SYNCED") != (queued == 0):
            errors.append("projection: SYNCED is exactly queued_assets == 0")
    if definition == "PendingSemanticPage":
        if instance.get("count") != len(instance.get("entries", [])):
            errors.append("topup: count must equal the number of entries")
        if instance.get("truncated") and instance.get("count") != instance.get("limit"):
            errors.append("topup: a truncated page is exactly a full page")
    if definition == "PersonSearchApiError":
        code = instance.get("code")
        details = instance.get("details") or {}
        reason = details.get("reason")
        if code == "INVALID_PERSON_FILTER":
            if reason not in SCHEMA["$defs"]["PersonFilterReason"]["enum"]:
                errors.append("error: INVALID_PERSON_FILTER needs a frozen reason")
            if reason == "person_filter_too_large" and details.get("max_person_ids") != 100:
                errors.append("error: the size refusal must publish max_person_ids = 100")
        if code == "CATALOG_FILTER_REVISION_STALE" and reason != (
            "CATALOG_FILTER_REVISION_CHANGED"
        ):
            errors.append("error: the stale revision keeps its historical reason")
        if code == "SEARCH_SESSION_EXPIRED" and reason != "SESSION_NOT_LIVE":
            errors.append("error: the expired session keeps its historical reason")
        if code in ("PERSON_NOT_FOUND", "PERSON_REDIRECT_CYCLE") and "person_id" not in details:
            errors.append("error: a person refusal must name the safe person id")
    return errors


def assert_valid(instance: dict[str, Any], definition: str) -> None:
    errors = contract_errors(instance, definition)
    if errors:
        raise ContractValidationError("\n".join(errors))


def assert_invalid(instance: dict[str, Any], definition: str) -> None:
    assert contract_errors(instance, definition)


def filters_echo(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source_id": None,
        "availability": "any",
        "place_ids": [4, 9],
        "include_descendants": True,
        "place_state": "any",
        "expanded_place_ids": [4, 5, 9],
        "catalog_filter_revision": 12,
        "suggestion_revision": 3,
        "binding": None,
        "person_ids": [3],
        "person_match": "any",
    }
    payload.update(overrides)
    return payload


def projection_block(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "documents": 17,
        "queued_assets": 0,
        "stale_documents": 0,
        "tokenizer_version": 3,
        "fts_generation_id": "fts-v3",
        "state": "SYNCED",
        "processed_assets": 0,
    }
    payload.update(overrides)
    return payload


def topup_page(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "entries": [
            {
                "asset_id": 5,
                "entry_state": None,
                "pending_events": 1,
                "generation_state": "READY",
                "semantic_ready": False,
            }
        ],
        "count": 1,
        "limit": MAX_TOP_UP,
        "truncated": False,
        "generation_id": "gen-1",
        "generation_state": "READY",
        "reason": "ENTRY_NOT_CONFIRMED",
    }
    payload.update(overrides)
    return payload


def api_error(
    code: str = "INVALID_PERSON_FILTER",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "message": "the person filter is not usable",
        "request_id": "search-1",
        "details": {} if details is None else details,
    }


# ------------------------------------------------------------------- schema identity
def test_schema_identity_and_closed_top_level_surface() -> None:
    assert SCHEMA["$schema"].endswith("2020-12/schema")
    assert SCHEMA["$defs"]["ContractVersion"]["const"] == CONTRACT_VERSION
    assert SCHEMA["$defs"]["SearchContractVersion"]["const"] == SEARCH_CONTRACT_VERSION
    assert SCHEMA["$defs"]["PersonMatch"]["enum"] == ["all", "any"]
    assert SCHEMA["$defs"]["MaxPersonFilterIds"]["const"] == MAX_PERSON_IDS
    assert SCHEMA["$defs"]["MaxTopUpEntries"]["const"] == MAX_TOP_UP
    assert SCHEMA["$defs"]["MaxAuthorityPageAssets"]["const"] == MAX_AUTHORITY_PAGE
    assert SCHEMA["$defs"]["SessionTtlMs"]["const"] == SESSION_TTL_MS
    for name in (
        "PersonCondition",
        "SearchFiltersEcho",
        "SearchProjectionBlock",
        "PendingSemanticPage",
        "PersonSearchApiError",
    ):
        assert SCHEMA["$defs"][name]["additionalProperties"] is False


def test_schema_does_not_restate_the_frozen_p2_search_envelope() -> None:
    definitions = set(SCHEMA["$defs"])
    for forbidden in ("SearchRequest", "SearchResult", "SearchHit", "FusionConfig"):
        assert forbidden not in definitions
    assert set(SCHEMA["$defs"]["PersonCondition"]["properties"]) == {
        "person_ids",
        "person_match",
    }
    assert "required" not in SCHEMA["$defs"]["PersonCondition"]


def test_existing_companions_are_byte_identical() -> None:
    contracts = ROOT / "docs" / "decisions" / "contracts"
    for name, expected in FROZEN_COMPANIONS.items():
        raw = (contracts / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected, name


# ------------------------------------------------------------------- request shape
def test_person_condition_freezes_the_two_optional_fields() -> None:
    assert_valid({}, "PersonCondition")
    assert_valid({"person_ids": [3]}, "PersonCondition")
    assert_valid({"person_ids": [3], "person_match": "all"}, "PersonCondition")
    assert_valid({"person_match": "any"}, "PersonCondition")
    assert_valid({"person_ids": []}, "PersonCondition")
    assert SCHEMA["$defs"]["PersonCondition"]["additionalProperties"] is False


def test_person_match_all_requires_at_least_one_id() -> None:
    assert_invalid({"person_match": "all"}, "PersonCondition")
    assert_invalid({"person_ids": [], "person_match": "all"}, "PersonCondition")
    assert_valid({"person_ids": [7], "person_match": "all"}, "PersonCondition")


@pytest.mark.parametrize(
    "person_ids",
    [
        "3",
        3,
        {"0": 3},
        [0],
        [-1],
        [True],
        [1.5],
        ["3"],
        [None],
        [3, 3],
        list(range(1, MAX_PERSON_IDS + 2)),
    ],
    ids=[
        "string",
        "number",
        "object",
        "zero",
        "negative",
        "boolean",
        "float",
        "numeric-string",
        "null",
        "raw-duplicate",
        "too-many",
    ],
)
def test_person_ids_shape_errors_are_refused(person_ids: Any) -> None:
    assert_invalid({"person_ids": person_ids}, "PersonCondition")


def test_person_ids_bound_is_exactly_one_hundred() -> None:
    assert_valid({"person_ids": list(range(1, MAX_PERSON_IDS + 1))}, "PersonCondition")
    assert_invalid({"person_ids": list(range(1, MAX_PERSON_IDS + 2))}, "PersonCondition")
    assert SCHEMA["$defs"]["PersonIds"]["maxItems"] == MAX_PERSON_IDS
    assert SCHEMA["$defs"]["PersonIds"]["uniqueItems"] is True


# ------------------------------------------------------------------ response shape
def test_filters_echo_is_closed_and_always_names_the_person_condition() -> None:
    assert_valid(filters_echo(), "SearchFiltersEcho")
    assert_valid(filters_echo(person_ids=[], person_match="any"), "SearchFiltersEcho")
    missing = filters_echo()
    missing.pop("person_match")
    assert_invalid(missing, "SearchFiltersEcho")
    assert_invalid(filters_echo(person_ids=[9, 3]), "SearchFiltersEcho")
    assert_invalid(filters_echo(person_ids=[3, 3]), "SearchFiltersEcho")
    assert_invalid(filters_echo(person_ids=[0]), "SearchFiltersEcho")
    for private_field in ("name", "name_key", "aliases", "bbox", "landmarks", "similarity"):
        assert_invalid(filters_echo(**{private_field: "x"}), "SearchFiltersEcho")


def test_filters_echo_keeps_the_frozen_place_and_binding_fields() -> None:
    assert set(filters_echo()) == {
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
    }
    assert_valid(
        filters_echo(
            binding={
                "generation_id": "gen-1",
                "space_id": "space-1",
                "dimension": 512,
                "processor_id": "clip@1",
                "generation_state": "READY",
                "is_active": True,
                "semantic_complete": True,
            }
        ),
        "SearchFiltersEcho",
    )
    assert_invalid(
        filters_echo(
            binding={
                "generation_id": "gen-1",
                "space_id": "space-1",
                "dimension": 512,
                "processor_id": "clip@1",
                "generation_state": "READY",
            }
        ),
        "SearchFiltersEcho",
    )


def test_projection_block_is_field_level_frozen_and_carries_no_person_key() -> None:
    assert_valid(projection_block(), "SearchProjectionBlock")
    assert_valid(projection_block(queued_assets=4, state="LAGGING"), "SearchProjectionBlock")
    assert_valid(
        projection_block(terms=["海边"], match_expression='\"海边\"'),
        "SearchProjectionBlock",
    )
    assert_invalid(projection_block(queued_assets=4), "SearchProjectionBlock")
    assert_invalid(projection_block(state="LAGGING"), "SearchProjectionBlock")
    assert_invalid(projection_block(state="FRESH"), "SearchProjectionBlock")
    assert_invalid(projection_block(processed_assets=-1), "SearchProjectionBlock")
    for extra in ("person_ids", "person_match", "person_lag", "persons"):
        assert_invalid(projection_block(**{extra: []}), "SearchProjectionBlock")


def test_top_up_page_freezes_the_bounded_page_shape() -> None:
    assert_valid(topup_page(), "PendingSemanticPage")
    assert_valid(
        topup_page(reason="GENERATION_NOT_READY", generation_state="BUILDING"),
        "PendingSemanticPage",
    )
    assert_invalid(topup_page(count=2), "PendingSemanticPage")
    assert_invalid(topup_page(limit=MAX_TOP_UP + 1), "PendingSemanticPage")
    assert_invalid(topup_page(reason="STALE"), "PendingSemanticPage")
    assert_invalid(topup_page(entries=[{"asset_id": 1}]), "PendingSemanticPage")
    assert_invalid(
        topup_page(
            truncated=True,
            count=4,
            entries=[
                {
                    "asset_id": index,
                    "entry_state": "PENDING_REPAIR",
                    "pending_events": 1,
                    "generation_state": "READY",
                    "semantic_ready": False,
                }
                for index in range(1, 5)
            ],
        ),
        "PendingSemanticPage",
    )
    assert_invalid(
        topup_page(
            entries=[
                {
                    "asset_id": 5,
                    "entry_state": None,
                    "pending_events": 1,
                    "generation_state": "READY",
                    "semantic_ready": True,
                }
            ]
        ),
        "PendingSemanticPage",
    )


# ------------------------------------------------------------------------ errors
def test_error_vocabulary_and_person_reasons_are_closed() -> None:
    expected = {
        "VALIDATION_ERROR",
        "INVALID_PERSON_FILTER",
        "PERSON_NOT_FOUND",
        "PERSON_REDIRECT_CYCLE",
        "CATALOG_FILTER_REVISION_STALE",
        "SEARCH_SESSION_EXPIRED",
        "INVALID_CURSOR",
        "SCHEMA_TOO_NEW",
        "SCHEMA_INCONSISTENT",
        "INVALID_PLACE_FILTER",
    }
    assert set(SCHEMA["$defs"]["PersonSearchApiError"]["properties"]["code"]["enum"]) == expected
    assert set(SCHEMA["$defs"]["PersonFilterReason"]["enum"]) == {
        "person_match_all_requires_ids",
        "person_filter_too_large",
    }
    assert_valid(
        api_error(details={"reason": "person_match_all_requires_ids", "person_match": "all"}),
        "PersonSearchApiError",
    )
    assert_valid(
        api_error(details={"reason": "person_filter_too_large", "max_person_ids": 100}),
        "PersonSearchApiError",
    )
    assert_valid(api_error("PERSON_NOT_FOUND", {"person_id": 7}), "PersonSearchApiError")
    assert_valid(
        api_error("PERSON_REDIRECT_CYCLE", {"person_id": 7, "steps": 3}),
        "PersonSearchApiError",
    )
    assert_valid(
        api_error(
            "CATALOG_FILTER_REVISION_STALE",
            {
                "reason": "CATALOG_FILTER_REVISION_CHANGED",
                "search_session_id": "s-1",
                "session_catalog_filter_revision": 12,
                "current_catalog_filter_revision": 13,
            },
        ),
        "PersonSearchApiError",
    )
    assert_valid(
        api_error(
            "SEARCH_SESSION_EXPIRED",
            {"reason": "SESSION_NOT_LIVE", "ttl_ms": SESSION_TTL_MS},
        ),
        "PersonSearchApiError",
    )
    assert_valid(
        api_error(
            "VALIDATION_ERROR",
            {"errors": [{"loc": ["body", "person_ids"], "msg": "bad", "type": "value_error"}]},
        ),
        "PersonSearchApiError",
    )


@pytest.mark.parametrize(
    "details",
    [
        {"reason": "something_else"},
        {"reason": "person_filter_too_large"},
        {"reason": "person_filter_too_large", "max_person_ids": 500},
    ],
    ids=["unknown-reason", "missing-bound", "wrong-bound"],
)
def test_invalid_person_filter_refuses_unfrozen_details(details: dict[str, Any]) -> None:
    assert_invalid(api_error(details=details), "PersonSearchApiError")


def test_private_fields_are_refused_in_the_person_search_error_details() -> None:
    for private_field in (
        "path",
        "name",
        "name_key",
        "aliases",
        "bbox",
        "landmarks",
        "vector",
        "confidence",
        "similarity",
        "payload_sha256",
        "before_value_json",
    ):
        assert_invalid(
            api_error(details={"reason": "person_match_all_requires_ids", private_field: "x"}),
            "PersonSearchApiError",
        )
    assert_invalid(api_error("UNKNOWN"), "PersonSearchApiError")


def test_companion_names_no_private_property_anywhere() -> None:
    forbidden = (
        "bbox",
        "landmark",
        "similarity",
        "confidence",
        "name_key",
        "alias",
        "embedding",
        "payload_sha256",
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


# -------------------------------------------------------------- reference semantics
def resolve_canonical(person_id: int, redirects: dict[int, int]) -> int:
    """The ADR-0006 walk: never repair, never guess, refuse a cycle or a dangling target."""
    seen: set[int] = {person_id}
    current = person_id
    for _ in range(len(redirects) + 1):
        target = redirects.get(current)
        if target is None:
            return current
        if target in seen:
            raise ValueError("PERSON_REDIRECT_CYCLE")
        seen.add(target)
        current = target
    raise ValueError("PERSON_REDIRECT_CYCLE")


def canonical_person_ids(
    person_ids: list[int], redirects: dict[int, int] | None = None
) -> list[int]:
    mapping = redirects or {}
    return sorted({resolve_canonical(value, mapping) for value in person_ids})


def person_hit_set(
    members: dict[int, set[int]], person_ids: list[int], match: str
) -> set[int] | None:
    """``None`` means "no person condition", which is not "photos without people"."""
    if not person_ids:
        return None
    sets = [set(members.get(person_id, set())) for person_id in person_ids]
    if match == "all":
        return set.intersection(*sets)
    return set.union(*sets)


def place_filter_is_valid(place_ids: list[int], place_state: str) -> bool:
    """ADR-0005 §10, unchanged: the place ids only combine with any/assigned."""
    return not place_ids or place_state in ("any", "assigned")


def query_digest(
    *,
    text: str | None,
    source_id: int | None,
    availability: str,
    place_ids: list[int],
    place_state: str,
    person_ids: list[int],
    person_match: str,
    redirects: dict[int, int] | None = None,
) -> str:
    """The frozen continuation digest of ADR-0009 §6.2 (person condition included)."""
    payload = {
        "text": text,
        "source_id": source_id,
        "availability": availability,
        "place_ids": list(place_ids),
        "place_state": place_state,
        "person_ids": canonical_person_ids(person_ids, redirects),
        "person_match": person_match,
    }
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def test_canonical_resolution_dedupes_merged_and_repeated_conditions() -> None:
    redirects = {7: 3, 11: 7}
    assert canonical_person_ids([3, 7], redirects) == [3]
    assert canonical_person_ids([11, 3], redirects) == [3]
    assert canonical_person_ids([9, 3], redirects) == [3, 9]
    assert canonical_person_ids([], redirects) == []
    with pytest.raises(ValueError):
        canonical_person_ids([5, 6], {5: 6, 6: 5})
    with pytest.raises(ValueError):
        canonical_person_ids([5], {5: 5})


def test_all_and_any_hit_sets_use_the_effective_member_set() -> None:
    members = {3: {1, 2, 4}, 7: {2, 4}, 9: {5}}
    assert person_hit_set(members, [], "any") is None
    assert person_hit_set(members, [3], "any") == {1, 2, 4}
    assert person_hit_set(members, [3], "all") == {1, 2, 4}
    assert person_hit_set(members, [3, 7], "all") == {2, 4}
    assert person_hit_set(members, [3, 7], "any") == {1, 2, 4}
    assert person_hit_set(members, [3, 9], "all") == set()
    assert person_hit_set(members, [3, 9], "any") == {1, 2, 4, 5}
    redirects = {7: 3}
    assert person_hit_set(members, canonical_person_ids([3, 7], redirects), "all") == (
        person_hit_set(members, [3], "all")
    )
    assert person_hit_set(members, canonical_person_ids([3, 7], redirects), "any") == (
        person_hit_set(members, [3], "any")
    )


def test_person_condition_ands_with_every_other_condition() -> None:
    members = {3: {1, 2, 4}, 7: {2, 4}}
    place_hits = {2, 4, 6}
    person_hits = person_hit_set(members, [3], "any") or set()
    assert person_hits & place_hits == {2, 4}
    assert person_hits & {6} == set()
    assert (person_hit_set(members, [3, 7], "all") or set()) & {4} == {4}
    assert (person_hit_set(members, [3, 7], "all") or set()) & {1, 6} == set()


def test_place_state_matrix_is_unchanged_by_the_person_condition() -> None:
    for state in ("any", "assigned"):
        assert place_filter_is_valid([4], state)
    for state in ("unassigned", "manual_unassigned", "suggested"):
        assert not place_filter_is_valid([4], state)
        assert place_filter_is_valid([], state)


def test_continuation_binds_one_revision_and_the_person_condition() -> None:
    def continuation(session_revision: int, current_revision: int) -> str:
        return "OK" if session_revision == current_revision else "CATALOG_FILTER_REVISION_STALE"

    assert continuation(12, 12) == "OK"
    assert continuation(12, 13) == "CATALOG_FILTER_REVISION_STALE"
    base = {
        "text": "海边",
        "source_id": None,
        "availability": "any",
        "place_ids": [4],
        "place_state": "any",
    }
    redirects = {7: 3}
    any_digest = query_digest(**base, person_ids=[3, 7], person_match="any", redirects=redirects)
    all_digest = query_digest(**base, person_ids=[3, 7], person_match="all", redirects=redirects)
    reordered = query_digest(**base, person_ids=[7, 3], person_match="any", redirects=redirects)
    canonical = query_digest(**base, person_ids=[3], person_match="any", redirects=redirects)
    no_person = query_digest(**base, person_ids=[], person_match="any", redirects=redirects)
    assert any_digest != all_digest
    assert any_digest == reordered
    assert any_digest == canonical
    assert any_digest != no_person


# ------------------------------------------------- one effective-member definition
def test_effective_member_predicate_is_the_single_shared_definition() -> None:
    assert "l.decision = 'CONFIRMED'" in MEMBER_PREDICATE
    assert "MANUAL_UNASSIGNED" in MEMBER_PREDICATE
    assert "face_exclusions" in MEMBER_PREDICATE
    assert "e.active = 1" in MEMBER_PREDICATE
    # ADR-0009 §3.1 freezes the *same* fragment for the person search predicate.
    assert "persons/members.py" in ADR
    assert "只有一份定义" in ADR
    assert "同源同值" in ADR


def test_person_hit_set_matches_the_person_asset_count(tmp_path: Path) -> None:
    from p3_person_fixtures import PersonHarness

    harness = PersonHarness(tmp_path)
    connection = harness.catalog.connection
    try:
        person = harness.add_person(name="当前成员")
        other = harness.add_person(name="其他成员")
        assets = [harness.add_asset(f"member-{index}.jpg") for index in range(3)]
        faces = [
            harness.add_face(assets[0]),
            harness.add_face(assets[0], ordinal=1),
            harness.add_face(assets[1]),
            harness.add_face(assets[1], ordinal=1),
            harness.add_face(assets[2]),
        ]
        for face_id in faces[:3]:
            harness.add_link(face_id, person)
        harness.add_link(faces[3], person, decision="SUGGESTED")
        harness.add_link(faces[4], other)
        harness.set_control(faces[0], "MANUAL_UNASSIGNED")
        harness.set_exclusion(faces[1], person)

        faces_count, assets_count = member_counts(connection, person)
        assert (faces_count, assets_count) == (1, 1)
        rows = connection.execute(
            "SELECT DISTINCT f.asset_id FROM face_person_links AS l "
            "JOIN faces AS f ON f.face_id = l.face_id "
            f"WHERE l.person_id = ? AND {MEMBER_PREDICATE} ORDER BY f.asset_id",
            (person,),
        ).fetchall()
        hit_set = {int(row["asset_id"]) for row in rows}
        assert hit_set == {assets[1]}
        assert len(hit_set) == assets_count
        assert hit_set == person_hit_set({person: {assets[1]}}, [person], "all")
        assert person_hit_set({person: {assets[1]}}, [person], "all") == (
            person_hit_set({person: {assets[1]}}, [person], "any")
        )
    finally:
        harness.close()


# --------------------------------------------------------------------------- ADR
def test_adr_freezes_the_single_surface_and_the_two_additive_fields() -> None:
    for phrase in (
        "POST /api/v1/search",
        "`person_ids`",
        "`person_match`",
        "`p2-contracts.v1`",
        "只**加性**增加两个可选人物字段",
        "不产生人物条件",
        "**不得**解释为「没有人物脸」",
        "`GET /api/v1/assets` 的地点/来源/可用性",
    ):
        assert phrase in ADR


def test_adr_freezes_the_shape_decision_table() -> None:
    for phrase in (
        "`INVALID_PERSON_FILTER`",
        "`reason=person_match_all_requires_ids`",
        "`reason=person_filter_too_large`",
        "`details.max_person_ids = 100`",
        "原始元素重复（如 `[3, 3]`）",
        "静默折叠为 `[3]`",
        "`PERSON_NOT_FOUND`",
        "`PERSON_REDIRECT_CYCLE`",
        "`VALIDATION_ERROR`",
    ):
        assert phrase in ADR


def test_adr_freezes_photo_level_all_any_and_the_and_rule() -> None:
    for phrase in (
        "`MEMBER_PREDICATE`",
        "照片级",
        "不在本阶段",
        "一律 AND",
        "`FILTER_ONLY`",
        "`TEXT+SEMANTIC`",
        "命中原因",
    ):
        assert phrase in ADR


def test_adr_freezes_no_migration_and_the_escalation_rule() -> None:
    for phrase in (
        "`schema_version` 仍为 13",
        "`0001`～`0013` 逐字节不变",
        "本 ADR 不授权任何迁移",
        "不得在 P3-07B 顺手加迁移",
        "DS-H 全量工程主审",
    ):
        assert phrase in ADR


def test_adr_freezes_one_revision_set_and_the_reused_stale_code() -> None:
    for phrase in (
        "`PERSON_CHANGED`",
        "不存在第二套 revision",
        "`CATALOG_FILTER_REVISION_STALE`",
        "选择复用而不是新码的理由",
        "`reason=CATALOG_FILTER_REVISION_CHANGED`",
        "`SEARCH_SESSION_EXPIRED`",
        "`INVALID_CURSOR`",
        "canonical、去重、升序",
    ):
        assert phrase in ADR


def test_adr_freezes_the_projection_and_top_up_bounds() -> None:
    for phrase in (
        "不新增任何字段",
        "1000",
        "200",
        "`SYNCED` **当且仅当**",
        "永远不代表人物条件的结果不完整",
        "GENERATION_NOT_READY",
        "ENTRY_NOT_CONFIRMED",
    ):
        assert phrase in ADR


def test_adr_states_the_document_only_phase_boundary() -> None:
    for phrase in (
        "只冻结规则",
        "不修改任何生产代码、migration、`schema_version`、依赖或既有测试",
        "也不声称",
        "P3-07B",
    ):
        assert phrase in ADR


# ------------------------------------- P3-07B: the implementation matches the freeze
def test_the_frozen_request_fields_are_the_implemented_request_fields() -> None:
    """The two additive fields exist, default as frozen and refuse exactly as frozen."""
    assert REQUEST_MAX_PERSON_IDS == RETRIEVAL_MAX_PERSON_IDS == MAX_PERSON_IDS
    assert SearchRequest.from_envelope(
        {"contract_version": "p2-contracts.v1", "request_id": "r"}
    ).person_ids == ()
    assert SearchRequest.from_envelope(
        {"contract_version": "p2-contracts.v1", "request_id": "r", "person_ids": [3]}
    ).person_ids == (3,)
    for overrides, expected in (
        ({"person_match": "all"}, INVALID_PERSON_FILTER),
        ({"person_ids": [], "person_match": "all"}, INVALID_PERSON_FILTER),
        ({"person_ids": list(range(1, MAX_PERSON_IDS + 2))}, INVALID_PERSON_FILTER),
        ({"person_ids": [3, 3]}, "VALIDATION_ERROR"),
        ({"person_ids": [0]}, "VALIDATION_ERROR"),
        ({"person_ids": "3"}, "VALIDATION_ERROR"),
        ({"person_match": "some"}, "VALIDATION_ERROR"),
        ({"surprise": 1}, "VALIDATION_ERROR"),
    ):
        with pytest.raises(CatalogError) as error:
            SearchRequest.from_envelope(
                {"contract_version": "p2-contracts.v1", "request_id": "r", **overrides}
            )
        assert error.value.code == expected, overrides


def test_the_frozen_reasons_and_ceiling_are_the_implemented_ones() -> None:
    with pytest.raises(CatalogError) as all_refusal:
        SearchRequest.from_envelope(
            {"contract_version": "p2-contracts.v1", "request_id": "r", "person_match": "all"}
        )
    assert all_refusal.value.details["reason"] == "person_match_all_requires_ids"
    with pytest.raises(CatalogError) as big_refusal:
        SearchRequest.from_envelope(
            {
                "contract_version": "p2-contracts.v1",
                "request_id": "r",
                "person_ids": list(range(1, MAX_PERSON_IDS + 2)),
            }
        )
    assert big_refusal.value.details == {
        "reason": "person_filter_too_large",
        "max_person_ids": MAX_PERSON_IDS,
    }
    reasons = set(SCHEMA["$defs"]["PersonFilterReason"]["enum"])
    assert {all_refusal.value.details["reason"], big_refusal.value.details["reason"]} == reasons
    assert INVALID_PERSON_FILTER in SCHEMA["$defs"]["PersonSearchApiError"]["properties"][
        "code"
    ]["enum"]


def test_the_frozen_echo_keys_are_the_implemented_echo_keys() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(
            "CREATE TABLE catalog_filter_revision (revision_id INTEGER PRIMARY KEY, "
            "revision INTEGER);"
            "CREATE TABLE place_suggestion_revision (revision_id INTEGER PRIMARY KEY, "
            "revision INTEGER);"
            "INSERT INTO catalog_filter_revision VALUES (1, 0);"
            "INSERT INTO place_suggestion_revision VALUES (1, 0);"
        )
        echo = scope_view(connection, SearchScope.build())
    finally:
        connection.close()
    # ``binding`` is added by the service when it shapes the response, so the two sets are
    # compared with that one key accounted for.
    #
    # P4-00B (ADR-0011 §8.2) extends this one assertion - and only by adding keys: the live
    # echo is now ``SearchFiltersEchoV2``, i.e. the frozen ADR-0009 eleven keys plus exactly
    # ``capture_from``/``capture_to_exclusive``. The P3 companion itself is untouched, as the
    # hash check above requires, so both the historical key set and the live superset are
    # asserted here.
    live = set(echo) | {"binding"}
    assert live == set(SCHEMA["$defs"]["SearchFiltersEcho"]["required"]) | {
        "capture_from",
        "capture_to_exclusive",
    }
    p4_schema = json.loads(
        (
            ROOT
            / "docs"
            / "decisions"
            / "contracts"
            / "p4-time-contracts.v1.json"
        ).read_text(encoding="utf-8")
    )
    assert set(p4_schema["$defs"]["SearchFiltersEchoV2"]["required"]) == live
    assert echo["person_ids"] == [] and echo["person_match"] == "any"
    assert echo["capture_from"] is None and echo["capture_to_exclusive"] is None


def test_the_frozen_bounds_are_the_implemented_bounds() -> None:
    assert REQUEST_MAX_PERSON_IDS == SCHEMA["$defs"]["MaxPersonFilterIds"]["const"]
    assert SESSION_TTL_MS == SESSION_TTL_MS_IMPL == SCHEMA["$defs"]["SessionTtlMs"]["const"]
    from photo_search.search.retrieval import AUTHORITY_PAGE_LIMIT, TOPUP_LIMIT

    assert AUTHORITY_PAGE_LIMIT == SCHEMA["$defs"]["MaxAuthorityPageAssets"]["const"]
    assert TOPUP_LIMIT == SCHEMA["$defs"]["MaxTopUpEntries"]["const"]


def test_the_person_predicate_uses_the_single_member_definition() -> None:
    """ADR-0009 §3.1: the retrieval predicate reuses ``MEMBER_PREDICATE``, not a copy."""
    from photo_search.search.retrieval import person_condition

    for scope in (
        SearchScope.build(person_ids=[3]),
        SearchScope.build(person_ids=[3], person_match="all"),
    ):
        sql, _ = person_condition(scope)
        assert MEMBER_PREDICATE in sql
        assert "face_person_links AS l" in sql and "faces AS f" in sql
