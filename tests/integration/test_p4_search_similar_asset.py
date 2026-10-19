"""P4: asset-backed similar-photo search reuses a confirmed vector artifact."""

from __future__ import annotations

from typing import Any

import pytest
from p2_search_api_fixtures import SearchEnv, build_search_env

from photo_search.artifacts.store import ArtifactStore
from photo_search.indexing.store import LanceVectorIndex
from photo_search.search.profile import activate_profile


@pytest.fixture()
def asset_search_env(tmp_path):
    env = build_search_env(tmp_path)
    profile_id = env.create_profile("similar-profile")
    activate_profile(env.catalog, profile_id=profile_id)
    # The shared P2 fixture uses synthetic `rev-{asset_id}` inputs. The P4 card requires
    # the real current asset digest, so align only this disposable fixture's metadata.
    with env.catalog.write() as connection:
        connection.execute(
            "UPDATE artifacts SET input_revision=(SELECT sha256 FROM assets a "
            "JOIN index_entries e ON e.asset_id=a.asset_id WHERE e.artifact_id=artifacts.artifact_id) "
            "WHERE artifact_id IN (SELECT artifact_id FROM index_entries WHERE state='CONFIRMED')"
        )
    try:
        yield env
    finally:
        env.context.close()
        env.library.close()


