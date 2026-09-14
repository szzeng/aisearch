"""Authoritative scope, the two recall channels and the pending-projection top-up.

Architecture §9.2 asks for two bounded channels in parallel, §9.3 fixes how they are
constrained, and ADR-0005 §10 fixes the place semantics. The rule this module implements
is the strict one:

* SQLite decides *membership*. The place/source/availability condition is applied as a
  restriction of the channel query, never as a filter on a global Top-K ("禁止全库 Top-K
  后过滤", architecture §9.3 item 4).
* The vector channel only ranks inside the ids whose ``index_entries`` row is CONFIRMED for
  the bound generation, in blocks of :data:`SCOPE_BLOCK_SIZE` (architecture §9.3 item 3,
  the documented "correct but slower" fallback). The per-block top-k merge is exact.
* Assets whose semantic projection is not confirmed yet are reported by
  :func:`pending_projection_page` instead of being silently dropped: a text hit, a
  re-classified asset or a freshly added photo must never disappear because the vector row
  is late (ADR-0005 §10, architecture §7.2).

Nothing here writes: no query moves ``created_at``, re-encodes an image or changes a
``space_id``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from photo_search.catalog.errors import PROCESSOR_MISMATCH, SPACE_MISMATCH, CatalogError
from photo_search.indexing.store import LanceVectorIndex
from photo_search.places.filter import place_condition, place_filter_summary
from photo_search.search import query as search_query
from photo_search.search.tokens import VALIDATION_ERROR, MatchQuery

#: Per-channel initial candidate bound (architecture §9.2: "初始 Top 200").
DEFAULT_TOP_K = 200
#: Hard ceiling of one request's candidate list, shared by the service and the protocol
#: envelope so the two can never disagree about what "too large" means.
MAX_TOP_K = 1000
#: How many authoritative candidates one vector block covers. Every block costs one
#: engine query, so the vector channel costs ``ceil(candidates / block)`` queries.
SCOPE_BLOCK_SIZE = 1000
#: Safety valve for a runaway scan; an incomplete scan is reported, never hidden.
MAX_VECTOR_BLOCKS = 1000
#: How many ids the authoritative page publishes (architecture §9.5: at most 1000
#: ordered ids per search session).
AUTHORITY_PAGE_LIMIT = 1000
#: How many "semantic projection still pending" entries the response carries.
TOPUP_LIMIT = 200
#: The availability spellings the asset listing already accepts (P1-04).
AVAILABILITY_VALUES = ("present", "missing", "any")

_CONFIRMED_BLOCK_SQL = (
    "SELECT e.asset_id FROM index_entries AS e JOIN assets AS a ON a.asset_id = e.asset_id "
    "WHERE e.generation_id = ? AND e.space_id = ? AND e.state = 'CONFIRMED' "
    "AND ({scope}) AND e.asset_id > ? ORDER BY e.asset_id LIMIT ?"
)
_READY_ENTRY_SQL = (
    "EXISTS (SELECT 1 FROM index_entries AS e JOIN index_generations AS g "
    "ON g.generation_id = e.generation_id WHERE e.space_id = ? AND e.asset_id = a.asset_id "
    "AND e.generation_id = ? AND e.state = 'CONFIRMED' AND g.state = 'READY')"
)


@dataclass(frozen=True)
class SearchScope:
    """The authoritative conditions every channel has to respect."""

    source_id: int | None = None
    availability: str = "any"
    place_ids: tuple[int, ...] = ()
    include_descendants: bool = True
    place_state: str = "any"

    @classmethod
    def build(
        cls,
        *,
        source_id: int | None = None,
        availability: str = "any",
        place_ids: Sequence[int] | None = None,
        include_descendants: bool = True,
        place_state: str = "any",
    ) -> SearchScope:
        value = str(availability or "any")
        if value not in AVAILABILITY_VALUES:
            raise CatalogError(
                VALIDATION_ERROR,
                "availability must be present, missing or any",
                details={"availability": value},
            )
        return cls(
            source_id=None if source_id is None else int(source_id),
            availability=value,
            place_ids=tuple(int(item) for item in (place_ids or ())),
            include_descendants=bool(include_descendants),
            place_state=str(place_state or "any"),
        )


@dataclass(frozen=True)
class VectorBinding:
    """One explicitly bound generation: the space a query vector is allowed to rank in."""

    generation_id: str
    space_id: str
    dimension: int
    processor_id: str
    state: str
    is_active: bool

    @property
    def semantic_complete(self) -> bool:
        """Whether every asset of this generation is expected to be projected."""
        return self.state == "READY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation_id": str(self.generation_id),
            "space_id": str(self.space_id),
            "dimension": int(self.dimension),
            "processor_id": str(self.processor_id),
            "generation_state": str(self.state),
            "is_active": bool(self.is_active),
            "semantic_complete": bool(self.semantic_complete),
        }


def resolve_binding(
    catalog: Any,
    *,
    generation_id: str,
    space_id: str,
    dimension: int,
) -> VectorBinding:
    """Bind one query to a generation, or refuse it structurally.

    A missing or retired generation and a space/dimension that does not match are
    structured refusals: the caller never gets a silently different space (ADR-0003 §8).
    """
    row = catalog.read_one(
        "SELECT * FROM index_generations WHERE generation_id = ?", (str(generation_id),)
    )
    if row is None:
        raise CatalogError(
            PROCESSOR_MISMATCH,
            "the bound index generation does not exist",
            details={"generation_id": str(generation_id), "reason": "GENERATION_NOT_FOUND"},
        )
    if str(row["space_id"]) != str(space_id):
        raise CatalogError(
            SPACE_MISMATCH,
            "the bound generation holds a different embedding space",
            details={
                "generation_id": str(generation_id),
                "requested_space_id": str(space_id),
                "generation_space_id": str(row["space_id"]),
            },
        )
    if int(row["dimension"]) != int(dimension):
        raise CatalogError(
            SPACE_MISMATCH,
            "the bound generation has a different dimension",
            details={
                "generation_id": str(generation_id),
                "requested_dimension": int(dimension),
                "generation_dimension": int(row["dimension"]),
            },
        )
    state = str(row["state"])
    if state == "RETIRED":
        raise CatalogError(
            PROCESSOR_MISMATCH,
            "a retired index generation is not served",
            details={"generation_id": str(generation_id), "reason": "GENERATION_RETIRED"},
        )
    return VectorBinding(
        generation_id=str(row["generation_id"]),
        space_id=str(row["space_id"]),
        dimension=int(row["dimension"]),
        processor_id=str(row["processor_id"]),
        state=state,
        is_active=bool(row["is_active"]),
    )


def scope_condition(
    connection: sqlite3.Connection, scope: SearchScope, *, alias: str = "a"
) -> tuple[str, list[Any]]:
    """One SQL predicate over ``<alias>.asset_id`` plus its bound parameters."""
    conditions: list[str] = []
    parameters: list[Any] = []
    if scope.source_id is not None:
        conditions.append(
            "EXISTS (SELECT 1 FROM file_locations AS l WHERE l.asset_id = "
            f"{alias}.asset_id AND l.source_id = ?)"
        )
        parameters.append(int(scope.source_id))
    if scope.availability == "present":
        conditions.append(
            "EXISTS (SELECT 1 FROM file_locations AS l WHERE l.asset_id = "
            f"{alias}.asset_id AND l.availability = 'PRESENT')"
        )
    elif scope.availability == "missing":
        conditions.append(
            "NOT EXISTS (SELECT 1 FROM file_locations AS l WHERE l.asset_id = "
            f"{alias}.asset_id AND l.availability = 'PRESENT')"
        )
    place_sql, place_parameters = place_condition(
        connection,
        place_ids=scope.place_ids,
        include_descendants=scope.include_descendants,
        place_state=scope.place_state,
        alias=alias,
    )
    if place_sql != "1":
        conditions.append(place_sql)
        parameters.extend(place_parameters)
    return (" AND ".join(conditions) if conditions else "1"), parameters


def scope_view(connection: sqlite3.Connection, scope: SearchScope) -> dict[str, Any]:
    """The echo of the applied conditions, including the two revisions they bind."""
    summary = place_filter_summary(
        connection,
        place_ids=scope.place_ids,
        include_descendants=scope.include_descendants,
        place_state=scope.place_state,
    )
    return {
        "source_id": scope.source_id,
        "availability": scope.availability,
        "place_ids": summary["place_ids"],
        "include_descendants": summary["include_descendants"],
        "place_state": summary["place_state"],
        "expanded_place_ids": summary["expanded_place_ids"],
        "catalog_filter_revision": summary["catalog_filter_revision"],
        "suggestion_revision": summary["suggestion_revision"],
    }


def authoritative_page(
    catalog: Any, scope: SearchScope, *, limit: int = AUTHORITY_PAGE_LIMIT
) -> dict[str, Any]:
    """The bounded authoritative candidate list, in ``asset_id`` order.

    This is the list a pure filter query answers with, and the proof that no channel could
    have reached a row outside the authority.
    """
    where, parameters = scope_condition(catalog.connection, scope)
    total = int(
        catalog.read_one(
            f"SELECT COUNT(*) AS value FROM assets AS a WHERE {where}", tuple(parameters)
        )["value"]
    )
    rows = catalog.read_all(
        f"SELECT a.asset_id FROM assets AS a WHERE {where} ORDER BY a.asset_id LIMIT ?",
        (*parameters, int(limit)),
    )
    return {
        "asset_ids": [int(row["asset_id"]) for row in rows],
        "count": len(rows),
        "total": total,
        "limit": int(limit),
        "truncated": total > len(rows),
        "order": "asset_id",
    }


def fts_channel(
    catalog: Any, match: MatchQuery, scope: SearchScope, *, top_k: int = DEFAULT_TOP_K
) -> list[dict[str, Any]]:
    """The text channel: one bounded MATCH, restricted by the authoritative predicate.

    The predicate is pushed into the same statement, so the FTS ranking only ever sees
    authoritative rows; ``bm25()`` is rank evidence and is never mixed with a vector
    distance.
    """
    where, parameters = scope_condition(catalog.connection, scope)
    sql = (
        "SELECT search_documents_fts.rowid AS asset_id, "
        "bm25(search_documents_fts) AS score FROM search_documents_fts "
        "JOIN assets AS a ON a.asset_id = search_documents_fts.rowid "
        f"WHERE search_documents_fts MATCH ? AND ({where}) "
        "ORDER BY score, asset_id LIMIT ?"
    )
    rows = catalog.read_all(sql, (match.expression, *parameters, int(top_k)))
    return [
        {"asset_id": int(row["asset_id"]), "score": float(row["score"])} for row in rows
    ]


def text_column_matches(
    catalog: Any, match: MatchQuery, asset_ids: Sequence[int]
) -> dict[int, list[str]]:
    """Which derived column produced each text hit (``filename_text``/``place_text``).

    P2-04 stores file names and the *effective* place in separate columns, so a column
    scoped MATCH answers "the file name says so" versus "the effective place says so"
    without inventing a second matching rule.
    """
    ids = sorted({int(value) for value in asset_ids})
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    hits: dict[int, list[str]] = {}
    for column in ("filename_text", "place_text"):
        expression = f"{{{column}}} : ({match.expression})"
        rows = catalog.read_all(
            "SELECT rowid AS asset_id FROM search_documents_fts "
            f"WHERE search_documents_fts MATCH ? AND rowid IN ({placeholders})",
            (expression, *ids),
        )
        for row in rows:
            hits.setdefault(int(row["asset_id"]), []).append(column)
    return {asset_id: sorted(columns) for asset_id, columns in hits.items()}


def vector_channel(
    catalog: Any,
    *,
    index: LanceVectorIndex,
    binding: VectorBinding,
    query_vector: Sequence[float],
    scope: SearchScope,
    top_k: int = DEFAULT_TOP_K,
    block_size: int = SCOPE_BLOCK_SIZE,
    max_blocks: int = MAX_VECTOR_BLOCKS,
) -> dict[str, Any]:
    """The image-text channel: top-k inside the *confirmed* authoritative candidates.

    The candidate stream is a keyset walk over ``index_entries`` (CONFIRMED) joined with
    the authority, so the merge of the per-block top-k is exactly the global top-k of that
    candidate set - no global Top-K is ever fetched and then filtered.
    """
    where, parameters = scope_condition(catalog.connection, scope)
    merged: dict[int, dict[str, Any]] = {}
    blocks = scanned = 0
    truncated = False
    after = 0
    while True:
        rows = catalog.read_all(
            _CONFIRMED_BLOCK_SQL.format(scope=where),
            (
                binding.generation_id,
                binding.space_id,
                *parameters,
                after,
                int(block_size),
            ),
        )
        ids = [int(row["asset_id"]) for row in rows]
        if not ids:
            break
        blocks += 1
        scanned += len(ids)
        for row in index.search_candidates(
            generation_id=binding.generation_id,
            dimension=binding.dimension,
            query_vector=query_vector,
            asset_ids=ids,
            limit=int(top_k),
            space_id=binding.space_id,
        ):
            merged[int(row["asset_id"])] = row
        if len(ids) < int(block_size):
            break
        after = ids[-1]
        if blocks >= int(max_blocks):
            truncated = True
            break
    ranked = sorted(merged.values(), key=lambda item: (item["distance"], item["asset_id"]))
    return {
        "hits": ranked[: int(top_k)],
        "blocks": blocks,
        "scanned_assets": scanned,
        "truncated": truncated,
    }


def pending_projection_page(
    catalog: Any,
    *,
    binding: VectorBinding,
    scope: SearchScope,
    match: MatchQuery | None = None,
    limit: int = TOPUP_LIMIT,
) -> dict[str, Any]:
    """Authoritative candidates whose semantic projection is not confirmed yet.

    ADR-0005 §10: "只过滤旧结果只能移除失效命中，不能补回刚归类、刚改名或刚改父级的
    资产". This page is the opposite direction - it names the assets the vector channel
    cannot represent yet, so a text hit or a fresh classification is never lost just
    because its vector row is late, and a caller can say "语义索引仍在追赶".
    """
    where, parameters = scope_condition(catalog.connection, scope)
    conditions = [f"({where})", "NOT " + _READY_ENTRY_SQL]
    # The two SELECT-list sub-queries bind first, then the WHERE clause in text order.
    select_parameters: list[Any] = [
        binding.space_id,
        binding.generation_id,
        binding.space_id,
        binding.generation_id,
    ]
    where_parameters: list[Any] = [*parameters, binding.space_id, binding.generation_id]
    if match is not None:
        text_sql, text_parameters = search_query.text_condition(match, alias="a")
        conditions.append(text_sql)
        where_parameters.extend(text_parameters)
    sql = (
        "SELECT a.asset_id, "
        "(SELECT e.state FROM index_entries AS e WHERE e.space_id = ? AND "
        "e.asset_id = a.asset_id AND e.generation_id = ?) AS entry_state, "
        "(SELECT COUNT(*) FROM index_outbox AS o WHERE o.space_id = ? AND "
        "o.asset_id = a.asset_id AND o.generation_id = ? AND o.state <> 'CONFIRMED') "
        "AS pending_events "
        "FROM assets AS a "
        f"WHERE {' AND '.join(conditions)} ORDER BY a.asset_id LIMIT ?"
    )
    rows = catalog.read_all(sql, (*select_parameters, *where_parameters, int(limit) + 1))
    truncated = len(rows) > int(limit)
    entries = [
        {
            "asset_id": int(row["asset_id"]),
            "entry_state": row["entry_state"],
            "pending_events": int(row["pending_events"]),
            "generation_state": binding.state,
            "semantic_ready": False,
        }
        for row in rows[: int(limit)]
    ]
    return {
        "entries": entries,
        "count": len(entries),
        "limit": int(limit),
        "truncated": truncated,
        "generation_id": binding.generation_id,
        "generation_state": binding.state,
        "reason": (
            "GENERATION_NOT_READY" if not binding.semantic_complete else "ENTRY_NOT_CONFIRMED"
        ),
    }
