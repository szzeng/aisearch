"""P4-02E: the bounded semantic-backlog claim (P4-02B section 9, finding F1).

``jobs.claimable_candidates`` used to materialise and sort the *whole* runnable backlog on
every claim (both ``UNION ALL`` sides showed ``USE TEMP B-TREE FOR ORDER BY``), which made a
one-shot 300 000-event burst quadratic. This file is the card's evidence that the rewritten
access path returns *exactly* the same page - the same ``job_id`` sequence and the same
``effective_priority`` values - on the randomised state matrix the DS-H review designed, that
its plan neither sorts nor scans the backlog on either runnable side, and that the frozen
lease / qualification / pause semantics around the claim are untouched.

The reference page is reproduced here *independently* of the product module (its own frozen
SQL and its own processor filter), so the comparison is a cross-check and not a tautology.
Every database lives in pytest's ``tmp_path``; no real photo library is read.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Sequence

import pytest

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import LEASE_EXPIRED, SCHEDULER_PAUSED, CatalogError
from photo_search.jobs import queue as jobs
from photo_search.scheduler import engine
from photo_search.scheduler.contracts import (
    CONTRACT_VERSION,
    ProcessorSpec,
    RuntimeCapabilities,
)

#: Fixed wall-clock origin: two runs of this file must produce the same pages.
NOW = 1_790_244_000_000
WINDOW = 60_000
#: The scheduler's own claim breadth.
LIMIT = 256
PROCESSOR = "geo-extract@1"
TASK_TYPE = "geo_extract"
PY_PACKAGE = "photo-search-runtime@1"

#: The DS-H state matrix: how many randomised rows one trial holds, and the axes it spans.
TRIALS = 240
ROWS_PER_TRIAL = 400
LIMITS = (1, 2, 3, 7, 64, 256)
WINDOWS = (1, 60_000, 86_400_000)


# ------------------------------------------------------------------ frozen reference page
def frozen_processor_filter(processor_ids: Sequence[str] | None) -> tuple[str, list[str]]:
    """The pre-P4-02E ``processor_id IN (...)`` fragment, written out again on purpose."""
    if processor_ids is None:
        return "", []
    values = [str(value) for value in processor_ids]
    if not values:
        return " AND processor_id IN ('')", []
    placeholders = ", ".join("?" for _ in values)
    return f" AND processor_id IN ({placeholders})", values


def frozen_page(
    connection,
    *,
    processor_ids: Sequence[str] | None,
    now_ms: int = NOW,
    window: int = WINDOW,
    limit: int = LIMIT,
) -> list[tuple[int, int]]:
    """The frozen claim query of ADR-0003 section 10, exactly as it was before P4-02E."""
    sql, parameters = frozen_claim_sql(processor_ids)
    bound = (
        int(now_ms),
        window,
        int(now_ms),
        *parameters,
        int(now_ms),
        window,
        int(now_ms),
        *parameters,
        max(int(limit), 1),
    )
    return [
        (int(row["job_id"]), int(row["effective_priority"]))
        for row in connection.execute(sql, bound).fetchall()
    ]


def frozen_claim_sql(processor_ids: Sequence[str] | None) -> tuple[str, list[str]]:
    """The frozen SQL text plus its processor filter parameters."""
    filter_sql, parameters = frozen_processor_filter(processor_ids)
    sql = (
        f"SELECT {jobs.JOB_COLUMNS}, "
        "(priority + ((? - created_at) / ?)) AS effective_priority "
        "FROM jobs WHERE state = 'QUEUED' AND run_after <= ?"
        f"{filter_sql} "
        "UNION ALL "
        f"SELECT {jobs.JOB_COLUMNS}, "
        "(priority + ((? - created_at) / ?)) AS effective_priority "
        "FROM jobs WHERE state = 'RUNNING' AND lease_expires_at <= ?"
        f"{filter_sql} "
        "ORDER BY effective_priority DESC, created_at ASC, job_id ASC LIMIT ?"
    )
    return sql, parameters


def product_page(
    connection,
    *,
    processor_ids: Sequence[str] | None,
    now_ms: int = NOW,
    window: int = WINDOW,
    limit: int = LIMIT,
) -> list[tuple[int, int]]:
    rows = jobs.claimable_candidates(
        connection,
        now_ms=int(now_ms),
        processor_ids=processor_ids,
        aging_window_ms=int(window),
        limit=int(limit),
    )
    return [(int(row["job_id"]), int(row["effective_priority"])) for row in rows]


def product_row_page(
    connection,
    *,
    processor_ids: Sequence[str] | None,
    now_ms: int = NOW,
    window: int = WINDOW,
    limit: int = LIMIT,
) -> list:
    """The product page as raw rows, for the claim-sequence test."""
    return jobs.claimable_candidates(
        connection,
        now_ms=int(now_ms),
        processor_ids=processor_ids,
        aging_window_ms=int(window),
        limit=int(limit),
    )


def frozen_row_page(
    connection,
    *,
    processor_ids: Sequence[str] | None,
    now_ms: int = NOW,
    window: int = WINDOW,
    limit: int = LIMIT,
) -> list:
    """The frozen page as raw rows, for the claim-sequence test."""
    sql, parameters = frozen_claim_sql(processor_ids)
    bound = (
        int(now_ms),
        window,
        int(now_ms),
        *parameters,
        int(now_ms),
        window,
        int(now_ms),
        *parameters,
        max(int(limit), 1),
    )
    return list(connection.execute(sql, bound).fetchall())


# ----------------------------------------------------------------------------- fixtures
def open_catalog(tmp_path: Path) -> Catalog:
    return Catalog.open(tmp_path / "data" / "catalog" / "catalog.db", lease_seconds=60)


def insert_rows(connection, rows: Sequence[tuple]) -> None:
    """Insert raw ``jobs`` rows; the claim reads columns, not the enqueue API."""
    connection.executemany(
        "INSERT INTO jobs(subject_type, subject_id, input_revision, task_type, processor_id, "
        "state, priority, max_attempts, run_after, created_at, updated_at, lease_expires_at) "
        "VALUES ('asset', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def backlog_rows(
    count: int,
    *,
    priority: int = 0,
    state: str = jobs.STATE_QUEUED,
    tag: str = "",
) -> list[tuple]:
    """A one-processor backlog spread over five aging windows (the burst shape).

    ``tag`` keeps the five-field logical job identity distinct when one test composes several
    backlogs into the same catalog.
    """
    span_ms = 5 * WINDOW
    rows = []
    for index in range(int(count)):
        created_at = NOW - span_ms + (index * span_ms) // max(int(count), 1)
        lease = NOW - 1 if state == jobs.STATE_RUNNING else None
        rows.append(
            (
                f"{tag}{index + 1}",
                f"rev-{tag}{index + 1}",
                TASK_TYPE,
                PROCESSOR,
                state,
                priority,
                3,
                NOW - span_ms,
                int(created_at),
                int(created_at),
                lease,
            )
        )
    return rows


def random_state(rng: random.Random, rows: int) -> list[tuple]:
    """One randomised state of the DS-H matrix (mixed priorities, both sides, future times)."""
    values = []
    for index in range(int(rows)):
        state = rng.choice(
            [
                jobs.STATE_QUEUED,
                jobs.STATE_QUEUED,
                jobs.STATE_QUEUED,
                jobs.STATE_RUNNING,
                jobs.STATE_RUNNING,
                jobs.STATE_SUCCEEDED,
                jobs.STATE_RETRY_WAIT,
            ]
        )
        priority = rng.choice([0, 0, 0, 1, 2, -3, 5])
        # ``created_at`` spans past and future; some rows share the exact millisecond.
        created_at = (
            NOW
            + rng.choice(
                [
                    -3_600_000,
                    -600_001,
                    -600_000,
                    -599_999,
                    -120_000,
                    -60_000,
                    -59_999,
                    -1,
                    0,
                    1,
                    90_000,
                ]
            )
            + rng.choice([0, 0, 3])
        )
        run_after = NOW + rng.choice([-1_000, 0, 1_000, 900_000])
        lease = None
        if state == jobs.STATE_RUNNING:
            lease = NOW + rng.choice([-5_000, -1, 0, 1, 500_000])
        values.append(
            (
                str(index + 1),
                f"rev-{index + 1}",
                TASK_TYPE,
                rng.choice([PROCESSOR, PROCESSOR, "other-processor@1"]),
                state,
                priority,
                3,
                int(run_after),
                int(created_at),
                int(created_at),
                None if lease is None else int(lease),
            )
        )
    return values


def fill(catalog: Catalog, rows: Sequence[tuple]) -> None:
    with catalog.write() as connection:
        connection.execute("DELETE FROM jobs")
        connection.execute("DELETE FROM sqlite_sequence WHERE name = 'jobs'")
        insert_rows(connection, rows)


# ------------------------------------------------------------------ exactness of the page
def test_the_bounded_claim_is_item_for_item_the_frozen_page_on_every_random_state(
    tmp_path: Path,
) -> None:
    rng = random.Random(20260924)
    compared = 0
    catalog = open_catalog(tmp_path)
    try:
        connection = catalog.connection
        for trial in range(TRIALS):
            fill(catalog, random_state(rng, ROWS_PER_TRIAL))
            for limit in LIMITS:
                for window in WINDOWS:
                    compared += 1
                    wanted = frozen_page(
                        connection, processor_ids=[PROCESSOR], window=window, limit=limit
                    )
                    got = product_page(
                        connection, processor_ids=[PROCESSOR], window=window, limit=limit
                    )
                    assert got == wanted, (
                        f"trial {trial} limit {limit} window {window}: "
                        f"frozen {wanted[:6]} != bounded {got[:6]}"
                    )
        # The DS-H matrix: 240 states x 6 limits x 3 aging windows.
        assert compared == TRIALS * len(LIMITS) * len(WINDOWS)
    finally:
        catalog.close()


def test_the_processor_scope_is_exact(tmp_path: Path) -> None:
    """``None``, the empty registry, several ids and an unknown id all keep their meaning."""
    catalog = open_catalog(tmp_path)
    try:
        fill(catalog, backlog_rows(40) + backlog_rows(10, priority=3, tag="p3-"))
        with catalog.write() as connection:
            insert_rows(
                connection,
                [
                    (
                        "foreign-1",
                        "rev-foreign",
                        TASK_TYPE,
                        "other-processor@1",
                        jobs.STATE_QUEUED,
                        9,
                        3,
                        NOW - 1_000,
                        NOW - 1_000,
                        NOW - 1_000,
                        None,
                    )
                ],
            )
        connection = catalog.connection
        scopes: tuple[Sequence[str] | None, ...] = (
            None,
            [],
            [PROCESSOR],
            [PROCESSOR, "other-processor@1"],
            ["no-such-processor@1"],
        )
        for scope in scopes:
            wanted = frozen_page(connection, processor_ids=scope)
            got = product_page(connection, processor_ids=scope)
            assert got == wanted, f"scope {scope!r}: frozen {wanted[:6]} != bounded {got[:6]}"
            # The empty registry claims nothing, and ``None`` really does mean "every processor".
            assert product_page(connection, processor_ids=[]) == []
            everything = [job_id for job_id, _ in product_page(connection, processor_ids=None)]
            scoped = [job_id for job_id, _ in product_page(connection, processor_ids=[PROCESSOR])]
            assert len(everything) == 51
            assert 51 in everything, "the foreign processor row must be claimable without a scope"
            assert 51 not in scoped
    finally:
        catalog.close()


def test_the_same_state_and_sequence_survive_a_restart(tmp_path: Path) -> None:
    rng = random.Random(20260925)
    state = random_state(rng, ROWS_PER_TRIAL)
    catalog = open_catalog(tmp_path)
    try:
        fill(catalog, state)
        before = product_page(catalog.connection, processor_ids=[PROCESSOR])
        frozen = frozen_page(catalog.connection, processor_ids=[PROCESSOR])
    finally:
        catalog.close()

    reopened = open_catalog(tmp_path)
    try:
        after = product_page(reopened.connection, processor_ids=[PROCESSOR])
        again = frozen_page(reopened.connection, processor_ids=[PROCESSOR])
    finally:
        reopened.close()
    assert before == frozen == after == again
    # A random state holds fewer than one claim breadth of runnable rows; the sequence must be
    # the same page on both sides of the restart, not merely the same length.
    assert 0 < len(after) <= LIMIT


#: Claim breadth of the sequence test: small enough to produce many pages, large enough that
#: the page bound, the class walk and the ties all matter.
SEQUENCE_LIMIT = 32


def seed_drain_state(catalog: Catalog) -> None:
    """A deterministic mixed backlog; two catalogs seeded this way are row-for-row identical."""
    edge = []
    for index in range(60):
        edge.append(
            (
                f"edge-{index}",
                f"rev-edge-{index}",
                TASK_TYPE,
                PROCESSOR if index % 4 else "other-processor@1",
                jobs.STATE_QUEUED,
                0 if index % 2 else -1,
                3,
                NOW - 1_000,
                # Same-millisecond rows plus one foreign processor: the two tie-break keys and
                # the processor scope must both survive the drain.
                NOW - 60_000 + (index % 5),
                NOW - 60_000,
                None,
            )
        )
    fill(
        catalog,
        backlog_rows(600, tag="q0-")
        + backlog_rows(150, priority=3, tag="q3-")
        + backlog_rows(100, state=jobs.STATE_RUNNING, tag="run-")
        + edge,
    )


def drain_claimed_sequence(catalog: Catalog, *, page, limit: int = SEQUENCE_LIMIT) -> list[int]:
    """Claim the best runnable row repeatedly through the scheduler's own claim steps."""
    sequence: list[int] = []
    while True:
        with catalog.write() as connection:
            jobs.requeue_due_retries(connection, now_ms=NOW, processor_ids=[PROCESSOR])
            jobs.fail_exhausted_leases(connection, now_ms=NOW, processor_ids=[PROCESSOR])
            rows = page(connection, processor_ids=[PROCESSOR], limit=limit)
            if not rows:
                return sequence
            claimed = jobs.claim_job(
                connection,
                job_id=int(rows[0]["job_id"]),
                worker_id="p4-02e-sequence",
                now_ms=NOW,
                lease_seconds=3_600,
            )
        sequence.append(int(claimed.job_id))


