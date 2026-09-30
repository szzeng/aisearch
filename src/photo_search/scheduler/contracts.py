"""The frozen ``p2-contracts.v1`` envelopes, as executable dataclasses.

ADR-0003 (frozen by P2-01) and its companion JSON Schema are authoritative. This module
does not redefine them: it validates the exact closed shapes the ADR freezes and converts
them to and from plain JSON, so a scheduler and an executor can exchange ordinary JSON and
still reject unknown or missing fields at both ends.

Every constructor raises ``CatalogError`` with the stable ``INVALID_JOB_RESULT`` code, so
a protocol violation is reported as a structured failure instead of a Python traceback.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from photo_search.catalog.errors import INVALID_JOB_RESULT, CatalogError

CONTRACT_VERSION = "p2-contracts.v1"
DEVICE_POLICIES = ("GPU_REQUIRED", "CPU_ALLOWED", "CPU_PREFERRED")
REQUESTED_DEVICES = ("auto", "cpu", "gpu")
RESOLVED_DEVICES = ("cpu", "gpu")
PROCESSOR_KINDS = ("embedding_model", "reranker_model", "non_model", "structured_model")
JOB_RESULT_STATES = ("SUCCEEDED", "RETRY_WAIT", "BLOCKED", "FAILED", "CANCELLED")
HEALTH_STATUSES = ("healthy", "degraded", "unhealthy", "unknown")
ARTIFACT_TYPES = ("embedding", "structured", "binary")

#: ADR-0003 §1: the stable ``gpu`` spelling is a one-to-one mapping of the runtime's
#: ``cuda`` device. They are the same logical device, never two spaces.
RUNTIME_DEVICE_BY_RESOLVED = {"cpu": "cpu", "gpu": "cuda"}

#: Upper bound for one result envelope. A worker that returns a larger payload is refused
#: before anything is committed, so a runaway payload can never become a catalog write.
MAX_JOB_RESULT_CHARS = 262_144


def _fail(message: str, **details: Any) -> CatalogError:
    return CatalogError(
        INVALID_JOB_RESULT, message, details={key: value for key, value in details.items()}
    )


def _object(value: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(f"{where} must be a JSON object", where=where)
    return dict(value)


def _closed(
    payload: Mapping[str, Any],
    *,
    where: str,
    required: Sequence[str],
    optional: Sequence[str] = (),
) -> None:
    missing = [name for name in required if name not in payload]
    unexpected = [name for name in payload if name not in required and name not in optional]
    if missing or unexpected:
        raise _fail(
            f"{where} does not match the frozen contract shape",
            where=where,
            missing=missing,
            unexpected=unexpected,
        )


def _text(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail(f"{where} must be a non-empty string", where=where)
    return value


def _optional_text(value: Any, *, where: str) -> str | None:
    if value is None:
        return None
    return _text(value, where=where)


def _integer(value: Any, *, where: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(f"{where} must be an integer", where=where)
    if minimum is not None and int(value) < minimum:
        raise _fail(f"{where} must be at least {minimum}", where=where)
    return int(value)


def _optional_integer(value: Any, *, where: str, minimum: int | None = None) -> int | None:
    if value is None:
        return None
    return _integer(value, where=where, minimum=minimum)


def _number(value: Any, *, where: str, low: float = 0.0, high: float = 1.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(f"{where} must be a number", where=where)
    number = float(value)
    if number < low or number > high:
        raise _fail(f"{where} must be within [{low}, {high}]", where=where)
    return number


def _boolean(value: Any, *, where: str) -> bool:
    if not isinstance(value, bool):
        raise _fail(f"{where} must be a boolean", where=where)
    return value


def _enum(value: Any, allowed: Sequence[str], *, where: str) -> str:
    if value not in allowed:
        raise _fail(f"{where} is outside {list(allowed)}", where=where, value=str(value))
    return str(value)


def _sha256(value: Any, *, where: str) -> str:
    text = _text(value, where=where)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise _fail(f"{where} must be a lowercase 64-character sha256", where=where)
    return text


def _string_list(value: Any, *, where: str, min_items: int = 0) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise _fail(f"{where} must be a JSON array", where=where)
    items = tuple(_text(item, where=f"{where}[{index}]") for index, item in enumerate(value))
    if len(items) < int(min_items):
        raise _fail(f"{where} needs at least {min_items} item(s)", where=where)
    if len(set(items)) != len(items):
        raise _fail(f"{where} must not repeat a value", where=where)
    return items


def canonical_json(payload: Mapping[str, Any]) -> str:
    """One deterministic rendering of a JSON envelope, used for checksums and storage."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_of(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- JobRequest
