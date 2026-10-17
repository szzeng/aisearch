"""Literal search is useful without models and retains authoritative scopes and cursors."""

from __future__ import annotations

from typing import Any

import pytest
from catalog_concurrency_fixtures import second_catalog_writer
from p2_search_api_fixtures import build_search_env
from p2_search_fixtures import assign_place
from p3_persons_page_fixtures import add_link, add_person, seed_library
from p4_cn_fixtures import install

from photo_search.api import routes
from photo_search.api.context import ApiContext
from photo_search.catalog.errors import CatalogError
from photo_search.persons.service import PersonService
from photo_search.places import choices
from photo_search.search import service
from photo_search.search.contracts import SearchRequest
from photo_search.search.personal_search import PersonalSearchCoordinator
from photo_search.search.sessions import SearchSessionStore
from photo_search.search.tokens import TOKENIZER_VERSION, index_text


@pytest.fixture()
def forbid_semantics(monkeypatch: Any) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("literal search must not initialize models, resolve profiles or infer intent")

    monkeypatch.setattr(ApiContext, "query_encoder", forbidden)
    monkeypatch.setattr(ApiContext, "reranker", forbidden)
    monkeypatch.setattr(service, "_resolve_profile", forbidden)
    monkeypatch.setattr(service, "vector_channel", forbidden)
    monkeypatch.setattr(routes, "_resolve_profile", forbidden)
    monkeypatch.setattr(routes, "resolve_request_binding", forbidden)
    monkeypatch.setattr(PersonalSearchCoordinator, "collect", forbidden)


def payload(query: str = "sunset", **extra: Any) -> dict[str, Any]:
    return {"request_id": "literal-search", "retrieval_mode": "text", "text": query, **extra}


def add_ocr(catalog: Any, asset_id: int, text: str) -> None:
    row = catalog.read_one("SELECT sha256 FROM assets WHERE asset_id=?", (asset_id,))
    with catalog.write() as conn:
        conn.execute(
            "INSERT INTO asset_ocr_text(asset_id,ocr_document_id,input_revision,text_revision,"
            "processor_id,spec_revision,model_fingerprint,artifact_id,region_count,search_text,"
            "tokenizer_version,created_at,updated_at) VALUES(?,?,?,1,'fixture','fixture',?,"
            "'fixture',0,?,?,1,1)",
            (asset_id, f"text-mode-{asset_id}", row["sha256"], "a" * 64,
             index_text(text), TOKENIZER_VERSION),
        )


def test_text_search_uses_original_words_ocr_and_cross_field_explanations(api_env, forbid_semantics):
    seeded = seed_library(api_env, {"深圳 2026 AB123.jpg": [], "unrelated.jpg": []})
    selected = seeded["深圳 2026 AB123.jpg"]["asset_id"]
    add_ocr(api_env.service.catalog, selected, "发票付款 1280")

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        for query, expected_fields in (
            ("深圳", ["filename_text"]),
            ("2026", ["filename_text"]),
            ('"AB123"', ["filename_text"]),
            ("付款", ["ocr_text"]),
            ("深圳 付款", ["filename_text", "ocr_text"]),
        ):
            response = await client.post("/api/v1/search/place-aware", headers=api_env.post_headers(csrf),
                                         json=payload(query, original_query=query))
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["retrieval_mode"] == "text"
            assert body["content_query"] == query
            assert body["intent_plan"] is None
            assert body["place_scope"] is None
            assert body["profile_id"] is body["space_id"] is None
            assert body["channels"]["vector"]["engaged"] is False
            assert [hit["asset_id"] for hit in body["hits"]] == [selected]
            assert set(body["hits"][0]["matched_text"]) == set(expected_fields)
            assert body["hits"][0]["hit_reason"] == "TEXT"
        no_prefix = await client.post("/api/v1/search", headers=api_env.post_headers(csrf),
                                     json=payload('"AB12"'))
        prefix = await client.post("/api/v1/search", headers=api_env.post_headers(csrf),
                                  json=payload("AB12"))
        assert no_prefix.status_code == prefix.status_code == 200
        assert no_prefix.json()["hits"] == []
        assert [hit["asset_id"] for hit in prefix.json()["hits"]] == [selected]

    api_env.scenario(scenario)


