"""Small personal-album loop over synthetic photos and real protected API/transactions."""

from __future__ import annotations

from conftest import ApiEnv
from p3_persons_page_fixtures import add_link, add_person, seed_library, set_control, set_exclusion


def seed(env: ApiEnv) -> dict:
    photos = seed_library(
        env,
        {
            "合照.jpg": [(20, 20, 90, 90), (150, 30, 220, 100)],
            "爸爸.jpg": [(40, 40, 120, 120)],
            "待整理.jpg": [(30, 30, 90, 90), (130, 30, 190, 90)],
        },
    )
    catalog = env.service.catalog
    first, neighbour = photos["合照.jpg"]["faces"]
    dad = add_person(catalog, name="爸爸", avatar_face_id=first)
    mom = add_person(catalog, name="妈妈", avatar_face_id=neighbour)
    unnamed = add_person(catalog)
    add_link(catalog, first, dad)
    add_link(catalog, neighbour, mom)
    add_link(catalog, photos["爸爸.jpg"]["faces"][0], dad)
    pending, removed = photos["待整理.jpg"]["faces"]
    add_link(catalog, pending, mom, decision="SUGGESTED")
    add_link(catalog, removed, dad)
    set_control(catalog, removed, "MANUAL_UNASSIGNED")
    return dict(
        photos=photos,
        dad=dad,
        mom=mom,
        unnamed=unnamed,
        first=first,
        neighbour=neighbour,
        pending=pending,
        removed=removed,
    )


def test_people_query_is_literal_paged_and_session_protected(api_env: ApiEnv) -> None:
    data = seed(api_env)

    async def scenario(client):
        assert (await client.get("/api/v1/persons/browse")).status_code == 401
        await api_env.authenticate(client)
        page = (await client.get("/api/v1/persons/browse", params={"limit": 1})).json()
        assert page["has_more"] and len(page["persons"]) == 1
        next_page = (
            await client.get(
                "/api/v1/persons/browse", params={"limit": 1, "cursor": page["next_cursor"]}
            )
        ).json()
        assert page["persons"][0]["person_id"] != next_page["persons"][0]["person_id"]
        changed = await client.get(
            "/api/v1/persons/browse", params={"q": "妈", "cursor": page["next_cursor"]}
        )
        assert changed.status_code == 409
        matches = (await client.get("/api/v1/persons/browse", params={"q": "妈"})).json()
        assert [p["person_id"] for p in matches["persons"]] == [data["mom"]]
        assert not (await client.get("/api/v1/persons/browse", params={"q": "%"})).json()["persons"]
        assert "bbox" not in repr(matches) and "name_key" not in repr(matches)

    api_env.scenario(scenario)


def test_album_is_complete_deduplicated_and_cursor_bound_to_person(api_env: ApiEnv) -> None:
    data = seed(api_env)
    # A duplicate face of the same person still represents exactly one photo.
    with api_env.service.catalog.write() as connection:
        connection.execute(
            "UPDATE face_person_links SET person_id = ? WHERE face_id = ?",
            (data["dad"], data["neighbour"]),
        )

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        url = f"/api/v1/persons/{data['dad']}/photos"
        page = (await client.get(url, params={"limit": 1})).json()
        assert page["person"]["asset_count"] == 2 and page["has_more"]
        other = await client.get(
            f"/api/v1/persons/{data['mom']}/photos", params={"cursor": page["next_cursor"]}
        )
        assert other.status_code == 409
        second = (await client.get(url, params={"limit": 1, "cursor": page["next_cursor"]})).json()
        assert not second["has_more"]
        ids = {a["asset_id"] for a in page["assets"] + second["assets"]}
        assert ids == {
            data["photos"]["合照.jpg"]["asset_id"],
            data["photos"]["爸爸.jpg"]["asset_id"],
        }
        assert all("current_location" not in a for a in page["assets"])
        assert "taken_time" in page["assets"][0]
        change = await client.put(
            f"/api/v1/persons/{data['dad']}/label",
            headers=api_env.post_headers(csrf),
            json={"request_id": "dad-label", "name": "老爸", "expected_revision": 1},
        )
        assert change.status_code == 200
        stale = await client.get(url, params={"cursor": page["next_cursor"]})
        assert stale.status_code == 409 and stale.json()["code"] == "CATALOG_FILTER_REVISION_STALE"

    api_env.scenario(scenario)


