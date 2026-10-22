from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from p2_search_api_fixtures import build_search_env
from p2_search_fixtures import FakeEncoder, configured_scheduler, embedding_spec, index_vector

from photo_search.artifacts.store import generation_id_for
from photo_search.catalog.errors import CatalogError
from photo_search.models.embedding.base import identity_of, read_manifest
from photo_search.models.embedding.registry import shipped_manifest_dir
from photo_search.search import category_rank as heads


def install_head(root: Path):
    source = shipped_manifest_dir() / "qwen3-vl-embedding-2b-exif-v2.json"
    identity = identity_of(read_manifest(source))
    directory = root / "models/search_heads/qwen512-category-v1"
    directory.mkdir(parents=True, exist_ok=True)
    weights = {"schema": "photo-search-category-weights-v1", "weights": {
        name: [0.0, 1.0] + [0.0] * 510 for name in heads.CLASSES}}
    raw = json.dumps(weights, ensure_ascii=False).encode()
    (directory / "weights.json").write_bytes(raw)
    manifest = {"schema": "photo-search-category-head-v1", "version": heads.VERSION,
                "processor_id": identity.processor_id, "space_id": identity.space_id,
                "dimension": 512, "source_manifest_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "weights_sha256": hashlib.sha256(raw).hexdigest(), "export_evidence_sha256": "a" * 64,
                "classes": list(heads.CLASSES), "aliases": heads.ALIASES}
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    binding = SimpleNamespace(processor_id=identity.processor_id, space_id=identity.space_id, dimension=512)
    return directory, manifest, weights, binding


def write_manifest(directory, manifest):
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def test_head_is_explicit_normalized_bound_and_never_probability(tmp_path):
    directory, manifest, _, binding = install_head(tmp_path)
    query = heads.resolve_category_query(tmp_path, binding, " 猫 ")
    assert query.vector == (0.0, 1.0, *([0.0] * 510))
    assert query.class_id == "cat"
    assert query.identity == hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest()
    assert query.summary(applied=True)["requested"] == heads.VERSION
    assert heads.availability(tmp_path, binding, "猫")["available"]
    manifest["export_evidence_sha256"] = "b" * 64
    write_manifest(directory, manifest)
    assert heads.resolve_category_query(tmp_path, binding, "猫").identity != query.identity


@pytest.mark.parametrize("query", [None, "", "一只猫", "猫狗", "猫在沙发上", "cat"])
def test_only_frozen_exact_queries_are_supported(tmp_path, query):
    assert heads.availability(tmp_path, None, query)["reason"] == "UNSUPPORTED_QUERY"


@pytest.mark.parametrize("field,value", [
    ("dimension", True), ("dimension", 1024), ("processor_id", "wrong"),
    ("space_id", "wrong"), ("source_manifest_sha256", "b" * 64),
    ("weights_sha256", "b" * 64), ("export_evidence_sha256", "x" * 64),
    ("version", "next"), ("aliases", {"小猫": "cat"}), ("classes", ["cat"]),
])
def test_corrupt_or_mismatched_package_is_not_applied(tmp_path, field, value):
    directory, manifest, _, binding = install_head(tmp_path)
    manifest[field] = value
    write_manifest(directory, manifest)
    with pytest.raises(CatalogError, match="类别优先") as caught:
        heads.resolve_category_query(tmp_path, binding, "猫")
    assert caught.value.code == "CATEGORY_RANK_UNAVAILABLE"


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "1", None])
def test_bad_numeric_weights_fail_even_when_checksum_matches(tmp_path, value):
    directory, manifest, weights, binding = install_head(tmp_path)
    weights["weights"]["cat"][0] = value
    raw = json.dumps(weights).encode()
    (directory / "weights.json").write_bytes(raw)
    manifest["weights_sha256"] = hashlib.sha256(raw).hexdigest()
    write_manifest(directory, manifest)
    assert heads.availability(tmp_path, binding, "猫")["reason"] == "HEAD_INVALID"


@pytest.mark.parametrize("case", ["missing", "oversized", "duplicates", "zero", "short", "extra"])
def test_file_and_schema_guards(tmp_path, case):
    directory, manifest, weights, binding = install_head(tmp_path)
    if case == "missing":
        (directory / "weights.json").unlink()
    elif case == "oversized":
        (directory / "weights.json").write_bytes(b" " * heads.MAX_PACKAGE_BYTES)
    elif case == "duplicates":
        (directory / "manifest.json").write_text('{"schema":1,"schema":2}', encoding="utf-8")
    elif case == "extra":
        manifest["unexpected"] = True
        write_manifest(directory, manifest)
    else:
        weights["weights"]["cat"] = [0.0] * (512 if case == "zero" else 511)
        raw = json.dumps(weights).encode()
        (directory / "weights.json").write_bytes(raw)
        manifest["weights_sha256"] = hashlib.sha256(raw).hexdigest()
        write_manifest(directory, manifest)
    assert not heads.availability(tmp_path, binding, "猫")["available"]


