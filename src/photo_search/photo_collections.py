"""User-confirmed, static photo collection membership."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
import unicodedata
from typing import Any

from photo_search.api.errors import INVALID_CURSOR, VALIDATION_ERROR, ApiError
from photo_search.catalog.video import photo_asset_sql

MAX_BATCH = 500
MAX_CREATE_IDS = 5000
MAX_NAME = 80
_MISSING = "PHOTO_COLLECTION_NOT_FOUND"


def _fail(message: str, *, status: int = 422, code: str = VALIDATION_ERROR) -> ApiError:
    return ApiError(code, message, status=status)


def _name(value: Any) -> tuple[str, str]:
    if not isinstance(value, str):
        raise _fail("合集名称必须是文本")
    display = unicodedata.normalize("NFC", " ".join(value.split()))
    if not display or len(display) > MAX_NAME:
        raise _fail("合集名称不能为空且最多 80 个字符")
    return display, "nfc1:" + display.casefold()


def _cursor_encode(prefix: str, binding: list[Any], position: Any) -> str:
    raw = json.dumps([prefix, *binding, position], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _cursor_decode(token: str | None, prefix: str, binding: list[Any]) -> Any:
    if token is None:
        return None
    try:
        if len(token) > 2048:
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        if not isinstance(data, list) or data[:-1] != [prefix, *binding]:
            raise ValueError
        return data[-1]
    except (ValueError, TypeError, UnicodeError, binascii.Error) as error:
        raise _fail("照片合集内容已变化，请刷新", status=409, code=INVALID_CURSOR) from error


def _revision(connection: Any) -> int:
    row = connection.execute("SELECT value FROM metadata WHERE key='photo_collections_revision'").fetchone()
    if row is None:
        raise _fail("照片合集目录版本缺失", status=500)
    return int(row[0])


def _row(connection: Any, collection_id: int, token: str | None = None) -> Any:
    row = connection.execute(
        "SELECT * FROM photo_collections WHERE collection_id=?", (int(collection_id),)
    ).fetchone()
    if row is None:
        raise _fail("照片合集不存在", status=404, code=_MISSING)
    if token is not None and str(row["instance_token"]) != token:
        raise _fail("照片合集实例已变化，请刷新", status=409, code=_MISSING)
    return row


def _check_place_scope(connection: Any, expected: tuple[int, str | None] | None) -> None:
    if expected is None:
        return
    expected_suggestion_revision, expected_package_revision = expected
    from photo_search.places import revision

    suggestion_revision = revision.read_suggestion_revision(connection)
    dataset = connection.execute(
        "SELECT a.dataset_id,a.revision,g.content_sha256 FROM active_geo_dataset a "
        "LEFT JOIN geo_datasets g ON g.dataset_id=a.dataset_id WHERE a.singleton=1"
    ).fetchone()
    map_revision = connection.execute(
        "SELECT revision FROM map_member_revision WHERE singleton=1"
    ).fetchone()
    package_revision = None if dataset is None or dataset["dataset_id"] is None else (
        f"{dataset['dataset_id']}:{dataset['content_sha256']}:{dataset['revision']}:{map_revision['revision']}"
    )
    if (suggestion_revision, package_revision) != (int(expected_suggestion_revision), expected_package_revision):
        raise _fail("地点搜索范围已变化，请重新搜索后保存", status=409, code="CATALOG_FILTER_REVISION_STALE")


def _summary(row: Any) -> dict[str, Any]:
    return {key: (int(row[key]) if key in {"collection_id", "revision", "member_count", "next_position", "created_at_ms", "updated_at_ms"} else str(row[key]))
            for key in ("collection_id", "instance_token", "name", "revision", "member_count", "created_at_ms", "updated_at_ms")}


def list_collections(catalog: Any, *, limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
    limit = max(1, min(int(limit), 50))
    with catalog.read_snapshot():
        revision = _revision(catalog.connection)
        after = _cursor_decode(cursor, "pcl1", [revision])
        if after is not None and (not isinstance(after, list) or len(after) != 2):
            raise _fail("照片合集游标无效", status=409, code=INVALID_CURSOR)
        where = "WHERE updated_at_ms < ? OR (updated_at_ms=? AND collection_id<?)" if after else ""
        params = (*([int(after[0]), int(after[0]), int(after[1])] if after else []), limit + 1)
        rows = catalog.read_all(
            "SELECT * FROM photo_collections " + where +
            " ORDER BY updated_at_ms DESC,collection_id DESC LIMIT ?", params
        )
        more = len(rows) > limit
        rows = rows[:limit]
        items = [_summary(row) for row in rows]
        # One bounded cover query for this directory page; never one query per collection.
        if rows:
            ids = [int(row["collection_id"]) for row in rows]
            marks = ",".join("?" for _ in ids)
            covers = catalog.read_all(
                "SELECT c.collection_id,a.asset_id,a.sha256 FROM photo_collections c "
                "JOIN photo_collection_members m ON m.collection_id=c.collection_id "
                "AND m.position=(SELECT m2.position FROM photo_collection_members m2 "
                "JOIN assets a2 ON a2.asset_id=m2.asset_id AND a2.state='ACTIVE' "
                "WHERE m2.collection_id=c.collection_id "
                "AND EXISTS (SELECT 1 FROM file_locations l WHERE l.asset_id=a2.asset_id AND l.availability='PRESENT') "
                "ORDER BY m2.position LIMIT 1) "
                "JOIN assets a ON a.asset_id=m.asset_id "
                "WHERE c.collection_id IN (" + marks + ")", tuple(ids)
            )
            seen: set[int] = set()
            for cover in covers:
                cid = int(cover["collection_id"])
                if cid not in seen:
                    next(item for item in items if item["collection_id"] == cid)["cover_asset_id"] = int(cover["asset_id"])
                    seen.add(cid)
            for item in items:
                item.setdefault("cover_asset_id", None)
        return {"collections": items, "photo_collections_revision": revision,
                "has_more": more, "next_cursor": _cursor_encode("pcl1", [revision], [int(rows[-1]["updated_at_ms"]), int(rows[-1]["collection_id"])]) if more and rows else None}


def create_collection(catalog: Any, name: Any, request_id: str, *, source_hash: str = "empty",
                     asset_ids: list[int] | None = None,
                     request_payload_hash: str | None = None,
                     auth_session_hash: str | None = None,
                     source_token_hash: str | None = None,
                     expected_catalog_filter_revision: int | None = None,
                     expected_favorites_revision: int | None = None,
                     expected_photo_collection: tuple[int, str, int] | None = None,
                     expected_person_collection: tuple[int, str, int] | None = None,
                     expected_place_scope: tuple[int, str | None] | None = None) -> dict[str, Any]:
    display, key = _name(name)
    ids = list(asset_ids or [])
    if len(ids) > MAX_CREATE_IDS or any(type(value) is not int or value <= 0 for value in ids) or len(ids) != len(set(ids)):
        raise _fail("新建合集时只能导入 0～5000 个不重复的正整数照片 ID")
    photo_sql, photo_params = photo_asset_sql(asset_alias="a")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
        raise _fail("create_request_id 无效")
    payload_hash = request_payload_hash or hashlib.sha256(json.dumps([display, source_hash, ids], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    with catalog.write() as connection:
        old = connection.execute("SELECT * FROM photo_collections WHERE create_request_id=?", (request_id,)).fetchone()
        if old is not None:
            if old["create_request_hash"] != payload_hash:
                raise _fail("同一创建请求键不能用于不同内容", status=409)
            return {"collection": _summary(old), "reused": True}
        if expected_catalog_filter_revision is not None:
            from photo_search.places.revision import read_filter_revision

            if read_filter_revision(connection) != int(expected_catalog_filter_revision):
                raise _fail("搜索范围已变化，请重新搜索后保存", status=409, code="CATALOG_FILTER_REVISION_STALE")
        _check_place_scope(connection, expected_place_scope)
        if expected_favorites_revision is not None:
            from photo_search.favorites import _revision as favorite_revision

            if favorite_revision(connection) != int(expected_favorites_revision):
                raise _fail("收藏范围已变化，请重新搜索后保存", status=409, code="FAVORITES_REVISION_STALE")
        if expected_photo_collection is not None:
            scoped_id, scoped_token, scoped_revision = expected_photo_collection
            current_scope = connection.execute(
                "SELECT instance_token,revision FROM photo_collections WHERE collection_id=?", (int(scoped_id),)
            ).fetchone()
            if (current_scope is None or str(current_scope["instance_token"]) != str(scoped_token)
                    or int(current_scope["revision"]) != int(scoped_revision)):
                raise _fail("照片合集搜索范围已变化，请重新搜索", status=409, code="PHOTO_COLLECTION_REVISION_STALE")
        if expected_person_collection is not None:
            scoped_id, scoped_token, scoped_revision = expected_person_collection
            current_scope = connection.execute(
                "SELECT instance_token,revision FROM person_collections WHERE collection_id=?", (int(scoped_id),)
            ).fetchone()
            if (current_scope is None or str(current_scope["instance_token"]) != str(scoped_token)
                    or int(current_scope["revision"]) != int(scoped_revision)):
                raise _fail("人物合集搜索范围已变化，请重新搜索", status=409, code="PERSON_COLLECTION_REVISION_STALE")
        if ids:
            marks = ",".join("?" for _ in ids)
            valid = {int(row[0]) for row in connection.execute(
                "SELECT a.asset_id FROM assets a WHERE a.asset_id IN (" + marks + ") AND a.state='ACTIVE' AND " + photo_sql,
                (*ids, *photo_params))}
            if valid != set(ids):
                raise _fail("照片不存在、不可用或不符合图片资格", status=409, code="PHOTO_COLLECTION_ASSET_INVALID")
        token = secrets.token_hex(16)
        now = catalog.now_ms()
        try:
            cursor = connection.execute(
                "INSERT INTO photo_collections(instance_token,name,name_key,create_request_id,create_request_hash,create_auth_hash,create_source_token_hash,created_at_ms,updated_at_ms) "
                "VALUES(?,?,?,?,?,?,?,?,?)", (token, display, key, request_id, payload_hash, auth_session_hash,
                                               source_token_hash, now, now)
            )
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                raise _fail("照片合集中已存在同名合集", status=409) from error
            raise
        connection.execute("UPDATE metadata SET value=CAST(value AS INTEGER)+1 WHERE key='photo_collections_revision'")
        if ids:
            connection.executemany(
                "INSERT INTO photo_collection_members(collection_id,asset_id,position,added_at_ms) VALUES(?,?,?,?)",
                [(int(cursor.lastrowid), asset_id, offset + 1, now) for offset, asset_id in enumerate(ids)]
            )
            connection.execute("UPDATE photo_collections SET member_count=?,next_position=?,revision=2 WHERE collection_id=?",
                               (len(ids), len(ids)+1, int(cursor.lastrowid)))
        row = connection.execute("SELECT * FROM photo_collections WHERE collection_id=?", (cursor.lastrowid,)).fetchone()
        return {"collection": _summary(row), "reused": False}


def create_request_payload_hash(name: Any, source_hash: str) -> str:
    display, _ = _name(name)
    return hashlib.sha256(json.dumps([display, source_hash], ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def append_request_payload_hash(collection_id: int, instance_token: str, source_token: str,
                                mode: str, confirm_truncated: bool) -> str:
    source_token_hash = hashlib.sha256(str(source_token).encode()).hexdigest()
    return hashlib.sha256(json.dumps(
        [int(collection_id), str(instance_token), source_token_hash, str(mode), bool(confirm_truncated)],
        separators=(",", ":"),
    ).encode()).hexdigest()


def replay_search_append(catalog: Any, request_id: str, *, collection_id: int,
                         instance_token: str, source_token: str, mode: str,
                         confirm_truncated: bool, auth_session_id: str) -> dict[str, Any] | None:
    """Replay a committed append before depending on its ephemeral source snapshot."""
    row = catalog.read_one(
        "SELECT collection_id,instance_token,auth_session_hash,request_payload_hash,result_json "
        "FROM photo_collection_search_appends WHERE request_id=?", (request_id,)
    )
    if row is None:
        return None
    auth_hash = hashlib.sha256(str(auth_session_id).encode()).hexdigest()
    payload_hash = append_request_payload_hash(
        collection_id, instance_token, source_token, mode, confirm_truncated
    )
    if (int(row["collection_id"]) != int(collection_id)
            or str(row["instance_token"]) != str(instance_token)
            or str(row["auth_session_hash"]) != auth_hash
            or str(row["request_payload_hash"]) != payload_hash):
        raise _fail("同一追加请求键不能用于不同内容", status=409)
    result = json.loads(str(row["result_json"]))
    result["reused"] = True
    return result


def replay_search_create(catalog: Any, request_id: str, name: Any, source_token: str,
                         auth_session_id: str) -> dict[str, Any] | None:
    """Return a committed search-save result when a retry arrives after token expiry."""
    display, _ = _name(name)
    token_hash = hashlib.sha256(str(source_token).encode()).hexdigest()
    source_hash = hashlib.sha256(json.dumps(["photo_collection_search", token_hash],
                                           separators=(",", ":")).encode()).hexdigest()
    payload_hash = create_request_payload_hash(display, source_hash)
    auth_hash = hashlib.sha256(str(auth_session_id).encode()).hexdigest()
    row = catalog.read_one("SELECT * FROM photo_collections WHERE create_request_id=?", (request_id,))
    if row is None:
        return None
    if (str(row["create_request_hash"]) != payload_hash
            or str(row["create_auth_hash"] or "") != auth_hash
            or str(row["create_source_token_hash"] or "") != token_hash):
        raise _fail("同一创建请求键不能用于不同内容", status=409)
    return {"collection": _summary(row), "reused": True}


def collection_detail(catalog: Any, collection_id: int, *, instance_token: str | None = None) -> dict[str, Any]:
    with catalog.read_snapshot():
        return {"collection": _summary(_row(catalog.connection, collection_id, instance_token))}


def rename_collection(catalog: Any, collection_id: int, *, instance_token: str, expected_revision: int, name: Any) -> dict[str, Any]:
    display, key = _name(name)
    with catalog.write() as connection:
        row = _row(connection, collection_id, instance_token)
        if int(row["revision"]) != int(expected_revision):
            raise _fail("照片合集已变化，请刷新", status=409, code="PHOTO_COLLECTION_REVISION_STALE")
        if row["name"] == display:
            return {"collection": _summary(row), "changed": False}
        try:
            connection.execute("UPDATE photo_collections SET name=?,name_key=?,revision=revision+1,updated_at_ms=? WHERE collection_id=?",
                               (display, key, catalog.now_ms(), int(collection_id)))
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                raise _fail("照片合集中已存在同名合集", status=409) from error
            raise
        connection.execute("UPDATE metadata SET value=CAST(value AS INTEGER)+1 WHERE key='photo_collections_revision'")
        return {"collection": _summary(_row(connection, collection_id)), "changed": True}


def delete_collection(catalog: Any, collection_id: int, *, instance_token: str, expected_revision: int) -> dict[str, Any]:
    with catalog.write() as connection:
        row = _row(connection, collection_id, instance_token)
        if int(row["revision"]) != int(expected_revision):
            raise _fail("照片合集已变化，请刷新", status=409, code="PHOTO_COLLECTION_REVISION_STALE")
        connection.execute("DELETE FROM photo_collections WHERE collection_id=?", (int(collection_id),))
        connection.execute("UPDATE metadata SET value=CAST(value AS INTEGER)+1 WHERE key='photo_collections_revision'")
        return {"collection_id": int(collection_id), "deleted": True}


def edit_members(catalog: Any, collection_id: int, asset_ids: list[int], *, action: str,
                 instance_token: str, expected_revision: int,
                 max_batch: int = MAX_BATCH,
                 expected_catalog_filter_revision: int | None = None,
                 expected_favorites_revision: int | None = None,
                 expected_photo_collection: tuple[int, str, int] | None = None,
                 expected_person_collection: tuple[int, str, int] | None = None,
                 expected_place_scope: tuple[int, str | None] | None = None,
                 idempotency: tuple[str, str, str] | None = None) -> dict[str, Any]:
    if action not in {"add", "remove"} or not 1 <= len(asset_ids) <= int(max_batch):
        raise _fail(f"操作必须为 add/remove 且每批为 1～{int(max_batch)} 张")
    if any(type(asset_id) is not int or asset_id <= 0 for asset_id in asset_ids) or len(set(asset_ids)) != len(asset_ids):
        raise _fail("asset_ids 必须为不重复的正整数")
    photo_sql, photo_params = photo_asset_sql(asset_alias="a")
    placeholders = ",".join("?" for _ in asset_ids)
    with catalog.write() as connection:
        row = _row(connection, collection_id, instance_token)
        if idempotency is not None:
            request_id, auth_hash, payload_hash = idempotency
            old = connection.execute(
                "SELECT collection_id,instance_token,auth_session_hash,request_payload_hash,result_json "
                "FROM photo_collection_search_appends WHERE request_id=?", (request_id,)
            ).fetchone()
            if old is not None:
                if (int(old["collection_id"]) != int(collection_id)
                        or str(old["instance_token"]) != str(instance_token)
                        or str(old["auth_session_hash"]) != auth_hash
                        or str(old["request_payload_hash"]) != payload_hash):
                    raise _fail("同一追加请求键不能用于不同内容", status=409)
                replayed = json.loads(str(old["result_json"]))
                replayed["reused"] = True
                return replayed
        if int(row["revision"]) != int(expected_revision):
            raise _fail("照片合集已变化，请刷新", status=409, code="PHOTO_COLLECTION_REVISION_STALE")
        if expected_catalog_filter_revision is not None:
            from photo_search.places.revision import read_filter_revision

            if read_filter_revision(connection) != int(expected_catalog_filter_revision):
                raise _fail("搜索范围已变化，请重新搜索后保存", status=409, code="CATALOG_FILTER_REVISION_STALE")
        _check_place_scope(connection, expected_place_scope)
        if expected_favorites_revision is not None:
            from photo_search.favorites import _revision as favorite_revision

            if favorite_revision(connection) != int(expected_favorites_revision):
                raise _fail("收藏范围已变化，请重新搜索后保存", status=409, code="FAVORITES_REVISION_STALE")
        if expected_photo_collection is not None:
            scoped_id, scoped_token, scoped_revision = expected_photo_collection
            current_scope = connection.execute(
                "SELECT instance_token,revision FROM photo_collections WHERE collection_id=?", (int(scoped_id),)
            ).fetchone()
            if (current_scope is None or str(current_scope["instance_token"]) != str(scoped_token)
                    or int(current_scope["revision"]) != int(scoped_revision)):
                raise _fail("照片合集搜索范围已变化，请重新搜索", status=409, code="PHOTO_COLLECTION_REVISION_STALE")
        if expected_person_collection is not None:
            scoped_id, scoped_token, scoped_revision = expected_person_collection
            current_scope = connection.execute(
                "SELECT instance_token,revision FROM person_collections WHERE collection_id=?", (int(scoped_id),)
            ).fetchone()
            if (current_scope is None or str(current_scope["instance_token"]) != str(scoped_token)
                    or int(current_scope["revision"]) != int(scoped_revision)):
                raise _fail("人物合集搜索范围已变化，请重新搜索", status=409, code="PERSON_COLLECTION_REVISION_STALE")
        valid = {int(r[0]) for r in connection.execute(
            "SELECT a.asset_id FROM assets a WHERE a.asset_id IN (" + placeholders + ") AND a.state='ACTIVE' AND " + photo_sql,
            (*asset_ids, *photo_params)
        )}
        if valid != set(asset_ids):
            raise _fail("照片不存在、不可用或不符合图片资格", status=409, code="PHOTO_COLLECTION_ASSET_INVALID")
        if action == "add":
            existing = {int(r[0]) for r in connection.execute(
                "SELECT asset_id FROM photo_collection_members WHERE collection_id=? AND asset_id IN (" + placeholders + ")",
                (int(collection_id), *asset_ids))}
            changed_ids = [asset_id for asset_id in asset_ids if asset_id not in existing]
            if changed_ids:
                start = int(row["next_position"])
                now = catalog.now_ms()
                connection.executemany(
                    "INSERT INTO photo_collection_members(collection_id,asset_id,position,added_at_ms) VALUES(?,?,?,?)",
                    [(int(collection_id), aid, start + offset, now) for offset, aid in enumerate(changed_ids)]
                )
                connection.execute("UPDATE photo_collections SET member_count=member_count+?,next_position=next_position+?,revision=revision+1,updated_at_ms=? WHERE collection_id=?",
                                   (len(changed_ids), len(changed_ids), now, int(collection_id)))
        else:
            existing = {int(r[0]) for r in connection.execute(
                "SELECT asset_id FROM photo_collection_members WHERE collection_id=? AND asset_id IN (" + placeholders + ")",
                (int(collection_id), *asset_ids))}
            changed_ids = [asset_id for asset_id in asset_ids if asset_id in existing]
            if changed_ids:
                marks = ",".join("?" for _ in changed_ids)
                connection.execute("DELETE FROM photo_collection_members WHERE collection_id=? AND asset_id IN (" + marks + ")",
                                   (int(collection_id), *changed_ids))
                now = catalog.now_ms()
                connection.execute("UPDATE photo_collections SET member_count=member_count-?,revision=revision+1,updated_at_ms=? WHERE collection_id=?",
                                   (len(changed_ids), now, int(collection_id)))
        if changed_ids:
            connection.execute("UPDATE metadata SET value=CAST(value AS INTEGER)+1 WHERE key='photo_collections_revision'")
        result = {"collection": _summary(_row(connection, collection_id)), "action": action,
                  "requested": len(asset_ids), "changed": len(changed_ids),
                  "unchanged": len(asset_ids)-len(changed_ids), "changed_asset_ids": changed_ids}
        if idempotency is not None:
            request_id, auth_hash, payload_hash = idempotency
            connection.execute(
                "INSERT INTO photo_collection_search_appends(request_id,collection_id,instance_token,"
                "auth_session_hash,request_payload_hash,result_json,created_at_ms) VALUES(?,?,?,?,?,?,?)",
                (request_id, int(collection_id), str(instance_token), auth_hash, payload_hash,
                 json.dumps(result, separators=(",", ":")), catalog.now_ms()),
            )
        return result


def assets_page(catalog: Any, cache: Any, collection_id: int, *, instance_token: str,
                limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
    from photo_search.api.queries import locations_by_asset, summarize_asset

    limit = max(1, min(int(limit), 50))
    with catalog.read_snapshot():
        collection = _row(catalog.connection, collection_id, instance_token)
        binding = [int(collection_id), str(instance_token), int(collection["revision"])]
        after = _cursor_decode(cursor, "pca1", binding)
        if after is not None and (not isinstance(after, list) or len(after) != 2):
            raise _fail("照片合集游标无效", status=409, code=INVALID_CURSOR)
        where = " AND (m.position>? OR (m.position=? AND m.asset_id>?))" if after else ""
        params: tuple[Any, ...] = (int(collection_id), *([int(after[0]), int(after[0]), int(after[1])] if after else []), limit+1)
        rows = catalog.read_all(
            "SELECT a.asset_id,a.sha256,a.byte_size,a.created_at,m.position FROM photo_collection_members m "
            "JOIN assets a ON a.asset_id=m.asset_id WHERE m.collection_id=?" + where +
            " ORDER BY m.position,m.asset_id LIMIT ?", params
        )
        more = len(rows) > limit
        rows = rows[:limit]
        ids = [int(r["asset_id"]) for r in rows]
        locations = locations_by_asset(catalog, ids)
        assets = [summarize_asset(cache, asset_id=int(r["asset_id"]), sha256=str(r["sha256"]),
                  byte_size=int(r["byte_size"]), created_at=int(r["created_at"]),
                  locations=locations.get(int(r["asset_id"]), [])) for r in rows]
        for asset, row in zip(assets, rows, strict=True):
            asset["collection_position"] = int(row["position"])
        last = rows[-1] if rows else None
        return {"collection": _summary(collection), "assets": assets,
                "has_more": more, "next_cursor": _cursor_encode("pca1", binding,
                    [int(last["position"]), int(last["asset_id"])]) if more and last else None}