@pytest.mark.parametrize("endpoint", ["/search", "/search/place-aware"])
def test_text_paging_and_time_browse_need_no_profile_and_refuse_changed_scope(
    api_env, forbid_semantics, endpoint,
):
    seed_library(api_env, {"sunset-1.jpg": [], "sunset-2.jpg": [], "other.jpg": []})

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        body = payload(page_size=1, **({"original_query": "sunset"} if endpoint.endswith("aware") else {}))
        first = await client.post("/api/v1" + endpoint, headers=api_env.post_headers(csrf), json=body)
        assert first.status_code == 200, first.text
        result = first.json()
        assert result["total_candidates"] == 2
        continuation = {**body, "cursor": result["next_cursor"], "search_session_id": result["search_session_id"]}
        more = await client.post("/api/v1" + endpoint, headers=api_env.post_headers(csrf), json=continuation)
        assert more.status_code == 200, more.text
        assert more.json()["hits"][0]["asset_id"] != result["hits"][0]["asset_id"]
        assert more.json()["retrieval_mode"] == "text"
        time = await client.get(f"/api/v1/search/sessions/{result['search_session_id']}/time?limit=1")
        assert time.status_code == 200, time.text
        assert time.json()["retrieval_mode"] == "text"
        assert time.json()["candidate_scope"] == "current_text_candidate_snapshot"
        time_more = await client.get(f"/api/v1/search/sessions/{result['search_session_id']}/time",
                                     params={"cursor": time.json()["next_cursor"], "limit": 1})
        assert time_more.status_code == 200, time_more.text
        assert time_more.json()["assets"][0]["asset_id"] != time.json()["assets"][0]["asset_id"]
        changed = await client.post("/api/v1" + endpoint, headers=api_env.post_headers(csrf),
                                    json={**continuation, "availability": "present"})
        assert changed.status_code == 422
        assert changed.json()["code"] == "INVALID_CURSOR"
        with api_env.service.catalog.write() as conn:
            conn.execute("UPDATE catalog_filter_revision SET revision=revision+1 WHERE revision_id=1")
        for response in (
            await client.post("/api/v1" + endpoint, headers=api_env.post_headers(csrf), json=continuation),
            await client.get(f"/api/v1/search/sessions/{result['search_session_id']}/time"),
        ):
            assert response.status_code == 409
            assert response.json()["code"] == "CATALOG_FILTER_REVISION_STALE"

    api_env.scenario(scenario)


def test_text_session_cannot_be_reused_in_combined_mode(api_env):
    seed_library(api_env, {"sunset-1.jpg": [], "sunset-2.jpg": []})

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        first = await client.post("/api/v1/search", headers=api_env.post_headers(csrf), json=payload(page_size=1))
        assert first.status_code == 200, first.text
        response = await client.post("/api/v1/search", headers=api_env.post_headers(csrf), json=payload(
            page_size=1, retrieval_mode="combined", cursor=first.json()["next_cursor"],
            search_session_id=first.json()["search_session_id"],
        ))
        assert response.status_code == 422
        assert response.json()["code"] == "INVALID_CURSOR"
        combined = await client.post("/api/v1/search", headers=api_env.post_headers(csrf),
                                     json={"request_id": "legacy", "text": "sunset"})
        assert combined.status_code == 422
        assert combined.json()["details"]["reason"] == "SEARCH_PROFILE_REQUIRED"

    api_env.scenario(scenario)


@pytest.mark.parametrize("conflict", [
    {"profile_id": "missing"}, {"space_id": "missing"}, {"dimension": 3},
    {"image_generation_id": "missing"}, {"rerank": True},
])
def test_conflicting_semantic_parameters_are_refused_before_loading_models(api_env, forbid_semantics, conflict):
    async def scenario(client):
        csrf = await api_env.authenticate(client)
        for endpoint, extra in (("search", {}), ("search/place-aware", {"original_query": "sunset"})):
            response = await client.post("/api/v1/" + endpoint, headers=api_env.post_headers(csrf),
                                         json=payload(**extra, **conflict))
            assert response.status_code == 422, response.text
            assert response.json()["code"] == "VALIDATION_ERROR"

    api_env.scenario(scenario)


