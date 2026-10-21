"""P2-06A: ``POST /api/v1/search``, sessions/cursors and the profile binding.

Everything runs on a scanned ``%TEMP%`` library whose vectors were published through the
production artifact/outbox/index path; only the query encoder is the deterministic stand-in
injected through the documented ``encoder_factory`` seam. The cases below are the card's
D-level list: session expiry/restart, cursor isolation, mid-paging authority changes, the
profile activation gate and every caller-shape refusal.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from p2_search_api_fixtures import (
    PHOTOS,
    SESSION_TOKEN,
    SearchEnv,
    build_search_env,
    controllable_sessions,
    fresh_app,
)
from p2_search_fixtures import SPACE, assign_place, confirm_pending, install_source_places

from photo_search.catalog.errors import (
    CATALOG_FILTER_REVISION_STALE,
    INVALID_CURSOR,
    PROFILE_GENERATION_MISMATCH,
    RUNTIME_CAPABILITY_UNAVAILABLE,
    SEARCH_SESSION_EXPIRED,
)
from photo_search.scheduler.capability import record_capability
from photo_search.scheduler.contracts import RuntimeCapabilities
from photo_search.search import service as search_service
from photo_search.search.profile import DEFAULT_FUSION_CONFIG, activate_profile
from photo_search.search.sessions import SESSION_TTL_MS


@pytest.fixture()
def search_env(tmp_path: Any):
    env = build_search_env(tmp_path)
    try:
        yield env
    finally:
        env.context.close()
        env.library.close()


def search_body(env: SearchEnv, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": "req-1",
        **env.explicit_binding(),
        "text": "sunset",
    }
    payload.update(overrides)
    return payload


async def post_search(client: Any, payload: dict[str, Any], csrf: str) -> Any:
    return await client.post("/api/v1/search", json=payload, headers=SearchEnv.post_headers(csrf))


def run_on(app: Any, func: Any) -> Any:
    """Run one async scenario against another application instance (a restarted process)."""

    async def main() -> Any:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1",
            follow_redirects=False,
        ) as client:
            return await func(client)

    return asyncio.run(main())


def test_an_explicitly_bound_text_search_answers_one_reproducible_page(
    search_env: SearchEnv,
) -> None:
    async def scenario(client: Any) -> dict[str, Any]:
        anonymous = await client.post("/api/v1/search", json=search_body(search_env))
        csrf = await search_env.authenticate(client)
        first = await post_search(client, search_body(search_env), csrf)
        second = await post_search(client, search_body(search_env), csrf)
        return {
            "anonymous": anonymous.status_code,
            "first": first,
            "second": second,
            "headers": dict(first.headers),
        }

    result = search_env.scenario(scenario)
    assert result["anonymous"] == 401
    first = result["first"]
    assert first.status_code == 200, first.text
    body = first.json()
    sunset = search_env.assets["beach sunset.jpg"]

    assert body["contract_version"] == "p2-contracts.v1"
    assert body["request_id"] == "req-1"
    assert body["profile_id"] is None
    assert body["image_generation_id"] == search_env.generation
    assert body["space_id"] == SPACE and body["dimension"] == 3
    assert body["state"] == "OK"
    assert body["page_size"] == 20 and body["page_offset"] == 0
    assert body["has_more"] is False and body["next_cursor"] is None
    assert body["search_session_id"]
    assert body["total_candidates"] == len(PHOTOS)
    assert body["candidates_truncated"] is False
    assert body["fusion_config"] == DEFAULT_FUSION_CONFIG
    assert body["query"]["text"] == "sunset"
    assert body["query"]["match_expression"] == '"sunset"*'
    assert body["filters"]["binding"]["generation_id"] == search_env.generation
    assert body["filters"]["binding"]["generation_state"] == "READY"
    assert isinstance(body["catalog_filter_revision"], int)
    assert body["channels"]["fts"] == {"engaged": True, "candidates": 1, "truncated": False}
    assert body["channels"]["vector"]["engaged"] is True
    assert body["channels"]["vector"]["candidates"] == len(PHOTOS)
    assert body["channels"]["authoritative"]["total"] == len(PHOTOS)
    assert body["pending_semantic"]["count"] == 0
    assert body["search_projection"]["state"] == "SYNCED"
    # The first hit is the fused top hit: text and image-text both found it.
    assert body["hits"][0]["asset_id"] == sunset
    assert body["hits"][0]["rank"] == 1
    assert body["hits"][0]["matched_by"] == {"fts": 1, "vector": 1}
    assert body["hits"][0]["hit_reason"] == "TEXT+SEMANTIC"
    assert body["hits"][0]["matched_text"] == ["filename_text"]
    assert body["hits"][0]["semantic_ready"] is True
    assert [row["rank"] for row in body["hits"]] == [1, 2, 3]
    assert result["headers"].get("x-request-id")

    # Two independent requests (no cursor) return the same ranking.
    assert result["second"].status_code == 200
    assert result["second"].json()["hits"] == body["hits"]
    assert result["second"].json()["channels"] == body["channels"]
    assert result["second"].json()["search_session_id"] != body["search_session_id"]
    assert search_env.encoder.calls == ["sunset", "sunset"]

    rendered = first.text
    assert str(search_env.library.root) not in rendered
    assert "C:\\\\" not in rendered
    assert "latitude" not in rendered
    assert SESSION_TOKEN not in rendered


def test_strong_match_annotation_does_not_change_search_order_or_candidate_count(
    search_env: SearchEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(client: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        csrf = await search_env.authenticate(client)
        monkeypatch.setattr(search_service, "strong_match_max_distance", lambda _space_id: -1.0)
        below = await post_search(client, search_body(search_env), csrf)
        assert below.status_code == 200, below.text
        monkeypatch.setattr(search_service, "strong_match_max_distance", lambda _space_id: 1_000_000.0)
        above = await post_search(client, search_body(search_env), csrf)
        assert above.status_code == 200, above.text
        return below.json(), above.json()

    below, above = search_env.scenario(scenario)
    assert [hit["asset_id"] for hit in below["hits"]] == [
        hit["asset_id"] for hit in above["hits"]
    ]
    assert below["total_candidates"] == above["total_candidates"]
    below_vector = below["channels"]["vector"]
    above_vector = above["channels"]["vector"]
    assert below_vector["candidates"] == above_vector["candidates"]
    assert below_vector["strong_match_count"] == 0
    assert above_vector["strong_match_count"] == above_vector["candidates"]


def test_combined_search_freezes_server_only_strong_match_session_evidence(
    search_env: SearchEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(client: Any) -> dict[str, Any]:
        csrf = await search_env.authenticate(client)
        headers = SearchEnv.post_headers(csrf)
        monkeypatch.setattr(search_service, "strong_match_max_distance", lambda _space_id: 1.5)
        request_body = {
            "request_id":"smart-strong-search", "retrieval_mode":"combined",
            "original_query":"sunset", "text":"sunset", **search_env.explicit_binding(),
        }
        response = await client.post("/api/v1/search/place-aware", json=request_body, headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        session = search_env.context.search_sessions.get(body["search_session_id"])
        assert session is not None
        evidence = session.extra["strong_match_evidence"]
        assert evidence["session_id"] == session.session_id
        assert evidence["profile_id"] == session.profile_id
        assert evidence["generation_id"] == session.image_generation_id
        assert evidence["space_id"] == session.space_id
        assert evidence["dimension"] == session.dimension
        assert evidence["threshold"] == 1.5
        assert set(evidence["asset_ids"]).issubset({int(entry.asset_id) for entry in session.entries})
        return {
            "ids": evidence["asset_ids"],
            "count": body["channels"]["vector"]["strong_match_count"],
            "session_ids": [int(entry.asset_id) for entry in session.entries],
        }

    result = search_env.scenario(scenario)
    assert len(result["ids"]) == result["count"]
    assert result["ids"] == [asset_id for asset_id in result["session_ids"] if asset_id in set(result["ids"])]


def test_smart_collection_creation_publishes_only_strong_vector_matches(
    search_env: SearchEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(client: Any) -> list[int]:
        csrf = await search_env.authenticate(client)
        headers = SearchEnv.post_headers(csrf)
        monkeypatch.setattr(search_service, "strong_match_max_distance", lambda _space_id: 0.0)
        response = await client.post("/api/v1/search/place-aware", json={
            "request_id":"smart-strong-search", "retrieval_mode":"combined",
            "original_query":"sunset", "text":"sunset", **search_env.explicit_binding(),
        }, headers=headers)
        assert response.status_code == 200, response.text
        search = response.json()
        assert search["total_candidates"] > search["channels"]["vector"]["strong_match_count"]
        saved = await client.post("/api/v1/photo-smart-collections/from-search", headers=headers, json={
            "name":"Only strong match", "create_request_id":"smart-strong-create",
            "source_token":search["photo_collection_source_token"],
        })
        assert saved.status_code == 200, saved.text
        smart = saved.json()["collection"]
        assert saved.json()["initial_cache"]["candidate_count"] == 1
        page = await client.get(
            f"/api/v1/photo-smart-collections/{smart['smart_id']}/assets",
            params={"instance_token":smart["instance_token"], "limit":50},
        )
        assert page.status_code == 200, page.text
        assert page.json()["freshness"] == "CURRENT"
        return [int(asset["asset_id"]) for asset in page.json()["assets"]]

    visible = search_env.scenario(scenario)
    assert visible == [search_env.assets["beach sunset.jpg"]]


def test_legacy_strong_match_cache_refreshing_is_not_browsed_or_saved(
    search_env: SearchEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    search_env.create_profile("profile-1")
    activate_profile(search_env.catalog, profile_id="profile-1")

    async def scenario(client: Any) -> None:
        csrf = await search_env.authenticate(client)
        headers = SearchEnv.post_headers(csrf)
        monkeypatch.setattr(search_service, "strong_match_max_distance", lambda _space_id: 0.0)
        response = await client.post("/api/v1/search/place-aware", json={
            "request_id": "smart-strong-rebuild-search", "retrieval_mode": "combined",
            "original_query": "sunset", "text": "sunset", **search_env.explicit_binding(),
        }, headers=headers)
        assert response.status_code == 200, response.text
        created = await client.post("/api/v1/photo-smart-collections/from-search", headers=headers, json={
            "name": "Legacy strong cache", "create_request_id": "smart-strong-rebuild-create",
            "source_token": response.json()["photo_collection_source_token"],
        })
        assert created.status_code == 200, created.text
        smart = created.json()["collection"]
        with search_env.catalog.write() as connection:
            connection.execute(
                "UPDATE photo_smart_cache_state SET cache_policy_identity='legacy-policy',refresh_status='RUNNING' "
                "WHERE smart_id=?", (smart["smart_id"],),
            )

        search_calls: list[str] = []

        async def unexpected_search(*_args: Any, **_kwargs: Any) -> Any:
            search_calls.append("called")
            raise AssertionError("a policy-mismatched cache must not trigger a qualifying search")

        monkeypatch.setattr("photo_search.api.routes.run_catalog_search", unexpected_search)
        detail = await client.get(
            f"/api/v1/photo-smart-collections/{smart['smart_id']}",
            params={"instance_token": smart["instance_token"]},
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["collection"]["freshness"] == "REFRESHING"
        assert detail.json()["collection"]["strong_match_rebuild_required"] is True
        page = await client.get(
            f"/api/v1/photo-smart-collections/{smart['smart_id']}/assets",
            params={"instance_token": smart["instance_token"], "limit": 50},
        )
        assert page.status_code == 200, page.text
        assert page.json()["freshness"] == "REFRESHING"
        assert page.json()["assets"] == []
        assert page.json()["refresh_required"] is True
        before = int(search_env.catalog.read_one("SELECT COUNT(*) FROM photo_collections")[0])
        saved = await client.post(
            f"/api/v1/photo-smart-collections/{smart['smart_id']}/save-static", headers=headers, json={
                "instance_token": smart["instance_token"], "expected_revision": smart["revision"],
                "name": "Must not save", "create_request_id": "smart-strong-rebuild-save",
                "confirm_stale": True, "confirm_indexing": True, "confirm_truncated": True,
            },
        )
        assert saved.status_code == 409, saved.text
        assert saved.json()["code"] == "PHOTO_SMART_STRONG_MATCH_REBUILD_REQUIRED"
        assert int(search_env.catalog.read_one("SELECT COUNT(*) FROM photo_collections")[0]) == before
        assert search_calls == []

    search_env.scenario(scenario)


@pytest.mark.parametrize(
    "overrides",
    [
        {"page_size": 0},
        {"page_size": 101},
        {"top_k": 0},
        {"top_k": 5001},
        {"surprise": True},
        {"contract_version": "p2-contracts.v2"},
        {"request_id": ""},
    ],
)
def test_out_of_range_unknown_and_version_mismatched_bodies_are_422(
    search_env: SearchEnv, overrides: dict[str, Any]
) -> None:
    async def scenario(client: Any) -> list[tuple[int, str | None]]:
        csrf = await search_env.authenticate(client)
        payload = search_body(search_env)
        payload.update(overrides)
        response = await post_search(client, payload, csrf)
        json_body = response.json()
        return [(response.status_code, json_body.get("code"))]

    assert search_env.scenario(scenario) == [(422, "VALIDATION_ERROR")]


def test_a_text_query_without_any_binding_is_refused_and_unusable_text_is_422(
    search_env: SearchEnv,
) -> None:
    async def scenario(client: Any) -> dict[str, Any]:
        csrf = await search_env.authenticate(client)
        unbound = await post_search(client, {"request_id": "req-unbound", "text": "sunset"}, csrf)
        partial = await post_search(
            client,
            {"request_id": "req-partial", "text": "sunset", "space_id": SPACE},
            csrf,
        )
        empty = await post_search(client, search_body(search_env, text="   "), csrf)
        quoted = await post_search(client, search_body(search_env, text='"'), csrf)
        long_query = await post_search(client, search_body(search_env, text="杭" * 257), csrf)
        # A filter-only request needs no encoder and no activated profile.
        filter_only = await post_search(client, search_body(search_env, text=None), csrf)
        return {
            "unbound": (unbound.status_code, unbound.json().get("code")),
            "unbound_detail": unbound.json().get("details"),
            "partial": (partial.status_code, partial.json().get("code")),
            "empty": (empty.status_code, empty.json().get("code")),
            "quoted": (quoted.status_code, quoted.json().get("code")),
            "long": (long_query.status_code, long_query.json().get("code")),
            "filter_only": (filter_only.status_code, filter_only.json()),
        }

    result = search_env.scenario(scenario)
    assert result["unbound"] == (422, "VALIDATION_ERROR")
    assert result["unbound_detail"]["field"] == "profile_id"
    assert result["partial"] == (422, "VALIDATION_ERROR")
    for label in ("empty", "quoted", "long"):
        assert result[label] == (422, "VALIDATION_ERROR")
    status, body = result["filter_only"]
    assert status == 200
    assert body["state"] == "OK"
    assert body["query"] is None
    assert body["channels"]["fts"]["engaged"] is False
    assert [row["hit_reason"] for row in body["hits"]] == ["FILTER_ONLY"] * len(PHOTOS)
    assert [row["asset_id"] for row in body["hits"]] == sorted(search_env.assets.values())


def test_paging_walks_one_frozen_order_without_repeats_or_gaps(search_env: SearchEnv) -> None:
    async def scenario(client: Any) -> dict[str, Any]:
        csrf = await search_env.authenticate(client)
        first = await post_search(client, search_body(search_env, text="jpg", page_size=2), csrf)
        body = first.json()
        second = await post_search(
            client,
            search_body(
                search_env,
                text="jpg",
                page_size=2,
                search_session_id=body["search_session_id"],
                cursor=body["next_cursor"],
            ),
            csrf,
        )
        whole = await post_search(client, search_body(search_env, text="jpg", page_size=100), csrf)
        return {
            "first": body,
            "second": second.json(),
            "whole": whole.json(),
            "second_status": second.status_code,
        }

    result = search_env.scenario(scenario)
    first, second, whole = result["first"], result["second"], result["whole"]
    assert result["second_status"] == 200
    assert first["page_offset"] == 0 and first["has_more"] is True
    assert first["next_cursor"] and len(first["hits"]) == 2
    assert second["search_session_id"] == first["search_session_id"]
    assert second["page_offset"] == 2 and second["has_more"] is False
    assert second["next_cursor"] is None and len(second["hits"]) == 1
    # No repeats, no gaps, and the same order as a single wide page of the same query.
    walked = [row["asset_id"] for row in first["hits"] + second["hits"]]
    assert len(set(walked)) == len(walked) == len(PHOTOS)
    assert walked == [row["asset_id"] for row in whole["hits"]]
    assert [row["rank"] for row in first["hits"] + second["hits"]] == [1, 2, 3]
    # The continuation echoes the frozen provenance of the session.
    for key in ("channels", "filters", "fusion_config", "query", "search_projection"):
        assert second[key] == first[key]
    assert second["total_candidates"] == first["total_candidates"]


def test_a_foreign_tampered_or_stale_cursor_is_never_reinterpreted(search_env: SearchEnv) -> None:
    async def scenario(client: Any) -> dict[str, Any]:
        csrf = await search_env.authenticate(client)
        page = (
            await post_search(client, search_body(search_env, text="jpg", page_size=1), csrf)
        ).json()
        other = (
            await post_search(client, search_body(search_env, text="西湖", page_size=1), csrf)
        ).json()
        session_id, cursor = page["search_session_id"], page["next_cursor"]

        def body(**overrides: Any) -> dict[str, Any]:
            payload = search_body(
                search_env,
                text="jpg",
                page_size=1,
                search_session_id=session_id,
                cursor=cursor,
            )
            payload.update(overrides)
            return payload

        same_query = await post_search(client, body(), csrf)
        other_query = await post_search(client, body(text="西湖"), csrf)
        other_filters = await post_search(client, body(place_state="assigned"), csrf)
        cross_session = await post_search(
            client,
            body(search_session_id=other["search_session_id"], cursor=other["next_cursor"]),
            csrf,
        )
        tampered = await post_search(client, body(cursor="!!!not-base64!!!"), csrf)
        truncated = await post_search(client, body(cursor=cursor[:-3]), csrf)
        beyond = await post_search(
            client,
            body(
                cursor=base64.urlsafe_b64encode(
                    f"s1:{session_id}:99".encode()
                ).decode().rstrip("=")
            ),
            csrf,
        )
        no_cursor = await post_search(
            client,
            {k: v for k, v in body().items() if k != "cursor"},
            csrf,
        )
        unknown_session = await post_search(
            client,
            body(
                search_session_id="sess-does-not-exist",
                cursor=base64.urlsafe_b64encode(b"s1:sess-does-not-exist:1")
                .decode()
                .rstrip("="),
            ),
            csrf,
        )
        return {
            "same_query": same_query,
            "other_query": other_query,
            "other_filters": other_filters,
            "cross_session": cross_session,
            "tampered": tampered,
            "truncated": truncated,
            "beyond": beyond,
            "no_cursor": no_cursor,
            "unknown_session": unknown_session,
        }

    result = search_env.scenario(scenario)
    assert result["same_query"].status_code == 200
    assert result["same_query"].json()["page_offset"] == 1
    for label in ("other_query", "other_filters", "cross_session", "tampered", "truncated", "beyond", "no_cursor"):
        response = result[label]
        assert response.status_code == 422, (label, response.text)
        assert response.json()["code"] == INVALID_CURSOR
    expired = result["unknown_session"]
    assert expired.status_code == 409
    assert expired.json()["code"] == SEARCH_SESSION_EXPIRED
    assert expired.json()["details"]["reason"] == "SESSION_NOT_LIVE"


def test_a_restart_or_a_ttl_expiry_is_identified_and_researching_works(
    search_env: SearchEnv,
) -> None:
    clock = controllable_sessions(search_env)

    async def scenario(client: Any) -> dict[str, Any]:
        csrf = await search_env.authenticate(client)
        page = (
            await post_search(client, search_body(search_env, page_size=2), csrf)
        ).json()
        clock["now_ms"] += SESSION_TTL_MS + 1
        expired = await post_search(
            client,
            search_body(
                search_env,
                page_size=2,
                search_session_id=page["search_session_id"],
                cursor=page["next_cursor"],
            ),
            csrf,
        )
        restarted = await post_search(client, search_body(search_env, page_size=2), csrf)
        return {
            "page": page,
            "expired": expired,
            "restarted": restarted,
        }

    result = search_env.scenario(scenario)
    expired = result["expired"]
    assert expired.status_code == 409
    assert expired.json()["code"] == SEARCH_SESSION_EXPIRED
    assert expired.json()["details"]["ttl_ms"] == SESSION_TTL_MS
    # A re-search answers a complete first page again, not an empty or a partial one.
    fresh = result["restarted"].json()
    assert result["restarted"].status_code == 200
    assert fresh["page_offset"] == 0 and len(fresh["hits"]) == 2
    assert fresh["hits"] == result["page"]["hits"]
    assert fresh["search_session_id"] != result["page"]["search_session_id"]


def test_a_restart_has_no_sessions_and_the_old_cursor_is_identified(
    search_env: SearchEnv,
) -> None:
    async def first(client: Any) -> dict[str, Any]:
        csrf = await search_env.authenticate(client)
        return (
            await post_search(client, search_body(search_env, page_size=2), csrf)
        ).json()

    page = search_env.scenario(first)
    # A second application over the same data root models a restarted process: the durable
    # catalog is identical, the in-memory sessions are gone.
    other_app = fresh_app(search_env)
    try:
        async def second(client: Any) -> Any:
            csrf = await search_env.authenticate(client, token=SESSION_TOKEN)
            return await post_search(
                client,
                search_body(
                    search_env,
                    page_size=2,
                    search_session_id=page["search_session_id"],
                    cursor=page["next_cursor"],
                ),
                csrf,
            )

        response = run_on(other_app, second)
    finally:
        other_app.state.api.close()
        search_env.session_id = None
        search_env.csrf_token = None
    assert response.status_code == 409
    assert response.json()["code"] == SEARCH_SESSION_EXPIRED
    assert response.json()["details"]["reason"] == "SESSION_NOT_LIVE"


def test_a_place_edit_between_pages_is_stale_and_a_research_finds_the_new_hit(
    search_env: SearchEnv,
) -> None:
    place_id = install_source_places(search_env.catalog)["hangzhou"]
    confirm_pending(search_env.catalog)

    async def scenario(client: Any) -> dict[str, Any]:
        csrf = await search_env.authenticate(client)
        page = (
            await post_search(client, search_body(search_env, text="jpg", page_size=1), csrf)
        ).json()
        assign_place(
            search_env.catalog,
            asset_id=search_env.assets["beach sunset.jpg"],
            place_id=place_id,
            request_id="req-mid-paging",
        )
        continuation = await post_search(
            client,
            search_body(
                search_env,
                text="jpg",
                page_size=1,
                search_session_id=page["search_session_id"],
                cursor=page["next_cursor"],
            ),
            csrf,
        )
        re_search = await post_search(
            client, search_body(search_env, text="hangzhou", page_size=5), csrf
        )
        return {"continuation": continuation, "re_search": re_search}

    result = search_env.scenario(scenario)
    stale = result["continuation"]
    assert stale.status_code == 409
    assert stale.json()["code"] == CATALOG_FILTER_REVISION_STALE
    assert stale.json()["details"]["reason"] == "CATALOG_FILTER_REVISION_CHANGED"
    fresh = result["re_search"]
    assert fresh.status_code == 200
    body = fresh.json()
    hit = next(row for row in body["hits"] if row["asset_id"] == search_env.assets["beach sunset.jpg"])
    assert hit["matched_text"] == ["place_text"]
    assert body["catalog_filter_revision"] > stale.json()["details"]["session_catalog_filter_revision"]


def test_activating_a_profile_is_atomic_idempotent_and_invalidates_the_old_session(
    search_env: SearchEnv,
) -> None:
    async def scenario(client: Any) -> dict[str, Any]:
        csrf = await search_env.authenticate(client)
        before = await client.get("/api/v1/search-profile")
        unknown = await client.post(
            "/api/v1/search-profile/activate",
            json={"profile_id": "profile-missing"},
            headers=SearchEnv.post_headers(csrf),
        )
        search_env.create_profile("profile-1")
        activated = await client.post(
            "/api/v1/search-profile/activate",
            json={"profile_id": "profile-1"},
            headers=SearchEnv.post_headers(csrf),
        )
        pointer_after_first = search_env.pointer()
        generations_after_first = search_env.generation_state()
        again = await client.post(
            "/api/v1/search-profile/activate",
            json={"profile_id": "profile-1"},
            headers=SearchEnv.post_headers(csrf),
        )
        profile = await client.get("/api/v1/search-profile")
        bound = await post_search(
            client, {"request_id": "req-profile", "text": "sunset", "page_size": 2}, csrf
        )
        bound_body = bound.json()
        pinned = await post_search(
            client,
            {
                "request_id": "req-pinned",
                "text": "sunset",
                "page_size": 2,
                **search_env.explicit_binding(),
            },
            csrf,
        )
        mismatched = await post_search(
            client,
            {
                "request_id": "req-mismatch",
                "text": "sunset",
                "space_id": SPACE,
                "image_generation_id": search_env.generation,
                "dimension": 4,
            },
            csrf,
        )
        continuation = await post_search(
            client,
            {
                "request_id": "req-profile",
                "text": "sunset",
                "page_size": 2,
                "search_session_id": bound_body["search_session_id"],
                "cursor": bound_body["next_cursor"],
            },
            csrf,
        )
        return {
            "before": before,
            "unknown": unknown,
            "activated": activated,
            "again": again,
            "profile": profile,
            "bound": bound,
            "pinned": pinned,
            "mismatched": mismatched,
            "continuation": continuation,
            "pointer_after_first": pointer_after_first,
            "generations_after_first": generations_after_first,
        }

    result = search_env.scenario(scenario)
    assert result["before"].status_code == 200
    assert result["before"].json()["profile_id"] is None
    assert result["before"].json()["profile"] is None
    assert result["unknown"].status_code == 409
    assert result["unknown"].json()["code"] == PROFILE_GENERATION_MISMATCH
    assert result["unknown"].json()["details"]["reason"] == "PROFILE_NOT_FOUND"

    assert result["activated"].status_code == 200, result["activated"].text
    body = result["activated"].json()
    assert body["changed"] is True and body["state"] == "ACTIVE"
    assert body["profile_id"] == "profile-1"
    assert body["image_generation_id"] == search_env.generation
    assert body["readiness"]["device"] == "cpu"
    assert body["readiness"]["provider_name"] == "CPUExecutionProvider"
    assert result["pointer_after_first"] == "profile-1"
    assert result["generations_after_first"][search_env.generation] is True

    assert result["again"].json()["changed"] is False
    assert result["again"].json()["state"] == "ALREADY_ACTIVE"

    profile = result["profile"].json()
    assert profile["profile_id"] == "profile-1"
    assert profile["profile"]["fts_tokenizer_version"] == "1"
    assert profile["profile"]["fts_generation_id"] == "fts-v1"
    assert profile["profile"]["fusion_config"] == DEFAULT_FUSION_CONFIG
    assert profile["profile"]["reranker_processor_id"] is None
    assert str(search_env.library.root) not in result["profile"].text

    bound = result["bound"]
    assert bound.status_code == 200
    assert bound.json()["profile_id"] == "profile-1"
    assert bound.json()["image_generation_id"] == search_env.generation
    assert bound.json()["has_more"] is True
    assert result["pinned"].status_code == 200
    assert result["pinned"].json()["profile_id"] == "profile-1"
    assert result["continuation"].status_code == 200
    assert result["continuation"].json()["page_offset"] == 2

    mismatch = result["mismatched"]
    assert mismatch.status_code == 409
    assert mismatch.json()["code"] == PROFILE_GENERATION_MISMATCH
    assert mismatch.json()["details"]["reason"] == "EXPLICIT_BINDING_MISMATCH"


def test_switching_profiles_moves_the_pointer_and_expires_the_old_session(
    search_env: SearchEnv,
) -> None:
    async def scenario(client: Any) -> dict[str, Any]:
        csrf = await search_env.authenticate(client)
        search_env.create_profile("profile-1")
        await client.post(
            "/api/v1/search-profile/activate",
            json={"profile_id": "profile-1"},
            headers=SearchEnv.post_headers(csrf),
        )
        page = (
            await post_search(
                client,
                {"request_id": "req-old", "text": "sunset", "page_size": 2},
                csrf,
            )
        ).json()
        second_generation = search_env.create_generation(generation_id="image-gen-second")
        search_env.create_profile("profile-2", generation_id=second_generation)
        switched = await client.post(
            "/api/v1/search-profile/activate",
            json={"profile_id": "profile-2"},
            headers=SearchEnv.post_headers(csrf),
        )
        stale = await post_search(
            client,
            {
                "request_id": "req-old",
                "text": "sunset",
                "page_size": 2,
                "search_session_id": page["search_session_id"],
                "cursor": page["next_cursor"],
            },
            csrf,
        )
        fresh = await post_search(
            client, {"request_id": "req-new", "text": "sunset", "page_size": 2}, csrf
        )
        capabilities = await client.get("/api/v1/capabilities")
        return {
            "page": page,
            "switched": switched,
            "stale": stale,
            "fresh": fresh,
            "capabilities": capabilities,
            "generations": search_env.generation_state(),
            "second_generation": second_generation,
        }

    result = search_env.scenario(scenario)
    assert result["switched"].status_code == 200
    assert result["switched"].json()["changed"] is True
    assert result["generations"] == {
        result["second_generation"]: True,
        search_env.generation: False,
    }
    stale = result["stale"]
    assert stale.status_code == 409
    assert stale.json()["code"] == PROFILE_GENERATION_MISMATCH
    assert stale.json()["details"]["reason"] == "SESSION_BINDING_CHANGED"
    # A new request uses the new binding; it never mixes the two generations in one page.
    fresh = result["fresh"]
    assert fresh.status_code == 200
    assert fresh.json()["profile_id"] == "profile-2"
    assert fresh.json()["image_generation_id"] == result["second_generation"]
    assert fresh.json()["channels"]["vector"]["candidates"] == 0
    # Every text hit of the new generation is still unprojected, and it is named as such
    # instead of silently disappearing from the answer.
    assert fresh.json()["pending_semantic"]["count"] == 1
    assert fresh.json()["pending_semantic"]["entries"][0]["asset_id"] == (
        search_env.assets["beach sunset.jpg"]
    )
    assert fresh.json()["pending_semantic"]["reason"] == "ENTRY_NOT_CONFIRMED"

    search = result["capabilities"].json()["search"]
    assert search["active_profile_id"] == "profile-2"
    assert search["active_image_generation_id"] == result["second_generation"]
    assert search["active_fts_generation_id"] == "fts-v1"
    assert search["fts_generation_id"] == "fts-v1"
    assert search["query_encoder_available"] is True
    assert search["pending_projection"]["state"] in ("SYNCED", "LAGGING")
    assert search["documents"] >= len(PHOTOS)


def unhealthy_capability(package: str) -> RuntimeCapabilities:
    return RuntimeCapabilities.seal(
        {
            "runtime_package_id": package,
            "runtime_backend": "onnxruntime",
            "runtime_package_name": package,
            "runtime_package_version": "1",
            "python_version": "3.12.10",
            "platform": "windows-x86_64",
            "driver_version": None,
            "providers": [
                {
                    "name": "CUDAExecutionProvider",
                    "device": "gpu",
                    "precisions": ["FP32"],
                    "available": True,
                    "health": "unhealthy",
                    "health_checked_at": 1,
                    "evidence_id": None,
                }
            ],
            "health_status": "unhealthy",
            "health_checked_at": 1,
            "measured_at": 1,
            "evidence_ids": [],
            "failure_code": "RUNTIME_UNHEALTHY",
        }
    )


def test_every_activation_refusal_is_structured_and_writes_nothing(search_env: SearchEnv) -> None:
    search_env.create_generation(
        generation_id="image-gen-pending", state="PENDING", space_id=SPACE
    )
    search_env.create_generation(
        generation_id="image-gen-unqualified",
        processor_id="unqualified-processor@1",
    )
    with search_env.catalog.write() as connection:
        record_capability(connection, unhealthy_capability("onnxruntime-gpu@test"), now_ms=9_999)
    search_env.create_profile("profile-pending", generation_id="image-gen-pending")
    search_env.create_profile("profile-no-capability", runtime_package_id="onnxruntime-none@1")
    search_env.create_profile("profile-unhealthy", runtime_package_id="onnxruntime-gpu@test")
    search_env.create_profile(
        "profile-unqualified",
        generation_id="image-gen-unqualified",
        processor_id="unqualified-processor@1",
    )
    search_env.create_profile("profile-bad-fts", fts_generation_id="fts-v2")
    search_env.create_profile(
        "profile-bad-fusion",
        fusion_config={
            "revision": "weighted-v1",
            "method": "WEIGHTED",
            "parameters": {"k": 60, "weights": {"fts": 1.0, "vector": 1.0}},
        },
    )
    search_env.create_profile("profile-bad-space", space_id="test-space:foreign")
    before_generations = search_env.generation_state()

    async def scenario(client: Any) -> dict[str, Any]:
        csrf = await search_env.authenticate(client)
        outcomes: dict[str, Any] = {}
        for profile_id, expected in (
            ("profile-missing", "PROFILE_NOT_FOUND"),
            ("profile-pending", "GENERATION_NOT_READY"),
            ("profile-no-capability", "CAPABILITY_MISSING"),
            ("profile-unhealthy", "RUNTIME_UNHEALTHY"),
            ("profile-unqualified", "QUALIFICATION_MISSING"),
            ("profile-bad-fts", "FTS_GENERATION_UNSUPPORTED"),
            ("profile-bad-fusion", "FUSION_CONFIG_UNSUPPORTED"),
            ("profile-bad-space", "GENERATION_SPACE_MISMATCH"),
        ):
            response = await client.post(
                "/api/v1/search-profile/activate",
                json={"profile_id": profile_id},
                headers=SearchEnv.post_headers(csrf),
            )
            body = response.json()
            outcomes[profile_id] = {
                "status": response.status_code,
                "code": body.get("code"),
                "reason": (body.get("details") or {}).get("reason"),
                "expected": expected,
                "path_free": str(search_env.library.root) not in response.text,
            }
        pointer = await client.get("/api/v1/search-profile")
        return {
            "outcomes": outcomes,
            "pointer": pointer.json(),
            "generations": search_env.generation_state(),
            "before_generations": before_generations,
        }

    result = search_env.scenario(scenario)
    for profile_id, outcome in result["outcomes"].items():
        assert outcome["status"] == 409, (profile_id, outcome)
        assert outcome["code"] == RUNTIME_CAPABILITY_UNAVAILABLE or (
            outcome["code"] == PROFILE_GENERATION_MISMATCH
        ), (profile_id, outcome)
        assert outcome["reason"] == outcome["expected"], (profile_id, outcome)
        assert outcome["path_free"] is True
    # Nothing was activated: the pointer and every ``is_active`` flag are untouched.
    assert result["pointer"]["profile_id"] is None
    assert result["generations"] == result["before_generations"]


def test_an_interrupted_activation_rolls_back_both_writes(search_env: SearchEnv) -> None:
    search_env.create_profile("profile-1")
    activate_profile(search_env.catalog, profile_id="profile-1")
    second_generation = search_env.create_generation(generation_id="image-gen-second")
    search_env.create_profile("profile-2", generation_id=second_generation)
    before = search_env.generation_state()

    def explode(connection: Any) -> None:
        raise RuntimeError("injected activation failure")

    with pytest.raises(RuntimeError):
        activate_profile(
            search_env.catalog, profile_id="profile-2", activation_hook=explode
        )

    assert search_env.pointer() == "profile-1"
    assert search_env.generation_state() == before
    assert search_env.generation_state()[search_env.generation] is True
    assert search_env.generation_state()[second_generation] is False

    async def scenario(client: Any) -> Any:
        csrf = await search_env.authenticate(client)
        return await post_search(client, {"request_id": "req-after", "text": "sunset"}, csrf)

    # The old profile is still the served one, so a search keeps working after the failure.
    response = search_env.scenario(scenario)
    assert response.status_code == 200
    assert response.json()["profile_id"] == "profile-1"
