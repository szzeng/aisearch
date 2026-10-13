"""P4-02Y §6: the one-click library refresh, stage by stage.

The refresh is not a second scanner, indexer or processor. It is the *orchestration* of the
existing entries in the order a daily update needs them:

1. **fast scan** every requested source with :func:`photo_search.ingestion.scanner.execute_scan`
   in ``fast`` mode, which reuses every binding a previous complete scan already confirmed
   with a full SHA-256 and routes the change set as two bounded deltas;
2. **search projection catch-up** so new/renamed/missing filenames are searchable now;
3. **semantic confirmation of the content delta** - the only step that has to finish before
   the run may call itself ``SEARCH_READY``;
4. the **enrichment stages** (thumbnail, taken-time, GPS, place resolution, face, OCR) which
   consume the deltas only and are allowed to be slow, unavailable or partly failed.

Two rules are structural rather than incidental:

* no stage ever walks the whole library when it only has to work on the delta, and every
  stage's work set is bounded by the selector the processor already owns;
* ``SEARCH_READY`` is decided by *evidence* (projection queue empty + the content delta
  confirmed in the served generation), never by reaching a stage name.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import (
    FORMAT_UNSUPPORTED,
    RUNTIME_CAPABILITY_UNAVAILABLE,
    CatalogError,
)
from photo_search.ingestion.scanner import (
    DEFAULT_OBSERVATION_BATCH,
    DELTA_LIMIT,
    SCAN_MODE_FAST,
    execute_scan,
)
from photo_search.jobs import queue as jobs
from photo_search.refresh.state import (
    STAGE_FAILED,
    STAGE_OK,
    STAGE_SKIPPED,
    STAGE_SKIPPED_NO_TARGETS,
    RefreshStatus,
)

#: The semantic selector's own bounded batch (the P4-02O default) and how many of them one
#: refresh may run before it reports the remaining work instead of holding the run forever.
SEMANTIC_BATCH_LIMIT = 5000
SEMANTIC_BATCH_CAP = 40
#: Search-projection catch-up: the P4-02V bounded catch-up, repeated until the durable queue
#: is empty. Every round is a short transaction, so a concurrent search is never blocked for
#: long; a round that cannot make progress stops and reports the lag it leaves.
PROJECTION_BATCH_SIZE = 500
PROJECTION_BATCHES_PER_ROUND = 8
PROJECTION_ROUND_CAP = 40
#: Per-stage cap of one refresh. A delta beyond it is reported as truncated and the rest is
#: left for the next refresh (the selectors are all resumable).
ENRICH_LIMIT = 5000
#: P4-02Y-I §5: the two run modes of ``POST /api/v1/library/refresh``. ``refresh`` is the
#: daily incremental pass of P4-02Y; ``build`` is the first build of a source, which walks
#: the *durable* backlog of the named sources in bounded pages until their semantic index is
#: complete instead of stopping at one bounded delta.
REFRESH_MODE_DAILY = "refresh"
REFRESH_MODE_BUILD = "build"
REFRESH_MODES = (REFRESH_MODE_DAILY, REFRESH_MODE_BUILD)
#: One bounded page of a build, and the defensive cap on how many pages/rounds one build may
#: spend on a single stage in one run. The cap is not a work budget - a 30 万 first build
#: needs ~60 semantic batches, and each enrichment stage ~60 pages - it only stops a stage
#: that keeps producing no durable progress.
BUILD_PAGE_SIZE = 5000
BUILD_ROUND_CAP = 10_000
#: The frozen job task type of the image-text embedding processor (P2/P4 spelling).
SEMANTIC_TASK_TYPE = "image_embedding"


def resolve_face_profile(catalog: Any, *, source_ids: Sequence[int] | None,
                         requested: str | None) -> str:
    """Persist the choice before scanning; retry/restart cannot change the selected path."""
    import json
    key = "face_build_profile:" + json.dumps(sorted(set(source_ids or [])), separators=(",", ":"))
    if requested is None:
        row = catalog.read_one("SELECT value FROM metadata WHERE key=?", (key,))
        profile = str(row["value"]) if row is not None else "source_detail"
    else:
        profile = str(requested)
    if profile not in ("source_detail", "lightweight"):
        raise ValueError("unknown face profile")
    with catalog.write() as connection:
        connection.execute("INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) "
                           "DO UPDATE SET value=excluded.value", (key, profile))
    return profile


def refresh_library(
    service: Any,
    status: RefreshStatus,
    *,
    source_ids: Sequence[int] | None = None,
    batch_size: int = DEFAULT_OBSERVATION_BATCH,
    mode: str = REFRESH_MODE_DAILY,
    face_profile: str | None = None,
    encoder_factory: Callable[[], Any] | None = None,
    manifest_path: str | Path | None = None,
    device: str = "auto",
    device_policy: str = "cpu_allowed",
    enrich: bool = True,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Run one complete refresh and return the final status snapshot.

    ``service`` is the refresh's own :class:`~photo_search.ingestion.service.CatalogService`
    (one connection, one thread); the caller owns it. Every stage failure is *reported*: the
    run only ends as ``FAILED`` when the core search path itself could not be brought up to
    date, otherwise ``COMPLETE``/``PARTIAL_FAILED`` say exactly which enrichment is missing.

    ``mode`` selects between the P4-02Y daily pass (``refresh``, delta-driven and bounded by
    one round per stage) and the P4-02Y-I first build (``build``): the build keeps draining
    the *durable* backlog of the requested sources in bounded pages, so a 20-30 万 source
    cannot be left half-built by a delta or batch ceiling, and an interrupted build is
    continued by pressing the button again.
    """
    if mode not in REFRESH_MODES:
        raise ValueError(f"unknown refresh mode {mode!r}")
    build = mode == REFRESH_MODE_BUILD
    catalog = service.catalog
    face_profile = resolve_face_profile(catalog, source_ids=source_ids, requested=face_profile)
    status.set_face_profile(face_profile)
    started = time.perf_counter()
    content_delta: set[int] = set()
    location_delta: set[int] = set()
    overflow = False

    def absorb(target: set[int], values: Iterable[int]) -> bool:
        """Merge one scan's routing ids, bounded by the refresh-wide delta limit."""
        nonlocal overflow
        bounded = False
        for value in values:
            if len(target) >= DELTA_LIMIT:
                overflow = True
                bounded = True
                break
            target.add(int(value))
        return bounded

    # ------------------------------------------------------------------ A. fast scan
    status.set_state("SCANNING")
    requested = (
        [int(value) for value in source_ids]
        if source_ids is not None
        else [int(row["source_id"]) for row in catalog.read_all(
            "SELECT source_id FROM source_roots ORDER BY source_id"
        )]
    )
    status.set_counters(sources_requested=len(requested))
    for source_id in requested:
        outcome = _scan_one(
            service,
            status,
            source_id=source_id,
            content_delta=content_delta,
            location_delta=location_delta,
            absorb=absorb,
            cancel_check=cancel_check,
        )
        if outcome is None:
            continue
        overflow = overflow or bool(outcome["delta_overflow"])
    if overflow:
        content_delta.clear()
        location_delta.clear()
    scopes: list[int | None] = (
        [int(value) for value in requested] if requested else [None]
    )

    # ------------------------------------------------- B. search projection catch-up
    status.set_state("INDEXING")
    projection = _catch_up_projection(
        catalog,
        cancel_check=cancel_check,
        round_cap=BUILD_ROUND_CAP if build else PROJECTION_ROUND_CAP,
    )
    status.set_stage("search_projection", projection)

    # ------------------------------------------------ C. semantic (the SEARCH_READY gate)
    if build:
        semantic = _semantic_build_stage(
            catalog,
            status,
            scopes=scopes,
            content_delta=content_delta,
            delta_overflow=overflow,
            encoder_factory=encoder_factory,
            manifest_path=manifest_path,
            device=device,
            device_policy=device_policy,
            cancel_check=cancel_check,
        )
    else:
        semantic = _semantic_stage(
            catalog,
            status,
            content_delta=content_delta,
            delta_overflow=overflow,
            encoder_factory=encoder_factory,
            manifest_path=manifest_path,
            device=device,
            device_policy=device_policy,
            cancel_check=cancel_check,
        )
    status.set_stage("semantic", semantic)

    search_ready, basis = _search_ready(projection, semantic, mode=mode)
    if search_ready:
        status.mark_search_ready(
            basis=basis, elapsed_ms=_elapsed_ms(started)
        )
    else:
        status.note_search_gate(basis=basis)

    # -------------------------------------------------------------- D..G. enrichment
    enrichment: dict[str, Any] = {}
    if enrich:
        status.set_state("ENRICHING")
        enrichment = _enrichment_stages(
            service,
            status,
            mode=mode,
            face_profile=face_profile,
            scopes=scopes,
            content_delta=content_delta,
            location_delta=location_delta,
            delta_overflow=overflow,
            encoder_factory=encoder_factory,
            manifest_path=manifest_path,
            device=device,
            device_policy=device_policy,
            cancel_check=cancel_check,
        )

    failures = [
        name
        for name, payload in {**semantic_as_stage(semantic), **enrichment}.items()
        if str(payload.get("state")) == STAGE_FAILED
    ]
    if int(semantic.get("failed_assets", 0)) > 0:
        # Content the encoder was handed and refused is reported as a partial failure: the
        # rest of the library is searchable, and the next refresh retries that content.
        failures.append("semantic-content")
    if mode == REFRESH_MODE_BUILD and not search_ready and str(basis) in (
        "semantic-backlog",
        "semantic-confirmed-nothing",
    ):
        # A build that ran out of its bounded rounds, or one whose images could not be
        # encoded at all, is *not* a failure of the library and not a partial success either:
        # the core search path is simply not up to date yet. The run ends as FAILED (there is
        # no "incomplete" state in the frozen vocabulary) but names the reason, so the card can
        # say "还有 N 项语义索引待处理，可再次点击继续" or "N 张图片无法编码" instead of the
        # same word as a broken scan (P4-02Y-I review B1/F1).
        failures.append(str(basis))
    if mode == REFRESH_MODE_BUILD and not search_ready:
        # A build has one job: bring the named sources up to a searchable state. A run that did
        # not get there is FAILED, and ``failed_stages`` names why - "semantic-backlog" for a
        # bounded run that still has durable work left (press the button again to continue),
        # an unavailable semantic channel, or content the encoder refused. The P4-02Y daily
        # precedence below is deliberately left exactly as it was reviewed.
        final = "FAILED"
    elif failures:
        final = "PARTIAL_FAILED"
    elif not search_ready:
        final = "FAILED"
    else:
        final = "COMPLETE"
    status.set_failed_stages(failures)
    status.finish(state=final, elapsed_ms=_elapsed_ms(started))
    return status.snapshot()


