"""Read-model queries for the business API, kept out of the route handlers.

Keyset paging with bounded chunks is the rule everywhere here: a request never materialises
the library in memory, never holds an open cursor across a commit, and never returns a row
twice or skips one between pages (architecture §9.5 for search, applied here to assets).
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from photo_search.api.errors import INVALID_CURSOR, VALIDATION_ERROR, ApiError
from photo_search.catalog import store
from photo_search.catalog.errors import (
    ASSET_NOT_FOUND,
    CATALOG_FILTER_REVISION_STALE,
    JOB_NOT_FOUND,
    PLACE_SUGGESTION_REVISION_STALE,
)
from photo_search.catalog.video import photo_asset_sql
from photo_search.media.cache import MediaCache
from photo_search.media.contract import PREVIEW_SPEC, THUMBNAIL_SPEC
from photo_search.media.status import asset_media_states
from photo_search.persons.collection_sql import collection_condition
from photo_search.places import revision as revision_module
from photo_search.places.filter import place_condition
from photo_search.search import documents as search_documents
from photo_search.search import query as search_query
from photo_search.taken_time import contract as time_contract
from photo_search.taken_time import read as time_read

#: Opaque cursor prefix of the default ``asset_id`` ordering (P1-04, frozen).
CURSOR_PREFIX = "v1"
#: Opaque cursor prefix of the ``time_desc`` ordering. The two orderings deliberately use
#: different token spaces, so a cursor minted by one ordering is rejected by the other
#: (``INVALID_CURSOR``) instead of silently resuming at a meaningless position.
TIME_CURSOR_PREFIX = "t1"
#: Opaque cursor prefix of the ``capture_desc`` ordering. It is a third, independent token
#: space: a ``v1``/``t1`` cursor must never be reinterpreted as a capture position, and vice
#: versa (ADR-0011 §5.2).
CAPTURE_CURSOR_PREFIX = "c1"
#: ``order`` values the asset listing accepts. ``asset_id`` is the P1-04 default.
ORDER_ASSET_ID = "asset_id"
ORDER_TIME_DESC = "time_desc"
#: ADR-0011 §5.1: ``capture_desc`` browses by the *capture* time (the civil key), with the
#: rows that have no civil key collected at the tail.
ORDER_CAPTURE_DESC = "capture_desc"
ORDERS = (ORDER_ASSET_ID, ORDER_TIME_DESC, ORDER_CAPTURE_DESC)
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
#: How many asset rows one chunk examines; keeps memory bounded while still letting a
#: physical-cache filter (``has_thumbnail``) fill a page in a few round trips.
ASSET_CHUNK_SIZE = 128

_LOCATION_SQL = """
SELECT l.asset_id, l.location_id, l.source_id, l.relative_path, l.availability,
       l.size, l.mtime_ns, s.display_name
  FROM file_locations AS l
  JOIN source_roots AS s ON s.source_id = l.source_id
 WHERE l.asset_id IN ({placeholders})
 ORDER BY l.location_id
