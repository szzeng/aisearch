"""The unified API error envelope, its catalog-code mapping and path redaction.

Architecture §10.2 requires every failure to carry a stable ``code``, a human ``message``
and a ``request_id``, and §10.3 forbids handing a caller a Python traceback or a local
path outside the registered sources. ``Redactor`` is the single place that turns an
absolute source/data path into a placeholder before it can reach a response body, a log
line or a job listing.
"""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse

from photo_search.catalog.errors import (
    ACCESS_DENIED,
    ARTIFACT_WRITE_FAILED,
    ASSET_NOT_FOUND,
    ASSET_SIZE_MISMATCH,
    CATALOG_UNREADABLE,
    DATA_ROOT_INSIDE_SOURCE,
    DECODE_FAILED,
    DISK_SPACE_INSUFFICIENT,
    FILE_READ_ERROR,
    FORMAT_UNSUPPORTED,
    INPUT_UNSTABLE,
    INVALID_PATH,
    INVALID_STATE_TRANSITION,
    JOB_NOT_FOUND,
    LEASE_CONFLICT,
    LEASE_EXPIRED,
    MEDIA_INTERRUPTED,
    MIGRATION_FAILED,
    PATH_KEY_COLLISION,
    PROCESSOR_MISMATCH,
    RETRY_EXHAUSTED,
    SCAN_INTERRUPTED,
    SCHEMA_INCONSISTENT,
    SCHEMA_TOO_NEW,
    SOURCE_FILE_MISSING,
    SOURCE_NOT_FOUND,
    SOURCE_OFFLINE,
    SOURCE_OVERLAP,
    STALE_INPUT_REVISION,
    STALE_LEASE,
)

#: Session/transport-level codes raised by the API layer itself.
SESSION_REQUIRED = "SESSION_REQUIRED"
SESSION_INVALID = "SESSION_INVALID"
CSRF_INVALID = "CSRF_INVALID"
ORIGIN_NOT_ALLOWED = "ORIGIN_NOT_ALLOWED"
HOST_NOT_ALLOWED = "HOST_NOT_ALLOWED"
VALIDATION_ERROR = "VALIDATION_ERROR"
INVALID_CURSOR = "INVALID_CURSOR"
INVALID_RANGE = "INVALID_RANGE"
RANGE_NOT_SATISFIABLE = "RANGE_NOT_SATISFIABLE"
INTERNAL_ERROR = "INTERNAL_ERROR"
MEDIA_NOT_READY = "MEDIA_NOT_READY"
#: The packaged front-end shell or one of its assets is missing from this build.
WEB_ASSET_NOT_FOUND = "WEB_ASSET_NOT_FOUND"

REQUEST_ID_HEADER = "X-Request-ID"
MAX_REQUEST_ID_CHARS = 64

#: JSON body cap for a structured error, so a huge ``details`` never becomes a payload.
MAX_ERROR_DETAILS_CHARS = 2_000

_STATUS_BY_CODE: dict[str, int] = {
    # Not found: the caller named something that does not exist.
    ASSET_NOT_FOUND: 404,
    SOURCE_NOT_FOUND: 404,
    JOB_NOT_FOUND: 404,
    WEB_ASSET_NOT_FOUND: 404,
    # Conflict: the request is well-formed but the current state forbids it. The task card
    # deliberately folds "unsupported"/"unavailable" into 409 with a stable code.
    SOURCE_OVERLAP: 409,
    DATA_ROOT_INSIDE_SOURCE: 409,
    PATH_KEY_COLLISION: 409,
    DISK_SPACE_INSUFFICIENT: 409,
    SCHEMA_TOO_NEW: 409,
    SCHEMA_INCONSISTENT: 409,
    INVALID_STATE_TRANSITION: 409,
    LEASE_CONFLICT: 409,
    STALE_LEASE: 409,
    STALE_INPUT_REVISION: 409,
    PROCESSOR_MISMATCH: 409,
    RETRY_EXHAUSTED: 409,
    LEASE_EXPIRED: 409,
    DECODE_FAILED: 409,
    FORMAT_UNSUPPORTED: 409,
    ARTIFACT_WRITE_FAILED: 409,
    SOURCE_FILE_MISSING: 409,
    SOURCE_OFFLINE: 409,
    INPUT_UNSTABLE: 409,
    ASSET_SIZE_MISMATCH: 409,
    FILE_READ_ERROR: 409,
    MEDIA_INTERRUPTED: 409,
    SCAN_INTERRUPTED: 409,
    MEDIA_NOT_READY: 409,
    # Caller errors.
    INVALID_PATH: 422,
    INVALID_CURSOR: 422,
    INVALID_RANGE: 416,
    RANGE_NOT_SATISFIABLE: 416,
    VALIDATION_ERROR: 422,
    # Access.
    ACCESS_DENIED: 403,
    SESSION_REQUIRED: 401,
    SESSION_INVALID: 401,
    CSRF_INVALID: 403,
    ORIGIN_NOT_ALLOWED: 403,
    HOST_NOT_ALLOWED: 403,
    # Local installation failures.
    CATALOG_UNREADABLE: 500,
    MIGRATION_FAILED: 500,
    INTERNAL_ERROR: 500,
}


def status_for_code(code: str) -> int:
    """Map a structured error code to an HTTP status; unknown codes are server errors."""
    return _STATUS_BY_CODE.get(str(code), 500)


