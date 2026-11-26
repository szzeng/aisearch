"""Person write admission, SQLite contention and shutdown through the API."""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing, suppress

import pytest

from photo_search.api import context as api_context
from photo_search.api.person_writes import MAX_PENDING_PERSON_WRITES
from photo_search.api.settings import ApiSettings
from photo_search.app import create_app
from photo_search.catalog.sqlite_io import connect_rw
from photo_search.ingestion.service import CatalogService
from photo_search.persons.service import PersonService
from photo_search.runtime.data_root_lease import DataRootLeaseBusy, acquire_data_root_lease
from tests.integration.test_p3_person_api import ensure_source_root, seed_person


def test_sqlite_writer_wait_does_not_block_health_or_reads(api_env, monkeypatch):
    ensure_source_root(api_env)
    person_id, _ = seed_person(api_env, name="Original")
    db_path = api_env.service.catalog.db_path
    locked, release, watchdog, begin = (threading.Event() for _ in range(4))
    owners = []
    rename = PersonService.rename

    def traced_rename(service, **parameters):
        if parameters["request_id"] != "waiting-rename":
            return rename(service, **parameters)
        owners.append((threading.get_ident(), service.catalog, service.catalog.connection))

        def trace(sql):
            if sql == "BEGIN IMMEDIATE":
                begin.set()

        connection = service.catalog.connection
        connection.set_trace_callback(trace)
        try:
            return rename(service, **parameters)
        finally:
            connection.set_trace_callback(None)

    def hold_writer():
        with closing(connect_rw(db_path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            locked.set()
            if not release.wait(6):
                watchdog.set()
            connection.rollback()

    monkeypatch.setattr(PersonService, "rename", traced_rename)
    holder = threading.Thread(target=hold_writer, daemon=True)

    async def scenario(client):
        api_thread = threading.get_ident()
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        person_url = f"/api/v1/persons/{person_id}"
        name_url = f"{person_url}/name"
        before = await client.get(person_url)
        assert before.json()["name"] == "Original"
        api_catalog = api_env.context.service().catalog

        # Prepare the worker before locking SQLite so the trace observes the operation's
        # BEGIN, independently of the initial CatalogService.open work.
        warm = {"name": "Original", "expected_revision": 1, "request_id": "warm-person-writer"}
        assert (await client.put(name_url, json=warm, headers=headers)).status_code == 200

        holder.start()
        assert await asyncio.to_thread(locked.wait, 3)
        body = {"name": "Renamed", "expected_revision": 1, "request_id": "waiting-rename"}
        write = asyncio.create_task(client.put(name_url, json=body, headers=headers))
        try:
            assert await asyncio.to_thread(begin.wait, 3)
            health = await asyncio.wait_for(client.get("/api/v1/health"), 2)
            assert health.status_code == 200 and health.json()["status"] == "ok"
            assert not watchdog.is_set()
            assert not write.done()
            person = await asyncio.wait_for(client.get(person_url), 2)
            assert person.status_code == 200 and person.json()["name"] == "Original"
            writer_thread, writer_catalog, writer_connection = owners[0]
            assert writer_thread != api_thread
            assert writer_catalog is not api_catalog
            assert writer_connection is not api_catalog.connection
        finally:
            release.set()
            response = await asyncio.wait_for(write, 4)
        assert response.status_code == 200, response.text
        operation = response.json()
        assert operation["changed"]
        replay = await client.put(name_url, json=body, headers=headers)
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"]
        assert replay.json()["operation_id"] == operation["operation_id"]
        assert (await client.get(person_url)).json()["name"] == "Renamed"

    try:
        api_env.scenario(scenario)
    finally:
        release.set()
        if holder.ident is not None:
            holder.join(7)
    row = api_env.service.catalog.read_one(
        "SELECT COUNT(*) FROM person_operations WHERE request_id = 'waiting-rename'"
    )
    assert row[0] == 1


@pytest.mark.parametrize("defer_cleanup", [False, True], ids=["within-grace", "after-grace"])
def test_shutdown_drains_cancelled_waiter_and_releases_lease_after_owner_close(
    api_env, monkeypatch, defer_cleanup,
):
    ensure_source_root(api_env)
    active_id, _ = seed_person(api_env, name="Active")
    queued_id, _ = seed_person(api_env, name="Queued")
    api = api_env.context
    worker = api._person_write_worker
    app = api_env.app
    if defer_cleanup:
        monkeypatch.setattr(api_context, "PERSON_WRITE_CLOSE_GRACE_SECONDS", 0)
    monkeypatch.setattr(api, "start_place_cover_warming", lambda: None)
    monkeypatch.setattr(api, "start_automatic_retention_cleanup", lambda: None)
    entered, release = threading.Event(), threading.Event()
    close_failed, allow_close = threading.Event(), threading.Event()
    operations, opened, closed, close_attempts = [], [], [], []
    rename, close = PersonService.rename, CatalogService.close
    factory = worker._service_factory

    def open_worker_catalog():
        service = factory()
        opened.append((threading.get_ident(), service.catalog))
        return service

    def blocked_rename(service, **parameters):
        operations.append((threading.get_ident(), service.catalog, parameters["request_id"]))
        if parameters["request_id"] == "active-person-write":
            entered.set()
            assert release.wait(8)
        return rename(service, **parameters)

    def close_catalog(service):
        if opened and service.catalog is opened[0][1]:
            close_attempts.append(threading.get_ident())
            if len(close_attempts) == 1:
                close_failed.set()
                raise OSError("injected person catalog close failure")
            assert allow_close.wait(8)
            close(service)
            with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
                service.catalog.connection.execute("SELECT 1")
            closed.append((threading.get_ident(), service.catalog))
        else:
            close(service)

    monkeypatch.setattr(worker, "_service_factory", open_worker_catalog)
    monkeypatch.setattr(PersonService, "rename", blocked_rename)
    monkeypatch.setattr(CatalogService, "close", close_catalog)

    async def scenario(client):
        admitted = asyncio.Queue()
        stopping = asyncio.Event()
        submit, stop = worker.submit, worker.stop

        def observed_submit(operation, **parameters):
            accepted = submit(operation, **parameters)
            admitted.put_nowait(parameters["request_id"])
            return accepted

        def observed_stop():
            thread = stop()
            stopping.set()
            return thread

        monkeypatch.setattr(worker, "submit", observed_submit)
        monkeypatch.setattr(worker, "stop", observed_stop)
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)

        async def write(person_id, request_id, name):
            return await client.put(
                f"/api/v1/persons/{person_id}/name", headers=headers,
                json={"request_id": request_id, "name": name, "expected_revision": 1},
            )

        requests = []
        shutdown = None
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        try:
            active = asyncio.create_task(write(active_id, "active-person-write", "Active saved"))
            requests.append(active)
            assert await asyncio.wait_for(admitted.get(), 3) == "active-person-write"
            assert await asyncio.to_thread(entered.wait, 3)
            for _ in range(MAX_PENDING_PERSON_WRITES):
                queued = asyncio.create_task(write(queued_id, "queued-person-write", "Queued saved"))
                requests.append(queued)
                assert await asyncio.wait_for(admitted.get(), 3) == "queued-person-write"

            overflow = await write(queued_id, "rejected-person-write", "Rejected")
            assert overflow.status_code == 503 and overflow.headers["Retry-After"] == "1"
            assert overflow.json()["code"] == "PERSON_WRITE_BUSY"
            assert overflow.json()["details"] == {"retryable": True}

            # Cancel the only request for this intent that was admitted before its replays.
            # Other queued callers still prove that its operation committed first.
            requests[1].cancel()
            with pytest.raises(asyncio.CancelledError):
                await requests[1]

            shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
            await asyncio.wait_for(stopping.wait(), 3)
            if defer_cleanup:
                await asyncio.wait_for(asyncio.shield(shutdown), 3)
                assert api._lease_release_deferred
            else:
                assert not shutdown.done()
            assert not closed
            with pytest.raises(DataRootLeaseBusy):
                acquire_data_root_lease(api_env.data_root)
            release.set()
            assert await asyncio.to_thread(close_failed.wait, 3)
            assert not api._shutdown_complete.is_set()
            with pytest.raises(DataRootLeaseBusy):
                acquire_data_root_lease(api_env.data_root)
        finally:
            release.set()
            allow_close.set()
            if shutdown is None:
                await lifespan.__aexit__(None, None, None)
            else:
                await asyncio.wait_for(shutdown, 5)
            responses = await asyncio.gather(*requests, return_exceptions=True)
            if defer_cleanup:
                assert await asyncio.to_thread(api._shutdown_complete.wait, 3)

        assert responses[0].status_code == 200, responses[0].text
        for response in responses[2:]:
            assert response.status_code == 200, response.text
            assert response.json()["replayed"]
        assert [(thread, catalog) for thread, catalog, _ in operations] == opened * (
            1 + MAX_PENDING_PERSON_WRITES
        )
        assert [request_id for _, _, request_id in operations] == ["active-person-write"] + [
            "queued-person-write"
        ] * MAX_PENDING_PERSON_WRITES
        assert closed == opened
        assert close_attempts == [opened[0][0]] * 2
        assert worker._thread is not None and not worker._thread.is_alive()
        deadline = asyncio.get_running_loop().time() + 3
        while True:
            try:
                acquire_data_root_lease(api_env.data_root).close()
                break
            except DataRootLeaseBusy:
                if asyncio.get_running_loop().time() >= deadline:
                    raise
                await asyncio.sleep(0.01)
        assert not api.data_root_lease_held
        stopped = await write(queued_id, "after-shutdown", "Rejected")
        assert stopped.status_code == 503 and stopped.json()["code"] == "PERSON_WRITE_BUSY"

    api_env.scenario(scenario)
    rows = api_env.service.catalog.read_all("SELECT request_id FROM person_operations ORDER BY request_id")
    assert [row["request_id"] for row in rows] == ["active-person-write", "queued-person-write"]


def _defer_write_in_exiting_process(data_root, person_id):
    """Run in a child interpreter so the server loop and main thread really return."""
    api_context.PERSON_WRITE_CLOSE_GRACE_SECONDS = 0
    app = create_app(settings=ApiSettings.create(data_root=data_root))
    api = app.state.api
    entered, release = threading.Event(), threading.Event()
    rename = PersonService.rename

    def blocked_rename(service, **parameters):
        entered.set()
        assert release.wait(8)
        return rename(service, **parameters)

    PersonService.rename = blocked_rename

    async def run_lifespan():
        async with app.router.lifespan_context(app):
            write = asyncio.create_task(api.run_person_write(
                "rename", person_id=person_id, name="Saved after loop", expected_revision=1,
                request_id="after-owner-loop",
            ))
            assert await asyncio.to_thread(entered.wait, 3)
            write.cancel()
            with suppress(asyncio.CancelledError):
                await write
        assert api._lease_release_deferred

    asyncio.run(run_lifespan())
    assert api._person_write_worker._thread.is_alive()
    assert not api._person_write_worker._thread.daemon
    with pytest.raises(DataRootLeaseBusy):
        acquire_data_root_lease(data_root)
    # The worker is still blocked when main returns. Interpreter shutdown must keep it alive.
    unblock = threading.Timer(0.1, release.set)
    unblock.daemon = True
    unblock.start()


def test_deferred_accepted_write_survives_server_loop_and_process_shutdown(api_env):
    ensure_source_root(api_env)
    person_id, _ = seed_person(api_env, name="Waiting for shutdown")
    result = subprocess.run(
        [
            sys.executable, "-c",
            "from tests.integration.test_p4_person_write_worker import _defer_write_in_exiting_process; "
            "import sys; _defer_write_in_exiting_process(sys.argv[1], int(sys.argv[2]))",
            str(api_env.data_root), str(person_id),
        ],
        capture_output=True, text=True, encoding="utf-8", timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr
    with acquire_data_root_lease(api_env.data_root):
        person = api_env.service.catalog.read_one("SELECT name FROM persons WHERE person_id=?", (person_id,))
        operation = api_env.service.catalog.read_one(
            "SELECT COUNT(*) FROM person_operations WHERE request_id = 'after-owner-loop'"
        )
        assert person["name"] == "Saved after loop"
        assert operation[0] == 1
