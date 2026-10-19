"""Pydantic request/response models for the local business API (architecture §10.2).

The models are the OpenAPI contract P1-05 will consume, so every field the routers return
is declared here and nothing that carries a credential, a lease token or an absolute user
path is part of a response model.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

from photo_search.search.contracts import (
    DEFAULT_PAGE_SIZE,
    MAX_NOTE_CHARS,
    MAX_PAGE_SIZE,
)
from photo_search.search.retrieval import DEFAULT_TOP_K, MAX_TOP_K

AvailabilityFilter = Literal["present", "missing", "any"]
ReadyFilter = Literal["true", "false", "any"]
AssetOrder = Literal["asset_id", "time_desc", "capture_desc"]
PlaceStateFilter = Literal["any", "assigned", "unassigned", "manual_unassigned", "suggested"]
PersonMatchFilter = Literal["all", "any"]
PlaceAssignmentAction = Literal["assign", "unassign", "restore_auto"]
PlaceKind = Literal["country", "city", "attraction", "custom"]


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


class PageCapabilitiesResponse(BaseModel):
    """Schema and build facts for page startup, without model or coverage probes."""

    app_version: str
    api_version: Literal["v1"] = "v1"
    schema_version: int | None
    supported_schema_version: int
    schema_status: str
    controlled_reads: bool = True
    session_required: bool = True


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
    indexing: dict[str, Any] | None = None
    #: P2-04 derived-search facts (document/queue counts and the tokenizer generation);
    #: ``None`` means this catalog predates migration 0006. Contains no text and no path.
    search: dict[str, Any] | None = None
    #: P3-01 OCR facts: how many assets carry a *current* OCR document, how many carry one
    #: for an older content revision (still catching up), the region/confidence counts and
    #: the OCR job states. ``None`` means this catalog predates migration 0009. Contains no
    #: recognised text, no coordinate and no path.
    ocr: dict[str, Any] | None = None
    #: P3-02 face facts: how many assets carry *current* face facts, how many are still
    #: queued, the unnamed group/suggestion counts, the independent candidate-index state and
    #: the calibrated thresholds this build applies. ``None`` means this catalog predates
    #: migration 0010. Contains no bbox, no landmark, no vector and no path.
    faces: dict[str, Any] | None = None


class SourceSummary(BaseModel):
    """Fast, rootless source identity for the home screen.

    This is only catalog metadata; ``status`` is not a live filesystem check.
    """

    source_id: int
    display_name: str
    volume_hint: str | None = None
    status: str
    last_successful_scan: int | None = None


class SourceSummaryResponse(BaseModel):
    sources: list[SourceSummary]


class SourcePath(BaseModel):
    """A registered source path for the authenticated gallery-management screen."""

    source_id: int
    root_path: str


class SourcePathsResponse(BaseModel):
    sources: list[SourcePath]


class SourceRecord(BaseModel):
    source_id: int
    display_name: str
    root_path: str
    volume_hint: str | None = None
    status: str
    last_successful_scan: int | None = None


class SourceDetail(SourceRecord):
    counts: dict[str, Any] | None = None
    #: P4-02Y-I §3: the durable build progress of this source (scan denominator, semantic
    #: backlog, coarse enrichment states). Additive: an older client simply ignores it, and
    #: a caller that cannot resolve the semantic channel sees the stages reported as
    #: ``UNAVAILABLE`` instead of a guess.
    progress: dict[str, Any] | None = None


class SourceListResponse(BaseModel):
    sources: list[SourceDetail]


class SourceCreateRequest(BaseModel):
    """``root_path`` is a mapping to a registered root, never the identity of a source."""

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=200)
    root_path: str = Field(min_length=1, max_length=4096)
    volume_hint: str | None = Field(default=None, max_length=64)


class SourceRenameRequest(BaseModel):
    """Rename only the source's user-facing label."""

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=200)


