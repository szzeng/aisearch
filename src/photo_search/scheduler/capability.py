"""Immutable runtime-capability snapshots and per-processor device evidence (ADR-0003 §5/§6).

Two different facts decide whether a device may be dispatched:

* a *capability snapshot* — what this machine's runtime currently offers, and whether the
  provider was healthy at a known moment. Snapshots are immutable history: a new health
  check writes a new revision instead of rewriting the old one.
* *qualification evidence* — proof that this specific processor/device/precision/runtime
  combination was accepted. A listed or even healthy provider is never enough on its own,
  and another processor's evidence never qualifies this one.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from photo_search.catalog.errors import (
    INVALID_JOB_RESULT,
    RUNTIME_CAPABILITY_REVISION_CONFLICT,
    CatalogError,
)
from photo_search.scheduler.contracts import RuntimeCapabilities

#: The two ONNX Runtime providers the product maps onto the stable cpu/gpu spelling.
#: They are constant strings on purpose: importing ``photo_search.runtime.device`` would
#: pull ONNX Runtime into every scheduler-using process.
CUDA_PROVIDER_NAME = "CUDAExecutionProvider"
CPU_PROVIDER_NAME = "CPUExecutionProvider"


@dataclass(frozen=True)
class QualificationEvidence:
    evidence_id: str
    processor_id: str
    spec_revision: str
    device: str
    precision: str
    provider_name: str
    runtime_package_id: str
    verified_at: int
    note: str | None = None


def _snapshot_from_row(row: sqlite3.Row | Mapping[str, Any]) -> RuntimeCapabilities:
    import json

    return RuntimeCapabilities.from_envelope(json.loads(str(row["normalized_json"])))


def record_capability(
    connection: sqlite3.Connection,
    snapshot: RuntimeCapabilities,
    *,
    now_ms: int,
) -> RuntimeCapabilities:
    """Store one immutable snapshot; an identical revision is idempotent.

    The revision is derived from the facts themselves, so two snapshots of the same facts
    collapse into one row, while a real health change (or a later check time) produces a
    new revision and leaves the previous one readable for old dispatch records.
    """
    if snapshot.capability_revision != snapshot.expected_revision:
        raise CatalogError(
            RUNTIME_CAPABILITY_REVISION_CONFLICT,
            "the capability revision does not match its own facts",
            details={
                "capability_revision": snapshot.capability_revision,
                "expected_revision": snapshot.expected_revision,
            },
        )
    import json

    existing = connection.execute(
        "SELECT checksum_sha256 FROM runtime_capabilities WHERE capability_revision = ?",
        (snapshot.capability_revision,),
    ).fetchone()
    if existing is not None:
        if str(existing["checksum_sha256"]) != snapshot.checksum_sha256:
            raise CatalogError(
                RUNTIME_CAPABILITY_REVISION_CONFLICT,
                "this capability revision is already recorded with different facts",
                details={"capability_revision": snapshot.capability_revision},
            )
        return snapshot
    connection.execute(
        """
        INSERT INTO runtime_capabilities (
            capability_revision, contract_version, runtime_package_id, runtime_backend,
            runtime_package_name, runtime_package_version, python_version, platform,
            driver_version, health_status, health_checked_at, measured_at,
            evidence_ids_json, normalized_json, checksum_sha256, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot.capability_revision,
            str(snapshot.envelope.get("contract_version")),
            snapshot.runtime_package_id,
            snapshot.runtime_backend,
            snapshot.runtime_package_name,
            snapshot.runtime_package_version,
            snapshot.python_version,
            snapshot.platform,
            snapshot.driver_version,
            snapshot.health_status,
            snapshot.health_checked_at,
            snapshot.measured_at,
            json.dumps(list(snapshot.evidence_ids), ensure_ascii=False),
            snapshot.normalized_json,
            snapshot.checksum_sha256,
            int(now_ms),
        ),
    )
    return snapshot


def latest_capability(
    connection: sqlite3.Connection, *, runtime_package_id: str | None = None
) -> RuntimeCapabilities | None:
    """The most recently recorded snapshot, optionally for one runtime package.

    Snapshots are per runtime package: one product can host an ONNX Runtime backend and a
    pure-Python backend at the same time, so an unhealthy GPU package must not block an
    unrelated CPU-only processor (ADR-0003 §5).
    """
    if runtime_package_id is None:
        row = connection.execute(
            "SELECT normalized_json FROM runtime_capabilities "
            "ORDER BY recorded_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT normalized_json FROM runtime_capabilities WHERE runtime_package_id = ? "
            "ORDER BY recorded_at DESC, rowid DESC LIMIT 1",
            (str(runtime_package_id),),
        ).fetchone()
    return None if row is None else _snapshot_from_row(row)


def capability_by_revision(
    connection: sqlite3.Connection, capability_revision: str
) -> RuntimeCapabilities | None:
    row = connection.execute(
        "SELECT normalized_json FROM runtime_capabilities WHERE capability_revision = ?",
        (str(capability_revision),),
    ).fetchone()
    return None if row is None else _snapshot_from_row(row)


