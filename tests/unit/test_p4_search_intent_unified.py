from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from photo_search.search.intent_plan import validate_plan_shape
from photo_search.search.personal_search import PersonalSearchCoordinator
from photo_search.search.unified_intent import parse_unified_intent


def catalog() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        "CREATE TABLE persons(person_id INTEGER PRIMARY KEY,name TEXT,merged_into INTEGER);"
        "CREATE TABLE person_aliases(person_id INTEGER,alias TEXT);"
        "CREATE TABLE person_collections(collection_id INTEGER PRIMARY KEY,instance_token TEXT,name TEXT,revision INTEGER);"
        "CREATE TABLE source_roots(source_id INTEGER PRIMARY KEY,display_name TEXT);"
        "INSERT INTO persons VALUES(1,'张三',NULL),(2,'李四',NULL),(3,'Alice',NULL),(4,'Ann',NULL),(5,'Anna',NULL);"
        "INSERT INTO person_aliases VALUES(1,'小张'),(2,'小李');"
        "INSERT INTO person_collections VALUES(10,'0123456789abcdef0123456789abcdef','同事',3),(11,'1123456789abcdef0123456789abcdef','同事团',1);"
        "INSERT INTO source_roots VALUES(20,'手机备份图库'),(21,'手机备份图库');"
    )
    return db


PLACES = [{"kind": "city", "key": "7", "name": "济南", "parent": "山东", "aliases": [], "source": "OSM"},
          {"kind": "city", "key": "8", "name": "上海", "parent": "中国", "aliases": [], "source": "OSM"}]


def parse(text: str):
    return parse_unified_intent(text, connection=catalog(), place_choices=PLACES, today=date(2026, 10, 9))


def test_five_grounded_intents_and_content_are_resolved_from_original_query() -> None:
    plan = parse("2024年小张在济南拍的日落")
    assert plan["state"] == "ready"
    assert plan["capture_from"] == "2024-01-01"
    assert plan["person_ids"] == [1]
    assert plan["place_kind"] == "city" and plan["place_key"] == "7"
    assert plan["content_query"] == "日落"

    collection = parse("同事合集里的裙子")
    assert collection["collection_scope"]["collection_id"] == 10
    assert collection["content_query"] == "裙子"

    source = parse("手机备份图库里的小张照片")
    assert source["state"] == "needs_choice"
    assert {c["source_id"] for c in source["candidates"] if c["type"] == "source"} == {20, 21}
    assert source["person_ids"] == [1]


def test_conflicts_relations_and_unsupported_phrases_do_not_guess() -> None:
    assert parse("同事团的裙子")["collection_scope"]["collection_id"] == 11
    assert parse("小张和小李的合照")["person_match"] == "all"
    assert parse("小张或小李的照片")["person_match"] == "any"
    assert parse("小张和小李的照片")["state"] == "needs_choice"
    assert parse("排除小张的照片")["state"] == "unsupported"
    assert parse("上海或济南拍的照片")["state"] == "unsupported"
    place_and_collection = parse_unified_intent(
        "同事在济南的照片", connection=catalog(), place_choices=PLACES,
        today=date(2026, 10, 9),
    )
    assert place_and_collection["place_key"] == "7"
    assert place_and_collection["collection_scope"]["collection_id"] == 10
    assert place_and_collection["content_query"] is None


def test_same_person_canonical_name_and_identical_alias_are_one_candidate() -> None:
    db = catalog()
    db.execute("INSERT INTO person_aliases VALUES(3, 'Alice')")
    plan = parse_unified_intent(
        "Alice的照片", connection=db, place_choices=[], today=date(2026, 10, 9),
    )
    people = [item for item in plan["candidates"] if item["type"] == "person"]
    assert plan["state"] == "ready"
    assert plan["person_ids"] == [3]
    assert len(people) == 1


