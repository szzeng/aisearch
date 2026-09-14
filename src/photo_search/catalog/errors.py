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

#: P2-03 artifact/outbox/indexing codes. These identify deterministic protocol or
#: derived-store failures without leaking a local storage path.
ARTIFACT_INVALID = "ARTIFACT_INVALID"
ARTIFACT_CONFLICT = "ARTIFACT_CONFLICT"
ARTIFACT_MISSING = "ARTIFACT_MISSING"
SPACE_MISMATCH = "SPACE_MISMATCH"
INDEX_SUBMISSION_FAILED = "INDEX_SUBMISSION_FAILED"
INDEX_REPAIR_REQUIRED = "INDEX_REPAIR_REQUIRED"

#: P2-03A place codes, frozen by ADR-0005 §9. The spelling and the HTTP mapping are part
#: of the contract, so a caller can branch on them without parsing a message.
PLACE_NOT_FOUND = "PLACE_NOT_FOUND"
PLACE_ASSIGNMENT_CONFLICT = "PLACE_ASSIGNMENT_CONFLICT"
PLACE_IDEMPOTENCY_CONFLICT = "PLACE_IDEMPOTENCY_CONFLICT"
PLACE_BATCH_TOO_LARGE = "PLACE_BATCH_TOO_LARGE"
PLACE_PARENT_CYCLE = "PLACE_PARENT_CYCLE"
PLACE_REFERENCED = "PLACE_REFERENCED"
PLACE_OPERATION_NOT_UNDOABLE = "PLACE_OPERATION_NOT_UNDOABLE"
PLACE_DATASET_MISSING = "PLACE_DATASET_MISSING"
PLACE_DATASET_INVALID = "PLACE_DATASET_INVALID"
CATALOG_FILTER_REVISION_STALE = "CATALOG_FILTER_REVISION_STALE"
PLACE_SUGGESTION_REVISION_STALE = "PLACE_SUGGESTION_REVISION_STALE"
INVALID_PLACE_FILTER = "INVALID_PLACE_FILTER"
GEO_COORDINATE_INVALID = "GEO_COORDINATE_INVALID"

#: P2-03B: the resolver needs a *current* GPS fact. A missing fact is a blocking condition
#: (the GPS task may still be pending), never a "no candidate" success and never a write.
GEO_FACT_MISSING = "GEO_FACT_MISSING"

#: P2-05 retrieval: the vector engine refused the requested pre-filter expression. A
#: refused filter is a structured failure with zero rows; the engine never silently
#: degrades into an unfiltered search (P0-02C, architecture §9.3 item 4).
FILTER_UNSUPPORTED = "FILTER_UNSUPPORTED"

#: P2-06A search protocol. ``SEARCH_SESSION_EXPIRED`` is the identifiable "this session is
#: gone" answer (process restart or TTL) instead of an empty page or a silently new
#: ordering; ``PROFILE_GENERATION_MISMATCH`` is the ADR-0003 §8/§10 refusal of a
#: half-switched profile - a profile, generation, space or dimension that does not belong
#: together is never served by computing a similarity in the wrong space.
#: ``INVALID_CURSOR`` moved here from the API layer when the search protocol needed it: the
#: value is unchanged, and the service layer can now refuse a foreign cursor without
#: importing the HTTP layer.
INVALID_CURSOR = "INVALID_CURSOR"
SEARCH_SESSION_EXPIRED = "SEARCH_SESSION_EXPIRED"
PROFILE_GENERATION_MISMATCH = "PROFILE_GENERATION_MISMATCH"

#: P2-06B: explicit relevance feedback. ``FEEDBACK_IDEMPOTENCY_CONFLICT`` reuses the
#: P2-03A idempotency semantics (same ``request_id`` + different payload = structured
#: conflict, zero writes) under its own stable code, because the feedback resource is not a
#: place operation and a caller must be able to branch on it without parsing a message.
FEEDBACK_IDEMPOTENCY_CONFLICT = "FEEDBACK_IDEMPOTENCY_CONFLICT"

#: P2-07 upload-media boundary (architecture §10.3: "上传图有大小、像素数和解码时间限制").
#: Both are *caller-shape* refusals with no honest equivalent in the frozen set: a body over
#: the byte ceiling and an image over the pixel ceiling are different facts from an
#: undecodable file (``DECODE_FAILED``) and from a format this build refuses
#: (``FORMAT_UNSUPPORTED``). ``RUNTIME_CAPABILITY_UNAVAILABLE`` stays the code for "this
#: process has no image encoder", so a caller can tell "too big" from "cannot run".
UPLOAD_TOO_LARGE = "UPLOAD_TOO_LARGE"
UPLOAD_TOO_MANY_PIXELS = "UPLOAD_TOO_MANY_PIXELS"

