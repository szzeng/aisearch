"""P1-01 D-level: real temp fixtures, interruption, late submit and CLI contract."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from photo_search.catalog.errors import (
    DATA_ROOT_INSIDE_SOURCE,
    DISK_SPACE_INSUFFICIENT,
    PATH_KEY_COLLISION,
    SOURCE_OFFLINE,
    SOURCE_OVERLAP,
    STALE_LEASE,
    CatalogError,
)
from photo_search.catalog.paths import path_key
from photo_search.ingestion import scanner
from photo_search.ingestion.scanner import (
    FileObservation,
    ScanInterrupted,
    check_path_key_collisions,
)
from photo_search.ingestion.service import CatalogService
from photo_search.jobs import queue as jobs

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Clock:
    def __init__(self, start: int = 1_000_000) -> None:
        self.ms = start

    def __call__(self) -> int:
        return self.ms

    def advance(self, delta_ms: int) -> None:
        self.ms += delta_ms


class Usage:
    def __init__(self, free: int) -> None:
        self.total = 10**12
        self.used = 0
        self.free = free


@dataclass
class Harness:
    service: CatalogService
    clock: Clock
    data_root: Path


@pytest.fixture()
def harness(tmp_path: Path):
    clock = Clock()
    data_root = tmp_path / "PhotoSearchData"
    service = CatalogService.open(data_root=data_root, clock=clock, lease_seconds=60)
    try:
        yield Harness(service=service, clock=clock, data_root=data_root)
    finally:
        service.close()


def _write(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _locations(harness: Harness, source_id: int = 1) -> list[dict]:
    rows = harness.service.catalog.read_all(
        "SELECT location_id, relative_path, path_key, asset_id, size, mtime_ns, "
        "last_seen_scan_id, availability FROM file_locations WHERE source_id = ? "
        "ORDER BY path_key",
        (source_id,),
    )
    return [dict(row) for row in rows]


def _asset_id(harness: Harness, relative_path: str) -> int | None:
    return next(
        row["asset_id"] for row in _locations(harness) if row["relative_path"] == relative_path
    )


def _scan_row(harness: Harness, scan_id: int) -> dict:
    row = harness.service.catalog.read_one(
        "SELECT scan_id, source_id, started_at, finished_at, state, error FROM scan_runs "
        "WHERE scan_id = ?",
        (scan_id,),
    )
    return dict(row)


def _jobs(harness: Harness) -> list[dict]:
    rows = harness.service.catalog.read_all(
        "SELECT job_id, subject_id, state, attempts, progress, lease_token, last_error_code "
        "FROM jobs ORDER BY job_id"
    )
    return [dict(row) for row in rows]


def _make_junction(link: Path, target: Path) -> bool:
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0 and link.exists()


def test_scan_records_identity_for_hard_fixtures(harness: Harness, tmp_path: Path) -> None:
    source_root = tmp_path / "照片 库"
    long_dir = source_root / "照片 子目录" / ("long-segment-0123456789" * 6)
    long_file = long_dir / "c 图片.jpg"
    # The fixture must exercise real long-path semantics, not a short ASCII path.
    assert len(str(long_file)) > 220
    _write(source_root / "a.jpg", b"AAAA")
    _write(source_root / "b.jpg", b"AAAA")
    _write(long_file, b"CCCC")
    _write(source_root / "empty.bin", b"")
    _write(source_root / "notes.txt", b"not an image")
    read_only = _write(source_root / "readonly.jpg", b"RO")
    os.chmod(read_only, stat.S_IREAD)
    try:
        registered = harness.service.register_source(display_name="照片库", root_path=source_root)
        assert Path(registered["root_path"]) == source_root
        assert registered["status"] == "ONLINE"
        assert registered["last_successful_scan"] is None

        outcome = harness.service.run_scan(registered["source_id"])

        assert outcome["state"] == "SUCCEEDED"
        assert outcome["files_seen"] == 6
        assert outcome["locations_missing"] == 0
        assert outcome["locations_pending_content"] == 0
        assert outcome["reused_scan_request"] is False
        counts = harness.service.counts(source_id=registered["source_id"])
        assert counts["locations"] == 6
        assert counts["locations_present"] == 6
        assert counts["assets"] == 5
        assert counts["scans"] == 1
        assert counts["jobs"] == 1

        shared = _asset_id(harness, "a.jpg")
        assert shared is not None
        assert _asset_id(harness, "b.jpg") == shared
        assert any(row["relative_path"].endswith("c 图片.jpg") for row in _locations(harness))
        assert _asset_id(harness, "empty.bin") is not None
        assert _asset_id(harness, "notes.txt") is not None
        assert _asset_id(harness, "readonly.jpg") is not None

        os.replace(source_root / "a.jpg", source_root / "renamed.jpg")
        second = harness.service.run_scan(registered["source_id"])
        assert second["state"] == "SUCCEEDED"
        assert second["scan_id"] == 2
        assert _asset_id(harness, "renamed.jpg") == shared
        assert _asset_id(harness, "a.jpg") == shared
        assert _scan_row(harness, 2)["state"] == "SUCCEEDED"
        assert harness.service.get_source(registered["source_id"])["last_successful_scan"] == 2
        renamed_counts = harness.service.counts(source_id=registered["source_id"])
        assert renamed_counts["locations"] == 7
        assert renamed_counts["locations_missing"] == 1
        assert renamed_counts["assets"] == 5

        (source_root / "b.jpg").unlink()
        third = harness.service.run_scan(registered["source_id"])
        assert third["locations_missing"] == 1
        final_counts = harness.service.counts(source_id=registered["source_id"])
        assert final_counts["locations"] == 7
        assert final_counts["locations_present"] == 5
        assert final_counts["locations_missing"] == 2
        assert final_counts["assets"] == 5
        assert harness.service.get_source(registered["source_id"])["last_successful_scan"] == 3
        assert [job["state"] for job in _jobs(harness)] == ["SUCCEEDED"] * 3
    finally:
        os.chmod(read_only, stat.S_IWRITE | stat.S_IREAD)


def test_source_registration_rejects_overlap_and_the_data_root(
    harness: Harness, tmp_path: Path
) -> None:
    with pytest.raises(CatalogError) as data_root_error:
        harness.service.register_source(display_name="data", root_path=harness.data_root)
    assert data_root_error.value.code == DATA_ROOT_INSIDE_SOURCE

    with pytest.raises(CatalogError) as container_error:
        harness.service.register_source(display_name="container", root_path=tmp_path)
    assert container_error.value.code == DATA_ROOT_INSIDE_SOURCE

    root = tmp_path / "library"
    (root / "2020").mkdir(parents=True)
    first = harness.service.register_source(display_name="library", root_path=root)
    assert first["status"] == "ONLINE"

    with pytest.raises(CatalogError) as child_error:
        harness.service.register_source(display_name="child", root_path=root / "2020")
    assert child_error.value.code == SOURCE_OVERLAP
    assert child_error.value.details["existing_source_id"] == first["source_id"]

    with pytest.raises(CatalogError) as same_error:
        harness.service.register_source(display_name="same", root_path=root)
    assert same_error.value.code == SOURCE_OVERLAP

    inner = tmp_path / "outer" / "inner"
    inner.mkdir(parents=True)
    harness.service.register_source(display_name="inner", root_path=inner)
    with pytest.raises(CatalogError) as parent_error:
        harness.service.register_source(display_name="outer", root_path=tmp_path / "outer")
    assert parent_error.value.code == SOURCE_OVERLAP
    assert harness.service.counts()["sources"] == 2


def test_offline_source_blocks_the_scan_without_missing_marks(
    harness: Harness, tmp_path: Path
) -> None:
    root = tmp_path / "photos"
    _write(root / "a.jpg", b"AAAA")
    _write(root / "b.jpg", b"BBBB")
    source_id = harness.service.register_source(display_name="photos", root_path=root)["source_id"]
    assert harness.service.run_scan(source_id)["state"] == "SUCCEEDED"

    moved = tmp_path / "photos-moved"
    os.replace(root, moved)
    with pytest.raises(CatalogError) as error:
        harness.service.run_scan(source_id)
    assert error.value.code == SOURCE_OFFLINE

    blocked = _scan_row(harness, 2)
    assert blocked["state"] == "BLOCKED"
    assert blocked["finished_at"] is None
    assert json.loads(blocked["error"])["code"] == SOURCE_OFFLINE
    source = harness.service.get_source(source_id)
    assert source["status"] == "OFFLINE"
    assert source["last_successful_scan"] == 1
    blocked_counts = harness.service.counts(source_id=source_id)
    assert blocked_counts["locations_present"] == 2
    assert blocked_counts["locations_missing"] == 0
    jobs_after_block = _jobs(harness)
    assert [job["state"] for job in jobs_after_block] == ["SUCCEEDED", "BLOCKED"]
    assert jobs_after_block[1]["attempts"] == 0
    assert jobs_after_block[1]["lease_token"] is None

    requested = harness.service.start_scan(source_id)
    assert requested["reused"] is True
    assert requested["scan_id"] == 2
    assert requested["state"] == "BLOCKED"
    assert _scan_row(harness, 2)["state"] == "BLOCKED"

    os.replace(moved, root)
    resumed = harness.service.run_scan(source_id)
    assert resumed["scan_id"] == 2
    assert resumed["state"] == "SUCCEEDED"
    assert resumed["reused_scan_request"] is True
    assert harness.service.get_source(source_id)["last_successful_scan"] == 2
    assert harness.service.counts()["scans"] == 2


def test_interrupted_scan_resumes_after_restart_without_duplicate_jobs(
    harness: Harness, tmp_path: Path
) -> None:
    root = tmp_path / "library"
    for index in range(5):
        _write(root / f"photo-{index}.jpg", b"CONTENT-%d" % index)
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]

    with pytest.raises(ScanInterrupted):
        harness.service.run_scan(source_id, interrupt_after=2)

    interrupted = _scan_row(harness, 1)
    assert interrupted["state"] == "RUNNING"
    assert interrupted["finished_at"] is None
    jobs_after_interrupt = _jobs(harness)
    assert len(jobs_after_interrupt) == 1
    assert jobs_after_interrupt[0]["state"] == "RUNNING"
    assert jobs_after_interrupt[0]["attempts"] == 0
    assert jobs_after_interrupt[0]["lease_token"] is not None
    partial = harness.service.counts(source_id=source_id)
    assert partial["locations"] == 2
    assert partial["locations_pending_content"] == 0
    assert partial["assets"] == 2
    assert partial["scans"] == 1

    harness.service.close()
    harness.clock.advance(61_000)
    restarted = CatalogService.open(
        data_root=harness.data_root, clock=harness.clock, lease_seconds=60
    )
    try:
        outcome = restarted.run_scan(source_id)
        assert outcome["scan_id"] == 1
        assert outcome["state"] == "SUCCEEDED"
        assert outcome["reused_scan_request"] is True
        assert outcome["files_seen"] == 5
        assert outcome["locations_missing"] == 0
        assert outcome["locations_pending_content"] == 0
        final_counts = restarted.counts(source_id=source_id)
        assert final_counts["locations"] == 5
        assert final_counts["assets"] == 5
        assert final_counts["scans"] == 1
        assert final_counts["jobs"] == 1
        final_job = restarted.catalog.read_one("SELECT state, attempts, progress FROM jobs")
        assert final_job["state"] == "SUCCEEDED"
        assert final_job["attempts"] == 1
        assert final_job["progress"] == 1.0
    finally:
        restarted.close()


def test_expired_lease_result_is_refused_and_recovered_later(
    harness: Harness, tmp_path: Path
) -> None:
    root = tmp_path / "library"
    _write(root / "a.jpg", b"AAAA")
    _write(root / "b.jpg", b"BBBB")
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]

    with pytest.raises(CatalogError) as error:
        harness.service.run_scan(source_id, lease_seconds=0)
    assert error.value.code == STALE_LEASE
    late = _scan_row(harness, 1)
    assert late["state"] == "RUNNING"
    assert late["finished_at"] is None
    assert harness.service.get_source(source_id)["last_successful_scan"] is None
    late_counts = harness.service.counts(source_id=source_id)
    # C2 B1: an executor without a live lease must not leave any new persistent result.
    assert late_counts["locations"] == 0
    assert late_counts["assets_total"] == 0
    assert late_counts["locations_missing"] == 0
    assert _jobs(harness)[0]["state"] == "RUNNING"

    harness.clock.advance(1)
    recovered = harness.service.run_scan(source_id)
    assert recovered["state"] == "SUCCEEDED"
    assert recovered["scan_id"] == 1
    assert harness.service.counts(source_id=source_id)["locations_present"] == 2
    job = _jobs(harness)[0]
    assert job["state"] == "SUCCEEDED"
    assert job["attempts"] == 1
    assert harness.service.get_source(source_id)["last_successful_scan"] == 1


def test_positive_writes_require_a_live_submission(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C2 B1: no persistent scan result may be written without a qualified execution."""
    root = tmp_path / "library"
    _write(root / "a.jpg", b"AAAA")
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    calls = {"count": 0}
    real_validate = jobs.validate_submission

    def refuse_first(connection, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise CatalogError("PROCESSOR_MISMATCH", "simulated unqualified execution")
        return real_validate(connection, **kwargs)

    monkeypatch.setattr(scanner.jobs, "validate_submission", refuse_first)
    with pytest.raises(CatalogError) as error:
        harness.service.run_scan(source_id)
    assert error.value.code == "PROCESSOR_MISMATCH"
    counts = harness.service.counts(source_id=source_id)
    assert counts["locations"] == 0
    assert counts["assets_total"] == 0
    assert _scan_row(harness, 1)["state"] == "RUNNING"

    calls["count"] = 0
    monkeypatch.setattr(scanner.jobs, "validate_submission", real_validate)
    harness.clock.advance(61_000)
    harness.service.run_scan(source_id)
    assert harness.service.counts(source_id=source_id)["locations"] == 1


def test_content_binding_requires_a_live_submission(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C2 B1: the asset upsert + binding transaction is gated before it writes."""
    root = tmp_path / "library"
    _write(root / "a.jpg", b"AAAA")
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    calls = {"count": 0}
    real_validate = jobs.validate_submission

    def refuse_second(connection, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise CatalogError("STALE_INPUT_REVISION", "simulated revision change")
        return real_validate(connection, **kwargs)

    monkeypatch.setattr(scanner.jobs, "validate_submission", refuse_second)
    with pytest.raises(CatalogError) as error:
        harness.service.run_scan(source_id)
    assert error.value.code == "STALE_INPUT_REVISION"
    counts = harness.service.counts(source_id=source_id)
    assert counts["locations"] == 1
    assert counts["locations_pending_content"] == 1
    assert counts["assets_total"] == 0
    assert _scan_row(harness, 1)["state"] == "RUNNING"


def test_input_change_during_hashing_discards_the_stale_hash(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C2 B4: a hash taken before a change is discarded, never bound to the location."""
    root = tmp_path / "library"
    target = _write(root / "a.jpg", b"ORIGINAL")
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    real_hash = scanner.sha256_file
    mutated = {"done": False}

    def hashing_mutator(path: Path) -> str:
        digest = real_hash(path)
        if path.name == "a.jpg" and not mutated["done"]:
            mutated["done"] = True
            target.write_bytes(b"REPLACED-CONTENT")
        return digest

    monkeypatch.setattr(scanner, "sha256_file", hashing_mutator)
    outcome = harness.service.run_scan(source_id)

    assert outcome["state"] == "SUCCEEDED"
    assert outcome["unstable_retries"] == 1
    assert outcome["locations_pending_content"] == 0
    locations = _locations(harness)
    assert len(locations) == 1
    asset = harness.service.catalog.read_one(
        "SELECT sha256, byte_size FROM assets WHERE asset_id = ?", (locations[0]["asset_id"],)
    )
    assert asset["sha256"] == hashlib.sha256(b"REPLACED-CONTENT").hexdigest()
    assert asset["byte_size"] == len(b"REPLACED-CONTENT")
    assert harness.service.counts()["assets_total"] == 1


def test_long_scan_renews_its_lease_before_it_expires(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C2 B1: the per-write qualification must not be lost by a slow multi-file scan."""
    root = tmp_path / "library"
    for index in range(6):
        _write(root / f"photo-{index}.jpg", b"CONTENT-%d" % index)
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    real_hash = scanner.sha256_file

    def slow_hash(path: Path) -> str:
        harness.clock.advance(600)
        return real_hash(path)

    monkeypatch.setattr(scanner, "sha256_file", slow_hash)
    outcome = harness.service.run_scan(source_id, lease_seconds=1)

    assert outcome["state"] == "SUCCEEDED"
    assert outcome["files_seen"] == 6
    job = _jobs(harness)[0]
    assert job["state"] == "SUCCEEDED"
    assert job["attempts"] == 0
    assert harness.service.counts(source_id=source_id)["locations_present"] == 6


def test_persistent_instability_requeues_instead_of_succeeding(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C2 B4: unstable input must not be reported as a complete successful scan."""
    root = tmp_path / "library"
    target = _write(root / "a.jpg", b"ORIGINAL")
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    real_hash = scanner.sha256_file
    state = {"grow": True, "writes": 0}

    def always_changing(path: Path) -> str:
        digest = real_hash(path)
        if path.name == "a.jpg" and state["grow"]:
            state["writes"] += 1
            target.write_bytes(b"X" * (8 + state["writes"]))
        return digest

    monkeypatch.setattr(scanner, "sha256_file", always_changing)
    with pytest.raises(CatalogError) as error:
        harness.service.run_scan(source_id)

    assert error.value.code == "INPUT_UNSTABLE"
    unstable = _scan_row(harness, 1)
    assert unstable["state"] == "RUNNING"
    assert unstable["finished_at"] is None
    job = _jobs(harness)[0]
    assert job["state"] == "RETRY_WAIT"
    assert job["attempts"] == 1
    assert job["last_error_code"] == "INPUT_UNSTABLE"
    assert job["lease_token"] is None
    assert harness.service.get_source(source_id)["last_successful_scan"] is None
    counts = harness.service.counts(source_id=source_id)
    assert counts["locations_missing"] == 0
    assert counts["locations_pending_content"] == 1
    assert counts["scans"] == 1

    state["grow"] = False
    harness.clock.advance(harness.service.retry_backoff_ms)
    recovered = harness.service.run_scan(source_id)

    assert recovered["scan_id"] == 1
    assert recovered["state"] == "SUCCEEDED"
    assert recovered["reused_scan_request"] is True
    assert recovered["locations_pending_content"] == 0
    final_counts = harness.service.counts(source_id=source_id)
    assert final_counts["locations"] == 1
    assert final_counts["locations_pending_content"] == 0
    assert final_counts["locations_missing"] == 0
    assert final_counts["scans"] == 1
    assert harness.service.get_source(source_id)["last_successful_scan"] == 1


def test_same_path_content_change_rebinds_without_losing_the_old_asset(
    harness: Harness, tmp_path: Path
) -> None:
    root = tmp_path / "library"
    target = _write(root / "a.jpg", b"AAAA")
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    harness.service.run_scan(source_id)
    first_location = _locations(harness)[0]
    first_asset = first_location["asset_id"]
    assert first_asset is not None

    target.write_bytes(b"BBBBBB")
    harness.service.run_scan(source_id)
    replaced = _locations(harness)
    assert len(replaced) == 1
    assert replaced[0]["location_id"] == first_location["location_id"]
    assert replaced[0]["asset_id"] not in (None, first_asset)
    assert replaced[0]["size"] == 6
    old_asset = harness.service.catalog.read_one(
        "SELECT sha256, byte_size, state FROM assets WHERE asset_id = ?", (first_asset,)
    )
    assert old_asset["byte_size"] == 4
    assert old_asset["state"] == "ACTIVE"
    counts = harness.service.counts(source_id=source_id)
    # A source-scoped asset count follows this source's locations; the retired asset
    # stays in the catalog and is still visible in the global total.
    assert counts["assets"] == 1
    assert counts["assets_total"] == 2
    assert counts["locations"] == 1
    assert counts["locations_missing"] == 0

    previous_mtime = replaced[0]["mtime_ns"]
    target.write_bytes(b"CCCC")
    os.utime(target, ns=(previous_mtime, previous_mtime))
    harness.service.run_scan(source_id)
    same_size = _locations(harness)[0]
    assert same_size["size"] == 4
    assert same_size["asset_id"] not in (None, first_asset, replaced[0]["asset_id"])
    assert harness.service.counts(source_id=source_id)["assets"] == 1
    assert harness.service.counts(source_id=source_id)["assets_total"] == 3


def test_directory_junction_is_never_followed(harness: Harness, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _write(outside / "outside.jpg", b"OUTSIDE")
    root = tmp_path / "library"
    _write(root / "inside.jpg", b"INSIDE")
    if not _make_junction(root / "link", outside):
        pytest.skip("directory junctions are unavailable on this machine")
    _make_junction(root / "self", root)

    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    outcome = harness.service.run_scan(source_id)

    assert outcome["state"] == "SUCCEEDED"
    assert outcome["files_seen"] == 1
    locations = _locations(harness)
    assert [row["relative_path"] for row in locations] == ["inside.jpg"]
    assert (root / "link").exists()
    assert (outside / "outside.jpg").exists()


def test_two_sources_share_one_asset_and_survive_losing_one_location(
    harness: Harness, tmp_path: Path
) -> None:
    first_root = tmp_path / "photos-a"
    second_root = tmp_path / "photos-b"
    _write(first_root / "copy.jpg", b"SHARED-CONTENT")
    _write(second_root / "copy.jpg", b"SHARED-CONTENT")
    first = harness.service.register_source(display_name="a", root_path=first_root)["source_id"]
    second = harness.service.register_source(display_name="b", root_path=second_root)["source_id"]
    harness.service.run_scan(first)
    harness.service.run_scan(second)

    assert harness.service.counts()["assets_total"] == 1
    assert _asset_id(harness, "copy.jpg") is not None
    first_asset = _asset_id(harness, "copy.jpg")
    second_asset = next(
        row["asset_id"]
        for row in harness.service.catalog.read_all(
            "SELECT asset_id FROM file_locations WHERE source_id = ?", (second,)
        )
    )
    assert first_asset == second_asset

    (first_root / "copy.jpg").unlink()
    harness.service.run_scan(first)

    first_counts = harness.service.counts(source_id=first)
    assert first_counts["locations_missing"] == 1
    assert first_counts["locations_present"] == 0
    second_counts = harness.service.counts(source_id=second)
    assert second_counts["locations_present"] == 1
    assert second_counts["locations_missing"] == 0
    assert harness.service.counts()["assets_total"] == 1
    assert harness.service.get_source(second)["last_successful_scan"] == 2


def test_path_key_collision_fails_the_scan_without_writing_locations(
    harness: Harness, tmp_path: Path
) -> None:
    root = tmp_path / "library"
    target = _write(root / "a.jpg", b"AAAA")
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    info = target.stat()
    upper = FileObservation(
        relative_path="Photos/A.JPG",
        absolute_path=target,
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
        path_key=path_key("Photos/A.JPG"),
    )
    lower = FileObservation(
        relative_path="photos/a.jpg",
        absolute_path=target,
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
        path_key=path_key("photos/a.jpg"),
    )
    assert upper.path_key == lower.path_key
    with pytest.raises(CatalogError) as guard:
        check_path_key_collisions([upper, lower])
    assert guard.value.code == PATH_KEY_COLLISION

    with pytest.raises(CatalogError) as error:
        harness.service.run_scan(source_id, observations=[upper, lower])

    assert error.value.code == PATH_KEY_COLLISION
    assert error.value.details["first_path"] == "Photos/A.JPG"
    assert _scan_row(harness, 1)["state"] == "FAILED"
    assert json.loads(_scan_row(harness, 1)["error"])["code"] == PATH_KEY_COLLISION
    assert _jobs(harness)[0]["state"] == "FAILED"
    assert harness.service.counts(source_id=source_id)["locations"] == 0
    assert harness.service.get_source(source_id)["last_successful_scan"] is None


def test_disk_space_shortage_at_the_final_commit_keeps_every_identity(
    harness: Harness, tmp_path: Path
) -> None:
    root = tmp_path / "library"
    _write(root / "a.jpg", b"AAAA")
    _write(root / "b.jpg", b"BBBB")
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    assert harness.service.run_scan(source_id)["state"] == "SUCCEEDED"
    (root / "b.jpg").unlink()

    calls = {"count": 0}

    def failing_disk_usage(path: object) -> Usage:
        calls["count"] += 1
        return Usage(free=1 if calls["count"] >= 2 else 10**12)

    harness.service.disk_usage = failing_disk_usage
    with pytest.raises(CatalogError) as error:
        harness.service.run_scan(source_id)

    assert error.value.code == DISK_SPACE_INSUFFICIENT
    assert calls["count"] >= 2
    assert _scan_row(harness, 2)["state"] == "FAILED"
    assert harness.service.get_source(source_id)["last_successful_scan"] == 1
    counts = harness.service.counts(source_id=source_id)
    assert counts["locations"] == 2
    assert counts["locations_present"] == 2
    assert counts["locations_missing"] == 0
    assert counts["assets"] == 2


def _run_cli(data_root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "photo_search",
            "catalog",
            *arguments,
            "--data-root",
            str(data_root),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        cwd=str(PROJECT_ROOT),
        check=False,
    )


def test_cli_reports_json_results_and_structured_errors(tmp_path: Path) -> None:
    data_root = tmp_path / "PhotoSearchData"
    root = tmp_path / "照片 库"
    _write(root / "a.jpg", b"AAAA")

    initialized = _run_cli(data_root, "init", "--json")
    assert initialized.returncode == 0, initialized.stderr
    init_body = json.loads(initialized.stdout)
    assert init_body["status"] == "ok"
    assert init_body["schema_version"] == 1
    assert init_body["applied"] == [1]

    info = _run_cli(data_root, "info", "--json")
    assert info.returncode == 0
    info_body = json.loads(info.stdout)
    assert info_body["status"] == "ok"
    assert info_body["journal_mode"] == "wal"

    added = _run_cli(data_root, "add-source", "--name", "照片库", "--path", str(root), "--json")
    assert added.returncode == 0, added.stderr
    source = json.loads(added.stdout)["source"]
    assert Path(source["root_path"]) == root

    scanned = _run_cli(data_root, "scan", "--source-id", "1", "--json")
    assert scanned.returncode == 0, scanned.stderr
    assert json.loads(scanned.stdout)["scan"]["state"] == "SUCCEEDED"

    status = _run_cli(data_root, "status", "--source-id", "1", "--json")
    assert status.returncode == 0
    assert json.loads(status.stdout)["source"]["counts"]["assets"] == 1

    listed = _run_cli(data_root, "list-sources", "--json")
    assert listed.returncode == 0
    assert len(json.loads(listed.stdout)["sources"]) == 1

    missing_source = _run_cli(data_root, "scan", "--source-id", "42", "--json")
    assert missing_source.returncode == 1
    assert json.loads(missing_source.stdout)["error"]["code"] == "SOURCE_NOT_FOUND"

    overlap = _run_cli(data_root, "add-source", "--name", "again", "--path", str(root), "--json")
    assert overlap.returncode == 1
    assert json.loads(overlap.stdout)["error"]["code"] == "SOURCE_OVERLAP"

    plain_error = _run_cli(data_root, "status", "--source-id", "42")
    assert plain_error.returncode == 1
    assert "SOURCE_NOT_FOUND" in plain_error.stderr
