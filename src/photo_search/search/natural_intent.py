"""Small deterministic parser for personal search dates and named people.

Only catalog-backed canonical names/aliases become person filters. Unknown words stay in
the semantic query, and ambiguous spellings are returned for explicit user choice.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from typing import Any

_DATE_PATTERNS = (
    re.compile(r"(?P<year>(?:19|20)\d{2})年(?:(?P<month>1[0-2]|0?[1-9])月(?:(?P<day>3[01]|[12]\d|0?[1-9])日?)?)?"),
    re.compile(r"(?P<year>(?:19|20)\d{2})-(?P<month>1[0-2]|0?[1-9])(?:-(?P<day>3[01]|[12]\d|0?[1-9]))?"),
)
_SEASONS = {"春": (3, 6), "夏": (6, 9), "秋": (9, 12)}


def parse_collection_intent(text: str, *, connection: Any) -> dict[str, Any]:
    """Resolve an explicitly referenced collection by its saved name, never by fuzzy match."""
    raw = unicodedata.normalize("NFC", str(text or ""))
    rows = connection.execute(
        "SELECT collection_id, instance_token, name, revision FROM person_collections ORDER BY collection_id"
    ).fetchall()
    hits: list[tuple[int, int, Any]] = []
    for row in rows:
        name = unicodedata.normalize("NFC", str(row["name"])).strip()
        if not name:
            continue
        pattern = r"\s+".join(re.escape(part) for part in name.split())
        for match in re.finditer(pattern, raw, re.IGNORECASE):
            # A collection becomes a hard filter only in an explicit collection/photo
            # construction. Arbitrary occurrences such as "同事聚会" remain ordinary text.
            tail = raw[match.end():].casefold()
            prefix = raw[:match.start()].casefold()
            if not (re.match(r"(?:合集|的照片|的图片|的合照|的合影|照片|图片|合照|合影)", tail)
                    or (tail.startswith("的") and re.search(r"(?:照片|图片|合照|合影)$", tail))
                    or (tail.startswith("的") and bool(tail[1:].strip()))
                    or (tail.startswith("在") and re.search(r"(?:照片|图片|合照|合影)$", tail))
                    or re.match(r"(?:和|与|、|及).{1,24}(?:的)?(?:照片|图片|合照|合影|合集)$", tail)
                    or re.search(r"(?:人物)?合集(?:叫|是)?$", prefix)):
                continue
            hits.append((match.start(), match.end(), row))
    # For overlapping names, keep the longest whole-name hit. Multiple distinct explicit
    # collections are surfaced for a user choice rather than silently taking the first.
    hits = [hit for hit in hits if not any(
        other[0] <= hit[0] and other[1] >= hit[1]
        and (other[1] - other[0] > hit[1] - hit[0]) for other in hits
    )]
    by_id = {int(hit[2]["collection_id"]): hit[2] for hit in hits}
    if not by_id:
        return {"state": "none", "choices": [], "content_query": raw}
    if len(by_id) > 1:
        residual = raw
        for start, end in sorted({(hit[0], hit[1]) for hit in hits}, reverse=True):
            residual = residual[:start] + " " + residual[end:]
        residual = re.sub(r"(?:人物)?合集|(?:的)?(?:照片|图片|合照|合影)", " ", residual)
        residual = re.sub(r"(?:和|与|或|或者|、|及)", " ", residual)
        residual = re.sub(r"[，,。；;\s]+", " ", residual).strip()
        return {"state": "ambiguous", "choices": [dict(row) for row in by_id.values()],
                "content_query": residual or None}
    selected_id, row = next(iter(by_id.items()))
    # Remove the matched name and its collection marker; keep dates, place words and content.
    start, end, _ = next(hit for hit in hits if int(hit[2]["collection_id"]) == selected_id)
    remaining = raw[:start] + " " + raw[end:]
    # A trailing "的照片/合集" belongs to the collection reference, while "在 ..." starts
    # a place/content condition and is retained for the place parser.
    remaining = re.sub(r"^\s*(?:合集|的照片|的图片|的合照|的合影|照片|图片|合照|合影)", " ", remaining)
    remaining = re.sub(r"(?:的)?(?:照片|图片|合照|合影|合集)(?=\s*(?:$|[，,。；;]))", " ", remaining)
    return {"state": "recognized", "choices": [dict(row)], "content_query": remaining.strip(),
            "collection": {"collection_id": int(row["collection_id"]),
                           "instance_token": str(row["instance_token"]),
                           "revision": int(row["revision"]), "name": str(row["name"])} }


def _bounds(year: int, month: int | None = None, day: int | None = None) -> tuple[str, str]:
    if month is None:
        start = date(year, 1, 1)
        end = date(year + 1, 1, 1)
    elif day is None:
        start = date(year, month, 1)
        end = date(year + (month == 12), month % 12 + 1, 1)
    else:
        start = date(year, month, day)
        end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _take_date(text: str, today: date) -> tuple[str, str, str] | None:
    match = re.search(r"(?P<year>(?:19|20)\d{2})年?(?P<season>春|夏|秋|冬)(?:天|季)?", text)
    if match:
        year = int(match.group("year"))
        season = match.group("season")
        if season == "冬":
            start, end = date(year, 12, 1), date(year + 1, 3, 1)
        else:
            first, after = _SEASONS[season]
            start, end = date(year, first, 1), date(year, after, 1)
        return match.group(0), start.isoformat(), end.isoformat()
    for word, delta in (("去年", -1), ("今年", 0), ("前年", -2)):
        match = re.search(word + r"(?:(春|夏|秋|冬)(?:天|季)?)?", text)
        if match:
            year = today.year + delta
            season = match.group(1)
            if season == "冬":
                start = date(year - 1, 12, 1) if delta == 0 and today.month < 3 else date(year, 12, 1)
                end = date(start.year + 1, 3, 1)
            elif season:
                first, after = _SEASONS[season]
                start, end = date(year, first, 1), date(year, after, 1)
            else:
                start, end = date(year, 1, 1), date(year + 1, 1, 1)
            return match.group(0), start.isoformat(), end.isoformat()
    match = re.search(r"上个月", text)
    if match:
        year, month = today.year, today.month - 1
        if month == 0:
            year, month = year - 1, 12
        first, after = _bounds(year, month)
        return match.group(0), first, after
    matches = [match for pattern in _DATE_PATTERNS if (match := pattern.search(text))]
    matches.sort(key=lambda item: (len(item.group(0)), item.start()), reverse=True)
    for match in matches:
        raw_year = int(match.group("year"))
        month = int(match.group("month")) if match.group("month") else None
        day = int(match.group("day")) if match.group("day") else None
        try:
            first, after = _bounds(raw_year, month, day)
        except ValueError:
            continue
        return match.group(0), first, after
    return None


def _person_choices(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT p.person_id, p.name, a.alias
             FROM persons AS p LEFT JOIN person_aliases AS a ON a.person_id=p.person_id
            WHERE p.merged_into IS NULL AND p.name IS NOT NULL
            ORDER BY p.person_id, a.alias"""
    ).fetchall()
    choices: dict[int, dict[str, Any]] = {}
    for row in rows:
        person_id = int(row["person_id"])
        item = choices.setdefault(person_id, {"person_id": person_id, "name": str(row["name"]), "aliases": []})
        if row["alias"]:
            item["aliases"].append(str(row["alias"]))
    return list(choices.values())


