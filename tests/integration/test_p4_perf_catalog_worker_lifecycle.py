"""Catalog-owned query/projection work drains before application lease release."""

import asyncio
import importlib
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from photo_search.api import catalog_reads
from photo_search.api.catalog_reads import run_catalog_projection, run_catalog_read
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import CATALOG_UNREADABLE, CatalogError
from photo_search.catalog.sqlite_io import connect_rw, open_readonly
from photo_search.runtime.data_root_lease import DataRootLeaseBusy, acquire_data_root_lease


def observe_worker_close(monkeypatch):
    closed = []

    class ObservedCatalog(Catalog):
        def close(self):
            super().close()
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                self.connection.execute("SELECT 1")
            closed.append(threading.get_ident())

    monkeypatch.setattr(catalog_reads, "Catalog", ObservedCatalog)
    return closed


def long_sql(reader, started):
    reader.connection.create_function("started", 0, lambda: started.set() or 0)
    return reader.connection.execute(
        "WITH RECURSIVE n(x) AS (SELECT started() UNION ALL SELECT x+1 FROM n "
        "WHERE x<100000000) SELECT SUM(x) FROM n"
    ).fetchone()[0]


def test_owner_close_rejects_new_work_without_stopping_another_owner(api_env, tmp_path):
    owner = api_env.service.catalog
    with Catalog.open(data_root=tmp_path / "other-owner") as other:
        assert asyncio.run(run_catalog_read(owner, lambda r: r.read_one("SELECT 11")[0])) == 11
        assert asyncio.run(run_catalog_read(other, lambda r: r.read_one("SELECT 22")[0])) == 22
        first_pool = owner._catalog_workers
        owner.close()
        owner.close()
        assert all(not thread.is_alive() for thread in first_pool._executor._threads)
        for entry in (run_catalog_read, run_catalog_projection):
            with pytest.raises(CatalogError) as error:
                asyncio.run(entry(owner, lambda _r: pytest.fail("closed owner admitted work")))
            assert error.value.code == CATALOG_UNREADABLE
        assert asyncio.run(run_catalog_read(other, lambda r: r.read_one("SELECT 23")[0])) == 23


def test_owner_closed_before_first_request_never_opens_worker(api_env, monkeypatch):
    owner = api_env.service.catalog
    owner.close()
    monkeypatch.setattr(catalog_reads, "open_readonly", lambda *_a, **_kw: pytest.fail("opened"))
    with pytest.raises(CatalogError):
        asyncio.run(run_catalog_read(owner, lambda _r: None))


@pytest.mark.parametrize("projection", [False, True])
def test_owner_close_cancels_queue_interrupts_sql_and_waits_for_connections(
    api_env, monkeypatch, projection,
):
    closed = observe_worker_close(monkeypatch)
    started, queued = threading.Event(), threading.Event()
    futures = []

    class RecordedExecutor(ThreadPoolExecutor):
        def submit(self, fn, /, *args, **kwargs):
            future = super().submit(fn, *args, **kwargs)
            futures.append(future)
            if len(futures) == 2:
                queued.set()
            return future

    monkeypatch.setattr(catalog_reads, "CATALOG_WORKERS", 1)
    monkeypatch.setattr(catalog_reads, "ThreadPoolExecutor", RecordedExecutor)

    def project(worker, signal):
        with worker.write() as connection:
            connection.execute(
                "INSERT INTO assets(sha256,byte_size,created_at,state) VALUES(?,1,1,'ACTIVE')",
                ("e" * 64,),
            )
            return long_sql(worker, signal)

    async def scenario():
        owner = api_env.service.catalog
        entry, work = (run_catalog_projection, project) if projection else (run_catalog_read, long_sql)
        running = asyncio.create_task(entry(owner, work, started))
        assert await asyncio.to_thread(started.wait, 3)
        waiting = asyncio.create_task(run_catalog_read(
            owner, lambda _r: pytest.fail("queued read started after owner close"),
        ))
        assert await asyncio.to_thread(queued.wait, 3)
        owner.close()
        assert closed and len(closed) == 1
        assert futures[1].cancelled()
        assert all(not thread.is_alive() for thread in owner._catalog_workers._executor._threads)
        result = await asyncio.gather(running, waiting, return_exceptions=True)
        assert isinstance(result[0], sqlite3.OperationalError)
        assert "interrupted" in str(result[0])
        assert isinstance(result[1], asyncio.CancelledError)
        connection = open_readonly(owner.db_path)
        try:
            assert connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
        finally:
            connection.close()

    asyncio.run(scenario())