class ApiError(Exception):
    """A structured API failure with a stable code and an explicit HTTP status."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int | None = None,
        details: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.code = str(code)
        self.message = str(message)
        self.status = int(status) if status is not None else status_for_code(self.code)
        self.details = dict(details) if details else {}
        self.headers = dict(headers) if headers else {}
        super().__init__(f"{self.code}: {self.message}")


def request_id_from(raw: str | None) -> str:
    """Use a caller-supplied request id when it is harmless, otherwise generate one."""
    if raw:
        candidate = str(raw).strip()
        if 0 < len(candidate) <= MAX_REQUEST_ID_CHARS and all(
            0x21 <= ord(char) <= 0x7E for char in candidate
        ):
            return candidate
    return secrets.token_hex(8)


def error_payload(
    *,
    code: str,
    message: str,
    request_id: str,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The one envelope shape every failing endpoint returns."""
    payload: dict[str, Any] = {"code": str(code), "message": str(message), "request_id": str(request_id)}
    if details:
        trimmed = _bounded(dict(details))
        if trimmed:
            payload["details"] = trimmed
    return payload


def error_response(
    *,
    code: str,
    message: str,
    request_id: str,
    status: int | None = None,
    details: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    response_headers = {REQUEST_ID_HEADER: str(request_id)}
    if headers:
        response_headers.update({str(key): str(value) for key, value in headers.items()})
    return JSONResponse(
        status_code=int(status) if status is not None else status_for_code(code),
        content=error_payload(code=code, message=message, request_id=request_id, details=details),
        headers=response_headers,
    )


def _bounded(details: dict[str, Any]) -> dict[str, Any]:
    """Drop or truncate a ``details`` value that would dominate the error body."""
    try:
        encoded = json.dumps(details, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"note": "details could not be serialised"}
    if len(encoded) > MAX_ERROR_DETAILS_CHARS:
        return {"note": "details omitted: too large to report", "bytes": len(encoded)}
    return details


class Redactor:
    """Replace absolute user paths with placeholders before they reach a caller.

    Registered source roots and the data root are the only local absolute paths this
    application knows about; everything else (relative display paths, digests, codes)
    stays readable so a client can still act on the error.
    """

    def __init__(
        self,
        *,
        source_roots: Sequence[str] = (),
        data_root: str | Path | None = None,
    ) -> None:
        pairs: list[tuple[str, str]] = []
        for root in source_roots:
            text = str(root)
            if text:
                pairs.append((text, "<source_root>"))
        if data_root is not None:
            pairs.append((str(data_root), "<data_root>"))
        # Longest first so a nested root never leaves a partial path behind.
        self._pairs = tuple(sorted(pairs, key=lambda pair: len(pair[0]), reverse=True))
        self._variants = tuple(
            (self._root_pattern(root), replacement)
            for root, replacement in self._pairs
        )

    @classmethod
    def for_catalog(cls, catalog: Any, *, data_root: str | Path | None = None) -> Redactor:
        try:
            rows = catalog.read_all("SELECT root_path FROM source_roots")
        except Exception:  # pragma: no cover - a broken catalog must not break reporting
            rows = []
        return cls(source_roots=[str(row["root_path"]) for row in rows], data_root=data_root)

    @property
    def pairs(self) -> tuple[tuple[str, str], ...]:
        return self._pairs

    def text(self, value: Any) -> str:
        rendered = "" if value is None else str(value)
        if not self._pairs:
            return rendered
        for pattern, replacement in self._variants:
            rendered = pattern.sub(replacement, rendered)
        return _LOCAL_PATH.sub("<local_path>", rendered)

    def details(self, value: Any) -> Any:
        """Recursively redact a JSON-shaped value; keys are sanitised too."""
        if isinstance(value, Mapping):
            return {self.text(key): self.details(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.details(item) for item in value]
        if isinstance(value, str):
            return self.text(value)
        return value

    @staticmethod
    def _root_pattern(root: str) -> re.Pattern[str]:
        """Match Windows renderings of a logical root, plus its literal spelling."""
        if not re.match(r"^[A-Za-z]:[\\/]", root):
            return re.compile(re.escape(root), re.IGNORECASE)
        parts = [part for part in re.split(r"[\\/]+", root) if part]
        separator = r"(?:\\{1,2}|/)"
        # Windows error messages can use a normal path, a repr-escaped path, mixed
        # separators, or the extended-length ``\\?\`` spelling.
        pattern = r"(?:\\\\\?\\)?" + re.escape(parts[0])
        if len(parts) > 1:
            pattern += separator + separator.join(re.escape(part) for part in parts[1:])
        return re.compile(pattern, re.IGNORECASE)


# A registered root is preferred above, but error records can contain an unexpected
# absolute path (for example from a third-party decoder).  Keep this deliberately
# Windows-specific: POSIX-looking text is not necessarily a local path in this app.
_LOCAL_PATH = re.compile(
    r"(?i)(?:\\\\\?\\UNC\\[^\\\r\n\"']+(?:\\{1,2}|/)|"
    r"\\\\\?\\[A-Za-z]:(?:\\{1,2}|/)|"
    r"\\\\[^\\/\r\n\"']+(?:\\{1,2}|/)|"
    r"[A-Za-z]:(?:\\{1,2}|/))[^\r\n\"']+"
)
