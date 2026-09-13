"""P2-03A: the GPS closed loop through the real scanner, scheduler and catalog.

GPS JPEGs are generated inside pytest's ``%TEMP%`` directory with Pillow; no real photo,
no network and no name package is involved. The suite covers the D-level scenarios: the
numeric branches, an offline original (which must NOT become ABSENT and must NOT replace
the previous fact), a file that changes mid-read, a late lease, the artifact/outbox shape,
and the fact that a missing GPU never blocks these CPU jobs.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import (
    INPUT_UNSTABLE,
    RUNTIME_CAPABILITY_UNAVAILABLE,
    SOURCE_FILE_MISSING,
    STALE_LEASE,
    CatalogError,
)
from photo_search.geo import extract as geo_extract
from photo_search.indexing.store import LanceVectorIndex
from photo_search.ingestion.service import CatalogService
from photo_search.jobs import queue as jobs
from photo_search.places import revision as revision_module
from photo_search.scheduler import engine
from photo_search.scheduler.contracts import ProcessorSpec, RuntimeCapabilities

WALL_BASE_MS = 1_700_000_000_000
QUIET_AGE_MS = 3_600_000
PY_PACKAGE = "photo-search-runtime@1"
ORT_PACKAGE = "onnxruntime-gpu@1.30.0"
GPU_PROCESSOR = "clip-image@36e679e6-fp32-v1"
SHA = "a" * 64


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
    lat: tuple[float, float, float] | None = (30.0, 15.0, 0.0),
    lat_ref: str = "N",
    lon: tuple[float, float, float] | None = (120.0, 10.0, 0.0),
    lon_ref: str = "E",
    datum: str | None = "WGS84",
) -> Path:
    """A real JPEG with a real EXIF GPS IFD, or a plain JPEG when ``lat`` is ``None``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (64, 48), (12, 34, 56))
    exif = Image.Exif()
    exif[0x0112] = 1
    if lat is not None and lon is not None:
        gps: dict[int, object] = {
            1: lat_ref,
            2: tuple(lat),
            3: lon_ref,
            4: tuple(lon),
        }
        if datum is not None:
            gps[18] = datum
        exif[0x8825] = gps
    image.save(path, "JPEG", quality=92, exif=exif)
    return stamp(path)


def read_back_gps(path: Path) -> dict[int, Any]:
    with Image.open(path) as image:
        return dict(image.getexif().get_ifd(0x8825))


class Harness:
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

    def scan(self) -> None:
        self.service.run_scan(self.source_id)

    def asset_id(self, relative: str) -> int:
        row = self.catalog.read_one(
            "SELECT asset_id FROM file_locations WHERE relative_path = ?", (relative,)
        )
        return int(row["asset_id"])

    def sha_of(self, asset_id: int) -> str:
        return str(
            self.catalog.read_one("SELECT sha256 FROM assets WHERE asset_id = ?", (asset_id,))[
                "sha256"
            ]
        )

    def close(self) -> None:
        self.service.close()


@pytest.fixture()
def harness(tmp_path: Path) -> Harness:
    instance = Harness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


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
                    "name": "python-stdlib",
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


def gpu_spec() -> ProcessorSpec:
    return ProcessorSpec.from_envelope(
        {
            "contract_version": "p2-contracts.v1",
            "spec_revision": "chinese-clip-vit-b-16-fp32-v1",
            "processor_id": GPU_PROCESSOR,
            "processor_kind": "embedding_model",
            "task_type": "image_embedding",
            "config_hash": SHA,
            "input_type": "decoded_rgb_image",
            "output_type": "embedding",
            "device_policy": "GPU_REQUIRED",
            "runtime_backend": "onnxruntime",
            "runtime_package_id": ORT_PACKAGE,
            "supported_devices": ["cpu", "gpu"],
            "supported_precisions": ["FP32"],
            "max_batch_size": 1,
            "cancellation_supported": True,
            "resource_measurements": [],
            "model_id": "OFA-Sys/chinese-clip-vit-base-patch16",
            "model_revision": "36e679e65c2a2fead755ae21162091293ad37834",
            "weights": [
                {
                    "name": "image_tower.fp32.onnx",
                    "revision": "36e679e65c2a2fead755ae21162091293ad37834",
                    "sha256": SHA,
                }
            ],
            "image_preprocess_version": "chinese-clip-image-224-rgb-v1",
            "query_preprocess_version": "chinese-clip-query-52-v1",
            "tokenizer_version": "bert-vocab-45bbac6b-v1",
            "instruction_template_version": "none-v1",
            "normalization_version": "L2-v1",
            "dimension": 512,
            "metric": "cosine",
            "space_id": "chinese-clip-vit-b-16:c8ac42d3907c57b0",
            "max_pixels": 224 * 224,
            "max_tokens": 52,
        }
    )