def test_projection_uses_owner_configuration_and_transaction_boundaries(api_env, monkeypatch):
    closed = observe_worker_close(monkeypatch)
    owner = api_env.service.catalog
    owner.lease_seconds = 37
    caller_thread = threading.get_ident()
    version = owner.schema_version

    def prepare(worker):
        assert threading.get_ident() != caller_thread
        assert worker.now_ms() == owner.now_ms()
        assert worker.lease_seconds == 37
        assert worker.data_root == owner.data_root
        assert worker.schema_version == version
        assert worker.read_one("PRAGMA journal_mode")[0].lower() == "wal"
        assert worker.read_one("PRAGMA foreign_keys")[0] == 1
        with worker.write() as connection:
            connection.execute(
                "INSERT INTO assets(sha256,byte_size,created_at,state) VALUES(?,1,1,'ACTIVE')",
                ("c" * 64,),
            )
        return worker.read_one("SELECT COUNT(*) FROM assets")[0]

    assert asyncio.run(run_catalog_projection(owner, prepare)) == 1
    assert asyncio.run(run_catalog_read(owner, lambda r: r.read_one("SELECT COUNT(*) FROM assets")[0])) == 1

    def failed(worker):
        with worker.write() as connection:
            connection.execute("DELETE FROM assets")
            raise ValueError("projection failure")

    with pytest.raises(ValueError, match="projection failure"):
        asyncio.run(run_catalog_projection(owner, failed))
    assert owner.read_one("SELECT COUNT(*) FROM assets")[0] == 1
    assert len(closed) == 3


def test_cancelled_projection_rolls_back_and_releases_writer(api_env, monkeypatch):
    closed = observe_worker_close(monkeypatch)
    started = threading.Event()
    monkeypatch.setattr(catalog_reads, "CATALOG_WORKERS", 1)
    owner = api_env.service.catalog

    def prepare(worker):
        with worker.write() as connection:
            connection.execute(
                "INSERT INTO assets(sha256,byte_size,created_at,state) VALUES(?,1,1,'ACTIVE')",
                ("d" * 64,),
            )
            long_sql(worker, started)

    async def scenario():
        task = asyncio.create_task(run_catalog_projection(owner, prepare))
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # One worker orders this probe after rollback and connection close.
        assert await run_catalog_read(owner, lambda r: r.read_one("SELECT COUNT(*) FROM assets")[0]) == 0
        assert len(closed) == 2
        with owner.write() as connection:
            connection.execute("UPDATE metadata SET value=value")

    asyncio.run(scenario())


def test_projection_connection_never_creates_missing_database(tmp_path):
    target = tmp_path / "must-not-create" / "catalog.db"
    with pytest.raises(CatalogError) as error:
        connect_rw(target, create=False)
    assert error.value.code == CATALOG_UNREADABLE
    assert not target.parent.exists()


def test_existing_projection_connection_accepts_unicode_and_space_path(tmp_path):
    target = tmp_path / "合成 数据" / "catalog.db"
    connection = connect_rw(target)
    connection.execute("CREATE TABLE example(value TEXT)")
    connection.execute("INSERT INTO example VALUES('synthetic')")
    connection.close()
    connection = connect_rw(target, create=False)
    try:
        assert connection.execute("SELECT value FROM example").fetchone()[0] == "synthetic"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        connection.close()


def test_lifespan_drains_running_read_before_releasing_real_lease(api_env, monkeypatch):
    closed = observe_worker_close(monkeypatch)
    started = threading.Event()
    app_module = importlib.import_module("photo_search.app")
    api = api_env.app.state.api
    owned_catalog = api.service().catalog
    # Avoid unrelated thumbnail work; retain the actual app lifespan and OS lease.
    monkeypatch.setattr(api, "start_place_cover_warming", lambda: None)
    real_acquire = app_module.acquire_data_root_lease
    release_observations = []

    class ObservedLease:
        def __init__(self, lease):
            self.lease = lease

        def close(self):
            try:
                assert len(closed) == 1
                assert owned_catalog._closed
                with pytest.raises(DataRootLeaseBusy):
                    real_acquire(api.settings.data_root)
                release_observations.append("worker connection closed before lease release")
            finally:
                self.lease.close()

    monkeypatch.setattr(app_module, "acquire_data_root_lease", lambda root: ObservedLease(real_acquire(root)))

    async def scenario():
        async with api_env.app.router.lifespan_context(api_env.app):
            # ApiEnv has a separate writer connection for seeding fixtures.
            # Exercise the actual application-owned catalog closed by lifespan.
            running = asyncio.create_task(run_catalog_read(owned_catalog, long_sql, started))
            assert await asyncio.to_thread(started.wait, 3)
        result = await asyncio.gather(running, return_exceptions=True)
        assert isinstance(result[0], sqlite3.OperationalError)
        assert "interrupted" in str(result[0])
        with acquire_data_root_lease(api.settings.data_root):
            pass

    asyncio.run(scenario())
    assert len(release_observations) == 1