@dataclass(frozen=True)
class InputSnapshot:
    subject_revision: str
    asset_sha256: str | None
    location_id: int | None
    byte_size: int | None
    mtime_ns: int | None

    def to_envelope(self) -> dict[str, Any]:
        return {
            "subject_revision": self.subject_revision,
            "asset_sha256": self.asset_sha256,
            "location_id": self.location_id,
            "byte_size": self.byte_size,
            "mtime_ns": self.mtime_ns,
        }

    @classmethod
    def from_envelope(cls, payload: Any) -> InputSnapshot:
        where = "input_snapshot"
        data = _object(payload, where=where)
        _closed(
            data,
            where=where,
            required=("subject_revision", "asset_sha256", "location_id", "byte_size", "mtime_ns"),
        )
        return cls(
            subject_revision=_text(data["subject_revision"], where=f"{where}.subject_revision"),
            asset_sha256=(
                None
                if data["asset_sha256"] is None
                else _sha256(data["asset_sha256"], where=f"{where}.asset_sha256")
            ),
            location_id=_optional_integer(
                data["location_id"], where=f"{where}.location_id", minimum=1
            ),
            byte_size=_optional_integer(
                data["byte_size"], where=f"{where}.byte_size", minimum=0
            ),
            mtime_ns=_optional_integer(
                data["mtime_ns"], where=f"{where}.mtime_ns", minimum=0
            ),
        )


@dataclass(frozen=True)
class JobRequest:
    """The dispatch envelope handed to an executor: identity plus the resolved device."""

    job_id: int
    subject_type: str
    subject_id: str
    input_revision: str
    task_type: str
    processor_id: str
    lease_token: str
    requested_device: str
    resolved_device: str
    runtime_provider: str
    precision: str
    runtime_capability_revision: str
    input_snapshot: InputSnapshot

    def to_envelope(self) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "job_id": self.job_id,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "input_revision": self.input_revision,
            "task_type": self.task_type,
            "processor_id": self.processor_id,
            "lease_token": self.lease_token,
            "requested_device": self.requested_device,
            "resolved_device": self.resolved_device,
            "runtime_provider": self.runtime_provider,
            "precision": self.precision,
            "runtime_capability_revision": self.runtime_capability_revision,
            "input_snapshot": self.input_snapshot.to_envelope(),
        }

    @classmethod
    def from_envelope(cls, payload: Any) -> JobRequest:
        where = "JobRequest"
        data = _object(payload, where=where)
        _closed(
            data,
            where=where,
            required=(
                "contract_version",
                "job_id",
                "subject_type",
                "subject_id",
                "input_revision",
                "task_type",
                "processor_id",
                "lease_token",
                "requested_device",
                "resolved_device",
                "runtime_provider",
                "precision",
                "runtime_capability_revision",
                "input_snapshot",
            ),
        )
        if data["contract_version"] != CONTRACT_VERSION:
            raise _fail("JobRequest.contract_version is not this build's contract", where=where)
        return cls(
            job_id=_integer(data["job_id"], where="job_id", minimum=1),
            subject_type=_text(data["subject_type"], where="subject_type"),
            subject_id=_text(data["subject_id"], where="subject_id"),
            input_revision=_text(data["input_revision"], where="input_revision"),
            task_type=_text(data["task_type"], where="task_type"),
            processor_id=_text(data["processor_id"], where="processor_id"),
            lease_token=_text(data["lease_token"], where="lease_token"),
            requested_device=_enum(
                data["requested_device"], REQUESTED_DEVICES, where="requested_device"
            ),
            resolved_device=_enum(
                data["resolved_device"], RESOLVED_DEVICES, where="resolved_device"
            ),
            runtime_provider=_text(data["runtime_provider"], where="runtime_provider"),
            precision=_text(data["precision"], where="precision"),
            runtime_capability_revision=_text(
                data["runtime_capability_revision"], where="runtime_capability_revision"
            ),
            input_snapshot=InputSnapshot.from_envelope(data["input_snapshot"]),
        )


