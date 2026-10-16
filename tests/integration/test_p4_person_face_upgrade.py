"""One v1 -> v2 publication keeps human knowledge and uses the existing review hold."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch

from p3_redetection_fixtures import RedetectionHarness, make_record, shifted_bbox, shifted_landmarks

from photo_search.artifacts.store import ArtifactStore
from photo_search.face import contract
from photo_search.face.engine import FaceDocument
from photo_search.face.processor import artifact_ref, build_payload, persist_face_document
from photo_search.persons import albums
from photo_search.scheduler.contracts import ArtifactRef


def test_v1_publication_then_v2_preserves_manual_anchor_and_holds_new_face(tmp_path):
    harness = RedetectionHarness(tmp_path)
    try:
        asset = harness.add_asset("synthetic-upgrade.jpg")
        person = harness.add_person(name="家人")
        record = make_record(ordinal=0, bbox=shifted_bbox(0), landmarks=shifted_landmarks(0))
        harness.write_face_document(
            asset, [record], spec_revision="face-buffalo-l-scrfd-arcface-v1"
        )
        old = str(
            harness.catalog.read_one("SELECT face_id FROM faces WHERE asset_id = ?", (asset,))[
                "face_id"
            ]
        )
        harness.add_link(old, person, assignment_kind="MANUAL", confidence=0.0)
        harness.set_control(old, "MANUAL_ASSIGNED")
        before = dict(harness.control(old))
        harness.write_face_document(asset, [record])
        generations = harness.catalog.read_all(
            "SELECT generation_id, spec_revision FROM face_index_generations"
        )
        assert len({row["generation_id"] for row in generations}) == 2
        assert {row["spec_revision"] for row in generations} == {
            "face-buffalo-l-scrfd-arcface-v1",
            contract.FACE_SPEC_REVISION,
        }
        assert dict(harness.control(old)) == before
        assert harness.effective_link(old)["person_id"] == person
        assert harness.group(old)["state"] == "OPEN"
        new = harness.mappings(old)[0]["new_face_id"]
        assert harness.effective_link(new) is None
        # Held faces belong to the existing explicit mapping review, not the easy correction queue.
        assert albums.asset_faces(harness.catalog, asset)["faces"] == []
        assert albums.inbox_page(harness.catalog, limit=20, cursor=None)["faces"] == []
    finally:
        harness.close()


def test_old_and_new_artifacts_coexist_and_no_face_publication_retires_machine_inbox(tmp_path):
    harness = RedetectionHarness(tmp_path)
    try:
        asset = harness.add_asset("synthetic-no-face.jpg")
        row = harness.catalog.read_one("SELECT sha256 FROM assets WHERE asset_id = ?", (asset,))
        revision = row["sha256"]
        with harness.catalog.write() as connection:
            job_id = connection.execute(
                "INSERT INTO jobs(subject_type, subject_id, input_revision, task_type, processor_id, state, run_after, created_at, updated_at, dispatch_spec_revision) VALUES ('asset', ?, ?, ?, ?, 'SUCCEEDED', 1, 1, 1, 'face-buffalo-l-scrfd-arcface-v1')",
                (str(asset), revision, contract.FACE_TASK_TYPE, contract.FACE_PROCESSOR_ID),
            ).lastrowid
        job = SimpleNamespace(job_id=job_id, subject_id=str(asset))
        result = SimpleNamespace(input_revision=revision)
        spec = SimpleNamespace(processor_id=contract.FACE_PROCESSOR_ID)
        store = ArtifactStore(harness.catalog.data_root)

        def publish(records):
            document = FaceDocument(
                original_size=(640, 480),
                working_size=(640, 480),
                scale=1.0,
                faces=tuple(records),
                elapsed_ms=1,
            )
            payload = build_payload(
                input_revision=revision, location_id=None, document=document, decode_format="JPEG"
            )
            prepared = [store.prepare(ArtifactRef.from_envelope(artifact_ref(payload)))]
            with harness.catalog.write() as connection:
                store.persist_result(
                    connection, job=job, result=result, spec=spec, prepared=prepared, now_ms=1
                )
                persist_face_document(
                    connection, job=job, result=result, prepared=prepared, now_ms=1
                )
            return prepared[0].ref.artifact_id

        old_config = {
            k: v for k, v in contract.ENGINE_CONFIG.items() if k != "image_preprocess_version"
        }
        old_hash = hashlib.sha256(
            json.dumps(
                old_config, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
        record = make_record(
            ordinal=0, bbox=shifted_bbox(0), landmarks=shifted_landmarks(0), quality_state="LOW"
        )
        with patch.multiple(
            contract,
            FACE_SPEC_REVISION="face-buffalo-l-scrfd-arcface-v1",
            FACE_ARTIFACT_REVISION="face-document.v1",
            ENGINE_CONFIG=old_config,
            CONFIG_HASH=old_hash,
        ):
            old_id = publish([record])
        old_row = harness.catalog.read_one(
            "SELECT * FROM artifacts WHERE artifact_id = ?", (old_id,)
        )
        before = store.read(old_row)
        assert len(albums.inbox_page(harness.catalog, limit=20, cursor=None)["faces"]) == 1
        # Merely redispatching the old job cannot hide its facts before a new publication.
        with harness.catalog.write() as connection:
            connection.execute(
                "UPDATE jobs SET dispatch_spec_revision = ? WHERE job_id = ?",
                (contract.FACE_SPEC_REVISION, job_id),
            )
        assert len(albums.inbox_page(harness.catalog, limit=20, cursor=None)["faces"]) == 1
        new_id = publish([])
        assert old_id != new_id
        artifacts = harness.catalog.read_all("SELECT * FROM artifacts")
        assert len(artifacts) == 2
        assert {a["artifact_revision"] for a in artifacts} == {
            "face-document.v1",
            "face-document.v2",
        }
        assert store.read(old_row) == before
        assert albums.inbox_page(harness.catalog, limit=20, cursor=None)["faces"] == []
        assert albums.asset_faces(harness.catalog, asset)["faces"] == []
    finally:
        harness.close()


def test_geometry_upgrade_keeps_old_people_and_matches_new_people_without_activating_partial_index(
    tmp_path,
):
    import pytest

    from photo_search.face.index import FaceIndexCoordinator, _InjectedCrash, reconcile_asset
    from photo_search.persons.operations import _repair_candidates

    harness = RedetectionHarness(tmp_path)
    try:
        old_asset = harness.add_asset("old-known.jpg")
        new_asset = harness.add_asset("new-person.jpg")
        next_asset = harness.add_asset("new-person-again.jpg")
        record = make_record(ordinal=0, bbox=shifted_bbox(0), landmarks=shifted_landmarks(0))
        harness.write_face_document(
            old_asset, [record], spec_revision="face-buffalo-l-scrfd-arcface-v1"
        )
        index = FaceIndexCoordinator(harness.catalog)
        # Current worker replays an outstanding legacy event under its original revision.
        index.process_all(now_ms=2)
        legacy_index = FaceIndexCoordinator(
            harness.catalog, spec_revision="face-buffalo-l-scrfd-arcface-v1"
        )
        old_person = reconcile_asset(
            harness.catalog,
            asset_id=old_asset,
            coordinator=legacy_index,
            spec_revision=legacy_index.spec_revision,
        )[0]["person_id"]
        index.process_all(now_ms=3)
        assert index.active_generation()["generation_id"] == legacy_index.generation_id
        assert {c.person_id for c in index.candidates()} == {old_person}

        # Almost orthogonal to the old person, so this really starts a new person.
        new_record = make_record(
            ordinal=0, bbox=shifted_bbox(0), landmarks=shifted_landmarks(0), seed=1000
        )
        harness.write_face_document(new_asset, [new_record])
        index.process_all(now_ms=4)
        new_person = reconcile_asset(harness.catalog, asset_id=new_asset)[0]["person_id"]
        assert new_person != old_person
        index.process_all(now_ms=5)
        assert index.active_generation()["generation_id"] == legacy_index.generation_id
        assert {c.person_id for c in index.candidates()} == {old_person, new_person}
        harness.write_face_document(next_asset, [new_record])
        index.process_all(now_ms=6)
        decision = reconcile_asset(harness.catalog, asset_id=next_asset)[0]
        assert decision["person_id"] == new_person
        assert harness.catalog.read_one("SELECT COUNT(*) AS n FROM persons")["n"] == 2

        with harness.catalog.write() as connection:
            connection.execute(
                "UPDATE face_index_generations SET state = 'PENDING' WHERE generation_id = ?",
                (index.generation_id,),
            )
        assert {c.person_id for c in index.candidates()} == {old_person}
        with harness.catalog.write() as connection:
            connection.execute(
                "UPDATE face_index_generations SET state = 'READY', dimension = 513 WHERE generation_id = ?",
                (index.generation_id,),
            )
        assert {c.person_id for c in index.candidates()} == {old_person}
        with harness.catalog.write() as connection:
            connection.execute(
                "UPDATE face_index_generations SET dimension = 512 WHERE generation_id = ?",
                (index.generation_id,),
            )
        with patch.object(contract, "MAX_REPRESENTATIVES_PER_PERSON", 1):
            assert len(index.candidates()) == 2
        # Future detection revisions do not inherit this geometry-only exception.
        future = FaceIndexCoordinator(harness.catalog, spec_revision="future-unknown")
        assert future.candidates() == []

        # A queued human repair excludes its published-but-unconfirmed representative.
        old_face = harness.catalog.read_one(
            "SELECT face_id FROM faces WHERE asset_id = ?", (old_asset,)
        )["face_id"]
        with harness.catalog.write() as connection:
            _repair_candidates(connection, [old_face], now_ms=7)
        assert old_person not in {c.person_id for c in index.candidates()}
        with pytest.raises(_InjectedCrash):
            index.process_next(now_ms=8, failpoint="after_publish")
        assert old_person not in {c.person_id for c in index.candidates()}
        index.process_all(now_ms=9)
        assert old_person in {c.person_id for c in index.candidates()}
        harness.set_control(old_face, "MANUAL_UNASSIGNED")
        assert old_person not in {c.person_id for c in index.candidates()}
    finally:
        harness.close()