@pytest.mark.parametrize("transferred", [False, True])
def test_lifespan_retains_lease_until_candidate_worker_exits(api_env, monkeypatch, transferred):
    from photo_search.app import create_app
    from photo_search.models.embedding.candidate_task import _read_state

    api = api_env.app.state.api
    startup_lease = acquire_data_root_lease(api.settings.data_root) if transferred else None
    app = create_app(settings=api.settings, context=api, startup_lease=startup_lease) if transferred else api_env.app
    monkeypatch.setattr(api, "start_place_cover_warming", lambda: None)
    monkeypatch.setattr(api, "start_automatic_retention_cleanup", lambda: None)
    started = threading.Event()
    release = threading.Event()
    worker = None

    async def scenario():
        nonlocal worker
        try:
            async with app.router.lifespan_context(app):
                owned_catalog = api.service().catalog
                manager = api.embedding_candidate_manager()
                manager._save({"owner_id": manager._owner_id, "status": "RUNNING"})
                def blocked():
                    started.set()
                    release.wait(10)
                worker = threading.Thread(target=blocked, daemon=True)
                manager._thread = worker
                worker.start()
                assert started.wait(2)
                close = manager.close
                monkeypatch.setattr(manager, "close", lambda: close(timeout=0.01))
            assert worker.is_alive()
            state = _read_state(manager.root)
            assert state["status"] == "RUNNING"
            assert state["owner_id"] == manager._owner_id
            with pytest.raises(DataRootLeaseBusy):
                acquire_data_root_lease(api.settings.data_root)
            release.set()
            assert await asyncio.to_thread(api._shutdown_complete.wait, 3)
            with pytest.raises(sqlite3.ProgrammingError):
                owned_catalog.connection.execute("SELECT 1")
            # Lease release is performed by the app's separate shutdown waiter.
            deadline = time.monotonic() + 3
            while True:
                try:
                    lease = acquire_data_root_lease(api.settings.data_root)
                    break
                except DataRootLeaseBusy:
                    if time.monotonic() >= deadline:
                        raise
                    await asyncio.sleep(0.01)
            lease.close()
            assert _read_state(manager.root)["status"] == "INTERRUPTED"
        finally:
            release.set()
            if worker is not None:
                worker.join(timeout=3)

    asyncio.run(scenario())


def test_lifespan_retains_lease_until_refresh_worker_exits(api_env, monkeypatch):
    api = api_env.app.state.api
    monkeypatch.setattr(api, "start_place_cover_warming", lambda: None)
    monkeypatch.setattr(api, "start_automatic_retention_cleanup", lambda: None)
    entered = threading.Event()
    release = threading.Event()

    def blocked_refresh(_service, _status, _source_ids):
        entered.set()
        release.wait(10)

    api._refresher_executor = blocked_refresh

    async def scenario():
        try:
            async with api_env.app.router.lifespan_context(api_env.app):
                refresher = api.refresher()
                close = refresher.close
                monkeypatch.setattr(refresher, "close", lambda: close(timeout=0.01))
                refresher.start([1])
                assert entered.wait(2)
            assert refresher.is_running()
            with pytest.raises(DataRootLeaseBusy):
                acquire_data_root_lease(api.settings.data_root)
            release.set()
            assert await asyncio.to_thread(api._shutdown_complete.wait, 3)
            deadline = time.monotonic() + 3
            while True:
                try:
                    lease = acquire_data_root_lease(api.settings.data_root)
                    break
                except DataRootLeaseBusy:
                    if time.monotonic() >= deadline:
                        raise
                    await asyncio.sleep(0.01)
            lease.close()
            assert not refresher.is_running()
        finally:
            release.set()

    asyncio.run(scenario())


