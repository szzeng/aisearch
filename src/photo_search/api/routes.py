"""The read-only business API plus the controlled file endpoints (architecture §10.2).

Every handler is ``async`` and does its catalog work on the event loop thread, which is the
thread the owned SQLite connection was opened on. Blocking work that can be moved off the
loop (reading original bytes) is done in a worker thread; the decode of a single asset
(tens of milliseconds, one asset per request) stays synchronous on purpose so the catalog
connection is never touched from a second thread.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from email.utils import formatdate
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Body, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from photo_search.api.context import ApiContext
from photo_search.api.errors import (
    MEDIA_NOT_READY,
    SESSION_INVALID,
    ApiError,
)
from photo_search.api.files import (
    artifact_etag,
    content_disposition,
    media_type_for,
    original_etag,
    parse_range,
    resolve_optional_location,
    resolve_present_location,
    safe_display_filename,
    stream_file,
)
from photo_search.api.models import (
    AssetDetail,
    AssetListResponse,
    AssetOrder,
    AssetPlaceResponse,
    CapabilitiesResponse,
    FaceControlState,
    FaceCorrectionBatchRequest,
    FaceCorrectionRequest,
    FaceListResponse,
    FaceOperationResponse,
    FeedbackListResponseModel,
    FeedbackRequestModel,
    FeedbackResponseModel,
    GeoFactResponse,
    ImageSearchResultModel,
    JobListResponse,
    JobRecord,
    LibraryRefreshRequest,
    LibraryRefreshStatus,
    MappingGroup,
    MappingListResponse,
    MappingOperationResponse,
    PageCapabilitiesResponse,
    PersonCoverRequest,
    PersonListResponse,
    PersonMergeRequest,
    PersonNameRequest,
    PersonOperationListResponse,
    PersonOperationResponse,
    PersonRecord,
    PersonSplitOperationResponse,
    PersonSplitRequest,
    PersonUndoRequest,
    PlaceAssignmentRequest,
    PlaceAwareSearchRequestModel,
    PlaceBatchRequest,
    PlaceCoverageResponse,
    PlaceCreateRequest,
    PlaceDatasetStateResponse,
    PlaceListResponse,
    PlaceOperationListResponse,
    PlaceOperationResponse,
    PlacePatchRequest,
    PlaceRecord,
    PlaceStateFilter,
    PlaceUndoRequest,
    ScanRequestResponse,
    SchedulerStateResponse,
    SearchProfileActivateRequest,
    SearchProfileActivationResponse,
    SearchProfileResponse,
    SearchRequestModel,
    SearchResultModel,
    SessionResponse,
    SourceCreateRequest,
    SourceDeleteResponse,
    SourceDetail,
    SourceListResponse,
    SourceRenameRequest,
    SourceSummary,
    SourceSummaryResponse,
    StatsResponse,
)
from photo_search.api.queries import (
    asset_detail,
    asset_page,
    clamp_limit,
    job_detail,
    job_page,
    media_state,
)
from photo_search.api.session import (
    COOKIE_PATH,
    COOKIE_SAMESITE,
    CSRF_COOKIE,
    SESSION_COOKIE,
    safe_redirect_target,
)
from photo_search.catalog.errors import (
    CATALOG_FILTER_REVISION_STALE,
    FACE_NOT_FOUND,
    INVALID_CURSOR,
    INVALID_STATE_TRANSITION,
    RUNTIME_CAPABILITY_UNAVAILABLE,
    UPLOAD_TOO_LARGE,
    CatalogError,
)
from photo_search.catalog.migrate import catalog_info
from photo_search.config import catalog_db_path
from photo_search.face.weights import weights_present as face_weights_present
from photo_search.ingestion.service import CatalogService
from photo_search.media.cache import MediaCache
from photo_search.media.contract import (
    FACE_CROP_PROCESSOR_ID,
    PREVIEW,
    PREVIEW_SPEC,
    SPECS,
    THUMBNAIL,
    spec_for,
)
from photo_search.media.face_crop import (
    crop_key,
    parse_bbox,
    probe_original_frame,
    recorded_face_frame,
    render_crop,
)
from photo_search.media.pipeline import MediaInterrupted
from photo_search.media.status import (
    MEDIA_STATE_FAILED,
    MEDIA_STATE_READY,
    MEDIA_STATE_UNSUPPORTED,
)
from photo_search.ocr.weights import weights_present as ocr_weights_present
from photo_search.persons import albums as person_albums
from photo_search.persons.read import (
    clamp_page_limit as clamp_person_limit,
)
from photo_search.persons.read import (
    face_page,
    operation_page,
    person_page,
)
from photo_search.persons.service import PersonService
from photo_search.places import albums as place_albums
from photo_search.places import backfill as place_backfill
from photo_search.places import revision as place_revision
from photo_search.places.contracts import fail as place_fail
from photo_search.places.service import PlaceService, clamp_place_limit
from photo_search.refresh import progress as refresh_progress
from photo_search.scheduler import state as scheduler_state
from photo_search.scheduler.contracts import CONTRACT_VERSION
from photo_search.scheduler.projection import (
    face_summary,
    index_summary,
    ocr_summary,
    scheduler_summary,
    search_profile_summary,
    search_projection_summary,
)
from photo_search.search.contracts import FeedbackRequest, SearchRequest
from photo_search.search.contracts import fail as search_fail
from photo_search.search.profile import activate_profile, profile_projection
from photo_search.search.retrieval import SearchScope, scope_condition
from photo_search.search.service import (
    _person_authority_base,
    _projection_block,
    feedback_limit,
    list_feedback,
    resolve_request_binding,
    search_request,
    submit_feedback,
)
from photo_search.search.upload import (
    DEFAULT_LIMITS,
    UploadedImage,
    encode_image_query,
    image_encoder_of,
    image_query_identity,
    parse_multipart_form,
    stage_upload,
)

APP_VERSION = "0.1.0"

# Local catalog ids can be reused after a rebuild. Revalidate the id-based URL;
# the underlying content-addressed files still need no regeneration.
ARTIFACT_CACHE_CONTROL = "private, no-cache, must-revalidate"
ORIGINAL_CACHE_CONTROL = "private, max-age=0, must-revalidate"

router = APIRouter(prefix="/api/v1", tags=["photo-search"])


def context_of(request: Request) -> ApiContext:
    return request.app.state.api


def service_of(request: Request) -> CatalogService:
    return context_of(request).service()


def cache_of(request: Request) -> MediaCache:
    return service_of(request).media_cache()


def places_of(request: Request) -> PlaceService:
    """One place service per request over the request thread's own catalog connection."""
    return PlaceService(service_of(request).catalog)


def persons_of(request: Request) -> PersonService:
    """One person service per request over the request thread's own catalog connection."""
    return PersonService(service_of(request).catalog)


# ------------------------------------------------------------------------------ health
@router.get("/health", summary="Process-level readiness (no session required)")
async def health() -> dict[str, str]:
    """Unchanged P0-01 behaviour: no models, no catalog, no user data."""
    return {"status": "ok", "service": "photo-search"}


