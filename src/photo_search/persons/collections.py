"""Personal person collections backed by stable person IDs and effective face links."""

from __future__ import annotations

import base64
import binascii
import json
import secrets
import unicodedata
from typing import Any

from photo_search.api.errors import INVALID_CURSOR, VALIDATION_ERROR, ApiError
from photo_search.api.queries import asset_page, encode_capture_cursor
from photo_search.catalog.errors import CATALOG_FILTER_REVISION_STALE
from photo_search.persons import albums
from photo_search.persons.contracts import normalize_person_name
from photo_search.persons.identity import resolve_canonical_person_id
from photo_search.persons.members import MEMBER_PREDICATE
from photo_search.places.revision import read_filter_revision

_MISSING = "PERSON_COLLECTION_NOT_FOUND"
_CURSOR_PREFIX = "pc1"
_PEOPLE_CURSOR_PREFIX = "pcp1"
_PHOTOS_CURSOR_PREFIX = "pcf1"
_MAX_BATCH = 500
_MAX_NAME = 80


def _encode(prefix: str, binding: list[Any], position: Any) -> str:
    raw = json.dumps([prefix, *binding, position], separators=(",", ":"), ensure_ascii=True)
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode(cursor: str | None, prefix: str, binding: list[Any]) -> Any:
    if not cursor:
        return None
    try:
        if len(cursor) > 2048:
            raise ValueError
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        values = json.loads(raw)
        if not isinstance(values, list) or values[:-1] != [prefix, *binding]:
            raise ValueError
        return values[-1]
    except (ValueError, TypeError, UnicodeError, binascii.Error) as error:
        raise ApiError(INVALID_CURSOR, "合集内容已变化，请刷新后继续", status=409) from error


def _require_collection(connection: Any, collection_id: int) -> Any:
    row = connection.execute(
        "SELECT * FROM person_collections WHERE collection_id = ?", (int(collection_id),)
    ).fetchone()
    if row is None:
        raise ApiError(_MISSING, "人物合集不存在", status=404)
    return row


def _require_collection_instance(connection: Any, collection_id: int, instance_token: str) -> Any:
    row = _require_collection(connection, collection_id)
    if str(row["instance_token"]) != str(instance_token):
        raise ApiError(_MISSING, "人物合集实例已变化，请刷新", status=404)
    return row


def _normalise_collection_name(value: Any) -> tuple[str, str]:
    display, _ = normalize_person_name(value)
    if len(display) > _MAX_NAME:
        raise ApiError(VALIDATION_ERROR, "合集名称最多 80 个字符", status=422)
    # The display spelling is preserved; the key treats case and whitespace variants as one.
    return display, "nfc1:" + unicodedata.normalize("NFC", display.casefold())


def _canonical_cte() -> str:
    return (
        "WITH RECURSIVE chain(collection_id, origin_id, person_id, merged_into, path) AS ("
        "SELECT m.collection_id, p.person_id, p.person_id, p.merged_into, ',' || p.person_id || ',' "
        "FROM person_collection_members AS m JOIN persons AS p ON p.person_id = m.person_id "
        "UNION ALL SELECT c.collection_id, c.origin_id, p.person_id, p.merged_into, "
        "c.path || p.person_id || ',' FROM chain AS c JOIN persons AS p "
        "ON p.person_id = c.merged_into WHERE c.merged_into IS NOT NULL "
        "AND instr(c.path, ',' || p.person_id || ',') = 0), "
        "canonical_members AS (SELECT DISTINCT collection_id, origin_id, person_id "
        "FROM chain WHERE merged_into IS NULL) "
    )


def _collection_summary(row: Any) -> dict[str, Any]:
    return {
        "collection_id": int(row["collection_id"]),
        "instance_token": str(row["instance_token"]),
        "name": str(row["name"]),
        "revision": int(row["revision"]),
        "member_count": int(row["member_count"]),
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
    }


