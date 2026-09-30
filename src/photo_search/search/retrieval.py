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

from photo_search.catalog.errors import (
    INVALID_PERSON_FILTER,
    PROCESSOR_MISMATCH,
    SPACE_MISMATCH,
    CatalogError,
)
from photo_search.indexing.store import LanceVectorIndex
from photo_search.persons.members import MEMBER_PREDICATE
from photo_search.places.filter import place_condition, place_filter_summary
from photo_search.search import query as search_query
from photo_search.search.tokens import VALIDATION_ERROR, MatchQuery
from photo_search.taken_time import contract as time_contract
from photo_search.taken_time import read as time_read

#: Fixed per-channel candidate bound for the personal-trial product (architecture §9.2).
DEFAULT_TOP_K = 5000
#: Hard ceiling of one request's candidate list, shared by the service and the protocol
#: envelope so the two can never disagree about what "too large" means.
MAX_TOP_K = 5000
#: How many authoritative candidates one vector block covers. Every block costs one engine
#: query and each of those scans the generation, so the vector channel costs
#: ``ceil(candidates / block)`` whole-generation scans. P4-01 measured a 300 000-row
#: generation at 92 s per search with a 1 000-row block and 19.8 s with a 5 000-row block
#: (3.6 s once the ``asset_id`` scalar index exists). The merged answer is *identical*
#: either way - :func:`vector_channel` merges the per-block top-k exactly, so every global
#: top-k member is found in its own block's top-k - which is why this bound is an
#: access-path parameter and never a recall parameter.
#: P4-02M then cut each block's *transfer* to ``top_k + 1`` rows (the engine reports
#: ``resolved=False`` when a block's boundary tie group was cut, and exactly that block
#: falls back to the exhaustive one-row-per-candidate call), and P4-02X raised this bound
#: from 5 000 to 15 000: on the frozen 300 000-row fixture the vector channel goes from
#: ~60 to ~20 engine calls while every final ``(asset_id, distance)`` row stays identical
#: (paired service ratio ~0.86 - see docs/reports/P4-02M-bounded-block-size-retest.md).
#: The one cost that grows with this bound is a fallback block, which then materialises
#: that whole block; 15 000 was chosen over the marginally faster 30 000 for exactly that
#: reason (a tie-heavy corpus can only ever hit the fallback path).
SCOPE_BLOCK_SIZE = 15000
#: Safety valve for a runaway scan; an incomplete scan is reported, never hidden.
MAX_VECTOR_BLOCKS = 1000
#: How many ids the authoritative page publishes (architecture §9.5: at most 1000
#: ordered ids per search session).
AUTHORITY_PAGE_LIMIT = 1000
#: How many "semantic projection still pending" entries the response carries.
TOPUP_LIMIT = 200
#: The availability spellings the asset listing already accepts (P1-04).
AVAILABILITY_VALUES = ("present", "missing", "any")
#: ADR-0009 §2.2: the frozen ceiling of one ``person_ids`` condition. It lives beside the
#: other retrieval bounds so the envelope, the pydantic model and the predicate all quote
#: the same number.
MAX_PERSON_IDS = 100
#: ADR-0009 §2.1: the two spellings of ``person_match``.
PERSON_MATCH_VALUES = ("all", "any")

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
    #: ADR-0009 §3: the *canonical* person ids of the request, already de-duplicated and
    #: sorted by the caller, plus how they combine. An empty tuple means "no person
    #: condition at all" - it never means "a photo without people".
    person_ids: tuple[int, ...] = ()
    person_match: str = "any"
    #: ADR-0011 §6: the optional capture-time range. It is one more authoritative condition
    #: of the same AND set, applied by every channel, the authoritative page and the pre-page
    #: re-check - and it may only ever match a ``label = capture`` row.
    capture: time_contract.CaptureRange | None = None

    @classmethod
    def build(
        cls,
        *,
        source_id: int | None = None,
        availability: str = "any",
        place_ids: Sequence[int] | None = None,
        include_descendants: bool = True,
        place_state: str = "any",
        person_ids: Sequence[int] | None = None,
        person_match: str = "any",
        capture: time_contract.CaptureRange | None = None,
    ) -> SearchScope:
        value = str(availability or "any")
        if value not in AVAILABILITY_VALUES:
            raise CatalogError(
                VALIDATION_ERROR,
                "availability must be present, missing or any",
                details={"availability": value},
            )
        people = tuple(sorted({int(item) for item in (person_ids or ())}))
        mode = str(person_match or "any")
        if mode not in PERSON_MATCH_VALUES:
            raise CatalogError(
                VALIDATION_ERROR,
                "person_match must be all or any",
                details={"person_match": mode},
            )
        if len(people) > MAX_PERSON_IDS:
            raise CatalogError(
                INVALID_PERSON_FILTER,
                f"a person condition carries at most {MAX_PERSON_IDS} person ids",
                details={
                    "reason": "person_filter_too_large",
                    "max_person_ids": MAX_PERSON_IDS,
                },
            )
        if mode == "all" and not people:
            # ``all`` over an empty set would silently widen to "no condition"; ADR-0009
            # §2.2 fixes that input as an explicit refusal instead.
            raise CatalogError(
                INVALID_PERSON_FILTER,
                "person_match all needs at least one person id",
                details={"reason": "person_match_all_requires_ids", "person_match": "all"},
            )
        return cls(
            source_id=None if source_id is None else int(source_id),
            availability=value,
            place_ids=tuple(int(item) for item in (place_ids or ())),
            include_descendants=bool(include_descendants),
            place_state=str(place_state or "any"),
            person_ids=people,
            person_match=mode,
            capture=capture,
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
        # Set membership instead of a correlated ``EXISTS``: P4-01B measured the correlated
        # form at 19.4 s for ONE block of a 20 000-row generation (and >60 s at 300 000),
        # because SQLite drove the sub-query from ``source_id`` and re-scanned every
        # location of that source per asset. Both forms select exactly the assets that have
        # a location in that source, and ``file_locations.asset_id`` may be NULL, which
        # ``IN`` treats the same way ``EXISTS ... = `` does (a NULL never matches).
        conditions.append(
            f"{alias}.asset_id IN (SELECT l.asset_id FROM file_locations AS l "
            "WHERE l.source_id = ?)"
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
    person_sql, person_parameters = person_condition(scope, alias=alias)
    if person_sql != "1":
        conditions.append(person_sql)
        parameters.extend(person_parameters)
    capture_sql, capture_parameters = capture_condition(connection, scope, alias=alias)
    if capture_sql != "1":
        conditions.append(capture_sql)
        parameters.extend(capture_parameters)
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


def capture_condition(
    connection: sqlite3.Connection, scope: SearchScope, *, alias: str = "a"
) -> tuple[str, list[Any]]:
    """ADR-0011 §6.4: the half-open capture range over ``<alias>.asset_id``.

    The condition is the *authoritative* SQLite predicate of a time query, never a filter on
    a ranked list: it is pushed into the text channel's MATCH, the vector channel's candidate
    walk and the authoritative page alike. The fragment itself is
    ``taken_time.read.capture_predicate`` - the very same one ``GET /api/v1/assets`` applies -
    so the listing and the search can never drift into two interpretations.
    """
    return time_read.capture_predicate(connection, scope.capture, alias=alias)


def person_condition(scope: SearchScope, *, alias: str = "a") -> tuple[str, list[Any]]:
    """ADR-0009 §3: the photo-level person predicate over ``<alias>.asset_id``.

    A photo belongs to a person exactly when it has at least one *effective member face* of
    that person, so the fragment reused here is ``persons/members.py``'s single
    ``MEMBER_PREDICATE`` - the same one ``GET /api/v1/persons/{id}`` counts with. There is no
    second copy of "which face counts as assigned" in the retrieval layer.

    ``any`` is one sub-query over the requested people; ``all`` is one sub-query per person,
    ANDed. Both are authoritative SQLite predicates that every channel and the pre-page
    re-check apply, never a derived projection.

    They are written as ``asset_id IN (SELECT f.asset_id ...)`` rather than as a correlated
    ``EXISTS``: P4-01B measured the correlated form at 143 s for ONE block of a 20 000-row
    generation (and >60 s at 300 000), because SQLite drove it from ``person_id`` and
    re-scanned that person's faces per asset. The selected photo set is identical - a photo
    matches exactly when it has at least one effective member face of that person - and
    ``faces.asset_id`` is ``NOT NULL``, so ``IN`` and ``EXISTS`` cannot disagree.
    """
    if not scope.person_ids:
        return "1", []

    def exists(person_parameter: str) -> str:
        return (
            f"{alias}.asset_id IN (SELECT f.asset_id FROM face_person_links AS l "
            "JOIN faces AS f ON f.face_id = l.face_id WHERE "
            f"l.person_id {person_parameter} AND {MEMBER_PREDICATE})"
        )

    if scope.person_match == "all":
        return (
            " AND ".join(exists("= ?") for _ in scope.person_ids),
            [int(person_id) for person_id in scope.person_ids],
        )
    placeholders = ", ".join("?" for _ in scope.person_ids)
    return exists(f"IN ({placeholders})"), [int(person_id) for person_id in scope.person_ids]


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
        # ADR-0009 §7.1: the two person keys are always present; without a person condition
        # they are ``[]``/``"any"``. The ids echoed are the canonical, ascending ones.
        "person_ids": [int(value) for value in scope.person_ids],
        "person_match": str(scope.person_match),
        # ADR-0011 §8.1/§6.3: the two capture keys are always present too, as the normalized
        # fixed-width wall-clock spelling or ``null`` when no condition was given.
        **time_contract.capture_echo(scope.capture),
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
        # P4-02M: one bounded call per block - the engine returns its ``top_k + 1`` nearest
        # candidates and reports whether that answer is already complete for the caller's
        # ``(distance, asset_id)`` order. Only a block whose boundary tie group was cut falls
        # back to the exhaustive one-row-per-candidate call, so the merged result is exactly
        # the exhaustive path's while a resolved block transfers ``top_k + 1`` rows instead of
        # one row per candidate id.
        # The bounded access path is an *optimization*, not a new contract: a substitute index
        # that offers only the exhaustive ``search_candidates`` (the A3 contract test's stub,
        # diagnostics) keeps working and produces byte-identical rows, because the exhaustive
        # call *is* the documented behaviour. Only a resolved bounded answer is trusted.
        bounded = getattr(index, "search_candidates_bounded", None)
        hits: list[dict[str, Any]]
        if bounded is None:
            hits = index.search_candidates(
                generation_id=binding.generation_id,
                dimension=binding.dimension,
                query_vector=query_vector,
                asset_ids=ids,
                limit=int(top_k),
                space_id=binding.space_id,
            )
        else:
            hits, resolved = bounded(
                generation_id=binding.generation_id,
                dimension=binding.dimension,
                query_vector=query_vector,
                asset_ids=ids,
                top_k=int(top_k),
                space_id=binding.space_id,
            )
            if not resolved:
                hits = index.search_candidates(
                    generation_id=binding.generation_id,
                    dimension=binding.dimension,
                    query_vector=query_vector,
                    asset_ids=ids,
                    limit=int(top_k),
                    space_id=binding.space_id,
                )
        for row in hits:
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
