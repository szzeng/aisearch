"""Executable drift checks for the P4-03A ADR-0013 UpgradeRun contract companion.

P4-03A is a **contract only** task: it adds no production code, no migration, no API and no
UI, and it must not change ``SUPPORTED_SCHEMA_VERSION``. This file therefore

(a) validates the draft-2020-12 subset the checked-in companion actually uses and pins the
    cross-item rules JSON Schema cannot express (the run/target transition matrices, the
    pause-detail rule, the failure-reason mapping, the activation-gate matrix);
(b) proves the failure vocabulary adds **no new error code**, by resolving every code named
    by the companion through the real ``photo_search.catalog.errors`` module and the real
    HTTP status map;
(c) exercises the plan-time difference-set predicate, the ``(run_id, asset_id)`` idempotency
    key, the five evaluable activation gates with their **zero-write** obligation, and the
    reference-protection boundary against a real SQLite catalog built from the frozen
    ``0001``..``0016`` migrations plus ADR-0013 section 2's reference DDL;
(d) freezes the byte identity of the nine earlier companions and of the sixteen shipped
    migrations, and proves that ``0017`` is still free.

The reference DDL and the reference functions below are the ADR text in executable form.
They are **not** the P4-03B implementation: nothing here creates ``0017``, and nothing here
claims that any upgrade behaviour already exists.
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

from photo_search.api import errors as api_errors
from photo_search.api.errors import error_payload, status_for_code
from photo_search.catalog import errors as error_codes
from photo_search.catalog.schema import migration_files
from photo_search.search import profile as search_profile

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "docs" / "decisions" / "contracts"
COMPANION_PATH = CONTRACTS / "p4-upgrade-contracts.v1.json"
ADR_PATH = ROOT / "docs" / "decisions" / "ADR-0013-upgrade-run-and-profile-activation-contract.md"
MIGRATIONS_DIR = ROOT / "src" / "photo_search" / "catalog" / "migrations"

COMPANION = json.loads(COMPANION_PATH.read_text(encoding="utf-8"))
ADR = ADR_PATH.read_text(encoding="utf-8")
DEFS: dict[str, Any] = COMPANION["$defs"]

CONTRACT_VERSION = "p4-upgrade-contracts.v1"
RUN_STATES = ("PLANNED", "RUNNING", "PAUSED", "CANCELLED", "FAILED", "SUCCEEDED")
TERMINAL_RUN_STATES = ("CANCELLED", "FAILED", "SUCCEEDED")
PAUSE_REASONS = ("USER", "BUDGET", "ENVIRONMENT")
TARGET_STATES = ("PLANNED", "IN_FLIGHT", "SUCCEEDED", "FAILED", "CANCELLED")
TERMINAL_TARGET_STATES = ("SUCCEEDED", "CANCELLED")
FAILURE_REASONS = (
    "MODEL_WEIGHTS_MISSING",
    "VRAM_INSUFFICIENT",
    "RUNTIME_UNHEALTHY",
    "SOURCE_OFFLINE",
    "ARTIFACT_MISSING",
    "LEASE_EXPIRED",
    "DISK_SPACE_INSUFFICIENT",
)
REFERENCE_KINDS = (
    "ACTIVE_PROFILE",
    "LIVE_SEARCH_SESSION",
    "UPGRADE_RUN_TARGET",
    "ROLLBACK_TASK",
    "BACKUP_MANIFEST",
)

MAX_PAUSE_DETAIL_CHARS = 200
MAX_BUDGET_TARGETS = 100_000_000
MAX_BUDGET_SECONDS = 86_400_000
MAX_BUDGET_CONCURRENCY = 64
PAUSE_DETAIL_PATTERN = "^[^\\u0000-\\u001F\\u007F-\\u009F/\\\\:]{1,200}$"

#: ADR-0013 section 3.1, exactly as the companion freezes it.
ALLOWED_RUN_TRANSITIONS = (
    "PLANNED->RUNNING",
    "PLANNED->PAUSED",
    "PLANNED->CANCELLED",
    "RUNNING->PAUSED",
    "RUNNING->SUCCEEDED",
    "RUNNING->FAILED",
    "RUNNING->CANCELLED",
    "PAUSED->RUNNING",
    "PAUSED->CANCELLED",
    "PAUSED->FAILED",
)
NAMED_REJECTED_RUN_TRANSITIONS = (
    "CANCELLED->RUNNING",
    "CANCELLED->PAUSED",
    "SUCCEEDED->RUNNING",
    "SUCCEEDED->PAUSED",
    "SUCCEEDED->CANCELLED",
    "FAILED->RUNNING",
    "FAILED->PAUSED",
    "PAUSED->SUCCEEDED",
    "PLANNED->SUCCEEDED",
    "RUNNING->PLANNED",
)

#: ADR-0013 section 4.4.
ALLOWED_TARGET_TRANSITIONS = (
    "PLANNED->IN_FLIGHT",
    "PLANNED->CANCELLED",
    "IN_FLIGHT->SUCCEEDED",
    "IN_FLIGHT->FAILED",
    "IN_FLIGHT->PLANNED",
    "FAILED->PLANNED",
    "FAILED->CANCELLED",
)
NAMED_REJECTED_TARGET_TRANSITIONS = (
    "SUCCEEDED->PLANNED",
    "SUCCEEDED->FAILED",
    "CANCELLED->PLANNED",
    "CANCELLED->IN_FLIGHT",
    "IN_FLIGHT->CANCELLED",
)

#: ADR-0013 section 9: reason -> (error code, details.reason, location, retryable, attempts).
EXPECTED_FAILURE_MAPPING: dict[str, tuple[str, str | None, str, bool, bool]] = {
    "MODEL_WEIGHTS_MISSING": (
        "RUNTIME_CAPABILITY_UNAVAILABLE",
        "MODEL_WEIGHTS_MISSING",
        "TARGET",
        False,
        False,
    ),
    "VRAM_INSUFFICIENT": (
        "RUNTIME_CAPABILITY_UNAVAILABLE",
        "VRAM_INSUFFICIENT",
        "TARGET",
        False,
        False,
    ),
    "RUNTIME_UNHEALTHY": (
        "RUNTIME_CAPABILITY_UNAVAILABLE",
        "RUNTIME_UNHEALTHY",
        "TARGET",
        False,
        False,
    ),
    "SOURCE_OFFLINE": ("SOURCE_OFFLINE", None, "TARGET", True, True),
    "ARTIFACT_MISSING": ("ARTIFACT_MISSING", None, "TARGET", True, True),
    "LEASE_EXPIRED": ("LEASE_EXPIRED", None, "TARGET", True, True),
    "DISK_SPACE_INSUFFICIENT": ("DISK_SPACE_INSUFFICIENT", None, "RUN", True, False),
}

#: ADR-0013 section 7 (Sol F-D): gate -> the frozen refusals of that gate only. The sixth
#: item of the card is the atomicity obligation, not a predicate.
EXPECTED_GATES: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "GENERATION_READY": (("INVALID_STATE_TRANSITION", ("GENERATION_NOT_READY",)),),
    "RUN_SUCCEEDED": (("INVALID_STATE_TRANSITION", ("RUN_NOT_SUCCEEDED",)),),
    "AUTHORITATIVE_COVERAGE_COMPLETE": (("INVALID_STATE_TRANSITION", ("COVERAGE_INCOMPLETE",)),),
    "ARTIFACT_READABLE": (("ARTIFACT_MISSING", ()),),
    "REFERENCES_PROTECTED": (
        ("PROFILE_GENERATION_MISMATCH", ("PROFILE_REFERENCE_CHANGED",)),
        (
            "RUNTIME_CAPABILITY_UNAVAILABLE",
            ("CAPABILITY_MISSING", "RUNTIME_UNHEALTHY", "QUALIFICATION_MISSING",
             "RERANKER_QUALIFICATION_MISSING"),
        ),
    ),
}

#: ADR-0013 section 7: the closed gate vocabulary, in the frozen order.
EXPECTED_GATE_VOCABULARY = tuple(EXPECTED_GATES)

#: ADR-0013 section 10.2 (Sol F-B): the closed control-request vocabulary.
CONTROL_ACTIONS = ("PAUSE", "RESUME", "CANCEL")
DIGEST_FIELDS = ("action", "pause_reason", "pause_detail", "grant_targets", "grant_seconds")
#: Sol F-B: the precondition is deliberately outside the digest, so an idempotent replay that
#: carries an older expected_control_revision is still a replay.
DIGEST_EXCLUDES = ("expected_control_revision",)

#: Every companion that already existed when ADR-0013 was written. ADR-0013 declares no
#: supersession, so all nine keep their bytes.
FROZEN_COMPANIONS = {
        "p2-contracts.v1.json": "4270997cac64a3c19c80f1d118b5e3d16022fdccb2c0a454c21d43d7730325fa",
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
    "p4-a3-contracts.v1.json": "b5ac6b44df4f37548411dd807056485da8db347783c0dd93ee7bdc088c51fd2e",
    "p4-time-contracts.v1.json": (
        "80189349bb4ea306225f9eae77ae9266c6d81c42de52c0b72bdc940a0702afd7"
    ),
}

#: ADR-0013 section 16: ``0001``..``0016`` must stay byte-identical under P4-03A.
FROZEN_MIGRATION_HASHES = {
    "0001_initial.sql": "4010535c214275bebdd5c7b82e975f15a2c858c6dc36b3b7bfdb2b04eeb86c10",
    "0002_scheduler_runtime.sql": "0454a432709a755b793504d0932c4b393f1689c4761b9c4f967c96b418fe9306",
    "0003_artifact_outbox_index.sql": "63930be5feafe650f2eb56d1438901276f76913252bcb97b79d416c992b54dea",
    "0004_place_core.sql": "0e18ab2433b82764ffbe6b5f1566a5ef346f717ef3998aad4bf9228772e22e70",
    "0005_place_dataset_resolution.sql": "866cf09a479f1a72001c61864f753d0832ec80ffdcc2173c1341930ec47147bd",
    "0006_search_fts.sql": "1c92bf23bfdcffe926999d7a7b541b83d41884127208e7a0817aed1ae80abcdb",
    "0007_search_profiles.sql": "b1c6e218b070ab069999a4f8049f59ac12a05256fb786f947af2a08f7a74e7c7",
    "0008_search_feedback.sql": "45cc9b5cfd163204ebca6073a8e22ee29c7f71827500827e8fe8cf5d54164668",
    "0009_ocr_documents.sql": "28ff83c9b4907112787b58b65944a03a73be4d2cedf29d84625ec5f1b6316a08",
    "0010_face_core.sql": "77e22325e80ce2ae0a09b41663d8c7cd110e73994cc84761d00fabce9ed358d0",
    "0011_person_knowledge.sql": "0cd3dc42d838d7c6ae160335240e366e884353c0f42590a2e31781553a064325",
    "0012_face_correction_operations.sql": (
        "6e4e56f03ae93d20ef5bbc8a14037f1ca86e1b593b3c7e153d3a32d27ea1cf12"
    ),
    "0013_person_split_operations.sql": (
        "9dea2d67726a61a958af57cce7dfbea6a433dc7679c406320f2e5dd2e1ccbde4"
    ),
    "0014_face_redetection_mapping.sql": (
        "5cb7df5715ecc782779c77a26e401d33f6c98d8d4c2a864ebaa094249922da23"
    ),
    "0015_taken_time.sql": "4e2cfa09cc4f85f7c94011930d910481edac20584050d10d8679521828137d89",
    "0016_jobs_claim_order.sql": "067505b70e87642a10a0ddc67e0ce9cfb7806b9731f5fd0cb4f1665d02203392",
}

#: ADR-0013 section 2.1/2.2 as reference DDL. It reuses the real ``assets``,
#: ``index_generations``, ``artifacts``, ``index_outbox``, ``index_entries`` and
#: ``search_profiles`` tables created by ``0001``..``0016``: the contract must read the
#: existing authority columns, never a private copy.
REFERENCE_UPGRADE_DDL = """
CREATE TABLE upgrade_runs (
    run_id              TEXT PRIMARY KEY CHECK(run_id <> ''),
    profile_id          TEXT NOT NULL
        REFERENCES search_profiles(profile_id) ON DELETE RESTRICT,
    space_id            TEXT NOT NULL CHECK(space_id <> ''),
    image_generation_id TEXT NOT NULL
        REFERENCES index_generations(generation_id) ON DELETE RESTRICT,
    target_set_digest   TEXT NOT NULL
        CHECK(length(target_set_digest) = 64 AND target_set_digest NOT GLOB '*[^0-9a-f]*'),
    state               TEXT NOT NULL
        CHECK(state IN ('PLANNED','RUNNING','PAUSED','CANCELLED','FAILED','SUCCEEDED')),
    pause_reason        TEXT
        CHECK(pause_reason IS NULL OR pause_reason IN ('USER','BUDGET','ENVIRONMENT')),
    pause_detail        TEXT CHECK(pause_detail IS NULL OR (
        length(pause_detail) BETWEEN 1 AND 200
        AND instr(pause_detail, '/') = 0
        AND instr(pause_detail, '\\') = 0
        AND instr(pause_detail, ':') = 0
        AND instr(pause_detail, char(9)) = 0
        AND instr(pause_detail, char(10)) = 0
        AND instr(pause_detail, char(13)) = 0
        AND instr(pause_detail, char(0)) = 0
    )),
    budget_targets      INTEGER
        CHECK(budget_targets IS NULL OR (budget_targets BETWEEN 1 AND 100000000)),
    budget_seconds      INTEGER
        CHECK(budget_seconds IS NULL OR (budget_seconds BETWEEN 1 AND 86400000)),
    budget_concurrency  INTEGER NOT NULL DEFAULT 1
        CHECK(budget_concurrency BETWEEN 1 AND 64),
    elapsed_ms          INTEGER NOT NULL DEFAULT 0 CHECK(elapsed_ms >= 0),
    resumed_at          INTEGER CHECK(resumed_at IS NULL OR resumed_at >= 0),
    clock_owner         TEXT CHECK(clock_owner IS NULL OR clock_owner <> ''),
    control_revision    INTEGER NOT NULL CHECK(control_revision >= 1),
    last_error_code     TEXT,
    planned_at          INTEGER NOT NULL CHECK(planned_at >= 0),
    updated_at          INTEGER NOT NULL CHECK(updated_at >= 0),
    CHECK((state = 'PAUSED' AND pause_reason IS NOT NULL)
          OR (state <> 'PAUSED' AND pause_reason IS NULL AND pause_detail IS NULL)),
    CHECK((state = 'RUNNING' AND resumed_at IS NOT NULL AND clock_owner IS NOT NULL)
          OR (state <> 'RUNNING' AND resumed_at IS NULL AND clock_owner IS NULL)),
    CHECK(pause_reason <> 'ENVIRONMENT'
          OR (pause_detail IS NOT NULL AND pause_detail <> ''))
);

CREATE INDEX idx_upgrade_runs_state ON upgrade_runs(state, updated_at);

