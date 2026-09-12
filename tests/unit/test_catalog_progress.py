"""P1-02: monotone, lease-qualified job progress (``jobs.set_progress``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import LEASE_CONFLICT, STALE_LEASE, CatalogError
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


def _enqueue(catalog: Catalog) -> int:
    with catalog.write() as connection:
        return jobs.enqueue(connection, now_ms=1_000, **SUBJECT)


def _claimed(catalog: Catalog) -> jobs.Job:
    job_id = _enqueue(catalog)
    with catalog.write() as connection:
        return jobs.claim_job(
            connection, job_id=job_id, worker_id="worker-1", now_ms=1_000, lease_seconds=10
        )


def test_progress_is_monotone_clamped_and_lease_qualified(catalog: Catalog) -> None:
    claimed = _claimed(catalog)
    now_ms = 1_000

    with catalog.write() as connection:
        assert (
            jobs.set_progress(
                connection,
                job_id=claimed.job_id,
                lease_token=claimed.lease_token or "",
                now_ms=now_ms,
                progress=0.4,
            )
            == 0.4
        )
        # A smaller (for example out-of-order) value never lowers the stored progress.
        assert (
            jobs.set_progress(
                connection,
                job_id=claimed.job_id,
                lease_token=claimed.lease_token or "",
                now_ms=now_ms,
                progress=0.1,
            )
            == 0.4
        )
        assert (
            jobs.set_progress(
                connection,
                job_id=claimed.job_id,
                lease_token=claimed.lease_token or "",
                now_ms=now_ms,
                progress=1.5,
            )
            == 1.0
        )

        with pytest.raises(CatalogError) as stale_token:
            jobs.set_progress(
                connection,
                job_id=claimed.job_id,
                lease_token="not-the-live-token",
                now_ms=now_ms,
                progress=0.9,
            )
        assert stale_token.value.code == LEASE_CONFLICT

        with pytest.raises(CatalogError) as expired:
            jobs.set_progress(
                connection,
                job_id=claimed.job_id,
                lease_token=claimed.lease_token or "",
                now_ms=1_000 + 10_000,
                progress=0.9,
            )
        assert expired.value.code == STALE_LEASE

    stored = catalog.read_one("SELECT progress FROM jobs WHERE job_id = ?", (claimed.job_id,))
    assert stored["progress"] == 1.0


def test_progress_is_refused_once_the_job_leaves_running(catalog: Catalog) -> None:
    claimed = _claimed(catalog)
    with catalog.write() as connection:
        jobs.finish(
            connection,
            job_id=claimed.job_id,
            lease_token=claimed.lease_token or "",
            state=jobs.STATE_SUCCEEDED,
            input_revision=claimed.input_revision,
            processor_id=claimed.processor_id,
            now_ms=1_500,
            progress=1.0,
        )

    with catalog.write() as connection:
        with pytest.raises(CatalogError) as error:
            jobs.set_progress(
                connection,
                job_id=claimed.job_id,
                lease_token=claimed.lease_token or "",
                now_ms=1_600,
                progress=0.5,
            )
    assert error.value.code == LEASE_CONFLICT
    stored = catalog.read_one("SELECT progress FROM jobs WHERE job_id = ?", (claimed.job_id,))
    assert stored["progress"] == 1.0
