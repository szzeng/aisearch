"""P4-02A probe: the production semantic-outbox entry, its contention, and its injections.

Everything in this file runs against a real, seeded, project-local fixture (a real catalog,
a real Lance generation, real ``index_outbox`` events produced by the real artifact publish
path). It answers the three questions the P4-02A card has to answer with measurements
instead of prose:

``entry``       Does the production entry (``photo_search.indexing.maintenance`` /
                ``python -m photo_search.indexing.cli drain``) really close a pending outbox,
                is it an idempotent no-op afterwards, and what does one run cost
                (transactions, lock-wait and hold-time distributions, refusals)?
``injections``  The four D-level boundaries with before/after counts: a mid-drain restart,
                the ``Lance written / outbox unconfirmed`` window, a content change whose old
                vector must stay unreachable, and two maintainers draining at the same time.
``contention``  A real foreground writer (the P2-03A place-revision writer on its own
                connection) raced against the maintenance drain: lock-wait P50/P95 for both
                sides, the structured ``busy_timeout`` refusals with their retry outcome, and
                proof that a foreground write is never silently half-applied.
``recovery``    P4-02C: one artifact whose bytes no longer verify is isolated once the repair
                audit has reclaimed it (it may not pin the whole outbox), repeated audits cannot
                enlarge the backlog, a genuinely lost confirmation is still scheduled, and the
                production re-execution entry republishes the identical bytes so that the *same*
                event confirms afterwards.
``cli``         One real command-line invocation of ``status`` and ``drain`` in a subprocess,
                captured as the operator-facing evidence.

Machine-readable JSON goes to stdout (and to ``--output`` when given); progress goes to
stderr, so ``--output`` stays the single artifact of the run. Exit code 0 means every
executed scenario reported ``ok``.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = Path(__file__).resolve().parent
for _extra in (str(PROJECT_ROOT / "src"), str(TOOLS_DIR)):
    if _extra not in sys.path:
        sys.path.insert(0, _extra)

import ann_scale_benchmark as benchmark  # noqa: E402  (shares the P4-01 fixture builder)

from photo_search.artifacts.store import ArtifactStore  # noqa: E402
from photo_search.catalog.db import Catalog  # noqa: E402
from photo_search.catalog.errors import CatalogError  # noqa: E402
from photo_search.indexing import maintenance  # noqa: E402
from photo_search.indexing.store import IndexCoordinator  # noqa: E402
from photo_search.places import revision as places_revision  # noqa: E402
from photo_search.scheduler.projection import index_summary  # noqa: E402
from photo_search.search import retrieval  # noqa: E402

GENERATION_ID = benchmark.GENERATION_ID
SPACE_ID = benchmark.SPACE_ID
DIM = benchmark.DIM


def log(message: str) -> None:
    print(f"[p4-02a] {message}", file=sys.stderr, flush=True)


@contextlib.contextmanager
def engine_delay(ms: int) -> Iterator[None]:
    """Make one engine write take ``ms`` longer, inside the writer's transaction.

    A real drain holds the SQLite single-writer lock across its engine write (P4-01C second
    round), so a slower engine write *is* a longer lock hold. Injecting a sleep there is how
    the concurrency scenarios get a deterministic overlap window without touching the
    protocol; nothing else about the write changes.
    """
    original = benchmark.LanceVectorIndex.upsert_batch

    def slowed(self: Any, *, generation: Any, records: Any) -> Any:
        if records:
            time.sleep(int(ms) / 1000)
        return original(self, generation=generation, records=records)

    benchmark.LanceVectorIndex.upsert_batch = slowed
    try:
        yield
    finally:
        benchmark.LanceVectorIndex.upsert_batch = original


# ------------------------------------------------------------------------------- fixture
def fresh_fixture(work: Path, *, scale: int, seed: int, distribution: str) -> Any:
    """Build a seeded fixture in a work directory that must not hold one yet.

    Every scenario measures a *pending* outbox that it created itself, so a work directory
    holding an earlier run cannot be rebuilt in place; saying so beats silently measuring a
    half-drained fixture.
    """
    catalog_db = work / "data" / "catalog" / "catalog.db"
    if catalog_db.exists():
        raise RuntimeError(
            f"{work} already holds a fixture ({catalog_db}); pass a fresh --work-dir"
        )
    return benchmark.build_fixture(
        work, scale=int(scale), seed=int(seed), distribution=distribution
    )


def grow_library(
    fixture: Any, *, events: int, seed: int, prefix: str
) -> Any:
    """Add ``events`` real assets and publish one real embedding artifact for each.

    The new assets are inserted with a real ``file_locations`` row (so the semantic row is
    marked visible) and their embedding artifacts go through
    ``ArtifactStore.persist_result``, exactly like an AI worker submission - which is what
    writes the ``UPSERT_VECTOR`` outbox events this probe drains.
    """
    first = int(fixture.scale) + 1
    now = benchmark.now_ms()
    with fixture.catalog.write() as connection:
        for offset in range(int(events)):
            asset_id = first + offset
            connection.execute(
                "INSERT INTO assets(sha256, byte_size, width, height, format, created_at, state) "
                "VALUES (?, 2000000, 4032, 3024, 'JPEG', ?, 'ACTIVE')",
                (f"{asset_id:064x}", now),
            )
            connection.execute(
                "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, size, "
                "mtime_ns, last_seen_scan_id, availability) "
                "VALUES (?, ?, ?, ?, 2000000, 1700000000000000000, NULL, 'PRESENT')",
                (
                    fixture.source_ids[0],
                    f"{prefix}/photo-{asset_id:08d}.jpg",
                    f"{prefix}/photo-{asset_id:08d}.jpg",
                    asset_id,
                ),
            )
    vectors = benchmark.synthetic_vectors(int(events), seed=seed, distribution="clustered")
    for offset in range(int(events)):
        benchmark.publish_embedding(
            fixture,
            artifact_id=f"{prefix}-{first + offset}",
            asset_id=first + offset,
            vector=vectors[offset],
            now=benchmark.now_ms(),
        )
    return vectors


def outbox_states(fixture: Any) -> dict[str, int]:
    return benchmark.outbox_counts(fixture)


def semantic_ready(fixture: Any) -> int:
    row = fixture.catalog.read_one(
        "SELECT COUNT(*) AS total FROM index_entries AS e "
        "JOIN index_generations AS g USING(generation_id) "
        "WHERE e.state = 'CONFIRMED' AND g.state = 'READY'"
    )
    return int(row["total"])


def lance_rows(fixture: Any) -> list[dict[str, Any]]:
    index = IndexCoordinator(fixture.catalog).index
    return index.rows(generation_id=GENERATION_ID, dimension=DIM)


def state_digest(fixture: Any) -> dict[str, Any]:
    """A canonical, seeded-state fingerprint of the derived index (no floats, no ids of
    artifacts): two runs that converge to the same derived state produce the same digest."""
    rows = lance_rows(fixture)
    lance = sorted(
        (
            int(row["asset_id"]),
            int(row["projection_revision"]),
            str(row["artifact_revision"]),
            bool(row["visible"]),
        )
        for row in rows
    )
    entries = sorted(
        (
            int(row["asset_id"]),
            str(row["state"]),
            int(row["confirmation_revision"] or 0),
        )
        for row in fixture.catalog.read_all(
            "SELECT asset_id, state, confirmation_revision FROM index_entries"
        )
    )
    outbox = sorted(
        (int(row["asset_id"]), int(row["projection_revision"]), str(row["state"]))
        for row in fixture.catalog.read_all(
            "SELECT asset_id, projection_revision, state FROM index_outbox"
        )
    )
    payload = json.dumps(
        {"lance": lance, "entries": entries, "outbox": outbox},
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "lance_rows": len(lance),
        "lance_unique_assets": len({row[0] for row in lance}),
        "entries": len(entries),
        "entries_by_state": _counts(str(row["state"]) for row in fixture.catalog.read_all(
            "SELECT state FROM index_entries"
        )),
        "outbox": outbox_states(fixture),
        "semantic_ready": semantic_ready(fixture),
    }


def _counts(values: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return result


def _new_asset_hits(fixture: Any, query: Any, *, limit: int = 20) -> set[int]:
    """The production candidate walk for one query (exact path, no ANN main path)."""
    return benchmark._walk_ids(
        fixture, scope=retrieval.SearchScope.build(), query=query, limit=limit
    )


def _asset_distance(fixture: Any, *, query: Any, asset_id: int) -> float | None:
    """Engine distance between ``query`` and one asset's stored row (or ``None``)."""
    rows = IndexCoordinator(fixture.catalog).index.search(
        generation_id=GENERATION_ID,
        dimension=DIM,
        query_vector=[float(value) for value in query],
        limit=1,
        where=f"asset_id = {int(asset_id)}",
        space_id=SPACE_ID,
    )
    return None if not rows else float(rows[0]["distance"])


