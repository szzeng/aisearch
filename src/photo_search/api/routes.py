"""The read-only business API plus the controlled file endpoints (architecture §10.2).

Writes use the application's owned connection on the event loop. Expensive browse
and search work uses a bounded pool with thread-owned catalog connections.
Media generation separately owns read-only worker connections and bounded worker slots;
reading published media bytes also stays in a worker thread.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import secrets
import sqlite3
import sys
from collections.abc import Mapping
from dataclasses import replace
from email.utils import formatdate
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Body, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from photo_search import photo_collections, photo_smart_collections
from photo_search.api import timeline as timeline_queries
from photo_search.api.catalog_reads import (
    run_catalog_projection,
    run_catalog_read,
    run_catalog_search,
)
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
    resolve_registered_location,
    safe_display_filename,
    stream_file,
)
from photo_search.api.local_actions import perform_local_action
from photo_search.api.media_generation import (
    generate_one as _generate_one_core,
)
from photo_search.api.media_generation import (
    generate_one_in_worker as _generate_one_in_worker_core,
)
from photo_search.api.models import (
    AssetDetail,
    AssetFaceReviewResponse,
    AssetListResponse,
    AssetLocalActionRequest,
    AssetOrder,
    AssetPlaceResponse,
    CapabilitiesResponse,
    CreatePersonFromFaceRequest,
    CreatePersonFromFaceResponse,
    FaceControlState,
    FaceCorrectionBatchRequest,
    FaceCorrectionRequest,
    FaceDetailBatchRequest,
    FaceInboxVisibilityRequest,
    FaceInboxVisibilityResponse,
    FaceListResponse,
    FaceOperationResponse,
    FaceRegroupRequest,
    FaceRegroupResponse,
    FaceRematchRequest,
    FavoriteBatchRequest,
    FavoriteBatchResponse,
    FavoriteListResponse,
    FavoriteStatesResponse,
    FavoriteWriteResponse,
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
    PersonCollectionFaceMembersRequest,
    PersonCollectionMembersRequest,
    PersonCollectionNameRequest,
    PersonCoverRequest,
    PersonListResponse,
    PersonMergeRequest,
    PersonNameRequest,
    PersonOperationListResponse,
    PersonOperationResponse,
    PersonOperationRetentionCleanupRequest,
    PersonRecord,
    PersonSplitOperationResponse,
    PersonSplitRequest,
    PersonUndoRequest,
    PersonVisibilityRequest,
    PersonVisibilityResponse,
    PhotoCollectionAppendFromSearchRequest,
    PhotoCollectionCreateRequest,
    PhotoCollectionDeleteRequest,
    PhotoCollectionFromSearchRequest,
    PhotoCollectionMembersRequest,
    PhotoCollectionRenameRequest,
    PhotoSmartCollectionDeleteRequest,
    PhotoSmartCollectionFromSearchRequest,
    PhotoSmartCollectionRenameRequest,
    PhotoSmartExclusionRequest,
    PhotoSmartRefreshRequest,
    PhotoSmartRuleReplaceRequest,
    PhotoSmartSaveStaticRequest,
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
    SourcePathsResponse,
    SourceRenameRequest,
    SourceSummary,
    SourceSummaryResponse,
    StatsResponse,
)
from photo_search.api.queries import (
    asset_detail,
    clamp_limit,
    job_detail,
    job_page,
    media_state,
    prepare_asset_page,
    read_asset_page,
    source_list,
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
    PROFILE_GENERATION_MISMATCH,
    RUNTIME_CAPABILITY_UNAVAILABLE,
    SEARCH_SESSION_EXPIRED,
    UPLOAD_TOO_LARGE,
    CatalogError,
)
from photo_search.catalog.migrate import catalog_info
from photo_search.config import catalog_db_path
from photo_search.face.weights import weights_present as face_weights_present
from photo_search.favorites import (
    favorite_states,
    list_favorites,
    set_favorite,
    set_favorites_batch,
)
from photo_search.geo import resolver as geo_resolver
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
from photo_search.media.decode import read_normalized_dimensions
from photo_search.media.face_crop import (
    crop_key,
    parse_bbox,
    probe_original_frame,
    recorded_face_frame,
    render_crop,
)
from photo_search.media.pipeline import require_unchanged
from photo_search.media.status import (
    MEDIA_STATE_FAILED,
    MEDIA_STATE_READY,
    MEDIA_STATE_UNSUPPORTED,
)
from photo_search.ocr.weights import weights_present as ocr_weights_present
from photo_search.persons import albums as person_albums
from photo_search.persons import collections as person_collections
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
from photo_search.places import choices as place_choices
from photo_search.places import cn_facets
from photo_search.places import datasets as place_datasets_module
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
from photo_search.search.retrieval import SearchScope, person_condition, scope_condition
from photo_search.search.service import (
    _person_authority_base,
    _projection_block,
    _resolve_profile,
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
from photo_search.storage import quick_storage_summary
from photo_search.taken_time import read as taken_time_read

APP_VERSION = "0.3.0"

# Local catalog ids can be reused after a rebuild. Revalidate the id-based URL;
# the underlying content-addressed files still need no regeneration.
ARTIFACT_CACHE_CONTROL = "private, no-cache, must-revalidate"
ORIGINAL_CACHE_CONTROL = "private, max-age=0, must-revalidate"

router = APIRouter(prefix="/api/v1", tags=["photo-search"])


def _desktop_supported() -> bool:
    return sys.platform == "win32"


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
    # Capabilities use independent read-only probes even for an absent/old catalog;
    # opening the application's mutable Catalog here would change that contract.
    return await asyncio.to_thread(_capabilities_read, context_of(request))


def _capabilities_read(context: ApiContext) -> dict[str, Any]:
    settings = context.settings
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
        "search": _search_capabilities(context),
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


def _search_capabilities(context: ApiContext) -> dict[str, Any] | None:
    """Merge the read-only search facts of this catalog, or ``None`` when none exist.

    ``query_encoder_available`` is the honest answer to "can this process run the semantic
    channel at all?": this build ships no embedding adapter inside the API process
    (ADR-0003 §7), so a caller can disable the semantic entry instead of being refused.
    """
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
    return await run_catalog_read(service.catalog, _stats_read, data_root=service.data_root)


def _stats_read(catalog: Any, *, data_root: Path) -> dict[str, Any]:
    service = CatalogService(catalog, data_root=data_root)
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


@router.get("/storage/summary", summary="Read-only quick storage overview")
async def storage_summary(request: Request) -> dict[str, Any]:
    """Quick accounting only; never recursively walks local storage directories."""
    return quick_storage_summary(service_of(request).data_root)


@router.get("/storage/library-retention", summary="Read-only unavailable-photo retention preview")
async def storage_library_retention(
    request: Request,
    after_asset_id: int = Query(default=0, ge=0, le=9223372036854775807),
    limit: int = Query(default=50, ge=1, le=50),
) -> dict[str, Any]:
    """Summarize missing locations without inferring deletion or changing the catalog."""
    from photo_search.library_retention import library_retention_preview

    return library_retention_preview(service_of(request).catalog, after_asset_id=after_asset_id, limit=limit)


@router.post("/storage/inspect", summary="Run a bounded storage scan")
async def storage_deep_inspect(request: Request) -> dict[str, Any]:
    from photo_search.storage import storage_inspect

    return await asyncio.to_thread(storage_inspect, context_of(request).settings.data_root, deep=True)


@router.get("/storage/maintenance/preview", summary="Preview available maintenance")
async def storage_maintenance_preview(request: Request) -> dict[str, Any]:
    from photo_search.storage_ui import maintenance_request_state, preview_maintenance

    root = context_of(request).settings.data_root
    preview = await asyncio.to_thread(preview_maintenance, root)
    return {**preview, **maintenance_request_state(root)}


@router.get("/storage/backup", summary="Read manual catalog backup settings")
async def storage_backup_settings(request: Request) -> dict[str, Any]:
    from photo_search.storage_ui import backup_settings

    return backup_settings(context_of(request).settings.data_root)


@router.put("/storage/backup", summary="Save and check manual backup directory")
async def storage_save_backup_settings(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from photo_search.storage_ui import save_backup_directory

    try:
        return await asyncio.to_thread(save_backup_directory, context_of(request).settings.data_root, payload.get("directory", ""))
    except (OSError, ValueError, sqlite3.Error) as error:
        return {"status": "error", "message": str(error)}


@router.post("/storage/backup", summary="Create a manually requested catalog snapshot")
async def storage_create_manual_backup(request: Request) -> dict[str, Any]:
    from photo_search.storage_ui import create_catalog_snapshot

    try:
        return await asyncio.to_thread(create_catalog_snapshot, context_of(request).settings.data_root)
    except (OSError, ValueError, sqlite3.Error) as error:
        return {"status": "error", "message": str(error)}


@router.post("/storage/maintenance/request", summary="Confirm one offline maintenance batch")
async def storage_request_maintenance(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from photo_search.storage_ui import MAINTENANCE_REVIEWED, queue_maintenance

    if not MAINTENANCE_REVIEWED:
        return {"status": "error", "code": "INDEPENDENT_REVIEW_REQUIRED", "message": "独立窄审完成前暂不开放离线维护执行"}

    try:
        operations = payload.get("operations", payload.get("operation"))
        return queue_maintenance(context_of(request).settings.data_root, operations, confirmed=payload.get("confirmed") is True)
    except (OSError, ValueError) as error:
        return {"status": "error", "message": str(error)}


@router.delete("/storage/maintenance/request", summary="Cancel pending offline maintenance")
async def storage_cancel_maintenance(request: Request) -> dict[str, Any]:
    from photo_search.storage_ui import cancel_maintenance

    return cancel_maintenance(context_of(request).settings.data_root)


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


@router.get(
    "/sources/paths",
    response_model=SourcePathsResponse,
    summary="Registered source paths for gallery management",
)
async def list_source_paths(request: Request) -> dict[str, Any]:
    """Return registered roots only when the gallery-management view requests them."""
    return {
        "sources": [
            {"source_id": row["source_id"], "root_path": row["root_path"]}
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
    return await run_catalog_read(
        service.catalog, source_list,
        manifest_path=context.refresh_manifest_path(),
        device_policy=context.refresh_device_policy(),
        data_root=context.settings.data_root,
    )


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
    return await run_catalog_read(
        service.catalog, _source_detail_read, source_id,
        context=context, data_root=service.data_root,
    )


def _source_detail_read(catalog: Any, source_id: int, *, context: ApiContext, data_root: Path) -> dict[str, Any]:
    service = CatalogService(catalog, data_root=data_root)
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
    source-scoped or all-sources build is queued in FIFO order
    (``accepted = true, queued = true``); duplicate builds are reused. Status fields still
    describe the active run, with ``pending_builds`` and ``pending_all_build`` listing waiting
    scopes. This process-local queue is lost on shutdown.

    ``mode = "build"`` is the P4-02Y-I first build ("开始建库"): the same additive entry, but
    it drains the durable backlog of the selected sources in bounded batches instead of stopping
    at one bounded delta. Omitting ``source_ids`` means all sources registered when execution
    starts. The requested mode is echoed in the status so the caller sees which
    pass it is watching.
    """
    source_ids = None if payload is None else payload.source_ids
    mode = "refresh" if payload is None else str(payload.mode)
    face_profile = None if payload is None else payload.face_profile
    return context_of(request).refresher().start(
        source_ids, mode=mode, face_profile=face_profile
    )


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
@router.get("/favorites", response_model=FavoriteListResponse, summary="Paged favorite photos")
async def get_favorites(
    request: Request,
    limit: int = Query(default=50, ge=1, le=50),
    cursor: str | None = None,
    order: Literal["recent", "capture_desc"] = "recent",
) -> dict[str, Any]:
    service = service_of(request)
    return await run_catalog_read(
        service.catalog, list_favorites, service.media_cache(), limit=limit, cursor=cursor, order=order
    )


@router.get("/favorites/states", response_model=FavoriteStatesResponse, summary="Batch favorite states")
async def get_favorite_states(
    request: Request,
    asset_ids: list[int] = Query(..., min_length=1, max_length=100),
) -> dict[str, Any]:
    return await run_catalog_read(service_of(request).catalog, favorite_states, asset_ids)


@router.put("/assets/{asset_id}/favorite", response_model=FavoriteWriteResponse, summary="Favorite a photo")
async def put_favorite(request: Request, asset_id: int) -> dict[str, Any]:
    return set_favorite(service_of(request).catalog, asset_id, favorite=True)


@router.delete("/assets/{asset_id}/favorite", response_model=FavoriteWriteResponse, summary="Unfavorite a photo")
async def delete_favorite(request: Request, asset_id: int) -> dict[str, Any]:
    return set_favorite(service_of(request).catalog, asset_id, favorite=False)


@router.post("/favorites/batch", response_model=FavoriteBatchResponse, summary="Set favorite state for photos")
async def batch_favorites(payload: FavoriteBatchRequest, request: Request) -> dict[str, Any]:
    return set_favorites_batch(service_of(request).catalog, payload.asset_ids, favorite=payload.is_favorite)


@router.get("/photo-collections", summary="Paged ordinary photo collections")
async def list_photo_collections(request: Request, limit: int = Query(default=20, ge=1, le=50),
                                 cursor: str | None = Query(default=None, max_length=2048)) -> dict[str, Any]:
    return await run_catalog_read(service_of(request).catalog, photo_collections.list_collections,
                                  limit=limit, cursor=cursor)


@router.post("/photo-collections", summary="Create an ordinary photo collection")
async def create_photo_collection(payload: PhotoCollectionCreateRequest, request: Request) -> dict[str, Any]:
    return photo_collections.create_collection(service_of(request).catalog, payload.name, payload.create_request_id,
        asset_ids=payload.asset_ids)


@router.get("/photo-collections/{collection_id}", summary="Read one ordinary photo collection")
async def get_photo_collection(request: Request, collection_id: int,
                               instance_token: str | None = Query(default=None, max_length=128)) -> dict[str, Any]:
    return await run_catalog_read(service_of(request).catalog, photo_collections.collection_detail,
                                  collection_id, instance_token=instance_token)


@router.patch("/photo-collections/{collection_id}", summary="Rename an ordinary photo collection")
async def patch_photo_collection(payload: PhotoCollectionRenameRequest, request: Request,
                                 collection_id: int) -> dict[str, Any]:
    return photo_collections.rename_collection(service_of(request).catalog, collection_id,
        instance_token=payload.instance_token, expected_revision=payload.expected_revision, name=payload.name)


@router.delete("/photo-collections/{collection_id}", summary="Delete an ordinary photo collection")
async def delete_photo_collection(payload: PhotoCollectionDeleteRequest, request: Request,
                                  collection_id: int) -> dict[str, Any]:
    return photo_collections.delete_collection(service_of(request).catalog, collection_id,
        instance_token=payload.instance_token, expected_revision=payload.expected_revision)


@router.get("/photo-smart-collections", summary="Paged smart photo collection metadata")
async def list_photo_smart_collections(request: Request, limit: int = Query(default=20, ge=1, le=50),
                                       cursor: str | None = Query(default=None, max_length=2048)) -> dict[str, Any]:
    return await run_catalog_read(service_of(request).catalog, photo_smart_collections.list_collections,
                                  limit=limit, cursor=cursor)


@router.post("/photo-smart-collections/from-search", summary="Save a confirmed search rule as a smart collection")
async def create_photo_smart_collection_from_search(payload: PhotoSmartCollectionFromSearchRequest,
                                                    request: Request) -> dict[str, Any]:
    context = context_of(request)
    auth_session_id = request.cookies.get(SESSION_COOKIE)
    if not auth_session_id:
        raise ApiError(SESSION_INVALID, "application session is required", status=401)
    source_hash = hashlib.sha256(payload.source_token.encode()).hexdigest()
    auth_hash = hashlib.sha256(auth_session_id.encode()).hexdigest()
    request_hash = hashlib.sha256(json.dumps([payload.name, source_hash], ensure_ascii=False,
                                              separators=(",", ":")).encode()).hexdigest()
    # Durable idempotent replay precedes resolving the short-lived source credential.
    old = context.service().catalog.read_one(
        "SELECT * FROM photo_smart_collections WHERE create_request_id=?", (payload.create_request_id,))
    if old is not None:
        return photo_smart_collections.create_collection(
            context.service().catalog, payload.name, payload.create_request_id, {},
            request_hash=request_hash, auth_hash=auth_hash, source_hash=source_hash)
    try:
        source = context.resolve_photo_collection_source(payload.source_token, auth_session_id=auth_session_id)
    except ValueError as error:
        raise ApiError(INVALID_CURSOR, str(error), status=409) from error
    rule = photo_smart_collections.rule_from_search(source)
    dependency_signature_before = _photo_smart_dependency_signature(context.service().catalog, rule)
    if source["mode"] == "ranked_candidates":
        session = context.search_sessions.get(str(source["search_session_id"]))
        if session is None:
            raise ApiError(SEARCH_SESSION_EXPIRED, "搜索候选已过期，请重新搜索后创建智能合集", status=409)
        if not session.entries:
            raise ApiError("PHOTO_SMART_EMPTY_SEARCH", "本次搜索没有可保存的候选照片", status=409)
        try:
            seed = PlaceAwareSearchRequestModel.model_validate(source["payload"])
        except Exception as error:
            raise ApiError(INVALID_CURSOR, "搜索凭据无效，请重新搜索", status=409) from error
        validation_request = seed.model_copy(update={
            "search_session_id": session.session_id,
            "cursor": context.search_sessions.encode_cursor(session.session_id, 0),
        })
        await run_catalog_search(context.service().catalog, _place_aware_search_worker,
                                 validation_request, context=context,
                                 data_root=context.service().data_root)
    else:
        try:
            seed = PlaceAwareSearchRequestModel.model_validate(source["payload"])
        except Exception as error:
            raise ApiError(INVALID_CURSOR, "搜索凭据无效，请重新搜索", status=409) from error
        await run_catalog_search(context.service().catalog, _place_aware_search_worker,
                                 seed.model_copy(update={"cursor": None, "search_session_id": None}),
                                 context=context, data_root=context.service().data_root)
    dependency_signature_after = _photo_smart_dependency_signature(context.service().catalog, rule)
    if dependency_signature_after != dependency_signature_before:
        raise ApiError("PHOTO_SMART_DEPENDENCY_CHANGED", "搜索依赖在保存期间发生变化，请重新搜索", status=409)
    result = photo_smart_collections.create_collection(
        context.service().catalog, payload.name, payload.create_request_id, rule,
        request_hash=request_hash, auth_hash=auth_hash, source_hash=source_hash)
    # Reuse the exact live ranked snapshot only after the ordinary search binding check.
    if source["mode"] == "ranked_candidates" and not result["reused"]:
        session = context.search_sessions.get(str(source["search_session_id"]))
        ids = [int(entry.asset_id) for entry in session.entries]
        indexing, truncated = photo_smart_collections.session_coverage(session)
        try:
            result["initial_cache"] = photo_smart_collections.publish_candidates(
                context.service().catalog, int(result["collection"]["smart_id"]), ids,
                signature=dependency_signature_before, truncated=truncated, indexing=indexing)
        except ApiError as error:
            # The rule is already committed. Report that durable success and let the UI
            # queue a safe refresh instead of returning an error that invites a duplicate.
            result["initial_cache"] = {"published": False, "refresh_required": True,
                                       "reason": str(error.code)}
    return result