def semantic_as_stage(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """One entry so a failed *core* stage is reported with the enrichment failures."""
    return {"semantic": payload}


# ------------------------------------------------------------------------------- scanning
def _scan_one(
    service: Any,
    status: RefreshStatus,
    *,
    source_id: int,
    content_delta: set[int],
    location_delta: set[int],
    absorb: Callable[[set[int], Iterable[int]], bool],
    cancel_check: Callable[[], bool] | None,
) -> dict[str, Any] | None:
    """Fast-scan one source; a source that cannot be read must not stop the others."""
    started = time.perf_counter()
    try:
        outcome = execute_scan(
            service,
            int(source_id),
            mode=SCAN_MODE_FAST,
            cancel_check=cancel_check,
        )
    except CatalogError as error:
        status.add_source(
            {
                "source_id": int(source_id),
                "state": "FAILED",
                "code": str(error.code),
                "message": str(error.message)[:200],
                "elapsed_ms": _elapsed_ms(started),
            }
        )
        status.note_error(
            stage="scan",
            code=str(error.code),
            message=str(error.message)[:200],
            source_id=int(source_id),
        )
        status.add_counters(sources_failed=1)
        return None
    payload = outcome.as_dict()
    content_ids, location_ids = outcome.deltas()
    absorb(content_delta, content_ids)
    absorb(location_delta, location_ids)
    counters = {
        key: payload[key]
        for key in (
            "enumerated",
            "unchanged_reused",
            "hash_targets",
            "hash_bytes",
            "new_assets",
            "rebound_assets",
            "locations_added",
            "locations_missing",
            "content_delta_count",
            "location_delta_count",
        )
    }
    status.add_counters(sources_scanned=1, **counters)
    status.add_counters(scan_ms=int(payload["enumeration_ms"]) + int(payload["processing_ms"]))
    status.add_source(
        {
            "source_id": int(source_id),
            "state": "SCANNED",
            "scan_id": int(payload["scan_id"]),
            "elapsed_ms": _elapsed_ms(started),
            **counters,
            "delta_overflow": bool(payload["delta_overflow"]),
        }
    )
    return payload


# ---------------------------------------------------------------------- search projection
def _catch_up_projection(
    catalog: Any,
    *,
    cancel_check: Callable[[], bool] | None,
    round_cap: int = PROJECTION_ROUND_CAP,
) -> dict[str, Any]:
    """Derive the search documents of the queued assets until the durable queue is empty."""
    from photo_search.search import documents

    processed = written = rounds = deferred = 0
    state = "SYNCED"
    for _ in range(max(int(round_cap), 1)):
        result = documents.catch_up(
            catalog,
            batches=PROJECTION_BATCHES_PER_ROUND,
            batch_size=PROJECTION_BATCH_SIZE,
        )
        processed += int(result["processed"])
        written += int(result["written"])
        deferred += int(result["deferred_batches"])
        rounds += 1
        state = str(result["state"])
        if state == "SYNCED":
            break
        if int(result["processed"]) == 0:
            # LAGGING with nothing applied: the writer lock belongs to someone else right
            # now. Stop here, keep the durable queue, and let the caller see the lag.
            break
        if cancel_check is not None and cancel_check():
            state = "CANCELLED"
            break
    remaining = documents.queue_depth(catalog.connection)
    return {
        "state": state,
        "processed": processed,
        "written": written,
        "remaining": remaining,
        "rounds": rounds,
        "deferred_batches": deferred,
    }


# ------------------------------------------------------------------ semantic (core gate)
def _semantic_stage(
    catalog: Any,
    status: RefreshStatus,
    *,
    content_delta: set[int],
    delta_overflow: bool,
    encoder_factory: Callable[[], Any] | None,
    manifest_path: str | Path | None,
    device: str,
    device_policy: str,
    cancel_check: Callable[[], bool] | None,
) -> dict[str, Any]:
    """Confirm the content delta in the served semantic generation.

    The step is skipped *without loading a model* when the pinned manifest shows that nothing
    is pending for the served generation - which is what makes a no-op refresh cost no AI
    work at all. ``targets``/``encoded`` are reported so the acceptance evidence can show a
    zero.
    """
    from photo_search.indexing import semantic
    from photo_search.search import profile as profile_module

    if manifest_path is None:
        return {
            "state": STAGE_SKIPPED,
            "reason": "MANIFEST_UNCONFIGURED",
            "targets": 0,
            "encoded": 0,
            "batches": 0,
            **_content_report(catalog, None, content_delta, None),
        }
    spec = semantic.semantic_spec(manifest_path, {}, device_policy=device_policy)
    derived_space = str(spec.envelope["space_id"])
    derived_generation = _generation_for(str(spec.processor_id), derived_space)
    served = _served_generation(catalog, profile_module, derived_space, derived_generation)
    if served is None:
        return {
            "state": STAGE_SKIPPED,
            "reason": "ACTIVE_PROFILE_SPACE_MISMATCH",
            "space_id": derived_space,
            "generation_id": derived_generation,
            "active_space_id": _active_profile_space(catalog, profile_module),
            "targets": 0,
            "encoded": 0,
            "batches": 0,
            **_content_report(catalog, derived_space, content_delta, spec),
        }
    space_id, generation_id = served
    report = _content_report(catalog, space_id, content_delta, spec)
    required = report["required"]
    selection = semantic.semantic_selection(
        catalog,
        space_id=space_id,
        processor_id=str(spec.processor_id),
        limit=SEMANTIC_BATCH_LIMIT,
    )
    targets = len(selection["targets"])
    if targets == 0 and bool(selection["coverage_complete"]):
        # Nothing pending in the served generation: either the changed content is already
        # confirmed, or every changed file is one this index is not about (a document, a
        # video, an archive).
        return {
            "state": STAGE_SKIPPED_NO_TARGETS,
            "space_id": space_id,
            "generation_id": generation_id,
            "targets": 0,
            "encoded": 0,
            "batches": 0,
            "required": required,
            **report,
        }
    if encoder_factory is None:
        return {
            "state": STAGE_SKIPPED,
            "reason": "MODEL_UNAVAILABLE",
            "space_id": space_id,
            "generation_id": generation_id,
            "targets": targets,
            "encoded": 0,
            "batches": 0,
            **_content_report(catalog, space_id, content_delta, spec),
        }
    try:
        encoder = encoder_factory()
    except Exception as error:  # noqa: BLE001 - an unusable model is a reported skip
        status.note_error(
            stage="semantic",
            code=RUNTIME_CAPABILITY_UNAVAILABLE,
            message=f"the image encoder could not be loaded ({type(error).__name__})",
        )
        return {
            "state": STAGE_SKIPPED,
            "reason": "MODEL_LOAD_FAILED",
            "space_id": space_id,
            "generation_id": generation_id,
            "targets": targets,
            "encoded": 0,
            "batches": 0,
            **_content_report(catalog, space_id, content_delta, spec),
        }
    batches = 0
    encoded = 0
    last: dict[str, Any] | None = None
    try:
        while batches < SEMANTIC_BATCH_CAP:
            payload = semantic.build_index(
                catalog,
                encoder=encoder,
                manifest_path=manifest_path,
                expected_space_id=space_id,
                expected_processor_id=str(spec.processor_id),
                limit=SEMANTIC_BATCH_LIMIT,
                device=device,
                device_policy=device_policy,
                worker_id="library-refresh",
            )
            batches += 1
            encoded += int(payload["targets"])
            last = payload
            if bool(payload["coverage_complete"]) or int(payload["targets"]) == 0:
                break
            if cancel_check is not None and cancel_check():
                break
        # A run that confirmed nothing into a space which has never been published leaves no
        # generation row for a profile to point at. That is not "up to date": this build has
        # no image index to serve at all, so the stage reports it instead of letting the
        # activation foreign key fail or claiming a searchable library (P4-02Y-I review B1).
        published = semantic.generation_published(
            catalog, space_id=space_id, processor_id=str(spec.processor_id)
        )
        if published:
            live_spec = semantic.semantic_spec(
                manifest_path,
                semantic.adapter_runtime_facts(encoder),
                device_policy=device_policy,
            )
            profile = semantic.ensure_search_profile(
                catalog,
                live_spec,
                space_id=space_id,
                generation_id=(
                    str(last["generation"]["generation_id"]) if last else generation_id
                ),
            )
        else:
            profile = {
                "profile_id": None,
                "activated": False,
                "reason": "GENERATION_NOT_PUBLISHED",
            }
    except CatalogError as error:
        status.note_error(
            stage="semantic", code=str(error.code), message=str(error.message)[:200]
        )
        return {
            "state": STAGE_FAILED,
            "code": str(error.code),
            "space_id": space_id,
            "generation_id": generation_id,
            "targets": targets,
            "encoded": encoded,
            "batches": batches,
            **_content_report(catalog, space_id, content_delta, spec),
        }
    if not published:
        return {
            "state": STAGE_SKIPPED,
            "reason": "GENERATION_NOT_PUBLISHED",
            "space_id": space_id,
            "generation_id": generation_id,
            "targets": targets,
            "encoded": encoded,
            "batches": batches,
            "profile": None,
            "profile_activated": False,
            **_content_report(catalog, space_id, content_delta, spec),
            "delta_overflow": bool(delta_overflow),
        }
    return {
        "state": STAGE_OK,
        "space_id": space_id,
        "generation_id": generation_id,
        "targets": targets,
        "encoded": encoded,
        "batches": batches,
        "coverage_complete": bool(last["coverage_complete"]) if last else False,
        "profile": profile["profile_id"],
        "profile_activated": bool(profile["activated"]),
        "profile_reason": profile.get("reason"),
        **_content_report(catalog, space_id, content_delta, spec),
        "delta_overflow": bool(delta_overflow),
    }


def _generation_for(processor_id: str, space_id: str) -> str:
    from photo_search.artifacts.store import generation_id_for

    return generation_id_for(space_id, processor_id)


def _semantic_build_stage(
    catalog: Any,
    status: RefreshStatus,
    *,
    scopes: Sequence[int | None],
    content_delta: set[int],
    delta_overflow: bool,
    encoder_factory: Callable[[], Any] | None,
    manifest_path: str | Path | None,
    device: str,
    device_policy: str,
    cancel_check: Callable[[], bool] | None,
) -> dict[str, Any]:
    """First build of a source: classify the scope, then encode it in bounded batches.

    Two bounded phases, both driven by durable state rather than by this run's delta:

    1. **classify** - page through the scope with the existing selector and let it *enqueue*
       the jobs of every image it accepts. This is what makes the source card's denominator
       exact early ("183 500 / 231 420") and what keeps a document or a video out of it;
    2. **encode** - call the existing :func:`photo_search.indexing.semantic.build_index` in
       its own bounded batches until the served generation has no pending image left. The
       step stops only when the selection is empty or when a whole batch confirmed nothing
       (then the remainder is reported, and the next press of the button retries it).

    ``SEARCH_READY`` is decided afterwards from the same durable backlog, never from the
    in-memory delta, so a 20-30 万 first build cannot be declared searchable after one batch.
    """
    from photo_search.indexing import semantic
    from photo_search.search import profile as profile_module

    if manifest_path is None:
        return {
            "state": STAGE_SKIPPED,
            "reason": "MANIFEST_UNCONFIGURED",
            "mode": REFRESH_MODE_BUILD,
            "scopes": [None if scope is None else int(scope) for scope in scopes],
            "classified": 0,
            "targets": 0,
            "encoded": 0,
            "batches": 0,
            "pages": 0,
            **_backlog_report(catalog, None, None, scopes),
        }
    spec = semantic.semantic_spec(manifest_path, {}, device_policy=device_policy)
    derived_space = str(spec.envelope["space_id"])
    processor_id = str(spec.processor_id)
    derived_generation = _generation_for(processor_id, derived_space)
    served = _served_generation(catalog, profile_module, derived_space, derived_generation)
    if served is None:
        return {
            "state": STAGE_SKIPPED,
            "reason": "ACTIVE_PROFILE_SPACE_MISMATCH",
            "mode": REFRESH_MODE_BUILD,
            "scopes": [None if scope is None else int(scope) for scope in scopes],
            "space_id": derived_space,
            "generation_id": derived_generation,
            "active_space_id": _active_profile_space(catalog, profile_module),
            "classified": 0,
            "targets": 0,
            "encoded": 0,
            "batches": 0,
            "pages": 0,
            **_backlog_report(catalog, derived_space, processor_id, scopes),
        }
    space_id, generation_id = served
    classified = 0
    pages = 0
    cancelled = False
    for scope in scopes:
        cursor = 0
        while pages < BUILD_ROUND_CAP:
            selection = semantic.semantic_selection(
                catalog,
                space_id=space_id,
                processor_id=processor_id,
                source_id=None if scope is None else int(scope),
                after_asset_id=cursor,
                limit=BUILD_PAGE_SIZE,
            )
            targets = list(selection["targets"])
            if not targets:
                break
            semantic.enqueue_targets(
                catalog,
                targets,
                processor_id=processor_id,
                now_ms=catalog.now_ms(),
                device=semantic.job_device(device),
            )
            classified += len(targets)
            pages += 1
            last_asset = max(int(item["asset_id"]) for item in targets)
            if last_asset <= cursor:
                break
            cursor = last_asset
            if cancel_check is not None and cancel_check():
                break
        if cancel_check is not None and cancel_check():
            cancelled = True
            break
    if not cancelled:
        # The whole classification sweep finished: from now on "this scope has been looked at"
        # is a durable fact, which is what lets the card tell 未建库 from 已扫描但无可索引图片.
        _mark_scopes_built(catalog, scopes=scopes)
    report = {
        "mode": REFRESH_MODE_BUILD,
        "scopes": [None if scope is None else int(scope) for scope in scopes],
        "space_id": space_id,
        "generation_id": generation_id,
        "classified": classified,
        "pages": pages,
        "coverage_complete": False,
    }
    if encoder_factory is None:
        return {
            "state": STAGE_SKIPPED,
            "reason": "MODEL_UNAVAILABLE",
            "targets": 0,
            "encoded": 0,
            "batches": 0,
            **report,
            **_backlog_report(catalog, space_id, processor_id, scopes),
            **_content_report(catalog, space_id, content_delta, spec),
        }
    try:
        encoder = encoder_factory()
    except Exception as error:  # noqa: BLE001 - an unusable model is a reported skip
        status.note_error(
            stage="semantic",
            code=RUNTIME_CAPABILITY_UNAVAILABLE,
            message=f"the image encoder could not be loaded ({type(error).__name__})",
        )
        return {
            "state": STAGE_SKIPPED,
            "reason": "MODEL_LOAD_FAILED",
            "targets": 0,
            "encoded": 0,
            "batches": 0,
            **report,
            **_backlog_report(catalog, space_id, processor_id, scopes),
            **_content_report(catalog, space_id, content_delta, spec),
        }
    encoded = 0
    batches = 0
    last: dict[str, Any] | None = None
    try:
        for scope in scopes:
            while batches < BUILD_ROUND_CAP:
                payload = semantic.build_index(
                    catalog,
                    encoder=encoder,
                    manifest_path=manifest_path,
                    expected_space_id=space_id,
                    expected_processor_id=processor_id,
                    limit=SEMANTIC_BATCH_LIMIT,
                    source_id=None if scope is None else int(scope),
                    device=device,
                    device_policy=device_policy,
                    worker_id="library-refresh",
                )
                batches += 1
                encoded += int(payload["targets"])
                last = payload
                if int(payload["targets"]) == 0:
                    break
                if int(payload["drain"]["confirmed_delta"]) == 0:
                    # The batch handed work to the encoder and got nothing confirmed back
                    # (every remaining image was refused). Stop, report the remainder and
                    # leave it to the next run instead of spinning on the same assets.
                    break
                if cancel_check is not None and cancel_check():
                    break
        if semantic.generation_published(
            catalog, space_id=space_id, processor_id=processor_id
        ):
            live_spec = semantic.semantic_spec(
                manifest_path,
                semantic.adapter_runtime_facts(encoder),
                device_policy=device_policy,
            )
            profile = semantic.ensure_search_profile(
                catalog,
                live_spec,
                space_id=space_id,
                generation_id=(
                    str(last["generation"]["generation_id"]) if last else generation_id
                ),
            )
        else:
            # Nothing of this scope entered the served generation (a scope without any
            # indexable image, or one whose images the encoder refused), so there is no
            # generation row for a profile to point at. That is a *reported* outcome: the
            # backlog below decides whether the scope has nothing to index or nothing that
            # could be encoded (P4-02Y-I review B1/F2).
            profile = {
                "profile_id": None,
                "created": False,
                "activated": False,
                "reason": "GENERATION_NOT_PUBLISHED",
            }
    except CatalogError as error:
        status.note_error(
            stage="semantic", code=str(error.code), message=str(error.message)[:200]
        )
        return {
            "state": STAGE_FAILED,
            "code": str(error.code),
            "targets": 0,
            "encoded": encoded,
            "batches": batches,
            **report,
            **_backlog_report(catalog, space_id, processor_id, scopes),
            **_content_report(catalog, space_id, content_delta, spec),
        }
    backlog = _backlog_report(catalog, space_id, processor_id, scopes)
    nothing_to_index = int(backlog["backlog_total"]) == 0
    return {
        # "There is no image this index is about" is a terminal, honest answer - not a failure
        # and not an "up to date" index either. It stays distinguishable from a normal OK run
        # so the card can say 已扫描、无可索引图片 instead of an empty progress bar.
        "state": STAGE_SKIPPED_NO_TARGETS if nothing_to_index else STAGE_OK,
        "reason": "NO_INDEXABLE_CONTENT" if nothing_to_index else None,
        "profile": profile["profile_id"],
        "profile_activated": bool(profile["activated"]),
        "profile_reason": profile.get("reason"),
        "targets": 0,
        "encoded": encoded,
        "batches": batches,
        "coverage_complete": int(backlog["backlog_pending"]) == 0,
        **report,
        **backlog,
        **_content_report(catalog, space_id, content_delta, spec),
        "delta_overflow": bool(delta_overflow),
    }


def _mark_scopes_built(catalog: Any, *, scopes: Sequence[int | None]) -> None:
    """Record durably that a build has classified these scopes (P4-02Y-I review F2).

    One additive ``metadata`` key per source (``source_build:<id>``), written only after the
    classification sweep really finished for that scope. A whole-library scope
    (``scopes == [None]``) covers every registered source, because its sweep classified all of
    them. No table, column or migration is added; ``metadata`` is this catalog's existing
    key/value store.
    """
    from photo_search.refresh import progress as progress_module

    if scopes == [None] or None in scopes:
        identifiers = [
            int(row["source_id"])
            for row in catalog.read_all("SELECT source_id FROM source_roots ORDER BY source_id")
        ]
    else:
        identifiers = [int(scope) for scope in scopes if scope is not None]
    for source_id in identifiers:
        progress_module.mark_source_built(
            catalog, source_id=int(source_id), value=str(catalog.now_ms())
        )


def _backlog_report(
    catalog: Any, space_id: str | None, processor_id: str | None, scopes: Sequence[int | None]
) -> dict[str, int]:
    """The durable semantic backlog of the requested scopes, as one *union*.

    An asset that two of the named sources share is counted once: the number describes the
    content this build is about, not the sum of the per-source cards.
    """
    from photo_search.refresh import progress as progress_module

    if space_id is None or processor_id is None:
        return {
            "backlog_total": 0,
            "backlog_confirmed": 0,
            "backlog_pending": 0,
            "backlog_failed": 0,
            "backlog_queued": 0,
        }
    concrete = [int(scope) for scope in scopes if scope is not None]
    if None in scopes:
        part = progress_module.semantic_breakdown(
            catalog, space_id=str(space_id), processor_id=str(processor_id)
        )
    elif len(concrete) == 1:
        part = progress_module.semantic_breakdown(
            catalog,
            space_id=str(space_id),
            processor_id=str(processor_id),
            source_id=concrete[0],
        )
    else:
        part = progress_module.semantic_breakdown(
            catalog,
            space_id=str(space_id),
            processor_id=str(processor_id),
            source_ids=concrete,
        )
    return {
        "backlog_total": int(part["total"]),
        "backlog_confirmed": int(part["confirmed"]),
        "backlog_pending": int(part["pending"]),
        "backlog_failed": int(part["failed"]),
        "backlog_queued": int(part["queued"]),
    }


def _served_generation(
    catalog: Any, profile_module: Any, derived_space: str, derived_generation: str
) -> tuple[str, str] | None:
    """Which (space, generation) the *active* profile serves, or ``None`` on a space clash.

    A library whose active profile names another space is served by an older model: the
    refresh must never rebuild a different space and silently change what the user sees, so
    that case is reported instead of extended (the model upgrade is P4-03B).
    """
    active = profile_module.active_profile_id(catalog.connection)
    if active is None:
        return (derived_space, derived_generation)
    profile = profile_module.load_profile(catalog, str(active))
    if profile is None:
        return (derived_space, derived_generation)
    space = str(getattr(profile, "space_id", "") or derived_space)
    generation = str(getattr(profile, "image_generation_id", "") or derived_generation)
    if space != derived_space:
        return None
    return (space, generation)


def _active_profile_space(catalog: Any, profile_module: Any) -> str | None:
    active = profile_module.active_profile_id(catalog.connection)
    if active is None:
        return None
    profile = profile_module.load_profile(catalog, str(active))
    return None if profile is None else str(getattr(profile, "space_id", "") or "")


def _content_report(
    catalog: Any, space_id: str | None, asset_ids: Iterable[int], spec: Any
) -> dict[str, Any]:
    """What of the changed content is required for search, and how much of it is confirmed.

    Only an *image the media policy supports* can ever enter the image-text index, so a
    document or a video that changed next to the photos is out of scope instead of a
    permanent gap (P4-02Q's rule, kept here for the delta). The remaining assets are split
    into three honest buckets:

    * ``unconfirmed_content`` - still waiting for its CONFIRMED current-revision embedding;
    * ``failed_assets`` - the encoder was already handed it and the job ended without a
      confirmation (FAILED/RETRY_WAIT/BLOCKED/CANCELLED), so a retry - not this run - decides
      whether it ever becomes searchable;
    * ``confirmed`` - present in the served generation for exactly its current revision.
    """
    ids = sorted({int(value) for value in asset_ids})
    eligible, out_of_scope = _eligible_content(catalog, ids)
    if not eligible:
        return {
            "required": 0,
            "confirmed": 0,
            "unconfirmed_content": 0,
            "failed_assets": 0,
            "out_of_scope": out_of_scope,
        }
    if space_id is None or spec is None:
        return {
            "required": len(eligible),
            "confirmed": 0,
            "unconfirmed_content": len(eligible),
            "failed_assets": 0,
            "out_of_scope": out_of_scope,
        }
    unconfirmed = failed = 0
    for start in range(0, len(eligible), 256):
        batch = eligible[start : start + 256]
        placeholders = ", ".join("?" for _ in batch)
        rows = catalog.read_all(
            "SELECT a.asset_id AS asset_id, "
            "NOT EXISTS (SELECT 1 FROM index_entries AS e "
            "JOIN artifacts AS ar ON ar.artifact_id = e.artifact_id "
            "WHERE e.asset_id = a.asset_id AND e.space_id = ? AND e.state = 'CONFIRMED' "
            "AND ar.input_revision = a.sha256) AS missing, "
            "EXISTS (SELECT 1 FROM jobs AS j WHERE j.subject_type = 'asset' "
            "AND j.subject_id = CAST(a.asset_id AS TEXT) AND j.processor_id = ? "
            "AND j.task_type = ? AND j.input_revision = a.sha256 "
            "AND j.state IN ('FAILED', 'RETRY_WAIT', 'BLOCKED', 'CANCELLED')) AS refused "
            f"FROM assets AS a WHERE a.asset_id IN ({placeholders})",
            (str(space_id), str(spec.processor_id), SEMANTIC_TASK_TYPE, *batch),
        )
        for row in rows:
            if not int(row["missing"]):
                continue
            if int(row["refused"]):
                failed += 1
            else:
                unconfirmed += 1
    return {
        "required": len(eligible),
        "confirmed": len(eligible) - unconfirmed - failed,
        "unconfirmed_content": unconfirmed,
        "failed_assets": failed,
        "out_of_scope": out_of_scope,
    }


def _eligible_content(
    catalog: Any, asset_ids: Sequence[int]
) -> tuple[list[int], dict[str, Any]]:
    """Split the changed assets into "an image this build can index" and everything else."""
    from photo_search.indexing import semantic

    eligible: list[int] = []
    labels: dict[str, int] = {}
    for asset_id in asset_ids:
        row = catalog.read_one(
            "SELECT l.relative_path, s.root_path FROM file_locations AS l "
            "JOIN source_roots AS s ON s.source_id = l.source_id "
            "WHERE l.asset_id = ? AND l.availability = 'PRESENT' "
            "ORDER BY l.location_id LIMIT 1",
            (int(asset_id),),
        )
        if row is None:
            labels["NO_PRESENT_LOCATION"] = labels.get("NO_PRESENT_LOCATION", 0) + 1
            continue
        path = Path(str(row["root_path"])) / str(row["relative_path"])
        is_image, label = semantic.image_eligibility(path)
        if is_image:
            eligible.append(int(asset_id))
        else:
            labels[str(label)] = labels.get(str(label), 0) + 1
    return eligible, {"count": sum(labels.values()), "labels": dict(sorted(labels.items()))}


def _search_ready(
    projection: dict[str, Any], semantic: dict[str, Any], *, mode: str = REFRESH_MODE_DAILY
) -> tuple[bool, str]:
    """The P4-02Y §6 search gate: projection caught up *and* the delta really searchable.

    A new photo is only "searchable" when this build can actually serve it, so a run without
    a usable semantic channel - or one that confirmed *nothing* of the changed images - must
    not claim ``SEARCH_READY``. Content the encoder already refused is reported as a partial
    failure instead: the rest stays searchable and the next run retries it.

    A **build** run is judged against the durable backlog of its scopes rather than against
    its in-memory delta: the delta of a first 20-30 万 build overflows its bound and is
    discarded, so only the durable "no image of these sources is still pending" fact can
    justify "可以语义搜索" (P4-02Y-I §3).
    """
    if str(projection["state"]) != "SYNCED":
        return (False, "projection-lagging")
    state = str(semantic["state"])
    if state not in (STAGE_OK, STAGE_SKIPPED_NO_TARGETS):
        return (False, str(semantic.get("reason") or "semantic-unavailable"))
    if mode == REFRESH_MODE_BUILD:
        if int(semantic.get("backlog_pending", 0)) > 0:
            return (False, "semantic-backlog")
        if int(semantic.get("backlog_total", 0)) == 0:
            # No image of this scope is (or ever was) part of the image-text index, so its
            # backlog is empty by construction and this round's new content is reachable by
            # definition. The card words it as 已扫描、无可索引图片 rather than "可搜索".
            return (True, "semantic-nothing-to-index")
        if int(semantic.get("backlog_confirmed", 0)) == 0:
            # Nothing of this scope is in the served generation (no image at all, or every
            # image was refused), so this run must not promise a searchable library.
            return (False, "semantic-confirmed-nothing")
        return (True, "semantic-backlog-empty")
    if int(semantic.get("unconfirmed_content", 0)) > 0:
        return (False, "semantic-pending")
    required = int(semantic.get("required", 0))
    if required > 0 and int(semantic.get("confirmed", 0)) == 0:
        return (False, "semantic-confirmed-nothing")
    if state == STAGE_SKIPPED_NO_TARGETS:
        # Nothing is outstanding in the served generation (and, when this round changed
        # something, the changed content is either confirmed or out of scope).
        return (True, "semantic-up-to-date")
    return (True, "semantic-confirmed")


# --------------------------------------------------------------------------- enrichment
def _enrichment_stages(
    service: Any,
    status: RefreshStatus,
    *,
    mode: str = REFRESH_MODE_DAILY,
    face_profile: str = "source_detail",
    scopes: Sequence[int | None] = (None,),
    content_delta: set[int],
    location_delta: set[int],
    delta_overflow: bool,
    encoder_factory: Callable[[], Any] | None,
    manifest_path: str | Path | None,
    device: str,
    device_policy: str,
    cancel_check: Callable[[], bool] | None,
) -> dict[str, Any]:
    """Run the available enrichment processors, none of them a hard gate.

    A daily pass feeds every stage the delta (only what changed). A **build** walks the
    *durable* scope in bounded pages instead, because the first build of a 20-30 万 source is
    exactly the case where the delta is capped: a page that had already been processed
    before is a no-op (published artifacts are reused, jobs are keyed), so the walk both
    catches up historical backlog and never repeats finished work (P4-02Y-I §2/§4).
    """
    catalog = service.catalog
    content = set(sorted(content_delta))
    location = set(sorted(location_delta))
    stages: dict[str, Any] = {}
    from photo_search.face.engine import FaceEngineCache
    face_cache = FaceEngineCache()
    runners: dict[str, Callable[[], dict[str, Any]]] = {
        "thumbnail": lambda: _thumbnail_stage(service, content, cancel_check),
        "taken_time": lambda: _taken_time_stage(catalog, content | location),
        "gps": lambda: _gps_stage(catalog, content),
        "place_resolver": lambda: _resolver_stage(catalog, service.data_root, content),
        "face": lambda: _face_stage(
            catalog, service.data_root, content, source_detail=face_profile == "source_detail",
            engine_cache=face_cache,
        ),
        "ocr": lambda: _ocr_stage(catalog, service.data_root, content),
    }
    if mode == REFRESH_MODE_BUILD:
        scoped: dict[str, Callable[[], dict[str, Any]]] = {
            "thumbnail": lambda: _drain_scope(
                catalog,
                scopes=scopes,
                runner=lambda page: _thumbnail_stage(
                    service, page, cancel_check, retry_cr2_failures=True
                ),
                cancel_check=cancel_check,
            ),
            "taken_time": lambda: _drain_scope(
                catalog,
                scopes=scopes,
                runner=lambda page: _taken_time_stage(catalog, page),
                cancel_check=cancel_check,
            ),
            "gps": lambda: _drain_scope(
                catalog,
                scopes=scopes,
                runner=lambda page: _gps_stage(catalog, page),
                cancel_check=cancel_check,
            ),
            "place_resolver": lambda: _drain_scope(
                catalog,
                scopes=scopes,
                runner=lambda page: _resolver_stage(catalog, service.data_root, page),
                cancel_check=cancel_check,
            ),
            "face": lambda: _drain_scope(
                catalog,
                scopes=scopes,
                runner=lambda page: _face_stage(
                    catalog, service.data_root, page,
                    source_detail=face_profile == "source_detail",
                    engine_cache=face_cache,
                ),
                cancel_check=cancel_check,
            ),
            "ocr": lambda: _drain_scope(
                catalog,
                scopes=scopes,
                runner=lambda page: _ocr_stage(catalog, service.data_root, page),
                cancel_check=cancel_check,
            ),
        }
        runners = scoped
    for name, runner in runners.items():
        payload = _stage(status, name, runner)
        stages[name] = payload
        status.set_stage(name, payload)
    return stages


def _scoped_asset_page(
    catalog: Any, *, source_id: int | None, after_asset_id: int, limit: int
) -> list[int]:
    """One bounded page of the assets a build still has to consider for a stage.

    The page is expressed over ``file_locations`` (a ``PRESENT``, content-bound position in
    the requested source) and keyset-paginated on ``asset_id``, so both the memory and the
    per-page transaction stay bounded however large the source is.
    """
    source_filter = "" if source_id is None else " AND l.source_id = ?"
    parameters: tuple[Any, ...] = (() if source_id is None else (int(source_id),)) + (
        int(after_asset_id),
        max(int(limit), 1),
    )
    rows = catalog.read_all(
        "SELECT DISTINCT l.asset_id AS asset_id FROM file_locations AS l "
        "WHERE l.availability = 'PRESENT' AND l.asset_id IS NOT NULL "
        f"{source_filter} AND l.asset_id > ? ORDER BY l.asset_id LIMIT ?",
        parameters,
    )
    return [int(row["asset_id"]) for row in rows]


def _drain_scope(
    catalog: Any,
    *,
    scopes: Sequence[int | None],
    runner: Callable[[set[int]], dict[str, Any]],
    cancel_check: Callable[[], bool] | None,
) -> dict[str, Any]:
    """Run one stage over every in-scope asset in bounded pages, and merge the answers."""
    payloads: list[dict[str, Any]] = []
    page_size = BUILD_PAGE_SIZE
    walked = 0
    for scope in scopes:
        cursor = 0
        while len(payloads) < BUILD_ROUND_CAP:
            page = _scoped_asset_page(
                catalog, source_id=scope, after_asset_id=cursor, limit=page_size
            )
            if not page:
                break
            payload = runner(set(page))
            payloads.append(payload)
            walked += len(page)
            cursor = page[-1]
            state = str(payload.get("state"))
            if state in (STAGE_FAILED, STAGE_SKIPPED):
                # An unavailable or broken processor will not become available by walking
                # the next page, so stop here and report what happened.
                break
            if len(page) < page_size:
                break
            if cancel_check is not None and cancel_check():
                break
    merged = _merge_stage_payloads(payloads)
    # ``assets`` is the number of assets this stage really walked, whichever keys the
    # processor itself reports.
    merged["assets"] = walked
    return merged


def _merge_stage_payloads(payloads: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """One stage answer for many bounded pages: sums, worst state, and a page count."""
    if not payloads:
        return {"state": STAGE_SKIPPED_NO_TARGETS, "pages": 0, "assets": 0}
    states = {str(payload.get("state")) for payload in payloads}
    if STAGE_FAILED in states:
        state = STAGE_FAILED
    elif STAGE_SKIPPED in states:
        state = STAGE_SKIPPED
    elif states == {STAGE_SKIPPED_NO_TARGETS}:
        state = STAGE_SKIPPED_NO_TARGETS
    else:
        state = STAGE_OK
    merged: dict[str, Any] = {"state": state, "pages": len(payloads), "assets": 0}
    for payload in payloads:
        for key, value in payload.items():
            if key == "state":
                continue
            if isinstance(value, bool):
                merged[key] = bool(merged.get(key, False)) or value
            elif isinstance(value, (int, float)):
                merged[key] = merged.get(key, 0) + value
            elif value is not None:
                merged[key] = value
    return merged


def _stage(
    status: RefreshStatus, name: str, runner: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    """Run one enrichment stage; an unavailable or failing processor is reported, never fatal."""
    started = time.perf_counter()
    try:
        payload = runner()
    except CatalogError as error:
        status.note_error(stage=name, code=str(error.code), message=str(error.message)[:200])
        return {
            "state": STAGE_SKIPPED
            if str(error.code) == RUNTIME_CAPABILITY_UNAVAILABLE
            else STAGE_FAILED,
            "code": str(error.code),
            "elapsed_ms": _elapsed_ms(started),
        }
    except Exception as error:  # noqa: BLE001 - one broken enricher must not fail the run
        status.note_error(stage=name, code=type(error).__name__, message=str(error)[:200])
        return {
            "state": STAGE_FAILED,
            "code": type(error).__name__,
            "elapsed_ms": _elapsed_ms(started),
        }
    payload["elapsed_ms"] = _elapsed_ms(started)
    return payload


def _thumbnail_stage(
    service: Any,
    assets: set[int],
    cancel_check: Callable[[], bool] | None,
    *,
    retry_cr2_failures: bool = False,
) -> dict[str, Any]:
    if not assets:
        return {"state": STAGE_SKIPPED_NO_TARGETS, "assets": 0, "generated": 0}
    targets = sorted(assets)[:ENRICH_LIMIT]
    summary = service.generate_media("thumbnail", asset_ids=targets, cancel_check=cancel_check)
    cr2_retried = 0
    cr2_previews = 0
    if retry_cr2_failures:
        retry_assets = _registered_cr2_format_failures(service, targets)
        thumbnail_assets = retry_assets["thumbnail"]
        preview_assets = retry_assets["preview"]
        if thumbnail_assets:
            retried = service.generate_media(
                "thumbnail",
                asset_ids=thumbnail_assets,
                retry_failed=True,
                cancel_check=cancel_check,
            )
            cr2_retried = int(retried["assets_considered"])
        if preview_assets:
            preview = service.generate_media(
                "preview",
                asset_ids=preview_assets,
                retry_failed=True,
                cancel_check=cancel_check,
            )
            cr2_previews = int(preview["generated"])
    return {
        "state": STAGE_OK,
        "assets": int(summary["assets_considered"]),
        "generated": int(summary["generated"]),
        "already_published": int(summary["already_published"]),
        "failed": int(summary["failed"]),
        "unsupported": int(summary["unsupported"]),
        "source_missing": int(summary["source_missing"]),
        "cr2_retry_assets": cr2_retried,
        "cr2_previews_generated": cr2_previews,
        "truncated": len(assets) > len(targets),
    }


def _registered_cr2_format_failures(
    service: Any, asset_ids: Sequence[int]
) -> dict[str, list[int]]:
    """Select only header-verified CR2s with a FORMAT_UNSUPPORTED cache record."""
    from photo_search.media.contract import spec_for
    from photo_search.media.raw_cr2 import is_cr2

    result = {"thumbnail": [], "preview": []}
    if not asset_ids:
        return result
    catalog = service.catalog
    placeholders = ",".join("?" for _ in asset_ids)
    rows = catalog.read_all(
        "SELECT DISTINCT l.asset_id, l.relative_path, s.root_path "
        "FROM file_locations AS l JOIN source_roots AS s ON s.source_id = l.source_id "
        f"WHERE l.availability = 'PRESENT' AND l.asset_id IN ({placeholders}) "
        "AND lower(l.relative_path) LIKE '%.cr2' ORDER BY l.asset_id, l.location_id",
        tuple(int(value) for value in asset_ids),
    )
    verified: dict[int, str] = {}
    for row in rows:
        asset_id = int(row["asset_id"])
        if asset_id not in verified and is_cr2(Path(str(row["root_path"])) / str(row["relative_path"])):
            asset = catalog.read_one("SELECT sha256 FROM assets WHERE asset_id = ?", (asset_id,))
            if asset is not None:
                verified[asset_id] = str(asset["sha256"])
    cache = service.media_cache()
    for kind in result:
        spec = spec_for(kind)
        for asset_id, sha256 in verified.items():
            failure = cache.read_failure(spec=spec, sha256=sha256)
            if failure is not None and failure.get("code") == FORMAT_UNSUPPORTED:
                result[kind].append(asset_id)
    return result


def _taken_time_stage(catalog: Any, assets: set[int]) -> dict[str, Any]:
    from photo_search.taken_time import service as taken_time_service

    if not assets:
        return {"state": STAGE_SKIPPED_NO_TARGETS, "targets": 0, "queued": 0, "new_jobs": 0}
    targets = taken_time_service.taken_time_targets(catalog, asset_ids=sorted(assets))
    before = taken_time_service.job_counts(catalog)
    enqueued = taken_time_service.enqueue_targets(catalog, targets, now_ms=catalog.now_ms())
    after = taken_time_service.job_counts(catalog)
    run = taken_time_service.run_queue(catalog, worker_id="library-refresh")
    return {
        "state": STAGE_OK,
        "targets": len(targets),
        "queued": int(enqueued["queued"]),
        "new_jobs": max(0, sum(after.values()) - sum(before.values())),
        "steps": int(run["steps"]),
        "pending_jobs": int(run["pending_jobs"]),
    }


def _gps_stage(catalog: Any, assets: set[int]) -> dict[str, Any]:
    from photo_search.geo import extract as geo_extract
    from photo_search.taken_time.service import PYTHON_PROVIDER, python_runtime_capability

    if not assets:
        return {"state": STAGE_SKIPPED_NO_TARGETS, "targets": 0, "new_jobs": 0, "steps": 0}
    spec = geo_extract.gps_processor_spec()
    now_ms = catalog.now_ms()
    scheduler = _python_scheduler(
        catalog, spec, now_ms=now_ms, capability=python_runtime_capability, provider=PYTHON_PROVIDER
    )
    targets = _assets_with_hash(catalog, sorted(assets)[:ENRICH_LIMIT])
    created = _enqueue_assets(catalog, targets, spec=spec, now_ms=catalog.now_ms())
    steps = _drain(
        scheduler,
        geo_extract.gps_executor(catalog, clock=catalog.now_ms),
        cap=len(targets) + 8,
    )
    return {
        "state": STAGE_OK,
        "targets": len(targets),
        "new_jobs": created,
        "steps": steps,
        "truncated": len(assets) > len(targets),
    }


def _resolver_stage(catalog: Any, data_root: Any, assets: set[int]) -> dict[str, Any]:
    from photo_search.geo import resolver as resolver_module
    from photo_search.taken_time.service import PYTHON_PROVIDER, python_runtime_capability

    if not assets:
        return {"state": STAGE_SKIPPED_NO_TARGETS, "targets": 0, "new_jobs": 0, "steps": 0}
    try:
        resolver_module.load_active_content(catalog.connection, data_root=data_root)
    except CatalogError as error:
        return {
            "state": STAGE_SKIPPED,
            "reason": str(error.code),
            "targets": len(assets),
            "new_jobs": 0,
            "steps": 0,
        }
    spec = resolver_module.resolver_processor_spec()
    now_ms = catalog.now_ms()
    scheduler = _python_scheduler(
        catalog, spec, now_ms=now_ms, capability=python_runtime_capability, provider=PYTHON_PROVIDER
    )
    targets = _assets_with_hash(catalog, sorted(assets)[:ENRICH_LIMIT])
    created = _enqueue_assets(catalog, targets, spec=spec, now_ms=catalog.now_ms())
    steps = _drain(
        scheduler,
        resolver_module.resolver_executor(catalog, data_root=data_root, clock=catalog.now_ms),
        cap=len(targets) + 8,
    )
    return {"state": STAGE_OK, "targets": len(targets), "new_jobs": created, "steps": steps}


def _face_stage(
    catalog: Any, data_root: Any, assets: set[int], *, source_detail: bool = True,
    engine_cache: Any | None = None,
) -> dict[str, Any]:
    from photo_search.face import service as face_service
    from photo_search.face import weights as face_weights

    if not assets:
        return {"state": STAGE_SKIPPED_NO_TARGETS, "targets": 0, "steps": 0}
    try:
        if engine_cache is not None:
            engine_cache.verified_weights(data_root)
        else:
            face_weights.resolve_and_verify(data_root)
    except CatalogError as error:
        return {
            "state": STAGE_SKIPPED,
            "reason": str(error.code),
            "targets": len(assets),
            "steps": 0,
        }
    result = face_service.detect(
        catalog, data_root=data_root, asset_ids=sorted(assets)[:ENRICH_LIMIT],
        source_detail=bool(source_detail),
        engine_cache=engine_cache,
    )
    return {
        "state": STAGE_OK,
        "targets": int(result["enqueue"]["targets"]),
        "queued": int(result["enqueue"]["queued"]),
        "steps": int(result["run"]["steps"]),
        "association": int(result["association"].get("reconciled", 0))
        if isinstance(result.get("association"), dict)
        else 0,
    }


def _ocr_stage(catalog: Any, data_root: Any, assets: set[int]) -> dict[str, Any]:
    from photo_search.ocr import service as ocr_service
    from photo_search.ocr import weights as ocr_weights

    if not assets:
        return {"state": STAGE_SKIPPED_NO_TARGETS, "targets": 0, "steps": 0}
    try:
        ocr_weights.resolve_and_verify(data_root)
    except CatalogError as error:
        return {
            "state": STAGE_SKIPPED,
            "reason": str(error.code),
            "targets": len(assets),
            "steps": 0,
        }
    targets = ocr_service.ocr_targets(catalog, asset_ids=sorted(assets)[:ENRICH_LIMIT])
    enqueued = ocr_service.enqueue_targets(catalog, targets, now_ms=catalog.now_ms())
    run = ocr_service.run_queue(catalog, data_root=data_root, worker_id="library-refresh")
    return {
        "state": STAGE_OK,
        "targets": len(targets),
        "queued": int(enqueued["queued"]),
        "steps": int(run["steps"]),
        "pending_jobs": int(run["pending_jobs"]),
        "cpu_fallbacks": list(run.get("cpu_fallbacks", [])),
    }


# ------------------------------------------------------------------------------- helpers
def _python_scheduler(
    catalog: Any, spec: Any, *, now_ms: int, capability: Callable[..., Any], provider: str
) -> Any:
    """A scheduler with the pure-Python runtime capability of the non-model processors."""
    from photo_search.scheduler import engine as scheduler_engine

    scheduler = scheduler_engine.Scheduler(catalog, [spec], lease_seconds=catalog.lease_seconds)
    snapshot = capability(now_ms=int(now_ms))
    scheduler.record_capability(snapshot, now_ms=int(now_ms))
    scheduler.record_qualification(
        processor_id=str(spec.processor_id),
        spec_revision=str(spec.spec_revision),
        device="cpu",
        precision=str(spec.supported_precisions[0]),
        provider_name=str(provider),
        runtime_package_id=str(spec.runtime_package_id),
        now_ms=int(now_ms),
        note="pure-Python EXIF reader, no model weights, CPU only",
    )
    return scheduler


def _drain(scheduler: Any, executor: Callable[[dict[str, Any]], dict[str, Any]], *, cap: int) -> int:
    steps = 0
    while steps < max(int(cap), 0):
        if not scheduler.run_once(
            executor, worker_id="library-refresh", now_ms=scheduler.catalog.now_ms()
        ):
            break
        steps += 1
    return steps


def _assets_with_hash(catalog: Any, asset_ids: Sequence[int]) -> list[dict[str, Any]]:
    if not asset_ids:
        return []
    placeholders = ", ".join("?" for _ in asset_ids)
    return [
        {"asset_id": int(row["asset_id"]), "input_revision": str(row["sha256"])}
        for row in catalog.read_all(
            f"SELECT asset_id, sha256 FROM assets WHERE asset_id IN ({placeholders}) "
            "ORDER BY asset_id",
            tuple(int(value) for value in asset_ids),
        )
    ]


def _enqueue_assets(catalog: Any, targets: Sequence[dict[str, Any]], *, spec: Any, now_ms: int) -> int:
    """Queue one job per target; the five-field logical key makes this idempotent."""
    created = 0
    with catalog.write() as connection:
        for target in targets:
            before = connection.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE subject_type = 'asset' AND subject_id = ? "
                "AND input_revision = ? AND task_type = ? AND processor_id = ?",
                (
                    str(int(target["asset_id"])),
                    str(target["input_revision"]),
                    str(spec.task_type),
                    str(spec.processor_id),
                ),
            ).fetchone()["n"]
            jobs.enqueue(
                connection,
                subject_type="asset",
                subject_id=str(int(target["asset_id"])),
                input_revision=str(target["input_revision"]),
                task_type=str(spec.task_type),
                processor_id=str(spec.processor_id),
                now_ms=int(now_ms),
            )
            if not int(before):
                created += 1
    return created


def _elapsed_ms(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))


__all__ = [
    "BUILD_PAGE_SIZE",
    "ENRICH_LIMIT",
    "PROJECTION_BATCH_SIZE",
    "REFRESH_MODE_BUILD",
    "REFRESH_MODE_DAILY",
    "REFRESH_MODES",
    "SEMANTIC_BATCH_LIMIT",
    "refresh_library",
]
