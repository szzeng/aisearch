from __future__ import annotations

import hashlib
from typing import Any

from httpx import AsyncClient
from p2_search_api_fixtures import build_search_env
from p3_person_search_fixtures import build_person_search_env


def test_pure_filter_keyset_pages_past_5000_without_semantic_session(tmp_path: Any) -> None:
    env = build_search_env(tmp_path)
    try:
        with env.catalog.write() as connection:
            connection.executemany(
                "INSERT INTO assets (sha256,byte_size,created_at,state) VALUES (?,0,1,'ACTIVE')",
                [(hashlib.sha256(f"nl-ux-{index}".encode()).hexdigest(),) for index in range(5002)],
            )

        def forbidden_encoder() -> Any:
            raise AssertionError("pure filter search must not request a query encoder")

        env.context.query_encoder = forbidden_encoder

        async def scenario(client: AsyncClient) -> list[dict[str, Any]]:
            csrf = await env.authenticate(client)
            pages = []
            request_id = 0
            body: dict[str, Any] = {
                "request_id": "nl-filter-0", "original_query": "", "page_size": 100,
            }
            while True:
                response = await client.post(
                    "/api/v1/search/place-aware", json=body, headers=env.post_headers(csrf)
                )
                assert response.status_code == 200, response.text
                page = response.json()
                pages.append(page)
                if not page["has_more"]:
                    break
                request_id += 1
                body = {
                    "request_id": f"nl-filter-{request_id}", "original_query": "",
                    "page_size": 100, "intent_plan": page["intent_plan"],
                    "search_session_id": page["search_session_id"], "cursor": page["next_cursor"],
                }
            return pages

        pages = env.scenario(scenario)
        ids = [int(hit["asset_id"]) for page in pages for hit in page["hits"]]
        assert sum(int(page["total_candidates"]) for page in pages) == len(pages) * 5005
        assert all(page["total_candidates"] == 5005 for page in pages)
        assert len(ids) == 5005 and len(set(ids)) == 5005
        assert pages[0]["search_session_id"] == "filter-only"
        assert pages[-1]["next_cursor"] is None
        assert pages[-1]["has_more"] is False
        assert pages[0]["channels"]["authoritative"]["order"] == "asset_id"
    finally:
        env.context.close()
        env.library.close()


def test_named_person_natural_query_uses_canonical_all_and_any_scope(tmp_path: Any) -> None:
    env = build_person_search_env(tmp_path)
    first = int(env.assets["beach sunset.jpg"])
    second = int(env.assets["杭州西湖 照片.jpg"])
    a = env.person("张三")
    b = env.person("李四")
    face_a = env.face(first)
    face_b = env.face(first, ordinal=1)
    face_a_second = env.face(second)
    env.link(face_a, a)
    env.link(face_b, b)
    env.link(face_a_second, a)

    async def scenario(client: AsyncClient) -> tuple[dict[str, Any], dict[str, Any]]:
        csrf = await env.search.authenticate(client)
        replies = []
        for index, query in enumerate(("张三和李四的合照", "张三或者李四的照片")):
            response = await client.post(
                "/api/v1/search/place-aware",
                json={"request_id": f"nl-person-{index}", "original_query": query, "page_size": 20},
                headers=env.search.post_headers(csrf),
            )
            assert response.status_code == 200, response.text
            replies.append(response.json())
        return replies[0], replies[1]

    try:
        all_page, any_page = env.search.scenario(scenario)
        assert [hit["asset_id"] for hit in all_page["hits"]] == [first]
        assert all_page["filters"]["person_ids"] == [a, b]
        assert all_page["filters"]["person_match"] == "all"
        assert [hit["asset_id"] for hit in any_page["hits"]] == [first, second]
        assert any_page["filters"]["person_ids"] == [a, b]
        assert any_page["filters"]["person_match"] == "any"
        assert all_page["channels"]["vector"]["engaged"] is False
    finally:
        env.close()
