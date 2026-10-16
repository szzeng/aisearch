"""Recover every exhausted lease without a SQL variable per expired job."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from photo_search.catalog import store
from photo_search.catalog.db import Catalog
from photo_search.jobs import queue as jobs

NOW = 1_000


@pytest.fixture()
def catalog(tmp_path: Path):
    instance = Catalog.open(data_root=tmp_path / "data")
    try:
        yield instance
    finally:
        instance.close()


def seed(connection: sqlite3.Connection, number: int, **changes: Any) -> int:
    values = {
        "subject_type": "synthetic", "subject_id": str(number), "input_revision": "v1",
        "task_type": "embedding", "processor_id": "processor@1", "state": "RUNNING",
        "attempts": 2, "max_attempts": 3, "run_after": 7, "lease_token": f"lease-{number}",
        "lease_expires_at": NOW, "worker_id": "old-worker", "created_at": 1, "updated_at": 2,
    }
    values.update(changes)
    if values["state"] != "RUNNING":
        values.update(lease_token=None, lease_expires_at=None, worker_id=None)
    cursor = connection.execute(
        f"INSERT INTO jobs ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
        tuple(values.values()),
    )
    return int(cursor.lastrowid)


def snapshot(catalog: Catalog) -> dict[int, dict[str, Any]]:
    return {int(row["job_id"]): dict(row) for row in catalog.connection.execute("SELECT * FROM jobs")}


def test_exhausted_backlog_larger_than_parameter_limit_does_not_block_claim(catalog: Catalog) -> None:
    with catalog.write() as connection:
        for number in range(65):
            seed(connection, number)
        ready = seed(connection, 1000, state="QUEUED", attempts=0)
    # SQLite's real prepared-statement limit, reduced to exercise a library-size backlog
    # with a small fixture. A separate production-catalog probe reproduces default 32766.
    catalog.connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 64)
    with catalog.write() as connection:
        claimed = jobs.claim_next(connection, worker_id="new-worker", now_ms=NOW, lease_seconds=60)
    assert claimed is not None and claimed.job_id == ready
    failed = catalog.read_all("SELECT * FROM jobs WHERE state = 'FAILED'")
    assert len(failed) == 65
    assert all(row["attempts"] == 3 and row["last_error_code"] == "LEASE_EXPIRED" for row in failed)
    assert all(row["lease_token"] is None and row["worker_id"] is None for row in failed)


@pytest.mark.parametrize("task_type", [None, "embedding", "other-task"])
@pytest.mark.parametrize("processor_ids", [None, [], ["processor@1"], ["other@1"]])
def test_expiry_predicate_preserves_boundaries_and_unrelated_rows(
    catalog: Catalog, task_type: str | None, processor_ids: list[str] | None
) -> None:
    cases = [
        {}, {"lease_expires_at": NOW - 1}, {"lease_expires_at": NOW + 1},
        {"lease_expires_at": None}, {"attempts": 1}, {"attempts": 3},
        {"max_attempts": 1, "attempts": 0}, {"task_type": "other-task"},
        {"processor_id": "other@1"}, {"state": "QUEUED"}, {"state": "RETRY_WAIT"},
        {"state": "BLOCKED"}, {"state": "SUCCEEDED"}, {"state": "CANCELLED"}, {"state": "FAILED"},
    ]
    with catalog.write() as connection:
        for number, values in enumerate(cases):
            seed(connection, number, **values)
    before = snapshot(catalog)
    selected = {
        job_id for job_id, row in before.items()
        if row["state"] == "RUNNING" and row["lease_expires_at"] is not None
        and row["lease_expires_at"] <= NOW and row["attempts"] + 1 >= row["max_attempts"]
        and (task_type is None or row["task_type"] == task_type)
        and (processor_ids is None or row["processor_id"] in processor_ids)
    }
    with catalog.write() as connection:
        rows = jobs.fail_exhausted_leases(
            connection, now_ms=NOW, task_type=task_type, processor_ids=processor_ids
        )
    assert {int(row["job_id"]) for row in rows} == selected
    assert all(row["attempts"] == before[int(row["job_id"])]["attempts"] for row in rows)
    after = snapshot(catalog)
    for job_id, row in before.items():
        if job_id not in selected:
            assert after[job_id] == row
        else:
            assert after[job_id] == {
                **row, "state": "FAILED", "attempts": row["attempts"] + 1,
                "lease_token": None, "lease_expires_at": None, "worker_id": None,
                "last_error_code": "LEASE_EXPIRED",
                "last_error_message": "lease expired before completion; no retry budget left",
                "updated_at": NOW,
            }
    with catalog.write() as connection:
        assert jobs.fail_exhausted_leases(
            connection, now_ms=NOW, task_type=task_type, processor_ids=processor_ids
        ) == []
    assert snapshot(catalog) == after


def test_scan_subject_and_job_fail_atomically_and_survive_restart(catalog: Catalog, tmp_path: Path) -> None:
    with catalog.write() as connection:
        source_id = store.insert_source(
            connection, display_name="synthetic", root_path=str(tmp_path / "photos"),
            volume_hint=None, status="ONLINE",
        )
        scan_id = store.insert_scan_run(connection, source_id=source_id, state="RUNNING")
        scan_job = seed(
            connection, 1, subject_type=store.SCAN_SUBJECT_TYPE, subject_id=str(scan_id),
            task_type=store.SCAN_TASK_TYPE, processor_id=store.SCAN_PROCESSOR_ID,
        )
    before = snapshot(catalog)
    with pytest.raises(RuntimeError, match="injected rollback"):
        with catalog.write() as connection:
            jobs.fail_exhausted_leases(connection, now_ms=NOW)
            assert store.get_scan_row(connection, scan_id)["state"] == "FAILED"
            raise RuntimeError("injected rollback")
    assert snapshot(catalog) == before
    assert store.get_scan_row(catalog.connection, scan_id)["state"] == "RUNNING"
    with catalog.write() as connection:
        rows = jobs.fail_exhausted_leases(connection, now_ms=NOW)
        assert [int(row["job_id"]) for row in rows] == [scan_job]
    with Catalog.open(data_root=tmp_path / "data") as reopened:
        assert jobs.get_job(reopened.connection, scan_job).state == "FAILED"
        scan = store.get_scan_row(reopened.connection, scan_id)
        assert scan["state"] == "FAILED" and scan["finished_at"] == NOW
        assert json.loads(str(scan["error"]))["code"] == "LEASE_EXPIRED"
