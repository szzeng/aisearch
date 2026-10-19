from __future__ import annotations

from p3_persons_page_fixtures import seed_library


def test_filter_search_creates_dynamic_smart_rule_and_exclusion_is_recoverable(api_env):
    seeded = seed_library(api_env, {"one.jpg": [], "two.jpg": [], "three.jpg": []})
    asset_id = int(next(iter(seeded.values()))["asset_id"])

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        search = await client.post("/api/v1/search/place-aware", headers=headers, json={
            "request_id": "smart-source-filter", "retrieval_mode": "text",
            "original_query": "", "page_size": 1,
        })
        assert search.status_code == 200, search.text
        source = search.json()
        assert source["photo_collection_source_mode"] == "all_filter_matches"
        saved = await client.post("/api/v1/photo-smart-collections/from-search", headers=headers, json={
            "name": "Dynamic photos", "create_request_id": "smart-create-filter",
            "source_token": source["photo_collection_source_token"],
        })
        assert saved.status_code == 200, saved.text
        collection = saved.json()["collection"]
        assert collection["execution_kind"] == "FILTER"
        detail = await client.get(
            f"/api/v1/photo-smart-collections/{collection['smart_id']}?instance_token={collection['instance_token']}"
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["rule"]["execution_kind"] == "FILTER"
        assert detail.json()["collection"]["freshness"] == "NO_CACHE"
        listed = await client.get("/api/v1/photo-smart-collections?limit=20")
        assert listed.status_code == 200, listed.text
        assert [item["smart_id"] for item in listed.json()["collections"]] == [collection["smart_id"]]
        page = await client.get(
            f"/api/v1/photo-smart-collections/{collection['smart_id']}/assets"
            f"?instance_token={collection['instance_token']}&limit=50"
        )
        assert page.status_code == 200, page.text
        assert len(page.json()["assets"]) == len(seeded)
        excluded = await client.put(
            f"/api/v1/photo-smart-collections/{collection['smart_id']}/exclusions/{asset_id}",
            headers=headers, json={"instance_token": collection["instance_token"], "expected_revision": 1},
        )
        assert excluded.status_code == 200, excluded.text
        rows = await client.get(
            f"/api/v1/photo-smart-collections/{collection['smart_id']}/exclusions"
            f"?instance_token={collection['instance_token']}"
        )
        assert rows.status_code == 200, rows.text
        assert [item["asset_id"] for item in rows.json()["assets"]] == [asset_id]
        after_exclusion = await client.get(
            f"/api/v1/photo-smart-collections/{collection['smart_id']}/assets"
            f"?instance_token={collection['instance_token']}&limit=50"
        )
        assert after_exclusion.status_code == 200, after_exclusion.text
        assert asset_id not in {int(item["asset_id"]) for item in after_exclusion.json()["assets"]}
        restored = await client.request("DELETE",
            f"/api/v1/photo-smart-collections/{collection['smart_id']}/exclusions/{asset_id}",
            headers=headers, json={"instance_token": collection["instance_token"], "expected_revision": 2},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["changed"] is True
        assert api_env.service.catalog.read_one(
            "SELECT rule_json FROM photo_smart_collections WHERE smart_id=?", (collection["smart_id"],)
        )[0].find(source["photo_collection_source_token"]) == -1
        return collection

    api_env.scenario(scenario)
