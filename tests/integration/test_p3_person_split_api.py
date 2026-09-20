"""P3-05B: the frozen ``/api/v1/persons/{id}/split`` surface behind the local boundary.

Only ``%TEMP%`` fixtures are used. The split semantics are covered by the lifecycle/undo
suites; this file is about the transport contract: session/Host/Origin/CSRF, the frozen
error codes and their HTTP statuses, the shared undo endpoint answering in the split family,
the OpenAPI surface and the fact that no path, name, box, landmark, vector or per-pair
similarity ever leaves the process.
"""

from __future__ import annotations

import hashlib
from typing import Any

from httpx import AsyncClient

from photo_search.face.contract import FACE_SPEC_REVISION

CONTRACT_VERSION = "p3-person-split-contracts.v1"

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
    "name_key",
)


def seed_group(env: Any, *, name: str = "爸爸") -> dict[str, Any]:
    """One asset with two faces that are both effective members of one named person."""
    catalog = env.service.catalog
    if catalog.read_one("SELECT 1 FROM source_roots LIMIT 1") is None:
        with catalog.write() as connection:
            connection.execute(
                "INSERT INTO source_roots(display_name, root_path, volume_hint, status) "
                "VALUES ('library', ?, 'C:', 'ONLINE')",
                (str(env.library),),
            )
    with catalog.write() as connection:
        digest = hashlib.sha256(b"split-api-asset").hexdigest()
        connection.execute(
            "INSERT INTO assets(sha256, byte_size, created_at, state) VALUES (?, 4096, 1, 'ACTIVE')",
            (digest,),
        )
        asset_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, size, "
            "mtime_ns, last_seen_scan_id, availability) "
            "VALUES (1, 'photos/合影 一.jpg', 'k1', ?, 4096, 7, NULL, 'PRESENT')",
            (asset_id,),
        )
        connection.execute(
            "INSERT INTO persons(name, name_key, avatar_face_id, merged_into, revision, "
            "created_at, updated_at) VALUES (?, ?, NULL, NULL, 1, 1, 1)",
            (name, f"nfc1:{name}"),
        )
        person = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        faces: list[str] = []
        for ordinal in (0, 1):
            face_id = hashlib.sha256(f"split-api-face-{ordinal}".encode()).hexdigest()
            connection.execute(
                "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
                "input_revision, location_id, bbox_json, landmarks_json, det_score, "
                "face_pixels, blur_variance, quality_state, quality_reason, artifact_id, "
                "face_revision, created_at, updated_at) VALUES (?, ?, ?, 'det', ?, ?, 1, "
                "'[0,0,10,10]', '[[1,2],[2,3],[3,4],[4,5],[5,6]]', 0.9, 90.0, 200.0, 'OK', "
                "NULL, 'artifact', 1, 1, 1)",
                (face_id, asset_id, str(FACE_SPEC_REVISION), int(ordinal), digest),
            )
            connection.execute(
                "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
                "confidence, reason, revision, last_operation_id, created_at, updated_at) "
                "VALUES (?, ?, 'AUTO', 'CONFIRMED', 0.9, NULL, 1, NULL, 1, 1)",
                (face_id, person),
            )
            faces.append(face_id)
    return {"asset_id": asset_id, "faces": faces, "person": person}


def body_of(response: Any) -> dict[str, Any]:
    try:
        return response.json()
    except ValueError:  # pragma: no cover - every JSON endpoint answers JSON here
        return {"text": response.text}


def split_body(face_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "request_id": "split-api-1",
        "source_expected_revision": 1,
        "faces": [
            {
                "face_id": face_id,
                "expected_control_revision": 0,
                "expected_assignment_revision": 1,
            }
        ],
    }
    body.update(overrides)
    return body


