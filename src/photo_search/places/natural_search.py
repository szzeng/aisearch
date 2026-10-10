"""Bounded, local parsing for explicit natural-language place searches.

This module does not look up names or infer geography. Callers supply only the currently
active place choices; returned keys therefore always refer to existing authoritative data.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

_LEADING_REQUESTS = (
    "请帮我找一下",
    "请帮我找",
    "帮我找一下",
    "帮我找",
    "帮我查找一下",
    "帮我查找",
    "找一下",
    "查找一下",
    "搜索一下",
    "请找",
    "找",
    "搜索",
)
_PLACE_MARKERS = ("拍摄的", "拍的", "拍摄")
_PHOTO_TAILS = ("所有照片", "所有图片", "全部照片", "全部图片", "照片", "图片")
_CONTENT_PROTECTED = ("老照片", "照片里的", "照片中的", "照片中")
_NEGATIVE = ("不是在", "不在", "没有在", "并非在")
_JOINERS = ("或者", "以及", "或", "和")
_PUNCTUATION = "，,。；;、:：.!?！？"


@dataclass(frozen=True)
class _Hit:
    start: int
    end: int
    choice: dict[str, Any]


def _normalize_with_map(value: str) -> tuple[str, list[int]]:
    """Case/compatibility fold text while mapping every output character to its source."""
    output: list[str] = []
    offsets: list[int] = []
    for source_offset, char in enumerate(value):
        if char.isspace():
            continue
        folded = unicodedata.normalize("NFKC", char).casefold()
        output.extend(folded)
        offsets.extend([source_offset] * len(folded))
    return "".join(output), offsets


def _norm(value: str) -> str:
    return _normalize_with_map(value)[0]


def _public_choice(choice: dict[str, Any]) -> dict[str, Any]:
    """Drop parser-only identity metadata from returned choices."""
    return {key: value for key, value in choice.items() if not key.startswith("_")}


def _trim_content(value: str) -> str:
    result = value.strip(" \t\r\n，,。；;、:：")
    normalized = _norm(result)
    for request in _LEADING_REQUESTS:
        folded = _norm(request)
        if normalized.startswith(folded):
            result = result[len(request) :].lstrip(" \t\r\n，,。；;、:：")
            normalized = _norm(result)
            break
    normalized = _norm(result)
    if normalized in {"所有", "全部", "所有照片", "所有图片", "全部照片", "全部图片"}:
        return ""
    if not any(normalized.startswith(_norm(prefix)) for prefix in _CONTENT_PROTECTED):
        for tail in _PHOTO_TAILS:
            folded = _norm(tail)
            if normalized.endswith(folded):
                result = result[: -len(tail)].rstrip(" \t\r\n，,。；;、:：")
                normalized = _norm(result)
                break
    if normalized in {"所有", "全部"}:
        return ""
    return result


def _strip_request_prefix(value: str) -> str | None:
    remaining = value
    for request in _LEADING_REQUESTS:
        folded = _norm(request)
        if remaining.startswith(folded):
            remaining = remaining[len(folded) :]
            break
    if remaining.startswith("在"):
        remaining = remaining[1:]
    return remaining or None


def _spelling_hits(text: str, choices: list[dict[str, Any]]) -> list[_Hit]:
    normalized, _ = _normalize_with_map(text)
    hits: list[_Hit] = []
    seen: set[tuple[int, int, str, str]] = set()
    for choice in choices:
        spellings = [str(choice.get("name", "")), *map(str, choice.get("aliases", []))]
        for spelling in spellings:
            needle = _norm(spelling)
            if not needle:
                continue
            start = 0
            while (found := normalized.find(needle, start)) >= 0:
                end = found + len(needle)
                identity = (found, end, str(choice.get("kind")), str(choice.get("key")))
                if identity not in seen:
                    seen.add(identity)
                    hits.append(_Hit(found, end, choice))
                start = found + 1
    return hits


def _parent_chain_prefix(prefix: str, hit: _Hit, hits: list[_Hit]) -> bool:
    """Allow preceding place names only when the selected choice declares that parent."""
    if not prefix:
        return True
    parent = _norm(str(hit.choice.get("parent", "")).replace("·", " "))
    preceding = [item for item in hits if item.end <= hit.start and item.end > 0]
    preceding.sort(key=lambda item: item.end, reverse=True)
    for item in preceding:
        name = _norm(str(item.choice.get("name", "")))
        gap = prefix[item.end : hit.start]
        # Chinese region qualifiers commonly connect the parent and child with 的.
        if name and name in parent and gap in {"", "的"}:
            before_parent = prefix[: item.start]
            return _strip_request_prefix(before_parent) is None
    return False


def _original_boundary(offsets: list[int], text: str, normalized_offset: int) -> int:
    if normalized_offset <= 0:
        return 0
    if normalized_offset >= len(offsets):
        return len(text)
    return offsets[normalized_offset]


def _is_explicit_multi_or_negative(text: str, normalized: str, hits: list[_Hit]) -> bool:
    for hit in hits:
        before = normalized[: hit.start]
        after = normalized[hit.end :]
        if any(before.endswith(_norm(word)) for word in _NEGATIVE) and any(
            after.startswith(_norm(marker)) for marker in _PLACE_MARKERS
        ):
            return True
        if any(after.startswith(_norm(word)) for word in _JOINERS):
            return True
    for left in hits:
        for right in hits:
            if left.end >= right.start or left.start == right.start:
                continue
            between = normalized[left.end : right.start]
            if any(_norm(word) in between for word in _JOINERS):
                tail = normalized[right.end :]
                if any(tail.startswith(_norm(marker)) for marker in _PLACE_MARKERS) or tail.startswith("的"):
                    return True
    return False


def parse_place_query(
    text: str,
    choices: list[dict[str, Any]],
    *,
    contextual_prefix_spans: list[tuple[int, int]] | None = None,
) -> dict[str, Any]:
    """Return a recognized place plan, or preserve the original query without hard filtering."""
    raw = str(text)
    normalized, offsets = _normalize_with_map(raw)
    hits = _spelling_hits(raw, choices)
    if _is_explicit_multi_or_negative(raw, normalized, hits):
        return {"state": "unresolved", "choices": [], "content_query": None,
                "notice": "这句话包含否定或多个地点，尚未形成地点筛选；请改写或使用显式地点条件。"}

    parsed: list[tuple[_Hit, str]] = []
    for hit in hits:
        prefix = normalized[: hit.start]
        suffix = normalized[hit.end :]
        clean_prefix = _strip_request_prefix(prefix)
        source_hit_start = _original_boundary(offsets, raw, hit.start)
        original_prefix = raw[:source_hit_start]
        masked_prefix = list(original_prefix)
        for start, end in contextual_prefix_spans or []:
            if 0 <= start < end <= source_hit_start:
                masked_prefix[start:end] = " " * (end - start)
        contextual_prefix = "".join(masked_prefix).rstrip()
        if contextual_prefix.endswith("在"):
            contextual_prefix = contextual_prefix[:-1].rstrip()
        contextual_leading = not contextual_prefix
        leading = clean_prefix is None or _parent_chain_prefix(prefix, hit, hits) or contextual_leading
        marker = next((item for item in _PLACE_MARKERS if suffix.startswith(_norm(item))), None)
        if leading and marker:
            remainder_start = hit.end + len(_norm(marker))
            content_start = _original_boundary(offsets, raw, remainder_start)
            parsed.append((hit, _trim_content(raw[content_start:])))
            continue
        if leading and suffix.startswith("的"):
            content_start = _original_boundary(offsets, raw, hit.end + 1)
            parsed.append((hit, _trim_content(raw[content_start:])))
            continue
        if suffix.startswith("的") and prefix.endswith("在"):
            before_place = raw[: _original_boundary(offsets, raw, hit.start)].rstrip(" \t\r\n")
            if before_place.endswith("在"):
                before_place = before_place[:-1].rstrip(" \t\r\n")
            after_place = raw[_original_boundary(offsets, raw, hit.end):]
            content = _trim_content(before_place + " " + after_place.lstrip("的"))
            parsed.append((hit, content))
            continue
        if leading and not suffix.strip(_PUNCTUATION):
            parsed.append((hit, ""))
            continue
        if leading and suffix.startswith(("，", ",")):
            content_start = _original_boundary(offsets, raw, hit.end + 1)
            parsed.append((hit, _trim_content(raw[content_start:])))
            continue
        if leading and suffix.startswith("照片"):
            content_start = _original_boundary(offsets, raw, hit.end)
            parsed.append((hit, _trim_content(raw[content_start:])))
            continue

        # A place after content is accepted only as an explicit final shooting clause.
        if marker and suffix.startswith(_norm(marker)):
            clause_start = _original_boundary(offsets, raw, hit.start)
            before_raw = raw[:clause_start]
            trimmed = before_raw.rstrip(" \t\r\n")
            if trimmed.endswith("在"):
                trimmed = trimmed[:-1].rstrip(" \t\r\n")
                content = _trim_content(trimmed)
                if content:
                    parsed.append((hit, content))
            if trimmed.endswith(("，", ",", "；", ";")):
                content = _trim_content(trimmed[:-1])
                if content:
                    parsed.append((hit, content))

        # A bare name followed by a whitespace boundary can introduce content, as in
        # "济南 夜景". Concatenated words such as "济南路上的夜景" never enter here.
        if leading and suffix and hit.end < len(offsets):
            end_original = offsets[hit.end - 1] + 1
            next_original = offsets[hit.end]
            if next_original > end_original and raw[end_original:next_original].isspace():
                content_start = offsets[hit.end]
                parsed.append((hit, _trim_content(raw[content_start:])))

    # A hierarchy written as adjacent known names is resolved only by the selected child's
    # declared parent. Overlapping same-name sources remain separate choices.
    if parsed:
        # A known region followed by 的 + another known place is a qualifier expression.
        # Never return the region itself as the search target when the child relationship
        # cannot be verified by the directory.
        qualified_spans = [
            (parent_hit.start, parent_hit.end)
            for parent_hit in hits
            if any(
                parent_hit.end < child_hit.start
                and normalized[parent_hit.end : child_hit.start] == "的"
                for child_hit in hits
            )
        ]
        if qualified_spans:
            parsed = [
                item for item in parsed
                if (item[0].start, item[0].end) not in qualified_spans
            ]
        if not parsed and qualified_spans:
            return {"state": "unresolved", "choices": [], "content_query": None,
                    "notice": "当前地点目录无法确认这段地点层级；仍按原句检索，没有添加地点条件。"}
    if parsed:
        parsed.sort(key=lambda item: (item[0].end - item[0].start, item[0].start), reverse=True)
        best_span = parsed[0][0].end - parsed[0][0].start
        best = [item for item in parsed if item[0].end - item[0].start == best_span]
        # If a preceding directory candidate is a verified parent of the matched child,
        # treat it as a qualifier rather than as a competing search target (e.g. 台湾的深圳).
        qualified_children = [
            child for child in best
            if any(
                parent_hit.end <= child[0].start
                and _norm(str(parent_hit.choice.get("name", "")))
                in _norm(str(child[0].choice.get("parent", "")))
                and normalized[parent_hit.end : child[0].start] == "的"
                for parent_hit, _ in parsed
            )
        ]
        if qualified_children:
            best = qualified_children
        elif best:
            # An explicit regional qualifier excludes unrelated same-name candidates.
            # Only candidates whose declared parent contains that qualifier survive.
            qualifiers = [
                parent_hit for parent_hit, _ in parsed
                if any(
                    parent_hit.end <= child[0].start
                    and normalized[parent_hit.end : child[0].start] == "的"
                    for child in best
                )
            ]
            if qualifiers:
                qualified = [
                    item for item in best
                    if any(
                        _norm(str(q.choice.get("name", "")))
                        in _norm(str(item[0].choice.get("parent", "")))
                        for q in qualifiers
                    )
                ]
                if qualified:
                    best = qualified
        # For one identical matched spelling with no explicit region qualifier, prefer
        # OSM when present. This affects only candidates at the selected span.
        if best and not any(
            parent_hit.end <= child[0].start
            and normalized[parent_hit.end : child[0].start] == "的"
            for parent_hit, _ in parsed for child in best
        ):
            # When identical canonical names are connected by an explicit directory
            # parent identity (for example a GeoNames country and its same-named city),
            # the broader parent is the safe default for an unqualified query. Do not
            # apply this to unrelated same-name places or name-only parent matches.
            parented_child_ids = {
                (str(child[0].choice.get("kind")), str(child[0].choice.get("key")))
                for child in best
                for parent in best
                if str(child[0].choice.get("_parent_key", "")) == str(parent[0].choice.get("key", ""))
                and bool(child[0].choice.get("source"))
                and str(child[0].choice.get("source", "")).upper()
                    == str(parent[0].choice.get("source", "")).upper()
                and bool(child[0].choice.get("dataset_id"))
                and str(child[0].choice.get("dataset_id", ""))
                    == str(parent[0].choice.get("dataset_id", ""))
                and str(child[0].choice.get("kind", "")) == "city"
                and str(parent[0].choice.get("kind", "")) == "country"
                and _norm(str(child[0].choice.get("name", "")))
                    == _norm(str(parent[0].choice.get("name", "")))
            }
            if parented_child_ids:
                best = [
                    item for item in best
                    if (str(item[0].choice.get("kind")), str(item[0].choice.get("key")))
                    not in parented_child_ids
                ]
            osm = [item for item in best if str(item[0].choice.get("source", "")).upper() == "OSM"]
            geonames = [item for item in best if str(item[0].choice.get("source", "")).upper() == "GEONAMES"]
            if osm:
                best = osm
            elif geonames:
                best = geonames
        identities = {(str(hit.choice.get("kind")), str(hit.choice.get("key"))) for hit, _ in best}
        if len(identities) == 1:
            hit, content = best[0]
            return {"state": "recognized", "choices": [dict(_public_choice(hit.choice), content_query=content)],
                    "content_query": content, "notice": None}
        contents = {content for _, content in best}
        common = next(iter(contents)) if len(contents) == 1 else None
        options = [dict(_public_choice(hit.choice), content_query=content) for hit, content in best]
        return {"state": "ambiguous", "choices": options,
                "content_query": common, "notice": None}

    # A known child name surrounded by a place phrase is not enough to prove a longer
    # hierarchy. Keep the text search intact and tell the user why no hard condition formed.
    if any(
        any(normalized[hit.end :].startswith(_norm(marker)) for marker in _PLACE_MARKERS)
        and any(token in normalized[: hit.start] for token in ("省", "市", "区", "县"))
        for hit in hits
    ):
        return {"state": "unresolved", "choices": [], "content_query": None,
                "notice": "当前地点目录无法确认这段地点层级；仍按原句检索，没有添加地点条件。"}

    if not hits:
        probe_chars = list(raw)
        for start, end in contextual_prefix_spans or []:
            if 0 <= start < end <= len(probe_chars):
                probe_chars[start:end] = " " * (end - start)
        probe = _norm("".join(probe_chars))
        for marker in _PLACE_MARKERS:
            marker_at = probe.find(_norm(marker))
            if marker_at < 0:
                continue
            before_marker = probe[:marker_at]
            location_at = before_marker.rfind("在")
            if location_at >= 0 and marker_at - location_at <= 28:
                return {"state": "unresolved", "choices": [], "content_query": None,
                        "notice": "当前地点目录没有可确认的地点；仍按原句检索，没有添加地点条件。"}

        # A short leading ``X的照片/夜景/海边`` form is commonly an explicit place
        # request. With no local directory match, keep the text query intact and say why
        # no location filter was applied. This is informational only; it never invents a
        # place or changes the retrieval scope.
        of = probe.find("的")
        if 0 < of <= 12 and probe[of + 1 :].startswith(
            ("照片", "图片", "夜景", "海边", "风景", "旅游", "旅行")
        ):
            leading = probe[:of]
            if (leading and not any(leading.startswith(_norm(token)) for token in _CONTENT_PROTECTED)
                    and not re.search(r"(?:风格|技术|生成|拍)$", leading)):
                return {"state": "unresolved", "choices": [], "content_query": None,
                        "notice": "当前地点目录没有可确认的地点；仍按原句检索，没有添加地点条件。"}

    return {"state": None, "choices": [], "content_query": None, "notice": None}