class AssetLocalActionRequest(BaseModel):
    """One fixed desktop action on one database-registered file location."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["open", "reveal"]
    location_id: int = Field(gt=0, strict=True)


class SourceDeleteResponse(BaseModel):
    """``DELETE /api/v1/sources/{id}``: what leaving the managed set removed (P4-02Y-I §5).

    Counts only, and ``originals_touched`` is always zero: the answer must make the "this
    removes records, never photos" promise inspectable instead of merely asserted.
    """

    source_id: int
    display_name: str
    deleted: bool = True
    locations_removed: int
    scans_removed: int
    scan_jobs_removed: int
    #: Assets that had *only* this source: they keep their identity, embeddings and manual
    #: facts (no orphan GC in this card) but no longer have a readable position.
    assets_orphaned: int
    originals_touched: int = 0


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
    indexing: dict[str, Any] | None = None


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


class TakenTimeBlock(BaseModel):
    """ADR-0011 §8.1: the honesty block that explains a capture-conditioned empty page.

    It carries exactly four keys, and ``state`` is a pure function of the three counts it
    contains: a library whose stored ``taken_time_rules_version`` is not this build's reports
    ``extracted_assets``/``capture_time_assets`` as ``0`` (the rows stay in the catalog but
    are not trusted), so the same counts can never yield two different states. It never
    carries a raw EXIF value, a path, a coordinate or an instant.
    """

    model_config = ConfigDict(extra="forbid")

    state: Literal["PENDING", "PARTIAL", "READY", "NO_CAPTURE_TIME"]
    assets_total: int
    extracted_assets: int
    capture_time_assets: int


class AssetTakenTime(BaseModel):
    """ADR-0011 §8.1 item 3: the closed per-asset taken-time object of the detail endpoint.

    ``taken_at`` is the normalized *wall clock* string (``YYYY-MM-DDTHH:MM:SS.mmm``) and is
    present exactly when the row has a civil key; ``timezone_offset_minutes`` is present only
    when the file recorded one, so an offset-less row is never shown as UTC. ``taken_at_raw``
    is deliberately absent: the internal EXIF evidence text never leaves the catalog.
    """

    model_config = ConfigDict(extra="forbid")

    taken_at: str | None = None
    taken_at_sort: int | None = None
    time_source: Literal["EXIF_ORIGINAL", "EXIF_DIGITIZED", "EXIF_ROOT", "FILE_MTIME"] | None = (
        None
    )
    time_confidence: Literal["HIGH", "MEDIUM", "LOW"] | None = None
    timezone_offset_minutes: int | None = None
    label: Literal["capture", "file_time"] | None = None
    camera: str | None = None


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
    #: P4-00B: ``null`` means "no taken-time row has been published for this asset" (never
    #: extracted, or the library's rule version does not match this build). The object itself
    #: is closed and never carries the raw EXIF evidence.
    taken_time: AssetTakenTime | None = None


class SearchProjectionSummary(BaseModel):
    """The echo of one ``GET /assets?text=`` query and the freshness of its projection.

    ``state=LAGGING`` means the derived text index still has queued assets after the
    bounded catch-up; the authoritative SQLite filters were applied regardless, and every
    text hit here came from a document that was re-derived from SQLite in this request.
    """

    query: str
    terms: list[str] = Field(default_factory=list)
    match_expression: str
    tokenizer_version: int
    fts_generation_id: str
    documents: int
    queued_assets: int
    stale_documents: int
    processed_assets: int
    state: Literal["SYNCED", "LAGGING"]


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
    #: P2-03A place filter echo. ``catalog_filter_revision`` is the revision the page was
    #: computed under, so a caller can detect that a later edit invalidated it.
    place_ids: list[int] = Field(default_factory=list)
    include_descendants: bool = True
    place_state: PlaceStateFilter = "any"
    catalog_filter_revision: int | None = None
    #: P2-03B: the suggestion revision a ``place_state=suggested`` page was computed under.
    suggestion_revision: int | None = None
    #: P4-00B: the normalized capture condition this page ran under. Both keys are always
    #: present (``null`` when no condition was given), exactly like the frozen place keys.
    capture_from: str | None = None
    capture_to_exclusive: str | None = None
    #: P4-00B: how much of the library carries a trustworthy taken time. It appears only when
    #: at least one capture bound was given, and it exists so an empty page can say "the
    #: extraction has not run yet" instead of pretending there is no match.
    taken_time: TakenTimeBlock | None = None
    #: P2-04: present only when ``text`` was used, and honest about projection lag.
    search: SearchProjectionSummary | None = None


class FavoriteAssetSummary(AssetSummary):
    favorited_at_ms: int


class FavoriteListResponse(BaseModel):
    assets: list[FavoriteAssetSummary]
    next_cursor: str | None = None
    has_more: bool
    favorites_revision: int
    order: Literal["recent", "capture_desc"] = "recent"


class FavoriteStatesResponse(BaseModel):
    favorite_asset_ids: list[int]
    favorites_revision: int


class FavoriteWriteResponse(BaseModel):
    asset_id: int
    is_favorite: bool
    changed: bool
    favorites_revision: int


class FavoriteBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_ids: list[StrictInt] = Field(min_length=1, max_length=500)
    is_favorite: StrictBool

    @field_validator("asset_ids")
    @classmethod
    def valid_asset_ids(cls, value: list[int]) -> list[int]:
        if any(asset_id <= 0 for asset_id in value) or len(set(value)) != len(value):
            raise ValueError("asset_ids must be distinct positive integers")
        return value


class FavoriteBatchResponse(BaseModel):
    is_favorite: bool
    requested: int
    changed: int
    unchanged: int
    favorites_revision: int


class PhotoCollectionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr = Field(min_length=1, max_length=80)
    create_request_id: StrictStr = Field(min_length=1, max_length=128)
    asset_ids: list[StrictInt] | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("asset_ids")
    @classmethod
    def valid_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and (any(asset_id <= 0 for asset_id in value) or len(set(value)) != len(value)):
            raise ValueError("asset_ids must be distinct positive integers")
        return value


class PhotoCollectionRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_token: StrictStr = Field(min_length=1, max_length=128)
    expected_revision: StrictInt = Field(ge=1)
    name: StrictStr = Field(min_length=1, max_length=80)


class PhotoCollectionDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_token: StrictStr = Field(min_length=1, max_length=128)
    expected_revision: StrictInt = Field(ge=1)


class PhotoCollectionMembersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["add", "remove"]
    asset_ids: list[StrictInt] = Field(min_length=1, max_length=500)
    instance_token: StrictStr = Field(min_length=1, max_length=128)
    expected_revision: StrictInt = Field(ge=1)

    @field_validator("asset_ids")
    @classmethod
    def valid_ids(cls, value: list[int]) -> list[int]:
        if any(asset_id <= 0 for asset_id in value) or len(set(value)) != len(value):
            raise ValueError("asset_ids must be distinct positive integers")
        return value


class PhotoCollectionFromSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr = Field(min_length=1, max_length=80)
    create_request_id: StrictStr = Field(min_length=1, max_length=128)
    source_token: StrictStr = Field(min_length=16, max_length=256)
    mode: Literal["ranked_candidates", "all_filter_matches"]
    confirm_truncated: StrictBool = False


class PhotoCollectionAppendFromSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: StrictStr = Field(min_length=1, max_length=128)
    source_token: StrictStr = Field(min_length=16, max_length=256)
    mode: Literal["ranked_candidates", "all_filter_matches"]
    confirm_truncated: StrictBool = False
    instance_token: StrictStr = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)


class PhotoSmartCollectionFromSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr = Field(min_length=1, max_length=80)
    create_request_id: StrictStr = Field(min_length=1, max_length=128)
    source_token: StrictStr = Field(min_length=16, max_length=256)


class PhotoSmartRuleReplaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_token: StrictStr = Field(min_length=1, max_length=128)
    expected_revision: StrictInt = Field(ge=1)
    request_id: StrictStr = Field(min_length=1, max_length=128)
    source_token: StrictStr = Field(min_length=16, max_length=256)


class PhotoSmartCollectionRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_token: StrictStr = Field(min_length=1, max_length=128)
    expected_revision: StrictInt = Field(ge=1)
    name: StrictStr = Field(min_length=1, max_length=80)


class PhotoSmartCollectionDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_token: StrictStr = Field(min_length=1, max_length=128)
    expected_revision: StrictInt = Field(ge=1)


class PhotoSmartSaveStaticRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_token: StrictStr = Field(min_length=1, max_length=128)
    expected_revision: StrictInt = Field(ge=1)
    name: StrictStr = Field(min_length=1, max_length=80)
    create_request_id: StrictStr = Field(min_length=1, max_length=128)
    order: Literal["rank", "capture_desc"] = "rank"
    confirm_stale: StrictBool = False
    confirm_truncated: StrictBool = False
    confirm_indexing: StrictBool = False


class PhotoSmartExclusionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_token: StrictStr = Field(min_length=1, max_length=128)
    expected_revision: StrictInt = Field(ge=1)


class PhotoSmartRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_token: StrictStr = Field(min_length=1, max_length=128)
    manual: StrictBool = False


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


class LibraryRefreshRequest(BaseModel):
    """``POST /api/v1/library/refresh``: refresh every source, or only the named ones.

    An omitted/empty body means "every registered source" - the one-click case. The list is
    only useful for a user who knows which directory changed (P4-02Y §5).

    ``mode`` is ``refresh`` (the daily incremental pass of P4-02Y) or ``build`` (P4-02Y-I
    §1/§2): the first source build, which keeps draining the *durable* backlog of the named
    sources in bounded batches until their semantic index is complete instead of stopping at
    one bounded delta.
    """

    model_config = ConfigDict(extra="forbid")

    source_ids: list[int] | None = Field(default=None, max_length=64)
    mode: Literal["refresh", "build"] = "refresh"
    face_profile: Literal["source_detail", "lightweight"] | None = None


class LibraryRefreshStatus(BaseModel):
    """The refresh status; counts and stage names only, never an asset-id list (P4-02Y §4).

    ``SEARCH_READY`` and ``COMPLETE`` are distinct on purpose: the first means this round's
    new content is already reachable through the current search path, the second that the
    available enrichment stages also finished. ``search_ready_basis`` names the evidence the
    first one rests on.
    """

    refresh_id: str | None = None
    state: str
    accepted: bool = False
    running: bool = False
    #: Scoped builds waiting behind the active run, in FIFO order (process-local).
    queued: bool = False
    pending_builds: list[list[int]] = Field(default_factory=list)
    #: Whether one all-sources build is waiting in this process's FIFO.
    pending_all_build: bool = False
    #: ``refresh`` (daily incremental) or ``build`` (P4-02Y-I first build); ``null`` while the
    #: process has not started any refresh yet.
    mode: str | None = None
    face_profile: Literal["source_detail", "lightweight"] | None = None
    search_ready: bool = False
    search_ready_basis: str | None = None
    source_ids: list[int] | None = None
    batch_size: int
    started_at_ms: int | None = None
    updated_at_ms: int | None = None
    finished_at_ms: int | None = None
    stages: dict[str, Any] = Field(default_factory=dict)
    failed_stages: list[str] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    counters: dict[str, Any] = Field(default_factory=dict)
    errors: list[dict[str, Any]] = Field(default_factory=list)


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


# --------------------------------------------------------------------------------- places


class PlaceAliasInput(BaseModel):
    """One alias of a place: the display text plus an optional language tag."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200)
    language: str | None = Field(default=None, max_length=32)