def test_package_link_refused_without_windows_acl_changes(tmp_path, monkeypatch):
    _, _, _, binding = install_head(tmp_path)
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "search_heads" or original(self))
    assert heads.availability(tmp_path, binding, "猫")["reason"] == "HEAD_INVALID"


def test_bounded_parse_cache_never_hides_mutation(tmp_path):
    directory, _, _, binding = install_head(tmp_path)
    heads.resolve_category_query(tmp_path, binding, "猫")
    (directory / "weights.json").write_text("{}", encoding="utf-8")
    assert heads.availability(tmp_path, binding, "猫")["reason"] == "HEAD_INVALID"


@pytest.fixture
def category_env(tmp_path):
    env = build_search_env(tmp_path)
    directory, _, _, binding = install_head(env.data_root)
    spec = embedding_spec(space_id=binding.space_id, processor_id=binding.processor_id, dimension=512)
    scheduler = configured_scheduler(env.catalog, [spec])
    vectors = [[1.0, 0.0], [0.0, 1.0], [0.6, 0.8]]
    for index, (name, aid) in enumerate(env.assets.items()):
        index_vector(env.catalog, scheduler, spec, asset_id=aid,
                     vector=vectors[index] + [0.0] * 510, artifact_id=f"category-{index}")
    generation = generation_id_for(binding.space_id, spec.processor_id)
    profile = env.create_profile("category-profile", generation_id=generation,
                                 space_id=binding.space_id, processor_id=spec.processor_id)
    with env.catalog.write() as connection:
        connection.execute("UPDATE active_search_profile SET profile_id=? WHERE state_id=1", (profile,))
    ordinary = FakeEncoder(spec, vectors={"猫": [1.0, 0.0] + [0.0] * 510})
    env.context.query_encoder = lambda: ordinary
    try:
        yield env, directory
    finally:
        env.context.close()
        env.library.close()


def test_real_lance_category_search_pages_static_save_and_smart_refusal(category_env):
    env, _ = category_env

    async def scenario(client):
        csrf = await env.authenticate(client)
        headers = env.post_headers(csrf)
        body = {"request_id": "category-first", "original_query": "猫", "page_size": 1}
        plain = await client.post("/api/v1/search/place-aware", headers=headers, json=body)
        assert plain.status_code == 200, plain.text
        assert plain.json()["category_rank"]["available"] is True
        ordinary_resolution = env.context.query_encoder
        ordinary_reranker = env.context.reranker

        def forbidden_model():
            raise AssertionError("category search must not resolve a visual model or reranker")

        env.context.query_encoder = forbidden_model
        env.context.reranker = forbidden_model
        enabled = {**body, "category_rank": heads.VERSION}
        response = await client.post("/api/v1/search/place-aware", headers=headers, json=enabled)
        assert response.status_code == 200, response.text
        first = response.json()
        assert first["category_rank"]["applied"] is True
        assert first["hits"][0]["asset_id"] != plain.json()["hits"][0]["asset_id"]
        assert first["channels"]["vector"]["strong_match_available"] is False
        assert first["channels"]["vector"]["engaged"] is True
        assert first["channels"]["fts"]["engaged"] is True
        page_body = {**enabled, "intent_plan": first["intent_plan"],
                     "search_session_id": first["search_session_id"], "cursor": first["next_cursor"]}
        page = await client.post("/api/v1/search/place-aware", headers=headers, json=page_body)
        assert page.status_code == 200, page.text
        assert page.json()["category_rank"] == first["category_rank"]
        assert page.json()["hits"][0]["asset_id"] != first["hits"][0]["asset_id"]
        env.context.query_encoder = ordinary_resolution
        env.context.reranker = ordinary_reranker
        wrong = await client.post("/api/v1/search/place-aware", headers=headers,
                                  json={**page_body, "category_rank": "off"})
        assert wrong.status_code == 422, wrong.text
        assert wrong.json()["code"] == "INVALID_CURSOR"
        smart = await client.post("/api/v1/photo-smart-collections/from-search", headers=headers, json={
            "name": "No dynamic category", "create_request_id": "category-smart",
            "source_token": first["photo_collection_source_token"]})
        assert smart.status_code == 422, smart.text
        assert smart.json()["code"] == "PHOTO_SMART_CATEGORY_RANK_UNSUPPORTED"
        assert env.catalog.read_one("SELECT COUNT(*) FROM photo_smart_collections")[0] == 0
        saved = await client.post("/api/v1/photo-collections/from-search", headers=headers, json={
            "name": "Static category", "create_request_id": "category-save",
            "mode": "ranked_candidates", "source_token": first["photo_collection_source_token"]})
        assert saved.status_code == 200, saved.text
        collection_id = saved.json()["collection"]["collection_id"]
        stored = [r[0] for r in env.catalog.read_all(
            "SELECT asset_id FROM photo_collection_members WHERE collection_id=? ORDER BY position", (collection_id,))]
        session = env.context.search_sessions.get(first["search_session_id"])
        assert stored == [entry.asset_id for entry in session.entries]
        time_page = await client.get(f"/api/v1/search/sessions/{first['search_session_id']}/time?limit=1")
        assert time_page.status_code == 200, time_page.text
        return first

    env.scenario(scenario)


