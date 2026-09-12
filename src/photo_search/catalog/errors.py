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