def test_text_rejects_automatic_rewrite_and_image_query(api_env, forbid_semantics):
    async def scenario(client):
        csrf = await api_env.authenticate(client)
        for extra in ({"intent_plan": {}}, {"content_query": "beach"}, {"text": "beach"}):
            response = await client.post("/api/v1/search/place-aware", headers=api_env.post_headers(csrf),
                                         json=payload(original_query="sunset", **extra))
            assert response.status_code == 422, response.text
        image = await client.post("/api/v1/search/image", headers=api_env.post_headers(csrf),
                                  data={"request_id": "text-image", "retrieval_mode": "text"},
                                  files={"image": ("fixture.jpg", b"image", "image/jpeg")})
        assert image.status_code == 422, image.text

    api_env.scenario(scenario)
    with pytest.raises(CatalogError) as error:
        service.search_request(api_env.service.catalog, SearchRequest(request_id="direct", retrieval_mode="text"),
                               sessions=SearchSessionStore(), query_vector=[1.0])
    assert error.value.details["conflicting_fields"] == ["image_query"]


def test_text_explicit_person_place_source_and_time_filters_remain_authoritative(api_env, forbid_semantics):
    seeded = seed_library(api_env, {"sunset-1.jpg": [(20, 20, 90, 90)], "sunset-2.jpg": [(20, 20, 90, 90)]})
    catalog = api_env.service.catalog
    person = add_person(catalog, name="张三")
    for asset in seeded.values():
        add_link(catalog, asset["faces"][0], person)
    install(catalog, api_env.tmp_path / "places")
    city = catalog.read_one("SELECT place_id FROM place_external_refs WHERE external_id='city-hangzhou'")["place_id"]
    selected = seeded["sunset-1.jpg"]["asset_id"]
    assign_place(catalog, asset_id=selected, place_id=city, request_id="text-place")
    choice = next(item for item in choices.choices(catalog, text="杭州", data_root=api_env.data_root) if item["kind"] == "city")
    source_id = int(catalog.read_one("SELECT source_id FROM source_roots")["source_id"])

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        body = payload(original_query="sunset", source_id=source_id, person_ids=[person],
                       search_scope={"kind": "place", "place_kind": "city", "place_key": choice["key"]})
        selected_page = await client.post("/api/v1/search/place-aware", headers=api_env.post_headers(csrf), json=body)
        assert selected_page.status_code == 200, selected_page.text
        assert [hit["asset_id"] for hit in selected_page.json()["hits"]] == [selected]
        assert selected_page.json()["filters"]["person_ids"] == [person]
        assert selected_page.json()["filters"]["source_id"] == source_id
        bounded = await client.post("/api/v1/search/place-aware", headers=api_env.post_headers(csrf),
                                    json={**body, "capture_from": "2026-01-01", "capture_to_exclusive": "2027-01-01"})
        assert bounded.status_code == 200, bounded.text
        # These synthetic photos have no trusted capture timestamp: explicit range must exclude them.
        assert bounded.json()["hits"] == []
        assert bounded.json()["filters"]["capture_from"].startswith("2026-01-01")

    api_env.scenario(scenario)