def test_api_package_replacement_invalidates_continuation(category_env):
    env, directory = category_env

    async def scenario(client):
        csrf = await env.authenticate(client)
        headers = env.post_headers(csrf)
        body = {"request_id": "replace-first", "original_query": "猫", "page_size": 1,
                "category_rank": heads.VERSION}
        response = await client.post("/api/v1/search/place-aware", headers=headers, json=body)
        assert response.status_code == 200, response.text
        first = response.json()
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        manifest["export_evidence_sha256"] = "c" * 64
        write_manifest(directory, manifest)
        page = await client.post("/api/v1/search/place-aware", headers=headers, json={
            **body, "intent_plan": first["intent_plan"], "search_session_id": first["search_session_id"],
            "cursor": first["next_cursor"]})
        assert page.status_code == 422, page.text
        assert page.json()["code"] == "INVALID_CURSOR"
        time_page = await client.get(f"/api/v1/search/sessions/{first['search_session_id']}/time?limit=1")
        assert time_page.status_code == 422, time_page.text
        assert time_page.json()["code"] == "INVALID_CURSOR"
        (directory / "weights.json").unlink()
        missing = await client.post("/api/v1/search/place-aware", headers=headers, json=body)
        assert missing.status_code == 422, missing.text
        assert missing.json()["code"] == "CATEGORY_RANK_UNAVAILABLE"

    env.scenario(scenario)


def test_category_prefilters_favorites_before_top_k_and_binds_revision(category_env):
    from photo_search.favorites import set_favorite

    env, _ = category_env
    ids = list(env.assets.values())
    set_favorite(env.catalog, ids[0], favorite=True)

    async def scenario(client):
        csrf = await env.authenticate(client)
        headers = env.post_headers(csrf)
        body = {"request_id": "category-scoped", "original_query": "猫", "top_k": 1,
                "page_size": 1, "category_rank": heads.VERSION, "favorites_only": True}
        result = await client.post("/api/v1/search/place-aware", headers=headers, json=body)
        assert result.status_code == 200, result.text
        first = result.json()
        assert [row["asset_id"] for row in first["hits"]] == [ids[0]]
        assert first["total_candidates"] == 1
        set_favorite(env.catalog, ids[1], favorite=True)
        saved = await client.post("/api/v1/photo-collections/from-search", headers=headers, json={
            "name": "stale", "create_request_id": "category-stale-favorites",
            "mode": "ranked_candidates", "source_token": first["photo_collection_source_token"]})
        assert saved.status_code == 409, saved.text
        assert saved.json()["code"] == "FAVORITES_REVISION_STALE"

    env.scenario(scenario)


@pytest.mark.parametrize("query,extra", [("猫在沙发上", {}), ("猫", {"retrieval_mode": "text"}),
                                        ("猫", {"rerank": True}), ("", {})])
def test_category_rejects_non_exact_or_conflicting_mode_before_encoder(category_env, query, extra):
    env, _ = category_env
    env.context.query_encoder = lambda: pytest.fail("must refuse before encoder")

    async def scenario(client):
        csrf = await env.authenticate(client)
        response = await client.post("/api/v1/search/place-aware", headers=env.post_headers(csrf), json={
            "request_id": "bad-category", "original_query": query, "category_rank": heads.VERSION, **extra})
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "CATEGORY_RANK_UNAVAILABLE"

    env.scenario(scenario)