def end_state(catalog: Catalog) -> list[tuple[str, str, int]]:
    return sorted(
        (str(row["subject_id"]), str(row["state"]), int(row["attempts"]))
        for row in catalog.connection.execute("SELECT subject_id, state, attempts FROM jobs")
    )


def test_the_claimed_sequence_is_identical_to_the_frozen_sequence(tmp_path: Path) -> None:
    """Requirement 2 at sequence level: the *order of claims* must not move either.

    Two independently created catalogs hold the same rows; each is drained through the real
    claim path (the scheduler's prefilter statements plus ``claim_job``), one driven by the
    product page and one by the frozen pre-P4-02E page. Both the emitted ``job_id`` sequence
    and the end state (state plus ``attempts``, i.e. lease takeovers) must agree item by item.
    """
    product_catalog = open_catalog(tmp_path / "product")
    frozen_catalog = open_catalog(tmp_path / "frozen")
    try:
        seed_drain_state(product_catalog)
        seed_drain_state(frozen_catalog)
        product_fingerprint = end_state(product_catalog)
        frozen_fingerprint = end_state(frozen_catalog)
        assert product_fingerprint == frozen_fingerprint
        assert product_page(product_catalog.connection, processor_ids=[PROCESSOR]) == (
            frozen_page(frozen_catalog.connection, processor_ids=[PROCESSOR])
        )
        product_sequence = drain_claimed_sequence(product_catalog, page=product_row_page)
        frozen_sequence = drain_claimed_sequence(frozen_catalog, page=frozen_row_page)
        assert len(product_sequence) > 100
        assert product_sequence == frozen_sequence
        assert end_state(product_catalog) == end_state(frozen_catalog)
    finally:
        product_catalog.close()
        frozen_catalog.close()


