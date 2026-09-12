"""P1-02 D-level: streaming enumeration, stability waiting, recovery and missing rules.

Every fixture lives under pytest's temporary directory; no real photo library is ever
read. The wall clock is injected so the stability window is exercised deterministically
instead of by sleeping.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from photo_search.catalog import store
from photo_search.catalog.errors import (
    ACCESS_DENIED,
    INPUT_UNSTABLE,
    PATH_KEY_COLLISION,
    SOURCE_OFFLINE,
    CatalogError,
)
from photo_search.catalog.paths import path_key
from photo_search.ingestion import scanner
from photo_search.ingestion.scanner import FileObservation, ScanInterrupted
from photo_search.ingestion.service import CatalogService

STABILITY_WINDOW_MS = 1_500
WALL_BASE_MS = time.time_ns() // 1_000_000
QUIET_AGE_MS = 3_600_000


class Clock:
    def __init__(self, start: int = 1_000_000) -> None:
        self.ms = int(start)

    def __call__(self) -> int:
        return self.ms

    def advance(self, delta_ms: int) -> None:
        self.ms += int(delta_ms)


@dataclass
class Harness:
    service: CatalogService
    clock: Clock
    wall: Clock
    data_root: Path


@pytest.fixture()
def harness(tmp_path: Path):
    clock = Clock()
    wall = Clock(WALL_BASE_MS)
    data_root = tmp_path / "PhotoSearchData"
    service = CatalogService.open(
        data_root=data_root,
        clock=clock,
        lease_seconds=60,
        wall_clock_ms=wall,
        stability_sleep=wall.advance,
    )
    try:
        yield Harness(service=service, clock=clock, wall=wall, data_root=data_root)
    finally:
        service.close()


def _write(path: Path, payload: bytes, *, age_ms: int = QUIET_AGE_MS) -> Path:
    """Write a fixture file whose ``mtime`` is quiet (or deliberately fresh) for the probe."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    stamp = (WALL_BASE_MS - age_ms) * 1_000_000
    os.utime(path, ns=(stamp, stamp))
    return path


def _locations(harness: Harness, source_id: int = 1) -> dict[str, dict]:
    rows = harness.service.catalog.read_all(
        "SELECT location_id, relative_path, path_key, asset_id, size, mtime_ns, "
        "last_seen_scan_id, availability FROM file_locations WHERE source_id = ? "
        "ORDER BY path_key",
        (source_id,),
    )
    return {str(row["relative_path"]): dict(row) for row in rows}


def _scan_row(harness: Harness, scan_id: int) -> dict:
    row = harness.service.catalog.read_one(
        "SELECT scan_id, source_id, started_at, finished_at, state, error FROM scan_runs "
        "WHERE scan_id = ?",
        (scan_id,),
    )
    return dict(row)


def _job(harness: Harness, job_id: int = 1) -> dict:
    row = harness.service.catalog.read_one(
        "SELECT job_id, state, attempts, progress, lease_token, last_error_code FROM jobs "
        "WHERE job_id = ?",
        (job_id,),
    )
    return dict(row)


def _asset(harness: Harness, asset_id: int) -> dict:
    return dict(
        harness.service.catalog.read_one(
            "SELECT sha256, byte_size, state FROM assets WHERE asset_id = ?", (asset_id,)
        )
    )


def test_a_fresh_file_is_waited_for_and_then_committed(harness: Harness, tmp_path: Path) -> None:
    """P1-02 §2: the quiet period is really waited out, then the content is bound."""
    root = tmp_path / "library"
    _write(root / "busy.jpg", b"JUST-COPIED", age_ms=0)
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]

    outcome = harness.service.run_scan(source_id)

    assert outcome["state"] == "SUCCEEDED"
    assert outcome["enumerated"] == 1
    assert outcome["files_seen"] == 1
    assert outcome["stability_waits"] == 1
    assert outcome["stability_wait_ms"] == STABILITY_WINDOW_MS
    assert harness.wall() == WALL_BASE_MS + STABILITY_WINDOW_MS
    location = _locations(harness)["busy.jpg"]
    assert location["availability"] == "PRESENT"
    assert location["asset_id"] is not None
    assert _asset(harness, location["asset_id"])["sha256"] == hashlib.sha256(b"JUST-COPIED").hexdigest()
    assert _job(harness)["progress"] == 1.0