def record_qualification(
    connection: sqlite3.Connection,
    *,
    processor_id: str,
    spec_revision: str,
    device: str,
    precision: str,
    provider_name: str,
    runtime_package_id: str,
    now_ms: int,
    evidence_id: str | None = None,
    note: str | None = None,
) -> QualificationEvidence:
    """Record that this processor/device/precision combination was really accepted."""
    if device not in ("cpu", "gpu"):
        raise CatalogError(
            INVALID_JOB_RESULT,
            f"qualification device {device!r} is not cpu/gpu",
            details={"device": str(device)},
        )
    identifier = evidence_id or (
        f"{processor_id}:{spec_revision}:{device}:{precision}:{provider_name}"
    )
    connection.execute(
        """
        INSERT INTO runtime_qualification_evidence (
            evidence_id, processor_id, spec_revision, device, precision, provider_name,
            runtime_package_id, verified_at, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (evidence_id) DO NOTHING
        """,
        (
            identifier,
            str(processor_id),
            str(spec_revision),
            str(device),
            str(precision),
            str(provider_name),
            str(runtime_package_id),
            int(now_ms),
            note,
        ),
    )
    row = connection.execute(
        "SELECT * FROM runtime_qualification_evidence WHERE evidence_id = ?", (identifier,)
    ).fetchone()
    return QualificationEvidence(
        evidence_id=str(row["evidence_id"]),
        processor_id=str(row["processor_id"]),
        spec_revision=str(row["spec_revision"]),
        device=str(row["device"]),
        precision=str(row["precision"]),
        provider_name=str(row["provider_name"]),
        runtime_package_id=str(row["runtime_package_id"]),
        verified_at=int(row["verified_at"]),
        note=row["note"],
    )


def qualification_evidence(
    connection: sqlite3.Connection, *, processor_id: str | None = None
) -> tuple[QualificationEvidence, ...]:
    if processor_id is None:
        rows = connection.execute(
            "SELECT * FROM runtime_qualification_evidence ORDER BY evidence_id"
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM runtime_qualification_evidence WHERE processor_id = ? ORDER BY evidence_id",
            (str(processor_id),),
        ).fetchall()
    return tuple(
        QualificationEvidence(
            evidence_id=str(row["evidence_id"]),
            processor_id=str(row["processor_id"]),
            spec_revision=str(row["spec_revision"]),
            device=str(row["device"]),
            precision=str(row["precision"]),
            provider_name=str(row["provider_name"]),
            runtime_package_id=str(row["runtime_package_id"]),
            verified_at=int(row["verified_at"]),
            note=row["note"],
        )
        for row in rows
    )


def snapshot_from_runtime(
    runtime: Any,
    *,
    now_ms: int,
    health: str = "unknown",
    provider_health: Mapping[str, str] | None = None,
    provider_precisions: Mapping[str, Sequence[str]] | None = None,
    evidence_ids: Sequence[str] = (),
    driver_version: str | None = None,
    failure_code: str | None = None,
) -> RuntimeCapabilities:
    """Adapt the P0 runtime probe facts into a sealed ``RuntimeCapabilities`` snapshot.

    This only *describes* the environment. A healthy provider listed here still does not
    qualify any processor: that needs a matching ``runtime_qualification_evidence`` row.
    """
    health_by_name = dict(provider_health or {})
    precisions_by_name = dict(provider_precisions or {})
    providers: list[dict[str, Any]] = []
    for name in tuple(getattr(runtime, "providers", ()) or ()):
        provider = str(name)
        if provider == CUDA_PROVIDER_NAME:
            device = "gpu"
        elif provider == CPU_PROVIDER_NAME:
            device = "cpu"
        else:
            device = "gpu" if "cuda" in provider.lower() else "cpu"
        default_precision = ["FP32"] if "ExecutionProvider" in provider else ["not_applicable"]
        providers.append(
            {
                "name": provider,
                "device": device,
                "precisions": list(precisions_by_name.get(provider, default_precision)),
                "available": True,
                "health": str(health_by_name.get(provider, "unknown")),
                "health_checked_at": int(now_ms),
                "evidence_id": None,
            }
        )
    if not providers:
        # The contract needs at least one provider entry; an empty runtime still has to be
        # describable so that affected jobs block instead of silently dispatching nothing.
        providers.append(
            {
                "name": CPU_PROVIDER_NAME,
                "device": "cpu",
                "precisions": ["not_applicable"],
                "available": False,
                "health": "unhealthy",
                "health_checked_at": int(now_ms),
                "evidence_id": None,
            }
        )
    facts: dict[str, Any] = {
        "runtime_package_id": str(
            getattr(runtime, "runtime_package_id", None)
            or f"{getattr(runtime, 'runtime_package', 'runtime')}@"
            f"{getattr(runtime, 'runtime_version', 'unknown')}"
        ),
        "runtime_backend": str(getattr(runtime, "runtime_backend", "onnxruntime")),
        "runtime_package_name": str(getattr(runtime, "runtime_package", "runtime")),
        "runtime_package_version": str(getattr(runtime, "runtime_version", "unknown")),
        "python_version": str(getattr(runtime, "python", "unknown")),
        "platform": str(getattr(runtime, "platform", "unknown")),
        "driver_version": driver_version,
        "providers": providers,
        "health_status": str(health),
        "health_checked_at": int(now_ms),
        "measured_at": int(now_ms),
        "evidence_ids": [str(value) for value in evidence_ids],
        "failure_code": failure_code,
    }
    return RuntimeCapabilities.seal(facts)
