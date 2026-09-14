"""The frozen ``p2-contracts.v1`` search envelopes, as executable dataclasses.

The architecture §9.5/§10.2 query surface and ADR-0003 §8/§9 are authoritative; this module
does not redefine them. It validates the exact closed shapes they freeze (unknown fields
are refused, ids are non-empty strings, times are UTC epoch milliseconds) and converts
them to and from plain JSON, so the HTTP layer, the service layer and a diagnostic fixture
all speak the same protocol.

Deliberately *shape only*: the envelope never parses a natural language query, never turns
``text`` into conditions and never invents a filter. ``text`` is handed to P2-04's
``build_match`` exactly as the caller wrote it, and every refusal below is a structured
``VALIDATION_ERROR`` (HTTP 422) instead of a Python traceback.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from photo_search.catalog.errors import CatalogError
from photo_search.scheduler.contracts import CONTRACT_VERSION
from photo_search.search.retrieval import DEFAULT_TOP_K, MAX_TOP_K
from photo_search.search.tokens import VALIDATION_ERROR

#: The availability spellings the asset listing and the retrieval scope already accept.
AVAILABILITY_VALUES = ("present", "missing", "any")
#: ADR-0005 §10 / P2-03A: the place states a caller may filter by.
PLACE_STATE_VALUES = ("any", "assigned", "unassigned", "manual_unassigned", "suggested")
#: The per-request page bound. One page is a slice of the session's ordered candidates.
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
#: ``state`` of one answered page. ``FILTER_REVISION_CHANGED`` means an authoritative place
#: fact moved while this query ran: the hits are re-verified, but the caller is told that a
#: fresher answer may exist (P2-05 semantics, carried through the protocol unchanged).
RESULT_STATES = ("OK", "FILTER_REVISION_CHANGED")

_REQUEST_REQUIRED = ("contract_version", "request_id")
_REQUEST_OPTIONAL = (
    "text",
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
)
_HIT_FIELDS = (
    "asset_id",
    "rank",
    "score",
    "matched_by",
    "hit_reason",
    "matched_text",
    "semantic_ready",
)
_RESULT_FIELDS = (
    "contract_version",
    "request_id",
    "search_session_id",
    "profile_id",
    "image_generation_id",
    "space_id",
    "dimension",
    "catalog_filter_revision",
    "suggestion_revision",
    "fusion_config",
    "query",
    "filters",
    "hits",
    "page_size",
    "page_offset",
    "next_cursor",
    "has_more",
    "total_candidates",
    "candidates_truncated",
    "channels",
    "pending_semantic",
    "search_projection",
    "state",
)


def fail(message: str, **details: Any) -> CatalogError:
    """One structured caller-shape refusal (HTTP 422), with no local path in the detail."""
    return CatalogError(
        VALIDATION_ERROR, message, details={key: value for key, value in details.items()}
    )


def _object(value: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise fail(f"{where} must be a JSON object", where=where)
    return dict(value)


def _closed(
    payload: Mapping[str, Any],
    *,
    where: str,
    required: Sequence[str],
    optional: Sequence[str] = (),
) -> None:
    """Refuse a missing required field and any field this contract does not define."""
    missing = [name for name in required if name not in payload]
    unexpected = [name for name in payload if name not in required and name not in optional]
    if missing or unexpected:
        raise fail(
            f"{where} does not match the frozen contract shape",
            where=where,
            missing=sorted(missing),
            unexpected=sorted(unexpected),
        )


def _text(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise fail(f"{where} must be a non-empty string", where=where)
    return value


def _optional_text(value: Any, *, where: str) -> str | None:
    return None if value is None else _text(value, where=where)


def _integer(value: Any, *, where: str, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise fail(f"{where} must be an integer", where=where)
    number = int(value)
    if number < int(minimum):
        raise fail(f"{where} must be at least {minimum}", where=where)
    if maximum is not None and number > int(maximum):
        raise fail(f"{where} must be at most {maximum}", where=where)
    return number


def _optional_integer(value: Any, *, where: str, minimum: int = 0) -> int | None:
    return None if value is None else _integer(value, where=where, minimum=minimum)


def _boolean(value: Any, *, where: str) -> bool:
    if not isinstance(value, bool):
        raise fail(f"{where} must be a boolean", where=where)
    return value


def _enum(value: Any, allowed: Sequence[str], *, where: str) -> str:
    if value not in allowed:
        raise fail(f"{where} is outside {list(allowed)}", where=where, value=str(value))
    return str(value)


def _integer_list(value: Any, *, where: str) -> tuple[int, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise fail(f"{where} must be a JSON array", where=where)
    items: list[int] = []
    for index, item in enumerate(value):
        number = _integer(item, where=f"{where}[{index}]", minimum=1)
        if number not in items:
            items.append(number)
    return tuple(items)


def _number_or_none(value: Any, *, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise fail(f"{where} must be a number", where=where)
    return float(value)


@dataclass(frozen=True)
class SearchRequest:
    """One search request: the query, the explicit conditions and the continuation token."""

    request_id: str
    text: str | None = None
    profile_id: str | None = None
    space_id: str | None = None
    image_generation_id: str | None = None
    dimension: int | None = None
    source_id: int | None = None
    availability: str = "any"
    place_ids: tuple[int, ...] = ()
    include_descendants: bool = True
    place_state: str = "any"
    top_k: int = DEFAULT_TOP_K
    page_size: int = DEFAULT_PAGE_SIZE
    search_session_id: str | None = None
    cursor: str | None = None
    contract_version: str = CONTRACT_VERSION

    @classmethod
    def from_envelope(cls, payload: Mapping[str, Any]) -> SearchRequest:
        body = _object(payload, where="SearchRequest")
        _closed(
            body,
            where="SearchRequest",
            required=_REQUEST_REQUIRED,
            optional=_REQUEST_OPTIONAL,
        )
        version = _text(body["contract_version"], where="contract_version")
        if version != CONTRACT_VERSION:
            raise fail(
                "the request declares a different contract version",
                contract_version=version,
                expected=CONTRACT_VERSION,
            )
        dimension = _optional_integer(body.get("dimension"), where="dimension", minimum=1)
        return cls(
            request_id=_text(body["request_id"], where="request_id"),
            text=_optional_text(body.get("text"), where="text"),
            profile_id=_optional_text(body.get("profile_id"), where="profile_id"),
            space_id=_optional_text(body.get("space_id"), where="space_id"),
            image_generation_id=_optional_text(
                body.get("image_generation_id"), where="image_generation_id"
            ),
            dimension=dimension,
            source_id=_optional_integer(body.get("source_id"), where="source_id", minimum=1),
            availability=_enum(
                body.get("availability", "any"), AVAILABILITY_VALUES, where="availability"
            ),
            place_ids=_integer_list(body.get("place_ids", ()), where="place_ids"),
            include_descendants=_boolean(
                body.get("include_descendants", True), where="include_descendants"
            ),
            place_state=_enum(
                body.get("place_state", "any"), PLACE_STATE_VALUES, where="place_state"
            ),
            top_k=_integer(body.get("top_k", DEFAULT_TOP_K), where="top_k", minimum=1, maximum=MAX_TOP_K),
            page_size=_integer(
                body.get("page_size", DEFAULT_PAGE_SIZE),
                where="page_size",
                minimum=1,
                maximum=MAX_PAGE_SIZE,
            ),
            search_session_id=_optional_text(
                body.get("search_session_id"), where="search_session_id"
            ),
            cursor=_optional_text(body.get("cursor"), where="cursor"),
            contract_version=version,
        )

    def to_envelope(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "request_id": self.request_id,
            "text": self.text,
            "profile_id": self.profile_id,
            "space_id": self.space_id,
            "image_generation_id": self.image_generation_id,
            "dimension": self.dimension,
            "source_id": self.source_id,
            "availability": self.availability,
            "place_ids": list(self.place_ids),
            "include_descendants": self.include_descendants,
            "place_state": self.place_state,
            "top_k": self.top_k,
            "page_size": self.page_size,
            "search_session_id": self.search_session_id,
            "cursor": self.cursor,
        }

    @property
    def explicit_binding(self) -> tuple[str, str, int] | None:
        """The explicitly bound ``(space_id, generation_id, dimension)`` triple, or ``None``.

        A partially bound triple is refused here exactly as P2-05 refuses it: the encoder,
        the space, the generation and the dimension are bound together or not at all.
        """
        given = {
            "space_id": self.space_id,
            "image_generation_id": self.image_generation_id,
            "dimension": self.dimension,
        }
        present = [name for name, value in given.items() if value is not None]
        if not present:
            return None
        if len(present) != 3:
            raise fail(
                "the vector channel binds space_id, image_generation_id and dimension together",
                missing=sorted(name for name in given if name not in present),
            )
        return (str(self.space_id), str(self.image_generation_id), int(self.dimension))


@dataclass(frozen=True)
class SearchHit:
    """One row of one page: what the asset is, why it is here and how it was ranked."""

    asset_id: int
    rank: int
    score: float | None
    matched_by: dict[str, int]
    hit_reason: str
    matched_text: tuple[str, ...] = ()
    semantic_ready: bool = False

    @classmethod
    def from_envelope(cls, payload: Mapping[str, Any]) -> SearchHit:
        body = _object(payload, where="SearchHit")
        _closed(body, where="SearchHit", required=_HIT_FIELDS)
        matched_by = _object(body["matched_by"], where="matched_by")
        return cls(
            asset_id=_integer(body["asset_id"], where="asset_id", minimum=1),
            rank=_integer(body["rank"], where="rank", minimum=1),
            score=_number_or_none(body["score"], where="score"),
            matched_by={
                _text(name, where="matched_by key"): _integer(
                    value, where=f"matched_by[{name}]", minimum=1
                )
                for name, value in matched_by.items()
            },
            hit_reason=_text(body["hit_reason"], where="hit_reason"),
            matched_text=_text_list(body["matched_text"]),
            semantic_ready=_boolean(body["semantic_ready"], where="semantic_ready"),
        )

    def to_envelope(self) -> dict[str, Any]:
        return {
            "asset_id": int(self.asset_id),
            "rank": int(self.rank),
            "score": None if self.score is None else float(self.score),
            "matched_by": {str(name): int(rank) for name, rank in self.matched_by.items()},
            "hit_reason": str(self.hit_reason),
            "matched_text": list(self.matched_text),
            "semantic_ready": bool(self.semantic_ready),
        }


def _text_list(value: Any) -> tuple[str, ...]:
    """A non-empty strings array (``matched_text`` names derived columns, never a path)."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise fail("matched_text must be a JSON array", where="matched_text")
    return tuple(_text(item, where=f"matched_text[{index}]") for index, item in enumerate(value))


