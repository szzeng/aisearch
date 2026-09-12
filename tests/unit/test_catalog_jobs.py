"""P1-01: job identity, state machine, leases and late-submit rules (ADR-0002 §6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import (
    INVALID_STATE_TRANSITION,
    LEASE_CONFLICT,
    PROCESSOR_MISMATCH,
    RETRY_EXHAUSTED,
    STALE_INPUT_REVISION,
    STALE_LEASE,
    CatalogError,
)
from photo_search.jobs import queue as jobs

SUBJECT = {
    "subject_type": "asset",
    "subject_id": "7",
    "input_revision": "rev-1",
    "task_type": "embed_image",
    "processor_id": "chinese-clip-vit-b-16@36e679e65c2a",
}


@pytest.fixture()
def catalog(tmp_path: Path) -> Catalog:
    instance = Catalog.open(tmp_path / "data" / "catalog" / "catalog.db")
    try:
        yield instance
    finally:
        instance.close()


def _enqueue(catalog: Catalog, *, now_ms: int = 1_000, **overrides: object) -> int:
    parameters = {**SUBJECT, **overrides}
    with catalog.write() as connection:
        return jobs.enqueue(connection, now_ms=now_ms, **parameters)


def _finish(catalog: Catalog, job: jobs.Job, state: str, *, now_ms: int, **overrides: object) -> jobs.Job:
    parameters = {
        "job_id": job.job_id,
        "lease_token": job.lease_token or "",
        "state": state,
        "input_revision": job.input_revision,
        "processor_id": job.processor_id,
        "now_ms": now_ms,
    }
    parameters.update(overrides)
    with catalog.write() as connection:
        return jobs.finish(connection, **parameters)


def test_claim_starts_the_job_with_a_fresh_lease(catalog: Catalog) -> None:
    job_id = _enqueue(catalog)

    with catalog.write() as connection:
        claimed = jobs.claim_next(connection, worker_id="worker-1", now_ms=2_000, lease_seconds=60)

    assert claimed is not None
    assert claimed.job_id == job_id
    assert claimed.state == jobs.STATE_RUNNING
    assert claimed.worker_id == "worker-1"
    assert claimed.lease_token
    assert claimed.lease_expires_at == 2_000 + 60_000
    assert claimed.attempts == 0
    with catalog.write() as connection:
        assert jobs.claim_next(connection, worker_id="worker-2", now_ms=2_100, lease_seconds=60) is None


def test_enqueue_is_idempotent_on_the_five_field_key(catalog: Catalog) -> None:
    first = _enqueue(catalog)
    second = _enqueue(catalog)
    other_processor = _enqueue(catalog, processor_id="catalog-scan@1")
    other_revision = _enqueue(catalog, input_revision="rev-2")

    assert first == second
    assert len({first, other_processor, other_revision}) == 3
    assert catalog.read_one("SELECT COUNT(*) FROM jobs")[0] == 3


def test_heartbeat_extends_only_a_live_lease(catalog: Catalog) -> None:
    _enqueue(catalog)
    with catalog.write() as connection:
        claimed = jobs.claim_next(connection, worker_id="worker-1", now_ms=1_000, lease_seconds=60)
        extended = jobs.heartbeat(
            connection,
            job_id=claimed.job_id,
            lease_token=claimed.lease_token,
            worker_id="worker-1",
            now_ms=30_000,
            lease_seconds=60,
        )
        assert extended.lease_expires_at == 30_000 + 60_000
        with pytest.raises(CatalogError) as stale:
            jobs.heartbeat(
                connection,
                job_id=claimed.job_id,
                lease_token="0" * 32,
                worker_id="worker-1",
                now_ms=31_000,
                lease_seconds=60,
            )
        assert stale.value.code == STALE_LEASE
        with pytest.raises(CatalogError) as foreign:
            jobs.heartbeat(
                connection,
                job_id=claimed.job_id,
                lease_token=claimed.lease_token,
                worker_id="worker-2",
                now_ms=31_000,
                lease_seconds=60,
            )
        assert foreign.value.code == LEASE_CONFLICT
        with pytest.raises(CatalogError) as expired:
            jobs.heartbeat(
                connection,
                job_id=claimed.job_id,
                lease_token=claimed.lease_token,
                worker_id="worker-1",
                now_ms=extended.lease_expires_at + 1,
                lease_seconds=60,
            )
        assert expired.value.code == STALE_LEASE


def test_finish_clears_the_lease_and_is_terminal(catalog: Catalog) -> None:
    _enqueue(catalog)
    with catalog.write() as connection:
        claimed = jobs.claim_next(connection, worker_id="worker-1", now_ms=1_000, lease_seconds=60)
    finished = _finish(catalog, claimed, jobs.STATE_SUCCEEDED, now_ms=2_000, progress=1.0)

    assert finished.state == jobs.STATE_SUCCEEDED
    assert finished.lease_token is None
    assert finished.lease_expires_at is None
    assert finished.worker_id is None
    assert finished.progress == 1.0
    with catalog.write() as connection:
        assert jobs.claim_next(connection, worker_id="worker-2", now_ms=9_999_999, lease_seconds=60) is None
        with pytest.raises(CatalogError) as conflict:
            jobs.heartbeat(
                connection,
                job_id=claimed.job_id,
                lease_token=claimed.lease_token,
                worker_id="worker-1",
                now_ms=2_100,
                lease_seconds=60,
            )
        assert conflict.value.code == LEASE_CONFLICT


def test_illegal_transitions_are_refused(catalog: Catalog) -> None:
    _enqueue(catalog)
    with catalog.write() as connection:
        claimed = jobs.claim_next(connection, worker_id="worker-1", now_ms=1_000, lease_seconds=60)
        with pytest.raises(CatalogError) as error:
            jobs.finish(
                connection,
                job_id=claimed.job_id,
                lease_token=claimed.lease_token,
                state=jobs.STATE_QUEUED,
                input_revision=claimed.input_revision,
                processor_id=claimed.processor_id,
                now_ms=1_100,
            )
        assert error.value.code == INVALID_STATE_TRANSITION
        with pytest.raises(CatalogError) as mismatch:
            jobs.finish(
                connection,
                job_id=claimed.job_id,
                lease_token=claimed.lease_token,
                state=jobs.STATE_SUCCEEDED,
                input_revision="other-revision",
                processor_id=claimed.processor_id,
                now_ms=1_100,
            )
        assert mismatch.value.code == STALE_INPUT_REVISION
        with pytest.raises(CatalogError) as processor:
            jobs.finish(
                connection,
                job_id=claimed.job_id,
                lease_token=claimed.lease_token,
                state=jobs.STATE_SUCCEEDED,
                input_revision=claimed.input_revision,
                processor_id="other-processor",
                now_ms=1_100,
            )
        assert processor.value.code == PROCESSOR_MISMATCH


def test_expired_lease_can_be_taken_over_and_the_old_result_is_refused(catalog: Catalog) -> None:
    _enqueue(catalog)
    with catalog.write() as connection:
        first = jobs.claim_next(connection, worker_id="worker-1", now_ms=1_000, lease_seconds=1)
    with catalog.write() as connection:
        second = jobs.claim_next(connection, worker_id="worker-2", now_ms=2_001, lease_seconds=60)

    assert second.lease_token != first.lease_token
    assert second.worker_id == "worker-2"
    assert second.attempts == 1
    with catalog.write() as connection:
        with pytest.raises(CatalogError) as late:
            jobs.finish(
                connection,
                job_id=first.job_id,
                lease_token=first.lease_token,
                state=jobs.STATE_SUCCEEDED,
                input_revision=first.input_revision,
                processor_id=first.processor_id,
                now_ms=2_002,
            )
        assert late.value.code == STALE_LEASE
        current = jobs.get_job(connection, first.job_id)
        assert current.state == jobs.STATE_RUNNING
        assert current.lease_token == second.lease_token
        assert current.attempts == 1
    finished = _finish(catalog, second, jobs.STATE_SUCCEEDED, now_ms=3_000)
    assert finished.state == jobs.STATE_SUCCEEDED


def test_retry_wait_respects_run_after(catalog: Catalog) -> None:
    _enqueue(catalog)
    with catalog.write() as connection:
        claimed = jobs.claim_next(connection, worker_id="worker-1", now_ms=1_000, lease_seconds=60)
    retried = _finish(
        catalog,
        claimed,
        jobs.STATE_RETRY_WAIT,
        now_ms=2_000,
        run_after_ms=10_000,
        error=CatalogError("FILE_READ_ERROR", "temporary share violation"),
    )

    assert retried.state == jobs.STATE_RETRY_WAIT
    assert retried.attempts == 1
    assert retried.run_after == 10_000
    assert retried.last_error_code == "FILE_READ_ERROR"
    with catalog.write() as connection:
        assert jobs.claim_next(connection, worker_id="worker-1", now_ms=9_999, lease_seconds=60) is None
    with catalog.write() as connection:
        claimed_again = jobs.claim_next(connection, worker_id="worker-1", now_ms=10_000, lease_seconds=60)
    assert claimed_again is not None
    assert claimed_again.state == jobs.STATE_RUNNING
    assert claimed_again.attempts == 1


def test_retryable_failure_exhausts_the_attempt_ceiling(catalog: Catalog) -> None:
    _enqueue(catalog, max_attempts=2)
    with catalog.write() as connection:
        first = jobs.claim_next(connection, worker_id="worker-1", now_ms=1_000, lease_seconds=60)
    first_failure = _finish(
        catalog,
        first,
        jobs.STATE_RETRY_WAIT,
        now_ms=2_000,
        run_after_ms=2_500,
        error=CatalogError("FILE_READ_ERROR", "temporary share violation"),
    )
    assert first_failure.state == jobs.STATE_RETRY_WAIT
    assert first_failure.attempts == 1

    with catalog.write() as connection:
        second = jobs.claim_job(
            connection, job_id=first.job_id, worker_id="worker-1", now_ms=2_500, lease_seconds=60
        )
    assert second.attempts == 1
    final = _finish(
        catalog,
        second,
        jobs.STATE_RETRY_WAIT,
        now_ms=3_000,
        error=CatalogError("FILE_READ_ERROR", "temporary share violation"),
    )

    assert final.state == jobs.STATE_FAILED
    assert final.attempts == 2
    assert final.lease_token is None
    with catalog.write() as connection:
        assert jobs.claim_next(connection, worker_id="worker-2", now_ms=9_999_999, lease_seconds=60) is None
        with pytest.raises(CatalogError) as terminal:
            jobs.claim_job(
                connection, job_id=first.job_id, worker_id="worker-2", now_ms=9_999_999, lease_seconds=60
            )
        assert terminal.value.code == INVALID_STATE_TRANSITION


def test_expired_lease_takeover_respects_the_retry_ceiling(catalog: Catalog) -> None:
    _enqueue(catalog, max_attempts=1)
    with catalog.write() as connection:
        claimed = jobs.claim_next(connection, worker_id="worker-1", now_ms=1_000, lease_seconds=1)

    with catalog.write() as connection:
        with pytest.raises(CatalogError) as error:
            jobs.claim_job(
                connection, job_id=claimed.job_id, worker_id="worker-2", now_ms=2_001, lease_seconds=60
            )
        assert error.value.code == RETRY_EXHAUSTED
        failed = jobs.get_job(connection, claimed.job_id)
        assert failed.state == jobs.STATE_FAILED
        assert failed.attempts == 1
        assert failed.last_error_code == "LEASE_EXPIRED"
        assert failed.lease_token is None
        assert failed.lease_expires_at is None
        assert jobs.claim_next(connection, worker_id="worker-2", now_ms=2_002, lease_seconds=60) is None


def test_scheduler_fails_expired_executions_without_budget(catalog: Catalog) -> None:
    _enqueue(catalog, max_attempts=1)
    with catalog.write() as connection:
        claimed = jobs.claim_next(connection, worker_id="worker-1", now_ms=1_000, lease_seconds=1)

    with catalog.write() as connection:
        assert jobs.claim_next(connection, worker_id="worker-2", now_ms=3_000, lease_seconds=60) is None
        failed = jobs.get_job(connection, claimed.job_id)
        assert failed.state == jobs.STATE_FAILED
        assert failed.attempts == 1
        assert failed.last_error_code == "LEASE_EXPIRED"


def test_blocked_job_is_not_claimable_until_it_is_resumed(catalog: Catalog) -> None:
    _enqueue(catalog)
    with catalog.write() as connection:
        claimed = jobs.claim_next(connection, worker_id="worker-1", now_ms=1_000, lease_seconds=60)
    blocked = _finish(
        catalog,
        claimed,
        jobs.STATE_BLOCKED,
        now_ms=2_000,
        error=CatalogError("SOURCE_OFFLINE", "source root is not reachable"),
    )

    assert blocked.state == jobs.STATE_BLOCKED
    assert blocked.attempts == 0
    assert blocked.last_error_code == "SOURCE_OFFLINE"
    with catalog.write() as connection:
        assert jobs.claim_next(connection, worker_id="worker-1", now_ms=3_000, lease_seconds=60) is None
        resumed = jobs.resume_blocked(connection, job_id=blocked.job_id, now_ms=3_000)
        assert resumed.state == jobs.STATE_QUEUED
        with pytest.raises(CatalogError) as error:
            jobs.resume_blocked(connection, job_id=blocked.job_id, now_ms=3_100)
        assert error.value.code == INVALID_STATE_TRANSITION
        claimed_again = jobs.claim_job(
            connection,
            job_id=blocked.job_id,
            worker_id="worker-1",
            now_ms=3_000,
            lease_seconds=60,
        )
    assert claimed_again.state == jobs.STATE_RUNNING
