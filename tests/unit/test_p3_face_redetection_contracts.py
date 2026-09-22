"""Executable drift checks for the P3-08A ADR-0010 companions.

These checks execute the draft-2020-12 keyword subset used by the mapping companion plus the
cross-item rules JSON Schema cannot express, and they check the P3 acceptance matrix against
the clauses it claims to cover.  They prove the frozen documents and fixtures agree; they are
**not** evidence that the P3-08B database, service or API exists.
"""

from __future__ import annotations

import base64
import binascii
import copy
import json
import math
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
DECISIONS = ROOT / "docs" / "decisions"
CONTRACTS = DECISIONS / "contracts"
SCHEMA_PATH = CONTRACTS / "p3-face-redetection-contracts.v1.json"
MATRIX_PATH = CONTRACTS / "p3-acceptance-matrix.v1.json"
ADR_PATH = DECISIONS / "ADR-0010-face-redetection-mapping-and-p3-acceptance-contract.md"

CONTRACT_VERSION = "p3-face-redetection-contracts.v1"
MATRIX_VERSION = "p3-acceptance-matrix.v1"
OPERATION_ID = "b" * 64

SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
MATRIX = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
ADR = ADR_PATH.read_text(encoding="utf-8")

#: 主架构 §12.4 中本卡必须覆盖的项：人物 8、14～22；地点交叉 29～33。
REQUIRED_ARCHITECTURE_ITEMS = [str(item) for item in (8, *range(14, 23), *range(29, 34))]
SCENARIO_GROUPS = {"mapping", "person", "place", "cross", "review-defect", "browser"}
SCENARIO_FIELDS = (
    "id",
    "title",
    "group",
    "clause",
    "fixture",
    "injection",
    "expected_authority",
    "p3_08b_step",
)


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
    if "anyOf" in schema:
        matches = sum(not _errors(instance, branch, path) for branch in schema["anyOf"])
        if matches == 0:
            errors.append(f"{path}: anyOf matched 0")
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
    """The schema subset plus the cross-item rules ADR-0010 adds on top of it."""
    errors = _errors(instance, {"$ref": f"#/$defs/{definition}"})
    if definition == "MappingGroup":
        new_ids = [item.get("new_face_id") for item in instance.get("candidates", [])]
        matches = [item.get("match_state") for item in instance.get("candidates", [])]
        state = instance.get("candidate_state")
        if len(new_ids) != len(set(new_ids)):
            errors.append("group: one candidate row per new face")
        if state == "NO_CANDIDATE" and matches:
            errors.append("group: NO_CANDIDATE carries no candidates")
        if state == "UNIQUE_HIGH" and (len(matches) != 1 or matches[0] != "HIGH"):
            errors.append("group: UNIQUE_HIGH needs exactly one HIGH candidate")
        if state == "UNIQUE_WEAK" and (len(matches) != 1 or matches[0] != "WEAK"):
            errors.append("group: UNIQUE_WEAK needs exactly one WEAK candidate")
        if state == "AMBIGUOUS" and len(matches) < 2:
            errors.append("group: AMBIGUOUS needs at least two candidates")
        if instance.get("candidate_count") != len(new_ids):
            errors.append("group: candidate_count must equal len(candidates)")
    if definition == "DecisionRequest":
        decisions = instance.get("decisions", [])
        old_ids = [item.get("old_face_id") for item in decisions]
        if len(old_ids) != len(set(old_ids)):
            errors.append("request: one decision per old face")
        inheritors: dict[Any, int] = {}
        for item in decisions:
            targets = item.get("targets", [])
            new_ids = [target.get("new_face_id") for target in targets]
            if len(new_ids) != len(set(new_ids)):
                errors.append("request: one target per new face inside a decision")
            marked = sum(1 for target in targets if target.get("inherits_person"))
            if marked > 1:
                errors.append("request: at most one inheritor per old face")
            for target in targets:
                if target.get("inherits_person"):
                    key = target.get("new_face_id")
                    inheritors[key] = inheritors.get(key, 0) + 1
        if any(count > 1 for count in inheritors.values()):
            errors.append("request: at most one inheritor per new face")
    if definition == "MappingOperation":
        old_ids = [item.get("old_face_id") for item in instance.get("items", [])]
        if len(old_ids) != len(set(old_ids)):
            errors.append("operation: one FACE_MAPPING_GROUP item per old face")
    if definition == "MappingPage":
        old_ids = [item.get("old_face_id") for item in instance.get("groups", [])]
        if len(old_ids) != len(set(old_ids)):
            errors.append("page: one row per old face")
        if old_ids and old_ids != sorted(old_ids):
            errors.append("page: groups must be in canonical old_face_id order")
        cursor = instance.get("next_cursor")
        if cursor is not None and not _is_mapping_cursor(cursor):
            errors.append("page: next_cursor must decode to fm1:<old_face_id>")
    return errors


