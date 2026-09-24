"""P4-02E / F1 probe: cost, query plan and exactness of the semantic-backlog claim.
``jobs.claimable_candidates`` used to materialise and sort the *whole* runnable backlog on
every claim (both ``UNION ALL`` sides showed ``USE TEMP B-TREE FOR ORDER BY``), which made a
one-shot semantic burst quadratic: the DS-H review of P4-02B measured 2.30 / 11.13 / 21.85 /
117.95 ms per claim at 10 000 / 50 000 / 100 000 / 300 000 queued rows. This probe is the
permanent form of that review probe (``tmp/p402b-dsh/f1_claim_probe.py``) and answers three
questions on a throw-away synthetic catalog under ``tmp/``:
1. **Cost** - per-claim median / P95 of the *product* entry ``jobs.claimable_candidates`` and
   of the frozen pre-P4-02E query, at each backlog scale, plus the wall time of a real
   ``Scheduler.claim_next`` write transaction (the claim holds ``BEGIN IMMEDIATE`` through
   ``Catalog.write()``, so this is the write-lock hold time of the claim).
2. **Plan** - ``EXPLAIN QUERY PLAN`` for the frozen query and for every statement the bounded
   claim runs. The bounded class lookup must search the P4-02E index, never sort.
3. **Exactness** - the product page must be *identical* (same ``job_id`` order, same
   ``effective_priority`` values) to the frozen query on randomised states: mixed priorities
   including negatives, same-millisecond rows, ``created_at`` in the future, ``run_after`` in
   the future, expired and live leases, foreign ``processor_id`` rows, several ``limit`` and
   aging-window values.
The frozen semantics (ADR-0003 section 10, architecture 6.4) are reproduced here
*independently* of the product module, so the comparison is a real cross-check rather than a
tautology: the question is whether the rewritten access path still returns the same page.
The catalog is built with the real migrations of the build under test, so running this probe
on a pre-P4-02E checkout measures the "before" side (no claim index, whole-backlog sort) and
running it after the change measures the "after" side on exactly the same fixture.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ann_scale_benchmark as bench  # noqa: E402
import capacity_validation as capacity  # noqa: E402  (the shared diagnostic scheduler setup)

from photo_search.catalog import schema  # noqa: E402
from photo_search.catalog.db import Catalog  # noqa: E402
from photo_search.jobs import queue as job_queue  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
#: Fixed wall-clock origin so two runs of this probe are comparable (same as the DS-H probe).
NOW = 1_790_244_000_000
WINDOW = 60_000
#: ``MAX_CANDIDATES_PER_CLAIM``: the scheduler's own claim breadth.
LIMIT = 256
PROCESSOR = bench.PROCESSOR_ID
TASK_TYPE = "image_embedding"
#: The additive of ``0016``: the covering index the bounded claim seeks into.
CLAIM_INDEX = "idx_jobs_claim_order"
#: Candidate indexes the probe creates (and drops) on a throw-away tree to *measure* which
#: access path the two quadratic steps really need. Only the migration of the build under
#: test decides what ships; this list is diagnostic evidence, never a product change.
CANDIDATE_INDEXES: dict[str, str] = {
    CLAIM_INDEX: ("CREATE INDEX idx_jobs_claim_order ON jobs(state, priority, created_at, job_id)"),
    "idx_jobs_lease_expiry": (
        "CREATE INDEX idx_jobs_lease_expiry ON jobs(state, lease_expires_at)"
    ),
}


def fresh_catalog(root: Path) -> Catalog:
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    return Catalog.open(data_root=root)


def backlog_rows(count: int, *, now_ms: int = NOW) -> list[tuple]:
    """One-processor backlog spread over five aging windows (the burst shape of F1)."""
    span_ms = 5 * WINDOW
    rows = []
    for index in range(int(count)):
        created_at = now_ms - span_ms + (index * span_ms) // max(int(count), 1)
        rows.append(
            (
                "asset",
                str(index + 1),
                f"rev-{index + 1}",
                TASK_TYPE,
                PROCESSOR,
                job_queue.STATE_QUEUED,
                0,
                3,
                now_ms - span_ms,
                int(created_at),
                int(created_at),
            )
        )
    return rows


def insert_rows(connection, rows: Sequence[tuple]) -> None:
    connection.executemany(
        "INSERT INTO jobs(subject_type, subject_id, input_revision, task_type, processor_id, "
        "state, priority, max_attempts, run_after, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def random_state(rng: random.Random, rows: int) -> list[tuple]:
    """Randomised mixed state used by the exactness cross-check."""
    values = []
    for index in range(int(rows)):
        state = rng.choice(
            [
                job_queue.STATE_QUEUED,
                job_queue.STATE_QUEUED,
                job_queue.STATE_QUEUED,
                job_queue.STATE_RUNNING,
                job_queue.STATE_RUNNING,
                job_queue.STATE_SUCCEEDED,
                job_queue.STATE_RETRY_WAIT,
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
        lease: int | None = None
        if state == job_queue.STATE_RUNNING:
            # The schema CHECK keeps leases on RUNNING rows only.
            lease = NOW + rng.choice([-5_000, -1, 0, 1, 500_000])
        values.append(
            (
                "asset",
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


def insert_state_rows(connection, rows: Sequence[tuple]) -> None:
    connection.executemany(
        "INSERT INTO jobs(subject_type, subject_id, input_revision, task_type, processor_id, "
        "state, priority, max_attempts, run_after, created_at, updated_at, lease_expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def frozen_claim_sql(processors: Sequence[str]) -> tuple[str, str]:
    """The pre-P4-02E claim query, textually frozen here.
    This is the semantics every bounded rewrite must reproduce item by item (ADR-0003
    section 10): effective priority, then the two stable tie-breaks, over the union of the
    QUEUED and the expired-RUNNING side.
    """
    filter_sql, _ = job_queue._processor_filter(processors)
    sql = (
        f"SELECT {job_queue.JOB_COLUMNS}, "
        "(priority + ((? - created_at) / ?)) AS effective_priority "
        "FROM jobs WHERE state = 'QUEUED' AND run_after <= ?"
        f"{filter_sql} "
        "UNION ALL "
        f"SELECT {job_queue.JOB_COLUMNS}, "
        "(priority + ((? - created_at) / ?)) AS effective_priority "
        "FROM jobs WHERE state = 'RUNNING' AND lease_expires_at <= ?"
        f"{filter_sql} "
        "ORDER BY effective_priority DESC, created_at ASC, job_id ASC LIMIT ?"
    )
    return sql, filter_sql


def frozen_parameters(
    *,
    processors: Sequence[str],
    now_ms: int = NOW,
    window: int = WINDOW,
    limit: int = LIMIT,
) -> tuple:
    _, parameters = job_queue._processor_filter(processors)
    return (
        int(now_ms),
        int(window),
        int(now_ms),
        *parameters,
        int(now_ms),
        int(window),
        int(now_ms),
        *parameters,
        max(int(limit), 1),
    )


def frozen_page(
    connection,
    *,
    processors: Sequence[str],
    now_ms: int = NOW,
    window: int = WINDOW,
    limit: int = LIMIT,
) -> list[tuple[int, int]]:
    sql, _ = frozen_claim_sql(processors)
    bound = frozen_parameters(processors=processors, now_ms=now_ms, window=window, limit=limit)
    return [
        (int(row["job_id"]), int(row["effective_priority"]))
        for row in connection.execute(sql, bound).fetchall()
    ]


def product_page(
    connection,
    *,
    processors: Sequence[str],
    now_ms: int = NOW,
    window: int = WINDOW,
    limit: int = LIMIT,
) -> list[tuple[int, int]]:
    rows = job_queue.claimable_candidates(
        connection,
        now_ms=int(now_ms),
        processor_ids=list(processors),
        aging_window_ms=int(window),
        limit=int(limit),
    )
    return [(int(row["job_id"]), int(row["effective_priority"])) for row in rows]


def explain(connection, sql: str, parameters: tuple) -> list[str]:
    return [
        str(row[3])
        for row in connection.execute(f"EXPLAIN QUERY PLAN {sql}", parameters).fetchall()
    ]


def summarize(samples: Sequence[float]) -> dict[str, Any]:
    values = sorted(float(value) for value in samples)
    if not values:
        return {"count": 0}
    index = min(len(values) - 1, int(round(0.95 * (len(values) - 1))))
    return {
        "count": len(values),
        "ms_median": round(statistics.median(values), 3),
        "ms_p95": round(values[index], 3),
        "ms_min": round(values[0], 3),
        "ms_max": round(values[-1], 3),
        "ms_mean": round(statistics.fmean(values), 3),
    }


def timeit(call: Callable[[], Any], samples: int) -> dict[str, Any]:
    timings = []
    for _ in range(max(int(samples), 1)):
        started = time.perf_counter()
        call()
        timings.append((time.perf_counter() - started) * 1000)
    return summarize(timings)


def product_statement_plans(
    connection,
    *,
    processors: Sequence[str],
    now_ms: int = NOW,
    window: int = WINDOW,
    limit: int = LIMIT,
) -> dict[str, Any]:
    """Plans of the bounded claim's own statements (unavailable on a pre-P4-02E build)."""
    priority_sql = getattr(job_queue, "_claim_priority_sql", None)
    class_sql = getattr(job_queue, "_claim_class_sql", None)
    if priority_sql is None or class_sql is None:
        return {"available": False, "reason": "this build still claims with the frozen query"}
    filter_sql, parameters = job_queue._processor_filter(processors)
    plans: dict[str, Any] = {
        "available": True,
        "claim_index": getattr(job_queue, "_CLAIM_INDEX", None),
        "sides": [],
    }
    for state, condition in job_queue._CLAIM_SIDES:
        plans["sides"].append(
            {
                "state": state,
                "priority_first": explain(
                    connection,
                    priority_sql(condition, filter_sql, after_cursor=False),
                    (state, int(now_ms), *parameters),
                ),
                "priority_next": explain(
                    connection,
                    priority_sql(condition, filter_sql, after_cursor=True),
                    (state, 0, int(now_ms), *parameters),
                ),
                "class": explain(
                    connection,
                    class_sql(condition, filter_sql),
                    (
                        int(now_ms),
                        int(window),
                        state,
                        0,
                        int(now_ms),
                        *parameters,
                        max(int(limit), 1),
                    ),
                ),
            }
        )
    return plans


