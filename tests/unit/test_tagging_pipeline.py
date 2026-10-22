"""Real catalog/jobs/artifacts, synthetic photos, and a deterministic fake GPU runtime."""
from __future__ import annotations

import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from photo_search.catalog.errors import CatalogError
from photo_search.scheduler.contracts import JobResult
from photo_search.tagging import policy, service, store
from photo_search.tagging.model import CLASS_COUNT, TagClass, TaggerModel
from photo_search.tagging.runtime import TagPrediction


def model(root):
    return TaggerModel("ram-plus-v1:" + "a" * 64,
        tuple(TagClass(i, "class" + str(i), "类" + str(i), .5) for i in range(CLASS_COUNT)),
        "b" * 64, "c" * 64, 100, root)


class FakeRuntime:
    def __init__(self, info, callback=None):
        self.info, self.callback, self.calls, self.closed = info, callback, 0, False

    def predict(self, image):
        assert not self.closed and image.mode == "RGB"
        self.calls += 1
        if self.callback is not None:
            self.callback()
        return TagPrediction(self.info.model_id, [.75] + [.25] * (CLASS_COUNT - 1), (0,))

    def close(self):
        self.closed = True


@pytest.fixture
def pipeline(api_env):
    photo = api_env.add_photo("red.jpg")
    source = api_env.register_source()
    api_env.scan(source["source_id"])
    catalog = api_env.service.catalog
    runtime = FakeRuntime(model(catalog.data_root))
    registered = service.register_model(catalog, runtime.info)
    targets = service.discover_targets(catalog, processor_id=registered["processor_id"], after_id=0, through_id=999)
    service.enqueue_targets(catalog, targets, processor_id=registered["processor_id"])
    scheduler = service.build_scheduler(catalog, runtime)
    return SimpleNamespace(env=api_env, photo=photo, catalog=catalog, runtime=runtime,
        processor_id=registered["processor_id"], scheduler=scheduler, targets=targets)


def test_sparse_production_policy_keeps_original_top32_then_sorts_quantized_ties():
    values = [.1] * CLASS_COUNT
    values[2], values[1], values[0] = .5001, .5, .4999
    result = policy.sparse_scores(TagPrediction("model", values, (2,)))
    assert len(result) == policy.MAX_SCORES == 32
    assert [row["tag_id"] for row in result[:3]] == [0, 1, 2]
    assert [row["above_threshold"] for row in result[:3]] == [False, False, True]
    assert all(row["score"] == .5 for row in result[:3])
    values[0] = float("nan")
    with pytest.raises(CatalogError, match="invalid"):
        policy.sparse_scores(TagPrediction("model", values, (2,)))


def test_scheduler_publishes_tags_artifact_and_job_atomically_without_fts(pipeline):
    p = pipeline
    before_fts = p.catalog.read_one("SELECT COUNT(*) FROM asset_ocr_text")[0]
    job = service.run_one(p.scheduler, service.tag_executor(p.catalog, p.runtime), worker_id="test")
    assert job.state == "SUCCEEDED" and p.runtime.calls == 1
    rows = p.catalog.read_all("SELECT * FROM machine_tag_results")
    assert len(rows) == 1 and rows[0]["input_revision"] == hashlib.sha256(p.photo.read_bytes()).hexdigest()
    scores, covered = store.scores_for_assets(p.catalog.connection, processor_id=p.processor_id,
        asset_ids=[p.env.first_asset_id()], tag_id=0)
    assert covered == {p.env.first_asset_id()} and scores == {p.env.first_asset_id(): .75}
    assert p.catalog.read_one("SELECT COUNT(*) FROM artifacts WHERE artifact_id=?", (rows[0]["artifact_id"],))[0] == 1
    assert p.catalog.read_one("SELECT COUNT(*) FROM asset_ocr_text")[0] == before_fts
    assert service.discover_targets(p.catalog, processor_id=p.processor_id, after_id=0, through_id=999) == []
    assert list((p.catalog.data_root / "runtime/tagging-input").iterdir()) == []


def test_changed_original_is_blocked_before_inference_and_not_published(pipeline):
    p = pipeline
    p.photo.write_bytes(b"changed source bytes")
    job = service.run_one(p.scheduler, service.tag_executor(p.catalog, p.runtime), worker_id="test")
    assert job.state == "BLOCKED" and job.last_error_code == "INPUT_UNSTABLE"
    assert p.runtime.calls == 0
    assert p.catalog.read_one("SELECT COUNT(*) FROM machine_tag_results")[0] == 0