# ------------------------------------------------------------------------- access path
def test_the_claim_plan_uses_0016_and_never_sorts_either_side(tmp_path: Path) -> None:
    """Requirement 1: no ``USE TEMP B-TREE FOR ORDER BY`` on either runnable side."""
    catalog = open_catalog(tmp_path)
    try:
        fill(catalog, backlog_rows(5_000) + backlog_rows(300, priority=2, tag="p2-"))
        with catalog.write() as connection:
            insert_rows(connection, backlog_rows(120, state=jobs.STATE_RUNNING, tag="run-"))
        connection = catalog.connection
        parameters = [PROCESSOR]
        filter_sql, bound_filter = jobs._processor_filter(parameters)
        plans: dict[str, list[str]] = {}
        for state, condition in jobs._CLAIM_SIDES:
            plans[f"{state}:priority_first"] = [
                str(row[3])
                for row in connection.execute(
                    f"EXPLAIN QUERY PLAN "
                    f"{jobs._claim_priority_sql(condition, filter_sql, after_cursor=False)}",
                    (state, NOW, *bound_filter),
                ).fetchall()
            ]
            plans[f"{state}:priority_next"] = [
                str(row[3])
                for row in connection.execute(
                    f"EXPLAIN QUERY PLAN "
                    f"{jobs._claim_priority_sql(condition, filter_sql, after_cursor=True)}",
                    (state, 0, NOW, *bound_filter),
                ).fetchall()
            ]
            plans[f"{state}:class"] = [
                str(row[3])
                for row in connection.execute(
                    f"EXPLAIN QUERY PLAN {jobs._claim_class_sql(condition, filter_sql)}",
                    (NOW, WINDOW, state, 0, NOW, *bound_filter, LIMIT),
                ).fetchall()
            ]
        for name, plan in plans.items():
            joined = " | ".join(plan)
            assert "TEMP B-TREE" not in joined, f"{name} still sorts: {joined}"
            assert jobs._CLAIM_INDEX in joined, f"{name} does not use 0016: {joined}"
        # The frozen query is the counterexample: it *always* sorts the whole runnable set.
        frozen_sql, frozen_bound = frozen_claim_sql([PROCESSOR])
        frozen_plan = [
            str(row[3])
            for row in connection.execute(
                f"EXPLAIN QUERY PLAN {frozen_sql}",
                (NOW, WINDOW, NOW, *frozen_bound, NOW, WINDOW, NOW, *frozen_bound, LIMIT),
            ).fetchall()
        ]
        assert sum("USE TEMP B-TREE FOR ORDER BY" in line for line in frozen_plan) == 2
    finally:
        catalog.close()