def test_same_named_verified_place_parent_and_child_resolve_to_broader_scope() -> None:
    plan = parse_unified_intent(
        "香港的照片", connection=catalog(), today=date(2026, 10, 9), place_choices=[
            {"kind": "country", "key": "hk-country", "name": "香港", "parent": "", "aliases": [],
             "source": "GEONAMES", "dataset_id": "geo-1"},
            {"kind": "city", "key": "hk-city", "name": "香港", "parent": "香港", "aliases": [],
             "source": "GEONAMES", "dataset_id": "geo-1", "_parent_key": "hk-country"},
        ],
    )
    assert plan["state"] == "ready"
    assert plan["place_kind"] == "country"
    assert plan["place_key"] == "hk-country"


def test_visual_text_and_old_years_are_preserved_or_parsed_with_context() -> None:
    assert parse("照片上写着2024年")["content_query"] == "照片上写着2024年"
    old = parse("1905年的照片")
    assert old["capture_from"] == "1905-01-01"
    assert old["capture_to_exclusive"] == "1906-01-01"
    assert parse("去年夏天的海边")["capture_from"] == "2025-06-01"
    assert parse("Alice夜景")["person_ids"] == [3]
    names = parse("Anna和Ann的合照")
    assert {c["person_id"] for c in names["candidates"] if c["type"] == "person"} == {4, 5}


def test_alias_place_name_is_removed_without_losing_the_photo_tail() -> None:
    plan = parse_unified_intent(
        "在杭州拍的照片", connection=catalog(),
        place_choices=[{"kind": "city", "key": "cn:2", "name": "杭州市", "aliases": ["杭州"]}],
    )
    assert plan["place_key"] == "cn:2"
    assert plan["content_query"] is None


def test_plan_validation_is_bounded_and_bound_to_original_text() -> None:
    plan = {"plan_version": 1, "original_query": "小张的照片", "content_query": None,
            "person_ids": [1], "person_match": "any", "candidates": []}
    assert validate_plan_shape(plan, original_query="小张的照片") is plan
    try:
        validate_plan_shape(plan, original_query="别的查询")
    except ValueError as error:
        assert "不一致" in str(error)
    else:
        raise AssertionError("a plan must be bound to the submitted original query")


def test_coordinator_rejects_client_plan_changes_outside_original_candidates() -> None:
    db = catalog()

    class Catalog:
        connection = db

        @staticmethod
        def read_one(sql: str, params: tuple[Any, ...] = ()):
            return db.execute(sql, params).fetchone()

        @staticmethod
        def read_all(sql: str, params: tuple[Any, ...] = ()):
            return db.execute(sql, params).fetchall()

    coordinator = PersonalSearchCoordinator(Catalog(), None)
    parsed = parse_unified_intent(
        "手机备份图库里的小张照片", connection=db, place_choices=[], today=date(2026, 10, 9),
    )
    valid = {**parsed, "source_id": 20}
    assert coordinator.validate(valid, original_query=parsed["original_query"], parsed=parsed)["source_id"] == 20

    for key, changed in (("content_query", "任意内容词"), ("source_id", 999), ("person_ids", [3])):
        tampered = dict(valid)
        tampered[key] = changed
        try:
            coordinator.validate(tampered, original_query=parsed["original_query"], parsed=parsed)
        except ValueError:
            pass
        else:
            raise AssertionError(f"tampered {key} must be rejected")


def test_source_candidate_spans_map_nfc_matches_back_to_decomposed_original() -> None:
    db = catalog()
    decomposed = "cafe\u0301图库里的日落"
    db.execute("UPDATE source_roots SET display_name='café图库'")
    plan = parse_unified_intent(decomposed, connection=db, place_choices=[])
    source = next(candidate for candidate in plan["candidates"] if candidate["type"] == "source")
    assert decomposed[source["start"]:source["end"]] == "cafe\u0301图库里的"
    assert plan["content_query"] == "日落"
