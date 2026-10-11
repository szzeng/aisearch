"""Selected synthetic vectors only: grouping, authority, rollback and replay."""

from dataclasses import replace

import pytest
from p3_redetection_fixtures import RedetectionHarness, make_record, shifted_bbox, shifted_landmarks

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import CatalogError
from photo_search.face.index import FaceIndexCoordinator
from photo_search.persons import face_correction, regroup
from photo_search.persons.service import PersonService


def vector(axis):
    return tuple(1.0 if i == axis else 0.0 for i in range(512))


def store(h, label, values, quality="OK"):
    asset = h.add_asset(label)
    records = [
        replace(
            make_record(
                ordinal=i,
                bbox=shifted_bbox(i * 150),
                landmarks=shifted_landmarks(i * 150),
                quality_state=quality,
            ),
            embedding=v,
        )
        for i, v in enumerate(values)
    ]
    h.write_face_document(asset, records)
    return [
        r["face_id"]
        for r in h.catalog.read_all(
            "SELECT face_id FROM faces WHERE asset_id=? ORDER BY ordinal", (asset,)
        )
    ]


@pytest.fixture()
def env(tmp_path):
    h = RedetectionHarness(tmp_path)
    try:
        yield h
    finally:
        h.close()


def setup(h, values=None, quality="OK"):
    source = h.add_person(name="张三")
    anchor = store(h, "anchor", [vector(0)])[0]
    h.add_link(anchor, source)
    faces = [
        store(h, f"wrong{i}", [v], quality)[0] for i, v in enumerate(values or [vector(1)] * 10)
    ]
    for face in faces:
        h.add_link(face, source)
    FaceIndexCoordinator(h.catalog).process_all(now_ms=10)
    return source, faces


def payload(h, source, faces, request_id="regroup-1"):
    return dict(
        source_person_id=source,
        source_expected_revision=1,
        request_id=request_id,
        contract_version=regroup.CONTRACT_VERSION,
        faces=[
            dict(
                face_id=f,
                expected_control_revision=h.control_revision(f),
                expected_assignment_revision=h.effective_link(f)["revision"],
            )
            for f in faces
        ],
    )


@pytest.mark.parametrize(
    "case,groups,remaining",
    [
        ("same", 1, 0),
        ("existing", 0, 0),
        ("different", 2, 0),
        ("low", 0, 10),
        ("unique", 0, 10),
        ("ambiguous", 0, 10),
        ("borderline", 0, 10),
    ],
)
def test_ten_faces_use_existing_safe_policy(env, monkeypatch, case, groups, remaining):
    h = env
    values = [vector(1)] * 5 + [vector(2)] * 5 if case == "different" else None
    if case == "unique":
        values = [vector(i + 1) for i in range(10)]
    if case == "borderline":
        v = list(vector(1))
        v[1] = 0.46
        v[4] = (1 - 0.46**2) ** 0.5
        values = [tuple(v)] * 10
    source, faces = setup(h, values, "LOW" if case == "low" else "OK")
    known = None
    if case in {"existing", "ambiguous", "borderline"}:
        known = h.add_person(name="李四")
        h.add_link(store(h, "known", [vector(1)])[0], known)
        if case == "ambiguous":
            other = h.add_person(name="王五")
            h.add_link(store(h, "tie", [vector(1)])[0], other)
        FaceIndexCoordinator(h.catalog).process_all(now_ms=11)
    vectors = [tuple(r) for r in h.catalog.read_all("SELECT * FROM face_embeddings")]
    from photo_search.face import weights

    monkeypatch.setattr(
        weights, "resolve_and_verify", lambda *a, **k: pytest.fail("no model loading")
    )
    result = h.service.regroup_faces(**payload(h, source, faces))
    assert (result["grouped_faces"], result["new_persons"], result["remaining_faces"]) == (
        10 - remaining,
        groups,
        remaining,
    )
    assert result["replayed"] is False
    assert [tuple(r) for r in h.catalog.read_all("SELECT * FROM face_embeddings")] == vectors
    assert all(source in h.active_exclusions(f) and h.control_revision(f) == 1 for f in faces)
    assert all(
        h.effective_link(f) is None or h.effective_link(f)["person_id"] != source for f in faces
    )
    if case == "existing":
        assert {h.effective_link(f)["person_id"] for f in faces} == {known}
    if case in {"same", "different"}:
        assert len({h.effective_link(f)["person_id"] for f in faces}) == groups
        assert all(h.effective_link(f)["revision"] > 1 for f in faces)
    # Stored decision counts stay historical, and are independently available for review.
    assert h.service.operation(result["operation_id"])["grouped_faces"] == 10 - remaining
    if remaining == 0 or case == "low":
        from photo_search.face.service import rematch_unassigned

        rematch_unassigned(h.catalog)
        assert all(
            h.effective_link(f) is None or h.effective_link(f)["person_id"] != source for f in faces
        )


