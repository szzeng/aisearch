"""P3-04B: the frozen ``/api/v1`` face-correction surface behind the local session boundary.

Only ``%TEMP%`` fixtures are used. The face semantics are covered by the lifecycle/undo
suites; this file is about the transport contract: session/Host/Origin/CSRF, the frozen
error codes and their HTTP statuses, the response shapes of the shared undo endpoint, and
the fact that no path, box, landmark, vector or per-pair similarity ever leaves the process.
"""

from __future__ import annotations

import hashlib
from typing import Any

from httpx import AsyncClient

from photo_search.face.contract import FACE_SPEC_REVISION

CONTRACT_VERSION = "p3-face-correction-contracts.v1"

#: Anything that would identify a local file, a face geometry or a per-pair similarity. A
#: response that contains one of these strings has leaked something it must not.
FORBIDDEN_FRAGMENTS = (
    "C:\\\\",
    "/Users/",
    "bbox",
    "landmark",
    "vector",
    "latitude",
    "longitude",
    "confidence",
    "similarity",
    "payload_sha256",
)


def seed_face_photo(env: Any) -> dict[str, Any]:
    """One asset with two faces, two named persons and one automatic effective link."""
    catalog = env.service.catalog
    if catalog.read_one("SELECT 1 FROM source_roots LIMIT 1") is None:
        with catalog.write() as connection:
            connection.execute(
                "INSERT INTO source_roots(display_name, root_path, volume_hint, status) "
                "VALUES ('library', ?, 'C:', 'ONLINE')",
                (str(env.library),),
            )
    with catalog.write() as connection:
        digest = hashlib.sha256(b"face-api-asset").hexdigest()
        connection.execute(
            "INSERT INTO assets(sha256, byte_size, created_at, state) "
            "VALUES (?, 4096, 1, 'ACTIVE')",
            (digest,),
        )
        asset_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, size, "
            "mtime_ns, last_seen_scan_id, availability) "
            "VALUES (1, 'photos/合影 一.jpg', 'k1', ?, 4096, 7, NULL, 'PRESENT')",
            (asset_id,),
        )
        persons: dict[str, int] = {}
        for name in ("爸爸", "叔叔"):
            connection.execute(
                "INSERT INTO persons(name, name_key, avatar_face_id, merged_into, revision, "
                "created_at, updated_at) VALUES (?, ?, NULL, NULL, 1, 1, 1)",
                (name, f"nfc1:{name}"),
            )
            persons[name] = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        faces: list[str] = []
        for ordinal in (0, 1):
            face_id = hashlib.sha256(f"face-api-{ordinal}".encode()).hexdigest()
            connection.execute(
                "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
                "input_revision, location_id, bbox_json, landmarks_json, det_score, "
                "face_pixels, blur_variance, quality_state, quality_reason, artifact_id, "
                "face_revision, created_at, updated_at) VALUES (?, ?, ?, 'det', ?, ?, 1, "
                "'[0,0,10,10]', '[[1,2],[2,3],[3,4],[4,5],[5,6]]', 0.9, 90.0, 200.0, 'OK', "
                "NULL, 'artifact', 1, 1, 1)",
                (face_id, asset_id, str(FACE_SPEC_REVISION), int(ordinal), digest),
            )
            faces.append(face_id)
        connection.execute(
            "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
            "confidence, reason, revision, last_operation_id, created_at, updated_at) "
            "VALUES (?, ?, 'AUTO', 'CONFIRMED', 0.9, NULL, 1, NULL, 1, 1)",
            (faces[0], persons["爸爸"]),
        )
        connection.execute(
            "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
            "confidence, reason, revision, last_operation_id, created_at, updated_at) "
            "VALUES (?, ?, 'AUTO', 'SUGGESTED', 0.4, 'BORDERLINE', 1, NULL, 1, 1)",
            (faces[1], persons["叔叔"]),
        )
    return {"asset_id": asset_id, "faces": faces, "persons": persons}


def body_of(response: Any) -> dict[str, Any]:
    try:
        return response.json()
    except ValueError:  # pragma: no cover - every JSON endpoint answers JSON here
        return {"text": response.text}