def test_safe_name_collision_writes_nothing_then_explicit_merge_and_undo(api_env: ApiEnv) -> None:
    data = seed(api_env)
    catalog = api_env.service.catalog

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        before = tuple(catalog.connection.iterdump())
        result = await client.put(
            f"/api/v1/persons/{data['unnamed']}/label",
            headers=headers,
            json={"request_id": "name-collision", "name": "爸爸", "expected_revision": 1},
        )
        assert result.status_code == 409 and result.json()["details"]["reason"] == "NAME_IN_USE"
        assert result.json()["details"]["target_person_id"] == data["dad"]
        assert tuple(catalog.connection.iterdump()) == before
        merge = await client.post(
            "/api/v1/persons/merge",
            headers=headers,
            json={
                "request_id": "confirmed-merge",
                "target_person_id": data["dad"],
                "target_expected_revision": 1,
                "sources": [{"person_id": data["unnamed"], "expected_revision": 1}],
            },
        )
        assert merge.status_code == 200
        redirected = (await client.get(f"/api/v1/persons/{data['unnamed']}/photos")).json()
        assert redirected["person"]["person_id"] == data["dad"]
        body = merge.json()
        undo = await client.post(
            f"/api/v1/person-operations/{body['operation_id']}/undo",
            headers=headers,
            json={
                "request_id": "undo-merge",
                "expected_operation_revision": body["operation_revision"],
            },
        )
        assert undo.status_code == 200
        assert (await client.get(f"/api/v1/persons/{data['unnamed']}")).json()["person_id"] == data[
            "unnamed"
        ]

    api_env.scenario(scenario)


def test_name_replay_and_alias_lookup_preserve_legacy_idempotency(api_env: ApiEnv) -> None:
    data = seed(api_env)

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        body = {"request_id": "safe-label", "name": "老爸", "expected_revision": 1}
        url = f"/api/v1/persons/{data['dad']}"
        first = await client.put(url + "/label", headers=headers, json=body)
        assert first.status_code == 200
        replay = await client.put(url + "/label", headers=headers, json=body)
        assert replay.status_code == 200 and replay.json()["replayed"]
        assert replay.json()["operation_id"] == first.json()["operation_id"]
        changed_endpoint = await client.put(url + "/name", headers=headers, json=body)
        assert (
            changed_endpoint.status_code == 409
            and changed_endpoint.json()["code"] == "PERSON_IDEMPOTENCY_CONFLICT"
        )
        legacy = await client.put(
            url + "/name",
            headers=headers,
            json={"request_id": "legacy-merge", "name": "妈妈", "expected_revision": 2},
        )
        assert legacy.status_code == 200 and legacy.json()["operation_type"] == "MERGE_BY_NAME"
        alias = (await client.get("/api/v1/persons/browse", params={"q": "老爸"})).json()
        assert [p["person_id"] for p in alias["persons"]] == [data["mom"]]

    api_env.scenario(scenario)


def test_single_face_correction_leaves_neighbour_alone_and_undo_restores(api_env: ApiEnv) -> None:
    data = seed(api_env)
    catalog = api_env.service.catalog
    neighbour_before = dict(
        catalog.read_one("SELECT * FROM face_person_links WHERE face_id = ?", (data["neighbour"],))
    )

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        asset = data["photos"]["合照.jpg"]["asset_id"]
        faces = (await client.get(f"/api/v1/assets/{asset}/faces")).json()["faces"]
        chosen = next(f for f in faces if f["face_id"] == data["first"])
        assert chosen["assigned_person_id"] == data["dad"] and chosen["control_revision"] == 0
        assert not any(k in repr(faces) for k in ("bbox", "landmarks", "similarity", "name_key"))
        body = {
            "contract_version": "p3-face-correction-contracts.v1",
            "request_id": "face-correct",
            "action": "assign",
            "expected_revision": 0,
            "target_person_id": data["mom"],
        }
        url = f"/api/v1/faces/{data['first']}/assignment"
        changed = await client.put(url, headers=headers, json=body)
        assert changed.status_code == 200
        assert (
            dict(
                catalog.read_one(
                    "SELECT * FROM face_person_links WHERE face_id = ?", (data["neighbour"],)
                )
            )
            == neighbour_before
        )
        assert (await client.put(url, headers=headers, json=body)).json()["replayed"]
        stale = await client.put(
            url, headers=headers, json={**body, "request_id": "stale-correction"}
        )
        assert stale.status_code == 409
        operation = changed.json()
        undone = await client.post(
            f"/api/v1/person-operations/{operation['operation_id']}/undo",
            headers=headers,
            json={
                "request_id": "undo-face",
                "expected_operation_revision": operation["operation_revision"],
            },
        )
        assert undone.status_code == 200
        faces = (await client.get(f"/api/v1/assets/{asset}/faces")).json()["faces"]
        assert (
            next(f for f in faces if f["face_id"] == data["first"])["assigned_person_id"]
            == data["dad"]
        )

    api_env.scenario(scenario)


