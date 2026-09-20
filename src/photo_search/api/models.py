"""Pydantic request/response models for the local business API (architecture §10.2).

The models are the OpenAPI contract P1-05 will consume, so every field the routers return
is declared here and nothing that carries a credential, a lease token or an absolute user
path is part of a response model.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from photo_search.search.contracts import (
    DEFAULT_PAGE_SIZE,
    MAX_NOTE_CHARS,
    MAX_PAGE_SIZE,
)
from photo_search.search.retrieval import DEFAULT_TOP_K, MAX_TOP_K

AvailabilityFilter = Literal["present", "missing", "any"]
ReadyFilter = Literal["true", "false", "any"]
AssetOrder = Literal["asset_id", "time_desc"]
PlaceStateFilter = Literal["any", "assigned", "unassigned", "manual_unassigned", "suggested"]
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
    #: P2-04: present only when ``text`` was used, and honest about projection lag.
    search: SearchProjectionSummary | None = None


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

    asset_id: int = Field(ge=1)
    action: PlaceAssignmentAction
    target_place_id: int | None = Field(default=None, ge=1)
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


class FaceCorrectionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    face_id: str
    action: str
    target_person_id: int | None = None
    expected_revision: int = Field(ge=0)


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
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["p2-contracts.v1"] = "p2-contracts.v1"
    request_id: str = Field(min_length=1, max_length=64)
    text: str | None = Field(default=None, max_length=256)
    profile_id: str | None = Field(default=None, min_length=1, max_length=128)
    space_id: str | None = Field(default=None, min_length=1, max_length=128)
    image_generation_id: str | None = Field(default=None, min_length=1, max_length=128)
    dimension: int | None = Field(default=None, ge=1)
    source_id: int | None = Field(default=None, ge=1)
    availability: AvailabilityFilter = "any"
    place_ids: list[int] = Field(default_factory=list)
    include_descendants: bool = True
    place_state: PlaceStateFilter = "any"
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    #: P2-06B: ask for the optional candidate rerank. Additive and off by default.
    rerank: bool = False
    search_session_id: str | None = Field(default=None, min_length=1, max_length=128)
    cursor: str | None = Field(default=None, min_length=1, max_length=512)


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