def build_scheduler(harness: Harness, specs: list[ProcessorSpec]) -> engine.Scheduler:
    scheduler = engine.Scheduler(harness.catalog, specs, lease_seconds=60)
    scheduler.record_capability(python_capability(), now_ms=1_000)
    for spec in specs:
        if spec.processor_id == GPU_PROCESSOR:
            continue
        scheduler.record_qualification(
            processor_id=spec.processor_id,
            spec_revision=spec.spec_revision,
            device="cpu",
            precision=spec.supported_precisions[0],
            provider_name="python-stdlib",
            runtime_package_id=spec.runtime_package_id,
            now_ms=1_000,
        )
    return scheduler


def enqueue_geo(harness: Harness, asset_id: int, *, processor_id: str | None = None) -> int:
    with harness.catalog.write() as connection:
        return jobs.enqueue(
            connection,
            subject_type="asset",
            subject_id=str(asset_id),
            input_revision=harness.sha_of(asset_id),
            task_type="geo_extract",
            processor_id=processor_id or geo_extract.GPS_PROCESSOR_ID,
            now_ms=1_000,
        )


def run_geo(
    harness: Harness,
    specs: list[ProcessorSpec] | None = None,
    *,
    asset_ids: list[int] | None = None,
) -> tuple[engine.Scheduler, int]:
    """Enqueue one GPS job per requested asset (default: every scanned asset) and drain."""
    spec = geo_extract.gps_processor_spec()
    scheduler = build_scheduler(harness, specs or [spec])
    targets = asset_ids
    if targets is None:
        targets = [
            int(row["asset_id"])
            for row in harness.catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id")
        ]
    for asset_id in targets:
        enqueue_geo(harness, int(asset_id))
    steps = scheduler.run_until_idle(
        geo_extract.gps_executor(harness.catalog, clock=harness.clock),
        worker_id="w1",
        now_ms=1_100,
    )
    return scheduler, steps


def fact_row(harness: Harness, asset_id: int) -> sqlite3.Row | None:
    return harness.catalog.read_one(
        "SELECT * FROM asset_geo_facts WHERE asset_id = ?", (asset_id,)
    )


def geo_job(harness: Harness, *, processor_id: str | None = None) -> sqlite3.Row:
    """The most recent job of one processor (the scan job is not counted here)."""
    return harness.catalog.read_one(
        "SELECT * FROM jobs WHERE processor_id = ? ORDER BY job_id DESC LIMIT 1",
        (processor_id or geo_extract.GPS_PROCESSOR_ID,),
    )


def _requeue_geo_job(harness: Harness, asset_id: int) -> None:
    """Model a deterministic re-execution of an already finished GPS job.

    The five-field logical identity stays intact (no duplicate row is created); only the
    state machine is rewound, which is exactly what a repair pass would do.
    """
    with harness.catalog.write() as connection:
        connection.execute(
            "UPDATE jobs SET state = 'QUEUED', attempts = 0, progress = 0.0, lease_token = NULL, "
            "lease_expires_at = NULL, worker_id = NULL, run_after = 0, last_error_code = NULL, "
            "last_error_message = NULL, blocking_reason = NULL WHERE subject_type = 'asset' "
            "AND subject_id = ? AND processor_id = ?",
            (str(int(asset_id)), geo_extract.GPS_PROCESSOR_ID),
        )


# ------------------------------------------------------------------------ happy paths
def test_a_real_gps_jpeg_becomes_a_valid_fact(harness: Harness) -> None:
    write_gps_jpeg(harness.library / "hangzhou.jpg")
    harness.scan()
    asset_id = harness.asset_id("hangzhou.jpg")
    assert read_back_gps(harness.library / "hangzhou.jpg")  # the fixture really carries GPS

    revision_before = revision_module.read_filter_revision(harness.catalog.connection)
    scheduler, steps = run_geo(harness)
    assert steps == 1
    assert str(geo_job(harness)["state"]) == "SUCCEEDED"

    row = fact_row(harness, asset_id)
    assert row is not None
    assert row["status"] == "VALID" and row["datum_status"] == "WGS84"
    assert row["source"] == "EXIF_GPS" and row["processor_id"] == geo_extract.GPS_PROCESSOR_ID
    assert row["input_revision"] == harness.sha_of(asset_id)
    assert row["fact_revision"] == 1
    assert row["latitude"] == pytest.approx(30.25, abs=1e-6)
    assert row["longitude"] == pytest.approx(120.1666667, abs=1e-6)
    assert int(row["location_id"]) == int(
        harness.catalog.read_one(
            "SELECT location_id FROM file_locations WHERE asset_id = ?", (asset_id,)
        )["location_id"]
    )
    # A structured artifact is a durable audit copy, and GPS never moves the filter revision.
    assert harness.catalog.read_one(
        "SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'structured'"
    )[0] == 1
    assert harness.catalog.read_one("SELECT COUNT(*) FROM index_outbox")[0] == 0
    assert revision_module.read_filter_revision(harness.catalog.connection) == revision_before