CREATE TABLE upgrade_targets (
    run_id         TEXT NOT NULL REFERENCES upgrade_runs(run_id) ON DELETE CASCADE,
    asset_id       INTEGER NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    input_revision TEXT NOT NULL CHECK(input_revision <> ''),
    state          TEXT NOT NULL
        CHECK(state IN ('PLANNED','IN_FLIGHT','SUCCEEDED','FAILED','CANCELLED')),
    reason         TEXT CHECK(reason IS NULL OR reason IN (
        'MODEL_WEIGHTS_MISSING','VRAM_INSUFFICIENT','RUNTIME_UNHEALTHY','SOURCE_OFFLINE',
        'ARTIFACT_MISSING','LEASE_EXPIRED','DISK_SPACE_INSUFFICIENT')),
    attempts       INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    lease_token    TEXT CHECK(lease_token IS NULL OR lease_token <> ''),
    lease_expires_at INTEGER CHECK(lease_expires_at IS NULL OR lease_expires_at >= 0),
    updated_at     INTEGER NOT NULL CHECK(updated_at >= 0),
    PRIMARY KEY(run_id, asset_id),
    CHECK((state = 'FAILED' AND reason IS NOT NULL)
          OR (state <> 'FAILED' AND reason IS NULL)),
    CHECK((state = 'IN_FLIGHT' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
          OR (state <> 'IN_FLIGHT' AND lease_token IS NULL AND lease_expires_at IS NULL))
);

CREATE INDEX idx_upgrade_targets_state ON upgrade_targets(run_id, state, asset_id);

-- ADR-0013 section 2.3 (Sol F-B): the durable control-request log that carries request_id
-- idempotency across restarts.
CREATE TABLE upgrade_control_requests (
    run_id             TEXT NOT NULL REFERENCES upgrade_runs(run_id) ON DELETE CASCADE,
    request_id         TEXT NOT NULL CHECK(request_id <> '' AND length(request_id) <= 64),
    action             TEXT NOT NULL CHECK(action IN ('PAUSE','RESUME','CANCEL')),
    request_digest     TEXT NOT NULL
        CHECK(length(request_digest) = 64 AND request_digest NOT GLOB '*[^0-9a-f]*'),
    grant_targets      INTEGER CHECK(grant_targets IS NULL OR (grant_targets BETWEEN 1 AND 100000000)),
    grant_seconds      INTEGER CHECK(grant_seconds IS NULL OR (grant_seconds BETWEEN 1 AND 86400000)),
    outcome_state      TEXT NOT NULL
        CHECK(outcome_state IN ('PLANNED','RUNNING','PAUSED','CANCELLED','FAILED','SUCCEEDED')),
    outcome_pause_reason TEXT
        CHECK(outcome_pause_reason IS NULL OR outcome_pause_reason IN ('USER','BUDGET','ENVIRONMENT')),
    outcome_pause_detail TEXT,
    outcome_control_revision INTEGER NOT NULL CHECK(outcome_control_revision >= 1),
    changed            INTEGER NOT NULL CHECK(changed IN (0,1)),
    created_at         INTEGER NOT NULL CHECK(created_at >= 0),
    PRIMARY KEY(run_id, request_id),
    CHECK(grant_targets IS NULL OR (action = 'RESUME' AND changed = 1)),
    CHECK(grant_seconds IS NULL OR (action = 'RESUME' AND changed = 1)),
    CHECK((outcome_state = 'PAUSED' AND outcome_pause_reason IS NOT NULL)
          OR (outcome_state <> 'PAUSED' AND outcome_pause_reason IS NULL
              AND outcome_pause_detail IS NULL))
);

CREATE INDEX idx_upgrade_control_requests_run ON upgrade_control_requests(run_id, created_at);
"""

#: One existing, readable file relative to the repo root, used as an artifact ``storage_uri``.
#: ``artifacts.storage_uri`` forbids absolute paths, drive letters and ``..`` (0003).
DATA_ROOT = ROOT
ARTIFACT_URI = "docs/decisions/contracts/p4-upgrade-contracts.v1.json"
MISSING_ARTIFACT_URI = "docs/decisions/contracts/does-not-exist.json"


# --------------------------------------------------------------------------- validation
class ContractValidationError(AssertionError):
    """The companion uses a keyword this subset does not implement."""


class Refused(AssertionError):
    """The reference model refused a request; ``code``/``reason`` are the frozen values."""

    def __init__(self, code: str, reason: str | None = None) -> None:
        super().__init__(f"{code}:{reason}")
        self.code = code
        self.reason = reason


def _resolve(schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if ref is None:
        return schema
    prefix = "#/$defs/"
    if not isinstance(ref, str) or not ref.startswith(prefix):
        raise ContractValidationError(f"unsupported ref: {ref!r}")
    return DEFS[ref.removeprefix(prefix)]


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
    if "if" in schema:
        if not _errors(instance, schema["if"], path):
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
        for index, item_schema in enumerate(schema.get("prefixItems", [])):
            if index < len(instance):
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
    return _errors(instance, {"$ref": f"#/$defs/{definition}"})


def resolve_error_code(code: str) -> str | None:
    """The code's value as this build spells it, from the catalog or the API vocabulary."""
    for module in (error_codes, api_errors):
        value = getattr(module, code, None)
        if isinstance(value, str):
            return value
    return None


def referenced_defs() -> set[str]:
    """Every ``$defs`` entry reachable from the public surface (no orphan definitions)."""
    refs: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref" and isinstance(value, str):
                    refs.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(COMPANION["oneOf"])
    seen: set[str] = set()
    while refs:
        ref = refs.pop()
        name = ref.rsplit("/", 1)[-1]
        if name in seen:
            continue
        seen.add(name)
        walk(DEFS[name])
    return seen


# ------------------------------------------------------------------- reference models
def run_transition_allowed(source: str, target: str) -> bool:
    """ADR-0013 section 3.1: the accepted set is exactly ``ALLOWED_RUN_TRANSITIONS``."""
    return f"{source}->{target}" in ALLOWED_RUN_TRANSITIONS


def target_transition_allowed(source: str, target: str) -> bool:
    """ADR-0013 section 4.4."""
    return f"{source}->{target}" in ALLOWED_TARGET_TRANSITIONS


def control_run(state: str, action: str, *, pause_reason: str | None = None) -> str:
    """One control action as a pure run-state transition (ADR-0013 section 6).

    Repeating an action on the state it already produced is an idempotent replay: the state
    (and therefore ``control_revision``) does not change.
    """
    target = {"pause": "PAUSED", "resume": "RUNNING", "cancel": "CANCELLED"}.get(action)
    if target is None:
        raise Refused("VALIDATION_ERROR", "UNKNOWN_CONTROL_ACTION")
    if state == target:
        return state
    if not run_transition_allowed(state, target):
        raise Refused("INVALID_STATE_TRANSITION", f"{state}->{target}")
    if target == "PAUSED" and pause_reason not in PAUSE_REASONS:
        raise Refused("VALIDATION_ERROR", "PAUSE_REASON_REQUIRED")
    return target


def budget_stop(
    *,
    budget_targets: int | None,
    budget_seconds: int | None,
    completed_targets: int,
    elapsed_ms: int,
) -> bool:
    """ADR-0013 section 5.3: any cap reaching its limit is a soft stop."""
    if budget_targets is not None and completed_targets >= budget_targets:
        return True
    return budget_seconds is not None and elapsed_ms >= budget_seconds * 1000


def claim_batch_size(
    *, budget_concurrency: int, budget_targets: int | None, completed_targets: int
) -> int:
    """ADR-0013 section 5.3: concurrency bounds one claim and is never a stop."""
    remaining = budget_concurrency
    if budget_targets is not None:
        remaining = min(remaining, max(budget_targets - completed_targets, 0))
    return remaining


def resume_targets(target_states: tuple[str, ...]) -> tuple[str, ...]:
    """ADR-0013 section 6: resume only touches rows that are not ``SUCCEEDED``."""
    return tuple(state for state in target_states if state != "SUCCEEDED")


def advance_elapsed(state: str, elapsed_ms: int, delta_ms: int) -> int:
    """ADR-0013 section 5.2: the wall clock only advances while the run is RUNNING."""
    return elapsed_ms + delta_ms if state == "RUNNING" else elapsed_ms


# --------------------------------------------------------------------- SQLite fixture
def new_catalog() -> sqlite3.Connection:
    """A real catalog: the shipped ``0001``..``0016`` plus the ADR-0013 reference DDL."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    for version, path in migration_files():
        if version > 16:
            continue
        connection.executescript(path.read_text(encoding="utf-8"))
    connection.executescript(REFERENCE_UPGRADE_DDL)
    connection.execute("INSERT INTO metadata(key, value) VALUES ('schema_version', '16')")
    # Autocommit: the reference transactions below issue their own explicit BEGIN IMMEDIATE,
    # exactly like catalog/db.py::Catalog.write() does for the real writer.
    connection.isolation_level = None
    return connection


def digest_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def add_job(connection: sqlite3.Connection, job_id: int = 1) -> None:
    connection.execute(
        "INSERT INTO jobs(job_id, subject_type, subject_id, input_revision, task_type, "
        "processor_id, state, priority, attempts, max_attempts, run_after, progress, "
        "created_at, updated_at) VALUES (?, 'asset', ?, ?, 'embed', 'emb-v1', 'SUCCEEDED', "
        "0, 1, 3, 0, 1.0, 0, 0)",
        (job_id, f"job-{job_id}", "a" * 64),
    )


def add_asset(connection: sqlite3.Connection, asset_id: int) -> None:
    connection.execute(
        "INSERT INTO assets(asset_id, sha256, byte_size, created_at) VALUES (?, ?, 10, 0)",
        (asset_id, digest_of(f"asset-{asset_id}")),
    )


def add_generation(
    connection: sqlite3.Connection,
    generation_id: str,
    *,
    space_id: str = "space-a",
    processor_id: str = "emb-v1",
    dimension: int = 512,
    state: str = "READY",
    is_active: int = 0,
) -> None:
    connection.execute(
        "INSERT INTO index_generations(generation_id, space_id, processor_id, dimension, "
        "state, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
        (generation_id, space_id, processor_id, dimension, state, is_active),
    )


def add_profile(
    connection: sqlite3.Connection,
    profile_id: str,
    *,
    generation_id: str,
    space_id: str = "space-a",
    processor_id: str = "emb-v1",
) -> None:
    connection.execute(
        "INSERT INTO search_profiles(profile_id, embedding_processor_id, space_id, "
        "image_generation_id, query_preprocess_version, query_parser_version, "
        "fts_tokenizer_version, fts_generation_id, reranker_processor_id, "
        "fusion_config_json, runtime_package_id, created_at) VALUES (?, ?, ?, ?, '1', '1', "
        "'1', 'fts-v1', NULL, ?, 'pkg-v1', 0)",
        (
            profile_id,
            processor_id,
            space_id,
            generation_id,
            json.dumps(
                {
                    "revision": "rrf-v1",
                    "method": "RRF",
                    "parameters": {"k": 60, "weights": {"vector": 1.0, "fts": 1.0}},
                }
            ),
        ),
    )


def add_artifact(
    connection: sqlite3.Connection,
    artifact_id: str,
    *,
    storage_uri: str = ARTIFACT_URI,
    space_id: str = "space-a",
    dimension: int = 512,
    job_id: int = 1,
) -> None:
    connection.execute(
        "INSERT INTO artifacts(artifact_id, processor_id, input_revision, artifact_type, "
        "artifact_revision, media_type, checksum_sha256, byte_size, storage_uri, space_id, "
        "dimension, job_id, lifecycle_state, created_at, updated_at) VALUES (?, 'emb-v1', ?, "
        "'embedding', 'r1', 'application/json', ?, 1, ?, ?, ?, ?, 'PUBLISHED', 0, 0)",
        (
            artifact_id,
            digest_of(f"in-{artifact_id}"),
            digest_of(f"sum-{artifact_id}"),
            storage_uri,
            space_id,
            dimension,
            job_id,
        ),
    )


def add_outbox(
    connection: sqlite3.Connection,
    event_id: str,
    *,
    artifact_id: str,
    asset_id: int,
    generation_id: str,
    projection_revision: int,
    state: str = "CONFIRMED",
    space_id: str = "space-a",
    event_type: str = "UPSERT_VECTOR",
) -> None:
    connection.execute(
        "INSERT INTO index_outbox(event_id, event_type, artifact_id, space_id, asset_id, "
        "generation_id, projection_revision, payload_json, payload_checksum, state, attempts, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, 0, 0, 0)",
        (
            event_id,
            event_type,
            artifact_id,
            space_id,
            asset_id,
            generation_id,
            projection_revision,
            digest_of(f"payload-{event_id}"),
            state,
        ),
    )


def add_entry(
    connection: sqlite3.Connection,
    *,
    asset_id: int,
    generation_id: str,
    artifact_id: str,
    confirmation_revision: int,
    state: str = "CONFIRMED",
    space_id: str = "space-a",
) -> None:
    connection.execute(
        "INSERT INTO index_entries(space_id, asset_id, generation_id, artifact_id, "
        "confirmation_revision, confirmed_at, state) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            space_id,
            asset_id,
            generation_id,
            artifact_id,
            confirmation_revision,
            None if state != "CONFIRMED" else 1,
            state,
        ),
    )


def add_run(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    profile_id: str,
    generation_id: str,
    space_id: str = "space-a",
    state: str = "PLANNED",
    pause_reason: str | None = None,
    pause_detail: str | None = None,
    budget_targets: int | None = None,
    budget_seconds: int | None = None,
    budget_concurrency: int = 1,
    control_revision: int = 1,
    clock_owner: str | None = None,
    elapsed_ms: int = 0,
) -> None:
    owner = clock_owner if clock_owner is not None else ("session-1" if state == "RUNNING" else None)
    connection.execute(
        "INSERT INTO upgrade_runs(run_id, profile_id, space_id, image_generation_id, "
        "target_set_digest, state, pause_reason, pause_detail, budget_targets, budget_seconds, "
        "budget_concurrency, elapsed_ms, resumed_at, clock_owner, control_revision, "
        "last_error_code, planned_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, 0)",
        (
            run_id,
            profile_id,
            space_id,
            generation_id,
            digest_of(f"plan-{run_id}"),
            state,
            pause_reason,
            pause_detail,
            budget_targets,
            budget_seconds,
            budget_concurrency,
            elapsed_ms,
            0 if state == "RUNNING" else None,
            owner,
            control_revision,
        ),
    )


def add_target(
    connection: sqlite3.Connection,
    run_id: str,
    asset_id: int,
    *,
    state: str = "PLANNED",
    reason: str | None = None,
    attempts: int = 0,
    input_revision: str | None = None,
    lease_token: str | None = None,
    lease_expires_at: int | None = None,
) -> None:
    if state == "IN_FLIGHT":
        lease_token = lease_token or f"lease-{asset_id}"
        lease_expires_at = 1000 if lease_expires_at is None else lease_expires_at
    connection.execute(
        "INSERT INTO upgrade_targets(run_id, asset_id, input_revision, state, reason, "
        "attempts, lease_token, lease_expires_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
        (
            run_id,
            asset_id,
            input_revision or digest_of(f"asset-{asset_id}"),
            state,
            reason,
            attempts,
            lease_token,
            lease_expires_at,
        ),
    )


#: ADR-0013 section 4.1: the one authoritative difference-set predicate.
TARGET_SET_SQL = """
SELECT a.asset_id
FROM assets AS a
WHERE a.state = 'ACTIVE'
  AND (
        NOT EXISTS (
            SELECT 1 FROM index_entries AS e
            WHERE e.space_id = :space_id AND e.asset_id = a.asset_id
              AND e.generation_id = :generation_id
              AND e.state = 'CONFIRMED'
              AND e.confirmation_revision >= COALESCE(
                    (SELECT MAX(o.projection_revision) FROM index_outbox AS o
                     WHERE o.space_id = :space_id AND o.asset_id = a.asset_id
                       AND o.generation_id = :generation_id), 0))
        OR EXISTS (
            SELECT 1 FROM index_outbox AS o2
            WHERE o2.space_id = :space_id AND o2.asset_id = a.asset_id
              AND o2.generation_id = :generation_id
              AND o2.state <> 'CONFIRMED')
  )
ORDER BY a.asset_id
"""


def needs_projection(
    connection: sqlite3.Connection, *, space_id: str, generation_id: str
) -> list[int]:
    return [
        int(row["asset_id"])
        for row in connection.execute(
            TARGET_SET_SQL, {"space_id": space_id, "generation_id": generation_id}
        )
    ]


def coverage_complete(
    connection: sqlite3.Connection, *, space_id: str, generation_id: str
) -> bool:
    """Gate 3 (Sol F-A): re-evaluate the CURRENT difference set over every ACTIVE asset.

    The plan target set is a snapshot. A newly added ACTIVE asset has no entry and no outbox
    row, so "no pending rows" cannot prove that no uncovered asset exists; the bound of the
    check is the current difference set, not the plan.
    """
    if needs_projection(connection, space_id=space_id, generation_id=generation_id):
        return False
    pending_repair = connection.execute(
        "SELECT COUNT(*) FROM index_entries WHERE generation_id = ? AND state = 'PENDING_REPAIR'",
        (generation_id,),
    ).fetchone()[0]
    if int(pending_repair):
        return False
    unconfirmed = connection.execute(
        "SELECT COUNT(*) FROM index_outbox WHERE generation_id = ? AND state <> 'CONFIRMED'",
        (generation_id,),
    ).fetchone()[0]
    return not int(unconfirmed)


def activation_refusal(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    data_root: Path = DATA_ROOT,
    runtime_ok: bool = True,
    cancelled_rollback_generations: tuple[str, ...] = (),
) -> tuple[str, str | None] | None:
    """Gates 1..5 of ADR-0013 section 7; ``None`` means every gate holds."""
    run = connection.execute("SELECT * FROM upgrade_runs WHERE run_id = ?", (run_id,)).fetchone()
    if run is None:
        return ("INVALID_STATE_TRANSITION", "RUN_NOT_FOUND")
    generation = connection.execute(
        "SELECT * FROM index_generations WHERE generation_id = ?",
        (str(run["image_generation_id"]),),
    ).fetchone()
    if generation is None or str(generation["state"]) != "READY":
        return ("INVALID_STATE_TRANSITION", "GENERATION_NOT_READY")
    if str(run["state"]) != "SUCCEEDED":
        return ("INVALID_STATE_TRANSITION", "RUN_NOT_SUCCEEDED")
    if not coverage_complete(
        connection,
        space_id=str(run["space_id"]),
        generation_id=str(run["image_generation_id"]),
    ):
        return ("INVALID_STATE_TRANSITION", "COVERAGE_INCOMPLETE")
    uris = connection.execute(
        "SELECT DISTINCT a.storage_uri AS uri FROM index_entries AS e "
        "JOIN artifacts AS a ON a.artifact_id = e.artifact_id WHERE e.generation_id = ?",
        (str(run["image_generation_id"]),),
    ).fetchall()
    for row in uris:
        path = data_root / str(row["uri"])
        # Sol O-2: readability is a *real* read, not an existence probe.
        try:
            with path.open("rb") as handle:
                handle.read(1)
            readable = True
        except OSError:  # pragma: no cover - an unreadable path is a platform fact
            readable = False
        if not readable:
            return ("ARTIFACT_MISSING", None)
    profile = connection.execute(
        "SELECT * FROM search_profiles WHERE profile_id = ?", (str(run["profile_id"]),)
    ).fetchone()
    if (
        profile is None
        or str(profile["space_id"]) != str(run["space_id"])
        or str(profile["image_generation_id"]) != str(run["image_generation_id"])
    ):
        return ("PROFILE_GENERATION_MISMATCH", "PROFILE_REFERENCE_CHANGED")
    if str(run["image_generation_id"]) in cancelled_rollback_generations:
        return ("INVALID_STATE_TRANSITION", "GENERATION_REFERENCED")
    if not runtime_ok:
        return ("RUNTIME_CAPABILITY_UNAVAILABLE", "QUALIFICATION_MISSING")
    return None


def reference_activate(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    now_ms: int = 1000,
    activation_hook: Any = None,
    **kwargs: Any,
) -> None:
    """Gates 1..5 and the pointer move inside ONE ``BEGIN IMMEDIATE`` transaction (Sol O-2).

    A refusal or any fault before COMMIT rolls the whole transaction back, so the pointer,
    ``index_generations.is_active`` and the run all stay exactly as they were.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        refusal = activation_refusal(connection, run_id=run_id, **kwargs)
        if refusal is not None:
            raise Refused(*refusal)
        run = connection.execute(
            "SELECT * FROM upgrade_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        connection.execute(
            "UPDATE index_generations SET is_active = 0, updated_at = ? "
            "WHERE space_id = ? AND is_active = 1",
            (now_ms, str(run["space_id"])),
        )
        connection.execute(
            "UPDATE index_generations SET is_active = 1, updated_at = ? WHERE generation_id = ?",
            (now_ms, str(run["image_generation_id"])),
        )
        connection.execute(
            "UPDATE active_search_profile SET profile_id = ?, updated_at = ? WHERE state_id = 1",
            (str(run["profile_id"]), now_ms),
        )
        if activation_hook is not None:
            activation_hook(connection)
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def catalog_snapshot(connection: sqlite3.Connection) -> tuple[Any, ...]:
    """Everything the zero-write obligation covers: pointer, generations and run controls."""
    pointer = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT state_id, profile_id, updated_at FROM active_search_profile ORDER BY state_id"
        )
    )
    generations = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT generation_id, state, is_active, updated_at FROM index_generations "
            "ORDER BY generation_id"
        )
    )
    # Every column of every run row: a no-op must leave the whole row untouched (Sol F-B2).
    runs = tuple(
        tuple(row)
        for row in connection.execute("SELECT * FROM upgrade_runs ORDER BY run_id")
    )
    return (pointer, generations, runs)