#: The prefilter guard's backlog. It must be large *and* analysed: below ~50 000 rows the
#: planner picks ``idx_jobs_processor_state`` for both statements even when the two partial
#: indexes are missing, so a smaller fixture cannot tell "index present" from "index dropped"
#: (P4-02E review finding N1).
PREFILTER_GUARD_BACKLOG = 50_000
PREFILTER_STATEMENTS = {
    "requeue_due_retries": (
        "UPDATE jobs SET state = ?, updated_at = ? WHERE state = ? AND run_after <= ?"
        " AND processor_id IN (?)",
        "idx_jobs_retry_due",
    ),
    "fail_exhausted_leases": (
        "SELECT job_id, subject_type, subject_id, attempts, max_attempts FROM jobs "
        "WHERE state = ? AND lease_expires_at <= ? AND attempts + 1 >= max_attempts"
        " AND processor_id IN (?)",
        "idx_jobs_expired_lease",
    ),
}


def prefilter_plans(connection) -> dict[str, list[str]]:
    bound = {
        "requeue_due_retries": (
            jobs.STATE_QUEUED,
            NOW,
            jobs.STATE_RETRY_WAIT,
            NOW,
            PROCESSOR,
        ),
        "fail_exhausted_leases": (jobs.STATE_RUNNING, NOW, PROCESSOR),
    }
    return {
        name: [
            str(row[3])
            for row in connection.execute(f"EXPLAIN QUERY PLAN {sql}", bound[name]).fetchall()
        ]
        for name, (sql, _index) in PREFILTER_STATEMENTS.items()
    }