# ---------------------------------------------------------------------------- JobResult
@dataclass(frozen=True)
class ErrorEnvelope:
    code: str
    message: str
    retryable: bool
    blocking_reason: str | None

    def to_envelope(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "blocking_reason": self.blocking_reason,
        }

    @classmethod
    def from_envelope(cls, payload: Any) -> ErrorEnvelope:
        where = "error"
        data = _object(payload, where=where)
        _closed(data, where=where, required=("code", "message", "retryable", "blocking_reason"))
        return cls(
            code=_text(data["code"], where="error.code"),
            message=_text(data["message"], where="error.message"),
            retryable=_boolean(data["retryable"], where="error.retryable"),
            blocking_reason=_optional_text(data["blocking_reason"], where="error.blocking_reason"),
        )


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    artifact_type: str
    artifact_revision: str
    media_type: str
    checksum_sha256: str
    byte_size: int
    storage_uri: str | None
    payload: dict[str, Any] | None
    space_id: str | None
    dimension: int | None

    def to_envelope(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "artifact_revision": self.artifact_revision,
            "media_type": self.media_type,
            "checksum_sha256": self.checksum_sha256,
            "byte_size": self.byte_size,
            "storage_uri": self.storage_uri,
            "payload": self.payload,
            "space_id": self.space_id,
            "dimension": self.dimension,
        }

    @classmethod
    def from_envelope(cls, payload: Any) -> ArtifactRef:
        where = "artifacts[]"
        data = _object(payload, where=where)
        _closed(
            data,
            where=where,
            required=(
                "artifact_id",
                "artifact_type",
                "artifact_revision",
                "media_type",
                "checksum_sha256",
                "byte_size",
                "storage_uri",
                "payload",
                "space_id",
                "dimension",
            ),
        )
        artifact_type = _enum(data["artifact_type"], ARTIFACT_TYPES, where="artifact_type")
        space_id = _optional_text(data["space_id"], where="artifact.space_id")
        dimension = _optional_integer(data["dimension"], where="artifact.dimension", minimum=1)
        if artifact_type == "embedding":
            if space_id is None or dimension is None:
                raise _fail("an embedding artifact needs space_id and dimension", where=where)
        elif space_id is not None or dimension is not None:
            raise _fail(
                "a structured/binary artifact must not carry space_id or dimension",
                where=where,
            )
        if data["payload"] is not None:
            _object(data["payload"], where="artifact.payload")
        return cls(
            artifact_id=_text(data["artifact_id"], where="artifact.artifact_id"),
            artifact_type=artifact_type,
            artifact_revision=_text(data["artifact_revision"], where="artifact.artifact_revision"),
            media_type=_text(data["media_type"], where="artifact.media_type"),
            checksum_sha256=_sha256(data["checksum_sha256"], where="artifact.checksum_sha256"),
            byte_size=_integer(data["byte_size"], where="artifact.byte_size", minimum=0),
            storage_uri=_optional_text(data["storage_uri"], where="artifact.storage_uri"),
            payload=None if data["payload"] is None else dict(data["payload"]),
            space_id=space_id,
            dimension=dimension,
        )


