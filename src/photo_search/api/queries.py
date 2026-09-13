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
from photo_search.catalog.errors import ASSET_NOT_FOUND, JOB_NOT_FOUND
from photo_search.media.cache import MediaCache
from photo_search.media.contract import PREVIEW_SPEC, THUMBNAIL_SPEC
from photo_search.media.status import asset_media_states

#: Opaque cursor prefix of the default ``asset_id`` ordering (P1-04, frozen).
CURSOR_PREFIX = "v1"
#: Opaque cursor prefix of the ``time_desc`` ordering. The two orderings deliberately use
#: different token spaces, so a cursor minted by one ordering is rejected by the other
#: (``INVALID_CURSOR``) instead of silently resuming at a meaningless position.
TIME_CURSOR_PREFIX = "t1"
#: ``order`` values the asset listing accepts. ``asset_id`` is the P1-04 default.
ORDER_ASSET_ID = "asset_id"
ORDER_TIME_DESC = "time_desc"
ORDERS = (ORDER_ASSET_ID, ORDER_TIME_DESC)
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


def asset_page(
    catalog: Any,
    cache: MediaCache,
    *,
    limit: int,
    cursor: str | None = None,
    source_id: int | None = None,
    availability: str = "any",
    has_thumbnail: str = "any",
    order: str = ORDER_ASSET_ID,
) -> dict[str, Any]:
    """One bounded page of assets in a stable, caller-selected order.

    ``order=asset_id`` is the frozen P1-04 behaviour (same SQL, same cursor, same rows).
    ``order=time_desc`` walks ``(created_at DESC, asset_id DESC)``, where ``created_at`` is
    the *catalog insertion* time, not an EXIF capture time. Both orderings keep their own
    cursor token space, so a cursor never leaks across orderings.
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
            "order must be asset_id or time_desc",
            status=422,
            details={"order": str(order)},
        )
    start: int | tuple[int, int] | None
    if order == ORDER_TIME_DESC:
        start = decode_time_cursor(cursor)
    else:
        start = decode_cursor(cursor)
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
        )
        if not rows:
            exhausted = True
            break
        grouped = locations_by_asset(catalog, [int(row["asset_id"]) for row in rows])
        for row in rows:
            examined_last = _cursor_position(row, order)
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
    return {
        "assets": matched,
        "limit": page_limit,
        "next_cursor": _encode_position(examined_last, order) if has_more else None,
        "has_more": has_more,
        "availability": availability,
        "has_thumbnail": has_thumbnail,
        "source_id": None if source_id is None else int(source_id),
        "order": order,
    }


def _cursor_position(row: Any, order: str) -> Any:
    """The keyset position of one examined row, in the requested ordering."""
    if order == ORDER_TIME_DESC:
        return (int(row["created_at"]), int(row["asset_id"]))
    return int(row["asset_id"])


def _encode_position(position: Any, order: str) -> str | None:
    if position is None:  # pragma: no cover - defensive: has_more implies a position
        return None
    if order == ORDER_TIME_DESC:
        created_at, asset_id = position
        return encode_time_cursor(created_at, asset_id)
    return encode_cursor(position)


def _asset_chunk(
    catalog: Any,
    *,
    after: Any,
    size: int,
    source_id: int | None,
    availability: str,
    order: str = ORDER_ASSET_ID,
) -> list[Any]:
    conditions: list[str] = []
    parameters: list[Any] = []
    if order == ORDER_TIME_DESC:
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
    if source_id is not None:
        conditions.append(
            "EXISTS (SELECT 1 FROM file_locations AS l WHERE l.asset_id = a.asset_id "
            "AND l.source_id = ?)"
        )
        parameters.append(int(source_id))
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
    parameters.append(int(size))
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return list(
        catalog.read_all(
            "SELECT a.asset_id, a.sha256, a.byte_size, a.created_at FROM assets AS a "
            f"{where} ORDER BY {ordering} LIMIT ?",
            tuple(parameters),
        )
    )


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
