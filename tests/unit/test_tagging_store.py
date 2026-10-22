"""Machine tags keep content/lease identity and use bounded candidate-only reads."""
from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from photo_search.artifacts.store import ArtifactStore
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import CatalogError
from photo_search.jobs import queue as jobs
from photo_search.library_retention import _tables, _unknown_asset_reference_schema
from photo_search.scheduler.contracts import ArtifactRef, JobResult, canonical_json
from photo_search.tagging import store


@pytest.fixture
def catalog(tmp_path: Path):
    with Catalog.open(data_root=tmp_path / "data") as value:
        with value.write() as db:
            store.register_model(db, model_id="test-model", policy_id="top32-fp16",
                                 manifest_sha256="b" * 64, class_count=100,
                                 max_scores=32, now_ms=1)
        yield value


def processor() -> str:
    return store.processor_id_for("test-model", "top32-fp16")


def start(catalog: Catalog, asset_id: int = 1):
    sha = f"{asset_id:064x}"
    with catalog.write() as db:
        db.execute("INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) "
                   "VALUES(?,?,1,1,'ACTIVE')", (asset_id, sha))
        job_id = jobs.enqueue(db, subject_type="asset", subject_id=str(asset_id),
                              input_revision=sha, task_type=store.TASK_TYPE,
                              processor_id=processor(), now_ms=10)
        job = jobs.claim_job(db, job_id=job_id, worker_id="worker", now_ms=10,
                             lease_seconds=100)
    return job


def prepare(catalog: Catalog, job, scores=None):
    payload = {"schema": store.PAYLOAD_SCHEMA, "model_id": "test-model",
               "policy_id": "top32-fp16", "input_revision": job.input_revision,
               "scores": scores if scores is not None else [
                   {"tag_id": 2, "score": 0.75, "above_threshold": True},
                   {"tag_id": 3, "score": 0.5, "above_threshold": False}]}
    raw = canonical_json(payload).encode("utf-8")
    ref = ArtifactRef(artifact_id=store.artifact_id_for(job.input_revision, processor()),
                      artifact_type="structured", artifact_revision=store.ARTIFACT_REVISION,
                      media_type="application/json", checksum_sha256=hashlib.sha256(raw).hexdigest(),
                      byte_size=len(raw), storage_uri=None, payload=payload,
                      space_id=None, dimension=None)
    artifacts = ArtifactStore.open(catalog.data_root)
    item = artifacts.prepare(ref)
    result = JobResult(job_id=job.job_id, lease_token=job.lease_token,
                       input_revision=job.input_revision, task_type=store.TASK_TYPE,
                       processor_id=processor(), state="SUCCEEDED", resolved_device="cpu",
                       runtime_provider="test", precision="fp16", progress=1.0,
                       artifacts=(ref,), error=None, completed_at=20)
    return artifacts, item, result


def publish(catalog: Catalog, job, scores=None):
    artifacts, item, result = prepare(catalog, job, scores)
    try:
        with catalog.write() as db:
            artifacts.persist_result(db, job=job, result=result,
                                     spec=SimpleNamespace(processor_id=processor()),
                                     prepared=[item], now_ms=20)
            changed = store.persist_tag_result(db, job=job, result=result,
                                               prepared=[item], now_ms=20)
            jobs.finish(db, job_id=job.job_id, lease_token=job.lease_token,
                        state="SUCCEEDED", input_revision=job.input_revision,
                        processor_id=processor(), now_ms=20, progress=1)
        return changed
    finally:
        artifacts.discard([item])


def test_publish_reopen_and_candidate_lookup_distinguish_missing_tag_from_missing_result(catalog):
    one, two = start(catalog, 1), start(catalog, 2)
    assert publish(catalog, one)
    assert publish(catalog, two, [])
    start(catalog, 3)
    with catalog.read_snapshot() as db:
        assert store.scores_for_assets(db, processor_id=processor(), asset_ids=[1, 2, 3, 99, 1],
                                       tag_id=2) == ({1: 0.75}, {1, 2})
        assert store.scores_for_assets(db, processor_id=processor(), asset_ids=[2],
                                       tag_id=2) == ({}, {2})
    with Catalog.open(data_root=catalog.data_root) as reopened:
        assert store.scores_for_assets(reopened.connection, processor_id=processor(),
                                       asset_ids=[1], tag_id=3) == ({1: 0.5}, {1})
        state = store.status(reopened.connection, processor())
        assert state["completed_count"] == 2 and state["score_count"] == 2
        assert state["job_counts"]["SUCCEEDED"] == 2
        assert state["job_counts"]["RUNNING"] == 1


def test_registration_is_immutable_and_transaction_required(catalog):
    with pytest.raises(RuntimeError):
        store.register_model(catalog.connection, model_id="test-model", policy_id="top32-fp16",
                             manifest_sha256="c" * 64, class_count=100, max_scores=32, now_ms=1)
    with catalog.write() as db:
        original = store.get_model(db, processor())
        assert store.register_model(db, model_id="test-model", policy_id="top32-fp16",
                                    manifest_sha256="b" * 64, class_count=100,
                                    max_scores=32, now_ms=2) == original
        with pytest.raises(CatalogError, match="immutable"):
            store.register_model(db, model_id="test-model", policy_id="top32-fp16",
                                 manifest_sha256="c" * 64, class_count=100,
                                 max_scores=32, now_ms=2)