def phase_breakdown(
    connection,
    *,
    processors: Sequence[str],
    now_ms: int = NOW,
    window: int = WINDOW,
    limit: int = LIMIT,
    samples: int = 5,
) -> dict[str, Any]:
    """Re-runs the bounded claim's phases separately to attribute its cost."""
    if getattr(job_queue, "_claim_class_sql", None) is None:
        return {"available": False}
    filter_sql, parameters = job_queue._processor_filter(processors)
    bound = max(int(limit), 1)
    enumeration: list[float] = []
    classes: list[float] = []
    sorts: list[float] = []
    classes_seen = 0
    for _ in range(max(int(samples), 1)):
        discovered: list[tuple[str, str, int]] = []
        started = time.perf_counter()
        for state, condition in job_queue._CLAIM_SIDES:
            row = connection.execute(
                job_queue._claim_priority_sql(condition, filter_sql, after_cursor=False),
                (state, int(now_ms), *parameters),
            ).fetchone()
            while row is not None:
                priority = int(row["priority"])
                discovered.append((state, condition, priority))
                row = connection.execute(
                    job_queue._claim_priority_sql(condition, filter_sql, after_cursor=True),
                    (state, priority, int(now_ms), *parameters),
                ).fetchone()
        enumeration.append((time.perf_counter() - started) * 1000)
        classes_seen = len(discovered)
        started = time.perf_counter()
        rows: list[Any] = []
        for state, condition, priority in discovered:
            rows.extend(
                connection.execute(
                    job_queue._claim_class_sql(condition, filter_sql),
                    (int(now_ms), int(window), state, priority, int(now_ms), *parameters, bound),
                ).fetchall()
            )
        classes.append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        rows.sort(
            key=lambda row: (
                -int(row["effective_priority"]),
                int(row["created_at"]),
                int(row["job_id"]),
            )
        )
        rows[:bound]
        sorts.append((time.perf_counter() - started) * 1000)
    return {
        "available": True,
        "distinct_priority_classes": classes_seen,
        "class_enumeration": summarize(enumeration),
        "class_rows": summarize(classes),
        "python_sort": summarize(sorts),
    }


