"""P4-02A acceptance: the production semantic-outbox entry, its contention and its injections.

The card's question is not "can ``IndexCoordinator`` drain an outbox" (P2-03/P4-01C answered
that) but "is there a *production* entry, what concurrency discipline does it rely on, and
does that discipline still hold when a real second maintainer, a real foreground writer or a
real ``busy_timeout`` refusal is in the way". Every case below therefore drives
``photo_search.indexing.maintenance`` / ``python -m photo_search.indexing.cli`` rather than a
test-local replay loop, and reads its facts back from SQLite and the Lance generation.

The fixture builder is shared with the P4-02A probe (``tools/diagnostics/``), exactly like the
P4-01C tests share the P4-01 benchmark: one real catalog, real embedding artifacts published
through ``ArtifactStore``, real ``index_outbox`` events, real Lance rows.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import CATALOG_UNREADABLE, CatalogError
from photo_search.indexing import cli as index_cli
from photo_search.indexing import maintenance
from photo_search.indexing.store import IndexCoordinator
from photo_search.places import revision as places_revision

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROBE_PATH = PROJECT_ROOT / "tools" / "diagnostics" / "index_maintenance_probe.py"


def load_probe() -> ModuleType:
    spec = importlib.util.spec_from_file_location("p4_02a_probe", PROBE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = load_probe()
benchmark = probe.benchmark


def build(tmp_path: Path, *, scale: int = 40, events: int = 6, seed: int = 20260923) -> Any:
    """A real fixture plus ``events`` real pending ``UPSERT_VECTOR`` events."""
    fixture = probe.fresh_fixture(
        tmp_path / "work", scale=scale, seed=seed, distribution="clustered"
    )
    probe.grow_library(fixture, events=events, seed=seed + 1, prefix="p4-02a")
    return fixture


def open_like_a_second_process(fixture: Any) -> Catalog:
    """A maintainer connection, as a second process would hold it."""
    return Catalog.open(data_root=fixture.data_root)


@contextmanager
def engine_delay(ms: int) -> Any:
    with probe.engine_delay(ms):
        yield


# ---------------------------------------------------------------------------- the entry
def test_the_cli_entry_drains_a_pending_outbox(tmp_path: Path, capsys: Any) -> None:
    fixture = build(tmp_path, events=6)
    try:
        data_root = str(fixture.data_root)
        assert index_cli.main(["status", "--data-root", data_root, "--json"]) == 0
        before = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert before["status"] == "ok"
        assert before["index"]["pending"] == 6
        assert before["index"]["semantic_ready"] == 40

        assert index_cli.main(["drain", "--data-root", data_root, "--json"]) == 0
        report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert report["status"] == "ok"
        assert report["applied"] == 6
        assert report["confirmed_delta"] == 6
        assert report["index"]["after"]["pending"] == 0
        assert report["index"]["after"]["semantic_ready"] == 46
        assert report["write_transactions"] >= 1

        assert index_cli.main(["status", "--data-root", data_root, "--json"]) == 0
        after = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert after["index"]["pending"] == 0

        # A second drain has nothing left to apply and confirms nothing new.
        assert index_cli.main(["drain", "--data-root", data_root, "--json"]) == 0
        again = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert again["applied"] == 0 and again["confirmed_delta"] == 0
    finally:
        fixture.catalog.close()


def test_status_is_read_only_and_usage_errors_exit_two(tmp_path: Path, capsys: Any) -> None:
    missing_root = tmp_path / "nothing-here"
    assert index_cli.main(["status", "--data-root", str(missing_root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload == {"status": "ok", "schema_version": 0, "index": None}
    # The read-only command must not create (or migrate) anything.
    assert not (missing_root / "catalog" / "catalog.db").exists()

    assert index_cli.main([]) == 2
    assert index_cli.main(["drain", "--data-root", str(missing_root), "--limit", "-1"]) == 2
    assert index_cli.main(["drain", "--data-root", str(missing_root), "--batch-size", "0"]) == 2
    capsys.readouterr()


def test_the_entry_bounds_events_and_reports_its_transactions(tmp_path: Path) -> None:
    fixture = build(tmp_path, events=7)
    try:
        # The limit is a bound, not a failure: two events now, the rest stays claimable.
        limited = maintenance.drain_semantic_outbox(fixture.catalog, limit=2, batch_size=1)
        assert limited["applied"] == 2
        assert limited["limit"] == 2
        assert limited["batches"] == limited["write_phases"]["apply"] == 2
        assert limited["index"]["after"]["pending"] == 5

        report = maintenance.drain_semantic_outbox(fixture.catalog, batch_size=3)
        assert report["applied"] == 5
        assert report["batch_size"] == 3
        # 5 remaining events with a batch bound of 3 = 2 engine writes (3 + 2).
        assert report["batches"] == 2
        assert report["write_transactions"] == report["write_phases"]["claim"] + 2
        assert report["lock_wait_ms"]["count"] == report["write_transactions"]
        assert report["lock_wait_ms"]["p50_ms"] is not None
        assert report["write_rejections"]["count"] == 0
        assert report["index"]["after"]["pending"] == 0
        assert all(
            generation["state"] == "READY" for generation in report["index"]["after"]["active_generations"]
        )
        assert probe.state_digest(fixture)["outbox"] == {"CONFIRMED": 7}
    finally:
        fixture.catalog.close()


def test_a_busy_timeout_refusal_is_structured_and_retried(tmp_path: Path) -> None:
    """The only retryable maintenance outcome: an expired ``busy_timeout`` (5 s in this build)."""
    fixture = build(tmp_path, events=4)
    try:
        locked = threading.Event()
        holder_state: dict[str, Any] = {"released": False, "error": None}

        def hold_the_write_lock() -> None:
            holder = sqlite3.connect(
                str(fixture.catalog.db_path), isolation_level=None, timeout=1.0
            )
            try:
                holder.execute("BEGIN IMMEDIATE")
                locked.set()
                time.sleep(6.6)
                holder.execute("ROLLBACK")
                holder_state["released"] = True
            except Exception as error:  # noqa: BLE001 - reported, not swallowed
                holder_state["error"] = f"{type(error).__name__}: {error}"
            finally:
                holder.close()

        thread = threading.Thread(target=hold_the_write_lock, daemon=True)
        thread.start()
        assert locked.wait(timeout=30)
        report = maintenance.drain_semantic_outbox(
            fixture.catalog, batch_size=2, retry_limit=1, retry_backoff_ms=10
        )
        thread.join(timeout=60)

        rejections = report["write_rejections"]
        assert rejections["count"] == 1, rejections
        assert rejections["retries"] == 1
        sample = rejections["samples"][0]
        assert sample["code"] == CATALOG_UNREADABLE
        assert "locked" in sample["message"].lower()
        # The refusal waited out the configured 5 s busy timeout, then the retry recovered.
        # (A refused transaction never ran its body, so it is reported as a rejection with its
        # own wait rather than as a transaction sample.)
        assert sample["phase"] == "claim"
        assert sample["waited_ms"] > 4000
        assert report["batches"] == 2
        assert report["applied"] == 4
        state = probe.state_digest(fixture)
        assert state["outbox"] == {"CONFIRMED": 4}
        assert state["semantic_ready"] == 44
        assert holder_state == {"released": True, "error": None}
    finally:
        fixture.catalog.close()


def test_a_refused_write_leaves_the_events_claimable(tmp_path: Path) -> None:
    """A refusal is transient: nothing is written and a later call still drains everything."""
    fixture = build(tmp_path, events=4)
    try:
        holder = sqlite3.connect(str(fixture.catalog.db_path), isolation_level=None, timeout=1.0)
        holder.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(CatalogError) as refused:
                maintenance.drain_semantic_outbox(
                    fixture.catalog, retry_limit=0, retry_backoff_ms=0
                )
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        assert refused.value.code == CATALOG_UNREADABLE
        assert "locked" in refused.value.message.lower()
        assert probe.outbox_states(fixture) == {"PENDING": 4}

        report = maintenance.drain_semantic_outbox(fixture.catalog)
        assert report["applied"] == 4
        assert probe.state_digest(fixture)["outbox"] == {"CONFIRMED": 4}
    finally:
        fixture.catalog.close()


def test_a_refusal_after_partial_progress_still_reports_every_confirmed_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P4-02A review A1: a retry must add to ``applied``, never replace it.

    A ``busy_timeout`` refusal can land on a *later* batch of the same call, after earlier
    batches were already confirmed. Those confirmations are exactly the events the entry
    reports as its own work - the number P4-02B quotes - so the retry has to accumulate them
    (and re-attempt the rest with the remaining ``limit``) instead of overwriting the count.
    """
    fixture = build(tmp_path, events=5)
    try:
        original = Catalog.write
        calls = {"writes": 0, "refused": 0}

        @contextmanager
        def flaky_write(self: Any) -> Any:
            calls["writes"] += 1
            # #1 claim(1-2), #2 apply(1-2), #3 claim(3-4), #4 apply(3-4) <- refused once
            if calls["writes"] == 4:
                calls["refused"] += 1
                raise CatalogError(
                    CATALOG_UNREADABLE,
                    "cannot start a catalog write transaction: database is locked",
                )
            with original(self) as connection:
                yield connection

        monkeypatch.setattr(Catalog, "write", flaky_write)
        report = maintenance.drain_semantic_outbox(
            fixture.catalog, batch_size=2, retry_limit=1, retry_backoff_ms=0
        )

        assert calls["refused"] == 1
        assert report["write_rejections"]["count"] == 1
        assert report["write_rejections"]["retries"] == 1
        # The first attempt confirmed the first two events, the retry the remaining three.
        assert report["applied"] == report["confirmed_delta"] == 5
        assert report["applied"] <= report["limit"]
        assert probe.state_digest(fixture)["outbox"] == {"CONFIRMED": 5}
        assert probe.semantic_ready(fixture) == 45
    finally:
        fixture.catalog.close()