def test_the_prefilters_of_the_claim_use_their_partial_indexes(tmp_path: Path) -> None:
    """Requirement 4: the claim's own prefilter statements must stay bounded.

    The assertion names the two partial indexes of ``0016`` instead of only ruling out
    ``SCAN jobs``: at a smaller fixture (and without ``ANALYZE``) SQLite happens to pick
    ``idx_jobs_processor_state`` whether the partial indexes exist or not, so a
    "no SCAN jobs" assertion would keep passing with ``0016``'s indexes deleted. The
    control below proves this fixture *can* tell the difference.
    """
    catalog = open_catalog(tmp_path)
    try:
        # All rows share one state on purpose: that is the burst shape whose statistics make
        # the planner consider the whole-index/whole-table paths F1b/F1c removed, so both
        # partial indexes are chosen deterministically here (mixed states would let the
        # planner seek ``idx_jobs_processor_state`` instead and hide the regression).
        fill(catalog, backlog_rows(PREFILTER_GUARD_BACKLOG))
        with catalog.write() as writer:
            writer.execute("ANALYZE")
        connection = catalog.connection
        with_indexes = prefilter_plans(connection)
        for name, (_sql, index) in PREFILTER_STATEMENTS.items():
            joined = " | ".join(with_indexes[name])
            assert index in joined, f"{name} does not use {index}: {joined}"
            assert not any(line.startswith("SCAN jobs") for line in with_indexes[name]), (
                f"{name} scans: {joined}"
            )
        with catalog.write() as writer:
            for _sql, index in PREFILTER_STATEMENTS.values():
                writer.execute(f"DROP INDEX {index}")
        without_indexes = prefilter_plans(connection)
        # Control (non-vacuity): with the indexes gone the plans must actually change, and the
        # exhaustion side must fall back to the whole-table scan this card removed.
        for name in PREFILTER_STATEMENTS:
            assert without_indexes[name] != with_indexes[name], name
        assert any(
            line.startswith("SCAN jobs") for line in without_indexes["fail_exhausted_leases"]
        ), without_indexes["fail_exhausted_leases"]
        with catalog.write() as writer:
            writer.execute(
                "CREATE INDEX idx_jobs_retry_due ON jobs(run_after) WHERE state = 'RETRY_WAIT'"
            )
            writer.execute(
                "CREATE INDEX idx_jobs_expired_lease ON jobs(lease_expires_at) "
                "WHERE state = 'RUNNING' AND max_attempts <= attempts + 1"
            )
    finally:
        catalog.close()


