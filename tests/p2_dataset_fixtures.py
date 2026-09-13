"""Shared ``%TEMP%`` fixtures for the P2-03B suites.

Every fixture is a synthetic JPEG or a tiny synthetic city package built by the same
deterministic builder the explicit local tool wraps. Nothing here touches a real photo, a
real place dataset or the network.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from PIL import Image

from photo_search.catalog.db import Catalog
from photo_search.geo import dataset as dataset_module
from photo_search.geo import extract as geo_extract
from photo_search.geo import resolver as resolver_module
from photo_search.ingestion.service import CatalogService
from photo_search.jobs import queue as jobs
from photo_search.places import datasets as datasets_module
from photo_search.places.contracts import canonical_json
from photo_search.scheduler import engine
from photo_search.scheduler.contracts import RuntimeCapabilities

WALL_BASE_MS = 1_700_000_000_000
QUIET_AGE_MS = 3_600_000
PY_PACKAGE = "photo-search-runtime@1"
PY_PROVIDER = "python-stdlib"

#: The synthetic city points every suite resolves against. Distances are chosen so the
#: ranking, the "sea" empty result and the multi-candidate case are all reproducible.
MINI_PLACES: tuple[dict[str, Any], ...] = (
    {
        "dataset_place_id": "country-cn",
        "kind": "country",
        "display_name": "中国",
        "country_code": "CN",
        "latitude": 35.0,
        "longitude": 105.0,
    },
    {
        "dataset_place_id": "city-hangzhou",
        "kind": "city",
        "display_name": "杭州",
        "country_code": "CN",
        "latitude": 30.2741,
        "longitude": 120.1551,
        "population": 11936010,
        "parent_dataset_place_id": "country-cn",
    },
    {
        "dataset_place_id": "city-shanghai",
        "kind": "city",
        "display_name": "上海",
        "country_code": "CN",
        "latitude": 31.2304,
        "longitude": 121.4737,
        "population": 24870895,
        "parent_dataset_place_id": "country-cn",
    },
    {
        "dataset_place_id": "city-suzhou",
        "kind": "city",
        "display_name": "苏州",
        "country_code": "CN",
        "latitude": 31.2989,
        "longitude": 120.5853,
        "population": 12748262,
        "parent_dataset_place_id": "country-cn",
    },
    {
        "dataset_place_id": "city-lhasa",
        "kind": "city",
        "display_name": "拉萨",
        "country_code": "CN",
        "latitude": 29.6520,
        "longitude": 91.1721,
        "population": 867891,
        "parent_dataset_place_id": "country-cn",
    },
)

MINI_ALIASES: tuple[dict[str, Any], ...] = (
    {"dataset_place_id": "city-hangzhou", "text": "Hangzhou", "language": "en"},
    {"dataset_place_id": "city-shanghai", "text": "Shanghai", "language": "en"},
)

#: A position in the East China Sea: no city inside the default 200 km radius.
SEA_POSITION = (29.0, 125.5)
#: A position right at Hangzhou.
HANGZHOU_POSITION = (30.2741, 120.1551)
#: A position between Hangzhou and Suzhou, so both are ranked.
BETWEEN_POSITION = (30.9, 120.4)


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


def write_gps_jpeg(
    path: Path,
    *,
    position: tuple[float, float] | None = HANGZHOU_POSITION,
    datum: str | None = "WGS84",
    color: tuple[int, int, int] = (12, 34, 56),
) -> Path:
    """A real JPEG with a real EXIF GPS IFD (or a plain JPEG when ``position`` is ``None``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (64, 48), color)
    exif = Image.Exif()
    exif[0x0112] = 1
    if position is not None:
        latitude, longitude = position
        gps: dict[int, object] = {
            1: "N" if latitude >= 0 else "S",
            2: degrees_dms(abs(latitude)),
            3: "E" if longitude >= 0 else "W",
            4: degrees_dms(abs(longitude)),
        }
        if datum is not None:
            gps[18] = datum
        exif[0x8825] = gps
    image.save(path, "JPEG", quality=92, exif=exif)
    return stamp(path)


def degrees_dms(value: float) -> tuple[float, float, float]:
    degrees = int(value)
    minutes_full = (value - degrees) * 60.0
    minutes = int(minutes_full)
    seconds = (minutes_full - minutes) * 60.0
    return (float(degrees), float(minutes), round(seconds, 6))


def mini_spec(
    *,
    dataset_id: str = "geo-mini-cn-1",
    package_version: str = "1",
    places: tuple[dict[str, Any], ...] = MINI_PLACES,
    aliases: tuple[dict[str, Any], ...] = MINI_ALIASES,
    compatible: tuple[int, int] = (1, 99),
    previous_dataset_id: str | None = None,
    license_text: str = "Synthetic test fixture. Not a real dataset.\n",
    record_count: int | None = None,
) -> dict[str, Any]:
    parameters = {
        "source_files": ["mini-cities.synthetic.txt"],
        "minimum_population": 0,
        "languages": ["zh"],
        "include_aliases": True,
        "sort_order": "population-desc",
    }
    coverage: dict[str, Any] = {
        "country_codes": ["CN"],
        "bounds_wgs84": {"south": 18.0, "west": 73.0, "north": 54.0, "east": 135.0},
        "languages": ["zh"],
        "alias_languages": ["en"],
        "record_count": len(places) if record_count is None else int(record_count),
    }
    return {
        "package_id": f"{dataset_id}-package",
        "package_version": package_version,
        "dataset_id": dataset_id,
        "source": {
            "provider": "synthetic-mini",
            "source_url": "https://example.invalid/mini-cities",
            "snapshot": "2026-09-13",
            "license_id": "TEST-FIXTURE",
            "license_url": "https://example.invalid/license",
            "attribution": "Synthetic P2-03B fixture",
        },
        "build": {
            "builder_id": "photo-search-test-builder",
            "builder_version": "1",
            "generated_at": WALL_BASE_MS,
            "parameters_sha256": hashlib.sha256(
                canonical_json(parameters).encode("utf-8")
            ).hexdigest(),
            "parameters": parameters,
        },
        "coverage": coverage,
        "compatible_catalog_schema": {"minimum": int(compatible[0]), "maximum": int(compatible[1])},
        "state": "STAGED",
        "previous_dataset_id": previous_dataset_id,
        "places": [dict(place) for place in places],
        "aliases": [dict(alias) for alias in aliases],
        "license_text": license_text,
        "index": None,
    }