def assert_valid(instance: dict[str, Any], definition: str) -> None:
    errors = contract_errors(instance, definition)
    if errors:
        raise ContractValidationError("\n".join(errors))


def assert_invalid(instance: dict[str, Any], definition: str) -> None:
    assert contract_errors(instance, definition)


def face_id(seed: int) -> str:
    return f"{seed:064x}"


def mapping_cursor(seed: int = 900) -> str:
    payload = f"fm1:{face_id(seed)}"
    return base64.urlsafe_b64encode(payload.encode("ascii")).decode("ascii").rstrip("=")


def _is_mapping_cursor(text: str) -> bool:
    raw_text = str(text)
    try:
        raw = base64.urlsafe_b64decode(raw_text + "=" * (-len(raw_text) % 4)).decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    parts = raw.split(":")
    return len(parts) == 2 and parts[0] == "fm1" and re.fullmatch(
        r"[0-9a-f]{64}", parts[1]
    ) is not None


def thumbnail(seed: int) -> str:
    return f"/api/v1/faces/{face_id(seed)}/thumbnail"


def candidate(seed: int, state: str = "SUGGESTED", match: str = "HIGH") -> dict[str, Any]:
    return {
        "new_face_id": face_id(seed),
        "new_spec_revision": "face-buffalo-l-scrfd-arcface-v1",
        "match_state": match,
        "quality_state": "OK",
        "state": state,
        "revision": 1,
        "thumbnail_url": thumbnail(seed),
    }


def group_summary(seed: int = 900) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "old_face_id": face_id(seed),
        "asset_id": 11,
        "old_spec_revision": "face-buffalo-l-scrfd-arcface-v1",
        "candidate_state": "UNIQUE_HIGH",
        "state": "OPEN",
        "revision": 1,
        "control_mode": "MANUAL_ASSIGNED",
        "control_revision": 4,
        "assigned_person_id": 7,
        "candidate_count": 1,
        "thumbnail_url": thumbnail(seed),
    }


def group() -> dict[str, Any]:
    payload = group_summary()
    payload["candidates"] = [candidate(901)]
    return payload


def group_with(state: str, seeds: list[tuple[int, str]]) -> dict[str, Any]:
    payload = group_summary()
    payload["candidate_state"] = state
    payload["candidate_count"] = len(seeds)
    payload["candidates"] = [candidate(seed, match=match) for seed, match in seeds]
    return payload


def page() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "limit": 50,
        "next_cursor": mapping_cursor(900),
        "has_more": False,
        "groups": [group_summary()],
    }


def target(seed: int, inherits: bool = False) -> dict[str, Any]:
    return {"new_face_id": face_id(seed), "inherits_person": inherits}


def decision(
    kind: str = "confirm", *, seed: int = 900, targets: list | None = None
) -> dict[str, Any]:
    if targets is None:
        targets = [target(901, True)] if kind == "confirm" else []
    return {
        "old_face_id": face_id(seed),
        "expected_review_revision": 1,
        "decision": kind,
        "targets": targets,
    }


def decision_request(decisions: list | None = None) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": "map-op-1",
        "asset_id": 11,
        "decisions": [decision()] if decisions is None else decisions,
    }


def operation() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "operation_id": OPERATION_ID,
        "operation_type": "FACE_MAPPING_CONFIRM",
        "operation_revision": 12,
        "status": "COMMITTED",
        "undo_of_operation_id": None,
        "changed": True,
        "catalog_filter_revision": 9,
        "replayed": False,
        "items": [
            {
                "entity_type": "FACE_MAPPING_GROUP",
                "old_face_id": face_id(900),
                "changed": True,
                "state": "CONFIRMED",
                "revision": 2,
                "candidate_state": "UNIQUE_HIGH",
                "candidates": [
                    {
                        "new_face_id": face_id(901),
                        "state": "CONFIRMED",
                        "revision": 2,
                        "inherits_person": True,
                    }
                ],
            }
        ],
    }


