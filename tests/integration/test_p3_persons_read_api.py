"""P3-06: the three read lists and the face-avatar endpoint, driven through the real API.

Every fixture lives under ``tmp_path`` (``%TEMP%``): synthetic JPEGs with real bytes, real
ingestion, and deterministic face/person/control rows. No model, no network, no real photo
library.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from p3_persons_page_fixtures import (
    add_link,
    add_person,
    seed_library,
    set_control,
    set_exclusion,
)

#: Field names that must never reach the page, whatever the endpoint answers with.
FORBIDDEN_KEYS = (
    "name_key",
    "normalized_alias",
    "bbox",
    "bbox_json",
    "landmarks",
    "landmarks_json",
    "embedding",
    "vector",
    "vector_blob",
    "similarity",
    "confidence",
    "det_score",
    "blur_variance",
    "before_value_json",
    "after_value_json",
    "payload_sha256",
    "request_id",
    "root_path",
    "absolute_path",
    "relative_path",
)


def collect_keys(payload: Any) -> list[str]:
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.append(str(key))
            found.extend(collect_keys(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(collect_keys(item))
    return found


def assert_private_free(*payloads: Any) -> None:
    for payload in payloads:
        keys = [key.casefold() for key in collect_keys(payload)]
        for key in keys:
            for needle in FORBIDDEN_KEYS:
                assert key != needle, f"private field {key} leaked into a page response"
        rendered = repr(payload)
        assert "C:\\" not in rendered and "D:\\" not in rendered, "a local path leaked"


def seed_people(env: Any) -> dict[str, Any]:
    """Three photos, five faces, three people and one human lock."""
    photos = seed_library(
        env,
        {
            "合照.jpg": [(20, 20, 90, 90), (150, 30, 220, 100)],
            "单人.jpg": [(40, 40, 120, 120)],
            "合影.jpg": [(10, 10, 80, 80), (100, 10, 170, 80), (200, 10, 270, 80)],
        },
    )
    catalog = env.service.catalog
    group_face, neighbour = photos["合照.jpg"]["faces"]
    solo_face = photos["单人.jpg"]["faces"][0]
    third_first, third_second, third_third = photos["合影.jpg"]["faces"]

    dad = add_person(catalog, name="爸爸", avatar_face_id=group_face)
    mom = add_person(catalog, name="妈妈", avatar_face_id=solo_face)
    unnamed = add_person(catalog)
    add_link(catalog, group_face, dad)
    add_link(catalog, solo_face, mom)
    add_link(catalog, third_first, unnamed)
    add_link(catalog, third_second, dad, decision="SUGGESTED", reason="BORDERLINE")
    # The third face of the group photo was deliberately taken out of every automatic
    # assignment, which is exactly what the frozen unassign path writes: no effective link at
    # all, the human lock, and an active denial for the person it used to belong to. It must
    # leave the member list and appear in the manual queue.
    set_control(catalog, third_third, "MANUAL_UNASSIGNED")
    set_exclusion(catalog, third_third, unnamed)
    # `neighbour` stays unlinked on purpose: a face with no effective link belongs to nobody.
    return {
        "photos": photos,
        "dad": dad,
        "mom": mom,
        "unnamed": unnamed,
        "faces": {
            "group_face": group_face,
            "neighbour": neighbour,
            "solo_face": solo_face,
            "third_first": third_first,
            "third_second": third_second,
            "third_third": third_third,
        },
    }


def test_the_person_list_pages_without_gaps_or_repeats(api_env) -> None:
    seeded = seed_people(api_env)

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        first = await client.get("/api/v1/persons", params={"limit": 2})
        second = await client.get(
            "/api/v1/persons",
            params={"limit": 2, "cursor": first.json()["next_cursor"]},
        )
        named = await client.get("/api/v1/persons", params={"kind": "named", "limit": 50})
        unnamed = await client.get("/api/v1/persons", params={"kind": "unnamed", "limit": 50})
        return {"first": first, "second": second, "named": named, "unnamed": unnamed}

    result = api_env.scenario(scenario)
    first = result["first"]
    assert first.status_code == 200, first.text
    body = first.json()
    # Stable `person_id` order, and a bounded page that says so.
    assert [row["person_id"] for row in body["persons"]] == [seeded["dad"], seeded["mom"]]
    assert body["limit"] == 2
    assert body["has_more"] is True
    assert body["next_cursor"]
    assert body["kind"] == "all"
    dad_card = body["persons"][0]
    assert dad_card["name"] == "爸爸"
    assert dad_card["revision"] >= 1
    # Effective members only: the SUGGESTED link is not an assignment, and neither is the
    # third face of the group photo (a human took it out of every automatic path).
    assert dad_card["face_count"] == 1
    assert dad_card["asset_count"] == 1
    assert dad_card["avatar_face_id"] == seeded["faces"]["group_face"]
    assert dad_card["thumbnail_url"] == (
        "/api/v1/faces/" + seeded["faces"]["group_face"] + "/thumbnail"
    )

    second = result["second"]
    assert second.status_code == 200, second.text
    rows = second.json()["persons"]
    assert [row["person_id"] for row in rows] == [seeded["unnamed"]]
    assert second.json()["has_more"] is False
    assert second.json()["next_cursor"] is None
    assert rows[0]["name"] is None
    assert rows[0]["thumbnail_url"] is None
    assert rows[0]["face_count"] == 1

    # The two kinds partition the same active set.
    named_ids = {row["person_id"] for row in result["named"].json()["persons"]}
    unnamed_ids = {row["person_id"] for row in result["unnamed"].json()["persons"]}
    assert named_ids == {seeded["dad"], seeded["mom"]}
    assert unnamed_ids == {seeded["unnamed"]}
    for response in result.values():
        assert_private_free(response.json())


def test_a_merged_id_is_never_listed_twice_and_still_answers_with_its_canonical(api_env) -> None:
    seeded = seed_people(api_env)

    async def scenario(client: AsyncClient) -> dict:
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        # Saving the already-owned name on the second person is the ADR-0006 same-name merge.
        renamed = await client.put(
            f"/api/v1/persons/{seeded['mom']}/name",
            json={
                "request_id": "p3-06-rename-1",
                "name": "爸爸",
                "expected_revision": 1,
            },
            headers=headers,
        )
        people = await client.get("/api/v1/persons", params={"limit": 50})
        faces = await client.get(
            "/api/v1/faces", params={"person_id": seeded["mom"], "limit": 50}
        )
        lookup = await client.get(f"/api/v1/persons/{seeded['mom']}")
        return {"renamed": renamed, "people": people, "faces": faces, "lookup": lookup}

    result = api_env.scenario(scenario)
    renamed = result["renamed"]
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["operation_type"] == "MERGE_BY_NAME"
    assert renamed.json()["changed"] is True

    ids = [row["person_id"] for row in result["people"].json()["persons"]]
    assert seeded["dad"] in ids
    assert seeded["mom"] not in ids, "a merged id must not be listed a second time"

    faces = result["faces"]
    assert faces.status_code == 200, faces.text
    body = faces.json()
    assert body["requested_person_id"] == seeded["mom"]
    assert body["canonical_person_id"] == seeded["dad"]
    assert body["redirected"] is True
    assert body["view"] == "person"
    # Both members now resolve to the canonical person, and the id still works.
    assert {row["face_id"] for row in body["faces"]} == {
        seeded["faces"]["group_face"],
        seeded["faces"]["solo_face"],
    }
    assert body["person_revision"] == result["lookup"].json()["revision"]
    assert result["lookup"].json()["canonical_person_id"] == seeded["dad"]


def test_the_three_views_are_exclusive_and_cursors_never_cross_lists(api_env) -> None:
    seed_people(api_env)

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        both = await client.get(
            "/api/v1/faces",
            params={"person_id": 1, "assignment_state": "manual_unassigned"},
        )
        neither = await client.get("/api/v1/faces")
        wrong_state = await client.get(
            "/api/v1/faces", params={"assignment_state": "manual_assigned"}
        )
        wrong_kind = await client.get("/api/v1/persons", params={"kind": "everyone"})
        zero_limit = await client.get("/api/v1/persons", params={"limit": 0})
        unknown_person = await client.get("/api/v1/faces", params={"person_id": 999999})
        persons_page = await client.get("/api/v1/persons", params={"limit": 1})
        cross = await client.get(
            "/api/v1/faces",
            params={"person_id": 1, "cursor": persons_page.json()["next_cursor"]},
        )
        garbage = await client.get("/api/v1/persons", params={"cursor": "!!!not-base64!!!"})
        return {
            "both": both,
            "neither": neither,
            "wrong_state": wrong_state,
            "wrong_kind": wrong_kind,
            "zero_limit": zero_limit,
            "unknown_person": unknown_person,
            "cross": cross,
            "garbage": garbage,
            "persons_page": persons_page,
        }

    result = api_env.scenario(scenario)
    for key in ("both", "neither", "wrong_state", "wrong_kind", "zero_limit"):
        response = result[key]
        assert response.status_code == 422, (key, response.text)
        assert response.json()["code"] == "VALIDATION_ERROR", key
    assert result["unknown_person"].status_code == 404
    assert result["unknown_person"].json()["code"] == "PERSON_NOT_FOUND"
    # A cursor minted by the person list is refused by the face list instead of resuming at
    # a meaningless position.
    assert result["cross"].status_code == 422
    assert result["cross"].json()["code"] == "INVALID_CURSOR"
    assert result["garbage"].status_code == 422
    assert result["garbage"].json()["code"] == "INVALID_CURSOR"


def test_the_manual_unassigned_queue_is_exactly_the_human_lock(api_env) -> None:
    seeded = seed_people(api_env)

    async def scenario(client: AsyncClient) -> dict:
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        queued = await client.get(
            "/api/v1/faces", params={"assignment_state": "manual_unassigned", "limit": 50}
        )
        restored = await client.put(
            f"/api/v1/faces/{seeded['faces']['third_third']}/assignment",
            json={
                "contract_version": "p3-face-correction-contracts.v1",
                "request_id": "p3-06-restore-1",
                "action": "restore_auto",
                "target_person_id": None,
                "expected_revision": 1,
            },
            headers=headers,
        )
        after = await client.get(
            "/api/v1/faces", params={"assignment_state": "manual_unassigned", "limit": 50}
        )
        members = await client.get(
            "/api/v1/faces", params={"person_id": seeded["unnamed"], "limit": 50}
        )
        return {"queued": queued, "restored": restored, "after": after, "members": members}

    result = api_env.scenario(scenario)
    queued = result["queued"]
    assert queued.status_code == 200, queued.text
    body = queued.json()
    assert body["view"] == "manual_unassigned"
    assert body["requested_person_id"] is None
    assert body["canonical_person_id"] is None
    assert [row["face_id"] for row in body["faces"]] == [seeded["faces"]["third_third"]]
    row = body["faces"][0]
    assert row["mode"] == "MANUAL_UNASSIGNED"
    assert row["control_revision"] == 1
    assert row["assigned_person_id"] is None
    assert row["assignment_revision"] is None
    assert row["suggestions_available"] is False
    assert row["thumbnail_url"] == (
        "/api/v1/faces/" + seeded["faces"]["third_third"] + "/thumbnail"
    )
    assert_private_free(body)

    assert result["restored"].status_code == 200, result["restored"].text
    assert result["restored"].json()["changed"] is True
    assert result["after"].json()["faces"] == []
    # ``restore_auto`` hands the face back to the automatic path: the human's effective link
    # is ended (ADR-0007 §3), so the face is no longer an *effective* member of its old person
    # and only that face moved.
    assert [row["face_id"] for row in result["members"].json()["faces"]] == [
        seeded["faces"]["third_first"]
    ]


def test_a_face_with_an_active_exclusion_leaves_the_member_list(api_env) -> None:
    seeded = seed_people(api_env)
    set_exclusion(api_env.service.catalog, seeded["faces"]["third_first"], seeded["unnamed"])

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        members = await client.get(
            "/api/v1/faces", params={"person_id": seeded["unnamed"], "limit": 50}
        )
        detail = await client.get(f"/api/v1/persons/{seeded['unnamed']}")
        return {"members": members, "detail": detail}

    result = api_env.scenario(scenario)
    body = result["members"].json()
    # The face is still linked, but an active human denial removes it from the effective set
    # of both the list and the detail view (they share one definition).
    assert body["faces"] == []
    assert result["detail"].json()["face_count"] == 0


def test_the_operation_log_is_newest_first_and_carries_no_payload(api_env) -> None:
    seeded = seed_people(api_env)

    async def scenario(client: AsyncClient) -> dict:
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        first = await client.put(
            f"/api/v1/persons/{seeded['unnamed']}/name",
            json={
                "request_id": "p3-06-log-1",
                "name": "小舅",
                "expected_revision": 1,
            },
            headers=headers,
        )
        second = await client.put(
            f"/api/v1/faces/{seeded['faces']['third_second']}/assignment",
            json={
                "contract_version": "p3-face-correction-contracts.v1",
                "request_id": "p3-06-log-2",
                "action": "assign",
                "target_person_id": seeded["unnamed"],
                "expected_revision": 0,
            },
            headers=headers,
        )
        log = await client.get("/api/v1/person-operations", params={"limit": 50})
        page_one = await client.get("/api/v1/person-operations", params={"limit": 1})
        page_two = await client.get(
            "/api/v1/person-operations",
            params={"limit": 1, "cursor": page_one.json()["next_cursor"]},
        )
        undone = await client.post(
            f"/api/v1/person-operations/{second.json()['operation_id']}/undo",
            json={
                "request_id": "p3-06-log-3",
                "expected_operation_revision": second.json()["operation_revision"],
            },
            headers=headers,
        )
        after_undo = await client.get("/api/v1/person-operations", params={"limit": 50})
        stale_cursor = await client.get(
            "/api/v1/person-operations", params={"cursor": "not-a-cursor"}
        )
        return {
            "first": first,
            "second": second,
            "log": log,
            "page_one": page_one,
            "page_two": page_two,
            "undone": undone,
            "after_undo": after_undo,
            "stale_cursor": stale_cursor,
        }

    result = api_env.scenario(scenario)
    assert result["first"].status_code == 200, result["first"].text
    assert result["second"].status_code == 200, result["second"].text
    log = result["log"]
    assert log.status_code == 200, log.text
    body = log.json()
    revisions = [row["operation_revision"] for row in body["operations"]]
    assert revisions == sorted(revisions, reverse=True)
    assert revisions[0] == result["second"].json()["operation_revision"]
    newest = body["operations"][0]
    assert newest["operation_type"] == "FACE_ASSIGN"
    assert newest["status"] == "COMMITTED"
    assert newest["changed"] is True
    assert newest["face_ids"] == [seeded["faces"]["third_second"]]
    assert newest["item_count"] == 1
    assert newest["committed_at"] > 0
    rename_row = next(row for row in body["operations"] if row["operation_type"] == "RENAME")
    assert rename_row["person_ids"] == [seeded["unnamed"]]
    assert rename_row["face_ids"] == []
    assert_private_free(body)

    # Paging can only move to older operations and never repeats one.
    assert result["page_one"].json()["operations"][0]["operation_id"] == newest["operation_id"]
    assert (
        result["page_two"].json()["operations"][0]["operation_id"]
        != newest["operation_id"]
    )
    assert result["page_two"].json()["operations"][0]["operation_revision"] < revisions[0]
    assert result["stale_cursor"].status_code == 422
    assert result["stale_cursor"].json()["code"] == "INVALID_CURSOR"

    # The compensation is a new operation and the original flips to UNDONE.
    assert result["undone"].status_code == 200, result["undone"].text
    statuses = {
        row["operation_id"]: row["status"] for row in result["after_undo"].json()["operations"]
    }
    assert statuses[newest["operation_id"]] == "UNDONE"
    assert statuses[result["undone"].json()["operation_id"]] == "COMMITTED"


def test_the_read_surface_is_session_protected(api_env) -> None:
    seed_people(api_env)

    async def scenario(client: AsyncClient) -> dict:
        return {
            "persons": await client.get("/api/v1/persons"),
            "faces": await client.get("/api/v1/faces", params={"person_id": 1}),
            "operations": await client.get("/api/v1/person-operations"),
            "thumbnail": await client.get(
                "/api/v1/faces/" + "a" * 64 + "/thumbnail"
            ),
        }

    result = api_env.scenario(scenario)
    for key, response in result.items():
        assert response.status_code == 401, (key, response.text)
        assert response.json()["code"] == "SESSION_REQUIRED"


def test_openapi_closes_the_new_read_schemas(api_env) -> None:
    paths = api_env.app.openapi()["paths"]
    assert set(paths["/api/v1/persons"]) == {"get"}
    assert set(paths["/api/v1/faces"]) == {"get"}
    assert set(paths["/api/v1/faces/{face_id}/thumbnail"]) == {"get"}
    assert set(paths["/api/v1/person-operations"]) == {"get"}
    for path in (
        "/api/v1/persons",
        "/api/v1/faces",
        "/api/v1/faces/{face_id}/thumbnail",
        "/api/v1/person-operations",
    ):
        text = str(paths[path]).casefold()
        for fragment in ("bbox", "landmark", "vector", "confidence", "name_key", "similarity"):
            assert fragment not in text, f"{fragment} leaked into the {path} schema"

    schemas = api_env.app.openapi()["components"]["schemas"]
    for name in (
        "PersonSummary",
        "PersonListResponse",
        "FaceSummary",
        "FaceListResponse",
        "PersonOperationSummary",
        "PersonOperationListResponse",
    ):
        assert schemas[name]["additionalProperties"] is False, name
        for key in schemas[name].get("properties", {}):
            for fragment in (
                "bbox",
                "landmark",
                "vector",
                "confidence",
                "name_key",
                "similarity",
                "snapshot",
            ):
                assert fragment not in key.casefold(), (name, key)

    # The read endpoints declare exactly the query surface the page uses.
    person_params = {
        item["name"] for item in paths["/api/v1/persons"]["get"]["parameters"]
    }
    face_params = {item["name"] for item in paths["/api/v1/faces"]["get"]["parameters"]}
    operation_params = {
        item["name"] for item in paths["/api/v1/person-operations"]["get"]["parameters"]
    }
    assert person_params == {"kind", "limit", "cursor"}
    assert face_params == {"person_id", "assignment_state", "limit", "cursor"}
    assert operation_params == {"limit", "cursor"}
    # The face view is a closed vocabulary, not a free string.
    state_schema = next(
        item
        for item in paths["/api/v1/faces"]["get"]["parameters"]
        if item["name"] == "assignment_state"
    )
    # The one allowed value is declared, not described: a single-value ``Literal`` becomes a
    # ``const`` (older encoders emit a one-element ``enum``), and ``null`` is what makes it
    # optional.
    single = state_schema["schema"]["anyOf"][0]
    assert single.get("const") == "manual_unassigned" or single.get("enum") == [
        "manual_unassigned"
    ]
    assert state_schema["schema"]["anyOf"][1]["type"] == "null"
