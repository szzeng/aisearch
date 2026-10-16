"""Bounded DETAIL-B lifecycle checks; all photos/vectors here are synthetic."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from p3_persons_page_fixtures import add_link, add_person, seed_library, set_control
from p3_redetection_fixtures import make_record, shifted_bbox, shifted_landmarks

from photo_search.api.face_detail_runner import FaceDetailRunner
from photo_search.artifacts.store import ArtifactStore
from photo_search.catalog import schema
from photo_search.catalog.db import Catalog
from photo_search.face import contract, detail_policy
from photo_search.face import service as faces
from photo_search.face import source_detail_batches as batches
from photo_search.face.engine import FaceDocument
from photo_search.face.index import FaceIndexCoordinator, _InjectedCrash, reconcile, reconcile_asset
from photo_search.face.processor import (
    artifact_ref,
    build_payload,
    face_processor_spec,
    persist_face_document,
)
from photo_search.jobs import queue as jobs
from photo_search.persons import albums
from photo_search.persons import mapping as mapping_module
from photo_search.persons.service import PersonService
from photo_search.refresh.pipeline import _face_stage, resolve_face_profile
from photo_search.scheduler.contracts import ArtifactRef, RuntimeCapabilities


def record(ordinal=0, *, seed=0):
    return make_record(
        ordinal=ordinal,
        bbox=shifted_bbox(ordinal * 130),
        landmarks=shifted_landmarks(ordinal * 130),
        seed=seed,
    )


def publish(catalog, asset_id, records, *, source=False, automatic=False, frame=(640, 480)):
    revision = catalog.read_one("SELECT sha256 FROM assets WHERE asset_id=?", (asset_id,))[0]
    task_type = (
        contract.FACE_DETAIL_AUTO_TASK_TYPE
        if automatic
        else contract.FACE_DETAIL_TASK_TYPE
        if source
        else contract.FACE_TASK_TYPE
    )
    with catalog.write() as connection:
        job_id = jobs.enqueue(
            connection,
            subject_type="asset",
            subject_id=str(asset_id),
            input_revision=revision,
            task_type=task_type,
            processor_id=contract.FACE_PROCESSOR_ID,
            now_ms=1,
        )
        connection.execute("UPDATE jobs SET state='SUCCEEDED' WHERE job_id=?", (job_id,))
    payload = build_payload(
        input_revision=revision,
        location_id=None,
        decode_format="JPEG",
        source_detail=source,
        document=FaceDocument(frame, frame, 1.0, tuple(records), 1),
    )
    store = ArtifactStore(catalog.data_root)
    prepared = [store.prepare(ArtifactRef.from_envelope(artifact_ref(payload)))]
    job = SimpleNamespace(job_id=job_id, subject_id=str(asset_id), task_type=task_type)
    result = SimpleNamespace(input_revision=revision)
    with catalog.write() as connection:
        store.persist_result(
            connection,
            job=job,
            result=result,
            spec=face_processor_spec(source_detail=source, automatic_detail=automatic),
            prepared=prepared,
            now_ms=1,
        )
        persist_face_document(connection, job=job, result=result, prepared=prepared, now_ms=1)
    revision = contract.FACE_DETAIL_SPEC_REVISION if source else contract.FACE_SPEC_REVISION
    return [
        str(row[0])
        for row in catalog.read_all(
            "SELECT face_id FROM faces WHERE asset_id=? AND spec_revision=? ORDER BY ordinal",
            (asset_id, revision),
        )
    ]


def settle(catalog, asset):
    index = FaceIndexCoordinator(catalog, spec_revision=contract.FACE_DETAIL_SPEC_REVISION)
    index.process_all(now_ms=3)
    result = reconcile(
        catalog,
        coordinator=index,
        spec_revision=contract.FACE_DETAIL_SPEC_REVISION,
        asset_ids=[asset],
        limit=1,
    )
    index.process_all(now_ms=4)
    return result


def synthetic_runtime(scheduler, spec, cache, *, now_ms, **kwargs):
    provider = {
        "name": "CPUExecutionProvider",
        "device": "cpu",
        "available": True,
        "health": "healthy",
        "precisions": ["FP32"],
        "health_checked_at": now_ms,
        "evidence_id": None,
    }
    snapshot = RuntimeCapabilities.seal(
        {
            "runtime_package_id": spec.runtime_package_id,
            "runtime_backend": "onnxruntime",
            "runtime_package_name": "onnxruntime-gpu",
            "runtime_package_version": spec.runtime_package_id.split("@")[1],
            "python_version": "3.12.10",
            "platform": "windows-x86_64",
            "driver_version": None,
            "providers": [provider],
            "health_status": "healthy",
            "health_checked_at": now_ms,
            "measured_at": now_ms,
            "evidence_ids": [],
            "failure_code": None,
        }
    )
    scheduler.record_capability(snapshot, now_ms=now_ms)
    scheduler.record_qualification(
        processor_id=spec.processor_id,
        spec_revision=spec.spec_revision,
        device="cpu",
        precision="FP32",
        provider_name="CPUExecutionProvider",
        runtime_package_id=spec.runtime_package_id,
        now_ms=now_ms,
    )
    return {"synthetic_runtime": True}


def synthetic_executor(catalog, **options):
    def run(request):
        payload = build_payload(
            input_revision=request["input_revision"],
            location_id=None,
            decode_format="JPEG",
            source_detail=options["preserve_source_detail"],
            document=FaceDocument((320, 240), (320, 240), 1.0, (), 1),
        )
        return {
            "contract_version": contract.CONTRACT_VERSION,
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
            "artifacts": [artifact_ref(payload)],
            "error": None,
            "completed_at": catalog.now_ms(),
        }

    return run


def test_batch_scope_dedup_quality_and_late_manual_skip(api_env):
    library = seed_library(
        api_env,
        {
            "group.jpg": [(40, 30, 120, 110), (150, 30, 220, 110)],
            "manual.jpg": [(40, 30, 120, 110)],
            "blur.jpg": [(40, 30, 120, 110)],
            "zero.jpg": [],
        },
    )
    catalog = api_env.service.catalog
    set_control(catalog, library["manual.jpg"]["faces"][0], "MANUAL_UNASSIGNED")
    set_control(catalog, library["group.jpg"]["faces"][0], "MANUAL_UNASSIGNED")
    with catalog.write() as connection:
        connection.execute(
            "UPDATE faces SET quality_state='LOW',quality_reason='BLURRY' WHERE face_id=?",
            (library["blur.jpg"]["faces"][0],),
        )
    source = catalog.read_one("SELECT source_id FROM source_roots")[0]
    assert batches.preview(catalog, source_id=source)["total"] == 1
    batch = batches.create_batch(catalog, source_id=source)
    assert batches.create_batch(catalog, source_id=source)["batch_id"] == batch["batch_id"]
    assert len(batches.next_page(catalog, batch["batch_id"])) == 1
    item = batches.next_item(catalog, batch["batch_id"])
    set_control(catalog, library["group.jpg"]["faces"][1], "MANUAL_UNASSIGNED")
    assert batches.skip_if_ineligible(catalog, batch["batch_id"], item)
    batches.sync_jobs(catalog, batch["batch_id"])
    batches.next_page(catalog, batch["batch_id"])
    assert batches.status(catalog, batch["batch_id"])["skipped"] == 1
    assert (
        catalog.read_one("SELECT state FROM jobs WHERE job_id=?", (item["job_id"],))[0] == "QUEUED"
    )


def test_manual_result_restart_restores_publication_without_inference(api_env, monkeypatch):
    photo = seed_library(api_env, {"cached.jpg": [(40, 30, 120, 110)]})["cached.jpg"]
    catalog = api_env.service.catalog
    asset = photo["asset_id"]
    ids = publish(catalog, asset, [record()], source=True)
    batch = batches.create_batch(catalog)
    page = batches.next_page(catalog, batch["batch_id"])
    assert page[0]["job_id"] is None
    assert detail_policy.restore(catalog, asset_id=asset, input_revision=photo["sha256"])
    index = FaceIndexCoordinator(catalog, spec_revision=contract.FACE_DETAIL_SPEC_REVISION)
    with pytest.raises(_InjectedCrash):
        index.process_next(now_ms=2, failpoint="after_publish")
    batches.sync_jobs(catalog, batch["batch_id"])
    assert batches.status(catalog, batch["batch_id"])["succeeded"] == 0
    reopened = Catalog.open(catalog.db_path, data_root=catalog.data_root)
    try:
        assert batches.running_batches(reopened) == [batch["batch_id"]]
        monkeypatch.setattr(
            faces, "run_queue", lambda *a, **kw: pytest.fail("cached result inferred again")
        )
        runner = FaceDetailRunner(lambda: None)
        runner._run_batch(
            SimpleNamespace(catalog=reopened, data_root=reopened.data_root), batch["batch_id"]
        )
        state = batches.status(reopened, batch["batch_id"])
        assert state["state"] == "COMPLETE" and state["succeeded"] == 1
        assert [face["face_id"] for face in albums.asset_faces(reopened, asset)["faces"]] == ids
    finally:
        reopened.close()


def test_cross_profile_candidates_merge_without_old_machine_occupancy(api_env):
    catalog = api_env.service.catalog
    photo = seed_library(api_env, {"known.jpg": [], "mixed.jpg": []})
    old = publish(catalog, photo["known.jpg"]["asset_id"], [record()])[0]
    person = add_person(catalog, name="家人")
    add_link(catalog, old, person)
    prior = publish(catalog, photo["mixed.jpg"]["asset_id"], [record()])[0]
    add_link(catalog, prior, person)
    FaceIndexCoordinator(catalog).process_all(now_ms=2)
    asset = photo["mixed.jpg"]["asset_id"]
    new = publish(catalog, asset, [record(seed=0.2)], source=True, automatic=True)[0]
    result = settle(catalog, asset)
    assert result["merged"] == 1 and result["new_groups"] == 0
    assert (
        catalog.read_one(
            "SELECT person_id FROM face_person_links WHERE face_id=? AND decision='CONFIRMED'",
            (new,),
        )[0]
        == person
    )
    assert [face["face_id"] for face in albums.asset_faces(catalog, asset)["faces"]] == [new]
    assert catalog.read_one("SELECT COUNT(*) FROM faces WHERE asset_id=?", (asset,))[0] == 2
    assert (
        catalog.read_one(
            "SELECT is_active FROM face_index_generations WHERE spec_revision=?",
            (contract.FACE_DETAIL_SPEC_REVISION,),
        )[0]
        == 0
    )
    index = FaceIndexCoordinator(catalog, spec_revision=contract.FACE_DETAIL_SPEC_REVISION)
    assert new in {item.face_id for item in index.candidates()}
    with catalog.write() as connection:
        connection.execute(
            "UPDATE face_index_generations SET spec_revision='unsupported-source-v99' WHERE generation_id=?",
            (index.generation_id,),
        )
    assert index.active_generation() is None and index.candidates() == []


def test_late_human_anchor_hold_and_compatibility_gate(api_env, monkeypatch):
    catalog = api_env.service.catalog
    asset = seed_library(api_env, {"late.jpg": []})["late.jpg"]["asset_id"]
    old = publish(catalog, asset, [record()])[0]
    new = publish(catalog, asset, [record()], source=True, automatic=True)[0]
    index = FaceIndexCoordinator(catalog, spec_revision=contract.FACE_DETAIL_SPEC_REVISION)
    index.process_all(now_ms=2)
    person = add_person(catalog, name="保留人工记录")
    add_link(catalog, old, person, assignment_kind="MANUAL")
    set_control(catalog, old, "MANUAL_ASSIGNED")
    outcome = reconcile_asset(
        catalog, coordinator=index, asset_id=asset, spec_revision=contract.FACE_DETAIL_SPEC_REVISION
    )
    assert outcome[0]["action"] == "PROVISIONAL_HOLD"
    assert catalog.read_one("SELECT 1 FROM face_person_links WHERE face_id=?", (new,)) is None
    assert (
        catalog.read_one("SELECT person_id FROM face_person_links WHERE face_id=?", (old,))[0]
        == person
    )
    with monkeypatch.context() as changed:
        changed.setattr(detail_policy, "VERIFIED_ORDINARY_CONFIG_HASH", "0" * 64)
        assert not detail_policy.compatibility_verified()
        assert index.candidates() == []
        assert settle(catalog, asset)["processed"] == 0
    monkeypatch.setattr(detail_policy, "VERIFIED_DETAIL_CONFIG_HASH", "0" * 64)
    assert not detail_policy.compatibility_verified()
    assert index.candidates() == []
    assert settle(catalog, asset)["processed"] == 0


def test_build_actual_face_stage_profiles_zero_faces_and_paused_job_isolation(api_env, monkeypatch):
    catalog = api_env.service.catalog
    library = seed_library(
        api_env, {"batch.jpg": [(40, 30, 120, 110)], "new.jpg": [], "light.jpg": []}
    )
    batch = batches.create_batch(catalog)
    queued = batches.next_page(catalog, batch["batch_id"])[0]
    batches.pause(catalog, batch["batch_id"])
    monkeypatch.setattr("photo_search.face.weights.resolve_and_verify", lambda *a: None)
    monkeypatch.setattr(faces, "record_runtime_evidence", synthetic_runtime)
    monkeypatch.setattr(faces, "face_executor", synthetic_executor)
    for name, detail in (("new.jpg", True), ("light.jpg", False)):
        result = _face_stage(
            catalog, api_env.data_root, {library[name]["asset_id"]}, source_detail=detail
        )
        assert result["steps"] == 1
        kind = contract.FACE_DETAIL_AUTO_TASK_TYPE if detail else contract.FACE_TASK_TYPE
        job = catalog.read_one(
            "SELECT state,dispatch_spec_revision FROM jobs WHERE subject_id=? AND task_type=?",
            (str(library[name]["asset_id"]), kind),
        )
        assert job[0] == "SUCCEEDED"
        assert job[1] == (
            contract.FACE_DETAIL_SPEC_REVISION if detail else contract.FACE_SPEC_REVISION
        )
    assert (
        catalog.read_one("SELECT state FROM jobs WHERE job_id=?", (queued["job_id"],))[0]
        == "QUEUED"
    )
    assert (
        catalog.read_one(
            "SELECT ready FROM face_detail_auto_assets WHERE asset_id=?",
            (library["new.jpg"]["asset_id"],),
        )[0]
        == 1
    )
    assert (
        catalog.read_one(
            "SELECT COUNT(*) FROM faces WHERE asset_id=?", (library["new.jpg"]["asset_id"],)
        )[0]
        == 0
    )
    # The serial runner also honors a pause arriving before it gets the shared slot.
    run = faces.run_queue(
        catalog,
        source_detail=True,
        automatic_detail=True,
        job_ids=[queued["job_id"]],
        claim_check=lambda: False,
    )
    assert run["steps"] == 0


def test_build_profile_survives_reopen(api_env):
    catalog = api_env.service.catalog
    assert resolve_face_profile(catalog, source_ids=[1], requested=None) == "source_detail"
    assert resolve_face_profile(catalog, source_ids=[1], requested="lightweight") == "lightweight"
    reopened = Catalog.open(catalog.db_path, data_root=catalog.data_root)
    try:
        assert resolve_face_profile(reopened, source_ids=[1], requested=None) == "lightweight"
        assert resolve_face_profile(reopened, source_ids=[2], requested=None) == "source_detail"
    finally:
        reopened.close()


def test_mapping_confirm_undo_requeues_actual_source_profile(api_env):
    catalog = api_env.service.catalog
    asset = seed_library(api_env, {"mapping.jpg": []})["mapping.jpg"]["asset_id"]
    old = publish(catalog, asset, [record()])[0]
    person = add_person(catalog, name="人工家人")
    add_link(catalog, old, person, assignment_kind="MANUAL")
    set_control(catalog, old, "MANUAL_ASSIGNED")
    new = publish(catalog, asset, [record()], source=True, automatic=True)[0]
    settle(catalog, asset)
    group = catalog.read_one(
        "SELECT revision FROM face_redetection_groups WHERE old_face_id=?", (old,)
    )
    service = PersonService(catalog)
    with catalog.write() as connection:
        connection.execute("DELETE FROM face_detail_association_queue WHERE asset_id=?", (asset,))
    result = service.decide_face_mappings(
        payload=None,
        contract_version=mapping_module.CONTRACT_VERSION,
        request_id="f69b12c7-2809-4c6d-b02b-d9f45753310d",
        asset_id=asset,
        decisions=[
            {
                "old_face_id": old,
                "expected_review_revision": group[0],
                "decision": "confirm",
                "targets": [{"new_face_id": new, "inherits_person": True}],
            }
        ],
    )
    assert (
        catalog.read_one(
            "SELECT person_id FROM face_person_links WHERE face_id=? AND decision='CONFIRMED'",
            (new,),
        )[0]
        == person
    )
    assert catalog.read_one("SELECT spec_revision FROM face_detail_association_queue WHERE asset_id=?", (asset,))[0] == contract.FACE_DETAIL_SPEC_REVISION
    settle(catalog, asset)
    assert catalog.read_one("SELECT 1 FROM face_detail_association_queue WHERE asset_id=?", (asset,)) is None
    service.undo(
        operation_id=result["operation_id"],
        request_id="96b82f1b-4bf5-48f0-9cf3-f8a90d80a880",
        expected_operation_revision=result["operation_revision"],
    )
    assert (
        catalog.read_one("SELECT state FROM face_redetection_groups WHERE old_face_id=?", (old,))[0]
        == "OPEN"
    )
    assert (
        catalog.read_one(
            "SELECT spec_revision FROM face_detail_association_queue WHERE asset_id=?", (asset,)
        )[0]
        == contract.FACE_DETAIL_SPEC_REVISION
    )
    outcome = settle(catalog, asset)
    assert any(item["action"] == "PROVISIONAL_HOLD" for item in outcome["decisions"])
    assert (
        catalog.read_one(
            "SELECT person_id FROM face_person_links WHERE face_id=? AND decision='CONFIRMED'",
            (old,),
        )[0]
        == person
    )


def test_batch_single_failure_continues_and_retry_only_failed(api_env, monkeypatch):
    library = seed_library(api_env, {f"retry-{i}.jpg": [(40, 30, 120, 110)] for i in range(3)})
    catalog = api_env.service.catalog
    batch = batches.create_batch(catalog)
    failing = min(photo["asset_id"] for photo in library.values())
    counts = {}
    flushes = 0
    process_all = FaceIndexCoordinator.process_all
    monkeypatch.setattr(faces, "record_runtime_evidence", synthetic_runtime)

    def counted_process_all(self, **options):
        nonlocal flushes
        flushes += 1
        return process_all(self, **options)

    monkeypatch.setattr(FaceIndexCoordinator, "process_all", counted_process_all)

    def factory(catalog, **options):
        success = synthetic_executor(catalog, **options)

        def execute(request):
            asset = int(request["subject_id"])
            counts[asset] = counts.get(asset, 0) + 1
            result = success(request)
            if asset == failing and counts[asset] == 1:
                result.update(
                    state="FAILED",
                    artifacts=[],
                    progress=0.0,
                    error={
                        "code": "FACE_INVALID_IMAGE",
                        "message": "synthetic failure",
                        "retryable": False,
                        "blocking_reason": None,
                    },
                )
            return result

        return execute

    monkeypatch.setattr(faces, "face_executor", factory)
    runner = FaceDetailRunner(lambda: None)
    service = SimpleNamespace(catalog=catalog, data_root=catalog.data_root)
    runner._run_batch(service, batch["batch_id"])
    state = batches.status(catalog, batch["batch_id"])
    assert state["state"] == "PARTIAL_FAILED" and state["succeeded"] == 2 and state["failed"] == 1
    assert flushes == 3  # one pre-reconcile publication per photo; no extra empty flush
    batches.resume(catalog, batch["batch_id"], retry_failed=True)
    runner._run_batch(service, batch["batch_id"])
    state = batches.status(catalog, batch["batch_id"])
    assert state["state"] == "COMPLETE" and state["succeeded"] == 3
    assert counts[failing] == 2 and sorted(counts.values()) == [1, 1, 2]
    assert flushes == 4  # retry performs one pre-reconcile publication


def test_batch_api_session_csrf_scope_and_resume(api_env):
    library = seed_library(api_env, {"api.jpg": [(40, 30, 120, 110)]})
    source = api_env.service.catalog.read_one("SELECT source_id FROM source_roots")[0]

    class WakeOnly:
        def submit_batch(self, identifier):
            return True

        def close(self):
            pass

    api_env.context._face_detail_runner = WakeOnly()

    async def scenario(client):
        url = "/api/v1/faces/source-detail-batches"
        assert (await client.post(url, json={"source_id": source})).status_code == 401
        csrf = await api_env.authenticate(client)
        assert (await client.post(url, json={"source_id": source})).status_code == 403
        headers = api_env.post_headers(csrf)
        preview = await client.get(f"/api/v1/faces/source-detail-preview?source_id={source}")
        assert preview.json()["total"] == len(library)
        response = await client.post(url, json={"source_id": source}, headers=headers)
        assert response.status_code == 200
        identifier = response.json()["batch_id"]
        assert (await client.post(url, json={"source_id": source}, headers=headers)).json()[
            "batch_id"
        ] == identifier
        assert (await client.post(f"{url}/{identifier}/pause", json={}, headers=headers)).json()[
            "state"
        ] == "PAUSED"
        assert (await client.get(f"{url}/active?source_id={source}")).json()["state"] == "PAUSED"
        assert (await client.post(f"{url}/{identifier}/resume", json={}, headers=headers)).json()[
            "state"
        ] == "QUEUED"

    api_env.scenario(scenario)


def test_schema19_upgrade_preserves_manual_facts_and_batch_cursor(api_env):
    catalog = api_env.service.catalog
    photo = seed_library(api_env, {"upgrade.jpg": [(40, 30, 120, 110)]})["upgrade.jpg"]
    face = photo["faces"][0]
    person = add_person(catalog, name="升级保护")
    add_link(catalog, face, person, assignment_kind="MANUAL")
    set_control(catalog, face, "MANUAL_ASSIGNED")
    batch = batches.create_batch(catalog)
    path = api_env.tmp_path / "schema19" / "catalog" / "catalog.db"
    path.parent.mkdir(parents=True)
    destination = sqlite3.connect(path)
    # Build the genuine historical prefix; relabelling today's schema as 19 leaves
    # future objects behind and must be rejected by the production schema gate.
    for version, script in schema.migration_files():
        if version > 19:
            break
        destination.executescript(script.read_text(encoding="utf-8"))
    for table in schema.business_tables(destination):
        if table.startswith("search_documents_fts"):
            continue  # The search_documents triggers populate this derived FTS table.
        columns = [row[1] for row in destination.execute(f'PRAGMA table_info("{table}")')]
        names = ",".join(f'"{column}"' for column in columns)
        values = catalog.connection.execute(f'SELECT {names} FROM "{table}"').fetchall()
        destination.executemany(
            f'INSERT OR REPLACE INTO "{table}" ({names}) VALUES ({",".join("?" for _ in columns)})',
            [tuple(row) for row in values],
        )
    destination.execute("UPDATE metadata SET value='19' WHERE key='schema_version'")
    destination.commit()
    schema.verify_schema(destination, 19)
    assert not destination.execute("PRAGMA foreign_key_check").fetchall()
    destination.close()
    upgraded = Catalog.open(path, data_root=path.parent.parent)
    try:
        assert upgraded.schema_version == schema.SUPPORTED_SCHEMA_VERSION
        assert upgraded.read_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cn_admin_place_refs'"
        ) is not None
        assert upgraded.read_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='geo_dataset_place_refs'"
        ) is not None
        assert (
            upgraded.read_one("SELECT mode FROM face_assignment_controls WHERE face_id=?", (face,))[
                0
            ]
            == "MANUAL_ASSIGNED"
        )
        assert (
            upgraded.read_one("SELECT person_id FROM face_person_links WHERE face_id=?", (face,))[0]
            == person
        )
        assert batches.status(upgraded, batch["batch_id"])["state"] == "QUEUED"
        assert upgraded.read_one("SELECT COUNT(*) FROM face_detail_auto_assets")[0] == 0
    finally:
        upgraded.close()


def test_jpeg_draft_frame_mapping_protects_manual_unassigned_and_unknown_frame(api_env):
    from dataclasses import replace

    from photo_search.persons.mapping import hold_face_ids

    catalog = api_env.service.catalog
    asset = seed_library(api_env, {"draft.jpg": []})["draft.jpg"]["asset_id"]
    old_record = record()
    old = publish(catalog, asset, [old_record], frame=(320, 240))[0]
    set_control(catalog, old, "MANUAL_UNASSIGNED")
    old_artifact = catalog.read_one("SELECT artifact_id FROM faces WHERE face_id=?", (old,))[0]
    with catalog.write() as connection:
        connection.execute(
            "DELETE FROM face_detail_coordinate_frames WHERE artifact_id=?", (old_artifact,)
        )
    detail_policy.prepare_coordinate_frames(catalog, asset_id=asset)
    assert (
        catalog.read_one(
            "SELECT width FROM face_detail_coordinate_frames WHERE artifact_id=?", (old_artifact,)
        )[0]
        == 320
    )
    scaled = replace(
        old_record,
        bbox=tuple(value * 2 for value in old_record.bbox),
        landmarks=tuple((x * 2, y * 2) for x, y in old_record.landmarks),
    )
    new = publish(catalog, asset, [scaled], source=True, automatic=True)[0]
    outcome = settle(catalog, asset)
    assert outcome["decisions"][0]["action"] == "PROVISIONAL_HOLD"
    assert (
        catalog.read_one(
            "SELECT match_state FROM face_redetection_mappings WHERE old_face_id=? AND new_face_id=?",
            (old, new),
        )[0]
        == "HIGH"
    )
    assert catalog.read_one("SELECT 1 FROM face_person_links WHERE face_id=?", (new,)) is None
    with catalog.write() as connection:
        connection.execute(
            "DELETE FROM face_detail_coordinate_frames WHERE artifact_id=?", (old_artifact,)
        )
        connection.execute("DELETE FROM face_redetection_mappings WHERE old_face_id=?", (old,))
    # No guessed scale: if the verified old frame is unavailable, every new face waits.
    assert hold_face_ids(
        catalog.connection, asset_id=asset, spec_revision=contract.FACE_DETAIL_SPEC_REVISION
    ) == {new}
    assert settle(catalog, asset)["decisions"][0]["action"] == "PROVISIONAL_HOLD"
