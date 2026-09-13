"""The frozen device-qualification matrix (ADR-0003 §5), as one pure function.

The matrix is evaluated in the main scheduler before any lease is handed out, and the
answer is written to the job row. Workers never choose a device, and no failure ever
degrades into a different device: an eligible CPU is only used when the *policy* (or an
explicit request) says CPU, never as a silent rescue for a failed GPU.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from photo_search.catalog.errors import (
    DEVICE_POLICY_CONFLICT,
    INVALID_JOB_RESULT,
    RUNTIME_CAPABILITY_UNAVAILABLE,
    CatalogError,
)
from photo_search.scheduler.capability import QualificationEvidence
from photo_search.scheduler.contracts import (
    REQUESTED_DEVICES,
    ProcessorSpec,
    RuntimeCapabilities,
)


@dataclass(frozen=True)
class Qualification:
    """One resolution attempt: either a concrete device or a stable blocking reason."""

    eligible: bool
    device: str | None
    provider: str | None
    precision: str | None
    capability_revision: str | None
    reason_code: str | None
    detail: str

    @staticmethod
    def blocked(reason_code: str, detail: str) -> Qualification:
        return Qualification(
            eligible=False,
            device=None,
            provider=None,
            precision=None,
            capability_revision=None,
            reason_code=str(reason_code),
            detail=str(detail),
        )

    @staticmethod
    def ok(
        *, device: str, provider: str, precision: str, capability_revision: str, detail: str
    ) -> Qualification:
        return Qualification(
            eligible=True,
            device=device,
            provider=provider,
            precision=precision,
            capability_revision=capability_revision,
            reason_code=None,
            detail=detail,
        )


def _evidence_matches(
    spec: ProcessorSpec,
    *,
    device: str,
    provider_name: str,
    precision: str,
    evidence: Sequence[QualificationEvidence],
) -> bool:
    for row in evidence:
        if (
            row.processor_id == spec.processor_id
            and row.spec_revision == spec.spec_revision
            and row.device == device
            and row.precision == precision
            and row.provider_name == provider_name
            and row.runtime_package_id == spec.runtime_package_id
        ):
            return True
    return False


def _qualified(
    spec: ProcessorSpec,
    *,
    capability: RuntimeCapabilities | None,
    evidence: Sequence[QualificationEvidence],
    device: str,
) -> tuple[tuple[str, str] | None, str]:
    """Return ``((provider, precision), detail)`` for a fully qualified device."""
    if device not in spec.supported_devices:
        return None, f"processor does not declare {device} support"
    if capability is None:
        return None, "no runtime capability snapshot has been recorded"
    if capability.capability_revision != capability.expected_revision:
        return (
            None,
            "the recorded runtime capability snapshot does not match its own facts",
        )
    if str(capability.runtime_package_id) != str(spec.runtime_package_id):
        return None, (
            f"runtime package {capability.runtime_package_id} does not match the processor's "
            f"{spec.runtime_package_id}"
        )
    candidates = [
        provider
        for provider in capability.providers_for(device)
        if provider.available and provider.health == "healthy"
    ]
    if not candidates:
        named = ", ".join(provider.name for provider in capability.providers_for(device)) or "none"
        return None, (
            f"no available and healthy {device} provider in capability "
            f"{capability.capability_revision} (listed: {named})"
        )
    for provider in candidates:
        for precision in spec.supported_precisions:
            if precision not in provider.precisions:
                continue
            if _evidence_matches(
                spec,
                device=device,
                provider_name=provider.name,
                precision=precision,
                evidence=evidence,
            ):
                return (provider.name, precision), (
                    f"{device} via {provider.name}/{precision} using capability "
                    f"{capability.capability_revision}"
                )
    return None, (
        f"no {device} precision with recorded processor evidence for {spec.processor_id}"
    )


def _resolve_cpu(
    spec: ProcessorSpec,
    capability: RuntimeCapabilities | None,
    evidence: Sequence[QualificationEvidence],
) -> Qualification:
    found, detail = _qualified(spec, capability=capability, evidence=evidence, device="cpu")
    if found is None:
        return Qualification.blocked(RUNTIME_CAPABILITY_UNAVAILABLE, detail)
    provider, precision = found
    return Qualification.ok(
        device="cpu",
        provider=provider,
        precision=precision,
        capability_revision=str(capability.capability_revision),
        detail=detail,
    )


def _resolve_gpu(
    spec: ProcessorSpec,
    capability: RuntimeCapabilities | None,
    evidence: Sequence[QualificationEvidence],
) -> Qualification:
    found, detail = _qualified(spec, capability=capability, evidence=evidence, device="gpu")
    if found is None:
        return Qualification.blocked(RUNTIME_CAPABILITY_UNAVAILABLE, detail)
    provider, precision = found
    return Qualification.ok(
        device="gpu",
        provider=provider,
        precision=precision,
        capability_revision=str(capability.capability_revision),
        detail=detail,
    )


def resolve(
    spec: ProcessorSpec,
    *,
    requested_device: str,
    capability: RuntimeCapabilities | None,
    evidence: Sequence[QualificationEvidence] = (),
) -> Qualification:
    """Resolve one request against the frozen matrix, or explain why it must block."""
    if requested_device not in REQUESTED_DEVICES:
        raise CatalogError(
            INVALID_JOB_RESULT,
            f"requested_device {requested_device!r} is not auto/cpu/gpu",
            details={"requested_device": str(requested_device)},
        )
    if requested_device == "cpu":
        if spec.device_policy == "GPU_REQUIRED":
            return Qualification.blocked(
                DEVICE_POLICY_CONFLICT,
                "GPU_REQUIRED refuses an explicit cpu request; a GPU fault is never served "
                "by an implicit CPU run",
            )
        return _resolve_cpu(spec, capability, evidence)
    if requested_device == "gpu":
        return _resolve_gpu(spec, capability, evidence)
    # requested_device == "auto"
    if spec.device_policy == "GPU_REQUIRED":
        return _resolve_gpu(spec, capability, evidence)
    if spec.device_policy == "CPU_PREFERRED":
        return _resolve_cpu(spec, capability, evidence)
    gpu = _resolve_gpu(spec, capability, evidence)
    if gpu.eligible:
        return gpu
    # CPU_ALLOWED auto: a healthy, processor-qualified GPU is preferred; otherwise CPU is
    # the explicit auto resolution, not a fallback after an execution failure.
    cpu = _resolve_cpu(spec, capability, evidence)
    if cpu.eligible:
        return cpu
    return Qualification.blocked(
        RUNTIME_CAPABILITY_UNAVAILABLE,
        f"neither GPU nor CPU qualified (gpu: {gpu.detail}; cpu: {cpu.detail})",
    )