def test_decodes_hashed_snapshot_not_second_read_of_original(pipeline, monkeypatch):
    from photo_search.media import decode

    p = pipeline
    original = decode.load_normalized
    observed = []
    expected = p.targets[0]["input_revision"]

    def changed_source_after_snapshot(path, **kwargs):
        assert path != p.photo
        observed.append(hashlib.sha256(path.read_bytes()).hexdigest())
        p.photo.write_bytes(b"changed after snapshot")
        return original(path, **kwargs)

    monkeypatch.setattr(decode, "load_normalized", changed_source_after_snapshot)
    job = service.run_one(p.scheduler, service.tag_executor(p.catalog, p.runtime), worker_id="test")
    assert job.state == "SUCCEEDED" and observed == [expected]
    assert p.catalog.read_one("SELECT input_revision FROM machine_tag_results")[0] == expected


def test_artifact_and_projection_rollback_together_then_same_result_replays(pipeline, monkeypatch):
    p = pipeline
    request = p.scheduler.claim_next(worker_id="test", now_ms=service.now_ms())
    result = JobResult.from_envelope(service.tag_executor(p.catalog, p.runtime)(request.to_envelope()))
    persist = store.persist_tag_result

    def failure(*args, **kwargs):
        persist(*args, **kwargs)
        raise RuntimeError("injected after projection")

    monkeypatch.setattr(store, "persist_tag_result", failure)
    with pytest.raises(RuntimeError, match="injected"):
        p.scheduler.submit(result, now_ms=service.now_ms())
    assert p.catalog.read_one("SELECT COUNT(*) FROM machine_tag_results")[0] == 0
    assert p.catalog.read_one("SELECT COUNT(*) FROM artifacts WHERE artifact_id=?", (result.artifacts[0].artifact_id,))[0] == 0
    assert p.catalog.read_one("SELECT state FROM jobs WHERE job_id=?", (request.job_id,))[0] == "RUNNING"
    monkeypatch.setattr(store, "persist_tag_result", persist)
    assert p.scheduler.submit(result, now_ms=service.now_ms()).state == "SUCCEEDED"


def test_submission_uses_actual_completion_time_and_rejects_expired_lease(pipeline):
    p = pipeline
    started = service.now_ms()
    times = iter([started, started + 301000])
    with pytest.raises(CatalogError) as error:
        service.run_one(p.scheduler, service.tag_executor(p.catalog, p.runtime), worker_id="test", clock=lambda: next(times))
    assert error.value.code in {"STALE_LEASE", "LEASE_CONFLICT"}
    assert p.catalog.read_one("SELECT COUNT(*) FROM machine_tag_results")[0] == 0


def test_runtime_failure_and_stale_catalog_content_never_publish(pipeline):
    p = pipeline

    def fail():
        with p.catalog.write() as db:
            db.execute("UPDATE assets SET sha256=? WHERE asset_id=?", ("d" * 64, p.env.first_asset_id()))

    p.runtime.callback = fail
    with pytest.raises(CatalogError) as error:
        service.run_one(p.scheduler, service.tag_executor(p.catalog, p.runtime), worker_id="test")
    assert error.value.code == "STALE_INPUT_REVISION"
    assert p.catalog.read_one("SELECT COUNT(*) FROM machine_tag_results")[0] == 0


def test_cpu_dispatch_is_refused_without_loading_or_reading_photo(pipeline):
    p = pipeline
    request = p.scheduler.claim_next(worker_id="test", now_ms=service.now_ms()).to_envelope()
    request["resolved_device"] = "cpu"
    result = service.tag_executor(p.catalog, p.runtime)(request)
    assert result["state"] == "FAILED" and result["error"]["code"] == "RUNTIME_RESOLUTION_MISMATCH"
    assert p.runtime.calls == 0


def test_global_scheduler_pause_is_preserved(pipeline):
    p = pipeline
    p.scheduler.pause(now_ms=service.now_ms())
    assert service.run_one(p.scheduler, service.tag_executor(p.catalog, p.runtime), worker_id="test") is None
    assert p.scheduler.read_state().paused and p.runtime.calls == 0


