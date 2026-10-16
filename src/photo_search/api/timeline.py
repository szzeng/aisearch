"""Read-only, scope-bound photo timeline queries.

The module composes the catalog's existing photo, person and collection predicates. A
timeline request is a short SQLite read snapshot: page rows, date summaries, and the one
shared catalog revision are never assembled from different catalog states.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import ntpath
from datetime import date, datetime
from typing import Any

from photo_search.api.errors import INVALID_CURSOR, VALIDATION_ERROR, ApiError
from photo_search.catalog.errors import CATALOG_FILTER_REVISION_STALE
from photo_search.catalog.video import photo_asset_sql
from photo_search.persons.collection_sql import collection_condition
from photo_search.persons.identity import resolve_canonical_person_id
from photo_search.places import revision
from photo_search.places import timeline as place_timeline
from photo_search.search.retrieval import SearchScope, person_condition
from photo_search.taken_time import contract as time_contract
from photo_search.taken_time import read as time_read

CURSOR_PREFIX = "tl1"
DEFAULT_LIMIT = 50
MAX_LIMIT = 60
CAPTURE_SOURCES = ("EXIF_ORIGINAL", "EXIF_DIGITIZED")
UNKNOWN_SOURCES = ("EXIF_ROOT", "FILE_MTIME")
BUCKETS = ("all", "capture", "unknown", "pending")
BASES = ("capture", "library")
GROUPS = ("day", "month", "year")
SCOPES = ("all", "source", "person", "collection", "place")


def _fail(message: str, *, field: str | None = None) -> ApiError:
    details = {} if field is None else {"field": field}
    return ApiError(VALIDATION_ERROR, message, status=422, details=details)


def _limit(value: int | None) -> int:
    if value is None:
        return DEFAULT_LIMIT
    if int(value) < 1:
        raise _fail("limit must be at least 1", field="limit")
    return min(int(value), MAX_LIMIT)


def _scope(catalog: Any, kind: str, source_id: int | None,
           person_id: int | None, collection_id: int | None,
           place_kind: str | None = None, place_key: str | None = None,
           data_root: Any = None) -> tuple[str, list[Any], dict[str, Any]]:
    if kind not in SCOPES:
        if kind == "place":
            raise ApiError("TIMELINE_SCOPE_DEFERRED", "地点时间轴由 P4-UI-PLACES-E 提供", status=501)
        raise _fail("unsupported timeline scope", field="kind")
    expected = {"source": source_id, "person": person_id, "collection": collection_id}
    for required_kind, value in expected.items():
        if kind == required_kind and (value is None or int(value) < 1):
            raise _fail(f"{required_kind}_id is required and must be positive", field=f"{required_kind}_id")
    if kind != "source" and source_id is not None:
        raise _fail("source_id is only valid for source scope", field="source_id")
    if kind != "person" and person_id is not None:
        raise _fail("person_id is only valid for person scope", field="person_id")
    if kind != "collection" and collection_id is not None:
        raise _fail("collection_id is only valid for collection scope", field="collection_id")
    if kind == "place" and (not place_kind or not place_key):
        raise _fail("place_kind and place_key are required for place scope", field="place_key")
    if kind != "place" and (place_kind is not None or place_key is not None):
        raise _fail("place_kind and place_key are only valid for place scope", field="place_key")

    photo_sql, photo_params = photo_asset_sql(
        source_id=int(source_id) if kind == "source" else None
    )
    clauses = ["a.state = 'ACTIVE'", photo_sql]
    params: list[Any] = list(photo_params)
    binding: dict[str, Any] = {"kind": kind, "source_id": None, "person_id": None,
                               "collection_id": None, "collection_instance": None,
                               "collection_revision": None}
    if kind == "source":
        row = catalog.connection.execute(
            "SELECT source_id FROM source_roots WHERE source_id = ?", (int(source_id),)
        ).fetchone()
        if row is None:
            raise ApiError("SOURCE_NOT_FOUND", "图库不存在", status=404)
        binding["source_id"] = int(source_id)
    elif kind == "person":
        canonical = resolve_canonical_person_id(catalog.connection, int(person_id))
        sql, values = person_condition(SearchScope.build(person_ids=(canonical,)))
        clauses.append(sql)
        params.extend(values)
        binding["person_id"] = int(canonical)
    elif kind == "collection":
        row = catalog.connection.execute(
            "SELECT instance_token, revision FROM person_collections WHERE collection_id = ?",
            (int(collection_id),),
        ).fetchone()
        if row is None:
            raise ApiError("PERSON_COLLECTION_NOT_FOUND", "人物合集不存在", status=404)
        sql, values = collection_condition(int(collection_id))
        clauses.append(sql)
        params.extend(values)
        binding.update({"collection_id": int(collection_id),
                        "collection_instance": str(row["instance_token"]),
                        "collection_revision": int(row["revision"])})
    elif kind == "place":
        place_sql, values, place_revisions = place_timeline.selected_members_sql(
            catalog, data_root, kind=str(place_kind), key=str(place_key)
        )
        clauses.append(place_sql)
        params.extend(values)
        binding.update({"place_kind": str(place_kind), "place_key": str(place_key)})
        binding["place_revisions"] = place_revisions
    return " AND ".join(f"({item})" for item in clauses), params, binding


def _cursor_scope(scope: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in scope.items() if key != "place_revisions"}


def _params(*, kind: str, scope: dict[str, Any], time_basis: str, group_by: str,
            bucket: str, year: int | None, month: int | None,
            rules_version: int | None) -> dict[str, Any]:
    return {"kind": kind, **_cursor_scope(scope), "time_basis": time_basis, "group_by": group_by,
            "bucket": bucket, "year": year, "month": month,
            "rules_version": rules_version,
            "calendar_timezone": "system-local"}


def _fingerprint(parameters: dict[str, Any]) -> str:
    raw = json.dumps(parameters, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def _encode_cursor(binding: str, revision_value: int, position: list[int]) -> str:
    raw = json.dumps([CURSOR_PREFIX, binding, int(revision_value), position], separators=(",", ":"), ensure_ascii=True)
    return base64.urlsafe_b64encode(raw.encode("ascii")).decode("ascii").rstrip("=")


def _decode_cursor(value: str | None, binding: str, revision_value: int,
                   *, allow_stale: bool = False) -> list[int] | None:
    if not value:
        return None
    try:
        if len(value) > 2048:
            raise ValueError
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("ascii")
        parsed = json.loads(raw)
        if not isinstance(parsed, list) or len(parsed) != 4 or parsed[:2] != [CURSOR_PREFIX, binding]:
            raise ValueError
        if not isinstance(parsed[2], int):
            raise ValueError
        if int(parsed[2]) != int(revision_value) and not allow_stale:
            raise ApiError(CATALOG_FILTER_REVISION_STALE,
                           "照片日期或成员已更新，请重新定位时间轴", status=409,
                           details={"current_catalog_filter_revision": int(revision_value)})
        position = parsed[3]
        if not isinstance(position, list) or len(position) != 3:
            raise ValueError
        rank, sort_value, asset_id = position
        if (not isinstance(rank, int) or rank not in (0, 1, 2)
                or not isinstance(sort_value, int) or sort_value < 0
                or not isinstance(asset_id, int) or asset_id < 1):
            raise ValueError
        return [rank, sort_value, asset_id]
    except (ValueError, TypeError, UnicodeError, binascii.Error) as error:
        raise ApiError(INVALID_CURSOR, "时间轴游标无效或与当前筛选不匹配", status=409) from error


def _date_parts(sort_value: int, group_by: str) -> tuple[str, str]:
    # ADR-0011 stores a civil key, not an instant. Normalize through its fixed-width wall time.
    value = time_contract.normalize_bound(int(sort_value))
    day = value[:10]
    return day, {"day": day, "month": day[:7], "year": day[:4]}[group_by]


def _library_day(created_at: int) -> str:
    row = datetime.fromtimestamp(int(created_at) / 1000).date()
    return row.isoformat()


def _where_for_bucket(where: str, params: list[Any], *, bucket: str,
                      time_basis: str, rules_match: bool, year: int | None,
                      month: int | None) -> tuple[str, list[Any]]:
    clauses = [where]
    values = list(params)
    if time_basis == "capture":
        if bucket == "capture":
            if rules_match:
                marks = ",".join("?" for _ in CAPTURE_SOURCES)
                clauses.append(f"m.time_source IN ({marks}) AND m.taken_at_sort IS NOT NULL")
                values.extend(CAPTURE_SOURCES)
            else:
                clauses.append("0")
            if year is not None:
                low = (date(int(year), 1, 1) - date(1, 1, 1)).days * 86_400_000
                high = low + (366 if int(year) % 4 == 0 and (int(year) % 100 != 0 or int(year) % 400 == 0) else 365) * 86_400_000
                clauses.append("m.taken_at_sort >= ? AND m.taken_at_sort < ?")
                values.extend([int(low), int(high)])
                if month is not None:
                    next_year, next_month = (int(year) + 1, 1) if int(month) == 12 else (int(year), int(month) + 1)
                    start = (date(int(year), int(month), 1) - date(1, 1, 1)).days * 86_400_000
                    end = ((date(next_year, next_month, 1) - date(1, 1, 1)).days * 86_400_000
                           if next_year <= 9999 else 315_537_897_600_000)
                    values[-2:] = [int(start), int(end)]
        elif bucket == "unknown":
            if rules_match:
                marks = ",".join("?" for _ in UNKNOWN_SOURCES)
                clauses.append(f"m.time_source IN ({marks})")
                values.extend(UNKNOWN_SOURCES)
            else:
                clauses.append("0")
        elif bucket == "pending":
            clauses.append("(m.asset_id IS NULL OR ? = 0)")
            values.append(1 if rules_match else 0)
        elif bucket not in ("all",):
            clauses.append("0")
    elif bucket != "all":
        raise _fail("bucket is only valid for capture time", field="bucket")
    if year is not None and time_basis == "library":
        clauses.append("strftime('%Y', a.created_at / 1000, 'unixepoch', 'localtime') = ?")
        values.append(f"{year:04d}")
        if month is not None:
            clauses.append("strftime('%m', a.created_at / 1000, 'unixepoch', 'localtime') = ?")
            values.append(f"{month:02d}")
    return " AND ".join(f"({clause})" for clause in clauses), values


def _validate(time_basis: str, group_by: str, bucket: str,
              year: int | None, month: int | None) -> None:
    if time_basis not in BASES:
        raise _fail("time_basis must be capture or library", field="time_basis")
    if group_by not in GROUPS:
        raise _fail("group_by must be day, month or year", field="group_by")
    if bucket not in BUCKETS:
        raise _fail("invalid timeline bucket", field="bucket")
    if year is not None and not 1 <= int(year) <= 9999:
        raise _fail("year is outside 0001..9999", field="year")
    if month is not None and (year is None or not 1 <= int(month) <= 12):
        raise _fail("month requires a year and must be 1..12", field="month")
    if time_basis == "library" and bucket != "all":
        raise _fail("date buckets are only available for capture time", field="bucket")
    if time_basis == "capture" and bucket in ("unknown", "pending") and (year is not None or month is not None):
        raise _fail("unknown and pending buckets cannot be filtered by year or month", field="year")


def _seek_position(time_basis: str, year: int | None, month: int | None) -> list[int] | None:
    if year is None:
        if month is not None:
            raise _fail("seek_month requires seek_year", field="seek_month")
        return None
    if not 1 <= int(year) <= 9999 or (month is not None and not 1 <= int(month) <= 12):
        raise _fail("invalid seek year or month", field="seek_year")
    if time_basis == "capture":
        if month is None:
            next_year, next_month = int(year) + 1, 1
        else:
            next_year, next_month = ((int(year) + 1, 1) if int(month) == 12 else (int(year), int(month) + 1))
        if next_year > 9999:
            end_key = 315_537_897_600_000
        else:
            end_key = (date(next_year, next_month, 1) - date(1, 1, 1)).days * 86_400_000
        # The next period's first millisecond is exclusive, including every ID at that key.
        return [0, int(end_key), 0]
    if month is None:
        next_year, next_month = int(year) + 1, 1
    else:
        next_year, next_month = ((int(year) + 1, 1) if int(month) == 12 else (int(year), int(month) + 1))
    if next_year > 9999:
        end_ms = 253_402_300_800_000
    else:
        end_ms = int(datetime(next_year, next_month, 1).astimezone().timestamp() * 1000)
    return [0, int(end_ms), 0]


def _timeline_rows(connection: Any, where: str, params: list[Any], *, time_basis: str,
                   bucket: str, rules_match: bool, after: list[int] | None,
                   inclusive: bool, size: int) -> list[dict[str, Any]]:
    """Seek each ordered bucket, then merge only the bounded capture-source candidates.

    The metadata index starts with time_source. Two equality seeks preserve the global
    capture order without sorting all capture rows across both sources. Unknown/pending
    buckets use the asset creation index and are visited only if earlier buckets run out.
    """
    if time_basis == "library":
        ranks = [0]
    elif not rules_match:
        ranks = [2] if bucket in ("all", "pending") else []
    else:
        ranks = [0, 1, 2] if bucket == "all" else [{"capture": 0, "unknown": 1, "pending": 2}[bucket]]
    result: list[dict[str, Any]] = []
    for rank in ranks:
        if after is not None and rank < after[0]:
            continue
        capture = time_basis == "capture" and rules_match and rank == 0
        sort_key = "m.taken_at_sort" if capture else "a.created_at"
        id_key = "m.asset_id" if capture else "a.asset_id"
        if capture:
            source = "asset_metadata AS m JOIN assets AS a ON a.asset_id=m.asset_id"
        else:
            source = "assets AS a"
            if time_basis == "capture":
                # Unknown rows require metadata. Keep assets as the outer ordered
                # scan, otherwise SQLite scans both metadata sources and sorts
                # every unknown row before returning this small page.
                join = "CROSS JOIN" if rules_match and rank == 1 else "LEFT JOIN"
                source += f" {join} asset_metadata AS m ON m.asset_id=a.asset_id"
        fields = (
            "m.taken_at_sort AS sort_value,m.time_source,m.time_confidence,m.timezone_offset"
            if time_basis == "capture" else
            "a.created_at AS sort_value,NULL AS time_source,NULL AS time_confidence,NULL AS timezone_offset"
        )
        remaining = size - len(result)
        candidates: list[dict[str, Any]] = []
        for capture_source in CAPTURE_SOURCES if capture else (None,):
            conditions, values = [where], list(params)
            if capture:
                conditions.append("m.time_source=? AND m.taken_at_sort IS NOT NULL")
                values.append(capture_source)
            elif time_basis == "capture" and rules_match:
                conditions.append("m.time_source IN ('EXIF_ROOT','FILE_MTIME')" if rank == 1
                                  else "m.asset_id IS NULL")
            if after is not None and rank == after[0]:
                operator = "<=" if inclusive else "<"
                conditions.append(f"({sort_key},{id_key}) {operator} (?,?)")
                values.extend(after[1:])
            sql = (
                f"SELECT a.asset_id,a.sha256,a.created_at,{fields},"
                f"{rank} AS timeline_rank,{sort_key} AS timeline_sort FROM {source} "
                f"WHERE {' AND '.join(f'({condition})' for condition in conditions)} "
                f"ORDER BY {sort_key} DESC,{id_key} DESC LIMIT ?"
            )
            candidates.extend(dict(row) for row in connection.execute(sql, (*values, remaining)))
        if capture:
            candidates.sort(key=lambda row: (row["timeline_sort"], row["asset_id"]), reverse=True)
        result.extend(candidates[:remaining])
        if len(result) == size:
            break
    if result:
        # Resolve the preferred file only for the selected page, not for every asset
        # considered by an ordered SQL scan. The PRESENT/lowest-location-id rule is unchanged.
        marks = ",".join("?" for _ in result)
        locations = {
            int(row["asset_id"]): row for row in connection.execute(
                "SELECT a.asset_id,location.relative_path,source.root_path FROM assets AS a "
                "LEFT JOIN file_locations AS location ON location.location_id=("
                "SELECT candidate.location_id FROM file_locations AS candidate "
                "WHERE candidate.asset_id=a.asset_id "
                "ORDER BY CASE candidate.availability WHEN 'PRESENT' THEN 0 ELSE 1 END,"
                "candidate.location_id LIMIT 1) "
                "LEFT JOIN source_roots AS source ON source.source_id=location.source_id "
                f"WHERE a.asset_id IN ({marks})", [row["asset_id"] for row in result]
            )
        }
        for row in result:
            location = locations[row["asset_id"]]
            row.update(relative_path=location["relative_path"], root_path=location["root_path"])
    return result


def timeline_page(catalog: Any, *, kind: str = "all", source_id: int | None = None,
                  person_id: int | None = None, collection_id: int | None = None,
                  place_kind: str | None = None, place_key: str | None = None,
                  data_root: Any = None,
                  time_basis: str = "capture", group_by: str = "day", bucket: str = "all",
                  year: int | None = None, month: int | None = None,
                  seek_year: int | None = None, seek_month: int | None = None,
                  cursor: str | None = None, anchor: str | None = None,
                  limit: int | None = None) -> dict[str, Any]:
    """Return one read-snapshot page with date projections and a scope-bound continuation."""
    _validate(time_basis, group_by, bucket, year, month)
    if time_basis == "capture" and bucket == "all" and year is not None:
        bucket = "capture"
    page_limit = _limit(limit)
    with catalog.read_snapshot() as connection:
        where, params, scope = _scope(catalog, kind, source_id, person_id, collection_id,
                                      place_kind, place_key, data_root)
        revision_value = revision.read_filter_revision(connection)
        cursor_revision = (place_timeline.cursor_revision(revision_value, scope["place_revisions"])
                           if kind == "place" else revision_value)
        rules = time_read.rules_version(connection)
        rules_match = rules == time_contract.RULES_VERSION
        binding_parameters = _params(kind=kind, scope=scope, time_basis=time_basis,
                                     group_by=group_by, bucket=bucket, year=year, month=month,
                                      rules_version=rules)
        fingerprint = _fingerprint(binding_parameters)
        if cursor is not None and anchor is not None:
            raise _fail("cursor and anchor cannot both be supplied", field="cursor")
        if (cursor is not None or anchor is not None) and seek_year is not None:
            raise _fail("seek parameters only apply when opening a timeline", field="seek_year")
        after = _decode_cursor(cursor or anchor, fingerprint, cursor_revision,
                               allow_stale=anchor is not None)
        if after is None:
            after = _seek_position(time_basis, seek_year, seek_month)
        where, params = _where_for_bucket(where, params, bucket=bucket, time_basis=time_basis,
                                         rules_match=rules_match, year=year, month=month)
        rows = _timeline_rows(connection, where, params, time_basis=time_basis, bucket=bucket,
                              rules_match=rules_match, after=after, inclusive=anchor is not None,
                              size=page_limit + 1)
        page = list(rows[:page_limit])
        has_more = len(rows) > page_limit
        assets: list[dict[str, Any]] = []
        for row in page:
            asset_id = int(row["asset_id"])
            source = None if row["time_source"] is None or not rules_match else str(row["time_source"])
            sort_value = None if row["sort_value"] is None else int(row["sort_value"])
            is_capture = time_basis == "capture" and rules_match and source in CAPTURE_SOURCES
            state = ("capture" if is_capture else "unknown" if rules_match and source in UNKNOWN_SOURCES else "pending") if time_basis == "capture" else "library"
            if time_basis == "library":
                day_key = _library_day(int(row["created_at"]))
                display_time = datetime.fromtimestamp(int(row["created_at"]) / 1000).astimezone().isoformat(timespec="milliseconds")
                sort_value = int(row["created_at"])
            elif is_capture and sort_value is not None:
                day_key, _ = _date_parts(sort_value, group_by)
                display_time = time_contract.normalize_bound(sort_value)
                offset = row["timezone_offset"]
                if offset is not None:
                    minutes = int(offset)
                    sign = "+" if minutes >= 0 else "-"
                    hours, rest = divmod(abs(minutes), 60)
                    display_time += f"{sign}{hours:02d}:{rest:02d}"
            else:
                day_key = None
                display_time = None
                sort_value = int(row["created_at"])
            group_key = None if day_key is None else {"day": day_key, "month": day_key[:7], "year": day_key[:4]}[group_by]
            relative_path = str(row["relative_path"] or "")
            filename = ntpath.basename(relative_path.replace("/", "\\")) if relative_path else ""
            root_path = str(row["root_path"] or "")
            full_path = ntpath.normpath(ntpath.join(root_path, relative_path)) if root_path and relative_path else ""
            directory_path = ntpath.dirname(full_path) + "\\" if full_path else ""
            item_position = [int(row["timeline_rank"]), int(row["timeline_sort"]), asset_id]
            assets.append({
                "asset_id": asset_id,
                "thumbnail_url": f"/api/v1/assets/{asset_id}/thumbnail",
                "filename": filename,
                "directory_path": directory_path,
                "display_time": display_time,
                "time_basis": time_basis,
                "time_source": source if time_basis == "capture" else "LIBRARY_ADDED",
                "time_confidence": None if row["time_confidence"] is None or not rules_match else str(row["time_confidence"]),
                "time_label": time_contract.label_for(source) if source else ("日期待就绪" if time_basis == "capture" else "加入图库时间"),
                "date_state": state,
                "group_key": group_key,
                "created_at": int(row["created_at"]),
                "anchor": _encode_cursor(fingerprint, cursor_revision, item_position),
            })
        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = _encode_cursor(fingerprint, cursor_revision, [int(last["timeline_rank"]), int(last["timeline_sort"]), int(last["asset_id"])])
        return {
            "assets": assets, "limit": page_limit, "next_cursor": next_cursor,
            "has_more": has_more, "scope": binding_parameters,
            "catalog_filter_revision": revision_value, "rules_version": rules,
            "rules_version_matches": rules_match, "read_version": fingerprint,
            "calendar_timezone": "system-local",
            **({"place_revisions": scope["place_revisions"]} if kind == "place" else {}),
        }


def timeline_periods(catalog: Any, *, kind: str = "all", source_id: int | None = None,
                     person_id: int | None = None, collection_id: int | None = None,
                     place_kind: str | None = None, place_key: str | None = None,
                     data_root: Any = None,
                     time_basis: str = "capture", year: int | None = None) -> dict[str, Any]:
    """Exact scoped date facets and mutually exclusive capture-time readiness counts."""
    if time_basis not in BASES:
        raise _fail("time_basis must be capture or library", field="time_basis")
    if year is not None and not 1 <= int(year) <= 9999:
        raise _fail("year is outside 0001..9999", field="year")
    with catalog.read_snapshot() as connection:
        base, params, scope = _scope(catalog, kind, source_id, person_id, collection_id,
                                     place_kind, place_key, data_root)
        revision_value = revision.read_filter_revision(connection)
        rules = time_read.rules_version(connection)
        rules_match = rules == time_contract.RULES_VERSION
        trusted_sql = "m.time_source IN ('EXIF_ORIGINAL','EXIF_DIGITIZED') AND m.taken_at_sort IS NOT NULL"
        rank_sql = (f"CASE WHEN {trusted_sql} THEN 0 WHEN m.time_source IN ('EXIF_ROOT','FILE_MTIME') "
                    "THEN 1 ELSE 2 END") if rules_match else "2"
        period_params: list[Any] = []
        if time_basis == "capture" and rules_match:
            day_offset = "CAST(m.taken_at_sort / 86400000 AS INTEGER)"
            civil = "date('0001-01-01', '+' || " + day_offset + " || ' days')"
            if year is None:
                period_sql = f"CASE WHEN {trusted_sql} THEN strftime('%Y', {civil}) END"
            else:
                low = (date(int(year), 1, 1) - date(1, 1, 1)).days * 86_400_000
                high = ((date(int(year) + 1, 1, 1) - date(1, 1, 1)).days * 86_400_000
                        if int(year) < 9999 else 315_537_897_600_000)
                period_sql = (f"CASE WHEN {trusted_sql} AND m.taken_at_sort>=? AND m.taken_at_sort<? "
                              f"THEN strftime('%m', {civil}) END")
                period_params = [low, high]
        elif time_basis == "capture":
            period_sql = "NULL"
        elif year is None:
            period_sql = "strftime('%Y', a.created_at / 1000, 'unixepoch', 'localtime')"
        else:
            period_sql = ("CASE WHEN strftime('%Y', a.created_at / 1000, 'unixepoch', 'localtime')=? "
                          "THEN strftime('%m', a.created_at / 1000, 'unixepoch', 'localtime') END")
            period_params = [f"{int(year):04d}"]
        # Counts and requested date facets share exactly one qualified membership scan.
        # NULL periods retain other years and untrusted rows in the global readiness counts.
        rows = connection.execute(
            f"SELECT {rank_sql} AS date_rank,{period_sql} AS period,COUNT(*) AS count "
            "FROM assets AS a LEFT JOIN asset_metadata AS m USING(asset_id) "
            f"WHERE ({base}) GROUP BY date_rank,period", (*period_params, *params)
        ).fetchall()
        totals = [0, 0, 0]
        periods: dict[int, int] = {}
        for row in rows:
            count = int(row["count"])
            totals[int(row["date_rank"])] += count
            if row["period"] is not None:
                key = int(row["period"])
                periods[key] = periods.get(key, 0) + count
        trusted, unknown, pending = totals
        base_count = sum(totals)
        entries = [{"year" if year is None else "month": key, "count": periods[key]}
                   for key in sorted(periods, reverse=True)]
        years, months = (entries, []) if year is None else ([], entries)
        return {
            "scope": {"kind": kind, **scope}, "time_basis": time_basis,
            "calendar_timezone": "system-local", "years": years, "months": months,
            "counts": {"effective_total": base_count, "trusted": trusted,
                       "unknown": unknown, "pending": pending},
            "counts_conserve": base_count == trusted + unknown + pending,
            "catalog_filter_revision": revision_value, "rules_version": rules,
            "rules_version_matches": rules_match,
            **({"place_revisions": scope["place_revisions"]} if kind == "place" else {}),
        }


__all__ = ["timeline_page", "timeline_periods"]