def control_log_rows(connection: sqlite3.Connection) -> int:
    return int(
        connection.execute("SELECT COUNT(*) FROM upgrade_control_requests").fetchone()[0]
    )


def retire_blockers(
    connection: sqlite3.Connection,
    generation_id: str,
    *,
    live_sessions: tuple[str, ...] = (),
    rollback_tasks: tuple[str, ...] = (),
    backup_manifests: tuple[str, ...] = (),
) -> list[str]:
    """ADR-0013 section 8: which references block a RETIRED/cleanup of this generation."""
    blockers: list[str] = []
    active = connection.execute(
        "SELECT p.image_generation_id AS generation_id FROM active_search_profile AS a "
        "JOIN search_profiles AS p ON p.profile_id = a.profile_id WHERE a.state_id = 1"
    ).fetchone()
    if active is not None and str(active["generation_id"]) == generation_id:
        blockers.append("ACTIVE_PROFILE")
    if generation_id in live_sessions:
        blockers.append("LIVE_SEARCH_SESSION")
    if connection.execute(
        "SELECT COUNT(*) FROM upgrade_runs WHERE image_generation_id = ? "
        "AND state NOT IN ('CANCELLED','FAILED','SUCCEEDED')",
        (generation_id,),
    ).fetchone()[0]:
        blockers.append("UPGRADE_RUN_TARGET")
    if generation_id in rollback_tasks:
        blockers.append("ROLLBACK_TASK")
    if generation_id in backup_manifests:
        blockers.append("BACKUP_MANIFEST")
    return blockers


def _activation_fixture() -> sqlite3.Connection:
    """A settled, activatable run: ``prof-new``/``gen-new`` fully covered by ``run-1``."""
    connection = new_catalog()
    add_job(connection)
    add_asset(connection, 1)
    add_generation(connection, "gen-old", is_active=1)
    add_generation(connection, "gen-new")
    add_profile(connection, "prof-old", generation_id="gen-old")
    add_profile(connection, "prof-new", generation_id="gen-new")
    connection.execute(
        "UPDATE active_search_profile SET profile_id = 'prof-old', updated_at = 1 WHERE state_id = 1"
    )
    add_run(connection, "run-1", profile_id="prof-new", generation_id="gen-new", state="SUCCEEDED")
    add_target(connection, "run-1", 1, state="SUCCEEDED")
    add_artifact(connection, "art-1")
    add_outbox(
        connection, "ev1", artifact_id="art-1", asset_id=1, generation_id="gen-new", projection_revision=7
    )
    add_entry(
        connection, asset_id=1, generation_id="gen-new", artifact_id="art-1", confirmation_revision=7
    )
    return connection


