"""Deterministic P2-05 fixtures: a scanned temp library, a fake adapter, real generations.

Everything here lives under pytest's ``tmp_path`` (``%TEMP%``); no fixture reads, moves or
deletes a real photo library, and no model or GPU is involved. The photos go through the
production scanner, the derived text comes from the production triggers and re-derivation,
and the vectors are written by the production artifact/outbox/index path - only the
*encoding* is a deterministic stand-in for the ADR-0003 adapter.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from photo_search.ingestion.service import CatalogService
from photo_search.jobs import queue as jobs
from photo_search.places import assignments, directory, revision
from photo_search.scheduler import engine
from photo_search.scheduler.contracts import (
    CONTRACT_VERSION,
    JobResult,
    ProcessorSpec,
    RuntimeCapabilities,
    canonical_json,
)

#: Fixed wall clock so an aged fixture file is always outside the stability window.
WALL_BASE_MS = 1_700_000_000_000
QUIET_AGE_MS = 3_600_000
SHA = "a" * 64
PACKAGE = "onnxruntime-cpu@test"
DIMENSION = 3
SPACE = "test-space:a1"
PROCESSOR = "test-embedding@1"


def stamp(path: Path, *, age_ms: int = QUIET_AGE_MS) -> Path:
    moment = (WALL_BASE_MS - int(age_ms)) * 1_000_000
    os.utime(path, ns=(moment, moment))
    return path


def write_jpeg(path: Path, *, index: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (24, 18), (30 + index * 40 % 200, 60, 90))
    pixels = image.load()
    for x in range(image.size[0]):
        for y in range(image.size[1]):
            pixels[x, y] = ((30 + index * 7 + x) % 256, (60 + y) % 256, (90 + index) % 256)
    image.save(path, "JPEG", quality=90)
    return stamp(path)


class Clock:
    """Deterministic monotone clock for leases and job timestamps."""

    def __init__(self, start: int = WALL_BASE_MS) -> None:
        self.ms = int(start)

    def __call__(self) -> int:
        return self.ms

    def advance(self, delta_ms: int) -> None:
        self.ms += int(delta_ms)


@dataclass
class Library:
    """A scanned temp library plus the assets, keyed by their file name."""

    data_root: Path
    root: Path
    service: CatalogService
    clock: Clock
    assets: dict[str, int] = field(default_factory=dict)

    @property
    def catalog(self) -> Any:
        return self.service.catalog

    @property
    def source_id(self) -> int:
        row = self.catalog.read_one("SELECT MIN(source_id) AS value FROM source_roots")
        return int(row["value"])

    def close(self) -> None:
        self.service.close()


def build_library(tmp_path: Path, names: Sequence[str]) -> Library:
    """Write one distinct JPEG per name and scan it with the production scanner."""
    data_root = Path(tmp_path) / "PhotoSearchData"
    root = Path(tmp_path) / "library"
    clock = Clock()
    library = Library(
        data_root=data_root,
        root=root,
        service=CatalogService.open(
            data_root=data_root,
            clock=clock,
            lease_seconds=60,
            wall_clock_ms=clock,
            stability_sleep=clock.advance,
        ),
        clock=clock,
    )
    for index, name in enumerate(names):
        write_jpeg(root / name, index=index)
    source = library.service.register_source(display_name="library", root_path=root)
    library.service.run_scan(int(source["source_id"]))
    basenames: dict[str, int] = {}
    for row in library.catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id"):
        for location in library.catalog.read_all(
            "SELECT relative_path FROM file_locations WHERE asset_id = ? ORDER BY location_id",
            (int(row["asset_id"]),),
        ):
            key = str(location["relative_path"]).replace("\\", "/").rsplit("/", 1)[-1]
            basenames[key] = int(row["asset_id"])
    library.assets = basenames
    assert len(basenames) == len(names), (basenames, names)
    return library


@pytest.fixture()
def library(tmp_path: Path):
    value = build_library(tmp_path, ("杭州西湖 照片.jpg", "beach sunset.jpg", "IMG_0001.JPG"))
    try:
        yield value
    finally:
        value.close()


def embedding_spec(
    *,
    space_id: str = SPACE,
    processor_id: str = PROCESSOR,
    dimension: int = DIMENSION,
) -> ProcessorSpec:
    """A test embedding processor: metric cosine, L2 normalisation, no real model."""
    return ProcessorSpec.from_envelope(
        {
            "contract_version": CONTRACT_VERSION,
            "spec_revision": "test-embedding-v1",
            "processor_id": processor_id,
            "processor_kind": "embedding_model",
            "task_type": "image_embedding",
            "config_hash": SHA,
            "input_type": "asset",
            "output_type": "embedding",
            "device_policy": "CPU_ALLOWED",
            "runtime_backend": "onnxruntime",
            "runtime_package_id": PACKAGE,
            "supported_devices": ["cpu"],
            "supported_precisions": ["FP32"],
            "max_batch_size": 1,
            "cancellation_supported": True,
            "resource_measurements": [],
            "model_id": "test/model",
            "model_revision": "r1",
            "weights": [{"name": "test.onnx", "revision": "r1", "sha256": SHA}],
            "image_preprocess_version": "image-v1",
            "query_preprocess_version": "query-v1",
            "tokenizer_version": "tokenizer-v1",
            "instruction_template_version": "none-v1",
            "normalization_version": "L2-v1",
            "dimension": int(dimension),
            "metric": "cosine",
            "space_id": str(space_id),
            "max_pixels": 16,
            "max_tokens": 4,
        }
    )


class FakeEncoder:
    """The deterministic stand-in for the ADR-0003 ``EmbeddingAdapter`` query half."""

    def __init__(self, spec: ProcessorSpec, *, vectors: dict[str, list[float]]) -> None:
        self._spec = spec
        self._vectors = {str(key): [float(value) for value in vector] for key, vector in vectors.items()}
        self.calls: list[str] = []

    def spec(self) -> ProcessorSpec:
        return self._spec

    def encode_queries(
        self, texts: Sequence[str], cancellation: Any = None
    ) -> list[list[float]]:
        self.calls.extend(str(text) for text in texts)
        return [list(self._vectors[str(text)]) for text in texts]


def capability(package: str, backend: str, provider: str, precision: str) -> RuntimeCapabilities:
    return RuntimeCapabilities.seal(
        {
            "runtime_package_id": package,
            "runtime_backend": backend,
            "runtime_package_name": package,
            "runtime_package_version": "1",
            "python_version": "3.12.10",
            "platform": "windows-x86_64",
            "driver_version": None,
            "providers": [
                {
                    "name": provider,
                    "device": "cpu",
                    "precisions": [precision],
                    "available": True,
                    "health": "healthy",
                    "health_checked_at": 1_000,
                    "evidence_id": None,
                }
            ],
            "health_status": "healthy",
            "health_checked_at": 1_000,
            "measured_at": 1_000,
            "evidence_ids": [],
            "failure_code": None,
        }
    )


def configured_scheduler(catalog: Any, specs: Sequence[ProcessorSpec]) -> engine.Scheduler:
    """A scheduler with healthy CPU capability and qualification evidence per spec."""
    scheduler = engine.Scheduler(catalog, list(specs))
    for spec in specs:
        precision = spec.supported_precisions[0]
        scheduler.record_capability(
            capability(spec.runtime_package_id, spec.runtime_backend, "CPUExecutionProvider", precision),
            now_ms=1_000,
        )
        scheduler.record_qualification(
            processor_id=spec.processor_id,
            spec_revision=spec.spec_revision,
            device="cpu",
            precision=precision,
            provider_name="CPUExecutionProvider",
            runtime_package_id=spec.runtime_package_id,
            now_ms=1_000,
        )
    return scheduler


def embedding_artifact(
    artifact_id: str, vector: Sequence[float], *, space_id: str = SPACE, dimension: int = DIMENSION
) -> dict[str, Any]:
    payload = {"vector": [float(value) for value in vector]}
    content = canonical_json(payload).encode("utf-8")
    return {
        "artifact_id": str(artifact_id),
        "artifact_type": "embedding",
        "artifact_revision": "embedding.v1",
        "media_type": "application/json",
        "checksum_sha256": hashlib.sha256(content).hexdigest(),
        "byte_size": len(content),
        "storage_uri": None,
        "payload": payload,
        "space_id": str(space_id),
        "dimension": int(dimension),
    }


def submit_vector(
    catalog: Any,
    scheduler: engine.Scheduler,
    spec: ProcessorSpec,
    *,
    asset_id: int,
    vector: Sequence[float],
    artifact_id: str,
    revision: str | None = None,
    now_ms: int = 1_100,
) -> None:
    """Publish one embedding artifact through the production submit path (no confirm)."""
    # The artifact logical key is (processor, input_revision, kind, revision): one asset's
    # embedding must carry its own input revision, as the real job would.
    input_revision = f"rev-{int(asset_id)}" if revision is None else str(revision)
    with catalog.write() as connection:
        jobs.enqueue(
            connection,
            subject_type="asset",
            subject_id=str(int(asset_id)),
            input_revision=input_revision,
            task_type=spec.task_type,
            processor_id=spec.processor_id,
            now_ms=1_000,
        )
    request = scheduler.claim_next(worker_id="worker", now_ms=int(now_ms))
    assert request is not None
    result = JobResult.from_envelope(
        {
            "contract_version": CONTRACT_VERSION,
            "job_id": request.job_id,
            "lease_token": request.lease_token,
            "input_revision": request.input_revision,
            "task_type": request.task_type,
            "processor_id": request.processor_id,
            "state": "SUCCEEDED",
            "resolved_device": request.resolved_device,
            "runtime_provider": request.runtime_provider,
            "precision": request.precision,
            "progress": 1,
            "artifacts": [
                embedding_artifact(
                    artifact_id,
                    vector,
                    space_id=str(spec.envelope.get("space_id") or SPACE),
                    dimension=int(spec.envelope.get("dimension") or DIMENSION),
                )
            ],
            "error": None,
            "completed_at": int(now_ms) + 100,
        }
    )
    finished = scheduler.submit(result, now_ms=int(now_ms) + 100)
    assert finished.state == "SUCCEEDED"


def confirm_pending(catalog: Any, *, now_ms: int = 2_000) -> int:
    """Replay the outbox with a fresh coordinator, as a restart would."""
    from photo_search.indexing.store import IndexCoordinator

    return IndexCoordinator(catalog).process_all(now_ms=now_ms)


def index_vector(
    catalog: Any,
    scheduler: engine.Scheduler,
    spec: ProcessorSpec,
    *,
    asset_id: int,
    vector: Sequence[float],
    artifact_id: str,
) -> None:
    """Submit and confirm one asset's vector: it is then in semantic recall."""
    submit_vector(
        catalog,
        scheduler,
        spec,
        asset_id=asset_id,
        vector=vector,
        artifact_id=artifact_id,
    )
    confirm_pending(catalog)


def create_place(
    catalog: Any,
    canonical_name: str,
    *,
    kind: str = "city",
    parent_place_id: int | None = None,
    aliases: Sequence[dict[str, str]] = (),
    now_ms: int = 1_500,
) -> int:
    """Create one ACTIVE place through the production directory path."""
    with catalog.write() as connection:
        return int(
            directory.create_place(
                connection,
                kind=str(kind),
                canonical_name=str(canonical_name),
                parent_place_id=parent_place_id,
                aliases=list(aliases),
                now_ms=int(now_ms),
            )
        )


def assign_place(
    catalog: Any,
    *,
    asset_id: int,
    place_id: int,
    request_id: str,
    now_ms: int = 1_600,
) -> None:
    """One MANUAL_ASSIGNED edit with the real revision and projection refresh."""
    with catalog.write() as connection:
        assignments.apply_request(
            connection,
            items=[
                {
                    "asset_id": int(asset_id),
                    "action": "ASSIGN",
                    "target_place_id": int(place_id),
                    "expected_revision": 0,
                }
            ],
            request_id=str(request_id),
            now_ms=int(now_ms),
            refresh=revision.projection_refresher(catalog),
        )