def manager_for(pipeline, monkeypatch, *, callback=None):
    from photo_search.runtime.data_root_lease import acquire_data_root_lease
    from photo_search.tagging import task

    p = pipeline
    lease = acquire_data_root_lease(p.catalog.data_root)
    p.env.context.set_data_root_lease(lease)
    instances = []

    def loaded(info):
        assert p.env.context.model_resource_gate.reserved
        runtime = FakeRuntime(info, callback)
        close = runtime.close

        def closed_under_gate():
            assert p.env.context.model_resource_gate.reserved
            close()

        runtime.close = closed_under_gate
        instances.append(runtime)
        return runtime

    monkeypatch.setattr(task, "read_model", lambda root: p.runtime.info)
    monkeypatch.setattr(task.RamTagger, "load", loaded)
    return task.TagBuildManager(p.env.context), lease, instances


def join_worker(manager):
    manager._thread.join(timeout=10)
    assert not manager._thread.is_alive(), "synthetic tag worker did not reach its bounded stop"


def test_manager_pause_resume_preserves_queue_and_closes_model_inside_gate(pipeline, monkeypatch):
    p = pipeline
    p.env.add_photo("second.jpg", color=(20, 70, 100))
    p.env.scan(p.env.service.list_sources()[0]["source_id"])
    entered, release = threading.Event(), threading.Event()

    def hold_inference():
        entered.set()
        assert release.wait(10)

    manager, lease, runtimes = manager_for(p, monkeypatch, callback=hold_inference)
    try:
        state = manager.start("pause-resume", exclusive_confirmed=True)
        assert entered.wait(10)
        assert manager.pause(state["task_id"])["status"] == "PAUSE_REQUESTED"
        with pytest.raises(CatalogError):
            with p.env.context.model_resource_gate.query():
                pass
        release.set()
        join_worker(manager)
        assert manager.status()["status"] == "PAUSED" and runtimes[0].closed
        assert not p.env.context.model_resource_gate.reserved
        manager.resume(state["task_id"])
        join_worker(manager)
        assert manager.status()["status"] == "COMPLETED"
        assert len(runtimes) == 2 and all(item.closed for item in runtimes)
        assert p.catalog.read_one("SELECT COUNT(*) FROM machine_tag_results")[0] == 2
    finally:
        release.set()
        manager.close()
        p.env.context.set_data_root_lease(None)
        lease.close()


def test_stop_waits_for_inference_and_releases_cuda_before_gate_and_lease(pipeline, monkeypatch):
    p = pipeline
    entered, release, close_done = threading.Event(), threading.Event(), threading.Event()

    def hold():
        entered.set()
        assert release.wait(10)

    manager, lease, runtimes = manager_for(p, monkeypatch, callback=hold)
    try:
        manager.start("close-order", exclusive_confirmed=True)
        assert entered.wait(10)
        closing = threading.Thread(target=lambda: (manager.close(), close_done.set()))
        closing.start()
        assert not close_done.wait(.03)
        assert p.env.context.model_resource_gate.reserved
        release.set()
        assert close_done.wait(10)
        closing.join()
        assert runtimes[0].closed and not p.env.context.model_resource_gate.reserved
        assert manager.status()["status"] == "INTERRUPTED"
    finally:
        release.set()
        manager.close()
        p.env.context.set_data_root_lease(None)
        lease.close()


def test_new_os_lease_owner_reclaims_only_previous_task_worker_then_resumes(pipeline, monkeypatch):
    from photo_search.tagging import task

    p = pipeline
    manager, lease, runtimes = manager_for(p, monkeypatch)
    try:
        monkeypatch.setattr(manager, "_launch", lambda *args, **kwargs: None)
        state = manager.start("crash", exclusive_confirmed=True)
        request = p.scheduler.claim_next(worker_id="image-tags-" + state["task_id"], now_ms=service.now_ms())
        assert request is not None
        recovered = task.TagBuildManager(p.env.context)
        recovered.recover_after_lock()
        assert recovered.status()["status"] == "INTERRUPTED"
        job = p.catalog.read_one("SELECT lease_expires_at FROM jobs WHERE job_id=?", (request.job_id,))
        assert job[0] <= service.now_ms()
        recovered.resume(state["task_id"])
        join_worker(recovered)
        assert recovered.status()["status"] == "COMPLETED"
        assert p.catalog.read_one("SELECT attempts FROM jobs WHERE job_id=?", (request.job_id,))[0] == 1
        assert len(runtimes) == 1 and runtimes[0].closed
    finally:
        manager.close()
        p.env.context.set_data_root_lease(None)
        lease.close()


@pytest.mark.parametrize("damage", [b"{broken", b'{"schema":"tag-build-v1","status":[]}',
    b'{"schema":"tag-build-v1","task_id":"abc","status":"RUNNING","discovery_cursor":0,"highest_asset_id":1}'])