def test_similar_search_excludes_query_and_paginates_without_reloading_or_reranking(
    asset_search_env: SearchEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads: list[str] = []
    vector_calls: list[int] = []
    original_read = ArtifactStore.read
    original_search = LanceVectorIndex.search_candidates_bounded

    def counted_read(store: ArtifactStore, row: Any) -> bytes:
        reads.append(str(row["artifact_id"]))
        return original_read(store, row)

    def counted_search(index: LanceVectorIndex, **kwargs: Any):
        vector_calls.append(1)
        return original_search(index, **kwargs)

    monkeypatch.setattr(ArtifactStore, "read", counted_read)
    monkeypatch.setattr(LanceVectorIndex, "search_candidates_bounded", counted_search)
    reference = asset_search_env.assets["beach sunset.jpg"]
    expected = asset_search_env.assets["IMG_0001.JPG"]
    remaining = asset_search_env.assets["杭州西湖 照片.jpg"]

    async def scenario(client: Any) -> tuple[Any, Any]:
        csrf = await asset_search_env.authenticate(client)
        first = await client.post("/api/v1/search/by-asset", headers=asset_search_env.post_headers(csrf),
            json={"request_id": "similar-first", "asset_id": reference, "top_k": 2, "page_size": 1})
        first_body = first.json()
        second = await client.post("/api/v1/search/by-asset", headers=asset_search_env.post_headers(csrf),
            json={"request_id": "similar-next", "asset_id": reference, "top_k": 2, "page_size": 1,
                  "search_session_id": first_body["search_session_id"], "cursor": first_body["next_cursor"]})
        return first, second

    first, second = asset_search_env.scenario(scenario)
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert [hit["asset_id"] for hit in first_body["hits"]] == [expected]
    assert first_body["asset_query"]["asset_id"] == reference
    assert first_body["asset_query"]["filename"] == "beach sunset.jpg"
    assert "validation_cursor" not in first_body["asset_query"]
    assert reference not in [hit["asset_id"] for hit in first_body["hits"]]
    assert len(reads) == 1
    assert vector_calls

    reads.clear()
    vector_calls.clear()
    assert second.status_code == 200, second.text
    assert [hit["asset_id"] for hit in second.json()["hits"]] == [remaining]
    assert reference not in [hit["asset_id"] for hit in second.json()["hits"]]
    assert reads == []
    assert vector_calls == []


def test_empty_similar_search_has_read_only_session_validation(
    asset_search_env: SearchEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference = asset_search_env.assets["beach sunset.jpg"]
    with asset_search_env.catalog.write() as connection:
        connection.execute(
            "UPDATE index_entries SET state='STALE',confirmed_at=NULL WHERE asset_id<>?",
            (reference,),
        )
    reads: list[str] = []
    vector_calls: list[int] = []
    original_read = ArtifactStore.read
    original_search = LanceVectorIndex.search_candidates_bounded

    def counted_read(store: ArtifactStore, row: Any) -> bytes:
        reads.append(str(row["artifact_id"]))
        return original_read(store, row)

    def counted_search(index: LanceVectorIndex, **kwargs: Any):
        vector_calls.append(1)
        return original_search(index, **kwargs)

    monkeypatch.setattr(ArtifactStore, "read", counted_read)
    monkeypatch.setattr(LanceVectorIndex, "search_candidates_bounded", counted_search)

    async def scenario(client: Any) -> tuple[Any, Any]:
        csrf = await asset_search_env.authenticate(client)
        headers = asset_search_env.post_headers(csrf)
        first = await client.post("/api/v1/search/by-asset", headers=headers,
            json={"request_id": "similar-empty", "asset_id": reference, "top_k": 5, "page_size": 2})
        reads.clear()
        vector_calls.clear()
        validation = await client.post("/api/v1/search/by-asset/validate", headers=headers,
            json={"request_id": "similar-empty-validate", "asset_id": reference,
                  "top_k": 5, "page_size": 2,
                  "search_session_id": first.json()["search_session_id"]})
        return first, validation

    first, validation = asset_search_env.scenario(scenario)
    assert first.status_code == 200, first.text
    assert first.json()["hits"] == []
    assert first.json()["total_candidates"] == 0
    assert first.json()["has_more"] is False
    assert validation.status_code == 200, validation.text
    assert validation.json() == {
        "valid": True, "asset_id": reference,
        "search_session_id": first.json()["search_session_id"],
    }
    assert reads == []
    assert vector_calls == []


@pytest.mark.parametrize("mutation", ["expired", "asset-version"])
def test_asset_session_validation_rejects_expiry_and_reference_version_change(
    asset_search_env: SearchEnv, mutation: str
) -> None:
    reference = asset_search_env.assets["beach sunset.jpg"]

    async def scenario(client: Any) -> tuple[Any, Any]:
        csrf = await asset_search_env.authenticate(client)
        headers = asset_search_env.post_headers(csrf)
        first = await client.post("/api/v1/search/by-asset", headers=headers,
            json={"request_id": f"similar-validate-{mutation}", "asset_id": reference,
                  "top_k": 5, "page_size": 2})
        if mutation == "expired":
            asset_search_env.context.search_sessions.clear()
        else:
            with asset_search_env.catalog.write() as connection:
                changed_sha = "f" * 64
                connection.execute("UPDATE assets SET sha256=? WHERE asset_id=?", (changed_sha, reference))
                connection.execute(
                    "UPDATE artifacts SET input_revision=? WHERE artifact_id IN "
                    "(SELECT artifact_id FROM index_entries WHERE asset_id=?)",
                    (changed_sha, reference),
                )
        validation = await client.post("/api/v1/search/by-asset/validate", headers=headers,
            json={"request_id": f"similar-validate-only-{mutation}", "asset_id": reference,
                  "top_k": 5, "page_size": 2,
                  "search_session_id": first.json()["search_session_id"]})
        return first, validation

    first, validation = asset_search_env.scenario(scenario)
    assert first.status_code == 200, first.text
    assert validation.status_code in {409, 422}, validation.text
    if mutation == "expired":
        assert validation.json()["code"] == "SEARCH_SESSION_EXPIRED"
    else:
        assert validation.json()["code"] in {"INVALID_CURSOR", "PROFILE_GENERATION_MISMATCH"}


def test_similar_search_refuses_when_reference_has_no_current_confirmed_vector(
    asset_search_env: SearchEnv,
) -> None:
    reference = asset_search_env.assets["beach sunset.jpg"]
    with asset_search_env.catalog.write() as connection:
        connection.execute("UPDATE index_entries SET state='STALE',confirmed_at=NULL WHERE asset_id=?", (reference,))

    async def scenario(client: Any) -> Any:
        csrf = await asset_search_env.authenticate(client)
        return await client.post("/api/v1/search/by-asset", headers=asset_search_env.post_headers(csrf),
            json={"request_id": "similar-missing", "asset_id": reference})

    response = asset_search_env.scenario(scenario)
    assert response.status_code == 409
    assert response.json()["code"] == "ASSET_QUERY_VECTOR_UNAVAILABLE"
    assert response.json()["details"]["reason"] == "VECTOR_UNAVAILABLE"


def test_similar_search_reports_a_corrupt_published_vector_without_encoding(
    asset_search_env: SearchEnv,
) -> None:
    reference = asset_search_env.assets["beach sunset.jpg"]
    row = asset_search_env.catalog.read_one(
        "SELECT ar.storage_uri FROM index_entries e JOIN artifacts ar USING(artifact_id) "
        "WHERE e.asset_id=? AND e.state='CONFIRMED'", (reference,)
    )
    artifact_path = ArtifactStore.open(asset_search_env.data_root).resolve_uri(row["storage_uri"])
    artifact_path.write_bytes(b"corrupt-vector")

    async def scenario(client: Any) -> Any:
        csrf = await asset_search_env.authenticate(client)
        return await client.post("/api/v1/search/by-asset", headers=asset_search_env.post_headers(csrf),
            json={"request_id": "similar-corrupt", "asset_id": reference})

    response = asset_search_env.scenario(scenario)
    assert response.status_code == 409
    assert response.json()["code"] == "ASSET_QUERY_VECTOR_INVALID"
    assert response.json()["details"]["reason"] == "ARTIFACT_CORRUPT"
    assert asset_search_env.encoder.calls == []


def test_similar_search_rejects_asset_ids_outside_sqlite_integer_range(
    asset_search_env: SearchEnv,
) -> None:
    async def scenario(client: Any) -> Any:
        csrf = await asset_search_env.authenticate(client)
        return await client.post("/api/v1/search/by-asset", headers=asset_search_env.post_headers(csrf),
            json={"request_id": "similar-too-large", "asset_id": 2**63})

    response = asset_search_env.scenario(scenario)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
