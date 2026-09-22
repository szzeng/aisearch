"""P4-00B fixtures: synthetic EXIF JPEGs plus the production taken-time runner.

Everything lives under pytest's ``tmp_path``/``%TEMP%``: the photos are written here, the
catalog is the production one, and the extraction runs through the real P2-02 scheduler with
the real executor and the real persistence hook. No fixture reads, moves or deletes a real
photo library, and no model or GPU is involved.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from p2_dataset_fixtures import PY_PROVIDER, python_capability
from PIL import Image

from photo_search.catalog import schema
from photo_search.catalog.db import Catalog
from photo_search.catalog.migrate import migrate_database
from photo_search.catalog.sqlite_io import connect_rw, open_readonly
from photo_search.scheduler import engine as scheduler_engine
from photo_search.taken_time import extract as time_extract
from photo_search.taken_time.service import enqueue_targets, taken_time_targets

REAL_MIGRATIONS_DIR = Path(schema.__file__).resolve().parent / "migrations"
SUPPORTED_SCHEMA_VERSION = int(schema.SUPPORTED_SCHEMA_VERSION)

#: Fixed wall-clock origin so an aged fixture file is always outside the stability window.
WALL_BASE_MS = 1_700_000_000_000
QUIET_AGE_MS = 3_600_000

#: The EXIF tags this fixture writes (the same ones ADR-0011 §4.1/§9 freezes).
EXIF_IFD = 0x8769
TAG_DATETIME_ROOT = 0x0132
TAG_MAKE = 0x010F
TAG_MODEL = 0x0110
TAG_DATETIME_ORIGINAL = 0x9003
TAG_DATETIME_DIGITIZED = 0x9004
TAG_OFFSET_TIME = 0x9010
TAG_OFFSET_TIME_ORIGINAL = 0x9011
TAG_OFFSET_TIME_DIGITIZED = 0x9012
TAG_SUBSEC_TIME_ORIGINAL = 0x9291
TAG_SUBSEC_TIME_DIGITIZED = 0x9292


def stamp(path: Path, *, age_ms: int = QUIET_AGE_MS) -> Path:
    """Give a fixture file an ``mtime`` that is already outside the stability window."""
    moment = (WALL_BASE_MS - int(age_ms)) * 1_000_000
    os.utime(path, ns=(moment, moment))
    return path


def write_exif_jpeg(
    path: Path,
    *,
    size: tuple[int, int] = (48, 36),
    color: tuple[int, int, int] = (40, 60, 90),
    root: str | None = None,
    original: str | None = None,
    digitized: str | None = None,
    offset_original: str | None = None,
    offset_digitized: str | None = None,
    offset_root: str | None = None,
    subsec_original: str | None = None,
    subsec_digitized: str | None = None,
    make: str | None = None,
    model: str | None = None,
    age_ms: int = QUIET_AGE_MS,
) -> Path:
    """Write one real JPEG with exactly the EXIF time evidence the caller asked for."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, color)
    exif = Image.Exif()
    if root is not None:
        exif[TAG_DATETIME_ROOT] = root
    if offset_root is not None:
        exif[TAG_OFFSET_TIME] = offset_root
    if make is not None:
        exif[TAG_MAKE] = make
    if model is not None:
        exif[TAG_MODEL] = model
    sub: dict[int, Any] = {}
    if original is not None:
        sub[TAG_DATETIME_ORIGINAL] = original
    if digitized is not None:
        sub[TAG_DATETIME_DIGITIZED] = digitized
    if offset_original is not None:
        sub[TAG_OFFSET_TIME_ORIGINAL] = offset_original
    if offset_digitized is not None:
        sub[TAG_OFFSET_TIME_DIGITIZED] = offset_digitized
    if subsec_original is not None:
        sub[TAG_SUBSEC_TIME_ORIGINAL] = subsec_original
    if subsec_digitized is not None:
        sub[TAG_SUBSEC_TIME_DIGITIZED] = subsec_digitized
    if sub:
        exif[EXIF_IFD] = sub
    image.save(path, "JPEG", quality=90, exif=exif)
    return stamp(path, age_ms=age_ms)


