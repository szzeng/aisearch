from __future__ import annotations

import hashlib
from typing import Any

from p2_search_api_fixtures import build_search_env
from p3_person_search_fixtures import _FaceSerial, add_face, add_link, add_person
from p3_persons_page_fixtures import seed_library

from photo_search.favorites import set_favorite
from photo_search.search.profile import activate_profile
from photo_search.search.retrieval import SearchScope, scope_condition


def _favorite(catalog: Any, asset_id: int) -> None:
    set_favorite(catalog, asset_id, favorite=True)


def test_favorites_filter_only_is_keyset_paged_and_revision_bound(api_env):
    seeded = seed_library(api_env, {"one.jpg": [], "two.jpg": [], "three.jpg": [], "four.jpg": []})
    first_id = seeded["one.jpg"]["asset_id"]
    second_id = seeded["two.jpg"]["asset_id"]

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        body = {"request_id": "favorites-filter", "retrieval_mode": "text",
                "original_query": "", "favorites_only": True, "page_size": 1}
        empty = await client.post("/api/v1/search/place-aware", headers=headers, json=body)
        assert empty.status_code == 200, empty.text
        assert empty.json()["total_candidates"] == 0
        assert empty.json()["hits"] == []
        _favorite(api_env.service.catalog, first_id)
        _favorite(api_env.service.catalog, second_id)
        _favorite(api_env.service.catalog, seeded["three.jpg"]["asset_id"])
        first = await client.post("/api/v1/search/place-aware", headers=headers, json=body)
        assert first.status_code == 200, first.text
        page = first.json()
        assert page["favorites_only"] is True
        assert page["favorites_revision"] == 3
        assert page["total_candidates"] == 3
        assert len(page["hits"]) == 1 and page["next_cursor"]
        assert page["hits"][0]["asset_id"] == first_id
        continuation = {**body, "search_session_id": page["search_session_id"],
                        "cursor": page["next_cursor"]}
        _favorite(api_env.service.catalog, first_id)  # an idempotent write keeps the cursor valid
        second_page = await client.post("/api/v1/search/place-aware", headers=headers, json=continuation)
        assert second_page.status_code == 200, second_page.text
        ordinary = await client.post("/api/v1/search/place-aware", headers=headers,
            json={"request_id": "ordinary-filter", "retrieval_mode": "text",
                  "original_query": "", "page_size": 1})
        assert ordinary.status_code == 200, ordinary.text
        ordinary_body = ordinary.json()
        assert ordinary_body["favorites_only"] is False
        assert "favorites_revision" not in ordinary_body
        stale_cursor = {**body, "search_session_id": second_page.json()["search_session_id"],
                        "cursor": second_page.json()["next_cursor"]}
        _favorite(api_env.service.catalog, seeded["four.jpg"]["asset_id"])
        stale = await client.post("/api/v1/search/place-aware", headers=headers, json=stale_cursor)
        assert stale.status_code == 409
        assert stale.json()["code"] == "FAVORITES_REVISION_STALE"
        ordinary_next = await client.post("/api/v1/search/place-aware", headers=headers,
            json={"request_id": "ordinary-filter", "retrieval_mode": "text",
                  "original_query": "", "page_size": 1,
                  "search_session_id": ordinary_body["search_session_id"],
                  "cursor": ordinary_body["next_cursor"]})
        assert ordinary_next.status_code == 200, ordinary_next.text
        return page

    api_env.scenario(scenario)


def test_text_candidates_are_limited_to_favorites_before_top_k(api_env):
    seeded = seed_library(api_env, {"sunset-first.jpg": [], "sunset-favorite.jpg": [],
                                     "other.jpg": []})
    favorite_id = seeded["sunset-favorite.jpg"]["asset_id"]
    _favorite(api_env.service.catalog, favorite_id)

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        response = await client.post("/api/v1/search/place-aware",
            headers=api_env.post_headers(csrf),
            json={"request_id": "favorites-top-k", "retrieval_mode": "text",
                  "original_query": "sunset", "favorites_only": True,
                  "top_k": 1, "page_size": 10})
        assert response.status_code == 200, response.text
        body = response.json()
        assert [hit["asset_id"] for hit in body["hits"]] == [favorite_id]
        assert body["total_candidates"] == 1

    api_env.scenario(scenario)


def test_favorites_only_requires_a_strict_boolean(api_env):
    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        responses = []
        for value in ("true", 1):
            responses.append(await client.post("/api/v1/search/place-aware", headers=headers,
                json={"request_id": "favorites-strict-bool", "retrieval_mode": "text",
                      "original_query": "", "favorites_only": value}))
        return responses

    responses = api_env.scenario(scenario)
    assert all(response.status_code == 422 for response in responses)


def test_favorites_filter_intersects_the_fixed_person_scope(tmp_path):
    env = build_search_env(tmp_path)
    try:
        catalog = env.catalog
        person_id = add_person(catalog, name="收藏交集人物")
        serial = _FaceSerial()
        member_ids = [int(env.assets[name]) for name in ("杭州西湖 照片.jpg", "beach sunset.jpg")]
        outside_id = int(env.assets["IMG_0001.JPG"])
        for asset_id in member_ids:
            add_link(catalog, add_face(catalog, serial, asset_id), person_id)
        _favorite(catalog, member_ids[0])
        _favorite(catalog, outside_id)

        async def scenario(client):
            csrf = await env.authenticate(client)
            response = await client.post("/api/v1/search/place-aware",
                headers=env.post_headers(csrf),
                json={"request_id": "favorites-person-and", "original_query": "",
                      "retrieval_mode": "text", "favorites_only": True,
                      "person_ids": [person_id], "page_size": 10})
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["favorites_only"] is True
            assert body["filters"]["person_ids"] == [person_id]
            assert [int(hit["asset_id"]) for hit in body["hits"]] == [member_ids[0]]
            assert body["total_candidates"] == 1

        env.scenario(scenario)
    finally:
        env.context.close()
        env.library.close()


