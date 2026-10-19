from __future__ import annotations

from p3_persons_page_fixtures import seed_library

from photo_search.photo_collections import create_collection, edit_members


def test_filter_search_snapshot_and_photo_collection_search_scope(api_env):
    seeded = seed_library(api_env, {"one.jpg": [], "two.jpg": [], "three.jpg": []})
    expected_ids = {int(item["asset_id"]) for item in seeded.values()}

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        search = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "photo-collection-source", "retrieval_mode": "text",
            "original_query": "", "page_size": 1,
        })
        assert search.status_code == 200, search.text
        source = search.json()
        assert source["photo_collection_source_mode"] == "all_filter_matches"
        assert source["photo_collection_source_token"]

        saved = await client.post("/api/v1/photo-collections/from-search", headers=headers, json={
            "name": "All filtered", "create_request_id": "save-filtered-search",
            "source_token": source["photo_collection_source_token"],
            "mode": "all_filter_matches",
        })
        assert saved.status_code == 200, saved.text
        collection = saved.json()["collection"]
        assert collection["member_count"] == len(expected_ids)
        destination = await client.post("/api/v1/photo-collections", headers=headers, json={
            "name": "Append target", "create_request_id": "create-append-target",
        })
        assert destination.status_code == 200, destination.text
        target = destination.json()["collection"]
        appended = await client.post(
            f"/api/v1/photo-collections/{target['collection_id']}/members/from-search",
            headers=headers, json={"request_id":"append-all-filtered-once",
                "source_token":source["photo_collection_source_token"],
                "mode":"all_filter_matches", "instance_token":target["instance_token"],
                "expected_revision":target["revision"]},
        )
        assert appended.status_code == 200, appended.text
        assert appended.json()["changed"] == len(expected_ids)
        api_env.app.state.api._photo_collection_sources.clear()
        replayed_append = await client.post(
            f"/api/v1/photo-collections/{target['collection_id']}/members/from-search",
            headers=headers, json={"request_id":"append-all-filtered-once",
                "source_token":source["photo_collection_source_token"],
                "mode":"all_filter_matches", "instance_token":target["instance_token"],
                "expected_revision":target["revision"]},
        )
        assert replayed_append.status_code == 200, replayed_append.text
        assert replayed_append.json()["reused"] is True
        assert replayed_append.json()["changed"] == len(expected_ids)
        assert api_env.service.catalog.read_one(
            "SELECT member_count FROM photo_collections WHERE collection_id=?", (target["collection_id"],)
        )[0] == len(expected_ids)
        api_env.app.state.api._photo_collection_sources.clear()
        replay = await client.post("/api/v1/photo-collections/from-search", headers=headers, json={
            "name": "All filtered", "create_request_id": "save-filtered-search",
            "source_token": source["photo_collection_source_token"],
            "mode": "all_filter_matches",
        })
        assert replay.status_code == 200, replay.text
        assert replay.json()["reused"] is True
        assert replay.json()["collection"]["collection_id"] == collection["collection_id"]

        page = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "photo-collection-scoped-search", "retrieval_mode": "text",
            "original_query": "", "page_size": 1,
            "search_scope": {"kind": "photo_collection",
                "photo_collection_id": collection["collection_id"],
                "instance_token": collection["instance_token"], "revision": collection["revision"]},
        })
        assert page.status_code == 200, page.text
        scoped = page.json()
        assert scoped["total_candidates"] == len(expected_ids)
        assert int(scoped["hits"][0]["asset_id"]) in expected_ids

        assets = await client.get(
            f"/api/v1/photo-collections/{collection['collection_id']}/assets?limit=50"
            f"&instance_token={collection['instance_token']}"
        )
        assert assets.status_code == 200, assets.text
        assert {int(item["asset_id"]) for item in assets.json()["assets"]} == expected_ids
        assert saved.json()["reused"] is False
        return collection

    api_env.scenario(scenario)


def test_ranked_search_save_refuses_a_changed_photo_collection_scope(api_env):
    seeded = seed_library(api_env, {"sunset-one.jpg": [], "sunset-two.jpg": []})
    first_id = int(seeded["sunset-one.jpg"]["asset_id"])
    second_id = int(seeded["sunset-two.jpg"]["asset_id"])
    collection = create_collection(api_env.service.catalog, "Scoped source", "scoped-source",
                                   asset_ids=[first_id])["collection"]

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        response = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id":"ranked-source", "retrieval_mode":"text", "original_query":"sunset",
            "page_size":1, "search_scope":{"kind":"photo_collection",
                "photo_collection_id":collection["collection_id"],
                "instance_token":collection["instance_token"], "revision":collection["revision"]},
        })
        assert response.status_code == 200, response.text
        source = response.json()
        assert source["photo_collection_source_mode"] == "ranked_candidates"
        edit_members(api_env.service.catalog, collection["collection_id"], [second_id], action="add",
                     instance_token=collection["instance_token"], expected_revision=collection["revision"])
        saved = await client.post("/api/v1/photo-collections/from-search", headers=headers, json={
            "name":"Stale result", "create_request_id":"stale-ranked-save",
            "source_token":source["photo_collection_source_token"], "mode":"ranked_candidates",
        })
        assert saved.status_code == 409, saved.text
        assert saved.json()["code"] == "PHOTO_COLLECTION_REVISION_STALE"
        assert api_env.service.catalog.read_one(
            "SELECT COUNT(*) FROM photo_collections WHERE name='Stale result'")[0] == 0

    api_env.scenario(scenario)
