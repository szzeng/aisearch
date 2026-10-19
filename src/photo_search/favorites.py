"""Persistent user favorites for complete photo assets."""

from __future__ import annotations

import base64
import binascii
import heapq
import sqlite3
from typing import Any

from photo_search.api.errors import FAVORITES_REVISION_STALE, ApiError
from photo_search.api.queries import locations_by_asset, summarize_asset
from photo_search.catalog.errors import ASSET_NOT_FOUND, CATALOG_UNREADABLE, CatalogError
from photo_search.catalog.video import photo_asset_sql

CURSOR_PREFIX = "fav1"
DEFAULT_LIMIT = 50
MAX_LIMIT = 50
MAX_STATE_IDS = 100
SQLITE_INT64_MAX = (1 << 63) - 1


def _revision(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT value FROM metadata WHERE key='favorites_revision'").fetchone()
    if row is None:
        raise CatalogError(CATALOG_UNREADABLE, "favorites revision metadata is missing")
    try:
        revision = int(row[0])
    except (TypeError, ValueError) as error:
        raise CatalogError(CATALOG_UNREADABLE, "favorites revision metadata is invalid") from error
    if revision < 0:
        raise CatalogError(CATALOG_UNREADABLE, "favorites revision metadata is invalid")
    return revision


def _encode_cursor(order: str, position: tuple[int, ...], revision: int, catalog_revision: int) -> str:
    raw = ":".join((CURSOR_PREFIX, order, *(str(value) for value in position), str(revision), str(catalog_revision))).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[str, tuple[int, ...], int, int] | None:
    if cursor is None or not str(cursor).strip():
        return None
    try:
        raw = base64.urlsafe_b64decode(str(cursor) + "=" * (-len(str(cursor)) % 4)).decode("ascii")
        parts = raw.split(":")
        if len(parts) < 2 or parts[0] != CURSOR_PREFIX or parts[1] not in ("recent", "capture_desc"):
            raise ValueError("invalid cursor mode")
        order = parts[1]
        width = 2 if order == "recent" else 3
        if len(parts) != 2 + width + 2:
            raise ValueError("invalid cursor shape")
        position = tuple(int(value) for value in parts[2:2 + width])
        revision, catalog_revision = int(parts[-2]), int(parts[-1])
    except (ValueError, UnicodeDecodeError, binascii.Error) as error:
        raise ApiError("INVALID_CURSOR", "the favorites cursor is not valid", status=422) from error
    values = (*position, revision, catalog_revision)
    if min(values) < 0 or max(values) > SQLITE_INT64_MAX or position[-1] < 1:
        raise ApiError("INVALID_CURSOR", "the favorites cursor is not valid", status=422)
    if order == "capture_desc" and position[0] not in (0, 1):
        raise ApiError("INVALID_CURSOR", "the favorites cursor is not valid", status=422)
    return order, position, revision, catalog_revision


_TRUSTED_CAPTURE_SQL = "(m.time_source IN ('EXIF_ORIGINAL','EXIF_DIGITIZED') AND m.taken_at_sort IS NOT NULL)"


def _capture_known_query(
    source: str,
    *,
    photo_sql: str,
    photo_params: list[int],
    limit: int,
    after: tuple[int, int] | None,
) -> tuple[str, tuple[Any, ...]]:
    conditions = [
        "m.time_source=?",
        "m.taken_at_sort IS NOT NULL",
        "a.state='ACTIVE'",
        photo_sql,
    ]
    params: list[Any] = [source, *photo_params]
    if after is not None:
        conditions.append("(m.taken_at_sort,m.asset_id) < (?,?)")
        params.extend(after)
    sql = (
        "SELECT f.asset_id,f.added_at_ms,a.sha256,a.byte_size,a.created_at,"
        "m.taken_at_sort AS capture_sort "
        "FROM asset_metadata AS m INDEXED BY idx_asset_metadata_capture "
        "CROSS JOIN asset_favorites AS f ON f.asset_id=m.asset_id "
        "CROSS JOIN assets AS a ON a.asset_id=f.asset_id WHERE "
        + " AND ".join(conditions)
        + " ORDER BY m.taken_at_sort DESC,m.asset_id DESC LIMIT ?"
    )
    return sql, (*params, limit)


def _capture_unknown_query(
    *,
    photo_sql: str,
    photo_params: list[int],
    limit: int,
    after_asset_id: int | None,
) -> tuple[str, tuple[Any, ...]]:
    conditions = [
        "a.state='ACTIVE'",
        f"NOT {_TRUSTED_CAPTURE_SQL}",
        photo_sql,
    ]
    params: list[Any] = list(photo_params)
    if after_asset_id is not None:
        conditions.append("f.asset_id < ?")
        params.append(after_asset_id)
    sql = (
        "SELECT f.asset_id,f.added_at_ms,a.sha256,a.byte_size,a.created_at,"
        "NULL AS capture_sort FROM asset_favorites AS f NOT INDEXED "
        "CROSS JOIN assets AS a ON a.asset_id=f.asset_id "
        "LEFT JOIN asset_metadata AS m ON m.asset_id=f.asset_id WHERE "
        + " AND ".join(conditions)
        + " ORDER BY f.asset_id DESC LIMIT ?"
    )
    return sql, (*params, limit)


def _capture_favorite_rows(
    connection: sqlite3.Connection,
    *,
    page_limit: int,
    photo_sql: str,
    photo_params: list[int],
    position: tuple[int, ...] | None,
) -> list[sqlite3.Row]:
    """Read an ordered page from indexed EXIF streams, then the unknown-time tail."""
    known_cursor = position is None or position[0] == 1
    known_rows: list[sqlite3.Row] = []
    if known_cursor:
        after = None if position is None else (position[1], position[2])
        streams = []
        for source in ("EXIF_ORIGINAL", "EXIF_DIGITIZED"):
            sql, params = _capture_known_query(
                source,
                photo_sql=photo_sql,
                photo_params=photo_params,
                limit=page_limit + 1,
                after=after,
            )
            streams.append(connection.execute(sql, params).fetchall())
        known_rows = list(
            heapq.merge(
                *streams,
                key=lambda row: (-int(row["capture_sort"]), -int(row["asset_id"])),
            )
        )[: page_limit + 1]
        if len(known_rows) > page_limit:
            return known_rows

    unknown_after = None if position is None or known_cursor else position[2]
    unknown_limit = page_limit + 1 - len(known_rows)
    sql, params = _capture_unknown_query(
        photo_sql=photo_sql,
        photo_params=photo_params,
        limit=unknown_limit,
        after_asset_id=unknown_after,
    )
    unknown_rows = connection.execute(sql, params).fetchall()
    return known_rows + unknown_rows


def list_favorites(catalog: Any, cache: Any, *, limit: int = DEFAULT_LIMIT, cursor: str | None = None,
                   order: str = "recent") -> dict[str, Any]:
    if order not in ("recent", "capture_desc"):
        raise ApiError("VALIDATION_ERROR", "order must be recent or capture_desc", status=422)
    page_limit = max(1, min(int(limit), MAX_LIMIT))
    position = _decode_cursor(cursor)
    with catalog.read_snapshot() as connection:
        revision = _revision(connection)
        from photo_search.places.revision import read_filter_revision

        catalog_revision = read_filter_revision(connection)
        if position is not None and (position[0] != order or position[2] != revision
                                     or (order == "capture_desc" and position[3] != catalog_revision)):
            raise ApiError(FAVORITES_REVISION_STALE, "收藏或排序依据已变化，请刷新后从第一页重新读取。", status=409)
        photo_sql, photo_params = photo_asset_sql(asset_alias="a")
        conditions = ["a.state='ACTIVE'", photo_sql]
        params: list[Any] = list(photo_params)
        if order == "recent":
            if position is not None:
                conditions.append("(f.added_at_ms, f.asset_id) < (?, ?)")
                params.extend(position[1])
            order_sql = "f.added_at_ms DESC, f.asset_id DESC"
            selection = "f.asset_id, f.added_at_ms, a.sha256, a.byte_size, a.created_at"
            rows = connection.execute(
                f"SELECT {selection} FROM asset_favorites AS f INDEXED BY idx_asset_favorites_recent "
                "JOIN assets AS a ON a.asset_id=f.asset_id WHERE " + " AND ".join(conditions) +
                f" ORDER BY {order_sql} LIMIT ?",
                (*params, page_limit + 1),
            ).fetchall()
        else:
            capture_position = None if position is None else position[1]
            rows = _capture_favorite_rows(
                connection,
                page_limit=page_limit,
                photo_sql=photo_sql,
                photo_params=photo_params,
                position=capture_position,
            )
        has_more = len(rows) > page_limit
        page = rows[:page_limit]
        ids = [int(row["asset_id"]) for row in page]
        locations = locations_by_asset(catalog, ids)
        assets = []
        for row in page:
            item = summarize_asset(
                cache,
                asset_id=int(row["asset_id"]),
                sha256=str(row["sha256"]),
                byte_size=int(row["byte_size"]),
                created_at=int(row["created_at"]),
                locations=locations.get(int(row["asset_id"]), []),
            )
            item["favorited_at_ms"] = int(row["added_at_ms"])
            assets.append(item)
        last = page[-1] if page else None
        return {
            "assets": assets,
            "next_cursor": _encode_cursor(
                order,
                (int(last["added_at_ms"]), int(last["asset_id"])) if order == "recent" else
                (int(last["capture_sort"] is not None), int(last["capture_sort"] or 0), int(last["asset_id"])),
                revision, catalog_revision if order == "capture_desc" else 0,
            ) if has_more and last else None,
            "has_more": has_more,
            "favorites_revision": revision,
            "order": order,
        }


def favorite_states(catalog: Any, asset_ids: list[int]) -> dict[str, Any]:
    if not asset_ids or len(asset_ids) > MAX_STATE_IDS:
        raise ApiError("VALIDATION_ERROR", "asset_ids must contain between 1 and 100 ids", status=422)
    if any(isinstance(value, bool) or int(value) < 1 for value in asset_ids):
        raise ApiError("VALIDATION_ERROR", "asset_ids must be positive integers", status=422)
    ids = list(dict.fromkeys(int(value) for value in asset_ids))
    placeholders = ",".join("?" for _ in ids)
    with catalog.read_snapshot() as connection:
        revision = _revision(connection)
        rows = connection.execute(
            f"SELECT asset_id FROM asset_favorites WHERE asset_id IN ({placeholders})", ids
        ).fetchall()
        return {"favorite_asset_ids": [int(row[0]) for row in rows], "favorites_revision": revision}


def set_favorite(catalog: Any, asset_id: int, *, favorite: bool) -> dict[str, Any]:
    asset_id = int(asset_id)
    if asset_id < 1:
        raise CatalogError(ASSET_NOT_FOUND, "asset not found")
    photo_sql, photo_params = photo_asset_sql(asset_alias="a")
    with catalog.write() as connection:
        asset = connection.execute(
            f"SELECT a.asset_id FROM assets AS a WHERE a.asset_id=? AND a.state='ACTIVE' AND {photo_sql}",
            (asset_id, *photo_params),
        ).fetchone()
        if asset is None:
            raise CatalogError(ASSET_NOT_FOUND, "active photo asset not found")
        existing = connection.execute("SELECT 1 FROM asset_favorites WHERE asset_id=?", (asset_id,)).fetchone()
        changed = bool(favorite) != (existing is not None)
        if changed:
            if favorite:
                connection.execute(
                    "INSERT INTO asset_favorites(asset_id, added_at_ms) VALUES (?, ?)",
                    (asset_id, catalog.now_ms()),
                )
            else:
                connection.execute("DELETE FROM asset_favorites WHERE asset_id=?", (asset_id,))
            connection.execute(
                "UPDATE metadata SET value=CAST(value AS INTEGER)+1 WHERE key='favorites_revision'"
            )
        revision = _revision(connection)
        return {"asset_id": asset_id, "is_favorite": bool(favorite), "changed": changed, "favorites_revision": revision}


def set_favorites_batch(catalog: Any, asset_ids: list[int], *, favorite: bool) -> dict[str, Any]:
    ids = [int(value) for value in asset_ids]
    if not 1 <= len(ids) <= 500 or any(value <= 0 for value in ids) or len(set(ids)) != len(ids):
        raise ApiError("VALIDATION_ERROR", "asset_ids must contain 1 to 500 distinct positive integers", status=422)
    photo_sql, photo_params = photo_asset_sql(asset_alias="a")
    placeholders = ",".join("?" for _ in ids)
    with catalog.write() as connection:
        rows = connection.execute(
            f"SELECT a.asset_id FROM assets AS a WHERE a.asset_id IN ({placeholders}) "
            f"AND a.state='ACTIVE' AND {photo_sql}", (*ids, *photo_params),
        ).fetchall()
        eligible = {int(row[0]) for row in rows}
        if eligible != set(ids):
            missing = [value for value in ids if value not in eligible]
            raise ApiError("INVALID_BATCH_ASSET", "批量收藏包含不存在、非图片或非活动资产；没有执行任何更改。",
                           status=422, details={"asset_ids": missing})
        existing_rows = connection.execute(
            f"SELECT asset_id FROM asset_favorites WHERE asset_id IN ({placeholders})", ids
        ).fetchall()
        existing = {int(row[0]) for row in existing_rows}
        targets = set(ids) if favorite else set()
        changed_ids = targets - existing if favorite else existing
        if changed_ids:
            if favorite:
                now_ms = int(catalog.now_ms())
                connection.executemany(
                    "INSERT INTO asset_favorites(asset_id, added_at_ms) VALUES (?, ?)",
                    ((asset_id, now_ms) for asset_id in ids if asset_id in changed_ids),
                )
            else:
                connection.executemany(
                    "DELETE FROM asset_favorites WHERE asset_id=?",
                    ((asset_id,) for asset_id in ids if asset_id in changed_ids),
                )
            connection.execute("UPDATE metadata SET value=CAST(value AS INTEGER)+1 WHERE key='favorites_revision'")
        revision = _revision(connection)
        return {"is_favorite": bool(favorite), "requested": len(ids), "changed": len(changed_ids),
                "unchanged": len(ids) - len(changed_ids), "favorites_revision": revision}