def write_plain_jpeg(path: Path, *, age_ms: int = QUIET_AGE_MS) -> Path:
    """One JPEG with no EXIF at all: its only available source is ``FILE_MTIME``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (48, 36), (12, 34, 56)).save(path, "JPEG", quality=90)
    return stamp(path, age_ms=age_ms)


def build_scheduler(catalog: Catalog, *, now_ms: int = 1_000) -> scheduler_engine.Scheduler:
    """The real scheduler plus the capability/qualification rows the matrix needs."""
    spec = time_extract.taken_time_processor_spec()
    scheduler = scheduler_engine.Scheduler(catalog, [spec], lease_seconds=60)
    scheduler.record_capability(python_capability(now_ms=now_ms), now_ms=now_ms)
    scheduler.record_qualification(
        processor_id=spec.processor_id,
        spec_revision=spec.spec_revision,
        device="cpu",
        precision=str(spec.supported_precisions[0]),
        provider_name=PY_PROVIDER,
        runtime_package_id=spec.runtime_package_id,
        now_ms=now_ms,
    )
    return scheduler


def enqueue_all(
    catalog: Catalog,
    *,
    asset_ids: Sequence[int] | None = None,
    source_id: int | None = None,
    force: bool = False,
    now_ms: int = 1_000,
) -> dict[str, int]:
    targets = taken_time_targets(
        catalog, asset_ids=asset_ids, source_id=source_id, limit=None
    )
    return enqueue_targets(catalog, targets, now_ms=now_ms, force=force)


def run_extraction(
    catalog: Catalog,
    *,
    asset_ids: Sequence[int] | None = None,
    source_id: int | None = None,
    now_ms: int = 1_000,
    force: bool = False,
    clock: Any = None,
) -> dict[str, Any]:
    """Enqueue and drain the extraction through the production scheduler."""
    scheduler = build_scheduler(catalog, now_ms=now_ms)
    queued = enqueue_all(
        catalog, asset_ids=asset_ids, source_id=source_id, force=force, now_ms=now_ms
    )
    steps = scheduler.run_until_idle(
        time_extract.taken_time_executor(catalog, clock=clock),
        worker_id="p4-time-test",
        now_ms=now_ms,
    )
    return {"enqueued": queued, "steps": steps}


@dataclass(frozen=True)
class TimeRow:
    """One ``asset_metadata`` row as the tests read it back."""

    asset_id: int
    taken_at_raw: str
    taken_at_sort: int | None
    time_source: str
    time_confidence: str
    timezone_offset: int | None
    camera: str | None


def row_for(catalog: Catalog, asset_id: int) -> TimeRow | None:
    row = catalog.read_one(
        "SELECT asset_id, taken_at_raw, taken_at_sort, time_source, time_confidence, "
        "timezone_offset, camera FROM asset_metadata WHERE asset_id = ?",
        (int(asset_id),),
    )
    if row is None:
        return None
    return TimeRow(
        asset_id=int(row["asset_id"]),
        taken_at_raw=str(row["taken_at_raw"]),
        taken_at_sort=None if row["taken_at_sort"] is None else int(row["taken_at_sort"]),
        time_source=str(row["time_source"]),
        time_confidence=str(row["time_confidence"]),
        timezone_offset=(
            None if row["timezone_offset"] is None else int(row["timezone_offset"])
        ),
        camera=None if row["camera"] is None else str(row["camera"]),
    )


def assets_by_basename(catalog: Catalog) -> dict[str, int]:
    """``file name -> asset_id`` for a fixture library (paths stay source-relative)."""
    rows = catalog.read_all(
        "SELECT a.asset_id AS asset_id, l.relative_path AS relative_path FROM assets AS a "
        "JOIN file_locations AS l ON l.asset_id = a.asset_id ORDER BY a.asset_id, l.location_id"
    )
    names: dict[str, int] = {}
    for row in rows:
        key = str(row["relative_path"]).replace("\\", "/").rsplit("/", 1)[-1]
        names[key] = int(row["asset_id"])
    return names


def filter_revision(catalog: Catalog) -> int:
    row = catalog.read_one("SELECT revision FROM catalog_filter_revision WHERE revision_id = 1")
    return int(row["revision"])


def filter_causes(catalog: Catalog) -> list[str]:
    return [
        str(row["cause"])
        for row in catalog.read_all(
            "SELECT cause FROM catalog_filter_revision_events ORDER BY revision"
        )
    ]


# ------------------------------------------------------------- migration-level fixtures
def migrations_upto(tmp_path: Path, version: int) -> Path:
    """A copy of the real migration scripts 0001..``version`` (for pinned-version builds)."""
    directory = tmp_path / (
        f"migrations-{version}-{hashlib.sha256(str(version).encode()).hexdigest()[:8]}"
    )
    directory.mkdir(parents=True, exist_ok=True)
    for source in sorted(REAL_MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        if int(source.name.split("_", 1)[0]) <= int(version):
            shutil.copy2(source, directory / source.name)
    return directory


def v14_data_root(tmp_path: Path) -> Path:
    return Path(tmp_path) / "PhotoSearchData"


def v14_db_path(tmp_path: Path) -> Path:
    return v14_data_root(tmp_path) / "catalog" / "catalog.db"


def build_v14_catalog(tmp_path: Path, monkeypatch: Any) -> Path:
    """A real v14 catalog with sources, assets, positions, a revision and an operation log."""
    path = v14_db_path(tmp_path)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, 14))
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 14)
    migrate_database(path, data_root=v14_data_root(tmp_path))
    connection = connect_rw(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO source_roots(display_name, root_path, volume_hint, status) "
            "VALUES ('照片 库', 'C:\\photos 中文', 'C:', 'ONLINE')"
        )
        for index, name in enumerate(("合影 一.jpg", "海边日落.jpg"), start=1):
            connection.execute(
                "INSERT INTO assets(sha256, byte_size, created_at, state) VALUES (?, 4096, ?, "
                "'ACTIVE')",
                (str(index) * 64, 1_000 + index),
            )
            connection.execute(
                "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, "
                "size, mtime_ns, last_seen_scan_id, availability) "
                "VALUES (1, ?, ?, ?, 4096, ?, NULL, 'PRESENT')",
                (f"2024 年/{name}", f"k{index}", index, 1_600_000_000_000_000_000 + index),
            )
        connection.execute(
            "UPDATE catalog_filter_revision SET revision = 7, committed_at = 20, "
            "cause = 'PLACE_EFFECTIVE_ASSIGNMENT_CHANGED' WHERE revision_id = 1"
        )
        connection.execute(
            "INSERT INTO catalog_filter_revision_events(revision, cause, changed_count, "
            "all_assets, asset_ids_json, committed_at) VALUES (7, "
            "'PLACE_EFFECTIVE_ASSIGNMENT_CHANGED', 1, 0, '[1]', 20)"
        )
        connection.execute(
            "INSERT INTO persons(person_id, name, name_key, revision, created_at, updated_at) "
            "VALUES (3, '爸爸', 'nfc1:爸爸', 4, 1, 1)"
        )
        connection.execute(
            "INSERT INTO person_operations(operation_id, request_id, payload_sha256, type, "
            "source_person_ids_json, target_person_id, state, operation_revision, "
            "catalog_filter_revision, created_at, committed_at) "
            "VALUES ('op-v14', 'req-v14', ?, 'RENAME', '[]', 3, 'COMMITTED', 1, 7, 10, 10)",
            ("c" * 64,),
        )
        connection.execute(
            "INSERT INTO person_operation_items(operation_id, item_index, entity_type, "
            "entity_id, entity_key, before_value_json, after_value_json, after_revision) "
            "VALUES ('op-v14', 0, 'PERSON_BUNDLE', 3, NULL, '{}', '{\"name\":\"爸爸\"}', 4)"
        )
        connection.execute("COMMIT")
    finally:
        connection.close()
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED_SCHEMA_VERSION)
    return path


def snapshot_tables(path: Path, tables: Sequence[str]) -> dict[str, list[tuple]]:
    """Every row of the named tables, read through a read-only connection."""
    connection = open_readonly(path)
    try:
        return {
            table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
            for table in tables
        }
    finally:
        connection.close()


__all__ = [
    "REAL_MIGRATIONS_DIR",
    "QUIET_AGE_MS",
    "SUPPORTED_SCHEMA_VERSION",
    "WALL_BASE_MS",
    "TimeRow",
    "assets_by_basename",
    "build_v14_catalog",
    "build_scheduler",
    "enqueue_all",
    "filter_causes",
    "filter_revision",
    "migrations_upto",
    "row_for",
    "run_extraction",
    "snapshot_tables",
    "stamp",
    "v14_data_root",
    "v14_db_path",
    "write_exif_jpeg",
    "write_plain_jpeg",
]