#: One frozen, per-code closed ``details`` shape (ADR-0010 §8.2).  ``allowed``/``required``
#: mirror the ADR table exactly; ``foreign`` is a *safe* key of another code, so injecting it
#: proves the closure is per code and not just "the union of every safe key".
MAPPING_ERROR_CASES: dict[str, dict[str, Any]] = {
    "FACE_MAPPING_NOT_FOUND": {
        "allowed": ["old_face_id", "new_face_id", "asset_id"],
        "required": ["old_face_id"],
        "sample": {"old_face_id": face_id(900)},
        "foreign": "person_id",
    },
    "FACE_NOT_FOUND": {
        "allowed": ["old_face_id", "new_face_id", "asset_id"],
        "required": [],
        "sample": {"old_face_id": face_id(900)},
        "foreign": "person_id",
    },
    "PERSON_NOT_FOUND": {
        "allowed": ["person_id", "old_face_id"],
        "required": ["person_id"],
        "sample": {"person_id": 7},
        "foreign": "asset_id",
    },
    "PERSON_REDIRECT_CYCLE": {
        "allowed": ["person_id"],
        "required": ["person_id"],
        "sample": {"person_id": 7},
        "foreign": "old_face_id",
    },
    "FACE_MAPPING_CONFLICT": {
        "allowed": ["reason", "old_face_id", "new_face_id"],
        "required": ["reason"],
        "sample": {"reason": "human_affirm_deny_conflict"},
        "foreign": "person_id",
    },
    "FACE_MAPPING_STALE": {
        "allowed": [
            "old_face_id",
            "expected_review_revision",
            "current_review_revision",
        ],
        "required": [
            "old_face_id",
            "expected_review_revision",
            "current_review_revision",
        ],
        "sample": {
            "old_face_id": face_id(900),
            "expected_review_revision": 1,
            "current_review_revision": 2,
        },
        "foreign": "reason",
    },
    "FACE_MAPPING_TARGET_CONTROLLED": {
        "allowed": ["new_face_id", "old_face_id"],
        "required": ["new_face_id"],
        "sample": {"new_face_id": face_id(901)},
        "foreign": "reason",
    },
    "FACE_MAPPING_IDEMPOTENCY_CONFLICT": {
        "allowed": ["asset_id", "old_face_id"],
        "required": ["asset_id"],
        "sample": {"asset_id": 11},
        "foreign": "person_id",
    },
    "FACE_MAPPING_BATCH_TOO_LARGE": {
        "allowed": ["max_decisions", "max_targets"],
        "required": ["max_decisions"],
        "sample": {"max_decisions": 200, "max_targets": 16},
        "foreign": "reason",
    },
    "FACE_MAPPING_OPERATION_NOT_UNDOABLE": {
        "allowed": [
            "operation_id",
            "expected_operation_revision",
            "current_operation_revision",
            "status",
            "conflicts",
        ],
        "required": ["operation_id", "conflicts"],
        "sample": {
            "operation_id": OPERATION_ID,
            "conflicts": [
                {
                    "entity_type": "FACE_MAPPING_GROUP",
                    "entity_key": face_id(900),
                    "reason": "state",
                }
            ],
        },
        "foreign": "old_face_id",
    },
    "INVALID_CURSOR": {
        "allowed": [],
        "required": [],
        "sample": {},
        "foreign": "old_face_id",
    },
    "VALIDATION_ERROR": {
        "allowed": ["reason", "old_face_id", "new_face_id", "asset_id"],
        "required": ["reason"],
        "sample": {"reason": "confirm_requires_targets"},
        "foreign": "operation_id",
    },
}

#: A type-valid value for every safe ``details`` key, used to isolate the closure violation.
SAFE_KEY_SAMPLES: dict[str, Any] = {
    "old_face_id": face_id(900),
    "new_face_id": face_id(901),
    "asset_id": 11,
    "person_id": 7,
    "operation_id": OPERATION_ID,
    "expected_review_revision": 1,
    "current_review_revision": 2,
    "expected_operation_revision": 12,
    "current_operation_revision": 13,
    "status": "COMMITTED",
    "reason": "state",
    "max_decisions": 200,
    "max_targets": 16,
    "conflicts": [
        {
            "entity_type": "FACE_MAPPING_GROUP",
            "entity_key": face_id(900),
            "reason": "state",
        }
    ],
}


# ------------------------------------------------------------------ mapping companion