def _entry_revision(fixture: Any, asset_id: int) -> Any:
    return fixture.catalog.read_one(
        "SELECT state, confirmation_revision FROM index_entries "
        "WHERE space_id = ? AND asset_id = ? AND generation_id = ?",
        (SPACE_ID, int(asset_id), GENERATION_ID),
    )


def _row_for(fixture: Any, asset_id: int) -> dict[str, Any] | None:
    return next(
        (row for row in lance_rows(fixture) if int(row["asset_id"]) == int(asset_id)), None
    )


def _max_event_revision(fixture: Any, asset_id: int) -> int:
    row = fixture.catalog.read_one(
        "SELECT MAX(projection_revision) AS revision FROM index_outbox "
        "WHERE space_id = ? AND asset_id = ? AND generation_id = ?",
        (SPACE_ID, int(asset_id), GENERATION_ID),
    )
    return int(row["revision"] or 0)


def _wait_for_confirmed_events(fixture: Any, *, minimum: int, timeout_s: float) -> bool:
    """Wait (read-only) until the outbox holds ``minimum`` confirmed events."""
    probe = Catalog.open(data_root=fixture.data_root)
    try:
        deadline = time.monotonic() + float(timeout_s)
        while time.monotonic() < deadline:
            row = probe.read_one(
                "SELECT COUNT(*) AS total FROM index_outbox WHERE state = 'CONFIRMED'"
            )
            if int(row["total"]) >= int(minimum):
                return True
            time.sleep(0.005)
        return False
    finally:
        probe.close()


def _contended(report: dict[str, Any] | None, *, interleave_delay_ms: int) -> bool:
    """Whether a maintainer really ran against the other one's write transaction."""
    if report is None:
        return False
    if int(report["applied"]) > 0:
        return True
    waited = float(report["lock_wait_ms"]["max_ms"] or 0.0)
    return int(report["write_transactions"]) >= 1 and waited >= max(
        1.0, float(interleave_delay_ms) / 2
    )


def _outcome(report: dict[str, Any] | None) -> str | None:
    """How a maintainer's call ended - recorded, because both endings are correct."""
    if report is None:
        return None
    if int(report["applied"]) > 0:
        return "applied_events"
    if int(report["write_transactions"]) >= 1:
        return "claimed_but_the_other_maintainer_had_confirmed_them"
    return "nothing_to_do"


