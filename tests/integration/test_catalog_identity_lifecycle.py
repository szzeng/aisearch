"""P1-06 D-level: adversarial acceptance of the frozen ADR-0002 identity rules.

The P1-01…P1-05 suites prove the individual rules. This module runs the *closed loop*
the P1-06 card asks for: duplicate content, a move, a replacement, a source that goes
unreachable, a root remap and a kill/restart are each driven against real ``%TEMP%``
files, and every scenario asserts concrete catalog facts (row identity, availability,
content hash, artifact path) instead of only an exit code.

Every fixture lives under pytest's ``tmp_path``; nothing here reads, moves or deletes a
real photo library. Injected failures use the seams the production scanner already
exposes (``sha256_file``, ``_scandir``) or a real directory move, and never relax the
frozen identity, uniqueness, lease or missing rules.

Scenario map (``docs/tasks/P1-06.md``):

* duplicates   -> ``test_duplicate_content_shares_one_asset_and_one_artifact``
* move         -> ``test_move_keeps_identity_and_reuses_the_same_artifact``
* replace      -> ``test_replacing_content_*`` (3 cases)
* offline      -> ``test_unreachable_source_never_becomes_a_deletion_conclusion``,
                  ``test_source_vanishing_during_confirmation_blocks_the_scan``,
                  ``test_position_vanishing_before_confirmation_is_blocked``
* remap        -> ``test_remapping_the_root_preserves_identity_and_positions``
* restart      -> ``test_restart_recovery_takes_over_the_same_scan_run``,
                  ``test_repeated_takeovers_increase_attempts_monotonically``
* boundaries   -> ``test_empty_and_single_file_sources``,
                  ``test_large_source_scan_has_no_duplicates``
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from PIL import Image

from photo_search.app import create_app
from photo_search.catalog.errors import (
    FILE_READ_ERROR,
    SOURCE_FILE_MISSING,
    SOURCE_OFFLINE,
    CatalogError,
)
from photo_search.ingestion import scanner
from photo_search.ingestion.scanner import ScanInterrupted
from photo_search.ingestion.service import CatalogService

#: Fixed wall-clock origin so a fixture written "one hour ago" is already quiet.
WALL_BASE_MS = time.time_ns() // 1_000_000
QUIET_AGE_MS = 3_600_000

THUMBNAIL_PROCESSOR_ID = "catalog-thumbnail@1"
PREVIEW_PROCESSOR_ID = "catalog-preview@1"


class Clock:
    """Deterministic monotone clock for leases and job timestamps."""

    def __init__(self, start: int = 1_000_000) -> None:
        self.ms = int(start)

    def __call__(self) -> int:
        return self.ms

    def advance(self, delta_ms: int) -> None:
        self.ms += int(delta_ms)


class WallClock(Clock):
    """The wall clock the scanner compares file ``mtime`` against."""


class Usage:
    def __init__(self, free: int = 10**12) -> None:
        self.total = 10**12
        self.used = 0
        self.free = int(free)

    def __call__(self, path: object = None) -> Usage:
        return self


@dataclass
class Harness:
    service: CatalogService
    clock: Clock
    wall: WallClock
    data_root: Path
    usage: Usage

    def reopen(self) -> CatalogService:
        """Model a killed process: a brand new service on the same data root."""
        self.service = CatalogService.open(
            data_root=self.data_root,
            clock=self.clock,
            lease_seconds=60,
            disk_usage=self.usage,
            wall_clock_ms=self.wall,
            stability_sleep=self.wall.advance,
        )
        return self.service


@pytest.fixture()
def harness(tmp_path: Path):
    clock = Clock()
    wall = WallClock(WALL_BASE_MS)
    usage = Usage()
    data_root = tmp_path / "PhotoSearchData"
    service = CatalogService.open(
        data_root=data_root,
        clock=clock,
        lease_seconds=60,
        disk_usage=usage,
        wall_clock_ms=wall,
        stability_sleep=wall.advance,
    )
    try:
        yield Harness(service=service, clock=clock, wall=wall, data_root=data_root, usage=usage)
    finally:
        service.close()


# --------------------------------------------------------------------------- fixtures
def _write(path: Path, payload: bytes, *, age_ms: int = QUIET_AGE_MS) -> Path:
    """Write a fixture file whose ``mtime`` is already outside the stability window."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    stamp = (WALL_BASE_MS - age_ms) * 1_000_000
    os.utime(path, ns=(stamp, stamp))
    return path


