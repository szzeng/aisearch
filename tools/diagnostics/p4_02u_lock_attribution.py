"""P4-02U - who holds the SQLite write lock while a legal foreground search is refused.

Measurement / attribution only: **no product file is modified**. The probe works on the
throw-away P4-02J copy of the 30-man fixture (its state is recorded in
``test-results/p4-02j-contention-close.json``), publishes a fresh burst of real embedding
artifacts exactly like the AI worker, and then runs the four control arms of the card:

* ``arm-control``      - a third-party connection really holds ``BEGIN IMMEDIATE`` for 6 s
                         while one search runs: validates that the instrument observes a
                         >=5 s hold as ``CATALOG_UNREADABLE`` (positive control);
* ``arm-no-drain``     - the same non-empty projection queue, no background work;
* ``arm-drain-process``- the product drain as a subprocess (faithful) plus a lock timeline;
* ``arm-drain-thread`` - the same drain through the product API inside this process, with
                         every ``Catalog.write`` / ``_timed_write`` phase window timestamped
                         and thread stacks dumped while the lock stays unavailable. This is
                         the arm that names the holder.

Runtime instrumentation is monkeypatching inside this process only; the repository code and
the fixture are left exactly as they are (the fixture is *meant* to be consumed).
"""

from __future__ import annotations

import argparse
import faulthandler
import hashlib
import json
import os
import platform
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "tools" / "diagnostics"))

import ann_scale_benchmark as bench  # noqa: E402
import capacity_validation as cap  # noqa: E402

from photo_search.catalog.db import Catalog  # noqa: E402
from photo_search.indexing import maintenance  # noqa: E402
from photo_search.indexing.store import IndexCoordinator, LanceVectorIndex  # noqa: E402
from photo_search.search.service import search_assets  # noqa: E402

ROOT = PROJECT_ROOT / "tmp" / "p402j-contention-close" / "data"
OUT_DIR = PROJECT_ROOT / "test-results"
WORK = PROJECT_ROOT / "tmp" / "p402u"
DRAIN_BATCH = cap.DRAIN_BATCH
TIMELINE_LIMIT = 6000


def now_ms() -> int:
    return int(time.time() * 1000)


def elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


def counts(catalog: Catalog) -> dict[str, Any]:
    outbox = {
        str(row["state"]): int(row["total"])
        for row in catalog.read_all(
            "SELECT state, COUNT(*) AS total FROM index_outbox GROUP BY state"
        )
    }
    return {
        "assets": int(catalog.read_one("SELECT COUNT(*) FROM assets")[0]),
        "entries_confirmed": int(
            catalog.read_one("SELECT COUNT(*) FROM index_entries WHERE state = 'CONFIRMED'")[0]
        ),
        "entries_pending_repair": int(
            catalog.read_one(
                "SELECT COUNT(*) FROM index_entries WHERE state = 'PENDING_REPAIR'"
            )[0]
        ),
        "projection_queue": int(
            catalog.read_one("SELECT COUNT(*) FROM search_projection_queue")[0]
        ),
        "outbox": outbox,
    }


# ------------------------------------------------------------------ runtime tracing (no src change)
class Trace:
    """Timestamped catalog-write and drain-phase windows of *this* process."""

    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []
        self.phases: list[dict[str, Any]] = []
        self.engine: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._installed = False

    def install(self) -> None:
        if self._installed:
            return
        self._installed = True
        original_write = Catalog.write
        original_timed = IndexCoordinator._timed_write
        original_upsert = LanceVectorIndex.upsert_batch
        trace = self

        @contextmanager
        def traced_write(instance: Catalog):  # type: ignore[no-untyped-def]
            record = {
                "conn": id(instance),
                "thread": threading.get_ident(),
                "enter_ms": round((time.perf_counter() - trace.origin) * 1000, 3),
                "acquired_ms": None,
                "exit_ms": None,
                "rejected_ms": None,
                "error": None,
            }
            with trace._lock:
                trace.writes.append(record)
            try:
                with original_write(instance) as connection:
                    record["acquired_ms"] = round(
                        (time.perf_counter() - trace.origin) * 1000, 3
                    )
                    try:
                        yield connection
                    finally:
                        record["exit_ms"] = round(
                            (time.perf_counter() - trace.origin) * 1000, 3
                        )
            except BaseException as error:  # a refusal is evidence here, not noise
                record["rejected_ms"] = round(
                    (time.perf_counter() - trace.origin) * 1000, 3
                )
                record["error"] = type(error).__name__
                raise

        @contextmanager
        def traced_timed(instance: IndexCoordinator, phase: str):  # type: ignore[no-untyped-def]
            record = {
                "phase": str(phase),
                "thread": threading.get_ident(),
                "enter_ms": round((time.perf_counter() - trace.origin) * 1000, 3),
                "acquire_ms": None,
                "end_ms": None,
            }
            with original_timed(instance, phase) as connection:
                record["acquire_ms"] = round(
                    (time.perf_counter() - trace.origin) * 1000, 3
                )
                with trace._lock:
                    trace.phases.append(record)
                try:
                    yield connection
                finally:
                    record["end_ms"] = round(
                        (time.perf_counter() - trace.origin) * 1000, 3
                    )

        def timed_upsert(instance: Any, **kwargs: Any) -> Any:  # type: ignore[no-untyped-def]
            record = {
                "thread": threading.get_ident(),
                "enter_ms": round((time.perf_counter() - trace.origin) * 1000, 3),
            }
            with trace._lock:
                trace.engine.append(record)
            try:
                return original_upsert(instance, **kwargs)
            finally:
                record["exit_ms"] = round(
                    (time.perf_counter() - trace.origin) * 1000, 3
                )

        Catalog.write = traced_write  # type: ignore[method-assign]
        IndexCoordinator._timed_write = traced_timed  # type: ignore[method-assign]
        LanceVectorIndex.upsert_batch = timed_upsert  # type: ignore[method-assign]

    origin = 0.0