def _photo_smart_dependency_signature(catalog: Any, rule: dict[str, Any]) -> str:
    """Read cache dependencies together; incomplete indexing is represented separately."""
    with catalog.read_snapshot() as connection:
        signature, _, _ = photo_smart_collections.dependency_signature(connection, rule)
        return signature


@router.post("/photo-smart-collections/{smart_id}/replace-rule", summary="Replace a smart rule from a newly confirmed search")
async def replace_photo_smart_rule(payload: PhotoSmartRuleReplaceRequest, request: Request,
                                   smart_id: int) -> dict[str, Any]:
    context = context_of(request)
    auth_session_id = request.cookies.get(SESSION_COOKIE)
    if not auth_session_id:
        raise ApiError(SESSION_INVALID, "application session is required", status=401)
    auth_hash = hashlib.sha256(auth_session_id.encode()).hexdigest()
    source_hash = hashlib.sha256(payload.source_token.encode()).hexdigest()
    request_hash = hashlib.sha256(json.dumps(
        [int(smart_id), payload.instance_token, int(payload.expected_revision), source_hash],
        separators=(",", ":")).encode()).hexdigest()
    replay = photo_smart_collections.replay_rule_replacement(
        context.service().catalog, payload.request_id, smart_id=smart_id,
        auth_hash=auth_hash, request_hash=request_hash)
    if replay is not None:
        return replay
    try:
        source = context.resolve_photo_collection_source(payload.source_token, auth_session_id=auth_session_id)
    except ValueError as error:
        raise ApiError(INVALID_CURSOR, str(error), status=409) from error
    rule = photo_smart_collections.rule_from_search(source)
    dependency_signature_before = _photo_smart_dependency_signature(context.service().catalog, rule)
    session = None
    if source["mode"] == "ranked_candidates":
        session = context.search_sessions.get(str(source["search_session_id"]))
        if session is None or not session.entries:
            raise ApiError(SEARCH_SESSION_EXPIRED, "搜索候选已过期，请重新搜索后替换", status=409)
        seed = PlaceAwareSearchRequestModel.model_validate(source["payload"])
        validation_request = seed.model_copy(update={"search_session_id": session.session_id,
            "cursor": context.search_sessions.encode_cursor(session.session_id, 0)})
        await run_catalog_search(context.service().catalog, _place_aware_search_worker,
            validation_request, context=context, data_root=context.service().data_root)
    else:
        seed = PlaceAwareSearchRequestModel.model_validate(source["payload"])
        await run_catalog_search(context.service().catalog, _place_aware_search_worker,
            seed.model_copy(update={"cursor": None, "search_session_id": None}),
            context=context, data_root=context.service().data_root)
    dependency_signature_after = _photo_smart_dependency_signature(context.service().catalog, rule)
    if dependency_signature_after != dependency_signature_before:
        raise ApiError("PHOTO_SMART_DEPENDENCY_CHANGED", "搜索依赖在替换规则期间发生变化，请重新搜索", status=409)
    result = photo_smart_collections.replace_rule(context.service().catalog, smart_id,
        instance_token=payload.instance_token, expected_revision=payload.expected_revision,
        rule=rule, request_id=payload.request_id, auth_hash=auth_hash, request_hash=request_hash)
    if result["changed"] and session is not None:
        indexing, truncated = photo_smart_collections.session_coverage(session)
        try:
            result["initial_cache"] = photo_smart_collections.publish_candidates(
                context.service().catalog, smart_id, [int(entry.asset_id) for entry in session.entries],
                signature=dependency_signature_before, truncated=truncated, indexing=indexing,
                expected_rule_revision=int(result["collection"]["rule_revision"]))
        except ApiError as error:
            # Rule replacement is durable even if the old search snapshot can no longer
            # seed its cache; detail/refresh will present the new rule with no valid cache.
            result["initial_cache"] = {"published": False, "refresh_required": True,
                                       "reason": str(error.code)}
    return result


@router.get("/photo-smart-collections/{smart_id}", summary="Read a smart collection and its rule")
async def get_photo_smart_collection(request: Request, smart_id: int,
        instance_token: str | None = Query(default=None, min_length=1, max_length=128)) -> dict[str, Any]:
    return await run_catalog_read(service_of(request).catalog, photo_smart_collections.detail,
                                  smart_id, instance_token=instance_token)


@router.post("/photo-smart-collections/{smart_id}/refresh", summary="Queue one bounded smart collection refresh")
async def post_photo_smart_refresh(payload: PhotoSmartRefreshRequest, request: Request,
                                   smart_id: int) -> dict[str, Any]:
    context = context_of(request)
    catalog = context.service().catalog
    claim = photo_smart_collections.request_refresh(catalog, smart_id,
        instance_token=payload.instance_token, manual=payload.manual)
    if claim["status"] == "QUEUED":
        try:
            context.photo_smart_refresh_runner().submit(
                smart_id, owner=claim["lease_owner"], rule_revision=claim["rule_revision"])
        except Exception as error:
            with catalog.write() as connection:
                connection.execute(
                    "UPDATE photo_smart_cache_state SET refresh_status='FAILED',last_error_code='PHOTO_SMART_WORKER_UNAVAILABLE',lease_owner=NULL,lease_until_ms=NULL WHERE smart_id=? AND lease_owner=?",
                    (int(smart_id), claim["lease_owner"]))
            raise ApiError("PHOTO_SMART_WORKER_UNAVAILABLE", "智能合集刷新暂不可用，请稍后重试", status=503) from error
        return {"status": claim["status"], "refresh_id": claim["refresh_id"],
                "lease_until_ms": claim["lease_until_ms"]}
    return {key: value for key, value in claim.items() if key != "lease_owner"}


@router.patch("/photo-smart-collections/{smart_id}", summary="Rename a smart collection")
async def patch_photo_smart_collection(payload: PhotoSmartCollectionRenameRequest, request: Request,
                                       smart_id: int) -> dict[str, Any]:
    return photo_smart_collections.rename_collection(service_of(request).catalog, smart_id,
        instance_token=payload.instance_token, expected_revision=payload.expected_revision, name=payload.name)


@router.delete("/photo-smart-collections/{smart_id}", summary="Delete a smart collection")
async def delete_photo_smart_collection(payload: PhotoSmartCollectionDeleteRequest, request: Request,
                                        smart_id: int) -> dict[str, Any]:
    return photo_smart_collections.delete_collection(service_of(request).catalog, smart_id,
        instance_token=payload.instance_token, expected_revision=payload.expected_revision)


@router.get("/photo-smart-collections/{smart_id}/exclusions", summary="Page recoverable smart collection exclusions")
async def get_photo_smart_exclusions(request: Request, smart_id: int,
        instance_token: str = Query(min_length=1, max_length=128),
        limit: int = Query(default=50, ge=1, le=50), cursor: str | None = Query(default=None, max_length=2048)) -> dict[str, Any]:
    return await run_catalog_read(service_of(request).catalog, photo_smart_collections.exclusions_page,
        smart_id, instance_token=instance_token, limit=limit, cursor=cursor)


@router.get("/photo-smart-collections/{smart_id}/assets", summary="Page a smart collection's current or cached results")
async def get_photo_smart_assets(request: Request, smart_id: int,
        instance_token: str = Query(min_length=1, max_length=128),
        limit: int = Query(default=50, ge=1, le=50), cursor: str | None = Query(default=None, max_length=2048),
        order: Literal["rank", "capture_desc"] = "rank") -> dict[str, Any]:
    context = context_of(request)
    service = context.service()
    info = await run_catalog_read(service.catalog, photo_smart_collections.detail, smart_id,
                                  instance_token=instance_token)
    collection, rule = info["collection"], info["rule"]
    if collection["execution_kind"] == "FILTER":
        binding = [int(smart_id), str(instance_token), int(collection["rule_revision"]),
                   int(collection["exclusions_revision"]), order]
        position = photo_smart_collections._cursor_decode(cursor, "psf1", binding)
        if position is not None and (not isinstance(position, list) or
                                     (len(position) not in (10, 12) if order == "capture_desc" else
                                      len(position) != 2)):
            raise ApiError(INVALID_CURSOR, "智能合集游标无效，请刷新", status=409)
        inner_session, inner_cursor = position if position and order == "rank" else [None, None]
        capture_position = position if position and order == "capture_desc" else None
        seed_values = dict(rule["request"])
        seed_values["request_id"] = "smart-page-seed-" + secrets.token_hex(8)
        seed = PlaceAwareSearchRequestModel.model_validate(seed_values)
        query = seed.model_copy(update={"request_id": "smart-page-" + secrets.token_hex(8),
            "cursor": inner_cursor if order == "rank" else None,
            "search_session_id": inner_session if order == "rank" else None,
            "page_size": int(limit)})
        page = await run_catalog_search(service.catalog, _place_aware_search_worker, query,
            context=context, data_root=service.data_root, filter_statistics=False,
            filter_exclusion_smart_id=int(smart_id),
            filter_order="capture_desc" if order == "capture_desc" else "asset_id",
            filter_capture_position=capture_position)
        selected = page.get("hits", [])
        ids = [int(hit["asset_id"]) for hit in selected]
        assets = _photo_smart_summaries(service, ids)
        has_more = bool(page.get("has_more"))
        next_position = ([page.get("search_session_id"), page.get("next_cursor")] if order == "rank" else
                         page.get("smart_capture_position"))
        return {"collection": collection, "assets": assets, "hits": selected,
                "has_more": has_more, "next_cursor": photo_smart_collections._cursor_encode(
                    "psf1", binding, next_position) if has_more else None,
                "freshness": "CURRENT", "coverage": "DYNAMIC"}
    stale_browse = info["collection"]["freshness"] in {"STALE", "REFRESHING"}
    if stale_browse:
        if not info.get("build_id"):
            return {"collection": collection, "assets": [], "has_more": False, "next_cursor": None,
                    "freshness": collection["freshness"], "coverage": "BOUNDED",
                    "refresh_required": True}
        cache_rule = service.catalog.read_one(
            "SELECT cache_rule_revision FROM photo_smart_cache_state WHERE smart_id=?", (int(smart_id),),
        )
        if cache_rule is None or int(cache_rule["cache_rule_revision"] or 0) != int(collection["rule_revision"]):
            return {"collection": collection, "assets": [], "has_more": False, "next_cursor": None,
                    "freshness": "STALE", "coverage": "BOUNDED", "refresh_required": True,
                    "rule_replaced": True}
        try:
            seed_values = dict(rule["request"])
            seed_values["request_id"] = "smart-stale-seed-" + secrets.token_hex(8)
            seed = PlaceAwareSearchRequestModel.model_validate(seed_values)
        except Exception as error:
            raise ApiError("PHOTO_SMART_SCOPE_UNVERIFIABLE", "无法验证当前智能合集范围，旧候选不可展示", status=409) from error
        qualification = await run_catalog_search(
            service.catalog, _place_aware_search_worker,
            seed.model_copy(update={"request_id": "smart-stale-qualify-" + secrets.token_hex(8),
                                    "cursor": None, "search_session_id": None}),
            context=context, data_root=service.data_root,
            ranked_candidate_build=(int(smart_id), str(info["build_id"])),
        )
        eligible_ids = [int(hit["asset_id"]) for hit in qualification.get("hits", [])]
        page = await run_catalog_read(
            service.catalog, photo_smart_collections.ranked_assets_page,
            service.media_cache(), smart_id, instance_token=instance_token, limit=limit,
            cursor=cursor, order=order, eligible_asset_ids=eligible_ids,
        )
        if page.get("freshness") != "CURRENT":
            page["stale_label"] = "上次结果·待更新"
        page["refresh_required"] = True
        return page
    return await run_catalog_read(service.catalog, photo_smart_collections.ranked_assets_page,
        service.media_cache(), smart_id, instance_token=instance_token, limit=limit, cursor=cursor, order=order)


@router.post("/photo-smart-collections/{smart_id}/save-static",
             summary="Save a smart collection result as an ordinary collection")