def test_schema_identity_and_closed_surfaces() -> None:
    assert SCHEMA["$schema"].endswith("2020-12/schema")
    assert SCHEMA["$defs"]["ContractVersion"]["const"] == CONTRACT_VERSION
    assert SCHEMA["$defs"]["MappingOperationType"]["enum"] == [
        "FACE_MAPPING_CONFIRM",
        "FACE_MAPPING_REJECT",
        "UNDO",
    ]
    assert SCHEMA["$defs"]["MappingState"]["enum"] == [
        "SUGGESTED",
        "CONFIRMED",
        "REJECTED",
        "STALE",
    ]
    assert SCHEMA["$defs"]["CandidateState"]["enum"] == [
        "NO_CANDIDATE",
        "UNIQUE_HIGH",
        "UNIQUE_WEAK",
        "AMBIGUOUS",
    ]


def test_frozen_matching_constants_match_the_adr() -> None:
    limits = SCHEMA["$defs"]["MappingLimits"]["const"]
    assert limits == {
        "max_decisions_per_request": 200,
        "max_targets_per_decision": 16,
        "default_page_limit": 50,
        "max_page_limit": 200,
        "cursor_prefix": "fm1",
        "match_iou_high": 0.5,
        "match_landmark_high": 0.12,
        "match_iou_min": 0.3,
        "match_landmark_max": 0.25,
    }
    for keyword in ("0.30", "0.25", "0.50", "0.12", "1..200", "1..16", "fm1"):
        assert keyword in ADR, keyword


def test_public_shapes_are_valid() -> None:
    assert_valid(page(), "MappingPage")
    assert_valid(group(), "MappingGroup")
    assert_valid(group_summary(), "MappingGroupSummary")
    assert_valid(candidate(901), "MappingCandidate")
    assert_valid(decision_request(), "DecisionRequest")
    assert_valid(operation(), "MappingOperation")


def test_candidate_state_and_candidate_list_agree() -> None:
    assert_valid(group_with("NO_CANDIDATE", []), "MappingGroup")
    assert_valid(group_with("UNIQUE_HIGH", [(901, "HIGH")]), "MappingGroup")
    assert_valid(group_with("UNIQUE_WEAK", [(901, "WEAK")]), "MappingGroup")
    assert_valid(
        group_with("AMBIGUOUS", [(901, "HIGH"), (902, "WEAK")]), "MappingGroup"
    )
    assert_invalid(group_with("NO_CANDIDATE", [(901, "HIGH")]), "MappingGroup")
    assert_invalid(
        group_with("UNIQUE_HIGH", [(901, "WEAK"), (902, "HIGH")]), "MappingGroup"
    )
    assert_invalid(group_with("UNIQUE_WEAK", [(901, "HIGH")]), "MappingGroup")
    assert_invalid(group_with("AMBIGUOUS", [(901, "HIGH")]), "MappingGroup")
    miscounted = group()
    miscounted["candidate_count"] = 2
    assert_invalid(miscounted, "MappingGroup")
    duplicated = group()
    duplicated["candidates"].append(copy.deepcopy(duplicated["candidates"][0]))
    assert_invalid(duplicated, "MappingGroup")


def test_page_is_sorted_and_deduplicated_by_old_face() -> None:
    unordered = page()
    higher = group_summary(901)
    unordered["groups"] = [higher, group_summary(900)]
    assert_invalid(unordered, "MappingPage")
    ordered = page()
    ordered["groups"] = [group_summary(900), group_summary(901)]
    assert_valid(ordered, "MappingPage")


@pytest.mark.parametrize("mode", ["AUTO_ALLOWED", "MANUAL_ASSIGNED", "MANUAL_UNASSIGNED"])
def test_group_control_projection_matrix(mode: str) -> None:
    payload = group_summary()
    payload["control_mode"] = mode
    if mode != "MANUAL_ASSIGNED":
        payload["assigned_person_id"] = None
    assert_valid(payload, "MappingGroupSummary")


def test_manual_unassigned_never_reports_a_person() -> None:
    payload = group_summary()
    payload["control_mode"] = "MANUAL_UNASSIGNED"
    payload["assigned_person_id"] = 7
    assert_invalid(payload, "MappingGroupSummary")
    payload = group_summary()
    payload["assigned_person_id"] = None
    assert_invalid(payload, "MappingGroupSummary")


