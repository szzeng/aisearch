"""P2-06B: the rerank block of ``POST /api/v1/search`` and the reranker activation gate.

The base page comes from the P2-05 retrieval; the rerank is an optional block on top of it.
Every case below compares the base ``hits`` with and without the rerank request, so "the
optional model failed" can never be mistaken for "the search changed", and the candidate
scores are only ever reported in their own field.
"""

from __future__ import annotations

from typing import Any

import pytest
from p2_search_api_fixtures import SearchEnv, build_search_env, record_reranker_evidence
from p2_search_fixtures import FakeReranker, reranker_spec

from photo_search.catalog.errors import RUNTIME_CAPABILITY_UNAVAILABLE
from photo_search.search.profile import activate_profile
from photo_search.search.rerank import (
    REASON_ADAPTER_UNAVAILABLE,
    REASON_FAILED,
    REASON_NO_PROFILE_RERANKER,
    REASON_NO_QUERY_TEXT,
    REASON_TIMEOUT,
    RERANK_STATE_FAILED,
    RERANK_STATE_UNAVAILABLE,
)

FULL_ORDER_SCORES = {"sunset": 0.1, "lake": 0.9, "other": 0.5}


@pytest.fixture()
def rerank_env(tmp_path: Any):
    spec = reranker_spec()
    adapter = FakeReranker(spec)
    env = build_search_env(tmp_path, reranker=adapter)
    try:
        yield env, spec, adapter
    finally:
        env.context.close()
        env.library.close()


def search_body(env: SearchEnv, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": "req-1",
        **env.explicit_binding(),
        "text": "sunset",
        "page_size": 10,
    }
    payload.update(overrides)
    return payload


async def post_search(client: Any, payload: dict[str, Any], csrf: str) -> Any:
    return await client.post("/api/v1/search", json=payload, headers=SearchEnv.post_headers(csrf))


async def activate(client: Any, profile_id: str, csrf: str) -> Any:
    return await client.post(
        "/api/v1/search-profile/activate",
        json={"profile_id": profile_id},
        headers=SearchEnv.post_headers(csrf),
    )