def test_the_split_write_requires_the_session_origin_and_csrf(api_env) -> None:
    seeded = seed_group(api_env)
    target = seeded["faces"][0]

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        anonymous = await client.post(
            f"/api/v1/persons/{seeded['person']}/split", json=split_body(target)
        )
        csrf = await api_env.authenticate(client)
        no_csrf = await client.post(
            f"/api/v1/persons/{seeded['person']}/split",
            json=split_body(target, request_id="split-api-2"),
            headers={"Origin": "http://127.0.0.1"},
        )
        bad_origin = await client.post(
            f"/api/v1/persons/{seeded['person']}/split",
            json=split_body(target, request_id="split-api-3"),
            headers={"X-CSRF-Token": csrf, "Origin": "http://evil.example"},
        )
        bad_host = await client.post(
            f"/api/v1/persons/{seeded['person']}/split",
            json=split_body(target, request_id="split-api-4"),
            headers={
                "X-CSRF-Token": csrf,
                "Origin": "http://127.0.0.1",
                "Host": "evil.example",
            },
        )
        written = await client.post(
            f"/api/v1/persons/{seeded['person']}/split",
            json=split_body(target, request_id="split-api-5"),
            headers=api_env.post_headers(csrf),
        )
        return {
            "anonymous": (anonymous.status_code, body_of(anonymous)["code"]),
            "no_csrf": (no_csrf.status_code, body_of(no_csrf)["code"]),
            "bad_origin": (bad_origin.status_code, body_of(bad_origin)["code"]),
            "bad_host": (bad_host.status_code, body_of(bad_host)["code"]),
            "written": (written.status_code, body_of(written)),
            "written_text": written.text,
        }

    result = api_env.scenario(scenario)
    assert result["anonymous"] == (401, "SESSION_REQUIRED")
    assert result["no_csrf"] == (403, "CSRF_INVALID")
    assert result["bad_origin"] == (403, "ORIGIN_NOT_ALLOWED")
    assert result["bad_host"] == (403, "HOST_NOT_ALLOWED")
    assert result["written"][0] == 200
    written = result["written"][1]
    assert written["contract_version"] == CONTRACT_VERSION
    assert written["operation_type"] == "SPLIT"
    assert written["changed"] is True
    assert [item["entity_type"] for item in written["items"]] == [
        "SPLIT_GROUP",
        "SPLIT_GROUP",
        "FACE_CONTROL",
    ]
    for fragment in FORBIDDEN_FRAGMENTS:
        assert fragment not in result["written_text"], fragment
    assert "照片" not in result["written_text"]
    assert "爸爸" not in result["written_text"]


def test_the_frozen_split_error_codes_map_to_their_http_statuses(api_env) -> None:
    seeded = seed_group(api_env)
    person = seeded["person"]
    target, sibling = seeded["faces"]

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        missing_person = await client.post(
            "/api/v1/persons/9999/split",
            json=split_body(target, request_id="err-1"),
            headers=headers,
        )
        missing_face = await client.post(
            f"/api/v1/persons/{person}/split",
            json=split_body("0" * 64, request_id="err-2"),
            headers=headers,
        )
        stale_source = await client.post(
            f"/api/v1/persons/{person}/split",
            json=split_body(target, request_id="err-3", source_expected_revision=7),
            headers=headers,
        )
        stale_control = await client.post(
            f"/api/v1/persons/{person}/split",
            json={
                **split_body(target, request_id="err-4"),
                "faces": [
                    {
                        "face_id": target,
                        "expected_control_revision": 4,
                        "expected_assignment_revision": 1,
                    }
                ],
            },
            headers=headers,
        )
        empty = await client.post(
            f"/api/v1/persons/{person}/split",
            json=split_body(target, request_id="err-5", faces=[]),
            headers=headers,
        )
        shape = await client.post(
            f"/api/v1/persons/{person}/split",
            json=split_body(target, request_id="err-6", source_expected_revision=0),
            headers=headers,
        )
        too_large = await client.post(
            f"/api/v1/persons/{person}/split",
            json={
                **split_body(target, request_id="err-7"),
                "faces": [
                    {
                        "face_id": f"{index:064x}",
                        "expected_control_revision": 0,
                        "expected_assignment_revision": 1,
                    }
                    for index in range(501)
                ],
            },
            headers=headers,
        )
        would_empty = await client.post(
            f"/api/v1/persons/{person}/split",
            json={
                **split_body(target, request_id="err-8"),
                "faces": [
                    {
                        "face_id": face,
                        "expected_control_revision": 0,
                        "expected_assignment_revision": 1,
                    }
                    for face in (target, sibling)
                ],
            },
            headers=headers,
        )
        written = await client.post(
            f"/api/v1/persons/{person}/split",
            json=split_body(target, request_id="err-9"),
            headers=headers,
        )
        conflict = await client.post(
            f"/api/v1/persons/{person}/split",
            json=split_body(sibling, request_id="err-9"),
            headers=headers,
        )
        return {
            "missing_person": (missing_person.status_code, body_of(missing_person)["code"]),
            "missing_face": (missing_face.status_code, body_of(missing_face)["code"]),
            "stale_source": (stale_source.status_code, body_of(stale_source)["code"]),
            "stale_control": (stale_control.status_code, body_of(stale_control)["code"]),
            "empty": (empty.status_code, body_of(empty)["code"]),
            "shape": (shape.status_code, body_of(shape)["code"]),
            "too_large": (too_large.status_code, body_of(too_large)["code"]),
            "too_large_details": body_of(too_large)["details"],
            "would_empty": (would_empty.status_code, body_of(would_empty)["code"]),
            "would_empty_details": body_of(would_empty)["details"],
            "written": written.status_code,
            "conflict": (conflict.status_code, body_of(conflict)["code"]),
            "conflict_details": body_of(conflict)["details"],
        }

    result = api_env.scenario(scenario)
    assert result["missing_person"] == (404, "PERSON_NOT_FOUND")
    assert result["missing_face"] == (404, "FACE_NOT_FOUND")
    assert result["stale_source"] == (409, "PERSON_REVISION_CONFLICT")
    assert result["stale_control"] == (409, "FACE_ASSIGNMENT_CONFLICT")
    assert result["empty"] == (422, "VALIDATION_ERROR")
    assert result["shape"] == (422, "VALIDATION_ERROR")
    assert result["too_large"] == (422, "PERSON_BATCH_TOO_LARGE")
    assert result["too_large_details"]["max_batch_size"] == 500
    assert result["would_empty"] == (409, "PERSON_SPLIT_CONFLICT")
    assert result["would_empty_details"]["reason"] == "source_would_be_empty"
    assert result["written"] == 200
    assert result["conflict"] == (409, "PERSON_IDEMPOTENCY_CONFLICT")
    assert "payload" not in str(result["conflict_details"])