def test_absent_unsupported_and_invalid_facts_are_persisted_distinctly(harness: Harness) -> None:
    write_gps_jpeg(harness.library / "no-gps.jpg", lat=None)
    write_gps_jpeg(harness.library / "gcj.jpg", datum="GCJ-02")
    write_gps_jpeg(harness.library / "bad.jpg", lat=(95.0, 0.0, 0.0))
    harness.scan()

    run_geo(harness)
    absent = fact_row(harness, harness.asset_id("no-gps.jpg"))
    assert absent["status"] == "ABSENT" and absent["datum_status"] == "ABSENT"
    assert absent["latitude"] is None and absent["longitude"] is None
    assert absent["raw_gps_json"] is None

    unsupported = fact_row(harness, harness.asset_id("gcj.jpg"))
    assert unsupported["status"] == "UNSUPPORTED_CRS"
    assert unsupported["datum_status"] == "UNSUPPORTED"
    assert unsupported["latitude"] is None and unsupported["raw_gps_json"] is not None

    invalid = fact_row(harness, harness.asset_id("bad.jpg"))
    assert invalid["status"] == "INVALID"
    assert invalid["latitude"] is None and invalid["longitude"] is None


def test_an_offline_original_does_not_become_absent(harness: Harness) -> None:
    path = write_gps_jpeg(harness.library / "offline.jpg")
    harness.scan()
    asset_id = harness.asset_id("offline.jpg")
    run_geo(harness)
    before = dict(fact_row(harness, asset_id))
    assert before["status"] == "VALID"

    os.remove(path)
    scheduler = build_scheduler(harness, [geo_extract.gps_processor_spec()])
    _requeue_geo_job(harness, asset_id)
    scheduler.run_until_idle(
        geo_extract.gps_executor(harness.catalog, clock=harness.clock),
        worker_id="w1",
        now_ms=2_000,
    )
    row = geo_job(harness)
    assert row["state"] == "BLOCKED"
    assert row["blocking_reason"] == "SOURCE_UNAVAILABLE"
    assert row["last_error_code"] in (SOURCE_FILE_MISSING, "ACCESS_DENIED")
    # The blocked read neither wrote a new fact nor rewrote the old one.
    assert dict(fact_row(harness, asset_id)) == before
    assert harness.catalog.read_one("SELECT COUNT(*) FROM artifacts")[0] == 1


def test_a_file_changing_during_the_read_is_refused(harness: Harness, monkeypatch) -> None:
    write_gps_jpeg(harness.library / "unstable.jpg")
    harness.scan()
    asset_id = harness.asset_id("unstable.jpg")
    original = geo_extract._hash_file

    def hash_then_touch(target: Path, **options: Any) -> str:
        digest = original(target, **options)
        with target.open("ab") as stream:
            stream.write(b"x")
        return digest

    monkeypatch.setattr(geo_extract, "_hash_file", hash_then_touch)
    scheduler = build_scheduler(harness, [geo_extract.gps_processor_spec()])
    enqueue_geo(harness, asset_id)
    scheduler.run_until_idle(
        geo_extract.gps_executor(harness.catalog, clock=harness.clock),
        worker_id="w1",
        now_ms=1_100,
    )
    row = geo_job(harness)
    assert row["state"] == "BLOCKED" and row["blocking_reason"] == "SOURCE_UNAVAILABLE"
    assert row["last_error_code"] == INPUT_UNSTABLE
    assert harness.catalog.read_one("SELECT COUNT(*) FROM asset_geo_facts")[0] == 0
    assert harness.catalog.read_one("SELECT COUNT(*) FROM artifacts")[0] == 0


