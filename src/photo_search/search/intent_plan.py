"""Bounded client and server-side shapes for personal natural-language search plans."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

PLAN_VERSION = 1
MAX_QUERY_LENGTH = 256
MAX_CANDIDATES = 24


@dataclass(frozen=True)
class ValidatedSearchPlan:
    """A plan after catalog-backed object identities have been revalidated."""

    original_query: str
    content_query: str | None
    capture_from: str | None
    capture_to_exclusive: str | None
    person_ids: tuple[int, ...]
    person_match: str
    place_kind: str | None
    place_key: str | None
    source_id: int | None
    collection_scope: dict[str, Any] | None
    state: str = "ready"

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_version": PLAN_VERSION,
            "original_query": self.original_query,
            "content_query": self.content_query,
            "capture_from": self.capture_from,
            "capture_to_exclusive": self.capture_to_exclusive,
            "person_ids": list(self.person_ids),
            "person_match": self.person_match,
            "place_kind": self.place_kind,
            "place_key": self.place_key,
            "source_id": self.source_id,
            "collection_scope": self.collection_scope,
            "state": self.state,
        }


def validate_plan_shape(value: Any, *, original_query: str) -> dict[str, Any]:
    """Reject malformed/unbounded client plans before the caller checks object identity."""
    if not isinstance(value, dict) or type(value.get("plan_version")) is not int or value.get("plan_version") != PLAN_VERSION:
        raise ValueError("搜索计划版本无效，请重新搜索")
    try:
        encoded_size = len(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError("搜索计划结构无效，请重新搜索") from error
    if len(original_query) > MAX_QUERY_LENGTH or len(value) > 24 or encoded_size > 8192:
        raise ValueError("搜索计划过大，请缩小查询范围")
    if str(value.get("original_query", "")) != original_query:
        raise ValueError("搜索计划与原查询不一致，请重新搜索")
    content = value.get("content_query")
    if content is not None and (not isinstance(content, str) or len(content) > MAX_QUERY_LENGTH):
        raise ValueError("搜索计划中的内容查询无效")
    if not isinstance(value.get("collection_query", ""), str) or len(value.get("collection_query", "")) > MAX_QUERY_LENGTH:
        raise ValueError("搜索计划中的查询文本无效")
    ids = value.get("person_ids", [])
    if not isinstance(ids, list) or len(ids) > 100 or any(type(item) is not int or item < 1 for item in ids):
        raise ValueError("搜索计划中的人物条件无效")
    if value.get("person_match", "any") not in {"any", "all"}:
        raise ValueError("搜索计划中的人物匹配方式无效")
    source_id = value.get("source_id")
    if source_id is not None and (type(source_id) is not int or source_id < 1):
        raise ValueError("搜索计划中的来源条件无效")
    for field in ("capture_from", "capture_to_exclusive"):
        bound = value.get(field)
        if bound is not None and (not isinstance(bound, str) or len(bound) > 32):
            raise ValueError("搜索计划中的拍摄时间条件无效")
    if value.get("time_basis", "capture") not in {"capture", "library"}:
        raise ValueError("搜索计划中的时间依据无效")
    if value.get("capture_status") not in {None, "unknown", "pending"}:
        raise ValueError("搜索计划中的拍摄日期状态无效")
    for field in ("library_from", "library_to_exclusive"):
        bound = value.get(field)
        if bound is not None and (not isinstance(bound, str) or len(bound) > 32):
            raise ValueError("搜索计划中的入库时间条件无效")
    if value.get("time_basis", "capture") == "library":
        if value.get("capture_status") is not None or value.get("capture_from") is not None or value.get("capture_to_exclusive") is not None:
            raise ValueError("入库时间与拍摄时间状态不能混用")
    elif value.get("library_from") is not None or value.get("library_to_exclusive") is not None:
        raise ValueError("拍摄时间条件不能带入库时间范围")
    place_kind, place_key = value.get("place_kind"), value.get("place_key")
    if (place_kind is None) != (place_key is None) or (place_kind is not None and (
        place_kind not in {"city", "province", "country", "district"}
        or not isinstance(place_key, (str, int)) or len(str(place_key)) > 128
    )):
        raise ValueError("搜索计划中的地点条件无效")
    if place_kind is not None and not isinstance(value.get("place_selection"), dict):
        raise ValueError("搜索计划缺少地点身份绑定，请重新搜索")
    candidates = value.get("candidates", [])
    if not isinstance(candidates, list) or len(candidates) > MAX_CANDIDATES:
        raise ValueError("候选条件过多，请缩小查询范围")
    return value