# ---------------------------------------------------------------------- companion checks
def test_companion_identity_and_closed_public_objects() -> None:
    assert COMPANION["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert COMPANION["$id"].endswith("/p4-upgrade-contracts.v1.json")
    assert DEFS["ContractVersion"]["const"] == CONTRACT_VERSION
    assert DEFS["RulesVersion"]["const"] == 1
    assert DEFS["SearchContractVersion"]["const"] == "p2-contracts.v1"
    assert DEFS["SchemaVersionBefore"]["const"] == 16
    assert DEFS["SchemaVersionAfter"]["const"] == 17
    assert DEFS["MigrationFile"]["const"] == "0017_upgrade_runs.sql"
    assert DEFS["MaxPauseDetailChars"]["const"] == MAX_PAUSE_DETAIL_CHARS
    assert contract_errors(
        {
            "contract_version": CONTRACT_VERSION,
            "search_contract_version": "p2-contracts.v1",
            "rules_version": 1,
            "schema_version_before": 16,
            "schema_version_after": 17,
            "migration_file": "0017_upgrade_runs.sql",
        },
        "ContractIdentity",
    ) == []
    assert contract_errors(
        {
            "contract_version": CONTRACT_VERSION,
            "search_contract_version": "p2-contracts.v1",
            "rules_version": 2,
            "schema_version_before": 16,
            "schema_version_after": 17,
            "migration_file": "0017_upgrade_runs.sql",
        },
        "ContractIdentity",
    )
    assert contract_errors(
        {
            "contract_version": CONTRACT_VERSION,
            "search_contract_version": "p2-contracts.v1",
            "rules_version": 1,
            "schema_version_before": 16,
            "schema_version_after": 18,
            "migration_file": "0017_upgrade_runs.sql",
        },
        "ContractIdentity",
    )

    public = [branch["$ref"].rsplit("/", 1)[-1] for branch in COMPANION["oneOf"]]
    assert len(public) == len(set(public))
    for name in public:
        definition = DEFS[name]
        if "oneOf" in definition or "const" in definition:
            # A frozen rule or row set (Sol F-D): every branch is itself closed.
            for branch in definition.get("oneOf", [definition]):
                assert "const" in branch or branch.get("additionalProperties") is False, name
            continue
        assert definition.get("type") == "object", name
        assert definition.get("additionalProperties") is False, name
        assert definition.get("required"), name


def test_companion_has_no_orphan_definitions() -> None:
    reachable = referenced_defs()
    assert reachable == set(DEFS), sorted(set(DEFS) - reachable)
    assert "RunTransitionKey" not in DEFS
    assert "TargetTransitionKey" not in DEFS


def test_companion_closed_vocabularies() -> None:
    assert tuple(DEFS["RunState"]["enum"]) == RUN_STATES
    assert tuple(DEFS["PauseReason"]["enum"]) == PAUSE_REASONS
    assert tuple(DEFS["TargetState"]["enum"]) == TARGET_STATES
    assert tuple(DEFS["FailureReason"]["enum"]) == FAILURE_REASONS
    assert tuple(DEFS["ReferenceKind"]["enum"]) == REFERENCE_KINDS
    assert tuple(DEFS["GateId"]["enum"]) == tuple(EXPECTED_GATES)
    assert DEFS["ActivationGateMatrix"]["properties"]["gates_total"]["const"] == 6


def test_run_transition_matrix_is_exact() -> None:
    matrix = DEFS["RunTransitionMatrix"]["properties"]
    assert tuple(matrix["allowed"]["const"]) == ALLOWED_RUN_TRANSITIONS
    assert tuple(matrix["rejected"]["const"]) == NAMED_REJECTED_RUN_TRANSITIONS
    assert tuple(matrix["terminal_states"]["const"]) == TERMINAL_RUN_STATES
    assert tuple(matrix["idempotent_actions"]["const"]) == (
        {"state": "PAUSED", "action": "pause"},
        {"state": "RUNNING", "action": "resume"},
        {"state": "CANCELLED", "action": "cancel"},
    )
    # The reference model is the frozen matrix: every pair the ADR does not list is refused.
    for source in RUN_STATES:
        for target in RUN_STATES:
            expected = f"{source}->{target}" in ALLOWED_RUN_TRANSITIONS
            assert run_transition_allowed(source, target) is expected, (source, target)
    for key in NAMED_REJECTED_RUN_TRANSITIONS:
        source, target = key.split("->")
        assert not run_transition_allowed(source, target), key
    assert set(NAMED_REJECTED_RUN_TRANSITIONS) | set(ALLOWED_RUN_TRANSITIONS)
    assert not set(NAMED_REJECTED_RUN_TRANSITIONS) & set(ALLOWED_RUN_TRANSITIONS)


def test_target_transition_matrix_is_exact() -> None:
    matrix = DEFS["TargetTransitionMatrix"]["properties"]
    assert tuple(matrix["allowed"]["const"]) == ALLOWED_TARGET_TRANSITIONS
    assert tuple(matrix["rejected"]["const"]) == NAMED_REJECTED_TARGET_TRANSITIONS
    assert tuple(matrix["terminal_states"]["const"]) == TERMINAL_TARGET_STATES
    for source in TARGET_STATES:
        for target in TARGET_STATES:
            assert target_transition_allowed(source, target) is (
                f"{source}->{target}" in ALLOWED_TARGET_TRANSITIONS
            )
    for key in NAMED_REJECTED_TARGET_TRANSITIONS:
        source, target = key.split("->")
        assert not target_transition_allowed(source, target), key


def test_terminal_run_states_never_reopen() -> None:
    idempotent = {
        (entry["state"], entry["action"])
        for entry in DEFS["RunTransitionMatrix"]["properties"]["idempotent_actions"]["const"]
    }
    assert idempotent == {("PAUSED", "pause"), ("RUNNING", "resume"), ("CANCELLED", "cancel")}
    for state in TERMINAL_RUN_STATES:
        assert not run_transition_allowed(state, "RUNNING")
        with pytest.raises(Refused) as refused:
            control_run(state, "resume")
        assert refused.value.code == "INVALID_STATE_TRANSITION"
        with pytest.raises(Refused) as paused_refused:
            control_run(state, "pause", pause_reason="USER")
        assert paused_refused.value.code == "INVALID_STATE_TRANSITION"
        # cancel is an idempotent replay only on an already-cancelled run.
        if state == "CANCELLED":
            assert control_run(state, "cancel") == state
        else:
            with pytest.raises(Refused) as cancel_refused:
                control_run(state, "cancel")
            assert cancel_refused.value.code == "INVALID_STATE_TRANSITION"
    # A soft stop is not a terminal state, and it may not jump straight to SUCCEEDED.
    assert not run_transition_allowed("PAUSED", "SUCCEEDED")
    assert control_run("PLANNED", "pause", pause_reason="USER") == "PAUSED"
    with pytest.raises(Refused) as missing_reason:
        control_run("PLANNED", "pause")
    assert missing_reason.value.code == "VALIDATION_ERROR"


def test_pause_detail_rule_is_frozen_by_the_companion() -> None:
    rule = DEFS["PauseDetailRule"]
    assert rule["properties"]["pattern"]["const"] == PAUSE_DETAIL_PATTERN
    assert _resolve(rule["properties"]["max_chars"])["const"] == MAX_PAUSE_DETAIL_CHARS
    assert tuple(rule["properties"]["required_when"]["const"]) == ("ENVIRONMENT",)
    assert contract_errors(
        {
            "max_chars": MAX_PAUSE_DETAIL_CHARS,
            "pattern": PAUSE_DETAIL_PATTERN,
            "required_when": ["ENVIRONMENT"],
            "forbidden_characters": list(
                rule["properties"]["forbidden_characters"]["const"]
            ),
        },
        "PauseDetailRule",
    ) == []
    pattern = re.compile(PAUSE_DETAIL_PATTERN)
    assert pattern.fullmatch("CAPABILITY_MISSING")
    assert pattern.fullmatch("x" * MAX_PAUSE_DETAIL_CHARS)
    for bad in (
        "x" * (MAX_PAUSE_DETAIL_CHARS + 1),
        "C:/photos",
        "C:\\photos",
        "a/b",
        "a\\b",
        "a:b",
        "a\nb",
        "a\rb",
        "a\tb",
        "a\x00b",
        "a\x85b",
        "a\x9fb",
        "\x7f",
        "",
    ):
        assert pattern.fullmatch(bad) is None, bad  # Sol O-3: DEL and C1 are refused too


def test_run_row_contract_pins_pause_and_wall_clock_rules() -> None:
    row = {
        "run_id": "run-1",
        "profile_id": "prof-new",
        "space_id": "space-a",
        "image_generation_id": "gen-new",
        "target_set_digest": "a" * 64,
        "state": "PAUSED",
        "pause_reason": "ENVIRONMENT",
        "pause_detail": None,
        "budget_targets": None,
        "budget_seconds": None,
        "budget_concurrency": 1,
        "elapsed_ms": 0,
        "resumed_at": None,
        "clock_owner": None,
        "control_revision": 1,
        "last_error_code": None,
        "planned_at": 0,
        "updated_at": 0,
    }
    errors = contract_errors(row, "UpgradeRunRow")
    assert any("pause_detail" in error for error in errors), errors
    assert contract_errors(dict(row, pause_detail="DISK_SPACE"), "UpgradeRunRow") == []
    assert contract_errors(
        dict(row, pause_reason="USER", pause_detail=None), "UpgradeRunRow"
    ) == []
    for bad_detail in ("C:\\photos", "a/b", "a\nb", "x" * 201):
        assert contract_errors(dict(row, pause_detail=bad_detail), "UpgradeRunRow"), bad_detail
    # pause_reason/pause_detail are NULL outside PAUSED; resumed_at is set exactly on RUNNING.
    # Sol F-C: resumed_at and clock_owner are born and die together with RUNNING.
    assert contract_errors(dict(row, state="RUNNING", resumed_at=5), "UpgradeRunRow")
    assert contract_errors(dict(row, state="RUNNING", resumed_at=None), "UpgradeRunRow")
    assert contract_errors(
        dict(row, state="RUNNING", resumed_at=5, clock_owner="session-1"), "UpgradeRunRow"
    )
    assert contract_errors(
        dict(row, state="RUNNING", pause_reason=None, pause_detail=None, resumed_at=5,
             clock_owner="session-1"),
        "UpgradeRunRow",
    ) == []
    assert contract_errors(
        dict(row, state="PAUSED", pause_reason="USER", clock_owner="session-1"), "UpgradeRunRow"
    )
    assert contract_errors(
        dict(row, state="CANCELLED", pause_reason=None, pause_detail=None), "UpgradeRunRow"
    ) == []
    assert contract_errors(dict(row, state="CANCELLED", resume_reason="x"), "UpgradeRunRow")


def test_target_rows_require_a_reason_exactly_when_failed() -> None:
    row = {
        "run_id": "run-1",
        "asset_id": 1,
        "input_revision": "a" * 64,
        "state": "FAILED",
        "reason": None,
        "attempts": 1,
        "lease_token": None,
        "lease_expires_at": None,
        "updated_at": 0,
    }
    assert any("reason" in error for error in contract_errors(row, "UpgradeTargetRow"))
    assert contract_errors(dict(row, reason="SOURCE_OFFLINE"), "UpgradeTargetRow") == []
    assert any(
        "reason" in error
        for error in contract_errors(
            dict(row, state="PLANNED", reason="SOURCE_OFFLINE"), "UpgradeTargetRow"
        )
    )
    assert any(
        "enum" in error
        for error in contract_errors(dict(row, reason="NOT_A_REASON"), "UpgradeTargetRow")
    )
    assert any(
        "enum" in error
        for error in contract_errors(dict(row, state="NOT_A_STATE"), "UpgradeTargetRow")
    )
    # Sol F-E: only IN_FLIGHT carries a lease, and it must carry both columns.
    leased = dict(row, state="PLANNED", reason=None, lease_token="lease-1", lease_expires_at=10)
    assert contract_errors(leased, "UpgradeTargetRow")
    assert contract_errors(
        dict(row, state="IN_FLIGHT", reason=None, lease_token=None, lease_expires_at=None),
        "UpgradeTargetRow",
    )
    assert contract_errors(
        dict(row, state="IN_FLIGHT", reason=None, lease_token="lease-1", lease_expires_at=10),
        "UpgradeTargetRow",
    ) == []


def test_failure_mapping_uses_only_existing_error_codes() -> None:
    """The zero-new-error-code promise, checked against the real product module."""
    codes = tuple(DEFS["ErrorCode"]["enum"])
    assert codes
    for code in codes:
        assert resolve_error_code(code) == code, code
        assert status_for_code(code) in {404, 409, 413, 422, 500}, code
    # The statuses ADR-0013 pins must not drift.
    for code, status in (
        ("ARTIFACT_MISSING", 409),
        ("INVALID_STATE_TRANSITION", 409),
        ("RUNTIME_CAPABILITY_UNAVAILABLE", 409),
        ("SOURCE_OFFLINE", 409),
        ("LEASE_EXPIRED", 409),
        ("DISK_SPACE_INSUFFICIENT", 409),
        ("SCHEDULER_PAUSED", 409),
        ("PROFILE_GENERATION_MISMATCH", 409),
        ("SCHEMA_TOO_NEW", 409),
        ("VALIDATION_ERROR", 422),
    ):
        assert status_for_code(code) == status, code

    mapping = DEFS["FailureMapping"]
    assert mapping["properties"]["no_new_error_codes"]["const"] is True
    assert tuple(mapping["properties"]["forbidden_behaviours"]["const"]) == (
        "IMPLICIT_CPU_FALLBACK",
        "SILENT_PROFILE_SWITCH",
    )
    rows = mapping["properties"]["rows"]
    assert rows["minItems"] == rows["maxItems"] == len(FAILURE_REASONS)
    assert tuple(DEFS["FailureReason"]["enum"]) == tuple(EXPECTED_FAILURE_MAPPING)
    for reason, (code, details_reason, location, retryable, counts) in EXPECTED_FAILURE_MAPPING.items():
        assert code in codes, reason
        assert location in {"TARGET", "RUN"}
        assert details_reason is None or details_reason == reason
        assert isinstance(retryable, bool) and isinstance(counts, bool)

    assert contract_errors(
        {
            "rows": [
                {
                    "reason": reason,
                    "error_code": code,
                    "error_details_reason": details_reason,
                    "location": location,
                    "retryable": retryable,
                    "counts_toward_attempts": counts,
                }
                for reason, (code, details_reason, location, retryable, counts) in (
                    EXPECTED_FAILURE_MAPPING.items()
                )
            ],
            "control_plane_refusals": [
                {"code": "SCHEDULER_PAUSED", "when": "global scheduler persistently paused"},
                {
                    "code": "INVALID_STATE_TRANSITION",
                    "when": "illegal transition or referenced generation",
                },
            ],
            "no_new_error_codes": True,
            "forbidden_behaviours": ["IMPLICIT_CPU_FALLBACK", "SILENT_PROFILE_SWITCH"],
        },
        "FailureMapping",
    ) == []
    # A row that maps to a code outside the frozen enum, or a bad location, is rejected.
    assert contract_errors(
        {
            "rows": [
                {
                    "reason": reason,
                    "error_code": "UPGRADE_FAILED",
                    "error_details_reason": None,
                    "location": location,
                    "retryable": retryable,
                    "counts_toward_attempts": counts,
                }
                for reason, (_code, _reason, location, retryable, counts) in (
                    EXPECTED_FAILURE_MAPPING.items()
                )
            ],
            "control_plane_refusals": [
                {"code": "SCHEDULER_PAUSED", "when": "x"},
                {"code": "INVALID_STATE_TRANSITION", "when": "y"},
            ],
            "no_new_error_codes": True,
            "forbidden_behaviours": ["IMPLICIT_CPU_FALLBACK", "SILENT_PROFILE_SWITCH"],
        },
        "FailureMapping",
    )


def _expected_gate_rows() -> list[dict[str, Any]]:
    return [
        {
            "gate": gate,
            "refusals": [
                {"code": code, "details_reasons": list(reasons)} for code, reasons in refusals
            ],
            "zero_writes_on_refusal": True,
        }
        for gate, refusals in EXPECTED_GATES.items()
    ]


def _expected_matrix() -> dict[str, Any]:
    return {
        "gates": _expected_gate_rows(),
        "gates_total": 6,
        "gate_vocabulary": list(EXPECTED_GATE_VOCABULARY),
        "failure_atomicity": "any_failure_is_zero_write",
        "atomic_pointer_move": (
            "active_search_profile_and_index_generations_is_active_in_one_transaction"
        ),
        "envelope_unchanged": True,
        "gate_and_pointer_in_one_transaction": True,
        "artifact_readability_is_a_real_read": True,
        "activation_reevaluates_difference_set": True,
    }


def test_activation_gate_matrix_pins_five_gates_plus_atomicity() -> None:
    matrix = DEFS["ActivationGateMatrix"]["properties"]
    assert matrix["gates_total"]["const"] == 6
    assert matrix["failure_atomicity"]["const"] == "any_failure_is_zero_write"
    assert matrix["atomic_pointer_move"]["const"] == (
        "active_search_profile_and_index_generations_is_active_in_one_transaction"
    )
    assert matrix["envelope_unchanged"]["const"] is True
    assert matrix["gate_and_pointer_in_one_transaction"]["const"] is True
    assert matrix["artifact_readability_is_a_real_read"]["const"] is True
    assert matrix["activation_reevaluates_difference_set"]["const"] is True
    assert matrix["gates"]["minItems"] == matrix["gates"]["maxItems"] == 5
    assert matrix["gates"]["uniqueItems"] is True
    assert tuple(EXPECTED_GATE_VOCABULARY) == tuple(DEFS["GateId"]["enum"])
    for refusals in EXPECTED_GATES.values():
        for code, _reasons in refusals:
            assert code in tuple(DEFS["ErrorCode"]["enum"])
    # Sol F-D: the exact matrix is accepted, and every mutation of it is rejected.
    assert contract_errors(_expected_matrix(), "ActivationGateMatrix") == []
    mutations = {
        "wrong refusal code": {
            "gate": "GENERATION_READY",
            "refusals": [{"code": "ARTIFACT_MISSING", "details_reasons": ["GENERATION_NOT_READY"]}],
            "zero_writes_on_refusal": True,
        },
        "wrong details reason": {
            "gate": "GENERATION_READY",
            "refusals": [{"code": "INVALID_STATE_TRANSITION", "details_reasons": ["RUN_NOT_SUCCEEDED"]}],
            "zero_writes_on_refusal": True,
        },
        "legal-but-wrong gate": {
            "gate": "MADE_UP_GATE",
            "refusals": [{"code": "INVALID_STATE_TRANSITION", "details_reasons": ["GENERATION_NOT_READY"]}],
            "zero_writes_on_refusal": True,
        },
        "no zero-write promise": {
            "gate": "ARTIFACT_READABLE",
            "refusals": [{"code": "ARTIFACT_MISSING", "details_reasons": []}],
            "zero_writes_on_refusal": False,
        },
    }
    for label, row in mutations.items():
        broken = _expected_matrix()
        broken["gates"] = [
            row if candidate["gate"] == row["gate"] else candidate for candidate in broken["gates"]
        ]
        if row["gate"] not in EXPECTED_GATES:
            broken["gates"][0] = row
        assert contract_errors(broken, "ActivationGateMatrix"), label
    # Membership completeness: a duplicated row or a missing row is refused too.
    duplicated = _expected_matrix()
    duplicated["gates"] = [duplicated["gates"][0]] * 5
    assert contract_errors(duplicated, "ActivationGateMatrix")
    shortened = _expected_matrix()
    shortened["gates"] = shortened["gates"][:4]
    assert contract_errors(shortened, "ActivationGateMatrix")
    wrong_vocabulary = _expected_matrix()
    wrong_vocabulary["gate_vocabulary"] = list(EXPECTED_GATE_VOCABULARY[:-1])
    assert contract_errors(wrong_vocabulary, "ActivationGateMatrix")


def test_budget_rule_boundaries_and_any_first_wins() -> None:
    rule = DEFS["BudgetRule"]["properties"]
    assert rule["claim_bound"]["const"] == "min(budget_concurrency, remaining_targets)"
    assert rule["stop_predicate"]["const"] == "targets_or_seconds"
    assert rule["soft_stop_state"]["const"] == "PAUSED"
    assert rule["concurrency_is_not_a_stop"]["const"] is True
    assert rule["wall_clock_freezes_while_paused"]["const"] is True
    assert rule["completion_precedes_budget_pause"]["const"] is True
    assert rule["budget_targets"]["oneOf"][0]["maximum"] == MAX_BUDGET_TARGETS
    assert rule["budget_seconds"]["oneOf"][0]["maximum"] == MAX_BUDGET_SECONDS
    assert rule["budget_concurrency"]["maximum"] == MAX_BUDGET_CONCURRENCY

    def budget_object(**overrides: Any) -> dict[str, Any]:
        base = {
            "budget_targets": None,
            "budget_seconds": None,
            "budget_concurrency": 1,
            "elapsed_ms": 0,
            "claim_bound": "min(budget_concurrency, remaining_targets)",
            "stop_predicate": "targets_or_seconds",
            "soft_stop_state": "PAUSED",
            "concurrency_is_not_a_stop": True,
            "wall_clock_freezes_while_paused": True,
            "completion_precedes_budget_pause": True,
        }
        base.update(overrides)
        return base

    assert contract_errors(budget_object(), "BudgetRule") == []
    assert contract_errors(
        budget_object(budget_targets=MAX_BUDGET_TARGETS, budget_seconds=MAX_BUDGET_SECONDS),
        "BudgetRule",
    ) == []
    for bad in (0, -1, MAX_BUDGET_TARGETS + 1):
        assert contract_errors(budget_object(budget_targets=bad), "BudgetRule"), bad
    assert contract_errors(budget_object(budget_seconds=0), "BudgetRule")
    assert contract_errors(budget_object(budget_concurrency=0), "BudgetRule")
    assert contract_errors(budget_object(budget_concurrency=MAX_BUDGET_CONCURRENCY + 1), "BudgetRule")

    # targets first, seconds first, and "neither" as the three positive/negative controls.
    assert budget_stop(budget_targets=10, budget_seconds=None, completed_targets=10, elapsed_ms=0)
    assert not budget_stop(budget_targets=10, budget_seconds=None, completed_targets=9, elapsed_ms=10**9)
    assert budget_stop(budget_targets=None, budget_seconds=2, completed_targets=0, elapsed_ms=2000)
    assert not budget_stop(budget_targets=None, budget_seconds=2, completed_targets=99, elapsed_ms=1999)
    assert not budget_stop(
        budget_targets=None, budget_seconds=None, completed_targets=10**6, elapsed_ms=10**12
    )
    # The concurrency cap bounds one claim and never stops the run.
    assert claim_batch_size(budget_concurrency=3, budget_targets=None, completed_targets=0) == 3
    assert claim_batch_size(budget_concurrency=3, budget_targets=5, completed_targets=4) == 1
    assert claim_batch_size(budget_concurrency=3, budget_targets=5, completed_targets=5) == 0
    assert not budget_stop(budget_targets=5, budget_seconds=None, completed_targets=4, elapsed_ms=0)


def test_budget_limit_is_a_pause_that_can_resume() -> None:
    paused = control_run("RUNNING", "pause", pause_reason="BUDGET")
    assert paused == "PAUSED"
    assert run_transition_allowed("PAUSED", "RUNNING")
    assert control_run("PAUSED", "resume") == "RUNNING"
    assert control_run("PAUSED", "pause", pause_reason="BUDGET") == "PAUSED"
    # A soft stop must never be recorded as a terminal state.
    assert run_transition_allowed("RUNNING", "PAUSED") is True
    assert run_transition_allowed("PAUSED", "RUNNING") is True
    assert "PAUSED" not in TERMINAL_RUN_STATES
    assert not run_transition_allowed("PLANNED", "FAILED")
    # Resume never resets the budget: completed targets are counted cumulatively.
    assert budget_stop(budget_targets=4, budget_seconds=None, completed_targets=4, elapsed_ms=0)
    assert resume_targets(("SUCCEEDED", "PLANNED", "FAILED", "CANCELLED")) == (
        "PLANNED",
        "FAILED",
        "CANCELLED",
    )
    # elapsed_ms is frozen while PAUSED and only advances while RUNNING.
    assert advance_elapsed("PAUSED", 5000, 60_000) == 5000
    assert advance_elapsed("RUNNING", 5000, 1000) == 6000
    assert advance_elapsed("SUCCEEDED", 7000, 1000) == 7000


def test_cancelled_runs_are_not_resumable() -> None:
    assert control_run("RUNNING", "cancel") == "CANCELLED"
    assert control_run("CANCELLED", "cancel") == "CANCELLED"  # idempotent replay only
    with pytest.raises(Refused) as refused:
        control_run("CANCELLED", "resume")
    assert refused.value.code == "INVALID_STATE_TRANSITION"
    for state in TERMINAL_RUN_STATES:
        assert not run_transition_allowed(state, "RUNNING")


def test_target_set_predicate_selects_only_missing_or_stale_coverage() -> None:
    connection = new_catalog()
    add_job(connection)
    for asset_id in range(1, 7):
        add_asset(connection, asset_id)
    add_generation(connection, "gen-new")
    for asset_id in range(1, 7):
        add_artifact(connection, f"art-{asset_id}")
    # 1: current CONFIRMED coverage -> not a target.
    add_outbox(
        connection, "ev1", artifact_id="art-1", asset_id=1, generation_id="gen-new", projection_revision=7
    )
    add_entry(
        connection, asset_id=1, generation_id="gen-new", artifact_id="art-1", confirmation_revision=7
    )
    # 2: no entry at all -> target.
    # 3: CONFIRMED but older than the triple's authority revision -> target.
    add_outbox(
        connection, "ev3", artifact_id="art-3", asset_id=3, generation_id="gen-new", projection_revision=7
    )
    add_entry(
        connection, asset_id=3, generation_id="gen-new", artifact_id="art-3", confirmation_revision=5
    )
    # 4: current revision, but an unconfirmed outbox row still pins the triple -> target.
    add_outbox(
        connection, "ev4a", artifact_id="art-4", asset_id=4, generation_id="gen-new", projection_revision=9
    )
    add_outbox(
        connection,
        "ev4b",
        artifact_id="art-4",
        asset_id=4,
        generation_id="gen-new",
        projection_revision=9,
        state="PENDING",
        event_type="DELETE_VECTOR",
    )
    add_entry(
        connection, asset_id=4, generation_id="gen-new", artifact_id="art-4", confirmation_revision=9
    )
    # 5: covered in a *different* space -> still a target in this space.
    add_generation(connection, "gen-other", space_id="space-b", processor_id="emb-other")
    add_outbox(
        connection,
        "ev5",
        artifact_id="art-5",
        asset_id=5,
        generation_id="gen-other",
        projection_revision=3,
        space_id="space-b",
    )
    add_entry(
        connection,
        asset_id=5,
        generation_id="gen-other",
        artifact_id="art-5",
        confirmation_revision=3,
        space_id="space-b",
    )
    # 6: PENDING_REPAIR is not CONFIRMED -> target.
    add_outbox(
        connection, "ev6", artifact_id="art-6", asset_id=6, generation_id="gen-new", projection_revision=7
    )
    add_entry(
        connection,
        asset_id=6,
        generation_id="gen-new",
        artifact_id="art-6",
        confirmation_revision=7,
        state="PENDING_REPAIR",
    )
    assert needs_projection(connection, space_id="space-a", generation_id="gen-new") == [2, 3, 4, 5, 6]
    # The predicate is real SQLite over the existing authority columns, not a string assertion.
    assert "index_entries" in TARGET_SET_SQL and "index_outbox" in TARGET_SET_SQL
    assert "confirmation_revision" in TARGET_SET_SQL and "projection_revision" in TARGET_SET_SQL
    # assets.state is frozen to 'ACTIVE' by 0001, so availability cannot silently widen the set.
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE assets SET state = 'MISSING' WHERE asset_id = 1")


def test_plan_idempotency_is_enforced_by_the_primary_key() -> None:
    connection = new_catalog()
    add_job(connection)
    add_asset(connection, 1)
    add_generation(connection, "gen-new")
    add_profile(connection, "prof-new", generation_id="gen-new")
    add_run(connection, "run-1", profile_id="prof-new", generation_id="gen-new")
    add_target(connection, "run-1", 1)
    with pytest.raises(sqlite3.IntegrityError):
        add_target(connection, "run-1", 1)  # a second plan row for the same asset is refused
    # A replay through ON CONFLICT DO NOTHING changes neither the row count nor its progress.
    connection.execute(
        "INSERT INTO upgrade_targets(run_id, asset_id, input_revision, state, reason, attempts, "
        "updated_at) VALUES ('run-1', 1, ?, 'PLANNED', NULL, 0, 0) "
        "ON CONFLICT(run_id, asset_id) DO NOTHING",
        ("b" * 64,),
    )
    rows = [
        tuple(row)
        for row in connection.execute(
            "SELECT asset_id, state, attempts FROM upgrade_targets WHERE run_id = 'run-1'"
        )
    ]
    assert rows == [(1, "PLANNED", 0)]
    # The row-level state machine refuses a reopen and requires the reason exactly on FAILED.
    connection.execute("UPDATE upgrade_targets SET state = 'FAILED', reason = 'SOURCE_OFFLINE' WHERE asset_id = 1")
    assert tuple(DEFS["TargetState"]["enum"]) == TARGET_STATES
    assert target_transition_allowed("FAILED", "PLANNED")
    assert not target_transition_allowed("SUCCEEDED", "PLANNED")


def test_activation_gates_hold_and_move_the_pointer_atomically() -> None:
    connection = _activation_fixture()
    assert activation_refusal(connection, run_id="run-1") is None
    before = catalog_snapshot(connection)
    reference_activate(connection, run_id="run-1")
    after = catalog_snapshot(connection)
    assert before != after
    pointer = connection.execute(
        "SELECT profile_id FROM active_search_profile WHERE state_id = 1"
    ).fetchone()[0]
    assert pointer == "prof-new"
    active = {
        str(row["generation_id"]): int(row["is_active"])
        for row in connection.execute("SELECT generation_id, is_active FROM index_generations")
    }
    assert active == {"gen-old": 0, "gen-new": 1}
    # search_profiles stays immutable (0007's trigger), so the switch can never be a half-edit.
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE search_profiles SET runtime_package_id = 'pkg-2' WHERE profile_id = 'prof-new'"
        )