def _write_jpeg(
    path: Path, *, color: tuple[int, int, int], size: tuple[int, int] = (160, 120)
) -> Path:
    """Write a small *decodable* JPEG; the media scenarios need a real image input."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=92)
    stamp = (WALL_BASE_MS - QUIET_AGE_MS) * 1_000_000
    os.utime(path, ns=(stamp, stamp))
    return path


def _stamp(path: Path) -> tuple[int, int]:
    info = os.stat(path)
    return int(info.st_size), int(info.st_mtime_ns)


def _locations(harness: Harness, source_id: int) -> dict[str, dict]:
    rows = harness.service.catalog.read_all(
        "SELECT location_id, relative_path, path_key, asset_id, size, mtime_ns, "
        "last_seen_scan_id, availability FROM file_locations WHERE source_id = ? "
        "ORDER BY location_id",
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


def _job(harness: Harness, scan_id: int) -> dict:
    row = harness.service.catalog.read_one(
        "SELECT job_id, state, attempts, progress, lease_token FROM jobs "
        "WHERE subject_type = 'scan_run' AND subject_id = ?",
        (str(scan_id),),
    )
    return dict(row)


def _asset(harness: Harness, asset_id: int) -> dict:
    return dict(
        harness.service.catalog.read_one(
            "SELECT sha256, byte_size, state FROM assets WHERE asset_id = ?", (asset_id,)
        )
    )


def _artifact(harness: Harness, sha256: str, *, kind: str = "thumbnails") -> Path:
    processor = THUMBNAIL_PROCESSOR_ID if kind == "thumbnails" else PREVIEW_PROCESSOR_ID
    return (
        harness.data_root
        / kind
        / processor
        / sha256[:2]
        / sha256[2:4]
        / f"{sha256}.jpg"
    )


def _published(harness: Harness, *, kind: str = "thumbnails") -> list[Path]:
    root = harness.data_root / kind
    if not root.is_dir():
        return []
    return sorted(path for path in root.rglob("*.jpg") if path.is_file())


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _identities(rows: dict[str, dict]) -> dict[str, tuple]:
    """The identity-relevant projection of a location row (scan attribution excluded)."""
    return {
        path: (
            row["location_id"],
            row["path_key"],
            row["asset_id"],
            row["size"],
            row["mtime_ns"],
            row["availability"],
        )
        for path, row in rows.items()
    }


# ----------------------------------------------------------------- 1. duplicates
def test_duplicate_content_shares_one_asset_and_one_artifact(
    harness: Harness, tmp_path: Path
) -> None:
    """Same bytes in several places: one asset, many positions, one machine artifact."""
    root_a = tmp_path / "来源 A"
    root_b = tmp_path / "source-b"
    long_dir = root_a / "长 目录" / ("segment-0123456789" * 7)
    first = _write_jpeg(root_a / "top.jpg", color=(200, 40, 40))
    payload = first.read_bytes()
    sha = _digest(payload)
    second = _write(root_a / "子 目 录" / "副 本 一.jpg", payload)
    third = _write(long_dir / "deep copy.jpg", payload)
    fourth = _write(root_b / "shared.jpg", payload)
    assert len(str(third)) > 220, "the fixture must exercise real long-path semantics"
    os.chmod(second, stat.S_IREAD)  # a read-only original is still readable and hashable
    before = {path: _stamp(path) for path in (first, second, third, fourth)}

    try:
        source_a = harness.service.register_source(display_name="A", root_path=root_a)["source_id"]
        source_b = harness.service.register_source(display_name="B", root_path=root_b)["source_id"]
        assert harness.service.run_scan(source_a)["state"] == "SUCCEEDED"
        assert harness.service.run_scan(source_b)["state"] == "SUCCEEDED"

        counts = harness.service.counts()
        assert counts["assets_total"] == 1
        assert counts["locations"] == 4
        locations_a = _locations(harness, source_a)
        locations_b = _locations(harness, source_b)
        assert len(locations_a) == 3 and len(locations_b) == 1
        asset_id = next(iter(locations_a.values()))["asset_id"]
        assert asset_id is not None
        for row in (*locations_a.values(), *locations_b.values()):
            assert row["asset_id"] == asset_id
            assert row["availability"] == "PRESENT"
        asset = _asset(harness, asset_id)
        assert asset["sha256"] == sha and asset["byte_size"] == len(payload)

        # The user's originals are read-only inputs: their bytes and timestamps are untouched.
        assert {path: _stamp(path) for path in before} == before

        # One content identity -> exactly one content-addressed artifact, shared by all
        # four positions (two sources included).
        first_run = harness.service.generate_thumbnail(asset_ids=[asset_id])
        assert first_run["generated"] == 1
        artifact = _artifact(harness, sha)
        assert artifact == (
            harness.data_root / "thumbnails" / THUMBNAIL_PROCESSOR_ID
            / sha[:2] / sha[2:4] / f"{sha}.jpg"
        )
        assert _published(harness) == [artifact]
        assert harness.service.generate_thumbnail(asset_ids=[asset_id])["already_published"] == 1
        assert _published(harness) == [artifact]
        artifact_bytes = artifact.read_bytes()
        artifact_stat = _stamp(artifact)

        # Losing one copy is not losing the photo: the remaining positions keep the asset
        # PRESENT and the already published artifact stays readable, byte for byte.
        first.unlink()
        assert harness.service.run_scan(source_a)["state"] == "SUCCEEDED"
        after_delete = _locations(harness, source_a)
        assert after_delete["top.jpg"]["availability"] == "MISSING"
        assert after_delete["子 目 录\\副 本 一.jpg"]["availability"] == "PRESENT"
        counts_a = harness.service.counts(source_id=source_a)
        assert counts_a["locations"] == 3
        assert counts_a["locations_present"] == 2
        assert counts_a["locations_missing"] == 1
        assert counts_a["assets"] == 1
        assert harness.service.counts()["assets_total"] == 1
        assert artifact.read_bytes() == artifact_bytes
        assert _stamp(artifact) == artifact_stat
        assert harness.service.generate_thumbnail(asset_ids=[asset_id])["already_published"] == 1
        assert _published(harness) == [artifact]
        thumbnails = harness.service.media_report(kinds=("thumbnail",))["kinds"]["thumbnail"]
        assert thumbnails["published"] == 1 and thumbnails["source_missing"] == 0

        # Even when every position of the *other* source is gone the asset row and the
        # machine result survive; only availability changed.
        fourth.unlink()
        assert harness.service.run_scan(source_b)["state"] == "SUCCEEDED"
        locations_b = _locations(harness, source_b)
        assert locations_b["shared.jpg"]["availability"] == "MISSING"
        assert locations_b["shared.jpg"]["asset_id"] == asset_id
        counts_b = harness.service.counts(source_id=source_b)
        assert counts_b["locations"] == 1 and counts_b["locations_missing"] == 1
        assert counts_b["assets"] == 1  # the asset is still recorded for this source
        assert harness.service.counts()["assets_total"] == 1
        assert _asset(harness, asset_id)["state"] == "ACTIVE"
        assert artifact.read_bytes() == artifact_bytes
        assert harness.service.media_report(kinds=("thumbnail",))["kinds"]["thumbnail"][
            "published"
        ] == 1
    finally:
        os.chmod(second, stat.S_IWRITE | stat.S_IREAD)


# ----------------------------------------------------------------------- 2. move
def test_move_keeps_identity_and_reuses_the_same_artifact(
    harness: Harness, tmp_path: Path
) -> None:
    """A move is a new PRESENT position plus an old MISSING one, never a new asset."""
    root = tmp_path / "library"
    target = _write_jpeg(root / "a.jpg", color=(10, 120, 200))
    moved_payload = target.read_bytes()
    _write_jpeg(root / "b.jpg", color=(40, 40, 40))
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    assert harness.service.run_scan(source_id)["state"] == "SUCCEEDED"
    original = _locations(harness, source_id)["a.jpg"]
    asset_id = original["asset_id"]
    assert asset_id is not None
    assert harness.service.generate_thumbnail(asset_ids=[asset_id])["generated"] == 1
    artifact = _artifact(harness, _digest(moved_payload))
    assert artifact.is_file()
    artifact_stat = _stamp(artifact)
    artifacts_before = _published(harness)

    destination = root / "2024 年" / "照 片 一.jpg"
    destination.parent.mkdir()
    os.replace(target, destination)
    outcome = harness.service.run_scan(source_id)

    assert outcome["state"] == "SUCCEEDED"
    assert outcome["files_seen"] == 2
    assert outcome["locations_missing"] == 1
    final = _locations(harness, source_id)
    assert len(final) == 3
    assert final["2024 年\\照 片 一.jpg"]["availability"] == "PRESENT"
    assert final["2024 年\\照 片 一.jpg"]["asset_id"] == asset_id
    assert final["a.jpg"]["availability"] == "MISSING"
    # The old position keeps its last known content binding (ADR-0002 §3.4).
    assert final["a.jpg"]["asset_id"] == asset_id
    assert final["b.jpg"]["availability"] == "PRESENT"
    counts = harness.service.counts(source_id=source_id)
    assert counts["assets"] == 2 and counts["assets_total"] == 2
    assert _asset(harness, asset_id)["sha256"] == _digest(moved_payload)

    # Machine results are content-addressed: the move neither regenerates nor duplicates them.
    assert _published(harness) == artifacts_before
    assert _stamp(artifact) == artifact_stat
    assert harness.service.generate_thumbnail(asset_ids=[asset_id])["already_published"] == 1
    assert _published(harness) == artifacts_before


# -------------------------------------------------------------------- 3. replace
def test_replacing_content_rebinds_without_touching_the_old_asset(
    harness: Harness, tmp_path: Path
) -> None:
    """The old fingerprint must never end up pointing at the new bytes."""
    root = tmp_path / "library"
    target = _write(root / "a.jpg", b"AAAA")
    _write(root / "b.jpg", b"BBBBBB")
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    assert harness.service.run_scan(source_id)["state"] == "SUCCEEDED"
    old_asset = _locations(harness, source_id)["a.jpg"]["asset_id"]
    assert old_asset is not None

    replacement = b"CCCCCCCC"
    target.write_bytes(replacement)
    _write(root / "a.jpg", replacement)  # restore a quiet mtime for the fixture
    outcome = harness.service.run_scan(source_id)

    assert outcome["state"] == "SUCCEEDED"
    assert outcome["locations_missing"] == 0
    rebind = _locations(harness, source_id)["a.jpg"]
    assert outcome["files_seen"] == 2
    assert rebind["asset_id"] not in (None, old_asset)
    assert _asset(harness, rebind["asset_id"])["sha256"] == _digest(replacement)
    assert _asset(harness, rebind["asset_id"])["byte_size"] == len(replacement)
    # The previous content identity is retained untouched and still ACTIVE.
    previous = _asset(harness, old_asset)
    assert previous["state"] == "ACTIVE"
    assert previous["sha256"] == _digest(b"AAAA")
    assert previous["byte_size"] == 4
    assert harness.service.counts()["assets_total"] == 3
    # No location may pair the old hash with the new file.
    for row in _locations(harness, source_id).values():
        assert _asset(harness, row["asset_id"])["sha256"] == _digest(
            target.read_bytes() if row["relative_path"] == "a.jpg" else b"BBBBBB"
        )


def test_replacement_matching_another_position_reuses_that_asset(
    harness: Harness, tmp_path: Path
) -> None:
    """A replacement whose bytes already exist rebinds to that asset, not a third one."""
    root = tmp_path / "library"
    _write(root / "a.jpg", b"AAAA")
    shared = b"SHARED-REPLACEMENT"
    _write(root / "b.jpg", shared)
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    assert harness.service.run_scan(source_id)["state"] == "SUCCEEDED"
    before = _locations(harness, source_id)
    old_asset = before["a.jpg"]["asset_id"]
    shared_asset = before["b.jpg"]["asset_id"]
    assert old_asset != shared_asset

    _write(root / "a.jpg", shared)
    assert harness.service.run_scan(source_id)["state"] == "SUCCEEDED"

    after = _locations(harness, source_id)
    assert after["a.jpg"]["asset_id"] == shared_asset
    assert after["b.jpg"]["asset_id"] == shared_asset
    assert harness.service.counts()["assets_total"] == 2
    assert _asset(harness, old_asset)["state"] == "ACTIVE"
    assert _asset(harness, old_asset)["sha256"] == _digest(b"AAAA")


def test_same_size_replacement_with_restored_mtime_is_still_detected(
    harness: Harness, tmp_path: Path
) -> None:
    """size/mtime are only hints: every scan re-hashes, so identical stats are not trusted."""
    root = tmp_path / "library"
    target = _write(root / "a.jpg", b"AAAA")
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    assert harness.service.run_scan(source_id)["state"] == "SUCCEEDED"
    stored = _locations(harness, source_id)["a.jpg"]
    old_asset = stored["asset_id"]

    # Same byte length, different bytes, and the recorded mtime put back exactly.
    target.write_bytes(b"ZZZZ")
    os.utime(target, ns=(stored["mtime_ns"], stored["mtime_ns"]))
    assert target.stat().st_size == stored["size"]
    assert target.stat().st_mtime_ns == stored["mtime_ns"]

    outcome = harness.service.run_scan(source_id)

    assert outcome["state"] == "SUCCEEDED"
    rebind = _locations(harness, source_id)["a.jpg"]
    assert rebind["asset_id"] not in (None, old_asset)
    assert _asset(harness, rebind["asset_id"])["sha256"] == _digest(b"ZZZZ")
    assert _asset(harness, old_asset)["sha256"] == _digest(b"AAAA")
    assert _asset(harness, old_asset)["state"] == "ACTIVE"


def test_swapping_two_positions_rebinds_both_to_existing_content(
    harness: Harness, tmp_path: Path
) -> None:
    """When two names exchange content, each position follows its content identity."""
    root = tmp_path / "library"
    first = _write(root / "a.jpg", b"AAAA")
    second = _write(root / "b.jpg", b"BBBBBB")
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    assert harness.service.run_scan(source_id)["state"] == "SUCCEEDED"
    before = _locations(harness, source_id)
    asset_a = before["a.jpg"]["asset_id"]
    asset_b = before["b.jpg"]["asset_id"]
    assert asset_a != asset_b

    holding = root / "swap.tmp"
    os.replace(first, holding)
    os.replace(second, first)
    os.replace(holding, second)
    assert first.read_bytes() == b"BBBBBB" and second.read_bytes() == b"AAAA"

    outcome = harness.service.run_scan(source_id)

    assert outcome["state"] == "SUCCEEDED"
    assert outcome["files_seen"] == 2
    assert outcome["locations_missing"] == 0
    after = _locations(harness, source_id)
    assert after["a.jpg"]["asset_id"] == asset_b
    assert after["b.jpg"]["asset_id"] == asset_a
    assert after["a.jpg"]["availability"] == "PRESENT"
    assert after["b.jpg"]["availability"] == "PRESENT"
    # No extra identity was invented: the two known contents simply traded places.
    assert harness.service.counts()["assets_total"] == 2
    assert harness.service.counts(source_id=source_id)["assets"] == 2


def test_same_size_replacement_window_is_corrected_by_the_next_complete_scan(
    harness: Harness, tmp_path: Path
) -> None:
    """A content change that keeps size *and* mtime is caught by the next complete scan.

    ADR-0002 §5.1 calls ``size``/``mtime`` a fast hint and explicitly allows a file to
    change while keeping both, so a stopped scan may keep the previous observation. What
    must never happen is a silent success: the recovery scan re-hashes every position and
    rebinds. This pins that residual window as a documented, contract-consistent one.
    """
    root = tmp_path / "library"
    target = _write(root / "a.jpg", b"AAAA")
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    assert harness.service.run_scan(source_id)["state"] == "SUCCEEDED"
    stored = _locations(harness, source_id)["a.jpg"]
    old_asset = stored["asset_id"]
    assert _asset(harness, old_asset)["sha256"] == _digest(b"AAAA")

    target.write_bytes(b"ZZZZ")
    os.utime(target, ns=(stored["mtime_ns"], stored["mtime_ns"]))
    with pytest.raises(ScanInterrupted):
        harness.service.run_scan(source_id, interrupt_after=0)

    paused = _locations(harness, source_id)["a.jpg"]
    assert (paused["size"], paused["mtime_ns"]) == (stored["size"], stored["mtime_ns"])
    assert paused["asset_id"] == old_asset  # the change hint never fired
    assert paused["availability"] == "PRESENT"
    assert _scan_row(harness, 2)["state"] == "RUNNING"
    assert harness.service.get_source(source_id)["last_successful_scan"] == 1  # no silent success
    assert harness.service.counts(source_id=source_id)["locations_missing"] == 0

    harness.clock.advance(61_000)
    resumed = harness.service.run_scan(source_id)

    assert resumed["scan_id"] == 2
    assert resumed["state"] == "SUCCEEDED"
    settled = _locations(harness, source_id)["a.jpg"]
    assert settled["asset_id"] not in (None, old_asset)
    assert _asset(harness, settled["asset_id"])["sha256"] == _digest(b"ZZZZ")
    assert _asset(harness, old_asset)["sha256"] == _digest(b"AAAA")
    assert _asset(harness, old_asset)["state"] == "ACTIVE"


def test_replaced_content_stays_explicitly_unconfirmed_when_the_scan_stops(
    harness: Harness, tmp_path: Path
) -> None:
    """A stopped scan leaves the changed position content-pending, never silently bound."""
    root = tmp_path / "library"
    _write(root / "a.jpg", b"OLD-CONTENT")
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    assert harness.service.run_scan(source_id)["state"] == "SUCCEEDED"
    old_asset = _locations(harness, source_id)["a.jpg"]["asset_id"]
    assert old_asset is not None

    _write(root / "a.jpg", b"NEW-CONTENT-WITH-OTHER-SIZE")
    with pytest.raises(ScanInterrupted):
        harness.service.run_scan(source_id, interrupt_after=0)

    pending = _locations(harness, source_id)["a.jpg"]
    assert pending["availability"] == "PRESENT"
    # The changed observation cleared the content binding before any confirmation.
    assert pending["asset_id"] is None
    assert pending["last_seen_scan_id"] == 2
    assert _scan_row(harness, 2)["state"] == "RUNNING"
    assert _scan_row(harness, 2)["finished_at"] is None
    assert _job(harness, 2)["state"] == "RUNNING"
    counts = harness.service.counts(source_id=source_id)
    assert counts["locations_pending_content"] == 1
    assert counts["locations_missing"] == 0
    assert harness.service.get_source(source_id)["last_successful_scan"] == 1
    assert json.loads(_scan_row(harness, 2)["error"] or "{}") == {}

    harness.clock.advance(61_000)
    resumed = harness.service.run_scan(source_id)

    assert resumed["scan_id"] == 2
    assert resumed["state"] == "SUCCEEDED"
    assert resumed["reused_scan_request"] is True
    settled = _locations(harness, source_id)["a.jpg"]
    assert settled["asset_id"] not in (None, old_asset)
    assert _asset(harness, settled["asset_id"])["sha256"] == _digest(b"NEW-CONTENT-WITH-OTHER-SIZE")
    assert harness.service.counts(source_id=source_id)["locations_pending_content"] == 0
    assert harness.service.get_source(source_id)["last_successful_scan"] == 2


# -------------------------------------------------------------------- 4. offline
def test_unreachable_source_never_becomes_a_deletion_conclusion(
    harness: Harness, tmp_path: Path
) -> None:
    """A disconnected volume keeps every position and every artifact; nothing is deleted."""
    root = tmp_path / "library"
    first = _write_jpeg(root / "a.jpg", color=(200, 10, 10))
    second = _write_jpeg(root / "b.jpg", color=(10, 200, 10))
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    assert harness.service.run_scan(source_id)["state"] == "SUCCEEDED"
    asset_a = _locations(harness, source_id)["a.jpg"]["asset_id"]
    assert harness.service.generate_thumbnail()["generated"] == 2
    artifacts = {path: _stamp(path) for path in _published(harness)}
    assert len(artifacts) == 2

    moved = tmp_path / "library-unplugged"
    os.replace(root, moved)
    with pytest.raises(CatalogError) as error:
        harness.service.run_scan(source_id)
    assert error.value.code == SOURCE_OFFLINE

    assert _scan_row(harness, 2)["state"] == "BLOCKED"
    assert _scan_row(harness, 2)["finished_at"] is None
    assert json.loads(_scan_row(harness, 2)["error"])["code"] == SOURCE_OFFLINE
    job = _job(harness, 2)
    assert job["state"] == "BLOCKED" and job["attempts"] == 0 and job["lease_token"] is None
    source = harness.service.get_source(source_id)
    assert source["status"] == "OFFLINE"
    assert source["last_successful_scan"] == 1
    counts = harness.service.counts(source_id=source_id)
    assert counts["locations"] == 2
    assert counts["locations_present"] == 2
    assert counts["locations_missing"] == 0  # no batch deletion was invented
    assert counts["assets"] == 2

    # Existing thumbnails stay browsable byte for byte; the honest "original unavailable"
    # answer appears only where nothing was published yet.
    assert {path: _stamp(path) for path in _published(harness)} == artifacts
    thumbnails = harness.service.media_report(kinds=("thumbnail",))
    assert thumbnails["kinds"]["thumbnail"]["published"] == 2
    assert thumbnails["kinds"]["thumbnail"]["source_missing"] == 0
    previews = harness.service.media_report(kinds=("preview",))
    assert previews["kinds"]["preview"]["source_missing"] == 2
    unmet = harness.service.generate_media("preview", asset_ids=[asset_a])
    assert unmet["source_missing"] == 1
    assert unmet["failures"][0]["code"] == SOURCE_FILE_MISSING

    # The volume comes back: the same logical scan resumes and completes with no missing mark.
    os.replace(moved, root)
    resumed = harness.service.run_scan(source_id)
    assert resumed["scan_id"] == 2
    assert resumed["state"] == "SUCCEEDED"
    assert resumed["locations_missing"] == 0
    assert harness.service.get_source(source_id)["status"] == "ONLINE"
    assert harness.service.get_source(source_id)["last_successful_scan"] == 2

    # Only a later *complete* scan may conclude that a genuinely deleted file is missing.
    second.unlink()
    final = harness.service.run_scan(source_id)
    assert final["scan_id"] == 3
    assert final["locations_missing"] == 1
    rows = _locations(harness, source_id)
    assert rows["b.jpg"]["availability"] == "MISSING"
    assert rows["a.jpg"]["availability"] == "PRESENT"
    assert _scan_row(harness, 2)["state"] == "SUCCEEDED"
    assert first.exists()


def test_source_vanishing_during_confirmation_blocks_the_scan(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real mid-scan unplug stops the scan; it never concludes that files were deleted."""
    root = tmp_path / "library"
    _write(root / "a.jpg", b"VANISH-A")
    _write(root / "b.jpg", b"VANISH-B")
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    moved = tmp_path / "library-detached"
    real_hash = scanner.sha256_file
    state = {"moved": False}

    def detach_then_hash(path: Path) -> str:
        if not state["moved"]:
            state["moved"] = True
            os.replace(root, moved)  # the volume is unplugged during content confirmation
        return real_hash(path)

    monkeypatch.setattr(scanner, "sha256_file", detach_then_hash)
    with pytest.raises(CatalogError) as error:
        harness.service.run_scan(source_id)
    assert error.value.code == FILE_READ_ERROR

    monkeypatch.setattr(scanner, "sha256_file", real_hash)
    assert _scan_row(harness, 1)["state"] == "BLOCKED"
    assert _scan_row(harness, 1)["finished_at"] is None
    assert _job(harness, 1)["state"] == "BLOCKED"
    counts = harness.service.counts(source_id=source_id)
    assert counts["locations"] == 2
    assert counts["locations_present"] == 2
    assert counts["locations_missing"] == 0
    assert counts["assets_total"] == 0
    assert harness.service.get_source(source_id)["last_successful_scan"] is None
    # The originals were only moved by the fixture, never modified or deleted by the scan.
    assert (moved / "a.jpg").read_bytes() == b"VANISH-A"

    os.replace(moved, root)
    recovered = harness.service.run_scan(source_id)
    assert recovered["scan_id"] == 1
    assert recovered["state"] == "SUCCEEDED"
    assert recovered["locations_missing"] == 0
    assert harness.service.counts(source_id=source_id)["assets"] == 2


