"""Catalog-owned query/projection work drains before application lease release."""

import asyncio
import importlib
import sqlite3
import threading
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