def test_semantic_time_browse_and_cursor_refuse_changed_favorites(api_env):
    seeded = seed_library(api_env, {"sunset-one.jpg": [], "sunset-two.jpg": [],
                                     "sunset-three.jpg": [], "other.jpg": []})
    first_id = seeded["sunset-one.jpg"]["asset_id"]
    _favorite(api_env.service.catalog, first_id)
    _favorite(api_env.service.catalog, seeded["sunset-two.jpg"]["asset_id"])
    _favorite(api_env.service.catalog, seeded["sunset-three.jpg"]["asset_id"])

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        body = {"request_id": "favorites-time", "retrieval_mode": "text",
                "original_query": "sunset", "favorites_only": True,
                "top_k": 10, "page_size": 1}
        first = await client.post("/api/v1/search/place-aware", headers=headers, json=body)
        assert first.status_code == 200, first.text
        result = first.json()
        browse = await client.get(
            f"/api/v1/search/sessions/{result['search_session_id']}/time?limit=1")
        assert browse.status_code == 200, browse.text
        assert browse.json()["assets"]
        assert browse.json()["next_cursor"]
        _favorite(api_env.service.catalog, seeded["other.jpg"]["asset_id"])
        continuation = await client.post("/api/v1/search/place-aware", headers=headers,
            json={**body, "search_session_id": result["search_session_id"],
                  "cursor": result["next_cursor"]})
        stale_time = await client.get(
            f"/api/v1/search/sessions/{result['search_session_id']}/time?limit=1")
        assert continuation.status_code == stale_time.status_code == 409
        assert continuation.json()["code"] == stale_time.json()["code"] == "FAVORITES_REVISION_STALE"

    api_env.scenario(scenario)


def test_combined_candidates_are_limited_to_favorites_before_top_k(tmp_path):
    env = build_search_env(tmp_path)
    try:
        profile_id = env.create_profile("favorites-profile")
        activate_profile(env.catalog, profile_id=profile_id)
        favorite_id = env.assets["IMG_0001.JPG"]
        _favorite(env.catalog, favorite_id)

        async def scenario(client):
            csrf = await env.authenticate(client)
            response = await client.post("/api/v1/search/place-aware",
                headers=env.post_headers(csrf),
                json={"request_id": "favorites-combined", "original_query": "sunset",
                      "favorites_only": True, "top_k": 1, "page_size": 10})
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["retrieval_mode"] == "combined"
            assert body["favorites_only"] is True
            assert [hit["asset_id"] for hit in body["hits"]] == [favorite_id]
            assert body["channels"]["vector"]["engaged"] is True
            assert body["channels"]["fts"]["candidates"] == 0

        env.scenario(scenario)
    finally:
        env.context.close()
        env.library.close()


def test_favorites_scope_plans_probe_the_member_set_at_multiple_densities(api_env):
    seeded = seed_library(api_env, {"base.jpg": []})
    catalog = api_env.service.catalog
    source_id = int(catalog.read_one("SELECT source_id FROM file_locations LIMIT 1")["source_id"])
    first = max(int(row["asset_id"]) for row in catalog.read_all("SELECT asset_id FROM assets")) + 1
    last = first + 49_999
    assets = [(asset_id, hashlib.sha256(str(asset_id).encode()).hexdigest(), 8, asset_id, "ACTIVE")
              for asset_id in range(first, last + 1)]
    locations = [(source_id, f"synthetic-{asset_id}.jpg", f"synthetic-{asset_id}.jpg", asset_id,
                  8, 1, None, "PRESENT") for asset_id in range(first, last + 1)]
    with catalog.write() as connection:
        connection.executemany(
            "INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) VALUES(?,?,?,?,?)", assets)
        connection.executemany(
            "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,"
            "last_seen_scan_id,availability) VALUES(?,?,?,?,?,?,?,?)", locations)
    scope = SearchScope.build(favorites_only=True)
    plans = {}
    for count in (0, 100, 2_000, 50_000):
        with catalog.write() as connection:
            connection.execute("DELETE FROM asset_favorites")
            connection.executemany(
                "INSERT INTO asset_favorites(asset_id,added_at_ms) VALUES(?,?)",
                ((first + index, index) for index in range(count)),
            )
            connection.execute("ANALYZE asset_favorites")
        where, params = scope_condition(catalog.connection, scope)
        rows = catalog.read_all(
            f"EXPLAIN QUERY PLAN SELECT a.asset_id FROM assets AS a WHERE {where}", tuple(params))
        plans[count] = [str(row["detail"]) for row in rows]
    assert set(plans) == {0, 100, 2_000, 50_000}
    assert all(any("asset_favorites" in item for item in plan) for plan in plans.values())
    assert all(not any(item.startswith("SCAN a") for item in plan) for plan in plans.values())
    assert "base.jpg" in seeded
