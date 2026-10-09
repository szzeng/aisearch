"""Small personal-album loop over synthetic photos and real protected API/transactions."""

from __future__ import annotations

import base64
import json
from time import perf_counter

from conftest import ApiEnv
from p3_persons_page_fixtures import (
    add_link,
    add_person,
    insert_face,
    seed_library,
    set_control,
    set_exclusion,
)

from photo_search.persons.albums import _inbox_group_cte, people_page
from photo_search.places.revision import read_filter_revision


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


def test_people_can_be_sorted_by_global_effective_photo_count(api_env: ApiEnv) -> None:
    data = seed(api_env)
    catalog = api_env.service.catalog
    api_env.add_photo("extra-mom.jpg", noise=20)
    source = catalog.read_one("SELECT source_id FROM source_roots LIMIT 1")
    api_env.scan(int(source["source_id"]))
    extra_asset = catalog.read_one(
        "SELECT a.asset_id, a.sha256, l.location_id FROM assets AS a "
        "JOIN file_locations AS l ON l.asset_id = a.asset_id WHERE l.relative_path = ?",
        ("extra-mom.jpg",),
    )
    extra_face = insert_face(
        catalog,
        asset_id=int(extra_asset["asset_id"]),
        sha256=str(extra_asset["sha256"]),
        bbox=(40, 40, 120, 120),
        ordinal=0,
        location_id=int(extra_asset["location_id"]),
    )
    add_link(catalog, extra_face, data["mom"])
    with catalog.write() as connection:
        # A second confirmed face in one group photo must not increase the photo count;
        # confirming the pending face gives Mom a second distinct asset.
        connection.execute(
            "UPDATE face_person_links SET person_id = ? WHERE face_id = ?",
            (data["dad"], data["neighbour"]),
        )
        connection.execute(
            "UPDATE face_person_links SET decision = 'CONFIRMED', reason = NULL WHERE face_id = ?",
            (data["pending"],),
        )
    legacy_cursor = base64.urlsafe_b64encode(
        json.dumps(
            ["people1", "all", "", read_filter_revision(catalog.connection), data["dad"]],
            separators=(",", ":"),
        ).encode()
    ).decode().rstrip("=")

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        first = await client.get(
            "/api/v1/persons/browse", params={"kind": "all", "sort": "photos_desc", "limit": 1}
        )
        page2 = await client.get(
            "/api/v1/persons/browse",
            params={"kind": "all", "sort": "photos_desc", "limit": 1, "cursor": first.json()["next_cursor"]},
        )
        page3 = await client.get(
            "/api/v1/persons/browse",
            params={"kind": "all", "sort": "photos_desc", "limit": 1, "cursor": page2.json()["next_cursor"]},
        )
        scoped = await client.get(
            "/api/v1/persons/browse", params={"kind": "named", "q": "爸爸", "sort": "photos_desc"}
        )
        invalid_sort = await client.get("/api/v1/persons/browse", params={"sort": "recent"})
        wrong_sort = await client.get(
            "/api/v1/persons/browse",
            params={"kind": "all", "sort": "person_id", "limit": 1, "cursor": first.json()["next_cursor"]},
        )
        legacy_default = await client.get(
            "/api/v1/persons/browse",
            params={"kind": "all", "limit": 1, "cursor": legacy_cursor},
        )
        wrong_sort_reverse = await client.get(
            "/api/v1/persons/browse",
            params={
                "kind": "all",
                "sort": "photos_desc",
                "limit": 1,
                "cursor": legacy_default.json()["next_cursor"],
            },
        )
        wrong_query = await client.get(
            "/api/v1/persons/browse",
            params={
                "kind": "named",
                "q": "爸爸",
                "sort": "photos_desc",
                "limit": 1,
                "cursor": first.json()["next_cursor"],
            },
        )
        changed = await client.put(
            f"/api/v1/persons/{data['mom']}/label",
            headers=api_env.post_headers(csrf),
            json={"request_id": "sort-revision", "name": "Mom 2", "expected_revision": 1},
        )
        stale = await client.get(
            "/api/v1/persons/browse",
            params={"kind": "all", "sort": "photos_desc", "limit": 1, "cursor": first.json()["next_cursor"]},
        )
        old_default = await client.get("/api/v1/persons/browse", params={"kind": "all", "limit": 1})
        return (
            first,
            page2,
            page3,
            scoped,
            invalid_sort,
            wrong_sort,
            changed,
            stale,
            old_default,
            legacy_default,
            wrong_sort_reverse,
            wrong_query,
        )

    (
        first,
        page2,
        page3,
        scoped,
        invalid_sort,
        wrong_sort,
        changed,
        stale,
        old_default,
        legacy_default,
        wrong_sort_reverse,
        wrong_query,
    ) = api_env.scenario(scenario)
    assert first.status_code == page2.status_code == page3.status_code == 200
    first_person, second_person, third_person = [
        response.json()["persons"][0] for response in (first, page2, page3)
    ]
    assert (first_person["person_id"], first_person["asset_count"]) == (data["dad"], 2)
    assert (second_person["person_id"], second_person["asset_count"]) == (data["mom"], 2)
    assert (third_person["person_id"], third_person["asset_count"]) == (data["unnamed"], 0)
    assert [first_person["person_id"], second_person["person_id"], third_person["person_id"]] == [
        data["dad"], data["mom"], data["unnamed"]
    ]
    assert scoped.status_code == 200
    assert [person["person_id"] for person in scoped.json()["persons"]] == [data["dad"]]
    assert invalid_sort.status_code == 422
    assert wrong_sort.status_code == 409 and wrong_sort.json()["code"] == "INVALID_CURSOR"
    assert changed.status_code == 200
    assert stale.status_code == 409 and stale.json()["code"] == "INVALID_CURSOR"
    assert old_default.status_code == 200
    assert old_default.json()["persons"][0]["person_id"] == data["dad"]
    assert legacy_default.status_code == 200
    assert legacy_default.json()["persons"][0]["person_id"] == data["mom"]
    assert wrong_sort_reverse.status_code == 409
    assert wrong_sort_reverse.json()["code"] == "INVALID_CURSOR"
    assert wrong_query.status_code == 409
    assert wrong_query.json()["code"] == "INVALID_CURSOR"
    summary_trace: list[str] = []
    catalog.connection.set_trace_callback(summary_trace.append)
    try:
        people_page(catalog, kind="all", q="", limit=1, cursor=None, sort="photos_desc")
    finally:
        catalog.connection.set_trace_callback(None)
    summary_sql = [query.upper() for query in summary_trace]
    sort_counts = [
        query
        for query in summary_sql
        if "EFFECTIVE_COUNTS AS" in query and "COUNT(DISTINCT F.ASSET_ID)" in query
    ]
    page_member_counts = [
        query
        for query in summary_sql
        if "WITH PAGE(" in query and "COUNT(*) AS FACE_COUNT" in query
    ]
    assert len(sort_counts) == 1
    assert len(page_member_counts) == 1
    assert "COUNT(DISTINCT F.ASSET_ID)" not in page_member_counts[0]


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