def test_corrupt_optional_task_reports_error_without_blocking_app_recovery(pipeline, monkeypatch, damage):
    from photo_search.tagging import task

    p = pipeline
    manager, lease, _ = manager_for(p, monkeypatch)
    path = p.catalog.data_root / "runtime" / task.TASK_FILENAME
    path.write_bytes(damage)
    try:
        manager.recover_after_lock()
        assert manager.status()["last_error"]["code"] == "TAG_TASK_STATE_INVALID"
        with pytest.raises(CatalogError) as error:
            manager.start("do-not-overwrite", exclusive_confirmed=True)
        assert error.value.code == "TAG_TASK_STATE_INVALID" and path.read_bytes() == damage
    finally:
        p.env.context.set_data_root_lease(None)
        lease.close()


def test_resume_model_change_and_missing_owner_are_refused(pipeline, monkeypatch):
    from dataclasses import replace

    from photo_search.tagging import task

    p = pipeline
    manager, lease, _ = manager_for(p, monkeypatch)
    try:
        monkeypatch.setattr(manager, "_launch", lambda *args, **kwargs: None)
        state = manager.start("identity", exclusive_confirmed=True)
        state["status"] = "PAUSED"
        manager._save(state)
        monkeypatch.setattr(task, "read_model", lambda root: replace(p.runtime.info, model_id="changed"))
        with pytest.raises(CatalogError) as error:
            manager.resume(state["task_id"])
        assert error.value.code == "TAGGER_MODEL_INVALID"
        assert manager.status()["status"] == "PAUSED"
        p.env.context.set_data_root_lease(None)
        with pytest.raises(CatalogError, match="lease"):
            manager.recover_after_lock()
    finally:
        p.env.context.set_data_root_lease(None)
        lease.close()


@pytest.mark.parametrize("state_name", ["PAUSED", "INTERRUPTED", "FAILED"])
def test_explicit_new_batch_after_model_change_preserves_existing_results(pipeline, monkeypatch, state_name):
    from dataclasses import replace

    from photo_search.tagging import task

    p = pipeline
    assert service.run_one(p.scheduler, service.tag_executor(p.catalog, p.runtime),
                           worker_id="replacement-test").state == "SUCCEEDED"
    original = [tuple(row) for row in p.catalog.read_all("SELECT * FROM machine_tag_results")]
    manager, lease, _ = manager_for(p, monkeypatch)
    try:
        monkeypatch.setattr(manager, "_launch", lambda *args, **kwargs: None)
        state = manager.start("before-upgrade", exclusive_confirmed=True)
        state["status"] = state_name
        manager._save(state)
        updated = replace(p.runtime.info, model_id="updated-model", manifest_sha256="f" * 64)
        monkeypatch.setattr(task, "read_model", lambda root: updated)
        with pytest.raises(CatalogError) as error:
            manager.resume(state["task_id"])
        assert error.value.code == "TAGGER_MODEL_INVALID"
        with pytest.raises(CatalogError):
            manager.start("missing-replace", exclusive_confirmed=True)
        with pytest.raises(CatalogError):
            manager.start("stale-replace", exclusive_confirmed=True, replace_task_id="0" * 32)
        assert manager.status()["task_id"] == state["task_id"]
        replacement = manager.start("after-upgrade", exclusive_confirmed=True,
                                    replace_task_id=state["task_id"])
        assert replacement["task_id"] != state["task_id"]
        assert replacement["model_id"] == updated.model_id
        assert replacement["processor_id"] != state["processor_id"]
        assert [tuple(row) for row in p.catalog.read_all("SELECT * FROM machine_tag_results")] == original
        assert manager.start("after-upgrade", exclusive_confirmed=True,
                             replace_task_id=state["task_id"])["task_id"] == replacement["task_id"]
        with pytest.raises(CatalogError):
            manager.start("replace-running", exclusive_confirmed=True,
                          replace_task_id=replacement["task_id"])
        assert manager.status()["task_id"] == replacement["task_id"]
    finally:
        manager.close()
        p.env.context.set_data_root_lease(None)
        lease.close()


def test_old_content_queue_is_cancelled_before_retry(pipeline):
    p = pipeline
    with p.catalog.write() as db:
        db.execute("UPDATE assets SET sha256=? WHERE asset_id=?", ("d" * 64, p.env.first_asset_id()))
    assert service.cancel_stale_waiting(p.catalog, processor_id=p.processor_id) == 1
    assert service.retry_waiting(p.catalog, processor_id=p.processor_id) == 0
    assert p.catalog.read_one("SELECT state FROM jobs WHERE processor_id=?", (p.processor_id,))[0] == "CANCELLED"