async def save_photo_smart_collection_static(payload: PhotoSmartSaveStaticRequest,
                                             request: Request, smart_id: int) -> dict[str, Any]:
    context = context_of(request)
    auth_session_id = request.cookies.get(SESSION_COOKIE)
    if not auth_session_id:
        raise ApiError(SESSION_INVALID, "application session is required", status=401)
    service = context.service()
    auth_hash = hashlib.sha256(auth_session_id.encode()).hexdigest()
    display_name, _ = photo_collections._name(payload.name)
    payload_hash = hashlib.sha256(json.dumps(
        [display_name, int(smart_id), payload.instance_token, int(payload.expected_revision), payload.order,
         bool(payload.confirm_stale), bool(payload.confirm_truncated), bool(payload.confirm_indexing)],
        ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()
    replay = photo_collections.replay_create_request(
        service.catalog, payload.create_request_id, payload_hash=payload_hash, auth_session_hash=auth_hash,
    )
    if replay is not None:
        return replay
    info = await run_catalog_read(service.catalog, photo_smart_collections.detail, smart_id,
                                  instance_token=payload.instance_token)
    collection, rule = info["collection"], info["rule"]
    if int(collection["revision"]) != int(payload.expected_revision):
        raise ApiError("PHOTO_SMART_REVISION_CONFLICT", "智能合集已变化，请刷新后另存", status=409)
    execution_kind = str(collection["execution_kind"])
    eligible_ids: list[int]
    validation_state: dict[str, Any] | None = None
    if execution_kind == "FILTER":
        try:
            seed_values = dict(rule["request"])
            seed_values["request_id"] = "smart-static-filter-seed-" + secrets.token_hex(8)
            seed = PlaceAwareSearchRequestModel.model_validate(seed_values)
        except Exception as error:
            raise ApiError("PHOTO_SMART_SCOPE_UNVERIFIABLE", "无法验证当前筛选范围，不能另存", status=409) from error
        current = await run_catalog_search(
            service.catalog, _place_aware_search_worker,
            seed.model_copy(update={"request_id": "smart-static-filter-" + secrets.token_hex(8),
                                    "cursor": None, "search_session_id": None}),
            context=context, data_root=service.data_root, filter_authoritative_all=int(smart_id),
            filter_order="capture_desc" if payload.order == "capture_desc" else "asset_id",
        )
        validation_state = current
        eligible_ids = [int(hit["asset_id"]) for hit in current.get("hits", [])]
        if bool(current.get("has_more")) or len(eligible_ids) > photo_collections.MAX_CREATE_IDS:
            raise ApiError("PHOTO_SMART_STATIC_TOO_LARGE", "当前筛选结果超过 5000 张，未创建普通合集", status=409,
                           details={"limit": photo_collections.MAX_CREATE_IDS})
    else:
        freshness = str(collection["freshness"])
        if freshness in {"NO_CACHE", "MODEL_REBIND_REQUIRED", "BLOCKED"} or not info.get("build_id"):
            raise ApiError("PHOTO_SMART_STATIC_CACHE_UNAVAILABLE", "当前没有可验证的已发布结果，不能另存", status=409,
                           details={"freshness": freshness})
        cache_rule = service.catalog.read_one(
            "SELECT cache_rule_revision FROM photo_smart_cache_state WHERE smart_id=?", (int(smart_id),),
        )
        if cache_rule is None or int(cache_rule["cache_rule_revision"] or 0) != int(collection["rule_revision"]):
            raise ApiError("PHOTO_SMART_STATIC_CACHE_UNAVAILABLE", "规则已替换，旧排名候选不能另存", status=409,
                           details={"freshness": freshness})
        needs_stale = freshness in {"STALE", "REFRESHING"}
        needs_truncated = bool(collection.get("truncated"))
        needs_indexing = bool(collection.get("indexing")) or bool(info.get("indexing"))
        required = {"confirm_stale": needs_stale, "confirm_truncated": needs_truncated,
                    "confirm_indexing": needs_indexing}
        if ((needs_stale and not payload.confirm_stale)
                or (needs_truncated and not payload.confirm_truncated)
                or (needs_indexing and not payload.confirm_indexing)):
            raise ApiError("PHOTO_SMART_STATIC_CONFIRMATION_REQUIRED",
                           "请确认要保存上次结果、可能截断的候选或索引未完成时的当前候选快照",
                           status=409, details={"required_confirmations": required,
                                                "candidate_count": int(collection["candidate_count"])})
        try:
            seed_values = dict(rule["request"])
            seed_values["request_id"] = "smart-static-rank-seed-" + secrets.token_hex(8)
            seed = PlaceAwareSearchRequestModel.model_validate(seed_values)
        except Exception as error:
            raise ApiError("PHOTO_SMART_SCOPE_UNVERIFIABLE", "无法验证当前智能合集范围，不能另存", status=409) from error
        qualification = await run_catalog_search(
            service.catalog, _place_aware_search_worker,
            seed.model_copy(update={"request_id": "smart-static-rank-" + secrets.token_hex(8),
                                    "cursor": None, "search_session_id": None}),
            context=context, data_root=service.data_root,
            ranked_candidate_build=(int(smart_id), str(info["build_id"])),
        )
        validation_state = qualification
        eligible_ids = [int(hit["asset_id"]) for hit in qualification.get("hits", [])]
        ordered = await run_catalog_read(
            service.catalog, photo_smart_collections.ranked_assets_page, service.media_cache(), smart_id,
            instance_token=payload.instance_token, limit=photo_smart_collections.MAX_CANDIDATES,
            order=payload.order, eligible_asset_ids=eligible_ids,
        )
        eligible_ids = [int(asset["asset_id"]) for asset in ordered["assets"]]
    source_hash = hashlib.sha256(json.dumps(
        [int(smart_id), payload.instance_token, int(payload.expected_revision)], separators=(",", ":"),
    ).encode()).hexdigest()
    search_scope = None if validation_state is None else validation_state.get("search_scope")
    person_scope = None if validation_state is None else validation_state.get("collection_scope")
    place_scope = None if validation_state is None else validation_state.get("place_scope")
    expected_photo_scope = None if not isinstance(search_scope, dict) or search_scope.get("kind") != "photo_collection" else (
        int(search_scope["photo_collection_id"]), str(search_scope["instance_token"]), int(search_scope["revision"])
    )
    expected_person_scope = None if not isinstance(person_scope, dict) else (
        int(person_scope["collection_id"]), str(person_scope["instance_token"]), int(person_scope["revision"])
    )
    expected_place_scope = None if not isinstance(place_scope, dict) else (
        int(place_scope["suggestion_revision"]), place_scope.get("package_revision")
    )
    return photo_collections.create_collection(
        service.catalog, payload.name, payload.create_request_id, source_hash=source_hash,
        asset_ids=eligible_ids, request_payload_hash=payload_hash,
        auth_session_hash=auth_hash,
        expected_catalog_filter_revision=(None if validation_state is None else
                                           validation_state.get("catalog_filter_revision")),
        expected_favorites_revision=(None if validation_state is None else
                                     validation_state.get("favorites_revision")),
        expected_photo_collection=expected_photo_scope,
        expected_person_collection=expected_person_scope,
        expected_place_scope=expected_place_scope,
        expected_smart_collection=(int(smart_id), payload.instance_token, int(payload.expected_revision),
                                   int(collection["rule_revision"]), int(collection["exclusions_revision"]),
                                   None if execution_kind == "FILTER" else str(info["build_id"])),
    )


def _photo_smart_summaries(service: Any, asset_ids: list[int]) -> list[dict[str, Any]]:
    if not asset_ids:
        return []
    from photo_search.api.queries import locations_by_asset, summarize_asset

    marks = ",".join("?" for _ in asset_ids)
    rows = service.catalog.read_all(
        f"SELECT asset_id,sha256,byte_size,created_at FROM assets WHERE asset_id IN ({marks})", tuple(asset_ids))
    by_id = {int(row["asset_id"]): row for row in rows}
    locations = locations_by_asset(service.catalog, asset_ids)
    return [summarize_asset(service.media_cache(), asset_id=asset_id, sha256=str(by_id[asset_id]["sha256"]),
        byte_size=int(by_id[asset_id]["byte_size"]), created_at=int(by_id[asset_id]["created_at"]),
        locations=locations.get(asset_id, [])) for asset_id in asset_ids if asset_id in by_id]


@router.put("/photo-smart-collections/{smart_id}/exclusions/{asset_id}", summary="Exclude a photo from a smart collection")
async def put_photo_smart_exclusion(payload: PhotoSmartExclusionRequest, request: Request,
                                   smart_id: int, asset_id: int) -> dict[str, Any]:
    return photo_smart_collections.set_exclusion(service_of(request).catalog, smart_id, asset_id,
        instance_token=payload.instance_token, expected_revision=payload.expected_revision, excluded=True)


@router.delete("/photo-smart-collections/{smart_id}/exclusions/{asset_id}", summary="Restore a smart collection exclusion")
async def delete_photo_smart_exclusion(payload: PhotoSmartExclusionRequest, request: Request,
                                      smart_id: int, asset_id: int) -> dict[str, Any]:
    return photo_smart_collections.set_exclusion(service_of(request).catalog, smart_id, asset_id,
        instance_token=payload.instance_token, expected_revision=payload.expected_revision, excluded=False)


@router.get("/photo-collections/{collection_id}/assets", summary="Page ordinary photo collection members")
async def get_photo_collection_assets(request: Request, collection_id: int,
        instance_token: str = Query(min_length=1, max_length=128),
        limit: int = Query(default=50, ge=1, le=50), cursor: str | None = Query(default=None, max_length=2048)) -> dict[str, Any]:
    service = service_of(request)
    return await run_catalog_read(service.catalog, photo_collections.assets_page, service.media_cache(),
        collection_id, instance_token=instance_token, limit=limit, cursor=cursor)


@router.post("/photo-collections/{collection_id}/members/batch", summary="Edit ordinary photo collection members")
async def edit_photo_collection_members(payload: PhotoCollectionMembersRequest, request: Request,
                                       collection_id: int) -> dict[str, Any]:
    return photo_collections.edit_members(service_of(request).catalog, collection_id, payload.asset_ids,
        action=payload.action, instance_token=payload.instance_token, expected_revision=payload.expected_revision)


@router.post("/photo-collections/from-search", summary="Save an authoritative search result in a new collection")
async def create_photo_collection_from_search(payload: PhotoCollectionFromSearchRequest,
                                              request: Request) -> dict[str, Any]:
    context = context_of(request)
    auth_session_id = request.cookies.get(SESSION_COOKIE)
    if not auth_session_id:
        raise ApiError(SESSION_INVALID, "application session is required", status=401)
    replay = photo_collections.replay_search_create(
        context.service().catalog, payload.create_request_id, payload.name,
        payload.source_token, auth_session_id,
    )
    if replay is not None:
        return replay
    try:
        source = context.resolve_photo_collection_source(payload.source_token, auth_session_id=auth_session_id)
    except ValueError as error:
        raise ApiError(INVALID_CURSOR, str(error), status=409) from error
    if source["mode"] != payload.mode:
        raise ApiError(INVALID_CURSOR, "保存模式与搜索来源不符，请重新搜索", status=409)
    ids: list[int] = []
    expected_catalog_revision = source.get("catalog_filter_revision")
    place_scope = source.get("place_scope")
    expected_place_scope = None if not isinstance(place_scope, dict) else (
        int(place_scope["suggestion_revision"]), place_scope.get("package_revision")
    )
    expected_favorites_revision = source.get("favorites_revision")
    expected_photo_collection = None
    scope = source.get("search_scope")
    if isinstance(scope, dict) and scope.get("kind") == "photo_collection":
        expected_photo_collection = (int(scope["photo_collection_id"]), str(scope["instance_token"]), int(scope["revision"]))
    collection_scope = source.get("collection_scope")
    expected_person_collection = None if not isinstance(collection_scope, dict) else (
        int(collection_scope["collection_id"]), str(collection_scope["instance_token"]), int(collection_scope["revision"])
    )
    if payload.mode == "ranked_candidates":
        session = context.search_sessions.get(str(source["search_session_id"]))
        if session is None:
            raise ApiError(SEARCH_SESSION_EXPIRED, "搜索候选已过期，请重新搜索后保存", status=409)
        if not session.entries:
            raise ApiError("PHOTO_COLLECTION_EMPTY_SEARCH", "本次搜索没有可保存的候选照片", status=409)
        try:
            seed = PlaceAwareSearchRequestModel.model_validate(source["payload"])
        except Exception as error:
            raise ApiError(INVALID_CURSOR, "搜索保存凭据与原始请求不匹配，请重新搜索", status=409) from error
        validation_request = seed.model_copy(update={
            "search_session_id": session.session_id,
            "cursor": context.search_sessions.encode_cursor(session.session_id, 0),
        })
        # Re-enter the authoritative continuation path with a valid position-zero cursor.
        # It checks profile/generation, catalog and suggestion revisions, and place/person/
        # photo-collection scope bindings without running retrieval for another candidate set.
        await run_catalog_search(service_of(request).catalog, _place_aware_search_worker,
                                 validation_request, context=context,
                                 data_root=context.service().data_root)
        if bool(session.candidates_truncated) and not payload.confirm_truncated:
            raise ApiError("PHOTO_COLLECTION_TRUNCATION_CONFIRMATION_REQUIRED",
                           "候选可能被截断；确认后将保存当前候选快照", status=409,
                           details={"candidate_count": len(session.entries), "candidates_truncated": True})
        ids = [int(entry.asset_id) for entry in session.entries]
        expected_catalog_revision = int(session.catalog_filter_revision)
        expected_favorites_revision = session.extra.get("favorites_revision")
    else:
        service = context.service()
        try:
            seed = PlaceAwareSearchRequestModel.model_validate(source["payload"])
        except Exception as error:
            raise ApiError(INVALID_CURSOR, "保存搜索来源已损坏，请重新搜索", status=409) from error
        cursor = None
        search_session_id = str(source["search_session_id"])
        first_page = True
        while True:
            page_payload = seed.model_copy(update={"cursor": cursor, "search_session_id": None if first_page else search_session_id})
            page = await run_catalog_search(service.catalog, _place_aware_search_worker, page_payload,
                                            context=context, data_root=service.data_root,
                                            filter_statistics=first_page)
            if page.get("catalog_filter_revision") != expected_catalog_revision:
                raise ApiError(CATALOG_FILTER_REVISION_STALE, "搜索筛选已变化，请重新搜索后保存", status=409)
            if not _photo_collection_source_scope_matches(source, page):
                raise ApiError(CATALOG_FILTER_REVISION_STALE, "地点或人物合集搜索范围已变化，请重新搜索后保存", status=409)
            ids.extend(int(hit["asset_id"]) for hit in page.get("hits", []))
            total_candidates = page.get("total_candidates")
            if len(ids) > 5000 or (total_candidates is not None and int(total_candidates) > 5000):
                raise ApiError("PHOTO_COLLECTION_FILTER_RESULT_TOO_LARGE",
                               "全部筛选结果超过 5000 张，本次未创建合集；请细化筛选或分批选择", status=409,
                               details={"limit": 5000, "observed_at_least": max(5001, len(ids))})
            if not page.get("has_more"):
                break
            cursor = page.get("next_cursor")
            if not cursor:
                raise ApiError(INVALID_CURSOR, "筛选结果续页凭据缺失，请重新搜索", status=409)
            first_page = False
            expected_favorites_revision = page.get("favorites_revision", expected_favorites_revision)
            if len(ids) + int(page.get("page_size", 0)) > 5000:
                raise ApiError("PHOTO_COLLECTION_FILTER_RESULT_TOO_LARGE",
                               "全部筛选结果超过 5000 张，本次未创建合集；请细化筛选或分批选择", status=409,
                               details={"limit": 5000, "observed_at_least": 5001})
        if not ids:
            raise ApiError("PHOTO_COLLECTION_EMPTY_SEARCH", "本次搜索没有可保存的照片", status=409)
    token_hash = hashlib.sha256(payload.source_token.encode()).hexdigest()
    source_identity = hashlib.sha256(json.dumps(
        ["photo_collection_search", token_hash], separators=(",", ":")
    ).encode()).hexdigest()
    return photo_collections.create_collection(
        context.service().catalog, payload.name, payload.create_request_id,
        source_hash=source_identity, asset_ids=ids,
        request_payload_hash=photo_collections.create_request_payload_hash(payload.name, source_identity),
        auth_session_hash=hashlib.sha256(auth_session_id.encode()).hexdigest(),
        source_token_hash=token_hash,
        expected_catalog_filter_revision=None if expected_catalog_revision is None else int(expected_catalog_revision),
        expected_favorites_revision=None if expected_favorites_revision is None else int(expected_favorites_revision),
        expected_photo_collection=expected_photo_collection,
        expected_person_collection=expected_person_collection,
        expected_place_scope=expected_place_scope,
    )


def _photo_collection_source_scope_matches(source: dict[str, Any], page: dict[str, Any]) -> bool:
    """Compare every separately versioned search scope captured by the source token."""
    for key in ("place_scope", "collection_scope"):
        expected, current = source.get(key), page.get(key)
        if expected is None or current is None:
            if expected is not current:
                return False
            continue
        if not isinstance(expected, dict) or not isinstance(current, dict):
            return False
        fields = ("kind", "key", "collection_id", "instance_token", "revision",
                  "catalog_filter_revision", "suggestion_revision", "package_revision")
        if any(expected.get(field) != current.get(field) for field in fields):
            return False
    return True


@router.post("/photo-collections/{collection_id}/members/from-search",
             summary="Append an authoritative search result to an existing photo collection")
async def append_photo_collection_from_search(payload: PhotoCollectionAppendFromSearchRequest,
                                             request: Request, collection_id: int) -> dict[str, Any]:
    context = context_of(request)
    auth_session_id = request.cookies.get(SESSION_COOKIE)
    if not auth_session_id:
        raise ApiError(SESSION_INVALID, "application session is required", status=401)
    service = context.service()
    replay = photo_collections.replay_search_append(
        service.catalog, payload.request_id, collection_id=collection_id,
        instance_token=payload.instance_token, source_token=payload.source_token,
        mode=payload.mode, confirm_truncated=payload.confirm_truncated,
        auth_session_id=auth_session_id,
    )
    if replay is not None:
        return replay
    try:
        source = context.resolve_photo_collection_source(payload.source_token, auth_session_id=auth_session_id)
    except ValueError as error:
        raise ApiError(INVALID_CURSOR, str(error), status=409) from error
    if source["mode"] != payload.mode:
        raise ApiError(INVALID_CURSOR, "保存模式与搜索来源不符，请重新搜索", status=409)
    scope = source.get("search_scope")
    expected_photo_collection = None
    if isinstance(scope, dict) and scope.get("kind") == "photo_collection":
        expected_photo_collection = (int(scope["photo_collection_id"]), str(scope["instance_token"]), int(scope["revision"]))
    expected_catalog_revision = source.get("catalog_filter_revision")
    place_scope = source.get("place_scope")
    expected_place_scope = None if not isinstance(place_scope, dict) else (
        int(place_scope["suggestion_revision"]), place_scope.get("package_revision")
    )
    expected_favorites_revision = source.get("favorites_revision")
    person_scope = source.get("collection_scope")
    expected_person_collection = None if not isinstance(person_scope, dict) else (
        int(person_scope["collection_id"]), str(person_scope["instance_token"]), int(person_scope["revision"])
    )
    ids: list[int] = []
    if payload.mode == "ranked_candidates":
        session = context.search_sessions.get(str(source["search_session_id"]))
        if session is None:
            raise ApiError(SEARCH_SESSION_EXPIRED, "搜索候选已过期，请重新搜索后保存", status=409)
        if not session.entries:
            raise ApiError("PHOTO_COLLECTION_EMPTY_SEARCH", "本次搜索没有可保存的候选照片", status=409)
        try:
            seed = PlaceAwareSearchRequestModel.model_validate(source["payload"])
        except Exception as error:
            raise ApiError(INVALID_CURSOR, "搜索保存凭据与原始请求不匹配，请重新搜索", status=409) from error
        validation_request = seed.model_copy(update={
            "search_session_id": session.session_id,
            "cursor": context.search_sessions.encode_cursor(session.session_id, 0),
        })
        await run_catalog_search(service.catalog, _place_aware_search_worker, validation_request,
                                 context=context, data_root=service.data_root)
        if bool(session.candidates_truncated) and not payload.confirm_truncated:
            raise ApiError("PHOTO_COLLECTION_TRUNCATION_CONFIRMATION_REQUIRED",
                           "候选可能被截断；确认后将追加当前候选快照", status=409,
                           details={"candidate_count": len(session.entries), "candidates_truncated": True})
        ids = [int(entry.asset_id) for entry in session.entries]
        expected_catalog_revision = int(session.catalog_filter_revision)
        expected_favorites_revision = session.extra.get("favorites_revision")
    else:
        try:
            seed = PlaceAwareSearchRequestModel.model_validate(source["payload"])
        except Exception as error:
            raise ApiError(INVALID_CURSOR, "保存搜索来源已损坏，请重新搜索", status=409) from error
        cursor = None
        first_page = True
        while True:
            page_payload = seed.model_copy(update={"cursor": cursor,
                "search_session_id": None if first_page else str(source["search_session_id"])})
            page = await run_catalog_search(service.catalog, _place_aware_search_worker, page_payload,
                                            context=context, data_root=service.data_root,
                                            filter_statistics=first_page)
            if page.get("catalog_filter_revision") != expected_catalog_revision:
                raise ApiError(CATALOG_FILTER_REVISION_STALE, "搜索筛选已变化，请重新搜索后保存", status=409)
            if not _photo_collection_source_scope_matches(source, page):
                raise ApiError(CATALOG_FILTER_REVISION_STALE, "地点或人物合集搜索范围已变化，请重新搜索后保存", status=409)
            ids.extend(int(hit["asset_id"]) for hit in page.get("hits", []))
            total_candidates = page.get("total_candidates")
            if len(ids) > 5000 or (total_candidates is not None and int(total_candidates) > 5000):
                raise ApiError("PHOTO_COLLECTION_FILTER_RESULT_TOO_LARGE",
                               "全部筛选结果超过 5000 张，本次未追加；请细化筛选或分批选择", status=409,
                               details={"limit": 5000, "observed_at_least": max(5001, len(ids))})
            if not page.get("has_more"):
                break
            cursor = page.get("next_cursor")
            if not cursor:
                raise ApiError(INVALID_CURSOR, "筛选结果续页凭据缺失，请重新搜索", status=409)
            first_page = False
            expected_favorites_revision = page.get("favorites_revision", expected_favorites_revision)
    if not ids:
        raise ApiError("PHOTO_COLLECTION_EMPTY_SEARCH", "本次搜索没有可追加的照片", status=409)
    append_payload_hash = photo_collections.append_request_payload_hash(
        collection_id, payload.instance_token, payload.source_token,
        payload.mode, payload.confirm_truncated,
    )
    return photo_collections.edit_members(
        service.catalog, collection_id, ids, action="add", instance_token=payload.instance_token,
        expected_revision=payload.expected_revision, max_batch=5000,
        expected_catalog_filter_revision=None if expected_catalog_revision is None else int(expected_catalog_revision),
        expected_favorites_revision=None if expected_favorites_revision is None else int(expected_favorites_revision),
        expected_photo_collection=expected_photo_collection,
        expected_person_collection=expected_person_collection,
        expected_place_scope=expected_place_scope,
        idempotency=(payload.request_id, hashlib.sha256(auth_session_id.encode()).hexdigest(), append_payload_hash),
    )


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
    parameters = dict(
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
    projection = None
    if text is not None:
        projection = await run_catalog_projection(
            service.catalog, prepare_asset_page, **parameters
        )
    return await run_catalog_read(
        service.catalog, read_asset_page, service.media_cache(),
        projection=projection, **parameters,
    )


@router.get("/timeline/assets", summary="Read-only, scope-bound timeline page")
async def list_timeline_assets(
    request: Request,
    kind: str = "all",
    source_id: int | None = Query(default=None, ge=1),
    person_id: int | None = Query(default=None, ge=1),
    collection_id: int | None = Query(default=None, ge=1),
    photo_collection_id: int | None = Query(default=None, ge=1),
    photo_collection_instance: str | None = Query(default=None, min_length=1, max_length=128),
    photo_collection_revision: int | None = Query(default=None, ge=1),
    place_kind: Literal["city", "province", "district", "country"] | None = None,
    place_key: str | None = Query(default=None, min_length=1, max_length=256),
    time_basis: Literal["capture", "library"] = "capture",
    group_by: Literal["day", "month", "year"] = "day",
    bucket: Literal["all", "capture", "unknown", "pending"] = "all",
    year: int | None = Query(default=None, ge=1, le=9999),
    month: int | None = Query(default=None, ge=1, le=12),
    seek_year: int | None = Query(default=None, ge=1, le=9999),
    seek_month: int | None = Query(default=None, ge=1, le=12),
    cursor: str | None = Query(default=None, max_length=2048),
    anchor: str | None = Query(default=None, max_length=2048),
    limit: int | None = Query(default=None, ge=1, le=200),
) -> dict[str, Any]:
    service = service_of(request)
    return await run_catalog_read(
        service.catalog, timeline_queries.timeline_page,
        kind=kind,
        source_id=source_id,
        person_id=person_id,
        collection_id=collection_id,
        photo_collection_id=photo_collection_id,
        photo_collection_instance=photo_collection_instance,
        photo_collection_revision=photo_collection_revision,
        place_kind=place_kind,
        place_key=place_key,
        data_root=service.data_root,
        time_basis=time_basis,
        group_by=group_by,
        bucket=bucket,
        year=year,
        month=month,
        seek_year=seek_year,
        seek_month=seek_month,
        cursor=cursor,
        anchor=anchor,
        limit=limit,
    )


@router.get("/timeline/periods", summary="Read-only, scoped timeline date summary")
async def get_timeline_periods(
    request: Request,
    kind: str = "all",
    source_id: int | None = Query(default=None, ge=1),
    person_id: int | None = Query(default=None, ge=1),
    collection_id: int | None = Query(default=None, ge=1),
    photo_collection_id: int | None = Query(default=None, ge=1),
    photo_collection_instance: str | None = Query(default=None, min_length=1, max_length=128),
    photo_collection_revision: int | None = Query(default=None, ge=1),
    place_kind: Literal["city", "province", "district", "country"] | None = None,
    place_key: str | None = Query(default=None, min_length=1, max_length=256),
    time_basis: Literal["capture", "library"] = "capture",
    year: int | None = Query(default=None, ge=1, le=9999),
) -> dict[str, Any]:
    service = service_of(request)
    return await run_catalog_read(
        service.catalog, timeline_queries.timeline_periods,
        kind=kind,
        source_id=source_id,
        person_id=person_id,
        collection_id=collection_id,
        photo_collection_id=photo_collection_id,
        photo_collection_instance=photo_collection_instance,
        photo_collection_revision=photo_collection_revision,
        place_kind=place_kind,
        place_key=place_key,
        data_root=service.data_root,
        time_basis=time_basis,
        year=year,
    )


@router.get("/assets/{asset_id}/admin-place", summary="Current administrative facets and exclusion reason")
async def get_asset_admin_place(request: Request, asset_id: int) -> dict[str, Any]:
    return cn_facets.asset_projection(context_of(request).service().catalog, asset_id)


@router.get("/assets/{asset_id}", response_model=AssetDetail, summary="Asset detail")
async def get_asset(request: Request, asset_id: int) -> dict[str, Any]:
    context = context_of(request)
    service = context.service()
    detail = asset_detail(
        service.catalog, service.media_cache(), int(asset_id), redactor=context.redactor()
    )
    if detail["width"] is None or detail["height"] is None:
        location = resolve_optional_location(service.catalog, int(asset_id))
        if location is not None:
            observed = {
                "location_id": location.location_id,
                "size": location.size,
                "mtime_ns": location.mtime_ns,
            }
            try:
                require_unchanged(location.absolute_path, observed)
                dimensions = read_normalized_dimensions(location.absolute_path)
                require_unchanged(location.absolute_path, observed)
            except CatalogError:
                dimensions = None
            if dimensions is not None:
                detail["width"], detail["height"] = dimensions
    return detail


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


@router.post("/assets/{asset_id}/local-action", summary="Request a Windows action on a registered original")
async def asset_local_action(
    request: Request, asset_id: int, payload: AssetLocalActionRequest
) -> dict[str, str]:
    """Open or reveal exactly one validated, registered original on the local desktop."""
    settings = context_of(request).settings
    peer = request.client.host if request.client is not None else None
    try:
        peer_is_loopback = bool(peer) and ipaddress.ip_address(str(peer).strip("[]")).is_loopback
    except ValueError:
        peer_is_loopback = False
    if not settings.loopback_only or not peer_is_loopback:
        raise ApiError(
            "LOCAL_ACTION_UNAVAILABLE",
            "local desktop actions are unavailable for this connection",
            status=403,
        )
    if not _desktop_supported():
        raise ApiError("LOCAL_ACTION_UNAVAILABLE", "local desktop actions are available only on Windows", status=409)

    service = service_of(request)
    location = resolve_registered_location(service.catalog, int(asset_id), payload.location_id)
    try:
        await asyncio.to_thread(perform_local_action, location, payload.action)
    except ApiError:
        raise
    except OSError as error:
        raise ApiError(
            "LOCAL_ACTION_FAILED",
            "Windows could not start the requested file action",
            status=409,
        ) from error
    return {"status": "accepted", "action": payload.action}


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
        await asyncio.to_thread(_generate_one_in_worker, service, asset_id, kind)
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
    _generate_one_core(service, asset_id, kind)


def _generate_one_in_worker(service: CatalogService, asset_id: int, kind: str) -> None:
    """Generate on a worker with its own read-only SQLite connection."""
    _generate_one_in_worker_core(
        service, asset_id, kind, generator=_generate_one
    )


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


@router.get("/places/choices", summary="Source-backed choices for manual place selection")
async def list_place_choices(
    request: Request, name: str = Query(min_length=1, max_length=200)
) -> dict[str, Any]:
    service = service_of(request)
    values = place_choices.choices(service.catalog, text=name, data_root=service.data_root)
    return {"choices": values, "catalog_filter_revision": place_revision.read_filter_revision(service.catalog.connection),
            "package_revision": cn_facets.revisions(service.catalog)[2]}


@router.get("/places/albums", summary="Photo albums grouped by current GPS or manual place")
async def list_place_albums(
    request: Request,
    kind: Literal["city", "province", "country", "district"] = "city",
    name: str | None = Query(default=None, max_length=200),
) -> dict[str, Any]:
    service = service_of(request)
    return await run_catalog_read(service.catalog, place_albums.albums,
                                  service.data_root, kind=kind, name=name)


@router.post("/places/albums/backfill", summary="Fill missing GPS and nearby-place results in one bounded batch")
async def backfill_place_albums(request: Request) -> dict[str, Any]:
    service = service_of(request)
    return place_backfill.run(service.catalog, service.data_root)


@router.get("/places/albums/{kind}/{key}/assets", summary="Paged photos in one place album")
async def list_place_album_assets(
    request: Request,
    kind: Literal["city", "province", "country", "district"],
    key: str,
    limit: int = 48,
    cursor: str | None = None,
) -> dict[str, Any]:
    service = service_of(request)
    return await run_catalog_read(
        service.catalog, place_albums.album_assets, service.data_root,
        kind=kind, key=key, limit=limit, cursor=cursor
    )


@router.get("/places/admin/capabilities", summary="Versioned Chinese administrative facet capability")
async def cn_admin_capabilities(request: Request) -> dict[str, Any]:
    service = service_of(request)
    rows = cn_facets.choices(service.catalog)
    active = service.catalog.read_one("SELECT dataset_id FROM active_geo_dataset WHERE singleton=1")
    run = service.catalog.read_one("SELECT * FROM cn_update_runs WHERE dataset_id=?", (active["dataset_id"],))
    return {"contract_version": "cn-admin-facets.v1", "available": bool(rows),
            "kinds": ["province", "city", "district"], "manual_district_assignment": bool(rows),
            "dataset_id": active["dataset_id"], "update": None if run is None else dict(run),
            "package_revision": cn_facets.revisions(service.catalog)[2],
            "records": len(rows)}


@router.post("/places/admin/update", summary="Resolve one bounded batch of existing GPS, without reading photos")
async def cn_admin_update(request: Request, restart: bool = False, dataset_id: str | None = None) -> dict[str, Any]:
    service = service_of(request)
    active = service.catalog.read_one("SELECT g.* FROM geo_datasets g JOIN active_geo_dataset a ON a.dataset_id=g.dataset_id WHERE a.singleton=1")
    if active is None:
        raise ApiError("PLACE_DATASET_MISSING", "no active place package", status=409)
    content = await asyncio.to_thread(geo_resolver.load_content_snapshot, dict(active), data_root=service.data_root)
    if dataset_id is not None and dataset_id != content.dataset_id:
        raise ApiError(CATALOG_FILTER_REVISION_STALE, "the Chinese place package changed", status=409)
    run, rows = cn_facets.inputs(service.catalog, content, restart=restart)
    results = await asyncio.to_thread(cn_facets.calculate, content, rows)
    return cn_facets.publish(service.catalog, content, run, rows, results)


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
    service = service_of(request)
    snapshot = place_datasets_module.status_snapshot(service.catalog.connection)
    result = await asyncio.to_thread(place_datasets_module.render_status_snapshot, snapshot, data_root=service.data_root)
    if place_datasets_module.status_snapshot(service.catalog.connection) != snapshot:
        raise ApiError(CATALOG_FILTER_REVISION_STALE, "the place package changed; reload", status=409)
    return result


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
        target_place_ref=payload.target_place_ref,
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
            "target_place_ref": item.target_place_ref,
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
    "/person-operations/retention-preview",
    summary="Preview expired person-operation snapshots eligible for manual cleanup",
)
async def person_operation_retention_preview(request: Request) -> dict[str, Any]:
    return persons_of(request).retention_preview()


@router.post(
    "/person-operations/retention-cleanup",
    summary="Manually remove one bounded batch of expired person-operation snapshots",
)
async def person_operation_retention_cleanup(
    request: Request, payload: PersonOperationRetentionCleanupRequest
) -> dict[str, Any]:
    return persons_of(request).retention_cleanup(
        after_operation_revision=payload.after_operation_revision
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


@router.get(
    "/assets/{asset_id}/face-review",
    response_model=AssetFaceReviewResponse,
    summary="Bounded OPEN face reviews of one photo",
)
async def asset_face_review(
    request: Request, asset_id: int, limit: str | None = None, cursor: str | None = None
) -> dict[str, Any]:
    if asset_id < 1:
        raise ApiError("VALIDATION_ERROR", "asset_id must be positive", status=422)
    return persons_of(request).asset_mapping_page(asset_id, limit=limit, cursor=cursor)


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
    sort: Literal["person_id", "photos_desc", "photos_recent_desc"] = "person_id",
    visibility: Literal["all", "visible", "hidden"] = "all",
    ignore_state: Literal["TEMPORARY", "EXCLUDED"] | None = None,
    quality_view: Literal["all", "clean"] = "all",
    collection_state: Literal["all", "none"] = "all",
    limit: int | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    return await run_catalog_read(
        service_of(request).catalog, person_albums.people_page,
        kind=kind,
        q=q,
        sort=sort,
        limit=clamp_person_limit(limit),
        cursor=cursor,
        visibility=visibility,
        ignore_state=ignore_state,
        quality_view=quality_view,
        collection_state=collection_state,
    )


@router.get("/persons/collections", summary="List personal person collections")
async def list_person_collections(
    request: Request, limit: int = 40, cursor: str | None = None
) -> dict[str, Any]:
    return await run_catalog_read(
        service_of(request).catalog, person_collections.list_collections, limit=limit, cursor=cursor
    )


@router.post("/persons/collections", summary="Create a personal person collection")
async def create_person_collection(
    request: Request, payload: PersonCollectionNameRequest
) -> dict[str, Any]:
    return person_collections.create_collection(service_of(request).catalog, payload.name)


@router.get("/persons/collections/{collection_id}/{instance_token}", summary="Read a person collection instance")
async def get_person_collection(
    request: Request, collection_id: int, instance_token: str
) -> dict[str, Any]:
    return await run_catalog_read(
        service_of(request).catalog, person_collections.collection_detail, collection_id, instance_token=instance_token
    )


@router.patch("/persons/collections/{collection_id}/{instance_token}", summary="Rename a person collection")
async def patch_person_collection(
    request: Request, collection_id: int, instance_token: str, payload: PersonCollectionNameRequest
) -> dict[str, Any]:
    return person_collections.rename_collection(
        service_of(request).catalog, collection_id, payload.name, instance_token=instance_token
    )


@router.delete("/persons/collections/{collection_id}/{instance_token}", summary="Delete a person collection instance")
async def remove_person_collection(
    request: Request, collection_id: int, instance_token: str
) -> dict[str, Any]:
    return person_collections.delete_collection(
        service_of(request).catalog, collection_id, instance_token=instance_token
    )


@router.post("/persons/collections/{collection_id}/{instance_token}/members", summary="Add or remove collection members")
async def edit_person_collection_members(
    request: Request, collection_id: int, instance_token: str, payload: PersonCollectionMembersRequest
) -> dict[str, Any]:
    return person_collections.edit_members(
        service_of(request).catalog,
        collection_id,
        instance_token=instance_token,
        action=payload.action,
        person_ids=payload.person_ids,
        hide_after_add=payload.hide_after_add,
    )


@router.get("/persons/collections/{collection_id}/{instance_token}/faces", summary="Page saved faces in a collection")
async def person_collection_faces(
    request: Request, collection_id: int, instance_token: str, limit: int = 40, cursor: str | None = None
) -> dict[str, Any]:
    return await run_catalog_read(
        service_of(request).catalog, person_collections.face_members_page,
        collection_id, instance_token=instance_token, limit=limit, cursor=cursor
    )


@router.post("/persons/collections/{collection_id}/{instance_token}/faces", summary="Add or remove saved face members")
async def edit_person_collection_faces(
    request: Request, collection_id: int, instance_token: str, payload: PersonCollectionFaceMembersRequest
) -> dict[str, Any]:
    return person_collections.edit_face_members(
        service_of(request).catalog, collection_id, instance_token=instance_token,
        action=payload.action, face_ids=payload.face_ids, expected_revision=payload.expected_revision,
        hide_after_add=payload.hide_after_add,
    )


@router.get("/persons/collections/{collection_id}/{instance_token}/people", summary="Page the people in a collection")
async def person_collection_people(
    request: Request, collection_id: int, instance_token: str, limit: int = 40, cursor: str | None = None
) -> dict[str, Any]:
    return await run_catalog_read(
        service_of(request).catalog, person_collections.people_page, collection_id, instance_token=instance_token, limit=limit, cursor=cursor
    )


@router.get("/persons/collections/{collection_id}/{instance_token}/photos", summary="Page collection photos")
async def person_collection_photos(
    request: Request, collection_id: int, instance_token: str, limit: int = 60, cursor: str | None = None
) -> dict[str, Any]:
    service = service_of(request)
    return await run_catalog_read(
        service.catalog, person_collections.photo_page, service.media_cache(), collection_id,
        instance_token=instance_token, limit=limit, cursor=cursor
    )


@router.post(
    "/persons/visibility",
    response_model=PersonVisibilityResponse,
    summary="Hide or restore selected people in album lists",
)
async def post_person_visibility(
    request: Request, payload: PersonVisibilityRequest
) -> dict[str, Any]:
    return persons_of(request).set_visibility(
        person_ids=payload.person_ids, hidden=bool(payload.hidden), ignore_state=payload.ignore_state
    )


@router.get(
    "/persons/{person_id}/photos",
    summary="All effective photos of one person, without a semantic model",
)
async def person_photos(
    request: Request, person_id: int, limit: int | None = None, cursor: str | None = None
) -> dict[str, Any]:
    service = service_of(request)
    return await run_catalog_read(
        service.catalog, person_albums.photo_page,
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
    request: Request,
    limit: int | None = None,
    cursor: str | None = None,
    kind: Literal["all", "suggested", "quality", "manual", "pending"] = "all",
    quality_view: Literal["all", "clean"] = "all",
) -> dict[str, Any]:
    return person_albums.inbox_page(
        service_of(request).catalog, limit=clamp_person_limit(limit), cursor=cursor, kind=kind, quality_view=quality_view
    )


@router.get("/faces/inbox/hidden", summary="Read a page of explicitly hidden inbox faces")
async def face_inbox_hidden(
    request: Request, limit: int | None = None, cursor: str | None = None,
    ignore_state: Literal["TEMPORARY", "EXCLUDED"] | None = None,
) -> dict[str, Any]:
    return person_albums.inbox_hidden_page(
        service_of(request).catalog, limit=clamp_person_limit(limit), cursor=cursor, ignore_state=ignore_state
    )


@router.get("/faces/inbox/hidden/groups", summary="Read grouped ignored inbox faces")
async def face_inbox_hidden_groups(
    request: Request, limit: int | None = None, cursor: str | None = None,
    ignore_state: Literal["TEMPORARY", "EXCLUDED"] | None = None,
) -> dict[str, Any]:
    return person_albums.inbox_hidden_group_page(
        service_of(request).catalog, limit=min(clamp_person_limit(limit), 30), cursor=cursor,
        ignore_state=ignore_state,
    )


@router.get(
    "/faces/inbox/hidden/groups/{group_key}",
    summary="Read one ignored-face candidate group's faces",
)
async def face_inbox_hidden_group_faces(
    request: Request, group_key: str, limit: int | None = None, cursor: str | None = None,
    ignore_state: Literal["TEMPORARY", "EXCLUDED"] | None = None,
) -> dict[str, Any]:
    return person_albums.inbox_hidden_group_faces(
        service_of(request).catalog,
        group_key=group_key,
        limit=min(clamp_person_limit(limit), 30),
        cursor=cursor,
        ignore_state=ignore_state,
    )


@router.post(
    "/faces/inbox/visibility",
    response_model=FaceInboxVisibilityResponse,
    summary="Hide or restore selected detected faces in the personal inbox",
)
async def post_face_inbox_visibility(
    request: Request, payload: FaceInboxVisibilityRequest
) -> dict[str, Any]:
    return person_albums.set_inbox_visibility(
        service_of(request).catalog, face_ids=payload.face_ids, hidden=bool(payload.hidden),
        ignore_state=payload.ignore_state,
    )


@router.get("/faces/inbox/groups", summary="Read globally ordered candidate groups and single faces")
async def face_inbox_groups(
    request: Request,
    limit: int | None = None,
    cursor: str | None = None,
    kind: Literal["all", "suggested", "quality", "manual", "pending"] = "all",
    quality_view: Literal["all", "clean"] = "all",
) -> dict[str, Any]:
    return person_albums.inbox_group_page(
        service_of(request).catalog, limit=min(clamp_person_limit(limit), 30), cursor=cursor, kind=kind, quality_view=quality_view
    )


@router.get("/faces/inbox/groups/{group_key}", summary="Read one candidate group's pending faces")
async def face_inbox_group_faces(
    request: Request,
    group_key: str,
    limit: int | None = None,
    cursor: str | None = None,
    kind: Literal["all", "suggested", "quality", "manual", "pending"] = "all",
    quality_view: Literal["all", "clean"] = "all",
) -> dict[str, Any]:
    return person_albums.inbox_group_faces(
        service_of(request).catalog,
        group_key=group_key,
        limit=min(clamp_person_limit(limit), 30),
        cursor=cursor,
        kind=kind,
        quality_view=quality_view,
    )


@router.get("/faces/inbox/summary", summary="Safe counts of face organization reasons")
async def face_inbox_summary(
    request: Request, quality_view: Literal["all", "clean"] = "all"
) -> dict[str, Any]:
    return person_albums.inbox_summary(service_of(request).catalog, quality_view=quality_view)


@router.post("/faces/rematch", summary="Reconsider one batch of unassigned stored faces")
async def face_rematch(request: Request, payload: FaceRematchRequest) -> dict[str, Any]:
    from photo_search.face.service import rematch_unassigned

    result = rematch_unassigned(
        service_of(request).catalog,
        limit=payload.limit,
        after_asset_id=payload.after_asset_id,
        publication_limit=400,
    )
    # Internal association decisions contain similarity; only counts cross the API.
    return {
        key: result[key]
        for key in (
            "selected_assets",
            "processed",
            "merged",
            "new_groups",
            "suggested",
            "deferred",
            "has_more",
            "next_after_asset_id",
        )
    }


@router.post("/faces/source-detail-batches", summary="Queue one durable source-detail face batch")
async def post_source_detail_batch(
    request: Request, payload: FaceDetailBatchRequest | None = Body(default=None)
) -> dict[str, Any]:
    from photo_search.face import source_detail_batches

    catalog = service_of(request).catalog
    result = source_detail_batches.create_batch(
        catalog, source_id=None if payload is None else payload.source_id
    )
    if result["batch_id"] and result["state"] in ("QUEUED", "RUNNING"):
        context_of(request).face_detail_runner().submit_batch(str(result["batch_id"]))
    return result


@router.get("/faces/source-detail-batches/active", summary="Current durable source-detail batch")
async def get_active_source_detail_batch(request: Request, source_id: int | None = None) -> dict[str, Any]:
    from photo_search.face import source_detail_batches

    return source_detail_batches.status(service_of(request).catalog, source_id=source_id)


@router.get("/faces/source-detail-preview", summary="Count eligible photos without reading originals")
async def get_source_detail_preview(request: Request, source_id: int | None = None) -> dict[str, Any]:
    from photo_search.face import source_detail_batches

    return source_detail_batches.preview(service_of(request).catalog, source_id=source_id)


@router.get("/faces/source-detail-batches/{batch_id}", summary="Durable source-detail batch progress")
async def get_source_detail_batch(request: Request, batch_id: str) -> dict[str, Any]:
    from photo_search.face import source_detail_batches

    return source_detail_batches.status(service_of(request).catalog, str(batch_id))


@router.post("/faces/source-detail-batches/{batch_id}/pause", summary="Pause after the current photo")
async def pause_source_detail_batch(request: Request, batch_id: str) -> dict[str, Any]:
    from photo_search.face import source_detail_batches

    return source_detail_batches.pause(service_of(request).catalog, str(batch_id))


@router.post("/faces/source-detail-batches/{batch_id}/resume", summary="Resume a paused face batch")
async def resume_source_detail_batch(request: Request, batch_id: str) -> dict[str, Any]:
    from photo_search.face import source_detail_batches

    result = source_detail_batches.resume(service_of(request).catalog, str(batch_id))
    context_of(request).face_detail_runner().submit_batch(str(batch_id))
    return result


@router.post("/faces/source-detail-batches/{batch_id}/retry", summary="Retry failed photos in a face batch")
async def retry_source_detail_batch(request: Request, batch_id: str) -> dict[str, Any]:
    from photo_search.face import source_detail_batches

    result = source_detail_batches.resume(
        service_of(request).catalog, str(batch_id), retry_failed=True
    )
    context_of(request).face_detail_runner().submit_batch(str(batch_id))
    return result


@router.get("/assets/{asset_id}/faces", summary="Safe cropped faces of one photo")
async def photo_faces(request: Request, asset_id: int) -> dict[str, Any]:
    return person_albums.asset_faces(service_of(request).catalog, asset_id)


@router.post("/assets/{asset_id}/face-detail", summary="Queue on-demand source-detail face recognition")
async def post_face_detail(request: Request, asset_id: int) -> dict[str, Any]:
    from photo_search.face.service import enqueue_source_detail

    context = context_of(request)
    result = enqueue_source_detail(service_of(request).catalog, asset_id=int(asset_id))
    if not result["already_available"] and result["state"] in (
        "QUEUED",
        "RETRY_WAIT",
        "RUNNING",
    ):
        context.face_detail_runner().submit(int(asset_id))
    return result


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
    "/faces/{face_id}/create-person",
    response_model=CreatePersonFromFaceResponse,
    summary="Create one unnamed person from one explicitly selected inbox face",
)
async def post_create_person_from_face(
    request: Request, face_id: str, payload: CreatePersonFromFaceRequest
) -> dict[str, Any]:
    return persons_of(request).create_person_from_face(
        face_id=str(face_id),
        contract_version=payload.contract_version,
        request_id=payload.request_id,
        expected_control_revision=int(payload.expected_control_revision),
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


@router.post("/persons/{person_id}/regroup", response_model=FaceRegroupResponse,
             summary="Remove and safely regroup only explicitly selected faces")
async def post_face_regroup(request: Request, person_id: int, payload: FaceRegroupRequest) -> dict[str, Any]:
    return persons_of(request).regroup_faces(
        source_person_id=person_id, **payload.model_dump(),
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
        "SELECT face_id, asset_id, bbox_json, artifact_id, input_revision, spec_revision "
        "FROM faces WHERE face_id = ?",
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
    return await run_catalog_search(
        context.service().catalog, _search_request_worker, envelope, context=context,
    )


def _search_request_worker(catalog: Any, envelope: SearchRequest, *, context: ApiContext) -> dict[str, Any]:
    return search_request(
        catalog, envelope, sessions=context.search_sessions,
        encoder=None if envelope.retrieval_mode == "text" else context.query_encoder(),
        reranker=None if envelope.retrieval_mode == "text" else context.reranker(),
    )


@router.get("/search/sessions/{session_id}/time")
async def search_session_time_page(
    request: Request,
    session_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = None,
) -> dict[str, Any]:
    """Page by trusted capture time within one frozen, bounded semantic candidate set."""
    context = context_of(request)
    service = context.service()
    return await run_catalog_read(
        service.catalog, _search_session_time_worker, session_id,
        sessions=context.search_sessions, data_root=service.data_root, limit=limit, cursor=cursor,
    )


def _search_session_time_worker(
    catalog: Any, session_id: str, *, sessions: Any, data_root: Path, limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    session = sessions.get(session_id)
    if session is None:
        raise CatalogError(
            SEARCH_SESSION_EXPIRED,
            "the search session is unknown or expired; run the query again",
            details={"reason": "SESSION_NOT_LIVE"},
        )
    favorites_revision = session.extra.get("favorites_revision")
    if favorites_revision is not None:
        from photo_search.favorites import _revision

        current_revision = _revision(catalog.connection)
        if int(favorites_revision) != current_revision:
            raise ApiError(
                "FAVORITES_REVISION_STALE", "收藏已变化，请重新搜索", status=409,
                details={"reason": "FAVORITES_REVISION_CHANGED",
                         "favorites_revision": int(favorites_revision),
                         "current_favorites_revision": current_revision},
            )
    if session.query is None or not session.entries:
        raise CatalogError(
            INVALID_CURSOR,
            "capture-time browsing is available only for a semantic candidate snapshot",
            details={"reason": "SEMANTIC_CANDIDATE_SNAPSHOT_REQUIRED"},
        )
    requested_profile_id = session.extra.get("requested_profile_id", session.profile_id)
    binding_request = SearchRequest(
        request_id="semantic-time-browse",
        profile_id=requested_profile_id,
        space_id=session.space_id,
        image_generation_id=session.image_generation_id,
        dimension=session.dimension,
    )
    retrieval_mode = session.extra.get("retrieval_mode", "combined")
    current_profile = None if retrieval_mode == "text" else _resolve_profile(catalog, binding_request)
    current_binding = None if retrieval_mode == "text" else resolve_request_binding(catalog, binding_request)
    current_session_binding = (
        None if current_profile is None else current_profile.profile_id,
        None if current_binding is None else current_binding.generation_id,
        None if current_binding is None else current_binding.space_id,
        None if current_binding is None else int(current_binding.dimension),
    )
    if session.binding != current_session_binding:
        raise CatalogError(
            PROFILE_GENERATION_MISMATCH,
            "this page belongs to a different search profile or index generation",
            details={"reason": "SESSION_BINDING_CHANGED", "search_session_id": session.session_id},
        )
    search_scope = session.extra.get("search_scope")
    if not isinstance(search_scope, SearchScope):
        raise CatalogError(
            INVALID_CURSOR,
            "the semantic candidate session has no authoritative scope binding",
            details={"reason": "SESSION_SCOPE_UNAVAILABLE"},
        )
    album_scope_revision = tuple(session.extra.get("album_scope_revision", ()))
    revision_offset = 0
    if search_scope.album_membership_sql is not None:
        if len(album_scope_revision) < 5:
            raise CatalogError(
                INVALID_CURSOR,
                "the selected place session has no complete revision binding",
                details={"reason": "SESSION_SCOPE_REVISION_UNAVAILABLE"},
            )
        from photo_search.places.timeline import selected_members_sql

        _, _, place_revisions = selected_members_sql(
            catalog, data_root,
            kind=str(album_scope_revision[3]), key=str(album_scope_revision[4]),
        )
        current_place_revision = (
            place_revisions["catalog_filter_revision"],
            place_revisions["suggestion_revision"],
            place_revisions["package_revision"],
            str(album_scope_revision[3]),
            str(album_scope_revision[4]),
        )
        if current_place_revision != album_scope_revision[:5]:
            raise CatalogError(
                CATALOG_FILTER_REVISION_STALE,
                "the selected place changed after this search started",
                details={"reason": "PLACE_ALBUM_REVISION_CHANGED"},
            )
        revision_offset = 5
    if search_scope.collection_id is not None:
        try:
            marker = album_scope_revision.index("person_collection", revision_offset)
            expected_collection = album_scope_revision[marker + 1 : marker + 4]
        except (ValueError, IndexError) as exc:
            raise CatalogError(
                INVALID_CURSOR,
                "the selected collection session has no complete revision binding",
                details={"reason": "SESSION_SCOPE_REVISION_UNAVAILABLE"},
            ) from exc
        current_collection = catalog.read_one(
            "SELECT instance_token,revision FROM person_collections WHERE collection_id=?",
            (int(search_scope.collection_id),),
        )
        if (
            current_collection is None
            or int(expected_collection[0]) != int(search_scope.collection_id)
            or str(current_collection["instance_token"]) != str(expected_collection[1])
            or int(current_collection["revision"]) != int(expected_collection[2])
        ):
            raise CatalogError(
                CATALOG_FILTER_REVISION_STALE,
                "the selected collection changed after this search started",
                details={"reason": "COLLECTION_REVISION_CHANGED"},
            )
    try:
        photo_marker = album_scope_revision.index("photo_collection")
        expected_photo_collection = album_scope_revision[photo_marker + 1 : photo_marker + 4]
    except ValueError:
        expected_photo_collection = ()
    if expected_photo_collection:
        current_photo_collection = catalog.read_one(
            "SELECT instance_token,revision FROM photo_collections WHERE collection_id=?",
            (int(expected_photo_collection[0]),),
        )
        if (len(expected_photo_collection) != 3 or current_photo_collection is None
                or str(current_photo_collection["instance_token"]) != str(expected_photo_collection[1])
                or int(current_photo_collection["revision"]) != int(expected_photo_collection[2])):
            raise ApiError("PHOTO_COLLECTION_REVISION_STALE", "照片合集已变化，请重新搜索", status=409,
                           details={"reason": "PHOTO_COLLECTION_REVISION_CHANGED"})
    with catalog.read_snapshot() as conn:
        if favorites_revision is not None:
            from photo_search.favorites import _revision

            snapshot_favorites_revision = _revision(conn)
            if int(favorites_revision) != snapshot_favorites_revision:
                raise ApiError(
                    "FAVORITES_REVISION_STALE", "收藏已变化，请重新搜索", status=409,
                    details={"reason": "FAVORITES_REVISION_CHANGED",
                             "favorites_revision": int(favorites_revision),
                             "current_favorites_revision": snapshot_favorites_revision},
                )
        revision_row = conn.execute(
            "SELECT revision FROM catalog_filter_revision WHERE revision_id=1"
        ).fetchone()
        revision = int(revision_row[0]) if revision_row else 0
        time_rules_version = taken_time_read.rules_version(conn)
        if revision != int(session.catalog_filter_revision):
            raise CatalogError(
                CATALOG_FILTER_REVISION_STALE,
                "the search candidate snapshot is stale; run the query again",
                details={"reason": "CATALOG_FILTER_REVISION_CHANGED"},
            )
        token_offset = 0
        if cursor:
            try:
                raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
                token = json.loads(raw.decode("utf-8"))
                if (
                    token.get("v") != 1
                    or token.get("s") != session.session_id
                    or token.get("q") != session.query_hash
                    or int(token.get("r")) != revision
                    or token.get("tr") != time_rules_version
                ):
                    raise ValueError("cursor binding mismatch")
                token_offset = int(token["o"])
                if token_offset < 0 or token_offset >= len(session.entries):
                    raise ValueError("cursor offset out of range")
            except Exception as exc:
                raise CatalogError(
                    INVALID_CURSOR,
                    "the time-order cursor is invalid or belongs to another search snapshot",
                    details={"field": "cursor"},
                ) from exc
        ids = [entry.asset_id for entry in session.entries]
        where, scope_parameters = scope_condition(conn, search_scope)
        placeholders = ",".join("?" for _ in ids)
        qualified_ids = {
            int(row[0])
            for row in conn.execute(
                f"SELECT a.asset_id FROM assets AS a WHERE ({where}) "
                f"AND a.asset_id IN ({placeholders})",
                [*scope_parameters, *ids],
            ).fetchall()
        }
        metadata = {
            int(row[0]): (str(row[1]), None if row[2] is None else int(row[2]))
            for row in conn.execute(
                f"SELECT asset_id,time_source,taken_at_sort FROM asset_metadata WHERE asset_id IN ({placeholders})",
                ids,
            ).fetchall()
        }
        trusted = taken_time_read.rules_version_matches(conn)
        ranked = list(enumerate(session.entries, start=1))
        ranked.sort(
            key=lambda pair: (
                0
                if trusted
                and metadata.get(pair[1].asset_id, ("", None))[0]
                in {"EXIF_ORIGINAL", "EXIF_DIGITIZED"}
                and metadata.get(pair[1].asset_id, ("", None))[1] is not None
                else 1,
                -int(metadata[pair[1].asset_id][1])
                if trusted
                and pair[1].asset_id in metadata
                and metadata[pair[1].asset_id][0] in {"EXIF_ORIGINAL", "EXIF_DIGITIZED"}
                and metadata[pair[1].asset_id][1] is not None
                else 0,
                -int(pair[1].asset_id),
            )
        )
        page = ranked[token_offset : token_offset + int(limit)]
        has_more = token_offset + len(page) < len(ranked)
        assets = []
        for relevance_rank, entry in page:
            if entry.asset_id not in qualified_ids:
                continue
            assets.append(
                {
                    "asset_id": int(entry.asset_id),
                    "semantic_rank": int(relevance_rank),
                    "score": entry.score,
                    "matched_by": dict(entry.matched_by),
                    "hit_reason": entry.hit_reason,
                    "matched_text": list(entry.matched_text),
                    "semantic_ready": bool(entry.semantic_ready),
                    "capture_time": taken_time_read.asset_view(conn, entry.asset_id),
                    "thumbnail": f"/api/v1/assets/{int(entry.asset_id)}/thumbnail",
                    "detail": f"/#/asset/{int(entry.asset_id)}",
                }
            )
        next_cursor = None
        if has_more:
            next_cursor = base64.urlsafe_b64encode(
                json.dumps(
                    {"v": 1, "s": session.session_id, "q": session.query_hash,
                     "r": revision, "tr": time_rules_version,
                     "o": token_offset + len(page)},
                    separators=(",", ":"),
                ).encode("utf-8")
            ).decode("ascii").rstrip("=")
        return {
            "search_session_id": session.session_id,
            "retrieval_mode": retrieval_mode,
            "order": "capture_time_desc",
            "candidate_scope": (
                "current_text_candidate_snapshot" if retrieval_mode == "text"
                else "current_semantic_candidate_snapshot"
            ),
            "total_candidates": len(session.entries),
            "candidates_truncated": bool(session.candidates_truncated),
            "candidate_limit": (
                int(session.extra.get("candidate_limit", 5000)) if retrieval_mode == "text" else 5000
            ),
            "catalog_filter_revision": revision,
            "taken_time_rules_version": time_rules_version,
            "assets": assets,
            "next_cursor": next_cursor,
        }


def _place_aware_choices(service: Any) -> list[dict[str, Any]]:
    """Use the same source-backed candidate set as manual assignment."""
    return place_choices.choices(service.catalog, data_root=service.data_root)


@router.post("/search/place-aware", summary="Personal local place plus content search")
async def post_place_aware_search(request: Request, payload: PlaceAwareSearchRequestModel) -> dict[str, Any]:
    """Additive entry that resolves place intent before reusing the existing search/session service."""
    context = context_of(request)
    service = context.service()
    result = await run_catalog_search(
        service.catalog, _place_aware_search_worker, payload,
        context=context, data_root=service.data_root,
    )
    source_mode = result.pop("photo_collection_source_mode", None)
    auth_session_id = request.cookies.get(SESSION_COOKIE)
    if source_mode and auth_session_id and payload.cursor is None:
        source_payload = payload.model_dump(mode="json")
        # A combined search may resolve a server-authored intent plan on the first
        # request. Its continuation must reuse that exact accepted plan; replaying only
        # the user's raw text loses the plan and is refused as a stale/missing cursor.
        if isinstance(result.get("intent_plan"), dict):
            source_payload["intent_plan"] = result["intent_plan"]
        source_token = context.register_photo_collection_source(
            auth_session_id=auth_session_id, payload=source_payload,
            result=result, mode=str(source_mode),
        )
        if source_token:
            result["photo_collection_source_token"] = source_token
            result["photo_collection_source_mode"] = source_mode
    return result


def _place_aware_search_worker(
    catalog: Any, payload: PlaceAwareSearchRequestModel, *, context: ApiContext, data_root: Path,
    filter_statistics: bool = True,
    filter_exclusion_smart_id: int | None = None,
    filter_order: str = "asset_id",
    filter_capture_position: list[Any] | None = None,
    ranked_candidate_build: tuple[int, str] | None = None,
    filter_authoritative_all: int | None = None,
) -> dict[str, Any]:
    service = CatalogService(catalog, data_root=data_root)
    favorites_only = bool(payload.favorites_only)
    favorites_revision = _favorites_revision(catalog) if favorites_only else None
    original = payload.original_query
    text_mode = payload.retrieval_mode == "text"
    if text_mode:
        SearchRequest(
            request_id=payload.request_id, retrieval_mode="text", profile_id=payload.profile_id,
            space_id=payload.space_id, image_generation_id=payload.image_generation_id,
            dimension=payload.dimension, rerank=payload.rerank,
        ).validate_retrieval_mode()
        if payload.intent_plan is not None or any(
            value is not None and value != original for value in (payload.text, payload.content_query)
        ):
            raise ApiError("VALIDATION_ERROR", "文字搜索使用原文，不能同时提交自动搜索计划或改写的查询", status=422)
        if (payload.place_kind is None) != (payload.place_key is None):
            raise ApiError("VALIDATION_ERROR", "地点范围需要同时提供类型和标识", status=422)
    kind, key, content = payload.place_kind, payload.place_key, payload.content_query
    selected_scope = payload.search_scope
    if selected_scope is not None:
        if not isinstance(selected_scope, dict) or len(selected_scope) > 8:
            raise ApiError(INVALID_CURSOR, "搜索范围无效，请重新选择", status=409)
        selected_kind = selected_scope.get("kind")
        if selected_kind == "place":
            selected_place_kind = selected_scope.get("place_kind")
            selected_place_key = selected_scope.get("place_key")
            if selected_place_kind not in {"city", "province", "country", "district"} or not isinstance(selected_place_key, str) or not selected_place_key or len(selected_place_key) > 128:
                raise ApiError(INVALID_CURSOR, "地点搜索范围无效，请重新选择", status=409)
            if kind is not None and (kind != selected_place_kind or str(key) != selected_place_key):
                raise ApiError(INVALID_CURSOR, "固定地点范围与查询条件冲突，请重新搜索", status=409)
            kind, key = selected_place_kind, selected_place_key
        elif selected_kind not in {"person", "collection", "photo_collection"}:
            raise ApiError(INVALID_CURSOR, "搜索范围无效，请重新选择", status=409)
    parse_notice = None
    intent_plan = payload.intent_plan
    client_plan_supplied = intent_plan is not None
    collection_scope = None
    query_source = original
    from photo_search.search.personal_search import PersonalSearchCoordinator

    coordinator = PersonalSearchCoordinator(service.catalog, service.data_root)
    unified = None
    if not text_mode and payload.cursor is None and intent_plan is None:
        unified = coordinator.collect(original)
        if (kind is not None and key is not None and unified.get("place_kind") is not None
                and (kind != unified.get("place_kind") or str(key) != str(unified.get("place_key")))):
            raise ApiError(INVALID_CURSOR, "自动识别地点与手动地点条件冲突，请重新搜索", status=409)
        if unified["state"] == "needs_choice":
            choice_types = {str(candidate.get("type")) for candidate in unified.get("candidates", [])}
            if choice_types == {"collection"}:
                choices = []
                for candidate in unified.get("candidates", []):
                    scope = {key: candidate[key] for key in
                             ("collection_id", "instance_token", "revision", "name")}
                    choices.append({**scope, "label": str(candidate["name"]), "intent_plan": {
                        "plan_version": unified["plan_version"], "original_query": original,
                        "collection_scope": scope, "collection_query": unified.get("content_query") or "",
                        "content_query": unified.get("content_query"), "capture_from": unified.get("capture_from"),
                        "capture_to_exclusive": unified.get("capture_to_exclusive"),
                        "time_basis": unified.get("time_basis", "capture"),
                        "capture_status": unified.get("capture_status"),
                        "library_from": unified.get("library_from"),
                        "library_to_exclusive": unified.get("library_to_exclusive"),
                        "person_ids": list(unified.get("person_ids", [])),
                        "person_names": list(unified.get("person_names", [])),
                        "person_match": unified.get("person_match", "any"),
                        "place_kind": unified.get("place_kind"), "place_key": unified.get("place_key"),
                        "place_selection": unified.get("place_selection"),
                        "source_id": unified.get("source_id"), "source_name": unified.get("source_name"),
                    }})
                collection_label_counts: dict[str, int] = {}
                for choice in choices:
                    collection_label_counts[choice["label"]] = collection_label_counts.get(choice["label"], 0) + 1
                for choice in choices:
                    if collection_label_counts[choice["label"]] > 1:
                        choice["label"] += " #" + str(choice["collection_id"])
                return {"needs_collection_choice": True, "original_query": original,
                        "collection_choices": choices}
            if unified.get("choice_kind") == "person_match":
                choices = []
                for match_mode, label in (("any", "任一人物的照片"), ("all", "两人同框的合照")):
                    plan = {"plan_version": unified["plan_version"], "original_query": original,
                            "collection_query": unified.get("content_query") or "",
                            "content_query": unified.get("content_query"),
                            "capture_from": unified.get("capture_from"),
                            "capture_to_exclusive": unified.get("capture_to_exclusive"),
                            "time_basis": unified.get("time_basis", "capture"),
                            "capture_status": unified.get("capture_status"),
                            "library_from": unified.get("library_from"),
                            "library_to_exclusive": unified.get("library_to_exclusive"),
                            "person_ids": list(unified["person_ids"]),
                            "person_match": match_mode, "person_names": list(unified.get("person_names", [])),
                            "place_kind": unified.get("place_kind"), "place_key": unified.get("place_key"),
                            "place_name": unified.get("place_name"),
                            "place_selection": unified.get("place_selection"),
                            "source_id": unified.get("source_id"), "source_name": unified.get("source_name"),
                            "collection_scope": unified.get("collection_scope")}
                    choices.append({"type": "person", "label": label, "intent_plan": plan})
                return {"needs_scope_choice": True, "original_query": original,
                        "scope_choices": choices, "content_query": unified.get("content_query")}
            choices = []
            for candidate in unified.get("candidates", []):
                plan = {"plan_version": unified["plan_version"], "original_query": original,
                        "collection_query": unified.get("content_query") or "",
                        "content_query": unified.get("content_query"),
                        "capture_from": unified.get("capture_from"),
                        "capture_to_exclusive": unified.get("capture_to_exclusive"),
                        "time_basis": unified.get("time_basis", "capture"),
                        "capture_status": unified.get("capture_status"),
                        "library_from": unified.get("library_from"),
                        "library_to_exclusive": unified.get("library_to_exclusive"),
                        "person_ids": list(unified.get("person_ids", [])),
                        "person_match": unified.get("person_match", "any"),
                        "person_names": list(unified.get("person_names", [])),
                        "place_kind": unified.get("place_kind"), "place_key": unified.get("place_key"),
                        "place_name": unified.get("place_name"),
                        "place_selection": unified.get("place_selection"),
                        "source_id": unified.get("source_id"), "source_name": unified.get("source_name"),
                        "collection_scope": unified.get("collection_scope")}
                kind = str(candidate.get("type", ""))
                label = str(candidate.get("name") or kind)
                if kind == "collection":
                    plan["collection_scope"] = {key: candidate[key] for key in
                        ("collection_id", "instance_token", "revision", "name")}
                    label = "人物合集：" + label
                elif kind == "person":
                    if int(candidate["person_id"]) not in plan["person_ids"]:
                        plan["person_ids"].append(int(candidate["person_id"]))
                        plan["person_names"].append(label)
                    label = "人物：" + label + " #" + str(candidate["person_id"])
                elif kind == "place":
                    plan["place_kind"], plan["place_key"] = candidate["kind"], str(candidate["key"])
                    plan["place_selection"] = {key: candidate.get(key) for key in
                        ("kind", "key", "source", "source_id", "selection_ref", "dataset_id", "dataset_version")}
                    label = "地点：" + label
                    place_context = [str(candidate.get(field) or "").strip()
                                     for field in ("parent", "source")]
                    place_context = [value for value in place_context if value]
                    if place_context:
                        label += "（" + " · ".join(place_context) + "）"
                elif kind == "time":
                    value = candidate.get("value")
                    time_basis = str(candidate.get("time_basis", "capture"))
                    capture_status = candidate.get("capture_status")
                    plan["time_basis"] = time_basis
                    plan["capture_status"] = capture_status
                    if time_basis == "library" and value is not None:
                        plan["capture_from"] = plan["capture_to_exclusive"] = None
                        plan["library_from"], plan["library_to_exclusive"] = value
                        label = "入库时间：" + "—".join(value)
                    elif capture_status is not None:
                        plan["capture_from"] = plan["capture_to_exclusive"] = None
                        plan["library_from"] = plan["library_to_exclusive"] = None
                        label = "拍摄日期：" + ("未知" if capture_status == "unknown" else "待处理")
                    elif value is not None:
                        plan["capture_from"], plan["capture_to_exclusive"] = value
                        plan["library_from"] = plan["library_to_exclusive"] = None
                        label = "拍摄时间：" + "—".join(value)
                elif kind == "source":
                    plan["source_id"] = int(candidate["source_id"])
                    plan["source_name"] = label
                    label = "图库来源：" + label
                choices.append({"type": kind, "label": label, "intent_plan": plan,
                                "place": candidate if kind == "place" else None})
            label_counts: dict[str, int] = {}
            for choice in choices:
                label_counts[choice["label"]] = label_counts.get(choice["label"], 0) + 1
            for choice in choices:
                if label_counts[choice["label"]] < 2:
                    continue
                if choice["type"] == "place" and choice.get("place"):
                    identity_fields = ("kind", "key", "source", "source_id", "selection_ref",
                                       "dataset_id", "dataset_version")
                    identity = " / ".join(
                        str(choice["place"][field]) for field in identity_fields
                        if choice["place"].get(field) not in (None, "")
                    )
                elif choice["type"] == "source":
                    identity = str(choice["intent_plan"].get("source_id", ""))
                elif choice["type"] == "collection":
                    identity = str((choice["intent_plan"].get("collection_scope") or {}).get("collection_id", ""))
                else:
                    identity = ""
                if identity:
                    choice["label"] += " #" + identity
            return {"needs_scope_choice": True, "original_query": original,
                    "scope_choices": choices, "content_query": unified.get("content_query")}
        if unified["state"] == "unsupported":
            return {"unsupported_intent": True, "original_query": original,
                    "notice": unified.get("notice") or "这组条件暂不支持自动筛选，请改写或使用明确筛选。"}
        intent_plan = {**unified, "collection_query": unified.get("content_query") or ""}
        if unified.get("place_kind") is not None:
            kind, key = unified.get("place_kind"), unified.get("place_key")
        query_source = str(unified.get("content_query") or "")

    if intent_plan is not None and intent_plan.get("plan_version") is None:
        raise ApiError(INVALID_CURSOR, "搜索计划版本已过期，请重新搜索", status=409)
    if intent_plan is not None:
        try:
            if "person_match_before_fixed_scope" in intent_plan:
                raise ValueError("人物范围计划已更新，请重新搜索")
            intent_plan = coordinator.validate(
                intent_plan, original_query=original, parsed=unified,
                continuation=payload.cursor is not None,
            )
        except ValueError as error:
            code = CATALOG_FILTER_REVISION_STALE if "已变化" in str(error) else INVALID_CURSOR
            raise ApiError(code, str(error), status=409) from error
        plan_source = intent_plan.get("source_id")
        if plan_source is not None:
            if payload.source_id is not None and int(payload.source_id) != plan_source:
                raise ApiError(INVALID_CURSOR, "自动识别来源与手动来源条件冲突，请重新搜索", status=409)
        planned_people = set(map(int, intent_plan.get("person_ids", [])))
        if payload.person_ids and planned_people and set(map(int, payload.person_ids)) != planned_people:
            raise ApiError(INVALID_CURSOR, "自动识别人物与手动人物条件冲突，请重新搜索", status=409)
        if payload.person_ids and planned_people and payload.person_match != intent_plan.get("person_match", "any"):
            raise ApiError(INVALID_CURSOR, "自动识别人物匹配方式与手动人物条件冲突，请重新搜索", status=409)
        for bound in ("capture_from", "capture_to_exclusive"):
            planned, explicit = intent_plan.get(bound), getattr(payload, bound)
            if explicit is not None and planned is not None and str(explicit) != str(planned):
                raise ApiError(INVALID_CURSOR, "自动识别时间与手动时间条件冲突，请重新搜索", status=409)
        planned_kind, planned_key = intent_plan.get("place_kind"), intent_plan.get("place_key")
        if payload.place_kind is not None and planned_kind is not None and (
            payload.place_kind != planned_kind or str(payload.place_key) != str(planned_key)
        ):
            raise ApiError(INVALID_CURSOR, "自动识别地点与手动地点条件冲突，请重新搜索", status=409)
    if intent_plan and isinstance(intent_plan.get("collection_scope"), dict):
        requested = intent_plan["collection_scope"]
        try:
            requested_id = int(requested.get("collection_id", 0))
            requested_revision = int(requested.get("revision", -1))
        except (TypeError, ValueError, OverflowError) as error:
            raise ApiError(INVALID_CURSOR, "人物合集计划无效，请重新搜索", status=409) from error
        requested_token = str(requested.get("instance_token", ""))
        if requested_id < 1 or requested_revision < 0 or len(requested_token) != 32 or any(
            character not in "0123456789abcdef" for character in requested_token
        ):
            raise ApiError(INVALID_CURSOR, "人物合集计划无效，请重新搜索", status=409)
        current = person_collections._require_collection_instance(
            service.catalog.connection, requested_id, requested_token,
        )
        if (int(current["revision"]) != requested_revision
                or str(current["name"]) != str(requested.get("name", ""))):
            raise ApiError(CATALOG_FILTER_REVISION_STALE, "人物合集已变化，请重新搜索", status=409)
        collection_scope = {"collection_id": int(current["collection_id"]),
                            "instance_token": str(current["instance_token"]),
                            "revision": int(current["revision"]), "name": str(current["name"])}
        query_source = str(intent_plan.get("collection_query", ""))
        if intent_plan.get("place_kind") is not None:
            kind, key = intent_plan.get("place_kind"), intent_plan.get("place_key")
    elif intent_plan is not None and "collection_query" in intent_plan:
        query_source = str(intent_plan.get("collection_query") or "")
        if intent_plan.get("place_kind") is not None:
            kind, key = intent_plan.get("place_kind"), intent_plan.get("place_key")
    elif not text_mode and payload.cursor is not None and intent_plan is None and not (kind is not None and key is not None):
        raise ApiError(INVALID_CURSOR, "合集搜索计划缺失，请重新搜索", status=409)
    if text_mode:
        query_text = original.strip() or None
    elif payload.place_cancelled:
        kind = key = None
        if client_plan_supplied and intent_plan is not None:
            intent_plan["place_kind"] = intent_plan["place_key"] = None
            intent_plan["place_name"] = intent_plan["place_selection"] = None
            query_text = (intent_plan.get("content_query") or "").strip() or None
        else:
            query_text = (content if content is not None else query_source).strip() or None
    elif kind is None and key is None and intent_plan is None:
        from photo_search.places.natural_search import parse_place_query

        plan = parse_place_query(
            query_source,
            place_choices.choices(
                service.catalog, text=query_source, data_root=service.data_root,
                include_parent_identity=True,
            ),
        )
        if plan["state"] == "ambiguous":
            return {"needs_place_choice": True, "original_query": original,
                    "content_query": plan["content_query"], "place_choices": plan["choices"]}
        if plan["state"] == "recognized":
            chosen = plan["choices"][0]
            kind, key, content = chosen["kind"], chosen["key"], chosen["content_query"]
            query_text = content or None
        else:
            # SearchRequest treats an empty text field as invalid; a blank query is the
            # documented pure-filter search, so normalize it to None at this boundary.
            query_text = query_source.strip() or None
        parse_notice = plan["notice"]
    else:
        query_text = (content if content is not None else query_source).strip() or None
    album_ids = None
    revision = None
    place_block = None
    place_membership_sql = None
    place_membership_parameters: tuple[Any, ...] = ()
    if kind is not None and key is not None:
        album_ids, collection = place_albums.album_member_ids(
            service.catalog, service.data_root, kind=kind, key=key, include_ids=False,
        )
        from photo_search.places.timeline import selected_members_sql

        place_membership_sql, place_membership_parameters, revision = selected_members_sql(
            service.catalog, service.data_root, kind=kind, key=str(key),
        )
        album = next((item for item in collection["albums"] if item["key"] == str(key)), None)
        revision = (revision["catalog_filter_revision"], revision["suggestion_revision"],
                    revision["package_revision"], kind, str(key))
        collection["catalog_filter_revision"], collection["suggestion_revision"], \
            collection["package_revision"] = revision[:3]
        if album is None and kind in ("city", "country") and not str(key).startswith("cn:"):
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
        place_selection = intent_plan.get("place_selection") if intent_plan is not None else None
        if not isinstance(place_selection, dict) or (
            place_selection.get("kind") != kind or str(place_selection.get("key")) != str(key)
        ):
            source_choice = next(
                (item for item in place_choices.choices(service.catalog, data_root=service.data_root)
                 if item["kind"] == kind and str(item["key"]) == str(key)),
                None,
            )
            place_source = None if source_choice is None else source_choice["source"]
        else:
            place_source = place_selection.get("source")
        place_block = {"kind": kind, "key": str(key), "name": album["name"], "parent": album.get("parent", ""), "origin": album.get("origin"), "source": place_source, "catalog_filter_revision": revision[0], "suggestion_revision": revision[1], "package_revision": revision[2]}
    # A continuation reuses the structured first-page plan and never re-parses relative dates.
    if not text_mode and payload.cursor is None and intent_plan is None:
        from photo_search.search.natural_intent import parse_natural_intent

        natural = parse_natural_intent(query_text or "", connection=service.catalog.connection)
        if parse_notice and natural["state"] != "ambiguous" and not natural["choices"] and not natural["capture_from"]:
            # Keep unknown/negative/multi-place phrases intact unless another condition is
            # positively grounded in the active catalog or capture-time syntax.
            natural = {"state": "ready", "choices": [], "person_match": "any",
                       "capture_from": None, "capture_to_exclusive": None,
                       "content_query": query_text, "notice": parse_notice}
        if natural["state"] == "ambiguous":
            return {"needs_person_choice": True, "original_query": original,
                    "content_query": natural["content_query"], "person_choices": natural["choices"],
                    "intent_plan": {"original_query": original,
                                    "collection_scope": collection_scope,
                                    "collection_query": query_source,
                                    "content_query": natural["content_query"],
                                    "capture_from": natural["capture_from"],
                                    "capture_to_exclusive": natural["capture_to_exclusive"],
                                    "time_basis": "capture", "capture_status": None,
                                    "library_from": None, "library_to_exclusive": None,
                                    "person_ids": natural.get("known_person_ids", []),
                                    "person_names": natural.get("known_person_names", []),
                                    "person_match": natural.get("person_match", "any"),
                                    "place_kind": kind, "place_key": key}}
        if payload.capture_from is None and payload.capture_to_exclusive is None:
            values_time = {
                "capture_from": natural["capture_from"],
                "capture_to_exclusive": natural["capture_to_exclusive"],
            }
        else:
            values_time = {"capture_from": payload.capture_from,
                           "capture_to_exclusive": payload.capture_to_exclusive}
        inferred_people = [int(item["person_id"]) for item in natural["choices"]]
        if payload.person_ids:
            inferred_people = list(payload.person_ids)
            inferred_match = payload.person_match
        else:
            inferred_match = natural["person_match"]
        query_text = natural["content_query"]
        intent_plan = {
            "original_query": original,
            "collection_scope": collection_scope,
            "collection_query": query_source,
            "content_query": query_text,
            "capture_from": values_time["capture_from"],
            "capture_to_exclusive": values_time["capture_to_exclusive"],
            "time_basis": "capture", "capture_status": None,
            "library_from": None, "library_to_exclusive": None,
            "person_ids": inferred_people,
            "person_match": inferred_match,
            "person_names": [str(item["name"]) for item in natural["choices"]],
            "place_kind": kind,
            "place_key": key,
            "unresolved": parse_notice,
        }
    elif intent_plan is not None and not payload.place_cancelled:
        query_text = (intent_plan.get("content_query") or "").strip() or None
    resolved_search_scope = None
    fixed_person_sql = None
    fixed_person_parameters: tuple[Any, ...] = ()
    fixed_person_revision = None
    photo_collection_scope = None
    photo_collection_sql = None
    photo_collection_parameters: tuple[Any, ...] = ()
    if selected_scope is not None:
        selected_kind = str(selected_scope.get("kind"))
        if selected_kind == "person":
            try:
                selected_person_id = int(selected_scope.get("person_id", 0))
            except (TypeError, ValueError, OverflowError) as error:
                raise ApiError(INVALID_CURSOR, "人物搜索范围无效，请重新选择", status=409) from error
            person_row = service.catalog.read_one(
                "SELECT person_id, name, (SELECT revision FROM catalog_filter_revision "
                "WHERE revision_id=1) AS filter_revision "
                "FROM persons WHERE person_id=? AND merged_into IS NULL",
                (selected_person_id,),
            )
            if selected_person_id < 1 or person_row is None:
                raise ApiError(CATALOG_FILTER_REVISION_STALE, "人物身份已变化，请重新选择", status=409)
            # The album is an independent AND condition. Preserve the query's ANY/ALL
            # group: A's album plus (B OR C) means A AND (B OR C), never A AND B AND C.
            fixed_person_sql, fixed_parameters = person_condition(
                SearchScope.build(person_ids=[selected_person_id]),
            )
            fixed_person_parameters = tuple(fixed_parameters)
            fixed_person_revision = int(person_row["filter_revision"])
            resolved_search_scope = {"kind": "person", "person_id": selected_person_id,
                                     "name": str(person_row["name"] or "未命名人物")}
        elif selected_kind == "collection":
            try:
                selected_collection_id = int(selected_scope.get("collection_id", 0))
                selected_collection_revision = int(selected_scope.get("revision", -1))
                selected_collection_token = str(selected_scope.get("instance_token", ""))
            except (TypeError, ValueError, OverflowError) as error:
                raise ApiError(INVALID_CURSOR, "合集搜索范围无效，请重新选择", status=409) from error
            if selected_collection_id < 1 or selected_collection_revision < 0 or len(selected_collection_token) != 32:
                raise ApiError(INVALID_CURSOR, "合集搜索范围无效，请重新选择", status=409)
            current_collection = person_collections._require_collection_instance(
                service.catalog.connection, selected_collection_id, selected_collection_token,
            )
            current_collection_scope = {
                "collection_id": int(current_collection["collection_id"]),
                "instance_token": str(current_collection["instance_token"]),
                "revision": int(current_collection["revision"]),
                "name": str(current_collection["name"]),
            }
            if (current_collection_scope["revision"] != selected_collection_revision
                    or (collection_scope is not None and collection_scope != current_collection_scope)):
                raise ApiError(CATALOG_FILTER_REVISION_STALE, "合集已变化或与查询条件冲突，请重新选择", status=409)
            collection_scope = current_collection_scope
            if intent_plan is None:
                intent_plan = {"original_query": original, "content_query": query_text,
                               "collection_query": query_text or ""}
            intent_plan["collection_scope"] = current_collection_scope
            intent_plan["collection_query"] = str(intent_plan.get("content_query") or "")
            resolved_search_scope = {"kind": "collection", **current_collection_scope}
        elif selected_kind == "photo_collection":
            try:
                selected_photo_collection_id = int(selected_scope.get("photo_collection_id", 0))
                selected_photo_collection_revision = int(selected_scope.get("revision", -1))
                selected_photo_collection_token = str(selected_scope.get("instance_token", ""))
            except (TypeError, ValueError, OverflowError) as error:
                raise ApiError(INVALID_CURSOR, "照片合集搜索范围无效，请重新选择", status=409) from error
            if selected_photo_collection_id < 1 or selected_photo_collection_revision < 1 or len(selected_photo_collection_token) != 32:
                raise ApiError(INVALID_CURSOR, "照片合集搜索范围无效，请重新选择", status=409)
            current_photo_collection = service.catalog.read_one(
                "SELECT collection_id,instance_token,revision,name FROM photo_collections WHERE collection_id=?",
                (selected_photo_collection_id,),
            )
            if current_photo_collection is None:
                raise ApiError("PHOTO_COLLECTION_NOT_FOUND", "照片合集不存在", status=404)
            photo_collection_scope = {
                "photo_collection_id": int(current_photo_collection["collection_id"]),
                "instance_token": str(current_photo_collection["instance_token"]),
                "revision": int(current_photo_collection["revision"]),
                "name": str(current_photo_collection["name"]),
            }
            if (photo_collection_scope["instance_token"] != selected_photo_collection_token
                    or photo_collection_scope["revision"] != selected_photo_collection_revision):
                raise ApiError("PHOTO_COLLECTION_REVISION_STALE", "照片合集已变化，请重新搜索", status=409)
            photo_collection_sql = (
                "EXISTS (SELECT 1 FROM photo_collection_members pcm "
                "WHERE pcm.collection_id=? AND pcm.asset_id=a.asset_id)"
            )
            photo_collection_parameters = (selected_photo_collection_id,)
            resolved_search_scope = {"kind": "photo_collection", **photo_collection_scope}
        else:
            selected_choice = next((item for item in place_choices.choices(
                service.catalog, data_root=service.data_root
            ) if item.get("kind") == kind and str(item.get("key")) == str(key)), None)
            if selected_choice is None:
                raise ApiError(CATALOG_FILTER_REVISION_STALE, "地点身份已变化，请重新选择", status=409)
            if intent_plan is None:
                intent_plan = {"original_query": original, "content_query": query_text}
            planned_place = intent_plan.get("place_kind")
            if planned_place is not None and (planned_place != kind or str(intent_plan.get("place_key")) != str(key)):
                raise ApiError(INVALID_CURSOR, "固定地点范围与查询条件冲突，请重新搜索", status=409)
            intent_plan.update({
                "place_kind": kind, "place_key": str(key),
                "place_name": str(selected_choice.get("name") or ""),
                "place_selection": {field: selected_choice.get(field) for field in
                    ("kind", "key", "source", "source_id", "selection_ref", "dataset_id", "dataset_version")},
            })
            resolved_search_scope = {"kind": "place", "place_kind": kind, "place_key": str(key),
                                     "name": str(selected_choice.get("name") or ""),
                                     "parent": str(selected_choice.get("parent") or "")}
    if intent_plan is not None:
        coordinator.refresh_person_names(intent_plan)
    search_time_sql, search_time_parameters, search_time_identity = _search_time_condition(
        service.catalog, intent_plan,
    )
    if fixed_person_sql is not None:
        search_time_sql = (
            f"({search_time_sql}) AND ({fixed_person_sql})" if search_time_sql is not None
            else fixed_person_sql
        )
        search_time_parameters += fixed_person_parameters
        search_time_identity += ("fixed_person", selected_person_id)
    if photo_collection_sql is not None:
        search_time_sql = (
            f"({search_time_sql}) AND ({photo_collection_sql})" if search_time_sql is not None
            else photo_collection_sql
        )
        search_time_parameters += photo_collection_parameters
        search_time_identity += ("photo_collection", photo_collection_scope["photo_collection_id"],
                                 photo_collection_scope["instance_token"], photo_collection_scope["revision"])
    values = payload.model_dump(exclude={"original_query", "content_query", "place_kind", "place_key", "place_cancelled", "intent_plan", "search_scope", "favorites_only"})
    values["text"] = query_text
    if intent_plan is not None:
        for bound in ("capture_from", "capture_to_exclusive"):
            if intent_plan.get(bound) is not None:
                values[bound] = intent_plan[bound]
        if intent_plan.get("person_ids"):
            values["person_ids"] = intent_plan["person_ids"]
            values["person_match"] = intent_plan.get("person_match", "any")
        if intent_plan.get("source_id") is not None:
            values["source_id"] = int(intent_plan["source_id"])
    envelope = SearchRequest.from_envelope(values)
    execution_path = coordinator.execution_path(
        place_scope=kind is not None and key is not None,
        content_query=envelope.text,
    )
    if ranked_candidate_build is not None or filter_authoritative_all is not None:
        # Revalidate the bounded published candidate set against the exact saved,
        # structured SQL scope. This path deliberately bypasses FTS and vector retrieval.
        smart_id = int(ranked_candidate_build[0]) if ranked_candidate_build is not None else int(filter_authoritative_all)
        if ranked_candidate_build is not None:
            candidate_sql = (
                "EXISTS (SELECT 1 FROM photo_smart_cache_entries AS pse "
                "WHERE pse.smart_id=? AND pse.build_id=? AND pse.asset_id=a.asset_id)"
            )
            search_time_sql = f"({search_time_sql}) AND ({candidate_sql})" if search_time_sql else candidate_sql
            search_time_parameters += (smart_id, str(ranked_candidate_build[1]))
        exclusion_sql = (
            "NOT EXISTS (SELECT 1 FROM photo_smart_exclusions AS pse "
            "WHERE pse.smart_id=? AND pse.asset_id=a.asset_id)"
        )
        search_time_sql = f"({search_time_sql}) AND ({exclusion_sql})" if search_time_sql else exclusion_sql
        search_time_parameters += (int(smart_id),)
        bounded_envelope = replace(envelope, page_size=5001, cursor=None, search_session_id=None)
        qualification_order = "capture_desc" if filter_order == "capture_desc" else "asset_id"
        if kind is not None and key is not None:
            page = _place_album_search_page(
                service, bounded_envelope, kind=kind, key=str(key), album_ids=album_ids or [],
                collection=collection, place_block=place_block, original_query=original,
                person_collection=collection_scope, membership_sql=place_membership_sql,
                membership_parameters=place_membership_parameters,
                additional_condition_sql=search_time_sql,
                additional_condition_parameters=search_time_parameters,
                time_identity=search_time_identity, favorites_only=favorites_only,
                include_statistics=False, order=qualification_order,
            )
        else:
            page = _filter_only_search_page(
                service, bounded_envelope, collection_scope=collection_scope,
                additional_condition_sql=search_time_sql,
                additional_condition_parameters=search_time_parameters,
                time_identity=search_time_identity, favorites_only=favorites_only,
                include_statistics=False, order=qualification_order,
            )
        _verify_person_collection(service.catalog, collection_scope)
        _verify_fixed_person_revision(service.catalog, fixed_person_revision)
        _verify_favorites_revision(service.catalog, favorites_revision)
        return {**page, "hits": page.get("hits", []), "has_more": page.get("has_more", False),
                "validated_current_scope": True, "search_scope": resolved_search_scope,
                "collection_scope": collection_scope,
                "place_scope": page.get("place_scope") or place_block}
    if execution_path == "place_album":
        result = _place_album_search_page(
            service, envelope, kind=kind, key=str(key), album_ids=album_ids or [],
            collection=collection, place_block=place_block, original_query=original,
            person_collection=collection_scope, membership_sql=place_membership_sql,
            membership_parameters=place_membership_parameters,
            additional_condition_sql=search_time_sql,
            additional_condition_parameters=search_time_parameters,
            time_identity=search_time_identity,
            favorites_only=favorites_only,
            filter_exclusion_smart_id=filter_exclusion_smart_id,
            include_statistics=filter_statistics,
            order=filter_order,
            capture_position=filter_capture_position,
        )
        result["place_parse_notice"] = parse_notice
        result["intent_plan"] = None if text_mode else intent_plan
        result["search_scope"] = resolved_search_scope
        result["photo_collection_source_mode"] = (
            "all_filter_matches" if not (envelope.text or "").strip() else None
        )
        _verify_person_collection(service.catalog, collection_scope)
        _verify_fixed_person_revision(service.catalog, fixed_person_revision)
        _verify_favorites_revision(service.catalog, favorites_revision)
        _attach_favorites_echo(result, favorites_only=favorites_only, favorites_revision=favorites_revision)
        return result
    if execution_path == "filter_only":
        if filter_exclusion_smart_id is not None:
            exclusion_sql = (
                "NOT EXISTS (SELECT 1 FROM photo_smart_exclusions AS pse "
                "WHERE pse.smart_id=? AND pse.asset_id=a.asset_id)"
            )
            if search_time_sql is None:
                search_time_sql, search_time_parameters = exclusion_sql, (int(filter_exclusion_smart_id),)
            else:
                search_time_sql = f"({search_time_sql}) AND ({exclusion_sql})"
                search_time_parameters += (int(filter_exclusion_smart_id),)
        result = _filter_only_search_page(
            service, envelope, collection_scope=collection_scope,
            additional_condition_sql=search_time_sql,
            additional_condition_parameters=search_time_parameters,
            time_identity=search_time_identity,
            favorites_only=favorites_only,
            include_statistics=filter_statistics,
            order=filter_order,
            capture_position=filter_capture_position,
        )
        result["original_query"] = original
        result["content_query"] = None
        result["place_parse_notice"] = parse_notice
        result["intent_plan"] = None if text_mode else intent_plan
        result["place_scope"] = None
        result["collection_scope"] = collection_scope
        result["search_scope"] = resolved_search_scope
        result["photo_collection_source_mode"] = (
            "all_filter_matches" if not (envelope.text or "").strip() else None
        )
        _verify_person_collection(service.catalog, collection_scope)
        _verify_fixed_person_revision(service.catalog, fixed_person_revision)
        _verify_favorites_revision(service.catalog, favorites_revision)
        _attach_favorites_echo(result, favorites_only=favorites_only, favorites_revision=favorites_revision)
        return result
    collection_revision = () if collection_scope is None else ("person_collection", collection_scope["collection_id"], collection_scope["instance_token"], collection_scope["revision"])
    session_revision = tuple(revision or ()) + collection_revision + tuple(search_time_identity)
    result = search_request(
        service.catalog, envelope, sessions=context.search_sessions,
        encoder=None if text_mode else context.query_encoder(),
        reranker=None if text_mode else context.reranker(),
        album_asset_ids=None if place_membership_sql is not None else album_ids,
        album_scope_revision=session_revision or None,
        collection_id=None if collection_scope is None else collection_scope["collection_id"],
        album_membership_sql=place_membership_sql,
        album_membership_parameters=place_membership_parameters,
        additional_condition_sql=search_time_sql,
        additional_condition_parameters=search_time_parameters,
        favorites_only=favorites_only,
    )
    result["place_scope"] = place_block
    result["original_query"] = original
    result["content_query"] = query_text
    result["place_parse_notice"] = parse_notice
    result["intent_plan"] = None if text_mode else intent_plan
    result["collection_scope"] = collection_scope
    result["search_scope"] = resolved_search_scope
    result["photo_collection_source_mode"] = "ranked_candidates"
    _attach_favorites_echo(result, favorites_only=favorites_only, favorites_revision=favorites_revision)
    if collection_scope is not None:
        result["filters"]["person_collection"] = dict(collection_scope)
    if kind is not None and key is not None:
        from photo_search.places.timeline import selected_members_sql

        _, _, after_place_revision = selected_members_sql(
            service.catalog, service.data_root, kind=kind, key=str(key),
        )
        if tuple(after_place_revision[name] for name in (
            "catalog_filter_revision", "suggestion_revision", "package_revision",
        )) != tuple(revision[:3]):
            raise ApiError(CATALOG_FILTER_REVISION_STALE, "the place scope changed; restart the search", status=409)
    _verify_person_collection(service.catalog, collection_scope)
    _verify_fixed_person_revision(service.catalog, fixed_person_revision)
    _verify_favorites_revision(service.catalog, favorites_revision)
    return result


def _favorites_revision(catalog: Any) -> int:
    from photo_search.favorites import _revision

    return _revision(catalog.connection)


def _verify_favorites_revision(catalog: Any, expected: int | None) -> None:
    if expected is not None and _favorites_revision(catalog) != int(expected):
        raise ApiError(
            "FAVORITES_REVISION_STALE", "收藏已变化，请重新搜索", status=409,
            details={"reason": "FAVORITES_REVISION_CHANGED", "favorites_revision": int(expected),
                     "current_favorites_revision": _favorites_revision(catalog)},
        )


def _attach_favorites_echo(
    result: dict[str, Any], *, favorites_only: bool, favorites_revision: int | None,
) -> None:
    result["favorites_only"] = bool(favorites_only)
    if favorites_only:
        result["favorites_revision"] = int(favorites_revision if favorites_revision is not None else 0)
        result.setdefault("filters", {})["favorites_only"] = True
        result["filters"]["favorites_revision"] = result["favorites_revision"]


def _require_favorites_cursor_revision(cursor: str | None, expected: int | None) -> None:
    """Give a changed favorites member set its own stale code before generic cursor checks."""
    if cursor is None or expected is None:
        return
    try:
        raw = base64.urlsafe_b64decode(str(cursor) + "=" * (-len(str(cursor)) % 4))
        values = json.loads(raw)
        token_revision = int(values[-1])
    except (ValueError, TypeError, IndexError, json.JSONDecodeError):
        return  # The normal cursor decoder returns the established invalid-cursor response.
    if token_revision != int(expected):
        raise ApiError(
            "FAVORITES_REVISION_STALE", "收藏已变化，请重新搜索", status=409,
            details={"reason": "FAVORITES_REVISION_CHANGED",
                     "favorites_revision": token_revision,
                     "current_favorites_revision": int(expected)},
        )


def _verify_fixed_person_revision(catalog: Any, expected_revision: int | None) -> None:
    """The fixed album identity was resolved before the retrieval's own snapshot."""
    if expected_revision is not None and place_revision.read_filter_revision(catalog.connection) != expected_revision:
        raise ApiError(CATALOG_FILTER_REVISION_STALE, "人物范围已变化，请重新搜索", status=409)


def _verify_person_collection(catalog: Any, collection_scope: dict[str, Any] | None) -> None:
    if collection_scope is None:
        return
    row = catalog.read_one(
        "SELECT instance_token, revision FROM person_collections WHERE collection_id = ?",
        (int(collection_scope["collection_id"]),),
    )
    if (row is None or str(row["instance_token"]) != str(collection_scope["instance_token"])
            or int(row["revision"]) != int(collection_scope["revision"])):
        raise ApiError(CATALOG_FILTER_REVISION_STALE, "人物合集已变化，请重新搜索", status=409)


def _search_time_condition(catalog: Any, plan: dict[str, Any] | None) -> tuple[str | None, tuple[Any, ...], tuple[Any, ...]]:
    """Compile the SEARCH-MAP additive time intent into one bound read predicate."""
    if not plan:
        return None, (), ()
    basis = str(plan.get("time_basis", "capture"))
    status = plan.get("capture_status")
    if basis == "library" and (plan.get("library_from") or plan.get("library_to_exclusive")):
        from datetime import date, datetime, time

        def millis(value: Any) -> int:
            return int(datetime.combine(date.fromisoformat(str(value)), time.min).timestamp() * 1000)

        try:
            low = millis(plan["library_from"]) if plan.get("library_from") else None
            high = millis(plan["library_to_exclusive"]) if plan.get("library_to_exclusive") else None
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise ApiError(INVALID_CURSOR, "入库时间条件无效，请重新搜索", status=409) from error
        if low is not None and high is not None and low >= high:
            raise ApiError(INVALID_CURSOR, "入库时间范围无效，请重新搜索", status=409)
        fragments = []
        parameters: list[int] = []
        if low is not None:
            fragments.append("a.created_at >= ?")
            parameters.append(low)
        if high is not None:
            fragments.append("a.created_at < ?")
            parameters.append(high)
        return " AND ".join(fragments), tuple(parameters), (basis, low, high)
    if basis == "capture" and status in {"unknown", "pending"}:
        from photo_search.api.timeline import UNKNOWN_SOURCES
        from photo_search.taken_time import read as time_read

        if status == "unknown":
            if not time_read.rules_version_matches(catalog.connection):
                return "0", (), (basis, status, "rules-mismatch")
            marks = ",".join("?" for _ in UNKNOWN_SOURCES)
            return (f"EXISTS (SELECT 1 FROM asset_metadata AS tm WHERE tm.asset_id=a.asset_id "
                    f"AND tm.time_source IN ({marks}))", tuple(UNKNOWN_SOURCES), (basis, status))
        if not time_read.rules_version_matches(catalog.connection):
            return "1", (), (basis, status, "rules-mismatch")
        return ("NOT EXISTS (SELECT 1 FROM asset_metadata AS tm WHERE tm.asset_id=a.asset_id)",
                (), (basis, status))
    return None, (), ()


def _filter_capture_desc_rows(catalog: Any, where: str, parameters: tuple[Any, ...], *,
                              limit: int, position: list[int] | None) -> list[Any]:
    """Read trusted EXIF streams by the shared capture index, then the unknown tail."""
    trusted_sources = ("EXIF_ORIGINAL", "EXIF_DIGITIZED")
    known: list[Any] = []
    if position is None or int(position[0]) == 1:
        for source in trusted_sources:
            clauses = ["m.time_source=?", "m.taken_at_sort IS NOT NULL", f"({where})"]
            values: list[Any] = [source, *parameters]
            if position is not None:
                clauses.append("(m.taken_at_sort,m.asset_id) < (?,?)")
                values.extend((int(position[1]), int(position[2])))
            known.extend(catalog.read_all(
                "SELECT a.asset_id,m.taken_at_sort AS capture_sort FROM asset_metadata AS m "
                "INDEXED BY idx_asset_metadata_capture CROSS JOIN assets AS a ON a.asset_id=m.asset_id "
                f"WHERE {' AND '.join(clauses)} "
                "ORDER BY m.taken_at_sort DESC,m.asset_id DESC LIMIT ?",
                (*values, int(limit) + 1),
            ))
        known.sort(key=lambda row: (int(row["capture_sort"]), int(row["asset_id"])), reverse=True)
        known = known[:int(limit) + 1]
    if len(known) > int(limit):
        return known

    unknown_limit = int(limit) + 1 - len(known)
    unknown_clauses = [
        f"({where})",
        "NOT (m.time_source IN ('EXIF_ORIGINAL','EXIF_DIGITIZED') AND m.taken_at_sort IS NOT NULL)",
    ]
    unknown_values = list(parameters)
    if position is not None and int(position[0]) == 0:
        unknown_clauses.append("a.asset_id < ?")
        unknown_values.append(int(position[2]))
    unknown = catalog.read_all(
        "SELECT a.asset_id,NULL AS capture_sort FROM assets AS a "
        "LEFT JOIN asset_metadata AS m ON m.asset_id=a.asset_id "
        f"WHERE {' AND '.join(unknown_clauses)} "
        "ORDER BY a.asset_id DESC LIMIT ?",
        (*unknown_values, unknown_limit),
    )
    return known + unknown


def _filter_only_search_page(service: Any, request: SearchRequest, *, collection_scope: dict[str, Any] | None = None, additional_condition_sql: str | None = None, additional_condition_parameters: tuple[Any, ...] = (), time_identity: tuple[Any, ...] = (), favorites_only: bool = False, include_statistics: bool = True, order: str = "asset_id", capture_position: list[int] | None = None) -> dict[str, Any]:
    """Unbounded keyset pages for pure filters, without materializing IDs or sessions."""
    from photo_search.search.service import _person_authority_base

    catalog = service.catalog
    base = _person_authority_base(catalog, request)
    scope = SearchScope.build(
        source_id=request.source_id, availability=request.availability,
        place_ids=request.place_ids, include_descendants=request.include_descendants,
        place_state=request.place_state, person_ids=base.canonical_person_ids,
        person_match=request.person_match, capture=request.capture_range,
        collection_id=None if collection_scope is None else collection_scope["collection_id"],
        additional_condition_sql=additional_condition_sql,
        additional_condition_parameters=additional_condition_parameters,
        favorites_only=favorites_only,
    )
    fingerprint_payload = json.dumps({
        "retrieval_mode": request.retrieval_mode,
        "source_id": scope.source_id, "availability": scope.availability,
        "place_ids": list(scope.place_ids), "include_descendants": scope.include_descendants,
        "place_state": scope.place_state, "person_ids": list(scope.person_ids),
        "person_match": scope.person_match, "capture_from": request.capture_from,
        "capture_to_exclusive": request.capture_to_exclusive, "page_size": request.page_size,
        "person_collection": collection_scope,
        "time_identity": list(time_identity),
        "favorites_only": bool(favorites_only),
    }, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest()
    favorites_revision = _favorites_revision(catalog) if favorites_only else None
    revisions = (base.filter_revision, base.suggestion_revision, "filter-only", fingerprint,
                 None if collection_scope is None else collection_scope["instance_token"],
                 None if collection_scope is None else collection_scope["revision"], favorites_revision)
    session_id = "filter-only"
    capture_after = None
    if order not in {"asset_id", "capture_desc"}:
        raise ApiError("VALIDATION_ERROR", "unsupported filter ordering")
    if order == "capture_desc":
        if request.cursor is not None:
            raise ApiError(INVALID_CURSOR, "the capture cursor does not match this search", status=409)
        if capture_position is not None:
            if (len(capture_position) != len(revisions) + 3
                    or capture_position[:len(revisions)] != list(revisions)):
                raise ApiError(CATALOG_FILTER_REVISION_STALE,
                               "the filter scope changed; restart the smart collection page", status=409)
            capture_after = capture_position[len(revisions):]
            if (any(isinstance(value, bool) or not isinstance(value, int) for value in capture_after)
                    or capture_after[0] not in (0, 1) or capture_after[1] < 0
                    or capture_after[2] < 1):
                raise ApiError(INVALID_CURSOR, "the capture cursor does not match this search", status=409)
        after = 0
    elif request.cursor is not None:
        if request.search_session_id != session_id:
            raise ApiError(INVALID_CURSOR, "the filter cursor does not match this search")
        _require_favorites_cursor_revision(request.cursor, favorites_revision)
        after = place_albums._decode(request.cursor, revisions)
    else:
        after = 0
    where, params = scope_condition(catalog.connection, scope)
    total = (int(catalog.read_one(f"SELECT COUNT(*) AS value FROM assets AS a WHERE {where}", tuple(params))["value"])
             if include_statistics else None)
    rows = (_filter_capture_desc_rows(catalog, where, tuple(params), limit=int(request.page_size),
                                      position=capture_after)
            if order == "capture_desc" else catalog.read_all(
                f"SELECT a.asset_id,NULL AS capture_sort FROM assets AS a WHERE ({where}) AND a.asset_id>? ORDER BY a.asset_id LIMIT ?",
                (*params, int(after), int(request.page_size) + 1),
            ))
    if (place_revision.read_filter_revision(catalog.connection),
        place_revision.read_suggestion_revision(catalog.connection)) != revisions[:2]:
        raise ApiError(CATALOG_FILTER_REVISION_STALE, "the filters changed; restart the search", status=409)
    _verify_favorites_revision(catalog, favorites_revision)
    page = rows[: int(request.page_size)]
    ids = [int(row["asset_id"]) for row in page]
    offset = int(catalog.read_one(
        f"SELECT COUNT(*) AS value FROM assets AS a WHERE ({where}) AND a.asset_id<=?",
        (*params, int(after)),
    )["value"]) if order == "asset_id" and after and include_statistics else 0
    has_more = len(rows) > len(page)
    token = place_albums._token(ids[-1], revisions) if order == "asset_id" and has_more and ids else None
    hits = [{"asset_id": asset_id, "rank": offset + index + 1,
             "score": None, "matched_by": {"filter": offset + index + 1},
             "hit_reason": "FILTER_ONLY", "matched_text": [], "semantic_ready": False}
            for index, asset_id in enumerate(ids)]
    result = {
        "contract_version": "p2-contracts.v1", "request_id": request.request_id,
        "retrieval_mode": request.retrieval_mode,
        "search_session_id": session_id, "profile_id": None, "image_generation_id": None,
        "space_id": None, "dimension": None,
        "catalog_filter_revision": int(base.filter_revision),
        "suggestion_revision": int(base.suggestion_revision), "fusion_config": {},
        "query": None,
        "filters": {"source_id": scope.source_id, "availability": scope.availability,
                    "place_ids": list(scope.place_ids), "include_descendants": scope.include_descendants,
                    "place_state": scope.place_state, "person_ids": list(scope.person_ids),
                    "person_match": scope.person_match, "capture_from": request.capture_from,
                    "capture_to_exclusive": request.capture_to_exclusive,
                    "person_collection": collection_scope},
        "hits": hits, "page_size": int(request.page_size), "page_offset": offset,
        "next_cursor": token, "has_more": has_more, "total_candidates": total,
        "candidates_truncated": False,
        "channels": {"fts": {"engaged": False, "candidates": 0, "truncated": False},
                     "vector": {"engaged": False, "candidates": 0, "scan_truncated": False},
                     "authoritative": {"total": total, "listed": len(ids),
                                       "truncated": False, "order": "asset_id"}},
        "pending_semantic": None, "search_projection": _projection_block(catalog, match=None, catch_up=None),
        "rerank_state": "NOT_REQUESTED", "reranked": None, "state": "OK",
    }
    if order == "capture_desc":
        last = rows[len(page) - 1] if page else None
        result["smart_capture_position"] = (
            [*revisions, int(last["capture_sort"] is not None), int(last["capture_sort"] or 0),
             int(last["asset_id"])]
            if has_more and last else None
        )
    _attach_favorites_echo(result, favorites_only=favorites_only, favorites_revision=favorites_revision)
    return result


def _place_album_search_page(service: Any, request: SearchRequest, *, kind: str, key: str, album_ids: list[int], collection: dict[str, Any], place_block: dict[str, Any], original_query: str, person_collection: dict[str, Any] | None = None, membership_sql: str | None = None, membership_parameters: tuple[Any, ...] = (), additional_condition_sql: str | None = None, additional_condition_parameters: tuple[Any, ...] = (), time_identity: tuple[Any, ...] = (), favorites_only: bool = False, filter_exclusion_smart_id: int | None = None, include_statistics: bool = True, order: str = "asset_id", capture_position: list[Any] | None = None) -> dict[str, Any]:
    """Uncapped SQLite pages for a pure selected place; keeps album membership authoritative."""
    catalog = service.catalog
    favorites_revision = _favorites_revision(catalog) if favorites_only else None
    base = _person_authority_base(catalog, request)
    if filter_exclusion_smart_id is not None:
        exclusion_sql = (
            "NOT EXISTS (SELECT 1 FROM photo_smart_exclusions AS pse "
            "WHERE pse.smart_id=? AND pse.asset_id=a.asset_id)"
        )
        if additional_condition_sql is None:
            additional_condition_sql = exclusion_sql
            additional_condition_parameters = (int(filter_exclusion_smart_id),)
        else:
            additional_condition_sql = f"({additional_condition_sql}) AND ({exclusion_sql})"
            additional_condition_parameters += (int(filter_exclusion_smart_id),)
        time_identity = (*time_identity, "smart_exclusions", int(filter_exclusion_smart_id))
    scope = SearchScope.build(
        source_id=request.source_id,
        availability=request.availability,
        place_ids=request.place_ids,
        include_descendants=request.include_descendants,
        place_state=request.place_state,
        person_ids=base.canonical_person_ids,
        person_match=request.person_match,
        capture=request.capture_range,
        album_asset_ids=None,
        album_membership_sql=membership_sql,
        album_membership_parameters=membership_parameters,
        additional_condition_sql=additional_condition_sql,
        additional_condition_parameters=additional_condition_parameters,
        collection_id=None if person_collection is None else person_collection["collection_id"],
        favorites_only=favorites_only,
    )
    filter_payload = json.dumps({"retrieval_mode": request.retrieval_mode, "source_id": request.source_id, "availability": request.availability, "place_ids": list(request.place_ids), "include_descendants": request.include_descendants, "place_state": request.place_state, "person_ids": list(scope.person_ids), "person_match": request.person_match, "capture_from": request.capture_from, "capture_to_exclusive": request.capture_to_exclusive, "page_size": request.page_size, "person_collection": person_collection, "time_identity": list(time_identity), "favorites_only": bool(favorites_only)}, sort_keys=True, separators=(",", ":"))
    filter_fingerprint = hashlib.sha256(filter_payload.encode("utf-8")).hexdigest()
    revisions = (collection["catalog_filter_revision"], collection["suggestion_revision"], collection["package_revision"], kind, key, filter_fingerprint, None if person_collection is None else person_collection["instance_token"], None if person_collection is None else person_collection["revision"], favorites_revision)
    session_id = f"place-album:{kind}:{key}"
    if order not in {"asset_id", "capture_desc"}:
        raise ApiError("VALIDATION_ERROR", "unsupported filter ordering")
    capture_after = None
    if order == "capture_desc":
        if request.cursor is not None:
            raise ApiError(INVALID_CURSOR, "the capture cursor does not match this search", status=409)
        if capture_position is not None:
            if (len(capture_position) != len(revisions) + 3
                    or capture_position[:len(revisions)] != list(revisions)):
                raise ApiError(CATALOG_FILTER_REVISION_STALE,
                               "the place scope changed; restart the smart collection page", status=409)
            capture_after = capture_position[len(revisions):]
            if (any(isinstance(value, bool) or not isinstance(value, int) for value in capture_after)
                    or capture_after[0] not in (0, 1) or capture_after[1] < 0
                    or capture_after[2] < 1):
                raise ApiError(INVALID_CURSOR, "the capture cursor does not match this search", status=409)
        start = 0
    elif request.cursor is not None:
        if request.search_session_id != session_id:
            raise ApiError(INVALID_CURSOR, "the place album cursor does not match this search")
        _require_favorites_cursor_revision(request.cursor, favorites_revision)
        start = place_albums._decode(request.cursor, revisions)
    else:
        start = 0
    where, params = scope_condition(catalog.connection, scope)
    total = (int(catalog.read_one(f"SELECT COUNT(*) AS value FROM assets AS a WHERE {where}", tuple(params))["value"])
             if include_statistics else None)
    rows = (_filter_capture_desc_rows(catalog, where, tuple(params), limit=int(request.page_size),
                                      position=capture_after)
            if order == "capture_desc" else catalog.read_all(
                f"SELECT a.asset_id,NULL AS capture_sort FROM assets AS a WHERE ({where}) AND a.asset_id>? "
                "ORDER BY a.asset_id LIMIT ?",
                (*params, int(start), int(request.page_size) + 1),
            ))
    offset = int(catalog.read_one(
        f"SELECT COUNT(*) AS n FROM assets AS a WHERE ({where}) AND a.asset_id<=?",
        (*params, int(start)),
    )["n"]) if start and include_statistics and order == "asset_id" else 0
    from photo_search.places.timeline import selected_members_sql

    _, _, current_revisions = selected_members_sql(
        catalog, service.data_root, kind=kind, key=key,
    )
    if (
        current_revisions["catalog_filter_revision"],
        current_revisions["suggestion_revision"],
        current_revisions["package_revision"],
    ) != revisions[:3]:
        raise ApiError(CATALOG_FILTER_REVISION_STALE, "the place album changed; reload the search", status=409)
    _verify_favorites_revision(catalog, favorites_revision)
    page = rows[: int(request.page_size)]
    has_more = len(rows) > len(page)
    next_offset = int(page[-1]["asset_id"]) if page else start
    token = place_albums._token(next_offset, revisions) if has_more and order == "asset_id" else None
    ids = [int(row["asset_id"]) for row in page]
    hits = [{"asset_id": asset_id, "rank": offset + index + 1, "score": None, "matched_by": {"filter": offset + index + 1}, "hit_reason": "FILTER_ONLY", "matched_text": [], "semantic_ready": False} for index, asset_id in enumerate(ids)]
    result = {
        "contract_version": "p2-contracts.v1", "request_id": request.request_id,
        "retrieval_mode": request.retrieval_mode,
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
                    "suggestion_revision": int(collection["suggestion_revision"]),
                    "person_collection": person_collection},
        "hits": hits, "page_size": int(request.page_size), "page_offset": offset,
        "next_cursor": token, "has_more": has_more, "total_candidates": total,
        "candidates_truncated": False,
        "channels": {"fts": {"engaged": False, "candidates": 0, "truncated": False},
                     "vector": {"engaged": False, "candidates": 0, "scan_truncated": False},
                     "authoritative": {"total": total, "listed": len(ids), "truncated": False, "order": "asset_id"}},
        "pending_semantic": None,
        "search_projection": _projection_block(catalog, match=None, catch_up=None),
        "rerank_state": "NOT_REQUESTED", "reranked": None, "state": "OK",
        "place_scope": place_block, "collection_scope": person_collection,
        "original_query": original_query, "content_query": None,
    }
    if order == "capture_desc":
        last = rows[len(page) - 1] if page else None
        result["smart_capture_position"] = (
            [*revisions, int(last["capture_sort"] is not None), int(last["capture_sort"] or 0),
             int(last["asset_id"])]
            if has_more and last else None
        )
    _attach_favorites_echo(result, favorites_only=favorites_only, favorites_revision=favorites_revision)
    return result


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
    return await run_catalog_search(
        context.service().catalog, _search_image_worker, body,
        content_type=request.headers.get("content-type"), context=context,
    )


def _search_image_worker(
    catalog: Any, body: bytes, *, content_type: str | None, context: ApiContext,
) -> dict[str, Any]:
    form = parse_multipart_form(
        content_type,
        body,
        max_body_bytes=DEFAULT_LIMITS.max_body_bytes,
    )
    upload = form.files.get("image")
    if upload is None:
        raise search_fail(
            "the request must carry one multipart file field named image", field="image"
        )
    envelope = _image_search_request(form.fields)
    binding = resolve_request_binding(catalog, envelope)
    if binding is None:
        raise search_fail(
            "an image query needs a bound space_id, image_generation_id and dimension "
            "(or an activated search profile)",
            field="profile_id",
        )
    encoder = image_encoder_of(context.query_encoder())
    facts, vector = _prepare_upload(
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
                encoder, staged.path, space_id=str(space_id), dimension=int(dimension)
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
