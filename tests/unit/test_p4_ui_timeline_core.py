"""Focused P4 UI timeline query and cursor contract tests using isolated catalogs."""

from __future__ import annotations

from datetime import datetime

import pytest
from conftest import ApiEnv
from p3_persons_page_fixtures import add_link, add_person, seed_library
from p4_time_fixtures import assets_by_basename, run_extraction, write_exif_jpeg, write_plain_jpeg

from photo_search.api.errors import ApiError
from photo_search.api.timeline import timeline_page, timeline_periods
from photo_search.persons.collections import create_collection, edit_members
from photo_search.places import revision


def seed_timeline(env: ApiEnv) -> tuple[int, dict[str, int]]:
    write_exif_jpeg(env.library / "同日A.jpg", original="2022:02:28 23:59:59", color=(20, 30, 40))
    write_exif_jpeg(env.library / "同日B.jpg", original="2022:02:28 23:59:59", color=(60, 70, 80))
    write_exif_jpeg(env.library / "仅根时间.jpg", root="2019:01:02 03:04:05", color=(90, 100, 110))
    write_plain_jpeg(env.library / "已抽取无日期.jpg")
    write_exif_jpeg(env.library / "待抽取.jpg", original="2023:03:04 05:06:07", color=(130, 140, 150))
    source = env.register_source()
    env.scan(source["source_id"])
    ids = assets_by_basename(env.service.catalog)
    run_extraction(env.service.catalog, asset_ids=[ids[name] for name in ids if name != "待抽取.jpg"])
    return int(source["source_id"]), ids


def test_timeline_buckets_keyset_periods_and_direct_year_seek(api_env: ApiEnv) -> None:
    source_id, ids = seed_timeline(api_env)
    catalog = api_env.service.catalog
    page1 = timeline_page(catalog, kind="all", limit=2)
    page2 = timeline_page(catalog, kind="all", cursor=page1["next_cursor"], limit=2)
    page3 = timeline_page(catalog, kind="all", cursor=page2["next_cursor"], limit=2)
    found = [item["asset_id"] for page in (page1, page2, page3) for item in page["assets"]]
    assert found == [ids["同日B.jpg"], ids["同日A.jpg"], ids["已抽取无日期.jpg"], ids["仅根时间.jpg"], ids["待抽取.jpg"]]
    assert len(found) == len(set(found)) == 5
    assert page1["assets"][0]["display_time"] == "2022-02-28T23:59:59.000"
    assert page1["assets"][0]["group_key"] == "2022-02-28"
    assert page2["assets"][0]["date_state"] == "unknown"
    assert page3["assets"][0]["date_state"] == "pending"
    assert all(item["thumbnail_url"].endswith(f"/{item['asset_id']}/thumbnail") for page in (page1, page2, page3) for item in page["assets"])

    periods = timeline_periods(catalog, kind="all", time_basis="capture")
    assert periods["counts"] == {"effective_total": 5, "trusted": 2, "unknown": 2, "pending": 1}
    assert periods["counts_conserve"] is True
    assert periods["years"] == [{"year": 2022, "count": 2}]
    months = timeline_periods(catalog, kind="source", source_id=source_id, year=2022)
    assert months["months"] == [{"month": 2, "count": 2}]
    assert [item["asset_id"] for item in timeline_page(catalog, year=2022, month=2)["assets"]] == [ids["同日B.jpg"], ids["同日A.jpg"]]
    assert {item["date_state"] for item in timeline_page(catalog, bucket="unknown")["assets"]} == {"unknown"}
    assert {item["date_state"] for item in timeline_page(catalog, bucket="pending")["assets"]} == {"pending"}


def test_library_timeline_cursor_and_direct_year_month_seek(api_env: ApiEnv) -> None:
    _, _ = seed_timeline(api_env)
    catalog = api_env.service.catalog
    all_page = timeline_page(catalog, time_basis="library", limit=2)
    next_page = timeline_page(catalog, time_basis="library", cursor=all_page["next_cursor"], limit=2)
    assert [item["created_at"] for item in all_page["assets"] + next_page["assets"]] == sorted(
        [item["created_at"] for item in all_page["assets"] + next_page["assets"]], reverse=True
    )
    first = all_page["assets"][0]
    local = datetime.fromtimestamp(first["created_at"] / 1000).astimezone()
    sought = timeline_page(catalog, time_basis="library", seek_year=local.year,
                           seek_month=local.month, limit=2)
    assert sought["assets"]
    assert all(item["date_state"] == "library" for item in sought["assets"])


def test_timeline_cursor_stales_on_catalog_revision_and_rules_mismatch_is_pending(api_env: ApiEnv) -> None:
    _, _ = seed_timeline(api_env)
    catalog = api_env.service.catalog
    page = timeline_page(catalog, limit=1)
    with catalog.write() as connection:
        revision.bump_filter_revision(connection, cause="TEST_TIMELINE_CHANGE", now_ms=catalog.now_ms())
    with pytest.raises(ApiError) as stale:
        timeline_page(catalog, cursor=page["next_cursor"], limit=1)
    assert stale.value.code == "CATALOG_FILTER_REVISION_STALE"
    assert stale.value.status == 409
    repositioned = timeline_page(catalog, anchor=page["assets"][0]["anchor"], limit=1)
    assert repositioned["assets"][0]["asset_id"] == page["assets"][0]["asset_id"]

    with catalog.write() as connection:
        connection.execute("UPDATE metadata SET value='999' WHERE key='taken_time_rules_version'")
    periods = timeline_periods(catalog)
    assert periods["counts"] == {"effective_total": 5, "trusted": 0, "unknown": 0, "pending": 5}
    page = timeline_page(catalog, limit=10)
    assert {item["date_state"] for item in page["assets"]} == {"pending"}
    assert all(item["time_source"] is None and item["group_key"] is None for item in page["assets"])


