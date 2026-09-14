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

from photo_search.catalog.errors import CatalogError
from photo_search.config import resolve_data_root, vectors_dir
from photo_search.indexing.store import LanceVectorIndex
from photo_search.scheduler.contracts import CONTRACT_VERSION
from photo_search.search import documents as search_documents
from photo_search.search import retrieval as retrieval_module
from photo_search.search.embedding import QueryEncoder, encode_query
from photo_search.search.fusion import (
    DEFAULT_CHANNEL_WEIGHTS,
    FTS_CHANNEL,
    VECTOR_CHANNEL,
    FusionHit,
    reciprocal_rank_fusion,
)
from photo_search.search.retrieval import (
    AUTHORITY_PAGE_LIMIT,
    DEFAULT_TOP_K,
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
from photo_search.search.tokens import VALIDATION_ERROR, MatchQuery, build_match

#: The hard ceiling of one request's per-channel candidate list.
MAX_TOP_K = 1000

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


__all__ = [
    "MAX_TOP_K",
    "default_vector_index",
    "search_assets",
]