def index_facts(connection) -> list[dict[str, Any]]:
    return [
        {"name": str(row[0]), "sql": str(row[1])}
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'jobs' "
            "ORDER BY name"
        )
    ]


def prefilter_statements(processors: Sequence[str]) -> dict[str, tuple[str, tuple]]:
    """The statements ``Scheduler.claim_next`` runs before it lists candidates.

    They are reproduced here (read-only) so the probe can *attribute* the claim's
    write-hold time instead of assuming the candidate listing is the only expensive part.
    """
    filter_sql, parameters = job_queue._processor_filter(processors)
    return {
        "requeue_due_retries": (
            "UPDATE jobs SET state = ?, updated_at = ? WHERE state = ? AND run_after <= ?"
            f"{filter_sql}",
            (job_queue.STATE_QUEUED, NOW, job_queue.STATE_RETRY_WAIT, NOW, *parameters),
        ),
        "fail_exhausted_leases": (
            "SELECT job_id, subject_type, subject_id, attempts, max_attempts FROM jobs "
            "WHERE state = ? AND lease_expires_at <= ? AND attempts + 1 >= max_attempts"
            f"{filter_sql}",
            (job_queue.STATE_RUNNING, NOW, *parameters),
        ),
    }


def prefilter_cost(catalog: Catalog, *, processors: Sequence[str], samples: int) -> dict[str, Any]:
    """Plans and timings of the pre-claim steps plus one empty write transaction."""
    connection = catalog.connection
    plans = {
        name: explain(connection, sql, parameters)
        for name, (sql, parameters) in prefilter_statements(processors).items()
    }

    def run_prefilter() -> None:
        with catalog.write() as writer:
            job_queue.requeue_due_retries(writer, now_ms=NOW, processor_ids=list(processors))
            job_queue.fail_exhausted_leases(writer, now_ms=NOW, processor_ids=list(processors))

    def run_requeue_only() -> None:
        with catalog.write() as writer:
            job_queue.requeue_due_retries(writer, now_ms=NOW, processor_ids=list(processors))

    def run_fail_exhausted_only() -> None:
        with catalog.write() as writer:
            job_queue.fail_exhausted_leases(writer, now_ms=NOW, processor_ids=list(processors))

    def run_empty_transaction() -> None:
        with catalog.write():
            pass

    return {
        "plans": plans,
        "prefilter_in_write_tx": timeit(run_prefilter, samples),
        "requeue_only": timeit(run_requeue_only, samples),
        "fail_exhausted_only": timeit(run_fail_exhausted_only, samples),
        "empty_write_tx": timeit(run_empty_transaction, samples),
    }