@dataclass(frozen=True)
class JobResult:
    job_id: int
    lease_token: str
    input_revision: str
    task_type: str
    processor_id: str
    state: str
    resolved_device: str
    runtime_provider: str
    precision: str
    progress: float
    artifacts: tuple[ArtifactRef, ...]
    error: ErrorEnvelope | None
    completed_at: int

    def to_envelope(self) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "job_id": self.job_id,
            "lease_token": self.lease_token,
            "input_revision": self.input_revision,
            "task_type": self.task_type,
            "processor_id": self.processor_id,
            "state": self.state,
            "resolved_device": self.resolved_device,
            "runtime_provider": self.runtime_provider,
            "precision": self.precision,
            "progress": self.progress,
            "artifacts": [artifact.to_envelope() for artifact in self.artifacts],
            "error": None if self.error is None else self.error.to_envelope(),
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_envelope(cls, payload: Any) -> JobResult:
        where = "JobResult"
        data = _object(payload, where=where)
        _closed(
            data,
            where=where,
            required=(
                "contract_version",
                "job_id",
                "lease_token",
                "input_revision",
                "task_type",
                "processor_id",
                "state",
                "resolved_device",
                "runtime_provider",
                "precision",
                "progress",
                "artifacts",
                "error",
                "completed_at",
            ),
        )
        if data["contract_version"] != CONTRACT_VERSION:
            raise _fail("JobResult.contract_version is not this build's contract", where=where)
        if len(canonical_json(data)) > MAX_JOB_RESULT_CHARS:
            raise _fail(
                "JobResult payload is larger than the bounded protocol allows", where=where
            )
        state = _enum(data["state"], JOB_RESULT_STATES, where="state")
        progress = _number(data["progress"], where="progress")
        error = None if data["error"] is None else ErrorEnvelope.from_envelope(data["error"])
        if state == "SUCCEEDED":
            if progress != 1.0:
                raise _fail("a SUCCEEDED result must report progress 1", where=where)
            if error is not None:
                raise _fail("a SUCCEEDED result must not carry an error", where=where)
        elif error is None:
            raise _fail(f"a {state} result must carry an error envelope", where=where)
        artifacts_raw = data["artifacts"]
        if not isinstance(artifacts_raw, Sequence) or isinstance(artifacts_raw, (str, bytes)):
            raise _fail("artifacts must be a JSON array", where=where)
        return cls(
            job_id=_integer(data["job_id"], where="job_id", minimum=1),
            lease_token=_text(data["lease_token"], where="lease_token"),
            input_revision=_text(data["input_revision"], where="input_revision"),
            task_type=_text(data["task_type"], where="task_type"),
            processor_id=_text(data["processor_id"], where="processor_id"),
            state=state,
            resolved_device=_enum(
                data["resolved_device"], RESOLVED_DEVICES, where="resolved_device"
            ),
            runtime_provider=_text(data["runtime_provider"], where="runtime_provider"),
            precision=_text(data["precision"], where="precision"),
            progress=progress,
            artifacts=tuple(ArtifactRef.from_envelope(item) for item in artifacts_raw),
            error=error,
            completed_at=_integer(data["completed_at"], where="completed_at", minimum=0),
        )


# ------------------------------------------------------------------------ ProcessorSpec
_SPEC_COMMON_REQUIRED = (
    "contract_version",
    "spec_revision",
    "processor_id",
    "processor_kind",
    "task_type",
    "config_hash",
    "input_type",
    "output_type",
    "device_policy",
    "runtime_backend",
    "runtime_package_id",
    "supported_devices",
    "supported_precisions",
    "max_batch_size",
    "cancellation_supported",
    "resource_measurements",
)
_EMBEDDING_EXTRA = (
    "model_id",
    "model_revision",
    "weights",
    "image_preprocess_version",
    "query_preprocess_version",
    "tokenizer_version",
    "instruction_template_version",
    "normalization_version",
    "dimension",
    "metric",
    "space_id",
    "max_pixels",
    "max_tokens",
)
_RERANKER_EXTRA = (
    "model_id",
    "model_revision",
    "weights",
    "query_preprocess_version",
    "candidate_preprocess_version",
    "tokenizer_version",
    "instruction_template_version",
    "max_tokens",
)


