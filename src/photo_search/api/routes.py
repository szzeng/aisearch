"""The read-only business API plus the controlled file endpoints (architecture §10.2).

Every handler is ``async`` and does its catalog work on the event loop thread, which is the
thread the owned SQLite connection was opened on. Blocking work that can be moved off the
loop (reading original bytes) is done in a worker thread; the decode of a single asset
(tens of milliseconds, one asset per request) stays synchronous on purpose so the catalog
connection is never touched from a second thread.
"""

from __future__ import annotations

import asyncio
from email.utils import formatdate
from typing import Any, Literal

from fastapi import APIRouter, Query, Request, Response
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
    GeoFactResponse,
    JobListResponse,
    JobRecord,
    PlaceAssignmentRequest,
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
    SourceDetail,
    SourceListResponse,
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
from photo_search.catalog.errors import CatalogError
from photo_search.catalog.migrate import catalog_info
from photo_search.config import catalog_db_path
from photo_search.ingestion.service import CatalogService
from photo_search.media.cache import MediaCache
from photo_search.media.contract import PREVIEW, SPECS, THUMBNAIL, spec_for
from photo_search.media.pipeline import MediaInterrupted
from photo_search.media.status import (
    MEDIA_STATE_FAILED,
    MEDIA_STATE_READY,
    MEDIA_STATE_UNSUPPORTED,
)
from photo_search.places.contracts import fail as place_fail
from photo_search.places.service import PlaceService, clamp_place_limit
from photo_search.scheduler import state as scheduler_state
from photo_search.scheduler.projection import (
    index_summary,
    scheduler_summary,
    search_profile_summary,
    search_projection_summary,
)
from photo_search.search.contracts import SearchRequest
from photo_search.search.profile import activate_profile, profile_projection
from photo_search.search.service import search_request

APP_VERSION = "0.1.0"

ARTIFACT_CACHE_CONTROL = "private, max-age=31536000, immutable"
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
@router.get("/sources", response_model=SourceListResponse, summary="Registered sources")
async def list_sources(request: Request) -> dict[str, Any]:
    service = service_of(request)
    return {
        "sources": [
            service.get_source(int(row["source_id"])) for row in service.list_sources()
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
    return service_of(request).get_source(int(source_id))


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
@router.get("/assets", response_model=AssetListResponse, summary="Paged asset list")
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
@router.post(
    "/search",
    response_model=SearchResultModel,
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
