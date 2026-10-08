"""Opt-in source-detail requests use a separate durable queue profile."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

from conftest import ApiEnv
from p3_persons_page_fixtures import add_link, add_person, seed_library, set_control

from photo_search.api.face_detail_runner import FaceDetailRunner
from photo_search.artifacts.store import ArtifactStore, PreparedArtifact
from photo_search.face import contract, source_detail_batches
from photo_search.face.engine import FaceDocument, FaceRecord
from photo_search.face.processor import (
    artifact_ref,
    build_payload,
    face_executor,
    persist_face_document,
)
from photo_search.face.service import enqueue_targets
from photo_search.persons import albums
from photo_search.persons.mapping import hold_face_ids
from photo_search.scheduler.contracts import ArtifactRef


def test_source_detail_route_is_session_csrf_protected_and_deduplicated(api_env: ApiEnv) -> None:
    asset_id = int(
        seed_library(api_env, {"detail.jpg": [(40, 30, 120, 110)]})["detail.jpg"]["asset_id"]
    )
    catalog = api_env.service.catalog
    enqueue_targets(
        catalog,
        [
            {
                "asset_id": asset_id,
                "input_revision": catalog.read_one(
                    "SELECT sha256 FROM assets WHERE asset_id = ?", (asset_id,)
                )["sha256"],
            }
        ],
        now_ms=catalog.now_ms(),
        device="cpu",
    )

    class WakeOnly:
        submitted: list[int] = []
        batches: list[str] = []

        def submit(self, value: int) -> bool:
            self.submitted.append(int(value))
            return True

        def submit_batch(self, value: str) -> bool:
            self.batches.append(str(value))
            return True

    wake = WakeOnly()
    api_env.context._face_detail_runner = wake

    async def scenario(client):
        url = f"/api/v1/assets/{asset_id}/face-detail"
        batch_url = "/api/v1/faces/source-detail-batches"
        assert (await client.post(url, json={})).status_code == 401
        assert (await client.post(batch_url, json={})).status_code == 401
        csrf = await api_env.authenticate(client)
        assert (await client.post(url, json={})).status_code == 403
        headers = api_env.post_headers(csrf)
        first = await client.post(url, json={}, headers=headers)
        second = await client.post(url, json={}, headers=headers)
        assert first.status_code == second.status_code == 200
        assert first.json()["state"] == "QUEUED"
        assert first.json()["already_available"] is False
        assert second.json()["job_id"] == first.json()["job_id"]
        rows = catalog.read_all(
            "SELECT task_type FROM jobs WHERE subject_type = 'asset' AND subject_id = ? "
            "ORDER BY task_type",
            (str(asset_id),),
        )
        assert [str(row["task_type"]) for row in rows] == sorted(
            [contract.FACE_TASK_TYPE, contract.FACE_DETAIL_TASK_TYPE]
        )
        assert wake.submitted == [asset_id, asset_id]

        assert (await client.post(batch_url, json={})).status_code == 403
        first_batch = await client.post(batch_url, json={}, headers=headers)
        second_batch = await client.post(batch_url, json={}, headers=headers)
        assert first_batch.status_code == second_batch.status_code == 200
        assert first_batch.json()["total"] == 1
        assert second_batch.json()["batch_id"] == first_batch.json()["batch_id"]
        assert wake.batches == [first_batch.json()["batch_id"], second_batch.json()["batch_id"]]

    api_env.scenario(scenario)


def test_face_detail_runner_serializes_wakes_and_coalesces_duplicates() -> None:
    entered = threading.Event()
    release = threading.Event()
    seen: list[int] = []

    def execute(_service, asset_id: int) -> None:
        seen.append(asset_id)
        if asset_id == 1:
            entered.set()
            assert release.wait(5)

    runner = FaceDetailRunner(lambda: object(), executor=execute)
    try:
        assert runner.submit(1)
        assert entered.wait(5)
        assert not runner.submit(1)
        assert runner.submit(2)
        release.set()
        assert runner.wait_idle(5)
        assert seen == [1, 2]
    finally:
        release.set()
        runner.close()


def test_source_detail_batch_is_bounded_pausable_and_retryable(api_env: ApiEnv) -> None:
    seed_library(api_env, {f"batch-{index:03}.jpg": [(40, 30, 120, 110)] for index in range(3)})
    catalog = api_env.service.catalog
    batch = source_detail_batches.create_batch(catalog)
    identifier = batch["batch_id"]
    assert batch["total"] == 3
    page = source_detail_batches.next_page(catalog, identifier)
    assert len(page) == 3 <= source_detail_batches.PAGE_SIZE
    assert len({item["asset_id"] for item in page}) == 3
    assert source_detail_batches.next_page(catalog, identifier) == []
    assert source_detail_batches.pause(catalog, identifier)["state"] == "PAUSED"
    assert source_detail_batches.next_page(catalog, identifier) == []
    assert source_detail_batches.resume(catalog, identifier)["state"] == "QUEUED"
    with catalog.write() as connection:
        connection.execute("UPDATE jobs SET state='SUCCEEDED' WHERE job_id=?", (page[0]["job_id"],))
        connection.execute(
            "UPDATE jobs SET state='FAILED',last_error_code='SYNTHETIC' WHERE job_id=?",
            (page[1]["job_id"],),
        )
    source_detail_batches.sync_jobs(catalog, identifier)
    state = source_detail_batches.status(catalog, identifier)
    # Inference completion alone cannot report success or discard the derived work.
    assert state["succeeded"] == 0 and state["failed"] == 1 and state["pending"] == 2
    source_detail_batches.pause(catalog, identifier)
    source_detail_batches.resume(catalog, identifier, retry_failed=True)
    assert (
        catalog.read_one("SELECT state FROM jobs WHERE job_id=?", (page[1]["job_id"],))[0]
        == "QUEUED"
    )
    assert (
        catalog.read_one("SELECT state FROM jobs WHERE job_id=?", (page[0]["job_id"],))[0]
        == "SUCCEEDED"
    )


def test_published_detail_artifact_is_reused_without_running_inference(
    api_env: ApiEnv, monkeypatch
) -> None:
    photo = seed_library(api_env, {"reuse.jpg": []})["reuse.jpg"]
    catalog = api_env.service.catalog
    face = FaceRecord(
        ordinal=0,
        bbox=(40.0, 30.0, 120.0, 110.0),
        landmarks=((52.0, 55.0), (104.0, 55.0), (78.0, 76.0), (59.0, 94.0), (99.0, 94.0)),
        det_score=0.95,
        face_pixels=80.0,
        blur_variance=120.0,
        quality_state="OK",
        quality_reason=None,
        embedding=(1.0,) + (0.0,) * 511,
    )
    payload = build_payload(
        input_revision=photo["sha256"],
        location_id=int(photo["location_id"]),
        document=FaceDocument(
            original_size=(320, 240),
            working_size=(320, 240),
            scale=1.0,
            faces=(face,),
            elapsed_ms=1,
        ),
        decode_format="JPEG",
        source_detail=True,
    )
    ref = ArtifactRef.from_envelope(artifact_ref(payload))
    store = ArtifactStore.open(api_env.settings.data_root)
    prepared = store.prepare(ref)
    store._publish_file(prepared)
    enqueue_targets(
        catalog,
        [{"asset_id": photo["asset_id"], "input_revision": photo["sha256"]}],
        now_ms=catalog.now_ms(),
        device="cpu",
        task_type=contract.FACE_DETAIL_TASK_TYPE,
    )
    job_id = int(catalog.read_one(
        "SELECT job_id FROM jobs WHERE subject_id=? AND task_type=?",
        (str(photo["asset_id"]), contract.FACE_DETAIL_TASK_TYPE),
    )[0])
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO artifacts(artifact_id,processor_id,input_revision,artifact_type,"
            "artifact_revision,media_type,checksum_sha256,byte_size,storage_uri,space_id,"
            "dimension,job_id,lifecycle_state,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'PUBLISHED',1,1)",
            (
                ref.artifact_id,
                contract.FACE_PROCESSOR_ID,
                photo["sha256"],
                ref.artifact_type,
                ref.artifact_revision,
                ref.media_type,
                ref.checksum_sha256,
                ref.byte_size,
                prepared.storage_uri,
                ref.space_id,
                ref.dimension,
                job_id,
            ),
        )
    monkeypatch.setattr(
        "photo_search.face.processor.extract_for_asset",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected inference")),
    )
    result = face_executor(
        catalog, data_root=api_env.settings.data_root, preserve_source_detail=True
    )(
        {
            "job_id": job_id,
            "lease_token": "synthetic",
            "input_revision": photo["sha256"],
            "task_type": contract.FACE_DETAIL_AUTO_TASK_TYPE,
            "processor_id": contract.FACE_PROCESSOR_ID,
            "subject_id": str(photo["asset_id"]),
            "resolved_device": "cpu",
            "runtime_provider": "CPUExecutionProvider",
            "precision": "FP32",
        }
    )
    assert result["state"] == "SUCCEEDED"
    assert result["artifacts"][0]["artifact_id"] == ref.artifact_id


def test_detail_publication_preserves_manual_anchor_and_holds_cross_profile_auto_link(
    api_env: ApiEnv,
) -> None:
    photo = seed_library(api_env, {"anchor.jpg": [(40, 30, 120, 110)]})["anchor.jpg"]
    catalog = api_env.service.catalog
    asset_id, old_face = int(photo["asset_id"]), photo["faces"][0]
    person_id = add_person(catalog, name="本人")
    add_link(catalog, old_face, person_id, assignment_kind="MANUAL")
    set_control(catalog, old_face, "MANUAL_ASSIGNED")
    vector = (1.0,) + (0.0,) * 511
    old_geometry = catalog.read_one(
        "SELECT bbox_json, landmarks_json FROM faces WHERE face_id = ?", (old_face,)
    )
    old_bbox = json.loads(old_geometry["bbox_json"])
    if isinstance(old_bbox, dict):
        old_bbox = old_bbox["bbox"]
    landmarks = tuple(tuple(point) for point in json.loads(old_geometry["landmarks_json"]))
    document = FaceDocument(
        original_size=(320, 240),
        working_size=(320, 240),
        scale=1.0,
        faces=(
            FaceRecord(
                ordinal=0,
                bbox=tuple(float(value) for value in old_bbox),
                landmarks=landmarks,
                det_score=0.95,
                face_pixels=80.0,
                blur_variance=120.0,
                quality_state="OK",
                quality_reason=None,
                embedding=vector,
            ),
        ),
        elapsed_ms=1,
    )
    payload = build_payload(
        input_revision=photo["sha256"],
        location_id=int(photo["location_id"]),
        document=document,
        decode_format="JPEG",
        source_detail=True,
    )
    reference = ArtifactRef.from_envelope(artifact_ref(payload))
    prepared = PreparedArtifact(
        reference,
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
        Path("unused"),
        "unused",
        None,
        False,
    )
    with catalog.write() as connection:
        assert persist_face_document(
            connection,
            job=SimpleNamespace(
                subject_id=str(asset_id), task_type=contract.FACE_DETAIL_AUTO_TASK_TYPE
            ),
            result=SimpleNamespace(input_revision=photo["sha256"]),
            prepared=[prepared],
            now_ms=10,
        )

    details = catalog.read_all(
        "SELECT face_id, spec_revision, artifact_id FROM faces WHERE asset_id = ? ORDER BY spec_revision",
        (asset_id,),
    )
    detail_row = next(
        row for row in details if row["spec_revision"] == contract.FACE_DETAIL_SPEC_REVISION
    )
    assert len(details) == 2
    assert detail_row["face_id"] != old_face
    assert detail_row["artifact_id"] == reference.artifact_id
    mapping = catalog.read_one(
        "SELECT new_spec_revision FROM face_redetection_mappings WHERE old_face_id = ? "
        "AND new_face_id = ?",
        (old_face, detail_row["face_id"]),
    )
    assert mapping["new_spec_revision"] == contract.FACE_DETAIL_SPEC_REVISION
    assert (
        catalog.read_one(
            "SELECT mode FROM face_assignment_controls WHERE face_id = ?", (old_face,)
        )["mode"]
        == "MANUAL_ASSIGNED"
    )
    assert (
        catalog.read_one("SELECT person_id FROM face_person_links WHERE face_id = ?", (old_face,))[
            "person_id"
        ]
        == person_id
    )
    generation = catalog.read_one(
        "SELECT state FROM face_index_generations WHERE spec_revision = ?",
        (contract.FACE_DETAIL_SPEC_REVISION,),
    )
    assert generation["state"] == "PENDING"
    assert (
        catalog.read_one(
            "SELECT 1 FROM face_index_outbox WHERE face_id = ?", (detail_row["face_id"],)
        )
        is not None
    )
    assert (
        catalog.read_one(
            "SELECT 1 FROM face_detail_association_queue WHERE asset_id = ?", (asset_id,)
        )
        is not None
    )
    assert (
        catalog.read_one("SELECT 1 FROM face_association_queue WHERE asset_id = ?", (asset_id,))
        is None
    )
    assert hold_face_ids(
        catalog.connection, asset_id=asset_id, spec_revision=contract.FACE_DETAIL_SPEC_REVISION
    ) == {detail_row["face_id"]}
    assert (
        hold_face_ids(
            catalog.connection, asset_id=asset_id, spec_revision=contract.FACE_SPEC_REVISION
        )
        == set()
    )
    projection = albums.asset_faces(catalog, asset_id)
    visible = next(face for face in projection["faces"] if face["face_id"] == detail_row["face_id"])
    assert visible["source_detail"] is True
