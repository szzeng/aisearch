"""Shared ``%TEMP%`` fixtures for the P3-01 OCR suites.

Every image is rendered in the pytest temporary directory from a real system font: the
fixtures contain genuine Chinese text and digits, so the OCR assertions are about real
recognition, not about a stub. No fixture ever touches a real photo library, and the pinned
weights are only ever *copied* into the temporary data root, never moved or modified.

The pinned weights themselves come from the P0-04 model root (the machine's own data root).
When they are not installed, ``OCR_WEIGHTS_AVAILABLE`` is ``False`` and every test that needs
a real recogniser is skipped with that reason instead of pretending to pass.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw, ImageFont

from photo_search.catalog.db import Catalog
from photo_search.ingestion.service import CatalogService
from photo_search.ocr import weights
from photo_search.ocr.processor import ocr_processor_spec
from photo_search.ocr.service import (
    catch_up_projection,
    enqueue_targets,
    job_counts,
    ocr_targets,
    run_queue,
)

WALL_BASE_MS = 1_700_000_000_000
QUIET_AGE_MS = 3_600_000

#: The real pinned model root of this machine (the default data root's ``models``).
PINNED_MODEL_ROOT = weights.model_root()
OCR_WEIGHTS_AVAILABLE = weights.weights_present()
SKIP_WITHOUT_WEIGHTS = pytest.mark.skipif(
    not OCR_WEIGHTS_AVAILABLE,
    reason="the pinned PP-OCRv6 weights are not installed under the P0-04 model root",
)

#: The system font P0-04 used for its OCR fixtures. CJK rendering needs a font with CJK
#: coverage, so a machine without it skips instead of asserting on empty pixels.
FONT_PATH = Path(r"C:\Windows\Fonts\msyh.ttc")
if not FONT_PATH.is_file():
    FONT_PATH = Path("/System/Library/Fonts/STHeiti Medium.ttc")
FONT_AVAILABLE = FONT_PATH.is_file()
SKIP_WITHOUT_FONT = pytest.mark.skipif(
    not FONT_AVAILABLE, reason=f"{FONT_PATH} is not installed on this machine"
)


class Clock:
    def __init__(self, start: int = WALL_BASE_MS) -> None:
        self.ms = int(start)

    def __call__(self) -> int:
        return self.ms

    def advance(self, delta_ms: int) -> None:
        self.ms += int(delta_ms)


class Usage:
    def __init__(self, free: int = 10**12) -> None:
        self.total = 10**12
        self.used = 0
        self.free = int(free)

    def __call__(self, path: Any = None) -> Usage:
        return self


def stamp(path: Path) -> Path:
    moment = (WALL_BASE_MS - QUIET_AGE_MS) * 1_000_000
    os.utime(path, ns=(moment, moment))
    return path


def write_text_image(
    path: Path,
    lines: list[str],
    *,
    size: tuple[int, int] = (1400, 900),
    font_size: int = 48,
    background: tuple[int, int, int] = (255, 255, 255),
    color: tuple[int, int, int] = (10, 10, 10),
    columns: int = 1,
    line_gap: int = 24,
) -> Path:
    """Render a real, high-contrast image with genuine text (Chinese and digits)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(str(FONT_PATH), int(font_size))
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    rows = max(1, (len(lines) + columns - 1) // columns)
    column_width = size[0] // max(1, columns)
    line_height = font_size + line_gap
    for index, line in enumerate(lines):
        column = index // rows
        row = index % rows
        draw.text(
            (30 + column * column_width, 30 + row * line_height),
            line,
            font=font,
            fill=color,
        )
    image.save(path, "JPEG", quality=95)
    return stamp(path)


def write_blank_image(path: Path, *, size: tuple[int, int] = (800, 500)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (255, 255, 255)).save(path, "JPEG", quality=95)
    return stamp(path)


def install_weights(
    data_root: Path, *, corrupt: str | None = None, omit: str | None = None
) -> Path:
    """Copy the pinned files into ``<data_root>/models`` (optionally faulted)."""
    if not OCR_WEIGHTS_AVAILABLE:
        raise RuntimeError("the pinned PP-OCRv6 weights are not installed on this machine")
    resolved = weights.resolve()
    target = weights.model_root(data_root)
    for entry in resolved.files:
        if omit is not None and entry.role == str(omit):
            continue
        destination = target / entry.relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry.absolute_path, destination)
        if corrupt is not None and entry.role == str(corrupt):
            # Same byte size, different content: exactly the "installed but not the pinned
            # file" case the SHA-256 check exists for.
            with destination.open("r+b") as stream:
                stream.seek(entry.expected_bytes // 2)
                original = stream.read(1)
                stream.seek(entry.expected_bytes // 2)
                stream.write(bytes([original[0] ^ 0xFF]))
    return target


class Harness:
    """A registered temp library with the OCR helpers of the P3-01 suite."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = Path(tmp_path)
        self.data_root = self.tmp_path / "PhotoSearchData"
        self.library = self.tmp_path / "library"
        self.library.mkdir(parents=True, exist_ok=True)
        self.clock = Clock()
        self.usage = Usage()
        self.service = CatalogService.open(
            data_root=self.data_root,
            clock=self.clock,
            lease_seconds=60,
            disk_usage=self.usage,
            wall_clock_ms=self.clock,
            stability_sleep=self.clock.advance,
        )
        source = self.service.register_source(display_name="library", root_path=self.library)
        self.source_id = int(source["source_id"])

    @property
    def catalog(self) -> Catalog:
        return self.service.catalog

    def install_weights(self, *, corrupt: str | None = None, omit: str | None = None) -> Path:
        return install_weights(self.data_root, corrupt=corrupt, omit=omit)

    def scan(self) -> dict[str, Any]:
        return self.service.run_scan(self.source_id)

    def asset_id(self, relative: str) -> int:
        row = self.catalog.read_one(
            "SELECT asset_id FROM file_locations WHERE relative_path = ?", (relative,)
        )
        assert row is not None, f"{relative} was not catalogued"
        return int(row["asset_id"])

    def sha_of(self, asset_id: int) -> str:
        row = self.catalog.read_one("SELECT sha256 FROM assets WHERE asset_id = ?", (int(asset_id),))
        assert row is not None
        return str(row["sha256"])

    # ------------------------------------------------------------------ OCR helpers
    def targets(self, **options: Any) -> list[dict[str, Any]]:
        return ocr_targets(self.catalog, **options)

    def enqueue(self, targets: list[dict[str, Any]] | None = None, *, force: bool = False) -> dict[str, int]:
        if targets is None:
            targets = self.targets()
        return enqueue_targets(
            self.catalog, targets, now_ms=self.clock(), force=bool(force)
        )

    def run_ocr(self, *, limit: int | None = None, **options: Any) -> dict[str, Any]:
        return run_queue(self.catalog, data_root=self.data_root, limit=limit, **options)

    def recognize(self, *, force: bool = False, **options: Any) -> dict[str, Any]:
        """Install-independent end-to-end helper: enqueue everything, run, catch up."""
        queued = self.enqueue(force=force)
        run = self.run_ocr(**options)
        projection = catch_up_projection(self.catalog)
        return {"enqueue": queued, "run": run, "projection": projection}

    def ocr_document(self, asset_id: int) -> Any:
        return self.catalog.read_one(
            "SELECT * FROM asset_ocr_text WHERE asset_id = ?", (int(asset_id),)
        )

    def ocr_regions(self, asset_id: int) -> list[Any]:
        return self.catalog.read_all(
            "SELECT * FROM asset_ocr_regions WHERE asset_id = ? ORDER BY region_index",
            (int(asset_id),),
        )

    def document_row(self, asset_id: int) -> Any:
        return self.catalog.read_one(
            "SELECT * FROM search_documents WHERE asset_id = ?", (int(asset_id),)
        )

    def text_hits(self, match: str) -> list[int]:
        return [
            int(row["asset_id"])
            for row in self.catalog.read_all(
                "SELECT rowid AS asset_id FROM search_documents_fts WHERE "
                "search_documents_fts MATCH ? ORDER BY rowid",
                (str(match),),
            )
        ]

    def jobs(self) -> dict[str, int]:
        return job_counts(self.catalog)

    def spec(self) -> Any:
        return ocr_processor_spec()

    def close(self) -> None:
        self.service.close()


@pytest.fixture()
def harness(tmp_path: Path) -> Harness:
    instance = Harness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()
