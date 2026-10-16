"""Stored-vector matching, historical publication flags and real protected API batches."""

import json
import math
from dataclasses import replace
from types import SimpleNamespace

import pytest
from p3_persons_page_fixtures import add_link, add_person, seed_library, set_control
from p3_redetection_fixtures import RedetectionHarness, make_record, shifted_bbox, shifted_landmarks

from photo_search.artifacts.store import ArtifactStore
from photo_search.face import contract
from photo_search.face.engine import DetectedFace, FaceDocument
from photo_search.face.index import FaceIndexCoordinator, _InjectedCrash, reconcile_asset
from photo_search.face.processor import (
    artifact_id_for,
    build_payload,
    face_executor,
    persist_face_document,
)
from photo_search.persons.albums import inbox_summary
from photo_search.persons.operations import _repair_candidates
from photo_search.scheduler.contracts import ArtifactRef


def record(score=1, *, ordinal=0, pixels=120):
    return replace(
        make_record(
            ordinal=ordinal,
            bbox=shifted_bbox(ordinal * 150),
            landmarks=shifted_landmarks(ordinal * 150),
            seed=math.sqrt(1 / score**2 - 1),
        ),
        face_pixels=pixels,
    )


def seed_document(catalog, asset_id, records):
    revision = catalog.read_one("SELECT sha256 FROM assets WHERE asset_id = ?", (asset_id,))[
        "sha256"
    ]
    payload = build_payload(
        input_revision=revision,
        location_id=None,
        decode_format="JPEG",
        document=FaceDocument((640, 480), (640, 480), 1.0, tuple(records), 1),
    )
    with catalog.write() as connection:
        persist_face_document(
            connection,
            job=SimpleNamespace(subject_id=asset_id),
            result=SimpleNamespace(input_revision=revision),
            prepared=[
                SimpleNamespace(
                    ref=SimpleNamespace(
                        artifact_type="structured", artifact_id=artifact_id_for(revision)
                    ),
                    payload=json.dumps(payload).encode(),
                )
            ],
            now_ms=1,
        )
    return [
        row["face_id"]
        for row in catalog.read_all(
            "SELECT face_id FROM faces WHERE asset_id = ? ORDER BY ordinal",
            (asset_id,),
        )
    ]


def test_group_photo_assigns_both_people_and_never_suggests_an_excluded_local_person(tmp_path):
    harness = RedetectionHarness(tmp_path)
    try:
        index = FaceIndexCoordinator(harness.catalog)
        people = []
        for score in (0.53, 0.90):
            asset = harness.add_asset(f"known-{score}.jpg")
            harness.write_face_document(asset, [record(score)])
            face = harness.catalog.read_one(
                "SELECT face_id FROM faces WHERE asset_id = ?", (asset,)
            )["face_id"]
            person = harness.add_person()
            harness.add_link(face, person)
            people.append(person)
        target = harness.add_asset("group.jpg")
        harness.write_face_document(target, [record(0.53), record(1, ordinal=1)])
        index.process_all(now_ms=2)
        decisions = reconcile_asset(harness.catalog, asset_id=target, coordinator=index)
        assert [(d["action"], d["person_id"]) for d in decisions] == [
            ("MERGE", people[0]),
            ("MERGE", people[1]),
        ]
        denied_asset = harness.add_asset("excluded-group.jpg")
        harness.write_face_document(denied_asset, [record(0.53), record(0.53, ordinal=1)])
        faces = harness.catalog.read_all(
            "SELECT face_id FROM faces WHERE asset_id = ? ORDER BY ordinal", (denied_asset,)
        )
        denied = faces[1]["face_id"]
        harness.set_control(denied, "AUTO_ALLOWED")
        harness.set_exclusion(denied, people[0], active=True)
        index.process_all(now_ms=3)
        reconcile_asset(harness.catalog, asset_id=denied_asset, coordinator=index)
        assert all(link["person_id"] != people[0] for link in harness.face_links(denied))
        assert harness.active_exclusions(denied) == [people[0]]
    finally:
        harness.close()