def _clean_content(value: str) -> str | None:
    remaining = re.sub(r"(?:请帮我找一下|请帮我找|帮我找一下|帮我找|找一下|查找一下|搜索一下|找|搜索)", " ", value)
    remaining = re.sub(r"(?:拍摄的|拍的|拍摄|的)?(?:合照|合影|照片|图片)$", " ", remaining)
    remaining = re.sub(r"^[\s，,。；;、]*(?:或者|和|与|或|的)+", " ", remaining)
    remaining = re.sub(r"[，,。；;、\s]+", " ", remaining).strip()
    if re.fullmatch(r"(?:(?:的|拍摄的|拍的)\s*)?(?:所有|全部)?(?:合照|合影|照片|图片)?", remaining):
        return None
    return remaining or None


def parse_natural_intent(text: str, *, connection: Any, today: date | None = None) -> dict[str, Any]:
    """Extract a capture interval and catalog-backed named people from one query."""
    raw = str(text or "")
    remaining = raw
    current = today or date.today()
    capture_from = capture_to = None
    date_hit = _take_date(remaining, current)
    if date_hit:
        spelling, capture_from, capture_to = date_hit
        remaining = remaining.replace(spelling, " ", 1)
        remaining = re.sub(r"(?:的)?(?:照片|图片|拍摄|拍的)", " ", remaining, count=1)

    found: list[tuple[int, int, dict[str, Any], str]] = []
    folded = remaining.casefold()
    for person in _person_choices(connection):
        for spelling in [person["name"], *person["aliases"]]:
            if not spelling:
                continue
            for match in re.finditer(re.escape(spelling), folded, re.IGNORECASE):
                found.append((match.start(), match.end(), person, spelling))
    # Prefer a longer spelling only when it overlaps a shorter one. Unrelated names in
    # the same query may have different lengths and must both remain person filters.
    if found:
        found = [
            hit
            for hit in found
            if not any(
                other[0] < hit[1]
                and hit[0] < other[1]
                and other[1] - other[0] > hit[1] - hit[0]
                for other in found
            )
        ]
    by_id = {hit[2]["person_id"]: hit[2] for hit in found}
    same_span: dict[tuple[int, int], set[int]] = {}
    for start, end, person, _ in found:
        same_span.setdefault((start, end), set()).add(int(person["person_id"]))
    ambiguous_ids = set().union(*(ids for ids in same_span.values() if len(ids) > 1)) if same_span else set()
    if ambiguous_ids:
        chosen_options = [person for person in by_id.values() if int(person["person_id"]) in ambiguous_ids]
        known = [person for person in by_id.values() if int(person["person_id"]) not in ambiguous_ids]
        unmatched = remaining
        spans = sorted({(hit[0], hit[1]) for hit in found}, reverse=True)
        for start, end in spans:
            unmatched = unmatched[:start] + " " + unmatched[end:]
        return {
            "state": "ambiguous", "choices": chosen_options,
            "known_person_ids": [int(person["person_id"]) for person in known],
            "known_person_names": [str(person["name"]) for person in known],
            "capture_from": capture_from, "capture_to_exclusive": capture_to,
            "person_match": "all" if "和" in remaining or "与" in remaining else "any",
            "content_query": _clean_content(unmatched),
            "notice": "人物名称存在歧义，请选择人物。",
        }
    person_match = "any"
    if len(by_id) > 1:
        between = remaining[min(hit[0] for hit in found) : max(hit[1] for hit in found)]
        if "或者" in between or "或" in between:
            person_match = "any"
        elif "和" in between or "与" in between:
            person_match = "all"
        else:
                unmatched = remaining
                for start, end, _, _ in sorted(found, key=lambda hit: (hit[0], hit[1]), reverse=True):
                    unmatched = unmatched[:start] + " " + unmatched[end:]
                unmatched = re.sub(r"(?:和|与|或者|或|的)?(?:合照|合影|照片|图片)", " ", unmatched)
                return {
                "state": "ambiguous", "choices": list(by_id.values()),
                "capture_from": capture_from, "capture_to_exclusive": capture_to,
                    "person_match": person_match, "content_query": _clean_content(unmatched),
                    "notice": "人物名称存在歧义，请选择人物。",
            }
    if by_id:
        spans = sorted({(hit[0], hit[1]) for hit in found}, reverse=True)
        for start, end in spans:
            remaining = remaining[:start] + " " + remaining[end:]
        remaining = re.sub(r"(?:和|与|或者|或|的)?(?:合照|合影|照片|图片)", " ", remaining)
    remaining = _clean_content(remaining)
    return {
        "state": "ready", "choices": list(by_id.values()),
        "person_match": person_match,
        "capture_from": capture_from, "capture_to_exclusive": capture_to,
        "content_query": remaining, "notice": None,
    }