def test_text_fixed_person_and_collection_scopes_page_and_reject_collection_changes(api_env, forbid_semantics):
    seeded = seed_library(api_env, {
        "sunset-shared.jpg": [(20, 20, 90, 90), (150, 30, 220, 100)],
        "sunset-selected.jpg": [(20, 20, 90, 90)], "sunset-other.jpg": [(20, 20, 90, 90)],
    })
    catalog = api_env.service.catalog
    first, second = add_person(catalog, name="张三"), add_person(catalog, name="李四")
    add_link(catalog, seeded["sunset-shared.jpg"]["faces"][0], first)
    add_link(catalog, seeded["sunset-shared.jpg"]["faces"][1], second)
    add_link(catalog, seeded["sunset-selected.jpg"]["faces"][0], first)
    add_link(catalog, seeded["sunset-other.jpg"]["faces"][0], second)

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        fixed_person = await client.post("/api/v1/search/place-aware", headers=headers, json=payload(
            original_query="sunset", search_scope={"kind": "person", "person_id": first},
            person_ids=[second],
        ))
        assert fixed_person.status_code == 200, fixed_person.text
        assert [hit["asset_id"] for hit in fixed_person.json()["hits"]] == [seeded["sunset-shared.jpg"]["asset_id"]]
        created = await client.post("/api/v1/persons/collections", headers=headers, json={"name": "AI"})
        assert created.status_code == 200, created.text
        collection = created.json()["collection"]
        url = f"/api/v1/persons/collections/{collection['collection_id']}/{collection['instance_token']}"
        added = await client.post(url + "/members", headers=headers, json={"action": "add", "person_ids": [first]})
        assert added.status_code == 200, added.text
        scope = {"kind": "collection", **dict(catalog.read_one(
            "SELECT collection_id,instance_token,revision,name FROM person_collections WHERE collection_id=?",
            (collection["collection_id"],),
        ))}
        # Both a ranked text query and empty pure filters preserve the selected collection.
        for query in ("sunset", ""):
            body = payload(query, original_query=query, search_scope=scope, page_size=1)
            first_page = await client.post("/api/v1/search/place-aware", headers=headers, json=body)
            assert first_page.status_code == 200, first_page.text
            result = first_page.json()
            assert result["retrieval_mode"] == "text" and result["intent_plan"] is None
            assert result["total_candidates"] == 2
            continuation = {**body, "cursor": result["next_cursor"], "search_session_id": result["search_session_id"]}
            more = await client.post("/api/v1/search/place-aware", headers=headers, json=continuation)
            assert more.status_code == 200, more.text
            assert {result["hits"][0]["asset_id"], more.json()["hits"][0]["asset_id"]} == {
                seeded["sunset-shared.jpg"]["asset_id"], seeded["sunset-selected.jpg"]["asset_id"],
            }
        changed = await client.post(url + "/members", headers=headers, json={"action": "add", "person_ids": [second]})
        assert changed.status_code == 200, changed.text
        stale = await client.post("/api/v1/search/place-aware", headers=headers, json=continuation)
        assert stale.status_code == 409, stale.text
        assert stale.json()["code"] == "CATALOG_FILTER_REVISION_STALE"

    api_env.scenario(scenario)


def test_text_still_works_with_an_unservable_active_profile(tmp_path, monkeypatch):
    env = build_search_env(tmp_path)
    try:
        profile_id = env.create_profile(fts_tokenizer_version="obsolete")
        with env.catalog.write() as conn:
            conn.execute("UPDATE active_search_profile SET profile_id=? WHERE state_id=1", (profile_id,))

        def forbidden(*_args, **_kwargs):
            raise AssertionError("text mode must not initialize an encoder or reranker")

        monkeypatch.setattr(ApiContext, "query_encoder", forbidden)
        monkeypatch.setattr(ApiContext, "reranker", forbidden)

        async def scenario(client):
            csrf = await env.authenticate(client)
            response = await client.post("/api/v1/search", headers=env.post_headers(csrf), json=payload())
            assert response.status_code == 200, response.text
            assert response.json()["profile_id"] is None
            assert [hit["asset_id"] for hit in response.json()["hits"]] == [env.assets["beach sunset.jpg"]]
            time = await client.get(f"/api/v1/search/sessions/{response.json()['search_session_id']}/time")
            assert time.status_code == 200, time.text

        env.scenario(scenario)
    finally:
        env.context.close()
        env.library.close()