"""


def _encode_cursor(prefix: str, values: tuple[int, ...]) -> str:
    payload = ":".join((str(prefix), *(str(int(value)) for value in values)))
    return base64.urlsafe_b64encode(payload.encode("ascii")).decode("ascii").rstrip("=")


def _decode_cursor(text: str | None, *, prefix: str, arity: int) -> list[int] | None:
    """Decode one opaque cursor; ``None`` means "start at the first page"."""
    if text is None or str(text).strip() == "":
        return None
    raw = str(text).strip()
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError) as error:
        raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid") from error
    parts = decoded.split(":")
    if len(parts) != arity + 1 or parts[0] != prefix:
        # A cursor minted by the other ordering (or by another version) is never
        # reinterpreted; the caller has to restart that ordering explicitly.
        raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid")
    numbers: list[int] = []
    for part in parts[1:]:
        try:
            number = int(part)
        except ValueError as error:
            raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid") from error
        if number < 0:
            raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid")
        numbers.append(number)
    return numbers


def encode_cursor(value: int) -> str:
    """Opaque keyset cursor holding the last examined ``asset_id``."""
    return _encode_cursor(CURSOR_PREFIX, (int(value),))


def decode_cursor(text: str | None) -> int:
    """Decode an ``asset_id``-ordering cursor, rejecting anything this build did not make."""
    numbers = _decode_cursor(text, prefix=CURSOR_PREFIX, arity=1)
    return 0 if numbers is None else numbers[0]


def encode_time_cursor(created_at: int, asset_id: int) -> str:
    """Opaque keyset cursor holding the last examined ``(created_at, asset_id)`` pair."""
    return _encode_cursor(TIME_CURSOR_PREFIX, (int(created_at), int(asset_id)))


def decode_time_cursor(text: str | None) -> tuple[int, int] | None:
    """Decode a ``time_desc`` cursor; ``None`` means "start at the newest asset"."""
    numbers = _decode_cursor(text, prefix=TIME_CURSOR_PREFIX, arity=2)
    return None if numbers is None else (numbers[0], numbers[1])


def encode_capture_cursor(known: int, sort_key: int, asset_id: int, revision: int) -> str:
    """Opaque ``capture_desc`` cursor: ``[known, sort_key_or_0, asset_id, revision]``.

    ADR-0011 §5.2: the fourth element *is* the ``catalog_filter_revision`` the cursor was
    minted under, so there is deliberately no second revision field that could disagree with
    it. ``known`` distinguishes the ordered bucket (``1``, ``sort_key`` is the civil key) from
    the tail bucket (``0``, whose sort element is always exactly ``0``).
    """
    return _encode_cursor(
        CAPTURE_CURSOR_PREFIX,
        (int(known), int(sort_key), int(asset_id), int(revision)),
    )


def decode_capture_cursor(text: str | None) -> tuple[int, int, int, int] | None:
    """Decode a ``capture_desc`` cursor, refusing every shape ADR-0011 §5.2 forbids."""
    numbers = _decode_cursor(text, prefix=CAPTURE_CURSOR_PREFIX, arity=4)
    if numbers is None:
        return None
    known, sort_key, asset_id, revision = numbers
    if known not in (0, 1):
        raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid")
    if known == 0 and sort_key != 0:
        # The tail bucket writes 0 in the sort element; it is not a free-form value.
        raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid")
    if asset_id < 1:
        raise ApiError(INVALID_CURSOR, "the pagination cursor is not valid")
    return (known, sort_key, asset_id, revision)


def clamp_limit(limit: int | None, *, default: int = DEFAULT_PAGE_LIMIT) -> int:
    """Apply the documented bound: an over-large limit is clamped, never rejected."""
    if limit is None:
        return int(default)
    if int(limit) < 1:
        raise ApiError(VALIDATION_ERROR, "limit must be at least 1", status=422)
    return min(int(limit), MAX_PAGE_LIMIT)


def locations_by_asset(catalog: Any, asset_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Every location of a bounded set of assets, grouped by asset."""
    if not asset_ids:
        return {}
    placeholders = ", ".join("?" for _ in asset_ids)
    rows = catalog.read_all(_LOCATION_SQL.format(placeholders=placeholders), tuple(asset_ids))
    grouped: dict[int, list[dict[str, Any]]] = {int(value): [] for value in asset_ids}
    for row in rows:
        grouped[int(row["asset_id"])].append(
            {
                "location_id": int(row["location_id"]),
                "source_id": int(row["source_id"]),
                "source_display_name": str(row["display_name"]),
                "relative_path": str(row["relative_path"]),
                "availability": str(row["availability"]),
                "size": int(row["size"]),
                "mtime_ns": int(row["mtime_ns"]),
                "asset_bound": row["asset_id"] is not None,
            }
        )
    return grouped


def _collection_condition(collection_id: int) -> tuple[str, tuple[int]]:
    """Compatibility wrapper for the shared collection membership predicate."""
    return collection_condition(collection_id)


def summarize_asset(
    cache: MediaCache,
    *,
    asset_id: int,
    sha256: str,
    byte_size: int,
    created_at: int,
    locations: list[dict[str, Any]],
) -> dict[str, Any]:
    """One list row: identity, position counts, availability and artifact readiness."""
    present = [item for item in locations if item["availability"] == "PRESENT"]
    current = present[0] if present else None
    return {
        "asset_id": int(asset_id),
        "sha256": str(sha256),
        "byte_size": int(byte_size),
        "created_at": int(created_at),
        "location_count": len(locations),
        "availability": "present" if present else "missing",
        "thumbnail_ready": _published(cache, THUMBNAIL_SPEC, sha256),
        "preview_ready": _published(cache, PREVIEW_SPEC, sha256),
        "current_location": (
            None
            if current is None
            else {"source_id": current["source_id"], "relative_path": current["relative_path"]}
        ),
    }


def _published(cache: MediaCache, spec: Any, sha256: str) -> bool:
    return cache.is_published(cache.artifact_path(spec=spec, sha256=sha256))


def prepare_asset_page(catalog: Any, **parameters: Any) -> dict[str, Any] | None:
    """Validate the entire request before bounded FTS catch-up on a writable owner.

    The HTTP path runs this on a separately owned projection connection, then reads
    a fresh RO snapshot. Cursor/revision validation is repeated in that snapshot.
    """
    return _asset_page(catalog, None, _prepare_only=True, **parameters)