def test_inbox_includes_machine_suggestions_low_quality_and_retained_v1(api_env: ApiEnv) -> None:
    data = seed(api_env)
    catalog = api_env.service.catalog
    with catalog.write() as connection:
        connection.execute(
            "UPDATE faces SET spec_revision = 'face-buffalo-l-scrfd-arcface-v1', quality_state = 'LOW', quality_reason = 'SMALL' WHERE face_id = ?",
            (data["pending"],),
        )
        # The other face of this photo must also be old so there is no v2 publication yet.
        connection.execute(
            "UPDATE faces SET spec_revision = 'face-buffalo-l-scrfd-arcface-v1' WHERE face_id = ?",
            (data["removed"],),
        )
    set_exclusion(catalog, data["pending"], data["mom"])

    async def scenario(client):
        await api_env.authenticate(client)
        inbox = (await client.get("/api/v1/faces/inbox")).json()
        assert {f["face_id"] for f in inbox["faces"]} == {data["pending"], data["removed"]}
        pending = next(f for f in inbox["faces"] if f["face_id"] == data["pending"])
        assert pending["quality_reason"] == "SMALL" and not pending["suggestions"]
        assert (
            next(f for f in inbox["faces"] if f["face_id"] == data["removed"])["mode"]
            == "MANUAL_UNASSIGNED"
        )

    api_env.scenario(scenario)


def test_missing_avatars_use_own_effective_face_and_shared_photo_identifies_each_person(api_env):
    from integration.test_p3_face_thumbnail import mean_channels, seeded_two_faces

    data = seeded_two_faces(api_env)
    catalog = api_env.service.catalog
    left_person = add_person(catalog)
    right_person = add_person(catalog)
    empty = add_person(catalog, avatar_face_id=data["left"])
    add_link(catalog, data["left"], left_person)
    add_link(catalog, data["right"], right_person)

    async def scenario(client):
        await api_env.authenticate(client)
        before = tuple(catalog.connection.iterdump())
        people = (await client.get("/api/v1/persons/browse", params={"kind": "unnamed"})).json()[
            "persons"
        ]
        cards = {p["person_id"]: p for p in people}
        assert cards[left_person]["avatar_face_id"] == data["left"]
        assert cards[right_person]["avatar_face_id"] == data["right"]
        assert cards[empty]["thumbnail_url"] is None
        left_page = (await client.get(f"/api/v1/persons/{left_person}/photos")).json()
        right_page = (await client.get(f"/api/v1/persons/{right_person}/photos")).json()
        assert [a["asset_id"] for a in left_page["assets"]] == [data["asset_id"]]
        assert [a["asset_id"] for a in right_page["assets"]] == [data["asset_id"]]
        assert left_page["assets"][0]["person_faces"][0]["face_id"] == data["left"]
        assert right_page["assets"][0]["person_faces"][0]["face_id"] == data["right"]
        assert not left_page["assets"][0]["person_faces_has_more"]
        assert tuple(catalog.connection.iterdump()) == before
        assert "bbox" not in repr(left_page) and "vector" not in repr(left_page)
        first = await client.get(cards[left_person]["thumbnail_url"])
        second = await client.get(cards[right_person]["thumbnail_url"])
        assert first.status_code == second.status_code == 200
        left_red, left_blue = mean_channels(first.content)
        right_red, right_blue = mean_channels(second.content)
        assert left_red > left_blue + 100 and right_blue > right_red + 100
        # Removing the old face invalidates a persisted avatar too, without changing the person row.
        with catalog.write() as connection:
            connection.execute(
                "UPDATE persons SET avatar_face_id = ? WHERE person_id = ?",
                (data["left"], left_person),
            )
        set_control(catalog, data["left"], "MANUAL_UNASSIGNED")
        cards = {
            p["person_id"]: p
            for p in (await client.get("/api/v1/persons/browse")).json()["persons"]
        }
        assert cards[left_person]["thumbnail_url"] is None
        assert cards[right_person]["avatar_face_id"] == data["right"]
        page = (await client.get(f"/api/v1/persons/{left_person}/photos")).json()
        assert page["assets"] == []

    api_env.scenario(scenario)