# --------------------------------------------------------------------------- scenarios
def scenario_entry(work: Path, args: argparse.Namespace) -> dict[str, Any]:
    log(f"entry: building the {args.assets}-asset fixture with {args.events} pending events")
    fixture = fresh_fixture(
        work, scale=int(args.assets), seed=int(args.seed), distribution=args.distribution
    )
    try:
        first = int(fixture.scale) + 1
        vectors = grow_library(
            fixture, events=int(args.events), seed=int(args.seed) + 1, prefix="p4-02a-entry"
        )
        before = index_summary(fixture.catalog.db_path)
        assert before is not None and before["pending"] == int(args.events), before
        log(f"entry: draining {args.events} events through the production entry")
        report = maintenance.drain_semantic_outbox(fixture.catalog)
        after = index_summary(fixture.catalog.db_path)
        hits = _new_asset_hits(fixture, vectors[0])
        log("entry: re-running the entry to prove the second call is a no-op")
        second = maintenance.drain_semantic_outbox(fixture.catalog)
        started = time.perf_counter()
        audit = IndexCoordinator(fixture.catalog).audit_and_schedule_repairs(
            now_ms=benchmark.now_ms()
        )
        audit_ms = (time.perf_counter() - started) * 1000
        generation = fixture.catalog.read_all(
            "SELECT generation_id, state, is_active FROM index_generations"
        )
        ready = semantic_ready(fixture)
        expected_ready = int(fixture.scale) + int(args.events)
        checks = {
            "applied_all_events": int(report["applied"]) == int(args.events),
            "outbox_drained": after is not None and int(after["pending"]) == 0,
            "semantic_ready_complete": ready == expected_ready,
            "drained_row_is_searchable": first in hits,
            "second_call_is_a_noop": int(second["applied"]) == 0
            and int(second["confirmed_delta"]) == 0,
            "generation_ready_and_active": all(
                str(row["state"]) == "READY" and int(row["is_active"]) == 1 for row in generation
            ),
            "one_row_per_asset": len({int(row["asset_id"]) for row in lance_rows(fixture)})
            == len(lance_rows(fixture)),
            "repair_audit_is_clean": not audit["scheduled"] and not audit["requires_reexecution"],
        }
        return {
            "ok": all(checks.values()),
            "assets": int(fixture.scale),
            "events": int(args.events),
            "drain": report,
            "second_call": {
                "applied": int(second["applied"]),
                "confirmed_delta": int(second["confirmed_delta"]),
                "write_transactions": int(second["write_transactions"]),
            },
            "index_before": before,
            "index_after": after,
            "semantic_ready": {"ready": ready, "expected": expected_ready},
            "searchable_first_new_asset": first in hits,
            "repair_audit": {
                "ms": round(audit_ms, 1),
                "ms_per_artifact": round(audit_ms / max(int(fixture.scale), 1), 3),
                "scheduled": len(audit["scheduled"]),
                "requires_reexecution": len(audit["requires_reexecution"]),
                "note": (
                    "one engine point query per embedding artifact: a full-library repair pass "
                    "is an explicit --repair decision, not a routine drain cost"
                ),
            },
            "checks": checks,
        }
    finally:
        fixture.catalog.close()


def injection_restart(work: Path, args: argparse.Namespace) -> dict[str, Any]:
    """D-1: a drain that stops halfway, then a restart, must equal one uninterrupted run."""
    events = max(4, int(args.injection_events))
    seed = int(args.seed) + 5
    scale = max(int(args.injection_assets), 30)

    uninterrupted = fresh_fixture(
        work / "uninterrupted", scale=scale, seed=seed, distribution=args.distribution
    )
    try:
        grow_library(
            uninterrupted, events=events, seed=seed + 1, prefix="p4-02a-inj1-uninterrupted"
        )
        report = maintenance.drain_semantic_outbox(uninterrupted.catalog)
        reference = state_digest(uninterrupted)
    finally:
        uninterrupted.catalog.close()

    interrupted = fresh_fixture(
        work / "interrupted", scale=scale, seed=seed, distribution=args.distribution
    )
    try:
        grow_library(
            interrupted, events=events, seed=seed + 1, prefix="p4-02a-inj1-interrupted"
        )
        first_half = maintenance.drain_semantic_outbox(
            interrupted.catalog, limit=events // 2
        )
        mid = state_digest(interrupted)
        # The "restart": the process ends, so its connection and coordinator are gone.
        interrupted.catalog.close()
        reopened = Catalog.open(data_root=interrupted.data_root)
        interrupted = dataclasses.replace(interrupted, catalog=reopened)
        second_half = maintenance.drain_semantic_outbox(interrupted.catalog)
        recovered = state_digest(interrupted)
        checks = {
            "no_logical_duplicates": recovered["lance_rows"]
            == recovered["lance_unique_assets"],
            "no_lost_events": recovered["outbox"].get("PENDING", 0) == 0
            and recovered["outbox"].get("IN_FLIGHT", 0) == 0,
            "restart_reaches_the_uninterrupted_state": recovered["sha256"]
            == reference["sha256"],
            "all_events_accounted_for": int(first_half["applied"])
            + int(second_half["applied"])
            == events,
        }
        return {
            "ok": all(checks.values()),
            "events": events,
            "first_call": {
                "applied": int(first_half["applied"]),
                "limit": int(first_half["limit"]),
                "duration_ms": float(first_half["duration_ms"]),
            },
            "state_after_interruption": mid,
            "restart_call": {
                "applied": int(second_half["applied"]),
                "confirmed_delta": int(second_half["confirmed_delta"]),
                "lock_wait_ms": second_half["lock_wait_ms"],
            },
            "state_after_restart": recovered,
            "uninterrupted_state": reference,
            "reference_applied": int(report["applied"]),
            "checks": checks,
        }
    finally:
        interrupted.catalog.close()


def injection_crash_window(work: Path, args: argparse.Namespace) -> dict[str, Any]:
    """D-2: ``Lance written / outbox unconfirmed`` replays through the entry, once."""
    events = max(4, int(args.injection_events))
    scale = max(int(args.injection_assets), 30)
    fixture = fresh_fixture(
        work, scale=scale, seed=int(args.seed) + 7, distribution=args.distribution
    )
    try:
        grow_library(fixture, events=events, seed=int(args.seed) + 8, prefix="p4-02a-inj2")
        claimed = min(3, events)
        coordinator = IndexCoordinator(fixture.catalog)
        crashed = None
        try:
            coordinator.process_batch(
                now_ms=benchmark.now_ms(), max_events=claimed, failpoint="after_lance"
            )
        except RuntimeError as error:
            crashed = str(error)
        during = {
            "outbox": outbox_states(fixture),
            "lance_rows": len(lance_rows(fixture)),
            "semantic_ready": semantic_ready(fixture),
        }
        report = maintenance.drain_semantic_outbox(fixture.catalog)
        after = state_digest(fixture)
        expected_ready = scale + events
        checks = {
            "crash_window_was_entered": crashed is not None and "after_lance" in crashed,
            "claimed_events_stay_in_flight": during["outbox"] == {"PENDING": events - claimed, "IN_FLIGHT": claimed},
            "replay_confirms_everything_once": after["outbox"] == {"CONFIRMED": events}
            and int(report["applied"]) == events,
            "no_logical_duplicates": after["lance_rows"] == after["lance_unique_assets"],
            "semantic_ready_complete": after["semantic_ready"] == expected_ready,
        }
        return {
            "ok": all(checks.values()),
            "events": events,
            "claimed_in_the_crashed_batch": claimed,
            "crash": crashed,
            "state_after_crash": during,
            "replay": {
                "applied": int(report["applied"]),
                "confirmed_delta": int(report["confirmed_delta"]),
                "write_transactions": int(report["write_transactions"]),
            },
            "state_after_replay": after,
            "expected_semantic_ready": expected_ready,
            "checks": checks,
        }
    finally:
        fixture.catalog.close()