def test_lifespan_retains_lease_for_active_smart_refresh(api_env, monkeypatch):
    from concurrent.futures import Future

    api = api_env.app.state.api
    monkeypatch.setattr(api, "start_place_cover_warming", lambda: None)
    monkeypatch.setattr(api, "start_automatic_retention_cleanup", lambda: None)
    future = Future()
    future.set_running_or_notify_cancel()

    class SmartWorker:
        def __init__(self):
            self._shutdown_futures = []
        def close(self):
            self._shutdown_futures = [future]

    async def scenario():
        try:
            async with api_env.app.router.lifespan_context(api_env.app):
                api._photo_smart_refresh_runner = SmartWorker()
            with pytest.raises(DataRootLeaseBusy):
                acquire_data_root_lease(api.settings.data_root)
            future.set_result(None)
            assert await asyncio.to_thread(api._shutdown_complete.wait, 3)
            deadline = time.monotonic() + 3
            while True:
                try:
                    lease = acquire_data_root_lease(api.settings.data_root)
                    break
                except DataRootLeaseBusy:
                    if time.monotonic() >= deadline:
                        raise
                    await asyncio.sleep(0.01)
            lease.close()
        finally:
            if not future.done():
                future.set_result(None)

    asyncio.run(scenario())


def test_shutdown_keeps_lease_when_worker_cleanup_fails(api_env, monkeypatch):
    api = api_env.app.state.api
    monkeypatch.setattr(api, "start_place_cover_warming", lambda: None)
    monkeypatch.setattr(api, "start_automatic_retention_cleanup", lambda: None)

    class FailedWorker:
        _thread = None
        _cleanup_failed = True
        def close(self):
            pass

    async def scenario():
        async with api_env.app.router.lifespan_context(api_env.app):
            api._runner = FailedWorker()
        assert not api._shutdown_complete.is_set()
        with pytest.raises(DataRootLeaseBusy):
            acquire_data_root_lease(api.settings.data_root)
        # Test teardown only: the production path intentionally retains the lease.
        api._shutdown_complete.set()
        deadline = time.monotonic() + 3
        while True:
            try:
                lease = acquire_data_root_lease(api.settings.data_root)
                break
            except DataRootLeaseBusy:
                if time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(0.01)
        lease.close()

    asyncio.run(scenario())


def test_shutdown_keeps_data_root_lease_until_ocr_heartbeat_finishes(api_env, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from photo_search.ocr.lease_heartbeat import OcrLeaseHeartbeat

    api = api_env.app.state.api
    monkeypatch.setattr(api, "start_place_cover_warming", lambda: None)
    monkeypatch.setattr(api, "start_automatic_retention_cleanup", lambda: None)
    heartbeat_entered = threading.Event()
    release_heartbeat = threading.Event()
    drain_entered = threading.Event()
    real_write = Catalog.write

    @contextmanager
    def delayed_write(catalog):
        if threading.current_thread().name == "ocr-lease-heartbeat":
            heartbeat_entered.set()
            assert release_heartbeat.wait(10)
        with real_write(catalog) as connection:
            yield connection
    monkeypatch.setattr(Catalog, "write", delayed_write)

    def blocked_refresh(service, _status, _source_ids):
        heartbeat = OcrLeaseHeartbeat(service.catalog, lease_seconds=1)
        try:
            with heartbeat.track(SimpleNamespace(job_id=-1, lease_token="synthetic"), "synthetic"):
                drain_entered.set()
                assert heartbeat_entered.wait(3)
        finally:
            heartbeat.close()
    api._refresher_executor = blocked_refresh

    async def scenario():
        try:
            async with api_env.app.router.lifespan_context(api_env.app):
                refresher = api.refresher()
                close = refresher.close
                monkeypatch.setattr(refresher, "close", lambda: close(timeout=0.01))
                refresher.start([1])
                assert drain_entered.wait(2)
                assert heartbeat_entered.wait(3)
            assert refresher.is_running()
            with pytest.raises(DataRootLeaseBusy):
                acquire_data_root_lease(api.settings.data_root)
            release_heartbeat.set()
            assert await asyncio.to_thread(api._shutdown_complete.wait, 4)
            deadline = time.monotonic() + 3
            while True:
                try:
                    lease = acquire_data_root_lease(api.settings.data_root)
                    break
                except DataRootLeaseBusy:
                    if time.monotonic() >= deadline:
                        raise
                    await asyncio.sleep(0.01)
            lease.close()
            assert not refresher.is_running()
        finally:
            release_heartbeat.set()
    asyncio.run(scenario())
