"""Date expressions must never become a partially interpreted hard filter."""

import sqlite3
from contextlib import closing
from datetime import date

import pytest

from photo_search.search.natural_intent import parse_natural_intent
from photo_search.search.unified_intent import parse_unified_intent


def coverage_catalog():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        "CREATE TABLE persons(person_id INTEGER PRIMARY KEY,name TEXT,merged_into INTEGER);"
        "CREATE TABLE person_aliases(person_id INTEGER,alias TEXT);"
        "CREATE TABLE person_collections(collection_id INTEGER PRIMARY KEY,instance_token TEXT,name TEXT,revision INTEGER);"
        "CREATE TABLE source_roots(source_id INTEGER PRIMARY KEY,display_name TEXT);"
    )
    return closing(connection)


@pytest.mark.parametrize("query", [
    "2024-13的猫", "2024-123的猫", "2024-0712猫", "2024年13月的照片", "2024年0月的照片",
    "2024年2月30日的猫", "2024-02-30的猫", "2024年7月32日的猫",
    "去年13月的猫", "2024年7月之前的猫", "2024年7月之后的猫",
    "不在2024年7月拍的猫", "2024年7月和8月的猫", "2024年7月或8月的猫",
    "2024年7月到8月和10月的照片", "2024年7月到2025年8月的照片",
    "2024年8月到7月的照片", "去年和今年的猫", "2024-07 to 2024-08 cats",
    "before July 2024 cats", "July 2024 and August 2024 cats", "5 July 2024 cats",
    "7月到2024年8月的猫", "June to July 2024 cats", "not in July 2024 cats",
    "2024年7月到现在的猫", "2024年7月至今的猫", "截至2024年7月的照片",
    "除了2024年7月的照片", "not during July 2024 cats", "July 2024 to now",
    "2024年七月的猫", "今年八月的猫", "2024年7月三日的猫",
])
def test_invalid_or_unimplemented_dates_never_apply_partial_filter(query):
    with coverage_catalog() as db:
        for parser in (parse_natural_intent, parse_unified_intent):
            plan = parser(query, connection=db, today=date(2026, 10, 21))
            assert plan["state"] == "unsupported", (parser.__name__, query, plan)
            assert plan.get("capture_from") is None
            assert plan.get("capture_to_exclusive") is None
            assert plan.get("person_ids", []) == []
            assert plan.get("choices", []) == []
            assert plan["notice"]


@pytest.mark.parametrize("query", [
    "12024年照片", "abc2024-07 cats",
    "照片上写着2024年7月", "带有2024年7月字样的照片", "印着2024-07的票据",
    "photos with text July 2024", 'a sign reading "2024年7月"',
])
def test_numbers_and_printed_dates_remain_content(query):
    with coverage_catalog() as db:
        for parser in (parse_natural_intent, parse_unified_intent):
            plan = parser(query, connection=db, today=date(2026, 10, 21))
            assert plan.get("capture_from") is None, (parser.__name__, query, plan)
            assert plan.get("capture_to_exclusive") is None
            assert plan["content_query"] and "2024" in plan["content_query"]


@pytest.mark.parametrize(("query", "bounds"), [
    ("2024年7月的猫", ("2024-07-01", "2024-08-01")),
    ("2024-07 cats", ("2024-07-01", "2024-08-01")),
    ("July 2024 cats", ("2024-07-01", "2024-08-01")),
    ("2024 July cats", ("2024-07-01", "2024-08-01")),
    ("2024年2月29日的猫", ("2024-02-29", "2024-03-01")),
    ("2024年10月到12月的照片", ("2024-10-01", "2025-01-01")),
    ("去年8月的照片", ("2025-08-01", "2025-09-01")),
])
def test_supported_dates_share_exact_half_open_bounds(query, bounds):
    with coverage_catalog() as db:
        for parser in (parse_natural_intent, parse_unified_intent):
            plan = parser(query, connection=db, today=date(2026, 10, 21))
            assert plan["state"] == "ready"
            assert (plan["capture_from"], plan["capture_to_exclusive"]) == bounds


def test_repeated_date_text_keeps_the_printed_occurrence():
    query = "写着2024年7月，2024年7月的猫"
    with coverage_catalog() as db:
        for parser in (parse_natural_intent, parse_unified_intent):
            plan = parser(query, connection=db, today=date(2026, 10, 21))
            assert plan["capture_from"] == "2024-07-01"
            assert plan["content_query"].count("2024年7月") == 1
            assert "写着2024年7月" in plan["content_query"]
            if "candidates" in plan:
                temporal = next(item for item in plan["candidates"] if item["type"] == "time")
                assert temporal["start"] == query.rindex("2024年7月")
