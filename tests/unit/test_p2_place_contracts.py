"""Executable drift checks for the P2-01A ADR-0005 companion schema.

No JSON Schema dependency is added by this task.  The helper below executes the exact
draft-2020-12 keyword subset used by the companion and a few explicitly named cross-row
semantics.  These are document/fixture checks, not evidence that a future database, API,
or runtime validator has been implemented.
"""

from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "docs" / "decisions" / "contracts" / "p2-place-contracts.v1.json"
CORE_SCHEMA_PATH = ROOT / "docs" / "decisions" / "contracts" / "p2-contracts.v1.json"
ADR_GLOB = "ADR-0005-*.md"
CONTRACT_VERSION = "p2-place-contracts.v1"
SHA = "a" * 64
NOW = 1_789_000_000_000


def _load_contract() -> tuple[dict[str, Any], dict[str, Any], str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    core_schema = json.loads(CORE_SCHEMA_PATH.read_text(encoding="utf-8"))
    adr_paths = sorted((ROOT / "docs" / "decisions").glob(ADR_GLOB))
    assert len(adr_paths) == 1
    return schema, core_schema, adr_paths[0].read_text(encoding="utf-8")


SCHEMA, CORE_SCHEMA, ADR = _load_contract()


class ContractValidationError(AssertionError):
    """A fixture does not satisfy the checked schema and semantic subset."""


def _resolve(document: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if ref is None:
        return schema
    prefix = "#/$defs/"
    if not isinstance(ref, str) or not ref.startswith(prefix):
        raise ContractValidationError(f"unsupported ref: {ref!r}")
    name = ref.removeprefix(prefix)
    try:
        return document["$defs"][name]
    except KeyError as error:
        raise ContractValidationError(f"missing local definition: {name}") from error


def _is_type(instance: Any, type_name: str) -> bool:
    if type_name == "null":
        return instance is None
    if type_name == "boolean":
        return isinstance(instance, bool)
    if type_name == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if type_name == "number":
        return (
            isinstance(instance, (int, float))
            and not isinstance(instance, bool)
            and math.isfinite(instance)
        )
    if type_name == "string":
        return isinstance(instance, str)
    if type_name == "array":
        return isinstance(instance, list)
    if type_name == "object":
        return isinstance(instance, dict)
    raise ContractValidationError(f"unsupported schema type: {type_name!r}")


def _errors(
    document: dict[str, Any],
    instance: Any,
    raw_schema: dict[str, Any],
    path: str = "$",
) -> list[str]:
    schema = _resolve(document, raw_schema)
    errors: list[str] = []

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value is outside enum")

    if "oneOf" in schema:
        matches = sum(not _errors(document, instance, branch, path) for branch in schema["oneOf"])
        if matches != 1:
            errors.append(f"{path}: expected exactly one oneOf branch, got {matches}")
    if "anyOf" in schema and not any(
        not _errors(document, instance, branch, path) for branch in schema["anyOf"]
    ):
        errors.append(f"{path}: expected one anyOf branch")
    for branch in schema.get("allOf", []):
        errors.extend(_errors(document, instance, branch, path))
    if "not" in schema and not _errors(document, instance, schema["not"], path):
        errors.append(f"{path}: matched forbidden schema")
    if "if" in schema and not _errors(document, instance, schema["if"], path):
        if "then" in schema:
            errors.extend(_errors(document, instance, schema["then"], path))
    elif "else" in schema:
        errors.extend(_errors(document, instance, schema["else"], path))

    expected_type = schema.get("type")
    if expected_type is not None:
        accepted = [expected_type] if isinstance(expected_type, str) else expected_type
        if not any(_is_type(instance, name) for name in accepted):
            return errors + [f"{path}: expected type {accepted!r}"]

    if isinstance(instance, dict):
        for name in schema.get("required", []):
            if name not in instance:
                errors.append(f"{path}: missing required property {name!r}")
        properties = schema.get("properties", {})
        for name, value in instance.items():
            if name in properties:
                errors.extend(_errors(document, value, properties[name], f"{path}.{name}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected property {name!r}")

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            errors.append(f"{path}: too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in instance]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: duplicate items")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(instance):
                errors.extend(_errors(document, item, item_schema, f"{path}[{index}]"))

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: string is too short")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: string is too long")
        pattern = schema.get("pattern")
        if pattern is not None and re.fullmatch(pattern, instance) is None:
            errors.append(f"{path}: string does not match {pattern!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if not math.isfinite(instance):
            errors.append(f"{path}: number is not finite")
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: above maximum")

    return errors


def _semantic_errors(instance: dict[str, Any], definition: str) -> list[str]:
    errors: list[str] = []
    if definition == "Place":
        if instance.get("parent_place_id") == instance.get("place_id"):
            errors.append("Place: parent_place_id cannot equal place_id")
    elif definition == "PlaceAssignmentSet":
        active_assets = [
            item.get("asset_id")
            for item in instance.get("assignments", [])
            if item.get("record_state") == "ACTIVE"
        ]
        if len(active_assets) != len(set(active_assets)):
            errors.append("PlaceAssignmentSet: one asset has two ACTIVE assignments")
    elif definition == "PlaceOperation":
        asset_ids = [
            item.get("asset_id")
            for item in instance.get("items", [])
            if item.get("asset_id") is not None
        ]
        if len(asset_ids) != len(set(asset_ids)):
            errors.append("PlaceOperation: asset_ids must be unique within one atomic batch")
    elif definition == "PlaceFilter" and instance.get("cursor") is not None:
        if instance.get("cursor_catalog_filter_revision") != instance.get(
            "catalog_filter_revision"
        ):
            errors.append("PlaceFilter: stale CatalogFilterRevision continuation")
        if (
            instance.get("place_state") == "suggested"
            and instance.get("cursor_suggestion_revision")
            != instance.get("suggestion_revision")
        ):
            errors.append("PlaceFilter: stale suggestion revision continuation")
    elif definition == "PlaceDatasetManifest":
        bounds = instance.get("coverage", {}).get("bounds_wgs84", {})
        if bounds.get("south", -90) > bounds.get("north", 90):
            errors.append("PlaceDatasetManifest: south cannot exceed north")
        compatible = instance.get("compatible_catalog_schema", {})
        if compatible.get("minimum", 1) > compatible.get("maximum", 1):
            errors.append("PlaceDatasetManifest: schema minimum cannot exceed maximum")
    return errors


def contract_errors(instance: dict[str, Any], definition: str) -> list[str]:
    return _errors(SCHEMA, instance, {"$ref": f"#/$defs/{definition}"}) + _semantic_errors(
        instance, definition
    )


def assert_valid(instance: dict[str, Any], definition: str) -> None:
    errors = contract_errors(instance, definition)
    if errors:
        raise ContractValidationError("\n".join(errors))


def assert_invalid(instance: dict[str, Any], definition: str) -> None:
    assert contract_errors(instance, definition)


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _enum_from_adr(name: str) -> list[str]:
    match = re.search(rf"^{re.escape(name)}:\s*(.+)$", ADR, flags=re.MULTILINE)
    assert match is not None
    return [part.strip() for part in match.group(1).split("|")]


PRIVACY = {
    "detail_endpoint_only": True,
    "loggable": False,
    "url_serializable": False,
    "feedback_serializable": False,
}

NO_GPS = {
    "contract_version": CONTRACT_VERSION,
    "geo_fact_id": "geo:asset-1:rev-1",
    "asset_id": 1,
    "location_id": 4,
    "input_revision": SHA,
    "fact_revision": 1,
    "status": "ABSENT",
    "source": "EXIF_GPS",
    "processor_id": "geo-extract@1",
    "raw_fields_checksum_sha256": SHA,
    "raw_gps": None,
    "normalized_wgs84": None,
    "datum_status": "ABSENT",
    "privacy": PRIVACY,
    "created_at": NOW,
}

VALID_GPS = {
    **NO_GPS,
    "geo_fact_id": "geo:asset-1:valid-1",
    "status": "VALID",
    "raw_gps": {
        "latitude_dms": [30, 15, 0.0],
        "latitude_ref": "N",
        "longitude_dms": [120, 10, 0.0],
        "longitude_ref": "E",
        "map_datum": "WGS-84",
        "altitude_m": None,
        "altitude_ref": None,
        "direction_deg": None,
        "accuracy_m": 15.0,
    },
    "normalized_wgs84": {
        "datum": "WGS84",
        "latitude": 30.25,
        "longitude": 120.1667,
        "altitude_m": None,
        "direction_deg": None,
        "accuracy_m": 15.0,
    },
    "datum_status": "WGS84",
}

CUSTOM_PLACE = {
    "contract_version": CONTRACT_VERSION,
    "place_id": 9,
    "kind": "custom",
    "canonical_name": "家",
    "aliases": [],
    "parent_place_id": None,
    "origin": "USER",
    "external_refs": [],
    "lifecycle": "ACTIVE",
    "revision": 1,
    "created_at": NOW,
    "updated_at": NOW,
}


def _assignment(
    asset_id: int,
    *,
    mode: str = "MANUAL_ASSIGNED",
    place_id: int | None = 9,
    origin: str = "MANUAL",
    revision: int = 1,
) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "assignment_id": f"assignment:{asset_id}:{revision}",
        "asset_id": asset_id,
        "place_id": place_id,
        "mode": mode,
        "origin": origin,
        "manual_lock": mode != "AUTO_ALLOWED",
        "resolution_id": None,
        "revision": revision,
        "record_state": "ACTIVE",
        "actor": "LOCAL_USER",
        "operation_id": f"operation:{asset_id}:{revision}",
        "updated_at": NOW,
    }


def _operation_item(asset_id: int) -> dict[str, Any]:
    return {
        "item_index": asset_id - 1,
        "asset_id": asset_id,
        "place_id": None,
        "action": "ASSIGN",
        "target_place_id": 9,
        "expected_revision": 0,
        "before_assignment": None,
        "after_assignment": None,
    }


def _batch(size: int) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "operation_id": "operation:batch-1",
        "request_id": "request:batch-1",
        "payload_sha256": SHA,
        "operation_type": "BATCH_ASSIGN",
        "operation_revision": 1,
        "actor": "LOCAL_USER",
        "atomic": True,
        "status": "COMMITTED",
        "undo_of_operation_id": None,
        "items": [_operation_item(index) for index in range(1, size + 1)],
        "created_at": NOW,
        "committed_at": NOW,
    }


NEARBY_CITY = {
    "contract_version": CONTRACT_VERSION,
    "resolution_id": "resolution:asset-1:dataset-1",
    "asset_id": 1,
    "geo_fact_id": VALID_GPS["geo_fact_id"],
    "input_revision": SHA,
    "control_revision_seen": 0,
    "processor_id": "geonames-nearby-city@1",
    "dataset_id": "geonames-cn-2026-09",
    "dataset_version": "2026-09-01",
    "config_hash": SHA,
    "status": "CANDIDATES",
    "suggestion_revision": 1,
    "candidates": [
        {
            "candidate_id": "candidate:1",
            "dataset_place_id": "geonames:1808926",
            "local_place_id": 12,
            "kind": "city",
            "display_name": "杭州",
            "ancestor_names": ["中国"],
            "rank": 1,
            "distance_m": 1520.5,
            "match_basis": "NEARBY_CITY_POINT",
            "confidence_basis": "DISTANCE_ONLY",
            "disposition": "SUGGESTION_ONLY",
        }
    ],
    "created_at": NOW,
}

MANIFEST = {
    "contract_version": CONTRACT_VERSION,
    "manifest_schema": "photo-search-place-dataset-manifest.v1",
    "package_id": "geonames-cities500-cn",
    "package_version": "2026-09-01+builder.1",
    "dataset_id": "geonames-cn-2026-09",
    "source": {
        "provider": "GeoNames",
        "source_url": "https://download.geonames.org/export/dump/",
        "snapshot": "2026-09-01",
        "license_id": "CC-BY-4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": "GeoNames",
    },
    "build": {
        "builder_id": "photo-search-geonames-builder",
        "builder_version": "1",
        "generated_at": NOW,
        "parameters_sha256": SHA,
        "parameters": {
            "source_files": ["cities500.zip", "alternateNamesV2.zip"],
            "minimum_population": 500,
            "languages": ["zh", "en"],
            "include_aliases": True,
            "sort_order": "geonameid-ascending-v1",
        },
    },
    "content_sha256": SHA,
    "coordinate_reference_system": "WGS84",
    "coverage": {
        "country_codes": ["CN"],
        "bounds_wgs84": {"south": 18.0, "west": 73.0, "north": 54.0, "east": 135.0},
        "languages": ["zh", "en"],
        "alias_languages": ["zh"],
        "record_count": 100,
    },
    "compatible_catalog_schema": {"minimum": 2, "maximum": 2},
    "files": [
        {
            "relative_path": "places.sqlite3",
            "role": "PLACES",
            "media_type": "application/vnd.sqlite3",
            "sha256": SHA,
            "byte_size": 4096,
        },
        {
            "relative_path": "LICENSE.txt",
            "role": "LICENSE",
            "media_type": "text/plain",
            "sha256": "b" * 64,
            "byte_size": 1200,
        },
    ],
    "state": "VALIDATED",
    "previous_dataset_id": None,
    "validated_at": NOW,
    "activated_at": None,
    "retained_at": None,
}

ANCESTOR_FILTER = {
    "contract_version": CONTRACT_VERSION,
    "place_ids": [2],
    "include_descendants": True,
    "place_state": "assigned",
    "catalog_filter_revision": 8,
    "profile_id": None,
    "search_session_id": None,
    "cursor": None,
    "cursor_catalog_filter_revision": None,
    "suggestion_revision": None,
    "cursor_suggestion_revision": None,
}


def test_schema_document_named_definitions_and_closed_objects() -> None:
    assert SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert SCHEMA["$id"].endswith("/p2-place-contracts.v1.json")
    required_defs = {
        "Place",
        "GeoFact",
        "PlaceResolution",
        "PlaceAssignment",
        "PlaceOperation",
        "PlaceDatasetManifest",
        "PlaceFilter",
        "PlaceApiError",
    }
    assert required_defs <= SCHEMA["$defs"].keys()

    for name, definition in SCHEMA["$defs"].items():
        for node in _walk(definition):
            ref = node.get("$ref")
            if ref is not None:
                assert ref.startswith("#/$defs/")
                assert ref.removeprefix("#/$defs/") in SCHEMA["$defs"], (name, ref)
            if node.get("type") == "object" and "properties" in node:
                assert "required" in node, name
                assert node.get("additionalProperties") is False, name


def test_frozen_enums_match_adr_and_first_release_scope() -> None:
    pairs = {
        "place_kind": "PlaceKind",
        "place_lifecycle": "PlaceLifecycle",
        "geo_status": "GeoStatus",
        "datum_status": "DatumStatus",
        "resolution_status": "ResolutionStatus",
        "assignment_mode": "AssignmentMode",
        "assignment_origin": "AssignmentOrigin",
        "assignment_record_state": "AssignmentRecordState",
        "dataset_state": "DatasetState",
    }
    for adr_name, schema_name in pairs.items():
        assert _enum_from_adr(adr_name) == SCHEMA["$defs"][schema_name]["enum"]
    assert SCHEMA["$defs"]["PlaceKind"]["enum"] == [
        "country",
        "city",
        "attraction",
        "custom",
    ]
    assert "`ADMIN` 不属于" in ADR
    assert "ACCEPTED / FROZEN FOR P2 PLACE IMPLEMENTATION" in ADR


def test_contract_is_additive_design_only_and_current_schema_has_no_place_tables() -> None:
    migration = (
        ROOT / "src" / "photo_search" / "catalog" / "migrations" / "0001_initial.sql"
    ).read_text(encoding="utf-8")
    assert "CREATE TABLE places" not in migration
    assert "CREATE TABLE asset_geo_facts" not in migration
    assert "schema_version = 1" in ADR
    for table in (
        "places",
        "asset_geo_facts",
        "place_resolutions",
        "asset_place_assignments",
        "place_operations",
        "geo_datasets",
        "catalog_filter_revision",
    ):
        assert f"`{table}`" in ADR


def test_positive_no_gps_nearby_city_custom_place_and_cancel_lock() -> None:
    assert_valid(NO_GPS, "GeoFact")
    assert_valid(NEARBY_CITY, "PlaceResolution")
    assert_valid(CUSTOM_PLACE, "Place")
    assert_valid(_assignment(1), "PlaceAssignment")
    assert_valid(
        _assignment(
            1,
            mode="MANUAL_UNASSIGNED",
            place_id=None,
            origin="MANUAL",
            revision=2,
        ),
        "PlaceAssignment",
    )


def test_positive_batch_500_manifest_and_ancestor_filter() -> None:
    assert_valid(_batch(500), "PlaceOperation")
    assert_valid(MANIFEST, "PlaceDatasetManifest")
    assert_valid(ANCESTOR_FILTER, "PlaceFilter")


@pytest.mark.parametrize(
    ("value", "field"),
    [
        (91.0, "latitude"),
        (-181.0, "longitude"),
        (float("nan"), "latitude"),
        (float("inf"), "longitude"),
    ],
)
def test_out_of_range_and_non_finite_coordinates_are_rejected(value: float, field: str) -> None:
    candidate = copy.deepcopy(VALID_GPS)
    candidate["normalized_wgs84"][field] = value
    assert_invalid(candidate, "GeoFact")


def test_nearest_city_cannot_be_promoted_to_automatic_determination() -> None:
    candidate = copy.deepcopy(NEARBY_CITY)
    candidate["candidates"][0]["disposition"] = "AUTOMATIC_CONFIRMED"
    assert_invalid(candidate, "PlaceResolution")


def test_machine_cannot_create_or_override_a_manual_lock() -> None:
    assignment = _assignment(1)
    assignment["actor"] = "MACHINE"
    assert_invalid(assignment, "PlaceAssignment")


def test_two_active_assignments_for_one_asset_are_semantically_rejected() -> None:
    first = _assignment(1, place_id=9)
    second = _assignment(1, place_id=10, revision=2)
    fixture = {"contract_version": CONTRACT_VERSION, "assignments": [first, second]}
    assert not _errors(SCHEMA, fixture, {"$ref": "#/$defs/PlaceAssignmentSet"})
    assert_invalid(fixture, "PlaceAssignmentSet")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda operation: operation.pop("request_id"),
        lambda operation: operation["items"][0].pop("expected_revision"),
    ],
)
def test_operations_require_idempotency_and_expected_revision(mutation) -> None:
    operation = _batch(1)
    mutation(operation)
    assert_invalid(operation, "PlaceOperation")