def _gate_mutation_scripts() -> list[tuple[str, dict[str, Any]]]:
    """The five evaluable gates, each broken one at a time."""
    return [
        (
            "GENERATION_READY",
            {"sql": "UPDATE index_generations SET state = 'PENDING' WHERE generation_id = 'gen-new'"},
        ),
        (
            "RUN_SUCCEEDED",
            {
                "sql": "UPDATE upgrade_runs SET state = 'RUNNING', resumed_at = 5, "
                "clock_owner = 'session-1' WHERE run_id = 'run-1'"
            },
        ),
        (
            "AUTHORITATIVE_COVERAGE_COMPLETE",
            {"sql": "UPDATE index_entries SET confirmation_revision = 1 WHERE asset_id = 1"},
        ),
        (
            "AUTHORITATIVE_COVERAGE_COMPLETE",
            {
                "sql": "UPDATE index_entries SET state = 'PENDING_REPAIR', confirmed_at = NULL "
                "WHERE asset_id = 1"
            },
        ),
        (
            "AUTHORITATIVE_COVERAGE_COMPLETE",
            {"sql": "UPDATE index_outbox SET state = 'PENDING' WHERE event_id = 'ev1'"},
        ),
        (
            "ARTIFACT_READABLE",
            {
                "sql": "UPDATE artifacts SET storage_uri = ? WHERE artifact_id = 'art-1'",
                "args": (MISSING_ARTIFACT_URI,),
            },
        ),
        ("REFERENCES_PROTECTED", {"kwargs": {"runtime_ok": False}}),
        (
            "REFERENCES_PROTECTED",
            {"kwargs": {"cancelled_rollback_generations": ("gen-new",)}},
        ),
    ]


def test_every_activation_gate_refuses_with_zero_writes() -> None:
    expectations = {
        "GENERATION_READY": ("INVALID_STATE_TRANSITION", "GENERATION_NOT_READY"),
        "RUN_SUCCEEDED": ("INVALID_STATE_TRANSITION", "RUN_NOT_SUCCEEDED"),
        "AUTHORITATIVE_COVERAGE_COMPLETE": ("INVALID_STATE_TRANSITION", "COVERAGE_INCOMPLETE"),
        "ARTIFACT_READABLE": ("ARTIFACT_MISSING", None),
        "REFERENCES_PROTECTED": ("RUNTIME_CAPABILITY_UNAVAILABLE", "QUALIFICATION_MISSING"),
    }
    seen: set[str] = set()
    for gate, mutation in _gate_mutation_scripts():
        connection = _activation_fixture()
        if "sql" in mutation:
            connection.execute(mutation["sql"], mutation.get("args", ()))
        kwargs = dict(mutation.get("kwargs", {}))
        if gate == "REFERENCES_PROTECTED" and kwargs.get("cancelled_rollback_generations"):
            expectations_key = ("INVALID_STATE_TRANSITION", "GENERATION_REFERENCED")
        else:
            expectations_key = expectations[gate]
        before = catalog_snapshot(connection)
        with pytest.raises(Refused) as refused:
            reference_activate(connection, run_id="run-1", **kwargs)
        assert (refused.value.code, refused.value.reason) == expectations_key, gate
        assert catalog_snapshot(connection) == before, gate
        seen.add(gate)
    assert seen == set(EXPECTED_GATES)

    # Gate 5's third branch: a profile republished under the run no longer matches.
    connection = _activation_fixture()
    add_generation(connection, "gen-third", processor_id="emb-other")
    connection.execute("DROP TRIGGER trg_search_profiles_immutable")
    connection.execute(
        "UPDATE search_profiles SET image_generation_id = 'gen-third' WHERE profile_id = 'prof-new'"
    )
    before = catalog_snapshot(connection)
    with pytest.raises(Refused) as refused:
        reference_activate(connection, run_id="run-1")
    assert (refused.value.code, refused.value.reason) == (
        "PROFILE_GENERATION_MISMATCH",
        "PROFILE_REFERENCE_CHANGED",
    )
    assert catalog_snapshot(connection) == before

    # An unknown run id is refused, not invented.
    connection = _activation_fixture()
    before = catalog_snapshot(connection)
    with pytest.raises(Refused) as refused:
        reference_activate(connection, run_id="run-zzz")
    assert (refused.value.code, refused.value.reason) == (
        "INVALID_STATE_TRANSITION",
        "RUN_NOT_FOUND",
    )
    assert catalog_snapshot(connection) == before


def test_reference_protection_blocks_retire_and_cleanup() -> None:
    connection = new_catalog()
    add_job(connection)
    add_generation(connection, "gen-new", is_active=1)
    add_generation(connection, "gen-old")
    add_profile(connection, "prof-new", generation_id="gen-new")
    add_profile(connection, "prof-old", generation_id="gen-old")
    connection.execute(
        "UPDATE active_search_profile SET profile_id = 'prof-new', updated_at = 1 WHERE state_id = 1"
    )
    assert retire_blockers(connection, "gen-new") == ["ACTIVE_PROFILE"]
    assert retire_blockers(
        connection,
        "gen-new",
        live_sessions=("gen-new",),
        rollback_tasks=("gen-new",),
        backup_manifests=("gen-new",),
    ) == [
        "ACTIVE_PROFILE",
        "LIVE_SEARCH_SESSION",
        "ROLLBACK_TASK",
        "BACKUP_MANIFEST",
    ]
    add_run(connection, "run-live", profile_id="prof-new", generation_id="gen-new", state="RUNNING")
    assert "UPGRADE_RUN_TARGET" in retire_blockers(connection, "gen-new")
    # A finished run no longer holds the generation.
    connection.execute(
        "UPDATE upgrade_runs SET state = 'SUCCEEDED', resumed_at = NULL, clock_owner = NULL "
        "WHERE run_id = 'run-live'"
    )
    assert "UPGRADE_RUN_TARGET" not in retire_blockers(connection, "gen-new")
    # Only after every reference is gone may the generation be retired.
    connection.execute(
        "UPDATE index_generations SET is_active = 0 WHERE generation_id = 'gen-new'"
    )
    connection.execute(
        "UPDATE index_generations SET is_active = 1 WHERE generation_id = 'gen-old'"
    )
    connection.execute(
        "UPDATE active_search_profile SET profile_id = 'prof-old', updated_at = 2 WHERE state_id = 1"
    )
    assert retire_blockers(connection, "gen-new") == []
    rule = DEFS["ReferenceProtection"]["properties"]
    assert rule["refusal_code"]["const"] == "INVALID_STATE_TRANSITION"
    assert rule["refusal_details_reason"]["const"] == "GENERATION_REFERENCED"
    assert tuple(_resolve(rule["blocking_references"]["items"])["enum"]) == REFERENCE_KINDS
    assert rule["blocking_references"]["minItems"] == rule["blocking_references"]["maxItems"] == 5
    assert rule["retain_previous_versions_default"]["const"] == 1
    assert rule["retain_switch_is_configurable"]["const"] is True
    assert rule["never_deletes_human_knowledge_or_originals"]["const"] is True


