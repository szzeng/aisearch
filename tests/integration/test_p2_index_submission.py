"""P2-03 artifact/outbox/LanceDB submission and crash-replay acceptance."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from photo_search.artifacts.store import generation_id_for
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import (
    ARTIFACT_CONFLICT,
    ARTIFACT_INVALID,
    SPACE_MISMATCH,
    STALE_LEASE,
    CatalogError,
)
from photo_search.indexing.store import IndexCoordinator
from photo_search.jobs import queue as jobs
from photo_search.scheduler import engine
from photo_search.scheduler.contracts import (
    CONTRACT_VERSION,
    JobResult,
    ProcessorSpec,
    RuntimeCapabilities,
    canonical_json,
)

SHA = "a" * 64
SPACE = "test-space:a1"
PROCESSOR = "test-embedding@1"
PACKAGE = "onnxruntime-cpu@test"
DIMENSION = 3


def embedding_spec(*, space_id: str = SPACE, processor_id: str = PROCESSOR) -> ProcessorSpec:
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
            "dimension": DIMENSION,
            "metric": "cosine",
            "space_id": space_id,
            "max_pixels": 16,
            "max_tokens": 4,
        }
    )


def non_model_spec() -> ProcessorSpec:
    return ProcessorSpec.from_envelope(
        {
            "contract_version": CONTRACT_VERSION,
            "spec_revision": "metadata-v1",
            "processor_id": "metadata@test",
            "processor_kind": "non_model",
            "task_type": "metadata",
            "config_hash": SHA,
            "input_type": "asset",
            "output_type": "metadata.v1",
            "device_policy": "CPU_PREFERRED",
            "runtime_backend": "python",
            "runtime_package_id": "python@test",
            "supported_devices": ["cpu"],
            "supported_precisions": ["not_applicable"],
            "max_batch_size": 1,
            "cancellation_supported": True,
            "resource_measurements": [],
        }
    )


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


def configured_scheduler(catalog: Catalog, specs: list[ProcessorSpec]) -> engine.Scheduler:
    scheduler = engine.Scheduler(catalog, specs)
    for spec in specs:
        provider = "CPUExecutionProvider" if spec.processor_kind == "embedding_model" else "python-stdlib"
        precision = spec.supported_precisions[0]
        scheduler.record_capability(
            capability(spec.runtime_package_id, spec.runtime_backend, provider, precision), now_ms=1_000
        )
        scheduler.record_qualification(
            processor_id=spec.processor_id,
            spec_revision=spec.spec_revision,
            device="cpu",
            precision=precision,
            provider_name=provider,
            runtime_package_id=spec.runtime_package_id,
            now_ms=1_000,
        )
    return scheduler


def seed_asset(catalog: Catalog, *, suffix: str = "1") -> int:
    digest = hashlib.sha256(f"asset-{suffix}".encode()).hexdigest()
    with catalog.write() as connection:
        cursor = connection.execute(
            "INSERT INTO assets(sha256, byte_size, created_at, state) VALUES (?, 1, 1000, 'ACTIVE')",
            (digest,),
        )
        return int(cursor.lastrowid)


def enqueue_and_claim(
    catalog: Catalog,
    scheduler: engine.Scheduler,
    spec: ProcessorSpec,
    *,
    asset_id: int,
    revision: str = "rev-1",
    now_ms: int = 1_100,
) -> Any:
    with catalog.write() as connection:
        jobs.enqueue(
            connection,
            subject_type="asset",
            subject_id=str(asset_id),
            input_revision=revision,
            task_type=spec.task_type,
            processor_id=spec.processor_id,
            now_ms=1_000,
        )
    request = scheduler.claim_next(worker_id="worker", now_ms=now_ms)
    assert request is not None
    return request


def artifact_ref(
    kind: str,
    artifact_id: str,
    payload: dict[str, Any],
    *,
    space_id: str | None = None,
    dimension: int | None = None,
    binary: bytes | None = None,
) -> dict[str, Any]:
    content = binary if binary is not None else canonical_json(payload).encode("utf-8")
    return {
        "artifact_id": artifact_id,
        "artifact_type": kind,
        "artifact_revision": f"{kind}.v1",
        "media_type": "application/octet-stream" if kind == "binary" else "application/json",
        "checksum_sha256": hashlib.sha256(content).hexdigest(),
        "byte_size": len(content),
        "storage_uri": None,
        "payload": payload,
        "space_id": space_id,
        "dimension": dimension,
    }


def embedding_artifact(artifact_id: str, *, space_id: str = SPACE) -> dict[str, Any]:
    return artifact_ref(
        "embedding",
        artifact_id,
        {"vector": [0.1, 0.2, 0.3]},
        space_id=space_id,
        dimension=DIMENSION,
    )


def result(request: Any, artifacts: list[dict[str, Any]]) -> JobResult:
    return JobResult.from_envelope(
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
            "artifacts": artifacts,
            "error": None,
            "completed_at": 2_000,
        }
    )


@pytest.fixture()
def catalog(tmp_path: Path):
    value = Catalog.open(tmp_path / "PhotoSearchData" / "catalog" / "catalog.db", lease_seconds=60)
    try:
        yield value
    finally:
        value.close()


def submit_embedding(catalog: Catalog, *, artifact_id: str = "embedding-1") -> tuple[engine.Scheduler, int]:
    spec = embedding_spec()
    scheduler = configured_scheduler(catalog, [spec])
    asset_id = seed_asset(catalog)
    request = enqueue_and_claim(catalog, scheduler, spec, asset_id=asset_id)
    finished = scheduler.submit(result(request, [embedding_artifact(artifact_id)]), now_ms=1_200)
    assert finished.state == "SUCCEEDED"
    return scheduler, asset_id


def mark_asset_retention_eligible(catalog: Catalog, *, asset_id: int, now_ms: int) -> None:
    from photo_search.retention import OBSERVATION_MS

    first_missing = int(now_ms) - OBSERVATION_MS - 1_000
    with catalog.write() as connection:
        location = connection.execute(
            "SELECT source_id FROM file_locations WHERE asset_id=?", (asset_id,)
        ).fetchone()
        if location is None:
            source = connection.execute(
                "INSERT INTO source_roots(display_name,root_path,status) "
                "VALUES('retention-test','D:/synthetic-retention-source','ONLINE')"
            )
            source_id = int(source.lastrowid)
            connection.execute(
                "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,"
                "last_seen_scan_id,availability) VALUES(?,?,?, ?,1,1,NULL,'MISSING')",
                (source_id, f"retention-{asset_id}.jpg", f"retention-{asset_id}.jpg", asset_id),
            )
            location = {"source_id": source_id}
        assert location is not None
        source_id = int(location["source_id"])
        connection.execute(
            "UPDATE file_locations SET availability='MISSING' WHERE asset_id=?", (asset_id,)
        )
        scan = connection.execute(
            "INSERT INTO scan_runs(source_id,started_at,finished_at,state,error) "
            "VALUES(?,?,?,'SUCCEEDED',NULL)",
            (source_id, first_missing + OBSERVATION_MS, now_ms),
        )
        scan_id = int(scan.lastrowid)
        connection.execute(
            "UPDATE source_roots SET last_successful_scan=? WHERE source_id=?",
            (scan_id, source_id),
        )
        connection.execute(
            "INSERT INTO asset_retention_source_evidence(asset_id,source_id,scan_id,"
            "missing_confirmed_at_ms) VALUES(?,?,?,?)",
            (asset_id, source_id, scan_id, now_ms),
        )
        connection.execute(
            "INSERT INTO asset_retention_state(asset_id,first_missing_at_ms,last_qualified_at_ms,"
            "state,updated_at_ms) VALUES(?,?,?,'ELIGIBLE',?)",
            (asset_id, first_missing, now_ms, now_ms),
        )


def test_sqlite_commit_creates_one_published_artifact_one_outbox_and_no_ready(catalog: Catalog) -> None:
    _, asset_id = submit_embedding(catalog)
    artifact = catalog.read_one("SELECT * FROM artifacts")
    assert artifact["lifecycle_state"] == "PUBLISHED"
    assert not Path(str(artifact["storage_uri"])).is_absolute()
    assert (catalog.db_path.parent.parent / str(artifact["storage_uri"])).is_file()
    assert catalog.read_one("SELECT COUNT(*) FROM index_outbox")[0] == 1
    entry = catalog.read_one("SELECT * FROM index_entries")
    assert entry["state"] == "PENDING_REPAIR" and entry["confirmed_at"] is None
    generation = generation_id_for(SPACE, PROCESSOR)
    coordinator = IndexCoordinator(catalog)
    assert coordinator.semantic_ready(space_id=SPACE, asset_id=asset_id, generation_id=generation) is False


def test_crash_before_sqlite_commit_rolls_back_and_same_lease_replays_once(
    catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = embedding_spec()
    scheduler = configured_scheduler(catalog, [spec])
    asset_id = seed_asset(catalog)
    request = enqueue_and_claim(catalog, scheduler, spec, asset_id=asset_id)
    payload = result(request, [embedding_artifact("crash-before-sqlite")])
    original = scheduler.artifact_store.persist_result

    def crash(connection, **options):
        original(connection, **options)
        raise RuntimeError("injected before commit")

    monkeypatch.setattr(scheduler.artifact_store, "persist_result", crash)
    with pytest.raises(RuntimeError, match="injected"):
        scheduler.submit(payload, now_ms=1_200)
    assert catalog.read_one("SELECT COUNT(*) FROM artifacts")[0] == 0
    assert catalog.read_one("SELECT COUNT(*) FROM index_outbox")[0] == 0
    assert catalog.read_one("SELECT state FROM jobs WHERE job_id = ?", (request.job_id,))[0] == "RUNNING"
    assert not list((catalog.db_path.parent.parent / "artifacts").rglob("*.tmp"))

    monkeypatch.setattr(scheduler.artifact_store, "persist_result", original)
    scheduler.submit(payload, now_ms=1_300)
    assert catalog.read_one("SELECT COUNT(*) FROM artifacts")[0] == 1
    assert catalog.read_one("SELECT COUNT(*) FROM index_outbox")[0] == 1


def test_lance_persisted_before_sqlite_confirmation_replays_without_duplicates(catalog: Catalog) -> None:
    _, asset_id = submit_embedding(catalog, artifact_id="crash-after-lance")
    generation = generation_id_for(SPACE, PROCESSOR)
    coordinator = IndexCoordinator(catalog)
    with pytest.raises(RuntimeError, match="after_lance"):
        coordinator.process_next(now_ms=2_000, failpoint="after_lance")
    assert catalog.read_one("SELECT state FROM index_outbox")[0] == "IN_FLIGHT"
    assert coordinator.semantic_ready(space_id=SPACE, asset_id=asset_id, generation_id=generation) is False

    restarted = IndexCoordinator(catalog)
    assert restarted.process_next(now_ms=2_100) is True
    assert restarted.process_next(now_ms=2_200) is False
    generation_row = catalog.read_one("SELECT * FROM index_generations")
    assert restarted.index.count(
        generation_id=generation, dimension=DIMENSION, space_id=SPACE
    ) == 1
    assert generation_row["state"] == "READY" and generation_row["is_active"] == 1
    assert restarted.semantic_ready(space_id=SPACE, asset_id=asset_id, generation_id=generation)

    # A second sequential replay is an actual merge of the same logical record.
    with catalog.write() as connection:
        connection.execute("UPDATE index_outbox SET state = 'PENDING'")
        connection.execute("UPDATE index_entries SET state = 'PENDING_REPAIR', confirmed_at = NULL")
    assert restarted.process_next(now_ms=2_300) is True
    assert restarted.index.count(
        generation_id=generation, dimension=DIMENSION, space_id=SPACE
    ) == 1
    assert catalog.read_one("SELECT COUNT(*) FROM index_entries")[0] == 1


@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
def test_retention_delete_vector_replays_on_real_lance_and_never_resurrects(
    catalog: Catalog, batch: bool
) -> None:
    from photo_search.retention import activate_asset_retention_cleanup

    _, asset_id = submit_embedding(catalog, artifact_id=f"retention-delete-{batch}")
    coordinator = IndexCoordinator(catalog)
    first_generation = generation_id_for(SPACE, PROCESSOR)
    assert coordinator.process_next(now_ms=2_000) is True
    first_rows = coordinator.index.rows(
        generation_id=first_generation, dimension=DIMENSION
    )
    assert len(first_rows) == 1
    unrelated_assets = []

    # A second generation has its own real Lance table and identity for this same asset.
    second_processor = "test-embedding-legacy@1"
    second_generation = generation_id_for(SPACE, second_processor)
    artifact_id = str(catalog.read_one("SELECT artifact_id FROM artifacts")[0])
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO index_generations(generation_id,space_id,processor_id,dimension,state,"
            "is_active,created_at,updated_at) VALUES(?,?,?,?,'READY',0,1,1)",
            (second_generation, SPACE, second_processor, DIMENSION),
        )
        connection.execute(
            "INSERT INTO index_entries(space_id,asset_id,generation_id,artifact_id,"
            "confirmation_revision,confirmed_at,state) VALUES(?,?,?,?,0,2000,'CONFIRMED')",
            (SPACE, asset_id, second_generation, artifact_id),
        )
    second_record = dict(first_rows[0])
    second_record.update(
        record_id=f"{second_generation}:{asset_id}", generation_id=second_generation
    )
    coordinator.index.upsert(
        generation={
            "generation_id": second_generation,
            "space_id": SPACE,
            "dimension": DIMENSION,
        },
        record=second_record,
    )
    assert coordinator.index.count(
        generation_id=second_generation, dimension=DIMENSION, space_id=SPACE
    ) == 1

    # One asset may also have a generation in another vector space. It must be independently
    # scheduled and deleted without touching either same-space generation's record identity.
    second_space = "test-space:b2"
    second_space_processor = "test-embedding-space-b@1"
    second_space_spec = embedding_spec(
        space_id=second_space, processor_id=second_space_processor
    )
    second_space_scheduler = configured_scheduler(catalog, [second_space_spec])
    second_space_request = enqueue_and_claim(
        catalog, second_space_scheduler, second_space_spec, asset_id=asset_id
    )
    second_space_scheduler.submit(
        result(
            second_space_request,
            [embedding_artifact("retention-delete-space-b", space_id=second_space)],
        ),
        now_ms=2_500,
    )
    second_space_generation = generation_id_for(second_space, second_space_processor)
    second_space_coordinator = IndexCoordinator(catalog)
    assert second_space_coordinator.process_next(now_ms=2_600) is True
    assert second_space_coordinator.index.count(
        generation_id=second_space_generation, dimension=DIMENSION, space_id=second_space
    ) == 1

    if batch:
        artifact_id = str(catalog.read_one("SELECT artifact_id FROM artifacts WHERE job_id IS NOT NULL LIMIT 1")[0])
        checksum = hashlib.sha256(b"{}").hexdigest()
        unrelated_assets = [seed_asset(catalog, suffix=f"backlog-{batch}-{index}") for index in range(6)]
        with catalog.write() as connection:
            connection.executemany(
                "INSERT INTO index_outbox(event_id,event_type,artifact_id,space_id,asset_id,generation_id,"
                "projection_revision,payload_json,payload_checksum,state,attempts,created_at,updated_at) "
                "VALUES(?,'UPSERT_VECTOR',?,?,?,?,1,'{}',?,'PENDING',0,1100,1100)",
                ((f"retention-backlog-{index}", artifact_id, SPACE, other_asset, first_generation, checksum)
                 for index, other_asset in enumerate(unrelated_assets)),
            )

    with catalog.write() as connection:
        _, scheduled = activate_asset_retention_cleanup(
            connection, asset_id=asset_id, now_ms=3_000
        )
        assert len(scheduled) == 3
        assert activate_asset_retention_cleanup(
            connection, asset_id=asset_id, now_ms=3_001
        ) == (str(connection.execute(
            "SELECT barrier_id FROM asset_retention_barriers WHERE asset_id=?", (asset_id,)
        ).fetchone()[0]), scheduled)
        # A stale ordinary event remains durable but is not claimable under the tombstone.
        connection.execute(
            "UPDATE index_outbox SET state='PENDING' WHERE asset_id=? AND event_type='UPSERT_VECTOR'",
            (asset_id,),
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM index_outbox WHERE asset_id=? AND event_type='DELETE_VECTOR'",
            (asset_id,),
        ).fetchone()[0] == 3

    if batch:
        with pytest.raises(RuntimeError, match="after_vector_delete"):
            coordinator.process_retention_deletes(
                asset_id=asset_id, now_ms=3_100, limit=10, failpoint="after_vector_delete"
            )
        assert coordinator.index.count(
            generation_id=first_generation, dimension=DIMENSION, space_id=SPACE
        ) == 0
        assert coordinator.index.count(
            generation_id=second_generation, dimension=DIMENSION, space_id=SPACE
        ) == 0
        assert coordinator.index.count(
            generation_id=second_space_generation,
            dimension=DIMENSION,
            space_id=second_space,
        ) == 0
        restarted = IndexCoordinator(catalog)
        assert restarted.process_retention_deletes(asset_id=asset_id, now_ms=3_200, limit=10) == 3
        assert restarted.process_retention_deletes(asset_id=asset_id, now_ms=3_300, limit=10) == 0
        assert catalog.read_one(
            "SELECT COUNT(*) FROM index_outbox WHERE asset_id IN (" + ",".join("?" for _ in unrelated_assets) +
            ") AND event_type='UPSERT_VECTOR' AND state='PENDING'", tuple(unrelated_assets),
        )[0] == len(unrelated_assets)
        with catalog.write() as connection:
            connection.execute(
                "DELETE FROM index_outbox WHERE asset_id IN (" + ",".join("?" for _ in unrelated_assets) + ")",
                tuple(unrelated_assets),
            )
    else:
        with pytest.raises(RuntimeError, match="after_vector_delete"):
            coordinator.process_next(now_ms=3_100, failpoint="after_vector_delete")
        after_first = {
            first_generation: coordinator.index.count(
                generation_id=first_generation, dimension=DIMENSION, space_id=SPACE
            ),
            second_generation: coordinator.index.count(
                generation_id=second_generation, dimension=DIMENSION, space_id=SPACE
            ),
        }
        after_first[second_space_generation] = coordinator.index.count(
            generation_id=second_space_generation,
            dimension=DIMENSION,
            space_id=second_space,
        )
        assert sum(after_first.values()) == 2
        restarted = IndexCoordinator(catalog)
        assert restarted.process_next(now_ms=3_200) is True  # replay delete after external success
        assert restarted.process_next(now_ms=3_300) is True
        assert restarted.process_next(now_ms=3_400) is True
        assert restarted.process_next(now_ms=3_500) is False

    for generation_id, space_id in (
        (first_generation, SPACE),
        (second_generation, SPACE),
        (second_space_generation, second_space),
    ):
        assert restarted.index.count(
            generation_id=generation_id, dimension=DIMENSION, space_id=space_id
        ) == 0
        assert restarted.semantic_ready(
            space_id=space_id, asset_id=asset_id, generation_id=generation_id
        ) is False
    assert catalog.read_one(
        "SELECT COUNT(*) FROM index_outbox WHERE asset_id=? AND event_type='DELETE_VECTOR' "
        "AND state='CONFIRMED'",
        (asset_id,),
    )[0] == 3
    assert catalog.read_one(
        "SELECT COUNT(*) FROM index_outbox WHERE asset_id=? AND event_type='UPSERT_VECTOR' "
        "AND state='PENDING'",
        (asset_id,),
    )[0] == 2
    from photo_search.photo_smart_collections import dependency_signature

    with catalog.read_snapshot() as connection:
        _, pending_projection, _ = dependency_signature(
            connection,
            {
                "execution_kind": "COMBINED",
                "search_binding": {"image_generation_id": first_generation},
            },
        )
    assert pending_projection is False  # blocked old UPSERTs do not hold smart caches stale
    with catalog.write() as connection:
        assert activate_asset_retention_cleanup(
            connection, asset_id=asset_id, now_ms=3_600
        ) == (str(connection.execute(
            "SELECT barrier_id FROM asset_retention_barriers WHERE asset_id=?", (asset_id,)
        ).fetchone()[0]), scheduled)


def test_retention_cleanup_service_waits_for_lance_then_cancels_on_restore(catalog: Catalog) -> None:
    from photo_search.library_retention import (
        begin_asset_retention_cleanup,
        finalize_asset_retention_cleanup,
    )

    _, asset_id = submit_embedding(catalog, artifact_id="retention-finalize-restore")
    coordinator = IndexCoordinator(catalog)
    assert coordinator.process_next(now_ms=2_000) is True
    generation = generation_id_for(SPACE, PROCESSOR)
    assert coordinator.index.count(
        generation_id=generation, dimension=DIMENSION, space_id=SPACE
    ) == 1
    mark_asset_retention_eligible(catalog, asset_id=asset_id, now_ms=100_000_000_000)
    started = begin_asset_retention_cleanup(
        catalog, asset_id=asset_id, now_ms=100_000_000_001
    )
    assert started["state"] == "WAITING_FOR_VECTOR_DELETE"
    assert finalize_asset_retention_cleanup(
        catalog, asset_id=asset_id, now_ms=100_000_000_002
    )["state"] == "WAITING_FOR_VECTOR_DELETE"
    assert catalog.read_one("SELECT 1 FROM assets WHERE asset_id=?", (asset_id,)) is not None

    # The vector delete has committed, but a subsequent source recovery invalidates the
    # pending cleanup. The barrier is removed and an UPSERT is durably scheduled to restore it.
    assert coordinator.process_next(now_ms=100_000_000_003) is True
    assert coordinator.index.count(
        generation_id=generation, dimension=DIMENSION, space_id=SPACE
    ) == 0
    with catalog.write() as connection:
        connection.execute(
            "UPDATE file_locations SET availability='PRESENT' WHERE asset_id=?", (asset_id,)
        )
    result_state = finalize_asset_retention_cleanup(
        catalog, asset_id=asset_id, now_ms=100_000_000_004
    )
    assert result_state["state"] == "CANCELLED_RESTORED"
    assert catalog.read_one("SELECT 1 FROM asset_retention_barriers WHERE asset_id=?", (asset_id,)) is None
    assert coordinator.process_next(now_ms=100_000_000_005) is True
    assert coordinator.index.count(
        generation_id=generation, dimension=DIMENSION, space_id=SPACE
    ) == 1
    assert coordinator.semantic_ready(
        space_id=SPACE, asset_id=asset_id, generation_id=generation
    ) is True


def test_late_old_projection_event_reads_latest_state_and_cannot_rollback(catalog: Catalog) -> None:
    _, asset_id = submit_embedding(catalog, artifact_id="projection-merge")
    coordinator = IndexCoordinator(catalog)
    assert coordinator.process_next(now_ms=1_250) is True
    with catalog.write() as connection:
        created = coordinator.schedule_projection_refresh(
            connection,
            asset_ids=[asset_id, asset_id],
            projection_revision=1,
            now_ms=1_300,
        )
        connection.execute(
            "UPDATE index_outbox SET state = 'PENDING' WHERE projection_revision = 0"
        )
    assert created == 1
    generation = catalog.read_one("SELECT * FROM index_generations")
    # The revision-0 event is selected first but recognized as superseded; it does not
    # overwrite the logical row or mark the latest entry READY.
    assert coordinator.process_next(now_ms=1_400) is True
    old_rows = coordinator.index.rows(
        generation_id=str(generation["generation_id"]), dimension=DIMENSION
    )
    assert len(old_rows) == 1 and old_rows[0]["projection_revision"] == 0
    assert not coordinator.semantic_ready(
        space_id=SPACE,
        asset_id=asset_id,
        generation_id=str(generation["generation_id"]),
    )
    assert coordinator.process_next(now_ms=1_500) is True
    rows = coordinator.index.rows(
        generation_id=str(generation["generation_id"]), dimension=DIMENSION
    )
    assert len(rows) == 1 and rows[0]["projection_revision"] == 1


def test_invalid_payload_space_duplicate_and_late_lease_leave_no_persistent_artifact(
    catalog: Catalog,
) -> None:
    spec = embedding_spec()
    scheduler = configured_scheduler(catalog, [spec])
    asset_id = seed_asset(catalog)
    request = enqueue_and_claim(catalog, scheduler, spec, asset_id=asset_id)
    bad = embedding_artifact("bad-checksum")
    bad["checksum_sha256"] = "f" * 64
    with pytest.raises(CatalogError) as invalid:
        scheduler.submit(result(request, [bad]), now_ms=1_200)
    assert invalid.value.code == ARTIFACT_INVALID
    assert catalog.read_one("SELECT COUNT(*) FROM artifacts")[0] == 0

    valid_first = embedding_artifact("valid-first")
    invalid_second = embedding_artifact("invalid-second")
    invalid_second["byte_size"] += 1
    with pytest.raises(CatalogError) as multi_invalid:
        scheduler.submit(result(request, [valid_first, invalid_second]), now_ms=1_250)
    assert multi_invalid.value.code == ARTIFACT_INVALID
    artifact_root = catalog.db_path.parent.parent / "artifacts"
    assert not list(artifact_root.rglob("*.tmp"))

    foreign = embedding_artifact("foreign", space_id="other-space:same-dimension")
    with pytest.raises(CatalogError) as mismatch:
        scheduler.submit(result(request, [foreign]), now_ms=1_300)
    assert mismatch.value.code == SPACE_MISMATCH
    assert catalog.read_one("SELECT COUNT(*) FROM artifacts")[0] == 0

    newer = scheduler.claim_next(worker_id="new-worker", now_ms=61_101)
    assert newer is not None
    with pytest.raises(CatalogError) as stale:
        scheduler.submit(result(request, [embedding_artifact("late")]), now_ms=61_102)
    assert stale.value.code == STALE_LEASE
    assert catalog.read_one("SELECT COUNT(*) FROM artifacts")[0] == 0
    scheduler.submit(result(newer, [embedding_artifact("valid")]), now_ms=61_103)
    with pytest.raises(CatalogError):
        scheduler.submit(result(newer, [embedding_artifact("valid")]), now_ms=61_104)
    assert catalog.read_one("SELECT COUNT(*) FROM artifacts")[0] == 1


def test_structured_binary_and_empty_success_paths_do_not_create_vector_events(catalog: Catalog) -> None:
    spec = non_model_spec()
    scheduler = configured_scheduler(catalog, [spec])
    asset_id = seed_asset(catalog)
    structured = artifact_ref("structured", "structured-1", {"state": "ABSENT"})
    first = enqueue_and_claim(catalog, scheduler, spec, asset_id=asset_id, revision="r1")
    scheduler.submit(result(first, [structured]), now_ms=1_200)

    binary = b"binary payload"
    binary_ref = artifact_ref(
        "binary",
        "binary-1",
        {"base64": base64.b64encode(binary).decode()},
        binary=binary,
    )
    second = enqueue_and_claim(catalog, scheduler, spec, asset_id=asset_id, revision="r2", now_ms=1_300)
    scheduler.submit(result(second, [binary_ref]), now_ms=1_400)
    third = enqueue_and_claim(catalog, scheduler, spec, asset_id=asset_id, revision="r3", now_ms=1_500)
    scheduler.submit(result(third, []), now_ms=1_600)

    assert catalog.read_one("SELECT COUNT(*) FROM artifacts")[0] == 2
    assert catalog.read_one("SELECT COUNT(*) FROM index_outbox")[0] == 0
    assert catalog.read_one("SELECT COUNT(*) FROM index_entries")[0] == 0


def test_repair_rebuilds_deleted_confirmation_and_reexecution_can_republish(catalog: Catalog) -> None:
    _, asset_id = submit_embedding(catalog, artifact_id="repairable")
    coordinator = IndexCoordinator(catalog)
    coordinator.process_all(now_ms=2_000)
    generation = generation_id_for(SPACE, PROCESSOR)
    with catalog.write() as connection:
        connection.execute("DELETE FROM index_entries")
    repair = coordinator.audit_and_schedule_repairs(now_ms=2_100)
    assert repair == {"scheduled": ["repairable"], "requires_reexecution": []}
    coordinator.process_all(now_ms=2_200)
    assert coordinator.semantic_ready(space_id=SPACE, asset_id=asset_id, generation_id=generation)
    assert coordinator.index.count(generation_id=generation, dimension=DIMENSION, space_id=SPACE) == 1

    # Active generations and pending outbox rows are protected from reclamation.
    with pytest.raises(CatalogError) as protected:
        coordinator.artifacts.reclaim(catalog, artifact_id="repairable", now_ms=2_300)
    assert protected.value.code == ARTIFACT_CONFLICT
    path = coordinator.artifacts.resolve_uri(
        str(catalog.read_one("SELECT storage_uri FROM artifacts")[0])
    )
    assert coordinator.artifacts.prune_orphans(
        catalog, min_age_ms=0, now_ms=10**15
    ) == []
    assert path.is_file()
    with catalog.write() as connection:
        connection.execute("UPDATE index_generations SET is_active = 0")
    assert coordinator.artifacts.reclaim(catalog, artifact_id="repairable", now_ms=2_400)
    audit = coordinator.audit_and_schedule_repairs(now_ms=2_500)
    assert audit["requires_reexecution"] == ["repairable"]

    coordinator.artifacts.republish_reclaimed(
        catalog,
        artifact_id="repairable",
        payload={"vector": [0.1, 0.2, 0.3]},
        now_ms=2_600,
    )
    audit = coordinator.audit_and_schedule_repairs(now_ms=2_700)
    assert audit["scheduled"] == ["repairable"]
    coordinator.process_all(now_ms=2_800)
    assert coordinator.semantic_ready(space_id=SPACE, asset_id=asset_id, generation_id=generation)
    assert catalog.read_one("SELECT state FROM jobs")[0] == "SUCCEEDED"


def test_read_only_api_diagnostics_expose_counts_without_paths(api_env) -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        await api_env.authenticate(client)
        stats = await client.get("/api/v1/stats")
        capabilities = await client.get("/api/v1/capabilities")
        assert stats.status_code == capabilities.status_code == 200
        return {"stats": stats.json(), "capabilities": capabilities.json()}

    body = api_env.scenario(scenario)
    for payload in (body["stats"]["indexing"], body["capabilities"]["indexing"]):
        assert payload["pending"] == payload["confirmed"] == payload["pending_repair"] == 0
        assert payload["generations"] == {}
    serialised = json.dumps(body, ensure_ascii=False)
    assert str(api_env.data_root) not in serialised
    assert str(api_env.library) not in serialised