def test_batch_501_is_rejected_before_any_write() -> None:
    assert_invalid(_batch(501), "PlaceOperation")


@pytest.mark.parametrize("leak_field", ["url", "ordinary_log_fields", "feedback_payload"])
def test_precise_coordinates_cannot_enter_url_logs_or_feedback(leak_field: str) -> None:
    candidate = copy.deepcopy(VALID_GPS)
    candidate[leak_field] = {"latitude": 30.25, "longitude": 120.1667}
    assert_invalid(candidate, "GeoFact")
    assert PRIVACY == {
        "detail_endpoint_only": True,
        "loggable": False,
        "url_serializable": False,
        "feedback_serializable": False,
    }


def test_dataset_version_is_not_a_search_profile_field() -> None:
    profile = {
        "contract_version": "p2-contracts.v1",
        "profile_id": "profile-1",
        "embedding_processor_id": "embed@1",
        "space_id": "space-1",
        "image_generation_id": "image-generation-1",
        "query_preprocess_version": "query-preprocess-1",
        "query_parser_version": "query-parser-1",
        "fts_tokenizer_version": "fts-tokenizer-1",
        "fts_generation_id": "fts-generation-1",
        "reranker_processor_id": None,
        "fusion_config": {"revision": "rrf-1", "method": "rrf", "parameters": {}},
        "runtime_package_id": "runtime-1",
        "created_at": NOW,
        "place_dataset_version": "2026-09-01",
    }
    errors = _errors(CORE_SCHEMA, profile, {"$ref": "#/$defs/SearchProfile"})
    assert errors
    assert "地名数据包版本和 suggestion revision 独立于 SearchProfile" in ADR