def test_live_diverse_representatives_use_old_false_flags_but_refuse_unconfirmed_and_removed(
    tmp_path,
):
    harness = RedetectionHarness(tmp_path)
    try:
        person = harness.add_person()
        faces = []
        for i in range(10):
            asset = harness.add_asset(f"portrait-{i}.jpg")
            harness.write_face_document(asset, [record(1 if i < 9 else 0.65, pixels=200 - i)])
            face = harness.catalog.read_one(
                "SELECT face_id FROM faces WHERE asset_id = ?", (asset,)
            )["face_id"]
            harness.add_link(face, person)
            faces.append(face)
        index = FaceIndexCoordinator(harness.catalog)
        index.process_all(now_ms=2)
        assert (
            harness.catalog.read_one(
                "SELECT is_representative FROM face_candidates WHERE face_id = ?", (faces[-1],)
            )["is_representative"]
            == 0
        )
        assert {c.face_id for c in index.candidates()} == {faces[0], faces[-1]}
        assert index.index_state()["representatives"] == 2
        with harness.catalog.write() as connection:
            _repair_candidates(connection, [faces[-1]], now_ms=3)
        assert faces[-1] not in {c.face_id for c in index.candidates()}
        with pytest.raises(_InjectedCrash):
            index.process_next(now_ms=4, failpoint="after_publish")
        assert faces[-1] not in {c.face_id for c in index.candidates()}
        index.process_all(now_ms=5)
        assert faces[-1] in {c.face_id for c in index.candidates()}
        harness.set_control(faces[-1], "MANUAL_UNASSIGNED")
        assert faces[-1] not in {c.face_id for c in index.candidates()}
    finally:
        harness.close()


def test_explicitly_excluded_person_is_not_an_automatic_face_candidate(tmp_path):
    harness = RedetectionHarness(tmp_path)
    try:
        person = harness.add_person()
        asset = harness.add_asset("excluded-person.jpg")
        harness.write_face_document(asset, [record(1)])
        face = harness.catalog.read_one(
            "SELECT face_id FROM faces WHERE asset_id = ?", (asset,)
        )["face_id"]
        harness.add_link(face, person)
        index = FaceIndexCoordinator(harness.catalog)
        index.process_all(now_ms=2)
        assert {candidate.person_id for candidate in index.candidates()} == {person}

        with harness.catalog.write() as connection:
            connection.execute(
                "INSERT INTO person_hidden(person_id, ignore_state) VALUES (?, 'EXCLUDED')",
                (person,),
            )
        assert index.candidates() == []

        with harness.catalog.write() as connection:
            connection.execute(
                "UPDATE person_hidden SET ignore_state='TEMPORARY' WHERE person_id=?",
                (person,),
            )
        assert {candidate.person_id for candidate in index.candidates()} == {person}
    finally:
        harness.close()