# ------------------------------------------------------- lease / qualification / pause
def processor_spec() -> ProcessorSpec:
    return ProcessorSpec.from_envelope(
        {
            "contract_version": CONTRACT_VERSION,
            "spec_revision": "p4-02e-claim-v1",
            "processor_id": PROCESSOR,
            "processor_kind": "non_model",
            "task_type": TASK_TYPE,
            "config_hash": "b" * 64,
            "input_type": "asset_original",
            "output_type": "geo_fact.v1",
            "device_policy": "CPU_PREFERRED",
            "runtime_package_id": PY_PACKAGE,
            "supported_devices": ["cpu"],
            "supported_precisions": ["not_applicable"],
            "runtime_backend": "python",
            "max_batch_size": 32,
            "cancellation_supported": True,
            "resource_measurements": [],
        }
    )


def python_capability(*, now_ms: int = 1_000) -> RuntimeCapabilities:
    return RuntimeCapabilities.seal(
        {
            "runtime_package_id": PY_PACKAGE,
            "runtime_backend": "python",
            "runtime_package_name": "photo-search-runtime",
            "runtime_package_version": "1",
            "python_version": "3.12.10",
            "platform": "windows-x86_64",
            "driver_version": None,
            "providers": [
                {
                    "name": "python-stdlib",
                    "device": "cpu",
                    "precisions": ["not_applicable"],
                    "available": True,
                    "health": "healthy",
                    "health_checked_at": int(now_ms),
                    "evidence_id": None,
                }
            ],
            "health_status": "healthy",
            "health_checked_at": int(now_ms),
            "measured_at": int(now_ms),
            "evidence_ids": [],
            "failure_code": None,
        }
    )


def qualified_scheduler(catalog: Catalog, *, now_ms: int = 1_000) -> engine.Scheduler:
    spec = processor_spec()
    scheduler = engine.Scheduler(catalog, [spec], lease_seconds=60)
    scheduler.record_capability(python_capability(now_ms=now_ms), now_ms=now_ms)
    scheduler.record_qualification(
        processor_id=spec.processor_id,
        spec_revision=spec.spec_revision,
        device="cpu",
        precision="not_applicable",
        provider_name="python-stdlib",
        runtime_package_id=PY_PACKAGE,
        now_ms=now_ms,
    )
    return scheduler


