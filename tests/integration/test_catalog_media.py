"""P1-03 D-level: real orientation/media fixtures, cache rebuild, isolation and CLI.

Every fixture is created inside pytest's ``tmp_path`` (``%TEMP%``); no real photo library
is ever read.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageOps
from PIL.ExifTags import Base as ExifBase

from photo_search.catalog.errors import (
    ARTIFACT_WRITE_FAILED,
    ASSET_NOT_FOUND,
    DECODE_FAILED,
    DISK_SPACE_INSUFFICIENT,
    FORMAT_UNSUPPORTED,
    INPUT_UNSTABLE,
    SOURCE_FILE_MISSING,
    CatalogError,
)
from photo_search.ingestion.service import CatalogService
from photo_search.media import cache as cache_module
from photo_search.media import pipeline
from photo_search.media.contract import PREVIEW_SPEC, THUMBNAIL_SPEC
from photo_search.media.pipeline import MediaInterrupted

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WALL_BASE_MS = time.time_ns() // 1_000_000
QUIET_AGE_MS = 3_600_000
CORE_TABLES = (
    "metadata",
    "source_roots",
    "scan_runs",
    "assets",
    "file_locations",
    "jobs",
    "runtime_capabilities",
    "runtime_qualification_evidence",
    "scheduler_state",
)

#: Independent EXIF-orientation ground truth, mirroring the spec transform table.
ORIENTATION_TRANSFORMS = {
    1: lambda image: image,
    2: ImageOps.mirror,
    3: lambda image: image.rotate(180),
    4: ImageOps.flip,
    5: lambda image: image.transpose(Image.Transpose.TRANSPOSE),
    6: lambda image: image.transpose(Image.Transpose.ROTATE_270),
    7: lambda image: image.transpose(Image.Transpose.TRANSVERSE),
    8: lambda image: image.transpose(Image.Transpose.ROTATE_90),
}


class Clock:
    def __init__(self, start: int = 1_000_000) -> None:
        self.ms = start

    def __call__(self) -> int:
        return self.ms

    def advance(self, delta_ms: int) -> None:
        self.ms += delta_ms


class WallClock(Clock):
    """Wall-clock stand-in: the scanner compares file ``mtime`` against this."""


class Usage:
    def __init__(self, free: int) -> None:
        self.total = 10**12
        self.used = 0
        self.free = free


@dataclass
class Harness:
    service: CatalogService
    clock: Clock
    wall: WallClock
    data_root: Path
    library: Path


@pytest.fixture()
def harness(tmp_path: Path):
    clock = Clock()
    wall = WallClock(WALL_BASE_MS)
    data_root = tmp_path / "PhotoSearchData"
    library = tmp_path / "library"
    library.mkdir(parents=True)
    service = CatalogService.open(
        data_root=data_root,
        clock=clock,
        lease_seconds=60,
        wall_clock_ms=wall,
        stability_sleep=wall.advance,
    )
    try:
        yield Harness(service=service, clock=clock, wall=wall, data_root=data_root, library=library)
    finally:
        service.close()


# --------------------------------------------------------------------------- fixtures
def stamp(path: Path, *, age_ms: int = QUIET_AGE_MS) -> Path:
    moment = (WALL_BASE_MS - age_ms) * 1_000_000
    os.utime(path, ns=(moment, moment))
    return path


def quadrants(width: int = 320, height: int = 200) -> Image.Image:
    image = Image.new("RGB", (width, height))
    image.paste((230, 20, 20), (0, 0, width // 2, height // 2))
    image.paste((20, 230, 20), (width // 2, 0, width, height // 2))
    image.paste((20, 20, 230), (0, height // 2, width // 2, height))
    image.paste((235, 235, 30), (width // 2, height // 2, width, height))
    image.paste((0, 0, 0), (8, 8, 56, 40))
    return image


def write_image(
    path: Path,
    *,
    image: Image.Image | None = None,
    image_format: str = "JPEG",
    orientation: int | None = None,
    quality: int = 95,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    target = image if image is not None else quadrants()
    options: dict[str, Any] = {}
    if image_format == "JPEG":
        options.update(quality=quality)
        if orientation is not None:
            exif = Image.Exif()
            exif[ExifBase.Orientation] = int(orientation)
            options["exif"] = exif.tobytes()
    target.save(path, image_format, **options)
    return stamp(path)


def write_bytes(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return stamp(path)


def register(harness: Harness, root: Path | None = None, name: str = "fixture") -> int:
    return int(
        harness.service.register_source(
            display_name=name, root_path=root or harness.library
        )["source_id"]
    )


def scan(harness: Harness, source_id: int) -> dict[str, Any]:
    outcome = harness.service.run_scan(source_id)
    assert outcome["state"] == "SUCCEEDED", outcome
    return outcome


def asset_for(harness: Harness, relative_path: str, source_id: int = 1) -> int:
    row = harness.service.catalog.read_one(
        "SELECT asset_id FROM file_locations WHERE source_id = ? AND relative_path = ?",
        (source_id, relative_path),
    )
    assert row is not None and row["asset_id"] is not None, relative_path
    return int(row["asset_id"])


def sha_for(harness: Harness, asset_id: int) -> str:
    row = harness.service.catalog.read_one(
        "SELECT sha256 FROM assets WHERE asset_id = ?", (asset_id,)
    )
    assert row is not None
    return str(row["sha256"])


def artifact_for(
    harness: Harness, asset_id: int, *, kind: str = "thumbnail"
) -> Path:
    spec = THUMBNAIL_SPEC if kind == "thumbnail" else PREVIEW_SPEC
    return harness.service.media_cache().artifact_path(
        spec=spec, sha256=sha_for(harness, asset_id)
    )


def published_artifacts(harness: Harness, *, kind: str = "thumbnail") -> list[Path]:
    root = harness.data_root / ("thumbnails" if kind == "thumbnail" else "previews")
    return sorted(root.rglob("*.jpg")) if root.is_dir() else []


def table_dump(harness: Harness) -> dict[str, list[tuple]]:
    return {
        table: [
            tuple(row)
            for row in harness.service.catalog.read_all(f"SELECT * FROM {table} ORDER BY 1")
        ]
        for table in CORE_TABLES
    }


def tree_state(root: Path) -> dict[str, tuple[int, int]]:
    """Size and ``mtime_ns`` of every file below ``root`` (originals are read-only here)."""
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def block_colour(image: Image.Image, centre: tuple[int, int], *, box: int = 8) -> tuple[int, ...]:
    half = box // 2
    pixels = [
        image.getpixel((x, y))
        for x in range(centre[0] - half, centre[0] + half)
        for y in range(centre[1] - half, centre[1] + half)
    ]
    channels = list(zip(*[pixel[:3] for pixel in pixels], strict=True))
    return tuple(int(round(sum(channel) / len(channel))) for channel in channels)


def quadrant_centres(image: Image.Image) -> list[tuple[int, int]]:
    width, height = image.size
    return [
        (width // 4, height // 4),
        (3 * width // 4, height // 4),
        (width // 4, 3 * height // 4),
        (3 * width // 4, 3 * height // 4),
    ]


# ------------------------------------------------------------------------------ tests
def test_thumbnail_batch_publishes_orientation_correct_jpegs(harness: Harness) -> None:
    for orientation in range(1, 9):
        write_image(harness.library / f"orientation-{orientation}.jpg", orientation=orientation)
    write_image(harness.library / "small.png", image=quadrants(48, 24), image_format="PNG")
    source_id = register(harness)
    scan(harness, source_id)
    before = tree_state(harness.library)

    summary = harness.service.generate_thumbnail()

    assert summary["processor_id"] == "catalog-thumbnail@1"
    assert summary["long_edge"] == 320
    assert summary["assets_considered"] == 9
    assert summary["generated"] == 9
    assert summary["already_published"] == 0
    assert summary["failed"] == 0
    assert summary["unsupported"] == 0
    assert summary["artifacts_written"] == 9
    assert summary["bytes_written"] > 0
    assert summary["failures"] == []

    for orientation in range(1, 9):
        asset_id = asset_for(harness, f"orientation-{orientation}.jpg")
        artifact = artifact_for(harness, asset_id)
        assert artifact.is_file()
        with Image.open(harness.library / f"orientation-{orientation}.jpg") as stored:
            expected = ORIENTATION_TRANSFORMS[orientation](stored.copy())
        with Image.open(artifact) as rendered:
            assert rendered.format == "JPEG"
            assert max(rendered.size) <= 320
            assert rendered.size == expected.size
            for got, want in zip(
                quadrant_centres(rendered), quadrant_centres(expected), strict=True
            ):
                assert block_colour(rendered, got) == pytest.approx(
                    block_colour(expected, want), abs=20
                )

    small = artifact_for(harness, asset_for(harness, "small.png"))
    with Image.open(small) as rendered:
        assert rendered.size == (48, 24)
    # The originals are only ever read: no new file, no changed size or mtime.
    assert tree_state(harness.library) == before


def test_repeat_runs_and_single_asset_requests_are_idempotent(harness: Harness) -> None:
    for index in range(3):
        write_image(harness.library / f"photo-{index}.jpg", image=quadrants(320 + index, 200))
    source_id = register(harness)
    scan(harness, source_id)

    first = harness.service.generate_thumbnail()
    assert first["generated"] == 3
    stamps = {path: path.stat().st_mtime_ns for path in published_artifacts(harness)}

    second = harness.service.generate_thumbnail()
    assert second["generated"] == 0
    assert second["already_published"] == 3
    assert second["artifacts_written"] == 0
    assert {path: path.stat().st_mtime_ns for path in published_artifacts(harness)} == stamps

    asset_id = asset_for(harness, "photo-0.jpg")
    single = harness.service.generate_thumbnail(asset_ids=[asset_id])
    assert single["assets_considered"] == 1
    assert single["already_published"] == 1
    assert single["generated"] == 0
    assert len(published_artifacts(harness)) == 3


def test_broken_and_unsupported_inputs_are_isolated_with_reasons(harness: Harness) -> None:
    write_image(harness.library / "good.jpg")
    truncated = write_image(harness.library / "truncated.jpg", image=quadrants(400, 300))
    payload = truncated.read_bytes()
    write_bytes(truncated, payload[: len(payload) // 2])
    write_bytes(harness.library / "zero.jpg", b"")
    write_bytes(harness.library / "notes.txt", b"not an image at all")
    write_image(harness.library / "scan.tif", image=quadrants(60, 40), image_format="TIFF")
    source_id = register(harness)
    scan(harness, source_id)

    summary = harness.service.generate_thumbnail()

    assert summary["generated"] == 1
    assert summary["failed"] == 3
    assert summary["unsupported"] == 1
    assert summary["already_published"] == 0
    codes = sorted(
        (item["state"], item["code"]) for item in summary["failures"]
    )
    assert codes == [
        ("FAILED", DECODE_FAILED),
        ("FAILED", DECODE_FAILED),
        ("FAILED", DECODE_FAILED),
        ("UNSUPPORTED", FORMAT_UNSUPPORTED),
    ]
    assert len(published_artifacts(harness)) == 1

    report = harness.service.media_report(include_failures=True)
    assert report["kinds"]["thumbnail"]["failure_codes"] == {
        DECODE_FAILED: 3,
        FORMAT_UNSUPPORTED: 1,
    }
    assert report["kinds"]["thumbnail"]["published"] == 1
    assert report["kinds"]["thumbnail"]["pending"] == 0
    reasons = {(item["code"], item["source_format"]) for item in report["failures"]}
    assert (DECODE_FAILED, None) in reasons
    assert (FORMAT_UNSUPPORTED, "TIFF") in reasons

    # A deterministic content failure is not re-decoded on every later run, but an
    # explicit retry is always possible and changes nothing about the verdict.
    again = harness.service.generate_thumbnail()
    assert again["generated"] == 0
    assert again["already_published"] == 1
    assert again["skipped_cached_failures"] == 4
    retried = harness.service.generate_thumbnail(retry_failed=True)
    assert retried["failed"] == 3
    assert retried["unsupported"] == 1


def test_deleting_the_cache_rebuilds_without_touching_catalog_or_originals(
    harness: Harness,
) -> None:
    write_image(harness.library / "a.jpg")
    write_image(harness.library / "b.jpg", image=quadrants(240, 160))
    source_id = register(harness)
    scan(harness, source_id)
    catalog_before = table_dump(harness)
    originals = tree_state(harness.library)

    assert harness.service.generate_thumbnail()["generated"] == 2
    # P1-03 never writes the catalog: identity, positions and the NULL content-derived
    # columns stay exactly as the scan left them.
    assert table_dump(harness) == catalog_before

    shutil.rmtree(harness.data_root / "thumbnails")
    shutil.rmtree(harness.data_root / "cache", ignore_errors=True)
    emptied = harness.service.media_report()
    assert emptied["kinds"]["thumbnail"]["published"] == 0
    assert emptied["kinds"]["thumbnail"]["pending"] == 2

    rebuilt = harness.service.generate_thumbnail()

    assert rebuilt["generated"] == 2
    assert len(published_artifacts(harness)) == 2
    assert table_dump(harness) == catalog_before
    assert tree_state(harness.library) == originals


def test_interrupted_batch_publishes_nothing_half_written_and_resumes(
    harness: Harness,
) -> None:
    for index in range(4):
        write_image(harness.library / f"photo-{index}.jpg", image=quadrants(320 + index, 200))
    source_id = register(harness)
    scan(harness, source_id)

    with pytest.raises(MediaInterrupted) as interrupted:
        harness.service.generate_thumbnail(interrupt_after=2)

    assert interrupted.value.summary["generated"] == 2
    assert len(published_artifacts(harness)) == 2
    assert list((harness.data_root / "thumbnails").rglob("*.tmp")) == []
    # Every artifact present at its final name is complete and usable.
    for artifact in published_artifacts(harness):
        with Image.open(artifact) as rendered:
            assert rendered.format == "JPEG"
            rendered.load()

    resumed = harness.service.generate_thumbnail()

    assert resumed["generated"] == 2
    assert resumed["already_published"] == 2
    assert len(published_artifacts(harness)) == 4


def test_a_cancelled_or_interrupted_run_writes_nothing(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_image(harness.library / "a.jpg")
    write_image(harness.library / "b.jpg", image=quadrants(240, 160))
    source_id = register(harness)
    scan(harness, source_id)

    with pytest.raises(MediaInterrupted) as cancelled:
        harness.service.generate_thumbnail(cancel_check=lambda: True)
    assert cancelled.value.summary["generated"] == 0
    assert published_artifacts(harness) == []

    def interrupt(*_: Any, **__: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(pipeline, "load_normalized", interrupt)
    with pytest.raises(MediaInterrupted) as killed:
        harness.service.generate_thumbnail()
    assert killed.value.summary["generated"] == 0
    assert published_artifacts(harness) == []
    assert list((harness.data_root / "thumbnails").rglob("*.tmp")) == []


def test_a_failed_artifact_write_aborts_without_a_ready_artifact(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_image(harness.library / "a.jpg")
    write_image(harness.library / "b.jpg", image=quadrants(240, 160))
    source_id = register(harness)
    scan(harness, source_id)
    real_replace = os.replace

    def refuse(source: object, destination: object) -> None:
        raise OSError("simulated failure during the atomic replace")

    monkeypatch.setattr(cache_module.os, "replace", refuse)
    with pytest.raises(CatalogError) as error:
        harness.service.generate_thumbnail()

    assert error.value.code == ARTIFACT_WRITE_FAILED
    assert published_artifacts(harness) == []
    assert list((harness.data_root / "thumbnails").rglob("*.tmp")) == []

    monkeypatch.setattr(cache_module.os, "replace", real_replace)
    recovered = harness.service.generate_thumbnail()
    assert recovered["generated"] == 2
    assert len(published_artifacts(harness)) == 2


def test_a_leftover_temporary_file_is_never_a_ready_artifact(harness: Harness) -> None:
    write_image(harness.library / "a.jpg")
    source_id = register(harness)
    scan(harness, source_id)
    assert harness.service.generate_thumbnail()["generated"] == 1
    artifact = published_artifacts(harness)[0]
    leftover = artifact.parent / f"{artifact.stem}.{os.getpid()}-deadbeef.tmp"
    leftover.write_bytes(b"partial-write-from-a-killed-process")

    report = harness.service.media_report()
    assert report["kinds"]["thumbnail"]["published"] == 1
    assert report["kinds"]["thumbnail"]["temp_files"] == 1

    asset_id = asset_for(harness, "a.jpg")
    again = harness.service.generate_thumbnail(asset_ids=[asset_id])
    assert again["already_published"] == 1
    assert again["generated"] == 0
    assert {path.name for path in published_artifacts(harness)} == {artifact.name}

    pruned = harness.service.media_report(prune_temp=True, min_temp_age_ms=0)
    assert int(pruned["kinds"]["thumbnail"]["temp_files_pruned"]) == 1
    assert not leftover.exists()
    assert artifact.is_file()


def test_unavailable_sources_keep_published_artifacts_and_report_the_rest(
    harness: Harness,
) -> None:
    write_image(harness.library / "a.jpg")
    write_image(harness.library / "b.jpg", image=quadrants(240, 160))
    source_id = register(harness)
    scan(harness, source_id)
    browsable = asset_for(harness, "a.jpg")
    unavailable = asset_for(harness, "b.jpg")
    assert harness.service.generate_thumbnail(asset_ids=[browsable])["generated"] == 1

    (harness.library / "a.jpg").unlink()
    (harness.library / "b.jpg").unlink()
    scan(harness, source_id)
    assert harness.service.counts(source_id=source_id)["locations_missing"] == 2

    # The published thumbnail stays usable, and the batch does not re-read the source.
    still_usable = harness.service.generate_thumbnail(asset_ids=[browsable])
    assert still_usable["already_published"] == 1
    assert still_usable["source_missing"] == 0

    # Nothing was ever generated for the other asset: the honest answer is "source file
    # unavailable", not a decode failure and not a blank placeholder.
    missing = harness.service.generate_thumbnail(asset_ids=[unavailable])
    assert missing["source_missing"] == 1
    assert missing["failed"] == 0
    assert missing["failures"][0]["code"] == SOURCE_FILE_MISSING

    # The default (library-wide) batch only enumerates assets with a PRESENT position.
    assert harness.service.generate_thumbnail()["assets_considered"] == 0

    report = harness.service.media_report()
    assert report["kinds"]["thumbnail"]["published"] == 1
    assert report["kinds"]["thumbnail"]["source_missing"] == 1
    assert report["kinds"]["thumbnail"]["failed"] == 0
    assert report["kinds"]["thumbnail"]["failure_codes"] == {}


def test_an_offline_source_still_serves_published_thumbnails(harness: Harness) -> None:
    write_image(harness.library / "a.jpg")
    write_image(harness.library / "b.jpg", image=quadrants(240, 160))
    source_id = register(harness)
    scan(harness, source_id)
    browsable = asset_for(harness, "a.jpg")
    assert harness.service.generate_thumbnail(asset_ids=[browsable])["generated"] == 1

    moved = harness.library.parent / "library-offline"
    os.replace(harness.library, moved)
    assert not harness.library.exists()

    summary = harness.service.generate_thumbnail()

    assert summary["assets_considered"] == 2
    assert summary["already_published"] == 1
    assert summary["source_missing"] == 1
    assert summary["failed"] == 0
    report = harness.service.media_report()
    assert report["kinds"]["thumbnail"]["published"] == 1
    assert report["kinds"]["thumbnail"]["source_missing"] == 1


def test_a_second_location_is_used_when_the_first_copy_is_gone(harness: Harness, tmp_path: Path) -> None:
    first_root = tmp_path / "photos-a"
    second_root = tmp_path / "photos-b"
    write_image(first_root / "copy.jpg")
    second = write_image(second_root / "copy.jpg")
    first = register(harness, root=first_root, name="a")
    second_id = register(harness, root=second_root, name="b")
    scan(harness, first)
    scan(harness, second_id)
    asset_id = asset_for(harness, "copy.jpg", source_id=second_id)
    assert asset_for(harness, "copy.jpg", source_id=first) == asset_id

    (first_root / "copy.jpg").unlink()
    summary = harness.service.generate_thumbnail()

    assert summary["assets_considered"] == 1
    assert summary["generated"] == 1
    assert summary["source_missing"] == 0
    assert summary["failed"] == 0
    artifact = artifact_for(harness, asset_id)
    with Image.open(second) as expected, Image.open(artifact) as rendered:
        assert rendered.size == expected.size
        for got, want in zip(
            quadrant_centres(rendered), quadrant_centres(expected), strict=True
        ):
            assert block_colour(rendered, got) == pytest.approx(
                block_colour(expected, want), abs=20
            )


def test_disk_shortage_stops_the_batch_and_keeps_existing_artifacts(
    harness: Harness,
) -> None:
    for index in range(4):
        write_image(harness.library / f"photo-{index}.jpg", image=quadrants(320 + index, 200))
    source_id = register(harness)
    scan(harness, source_id)
    calls = {"count": 0}

    def failing_disk_usage(path: object) -> Usage:
        calls["count"] += 1
        return Usage(free=10**12 if calls["count"] <= 2 else 1)

    harness.service.disk_usage = failing_disk_usage
    with pytest.raises(CatalogError) as error:
        harness.service.generate_thumbnail()

    assert error.value.code == DISK_SPACE_INSUFFICIENT
    assert calls["count"] >= 3
    kept = published_artifacts(harness)
    assert len(kept) == 1
    assert list((harness.data_root / "thumbnails").rglob("*.tmp")) == []

    harness.service.disk_usage = lambda path: Usage(free=10**12)
    resumed = harness.service.generate_thumbnail()
    assert resumed["generated"] == 3
    assert resumed["already_published"] == 1
    assert len(published_artifacts(harness)) == 4


def test_read_only_originals_and_awkward_paths_are_generated_untouched(
    harness: Harness, tmp_path: Path
) -> None:
    root = tmp_path / "照片 库 只读"
    long_dir = root / "子 目录" / ("long-segment-0123456789" * 6)
    long_file = long_dir / "c 图片 一.JPG"
    assert len(str(long_file)) > 220
    write_image(long_file)
    read_only = write_image(root / "readonly.jpg", image=quadrants(240, 160))
    os.chmod(read_only, stat.S_IREAD)
    before = tree_state(root)
    try:
        source_id = register(harness, root=root, name="只读 照片库")
        scan(harness, source_id)

        summary = harness.service.generate_thumbnail()

        assert summary["generated"] == 2
        assert summary["failed"] == 0
        assert len(published_artifacts(harness)) == 2
        assert tree_state(root) == before
        assert not (read_only.stat().st_mode & stat.S_IWRITE)
    finally:
        os.chmod(read_only, stat.S_IWRITE | stat.S_IREAD)


def test_a_file_changed_after_the_scan_is_never_published_under_the_old_identity(
    harness: Harness,
) -> None:
    target = write_image(harness.library / "a.jpg")
    source_id = register(harness)
    scan(harness, source_id)
    old_asset = asset_for(harness, "a.jpg")

    # Rewrite the bytes without re-scanning: the catalog still claims the old size/mtime,
    # so the pipeline must refuse to attach the new pixels to the old content identity.
    write_image(target, image=quadrants(300, 180))
    summary = harness.service.generate_thumbnail()

    assert summary["generated"] == 0
    assert summary["input_unstable"] == 1
    assert summary["failures"][0]["code"] == INPUT_UNSTABLE
    assert published_artifacts(harness) == []
    # A transient condition is not written down as a content verdict.
    assert harness.service.media_report()["kinds"]["thumbnail"]["failure_codes"] == {}

    scan(harness, source_id)
    assert asset_for(harness, "a.jpg") != old_asset
    recovered = harness.service.generate_thumbnail()
    assert recovered["generated"] == 1
    assert published_artifacts(harness) == [artifact_for(harness, asset_for(harness, "a.jpg"))]


def test_previews_are_lazy_and_can_be_cleared_independently(harness: Harness) -> None:
    write_image(harness.library / "photo.jpg", image=quadrants(4000, 3000), quality=88)
    source_id = register(harness)
    scan(harness, source_id)
    asset_id = asset_for(harness, "photo.jpg")

    assert not (harness.data_root / "previews").exists()
    thumbnail = harness.service.generate_thumbnail()
    assert thumbnail["generated"] == 1
    assert not (harness.data_root / "previews").exists()

    preview = harness.service.generate_preview(asset_ids=[asset_id])
    assert preview["generated"] == 1
    assert preview["processor_id"] == "catalog-preview@1"
    with Image.open(artifact_for(harness, asset_id, kind="preview")) as rendered:
        assert rendered.size == (1600, 1200)
    with Image.open(artifact_for(harness, asset_id)) as rendered:
        assert rendered.size == (320, 240)

    # Previews are a separate, individually disposable cache: deleting them leaves the
    # grid thumbnails (and every identity) untouched.
    shutil.rmtree(harness.data_root / "previews")
    report = harness.service.media_report()
    assert report["kinds"]["thumbnail"]["published"] == 1
    assert report["kinds"]["preview"]["published"] == 0
    assert report["kinds"]["preview"]["pending"] == 1
    again = harness.service.generate_preview(asset_ids=[asset_id])
    assert again["generated"] == 1


def test_an_older_processing_version_is_never_mixed_with_the_current_one(
    harness: Harness,
) -> None:
    write_image(harness.library / "a.jpg")
    source_id = register(harness)
    scan(harness, source_id)
    asset_id = asset_for(harness, "a.jpg")
    sha256 = sha_for(harness, asset_id)
    stale = (
        harness.data_root
        / "thumbnails"
        / "catalog-thumbnail@0"
        / sha256[:2]
        / sha256[2:4]
        / f"{sha256}.jpg"
    )
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"artifact-from-an-older-processing-version")

    summary = harness.service.generate_thumbnail()

    assert summary["generated"] == 1
    assert stale.read_bytes() == b"artifact-from-an-older-processing-version"
    assert artifact_for(harness, asset_id).is_file()
    report = harness.service.media_report()
    assert report["kinds"]["thumbnail"]["stale_versions"] == ["catalog-thumbnail@0"]
    assert report["kinds"]["thumbnail"]["published"] == 1


def test_media_generation_never_writes_inside_a_source_root(harness: Harness) -> None:
    write_image(harness.library / "a.jpg")
    source_id = register(harness)
    scan(harness, source_id)

    harness.service.generate_thumbnail()
    harness.service.generate_preview(limit=1)

    assert {path.name for path in harness.library.rglob("*")} == {"a.jpg"}
    cache_root = harness.service.media_cache().thumbnails_root
    assert cache_root.is_relative_to(harness.data_root)


# -------------------------------------------------------------------------------- CLI
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


def test_cli_generates_media_and_reports_structured_errors(tmp_path: Path) -> None:
    data_root = tmp_path / "PhotoSearchData"
    library = tmp_path / "照片 库"
    write_image(library / "a.jpg")
    write_image(library / "b.jpg", image=quadrants(240, 160))
    write_image(library / "scan.tif", image=quadrants(60, 40), image_format="TIFF")

    assert _run_cli(data_root, "init", "--json").returncode == 0
    added = _run_cli(data_root, "add-source", "--name", "照片库", "--path", str(library), "--json")
    assert added.returncode == 0, added.stderr
    scanned = _run_cli(data_root, "scan", "--source-id", "1", "--json")
    assert scanned.returncode == 0, scanned.stderr

    thumbnails = _run_cli(data_root, "thumbnail", "--json")
    assert thumbnails.returncode == 0, thumbnails.stderr
    media = json.loads(thumbnails.stdout)["media"]
    assert media["generated"] == 2
    assert media["unsupported"] == 1
    assert media["processor_id"] == "catalog-thumbnail@1"

    status = _run_cli(data_root, "media-status", "--failures", "--json")
    assert status.returncode == 0, status.stderr
    body = json.loads(status.stdout)
    assert body["kinds"]["thumbnail"]["published"] == 2
    assert body["kinds"]["thumbnail"]["unsupported"] == 1
    assert body["kinds"]["preview"]["pending"] == 3
    assert [item["code"] for item in body["failures"]] == [FORMAT_UNSUPPORTED]

    lazy = _run_cli(data_root, "preview", "--json")
    assert lazy.returncode == 2
    assert json.loads(lazy.stdout)["error"]["code"] == "USAGE"

    preview = _run_cli(data_root, "preview", "--asset-id", "1", "--json")
    assert preview.returncode == 0, preview.stderr
    assert json.loads(preview.stdout)["media"]["generated"] == 1

    unknown = _run_cli(data_root, "thumbnail", "--asset-id", "9999", "--json")
    assert unknown.returncode == 1
    assert json.loads(unknown.stdout)["error"]["code"] == ASSET_NOT_FOUND

    negative = _run_cli(data_root, "thumbnail", "--limit", "-1", "--json")
    assert negative.returncode == 2
    assert json.loads(negative.stdout)["error"]["code"] == "USAGE"

    plain = _run_cli(data_root, "thumbnail", "--asset-id", "9999")
    assert plain.returncode == 1
    assert ASSET_NOT_FOUND in plain.stderr


def test_cli_thumbnail_output_stays_stable_across_repeated_runs(tmp_path: Path) -> None:
    data_root = tmp_path / "PhotoSearchData"
    library = tmp_path / "library"
    write_image(library / "a.jpg")
    assert _run_cli(data_root, "add-source", "--name", "lib", "--path", str(library)).returncode == 0
    assert _run_cli(data_root, "scan", "--source-id", "1").returncode == 0

    first = _run_cli(data_root, "thumbnail", "--json")
    second = _run_cli(data_root, "thumbnail", "--json")

    assert first.returncode == 0 and second.returncode == 0
    assert json.loads(first.stdout)["media"]["generated"] == 1
    assert json.loads(second.stdout)["media"]["already_published"] == 1
    assert json.loads(second.stdout)["media"]["generated"] == 0