def list_collections(catalog: Any, *, limit: int = 40, cursor: str | None = None) -> dict[str, Any]:
    limit = max(1, min(int(limit), 100))
    binding: list[Any] = ["collections1"]
    after = _decode(cursor, _CURSOR_PREFIX, binding)
    if after is not None and (type(after) is not int or after < 1):
        raise ApiError(INVALID_CURSOR, "合集游标无效", status=409)
    where = "WHERE c.collection_id > ?" if after else ""
    params: tuple[Any, ...] = ((int(after),) if after is not None else ()) + (limit + 1,)
    rows = catalog.read_all(
        _canonical_cte()
        + "SELECT c.*, COUNT(DISTINCT cm.person_id) AS member_count FROM person_collections AS c "
        "LEFT JOIN canonical_members AS cm ON cm.collection_id = c.collection_id "
        + where + " GROUP BY c.collection_id ORDER BY c.collection_id LIMIT ?",
        params,
    )
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "collections": [_collection_summary(row) for row in rows],
        "has_more": more,
        "next_cursor": _encode(_CURSOR_PREFIX, binding, int(rows[-1]["collection_id"])) if more else None,
    }


def create_collection(catalog: Any, name: Any) -> dict[str, Any]:
    display, name_key = _normalise_collection_name(name)
    now = catalog.now_ms()
    instance_token = secrets.token_hex(16)
    try:
        with catalog.write() as connection:
            cursor = connection.execute(
                "INSERT INTO person_collections(name, name_key, revision, created_at, updated_at, instance_token) "
                "VALUES (?, ?, 1, ?, ?, ?)", (display, name_key, now, now, instance_token)
            )
            collection_id = int(cursor.lastrowid)
            row = connection.execute(
                "SELECT c.*, 0 AS member_count FROM person_collections AS c WHERE collection_id = ?",
                (collection_id,),
            ).fetchone()
            return {"collection": _collection_summary(row)}
    except Exception as error:
        if "UNIQUE constraint failed" in str(error):
            raise ApiError(VALIDATION_ERROR, "已存在同名人物合集", status=409) from error
        raise


def collection_detail(catalog: Any, collection_id: int, *, instance_token: str) -> dict[str, Any]:
    connection = catalog.connection
    row = _require_collection_instance(connection, collection_id, instance_token)
    count = connection.execute(
        _canonical_cte() + "SELECT COUNT(DISTINCT person_id) FROM canonical_members WHERE collection_id = ?",
        (int(collection_id),),
    ).fetchone()[0]
    return {"collection": _collection_summary({**dict(row), "member_count": int(count)})}


def rename_collection(catalog: Any, collection_id: int, name: Any, *, instance_token: str) -> dict[str, Any]:
    display, name_key = _normalise_collection_name(name)
    try:
        with catalog.write() as connection:
            current = _require_collection_instance(connection, collection_id, instance_token)
            if str(current["name"]) != display:
                connection.execute(
                    "UPDATE person_collections SET name = ?, name_key = ?, revision = revision + 1, updated_at = ? "
                    "WHERE collection_id = ?",
                    (display, name_key, catalog.now_ms(), int(collection_id)),
                )
            return collection_detail(catalog, collection_id, instance_token=instance_token)
    except Exception as error:
        if "UNIQUE constraint failed" in str(error):
            raise ApiError(VALIDATION_ERROR, "已存在同名人物合集", status=409) from error
        raise


def delete_collection(catalog: Any, collection_id: int, *, instance_token: str) -> dict[str, Any]:
    with catalog.write() as connection:
        _require_collection_instance(connection, collection_id, instance_token)
        connection.execute("DELETE FROM person_collections WHERE collection_id = ?", (int(collection_id),))
    return {"deleted": True, "collection_id": int(collection_id)}


