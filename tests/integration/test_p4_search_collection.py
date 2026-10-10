"""Focused synthetic coverage for person-collection search scope and paging."""

from __future__ import annotations

from typing import Any

from conftest import ApiEnv
from p2_search_api_fixtures import build_search_env
from p3_persons_page_fixtures import add_link, add_person, insert_face, seed_library

from photo_search.search.contracts import SearchRequest
from photo_search.search.natural_intent import parse_collection_intent
from photo_search.search.retrieval import DEFAULT_TOP_K, SearchScope, scope_condition
from photo_search.search.service import search_request
from photo_search.search.sessions import SearchSessionStore


def _url(catalog: Any, collection_id: int) -> str:
    row = catalog.read_one(
        "SELECT instance_token FROM person_collections WHERE collection_id=?", (collection_id,)
    )
    return f"/api/v1/persons/collections/{collection_id}/{row['instance_token']}"


def test_collection_parse_scope_paging_empty_over_100_and_stale_cursor(api_env: ApiEnv, monkeypatch: Any) -> None:
    seeded = seed_library(api_env, {
        "shared.jpg": [(20, 20, 90, 90), (150, 30, 220, 100)],
        "first.jpg": [(30, 30, 100, 100)],
        "outside.jpg": [(40, 40, 110, 110)],
    })
    catalog = api_env.service.catalog
    first, second, outside = add_person(catalog), add_person(catalog), add_person(catalog)
    add_link(catalog, seeded["shared.jpg"]["faces"][0], first)
    add_link(catalog, seeded["shared.jpg"]["faces"][1], second)
    add_link(catalog, seeded["first.jpg"]["faces"][0], first)
    add_link(catalog, seeded["outside.jpg"]["faces"][0], outside)
    from photo_search.taken_time.contract import civil_key
    with catalog.write() as connection:
        for name, year in (("shared.jpg", 2023), ("first.jpg", 2024), ("outside.jpg", 2024)):
            connection.execute(
                "INSERT INTO asset_metadata(asset_id, taken_at_raw, taken_at_sort, time_source, "
                "time_confidence, timezone_offset, camera) VALUES (?, ?, ?, 'EXIF_ORIGINAL', "
                "'HIGH', NULL, NULL)",
                (seeded[name]["asset_id"], f"{year}:06:01 12:00:00", civil_key(year, 6, 1, 12, 0, 0)),
            )

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        created = await client.post("/api/v1/persons/collections", headers=headers,
                                    json={"name": "同事"})
        collection_id = int(created.json()["collection"]["collection_id"])
        long_name = await client.post("/api/v1/persons/collections", headers=headers,
                                      json={"name": "同事出游"})
        assert long_name.status_code == 200
        add = await client.post(_url(catalog, collection_id) + "/members", headers=headers,
                                json={"action": "add", "person_ids": [first, second]})
        assert add.status_code == 200

        # A semantic query phrased as "collection name + 的 + content" must retain both
        # the collection authority and only the residual content query.
        from photo_search.api import routes
        observed: dict[str, Any] = {}

        def capture_semantic_request(_catalog: Any, envelope: Any, **kwargs: Any) -> dict[str, Any]:
            observed["text"] = envelope.text
            observed["collection_id"] = kwargs["collection_id"]
            return {"filters": {}, "hits": []}

        with monkeypatch.context() as scoped_patch:
            scoped_patch.setattr(routes, "search_request", capture_semantic_request)
            semantic_phrase = await client.post("/api/v1/search/place-aware", headers=headers, json={
                "request_id": "collection-semantic-phrase", "original_query": "同事的裙子",
            })
        assert semantic_phrase.status_code == 200, semantic_phrase.text
        assert semantic_phrase.json()["collection_scope"]["collection_id"] == collection_id
        assert semantic_phrase.json()["content_query"] == "裙子"
        assert observed == {"text": "裙子", "collection_id": collection_id}

        first_page = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "collection-search-first", "original_query": "同事的照片",
            "page_size": 1,
        })
        assert first_page.status_code == 200, first_page.text
        body = first_page.json()
        assert body["collection_scope"]["collection_id"] == collection_id
        assert body["total_candidates"] == 2
        assert body["filters"]["person_collection"]["revision"] == add.json()["revision"]
        assert body["has_more"] is True
        assert body["hits"][0]["asset_id"] in {seeded["shared.jpg"]["asset_id"], seeded["first.jpg"]["asset_id"]}
        dated = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "collection-search-year", "original_query": "2024年同事的照片",
        })
        assert dated.status_code == 200, dated.text
        assert [hit["asset_id"] for hit in dated.json()["hits"]] == [seeded["first.jpg"]["asset_id"]]
        assert dated.json()["filters"]["capture_from"].startswith("2024-01-01")

        # The place album and person collection are intersected inside the same SQLite scope.
        from photo_search.api import routes
        from photo_search.places import revision as place_revision
        place_choice = {"kind": "city", "key": "42", "name": "杭州", "parent": "浙江省",
                        "origin": None, "source": "synthetic", "aliases": ["杭州"]}
        monkeypatch.setattr(routes.place_choices, "choices", lambda *args, **kwargs: [place_choice])
        filter_revision = place_revision.read_filter_revision(catalog.connection)
        suggestion_revision = place_revision.read_suggestion_revision(catalog.connection)
        package_revision = routes.place_albums._index(catalog, api_env.data_root)[1]
        monkeypatch.setattr(routes.place_albums, "album_member_ids", lambda *args, **kwargs: (
            [seeded["shared.jpg"]["asset_id"], seeded["outside.jpg"]["asset_id"]],
            {"albums": [{"key": "42", "name": "杭州", "parent": "浙江省", "origin": None}],
             "catalog_filter_revision": filter_revision,
             "suggestion_revision": suggestion_revision, "package_revision": package_revision},
        ))
        place_page = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "collection-place-intersection",
            "original_query": "同事在杭州的照片", "page_size": 10,
        })
        assert place_page.status_code == 200, place_page.text
        assert [hit["asset_id"] for hit in place_page.json()["hits"]] == [seeded["shared.jpg"]["asset_id"]]
        assert place_page.json()["collection_scope"]["collection_id"] == collection_id

        # A collection with no members is a valid empty scope, never an unrestricted query.
        empty = await client.post("/api/v1/persons/collections", headers=headers,
                                  json={"name": "空合集"})
        empty_id = int(empty.json()["collection"]["collection_id"])
        empty_page = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "collection-search-empty", "original_query": "空合集的照片",
        })
        assert empty_page.status_code == 200, empty_page.text
        assert empty_page.json()["total_candidates"] == 0
        assert empty_page.json()["hits"] == []
        assert empty_page.json()["collection_scope"]["collection_id"] == empty_id

        # Members can exceed the ordinary SearchScope 100-person cap: the predicate takes
        # one collection parameter and still deduplicates a shared asset before paging.
        more_people = [add_person(catalog) for _ in range(99)]
        expanded = await client.post(_url(catalog, collection_id) + "/members", headers=headers,
                                     json={"action": "add", "person_ids": more_people})
        assert expanded.status_code == 200
        assert catalog.read_one(
            "SELECT COUNT(*) AS n FROM person_collection_members WHERE collection_id=?",
            (collection_id,),
        )["n"] == 101
        sql, params = scope_condition(catalog.connection,
                                     SearchScope.build(collection_id=collection_id))
        assert len(params) == 1
        assert catalog.read_all(f"SELECT a.asset_id FROM assets a WHERE {sql}", params)
        plan = catalog.read_all(f"EXPLAIN QUERY PLAN SELECT a.asset_id FROM assets a WHERE {sql}", params)
        assert "person_collection_members" in " ".join(str(row["detail"]) for row in plan)

        continuation = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "collection-search-stale", "original_query": "同事的照片",
            "intent_plan": body["intent_plan"], "page_size": 1,
            "search_session_id": body["search_session_id"], "cursor": body["next_cursor"],
        })
        assert continuation.status_code == 409, continuation.text
        assert continuation.json()["code"] == "CATALOG_FILTER_REVISION_STALE"

        wrong_token_plan = dict(body["intent_plan"])
        wrong_token_plan["collection_scope"] = dict(wrong_token_plan["collection_scope"])
        wrong_token_plan["collection_scope"]["instance_token"] = "0" * 32
        wrong_instance = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "collection-search-wrong-instance", "original_query": "同事的照片",
            "intent_plan": wrong_token_plan, "page_size": 1,
        })
        assert wrong_instance.status_code == 404

        # A membership edit between the SQL page and publication refuses the first page.
        race_collection = await client.post("/api/v1/persons/collections", headers=headers,
                                            json={"name": "竞争更新"})
        race_id = int(race_collection.json()["collection"]["collection_id"])
        race_token = race_collection.json()["collection"]["instance_token"]
        await client.post(_url(catalog, race_id) + "/members", headers=headers,
                          json={"action": "add", "person_ids": [first]})
        from photo_search.api import routes
        from photo_search.persons import collections as collection_service
        regular_filter_page = routes._filter_only_search_page

        def race_after_page(service, request, *, collection_scope=None):
            result = regular_filter_page(service, request, collection_scope=collection_scope)
            if collection_scope and collection_scope["collection_id"] == race_id:
                collection_service.edit_members(catalog, race_id, action="add",
                                                person_ids=[outside], instance_token=race_token)
            return result

        with monkeypatch.context() as scoped_patch:
            scoped_patch.setattr(routes, "_filter_only_search_page", race_after_page)
            raced = await client.post("/api/v1/search/place-aware", headers=headers, json={
                "request_id": "collection-search-publish-race", "original_query": "竞争更新的照片",
            })
        assert raced.status_code == 409
        assert raced.json()["code"] == "CATALOG_FILTER_REVISION_STALE"
        return collection_id

    api_env.scenario(scenario)
    parsed = parse_collection_intent("2024年同事出游合集", connection=catalog.connection)
    assert parsed["state"] == "recognized"
    assert parsed["collection"]["name"] == "同事出游"
    semantic_collection_query = parse_collection_intent("同事的裙子", connection=catalog.connection)
    assert semantic_collection_query["state"] == "recognized"
    assert semantic_collection_query["content_query"] == "的裙子"
    assert parse_collection_intent("同事聚会照片", connection=catalog.connection)["state"] == "none"
    assert parse_collection_intent("同事合集和同事出游合集", connection=catalog.connection)["state"] == "ambiguous"
    assert DEFAULT_TOP_K == 5000


