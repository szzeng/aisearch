import asyncio

from httpx import ASGITransport, AsyncClient

from photo_search.app import app


def test_health_reports_process_readiness() -> None:
    async def get_health() -> tuple[int, dict[str, str]]:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/v1/health")
        return response.status_code, response.json()

    status_code, body = asyncio.run(get_health())

    assert status_code == 200
    assert body == {"status": "ok", "service": "photo-search"}