def test_active_refresh_is_refused_before_creating_task(pipeline, monkeypatch):
    p = pipeline
    manager, lease, _ = manager_for(p, monkeypatch)
    try:
        monkeypatch.setattr(p.env.context, "_refresher", SimpleNamespace(is_running=lambda: True, close=lambda: None))
        with pytest.raises(CatalogError) as error:
            manager.start("refresh-busy", exclusive_confirmed=True)
        assert error.value.code == "MODEL_RESOURCE_BUSY"
        assert manager.status() is None
    finally:
        p.env.context.set_data_root_lease(None)
        lease.close()


def test_status_does_not_rewrite_invalid_catalog_identity(pipeline, monkeypatch):
    from photo_search.tagging import task

    p = pipeline
    manager, lease, _ = manager_for(p, monkeypatch)
    try:
        monkeypatch.setattr(manager, "_launch", lambda *args, **kwargs: None)
        state = manager.start("catalog", exclusive_confirmed=True)
        state["catalog_identity"] = {"exists": False, "path": "other"}
        path = p.catalog.data_root / "runtime" / task.TASK_FILENAME
        path.write_text(json.dumps(state), encoding="utf-8")
        raw = path.read_bytes()
        assert manager.status()["last_error"]["code"] == "CATALOG_IDENTITY_CHANGED"
        assert path.read_bytes() == raw
        with pytest.raises(CatalogError, match="different catalog"):
            manager.resume(state["task_id"])
    finally:
        p.env.context.set_data_root_lease(None)
        lease.close()


def test_cli_limit_includes_preexisting_queue_and_releases_runtime_and_os_lease(pipeline, monkeypatch):
    from photo_search.runtime.data_root_lease import acquire_data_root_lease
    from photo_search.tagging import cli

    p = pipeline
    p.env.add_photo("second.jpg", color=(10, 50, 90))
    p.env.scan(p.env.service.list_sources()[0]["source_id"])
    monkeypatch.setattr(cli, "read_model", lambda root: p.runtime.info)
    monkeypatch.setattr(cli.RamTagger, "load", lambda info: p.runtime)
    result = cli.build(p.catalog.data_root, limit=1)
    assert result["state"] == "TARGET_LIMIT" and result["processed"] == 1
    assert p.runtime.closed and p.catalog.read_one("SELECT COUNT(*) FROM machine_tag_results")[0] == 1
    with acquire_data_root_lease(p.catalog.data_root):
        pass


def test_cli_refuses_existing_process_lease_before_loading_model(pipeline, monkeypatch):
    from photo_search.runtime.data_root_lease import DataRootLeaseBusy, acquire_data_root_lease
    from photo_search.tagging import cli

    p = pipeline
    monkeypatch.setattr(cli, "read_model", lambda root: pytest.fail("must not inspect model after lease refusal"))
    with acquire_data_root_lease(p.catalog.data_root):
        with pytest.raises(DataRootLeaseBusy):
            cli.build(p.catalog.data_root)


def test_discovery_ignores_offline_sources_and_any_retention_tombstone(pipeline):
    p = pipeline
    with p.catalog.write() as db:
        db.execute("UPDATE source_roots SET status='OFFLINE'")
    assert service.discover_targets(p.catalog, processor_id=p.processor_id, after_id=0, through_id=999) == []
    with p.catalog.write() as db:
        db.execute("UPDATE source_roots SET status='ONLINE'")
    from photo_search.retention import activate_asset_retention_barrier

    with p.catalog.write() as db:
        activate_asset_retention_barrier(db, asset_id=p.env.first_asset_id(), now_ms=service.now_ms())
    assert service.discover_targets(p.catalog, processor_id=p.processor_id, after_id=0, through_id=999) == []


