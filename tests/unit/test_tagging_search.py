from __future__ import annotations

import hashlib
import random
from types import SimpleNamespace

import pytest
from p2_search_api_fixtures import build_search_env
from p2_search_fixtures import FakeEncoder, configured_scheduler, embedding_spec, index_vector

from photo_search.artifacts.store import ArtifactStore, generation_id_for
from photo_search.catalog.errors import CatalogError
from photo_search.jobs import queue as jobs
from photo_search.models.embedding.base import identity_of, read_manifest
from photo_search.models.embedding.registry import shipped_manifest_dir
from photo_search.scheduler.contracts import ArtifactRef, JobResult, canonical_json
from photo_search.search.sessions import SessionEntry
from photo_search.tagging import search as tags
from photo_search.tagging import store
from photo_search.tagging.model import TagClass, TaggerModel
from photo_search.tagging.policy import POLICY_ID


def test_category_lookup_is_whole_name_and_refuses_cross_language_ambiguity():
    classes = (TagClass(0, "Cat", "猫", .5), TagClass(1, "face", "脸", .5),
               TagClass(2, "flower", "花", .5), TagClass(3, "bloom", "花", .5))
    names = tags._names(classes)
    assert names[tags.normalized(" CAT ")] == 0
    assert names["猫"] == 0
    assert "花" not in names
    for text in ("cat on chair", "猫和狗", "猫的照片", "human face", "不是猫", "花 "):
        assert tags.normalized(text) not in names


