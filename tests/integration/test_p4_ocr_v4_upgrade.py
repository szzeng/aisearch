"""Versioned OCR upgrades preserve old bytes, leases and the current searchable result."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from p3_ocr_fixtures import Harness, write_blank_image

from photo_search.artifacts.store import ArtifactStore
from photo_search.catalog.errors import ARTIFACT_CONFLICT, STALE_LEASE, CatalogError
from photo_search.ingestion.service import CatalogService
from photo_search.jobs import queue as jobs
from photo_search.ocr import contract
from photo_search.ocr import processor as ocr_processor
from photo_search.ocr import service as ocr_service
from photo_search.ocr.engine import OcrDocument, OcrRegion
from photo_search.scheduler.contracts import JobResult, canonical_json
from photo_search.scheduler.projection import ocr_summary
from photo_search.search import documents
from photo_search.search.tokens import TOKENIZER_VERSION, build_match, index_text
from tests.integration.test_p4_02y_l_identity import (
    FakeEngineCache,
    enqueue,
    install_runtime_evidence,
    jobs_for,
    make_asset,
    run,
)

HISTORY = json.loads(
    (Path(__file__).parents[1] / "fixtures/ocr/historical-v1-v2.json").read_text(encoding="utf-8")
)


@pytest.fixture()
def harness(tmp_path):
    instance = Harness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def _seed_job(harness, asset_id, processor_id):
    with harness.catalog.write() as connection:
        return jobs.enqueue(
            connection, subject_type="asset", subject_id=str(asset_id),
            input_revision=harness.sha_of(asset_id), task_type=contract.OCR_TASK_TYPE,
            processor_id=processor_id, now_ms=harness.clock(),
        )


def _seed_historical_current(harness, asset_id, processor_id):
    """Install actual pre-v4 artifact bytes and matching current rows, never execute old code."""
    sample = next(
        (sample for sample in HISTORY["artifacts"] if sample["processor_id"] == processor_id),
        None,
    )
    if sample is not None:
        payload = deepcopy(sample["payload"])
    else:
        region = OcrRegion(
            index=0,
            polygon=((4.0, 3.0), (80.0, 3.0), (80.0, 24.0), (4.0, 24.0)),
            box=(4.0, 3.0, 80.0, 24.0),
            text="historical-v3 text",
            confidence=0.91,
        )
        payload = ocr_processor.build_payload(
            input_revision=harness.sha_of(asset_id),
            location_id=None,
            document=OcrDocument(
                original_size=(901, 501), working_size=(901, 501), scale=1.0,
                slice_count=1, regions=(region,), elapsed_ms=1,
            ),
            decode_format="JPEG",
            processor_id=processor_id,
        )
    payload["input_revision"] = harness.sha_of(asset_id)
    payload["location_id"] = harness.catalog.read_one(
        "SELECT location_id FROM file_locations WHERE asset_id=?", (asset_id,),
    )["location_id"]
    ocr_processor.validate_payload(payload)
    ref = ocr_processor.artifact_ref(payload)
    body = canonical_json(payload).encode("utf-8")
    storage_uri = "artifacts/historical-fixture/" + ref["checksum_sha256"] + ".json"
    path = harness.data_root / storage_uri
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    job_id = _seed_job(harness, asset_id, processor_id)
    spec_revision = (
        "ocr-text-ppocrv4-mobile-v1" if processor_id == "ocr-text@1"
        else "ocr-text-ppocrv4-mobile-" + ("cpu" if "cpu" in processor_id else "cuda")
        + "-fp32-v" + ("3" if "@3" in processor_id else "2")
    )
    region = payload["regions"][0]
    with harness.catalog.write() as connection:
        connection.execute("UPDATE jobs SET state='SUCCEEDED' WHERE job_id=?", (job_id,))
        connection.execute(
            "INSERT INTO artifacts (artifact_id, processor_id, input_revision, artifact_type, "
            "artifact_revision, media_type, checksum_sha256, byte_size, storage_uri, job_id, "
            "lifecycle_state, created_at, updated_at) VALUES (?, ?, ?, 'structured', ?, "
            "'application/json', ?, ?, ?, ?, 'PUBLISHED', 1, 1)",
            (ref["artifact_id"], processor_id, payload["input_revision"],
             contract.OCR_ARTIFACT_REVISION, ref["checksum_sha256"], len(body), storage_uri, job_id),
        )
        connection.execute(
            "INSERT INTO asset_ocr_text (asset_id, ocr_document_id, input_revision, location_id, "
            "text_revision, processor_id, spec_revision, model_fingerprint, artifact_id, "
            "region_count, min_confidence, mean_confidence, max_confidence, search_text, "
            "tokenizer_version, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, "
            "1, ?, ?, ?, ?, ?, 1, 1)",
            (asset_id, ocr_processor.ocr_document_id_for(payload["input_revision"], 1, processor_id),
             payload["input_revision"], payload["location_id"], processor_id, spec_revision,
             payload["model_fingerprint"], ref["artifact_id"], region["confidence"],
             region["confidence"], region["confidence"], index_text(payload["original_text"]),
             TOKENIZER_VERSION),
        )
        connection.execute(
            "INSERT INTO asset_ocr_regions (asset_id, region_index, text_revision, box_json, confidence) "
            "VALUES (?, 0, 1, ?, ?)",
            (asset_id, canonical_json({"box": region["box"]}), region["confidence"]),
        )
        documents.sync_document(connection, asset_id, now_ms=harness.clock())
    ocr_service.catch_up_projection(harness.catalog)
    artifact = harness.catalog.read_one("SELECT * FROM artifacts WHERE artifact_id=?", (ref["artifact_id"],))
    assert ArtifactStore.open(harness.data_root).read(artifact) == body
    return dict(artifact), body


def _current_state(harness, asset_id):
    return {
        "current": dict(harness.ocr_document(asset_id)),
        "regions": [dict(row) for row in harness.ocr_regions(asset_id)],
        "projection": dict(harness.document_row(asset_id)),
        "queue": [dict(row) for row in harness.catalog.read_all(
            "SELECT * FROM search_projection_queue WHERE asset_id=? ORDER BY asset_id", (asset_id,),
        )],
    }


def test_v4_reprocess_is_counted_despite_current_v3_and_repeated_enqueue_is_idempotent(
    harness, monkeypatch,
):
    asset_id = make_asset(harness)
    _seed_historical_current(harness, asset_id, contract.OCR_V3_CPU_PROCESSOR_ID)

    first = enqueue(harness, asset_id, device="cpu")
    assert first == {"targets": 1, "queued": 1, "rewound": 0, "live": 0}
    job = jobs_for(harness, asset_id)[contract.OCR_CPU_PROCESSOR_ID]
    assert job["state"] == "QUEUED"
    assert ocr_service.pending_job_count(harness.catalog) == 1
    summary = ocr_summary(harness.catalog.db_path)
    assert summary["documents"] == 1
    assert summary["current_results_by_processor"][contract.OCR_V3_CPU_PROCESSOR_ID] == 1
    assert summary["pending_jobs"] == 1 and summary["state"] == "LAGGING"

    repeated = enqueue(harness, asset_id, device="cpu")
    assert repeated == {"targets": 1, "queued": 0, "rewound": 0, "live": 0}
    assert len(jobs_for(harness, asset_id)) == 2

    run_result = run(harness, monkeypatch, cache=FakeEngineCache())
    assert run_result["steps"] == 1
    assert ocr_service.pending_job_count(harness.catalog) == 0
    current = harness.ocr_document(asset_id)
    assert current["processor_id"] == contract.OCR_CPU_PROCESSOR_ID
    ocr_service.catch_up_projection(harness.catalog)
    assert ocr_summary(harness.catalog.db_path)["state"] == "SYNCED"

    # A normal repeat keeps the successful @4 row intact; only explicit --force rewinds it.
    repeated_success = enqueue(harness, asset_id, device="cpu")
    assert repeated_success["queued"] == 0 and repeated_success["rewound"] == 0
    assert run(harness, monkeypatch, cache=FakeEngineCache())["steps"] == 0
    assert jobs_for(harness, asset_id)[contract.OCR_CPU_PROCESSOR_ID]["state"] == "SUCCEEDED"


@pytest.mark.parametrize("unavailable_kind", ("source_offline", "location_missing"))
def test_unavailable_original_is_skipped_and_keeps_current_ocr(harness, unavailable_kind):
    asset_id = make_asset(harness)
    _seed_historical_current(harness, asset_id, contract.OCR_V3_CPU_PROCESSOR_ID)
    location = harness.catalog.read_one(
        "SELECT location_id, source_id FROM file_locations WHERE asset_id=?", (asset_id,),
    )
    with harness.catalog.write() as connection:
        if unavailable_kind == "source_offline":
            connection.execute(
                "UPDATE source_roots SET status='OFFLINE' WHERE source_id=?",
                (location["source_id"],),
            )
        else:
            connection.execute(
                "UPDATE file_locations SET availability='MISSING' WHERE location_id=?",
                (location["location_id"],),
            )

    targets = ocr_service.ocr_targets(harness.catalog)
    assert targets == []
    skipped = ocr_service.enqueue_targets(
        harness.catalog, targets, now_ms=harness.clock(), requested_device="cpu",
    )
    assert skipped["queued"] == 0
    assert harness.ocr_document(asset_id)["processor_id"] == contract.OCR_V3_CPU_PROCESSOR_ID
    assert contract.OCR_CPU_PROCESSOR_ID not in jobs_for(harness, asset_id)
    summary = ocr_summary(harness.catalog.db_path)
    assert summary["documents"] == 1 and summary["unavailable_assets"] == 1


def test_live_v3_lease_delays_v4_and_late_v3_submission_cannot_replace_v4(
    harness, monkeypatch,
):
    monkeypatch.setattr(
        "photo_search.scheduler.projection.time.time_ns",
        lambda: harness.clock() * 1_000_000,
    )
    asset_id = make_asset(harness)
    _seed_historical_current(harness, asset_id, contract.OCR_V3_CPU_PROCESSOR_ID)
    old_job = harness.catalog.read_one(
        "SELECT job_id FROM jobs WHERE subject_id=? AND processor_id=?",
        (str(asset_id), contract.OCR_V3_CPU_PROCESSOR_ID),
    )
    with harness.catalog.write() as connection:
        connection.execute("UPDATE jobs SET state='QUEUED' WHERE job_id=?", (old_job["job_id"],))
        old_lease = jobs.claim_job(
            connection, job_id=int(old_job["job_id"]), worker_id="old-v3-worker",
            now_ms=harness.clock(), lease_seconds=60,
        )

    held = enqueue(harness, asset_id, device="cpu")
    assert held == {"targets": 1, "queued": 0, "rewound": 0, "live": 1}
    assert contract.OCR_CPU_PROCESSOR_ID not in jobs_for(harness, asset_id)
    assert ocr_summary(harness.catalog.db_path)["retired_live_leases"] == 1

    with harness.catalog.write() as connection:
        connection.execute(
            "UPDATE jobs SET lease_expires_at=? WHERE job_id=?",
            (harness.clock() - 1, int(old_job["job_id"])),
        )
    assert enqueue(harness, asset_id, device="cpu")["queued"] == 1
    run(harness, monkeypatch, cache=FakeEngineCache())
    current_after_v4 = dict(harness.ocr_document(asset_id))
    assert current_after_v4["processor_id"] == contract.OCR_CPU_PROCESSOR_ID

    with pytest.raises(CatalogError):
        with harness.catalog.write() as connection:
            jobs.validate_submission(
                connection, job_id=int(old_job["job_id"]), lease_token=old_lease.lease_token,
                now_ms=harness.clock(), input_revision=harness.sha_of(asset_id),
                processor_id=contract.OCR_V3_CPU_PROCESSOR_ID,
            )
    assert dict(harness.ocr_document(asset_id)) == current_after_v4


@pytest.mark.parametrize("has_current_document", (False, True))
@pytest.mark.parametrize(
    ("job_state", "expected_state"),
    ((None, "LAGGING"), ("QUEUED", "LAGGING"), ("RUNNING", "LAGGING"),
     ("RETRY_WAIT", "LAGGING"), ("BLOCKED", "BLOCKED"), ("FAILED", "FAILED")),
)
def test_stale_ocr_is_never_reported_as_not_run_or_synced(
    harness, has_current_document, job_state, expected_state,
):
    asset_id = make_asset(harness)
    _seed_historical_current(harness, asset_id, "ocr-text-cpu-fp32@2")
    if has_current_document:
        write_blank_image(harness.library / "current.jpg", size=(901, 501))
        harness.scan()
        _seed_historical_current(harness, harness.asset_id("current.jpg"), "ocr-text-cpu-fp32@2")
    # Retiring a processor does not make matching-content historical results stale.
    before = ocr_summary(harness.catalog.db_path)
    assert before["state"] == "SYNCED" and before["stale_documents"] == 0
    assert before["documents"] == 1 + int(has_current_document)
    with harness.catalog.write() as connection:
        connection.execute(
            "UPDATE asset_ocr_text SET input_revision=? WHERE asset_id=?", ("0" * 64, asset_id),
        )
    ocr_service.catch_up_projection(harness.catalog)
    assert asset_id not in harness.text_hits(build_match("historical").expression)
    if job_state is not None:
        job_id = _seed_job(harness, asset_id, contract.OCR_CPU_PROCESSOR_ID)
        with harness.catalog.write() as connection:
            if job_state == "RUNNING":
                jobs.claim_job(connection, job_id=job_id, worker_id="summary-test",
                               now_ms=harness.clock(), lease_seconds=60)
            else:
                connection.execute("UPDATE jobs SET state=? WHERE job_id=?", (job_state, job_id))
    summary = ocr_summary(harness.catalog.db_path)
    assert summary["stale_documents"] == 1
    assert summary["documents"] == int(has_current_document)
    assert summary["state"] == expected_state


@pytest.mark.parametrize("processor_id", ("ocr-text-cpu-fp32@2", "ocr-text-cuda-fp32@2"))
def test_real_v2_retirement_preserves_live_and_anomalous_leases_without_bulk_enqueue(harness, processor_id):
    for index in range(8):
        write_blank_image(harness.library / f"version-{index}.jpg", size=(800 + index, 500))
    harness.scan()
    asset_ids = [harness.asset_id(f"version-{index}.jpg") for index in range(8)]
    job_ids = [_seed_job(harness, asset_id, processor_id) for asset_id in asset_ids[:7]]
    unknown = _seed_job(harness, asset_ids[7], "unknown-ocr@9")
    queued, retry, blocked, live, expired, anomalous, terminal = job_ids
    now = harness.clock()
    with harness.catalog.write() as connection:
        connection.execute("UPDATE jobs SET state='RETRY_WAIT' WHERE job_id=?", (retry,))
        connection.execute("UPDATE jobs SET state='BLOCKED' WHERE job_id=?", (blocked,))
        leases = {
            job_id: jobs.claim_job(connection, job_id=job_id, worker_id="historical-worker",
                                   now_ms=now, lease_seconds=60)
            for job_id in (live, expired, anomalous)
        }
        connection.execute("UPDATE jobs SET lease_expires_at=? WHERE job_id=?", (now - 1, expired))
        connection.execute("UPDATE jobs SET lease_expires_at=NULL WHERE job_id=?", (anomalous,))
        connection.execute("UPDATE jobs SET state='SUCCEEDED' WHERE job_id=?", (terminal,))
    protected = {
        job_id: dict(harness.catalog.read_one("SELECT * FROM jobs WHERE job_id=?", (job_id,)))
        for job_id in (live, anomalous, terminal, unknown)
    }
    with CatalogService.open(data_root=harness.data_root, clock=harness.clock,
                             wall_clock_ms=harness.clock, lease_seconds=60) as reopened:
        for job_id in (queued, retry, blocked, expired):
            row = reopened.catalog.read_one("SELECT * FROM jobs WHERE job_id=?", (job_id,))
            assert row["state"] == "CANCELLED" and row["last_error_code"] == "OCR_PROCESSOR_RETIRED"
            assert row["lease_token"] is row["lease_expires_at"] is row["worker_id"] is None
        for job_id, before in protected.items():
            assert dict(reopened.catalog.read_one("SELECT * FROM jobs WHERE job_id=?", (job_id,))) == before
        assert reopened.catalog.read_one("SELECT count(*) AS n FROM jobs WHERE processor_id LIKE '%@4'")["n"] == 0
    for asset_id in (asset_ids[3], asset_ids[5]):
        assert enqueue(harness, asset_id, device="cpu") == {
            "targets": 1, "queued": 0, "rewound": 0, "live": 1,
        }
    with pytest.raises(CatalogError) as caught:
        with harness.catalog.write() as connection:
            jobs.validate_submission(
                connection, job_id=expired, lease_token=leases[expired].lease_token,
                now_ms=now, input_revision=harness.sha_of(asset_ids[4]), processor_id=processor_id,
            )
    assert caught.value.code in (STALE_LEASE, "LEASE_CONFLICT")
    assert ocr_service.pending_job_count(harness.catalog) == 0
    assert ocr_summary(harness.catalog.db_path)["retired_pending_jobs"] == 2


@pytest.mark.parametrize("processor_id", (
    "ocr-text@1", "ocr-text-cpu-fp32@2", "ocr-text-cuda-fp32@2",
    "ocr-text-cpu-fp32@3", "ocr-text-cuda-fp32@3",
))
@pytest.mark.parametrize("device", ("cpu", "gpu"))
def test_historical_current_stays_searchable_until_successful_v4_replacement(harness, monkeypatch, processor_id, device):
    asset_id = make_asset(harness)
    old_artifact, old_bytes = _seed_historical_current(harness, asset_id, processor_id)
    before = _current_state(harness, asset_id)
    assert harness.text_hits(build_match("historical").expression) == [asset_id]
    with CatalogService.open(data_root=harness.data_root, clock=harness.clock,
                             wall_clock_ms=harness.clock, lease_seconds=60):
        pass
    assert _current_state(harness, asset_id) == before

    selected = contract.OCR_CPU_PROCESSOR_ID if device == "cpu" else contract.OCR_GPU_PROCESSOR_ID
    for failure, cancel in (("OCR_SYNTHETIC_FAILURE", None), (None, lambda: True)):
        enqueue(harness, asset_id, device=device, force=True)
        run(harness, monkeypatch, cache=FakeEngineCache(cpu_failure=failure, gpu_failure=failure), cancel=cancel)
        assert _current_state(harness, asset_id) == before
        assert harness.text_hits(build_match("historical").expression) == [asset_id]
        assert jobs_for(harness, asset_id)[selected]["state"] == ("FAILED" if failure else "CANCELLED")
        if failure:
            summary = ocr_summary(harness.catalog.db_path)
            assert summary["documents"] == 1
            assert summary["failed_jobs"] == 1 and summary["state"] == "FAILED"

    enqueue(harness, asset_id, device=device, force=True)
    run(harness, monkeypatch, cache=FakeEngineCache())
    after = dict(harness.ocr_document(asset_id))
    assert after["processor_id"] == selected
    assert after["spec_revision"].endswith("-v4")
    assert after["text_revision"] == before["current"]["text_revision"] + 1
    assert all(row["text_revision"] == after["text_revision"] for row in harness.ocr_regions(asset_id))
    assert harness.catalog.read_one("SELECT count(*) AS n FROM artifacts")["n"] == 2
    ocr_service.catch_up_projection(harness.catalog)
    assert harness.text_hits(build_match("historical").expression) == []
    assert harness.text_hits(build_match("合成文字").expression) == [asset_id]
    stored_old = harness.catalog.read_one("SELECT * FROM artifacts WHERE artifact_id=?", (old_artifact["artifact_id"],))
    assert dict(stored_old) == old_artifact
    assert ArtifactStore.open(harness.data_root).read(stored_old) == old_bytes
    assert hashlib.sha256(old_bytes).hexdigest() == old_artifact["checksum_sha256"]
    current_artifact = harness.catalog.read_one("SELECT * FROM artifacts WHERE artifact_id=?", (after["artifact_id"],))
    current_payload = json.loads(ArtifactStore.open(harness.data_root).read(current_artifact))
    ocr_processor.validate_payload(current_payload)
    assert current_payload["processor_id"] == selected
    assert current_payload["config_hash"] == ocr_processor.ocr_processor_spec(device).config_hash


def test_failed_atomic_v4_publication_restores_historical_current_and_fts(harness, monkeypatch):
    asset_id = make_asset(harness)
    old_artifact, old_bytes = _seed_historical_current(harness, asset_id, "ocr-text-cpu-fp32@2")
    before = _current_state(harness, asset_id)
    enqueue(harness, asset_id, device="cpu")
    install_runtime_evidence(monkeypatch)
    scheduler = ocr_service.build_scheduler(harness.catalog)
    ocr_service.record_runtime_evidence(scheduler, scheduler.spec_for(contract.OCR_CPU_PROCESSOR_ID),
                                      FakeEngineCache(), now_ms=harness.clock())
    request = scheduler.claim_next(worker_id="v4-publication", now_ms=harness.clock())
    raw = ocr_processor.ocr_executor(harness.catalog, engine_cache=FakeEngineCache())(request.to_envelope())
    original_persist = ocr_processor.persist_ocr_document

    def fail_after_current_write(*args, **kwargs):
        assert original_persist(*args, **kwargs)
        raise CatalogError(ARTIFACT_CONFLICT, "synthetic failure after current document writes")

    with monkeypatch.context() as patch:
        patch.setattr(ocr_processor, "persist_ocr_document", fail_after_current_write)
        with pytest.raises(CatalogError) as caught:
            scheduler.submit(JobResult.from_envelope(raw), now_ms=harness.clock())
    assert caught.value.code == ARTIFACT_CONFLICT
    assert _current_state(harness, asset_id) == before
    assert harness.text_hits(build_match("historical").expression) == [asset_id]
    assert harness.catalog.read_one("SELECT count(*) AS n FROM artifacts")["n"] == 1
    assert ArtifactStore.open(harness.data_root).read(old_artifact) == old_bytes
    assert scheduler.submit(JobResult.from_envelope(raw), now_ms=harness.clock()).state == "SUCCEEDED"
    committed = _current_state(harness, asset_id)
    with pytest.raises(CatalogError):
        scheduler.submit(JobResult.from_envelope(raw), now_ms=harness.clock())
    assert _current_state(harness, asset_id) == committed