def test_scope_and_existing_human_denials_are_preserved(env):
    h = env
    source, faces = setup(h)
    other = h.add_person(name="其他人工归属")
    asset = h.catalog.read_one("SELECT asset_id FROM faces WHERE face_id=?", (faces[0],))[0]
    unselected = h.add_face(asset, ordinal=1)
    h.add_link(unselected, other, assignment_kind="MANUAL")
    h.set_control(unselected, "MANUAL_ASSIGNED")
    denied = h.add_person(name="排除对象")
    h.set_exclusion(faces[0], denied)
    h.set_control(faces[0], "MANUAL_ASSIGNED")
    with h.catalog.write() as c:
        c.execute(
            "UPDATE face_person_links SET assignment_kind='MANUAL' WHERE face_id=?", (faces[0],)
        )
    before = face_correction.read_control_bundle(h.catalog.connection, unselected)
    result = h.service.regroup_faces(**payload(h, source, faces))
    assert result["grouped_faces"] == 10
    assert {source, denied} <= set(h.active_exclusions(faces[0]))
    assert face_correction.read_control_bundle(h.catalog.connection, unselected) == before


def test_interruption_rolls_back_then_restart_replays_without_duplicates(env, monkeypatch):
    h = env
    source, faces = setup(h)
    body = payload(h, source, faces)
    before = list(h.catalog.connection.iterdump())
    original = __import__("photo_search.face.index", fromlist=["_confirm"])._confirm
    from photo_search.face import index

    calls = 0

    def fail_midway(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("interrupted")
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(index, "_confirm", fail_midway)
        with pytest.raises(RuntimeError, match="interrupted"):
            h.service.regroup_faces(**body)
    assert list(h.catalog.connection.iterdump()) == before
    completed = h.service.regroup_faces(**body)
    count = h.catalog.read_one("SELECT COUNT(*) FROM persons")[0]
    h.catalog.close()
    h.catalog = Catalog.open(data_root=h.data_root, clock=h.clock)
    h.service = PersonService(h.catalog, clock=h.clock)
    replay = h.service.regroup_faces(**body)
    assert replay["replayed"] and replay["operation_id"] == completed["operation_id"]
    assert (
        replay["new_persons"] == 1
        and h.catalog.read_one("SELECT COUNT(*) FROM persons")[0] == count
    )
    with pytest.raises(CatalogError) as error:
        h.service.regroup_faces(**{**body, "request_id": "fresh-request"})
    assert error.value.code == "FACE_ASSIGNMENT_CONFLICT"
    assert h.catalog.read_one("SELECT COUNT(*) FROM persons")[0] == count
    with pytest.raises(CatalogError) as error:
        h.service.regroup_faces(**{**body, "faces": body["faces"][:-1]})
    assert error.value.code == "FACE_IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize(
    "conflict", ["control", "assignment", "source", "hold", "duplicate", "index"]
)
def test_conflicts_write_no_partial_authority(env, conflict):
    h = env
    source, faces = setup(h)
    body = payload(h, source, faces)
    with h.catalog.write() as c:
        if conflict == "control":
            hface = faces[-1]
            c.execute(
                "INSERT INTO face_assignment_controls VALUES (?,'MANUAL_UNASSIGNED',1,'later',1)",
                (hface,),
            )
            c.execute("DELETE FROM face_person_links WHERE face_id=?", (hface,))
        elif conflict == "assignment":
            c.execute(
                "UPDATE face_person_links SET revision=revision+1 WHERE face_id=?", (faces[-1],)
            )
        elif conflict == "source":
            c.execute("UPDATE persons SET revision=2 WHERE person_id=?", (source,))
        elif conflict == "hold":
            c.execute(
                "INSERT INTO face_redetection_groups(old_face_id,asset_id,old_spec_revision,state,candidate_state,revision,created_at,updated_at) VALUES (?,?,'old','OPEN','NO_CANDIDATE',1,1,1)",
                (
                    faces[-1],
                    h.catalog.read_one("SELECT asset_id FROM faces WHERE face_id=?", (faces[-1],))[
                        0
                    ],
                ),
            )
        elif conflict == "index":
            c.execute("UPDATE face_index_generations SET state='PENDING',is_active=0")
    if conflict == "duplicate":
        body["faces"].append(body["faces"][0])
    before = list(h.catalog.connection.iterdump())
    with pytest.raises(CatalogError):
        regroup.apply(h.catalog, **body)
    assert list(h.catalog.connection.iterdump()) == before


