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
    async def scenario(client: AsyncClient) -> dict:
        shell = await client.get("/")
        script = await client.get("/web/app.js")
        style = await client.get("/web/styles.css")
        missing = await client.get("/web/does-not-exist.js")
        traversal = await client.get("/web/%2e%2e/app.js")
        deep_traversal = await client.get("/web/app.js/../../index.html")
        stats = await client.get("/api/v1/stats")
        return {
            "shell": shell,
            "script": script,
            "style": style,
            "missing": missing,
            "traversal": traversal,
            "deep_traversal": deep_traversal,
            "stats": stats,
        }

    result = api_env.scenario(scenario)

    shell = result["shell"]
    assert shell.status_code == 200, shell.text
    assert shell.headers["Content-Type"].startswith("text/html")
    assert "no-cache" in shell.headers["Cache-Control"]
    assert shell.headers["X-Request-ID"]
    assert "http://" not in shell.text and "https://" not in shell.text
    assert str(api_env.library) not in shell.text
    assert str(api_env.data_root) not in shell.text
    assert TOKEN not in shell.text

    assert result["script"].status_code == 200
    assert "javascript" in result["script"].headers["Content-Type"]
    assert "max-age" in result["script"].headers["Cache-Control"]
    assert result["style"].status_code == 200
    assert result["style"].headers["Content-Type"].startswith("text/css")

    assert result["missing"].status_code == 404
    assert result["missing"].json()["code"] == "WEB_ASSET_NOT_FOUND"
    assert result["traversal"].status_code == 404
    assert result["deep_traversal"].status_code == 404

    # The shell is public, the data is not: the boundary is untouched by the new routes.
    assert result["stats"].status_code == 401
    assert result["stats"].json()["code"] == "SESSION_REQUIRED"


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