def test_an_unqualified_processor_is_still_blocked_on_a_large_page(tmp_path: Path) -> None:
    catalog = open_catalog(tmp_path)
    try:
        # A backlog larger than one claim breadth: the bounded page must still reach the
        # qualification gate and block exactly one job without consuming an attempt.
        fill(catalog, backlog_rows(300))
        scheduler = engine.Scheduler(catalog, [processor_spec()], lease_seconds=60)
        assert scheduler.claim_next(worker_id="w1", now_ms=NOW - 10_000) is None
        counts = catalog.read_all("SELECT state, COUNT(*) AS total FROM jobs GROUP BY state")
        # The frozen scheduler semantics: one claim call walks its whole candidate page and
        # blocks every unqualified job it finds there (MAX_CANDIDATES_PER_CLAIM = 256), so the
        # bounded page must block exactly that many - and never more than the page.
        assert {str(row["state"]): int(row["total"]) for row in counts} == {
            "BLOCKED": LIMIT,
            "QUEUED": 300 - LIMIT,
        }
        blocked = catalog.read_one(
            "SELECT attempts, blocking_reason FROM jobs WHERE state = 'BLOCKED'"
        )
        assert int(blocked["attempts"]) == 0
        assert str(blocked["blocking_reason"]) == "RUNTIME_CAPABILITY_UNAVAILABLE"
    finally:
        catalog.close()


def test_an_expired_lease_takeover_still_consumes_the_attempt(tmp_path: Path) -> None:
    catalog = open_catalog(tmp_path)
    try:
        # One expired execution with no retry budget left, behind a backlog larger than one
        # claim breadth: the takeover must still run, count one lost execution and terminate.
        with catalog.write() as connection:
            connection.execute(
                "INSERT INTO jobs(subject_type, subject_id, input_revision, "
                "task_type, processor_id, state, priority, attempts, max_attempts, run_after, "
                "lease_token, lease_expires_at, worker_id, created_at, updated_at) VALUES "
                "('asset', 'expired', 'rev-1', ?, ?, 'RUNNING', 0, 0, 1, ?, 'stale-token', ?, "
                "'old-worker', ?, ?)",
                (TASK_TYPE, PROCESSOR, NOW - 10_000, NOW - 1_000, NOW - 10_000, NOW - 10_000),
            )
            insert_rows(connection, backlog_rows(300))
        scheduler = qualified_scheduler(catalog, now_ms=NOW - 10_000)
        # max_attempts=1 has no budget left for the takeover, so the expired execution is
        # terminated with one lost attempt and the claim continues with the next candidate.
        claimed = scheduler.claim_next(worker_id="w2", now_ms=NOW)
        assert claimed is not None and claimed.subject_id == "1"
        failed = catalog.read_one(
            "SELECT attempts, last_error_code FROM jobs WHERE subject_id = 'expired'"
        )
        assert int(failed["attempts"]) == 1
        assert str(failed["last_error_code"]) == LEASE_EXPIRED
    finally:
        catalog.close()


def test_a_paused_scheduler_still_refuses_claims(tmp_path: Path) -> None:
    catalog = open_catalog(tmp_path)
    try:
        fill(catalog, backlog_rows(300))
        scheduler = qualified_scheduler(catalog, now_ms=NOW - 10_000)
        scheduler.pause(now_ms=NOW - 5_000)
        with pytest.raises(CatalogError) as paused:
            scheduler.claim_next(worker_id="w1", now_ms=NOW)
        assert paused.value.code == SCHEDULER_PAUSED
        assert (
            catalog.read_one("SELECT COUNT(*) AS total FROM jobs WHERE state = 'QUEUED'")["total"]
            == 300
        )
    finally:
        catalog.close()


