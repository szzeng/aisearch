"""Structured catalog errors (ADR-0002 §9).

The codes below are the minimum set the P1-01 contract requires. Extra codes exist only
where a failure cannot be classified honestly with the frozen set; they never replace a
required code.
"""

from __future__ import annotations

from typing import Any

SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
SOURCE_OFFLINE = "SOURCE_OFFLINE"
SOURCE_OVERLAP = "SOURCE_OVERLAP"
DATA_ROOT_INSIDE_SOURCE = "DATA_ROOT_INSIDE_SOURCE"
ACCESS_DENIED = "ACCESS_DENIED"
PATH_KEY_COLLISION = "PATH_KEY_COLLISION"
DISK_SPACE_INSUFFICIENT = "DISK_SPACE_INSUFFICIENT"
SCHEMA_TOO_NEW = "SCHEMA_TOO_NEW"
SCHEMA_INCONSISTENT = "SCHEMA_INCONSISTENT"
INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
LEASE_CONFLICT = "LEASE_CONFLICT"
STALE_LEASE = "STALE_LEASE"
STALE_INPUT_REVISION = "STALE_INPUT_REVISION"
PROCESSOR_MISMATCH = "PROCESSOR_MISMATCH"

#: Extra codes used where the frozen set has no honest classification.
INVALID_PATH = "INVALID_PATH"
CATALOG_UNREADABLE = "CATALOG_UNREADABLE"
MIGRATION_FAILED = "MIGRATION_FAILED"
JOB_NOT_FOUND = "JOB_NOT_FOUND"
FILE_READ_ERROR = "FILE_READ_ERROR"
ASSET_SIZE_MISMATCH = "ASSET_SIZE_MISMATCH"
RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
INPUT_UNSTABLE = "INPUT_UNSTABLE"
LEASE_EXPIRED = "LEASE_EXPIRED"
SCAN_INTERRUPTED = "SCAN_INTERRUPTED"

#: P1-03 derived-media codes. ``SOURCE_FILE_MISSING`` and ``DISK_SPACE_INSUFFICIENT`` are
#: deliberately reused from the frozen scan set; the rest have no honest equivalent there.
ASSET_NOT_FOUND = "ASSET_NOT_FOUND"
SOURCE_FILE_MISSING = "SOURCE_FILE_MISSING"
DECODE_FAILED = "DECODE_FAILED"
FORMAT_UNSUPPORTED = "FORMAT_UNSUPPORTED"
ARTIFACT_WRITE_FAILED = "ARTIFACT_WRITE_FAILED"
MEDIA_INTERRUPTED = "MEDIA_INTERRUPTED"

#: Codes a media batch records per asset and keeps enumerable without re-decoding.
#: Only the first two are treated as deterministic properties of the input content.
DETERMINISTIC_MEDIA_FAILURES = frozenset({DECODE_FAILED, FORMAT_UNSUPPORTED})

#: P2-02 scheduler/runtime codes (ADR-0003 §2/§5/§6). ``DEVICE_POLICY_CONFLICT`` is a
#: permanent configuration refusal; ``RUNTIME_CAPABILITY_UNAVAILABLE`` is a capability
#: block that a new healthy revision may re-qualify; ``SCHEDULER_PAUSED`` refuses new
#: claims only and never touches a live lease.
DEVICE_POLICY_CONFLICT = "DEVICE_POLICY_CONFLICT"
RUNTIME_CAPABILITY_UNAVAILABLE = "RUNTIME_CAPABILITY_UNAVAILABLE"
RUNTIME_RESOLUTION_MISMATCH = "RUNTIME_RESOLUTION_MISMATCH"
RUNTIME_CAPABILITY_REVISION_CONFLICT = "RUNTIME_CAPABILITY_REVISION_CONFLICT"
SCHEDULER_PAUSED = "SCHEDULER_PAUSED"
#: Extra codes with no honest equivalent in the frozen set above.
INVALID_JOB_RESULT = "INVALID_JOB_RESULT"
SCHEDULER_EXECUTOR_ERROR = "SCHEDULER_EXECUTOR_ERROR"


class CatalogError(Exception):
    """A structured, JSON-serialisable failure of the catalog or ingestion layer."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.details = dict(details) if details else {}
        super().__init__(f"{code}: {message}")

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = dict(self.details)
        return payload

    @property
    def error_json(self) -> str:
        """Compact JSON for ``scan_runs.error``; contains no personal data."""
        import json

        return json.dumps(self.as_dict(), ensure_ascii=False)