class PlaceCreateRequest(BaseModel):
    """Create a manual city/attraction/custom place (a country comes from a dataset)."""

    model_config = ConfigDict(extra="forbid")

    kind: PlaceKind
    canonical_name: str = Field(min_length=1, max_length=200)
    parent_place_id: int | None = Field(default=None, ge=1)
    aliases: list[PlaceAliasInput] = Field(default_factory=list)


class PlacePatchRequest(BaseModel):
    """PATCH v1 opens rename and aliases only.

    ``parent_place_id`` exists in the schema purely so a re-parent attempt is answered with
    an explicit refusal instead of a generic "unknown field" validation error.
    """

    model_config = ConfigDict(extra="forbid")

    canonical_name: str | None = Field(default=None, min_length=1, max_length=200)
    aliases: list[PlaceAliasInput] | None = None
    parent_place_id: int | None = None
    expected_revision: int = Field(ge=1)


class PlaceRecord(BaseModel):
    """The frozen ``Place`` projection (ADR-0005 §2)."""

    contract_version: str
    place_id: int
    kind: str
    canonical_name: str
    aliases: list[dict[str, Any]]
    parent_place_id: int | None = None
    origin: str
    external_refs: list[dict[str, Any]] = Field(default_factory=list)
    lifecycle: str
    revision: int
    created_at: int
    updated_at: int


class PlaceListResponse(BaseModel):
    places: list[PlaceRecord]
    limit: int
    next_cursor: str | None = None
    has_more: bool
    catalog_filter_revision: int


class PlaceCoverageResponse(BaseModel):
    contract_version: str
    assets_total: int
    gps_extracted: int
    gps_valid: int
    gps_absent: int
    gps_invalid: int
    resolver_available: bool
    place_resolved: int
    places_assigned: int
    places_manual_unassigned: int
    places_suggested: int
    places_unclassified: int
    catalog_filter_revision: int
    suggestion_revision: int


class GeoFactResponse(BaseModel):
    """Exact coordinates. Only the session-protected per-asset ``/geo`` endpoint serves it."""

    contract_version: str
    geo_fact_id: str
    asset_id: int
    location_id: int | None = None
    input_revision: str
    fact_revision: int
    status: str
    source: str
    processor_id: str
    raw_fields_checksum_sha256: str
    raw_gps: dict[str, Any] | None = None
    normalized_wgs84: dict[str, Any] | None = None
    datum_status: str
    privacy: dict[str, bool]
    created_at: int


class PlaceSummary(BaseModel):
    """The effective place of one asset, with the full chain needed to disambiguate."""

    place_id: int
    kind: str
    canonical_name: str
    lifecycle: str
    ancestors: list[dict[str, Any]] = Field(default_factory=list)
    source: str | None = None
    source_id: str | None = None
    dataset_id: str | None = None


