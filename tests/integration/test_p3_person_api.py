"""P3-03: the frozen ``/api/v1`` person surface behind the local session boundary.

Only ``%TEMP%`` fixtures are used. Persons, faces and links are seeded straight into the
protected catalog (the person semantics themselves are covered by the merge/undo suites),
because this file is about the transport contract: session/Host/Origin/CSRF, the error
envelope with its frozen codes and HTTP statuses, and the fact that no path, box, landmark,
vector or similarity ever leaves the process.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from httpx import AsyncClient

from photo_search.api.settings import ApiSettings
from photo_search.face.contract import FACE_SPEC_REVISION
from photo_search.persons.contracts import name_key_for

MUTATING_ROUTES = (
    ("put", "/api/v1/persons/1/name"),
    ("post", "/api/v1/persons/merge"),
    ("post", "/api/v1/person-operations/op/undo"),
)


def seed_person(env: Any, *, name: str | None = None, faces: int = 1) -> tuple[int, list[str]]:
    """One unnamed or named group with ``faces`` effective faces on distinct assets."""
    catalog = env.service.catalog
    with catalog.write() as connection:
        cursor = connection.execute(
            "INSERT INTO persons(name, name_key, avatar_face_id, merged_into, revision, "
            "created_at, updated_at) VALUES (?, ?, NULL, NULL, 1, 1, 1)",
            (name, None if name is None else name_key_for(name)),
        )
        person_id = int(cursor.lastrowid)
        face_ids: list[str] = []
        for index in range(int(faces)):
            serial = f"{person_id}-{index}"
            asset_digest = hashlib.sha256(f"asset-{serial}".encode()).hexdigest()
            connection.execute(
                "INSERT INTO assets(sha256, byte_size, created_at, state) "
                "VALUES (?, 4096, 1, 'ACTIVE')",
                (asset_digest,),
            )
            asset_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
            connection.execute(
                "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, "
                "size, mtime_ns, last_seen_scan_id, availability) "
                "VALUES (1, ?, ?, ?, 4096, 7, NULL, 'PRESENT')",
                (f"photos/{serial}.jpg", f"photos/{serial}.jpg", asset_id),
            )
            face_id = hashlib.sha256(f"face-{serial}".encode()).hexdigest()
            connection.execute(
                "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
                "input_revision, location_id, bbox_json, landmarks_json, det_score, "
                "face_pixels, blur_variance, quality_state, quality_reason, artifact_id, "
                "face_revision, created_at, updated_at) VALUES (?, ?, ?, 'det', 0, ?, 1, "
                "'[0,0,10,10]', '[[1,2],[2,3],[3,4],[4,5],[5,6]]', 0.9, 90.0, 200.0, 'OK', "
                "NULL, 'artifact', 1, 1, 1)",
                (face_id, asset_id, str(FACE_SPEC_REVISION), asset_digest),
            )
            connection.execute(
                "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
                "confidence, reason, revision, last_operation_id, created_at, updated_at) "
                "VALUES (?, ?, 'AUTO', 'CONFIRMED', 0.9, NULL, 1, NULL, 1, 1)",
                (face_id, person_id),
            )
            face_ids.append(face_id)
    # One source root is what the redactor knows about; the seed needs it once.
    if catalog.read_one("SELECT 1 FROM source_roots LIMIT 1") is None:  # pragma: no cover
        raise AssertionError("the api fixture registers its own source root")
    return person_id, face_ids


def ensure_source_root(env: Any) -> None:
    catalog = env.service.catalog
    if catalog.read_one("SELECT 1 FROM source_roots LIMIT 1") is None:
        with catalog.write() as connection:
            connection.execute(
                "INSERT INTO source_roots(display_name, root_path, volume_hint, status) "
                "VALUES ('library', ?, 'C:', 'ONLINE')",
                (str(env.library),),
            )


def api_settings(env: Any) -> ApiSettings:
    return env.settings


async def get_json(client: AsyncClient, url: str, **kwargs: Any) -> dict[str, Any]:
    response = await client.get(url, **kwargs)
    try:
        body = response.json()
    except ValueError:  # pragma: no cover - every JSON endpoint answers JSON here
        body = None
    return {"status": response.status_code, "body": body}


def test_the_person_writes_require_the_session_origin_and_csrf(api_env) -> None:
    ensure_source_root(api_env)
    person, _ = seed_person(api_env)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        anonymous = await client.put(
            f"/api/v1/persons/{person}/name",
            json={"request_id": "r1", "name": "妈妈", "expected_revision": 1},
        )
        merge_anonymous = await client.post(
            "/api/v1/persons/merge",
            json={
                "request_id": "r2",
                "target_person_id": person,
                "target_expected_revision": 1,
                "sources": [{"person_id": person, "expected_revision": 1}],
            },
        )
        csrf = await api_env.authenticate(client)
        no_csrf = await client.put(
            f"/api/v1/persons/{person}/name",
            json={"request_id": "r3", "name": "妈妈", "expected_revision": 1},
            headers={"Origin": "http://127.0.0.1"},
        )
        bad_origin = await client.put(
            f"/api/v1/persons/{person}/name",
            json={"request_id": "r4", "name": "妈妈", "expected_revision": 1},
            headers={"X-CSRF-Token": csrf, "Origin": "http://evil.example"},
        )
        read_ok = await client.get(f"/api/v1/persons/{person}")
        return {
            "anonymous": anonymous.status_code,
            "anonymous_code": anonymous.json()["code"],
            "merge_anonymous": merge_anonymous.status_code,
            "no_csrf": no_csrf.status_code,
            "no_csrf_code": no_csrf.json()["code"],
            "bad_origin": bad_origin.status_code,
            "bad_origin_code": bad_origin.json()["code"],
            "read_ok": read_ok.status_code,
        }

    result = api_env.scenario(scenario)
    assert result == {
        "anonymous": 401,
        "anonymous_code": "SESSION_REQUIRED",
        "merge_anonymous": 401,
        "no_csrf": 403,
        "no_csrf_code": "CSRF_INVALID",
        "bad_origin": 403,
        "bad_origin_code": "ORIGIN_NOT_ALLOWED",
        "read_ok": 200,
    }


def test_a_name_is_saved_and_an_old_id_answers_with_its_canonical_person(api_env) -> None:
    ensure_source_root(api_env)
    first, _ = seed_person(api_env)
    second, _ = seed_person(api_env)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await api_env.authenticate(client)
        named = await client.put(
            f"/api/v1/persons/{first}/name",
            json={"request_id": "name-1", "name": "  爸爸 ", "expected_revision": 1},
            headers=api_env.post_headers(csrf),
        )
        merged = await client.put(
            f"/api/v1/persons/{second}/name",
            json={"request_id": "name-2", "name": "爸爸", "expected_revision": 1},
            headers=api_env.post_headers(csrf),
        )
        old = await get_json(client, f"/api/v1/persons/{second}")
        target = await get_json(client, f"/api/v1/persons/{first}")
        missing = await get_json(client, "/api/v1/persons/9999")
        return {
            "named": named.status_code,
            "named_body": named.json(),
            "merged": merged.status_code,
            "merged_type": merged.json()["operation_type"],
            "old_status": old["status"],
            "old_body": old["body"],
            "target_body": target["body"],
            "missing_status": missing["status"],
            "missing_code": missing["body"]["code"],
        }

    result = api_env.scenario(scenario)
    assert result["named"] == 200
    assert result["named_body"]["operation_type"] == "RENAME"
    assert result["named_body"]["replayed"] is False
    assert result["merged"] == 200
    assert result["merged_type"] == "MERGE_BY_NAME"
    assert result["old_status"] == 200
    assert result["old_body"]["requested_person_id"] == second
    assert result["old_body"]["canonical_person_id"] == first
    assert result["old_body"]["redirected"] is True
    assert result["target_body"]["name"] == "爸爸"
    # Two members on two assets: the counts follow the canonical group.
    assert result["target_body"]["face_count"] == 2
    assert result["target_body"]["asset_count"] == 2
    assert result["missing_status"] == 404
    assert result["missing_code"] == "PERSON_NOT_FOUND"


def test_the_merge_and_undo_endpoints_round_trip(api_env) -> None:
    ensure_source_root(api_env)
    target, _ = seed_person(api_env, name="爸爸")
    source, _ = seed_person(api_env)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await api_env.authenticate(client)
        merged = await client.post(
            "/api/v1/persons/merge",
            json={
                "request_id": "merge-1",
                "target_person_id": target,
                "target_expected_revision": 1,
                "sources": [{"person_id": source, "expected_revision": 1}],
            },
            headers=api_env.post_headers(csrf),
        )
        body = merged.json()
        undo = await client.post(
            f"/api/v1/person-operations/{body['operation_id']}/undo",
            json={
                "request_id": "undo-1",
                "expected_operation_revision": body["operation_revision"],
            },
            headers=api_env.post_headers(csrf),
        )
        twice = await client.post(
            f"/api/v1/person-operations/{body['operation_id']}/undo",
            json={
                "request_id": "undo-2",
                "expected_operation_revision": body["operation_revision"],
            },
            headers=api_env.post_headers(csrf),
        )
        return {
            "merged": merged.status_code,
            "merged_body": body,
            "undo": undo.status_code,
            "undo_body": undo.json(),
            "twice": twice.status_code,
            "twice_body": twice.json(),
        }

    result = api_env.scenario(scenario)
    assert result["merged"] == 200
    assert result["merged_body"]["operation_type"] == "MERGE"
    assert result["merged_body"]["changed"] is True
    assert result["undo"] == 200
    assert result["undo_body"]["operation_type"] == "UNDO"
    assert result["undo_body"]["undo_of_operation_id"] == result["merged_body"]["operation_id"]
    assert result["twice"] == 409
    assert result["twice_body"]["code"] == "PERSON_OPERATION_NOT_UNDOABLE"


def test_the_person_errors_keep_their_frozen_codes_and_statuses(api_env) -> None:
    ensure_source_root(api_env)
    person, _ = seed_person(api_env)
    other, _ = seed_person(api_env)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        blank = await client.put(
            f"/api/v1/persons/{person}/name",
            json={"request_id": "bad-1", "name": "   ", "expected_revision": 1},
            headers=headers,
        )
        stale = await client.put(
            f"/api/v1/persons/{person}/name",
            json={"request_id": "bad-2", "name": "妈妈", "expected_revision": 9},
            headers=headers,
        )
        no_sources = await client.post(
            "/api/v1/persons/merge",
            json={
                "request_id": "bad-3",
                "target_person_id": person,
                "target_expected_revision": 1,
                "sources": [],
            },
            headers=headers,
        )
        too_many = await client.post(
            "/api/v1/persons/merge",
            json={
                "request_id": "bad-4",
                "target_person_id": person,
                "target_expected_revision": 1,
                "sources": [
                    {"person_id": index + 2, "expected_revision": 1} for index in range(501)
                ],
            },
            headers=headers,
        )
        # A rejected request never burns its idempotency key: "bad-2" is still free.
        retry = await client.put(
            f"/api/v1/persons/{person}/name",
            json={"request_id": "bad-2", "name": "妈妈", "expected_revision": 1},
            headers=headers,
        )
        # The same request_id with a different payload is the structured conflict.
        conflict = await client.put(
            f"/api/v1/persons/{person}/name",
            json={"request_id": "bad-2", "name": "爸爸", "expected_revision": 1},
            headers=headers,
        )
        return {
            "blank": [blank.status_code, blank.json()["code"]],
            "stale": [stale.status_code, stale.json()["code"]],
            "no_sources": [no_sources.status_code, no_sources.json()["code"]],
            "too_many": [too_many.status_code, too_many.json()["code"]],
            "retry": [retry.status_code, retry.json()["operation_type"]],
            "conflict": [conflict.status_code, conflict.json()["code"]],
            "other_untouched": await get_json(client, f"/api/v1/persons/{other}"),
        }

    result = api_env.scenario(scenario)
    assert result["blank"] == [422, "PERSON_NAME_INVALID"]
    assert result["stale"] == [409, "PERSON_REVISION_CONFLICT"]
    assert result["no_sources"] == [422, "VALIDATION_ERROR"]
    assert result["too_many"] == [422, "PERSON_BATCH_TOO_LARGE"]
    # A rejected request never burns its idempotency key; the same key with another payload
    # is the frozen structured conflict.
    assert result["retry"] == [200, "RENAME"]
    assert result["conflict"] == [409, "PERSON_IDEMPOTENCY_CONFLICT"]


def test_capabilities_reports_person_counts_without_a_name(api_env) -> None:
    ensure_source_root(api_env)
    named, _ = seed_person(api_env)
    seed_person(api_env)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await api_env.authenticate(client)
        await client.put(
            f"/api/v1/persons/{named}/name",
            json={"request_id": "caps-1", "name": "妈妈", "expected_revision": 1},
            headers=api_env.post_headers(csrf),
        )
        response = await client.get("/api/v1/capabilities")
        return {"status": response.status_code, "text": response.text, "body": response.json()}

    result = api_env.scenario(scenario)
    faces = result["body"]["faces"]
    assert result["status"] == 200
    assert faces["named_persons"] == 1
    assert faces["unnamed_persons"] == 1
    assert faces["aliases"] == 0
    assert faces["recent_operations"] == 1
    assert faces["pending_confirmation"] == 0
    assert "妈妈" not in result["text"]
    assert name_key_for("妈妈") not in result["text"]
    assert str(api_settings(api_env).data_root) not in result["text"]
    assert str(Path(api_env.library)) not in result["text"]