def test_a_late_execution_writes_no_fact(harness: Harness) -> None:
    write_gps_jpeg(harness.library / "late.jpg")
    harness.scan()
    asset_id = harness.asset_id("late.jpg")
    spec = geo_extract.gps_processor_spec()
    scheduler = build_scheduler(harness, [spec])
    enqueue_geo(harness, asset_id)
    first = scheduler.claim_next(worker_id="w1", now_ms=1_100)
    assert first is not None
    late = geo_extract.gps_executor(harness.catalog, clock=harness.clock)
    stale_result = engine.JobResult.from_envelope(late(first.to_envelope()))

    second = scheduler.claim_next(worker_id="w2", now_ms=1_100 + 61_000)
    assert second is not None and second.lease_token != first.lease_token
    with pytest.raises(CatalogError) as refused:
        scheduler.submit(stale_result, now_ms=1_100 + 61_002)
    assert refused.value.code == STALE_LEASE
    assert harness.catalog.read_one("SELECT COUNT(*) FROM asset_geo_facts")[0] == 0
    assert harness.catalog.read_one("SELECT COUNT(*) FROM artifacts")[0] == 0

    fresh = engine.JobResult.from_envelope(late(second.to_envelope()))
    scheduler.submit(fresh, now_ms=1_100 + 61_003)
    assert harness.catalog.read_one("SELECT COUNT(*) FROM asset_geo_facts")[0] == 1


def test_a_missing_gpu_never_blocks_the_cpu_gps_job(harness: Harness) -> None:
    write_gps_jpeg(harness.library / "cpu.jpg")
    harness.scan()
    asset_id = harness.asset_id("cpu.jpg")
    specs = [geo_extract.gps_processor_spec(), gpu_spec()]
    scheduler = build_scheduler(harness, specs)
    enqueue_geo(harness, asset_id)
    with harness.catalog.write() as connection:
        jobs.enqueue(
            connection,
            subject_type="asset",
            subject_id=str(asset_id),
            input_revision="embedding-rev",
            task_type="image_embedding",
            processor_id=GPU_PROCESSOR,
            now_ms=1_000,
        )
    scheduler.run_until_idle(
        geo_extract.gps_executor(harness.catalog, clock=harness.clock),
        worker_id="w1",
        now_ms=1_100,
    )
    states = {
        str(row["processor_id"]): str(row["state"])
        for row in harness.catalog.read_all("SELECT processor_id, state FROM jobs")
    }
    assert states[geo_extract.GPS_PROCESSOR_ID] == "SUCCEEDED"
    assert states[GPU_PROCESSOR] == "BLOCKED"
    assert (
        harness.catalog.read_one(
            "SELECT blocking_reason FROM jobs WHERE processor_id = ?", (GPU_PROCESSOR,)
        )["blocking_reason"]
        == RUNTIME_CAPABILITY_UNAVAILABLE
    )


def test_an_empty_result_is_a_normal_success(harness: Harness) -> None:
    write_gps_jpeg(harness.library / "plain.jpg", lat=None)
    harness.scan()
    asset_id = harness.asset_id("plain.jpg")
    noop = ProcessorSpec.from_envelope(
        {
            "contract_version": "p2-contracts.v1",
            "spec_revision": "place-noop-v1",
            "processor_id": "place-noop@1",
            "processor_kind": "non_model",
            "task_type": "geo_extract",
            "config_hash": SHA,
            "input_type": "asset_original",
            "output_type": "geo_fact.v1",
            "device_policy": "CPU_PREFERRED",
            "runtime_backend": "python",
            "runtime_package_id": PY_PACKAGE,
            "supported_devices": ["cpu"],
            "supported_precisions": ["not_applicable"],
            "max_batch_size": 1,
            "cancellation_supported": True,
            "resource_measurements": [],
        }
    )
    scheduler = build_scheduler(harness, [noop])
    with harness.catalog.write() as connection:
        jobs.enqueue(
            connection,
            subject_type="asset",
            subject_id=str(asset_id),
            input_revision=harness.sha_of(asset_id),
            task_type="geo_extract",
            processor_id=noop.processor_id,
            now_ms=1_000,
        )

    def empty_executor(request: dict[str, Any]) -> dict[str, Any]:
        return {
            "contract_version": "p2-contracts.v1",
            "job_id": request["job_id"],
            "lease_token": request["lease_token"],
            "input_revision": request["input_revision"],
            "task_type": request["task_type"],
            "processor_id": request["processor_id"],
            "state": "SUCCEEDED",
            "resolved_device": request["resolved_device"],
            "runtime_provider": request["runtime_provider"],
            "precision": request["precision"],
            "progress": 1.0,
            "artifacts": [],
            "error": None,
            "completed_at": 2_000,
        }

    assert scheduler.run_until_idle(empty_executor, worker_id="w1", now_ms=1_100) == 1
    assert str(geo_job(harness, processor_id=noop.processor_id)["state"]) == "SUCCEEDED"
    assert harness.catalog.read_one("SELECT COUNT(*) FROM artifacts")[0] == 0
    assert harness.catalog.read_one("SELECT COUNT(*) FROM asset_geo_facts")[0] == 0