def test_reranking_matches_independent_oracle_and_freezes_unprocessed_positions(monkeypatch):
    rng = random.Random(4585)
    monkeypatch.setattr(tags, "require_current", lambda *args: None)
    for size in (1, 2, 7, 33, 301):
        entries = tuple(SessionEntry(i, .1 / (i // 3 + 1), {"vector": i + 1}, "SEMANTIC") for i in range(size))
        covered = {i for i in range(size) if rng.random() > .2}
        scores = {i: rng.choice((0, 0, .25, .5, .75, 1)) for i in covered}
        monkeypatch.setattr(store, "scores_for_assets", lambda *args, **kwargs: (scores, covered))
        query = tags.TagQuery("m:1", "0", "m", 1, "猫", size)
        result, metadata = tags.rerank(SimpleNamespace(connection=None), query, entries)
        positions = sorted(covered)
        expected = list(range(size))
        def score(asset):
            tag_rank = 1 + sum(scores[j] > scores[asset] for j in covered)
            tag_rank += (sum(scores[j] == scores[asset] for j in covered) - 1) / 2
            base_rank = 1 + sum(entries[j].score > entries[asset].score for j in covered)
            base_rank += (sum(entries[j].score == entries[asset].score for j in covered) - 1) / 2
            return 1 / (60 + base_rank) + 1 / (60 + tag_rank)
        order = sorted(positions, key=lambda i: (-score(i), i))
        for target, source in zip(positions, order, strict=True):
            expected[target] = source
        assert [entry.asset_id for entry in result] == expected
        assert metadata["covered_candidates"] == len(covered)
        assert metadata["unprocessed_candidates"] == size - len(covered)
        assert all(result[i] == entries[i] for i in range(size) if i not in covered)
        assert {entry.asset_id for entry in result} == set(range(size))


@pytest.mark.parametrize("scores", [{}, {1: .5, 2: .5}])
def test_no_informative_machine_evidence_is_exact_noop(monkeypatch, scores):
    monkeypatch.setattr(tags, "require_current", lambda *args: None)
    monkeypatch.setattr(store, "scores_for_assets", lambda *args, **kwargs: (scores, {1, 2}))
    entries = tuple(SessionEntry(i, .12, {"vector": i}, "SEMANTIC") for i in (1, 2))
    result, _ = tags.rerank(SimpleNamespace(connection=None), tags.TagQuery("m", "0", "m", 1, "猫", 2), entries)
    assert result == entries


@pytest.fixture
def tagged_env(tmp_path, monkeypatch):
    env = build_search_env(tmp_path)
    identity = identity_of(read_manifest(shipped_manifest_dir() / "qwen3-vl-embedding-2b-exif-v2.json"))
    spec = embedding_spec(space_id=identity.space_id, processor_id=identity.processor_id, dimension=512)
    scheduler = configured_scheduler(env.catalog, [spec])
    for index, asset in enumerate(env.assets.values()):
        vector = ([1., 0.], [0., 1.], [.6, .8])[index] + [0.] * 510
        index_vector(env.catalog, scheduler, spec, asset_id=asset, vector=vector,
                     artifact_id=f"ram-baseline-{index}")
    profile = env.create_profile("ram-baseline", generation_id=generation_id_for(identity.space_id, spec.processor_id),
                                 space_id=identity.space_id, processor_id=spec.processor_id)
    with env.catalog.write() as db:
        db.execute("UPDATE active_search_profile SET profile_id=? WHERE state_id=1", (profile,))
    env.context.query_encoder = lambda: FakeEncoder(spec, vectors={"猫": [1., 0.] + [0.] * 510})
    model = TaggerModel("ram-test", (TagClass(0, "cat", "猫", .5),
                                    TagClass(1, "flower", "花", .5)),
                        "b" * 64, "c" * 64, 1, env.data_root)
    monkeypatch.setattr(tags, "read_model", lambda root: model)
    processor = store.processor_id_for(model.model_id, POLICY_ID)
    with env.catalog.write() as db:
        store.register_model(db, model_id=model.model_id, policy_id=POLICY_ID,
                             manifest_sha256=model.manifest_sha256, class_count=2,
                             max_scores=2, now_ms=1)
    for index, asset in enumerate(env.assets.values()):
        sha = env.catalog.read_one("SELECT sha256 FROM assets WHERE asset_id=?", (asset,))[0]
        with env.catalog.write() as db:
            job_id = jobs.enqueue(db, subject_type="asset", subject_id=str(asset), input_revision=sha,
                                  task_type=store.TASK_TYPE, processor_id=processor, now_ms=1)
            job = jobs.claim_job(db, job_id=job_id, worker_id="tag-test", now_ms=1, lease_seconds=100)
        payload = {"schema": store.PAYLOAD_SCHEMA, "model_id": model.model_id,
                   "policy_id": POLICY_ID, "input_revision": sha, "scores": [
                       {"tag_id": 0, "score": .75 if index == 2 else 0.0,
                        "above_threshold": index == 2}]}
        raw = canonical_json(payload).encode()
        ref = ArtifactRef(store.artifact_id_for(sha, processor), "structured", store.ARTIFACT_REVISION,
                          "application/json", hashlib.sha256(raw).hexdigest(), len(raw), None, payload, None, None)
        artifacts = ArtifactStore.open(env.data_root)
        prepared = artifacts.prepare(ref)
        result = JobResult(job.job_id, job.lease_token, sha, store.TASK_TYPE, processor,
                           "SUCCEEDED", "cpu", "test", "fp16", 1.0, (ref,), None, 2)
        try:
            with env.catalog.write() as db:
                artifacts.persist_result(db, job=job, result=result,
                                         spec=SimpleNamespace(processor_id=processor),
                                         prepared=[prepared], now_ms=2)
                store.persist_tag_result(db, job=job, result=result, prepared=[prepared], now_ms=2)
                jobs.finish(db, job_id=job_id, lease_token=job.lease_token, state="SUCCEEDED",
                            input_revision=sha, processor_id=processor, now_ms=2)
        finally:
            artifacts.discard([prepared])
    try:
        yield env, processor
    finally:
        env.context.close()
        env.library.close()


def test_ram_api_uses_original_encoder_and_freezes_pages_and_static_save(tagged_env):
    env, _ = tagged_env
    async def scenario(client):
        csrf = await env.authenticate(client)
        headers = env.post_headers(csrf)
        body = {"request_id": "ram-query", "original_query": "猫", "page_size": 1}
        baseline = await client.post("/api/v1/search/place-aware", headers=headers, json=body)
        assert baseline.status_code == 200, baseline.text
        assert baseline.json()["category_rank"]["mode"] == tags.VERSION
        response = await client.post("/api/v1/search/place-aware", headers=headers,
                                     json={**body, "category_rank": tags.VERSION})
        assert response.status_code == 200, response.text
        first = response.json()
        assert first["hits"][0]["asset_id"] != baseline.json()["hits"][0]["asset_id"]
        assert first["category_rank"]["covered_candidates"] == 3
        assert first["channels"]["vector"]["strong_match_available"] is False
        continuation = {**body, "category_rank": tags.VERSION, "intent_plan": first["intent_plan"],
                        "search_session_id": first["search_session_id"], "cursor": first["next_cursor"]}
        page = await client.post("/api/v1/search/place-aware", headers=headers, json=continuation)
        assert page.status_code == 200, page.text
        assert page.json()["category_rank"] == first["category_rank"]
        timed = await client.get(f"/api/v1/search/sessions/{first['search_session_id']}/time?limit=1")
        assert timed.status_code == 200, timed.text
        saved = await client.post("/api/v1/photo-collections/from-search", headers=headers, json={
            "name": "RAM static", "create_request_id": "ram-static", "mode": "ranked_candidates",
            "source_token": first["photo_collection_source_token"]})
        assert saved.status_code == 200, saved.text
        dynamic = await client.post("/api/v1/photo-smart-collections/from-search", headers=headers, json={
            "name": "RAM dynamic", "create_request_id": "ram-dynamic",
            "source_token": first["photo_collection_source_token"]})
        assert dynamic.status_code == 422
        assert dynamic.json()["code"] == "PHOTO_SMART_CATEGORY_RANK_UNSUPPORTED"
        off = await client.post("/api/v1/search/place-aware", headers=headers, json={**continuation, "category_rank": "off"})
        assert off.status_code == 422 and off.json()["code"] == "INVALID_CURSOR"
    env.scenario(scenario)


def test_ram_republication_invalidates_time_paging_and_saved_search_token(tagged_env):
    env, processor = tagged_env
    async def scenario(client):
        csrf = await env.authenticate(client)
        headers = env.post_headers(csrf)
        body = {"request_id": "ram-revision", "original_query": "猫", "page_size": 1, "category_rank": tags.VERSION}
        response = await client.post("/api/v1/search/place-aware", headers=headers, json=body)
        assert response.status_code == 200, response.text
        first = response.json()
        with env.catalog.write() as db:
            db.execute("UPDATE machine_tag_models SET revision=revision+1 WHERE processor_id=?", (processor,))
        page = await client.post("/api/v1/search/place-aware", headers=headers, json={
            **body, "intent_plan": first["intent_plan"], "search_session_id": first["search_session_id"],
            "cursor": first["next_cursor"]})
        assert page.status_code == 422 and page.json()["code"] == "INVALID_CURSOR"
        timed = await client.get(f"/api/v1/search/sessions/{first['search_session_id']}/time?limit=1")
        assert timed.status_code == 422 and timed.json()["code"] == "INVALID_CURSOR"
        saved = await client.post("/api/v1/photo-collections/from-search", headers=headers, json={
            "name": "Stale RAM", "create_request_id": "ram-stale", "mode": "ranked_candidates",
            "source_token": first["photo_collection_source_token"]})
        assert saved.status_code == 422, saved.text
    env.scenario(scenario)


@pytest.mark.parametrize("query,mode", [("猫在海边", "combined"), ("猫", "text")])
def test_ram_cannot_reinterpret_complex_or_text_queries(tagged_env, query, mode):
    env, _ = tagged_env
    async def scenario(client):
        csrf = await env.authenticate(client)
        response = await client.post("/api/v1/search/place-aware", headers=env.post_headers(csrf), json={
            "request_id": "ram-refuse", "original_query": query, "retrieval_mode": mode,
            "category_rank": tags.VERSION})
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "CATEGORY_RANK_UNAVAILABLE"
    env.scenario(scenario)


@pytest.mark.parametrize("space,processor", [
    ("qwen3-vl-embedding-2b-exif-v2:invented", "unregistered:processor"),
    ("qwen3-vl-embedding-2b-exif-v2:invented", "official"),
])
def test_same_space_prefix_does_not_authorize_an_unverified_provider(tagged_env, space, processor):
    env, _ = tagged_env
    identity = identity_of(read_manifest(shipped_manifest_dir() / "qwen3-vl-embedding-2b-exif-v2.json"))
    binding = SimpleNamespace(space_id=space, dimension=512,
                              processor_id=identity.processor_id if processor == "official" else processor)
    with pytest.raises(CatalogError) as error:
        tags.resolve_tag_query(env.catalog, env.data_root, binding, "猫")
    assert error.value.code == "CATEGORY_RANK_UNAVAILABLE"


def test_tagging_control_preserves_session_csrf_validation_and_refresh_exclusion(tagged_env, monkeypatch):
    from photo_search.tagging import model

    env, _ = tagged_env
    monkeypatch.setattr(model, "read_model", tags.read_model)
    calls = []
    state = {"task_id": "a" * 32, "status": "RUNNING"}
    def start(request_id, *, exclusive_confirmed, replace_task_id=None):
        if not exclusive_confirmed:
            raise CatalogError("USAGE", "exclusive confirmation required")
        calls.append(("start", request_id, replace_task_id))
        return state.copy()
    def pause(task_id):
        calls.append(("pause", task_id))
        return {**state, "status": "PAUSED"}
    def resume(task_id):
        calls.append(("resume", task_id))
        return state.copy()
    manager = SimpleNamespace(status=lambda: state.copy(), start=start, pause=pause, resume=resume)
    monkeypatch.setattr(env.context, "tag_build_manager", lambda: manager)
    async def scenario(client):
        assert (await client.get("/api/v1/tagging/status")).status_code == 401
        csrf = await env.authenticate(client)
        headers = env.post_headers(csrf)
        status = await client.get("/api/v1/tagging/status")
        assert status.status_code == 200 and status.json()["installed"]
        assert status.json()["coverage"]["completed_count"] == 3
        endpoint = "/api/v1/tagging/build"
        body = {"request_id": "tag-control", "exclusive_model_use_confirmed": True}
        refused = await client.post(endpoint, json=body)
        assert refused.status_code == 403 and calls == []
        for invalid in ([], {"request_id": 12}, {"request_id": "tag-control"}):
            refused = await client.post(endpoint, headers=headers, json=invalid)
            assert refused.status_code == 422, refused.text
        refused = await client.post(endpoint, headers=headers, content="{")
        assert refused.status_code == 422 and calls == []
        started = await client.post(endpoint, headers=headers, json=body)
        assert started.status_code == 200
        path = "/api/v1/tagging/tasks/" + state["task_id"]
        assert (await client.post(path + "/pause", headers=headers)).status_code == 200
        refused = await client.post(path + "/resume", headers=headers, json={})
        assert refused.status_code == 422
        refused = await client.post(path + "/resume", headers=headers, content="{")
        assert refused.status_code == 422
        assert (await client.post(path + "/resume", headers=headers, json=body)).status_code == 200
        refresh = await client.post("/api/v1/library/refresh", headers=headers, json={})
        assert refresh.status_code == 409, refresh.text
        assert refresh.json()["details"]["reason"] == "MODEL_RESOURCE_BUSY"
        assert calls == [("start", "tag-control", None), ("pause", state["task_id"]), ("resume", state["task_id"])]
    env.scenario(scenario)