def test_bad_middle_image_does_not_stop_later_photos_or_claim_full_coverage(pipeline, monkeypatch):
    p = pipeline
    broken = p.env.add_photo("bad.jpg", color=(20, 70, 100))
    p.env.add_photo("third.jpg", color=(90, 40, 20))
    p.env.scan(p.env.service.list_sources()[0]["source_id"])
    broken.write_bytes(b"synthetic malformed jpeg")
    with p.catalog.write() as db:
        bad_id = db.execute("SELECT asset_id FROM file_locations WHERE relative_path='bad.jpg'").fetchone()[0]
        db.execute("UPDATE assets SET sha256=?,byte_size=? WHERE asset_id=?",
            (hashlib.sha256(broken.read_bytes()).hexdigest(), broken.stat().st_size, bad_id))
        db.execute("UPDATE file_locations SET size=?,mtime_ns=? WHERE asset_id=?",
            (broken.stat().st_size, broken.stat().st_mtime_ns, bad_id))
    manager, lease, runtimes = manager_for(p, monkeypatch)
    try:
        manager.start("keep-going", exclusive_confirmed=True)
        join_worker(manager)
        state = manager.status()
        assert state["status"] == "COMPLETED" and state["last_error"]["code"] == "IMAGES_SKIPPED"
        assert state["last_error"]["jobs"] == {"FAILED": 1}
        assert p.catalog.read_one("SELECT COUNT(*) FROM machine_tag_results")[0] == 2
        assert runtimes[0].calls == 2 and runtimes[0].closed
        assert p.catalog.read_one("SELECT last_error_code FROM jobs WHERE processor_id=? AND subject_id=?",
            (p.processor_id, str(bad_id)))[0] == "DECODE_FAILED"
    finally:
        manager.close()
        p.env.context.set_data_root_lease(None)
        lease.close()


def test_retry_waiting_uses128_job_transactions_and_current_content_only(pipeline):
    from photo_search.jobs import queue as jobs

    p = pipeline
    with p.catalog.write() as db:
        for number in range(1000, 1130):
            revision = f"{number:064x}"
            db.execute("INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) VALUES(?,?,1,1,'ACTIVE')",
                (number, revision))
            job_id = jobs.enqueue(db, subject_type="asset", subject_id=str(number), input_revision=revision,
                task_type=store.TASK_TYPE, processor_id=p.processor_id, now_ms=1)
            db.execute("UPDATE jobs SET state='FAILED' WHERE job_id=?", (job_id,))
    statements = []
    p.catalog.connection.set_trace_callback(statements.append)
    assert service.retry_waiting(p.catalog, processor_id=p.processor_id) == 128
    p.catalog.connection.set_trace_callback(None)
    update = next(sql for sql in statements if sql.startswith("UPDATE jobs SET state='QUEUED'"))
    plan = p.catalog.read_all("EXPLAIN QUERY PLAN " + update)
    assert not any("TEMP B-TREE" in str(row[3]) for row in plan)
    assert service.retry_waiting(p.catalog, processor_id=p.processor_id) == 2
    assert service.retry_waiting(p.catalog, processor_id=p.processor_id) == 0


def test_cli_status_old_catalog_is_read_only_and_reports_upgrade(tmp_path, monkeypatch, capsys):
    import sqlite3

    from photo_search.tagging import cli

    root = tmp_path / "old-data"
    path = root / "catalog/catalog.db"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)")
        db.execute("INSERT INTO metadata VALUES('schema_version','42')")
    original = path.read_bytes()
    monkeypatch.setattr(cli, "read_model", lambda root: model(root))
    assert cli.main(["status", "--data-root", str(root)]) == 2
    assert json.loads(capsys.readouterr().out)["state"] == "SCHEMA_UPGRADE_REQUIRED"
    assert path.read_bytes() == original


@pytest.mark.parametrize("worker,recovered", [("image-tags-cli", True), ("independent-worker", False)])
def test_recovery_only_expires_owned_cli_jobs_and_never_reexecutes_committed_result(pipeline, worker, recovered):
    p = pipeline
    request = p.scheduler.claim_next(worker_id=worker, now_ms=service.now_ms())
    before = p.catalog.read_one("SELECT lease_expires_at FROM jobs WHERE job_id=?", (request.job_id,))[0]
    assert service.recover_cli_leases(p.catalog, processor_id=p.processor_id) == int(recovered)
    expiry = p.catalog.read_one("SELECT lease_expires_at FROM jobs WHERE job_id=?", (request.job_id,))[0]
    assert (expiry <= service.now_ms()) if recovered else (expiry == before)
    if recovered:
        executor = service.tag_executor(p.catalog, p.runtime)
        assert service.run_one(p.scheduler, executor, worker_id="image-tags-cli").state == "SUCCEEDED"
        assert service.recover_cli_leases(p.catalog, processor_id=p.processor_id) == 0
        assert service.run_one(p.scheduler, executor, worker_id="image-tags-cli") is None
        assert p.runtime.calls == 1