def build_package(destination: Path, *, spec: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    """Build a package directory and return its manifest."""
    return dataset_module.build_package(
        spec if spec is not None else mini_spec(**overrides), destination
    )


class Harness:
    """A registered temp library, its catalog and the P2-03B helpers on top of it."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = Path(tmp_path)
        self.data_root = self.tmp_path / "PhotoSearchData"
        self.library = self.tmp_path / "library"
        self.library.mkdir(parents=True, exist_ok=True)
        self.package_sources = self.tmp_path / "package-sources"
        self.package_sources.mkdir(parents=True, exist_ok=True)
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

    def scan(self) -> None:
        self.service.run_scan(self.source_id)

    def asset_id(self, relative: str) -> int:
        row = self.catalog.read_one(
            "SELECT asset_id FROM file_locations WHERE relative_path = ?", (relative,)
        )
        return int(row["asset_id"])

    def sha_of(self, asset_id: int) -> str:
        row = self.catalog.read_one(
            "SELECT sha256 FROM assets WHERE asset_id = ?", (int(asset_id),)
        )
        return str(row["sha256"])

    def close(self) -> None:
        self.service.close()

    # ------------------------------------------------------------------ dataset helpers
    def source_package(self, name: str, *, spec: dict[str, Any] | None = None, **overrides: Any) -> Path:
        directory = self.package_sources / name
        build_package(directory, spec=spec, **overrides)
        return directory

    def install(self, name: str = "geo-mini-cn-1", *, activate: bool = True, **overrides: Any) -> dict[str, Any]:
        """Stage, validate and (by default) activate one synthetic package."""
        source = self.source_package(name, **overrides)
        staged = datasets_module.stage_package(self.catalog, source)
        validated = datasets_module.validate_dataset(self.catalog, str(staged["dataset_id"]))
        if not activate:
            return validated
        return datasets_module.activate_dataset(self.catalog, str(validated["dataset_id"]))

    # ------------------------------------------------------------------- job helpers
    def scheduler(self, specs: list[Any] | None = None) -> engine.Scheduler:
        scenario_specs = specs or [
            geo_extract.gps_processor_spec(),
            resolver_module.resolver_processor_spec(),
        ]
        scheduler = engine.Scheduler(self.catalog, scenario_specs, lease_seconds=60)
        scheduler.record_capability(python_capability(), now_ms=1_000)
        for spec in scenario_specs:
            scheduler.record_qualification(
                processor_id=spec.processor_id,
                spec_revision=spec.spec_revision,
                device="cpu",
                precision=spec.supported_precisions[0],
                provider_name=PY_PROVIDER,
                runtime_package_id=spec.runtime_package_id,
                now_ms=1_000,
            )
        return scheduler

    def enqueue(self, asset_id: int, processor_id: str, task_type: str) -> int:
        with self.catalog.write() as connection:
            return jobs.enqueue(
                connection,
                subject_type="asset",
                subject_id=str(int(asset_id)),
                input_revision=self.sha_of(int(asset_id)),
                task_type=task_type,
                processor_id=processor_id,
                now_ms=1_000,
            )

    def run_gps(self, asset_ids: list[int]) -> int:
        scheduler = self.scheduler()
        for asset_id in asset_ids:
            self.enqueue(int(asset_id), geo_extract.GPS_PROCESSOR_ID, geo_extract.GPS_TASK_TYPE)
        return scheduler.run_until_idle(
            geo_extract.gps_executor(self.catalog, clock=self.clock),
            worker_id="w1",
            now_ms=1_100,
        )

    def run_resolver(self, asset_ids: list[int], *, now_ms: int = 1_200) -> int:
        scheduler = self.scheduler()
        for asset_id in asset_ids:
            self.enqueue(
                int(asset_id),
                resolver_module.RESOLVER_PROCESSOR_ID,
                resolver_module.RESOLVER_TASK_TYPE,
            )
        return scheduler.run_until_idle(
            resolver_module.resolver_executor(self.catalog, data_root=self.data_root, clock=self.clock),
            worker_id="w1",
            now_ms=int(now_ms),
        )


def python_capability(*, now_ms: int = 1_000) -> RuntimeCapabilities:
    return RuntimeCapabilities.seal(
        {
            "runtime_package_id": PY_PACKAGE,
            "runtime_backend": "python",
            "runtime_package_name": "photo-search-runtime",
            "runtime_package_version": "1",
            "python_version": "3.12.10",
            "platform": "windows-x86_64",
            "driver_version": None,
            "providers": [
                {
                    "name": PY_PROVIDER,
                    "device": "cpu",
                    "precisions": ["not_applicable"],
                    "available": True,
                    "health": "healthy",
                    "health_checked_at": now_ms,
                    "evidence_id": None,
                }
            ],
            "health_status": "healthy",
            "health_checked_at": now_ms,
            "measured_at": now_ms,
            "evidence_ids": [],
            "failure_code": None,
        }
    )
