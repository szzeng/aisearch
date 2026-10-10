"""Synthetic API coverage for controlled local actions; no desktop process is launched."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from photo_search.api import local_actions, routes
from photo_search.api.errors import ApiError
from photo_search.api.files import ControlledLocation


def _asset_locations(env: Any, asset_id: int) -> list[int]:
    return [
        int(row["location_id"])
        for row in env.service.catalog.read_all(
            "SELECT location_id FROM file_locations WHERE asset_id = ? ORDER BY location_id",
            (asset_id,),
        )
    ]


def test_local_action_selects_exact_registered_copy_and_protects_boundaries(api_env, monkeypatch) -> None:
    api_env.add_photo("相册 folder/海边 照片.jpg", color=(12, 34, 56))
    first_source = api_env.register_source("主库")
    api_env.scan(first_source["source_id"])
    first_asset = api_env.first_asset_id()

    second_root = api_env.tmp_path / "second library"
    second_root.mkdir()
    shutil.copy2(api_env.library / "相册 folder" / "海边 照片.jpg", second_root / "海边 照片.jpg")
    second_source = api_env.register_source("备份", path=second_root)
    api_env.scan(second_source["source_id"])
    locations = _asset_locations(api_env, first_asset)
    assert len(locations) == 2

    other = api_env.add_photo("other.jpg", color=(200, 12, 9))
    api_env.scan(first_source["source_id"])
    other_asset = api_env.asset_id_for(hashlib.sha256(other.read_bytes()).hexdigest())
    other_location = _asset_locations(api_env, other_asset)[0]

    calls: list[tuple[int, str, str]] = []
    monkeypatch.setattr(routes, "_desktop_supported", lambda: True)
    monkeypatch.setattr(
        routes,
        "perform_local_action",
        lambda location, action: calls.append((location.location_id, action, str(location.absolute_path))),
    )

    async def scenario(client: AsyncClient) -> None:
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        for action in ("open", "reveal"):
            response = await client.post(
                f"/api/v1/assets/{first_asset}/local-action",
                json={"action": action, "location_id": locations[1]},
                headers=headers,
            )
            assert response.status_code == 200, response.text
            assert response.json() == {"status": "accepted", "action": action}
        assert [call[:2] for call in calls] == [(locations[1], "open"), (locations[1], "reveal")]
        assert calls[0][2].endswith("海边 照片.jpg")

        # A location belonging to another asset cannot be selected by tampering with the body.
        refused = await client.post(
            f"/api/v1/assets/{first_asset}/local-action",
            json={"action": "open", "location_id": other_location},
            headers=headers,
        )
        assert refused.status_code == 409
        assert len(calls) == 2

        # The action schema accepts no caller-supplied path or executable arguments.
        malformed = await client.post(
            f"/api/v1/assets/{first_asset}/local-action",
            json={"action": "open", "location_id": locations[0], "path": "C:/evil.exe"},
            headers=headers,
        )
        assert malformed.status_code == 422
        assert len(calls) == 2

        api_env.service.catalog.connection.execute(
            "UPDATE file_locations SET relative_path = ?, path_key = ? WHERE location_id = ?",
            ("../outside.jpg", "../outside.jpg", locations[0]),
        )
        malicious = await client.post(
            f"/api/v1/assets/{first_asset}/local-action",
            json={"action": "reveal", "location_id": locations[0]},
            headers=headers,
        )
        assert malicious.status_code == 403
        assert malicious.json()["code"] == "ACCESS_DENIED"
        assert len(calls) == 2

    api_env.scenario(scenario)


def test_local_action_rejects_missing_csrf_remote_peer_offline_and_changed_file(api_env, monkeypatch) -> None:
    path = api_env.add_photo("中文/原图.jpg")
    source = api_env.register_source()
    api_env.scan(source["source_id"])
    asset_id = api_env.first_asset_id()
    location_id = _asset_locations(api_env, asset_id)[0]
    calls: list[int] = []
    monkeypatch.setattr(routes, "_desktop_supported", lambda: True)
    monkeypatch.setattr(routes, "perform_local_action", lambda *_: calls.append(1))

    async def scenario(client: AsyncClient) -> None:
        unauthenticated = await client.post(
            f"/api/v1/assets/{asset_id}/local-action",
            json={"action": "open", "location_id": location_id},
            headers={"Origin": "http://127.0.0.1"},
        )
        assert unauthenticated.status_code == 401
        assert calls == []
        csrf = await api_env.authenticate(client)
        response = await client.post(
            f"/api/v1/assets/{asset_id}/local-action",
            json={"action": "open", "location_id": location_id},
            headers={"Origin": "http://127.0.0.1"},
        )
        assert response.status_code == 403
        assert response.json()["code"] == "CSRF_INVALID"
        wrong_csrf = await client.post(
            f"/api/v1/assets/{asset_id}/local-action",
            json={"action": "open", "location_id": location_id},
            headers={"Origin": "http://127.0.0.1", "X-CSRF-Token": "wrong-token"},
        )
        assert wrong_csrf.status_code == 403
        assert wrong_csrf.json()["code"] == "CSRF_INVALID"

        api_env.service.catalog.connection.execute(
            "UPDATE source_roots SET status = 'OFFLINE' WHERE source_id = ?", (source["source_id"],)
        )
        offline = await client.post(
            f"/api/v1/assets/{asset_id}/local-action",
            json={"action": "open", "location_id": location_id},
            headers=api_env.post_headers(csrf),
        )
        assert offline.status_code == 409
        assert calls == []
        api_env.service.catalog.connection.execute(
            "UPDATE source_roots SET status = 'ONLINE' WHERE source_id = ?", (source["source_id"],)
        )

        path.unlink()
        missing = await client.post(
            f"/api/v1/assets/{asset_id}/local-action",
            json={"action": "open", "location_id": location_id},
            headers=api_env.post_headers(csrf),
        )
        assert missing.status_code == 409
        assert missing.json()["code"] == "SOURCE_FILE_MISSING"
        assert calls == []

        # Replacing it with different bytes invalidates the recorded size/mtime guard.
        path.write_bytes(b"changed")
        changed = await client.post(
            f"/api/v1/assets/{asset_id}/local-action",
            json={"action": "reveal", "location_id": location_id},
            headers=api_env.post_headers(csrf),
        )
        assert changed.status_code == 409
        assert calls == []

    api_env.scenario(scenario)


def test_local_action_rejects_non_loopback_peer(api_env, monkeypatch) -> None:
    api_env.add_photo("photo.jpg")
    source = api_env.register_source()
    api_env.scan(source["source_id"])
    asset_id = api_env.first_asset_id()
    location_id = _asset_locations(api_env, asset_id)[0]
    calls: list[int] = []
    monkeypatch.setattr(routes, "_desktop_supported", lambda: True)
    monkeypatch.setattr(routes, "perform_local_action", lambda *_: calls.append(1))

    async def scenario() -> None:
        transport = ASGITransport(app=api_env.app, client=("192.0.2.20", 4567))
        async with AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            csrf = await api_env.authenticate(client)
            response = await client.post(
                f"/api/v1/assets/{asset_id}/local-action",
                json={"action": "open", "location_id": location_id},
                headers=api_env.post_headers(csrf),
            )
            assert response.status_code == 403
            assert response.json()["code"] == "LOCAL_ACTION_UNAVAILABLE"
        assert calls == []

    asyncio.run(scenario())


def test_local_action_refuses_non_windows(api_env, monkeypatch) -> None:
    api_env.add_photo("photo.jpg")
    source = api_env.register_source()
    api_env.scan(source["source_id"])
    asset_id = api_env.first_asset_id()
    location_id = _asset_locations(api_env, asset_id)[0]
    calls: list[int] = []
    monkeypatch.setattr(routes, "_desktop_supported", lambda: False)
    monkeypatch.setattr(routes, "perform_local_action", lambda *_: calls.append(1))

    async def scenario(client: AsyncClient) -> None:
        csrf = await api_env.authenticate(client)
        response = await client.post(
            f"/api/v1/assets/{asset_id}/local-action",
            json={"action": "open", "location_id": location_id},
            headers=api_env.post_headers(csrf),
        )
        assert response.status_code == 409
        assert response.json()["code"] == "LOCAL_ACTION_UNAVAILABLE"
        assert calls == []

    api_env.scenario(scenario)


def test_local_action_refuses_a_non_loopback_server_bind(api_env, monkeypatch) -> None:
    api_env.add_photo("photo.jpg")
    source = api_env.register_source()
    api_env.scan(source["source_id"])
    asset_id = api_env.first_asset_id()
    location_id = _asset_locations(api_env, asset_id)[0]
    calls: list[int] = []
    monkeypatch.setattr(routes, "_desktop_supported", lambda: True)
    monkeypatch.setattr(routes, "perform_local_action", lambda *_: calls.append(1))
    api_env.context.settings = replace(
        api_env.context.settings, bind_host="0.0.0.0", allow_non_loopback=True
    )

    async def scenario(client: AsyncClient) -> None:
        csrf = await api_env.authenticate(client)
        response = await client.post(
            f"/api/v1/assets/{asset_id}/local-action",
            json={"action": "open", "location_id": location_id},
            headers=api_env.post_headers(csrf),
        )
        assert response.status_code == 403
        assert response.json()["code"] == "LOCAL_ACTION_UNAVAILABLE"
        assert calls == []

    api_env.scenario(scenario)


def test_fixed_windows_adapters_use_only_the_registered_path(monkeypatch, tmp_path) -> None:
    original = tmp_path / "中文 相册" / "照片 1.jpg"
    location = ControlledLocation(3, 2, "中文 相册/照片 1.jpg", str(tmp_path), original, 10, 20)
    opened: list[Path] = []
    revealed: list[tuple[list[str], bool]] = []
    monkeypatch.setattr(local_actions, "_system_open", lambda path: opened.append(path))

    def fake_popen(args, *, shell):
        revealed.append((args, shell))
        return object()

    monkeypatch.setattr(local_actions.subprocess, "Popen", fake_popen)
    local_actions.perform_local_action(location, "open")
    local_actions.perform_local_action(location, "reveal")
    assert opened == [original]
    assert revealed == [(["explorer.exe", f"/select,{original}"], False)]

    executable_named = ControlledLocation(
        4, 2, "photo.exe", str(tmp_path), tmp_path / "photo.exe", 10, 20
    )
    with pytest.raises(ApiError) as failure:
        local_actions.perform_local_action(executable_named, "open")
    assert failure.value.code == "ACCESS_DENIED"
    assert opened == [original]