@pytest.mark.parametrize("scores", [
    [{"tag_id": 0, "score": float("nan"), "above_threshold": True}],
    [{"tag_id": 0, "score": -1.0, "above_threshold": True}],
    [{"tag_id": 0, "score": 0.1, "above_threshold": True}],
    [{"tag_id": 100, "score": 0.5, "above_threshold": True}],
    [{"tag_id": True, "score": 0.5, "above_threshold": True}],
    [{"tag_id": 0, "score": 0.5, "above_threshold": 1}],
    [{"tag_id": 0, "score": 0.5, "above_threshold": True}] * 2,
    [{"tag_id": 1, "score": 0.25, "above_threshold": True},
     {"tag_id": 0, "score": 0.5, "above_threshold": True}],
    [{"tag_id": value, "score": 0.5, "above_threshold": True} for value in range(33)],
])
def test_sparse_policy_rejects_bad_scores(catalog, scores):
    model = store.get_model(catalog.connection, processor())
    payload = {"schema": store.PAYLOAD_SCHEMA, "model_id": "test-model",
               "policy_id": "top32-fp16", "input_revision": "1" * 64, "scores": scores}
    with pytest.raises(CatalogError):
        store._validated_payload(payload, model)


@pytest.mark.parametrize("failure", ["stale_lease", "expired", "sha", "barrier", "artifact"])
def test_late_stale_deleted_or_unpublished_result_cannot_publish(catalog, failure):
    job = start(catalog)
    artifacts, item, result = prepare(catalog, job)
    try:
        if failure == "stale_lease":
            result = replace(result, lease_token="old-worker")
        with pytest.raises(CatalogError):
            with catalog.write() as db:
                if failure == "sha":
                    db.execute("UPDATE assets SET sha256=? WHERE asset_id=1", ("c" * 64,))
                if failure == "barrier":
                    db.execute("INSERT INTO asset_retention_barriers(asset_id,barrier_id,state,created_at_ms,"
                               "updated_at_ms) VALUES(1,?,'ACTIVE',1,1)", ("1" * 32,))
                if failure != "artifact":
                    artifacts.persist_result(db, job=job, result=result,
                                             spec=SimpleNamespace(processor_id=processor()),
                                             prepared=[item], now_ms=20)
                store.persist_tag_result(db, job=job, result=result, prepared=[item],
                                         now_ms=100010 if failure == "expired" else 20)
        assert catalog.read_one("SELECT COUNT(*) FROM machine_tag_results")[0] == 0
        assert catalog.read_one("SELECT COUNT(*) FROM artifacts")[0] == 0
        assert store.get_model(catalog.connection, processor())["revision"] == 0
    finally:
        artifacts.discard([item], remove_published=True)


def test_failed_transaction_is_replayable_and_identical_projection_is_idempotent(catalog):
    job = start(catalog)
    artifacts, item, result = prepare(catalog, job)
    try:
        with pytest.raises(RuntimeError, match="crash"):
            with catalog.write() as db:
                artifacts.persist_result(db, job=job, result=result,
                                         spec=SimpleNamespace(processor_id=processor()),
                                         prepared=[item], now_ms=20)
                store.persist_tag_result(db, job=job, result=result, prepared=[item], now_ms=20)
                raise RuntimeError("injected crash before commit")
        assert store.status(catalog.connection, processor())["completed_count"] == 0
        assert jobs.get_job(catalog.connection, job.job_id).state == "RUNNING"
        with catalog.write() as db:
            artifacts.persist_result(db, job=job, result=result,
                                     spec=SimpleNamespace(processor_id=processor()),
                                     prepared=[item], now_ms=20)
            assert store.persist_tag_result(db, job=job, result=result, prepared=[item], now_ms=20)
            revision = store.get_model(db, processor())["revision"]
            assert not store.persist_tag_result(db, job=job, result=result, prepared=[item], now_ms=20)
            assert store.get_model(db, processor())["revision"] == revision
    finally:
        artifacts.discard([item])


def test_content_or_retention_change_hides_scores_and_cascade_gc_updates_revision(catalog):
    job = start(catalog)
    publish(catalog, job)
    assert not _unknown_asset_reference_schema(catalog.connection, _tables(catalog.connection))
    with catalog.write() as db:
        db.execute("UPDATE assets SET sha256=? WHERE asset_id=1", ("c" * 64,))
    assert store.scores_for_assets(catalog.connection, processor_id=processor(), asset_ids=[1],
                                   tag_id=2) == ({}, set())
    with catalog.write() as db:
        db.execute("UPDATE assets SET sha256=? WHERE asset_id=1", (job.input_revision,))
        db.execute("INSERT INTO asset_retention_barriers(asset_id,barrier_id,state,created_at_ms,"
                   "updated_at_ms) VALUES(1,?,'ACTIVE',1,1)", ("1" * 32,))
    assert store.scores_for_assets(catalog.connection, processor_id=processor(), asset_ids=[1],
                                   tag_id=2) == ({}, set())
    revision = store.get_model(catalog.connection, processor())["revision"]
    with catalog.write() as db:
        db.execute("DELETE FROM assets WHERE asset_id=1")
    state = store.status(catalog.connection, processor())
    assert state["completed_count"] == state["score_count"] == 0
    assert state["revision"] > revision
    assert state["job_counts"]["SUCCEEDED"] == 1  # Existing retained job history.