def test_collection_person_match_extends_across_synthetic_sources_but_static_face_does_not(
    tmp_path,
) -> None:
    from photo_search.persons import collections

    def run_case(root, *, ignore_state=None, dynamic_member, static_face):
        harness = RedetectionHarness(root)
        try:
            source_a = harness.add_asset("source-A-anchor.jpg")
            harness.write_face_document(source_a, [record(1)])
            anchor = harness.catalog.read_one(
                "SELECT face_id FROM faces WHERE asset_id = ?", (source_a,)
            )["face_id"]
            person = harness.add_person()
            add_link(harness.catalog, anchor, person, assignment_kind="MANUAL")
            collection = collections.create_collection(harness.catalog, "来源延续") ["collection"]
            collection_id, token = int(collection["collection_id"]), str(collection["instance_token"])
            if dynamic_member:
                collections.edit_members(
                    harness.catalog, collection_id, action="add", person_ids=[person], instance_token=token
                )
            if static_face:
                collections.edit_face_members(
                    harness.catalog, collection_id, action="add", face_ids=[anchor],
                    instance_token=token, expected_revision=int(collection["revision"]),
                )
                collection = collections.collection_detail(
                    harness.catalog, collection_id, instance_token=token
                )["collection"]
            if ignore_state:
                with harness.catalog.write() as connection:
                    connection.execute(
                        "INSERT INTO person_hidden(person_id, ignore_state) VALUES (?, ?)",
                        (person, ignore_state),
                    )
            index = FaceIndexCoordinator(harness.catalog)
            index.process_all(now_ms=2)
            source_b = harness.add_asset("source-B-synthetic.jpg")
            harness.write_face_document(source_b, [record(1)])
            index.process_all(now_ms=3)
            target = harness.catalog.read_one(
                "SELECT face_id FROM faces WHERE asset_id = ?", (source_b,)
            )["face_id"]
            decisions = reconcile_asset(harness.catalog, asset_id=source_b, coordinator=index)
            assigned = harness.effective_link(target)
            summary = next(
                row for row in collections.list_collections(harness.catalog)["collections"]
                if row["collection_id"] == collection_id
            )
            return assigned, decisions, summary["photo_count"], person, harness
        except Exception:
            harness.close()
            raise

    assigned, decisions, photo_count, person, harness = run_case(
        tmp_path / "normal", dynamic_member=True, static_face=False
    )
    try:
        assert assigned is not None and int(assigned["person_id"]) == person
        assert photo_count == 2
    finally:
        harness.close()

    assigned, decisions, photo_count, person, harness = run_case(
        tmp_path / "temporary", ignore_state="TEMPORARY", dynamic_member=True, static_face=False
    )
    try:
        assert assigned is not None and int(assigned["person_id"]) == person
        assert photo_count == 2
    finally:
        harness.close()

    assigned, decisions, photo_count, person, harness = run_case(
        tmp_path / "excluded", ignore_state="EXCLUDED", dynamic_member=True, static_face=False
    )
    try:
        assert assigned is not None and int(assigned["person_id"]) != person
        assert photo_count == 1
    finally:
        harness.close()

    assigned, decisions, photo_count, person, harness = run_case(
        tmp_path / "static", dynamic_member=False, static_face=True
    )
    try:
        assert assigned is not None and int(assigned["person_id"]) == person
        assert photo_count == 1
    finally:
        harness.close()