class AssetPlaceResponse(BaseModel):
    contract_version: str
    asset_id: int
    assignment: dict[str, Any] | None = None
    place: PlaceSummary | None = None
    exclusions: list[dict[str, Any]] = Field(default_factory=list)
    suggested: dict[str, Any]
    catalog_filter_revision: int
    suggestion_revision: int
    gps_state: str


class PlaceAssignmentRequest(BaseModel):
    """``PUT /assets/{id}/place-assignment``: one action with an idempotency key."""

    model_config = ConfigDict(extra="forbid")

    action: PlaceAssignmentAction
    target_place_id: int | None = Field(default=None, ge=1)
    target_place_ref: str | None = Field(default=None, min_length=5, max_length=512)
    request_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=0)
    #: P2-03B: an ASSIGN that confirms a machine suggestion may record that origin.
    origin: Literal["CONFIRMED_SUGGESTION"] | None = None


class PlaceDatasetCompability(BaseModel):
    minimum: int
    maximum: int


class PlaceDatasetRecord(BaseModel):
    """One imported name-dataset package. Contains no local path."""

    contract_version: str
    dataset_id: str
    package_id: str
    package_version: str
    provider: str
    license_id: str
    attribution: str
    state: str
    coverage: dict[str, Any]
    compatible_catalog_schema: PlaceDatasetCompability
    record_count: int
    file_count: int
    previous_dataset_id: str | None = None
    generated_at: int
    validated_at: int | None = None
    activated_at: int | None = None
    retained_at: int | None = None
    problems: list[dict[str, Any]] = Field(default_factory=list)


class PlaceDatasetStateResponse(BaseModel):
    """``GET /places/datasets``: the real package state, never a fabricated ACTIVE one."""

    contract_version: str
    state: str
    reason: str | None = None
    resolver_available: bool
    active_dataset: PlaceDatasetRecord | None = None
    datasets: list[PlaceDatasetRecord]
    suggestion_revision: int


class PlaceBatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: StrictInt = Field(ge=1)
    action: PlaceAssignmentAction
    target_place_id: int | None = Field(default=None, ge=1)
    target_place_ref: str | None = Field(default=None, min_length=5, max_length=512)
    expected_revision: int = Field(ge=0)


class PlaceBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=64)
    items: list[PlaceBatchItem]


class PlaceUndoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=64)
    expected_operation_revision: int = Field(ge=1)


class PlaceOperationItemResponse(BaseModel):
    asset_id: int | None = None
    action: str
    assignment: dict[str, Any] | None = None
    changed: bool
    cleared_exclusions: list[int] = Field(default_factory=list)


class PlaceOperationResponse(BaseModel):
    """The result of one single, batch or undo operation."""

    operation_id: str
    operation_type: str
    operation_revision: int
    status: str
    undo_of_operation_id: str | None = None
    changed: bool
    catalog_filter_revision: int
    replayed: bool
    items: list[PlaceOperationItemResponse]


class PlaceOperationRecord(BaseModel):
    contract_version: str
    operation_id: str
    request_id: str
    payload_sha256: str
    operation_type: str
    operation_revision: int
    actor: str
    atomic: bool
    status: str
    undo_of_operation_id: str | None = None
    catalog_filter_revision: int
    created_at: int
    committed_at: int


class PlaceOperationListResponse(BaseModel):
    operations: list[PlaceOperationRecord]
    limit: int
    next_cursor: str | None = None
    has_more: bool


# -------------------------------------------------------------------------------- persons


class PersonAliasRecord(BaseModel):
    """One search alias of a named person; the alias is never a merge key."""

    alias: str
    normalized_alias: str


class PersonRecord(BaseModel):
    """The canonical person behind one id (ADR-0006 §5).

    An old id is answered with its canonical target instead of an HTTP redirect, and the
    counts are the effective members: no face under a ``MANUAL_UNASSIGNED`` lock and no face
    an active human denial forbids. No name, alias, box, landmark, vector or similarity of
    an individual pair travels through this model.
    """

    contract_version: str
    requested_person_id: int
    canonical_person_id: int
    redirected: bool
    person_id: int
    name: str | None = None
    avatar_face_id: str | None = None
    revision: int
    aliases: list[PersonAliasRecord] = Field(default_factory=list)
    face_count: int
    asset_count: int
    created_at: int
    updated_at: int


class PersonNameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=64)
    name: str
    expected_revision: int = Field(ge=1)


class PersonCoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    face_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_revision: int = Field(ge=1, strict=True)


class PersonMergeSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: int = Field(ge=1)
    expected_revision: int = Field(ge=1)


class PersonMergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=64)
    target_person_id: int = Field(ge=1)
    target_expected_revision: int = Field(ge=1)
    #: The bound is enforced by the service (``PERSON_BATCH_TOO_LARGE``), exactly like the
    #: place batch: a transport-level ``max_length`` would replace the frozen code with a
    #: generic validation error and hide which limit was hit.
    sources: list[PersonMergeSource] = Field(default_factory=list)


class PersonVisibilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_ids: list[StrictInt] = Field(min_length=1, max_length=500)
    hidden: StrictBool
    ignore_state: Literal["TEMPORARY", "EXCLUDED"] = "TEMPORARY"

    @field_validator("person_ids")
    @classmethod
    def valid_person_ids(cls, value: list[int]) -> list[int]:
        if any(person_id <= 0 for person_id in value) or len(set(value)) != len(value):
            raise ValueError("person_ids must be distinct positive integers")
        return value


class PersonVisibilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hidden: bool
    ignore_state: Literal["TEMPORARY", "EXCLUDED"]
    person_count: int
    changed: int


class PersonCollectionNameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr


class PersonCollectionMembersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["add", "remove"]
    person_ids: list[StrictInt] = Field(min_length=1, max_length=500)
    hide_after_add: StrictBool = False

    @field_validator("person_ids")
    @classmethod
    def valid_collection_person_ids(cls, value: list[int]) -> list[int]:
        if any(person_id <= 0 for person_id in value) or len(set(value)) != len(value):
            raise ValueError("person_ids must be distinct positive integers")
        return value


class PersonCollectionFaceMembersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["add", "remove"]
    face_ids: list[StrictStr] = Field(min_length=1, max_length=500)
    expected_revision: StrictInt = Field(ge=1)
    hide_after_add: StrictBool = False

    @field_validator("face_ids")
    @classmethod
    def valid_collection_face_ids(cls, value: list[str]) -> list[str]:
        if any(len(face_id) != 64 or any(char not in "0123456789abcdef" for char in face_id) for face_id in value):
            raise ValueError("face_ids must be 64-character lowercase hexadecimal strings")
        if len(set(value)) != len(value):
            raise ValueError("face_ids must be distinct")
        return value


class PersonUndoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=64)
    expected_operation_revision: int = Field(ge=1)


class PersonSplitFaceSelection(BaseModel):
    """One face of ``POST /api/v1/persons/{source_person_id}/split`` (ADR-0008 §2).

    Both revisions are part of the request on purpose: the logical control revision proves
    the human control did not move, and the effective link revision proves the membership
    the caller saw is still the current one.
    """

    model_config = ConfigDict(extra="forbid")

    face_id: str
    expected_control_revision: int = Field(ge=0)
    expected_assignment_revision: int = Field(ge=1)


class PersonSplitRequest(BaseModel):
    """``POST /api/v1/persons/{source_person_id}/split``: 1..500 explicit faces."""

    model_config = ConfigDict(extra="forbid")

    contract_version: str
    request_id: str = Field(min_length=1, max_length=64)
    source_expected_revision: int = Field(ge=1)
    #: The 1..500 bound, the empty refusal and the duplicate refusal belong to the service,
    #: so the frozen ``PERSON_BATCH_TOO_LARGE``/``VALIDATION_ERROR`` codes are what a caller
    #: sees instead of a transport-level shape error. The list itself is required, exactly
    #: like the companion: omitting it is a caller-shape error, not an empty selection.
    faces: list[PersonSplitFaceSelection]


class PersonOperationItemResponse(BaseModel):
    entity_type: str
    #: A ``PERSON_BUNDLE`` item names an integer person; a ``FACE_CONTROL`` item of
    #: ADR-0007 names a ``face_id`` instead. The union of the two responses below keeps
    #: each item shape exact, so this model is only ever populated by one of them.
    entity_id: int | None = None
    changed: bool
    merged_into: int | None = None
    revision: int


class PersonOperationResponse(BaseModel):
    """The result of one rename, merge, batch merge or undo."""

    operation_id: str
    operation_type: str
    operation_revision: int
    status: str
    undo_of_operation_id: str | None = None
    changed: bool
    catalog_filter_revision: int
    replayed: bool
    items: list[PersonOperationItemResponse]


class PersonSummary(BaseModel):
    """One card of ``GET /api/v1/persons`` (P3-06).

    Only the fields a person card paints are part of it: identity, revision and the two
    *effective* counts. ``name_key``, the alias spelling, a face box and every vector stay
    inside the catalog. ``thumbnail_url`` is a relative controlled API path, never a local
    path, and it is ``None`` for a person without an avatar face.
    """

    model_config = ConfigDict(extra="forbid")

    person_id: int
    name: str | None = None
    revision: int
    avatar_face_id: str | None = None
    face_count: int
    asset_count: int
    thumbnail_url: str | None = None


class PersonListResponse(BaseModel):
    """A bounded page of active canonical persons in stable ``person_id`` order."""

    model_config = ConfigDict(extra="forbid")

    persons: list[PersonSummary]
    limit: int
    next_cursor: str | None = None
    has_more: bool
    kind: Literal["all", "named", "unnamed"]


class FaceSummary(BaseModel):
    """One row of ``GET /api/v1/faces``: the page's own view of one face.

    The control projection mirrors ``GET /api/v1/faces/{face_id}/assignment``: a face
    without a control row is ``AUTO_ALLOWED`` at revision 0. No box, landmark, embedding,
    candidate score, similarity or path is part of this model.
    """

    model_config = ConfigDict(extra="forbid")

    face_id: str
    asset_id: int
    mode: Literal["AUTO_ALLOWED", "MANUAL_ASSIGNED", "MANUAL_UNASSIGNED"]
    control_revision: int
    assigned_person_id: int | None = None
    assignment_revision: int | None = None
    ignore_state: Literal["TEMPORARY", "EXCLUDED"] | None = None
    suggestions_available: bool
    thumbnail_url: str


class FaceListResponse(BaseModel):
    """One of the two mutually exclusive face views: a person's members or the manual queue."""

    model_config = ConfigDict(extra="forbid")

    faces: list[FaceSummary]
    limit: int
    next_cursor: str | None = None
    has_more: bool
    view: Literal["person", "manual_unassigned"]
    requested_person_id: int | None = None
    canonical_person_id: int | None = None
    redirected: bool = False
    #: The canonical person's current revision, so a page can submit a rename without
    #: inventing one. It is ``None`` for the manual-unassigned queue.
    person_revision: int | None = None


class PersonOperationSummary(BaseModel):
    """One line of ``GET /api/v1/person-operations``.

    The log itself is the authority for idempotency and history, but a page only needs the
    identity, the status and *which ids* an operation touched. ``request_id``,
    ``payload_sha256``, the before/after snapshots, names, aliases and face geometry are
    deliberately absent. ``expired`` is server-owned; the compensation endpoint remains
    authoritative for the full conflict matrix.
    """

    model_config = ConfigDict(extra="forbid")

    operation_id: str
    operation_type: str
    operation_revision: int
    status: Literal["COMMITTED", "UNDONE"]
    undo_of_operation_id: str | None = None
    undone_by_operation_id: str | None = None
    target_person_id: int | None = None
    changed: bool
    item_count: int
    person_ids: list[int] = Field(default_factory=list)
    face_ids: list[str] = Field(default_factory=list)
    catalog_filter_revision: int
    created_at: int
    committed_at: int
    expired: bool = False


class PersonOperationListResponse(BaseModel):
    """A bounded page of the append-only person operation log, newest first."""

    model_config = ConfigDict(extra="forbid")

    operations: list[PersonOperationSummary]
    limit: int
    next_cursor: str | None = None
    has_more: bool


# ---------------------------------------------------------------------------------- faces


