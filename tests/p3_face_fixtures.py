"""Shared ``%TEMP%`` fixtures for the P3-02 face suites.

Every picture is a real face that ships with a library this project already depends on
(``skimage``'s NASA astronaut sample, and InsightFace's own test images for the six-person
group photo and the tight portrait). Synthetic variants of the astronaut cover the
identity-preserving cases, the low-quality case and the same-picture conflict case. No
fixture ever touches a real photo library, and the pinned weights are only *copied* into the
temporary data root, never moved or modified.

The pinned weights themselves come from the machine's own P0-04 model root. When they are not
installed, ``FACE_WEIGHTS_AVAILABLE`` is ``False`` and every test that needs a real detector is
skipped with that reason instead of pretending to pass.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from photo_search.catalog.db import Catalog
from photo_search.face import weights
from photo_search.face.engine import FaceEngineCache
from photo_search.face.index import FaceIndexCoordinator
from photo_search.face.processor import face_processor_spec
from photo_search.face.service import (
    catch_up_association,
    catch_up_index,
    detect,
    enqueue_targets,
    face_targets,
    job_counts,
    run_queue,
)
from photo_search.ingestion.service import CatalogService
from photo_search.scheduler.projection import face_summary

WALL_BASE_MS = 1_700_000_000_000
QUIET_AGE_MS = 3_600_000

PINNED_MODEL_ROOT = weights.model_root()
FACE_WEIGHTS_AVAILABLE = weights.weights_present()
SKIP_WITHOUT_FACE_WEIGHTS = pytest.mark.skipif(
    not FACE_WEIGHTS_AVAILABLE,
    reason="the pinned buffalo_l face weights are not installed under the P0-04 model root",
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


# --------------------------------------------------------------------------- fixtures
def astronaut_image() -> Image.Image:
    from skimage import data as skimage_data

    return Image.fromarray(skimage_data.astronaut()).convert("RGB")


def group_photo() -> Image.Image:
    import insightface

    path = Path(insightface.__file__).resolve().parent / "data" / "images" / "t1.jpg"
    return Image.open(path).convert("RGB")


def tight_portrait() -> Image.Image:
    import insightface

    path = (
        Path(insightface.__file__).resolve().parent
        / "data"
        / "images"
        / "Tom_Hanks_54745.png"
    )
    return Image.open(path).convert("RGB")


def brighten(image: Image.Image, *, factor: float = 1.22, offset: float = 8.0) -> Image.Image:
    values = np.clip(np.asarray(image, dtype=np.float32) * factor + offset, 0, 255)
    return Image.fromarray(values.astype(np.uint8))


def downscale(image: Image.Image, *, factor: float) -> Image.Image:
    size = (max(1, int(image.width * factor)), max(1, int(image.height * factor)))
    return image.resize(size, Image.BILINEAR)


def composite_pair(image: Image.Image) -> Image.Image:
    """The same picture pasted twice: the mirror/composite conflict case of §8.3."""
    canvas = Image.new("RGB", (image.width * 2, image.height), (128, 128, 128))
    canvas.paste(image, (0, 0))
    canvas.paste(image, (image.width, 0))
    return canvas


def write_fixture_library(library: Path, *, with_variants: bool = True) -> None:
    """Write the standard P3-02 fixture set into ``library`` (already ``%TEMP%``)."""
    library.mkdir(parents=True, exist_ok=True)

    def save(image: Image.Image, name: str, **options: Any) -> None:
        path = library / name
        image.save(path, **options)
        stamp(path)

    astronaut = astronaut_image()
    save(astronaut, "astronaut.png")
    save(group_photo(), "group.jpg", quality=95)
    save(tight_portrait(), "portrait.png")
    if with_variants:
        save(brighten(astronaut), "astronaut_bright.jpg", quality=92)
        save(downscale(astronaut, factor=0.42), "astronaut_small.jpg", quality=70)
        save(composite_pair(astronaut), "composite.png")


def install_weights(
    data_root: Path, *, corrupt: str | None = None, omit: str | None = None
) -> Path:
    """Copy the pinned files into ``<data_root>/models`` (optionally faulted)."""
    if not FACE_WEIGHTS_AVAILABLE:
        raise RuntimeError("the pinned buffalo_l weights are not installed on this machine")
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
    """A registered temp library with the face helpers of the P3-02 suite."""

    def __init__(self, tmp_path: Path, *, with_variants: bool = True) -> None:
        self.tmp_path = Path(tmp_path)
        self.data_root = self.tmp_path / "PhotoSearchData"
        self.library = self.tmp_path / "library"
        self.clock = Clock()
        self.usage = Usage()
        write_fixture_library(self.library, with_variants=with_variants)
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

    def file_for(self, relative: str) -> Path:
        return self.library / relative

    # ------------------------------------------------------------------ face helpers
    def targets(self, **options: Any) -> list[dict[str, Any]]:
        return face_targets(self.catalog, **options)

    def enqueue(
        self,
        targets: list[dict[str, Any]] | None = None,
        *,
        force: bool = False,
        device: str = "cpu",
    ) -> dict[str, int]:
        if targets is None:
            targets = self.targets()
        return enqueue_targets(
            self.catalog,
            targets,
            now_ms=self.clock(),
            force=bool(force),
            device=str(device),
        )

    def run_faces(
        self, *, limit: int | None = None, device: str = "cpu", **options: Any
    ) -> dict[str, Any]:
        return run_queue(
            self.catalog,
            data_root=self.data_root,
            device=str(device),
            limit=limit,
            **options,
        )

    def index(self) -> dict[str, Any]:
        return catch_up_index(self.catalog)

    def associate(self, **options: Any) -> dict[str, Any]:
        return catch_up_association(self.catalog, **options)

    def detect(
        self, *, device: str = "cpu", force: bool = False, **options: Any
    ) -> dict[str, Any]:
        """The whole closed loop on the temp library."""
        return detect(
            self.catalog,
            data_root=self.data_root,
            device=str(device),
            force=bool(force),
            **options,
        )

    # ----------------------------------------------------------------------- readers
    def faces(self, asset_id: int | None = None) -> list[Any]:
        if asset_id is None:
            return self.catalog.read_all("SELECT * FROM faces ORDER BY asset_id, ordinal")
        return self.catalog.read_all(
            "SELECT * FROM faces WHERE asset_id = ? ORDER BY ordinal", (int(asset_id),)
        )

    def links(self) -> list[Any]:
        return self.catalog.read_all(
            "SELECT * FROM face_person_links ORDER BY person_id, face_id"
        )

    def persons(self) -> list[Any]:
        return self.catalog.read_all("SELECT * FROM persons ORDER BY person_id")

    def person_files(self) -> dict[int, list[str]]:
        rows = self.catalog.read_all(
            "SELECT l.person_id AS person_id, l.decision AS decision, "
            "f.relative_path AS path FROM face_person_links AS l "
            "JOIN faces AS fa ON fa.face_id = l.face_id "
            "JOIN file_locations AS f ON f.asset_id = fa.asset_id "
            "ORDER BY l.person_id, f.relative_path"
        )
        grouped: dict[int, list[str]] = {}
        for row in rows:
            if str(row["decision"]) != "CONFIRMED":
                continue
            grouped.setdefault(int(row["person_id"]), []).append(str(row["path"]))
        return grouped

    def faces_of(self, relative: str) -> list[Any]:
        return self.faces(self.asset_id(relative))

    def summary(self) -> dict[str, Any]:
        return face_summary(self.catalog.db_path, weights_present=True) or {}

    def index_state(self) -> dict[str, Any]:
        return FaceIndexCoordinator(self.catalog).index_state()

    def spec(self) -> Any:
        return face_processor_spec()

    def jobs(self) -> dict[str, int]:
        return job_counts(self.catalog)

    def cache(self) -> FaceEngineCache:
        return FaceEngineCache()

    def close(self) -> None:
        self.service.close()


@pytest.fixture()
def harness(tmp_path: Path) -> Harness:
    instance = Harness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()