def test_collection_scope_is_applied_by_fts_and_vector_candidate_walk(tmp_path: Any) -> None:
    env = build_search_env(tmp_path)
    catalog = env.catalog
    try:
        target_asset = int(env.assets["beach sunset.jpg"])
        outside_asset = int(env.assets["IMG_0001.JPG"])
        rows = {int(row["asset_id"]): row for row in catalog.read_all(
            "SELECT a.asset_id, a.sha256, l.location_id FROM assets a "
            "JOIN file_locations l ON l.asset_id=a.asset_id WHERE a.asset_id IN (?,?)",
            (target_asset, outside_asset),
        )}
        target_face = insert_face(catalog, asset_id=target_asset,
                                  sha256=str(rows[target_asset]["sha256"]),
                                  bbox=(20, 20, 90, 90), ordinal=0,
                                  location_id=int(rows[target_asset]["location_id"]))
        outside_face = insert_face(catalog, asset_id=outside_asset,
                                   sha256=str(rows[outside_asset]["sha256"]),
                                   bbox=(30, 30, 100, 100), ordinal=0,
                                   location_id=int(rows[outside_asset]["location_id"]))
        member, other = add_person(catalog), add_person(catalog)
        add_link(catalog, target_face, member)
        add_link(catalog, outside_face, other)
        from photo_search.persons import collections
        from photo_search.search.documents import catch_up
        from photo_search.search.retrieval import fts_channel, resolve_binding, vector_channel
        from photo_search.search.tokens import build_match

        created = collections.create_collection(catalog, "夕阳同事")
        collection_id = int(created["collection"]["collection_id"])
        collections.edit_members(catalog, collection_id, action="add", person_ids=[member],
                                 instance_token=created["collection"]["instance_token"])
        membership = catalog.read_one(
            "SELECT revision FROM person_collections WHERE collection_id=?", (collection_id,)
        )
        catch_up(catalog)
        scope = SearchScope.build(collection_id=collection_id)
        text_hits = fts_channel(catalog, build_match("sunset"), scope)
        assert [int(hit["asset_id"]) for hit in text_hits] == [target_asset]

        seen: list[int] = []

        class RecordingIndex:
            def search_candidates(self, *, asset_ids: list[int], **kwargs: Any) -> list[dict[str, Any]]:
                seen.extend(int(value) for value in asset_ids)
                return [{"asset_id": int(value), "distance": 0.1} for value in asset_ids]

        binding = resolve_binding(catalog, generation_id=env.generation,
                                  space_id=str(env.spec.envelope["space_id"]),
                                  dimension=int(env.spec.envelope["dimension"]))
        vector_page = vector_channel(catalog, index=RecordingIndex(), binding=binding,
                                     query_vector=[1.0, 0.0, 0.0], scope=scope, top_k=20)
        expected = [target_asset]
        assert seen == expected
        assert [int(hit["asset_id"]) for hit in vector_page["hits"]] == expected

        # Exercise the full semantic service path used by /search/place-aware. The
        # collection stays in the retrieval scope while its revision binds the cursor digest.
        service_vector_ids: list[int] = []

        class ServiceRecordingIndex:
            def search_candidates(self, *, asset_ids: list[int], **kwargs: Any) -> list[dict[str, Any]]:
                service_vector_ids.extend(int(value) for value in asset_ids)
                return [{"asset_id": int(value), "distance": 0.1} for value in asset_ids]

        semantic = search_request(
            catalog,
            SearchRequest(request_id="collection-semantic-query", text="sunset",
                          space_id=str(env.spec.envelope["space_id"]),
                          image_generation_id=env.generation,
                          dimension=int(env.spec.envelope["dimension"])),
            sessions=SearchSessionStore(),
            encoder=env.encoder,
            vector_index=ServiceRecordingIndex(),
            collection_id=collection_id,
            album_scope_revision=("person_collection", collection_id,
                                  created["collection"]["instance_token"],
                                  int(membership["revision"])),
        )
        assert service_vector_ids == expected
        assert [int(hit["asset_id"]) for hit in semantic["hits"]] == expected
    finally:
        env.library.close()


def test_collection_name_shared_with_person_and_place_requires_explicit_choice(api_env: ApiEnv, monkeypatch: Any) -> None:
    from photo_search.api import routes

    monkeypatch.setattr(routes.place_choices, "choices", lambda *args, **kwargs: [{
        "kind": "city", "key": "42", "name": "杭州", "content_query": "",
    }])
    add_person(api_env.service.catalog, name="杭州")

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        await client.post("/api/v1/persons/collections", headers=headers,
                          json={"name": "杭州"})
        response = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "collection-name-collision", "original_query": "杭州的照片",
        })
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["needs_scope_choice"] is True
        assert {choice["type"] for choice in body["scope_choices"]} == {
            "collection", "person", "place",
        }

    api_env.scenario(scenario)