def test_a_requested_rerank_is_a_separate_block_that_never_touches_scores(
    rerank_env: Any,
) -> None:
    env, spec, adapter = rerank_env
    sunset = env.assets["beach sunset.jpg"]
    lake = env.assets["杭州西湖 照片.jpg"]
    other = env.assets["IMG_0001.JPG"]
    adapter.set_scores({sunset: 0.1, lake: 0.9, other: 0.5})

    async def scenario(client: Any) -> dict[str, Any]:
        csrf = await env.authenticate(client)
        unknown_profile = await activate(client, "profile-rerank", csrf)
        env.create_profile("profile-rerank", reranker_processor_id=str(spec.processor_id))
        generations_before = env.generation_state()
        without_evidence = await activate(client, "profile-rerank", csrf)
        pointer_without_evidence = env.pointer()
        generations_without_evidence = env.generation_state()
        record_reranker_evidence(env, spec)
        activated = await activate(client, "profile-rerank", csrf)
        base = (await post_search(client, search_body(env, request_id="req-base"), csrf)).json()
        reranked = (
            await post_search(client, search_body(env, request_id="req-rerank", rerank=True), csrf)
        ).json()
        repeated = (
            await post_search(client, search_body(env, request_id="req-rerank", rerank=True), csrf)
        ).json()
        first_page = (
            await post_search(
                client,
                search_body(env, request_id="req-paged", rerank=True, page_size=1),
                csrf,
            )
        ).json()
        continuation = (
            await post_search(
                client,
                search_body(
                    env,
                    request_id="req-paged",
                    rerank=True,
                    page_size=1,
                    search_session_id=first_page["search_session_id"],
                    cursor=first_page["next_cursor"],
                ),
                csrf,
            )
        ).json()
        filtered = (
            await post_search(
                client,
                search_body(env, request_id="req-filter", rerank=True, text=None),
                csrf,
            )
        ).json()
        return {
            "unknown_profile": unknown_profile,
            "without_evidence": without_evidence,
            "pointer_without_evidence": pointer_without_evidence,
            "generations_before": generations_before,
            "generations_without_evidence": generations_without_evidence,
            "activated": activated,
            "base": base,
            "reranked": reranked,
            "repeated": repeated,
            "first_page": first_page,
            "continuation": continuation,
            "filtered": filtered,
            "paths": (str(env.data_root), str(env.library.root)),
        }

    result = env.scenario(scenario)
    assert result["unknown_profile"].status_code == 409
    assert result["unknown_profile"].json()["details"]["reason"] == "PROFILE_NOT_FOUND"

    # ADR-0003 §5 applied to the reranker: no evidence, no activation, and zero writes.
    without = result["without_evidence"]
    assert without.status_code == 409, without.text
    assert without.json()["code"] == RUNTIME_CAPABILITY_UNAVAILABLE
    assert without.json()["details"]["reason"] == "RERANKER_QUALIFICATION_MISSING"
    assert result["pointer_without_evidence"] is None
    # Zero writes: the generation's active flag is untouched by the refused activation.
    assert result["generations_without_evidence"] == result["generations_before"]

    activated = result["activated"]
    assert activated.status_code == 200, activated.text
    assert activated.json()["changed"] is True
    assert activated.json()["readiness"]["reranker"]["processor_id"] == str(spec.processor_id)

    base, reranked = result["base"], result["reranked"]
    assert base["rerank_state"] == "NOT_REQUESTED" and base["reranked"] is None
    assert reranked["rerank_state"] == "OK", reranked
    block = reranked["reranked"]
    assert block["state"] == "OK" and block["processor_id"] == str(spec.processor_id)
    assert block["timeout_ms"] > 0 and block["candidates"] == len(reranked["hits"])
    assert block["error"] is None

    # The base page is exactly the page a caller gets without asking for a rerank.
    assert reranked["hits"] == base["hits"]
    assert reranked["channels"] == base["channels"]
    assert reranked["total_candidates"] == base["total_candidates"]
    # ... while the reranked order is the model's order, not the fused order.
    assert [row["asset_id"] for row in block["rows"]] == [lake, other, sunset]
    # ``rank`` is the *fused* rank of that candidate, so a row whose score is highest may
    # carry the lowest rank: the two orderings stay distinguishable on purpose.
    assert [row["rank"] for row in block["rows"]] == [3, 2, 1]
    assert [row["reranker_score"] for row in block["rows"]] == [0.9, 0.5, 0.1]
    for row in block["rows"]:
        assert set(row) == {"asset_id", "rank", "reranker_score", "reranker_processor_id"}
    for row in reranked["hits"]:
        assert set(row) == {
            "asset_id",
            "rank",
            "score",
            "matched_by",
            "hit_reason",
            "matched_text",
            "semantic_ready",
        }
    assert [row["asset_id"] for row in reranked["hits"]] != [
        row["asset_id"] for row in block["rows"]
    ]
    # Deterministic: the same request twice yields the same candidate scores and order.
    assert result["repeated"]["reranked"]["rows"] == block["rows"]

    # A continuation page echoes the session's frozen block instead of reranking again.
    first_page, continuation = result["first_page"], result["continuation"]
    assert continuation["search_session_id"] == first_page["search_session_id"]
    assert continuation["reranked"] == first_page["reranked"]
    assert continuation["page_offset"] == 1

    # A pure filter query has no query text to score: the authoritative list still answers.
    filtered = result["filtered"]
    assert filtered["rerank_state"] == RERANK_STATE_UNAVAILABLE
    assert filtered["reranked"]["reason"] == REASON_NO_QUERY_TEXT
    assert filtered["reranked"]["rows"] == []
    assert [row["hit_reason"] for row in filtered["hits"]] == ["FILTER_ONLY"] * len(
        filtered["hits"]
    )

    for needle in result["paths"]:
        assert needle not in str(reranked)


def build_case_env(
    tmp_path: Any, *, with_adapter: bool = True, failure: BaseException | None = None,
    delay_ms: int = 0,
) -> tuple[SearchEnv, Any]:
    spec = reranker_spec()
    adapter = FakeReranker(spec, failure=failure, delay_ms=delay_ms) if with_adapter else None
    return build_search_env(tmp_path, reranker=adapter), spec