class FaceExclusionChanges(BaseModel):
    """The person ids one face operation activated and lifted (ADR-0007 §6)."""

    model_config = ConfigDict(extra="forbid")

    activated: list[int] = Field(default_factory=list)
    deactivated: list[int] = Field(default_factory=list)


class FaceControlState(BaseModel):
    """``GET /api/v1/faces/{face_id}/assignment``: mode, revision, person, denials.

    No similarity, no box, no landmark, no vector and no path is part of this projection.
    """

    contract_version: str
    face_id: str
    mode: str
    revision: int
    assigned_person_id: int | None = None
    active_exclusions: list[int] = Field(default_factory=list)
    suggestions_available: bool


class MappingCandidate(BaseModel):
    """One candidate of a review group (ADR-0010 §8.1; no geometry, ever)."""

    model_config = ConfigDict(extra="forbid")

    new_face_id: str
    new_spec_revision: str
    match_state: Literal["HIGH", "WEAK"]
    quality_state: Literal["OK", "LOW"]
    state: Literal["SUGGESTED", "CONFIRMED", "REJECTED", "STALE"]
    revision: int
    thumbnail_url: str


class MappingGroupSummary(BaseModel):
    """One row of the ``GET /api/v1/face-mappings`` review list."""

    model_config = ConfigDict(extra="forbid")

    contract_version: str
    old_face_id: str
    asset_id: int
    old_spec_revision: str
    candidate_state: Literal["NO_CANDIDATE", "UNIQUE_HIGH", "UNIQUE_WEAK", "AMBIGUOUS"]
    state: Literal["OPEN", "CONFIRMED", "REJECTED"]
    revision: int
    control_mode: Literal["AUTO_ALLOWED", "MANUAL_ASSIGNED", "MANUAL_UNASSIGNED"]
    control_revision: int
    assigned_person_id: int | None = None
    candidate_count: int
    thumbnail_url: str


class MappingGroup(MappingGroupSummary):
    """One review group with its live candidate array."""

    candidates: list[MappingCandidate] = Field(default_factory=list)


class MappingListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str
    limit: int
    next_cursor: str | None = None
    has_more: bool
    groups: list[MappingGroupSummary] = Field(default_factory=list)


class AssetFaceReviewSummary(MappingGroupSummary):
    """Add only the old face's exclusion presence on the new photo-scoped read."""

    has_active_exclusions: bool


class AssetFaceReviewResponse(MappingListResponse):
    groups: list[AssetFaceReviewSummary] = Field(default_factory=list)


class MappingOperationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_face_id: str
    state: Literal["SUGGESTED", "CONFIRMED", "REJECTED", "STALE"]
    revision: int
    inherits_person: bool


class MappingGroupItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: Literal["FACE_MAPPING_GROUP"]
    old_face_id: str
    changed: bool
    state: Literal["OPEN", "CONFIRMED", "REJECTED"]
    revision: int
    candidate_state: Literal["NO_CANDIDATE", "UNIQUE_HIGH", "UNIQUE_WEAK", "AMBIGUOUS"]
    candidates: list[MappingOperationCandidate] = Field(default_factory=list)


class FaceRematchRequest(BaseModel):
    """A user-requested batch over stored face vectors, without detection."""

    model_config = ConfigDict(extra="forbid")

    limit: StrictInt = Field(default=20, ge=1, le=50)
    after_asset_id: StrictInt = Field(default=0, ge=0)


class FaceInboxVisibilityRequest(BaseModel):
    """Hide or restore 1..500 explicit detected faces in the personal inbox."""

    model_config = ConfigDict(extra="forbid")

    face_ids: list[StrictStr] = Field(min_length=1, max_length=500)
    hidden: StrictBool
    ignore_state: Literal["TEMPORARY", "EXCLUDED"] = "TEMPORARY"

    @field_validator("face_ids")
    @classmethod
    def valid_face_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any(
            len(face_id) != 64 or any(char not in "0123456789abcdef" for char in face_id)
            for face_id in value
        ):
            raise ValueError("face_ids must be distinct lowercase 64-character face ids")
        return value


class FaceInboxVisibilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hidden: bool
    ignore_state: Literal["TEMPORARY", "EXCLUDED"] = "TEMPORARY"
    face_count: int
    changed: int


class FaceDetailBatchRequest(BaseModel):
    """Start or reuse one durable source-detail batch over eligible detected faces."""

    model_config = ConfigDict(extra="forbid")

    source_id: StrictInt | None = Field(default=None, ge=1)


class FaceCorrectionRequest(BaseModel):
    """``PUT /api/v1/faces/{face_id}/assignment``: one face, one action, one revision."""

    model_config = ConfigDict(extra="forbid")

    contract_version: str
    request_id: str = Field(min_length=1, max_length=64)
    action: str
    #: Required by ``assign`` and refused otherwise; the service owns that rule so the
    #: frozen ``VALIDATION_ERROR`` is answered instead of a transport-level shape error.
    target_person_id: int | None = None
    expected_revision: int = Field(ge=0)


class CreatePersonFromFaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str
    request_id: str = Field(min_length=1, max_length=64)
    expected_control_revision: int = Field(ge=0)


class FaceCorrectionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    face_id: str
    action: str
    target_person_id: int | None = None
    expected_revision: int = Field(ge=0)


class FaceRegroupItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    face_id: str
    expected_control_revision: int = Field(ge=0)
    expected_assignment_revision: int = Field(ge=1)


class FaceRegroupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: str
    request_id: str = Field(min_length=1, max_length=64)
    source_expected_revision: int = Field(ge=1)
    faces: list[FaceRegroupItem]


class FaceCorrectionBatchRequest(BaseModel):
    """``POST /api/v1/face-assignments/batch``: 1..500 explicit face items."""

    model_config = ConfigDict(extra="forbid")

    contract_version: str
    request_id: str = Field(min_length=1, max_length=64)
    #: The 500 bound and the duplicate refusal are the service's, so the frozen
    #: ``FACE_BATCH_TOO_LARGE``/``VALIDATION_ERROR`` codes are what a caller sees.
    items: list[FaceCorrectionItem] = Field(default_factory=list)


