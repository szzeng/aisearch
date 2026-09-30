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

import base64
import binascii
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from photo_search.catalog.errors import (
    CATALOG_FILTER_REVISION_STALE,
    FEEDBACK_IDEMPOTENCY_CONFLICT,
    INVALID_CURSOR,
    PLACE_SUGGESTION_REVISION_STALE,
    PROFILE_GENERATION_MISMATCH,
    RUNTIME_CAPABILITY_UNAVAILABLE,
    CatalogError,
)
from photo_search.config import resolve_data_root, vectors_dir
from photo_search.indexing.store import LanceVectorIndex
from photo_search.persons.identity import resolve_canonical_person_id
from photo_search.places import revision as place_revision
from photo_search.scheduler.contracts import CONTRACT_VERSION
from photo_search.search import documents as search_documents
from photo_search.search import retrieval as retrieval_module
from photo_search.search.contracts import (
    DEFAULT_FEEDBACK_PAGE_SIZE,
    MAX_FEEDBACK_PAGE_SIZE,
    FeedbackPage,
    FeedbackRecord,
    FeedbackRequest,
    FeedbackResponse,
    SearchHit,
    SearchRequest,
    SearchResult,
    fail,
)
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
    reranker_readiness,
)
from photo_search.search.rerank import (
    DEFAULT_RERANK_CANDIDATES,
    DEFAULT_RERANK_TIMEOUT_MS,
    REASON_NO_PROFILE_RERANKER,
    REASON_NO_QUERY_TEXT,
    REASON_QUALIFICATION_MISSING,
    RERANK_STATE_NOT_REQUESTED,
    RerankCandidate,
    not_requested_outcome,
    rerank_hits,
    unavailable_outcome,
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
from photo_search.search.upload import checked_query_vector
from photo_search.taken_time import contract as time_contract
from photo_search.taken_time import read as time_read

#: Normalised-vector squared-L2 threshold; equivalent to cosine similarity >= 0.35.
STRONG_MATCH_MAX_DISTANCE = 1.3

_HIT_REASON_BY_CHANNELS = {
    (FTS_CHANNEL,): "TEXT",
    (VECTOR_CHANNEL,): "SEMANTIC",
    (FTS_CHANNEL, VECTOR_CHANNEL): "TEXT+SEMANTIC",
}

#: How many times one *new* search may re-run when the authoritative filter revision moved
#: under it - that is, when a person/place edit committed between the canonical resolution
#: and the retrieval. ADR-0005 §10 forbids answering with a page that mixes two authority
#: versions, so a moved revision discards the attempt instead of publishing it; three
#: bounded attempts keep the (rare) race cheap and still end in a structured
#: ``CATALOG_FILTER_REVISION_STALE`` refusal rather than in a mixed answer.
SEARCH_PUBLISH_ATTEMPTS = 3


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
    person_ids: Sequence[int] | None = None,
    person_match: str = "any",
    capture: time_contract.CaptureRange | None = None,
    vector_index: LanceVectorIndex | None = None,
    query_vector: Sequence[float] | None = None,
) -> dict[str, Any]:
    """One fused retrieval over the authoritative candidate scope.

    ``text`` alone gives the text channel (the P2-04 surface, usable without any GPU).
    Supplying the encoder and the bound generation as well adds the image-text channel:
    then the query is encoded once and both channels are fused. Filters alone answer with
    the authoritative list, still without a GPU.

    P2-07 adds exactly one alternative to "encode this text": a caller that already holds a
    validated query vector (the uploaded-image path) may pass it as ``query_vector``. The
    ranking, the RRF formula and the authoritative re-check are the same code either way —
    an image query is not a second kind of search.
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
        # ADR-0009 §3: the person condition is one more authoritative condition of the same
        # scope, so every channel, the authoritative page and the pre-page re-check apply it.
        person_ids=person_ids,
        person_match=person_match,
        # ADR-0011 §6.5: the capture range joins the very same AND set, and only
        # ``label = capture`` rows may satisfy it.
        capture=capture,
    )
    match = build_match(text) if text is not None else None
    # The image path brings a vector instead of a text to encode: exactly one of the two
    # satisfies the "encoder" slot of the binding gate below.
    vector_enabled = _require_full_vector_binding(
        encoder=encoder if encoder is not None else query_vector,
        space_id=space_id,
        generation_id=generation_id,
        dimension=dimension,
    )
    if vector_enabled and match is None and query_vector is None:
        raise CatalogError(
            VALIDATION_ERROR,
            "a semantic channel needs search text to encode",
        )

    filters = scope_view(catalog.connection, scope)
    revision_at_start = int(filters["catalog_filter_revision"])
    binding: VectorBinding | None = None
    #: The vector this retrieval actually ranks with: the caller's (already validated) image
    #: vector, or the one encoded here from the query text.
    vector: list[float] | None = None
    if vector_enabled:
        binding = resolve_binding(
            catalog,
            generation_id=str(generation_id),
            space_id=str(space_id),
            dimension=int(dimension),
        )
        vector = (
            encode_query(encoder, str(text), space_id=str(space_id), dimension=int(dimension))
            if query_vector is None
            # An uploaded-image vector is re-checked here as well: the service layer never
            # trusts a vector it did not see validated, whatever produced it.
            else checked_query_vector(query_vector, dimension=int(dimension))
        )

    catch_up = search_documents.catch_up(catalog) if match is not None else None
    text_hits = fts_channel(catalog, match, scope, top_k=limit) if match is not None else []
    vector_block: dict[str, Any] | None = None
    if binding is not None and vector is not None:
        index = vector_index if vector_index is not None else default_vector_index(catalog)
        vector_block = vector_channel(
            catalog,
            index=index,
            binding=binding,
            query_vector=vector,
            scope=scope,
            top_k=limit,
        )
    vector_hits = [] if vector_block is None else list(vector_block["hits"])

    channels = {
        FTS_CHANNEL: [int(row["asset_id"]) for row in text_hits],
        VECTOR_CHANNEL: [int(row["asset_id"]) for row in vector_hits],
    }
    fused = reciprocal_rank_fusion(channels)
    if not fused and match is None and vector is None:
        # A pure filter query: the authoritative list *is* the answer (architecture §9.2
        # "纯筛选查询走 SQLite 分页，无须生成文本向量"). An image query never takes this
        # branch: when its vector channel finds nothing, the honest answer is "no candidate
        # is semantically close", not an arbitrary list of authoritative assets.
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


def resolve_request_binding(catalog: Any, request: SearchRequest) -> VectorBinding | None:
    """The binding one request will be served under, resolved exactly as the search does.

    P2-07's upload path has to know the bound space *before* it can encode an image, and it
    has to reach the same decision the search itself would: the same profile resolution, the
    same explicit-triple agreement check and therefore the same refusal codes. This exposes
    that one decision (``None`` = a pure-filter request with no semantic channel at all).
    """
    return _resolve_binding(catalog, request, _resolve_profile(catalog, request))


def _channel_summary(
    payload: dict[str, Any],
    *,
    match: MatchQuery | None,
    binding: VectorBinding | None,
    top_k: int,
    image_query: bool = False,
) -> dict[str, Any]:
    """Per-channel candidate count and honest truncation, plus the authority bound.

    ``image_query`` names the P2-07 case where the vector channel is engaged by an uploaded
    image rather than by a text query: it ran, so it is reported as engaged, but the text
    channel did not and says so.
    """
    text_count = len(payload["channels"][FTS_CHANNEL])
    vector_count = len(payload["channels"][VECTOR_CHANNEL])
    vector_rows = payload["channels"][VECTOR_CHANNEL]
    scan = payload["vector_scan"] or {}
    engaged = binding is not None and (match is not None or bool(image_query))
    scan_truncated = bool(scan.get("truncated"))
    strong_match_count = sum(
        float(row["distance"]) <= STRONG_MATCH_MAX_DISTANCE for row in vector_rows
    )
    strong_match_more_possible = bool(
        vector_count == MAX_TOP_K
        and vector_rows
        and float(vector_rows[-1]["distance"]) <= STRONG_MATCH_MAX_DISTANCE
    )
    return {
        FTS_CHANNEL: {
            "engaged": match is not None,
            "candidates": text_count,
            "truncated": match is not None and text_count >= int(top_k),
        },
        VECTOR_CHANNEL: {
            "engaged": engaged,
            "candidates": vector_count,
            "truncated": engaged and scan_truncated,
            "strong_match_count": strong_match_count,
            "strong_match_more_possible": strong_match_more_possible,
            "scan_truncated": scan_truncated,
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
    """The applied conditions plus the binding, with no path, coordinate or token.

    ADR-0011 §8.1/§8.2: the capture keys arrive from ``scope_view`` (they are part of the
    scope's own echo), so the live ``filters`` object is the frozen 11-key ADR-0009 shape
    plus exactly ``capture_from``/``capture_to_exclusive`` and the service-added ``binding``.
    """
    echoed = dict(payload["filters"])
    echoed["binding"] = None if binding is None else binding.to_dict()
    return echoed


def _taken_time_block(catalog: Any, filters: Mapping[str, Any]) -> dict[str, Any] | None:
    """ADR-0011 §8.1: the honesty block for a capture-conditioned query, or ``None``.

    It stays out of the frozen ``search_projection`` block and out of a query that carries no
    time condition, and it is read live (the counts describe the catalog right now) rather
    than frozen in the session: that is what lets an empty page say "the extraction has not
    run yet" instead of pretending there is no match.
    """
    if filters.get(time_contract.FIELD_FROM) is None and (
        filters.get(time_contract.FIELD_TO) is None
    ):
        return None
    return time_read.state_block(catalog.connection)


@dataclass(frozen=True)
class _AuthorityBase:
    """The authority state one new search is anchored to (ADR-0003 §9, ADR-0009 §4.2)."""

    canonical_person_ids: tuple[int, ...]
    filter_revision: int
    suggestion_revision: int


def _person_authority_base(catalog: Any, request: SearchRequest) -> _AuthorityBase:
    """ADR-0009 §4.1～§4.3: the canonical id set AND its revisions, from ONE read snapshot.

    The result is the canonical, de-duplicated, ascending id set that the predicate, the
    ``filters`` echo and the continuation digest all use. Resolution reuses the one redirect
    walker (``persons/identity.resolve_canonical_person_id``); the retrieval layer never
    follows ``merged_into`` itself. Any id that does not resolve refuses the whole request
    with its own code, so no id is dropped and no partial answer is ever produced.

    Both halves come out of ``Catalog.read_snapshot()``: the connection is in autocommit
    mode, so resolving several ids with bare statements would let a merge committed by
    another connection land *between* two of them and yield "the pre-merge resolution of id
    1 plus the post-merge resolution of id 2" - a set no single state ever contained. The
    same snapshot also yields the ``catalog_filter_revision`` (and the independent
    ``place_suggestion_revision``) this answer is published against, so the caller can
    reject any attempt that straddled an edit.
    """
    with catalog.read_snapshot() as connection:
        base = _AuthorityBase(
            canonical_person_ids=(),
            filter_revision=int(place_revision.read_filter_revision(connection)),
            suggestion_revision=int(place_revision.read_suggestion_revision(connection)),
        )
        if not request.person_ids:
            return base
        canonical = tuple(
            sorted(
                {
                    int(resolve_canonical_person_id(connection, int(person_id)))
                    for person_id in request.person_ids
                }
            )
        )
    return _AuthorityBase(
        canonical_person_ids=canonical,
        filter_revision=base.filter_revision,
        suggestion_revision=base.suggestion_revision,
    )


def _authority_drift(catalog: Any, request: SearchRequest, base: _AuthorityBase) -> str | None:
    """Which frozen counter moved under the page that was just built, or ``None``.

    ADR-0005 §10 forbids publishing a page whose parts were read from two different catalog
    versions. Every authority fact this boundary covers moves a frozen counter in the same
    transaction that changes it (ADR-0005 §11, ADR-0006/0007/0008 §8), so re-reading the
    counters is the proof: no drift means nothing changed under the page.

    The filter revision moves for place assignments and for every person edit; the
    independent suggestion revision moves when the visible *suggested* candidates change, so
    it is inspected exactly for the one request shape whose predicate depends on it
    (``place_state=suggested``), the same way ``_continue_session`` binds it. The filter
    revision is inspected first, which is also the priority the refusal keeps.
    """
    connection = catalog.connection
    if int(place_revision.read_filter_revision(connection)) != base.filter_revision:
        return "filter"
    if str(request.place_state) == "suggested" and (
        int(place_revision.read_suggestion_revision(connection)) != base.suggestion_revision
    ):
        return "suggestion"
    return None


def _publication_refusal(catalog: Any, request: SearchRequest, base: _AuthorityBase) -> CatalogError:
    """The structured refusal for a new search whose page could never be published.

    ADR-0005 §9/§10 keep the two frozen counters distinct, and so does the error surface: a
    moved ``catalog_filter_revision`` (a place assignment, or any person edit) is
    ``CATALOG_FILTER_REVISION_STALE``; a ``place_state=suggested`` search whose filter
    revision never moved is ``PLACE_SUGGESTION_REVISION_STALE``. Both codes, reasons and
    detail keys are the ones the continuation path already answers with, and the filter
    revision wins when both moved - the same order ``_continue_session`` compares them in.
    No session exists yet, so neither refusal names one.
    """
    connection = catalog.connection
    filter_now = int(place_revision.read_filter_revision(connection))
    if filter_now != base.filter_revision or str(request.place_state) != "suggested":
        return _refuse(
            CATALOG_FILTER_REVISION_STALE,
            "the authoritative filter revision changed while this search ran",
            reason="CATALOG_FILTER_REVISION_CHANGED",
            session_catalog_filter_revision=base.filter_revision,
            current_catalog_filter_revision=filter_now,
        )
    return _refuse(
        PLACE_SUGGESTION_REVISION_STALE,
        "the visible place suggestions changed while this search ran",
        reason="SUGGESTION_REVISION_CHANGED",
        session_suggestion_revision=base.suggestion_revision,
        current_suggestion_revision=int(place_revision.read_suggestion_revision(connection)),
    )


def _rerank_outcome(
    catalog: Any,
    request: SearchRequest,
    *,
    profile: SearchProfile | None,
    match: MatchQuery | None,
    entries: Sequence[SessionEntry],
    reranker: Any,
    timeout_ms: int,
) -> Any:
    """The optional rerank block of one *new* page, or the honest reason there is none.

    The candidates are the frozen order of the session that is about to be created, so a
    reranked row always names a real candidate of this search. Every refusal path returns
    zero rows; the base hits are computed before this runs and are never touched here.
    """
    if not request.rerank:
        return not_requested_outcome()
    candidates = tuple(
        RerankCandidate(asset_id=int(entry.asset_id), rank=index + 1)
        for index, entry in enumerate(entries)
    )
    context: dict[str, Any] = {
        "candidates": len(candidates),
        "limit": DEFAULT_RERANK_CANDIDATES,
        "timeout_ms": int(timeout_ms),
    }
    if profile is None or profile.reranker_processor_id is None:
        # No activated profile declares a reranker (or no profile is active at all): there
        # is nothing to run, and the answer says so instead of quietly returning the base
        # order as if a model had confirmed it.
        return unavailable_outcome(REASON_NO_PROFILE_RERANKER, **context)
    processor_id = str(profile.reranker_processor_id)
    context["processor_id"] = processor_id
    if match is None:
        # A pure filter query has no query text for a reranker to score against.
        return unavailable_outcome(REASON_NO_QUERY_TEXT, **context)
    try:
        reranker_readiness(catalog.connection, profile)
    except CatalogError as refusal:
        # The same evidence rule the activation gate applies (ADR-0003 §5): a reranker that
        # this process cannot prove healthy and qualified for does not score anything.
        return unavailable_outcome(
            str(refusal.details.get("reason") or REASON_QUALIFICATION_MISSING), **context
        )
    return rerank_hits(
        reranker,
        query=match.raw,
        candidates=candidates,
        expected_processor_id=processor_id,
        timeout_ms=int(timeout_ms),
    )


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
    # The two revision checks come *before* the digest comparison on purpose. ADR-0005 §10
    # binds a session to the CatalogFilterRevision it was created under and answers
    # ``CATALOG_FILTER_REVISION_STALE`` when it moved; ADR-0009 §4.4/§6.2 keeps that answer
    # for every person edit, including a merge that also changes the *canonical* id a raw
    # condition resolves to (the digest alone would report that as a malformed cursor). A
    # genuinely different condition with an unchanged revision is still refused below.
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
        # ADR-0011 §8.1: a continuation of a capture-conditioned search carries the same
        # honesty block, recomputed live like the frozen session's own echo.
        taken_time=_taken_time_block(catalog, session.filters),
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
    taken_time: dict[str, Any] | None = None,
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
        rerank_state=str(session.rerank_state),
        reranked=None if session.reranked is None else dict(session.reranked),
        taken_time=None if taken_time is None else dict(taken_time),
        state=str(session.state),
    ).to_envelope()


def search_request(
    catalog: Any,
    request: SearchRequest,
    *,
    sessions: SearchSessionStore,
    encoder: QueryEncoder | None = None,
    vector_index: LanceVectorIndex | None = None,
    reranker: Any = None,
    rerank_timeout_ms: int = DEFAULT_RERANK_TIMEOUT_MS,
    query_vector: Sequence[float] | None = None,
    query_identity: str | None = None,
) -> dict[str, Any]:
    """One protocol-level search: resolve, bind, run once, cache and answer one page.

    The first page runs the P2-05 retrieval exactly once and freezes its ordered candidates
    plus their provenance in the session; every later page is a slice of that same ranking,
    re-checked against the current SQLite authority. The optional P2-06B rerank runs on that
    frozen order *after* the base page exists, and its scores are reported in their own
    block - they are never folded into ``hits[].score``.

    P2-07 reuses this whole path for an uploaded image: ``query_vector`` replaces the text
    that would have been encoded (there is none), and ``query_identity`` replaces the text in
    the session digest, so a cursor minted for one image can never be replayed against
    another while the binding, revision and page semantics stay exactly the same.
    """
    # ADR-0009 §4.1～§4.3: the canonical ids and the ``catalog_filter_revision`` they are
    # anchored to are read in ONE snapshot, before the scope exists, so the predicate, the
    # echo and the digest cannot disagree about which people the request named - and cannot
    # mix two authority states either. The revision is the pre-binding of this new search.
    base = _person_authority_base(catalog, request)
    # ADR-0011 §6.5: the capture condition is part of the same AND set and the same digest.
    capture = request.capture_range

    def build_scope(people: tuple[int, ...]) -> SearchScope:
        return SearchScope.build(
            source_id=request.source_id,
            availability=request.availability,
            place_ids=request.place_ids,
            include_descendants=request.include_descendants,
            place_state=request.place_state,
            person_ids=people,
            person_match=request.person_match,
            capture=capture,
        )

    scope = build_scope(base.canonical_person_ids)
    match = build_match(request.text) if request.text is not None else None
    profile = _resolve_profile(catalog, request)
    binding = _resolve_binding(catalog, request, profile)
    if query_vector is not None and binding is None:
        # An image query has no text to fall back on, so it must be bound to one space.
        raise fail(
            "an image query needs a bound space_id, image_generation_id and dimension "
            "(or an activated search profile)",
            field="profile_id",
        )
    def digest_of(current: SearchScope) -> str:
        return query_hash(
            # The digest is "the conditions that must stay identical across pages"; for an
            # uploaded image the image itself is that condition, and it has no text.
            text=request.text if query_identity is None else str(query_identity),
            source_id=request.source_id,
            availability=request.availability,
            place_ids=request.place_ids,
            include_descendants=request.include_descendants,
            place_state=request.place_state,
            # ADR-0009 §6.2: the canonical person condition is part of the continuation
            # digest, so a cursor minted for one person condition is never accepted for
            # another.
            person_ids=current.person_ids,
            person_match=current.person_match,
            capture_from=request.capture_from,
            capture_to_exclusive=request.capture_to_exclusive,
            top_k=int(request.top_k),
            page_size=int(request.page_size),
        )

    digest = digest_of(scope)
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
    if match is not None and binding is not None and encoder is None and query_vector is None:
        raise _refuse(
            RUNTIME_CAPABILITY_UNAVAILABLE,
            "this process has no query encoder for the bound embedding space",
            reason="QUERY_ENCODER_UNAVAILABLE",
            generation_id=binding.generation_id,
            space_id=binding.space_id,
        )

    # The semantic channel runs when the session is bound *and* there is something to encode:
    # query text (P2-06A) or an already validated query vector (P2-07 image query).
    uses_vector_channel = binding is not None and (match is not None or query_vector is not None)

    def run_retrieval() -> dict[str, Any]:
        """One retrieval over the scope the attempt currently holds."""
        return search_assets(
            catalog,
            text=request.text,
            encoder=encoder if match is not None and uses_vector_channel else None,
            # The vector channel engages only when there is both a text query and a resolved
            # binding: a filter-only request is served from the authoritative list alone,
            # even when the session is pinned to a profile (P2-05's "纯筛选查询" path).
            space_id=None if not uses_vector_channel else binding.space_id,
            generation_id=None if not uses_vector_channel else binding.generation_id,
            dimension=None if not uses_vector_channel else int(binding.dimension),
            top_k=int(request.top_k),
            source_id=request.source_id,
            availability=request.availability,
            place_ids=request.place_ids,
            include_descendants=request.include_descendants,
            place_state=request.place_state,
            # The person condition is the canonical one the scope and the digest were built
            # from; a pure person filter therefore answers from the authoritative list alone,
            # with no profile, no encoder and no GPU (ADR-0009 §3.3/§5.5).
            person_ids=scope.person_ids,
            person_match=scope.person_match,
            # ADR-0011 §6.5: the same capture condition, applied by every channel and by the
            # authoritative page; only ``label = capture`` rows can satisfy it.
            capture=scope.capture,
            vector_index=vector_index,
            # Exactly one of "text to encode" and "an already validated vector" reaches the
            # retrieval, and only when the session is actually bound to a space.
            query_vector=(
                list(query_vector) if query_vector is not None and uses_vector_channel else None
            ),
        )

    # ADR-0005 §10 / ADR-0009 §4.2: a new search must not answer with a page that straddles a
    # committed authority change. The anchor (canonical ids + revisions) was read in one
    # snapshot *before* the scope existed; the page is then built - retrieval, rerank, and
    # finally ``_render_page``, which re-reads the authority once more to re-verify every row
    # it is about to publish - and only after that last read is the result accepted. A moved
    # counter means the parts of this page came from two catalog versions, so the whole
    # attempt is discarded (its session is never registered) and re-anchored. The re-runs are
    # bounded and end in a structured refusal, never in a mixed page.
    def publish_once() -> dict[str, Any] | None:
        """Build and render one first page, or ``None`` if it straddled an authority change."""
        candidate = run_retrieval()
        if int(candidate["catalog_filter_revision"]) != base.filter_revision:
            return None  # the cheap early discard: nothing is built or rendered
        entries = _entries_of(candidate)
        outcome = _rerank_outcome(
            catalog,
            request,
            profile=profile,
            match=match,
            entries=entries,
            reranker=reranker,
            timeout_ms=int(rerank_timeout_ms),
        )
        # Prepared, *not* published: a session may only exist once its page is known to be an
        # answer about one catalog version, so a discarded attempt cannot leak (or evict) one.
        filters_echo = _filters_echo(candidate, binding)
        session = sessions.build_session(
            query_hash=digest,
            entries=entries,
            profile_id=None if profile is None else profile.profile_id,
            image_generation_id=None if binding is None else binding.generation_id,
            space_id=None if binding is None else binding.space_id,
            dimension=None if binding is None else int(binding.dimension),
            catalog_filter_revision=int(candidate["catalog_filter_revision"]),
            suggestion_revision=int(candidate["filters"]["suggestion_revision"]),
            fusion_config=(
                dict(DEFAULT_FUSION_CONFIG) if profile is None else dict(profile.fusion_config)
            ),
            query=candidate["query"],
            filters=filters_echo,
            channels=_channel_summary(
                candidate,
                match=match,
                binding=binding,
                top_k=int(request.top_k),
                image_query=query_vector is not None,
            ),
            pending_semantic=candidate["pending_semantic"],
            search_projection=candidate["search_projection"],
            rerank_state=str(outcome.state),
            reranked=(
                None if str(outcome.state) == RERANK_STATE_NOT_REQUESTED else outcome.to_envelope()
            ),
            state=str(candidate["state"]),
            candidates_truncated=len(entries) > MAX_SESSION_IDS,
        )
        hits, next_offset, has_more, token = _render_page(
            catalog,
            scope,
            session,
            sessions=sessions,
            page_size=int(request.page_size),
            offset=0,
        )
        # ADR-0011 §8.1: read the honesty block of this capture-conditioned page *before* the
        # publication gate, so a discarded attempt never becomes the block a caller sees.
        taken_time = _taken_time_block(catalog, filters_echo)
        # The publication gate runs *after* the page exists, because ``_render_page`` performs
        # the last authoritative read of this response (it re-verifies every row it is about
        # to publish). A pass here means every part of the page - canonical ids, echo, digest,
        # revision binding and the verified rows themselves - belongs to one catalog version.
        if _authority_drift(catalog, request, base) is not None:
            return None
        sessions.register(session)
        return _result_envelope(
            request,
            session,
            hits=hits,
            offset=0,
            next_offset=next_offset,
            has_more=has_more,
            token=token,
            taken_time=taken_time,
        )

    for attempt in range(SEARCH_PUBLISH_ATTEMPTS):
        page = publish_once()
        if page is not None:
            return page
        if attempt + 1 >= SEARCH_PUBLISH_ATTEMPTS:
            break
        base = _person_authority_base(catalog, request)
        scope = build_scope(base.canonical_person_ids)
        digest = digest_of(scope)
    raise _publication_refusal(catalog, request, base)


# ------------------------------------------------------------------ P2-06B feedback

#: The feedback log is paged by an opaque keyset token; it never exposes a row offset.
FEEDBACK_CURSOR_PREFIX = "fb1"


def feedback_limit(limit: int | None) -> int:
    """The page bound of the feedback log, defaulted and clamped like every other listing."""
    if limit is None:
        return DEFAULT_FEEDBACK_PAGE_SIZE
    value = int(limit)
    if value < 1:
        raise fail("limit must be at least 1", field="limit")
    return min(value, MAX_FEEDBACK_PAGE_SIZE)


def _decode_feedback_cursor(text: str | None) -> int:
    if text is None or str(text).strip() == "":
        return 0
    raw = str(text).strip()
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError) as error:
        raise _refuse(INVALID_CURSOR, "the feedback cursor is not valid") from error
    prefix, _, value = decoded.partition(":")
    if prefix != FEEDBACK_CURSOR_PREFIX or not value.isdigit():
        raise _refuse(INVALID_CURSOR, "the feedback cursor is not valid")
    return int(value)


def _encode_feedback_cursor(feedback_id: int) -> str:
    payload = f"{FEEDBACK_CURSOR_PREFIX}:{int(feedback_id)}"
    return base64.urlsafe_b64encode(payload.encode("ascii")).decode("ascii").rstrip("=")


def submit_feedback(
    catalog: Any,
    request: FeedbackRequest,
    *,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Record one explicit verdict, idempotently and without touching any ranking input.

    * an unknown ``asset_id`` is a caller error (422): feedback never creates or resurrects
      an asset;
    * the same ``request_id`` with the same payload returns the recorded row and writes
      nothing (``replayed=true``);
    * the same ``request_id`` with a different payload is a structured
      ``FEEDBACK_IDEMPOTENCY_CONFLICT`` and writes nothing - the first verdict stays the one
      on record rather than being silently overwritten;
    * nothing here changes ``catalog_filter_revision``, ``suggestion_revision``,
      ``search_documents``, visibility or any vector row. Marking a photo "not relevant"
      does not remove it from later searches; that would be a new product decision.
    """
    asset = catalog.read_one(
        "SELECT asset_id FROM assets WHERE asset_id = ?", (int(request.asset_id),)
    )
    if asset is None:
        raise fail(
            "the feedback refers to an asset that does not exist",
            field="asset_id",
            asset_id=int(request.asset_id),
        )
    moment = int(catalog.now_ms() if now_ms is None else now_ms)
    with catalog.write() as connection:
        existing = connection.execute(
            "SELECT * FROM search_feedback WHERE request_id = ?", (str(request.request_id),)
        ).fetchone()
        if existing is not None:
            record = FeedbackRecord.from_row(existing)
            stored = (
                record.search_session_id,
                record.asset_id,
                record.rank,
                record.relevance,
                record.note,
            )
            requested = request.payload_identity()
            if stored != requested:
                names = (
                    "search_session_id",
                    "asset_id",
                    "rank",
                    "relevance",
                    "note",
                )
                raise _refuse(
                    FEEDBACK_IDEMPOTENCY_CONFLICT,
                    "this request_id already recorded a different feedback payload",
                    request_id=str(request.request_id),
                    existing_feedback_id=int(record.feedback_id),
                    different_fields=[
                        name
                        for name, before, after in zip(names, stored, requested, strict=True)
                        if before != after
                    ],
                )
            return FeedbackResponse(feedback=record, replayed=True).to_envelope()
        cursor = connection.execute(
            "INSERT INTO search_feedback(request_id, search_session_id, asset_id, rank, "
            "relevance, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(request.request_id),
                str(request.search_session_id),
                int(request.asset_id),
                int(request.rank),
                str(request.relevance),
                request.note,
                moment,
            ),
        )
        row = connection.execute(
            "SELECT * FROM search_feedback WHERE feedback_id = ?", (int(cursor.lastrowid),)
        ).fetchone()
        return FeedbackResponse(feedback=FeedbackRecord.from_row(row), replayed=False).to_envelope()


def list_feedback(catalog: Any, *, limit: int, cursor: str | None = None) -> dict[str, Any]:
    """One keyset page of the append-only feedback log (its own fields, nothing else)."""
    after = _decode_feedback_cursor(cursor)
    rows = catalog.read_all(
        "SELECT * FROM search_feedback WHERE feedback_id > ? ORDER BY feedback_id LIMIT ?",
        (int(after), int(limit) + 1),
    )
    has_more = len(rows) > int(limit)
    page = rows[: int(limit)]
    return FeedbackPage(
        feedback=tuple(FeedbackRecord.from_row(row) for row in page),
        limit=int(limit),
        next_cursor=(
            _encode_feedback_cursor(int(page[-1]["feedback_id"])) if has_more and page else None
        ),
        has_more=has_more,
    ).to_envelope()


__all__ = [
    "MAX_TOP_K",
    "default_vector_index",
    "list_feedback",
    "resolve_request_binding",
    "search_assets",
    "search_request",
    "submit_feedback",
]
