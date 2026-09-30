"""Executable drift checks for the P2-01 ADR-0003 companion schema.

The project intentionally adds no JSON Schema dependency in P2-01.  This module therefore
validates instances with the explicit draft-2020-12 keyword subset used by the companion
file.  It is contract-fixture validation, not a claim that the future runtime uses this
test helper as its protocol validator.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "docs" / "decisions" / "contracts" / "p2-contracts.v1.json"
ADR_GLOB = "ADR-0003-*.md"
CONTRACT_VERSION = "p2-contracts.v1"
SPACE_ID = "chinese-clip-vit-b-16:c8ac42d3907c57b0"
SHA = "a" * 64


def _load_contract() -> tuple[dict[str, Any], str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    adr_paths = sorted((ROOT / "docs" / "decisions").glob(ADR_GLOB))
    assert len(adr_paths) == 1
    return schema, adr_paths[0].read_text(encoding="utf-8")


SCHEMA, ADR = _load_contract()


class ContractValidationError(AssertionError):
    """A fixture does not satisfy the checked JSON Schema keyword subset."""


def _resolve(schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if ref is None:
        return schema
    prefix = "#/$defs/"
    if not isinstance(ref, str) or not ref.startswith(prefix):
        raise ContractValidationError(f"unsupported ref: {ref!r}")
    name = ref.removeprefix(prefix)
    try:
        return SCHEMA["$defs"][name]
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
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if type_name == "string":
        return isinstance(instance, str)
    if type_name == "array":
        return isinstance(instance, list)
    if type_name == "object":
        return isinstance(instance, dict)
    raise ContractValidationError(f"unsupported schema type: {type_name!r}")


def _errors(instance: Any, raw_schema: dict[str, Any], path: str = "$") -> list[str]:
    schema = _resolve(raw_schema)
    errors: list[str] = []

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value is outside enum")

    if "oneOf" in schema:
        matches = sum(not _errors(instance, branch, path) for branch in schema["oneOf"])
        if matches != 1:
            errors.append(f"{path}: expected exactly one oneOf branch, got {matches}")
    if "anyOf" in schema and not any(
        not _errors(instance, branch, path) for branch in schema["anyOf"]
    ):
        errors.append(f"{path}: expected one anyOf branch")
    for branch in schema.get("allOf", []):
        errors.extend(_errors(instance, branch, path))
    if "not" in schema and not _errors(instance, schema["not"], path):
        errors.append(f"{path}: matched forbidden schema")
    if "if" in schema and not _errors(instance, schema["if"], path) and "then" in schema:
        errors.extend(_errors(instance, schema["then"], path))

    expected_type = schema.get("type")
    if expected_type is not None:
        accepted = [expected_type] if isinstance(expected_type, str) else expected_type
        if not any(_is_type(instance, name) for name in accepted):
            return errors + [f"{path}: expected type {accepted!r}"]

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for name in required:
            if name not in instance:
                errors.append(f"{path}: missing required property {name!r}")
        properties = schema.get("properties", {})
        for name, value in instance.items():
            if name in properties:
                errors.extend(_errors(value, properties[name], f"{path}.{name}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected property {name!r}")

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            errors.append(f"{path}: too few items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in instance]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: duplicate items")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(instance):
                errors.extend(_errors(item, item_schema, f"{path}[{index}]"))

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: string is too short")
        pattern = schema.get("pattern")
        if pattern is not None and re.fullmatch(pattern, instance) is None:
            errors.append(f"{path}: string does not match {pattern!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: above maximum")

    return errors


def assert_valid(instance: dict[str, Any], definition: str) -> None:
    errors = _errors(instance, {"$ref": f"#/$defs/{definition}"})
    if errors:
        raise ContractValidationError("\n".join(errors))


def assert_invalid(instance: dict[str, Any], definition: str) -> None:
    assert _errors(instance, {"$ref": f"#/$defs/{definition}"})


def _enum_from_adr(name: str) -> list[str]:
    match = re.search(rf"^{re.escape(name)}:\s*(.+)$", ADR, flags=re.MULTILINE)
    assert match is not None
    return [part.strip() for part in match.group(1).split("|")]


NON_MODEL_SPEC = {
    "contract_version": CONTRACT_VERSION,
    "spec_revision": "geo-extract-v1",
    "processor_id": "geo-extract@1",
    "processor_kind": "non_model",
    "task_type": "geo_extract",
    "config_hash": SHA,
    "input_type": "asset_original",
    "output_type": "geo_fact.v1",
    "device_policy": "CPU_PREFERRED",
    "runtime_backend": "python",
    "runtime_package_id": "photo-search-runtime@1",
    "supported_devices": ["cpu"],
    "supported_precisions": ["not_applicable"],
    "max_batch_size": 32,
    "cancellation_supported": True,
    "resource_measurements": [],
}

STRUCTURED_MODEL_SPEC = {
    **{key: value for key, value in NON_MODEL_SPEC.items() if key != "processor_kind"},
    "spec_revision": "rapidocr-ppocrv4-mobile-v1",
    "processor_id": "ocr-text@1",
    "processor_kind": "structured_model",
    "task_type": "image_ocr",
    "input_type": "asset_original",
    "output_type": "ocr_document.v1",
    "device_policy": "CPU_ALLOWED",
    "runtime_backend": "onnxruntime",
    "runtime_package_id": "onnxruntime-gpu@1.30.0",
    "supported_devices": ["gpu", "cpu"],
    "supported_precisions": ["FP32"],
    "max_batch_size": 1,
    "model_id": "rapidocr-ppocrv4-mobile",
    "model_revision": "rapidocr-onnxruntime@1.4.4",
    "weights": [
        {"name": "detection", "revision": "rapidocr-onnxruntime@1.4.4", "sha256": SHA},
        {"name": "classification", "revision": "rapidocr-onnxruntime@1.4.4", "sha256": SHA},
        {"name": "recognition", "revision": "rapidocr-onnxruntime@1.4.4", "sha256": SHA},
    ],
}

EMBEDDING_SPEC = {
    **{key: value for key, value in NON_MODEL_SPEC.items() if key != "processor_kind"},
    "spec_revision": "chinese-clip-vit-b-16-fp32-v1",
    "processor_id": "chinese-clip-vit-b-16@36e679e6-fp32-v1",
    "processor_kind": "embedding_model",
    "task_type": "image_embedding",
    "input_type": "decoded_rgb_image",
    "output_type": "embedding",
    "device_policy": "CPU_ALLOWED",
    "runtime_backend": "onnxruntime",
    "runtime_package_id": "onnxruntime-gpu@1.30.0",
    "supported_devices": ["cpu", "gpu"],
    "supported_precisions": ["FP32"],
    "max_batch_size": 1,
    "model_id": "OFA-Sys/chinese-clip-vit-base-patch16",
    "model_revision": "36e679e65c2a2fead755ae21162091293ad37834",
    "weights": [
        {
            "name": "image_tower.fp32.onnx",
            "revision": "36e679e65c2a2fead755ae21162091293ad37834",
            "sha256": "c8ac42d3907c57b0b6e75ec85572b8638cb4ccd6ad772fb2ae8e4ba38feddec0",
        },
        {
            "name": "text_tower.fp32.onnx",
            "revision": "36e679e65c2a2fead755ae21162091293ad37834",
            "sha256": "4699a6ef7355445136523b5035022cad8d5cc215a225e8d404b1f400f7cba967",
        },
    ],
    "image_preprocess_version": "chinese-clip-image-224-rgb-v1",
    "query_preprocess_version": "chinese-clip-query-52-v1",
    "tokenizer_version": "bert-vocab-45bbac6b-v1",
    "instruction_template_version": "none-v1",
    "normalization_version": "L2-v1",
    "dimension": 512,
    "metric": "cosine",
    "space_id": SPACE_ID,
    "max_pixels": 224 * 224,
    "max_tokens": 52,
}

STRUCTURED_ARTIFACT = {
    "artifact_id": "geo-fact:asset-1:geo-extract@1",
    "artifact_type": "structured",
    "artifact_revision": "geo-fact.v1",
    "media_type": "application/json",
    "checksum_sha256": SHA,
    "byte_size": 2,
    "storage_uri": None,
    "payload": {},
    "space_id": None,
    "dimension": None,
}


def test_schema_document_and_named_definitions_are_closed() -> None:
    assert SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert SCHEMA["$id"].endswith("/p2-contracts.v1.json")
    required_defs = {
        "JobRequest",
        "JobResult",
        "ProcessorSpec",
        "RuntimeCapabilities",
        "SearchProfile",
        "CatalogFilterRevision",
    }
    assert required_defs <= SCHEMA["$defs"].keys()

    for name, definition in SCHEMA["$defs"].items():
        for node in _walk(definition):
            ref = node.get("$ref")
            if ref is not None:
                assert ref.startswith("#/$defs/")
                assert ref.removeprefix("#/$defs/") in SCHEMA["$defs"], (name, ref)
            if node.get("type") == "object" and "properties" in node:
                assert "required" in node or name == "ArtifactRef"
                assert node.get("additionalProperties") is False or node == {
                    "type": "object"
                }


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def test_frozen_enums_match_adr_schema_and_existing_job_table() -> None:
    pairs = {
        "device_policy": "DevicePolicy",
        "requested_device": "RequestedDevice",
        "resolved_device": "ResolvedDevice",
        "job_state": "JobState",
        "job_result_state": "JobResultState",
    }
    for adr_name, schema_name in pairs.items():
        assert _enum_from_adr(adr_name) == SCHEMA["$defs"][schema_name]["enum"]

    migration = (
        ROOT / "src" / "photo_search" / "catalog" / "migrations" / "0001_initial.sql"
    ).read_text(encoding="utf-8")
    jobs_table = migration.split("CREATE TABLE jobs", maxsplit=1)[1]
    state_check = re.search(r"CHECK\(state IN \(([^)]+)\)\)", jobs_table)
    assert state_check is not None
    sql_states = [value.strip(" '") for value in state_check.group(1).split(",")]
    assert sql_states == SCHEMA["$defs"]["JobState"]["enum"]


def test_adr_freezes_space_and_forbids_implicit_fallback() -> None:
    assert SPACE_ID in ADR
    assert "禁止隐式 fallback" in ADR
    assert "ACCEPTED / FROZEN FOR P2 IMPLEMENTATION" in ADR

    manifest = json.loads(
        (
            ROOT
            / "tools"
            / "prepare_models"
            / "manifests"
            / "chinese-clip-vit-b-16.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["artifacts"]["onnx"]["image"]["sha256"].startswith(
        SPACE_ID.rsplit(":", 1)[1]
    )
    assert manifest["embedding"] == {
        "dimension": 512,
        "metric": "cosine",
        "normalization": "L2",
    }


def test_job_request_and_non_model_processor_instances_validate() -> None:
    request = {
        "contract_version": CONTRACT_VERSION,
        "job_id": 7,
        "subject_type": "asset",
        "subject_id": "asset-1",
        "input_revision": SHA,
        "task_type": "geo_extract",
        "processor_id": "geo-extract@1",
        "lease_token": "lease-current",
        "requested_device": "auto",
        "resolved_device": "cpu",
        "runtime_provider": "python-stdlib",
        "precision": "not_applicable",
        "runtime_capability_revision": "runtime-cpu-1",
        "input_snapshot": {
            "subject_revision": SHA,
            "asset_sha256": SHA,
            "location_id": 3,
            "byte_size": 123,
            "mtime_ns": 456,
        },
    }
    assert_valid(request, "JobRequest")
    assert_valid(NON_MODEL_SPEC, "ProcessorSpec")
    assert_valid(STRUCTURED_MODEL_SPEC, "ProcessorSpec")


def test_structured_model_requires_model_identity_and_rejects_vector_fields() -> None:
    assert_valid(STRUCTURED_MODEL_SPEC, "ProcessorSpec")
    for field in ("model_id", "model_revision", "weights"):
        invalid = dict(STRUCTURED_MODEL_SPEC)
        invalid.pop(field)
        assert_invalid(invalid, "ProcessorSpec")
    for field, value in (("space_id", SPACE_ID), ("dimension", 512), ("metric", "cosine")):
        invalid = {**STRUCTURED_MODEL_SPEC, field: value}
        assert_invalid(invalid, "ProcessorSpec")
    assert_invalid({**STRUCTURED_MODEL_SPEC, "output_type": "embedding"}, "ProcessorSpec")
    assert_invalid({**STRUCTURED_MODEL_SPEC, "weights": []}, "ProcessorSpec")
    # The additive branch must leave the frozen no-model CPU contract untouched.
    assert_valid(NON_MODEL_SPEC, "ProcessorSpec")
    assert_invalid(
        {**NON_MODEL_SPEC, "device_policy": "CPU_ALLOWED", "supported_devices": ["gpu", "cpu"]},
        "ProcessorSpec",
    )


def test_runtime_parser_rejects_invalid_structured_model_identity() -> None:
    from photo_search.catalog.errors import CatalogError
    from photo_search.scheduler.contracts import ProcessorSpec

    assert ProcessorSpec.from_envelope(STRUCTURED_MODEL_SPEC).processor_kind == "structured_model"
    for invalid in (
        {**STRUCTURED_MODEL_SPEC, "output_type": "embedding"},
        {**STRUCTURED_MODEL_SPEC, "model_id": ""},
        {**STRUCTURED_MODEL_SPEC, "weights": []},
        {**STRUCTURED_MODEL_SPEC, "weights": [{"name": "det", "revision": "v1", "sha256": "bad"}]},
    ):
        with pytest.raises(CatalogError):
            ProcessorSpec.from_envelope(invalid)


def test_embedding_processor_and_non_vector_results_validate() -> None:
    assert_valid(EMBEDDING_SPEC, "ProcessorSpec")
    result = {
        "contract_version": CONTRACT_VERSION,
        "job_id": 7,
        "lease_token": "lease-current",
        "input_revision": SHA,
        "task_type": "geo_extract",
        "processor_id": "geo-extract@1",
        "state": "SUCCEEDED",
        "resolved_device": "cpu",
        "runtime_provider": "python-stdlib",
        "precision": "not_applicable",
        "progress": 1,
        "artifacts": [STRUCTURED_ARTIFACT],
        "error": None,
        "completed_at": 1_789_000_000_000,
    }
    assert_valid(result, "JobResult")
    result["artifacts"] = []
    assert_valid(result, "JobResult")


def test_runtime_profile_and_catalog_revision_instances_validate() -> None:
    capabilities = {
        "contract_version": CONTRACT_VERSION,
        "capability_revision": "rtx4060-ort130-fp32-1",
        "runtime_package_id": "onnxruntime-gpu@1.30.0",
        "runtime_backend": "onnxruntime",
        "runtime_package_name": "onnxruntime-gpu",
        "runtime_package_version": "1.30.0",
        "python_version": "3.12.10",
        "platform": "windows-x86_64",
        "driver_version": "616.56",
        "providers": [
            {
                "name": "CUDAExecutionProvider",
                "device": "gpu",
                "precisions": ["FP32"],
                "available": True,
                "health": "healthy",
                "health_checked_at": 1_789_000_000_000,
                "evidence_id": "P0-06-chinese-clip-gpu-check",
            },
            {
                "name": "CPUExecutionProvider",
                "device": "cpu",
                "precisions": ["FP32"],
                "available": True,
                "health": "healthy",
                "health_checked_at": 1_789_000_000_000,
                "evidence_id": "P0-06-source-cpu-worker",
            },
        ],
        "health_status": "healthy",
        "health_checked_at": 1_789_000_000_000,
        "measured_at": 1_789_000_000_000,
        "evidence_ids": ["P0-06-chinese-clip-gpu-check"],
        "failure_code": None,
    }
    assert_valid(capabilities, "RuntimeCapabilities")

    profile = {
        "contract_version": CONTRACT_VERSION,
        "profile_id": "search-profile-baseline-v1",
        "embedding_processor_id": EMBEDDING_SPEC["processor_id"],
        "space_id": SPACE_ID,
        "image_generation_id": "image-index-v1",
        "query_preprocess_version": EMBEDDING_SPEC["query_preprocess_version"],
        "query_parser_version": "query-parser-v1",
        "fts_tokenizer_version": "unicode61-v1",
        "fts_generation_id": "fts-v1",
        "reranker_processor_id": None,
        "fusion_config": {
            "revision": "rrf-v1",
            "method": "rrf",
            "parameters": {"k": 60},
        },
        "runtime_package_id": EMBEDDING_SPEC["runtime_package_id"],
        "created_at": 1_789_000_000_000,
    }
    assert_valid(profile, "SearchProfile")
    assert_valid(
        {
            "contract_version": CONTRACT_VERSION,
            "revision": 8,
            "committed_at": 1_789_000_000_000,
            "cause": "PLACE_EFFECTIVE_ASSIGNMENT_CHANGED",
            "changed_asset_ids": ["asset-1"],
        },
        "CatalogFilterRevision",
    )


@pytest.mark.parametrize(
    ("definition", "fixture", "mutation"),
    [
        ("ProcessorSpec", NON_MODEL_SPEC, lambda value: value.pop("spec_revision")),
        ("ProcessorSpec", EMBEDDING_SPEC, lambda value: value.pop("space_id")),
        (
            "ProcessorSpec",
            NON_MODEL_SPEC,
            lambda value: value.update({"space_id": SPACE_ID}),
        ),
        (
            "JobResult",
            {
                "contract_version": CONTRACT_VERSION,
                "job_id": 1,
                "lease_token": "lease",
                "input_revision": "rev",
                "task_type": "image_embedding",
                "processor_id": "embed@1",
                "state": "SUCCEEDED",
                "resolved_device": "gpu",
                "runtime_provider": "CUDAExecutionProvider",
                "precision": "FP32",
                "progress": 1,
                "artifacts": [
                    {
                        **STRUCTURED_ARTIFACT,
                        "artifact_type": "embedding",
                    }
                ],
                "error": None,
                "completed_at": 1,
            },
            lambda value: None,
        ),
        (
            "SearchProfile",
            {
                "contract_version": CONTRACT_VERSION,
                "profile_id": "p1",
                "embedding_processor_id": "embed@1",
                "space_id": SPACE_ID,
                "image_generation_id": "g1",
                "query_preprocess_version": "qp1",
                "query_parser_version": "parser1",
                "fts_tokenizer_version": "fts-tokenizer1",
                "fts_generation_id": "fts1",
                "reranker_processor_id": None,
                "fusion_config": {
                    "revision": "rrf1",
                    "method": "rrf",
                    "parameters": {},
                },
                "runtime_package_id": "runtime1",
                "created_at": 1,
            },
            lambda value: value.update({"catalog_filter_revision": 3}),
        ),
    ],
)
def test_invalid_contract_examples_are_rejected(
    definition: str,
    fixture: dict[str, Any],
    mutation,
) -> None:
    candidate = copy.deepcopy(fixture)
    mutation(candidate)
    assert_invalid(candidate, definition)