def read_asset_page(catalog: Any, cache: MediaCache, *,
                    projection: dict[str, Any] | None = None, **parameters: Any) -> dict[str, Any]:
    """Publish rows, counts and cursor from one snapshot; never writes FTS."""
    with catalog.read_snapshot():
        return read_asset_page_in_snapshot(catalog, cache, projection=projection, **parameters)


def read_asset_page_in_snapshot(catalog: Any, cache: MediaCache, *,
                                projection: dict[str, Any] | None = None,
                                **parameters: Any) -> dict[str, Any]:
    """Extend a caller's read snapshot through album identity, rows and face facts.

    This entry never opens a transaction or prepares FTS. Its caller must already
    own a Catalog.read_snapshot; a write transaction is deliberately insufficient.
    """
    if not getattr(catalog, "_in_read_snapshot", False):
        raise RuntimeError("asset reads require an existing catalog read snapshot")
    return _asset_page(catalog, cache, _projection=projection, **parameters)


def asset_page(catalog: Any, cache: MediaCache, **parameters: Any) -> dict[str, Any]:
    """Synchronous service entry; projection transactions precede the page snapshot."""
    projection = prepare_asset_page(catalog, **parameters) if parameters.get("text") is not None else None
    return read_asset_page(catalog, cache, projection=projection, **parameters)