def test_decision_targets_matrix() -> None:
    assert_valid(decision_request(), "DecisionRequest")
    assert_valid(decision_request([decision("reject")]), "DecisionRequest")
    assert_invalid(
        decision_request([decision("confirm", targets=[])]), "DecisionRequest"
    )
    assert_invalid(
        decision_request([decision("reject", targets=[target(901)])]), "DecisionRequest"
    )


def test_one_decision_per_old_face_and_one_inheritor_per_new_face() -> None:
    assert_invalid(decision_request([decision(), decision()]), "DecisionRequest")
    two_targets_one_old_face = decision_request(
        [decision("confirm", targets=[target(901, True), target(902, True)])]
    )
    assert_invalid(two_targets_one_old_face, "DecisionRequest")
    one_target_marked = decision_request(
        [decision("confirm", targets=[target(901, True), target(902, False)])]
    )
    assert_valid(one_target_marked, "DecisionRequest")
    no_inheritor = decision_request(
        [decision("confirm", targets=[target(901, False), target(902, False)])]
    )
    assert_valid(no_inheritor, "DecisionRequest")
    two_inheritors = decision_request(
        [
            decision("confirm", seed=900, targets=[target(901, True)]),
            decision("confirm", seed=905, targets=[target(901, True)]),
        ]
    )
    assert_invalid(two_inheritors, "DecisionRequest")
    many_to_one = decision_request(
        [
            decision("confirm", seed=900, targets=[target(901, True)]),
            decision("confirm", seed=905, targets=[target(901, False)]),
        ]
    )
    assert_valid(many_to_one, "DecisionRequest")
    duplicated_target = decision_request(
        [decision("confirm", targets=[target(901, True), target(901, False)])]
    )
    assert_invalid(duplicated_target, "DecisionRequest")


def test_request_and_operation_limits_are_bounded() -> None:
    oversized = decision_request(
        [decision("reject", seed=900 + index) for index in range(201)]
    )
    assert_invalid(oversized, "DecisionRequest")
    wide = decision_request(
        [decision("confirm", targets=[target(1000 + index) for index in range(17)])]
    )
    assert_invalid(wide, "DecisionRequest")
    duplicated_item = operation()
    duplicated_item["operation_type"] = "FACE_MAPPING_REJECT"
    duplicated_item["items"].append(copy.deepcopy(duplicated_item["items"][0]))
    assert_invalid(duplicated_item, "MappingOperation")


def test_request_id_is_bounded_printable_ascii() -> None:
    invalid = decision_request()
    invalid["request_id"] = "带路径的键"
    assert_invalid(invalid, "DecisionRequest")
    invalid["request_id"] = "x" * 65
    assert_invalid(invalid, "DecisionRequest")


def test_revision_bounds_and_thumbnail_template() -> None:
    invalid = decision_request([decision("reject")])
    invalid["decisions"][0]["expected_review_revision"] = 0
    assert_invalid(invalid, "DecisionRequest")
    bad_url = candidate(901)
    bad_url["thumbnail_url"] = "/api/v1/faces/901/thumbnail"
    assert_invalid(bad_url, "MappingCandidate")


@pytest.mark.parametrize(
    "private_field",
    ["bbox", "landmarks", "iou", "landmark_ratio", "vector", "confidence", "path"],
)
def test_public_mapping_shapes_reject_private_fields(private_field: str) -> None:
    payload = candidate(901)
    payload[private_field] = "forbidden"
    assert_invalid(payload, "MappingCandidate")
    item = operation()
    item["items"][0][private_field] = "forbidden"
    assert_invalid(item, "MappingOperation")
    summary = group_summary()
    summary[private_field] = "forbidden"
    assert_invalid(summary, "MappingGroupSummary")


def error_payload(code: str, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": code,
        "message": "the mapping surface refused the request",
        "request_id": "api-request-1",
        "details": details,
    }


def test_error_code_set_is_the_closed_one_of_branches() -> None:
    branches = SCHEMA["$defs"]["MappingApiError"]["oneOf"]
    codes = {branch["properties"]["code"]["const"] for branch in branches}
    assert codes == set(MAPPING_ERROR_CASES)
    assert len(branches) == len(codes)
    for code in codes:
        assert code in ADR, code
    assert_invalid(error_payload("UNKNOWN", {}), "MappingApiError")


