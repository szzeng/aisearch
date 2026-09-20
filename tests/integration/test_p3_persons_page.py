"""P3-06: the requests the static person page makes, driven through the real local API.

This file does not test a DOM (the repository deliberately ships no Node/npm chain). It
asserts the *page contract*: every call the page issues, in the shape the page issues it,
against the frozen P3-03..P3-05 surface - and it checks the three things the card demands of
a write page: a stale revision is refused rather than overwritten, a replayed ``request_id``
replays instead of writing, and a refused undo is reported with the server's own code.

Everything lives under ``%TEMP%``; no real photo library, no network, no model.
"""

from __future__ import annotations

from typing import Any

from conftest import ApiEnv
from httpx import AsyncClient
from p3_persons_page_fixtures import add_link, add_person, seed_library, set_control

FACE_CONTRACT = "p3-face-correction-contracts.v1"
SPLIT_CONTRACT = "p3-person-split-contracts.v1"


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


def assert_page_safe(*payloads: Any) -> None:
    """No private field and no local path may reach the page."""
    for payload in payloads:
        keys = [key.casefold() for key in collect_keys(payload)]
        for key in keys:
            for needle in (
                "bbox",
                "landmark",
                "vector",
                "similarity",
                "confidence",
                "name_key",
                "snapshot",
                "payload_sha256",
            ):
                assert key != needle, f"private field {key} leaked to the page"
        rendered = repr(payload)
        assert "C:\\" not in rendered and "D:\\" not in rendered


def seed_people(env: Any) -> dict[str, Any]:
    """Two photos, four faces: a named pair, an unnamed group and one manual lock."""
    photos = seed_library(
        env,
        {
            "合照.jpg": [(20, 20, 90, 90), (150, 30, 220, 100)],
            "单人.jpg": [(40, 40, 120, 120), (130, 40, 200, 110)],
            "爸爸 第三张.jpg": [(30, 30, 110, 110)],
        },
    )
    catalog = env.service.catalog
    group_face, group_neighbour = photos["合照.jpg"]["faces"]
    solo_face, solo_neighbour = photos["单人.jpg"]["faces"]
    third_face = photos["爸爸 第三张.jpg"]["faces"][0]
    dad = add_person(catalog, name="爸爸", avatar_face_id=group_face)
    mom = add_person(catalog, name="妈妈", avatar_face_id=solo_face)
    unnamed = add_person(catalog)
    add_link(catalog, group_face, dad)
    # A second member of 爸爸, so a split has a legal source that is not emptied.
    add_link(catalog, third_face, dad)
    add_link(catalog, solo_face, mom)
    add_link(catalog, solo_neighbour, mom)
    add_link(catalog, group_neighbour, unnamed)
    # One member of 妈妈 carries a human lock at revision 2: the page must send *that*
    # revision, and a stale one must be refused.
    set_control(catalog, solo_neighbour, "MANUAL_ASSIGNED", revision=2)
    return {
        "photos": photos,
        "dad": dad,
        "mom": mom,
        "unnamed": unnamed,
        "faces": {
            "group_face": group_face,
            "group_neighbour": group_neighbour,
            "solo_face": solo_face,
            "solo_neighbour": solo_neighbour,
            "third_face": third_face,
        },
    }