def test_a_continuously_written_file_is_requeued_without_any_content_binding(
    tmp_path: Path,
) -> None:
    """P1-02 §2: a file that never becomes quiet is re-queued, not committed half-written."""
    root = tmp_path / "library"
    busy = _write(root / "busy.jpg", b"PARTIAL", age_ms=0)
    clock = Clock()
    wall = Clock(WALL_BASE_MS)
    with CatalogService.open(
        data_root=tmp_path / "data",
        clock=clock,
        lease_seconds=60,
        wall_clock_ms=wall,
        stability_sleep=wall.advance,
        stability_window_ms=STABILITY_WINDOW_MS,
        stability_wait_limit_ms=0,
    ) as service:
        source_id = service.register_source(display_name="library", root_path=root)["source_id"]

        with pytest.raises(CatalogError) as error:
            service.run_scan(source_id)

        assert error.value.code == INPUT_UNSTABLE
        assert error.value.details["window_ms"] == STABILITY_WINDOW_MS
        counts = service.counts(source_id=source_id)
        assert counts["locations"] == 1
        assert counts["locations_pending_content"] == 1
        assert counts["assets_total"] == 0
        assert counts["locations_missing"] == 0
        assert service.get_source(source_id)["last_successful_scan"] is None
        scan = service.catalog.read_one("SELECT state, finished_at FROM scan_runs WHERE scan_id = 1")
        assert dict(scan) == {"state": "RUNNING", "finished_at": None}
        job = service.catalog.read_one(
            "SELECT state, attempts, last_error_code, progress FROM jobs WHERE job_id = 1"
        )
        assert job["state"] == "RETRY_WAIT"
        assert job["attempts"] == 1
        assert job["last_error_code"] == INPUT_UNSTABLE
        assert 0.0 < job["progress"] < 1.0

        # The copy finishes: the file gets a quiet mtime and the retry becomes a success.
        stamp = (WALL_BASE_MS - QUIET_AGE_MS) * 1_000_000
        os.utime(busy, ns=(stamp, stamp))
        clock.advance(service.retry_backoff_ms)
        recovered = service.run_scan(source_id)

        assert recovered["scan_id"] == 1
        assert recovered["state"] == "SUCCEEDED"
        assert recovered["reused_scan_request"] is True
        counts = service.counts(source_id=source_id)
        assert counts["locations"] == 1
        assert counts["locations_pending_content"] == 0
        assert counts["assets"] == 1
        assert counts["locations_missing"] == 0
        assert service.get_source(source_id)["last_successful_scan"] == 1


