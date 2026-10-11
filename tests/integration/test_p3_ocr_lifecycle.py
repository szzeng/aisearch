"""P3-01 D-level: the OCR document under fault injection, replay and content changes.

Named scenarios: missing and corrupted pinned weights, a submission whose content changed
before the commit, a crash between artifact publication and the SQLite commit, a forced
re-recognition (same logical job), a cancelled execution, a late result after a real
cancellation, and an exhausted wall-clock budget.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from p3_ocr_fixtures import (
    SKIP_WITHOUT_FONT,
    SKIP_WITHOUT_WEIGHTS,
    Harness,
    write_blank_image,
    write_text_image,
)

from photo_search.artifacts.store import ArtifactStore
from photo_search.catalog.errors import (
    OCR_DEADLINE_EXCEEDED,
    OCR_WEIGHTS_HASH_MISMATCH,
    OCR_WEIGHTS_MISSING,
    RUNTIME_CAPABILITY_UNAVAILABLE,
    STALE_LEASE,
    CatalogError,
)
from photo_search.jobs import queue as jobs_module
from photo_search.ocr import contract
from photo_search.ocr.processor import ocr_executor, ocr_processor_spec
from photo_search.ocr.service import (
    build_scheduler,
    catch_up_projection,
    enqueue_targets,
    reconcile_retired_ocr_jobs,
    record_runtime_evidence,
    run_queue,
)
from photo_search.search import documents as search_documents


@pytest.fixture()
def harness(tmp_path: Path) -> Harness:
    instance = Harness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def qualified_scheduler(harness: Harness) -> Any:
    """A scheduler with stub CPU qualification and no GPU qualification."""
    spec = ocr_processor_spec()
    scheduler = build_scheduler(harness.catalog, spec)

    class StubCache:
        def engine(self, _data_root: Path, *, resolved_device: str) -> Any:
            if resolved_device == "gpu":
                raise CatalogError(RUNTIME_CAPABILITY_UNAVAILABLE, "GPU is not qualified")
            return SimpleNamespace(runtime_provider="CPUExecutionProvider")

    record_runtime_evidence(
        scheduler,
        spec,
        StubCache(),
        data_root=harness.data_root,
        now_ms=1_000,
        note="P3-01 lifecycle fixture",
    )
    return scheduler


def enqueue_one(harness: Harness, asset_id: int) -> None:
    enqueue_targets(
        harness.catalog,
        [{"asset_id": int(asset_id), "input_revision": harness.sha_of(asset_id)}],
        now_ms=harness.clock(),
        requested_device="cpu",
    )


def run_with(harness: Harness, scheduler: Any, **options: Any) -> Any:
    executor = ocr_executor(harness.catalog, data_root=harness.data_root, **options)
    return scheduler.run_until_idle(executor, worker_id="w1", now_ms=harness.clock())


def ocr_job(harness: Harness, asset_id: int | None = None) -> Any:
    if asset_id is None:
        return harness.catalog.read_one(
            "SELECT * FROM jobs WHERE task_type = ? ORDER BY job_id DESC LIMIT 1",
            (contract.OCR_TASK_TYPE,),
        )
    return harness.catalog.read_one(
        "SELECT * FROM jobs WHERE task_type = ? AND subject_id = ? ORDER BY job_id DESC LIMIT 1",
        (contract.OCR_TASK_TYPE, str(int(asset_id))),
    )


def count(harness: Harness, table: str) -> int:
    return int(harness.catalog.read_one(f"SELECT COUNT(*) AS total FROM {table}")["total"])


def assert_no_ocr_writes(harness: Harness) -> None:
    assert count(harness, "asset_ocr_text") == 0
    assert count(harness, "asset_ocr_regions") == 0
    assert count(harness, "artifacts") == 0
    assert (
        harness.catalog.read_one(
            "SELECT COUNT(*) AS total FROM search_projection_queue WHERE cause = 'OCR_TEXT_CHANGED'"
        )["total"]
        == 0
    )


# ------------------------------------------------------------------- weights preflight
@SKIP_WITHOUT_FONT
def test_missing_pinned_weights_block_the_job_without_burning_an_attempt(
    harness: Harness,
) -> None:
    write_text_image(harness.library / "a.jpg", ["编号 1"], size=(600, 300))
    harness.scan()
    asset_id = harness.asset_id("a.jpg")
    scheduler = qualified_scheduler(harness)
    enqueue_one(harness, asset_id)

    scheduler.run_until_idle(
        ocr_executor(harness.catalog, data_root=harness.data_root),
        worker_id="w1",
        now_ms=harness.clock(),
    )
    job = ocr_job(harness, asset_id)
    assert str(job["state"]) == "BLOCKED"
    assert str(job["last_error_code"]) == OCR_WEIGHTS_MISSING
    assert str(job["blocking_reason"]) == "SOURCE_UNAVAILABLE"
    assert int(job["attempts"]) == 0
    text = str(job["last_error_message"])
    assert "C:\\" not in text and "\\\\" not in text
    assert_no_ocr_writes(harness)


@SKIP_WITHOUT_WEIGHTS
@SKIP_WITHOUT_FONT
def test_a_pinned_file_with_different_bytes_blocks_the_job(harness: Harness) -> None:
    write_text_image(harness.library / "b.jpg", ["编号 2"], size=(600, 300))
    harness.scan()
    asset_id = harness.asset_id("b.jpg")
    harness.install_weights(corrupt="detection")
    scheduler = qualified_scheduler(harness)
    enqueue_one(harness, asset_id)

    run_with(harness, scheduler)
    job = ocr_job(harness, asset_id)
    assert str(job["state"]) == "BLOCKED"
    assert str(job["last_error_code"]) == OCR_WEIGHTS_HASH_MISMATCH
    assert_no_ocr_writes(harness)


@SKIP_WITHOUT_WEIGHTS
@SKIP_WITHOUT_FONT
def test_an_incomplete_weight_set_is_reported_before_anything_is_queued(harness: Harness) -> None:
    harness.install_weights(omit="recognition")
    from photo_search.ocr.weights import resolve, verify

    with pytest.raises(CatalogError) as error:
        verify(resolve(harness.data_root))
    assert error.value.code == OCR_WEIGHTS_MISSING
    assert error.value.details["role"] == "recognition"
    # A work command fails before it touches the queue, so no blocked job is created.
    with pytest.raises(CatalogError):
        run_queue(harness.catalog, data_root=harness.data_root)
    assert count(harness, "jobs") == 0
    assert count(harness, "asset_ocr_text") == 0


# ------------------------------------------------------------------- content changes
@SKIP_WITHOUT_WEIGHTS
@SKIP_WITHOUT_FONT
def test_a_replaced_original_never_inherits_the_previous_recognition(harness: Harness) -> None:
    """A replacement is a new content identity: the old text can never follow it."""
    harness.install_weights()
    write_text_image(harness.library / "change.jpg", ["旧编号 111"], size=(700, 300))
    harness.scan()
    first_asset = harness.asset_id("change.jpg")
    first_revision = harness.sha_of(first_asset)
    harness.recognize()
    assert "111" in str(harness.ocr_document(first_asset)["search_text"])

    # The content really changes: ADR-0002 re-binds the location to a new content identity,
    # so the new content is a *new asset* and the old document cannot follow it.
    write_text_image(harness.library / "change.jpg", ["新编号 222 999"], size=(700, 300))
    harness.scan()
    second_asset = harness.asset_id("change.jpg")
    second_revision = harness.sha_of(second_asset)
    assert second_revision != first_revision
    assert search_documents.document_source(harness.catalog.connection, second_asset)["ocr_text"] == ""
    harness.recognize()
    catch_up_projection(harness.catalog)
    # Neither the old digits nor the old artifact can be reached through the new asset.
    assert harness.text_hits('"222999"*') == [second_asset]
    assert harness.text_hits('"111"*') != [second_asset]
    text_row = harness.ocr_document(second_asset)
    assert str(text_row["input_revision"]) == second_revision
    assert "222" in str(text_row["search_text"]) and "999" in str(text_row["search_text"])
    assert "111" not in str(text_row["search_text"])
    assert str(text_row["artifact_id"]) != str(harness.ocr_document(first_asset)["artifact_id"])


@SKIP_WITHOUT_WEIGHTS
@SKIP_WITHOUT_FONT
def test_an_old_revision_document_never_reaches_the_projection(harness: Harness) -> None:
    harness.install_weights()
    write_text_image(harness.library / "revision.jpg", ["编号 20260914"], size=(800, 300))
    harness.scan()
    asset_id = harness.asset_id("revision.jpg")
    harness.recognize()
    current = str(harness.sha_of(asset_id))
    assert "20260914" in str(harness.ocr_document(asset_id)["search_text"])

    # A late document for an *older* content revision is exactly the case the projection
    # must never serve: the row is history, not a current fact.
    with harness.catalog.write() as connection:
        connection.execute(
            "UPDATE asset_ocr_text SET input_revision = ? WHERE asset_id = ?",
            ("0" * 64, asset_id),
        )
    assert (
        harness.catalog.read_one(
            "SELECT cause FROM search_projection_queue WHERE asset_id = ?", (asset_id,)
        )["cause"]
        == "OCR_TEXT_CHANGED"
    )
    catch_up_projection(harness.catalog)
    assert search_documents.document_source(harness.catalog.connection, asset_id)["ocr_text"] == ""
    assert harness.text_hits('"20260914"*') == []
    from photo_search.config import catalog_db_path
    from photo_search.scheduler.projection import ocr_summary

    summary = ocr_summary(catalog_db_path(harness.data_root), weights_present=True)
    assert summary["stale_documents"] == 1 and summary["documents"] == 0
    assert summary["state"] == "LAGGING"

    # Re-recognising the current content restores the current document, and the stale row is
    # replaced rather than duplicated.
    harness.recognize(force=True)
    catch_up_projection(harness.catalog)
    restored = harness.ocr_document(asset_id)
    assert str(restored["input_revision"]) == current
    assert int(restored["text_revision"]) == 2
    assert count(harness, "asset_ocr_text") == 1
    assert harness.text_hits('"20260914"*') == [asset_id]


def scheduler_engine_result(raw: dict[str, Any]) -> Any:
    from photo_search.scheduler.contracts import JobResult

    return JobResult.from_envelope(raw)


@SKIP_WITHOUT_WEIGHTS
@SKIP_WITHOUT_FONT
def test_a_crash_before_the_commit_leaves_no_document_and_can_be_retried(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.install_weights()
    write_text_image(harness.library / "crash.jpg", ["编号 333"], size=(700, 300))
    harness.scan()
    asset_id = harness.asset_id("crash.jpg")
    scheduler = qualified_scheduler(harness)
    enqueue_one(harness, asset_id)
    request = scheduler.claim_next(worker_id="w1", now_ms=harness.clock())
    assert request is not None
    executor = ocr_executor(harness.catalog, data_root=harness.data_root)
    result = scheduler_engine_result(executor(request.to_envelope()))

    def boom(*_args: Any, **_options: Any) -> Any:
        raise RuntimeError("injected crash after the OCR document was staged")

    with monkeypatch.context() as patched:
        patched.setattr(jobs_module, "finish", boom)
        with pytest.raises(RuntimeError):
            scheduler.submit(result, now_ms=harness.clock())
        assert_no_ocr_writes(harness)
        assert str(ocr_job(harness, asset_id)["state"]) == "RUNNING"
        store = ArtifactStore.open(harness.data_root)
        assert list(store.root.rglob("*.json")) == []

    # The exact same result is accepted once the fault is gone (no half-written state).
    scheduler.submit(result, now_ms=harness.clock())
    assert str(ocr_job(harness, asset_id)["state"]) == "SUCCEEDED"
    assert count(harness, "asset_ocr_text") == 1
    assert count(harness, "artifacts") == 1


# ------------------------------------------------------------------------ replay/force
@SKIP_WITHOUT_WEIGHTS
@SKIP_WITHOUT_FONT
def test_a_forced_rerecognition_reuses_the_job_and_writes_no_second_row(harness: Harness) -> None:
    harness.install_weights()
    write_text_image(harness.library / "again.jpg", ["编号 444 金额 1280"], size=(800, 300))
    harness.scan()
    asset_id = harness.asset_id("again.jpg")
    harness.recognize()

    first = dict(harness.ocr_document(asset_id))
    first_document = dict(harness.document_row(asset_id))
    first_artifact = dict(harness.catalog.read_one("SELECT * FROM artifacts"))
    first_regions = [dict(row) for row in harness.ocr_regions(asset_id)]

    # A forced re-recognition of unchanged content rewinds the same five-field job.
    summary = harness.recognize(force=True)
    assert summary["enqueue"]["rewound"] == 1
    assert summary["enqueue"]["queued"] == 0
    assert summary["run"]["jobs"] == {"SUCCEEDED": 1}

    second = dict(harness.ocr_document(asset_id))
    assert int(second["text_revision"]) == 2
    assert str(second["ocr_document_id"]) != str(first["ocr_document_id"])
    assert str(second["search_text"]) == str(first["search_text"])
    assert str(second["artifact_id"]) == str(first["artifact_id"])
    assert int(second["created_at"]) == int(first["created_at"])
    assert count(harness, "asset_ocr_text") == 1
    assert count(harness, "asset_ocr_regions") == len(first_regions)
    # The artifact is content-addressed and deterministic: the identical bytes are reused.
    artifacts = harness.catalog.read_all("SELECT * FROM artifacts")
    assert len(artifacts) == 1
    assert str(artifacts[0]["checksum_sha256"]) == str(first_artifact["checksum_sha256"])
    # The derived document did not change, so it was not rewritten at all.
    assert dict(harness.document_row(asset_id)) == first_document
    assert count(harness, "search_documents") == 1
    assert count(harness, "jobs") == 2  # the scan job plus exactly one OCR job


@SKIP_WITHOUT_WEIGHTS
@SKIP_WITHOUT_FONT
def test_the_projection_queue_carries_the_ocr_cause_and_drains_idempotently(
    harness: Harness,
) -> None:
    harness.install_weights()
    write_text_image(harness.library / "queue.jpg", ["编号 555"], size=(700, 300))
    harness.scan()
    asset_id = harness.asset_id("queue.jpg")
    harness.enqueue()
    run = harness.run_ocr()
    assert run["jobs"] == {"SUCCEEDED": 1}

    queued = harness.catalog.read_one(
        "SELECT cause FROM search_projection_queue WHERE asset_id = ?", (asset_id,)
    )
    assert queued is not None and str(queued["cause"]) == "OCR_TEXT_CHANGED"
    assert int(
        harness.catalog.read_one(
            "SELECT COUNT(*) AS total FROM search_documents WHERE asset_id = ?", (asset_id,)
        )["total"]
    ) == 0

    first = catch_up_projection(harness.catalog)
    assert first["processed"] >= 1 and first["remaining"] == 0 and first["state"] == "SYNCED"
    again = catch_up_projection(harness.catalog)
    assert again["processed"] == 0 and again["state"] == "SYNCED"
    assert count(harness, "search_documents") == 1
    hits = harness.text_hits('"555"*')
    assert hits == [asset_id]


# ------------------------------------------------------------------------ cancellation
@SKIP_WITHOUT_WEIGHTS
@SKIP_WITHOUT_FONT
def test_a_cancelled_execution_publishes_nothing(harness: Harness) -> None:
    harness.install_weights()
    write_text_image(harness.library / "cancel.jpg", ["编号 666"], size=(700, 300))
    harness.scan()
    asset_id = harness.asset_id("cancel.jpg")
    scheduler = qualified_scheduler(harness)
    enqueue_one(harness, asset_id)

    run_with(harness, scheduler, cancel=lambda: True)
    job = ocr_job(harness, asset_id)
    assert str(job["state"]) == "CANCELLED"
    assert_no_ocr_writes(harness)


@SKIP_WITHOUT_WEIGHTS
@SKIP_WITHOUT_FONT
def test_a_late_result_from_an_expired_lease_is_refused(harness: Harness) -> None:
    harness.install_weights()
    write_text_image(harness.library / "late.jpg", ["编号 777"], size=(700, 300))
    harness.scan()
    asset_id = harness.asset_id("late.jpg")
    scheduler = qualified_scheduler(harness)
    enqueue_one(harness, asset_id)
    request = scheduler.claim_next(worker_id="w1", now_ms=harness.clock())
    assert request is not None
    stale = scheduler_engine_result(
        ocr_executor(harness.catalog, data_root=harness.data_root)(request.to_envelope())
    )
    assert stale.state == "SUCCEEDED"

    # The first execution dies without reporting; its lease expires and a second worker
    # takes the same logical job over.
    harness.clock.advance(61_000)
    second = scheduler.claim_next(worker_id="w2", now_ms=harness.clock())
    assert second is not None and second.lease_token != request.lease_token

    with pytest.raises(CatalogError) as refused:
        scheduler.submit(stale, now_ms=harness.clock())
    assert refused.value.code == STALE_LEASE
    assert harness.ocr_document(asset_id) is None
    assert count(harness, "artifacts") == 0
    assert str(ocr_job(harness, asset_id)["state"]) == "RUNNING"

    # The surviving lease may still finish normally, and only then is a document written.
    fresh = scheduler_engine_result(
        ocr_executor(harness.catalog, data_root=harness.data_root)(second.to_envelope())
    )
    scheduler.submit(fresh, now_ms=harness.clock())
    assert count(harness, "asset_ocr_text") == 1
    assert count(harness, "artifacts") == 1


@SKIP_WITHOUT_WEIGHTS
@SKIP_WITHOUT_FONT
def test_a_cancelled_job_still_refuses_a_late_result(harness: Harness) -> None:
    harness.install_weights()
    write_text_image(harness.library / "cancelled.jpg", ["编号 778"], size=(700, 300))
    harness.scan()
    asset_id = harness.asset_id("cancelled.jpg")
    scheduler = qualified_scheduler(harness)
    enqueue_one(harness, asset_id)
    request = scheduler.claim_next(worker_id="w1", now_ms=harness.clock())
    assert request is not None
    late = scheduler_engine_result(
        ocr_executor(harness.catalog, data_root=harness.data_root)(request.to_envelope())
    )
    scheduler.cancel(job_id=request.job_id, now_ms=harness.clock(), reason="user request")
    assert str(ocr_job(harness, asset_id)["state"]) == "CANCELLED"

    with pytest.raises(CatalogError) as refused:
        scheduler.submit(late, now_ms=harness.clock())
    assert refused.value.code in (STALE_LEASE, "LEASE_CONFLICT")
    assert_no_ocr_writes(harness)
    assert str(ocr_job(harness, asset_id)["state"]) == "CANCELLED"


# ---------------------------------------------------------------------- budget bound
@SKIP_WITHOUT_WEIGHTS
@SKIP_WITHOUT_FONT
def test_an_exhausted_time_budget_is_a_retryable_failure(harness: Harness) -> None:
    harness.install_weights()
    write_text_image(harness.library / "budget.jpg", ["编号 888"], size=(700, 300))
    harness.scan()
    asset_id = harness.asset_id("budget.jpg")
    scheduler = qualified_scheduler(harness)
    enqueue_one(harness, asset_id)

    run_with(harness, scheduler, deadline_ms=0)
    job = ocr_job(harness, asset_id)
    assert str(job["state"]) == "RETRY_WAIT"
    assert str(job["last_error_code"]) == OCR_DEADLINE_EXCEEDED
    assert int(job["attempts"]) == 1
    assert int(job["run_after"]) > harness.clock()
    assert_no_ocr_writes(harness)


def test_the_executor_result_never_carries_a_path_or_text(harness: Harness) -> None:
    """Even a blocked execution must not leak the library path or a recognised region."""
    write_text_image(harness.library / "private.jpg", ["编号 999"], size=(600, 300))
    harness.scan()
    asset_id = harness.asset_id("private.jpg")
    scheduler = qualified_scheduler(harness)
    enqueue_one(harness, asset_id)
    request = scheduler.claim_next(worker_id="w1", now_ms=harness.clock())
    assert request is not None
    raw = ocr_executor(harness.catalog, data_root=harness.data_root)(request.to_envelope())
    body = json.dumps(raw, ensure_ascii=False)
    assert str(harness.library) not in body
    assert "编号" not in body and "999" not in body
    assert "polygon" not in body and "confidence" not in body
    assert raw["artifacts"] == []


def _seed_retired_job(harness: Harness, asset_id: int, *, processor_id: str | None = None) -> int:
    with harness.catalog.write() as connection:
        return jobs_module.enqueue(
            connection,
            subject_type="asset",
            subject_id=str(asset_id),
            input_revision=harness.sha_of(asset_id),
            task_type=contract.OCR_TASK_TYPE,
            processor_id=processor_id or contract.OCR_LEGACY_PROCESSOR_ID,
            now_ms=harness.clock(),
        )


def _seed_current_legacy_result(harness: Harness, asset_id: int, job_id: int) -> None:
    from photo_search.search.tokens import TOKENIZER_VERSION

    revision = harness.sha_of(asset_id)
    location = harness.catalog.read_one(
        "SELECT location_id FROM file_locations WHERE asset_id=? AND availability='PRESENT'",
        (asset_id,),
    )
    artifact_id = f"ocr-document:{contract.OCR_LEGACY_PROCESSOR_ID}:{revision}"
    with harness.catalog.write() as connection:
        connection.execute(
            "INSERT INTO artifacts (artifact_id, processor_id, input_revision, artifact_type, "
            "artifact_revision, media_type, checksum_sha256, byte_size, storage_uri, job_id, "
            "lifecycle_state, created_at, updated_at) VALUES (?, ?, ?, 'structured', ?, "
            "'application/json', ?, 0, 'ocr/legacy.json', ?, 'PUBLISHED', 1, 1)",
            (
                artifact_id, contract.OCR_LEGACY_PROCESSOR_ID, revision,
                contract.OCR_ARTIFACT_REVISION, "b" * 64, job_id,
            ),
        )
        connection.execute(
            "INSERT INTO asset_ocr_text (asset_id, ocr_document_id, input_revision, location_id, "
            "text_revision, processor_id, spec_revision, model_fingerprint, artifact_id, "
            "region_count, min_confidence, mean_confidence, max_confidence, search_text, "
            "tokenizer_version, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, "
            "1, 0.9, 0.9, 0.9, '', ?, 1, 1)",
            (
                asset_id, "ocr:empty-current", revision, location["location_id"],
                contract.OCR_LEGACY_PROCESSOR_ID, "legacy-readable", "a" * 64,
                artifact_id, TOKENIZER_VERSION,
            ),
        )
        connection.execute(
            "INSERT INTO asset_ocr_regions (asset_id, region_index, text_revision, box_json, confidence) "
            "VALUES (?, 0, 1, '[0,0,1,1]', 0.9)",
            (asset_id,),
        )


def _make_assets(harness: Harness, count_assets: int) -> list[int]:
    for index in range(count_assets):
        write_blank_image(harness.library / f"retired-{index}.jpg", size=(800 + index, 500))
    harness.scan()
    return [harness.asset_id(f"retired-{index}.jpg") for index in range(count_assets)]


def test_retired_reconciliation_cas_preserves_live_anomalous_and_terminal_jobs(
    harness: Harness,
) -> None:
    assets = _make_assets(harness, 7)
    queued = _seed_retired_job(harness, assets[0])
    blocked = _seed_retired_job(harness, assets[1])
    live = _seed_retired_job(harness, assets[2])
    expired = _seed_retired_job(harness, assets[3])
    malformed = _seed_retired_job(harness, assets[4])
    terminal = _seed_retired_job(harness, assets[5])
    unknown = _seed_retired_job(harness, assets[6], processor_id="ocr-text-unregistered@9")
    now = harness.clock()
    with harness.catalog.write() as connection:
        connection.execute("UPDATE jobs SET state='BLOCKED' WHERE job_id=?", (blocked,))
        jobs_module.claim_job(
            connection, job_id=live, worker_id="worker-live", now_ms=now,
            lease_seconds=harness.catalog.lease_seconds,
        )
        expired_job = jobs_module.claim_job(
            connection, job_id=expired, worker_id="worker-expired", now_ms=now,
            lease_seconds=harness.catalog.lease_seconds,
        )
        malformed_job = jobs_module.claim_job(
            connection, job_id=malformed, worker_id="worker-malformed", now_ms=now,
            lease_seconds=harness.catalog.lease_seconds,
        )
        connection.execute(
            "UPDATE jobs SET lease_expires_at=? WHERE job_id=?",
            (now - 1, expired),
        )
        connection.execute(
            "UPDATE jobs SET lease_expires_at=NULL WHERE job_id=?", (malformed,)
        )
        connection.execute(
            "UPDATE jobs SET state='SUCCEEDED', last_error_code='KEEP_TERMINAL' WHERE job_id=?",
            (terminal,),
        )
    _seed_current_legacy_result(harness, assets[5], terminal)
    with harness.catalog.write() as connection:
        connection.execute(
            "DELETE FROM search_projection_queue WHERE asset_id=?", (assets[5],)
        )
    protected_rows = {
        "document": dict(harness.catalog.read_one(
            "SELECT * FROM asset_ocr_text WHERE asset_id=?", (assets[5],)
        )),
        "regions": [dict(row) for row in harness.catalog.read_all(
            "SELECT * FROM asset_ocr_regions WHERE asset_id=?", (assets[5],)
        )],
        "artifact": dict(harness.catalog.read_one(
            "SELECT * FROM artifacts WHERE job_id=?", (terminal,)
        )),
        "projection": harness.catalog.read_one(
            "SELECT * FROM search_projection_queue WHERE asset_id=?", (assets[5],)
        ),
    }

    result = reconcile_retired_ocr_jobs(harness.catalog, now_ms=now)
    assert result == {
        "examined": 5,
        "would_cancel": 3,
        "cancelled": 3,
        "live_leases": 1,
        "lease_anomalies": 1,
        "cas_conflicts": 0,
        "remaining": 2,
        "has_more": True,
    }
    for job_id in (queued, blocked, expired):
        row = harness.catalog.read_one("SELECT * FROM jobs WHERE job_id=?", (job_id,))
        assert row["state"] == "CANCELLED"
        assert row["last_error_code"] == "OCR_PROCESSOR_RETIRED"
        assert row["lease_token"] is None and row["lease_expires_at"] is None and row["worker_id"] is None
    assert harness.catalog.read_one("SELECT state FROM jobs WHERE job_id=?", (live,))["state"] == "RUNNING"
    malformed_row = harness.catalog.read_one("SELECT * FROM jobs WHERE job_id=?", (malformed,))
    assert malformed_row["state"] == "RUNNING" and malformed_row["lease_token"] == malformed_job.lease_token
    assert harness.catalog.read_one("SELECT last_error_code FROM jobs WHERE job_id=?", (terminal,))["last_error_code"] == "KEEP_TERMINAL"
    assert harness.catalog.read_one("SELECT state FROM jobs WHERE job_id=?", (unknown,))["state"] == "QUEUED"

    with pytest.raises(CatalogError) as late:
        with harness.catalog.write() as connection:
            jobs_module.validate_submission(
                connection,
                job_id=expired,
                lease_token=expired_job.lease_token or "",
                now_ms=now,
                input_revision=harness.sha_of(assets[3]),
                processor_id=contract.OCR_LEGACY_PROCESSOR_ID,
            )
    assert late.value.code in (STALE_LEASE, "LEASE_CONFLICT")
    protected_enqueue = enqueue_targets(
        harness.catalog,
        [{"asset_id": assets[4], "input_revision": harness.sha_of(assets[4])}],
        now_ms=now,
        requested_device="cpu",
    )
    assert protected_enqueue["live"] == 1 and protected_enqueue["queued"] == 0
    assert harness.catalog.read_one("SELECT state FROM jobs WHERE job_id=?", (malformed,))["state"] == "RUNNING"
    repeated = reconcile_retired_ocr_jobs(harness.catalog, now_ms=now)
    assert repeated["cancelled"] == repeated["would_cancel"] == 0
    assert repeated["remaining"] == 2
    from photo_search.scheduler.projection import ocr_summary

    status = ocr_summary(harness.catalog.db_path)
    assert status["pending_jobs"] == 0
    assert status["retired_pending_jobs"] == 2
    assert status["state"] == "SYNCED"
    from photo_search.ocr.service import pending_job_count

    assert pending_job_count(harness.catalog) == 0
    assert dict(harness.catalog.read_one(
        "SELECT * FROM asset_ocr_text WHERE asset_id=?", (assets[5],)
    )) == protected_rows["document"]
    assert [dict(row) for row in harness.catalog.read_all(
        "SELECT * FROM asset_ocr_regions WHERE asset_id=?", (assets[5],)
    )] == protected_rows["regions"]
    assert dict(harness.catalog.read_one(
        "SELECT * FROM artifacts WHERE job_id=?", (terminal,)
    )) == protected_rows["artifact"]
    assert harness.catalog.read_one(
        "SELECT * FROM search_projection_queue WHERE asset_id=?", (assets[5],)
    ) == protected_rows["projection"]


def test_retired_reconciliation_is_bounded_and_ocr_enqueue_triggers_it(
    harness: Harness,
) -> None:
    assets = _make_assets(harness, 4)
    for asset_id in assets[:3]:
        _seed_retired_job(harness, asset_id)
    bounded = reconcile_retired_ocr_jobs(harness.catalog, now_ms=harness.clock(), batch_size=2)
    assert bounded["cancelled"] == 2 and bounded["remaining"] == 1 and bounded["has_more"] is True
    final = reconcile_retired_ocr_jobs(harness.catalog, now_ms=harness.clock(), batch_size=2)
    assert final["cancelled"] == 1 and final["remaining"] == 0
    assert reconcile_retired_ocr_jobs(harness.catalog, now_ms=harness.clock())["cancelled"] == 0

    auto_job = _seed_retired_job(harness, assets[3])
    queued = enqueue_targets(
        harness.catalog,
        [{"asset_id": assets[3], "input_revision": harness.sha_of(assets[3])}],
        now_ms=harness.clock(),
        requested_device="cpu",
    )
    assert queued["queued"] == 1
    assert harness.catalog.read_one("SELECT state FROM jobs WHERE job_id=?", (auto_job,))["state"] == "CANCELLED"
    assert harness.catalog.read_one(
        "SELECT state FROM jobs WHERE subject_id=? AND processor_id=?",
        (str(assets[3]), contract.OCR_CPU_PROCESSOR_ID),
    )["state"] == "QUEUED"


def test_catalog_startup_automatically_reconciles_a_bounded_retired_batch(
    harness: Harness,
) -> None:
    from photo_search.ingestion.service import CatalogService

    assets = _make_assets(harness, 1)
    job_id = _seed_retired_job(harness, assets[0])
    with CatalogService.open(
        data_root=harness.data_root,
        clock=harness.clock,
        lease_seconds=harness.catalog.lease_seconds,
        wall_clock_ms=harness.clock,
    ) as reopened:
        assert reopened.catalog.read_one(
            "SELECT state FROM jobs WHERE job_id=?", (job_id,)
        )["state"] == "CANCELLED"


def test_retirement_scope_follows_the_declared_processor_version_set(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    assets = _make_assets(harness, 2)
    future_retired = _seed_retired_job(harness, assets[0], processor_id="ocr-text@2")
    unknown = _seed_retired_job(harness, assets[1], processor_id="ocr-text-unregistered@9")
    monkeypatch.setattr(contract, "OCR_ACTIVE_PROCESSOR_IDS", ("ocr-text@3",))
    monkeypatch.setattr(contract, "OCR_RETIRED_PROCESSOR_IDS", ("ocr-text@2",))
    result = reconcile_retired_ocr_jobs(harness.catalog, now_ms=harness.clock())
    assert result["cancelled"] == 1
    assert harness.catalog.read_one(
        "SELECT state FROM jobs WHERE job_id=?", (future_retired,)
    )["state"] == "CANCELLED"
    assert harness.catalog.read_one(
        "SELECT state FROM jobs WHERE job_id=?", (unknown,)
    )["state"] == "QUEUED"


def test_ocr_source_progress_uses_current_results_and_active_asset_jobs_once(
    harness: Harness,
) -> None:
    from photo_search.refresh.progress import source_progress
    assets = _make_assets(harness, 5)
    # A current empty document is a successful result, independent of its old processor ID.
    successful_legacy = _seed_retired_job(harness, assets[0])
    with harness.catalog.write() as connection:
        connection.execute("UPDATE jobs SET state='SUCCEEDED' WHERE job_id=?", (successful_legacy,))
    _seed_current_legacy_result(harness, assets[0], successful_legacy)
    failed_gpu = _seed_retired_job(harness, assets[1], processor_id=contract.OCR_GPU_PROCESSOR_ID)
    queued_cpu = _seed_retired_job(harness, assets[1], processor_id=contract.OCR_CPU_PROCESSOR_ID)
    blocked = _seed_retired_job(harness, assets[2], processor_id=contract.OCR_GPU_PROCESSOR_ID)
    failed = _seed_retired_job(harness, assets[3], processor_id=contract.OCR_CPU_PROCESSOR_ID)
    legacy = _seed_retired_job(harness, assets[4])
    with harness.catalog.write() as connection:
        connection.execute("UPDATE jobs SET state='FAILED' WHERE job_id=?", (failed_gpu,))
        connection.execute("UPDATE jobs SET state='BLOCKED' WHERE job_id=?", (blocked,))
        connection.execute("UPDATE jobs SET state='FAILED' WHERE job_id=?", (failed,))

    stage = source_progress(
        harness.catalog, source_id=1, binding=None, available={"ocr": True}
    )["stages"]["ocr"]
    assert stage == {
        "state": "IN_PROGRESS",
        "total": 5,
        "succeeded": 1,
        "handled": 3,
        "pending": 1,
        "failed": 1,
        "blocked": 1,
        "unattempted": 1,
        "attempted": 4,
        "runtime_available": True,
    }
    with harness.catalog.write() as connection:
        connection.execute("UPDATE jobs SET state='FAILED' WHERE job_id=?", (queued_cpu,))
        connection.execute("UPDATE jobs SET state='FAILED' WHERE job_id=?", (blocked,))
        connection.execute("UPDATE jobs SET state='FAILED' WHERE job_id=?", (legacy,))
    stage = source_progress(
        harness.catalog, source_id=1, binding=None, available={"ocr": True}
    )["stages"]["ocr"]
    assert stage["state"] == "PENDING"
    assert stage["pending"] == 0 and stage["failed"] == 3 and stage["unattempted"] == 1
    assert "IN_PROGRESS" != stage["state"]
    final_failure = _seed_retired_job(
        harness, assets[4], processor_id=contract.OCR_CPU_PROCESSOR_ID
    )
    with harness.catalog.write() as connection:
        connection.execute("UPDATE jobs SET state='FAILED' WHERE job_id=?", (final_failure,))
    stage = source_progress(
        harness.catalog, source_id=1, binding=None, available={"ocr": True}
    )["stages"]["ocr"]
    assert stage["state"] == "COMPLETE"
    assert stage["pending"] == 0 and stage["failed"] == 4 and stage["unattempted"] == 0
