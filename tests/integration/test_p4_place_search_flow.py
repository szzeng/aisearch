"""Synthetic contract coverage for the additive personal place-aware search entry."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from p2_search_api_fixtures import build_search_env
from p2_search_fixtures import assign_place, create_place

from photo_search.places.albums import album_member_ids
from photo_search.search.documents import catch_up
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
        city_id = create_place(env.catalog, "济南")
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
            env.catalog, env.data_root, kind="city", key=str(city_id)
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
        created = await client.post(
            "/api/v1/places",
            json={"kind": "city", "canonical_name": "济南", "aliases": [{"text": "Jinan", "language": "en"}]},
            headers=api_env.post_headers(csrf),
        )
        assert created.status_code == 201, created.text
        place_id = int(created.json()["place_id"])
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
            json={"request_id": "place-aware-city", "original_query": "济南", "page_size": 1},
            headers=api_env.post_headers(csrf),
        )
        assert selected.status_code == 200, selected.text
        next_page = await client.post(
            "/api/v1/search/place-aware",
            json={
                "request_id": "place-aware-city-next",
                "original_query": "济南",
                "content_query": "",
                "place_kind": "city",
                "place_key": str(place_id),
                "page_size": 1,
                "search_session_id": selected.json()["search_session_id"],
                "cursor": selected.json()["next_cursor"],
            },
            headers=api_env.post_headers(csrf),
        )
        assert next_page.status_code == 200, next_page.text
        cancelled = await client.post(
            "/api/v1/search/place-aware",
            json={"request_id": "place-aware-cancel", "original_query": "济南的日落", "content_query": "", "place_cancelled": True},
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
    assert selected["place_scope"]["name"] == "济南"
    assert [hit["asset_id"] for hit in selected["hits"]] == [assets[0]]
    assert selected["has_more"] is True
    assert [hit["asset_id"] for hit in next_page["hits"]] == [assets[1]]
    assert next_page["page_offset"] == 1
    assert cancelled["place_scope"] is None
    assert {hit["asset_id"] for hit in cancelled["hits"]} == set(assets)
    assert pure_filter["query"] is None
    assert {hit["asset_id"] for hit in pure_filter["hits"]} == set(assets)
