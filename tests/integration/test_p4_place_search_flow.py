"""Synthetic contract coverage for the additive personal place-aware search entry."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from p2_search_api_fixtures import build_search_env
from p2_search_fixtures import assign_place, write_jpeg
from p4_cn_fixtures import install

from photo_search.places import choices as place_choices
from photo_search.places.albums import album_member_ids
from photo_search.search.documents import catch_up
from photo_search.search.profile import activate_profile
from photo_search.search.retrieval import (
    SearchScope,
    fts_channel,
    resolve_binding,
    vector_channel,
)
from photo_search.search.service import search_assets
from photo_search.search.tokens import build_match


def test_place_membership_restricts_both_fts_and_vector_before_ranking(tmp_path: Any) -> None:
    env = build_search_env(tmp_path)
    try:
        install(env.catalog, tmp_path / "places")
        city_id = int(env.catalog.read_one(
            "SELECT place_id FROM place_external_refs WHERE external_id='city-hangzhou'"
        )["place_id"])
        choice = next(item for item in place_choices.choices(
            env.catalog, text="杭州", data_root=env.data_root
        ) if item["kind"] == "city")
        selected_asset = int(env.assets["beach sunset.jpg"])
        assign_place(
            env.catalog,
            asset_id=selected_asset,
            place_id=city_id,
            request_id="p4-place-vector-scope",
        )
        # Reconfirm the controlled fixture row after assignment (which correctly marks
        # vectors stale in production); the fake index below owns the ranked vector result.
        with env.catalog.write() as connection:
            connection.execute(
                "UPDATE index_entries SET state = 'CONFIRMED', confirmed_at = 1 "
                "WHERE generation_id = ? AND asset_id = ?",
                (env.generation, selected_asset),
            )
        member_ids, _ = album_member_ids(
            env.catalog, env.data_root, kind="city", key=choice["key"]
        )
        assert member_ids == [selected_asset]
        result = search_assets(
            env.catalog,
            text="sunset",
            encoder=env.encoder,
            space_id=str(env.spec.envelope["space_id"]),
            generation_id=env.generation,
            dimension=int(env.spec.envelope["dimension"]),
            top_k=10,
            album_asset_ids=member_ids,
        )
        assert [int(row["asset_id"]) for row in result["channels"]["fts"]] == [selected_asset]
        seen: list[int] = []

        class RecordingIndex:
            def search_candidates(self, *, asset_ids: list[int], **kwargs: Any) -> list[dict[str, Any]]:
                seen.extend(int(value) for value in asset_ids)
                return [{"asset_id": int(value), "distance": 0.25} for value in asset_ids]

        binding = resolve_binding(
            env.catalog,
            generation_id=env.generation,
            space_id=str(env.spec.envelope["space_id"]),
            dimension=int(env.spec.envelope["dimension"]),
        )
        vector_result = vector_channel(
            env.catalog,
            index=RecordingIndex(),
            binding=binding,
            query_vector=[1.0, 0.0, 0.0],
            scope=SearchScope.build(album_asset_ids=member_ids),
            top_k=10,
        )
        assert seen == member_ids
        assert [int(row["asset_id"]) for row in vector_result["hits"]] == member_ids
    finally:
        env.context.close()
        env.library.close()


def test_pure_place_search_and_explicit_place_cancellation(api_env: Any) -> None:
    install(api_env.service.catalog, api_env.tmp_path / "places")
    api_env.add_photo("place-sunset.jpg", noise=1)
    api_env.add_photo("other-sunset.jpg", noise=2)
    source = api_env.register_source("place-search-library")
    api_env.scan(int(source["source_id"]))
    assets = [int(row["asset_id"]) for row in api_env.service.catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id")]
    catch_up(api_env.service.catalog)
    scoped_text_hits = fts_channel(
        api_env.service.catalog,
        build_match("sunset"),
        SearchScope.build(album_asset_ids=[assets[0]]),
    )
    assert [int(hit["asset_id"]) for hit in scoped_text_hits] == [assets[0]]

    async def scenario(client: AsyncClient) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        csrf = await api_env.authenticate(client)
        place_id = int(api_env.service.catalog.read_one(
            "SELECT place_id FROM place_external_refs WHERE external_id='city-hangzhou'"
        )["place_id"])
        assigned = await client.put(
            f"/api/v1/assets/{assets[0]}/place-assignment",
            json={"action": "assign", "target_place_id": place_id, "expected_revision": 0, "request_id": "place-aware-assign"},
            headers=api_env.post_headers(csrf),
        )
        assert assigned.status_code == 200, assigned.text
        assigned_second = await client.put(
            f"/api/v1/assets/{assets[1]}/place-assignment",
            json={"action": "assign", "target_place_id": place_id, "expected_revision": 0, "request_id": "place-aware-assign-second"},
            headers=api_env.post_headers(csrf),
        )
        assert assigned_second.status_code == 200, assigned_second.text
        selected = await client.post(
            "/api/v1/search/place-aware",
            json={"request_id": "place-aware-city", "original_query": "杭州", "page_size": 1},
            headers=api_env.post_headers(csrf),
        )
        assert selected.status_code == 200, selected.text
        next_page = await client.post(
            "/api/v1/search/place-aware",
            json={
                "request_id": "place-aware-city-next",
                "original_query": "杭州",
                "content_query": "",
                "place_kind": selected.json()["place_scope"]["kind"],
                "place_key": selected.json()["place_scope"]["key"],
                "page_size": 1,
                "search_session_id": selected.json()["search_session_id"],
                "cursor": selected.json()["next_cursor"],
            },
            headers=api_env.post_headers(csrf),
        )
        assert next_page.status_code == 200, next_page.text
        cancelled = await client.post(
            "/api/v1/search/place-aware",
            json={"request_id": "place-aware-cancel", "original_query": "杭州的日落", "content_query": "", "place_cancelled": True},
            headers=api_env.post_headers(csrf),
        )
        assert cancelled.status_code == 200, cancelled.text
        pure_filter = await client.post(
            "/api/v1/search/place-aware",
            json={"request_id": "place-aware-pure-filter", "original_query": ""},
            headers=api_env.post_headers(csrf),
        )
        assert pure_filter.status_code == 200, pure_filter.text
        return selected.json(), next_page.json(), cancelled.json(), pure_filter.json()

    selected, next_page, cancelled, pure_filter = api_env.scenario(scenario)
    assert selected["place_scope"]["source"] == "OSM"
    assert selected["original_query"] == "杭州"
    assert selected["content_query"] is None
    assert [hit["asset_id"] for hit in selected["hits"]] == [assets[0]]
    assert selected["has_more"] is True
    assert [hit["asset_id"] for hit in next_page["hits"]] == [assets[1]]
    assert next_page["page_offset"] == 1
    assert cancelled["place_scope"] is None
    assert {hit["asset_id"] for hit in cancelled["hits"]} == set(assets)
    assert pure_filter["query"] is None
    assert {hit["asset_id"] for hit in pure_filter["hits"]} == set(assets)


def test_place_aware_api_parses_daily_phrasing_and_keeps_unsafe_queries_unfiltered(tmp_path: Any) -> None:
    env = build_search_env(tmp_path)
    asset_id = int(env.assets["杭州西湖 照片.jpg"])
    install(env.catalog, tmp_path / "places", duplicate=True)
    city_id = int(env.catalog.read_one(
        "SELECT place_id FROM place_external_refs WHERE external_id='city-hangzhou'"
    )["place_id"])
    assign_place(env.catalog, asset_id=asset_id, place_id=city_id, request_id="phrasing-assign")
    env.create_profile("place-nl-profile")
    activate_profile(env.catalog, profile_id="place-nl-profile", now_ms=1_700_000_001_000)
    env.encoder._vectors["日落"] = [1.0, 0.0, 0.0]
    env.encoder._vectors["不是在杭州拍的照片"] = [1.0, 0.0, 0.0]

    async def scenario(client: AsyncClient) -> list[dict[str, Any]]:
        csrf = await env.authenticate(client)
        replies = []
        for index, query in enumerate(
            ["在杭州拍的照片", "杭州的日落", "日落照片，在杭州拍的", "在 杭州 拍的 日落", "不是在杭州拍的照片"]
        ):
            response = await client.post(
                "/api/v1/search/place-aware",
                json={"request_id": f"phrasing-{index}", "original_query": query, "page_size": 2},
                headers=env.post_headers(csrf),
            )
            assert response.status_code == 200, response.text
            replies.append(response.json())
        ambiguous = await client.post(
            "/api/v1/search/place-aware",
            json={"request_id": "phrasing-ambiguous", "original_query": "西湖区的日落"},
            headers=env.post_headers(csrf),
        )
        assert ambiguous.status_code == 200, ambiguous.text
        replies.append(ambiguous.json())
        return replies

    try:
        pure, prefix, suffix, spaced, negative, ambiguous = env.scenario(scenario)
        assert pure["original_query"] == "在杭州拍的照片"
        assert pure["content_query"] is None
        assert pure["place_scope"]["source"] == "OSM"
        for body in (prefix, suffix, spaced):
            assert body["place_scope"]["source"] == "OSM"
            assert body["content_query"] == "日落"
        assert negative["place_scope"] is None
        assert negative["place_parse_notice"]
        assert negative["original_query"] == "不是在杭州拍的照片"
        assert ambiguous["needs_place_choice"] is True
        assert ambiguous["content_query"] == "日落"
        assert len(ambiguous["place_choices"]) == 2
        assert {choice["content_query"] for choice in ambiguous["place_choices"]} == {"日落"}
    finally:
        env.context.close()
        env.library.close()


def test_pure_place_query_pages_all_24_synthetic_assets_without_encoding(tmp_path: Any) -> None:
    env = build_search_env(tmp_path)
    try:
        install(env.catalog, tmp_path / "places")
        for index in range(21):
            write_jpeg(env.library.root / f"synthetic-{index:02d}.jpg", index=index + 10)
        env.library.service.run_scan(env.library.source_id)
        asset_ids = [
            int(row["asset_id"])
            for row in env.catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id")
        ]
        assert len(asset_ids) == 24
        city_id = int(env.catalog.read_one(
            "SELECT place_id FROM place_external_refs WHERE external_id='city-hangzhou'"
        )["place_id"])
        for index, asset_id in enumerate(asset_ids):
            assign_place(
                env.catalog,
                asset_id=asset_id,
                place_id=city_id,
                request_id=f"pure-place-24-{index}",
            )
        env.encoder.calls.clear()

        def forbidden_encoder() -> Any:
            raise AssertionError("pure place paging must not request a text encoder")

        env.context.query_encoder = forbidden_encoder

        async def scenario(client: AsyncClient) -> list[dict[str, Any]]:
            csrf = await env.authenticate(client)
            headers = env.post_headers(csrf)
            pages: list[dict[str, Any]] = []
            body: dict[str, Any] = {
                "request_id": "pure-place-page-0",
                "original_query": "在杭州拍的照片",
                "page_size": 7,
            }
            for index in range(4):
                response = await client.post(
                    "/api/v1/search/place-aware", json=body, headers=headers
                )
                assert response.status_code == 200, response.text
                page = response.json()
                pages.append(page)
                if index < 3:
                    body = {
                        "request_id": f"pure-place-page-{index + 1}",
                        "original_query": "在杭州拍的照片",
                        "content_query": "",
                        "place_kind": page["place_scope"]["kind"],
                        "place_key": page["place_scope"]["key"],
                        "page_size": 7,
                        "search_session_id": page["search_session_id"],
                        "cursor": page["next_cursor"],
                    }
            return pages

        pages = env.scenario(scenario)
        returned = [int(hit["asset_id"]) for page in pages for hit in page["hits"]]
        assert returned == asset_ids
        assert [page["page_offset"] for page in pages] == [0, 7, 14, 21]
        assert [page["total_candidates"] for page in pages] == [24] * 4
        assert [page["original_query"] for page in pages] == ["在杭州拍的照片"] * 4
        assert [page["has_more"] for page in pages] == [True, True, True, False]
        assert env.encoder.calls == []

        async def empty_scenario(client: AsyncClient) -> dict[str, Any]:
            csrf = await env.authenticate(client)
            response = await client.post(
                "/api/v1/search/place-aware",
                json={"request_id": "pure-place-empty", "original_query": "上海市"},
                headers=env.post_headers(csrf),
            )
            assert response.status_code == 200, response.text
            return response.json()

        empty = env.scenario(empty_scenario)
        assert empty["place_scope"]["key"].startswith("cn:")
        assert empty["total_candidates"] == 0
        assert empty["hits"] == []
        assert env.encoder.calls == []

        write_jpeg(env.library.root / "late-member.jpg", index=99)
        env.library.service.run_scan(env.library.source_id)
        late_asset_id = int(
            env.catalog.read_one(
                "SELECT asset_id FROM file_locations WHERE relative_path='late-member.jpg'"
            )["asset_id"]
        )
        assign_place(
            env.catalog,
            asset_id=late_asset_id,
            place_id=city_id,
            request_id="pure-place-revise-members",
        )

        async def stale_continuation(client: AsyncClient) -> Any:
            csrf = await env.authenticate(client)
            return await client.post(
                "/api/v1/search/place-aware",
                json={
                    "request_id": "pure-place-stale-page",
                    "original_query": "在杭州拍的照片",
                    "content_query": "",
                        "place_kind": pages[0]["place_scope"]["kind"],
                        "place_key": pages[0]["place_scope"]["key"],
                    "page_size": 7,
                    "search_session_id": pages[0]["search_session_id"],
                    "cursor": pages[0]["next_cursor"],
                },
                headers=env.post_headers(csrf),
            )

        stale = env.scenario(stale_continuation)
        assert stale.status_code == 409
        assert stale.json()["code"] == "CATALOG_FILTER_REVISION_STALE"
    finally:
        env.context.close()
        env.library.close()
