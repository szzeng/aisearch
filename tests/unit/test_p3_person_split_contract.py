"""Executable drift checks for the P3-05A ADR-0008 companion.

These tests validate the draft-2020-12 subset used by the checked-in JSON companion and
the cross-item rules JSON Schema cannot express. They are contract evidence only: P3-05A
does not implement the split service, migration or HTTP route.
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
    / "p3-person-split-contracts.v1.json"
)
ADR_PATH = ROOT / "docs" / "decisions" / "ADR-0008-person-split-operation-contract.md"
FACE_COMPANION_PATH = (
    ROOT
    / "docs"
    / "decisions"
    / "contracts"
    / "p3-face-correction-contracts.v1.json"
)

CONTRACT_VERSION = "p3-person-split-contracts.v1"
OPERATION_ID = "a" * 64
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
FACE_SCHEMA = json.loads(FACE_COMPANION_PATH.read_text(encoding="utf-8"))
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
    if definition == "PersonSplitRequest":
        face_ids = [item.get("face_id") for item in instance.get("faces", [])]
        if len(face_ids) != len(set(face_ids)):
            errors.append("request: face_id must be unique")
    if definition == "PersonSplitOperation":
        items = instance.get("items", [])
        groups = [item for item in items if item.get("entity_type") == "SPLIT_GROUP"]
        faces = [item for item in items if item.get("entity_type") == "FACE_CONTROL"]
        roles = [item.get("role") for item in groups]
        face_ids = [item.get("face_id") for item in faces]
        if roles != ["SOURCE", "CREATED"]:
            errors.append("operation: source and created group items must be first")
        if len(face_ids) != len(set(face_ids)) or face_ids != sorted(face_ids):
            errors.append("operation: face items must be unique and sorted")
        if len(items) != len(groups) + len(faces):
            errors.append("operation: unknown item family")
        if len(groups) == 2:
            if groups[0].get("entity_id") != instance.get("canonical_source_person_id"):
                errors.append("operation: source item id")
            if groups[1].get("entity_id") != instance.get("new_person_id"):
                errors.append("operation: created item id")
        if instance.get("operation_type") == "SPLIT":
            for item in faces:
                if (
                    item.get("mode") != "MANUAL_ASSIGNED"
                    or item.get("assigned_person_id") != instance.get("new_person_id")
                    or item.get("changed") is not True
                ):
                    errors.append("operation: split face must be manually assigned to new person")
    return errors


def assert_valid(instance: dict[str, Any], definition: str) -> None:
    errors = contract_errors(instance, definition)
    if errors:
        raise ContractValidationError("\n".join(errors))


def assert_invalid(instance: dict[str, Any], definition: str) -> None:
    assert contract_errors(instance, definition)


def selection(face_id: str = "face-001") -> dict[str, Any]:
    return {
        "face_id": face_id,
        "expected_control_revision": 0,
        "expected_assignment_revision": 3,
    }


def split_request() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": "split-1",
        "source_expected_revision": 7,
        "faces": [selection()],
    }


def group_item(
    role: str, entity_id: int, *, revision: int, merged_into: int | None = None
) -> dict[str, Any]:
    return {
        "entity_type": "SPLIT_GROUP",
        "entity_id": entity_id,
        "role": role,
        "changed": True,
        "revision": revision,
        "merged_into": merged_into,
    }


def face_item(face_id: str = "face-001", *, new_person_id: int = 12) -> dict[str, Any]:
    return {
        "entity_type": "FACE_CONTROL",
        "face_id": face_id,
        "changed": True,
        "mode": "MANUAL_ASSIGNED",
        "revision": 1,
        "assigned_person_id": new_person_id,
        "exclusions": {"activated": [7], "deactivated": []},
    }


def operation() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "operation_id": OPERATION_ID,
        "operation_type": "SPLIT",
        "operation_revision": 21,
        "status": "COMMITTED",
        "undo_of_operation_id": None,
        "requested_source_person_id": 4,
        "canonical_source_person_id": 7,
        "new_person_id": 12,
        "changed": True,
        "catalog_filter_revision": 9,
        "replayed": False,
        "items": [
            group_item("SOURCE", 7, revision=8),
            group_item("CREATED", 12, revision=1),
            face_item(),
        ],
    }


def test_schema_identity_and_closed_top_level_surface() -> None:
    assert SCHEMA["$schema"].endswith("2020-12/schema")
    assert SCHEMA["$defs"]["ContractVersion"]["const"] == CONTRACT_VERSION
    assert SCHEMA["$defs"]["PersonSplitOperation"]["properties"]["operation_type"][
        "enum"
    ] == ["SPLIT", "UNDO"]
    assert SCHEMA["$defs"]["PersonSplitRequest"]["additionalProperties"] is False
    assert SCHEMA["$defs"]["PersonSplitOperation"]["additionalProperties"] is False


def test_request_freezes_both_face_revisions_and_the_500_bound() -> None:
    request = split_request()
    assert_valid(request, "PersonSplitRequest")
    assert set(request["faces"][0]) == {
        "face_id",
        "expected_control_revision",
        "expected_assignment_revision",
    }
    face_schema = SCHEMA["$defs"]["PersonSplitRequest"]["properties"]["faces"]
    assert (face_schema["minItems"], face_schema["maxItems"]) == (1, 500)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("contract_version", "future"),
        ("request_id", "含中文"),
        ("request_id", "x" * 65),
        ("source_expected_revision", 0),
    ],
)
def test_invalid_request_identity_and_source_revision_are_rejected(
    field: str, value: Any
) -> None:
    request = split_request()
    request[field] = value
    assert_invalid(request, "PersonSplitRequest")


def test_empty_duplicate_and_501_face_requests_are_rejected() -> None:
    empty = split_request()
    empty["faces"] = []
    assert_invalid(empty, "PersonSplitRequest")
    duplicate = split_request()
    duplicate["faces"] = [selection(), selection()]
    assert_invalid(duplicate, "PersonSplitRequest")
    oversized = split_request()
    oversized["faces"] = [selection(f"face-{index:03d}") for index in range(501)]
    assert_invalid(oversized, "PersonSplitRequest")


def test_face_revision_shapes_distinguish_control_zero_from_stored_link() -> None:
    request = split_request()
    request["faces"][0]["expected_control_revision"] = 0
    request["faces"][0]["expected_assignment_revision"] = 1
    assert_valid(request, "PersonSplitRequest")
    request["faces"][0]["expected_assignment_revision"] = 0
    assert_invalid(request, "PersonSplitRequest")


def test_shared_undo_request_keeps_the_existing_two_field_shape() -> None:
    request = {"request_id": "undo-split-1", "expected_operation_revision": 21}
    assert_valid(request, "PersonOperationUndoRequest")
    request["contract_version"] = CONTRACT_VERSION
    assert_invalid(request, "PersonOperationUndoRequest")


def test_split_operation_has_two_group_items_then_sorted_face_items() -> None:
    result = operation()
    result["items"].extend([face_item("face-003"), face_item("face-002")])
    assert_invalid(result, "PersonSplitOperation")
    result["items"][3:] = sorted(result["items"][3:], key=lambda item: item["face_id"])
    assert_valid(result, "PersonSplitOperation")


def test_group_roles_and_response_ids_must_agree() -> None:
    result = operation()
    assert_valid(result, "PersonSplitOperation")
    swapped = copy.deepcopy(result)
    swapped["items"][0], swapped["items"][1] = swapped["items"][1], swapped["items"][0]
    assert_invalid(swapped, "PersonSplitOperation")
    wrong_id = copy.deepcopy(result)
    wrong_id["items"][1]["entity_id"] = 99
    assert_invalid(wrong_id, "PersonSplitOperation")


def test_a_split_face_is_always_manually_assigned_to_the_new_person() -> None:
    assert_valid(operation(), "PersonSplitOperation")
    for field, value in (
        ("mode", "AUTO_ALLOWED"),
        ("assigned_person_id", 7),
        ("changed", False),
    ):
        invalid = operation()
        invalid["items"][2][field] = value
        assert_invalid(invalid, "PersonSplitOperation")


def test_undo_uses_the_same_envelope_and_can_redirect_the_created_group() -> None:
    result = operation()
    result["operation_type"] = "UNDO"
    result["undo_of_operation_id"] = "b" * 64
    result["items"][1]["revision"] = 2
    result["items"][1]["merged_into"] = 7
    result["items"][2]["mode"] = "AUTO_ALLOWED"
    result["items"][2]["assigned_person_id"] = 7
    result["items"][2]["revision"] = 2
    assert_valid(result, "PersonSplitOperation")


def test_success_replay_and_undo_responses_reject_extra_private_fields() -> None:
    for private_field in (
        "path",
        "name",
        "name_key",
        "aliases",
        "bbox",
        "landmarks",
        "vector",
        "confidence",
        "payload_sha256",
        "before_value_json",
    ):
        result = operation()
        result["items"][2][private_field] = "forbidden"
        assert_invalid(result, "PersonSplitOperation")


def test_error_vocabulary_and_split_reason_are_closed() -> None:
    expected = {
        "PERSON_NOT_FOUND",
        "FACE_NOT_FOUND",
        "PERSON_REVISION_CONFLICT",
        "FACE_ASSIGNMENT_CONFLICT",
        "PERSON_SPLIT_CONFLICT",
        "PERSON_IDEMPOTENCY_CONFLICT",
        "PERSON_REDIRECT_CYCLE",
        "PERSON_OPERATION_NOT_UNDOABLE",
        "SCHEMA_INCONSISTENT",
        "SCHEMA_TOO_NEW",
        "PERSON_BATCH_TOO_LARGE",
        "VALIDATION_ERROR",
    }
    assert set(SCHEMA["$defs"]["PersonSplitApiError"]["properties"]["code"]["enum"]) == expected
    assert set(SCHEMA["$defs"]["SplitConflictReason"]["enum"]) == {
        "face_not_confirmed",
        "face_owned_by_other_person",
        "assignment_revision",
        "face_manual_unassigned",
        "face_state_inconsistent",
        "source_would_be_empty",
    }
    error = {
        "code": "PERSON_SPLIT_CONFLICT",
        "message": "the selected face is not an effective source member",
        "request_id": "api-request-1",
        "details": {"face_id": "face-1", "reason": "face_not_confirmed"},
    }
    assert_valid(error, "PersonSplitApiError")
    private = copy.deepcopy(error)
    private["details"]["path"] = "C:\\private\\photo.jpg"
    assert_invalid(private, "PersonSplitApiError")
    error["code"] = "UNKNOWN"
    assert_invalid(error, "PersonSplitApiError")


def test_adr_freezes_the_unique_path_and_payload_hash_inputs() -> None:
    for phrase in (
        "POST /api/v1/persons/{source_person_id}/split",
        "endpoint family `persons.split`",
        "source_expected_revision",
        "expected_control_revision",
        "expected_assignment_revision",
        "任何 revision 或实体存在性检查之前",
    ):
        assert phrase in ADR


def test_adr_closes_face_eligibility_and_empty_source_results() -> None:
    for phrase in (
        "face_not_confirmed",
        "face_owned_by_other_person",
        "face_manual_unassigned",
        "source_would_be_empty",
        "拆分没有语义 no-op",
        "1～500",
    ):
        assert phrase in ADR


def test_adr_requires_the_new_split_group_item_for_created_identity() -> None:
    for phrase in (
        "`PERSON_BUNDLE + FACE_CONTROL` 不足够",
        "`SPLIT_GROUP`",
        "{role: CREATED, exists: false, person_id: new_id}",
        "suggestion 不进入 group conflict fingerprint",
        "source `SPLIT_GROUP` index 0",
    ):
        assert phrase in ADR


def test_adr_freezes_forward_only_compensation_and_cross_type_conflicts() -> None:
    for phrase in (
        "不允许 undo an UNDO",
        "`group_changed`",
        "`face_control_changed`",
        "`effective_members_changed`",
        "revision 从 live +1",
        "不冲突，保留原状",
        "`merged_into` 指向 source",
    ):
        assert phrase in ADR


def test_adr_freezes_one_filter_revision_and_forbids_image_vector_work() -> None:
    for phrase in (
        "`catalog_filter_revision` **恰好 +1**",
        "`PERSON_CHANGED`",
        "`PENDING_REPAIR`",
        "不得重新编码图片",
        "不得重新编码图片、写图片向量 `index_outbox`",
        "split 没有业务 no-op",
    ):
        assert phrase in ADR


def test_adr_and_companion_preserve_the_accepted_face_contract() -> None:
    assert FACE_SCHEMA["$defs"]["ContractVersion"]["const"] == (
        "p3-face-correction-contracts.v1"
    )
    assert FACE_SCHEMA["$defs"]["OperationType"]["enum"] == [
        "FACE_ASSIGN",
        "FACE_UNASSIGN",
        "FACE_RESTORE_AUTO",
        "BATCH_FACE_EDIT",
        "UNDO",
    ]
    assert "`0001`～`0012` 逐字节不变" in ADR
    assert "本 ADR 不修改 migration" in ADR


def test_adr_states_the_document_only_phase_boundary() -> None:
    for phrase in (
        "只冻结规则，不实现生产服务、API 或 migration",
        "不声称数据库已执行这些拒绝",
        "P3-06",
        "P3-08",
        "不得复用、重写或重新生成检测 `face_id`",
    ):
        assert phrase in ADR
