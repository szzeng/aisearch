from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from photo_search.api.context import ApiContext
from photo_search.api.face_detail_runner import FaceDetailRunner
from photo_search.api.place_cover_warmer import PlaceCoverWarmer
from photo_search.api.refresher import LibraryRefresher
from photo_search.api.runner import ScanRunner
from photo_search.api.settings import ApiSettings
from photo_search.catalog.db import Catalog
from photo_search.runtime.resource_lifecycle import close_owned_resource


class FlakyService:
    def __init__(self) -> None:
        self.calls = 0
        self.owner_threads: list[int] = []

    def close(self) -> None:
        self.calls += 1
        self.owner_threads.append(threading.get_ident())
        if self.calls == 1:
            raise OSError("synthetic service close failure")


def test_owned_resource_without_close_needs_no_retry() -> None:
    failures: list[Exception] = []
    close_owned_resource(object(), on_failure=failures.append)
    assert failures == []


def test_refresher_retries_its_service_close_on_the_same_worker_thread() -> None:
    service = FlakyService()

    def execute(_service, status, _source_ids) -> None:
        status.finish(state="COMPLETE", elapsed_ms=0)

    refresher = LibraryRefresher(lambda: service, executor=execute)
    try:
        assert refresher.start([1], mode="build")["accepted"] is True
        assert refresher.wait_idle(timeout=3)
        assert service.calls == 2
        assert len(set(service.owner_threads)) == 1
        assert service.owner_threads[0] != threading.get_ident()
        assert refresher.close() is True
        assert refresher.close() is True
        assert service.calls == 2
    finally:
        refresher.close()


def test_scan_runner_close_timeout_keeps_work_alive_then_finishes_safely() -> None:
    entered = threading.Event()
    release = threading.Event()
    service = FlakyService()

    def execute(_service, _source_id) -> dict[str, str]:
        entered.set()
        assert release.wait(3)
        return {"state": "SUCCEEDED"}

    runner = ScanRunner(lambda: service, executor=execute)
    try:
        assert runner.submit(1)
        assert entered.wait(2)
        assert runner.close(timeout=0.01) is False
        assert service.calls == 0
        release.set()
        assert runner.close(timeout=3) is True
        assert service.calls == 2
        assert len(set(service.owner_threads)) == 1
        assert runner.close() is True
        assert service.calls == 2
    finally:
        release.set()
        runner.close()


def test_place_cover_warmer_retains_and_retries_a_failed_service_close(
    monkeypatch,
) -> None:
    service = FlakyService()

    from photo_search.api import place_cover_warmer

    monkeypatch.setattr(
        place_cover_warmer.place_albums,
        "albums",
        lambda _catalog, _root, *, kind: {"albums": []},
    )
    warmer = PlaceCoverWarmer(lambda: service)
    try:
        assert warmer.request()
        assert warmer.wait_idle(timeout=3)
        assert service.calls == 2
        assert len(set(service.owner_threads)) == 1
        assert warmer.close() is True
        assert warmer.close() is True
        assert service.calls == 2
    finally:
        warmer.close()


def test_face_detail_runner_retries_its_service_close_on_the_worker_thread() -> None:
    service = FlakyService()

    def execute(_service, _asset_id) -> None:
        return None

    runner = FaceDetailRunner(lambda: service, executor=execute)
    try:
        assert runner.submit(7) is True
        assert runner.wait_idle(timeout=3)
        assert runner.close(timeout=3) is True
        assert service.calls == 2
        assert len(set(service.owner_threads)) == 1
        assert service.owner_threads[0] != threading.get_ident()
        assert runner.close() is True
        assert service.calls == 2
    finally:
        runner.close()


def test_catalog_close_retains_failed_callback_and_does_not_double_close() -> None:
    class Connection:
        def __init__(self) -> None:
            self.calls = 0

        def close(self) -> None:
            self.calls += 1

    callback_calls = 0
    connection = Connection()
    catalog = Catalog(connection, db_path=Path("catalog.db"))

    def callback() -> None:
        nonlocal callback_calls
        callback_calls += 1
        if callback_calls == 1:
            raise OSError("synthetic callback failure")

    catalog.add_close_callback(callback)
    try:
        catalog.close()
    except OSError:
        pass
    else:
        raise AssertionError("the first close must report its callback failure")
    assert connection.calls == 0
    assert catalog._closed is False

    catalog.close()
    catalog.close()
    assert callback_calls == 2
    assert connection.calls == 1
    assert catalog._closed is True


def test_catalog_close_retries_an_unacknowledged_connection_close() -> None:
    class Connection:
        def __init__(self) -> None:
            self.calls = 0

        def close(self) -> None:
            self.calls += 1
            if self.calls == 1:
                raise OSError("synthetic connection close failure")

    connection = Connection()
    catalog = Catalog(connection, db_path=Path("catalog.db"))
    try:
        catalog.close()
    except OSError:
        pass
    else:
        raise AssertionError("the first close must report its connection failure")
    assert catalog._closed is False
    catalog.close()
    assert catalog._closed is True
    assert connection.calls == 2


def test_api_context_keeps_only_the_failed_worker_for_retry() -> None:
    retry_entered = threading.Event()
    retry_release = threading.Event()

    class Worker:
        _thread = None

        def __init__(self, failures: int, *, block_retry: bool = False) -> None:
            self.failures = failures
            self.block_retry = block_retry
            self.calls = 0
            self._cleanup_failed = False

        def close(self) -> bool:
            self.calls += 1
            if self.block_retry and self.calls == 2:
                retry_entered.set()
                assert retry_release.wait(3)
            if self.calls <= self.failures:
                self._cleanup_failed = True
                return False
            self._cleanup_failed = False
            return True

    flaky = Worker(failures=1, block_retry=True)
    healthy = Worker(failures=0)
    context = ApiContext(ApiSettings(data_root=Path(".tmp")))
    context._runner = flaky
    context._place_cover_warmer = healthy

    async def scenario() -> None:
        assert context.close() is False
        assert flaky.calls == 2
        assert healthy.calls == 1
        assert context._runner is flaky
        assert context._place_cover_warmer is None
        assert await asyncio.to_thread(retry_entered.wait, 3)
        assert context._runner is flaky
        retry_release.set()
        assert await asyncio.to_thread(context._shutdown_complete.wait, 3)

    asyncio.run(scenario())
    assert flaky.calls == 2
    assert context._runner is None
    assert context.close() is True