def claim_transaction(catalog: Catalog, *, samples: int, now_ms: int = NOW) -> dict[str, Any]:
    """Wall time of a real ``Scheduler.claim_next`` (a full ``BEGIN IMMEDIATE`` claim).
    Each sample claims the best runnable job and then puts that single row back, so the
    backlog size stays constant across samples and the measured value is the claim's own
    write-lock hold time - which must not grow with the backlog either.
    """
    scheduler = capacity.prepare_scheduler(catalog, now=now_ms)
    timings: list[float] = []
    for _ in range(max(int(samples), 1)):
        started = time.perf_counter()
        request = scheduler.claim_next(worker_id="p4-02e-probe", now_ms=now_ms)
        timings.append((time.perf_counter() - started) * 1000)
        if request is None:  # pragma: no cover - the fixture always has runnable rows
            raise RuntimeError("the probe backlog had no runnable job")
        with catalog.write() as connection:
            connection.execute(
                "UPDATE jobs SET state = ?, lease_token = NULL, lease_expires_at = NULL, "
                "worker_id = NULL, attempts = 0, updated_at = ? WHERE job_id = ?",
                (job_queue.STATE_QUEUED, int(now_ms), int(request.job_id)),
            )
    return {"claim_next": summarize(timings), "lease_seconds": int(scheduler.lease_seconds)}


