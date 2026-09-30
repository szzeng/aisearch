"""P1-05 scenarios 1 and 2: the public shell, the static assets and the session redirect.

Every fixture lives under ``tmp_path``; no real photo library is read.
"""

from __future__ import annotations

from conftest import DEFAULT_SESSION_TOKEN
from httpx import AsyncClient

TOKEN = DEFAULT_SESSION_TOKEN

_UNSAFE_TARGETS = [
    "http://evil.example",
    "https://evil.example/next",
    "//evil.example",
    "/\\evil.example",
    "/a\r\nb",
    "",
]


def test_the_shell_and_its_assets_are_public_while_the_api_is_not(api_env) -> None:
    import re

    async def scenario(client: AsyncClient) -> None:
        for path in ("/", "/search", "/persons", "/places", "/library", "/sources"):
            shell = await client.get(path)
            assert shell.status_code == 200
            assert "no-cache" in shell.headers["Cache-Control"]
            assert TOKEN not in shell.text
            assert str(api_env.data_root) not in shell.text
            for asset in re.findall(r'(?:src|href)="(/web/[^"]+)"', shell.text):
                response = await client.get(asset)
                assert response.status_code == 200
                assert "max-age" in response.headers["Cache-Control"]
        for path in ("/web/does-not-exist.js", "/web/%2e%2e/app.js", "/web/app.js/../../index.html"):
            assert (await client.get(path)).status_code == 404
        for path in ("/api/v1/stats", "/api/v1/persons", "/api/v1/places", "/api/v1/places/1"):
            assert (await client.get(path)).status_code == 401
        assert (await client.post("/api/v1/search", json={})).status_code == 401
        assert (await client.post("/api/v1/search/image")).status_code == 401
        await api_env.authenticate(client)
        created = await client.post("/api/v1/places", json={"canonical_name": "测试地点", "kind": "custom"}, headers=api_env.post_headers(api_env.csrf_token))
        assert created.status_code == 201, created.text
        detail = await client.get(f"/api/v1/places/{created.json()['place_id']}")
        assert detail.status_code == 200
        assert detail.json()["canonical_name"] == "测试地点"
        assert (await client.get("/api/v1/places/99999")).status_code == 404

    api_env.scenario(scenario)


def test_session_exchange_without_next_keeps_the_p1_04_json(api_env) -> None:
    async def scenario(client: AsyncClient) -> dict:
        response = await client.get("/api/v1/session", params={"token": TOKEN})
        again = await client.get("/api/v1/session", params={"token": TOKEN})
        stats = await client.get("/api/v1/stats")
        return {"response": response, "again": again, "stats": stats}

    result = api_env.scenario(scenario)
    response = result["response"]
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert "location" not in response.headers
    assert set(response.json()) == {"status", "csrf_token", "session_expires"}
    assert response.json()["status"] == "ok"
    assert response.json()["session_expires"] == "process"
    assert result["again"].status_code == 401
    assert result["again"].json()["code"] == "SESSION_INVALID"
    assert result["stats"].status_code == 200


def test_session_exchange_with_next_redirects_onto_the_shell(api_env) -> None:
    async def scenario(client: AsyncClient) -> dict:
        response = await client.get("/api/v1/session", params={"next": "/", "token": TOKEN})
        follow = await client.get("/")
        stats = await client.get("/api/v1/stats")
        return {"response": response, "follow": follow, "stats": stats}

    result = api_env.scenario(scenario)
    response = result["response"]
    assert response.status_code == 303
    assert response.headers["Location"] == "/"
    assert response.headers["Cache-Control"] == "no-store"

    set_cookie = " | ".join(
        value
        for key, value in response.headers.multi_items()
        if key.lower() == "set-cookie"
    )
    assert "ps_session=" in set_cookie and "HttpOnly" in set_cookie
    assert "ps_csrf=" in set_cookie
    assert "Path=/" in set_cookie

    assert result["follow"].status_code == 200
    assert result["stats"].status_code == 200


def test_an_unsafe_next_is_refused_without_burning_the_credential(api_env) -> None:
    async def scenario(client: AsyncClient) -> dict:
        refusals = []
        for target in _UNSAFE_TARGETS:
            refusals.append(
                await client.get(
                    "/api/v1/session", params={"next": target, "token": TOKEN}
                )
            )
        # The credential is still usable afterwards.
        accepted = await client.get(
            "/api/v1/session", params={"next": "/sources", "token": TOKEN}
        )
        return {"refusals": refusals, "accepted": accepted}

    result = api_env.scenario(scenario)
    for refusal in result["refusals"]:
        assert refusal.status_code == 422, refusal.text
        assert refusal.json()["code"] == "VALIDATION_ERROR"
        assert refusal.json()["details"] == {"param": "next"}
        assert "location" not in refusal.headers
    assert result["accepted"].status_code == 303
    assert result["accepted"].headers["Location"] == "/sources"
