"""Collect grounded personal-search candidates from one immutable source sentence.

The existing domain recognizers remain the authorities for their local catalogs. This
module runs them against the same original string, resolves overlapping spans, then
produces a single execution plan. It deliberately does not invent identities or SQL.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

from photo_search.places.natural_search import parse_place_query
from photo_search.search.intent_plan import MAX_CANDIDATES, PLAN_VERSION
from photo_search.search.natural_intent import (
    _person_choices,
    _take_date,
    parse_collection_intent,
    parse_natural_intent,
)


def _span(raw: str, value: str) -> tuple[int, int] | None:
    if not value:
        return None
    match = re.search(re.escape(value), raw, re.IGNORECASE)
    return None if match is None else (match.start(), match.end())


def _name_spans(raw: str, spelling: str, all_spellings: list[str]) -> list[tuple[int, int]]:
    if not spelling:
        return []
    spans = [(match.start(), match.end()) for match in re.finditer(re.escape(spelling), raw, re.IGNORECASE)]
    if spelling.isascii() and spelling[0].isalnum():
        spans = [(start, end) for start, end in spans
                 if (start == 0 or not (raw[start - 1].isascii() and raw[start - 1].isalnum()))
                 and (end == len(raw) or not (raw[end].isascii() and raw[end].isalnum()))]
    return [(start, end) for start, end in spans if not any(
        len(other) > len(spelling) and other.casefold().find(spelling.casefold()) >= 0
        and any(start >= match.start() and end <= match.end()
                for match in re.finditer(re.escape(other), raw, re.IGNORECASE))
        for other in all_spellings
    )]


def _erase(raw: str, items: list[dict[str, Any]]) -> str | None:
    """Remove recognized spans and syntax attached to those spans only."""
    ordered = sorted(items, key=lambda item: (int(item["start"]), int(item["end"])))
    ranges: list[tuple[int, int]] = []
    for item in ordered:
        start, end = int(item["start"]), int(item["end"])
        kind = str(item.get("type", ""))
        prefix, suffix = raw[:start], raw[end:]
        if kind == "place":
            prefix_end = len(prefix.rstrip())
            if raw[:prefix_end].endswith("在"):
                start = prefix_end - 1
            marker = re.match(r"\s*(拍摄的|拍的|拍摄)\s*", suffix)
            if marker:
                end += marker.end()
            elif re.match(r"\s*的", suffix):
                end += re.match(r"\s*的", suffix).end()
        elif kind == "collection":
            marker = next((value for value in ("合集里的", "合集中的", "合集", "的照片", "的图片", "的合照", "的合影", "的")
                           if suffix.startswith(value)), None)
            if marker:
                end += len(marker)
        elif kind in {"person", "time", "source"} and suffix.startswith("的"):
            end += 1
        ranges.append((start, end))
    people = [item for item in ordered if item.get("type") == "person"]
    for left, right in zip(people, people[1:]):
        start, end = int(left["end"]), int(right["start"])
        if start <= end and re.fullmatch(r"\s*(?:和|与|或|或者|、|及)\s*", raw[start:end]):
            ranges.append((start, end))
    result = raw
    for start, end in sorted(set(ranges), reverse=True):
        result = result[:start] + " " + result[end:]
    result = re.sub(r"(?:请帮我找一下|请帮我找|帮我找一下|帮我找|找一下|搜索一下|查找一下)", " ", result)
    result = result.strip(" \t\r\n，,。；;、")
    result = re.sub(r"(?:人物)?合集|(?:的)?(?:照片|图片|合照|合影)$", " ", result)
    return re.sub(r"[，,。；;、\s]+", " ", result).strip() or None


def _source_candidates(raw: str, connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute("SELECT source_id, display_name FROM source_roots ORDER BY source_id").fetchall()
    found = []
    folded = unicodedata.normalize("NFC", raw)
    original_boundaries: dict[int, int] = {}
    for index in range(len(raw) + 1):
        normalized_boundary = len(unicodedata.normalize("NFC", raw[:index]))
        original_boundaries[normalized_boundary] = index
    for row in rows:
        name = unicodedata.normalize("NFC", str(row["display_name"] or "")).strip()
        if not name:
            continue
        for match in re.finditer(re.escape(name), folded, re.IGNORECASE):
            around = folded[max(0, match.start() - 10):match.start()].casefold()
            tail = folded[match.end():].casefold()
            explicit_tail = re.match(r"(?:里|中的|中)", tail) and re.search(r"(?:图库|来源|备份)", name)
            if not (re.search(r"(?:图库|来源|备份)(?:里|中的|中)?$", around)
                    or re.match(r"(?:图库|来源|备份)(?:里|中的|中)?", tail)
                    or explicit_tail):
                continue
            normalized_end = match.end() + (len(explicit_tail.group(0)) if explicit_tail else 0)
            found.append({"type": "source", "source_id": int(row["source_id"]),
                          "name": name,
                          "start": original_boundaries.get(match.start(), match.start()),
                          "end": original_boundaries.get(normalized_end, normalized_end),
                          "status": "confirmed"})
    return found


def parse_unified_intent(
    text: str, *, connection: Any, place_choices: list[dict[str, Any]] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Resolve five catalog-backed intent types against ``text`` without staged rewrites."""
    raw = str(text or "")
    if len(raw) > 256:
        return {"plan_version": PLAN_VERSION, "state": "unsupported", "original_query": raw[:256],
                "content_query": None, "candidates": [], "notice": "查询过长，请缩短后重试。"}

    candidates: list[dict[str, Any]] = []
    collection = parse_collection_intent(raw, connection=connection)
    choice_required = collection["state"] == "ambiguous"
    if collection["state"] == "recognized":
        item = collection["collection"]
        span = _span(raw, item["name"])
        if span:
            candidates.append({"type": "collection", **item, "start": span[0], "end": span[1], "status": "confirmed"})
    elif collection["state"] == "ambiguous":
        for item in collection["choices"]:
            span = _span(raw, str(item["name"]))
            if span:
                candidates.append({"type": "collection", "collection_id": int(item["collection_id"]),
                                   "instance_token": str(item["instance_token"]), "revision": int(item["revision"]),
                                   "name": str(item["name"]), "start": span[0], "end": span[1], "status": "needs_choice"})

    people = parse_natural_intent(raw, connection=connection, today=today)
    choice_required = choice_required or people["state"] == "ambiguous"
    person_by_id = {int(item["person_id"]): item for item in _person_choices(connection)}
    person_rows = [*people.get("choices", []), *[
        person_by_id[person_id] for person_id in people.get("known_person_ids", []) if person_id in person_by_id
    ]]
    all_person_spellings = [spelling for item in person_by_id.values()
                            for spelling in [str(item.get("name", "")), *map(str, item.get("aliases", []))]
                            if spelling]
    for item in person_rows:
        spellings = [str(item.get("name", "")), *map(str, item.get("aliases", []))]
        for spelling in spellings:
            for span in _name_spans(raw, spelling, all_person_spellings):
                candidates.append({"type": "person", "person_id": int(item["person_id"]),
                                   "name": str(item["name"]), "start": span[0], "end": span[1],
                                   "status": "needs_choice" if people["state"] == "ambiguous" else "confirmed"})

    hit = _take_date(raw, today or date.today())
    # A bare year embedded in quoted OCR/visual content is not a time constraint.
    if hit and not re.search(r"(?:写着|写有|标着|编号|文字)[^，,。；;]*$", raw[:raw.find(hit[0])]):
        span = _span(raw, hit[0])
        if span and (not re.fullmatch(r"20\d{2}年?", hit[0]) or re.search(r"(?:照片|图片|拍|的|年)$", raw[span[1]:]) or span[0] == 0):
            candidates.append({"type": "time", "value": [hit[1], hit[2]], "start": span[0], "end": span[1], "status": "confirmed"})

    place = parse_place_query(raw, place_choices or [])
    choice_required = choice_required or place["state"] == "ambiguous"
    if place["state"] in {"recognized", "ambiguous"}:
        for item in place["choices"]:
            span = next((candidate_span for spelling in
                         [str(item.get("name", "")), *map(str, item.get("aliases", []))]
                         if (candidate_span := _span(raw, spelling)) is not None), None)
            if span:
                candidates.append({"type": "place", **item, "start": span[0], "end": span[1],
                                   "status": "needs_choice" if place["state"] == "ambiguous" else "confirmed"})
    candidates.extend(_source_candidates(raw, connection))
    # A person's canonical name can also be stored as one of their aliases. The
    # recognizer visits both spellings, but they represent one identity at one span.
    # Collapse those repeats before overlap/conflict detection; otherwise the same
    # person is incorrectly sent to the scope-choice UI twice.
    identity_fields = {
        "collection": ("collection_id", "instance_token", "revision"),
        "person": ("person_id",),
        "place": ("kind", "key", "source", "source_id", "selection_ref", "dataset_id", "dataset_version"),
        "source": ("source_id",),
        "time": ("value",),
    }
    unique_candidates: dict[tuple[Any, ...], dict[str, Any]] = {}
    for candidate in candidates:
        kind = str(candidate.get("type", ""))
        identity = tuple(
            tuple(value) if isinstance(value := candidate.get(field), list) else value
            for field in identity_fields.get(kind, ())
        )
        key = (kind, identity, int(candidate["start"]), int(candidate["end"]))
        existing = unique_candidates.get(key)
        if existing is None:
            unique_candidates[key] = candidate
        elif candidate.get("status") == "needs_choice":
            existing["status"] = "needs_choice"
    candidates = list(unique_candidates.values())
    if len(candidates) > MAX_CANDIDATES:
        return {"plan_version": PLAN_VERSION, "state": "unsupported", "original_query": raw,
                "content_query": raw, "candidates": [], "notice": "可选条件过多，请缩小查询范围。"}

    # Same-span cross-type identities are never selected by parser order.
    groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for item in candidates:
        groups.setdefault((int(item["start"]), int(item["end"])), []).append(item)
    conflicts = [items for items in groups.values() if len({item["type"] for item in items}) > 1]
    duplicate_identities = [items for items in groups.values() if len({
        (item["type"], item.get("person_id", item.get("collection_id", item.get("source_id", item.get("key")))))
        for item in items
    }) > 1]
    if duplicate_identities:
        choice_required = True
        for items in duplicate_identities:
            for item in items:
                item["status"] = "needs_choice"

    def pending_fields() -> dict[str, Any]:
        fixed = [item for item in candidates if item["status"] == "confirmed"]
        person_ids = list(dict.fromkeys(int(item["person_id"]) for item in fixed if item["type"] == "person"))
        chosen_collection = next((item for item in fixed if item["type"] == "collection"), None)
        chosen_place = next((item for item in fixed if item["type"] == "place"), None)
        chosen_source = next((item for item in fixed if item["type"] == "source"), None)
        chosen_time = next((item for item in fixed if item["type"] == "time"), None)
        return {
            "collection_scope": None if chosen_collection is None else {
                key: chosen_collection[key] for key in ("collection_id", "instance_token", "revision", "name")},
            "person_ids": person_ids,
            "person_names": list(dict.fromkeys(str(item["name"]) for item in fixed if item["type"] == "person")),
            "person_match": "all" if "合照" in raw or "合影" in raw else "any",
            "place_kind": None if chosen_place is None else chosen_place["kind"],
            "place_key": None if chosen_place is None else str(chosen_place["key"]),
            "place_name": None if chosen_place is None else str(chosen_place.get("name", "")),
            "place_selection": None if chosen_place is None else {
                key: chosen_place.get(key) for key in
                ("kind", "key", "source", "source_id", "selection_ref", "dataset_id", "dataset_version")},
            "source_id": None if chosen_source is None else int(chosen_source["source_id"]),
            "source_name": None if chosen_source is None else str(chosen_source.get("name", "")),
            "capture_from": None if chosen_time is None else chosen_time["value"][0],
            "capture_to_exclusive": None if chosen_time is None else chosen_time["value"][1],
            "content_query": _erase(raw, candidates),
        }
    if conflicts:
        for items in conflicts:
            for item in items:
                item["status"] = "needs_choice"
        return {"plan_version": PLAN_VERSION, "state": "needs_choice", "original_query": raw,
                **pending_fields(), "candidates": candidates, "notice": "同一名称对应多种筛选条件，请选择。"}

    if choice_required:
        return {"plan_version": PLAN_VERSION, "state": "needs_choice", "original_query": raw,
                **pending_fields(), "candidates": candidates, "notice": "名称对应多个对象，请选择后再搜索。"}

    matched_place_ids = {
        (str(item.get("kind")), str(item.get("key")))
        for item in (place_choices or [])
        if any(_span(raw, str(spelling)) for spelling in [item.get("name", ""), *item.get("aliases", [])])
    }
    explicit_place_negative = bool(matched_place_ids) and bool(
        re.search(r"(?:不是在|不在|没有在|并非在|排除|不要).{0,12}(?:地点|城市|城市名)?", raw)
    )
    unsupported = bool(re.search(r"(?:排除|不要|不是|不在).{0,12}(?:人物|地点|照片)", raw))
    unsupported = unsupported or (place.get("state") == "unresolved" and (
        (len(matched_place_ids) > 1 and bool(re.search(r"(?:或者|或|以及|和)", raw)))
        or explicit_place_negative or not any(
            item["type"] in {"collection", "person", "source"} for item in candidates
        )
    ))
    time_candidates = [item for item in candidates if item["type"] == "time"]
    explicit_times = list(re.finditer(r"(?<!\d)(?:19|20)\d{2}(?:年|-(?:1[0-2]|0?[1-9]))|(?:去年|前年|今年)(?:(?:春|夏|秋|冬)(?:天|季)?)?", raw))
    if len(time_candidates) > 1 or (len(explicit_times) > 1 and not re.search(r"(?:写着|写有|标着|编号|文字)", raw)):
        unsupported = True
    confirmed = [item for item in candidates if item["status"] == "confirmed"]
    spans = [(int(item["start"]), int(item["end"])) for item in confirmed]
    overlaps = any(a < d and c < b for i, (a, b) in enumerate(spans) for c, d in spans[i + 1:])
    if overlaps:
        for item in confirmed:
            item["status"] = "needs_choice"
        return {"plan_version": PLAN_VERSION, "state": "needs_choice", "original_query": raw,
                "content_query": raw, "candidates": candidates, "notice": "识别条件重叠，请选择一种解释。"}

    residual = _erase(raw, confirmed) if spans else raw.strip() or None
    unique_people = []
    for item in confirmed:
        if item["type"] == "person" and item["person_id"] not in unique_people:
            unique_people.append(int(item["person_id"]))
    person_match = "any"
    if len(unique_people) > 1:
        if "或者" in raw or "或" in raw:
            person_match = "any"
        elif "合照" in raw or "合影" in raw:
            person_match = "all"
        else:
            for item in confirmed:
                if item["type"] == "person":
                    item["status"] = "needs_choice"
            return {"plan_version": PLAN_VERSION, "state": "needs_choice", "original_query": raw,
                    "choice_kind": "person_match", "person_ids": unique_people,
                    "person_names": [person_by_id[item]["name"] for item in unique_people if item in person_by_id],
                    "collection_scope": pending_fields()["collection_scope"],
                    "place_kind": pending_fields()["place_kind"], "place_key": pending_fields()["place_key"],
                    "place_selection": pending_fields()["place_selection"],
                    "source_id": pending_fields()["source_id"],
                    "capture_from": pending_fields()["capture_from"],
                    "capture_to_exclusive": pending_fields()["capture_to_exclusive"],
                    "content_query": pending_fields()["content_query"], "candidates": candidates,
                    "notice": "请确认按任一人物还是同框匹配。"}

    chosen_collection = next((item for item in confirmed if item["type"] == "collection"), None)
    chosen_place = next((item for item in confirmed if item["type"] == "place"), None)
    chosen_source = next((item for item in confirmed if item["type"] == "source"), None)
    chosen_time = next((item for item in confirmed if item["type"] == "time"), None)
    state = "unsupported" if unsupported else ("ready" if confirmed else "none")
    return {"plan_version": PLAN_VERSION, "state": state, "original_query": raw,
            "content_query": None if state == "unsupported" else (raw if state == "none" else residual),
            "collection_scope": None if chosen_collection is None else {
                key: chosen_collection[key] for key in ("collection_id", "instance_token", "revision", "name")},
            "person_ids": unique_people, "person_match": person_match,
            "place_kind": None if chosen_place is None else chosen_place["kind"],
            "place_key": None if chosen_place is None else str(chosen_place["key"]),
            "place_name": None if chosen_place is None else str(chosen_place.get("name", "")),
            "place_selection": None if chosen_place is None else {
                key: chosen_place.get(key) for key in
                ("kind", "key", "source", "source_id", "selection_ref", "dataset_id", "dataset_version")},
            "source_id": None if chosen_source is None else int(chosen_source["source_id"]),
            "source_name": None if chosen_source is None else str(chosen_source.get("name", "")),
            "capture_from": None if chosen_time is None else chosen_time["value"][0],
            "capture_to_exclusive": None if chosen_time is None else chosen_time["value"][1],
            "candidates": candidates, "notice": place.get("notice") if unsupported else None}