def test_api_projections_are_closed_and_the_error_envelope_adds_no_field() -> None:
    run = {
        "contract_version": CONTRACT_VERSION,
        "run_id": "run-1",
        "profile_id": "prof-new",
        "space_id": "space-a",
        "image_generation_id": "gen-new",
        "state": "PAUSED",
        "pause_reason": "BUDGET",
        "pause_detail": None,
        "control_revision": 3,
        "elapsed_ms": 1000,
        "budget_targets": 10,
        "budget_seconds": None,
        "budget_concurrency": 2,
        "counts": {
            "targets_total": 10,
            "targets_planned": 1,
            "targets_in_flight": 0,
            "targets_succeeded": 9,
            "targets_failed": 0,
            "targets_cancelled": 0,
        },
        "resumable": True,
        "target_set_digest": "a" * 64,
        "planned_at": 0,
        "updated_at": 5,
    }
    assert contract_errors(run, "UpgradeRunProjection") == []
    assert contract_errors(dict(run, extra=1), "UpgradeRunProjection")
    assert contract_errors(dict(run, unbudgeted_field=1), "UpgradeRunProjection")
    # A cancelled/completed run is never resumable, and a non-PAUSED run carries no reason.
    assert contract_errors(
        dict(run, state="CANCELLED", pause_reason=None, resumable=True), "UpgradeRunProjection"
    )
    assert contract_errors(
        dict(run, state="SUCCEEDED", pause_reason="USER", resumable=False),
        "UpgradeRunProjection",
    )
    assert contract_errors(
        dict(run, state="RUNNING", pause_reason=None, pause_detail=None, resumable=True),
        "UpgradeRunProjection",
    ) == []
    assert contract_errors(
        {
            "contract_version": CONTRACT_VERSION,
            "runs": [run],
            "limit": 50,
            "next_cursor": None,
            "has_more": False,
        },
        "UpgradeRunPage",
    ) == []
    detail = {
        "contract_version": CONTRACT_VERSION,
        "run": run,
        "targets": [
            {
                "asset_id": 1,
                "input_revision": "a" * 64,
                "state": "FAILED",
                "reason": None,
                "attempts": 1,
                "updated_at": 3,
            }
        ],
        "targets_limit": 100,
        "targets_next_cursor": None,
        "targets_has_more": False,
    }
    assert any("reason" in error for error in contract_errors(detail, "UpgradeRunDetail"))
    detail["targets"][0]["reason"] = "SOURCE_OFFLINE"
    assert contract_errors(detail, "UpgradeRunDetail") == []
    control = {
        "contract_version": CONTRACT_VERSION,
        "request_id": "req-1",
        "run_id": "run-1",
        "state": "PAUSED",
        "pause_reason": "USER",
        "pause_detail": None,
        "control_revision": 2,
        "changed": True,
        "replayed": False,
    }
    assert contract_errors(control, "UpgradeControlResponse") == []
    assert contract_errors(dict(control, changed=True, replayed=True), "UpgradeControlResponse")
    assert contract_errors(
        dict(control, state="RUNNING", pause_reason=None, changed=False, replayed=True),
        "UpgradeControlResponse",
    ) == []

    # The error envelope has exactly the three existing top-level keys (plus the optional
    # details object); the reference model never adds a fourth.
    payload = error_payload(code="INVALID_STATE_TRANSITION", message="no", request_id="req-1")
    assert set(payload) == {"code", "message", "request_id"}
    with_details = error_payload(
        code="INVALID_STATE_TRANSITION",
        message="no",
        request_id="req-1",
        details={"reason": "GENERATION_REFERENCED"},
    )
    assert set(with_details) == {"code", "message", "request_id", "details"}
    assert contract_errors(payload, "UpgradeApiError") == []
    assert contract_errors(
        {
            "code": "INVALID_STATE_TRANSITION",
            "message": "no",
            "request_id": "req-1",
            "details": {
                "reason": "GENERATION_REFERENCED",
                "blocking_references": ["ACTIVE_PROFILE"],
            },
        },
        "UpgradeApiError",
    ) == []
    assert contract_errors(dict(payload, retryable=True), "UpgradeApiError")
    assert contract_errors(
        {"code": "UPGRADE_FAILED", "message": "no", "request_id": "req-1"}, "UpgradeApiError"
    )


def test_migration_contract_and_byte_identity() -> None:
    contract = DEFS["MigrationContract"]["properties"]
    assert contract["migration_file"]["$ref"] == "#/$defs/MigrationFile"
    assert contract["from_version"]["$ref"] == "#/$defs/SchemaVersionBefore"
    assert contract["to_version"]["$ref"] == "#/$defs/SchemaVersionAfter"
    assert contract["additive_only"]["const"] is True
    assert contract["earlier_migrations_byte_identical"]["const"] is True
    assert contract["transactional_and_idempotent"]["const"] is True
    assert contract["no_down_migration"]["const"] is True
    assert contract["too_new_behaviour"]["const"] == "SCHEMA_TOO_NEW"
    assert contract["unused_upgrade_behaviour_unchanged"]["const"] is True
    assert tuple(contract["table_names"]["const"]) == (
        "upgrade_runs",
        "upgrade_targets",
        "upgrade_control_requests",
    )
    assert contract_errors(
        {
            "migration_file": "0017_upgrade_runs.sql",
            "from_version": 16,
            "to_version": 17,
            "additive_only": True,
            "earlier_migrations_byte_identical": True,
            "transactional_and_idempotent": True,
            "no_down_migration": True,
            "too_new_behaviour": "SCHEMA_TOO_NEW",
            "unused_upgrade_behaviour_unchanged": True,
            "table_names": ["upgrade_runs", "upgrade_targets", "upgrade_control_requests"],
        },
        "MigrationContract",
    ) == []

    # This contract describes its historical v16 -> v17 proposal. Later, unrelated
    # migrations and the current supported version do not change that fixed contract.
    assert DEFS["SchemaVersionBefore"]["const"] == 16
    historical = [(version, path) for version, path in migration_files() if version <= 16]
    assert [version for version, _path in historical] == list(range(1, 17))
    assert not (MIGRATIONS_DIR / "0017_upgrade_runs.sql").exists()
    assert sorted(FROZEN_MIGRATION_HASHES) == sorted(path.name for _v, path in historical)
    for name, expected in FROZEN_MIGRATION_HASHES.items():
        path = MIGRATIONS_DIR / name
        assert path.is_file(), name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, name


def test_adr_records_the_companion_hash_and_the_earlier_companions_are_untouched() -> None:
    recorded = re.findall(r"SHA-256：`([0-9a-f]{64})`", ADR)
    assert len(recorded) == 1, recorded
    assert recorded[0] == hashlib.sha256(COMPANION_PATH.read_bytes()).hexdigest()
    assert "Proposed" in ADR
    assert "ENGINEERING CONTRACT REVIEW PASS / SOL FREEZE PENDING" in ADR
    assert "P4-03B" in ADR
    for name, expected in FROZEN_COMPANIONS.items():
        assert hashlib.sha256((CONTRACTS / name).read_bytes()).hexdigest() == expected, name


def test_the_companion_declares_no_supersession_and_no_private_property() -> None:
    description = COMPANION["description"]
    assert "CONTRACT ONLY" in description
    assert "declares no supersession" in description
    text = COMPANION_PATH.read_text(encoding="utf-8")
    for forbidden in ("storage_uri", "source_root", "relative_path", "taken_at_raw", "root_path"):
        assert forbidden not in text, forbidden