def test_undo_restores_faces_and_refuses_later_manual_changes(env):
    h = env
    source, faces = setup(h)
    body = payload(h, source, faces)
    result = h.service.regroup_faces(**body)
    h.undo(result["operation_id"], "undo-regroup", result["operation_revision"])
    assert all(
        h.effective_link(f)["person_id"] == source and source not in h.active_exclusions(f)
        for f in faces
    )
    assert all(h.control_revision(f) == 2 for f in faces)
    assert h.service.regroup_faces(**body)["status"] == "UNDONE"
    result = h.service.regroup_faces(**payload(h, source, faces, "regroup-2"))
    target = h.add_person(name="人工决定")
    h.edit(
        faces[-1],
        "assign",
        expected_revision=h.control_revision(faces[-1]),
        target_person_id=target,
    )
    before = list(h.catalog.connection.iterdump())
    with pytest.raises(CatalogError) as error:
        h.undo(result["operation_id"], "stale-undo", result["operation_revision"])
    assert error.value.code == "FACE_OPERATION_NOT_UNDOABLE"
    assert list(h.catalog.connection.iterdump()) == before


def test_regroup_api_uses_session_csrf_and_private_response(api_env, env, monkeypatch):
    from photo_search.api import routes

    h = env
    source, faces = setup(h)
    monkeypatch.setattr(routes, "persons_of", lambda request: h.service)
    body = payload(h, source, faces)
    del body["source_person_id"]
    path = f"/api/v1/persons/{source}/regroup"

    async def scenario(client):
        anonymous = await client.post(path, json=body)
        assert anonymous.status_code == 401
        csrf = await api_env.authenticate(client)
        assert (await client.post(path, json=body)).status_code == 403
        headers = api_env.post_headers(csrf)
        answer = await client.post(path, json=body, headers=headers)
        assert answer.status_code == 200, answer.text
        assert answer.json()["grouped_faces"] == 10
        assert answer.json()["new_persons"] == 1
        assert answer.json()["remaining_faces"] == 0
        replay = await client.post(path, json=body, headers=headers)
        assert replay.json()["replayed"]
        assert replay.json()["operation_id"] == answer.json()["operation_id"]
        for key in ("vector", "bbox", "landmark", "confidence", "similarity", "payload_sha256"):
            assert key not in answer.text

    api_env.scenario(scenario)


def test_new_mapping_candidate_hold_and_later_hold_on_undo(env):
    h = env
    source = h.add_person(name="映射源人物")
    asset = h.add_asset("held")
    record = make_record(ordinal=0, bbox=shifted_bbox(0), landmarks=shifted_landmarks(0))
    h.write_face_document(asset, [record], spec_revision="face-buffalo-l-scrfd-arcface-v1")
    old = h.catalog.read_one("SELECT face_id FROM faces WHERE asset_id=?", (asset,))[0]
    h.add_link(old, source, assignment_kind="MANUAL")
    h.set_control(old, "MANUAL_ASSIGNED")
    h.write_face_document(asset, [record])
    new = h.mappings(old)[0]["new_face_id"]
    h.add_link(new, source)
    FaceIndexCoordinator(h.catalog).process_all(now_ms=10)
    before = list(h.catalog.connection.iterdump())
    with pytest.raises(CatalogError) as error:
        regroup.apply(h.catalog, **payload(h, source, [new]))
    assert error.value.details["reason"] == "provisional_hold"
    assert list(h.catalog.connection.iterdump()) == before
    # A later OPEN mapping cannot be bypassed by compensating an earlier regroup.
    source, faces = setup(h)
    result = h.service.regroup_faces(**payload(h, source, faces))
    with h.catalog.write() as c:
        asset = c.execute("SELECT asset_id FROM faces WHERE face_id=?", (faces[0],)).fetchone()[0]
        c.execute(
            "INSERT INTO face_redetection_groups(old_face_id,asset_id,old_spec_revision,state,candidate_state,revision,created_at,updated_at) VALUES (?,?,'old','OPEN','NO_CANDIDATE',1,1,1)",
            (faces[0], asset),
        )
    before = list(h.catalog.connection.iterdump())
    with pytest.raises(CatalogError) as error:
        h.undo(result["operation_id"], "held-undo", result["operation_revision"])
    assert error.value.details["reason"] == "provisional_hold"
    assert list(h.catalog.connection.iterdump()) == before


