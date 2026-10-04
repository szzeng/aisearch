"""Small synthetic cover selection through the real protected API."""

from __future__ import annotations

import pytest
from conftest import ApiEnv
from p3_persons_page_fixtures import add_link, add_person, seed_library, set_control, set_exclusion


def seed_cover(env: ApiEnv) -> dict:
    photo = seed_library(
        env, {"合照.jpg": [(20, 20, 80, 90), (100, 20, 160, 90), (200, 20, 260, 90)]}
    )["合照.jpg"]
    first, chosen, other = photo["faces"]
    catalog = env.service.catalog
    person = add_person(catalog, name="爸爸", avatar_face_id=first)
    neighbour = add_person(catalog, name="妈妈", avatar_face_id=other)
    add_link(catalog, first, person)
    add_link(catalog, chosen, person)
    add_link(catalog, other, neighbour)
    return dict(person=person, neighbour=neighbour, first=first, chosen=chosen, other=other)


def test_cover_protected_persisted_and_only_display_metadata_changes(api_env: ApiEnv) -> None:
    data = seed_cover(api_env)
    catalog = api_env.service.catalog

    async def scenario(client):
        url = f"/api/v1/persons/{data['person']}/cover"
        body = {"face_id": data["chosen"], "expected_revision": 1}
        assert (await client.put(url, json=body)).status_code == 401
        csrf = await api_env.authenticate(client)
        assert (await client.put(url, json=body)).status_code == 403
        before = tuple(catalog.connection.iterdump())
        response = await client.put(url, json=body, headers=api_env.post_headers(csrf))
        assert response.status_code == 200
        result = response.json()
        assert result["changed"]
        assert result["person"]["avatar_face_id"] == data["chosen"]
        assert result["person"]["revision"] == 2
        after = tuple(catalog.connection.iterdump())
        changed_statements = set(before).symmetric_difference(after)
        assert len(changed_statements) == 2
        assert all(s.startswith('INSERT INTO "persons"') for s in changed_statements)
        assert (
            catalog.read_one(
                "SELECT avatar_face_id FROM persons WHERE person_id = ?", (data["person"],)
            )["avatar_face_id"]
            == data["chosen"]
        )
        # Both independent HTTP reads select the saved cover, rather than the older/larger face.
        people = (await client.get("/api/v1/persons/browse", params={"kind": "named"})).json()
        card = next(p for p in people["persons"] if p["person_id"] == data["person"])
        assert card["thumbnail_url"] == f"/api/v1/faces/{data['chosen']}/thumbnail"
        album = (await client.get(f"/api/v1/persons/{data['person']}/photos")).json()
        assert album["person"]["avatar_face_id"] == data["chosen"]
        assert len(album["assets"]) == 1 and album["person"]["face_count"] == 2
        # Saving the current choice with a current revision is a true no-op.
        same = await client.put(
            url, json={**body, "expected_revision": 2}, headers=api_env.post_headers(csrf)
        )
        assert same.status_code == 200 and not same.json()["changed"]
        assert tuple(catalog.connection.iterdump()) == after

    api_env.scenario(scenario)


@pytest.mark.parametrize(
    "invalid", ["other", "missing", "suggested", "locked", "excluded", "stale"]
)
def test_cover_rejects_invalid_choice_or_revision_without_writes(api_env: ApiEnv, invalid: str):
    data = seed_cover(api_env)
    catalog = api_env.service.catalog
    face = data["chosen"]
    if invalid == "other":
        face = data["other"]
    elif invalid == "missing":
        face = "f" * 64
    elif invalid == "suggested":
        with catalog.write() as connection:
            connection.execute(
                "UPDATE face_person_links SET decision = 'SUGGESTED', reason = 'BORDERLINE' "
                "WHERE face_id = ?",
                (face,),
            )
    elif invalid == "locked":
        set_control(catalog, face, "MANUAL_UNASSIGNED")
    elif invalid == "excluded":
        set_exclusion(catalog, face, data["person"])

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        before = tuple(catalog.connection.iterdump())
        response = await client.put(
            f"/api/v1/persons/{data['person']}/cover",
            json={"face_id": face, "expected_revision": 2 if invalid == "stale" else 1},
            headers=api_env.post_headers(csrf),
        )
        assert response.status_code == (409 if invalid == "stale" else 422)
        assert tuple(catalog.connection.iterdump()) == before

    api_env.scenario(scenario)


def test_cover_uses_canonical_person_and_falls_back_after_face_is_removed(api_env: ApiEnv):
    data = seed_cover(api_env)
    source = add_person(api_env.service.catalog)

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        merge = await client.post(
            "/api/v1/persons/merge",
            headers=headers,
            json={
                "request_id": "cover-merge",
                "target_person_id": data["person"],
                "target_expected_revision": 1,
                "sources": [{"person_id": source, "expected_revision": 1}],
            },
        )
        assert merge.status_code == 200
        person = (await client.get(f"/api/v1/persons/{source}")).json()
        cover = await client.put(
            f"/api/v1/persons/{source}/cover",
            headers=headers,
            json={"face_id": data["chosen"], "expected_revision": person["revision"]},
        )
        assert cover.status_code == 200 and cover.json()["person"]["person_id"] == data["person"]
        set_control(api_env.service.catalog, data["chosen"], "MANUAL_UNASSIGNED")
        album = (await client.get(f"/api/v1/persons/{source}/photos")).json()
        assert album["person"]["avatar_face_id"] == data["first"]

    api_env.scenario(scenario)


def test_previous_undo_cannot_overwrite_later_cover_choice(api_env: ApiEnv):
    data = seed_cover(api_env)

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        renamed = await client.put(
            f"/api/v1/persons/{data['person']}/label",
            headers=headers,
            json={"request_id": "cover-before-name", "name": "老爸", "expected_revision": 1},
        )
        assert renamed.status_code == 200
        cover = await client.put(
            f"/api/v1/persons/{data['person']}/cover",
            headers=headers,
            json={"face_id": data["chosen"], "expected_revision": 2},
        )
        assert cover.status_code == 200
        before = tuple(api_env.service.catalog.connection.iterdump())
        operation = renamed.json()
        undo = await client.post(
            f"/api/v1/person-operations/{operation['operation_id']}/undo",
            headers=headers,
            json={
                "request_id": "cover-later-undo",
                "expected_operation_revision": operation["operation_revision"],
            },
        )
        assert undo.status_code == 409
        assert tuple(api_env.service.catalog.connection.iterdump()) == before

    api_env.scenario(scenario)