@pytest.mark.parametrize(
    ("case", "kwargs", "expected_state", "expected_reason"),
    [
        (
            "missing-adapter",
            {"with_adapter": False},
            RERANK_STATE_UNAVAILABLE,
            REASON_ADAPTER_UNAVAILABLE,
        ),
        ("broken-adapter", {"failure": RuntimeError("boom")}, RERANK_STATE_FAILED, REASON_FAILED),
        (
            "slow-adapter",
            {"delay_ms": 2_500},
            RERANK_STATE_FAILED,
            REASON_TIMEOUT,
        ),
    ],
)
def test_a_rerank_that_cannot_run_leaves_the_base_page_untouched(
    tmp_path: Any, case: str, kwargs: dict[str, Any], expected_state: str, expected_reason: str
) -> None:
    env, spec = build_case_env(tmp_path, **kwargs)
    try:
        async def scenario(client: Any) -> dict[str, Any]:
            csrf = await env.authenticate(client)
            env.create_profile("profile-rerank", reranker_processor_id=str(spec.processor_id))
            record_reranker_evidence(env, spec)
            await activate(client, "profile-rerank", csrf)
            base = (await post_search(client, search_body(env, request_id="req-b"), csrf)).json()
            attempted = (
                await post_search(client, search_body(env, request_id="req-r", rerank=True), csrf)
            ).json()
            return {"base": base, "attempted": attempted}

        result = env.scenario(scenario)
        attempted = result["attempted"]
        assert attempted["rerank_state"] == expected_state, case
        assert attempted["reranked"]["reason"] == expected_reason, case
        assert attempted["reranked"]["rows"] == [], case
        # D-5: the base page is identical, field by field, whether or not the model ran.
        assert attempted["hits"] == result["base"]["hits"], case
        assert attempted["channels"] == result["base"]["channels"], case
        assert attempted["total_candidates"] == result["base"]["total_candidates"], case
        assert attempted["state"] == result["base"]["state"], case
    finally:
        env.context.close()
        env.library.close()


def test_a_rerank_without_a_profile_reranker_is_unavailable_not_silent(
    rerank_env: Any,
) -> None:
    env, _spec, adapter = rerank_env

    async def scenario(client: Any) -> dict[str, Any]:
        csrf = await env.authenticate(client)
        # No profile is activated: only the explicit P2-05 binding exists, and no reranker is
        # declared anywhere, so the answer says exactly that.
        bound = (
            await post_search(client, search_body(env, request_id="req-bound", rerank=True), csrf)
        ).json()
        env.create_profile("profile-plain")
        await activate(client, "profile-plain", csrf)
        profiled = (
            await post_search(
                client,
                {
                    "request_id": "req-plain",
                    "text": "sunset",
                    "rerank": True,
                    "page_size": 10,
                },
                csrf,
            )
        ).json()
        return {"bound": bound, "profiled": profiled}

    result = env.scenario(scenario)
    for key in ("bound", "profiled"):
        body = result[key]
        assert body["rerank_state"] == RERANK_STATE_UNAVAILABLE, key
        assert body["reranked"]["reason"] == REASON_NO_PROFILE_RERANKER, key
        assert body["reranked"]["rows"] == [], key
    # The adapter was wired but never asked to score anything: no profile declared it.
    assert adapter.calls == []
    # The base results are still a full answer.
    assert result["profiled"]["hits"] != []


def test_an_interrupted_reranker_activation_rolls_back_both_writes(
    rerank_env: Any,
) -> None:
    env, spec, _adapter = rerank_env
    env.create_profile("profile-rerank", reranker_processor_id=str(spec.processor_id))
    record_reranker_evidence(env, spec)

    def interrupt(_: Any) -> None:
        raise RuntimeError("interrupted before commit")

    generations_before = env.generation_state()
    with pytest.raises(RuntimeError):
        activate_profile(
            env.catalog,
            profile_id="profile-rerank",
            now_ms=env.catalog.now_ms(),
            activation_hook=interrupt,
        )
    assert env.pointer() is None
    # Both halves of the single transaction were rolled back together.
    assert env.generation_state() == generations_before

    profile, changed, readiness = activate_profile(
        env.catalog, profile_id="profile-rerank", now_ms=env.catalog.now_ms()
    )
    assert profile.profile_id == "profile-rerank" and changed is True
    assert readiness["reranker"]["processor_id"] == str(spec.processor_id)
    assert env.pointer() == "profile-rerank"
    assert env.generation_state()[env.generation] is True
