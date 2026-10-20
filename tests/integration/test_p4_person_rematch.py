"""Small synthetic stored-vector rematch; never loads models or scans a photo library."""

import json
from types import SimpleNamespace

import pytest
from p3_redetection_fixtures import RedetectionHarness, make_record, shifted_bbox, shifted_landmarks

from photo_search.face import contract
from photo_search.face.cli import main
from photo_search.face.index import FaceIndexCoordinator, reconcile_asset
from photo_search.face.service import rematch_unassigned

LEGACY = "face-buffalo-l-scrfd-arcface-v1"


def test_rematch_refuses_wrong_data_root_without_creating_an_empty_library(tmp_path, capsys):
    missing = tmp_path / "wrong-root"
    assert main(["rematch", "--data-root", str(missing), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "CATALOG_UNREADABLE"
    assert not missing.exists()
    assert main(["rematch", "--data-root", str(missing), "--limit", "-1", "--json"]) == 2
    capsys.readouterr()
    assert not missing.exists()


def stored_face(harness, label, *, revision=None, similarity=1.0, quality="OK"):
    asset = harness.add_asset(label)
    record = make_record(
        ordinal=0,
        bbox=shifted_bbox(0),
        landmarks=shifted_landmarks(0),
        seed=(1 / similarity**2 - 1) ** 0.5,
        quality_state=quality,
    )
    harness.write_face_document(asset, [record], spec_revision=revision)
    face = harness.catalog.read_one("SELECT face_id FROM faces WHERE asset_id = ?", (asset,))[
        "face_id"
    ]
    return asset, face


@pytest.mark.parametrize("revision", [LEGACY, contract.FACE_SPEC_REVISION])
def test_cli_rematches_drained_borderline_faces_without_models_and_keeps_human_facts(
    tmp_path, monkeypatch, capsys, revision
):
    from photo_search.face import weights

    def forbidden(*args, **kwargs):
        raise AssertionError("stored-vector rematch must not verify/load models")

    monkeypatch.setattr(weights, "resolve_and_verify", forbidden)
    harness = RedetectionHarness(tmp_path)
    try:
        known_asset, known = stored_face(harness, "known.jpg", revision=revision)
        index = FaceIndexCoordinator(harness.catalog, spec_revision=revision)
        index.process_all(now_ms=1)
        person = reconcile_asset(
            harness.catalog, asset_id=known_asset, coordinator=index, spec_revision=revision
        )[0]["person_id"]
        index.process_all(now_ms=2)
        pending_asset, pending = stored_face(
            harness, "borderline.jpg", revision=revision, similarity=0.53
        )
        _, removed = stored_face(harness, "removed.jpg", revision=revision)
        harness.set_control(removed, "MANUAL_UNASSIGNED")
        _, denied = stored_face(harness, "excluded.jpg", revision=revision)
        harness.set_control(denied, "AUTO_ALLOWED")
        harness.set_exclusion(denied, person, active=True)
        _, manual = stored_face(harness, "manual.jpg", revision=revision)
        harness.add_link(manual, person, assignment_kind="MANUAL", confidence=0.0)
        harness.set_control(manual, "MANUAL_ASSIGNED")
        index.process_all(now_ms=3)
        with monkeypatch.context() as old:
            old.setattr(contract, "MERGE_THRESHOLD", 0.56)
            outcome = reconcile_asset(
                harness.catalog, asset_id=pending_asset, coordinator=index, spec_revision=revision
            )
        assert outcome[0]["action"] == "SUGGESTED"
        # Isolate the previously drained queue: only the new explicit rematch can revive it.
        with harness.catalog.write() as connection:
            connection.execute("DELETE FROM face_association_queue")
        before = {
            table: [tuple(r) for r in harness.catalog.read_all(f"SELECT * FROM {table}")]
            for table in ("faces", "face_embeddings", "face_assignment_controls", "face_exclusions")
        }
        known_link = dict(harness.effective_link(known))
        manual_link = dict(harness.effective_link(manual))
        assert main(["rematch", "--data-root", str(harness.catalog.data_root), "--json"]) == 0
        result = json.loads(capsys.readouterr().out)["rematch"]
        assert result["merge_threshold"] == 0.50
        assert result["merged"] == 1
        assert harness.effective_link(pending)["person_id"] == person
        assert harness.effective_link(removed) is None
        assert harness.effective_link(denied)["person_id"] != person
        assert dict(harness.effective_link(known)) == known_link
        assert dict(harness.effective_link(manual)) == manual_link
        for table, rows in before.items():
            assert [tuple(r) for r in harness.catalog.read_all(f"SELECT * FROM {table}")] == rows
        again = rematch_unassigned(harness.catalog)
        assert again["merged"] == 0
        assert harness.effective_link(removed) is None
    finally:
        harness.close()


def test_rematch_preserves_redetection_hold_and_does_not_merge_low_quality(tmp_path):
    harness = RedetectionHarness(tmp_path)
    try:
        asset, known = stored_face(harness, "known.jpg")
        index = FaceIndexCoordinator(harness.catalog)
        index.process_all(now_ms=1)
        person = reconcile_asset(harness.catalog, asset_id=asset)[0]["person_id"]
        index.process_all(now_ms=2)
        _, blurry = stored_face(harness, "blurry.jpg", quality="LOW")
        held_asset, old = stored_face(harness, "held.jpg", revision=LEGACY)
        harness.add_link(old, person, assignment_kind="MANUAL", confidence=0.0)
        harness.set_control(old, "MANUAL_ASSIGNED")
        harness.write_face_document(
            held_asset,
            [make_record(ordinal=0, bbox=shifted_bbox(0), landmarks=shifted_landmarks(0))],
        )
        new = harness.mappings(old)[0]["new_face_id"]
        result = rematch_unassigned(harness.catalog)
        assert result["merged"] == 0
        assert harness.effective_link(blurry) is None
        assert harness.effective_link(new) is None
        assert harness.effective_link(old)["person_id"] == person
        assert harness.group(old)["state"] == "OPEN"
        assert harness.queue_row(held_asset) is not None
        assert harness.effective_link(known)["person_id"] == person
    finally:
        harness.close()


def test_rematch_zero_limit_does_no_work_and_missing_index_is_deferred(tmp_path):
    harness = RedetectionHarness(tmp_path)
    try:
        asset, face = stored_face(harness, "stored.jpg")
        before = list(harness.catalog.connection.iterdump())
        assert rematch_unassigned(harness.catalog, limit=0)["processed"] == 0
        assert list(harness.catalog.connection.iterdump()) == before
        # A non-published candidate must not be treated as a new unknown person.
        with harness.catalog.write() as connection:
            connection.execute("DELETE FROM face_index_outbox")
        result = rematch_unassigned(harness.catalog)
        assert result["deferred"] == 1
        assert harness.effective_link(face) is None
        assert harness.queue_row(asset) is not None
    finally:
        harness.close()


def test_rematch_does_not_process_superseded_or_foreign_faces_in_an_old_queue(tmp_path):
    from photo_search.artifacts.store import ArtifactStore
    from photo_search.face.engine import FaceDocument
    from photo_search.face.processor import artifact_ref, build_payload
    from photo_search.scheduler.contracts import ArtifactRef

    harness = RedetectionHarness(tmp_path)
    try:
        old_asset, old_face = stored_face(harness, "old.jpg", revision=LEGACY)
        # A successful new zero-face publication retires the old machine-only inbox.
        harness.write_face_document(old_asset, [])
        revision = harness.catalog.read_one(
            "SELECT sha256 FROM assets WHERE asset_id = ?", (old_asset,)
        )["sha256"]
        payload = build_payload(
            input_revision=revision,
            location_id=None,
            decode_format="JPEG",
            document=FaceDocument(
                original_size=(640, 480),
                working_size=(640, 480),
                scale=1.0,
                faces=(),
                elapsed_ms=1,
            ),
        )
        store = ArtifactStore(harness.catalog.data_root)
        prepared = [store.prepare(ArtifactRef.from_envelope(artifact_ref(payload)))]
        with harness.catalog.write() as connection:
            job_id = connection.execute(
                "INSERT INTO jobs(subject_type, subject_id, input_revision, task_type, "
                "processor_id, state, run_after, created_at, updated_at) "
                "VALUES ('asset', ?, ?, ?, ?, 'SUCCEEDED', 1, 1, 1)",
                (str(old_asset), revision, contract.FACE_TASK_TYPE, contract.FACE_PROCESSOR_ID),
            ).lastrowid
            store.persist_result(
                connection,
                job=SimpleNamespace(
                    job_id=job_id, subject_type="asset", subject_id=str(old_asset)
                ),
                result=SimpleNamespace(input_revision=revision),
                spec=SimpleNamespace(processor_id=contract.FACE_PROCESSOR_ID),
                prepared=prepared,
                now_ms=1,
            )
        foreign_asset, foreign_face = stored_face(harness, "foreign.jpg")
        with harness.catalog.write() as connection:
            connection.execute(
                "UPDATE face_embeddings SET space_id = 'foreign-face-space' WHERE face_id = ?",
                (foreign_face,),
            )
            connection.execute("DELETE FROM face_index_outbox")
            connection.execute(
                "UPDATE face_association_queue SET spec_revision = ? WHERE asset_id = ?",
                (LEGACY, old_asset),
            )
        result = rematch_unassigned(harness.catalog)
        assert result["selected_assets"] == result["processed"] == 0
        assert harness.effective_link(old_face) is None
        assert harness.effective_link(foreign_face) is None
        assert harness.queue_row(old_asset) is not None
        assert harness.queue_row(foreign_asset) is not None
    finally:
        harness.close()