def test_existing_same_photo_person_is_only_a_suggestion(env):
    h = env
    source, faces = setup(h)
    known = h.add_person(name="李四")
    h.add_link(store(h, "known", [vector(1)])[0], known)
    asset = h.catalog.read_one("SELECT asset_id FROM faces WHERE face_id=?", (faces[0],))[0]
    other = h.add_face(asset, ordinal=1)
    h.add_link(other, known, assignment_kind="MANUAL")
    h.set_control(other, "MANUAL_ASSIGNED")
    before = face_correction.read_control_bundle(h.catalog.connection, other)
    result = h.service.regroup_faces(**payload(h, source, faces))
    assert (result["grouped_faces"], result["remaining_faces"], result["new_persons"]) == (9, 1, 0)
    assert h.effective_link(faces[0]) is None
    assert face_correction.read_control_bundle(h.catalog.connection, other) == before


def test_undo_keeps_a_generated_persons_later_manual_member(env):
    h = env
    source, faces = setup(h)
    result = h.service.regroup_faces(**payload(h, source, faces))
    generated = h.effective_link(faces[0])["person_id"]
    newcomer = store(h, "later manual face", [vector(1)])[0]
    h.edit(newcomer, "assign", expected_revision=0, target_person_id=generated)
    manual = face_correction.read_control_bundle(h.catalog.connection, newcomer)
    h.undo(result["operation_id"], "undo-keep-later-member", result["operation_revision"])
    assert face_correction.read_control_bundle(h.catalog.connection, newcomer) == manual
    assert all(h.effective_link(f)["person_id"] == source for f in faces)
    # Retention must not prune the outcome metadata needed for historical replay.
    from photo_search.persons import operations, retention

    row = operations.operation_row(h.catalog.connection, result["operation_id"])
    assert (
        retention._unsupported_reason(h.catalog.connection, row)
        == "operation_family_not_enabled_for_cleanup"
    )


def test_compatible_detail_representative_outbox_is_resumed(env):
    from photo_search.face import contract

    h = env
    source, faces = setup(h)
    known = h.add_person(name="已有详情人物")
    asset = h.add_asset("detail representative")
    record = replace(
        make_record(ordinal=0, bbox=shifted_bbox(0), landmarks=shifted_landmarks(0)),
        embedding=vector(1),
    )
    h.write_face_document(asset, [record], spec_revision=contract.FACE_DETAIL_SPEC_REVISION)
    face = h.catalog.read_one("SELECT face_id FROM faces WHERE asset_id=?", (asset,))[0]
    h.add_link(face, known)
    # The generic document fixture writes a manual detail result. Model an already
    # authorized detail publication's pending outbox, without running a detector.
    from photo_search.face.processor import queue_candidate

    with h.catalog.write() as connection:
        artifact = connection.execute(
            "SELECT artifact_id FROM faces WHERE face_id=?", (face,)
        ).fetchone()[0]
        queue_candidate(
            connection,
            face_id=face,
            generation_id=FaceIndexCoordinator(
                h.catalog, spec_revision=contract.FACE_DETAIL_SPEC_REVISION
            ).generation_id,
            artifact_id=artifact,
            now_ms=12,
        )
    result = h.service.regroup_faces(**payload(h, source, faces))
    assert (result["grouped_faces"], result["new_persons"]) == (10, 0)
    assert {h.effective_link(f)["person_id"] for f in faces} == {known}