def test_old_catalog_filter_revision_cannot_continue_paging() -> None:
    continuation = {
        **ANCESTOR_FILTER,
        "search_session_id": "search-session-1",
        "cursor": "opaque-cursor",
        "cursor_catalog_filter_revision": 7,
    }
    assert not _errors(SCHEMA, continuation, {"$ref": "#/$defs/PlaceFilter"})
    assert_invalid(continuation, "PlaceFilter")
    assert "CATALOG_FILTER_REVISION_STALE" in SCHEMA["$defs"]["PlaceApiError"][
        "properties"
    ]["code"]["enum"]


def test_api_error_codes_are_closed_and_never_carry_coordinate_or_path_fields() -> None:
    details = SCHEMA["$defs"]["PlaceApiErrorDetails"]
    assert details["additionalProperties"] is False
    assert not ({"latitude", "longitude", "path", "root_path"} & details["properties"].keys())
    error = {
        "code": "PLACE_ASSIGNMENT_CONFLICT",
        "message": "the assignment revision changed",
        "request_id": "request-1",
        "details": {
            "asset_ids": [1],
            "place_id": 9,
            "operation_id": None,
            "dataset_id": None,
            "expected_revision": 2,
            "current_revision": 3,
            "max_batch_size": 500,
        },
    }
    assert_valid(error, "PlaceApiError")