@pytest.mark.parametrize("code", sorted(MAPPING_ERROR_CASES))
def test_error_details_are_closed_per_code(code: str) -> None:
    case = MAPPING_ERROR_CASES[code]
    sample = copy.deepcopy(case["sample"])
    assert_valid(error_payload(code, sample), "MappingApiError")
    for key in case["allowed"]:
        assert key in SAFE_KEY_SAMPLES, key
    foreign = case["foreign"]
    assert foreign not in case["allowed"]
    crossed = copy.deepcopy(case["sample"])
    crossed[foreign] = SAFE_KEY_SAMPLES[foreign]
    assert_invalid(error_payload(code, crossed), "MappingApiError")
    for key in case["required"]:
        missing = {
            name: value for name, value in copy.deepcopy(case["sample"]).items() if name != key
        }
        assert_invalid(error_payload(code, missing), "MappingApiError")


def test_face_not_found_needs_at_least_one_of_the_two_ids() -> None:
    assert_valid(error_payload("FACE_NOT_FOUND", {"old_face_id": face_id(900)}), "MappingApiError")
    assert_valid(error_payload("FACE_NOT_FOUND", {"new_face_id": face_id(901)}), "MappingApiError")
    assert_invalid(error_payload("FACE_NOT_FOUND", {}), "MappingApiError")
    assert_invalid(error_payload("FACE_NOT_FOUND", {"asset_id": 11}), "MappingApiError")


def test_invalid_cursor_details_must_be_empty() -> None:
    assert_valid(error_payload("INVALID_CURSOR", {}), "MappingApiError")
    assert_invalid(
        error_payload("INVALID_CURSOR", {"old_face_id": face_id(900)}), "MappingApiError"
    )


@pytest.mark.parametrize(
    "private_field",
    ["bbox", "landmark", "iou", "path", "internal_snapshot", "payload_sha256"],
)
def test_error_details_reject_private_keys(private_field: str) -> None:
    payload = {
        "code": "FACE_MAPPING_STALE",
        "message": "the review moved on",
        "request_id": "api-request-1",
        "details": {
            "old_face_id": face_id(900),
            "expected_review_revision": 1,
            "current_review_revision": 2,
            private_field: "forbidden",
        },
    }
    assert_invalid(payload, "MappingApiError")


def test_undoable_conflicts_are_bounded_and_typed() -> None:
    payload = {
        "code": "FACE_MAPPING_OPERATION_NOT_UNDOABLE",
        "message": "a later edit changed the snapshot",
        "request_id": "api-request-1",
        "details": {
            "operation_id": OPERATION_ID,
            "expected_operation_revision": 12,
            "current_operation_revision": 13,
            "status": "COMMITTED",
            "conflicts": [
                {
                    "entity_type": "FACE_MAPPING_GROUP",
                    "entity_key": face_id(900),
                    "reason": "state,revision",
                }
            ],
        },
    }
    assert_valid(payload, "MappingApiError")
    empty = copy.deepcopy(payload)
    empty["details"]["conflicts"] = []
    assert_invalid(empty, "MappingApiError")
    leaked = copy.deepcopy(payload)
    leaked["details"]["conflicts"][0]["after_value_json"] = {"bbox": [0, 0, 1, 1]}
    assert_invalid(leaked, "MappingApiError")


def test_pagination_bounds_and_cursor_space_are_enforced() -> None:
    assert_valid(page(), "MappingPage")
    at_max = page()
    at_max["limit"] = 200
    assert_valid(at_max, "MappingPage")
    first_page = page()
    first_page["next_cursor"] = None
    assert_valid(first_page, "MappingPage")
    for bad_limit in (0, 201, 999):
        bounded = page()
        bounded["limit"] = bad_limit
        assert_invalid(bounded, "MappingPage")
    for bad_cursor in ("not-fm1", "fm1.0000", "", "!!!!"):
        foreign = page()
        foreign["next_cursor"] = bad_cursor
        assert_invalid(foreign, "MappingPage")
    other_prefix = base64.urlsafe_b64encode(
        f"pf1:{face_id(900)}".encode("ascii")
    ).decode("ascii").rstrip("=")
    foreign_prefix = page()
    foreign_prefix["next_cursor"] = other_prefix
    assert_invalid(foreign_prefix, "MappingPage")


# ------------------------------------------------------------------ acceptance matrix