def injection_content_change(work: Path, args: argparse.Namespace) -> dict[str, Any]:
    """D-3: a content change must leave only the newest vector reachable for that asset."""
    scale = max(int(args.injection_assets), 30)
    fixture = fresh_fixture(
        work, scale=scale, seed=int(args.seed) + 11, distribution=args.distribution
    )
    try:
        asset_id = 1
        older = fixture.matrix[asset_id - 1]
        newer = benchmark.synthetic_vectors(
            1, seed=int(args.seed) + 12, distribution="clustered"
        )[0]
        before_revision = int(
            fixture.catalog.read_one(
                "SELECT revision FROM catalog_filter_revision WHERE revision_id = 1"
            )["revision"]
        )
        benchmark.bump_filter_revision(fixture, cause="CONTENT_CHANGED")
        benchmark.publish_embedding(
            fixture,
            artifact_id="p4-02a-inj3-new",
            asset_id=asset_id,
            vector=newer,
            now=benchmark.now_ms(),
            input_revision=hashlib.sha256(b"p4-02a-content-changed").hexdigest(),
        )
        pending = outbox_states(fixture)
        report = maintenance.drain_semantic_outbox(fixture.catalog)
        drained = outbox_states(fixture)
        row = _row_for(fixture, asset_id) or {}
        entry = _entry_revision(fixture, asset_id)
        newest_revision = _max_event_revision(fixture, asset_id)
        distance_new = _asset_distance(fixture, query=newer, asset_id=asset_id)
        distance_old = _asset_distance(fixture, query=older, asset_id=asset_id)
        checks = {
            "new_row_revision_is_the_newest": int(row.get("projection_revision", -1))
            == newest_revision
            == int(entry["confirmation_revision"]),
            "stored_row_is_the_new_vector": distance_new is not None and distance_new <= 1e-6,
            "old_vector_no_longer_matches": distance_old is not None
            and distance_old > 0.0
            and distance_old > float(distance_new),
            "entry_still_confirmed": str(entry["state"]) == "CONFIRMED",
            "outbox_drained": drained == {"CONFIRMED": pending.get("PENDING", 0)}
            and int(report["applied"]) == int(pending.get("PENDING", 0)),
        }
        return {
            "ok": all(checks.values()),
            "asset_id": asset_id,
            "filter_revision_before": before_revision,
            "filter_revision_after": before_revision + 1,
            "newest_event_revision": newest_revision,
            "outbox_before": pending,
            "stored_row_revision": int(row.get("projection_revision", -1)),
            "entry_confirmation_revision": int(entry["confirmation_revision"]),
            "distance_new_vector": distance_new,
            "distance_old_vector": distance_old,
            "applied": int(report["applied"]),
            "checks": checks,
        }
    finally:
        fixture.catalog.close()


def injection_two_maintainers(work: Path, args: argparse.Namespace) -> dict[str, Any]:
    """D-4: two maintainers on their own connections, one asset with a newer revision."""
    events = max(4, int(args.injection_events))
    scale = max(int(args.injection_assets), 30)
    fixture = fresh_fixture(
        work, scale=scale, seed=int(args.seed) + 17, distribution=args.distribution
    )
    try:
        grow_library(fixture, events=events, seed=int(args.seed) + 18, prefix="p4-02a-inj4")
        target = int(fixture.scale) + 1
        newest = benchmark.synthetic_vectors(
            1, seed=int(args.seed) + 19, distribution="clustered"
        )[0]
        benchmark.bump_filter_revision(fixture, cause="CONTENT_CHANGED")
        benchmark.publish_embedding(
            fixture,
            artifact_id="p4-02a-inj4-newest",
            asset_id=target,
            vector=newest,
            now=benchmark.now_ms(),
            input_revision=hashlib.sha256(b"p4-02a-newest").hexdigest(),
        )
        total_events = events + 1
        before = state_digest(fixture)
        results: list[dict[str, Any] | None] = [None, None]
        failures: list[str | None] = [None, None]
        batch_size = max(2, min(3, events))

        def maintainer(index: int) -> None:
            # One maintainer = one process-shaped connection, opened inside its own thread.
            handle = Catalog.open(data_root=fixture.data_root)
            try:
                results[index] = maintenance.drain_semantic_outbox(
                    handle,
                    limit=total_events * 2,
                    batch_size=batch_size,
                    retry_limit=5,
                    retry_backoff_ms=50,
                )
            except BaseException as error:  # noqa: BLE001 - reported, not swallowed
                failures[index] = f"{type(error).__name__}: {error}"
            finally:
                handle.close()

        log("injections: two maintainers draining the same outbox concurrently")
        with engine_delay(int(args.interleave_delay_ms)):
            first = threading.Thread(target=maintainer, args=(0,))
            first.start()
            # The second maintainer joins as soon as the first one has confirmed a batch: from
            # then on the remaining events are unclaimed, so it either applies them itself or
            # is blocked by the first maintainer's write transaction (both are recorded).
            _wait_for_confirmed_events(fixture, minimum=1, timeout_s=30)
            time.sleep(int(args.maintainer_stagger_ms) / 1000)
            second = threading.Thread(target=maintainer, args=(1,))
            second.start()
            for thread in (first, second):
                thread.join(timeout=600)
        leftover = maintenance.drain_semantic_outbox(fixture.catalog)
        after = state_digest(fixture)
        row = _row_for(fixture, target) or {}
        entry = _entry_revision(fixture, target)
        distance = _asset_distance(fixture, query=newest, asset_id=target)
        applied = [int(item["applied"]) for item in results if item is not None]
        applied_total = sum(applied) + int(leftover["applied"])
        second_report = results[1]
        checks = {
            "maintainers_reported_no_failure": all(item is None for item in failures),
            "the_second_maintainer_really_contended": _contended(
                second_report, interleave_delay_ms=int(args.interleave_delay_ms)
            ),
            "every_event_confirmed_once": applied_total == total_events
            and after["outbox"] == {"CONFIRMED": total_events},
            "no_logical_duplicates": after["lance_rows"] == after["lance_unique_assets"],
            "newest_revision_landed": int(row.get("projection_revision", -1))
            == _max_event_revision(fixture, target)
            == int(entry["confirmation_revision"]),
            "newest_vector_is_the_stored_one": distance is not None and distance <= 1e-6,
            "semantic_ready_complete": after["semantic_ready"] == scale + events,
        }
        return {
            "ok": all(checks.values()),
            "events": total_events,
            "batch_size": batch_size,
            "interleave_delay_ms": int(args.interleave_delay_ms),
            "maintainer_stagger_ms": int(args.maintainer_stagger_ms),
            "state_before": before,
            "maintainers": [
                None
                if item is None
                else {
                    "applied": int(item["applied"]),
                    "write_transactions": int(item["write_transactions"]),
                    "lock_wait_ms": item["lock_wait_ms"],
                    "write_rejections": item["write_rejections"]["count"],
                }
                for item in results
            ],
            "failures": failures,
            "applied_total": applied_total,
            "both_maintainers_applied_events": len(applied) == 2 and all(item > 0 for item in applied),
            "second_maintainer_outcome": _outcome(second_report),
            "leftover_call": {
                "applied": int(leftover["applied"]),
                "confirmed_delta": int(leftover["confirmed_delta"]),
            },
            "state_after": after,
            "target_asset": {
                "asset_id": target,
                "stored_row_revision": int(row.get("projection_revision", -1)),
                "entry_confirmation_revision": int(entry["confirmation_revision"]),
                "max_event_revision": _max_event_revision(fixture, target),
                "distance_to_newest_vector": distance,
            },
            "checks": checks,
        }
    finally:
        fixture.catalog.close()


