"""Pydantic request/response models for the local business API (architecture §10.2).

The models are the OpenAPI contract P1-05 will consume, so every field the routers return
is declared here and nothing that carries a credential, a lease token or an absolute user
path is part of a response model.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AvailabilityFilter = Literal["present", "missing", "any"]
ReadyFilter = Literal["true", "false", "any"]
AssetOrder = Literal["asset_id", "time_desc"]


class ErrorResponse(BaseModel):
    """The single failure envelope every endpoint uses."""

    code: str
    message: str
    request_id: str
    details: dict[str, Any] | None = None


class SessionResponse(BaseModel):
    """Result of burning the one-time credential; the cookies carry the session itself."""

    status: Literal["ok"] = "ok"
    csrf_token: str
    session_expires: Literal["process"] = "process"


class MediaKindSpec(BaseModel):
    kind: str
    processor_id: str
    long_edge: int
    output_format: str
    jpeg_quality: int
    supported_input_formats: list[str]


class SchedulerSummary(BaseModel):
    """Persisted pause policy; carries no credential, lease or path."""

    state: Literal["running", "paused"]
    revision: int
    aging_window_ms: int
    updated_at: int


class RuntimeHealthSummary(BaseModel):
    """The latest runtime capability revision; no model path or driver DLL path."""

    capability_revision: str
    runtime_package_id: str
    runtime_backend: str
    health_status: Literal["healthy", "degraded", "unhealthy", "unknown"]
    health_checked_at: int
    measured_at: int


class SchedulerStateResponse(BaseModel):
    """Result of ``POST /api/v1/scheduler/pause`` / ``resume``; idempotent by design."""

    state: Literal["running", "paused"]
    revision: int
    aging_window_ms: int
    updated_at: int
    changed: bool
    reason: str | None = None


class CapabilitiesResponse(BaseModel):
    """Build and contract facts. Contains no data-directory path and no user data."""

    app_version: str
    api_version: Literal["v1"] = "v1"
    schema_version: int | None
    supported_schema_version: int
    writable: bool
    schema_status: str
    journal_mode: str | None = None
    controlled_reads: bool = True
    session_required: bool = True
    loopback_only: bool
    allowed_hosts: list[str]
    media: dict[str, MediaKindSpec]
    supported_input_formats: list[str]
    #: P2-02 scheduler/runtime health. ``None`` means this catalog predates the 0002
    #: migration; the API never guesses a device or a health verdict.
    scheduler: SchedulerSummary | None = None
    runtime: RuntimeHealthSummary | None = None
    qualification_evidence: int = 0


class SourceRecord(BaseModel):
    source_id: int
    display_name: str
    root_path: str
    volume_hint: str | None = None
    status: str
    last_successful_scan: int | None = None


class SourceDetail(SourceRecord):
    counts: dict[str, Any] | None = None


class SourceListResponse(BaseModel):
    sources: list[SourceDetail]


class SourceCreateRequest(BaseModel):
    """``root_path`` is a mapping to a registered root, never the identity of a source."""

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=200)
    root_path: str = Field(min_length=1, max_length=4096)
    volume_hint: str | None = Field(default=None, max_length=64)


class MediaKindSummary(BaseModel):
    processor_id: str
    long_edge: int
    published: int
    pending: int
    source_missing: int
    failed: int
    unsupported: int
    failure_codes: dict[str, int]
    published_files: int
    published_bytes: int
    temp_files: int
    stale_versions: list[str]


class StatsResponse(BaseModel):
    counts: dict[str, Any]
    media: dict[str, MediaKindSummary]
    assets_examined: int
    assets_total: int
    partial: bool


class LocationRef(BaseModel):
    """The one location summary a list row may show; always source-relative."""

    source_id: int
    relative_path: str


class AssetLocation(BaseModel):
    location_id: int
    source_id: int
    source_display_name: str | None = None
    relative_path: str
    availability: str
    size: int
    mtime_ns: int
    asset_bound: bool


class MediaState(BaseModel):
    kind: str
    processor_id: str
    long_edge: int
    state: Literal["ready", "pending", "source_missing", "failed", "unsupported"]
    code: str | None = None
    message: str | None = None


class AssetSummary(BaseModel):
    asset_id: int
    sha256: str
    byte_size: int
    created_at: int
    location_count: int
    availability: Literal["present", "missing"]
    thumbnail_ready: bool
    preview_ready: bool
    current_location: LocationRef | None = None


class AssetDetail(BaseModel):
    asset_id: int
    sha256: str
    byte_size: int
    width: int | None = None
    height: int | None = None
    format: str | None = None
    created_at: int
    state: str
    availability: Literal["present", "missing"]
    location_count: int
    locations: list[AssetLocation]
    media: dict[str, MediaState]


class AssetListResponse(BaseModel):
    assets: list[AssetSummary]
    limit: int
    next_cursor: str | None = None
    has_more: bool
    availability: AvailabilityFilter
    has_thumbnail: ReadyFilter
    source_id: int | None = None
    #: The ordering that actually produced this page; ``time_desc`` is insertion time.
    order: AssetOrder = "asset_id"


class ScanState(BaseModel):
    """The ``scan_runs`` projection a job listing may show."""

    scan_id: int
    source_id: int
    state: str
    started_at: int | None = None
    finished_at: int | None = None
    error_code: str | None = None
    error_message: str | None = None


class ScanRequestResponse(BaseModel):
    """``202 Accepted``: the scan runs in the background, never inside the request."""

    source_id: int
    scan_id: int
    job_id: int
    state: str
    reused: bool


class JobRecord(BaseModel):
    """A ``jobs`` row without the lease token, the worker id or any absolute path."""

    job_id: int
    subject_type: str
    subject_id: str
    task_type: str
    processor_id: str
    state: str
    priority: int
    attempts: int
    max_attempts: int
    progress: float
    run_after: int
    last_error_code: str | None = None
    last_error_message: str | None = None
    created_at: int
    updated_at: int
    #: P2-02 dispatch summary. The device fields are the resolved snapshot, never a request
    #: to the client, and ``blocking`` states a stable code instead of a local path.
    requested_device: str = "auto"
    resolved_device: str | None = None
    runtime_provider: str | None = None
    precision: str | None = None
    runtime_capability_revision: str | None = None
    blocking_reason: str | None = None
    scan: ScanState | None = None


class JobListResponse(BaseModel):
    jobs: list[JobRecord]
    limit: int
    next_cursor: str | None = None
    has_more: bool