class FaceOperationItemResponse(BaseModel):
    """One ``FACE_CONTROL`` item of a face operation answer (ADR-0007 §6)."""

    model_config = ConfigDict(extra="forbid")

    entity_type: str
    face_id: str
    changed: bool
    mode: str
    revision: int
    assigned_person_id: int | None = None
    exclusions: FaceExclusionChanges


class FaceOperationResponse(BaseModel):
    """The result of one face correction or of the compensation of one."""

    contract_version: str
    operation_id: str
    operation_type: str
    operation_revision: int
    status: str
    undo_of_operation_id: str | None = None
    changed: bool
    catalog_filter_revision: int
    replayed: bool
    items: list[FaceOperationItemResponse]


class FaceRegroupResponse(FaceOperationResponse):
    grouped_faces: int
    new_persons: int
    remaining_faces: int


class CreatePersonFromFaceResponse(FaceOperationResponse):
    new_person_id: int
    face_id: str


class MappingOperationResponse(BaseModel):
    """The ADR-0010 §5 response: group items plus the transferred face-control items.

    The mapping family shares the compensation endpoint and the envelope fields of the other
    families; the item list keeps the two frozen shapes it may carry (``FACE_MAPPING_GROUP``
    and the ADR-0007 ``FACE_CONTROL``), so an undo answer can be read by the same page.
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: str
    operation_id: str
    operation_type: str
    operation_revision: int
    status: str
    undo_of_operation_id: str | None = None
    changed: bool
    catalog_filter_revision: int
    replayed: bool
    items: list[MappingGroupItemResponse | FaceOperationItemResponse] = Field(
        default_factory=list
    )


class SplitGroupItemResponse(BaseModel):
    """One ``SPLIT_GROUP`` item of a split answer (ADR-0008 §6).

    It names a person id, its role in the transaction and - after a compensation - where a
    created group now points. No name, alias, member list or similarity is part of it.
    """

    model_config = ConfigDict(extra="forbid")

    entity_type: str
    entity_id: int
    role: str
    changed: bool
    revision: int
    merged_into: int | None = None


class PersonSplitOperationResponse(BaseModel):
    """The result of one split or of the compensation of one (``p3-person-split-contracts.v1``).

    ``items`` mixes exactly two group items (source, then created) with 1..500 ``FACE_CONTROL``
    items sorted by ``face_id``; the union below keeps each shape exact so a group item can
    never be read as a face control or the other way round.
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: str
    operation_id: str
    operation_type: str
    operation_revision: int
    status: str
    undo_of_operation_id: str | None = None
    requested_source_person_id: int
    canonical_source_person_id: int
    new_person_id: int
    changed: bool
    catalog_filter_revision: int
    replayed: bool
    items: list[SplitGroupItemResponse | FaceOperationItemResponse]


# --------------------------------------------------------------------------------- search


class SearchRequestModel(BaseModel):
    """``POST /api/v1/search``: the frozen P2-06A request envelope.

    The field names and bounds mirror ``search.contracts.SearchRequest`` exactly; the
    service converts this validated body with ``SearchRequest.from_envelope``, so the
    protocol is enforced once more at the transport-free layer.

    ``person_ids`` (P3-07B, ADR-0009 §2) is deliberately the one list without a transport
    bound: an over-long list has to be answered with the frozen ``INVALID_PERSON_FILTER``
    (which names ``max_person_ids``), not with a generic pydantic refusal, so the count is
    checked by the envelope. ``StrictInt`` still refuses ``true``/``1.0``/``"3"``/
    ``null`` here, and a repeated id is refused by the envelope as well.
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["p2-contracts.v1"] = "p2-contracts.v1"
    request_id: str = Field(min_length=1, max_length=64)
    text: str | None = Field(default=None, max_length=256)
    retrieval_mode: Literal["combined", "text"] = "combined"
    profile_id: str | None = Field(default=None, min_length=1, max_length=128)
    space_id: str | None = Field(default=None, min_length=1, max_length=128)
    image_generation_id: str | None = Field(default=None, min_length=1, max_length=128)
    dimension: int | None = Field(default=None, ge=1)
    source_id: int | None = Field(default=None, ge=1)
    availability: AvailabilityFilter = "any"
    place_ids: list[int] = Field(default_factory=list)
    include_descendants: bool = True
    place_state: PlaceStateFilter = "any"
    person_ids: list[StrictInt] = Field(default_factory=list)
    person_match: PersonMatchFilter = "any"
    #: ADR-0011 §6: the optional capture-time range. Both bounds are wall-clock spellings
    #: (``YYYY-MM-DD`` / ``YYYY-MM-DDTHH:MM:SS[.f{1,3}]``); a timezone suffix is refused by
    #: the envelope with the frozen ``INVALID_TIME_RANGE``, because a capture time has no
    #: single timezone and accepting one would pretend the server converted it.
    capture_from: str | None = Field(default=None, max_length=32)
    capture_to_exclusive: str | None = Field(default=None, max_length=32)
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    #: P2-06B: ask for the optional candidate rerank. Additive and off by default.
    rerank: bool = False
    search_session_id: str | None = Field(default=None, min_length=1, max_length=128)
    cursor: str | None = Field(default=None, min_length=1, max_length=512)


class PlaceAwareSearchRequestModel(SearchRequestModel):
    """Additive personal-search envelope; the frozen ``/search`` body stays unchanged."""

    original_query: str = Field(max_length=256)
    content_query: str | None = Field(default=None, max_length=256)
    place_kind: Literal["city", "province", "country", "district"] | None = None
    place_key: str | None = Field(default=None, max_length=128)
    place_cancelled: bool = False
    intent_plan: dict[str, Any] | None = None
    #: Identity-only handoff from a selected album title. The API resolves the current
    #: members and revisions; clients never send asset IDs as the authority.
    search_scope: dict[str, Any] | None = None
    favorites_only: StrictBool = False


class SearchHitModel(BaseModel):
    """One row of one page: identity, rank provenance and why it matched."""

    asset_id: int
    rank: int
    score: float | None = None
    matched_by: dict[str, int]
    hit_reason: str
    matched_text: list[str] = Field(default_factory=list)
    semantic_ready: bool = False


class SearchResultModel(BaseModel):
    """``POST /api/v1/search`` response: one page bound to session, profile and revisions."""

    contract_version: str
    request_id: str
    retrieval_mode: Literal["combined", "text"] = "combined"
    search_session_id: str
    profile_id: str | None = None
    image_generation_id: str | None = None
    space_id: str | None = None
    dimension: int | None = None
    catalog_filter_revision: int
    suggestion_revision: int
    fusion_config: dict[str, Any]
    query: dict[str, Any] | None = None
    filters: dict[str, Any]
    hits: list[SearchHitModel]
    page_size: int
    page_offset: int
    next_cursor: str | None = None
    has_more: bool
    total_candidates: int
    candidates_truncated: bool
    channels: dict[str, Any]
    pending_semantic: dict[str, Any] | None = None
    search_projection: dict[str, Any]
    #: P4-00B: the honesty block of a capture-conditioned query. It is a sibling of the frozen
    #: ``search_projection`` block and appears only when the request carried a capture bound.
    taken_time: TakenTimeBlock | None = None
    #: P2-06B: the candidate-score block, kept apart from ``hits[].score`` on purpose.
    rerank_state: Literal["NOT_REQUESTED", "OK", "UNAVAILABLE", "FAILED"]
    reranked: dict[str, Any] | None = None
    state: str


class ImageQueryLimitsModel(BaseModel):
    """The hard bounds this build enforces on one uploaded image (architecture §10.3)."""

    max_bytes: int
    max_pixels: int
    decode_budget_ms: int
    decode_long_edge: int


class ImageQuerySummaryModel(BaseModel):
    """What the accepted upload actually was: measured facts, never a local path.

    ``digest`` is the SHA-256 of the uploaded bytes and is what a continuation is bound to;
    ``decode_ms`` is the measured decode time so a caller can see how close the input came to
    the budget instead of having to guess.
    """

    byte_size: int
    width: int
    height: int
    format: str
    digest: str
    decode_ms: int
    limits: ImageQueryLimitsModel


class ImageSearchResultModel(SearchResultModel):
    """``POST /api/v1/search/image``: the frozen page envelope plus the upload's own facts.

    The image endpoint answers with the *same* page shape ``POST /search`` uses - same
    session, binding, revisions, channels and hits - and adds one block describing the query
    image. ``SearchRequest``/``SearchResult`` themselves are untouched and stay frozen.
    """

    image_query: ImageQuerySummaryModel


class SimilarAssetSearchRequestModel(BaseModel):
    """A search whose query vector comes from one confirmed catalog artifact."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["p2-contracts.v1"] = "p2-contracts.v1"
    request_id: str = Field(min_length=1, max_length=64)
    asset_id: int = Field(ge=1, le=9_223_372_036_854_775_807)
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    search_session_id: str | None = Field(default=None, min_length=1, max_length=128)
    cursor: str | None = Field(default=None, min_length=1, max_length=512)


