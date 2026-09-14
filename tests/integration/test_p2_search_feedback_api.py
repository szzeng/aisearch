"""P2-06B: ``POST/GET /api/v1/search/feedback`` over a real scanned temp library.

The verdicts are recorded truthfully (readable, idempotent, append-only) and change nothing
that decides what a search returns: the authoritative revisions, the derived text index and
the vectors are compared row by row before and after a verdict, and a "not relevant" photo
is still returned by the very next query.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from p2_search_api_fixtures import SESSION_TOKEN, SearchEnv, build_search_env

from photo_search.catalog.errors import INVALID_CURSOR
from photo_search.search.tokens import VALIDATION_ERROR


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


def feedback_body(session_id: str, asset_id: int, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "contract_version": "p2-contracts.v1",
        "request_id": "fb-1",
        "search_session_id": str(session_id),
        "asset_id": int(asset_id),
        "rank": 1,
        "relevance": "RELEVANT",
    }
    payload.update(overrides)
    return payload


async def post_search(client: Any, payload: dict[str, Any], csrf: str) -> Any:
    return await client.post("/api/v1/search", json=payload, headers=SearchEnv.post_headers(csrf))


async def post_feedback(client: Any, payload: dict[str, Any], csrf: str) -> Any:
    return await client.post(
        "/api/v1/search/feedback", json=payload, headers=SearchEnv.post_headers(csrf)
    )


def authority_snapshot(catalog: Any) -> dict[str, Any]:
    """Everything that decides membership: revisions, text projection, vectors and rows."""
    return {
        "catalog_filter_revision": int(
            catalog.read_one("SELECT revision FROM catalog_filter_revision WHERE revision_id = 1")[
                "revision"
            ]
        ),
        "suggestion_revision": int(
            catalog.read_one("SELECT revision FROM place_suggestion_revision")["revision"]
        ),
        "documents": [
            tuple(row)
            for row in catalog.read_all(
                "SELECT asset_id, filename_text, place_text, tokenizer_version, "
                "fts_generation_id FROM search_documents ORDER BY asset_id"
            )
        ],
        "created_at": [
            tuple(row) for row in catalog.read_all("SELECT asset_id, created_at FROM assets ORDER BY asset_id")
        ],
        "index_entries": int(
            catalog.read_one("SELECT COUNT(*) AS value FROM index_entries")["value"]
        ),
        "index_outbox": int(
            catalog.read_one("SELECT COUNT(*) AS value FROM index_outbox")["value"]
        ),
        "feedback_rows": int(
            catalog.read_one("SELECT COUNT(*) AS value FROM search_feedback")["value"]
        ),
    }


def test_a_verdict_is_idempotent_auditable_and_changes_no_authority(
    search_env: SearchEnv,
) -> None:
    async def scenario(client: Any) -> dict[str, Any]:
        anonymous = await client.post(
            "/api/v1/search", json=search_body(search_env, request_id="req-anon")
        )
        anonymous_feedback = await client.post(
            "/api/v1/search/feedback",
            json=feedback_body("unknown-session", 1, request_id="fb-anon"),
        )
        csrf = await search_env.authenticate(client)
        page = (await post_search(client, search_body(search_env, page_size=10), csrf)).json()
        hit_ids = [row["asset_id"] for row in page["hits"]]
        session_id = page["search_session_id"]
        before = authority_snapshot(search_env.catalog)
        first = await post_feedback(
            client, feedback_body(session_id, hit_ids[0], note="婚礼照片"), csrf
        )
        replay = await post_feedback(
            client, feedback_body(session_id, hit_ids[0], note="婚礼照片"), csrf
        )
        conflict = await post_feedback(
            client,
            feedback_body(session_id, hit_ids[0], relevance="NOT_RELEVANT", note="婚礼照片"),
            csrf,
        )
        second = await post_feedback(
            client,
            feedback_body(
                session_id, hit_ids[1], request_id="fb-2", rank=2, relevance="NOT_RELEVANT"
            ),
            csrf,
        )
        after = authority_snapshot(search_env.catalog)
        again = (await post_search(client, search_body(search_env, page_size=10), csrf)).json()
        stored = [
            tuple(row)
            for row in search_env.catalog.read_all(
                "SELECT request_id, relevance, note FROM search_feedback ORDER BY feedback_id"
            )
        ]
        listed = await client.get("/api/v1/search/feedback", params={"limit": 1})
        listed_body = listed.json()
        walked = await client.get(
            "/api/v1/search/feedback",
            params={"limit": 1, "cursor": listed_body["next_cursor"]},
        )
        broken = await client.get("/api/v1/search/feedback", params={"cursor": "not-a-cursor"})
        return {
            "anonymous": anonymous.status_code,
            "anonymous_feedback": anonymous_feedback.status_code,
            "hit_ids": hit_ids,
            "page_hits": page["hits"],
            "before": before,
            "after": after,
            "first": first,
            "replay": replay,
            "conflict": conflict,
            "second": second,
            "again": again,
            "stored": stored,
            "listed": listed,
            "walked": walked,
            "broken": broken,
            "paths": (str(search_env.data_root), str(search_env.library.root)),
        }

    result = search_env.scenario(scenario)
    assert result["anonymous"] == 401
    assert result["anonymous_feedback"] == 401

    first = result["first"]
    assert first.status_code == 201, first.text
    assert first.json()["replayed"] is False
    assert first.json()["feedback"]["feedback_id"] == 1
    assert first.json()["feedback"]["note"] == "婚礼照片"

    replay = result["replay"]
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["feedback"] == first.json()["feedback"]

    conflict = result["conflict"]
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "FEEDBACK_IDEMPOTENCY_CONFLICT"
    assert conflict.json()["details"]["different_fields"] == ["relevance"]
    assert result["second"].status_code == 201

    # The recorded verdicts are exactly the two the caller sent; the conflict wrote nothing.
    assert result["stored"] == [
        ("fb-1", "RELEVANT", "婚礼照片"),
        ("fb-2", "NOT_RELEVANT", None),
    ]
    # Authority is untouched, and the NOT_RELEVANT photo is still returned by the next query.
    assert result["after"] == {**result["before"], "feedback_rows": 2}
    assert [row["asset_id"] for row in result["again"]["hits"]] == result["hit_ids"]
    assert result["again"]["hits"] == result["page_hits"]

    listed = result["listed"]
    assert listed.status_code == 200
    assert listed.json()["limit"] == 1 and listed.json()["has_more"] is True
    assert len(listed.json()["feedback"]) == 1
    walked = result["walked"]
    assert walked.status_code == 200 and walked.json()["has_more"] is False
    assert walked.json()["feedback"][0]["request_id"] == "fb-2"
    assert result["broken"].status_code == 422
    assert result["broken"].json()["code"] == INVALID_CURSOR

    for body in (first, replay, listed, walked):
        for needle in result["paths"]:
            assert needle not in body.text
        assert "latitude" not in body.text and "longitude" not in body.text


def test_every_feedback_refusal_is_structured_and_writes_nothing(
    search_env: SearchEnv,
) -> None:
    async def scenario(client: Any) -> dict[str, Any]:
        csrf = await search_env.authenticate(client)
        page = (await post_search(client, search_body(search_env, page_size=10), csrf)).json()
        asset_id = page["hits"][0]["asset_id"]
        session_id = page["search_session_id"]
        unknown_asset = await post_feedback(
            client, feedback_body(session_id, 999_999), csrf
        )
        bad_rank = await post_feedback(client, feedback_body(session_id, asset_id, rank=0), csrf)
        bad_verdict = await post_feedback(
            client, feedback_body(session_id, asset_id, relevance="MAYBE"), csrf
        )
        long_note = await post_feedback(
            client, feedback_body(session_id, asset_id, note="x" * 501), csrf
        )
        incomplete = await client.post(
            "/api/v1/search/feedback",
            json={"request_id": "fb-3", "relevance": "RELEVANT"},
            headers=SearchEnv.post_headers(csrf),
        )
        missing_csrf = await client.post(
            "/api/v1/search/feedback",
            json=feedback_body(session_id, asset_id),
            headers={"Origin": "http://127.0.0.1"},
        )
        wrong_origin = await client.post(
            "/api/v1/search/feedback",
            json=feedback_body(session_id, asset_id),
            headers={"X-CSRF-Token": csrf, "Origin": "http://evil.example"},
        )
        return {
            "unknown_asset": unknown_asset,
            "bad_rank": bad_rank,
            "bad_verdict": bad_verdict,
            "long_note": long_note,
            "incomplete": incomplete,
            "missing_csrf": missing_csrf,
            "wrong_origin": wrong_origin,
            "rows": int(
                search_env.catalog.read_one("SELECT COUNT(*) AS value FROM search_feedback")[
                    "value"
                ]
            ),
        }

    result = search_env.scenario(scenario)
    unknown = result["unknown_asset"]
    assert unknown.status_code == 422
    assert unknown.json()["code"] == VALIDATION_ERROR
    assert unknown.json()["details"]["field"] == "asset_id"
    for key in ("bad_rank", "bad_verdict", "long_note", "incomplete"):
        assert result[key].status_code == 422, key
        assert result[key].json()["code"] == VALIDATION_ERROR
    # A missing CSRF token and a foreign Origin both stop before the service layer.
    assert result["missing_csrf"].status_code == 403
    assert result["missing_csrf"].json()["code"] == "CSRF_INVALID"
    assert result["wrong_origin"].status_code == 403
    assert result["rows"] == 0


def test_the_feedback_log_is_append_only_for_any_writer(search_env: SearchEnv) -> None:
    async def scenario(client: Any) -> None:
        csrf = await search_env.authenticate(client)
        page = (await post_search(client, search_body(search_env, page_size=10), csrf)).json()
        await post_feedback(
            client, feedback_body(page["search_session_id"], page["hits"][0]["asset_id"]), csrf
        )

    search_env.scenario(scenario)
    catalog = search_env.catalog
    before = [
        tuple(row)
        for row in catalog.read_all(
            "SELECT feedback_id, request_id, relevance, note, created_at FROM search_feedback"
        )
    ]
    with catalog.write() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE search_feedback SET relevance = 'NOT_RELEVANT' WHERE feedback_id = 1"
            )
    with catalog.write() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM search_feedback WHERE feedback_id = 1")
    assert [
        tuple(row)
        for row in catalog.read_all(
            "SELECT feedback_id, request_id, relevance, note, created_at FROM search_feedback"
        )
    ] == before
    assert search_env.session_token == SESSION_TOKEN