def test_source_crop_worker_publishes_indexes_and_reconciles_into_dynamic_collection(
    api_env, monkeypatch
):
    from photo_search.face.service import enqueue_targets
    from photo_search.persons import collections

    photos = seed_library(
        api_env,
        {"source-a-confirmed.jpg": [(20, 20, 140, 140)], "source-b-new.jpg": [(30, 25, 150, 145)]},
    )
    catalog = api_env.service.catalog
    source_a = int(photos["source-a-confirmed.jpg"]["asset_id"])
    source_b = int(photos["source-b-new.jpg"]["asset_id"])
    anchor = seed_document(catalog, source_a, [record(1)])[0]
    person = add_person(catalog, name="source detail person")
    add_link(catalog, anchor, person, assignment_kind="MANUAL")
    set_control(catalog, anchor, "MANUAL_ASSIGNED")

    dynamic = collections.create_collection(catalog, "source detail dynamic")["collection"]
    collections.edit_members(
        catalog, int(dynamic["collection_id"]), action="add", person_ids=[person],
        instance_token=str(dynamic["instance_token"]),
    )
    static_only = collections.create_collection(catalog, "source detail static only")["collection"]
    collections.edit_face_members(
        catalog, int(static_only["collection_id"]), action="add", face_ids=[anchor],
        instance_token=str(static_only["instance_token"]),
        expected_revision=int(static_only["revision"]),
    )

    seed_document(catalog, source_b, [record(1)])
    revision = str(catalog.read_one(
        "SELECT sha256 FROM assets WHERE asset_id=?", (source_b,)
    )["sha256"])
    coarse_index = FaceIndexCoordinator(catalog)
    coarse_index.process_all(now_ms=2)
    queued = enqueue_targets(
        catalog,
        [{"asset_id": source_b, "input_revision": revision}],
        now_ms=3,
        device="cpu",
        task_type=contract.FACE_DETAIL_AUTO_TASK_TYPE,
    )
    assert queued["queued"] == 1

    from photo_search.face import engine as face_engine
    from photo_search.face import processor as face_processor

    class SyntheticEngine:
        def detect(self, _blob):
            return []

        def recognize(self, _aligned):
            return (1.0,) + (0.0,) * 511

    class SyntheticCache:
        def engine(self, *_args, **_kwargs):
            return SyntheticEngine()

    source_face = DetectedFace(
        ordinal=0,
        bbox=(24.0, 18.0, 142.0, 136.0),
        landmarks=((53.0, 62.0), (111.0, 62.0), (82.0, 89.0), (58.0, 111.0), (106.0, 111.0)),
        det_score=0.99,
    )
    decode_calls = []
    real_decode = face_processor._decode

    def observe_decode(path, *, preserve_source_detail=False):
        decode_calls.append(bool(preserve_source_detail))
        return real_decode(path, preserve_source_detail=preserve_source_detail)

    crop_calls = []
    real_source_crop = face_engine.warp_aligned_source_crop

    def observe_source_crop(image, *, bbox, landmarks):
        crop_calls.append(tuple(image.size))
        return real_source_crop(image, bbox=bbox, landmarks=landmarks)

    monkeypatch.setattr(face_processor, "_decode", observe_decode)
    monkeypatch.setattr(face_engine, "warp_aligned_source_crop", observe_source_crop)
    monkeypatch.setattr(
        face_engine,
        "decode_detections",
        lambda *_args, **_kwargs: [source_face],
    )
    result = face_executor(
        catalog,
        data_root=api_env.settings.data_root,
        engine_cache=SyntheticCache(),
        preserve_source_detail=True,
    )(
        {
            "job_id": 1,
            "lease_token": "synthetic-source-crop",
            "input_revision": revision,
            "task_type": contract.FACE_DETAIL_AUTO_TASK_TYPE,
            "processor_id": contract.FACE_PROCESSOR_ID,
            "subject_id": str(source_b),
            "resolved_device": "cpu",
            "runtime_provider": "CPUExecutionProvider",
            "precision": "FP32",
        }
    )
    assert result["state"] == "SUCCEEDED", result
    assert decode_calls == [True]
    assert crop_calls == [(320, 240)]
    reference = ArtifactRef.from_envelope(result["artifacts"][0])
    store = ArtifactStore.open(api_env.settings.data_root)
    prepared = store.prepare(reference)
    store._publish_file(prepared)
    with catalog.write() as connection:
        assert persist_face_document(
            connection,
            job=SimpleNamespace(
                subject_id=str(source_b), task_type=contract.FACE_DETAIL_AUTO_TASK_TYPE
            ),
            result=SimpleNamespace(input_revision=revision),
            prepared=[prepared],
            now_ms=4,
        )

    detail_face = catalog.read_one(
        "SELECT face_id FROM faces WHERE asset_id=? AND spec_revision=?",
        (source_b, contract.FACE_DETAIL_SPEC_REVISION),
    )["face_id"]
    detail_index = FaceIndexCoordinator(
        catalog, spec_revision=contract.FACE_DETAIL_SPEC_REVISION
    )
    assert detail_index.process_all(now_ms=5) >= 1
    decisions = reconcile_asset(
        catalog,
        asset_id=source_b,
        coordinator=detail_index,
        spec_revision=contract.FACE_DETAIL_SPEC_REVISION,
    )
    assert decisions
    assigned = catalog.read_one(
        "SELECT person_id FROM face_person_links WHERE face_id=? AND decision='CONFIRMED'",
        (detail_face,),
    )
    assert assigned is not None and int(assigned["person_id"]) == person
    summaries = {
        int(item["collection_id"]): item
        for item in collections.list_collections(catalog)["collections"]
    }
    dynamic_summary = summaries[int(dynamic["collection_id"])]
    static_summary = summaries[int(static_only["collection_id"])]
    assert int(dynamic_summary["photo_count"]) == 2
    assert int(static_summary["photo_count"]) == 1
    assert prepared.target.exists()
    assert catalog.read_one(
        "SELECT state FROM face_index_generations WHERE generation_id=?",
        (detail_index.generation_id,),
    )["state"] == "READY"