def test_text_candidate_cap_is_honest_in_relevance_and_time_views(api_env, forbid_semantics):
    seed_library(api_env, {"sunset-1.jpg": [], "sunset-2.jpg": [], "sunset-3.jpg": []})

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        response = await client.post("/api/v1/search", headers=api_env.post_headers(csrf),
                                     json=payload(top_k=2))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total_candidates"] == 2 and body["candidates_truncated"] is True
        assert body["channels"]["vector"]["reason"] == "TEXT_RETRIEVAL_ONLY"
        time = await client.get(f"/api/v1/search/sessions/{body['search_session_id']}/time")
        assert time.status_code == 200, time.text
        assert time.json()["candidate_limit"] == 2
        assert time.json()["candidates_truncated"] is True

    api_env.scenario(scenario)


def test_empty_literal_query_cannot_reuse_combined_filter_cursor(api_env):
    seed_library(api_env, {"first.jpg": [], "second.jpg": []})

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        first = await client.post("/api/v1/search/place-aware", headers=api_env.post_headers(csrf),
                                  json=payload("", original_query="", retrieval_mode="combined", page_size=1))
        assert first.status_code == 200, first.text
        changed = await client.post("/api/v1/search/place-aware", headers=api_env.post_headers(csrf),
                                    json=payload("", original_query="", page_size=1,
                                                 search_session_id=first.json()["search_session_id"],
                                                 cursor=first.json()["next_cursor"]))
        assert changed.status_code == 409, changed.text
        assert changed.json()["code"] == "CATALOG_FILTER_REVISION_STALE"

    api_env.scenario(scenario)


@pytest.mark.parametrize("mode,query_kind", [
    ("text", "content"), ("text", "empty"),
    ("combined", "empty"), ("combined", "natural"),
])
@pytest.mark.parametrize("person_match", ["any", "all"])
def test_fixed_person_album_intersects_person_group_without_changing_any_or_all(
    api_env, monkeypatch, mode, query_kind, person_match,
):
    boxes = [(10, 20, 70, 80), (90, 20, 150, 80), (180, 20, 240, 80)]
    groups = {"a": (0,), "ab": (0, 1), "ac": (0, 2), "abc": (0, 1, 2), "bc": (1, 2)}
    seeded = seed_library(api_env, {f"sunset-{name}.jpg": boxes[:len(group)] for name, group in groups.items()})
    catalog = api_env.service.catalog
    people = [add_person(catalog, name=name) for name in ("张三", "李四", "王五")]
    for name, group in groups.items():
        for face, person_index in zip(seeded[f"sunset-{name}.jpg"]["faces"], group, strict=True):
            add_link(catalog, face, people[person_index])

    def forbidden(*_args, **_kwargs):
        raise AssertionError("text and pure person filters do not need semantic models")

    monkeypatch.setattr(ApiContext, "query_encoder", forbidden)
    monkeypatch.setattr(ApiContext, "reranker", forbidden)
    query = "sunset" if query_kind == "content" else (
        ("李四或者王五的照片" if person_match == "any" else "李四和王五的合照")
        if query_kind == "natural" else ""
    )
    expected = {seeded[f"sunset-{name}.jpg"]["asset_id"] for name in (
        ("ab", "ac", "abc") if person_match == "any" else ("abc",)
    )}

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        body = payload(query, retrieval_mode=mode, original_query=query, page_size=1,
                       search_scope={"kind": "person", "person_id": people[0]})
        if query_kind != "natural":
            body.update(person_ids=people[1:], person_match=person_match)
        first = await client.post("/api/v1/search/place-aware", headers=headers, json=body)
        assert first.status_code == 200, first.text
        first_body = page = first.json()
        assert page["total_candidates"] == len(expected)
        assert page["filters"]["person_ids"] == people[1:]
        assert page["filters"]["person_match"] == person_match
        if mode == "combined":
            assert "person_match_before_fixed_scope" not in page["intent_plan"]
            repeat = await client.post("/api/v1/search/place-aware", headers=headers,
                                       json={**body, "intent_plan": page["intent_plan"]})
            assert repeat.status_code == 200, repeat.text
            assert repeat.json()["total_candidates"] == len(expected)
            stale_plan = {**page["intent_plan"], "person_match_before_fixed_scope": "any"}
            legacy = await client.post("/api/v1/search/place-aware", headers=headers,
                                       json={**body, "intent_plan": stale_plan})
            assert legacy.status_code == 409
            assert "重新搜索" in legacy.json()["message"]
        found = {hit["asset_id"] for hit in page["hits"]}
        while page["has_more"]:
            continuation = {**body, "search_session_id": page["search_session_id"], "cursor": page["next_cursor"]}
            if mode == "combined":
                continuation["intent_plan"] = page["intent_plan"]
            response = await client.post("/api/v1/search/place-aware", headers=headers, json=continuation)
            assert response.status_code == 200, response.text
            page = response.json()
            found.update(hit["asset_id"] for hit in page["hits"])
        assert found == expected
        if person_match == "any":
            changed_scope = {**body, "search_session_id": first_body["search_session_id"],
                             "cursor": first_body["next_cursor"],
                             "search_scope": {"kind": "person", "person_id": people[1]}}
            if mode == "combined":
                changed_scope["intent_plan"] = first_body["intent_plan"]
            changed = await client.post("/api/v1/search/place-aware", headers=headers, json=changed_scope)
            assert changed.status_code == 409, changed.text
            assert changed.json()["code"] == "CATALOG_FILTER_REVISION_STALE"
        if query_kind == "content":
            time = await client.get(f"/api/v1/search/sessions/{first_body['search_session_id']}/time")
            assert time.status_code == 200, time.text
            assert {item["asset_id"] for item in time.json()["assets"]} == expected
        with catalog.write() as conn:
            conn.execute("UPDATE persons SET merged_into=?,revision=revision+1 WHERE person_id=?", (people[1], people[0]))
        stale_identity = await client.post("/api/v1/search/place-aware", headers=headers, json=body)
        assert stale_identity.status_code == 409, stale_identity.text
        assert stale_identity.json()["code"] == "CATALOG_FILTER_REVISION_STALE"

    api_env.scenario(scenario)


