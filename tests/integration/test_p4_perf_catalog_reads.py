"""Browse work owns RO connections and leaves the authenticated event loop free."""
import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from photo_search.api import catalog_reads
from photo_search.api.catalog_reads import run_catalog_read
from photo_search.catalog.db import Catalog


@pytest.fixture
def worker_closures(monkeypatch):
    """Prove closure on the connection's owner thread, not a thread-affinity error."""
    closed_threads = []

    class ObservedCatalog(Catalog):
        def close(self):
            super().close()
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                self.connection.execute("SELECT 1")
            closed_threads.append(threading.get_ident())

    monkeypatch.setattr(catalog_reads, "Catalog", ObservedCatalog)
    return closed_threads


def test_worker_observes_live_commits_rejects_writes_and_closes(api_env, worker_closures):
    catalog = api_env.service.catalog

    def read(reader):
        assert threading.get_ident() != owner
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.connection.execute("UPDATE metadata SET value=value")
        with reader.read_snapshot() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0])

    owner = threading.get_ident()
    assert asyncio.run(run_catalog_read(catalog, read)) == 0
    with catalog.write() as conn:
        conn.execute("INSERT INTO assets(sha256,byte_size,created_at,state) VALUES(?,1,1,'ACTIVE')", ("a" * 64,))
    assert asyncio.run(run_catalog_read(catalog, read)) == 1
    assert len(worker_closures) == 2
    assert all(thread != owner for thread in worker_closures)


def test_worker_closes_its_connection_when_query_raises(api_env, worker_closures):
    def fail(reader):
        assert reader.connection.execute("SELECT 1").fetchone()[0] == 1
        raise ValueError("query failed intentionally")

    with pytest.raises(ValueError, match="query failed intentionally"):
        asyncio.run(run_catalog_read(api_env.service.catalog, fail))
    assert len(worker_closures) == 1
    assert worker_closures[0] != threading.get_ident()


def test_worker_snapshot_remains_consistent_during_owner_commit(api_env, worker_closures):
    catalog = api_env.service.catalog
    started, release = threading.Event(), threading.Event()

    def read(reader):
        with reader.read_snapshot() as conn:
            before = int(conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0])
            started.set()
            if not release.wait(5):
                raise RuntimeError("owner did not release snapshot reader")
            after = int(conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0])
            return before, after

    async def scenario():
        task = asyncio.create_task(run_catalog_read(catalog, read))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            with catalog.write() as conn:
                conn.execute(
                    "INSERT INTO assets(sha256,byte_size,created_at,state) VALUES(?,1,1,'ACTIVE')",
                    ("b" * 64,),
                )
        finally:
            release.set()
            pair = await task
        assert pair == (0, 0)
        count = await run_catalog_read(
            catalog, lambda reader: int(reader.read_one("SELECT COUNT(*) FROM assets")[0])
        )
        assert count == 1

    asyncio.run(scenario())
    assert len(worker_closures) == 2


def test_cancelled_queued_read_never_runs_and_releases_capacity(api_env, monkeypatch,
                                                             worker_closures):
    started = [threading.Event(), threading.Event()]
    release, queued, cancelled_ran = threading.Event(), threading.Event(), threading.Event()
    submissions = []

    class ObservedExecutor(ThreadPoolExecutor):
        def submit(self, fn, /, *args, **kwargs):
            future = super().submit(fn, *args, **kwargs)
            submissions.append(future)
            if len(submissions) == 3:
                queued.set()
            return future

    def hold(reader, index):
        started[index].set()
        if not release.wait(5):
            raise RuntimeError("owner did not release busy reader")
        return reader.connection.execute("SELECT 1").fetchone()[0]

    def obsolete(reader):
        cancelled_ran.set()
        return reader.connection.execute("SELECT 1").fetchone()[0]

    async def scenario():
        first = [asyncio.create_task(run_catalog_read(api_env.service.catalog, hold, index))
                 for index in range(2)]
        try:
            for event in started:
                assert await asyncio.to_thread(event.wait, 3)
            cancelled = asyncio.create_task(run_catalog_read(api_env.service.catalog, obsolete))
            assert await asyncio.to_thread(queued.wait, 3)
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            assert submissions[2].cancelled()
        finally:
            release.set()
            results = await asyncio.gather(*first)
        assert results == [1, 1]
        assert not cancelled_ran.is_set()
        assert await run_catalog_read(
            api_env.service.catalog, lambda reader: reader.connection.execute("SELECT 42").fetchone()[0]
        ) == 42

    with ObservedExecutor(max_workers=2, thread_name_prefix="catalog-read-test") as executor:
        monkeypatch.setattr(catalog_reads, "ThreadPoolExecutor", lambda **_kwargs: executor)
        asyncio.run(scenario())
    assert len(worker_closures) == 3


def test_cancelled_running_sql_is_interrupted_and_connection_closed(api_env, monkeypatch,
                                                                   worker_closures):
    started, release = threading.Event(), threading.Event()
    errors = []

    def query(reader):
        def signal(number):
            if number == 1:
                started.set()
                if not release.wait(5):
                    raise RuntimeError("owner did not release SQL callback")
            return 0

        reader.connection.create_function("signal", 1, signal)
        try:
            return reader.connection.execute(
                "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<100000) "
                "SELECT SUM(x+signal(x)) FROM n"
            ).fetchone()[0]
        except sqlite3.OperationalError as error:
            errors.append(str(error))
            raise

    async def scenario():
        task = asyncio.create_task(run_catalog_read(api_env.service.catalog, query))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
        # A single-worker pool orders this probe after the cancelled worker's close.
        assert await run_catalog_read(
            api_env.service.catalog, lambda reader: reader.connection.execute("SELECT 42").fetchone()[0]
        ) == 42
        assert errors == ["interrupted"]

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="catalog-cancel-test") as executor:
        monkeypatch.setattr(catalog_reads, "ThreadPoolExecutor", lambda **_kwargs: executor)
        asyncio.run(scenario())
    assert len(worker_closures) == 2


def test_busy_timeline_does_not_block_authenticated_health(api_env, monkeypatch):
    from photo_search.api import timeline

    started, release = threading.Event(), threading.Event()
    original = timeline.timeline_periods

    def slow(reader, **kwargs):
        started.set()
        if not release.wait(5):
            raise RuntimeError("health request did not release worker")
        return original(reader, **kwargs)

    monkeypatch.setattr(timeline, "timeline_periods", slow)

    async def scenario(client):
        await api_env.authenticate(client)
        task = asyncio.create_task(client.get("/api/v1/timeline/periods"))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            response = await asyncio.wait_for(client.get("/api/v1/health"), timeout=2)
            assert response.status_code == 200
        finally:
            release.set()
            result = await task
        assert result.status_code == 200

    api_env.scenario(scenario)
