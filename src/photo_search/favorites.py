"""Persistent user favorites for complete photo assets."""

from __future__ import annotations

import base64
import binascii
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


def _encode_cursor(added_at_ms: int, asset_id: int, revision: int) -> str:
    raw = f"{CURSOR_PREFIX}:{added_at_ms}:{asset_id}:{revision}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[int, int, int] | None:
    if cursor is None or not str(cursor).strip():
        return None
    try:
        raw = base64.urlsafe_b64decode(str(cursor) + "=" * (-len(str(cursor)) % 4)).decode("ascii")
        prefix, added_at, asset_id, revision = raw.split(":")
        values = int(added_at), int(asset_id), int(revision)
    except (ValueError, UnicodeDecodeError, binascii.Error) as error:
        raise ApiError("INVALID_CURSOR", "the favorites cursor is not valid", status=422) from error
    if prefix != CURSOR_PREFIX or min(values) < 0 or values[1] < 1:
        raise ApiError("INVALID_CURSOR", "the favorites cursor is not valid", status=422)
    return values


def list_favorites(catalog: Any, cache: Any, *, limit: int = DEFAULT_LIMIT, cursor: str | None = None) -> dict[str, Any]:
    page_limit = max(1, min(int(limit), MAX_LIMIT))
    position = _decode_cursor(cursor)
    with catalog.read_snapshot() as connection:
        revision = _revision(connection)
        if position is not None and position[2] != revision:
            raise ApiError(FAVORITES_REVISION_STALE, "收藏内容已变化，请刷新后重新读取。", status=409)
        photo_sql, photo_params = photo_asset_sql(asset_alias="a")
        conditions = ["a.state='ACTIVE'", photo_sql]
        params: list[Any] = list(photo_params)
        if position is not None:
            conditions.append("(f.added_at_ms, f.asset_id) < (?, ?)")
            params.extend((position[0], position[1]))
        rows = connection.execute(
            "SELECT f.asset_id, f.added_at_ms, a.sha256, a.byte_size, a.created_at "
            "FROM asset_favorites AS f INDEXED BY idx_asset_favorites_recent "
            "JOIN assets AS a ON a.asset_id=f.asset_id WHERE " + " AND ".join(conditions) +
            " ORDER BY f.added_at_ms DESC, f.asset_id DESC LIMIT ?",
            (*params, page_limit + 1),
        ).fetchall()
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
            "next_cursor": _encode_cursor(int(last["added_at_ms"]), int(last["asset_id"]), revision) if has_more and last else None,
            "has_more": has_more,
            "favorites_revision": revision,
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
