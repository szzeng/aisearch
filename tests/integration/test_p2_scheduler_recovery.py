"""P2-02 D-level fault injection: migration, restart, lease takeover and blocking recovery.

Every database lives in a ``%TEMP%`` subdirectory created by pytest; no real photo library
is read, moved or scanned. The fake executor is a deterministic non-model CPU processor:
it proves scheduler state closure without any model, EXIF or GPS work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from photo_search.catalog import schema
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import (
    LEASE_EXPIRED,
    MIGRATION_FAILED,
    RUNTIME_CAPABILITY_UNAVAILABLE,
    SCHEMA_INCONSISTENT,
    SCHEMA_TOO_NEW,
    STALE_LEASE,
    CatalogError,
)
from photo_search.catalog.migrate import inspect_database, migrate_database
from photo_search.catalog.sqlite_io import connect_rw, open_readonly
from photo_search.jobs import queue as jobs
from photo_search.scheduler import engine
from photo_search.scheduler.contracts import (
    CONTRACT_VERSION,
    ProcessorSpec,
    RuntimeCapabilities,
)

SHA = "a" * 64
PY_PACKAGE = "photo-search-runtime@1"
ORT_PACKAGE = "onnxruntime-gpu@1.30.0"
CPU_PROCESSOR = "geo-extract@1"
GPU_PROCESSOR = "clip-image@36e679e6-fp32-v1"

V1_TABLES = ("assets", "file_locations", "jobs", "metadata", "scan_runs", "source_roots")
V2_TABLES = (*V1_TABLES, "runtime_capabilities", "runtime_qualification_evidence", "scheduler_state")
V3_TABLES = (*V2_TABLES, "artifacts", "index_entries", "index_generations", "index_outbox")
V4_TABLES = (
    *V3_TABLES,
    "asset_geo_facts",
    "asset_place_assignment_history",
    "asset_place_assignments",
    "asset_place_exclusions",
    "catalog_filter_revision",
    "catalog_filter_revision_events",
    "place_aliases",
    "place_external_refs",
    "place_operation_items",
    "place_operations",
    "place_suggestion_revision",
    "places",
)

V5_TABLES = (
    *V4_TABLES,
    "active_geo_dataset",
    "geo_dataset_files",
    "geo_datasets",
    "place_resolution_candidates",
    "place_resolutions",
)

SUPPORTED = int(schema.SUPPORTED_SCHEMA_VERSION)
NEXT_VERSION = SUPPORTED + 1

#: The exact ADR-0002 §3.6 column list whose values a v1 row must keep, byte for byte.
V1_JOB_COLUMNS = (
    "job_id",
    "subject_type",
    "subject_id",
    "input_revision",
    "task_type",
    "processor_id",
    "state",
    "priority",
    "attempts",
    "max_attempts",
    "run_after",
    "lease_token",
    "lease_expires_at",
    "worker_id",
    "progress",
    "last_error_code",
    "last_error_message",
    "created_at",
    "updated_at",
)
DISPATCH_COLUMNS = (
    "requested_device",
    "resolved_device",
    "runtime_provider",
    "precision",
    "runtime_capability_revision",
    "dispatch_spec_revision",
    "blocking_reason",
)


def data_root(tmp_path: Path) -> Path:
    return tmp_path / "PhotoSearchData"


def db_path(tmp_path: Path) -> Path:
    return data_root(tmp_path) / "catalog" / "catalog.db"


# --------------------------------------------------------------------------- builders
def non_model_spec(**overrides: Any) -> ProcessorSpec:
    envelope: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "spec_revision": "geo-extract-v1",
        "processor_id": CPU_PROCESSOR,
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
        "max_batch_size": 32,
        "cancellation_supported": True,
        "resource_measurements": [],
    }
    envelope.update(overrides)
    return ProcessorSpec.from_envelope(envelope)


def embedding_spec() -> ProcessorSpec:
    return ProcessorSpec.from_envelope(
        {
            "contract_version": CONTRACT_VERSION,
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
            "weights": [{"name": "image_tower.fp32.onnx", "revision": "36e679e6", "sha256": SHA}],
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


def _provider(name: str, device: str, health: str, precisions: tuple[str, ...]) -> dict[str, Any]:
    return {
        "name": name,
        "device": device,
        "precisions": list(precisions),
        "available": True,
        "health": health,
        "health_checked_at": 1_000,
        "evidence_id": None,
    }


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
            "providers": [_provider("python-stdlib", "cpu", "healthy", ("not_applicable",))],
            "health_status": "healthy",
            "health_checked_at": now_ms,
            "measured_at": now_ms,
            "evidence_ids": [],
            "failure_code": None,
        }
    )


def onnx_capability(*, gpu_health: str | None, now_ms: int = 1_000) -> RuntimeCapabilities:
    providers = [_provider("CPUExecutionProvider", "cpu", "healthy", ("FP32",))]
    if gpu_health is not None:
        providers.append(_provider("CUDAExecutionProvider", "gpu", gpu_health, ("FP32",)))
    return RuntimeCapabilities.seal(
        {
            "runtime_package_id": ORT_PACKAGE,
            "runtime_backend": "onnxruntime",
            "runtime_package_name": "onnxruntime-gpu",
            "runtime_package_version": "1.30.0",
            "python_version": "3.12.10",
            "platform": "windows-x86_64",
            "driver_version": "616.56",
            "providers": providers,
            "health_status": "healthy" if gpu_health == "healthy" else "unhealthy",
            "health_checked_at": now_ms,
            "measured_at": now_ms,
            "evidence_ids": [],
            "failure_code": None,
        }
    )


def qualify(
    scheduler: engine.Scheduler,
    spec: ProcessorSpec,
    *,
    device: str = "cpu",
    provider_name: str = "python-stdlib",
    precision: str | None = None,
    now_ms: int = 1_000,
) -> None:
    scheduler.record_qualification(
        processor_id=spec.processor_id,
        spec_revision=spec.spec_revision,
        device=device,
        precision=precision or spec.supported_precisions[0],
        provider_name=provider_name,
        runtime_package_id=spec.runtime_package_id,
        now_ms=now_ms,
    )


def fake_executor(**overrides: Any):
    """A deterministic non-model CPU executor with no file, model or GPS access."""

    def run(request: dict[str, Any]) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "job_id": request["job_id"],
            "lease_token": request["lease_token"],
            "input_revision": request["input_revision"],
            "task_type": request["task_type"],
            "processor_id": request["processor_id"],
            "state": "SUCCEEDED",
            "resolved_device": overrides.get("device") or request["resolved_device"],
            "runtime_provider": overrides.get("provider") or request["runtime_provider"],
            "precision": overrides.get("precision") or request["precision"],
            "progress": 1,
            "artifacts": list(overrides.get("artifacts") or []),
            "error": None,
            "completed_at": 2_000,
        }

    return run


def enqueue(
    catalog: Catalog,
    *,
    processor_id: str = CPU_PROCESSOR,
    task_type: str = "geo_extract",
    subject_id: str = "1",
    priority: int = 0,
    now_ms: int = 1_000,
    max_attempts: int = 3,
) -> int:
    with catalog.write() as connection:
        return jobs.enqueue(
            connection,
            subject_type="asset",
            subject_id=subject_id,
            input_revision="rev-1",
            task_type=task_type,
            processor_id=processor_id,
            now_ms=now_ms,
            priority=priority,
            max_attempts=max_attempts,
        )


# --------------------------------------------------------------- migration fixtures
def build_schema_v1(path: Path) -> None:
    """Create exactly what the frozen P1-01 build creates: 0001 plus schema_version=1."""
    script = (schema.MIGRATIONS_DIR / "0001_initial.sql").read_text(encoding="utf-8")
    connection = connect_rw(path)
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            f"{script}\n"
            f"INSERT INTO metadata(key, value) VALUES('{schema.SCHEMA_VERSION_KEY}', '1');\n"
            "COMMIT;\n"
        )
    finally:
        connection.close()


def seed_v1_rows(path: Path) -> dict[str, int]:
    """Two jobs, one asset with a location and a source: everything a v1 build can hold."""
    connection = connect_rw(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        source_id = int(
            connection.execute(
                "INSERT INTO source_roots (display_name, root_path, volume_hint, status) "
                "VALUES ('照片 库', 'C:\\照片 库', 'C:', 'ONLINE')"
            ).lastrowid
        )
        scan_id = int(
            connection.execute(
                "INSERT INTO scan_runs (source_id, started_at, finished_at, state) "
                "VALUES (?, 10, 20, 'SUCCEEDED')",
                (source_id,),
            ).lastrowid
        )
        connection.execute(
            "UPDATE source_roots SET last_successful_scan = ? WHERE source_id = ?",
            (scan_id, source_id),
        )
        asset_id = int(
            connection.execute(
                "INSERT INTO assets (sha256, byte_size, width, height, format, created_at, state) "
                "VALUES (?, 1234, 40, 30, 'JPEG', 7, 'ACTIVE')",
                (SHA,),
            ).lastrowid
        )
        location_id = int(
            connection.execute(
                "INSERT INTO file_locations "
                "(source_id, relative_path, path_key, asset_id, size, mtime_ns, "
                " last_seen_scan_id, availability) "
                "VALUES (?, '子目录/照 片.jpg', '子目录/照 片.jpg', ?, 1234, 99, ?, 'PRESENT')",
                (source_id, asset_id, scan_id),
            ).lastrowid
        )
        job_id = int(
            connection.execute(
                "INSERT INTO jobs (subject_type, subject_id, input_revision, task_type, "
                "processor_id, state, priority, attempts, max_attempts, run_after, lease_token, "
                "lease_expires_at, worker_id, progress, last_error_code, last_error_message, "
                "created_at, updated_at) "
                "VALUES ('scan_run', ?, '1', 'catalog_scan', 'catalog-scan@1', 'RUNNING', 5, 1, 3, "
                "100, 'legacy-lease-token', 999999, 'legacy-worker', 0.25, 'SOURCE_OFFLINE', "
                "'{\"code\":\"SOURCE_OFFLINE\"}', 100, 200)",
                (str(scan_id),),
            ).lastrowid
        )
        queued_job = int(
            connection.execute(
                "INSERT INTO jobs (subject_type, subject_id, input_revision, task_type, "
                "processor_id, state, priority, attempts, max_attempts, run_after, progress, "
                "created_at, updated_at) "
                "VALUES ('asset', '1', 'rev-1', 'geo_extract', 'geo-extract@1', 'QUEUED', 0, 0, 3, "
                "100, 0.0, 300, 300)"
            ).lastrowid
        )
        connection.execute("COMMIT")
    finally:
        connection.close()
    return {
        "source_id": source_id,
        "scan_id": scan_id,
        "asset_id": asset_id,
        "location_id": location_id,
        "job_id": job_id,
        "queued_job": queued_job,
    }


def job_rows(path: Path, columns: tuple[str, ...]) -> dict[int, tuple[Any, ...]]:
    """Every job keyed by ``job_id``, projected onto ``columns`` (order preserved)."""
    select = columns if "job_id" in columns else ("job_id", *columns)
    connection = open_readonly(path)
    try:
        rows = connection.execute(
            f"SELECT {', '.join(select)} FROM jobs ORDER BY job_id"
        ).fetchall()
        return {
            int(row["job_id"]): tuple(row[name] for name in columns) for row in rows
        }
    finally:
        connection.close()


def all_table_rows(path: Path, tables: tuple[str, ...]) -> dict[str, list[tuple[Any, ...]]]:
    connection = open_readonly(path)
    try:
        return {
            table: [
                tuple(row)
                for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            ]
            for table in tables
        }
    finally:
        connection.close()


# ------------------------------------------------------- scenario 1: schema migration
def test_v1_upgrade_preserves_every_existing_row_and_writes_a_backup(tmp_path: Path) -> None:
    path = db_path(tmp_path)
    build_schema_v1(path)
    ids = seed_v1_rows(path)
    assert inspect_database(path) == 1
    before = job_rows(path, V1_JOB_COLUMNS)
    other_before = all_table_rows(
        path, ("source_roots", "scan_runs", "assets", "file_locations")
    )

    result = migrate_database(path, data_root=data_root(tmp_path))

    assert result.previous_version == 1
    assert result.schema_version == SUPPORTED
    assert result.applied == tuple(range(2, SUPPORTED + 1))
    assert result.backup_path is not None and result.backup_path.is_file()
    backup = open_readonly(result.backup_path)
    try:
        assert schema.read_schema_version(backup) == 1
        assert set(schema.business_tables(backup)) == set(V1_TABLES)
        assert backup.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2
    finally:
        backup.close()

    # Every frozen column of every row survived byte for byte; the live schema is current.
    assert job_rows(path, V1_JOB_COLUMNS) == before
    assert all_table_rows(
        path, ("source_roots", "scan_runs", "assets", "file_locations")
    ) == other_before
    live = open_readonly(path)
    try:
        assert set(schema.business_tables(live)) == set(V5_TABLES)
        schema.verify_schema(live, SUPPORTED)
    finally:
        live.close()

    # Only ``requested_device`` receives a safe default; every other new column is NULL.
    after = job_rows(path, DISPATCH_COLUMNS)
    assert set(after) == set(before)
    assert {row[0] for row in after.values()} == {"auto"}
    for row in after.values():
        assert row[1:] == (None,) * (len(DISPATCH_COLUMNS) - 1)
    assert ids["job_id"] in after

    # The legacy RUNNING lease keeps its exact ADR-0002 meaning after the upgrade.
    legacy = job_rows(path, ("lease_token", "lease_expires_at", "worker_id"))[ids["job_id"]]
    assert legacy == ("legacy-lease-token", 999_999, "legacy-worker")


def test_an_interrupted_upgrade_rolls_back_and_keeps_every_row(tmp_path: Path, monkeypatch) -> None:
    path = db_path(tmp_path)
    build_schema_v1(path)
    seed_v1_rows(path)
    migrate_database(path, data_root=data_root(tmp_path))
    before = job_rows(path, V1_JOB_COLUMNS)
    directory = tmp_path / "broken-migrations"
    directory.mkdir()
    for _, source in schema.migration_files():
        (directory / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (directory / f"{NEXT_VERSION:04d}_broken.sql").write_text(
        "ALTER TABLE jobs ADD COLUMN probe_note TEXT;\n"
        "INSERT INTO metadata(key, value) VALUES('probe', 'yes');\n"
        "SELECT * FROM table_that_does_not_exist;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", directory)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", NEXT_VERSION)

    with pytest.raises(CatalogError) as error:
        migrate_database(path, data_root=data_root(tmp_path))

    assert error.value.code == MIGRATION_FAILED
    assert inspect_database(path) == SUPPORTED
    assert job_rows(path, V1_JOB_COLUMNS) == before
    connection = open_readonly(path)
    try:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()]
        assert "probe_note" not in columns
        probe = connection.execute("SELECT COUNT(*) FROM metadata WHERE key = 'probe'").fetchone()[0]
        assert probe == 0
    finally:
        connection.close()


def test_a_missing_target_schema_object_refuses_every_write(tmp_path: Path) -> None:
    path = db_path(tmp_path)
    migrate_database(path, data_root=data_root(tmp_path))
    writer = connect_rw(path)
    try:
        writer.execute("DROP TABLE scheduler_state")
    finally:
        writer.close()
    before_bytes = path.read_bytes()
    before_names = sorted(item.name for item in path.parent.iterdir())

    with pytest.raises(CatalogError) as error:
        inspect_database(path)
    assert error.value.code == SCHEMA_INCONSISTENT
    assert "scheduler_state" in " ".join(error.value.details["missing_objects"])
    with pytest.raises(CatalogError) as open_error:
        Catalog.open(path, data_root=data_root(tmp_path))
    assert open_error.value.code == SCHEMA_INCONSISTENT

    assert path.read_bytes() == before_bytes
    assert sorted(item.name for item in path.parent.iterdir()) == before_names


def test_a_higher_version_database_is_untouched_by_the_scheduler(tmp_path: Path) -> None:
    path = db_path(tmp_path)
    migrate_database(path, data_root=data_root(tmp_path))
    seed = Catalog.open(path, data_root=data_root(tmp_path))
    try:
        enqueue(seed, subject_id="future")
    finally:
        seed.close()
    writer = connect_rw(path)
    try:
        writer.execute(
            "UPDATE metadata SET value = ? WHERE key = ?",
            (str(int(schema.SUPPORTED_SCHEMA_VERSION) + 1), schema.SCHEMA_VERSION_KEY),
        )
    finally:
        writer.close()
    before_bytes = path.read_bytes()
    before_names = sorted(item.name for item in path.parent.iterdir())

    with pytest.raises(CatalogError) as error:
        Catalog.open(path, data_root=data_root(tmp_path))

    assert error.value.code == SCHEMA_TOO_NEW
    assert path.read_bytes() == before_bytes
    assert sorted(item.name for item in path.parent.iterdir()) == before_names
    assert not list(path.parent.glob("*-wal"))
    assert not list(path.parent.glob("*-shm"))


# ---------------------------------------------------- scenario 2/3: restart & takeover
def test_restart_takes_over_an_expired_lease_and_refuses_the_old_worker(tmp_path: Path) -> None:
    path = db_path(tmp_path)
    spec = non_model_spec()
    first_catalog = Catalog.open(path, data_root=data_root(tmp_path), lease_seconds=60)
    first = engine.Scheduler(first_catalog, [spec])
    first.record_capability(python_capability(), now_ms=1_000)
    qualify(first, spec)
    job_id = enqueue(first_catalog)
    stale_request = first.claim_next(worker_id="worker-old", now_ms=1_100)
    assert stale_request is not None
    first_catalog.close()  # the executing process dies without releasing its lease

    second_catalog = Catalog.open(path, data_root=data_root(tmp_path), lease_seconds=60)
    try:
        second = engine.Scheduler(second_catalog, [spec])
        assert second.claim_next(worker_id="worker-new", now_ms=1_100 + 59_000) is None
        taken = second.claim_next(worker_id="worker-new", now_ms=1_100 + 60_001)
        assert taken is not None and taken.job_id == job_id
        assert taken.lease_token != stale_request.lease_token
        row = second_catalog.read_one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        assert row["state"] == "RUNNING"
        assert row["attempts"] == 1  # the lost execution counts exactly once
        assert row["worker_id"] == "worker-new"

        # The old execution can neither extend nor commit anything any more.
        with pytest.raises(CatalogError) as heartbeat:
            second.heartbeat(
                job_id=job_id,
                lease_token=stale_request.lease_token,
                worker_id="worker-old",
                now_ms=61_200,
            )
        assert heartbeat.value.code == STALE_LEASE
        late = engine.JobResult.from_envelope(fake_executor()(stale_request.to_envelope()))
        before = dict(second_catalog.read_one("SELECT * FROM jobs WHERE job_id = ?", (job_id,)))
        with pytest.raises(CatalogError) as refused:
            second.submit(late, now_ms=61_300)
        assert refused.value.code == STALE_LEASE
        after = second_catalog.read_one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        assert dict(after) == before

        finished = second.submit(
            engine.JobResult.from_envelope(fake_executor()(taken.to_envelope())), now_ms=61_400
        )
        assert finished.state == "SUCCEEDED"
    finally:
        second_catalog.close()


def test_an_expired_lease_without_budget_fails_with_lease_expired(tmp_path: Path) -> None:
    path = db_path(tmp_path)
    spec = non_model_spec()
    first_catalog = Catalog.open(path, data_root=data_root(tmp_path), lease_seconds=60)
    first = engine.Scheduler(first_catalog, [spec])
    first.record_capability(python_capability(), now_ms=1_000)
    qualify(first, spec)
    job_id = enqueue(first_catalog, max_attempts=1)
    assert first.claim_next(worker_id="worker-old", now_ms=1_100) is not None
    first_catalog.close()

    second_catalog = Catalog.open(path, data_root=data_root(tmp_path), lease_seconds=60)
    try:
        second = engine.Scheduler(second_catalog, [spec])
        assert second.claim_next(worker_id="worker-new", now_ms=1_100 + 60_001) is None
        row = second_catalog.read_one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        assert row["state"] == "FAILED"
        assert row["attempts"] == 1
        assert row["last_error_code"] == LEASE_EXPIRED
        assert row["lease_token"] is None and row["worker_id"] is None
    finally:
        second_catalog.close()


# ------------------------------------------------------------- scenario 4: pause
def test_pause_survives_a_restart_and_a_running_job_still_finishes(tmp_path: Path) -> None:
    path = db_path(tmp_path)
    spec = non_model_spec()
    first_catalog = Catalog.open(path, data_root=data_root(tmp_path), lease_seconds=60)
    first = engine.Scheduler(first_catalog, [spec])
    first.record_capability(python_capability(), now_ms=1_000)
    qualify(first, spec)
    # The oldest equal-priority job is claimed first, so it is the one already running when
    # the pause arrives; the newer one must stay QUEUED.
    running_job = enqueue(first_catalog, subject_id="a")
    paused_job = enqueue(first_catalog, subject_id="b")
    request = first.claim_next(worker_id="w1", now_ms=1_100)
    assert request is not None and request.job_id == running_job
    paused = first.pause(now_ms=1_200)
    assert paused["state"] == "paused"
    first_catalog.close()

    second_catalog = Catalog.open(path, data_root=data_root(tmp_path), lease_seconds=60)
    try:
        second = engine.Scheduler(second_catalog, [spec])
        state = second.read_state()
        assert state.paused is True and state.revision == paused["revision"]
        assert second.run_once(fake_executor(), worker_id="w2", now_ms=1_300) is False
        pending = second_catalog.read_one("SELECT state FROM jobs WHERE job_id = ?", (paused_job,))
        assert pending[0] == "QUEUED"

        # The pre-pause execution finishes legitimately although claiming stays disabled.
        finished = second.submit(
            engine.JobResult.from_envelope(fake_executor()(request.to_envelope())), now_ms=1_400
        )
        assert finished.state == "SUCCEEDED"
        assert second.resume(now_ms=1_500)["state"] == "running"
        assert second.run_until_idle(fake_executor(), worker_id="w1", now_ms=1_600) == 1
        assert second.counts() == {"SUCCEEDED": 2}
    finally:
        second_catalog.close()


# --------------------------------------------- scenario 5: capability block/recovery
def test_a_capability_block_survives_a_restart_and_recovers(tmp_path: Path) -> None:
    path = db_path(tmp_path)
    gpu_spec = embedding_spec()
    cpu_spec = non_model_spec()
    first_catalog = Catalog.open(path, data_root=data_root(tmp_path), lease_seconds=60)
    first = engine.Scheduler(first_catalog, [gpu_spec, cpu_spec])
    first.record_capability(onnx_capability(gpu_health=None), now_ms=1_000)
    first.record_capability(python_capability(), now_ms=1_000)
    qualify(first, gpu_spec, device="gpu", provider_name="CUDAExecutionProvider", precision="FP32")
    qualify(first, cpu_spec)
    gpu_job = enqueue(
        first_catalog, processor_id=GPU_PROCESSOR, task_type="image_embedding", subject_id="gpu"
    )
    # Nothing is executed: the only queued job cannot be served and is blocked instead.
    assert first.run_until_idle(fake_executor(), worker_id="w1", now_ms=1_100) == 0
    blocked = first_catalog.read_one("SELECT * FROM jobs WHERE job_id = ?", (gpu_job,))
    assert blocked["state"] == "BLOCKED"
    assert blocked["blocking_reason"] == RUNTIME_CAPABILITY_UNAVAILABLE
    assert blocked["attempts"] == 0
    first_catalog.close()

    second_catalog = Catalog.open(path, data_root=data_root(tmp_path), lease_seconds=60)
    try:
        second = engine.Scheduler(second_catalog, [gpu_spec, cpu_spec])
        assert second.counts() == {"BLOCKED": 1}
        assert second.claim_next(worker_id="w2", now_ms=2_000) is None
        revision = second.record_capability(
            onnx_capability(gpu_health="healthy", now_ms=2_000), now_ms=2_000
        )
        resumed = second.recover_blocked(
            now_ms=2_100, capability_revision=revision.capability_revision
        )
        assert resumed == [gpu_job]
        assert second.run_until_idle(fake_executor(), worker_id="w2", now_ms=2_200) == 1
        assert second.counts() == {"SUCCEEDED": 1}
    finally:
        second_catalog.close()


# --------------------------------------------------- scenario 7: fairness & restart
def test_fairness_and_ordering_survive_a_restart(tmp_path: Path) -> None:
    path = db_path(tmp_path)
    spec = non_model_spec()
    first_catalog = Catalog.open(path, data_root=data_root(tmp_path), lease_seconds=60)
    first = engine.Scheduler(first_catalog, [spec])
    first.record_capability(python_capability(), now_ms=1_000)
    qualify(first, spec)
    first.configure_aging_window(aging_window_ms=1_000, now_ms=0)
    backlog = enqueue(first_catalog, subject_id="old", priority=0, now_ms=0)
    for step in range(3):
        enqueue(first_catalog, subject_id=f"new-{step}", priority=10, now_ms=step * 1_000)
    first_catalog.close()

    second_catalog = Catalog.open(path, data_root=data_root(tmp_path), lease_seconds=60)
    try:
        second = engine.Scheduler(second_catalog, [spec])
        # The persisted aging window survives, so the selection is reproducible after a
        # restart instead of silently reverting to a build default.
        assert second.read_state().aging_window_ms == 1_000
        chosen = []
        for now_ms in (0, 1_000, 2_000):
            request = second.claim_next(worker_id="w", now_ms=now_ms)
            assert request is not None and request.job_id != backlog
            chosen.append(request.job_id)
        assert chosen == sorted(chosen)
        final = second.claim_next(worker_id="w", now_ms=10_000)
        assert final is not None and final.job_id == backlog
    finally:
        second_catalog.close()


def test_the_v1_fixture_really_is_the_frozen_schema(tmp_path: Path) -> None:
    """Guard rail: the migration fixtures above use the frozen 0001 contract, nothing more."""
    path = db_path(tmp_path)
    build_schema_v1(path)
    connection = open_readonly(path)
    try:
        assert set(schema.business_tables(connection)) == set(V1_TABLES)
        columns = [row[1] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()]
        assert tuple(columns) == V1_JOB_COLUMNS
    finally:
        connection.close()