def test_polling_is_constant_rows_and_lookup_is_bounded_primary_key(catalog):
    job = start(catalog)
    publish(catalog, job)
    statements = []
    catalog.connection.set_trace_callback(statements.append)
    try:
        store.status(catalog.connection, processor())
        assert len(statements) == 2 and all("COUNT(" not in value.upper() for value in statements)
        statements.clear()
        assert store.scores_for_assets(catalog.connection, processor_id=processor(),
                                       asset_ids=list(range(1, 5001)), tag_id=2) == ({1: 0.75}, {1})
        assert len(statements) == 11  # model identity plus ten bounded point-lookup batches
    finally:
        catalog.connection.set_trace_callback(None)
    with pytest.raises(ValueError):
        store.scores_for_assets(catalog.connection, processor_id=processor(),
                                asset_ids=list(range(1, 5002)), tag_id=2)
    assert store.scores_for_assets(catalog.connection, processor_id="missing", asset_ids=[1],
                                   tag_id=2) == ({}, set())


def test_shared_job_retry_block_cancel_and_delete_keep_incremental_counts(catalog):
    job = start(catalog)
    with catalog.write() as db:
        jobs.finish(db, job_id=job.job_id, lease_token=job.lease_token, state="RETRY_WAIT",
                    input_revision=job.input_revision, processor_id=processor(), now_ms=20,
                    run_after_ms=21)
        assert store.status(db, processor())["job_counts"]["RETRY_WAIT"] == 1
        jobs.requeue_due_retries(db, now_ms=21)
        job = jobs.claim_job(db, job_id=job.job_id, worker_id="next", now_ms=21, lease_seconds=10)
        jobs.finish(db, job_id=job.job_id, lease_token=job.lease_token, state="BLOCKED",
                    input_revision=job.input_revision, processor_id=processor(), now_ms=22)
        jobs.resume_blocked(db, job_id=job.job_id, now_ms=23)
        db.execute("UPDATE jobs SET state='CANCELLED' WHERE job_id=?", (job.job_id,))
        counts = store.status(db, processor())["job_counts"]
        assert counts["CANCELLED"] == 1 and sum(counts.values()) == 1
        db.execute("DELETE FROM jobs WHERE job_id=?", (job.job_id,))
        assert sum(store.status(db, processor())["job_counts"].values()) == 0


def test_real_retention_cleanup_reclaims_only_machine_tags_and_keeps_human_favorite(catalog):
    from photo_search.library_retention import (
        begin_asset_retention_cleanup,
        finalize_asset_retention_cleanup,
    )
    from photo_search.retention import OBSERVATION_MS

    for asset in (1, 2):
        publish(catalog, start(catalog, asset))
    now = OBSERVATION_MS + 1000
    with catalog.write() as db:
        db.execute("INSERT INTO source_roots(display_name,root_path,status) VALUES('synthetic',?,'ONLINE')",
                   (str(catalog.data_root / "synthetic-photos"),))
        db.execute("INSERT INTO scan_runs(source_id,started_at,finished_at,state) "
                   "VALUES(1,1,?,'SUCCEEDED')", (now,))
        for asset in (1, 2):
            db.execute("INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,"
                       "mtime_ns,availability) VALUES(1,?,?,?,1,1,'MISSING')",
                       (f"{asset}.jpg", f"{asset}.jpg", asset))
            db.execute("INSERT INTO asset_retention_source_evidence(asset_id,source_id,scan_id,"
                       "missing_confirmed_at_ms) VALUES(?,1,1,?)", (asset, now))
            db.execute("INSERT INTO asset_retention_state(asset_id,first_missing_at_ms,"
                       "last_qualified_at_ms,state,updated_at_ms) VALUES(?,0,?,'ELIGIBLE',?)",
                       (asset, now, now))
        db.execute("INSERT INTO asset_favorites VALUES(2,?)", (now,))
    with pytest.raises(CatalogError):
        begin_asset_retention_cleanup(catalog, asset_id=2, now_ms=now + 1)
    begin_asset_retention_cleanup(catalog, asset_id=1, now_ms=now + 1)
    result = finalize_asset_retention_cleanup(catalog, asset_id=1, now_ms=now + 2)
    assert result["state"] == "COMPLETE"
    assert catalog.read_one("SELECT 1 FROM assets WHERE asset_id=1") is None
    assert catalog.read_one("SELECT asset_id FROM asset_favorites")[0] == 2
    assert store.get_model(catalog.connection, processor())["completed_count"] == 1
    assert store.scores_for_assets(catalog.connection, processor_id=processor(),
                                   asset_ids=[1, 2], tag_id=2) == ({2: 0.75}, {2})
