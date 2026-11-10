from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from photo_search.api.runner import (
    BUSY_AUTO_RECOVERY_ATTEMPTS,
    BUSY_AUTO_RECOVERY_WAIT_LIMIT_SECONDS,
    ScanRunner,
)
from photo_search.catalog.errors import CATALOG_UNREADABLE, CatalogError


def _begin_busy() -> CatalogError:
    return CatalogError(
        CATALOG_UNREADABLE,
        "synthetic BEGIN contention",
        details={
            "phase": "begin_immediate",
            "sqlite_errorcode": sqlite3.SQLITE_BUSY,
        },
    )


def test_scan_runner_spends_one_cumulative_recovery_wait_budget() -> None:
    runner = ScanRunner(lambda: object(), executor=lambda _service, _source: None)
    execute_calls = 0
    remaining_budgets: list[float] = []

    def always_busy(_source_id: int):
        nonlocal execute_calls
        execute_calls += 1
        raise _begin_busy()

    def consume_wait(source_id: int, remaining: float) -> float | None:
        assert source_id == 9
        remaining_budgets.append(remaining)
        return 12.0 if remaining >= 12.0 else None

    runner._execute = always_busy
    runner._wait_for_scan_retry = consume_wait
    try:
        assert not runner._run_one(9)
    finally:
        runner.close()

    assert execute_calls == 3
    assert remaining_budgets == [30.0, 18.0, 6.0]
    assert BUSY_AUTO_RECOVERY_WAIT_LIMIT_SECONDS == 30.0


def test_scan_runner_stops_after_three_automatic_recoveries() -> None:
    runner = ScanRunner(lambda: object(), executor=lambda _service, _source: None)
    execute_calls = 0
    wait_calls = 0

    def busy_then_success(_source_id: int):
        nonlocal execute_calls
        execute_calls += 1
        if execute_calls <= BUSY_AUTO_RECOVERY_ATTEMPTS:
            raise _begin_busy()
        return {"state": "SUCCEEDED"}

    def no_wait(source_id: int, remaining: float) -> float:
        nonlocal wait_calls
        assert source_id == 11 and remaining > 0
        wait_calls += 1
        return 0.0

    runner._execute = busy_then_success
    runner._wait_for_scan_retry = no_wait
    try:
        assert runner._run_one(11)
    finally:
        runner.close()

    assert execute_calls == BUSY_AUTO_RECOVERY_ATTEMPTS + 1
    assert wait_calls == BUSY_AUTO_RECOVERY_ATTEMPTS


def test_scan_runner_does_not_retry_a_live_lease_but_allows_expired_lease(
    monkeypatch,
) -> None:
    from photo_search.catalog import store
    from photo_search.jobs import queue as jobs

    now_ms = 50_000
    service = SimpleNamespace(
        catalog=SimpleNamespace(connection=object(), now_ms=lambda: now_ms)
    )
    runner = ScanRunner(lambda: object(), executor=lambda _service, _source: None)
    runner._service_for_thread = lambda: service
    monkeypatch.setattr(store, "find_active_scan", lambda _connection, _source_id: {"scan_id": 3})
    monkeypatch.setattr(store, "scan_job_id", lambda _connection, *, scan_id: 4)
    job = SimpleNamespace(state=jobs.STATE_RUNNING, lease_expires_at=now_ms + 1000)
    monkeypatch.setattr(jobs, "get_job", lambda _connection, _job_id: job)
    monkeypatch.setattr(
        runner._condition,
        "wait",
        lambda _timeout: (_ for _ in ()).throw(AssertionError("must not wait on a live lease")),
    )
    try:
        assert runner._wait_for_scan_retry(3, 30.0) is None
        monkeypatch.setattr(store, "scan_job_id", lambda _connection, *, scan_id: None)
        assert runner._wait_for_scan_retry(3, 30.0) is None
        monkeypatch.setattr(store, "scan_job_id", lambda _connection, *, scan_id: 4)
        job.lease_expires_at = now_ms - 1
        elapsed = runner._wait_for_scan_retry(3, 30.0)
        assert elapsed is not None and elapsed < 0.01
    finally:
        runner.close()


def test_scan_runner_close_cancels_contention_wait_before_next_attempt() -> None:
    runner = ScanRunner(lambda: object(), executor=lambda _service, _source: None)
    execute_calls = 0

    def busy(_source_id: int):
        nonlocal execute_calls
        execute_calls += 1
        raise _begin_busy()

    def close_during_wait(_source_id: int, _remaining: float) -> None:
        with runner._condition:
            runner._stop = True
        return 0.0

    runner._execute = busy
    runner._wait_for_scan_retry = close_during_wait
    try:
        assert not runner._run_one(5)
    finally:
        runner.close()
    assert execute_calls == 1
