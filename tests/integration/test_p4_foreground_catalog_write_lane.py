"""Foreground writes share the bounded person lane without blocking the API loop."""
import asyncio
import threading
from contextlib import closing, suppress

import pytest

import photo_search.favorites as favorites
from photo_search.api import context as api_context
from photo_search.api.errors import ApiError
from photo_search.catalog import db as catalog_db
from photo_search.catalog import sqlite_io
from photo_search.catalog.errors import CATALOG_UNREADABLE, CatalogError
from photo_search.catalog.sqlite_io import connect_rw
from photo_search.runtime.data_root_lease import DataRootLeaseBusy, acquire_data_root_lease
from tests.integration.test_p3_person_api import seed_person
from tests.integration.test_p4_favorites_core import _seed


def _setup(api_env):
    root = api_env.tmp_path / "foreground-source"
    root.mkdir()
    source_id = api_env.register_source("foreground", root)["source_id"]
    _seed(api_env.service.catalog, int(source_id), 1, path="one.jpg")
    return seed_person(api_env, name="Before")[0]


def test_favorite_lock_keeps_api_responsive_and_uses_owned_connection(api_env, monkeypatch):
    _setup(api_env)
    entered, locked, release = (threading.Event() for _ in range(3))
    owners = []
    original = favorites.set_favorite

    def traced(catalog, *args, **kwargs):
        owners.append((threading.get_ident(), catalog, catalog.connection))
        catalog.connection.set_trace_callback(lambda sql: entered.set() if sql == "BEGIN IMMEDIATE" else None)
        try:
            return original(catalog, *args, **kwargs)
        finally:
            catalog.connection.set_trace_callback(None)

    def hold():
        with closing(connect_rw(api_env.service.catalog.db_path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            locked.set()
            assert release.wait(10)
            connection.rollback()

    monkeypatch.setattr(favorites, "set_favorite", traced)
    holder = threading.Thread(target=hold)

    async def scenario(client):
        headers = api_env.post_headers(await api_env.authenticate(client))
        # Create the worker's own service before the external write lock.
        warm = await client.delete("/api/v1/assets/1/favorite", headers=headers)
        assert warm.status_code == 200
        owners.clear()
        holder.start()
        assert await asyncio.to_thread(locked.wait, 3)
        request = asyncio.create_task(client.put("/api/v1/assets/1/favorite", headers=headers))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            assert not request.done()
            for path in ("/api/v1/health", "/api/v1/persons/browse", "/api/v1/assets", "/api/v1/sources/summary"):
                response = await asyncio.wait_for(client.get(path), 2)
                assert response.status_code == 200, (path, response.text)
            assert not request.done()
            thread_id, catalog, connection = owners[0]
            assert thread_id != threading.get_ident()
            assert catalog is not api_env.context.service().catalog
            assert connection is not api_env.context.service().catalog.connection
        finally:
            release.set()
            response = await asyncio.wait_for(request, 5)
        assert response.status_code == 200 and response.json()["changed"] is True

    async def with_lifespan(client):
        async with api_env.app.router.lifespan_context(api_env.app):
            assert api_env.context._service is not None
            await scenario(client)

    monkeypatch.setattr(api_env.context, "start_place_cover_warming", lambda: None)
    monkeypatch.setattr(api_env.context, "start_automatic_retention_cleanup", lambda: None)
    try:
        api_env.scenario(with_lifespan)
    finally:
        release.set()
        if holder.ident is not None:
            holder.join(10)


def test_mixed_queue_busy_codes_cancel_and_drain(api_env, monkeypatch):
    person_id = _setup(api_env)
    entered, release = threading.Event(), threading.Event()
    original = favorites.set_favorite
    completed = []

    def blocked(catalog, *args, **kwargs):
        entered.set()
        assert release.wait(10)
        result = original(catalog, *args, **kwargs)
        completed.append((kwargs["favorite"], result["changed"]))
        return result

    monkeypatch.setattr(favorites, "set_favorite", blocked)
    worker = api_env.context._person_write_worker

    async def scenario(client):
        headers = api_env.post_headers(await api_env.authenticate(client))
        active = asyncio.create_task(client.put("/api/v1/assets/1/favorite", headers=headers))
        tasks = [active]
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            for index in range(4):
                if index % 2:
                    task = asyncio.create_task(client.delete("/api/v1/assets/1/favorite", headers=headers))
                else:
                    task = asyncio.create_task(client.put(
                        f"/api/v1/persons/{person_id}/name", headers=headers,
                        json={"name": "After", "expected_revision": 1, "request_id": "mixed-replay"},
                    ))
                tasks.append(task)
                for _ in range(200):
                    with worker._condition:
                        count = len(worker._pending)
                    if count == index + 1:
                        break
                    await asyncio.sleep(.01)
                assert count == index + 1
            for path, body in (("/api/v1/assets/1/favorite", None),
                               (f"/api/v1/persons/{person_id}/name", {"name": "After", "expected_revision": 1, "request_id": "overflow"})):
                response = await client.put(path, headers=headers, json=body)
                assert response.status_code == 503 and response.headers["Retry-After"] == "1"
                assert response.json()["code"] == ("PERSON_WRITE_BUSY" if body else "CATALOG_WRITE_BUSY")
                assert response.json()["details"] == {"retryable": True}
            tasks[2].cancel()
            with suppress(asyncio.CancelledError):
                await tasks[2]
            worker.stop()
            assert worker._thread.is_alive()
        finally:
            release.set()
            responses = await asyncio.gather(*tasks, return_exceptions=True)
        assert all(response.status_code == 200 for i, response in enumerate(responses) if i != 2)
        assert await asyncio.to_thread(worker.close, timeout=5)
        assert api_env.service.catalog.read_one("SELECT COUNT(*) FROM person_operations WHERE request_id='mixed-replay'")[0] == 1
        assert completed == [(True, True), (False, True), (False, False)]
        assert api_env.service.catalog.read_one("SELECT COUNT(*) FROM asset_favorites WHERE asset_id=1")[0] == 0

    api_env.scenario(scenario)


@pytest.mark.parametrize("deferred", [False, True])
def test_mixed_writes_drain_on_shutdown_and_hold_lease(api_env, monkeypatch, deferred):
    person_id = _setup(api_env)
    api = api_env.context
    worker = api._person_write_worker
    if deferred:
        monkeypatch.setattr(api_context, "PERSON_WRITE_CLOSE_GRACE_SECONDS", 0)
    monkeypatch.setattr(api, "start_place_cover_warming", lambda: None)
    monkeypatch.setattr(api, "start_automatic_retention_cleanup", lambda: None)
    entered, release = threading.Event(), threading.Event()
    original = favorites.set_favorite

    def blocked(catalog, *args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(catalog, *args, **kwargs)

    monkeypatch.setattr(favorites, "set_favorite", blocked)

    async def scenario(client):
        headers = api_env.post_headers(await api_env.authenticate(client))
        lifespan = api_env.app.router.lifespan_context(api_env.app)
        await lifespan.__aenter__()
        active = asyncio.create_task(client.put("/api/v1/assets/1/favorite", headers=headers))
        pending = None
        shutdown = None
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            pending = asyncio.create_task(client.put(
                f"/api/v1/persons/{person_id}/name", headers=headers,
                json={"name": "Saved", "expected_revision": 1, "request_id": "shutdown-mixed"},
            ))
            for _ in range(200):
                with worker._condition:
                    queued = len(worker._pending)
                if queued:
                    break
                await asyncio.sleep(.01)
            assert queued == 1
            shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
            if deferred:
                await asyncio.wait_for(asyncio.shield(shutdown), 3)
                assert api._lease_release_deferred
            else:
                for _ in range(200):
                    if worker._stopping:
                        break
                    await asyncio.sleep(.01)
                assert worker._stopping and not shutdown.done()
            with pytest.raises(DataRootLeaseBusy):
                acquire_data_root_lease(api_env.data_root)
            assert worker._thread.is_alive()
        finally:
            release.set()
            if shutdown is None:
                await lifespan.__aexit__(None, None, None)
            else:
                await asyncio.wait_for(shutdown, 5)
            results = await asyncio.gather(active, *([pending] if pending else []))
        assert len(results) == 2 and all(result.status_code == 200 for result in results)
        assert await asyncio.to_thread(api._shutdown_complete.wait, 5)
        assert not worker._thread.is_alive()
        assert api_env.service.catalog.read_one("SELECT COUNT(*) FROM asset_favorites WHERE asset_id=1")[0] == 1
        assert api_env.service.catalog.read_one("SELECT COUNT(*) FROM person_operations WHERE request_id='shutdown-mixed'")[0] == 1
        with acquire_data_root_lease(api_env.data_root):
            pass

    api_env.scenario(scenario)


def test_photo_collection_write_routes_preserve_revision_and_instance(api_env):
    _setup(api_env)

    async def scenario(client):
        headers = api_env.post_headers(await api_env.authenticate(client))
        created = await client.post("/api/v1/photo-collections", headers=headers,
                                    json={"name": "Before", "create_request_id": "lane-create"})
        assert created.status_code == 200, created.text
        item = created.json()["collection"]
        url = f"/api/v1/photo-collections/{item['collection_id']}"
        token = item["instance_token"]
        renamed = await client.patch(url, headers=headers, json={"instance_token": token,
                                     "expected_revision": item["revision"], "name": "After"})
        assert renamed.status_code == 200, renamed.text
        revision = renamed.json()["collection"]["revision"]
        assert revision == item["revision"] + 1
        stale = await client.post(url + "/members/batch", headers=headers, json={
            "instance_token": token, "expected_revision": item["revision"], "action": "add", "asset_ids": [1]})
        assert stale.status_code == 409
        added = await client.post(url + "/members/batch", headers=headers, json={
            "instance_token": token, "expected_revision": revision, "action": "add", "asset_ids": [1]})
        assert added.status_code == 200 and added.json()["changed"] == 1, added.text
        revision = added.json()["collection"]["revision"]
        removed = await client.post(url + "/members/batch", headers=headers, json={
            "instance_token": token, "expected_revision": revision, "action": "remove", "asset_ids": [1]})
        assert removed.status_code == 200 and removed.json()["changed"] == 1, removed.text
        deleted = await client.request("DELETE", url, headers=headers, json={
            "instance_token": token, "expected_revision": removed.json()["collection"]["revision"]})
        assert deleted.status_code == 200 and deleted.json()["deleted"] is True, deleted.text

    api_env.scenario(scenario)


def test_error_responses_do_not_retry_deferred_recovery_under_writer_lock(api_env, monkeypatch):
    _setup(api_env)
    api = api_env.context
    manager = api.embedding_candidate_manager()
    recover_candidate = manager.recover_after_lock
    startup_lock = connect_rw(api_env.service.catalog.db_path)
    entered, locked, release, watchdog = (threading.Event() for _ in range(4))
    original = favorites.set_favorite
    source = str(api_env.tmp_path / "foreground-source")
    data_root = str(api_env.data_root)
    statements = []

    def defer_recovery():
        recover_candidate()
        startup_lock.execute("BEGIN IMMEDIATE")

    def traced(catalog, asset_id, **kwargs):
        if asset_id == -2:
            raise ApiError("TEST_REFUSAL", f"{source}/private.jpg", status=422, details={"path": data_root})
        if asset_id == -1:
            raise CatalogError(CATALOG_UNREADABLE, f"{source}/private.jpg", details={"path": data_root})
        catalog.connection.set_trace_callback(lambda sql: entered.set() if sql == "BEGIN IMMEDIATE" else None)
        try:
            return original(catalog, asset_id, **kwargs)
        finally:
            catalog.connection.set_trace_callback(None)

    def hold():
        with closing(connect_rw(api_env.service.catalog.db_path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            locked.set()
            if not release.wait(3):
                watchdog.set()
            connection.rollback()

    monkeypatch.setattr(manager, "recover_after_lock", defer_recovery)
    monkeypatch.setattr(api, "start_place_cover_warming", lambda: None)
    monkeypatch.setattr(api, "start_automatic_retention_cleanup", lambda: None)
    monkeypatch.setattr(favorites, "set_favorite", traced)
    holder = threading.Thread(target=hold)

    async def scenario(client):
        lifespan = api_env.app.router.lifespan_context(api_env.app)
        with monkeypatch.context() as startup_patch:
            startup_patch.setattr(sqlite_io, "BUSY_TIMEOUT_SECONDS", .01)
            startup_patch.setattr(catalog_db, "BEGIN_BUSY_RETRIES", 0)
            await lifespan.__aenter__()
        tasks = []
        try:
            assert api.data_root_lease_held and api._service is not None
            assert not api._smart_refresh_recovery_complete
            startup_lock.rollback()
            api._service.catalog.connection.execute("PRAGMA busy_timeout=2000")
            api._service.catalog.connection.set_trace_callback(statements.append)
            headers = api_env.post_headers(await api_env.authenticate(client))
            assert (await client.delete("/api/v1/assets/1/favorite", headers=headers)).status_code == 200
            holder.start()
            assert await asyncio.to_thread(locked.wait, 2)
            # Business ApiError and CatalogError must preserve both code and path redaction.
            invalid = await client.put("/api/v1/assets/-2/favorite", headers=headers)
            assert not watchdog.is_set()
            catalog_error = await client.put("/api/v1/assets/-1/favorite", headers=headers)
            assert invalid.status_code == 422 and invalid.json()["code"] == "TEST_REFUSAL"
            assert source not in invalid.text and data_root not in invalid.text
            assert "<source_root>" in invalid.text and "<data_root>" in invalid.text
            assert catalog_error.status_code == 500 and catalog_error.json()["code"] == CATALOG_UNREADABLE
            assert source not in catalog_error.text and data_root not in catalog_error.text
            assert "<source_root>" in catalog_error.text and "<data_root>" in catalog_error.text
            assert not watchdog.is_set()
            tasks.append(asyncio.create_task(client.put("/api/v1/assets/1/favorite", headers=headers)))
            assert await asyncio.to_thread(entered.wait, 2)
            worker = api._person_write_worker
            for count in range(1, 5):
                tasks.append(asyncio.create_task(client.put("/api/v1/assets/1/favorite", headers=headers)))
                for _ in range(200):
                    with worker._condition:
                        pending = len(worker._pending)
                    if pending == count:
                        break
                    await asyncio.sleep(.005)
                assert pending == count
            response = await client.put("/api/v1/assets/1/favorite", headers=headers)
            health = await client.get("/api/v1/health")
            assert response.status_code == 503 and response.json()["code"] == "CATALOG_WRITE_BUSY"
            assert response.headers["Retry-After"] == "1" and response.json()["details"] == {"retryable": True}
            assert health.status_code == 200 and not watchdog.is_set()
            assert "BEGIN IMMEDIATE" not in statements
            assert not api._smart_refresh_recovery_complete
        finally:
            release.set()
            if holder.ident is not None:
                holder.join(5)
            await asyncio.gather(*tasks, return_exceptions=True)
            if api._service is not None:
                api._service.catalog.connection.set_trace_callback(None)
            startup_lock.close()
            await lifespan.__aexit__(None, None, None)

    api_env.scenario(scenario)