# ------------------------------------------------------------------ mutation controls
def mutant_pages(connection) -> dict[str, list[tuple[int, int]]]:
    """Three deliberate mutations of the bounded algorithm, for the control test."""
    filter_sql, parameters = jobs._processor_filter([PROCESSOR])
    classes: list[tuple[str, str, int]] = []
    for state, condition in jobs._CLAIM_SIDES:
        row = connection.execute(
            jobs._claim_priority_sql(condition, filter_sql, after_cursor=False),
            (state, NOW, *parameters),
        ).fetchone()
        while row is not None:
            priority = int(row["priority"])
            classes.append((state, condition, priority))
            row = connection.execute(
                jobs._claim_priority_sql(condition, filter_sql, after_cursor=True),
                (state, priority, NOW, *parameters),
            ).fetchone()
    rows: list[Any] = []
    per_class: dict[tuple[str, int], list[Any]] = {}
    for state, condition, priority in classes:
        found = connection.execute(
            jobs._claim_class_sql(condition, filter_sql),
            (NOW, WINDOW, state, priority, NOW, *parameters, LIMIT),
        ).fetchall()
        rows.extend(found)
        per_class[(state, priority)] = list(found)

    def as_pairs(selection: Sequence[Any]) -> list[tuple[int, int]]:
        return [(int(row["job_id"]), int(row["effective_priority"])) for row in selection]

    m2 = sorted(rows, key=lambda row: (-int(row["effective_priority"]), int(row["created_at"])))
    m3 = sorted(
        rows,
        key=lambda row: (
            int(row["created_at"]),
            int(row["job_id"]),
            -int(row["effective_priority"]),
        ),
    )
    m4 = sorted(
        rows,
        key=lambda row: (
            -int(row["effective_priority"]),
            int(row["job_id"]),
            int(row["created_at"]),
        ),
    )
    return {
        "no_candidate_bound": as_pairs(
            per_class.get((jobs.STATE_QUEUED, min(item[2] for item in classes)), [])
        ),
        "unstable_tie_break": as_pairs(m2)[:LIMIT],
        "created_at_first": as_pairs(m3)[:LIMIT],
        "job_id_before_created_at": as_pairs(m4)[:LIMIT],
    }


def test_the_equivalence_case_is_not_vacuous(tmp_path: Path) -> None:
    """Removing the bound, the adjudication or a sort key must change the page."""
    catalog = open_catalog(tmp_path)
    try:
        # One designed state that exposes all four mutations at once:
        #   job 1 = expired RUNNING lease, priority 5, created NOW-120s  -> effective 7
        #   job 2 = QUEUED,            priority 5, created NOW-120s  -> effective 7 (tie with 1)
        #   job 3 = QUEUED,            priority 1, created NOW-60s   -> effective 2
        #   job 4 = QUEUED,            priority 0, created NOW-120s  -> effective 2 (older than 3)
        #   job 5 = QUEUED,            priority 5, created NOW-1s    -> effective 5
        #   jobs 6..15 = QUEUED,       priority 0, created NOW-300s  -> effective 5 (filler)
        rows = [
            (
                "running",
                "rev-running",
                TASK_TYPE,
                PROCESSOR,
                jobs.STATE_RUNNING,
                5,
                3,
                NOW - 1_000,
                NOW - 120_000,
                NOW - 120_000,
                NOW - 1,
            ),
            (
                "queued-5-old",
                "rev-1",
                TASK_TYPE,
                PROCESSOR,
                jobs.STATE_QUEUED,
                5,
                3,
                NOW - 1_000,
                NOW - 120_000,
                NOW - 120_000,
                None,
            ),
            (
                "queued-1",
                "rev-2",
                TASK_TYPE,
                PROCESSOR,
                jobs.STATE_QUEUED,
                1,
                3,
                NOW - 1_000,
                NOW - 60_000,
                NOW - 60_000,
                None,
            ),
            (
                "queued-0-old",
                "rev-3",
                TASK_TYPE,
                PROCESSOR,
                jobs.STATE_QUEUED,
                0,
                3,
                NOW - 1_000,
                NOW - 120_000,
                NOW - 120_000,
                None,
            ),
            (
                "queued-5-fresh",
                "rev-4",
                TASK_TYPE,
                PROCESSOR,
                jobs.STATE_QUEUED,
                5,
                3,
                NOW - 1_000,
                NOW - 1_000,
                NOW - 1_000,
                None,
            ),
        ]
        for index in range(10):
            rows.append(
                (
                    f"filler-{index}",
                    f"rev-filler-{index}",
                    TASK_TYPE,
                    PROCESSOR,
                    jobs.STATE_QUEUED,
                    0,
                    3,
                    NOW - 1_000,
                    NOW - 300_000,
                    NOW - 300_000,
                    None,
                )
            )
        fill(catalog, rows)
        connection = catalog.connection
        frozen = frozen_page(connection, processor_ids=[PROCESSOR])
        bounded = product_page(connection, processor_ids=[PROCESSOR])
        assert bounded == frozen
        mutants = mutant_pages(connection)
        for name, page in mutants.items():
            assert page != frozen, f"mutant {name} was not detected: {page[:6]}"
            assert page != bounded
    finally:
        catalog.close()