def scenario_injections(work: Path, args: argparse.Namespace) -> dict[str, Any]:
    results = {
        "restart": injection_restart(work / "restart", args),
        "crash_window": injection_crash_window(work / "crash-window", args),
        "content_change": injection_content_change(work / "content-change", args),
        "two_maintainers": injection_two_maintainers(work / "two-maintainers", args),
    }
    return {"ok": all(item["ok"] for item in results.values()), **results}


def scenario_contention(work: Path, args: argparse.Namespace) -> dict[str, Any]:
    """The maintenance drain raced against a real foreground writer on its own connection."""
    fixture = fresh_fixture(
        work, scale=int(args.assets), seed=int(args.seed) + 21, distribution=args.distribution
    )
    try:
        events = int(args.events)
        vectors = grow_library(
            fixture, events=events, seed=int(args.seed) + 22, prefix="p4-02a-contention"
        )
        target = int(fixture.scale) + 1
        newest = benchmark.synthetic_vectors(
            1, seed=int(args.seed) + 23, distribution="clustered"
        )[0]
        original = vectors[0]
        revision_before = int(
            fixture.catalog.read_one(
                "SELECT revision FROM catalog_filter_revision WHERE revision_id = 1"
            )["revision"]
        )
        events_before = int(
            fixture.catalog.read_one(
                "SELECT COUNT(*) AS total FROM catalog_filter_revision_events"
            )["total"]
        )
        stop = threading.Event()
        record: dict[str, Any] = {
            "bump_wait_ms": [],
            "bump_hold_ms": [],
            "publish_ms": [],
            "committed_bumps": 0,
            "committed_publishes": 0,
            "refusals": [],
            "iterations": 0,
        }

        def foreground() -> None:
            # A foreground writer is its own connection (and its own thread), like the API
            # process: sharing this thread's handle would serialise on a Python lock instead
            # of exercising SQLite's single-writer lock.
            handle = Catalog.open(data_root=fixture.data_root)
            view = dataclasses.replace(fixture, catalog=handle)
            try:
                for iteration in range(int(args.foreground_writes)):
                    if stop.is_set():
                        break
                    record["iterations"] += 1
                    started = time.perf_counter()
                    try:
                        with handle.write() as connection:
                            acquired = time.perf_counter()
                            places_revision.bump_filter_revision(
                                connection, cause="CONTENT_CHANGED", now_ms=benchmark.now_ms()
                            )
                    except CatalogError as error:
                        record["refusals"].append(
                            {
                                "phase": "revision",
                                "code": str(error.code),
                                "message": str(error.message),
                                "waited_ms": round((time.perf_counter() - started) * 1000, 1),
                            }
                        )
                        continue
                    record["bump_wait_ms"].append((acquired - started) * 1000)
                    record["bump_hold_ms"].append((time.perf_counter() - acquired) * 1000)
                    record["committed_bumps"] += 1
                    if iteration % 3 == 0:
                        started_publish = time.perf_counter()
                        benchmark.publish_embedding(
                            view,
                            artifact_id=f"p4-02a-contention-{iteration}",
                            asset_id=target,
                            vector=newest,
                            now=benchmark.now_ms(),
                            input_revision=hashlib.sha256(
                                f"p4-02a-contention-{iteration}".encode()
                            ).hexdigest(),
                        )
                        record["publish_ms"].append(
                            (time.perf_counter() - started_publish) * 1000
                        )
                        record["committed_publishes"] += 1
            finally:
                handle.close()

        log(f"contention: draining {events} events while a foreground writer is active")
        worker = threading.Thread(target=foreground, daemon=True)
        with engine_delay(int(args.interleave_delay_ms)):
            worker.start()
            report = maintenance.drain_semantic_outbox(
                fixture.catalog, limit=events * 6, retry_limit=5, retry_backoff_ms=50
            )
        worker.join(timeout=600)
        stop.set()
        leftover = maintenance.drain_semantic_outbox(fixture.catalog)
        revision_after = int(
            fixture.catalog.read_one(
                "SELECT revision FROM catalog_filter_revision WHERE revision_id = 1"
            )["revision"]
        )
        events_after = int(
            fixture.catalog.read_one(
                "SELECT COUNT(*) AS total FROM catalog_filter_revision_events"
            )["total"]
        )
        state = state_digest(fixture)
        row = _row_for(fixture, target) or {}
        entry = _entry_revision(fixture, target)
        distance = _asset_distance(fixture, query=newest, asset_id=target)
        distance_original = _asset_distance(fixture, query=original, asset_id=target)
        checks = {
            "no_silent_foreground_failure": revision_after
            == revision_before + int(record["committed_bumps"])
            and events_after == events_before + int(record["committed_bumps"]),
            "every_foreground_failure_is_structured": all(
                item["code"] == "CATALOG_UNREADABLE"
                and "locked" in item["message"].lower()
                for item in record["refusals"]
            ),
            "the_foreground_really_published": int(record["committed_publishes"]) > 0,
            "outbox_drained": state["outbox"].get("PENDING", 0) == 0
            and state["outbox"].get("IN_FLIGHT", 0) == 0,
            "no_logical_duplicates": state["lance_rows"] == state["lance_unique_assets"],
            "newest_revision_landed": int(row.get("projection_revision", -1))
            == _max_event_revision(fixture, target)
            == int(entry["confirmation_revision"]),
            "newest_vector_is_the_stored_one": distance is not None and distance <= 1e-6,
            "superseded_vector_is_unreachable": distance_original is not None
            and distance_original > 0.0
            and distance_original > float(distance),
        }
        return {
            "ok": all(checks.values()),
            "assets": int(fixture.scale),
            "events": events,
            "interleave_delay_ms": int(args.interleave_delay_ms),
            "drain": {
                "applied": int(report["applied"]),
                "confirmed_delta": int(report["confirmed_delta"]),
                "duration_ms": float(report["duration_ms"]),
                "write_transactions": int(report["write_transactions"]),
                "lock_wait_ms": report["lock_wait_ms"],
                "write_hold_ms": report["write_hold_ms"],
                "write_rejections": report["write_rejections"],
            },
            "foreground": {
                "iterations": int(record["iterations"]),
                "committed_bumps": int(record["committed_bumps"]),
                "committed_publishes": int(record["committed_publishes"]),
                "revision_wait_ms": maintenance.summarize_ms(record["bump_wait_ms"]),
                "revision_hold_ms": maintenance.summarize_ms(record["bump_hold_ms"]),
                "publish_ms": maintenance.summarize_ms(record["publish_ms"]),
                "refusal_count": len(record["refusals"]),
                "refusals": record["refusals"][:20],
            },
            "revision_writer": {
                "before": revision_before,
                "after": revision_after,
                "event_rows_before": events_before,
                "event_rows_after": events_after,
            },
            "leftover_call": {
                "applied": int(leftover["applied"]),
                "confirmed_delta": int(leftover["confirmed_delta"]),
            },
            "state_after": state,
            "target_asset": {
                "asset_id": target,
                "stored_row_revision": int(row.get("projection_revision", -1)),
                "entry_confirmation_revision": int(entry["confirmation_revision"]),
                "max_event_revision": _max_event_revision(fixture, target),
                "distance_to_newest_vector": distance,
                "distance_to_superseded_vector": distance_original,
            },
            "checks": checks,
        }
    finally:
        fixture.catalog.close()