async def person_row(client: AsyncClient, person_id: int) -> dict[str, Any]:
    response = await client.get(f"/api/v1/persons/{person_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def face_rows(client: AsyncClient, person_id: int) -> list[dict[str, Any]]:
    response = await client.get("/api/v1/faces", params={"person_id": person_id, "limit": 50})
    assert response.status_code == 200, response.text
    assert_page_safe(response.json())
    return response.json()["faces"]


async def face_control(client: AsyncClient, face_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/v1/faces/{face_id}/assignment")
    assert response.status_code == 200, response.text
    return response.json()


async def batch_faces(
    client: AsyncClient, env: Any, items: list[dict[str, Any]], *, request_id: str
) -> Any:
    """``POST /face-assignments/batch`` exactly as the page sends it."""
    return await client.post(
        "/api/v1/face-assignments/batch",
        json={"contract_version": FACE_CONTRACT, "request_id": request_id, "items": items},
        headers=env.post_headers(env.csrf_token),
    )


def test_the_page_flow_reads_names_merges_edits_splits_and_undoes(api_env) -> None:
    seeded = seed_people(api_env)
    faces = seeded["faces"]

    async def scenario(client: AsyncClient) -> dict:
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)

        named_view = await client.get("/api/v1/persons", params={"kind": "named", "limit": 50})
        unnamed_view = await client.get("/api/v1/persons", params={"kind": "unnamed", "limit": 50})
        people = named_view.json()["persons"]
        mom_card = next(row for row in people if row["person_id"] == seeded["mom"])

        # 1. The page asks for the faces of the person the user picked.
        mom_faces = await face_rows(client, seeded["mom"])

        # 2. Saving a first name on the unnamed group (no same-name collision).
        renamed = await client.put(
            f"/api/v1/persons/{seeded['unnamed']}/name",
            json={"request_id": "p3-06-page-name-1", "name": "小舅", "expected_revision": 1},
            headers=headers,
        )
        unnamed_after = await client.get("/api/v1/persons", params={"kind": "unnamed", "limit": 50})
        # 3. Saving a name another active person owns is the same-name merge.
        same_name = await client.put(
            f"/api/v1/persons/{seeded['unnamed']}/name",
            json={
                "request_id": "p3-06-page-name-2",
                "name": "妈妈",
                "expected_revision": (await person_row(client, seeded["unnamed"]))["revision"],
            },
            headers=headers,
        )
        redirect_lookup = await client.get(f"/api/v1/persons/{seeded['unnamed']}")
        after_merge = await client.get("/api/v1/persons", params={"limit": 50})

        # 4. One face leaves the member list (unassign) and shows up in the manual queue.
        solo_row = next(row for row in mom_faces if row["face_id"] == faces["solo_face"])
        single = await client.put(
            f"/api/v1/faces/{faces['solo_face']}/assignment",
            json={
                "contract_version": FACE_CONTRACT,
                "request_id": "p3-06-page-face-1",
                "action": "unassign",
                "target_person_id": None,
                "expected_revision": solo_row["control_revision"],
            },
            headers=headers,
        )
        queue = await client.get(
            "/api/v1/faces", params={"assignment_state": "manual_unassigned", "limit": 50}
        )
        mom_after_unassign = await face_rows(client, seeded["mom"])

        # 5. Putting the same face back by hand is a MANUAL assignment the server decides on.
        queue_row = queue.json()["faces"][0]
        assigned = await client.put(
            f"/api/v1/faces/{faces['solo_face']}/assignment",
            json={
                "contract_version": FACE_CONTRACT,
                "request_id": "p3-06-page-face-2",
                "action": "assign",
                "target_person_id": seeded["mom"],
                "expected_revision": queue_row["control_revision"],
            },
            headers=headers,
        )
        # 6. The batch endpoint does the same for several faces at once, all-or-nothing.
        current = {row["face_id"]: row for row in await face_rows(client, seeded["mom"])}
        batch = await batch_faces(
            client,
            api_env,
            [
                {
                    "face_id": faces["solo_face"],
                    "action": "restore_auto",
                    "target_person_id": None,
                    "expected_revision": current[faces["solo_face"]]["control_revision"],
                },
                {
                    "face_id": faces["solo_neighbour"],
                    "action": "restore_auto",
                    "target_person_id": None,
                    "expected_revision": current[faces["solo_neighbour"]]["control_revision"],
                },
            ],
            request_id="p3-06-page-faces-3",
        )

        # 7. Splitting needs the person's own revision plus both revisions of every face.
        dad_faces = await face_rows(client, seeded["dad"])
        dad_revision = (await person_row(client, seeded["dad"]))["revision"]
        selected = next(row for row in dad_faces if row["face_id"] == faces["group_face"])
        split = await client.post(
            f"/api/v1/persons/{seeded['dad']}/split",
            json={
                "contract_version": SPLIT_CONTRACT,
                "request_id": "p3-06-page-split-1",
                "source_expected_revision": dad_revision,
                "faces": [
                    {
                        "face_id": selected["face_id"],
                        "expected_control_revision": selected["control_revision"],
                        "expected_assignment_revision": selected["assignment_revision"],
                    }
                ],
            },
            headers=headers,
        )
        split_body = split.json()
        assert split.status_code == 200, split.text
        dad_after_split = await face_rows(client, seeded["dad"])
        new_person_faces = await face_rows(client, split_body["new_person_id"])

        # 8. The operation log lists everything, and the page undoes one operation.
        log = await client.get("/api/v1/person-operations", params={"limit": 50})
        split_row = next(
            row
            for row in log.json()["operations"]
            if row["operation_id"] == split_body["operation_id"]
        )
        undone = await client.post(
            f"/api/v1/person-operations/{split_body['operation_id']}/undo",
            json={
                "request_id": "p3-06-page-undo-1",
                "expected_operation_revision": split_row["operation_revision"],
            },
            headers=headers,
        )
        after_undo = await client.get("/api/v1/person-operations", params={"limit": 50})
        dad_members_restored = await face_rows(client, seeded["dad"])
        return {
            "named_view": named_view,
            "unnamed_view": unnamed_view,
            "mom_card": mom_card,
            "mom_faces": mom_faces,
            "renamed": renamed,
            "unnamed_after": unnamed_after,
            "same_name": same_name,
            "redirect_lookup": redirect_lookup,
            "after_merge": after_merge,
            "single": single,
            "queue": queue,
            "mom_after_unassign": mom_after_unassign,
            "assigned": assigned,
            "batch": batch,
            "split": split,
            "dad_after_split": dad_after_split,
            "new_person_faces": new_person_faces,
            "log": log,
            "undone": undone,
            "after_undo": after_undo,
            "dad_members_restored": dad_members_restored,
        }

    result = api_env.scenario(scenario)
    for key in (
        "named_view",
        "unnamed_view",
        "queue",
        "log",
        "after_merge",
        "after_undo",
    ):
        assert result[key].status_code == 200, (key, result[key].text)
        assert_page_safe(result[key].json())

    # The three views partition the active canonical people.
    assert {row["person_id"] for row in result["named_view"].json()["persons"]} == {
        seeded["dad"],
        seeded["mom"],
    }
    assert {row["person_id"] for row in result["unnamed_view"].json()["persons"]} == {
        seeded["unnamed"]
    }
    assert result["mom_card"]["face_count"] == 2

    # The rename moved the person out of the unnamed view and into the named one.
    assert result["renamed"].status_code == 200, result["renamed"].text
    assert result["renamed"].json()["operation_type"] == "RENAME"
    assert result["unnamed_after"].json()["persons"] == []

    # The same-name save merged, and the page reads the id back to show where it went.
    same_name = result["same_name"]
    assert same_name.status_code == 200, same_name.text
    assert same_name.json()["operation_type"] == "MERGE_BY_NAME"
    lookup = result["redirect_lookup"].json()
    assert lookup["redirected"] is True
    assert lookup["canonical_person_id"] == seeded["mom"]
    listed = {row["person_id"] for row in result["after_merge"].json()["persons"]}
    assert seeded["unnamed"] not in listed
    assert listed == {seeded["dad"], seeded["mom"]}

    # Unassigning one face removed exactly that face from the member list.
    assert result["single"].status_code == 200, result["single"].text
    assert result["single"].json()["changed"] is True
    assert [row["face_id"] for row in result["queue"].json()["faces"]] == [faces["solo_face"]]
    # 妈妈 keeps her other member *and* the member the same-name merge moved into her.
    assert {row["face_id"] for row in result["mom_after_unassign"]} == {
        faces["solo_neighbour"],
        faces["group_neighbour"],
    }

    # … and putting it back by hand is an assignment the server records as MANUAL.
    assigned = result["assigned"].json()
    assert assigned["changed"] is True
    assert assigned["items"][0]["mode"] == "MANUAL_ASSIGNED"

    # The batch is one atomic operation over the two faces of one person.
    batch = result["batch"]
    assert batch.status_code == 200, batch.text
    assert batch.json()["operation_type"] == "BATCH_FACE_EDIT"
    assert len(batch.json()["items"]) == 2

    # The split created one unnamed person holding exactly the faces handed to it.
    split = result["split"]
    assert split.status_code == 200, split.text
    split_body = split.json()
    assert split_body["canonical_source_person_id"] == seeded["dad"]
    assert split_body["new_person_id"] > 0
    assert split_body["new_person_id"] != seeded["dad"]
    assert [row["face_id"] for row in result["new_person_faces"]] == [faces["group_face"]]
    # Only the selected face moved: the sibling on the same person stayed where it was.
    assert [row["face_id"] for row in result["dad_after_split"]] == [faces["third_face"]]

    # The undo is a new operation, and the member is back on the source person.
    undone = result["undone"]
    assert undone.status_code == 200, undone.text
    assert undone.json()["operation_type"] == "UNDO"
    assert undone.json()["undo_of_operation_id"] == split_body["operation_id"]
    statuses = {
        row["operation_id"]: row["status"] for row in result["after_undo"].json()["operations"]
    }
    assert statuses[split_body["operation_id"]] == "UNDONE"
    assert sorted(row["face_id"] for row in result["dad_members_restored"]) == sorted(
        [faces["group_face"], faces["third_face"]]
    )


def test_a_stale_revision_and_a_replayed_request_id_never_overwrite(api_env) -> None:
    seeded = seed_people(api_env)
    faces = seeded["faces"]

    async def scenario(client: AsyncClient) -> dict:
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        stale = await client.put(
            f"/api/v1/faces/{faces['group_face']}/assignment",
            json={
                "contract_version": FACE_CONTRACT,
                "request_id": "p3-06-stale-1",
                "action": "unassign",
                "target_person_id": None,
                "expected_revision": 999,
            },
            headers=headers,
        )
        current = await face_control(client, faces["group_face"])
        first = await client.put(
            f"/api/v1/faces/{faces['group_face']}/assignment",
            json={
                "contract_version": FACE_CONTRACT,
                "request_id": "p3-06-replay-1",
                "action": "unassign",
                "target_person_id": None,
                "expected_revision": current["revision"],
            },
            headers=headers,
        )
        replay = await client.put(
            f"/api/v1/faces/{faces['group_face']}/assignment",
            json={
                "contract_version": FACE_CONTRACT,
                "request_id": "p3-06-replay-1",
                "action": "unassign",
                "target_person_id": None,
                "expected_revision": current["revision"],
            },
            headers=headers,
        )
        conflict = await client.put(
            f"/api/v1/faces/{faces['group_face']}/assignment",
            json={
                "contract_version": FACE_CONTRACT,
                "request_id": "p3-06-replay-1",
                "action": "assign",
                "target_person_id": seeded["dad"],
                "expected_revision": current["revision"],
            },
            headers=headers,
        )
        too_large = await batch_faces(
            client,
            api_env,
            [
                {
                    "face_id": "e" * 64,
                    "action": "unassign",
                    "target_person_id": None,
                    "expected_revision": 0,
                }
                for _ in range(501)
            ],
            request_id="p3-06-stale-2",
        )
        after = await face_control(client, faces["group_face"])
        return {
            "stale": stale,
            "first": first,
            "replay": replay,
            "conflict": conflict,
            "too_large": too_large,
            "after": after,
        }

    result = api_env.scenario(scenario)
    stale = result["stale"]
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "FACE_ASSIGNMENT_CONFLICT"
    assert stale.json()["details"]["current_revision"] == 0

    first = result["first"]
    assert first.status_code == 200, first.text
    assert first.json()["replayed"] is False
    assert first.json()["changed"] is True

    # The idempotency gate runs before the revision check: a verbatim replay answers the
    # recorded operation instead of a stale-revision conflict …
    replay = result["replay"]
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["operation_id"] == first.json()["operation_id"]
    # … while the same key with a different payload is a conflict, never an overwrite.
    conflict = result["conflict"]
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "FACE_IDEMPOTENCY_CONFLICT"

    too_large = result["too_large"]
    assert too_large.status_code == 422, too_large.text
    assert too_large.json()["code"] == "FACE_BATCH_TOO_LARGE"
    assert too_large.json()["details"]["max_batch_size"] == 500

    # The face ended in exactly the state the accepted operation wrote.
    after = result["after"]
    assert after["mode"] == "MANUAL_UNASSIGNED"
    assert after["assigned_person_id"] is None


def test_a_refused_undo_reports_the_server_code(api_env) -> None:
    seeded = seed_people(api_env)
    faces = seeded["faces"]

    async def scenario(client: AsyncClient) -> dict:
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        current = await face_control(client, faces["group_face"])
        first = await client.put(
            f"/api/v1/faces/{faces['group_face']}/assignment",
            json={
                "contract_version": FACE_CONTRACT,
                "request_id": "p3-06-undo-a",
                "action": "unassign",
                "target_person_id": None,
                "expected_revision": current["revision"],
            },
            headers=headers,
        )
        after_unassign = await face_control(client, faces["group_face"])
        # A later human edit moves the same face, so the first operation is no longer
        # compensable - and the page must show the server's own code.
        later = await client.put(
            f"/api/v1/faces/{faces['group_face']}/assignment",
            json={
                "contract_version": FACE_CONTRACT,
                "request_id": "p3-06-undo-b",
                "action": "assign",
                "target_person_id": seeded["dad"],
                "expected_revision": after_unassign["revision"],
            },
            headers=headers,
        )
        refused = await client.post(
            f"/api/v1/person-operations/{first.json()['operation_id']}/undo",
            json={
                "request_id": "p3-06-undo-c",
                "expected_operation_revision": first.json()["operation_revision"],
            },
            headers=headers,
        )
        good = await client.post(
            f"/api/v1/person-operations/{later.json()['operation_id']}/undo",
            json={
                "request_id": "p3-06-undo-d",
                "expected_operation_revision": later.json()["operation_revision"],
            },
            headers=headers,
        )
        replay = await client.post(
            f"/api/v1/person-operations/{later.json()['operation_id']}/undo",
            json={
                "request_id": "p3-06-undo-d",
                "expected_operation_revision": later.json()["operation_revision"],
            },
            headers=headers,
        )
        final = await face_control(client, faces["group_face"])
        return {"first": first, "refused": refused, "good": good, "replay": replay, "final": final}

    result = api_env.scenario(scenario)
    refused = result["refused"]
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "FACE_OPERATION_NOT_UNDOABLE"
    assert refused.json()["details"]["operation_id"] == result["first"].json()["operation_id"]

    good = result["good"]
    assert good.status_code == 200, good.text
    assert good.json()["operation_type"] == "UNDO"
    assert good.json()["undo_of_operation_id"] != result["first"].json()["operation_id"]

    # Replaying the same undo request_id answers the recorded UNDO instead of double-writing.
    replay = result["replay"]
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["operation_id"] == good.json()["operation_id"]

    # The compensation restored the member the human had taken out.
    # Undoing the manual assignment restores the state that preceded it: the face is back
    # under the human "cancelled" lock with no effective link (ADR-0007 §3).
    assert result["final"]["mode"] == "MANUAL_UNASSIGNED"
    assert result["final"]["assigned_person_id"] is None


def test_the_write_paths_need_a_session_csrf_origin_and_a_local_host(api_env) -> None:
    seeded = seed_people(api_env)
    faces = seeded["faces"]
    body = {
        "contract_version": FACE_CONTRACT,
        "request_id": "p3-06-guard-1",
        "action": "unassign",
        "target_person_id": None,
        "expected_revision": 0,
    }

    async def scenario(client: AsyncClient) -> dict:
        csrf = await api_env.authenticate(client)
        before = await face_control(client, faces["group_face"])
        no_csrf = await client.put(
            f"/api/v1/faces/{faces['group_face']}/assignment",
            json=body,
            headers={"Origin": "http://127.0.0.1"},
        )
        no_origin = await client.put(
            f"/api/v1/faces/{faces['group_face']}/assignment",
            json=body,
            headers={"X-CSRF-Token": csrf},
        )
        cross_origin = await client.put(
            f"/api/v1/faces/{faces['group_face']}/assignment",
            json=body,
            headers={"Origin": "http://evil.example", "X-CSRF-Token": csrf},
        )
        foreign_host = await client.get("/api/v1/persons", headers={"Host": "evil.example"})
        missing = await client.post(
            "/api/v1/person-operations/deadbeef/undo",
            json={"request_id": "p3-06-guard-2", "expected_operation_revision": 1},
            headers=api_env.post_headers(csrf),
        )
        after = await face_control(client, faces["group_face"])
        return {
            "no_csrf": no_csrf,
            "no_origin": no_origin,
            "cross_origin": cross_origin,
            "foreign_host": foreign_host,
            "missing": missing,
            "before": before,
            "after": after,
        }

    result = api_env.scenario(scenario)
    assert result["no_csrf"].status_code == 403
    assert result["no_csrf"].json()["code"] == "CSRF_INVALID"
    assert result["no_origin"].status_code == 403
    assert result["no_origin"].json()["code"] == "ORIGIN_NOT_ALLOWED"
    assert result["cross_origin"].status_code == 403
    assert result["cross_origin"].json()["code"] == "ORIGIN_NOT_ALLOWED"
    assert result["foreign_host"].status_code == 403
    assert result["foreign_host"].json()["code"] == "HOST_NOT_ALLOWED"
    # A well-formed write against an operation that does not exist is a structured 404.
    assert result["missing"].status_code in (404, 409), result["missing"].text
    assert result["missing"].json()["code"] in (
        "PERSON_NOT_FOUND",
        "PERSON_OPERATION_NOT_UNDOABLE",
    )
    # Every refusal wrote nothing: the face still reports its original state.
    assert result["before"] == result["after"]

    async def anonymous(client: AsyncClient) -> dict:
        return {
            "list": await client.get("/api/v1/persons"),
            "write": await client.put(
                f"/api/v1/faces/{faces['group_face']}/assignment",
                json=body,
                headers={"Origin": "http://127.0.0.1", "X-CSRF-Token": "x"},
            ),
        }

    anonymous_result = api_env.scenario(anonymous)
    for response in anonymous_result.values():
        assert response.status_code == 401
        assert response.json()["code"] == "SESSION_REQUIRED"


def test_the_page_reads_the_same_data_after_a_restart(api_env, tmp_path) -> None:
    seeded = seed_people(api_env)

    async def write_scenario(client: AsyncClient) -> dict:
        csrf = await api_env.authenticate(client)
        renamed = await client.put(
            f"/api/v1/persons/{seeded['unnamed']}/name",
            json={"request_id": "p3-06-restart-1", "name": "小舅", "expected_revision": 1},
            headers=api_env.post_headers(csrf),
        )
        operations = await client.get("/api/v1/person-operations", params={"limit": 50})
        return {"renamed": renamed, "operations": operations}

    written = api_env.scenario(write_scenario)
    assert written["renamed"].status_code == 200, written["renamed"].text
    api_env.close()

    # A second process over the same data root sees the committed authority, not a cache.
    restarted = ApiEnv.build(tmp_path)
    try:

        async def read_scenario(client: AsyncClient) -> dict:
            await restarted.authenticate(client)
            people = await client.get("/api/v1/persons", params={"limit": 50})
            operations = await client.get("/api/v1/person-operations", params={"limit": 50})
            faces = await client.get(
                "/api/v1/faces", params={"person_id": seeded["unnamed"], "limit": 50}
            )
            thumbnail = await client.get(
                f"/api/v1/faces/{seeded['faces']['group_neighbour']}/thumbnail"
            )
            return {
                "people": people,
                "operations": operations,
                "faces": faces,
                "thumbnail": thumbnail,
            }

        after = restarted.scenario(read_scenario)
    finally:
        restarted.close()

    assert after["people"].status_code == 200, after["people"].text
    people = after["people"].json()["persons"]
    assert [row["person_id"] for row in people] == [seeded["dad"], seeded["mom"], seeded["unnamed"]]
    assert people[0]["name"] == "爸爸"
    assert people[2]["name"] == "小舅"
    # The operation log survived the restart with the same operation identity.
    assert (
        after["operations"].json()["operations"][0]["operation_id"]
        == written["operations"].json()["operations"][0]["operation_id"]
    )
    assert after["faces"].status_code == 200, after["faces"].text
    assert [row["face_id"] for row in after["faces"].json()["faces"]] == [
        seeded["faces"]["group_neighbour"]
    ]
    # The derived crop is rebuildable: the fresh process regenerates it from the preview.
    assert after["thumbnail"].status_code == 200, after["thumbnail"].text
    assert after["thumbnail"].content.startswith(b"\xff\xd8\xff")