def test_timeline_cursor_stales_after_real_capture_extraction_change(api_env: ApiEnv) -> None:
    _, ids = seed_timeline(api_env)
    catalog = api_env.service.catalog
    first = timeline_page(catalog, limit=1)
    before = revision.read_filter_revision(catalog.connection)
    run_extraction(catalog, asset_ids=[ids["待抽取.jpg"]])
    after_change = revision.read_filter_revision(catalog.connection)
    assert after_change == before + 1
    with pytest.raises(ApiError) as stale:
        timeline_page(catalog, cursor=first["next_cursor"], limit=1)
    assert stale.value.code == "CATALOG_FILTER_REVISION_STALE"
    assert stale.value.status == 409
    run_extraction(catalog, asset_ids=[ids["待抽取.jpg"]])
    assert revision.read_filter_revision(catalog.connection) == after_change


def test_timeline_scope_validation_is_explicit_and_source_is_authoritative(api_env: ApiEnv) -> None:
    source_id, ids = seed_timeline(api_env)
    catalog = api_env.service.catalog
    page = timeline_page(catalog, kind="source", source_id=source_id)
    assert {item["asset_id"] for item in page["assets"]} == set(ids.values())
    with pytest.raises(ApiError) as missing:
        timeline_page(catalog, kind="source", source_id=999999)
    assert missing.value.code == "SOURCE_NOT_FOUND"
    with pytest.raises(ApiError) as no_collection:
        timeline_page(catalog, kind="collection", collection_id=999999)
    assert no_collection.value.code == "PERSON_COLLECTION_NOT_FOUND"
    with pytest.raises(ApiError) as deferred_place:
        timeline_page(catalog, kind="place")
    assert deferred_place.value.code == "TIMELINE_SCOPE_DEFERRED"


def test_person_and_collection_scopes_use_effective_unique_photo_members(api_env: ApiEnv) -> None:
    photos = seed_library(api_env, {
        "同一合照.jpg": [(20, 20, 90, 90), (120, 20, 190, 90), (210, 20, 280, 90)],
        "人物单照.jpg": [(30, 30, 100, 100)],
        "无归属照片.jpg": [],
    })
    catalog = api_env.service.catalog
    first_person = add_person(catalog, name="人物甲")
    second_person = add_person(catalog, name="人物乙")
    add_link(catalog, photos["同一合照.jpg"]["faces"][0], first_person)
    add_link(catalog, photos["同一合照.jpg"]["faces"][1], first_person)
    add_link(catalog, photos["人物单照.jpg"]["faces"][0], first_person)
    add_link(catalog, photos["同一合照.jpg"]["faces"][2], second_person)
    person_ids = {photos["同一合照.jpg"]["asset_id"], photos["人物单照.jpg"]["asset_id"]}
    assert {item["asset_id"] for item in timeline_page(catalog, kind="person", person_id=first_person)["assets"]} == person_ids

    collection = create_collection(catalog, "测试合集")["collection"]
    edit_members(catalog, collection["collection_id"], action="add", person_ids=[first_person, second_person],
                 instance_token=collection["instance_token"])
    first = timeline_page(catalog, kind="collection", collection_id=collection["collection_id"], limit=1)
    collected = timeline_page(catalog, kind="collection", collection_id=collection["collection_id"],
                              cursor=first["next_cursor"], limit=2)
    all_collection_assets = [item["asset_id"] for item in first["assets"] + collected["assets"]]
    assert set(all_collection_assets) == person_ids
    assert len(all_collection_assets) == len(set(all_collection_assets))

    edit_members(catalog, collection["collection_id"], action="remove", person_ids=[second_person],
                 instance_token=collection["instance_token"])
    with pytest.raises(ApiError) as changed:
        timeline_page(catalog, kind="collection", collection_id=collection["collection_id"],
                      cursor=first["next_cursor"], limit=1)
    assert changed.value.status == 409


def test_timeline_http_routes_are_session_protected_and_readable(api_env: ApiEnv) -> None:
    seed_timeline(api_env)

    async def scenario(client):
        denied = await client.get("/api/v1/timeline/assets")
        assert denied.status_code == 401
        await api_env.authenticate(client)
        first = await client.get("/api/v1/timeline/assets", params={"limit": 2})
        periods = await client.get("/api/v1/timeline/periods")
        assert first.status_code == 200, first.text
        assert periods.status_code == 200, periods.text
        body = first.json()
        assert len(body["assets"]) == 2 and body["next_cursor"]
        assert all("display_time" in item and "thumbnail_url" in item for item in body["assets"])
        next_page = await client.get("/api/v1/timeline/assets", params={"limit": 2, "cursor": body["next_cursor"]})
        assert next_page.status_code == 200, next_page.text
        return periods.json()

    periods = api_env.scenario(scenario)
    assert periods["counts_conserve"]