def test_inbox_reason_summary_filters_and_cursors_are_read_only_and_private(api_env):
    photos = seed_library(api_env, {f"{i}.jpg": [(10, 10, 110, 110)] for i in range(5)})
    catalog = api_env.service.catalog
    person = add_person(catalog, name="家人")
    faces = [photos[f"{i}.jpg"]["faces"][0] for i in range(5)]
    for face, reason in zip(faces[:2], ("BORDERLINE", "SAME_IMAGE_CONFLICT"), strict=True):
        add_link(catalog, face, person, decision="SUGGESTED", reason=reason)
    with catalog.write() as connection:
        connection.execute(
            "UPDATE faces SET quality_state = 'LOW', quality_reason = 'BLURRY' WHERE face_id = ?",
            (faces[2],),
        )
    set_control(catalog, faces[3], "MANUAL_UNASSIGNED")

    async def scenario(client):
        assert (await client.get("/api/v1/faces/inbox/summary")).status_code == 401
        await api_env.authenticate(client)
        before = tuple(catalog.connection.iterdump())
        summary = (await client.get("/api/v1/faces/inbox/summary")).json()
        assert summary == {
            "total": 5,
            "all_total": 5, "quality_hidden": 1, "quality_view": "all",
            "held": 0,
            "reasons": {
                "BORDERLINE": 1,
                "SAME_IMAGE_CONFLICT": 1,
                "BLURRY": 1,
                "MANUAL_UNASSIGNED": 1,
                "WAITING_INDEX": 1,
            },
        }
        page = (
            await client.get("/api/v1/faces/inbox", params={"kind": "suggested", "limit": 1})
        ).json()
        assert page["has_more"] and page["faces"][0]["organization_reason"] in (
            "BORDERLINE",
            "SAME_IMAGE_CONFLICT",
        )
        next_page = (
            await client.get(
                "/api/v1/faces/inbox", params={"kind": "suggested", "cursor": page["next_cursor"]}
            )
        ).json()
        assert {page["faces"][0]["face_id"], next_page["faces"][0]["face_id"]} == set(faces[:2])
        changed = await client.get(
            "/api/v1/faces/inbox", params={"kind": "quality", "cursor": page["next_cursor"]}
        )
        assert changed.status_code == 409
        for kind, expected in (("quality", faces[2]), ("manual", faces[3]), ("pending", faces[4])):
            response = await client.get("/api/v1/faces/inbox", params={"kind": kind})
            assert [f["face_id"] for f in response.json()["faces"]] == [expected]
        assert (
            await client.get("/api/v1/faces/inbox", params={"kind": "invalid"})
        ).status_code == 422
        assert tuple(catalog.connection.iterdump()) == before
        assert not any(
            field in repr(page) for field in ("confidence", "similarity", "vector", "bbox", "path")
        )

    api_env.scenario(scenario)


def test_summary_separates_open_redetection_hold_from_machine_quality(tmp_path):
    harness = RedetectionHarness(tmp_path)
    try:
        asset = harness.add_asset("held.jpg")
        person = harness.add_person()
        harness.seed_anchor(asset, person_id=person)
        harness.write_face_document(asset, [record()])
        low_asset = harness.add_asset("small.jpg")
        low = replace(record(), quality_state="LOW", quality_reason="SMALL")
        harness.write_face_document(low_asset, [low])
        before = tuple(harness.catalog.connection.iterdump())
        assert inbox_summary(harness.catalog) == {
            "total": 1, "reasons": {"SMALL": 1}, "held": 1,
            "all_total": 1, "quality_hidden": 1, "quality_view": "all",
        }
        assert tuple(harness.catalog.connection.iterdump()) == before
    finally:
        harness.close()