# ------------------------------------------------------------------------ D-level cases
def test_a_mid_drain_restart_converges_to_the_uninterrupted_state(tmp_path: Path) -> None:
    """D-1: stop halfway, restart the process, and land on exactly the uninterrupted state."""
    events = 12
    reference = build(tmp_path / "reference", events=events, seed=101)
    try:
        assert maintenance.drain_semantic_outbox(reference.catalog)["applied"] == events
        expected = probe.state_digest(reference)
    finally:
        reference.catalog.close()

    interrupted = build(tmp_path / "interrupted", events=events, seed=101)
    try:
        first = maintenance.drain_semantic_outbox(interrupted.catalog, limit=events // 2)
        assert first["applied"] == events // 2
        assert probe.outbox_states(interrupted) == {
            "CONFIRMED": events // 2,
            "PENDING": events - events // 2,
        }

        # The process ends here: its connection, its coordinator and its in-memory samples go.
        data_root = interrupted.data_root
        interrupted.catalog.close()
        reopened = Catalog.open(data_root=data_root)
        interrupted.catalog = reopened
        second = maintenance.drain_semantic_outbox(reopened)
        assert first["applied"] + second["applied"] == events
        recovered = probe.state_digest(interrupted)
        assert recovered["sha256"] == expected["sha256"]
        assert recovered["lance_rows"] == recovered["lance_unique_assets"] == 40 + events
        assert recovered["outbox"] == {"CONFIRMED": events}
    finally:
        interrupted.catalog.close()


def test_the_crash_window_replays_through_the_entry_without_duplicates(tmp_path: Path) -> None:
    """D-2: Lance written, outbox unconfirmed - the entry replays it exactly once."""
    events = 8
    fixture = build(tmp_path, events=events)
    try:
        coordinator = IndexCoordinator(fixture.catalog)
        with pytest.raises(RuntimeError, match="after_lance"):
            coordinator.process_batch(
                now_ms=benchmark.now_ms(), max_events=3, failpoint="after_lance"
            )
        assert probe.outbox_states(fixture) == {"IN_FLIGHT": 3, "PENDING": events - 3}
        assert probe.semantic_ready(fixture) == 40

        report = maintenance.drain_semantic_outbox(fixture.catalog)
        assert report["applied"] == events
        state = probe.state_digest(fixture)
        assert state["outbox"] == {"CONFIRMED": events}
        assert state["lance_rows"] == state["lance_unique_assets"] == 40 + events
        assert state["semantic_ready"] == 40 + events
    finally:
        fixture.catalog.close()


def test_a_content_change_leaves_only_the_newest_vector_reachable(tmp_path: Path) -> None:
    """D-3: the newer projection revision lands, the superseded vector stops matching."""
    fixture = build(tmp_path, events=2)
    try:
        assert maintenance.drain_semantic_outbox(fixture.catalog)["applied"] == 2
        asset_id = 1
        older = fixture.matrix[asset_id - 1]
        newer = benchmark.synthetic_vectors(1, seed=555, distribution="clustered")[0]
        assert probe._asset_distance(fixture, query=older, asset_id=asset_id) <= 1e-6

        benchmark.bump_filter_revision(fixture, cause="CONTENT_CHANGED")
        benchmark.publish_embedding(
            fixture,
            artifact_id="p4-02a-content-change",
            asset_id=asset_id,
            vector=newer,
            now=benchmark.now_ms(),
            input_revision="d" * 64,
        )
        report = maintenance.drain_semantic_outbox(fixture.catalog)
        assert report["applied"] == 1

        row = probe._row_for(fixture, asset_id) or {}
        entry = probe._entry_revision(fixture, asset_id)
        newest_revision = probe._max_event_revision(fixture, asset_id)
        assert int(row["projection_revision"]) == newest_revision == 1
        assert int(entry["confirmation_revision"]) == newest_revision
        assert str(entry["state"]) == "CONFIRMED"
        assert probe._asset_distance(fixture, query=newer, asset_id=asset_id) <= 1e-6
        distance_old = probe._asset_distance(fixture, query=older, asset_id=asset_id)
        assert distance_old is not None and distance_old > 0.0
        rows = probe.lance_rows(fixture)
        assert len(rows) == len({int(row["asset_id"]) for row in rows}) == 42
    finally:
        fixture.catalog.close()


def test_two_maintainers_confirm_every_event_exactly_once(tmp_path: Path) -> None:
    """D-4: two connections drain concurrently; invariants 1 and 3 both hold."""
    events = 12
    fixture = build(tmp_path, events=events)
    try:
        target = 41
        newest = benchmark.synthetic_vectors(1, seed=777, distribution="clustered")[0]
        benchmark.bump_filter_revision(fixture, cause="CONTENT_CHANGED")
        benchmark.publish_embedding(
            fixture,
            artifact_id="p4-02a-two-maintainers",
            asset_id=target,
            vector=newest,
            now=benchmark.now_ms(),
            input_revision="e" * 64,
        )
        total_events = events + 1
        results: list[dict[str, Any] | None] = [None, None]
        failures: list[str | None] = [None, None]

        def maintainer(index: int) -> None:
            handle = open_like_a_second_process(fixture)
            try:
                results[index] = maintenance.drain_semantic_outbox(
                    handle,
                    limit=total_events * 2,
                    batch_size=2,
                    retry_limit=5,
                    retry_backoff_ms=20,
                )
            except BaseException as error:  # noqa: BLE001 - reported, not swallowed
                failures[index] = f"{type(error).__name__}: {error}"
            finally:
                handle.close()

        with engine_delay(25):
            first = threading.Thread(target=maintainer, args=(0,))
            first.start()
            assert probe._wait_for_confirmed_events(fixture, minimum=1, timeout_s=30)
            second = threading.Thread(target=maintainer, args=(1,))
            second.start()
            for thread in (first, second):
                thread.join(timeout=300)
        assert failures == [None, None]

        leftover = maintenance.drain_semantic_outbox(fixture.catalog)
        applied = [int(item["applied"]) for item in results if item is not None]
        assert sum(applied) + int(leftover["applied"]) == total_events
        # The second maintainer really ran against the first one's write transaction: it
        # either applied events itself or was blocked by a transaction it then found confirmed.
        contender = results[1]
        assert contender is not None
        assert contender["applied"] > 0 or (
            contender["write_transactions"] >= 1
            and float(contender["lock_wait_ms"]["max_ms"] or 0.0) >= 10.0
        )
        assert leftover["applied"] == 0

        state = probe.state_digest(fixture)
        assert state["outbox"] == {"CONFIRMED": total_events}
        assert state["lance_rows"] == state["lance_unique_assets"] == 40 + events
        row = probe._row_for(fixture, target) or {}
        entry = probe._entry_revision(fixture, target)
        newest_revision = probe._max_event_revision(fixture, target)
        assert int(row["projection_revision"]) == newest_revision == 1
        assert int(entry["confirmation_revision"]) == newest_revision
        assert probe._asset_distance(fixture, query=newest, asset_id=target) <= 1e-6
    finally:
        fixture.catalog.close()


def test_a_foreground_writer_is_never_silently_lost_during_maintenance(tmp_path: Path) -> None:
    """Invariant 6: every concurrent foreground write commits or fails structurally."""
    fixture = build(tmp_path, events=12)
    try:
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
        record: dict[str, Any] = {"committed": 0, "refusals": [], "waits": []}
        stop = threading.Event()

        def foreground_writer() -> None:
            handle = open_like_a_second_process(fixture)
            try:
                for _ in range(12):
                    if stop.is_set():
                        break
                    started = time.perf_counter()
                    try:
                        with handle.write() as connection:
                            acquired = time.perf_counter()
                            places_revision.bump_filter_revision(
                                connection, cause="CONTENT_CHANGED", now_ms=benchmark.now_ms()
                            )
                    except CatalogError as error:
                        record["refusals"].append((error.code, error.message))
                        continue
                    record["waits"].append((acquired - started) * 1000)
                    record["committed"] += 1
            finally:
                handle.close()

        with engine_delay(40):
            writer = threading.Thread(target=foreground_writer, daemon=True)
            writer.start()
            report = maintenance.drain_semantic_outbox(
                fixture.catalog, retry_limit=5, retry_backoff_ms=20
            )
            writer.join(timeout=300)
        stop.set()

        # No silent failure: the singleton row moved exactly as often as a writer committed,
        # and each refusal is the structured, retryable lock claim.
        assert int(
            fixture.catalog.read_one(
                "SELECT revision FROM catalog_filter_revision WHERE revision_id = 1"
            )["revision"]
        ) == revision_before + int(record["committed"])
        assert int(
            fixture.catalog.read_one(
                "SELECT COUNT(*) AS total FROM catalog_filter_revision_events"
            )["total"]
        ) == events_before + int(record["committed"])
        for code, message in record["refusals"]:
            assert code == CATALOG_UNREADABLE
            assert "locked" in message.lower()
        assert len(record["waits"]) == int(record["committed"])

        assert report["applied"] == 12
        state = probe.state_digest(fixture)
        assert state["outbox"] == {"CONFIRMED": 12}
        assert state["lance_rows"] == state["lance_unique_assets"]
    finally:
        fixture.catalog.close()