def scenario_busy_timeout(work: Path, args: argparse.Namespace) -> dict[str, Any]:
    """One real expired ``busy_timeout``: structured refusal, bounded retry, no loss.

    A holder connection takes the single-writer lock and keeps it past SQLite's 5 s busy
    timeout, so the drain's first write transaction is refused *before* its body ran. The
    entry must report that refusal (code and wait), retry, and still confirm every event
    exactly once after the lock is released.
    """
    events = max(4, int(args.injection_events))
    fixture = fresh_fixture(
        work, scale=max(int(args.injection_assets), 30), seed=int(args.seed) + 31,
        distribution=args.distribution,
    )
    try:
        grow_library(fixture, events=events, seed=int(args.seed) + 32, prefix="p4-02a-busy")
        before = outbox_states(fixture)
        hold_ms = int(args.busy_timeout_hold_ms)
        locked = threading.Event()
        holder_state: dict[str, Any] = {"began": False, "released": False, "error": None}

        def hold_the_lock() -> None:
            holder = sqlite3.connect(
                str(fixture.catalog.db_path), isolation_level=None, timeout=1.0
            )
            try:
                holder.execute("BEGIN IMMEDIATE")
                holder_state["began"] = True
                locked.set()
                time.sleep(hold_ms / 1000)
                holder.execute("ROLLBACK")
                holder_state["released"] = True
            except Exception as error:  # noqa: BLE001 - reported, not swallowed
                holder_state["error"] = f"{type(error).__name__}: {error}"
            finally:
                holder.close()

        thread = threading.Thread(target=hold_the_lock, daemon=True)
        thread.start()
        locked.wait(timeout=30)
        started = time.perf_counter()
        report = maintenance.drain_semantic_outbox(
            fixture.catalog,
            batch_size=2,
            retry_limit=int(args.retry_limit),
            retry_backoff_ms=50,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        thread.join(timeout=60)
        after = state_digest(fixture)
        rejections = report["write_rejections"]
        samples = list(rejections["samples"])
        structured = [
            item
            for item in samples
            if item["code"] == "CATALOG_UNREADABLE" and "locked" in item["message"].lower()
        ]
        checks = {
            "the_busy_timeout_really_expired": int(rejections["count"]) >= 1,
            "the_refusal_is_structured_and_retryable": len(structured) == int(
                rejections["count"]
            ),
            "the_refusal_waited_for_the_busy_timeout": bool(
                structured
                and min(item["waited_ms"] for item in structured) > 4000
            ),
            "a_bounded_retry_recovered": int(rejections["retries"]) >= 1
            and int(report["applied"]) == events,
            "every_event_confirmed_once": after["outbox"] == {"CONFIRMED": events},
            "no_logical_duplicates": after["lance_rows"] == after["lance_unique_assets"],
            "lock_holder_released_cleanly": bool(holder_state["released"])
            and holder_state["error"] is None,
        }
        return {
            "ok": all(checks.values()),
            "events": events,
            "hold_ms": hold_ms,
            "configured_busy_timeout_ms": 5000,
            "drain_elapsed_ms": round(elapsed_ms, 1),
            "write_rejections": rejections,
            "lock_wait_ms": report["lock_wait_ms"],
            "outbox_before": before,
            "state_after": after,
            "holder": holder_state,
            "checks": checks,
        }
    finally:
        fixture.catalog.close()


def scenario_cli(work: Path, args: argparse.Namespace) -> dict[str, Any]:
    """The operator-facing command line, in a real subprocess."""
    fixture = fresh_fixture(
        work, scale=max(int(args.injection_assets), 30), seed=int(args.seed) + 29,
        distribution=args.distribution,
    )
    try:
        events = max(4, int(args.injection_events))
        grow_library(fixture, events=events, seed=int(args.seed) + 30, prefix="p4-02a-cli")
        data_root = str(fixture.data_root)
        status_before = _run_cli(["status", "--data-root", data_root, "--json"])
        drain = _run_cli(["drain", "--data-root", data_root, "--json"])
        status_after = _run_cli(["status", "--data-root", data_root, "--json"])
        usage = _run_cli(["--json"])
        drained = drain["payload"].get("applied") if drain["payload"] else None
        checks = {
            "status_is_read_only_json": status_before["returncode"] == 0
            and (status_before["payload"] or {}).get("status") == "ok",
            "drain_reports_every_event": drain["returncode"] == 0 and drained == events,
            "drain_reports_its_cost": bool(
                (drain["payload"] or {}).get("lock_wait_ms")
            ),
            "status_after_shows_a_drained_outbox": (status_after["payload"] or {})
            .get("index", {})
            .get("pending")
            == 0,
            "missing_command_is_a_usage_error": usage["returncode"] == 2,
        }
        return {
            "ok": all(checks.values()),
            "events": events,
            "status_before": status_before,
            "drain": drain,
            "status_after": status_after,
            "usage_error": usage,
            "checks": checks,
        }
    finally:
        fixture.catalog.close()


# ------------------------------------------------------------------------------- recovery
class StubImageEncoder:
    """The deterministic stand-in for the ADR-0003 image tower during a re-execution.

    It is keyed by the source file's name, so the proof stays about *the same input*: the vector
    it returns is the one read out of the published artifact before its bytes were corrupted.
    """

    def __init__(self, vectors: dict[str, Sequence[float]]) -> None:
        self.vectors = {
            str(name): [float(value) for value in vector] for name, vector in vectors.items()
        }
        self.calls: list[str] = []

    def encode_image(self, path: str) -> list[float]:
        name = Path(path).name
        self.calls.append(name)
        return list(self.vectors[name])


def corrupt_artifact(fixture: Any, artifact_id: str) -> tuple[Path, list[float], bytes]:
    """Break the published bytes; return the path, the deterministic re-run vector and the bytes."""
    row = fixture.catalog.read_one(
        "SELECT * FROM artifacts WHERE artifact_id = ?", (str(artifact_id),)
    )
    if row is None:
        raise RuntimeError(f"artifact {artifact_id} is not in the fixture")
    path = ArtifactStore.open(fixture.data_root).resolve_uri(str(row["storage_uri"]))
    published = path.read_bytes()
    payload = json.loads(published.decode("utf-8"))
    path.write_bytes(b"these bytes no longer verify against the recorded checksum")
    return path, [float(value) for value in payload["vector"]], published


def give_the_asset_a_real_source_file(fixture: Any, work: Path, asset_id: int) -> Path:
    """Point the asset's source root at a real project-local file.

    The shared benchmark fixture records synthetic locations under ``D:\\synthetic\\...`` with no
    files behind them, so the recovery path - which only reads a PRESENT location - needs one
    honest file inside this run's work directory. The stand-in encoder never decodes it.
    """
    location = fixture.catalog.read_one(
        "SELECT source_id, relative_path FROM file_locations WHERE asset_id = ? "
        "ORDER BY location_id LIMIT 1",
        (int(asset_id),),
    )
    if location is None:
        raise RuntimeError(f"asset {asset_id} has no location")
    root = (work / "library").resolve()
    with fixture.catalog.write() as connection:
        connection.execute(
            "UPDATE source_roots SET root_path = ? WHERE source_id = ?",
            (str(root), int(location["source_id"])),
        )
    path = root / str(location["relative_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic source image (never decoded by the stand-in encoder)")
    return path


def scenario_recovery(work: Path, args: argparse.Namespace) -> dict[str, Any]:
    """A2/A3 from the P4-02A engineering review: isolation, bounded audits, supported recovery."""
    scale = max(int(args.injection_assets), 30)
    fixture = fresh_fixture(work, scale=scale, seed=int(args.seed) + 21, distribution=args.distribution)
    try:
        events = 3
        grow_library(fixture, events=events, seed=int(args.seed) + 22, prefix="p4-02c")
        first = int(fixture.scale) + 1
        target = first + 1
        target_artifact = f"p4-02c-{target}"
        other_artifact = f"p4-02c-{first}"
        path, vector, published_bytes = corrupt_artifact(fixture, target_artifact)

        refusal: dict[str, Any] | None = None
        try:
            maintenance.drain_semantic_outbox(fixture.catalog)
        except CatalogError as error:
            refusal = {"code": str(error.code), "message": str(error.message)}
        states_after_refusal = outbox_states(fixture)

        isolation = maintenance.drain_semantic_outbox(fixture.catalog, repair=True)
        states_after_repair = outbox_states(fixture)
        audit_growth: list[dict[str, Any]] = []
        for _ in range(3):
            report = maintenance.drain_semantic_outbox(fixture.catalog, repair=True)
            audit_growth.append(
                {
                    "applied": int(report["applied"]),
                    "scheduled": int(report["repair"]["scheduled"]),
                    "requires_reexecution": int(report["repair"]["requires_reexecution"]),
                    "outbox_rows": _outbox_rows(fixture),
                    "outbox": outbox_states(fixture),
                }
            )
        # A genuinely lost confirmation must still be scheduled, even though the auditor is
        # idempotent on the stuck state above.
        with fixture.catalog.write() as connection:
            connection.execute(
                "DELETE FROM index_entries WHERE space_id = ? AND asset_id = ? AND generation_id = ?",
                (SPACE_ID, first, GENERATION_ID),
            )
        real_repair = maintenance.drain_semantic_outbox(fixture.catalog, repair=True)

        source = give_the_asset_a_real_source_file(fixture, work, target)
        encoder = StubImageEncoder({source.name: vector})
        digest_before = state_digest(fixture)["sha256"]
        recovery = maintenance.reexecute_reclaimed_artifacts(fixture.catalog, encoder=encoder)
        republished_bytes = path.read_bytes()
        lifecycle = str(
            fixture.catalog.read_one(
                "SELECT lifecycle_state FROM artifacts WHERE artifact_id = ?", (target_artifact,)
            )["lifecycle_state"]
        )
        digest_without_drain = state_digest(fixture)["sha256"]
        final = maintenance.drain_semantic_outbox(fixture.catalog)
        audit_after = IndexCoordinator(fixture.catalog).audit_and_schedule_repairs(
            now_ms=benchmark.now_ms()
        )
        row = _row_for(fixture, target) or {}
        entry = _entry_revision(fixture, target)
        ready = semantic_ready(fixture)
        expected_ready = int(fixture.scale) + events
        digest_after = state_digest(fixture)

        dry_run = _run_cli(["reexecute", "--data-root", str(fixture.data_root), "--dry-run", "--json"])
        digest_after_dry_run = state_digest(fixture)["sha256"]
        missing_model = _run_cli(
            [
                "reexecute",
                "--data-root",
                str(fixture.data_root),
                "--model-dir",
                str(work / "no-model-here"),
                "--manifest",
                str(work / "no-manifest.json"),
                "--device",
                "cpu",
                "--json",
            ]
        )
        without_model = _run_cli(["reexecute", "--data-root", str(fixture.data_root), "--json"])

        checks = {
            "one_bad_artifact_refuses_the_drain": refusal is not None
            and refusal["code"] == "ARTIFACT_MISSING"
            and states_after_refusal == {"PENDING": events},
            "the_reclaimed_event_is_isolated_not_dropped": int(isolation["applied"]) == events - 1
            and int(isolation["awaiting_reexecution"]["count"]) == 1
            and int(isolation["index"]["after"]["pending"]) == 1
            and states_after_repair == {"CONFIRMED": events - 1, "PENDING": 1}
            and isolation["awaiting_reexecution"]["sample"][0]["artifact_id"] == target_artifact,
            "repeated_audits_do_not_grow_the_backlog": all(
                item["outbox_rows"] == events and item["outbox"] == states_after_repair
                for item in audit_growth
            ),
            "a_real_loss_is_still_scheduled": int(real_repair["repair"]["scheduled"]) == 1
            and _outbox_rows(fixture) == events + 1,
            "reexecution_republishes_the_identical_bytes": int(recovery["republished"]) == 1
            and lifecycle == "PUBLISHED"
            and republished_bytes == published_bytes,
            "the_republish_alone_projects_nothing": digest_without_drain == digest_before,
            "the_same_event_confirms_afterwards": int(final["applied"]) == 1
            and int(final["awaiting_reexecution"]["count"]) == 0
            and int(row["projection_revision"]) == int(entry["confirmation_revision"])
            and str(entry["state"]) == "CONFIRMED"
            and ready == expected_ready,
            "audit_after_recovery_is_clean": not audit_after["scheduled"]
            and not audit_after["requires_reexecution"],
            "dry_run_lists_without_writing": int(dry_run["returncode"]) == 0
            and int((dry_run["payload"] or {}).get("targets", -1)) == 0
            and digest_after_dry_run == digest_after["sha256"],
            "the_cli_refuses_without_an_explicit_model": int(without_model["returncode"]) == 2
            and int(missing_model["returncode"]) == 1
            and str(((missing_model["payload"] or {}).get("error") or {}).get("code"))
            == "RUNTIME_CAPABILITY_UNAVAILABLE",
        }
        return {
            "ok": all(checks.values()),
            "assets": int(fixture.scale),
            "events": events,
            "target_asset": target,
            "target_artifact": target_artifact,
            "other_artifact": other_artifact,
            "refusal": refusal,
            "isolation": isolation,
            "audit_growth": audit_growth,
            "real_repair": real_repair["repair"],
            "recovery": recovery,
            "final_drain": {
                "applied": int(final["applied"]),
                "awaiting_reexecution": final["awaiting_reexecution"],
                "index_after": final["index"]["after"],
            },
            "digests": {
                "before_recovery": digest_before,
                "after_republish": digest_without_drain,
                "after_drain": digest_after["sha256"],
                "after_dry_run": digest_after_dry_run,
            },
            "cli": {"dry_run": dry_run, "without_model": without_model, "missing_model": missing_model},
            "checks": checks,
        }
    finally:
        fixture.catalog.close()


def _outbox_rows(fixture: Any) -> int:
    return int(fixture.catalog.read_one("SELECT COUNT(*) AS total FROM index_outbox")["total"])


def _run_cli(argv: Sequence[str]) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-m", "photo_search.indexing.cli", *argv],
        cwd=str(PROJECT_ROOT),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=1800,
    )
    payload: dict[str, Any] | None = None
    for line in reversed(completed.stdout.strip().splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        break
    return {
        "argv": ["photo_search.indexing.cli", *argv],
        "returncode": int(completed.returncode),
        "payload": payload,
        "stderr_tail": completed.stderr.strip().splitlines()[-5:],
    }


# --------------------------------------------------------------------------------- cli
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P4-02A semantic-outbox maintenance probe")
    parser.add_argument(
        "--mode",
        default="all",
        choices=("all", "entry", "injections", "contention", "busy-timeout", "recovery", "cli"),
        help="which scenarios to run",
    )
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--assets", type=int, default=400)
    parser.add_argument("--events", type=int, default=300)
    parser.add_argument("--injection-assets", type=int, default=60)
    parser.add_argument("--injection-events", type=int, default=12)
    parser.add_argument("--foreground-writes", type=int, default=30)
    parser.add_argument(
        "--interleave-delay-ms",
        type=int,
        default=30,
        help="extra time one engine write takes (a longer lock hold) in the concurrency cases",
    )
    parser.add_argument(
        "--maintainer-stagger-ms",
        type=int,
        default=60,
        help="how much later the second maintainer joins the first one",
    )
    parser.add_argument(
        "--busy-timeout-hold-ms",
        type=int,
        default=9000,
        help="how long the competing writer holds the lock (must exceed the 5 s busy timeout)",
    )
    parser.add_argument("--retry-limit", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260923)
    parser.add_argument("--distribution", default="clustered")
    parser.add_argument("--output", type=Path, default=None, help="write the JSON report here")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "probe": "p4-02a-index-maintenance",
        "machine": benchmark.machine_facts(),
        "work_dir": str(args.work_dir),
        "seed": int(args.seed),
        "distribution": args.distribution,
        "assets": int(args.assets),
        "events": int(args.events),
    }
    modes = (
        ("entry", scenario_entry),
        ("injections", scenario_injections),
        ("contention", scenario_contention),
        ("busy_timeout", scenario_busy_timeout),
        ("recovery", scenario_recovery),
        ("cli", scenario_cli),
    )
    for name, scenario in modes:
        if args.mode not in ("all", name, name.replace("_", "-")):
            continue
        started = time.perf_counter()
        report[name] = scenario(args.work_dir / name, args)
        report[name]["wall_ms"] = round((time.perf_counter() - started) * 1000, 1)
        log(f"{name}: ok={report[name]['ok']} in {report[name]['wall_ms']} ms")
    executed = [report[name] for name, _ in modes if name in report]
    report["ok"] = all(item["ok"] for item in executed)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload + "\n")
    print(payload)
    return 0 if report["ok"] else 1


if __name__ == "__main__":  # pragma: no cover - script entry point
    raise SystemExit(main())