# ----------------------------------------------------------------------------- session
@router.get(
    "/session",
    response_model=SessionResponse,
    summary="Exchange the one-time local credential for a session cookie",
)
async def exchange_session(
    request: Request,
    token: str | None = None,
    redirect_to: str | None = Query(default=None, alias="next"),
) -> Response:
    """Burn the launcher credential and set the HttpOnly session plus CSRF cookies.

    Without ``next`` this is exactly the P1-04 behaviour: ``200`` plus the same JSON body
    and the same two cookies. With a safe same-origin ``next`` the exchange answers
    ``303 See Other`` instead, so the launcher link can drop the user straight on the
    front-end while still setting both cookies on the redirect response.
    """
    target = safe_redirect_target(redirect_to)
    context = context_of(request)
    session = context.sessions.exchange(token)
    if session is None:
        raise ApiError(
            SESSION_INVALID,
            "the session credential is not valid",
            details={"hint": "use the credential printed by the launcher; it is single-use"},
        )
    if target is None:
        response: Response = JSONResponse(
            status_code=200,
            content={
                "status": "ok",
                "csrf_token": session.csrf_token,
                "session_expires": "process",
            },
        )
    else:
        response = RedirectResponse(url=target, status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        session.session_id,
        httponly=True,
        samesite=COOKIE_SAMESITE,
        path=COOKIE_PATH,
    )
    response.set_cookie(
        CSRF_COOKIE,
        session.csrf_token,
        httponly=False,
        samesite=COOKIE_SAMESITE,
        path=COOKIE_PATH,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


# ------------------------------------------------------------------------ capabilities
@router.get(
    "/capabilities/page",
    response_model=PageCapabilitiesResponse,
    summary="Lightweight page startup facts without model initialization",
)
def page_capabilities(request: Request) -> dict[str, Any]:
    """Validate schema using a local read-only connection, without resolving the encoder.

    This sync route runs outside the event loop. It never uses the request-thread catalog
    service: catalog_info opens and closes its own read-only connection in this thread.
    """
    info = catalog_info(None, data_root=context_of(request).settings.data_root)
    error = info.get("error")
    if isinstance(error, dict) and error.get("code"):
        raise CatalogError(
            str(error["code"]),
            str(error.get("message") or "the catalog is not usable"),
            details=error.get("details") or None,
        )
    return {
        "app_version": APP_VERSION,
        "schema_version": info.get("schema_version"),
        "supported_schema_version": info.get("supported_schema_version"),
        "schema_status": str(info.get("status") or "unknown"),
    }


@router.get(
    "/capabilities",
    response_model=CapabilitiesResponse,
    summary="Build facts, media contract and access boundary (no user data)",
)
async def capabilities(request: Request) -> dict[str, Any]:
    """Read-only build facts. Opens the catalog read-only, so it never writes anything."""
    settings = context_of(request).settings
    info = catalog_info(None, data_root=settings.data_root)
    error = info.get("error")
    if isinstance(error, dict) and error.get("code"):
        raise CatalogError(
            str(error["code"]),
            str(error.get("message") or "the catalog is not usable"),
            details=error.get("details") or None,
        )
    # P2-02: the scheduler/runtime health summary is read through its own read-only
    # connection, so a catalog that predates migration 0002 simply reports ``None``.
    summary = scheduler_summary(catalog_db_path(settings.data_root)) or {}
    return {
        "app_version": APP_VERSION,
        "api_version": "v1",
        "schema_version": info.get("schema_version"),
        "supported_schema_version": info.get("supported_schema_version"),
        "writable": bool(info.get("writable")),
        "schema_status": str(info.get("status") or "unknown"),
        "journal_mode": info.get("journal_mode"),
        "controlled_reads": True,
        "session_required": True,
        "loopback_only": settings.loopback_only,
        "allowed_hosts": sorted(settings.effective_allowed_hosts()),
        "media": {kind: spec.as_dict() for kind, spec in SPECS.items()},
        "supported_input_formats": sorted(SPECS[THUMBNAIL].as_dict()["supported_input_formats"]),
        "scheduler": summary.get("scheduler"),
        "runtime": summary.get("runtime"),
        "qualification_evidence": int(summary.get("qualification_evidence") or 0),
        "indexing": index_summary(catalog_db_path(settings.data_root)),
        # P2-04 derived-search counts plus the P2-06A active profile (never any search
        # text, session token, coordinate or path).
        "search": _search_capabilities(request),
        # P3-01: how much of the library carries a current OCR document, how much is still
        # catching up, and whether the pinned weights are installed. Counts and states only:
        # no recognised text and no coordinate is projected here.
        "ocr": ocr_summary(
            catalog_db_path(settings.data_root),
            weights_present=ocr_weights_present(settings.data_root),
        ),
        # P3-02: how many assets carry *current* face facts, how many are still queued, how
        # the unnamed groups/suggestions and the independent candidate index are doing, and
        # which calibrated thresholds this build applies. Counts, states and thresholds only:
        # no bbox, no landmark, no vector and no path is projected here.
        "faces": face_summary(
            catalog_db_path(settings.data_root),
            weights_present=face_weights_present(settings.data_root),
        ),
    }


def _search_capabilities(request: Request) -> dict[str, Any] | None:
    """Merge the read-only search facts of this catalog, or ``None`` when none exist.

    ``query_encoder_available`` is the honest answer to "can this process run the semantic
    channel at all?": this build ships no embedding adapter inside the API process
    (ADR-0003 §7), so a caller can disable the semantic entry instead of being refused.
    """
    context = context_of(request)
    db_path = catalog_db_path(context.settings.data_root)
    projection = search_projection_summary(db_path)
    profile = search_profile_summary(db_path)
    if projection is None and profile is None:
        return None
    return {
        **(projection or {}),
        **(profile or {}),
        "query_encoder_available": context.query_encoder() is not None,
        "active_embedding_model": active_embedding_model(context, profile),
    }


def active_embedding_model(context: Any, profile: Any) -> dict[str, Any] | None:
    """The *served* semantic model of this catalog, named without loading it (P4-Embedding-Arch).

    The served space comes from the active generation, so the page can say which model is
    answering and whether the P4-02Y-W strong-match statistic belongs to it - without starting a
    worker, opening a model directory or leaking a path.
    """
    from photo_search.models.embedding.registry import registry_for
    from photo_search.search.service import strong_match_policy

    # The *served* generation is the one the active profile names. ``active_generations`` lists
    # every generation whose own space is marked active (ADR-0003 §8 keeps one per space), so
    # picking the first one would name another space's model as soon as two providers are
    # installed - the profile's own ``image_generation_id`` is what decides.
    generations = (profile or {}).get("active_generations") or []
    active_generation_id = str((profile or {}).get("active_image_generation_id") or "")
    served = next(
        (
            item
            for item in generations
            if str(item.get("generation_id") or "") == active_generation_id
        ),
        None,
    )
    if served is None:
        return None
    space_id = str(served.get("space_id") or "")
    if not space_id:
        return None
    provider_id = space_id.split(":", 1)[0]
    entry = registry_for(context.settings.data_root).find(provider_id)
    return {
        "provider_id": provider_id,
        "space_id": space_id,
        "display_name": None if entry is None else str(entry.display_name),
        "adapter": None if entry is None else str(entry.adapter),
        "dimension": None if entry is None else int(entry.identity.dimension),
        "installed": bool(entry is not None and entry.installed),
        "reason": "PROVIDER_UNKNOWN" if entry is None else str(entry.reason),
        "strong_match_available": bool(strong_match_policy(space_id)),
    }


# -------------------------------------------------------------------------- scheduler
@router.post(
    "/scheduler/pause",
    response_model=SchedulerStateResponse,
    summary="Persistently stop claiming new background work",
)
async def pause_scheduler(request: Request) -> dict[str, Any]:
    """Idempotent: repeating a pause that is already persisted changes nothing.

    Pausing never touches a live lease, so a running execution may still heartbeat and
    finish legally (architecture §6.2: pause is a scheduler strategy, not a job state).
    """
    service = service_of(request)
    now_ms = service.catalog.now_ms()
    with service.catalog.write() as connection:
        snapshot, changed = scheduler_state.set_paused(connection, paused=True, now_ms=now_ms)
    return {**snapshot.as_dict(), "changed": changed, "reason": None}


@router.post(
    "/scheduler/resume",
    response_model=SchedulerStateResponse,
    summary="Resume background claiming from the persistent queue",
)
async def resume_scheduler(request: Request) -> dict[str, Any]:
    service = service_of(request)
    now_ms = service.catalog.now_ms()
    with service.catalog.write() as connection:
        snapshot, changed = scheduler_state.set_paused(connection, paused=False, now_ms=now_ms)
    return {**snapshot.as_dict(), "changed": changed, "reason": None}


# ------------------------------------------------------------------------------- stats
_MEDIA_SUMMARY_FIELDS = (
    "processor_id",
    "long_edge",
    "published",
    "pending",
    "source_missing",
    "failed",
    "unsupported",
    "failure_codes",
    "published_files",
    "published_bytes",
    "temp_files",
    "stale_versions",
)


@router.get("/stats", response_model=StatsResponse, summary="Counts plus artifact coverage")
async def stats(request: Request) -> dict[str, Any]:
    service = service_of(request)
    report = service.media_report()
    media = {
        kind: {field: bucket.get(field) for field in _MEDIA_SUMMARY_FIELDS}
        for kind, bucket in report["kinds"].items()
    }
    return {
        "counts": service.counts(),
        "media": media,
        "assets_examined": int(report["assets_examined"]),
        "assets_total": int(report["assets_total"]),
        "partial": bool(report["partial"]),
        "indexing": index_summary(service.catalog.db_path),
    }


# ----------------------------------------------------------------------------- sources
@router.get(
    "/sources/summary",
    response_model=SourceSummaryResponse,
    summary="Lightweight registered source identities",
)
async def list_source_summaries(request: Request) -> dict[str, Any]:
    """Return only catalog identity fields; do not count assets or probe source roots."""
    return {
        "sources": [
            {
                key: row[key]
                for key in ("source_id", "display_name", "volume_hint", "status", "last_successful_scan")
            }
            for row in service_of(request).list_sources()
        ]
    }


def _source_progress_context(context: ApiContext, service: CatalogService) -> tuple[Any, dict[str, bool]]:
    """The served semantic binding and the enrichment availability of this machine.

    Resolved once per request: the manifest is read once and the weight/dataset probes are
    done once, however many sources the answer carries. Both are *reported*: a build without
    a pinned model or without face weights shows ``UNAVAILABLE`` instead of a progress bar
    that could never move.
    """
    binding = refresh_progress.served_binding(
        service.catalog,
        manifest_path=context.refresh_manifest_path(),
        device_policy=context.refresh_device_policy(),
    )
    available = refresh_progress.enrichment_availability(
        service.catalog, data_root=context.settings.data_root
    )
    return binding, available


def _with_progress(
    service: CatalogService,
    payload: dict[str, Any],
    *,
    binding: Any,
    available: dict[str, bool],
) -> dict[str, Any]:
    """Attach the durable P4-02Y-I build progress to one source payload (additive)."""
    payload["progress"] = refresh_progress.source_progress(
        service.catalog,
        source_id=int(payload["source_id"]),
        binding=binding,
        available=available,
    )
    return payload


@router.get("/sources", response_model=SourceListResponse, summary="Registered sources")
async def list_sources(request: Request) -> dict[str, Any]:
    context = context_of(request)
    service = context.service()
    binding, available = _source_progress_context(context, service)
    return {
        "sources": [
            _with_progress(
                service,
                service.get_source(int(row["source_id"])),
                binding=binding,
                available=available,
            )
            for row in service.list_sources()
        ]
    }


@router.post(
    "/sources",
    response_model=SourceDetail,
    status_code=201,
    summary="Register a photo source root",
)
async def create_source(request: Request, payload: SourceCreateRequest) -> dict[str, Any]:
    """Reuses the CLI registration rules: overlap and data-root checks are identical."""
    return service_of(request).register_source(
        display_name=payload.display_name,
        root_path=payload.root_path,
        volume_hint=payload.volume_hint,
    )


@router.get("/sources/{source_id}", response_model=SourceDetail, summary="One source")
async def get_source(request: Request, source_id: int) -> dict[str, Any]:
    context = context_of(request)
    service = context.service()
    binding, available = _source_progress_context(context, service)
    return _with_progress(
        service,
        service.get_source(int(source_id)),
        binding=binding,
        available=available,
    )


@router.patch("/sources/{source_id}", response_model=SourceSummary, summary="Rename a photo source")
async def rename_source(
    request: Request, source_id: int, payload: SourceRenameRequest
) -> dict[str, Any]:
    """Change only display metadata, including while this source is being built."""
    return service_of(request).rename_source(int(source_id), display_name=payload.display_name)


@router.delete(
    "/sources/{source_id}",
    response_model=SourceDeleteResponse,
    summary="Stop managing a source root (records only; never a photo)",
)
async def delete_source(request: Request, source_id: int) -> dict[str, Any]:
    """Remove the registration, its locations and its scan state - and nothing else.

    The card's promise is "删除来源只会移除 PhotoSearch 中的记录，不会删除文件夹中的照片"
    (P4-02Y-I §5). This endpoint therefore never touches a file: it drops the source's
    ``file_locations``/``scan_runs``/``source_roots`` rows in one transaction and reports
    ``originals_touched = 0``. An asset that also lives in another source keeps everything;
    the library refresh (``P4-02Y-I`` §2) may be running as a second writer, so a removal is
    refused with a structured state conflict while one is active - exactly like a scan in
    flight, which the store refuses on its own.
    """
    context = context_of(request)
    refresher = context.refresher()
    if refresher.is_running():
        raise CatalogError(
            INVALID_STATE_TRANSITION,
            "a library refresh is running; delete the source after it finishes",
            details={"source_id": int(source_id), "reason": "REFRESH_IN_PROGRESS"},
        )
    return context.service().delete_source(int(source_id))


@router.post(
    "/sources/{source_id}/scan",
    response_model=ScanRequestResponse,
    status_code=202,
    summary="Request a scan; returns immediately with the scan and job ids",
)
async def request_scan(request: Request, source_id: int) -> dict[str, Any]:
    """Create or reuse the single non-terminal scan, then let the runner execute it."""
    context = context_of(request)
    requested = context.service().start_scan(int(source_id))
    context.runner().submit(int(source_id))
    return {
        "source_id": int(requested["source_id"]),
        "scan_id": int(requested["scan_id"]),
        "job_id": int(requested["job_id"]),
        "state": str(requested["state"]),
        "reused": bool(requested["reused"]),
    }


# ------------------------------------------------------------------------------ refresh
@router.post(
    "/library/refresh",
    response_model=LibraryRefreshStatus,
    status_code=202,
    summary="Refresh the library: fast scan, incremental derivation, searchable first",
)
async def request_library_refresh(
    request: Request, payload: LibraryRefreshRequest | None = Body(default=None)
) -> dict[str, Any]:
    """Start the one-click refresh and answer ``202`` with the current status.

    The request never waits for the scan, the embeddings or the enrichment stages: the
    refresh runs on its own worker thread and reports through
    ``GET /api/v1/library/refresh``. Daily refresh requests reuse the active run. A distinct
    scoped build is queued in FIFO order (``accepted = true, queued = true``); duplicate
    builds are reused. Status fields still describe the active run, with ``pending_builds``
    listing the waiting source scopes. This process-local queue is lost on shutdown.

    ``mode = "build"`` is the P4-02Y-I first build ("开始建库"): the same additive entry, but
    it drains the durable backlog of the named sources in bounded batches instead of stopping
    at one bounded delta. The requested mode is echoed in the status so the caller sees which
    pass it is watching.
    """
    source_ids = None if payload is None else payload.source_ids
    mode = "refresh" if payload is None else str(payload.mode)
    return context_of(request).refresher().start(source_ids, mode=mode)


@router.get(
    "/library/refresh",
    response_model=LibraryRefreshStatus,
    summary="The status of this process's library refresh",
)
async def library_refresh_status(request: Request) -> dict[str, Any]:
    """Read-only status: stage, search readiness and per-source counters (no asset ids)."""
    return context_of(request).refresher().status()


# -------------------------------------------------------------------------------- jobs
@router.get("/jobs", response_model=JobListResponse, summary="Job and scan state")
async def list_jobs(
    request: Request,
    state: str | None = None,
    task_type: str | None = None,
    source_id: int | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    context = context_of(request)
    return job_page(
        context.service().catalog,
        limit=clamp_limit(limit),
        cursor=cursor,
        state=state,
        task_type=task_type,
        source_id=source_id,
        redactor=context.redactor(),
    )


@router.get("/jobs/{job_id}", response_model=JobRecord, summary="One job")
async def get_job(request: Request, job_id: int) -> dict[str, Any]:
    context = context_of(request)
    return job_detail(context.service().catalog, int(job_id), redactor=context.redactor())


# ------------------------------------------------------------------------------ assets
@router.get(
    "/assets",
    response_model=AssetListResponse,
    # ``taken_time`` is *conditional* by contract (ADR-0011 §8.1): it appears only when the
    # request carried a capture bound. ``exclude_unset`` keeps that promise without changing
    # any other key, because ``asset_page`` always returns every other field explicitly.
    response_model_exclude_unset=True,
    summary="Paged asset list",
)
async def list_assets(
    request: Request,
    source_id: int | None = None,
    availability: Literal["present", "missing", "any"] = "any",
    has_thumbnail: Literal["true", "false", "any"] = "any",
    order: AssetOrder = "asset_id",
    place_ids: list[int] | None = Query(default=None),
    include_descendants: bool = True,
    place_state: PlaceStateFilter = "any",
    suggestion_revision: int | None = Query(default=None, ge=0),
    text: str | None = Query(default=None),
    capture_from: str | None = Query(default=None, max_length=32),
    capture_to_exclusive: str | None = Query(default=None, max_length=32),
    limit: int | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    service = service_of(request)
    return asset_page(
        service.catalog,
        service.media_cache(),
        limit=clamp_limit(limit),
        cursor=cursor,
        source_id=source_id,
        availability=availability,
        has_thumbnail=has_thumbnail,
        order=order,
        place_ids=place_ids,
        include_descendants=include_descendants,
        place_state=place_state,
        suggestion_revision=suggestion_revision,
        text=text,
        capture_from=capture_from,
        capture_to_exclusive=capture_to_exclusive,
    )


@router.get("/assets/{asset_id}", response_model=AssetDetail, summary="Asset detail")
async def get_asset(request: Request, asset_id: int) -> dict[str, Any]:
    context = context_of(request)
    service = context.service()
    return asset_detail(
        service.catalog, service.media_cache(), int(asset_id), redactor=context.redactor()
    )


@router.get("/assets/{asset_id}/thumbnail", summary="Grid thumbnail (generated on demand)")
async def asset_thumbnail(request: Request, asset_id: int) -> Response:
    return await _media_response(request, int(asset_id), THUMBNAIL)


@router.head("/assets/{asset_id}/thumbnail", include_in_schema=False)
async def asset_thumbnail_head(request: Request, asset_id: int) -> Response:
    return await _media_response(request, int(asset_id), THUMBNAIL)


@router.get("/assets/{asset_id}/preview", summary="Detail preview (generated on demand)")
async def asset_preview(request: Request, asset_id: int) -> Response:
    return await _media_response(request, int(asset_id), PREVIEW)


@router.head("/assets/{asset_id}/preview", include_in_schema=False)
async def asset_preview_head(request: Request, asset_id: int) -> Response:
    return await _media_response(request, int(asset_id), PREVIEW)


@router.get("/assets/{asset_id}/original", summary="Original bytes behind a controlled id")
async def asset_original(request: Request, asset_id: int) -> Response:
    """Serve the original only through the registered, still-matching position."""
    service = service_of(request)
    row = service.catalog.read_one(
        "SELECT sha256 FROM assets WHERE asset_id = ?", (int(asset_id),)
    )
    if row is None:
        raise ApiError(
            "ASSET_NOT_FOUND",
            f"asset {int(asset_id)} does not exist",
            details={"asset_id": int(asset_id)},
        )
    location = resolve_present_location(service.catalog, int(asset_id))
    etag = original_etag(str(row["sha256"]), location.size, location.mtime_ns)
    headers = {
        "ETag": etag,
        "Accept-Ranges": "bytes",
        "Cache-Control": ORIGINAL_CACHE_CONTROL,
        "Last-Modified": formatdate(location.mtime_ns / 1_000_000_000, usegmt=True),
        "Content-Disposition": content_disposition(
            safe_display_filename(location.relative_path, f"asset-{int(asset_id)}")
        ),
    }
    if _not_modified(request, etag):
        return Response(status_code=304, headers=headers)
    try:
        byte_range = parse_range(request.headers.get("range"), size=location.size)
    except ApiError as refusal:
        # RFC 7233 §4.4: an unsatisfiable range answers 416 with the current extent.
        if refusal.status == 416 and "Content-Range" not in refusal.headers:
            refusal.headers["Content-Range"] = f"bytes */{location.size}"
        raise
    if byte_range is None:
        start, length, status = 0, location.size, 200
    else:
        start, length, status = byte_range.start, byte_range.length, 206
        headers["Content-Range"] = f"bytes {byte_range.start}-{byte_range.end}/{location.size}"
    headers["Content-Length"] = str(int(length))
    headers["Content-Type"] = media_type_for(location.relative_path)
    if request.method.upper() == "HEAD":
        return Response(status_code=status, headers=headers)
    return StreamingResponse(
        stream_file(location.absolute_path, start=start, length=length),
        status_code=status,
        headers=headers,
        media_type=None,
    )


@router.head("/assets/{asset_id}/original", include_in_schema=False)
async def asset_original_head(request: Request, asset_id: int) -> Response:
    """``HEAD`` shares the whole guarded path of ``GET`` and drops the body."""
    return await asset_original(request, asset_id)


async def _media_response(request: Request, asset_id: int, kind: str) -> Response:
    """Serve (generating once when needed) one derived artifact for one asset."""
    service = service_of(request)
    cache = service.media_cache()
    state = media_state(service.catalog, cache, asset_id, kind)
    spec = spec_for(kind)
    row = service.catalog.read_one("SELECT sha256 FROM assets WHERE asset_id = ?", (asset_id,))
    sha256 = str(row["sha256"])
    etag = artifact_etag(spec.processor_id, sha256)
    if state["state"] in (MEDIA_STATE_FAILED, MEDIA_STATE_UNSUPPORTED):
        # The recorded failure is a deterministic property of the content bytes; report it
        # instead of decoding the same input again (P1-03 failure records are reused).
        raise ApiError(
            str(state["code"]),
            str(
                (state["record"] or {}).get("message")
                or "this input cannot be decoded into a derived artifact"
            ),
            details={"asset_id": int(asset_id), "kind": kind, "state": state["state"]},
        )
    if state["state"] != MEDIA_STATE_READY:
        # Generation is only attempted when a registered position passes the same
        # controlled boundary check the original endpoint uses, so a junction swapped in
        # after the scan cannot make the media pipeline read outside its source root.
        try:
            resolve_present_location(service.catalog, asset_id)
        except ApiError as refusal:
            raise ApiError(
                refusal.code, refusal.message, details=refusal.details, headers=refusal.headers
            ) from refusal
        _generate_one(service, asset_id, kind)
        state = media_state(service.catalog, cache, asset_id, kind)
    if state["state"] != MEDIA_STATE_READY:
        raise ApiError(
            str(state["code"] or MEDIA_NOT_READY),
            str(
                (state["record"] or {}).get("message")
                or "no usable artifact could be produced for this asset"
            ),
            details={"asset_id": int(asset_id), "kind": kind, "state": state["state"]},
        )
    path = cache.artifact_path(spec=spec, sha256=sha256)
    available = resolve_optional_location(service.catalog, asset_id) is not None
    headers = {
        "ETag": etag,
        "Cache-Control": ARTIFACT_CACHE_CONTROL,
        "X-Original-Available": "true" if available else "false",
    }
    if _not_modified(request, etag):
        return Response(status_code=304, headers=headers)
    payload = await asyncio.to_thread(path.read_bytes)
    headers["Content-Length"] = str(len(payload))
    if request.method.upper() == "HEAD":
        return Response(status_code=200, headers=headers, media_type="image/jpeg")
    return Response(content=payload, status_code=200, headers=headers, media_type="image/jpeg")


def _generate_one(service: CatalogService, asset_id: int, kind: str) -> None:
    """Run the frozen P1-03 single-asset pipeline; per-asset failures become 409s."""
    try:
        service.generate_media(kind, asset_ids=[int(asset_id)], limit=1)
    except MediaInterrupted as interrupted:  # pragma: no cover - no cancel hook here
        raise ApiError("MEDIA_INTERRUPTED", str(interrupted)) from interrupted
    except CatalogError as error:
        raise ApiError(error.code, error.message, details=error.details) from error


def _not_modified(request: Request, etag: str) -> bool:
    header = request.headers.get("if-none-match")
    if not header:
        return False
    candidates = [value.strip() for value in str(header).split(",")]
    return "*" in candidates or etag in candidates


# ------------------------------------------------------------------------------ places
_ACTION_BY_REQUEST = {"assign": "ASSIGN", "unassign": "UNASSIGN", "restore_auto": "RESTORE_AUTO"}


@router.get("/places", response_model=PlaceListResponse, summary="Paged place directory")
async def list_places(
    request: Request,
    name: str | None = None,
    kind: str | None = None,
    parent: int | None = None,
    ancestor: int | None = None,
    lifecycle: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Keyset paging bound to the place-directory revision (ADR-0005 §9)."""
    return places_of(request).list_places(
        limit=clamp_place_limit(limit),
        cursor=cursor,
        name=name,
        kind=kind,
        parent_place_id=None if parent is None else int(parent),
        ancestor_place_id=None if ancestor is None else int(ancestor),
        lifecycle=lifecycle,
    )


@router.get("/places/albums", summary="Photo albums grouped by current GPS or manual place")
async def list_place_albums(
    request: Request,
    kind: Literal["city", "province", "country"] = "city",
    name: str | None = Query(default=None, max_length=200),
) -> dict[str, Any]:
    service = service_of(request)
    return place_albums.albums(service.catalog, service.data_root, kind=kind, name=name)


@router.post("/places/albums/backfill", summary="Fill missing GPS and nearby-place results in one bounded batch")
async def backfill_place_albums(request: Request) -> dict[str, Any]:
    service = service_of(request)
    return place_backfill.run(service.catalog, service.data_root)


@router.get("/places/albums/{kind}/{key}/assets", summary="Paged photos in one place album")
async def list_place_album_assets(
    request: Request,
    kind: Literal["city", "province", "country"],
    key: str,
    limit: int = 48,
    cursor: str | None = None,
) -> dict[str, Any]:
    service = service_of(request)
    return place_albums.album_assets(
        service.catalog, service.data_root, kind=kind, key=key, limit=limit, cursor=cursor
    )


@router.get("/places/coverage", response_model=PlaceCoverageResponse, summary="Place coverage")
async def place_coverage(request: Request) -> dict[str, Any]:
    return places_of(request).coverage()


@router.get(
    "/places/datasets",
    response_model=PlaceDatasetStateResponse,
    summary="Offline name-dataset state, the active package and any defect",
)
async def place_datasets(request: Request) -> dict[str, Any]:
    """The read-only dataset projection of the catalog (ADR-0005 §8/§9).

    With no imported package it stays ``unavailable`` and never invents an ACTIVE one; a
    package that is present but missing/faulty is reported as ``missing``/``invalid``.
    """
    return places_of(request).datasets()


@router.post(
    "/places",
    response_model=PlaceRecord,
    status_code=201,
    summary="Create a manual city, attraction or custom place",
)
async def create_place(request: Request, payload: PlaceCreateRequest) -> dict[str, Any]:
    if payload.kind == "country":
        raise place_fail(
            "VALIDATION_ERROR",
            "a country place comes from a validated name dataset, not from a manual create",
            field="kind",
        )
    return places_of(request).create_place(
        kind=payload.kind,
        canonical_name=payload.canonical_name,
        parent_place_id=payload.parent_place_id,
        aliases=[alias.model_dump() for alias in payload.aliases],
    )


@router.patch(
    "/places/{place_id}",
    response_model=PlaceRecord,
    summary="Rename a place or edit its alias set (never re-parents)",
)
async def patch_place(
    request: Request, place_id: int, payload: PlacePatchRequest
) -> dict[str, Any]:
    if "parent_place_id" in payload.model_fields_set:
        raise place_fail(
            "VALIDATION_ERROR",
            "re-parenting a place is reserved for a later task and is refused here",
            field="parent_place_id",
        )
    view, _changed = places_of(request).patch_place(
        place_id=int(place_id),
        expected_revision=int(payload.expected_revision),
        canonical_name=payload.canonical_name,
        aliases=None if payload.aliases is None else [alias.model_dump() for alias in payload.aliases],
    )
    return view


@router.get(
    "/assets/{asset_id}/geo",
    response_model=GeoFactResponse,
    summary="The exact GPS fact of one asset (session-protected detail endpoint)",
)
async def asset_geo(request: Request, asset_id: int) -> dict[str, Any]:
    fact = places_of(request).asset_geo(int(asset_id))
    if fact is None:
        raise place_fail(
            "PLACE_NOT_FOUND",
            "no current GPS fact exists for this asset",
            asset_ids=[int(asset_id)],
        )
    return fact


@router.get(
    "/assets/{asset_id}/place",
    response_model=AssetPlaceResponse,
    summary="Effective place, lock, exclusions and revision facts for one asset",
)
async def asset_place(request: Request, asset_id: int) -> dict[str, Any]:
    return places_of(request).asset_place(int(asset_id))


@router.put(
    "/assets/{asset_id}/place-assignment",
    response_model=PlaceOperationResponse,
    summary="Assign, unassign or restore automatic place handling for one asset",
)
async def put_place_assignment(
    request: Request, asset_id: int, payload: PlaceAssignmentRequest
) -> dict[str, Any]:
    return places_of(request).write_assignment(
        asset_id=int(asset_id),
        action=_ACTION_BY_REQUEST[payload.action],
        target_place_id=payload.target_place_id,
        request_id=payload.request_id,
        expected_revision=int(payload.expected_revision),
        origin=payload.origin,
    )


@router.post(
    "/place-assignments/batch",
    response_model=PlaceOperationResponse,
    summary="Apply one atomic place operation to 1..500 assets",
)
async def post_place_batch(request: Request, payload: PlaceBatchRequest) -> dict[str, Any]:
    items = [
        {
            "asset_id": int(item.asset_id),
            "action": _ACTION_BY_REQUEST[item.action],
            "target_place_id": item.target_place_id,
            "expected_revision": int(item.expected_revision),
        }
        for item in payload.items
    ]
    return places_of(request).write_batch(items=items, request_id=payload.request_id)


@router.get(
    "/place-operations",
    response_model=PlaceOperationListResponse,
    summary="The append-only place operation log",
)
async def list_place_operations(
    request: Request, limit: int | None = None, cursor: str | None = None
) -> dict[str, Any]:
    return places_of(request).list_operations(limit=clamp_place_limit(limit), cursor=cursor)


@router.post(
    "/place-operations/{operation_id}/undo",
    response_model=PlaceOperationResponse,
    summary="Compensate one committed place operation",
)
async def undo_place_operation(
    request: Request, operation_id: str, payload: PlaceUndoRequest
) -> dict[str, Any]:
    return places_of(request).undo(
        operation_id=str(operation_id),
        request_id=payload.request_id,
        expected_operation_revision=int(payload.expected_operation_revision),
    )


# ------------------------------------------------------------------------------ search
@router.get(
    "/persons",
    response_model=PersonListResponse,
    summary="Paged active canonical persons with their effective counts",
)
async def list_persons(
    request: Request,
    kind: Literal["all", "named", "unnamed"] = "all",
    limit: int | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """The person list of the P3-06 page.

    Only *active canonical* persons are listed, so a merged id is never shown twice: the row
    it used to be resolves to the person the list already carries. The counts are the
    effective members of ADR-0006 §5 (no ``MANUAL_UNASSIGNED`` face, no actively denied
    one), the ordering is the stable ``person_id``, and the page never materialises the
    library in memory.
    """
    return person_page(
        service_of(request).catalog,
        limit=clamp_person_limit(limit),
        cursor=cursor,
        kind=kind,
    )


@router.get(
    "/faces",
    response_model=FaceListResponse,
    summary="Paged faces: one person's effective members, or the manual-unassigned queue",
)
async def list_faces(
    request: Request,
    person_id: int | None = None,
    assignment_state: Literal["manual_unassigned"] | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Two mutually exclusive views, exactly as the page uses them.

    ``person_id=<id>`` resolves to the canonical person and lists that person's current
    effective member faces; ``assignment_state=manual_unassigned`` lists the faces a human
    deliberately took out of every automatic assignment. Asking for both (or neither) is a
    caller-shape error, and a face is identified by ``face_id`` alone: no geometry, embedding,
    score or local path leaves the catalog through this projection.
    """
    return face_page(
        service_of(request).catalog,
        limit=clamp_person_limit(limit),
        cursor=cursor,
        person_id=None if person_id is None else int(person_id),
        assignment_state=None if assignment_state is None else str(assignment_state),
    )


@router.get(
    "/person-operations",
    response_model=PersonOperationListResponse,
    summary="The append-only person operation log, newest first",
)
async def list_person_operations(
    request: Request, limit: int | None = None, cursor: str | None = None
) -> dict[str, Any]:
    """Read-only history of rename/merge/split/face-control operations and their undos.

    The page gets the ``operation_id``/``operation_revision`` the shared compensation
    endpoint needs, and nothing it must not see: no ``request_id``, no payload hash, no
    before/after snapshot, no name, alias or face geometry. Whether an operation can still be
    compensated is decided by the server, never predicted here.
    """
    return operation_page(
        service_of(request).catalog,
        limit=clamp_person_limit(limit),
        cursor=cursor,
    )


@router.get(
    "/face-mappings",
    response_model=MappingListResponse,
    summary="Paged face-redetection review groups (open or all)",
)
async def list_face_mappings(
    request: Request,
    state: str = "open",
    limit: str | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """The review list of ADR-0010 §8.1.

    ``state=open`` shows the groups a human still has to decide; ``state=all`` adds the
    decided ones. The limit is *validated* (1..200, default 50) rather than clamped, the
    cursor is the independent ``fm1`` space, and every field is an id, a state, a revision, a
    count or a controlled crop URL - never a box, a landmark, a similarity or a path.

    Both parameters are typed as plain strings on purpose: ADR-0010 §8.2 freezes the refusal
    body of this surface, and the transport validator would answer first with its generic
    ``{"errors": [...]}`` shape. The read layer therefore parses ``limit`` and checks ``state``
    itself, so every 422 of this path is the frozen ``VALIDATION_ERROR`` closure.
    """
    return persons_of(request).mapping_page(limit=limit, cursor=cursor, state=str(state))


@router.get(
    "/face-mappings/{old_face_id}",
    response_model=MappingGroup,
    summary="One face-redetection review group and its live candidates",
)
async def get_face_mapping(request: Request, old_face_id: str) -> dict[str, Any]:
    return persons_of(request).mapping_group(str(old_face_id))


@router.post(
    "/face-mappings/decisions",
    response_model=MappingOperationResponse,
    summary="Confirm or reject 1..200 review groups atomically, or nothing at all",
)
async def post_face_mapping_decisions(
    request: Request, payload: Any = Body(default=None)
) -> dict[str, Any]:
    """The one mapping write of ADR-0010 §5.2.

    The body is validated by the mapping module - not by the transport - so every refusal
    answers in the frozen per-code ``details`` closure (``VALIDATION_ERROR.reason``,
    ``FACE_MAPPING_STALE`` revision pair, ...) instead of a transport-shaped error list.
    """
    body = payload if isinstance(payload, Mapping) else {}
    return persons_of(request).decide_face_mappings(
        payload=payload,
        contract_version=body.get("contract_version"),
        request_id=body.get("request_id"),
        asset_id=body.get("asset_id"),
        decisions=body.get("decisions"),
    )


@router.get("/persons/browse", summary="Find people by name for personal browsing")
async def browse_persons(
    request: Request,
    kind: str = "all",
    q: str = "",
    limit: int | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    return person_albums.people_page(
        service_of(request).catalog, kind=kind, q=q, limit=clamp_person_limit(limit), cursor=cursor
    )


@router.get(
    "/persons/{person_id}/photos",
    summary="All effective photos of one person, without a semantic model",
)
async def person_photos(
    request: Request, person_id: int, limit: int | None = None, cursor: str | None = None
) -> dict[str, Any]:
    service = service_of(request)
    return person_albums.photo_page(
        service.catalog,
        service.media_cache(),
        person_id=person_id,
        limit=clamp_person_limit(limit),
        cursor=cursor,
    )


@router.put("/persons/{person_id}/cover", summary="Choose an effective member face as album cover")
async def put_person_cover(
    request: Request, person_id: int, payload: PersonCoverRequest
) -> dict[str, Any]:
    return person_albums.set_cover(
        service_of(request).catalog,
        person_id=person_id,
        face_id=payload.face_id,
        expected_revision=payload.expected_revision,
    )


@router.get("/faces/inbox", summary="Detected faces still needing personal organization")
async def face_inbox(
    request: Request, limit: int | None = None, cursor: str | None = None
) -> dict[str, Any]:
    return person_albums.inbox_page(
        service_of(request).catalog, limit=clamp_person_limit(limit), cursor=cursor
    )


@router.get("/assets/{asset_id}/faces", summary="Safe cropped faces of one photo")
async def photo_faces(request: Request, asset_id: int) -> dict[str, Any]:
    return person_albums.asset_faces(service_of(request).catalog, asset_id)


@router.put(
    "/persons/{person_id}/label",
    response_model=PersonOperationResponse,
    summary="Rename without implicitly merging another person",
)
async def put_person_label(
    request: Request, person_id: int, payload: PersonNameRequest
) -> dict[str, Any]:
    return persons_of(request).rename(
        person_id=person_id,
        name=payload.name,
        request_id=payload.request_id,
        expected_revision=int(payload.expected_revision),
        merge_by_name=False,
    )


@router.get(
    "/persons/{person_id}",
    response_model=PersonRecord,
    summary="The canonical person behind one id, with alias and member counts",
)
async def get_person(request: Request, person_id: int) -> dict[str, Any]:
    """An id that was merged away is answered with its canonical person, not a redirect."""
    return persons_of(request).get_person(int(person_id))


@router.put(
    "/persons/{person_id}/name",
    response_model=PersonOperationResponse,
    summary="Save a display name; a name another active person owns merges into it",
)
async def put_person_name(
    request: Request, person_id: int, payload: PersonNameRequest
) -> dict[str, Any]:
    """The single naming entry point: rename, same-name merge, never a second person."""
    return persons_of(request).rename(
        person_id=int(person_id),
        name=payload.name,
        request_id=payload.request_id,
        expected_revision=int(payload.expected_revision),
    )


@router.post(
    "/persons/merge",
    response_model=PersonOperationResponse,
    summary="Merge 1..500 selected persons into one target, atomically",
)
async def post_person_merge(
    request: Request, payload: PersonMergeRequest
) -> dict[str, Any]:
    return persons_of(request).merge(
        target_person_id=int(payload.target_person_id),
        target_expected_revision=int(payload.target_expected_revision),
        sources=[
            {
                "person_id": int(source.person_id),
                "expected_revision": int(source.expected_revision),
            }
            for source in payload.sources
        ],
        request_id=payload.request_id,
    )


@router.post(
    "/persons/{source_person_id}/split",
    response_model=PersonSplitOperationResponse,
    summary="Split selected faces of one person into a new unnamed person, atomically",
)
async def post_person_split(
    request: Request, source_person_id: int, payload: PersonSplitRequest
) -> dict[str, Any]:
    """The only split entry point of ADR-0008: 1..500 explicit ``face_id``s, or nothing.

    The request never widens to the other faces of a photo, and a rejected split leaves the
    catalog byte-identical: the server validates every selected face, every revision and the
    remaining members before it writes the operation row or creates the new person.
    """
    return persons_of(request).split(
        source_person_id=int(source_person_id),
        contract_version=payload.contract_version,
        source_expected_revision=int(payload.source_expected_revision),
        faces=[
            {
                "face_id": selection.face_id,
                "expected_control_revision": int(selection.expected_control_revision),
                "expected_assignment_revision": int(selection.expected_assignment_revision),
            }
            for selection in payload.faces
        ],
        request_id=payload.request_id,
    )


@router.post(
    "/person-operations/{operation_id}/undo",
    response_model=(
        PersonOperationResponse
        | FaceOperationResponse
        | PersonSplitOperationResponse
        | MappingOperationResponse
    ),
    summary="Compensate one committed person operation, or refuse it whole",
)
async def undo_person_operation(
    request: Request, operation_id: str, payload: PersonUndoRequest
) -> dict[str, Any]:
    """The one compensation endpoint of ADR-0006 and ADR-0007: either family.

    The answer shape follows the family of the operation being compensated - a person
    bundle item or a ``FACE_CONTROL`` item - and both branches carry the same frozen
    ``operation_id``/``status``/``changed``/``catalog_filter_revision`` envelope.
    """
    return persons_of(request).undo(
        operation_id=str(operation_id),
        request_id=payload.request_id,
        expected_operation_revision=int(payload.expected_operation_revision),
    )


# ------------------------------------------------------------------------ face control
@router.get(
    "/faces/{face_id}/assignment",
    response_model=FaceControlState,
    summary="The mode, logical revision, effective person and denials of one face",
)
async def get_face_assignment(request: Request, face_id: str) -> dict[str, Any]:
    return persons_of(request).face_assignment(str(face_id))


@router.put(
    "/faces/{face_id}/assignment",
    response_model=FaceOperationResponse,
    summary="Assign, unassign or restore one face without touching its photo's other faces",
)
async def put_face_assignment(
    request: Request, face_id: str, payload: FaceCorrectionRequest
) -> dict[str, Any]:
    return persons_of(request).set_face_assignment(
        face_id=str(face_id),
        contract_version=payload.contract_version,
        action=payload.action,
        target_person_id=payload.target_person_id,
        expected_revision=int(payload.expected_revision),
        request_id=payload.request_id,
    )


@router.post(
    "/face-assignments/batch",
    response_model=FaceOperationResponse,
    summary="Apply 1..500 explicit face corrections atomically, or nothing at all",
)
async def post_face_assignment_batch(
    request: Request, payload: FaceCorrectionBatchRequest
) -> dict[str, Any]:
    return persons_of(request).edit_face_assignments(
        items=[
            {
                "face_id": item.face_id,
                "action": item.action,
                "target_person_id": item.target_person_id,
                "expected_revision": int(item.expected_revision),
            }
            for item in payload.items
        ],
        contract_version=payload.contract_version,
        request_id=payload.request_id,
    )


@router.get(
    "/faces/{face_id}/thumbnail",
    summary="Bounded face-avatar crop, rendered from the safe preview of its photo",
)
async def face_thumbnail(request: Request, face_id: str) -> Response:
    """Serve the avatar of one face, cropped server-side from the published preview.

    The client names a ``face_id`` and nothing else: it cannot supply a path, a box or a
    size. The authoritative box comes from ``faces``, the pixels from the frozen P1-03
    preview (generated on demand through the same controlled boundary the asset endpoints
    use), and the result is published into the rebuildable face-crop cache. A missing face is
    a structured 404, an offline source or an undecodable input keeps its own stable code,
    and the photo's other faces are never involved.
    """
    return await _face_crop_response(request, str(face_id))


@router.head("/faces/{face_id}/thumbnail", include_in_schema=False)
async def face_thumbnail_head(request: Request, face_id: str) -> Response:
    return await _face_crop_response(request, str(face_id))


async def _face_crop_response(request: Request, face_id: str) -> Response:
    service = service_of(request)
    catalog = service.catalog
    cache = service.media_cache()
    row = catalog.read_one(
        "SELECT face_id, asset_id, bbox_json, artifact_id, input_revision FROM faces WHERE face_id = ?",
        (str(face_id),),
    )
    if row is None:
        raise ApiError(
            FACE_NOT_FOUND,
            f"face {str(face_id)} does not exist",
            details={"face_id": str(face_id)},
        )
    # A stored box that is not a finite four-number rectangle is a catalog defect, not a
    # request error; it is reported as a state conflict instead of being guessed at.
    box = parse_bbox(row["bbox_json"])
    asset_id = int(row["asset_id"])
    asset = catalog.read_one(
        "SELECT sha256 FROM assets WHERE asset_id = ?", (asset_id,)
    )
    if asset is None:
        raise ApiError(
            FACE_NOT_FOUND,
            "the face refers to an asset that no longer exists",
            details={"face_id": str(face_id)},
        )
    sha256 = str(asset["sha256"])
    key = crop_key(face_id=str(row["face_id"]), box=box, sha256=sha256)
    path = cache.face_crop_path(key=key)
    etag = f'"{FACE_CROP_PROCESSOR_ID}:{key}"'
    headers = {"ETag": etag, "Cache-Control": ARTIFACT_CACHE_CONTROL}
    if not cache.is_published(path):
        # 1. the source must still be a registered position that passes every boundary check
        location = resolve_present_location(catalog, asset_id)
        # 2. the frozen preview is published first, exactly like ``/assets/{id}/preview``
        preview = _ensure_preview(service, asset_id, sha256)
        # 3. the crop is rendered off the event loop, then published atomically
        # JPEG draft decoding can make the recorded face frame smaller than the
        # original header. The immutable detection document tells us which frame
        # its box actually uses, without rewriting faces or running recognition.
        artifact = catalog.read_one(
            "SELECT * FROM artifacts WHERE artifact_id = ?", (str(row["artifact_id"]),)
        )
        source_size = await asyncio.to_thread(
            recorded_face_frame,
            catalog.data_root,
            dict(row),
            None if artifact is None else dict(artifact),
            box=box,
            sha256=sha256,
        )
        if source_size is None:
            source_size = await asyncio.to_thread(probe_original_frame, location.absolute_path)
        payload = await asyncio.to_thread(
            render_crop, preview, box=box, source_size=source_size
        )
        cache.publish(path, payload)
    if _not_modified(request, etag):
        return Response(status_code=304, headers=headers)
    payload = await asyncio.to_thread(path.read_bytes)
    headers["Content-Length"] = str(len(payload))
    if request.method.upper() == "HEAD":
        return Response(status_code=200, headers=headers, media_type="image/jpeg")
    return Response(content=payload, status_code=200, headers=headers, media_type="image/jpeg")


def _ensure_preview(service: CatalogService, asset_id: int, sha256: str) -> Path:
    """Publish (once) the frozen P1-03 preview of one asset, or refuse with its own code.

    The state machine is the one ``GET /assets/{id}/preview`` already implements: a recorded
    deterministic failure is reported from its record instead of re-decoding the same bytes,
    and generation only starts after a registered position passed the controlled boundary.
    """
    catalog = service.catalog
    cache = service.media_cache()
    state = media_state(catalog, cache, int(asset_id), PREVIEW)
    if state["state"] in (MEDIA_STATE_FAILED, MEDIA_STATE_UNSUPPORTED):
        raise ApiError(
            str(state["code"]),
            str(
                (state["record"] or {}).get("message")
                or "this input cannot be decoded into a derived artifact"
            ),
            details={"asset_id": int(asset_id), "kind": PREVIEW, "state": state["state"]},
        )
    if state["state"] != MEDIA_STATE_READY:
        resolve_present_location(catalog, int(asset_id))
        _generate_one(service, int(asset_id), PREVIEW)
        state = media_state(catalog, cache, int(asset_id), PREVIEW)
    if state["state"] != MEDIA_STATE_READY:
        raise ApiError(
            str(state["code"] or MEDIA_NOT_READY),
            str(
                (state["record"] or {}).get("message")
                or "no usable preview could be produced for this asset"
            ),
            details={"asset_id": int(asset_id), "kind": PREVIEW, "state": state["state"]},
        )
    return cache.artifact_path(spec=PREVIEW_SPEC, sha256=str(sha256))


# ------------------------------------------------------------------------------ search
@router.post(
    "/search",
    response_model=SearchResultModel,
    # ADR-0011 §8.1: the ``taken_time`` honesty block is conditional (a capture-conditioned
    # query only), so unset keys are not serialised. Every other field is always returned by
    # the service, so this changes nothing else in the frozen envelope.
    response_model_exclude_unset=True,
    summary="One page of a fused search bound to a session, profile and catalog revision",
)
async def post_search(request: Request, payload: SearchRequestModel) -> dict[str, Any]:
    """Run or continue one search.

    The first request runs the retrieval once and answers page one together with its
    ``search_session_id`` and ``next_cursor``; a continuation repeats the same body plus
    those two fields and receives the next slice of the very same frozen ranking. An
    expired session, a foreign cursor, a switched profile or a moved catalog revision is
    reported with its own stable code instead of a silently different ordering.
    """
    context = context_of(request)
    envelope = SearchRequest.from_envelope(payload.model_dump())
    return search_request(
        context.service().catalog,
        envelope,
        sessions=context.search_sessions,
        encoder=context.query_encoder(),
        reranker=context.reranker(),
    )


def _place_aware_parse(service: Any, text: str) -> tuple[str | None, list[dict[str, Any]], str | None]:
    """Recognize only the bounded place-first Chinese forms supported by P4-PLACE-SEARCH-A."""
    normalized = "".join(str(text).split()).casefold()
    rows = service.catalog.read_all(
        "SELECT place_id, kind, canonical_name, parent_place_id FROM places WHERE lifecycle='ACTIVE' ORDER BY length(canonical_name) DESC"
    )
    candidates: list[dict[str, Any]] = []
    place_names = {int(row["place_id"]): str(row["canonical_name"]) for row in rows}
    for row in rows:
        if str(row["kind"]) not in ("city", "country"):
            continue
        aliases = service.catalog.read_all(
            "SELECT text FROM place_aliases WHERE place_id=? AND lifecycle='ACTIVE'", (int(row["place_id"]),)
        )
        parent_id = row["parent_place_id"]
        candidates.append({"kind": str(row["kind"]), "key": str(row["place_id"]), "name": str(row["canonical_name"]), "parent": place_names.get(int(parent_id), "") if parent_id else "", "aliases": [str(value["text"]) for value in aliases]})
    # Province names come from the installed INDEX, including known provinces that
    # currently have no album members.
    province_index, _, _, province_index_available = place_albums._index(service.catalog, service.data_root)
    if province_index_available:
        provinces: dict[str, dict[str, Any]] = {}
        for region in province_index.get("city_to_admin1", {}).values():
            provinces.setdefault(str(region["id"]), {"kind": "province", "key": str(region["id"]), "name": str(region["name"]), "parent": "", "aliases": []})
            entry = provinces[str(region["id"])]
            entry["aliases"] = list(dict.fromkeys([*entry["aliases"], *region.get("aliases", [])]))
        candidates.extend(provinces.values())
    matches: list[tuple[int, dict[str, Any], str | None]] = []
    for item in candidates:
        for spelling in [item["name"], *item["aliases"]]:
            name = "".join(str(spelling).split()).casefold()
            if not name:
                continue
            if normalized == name:
                matches.append((len(name), item, None))
            elif normalized.startswith("在" + name + "拍的"):
                matches.append((len(name), item, str(text).strip()[len("在" + str(spelling) + "拍的"):].strip()))
            elif normalized.startswith(name + "的"):
                matches.append((len(name), item, str(text).strip()[len(str(spelling) + "的"):].strip()))
    if not matches:
        return None, [], None
    longest = max(value[0] for value in matches)
    selected = {(value[1]["kind"], value[1]["key"]): value for value in matches if value[0] == longest}
    options = [value[1] for value in selected.values()]
    content = next(iter(selected.values()))[2] if len(selected) == 1 else None
    return ("ambiguous" if len(options) > 1 else "recognized"), options, content


@router.post("/search/place-aware", summary="Personal local place plus content search")
async def post_place_aware_search(request: Request, payload: PlaceAwareSearchRequestModel) -> dict[str, Any]:
    """Additive entry that resolves place intent before reusing the existing search/session service."""
    context = context_of(request)
    service = context.service()
    original = payload.original_query.strip()
    kind, key, content = payload.place_kind, payload.place_key, payload.content_query
    if payload.place_cancelled:
        kind = key = None
        query_text = (content if content is not None else original).strip() or None
        state = None
    elif kind is None and key is None:
        state, options, parsed_content = _place_aware_parse(service, original)
        if state == "ambiguous":
            return {"needs_place_choice": True, "original_query": original, "content_query": parsed_content, "place_choices": options}
        if state == "recognized":
            chosen = options[0]
            kind, key, content = chosen["kind"], chosen["key"], parsed_content
            query_text = content or None
        else:
            # SearchRequest treats an empty text field as invalid; a blank query is the
            # documented pure-filter search, so normalize it to None at this boundary.
            query_text = original or None
    else:
        state = "selected"
        query_text = (content if content is not None else original).strip() or None
    album_ids = None
    revision = None
    place_block = None
    if kind is not None and key is not None:
        album_ids, collection = place_albums.album_member_ids(service.catalog, service.data_root, kind=kind, key=key)
        album = next((item for item in collection["albums"] if item["key"] == str(key)), None)
        revision = (collection["catalog_filter_revision"], collection["suggestion_revision"], collection["package_revision"], kind, str(key))
        if album is None and kind in ("city", "country"):
            known = service.catalog.read_one("SELECT canonical_name, parent_place_id FROM places WHERE place_id=? AND kind=? AND lifecycle='ACTIVE'", (int(key), kind))
            if known is None:
                raise ApiError("PLACE_NOT_FOUND", "the selected place is no longer available", status=409)
            parent_row = service.catalog.read_one("SELECT canonical_name FROM places WHERE place_id=?", (int(known["parent_place_id"]),)) if known["parent_place_id"] else None
            album = {"name": str(known["canonical_name"]), "parent": "" if parent_row is None else str(parent_row["canonical_name"]), "origin": None}
        if album is None and kind == "province":
            province_index, _, _, available = place_albums._index(service.catalog, service.data_root)
            region = next((value for value in province_index.get("city_to_admin1", {}).values() if available and str(value["id"]) == str(key)), None)
            if region is not None:
                album = {"name": str(region["name"]), "parent": "", "origin": None}
        if album is None:
            raise ApiError("PLACE_NOT_FOUND", "the selected place is no longer available", status=409)
        place_block = {"kind": kind, "key": str(key), "name": album["name"], "parent": album.get("parent", ""), "origin": album.get("origin"), "catalog_filter_revision": revision[0], "suggestion_revision": revision[1], "package_revision": revision[2]}
    values = payload.model_dump(exclude={"original_query", "content_query", "place_kind", "place_key", "place_cancelled"})
    values["text"] = query_text
    envelope = SearchRequest.from_envelope(values)
    if kind is not None and key is not None and query_text is None:
        return _place_album_search_page(service, envelope, kind=kind, key=str(key), album_ids=album_ids or [], collection=collection, place_block=place_block)
    result = search_request(service.catalog, envelope, sessions=context.search_sessions, encoder=context.query_encoder(), reranker=context.reranker(), album_asset_ids=album_ids, album_scope_revision=revision)
    result["place_scope"] = place_block
    result["original_query"] = original
    result["content_query"] = query_text
    return result


def _place_album_search_page(service: Any, request: SearchRequest, *, kind: str, key: str, album_ids: list[int], collection: dict[str, Any], place_block: dict[str, Any]) -> dict[str, Any]:
    """Uncapped SQLite pages for a pure selected place; keeps album membership authoritative."""
    catalog = service.catalog
    base = _person_authority_base(catalog, request)
    scope = SearchScope.build(
        source_id=request.source_id,
        availability=request.availability,
        place_ids=request.place_ids,
        include_descendants=request.include_descendants,
        place_state=request.place_state,
        person_ids=base.canonical_person_ids,
        person_match=request.person_match,
        capture=request.capture_range,
        album_asset_ids=album_ids,
    )
    filter_fingerprint = json.dumps({"source_id": request.source_id, "availability": request.availability, "place_ids": list(request.place_ids), "include_descendants": request.include_descendants, "place_state": request.place_state, "person_ids": list(scope.person_ids), "person_match": scope.person_match, "capture_from": request.capture_from, "capture_to_exclusive": request.capture_to_exclusive, "page_size": request.page_size}, sort_keys=True, separators=(",", ":"))
    revisions = (collection["catalog_filter_revision"], collection["suggestion_revision"], collection["package_revision"], kind, key, filter_fingerprint)
    session_id = f"place-album:{kind}:{key}"
    if request.cursor is not None:
        if request.search_session_id != session_id:
            raise ApiError(INVALID_CURSOR, "the place album cursor does not match this search")
        start = place_albums._decode(request.cursor, revisions)
    else:
        start = 0
    where, params = scope_condition(catalog.connection, scope)
    total = int(catalog.read_one(f"SELECT COUNT(*) AS value FROM assets AS a WHERE {where}", tuple(params))["value"])
    rows = catalog.read_all(f"SELECT a.asset_id FROM assets AS a WHERE {where} ORDER BY a.asset_id LIMIT ? OFFSET ?", (*params, int(request.page_size) + 1, start))
    if (
        place_revision.read_filter_revision(catalog.connection),
        place_revision.read_suggestion_revision(catalog.connection),
        place_albums._index(catalog, service.data_root)[1],
    ) != (
        collection["catalog_filter_revision"],
        collection["suggestion_revision"],
        collection["package_revision"],
    ):
        raise ApiError(CATALOG_FILTER_REVISION_STALE, "the place album changed; reload the search", status=409)
    page = rows[: int(request.page_size)]
    next_offset = start + len(page)
    has_more = next_offset < total
    token = place_albums._token(next_offset, revisions) if has_more else None
    ids = [int(row["asset_id"]) for row in page]
    hits = [{"asset_id": asset_id, "rank": start + index + 1, "score": None, "matched_by": {"filter": start + index + 1}, "hit_reason": "FILTER_ONLY", "matched_text": [], "semantic_ready": False} for index, asset_id in enumerate(ids)]
    return {
        "contract_version": "p2-contracts.v1", "request_id": request.request_id,
        "search_session_id": session_id, "profile_id": None, "image_generation_id": None,
        "space_id": None, "dimension": None,
        "catalog_filter_revision": int(collection["catalog_filter_revision"]),
        "suggestion_revision": int(collection["suggestion_revision"]), "fusion_config": {},
        "query": None,
        "filters": {"source_id": request.source_id, "availability": request.availability,
                    "place_ids": [int(value) for value in request.place_ids],
                    "include_descendants": request.include_descendants, "place_state": request.place_state,
                    "person_ids": list(scope.person_ids), "person_match": scope.person_match,
                    "capture_from": request.capture_from, "capture_to_exclusive": request.capture_to_exclusive,
                    "catalog_filter_revision": int(collection["catalog_filter_revision"]),
                    "suggestion_revision": int(collection["suggestion_revision"])},
        "hits": hits, "page_size": int(request.page_size), "page_offset": start,
        "next_cursor": token, "has_more": has_more, "total_candidates": total,
        "candidates_truncated": False,
        "channels": {"fts": {"engaged": False, "candidates": 0, "truncated": False},
                     "vector": {"engaged": False, "candidates": 0, "scan_truncated": False},
                     "authoritative": {"total": total, "listed": len(ids), "truncated": False, "order": "asset_id"}},
        "pending_semantic": None,
        "search_projection": _projection_block(catalog, match=None, catch_up=None),
        "rerank_state": "NOT_REQUESTED", "reranked": None, "state": "OK",
        "place_scope": place_block, "original_query": request.text, "content_query": None,
    }


#: The multipart fields ``POST /search/image`` defines. ``text`` is deliberately absent: an
#: uploaded image has no text, and accepting one would let a caller mix two query kinds
#: under a single ranking.
_IMAGE_FORM_FIELDS = frozenset(
    {
        "request_id",
        "contract_version",
        "profile_id",
        "space_id",
        "image_generation_id",
        "dimension",
        "source_id",
        "availability",
        "place_ids",
        "include_descendants",
        "place_state",
        "top_k",
        "page_size",
        "search_session_id",
        "cursor",
    }
)
_IMAGE_TEXT_FIELDS = (
    "contract_version",
    "profile_id",
    "space_id",
    "image_generation_id",
    "availability",
    "place_state",
    "search_session_id",
    "cursor",
)
_IMAGE_INT_FIELDS = ("dimension", "source_id", "top_k", "page_size")


@router.post(
    "/search/image",
    response_model=ImageSearchResultModel,
    response_model_exclude_unset=True,
    summary="Find similar photos for one uploaded image",
)
async def post_search_image(request: Request) -> dict[str, Any]:
    """Search with one uploaded image, reusing the frozen binding and ranking path.

    The upload is validated against the architecture §10.3 hard bounds (byte ceiling, pixel
    ceiling, format allow-list, decode time) and its temporary file is removed before the
    response is sent, on the success and the failure path alike. The image never becomes an
    asset: no ``assets`` row, no index row, no search document and no feedback row is written
    for it, and the returned page is produced by the very same retrieval, RRF and
    authoritative re-check that serves a text query.
    """
    context = context_of(request)
    body = await _read_bounded_body(request, limit=DEFAULT_LIMITS.max_body_bytes)
    form = parse_multipart_form(
        request.headers.get("content-type"),
        body,
        max_body_bytes=DEFAULT_LIMITS.max_body_bytes,
    )
    upload = form.files.get("image")
    if upload is None:
        raise search_fail(
            "the request must carry one multipart file field named image", field="image"
        )
    envelope = _image_search_request(form.fields)
    catalog = context.service().catalog
    binding = resolve_request_binding(catalog, envelope)
    if binding is None:
        raise search_fail(
            "an image query needs a bound space_id, image_generation_id and dimension "
            "(or an activated search profile)",
            field="profile_id",
        )
    encoder = image_encoder_of(context.query_encoder())
    # Decoding and encoding are the only heavy steps here, so they run in a worker thread:
    # the catalog connection stays on the request thread (as in the media endpoints) and the
    # event loop is never blocked by a large photo.
    facts, vector = await asyncio.to_thread(
        _prepare_upload,
        upload.payload,
        encoder,
        str(binding.space_id),
        int(binding.dimension),
        envelope.cursor is not None,
    )
    if vector is None and envelope.cursor is None:
        # No image tower in this process (ADR-0003 §7). The text channel is *not* used as a
        # stand-in: "no image encoder" and "nothing looked similar" must stay different.
        raise CatalogError(
            RUNTIME_CAPABILITY_UNAVAILABLE,
            "this process has no image encoder for the bound embedding space",
            details={
                "reason": "IMAGE_ENCODER_UNAVAILABLE",
                "generation_id": binding.generation_id,
                "space_id": binding.space_id,
            },
        )
    result = search_request(
        catalog,
        envelope,
        sessions=context.search_sessions,
        query_vector=vector,
        query_identity=image_query_identity(facts),
    )
    result["image_query"] = facts.to_envelope(limits=DEFAULT_LIMITS)
    return result


def _image_search_request(fields: Mapping[str, str]) -> SearchRequest:
    """The frozen ``SearchRequest`` one multipart image query declares, or a 422.

    The multipart fields are converted into the *same* envelope a JSON caller sends, so the
    frozen shape (closed keys, bounds, the all-or-nothing binding triple) is enforced once,
    by :meth:`SearchRequest.from_envelope`, instead of by a second set of rules here.
    """
    unexpected = sorted(set(str(name) for name in fields) - _IMAGE_FORM_FIELDS)
    if unexpected:
        raise search_fail(
            "this endpoint does not define those multipart fields", fields=unexpected
        )
    body: dict[str, Any] = {
        "contract_version": str(fields.get("contract_version") or CONTRACT_VERSION),
        "request_id": str(fields.get("request_id") or ""),
    }
    for name in _IMAGE_TEXT_FIELDS:
        if name in ("contract_version",):
            continue
        value = fields.get(name)
        if value:
            body[name] = str(value)
    for name in _IMAGE_INT_FIELDS:
        if name in fields:
            body[name] = _form_integer(fields[name], field=name)
    if "include_descendants" in fields:
        body["include_descendants"] = _form_boolean(
            fields["include_descendants"], field="include_descendants"
        )
    if "place_ids" in fields:
        body["place_ids"] = _form_integer_list(fields["place_ids"], field="place_ids")
    return SearchRequest.from_envelope(body)


def _form_integer(value: str, *, field: str) -> int:
    text = str(value).strip()
    try:
        return int(text)
    except ValueError as error:
        raise search_fail(f"{field} must be an integer", field=field) from error


def _form_integer_list(value: str, *, field: str) -> list[int]:
    text = str(value).strip()
    if not text:
        return []
    return [_form_integer(part, field=field) for part in text.split(",") if part.strip() != ""]


def _form_boolean(value: str, *, field: str) -> bool:
    text = str(value).strip().lower()
    if text in ("true", "1"):
        return True
    if text in ("false", "0"):
        return False
    raise search_fail(f"{field} must be true or false", field=field)


def _prepare_upload(
    payload: bytes,
    encoder: Any,
    space_id: str,
    dimension: int,
    continuation: bool,
) -> tuple[UploadedImage, list[float] | None]:
    """Validate one upload in its temporary directory and encode it when needed.

    Runs off the event loop. The temporary directory is created and removed inside
    :func:`stage_upload`, so this function cannot leak one whatever it raises.
    """
    with stage_upload(payload, limits=DEFAULT_LIMITS) as staged:
        vector: list[float] | None = None
        if encoder is not None and not continuation:
            vector = encode_image_query(
                encoder, staged.image, space_id=str(space_id), dimension=int(dimension)
            )
        return staged.facts, vector


async def _read_bounded_body(request: Request, *, limit: int) -> bytes:
    """Read the request body with a hard ceiling, whatever ``Content-Length`` claims."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > int(limit):
                raise CatalogError(
                    UPLOAD_TOO_LARGE,
                    "the uploaded image is larger than this build accepts",
                    details={"byte_size": int(declared), "max_bytes": int(limit)},
                )
        except ValueError:
            # A malformed length is not trusted either way; the read ceiling below decides.
            pass
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > int(limit):
            raise CatalogError(
                UPLOAD_TOO_LARGE,
                "the uploaded image is larger than this build accepts",
                details={"byte_size": int(total), "max_bytes": int(limit)},
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/search/feedback",
    response_model=FeedbackResponseModel,
    status_code=201,
    summary="Record one explicit relevance verdict (idempotent by request_id)",
)
async def post_search_feedback(
    request: Request, payload: FeedbackRequestModel
) -> Response:
    """Store one verdict and answer ``201``; an identical replay answers ``200``.

    The verdict is an audit fact: it never hides an asset, never changes the authoritative
    place/person filters and never feeds back into ranking in this phase. A repeated
    ``request_id`` with the same payload returns the recorded row; a repeated
    ``request_id`` with a different payload is a structured 409 and writes nothing.
    """
    envelope = FeedbackRequest.from_envelope(payload.model_dump())
    result = submit_feedback(service_of(request).catalog, envelope)
    return JSONResponse(
        status_code=200 if bool(result.get("replayed")) else 201, content=result
    )


@router.get(
    "/search/feedback",
    response_model=FeedbackListResponseModel,
    summary="Paged, read-only view of the recorded relevance verdicts",
)
async def get_search_feedback(
    request: Request, limit: int | None = None, cursor: str | None = None
) -> dict[str, Any]:
    """The audit view of the feedback log: its own fields, keyset paged, nothing else."""
    return list_feedback(
        service_of(request).catalog, limit=feedback_limit(limit), cursor=cursor
    )


@router.get(
    "/search-profile",
    response_model=SearchProfileResponse,
    summary="The activated search profile (read-only; profile_id is null when unset)",
)
async def get_search_profile(request: Request) -> dict[str, Any]:
    return profile_projection(service_of(request).catalog)


@router.post(
    "/search-profile/activate",
    response_model=SearchProfileActivationResponse,
    summary="Activate one immutable search profile, atomically and idempotently",
)
async def activate_search_profile(
    request: Request, payload: SearchProfileActivateRequest
) -> dict[str, Any]:
    """Verify the whole profile bundle, then move the pointer and ``is_active`` together.

    A profile whose embedding processor has no healthy, evidenced runtime, whose index
    generation is not READY or whose FTS generation this build cannot serve is refused with
    a structured 409 and **zero** writes, so no reader can ever observe a half-switched
    encoder/index pair (ADR-0003 §8/§10).
    """
    service = service_of(request)
    profile, changed, readiness = activate_profile(
        service.catalog, profile_id=payload.profile_id, now_ms=service.catalog.now_ms()
    )
    pointer = profile_projection(service.catalog)
    return {
        "contract_version": profile.to_dict()["contract_version"],
        "profile_id": profile.profile_id,
        "image_generation_id": profile.image_generation_id,
        "space_id": profile.space_id,
        "fts_generation_id": profile.fts_generation_id,
        "updated_at": int(pointer["updated_at"]),
        "changed": bool(changed),
        "state": "ACTIVE" if changed else "ALREADY_ACTIVE",
        "readiness": readiness,
    }


__all__ = ["APP_VERSION", "context_of", "router", "service_of"]