def test_an_edit_during_hashing_never_binds_the_old_content(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-02 §2: a hash taken before a change is discarded, and the old asset survives."""
    root = tmp_path / "library"
    target = _write(root / "a.jpg", b"ORIGINAL")
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    assert harness.service.run_scan(source_id)["state"] == "SUCCEEDED"
    first_asset = _locations(harness)["a.jpg"]["asset_id"]
    assert _asset(harness, first_asset)["sha256"] == hashlib.sha256(b"ORIGINAL").hexdigest()

    real_hash = scanner.sha256_file
    mutated = {"done": False}

    def hash_then_replace(path: Path) -> str:
        digest = real_hash(path)
        if path.name == "a.jpg" and not mutated["done"]:
            mutated["done"] = True
            target.write_bytes(b"REPLACED-CONTENT")
            stamp = (WALL_BASE_MS - QUIET_AGE_MS) * 1_000_000
            os.utime(target, ns=(stamp, stamp))
        return digest

    monkeypatch.setattr(scanner, "sha256_file", hash_then_replace)
    outcome = harness.service.run_scan(source_id)

    assert outcome["state"] == "SUCCEEDED"
    assert outcome["unstable_retries"] == 1
    assert outcome["locations_pending_content"] == 0
    location = _locations(harness)["a.jpg"]
    assert location["asset_id"] not in (None, first_asset)
    assert _asset(harness, location["asset_id"])["sha256"] == hashlib.sha256(
        b"REPLACED-CONTENT"
    ).hexdigest()
    # The previous content identity is neither overwritten nor deleted.
    assert _asset(harness, first_asset)["sha256"] == hashlib.sha256(b"ORIGINAL").hexdigest()
    assert _asset(harness, first_asset)["state"] == "ACTIVE"
    assert harness.service.counts(source_id=source_id)["assets_total"] == 2


def test_processing_cancellation_resumes_the_same_scan_without_duplicate_jobs(
    harness: Harness, tmp_path: Path
) -> None:
    """P1-02 §1: an injected cancel path stops without negative facts and can be resumed."""
    root = tmp_path / "library"
    for index in range(4):
        _write(root / f"photo-{index}.jpg", b"CONTENT-%d" % index)
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]

    def cancel_after_two_confirmed_files() -> bool:
        counts = harness.service.counts(source_id=source_id)
        return counts["assets_total"] >= 2

    with pytest.raises(ScanInterrupted):
        harness.service.run_scan(source_id, cancel_check=cancel_after_two_confirmed_files)

    assert _scan_row(harness, 1)["state"] == "RUNNING"
    assert _scan_row(harness, 1)["finished_at"] is None
    partial = harness.service.counts(source_id=source_id)
    assert partial["locations"] == 4
    assert partial["assets"] == 2
    assert partial["locations_pending_content"] == 2
    assert partial["locations_missing"] == 0
    assert harness.service.get_source(source_id)["last_successful_scan"] is None

    harness.clock.advance(61_000)
    resumed = harness.service.run_scan(source_id)

    assert resumed["scan_id"] == 1
    assert resumed["state"] == "SUCCEEDED"
    assert resumed["files_seen"] == 4
    assert resumed["locations_missing"] == 0
    assert harness.service.counts()["jobs"] == 1
    assert harness.service.counts(source_id=source_id)["locations_pending_content"] == 0
    assert _job(harness)["progress"] == 1.0


def test_keyboard_interrupt_keeps_the_lease_and_is_recoverable(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-02 §1: Ctrl-C keeps P1-01 semantics (no negative fact, no SUCCEEDED)."""
    root = tmp_path / "library"
    target = _write(root / "a.jpg", b"AAAA")
    _write(root / "b.jpg", b"BBBB")
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    real_hash = scanner.sha256_file
    stopped = {"done": False}

    def interrupt_once(path: Path) -> str:
        if path.name == "a.jpg" and not stopped["done"]:
            stopped["done"] = True
            raise KeyboardInterrupt
        return real_hash(path)

    monkeypatch.setattr(scanner, "sha256_file", interrupt_once)
    with pytest.raises(ScanInterrupted):
        harness.service.run_scan(source_id)

    assert _scan_row(harness, 1)["state"] == "RUNNING"
    assert _job(harness)["state"] == "RUNNING"
    assert _job(harness)["lease_token"] is not None
    counts = harness.service.counts(source_id=source_id)
    assert counts["locations_missing"] == 0
    assert counts["assets_total"] == 0
    assert target.exists()

    monkeypatch.setattr(scanner, "sha256_file", real_hash)
    harness.clock.advance(61_000)
    resumed = harness.service.run_scan(source_id)
    assert resumed["state"] == "SUCCEEDED"
    assert resumed["scan_id"] == 1
    assert harness.service.counts(source_id=source_id)["locations_present"] == 2


def test_enumeration_permission_failure_blocks_without_missing_or_success(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-02 §4: an unreadable directory is BLOCKED, never a batch of MISSING rows."""
    root = tmp_path / "library"
    _write(root / "a.jpg", b"AAAA")
    locked = root / "locked"
    _write(locked / "hidden.jpg", b"BBBB")
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    assert harness.service.run_scan(source_id)["state"] == "SUCCEEDED"

    real_scandir = scanner._scandir

    def deny_locked_directory(path: object) -> object:
        if Path(str(path)).name == "locked":
            raise PermissionError(13, "Access is denied")
        return real_scandir(path)

    monkeypatch.setattr(scanner, "_scandir", deny_locked_directory)
    with pytest.raises(CatalogError) as error:
        harness.service.run_scan(source_id)

    assert error.value.code == ACCESS_DENIED
    blocked = _scan_row(harness, 2)
    assert blocked["state"] == "BLOCKED"
    assert blocked["finished_at"] is None
    assert json.loads(blocked["error"])["code"] == ACCESS_DENIED
    source = harness.service.get_source(source_id)
    assert source["status"] == "ERROR"
    assert source["last_successful_scan"] == 1
    counts = harness.service.counts(source_id=source_id)
    assert counts["locations"] == 2
    assert counts["locations_present"] == 2
    assert counts["locations_missing"] == 0
    assert counts["assets"] == 2

    # The unreadable directory is repaired and one file disappears: the resumed scan is a
    # complete scan again and may then conclude that the missing path is gone.
    monkeypatch.setattr(scanner, "_scandir", real_scandir)
    (root / "a.jpg").unlink()
    resumed = harness.service.run_scan(source_id)

    assert resumed["scan_id"] == 2
    assert resumed["state"] == "SUCCEEDED"
    assert resumed["reused_scan_request"] is True
    assert resumed["locations_missing"] == 1
    final = _locations(harness)
    assert final["a.jpg"]["availability"] == "MISSING"
    assert final["locked\\hidden.jpg"]["availability"] == "PRESENT"
    assert harness.service.get_source(source_id)["last_successful_scan"] == 2


def test_complete_scan_commits_missing_last_success_and_succeeded_together(
    harness: Harness, tmp_path: Path
) -> None:
    """P1-02 §3: the missing conclusion only appears with SUCCEEDED + last_successful_scan."""
    root = tmp_path / "library"
    _write(root / "a.jpg", b"AAAA")
    _write(root / "b.jpg", b"BBBB")
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    assert harness.service.run_scan(source_id)["state"] == "SUCCEEDED"
    assert harness.service.counts(source_id=source_id)["locations_missing"] == 0

    (root / "b.jpg").unlink()
    outcome = harness.service.run_scan(source_id)

    assert outcome["locations_missing"] == 1
    scan = _scan_row(harness, 2)
    assert scan["state"] == "SUCCEEDED"
    assert scan["finished_at"] is not None
    source = harness.service.get_source(source_id)
    assert source["last_successful_scan"] == 2
    assert _locations(harness)["b.jpg"]["availability"] == "MISSING"
    assert _job(harness)["state"] == "SUCCEEDED"
    assert _job(harness)["progress"] == 1.0

    # A blocked scan is not allowed to advance any of those facts.
    moved = tmp_path / "library-moved"
    os.replace(root, moved)
    with pytest.raises(CatalogError) as error:
        harness.service.run_scan(source_id)
    assert error.value.code == SOURCE_OFFLINE
    assert harness.service.get_source(source_id)["last_successful_scan"] == 2
    assert _scan_row(harness, 3)["state"] == "BLOCKED"
    assert harness.service.counts(source_id=source_id)["locations_missing"] == 1


def test_a_move_into_a_subdirectory_keeps_the_asset_and_reports_both_truths(
    harness: Harness, tmp_path: Path
) -> None:
    """P1-02 §3: a move is never a content deletion, even without file-id acceleration."""
    root = tmp_path / "library"
    target = _write(root / "a.jpg", b"MOVED-CONTENT")
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    assert harness.service.run_scan(source_id)["state"] == "SUCCEEDED"
    asset = _locations(harness)["a.jpg"]["asset_id"]
    assert asset is not None

    moved = root / "2024" / "a.jpg"
    moved.parent.mkdir()
    os.replace(target, moved)
    outcome = harness.service.run_scan(source_id)

    assert outcome["state"] == "SUCCEEDED"
    assert outcome["locations_missing"] == 1
    final = _locations(harness)
    assert final["2024\\a.jpg"]["availability"] == "PRESENT"
    assert final["2024\\a.jpg"]["asset_id"] == asset
    assert final["a.jpg"]["availability"] == "MISSING"
    assert final["a.jpg"]["asset_id"] == asset
    counts = harness.service.counts(source_id=source_id)
    assert counts["assets_total"] == 1
    assert counts["assets"] == 1
    assert _asset(harness, asset)["byte_size"] == len(b"MOVED-CONTENT")


def test_streaming_enumeration_detects_a_path_key_collision_without_merging(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-02 §4 (ADR-0002 §4.2): a streamed collision fails the scan, it is never merged."""
    root = tmp_path / "library"
    first = _write(root / "A.JPG", b"UPPER")
    second = _write(root / "a.jpg", b"lower")
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    upper = FileObservation(
        relative_path="Photos\\A.JPG",
        absolute_path=first,
        size=first.stat().st_size,
        mtime_ns=first.stat().st_mtime_ns,
        path_key=path_key("Photos\\A.JPG"),
    )
    lower = FileObservation(
        relative_path="photos\\a.jpg",
        absolute_path=second,
        size=second.stat().st_size,
        mtime_ns=second.stat().st_mtime_ns,
        path_key=path_key("photos\\a.jpg"),
    )
    assert upper.path_key == lower.path_key

    def colliding_enumeration(path: Path):
        yield upper
        yield lower

    monkeypatch.setattr(scanner, "iter_observations", colliding_enumeration)
    with pytest.raises(CatalogError) as error:
        harness.service.run_scan(source_id)

    assert error.value.code == PATH_KEY_COLLISION
    assert error.value.details["first_path"] == "Photos\\A.JPG"
    assert error.value.details["second_path"] == "photos\\a.jpg"
    scan = _scan_row(harness, 1)
    assert scan["state"] == "FAILED"
    assert json.loads(scan["error"])["code"] == PATH_KEY_COLLISION
    assert _job(harness)["state"] == "FAILED"
    assert harness.service.get_source(source_id)["last_successful_scan"] is None
    first_row = _locations(harness)["Photos\\A.JPG"]
    # The already-observed first spelling is a positive fact and must stay untouched: the
    # second path must never silently overwrite it.
    assert first_row["relative_path"] == "Photos\\A.JPG"
    assert first_row["asset_id"] is None
    assert harness.service.counts(source_id=source_id)["locations"] == 1
    assert harness.service.counts(source_id=source_id)["locations_missing"] == 0


def test_the_production_scan_streams_enumeration_and_position_reads(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-02 §1: no full-library list is built while enumerating or while confirming."""
    root = tmp_path / "library"
    for index in range(5):
        _write(root / f"photo-{index}.jpg", b"CONTENT-%d" % index)
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]

    def forbidden(*_args: object, **_kwargs: object) -> list:
        raise AssertionError("the production scan must stream the enumeration")

    monkeypatch.setattr(scanner, "collect_observations", forbidden)
    assert harness.service.run_scan(source_id)["state"] == "SUCCEEDED"

    class CountingConnection:
        def __init__(self, connection: object) -> None:
            self.connection = connection
            self.calls = 0

        def execute(self, sql: str, parameters: tuple = ()):
            self.calls += 1
            return self.connection.execute(sql, parameters)

    proxy = CountingConnection(harness.service.catalog.connection)
    rows = list(
        store.iter_seen_locations(
            proxy, source_id=source_id, scan_id=1, chunk_size=2  # type: ignore[arg-type]
        )
    )
    assert len(rows) == 5
    # Three data queries of at most two rows plus one terminating empty query: the
    # position list is never read as a single result set.
    assert proxy.calls == 4