def index_candidate_experiment(
    catalog: Catalog, *, processors: Sequence[str], args: argparse.Namespace
) -> dict[str, Any]:
    """Measure each candidate index on the already-built throw-away backlog.

    The probe creates one candidate index, re-runs ``ANALYZE``, re-measures the plans and the
    two costs, then drops it again. That is what "以实测 EXPLAIN QUERY PLAN 为准" needs: the
    shipped migration contains only the index that demonstrably removes its own scan or sort.
    """
    connection = catalog.connection
    samples = args.samples
    result: dict[str, Any] = {}
    for name, create_sql in CANDIDATE_INDEXES.items():
        with catalog.write() as writer:
            writer.execute(create_sql)
            writer.execute("ANALYZE")
        sql, _ = frozen_claim_sql(processors)
        result[name] = {
            "sql": create_sql,
            "prefilter_plans": {
                statement: explain(connection, statement_sql, parameters)
                for statement, (statement_sql, parameters) in prefilter_statements(
                    processors
                ).items()
            },
            "frozen_query_plan": explain(connection, sql, frozen_parameters(processors=processors)),
            "prefilter": prefilter_cost(catalog, processors=processors, samples=samples),
            "cost_ms": {
                "product_claimable_candidates": timeit(
                    lambda: product_page(connection, processors=processors), samples
                ),
                "frozen_query": timeit(
                    lambda: frozen_page(connection, processors=processors), samples
                ),
            },
            "claim_transaction": claim_transaction(catalog, samples=samples),
        }
        with catalog.write() as writer:
            writer.execute(f"DROP INDEX {name}")
    return result


def scale_report(scale: int, *, args: argparse.Namespace, work: Path) -> dict[str, Any]:
    label = getattr(args, "label", None)
    root = work / (f"scale-{scale}" if not label else f"scale-{scale}-{label}")
    catalog = fresh_catalog(root / "data")
    try:
        started = time.perf_counter()
        rows = backlog_rows(scale)
        for offset in range(0, len(rows), 10_000):
            with catalog.write() as connection:
                insert_rows(connection, rows[offset : offset + 10_000])
        build_ms = round((time.perf_counter() - started) * 1000, 1)
        connection = catalog.connection
        processors = [PROCESSOR]
        runnable = int(
            connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE state = 'QUEUED' AND run_after <= ?", (NOW,)
            ).fetchone()[0]
        )
        if not getattr(args, "skip_analyze", False):
            with catalog.write() as writer:
                writer.execute("ANALYZE")
        sql, _ = frozen_claim_sql(processors)
        indexes = index_facts(connection)
        return {
            "scale": int(scale),
            "runnable": runnable,
            "fixture_build_ms": build_ms,
            "schema_version": int(catalog.schema_version),
            "claim_index_present": any(item["name"] == CLAIM_INDEX for item in indexes),
            "jobs_indexes": indexes,
            "plans": {
                "frozen_query": explain(connection, sql, frozen_parameters(processors=processors)),
                "product": product_statement_plans(connection, processors=processors),
            },
            "cost_ms": {
                "product_claimable_candidates": timeit(
                    lambda: product_page(connection, processors=processors), args.samples
                ),
                "frozen_query": timeit(
                    lambda: frozen_page(connection, processors=processors), args.samples
                ),
            },
            "phases_ms": phase_breakdown(
                connection, processors=processors, samples=min(int(args.samples), 5)
            ),
            "prefilter": prefilter_cost(catalog, processors=processors, samples=args.samples),
            "claim_transaction": claim_transaction(catalog, samples=args.samples),
            "index_candidates": (
                index_candidate_experiment(catalog, processors=processors, args=args)
                if getattr(args, "index_candidates", False)
                else None
            ),
        }
    finally:
        catalog.close()
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)


