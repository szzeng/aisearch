"""Persistent job rows with claim, heartbeat, lease and late-submit rules (ADR-0002 §6).

Every function here takes an open catalog connection and must be called inside the
catalog's single-writer transaction (``Catalog.write()`` or ``CatalogService.write()``).
Time is always passed in explicitly so lease expiry is deterministic in tests.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Any

from photo_search.catalog.errors import (
    INVALID_STATE_TRANSITION,
    JOB_NOT_FOUND,
    LEASE_CONFLICT,
    LEASE_EXPIRED,
    PROCESSOR_MISMATCH,
    RETRY_EXHAUSTED,
    STALE_INPUT_REVISION,
    STALE_LEASE,
    CatalogError,
)

STATE_QUEUED = "QUEUED"
STATE_RUNNING = "RUNNING"
STATE_SUCCEEDED = "SUCCEEDED"
STATE_RETRY_WAIT = "RETRY_WAIT"
STATE_BLOCKED = "BLOCKED"
STATE_FAILED = "FAILED"
STATE_CANCELLED = "CANCELLED"

TERMINAL_STATES = frozenset({STATE_SUCCEEDED, STATE_FAILED, STATE_CANCELLED})

#: Backoff applied when a retryable failure returns a job to RETRY_WAIT (ADR-0002 §6.2).
DEFAULT_RETRY_BACKOFF_MS = 5_000

_EXHAUSTED_MESSAGE = "lease expired before completion; no retry budget left"

#: Ordinary, contract-frozen state transitions (ADR-0002 §6.1).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    STATE_QUEUED: frozenset({STATE_RUNNING, STATE_CANCELLED}),
    STATE_RUNNING: frozenset(
        {STATE_SUCCEEDED, STATE_RETRY_WAIT, STATE_BLOCKED, STATE_FAILED, STATE_CANCELLED}
    ),
    STATE_RETRY_WAIT: frozenset({STATE_QUEUED, STATE_CANCELLED}),
    STATE_BLOCKED: frozenset({STATE_QUEUED, STATE_CANCELLED}),
    STATE_SUCCEEDED: frozenset(),
    STATE_FAILED: frozenset(),
    STATE_CANCELLED: frozenset(),
}

JOB_FIELDS = (
    "job_id",
    "subject_type",
    "subject_id",
    "input_revision",
    "task_type",
    "processor_id",
    "state",
    "priority",
    "attempts",
    "max_attempts",
    "run_after",
    "lease_token",
    "lease_expires_at",
    "worker_id",
    "progress",
    "last_error_code",
    "last_error_message",
    "created_at",
    "updated_at",
)
JOB_COLUMNS = ", ".join(JOB_FIELDS)

MAX_ERROR_MESSAGE_CHARS = 500


@dataclass(frozen=True)
class Job:
    job_id: int
    subject_type: str
    subject_id: str
    input_revision: str
    task_type: str
    processor_id: str
    state: str
    priority: int
    attempts: int
    max_attempts: int
    run_after: int
    lease_token: str | None
    lease_expires_at: int | None
    worker_id: str | None
    progress: float
    last_error_code: str | None
    last_error_message: str | None
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Job:
        return cls(**{name: row[name] for name in JOB_FIELDS})

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "input_revision": self.input_revision,
            "task_type": self.task_type,
            "processor_id": self.processor_id,
            "state": self.state,
            "priority": self.priority,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "run_after": self.run_after,
            "lease_token": self.lease_token,
            "lease_expires_at": self.lease_expires_at,
            "worker_id": self.worker_id,
            "progress": self.progress,
            "last_error_code": self.last_error_code,
            "last_error_message": self.last_error_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def new_lease_token() -> str:
    return secrets.token_hex(16)


def enqueue(
    connection: sqlite3.Connection,
    *,
    subject_type: str,
    subject_id: str,
    input_revision: str,
    task_type: str,
    processor_id: str,
    now_ms: int,
    priority: int = 0,
    max_attempts: int = 3,
    run_after: int | None = None,
) -> int:
    """Create the logical job once; the five-field unique key makes this idempotent."""
    connection.execute(
        """
        INSERT INTO jobs (
            subject_type, subject_id, input_revision, task_type, processor_id,
            state, priority, attempts, max_attempts, run_after, progress, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 0.0, ?, ?)
        ON CONFLICT (subject_type, subject_id, input_revision, task_type, processor_id)
        DO NOTHING
        """,
        (
            subject_type,
            subject_id,
            input_revision,
            task_type,
            processor_id,
            STATE_QUEUED,
            priority,
            max_attempts,
            now_ms if run_after is None else run_after,
            now_ms,
            now_ms,
        ),
    )
    row = connection.execute(
        "SELECT job_id FROM jobs WHERE subject_type = ? AND subject_id = ? AND input_revision = ? "
        "AND task_type = ? AND processor_id = ?",
        (subject_type, subject_id, input_revision, task_type, processor_id),
    ).fetchone()
    return int(row[0])


def get_job(connection: sqlite3.Connection, job_id: int) -> Job:
    row = connection.execute(f"SELECT {JOB_COLUMNS} FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        raise CatalogError(JOB_NOT_FOUND, f"job {job_id} does not exist", details={"job_id": job_id})
    return Job.from_row(row)


def _claimable_row(
    connection: sqlite3.Connection,
    *,
    now_ms: int,
    task_type: str | None,
) -> sqlite3.Row | None:
    task_filter = "" if task_type is None else " AND task_type = ?"
    parameters: tuple = (now_ms,) if task_type is None else (now_ms, task_type)
    row = connection.execute(
        f"SELECT {JOB_COLUMNS} FROM jobs WHERE state = 'QUEUED' AND run_after <= ?{task_filter} "
        "ORDER BY priority DESC, created_at ASC, job_id ASC LIMIT 1",
        parameters,
    ).fetchone()
    if row is not None:
        return row
    parameters = (now_ms,) if task_type is None else (now_ms, task_type)
    return connection.execute(
        f"SELECT {JOB_COLUMNS} FROM jobs WHERE state = 'RUNNING' AND lease_expires_at <= ?{task_filter} "
        "ORDER BY priority DESC, created_at ASC, job_id ASC LIMIT 1",
        parameters,
    ).fetchone()


def _start_running(
    connection: sqlite3.Connection,
    *,
    job_id: int,
    worker_id: str,
    now_ms: int,
    lease_seconds: int,
    takeover: bool,
) -> Job:
    job = get_job(connection, job_id)
    token = new_lease_token()
    expires_at = now_ms + int(lease_seconds) * 1000
    if takeover:
        # A lost execution counts as one ordinary failure before this worker may continue,
        # and the retry ceiling applies to it exactly as it does to a reported failure.
        lost_attempts = job.attempts + 1
        if lost_attempts >= job.max_attempts:
            connection.execute(
                """
                UPDATE jobs
                   SET state = ?, attempts = ?, lease_token = NULL, lease_expires_at = NULL,
                       worker_id = NULL, last_error_code = ?, last_error_message = ?, updated_at = ?
                 WHERE job_id = ? AND state = ? AND lease_expires_at <= ?
                """,
                (
                    STATE_FAILED,
                    lost_attempts,
                    LEASE_EXPIRED,
                    f"lease expired before completion; attempts reached max_attempts={job.max_attempts}",
                    now_ms,
                    job_id,
                    STATE_RUNNING,
                    now_ms,
                ),
            )
            raise CatalogError(
                RETRY_EXHAUSTED,
                f"job {job_id} exhausted {job.max_attempts} attempts after an expired lease",
                details={
                    "job_id": job_id,
                    "attempts": lost_attempts,
                    "max_attempts": job.max_attempts,
                },
            )
        connection.execute(
            """
            UPDATE jobs
               SET state = ?, lease_token = ?, lease_expires_at = ?, worker_id = ?,
                   attempts = ?, updated_at = ?
             WHERE job_id = ? AND state = ? AND lease_expires_at <= ?
            """,
            (
                STATE_RUNNING,
                token,
                expires_at,
                worker_id,
                lost_attempts,
                now_ms,
                job_id,
                STATE_RUNNING,
                now_ms,
            ),
        )
    else:
        connection.execute(
            """
            UPDATE jobs
               SET state = ?, lease_token = ?, lease_expires_at = ?, worker_id = ?, updated_at = ?
             WHERE job_id = ? AND state = ? AND run_after <= ?
            """,
            (STATE_RUNNING, token, expires_at, worker_id, now_ms, job_id, STATE_QUEUED, now_ms),
        )
    claimed = get_job(connection, job_id)
    if claimed.state != STATE_RUNNING or claimed.lease_token != token:
        raise CatalogError(
            LEASE_CONFLICT,
            f"job {job_id} could not be claimed atomically",
            details={"job_id": job_id, "state": claimed.state},
        )
    return claimed


def claim_job(
    connection: sqlite3.Connection,
    *,
    job_id: int,
    worker_id: str,
    now_ms: int,
    lease_seconds: int,
) -> Job:
    """Claim one specific logical job, or atomically take over its expired lease."""
    job = get_job(connection, job_id)
    if job.state == STATE_RETRY_WAIT:
        if job.run_after > now_ms:
            raise CatalogError(
                INVALID_STATE_TRANSITION,
                f"job {job_id} is not runnable before {job.run_after}",
                details={"job_id": job_id, "run_after": job.run_after, "now": now_ms},
            )
        connection.execute(
            "UPDATE jobs SET state = ?, updated_at = ? WHERE job_id = ? AND state = ?",
            (STATE_QUEUED, now_ms, job_id, STATE_RETRY_WAIT),
        )
        job = get_job(connection, job_id)
    if job.state == STATE_QUEUED:
        if job.run_after > now_ms:
            raise CatalogError(
                INVALID_STATE_TRANSITION,
                f"job {job_id} is not runnable before {job.run_after}",
                details={"job_id": job_id, "run_after": job.run_after, "now": now_ms},
            )
        return _start_running(
            connection,
            job_id=job_id,
            worker_id=worker_id,
            now_ms=now_ms,
            lease_seconds=lease_seconds,
            takeover=False,
        )
    if job.state == STATE_RUNNING:
        if job.lease_expires_at is None or job.lease_expires_at > now_ms:
            raise CatalogError(
                LEASE_CONFLICT,
                f"job {job_id} is already running under an unexpired lease",
                details={"job_id": job_id, "lease_expires_at": job.lease_expires_at, "now": now_ms},
            )
        return _start_running(
            connection,
            job_id=job_id,
            worker_id=worker_id,
            now_ms=now_ms,
            lease_seconds=lease_seconds,
            takeover=True,
        )
    raise CatalogError(
        INVALID_STATE_TRANSITION,
        f"job {job_id} in state {job.state} cannot be claimed",
        details={"job_id": job_id, "state": job.state},
    )


def claim_next(
    connection: sqlite3.Connection,
    *,
    worker_id: str,
    now_ms: int,
    lease_seconds: int,
    task_type: str | None = None,
) -> Job | None:
    """Claim the highest-priority runnable job; ``None`` when nothing is runnable."""
    connection.execute(
        "UPDATE jobs SET state = ?, updated_at = ? WHERE state = ? AND run_after <= ?",
        (STATE_QUEUED, now_ms, STATE_RETRY_WAIT, now_ms),
    )
    _fail_exhausted_leases(connection, now_ms=now_ms, task_type=task_type)
    row = _claimable_row(connection, now_ms=now_ms, task_type=task_type)
    if row is None:
        return None
    takeover = row["state"] == STATE_RUNNING
    return _start_running(
        connection,
        job_id=int(row["job_id"]),
        worker_id=worker_id,
        now_ms=now_ms,
        lease_seconds=lease_seconds,
        takeover=takeover,
    )


def _fail_exhausted_leases(
    connection: sqlite3.Connection,
    *,
    now_ms: int,
    task_type: str | None = None,
) -> int:
    """Fail expired executions that have no retry budget left before claiming again."""
    task_filter = "" if task_type is None else " AND task_type = ?"
    parameters: tuple = (
        (STATE_FAILED, LEASE_EXPIRED, _EXHAUSTED_MESSAGE, int(now_ms), STATE_RUNNING, int(now_ms))
        if task_type is None
        else (
            STATE_FAILED,
            LEASE_EXPIRED,
            _EXHAUSTED_MESSAGE,
            int(now_ms),
            STATE_RUNNING,
            int(now_ms),
            task_type,
        )
    )
    cursor = connection.execute(
        "UPDATE jobs SET state = ?, attempts = attempts + 1, lease_token = NULL, "
        "lease_expires_at = NULL, worker_id = NULL, last_error_code = ?, last_error_message = ?, "
        f"updated_at = ? WHERE state = ? AND lease_expires_at <= ? "
        f"AND attempts + 1 >= max_attempts{task_filter}",
        parameters,
    )
    return int(cursor.rowcount or 0)


def heartbeat(
    connection: sqlite3.Connection,
    *,
    job_id: int,
    lease_token: str,
    worker_id: str,
    now_ms: int,
    lease_seconds: int,
) -> Job:
    """Extend a live lease; stale or expired tokens are refused, never reactivated."""
    job = get_job(connection, job_id)
    if job.state != STATE_RUNNING:
        raise CatalogError(
            LEASE_CONFLICT,
            f"job {job_id} is not RUNNING",
            details={"job_id": job_id, "state": job.state},
        )
    if job.lease_token != lease_token:
        raise CatalogError(
            STALE_LEASE,
            f"lease token for job {job_id} is stale",
            details={"job_id": job_id},
        )
    if job.lease_expires_at is None or job.lease_expires_at <= now_ms:
        raise CatalogError(
            STALE_LEASE,
            f"lease for job {job_id} has expired",
            details={"job_id": job_id, "lease_expires_at": job.lease_expires_at, "now": now_ms},
        )
    if job.worker_id != worker_id:
        raise CatalogError(
            LEASE_CONFLICT,
            f"job {job_id} is leased to another worker",
            details={"job_id": job_id, "worker_id": worker_id, "lease_owner": job.worker_id},
        )
    connection.execute(
        "UPDATE jobs SET lease_expires_at = ?, updated_at = ? WHERE job_id = ?",
        (now_ms + int(lease_seconds) * 1000, now_ms, job_id),
    )
    return get_job(connection, job_id)


def validate_submission(
    connection: sqlite3.Connection,
    *,
    job_id: int,
    lease_token: str,
    now_ms: int,
    input_revision: str,
    processor_id: str,
) -> Job:
    """Check that this execution is still allowed to write a persistent result."""
    job = get_job(connection, job_id)
    if job.state != STATE_RUNNING:
        raise CatalogError(
            LEASE_CONFLICT,
            f"job {job_id} is not RUNNING",
            details={"job_id": job_id, "state": job.state},
        )
    if job.lease_token != lease_token:
        raise CatalogError(
            STALE_LEASE,
            f"lease token for job {job_id} is stale",
            details={"job_id": job_id},
        )
    if job.lease_expires_at is None or job.lease_expires_at <= now_ms:
        raise CatalogError(
            STALE_LEASE,
            f"lease for job {job_id} has expired",
            details={"job_id": job_id, "lease_expires_at": job.lease_expires_at, "now": now_ms},
        )
    if job.input_revision != input_revision:
        raise CatalogError(
            STALE_INPUT_REVISION,
            f"job {job_id} was created for input revision {job.input_revision}",
            details={"job_id": job_id, "input_revision": input_revision},
        )
    if job.processor_id != processor_id:
        raise CatalogError(
            PROCESSOR_MISMATCH,
            f"job {job_id} was created for processor {job.processor_id}",
            details={"job_id": job_id, "processor_id": processor_id},
        )
    return job


def finish(
    connection: sqlite3.Connection,
    *,
    job_id: int,
    lease_token: str,
    state: str,
    input_revision: str,
    processor_id: str,
    now_ms: int,
    progress: float | None = None,
    error: CatalogError | None = None,
    run_after_ms: int | None = None,
) -> Job:
    """Leave RUNNING through a legal transition and clear the lease in the same commit."""
    job = validate_submission(
        connection,
        job_id=job_id,
        lease_token=lease_token,
        now_ms=now_ms,
        input_revision=input_revision,
        processor_id=processor_id,
    )
    if state not in ALLOWED_TRANSITIONS[job.state]:
        raise CatalogError(
            INVALID_STATE_TRANSITION,
            f"job {job_id} cannot move from {job.state} to {state}",
            details={"job_id": job_id, "from": job.state, "to": state},
        )
    target = state
    attempts = job.attempts
    error_code = error.code if error else None
    error_message = _short_message(error)
    if state in (STATE_RETRY_WAIT, STATE_FAILED):
        # ADR-0002 §6.2: every failed execution consumes retry budget, and a retryable
        # failure that reaches max_attempts becomes a terminal failure.
        attempts += 1
        if state == STATE_RETRY_WAIT and attempts >= job.max_attempts:
            target = STATE_FAILED
            error_code = error_code or RETRY_EXHAUSTED
            error_message = error_message or (
                f"attempts reached max_attempts={job.max_attempts} after a retryable failure"
            )
    if target == STATE_SUCCEEDED:
        error_code = None
        error_message = None
    next_run_after = job.run_after
    if target == STATE_RETRY_WAIT:
        next_run_after = run_after_ms if run_after_ms is not None else now_ms + DEFAULT_RETRY_BACKOFF_MS
    connection.execute(
        """
        UPDATE jobs
           SET state = ?, attempts = ?, progress = COALESCE(?, progress), last_error_code = ?,
               last_error_message = ?, lease_token = NULL, lease_expires_at = NULL, worker_id = NULL,
               run_after = ?, updated_at = ?
         WHERE job_id = ?
        """,
        (
            target,
            attempts,
            progress,
            error_code,
            error_message,
            next_run_after,
            now_ms,
            job_id,
        ),
    )
    return get_job(connection, job_id)


def resume_blocked(connection: sqlite3.Connection, *, job_id: int, now_ms: int) -> Job:
    """Move a BLOCKED job back to QUEUED; the scan_run follows in the caller's transaction."""
    job = get_job(connection, job_id)
    if STATE_QUEUED not in ALLOWED_TRANSITIONS[job.state]:
        raise CatalogError(
            INVALID_STATE_TRANSITION,
            f"job {job_id} in state {job.state} cannot be resumed",
            details={"job_id": job_id, "state": job.state},
        )
    connection.execute(
        "UPDATE jobs SET state = ?, updated_at = ? WHERE job_id = ?",
        (STATE_QUEUED, now_ms, job_id),
    )
    return get_job(connection, job_id)


def _short_message(error: CatalogError | None) -> str | None:
    if error is None:
        return None
    payload = json.dumps(error.as_dict(), ensure_ascii=False)
    return payload[:MAX_ERROR_MESSAGE_CHARS]