# ------------------------------------------------------- revised-surface reference models
def control_digest(
    *,
    action: str,
    pause_reason: str | None = None,
    pause_detail: str | None = None,
    grant_targets: int | None = None,
    grant_seconds: int | None = None,
) -> str:
    """ADR-0013 section 3.2 (Sol F-B): the digest excludes ``expected_control_revision``."""
    payload = {
        "action": action,
        "pause_reason": pause_reason,
        "pause_detail": pause_detail,
        "grant_targets": grant_targets,
        "grant_seconds": grant_seconds,
    }
    assert set(payload) == set(DIGEST_FIELDS)
    return digest_of(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def apply_grant(*, cap: int | None, grant: int, maximum: int) -> int:
    """ADR-0013 section 5.4 (Sol F-C): an additive budget grant, never a silent reset."""
    if cap is None:
        raise Refused("VALIDATION_ERROR", "GRANT_WITHOUT_CAP")
    if isinstance(grant, bool) or not isinstance(grant, int) or grant < 1:
        raise Refused("VALIDATION_ERROR", "GRANT_NOT_POSITIVE")
    if cap + grant > maximum:
        raise Refused("VALIDATION_ERROR", "GRANT_EXCEEDS_CEILING")
    return cap + grant


def effective_elapsed_ms(
    *,
    state: str,
    elapsed_ms: int,
    resumed_at: int | None,
    clock_owner: str | None,
    session: str,
    now_ms: int,
) -> int:
    """ADR-0013 section 5.2 (Sol F-C): settled segment + the live, owned segment only."""
    if state == "RUNNING" and clock_owner == session and resumed_at is not None:
        return elapsed_ms + (now_ms - resumed_at)
    return elapsed_ms


def reanchor(*, clock_owner: str | None, session: str, now_ms: int) -> tuple[int, str]:
    """Recovery re-anchors unconditionally: the downtime interval is never charged."""
    assert clock_owner != session or clock_owner is None
    return now_ms, session


def settle_on_leave(*, elapsed_ms: int, resumed_at: int | None, clock_owner: str | None,
                    session: str, now_ms: int) -> int:
    if clock_owner != session:
        raise Refused("INVALID_STATE_TRANSITION", "CLOCK_NOT_OWNED")
    assert resumed_at is not None
    return elapsed_ms + (now_ms - resumed_at)


def batch_boundary_decision(*, target_states: tuple[str, ...], budget_reached: bool) -> str:
    """ADR-0013 section 5.3 (Sol F-C): completion precedes the budget soft stop."""
    if not any(state in ("PLANNED", "IN_FLIGHT") for state in target_states) and (
        "FAILED" not in target_states
    ):
        return "SUCCEEDED"
    return "PAUSED" if budget_reached else "RUNNING"


def takeover_expired_lease(
    connection: sqlite3.Connection, *, run_id: str, asset_id: int, now_ms: int
) -> str:
    """Sol F-E: the one expired-lease takeover path, executable, counted exactly once."""
    row = connection.execute(
        "SELECT * FROM upgrade_targets WHERE run_id = ? AND asset_id = ?", (run_id, asset_id)
    ).fetchone()
    if row is None or str(row["state"]) != "IN_FLIGHT":
        raise Refused("INVALID_STATE_TRANSITION", "TAKEOVER_NOT_ELIGIBLE")
    if int(row["lease_expires_at"]) > int(now_ms):
        # A still-valid lease is the zero-count process replay, not a takeover.
        raise Refused("INVALID_STATE_TRANSITION", "LEASE_STILL_VALID")
    delta = attempt_delta(transition="IN_FLIGHT->FAILED", reason="LEASE_EXPIRED")
    connection.execute(
        "UPDATE upgrade_targets SET state = 'FAILED', reason = 'LEASE_EXPIRED', "
        "attempts = attempts + ?, lease_token = NULL, lease_expires_at = NULL, updated_at = ? "
        "WHERE run_id = ? AND asset_id = ? AND state = 'IN_FLIGHT'",
        (delta, now_ms, run_id, asset_id),
    )
    return "FAILED"


def attempt_delta(*, transition: str, reason: str | None) -> int:
    """ADR-0013 section 4.5 (Sol F-E): attempts increments only when entering FAILED."""
    if transition != "IN_FLIGHT->FAILED":
        return 0
    assert reason is not None
    return 1 if EXPECTED_FAILURE_MAPPING[reason][4] else 0


def noop_matches(
    *,
    state: str,
    pause_reason: str | None,
    pause_detail: str | None,
    grant_targets: int | None,
    grant_seconds: int | None,
    run: Any,
) -> bool:
    """Sol F-B2: what "the parameters match the state that already holds" means."""
    if state == "PAUSED":
        return pause_reason == run["pause_reason"] and pause_detail == run["pause_detail"]
    if state == "RUNNING":
        return grant_targets is None and grant_seconds is None
    if state == "CANCELLED":
        return True
    return False


def apply_control_request(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    request_id: str,
    action: str,
    pause_reason: str | None = None,
    pause_detail: str | None = None,
    grant_targets: int | None = None,
    grant_seconds: int | None = None,
    expected_control_revision: int | None = None,
    session: str = "session-1",
    now_ms: int = 2000,
) -> dict[str, Any]:
    """ADR-0013 section 3.2 (Sol F-B) as one ``BEGIN IMMEDIATE`` transaction.

    Replay lookup comes first: a replayed request is answered from the recorded row and is
    never re-judged by the state predicate or by ``expected_control_revision``.
    """
    digest = control_digest(
        action=action,
        pause_reason=pause_reason,
        pause_detail=pause_detail,
        grant_targets=grant_targets,
        grant_seconds=grant_seconds,
    )
    target = {"PAUSE": "PAUSED", "RESUME": "RUNNING", "CANCEL": "CANCELLED"}.get(action)
    if target is None:
        raise Refused("VALIDATION_ERROR", "UNKNOWN_CONTROL_ACTION")
    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = connection.execute(
            "SELECT * FROM upgrade_control_requests WHERE run_id = ? AND request_id = ?",
            (run_id, request_id),
        ).fetchone()
        if existing is not None:
            if str(existing["request_digest"]) != digest:
                raise Refused("VALIDATION_ERROR", "IDEMPOTENCY_CONFLICT")
            connection.execute("COMMIT")
            # Sol F-B1: the response describes *this call*, which wrote nothing.
            return {
                "contract_version": CONTRACT_VERSION,
                "request_id": request_id,
                "run_id": run_id,
                "state": str(existing["outcome_state"]),
                "pause_reason": existing["outcome_pause_reason"],
                "pause_detail": existing["outcome_pause_detail"],
                "control_revision": int(existing["outcome_control_revision"]),
                "changed": False,
                "replayed": True,
            }
        run = connection.execute(
            "SELECT * FROM upgrade_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if run is None:
            raise Refused("INVALID_STATE_TRANSITION", "RUN_NOT_FOUND")
        state = str(run["state"])
        changed = state != target
        if changed and not run_transition_allowed(state, target):
            raise Refused("INVALID_STATE_TRANSITION", f"{state}->{target}")
        if not changed and not noop_matches(
            state=state,
            pause_reason=pause_reason,
            pause_detail=pause_detail,
            grant_targets=grant_targets,
            grant_seconds=grant_seconds,
            run=run,
        ):
            # Sol F-B2: the state already holds but the parameters disagree.
            raise Refused("INVALID_STATE_TRANSITION", "CONTROL_NOOP_ARGUMENT_MISMATCH")
        if expected_control_revision is not None and int(expected_control_revision) != int(
            run["control_revision"]
        ):
            raise Refused("INVALID_STATE_TRANSITION", "CONTROL_REVISION_STALE")
        new_state = target if changed else state
        new_pause_reason = pause_reason if new_state == "PAUSED" else None
        new_pause_detail = pause_detail if new_state == "PAUSED" else None
        new_revision = int(run["control_revision"]) + (1 if changed else 0)
        elapsed = int(run["elapsed_ms"])
        resumed_at = run["resumed_at"]
        owner = run["clock_owner"]
        budget_targets = run["budget_targets"]
        budget_seconds = run["budget_seconds"]
        if changed and state == "RUNNING":
            elapsed = settle_on_leave(
                elapsed_ms=elapsed,
                resumed_at=int(resumed_at),
                clock_owner=None if owner is None else str(owner),
                session=session,
                now_ms=now_ms,
            )
            resumed_at = None
            owner = None
        if changed and new_state == "RUNNING":
            # Sol F-B2-G: a budget grant is only legal on PAUSED -> RUNNING. The check runs
            # before any run/log write, and a replay has already returned above.
            if (grant_targets is not None or grant_seconds is not None) and state != "PAUSED":
                raise Refused("INVALID_STATE_TRANSITION", "GRANT_SOURCE_STATE_NOT_PAUSED")
            if owner is not None and str(owner) != session:
                raise Refused("INVALID_STATE_TRANSITION", "CLOCK_NOT_OWNED")
            resumed_at = now_ms
            owner = session
            if grant_targets is not None:
                budget_targets = apply_grant(
                    cap=None if budget_targets is None else int(budget_targets),
                    grant=grant_targets,
                    maximum=MAX_BUDGET_TARGETS,
                )
            if grant_seconds is not None:
                budget_seconds = apply_grant(
                    cap=None if budget_seconds is None else int(budget_seconds),
                    grant=grant_seconds,
                    maximum=MAX_BUDGET_SECONDS,
                )
        if changed:
            # Sol F-B2: a *first* no-op inserts the log row only; it must not touch any column.
            connection.execute(
                "UPDATE upgrade_runs SET state = ?, pause_reason = ?, pause_detail = ?, "
                "budget_targets = ?, budget_seconds = ?, elapsed_ms = ?, resumed_at = ?, "
                "clock_owner = ?, control_revision = ?, updated_at = ? WHERE run_id = ?",
                (
                    new_state,
                    new_pause_reason,
                    new_pause_detail,
                    budget_targets,
                    budget_seconds,
                    elapsed,
                    resumed_at,
                    owner,
                    new_revision,
                    now_ms,
                    run_id,
                ),
            )
        connection.execute(
            "INSERT INTO upgrade_control_requests(run_id, request_id, action, request_digest, "
            "grant_targets, grant_seconds, outcome_state, outcome_pause_reason, "
            "outcome_pause_detail, outcome_control_revision, changed, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                request_id,
                action,
                digest,
                grant_targets if changed else None,
                grant_seconds if changed else None,
                new_state,
                new_pause_reason,
                new_pause_detail,
                new_revision,
                1 if changed else 0,
                now_ms,
            ),
        )
        connection.execute("COMMIT")
        return {
            "contract_version": CONTRACT_VERSION,
            "request_id": request_id,
            "run_id": run_id,
            "state": new_state,
            "pause_reason": new_pause_reason,
            "pause_detail": new_pause_detail,
            "control_revision": new_revision,
            "changed": changed,
            "replayed": False,
        }
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def _profile(**overrides: Any) -> Any:
    base: dict[str, Any] = {
        "profile_id": "prof-x",
        "embedding_processor_id": "emb-v1",
        "space_id": "space-a",
        "image_generation_id": "gen-new",
        "query_preprocess_version": "1",
        "query_parser_version": "1",
        "fts_tokenizer_version": search_profile.SUPPORTED_FTS_TOKENIZER_VERSION,
        "fts_generation_id": search_profile.SUPPORTED_FTS_GENERATION_ID,
        "reranker_processor_id": None,
        "fusion_config": dict(search_profile.DEFAULT_FUSION_CONFIG),
        "runtime_package_id": "pkg-v1",
        "created_at": 0,
    }
    base.update(overrides)
    return search_profile.SearchProfile(**base)


def _catalog_shim(connection: sqlite3.Connection) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(connection=connection, read_one=None, read_all=None, now_ms=lambda: 0)


def reused_refusal_details_keys() -> set[str]:
    """Sol O-1: every ``details`` key the reused readiness/binding refusals really carry."""
    keys: set[str] = set()

    def capture(call: Any) -> None:
        try:
            call()
        except error_codes.CatalogError as error:  # type: ignore[attr-defined]
            keys.update(str(name) for name in error.details)
        else:  # pragma: no cover - each probe must refuse
            raise AssertionError("the probe was expected to refuse")

    connection = new_catalog()
    add_job(connection)
    add_asset(connection, 1)
    add_generation(connection, "gen-new")
    add_generation(connection, "gen-b", space_id="space-b", processor_id="emb-other")
    catalog = _catalog_shim(connection)

    capture(lambda: search_profile.resolve_profile_binding(
        catalog, _profile(image_generation_id="gen-missing"), connection=connection))
    capture(lambda: search_profile.resolve_profile_binding(
        catalog, _profile(space_id="space-b"), connection=connection))
    capture(lambda: search_profile.resolve_profile_binding(
        catalog, _profile(embedding_processor_id="emb-other"), connection=connection))
    add_generation(connection, "gen-pending", state="PENDING")
    capture(lambda: search_profile.resolve_profile_binding(
        catalog, _profile(image_generation_id="gen-pending"), connection=connection))
    capture(lambda: search_profile.validate_profile_readiness(
        connection, _profile(), now_ms=0))
    capture(lambda: search_profile.reranker_readiness(
        connection, _profile(reranker_processor_id="rerank-v1")))
    capture(lambda: search_profile.check_profile_servable(
        catalog, _profile(fts_generation_id="fts-other"), connection=connection))
    capture(lambda: search_profile.check_profile_servable(
        catalog, _profile(fusion_config={"revision": "x", "method": "MEAN",
                                         "parameters": {"k": 1, "weights": {}}}),
        connection=connection))
    return keys


def _running_fixture(**run_kwargs: Any) -> sqlite3.Connection:
    connection = new_catalog()
    add_job(connection)
    add_asset(connection, 1)
    add_generation(connection, "gen-new")
    add_profile(connection, "prof-new", generation_id="gen-new")
    add_run(
        connection,
        "run-1",
        profile_id="prof-new",
        generation_id="gen-new",
        state="RUNNING",
        **run_kwargs,
    )
    return connection


def _planned_fixture(**run_kwargs: Any) -> sqlite3.Connection:
    """A legal PLANNED run (no clock anchor), used for the F-B2-G source-state probe."""
    connection = _running_fixture(**run_kwargs)
    connection.execute(
        "UPDATE upgrade_runs SET state = 'PLANNED', resumed_at = NULL, clock_owner = NULL "
        "WHERE run_id = 'run-1'"
    )
    return connection


# ------------------------------------------------------------------- Sol F-A / O-2 cases
def test_activation_reevaluates_the_current_difference_set() -> None:
    """Sol F-A: the plan snapshot is not the bound of the coverage gate."""
    # (a) an ACTIVE asset added after plan: no target row, no entry, no outbox row.
    connection = _activation_fixture()
    add_asset(connection, 2)
    assert needs_projection(connection, space_id="space-a", generation_id="gen-new") == [2]
    before = catalog_snapshot(connection)
    with pytest.raises(Refused) as refused:
        reference_activate(connection, run_id="run-1")
    assert (refused.value.code, refused.value.reason) == (
        "INVALID_STATE_TRANSITION",
        "COVERAGE_INCOMPLETE",
    )
    assert catalog_snapshot(connection) == before
    # Positive control: once the new asset is covered, the same run activates.
    add_artifact(connection, "art-2")
    add_outbox(
        connection, "ev2", artifact_id="art-2", asset_id=2, generation_id="gen-new",
        projection_revision=7,
    )
    add_entry(
        connection, asset_id=2, generation_id="gen-new", artifact_id="art-2",
        confirmation_revision=7,
    )
    reference_activate(connection, run_id="run-1")
    assert connection.execute(
        "SELECT profile_id FROM active_search_profile WHERE state_id = 1"
    ).fetchone()[0] == "prof-new"

    # (b) an empty plan (no target rows at all) then a new ACTIVE asset.
    connection = _activation_fixture()
    connection.execute("DELETE FROM upgrade_targets WHERE run_id = 'run-1'")
    add_asset(connection, 2)
    before = catalog_snapshot(connection)
    with pytest.raises(Refused) as refused:
        reference_activate(connection, run_id="run-1")
    assert refused.value.reason == "COVERAGE_INCOMPLETE"
    assert catalog_snapshot(connection) == before

    # (c) an already covered asset that was never in the plan loses its coverage.
    connection = _activation_fixture()
    connection.execute("DELETE FROM index_entries WHERE asset_id = 1")
    before = catalog_snapshot(connection)
    with pytest.raises(Refused) as refused:
        reference_activate(connection, run_id="run-1")
    assert refused.value.reason == "COVERAGE_INCOMPLETE"
    assert catalog_snapshot(connection) == before


def test_activation_is_one_transaction_and_rolls_back_on_fault() -> None:
    """Sol O-2: gates and the pointer move share one transaction, so a mid-way fault rolls back."""
    connection = _activation_fixture()
    before = catalog_snapshot(connection)

    def explode(_connection: sqlite3.Connection) -> None:
        raise RuntimeError("mid-transaction fault")

    with pytest.raises(RuntimeError):
        reference_activate(connection, run_id="run-1", activation_hook=explode)
    assert catalog_snapshot(connection) == before


def test_artifact_readability_is_a_real_read() -> None:
    """Sol O-2: the gate opens the artifact instead of probing for a path."""
    connection = _activation_fixture()
    # A directory exists on disk but cannot be read as a file.
    connection.execute(
        "UPDATE artifacts SET storage_uri = ? WHERE artifact_id = 'art-1'",
        ("docs/decisions/contracts",),
    )
    before = catalog_snapshot(connection)
    with pytest.raises(Refused) as refused:
        reference_activate(connection, run_id="run-1")
    assert (refused.value.code, refused.value.reason) == ("ARTIFACT_MISSING", None)
    assert catalog_snapshot(connection) == before


# ------------------------------------------------------------------- Sol F-B cases
def test_control_request_schema_is_closed() -> None:
    request = {
        "contract_version": CONTRACT_VERSION,
        "request_id": "R1",
        "action": "PAUSE",
        "pause_reason": "USER",
    }
    assert contract_errors(request, "UpgradeControlRequest") == []
    assert contract_errors(dict(request, unknown_field=1), "UpgradeControlRequest")
    assert contract_errors(
        {"contract_version": CONTRACT_VERSION, "request_id": "R1"}, "UpgradeControlRequest"
    )
    assert contract_errors(
        {"contract_version": CONTRACT_VERSION, "request_id": "R1", "action": "MADE_UP"},
        "UpgradeControlRequest",
    )
    # pause needs a reason; ENVIRONMENT needs a detail; grants belong to resume only.
    assert contract_errors(
        {"contract_version": CONTRACT_VERSION, "request_id": "R1", "action": "PAUSE"},
        "UpgradeControlRequest",
    )
    assert contract_errors(
        {
            "contract_version": CONTRACT_VERSION,
            "request_id": "R1",
            "action": "PAUSE",
            "pause_reason": "ENVIRONMENT",
        },
        "UpgradeControlRequest",
    )
    assert contract_errors(
        {
            "contract_version": CONTRACT_VERSION,
            "request_id": "R1",
            "action": "CANCEL",
            "grant_targets": 5,
        },
        "UpgradeControlRequest",
    )
    assert contract_errors(
        {
            "contract_version": CONTRACT_VERSION,
            "request_id": "R1",
            "action": "RESUME",
            "grant_targets": 5,
            "grant_seconds": 60,
            "expected_control_revision": 3,
        },
        "UpgradeControlRequest",
    ) == []
    assert contract_errors(
        {
            "contract_version": CONTRACT_VERSION,
            "request_id": "R1",
            "action": "RESUME",
            "grant_targets": 5,
            "grant_seconds": 60,
            "expected_control_revision": 3,
        },
        "UpgradeControlRequestRow",
    )
    row = {
        "run_id": "run-1",
        "request_id": "R1",
        "action": "RESUME",
        "request_digest": "a" * 64,
        "grant_targets": 5,
        "grant_seconds": None,
        "outcome_state": "RUNNING",
        "outcome_pause_reason": None,
        "outcome_pause_detail": None,
        "outcome_control_revision": 4,
        "changed": True,
        "created_at": 10,
    }
    assert contract_errors(row, "UpgradeControlRequestRow") == []
    assert contract_errors(dict(row, extra=1), "UpgradeControlRequestRow")
    assert contract_errors(dict(row, outcome_state="PAUSED"), "UpgradeControlRequestRow")
    assert contract_errors(dict(row, unknown_field=1), "UpgradeControlRequestRow")
    assert contract_errors(
        dict(row, action="PAUSE", grant_targets=None), "UpgradeControlRequestRow"
    ) == []
    # A grant on a row whose run was not changed is refused by the contract and by the DDL.
    assert contract_errors(dict(row, changed=False), "UpgradeControlRequestRow")
    connection = _running_fixture()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO upgrade_control_requests(run_id, request_id, action, request_digest, "
            "grant_targets, grant_seconds, outcome_state, outcome_pause_reason, "
            "outcome_pause_detail, outcome_control_revision, changed, created_at) "
            "VALUES ('run-1', 'RX', 'PAUSE', ?, 5, NULL, 'PAUSED', 'USER', NULL, 2, 1, 10)",
            ("b" * 64,),
        )


def test_control_request_idempotency_is_durable_and_replay_first() -> None:
    """Sol F-B1/F-B2: pause(R1) → resume(R2) → replay R1 must not pause the run again."""
    connection = _running_fixture()
    first = apply_control_request(
        connection, run_id="run-1", request_id="R1", action="PAUSE", pause_reason="USER"
    )
    assert first["changed"] is True and first["replayed"] is False
    assert first["state"] == "PAUSED" and first["control_revision"] == 2
    second = apply_control_request(connection, run_id="run-1", request_id="R2", action="RESUME")
    assert second["state"] == "RUNNING" and second["control_revision"] == 3
    # The restart replays R1 with an older expected revision: replay wins over the revision check.
    before = catalog_snapshot(connection)
    log_before = control_log_rows(connection)
    replay = apply_control_request(
        connection,
        run_id="run-1",
        request_id="R1",
        action="PAUSE",
        pause_reason="USER",
        expected_control_revision=2,
    )
    # Sol F-B1: the response describes *this* call, so a replay is changed=false.
    assert replay["changed"] is False and replay["replayed"] is True
    assert replay["state"] == "PAUSED" and replay["control_revision"] == 2
    assert catalog_snapshot(connection) == before
    assert control_log_rows(connection) == log_before
    pointer = connection.execute("SELECT state FROM upgrade_runs WHERE run_id = 'run-1'").fetchone()[0]
    assert pointer == "RUNNING"  # the replayed request did not touch the run
    # The same key with a different intent is a structured conflict.
    for kwargs in ({"action": "RESUME"}, {"action": "PAUSE", "pause_reason": "BUDGET"}):
        with pytest.raises(Refused) as conflicted:
            apply_control_request(
                connection, run_id="run-1", request_id="R1", **kwargs  # type: ignore[arg-type]
            )
        assert conflicted.value.code == "VALIDATION_ERROR"
    # A first no-op (pause on an already paused run) is recorded too, with changed = 0 --
    # and it must leave the whole run row untouched (Sol F-B2).
    apply_control_request(
        connection, run_id="run-1", request_id="R3", action="PAUSE", pause_reason="USER"
    )
    run_before_noop = catalog_snapshot(connection)
    log_before_noop = control_log_rows(connection)
    noop = apply_control_request(
        connection, run_id="run-1", request_id="R4", action="PAUSE", pause_reason="USER"
    )
    assert noop["changed"] is False and noop["replayed"] is False
    assert noop["state"] == "PAUSED" and noop["control_revision"] == 4
    assert catalog_snapshot(connection) == run_before_noop
    assert control_log_rows(connection) == log_before_noop + 1
    rows = [
        tuple(row)
        for row in connection.execute(
            "SELECT request_id, changed, outcome_state FROM upgrade_control_requests "
            "WHERE run_id = 'run-1' ORDER BY request_id"
        )
    ]
    assert rows == [
        ("R1", 1, "PAUSED"),
        ("R2", 1, "RUNNING"),
        ("R3", 1, "PAUSED"),
        ("R4", 0, "PAUSED"),
    ]
    # expected_control_revision is a precondition only for a *fresh* request.
    before_stale = catalog_snapshot(connection)
    with pytest.raises(Refused) as stale:
        apply_control_request(
            connection, run_id="run-1", request_id="R5", action="CANCEL",
            expected_control_revision=99,
        )
    assert stale.value.reason == "CONTROL_REVISION_STALE"
    assert catalog_snapshot(connection) == before_stale

    # Sol F-B2: the state already holds but the parameters disagree -> refuse with zero writes,
    # including no log row (a different reason on an already paused run, and a grant on a run
    # that is already RUNNING).
    connection2 = _running_fixture()
    apply_control_request(
        connection2, run_id="run-1", request_id="P1", action="PAUSE", pause_reason="BUDGET"
    )
    before_mismatch = catalog_snapshot(connection2)
    log_before_mismatch = control_log_rows(connection2)
    with pytest.raises(Refused) as mismatch:
        apply_control_request(
            connection2, run_id="run-1", request_id="P2", action="PAUSE", pause_reason="USER"
        )
    assert mismatch.value.code == "INVALID_STATE_TRANSITION"
    assert mismatch.value.reason == "CONTROL_NOOP_ARGUMENT_MISMATCH"
    assert catalog_snapshot(connection2) == before_mismatch
    assert control_log_rows(connection2) == log_before_mismatch
    connection3 = _running_fixture(budget_targets=1)
    before_grant = catalog_snapshot(connection3)
    log_before_grant = control_log_rows(connection3)
    with pytest.raises(Refused) as granted_run:
        apply_control_request(
            connection3, run_id="run-1", request_id="G1", action="RESUME", grant_targets=2
        )
    assert granted_run.value.reason == "CONTROL_NOOP_ARGUMENT_MISMATCH"
    assert catalog_snapshot(connection3) == before_grant
    assert control_log_rows(connection3) == log_before_grant


# ------------------------------------------------------------------- Sol F-C cases
def test_budget_grant_and_wall_clock_ledger() -> None:
    assert apply_grant(cap=1, grant=2, maximum=MAX_BUDGET_TARGETS) == 3
    for kwargs in (
        {"cap": None, "grant": 1, "maximum": MAX_BUDGET_TARGETS},
        {"cap": MAX_BUDGET_TARGETS, "grant": 1, "maximum": MAX_BUDGET_TARGETS},
        {"cap": 5, "grant": 0, "maximum": MAX_BUDGET_TARGETS},
    ):
        with pytest.raises(Refused) as refused:
            apply_grant(**kwargs)  # type: ignore[arg-type]
        assert refused.value.code == "VALIDATION_ERROR"

    # An exhausted budget claims nothing on a bare resume; a grant lets the same run continue.
    connection = _running_fixture(budget_targets=1)
    add_target(connection, "run-1", 1, state="SUCCEEDED")
    assert claim_batch_size(budget_concurrency=4, budget_targets=1, completed_targets=1) == 0
    apply_control_request(
        connection, run_id="run-1", request_id="A1", action="PAUSE", pause_reason="BUDGET"
    )
    bare = apply_control_request(connection, run_id="run-1", request_id="A2", action="RESUME")
    assert bare["state"] == "RUNNING"
    assert claim_batch_size(budget_concurrency=4, budget_targets=1, completed_targets=1) == 0
    apply_control_request(
        connection, run_id="run-1", request_id="A3", action="PAUSE", pause_reason="BUDGET"
    )
    granted = apply_control_request(
        connection, run_id="run-1", request_id="A4", action="RESUME", grant_targets=3
    )
    assert granted["state"] == "RUNNING"
    caps = connection.execute(
        "SELECT budget_targets, budget_seconds FROM upgrade_runs WHERE run_id = 'run-1'"
    ).fetchone()
    assert int(caps["budget_targets"]) == 4
    assert claim_batch_size(budget_concurrency=4, budget_targets=4, completed_targets=1) == 3
    # A grant on an unlimited cap is refused, and it never resets what is already recorded.
    with pytest.raises(Refused) as no_cap:
        apply_grant(cap=None, grant=1, maximum=MAX_BUDGET_TARGETS)
    assert no_cap.value.reason == "GRANT_WITHOUT_CAP"

    # The wall-clock ledger: settled segment + live segment, re-anchored on recovery.
    assert effective_elapsed_ms(
        state="PAUSED", elapsed_ms=5000, resumed_at=None, clock_owner=None, session="s", now_ms=99999
    ) == 5000
    assert effective_elapsed_ms(
        state="RUNNING", elapsed_ms=5000, resumed_at=0, clock_owner="s", session="s", now_ms=1500
    ) == 6500
    assert effective_elapsed_ms(
        state="RUNNING", elapsed_ms=5000, resumed_at=0, clock_owner="other", session="s", now_ms=1500
    ) == 5000
    # Sol F-C's trap: reading only the settled value never reaches a seconds cap.
    assert not budget_stop(budget_targets=None, budget_seconds=1, completed_targets=0, elapsed_ms=0)
    assert budget_stop(budget_targets=None, budget_seconds=1, completed_targets=0, elapsed_ms=1000)
    assert reanchor(clock_owner="previous-session", session="s", now_ms=9999) == (9999, "s")
    assert settle_on_leave(
        elapsed_ms=100, resumed_at=1000, clock_owner="s", session="s", now_ms=1050
    ) == 150
    with pytest.raises(Refused) as not_owner:
        settle_on_leave(elapsed_ms=100, resumed_at=1000, clock_owner="other", session="s", now_ms=1050)
    assert not_owner.value.reason == "CLOCK_NOT_OWNED"

    # Boundary order: completion precedes the budget soft stop.
    assert batch_boundary_decision(target_states=("SUCCEEDED",), budget_reached=True) == "SUCCEEDED"
    assert batch_boundary_decision(
        target_states=("SUCCEEDED", "PLANNED"), budget_reached=True
    ) == "PAUSED"
    assert batch_boundary_decision(
        target_states=("SUCCEEDED", "FAILED"), budget_reached=True
    ) == "PAUSED"
    assert batch_boundary_decision(
        target_states=("SUCCEEDED",), budget_reached=False
    ) == "SUCCEEDED"


# ------------------------------------------------------------------- Sol F-E cases
def test_expired_lease_takeover_counts_exactly_once() -> None:
    assert attempt_delta(transition="IN_FLIGHT->FAILED", reason="LEASE_EXPIRED") == 1
    assert attempt_delta(transition="IN_FLIGHT->FAILED", reason="SOURCE_OFFLINE") == 1
    assert attempt_delta(transition="IN_FLIGHT->FAILED", reason="MODEL_WEIGHTS_MISSING") == 0
    assert attempt_delta(transition="IN_FLIGHT->FAILED", reason="VRAM_INSUFFICIENT") == 0
    assert attempt_delta(transition="IN_FLIGHT->PLANNED", reason=None) == 0
    assert attempt_delta(transition="FAILED->PLANNED", reason=None) == 0

    connection = _running_fixture()
    add_target(connection, "run-1", 1, state="IN_FLIGHT")
    # Leaving IN_FLIGHT without clearing the lease is refused by the DDL.
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE upgrade_targets SET state = 'SUCCEEDED' WHERE asset_id = 1")
    # The one takeover path, with exactly one increment.
    connection.execute(
        "UPDATE upgrade_targets SET state = 'FAILED', reason = 'LEASE_EXPIRED', attempts = attempts + 1, "
        "lease_token = NULL, lease_expires_at = NULL WHERE asset_id = 1"
    )
    row = connection.execute(
        "SELECT state, reason, attempts FROM upgrade_targets WHERE asset_id = 1"
    ).fetchone()
    assert tuple(row) == ("FAILED", "LEASE_EXPIRED", 1)
    # The second takeover call is executed for real: the row is no longer eligible, so it
    # cannot count a second time (Sol F-E).
    connection.execute(
        "UPDATE upgrade_targets SET state = 'IN_FLIGHT', reason = NULL, "
        "lease_token = 'lease-2', lease_expires_at = 500, attempts = 0 WHERE asset_id = 1"
    )
    with pytest.raises(Refused) as still_valid:
        takeover_expired_lease(connection, run_id="run-1", asset_id=1, now_ms=100)
    assert still_valid.value.reason == "LEASE_STILL_VALID"
    assert takeover_expired_lease(
        connection, run_id="run-1", asset_id=1, now_ms=600
    ) == "FAILED"
    assert int(
        connection.execute(
            "SELECT attempts FROM upgrade_targets WHERE asset_id = 1"
        ).fetchone()[0]
    ) == 1
    with pytest.raises(Refused) as ineligible:
        takeover_expired_lease(connection, run_id="run-1", asset_id=1, now_ms=700)
    assert ineligible.value.reason == "TAKEOVER_NOT_ELIGIBLE"
    assert int(
        connection.execute(
            "SELECT attempts FROM upgrade_targets WHERE asset_id = 1"
        ).fetchone()[0]
    ) == 1
    connection.execute(
        "UPDATE upgrade_targets SET state = 'FAILED', reason = 'LEASE_EXPIRED', "
        "lease_token = NULL, lease_expires_at = NULL WHERE asset_id = 1"
    )
    # The explicit retry afterwards does not count again.
    connection.execute(
        "UPDATE upgrade_targets SET state = 'PLANNED', reason = NULL WHERE asset_id = 1"
    )
    assert int(
        connection.execute(
            "SELECT attempts FROM upgrade_targets WHERE asset_id = 1"
        ).fetchone()[0]
    ) == 1


# ------------------------------------------------------------------- Sol O-1 case
def test_reused_refusal_details_keys_are_inside_the_frozen_error_details() -> None:
    frozen = set(DEFS["UpgradeApiErrorDetails"]["properties"])
    keys = reused_refusal_details_keys()
    assert keys, "the probes must exercise the real readiness/binding refusals"
    assert keys <= frozen, sorted(keys - frozen)
    assert {"reason", "runtime_package_id", "profile_id", "generation_state"} <= keys


def test_control_response_changed_semantics() -> None:
    """Sol F-B1: first / no-op / replay must each produce one valid, distinct envelope."""
    connection = _running_fixture()
    first = apply_control_request(
        connection, run_id="run-1", request_id="C1", action="PAUSE", pause_reason="USER"
    )
    assert contract_errors(first, "UpgradeControlResponse") == []
    assert (first["changed"], first["replayed"]) == (True, False)

    noop = apply_control_request(
        connection, run_id="run-1", request_id="C2", action="PAUSE", pause_reason="USER"
    )
    assert contract_errors(noop, "UpgradeControlResponse") == []
    assert (noop["changed"], noop["replayed"]) == (False, False)

    replay = apply_control_request(
        connection, run_id="run-1", request_id="C1", action="PAUSE", pause_reason="USER"
    )
    assert contract_errors(replay, "UpgradeControlResponse") == []
    assert (replay["changed"], replay["replayed"]) == (False, True)
    assert replay["control_revision"] == first["control_revision"]

    # The pairing the companion forbids: a call cannot both have written the run and be a replay.
    assert contract_errors(dict(replay, changed=True), "UpgradeControlResponse")
    assert contract_errors(dict(first, replayed=True), "UpgradeControlResponse")
    # The durable log keeps the *original* outcome, which is a different fact from the response.
    recorded = connection.execute(
        "SELECT changed, outcome_state, outcome_control_revision FROM upgrade_control_requests "
        "WHERE request_id = 'C1'"
    ).fetchone()
    assert tuple(recorded) == (1, "PAUSED", 2)


def test_budget_grant_requires_a_paused_source_state() -> None:
    """Sol F-B2-G: grant_* is only legal on PAUSED -> RUNNING, never on PLANNED -> RUNNING."""
    for cap_field, grant_field in (("budget_targets", "grant_targets"), ("budget_seconds", "grant_seconds")):
        connection = _planned_fixture(**{cap_field: 1})
        before = catalog_snapshot(connection)
        log_before = control_log_rows(connection)
        with pytest.raises(Refused) as refused:
            apply_control_request(
                connection,
                run_id="run-1",
                request_id="PLAN_GRANT",
                action="RESUME",
                **{grant_field: 2},  # type: ignore[arg-type]
            )
        assert refused.value.code == "INVALID_STATE_TRANSITION"
        assert refused.value.reason == "GRANT_SOURCE_STATE_NOT_PAUSED"
        assert catalog_snapshot(connection) == before
        assert control_log_rows(connection) == log_before
        # The plain start transition is untouched by this rule (no grant, no refusal).
        started = apply_control_request(
            connection, run_id="run-1", request_id="PLAN_START", action="RESUME"
        )
        assert started["changed"] is True and started["state"] == "RUNNING"

    # A successful PAUSED + grant may still be replayed by its own key once the run is RUNNING.
    connection = _running_fixture(budget_targets=1)
    add_target(connection, "run-1", 1, state="SUCCEEDED")
    apply_control_request(
        connection, run_id="run-1", request_id="G1", action="PAUSE", pause_reason="BUDGET"
    )
    granted = apply_control_request(
        connection, run_id="run-1", request_id="G2", action="RESUME", grant_targets=2
    )
    assert granted["changed"] is True and granted["state"] == "RUNNING"
    cap = int(
        connection.execute(
            "SELECT budget_targets FROM upgrade_runs WHERE run_id = 'run-1'"
        ).fetchone()[0]
    )
    assert cap == 3
    before_replay = catalog_snapshot(connection)
    log_before_replay = control_log_rows(connection)
    replay = apply_control_request(
        connection, run_id="run-1", request_id="G2", action="RESUME", grant_targets=2
    )
    assert replay["changed"] is False and replay["replayed"] is True
    assert catalog_snapshot(connection) == before_replay
    assert control_log_rows(connection) == log_before_replay
    assert int(
        connection.execute(
            "SELECT budget_targets FROM upgrade_runs WHERE run_id = 'run-1'"
        ).fetchone()[0]
    ) == 3  # the replay does not grant a second time