# ------------------------------------------------------------------ lock timeline
class LockTimeline(threading.Thread):
    """Independent connection: is the single-writer lock obtainable right now?"""

    def __init__(self, db_path: Path, *, timeout_ms: int = 40, tick_ms: float = 8.0) -> None:
        super().__init__(daemon=True)
        self.db_path = Path(db_path)
        self.timeout_ms = int(timeout_ms)
        self.tick = float(tick_ms) / 1000.0
        self.samples: list[dict[str, Any]] = []
        self._stop_event = threading.Event()
        self.origin = 0.0

    def run(self) -> None:
        connection = sqlite3.connect(str(self.db_path), isolation_level=None, timeout=0.2)
        connection.execute(f"PRAGMA busy_timeout = {self.timeout_ms}")
        try:
            while not self._stop_event.is_set():
                started = time.perf_counter()
                entry: dict[str, Any] = {
                    "t_ms": round((started - self.origin) * 1000, 3),
                }
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute("ROLLBACK")
                    entry["state"] = "available"
                except sqlite3.Error as error:
                    entry["state"] = "blocked"
                    entry["error"] = str(error)
                    try:
                        connection.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                entry["probe_ms"] = elapsed_ms(started)
                wal = self.db_path.with_name(self.db_path.name + "-wal")
                try:
                    entry["wal_bytes"] = int(wal.stat().st_size)
                except OSError:
                    entry["wal_bytes"] = None
                if len(self.samples) < TIMELINE_LIMIT:
                    self.samples.append(entry)
                time.sleep(self.tick)
        finally:
            connection.close()

    def stop(self) -> None:
        self._stop_event.set()


class CheckpointSampler(threading.Thread):
    """How long a PASSIVE checkpoint takes, and how big the WAL is (checkpoint hypothesis)."""

    def __init__(self, db_path: Path, *, tick_ms: float = 250.0) -> None:
        super().__init__(daemon=True)
        self.db_path = Path(db_path)
        self.tick = float(tick_ms) / 1000.0
        self.samples: list[dict[str, Any]] = []
        self._stop_event = threading.Event()
        self.origin = 0.0

    def run(self) -> None:
        connection = sqlite3.connect(str(self.db_path), isolation_level=None, timeout=0.2)
        connection.execute("PRAGMA busy_timeout = 40")
        try:
            while not self._stop_event.is_set():
                started = time.perf_counter()
                entry: dict[str, Any] = {
                    "t_ms": round((started - self.origin) * 1000, 3)
                }
                try:
                    row = connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
                    entry["busy"] = int(row[0])
                    entry["log_frames"] = int(row[1])
                    entry["checkpointed_frames"] = int(row[2])
                except sqlite3.Error as error:
                    entry["error"] = str(error)
                entry["ms"] = elapsed_ms(started)
                if len(self.samples) < TIMELINE_LIMIT:
                    self.samples.append(entry)
                time.sleep(self.tick)
        finally:
            connection.close()

    def stop(self) -> None:
        self._stop_event.set()


# ------------------------------------------------------------------ fixture work
def inject(catalog: Catalog, *, events: int, log: Any) -> dict[str, Any]:
    source_id = int(
        catalog.read_one("SELECT source_id FROM source_roots ORDER BY source_id LIMIT 1")[
            "source_id"
        ]
    )
    first_asset = int(catalog.read_one("SELECT MAX(asset_id) FROM assets")[0]) + 1
    started = time.perf_counter()
    with catalog.write() as connection:
        for offset in range(events):
            asset_id = first_asset + offset
            relative = f"bucket-lock-attr/photo-{asset_id:08d}.jpg"
            connection.execute(
                "INSERT INTO assets(sha256, byte_size, width, height, format, created_at, "
                "state) VALUES (?, ?, 4032, 3024, 'JPEG', ?, 'ACTIVE')",
                (
                    hashlib.sha256(f"p402u-lock-{asset_id}".encode()).hexdigest(),
                    2_000_000,
                    now_ms(),
                ),
            )
            connection.execute(
                "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, "
                "size, mtime_ns, last_seen_scan_id, availability) "
                "VALUES (?, ?, ?, ?, 2000000, 1700000000000000000, NULL, 'PRESENT')",
                (source_id, relative, relative, asset_id),
            )
    assets_ms = elapsed_ms(started)
    fixture = cap.Fixture(catalog, ROOT)
    vectors = bench.synthetic_vectors(events, seed=20260923 + 977, distribution="clustered")
    started = time.perf_counter()
    for offset in range(events):
        if offset % 2000 == 0:
            log(f"  publish {offset}/{events}")
        bench.publish_embedding(
            fixture,
            artifact_id=f"p402u-lock-{first_asset + offset}",
            asset_id=first_asset + offset,
            vector=vectors[offset],
            now=now_ms(),
        )
    publish_ms = elapsed_ms(started)
    return {
        "events": events,
        "first_asset_id": first_asset,
        "assets_insert_ms": assets_ms,
        "publish_ms": publish_ms,
        "publish_ms_per_event": round(publish_ms / events, 2),
        "after_publish": counts(catalog),
    }