def test_inbox_candidate_groups_sort_distinct_photos_and_page_consistently(api_env: ApiEnv) -> None:
    photos = {f"a-{i}.jpg": [(20, 20, 90, 90)] for i in range(10)}
    photos["a-0.jpg"] = [(20, 20, 90, 90), (130, 20, 200, 90)]
    photos.update({f"b-{i}.jpg": [(20, 20, 90, 90)] for i in range(5)})
    photos.update({f"c-{i}.jpg": [(20, 20, 90, 90)] for i in range(2)})
    photos["loose.jpg"] = [(20, 20, 90, 90)]
    seeded = seed_library(api_env, photos)
    catalog = api_env.service.catalog
    people = [add_person(catalog, name=name) for name in ("甲", "乙", "丙", "竞争候选")]
    faces = {path: value["faces"] for path, value in seeded.items()}
    for path, path_faces in faces.items():
        if path.startswith("a-"):
            for face in path_faces:
                add_link(catalog, face, people[0], decision="SUGGESTED", confidence=0.8)
        elif path.startswith("b-"):
            add_link(catalog, path_faces[0], people[1], decision="SUGGESTED", confidence=0.8)
        elif path.startswith("c-"):
            add_link(catalog, path_faces[0], people[2], decision="SUGGESTED", confidence=0.8)
    add_link(catalog, faces["a-1.jpg"][0], people[3], decision="SUGGESTED", confidence=0.2)
    add_link(catalog, faces["loose.jpg"][0], people[3], decision="SUGGESTED", confidence=0.9)
    set_exclusion(catalog, faces["a-9.jpg"][0], people[0])
    cte, params = _inbox_group_cte("all")
    plan = catalog.read_all(
        "EXPLAIN QUERY PLAN " + cte + "SELECT * FROM cards ORDER BY photo_count DESC, stable_key ASC LIMIT 31",
        params,
    )
    plan_details = [str(row["detail"]) for row in plan]
    assert any("face_person_links" in detail and "INDEX" in detail.upper() for detail in plan_details)

    async def scenario(client):
        assert (await client.get("/api/v1/faces/inbox/groups")).status_code == 401
        await api_env.authenticate(client)
        before = tuple(catalog.connection.iterdump())
        first = (await client.get("/api/v1/faces/inbox/groups", params={"limit": 2})).json()
        assert [(card["type"], card["photo_count"]) for card in first["cards"]] == [
            ("candidate_group", 9), ("candidate_group", 5)
        ]
        assert first["cards"][0]["candidate_name"] == "甲"
        second = (await client.get(
            "/api/v1/faces/inbox/groups", params={"limit": 2, "cursor": first["next_cursor"]}
        )).json()
        assert [(card["type"], card["photo_count"]) for card in second["cards"]] == [
            ("candidate_group", 2), ("single_face", 1)
        ]
        expanded = (await client.get(
            f"/api/v1/faces/inbox/groups/{first['cards'][0]['group_key']}", params={"limit": 2}
        )).json()
        assert expanded["has_more"] and len(expanded["faces"]) == 2
        expanded2 = (await client.get(
            f"/api/v1/faces/inbox/groups/{first['cards'][0]['group_key']}",
            params={"limit": 20, "cursor": expanded["next_cursor"]},
        )).json()
        all_faces = expanded["faces"] + expanded2["faces"]
        assert len(all_faces) == 10
        assert len({face["asset_id"] for face in all_faces}) == 9
        assert (await client.get(
            f"/api/v1/faces/inbox/groups/{first['cards'][0]['group_key']}",
            params={"kind": "suggested", "cursor": expanded["next_cursor"]},
        )).status_code == 409
        assert (await client.get(
            f"/api/v1/faces/inbox/groups/{second['cards'][0]['group_key']}",
            params={"cursor": expanded["next_cursor"]},
        )).status_code == 409
        third = (await client.get(
            "/api/v1/faces/inbox/groups", params={"limit": 2, "cursor": second["next_cursor"]}
        )).json()
        assert len(third["cards"]) == 1 and third["cards"][0]["type"] == "single_face"
        assert {second["cards"][1]["face"]["face_id"], third["cards"][0]["face"]["face_id"]} == {
            faces["a-9.jpg"][0], faces["loose.jpg"][0]
        }
        assert (await client.get(
            "/api/v1/faces/inbox/groups", params={"kind": "suggested", "cursor": first["next_cursor"]}
        )).status_code == 409
        assert (await client.get("/api/v1/faces/inbox/groups", params={"cursor": "bad"})).status_code == 409
        legacy = (await client.get("/api/v1/faces/inbox", params={"limit": 30})).json()
        assert "faces" in legacy and "cards" not in legacy
        assert (await client.get("/api/v1/faces/inbox/summary")).json()["total"] == 19
        assert tuple(catalog.connection.iterdump()) == before
        set_exclusion(catalog, faces["a-9.jpg"][0], people[0], active=False)
        refreshed = (await client.get("/api/v1/faces/inbox/groups", params={"limit": 1})).json()
        assert refreshed["cards"][0]["photo_count"] == 10

    api_env.scenario(scenario)


def test_inbox_group_card_page_has_a_30_card_boundary(api_env: ApiEnv) -> None:
    seeded = seed_library(api_env, {f"single-{i}.jpg": [(20, 20, 90, 90)] for i in range(31)})

    async def scenario(client):
        await api_env.authenticate(client)
        started = perf_counter()
        first_response = await client.get("/api/v1/faces/inbox/groups", params={"limit": 100})
        print(f"synthetic_31_face_group_page_ms={(perf_counter() - started) * 1000:.3f}")
        first = first_response.json()
        assert len(first["cards"]) == 30 and first["has_more"]
        second = (await client.get(
            "/api/v1/faces/inbox/groups", params={"limit": 100, "cursor": first["next_cursor"]}
        )).json()
        assert len(second["cards"]) == 1 and not second["has_more"]
        ids = [card["face"]["face_id"] for card in first["cards"] + second["cards"]]
        assert len(ids) == len(set(ids)) == len(seeded)

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
        assert empty not in cards
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