def test_summary_waits_for_ready_generation_after_one_face_is_confirmed(tmp_path):
    harness = RedetectionHarness(tmp_path)
    try:
        for label in ("first.jpg", "second.jpg"):
            asset = harness.add_asset(label)
            harness.write_face_document(asset, [record()])
        index = FaceIndexCoordinator(harness.catalog)
        index.process_next(now_ms=2)
        assert index.generation()["state"] == "PENDING"
        assert inbox_summary(harness.catalog) == {
            "total": 2, "reasons": {"WAITING_INDEX": 2}, "held": 0,
            "all_total": 2, "quality_hidden": 0, "quality_view": "all",
        }
        index.process_all(now_ms=3)
        assert inbox_summary(harness.catalog) == {
            "total": 2, "reasons": {"NO_MATCH": 2}, "held": 0,
            "all_total": 2, "quality_hidden": 0, "quality_view": "all",
        }
    finally:
        harness.close()


def test_api_rematch_real_batches_need_csrf_keep_manual_knowledge_and_advance(api_env, monkeypatch):
    from photo_search.face import weights

    def forbidden(*args, **kwargs):
        raise AssertionError("stored-vector API must not load or verify models")

    monkeypatch.setattr(weights, "resolve_and_verify", forbidden)
    photos = seed_library(api_env, {f"{i}.jpg": [] for i in range(5)})
    catalog = api_env.service.catalog
    faces = [
        seed_document(catalog, photos[f"{i}.jpg"]["asset_id"], [record()])[0] for i in range(5)
    ]
    known = add_person(catalog, name="家人")
    add_link(catalog, faces[0], known)
    set_control(catalog, faces[4], "MANUAL_UNASSIGNED")
    index = FaceIndexCoordinator(catalog)
    index.process_all(now_ms=2)

    async def scenario(client):
        url = "/api/v1/faces/rematch"
        assert (await client.post(url, json={"limit": 1})).status_code == 401
        csrf = await api_env.authenticate(client)
        before = tuple(catalog.connection.iterdump())
        assert (await client.post(url, json={"limit": 1})).status_code == 403
        headers = api_env.post_headers(csrf)
        for body in (
            {"limit": 0},
            {"limit": 51},
            {"limit": True},
            {"after_asset_id": -1},
            {"extra": 1},
        ):
            assert (await client.post(url, headers=headers, json=body)).status_code == 422
        assert tuple(catalog.connection.iterdump()) == before
        first = (await client.post(url, headers=headers, json={"limit": 1})).json()
        assert first["selected_assets"] == first["processed"] == first["merged"] == 1
        assert first["has_more"] and first["next_after_asset_id"]
        assert (
            catalog.read_one(
                "SELECT COUNT(*) AS n FROM face_person_links WHERE decision = 'CONFIRMED'"
            )["n"]
            == 2
        )
        second = (
            await client.post(
                url,
                headers=headers,
                json={"limit": 1, "after_asset_id": first["next_after_asset_id"]},
            )
        ).json()
        assert second["has_more"] and second["next_after_asset_id"] > first["next_after_asset_id"]
        last = (
            await client.post(
                url,
                headers=headers,
                json={"limit": 1, "after_asset_id": second["next_after_asset_id"]},
            )
        ).json()
        assert last["merged"] == 1 and not last["has_more"] and last["next_after_asset_id"] is None
        assert (
            catalog.read_one(
                "SELECT COUNT(*) AS n FROM face_person_links WHERE decision = 'CONFIRMED'"
            )["n"]
            == 4
        )
        assert (
            catalog.read_one(
                "SELECT mode FROM face_assignment_controls WHERE face_id = ?", (faces[4],)
            )["mode"]
            == "MANUAL_UNASSIGNED"
        )
        assert (
            catalog.read_one("SELECT 1 FROM face_person_links WHERE face_id = ?", (faces[4],))
            is None
        )
        again = (await client.post(url, headers=headers, json={})).json()
        assert again["processed"] == 0
        assert not any(
            field in repr(first)
            for field in ("similarity", "vector", "face_id", "path", "decisions")
        )

    api_env.scenario(scenario)