def test_the_face_writes_require_the_session_origin_and_csrf(api_env) -> None:
    seeded = seed_face_photo(api_env)
    face = seeded["faces"][0]
    body = {
        "contract_version": CONTRACT_VERSION,
        "request_id": "f1",
        "action": "unassign",
        "target_person_id": None,
        "expected_revision": 0,
    }

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        anonymous_write = await client.put(f"/api/v1/faces/{face}/assignment", json=body)
        anonymous_batch = await client.post(
            "/api/v1/face-assignments/batch",
            json={"contract_version": CONTRACT_VERSION, "request_id": "f2", "items": []},
        )
        anonymous_read = await client.get(f"/api/v1/faces/{face}/assignment")
        csrf = await api_env.authenticate(client)
        no_csrf = await client.put(
            f"/api/v1/faces/{face}/assignment",
            json={**body, "request_id": "f3"},
            headers={"Origin": "http://127.0.0.1"},
        )
        bad_origin = await client.put(
            f"/api/v1/faces/{face}/assignment",
            json={**body, "request_id": "f4"},
            headers={"X-CSRF-Token": csrf, "Origin": "http://evil.example"},
        )
        bad_host = await client.put(
            f"/api/v1/faces/{face}/assignment",
            json={**body, "request_id": "f5"},
            headers={"X-CSRF-Token": csrf, "Origin": "http://127.0.0.1", "Host": "evil.example"},
        )
        read_ok = await client.get(f"/api/v1/faces/{face}/assignment")
        written = await client.put(
            f"/api/v1/faces/{face}/assignment",
            json={**body, "request_id": "f6"},
            headers=api_env.post_headers(csrf),
        )
        return {
            "anonymous_write": anonymous_write.status_code,
            "anonymous_write_code": body_of(anonymous_write)["code"],
            "anonymous_batch": anonymous_batch.status_code,
            "anonymous_read": anonymous_read.status_code,
            "anonymous_read_code": body_of(anonymous_read)["code"],
            "no_csrf": no_csrf.status_code,
            "no_csrf_code": body_of(no_csrf)["code"],
            "bad_origin": bad_origin.status_code,
            "bad_origin_code": body_of(bad_origin)["code"],
            "bad_host": bad_host.status_code,
            "bad_host_code": body_of(bad_host)["code"],
            "read_ok": read_ok.status_code,
            "written": written.status_code,
            "written_body": body_of(written),
        }

    result = api_env.scenario(scenario)
    assert result["anonymous_write"] == 401
    assert result["anonymous_write_code"] == "SESSION_REQUIRED"
    assert result["anonymous_batch"] == 401
    assert result["anonymous_read"] == 401
    assert result["anonymous_read_code"] == "SESSION_REQUIRED"
    assert result["no_csrf"] == 403 and result["no_csrf_code"] == "CSRF_INVALID"
    assert result["bad_origin"] == 403 and result["bad_origin_code"] == "ORIGIN_NOT_ALLOWED"
    assert result["bad_host"] == 403 and result["bad_host_code"] == "HOST_NOT_ALLOWED"
    assert result["read_ok"] == 200
    assert result["written"] == 200
    assert result["written_body"]["operation_type"] == "FACE_UNASSIGN"
    assert result["written_body"]["contract_version"] == CONTRACT_VERSION
    assert isinstance(result["written_body"]["catalog_filter_revision"], int)


def test_the_read_projection_and_the_operation_answer_carry_no_private_fact(api_env) -> None:
    seeded = seed_face_photo(api_env)
    face = seeded["faces"][0]

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await api_env.authenticate(client)
        state = await client.get(f"/api/v1/faces/{face}/assignment")
        written = await client.put(
            f"/api/v1/faces/{face}/assignment",
            json={
                "contract_version": CONTRACT_VERSION,
                "request_id": "shape-1",
                "action": "assign",
                "target_person_id": seeded["persons"]["叔叔"],
                "expected_revision": 0,
            },
            headers=api_env.post_headers(csrf),
        )
        conflict = await client.put(
            f"/api/v1/faces/{face}/assignment",
            json={
                "contract_version": CONTRACT_VERSION,
                "request_id": "shape-2",
                "action": "unassign",
                "target_person_id": None,
                "expected_revision": 0,
            },
            headers=api_env.post_headers(csrf),
        )
        return {
            "state": body_of(state),
            "written": body_of(written),
            "conflict": body_of(conflict),
            "conflict_status": conflict.status_code,
            "state_text": state.text,
            "written_text": written.text,
            "conflict_text": conflict.text,
        }

    result = api_env.scenario(scenario)
    assert result["state"] == {
        "contract_version": CONTRACT_VERSION,
        "face_id": face,
        "mode": "AUTO_ALLOWED",
        "revision": 0,
        "assigned_person_id": seeded["persons"]["爸爸"],
        "active_exclusions": [],
        "suggestions_available": False,
    }
    assert result["written"]["items"][0] == {
        "entity_type": "FACE_CONTROL",
        "face_id": face,
        "changed": True,
        "mode": "MANUAL_ASSIGNED",
        "revision": 1,
        "assigned_person_id": seeded["persons"]["叔叔"],
        "exclusions": {"activated": [seeded["persons"]["爸爸"]], "deactivated": []},
    }
    assert result["conflict_status"] == 409
    assert result["conflict"]["code"] == "FACE_ASSIGNMENT_CONFLICT"
    assert result["conflict"]["details"] == {
        "face_id": face,
        "expected_revision": 0,
        "current_revision": 1,
    }
    for text in (result["state_text"], result["written_text"], result["conflict_text"]):
        for fragment in FORBIDDEN_FRAGMENTS:
            assert fragment not in text, f"{fragment} leaked from {text[:120]}"
        assert "artifact" not in text


