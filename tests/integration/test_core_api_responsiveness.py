"""Slow primary flows must leave the authenticated API event loop responsive."""
from __future__ import annotations

import asyncio
import sqlite3
import threading

import pytest

from photo_search.api import catalog_reads, routes
from photo_search.catalog.db import Catalog
from photo_search.persons import albums, collections
from photo_search.search.personal_search import PersonalSearchCoordinator


@pytest.mark.parametrize("case", ["text", "place", "image", "people", "collections", "capabilities", "stats"])
def test_primary_query_does_not_block_health(api_env, monkeypatch, case):
    started, release, watchdog_fired = threading.Event(), threading.Event(), threading.Event()
    if case == "text":
        target, name = routes, "search_request"
    elif case == "place":
        target, name = PersonalSearchCoordinator, "collect"
    elif case == "image":
        target, name = routes, "parse_multipart_form"
    elif case == "people":
        target, name = albums, "people_page"
    elif case == "collections":
        target, name = collections, "list_collections"
    elif case == "capabilities":
        target, name = routes, "catalog_info"
    else:
        target, name = routes.CatalogService, "media_report"
    original = getattr(target, name)

    def slow(*args, **kwargs):
        started.set()
        if not release.wait(2):
            watchdog_fired.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(target, name, slow)

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        if case == "text":
            call = client.post("/api/v1/search", json={"request_id": "responsive"}, headers=headers)
        elif case == "place":
            call = client.post("/api/v1/search/place-aware", json={"request_id": "responsive", "original_query": ""}, headers=headers)
        elif case == "image":
            call = client.post("/api/v1/search/image", files={"image": ("invalid.jpg", b"bad", "image/jpeg")}, headers=headers)
        else:
            path = {
                "people": "/api/v1/persons/browse",
                "collections": "/api/v1/persons/collections",
                "capabilities": "/api/v1/capabilities",
                "stats": "/api/v1/stats",
            }[case]
            call = client.get(path)
        task = asyncio.create_task(call)
        try:
            assert await asyncio.to_thread(started.wait, 4), "primary path did not run"
            health = await asyncio.wait_for(client.get("/api/v1/health"), 1)
            assert health.status_code == 200
            assert not watchdog_fired.is_set(), "primary query blocked the HTTP event loop"
            assert not task.done(), "health should complete while the primary query is still held"
        finally:
            release.set()
            response = await task
        assert response.status_code in (200, 422), response.text

    api_env.scenario(scenario)


@pytest.mark.parametrize("cancel_request", [False, True])
def test_search_shutdown_drains_model_and_connection_before_encoder_close(api_env, monkeypatch, cancel_request):
    entered, release, closing = threading.Event(), threading.Event(), threading.Event()
    order = []
    errors = []

    class ObservedCatalog(Catalog):
        def close(self):
            super().close()
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                self.connection.execute("SELECT 1")
            order.append("connection-closed")

    class Encoder:
        def close(self):
            order.append("encoder-closed")

    context = api_env.context
    owner = context.service().catalog
    context._encoder_factory = Encoder
    assert context.query_encoder() is not None
    monkeypatch.setattr(catalog_reads, "Catalog", ObservedCatalog)
    original_close = catalog_reads._CatalogWorkers.close

    def close_pool(pool):
        closing.set()
        original_close(pool)

    monkeypatch.setattr(catalog_reads._CatalogWorkers, "close", close_pool)

    def query(worker):
        assert worker.read_one("SELECT 1")[0] == 1
        entered.set()
        assert release.wait(3)
        order.append("model-finished")
        return 1

    def release_during_close():
        try:
            assert closing.wait(3)
            assert order == [], "shutdown closed a resource while encoding was active"
        except BaseException as error:
            errors.append(error)
        finally:
            release.set()

    async def scenario():
        running = asyncio.create_task(catalog_reads.run_catalog_search(owner, query))
        assert await asyncio.to_thread(entered.wait, 3)
        if cancel_request:
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
        releaser = threading.Thread(target=release_during_close)
        releaser.start()
        try:
            # Catalog stays on its creating thread; shutdown must drain its worker.
            context.close()
        finally:
            release.set()
            releaser.join(timeout=3)
        if not cancel_request:
            assert await running == 1

    asyncio.run(scenario())
    assert errors == []
    assert order == ["model-finished", "connection-closed", "encoder-closed"]
    assert all(not thread.is_alive() for thread in owner._catalog_workers._executor._threads)