#: ADR-0005 §6.2: a batch is 1..500 de-duplicated assets, committed in one transaction.
MAX_PLACE_BATCH_SIZE = 500

#: P3-03 person naming/merge/redirect layer, frozen by ADR-0006 §9. As with the place
#: family the spelling and the HTTP mapping are part of the contract, so a caller can branch
#: on the code without parsing a message: ``PERSON_NOT_FOUND`` is the missing person or the
#: missing operation, the name/batch refusals are caller-shape errors, and everything else
#: is a current-state conflict that must never overwrite a later human edit.
PERSON_NOT_FOUND = "PERSON_NOT_FOUND"
PERSON_NAME_INVALID = "PERSON_NAME_INVALID"
PERSON_REVISION_CONFLICT = "PERSON_REVISION_CONFLICT"
PERSON_IDEMPOTENCY_CONFLICT = "PERSON_IDEMPOTENCY_CONFLICT"
PERSON_BATCH_TOO_LARGE = "PERSON_BATCH_TOO_LARGE"
PERSON_REDIRECT_CYCLE = "PERSON_REDIRECT_CYCLE"
PERSON_OPERATION_NOT_UNDOABLE = "PERSON_OPERATION_NOT_UNDOABLE"

#: ADR-0006 §5: an explicit merge names 1..500 de-duplicated source persons and commits
#: them, with their target, in one transaction.
MAX_PERSON_BATCH_SIZE = 500

#: P3-01 OCR (ADR-0003 §2/§4 frozen shell; the weights are the P0-04 pinned files). The
#: pinned det/cls/rec ONNX files and their ``config.yaml`` live under the user data root, so
#: "not installed yet" and "installed but different bytes" are configuration facts with no
#: honest equivalent in the frozen set - and they must never degrade into a silent load of
#: the wheel's own copy of the same models. ``OCR_RESULT_TOO_LARGE`` is the bounded-output
#: refusal (more regions than one document may carry); ``OCR_DEADLINE_EXCEEDED`` is the
#: bounded-execution refusal of the CPU budget; ``OCR_INPUT_TOO_LARGE`` refuses an image
#: whose declared pixels or required slice count exceed the frozen limits.
OCR_WEIGHTS_MISSING = "OCR_WEIGHTS_MISSING"
OCR_WEIGHTS_HASH_MISMATCH = "OCR_WEIGHTS_HASH_MISMATCH"
OCR_RESULT_TOO_LARGE = "OCR_RESULT_TOO_LARGE"
OCR_DEADLINE_EXCEEDED = "OCR_DEADLINE_EXCEEDED"
OCR_INPUT_TOO_LARGE = "OCR_INPUT_TOO_LARGE"

#: P3-02 face layer (ADR-0003 §2/§4 frozen shell; the weights are the P0-04 pinned
#: ``buffalo_l`` detection/recognition graphs). The family mirrors the P3-01 OCR codes
#: one-for-one for the same reasons: a pinned file that is not installed or has different
#: bytes is a configuration fact that must never degrade into a silent load of another
#: copy, the bounded-input/bounded-output refusals are caller-shape facts rather than
#: image statements (``DECODE_FAILED``), and running out of the wall-clock budget is a
#: transient condition rather than a verdict about the picture. ``FACE_DEVICE_UNAVAILABLE``
#: is the "this process cannot open the resolved execution provider" refusal: a GPU fault
#: never becomes a silent CPU run (ADR-0001/ADR-0003 §2).
FACE_WEIGHTS_MISSING = "FACE_WEIGHTS_MISSING"
FACE_WEIGHTS_HASH_MISMATCH = "FACE_WEIGHTS_HASH_MISMATCH"
FACE_RESULT_TOO_LARGE = "FACE_RESULT_TOO_LARGE"
FACE_DEADLINE_EXCEEDED = "FACE_DEADLINE_EXCEEDED"
FACE_INPUT_TOO_LARGE = "FACE_INPUT_TOO_LARGE"
FACE_DEVICE_UNAVAILABLE = "FACE_DEVICE_UNAVAILABLE"


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