@dataclass(frozen=True)
class ProcessorSpec:
    """The scheduling-relevant view of a processor: kind, device policy and the extras."""

    spec_revision: str
    processor_id: str
    processor_kind: str
    task_type: str
    config_hash: str
    device_policy: str
    runtime_backend: str
    runtime_package_id: str
    supported_devices: tuple[str, ...]
    supported_precisions: tuple[str, ...]
    max_batch_size: int
    cancellation_supported: bool
    envelope: dict[str, Any]

    @property
    def is_non_model(self) -> bool:
        return self.processor_kind == "non_model"

    def to_envelope(self) -> dict[str, Any]:
        return dict(self.envelope)

    @classmethod
    def from_envelope(cls, payload: Any) -> ProcessorSpec:
        where = "ProcessorSpec"
        data = _object(payload, where=where)
        if data.get("contract_version") != CONTRACT_VERSION:
            raise _fail("ProcessorSpec.contract_version is not this build's contract", where=where)
        kind = _enum(data.get("processor_kind"), PROCESSOR_KINDS, where="processor_kind")
        if kind == "embedding_model":
            extra = _EMBEDDING_EXTRA
        elif kind == "reranker_model":
            extra = _RERANKER_EXTRA
        elif kind == "structured_model":
            extra = ("model_id", "model_revision", "weights")
        else:
            extra = ()
        _closed(data, where=where, required=(*_SPEC_COMMON_REQUIRED, *extra))
        devices = _string_list(data["supported_devices"], where="supported_devices", min_items=1)
        for device in devices:
            if device not in RESOLVED_DEVICES:
                raise _fail("supported_devices holds an unknown device", where="supported_devices")
        policy = _enum(data["device_policy"], DEVICE_POLICIES, where="device_policy")
        if kind == "non_model":
            if policy != "CPU_PREFERRED" or devices != ("cpu",):
                raise _fail(
                    "a non_model processor is CPU_PREFERRED on cpu only",
                    where=where,
                    device_policy=policy,
                    supported_devices=list(devices),
                )
        if kind == "structured_model":
            if _text(data["output_type"], where="output_type") == "embedding":
                raise _fail("a structured_model cannot declare embedding output", where=where)
            _text(data["model_id"], where="model_id")
            _text(data["model_revision"], where="model_revision")
            weights = data["weights"]
            if not isinstance(weights, Sequence) or isinstance(weights, (str, bytes)) or not weights:
                raise _fail("structured_model.weights must be a non-empty array", where=where)
            for index, weight in enumerate(weights):
                item = _object(weight, where=f"weights[{index}]")
                _closed(item, where=f"weights[{index}]", required=("name", "revision", "sha256"))
                _text(item["name"], where=f"weights[{index}].name")
                _text(item["revision"], where=f"weights[{index}].revision")
                _sha256(item["sha256"], where=f"weights[{index}].sha256")
        _sha256(data["config_hash"], where="config_hash")
        measurements = data["resource_measurements"]
        if not isinstance(measurements, Sequence) or isinstance(measurements, (str, bytes)):
            raise _fail("resource_measurements must be a JSON array", where=where)
        return cls(
            spec_revision=_text(data["spec_revision"], where="spec_revision"),
            processor_id=_text(data["processor_id"], where="processor_id"),
            processor_kind=kind,
            task_type=_text(data["task_type"], where="task_type"),
            config_hash=str(data["config_hash"]),
            device_policy=policy,
            runtime_backend=_text(data["runtime_backend"], where="runtime_backend"),
            runtime_package_id=_text(data["runtime_package_id"], where="runtime_package_id"),
            supported_devices=devices,
            supported_precisions=_string_list(
                data["supported_precisions"], where="supported_precisions", min_items=1
            ),
            max_batch_size=_integer(data["max_batch_size"], where="max_batch_size", minimum=1),
            cancellation_supported=_boolean(
                data["cancellation_supported"], where="cancellation_supported"
            ),
            envelope=dict(data),
        )


# ------------------------------------------------------------------- RuntimeCapabilities
@dataclass(frozen=True)
class RuntimeProvider:
    name: str
    device: str
    precisions: tuple[str, ...]
    available: bool
    health: str
    health_checked_at: int
    evidence_id: str | None

    def to_envelope(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "device": self.device,
            "precisions": list(self.precisions),
            "available": self.available,
            "health": self.health,
            "health_checked_at": self.health_checked_at,
            "evidence_id": self.evidence_id,
        }

    @classmethod
    def from_envelope(cls, payload: Any) -> RuntimeProvider:
        where = "providers[]"
        data = _object(payload, where=where)
        _closed(
            data,
            where=where,
            required=(
                "name",
                "device",
                "precisions",
                "available",
                "health",
                "health_checked_at",
                "evidence_id",
            ),
        )
        return cls(
            name=_text(data["name"], where="provider.name"),
            device=_enum(data["device"], RESOLVED_DEVICES, where="provider.device"),
            precisions=_string_list(
                data["precisions"], where="provider.precisions", min_items=1
            ),
            available=_boolean(data["available"], where="provider.available"),
            health=_enum(data["health"], HEALTH_STATUSES, where="provider.health"),
            health_checked_at=_integer(
                data["health_checked_at"], where="provider.health_checked_at", minimum=0
            ),
            evidence_id=_optional_text(data["evidence_id"], where="provider.evidence_id"),
        )


