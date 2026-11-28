from __future__ import annotations


def test_baidu_provider_settings_are_private_redacted_and_csrf_protected(api_env):
    async def scenario(client):
        unauthenticated = await client.get("/api/v1/map/baidu/provider-runtime")
        assert unauthenticated.status_code == 401
        csrf = await api_env.authenticate(client)

        missing = await client.get("/api/v1/map/baidu/provider-settings")
        assert missing.status_code == 200
        assert missing.headers["cache-control"] == "no-store, private"
        assert missing.json() == {"configured": False, "provider": "baidu", "default_layer": "normal", "status": "NOT_CONFIGURED"}
        runtime = await client.get("/api/v1/map/baidu/provider-runtime")
        assert runtime.json()["status"] == "NOT_CONFIGURED"
        assert "ak" not in runtime.json()

        blocked = await client.put("/api/v1/map/baidu/provider-settings", json={"ak": "fake-map-key"})
        assert blocked.status_code == 403
        saved = await client.put("/api/v1/map/baidu/provider-settings", json={"ak": "fake-map-key-001"}, headers={"X-CSRF-Token": csrf, "Origin": "http://127.0.0.1"})
        assert saved.status_code == 200
        assert "fake-map-key-001" not in saved.text
        assert saved.headers["cache-control"] == "no-store, private"
        runtime = await client.get("/api/v1/map/baidu/provider-runtime")
        assert runtime.json()["ak"] == "fake-map-key-001"
        assert runtime.headers["cache-control"] == "no-store, private"

        empty = await client.put("/api/v1/map/baidu/provider-settings", json={"ak": ""}, headers={"X-CSRF-Token": csrf, "Origin": "http://127.0.0.1"})
        assert empty.status_code == 422
        assert (await client.get("/api/v1/map/baidu/provider-runtime")).json()["ak"] == "fake-map-key-001"

        layer = await client.put("/api/v1/map/baidu/provider-settings", json={"default_layer": "hybrid"}, headers={"X-CSRF-Token": csrf, "Origin": "http://127.0.0.1"})
        assert layer.status_code == 200 and layer.json()["default_layer"] == "hybrid"
        cleared = await client.delete("/api/v1/map/baidu/provider-settings/ak", headers={"X-CSRF-Token": csrf, "Origin": "http://127.0.0.1"})
        assert cleared.status_code == 200 and not cleared.json()["configured"]
        assert (await client.get("/api/v1/map/baidu/provider-runtime")).json()["status"] == "NOT_CONFIGURED"

    api_env.scenario(scenario)


def test_baidu_provider_config_corruption_is_reported_without_key_disclosure(api_env):
    path = api_env.data_root / "settings" / "map-provider-baidu.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"schema_version":1,"ak":"never-return-this', encoding="utf-8")

    async def scenario(client):
        await api_env.authenticate(client)
        settings = await client.get("/api/v1/map/baidu/provider-settings")
        runtime = await client.get("/api/v1/map/baidu/provider-runtime")
        assert settings.status_code == 200 and settings.json()["status"] == "CONFIG_UNREADABLE"
        assert runtime.status_code == 200 and runtime.json() == {"status": "CONFIG_UNREADABLE"}
        assert "never-return-this" not in settings.text + runtime.text

    api_env.scenario(scenario)