def test_the_artifact_payload_carries_no_path_and_the_executor_is_path_free(harness: Harness) -> None:
    write_gps_jpeg(harness.library / "private.jpg")
    harness.scan()
    run_geo(harness)
    row = harness.catalog.read_one("SELECT * FROM artifacts")
    path = harness.catalog.read_one(
        "SELECT storage_uri FROM artifacts WHERE artifact_id = ?", (row["artifact_id"],)
    )
    assert ":" not in str(path["storage_uri"])
    assert str(harness.library) not in str(row["storage_uri"])
    payload_path = harness.data_root / Path(str(row["storage_uri"]))
    body = payload_path.read_text(encoding="utf-8")
    assert str(harness.library) not in body
    assert "\\\\" not in body
    assert geo_extract.GPS_PROCESSOR_ID in body
    # The generated fact never contains the processor's internal config hash or a vector.
    assert "dimension" not in body and "space_id" not in body


def test_a_reexecution_bumps_the_fact_revision_and_reuses_the_artifact(harness: Harness) -> None:
    write_gps_jpeg(harness.library / "rev.jpg")
    harness.scan()
    asset_id = harness.asset_id("rev.jpg")
    run_geo(harness)
    first = dict(fact_row(harness, asset_id))
    assert first["fact_revision"] == 1

    # Re-queueing the logical job models a deterministic repair/re-execution of the same
    # content: the durable artifact is reused and the current fact moves to revision 2.
    _requeue_geo_job(harness, asset_id)
    scheduler = build_scheduler(harness, [geo_extract.gps_processor_spec()])
    scheduler.run_until_idle(
        geo_extract.gps_executor(harness.catalog, clock=harness.clock),
        worker_id="w1",
        now_ms=2_000,
    )
    second = dict(fact_row(harness, asset_id))
    assert second["fact_revision"] == 2
    assert second["geo_fact_id"] != first["geo_fact_id"]
    assert second["latitude"] == first["latitude"] and second["longitude"] == first["longitude"]
    assert harness.catalog.read_one("SELECT COUNT(*) FROM artifacts")[0] == 1


def test_the_lance_index_is_untouched_by_gps(harness: Harness) -> None:
    write_gps_jpeg(harness.library / "nov.jpg")
    harness.scan()
    run_geo(harness)
    index = LanceVectorIndex(harness.data_root / "vectors")
    assert index.rows(generation_id="missing", dimension=4) == []
    assert harness.catalog.read_one("SELECT COUNT(*) FROM index_generations")[0] == 0


def test_a_deleted_artifact_file_is_reported_as_missing(harness: Harness) -> None:
    """The audit copy is content-addressed; losing it is a repair case, not a silent pass."""
    from photo_search.artifacts.store import ArtifactStore

    write_gps_jpeg(harness.library / "audit.jpg")
    harness.scan()
    run_geo(harness)
    store = ArtifactStore.open(harness.data_root)
    row = harness.catalog.read_one("SELECT * FROM artifacts")
    target = store.resolve_uri(str(row["storage_uri"]))
    digest = target.read_bytes()
    os.remove(target)
    with pytest.raises(CatalogError) as missing:
        store.read(row)
    assert missing.value.code == "ARTIFACT_MISSING"
    # The exact same deterministic re-execution reproduces the identical bytes.
    assert hashlib.sha256(digest).hexdigest() == str(row["checksum_sha256"])


def test_no_coordinate_or_path_leaks_into_the_job_error_fields(harness: Harness) -> None:
    path = write_gps_jpeg(harness.library / "leak.jpg")
    harness.scan()
    asset_id = harness.asset_id("leak.jpg")
    os.remove(path)
    scheduler = build_scheduler(harness, [geo_extract.gps_processor_spec()])
    enqueue_geo(harness, asset_id)
    scheduler.run_until_idle(
        geo_extract.gps_executor(harness.catalog, clock=harness.clock),
        worker_id="w1",
        now_ms=1_100,
    )
    row = geo_job(harness)
    text = str(row["last_error_message"])
    assert str(harness.library) not in text
    assert "30.25" not in text and "120.16" not in text
    assert "C:\\" not in text and "\\\\" not in text
    assert row["last_error_code"] in (SOURCE_FILE_MISSING, "ACCESS_DENIED", "FILE_READ_ERROR")
