from __future__ import annotations

from httpx import AsyncClient
from p3_persons_page_fixtures import seed_library


def test_home_and_ordinary_browse_do_not_start_memories(api_env) -> None:
    async def scenario(client: AsyncClient) -> None:
        await api_env.authenticate(client)
        assert api_env.context._memories_runner is None
        home = await client.get("/")
        assert home.status_code == 200
        browse = await client.get("/api/v1/assets", params={"limit": 1})
        assert browse.status_code == 200, browse.text
        assert api_env.context._memories_runner is None

        memories = await client.get("/api/v1/memories/cards")
        assert memories.status_code == 200, memories.text
        assert api_env.context._memories_runner is not None

    api_env.scenario(scenario)
    runner = api_env.context._memories_runner
    assert runner is not None and runner.wait_idle()


def test_memories_save_rechecks_stale_members_without_partial_collection(api_env) -> None:
    seeded = seed_library(api_env, {"selected.jpg": [], "stale.jpg": []})
    asset_ids = [int(item["asset_id"]) for item in seeded.values()]
    stale_id = asset_ids[-1]
    with api_env.service.catalog.write() as connection:
        connection.execute("UPDATE file_locations SET availability='MISSING' WHERE asset_id=?", (stale_id,))
    before = int(api_env.service.catalog.read_one("SELECT COUNT(*) FROM photo_collections")[0])

    async def scenario(client: AsyncClient) -> None:
        csrf = await api_env.authenticate(client)
        response = await client.post("/api/v1/photo-collections", headers=api_env.post_headers(csrf), json={
            "name": "Stale memory selection", "create_request_id": "memories-stale-save",
            "asset_ids": asset_ids, "require_present": True,
        })
        assert response.status_code == 409, response.text
        assert int(api_env.service.catalog.read_one("SELECT COUNT(*) FROM photo_collections")[0]) == before
        assert api_env.service.catalog.read_one(
            "SELECT 1 FROM photo_collections WHERE create_request_id='memories-stale-save'"
        ) is None

    api_env.scenario(scenario)


def test_memories_save_idempotency_does_not_reuse_collection_without_present_images(api_env) -> None:
    seeded = seed_library(api_env, {"selected.jpg": [], "stale.jpg": []})
    asset_ids = [int(item["asset_id"]) for item in seeded.values()]
    stale_id = asset_ids[-1]
    with api_env.service.catalog.write() as connection:
        connection.execute("UPDATE file_locations SET availability='MISSING' WHERE asset_id=?", (stale_id,))
    before = int(api_env.service.catalog.read_one("SELECT COUNT(*) FROM photo_collections")[0])

    async def scenario(client: AsyncClient) -> None:
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        base = {"name": "Existing ordinary save", "create_request_id": "memories-present-replay",
                "asset_ids": asset_ids}
        existing = await client.post("/api/v1/photo-collections", headers=headers, json=base)
        assert existing.status_code == 200, existing.text
        replay = await client.post("/api/v1/photo-collections", headers=headers,
                                   json={**base, "require_present": True})
        assert replay.status_code == 409, replay.text
        assert int(api_env.service.catalog.read_one("SELECT COUNT(*) FROM photo_collections")[0]) == before + 1

    api_env.scenario(scenario)