@dataclass(frozen=True)
class SearchResult:
    """One answered page, bound to the session, profile, generation and filter revision."""

    request_id: str
    search_session_id: str
    profile_id: str | None
    image_generation_id: str | None
    space_id: str | None
    dimension: int | None
    catalog_filter_revision: int
    suggestion_revision: int
    fusion_config: dict[str, Any]
    query: dict[str, Any] | None
    filters: dict[str, Any]
    hits: tuple[SearchHit, ...]
    page_size: int
    page_offset: int
    next_cursor: str | None
    has_more: bool
    total_candidates: int
    candidates_truncated: bool
    channels: dict[str, Any]
    pending_semantic: dict[str, Any] | None
    search_projection: dict[str, Any]
    state: str
    contract_version: str = CONTRACT_VERSION

    @classmethod
    def from_envelope(cls, payload: Mapping[str, Any]) -> SearchResult:
        body = _object(payload, where="SearchResult")
        _closed(body, where="SearchResult", required=_RESULT_FIELDS)
        hits = body["hits"]
        if not isinstance(hits, Sequence) or isinstance(hits, (str, bytes)):
            raise fail("hits must be a JSON array", where="hits")
        version = _text(body["contract_version"], where="contract_version")
        if version != CONTRACT_VERSION:
            raise fail(
                "the result declares a different contract version",
                contract_version=version,
                expected=CONTRACT_VERSION,
            )
        return cls(
            request_id=_text(body["request_id"], where="request_id"),
            search_session_id=_text(body["search_session_id"], where="search_session_id"),
            profile_id=_optional_text(body["profile_id"], where="profile_id"),
            image_generation_id=_optional_text(
                body["image_generation_id"], where="image_generation_id"
            ),
            space_id=_optional_text(body["space_id"], where="space_id"),
            dimension=_optional_integer(body["dimension"], where="dimension", minimum=1),
            catalog_filter_revision=_integer(
                body["catalog_filter_revision"], where="catalog_filter_revision"
            ),
            suggestion_revision=_integer(
                body["suggestion_revision"], where="suggestion_revision"
            ),
            fusion_config=_object(body["fusion_config"], where="fusion_config"),
            query=None
            if body["query"] is None
            else _object(body["query"], where="query"),
            filters=_object(body["filters"], where="filters"),
            hits=tuple(SearchHit.from_envelope(row) for row in hits),
            page_size=_integer(body["page_size"], where="page_size", minimum=1),
            page_offset=_integer(body["page_offset"], where="page_offset"),
            next_cursor=_optional_text(body["next_cursor"], where="next_cursor"),
            has_more=_boolean(body["has_more"], where="has_more"),
            total_candidates=_integer(body["total_candidates"], where="total_candidates"),
            candidates_truncated=_boolean(
                body["candidates_truncated"], where="candidates_truncated"
            ),
            channels=_object(body["channels"], where="channels"),
            pending_semantic=None
            if body["pending_semantic"] is None
            else _object(body["pending_semantic"], where="pending_semantic"),
            search_projection=_object(body["search_projection"], where="search_projection"),
            state=_enum(body["state"], RESULT_STATES, where="state"),
            contract_version=version,
        )

    def to_envelope(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "contract_version": self.contract_version,
            "request_id": str(self.request_id),
            "search_session_id": str(self.search_session_id),
            "profile_id": self.profile_id,
            "image_generation_id": self.image_generation_id,
            "space_id": self.space_id,
            "dimension": self.dimension,
            "catalog_filter_revision": int(self.catalog_filter_revision),
            "suggestion_revision": int(self.suggestion_revision),
            "fusion_config": dict(self.fusion_config),
            "query": None if self.query is None else dict(self.query),
            "filters": dict(self.filters),
            "hits": [hit.to_envelope() for hit in self.hits],
            "page_size": int(self.page_size),
            "page_offset": int(self.page_offset),
            "next_cursor": self.next_cursor,
            "has_more": bool(self.has_more),
            "total_candidates": int(self.total_candidates),
            "candidates_truncated": bool(self.candidates_truncated),
            "channels": dict(self.channels),
            "pending_semantic": None
            if self.pending_semantic is None
            else dict(self.pending_semantic),
            "search_projection": dict(self.search_projection),
            "state": str(self.state),
        }
        return payload


__all__ = [
    "AVAILABILITY_VALUES",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "PLACE_STATE_VALUES",
    "RESULT_STATES",
    "SearchHit",
    "SearchRequest",
    "SearchResult",
    "fail",
]