def test_the_frozen_error_codes_map_to_their_http_statuses(api_env) -> None:
    seeded = seed_face_photo(api_env)
    face = seeded["faces"][0]
    untouched = seeded["faces"][1]

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        request = {
            "contract_version": CONTRACT_VERSION,
            "request_id": "codes-1",
            "action": "unassign",
            "target_person_id": None,
            "expected_revision": 0,
        }
        missing_face = await client.put(
            f"/api/v1/faces/{'0' * 64}/assignment", json=request, headers=headers
        )
        missing_person = await client.put(
            f"/api/v1/faces/{face}/assignment",
            json={
                **request,
                "request_id": "codes-2",
                "action": "assign",
                "target_person_id": 9999,
            },
            headers=headers,
        )
        shape = await client.put(
            f"/api/v1/faces/{face}/assignment",
            json={
                **request,
                "request_id": "codes-3",
                "target_person_id": seeded["persons"]["爸爸"],
            },
            headers=headers,
        )
        version = await client.put(
            f"/api/v1/faces/{face}/assignment",
            json={**request, "request_id": "codes-4", "contract_version": "nope"},
            headers=headers,
        )
        too_large = await client.post(
            "/api/v1/face-assignments/batch",
            json={
                "contract_version": CONTRACT_VERSION,
                "request_id": "codes-5",
                "items": [
                    {
                        "face_id": f"{index:064x}",
                        "action": "unassign",
                        "target_person_id": None,
                        "expected_revision": 0,
                    }
                    for index in range(501)
                ],
            },
            headers=headers,
        )
        duplicate = await client.post(
            "/api/v1/face-assignments/batch",
            json={
                "contract_version": CONTRACT_VERSION,
                "request_id": "codes-6",
                "items": [
                    {
                        "face_id": face,
                        "action": "unassign",
                        "target_person_id": None,
                        "expected_revision": 0,
                    },
                    {
                        "face_id": face,
                        "action": "restore_auto",
                        "target_person_id": None,
                        "expected_revision": 0,
                    },
                ],
            },
            headers=headers,
        )
        first = await client.put(
            f"/api/v1/faces/{face}/assignment", json=request, headers=headers
        )
        second = await client.put(
            f"/api/v1/faces/{face}/assignment",
            json={
                **request,
                "request_id": "codes-7",
                "action": "restore_auto",
                "expected_revision": 1,
            },
            headers=headers,
        )
        not_undoable = await client.post(
            f"/api/v1/person-operations/{first.json()['operation_id']}/undo",
            json={"request_id": "codes-8", "expected_operation_revision": 1},
            headers=headers,
        )
        return {
            "missing_face": (missing_face.status_code, body_of(missing_face)["code"]),
            "missing_person": (missing_person.status_code, body_of(missing_person)["code"]),
            "shape": (shape.status_code, body_of(shape)["code"]),
            "version": (version.status_code, body_of(version)["code"]),
            "too_large": (too_large.status_code, body_of(too_large)["code"]),
            "too_large_details": body_of(too_large)["details"],
            "duplicate": (duplicate.status_code, body_of(duplicate)["code"]),
            "second": second.status_code,
            "not_undoable": (not_undoable.status_code, body_of(not_undoable)["code"]),
            "not_undoable_details": body_of(not_undoable)["details"],
        }

    result = api_env.scenario(scenario)
    assert result["missing_face"] == (404, "FACE_NOT_FOUND")
    assert result["missing_person"] == (404, "PERSON_NOT_FOUND")
    assert result["shape"] == (422, "VALIDATION_ERROR")
    assert result["version"] == (422, "VALIDATION_ERROR")
    assert result["too_large"] == (422, "FACE_BATCH_TOO_LARGE")
    assert result["too_large_details"]["max_batch_size"] == 500
    assert result["duplicate"] == (422, "VALIDATION_ERROR")
    assert result["second"] == 200
    assert result["not_undoable"][0] == 409
    assert result["not_undoable"][1] == "FACE_OPERATION_NOT_UNDOABLE"
    assert result["not_undoable_details"]["conflicts"][0]["entity_type"] == "FACE_CONTROL"
    assert untouched not in str(result["not_undoable_details"])


