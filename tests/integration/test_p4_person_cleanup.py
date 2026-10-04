"""Cleanup reflects effective face ownership without deleting undoable identities."""

from __future__ import annotations

import pytest
from conftest import ApiEnv
from p3_persons_page_fixtures import add_link, add_person, seed_library, set_control, set_exclusion


@pytest.mark.parametrize("action", ["assign", "merge", "label"])
def test_completed_group_leaves_cleanup_and_undo_restores(api_env: ApiEnv, action: str) -> None:
    photos = seed_library(api_env, {"合照.jpg": [(20, 20, 90, 90), (150, 30, 220, 100)]})
    first, neighbour = photos["合照.jpg"]["faces"]
    catalog = api_env.service.catalog
    source = add_person(catalog)
    target = add_person(catalog, name="爸爸")
    add_link(catalog, first, source)
    add_link(catalog, neighbour, target)

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)

        async def unnamed():
            response = await client.get("/api/v1/persons/browse", params={"kind": "unnamed"})
            assert response.status_code == 200
            return [p["person_id"] for p in response.json()["persons"]]

        assert await unnamed() == [source]
        if action == "assign":
            response = await client.put(
                f"/api/v1/faces/{first}/assignment",
                headers=headers,
                json={
                    "contract_version": "p3-face-correction-contracts.v1",
                    "request_id": "cleanup-assign",
                    "action": "assign",
                    "expected_revision": 0,
                    "target_person_id": target,
                },
            )
        elif action == "merge":
            response = await client.post(
                "/api/v1/persons/merge",
                headers=headers,
                json={
                    "request_id": "cleanup-merge",
                    "target_person_id": target,
                    "target_expected_revision": 1,
                    "sources": [{"person_id": source, "expected_revision": 1}],
                },
            )
        else:
            response = await client.put(
                f"/api/v1/persons/{source}/label",
                headers=headers,
                json={"request_id": "cleanup-label", "name": "妈妈", "expected_revision": 1},
            )
        assert response.status_code == 200
        operation = response.json()
        before_read = tuple(catalog.connection.iterdump())
        assert await unnamed() == []
        assert (await client.get("/api/v1/faces/inbox")).json()["faces"] == []
        assert tuple(catalog.connection.iterdump()) == before_read
        assert catalog.read_one("SELECT person_id FROM persons WHERE person_id = ?", (source,))
        owner = source if action == "label" else target
        album = (await client.get(f"/api/v1/persons/{owner}/photos")).json()
        assert len(album["assets"]) == 1
        assert first in {f["face_id"] for f in album["assets"][0]["person_faces"]}
        undo = await client.post(
            f"/api/v1/person-operations/{operation['operation_id']}/undo",
            headers=headers,
            json={
                "request_id": "cleanup-undo",
                "expected_operation_revision": operation["operation_revision"],
            },
        )
        assert undo.status_code == 200
        assert await unnamed() == [source]
        album = (await client.get(f"/api/v1/persons/{source}/photos")).json()
        assert [f["face_id"] for f in album["assets"][0]["person_faces"]] == [first]

    api_env.scenario(scenario)


def test_other_unnamed_face_on_corrected_photo_stays_in_cleanup(api_env: ApiEnv) -> None:
    photos = seed_library(api_env, {"合照.jpg": [(20, 20, 90, 90), (150, 30, 220, 100)]})
    first, neighbour = photos["合照.jpg"]["faces"]
    catalog = api_env.service.catalog
    source = add_person(catalog)
    target = add_person(catalog, name="爸爸")
    add_link(catalog, first, source)
    add_link(catalog, neighbour, source)

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        response = await client.put(
            f"/api/v1/faces/{first}/assignment",
            headers=api_env.post_headers(csrf),
            json={
                "contract_version": "p3-face-correction-contracts.v1",
                "request_id": "cleanup-one-of-two",
                "action": "assign",
                "expected_revision": 0,
                "target_person_id": target,
            },
        )
        assert response.status_code == 200
        page = (await client.get("/api/v1/persons/browse", params={"kind": "unnamed"})).json()
        assert [p["person_id"] for p in page["persons"]] == [source]
        assert page["persons"][0]["avatar_face_id"] == neighbour
        for person, face in ((source, neighbour), (target, first)):
            album = (await client.get(f"/api/v1/persons/{person}/photos")).json()
            assert [a["asset_id"] for a in album["assets"]] == [photos["合照.jpg"]["asset_id"]]
            assert [f["face_id"] for f in album["assets"][0]["person_faces"]] == [face]

    api_env.scenario(scenario)


def test_empty_or_ineffective_groups_are_filtered_before_paging(api_env: ApiEnv) -> None:
    photos = seed_library(
        api_env,
        {
            "合照.jpg": [(20 + 35 * i, 20, 45 + 35 * i, 50) for i in range(5)],
        },
    )
    catalog = api_env.service.catalog
    empty_named = add_person(catalog, name="叔叔")
    empty = add_person(catalog)
    suggested, locked, excluded, first, last = photos["合照.jpg"]["faces"]
    suggestion_person = add_person(catalog)
    add_link(catalog, suggested, suggestion_person, decision="SUGGESTED")
    locked_person = add_person(catalog)
    add_link(catalog, locked, locked_person)
    set_control(catalog, locked, "MANUAL_UNASSIGNED")
    excluded_person = add_person(catalog)
    add_link(catalog, excluded, excluded_person)
    set_exclusion(catalog, excluded, excluded_person)
    first_person = add_person(catalog)
    add_link(catalog, first, first_person)
    add_person(catalog)  # An empty identity between two real pages.
    last_person = add_person(catalog)
    add_link(catalog, last, last_person)
    add_person(catalog)  # Empty tail must not produce a spurious next page.

    async def scenario(client):
        await api_env.authenticate(client)
        before = tuple(catalog.connection.iterdump())
        first_page = (
            await client.get("/api/v1/persons/browse", params={"kind": "unnamed", "limit": 1})
        ).json()
        assert [p["person_id"] for p in first_page["persons"]] == [first_person]
        assert first_page["has_more"]
        last_page = (
            await client.get(
                "/api/v1/persons/browse",
                params={"kind": "unnamed", "limit": 1, "cursor": first_page["next_cursor"]},
            )
        ).json()
        assert [p["person_id"] for p in last_page["persons"]] == [last_person]
        assert not last_page["has_more"] and last_page["next_cursor"] is None
        named = (await client.get("/api/v1/persons/browse", params={"kind": "named"})).json()
        assert [p["person_id"] for p in named["persons"]] == [empty_named]
        all_people = (await client.get("/api/v1/persons/browse")).json()
        assert empty in {p["person_id"] for p in all_people["persons"]}
        assert tuple(catalog.connection.iterdump()) == before

    api_env.scenario(scenario)