@pytest.mark.parametrize("query", ["sunset", ""])
def test_fixed_person_merge_between_scope_resolution_and_retrieval_refuses_mixed_page(api_env, monkeypatch, query):
    seeded = seed_library(api_env, {"sunset.jpg": [(10, 20, 70, 80)]})
    catalog = api_env.service.catalog
    fixed, target = add_person(catalog, name="张三"), add_person(catalog, name="李四")
    add_link(catalog, seeded["sunset.jpg"]["faces"][0], fixed)
    worker_name = "search_request" if query else "_filter_only_search_page"
    real_worker = getattr(routes, worker_name)
    merged = []

    with second_catalog_writer(catalog) as (second, run_commit):
        persons = PersonService(second, clock=second.now_ms)

        def commit_merge():
            revisions = {int(row["person_id"]): int(row["revision"]) for row in second.read_all(
                "SELECT person_id,revision FROM persons WHERE person_id IN (?,?)", (fixed, target),
            )}
            merged.append(persons.merge(
                target_person_id=target, target_expected_revision=revisions[target],
                sources=[{"person_id": fixed, "expected_revision": revisions[fixed]}],
                request_id="fixed-scope-concurrent-merge",
            ))

        def race_before_retrieval(*args, **kwargs):
            run_commit(commit_merge)
            return real_worker(*args, **kwargs)

        monkeypatch.setattr(routes, worker_name, race_before_retrieval)

        async def scenario(client):
            csrf = await api_env.authenticate(client)
            response = await client.post("/api/v1/search/place-aware", headers=api_env.post_headers(csrf),
                                         json=payload(query, original_query=query,
                                                      search_scope={"kind": "person", "person_id": fixed}))
            assert len(merged) == 1
            assert response.status_code == 409, response.text
            assert response.json()["code"] == "CATALOG_FILTER_REVISION_STALE"
            assert "hits" not in response.json()

        api_env.scenario(scenario)
