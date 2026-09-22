"""P4-00B: the capture condition, the ``capture_desc`` ordering and the honesty block.

The listing, the detail object and the search envelope all have to keep the frozen shapes of
ADR-0009 while adding exactly what ADR-0011 §8 declares: two always-present echo keys, one
*conditional* honesty block, and - for the detail endpoint only - the closed per-asset
object. Everything here runs against the real application over a synthetic library.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from p4_time_fixtures import (
    assets_by_basename,
    filter_revision,
    run_extraction,
    write_exif_jpeg,
    write_plain_jpeg,
)

from photo_search.taken_time.contract import RULES_VERSION_KEY

#: The P1-05 listing keys plus the two the ADR adds; ``taken_time`` is deliberately absent
#: unless the request carried a capture bound.
LISTING_KEYS = {
    "assets",
    "limit",
    "next_cursor",
    "has_more",
    "availability",
    "has_thumbnail",
    "source_id",
    "order",
    "place_ids",
    "include_descendants",
    "place_state",
    "catalog_filter_revision",
    "suggestion_revision",
    "search",
    "capture_from",
    "capture_to_exclusive",
}

DETAIL_KEYS = {
    "taken_at",
    "taken_at_sort",
    "time_source",
    "time_confidence",
    "timezone_offset_minutes",
    "label",
    "camera",
}

#: The live ``filters`` echo of ADR-0011 §8.2: the eleven ADR-0009 keys plus the two capture
#: keys (and the service-added ``binding``).
FILTERS_V2_KEYS = {
    "source_id",
    "availability",
    "place_ids",
    "include_descendants",
    "place_state",
    "expanded_place_ids",
    "catalog_filter_revision",
    "suggestion_revision",
    "binding",
    "person_ids",
    "person_match",
    "capture_from",
    "capture_to_exclusive",
}


def prepare(api_env) -> dict[str, int]:
    """Five photos covering the whole source chain, scanned and extracted."""
    write_exif_jpeg(
        api_env.library / "原图 有偏移.jpg",
        original="2022:07:05 18:31:07",
        offset_original="+08:00",
        make="TestMake",
        model="TestModel",
    )
    write_exif_jpeg(api_env.library / "原图 无偏移.jpg", original="2022:07:05 18:31:07")
    write_exif_jpeg(api_env.library / "数字化时间.jpg", digitized="2021:03:04 05:06:07", offset_digitized="-05:30")
    write_exif_jpeg(api_env.library / "根时间.jpg", root="2019:01:02 03:04:05")
    write_plain_jpeg(api_env.library / "无拍摄时间.jpg")
    source = api_env.register_source()
    api_env.scan(source["source_id"])
    run_extraction(api_env.service.catalog)
    names = assets_by_basename(api_env.service.catalog)
    assert len(names) == 5
    return names


async def get_json(client: AsyncClient, path: str, **kwargs: Any) -> dict[str, Any]:
    response = await client.get(path, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_listing_adds_only_the_two_echo_keys_and_a_conditional_block(api_env) -> None:
    names = prepare(api_env)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        await api_env.authenticate(client)
        plain = await get_json(client, "/api/v1/assets", params={"limit": 10})
        ranged = await get_json(
            client,
            "/api/v1/assets",
            params={"limit": 10, "capture_from": "2022-01-01"},
        )
        assert names
        return {"plain": plain, "ranged": ranged}

    result = api_env.scenario(scenario)
    plain = result["plain"]
    # The frozen key set plus the two always-present echo keys; no conditional block.
    assert set(plain) == LISTING_KEYS
    assert plain["capture_from"] is None and plain["capture_to_exclusive"] is None
    assert "taken_time" not in plain

    ranged = result["ranged"]
    assert set(ranged) == LISTING_KEYS | {"taken_time"}
    assert ranged["capture_from"] == "2022-01-01T00:00:00.000"
    assert ranged["capture_to_exclusive"] is None
    assert ranged["taken_time"] == {
        "state": "READY",
        "assets_total": 5,
        "extracted_assets": 5,
        "capture_time_assets": 3,
    }


def test_only_capture_labelled_rows_can_match_a_time_condition(api_env) -> None:
    names = prepare(api_env)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        await api_env.authenticate(client)
        everything = await get_json(
            client,
            "/api/v1/assets",
            params={"limit": 10, "capture_from": "0001-01-01", "capture_to_exclusive": "9999-12-31T23:59:59.999"},
        )
        year_2022 = await get_json(
            client,
            "/api/v1/assets",
            params={"limit": 10, "capture_from": "2022-01-01", "capture_to_exclusive": "2023-01-01"},
        )
        left_open = await get_json(
            client, "/api/v1/assets", params={"limit": 10, "capture_to_exclusive": "2020-01-01"}
        )
        return {"everything": everything, "year_2022": year_2022, "left_open": left_open}

    result = api_env.scenario(scenario)
    everything = {item["asset_id"] for item in result["everything"]["assets"]}
    # EXIF_ORIGINAL, EXIF_DIGITIZED and the offset-less original - never EXIF_ROOT or the file
    # time, even though both have a civil key (or a recorded mtime) of their own.
    assert everything == {
        names["原图 有偏移.jpg"],
        names["原图 无偏移.jpg"],
        names["数字化时间.jpg"],
    }
    assert "根时间.jpg" not in names or names["根时间.jpg"] not in everything
    assert names["无拍摄时间.jpg"] not in everything

    # Half-open: the left edge is included, the right edge is not.
    year_ids = {item["asset_id"] for item in result["year_2022"]["assets"]}
    assert year_ids == {names["原图 有偏移.jpg"], names["原图 无偏移.jpg"]}
    left = {item["asset_id"] for item in result["left_open"]["assets"]}
    assert left == {names["根时间.jpg"]} - {names["根时间.jpg"]}  # EXIF_ROOT never matches
    assert left == set()


def test_capture_desc_is_complete_ordered_and_puts_the_tail_last(api_env) -> None:
    names = prepare(api_env)

    async def walk(client: AsyncClient, limit: int) -> dict[str, Any]:
        await api_env.authenticate(client)
        pages: list[dict[str, Any]] = []
        collected: list[int] = []
        cursor = None
        for _ in range(20):
            params: dict[str, Any] = {"order": "capture_desc", "limit": limit}
            if cursor:
                params["cursor"] = cursor
            response = await client.get("/api/v1/assets", params=params)
            assert response.status_code == 200, response.text
            page = response.json()
            pages.append(page)
            collected.extend(int(item["asset_id"]) for item in page["assets"])
            cursor = page["next_cursor"]
            if not page["has_more"]:
                break
        return {"pages": pages, "assets": collected}

    result = api_env.scenario(lambda client: walk(client, 2))
    collected = result["assets"]
    assert len(collected) == len(set(collected)) == 5
    assert all(page["order"] == "capture_desc" for page in result["pages"])
    assert result["pages"][-1]["next_cursor"] is None
    # The ordered bucket first (civil key descending: 2022-07-05, then 2021-03-04, then the
    # 2019 EXIF root date), and the tail bucket last.
    assert collected[:3] == [
        names["原图 有偏移.jpg"],
        names["原图 无偏移.jpg"],
        names["数字化时间.jpg"],
    ]
    assert collected[3] == names["根时间.jpg"]
    assert collected[4] == names["无拍摄时间.jpg"]

    # The default ordering and ``time_desc`` keep their own frozen semantics and cursors.
    async def other_orders(client: AsyncClient) -> dict[str, Any]:
        await api_env.authenticate(client)
        by_id = await get_json(client, "/api/v1/assets", params={"limit": 10})
        timed = await get_json(client, "/api/v1/assets", params={"limit": 2, "order": "time_desc"})
        crossed = await client.get(
            "/api/v1/assets",
            params={"limit": 2, "order": "time_desc", "cursor": result["pages"][0]["next_cursor"]},
        )
        crossed_back = await client.get(
            "/api/v1/assets",
            params={"limit": 2, "order": "capture_desc", "cursor": timed["next_cursor"]},
        )
        return {"by_id": by_id, "timed": timed, "crossed": crossed, "crossed_back": crossed_back}

    other = api_env.scenario(other_orders)
    assert [item["asset_id"] for item in other["by_id"]["assets"]] == sorted(names.values())
    assert other["crossed"].status_code == 422
    assert other["crossed"].json()["code"] == "INVALID_CURSOR"
    assert other["crossed_back"].status_code == 422
    assert other["crossed_back"].json()["code"] == "INVALID_CURSOR"


def test_a_moved_revision_refuses_the_capture_cursor_and_a_reopen_never_skips(api_env) -> None:
    prepare(api_env)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        await api_env.authenticate(client)
        first = await get_json(
            client, "/api/v1/assets", params={"order": "capture_desc", "limit": 2}
        )
        return {"first": first}

    first = api_env.scenario(scenario)["first"]
    assert first["next_cursor"]
    revision_before = int(first["catalog_filter_revision"])

    # Any authoritative edit moves the one shared revision (here: a fresh capture fact).
    write_exif_jpeg(api_env.library / "新增 原图.jpg", original="2024:01:01 00:00:00", offset_original="+08:00")
    api_env.scan(int(api_env.service.catalog.read_one("SELECT MIN(source_id) AS value FROM source_roots")["value"]))
    run_extraction(api_env.service.catalog)
    assert filter_revision(api_env.service.catalog) > revision_before

    async def reuse(client: AsyncClient) -> dict[str, Any]:
        await api_env.authenticate(client)
        stale = await client.get(
            "/api/v1/assets",
            params={
                "order": "capture_desc",
                "limit": 2,
                "cursor": first["next_cursor"],
            },
        )
        # Dropping the cursor must not repeat or skip a row.
        reopened: list[int] = []
        cursor = None
        for _ in range(20):
            params: dict[str, Any] = {"order": "capture_desc", "limit": 2}
            if cursor:
                params["cursor"] = cursor
            page = await get_json(client, "/api/v1/assets", params=params)
            reopened.extend(int(item["asset_id"]) for item in page["assets"])
            cursor = page["next_cursor"]
            if not page["has_more"]:
                break
        return {"stale": stale, "reopened": reopened}

    result = api_env.scenario(reuse)
    stale = result["stale"]
    assert stale.status_code == 409, stale.text
    body = stale.json()
    assert body["code"] == "CATALOG_FILTER_REVISION_STALE"
    assert body["details"]["reason"] == "CATALOG_FILTER_REVISION_CHANGED"
    assert body["details"]["current_catalog_filter_revision"] == filter_revision(
        api_env.service.catalog
    )
    # The listing has no session, so it must not borrow the search-session detail keys.
    assert "search_session_id" not in body["details"]
    assert "session_catalog_filter_revision" not in body["details"]
    assert sorted(result["reopened"]) == sorted(assets_by_basename(api_env.service.catalog).values())


def test_the_detail_object_is_closed_and_never_publishes_the_raw_text(api_env) -> None:
    names = prepare(api_env)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        await api_env.authenticate(client)
        detailed = await get_json(client, f"/api/v1/assets/{names['原图 有偏移.jpg']}")
        file_time = await get_json(client, f"/api/v1/assets/{names['无拍摄时间.jpg']}")
        return {"detailed": detailed, "file_time": file_time}

    result = api_env.scenario(scenario)
    detailed = result["detailed"]
    assert set(detailed["taken_time"]) == DETAIL_KEYS
    assert detailed["taken_time"] == {
        "taken_at": "2022-07-05T18:31:07.000",
        "taken_at_sort": detailed["taken_time"]["taken_at_sort"],
        "time_source": "EXIF_ORIGINAL",
        "time_confidence": "HIGH",
        "timezone_offset_minutes": 480,
        "label": "capture",
        "camera": "TestMake TestModel",
    }
    rendered = str(detailed)
    for private in ("taken_at_raw", "2022:07:05 18:31:07", str(api_env.library), "raw_gps"):
        assert private not in rendered

    assert result["file_time"]["taken_time"] == {
        "taken_at": None,
        "taken_at_sort": None,
        "time_source": "FILE_MTIME",
        "time_confidence": "LOW",
        "timezone_offset_minutes": None,
        "label": "file_time",
        "camera": None,
    }


def test_the_bounds_are_refused_with_the_frozen_error_surface(api_env) -> None:
    prepare(api_env)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        await api_env.authenticate(client)
        return {
            "offset": await client.get(
                "/api/v1/assets", params={"capture_from": "2022-01-01T00:00:00Z"}
            ),
            "zone": await client.get(
                "/api/v1/assets", params={"capture_from": "2022-01-01T00:00:00+08:00"}
            ),
            "malformed": await client.get(
                "/api/v1/assets", params={"capture_from": "2022/01/01"}
            ),
            "calendar": await client.get(
                "/api/v1/assets", params={"capture_from": "2022-02-30"}
            ),
            "range": await client.get(
                "/api/v1/assets",
                params={"capture_from": "2022-01-01", "capture_to_exclusive": "2022-01-01"},
            ),
            "out_of_range": await client.get(
                "/api/v1/assets", params={"capture_from": "0000-01-01"}
            ),
            "bad_order": await client.get(
                "/api/v1/assets", params={"order": "captured_at"}
            ),
        }

    result = api_env.scenario(scenario)
    for key, reason, field in (
        ("offset", "capture_bound_has_offset", "capture_from"),
        ("zone", "capture_bound_has_offset", "capture_from"),
        ("malformed", "capture_bound_malformed", "capture_from"),
        ("calendar", "capture_bound_malformed", "capture_from"),
        ("range", "capture_range_not_ascending", "capture_from"),
        ("out_of_range", "capture_bound_out_of_range", "capture_from"),
    ):
        response = result[key]
        assert response.status_code == 422, (key, response.text)
        body = response.json()
        assert body["code"] == "INVALID_TIME_RANGE"
        assert set(body["details"]) == {"reason", "field"}
        assert body["details"] == {"reason": reason, "field": field}
    assert result["bad_order"].status_code == 422
    assert result["bad_order"].json()["code"] == "VALIDATION_ERROR"


def test_the_search_condition_joins_the_one_and_set_and_echoes_v2(api_env) -> None:
    names = prepare(api_env)
    place_id = 0
    source_id = int(
        api_env.service.catalog.read_one("SELECT MIN(source_id) AS value FROM source_roots")["value"]
    )

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        nonlocal place_id
        csrf = await api_env.authenticate(client)
        created = await client.post(
            "/api/v1/places",
            json={"kind": "city", "canonical_name": "测试城市"},
            headers=api_env.post_headers(csrf),
        )
        assert created.status_code == 201, created.text
        place_id = int(created.json()["place_id"])
        assigned = await client.put(
            f"/api/v1/assets/{names['原图 有偏移.jpg']}/place-assignment",
            json={
                "action": "assign",
                "target_place_id": place_id,
                "request_id": "p4-place-1",
                "expected_revision": 0,
            },
            headers=api_env.post_headers(csrf),
        )
        assert assigned.status_code == 200, assigned.text

        def body(**overrides: Any) -> dict[str, Any]:
            payload: dict[str, Any] = {
                "contract_version": "p2-contracts.v1",
                "request_id": "p4-search-1",
                "page_size": 10,
                "capture_from": "0001-01-01",
                "capture_to_exclusive": "9999-12-31T23:59:59.999",
            }
            payload.update(overrides)
            return payload

        capture_only = await client.post(
            "/api/v1/search", json=body(request_id="p4-search-1"), headers=api_env.post_headers(csrf)
        )
        with_place = await client.post(
            "/api/v1/search",
            json=body(request_id="p4-search-2", place_ids=[place_id]),
            headers=api_env.post_headers(csrf),
        )
        with_other_source = await client.post(
            "/api/v1/search",
            json=body(request_id="p4-search-3", source_id=source_id + 99),
            headers=api_env.post_headers(csrf),
        )
        bad_range = await client.post(
            "/api/v1/search",
            json=body(request_id="p4-search-4", capture_from="2022-01-01T00:00:00Z"),
            headers=api_env.post_headers(csrf),
        )
        bad_order_range = await client.post(
            "/api/v1/search",
            json=body(
                request_id="p4-search-5",
                capture_from="2022-01-01",
                capture_to_exclusive="2022-01-01",
            ),
            headers=api_env.post_headers(csrf),
        )
        return {
            "capture_only": capture_only,
            "with_place": with_place,
            "with_other_source": with_other_source,
            "bad_range": bad_range,
            "bad_order_range": bad_order_range,
        }

    result = api_env.scenario(scenario)
    capture_body = result["capture_only"].json()
    assert result["capture_only"].status_code == 200, result["capture_only"].text
    assert set(capture_body["filters"]) == FILTERS_V2_KEYS
    assert capture_body["filters"]["capture_from"] == "0001-01-01T00:00:00.000"
    assert capture_body["filters"]["capture_to_exclusive"] == "9999-12-31T23:59:59.999"
    assert capture_body["taken_time"] == {
        "state": "READY",
        "assets_total": 5,
        "extracted_assets": 5,
        "capture_time_assets": 3,
    }
    hit_ids = {int(hit["asset_id"]) for hit in capture_body["hits"]}
    assert hit_ids == {
        names["原图 有偏移.jpg"],
        names["原图 无偏移.jpg"],
        names["数字化时间.jpg"],
    }
    assert all(hit["hit_reason"] == "FILTER_ONLY" for hit in capture_body["hits"])

    # The time condition ANDs with the place condition: only the assigned photo survives.
    place_body = result["with_place"].json()
    assert {int(hit["asset_id"]) for hit in place_body["hits"]} == {names["原图 有偏移.jpg"]}
    assert place_body["filters"]["place_ids"] == [place_id]
    assert place_body["filters"]["capture_from"] == "0001-01-01T00:00:00.000"

    # ... and with the source condition: a source that owns nothing answers nothing.
    other_source_body = result["with_other_source"].json()
    assert other_source_body["hits"] == []
    assert other_source_body["filters"]["source_id"] == source_id + 99

    bad = result["bad_range"].json()
    assert result["bad_range"].status_code == 422
    assert bad["code"] == "INVALID_TIME_RANGE"
    assert bad["details"] == {
        "reason": "capture_bound_has_offset",
        "field": "capture_from",
    }
    empty_range = result["bad_order_range"].json()
    assert result["bad_order_range"].status_code == 422
    assert empty_range["details"] == {
        "reason": "capture_range_not_ascending",
        "field": "capture_from",
    }


def test_the_search_continuation_binds_the_capture_condition(api_env) -> None:
    names = prepare(api_env)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await api_env.authenticate(client)
        base = {
            "contract_version": "p2-contracts.v1",
            "request_id": "p4-cont-1",
            "page_size": 1,
            "capture_from": "0001-01-01",
            "capture_to_exclusive": "9999-12-31T23:59:59.999",
        }
        first = await client.post(
            "/api/v1/search", json=base, headers=api_env.post_headers(csrf)
        )
        page = first.json()
        continuation = {
            **base,
            "request_id": "p4-cont-2",
            "search_session_id": page["search_session_id"],
            "cursor": page["next_cursor"],
        }
        same = await client.post(
            "/api/v1/search", json=continuation, headers=api_env.post_headers(csrf)
        )
        other_condition = await client.post(
            "/api/v1/search",
            json={**continuation, "capture_from": "2021-01-01"},
            headers=api_env.post_headers(csrf),
        )
        other_spelling = await client.post(
            "/api/v1/search",
            json={**continuation, "capture_from": "0001-01-01T00:00:00"},
            headers=api_env.post_headers(csrf),
        )
        return {"first": first, "same": same, "other_condition": other_condition, "other_spelling": other_spelling}

    result = api_env.scenario(scenario)
    assert result["first"].status_code == 200 and result["same"].status_code == 200
    assert result["same"].json()["page_offset"] == 1
    # Two spellings of one interval are one condition: the continuation is accepted.
    assert result["other_spelling"].status_code == 200
    # A genuinely different interval with an unchanged revision is a malformed cursor.
    assert result["other_condition"].status_code == 422
    assert result["other_condition"].json()["code"] == "INVALID_CURSOR"
    assert names


def test_an_untrusted_rule_version_hides_the_rows_and_the_filter(api_env) -> None:
    names = prepare(api_env)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        await api_env.authenticate(client)
        before = await get_json(
            client, "/api/v1/assets", params={"limit": 10, "capture_from": "0001-01-01"}
        )
        return {"before": before}

    before = api_env.scenario(scenario)["before"]
    assert len(before["assets"]) == 3

    with api_env.service.catalog.write() as connection:
        connection.execute(
            "UPDATE metadata SET value = '99' WHERE key = ?", (RULES_VERSION_KEY,)
        )

    async def after(client: AsyncClient) -> dict[str, Any]:
        await api_env.authenticate(client)
        listing = await get_json(
            client, "/api/v1/assets", params={"limit": 10, "capture_from": "0001-01-01"}
        )
        detail = await get_json(client, f"/api/v1/assets/{names['原图 有偏移.jpg']}")
        search = await client.post(
            "/api/v1/search",
            json={
                "contract_version": "p2-contracts.v1",
                "request_id": "p4-untrusted-1",
                "capture_from": "0001-01-01",
            },
            headers=api_env.post_headers(api_env.csrf_token or ""),
        )
        return {"listing": listing, "detail": detail, "search": search}

    result = api_env.scenario(after)
    assert result["listing"]["assets"] == []
    assert result["listing"]["taken_time"] == {
        "state": "PENDING",
        "assets_total": 5,
        "extracted_assets": 0,
        "capture_time_assets": 0,
    }
    assert result["detail"]["taken_time"] is None
    assert result["search"].status_code == 200
    assert result["search"].json()["hits"] == []
    assert result["search"].json()["taken_time"]["state"] == "PENDING"
