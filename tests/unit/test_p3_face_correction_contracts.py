"""Executable drift checks for the P3-04A ADR-0007 companion schema.

These checks execute the draft-2020-12 keyword subset used by the companion plus the
cross-item rules JSON Schema cannot express.  They prove the frozen document and fixtures
agree; they are not evidence that the P3-04B database/API implementation exists.
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
SCHEMA_PATH = (
    ROOT
    / "docs"
    / "decisions"
    / "contracts"
    / "p3-face-correction-contracts.v1.json"
)
ADR_PATH = ROOT / "docs" / "decisions" / "ADR-0007-face-correction-lock-exclusion-contract.md"
CONTRACT_VERSION = "p3-face-correction-contracts.v1"
OPERATION_ID = "a" * 64

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
    return errors


def contract_errors(instance: dict[str, Any], definition: str) -> list[str]:
    errors = _errors(instance, {"$ref": f"#/$defs/{definition}"})
    if definition == "FaceCorrectionBatchRequest":
        face_ids = [item.get("face_id") for item in instance.get("items", [])]
        if len(face_ids) != len(set(face_ids)):
            errors.append("batch: face_id must be unique")
    if definition == "FaceControlState":
        exclusions = instance.get("active_exclusions", [])
        if exclusions != sorted(exclusions):
            errors.append("state: exclusions must be canonical and sorted")
    if definition == "FaceOperation":
        face_ids = [item.get("face_id") for item in instance.get("items", [])]
        if len(face_ids) != len(set(face_ids)):
            errors.append("operation: one FACE_CONTROL item per face")
    return errors


def assert_valid(instance: dict[str, Any], definition: str) -> None:
    errors = contract_errors(instance, definition)
    if errors:
        raise ContractValidationError("\n".join(errors))


def assert_invalid(instance: dict[str, Any], definition: str) -> None:
    assert contract_errors(instance, definition)


def request(action: str = "assign") -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": "face-op-1",
        "action": action,
        "target_person_id": 7 if action == "assign" else None,
        "expected_revision": 0,
    }


def correction_item(face_id: str, action: str = "unassign") -> dict[str, Any]:
    return {
        "face_id": face_id,
        "action": action,
        "target_person_id": 9 if action == "assign" else None,
        "expected_revision": 3,
    }


def control_state(mode: str = "MANUAL_ASSIGNED") -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "face_id": "face-001",
        "mode": mode,
        "revision": 4,
        "assigned_person_id": 7 if mode == "MANUAL_ASSIGNED" else None,
        "active_exclusions": [2, 5],
        "suggestions_available": True,
    }


def operation_item(face_id: str = "face-001") -> dict[str, Any]:
    return {
        "entity_type": "FACE_CONTROL",
        "face_id": face_id,
        "changed": True,
        "mode": "MANUAL_ASSIGNED",
        "revision": 4,
        "assigned_person_id": 7,
        "exclusions": {"activated": [2], "deactivated": [7]},
    }


def operation() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "operation_id": OPERATION_ID,
        "operation_type": "FACE_ASSIGN",
        "operation_revision": 11,
        "status": "COMMITTED",
        "undo_of_operation_id": None,
        "changed": True,
        "catalog_filter_revision": 8,
        "replayed": False,
        "items": [operation_item()],
    }


def test_schema_identity_and_closed_surface() -> None:
    assert SCHEMA["$schema"].endswith("2020-12/schema")
    assert SCHEMA["$defs"]["ContractVersion"]["const"] == CONTRACT_VERSION
    assert SCHEMA["$defs"]["OperationType"]["enum"] == [
        "FACE_ASSIGN",
        "FACE_UNASSIGN",
        "FACE_RESTORE_AUTO",
        "BATCH_FACE_EDIT",
        "UNDO",
    ]


@pytest.mark.parametrize("action", ["assign", "unassign", "restore_auto"])
def test_single_request_action_target_matrix(action: str) -> None:
    assert_valid(request(action), "FaceCorrectionRequest")


@pytest.mark.parametrize(
    ("action", "target"),
    [("assign", None), ("unassign", 3), ("restore_auto", 3), ("guess", None)],
)
def test_invalid_action_target_combinations_are_rejected(action: str, target: int | None) -> None:
    invalid = request("unassign")
    invalid["action"] = action
    invalid["target_person_id"] = target
    assert_invalid(invalid, "FaceCorrectionRequest")


def test_request_revision_zero_is_the_no_control_row_precondition() -> None:
    assert_valid(request(), "FaceCorrectionRequest")
    invalid = request()
    invalid["expected_revision"] = -1
    assert_invalid(invalid, "FaceCorrectionRequest")


def test_request_id_is_bounded_printable_ascii() -> None:
    invalid = request()
    invalid["request_id"] = "带路径的键"
    assert_invalid(invalid, "FaceCorrectionRequest")
    invalid["request_id"] = "x" * 65
    assert_invalid(invalid, "FaceCorrectionRequest")


def test_batch_is_explicit_bounded_and_face_unique() -> None:
    batch = {
        "contract_version": CONTRACT_VERSION,
        "request_id": "batch-1",
        "items": [correction_item("face-a"), correction_item("face-b", "assign")],
    }
    assert_valid(batch, "FaceCorrectionBatchRequest")
    duplicate = copy.deepcopy(batch)
    duplicate["items"][1]["face_id"] = "face-a"
    assert_invalid(duplicate, "FaceCorrectionBatchRequest")
    oversized = copy.deepcopy(batch)
    oversized["items"] = [correction_item(f"face-{index}") for index in range(501)]
    assert_invalid(oversized, "FaceCorrectionBatchRequest")


@pytest.mark.parametrize(
    "mode", ["AUTO_ALLOWED", "MANUAL_ASSIGNED", "MANUAL_UNASSIGNED"]
)
def test_control_state_mode_assignment_matrix(mode: str) -> None:
    assert_valid(control_state(mode), "FaceControlState")


def test_only_manual_assigned_can_have_an_effective_person() -> None:
    invalid = control_state("MANUAL_UNASSIGNED")
    invalid["assigned_person_id"] = 7
    assert_invalid(invalid, "FaceControlState")
    invalid = control_state("MANUAL_ASSIGNED")
    invalid["assigned_person_id"] = None
    assert_invalid(invalid, "FaceControlState")


def test_auto_allowed_may_keep_an_existing_automatic_assignment() -> None:
    state = control_state("AUTO_ALLOWED")
    state["assigned_person_id"] = 7
    assert_valid(state, "FaceControlState")


def test_active_exclusions_are_unique_and_canonical_ordered() -> None:
    duplicate = control_state()
    duplicate["active_exclusions"] = [2, 2]
    assert_invalid(duplicate, "FaceControlState")
    unsorted = control_state()
    unsorted["active_exclusions"] = [5, 2]
    assert_invalid(unsorted, "FaceControlState")


def test_operation_is_bounded_to_one_item_per_face() -> None:
    assert_valid(operation(), "FaceOperation")
    duplicate = operation()
    duplicate["operation_type"] = "BATCH_FACE_EDIT"
    duplicate["items"].append(copy.deepcopy(duplicate["items"][0]))
    assert_invalid(duplicate, "FaceOperation")


@pytest.mark.parametrize("private_field", ["path", "bbox", "landmarks", "vector", "confidence"])
def test_public_state_and_operation_reject_private_fields(private_field: str) -> None:
    state = control_state()
    state[private_field] = "forbidden"
    assert_invalid(state, "FaceControlState")
    result = operation()
    result["items"][0][private_field] = "forbidden"
    assert_invalid(result, "FaceOperation")


def test_error_code_set_and_envelope_are_closed() -> None:
    expected = {
        "FACE_NOT_FOUND",
        "PERSON_NOT_FOUND",
        "FACE_ASSIGNMENT_CONFLICT",
        "FACE_IDEMPOTENCY_CONFLICT",
        "FACE_BATCH_TOO_LARGE",
        "FACE_STATE_CONFLICT",
        "FACE_OPERATION_NOT_UNDOABLE",
        "PERSON_REDIRECT_CYCLE",
        "VALIDATION_ERROR",
    }
    assert set(SCHEMA["$defs"]["FaceApiError"]["properties"]["code"]["enum"]) == expected
    valid = {
        "code": "FACE_ASSIGNMENT_CONFLICT",
        "message": "the face changed",
        "request_id": "api-request-1",
        "details": {"face_id": "face-1", "expected_revision": 2, "current_revision": 3},
    }
    assert_valid(valid, "FaceApiError")
    valid["code"] = "UNKNOWN"
    assert_invalid(valid, "FaceApiError")


def test_adr_freezes_migration_projection_and_no_implementation_claim() -> None:
    for phrase in (
        "0012_face_correction_operations.sql",
        "PERSON_CHANGED",
        "PENDING_REPAIR",
        "expected_revision=0",
        "FACE_OPERATION_NOT_UNDOABLE",
        "不实现生产行为",
        "不声称数据库或 API 已经执行这些拒绝",
    ):
        assert phrase in ADR


def test_adr_names_the_required_counterexamples_and_privacy_boundary() -> None:
    for phrase in (
        "重启/重扫/重新关联",
        "晚到 AUTO 结果",
        "restore_auto 保留全部排除",
        "同一 face 在一个 batch 出现两次",
        "合照中请求只列一个 face_id",
        "首版不允许 undo an",
        "同 request_id 同 payload",
        "bbox、landmark、向量、confidence/单对相似度",
    ):
        assert phrase in ADR