def edit_members(
    catalog: Any,
    collection_id: int,
    *,
    action: Any,
    person_ids: Any,
    instance_token: str,
    hide_after_add: Any = False,
) -> dict[str, Any]:
    if action not in ("add", "remove"):
        raise ApiError(VALIDATION_ERROR, "action 必须是 add 或 remove", status=422)
    if not isinstance(person_ids, list) or not 1 <= len(person_ids) <= _MAX_BATCH:
        raise ApiError(VALIDATION_ERROR, "每次需要提交 1 到 500 个 person_id", status=422)
    if any(type(value) is not int or value < 1 for value in person_ids) or len(set(person_ids)) != len(person_ids):
        raise ApiError(VALIDATION_ERROR, "person_ids 必须是互不重复的正整数", status=422)
    if type(hide_after_add) is not bool or (hide_after_add and action != "add"):
        raise ApiError(VALIDATION_ERROR, "hide_after_add 只可用于 add", status=422)
    with catalog.write() as connection:
        _require_collection_instance(connection, collection_id, instance_token)
        # Validate the complete input set and resolve redirects before the first write.
        canonical: list[int] = []
        for requested in person_ids:
            canonical.append(resolve_canonical_person_id(connection, requested))
        canonical = sorted(set(canonical))
        if action == "add":
            changed = 0
            for person_id in canonical:
                result = connection.execute(
                    "INSERT OR IGNORE INTO person_collection_members(collection_id, person_id) VALUES (?, ?)",
                    (int(collection_id), person_id),
                )
                changed += int(result.rowcount == 1)
            if hide_after_add:
                connection.executemany(
                    "INSERT OR IGNORE INTO person_hidden(person_id) VALUES (?)",
                    [(person_id,) for person_id in canonical],
                )
        else:
            marks = ",".join("?" for _ in canonical)
            query = (
                _canonical_cte()
                + "DELETE FROM person_collection_members WHERE collection_id = ? AND person_id IN ("
                "SELECT origin_id FROM canonical_members WHERE collection_id = ? AND person_id IN ("
                + marks + ")) RETURNING person_id"
            )
            changed = len(connection.execute(
                query, (int(collection_id), int(collection_id), *canonical)
            ).fetchall())
        if changed:
            connection.execute(
                "UPDATE person_collections SET revision = revision + 1, updated_at = ? WHERE collection_id = ?",
                (catalog.now_ms(), int(collection_id)),
            )
        row = connection.execute(
            "SELECT revision FROM person_collections WHERE collection_id = ?", (int(collection_id),)
        ).fetchone()
        return {"action": action, "changed": int(changed), "revision": int(row["revision"]), "hidden": bool(hide_after_add)}


def people_page(
    catalog: Any, collection_id: int, *, instance_token: str, limit: int = 40, cursor: str | None = None
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 100))
    connection = catalog.connection
    collection = _require_collection_instance(connection, collection_id, instance_token)
    filter_revision = read_filter_revision(connection)
    binding = [int(collection_id), str(collection["instance_token"]), int(collection["revision"]), int(filter_revision)]
    after = _decode(cursor, _PEOPLE_CURSOR_PREFIX, binding)
    if after is not None and (type(after) is not int or after < 1):
        raise ApiError(INVALID_CURSOR, "合集人物游标无效", status=409)
    rows = catalog.read_all(
        _canonical_cte()
        + "SELECT p.*, (SELECT COUNT(DISTINCT f.asset_id) FROM face_person_links AS l "
        "JOIN faces AS f ON f.face_id = l.face_id JOIN assets AS a ON a.asset_id=f.asset_id "
        "WHERE l.person_id = p.person_id AND a.state='ACTIVE' AND " + MEMBER_PREDICATE + ") AS page_asset_count "
        "FROM canonical_members AS cm JOIN persons AS p ON p.person_id = cm.person_id "
        "WHERE cm.collection_id = ? AND p.person_id > ? GROUP BY p.person_id "
        "ORDER BY p.person_id LIMIT ?",
        (int(collection_id), int(after or 0), limit + 1),
    )
    more = len(rows) > limit
    rows = rows[:limit]
    summaries = albums._album_summaries(catalog.connection, rows, reuse_asset_counts=True)
    return {
        "collection": {"collection_id": int(collection_id), "instance_token": str(collection["instance_token"]), "name": str(collection["name"]), "revision": int(collection["revision"])},
        "people": summaries,
        "has_more": more,
        "next_cursor": _encode(_PEOPLE_CURSOR_PREFIX, binding, int(rows[-1]["person_id"])) if more else None,
    }