def test_the_shared_undo_endpoint_answers_in_the_family_of_the_operation(api_env) -> None:
    seeded = seed_face_photo(api_env)
    face = seeded["faces"][0]

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        face_op = await client.put(
            f"/api/v1/faces/{face}/assignment",
            json={
                "contract_version": CONTRACT_VERSION,
                "request_id": "family-1",
                "action": "unassign",
                "target_person_id": None,
                "expected_revision": 0,
            },
            headers=headers,
        )
        face_undo = await client.post(
            f"/api/v1/person-operations/{face_op.json()['operation_id']}/undo",
            json={"request_id": "family-2", "expected_operation_revision": 1},
            headers=headers,
        )
        face_undo_replay = await client.post(
            f"/api/v1/person-operations/{face_op.json()['operation_id']}/undo",
            json={"request_id": "family-2", "expected_operation_revision": 1},
            headers=headers,
        )
        person_op = await client.put(
            f"/api/v1/persons/{seeded['persons']['爸爸']}/name",
            json={"request_id": "family-3", "name": "老爸", "expected_revision": 1},
            headers=headers,
        )
        person_undo = await client.post(
            f"/api/v1/person-operations/{person_op.json()['operation_id']}/undo",
            json={
                "request_id": "family-4",
                "expected_operation_revision": int(person_op.json()["operation_revision"]),
            },
            headers=headers,
        )
        return {
            "face_undo_status": face_undo.status_code,
            "face_undo": body_of(face_undo),
            "face_undo_replay_status": face_undo_replay.status_code,
            "face_undo_replay": body_of(face_undo_replay),
            "person_undo_status": person_undo.status_code,
            "person_undo": body_of(person_undo),
        }

    result = api_env.scenario(scenario)
    assert result["face_undo_status"] == 200
    assert result["face_undo"]["contract_version"] == CONTRACT_VERSION
    assert result["face_undo"]["operation_type"] == "UNDO"
    assert set(result["face_undo"]["items"][0]) == {
        "entity_type",
        "face_id",
        "changed",
        "mode",
        "revision",
        "assigned_person_id",
        "exclusions",
    }
    assert result["face_undo_replay_status"] == 200
    assert result["face_undo_replay"]["contract_version"] == CONTRACT_VERSION
    assert result["face_undo_replay"]["replayed"] is True
    assert result["face_undo_replay"]["items"] == result["face_undo"]["items"]
    assert result["face_undo_replay"]["catalog_filter_revision"] == result["face_undo"][
        "catalog_filter_revision"
    ]
    assert result["person_undo_status"] == 200
    assert "contract_version" not in result["person_undo"]
    assert result["person_undo"]["operation_type"] == "UNDO"
    assert "entity_id" in result["person_undo"]["items"][0]
    assert "face_id" not in result["person_undo"]["items"][0]


def test_openapi_declares_the_face_correction_paths(api_env) -> None:
    paths = api_env.app.openapi()["paths"]
    assert set(paths["/api/v1/faces/{face_id}/assignment"]) == {"get", "put"}
    assert "post" in paths["/api/v1/face-assignments/batch"]
    assert "post" in paths["/api/v1/person-operations/{operation_id}/undo"]
    for path, methods in paths.items():
        if "faces/" not in path and "face-assignments" not in path:
            continue
        text = str(methods)
        for fragment in ("bbox", "landmark", "vector"):
            assert fragment not in text