def test_matrix_header_and_coverage_keys() -> None:
    assert MATRIX["contract_version"] == MATRIX_VERSION
    assert MATRIX["authority"]["adr"].endswith(ADR_PATH.name)
    assert MATRIX["authority"]["consumer_task"] == "P3-08B"
    coverage = MATRIX["architecture_coverage"]
    assert set(coverage) == set(REQUIRED_ARCHITECTURE_ITEMS)
    for item, ids in coverage.items():
        assert ids, item


def test_every_scenario_is_well_formed_and_unique() -> None:
    scenarios = MATRIX["scenarios"]
    assert scenarios
    ids = [scenario["id"] for scenario in scenarios]
    assert len(ids) == len(set(ids))
    for scenario in scenarios:
        for field in SCENARIO_FIELDS:
            assert scenario.get(field), f"{scenario.get('id')}: {field}"
        assert scenario["group"] in SCENARIO_GROUPS
        assert re.fullmatch(r"P3-08B-[A-Z]+-\d{3}", scenario["id"]), scenario["id"]
        assert isinstance(scenario["allowed_side_effects"], list)
        assert scenario["forbidden_side_effects"], scenario["id"]
        assert scenario["evidence"]["status"] in MATRIX["evidence_status"]
        assert scenario["evidence"]["detail"], scenario["id"]
        if scenario["group"] == "browser":
            assert scenario["p3_08b_step"].startswith("记录的真实浏览器 smoke"), scenario["id"]
        else:
            assert scenario["p3_08b_step"].startswith("tests/"), scenario["id"]


def test_every_coverage_entry_points_at_a_real_scenario() -> None:
    ids = {scenario["id"] for scenario in MATRIX["scenarios"]}
    for item, covered in MATRIX["architecture_coverage"].items():
        for scenario_id in covered:
            assert scenario_id in ids, (item, scenario_id)


def test_clause_citations_name_the_expected_authority() -> None:
    for scenario in MATRIX["scenarios"]:
        clause = scenario["clause"]
        assert "§" in clause, scenario["id"]
        if scenario["group"] in {"mapping", "review-defect"}:
            assert clause.startswith("ADR-0010"), scenario["id"]
        if scenario["group"] in {"person", "place"}:
            assert clause.startswith("主架构 §12.4"), scenario["id"]


def test_matrix_covers_the_mapping_lifecycle_and_the_known_defect() -> None:
    ids = {scenario["id"] for scenario in MATRIX["scenarios"]}
    for required in (
        "P3-08B-MAP-001",  # 生成
        "P3-08B-MAP-002",  # 永不自动确认
        "P3-08B-MAP-009",  # 确认
        "P3-08B-MAP-004",  # 拒绝
        "P3-08B-MAP-016",  # 撤销
        "P3-08B-MAP-017",  # 重启
        "P3-08B-MAP-018",  # 重扫
        "P3-08B-MAP-020",  # 升级失败
        "P3-08B-MAP-021",  # 回退
        "P3-08B-MAP-022",  # 晚到 worker
        "P3-08B-DEF-001",  # P3-07B §6 原始反例
    ):
        assert required in ids, required


def test_matrix_freezes_the_corrected_lifecycle_boundaries() -> None:
    scenarios = {scenario["id"]: scenario for scenario in MATRIX["scenarios"]}
    for required in (
        "P3-08B-MAP-027",  # 未变化重跑零映射
        "P3-08B-MAP-028",  # 自映射不是映射
        "P3-08B-MAP-029",  # 保持接在自动关联写门
        "P3-08B-MAP-030",  # 释放后重新排队
        "P3-08B-MAP-031",  # 失效先于保持
        "P3-08B-MAP-032",  # 撤销回 OPEN 重新施加保持
        "P3-08B-MAP-033",  # 错误 details 闭集
        "P3-08B-MAP-034",  # 分页边界闭集
        "P3-08B-MAP-035",  # candidate_state 交叉约束
    ):
        assert required in scenarios, required
    generator = scenarios["P3-08B-MAP-001"]
    assert "跨世代" in generator["title"]
    assert "同 spec_revision" not in generator["fixture"]
    unchanged = scenarios["P3-08B-MAP-027"]
    for keyword in ("零新脸", "零新映射行", "零新 group"):
        assert keyword in unchanged["expected_authority"], keyword
    hold = scenarios["P3-08B-MAP-029"]
    assert "face_association_queue" in hold["expected_authority"]
    assert any("CONFIRMED" in effect for effect in hold["forbidden_side_effects"])
    assert any("队列行" in effect for effect in hold["forbidden_side_effects"])
    covered = set(MATRIX["architecture_coverage"]["20"])
    for scenario_id in ("P3-08B-MAP-027", "P3-08B-MAP-029", "P3-08B-MAP-031"):
        assert scenario_id in covered, scenario_id


