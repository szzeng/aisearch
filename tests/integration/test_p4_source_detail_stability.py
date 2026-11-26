"""Isolated source-detail admission, snapshot and disconnect regression."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import sqlite3
import threading
import time
from collections import deque

import httpx
import pytest
import uvicorn

from integration.test_p4_02y_library_refresh import MANIFEST, RefreshEnv, StubPinnedAdapter
from photo_search.api import catalog_reads, routes
from photo_search.refresh import pipeline, progress


@pytest.mark.parametrize("built", [False, True])
@pytest.mark.parametrize("binding_available", [False, True])
@pytest.mark.parametrize("ocr_available", [False, True])
def test_empty_scope_preserves_stage_states_and_skips_stage_sql(
    api_env, monkeypatch, built, binding_available, ocr_available,
):
    source_id = api_env.register_source()["source_id"]
    catalog = api_env.service.catalog
    if built:
        progress.mark_source_built(catalog, source_id=source_id, value="test")
    binding = (
        {"space_id": "test", "processor_id": "test", "generation_id": "test"}
        if binding_available else None
    )
    available = {"face": False, "place": False, "ocr": ocr_available}
    with catalog.read_snapshot():
        legacy_semantic = progress.semantic_breakdown(
            catalog, source_id=source_id, space_id="test", processor_id="test",
        )
        legacy_ocr = progress.ocr_enrichment_counts(catalog, source_id=source_id)

        def unexpected(*_args, **_kwargs):
            pytest.fail("an empty scope ran a stage aggregate")

        for name in ("semantic_breakdown", "ocr_enrichment_counts", "enrichment_counts"):
            monkeypatch.setattr(progress, name, unexpected)
        payload = progress.source_progress(
            catalog, source_id=source_id, binding=binding, available=available,
        )
    semantic = payload["stages"]["semantic"]
    if binding_available:
        assert semantic["state"] == ("NO_IMAGES" if built else "UNBUILT")
        assert {key: semantic[key] for key in legacy_semantic} == legacy_semantic
    else:
        assert semantic["state"] == "UNAVAILABLE"
    assert payload["built"] is built
    assert payload["search_ready"] is (built and binding_available)
    assert payload["scan"] == {"files": 0, "present": 0, "missing": 0}
    assert payload["stages"]["taken_time"]["state"] == "NOT_STARTED"
    assert payload["stages"]["face"]["state"] == "UNAVAILABLE"
    ocr = payload["stages"]["ocr"]
    assert ocr["runtime_available"] is ocr_available
    assert ocr["state"] == ("NOT_STARTED" if ocr_available else "UNAVAILABLE")
    assert {key: ocr[key] for key in legacy_ocr} == legacy_ocr


def test_detail_admission_leaves_other_reads_capacity_and_reclaims_queue(api_env, monkeypatch):
    source_id = api_env.register_source()["source_id"]
    original = routes._source_detail_read
    entered, release = threading.Event(), threading.Event()

    def blocked(catalog, *args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(catalog, *args, **kwargs)

    monkeypatch.setattr(routes, "_source_detail_read", blocked)

    async def scenario(client):
        await api_env.authenticate(client)
        waiting = asyncio.Queue()
        admission = api_env.context.source_detail_reads

        class ObservableQueue(deque):
            def append(self, value):
                super().append(value)
                waiting.put_nowait(len(self))

        monkeypatch.setattr(admission, "_waiting", ObservableQueue())
        path = f"/api/v1/sources/{source_id}"
        active = asyncio.create_task(client.get(path))
        tasks = [active]
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            for count in (1, 2):
                tasks.append(asyncio.create_task(client.get(path)))
                assert await asyncio.wait_for(waiting.get(), 3) == count
            refused = await client.get(path)
            assert refused.status_code == 503
            assert refused.json()["code"] == "SOURCE_DETAIL_BUSY"
            assert refused.json()["details"]["retryable"] is True

            for other in ("/api/v1/assets", "/api/v1/persons/browse"):
                assert (await asyncio.wait_for(client.get(other), 3)).status_code == 200
            assert not active.done()
            tasks[1].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[1]
            replacement = asyncio.create_task(client.get(path))
            tasks.append(replacement)
            assert await asyncio.wait_for(waiting.get(), 3) == 2
        finally:
            release.set()
            completed = await asyncio.gather(*tasks, return_exceptions=True)
        assert [completed[i].status_code for i in (0, 2, 3)] == [200, 200, 200]
        assert not admission._waiting
        assert not admission._active

    api_env.scenario(scenario)


def test_http_disconnect_interrupts_actual_sql_and_closes_worker(api_env, monkeypatch, record_property):
    source_id = api_env.register_source()["source_id"]
    started, finished, ready = threading.Event(), threading.Event(), threading.Event()
    state = {}
    original_read = routes._source_detail_read
    original_submit = catalog_reads._CatalogWorkers.submit

    def held_sql(catalog, *args, **kwargs):
        if started.is_set():
            return original_read(catalog, *args, **kwargs)
        catalog.connection.create_function("mark_start", 0, lambda: started.set() or 0)
        catalog.connection.execute(
            "WITH RECURSIVE n(i) AS (SELECT mark_start() UNION ALL "
            "SELECT i+1 FROM n WHERE i < 100000000) SELECT SUM(i) FROM n"
        ).fetchone()
        pytest.fail("the disconnected request finished its long-running SQL")

    def observed_submit(pool, query, args, kwargs, *, projection):
        future, request = original_submit(pool, query, args, kwargs, projection=projection)
        if query is held_sql and not state:
            def observe_completion(done):
                state["closed"] = request.connection is None
                state["interrupted"] = done.exception()
                state["completed_at"] = time.perf_counter()
                finished.set()

            future.add_done_callback(observe_completion)
        return future, request

    monkeypatch.setattr(routes, "_source_detail_read", held_sql)
    monkeypatch.setattr(catalog_reads._CatalogWorkers, "submit", observed_submit)

    class SignalledServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            ready.set()

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = SignalledServer(uvicorn.Config(api_env.app, log_config=None, log_level="error",
                                                access_log=False, timeout_graceful_shutdown=2))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            assert ready.wait(5) and server.started

            async def scenario():
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=5) as client:
                    await api_env.authenticate(client)
                    path = f"/api/v1/sources/{source_id}"
                    _, writer = await asyncio.open_connection("127.0.0.1", port)
                    try:
                        writer.write(
                            f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                            f"Cookie: ps_session={api_env.session_id}\r\nConnection: close\r\n\r\n"
                            .encode("ascii")
                        )
                        await writer.drain()
                        assert await asyncio.to_thread(started.wait, 3)
                        disconnect_at = time.perf_counter()
                    finally:
                        writer.close()
                        with contextlib.suppress(ConnectionResetError):
                            await writer.wait_closed()
                    assert await asyncio.to_thread(finished.wait, 3)
                    assert isinstance(state["interrupted"], sqlite3.OperationalError)
                    assert "interrupted" in str(state["interrupted"])
                    assert state["closed"]
                    record_property("disconnect_to_future_ms",
                                    round((state["completed_at"] - disconnect_at) * 1000, 3))
                    assert (await client.get(path)).status_code == 200

            asyncio.run(scenario())
        finally:
            server.should_exit = True
            thread.join(6)
        assert not thread.is_alive()
        assert api_env.context._shutdown_complete.is_set()
        assert not api_env.context.data_root_lease_held


def test_detail_snapshot_keeps_counts_and_progress_together_while_scan_commits(api_env, monkeypatch):
    api_env.add_photo("synthetic.jpg")
    source_id = api_env.register_source()["source_id"]
    before_scope = threading.Event()
    allow_scope = threading.Event()
    original = progress.scoped_asset_count

    def pause_scope(catalog, *, source_id):
        if not before_scope.is_set():
            before_scope.set()
            assert allow_scope.wait(5)
        return original(catalog, source_id=source_id)

    def scan_on_its_own_connection():
        service = api_env.context._service_factory()
        try:
            service.run_scan(source_id)
        finally:
            service.close()

    monkeypatch.setattr(progress, "scoped_asset_count", pause_scope)

    async def scenario(client):
        await api_env.authenticate(client)
        path = f"/api/v1/sources/{source_id}"
        reading = asyncio.create_task(client.get(path))
        try:
            assert await asyncio.to_thread(before_scope.wait, 3)
            await asyncio.wait_for(asyncio.to_thread(scan_on_its_own_connection), 3)
        finally:
            allow_scope.set()
        before = (await reading).json()
        after = (await client.get(path)).json()
        assert before["counts"]["assets"] == before["progress"]["scan"]["present"] == 0
        assert before["progress"]["stages"]["taken_time"]["total"] == 0
        assert after["counts"]["assets"] == after["progress"]["scan"]["present"] == 1
        assert after["progress"]["stages"]["taken_time"]["total"] == 1

    api_env.scenario(scenario)


def test_close_rejects_queued_detail_and_drains_connection_before_lease_release(api_env, monkeypatch):
    source_id = api_env.register_source()["source_id"]
    started = threading.Event()
    interrupted = threading.Event()
    observed = []

    def read_on_shutdown(catalog, *args, **kwargs):
        observed.append(threading.get_ident())
        with catalog.read_snapshot():
            catalog.connection.create_function("mark_start", 0, lambda: started.set() or 0)
            try:
                return catalog.connection.execute(
                    "WITH RECURSIVE n(i) AS (SELECT mark_start() UNION ALL "
                    "SELECT i+1 FROM n WHERE i < 100000000) SELECT SUM(i) FROM n"
                ).fetchone()
            except sqlite3.OperationalError as error:
                if error.sqlite_errorcode == sqlite3.SQLITE_INTERRUPT:
                    interrupted.set()
                raise

    monkeypatch.setattr(routes, "_source_detail_read", read_on_shutdown)

    async def scenario(client):
        queued = asyncio.Event()
        admission = api_env.context.source_detail_reads

        class ObservableQueue(deque):
            def append(self, value):
                super().append(value)
                queued.set()

        monkeypatch.setattr(admission, "_waiting", ObservableQueue())
        async with api_env.app.router.lifespan_context(api_env.app):
            await api_env.authenticate(client)
            path = f"/api/v1/sources/{source_id}"
            active = asyncio.create_task(client.get(path))
            tasks = [active]
            try:
                assert await asyncio.to_thread(started.wait, 3)
                waiting = asyncio.create_task(client.get(path))
                tasks.append(waiting)
                await asyncio.wait_for(queued.wait(), 3)
            finally:
                assert api_env.context.close()
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), 3,
                )
            assert interrupted.is_set()
            for response in results:
                assert response.json()["code"] == "CATALOG_UNREADABLE"
            assert len(observed) == 1
            assert not admission._waiting and not admission._active
            assert api_env.context.data_root_lease_held
        assert not api_env.context.data_root_lease_held

    api_env.scenario(scenario)


@pytest.mark.parametrize("phase", ["_semantic_build_stage", "_face_stage", "_ocr_stage"])
def test_detail_can_read_at_a_background_build_stage(tmp_path, monkeypatch, phase):
    env = RefreshEnv.build(tmp_path, photos=2, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    reached = threading.Event()
    resume = threading.Event()
    stage = getattr(pipeline, phase)

    def paused_stage(*args, **kwargs):
        reached.set()
        if not resume.wait(5):
            raise TimeoutError("the detail test did not release the background stage")
        return stage(*args, **kwargs)

    monkeypatch.setattr(pipeline, phase, paused_stage)

    async def scenario(client):
        csrf = await env.authenticate(client)
        start = await client.post(
            "/api/v1/library/refresh", json={"mode": "build", "source_ids": [1]},
            headers=env.post_headers(csrf),
        )
        assert start.status_code == 202
        try:
            assert await asyncio.to_thread(reached.wait, 3)
            response = await asyncio.wait_for(client.get("/api/v1/sources/1"), 3)
            assert response.status_code == 200
            source = response.json()
            assert source["counts"]["assets"] == source["progress"]["scan"]["present"] == 2
            status = await client.get("/api/v1/library/refresh")
            assert status.json()["running"]
        finally:
            resume.set()

    try:
        env.scenario(scenario)
        assert env.context.refresher().wait_idle(timeout=10)
    finally:
        resume.set()
        env.context.close()
