from __future__ import annotations

import sqlite3
from datetime import date

from photo_search.search.natural_intent import parse_natural_intent


def catalog() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        "CREATE TABLE persons(person_id INTEGER PRIMARY KEY,name TEXT,merged_into INTEGER);"
        "CREATE TABLE person_aliases(person_id INTEGER,alias TEXT);"
        "INSERT INTO persons VALUES (1,'张三',NULL),(2,'李四',NULL),(3,'张三',NULL);"
        "INSERT INTO person_aliases VALUES (1,'小张'),(2,'小李');"
    )
    return connection


def test_date_shapes_and_relative_ranges_are_half_open_and_frozen() -> None:
    connection = catalog()
    cases = {
        "2023年的照片": (("2023-01-01", "2024-01-01"), None),
        "2023年7月照片": (("2023-07-01", "2023-08-01"), None),
        "2023年7月5日日落": (("2023-07-05", "2023-07-06"), "日落"),
        "2023-12-31": (("2023-12-31", "2024-01-01"), None),
        "去年夏天的照片": (("2025-06-01", "2025-09-01"), None),
        "今年冬天": (("2026-12-01", "2027-03-01"), None),
        "上个月的照片": (("2026-09-01", "2026-10-01"), None),
    }
    for query, (bounds, content) in cases.items():
        plan = parse_natural_intent(query, connection=connection, today=date(2026, 10, 9))
        assert (plan["capture_from"], plan["capture_to_exclusive"]) == bounds
        assert plan["content_query"] == content


def test_people_all_any_ambiguous_and_unknown_names() -> None:
    connection = catalog()
    all_people = parse_natural_intent("张三和李四的合照", connection=connection)
    assert all_people["state"] == "ambiguous"
    assert [item["person_id"] for item in all_people["choices"]] == [1, 3]
    assert all_people["known_person_ids"] == [2]
    assert all_people["person_match"] == "all"
    assert all_people["content_query"] is None

    unique_all = parse_natural_intent("小张和小李的合照", connection=connection)
    assert [item["person_id"] for item in unique_all["choices"]] == [1, 2]
    assert unique_all["person_match"] == "all"

    any_people = parse_natural_intent("小张或者小李的照片", connection=connection)
    assert [item["person_id"] for item in any_people["choices"]] == [1, 2]
    assert any_people["person_match"] == "any"

    ambiguous = parse_natural_intent("张三的照片", connection=connection)
    assert ambiguous["state"] == "ambiguous"
    assert {item["person_id"] for item in ambiguous["choices"]} == {1, 3}

    unknown = parse_natural_intent("王五的日落", connection=connection)
    assert unknown["choices"] == []
    assert unknown["content_query"] == "王五的日落"


def test_time_person_and_content_are_separated() -> None:
    plan = parse_natural_intent(
        "2023年张三的日落照片", connection=catalog(), today=date(2026, 10, 9)
    )
    assert plan["capture_from"] == "2023-01-01"
    assert [item["person_id"] for item in plan["choices"]] == [1, 3]
    assert plan["content_query"] == "日落"