def test_browser_closure_plan_is_complete_but_subordinate() -> None:
    closure = MATRIX["browser_closure"]
    assert closure["routes"] == ["/persons", "/places", "/search"]
    assert len(closure["steps"]) >= 5
    for keyword in ("360x640", "stale", "日志", "路径"):
        assert any(keyword in step for step in closure["steps"]), keyword
    assert "Playwright" in closure["tooling_boundary"]
    assert "记录的真实浏览器 smoke" in closure["tooling_boundary"]


def test_evidence_status_is_honest_and_never_claims_an_unrun_pass() -> None:
    statuses = {scenario["evidence"]["status"] for scenario in MATRIX["scenarios"]}
    assert statuses == set(MATRIX["evidence_status"])
    for scenario in MATRIX["scenarios"]:
        assert "PASS" not in scenario["evidence"]["detail"], scenario["id"]


# ------------------------------------------------------------------ ADR drift checks


def test_adr_is_accepted_and_scopes_the_implementation() -> None:
    assert "状态：Accepted" in ADR
    assert "状态：Proposed" not in ADR
    for phrase in (
        "P3-08B",
        "0014_face_redetection_mapping.sql",
        "schema_version 13 → 14",
        "FACE_MAPPING_GROUP",
        "FACE_MAPPING_CONFIRM",
        "FACE_MAPPING_REJECT",
        "provisional hold",
        "临时保持",
        "不实现生产行为",
        "不声称数据库或",
    ):
        assert phrase in ADR, phrase


def test_adr_freezes_the_transfer_rules_and_the_defect_fix() -> None:
    for phrase in (
        "任何歧义不得自动转移人工知识",
        "multiple_targets_without_single_inheritor",
        "两个谓词必须分开",
        "检测行保留集合",
        "自动关联保持集合",
        "_manually_locked_face_ids",
        "face_association_queue",
        "自映射不是映射",
        "v1 不定义同 spec 的新旧映射",
        "永不自动确认",
        "human_affirm_deny_conflict",
        "FACE_MAPPING_TARGET_CONTROLLED",
        "FACE_MAPPING_STALE",
        "FACE_MAPPING_OPERATION_NOT_UNDOABLE",
        "merge(target=高 id, sources=[低 id])",
        "与人 id 顺序无关",
        "max(before, live) + 1",
        "不写图片向量的 `index_outbox`",
    ):
        assert phrase in ADR, phrase


def test_adr_freezes_the_public_closed_sets() -> None:
    for phrase in (
        "`limit` 只接受 1..200（缺省 50）",
        "`INVALID_CURSOR`",
        "**`details` 是闭集，不是自由对象。**",
        "`details.conflicts` 是 1..64 项的数组",
        "逐码闭集在 companion 里用**严格 `oneOf`** 实现",
        "禁止用“所有安全键的并集 + 逐码追加必填键”的弱形状代替",
        "失效判定排在保持判定**之前**",
        "已被（重新）排队",
    ):
        assert phrase in ADR, phrase


def test_undo_is_never_part_of_the_release_set() -> None:
    assert "**释放集合只有 `confirm` / `reject`**" in ADR
    assert "**`undo` 回到 `OPEN` 不是释放**" in ADR
    release_start = ADR.index("**释放集合只有")
    release_end = ADR.index("**`undo` 回到 `OPEN` 不是释放**")
    release_clause = ADR[release_start:release_end]
    assert "undo" not in release_clause
    assert "撤销" not in release_clause
    assert "该 group 的候选新脸重新被关联遍跳过" in ADR


def test_adr_names_the_required_counterexamples_and_privacy_boundary() -> None:
    for phrase in (
        "两个 `inherits_person=true`",
        "2 旧脸 → 1 新脸",
        "NO_CANDIDATE",
        "低质量",
        "同 `request_id` 同 payload",
        "备份恢复",
        "bbox、landmark、IoU",
        "catalog_filter_revision",
    ):
        assert phrase in ADR, phrase


def test_the_contract_does_not_claim_that_the_implementation_exists() -> None:
    assert "P3-08A **只冻结规则**" in ADR
    assert "persons/mapping.py" in ADR
    assert "src/photo_search" in ADR