def equivalence(*, trials: int, rows: int, args: argparse.Namespace, work: Path) -> dict[str, Any]:
    """The bounded page must equal the frozen page on every randomised state."""
    rng = random.Random(int(args.seed))
    root = work / "equivalence"
    catalog = fresh_catalog(root / "data")
    try:
        connection = catalog.connection
        mismatch: dict[str, Any] | None = None
        compared = 0
        for trial in range(int(trials)):
            with catalog.write() as writer:
                writer.execute("DELETE FROM jobs")
                writer.execute("DELETE FROM sqlite_sequence WHERE name = 'jobs'")
                insert_state_rows(writer, random_state(rng, rows))
            for limit in (1, 2, 3, 7, 64, 256):
                for window in (1, 60_000, 86_400_000):
                    compared += 1
                    wanted = frozen_page(
                        connection, processors=[PROCESSOR], window=window, limit=limit
                    )
                    got = product_page(
                        connection, processors=[PROCESSOR], window=window, limit=limit
                    )
                    if wanted != got and mismatch is None:
                        mismatch = {
                            "trial": trial,
                            "limit": limit,
                            "aging_window_ms": window,
                            "frozen_head": wanted[:8],
                            "product_head": got[:8],
                            "frozen_len": len(wanted),
                            "product_len": len(got),
                        }
        return {
            "trials": int(trials),
            "rows_per_trial": int(rows),
            "pages_compared": compared,
            "identical": mismatch is None,
            "first_mismatch": mismatch,
        }
    finally:
        catalog.close()
        if not getattr(args, "keep", False):
            shutil.rmtree(root, ignore_errors=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P4-02E claim cost/plan/exactness probe")
    parser.add_argument("--scales", default="10000,50000,100000,300000")
    parser.add_argument("--work-dir", type=Path, default=REPO / "tmp" / "p402e-claim-probe")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--label", default=None, help="suffix for the per-scale tree name")
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--equivalence-trials", type=int, default=240)
    parser.add_argument("--equivalence-rows", type=int, default=400)
    parser.add_argument("--seed", type=int, default=20260924)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--skip-equivalence", action="store_true")
    parser.add_argument(
        "--index-candidates",
        action="store_true",
        help="also measure each candidate claim index (created and dropped on the tree)",
    )
    parser.add_argument(
        "--skip-analyze",
        action="store_true",
        help="do not create sqlite_stat1 first (the product itself never runs ANALYZE)",
    )
    return parser.parse_args(argv)


def write_output(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", file=sys.stderr, flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "task": "P4-02E",
        "check": "F1 bounded semantic-backlog claim: cost, plan and exactness",
        "work_dir": str(work),
        "now_ms": NOW,
        "aging_window_ms": WINDOW,
        "limit": LIMIT,
        "processors": [PROCESSOR],
        "build": {
            "supported_schema_version": int(schema.SUPPORTED_SCHEMA_VERSION),
            "claim_index": CLAIM_INDEX,
            "bounded_claim": bool(getattr(job_queue, "_claim_class_sql", None) is not None),
        },
        "status": "ok",
        "scales": [],
    }
    for scale in [int(part) for part in str(args.scales).split(",") if part.strip()]:
        log(f"scale {scale}: start")
        block = scale_report(scale, args=args, work=work)
        report["scales"].append(block)
        log(
            f"scale {scale}: product claim "
            f"{block['cost_ms']['product_claimable_candidates'].get('ms_median')} ms median, "
            f"{block['cost_ms']['product_claimable_candidates'].get('ms_p95')} ms p95; "
            f"claim_next {block['claim_transaction']['claim_next'].get('ms_median')} ms median"
        )
        write_output(args.output, report)
    if not args.skip_equivalence:
        log("equivalence: start")
        report["equivalence"] = equivalence(
            trials=args.equivalence_trials, rows=args.equivalence_rows, args=args, work=work
        )
        log(f"equivalence identical={report['equivalence']['identical']}")
    write_output(args.output, report)
    print(
        json.dumps(
            {"status": "ok", "output": None if args.output is None else str(args.output)},
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
