"""Dynamic smart photo collections and their bounded, rebuildable ranked snapshots."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
import threading
import unicodedata
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from photo_search.api.errors import INVALID_CURSOR, VALIDATION_ERROR, ApiError

MAX_NAME = 80
MAX_CANDIDATES = 5000
MAX_PAGE = 50
SMART_RANKING_CONTRACT_VERSION = "photo-search-ranking-v1"
SMART_QUERY_PLANNING_VERSION = "unified-query-plan-v1"


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
        value = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        if not isinstance(value, list) or value[:-1] != [prefix, *binding]:
            raise ValueError
        return value[-1]
    except (ValueError, TypeError, UnicodeError, binascii.Error) as error:
        raise _fail("智能合集内容已变化，请刷新", status=409, code=INVALID_CURSOR) from error


def rule_from_search(source: dict[str, Any]) -> dict[str, Any]:
    """Freeze only server-registered, Pydantic-validated search fields as the rule."""
    from photo_search.api.models import PlaceAwareSearchRequestModel

    submitted = source.get("payload")
    confirmed = source.get("confirmed_execution")
    if not isinstance(submitted, dict) or not isinstance(confirmed, dict):
        raise _fail("无法恢复服务端确认的搜索规则，请重新搜索后保存", code="PHOTO_SMART_RULE_UNRECOVERABLE")
    payload = PlaceAwareSearchRequestModel.model_validate(submitted)
    execution_mode = confirmed.get("mode")
    retrieval_mode = confirmed.get("retrieval_mode")
    confirmed_plan = confirmed.get("intent_plan")
    confirmed_query = confirmed.get("content_query")
    if execution_mode == "all_filter_matches":
        kind, query = "FILTER", None
    elif execution_mode == "ranked_candidates" and retrieval_mode == "text":
        # The text endpoint searches original_query even when the submitted payload
        # leaves both `text` and `content_query` empty.
        query = payload.original_query.strip()
        if not query:
            raise _fail("文字搜索缺少可恢复的原始查询，不能保存智能合集", code="PHOTO_SMART_RULE_UNRECOVERABLE")
        kind = "TEXT"
    elif execution_mode == "ranked_candidates" and retrieval_mode == "combined":
        query = str(confirmed_query or (confirmed_plan or {}).get("content_query") or "").strip()
        if not query:
            raise _fail("综合搜索缺少服务端确认的内容查询，不能保存智能合集", code="PHOTO_SMART_RULE_UNRECOVERABLE")
        kind = "COMBINED"
    else:
        raise _fail("搜索执行路径无法恢复，请重新搜索后保存", code="PHOTO_SMART_RULE_UNRECOVERABLE")

    values = payload.model_dump(exclude={"request_id", "search_session_id", "cursor"})
    if retrieval_mode == "combined":
        if confirmed_plan is not None:
            if not isinstance(confirmed_plan, dict):
                raise _fail("服务端确认的搜索计划无效，不能保存智能合集", code="PHOTO_SMART_RULE_UNRECOVERABLE")
            values["intent_plan"] = confirmed_plan
        elif kind == "COMBINED":
            # A ranked combined query without a frozen structured plan cannot be
            # replayed safely. Do not fall back to parsing original_query later.
            raise _fail("综合搜索缺少已确认计划，不能保存智能合集", code="PHOTO_SMART_RULE_UNRECOVERABLE")
    if query is not None:
        values["content_query"] = query
        if retrieval_mode == "text":
            values["text"] = query
    values["request_id"] = "photo-smart-frozen-rule"
    payload = PlaceAwareSearchRequestModel.model_validate(values)
    values = payload.model_dump(exclude={"request_id", "search_session_id", "cursor"})
    # Re-evaluation always follows the confirmed structured request, never a browser SQL fragment.
    return {
        "version": 1,
        "execution_kind": kind,
        "request": values,
        "display_query": payload.original_query,
        "content_query": query,
        "confirmed_execution_mode": execution_mode,
        "confirmed_scope": source.get("search_scope"),
        "confirmed_place_scope": source.get("place_scope"),
        "confirmed_person_scope": source.get("collection_scope"),
        "confirmed_filter_revision": source.get("catalog_filter_revision"),
        "confirmed_suggestion_revision": source.get("suggestion_revision"),
        "confirmed_favorites_revision": source.get("favorites_revision"),
        "search_binding": source.get("search_binding"),
    }


def _rule_hash(rule: dict[str, Any]) -> tuple[str, str]:
    raw = json.dumps(rule, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def _directory_revision(connection: Any) -> int:
    row = connection.execute(
        "SELECT value FROM metadata WHERE key='photo_smart_collections_revision'"
    ).fetchone()
    if row is None:
        raise _fail("智能合集目录版本缺失", status=500)
    return int(row[0])


def dependency_signature(connection: Any, rule: dict[str, Any]) -> tuple[str, bool, dict[str, Any] | None]:
    from photo_search.places.revision import read_filter_revision, read_suggestion_revision
    from photo_search.search.tokens import FTS_GENERATION_ID, TOKENIZER_VERSION

    kind = str(rule.get("execution_kind") or "")
    active_profile = connection.execute(
        "SELECT p.profile_id,p.space_id,p.image_generation_id,p.fts_generation_id,g.dimension,"
        "p.embedding_processor_id,p.query_preprocess_version,p.query_parser_version,"
        "p.fts_tokenizer_version,p.reranker_processor_id,p.fusion_config_json,p.runtime_package_id "
        "FROM active_search_profile a JOIN search_profiles p USING(profile_id) "
        "JOIN index_generations g ON g.generation_id=p.image_generation_id WHERE a.state_id=1"
    ).fetchone()
    active_binding = None if active_profile is None else dict(active_profile)
    pinned_binding = rule.get("search_binding") if kind == "COMBINED" else None
    profile = pinned_binding or active_binding
    generation = None if profile is None else str(profile.get("image_generation_id") or "")
    vector_revision = 0 if not generation else int(connection.execute(
        "SELECT COALESCE((SELECT revision FROM photo_smart_vector_publish_revisions WHERE generation_id=?),0)",
        (generation,),
    ).fetchone()[0])
    pending_vectors = False if not generation else bool(connection.execute(
        "SELECT EXISTS(SELECT 1 FROM index_outbox WHERE generation_id=? AND state<>'CONFIRMED')",
        (generation,),
    ).fetchone()[0])
    pending_text = bool(connection.execute("SELECT EXISTS(SELECT 1 FROM search_projection_queue)").fetchone()[0])
    fts_revision = int(connection.execute(
        "SELECT value FROM metadata WHERE key='photo_smart_fts_publish_revision'"
    ).fetchone()[0])
    taken_revision = int(connection.execute(
        "SELECT value FROM metadata WHERE key='taken_time_rules_version'"
    ).fetchone()[0])
    active_place_dataset = connection.execute(
        "SELECT a.dataset_id,d.content_sha256,a.revision FROM active_geo_dataset a "
        "LEFT JOIN geo_datasets d USING(dataset_id) WHERE a.singleton=1"
    ).fetchone()
    map_revision_row = connection.execute(
        "SELECT revision FROM map_member_revision WHERE singleton=1"
    ).fetchone()
    place_identity = {
        "suggestion_revision": read_suggestion_revision(connection),
        "active_dataset": None if active_place_dataset is None else list(active_place_dataset),
        "map_member_revision": None if map_revision_row is None else int(map_revision_row[0]),
        "confirmed_scope": rule.get("confirmed_scope"),
        "confirmed_place_scope": rule.get("confirmed_place_scope"),
        "confirmed_person_scope": rule.get("confirmed_person_scope"),
    }
    binding = None if profile is None else profile
    text_profile_semantics = None if active_binding is None else {
        key: active_binding[key] for key in
        ("query_preprocess_version", "query_parser_version", "fts_tokenizer_version", "fts_generation_id")
    }
    identity = [read_filter_revision(connection), fts_revision if kind in {"TEXT", "COMBINED"} else 0,
                FTS_GENERATION_ID if kind in {"TEXT", "COMBINED"} else None,
                TOKENIZER_VERSION if kind in {"TEXT", "COMBINED"} else None,
                SMART_QUERY_PLANNING_VERSION if kind in {"TEXT", "COMBINED"} else None,
                SMART_RANKING_CONTRACT_VERSION if kind in {"TEXT", "COMBINED"} else None,
                vector_revision if kind == "COMBINED" else 0,
                binding if kind == "COMBINED" else None,
                active_binding if kind == "COMBINED" else None,
                text_profile_semantics if kind == "TEXT" else None, taken_revision,
                place_identity, rule.get("execution_kind")]
    signature = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return signature, bool((pending_text if kind in {"TEXT", "COMBINED"} else False) or
                           (pending_vectors if kind == "COMBINED" else False)), active_binding


def session_coverage(session: Any) -> tuple[bool, bool]:
    """Return persisted cache coverage flags from the actual frozen search session."""
    channels = getattr(session, "channels", {}) or {}
    projection = getattr(session, "search_projection", {}) or {}
    truncated = bool(
        getattr(session, "candidates_truncated", False)
        or (channels.get("fts") or {}).get("truncated", False)
        or (channels.get("vector") or {}).get("truncated", False)
    )
    indexing = bool(
        getattr(session, "pending_semantic", None)
        or str(projection.get("state", "SYNCED")) != "SYNCED"
    )
    return indexing, truncated


def _freshness(collection: Any, cache: Any, current_signature: str | None = None) -> str:
    if cache is None or not cache["build_id"]:
        return "NO_CACHE"
    if int(cache["cache_rule_revision"] or 0) != int(collection["rule_revision"]):
        return "STALE"
    if current_signature is not None and str(cache["dependency_signature"] or "") != current_signature:
        return "STALE"
    if cache["refresh_status"] in {"QUEUED", "RUNNING"}:
        return "REFRESHING"
    return "CURRENT"


def _summary(collection: Any, cache: Any, current_signature: str | None = None) -> dict[str, Any]:
    return {
        "smart_id": int(collection["smart_id"]),
        "instance_token": str(collection["instance_token"]),
        "name": str(collection["name"]),
        "execution_kind": str(collection["execution_kind"]),
        "revision": int(collection["revision"]),
        "rule_revision": int(collection["rule_revision"]),
        "exclusions_revision": int(collection["exclusions_revision"]),
        "rule_summary": str(json.loads(collection["rule_json"]).get("display_query") or "筛选条件"),
        "candidate_count": 0 if cache is None else int(cache["candidate_count"]),
        "truncated": False if cache is None else bool(cache["truncated"]),
        "indexing": False if cache is None else bool(cache["indexing"]),
        "refreshed_at_ms": None if cache is None or cache["refreshed_at_ms"] is None else int(cache["refreshed_at_ms"]),
        "freshness": _freshness(collection, cache, current_signature),
        "refresh_status": "NO_CACHE" if cache is None else str(cache["refresh_status"]),
        "last_error_code": None if cache is None else cache["last_error_code"],
    }


def _get(catalog: Any, smart_id: int, instance_token: str | None = None) -> Any:
    row = catalog.read_one("SELECT * FROM photo_smart_collections WHERE smart_id=?", (int(smart_id),))
    if row is None:
        raise _fail("智能合集不存在", status=404, code="PHOTO_SMART_COLLECTION_NOT_FOUND")
    if instance_token is not None and str(row["instance_token"]) != str(instance_token):
        raise _fail("智能合集实例已变化，请刷新", status=409, code="PHOTO_SMART_COLLECTION_STALE")
    return row


def _cache(catalog: Any, smart_id: int) -> Any:
    return catalog.read_one("SELECT * FROM photo_smart_cache_state WHERE smart_id=?", (int(smart_id),))


def list_collections(catalog: Any, *, limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
    limit = max(1, min(int(limit), 50))
    with catalog.read_snapshot():
        revision = _directory_revision(catalog.connection)
        after = _cursor_decode(cursor, "psc1", [revision])
        if after is not None and (not isinstance(after, list) or len(after) != 2):
            raise _fail("智能合集目录游标无效", status=409, code=INVALID_CURSOR)
        where = "WHERE c.updated_at_ms < ? OR (c.updated_at_ms=? AND c.smart_id<?)" if after else ""
        params = (*([int(after[0]), int(after[0]), int(after[1])] if after else []), limit + 1)
        rows = catalog.read_all(
            "SELECT c.*,s.build_id,s.dependency_signature,s.cache_rule_revision,s.candidate_count,"
            "s.truncated,s.indexing,s.refreshed_at_ms,s.last_error_code,s.refresh_status "
            "FROM photo_smart_collections c LEFT JOIN photo_smart_cache_state s USING(smart_id) "
            + where + " ORDER BY c.updated_at_ms DESC,c.smart_id DESC LIMIT ?", params
        )
        more = len(rows) > limit
        rows = rows[:limit]
        items = [_summary(row, row if row["refresh_status"] is not None else None) for row in rows]
        # The directory is deliberately metadata-only; exact environment freshness is
        # checked once on detail entry, never via one signature query per listed rule.
        for item in items:
            if item["freshness"] == "CURRENT":
                item["freshness"] = "CHECK_ON_OPEN"
        return {"collections": items, "photo_smart_collections_revision": revision,
                "has_more": more,
                "next_cursor": _cursor_encode("psc1", [revision], [int(rows[-1]["updated_at_ms"]), int(rows[-1]["smart_id"])]) if more and rows else None}


def create_collection(catalog: Any, name: Any, request_id: str, rule: dict[str, Any],
                      *, request_hash: str, auth_hash: str, source_hash: str) -> dict[str, Any]:
    display, key = _name(name)
    rule_json, digest = _rule_hash(rule)
    if len(request_id) > 128 or not request_id.strip():
        raise _fail("create_request_id 无效")
    with catalog.write() as connection:
        old = connection.execute("SELECT * FROM photo_smart_collections WHERE create_request_id=?", (request_id,)).fetchone()
        if old is not None:
            if str(old["create_request_hash"]) != request_hash or str(old["create_auth_hash"]) != auth_hash or str(old["create_source_hash"]) != source_hash:
                raise _fail("同一创建请求键不能用于不同内容", status=409, code="PHOTO_SMART_IDEMPOTENCY_CONFLICT")
            return {"collection": _summary(old, _cache(catalog, int(old["smart_id"]))), "reused": True}
        now = catalog.now_ms()
        token = secrets.token_hex(16)
        try:
            cur = connection.execute(
                "INSERT INTO photo_smart_collections(instance_token,name,name_key,rule_version,rule_json,rule_hash,execution_kind,create_request_id,create_request_hash,create_auth_hash,create_source_hash,created_at_ms,updated_at_ms) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (token, display, key, 1, rule_json, digest, rule["execution_kind"], request_id, request_hash, auth_hash, source_hash, now, now),
            )
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                raise _fail("智能合集中已存在同名合集", status=409, code="PHOTO_SMART_NAME_CONFLICT") from error
            raise
        smart_id = int(cur.lastrowid)
        connection.execute("INSERT INTO photo_smart_cache_state(smart_id) VALUES(?)", (smart_id,))
        connection.execute("UPDATE metadata SET value=CAST(value AS INTEGER)+1 WHERE key='photo_smart_collections_revision'")
        row = connection.execute("SELECT * FROM photo_smart_collections WHERE smart_id=?", (smart_id,)).fetchone()
        return {"collection": _summary(row, None), "reused": False}


def detail(catalog: Any, smart_id: int, *, instance_token: str | None = None) -> dict[str, Any]:
    with catalog.read_snapshot():
        row = _get(catalog, smart_id, instance_token)
        cache = _cache(catalog, smart_id)
        rule = json.loads(row["rule_json"])
        signature, indexing, current_profile = dependency_signature(catalog.connection, rule)
        summary = _summary(row, cache, signature)
        pinned = rule.get("search_binding") if str(row["execution_kind"]) == "COMBINED" else None
        if pinned and (current_profile is None or any(
                pinned.get(key) != current_profile.get(key) for key in
                ("profile_id", "space_id", "image_generation_id", "dimension"))):
            summary["freshness"] = "MODEL_REBIND_REQUIRED"
        return {"collection": summary, "rule": {
            "version": int(row["rule_version"]), "execution_kind": str(row["execution_kind"]),
            "display_query": rule.get("display_query"), "content_query": rule.get("content_query"),
            "request": rule.get("request"), "confirmed_scope": rule.get("confirmed_scope"),
        }, "build_id": None if cache is None else cache["build_id"],
        "current_search_binding": current_profile, "indexing": indexing}


def rename_collection(catalog: Any, smart_id: int, *, instance_token: str,
                      expected_revision: int, name: Any) -> dict[str, Any]:
    display, key = _name(name)
    with catalog.write() as connection:
        row = _get(catalog, smart_id, instance_token)
        if int(row["revision"]) != int(expected_revision):
            raise _fail("智能合集已变化，请刷新", status=409, code="PHOTO_SMART_REVISION_CONFLICT")
        if display == str(row["name"]):
            return {"collection": _summary(row, _cache(catalog, smart_id)), "changed": False}
        try:
            connection.execute("UPDATE photo_smart_collections SET name=?,name_key=?,revision=revision+1,updated_at_ms=? WHERE smart_id=?",
                               (display, key, catalog.now_ms(), int(smart_id)))
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                raise _fail("智能合集中已存在同名合集", status=409, code="PHOTO_SMART_NAME_CONFLICT") from error
            raise
        connection.execute("UPDATE metadata SET value=CAST(value AS INTEGER)+1 WHERE key='photo_smart_collections_revision'")
        row = connection.execute("SELECT * FROM photo_smart_collections WHERE smart_id=?", (int(smart_id),)).fetchone()
        return {"collection": _summary(row, _cache(catalog, smart_id)), "changed": True}


def delete_collection(catalog: Any, smart_id: int, *, instance_token: str, expected_revision: int) -> dict[str, Any]:
    with catalog.write() as connection:
        row = _get(catalog, smart_id, instance_token)
        if int(row["revision"]) != int(expected_revision):
            raise _fail("智能合集已变化，请刷新", status=409, code="PHOTO_SMART_REVISION_CONFLICT")
        connection.execute("DELETE FROM photo_smart_collections WHERE smart_id=?", (int(smart_id),))
        connection.execute("UPDATE metadata SET value=CAST(value AS INTEGER)+1 WHERE key='photo_smart_collections_revision'")
        return {"deleted": True, "smart_id": int(smart_id)}


def replay_rule_replacement(catalog: Any, request_id: str, *, smart_id: int,
                            auth_hash: str, request_hash: str) -> dict[str, Any] | None:
    row = catalog.read_one("SELECT smart_id,auth_hash,request_hash,result_json FROM photo_smart_rule_replacements WHERE request_id=?",
                           (str(request_id),))
    if row is None:
        return None
    if int(row["smart_id"]) != int(smart_id) or str(row["auth_hash"]) != auth_hash or str(row["request_hash"]) != request_hash:
        raise _fail("同一替换请求键不能用于不同内容", status=409, code="PHOTO_SMART_IDEMPOTENCY_CONFLICT")
    return json.loads(row["result_json"])


def replace_rule(catalog: Any, smart_id: int, *, instance_token: str, expected_revision: int,
                 rule: dict[str, Any], request_id: str, auth_hash: str,
                 request_hash: str) -> dict[str, Any]:
    rule_json, digest = _rule_hash(rule)
    with catalog.write() as connection:
        existing = connection.execute("SELECT smart_id,auth_hash,request_hash,result_json FROM photo_smart_rule_replacements WHERE request_id=?",
                                      (str(request_id),)).fetchone()
        if existing is not None:
            if int(existing["smart_id"]) != int(smart_id) or str(existing["auth_hash"]) != auth_hash or str(existing["request_hash"]) != request_hash:
                raise _fail("同一替换请求键不能用于不同内容", status=409, code="PHOTO_SMART_IDEMPOTENCY_CONFLICT")
            return json.loads(existing["result_json"])
        collection = _get(catalog, smart_id, instance_token)
        if int(collection["revision"]) != int(expected_revision):
            raise _fail("智能合集已变化，请刷新", status=409, code="PHOTO_SMART_REVISION_CONFLICT")
        changed = str(collection["rule_hash"]) != digest
        now = catalog.now_ms()
        if changed:
            connection.execute(
                "UPDATE photo_smart_collections SET rule_version=1,rule_json=?,rule_hash=?,execution_kind=?,revision=revision+1,rule_revision=rule_revision+1,updated_at_ms=? WHERE smart_id=?",
                (rule_json, digest, rule["execution_kind"], now, int(smart_id)))
            connection.execute("UPDATE metadata SET value=CAST(value AS INTEGER)+1 WHERE key='photo_smart_collections_revision'")
        row = connection.execute("SELECT * FROM photo_smart_collections WHERE smart_id=?", (int(smart_id),)).fetchone()
        cache = connection.execute("SELECT * FROM photo_smart_cache_state WHERE smart_id=?", (int(smart_id),)).fetchone()
        result = {"collection": _summary(row, cache), "changed": changed,
                  "exclusions_preserved": int(row["exclusions_revision"]) > 0}
        connection.execute("INSERT INTO photo_smart_rule_replacements(request_id,smart_id,auth_hash,request_hash,result_json,created_at_ms) VALUES(?,?,?,?,?,?)",
                           (str(request_id), int(smart_id), auth_hash, request_hash,
                            json.dumps(result, ensure_ascii=False, separators=(",", ":")), now))
        return result


def publish_candidates(catalog: Any, smart_id: int, asset_ids: list[int], *, signature: str,
                       truncated: bool = False, indexing: bool = False,
                       expected_rule_revision: int | None = None,
                       lease_owner: str | None = None) -> dict[str, Any]:
    """Atomically replace one bounded derived snapshot; no long computation holds this write."""
    if len(asset_ids) > MAX_CANDIDATES or len(set(asset_ids)) != len(asset_ids):
        raise _fail("智能合集候选快照超过 5000 或含重复照片", code="PHOTO_SMART_CACHE_INVALID")
    from photo_search.catalog.video import photo_asset_sql

    build_id = secrets.token_hex(16)
    photo_sql, photo_params = photo_asset_sql(asset_alias="a")
    with catalog.write() as connection:
        collection = _get(catalog, smart_id)
        if expected_rule_revision is not None and int(collection["rule_revision"]) != int(expected_rule_revision):
            raise _fail("智能合集规则在刷新期间已变化，旧结果未发布", status=409,
                        code="PHOTO_SMART_RULE_CHANGED")
        current_signature, current_indexing, _ = dependency_signature(connection, json.loads(collection["rule_json"]))
        if current_signature != signature:
            raise _fail("搜索依赖在刷新期间已变化，旧结果未发布", status=409,
                        code="PHOTO_SMART_DEPENDENCY_CHANGED")
        indexing = bool(indexing or current_indexing)
        if asset_ids:
            marks = ",".join("?" for _ in asset_ids)
            valid = {int(row[0]) for row in connection.execute(
                f"SELECT a.asset_id FROM assets a WHERE a.asset_id IN ({marks}) AND a.state='ACTIVE' AND {photo_sql}",
                (*asset_ids, *photo_params))}
            if valid != set(asset_ids):
                raise _fail("候选包含不存在或不合格的照片", status=409, code="PHOTO_SMART_CACHE_INVALID")
        state = connection.execute("SELECT * FROM photo_smart_cache_state WHERE smart_id=?", (int(smart_id),)).fetchone()
        if lease_owner is not None and (
            state is None
            or str(state["lease_owner"] or "") != str(lease_owner)
            or int(state["lease_until_ms"] or 0) <= catalog.now_ms()
            or (expected_rule_revision is not None
                and int(collection["rule_revision"]) != int(expected_rule_revision))
        ):
            raise _fail("刷新租约已失效，旧工作未发布", status=409, code="PHOTO_SMART_LEASE_EXPIRED")
        connection.execute("DELETE FROM photo_smart_cache_entries WHERE smart_id=?", (int(smart_id),))
        connection.executemany(
            "INSERT INTO photo_smart_cache_entries(smart_id,build_id,rank,asset_id) VALUES(?,?,?,?)",
            [(int(smart_id), build_id, rank, int(asset_id)) for rank, asset_id in enumerate(asset_ids, 1)],
        )
        connection.execute(
            "UPDATE photo_smart_cache_state SET build_id=?,dependency_signature=?,cache_rule_revision=?,candidate_count=?,truncated=?,indexing=?,refreshed_at_ms=?,last_error_code=NULL,lease_owner=NULL,lease_until_ms=NULL,refresh_status='NO_CACHE' WHERE smart_id=?",
            (build_id, signature, int(collection["rule_revision"]), len(asset_ids), int(truncated), int(indexing), catalog.now_ms(), int(smart_id)),
        )
        return {"build_id": build_id, "candidate_count": len(asset_ids), "truncated": bool(truncated),
                "indexing": bool(indexing), "previous_build_id": None if state is None else state["build_id"]}


def exclusions_page(catalog: Any, smart_id: int, *, instance_token: str, limit: int = 50,
                    cursor: str | None = None) -> dict[str, Any]:
    limit = max(1, min(int(limit), MAX_PAGE))
    with catalog.read_snapshot():
        row = _get(catalog, smart_id, instance_token)
        revision = int(row["exclusions_revision"])
        binding = [int(smart_id), str(instance_token), revision]
        after = _cursor_decode(cursor, "pse1", binding)
        if after is not None and (not isinstance(after, list) or len(after) != 2):
            raise _fail("已排除游标无效", status=409, code=INVALID_CURSOR)
        where = "WHERE e.smart_id=?"
        params: list[Any] = [int(smart_id)]
        if after:
            where += " AND (e.created_at_ms<? OR (e.created_at_ms=? AND e.asset_id<?))"
            params.extend([int(after[0]), int(after[0]), int(after[1])])
        params.append(limit + 1)
        rows = catalog.read_all(
            "SELECT e.asset_id,e.created_at_ms,a.sha256,a.state,"
            "(SELECT l.relative_path FROM file_locations l WHERE l.asset_id=e.asset_id ORDER BY l.availability='PRESENT' DESC,l.location_id LIMIT 1) display_name,"
            "EXISTS(SELECT 1 FROM file_locations lp WHERE lp.asset_id=e.asset_id AND lp.availability='PRESENT') has_present_location "
            "FROM photo_smart_exclusions e LEFT JOIN assets a ON a.asset_id=e.asset_id " + where +
            " ORDER BY e.created_at_ms DESC,e.asset_id DESC LIMIT ?", tuple(params))
        more = len(rows) > limit
        rows = rows[:limit]
        return {"assets": [{"asset_id": int(x["asset_id"]), "created_at_ms": int(x["created_at_ms"]),
                            "available": bool(x["has_present_location"]), "filename": x["display_name"] or "照片当前不可用",
                            # The web shell prefixes API to this endpoint-relative path.
                            "thumbnail_url": f"/assets/{int(x['asset_id'])}/thumbnail" if x["has_present_location"] else None}
                           for x in rows], "has_more": more,
                "next_cursor": _cursor_encode("pse1", binding, [int(rows[-1]["created_at_ms"]), int(rows[-1]["asset_id"])]) if more and rows else None,
                "exclusions_revision": revision}


def set_exclusion(catalog: Any, smart_id: int, asset_id: int, *, instance_token: str,
                  expected_revision: int, excluded: bool) -> dict[str, Any]:
    from photo_search.catalog.video import photo_asset_sql

    photo_sql, photo_params = photo_asset_sql(asset_alias="a")
    with catalog.write() as connection:
        collection = _get(catalog, smart_id, instance_token)
        if int(collection["revision"]) != int(expected_revision):
            raise _fail("智能合集已变化，请刷新", status=409, code="PHOTO_SMART_REVISION_CONFLICT")
        if excluded:
            valid = connection.execute(
                f"SELECT 1 FROM assets a WHERE a.asset_id=? AND a.state='ACTIVE' AND {photo_sql}",
                (int(asset_id), *photo_params)).fetchone()
            if valid is None:
                raise _fail("照片不存在或不符合图片资格", status=404, code="PHOTO_SMART_ASSET_NOT_FOUND")
            cur = connection.execute(
                "INSERT OR IGNORE INTO photo_smart_exclusions(smart_id,asset_id,created_at_ms) VALUES(?,?,?)",
                (int(smart_id), int(asset_id), catalog.now_ms()))
        else:
            cur = connection.execute("DELETE FROM photo_smart_exclusions WHERE smart_id=? AND asset_id=?",
                                     (int(smart_id), int(asset_id)))
        changed = cur.rowcount > 0
        if changed:
            connection.execute("UPDATE photo_smart_collections SET exclusions_revision=exclusions_revision+1,revision=revision+1,updated_at_ms=? WHERE smart_id=?",
                               (catalog.now_ms(), int(smart_id)))
            connection.execute("UPDATE metadata SET value=CAST(value AS INTEGER)+1 WHERE key='photo_smart_collections_revision'")
        current = connection.execute("SELECT * FROM photo_smart_collections WHERE smart_id=?", (int(smart_id),)).fetchone()
        return {"changed": changed, "excluded": bool(excluded), "revision": int(current["revision"]),
                "exclusions_revision": int(current["exclusions_revision"])}


def ranked_assets_page(catalog: Any, cache: Any, smart_id: int, *, instance_token: str,
                       limit: int = 50, cursor: str | None = None,
                       order: str = "rank", eligible_asset_ids: list[int] | None = None) -> dict[str, Any]:
    from photo_search.api.queries import locations_by_asset, summarize_asset
    from photo_search.catalog.video import photo_asset_sql

    limit = max(1, min(int(limit), MAX_PAGE))
    if order not in {"rank", "capture_desc"}:
        raise _fail("智能合集排序无效")
    with catalog.read_snapshot():
        collection = _get(catalog, smart_id, instance_token)
        state = _cache(catalog, smart_id)
        if state is None or not state["build_id"]:
            return {"collection": _summary(collection, state), "assets": [], "has_more": False,
                    "next_cursor": None, "freshness": "NO_CACHE", "coverage": "BOUNDED"}
        rule = json.loads(collection["rule_json"])
        signature, indexing, _ = dependency_signature(catalog.connection, rule)
        freshness = _freshness(collection, state, signature)
        if freshness != "CURRENT" and eligible_asset_ids is None:
            return {"collection": _summary(collection, state, signature), "assets": [], "has_more": False,
                    "next_cursor": None, "freshness": freshness,
                    "coverage": "INDEXING" if indexing else ("TRUNCATED" if state["truncated"] else "BOUNDED"),
                    "refresh_required": True}
        build_id = str(state["build_id"])
        eligible = None if eligible_asset_ids is None else sorted({int(value) for value in eligible_asset_ids})
        eligible_hash = None if eligible is None else hashlib.sha256(
            json.dumps(eligible, separators=(",", ":")).encode()
        ).hexdigest()
        binding = [int(smart_id), str(instance_token), int(collection["rule_revision"]),
                   int(collection["exclusions_revision"]), build_id, signature, order, eligible_hash]
        after = _cursor_decode(cursor, "psr1", binding)
        if after is not None and (not isinstance(after, list) or len(after) != (2 if order == "rank" else 4)):
            raise _fail("智能合集游标无效，请刷新", status=409, code=INVALID_CURSOR)
        photo_sql, photo_params = photo_asset_sql(asset_alias="a")
        where = ["e.smart_id=?", "e.build_id=?", "a.state='ACTIVE'", photo_sql,
                 "NOT EXISTS(SELECT 1 FROM photo_smart_exclusions x WHERE x.smart_id=e.smart_id AND x.asset_id=e.asset_id)"]
        params: list[Any] = [int(smart_id), build_id, *photo_params]
        if eligible is not None:
            if eligible:
                where.append("e.asset_id IN (" + ",".join("?" for _ in eligible) + ")")
                params.extend(eligible)
            else:
                where.append("0")
        if order == "rank":
            if after:
                where.append("(e.rank>? OR (e.rank=? AND e.asset_id>?))")
                params.extend([int(after[0]), int(after[0]), int(after[1])])
            sort = "e.rank,e.asset_id"
            selected = "e.rank AS smart_rank,NULL AS capture_sort"
        else:
            trusted_capture = (
                "(m.time_source IN ('EXIF_ORIGINAL','EXIF_DIGITIZED') "
                "AND m.taken_at_sort IS NOT NULL)"
            )
            if after:
                known, sort_key, asset_id, _rank = after
                if int(known):
                    where.append(
                        f"(({trusted_capture} AND (m.taken_at_sort<? OR "
                        "(m.taken_at_sort=? AND a.asset_id<?))) OR NOT "
                        f"{trusted_capture})"
                    )
                    params.extend([int(sort_key), int(sort_key), int(asset_id)])
                else:
                    where.append(f"NOT {trusted_capture} AND a.asset_id<?")
                    params.append(int(asset_id))
            sort = (f"({trusted_capture}) DESC, "
                    f"CASE WHEN {trusted_capture} THEN m.taken_at_sort END DESC, a.asset_id DESC")
            selected = "e.rank AS smart_rank,m.taken_at_sort AS capture_sort"
        rows = catalog.read_all(
            "SELECT a.asset_id,a.sha256,a.byte_size,a.created_at,e.rank,m.taken_at_sort,m.time_source,"
            + selected + " FROM photo_smart_cache_entries e JOIN assets a ON a.asset_id=e.asset_id "
            "LEFT JOIN asset_metadata m ON m.asset_id=a.asset_id WHERE " + " AND ".join(where) +
            " ORDER BY " + sort + " LIMIT ?", (*params, limit + 1))
        more = len(rows) > limit
        rows = rows[:limit]
        ids = [int(row["asset_id"]) for row in rows]
        locations = locations_by_asset(catalog, ids)
        assets = [summarize_asset(cache, asset_id=int(row["asset_id"]), sha256=str(row["sha256"]),
            byte_size=int(row["byte_size"]), created_at=int(row["created_at"]),
            locations=locations.get(int(row["asset_id"]), [])) for row in rows]
        for asset, row in zip(assets, rows, strict=True):
            asset["smart_rank"] = int(row["rank"])
        last = rows[-1] if rows else None
        if more and last:
            position = ([int(last["rank"]), int(last["asset_id"])] if order == "rank" else
                        [int(last["time_source"] in {"EXIF_ORIGINAL", "EXIF_DIGITIZED"}
                             and last["taken_at_sort"] is not None), int(last["taken_at_sort"] or 0),
                         int(last["asset_id"]), int(last["rank"])])
            next_cursor = _cursor_encode("psr1", binding, position)
        else:
            next_cursor = None
        return {"collection": _summary(collection, state, signature), "assets": assets,
                "has_more": more, "next_cursor": next_cursor,
                "freshness": freshness,
                "coverage": "INDEXING" if indexing else ("TRUNCATED" if state["truncated"] else "BOUNDED")}


def request_refresh(catalog: Any, smart_id: int, *, instance_token: str,
                    manual: bool = False) -> dict[str, Any]:
    """Claim one durable refresh lease in a short transaction."""
    owner = secrets.token_hex(16)
    refresh_id = secrets.token_urlsafe(18)
    with catalog.write() as connection:
        collection = _get(catalog, smart_id, instance_token)
        if str(collection["execution_kind"]) == "FILTER":
            return {"status": "ALREADY_CURRENT", "refresh_id": None, "execution_kind": "FILTER"}
        rule = json.loads(collection["rule_json"])
        signature, indexing, active = dependency_signature(connection, rule)
        if str(collection["execution_kind"]) == "COMBINED":
            pinned = rule.get("search_binding")
            if pinned and (active is None or any(
                    pinned.get(key) != active.get(key) for key in
                    ("profile_id", "space_id", "image_generation_id", "dimension"))):
                cache = connection.execute("SELECT * FROM photo_smart_cache_state WHERE smart_id=?", (int(smart_id),)).fetchone()
                connection.execute("UPDATE photo_smart_cache_state SET refresh_status='FAILED',last_error_code='MODEL_REBIND_REQUIRED' WHERE smart_id=?",
                                   (int(smart_id),))
                return {"status": "DEFERRED", "refresh_id": None, "reason": "MODEL_REBIND_REQUIRED",
                        "cached": bool(cache and cache["build_id"])}
        state = connection.execute("SELECT * FROM photo_smart_cache_state WHERE smart_id=?", (int(smart_id),)).fetchone()
        now = catalog.now_ms()
        if (state is not None and state["build_id"] and state["dependency_signature"] == signature
                and not indexing and int(state["cache_rule_revision"] or 0) == int(collection["rule_revision"])
                and not manual):
            return {"status": "ALREADY_CURRENT", "refresh_id": None,
                    "candidate_count": int(state["candidate_count"])}
        if state is not None and state["lease_owner"] and int(state["lease_until_ms"] or 0) > now:
            return {"status": "RUNNING", "refresh_id": state["refresh_request_id"],
                    "lease_until_ms": int(state["lease_until_ms"])}
        lease_until = now + 600_000
        connection.execute(
            "UPDATE photo_smart_cache_state SET lease_owner=?,lease_until_ms=?,refresh_request_id=?,refresh_status='QUEUED',last_error_code=NULL WHERE smart_id=?",
            (owner, lease_until, refresh_id, int(smart_id)),
        )
        return {"status": "QUEUED", "refresh_id": refresh_id, "lease_owner": owner,
                "lease_until_ms": lease_until, "rule_revision": int(collection["rule_revision"]),
                "signature": signature}


class SmartRefreshRunner:
    """One process-local, bounded worker; the catalog lease coordinates other processes."""

    def __init__(self, context: Any) -> None:
        self.context = context
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="photo-smart-refresh")
        self._lock = threading.Lock()
        self._closed = False
        self._futures: set[Future[Any]] = set()

    def submit(self, smart_id: int, *, owner: str, rule_revision: int) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("smart refresh runner is closed")
            future = self._executor.submit(self._run, int(smart_id), str(owner), int(rule_revision))
            self._futures.add(future)
            future.add_done_callback(self._finished)

    def _finished(self, future: Future[Any]) -> None:
        with self._lock:
            self._futures.discard(future)

    def _run(self, smart_id: int, owner: str, rule_revision: int) -> None:
        service = None
        catalog = None
        owns_service = False
        try:
            service = self.context._service_factory()
            owns_service = True
            catalog = service.catalog
            with catalog.write() as connection:
                state = connection.execute("SELECT lease_owner,lease_until_ms FROM photo_smart_cache_state WHERE smart_id=?", (smart_id,)).fetchone()
                if (state is None or str(state["lease_owner"] or "") != owner
                        or int(state["lease_until_ms"] or 0) <= catalog.now_ms()):
                    return
                connection.execute("UPDATE photo_smart_cache_state SET refresh_status='RUNNING' WHERE smart_id=? AND lease_owner=?",
                                   (smart_id, owner))
            collection = _get(catalog, smart_id)
            if int(collection["rule_revision"]) != rule_revision:
                raise _fail("智能合集规则已变化", status=409, code="PHOTO_SMART_RULE_CHANGED")
            rule = json.loads(collection["rule_json"])
            from photo_search.api.models import PlaceAwareSearchRequestModel
            from photo_search.api.routes import _place_aware_search_worker

            seed_values = dict(rule["request"])
            seed_values.update({"request_id": "smart-refresh-" + secrets.token_hex(12),
                                "cursor": None, "search_session_id": None})
            seed = PlaceAwareSearchRequestModel.model_validate(seed_values)
            if str(collection["execution_kind"]) == "COMBINED":
                pinned = rule.get("search_binding") or {}
                current = catalog.read_one(
                    "SELECT p.profile_id,p.space_id,p.image_generation_id,p.fts_generation_id,g.dimension "
                    "FROM active_search_profile a JOIN search_profiles p USING(profile_id) "
                    "JOIN index_generations g ON g.generation_id=p.image_generation_id WHERE a.state_id=1")
                if current is None or any(pinned.get(key) != current[key] for key in
                                          ("profile_id", "space_id", "image_generation_id")):
                    raise _fail("当前搜索模型已变化，请重新搜索后替换规则", status=409,
                                code="MODEL_REBIND_REQUIRED")
                seed = seed.model_copy(update={
                    "profile_id": pinned.get("profile_id"), "space_id": pinned.get("space_id"),
                    "image_generation_id": pinned.get("image_generation_id"),
                    "dimension": pinned.get("dimension"),
                })
            with catalog.read_snapshot() as connection:
                signature_before, _, _ = dependency_signature(connection, rule)
            result = _place_aware_search_worker(catalog, seed, context=self.context,
                                                data_root=service.data_root)
            session = self.context.search_sessions.get(str(result.get("search_session_id") or ""))
            ids = [] if session is None else [int(entry.asset_id) for entry in session.entries]
            if session is None or len(ids) > MAX_CANDIDATES:
                raise _fail("检索没有返回可发布的候选快照", status=409, code="PHOTO_SMART_REFRESH_EMPTY")
            with catalog.read_snapshot() as connection:
                signature_after, indexing, _ = dependency_signature(connection, rule)
            if signature_after != signature_before:
                raise _fail("搜索依赖在刷新期间已变化，旧结果未发布", status=409,
                            code="PHOTO_SMART_DEPENDENCY_CHANGED")
            session_indexing, session_truncated = session_coverage(session)
            publish_candidates(catalog, smart_id, ids, signature=signature_before,
                                truncated=session_truncated, indexing=bool(indexing or session_indexing),
                                expected_rule_revision=rule_revision, lease_owner=owner)
        except Exception as error:  # keep the prior published build intact on every failure
            code = str(getattr(error, "code", "PHOTO_SMART_REFRESH_FAILED"))
            try:
                if catalog is None:
                    # The request path has already opened the shared service before it
                    # submits this worker. Use it only to release this task's own lease
                    # when worker initialization itself fails.
                    service = self.context.service()
                    catalog = service.catalog
                with catalog.write() as connection:
                    connection.execute(
                        "UPDATE photo_smart_cache_state SET refresh_status='FAILED',last_error_code=?,lease_owner=NULL,lease_until_ms=NULL "
                        "WHERE smart_id=? AND lease_owner=? AND lease_until_ms>?",
                        (code[:80], smart_id, owner, catalog.now_ms()))
            except Exception:
                pass
        finally:
            if service is not None and owns_service:
                service.close()

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)
