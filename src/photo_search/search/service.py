"""The P2-05 retrieval service: two parallel channels, RRF, authority, pending top-up.

This is the service layer of architecture §9.2 - **not** a search protocol. It takes
already structured conditions (text/filters/optional bound generation), never parses a
natural-language query, and returns a dictionary a later task can shape into
``SearchRequest``/``SearchResult`` (P2-06) without this task inventing that protocol.

Order of work for one request:

1. refuse an unusable text query before anything runs (P2-04's ``build_match``);
2. resolve the bound generation (space + dimension + state) and encode the query text
   exactly once;
3. catch the text projection up in bounded batches and report the lag that is left;
4. recall from the text channel and, when a generation is bound, from the vector channel,
   both restricted by the authoritative SQLite predicate;
5. fuse with RRF (pure function, recomputable by hand);
6. re-verify the hits against the *current* authority and report the revision change if
   one happened while the query ran;
7. name the authoritative candidates whose semantic projection is still pending.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from photo_search.catalog.errors import (
    CATALOG_FILTER_REVISION_STALE,
    INVALID_CURSOR,
    PLACE_SUGGESTION_REVISION_STALE,
    PROFILE_GENERATION_MISMATCH,
    RUNTIME_CAPABILITY_UNAVAILABLE,
    CatalogError,
)
from photo_search.config import resolve_data_root, vectors_dir
from photo_search.indexing.store import LanceVectorIndex
from photo_search.scheduler.contracts import CONTRACT_VERSION
from photo_search.search import documents as search_documents
from photo_search.search import retrieval as retrieval_module
from photo_search.search.contracts import SearchHit, SearchRequest, SearchResult, fail
from photo_search.search.embedding import QueryEncoder, encode_query
from photo_search.search.fusion import (
    DEFAULT_CHANNEL_WEIGHTS,
    FTS_CHANNEL,
    VECTOR_CHANNEL,
    FusionHit,
    reciprocal_rank_fusion,
)
from photo_search.search.profile import (
    DEFAULT_FUSION_CONFIG,
    SearchProfile,
    active_profile,
    check_profile_servable,
    load_profile,
)
from photo_search.search.retrieval import (
    AUTHORITY_PAGE_LIMIT,
    DEFAULT_TOP_K,
    MAX_TOP_K,
    SearchScope,
    VectorBinding,
    authoritative_page,
    fts_channel,
    pending_projection_page,
    resolve_binding,
    scope_view,
    text_column_matches,
    vector_channel,
)
from photo_search.search.sessions import (
    MAX_SESSION_IDS,
    SearchSession,
    SearchSessionStore,
    SessionEntry,
    query_hash,
)
from photo_search.search.tokens import VALIDATION_ERROR, MatchQuery, build_match

_HIT_REASON_BY_CHANNELS = {
    (FTS_CHANNEL,): "TEXT",
    (VECTOR_CHANNEL,): "SEMANTIC",
    (FTS_CHANNEL, VECTOR_CHANNEL): "TEXT+SEMANTIC",
}


def _require_full_vector_binding(**values: Any) -> bool:
    """The vector channel binds encoder, ``space_id``, ``generation_id`` and dimension."""
    given = {name: value is not None for name, value in values.items()}
    if any(given.values()) and not all(given.values()):
        raise CatalogError(
            VALIDATION_ERROR,
            "the vector channel binds its encoder, space_id, generation_id and dimension "
            "together",
            details={"missing": sorted(name for name, present in given.items() if not present)},
        )
    return all(given.values())


def _rewrite_hit(hit: FusionHit, *, columns: dict[int, list[str]]) -> dict[str, Any]:
    """One fused row with its reason, its text provenance and no similarity score."""
    payload = hit.to_dict()
    channels = tuple(payload["channels"])
    payload["hit_reason"] = _HIT_REASON_BY_CHANNELS.get(channels, "SEMANTIC")
    payload["matched_text"] = list(columns.get(int(hit.asset_id), []))
    # The vector channel only ranks a CONFIRMED entry, so being in it *is* the fact.
    payload["semantic_ready"] = VECTOR_CHANNEL in channels
    return payload


def default_vector_index(catalog: Any) -> LanceVectorIndex:
    """The derived vector store of this data root (same resolution as P2-03)."""
    return LanceVectorIndex(vectors_dir(resolve_data_root(catalog.db_path.parent.parent)))


def _verified_ids(catalog: Any, scope: SearchScope, asset_ids: Sequence[int]) -> set[int]:
    """Which of the given assets still satisfy the authoritative predicate right now."""
    ids = sorted({int(value) for value in asset_ids})
    if not ids:
        return set()
    where, parameters = retrieval_module.scope_condition(catalog.connection, scope)
    placeholders = ", ".join("?" for _ in ids)
    rows = catalog.read_all(
        f"SELECT a.asset_id FROM assets AS a WHERE {where} AND a.asset_id IN ({placeholders})",
        (*parameters, *ids),
    )
    return {int(row["asset_id"]) for row in rows}


def _projection_block(
    catalog: Any, *, match: MatchQuery | None, catch_up: dict[str, Any] | None
) -> dict[str, Any]:
    summary = search_documents.projection_summary(catalog.connection)
    processed = 0 if catch_up is None else int(catch_up["processed"])
    state = (
        str(catch_up["state"])
        if catch_up is not None
        else ("SYNCED" if int(summary["queued_assets"]) == 0 else "LAGGING")
    )
    block: dict[str, Any] = {
        **summary,
        "state": state,
        "processed_assets": processed,
    }
    if match is not None:
        block["terms"] = match.term_texts()
        block["match_expression"] = match.expression
    return block


def search_assets(
    catalog: Any,
    *,
    text: str | None = None,
    encoder: QueryEncoder | None = None,
    space_id: str | None = None,
    generation_id: str | None = None,
    dimension: int | None = None,
    top_k: int = DEFAULT_TOP_K,
    source_id: int | None = None,
    availability: str = "any",
    place_ids: Sequence[int] | None = None,
    include_descendants: bool = True,
    place_state: str = "any",
    vector_index: LanceVectorIndex | None = None,
) -> dict[str, Any]:
    """One fused retrieval over the authoritative candidate scope.

    ``text`` alone gives the text channel (the P2-04 surface, usable without any GPU).
    Supplying the encoder and the bound generation as well adds the image-text channel:
    then the query is encoded once and both channels are fused. Filters alone answer with
    the authoritative list, still without a GPU.
    """
    limit = int(top_k)
    if limit < 1 or limit > MAX_TOP_K:
        raise CatalogError(
            VALIDATION_ERROR,
            f"top_k must be between 1 and {MAX_TOP_K}",
            details={"top_k": limit},
        )
    scope = SearchScope.build(
        source_id=source_id,
        availability=availability,
        place_ids=place_ids,
        include_descendants=include_descendants,
        place_state=place_state,
    )
    match = build_match(text) if text is not None else None
    vector_enabled = _require_full_vector_binding(
        encoder=encoder,
        space_id=space_id,
        generation_id=generation_id,
        dimension=dimension,
    )
    if vector_enabled and match is None:
        raise CatalogError(
            VALIDATION_ERROR,
            "a semantic channel needs search text to encode",
        )

    filters = scope_view(catalog.connection, scope)
    revision_at_start = int(filters["catalog_filter_revision"])
    binding: VectorBinding | None = None
    query_vector: list[float] | None = None
    if vector_enabled:
        binding = resolve_binding(
            catalog,
            generation_id=str(generation_id),
            space_id=str(space_id),
            dimension=int(dimension),
        )
        query_vector = encode_query(
            encoder, str(text), space_id=str(space_id), dimension=int(dimension)
        )

    catch_up = search_documents.catch_up(catalog) if match is not None else None
    text_hits = fts_channel(catalog, match, scope, top_k=limit) if match is not None else []
    vector_block: dict[str, Any] | None = None
    if binding is not None and query_vector is not None:
        index = vector_index if vector_index is not None else default_vector_index(catalog)
        vector_block = vector_channel(
            catalog,
            index=index,
            binding=binding,
            query_vector=query_vector,
            scope=scope,
            top_k=limit,
        )
    vector_hits = [] if vector_block is None else list(vector_block["hits"])

    channels = {
        FTS_CHANNEL: [int(row["asset_id"]) for row in text_hits],
        VECTOR_CHANNEL: [int(row["asset_id"]) for row in vector_hits],
    }
    fused = reciprocal_rank_fusion(channels)
    if not fused and match is None:
        # A pure filter query: the authoritative list *is* the answer (architecture §9.2
        # "纯筛选查询走 SQLite 分页，无须生成文本向量").
        authority = authoritative_page(catalog, scope, limit=AUTHORITY_PAGE_LIMIT)
        hits = [
            {
                "asset_id": asset_id,
                "score": None,
                "matched_by": {"filter": rank},
                "channels": ["filter"],
                "hit_reason": "FILTER_ONLY",
                "matched_text": [],
                "semantic_ready": False,
            }
            for rank, asset_id in enumerate(authority["asset_ids"], start=1)
        ]
    else:
        authority = authoritative_page(catalog, scope, limit=AUTHORITY_PAGE_LIMIT)
        still_authoritative = _verified_ids(
            catalog, scope, [hit.asset_id for hit in fused]
        )
        columns = (
            text_column_matches(
                catalog,
                match,
                [hit.asset_id for hit in fused if FTS_CHANNEL in hit.matched_by],
            )
            if match is not None
            else {}
        )
        hits = [
            _rewrite_hit(hit, columns=columns)
            for hit in fused
            if hit.asset_id in still_authoritative
        ]

    topup = (
        pending_projection_page(catalog, binding=binding, scope=scope, match=match)
        if binding is not None
        else None
    )
    ranked = {int(row["asset_id"]) for row in hits}
    if topup is not None:
        for entry in topup["entries"]:
            entry["in_ranked_hits"] = int(entry["asset_id"]) in ranked

    filters_at_end = scope_view(catalog.connection, scope)
    revision_at_end = int(filters_at_end["catalog_filter_revision"])
    payload: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "query": (
            None
            if match is None
            else {
                "text": match.raw,
                "normalized_text": match.normalized,
                "terms": match.term_texts(),
                "match_expression": match.expression,
                "tokenizer_version": int(match.tokenizer_version),
                "fts_generation_id": str(match.fts_generation_id),
            }
        ),
        # ``filters`` echoes the conditions the query ran under; the top-level revision is
        # the one a caller must bind for a continuation (ADR-0005 §10).
        "filters": filters,
        "binding": None if binding is None else binding.to_dict(),
        "channels": {
            FTS_CHANNEL: [
                {"asset_id": int(row["asset_id"]), "rank": rank, "score": float(row["score"])}
                for rank, row in enumerate(text_hits, start=1)
            ],
            VECTOR_CHANNEL: [
                {
                    "asset_id": int(row["asset_id"]),
                    "rank": rank,
                    "distance": float(row["distance"]),
                }
                for rank, row in enumerate(vector_hits, start=1)
            ],
        },
        "weights": dict(DEFAULT_CHANNEL_WEIGHTS),
        "hits": hits,
        "authoritative": authority,
        "pending_semantic": topup,
        "search_projection": _projection_block(catalog, match=match, catch_up=catch_up),
        "vector_scan": None
        if vector_block is None
        else {
            "blocks": int(vector_block["blocks"]),
            "scanned_assets": int(vector_block["scanned_assets"]),
            "truncated": bool(vector_block["truncated"]),
        },
        "top_k": limit,
        "catalog_filter_revision": revision_at_end,
        "state": "OK" if revision_at_start == revision_at_end else "FILTER_REVISION_CHANGED",
    }
    return payload


# --------------------------------------------------------------- protocol (P2-06A)


def _refuse(code: str, message: str, **details: Any) -> CatalogError:
    return CatalogError(code, message, details={key: value for key, value in details.items()})


def _resolve_profile(catalog: Any, request: SearchRequest) -> SearchProfile | None:
    """The profile this request is pinned to: the named one, or the activated one."""
    if request.profile_id is not None:
        profile = load_profile(catalog, request.profile_id)
        if profile is None:
            raise _refuse(
                PROFILE_GENERATION_MISMATCH,
                "the requested search profile does not exist",
                reason="PROFILE_NOT_FOUND",
                profile_id=str(request.profile_id),
            )
        return profile
    return active_profile(catalog)


def _resolve_binding(
    catalog: Any, request: SearchRequest, profile: SearchProfile | None
) -> VectorBinding | None:
    """The binding the session is pinned to, or ``None`` for a pure filter query.

    With a profile the binding is the profile's own bundle, and any explicit triple in the
    request must agree with it exactly (``PROFILE_GENERATION_MISMATCH`` otherwise). Without
    a profile the caller must supply the complete explicit triple (the P2-05 path); a
    partial triple is the same caller-shape refusal P2-05 raises.
    """
    explicit = request.explicit_binding
    if profile is not None:
        binding = check_profile_servable(catalog, profile)
        if explicit is not None and explicit != (
            binding.space_id,
            binding.generation_id,
            binding.dimension,
        ):
            raise _refuse(
                PROFILE_GENERATION_MISMATCH,
                "the request's explicit binding does not match its search profile",
                reason="EXPLICIT_BINDING_MISMATCH",
                profile_id=profile.profile_id,
                requested_space_id=explicit[0],
                requested_generation_id=explicit[1],
                requested_dimension=int(explicit[2]),
                profile_space_id=binding.space_id,
                profile_generation_id=binding.generation_id,
                profile_dimension=int(binding.dimension),
            )
        return binding
    if explicit is None:
        return None
    return resolve_binding(
        catalog,
        generation_id=explicit[1],
        space_id=explicit[0],
        dimension=int(explicit[2]),
    )


def _require_binding_for_text(binding: VectorBinding | None, match: MatchQuery | None) -> None:
    """A text query is a semantic query, so it needs a binding and a query encoder.

    With no activated profile the caller must bring the explicit P2-05 binding; with no
    encoder in this process the request is refused instead of quietly returning a text-only
    page, so "the semantic channel never ran" can never be mistaken for "nothing was
    semantically similar".
    """
    if match is not None and binding is None:
        raise fail(
            "with no activated search profile the request must bind space_id, "
            "image_generation_id and dimension explicitly",
            field="profile_id",
        )


def _channel_summary(
    payload: dict[str, Any],
    *,
    match: MatchQuery | None,
    binding: VectorBinding | None,
    top_k: int,
) -> dict[str, Any]:
    """Per-channel candidate count and honest truncation, plus the authority bound."""
    text_count = len(payload["channels"][FTS_CHANNEL])
    vector_count = len(payload["channels"][VECTOR_CHANNEL])
    scan = payload["vector_scan"] or {}
    engaged = match is not None and binding is not None
    return {
        FTS_CHANNEL: {
            "engaged": match is not None,
            "candidates": text_count,
            "truncated": match is not None and text_count >= int(top_k),
        },
        VECTOR_CHANNEL: {
            "engaged": engaged,
            "candidates": vector_count,
            "truncated": engaged
            and (bool(scan.get("truncated")) or vector_count >= int(top_k)),
            "engine_blocks": int(scan.get("blocks", 0)),
            "reason": None if engaged else "NO_QUERY_TEXT",
        },
        "authoritative": {
            "total": int(payload["authoritative"]["total"]),
            "listed": int(payload["authoritative"]["count"]),
            "truncated": bool(payload["authoritative"]["truncated"]),
            "order": str(payload["authoritative"]["order"]),
        },
    }


def _entries_of(payload: dict[str, Any]) -> tuple[SessionEntry, ...]:
    """The ordered candidate list, as entries carrying their frozen rank provenance."""
    return tuple(
        SessionEntry(
            asset_id=int(row["asset_id"]),
            score=row["score"],
            matched_by={str(name): int(rank) for name, rank in row["matched_by"].items()},
            hit_reason=str(row["hit_reason"]),
            matched_text=tuple(str(value) for value in row.get("matched_text", ())),
            semantic_ready=bool(row.get("semantic_ready", False)),
        )
        for row in payload["hits"]
    )


def _filters_echo(payload: dict[str, Any], binding: VectorBinding | None) -> dict[str, Any]:
    """The applied conditions plus the binding, with no path, coordinate or token."""
    echoed = dict(payload["filters"])
    echoed["binding"] = None if binding is None else binding.to_dict()
    return echoed


def _render_page(
    catalog: Any,
    scope: SearchScope,
    session: SearchSession,
    *,
    sessions: SearchSessionStore,
    page_size: int,
    offset: int,
) -> tuple[tuple[SearchHit, ...], int, bool, str | None]:
    """One page of the frozen order, with every row re-checked against current authority.

    A row the current authoritative predicate no longer allows simply does not appear; the
    offset still advances by ``page_size``, so the cached order is never re-cut and no later
    row is silently skipped or repeated.
    """
    start = max(0, int(offset))
    window = session.entries[start : start + int(page_size)]
    verified = _verified_ids(catalog, scope, [entry.asset_id for entry in window])
    hits = tuple(
        SearchHit(
            asset_id=entry.asset_id,
            rank=start + position + 1,
            score=entry.score,
            matched_by=dict(entry.matched_by),
            hit_reason=entry.hit_reason,
            matched_text=entry.matched_text,
            semantic_ready=entry.semantic_ready,
        )
        for position, entry in enumerate(window)
        if entry.asset_id in verified
    )
    next_offset = start + int(page_size)
    has_more = next_offset < len(session.entries)
    token = sessions.cursor_for_offset(session, next_offset) if has_more else None
    return hits, next_offset, has_more, token


def _continue_session(
    catalog: Any,
    request: SearchRequest,
    *,
    sessions: SearchSessionStore,
    scope: SearchScope,
    profile: SearchProfile | None,
    binding: VectorBinding | None,
    digest: str,
) -> dict[str, Any]:
    """Serve the next page of one live session, or the identifiable expiry/staleness code."""
    session_id, offset = sessions.decode_cursor(request.cursor)
    if request.search_session_id != session_id:
        raise _refuse(
            INVALID_CURSOR,
            "the cursor does not belong to the supplied search session",
            field="cursor",
        )
    session = sessions.require(session_id)
    if offset < 0 or offset >= len(session.entries):
        raise _refuse(
            INVALID_CURSOR,
            "the cursor points outside the cached candidate list",
            field="cursor",
            offset=int(offset),
            candidates=len(session.entries),
        )
    if session.query_hash != digest:
        raise _refuse(
            INVALID_CURSOR,
            "the cursor was minted for a different query or filter combination",
            field="cursor",
        )
    current = (
        None if profile is None else profile.profile_id,
        None if binding is None else binding.generation_id,
        None if binding is None else binding.space_id,
        None if binding is None else int(binding.dimension),
    )
    if session.binding != current:
        raise _refuse(
            PROFILE_GENERATION_MISMATCH,
            "this page belongs to a different search profile or index generation",
            reason="SESSION_BINDING_CHANGED",
            search_session_id=session.session_id,
            session_profile_id=session.profile_id,
            session_generation_id=session.image_generation_id,
            current_profile_id=current[0],
            current_generation_id=current[1],
        )
    revision_now = int(scope_view(catalog.connection, scope)["catalog_filter_revision"])
    if int(session.catalog_filter_revision) != revision_now:
        raise _refuse(
            CATALOG_FILTER_REVISION_STALE,
            "the effective place of at least one asset changed after this search started",
            reason="CATALOG_FILTER_REVISION_CHANGED",
            search_session_id=session.session_id,
            session_catalog_filter_revision=int(session.catalog_filter_revision),
            current_catalog_filter_revision=revision_now,
        )
    if request.place_state == "suggested":
        suggestion_now = int(scope_view(catalog.connection, scope)["suggestion_revision"])
        if int(session.suggestion_revision) != suggestion_now:
            raise _refuse(
                PLACE_SUGGESTION_REVISION_STALE,
                "the visible place suggestions changed after this search started",
                reason="SUGGESTION_REVISION_CHANGED",
                search_session_id=session.session_id,
                session_suggestion_revision=int(session.suggestion_revision),
                current_suggestion_revision=suggestion_now,
            )
    hits, next_offset, has_more, token = _render_page(
        catalog,
        scope,
        session,
        sessions=sessions,
        page_size=int(request.page_size),
        offset=int(offset),
    )
    return _result_envelope(
        request,
        session,
        hits=hits,
        offset=int(offset),
        next_offset=next_offset,
        has_more=has_more,
        token=token,
    )


def _result_envelope(
    request: SearchRequest,
    session: SearchSession,
    *,
    hits: tuple[SearchHit, ...],
    offset: int,
    next_offset: int,
    has_more: bool,
    token: str | None,
) -> dict[str, Any]:
    """One page as the frozen ``SearchResult`` envelope (the session carries the rest)."""
    return SearchResult(
        request_id=request.request_id,
        search_session_id=session.session_id,
        profile_id=session.profile_id,
        image_generation_id=session.image_generation_id,
        space_id=session.space_id,
        dimension=session.dimension,
        catalog_filter_revision=int(session.catalog_filter_revision),
        suggestion_revision=int(session.suggestion_revision),
        fusion_config=dict(session.fusion_config),
        query=None if session.query is None else dict(session.query),
        filters=dict(session.filters),
        hits=hits,
        page_size=int(request.page_size),
        page_offset=int(offset),
        next_cursor=token,
        has_more=bool(has_more),
        total_candidates=len(session.entries),
        candidates_truncated=bool(session.candidates_truncated),
        channels=dict(session.channels),
        pending_semantic=None
        if session.pending_semantic is None
        else dict(session.pending_semantic),
        search_projection=dict(session.search_projection),
        state=str(session.state),
    ).to_envelope()


def search_request(
    catalog: Any,
    request: SearchRequest,
    *,
    sessions: SearchSessionStore,
    encoder: QueryEncoder | None = None,
    vector_index: LanceVectorIndex | None = None,
) -> dict[str, Any]:
    """One protocol-level search: resolve, bind, run once, cache and answer one page.

    The first page runs the P2-05 retrieval exactly once and freezes its ordered candidates
    plus their provenance in the session; every later page is a slice of that same ranking,
    re-checked against the current SQLite authority.
    """
    scope = SearchScope.build(
        source_id=request.source_id,
        availability=request.availability,
        place_ids=request.place_ids,
        include_descendants=request.include_descendants,
        place_state=request.place_state,
    )
    match = build_match(request.text) if request.text is not None else None
    profile = _resolve_profile(catalog, request)
    binding = _resolve_binding(catalog, request, profile)
    digest = query_hash(
        text=request.text,
        source_id=request.source_id,
        availability=request.availability,
        place_ids=request.place_ids,
        include_descendants=request.include_descendants,
        place_state=request.place_state,
        top_k=int(request.top_k),
        page_size=int(request.page_size),
    )
    if request.cursor is not None:
        return _continue_session(
            catalog,
            request,
            sessions=sessions,
            scope=scope,
            profile=profile,
            binding=binding,
            digest=digest,
        )
    if request.search_session_id is not None:
        raise _refuse(
            INVALID_CURSOR,
            "a search_session_id alone is not a continuation; send its cursor too",
            field="cursor",
        )
    _require_binding_for_text(binding, match)
    if match is not None and binding is not None and encoder is None:
        raise _refuse(
            RUNTIME_CAPABILITY_UNAVAILABLE,
            "this process has no query encoder for the bound embedding space",
            reason="QUERY_ENCODER_UNAVAILABLE",
            generation_id=binding.generation_id,
            space_id=binding.space_id,
        )

    payload = search_assets(
        catalog,
        text=request.text,
        encoder=encoder if match is not None and binding is not None else None,
        # The vector channel engages only when there is both a text query and a resolved
        # binding: a filter-only request is served from the authoritative list alone, even
        # when the session is pinned to a profile (P2-05's "纯筛选查询" path).
        space_id=None if binding is None or match is None else binding.space_id,
        generation_id=None if binding is None or match is None else binding.generation_id,
        dimension=None if binding is None or match is None else int(binding.dimension),
        top_k=int(request.top_k),
        source_id=request.source_id,
        availability=request.availability,
        place_ids=request.place_ids,
        include_descendants=request.include_descendants,
        place_state=request.place_state,
        vector_index=vector_index,
    )
    entries = _entries_of(payload)
    session = sessions.create(
        query_hash=digest,
        entries=entries,
        profile_id=None if profile is None else profile.profile_id,
        image_generation_id=None if binding is None else binding.generation_id,
        space_id=None if binding is None else binding.space_id,
        dimension=None if binding is None else int(binding.dimension),
        catalog_filter_revision=int(payload["catalog_filter_revision"]),
        suggestion_revision=int(payload["filters"]["suggestion_revision"]),
        fusion_config=(
            dict(DEFAULT_FUSION_CONFIG) if profile is None else dict(profile.fusion_config)
        ),
        query=payload["query"],
        filters=_filters_echo(payload, binding),
        channels=_channel_summary(
            payload, match=match, binding=binding, top_k=int(request.top_k)
        ),
        pending_semantic=payload["pending_semantic"],
        search_projection=payload["search_projection"],
        state=str(payload["state"]),
        candidates_truncated=len(entries) >= MAX_SESSION_IDS,
    )
    hits, next_offset, has_more, token = _render_page(
        catalog,
        scope,
        session,
        sessions=sessions,
        page_size=int(request.page_size),
        offset=0,
    )
    return _result_envelope(
        request,
        session,
        hits=hits,
        offset=0,
        next_offset=next_offset,
        has_more=has_more,
        token=token,
    )


__all__ = [
    "MAX_TOP_K",
    "default_vector_index",
    "search_assets",
    "search_request",
]
