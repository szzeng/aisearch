"""Small deterministic parser for personal search dates and named people.

Only catalog-backed canonical names/aliases become person filters. Unknown words stay in
the semantic query, and ambiguous spellings are returned for explicit user choice.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

_DATE_PATTERNS = (
    # Consume the whole numeric token first, then validate it. A regex restricted to
    # 1..12 can backtrack from "2024-13" to the valid but wrong prefix "2024-1".
    re.compile(r"(?<![\dA-Za-z])(?P<year>(?:19|20)\d{2})年(?:\s*(?P<month>\d+)月(?:\s*(?P<day>\d+)[日号]?)?)?"),
    re.compile(r"(?<![\dA-Za-z/-])(?P<year>(?:19|20)\d{2})-(?P<month>\d+)(?:-(?P<day>\d+))?(?![\dA-Za-z/-])"),
)
_SEASONS = {"春": (3, 6), "夏": (6, 9), "秋": (9, 12)}
_MONTH_NAMES = ("january", "february", "march", "april", "may", "june",
                "july", "august", "september", "october", "november", "december")
_MONTH_WORD = "(?:" + "|".join(_MONTH_NAMES) + ")"


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
            # Longest range connectors are recognized together with the name. Keeping
            # this grammar catalog-backed avoids treating ordinary "AI风格" content as
            # a collection merely because a collection named AI exists.
            tail = raw[match.end():].casefold()
            prefix = raw[:match.start()].casefold()
            connector = re.match(r"(?:合集(?:里面|里|中的|中)?(?:的)?|里面的|里面|里的|里|中的|中|的)", tail)
            explicit_tail = bool(
                re.match(r"(?:照片|图片|合照|合影|合集)", tail)
                or connector and (bool(tail[connector.end():].strip()) or connector.group(0) != "的")
                or tail.startswith("在") and re.search(r"(?:照片|图片|合照|合影)$", tail)
                or re.match(r"(?:和|与|或|或者|、|及).{1,24}(?:的)?(?:照片|图片|合照|合影|合集)$", tail)
                or re.search(r"(?:人物)?合集(?:叫|是)?$", prefix)
            )
            if not explicit_tail:
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
        spans = sorted({(hit[0], hit[1]) for hit in hits})
        between = [raw[left[1]:right[0]] for left, right in zip(spans, spans[1:])]
        if any(re.fullmatch(r"\s*(?:或|或者|和|与|及|、)\s*", part) for part in between):
            return {"state": "unsupported", "choices": [], "content_query": None,
                    "notice": "多个合集之间的联合条件暂不支持，请改用单个合集。"}
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


def _date_is_content(text: str, start: int, end: int) -> bool:
    """A date printed inside a photo is content, not its capture timestamp."""
    prefix = re.split(r"[，,。；;\n]", text[:start])[-1]
    suffix = text[end:]
    if re.search(r"写着|写有|标着|标有|标注|编号|文字|上写|印着|印有|"
                 r"\b(?:text|written|reads|reading|labelled|labeled)\b", prefix, re.I):
        return True
    if re.match(r"\s*(?:的)?(?:字样|文字|编号)", suffix):
        return True
    for opening, closing in (("\"", "\""), ("'", "'"), ("“", "”"), ("‘", "’"), ("「", "」")):
        left = text.rfind(opening, 0, start)
        if left >= 0 and (opening == closing and text[:start].count(opening) % 2
                          or opening != closing and text.rfind(closing, 0, start) < left):
            if text.find(closing, end) >= end:
                return True
    return False


@dataclass(frozen=True)
class DateIntent:
    hit: tuple[str, str, str] | None = None
    notice: str | None = None
    content_only: bool = False
    span: tuple[int, int] | None = None


def date_intent(text: str, today: date) -> DateIntent:
    """One complete supported interval, no date, or an explicit refusal reason.

    Both personal-search parsers use this bounded grammar. Longer tokens own their
    component spans, so a malformed date/range can never fall back to its year.
    """
    if len(text) > 256:
        return DateIntent(notice="查询过长，请缩短后重试。")
    tokens: list[tuple[int, int, tuple[str, str] | None]] = []

    def append(match: re.Match[str], bounds: tuple[str, str] | None) -> None:
        tokens.append((match.start(), match.end(), bounds))

    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            try:
                parts = [match.group(field) for field in ("month", "day")]
                if any(part and len(part) > 2 for part in parts):
                    raise ValueError("oversized date component")
                bounds = _bounds(int(match["year"]), *(int(part) if part else None for part in parts))
            except ValueError:
                bounds = None
            append(match, bounds)
    for match in re.finditer(
        r"(?<![\dA-Za-z])(?P<year>(?:19|20)\d{2})年\s*(?P<first>\d+)月\s*到\s*"
        r"(?:(?P<last_year>(?:19|20)\d{2})年\s*)?(?P<last>\d+)月", text,
    ):
        year, first, last = int(match["year"]), int(match["first"]), int(match["last"])
        valid = (int(match["last_year"] or year) == year and 1 <= first <= last <= 12
                 and len(match["first"]) <= 2 and len(match["last"]) <= 2)
        append(match, (_bounds(year, first)[0], _bounds(year, last)[1]) if valid else None)

    for match in re.finditer(
        r"(?<![\dA-Za-z])(?P<year>(?:19|20)\d{2})年?(?P<season>春|夏|秋|冬)(?:天|季)?|"
        r"(?P<relative>去年|今年|前年)(?:(?P<month>\d+)月(?:\s*(?P<day>\d+)[日号]?)?|"
        r"(?P<relative_season>春|夏|秋|冬)(?:天|季)?)?|(?P<last_month>上个月)", text,
    ):
        year = int(match["year"]) if match["year"] else today.year + {"去年": -1, "前年": -2}.get(match["relative"], 0)
        season = match["season"] or match["relative_season"]
        try:
            if match["last_month"]:
                bounds = _bounds(today.year - (today.month == 1), (today.month - 2) % 12 + 1)
            elif season:
                if season == "冬":
                    year -= int(match["relative"] == "今年" and today.month < 3)
                    bounds = (date(year, 12, 1).isoformat(), date(year + 1, 3, 1).isoformat())
                else:
                    first, after = _SEASONS[season]
                    bounds = (date(year, first, 1).isoformat(), date(year, after, 1).isoformat())
            else:
                parts = [match[field] for field in ("month", "day")]
                if any(part and len(part) > 2 for part in parts):
                    raise ValueError("oversized date component")
                bounds = _bounds(year, *(int(part) if part else None for part in parts))
        except ValueError:
            bounds = None
        append(match, bounds)

    for pattern in (rf"\b(?P<month>{_MONTH_WORD})\s+(?P<year>(?:19|20)\d{{2}})\b",
                    rf"\b(?P<year>(?:19|20)\d{{2}})\s+(?P<month>{_MONTH_WORD})\b"):
        for match in re.finditer(pattern, text, re.I):
            append(match, _bounds(int(match["year"]), _MONTH_NAMES.index(match["month"].lower()) + 1))
    # English day expressions are deliberately outside this small month grammar.
    for match in re.finditer(
        rf"\b(?:\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH_WORD}|{_MONTH_WORD}\s+\d{{1,2}}(?:st|nd|rd|th)?[,]?)\s+(?:19|20)\d{{2}}\b",
        text, re.I,
    ):
        append(match, None)

    tokens = [item for item in tokens if not any(
        other[0] <= item[0] and other[1] >= item[1] and other[1] - other[0] > item[1] - item[0]
        for other in tokens
    )]
    has_tokens = bool(tokens)
    tokens = [item for item in tokens if not _date_is_content(text, item[0], item[1])]
    if not tokens:
        return DateIntent(content_only=has_tokens)
    notice = "日期或时间关系暂不支持，请使用有效的单个日期、月份、年份或同一年内连续月份。"
    if len(tokens) != 1 or tokens[0][2] is None:
        return DateIntent(notice=notice)
    start, end, bounds = tokens[0]
    prefix, suffix = text[:start], text[end:]
    if (re.search(r"(?:不(?:是)?在|不是|并非|排除|不要|除了?|除去|早于|晚于|截至|截止(?:到)?|直到|"
                  r"\b(?:before|after|until|since|between|not|except)(?:\s+(?:in|during|from|for|the))?)\s*$", prefix, re.I)
            or re.search(r"(?:\d+\s*月|" + _MONTH_WORD + r")\s*(?:到|至|和|或|以及|、|[-~～]|\b(?:to|and|or)\b)\s*$", prefix, re.I)
            or re.match(r"\s*(?:之前|之后|以前|以后|以来|前|后|起|开始|为止|以外|到|至|"
                        r"\b(?:to|through|until|onwards?)\b)", suffix, re.I)
            or re.match(r"\s*(?:到|至|和|或|以及|、|[-~～]|\b(?:to|and|or)\b)\s*(?:\d|[一二三四五六七八九十]|" + _MONTH_WORD + ")", suffix, re.I)
            or re.match(r"\s*(?:\d|[零〇一二三四五六七八九十廿卅]+[月日号])", suffix)):
        return DateIntent(notice=notice)
    assert bounds is not None
    return DateIntent(hit=(text[start:end], *bounds), span=(start, end))


def _take_date(text: str, today: date) -> tuple[str, str, str] | None:
    """Compatibility helper; callers needing refusal details use ``date_intent``."""
    return date_intent(text, today).hit


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
    dates = date_intent(remaining, current)
    date_hit = dates.hit
    if dates.notice:
        return {"state": "unsupported", "choices": [], "person_match": "any",
                "capture_from": None, "capture_to_exclusive": None,
                "content_query": None, "notice": dates.notice}
    if date_hit:
        spelling, capture_from, capture_to = date_hit
        assert dates.span is not None
        start, end = dates.span
        remaining = remaining[:start] + " " + remaining[end:]
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