class SimilarAssetSearchSessionValidationRequestModel(BaseModel):
    """Read-only validation for a frozen asset-image result snapshot."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["p2-contracts.v1"] = "p2-contracts.v1"
    request_id: str = Field(min_length=1, max_length=64)
    asset_id: int = Field(ge=1, le=9_223_372_036_854_775_807)
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    search_session_id: str = Field(min_length=1, max_length=128)


class AssetQuerySummaryModel(BaseModel):
    asset_id: int
    filename: str
    thumbnail_url: str | None = None


class SimilarAssetSearchResultModel(SearchResultModel):
    """The ordinary frozen search page plus the catalog asset used as its query."""

    asset_query: AssetQuerySummaryModel


class AssetQuerySessionValidationModel(BaseModel):
    valid: Literal[True]
    asset_id: int
    search_session_id: str


class SearchProfileActivateRequest(BaseModel):
    """``POST /api/v1/search-profile/activate``: publish one immutable profile."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(min_length=1, max_length=128)


class SearchProfileResponse(BaseModel):
    """``GET /api/v1/search-profile``: the single active pointer and its profile."""

    contract_version: str
    profile_id: str | None = None
    profile: dict[str, Any] | None = None
    updated_at: int


class SearchProfileActivationResponse(BaseModel):
    """The result of activating a profile: idempotent, and honest about the readiness gate."""

    contract_version: str
    profile_id: str
    image_generation_id: str
    space_id: str
    fts_generation_id: str
    updated_at: int
    changed: bool
    state: Literal["ACTIVE", "ALREADY_ACTIVE"]
    readiness: dict[str, Any]


class FeedbackRequestModel(BaseModel):
    """``POST /api/v1/search/feedback``: one explicit relevance verdict, idempotent by key."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["p2-contracts.v1"] = "p2-contracts.v1"
    request_id: str = Field(min_length=1, max_length=64)
    search_session_id: str = Field(min_length=1, max_length=128)
    asset_id: int = Field(ge=1)
    rank: int = Field(ge=1)
    relevance: Literal["RELEVANT", "NOT_RELEVANT"]
    note: str | None = Field(default=None, max_length=MAX_NOTE_CHARS)


class FeedbackRecordModel(BaseModel):
    """One stored verdict: its own fields only (no place, coordinate, path or query text)."""

    feedback_id: int
    request_id: str
    search_session_id: str
    asset_id: int
    rank: int
    relevance: Literal["RELEVANT", "NOT_RELEVANT"]
    note: str | None = None
    created_at: int


class FeedbackResponseModel(BaseModel):
    """``201`` for a new verdict, ``200`` when an identical ``request_id`` replays it."""

    contract_version: str
    replayed: bool
    feedback: FeedbackRecordModel


class FeedbackListResponseModel(BaseModel):
    """``GET /api/v1/search/feedback``: a read-only keyset page of the audit log."""

    contract_version: str
    feedback: list[FeedbackRecordModel]
    limit: int
    next_cursor: str | None = None
    has_more: bool


class PersonOperationRetentionCleanupRequest(BaseModel):
    """Optional continuation cursor for one bounded expired-snapshot cleanup batch."""

    model_config = ConfigDict(extra="forbid")

    after_operation_revision: int | None = Field(default=None, ge=0)
