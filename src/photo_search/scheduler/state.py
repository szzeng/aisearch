"""Persistent scheduler policy: pause/resume, the fairness window and retry backoff.

Pause is a *scheduler* strategy (architecture §6.2), so it lives in its own singleton row
and never rewrites job states: a paused scheduler simply stops claiming, while a live lease
may still heartbeat and submit. Both ``pause`` and ``resume`` are idempotent — repeating a
request that would not change the persisted policy does not bump the revision.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from photo_search.catalog.errors import CATALOG_UNREADABLE, CatalogError

SCHEDULER_STATE_ID = 1
#: Backlog aging window: a waiting job gains one effective priority point per window.
DEFAULT_AGING_WINDOW_MS = 60_000
#: Exponential retry backoff after a retryable execution failure.
DEFAULT_RETRY_BASE_MS = 5_000
DEFAULT_RETRY_MULTIPLIER = 2
DEFAULT_RETRY_MAX_MS = 300_000


@dataclass(frozen=True)
class SchedulerState:
    paused: bool
    revision: int
    aging_window_ms: int
    updated_at: int

    @property
    def status(self) -> str:
        return "paused" if self.paused else "running"

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.status,
            "paused": self.paused,
            "revision": self.revision,
            "aging_window_ms": self.aging_window_ms,
            "updated_at": self.updated_at,
        }


def read_state(connection: sqlite3.Connection) -> SchedulerState:
    row = connection.execute(
        "SELECT paused, revision, aging_window_ms, updated_at FROM scheduler_state "
        "WHERE state_id = ?",
        (SCHEDULER_STATE_ID,),
    ).fetchone()
    if row is None:
        raise CatalogError(
            CATALOG_UNREADABLE,
            "the catalog has no scheduler_state row; the schema is not usable",
            details={"table": "scheduler_state"},
        )
    return SchedulerState(
        paused=bool(row["paused"]),
        revision=int(row["revision"]),
        aging_window_ms=int(row["aging_window_ms"]),
        updated_at=int(row["updated_at"]),
    )


def set_paused(
    connection: sqlite3.Connection, *, paused: bool, now_ms: int
) -> tuple[SchedulerState, bool]:
    """Idempotently persist the pause policy; returns ``(state, changed)``."""
    current = read_state(connection)
    if current.paused == bool(paused):
        return current, False
    connection.execute(
        "UPDATE scheduler_state SET paused = ?, revision = revision + 1, updated_at = ? "
        "WHERE state_id = ?",
        (1 if paused else 0, int(now_ms), SCHEDULER_STATE_ID),
    )
    return read_state(connection), True


def configure_aging_window(
    connection: sqlite3.Connection, *, aging_window_ms: int, now_ms: int
) -> tuple[SchedulerState, bool]:
    """Change the fairness window; a real change bumps the revision like pause does."""
    window = int(aging_window_ms)
    if window <= 0:
        raise CatalogError(
            CATALOG_UNREADABLE,
            "the scheduler aging window must be a positive number of milliseconds",
            details={"aging_window_ms": window},
        )
    current = read_state(connection)
    if current.aging_window_ms == window:
        return current, False
    connection.execute(
        "UPDATE scheduler_state SET aging_window_ms = ?, revision = revision + 1, updated_at = ? "
        "WHERE state_id = ?",
        (window, int(now_ms), SCHEDULER_STATE_ID),
    )
    return read_state(connection), True


def effective_priority(
    *, priority: int, created_at: int, now_ms: int, aging_window_ms: int
) -> int:
    """Stored priority plus one aging point per elapsed fairness window."""
    window = max(int(aging_window_ms), 1)
    waited = max(int(now_ms) - int(created_at), 0)
    return int(priority) + waited // window


def backoff_delay_ms(
    attempts: int,
    *,
    base_ms: int = DEFAULT_RETRY_BASE_MS,
    multiplier: int = DEFAULT_RETRY_MULTIPLIER,
    max_ms: int = DEFAULT_RETRY_MAX_MS,
) -> int:
    """Exponential, capped retry delay for the ``attempts``-th failed execution.

    The result is persisted in ``jobs.run_after``, so "do not retry before the deadline" is
    a database fact rather than a sleep, and a scheduler that runs early simply finds
    nothing runnable.
    """
    exponent = max(int(attempts) - 1, 0)
    delay = int(base_ms) * (int(multiplier) ** exponent)
    return min(delay, int(max_ms))