@dataclass(frozen=True)
class RuntimeCapabilities:
    """One immutable, revisioned snapshot of the runtime's health facts."""

    capability_revision: str
    runtime_package_id: str
    runtime_backend: str
    runtime_package_name: str
    runtime_package_version: str
    python_version: str
    platform: str
    driver_version: str | None
    providers: tuple[RuntimeProvider, ...]
    health_status: str
    health_checked_at: int
    measured_at: int
    evidence_ids: tuple[str, ...]
    failure_code: str | None
    envelope: dict[str, Any]

    @property
    def normalized_json(self) -> str:
        return canonical_json(self.envelope)

    @property
    def checksum_sha256(self) -> str:
        return digest_of(self.envelope)

    @property
    def expected_revision(self) -> str:
        """The only revision a snapshot with these facts may carry."""
        data = dict(self.envelope)
        data["capability_revision"] = ""
        return f"cap-{digest_of(data)[:16]}"

    def providers_for(self, device: str) -> tuple[RuntimeProvider, ...]:
        return tuple(sorted(
            (provider for provider in self.providers if provider.device == device),
            key=lambda provider: provider.name,
        ))

    def to_envelope(self) -> dict[str, Any]:
        return dict(self.envelope)

    @classmethod
    def seal(cls, facts: Mapping[str, Any]) -> RuntimeCapabilities:
        """Give a fact snapshot its deterministic revision and return the frozen object."""
        data = _object(facts, where="RuntimeCapabilities")
        data.setdefault("contract_version", CONTRACT_VERSION)
        data["capability_revision"] = ""
        revision = f"cap-{digest_of(data)[:16]}"
        data["capability_revision"] = revision
        return cls.from_envelope(data)

    @classmethod
    def from_envelope(cls, payload: Any) -> RuntimeCapabilities:
        where = "RuntimeCapabilities"
        data = _object(payload, where=where)
        _closed(
            data,
            where=where,
            required=(
                "contract_version",
                "capability_revision",
                "runtime_package_id",
                "runtime_backend",
                "runtime_package_name",
                "runtime_package_version",
                "python_version",
                "platform",
                "driver_version",
                "providers",
                "health_status",
                "health_checked_at",
                "measured_at",
                "evidence_ids",
                "failure_code",
            ),
        )
        if data["contract_version"] != CONTRACT_VERSION:
            raise _fail(
                "RuntimeCapabilities.contract_version is not this build's contract", where=where
            )
        providers_raw = data["providers"]
        if not isinstance(providers_raw, Sequence) or isinstance(providers_raw, (str, bytes)):
            raise _fail("providers must be a JSON array", where=where)
        providers = tuple(RuntimeProvider.from_envelope(item) for item in providers_raw)
        if not providers:
            raise _fail("providers needs at least one entry", where=where)
        names = [provider.name for provider in providers]
        if len(set(names)) != len(names):
            raise _fail("providers must not repeat a provider name", where=where)
        return cls(
            capability_revision=_text(data["capability_revision"], where="capability_revision"),
            runtime_package_id=_text(data["runtime_package_id"], where="runtime_package_id"),
            runtime_backend=_text(data["runtime_backend"], where="runtime_backend"),
            runtime_package_name=_text(data["runtime_package_name"], where="runtime_package_name"),
            runtime_package_version=_text(
                data["runtime_package_version"], where="runtime_package_version"
            ),
            python_version=_text(data["python_version"], where="python_version"),
            platform=_text(data["platform"], where="platform"),
            driver_version=_optional_text(data["driver_version"], where="driver_version"),
            providers=providers,
            health_status=_enum(data["health_status"], HEALTH_STATUSES, where="health_status"),
            health_checked_at=_integer(
                data["health_checked_at"], where="health_checked_at", minimum=0
            ),
            measured_at=_integer(data["measured_at"], where="measured_at", minimum=0),
            evidence_ids=_string_list(data["evidence_ids"], where="evidence_ids"),
            failure_code=_optional_text(data["failure_code"], where="failure_code"),
            envelope=dict(data),
        )