def test_position_vanishing_before_confirmation_is_blocked(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that disappears between enumeration and confirmation is not called deleted yet."""
    root = tmp_path / "library"
    target = _write(root / "a.jpg", b"DISAPPEARS")
    _write(root / "b.jpg", b"STAYS")
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]
    real_hash = scanner.sha256_file
    state = {"deleted": False}

    def delete_then_hash(path: Path) -> str:
        if not state["deleted"]:
            state["deleted"] = True
            target.unlink()
        return real_hash(path)

    monkeypatch.setattr(scanner, "sha256_file", delete_then_hash)
    with pytest.raises(CatalogError) as error:
        harness.service.run_scan(source_id)
    assert error.value.code == FILE_READ_ERROR

    monkeypatch.setattr(scanner, "sha256_file", real_hash)
    counts = harness.service.counts(source_id=source_id)
    assert counts["locations"] == 2
    assert counts["locations_present"] == 2  # the positive observation is kept as-is
    assert counts["locations_missing"] == 0
    assert counts["assets_total"] == 0
    assert _scan_row(harness, 1)["state"] == "BLOCKED"
    assert harness.service.get_source(source_id)["last_successful_scan"] is None

    # The next complete scan is allowed to conclude the deletion.
    recovered = harness.service.run_scan(source_id)
    assert recovered["scan_id"] == 1
    assert recovered["state"] == "SUCCEEDED"
    assert recovered["locations_missing"] == 1
    rows = _locations(harness, source_id)
    assert rows["a.jpg"]["availability"] == "MISSING"
    assert rows["a.jpg"]["asset_id"] is None
    assert rows["b.jpg"]["availability"] == "PRESENT"


# --------------------------------------------------------------------- 5. remap
def test_remapping_the_root_preserves_identity_and_positions(
    harness: Harness, tmp_path: Path
) -> None:
    """A new mount point is a mapping change: no re-identifying, no path rewriting."""
    root = tmp_path / "照 片 根"
    _write_jpeg(root / "a.jpg", color=(10, 20, 30))
    _write_jpeg(root / "子 目录" / "b 图.jpg", color=(240, 220, 20))
    registered = harness.service.register_source(display_name="library", root_path=root)
    source_id = registered["source_id"]
    assert harness.service.run_scan(source_id)["state"] == "SUCCEEDED"
    before_locations = _locations(harness, source_id)
    before_assets = {path: row["asset_id"] for path, row in before_locations.items()}
    before_keys = {path: row["path_key"] for path, row in before_locations.items()}
    first_scan = harness.service.get_source(source_id)["last_successful_scan"]
    assert first_scan == 1
    assert harness.service.generate_thumbnail()["generated"] == 2
    artifacts = {path: _stamp(path) for path in _published(harness)}
    assert len(artifacts) == 2

    # The card's pre-check: the product remap endpoint does not exist yet (architecture
    # §10.2 lists the path, P1 has no implementation), so the identity proof below runs
    # through the controlled ``source_roots.root_path`` update in a write transaction.
    # ``create_app`` performs no I/O, so this inventory creates nothing on disk.
    paths = create_app().openapi()["paths"]
    assert "/api/v1/sources/{source_id}" in paths
    assert not any("remap" in path for path in paths)

    new_root = tmp_path / "D 盘符" / "照 片 新 根"
    new_root.parent.mkdir(parents=True)
    os.replace(root, new_root)
    with harness.service.catalog.write() as connection:
        connection.execute(
            "UPDATE source_roots SET root_path = ? WHERE source_id = ?",
            (str(new_root), source_id),
        )

    mapped = harness.service.get_source(source_id)
    assert mapped["root_path"] == str(new_root)
    assert mapped["last_successful_scan"] == 1
    # A mapping change alone rewrites no location row at all.
    assert _identities(_locations(harness, source_id)) == _identities(before_locations)

    outcome = harness.service.run_scan(source_id)

    assert outcome["source_id"] == source_id
    assert outcome["scan_id"] == 2
    assert outcome["state"] == "SUCCEEDED"
    assert outcome["files_seen"] == 2
    assert outcome["locations_missing"] == 0  # source-relative keys survived the remap
    after_locations = _locations(harness, source_id)
    assert _identities(after_locations) == _identities(before_locations)
    assert {row["last_seen_scan_id"] for row in after_locations.values()} == {2}
    assert {path: row["asset_id"] for path, row in after_locations.items()} == before_assets
    assert {path: row["path_key"] for path, row in after_locations.items()} == before_keys
    assert harness.service.counts()["assets_total"] == 2
    assert harness.service.get_source(source_id)["last_successful_scan"] == 2
    assert harness.service.get_source(source_id)["status"] == "ONLINE"

    # Content-addressed artifacts are reused; the remap regenerates nothing.
    assert {path: _stamp(path) for path in _published(harness)} == artifacts
    assert harness.service.generate_thumbnail()["already_published"] == 2


# ------------------------------------------------------------------- 6. restart
def test_restart_recovery_takes_over_the_same_scan_run(
    harness: Harness, tmp_path: Path
) -> None:
    """A killed process is recovered by the same scan_run, with no duplicate identities."""
    root = tmp_path / "library"
    payloads = {f"p{index}.jpg": f"PAYLOAD-{index}".encode() for index in range(6)}
    for name, payload in payloads.items():
        _write(root / name, payload)
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]

    with pytest.raises(ScanInterrupted):
        harness.service.run_scan(source_id, interrupt_after=2)

    interrupted = _scan_row(harness, 1)
    assert interrupted["state"] == "RUNNING" and interrupted["finished_at"] is None
    job = _job(harness, 1)
    assert job["state"] == "RUNNING" and job["attempts"] == 0 and job["lease_token"] is not None
    partial = _locations(harness, source_id)
    assert len(partial) == 6
    bound_before = {path: row["asset_id"] for path, row in partial.items() if row["asset_id"]}
    assert set(bound_before) == {"p0.jpg", "p1.jpg"}
    counts = harness.service.counts(source_id=source_id)
    assert counts["scans"] == 1 and counts["jobs"] == 1
    assert counts["locations_pending_content"] == 4
    assert counts["locations_missing"] == 0
    assert harness.service.get_source(source_id)["last_successful_scan"] is None

    # The process dies; meanwhile a bound file is replaced and another is deleted.
    harness.service.close()
    _write(root / "p1.jpg", b"REPLACED-WHILE-DOWN")
    (root / "p5.jpg").unlink()
    harness.clock.advance(61_000)
    restarted = harness.reopen()
    requested = restarted.start_scan(source_id)
    assert requested["reused"] is True and requested["scan_id"] == 1
    assert requested["state"] == "RUNNING"
    assert restarted.counts(source_id=source_id)["jobs"] == 1  # no concurrent second scan

    outcome = restarted.run_scan(source_id)

    assert outcome["scan_id"] == 1
    assert outcome["state"] == "SUCCEEDED"
    assert outcome["reused_scan_request"] is True
    assert outcome["files_seen"] == 5
    assert outcome["locations_missing"] == 1
    final_counts = restarted.counts(source_id=source_id)
    assert final_counts["scans"] == 1 and final_counts["jobs"] == 1
    assert final_counts["locations"] == 6
    final = _locations(harness, source_id)
    # The untouched position keeps its identity across the restart.
    assert final["p0.jpg"]["asset_id"] == bound_before["p0.jpg"]
    assert final["p0.jpg"]["availability"] == "PRESENT"
    # The replaced position never keeps the old identity.
    assert final["p1.jpg"]["asset_id"] not in (None, bound_before["p1.jpg"])
    assert _asset(harness, final["p1.jpg"]["asset_id"])["sha256"] == _digest(
        b"REPLACED-WHILE-DOWN"
    )
    assert final["p1.jpg"]["availability"] == "PRESENT"
    # The deleted position was never confirmed, so it is missing with no content.
    assert final["p5.jpg"]["availability"] == "MISSING"
    assert final["p5.jpg"]["asset_id"] is None
    # The pre-crash content identity of p1 is retained and still ACTIVE.
    assert _asset(harness, bound_before["p1.jpg"])["state"] == "ACTIVE"
    assert final_counts["locations_pending_content"] == 0
    assert restarted.get_source(source_id)["last_successful_scan"] == 1
    taken_over = _job(harness, 1)
    assert taken_over["state"] == "SUCCEEDED"
    assert taken_over["attempts"] == 1  # one lost execution, per the frozen rule
    assert taken_over["progress"] == 1.0


def test_repeated_takeovers_increase_attempts_monotonically(
    harness: Harness, tmp_path: Path
) -> None:
    """Each lease takeover costs exactly one attempt, and never creates a second scan."""
    root = tmp_path / "library"
    for index in range(4):
        _write(root / f"photo-{index}.jpg", f"CONTENT-{index}".encode())
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]

    with pytest.raises(ScanInterrupted):
        harness.service.run_scan(source_id, interrupt_after=1)
    assert _job(harness, 1)["attempts"] == 0
    assert _scan_row(harness, 1)["state"] == "RUNNING"

    harness.clock.advance(61_000)
    with pytest.raises(ScanInterrupted):
        harness.service.run_scan(source_id, interrupt_after=1)
    assert _job(harness, 1)["attempts"] == 1
    assert _scan_row(harness, 1)["state"] == "RUNNING"

    harness.clock.advance(61_000)
    outcome = harness.service.run_scan(source_id)

    assert outcome["scan_id"] == 1
    assert outcome["state"] == "SUCCEEDED"
    assert outcome["locations_missing"] == 0
    job = _job(harness, 1)
    assert job["state"] == "SUCCEEDED" and job["attempts"] == 2
    counts = harness.service.counts(source_id=source_id)
    assert counts["scans"] == 1 and counts["jobs"] == 1
    assert counts["locations"] == 4 and counts["assets"] == 4
    assert counts["locations_pending_content"] == 0


# ----------------------------------------------------------------- boundaries
def test_empty_and_single_file_sources(harness: Harness, tmp_path: Path) -> None:
    """0 files, 1 file and an empty subdirectory are all complete, honest scans."""
    root = tmp_path / "library"
    (root / "empty 子目录").mkdir(parents=True)
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]

    empty = harness.service.run_scan(source_id)
    assert empty["state"] == "SUCCEEDED"
    assert empty["files_seen"] == 0 and empty["locations_missing"] == 0
    assert harness.service.counts(source_id=source_id)["locations"] == 0
    assert harness.service.counts()["assets_total"] == 0
    assert harness.service.get_source(source_id)["last_successful_scan"] == 1

    target = _write(root / "only.jpg", b"ONLY-ONE")
    single = harness.service.run_scan(source_id)
    assert single["state"] == "SUCCEEDED" and single["files_seen"] == 1
    assert harness.service.counts(source_id=source_id)["locations_present"] == 1

    target.unlink()
    gone = harness.service.run_scan(source_id)
    assert gone["state"] == "SUCCEEDED"
    assert gone["locations_missing"] == 1
    final = harness.service.counts(source_id=source_id)
    assert final["locations"] == 1 and final["locations_missing"] == 1
    assert final["assets_total"] == 1  # the identity is retained, not deleted
    assert harness.service.get_source(source_id)["last_successful_scan"] == 3


def test_large_source_scan_has_no_duplicates(harness: Harness, tmp_path: Path) -> None:
    """>1000 files in one source: one location and one asset per file, no duplicates."""
    root = tmp_path / "library"
    total = 1001
    for index in range(total):
        _write(root / f"batch-{index:04d}.jpg", f"DISTINCT-{index:05d}".encode())
    source_id = harness.service.register_source(display_name="library", root_path=root)["source_id"]

    outcome = harness.service.run_scan(source_id)

    assert outcome["state"] == "SUCCEEDED"
    assert outcome["files_seen"] == total
    assert outcome["locations_missing"] == 0
    counts = harness.service.counts(source_id=source_id)
    assert counts["locations"] == total
    assert counts["locations_present"] == total
    assert counts["assets"] == total
    assert harness.service.counts()["assets_total"] == total
    assert (
        harness.service.catalog.read_one(
            "SELECT COUNT(DISTINCT path_key) AS value FROM file_locations WHERE source_id = ?",
            (source_id,),
        )["value"]
        == total
    )

    (root / "batch-0000.jpg").unlink()
    after = harness.service.run_scan(source_id)
    assert after["locations_missing"] == 1
    assert harness.service.counts(source_id=source_id)["locations_present"] == total - 1
    assert harness.service.counts()["assets_total"] == total