def test_the_shared_undo_endpoint_answers_the_split_family_and_replays(api_env) -> None:
    seeded = seed_group(api_env)
    person = seeded["person"]
    target = seeded["faces"][0]

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        split = await client.post(
            f"/api/v1/persons/{person}/split",
            json=split_body(target, request_id="family-1"),
            headers=headers,
        )
        split_answer = body_of(split)
        undone = await client.post(
            f"/api/v1/person-operations/{split_answer['operation_id']}/undo",
            json={"request_id": "family-2", "expected_operation_revision": 1},
            headers=headers,
        )
        replay = await client.post(
            f"/api/v1/person-operations/{split_answer['operation_id']}/undo",
            json={"request_id": "family-2", "expected_operation_revision": 1},
            headers=headers,
        )
        person_op = await client.put(
            f"/api/v1/persons/{person}/name",
            json={"request_id": "family-3", "name": "老爸", "expected_revision": 3},
            headers=headers,
        )
        person_undo = await client.post(
            f"/api/v1/person-operations/{body_of(person_op)['operation_id']}/undo",
            json={
                "request_id": "family-4",
                "expected_operation_revision": body_of(person_op)["operation_revision"],
            },
            headers=headers,
        )
        return {
            "split_replay": body_of(
                await client.post(
                    f"/api/v1/persons/{person}/split",
                    json=split_body(target, request_id="family-1"),
                    headers=headers,
                )
            ),
            "undone": body_of(undone),
            "replay": body_of(replay),
            "person_undo": body_of(person_undo),
            "text": undone.text + replay.text,
        }

    result = api_env.scenario(scenario)
    assert result["split_replay"]["replayed"] is True
    assert result["undone"]["contract_version"] == CONTRACT_VERSION
    assert result["undone"]["operation_type"] == "UNDO"
    assert result["undone"]["undo_of_operation_id"] is not None
    assert set(result["undone"]["items"][0]) == {
        "entity_type",
        "entity_id",
        "role",
        "changed",
        "revision",
        "merged_into",
    }
    assert set(result["undone"]["items"][2]) == {
        "entity_type",
        "face_id",
        "changed",
        "mode",
        "revision",
        "assigned_person_id",
        "exclusions",
    }
    assert result["replay"]["replayed"] is True
    assert result["replay"]["items"] == result["undone"]["items"]
    assert result["replay"]["catalog_filter_revision"] == result["undone"][
        "catalog_filter_revision"
    ]
    # The P3-03 person family is untouched by the new branch.
    assert "contract_version" not in result["person_undo"]
    assert "entity_id" in result["person_undo"]["items"][0]
    for fragment in FORBIDDEN_FRAGMENTS:
        assert fragment not in result["text"], fragment
    assert "爸爸" not in result["text"]


def test_openapi_declares_the_split_path_and_no_private_fact(api_env) -> None:
    paths = api_env.app.openapi()["paths"]
    split_path = paths["/api/v1/persons/{source_person_id}/split"]
    assert set(split_path) == {"post"}
    text = str(split_path)
    for fragment in ("bbox", "landmark", "vector", "confidence", "name_key"):
        assert fragment not in text
    undo = paths["/api/v1/person-operations/{operation_id}/undo"]
    assert set(undo) == {"post"}
    # The request body is closed: an extra key is refused by the model, not ignored.
    schema = api_env.app.openapi()["components"]["schemas"]["PersonSplitRequest"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "contract_version",
        "request_id",
        "source_expected_revision",
        "faces",
    }
    schemas = api_env.app.openapi()["components"]["schemas"]
    for name in (
        "PersonSplitOperationResponse",
        "SplitGroupItemResponse",
        "FaceOperationItemResponse",
        "FaceExclusionChanges",
    ):
        assert schemas[name]["additionalProperties"] is False