def _collection_photo_faces(connection: Any, collection_id: int, asset_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not asset_ids:
        return {}
    placeholders = ",".join("?" for _ in asset_ids)
    rows = connection.execute(
        _canonical_cte()
        + ", current_members AS (SELECT DISTINCT collection_id, person_id FROM canonical_members), "
        "eligible_faces AS (SELECT DISTINCT f.asset_id, f.face_id, f.face_pixels "
        "FROM faces AS f JOIN face_person_links AS l ON l.face_id = f.face_id "
        "JOIN current_members AS cm ON cm.person_id = l.person_id "
        "WHERE cm.collection_id = ? AND f.asset_id IN (" + placeholders + ") AND " + MEMBER_PREDICATE + "), "
        "ranked_faces AS (SELECT asset_id, face_id, ROW_NUMBER() OVER "
        "(PARTITION BY asset_id ORDER BY face_pixels DESC, face_id) AS position FROM eligible_faces) "
        "SELECT asset_id, face_id, position FROM ranked_faces "
        "WHERE position <= 9 ORDER BY asset_id, position",
        (int(collection_id), *asset_ids),
    ).fetchall()
    grouped: dict[int, list[str]] = {}
    for row in rows:
        grouped.setdefault(int(row["asset_id"]), []).append(str(row["face_id"]))
    return {
        asset_id: {
            "person_faces": [{"face_id": face_id, "thumbnail_url": f"/api/v1/faces/{face_id}/thumbnail"} for face_id in ids[:8]],
            "person_faces_has_more": len(ids) > 8,
        }
        for asset_id, ids in grouped.items()
    }


def photo_page(
    catalog: Any, cache: Any, collection_id: int, *, instance_token: str, limit: int = 60, cursor: str | None = None
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 100))
    connection = catalog.connection
    collection = _require_collection_instance(connection, collection_id, instance_token)
    filter_revision = read_filter_revision(connection)
    binding = [int(collection_id), str(collection["instance_token"]), int(collection["revision"]), int(filter_revision)]
    inner = _decode(cursor, _PHOTOS_CURSOR_PREFIX, binding)
    if inner is not None and not isinstance(inner, str):
        raise ApiError(INVALID_CURSOR, "合集照片游标无效", status=409)
    page = asset_page(
        catalog, cache, limit=limit + 1, cursor=inner, order="capture_desc", collection_id=int(collection_id)
    )
    if int(page["catalog_filter_revision"]) != int(filter_revision):
        raise ApiError(CATALOG_FILTER_REVISION_STALE, "图库人物关系已变化，请刷新合集", status=409)
    page["has_more"] = len(page["assets"]) > limit
    page["assets"] = page["assets"][:limit]
    page["limit"] = limit
    page["next_cursor"] = None
    if page["has_more"]:
        last_id = int(page["assets"][-1]["asset_id"])
        metadata = catalog.read_one("SELECT taken_at_sort FROM asset_metadata WHERE asset_id = ?", (last_id,))
        sort_key = None if metadata is None else metadata["taken_at_sort"]
        next_inner = encode_capture_cursor(int(sort_key is not None), int(sort_key or 0), last_id, page["catalog_filter_revision"])
        page["next_cursor"] = _encode(_PHOTOS_CURSOR_PREFIX, binding, next_inner)
    ids = [int(item["asset_id"]) for item in page["assets"]]
    time_views, _ = albums._photo_page_facts(connection, asset_ids=ids, person_id=-1)
    faces = _collection_photo_faces(connection, collection_id, ids)
    for item in page["assets"]:
        item.pop("current_location", None)
        asset_id = int(item["asset_id"])
        item["taken_time"] = time_views.get(asset_id)
        item.update(faces.get(asset_id, {"person_faces": [], "person_faces_has_more": False}))
    page["collection"] = {"collection_id": int(collection_id), "instance_token": str(collection["instance_token"]), "name": str(collection["name"]), "revision": int(collection["revision"])}
    page.pop("search", None)
    return page


__all__ = ["collection_detail", "create_collection", "delete_collection", "edit_members", "list_collections", "people_page", "photo_page", "rename_collection"]
