"""P1-04 scenarios 1 and 9: session boundary, CSRF/Origin/Host guards, schema preflight.

Every fixture is a ``tmp_path`` fixture; no real photo library is read.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from conftest import ALLOWED_HOSTS, DEFAULT_SESSION_TOKEN
from httpx import ASGITransport, AsyncClient

from photo_search.api.settings import ApiSettings
from photo_search.app import create_app

TOKEN = DEFAULT_SESSION_TOKEN


def test_health_needs_no_session_and_keeps_its_body(api_env) -> None:
    async def scenario(client: AsyncClient) -> None:
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "photo-search"}
        assert response.headers["X-Request-ID"]

    api_env.scenario(scenario)


def test_business_endpoints_require_a_session(api_env) -> None:
    async def scenario(client: AsyncClient) -> None:
        for path in (
            "/api/v1/capabilities",
            "/api/v1/capabilities/page",
            "/api/v1/stats",
            "/api/v1/sources",
            "/api/v1/sources/summary",
            "/api/v1/assets",
            "/api/v1/jobs",
            "/api/v1/assets/1",
            "/api/v1/assets/1/thumbnail",
            "/api/v1/assets/1/original",
        ):
            response = await client.get(path)
            assert response.status_code == 401, (path, response.text)
            body = response.json()
            assert body["code"] == "SESSION_REQUIRED"
            assert body["request_id"] == response.headers["X-Request-ID"]
            assert "traceback" not in response.text.lower()

    api_env.scenario(scenario)


def test_unknown_route_is_hidden_without_a_session(api_env) -> None:
    async def scenario(client: AsyncClient) -> None:
        response = await client.get("/api/v1/does-not-exist")
        assert response.status_code == 401
        assert response.json()["code"] == "SESSION_REQUIRED"

    api_env.scenario(scenario)


def test_openapi_document_is_behind_the_session(api_env) -> None:
    async def scenario(client: AsyncClient) -> None:
        assert (await client.get("/api/v1/openapi.json")).status_code == 401
        await api_env.authenticate(client)
        response = await client.get("/api/v1/openapi.json")
        assert response.status_code == 200
        document = response.json()
        assert document["info"]["version"] == "0.5.0"
        page_capabilities = await client.get("/api/v1/capabilities/page")
        assert page_capabilities.status_code == 200
        assert page_capabilities.json()["app_version"] == document["info"]["version"]
        assert "/api/v1/assets/{asset_id}/original" in document["paths"]
        assert "AssetDetail" in document["components"]["schemas"]

    api_env.scenario(scenario)


def test_session_exchange_sets_cookies_and_unlocks_reads(api_env) -> None:
    async def scenario(client: AsyncClient) -> None:
        response = await client.get("/api/v1/session", params={"token": TOKEN})
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.headers["Cache-Control"] == "no-store"

        session_cookie = response.cookies.get("ps_session")
        csrf_cookie = response.cookies.get("ps_csrf")
        assert session_cookie and csrf_cookie
        session_header = " ".join(
            value
            for key, value in response.headers.multi_items()
            if key.lower() == "set-cookie" and value.startswith("ps_session=")
        )
        csrf_header = " ".join(
            value
            for key, value in response.headers.multi_items()
            if key.lower() == "set-cookie" and value.startswith("ps_csrf=")
        )
        assert "HttpOnly" in session_header
        assert "SameSite=strict" in session_header or "SameSite=Strict" in session_header
        assert "Path=/" in session_header
        assert "HttpOnly" not in csrf_header
        assert "Path=/" in csrf_header

        stats = await client.get("/api/v1/stats")
        assert stats.status_code == 200, stats.text
        assert stats.json()["counts"]["sources"] == 0

    api_env.scenario(scenario)


def test_bad_missing_and_reused_tokens_are_indistinguishable(api_env) -> None:
    async def scenario(client: AsyncClient) -> None:
        wrong = await client.get("/api/v1/session", params={"token": "not-the-token"})
        missing = await client.get("/api/v1/session")
        assert wrong.status_code == missing.status_code == 401
        assert wrong.json()["code"] == missing.json()["code"] == "SESSION_INVALID"
        assert "not-the-token" not in wrong.text

        assert (await client.get("/api/v1/session", params={"token": TOKEN})).status_code == 200
        reused = await client.get("/api/v1/session", params={"token": TOKEN})
        assert reused.status_code == 401
        assert reused.json()["code"] == "SESSION_INVALID"

    api_env.scenario(scenario)


def test_state_changing_requests_need_origin_and_csrf(api_env) -> None:
    async def scenario(client: AsyncClient) -> None:
        csrf = await api_env.authenticate(client)
        body = {"display_name": "lib", "root_path": str(api_env.library)}

        no_csrf = await client.post(
            "/api/v1/sources", json=body, headers={"Origin": "http://127.0.0.1"}
        )
        assert no_csrf.status_code == 403
        assert no_csrf.json()["code"] == "CSRF_INVALID"

        bad_csrf = await client.post(
            "/api/v1/sources",
            json=body,
            headers={"Origin": "http://127.0.0.1", "X-CSRF-Token": "wrong"},
        )
        assert bad_csrf.status_code == 403
        assert bad_csrf.json()["code"] == "CSRF_INVALID"

        no_origin = await client.post("/api/v1/sources", json=body, headers={"X-CSRF-Token": csrf})
        assert no_origin.status_code == 403
        assert no_origin.json()["code"] == "ORIGIN_NOT_ALLOWED"

        cross_origin = await client.post(
            "/api/v1/sources",
            json=body,
            headers={"Origin": "http://evil.example", "X-CSRF-Token": csrf},
        )
        assert cross_origin.status_code == 403
        assert cross_origin.json()["code"] == "ORIGIN_NOT_ALLOWED"

        wrong_port = await client.post(
            "/api/v1/sources",
            json=body,
            headers={"Origin": "http://127.0.0.1:9", "X-CSRF-Token": csrf},
        )
        assert wrong_port.status_code == 403

        accepted = await client.post(
            "/api/v1/sources",
            json=body,
            headers=api_env.post_headers(csrf),
        )
        assert accepted.status_code == 201, accepted.text

    api_env.scenario(scenario)


def test_host_header_must_be_a_loopback_spelling(api_env) -> None:
    async def scenario(client: AsyncClient) -> None:
        csrf = await api_env.authenticate(client)
        rejected = await client.get("/api/v1/stats", headers={"Host": "evil.example"})
        assert rejected.status_code == 403
        assert rejected.json()["code"] == "HOST_NOT_ALLOWED"

        rebound = await client.get(
            "/api/v1/assets",
            headers={"Host": "127.0.0.1", "X-CSRF-Token": csrf},
        )
        assert rebound.status_code == 200

    api_env.scenario(scenario)


def _too_new_database(data_root: Path) -> Path:
    """A database whose version is one past what this build supports."""
    from photo_search.catalog import schema

    future_version = int(schema.SUPPORTED_SCHEMA_VERSION) + 1
    db_dir = data_root / "catalog"
    db_dir.mkdir(parents=True, exist_ok=True)
    path = db_dir / "catalog.db"
    connection = sqlite3.connect(str(path))
    try:
        connection.executescript(
            "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            f"INSERT INTO metadata(key, value) VALUES('schema_version', '{future_version}');"
            "CREATE TABLE future_only (id INTEGER PRIMARY KEY);"
        )
        connection.commit()
    finally:
        connection.close()
    return path


def test_higher_schema_version_is_structured_and_has_no_write_side_effects(tmp_path) -> None:
    data_root = tmp_path / "PhotoSearchData"
    _too_new_database(data_root)
    before = sorted(path.name for path in (data_root / "catalog").iterdir())
    app = create_app(
        settings=ApiSettings(data_root=data_root, session_token=TOKEN, allowed_hosts=ALLOWED_HOSTS)
    )
    try:
        result = _probe_read_only_business_api(app, data_root)
    finally:
        app.state.api.close()

    assert result["codes"] == ["SCHEMA_TOO_NEW"] * len(result["paths"]), result
    assert result["posts"] == ["SCHEMA_TOO_NEW"] * len(result["post_paths"])
    assert result["status"] == [409] * len(result["paths"])
    assert result["health"] == 200
    assert result["session"] == 200
    assert sorted(path.name for path in (data_root / "catalog").iterdir()) == before
    assert not list((data_root / "catalog").glob("*-wal"))
    assert not list((data_root / "catalog").glob("*-shm"))


def _probe_read_only_business_api(app, data_root: Path) -> dict:
    async def scenario() -> dict:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            health = await client.get("/api/v1/health")
            session = await client.get("/api/v1/session", params={"token": TOKEN})
            csrf = session.json()["csrf_token"]
            paths = [
                "/api/v1/capabilities",
                "/api/v1/stats",
                "/api/v1/sources",
                "/api/v1/assets",
                "/api/v1/jobs",
            ]
            responses = [await client.get(path) for path in paths]
            post_paths = ["/api/v1/sources", "/api/v1/sources/1/scan"]
            posts = [
                await client.post(
                    post_paths[0],
                    json={"display_name": "x", "root_path": str(data_root / "library")},
                    headers={
                        "Origin": "http://127.0.0.1",
                        "X-CSRF-Token": csrf,
                    },
                ),
                await client.post(
                    post_paths[1], headers={"Origin": "http://127.0.0.1", "X-CSRF-Token": csrf}
                ),
            ]
            return {
                "paths": paths,
                "codes": [response.json()["code"] for response in responses],
                "status": [response.status_code for response in responses],
                "post_paths": post_paths,
                "posts": [response.json()["code"] for response in posts],
                "health": health.status_code,
                "session": session.status_code,
            }

    return asyncio.run(scenario())


def test_local_origin_and_host_must_use_the_actual_listening_port(api_env) -> None:
    """A separate localhost service must not share a session across ports."""
    app = api_env.app
    library = api_env.library

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://localhost:8000"
        ) as client:
            session = await client.get("/api/v1/session", params={"token": TOKEN})
            assert session.status_code == 200
            csrf = session.json()["csrf_token"]
            body = {"display_name": "local-port", "root_path": str(library)}

            for origin in ("http://localhost", "http://localhost:80", "https://localhost"):
                refused = await client.post(
                    "/api/v1/sources",
                    json=body,
                    headers={"Origin": origin, "X-CSRF-Token": csrf},
                )
                assert refused.status_code == 403, (origin, refused.text)
                assert refused.json()["code"] == "ORIGIN_NOT_ALLOWED"

            missing_host_port = await client.get("/api/v1/stats", headers={"Host": "localhost"})
            assert missing_host_port.status_code == 403
            assert missing_host_port.json()["code"] == "HOST_NOT_ALLOWED"

            accepted = await client.post(
                "/api/v1/sources",
                json=body,
                headers={"Origin": "http://localhost:8000", "X-CSRF-Token": csrf},
            )
            assert accepted.status_code == 201, accepted.text

    asyncio.run(scenario())