def search_once(catalog: Catalog, encoder: Any, text: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        search_assets(
            catalog,
            text=text,
            top_k=20,
            encoder=encoder,
            space_id=bench.SPACE_ID,
            generation_id=bench.GENERATION_ID,
            dimension=bench.DIM,
        )
        return {"ms": elapsed_ms(started), "refused": False}
    except Exception as error:  # noqa: BLE001 - a refusal is the measurement
        return {
            "ms": elapsed_ms(started),
            "refused": True,
            "error": type(error).__name__,
            "detail": str(error)[:200],
        }


def run_searches(catalog: Catalog, encoder: Any, text: str, *, samples: int, log: Any,
                 timeline: LockTimeline | None = None, trace: Trace | None = None,
                 dumps: Any = None) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for index in range(samples):
        at = elapsed_ms(trace.origin) if trace else None
        result = search_once(catalog, encoder, text)
        result["index"] = index + 1
        result["at_ms"] = at
        results.append(result)
        log(
            f"  search[{index + 1}] {result['ms']:.1f} ms "
            f"{'REFUSED' if result['refused'] else 'ok'}"
        )
    if timeline is not None:
        blocked = sum(1 for item in timeline.samples if item["state"] == "blocked")
        log(f"  lock timeline: {len(timeline.samples)} probes, {blocked} blocked")
    return {
        "samples": results,
        "count": len(results),
        "refused": sum(1 for item in results if item["refused"]),
    }


# ------------------------------------------------------------------ arms
def arm_control(args: argparse.Namespace, log: Any) -> dict[str, Any]:
    """A deliberate third-party writer holds the lock for 6 s while one search runs."""
    catalog = Catalog.open(data_root=ROOT)
    trace = Trace()
    trace.origin = time.perf_counter()
    timeline = LockTimeline(catalog.db_path)
    timeline.origin = trace.origin
    timeline.start()
    report: dict[str, Any] = {"arm": "control", "holder_seconds": args.holder_seconds}
    try:
        encoder = bench.DeterministicEncoder()
        holder: dict[str, Any] = {}

        def hold() -> None:
            connection = sqlite3.connect(str(catalog.db_path), isolation_level=None, timeout=5.0)
            connection.execute("PRAGMA busy_timeout = 5000")
            time.sleep(0.3)
            connection.execute("BEGIN IMMEDIATE")
            holder["acquired"] = time.perf_counter()
            log(f"  third-party holder acquired BEGIN IMMEDIATE for {args.holder_seconds}s")
            time.sleep(float(args.holder_seconds))
            connection.execute("ROLLBACK")
            connection.close()
            holder["released"] = time.perf_counter()

        worker_thread = threading.Thread(target=hold, daemon=True)
        worker_thread.start()
        while "acquired" not in holder:
            time.sleep(0.01)
        report["search"] = run_searches(
            catalog, encoder, cap.DEFAULT_QUERY_TEXT, samples=1, log=log, timeline=timeline
        )
        worker_thread.join(timeout=30)
        report["holder_held_ms"] = round(
            (holder.get("released", time.perf_counter()) - holder["acquired"]) * 1000, 3
        )
    finally:
        timeline.stop()
        timeline.join(timeout=5)
        catalog.close()
    blocked = [item for item in timeline.samples if item["state"] == "blocked"]
    report["timeline"] = timeline.samples
    report["blocked_probes"] = len(blocked)
    report["longest_blocked_ms"] = round(
        max([item["probe_ms"] for item in blocked], default=0.0), 3
    )
    return report


def arm_no_drain(args: argparse.Namespace, log: Any) -> dict[str, Any]:
    catalog = Catalog.open(data_root=ROOT)
    timeline = LockTimeline(catalog.db_path)
    timeline.origin = time.perf_counter()
    timeline.start()
    try:
        encoder = bench.DeterministicEncoder()
        report = {
            "arm": "no-drain",
            "search": run_searches(
                catalog, encoder, cap.DEFAULT_QUERY_TEXT,
                samples=args.samples, log=log, timeline=timeline,
            ),
        }
    finally:
        timeline.stop()
        timeline.join(timeout=5)
        catalog.close()
    report["timeline"] = timeline.samples
    return report


def arm_drain_process(args: argparse.Namespace, log: Any) -> dict[str, Any]:
    """Faithful shape: the drain is a subprocess; the lock timeline is external to it."""
    catalog = Catalog.open(data_root=ROOT)
    timeline = LockTimeline(catalog.db_path)
    timeline.origin = time.perf_counter()
    timeline.start()
    dumps = open(WORK / "thread-dumps-process.txt", "a", encoding="utf-8")
    stopper = threading.Thread(
        target=_dump_while_blocked, args=(timeline, dumps, 1200.0), daemon=True
    )
    stopper.start()
    try:
        encoder = bench.DeterministicEncoder()
        worker = cap.spawn_cli(
            [
                "drain", "--data-root", str(ROOT), "--json",
                "--limit", str(args.events + 16), "--batch-size", str(DRAIN_BATCH),
            ]
        )
        started = time.perf_counter()
        report = {
            "arm": "drain-process",
            "search": run_searches(
                catalog, encoder, cap.DEFAULT_QUERY_TEXT,
                samples=args.samples, log=log, timeline=timeline,
            ),
        }
        overlap_ms = elapsed_ms(started)
        stdout, stderr = worker.communicate()
        drain = cap.cli_result(worker, started=started, stdout=stdout, stderr=stderr)
        payload = drain.get("payload") or {}
        report["overlap_ms"] = overlap_ms
        report["drain"] = {
            "returncode": drain["returncode"],
            "status": payload.get("status"),
            "applied": payload.get("applied"),
            "batches": payload.get("batches"),
            "duration_ms": payload.get("duration_ms"),
            "write_hold_ms": payload.get("write_hold_ms"),
            "lock_wait_ms": payload.get("lock_wait_ms"),
            "write_rejections": payload.get("write_rejections"),
            "write_phases": payload.get("write_phases"),
            "stderr_tail": drain["stderr_tail"],
        }
    finally:
        timeline.stop()
        timeline.join(timeout=5)
        dumps.close()
        catalog.close()
    report["timeline"] = timeline.samples
    return report


def arm_drain_thread(args: argparse.Namespace, log: Any) -> dict[str, Any]:
    """Attribution: the drain runs in this process so every write window has a timestamp."""
    catalog = Catalog.open(data_root=ROOT)
    trace = Trace()
    trace.origin = time.perf_counter()
    trace.install()
    timeline = LockTimeline(catalog.db_path)
    timeline.origin = trace.origin
    timeline.start()
    checkpoints = CheckpointSampler(catalog.db_path)
    checkpoints.origin = trace.origin
    checkpoints.start()
    dumps_path = WORK / "thread-dumps-thread.txt"
    dumps = open(dumps_path, "a", encoding="utf-8")
    dumps.write(f"\n===== arm-drain-thread run at {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    stopper = threading.Thread(
        target=_dump_while_blocked, args=(timeline, dumps, 1200.0), daemon=True
    )
    stopper.start()
    result: dict[str, Any] = {}

    def drain() -> None:
        background = Catalog.open(data_root=ROOT)
        try:
            result["payload"] = maintenance.drain_semantic_outbox(
                background, limit=args.events + 16, batch_size=DRAIN_BATCH
            )
        except BaseException as error:  # noqa: BLE001
            result["error"] = f"{type(error).__name__}: {error}"
        finally:
            background.close()

    worker = threading.Thread(target=drain, name="p4-02u-drain", daemon=True)
    try:
        encoder = bench.DeterministicEncoder()
        worker.start()
        started = time.perf_counter()
        report = {
            "arm": "drain-thread",
            "search": run_searches(
                catalog, encoder, cap.DEFAULT_QUERY_TEXT,
                samples=args.samples, log=log, timeline=timeline, trace=trace,
            ),
        }
        worker.join(timeout=args.drain_timeout)
        report["overlap_ms"] = elapsed_ms(started)
        report["drain_thread_alive"] = worker.is_alive()
        payload = result.get("payload") or {}
        applied = int(payload.get("applied") or 0)
        report["arm_valid"] = bool(
            not result.get("error") and applied > 0 and trace.phases
        )
        report["arm_invalid_reason"] = (
            None
            if report["arm_valid"]
            else (result.get("error") or "the drain applied nothing / no phase windows")
        )
        report["drain"] = {
            "error": result.get("error"),
            "applied": payload.get("applied"),
            "batches": payload.get("batches"),
            "duration_ms": payload.get("duration_ms"),
            "write_hold_ms": payload.get("write_hold_ms"),
            "lock_wait_ms": payload.get("lock_wait_ms"),
            "write_rejections": payload.get("write_rejections"),
        }
    finally:
        timeline.stop()
        timeline.join(timeout=5)
        checkpoints.stop()
        checkpoints.join(timeout=5)
        dumps.close()
        catalog.close()
    report["timeline"] = timeline.samples
    report["checkpoint_samples"] = checkpoints.samples
    report["write_windows"] = trace.writes
    report["phase_windows"] = trace.phases
    report["engine_windows"] = trace.engine
    report["dumps_path"] = str(dumps_path)
    return report


def arm_interleaved(args: argparse.Namespace, log: Any) -> dict[str, Any]:
    """Same fixture state, alternating A/B: the drain writing vs the drain held back."""
    catalog = Catalog.open(data_root=ROOT)
    pause = threading.Event()
    stop = threading.Event()
    state: dict[str, Any] = {"applied": 0, "batches": 0, "error": None, "holds_ms": []}
    trace = Trace()
    trace.origin = time.perf_counter()
    trace.install()
    timeline = LockTimeline(catalog.db_path)
    timeline.origin = trace.origin
    timeline.start()

    def drain_loop() -> None:
        background = Catalog.open(data_root=ROOT)
        try:
            coordinator = IndexCoordinator(background)
            coordinator.ensure_scalar_indexes()
            while not stop.is_set():
                while pause.is_set() and not stop.is_set():
                    time.sleep(0.02)
                if stop.is_set():
                    break
                applied = int(
                    coordinator.process_batch(now_ms=now_ms(), max_events=DRAIN_BATCH) or 0
                )
                state["applied"] += applied
                state["batches"] += 1 if applied else 0
                if not applied:
                    break
            state["holds_ms"] = [
                round(sample["hold_ms"], 3) for sample in coordinator.write_samples
            ]
        except BaseException as error:  # noqa: BLE001
            state["error"] = f"{type(error).__name__}: {error}"
        finally:
            background.close()

    worker = threading.Thread(target=drain_loop, name="p4-02u-paced-drain", daemon=True)
    worker.start()
    encoder = bench.DeterministicEncoder()
    pairs: list[dict[str, Any]] = []
    try:
        for index in range(int(args.pairs)):
            pause.clear()
            time.sleep(0.2)
            running = search_once(catalog, encoder, cap.DEFAULT_QUERY_TEXT)
            pause.set()
            time.sleep(float(args.pause_settle))
            held = search_once(catalog, encoder, cap.DEFAULT_QUERY_TEXT)
            pairs.append({"pair": index + 1, "drain_running": running, "drain_held": held})
            log(
                f"  pair[{index + 1}] drain-running="
                f"{'REFUSED' if running['refused'] else 'ok'} ({running['ms']:.0f} ms), "
                f"drain-held={'REFUSED' if held['refused'] else 'ok'} ({held['ms']:.0f} ms)"
            )
    finally:
        pause.set()
        stop.set()
        worker.join(timeout=300)
        timeline.stop()
        timeline.join(timeout=5)
        catalog.close()
    holds = sorted(state.pop("holds_ms", []))
    report: dict[str, Any] = {
        "arm": "interleaved",
        "pairs": pairs,
        "pause_settle_ms": float(args.pause_settle) * 1000.0,
        "drain": {
            **state,
            "write_hold_p50_ms": holds[len(holds) // 2] if holds else None,
            "write_hold_max_ms": holds[-1] if holds else None,
        },
        "phase_windows": trace.phases,
        "engine_windows": trace.engine,
        "timeline": timeline.samples,
        "summary": {
            "pairs": len(pairs),
            "drain_running_refused": sum(
                1 for pair in pairs if pair["drain_running"]["refused"]
            ),
            "drain_held_refused": sum(1 for pair in pairs if pair["drain_held"]["refused"]),
        },
    }
    report["arm_valid"] = bool(not state["error"] and state["applied"] > 0 and pairs)
    report["arm_invalid_reason"] = (
        None if report["arm_valid"] else (state["error"] or "the paced drain applied nothing")
    )
    return report


# ------------------------------------------------------------------ P4-02V additions
def install_legacy_catch_up() -> None:
    """Restore the pre-P4-02V catch-up (verbatim) so this driver can measure the *before* shape.

    The pre-fix algorithm raised ``CATALOG_UNREADABLE`` as soon as the 5 s maintenance budget
    expired; that is exactly the refusal P4-02U recorded (thread arm 5/6, process arm 4/6).
    """
    from photo_search.search import documents as search_documents

    def legacy_catch_up(
        catalog: Any, *, batches: int = 4, batch_size: int = 200
    ) -> dict[str, Any]:
        if search_documents.queue_depth(catalog.connection) == 0:
            return {"processed": 0, "written": 0, "remaining": 0, "batches": 0, "state": "SYNCED"}
        processed = written = used = 0
        remaining = 0
        for _ in range(max(1, int(batches))):
            with catalog.write() as connection:
                result = search_documents.drain_queue(
                    connection, now_ms=catalog.now_ms(), limit=int(batch_size)
                )
            processed += int(result["processed"])
            written += int(result["written"])
            remaining = int(result["remaining"])
            used += 1
            if int(result["processed"]) < int(batch_size):
                break
        return {
            "processed": processed,
            "written": written,
            "remaining": remaining,
            "batches": used,
            "state": "SYNCED" if remaining == 0 else "LAGGING",
        }

    search_documents.catch_up = legacy_catch_up  # type: ignore[assignment]


def seed_reembed(catalog: Catalog, *, events: int, log: Any) -> dict[str, Any]:
    """Publish fresh embeddings for *existing* assets: real events without growing the fixture."""
    rows = catalog.read_all(
        "SELECT asset_id FROM assets ORDER BY asset_id DESC LIMIT ?", (int(events),)
    )
    ids = [int(row["asset_id"]) for row in rows]
    if len(ids) != int(events):
        raise SystemExit(f"asked for {events} assets, found {len(ids)}")
    fixture = cap.Fixture(catalog, ROOT)
    revision = bench.bump_filter_revision(fixture, cause="p4-02v-reembed")
    vectors = bench.synthetic_vectors(len(ids), seed=20260928, distribution="clustered")
    started = time.perf_counter()
    for index, asset_id in enumerate(ids):
        if index % 1000 == 0:
            log(f"  reembed {index}/{len(ids)}")
        bench.publish_embedding(
            fixture,
            artifact_id=f"p4v-reembed-{revision}-{asset_id}",
            asset_id=asset_id,
            vector=vectors[index],
            now=now_ms(),
            input_revision=hashlib.sha256(f"p4v-{revision}-{asset_id}".encode()).hexdigest(),
        )
    elapsed = elapsed_ms(started)
    return {
        "events": len(ids),
        "filter_revision": revision,
        "publish_ms": elapsed,
        "ms_per_event": round(elapsed / len(ids), 3),
        "after_publish": counts(catalog),
    }


def seed_bulk(catalog: Catalog, *, events: int, log: Any) -> dict[str, Any]:
    """Queue a re-projection backlog in the exact row shape the product's publish writes.

    ``ArtifactStore.persist_result`` writes one ``UPSERT_VECTOR`` row per published embedding
    (``event_id``/payload/checksum/state/attempts/timestamps). This fixture writes the *same*
    shape for artifacts that already exist, at one new ``catalog_filter_revision`` - i.e. the
    "project this vector again" backlog the outbox is designed for, without publishing 100k new
    files. The drain validates every row it claims (payload checksum, space/generation match,
    supersede order), so a malformed injection would be refused rather than silently applied.
    """
    from photo_search.scheduler.contracts import canonical_json

    rows = catalog.read_all(
        "SELECT e.asset_id, e.artifact_id, e.generation_id, e.space_id, a.dimension "
        "FROM index_entries e JOIN artifacts a ON a.artifact_id = e.artifact_id "
        "WHERE e.state = 'CONFIRMED' ORDER BY e.asset_id DESC LIMIT ?",
        (int(events),),
    )
    if len(rows) != int(events):
        raise SystemExit(f"asked for {events} confirmed entries, found {len(rows)}")
    revision = bench.bump_filter_revision(cap.Fixture(catalog, ROOT), cause="p4-02v-refresh")
    stamp = now_ms()
    started = time.perf_counter()
    with catalog.write() as connection:
        for row in rows:
            asset_id = int(row["asset_id"])
            artifact_id = str(row["artifact_id"])
            generation_id = str(row["generation_id"])
            space_id = str(row["space_id"])
            payload = {
                "artifact_id": artifact_id,
                "asset_id": asset_id,
                "generation_id": generation_id,
                "projection_revision": int(revision),
                "space_id": space_id,
            }
            payload_json = canonical_json(payload)
            connection.execute(
                "INSERT OR IGNORE INTO index_outbox("
                "event_id, event_type, artifact_id, space_id, asset_id, generation_id, "
                "projection_revision, payload_json, payload_checksum, state, attempts, "
                "last_error_code, created_at, updated_at"
                ") VALUES (?, 'UPSERT_VECTOR', ?, ?, ?, ?, ?, ?, ?, 'PENDING', 0, NULL, ?, ?)",
                (
                    "p4v-"
                    + hashlib.sha256(
                        f"{artifact_id}\0{generation_id}\0{asset_id}\0{revision}".encode()
                    ).hexdigest(),
                    artifact_id,
                    space_id,
                    asset_id,
                    generation_id,
                    int(revision),
                    payload_json,
                    hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
                    stamp,
                    stamp,
                ),
            )
    elapsed = elapsed_ms(started)
    log(f"  queued {len(rows)} re-projection events at revision {revision}")
    return {
        "events": len(rows),
        "filter_revision": int(revision),
        "schedule_ms": elapsed,
        "ms_per_event": round(elapsed / max(1, len(rows)), 3),
        "after_schedule": counts(catalog),
    }


def seed_queue(catalog: Catalog, *, events: int, log: Any) -> dict[str, Any]:
    """Refill ``search_projection_queue`` in the exact shape the product's triggers write.

    A legal text search only touches the writer lock when the projection queue is non-empty
    (``catch_up`` short-circuits on an empty queue), and earlier samples in this session had
    drained it. The triggers queue ``(asset_id, FILENAME_CHANGED, now)`` with an upsert, and
    this fixture does the same for existing assets - no derived row is written, so the lag the
    searches report stays real.
    """
    stamp = now_ms()
    started = time.perf_counter()
    with catalog.write() as connection:
        connection.executemany(
            "INSERT INTO search_projection_queue(asset_id, cause, queued_at) "
            "VALUES (?, 'FILENAME_CHANGED', ?) "
            "ON CONFLICT(asset_id) DO UPDATE SET cause = excluded.cause, "
            "queued_at = excluded.queued_at",
            [
                (int(row["asset_id"]), stamp)
                for row in catalog.read_all(
                    "SELECT asset_id FROM assets ORDER BY asset_id DESC LIMIT ?", (int(events),)
                )
            ],
        )
    elapsed = elapsed_ms(started)
    log(f"  queued {events} projection rows in {elapsed} ms")
    return {
        "events": int(events),
        "queue_ms": elapsed,
        "after_schedule": counts(catalog),
    }


def seed_refresh(catalog: Catalog, *, events: int, log: Any) -> dict[str, Any]:
    """Queue re-projection events for existing confirmed vectors (the product's own entry).

    ``IndexCoordinator.schedule_projection_refresh`` is what the manual-knowledge services call
    inside an authority transaction: it queues "project this existing artifact" for the assets
    that already have a confirmed vector, with no new artifact and no file write. That gives
    this driver a backlog of real ``UPSERT_VECTOR`` events at a tiny cost, so a drain window
    can be long enough to hold >= 30 genuinely overlapped foreground samples.
    """
    from photo_search.indexing.store import IndexCoordinator

    rows = catalog.read_all(
        "SELECT asset_id FROM index_entries WHERE state = 'CONFIRMED' "
        "ORDER BY asset_id DESC LIMIT ?",
        (int(events),),
    )
    ids = sorted(int(row["asset_id"]) for row in rows)
    if len(ids) != int(events):
        raise SystemExit(f"asked for {events} confirmed entries, found {len(ids)}")
    revision = bench.bump_filter_revision(cap.Fixture(catalog, ROOT), cause="p4-02v-refresh")
    coordinator = IndexCoordinator(catalog)
    started = time.perf_counter()
    with catalog.write() as connection:
        created = coordinator.schedule_projection_refresh(
            connection, asset_ids=ids, projection_revision=revision, now_ms=now_ms()
        )
    elapsed = elapsed_ms(started)
    log(f"  refresh queued {created} events for revision {revision}")
    return {
        "events": len(ids),
        "created": int(created),
        "filter_revision": int(revision),
        "schedule_ms": elapsed,
        "ms_per_event": round(elapsed / max(1, len(ids)), 3),
        "after_schedule": counts(catalog),
    }


def search_capture(
    catalog: Catalog, encoder: Any, text: str, *, text_only: bool = False
) -> dict[str, Any]:
    """One foreground search that also captures the page it served (P4-02V).

    ``text_only`` drops the vector binding, so the sample is the FTS channel plus the very
    same projection catch-up: the cheaper request isolates the write-lock wait from the
    ~10-70 s exact-vector scan, which is what lets one drain window hold >= 30 samples.
    """
    started = time.perf_counter()
    try:
        payload = search_assets(
            catalog,
            text=text,
            top_k=20,
            encoder=None if text_only else encoder,
            space_id=None if text_only else bench.SPACE_ID,
            generation_id=None if text_only else bench.GENERATION_ID,
            dimension=None if text_only else bench.DIM,
        )
    except Exception as error:  # noqa: BLE001 - a refusal is the measurement
        return {
            "ms": elapsed_ms(started),
            "refused": True,
            "error": type(error).__name__,
            "detail": str(error)[:200],
        }
    projection = payload.get("search_projection") or {}
    hits = payload.get("hits") or []
    return {
        "ms": elapsed_ms(started),
        "refused": False,
        "state": payload.get("state"),
        "hits": [int(hit["asset_id"]) for hit in hits],
        "hit_channels": [sorted(hit.get("matched_by") or []) for hit in hits],
        "projection_state": projection.get("state"),
        "queued_assets": projection.get("queued_assets"),
        "pending_semantic": len((payload.get("pending_semantic") or {}).get("entries") or []),
        "processed_assets": projection.get("processed_assets"),
        "catalog_filter_revision": payload.get("catalog_filter_revision"),
    }


def arm_concurrent(args: argparse.Namespace, log: Any) -> dict[str, Any]:
    """K independent foreground clients sample the page while the product drain builds.

    ``--legacy`` installs the pre-P4-02V catch-up in this process, so the *before* (refusal)
    and *after* (degrade) shapes run through the same driver, fixture and drain entry.
    """
    if args.legacy:
        install_legacy_catch_up()
    opener = Catalog.open(data_root=ROOT)
    db_path = opener.db_path
    opener.close()
    trace = Trace()
    trace.origin = time.perf_counter()
    trace.install()
    timeline = LockTimeline(db_path)
    timeline.origin = trace.origin
    timeline.start()
    state: dict[str, Any] = {"applied": 0, "payload": None, "error": None}

    def drain() -> None:
        background = Catalog.open(data_root=ROOT)
        try:
            payload = maintenance.drain_semantic_outbox(
                background, limit=args.events + 16, batch_size=DRAIN_BATCH
            )
            state["payload"] = payload
            state["applied"] = int(payload.get("applied") or 0)
        except BaseException as error:  # noqa: BLE001
            state["error"] = f"{type(error).__name__}: {error}"
        finally:
            background.close()

    results: list[dict[str, Any]] = []
    results_lock = threading.Lock()

    def client(index: int) -> None:
        catalog = Catalog.open(data_root=ROOT)
        try:
            encoder = bench.DeterministicEncoder()
            for sample in range(int(args.samples)):
                at = elapsed_ms(trace.origin)
                captured = search_capture(
                    catalog, encoder, cap.DEFAULT_QUERY_TEXT, text_only=bool(args.text_only)
                )
                entry = {"client": index + 1, "sample": sample + 1, "at_ms": at, **captured}
                with results_lock:
                    results.append(entry)
                log(
                    f"  client[{index + 1}] sample[{sample + 1}] {captured['ms']:.0f} ms "
                    f"{'REFUSED' if captured['refused'] else captured.get('projection_state')}"
                )
        finally:
            catalog.close()

    worker = threading.Thread(target=drain, name="p4-02v-drain", daemon=True)
    clients = [
        threading.Thread(target=client, args=(index,), name=f"p4-02v-client-{index}", daemon=True)
        for index in range(int(args.clients))
    ]
    started = time.perf_counter()
    try:
        worker.start()
        for thread in clients:
            thread.start()
        for thread in clients:
            thread.join(timeout=args.drain_timeout)
        worker.join(timeout=args.drain_timeout)
        elapsed = elapsed_ms(started)
    finally:
        timeline.stop()
        timeline.join(timeout=5)
    phases = trace.phases
    for entry in results:
        start = entry["at_ms"]
        end = start + entry["ms"]
        entry["overlapped_drain"] = any(
            window.get("end_ms") is not None
            and window.get("acquire_ms") is not None
            and start < window["end_ms"]
            and window["acquire_ms"] < end
            for window in phases
        )
    report: dict[str, Any] = {
        "arm": "concurrent",
        "legacy": bool(args.legacy),
        "clients": int(args.clients),
        "samples_per_client": int(args.samples),
        "overlap_ms": elapsed,
        "samples": sorted(results, key=lambda item: item["at_ms"]),
        "drain": {
            "error": state["error"],
            "applied": state["applied"],
            "batches": (state["payload"] or {}).get("batches"),
            "duration_ms": (state["payload"] or {}).get("duration_ms"),
            "write_hold_ms": (state["payload"] or {}).get("write_hold_ms"),
            "write_rejections": (state["payload"] or {}).get("write_rejections"),
        },
        "phase_windows": phases,
        "timeline": timeline.samples,
    }
    totals = {
        "samples": len(results),
        "overlapped": sum(1 for item in results if item["overlapped_drain"]),
        "refused": sum(1 for item in results if item["refused"]),
        "refused_overlapped": sum(
            1 for item in results if item["refused"] and item["overlapped_drain"]
        ),
        "ok_overlapped": sum(
            1 for item in results if not item["refused"] and item["overlapped_drain"]
        ),
    }
    report["totals"] = totals
    log(f"  totals: {json.dumps(totals)}")
    return report


def analyze(paths: list[str]) -> dict[str, Any]:
    """Recompute every reported statistic straight from stored JSONs (review F2/F4/F5/F8)."""
    summary: dict[str, Any] = {"inputs": [], "arms": []}
    for raw_path in paths:
        path = Path(raw_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        arm: dict[str, Any] = {"file": path.name, "mode": data.get("mode") or data.get("arm")}
        for label in ("before", "after"):
            block = data.get(label)
            if isinstance(block, dict):
                candidate = block.get("counts") if "counts" in block else block
                if isinstance(candidate, dict) and "assets" in candidate:
                    arm[f"fixture_{label}"] = candidate
        phase = [
            item
            for item in data.get("phase_windows", [])
            if item.get("acquire_ms") is not None and item.get("end_ms") is not None
        ]
        if phase:
            phase.sort(key=lambda item: item["acquire_ms"])
            holds = [item["end_ms"] - item["acquire_ms"] for item in phase]
            span = phase[-1]["end_ms"] - phase[0]["acquire_ms"]
            gaps = sorted(
                round(phase[i + 1]["acquire_ms"] - phase[i]["end_ms"], 3)
                for i in range(len(phase) - 1)
            )
            arm["duty_cycle"] = {
                "windows": len(phase),
                "sum_hold_ms": round(sum(holds), 3),
                "span_ms": round(span, 3),
                "duty_percent": round(100.0 * sum(holds) / span, 2) if span else None,
                "max_single_hold_ms": round(max(holds), 3),
                "gap_ms": {
                    "p50": gaps[len(gaps) // 2] if gaps else None,
                    "p95": gaps[int(0.95 * len(gaps)) - 1] if gaps else None,
                    "max": gaps[-1] if gaps else None,
                },
                "definition": (
                    "window = acquire_ms..end_ms of IndexCoordinator._timed_write; the product's "
                    "COMMIT happens after the yield, so gap_ms contains the previous COMMIT and is "
                    "NOT a freely contestable idle window"
                ),
            }
        engine = [
            item for item in data.get("engine_windows", []) if item.get("exit_ms") is not None
        ]
        timeline = data.get("timeline") or []
        blocked = [item for item in timeline if item.get("state") == "blocked"]
        if timeline:
            runs: list[dict[str, Any]] = []
            start = None
            previous = None
            for item in timeline:
                if item.get("state") == "blocked":
                    start = item["t_ms"] if start is None else start
                    previous = item
                elif start is not None:
                    runs.append(
                        {"start_ms": start, "wall_span_ms": round(previous["t_ms"] - start, 3)}
                    )
                    start = None
            if start is not None:
                runs.append(
                    {"start_ms": start, "wall_span_ms": round(timeline[-1]["t_ms"] - start, 3)}
                )
            probe_ms = sorted(item.get("probe_ms") or 0.0 for item in blocked)
            arm["lock_probe"] = {
                "samples": len(timeline),
                "blocked": len(blocked),
                "blocked_percent": round(100.0 * len(blocked) / len(timeline), 2),
                "blocked_probe_ms_p50": probe_ms[len(probe_ms) // 2] if probe_ms else None,
                "blocked_probe_ms_p95": (
                    probe_ms[int(0.95 * len(probe_ms)) - 1] if probe_ms else None
                ),
                "runs": len(runs),
                "max_run_wall_span_ms": max((run["wall_span_ms"] for run in runs), default=0.0),
                "top_runs_ms": sorted((run["wall_span_ms"] for run in runs), reverse=True)[:8],
                "definition": (
                    "run = consecutive blocked probe samples; wall span = last.t_ms - first.t_ms. "
                    "blocked only registers unavailability >= ~85 ms (a 40 ms probe timeout is "
                    "stretched by SQLite's busy handler), so '0 blocked' is not 'no writer'"
                ),
            }
        searches = (data.get("search") or {}).get("samples") or []
        if searches:
            arm["searches"] = {
                "count": len(searches),
                "refused": sum(1 for item in searches if item.get("refused")),
                "durations_ms": [round(item.get("ms") or 0.0, 1) for item in searches],
            }
        coverage = []
        for item in searches:
            if not item.get("refused") or item.get("at_ms") is None:
                continue
            start = item["at_ms"]
            end = start + (item.get("ms") or 0.0)
            span = end - start
            for label, windows, keys in (
                ("drain_phase", phase, ("acquire_ms", "end_ms")),
                ("engine", engine, ("enter_ms", "exit_ms")),
            ):
                covered = 0.0
                for window in windows:
                    low = max(start, window[keys[0]])
                    high = min(end, window[keys[1]])
                    if high > low:
                        covered += high - low
                coverage.append(
                    {
                        "search_at_ms": round(start, 1),
                        "window": label,
                        "covered_ms": round(covered, 1),
                        "covered_percent": round(100.0 * covered / span, 1) if span else None,
                    }
                )
        if coverage:
            arm["refusal_coverage"] = coverage
        checkpoints = data.get("checkpoint_samples") or []
        if checkpoints:
            values = sorted(item.get("ms") or 0.0 for item in checkpoints)
            arm["checkpoint"] = {
                "samples": len(checkpoints),
                "max_ms": values[-1],
                "p95_ms": values[int(0.95 * len(values)) - 1],
                "busy_samples": sum(1 for item in checkpoints if item.get("busy")),
                "max_log_frames": max((item.get("log_frames") or 0) for item in checkpoints),
            }
            arm["checkpoint_alignment"] = [
                {
                    "search_at_ms": round(item["at_ms"], 1),
                    "samples": len(
                        inside := [
                            cp
                            for cp in checkpoints
                            if item["at_ms"] <= cp.get("t_ms", -1) <= item["at_ms"] + item["ms"]
                        ]
                    ),
                    "max_ms": max((cp.get("ms") or 0.0) for cp in inside) if inside else None,
                    "max_log_frames": max((cp.get("log_frames") or 0) for cp in inside)
                    if inside
                    else None,
                    "busy": sum(1 for cp in inside if cp.get("busy")),
                }
                for item in searches
                if item.get("refused") and item.get("at_ms") is not None
            ]
        summary["inputs"].append(path.name)
        summary["arms"].append(arm)
    return summary

def _dump_while_blocked(timeline: LockTimeline, handle: Any, threshold_ms: float) -> None:
    """Dump every thread stack the first time the lock has been unavailable that long."""
    reported = False
    streak_start: float | None = None
    while not timeline._stop_event.is_set():
        samples = timeline.samples
        if samples and samples[-1]["state"] == "blocked":
            streak_start = streak_start or time.perf_counter()
            if not reported and elapsed_ms(streak_start) >= threshold_ms:
                reported = True
                handle.write(
                    f"\n--- lock unavailable >= {threshold_ms:.0f} ms "
                    f"at wall {time.time():.3f} ---\n"
                )
                faulthandler.dump_traceback(file=handle, all_threads=True)
                handle.flush()
        else:
            streak_start = None
            reported = False
        time.sleep(0.05)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=["inject", "seed-reembed", "seed-refresh", "seed-bulk", "seed-queue", "arm-control", "arm-no-drain", "arm-drain-process", "arm-drain-thread", "arm-interleaved", "arm-concurrent", "analyze"],
    )
    parser.add_argument("--events", type=int, default=6000)
    parser.add_argument("--samples", type=int, default=4)
    parser.add_argument("--holder-seconds", type=float, default=6.0)
    parser.add_argument("--drain-timeout", type=float, default=600.0)
    parser.add_argument("--pairs", type=int, default=3)
    parser.add_argument("--pause-settle", type=float, default=1.0)
    parser.add_argument("--clients", type=int, default=2)
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--text-only", action="store_true")
    parser.add_argument("--inputs", nargs="*", default=[])
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    def log(message: str) -> None:
        print(f"[p4-02u] {message}", flush=True)

    WORK.mkdir(parents=True, exist_ok=True)
    if not ROOT.exists():
        raise SystemExit(f"missing measurement root: {ROOT}")
    report: dict[str, Any] | None = None
    catalog = Catalog.open(data_root=ROOT)
    try:
        report = {
            "task": "P4-02U lock attribution (product code unchanged)",
            "mode": args.mode,
            "root": str(ROOT),
            "machine": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "logical_cpus": os.cpu_count(),
            },
            "before": counts(catalog),
        }
        log(f"before: {json.dumps(report['before'], ensure_ascii=False)}")
        if args.mode == "inject":
            report["injection"] = inject(catalog, events=args.events, log=log)
            report["after"] = counts(catalog)
        elif args.mode == "seed-reembed":
            report["injection"] = seed_reembed(catalog, events=args.events, log=log)
        elif args.mode == "seed-refresh":
            report["injection"] = seed_refresh(catalog, events=args.events, log=log)
        elif args.mode == "seed-bulk":
            report["injection"] = seed_bulk(catalog, events=args.events, log=log)
        elif args.mode == "seed-queue":
            report["injection"] = seed_queue(catalog, events=args.events, log=log)
        elif args.mode == "arm-control":
            catalog.close()
            report.update(arm_control(args, log))
        else:
            catalog.close()
            if args.mode == "arm-no-drain":
                report.update(arm_no_drain(args, log))
            elif args.mode == "arm-drain-process":
                report.update(arm_drain_process(args, log))
            elif args.mode == "arm-drain-thread":
                report.update(arm_drain_thread(args, log))
            elif args.mode == "arm-interleaved":
                report.update(arm_interleaved(args, log))
            elif args.mode == "arm-concurrent":
                report.update(arm_concurrent(args, log))
            else:
                report["analysis"] = analyze(args.inputs)
        after_catalog = Catalog.open(data_root=ROOT)
        try:
            report["after"] = {"counts": counts(after_catalog)}
        finally:
            after_catalog.close()
    finally:
        try:
            catalog.close()
        except Exception:  # noqa: BLE001 - already closed in the arm branch
            pass
    if args.output:
        output = Path(args.output)
    else:
        stamp = time.strftime("%H%M%S")
        output = (
            OUT_DIR
            / f"p4-02u-{args.mode}-e{args.events}-s{args.samples}-{stamp}.json"
        )
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    log(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