def _asset_page(
    catalog: Any,
    cache: MediaCache,
    *,
    limit: int,
    cursor: str | None = None,
    source_id: int | None = None,
    availability: str = "any",
    has_thumbnail: str = "any",
    order: str = ORDER_ASSET_ID,
    place_ids: list[int] | None = None,
    include_descendants: bool = True,
    place_state: str = "any",
    suggestion_revision: int | None = None,
    text: str | None = None,
    capture_from: str | None = None,
    capture_to_exclusive: str | None = None,
    person_ids: tuple[int, ...] = (),
    collection_id: int | None = None,
    _prepare_only: bool = False,
    _projection: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """One bounded page of assets in a stable, caller-selected order.

    ``order=asset_id`` is the frozen P1-04 behaviour (same SQL, same cursor, same rows).
    ``order=time_desc`` walks ``(created_at DESC, asset_id DESC)``, where ``created_at`` is
    the *catalog insertion* time, not an EXIF capture time. Both orderings keep their own
    cursor token space, so a cursor never leaks across orderings.

    The P2-03A place filter is an ADD condition on the same page: it never changes the
    cursor space, and the authoritative rows still come from SQLite.

    ``text`` is the P2-04 add-on: a bound FTS5 MATCH over the derived search document
    (file names of PRESENT locations plus the *effective* place). It is another AND
    condition on the same page, so the cursor space and every authoritative place filter
    stay exactly as they were. Before the match runs, the projection is caught up in
    bounded batches, and the response states how much lag is left.

    ``order=capture_desc`` and the optional ``capture_from``/``capture_to_exclusive`` pair
    are the P4-00B add-on of ADR-0011. The range is one more authoritative AND condition -
    it only ever matches a ``label = capture`` row, and it is satisfied by no row at all
    while the library's ``taken_time_rules_version`` is not this build's. The ordering keeps
    its own ``c1`` cursor space and binds the one existing ``catalog_filter_revision``: a
    moved revision refuses the continuation with the established 409 code instead of
    publishing a page that mixes two catalog versions.
    """
    if availability not in ("present", "missing", "any"):
        raise ApiError(
            VALIDATION_ERROR, "availability must be present, missing or any", status=422
        )
    if has_thumbnail not in ("true", "false", "any"):
        raise ApiError(
            VALIDATION_ERROR, "has_thumbnail must be true, false or any", status=422
        )
    if order not in ORDERS:
        raise ApiError(
            VALIDATION_ERROR,
            "order must be asset_id, time_desc or capture_desc",
            status=422,
            details={"order": str(order)},
        )
    # ADR-0011 §6.2/§6.3: both bounds are parsed once, up front, so a malformed bound, a
    # timezone suffix or a non-ascending interval is the frozen 422 and nothing is written.
    capture = time_contract.parse_capture_range(capture_from, capture_to_exclusive)
    # The one filter revision this page is computed under: it is read once, echoed once and -
    # for ``capture_desc`` - bound into the cursor, so the echo and the cursor can never
    # disagree about which version answered the page.
    filter_revision = int(revision_module.read_filter_revision(catalog.connection))
    capture_sql, capture_parameters = time_read.capture_predicate(
        catalog.connection, capture
    )
    # An unusable text query is refused before anything is written (structured 422).
    text_match = None if text is None else search_query.build_match(text)
    text_sql: str | None = None
    text_parameters: list[Any] = []
    search_block: dict[str, Any] | None = None
    place_sql, place_parameters = place_condition(
        catalog.connection,
        place_ids=place_ids,
        include_descendants=include_descendants,
        place_state=place_state,
    )
    if person_ids:
        from photo_search.search.retrieval import SearchScope, person_condition

        person_sql, person_parameters = person_condition(SearchScope.build(person_ids=person_ids))
        place_sql = f"({place_sql}) AND ({person_sql})"
        place_parameters = [*place_parameters, *person_parameters]
    if collection_id is not None:
        # Keep the collection filter as one indexed, set-based predicate. Stable member
        # anchors are recursively resolved at read time so merge/undo needs no collection
        # side effects and large collections never cross the ordinary 100-person search cap.
        collection_sql, collection_parameters = _collection_condition(collection_id)
        place_sql = f"({place_sql}) AND ({collection_sql})"
        place_parameters = [*place_parameters, *collection_parameters]
    # ADR-0005 §10: the ``suggested`` state has its own revision. A continuation (or a page)
    # that was computed under a different visible candidate set must be re-queried instead of
    # silently mixing two versions of the answer.
    current_suggestion_revision = revision_module.read_suggestion_revision(catalog.connection)
    if (
        str(place_state or "any") == "suggested"
        and suggestion_revision is not None
        and int(suggestion_revision) != int(current_suggestion_revision)
    ):
        raise ApiError(
            PLACE_SUGGESTION_REVISION_STALE,
            "the visible suggestion set changed since this page was requested",
            status=409,
            details={
                "expected_revision": int(suggestion_revision),
                "current_revision": int(current_suggestion_revision),
            },
        )
    start: int | tuple[int, int] | None
    if order == ORDER_CAPTURE_DESC:
        start = decode_capture_cursor(cursor)
        if start is not None and int(start[3]) != filter_revision:
            raise ApiError(
                CATALOG_FILTER_REVISION_STALE,
                "the capture ordering moved since this page was requested",
                status=409,
                details={
                    "reason": "CATALOG_FILTER_REVISION_CHANGED",
                    "current_catalog_filter_revision": int(filter_revision),
                },
            )
    elif order == ORDER_TIME_DESC:
        start = decode_time_cursor(cursor)
    else:
        start = decode_cursor(cursor)
    if _prepare_only:
        # All filter/cursor validation happens before the first projection write.
        return None if text_match is None else search_documents.catch_up(catalog)
    if text_match is not None:
        if _projection is None:
            raise RuntimeError("text projection must be prepared before a read-only page")
        text_sql, text_parameters = search_query.text_condition(text_match)
        search_block = search_query.search_summary(
            catalog.connection, text_match, catch_up=_projection
        )
    page_limit = int(limit)
    chunk_size = max(ASSET_CHUNK_SIZE, page_limit)
    matched: list[dict[str, Any]] = []
    examined_last: Any = start
    exhausted = False
    while len(matched) < page_limit:
        rows = _asset_chunk(
            catalog,
            after=examined_last,
            size=chunk_size,
            source_id=source_id,
            availability=availability,
            order=order,
            place_sql=None if place_sql == "1" else place_sql,
            place_parameters=place_parameters,
            text_sql=text_sql,
            text_parameters=text_parameters,
            capture_sql=None if capture_sql == "1" else capture_sql,
            capture_parameters=capture_parameters,
            prefer_member_scan=bool(person_ids) or collection_id is not None,
        )
        if not rows:
            exhausted = True
            break
        grouped = locations_by_asset(catalog, [int(row["asset_id"]) for row in rows])
        for row in rows:
            examined_last = _cursor_position(row, order, revision=filter_revision)
            summary = summarize_asset(
                cache,
                asset_id=int(row["asset_id"]),
                sha256=str(row["sha256"]),
                byte_size=int(row["byte_size"]),
                created_at=int(row["created_at"]),
                locations=grouped.get(int(row["asset_id"]), []),
            )
            if has_thumbnail == "true" and not summary["thumbnail_ready"]:
                continue
            if has_thumbnail == "false" and summary["thumbnail_ready"]:
                continue
            matched.append(summary)
            if len(matched) >= page_limit:
                break
        else:
            if len(rows) < chunk_size:
                exhausted = True
                break
    has_more = not exhausted
    payload: dict[str, Any] = {
        "assets": matched,
        "limit": page_limit,
        "next_cursor": (
            _encode_position(examined_last, order, revision=filter_revision)
            if has_more
            else None
        ),
        "has_more": has_more,
        "availability": availability,
        "has_thumbnail": has_thumbnail,
        "source_id": None if source_id is None else int(source_id),
        "order": order,
        "place_ids": [int(value) for value in (place_ids or ())],
        "include_descendants": bool(include_descendants),
        "place_state": str(place_state or "any"),
        "catalog_filter_revision": filter_revision,
        "suggestion_revision": int(current_suggestion_revision),
        "search": search_block,
        # ADR-0011 §8.1: both capture keys are always present, and the honesty block only
        # accompanies a page that really carried a time condition.
        **time_contract.capture_echo(capture),
    }
    if capture is not None:
        payload["taken_time"] = time_read.state_block(catalog.connection)
    return payload


def _cursor_position(row: Any, order: str, *, revision: int) -> Any:
    """The keyset position of one examined row, in the requested ordering."""
    if order == ORDER_TIME_DESC:
        return (int(row["created_at"]), int(row["asset_id"]))
    if order == ORDER_CAPTURE_DESC:
        sort_key = row["capture_sort"]
        if sort_key is None:
            # The tail bucket writes exactly 0 in the sort element (ADR-0011 §5.2).
            return (0, 0, int(row["asset_id"]), int(revision))
        return (1, int(sort_key), int(row["asset_id"]), int(revision))
    return int(row["asset_id"])


def _encode_position(position: Any, order: str, *, revision: int) -> str | None:
    if position is None:  # pragma: no cover - defensive: has_more implies a position
        return None
    if order == ORDER_TIME_DESC:
        created_at, asset_id = position
        return encode_time_cursor(created_at, asset_id)
    if order == ORDER_CAPTURE_DESC:
        known, sort_key, asset_id, bound_revision = position
        return encode_capture_cursor(known, sort_key, asset_id, bound_revision)
    return encode_cursor(position)


def _asset_chunk(
    catalog: Any,
    *,
    after: Any,
    size: int,
    source_id: int | None,
    availability: str,
    order: str = ORDER_ASSET_ID,
    place_sql: str | None = None,
    place_parameters: list[Any] | None = None,
    text_sql: str | None = None,
    text_parameters: list[Any] | None = None,
    capture_sql: str | None = None,
    capture_parameters: list[Any] | None = None,
    prefer_member_scan: bool = False,
) -> list[Any]:
    conditions: list[str] = []
    parameters: list[Any] = []
    join = ""
    selection = "a.asset_id, a.sha256, a.byte_size, a.created_at"
    if order == ORDER_CAPTURE_DESC and prefer_member_scan:
        # Sparse person/collection membership is an indexed candidate set. Let
        # SQLite visit those assets before sorting; scanning the global time
        # index can otherwise inspect the whole library for a tiny album.
        join = " LEFT JOIN asset_metadata AS m ON m.asset_id = a.asset_id"
        selection += ", m.taken_at_sort AS capture_sort"
        if after is not None:
            known, sort_key, asset_id, _bound_revision = after
            if int(known) == 1:
                conditions.append(
                    "((m.taken_at_sort IS NOT NULL AND (m.taken_at_sort < ? OR "
                    "(m.taken_at_sort = ? AND a.asset_id < ?))) OR m.taken_at_sort IS NULL)"
                )
                parameters.extend([int(sort_key), int(sort_key), int(asset_id)])
            else:
                conditions.append("(m.taken_at_sort IS NULL AND a.asset_id < ?)")
                parameters.append(int(asset_id))
        ordering = "(m.taken_at_sort IS NULL), m.taken_at_sort DESC, a.asset_id DESC"
    elif order == ORDER_CAPTURE_DESC:
        # Ordered reads are split per time_source below to use the existing
        # (time_source, taken_at_sort, asset_id) index without a global sort.
        ordering = ""
    elif order == ORDER_TIME_DESC:
        if after is not None:
            created_at, asset_id = after
            conditions.append(
                "(a.created_at < ? OR (a.created_at = ? AND a.asset_id < ?))"
            )
            parameters.extend([int(created_at), int(created_at), int(asset_id)])
        ordering = "a.created_at DESC, a.asset_id DESC"
    else:
        conditions.append("a.asset_id > ?")
        parameters.append(0 if after is None else int(after))
        ordering = "a.asset_id"
    photo_sql, photo_parameters = photo_asset_sql(source_id=source_id)
    conditions.append(photo_sql)
    parameters.extend(photo_parameters)
    if availability == "present":
        conditions.append(
            "EXISTS (SELECT 1 FROM file_locations AS l WHERE l.asset_id = a.asset_id "
            "AND l.availability = 'PRESENT')"
        )
    elif availability == "missing":
        conditions.append(
            "NOT EXISTS (SELECT 1 FROM file_locations AS l WHERE l.asset_id = a.asset_id "
            "AND l.availability = 'PRESENT')"
        )
    if place_sql:
        conditions.append(place_sql)
        parameters.extend(place_parameters or [])
    if text_sql:
        conditions.append(text_sql)
        parameters.extend(text_parameters or [])
    if capture_sql:
        conditions.append(capture_sql)
        parameters.extend(capture_parameters or [])
    if order == ORDER_CAPTURE_DESC and not prefer_member_scan:
        return _capture_chunk(catalog, conditions, parameters, after=after, size=size)
    parameters.append(int(size))
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return list(
        catalog.read_all(
            f"SELECT {selection} FROM assets AS a{join}"
            f"{where} ORDER BY {ordering} LIMIT ?",
            tuple(parameters),
        )
    )


def _capture_chunk(catalog: Any, conditions: list[str], parameters: list[Any], *,
                   after: Any, size: int) -> list[Any]:
    """Exact c1 ordering with at most three bounded source pages plus a tail.

    EXIF_ROOT is ordered too (unlike the timeline's capture-only bucket).
    Missing metadata and FILE_MTIME remain one asset-id-ordered NULL tail.
    """
    selection = "a.asset_id,a.sha256,a.byte_size,a.created_at,m.taken_at_sort AS capture_sort"
    candidates: list[Any] = []
    if after is None or int(after[0]) == 1:
        for source in ("EXIF_ORIGINAL", "EXIF_DIGITIZED", "EXIF_ROOT"):
            pieces = [*conditions, "m.time_source=?", "m.taken_at_sort IS NOT NULL"]
            values = [*parameters, source]
            if after is not None:
                pieces.append("(m.taken_at_sort,m.asset_id) < (?,?)")
                values.extend((int(after[1]), int(after[2])))
            candidates.extend(catalog.read_all(
                f"SELECT {selection} FROM asset_metadata AS m "
                "INDEXED BY idx_asset_metadata_capture "
                "CROSS JOIN assets AS a ON a.asset_id=m.asset_id "
                f"WHERE {' AND '.join(f'({p})' for p in pieces)} "
                "ORDER BY m.taken_at_sort DESC,m.asset_id DESC LIMIT ?",
                (*values, int(size)),
            ))
        candidates.sort(key=lambda row: (row["capture_sort"], row["asset_id"]), reverse=True)
        candidates = candidates[:size]
    remaining = size - len(candidates)
    if remaining:
        pieces = [*conditions, "m.taken_at_sort IS NULL"]
        values = list(parameters)
        if after is not None and int(after[0]) == 0:
            pieces.append("a.asset_id < ?")
            values.append(int(after[2]))
        candidates.extend(catalog.read_all(
            f"SELECT {selection} FROM assets AS a "
            "LEFT JOIN asset_metadata AS m ON m.asset_id=a.asset_id "
            f"WHERE {' AND '.join(f'({p})' for p in pieces)} "
            "ORDER BY a.asset_id DESC LIMIT ?", (*values, remaining),
        ))
    return candidates


def source_list(catalog: Any, *, manifest_path: str | None, device_policy: str,
                data_root: Any) -> dict[str, Any]:
    """Source counts and progress from one worker-owned read snapshot."""
    from photo_search.refresh import progress

    with catalog.read_snapshot():
        binding = progress.served_binding(catalog, manifest_path=manifest_path,
                                          device_policy=device_policy)
        available = progress.enrichment_availability(catalog, data_root=data_root)
        sources = []
        for row in store.list_source_rows(catalog.connection):
            source_id = int(row["source_id"])
            item = store.row_dict(row, store.SOURCE_FIELDS)
            item["counts"] = store.catalog_counts(catalog.connection, source_id=source_id)
            item["progress"] = progress.source_progress(catalog, source_id=source_id,
                                                        binding=binding, available=available)
            sources.append(item)
        return {"sources": sources}


def asset_detail(
    catalog: Any, cache: MediaCache, asset_id: int, *, redactor: Any = None
) -> dict[str, Any]:
    """The asset row, every registered position and the state of each artifact kind."""
    row = catalog.read_one(
        "SELECT asset_id, sha256, byte_size, width, height, format, created_at, state "
        "FROM assets WHERE asset_id = ?",
        (int(asset_id),),
    )
    if row is None:
        raise ApiError(
            ASSET_NOT_FOUND,
            f"asset {int(asset_id)} does not exist",
            details={"asset_id": int(asset_id)},
        )
    locations = locations_by_asset(catalog, [int(asset_id)]).get(int(asset_id), [])
    states = asset_media_states(catalog, cache, asset_id=int(asset_id), sha256=str(row["sha256"]))
    return {
        "asset_id": int(row["asset_id"]),
        "sha256": str(row["sha256"]),
        "byte_size": int(row["byte_size"]),
        "width": row["width"],
        "height": row["height"],
        "format": row["format"],
        "created_at": int(row["created_at"]),
        "state": str(row["state"]),
        "availability": (
            "present"
            if any(item["availability"] == "PRESENT" for item in locations)
            else "missing"
        ),
        "location_count": len(locations),
        "locations": locations,
        # P4-00B (ADR-0011 §8.1 item 3): the closed per-asset taken-time object, or ``null``
        # when this build has no trustworthy row for the asset. ``taken_at_raw`` never leaves
        # the catalog.
        "taken_time": time_read.asset_view(catalog.connection, int(asset_id)),
        "media": {
            kind: {
                "kind": state["kind"],
                "processor_id": state["processor_id"],
                "long_edge": state["long_edge"],
                "state": state["state"],
                "code": state["code"],
                "message": _record_message(state["record"], redactor),
            }
            for kind, state in states.items()
        },
    }


OCR_TEXT_MAX_BYTES = 128 * 1024
_OCR_POINTER_FIELDS = (
    "ocr_document_id", "text_revision", "processor_id", "input_revision", "artifact_id",
)


def _ocr_text_snapshot(catalog: Any, asset_id: int) -> dict[str, Any]:
    """Copy one asset's current OCR pointer and published artifact metadata."""
    with catalog.read_snapshot():
        asset = catalog.read_one(
            "SELECT sha256 FROM assets WHERE asset_id = ?", (int(asset_id),)
        )
        if asset is None:
            raise ApiError(
                ASSET_NOT_FOUND, f"asset {int(asset_id)} does not exist",
                details={"asset_id": int(asset_id)},
            )
        pointer = catalog.read_one(
            "SELECT ocr_document_id, text_revision, processor_id, input_revision, artifact_id, "
            "region_count FROM asset_ocr_text WHERE asset_id = ?", (int(asset_id),)
        )
        if pointer is None or str(pointer["input_revision"]) != str(asset["sha256"]):
            return {"state": "none"}
        value = {key: pointer[key] for key in _OCR_POINTER_FIELDS}
        value["region_count"] = int(pointer["region_count"])
        value["sha256"] = str(asset["sha256"])
        if value["region_count"] == 0:
            return {"state": "none"}
        artifact = catalog.read_one(
            "SELECT artifact_id, processor_id, input_revision, artifact_type, "
            "artifact_revision, media_type, checksum_sha256, byte_size, storage_uri, "
            "lifecycle_state FROM artifacts WHERE artifact_id = ?", (str(value["artifact_id"]),)
        )
        if artifact is None:
            return {"state": "unavailable"}
        value["artifact"] = dict(artifact)
        return {"state": "candidate", "value": value}


def _ocr_text_recheck(catalog: Any, asset_id: int, expected: dict[str, Any]) -> str:
    """Recheck document identity and publication after file I/O."""
    with catalog.read_snapshot():
        row = catalog.read_one(
            "SELECT a.sha256, t.ocr_document_id, t.text_revision, t.processor_id, "
            "t.input_revision, t.artifact_id, t.region_count, r.lifecycle_state "
            "FROM assets AS a JOIN asset_ocr_text AS t ON t.asset_id = a.asset_id "
            "LEFT JOIN artifacts AS r ON r.artifact_id = t.artifact_id "
            "WHERE a.asset_id = ?", (int(asset_id),)
        )
        if row is None or str(row["sha256"]) != str(expected["sha256"]):
            return "none"
        if any(str(row[field]) != str(expected[field]) for field in _OCR_POINTER_FIELDS):
            return "none"
        if int(row["region_count"]) != int(expected["region_count"]):
            return "none"
        return "available" if row["lifecycle_state"] == "PUBLISHED" else "unavailable"


def _record_message(record: Any, redactor: Any) -> str | None:
    """A recorded failure message, with any registered root replaced by a placeholder."""
    if record is None:
        return None
    message = record.get("message")
    if message is None:
        return None
    return redactor.text(message) if redactor is not None else str(message)


def media_state(catalog: Any, cache: MediaCache, asset_id: int, kind: str) -> dict[str, Any]:
    """The recorded per-kind state, used to explain a refused media request."""
    row = catalog.read_one("SELECT sha256 FROM assets WHERE asset_id = ?", (int(asset_id),))
    if row is None:
        raise ApiError(
            ASSET_NOT_FOUND,
            f"asset {int(asset_id)} does not exist",
            details={"asset_id": int(asset_id)},
        )
    states = asset_media_states(
        catalog, cache, asset_id=int(asset_id), sha256=str(row["sha256"]), kinds=(kind,)
    )
    return states[kind]


_JOB_COLUMNS = (
    "job_id",
    "subject_type",
    "subject_id",
    "task_type",
    "processor_id",
    "state",
    "priority",
    "attempts",
    "max_attempts",
    "progress",
    "run_after",
    "last_error_code",
    "last_error_message",
    "created_at",
    "updated_at",
    "requested_device",
    "resolved_device",
    "runtime_provider",
    "precision",
    "runtime_capability_revision",
    "blocking_reason",
)

#: The lease token and the worker identity are deliberately not part of any projection.
_JOB_SELECT = (
    ", ".join(f"j.{column}" for column in _JOB_COLUMNS)
    + """
       , s.scan_id AS scan_scan_id, s.source_id AS scan_source_id, s.state AS scan_state,
         s.started_at AS scan_started_at, s.finished_at AS scan_finished_at, s.error AS scan_error
  FROM jobs AS j
  LEFT JOIN scan_runs AS s
    ON j.subject_type = ? AND CAST(s.scan_id AS TEXT) = j.subject_id
"""
)


def job_record(row: Any, *, redactor: Any = None) -> dict[str, Any]:
    """One job row without the lease token or the worker identity."""
    scan_id = row["scan_scan_id"]
    payload: dict[str, Any] = {
        "job_id": int(row["job_id"]),
        "subject_type": str(row["subject_type"]),
        "subject_id": str(row["subject_id"]),
        "task_type": str(row["task_type"]),
        "processor_id": str(row["processor_id"]),
        "state": str(row["state"]),
        "priority": int(row["priority"]),
        "attempts": int(row["attempts"]),
        "max_attempts": int(row["max_attempts"]),
        "progress": float(row["progress"]),
        "run_after": int(row["run_after"]),
        "last_error_code": row["last_error_code"],
        "last_error_message": row["last_error_message"],
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
        "requested_device": str(row["requested_device"]),
        "resolved_device": row["resolved_device"],
        "runtime_provider": row["runtime_provider"],
        "precision": row["precision"],
        "runtime_capability_revision": row["runtime_capability_revision"],
        "blocking_reason": row["blocking_reason"],
        "scan": None,
    }
    if scan_id is not None:
        payload["scan"] = {
            "scan_id": int(scan_id),
            "source_id": int(row["scan_source_id"]),
            "state": str(row["scan_state"]),
            "started_at": row["scan_started_at"],
            "finished_at": row["scan_finished_at"],
            "error_code": _error_code(row["scan_error"]),
            "error_message": _error_message(row["scan_error"]),
        }
    if redactor is not None:
        payload["last_error_message"] = _redacted(redactor, payload["last_error_message"])
        if payload["scan"] is not None:
            payload["scan"]["error_message"] = _redacted(redactor, payload["scan"]["error_message"])
    return payload


def _redacted(redactor: Any, value: Any) -> Any:
    return None if value is None else redactor.text(value)


def _error_payload(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        payload = json.loads(str(raw))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _error_code(raw: Any) -> str | None:
    payload = _error_payload(raw)
    return None if payload is None else payload.get("code")


def _error_message(raw: Any) -> str | None:
    payload = _error_payload(raw)
    return None if payload is None else payload.get("message")


def job_page(
    catalog: Any,
    *,
    limit: int,
    cursor: str | None = None,
    state: str | None = None,
    task_type: str | None = None,
    source_id: int | None = None,
    redactor: Any = None,
) -> dict[str, Any]:
    """One bounded page of jobs in stable ``job_id`` order."""
    start = decode_cursor(cursor)
    conditions = ["j.job_id > ?"]
    parameters: list[Any] = [store.SCAN_SUBJECT_TYPE, start]
    if state:
        conditions.append("j.state = ?")
        parameters.append(str(state))
    if task_type:
        conditions.append("j.task_type = ?")
        parameters.append(str(task_type))
    if source_id is not None:
        conditions.append("s.source_id = ?")
        parameters.append(int(source_id))
    parameters.append(int(limit) + 1)
    rows = list(
        catalog.read_all(
            f"SELECT {_JOB_SELECT} WHERE {' AND '.join(conditions)} ORDER BY j.job_id LIMIT ?",
            tuple(parameters),
        )
    )
    has_more = len(rows) > int(limit)
    rows = rows[: int(limit)]
    return {
        "jobs": [job_record(row, redactor=redactor) for row in rows],
        "limit": int(limit),
        "next_cursor": encode_cursor(int(rows[-1]["job_id"])) if has_more and rows else None,
        "has_more": has_more,
    }


def job_detail(catalog: Any, job_id: int, *, redactor: Any = None) -> dict[str, Any]:
    """One job by id; the lease token never leaves the database."""
    row = catalog.read_one(
        f"SELECT {_JOB_SELECT} WHERE j.job_id = ?", (store.SCAN_SUBJECT_TYPE, int(job_id))
    )
    if row is None:
        raise ApiError(
            JOB_NOT_FOUND, f"job {int(job_id)} does not exist", details={"job_id": int(job_id)}
        )
    return job_record(row, redactor=redactor)
