"""Synthetic API and SQLite coverage for personal person collections."""

from __future__ import annotations

from conftest import ApiEnv
from p3_persons_page_fixtures import add_link, add_person, seed_library

from photo_search.api.queries import _collection_condition
from photo_search.catalog.db import Catalog


def _collection_url(catalog: Catalog, collection_id: int, suffix: str = "") -> str:
    row = catalog.read_one(
        "SELECT instance_token FROM person_collections WHERE collection_id = ?", (collection_id,)
    )
    assert row is not None
    return f"/api/v1/persons/collections/{collection_id}/{row['instance_token']}{suffix}"


def test_collection_batch_add_hide_is_atomic_and_persistent(api_env: ApiEnv) -> None:
    catalog = api_env.service.catalog
    synthetic = seed_library(
        api_env,
        {f"路人{index:03d}.jpg": [(20, 20, 90, 90)] for index in range(100)},
    )
    people = []
    for index in range(100):
        person_id = add_person(catalog)
        people.append(person_id)
        add_link(catalog, synthetic[f"路人{index:03d}.jpg"]["faces"][0], person_id)
    before = {
        table: int(catalog.read_one(f"SELECT COUNT(*) AS n FROM {table}")["n"])
        for table in ("persons", "faces", "face_person_links", "face_embeddings", "assets")
    }
    from photo_search.persons.service import PersonService
    PersonService.open(catalog).set_visibility(person_ids=people[:20], hidden=True)

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        created = await client.post("/api/v1/persons/collections", headers=headers, json={"name": "  路人  "})
        assert created.status_code == 200
        collection_id = created.json()["collection"]["collection_id"]
        hidden = people[80:]
        first_batch = await client.post(
            _collection_url(catalog, collection_id, "/members"), headers=headers,
            json={"action": "add", "person_ids": people[:80]},
        )
        assert first_batch.json()["changed"] == 80
        assert (await client.post(
            _collection_url(catalog, collection_id, "/members"), headers=headers,
            json={"action": "add", "person_ids": hidden, "hide_after_add": True},
        )).json()["changed"] == 20
        rejected = await client.post(
            _collection_url(catalog, collection_id, "/members"), headers=headers,
            json={"action": "add", "person_ids": [people[80], 999999], "hide_after_add": True},
        )
        assert rejected.status_code == 404
        assert catalog.read_one("SELECT COUNT(*) AS n FROM person_hidden")["n"] == 40
        assert catalog.read_one(
            "SELECT COUNT(*) AS n FROM person_collection_members WHERE collection_id = ?", (collection_id,)
        )["n"] == 100
        people_page = (await client.get(_collection_url(catalog, collection_id, "/people"), params={"limit": 200})).json()
        assert len(people_page["people"]) == 100
        reopened = Catalog.open(catalog.db_path)
        try:
            assert reopened.read_one("SELECT COUNT(*) AS n FROM person_collection_members WHERE collection_id = ?", (collection_id,))["n"] == 100
            assert reopened.read_one("SELECT COUNT(*) AS n FROM person_hidden")["n"] == 40
            for table, count in before.items():
                assert reopened.read_one(f"SELECT COUNT(*) AS n FROM {table}")["n"] == count
        finally:
            reopened.close()

    api_env.scenario(scenario)


def test_collection_photo_stream_deduplicates_and_tracks_new_confirmed_faces(api_env: ApiEnv) -> None:
    seeded = seed_library(api_env, {
        "shared.jpg": [(20, 20, 90, 90), (150, 30, 220, 100)],
        "first.jpg": [(30, 30, 100, 100)],
        "second.jpg": [(40, 40, 110, 110)],
        "new.jpg": [],
        "pending.jpg": [(50, 50, 120, 120)],
    })
    catalog = api_env.service.catalog
    a, b = add_person(catalog), add_person(catalog)
    add_link(catalog, seeded["shared.jpg"]["faces"][0], a)
    add_link(catalog, seeded["shared.jpg"]["faces"][1], b)
    add_link(catalog, seeded["first.jpg"]["faces"][0], a)
    add_link(catalog, seeded["second.jpg"]["faces"][0], b)
    add_link(catalog, seeded["pending.jpg"]["faces"][0], a, decision="SUGGESTED")
    with catalog.write() as connection:
        for offset, name in enumerate(("shared.jpg", "first.jpg", "second.jpg"), 1):
            connection.execute(
                "INSERT INTO asset_metadata(asset_id, taken_at_raw, taken_at_sort, time_source, time_confidence, timezone_offset, camera) "
                "VALUES (?, NULL, ?, 'EXIF_ORIGINAL', 'MEDIUM', NULL, NULL)",
                (seeded[name]["asset_id"], offset * 1000),
            )

    collection_ids: list[int] = []
    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        collection_id = (await client.post("/api/v1/persons/collections", headers=headers, json={"name": "同学"})).json()["collection"]["collection_id"]
        collection_ids.append(collection_id)
        assert (await client.post(_collection_url(catalog, collection_id, "/members"), headers=headers, json={"action": "add", "person_ids": [a, b]})).status_code == 200
        listed = await client.get("/api/v1/persons/collections")
        assert listed.status_code == 200
        assert next(item for item in listed.json()["collections"] if item["collection_id"] == collection_id)["photo_count"] == 3
        condition, parameters = _collection_condition(collection_id)
        plan = catalog.read_all(
            f"EXPLAIN QUERY PLAN SELECT a.asset_id FROM assets AS a WHERE {condition}", parameters
        )
        plan_text = " ".join(str(row["detail"]) for row in plan)
        assert "person_collection_members" in plan_text
        assert "idx_face_person_links_person" in plan_text or "sqlite_autoindex_person_collection_members_1" in plan_text
        empty = (await client.post("/api/v1/persons/collections", headers=headers, json={"name": "空"})).json()["collection"]["collection_id"]
        empty_page = await client.get(_collection_url(catalog, empty, "/photos"))
        assert empty_page.json()["collection"]["photo_count"] == 0
        listed = (await client.get("/api/v1/persons/collections")).json()["collections"]
        empty_list_item = next(
            item for item in listed if item["collection_id"] == empty
        )
        assert empty_list_item["photo_count"] == 0
        cursor = None
        asset_ids = []
        first_page_cursor = None
        while True:
            params = {"limit": 1}
            if cursor:
                params["cursor"] = cursor
            page_response = await client.get(_collection_url(catalog, collection_id, "/photos"), params=params)
            assert page_response.status_code == 200, page_response.text
            page = page_response.json()
            assert page["collection"]["photo_count"] == 3
            if first_page_cursor is None:
                first_page_cursor = page.get("next_cursor")
            asset_ids.extend(asset["asset_id"] for asset in page["assets"])
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]
        cross_collection = await client.get(
            _collection_url(catalog, empty, "/photos"), params={"cursor": first_page_cursor}
        )
        assert cross_collection.status_code == 409
        assert len(asset_ids) == len(set(asset_ids)) == 3
        assert set(asset_ids) == {seeded[name]["asset_id"] for name in ("shared.jpg", "first.jpg", "second.jpg")}
        assert asset_ids == [seeded[name]["asset_id"] for name in ("second.jpg", "first.jpg", "shared.jpg")]
        from photo_search.persons.service import PersonService
        PersonService.open(catalog).set_visibility(person_ids=[a], hidden=True)
        still_hidden = (await client.get(_collection_url(catalog, collection_id, "/photos"), params={"limit": 100})).json()
        assert {item["asset_id"] for item in still_hidden["assets"]} == set(asset_ids)
        PersonService.open(catalog).set_visibility(person_ids=[a], hidden=False)
        assert (await client.get(_collection_url(catalog, empty, "/photos"))).json()["assets"] == []

    api_env.scenario(scenario)
    # A new authoritative confirmed face of an existing member appears on the next query.
    from p3_persons_page_fixtures import insert_face
    face = insert_face(catalog, asset_id=seeded["new.jpg"]["asset_id"], sha256=seeded["new.jpg"]["sha256"], bbox=(20, 20, 90, 90), ordinal=0)
    add_link(catalog, face, a)
    async def refreshed(client):
        await api_env.authenticate(client)
        response = await client.get(_collection_url(catalog, collection_ids[0], "/photos"), params={"limit": 100})
        assert response.status_code == 200
        assert seeded["new.jpg"]["asset_id"] in {item["asset_id"] for item in response.json()["assets"]}
        assert response.json()["collection"]["photo_count"] == 4
    api_env.scenario(refreshed)


def test_collection_name_conflict_revision_cursor_and_delete_preserve_people(api_env: ApiEnv) -> None:
    catalog = api_env.service.catalog
    person_id, other_person_id = add_person(catalog), add_person(catalog)

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        created = await client.post("/api/v1/persons/collections", headers=headers, json={"name": "朋友"})
        collection_id = created.json()["collection"]["collection_id"]
        conflict = await client.post("/api/v1/persons/collections", headers=headers, json={"name": " 朋友 "})
        assert conflict.status_code == 409
        case_created = await client.post("/api/v1/persons/collections", headers=headers, json={"name": "Friends"})
        assert case_created.status_code == 200
        case_conflict = await client.post("/api/v1/persons/collections", headers=headers, json={"name": "friends"})
        assert case_conflict.status_code == 409
        case_collection_id = case_created.json()["collection"]["collection_id"]
        await client.delete(_collection_url(catalog, case_collection_id), headers=headers)
        await client.post(_collection_url(catalog, collection_id, "/members"), headers=headers, json={"action": "add", "person_ids": [person_id, other_person_id]})
        page = await client.get(_collection_url(catalog, collection_id, "/people"), params={"limit": 1})
        assert page.status_code == 200
        cross_collection = (await client.post("/api/v1/persons/collections", headers=headers, json={"name": "另一组"})).json()["collection"]["collection_id"]
        wrong_cursor = await client.get(_collection_url(catalog, cross_collection, "/people"), params={"limit": 1, "cursor": page.json()["next_cursor"]})
        assert wrong_cursor.status_code == 409
        renamed = await client.patch(_collection_url(catalog, collection_id), headers=headers, json={"name": "伙伴"})
        assert renamed.json()["collection"]["revision"] == 3
        stale = await client.get(_collection_url(catalog, collection_id, "/people"), params={"limit": 1, "cursor": page.json()["next_cursor"]})
        assert stale.status_code == 409
        deleted = await client.delete(_collection_url(catalog, collection_id), headers=headers)
        assert deleted.status_code == 200
        assert catalog.read_one("SELECT person_id FROM persons WHERE person_id=?", (person_id,)) is not None
        assert catalog.read_one("SELECT COUNT(*) AS n FROM person_collection_members WHERE collection_id=?", (collection_id,))["n"] == 0

    api_env.scenario(scenario)


def test_collection_merge_undo_remove_and_large_member_sql(api_env: ApiEnv) -> None:
    catalog = api_env.service.catalog
    people = [add_person(catalog) for _ in range(160)]
    collection = __import__("photo_search.persons.collections", fromlist=["create_collection", "edit_members"])
    created = collection.create_collection(catalog, "大家")
    collection_id = created["collection"]["collection_id"]
    token = created["collection"]["instance_token"]
    collection.edit_members(catalog, collection_id, action="add", person_ids=people, instance_token=token)
    # Simulate a committed merge and its later compensating undo while anchors remain stable.
    with catalog.write() as connection:
        connection.execute("UPDATE persons SET merged_into = ? WHERE person_id = ?", (people[1], people[0]))
    def all_people():
        cursor = None
        result = []
        while True:
            page = collection.people_page(catalog, collection_id, instance_token=token, limit=100, cursor=cursor)
            result.extend(page["people"])
            if not page["has_more"]:
                return result
            cursor = page["next_cursor"]
    assert len(all_people()) == 159
    with catalog.write() as connection:
        connection.execute("UPDATE persons SET merged_into = NULL WHERE person_id = ?", (people[0],))
    assert len(all_people()) == 160
    with catalog.write() as connection:
        connection.execute("UPDATE persons SET merged_into = ? WHERE person_id = ?", (people[1], people[0]))
    collection.edit_members(catalog, collection_id, action="remove", person_ids=[people[1]], instance_token=token)
    assert catalog.read_one("SELECT COUNT(*) AS n FROM person_collection_members WHERE collection_id=? AND person_id IN (?,?)", (collection_id, people[0], people[1]))["n"] == 0
    plan = catalog.read_all(
        "EXPLAIN QUERY PLAN SELECT collection_id FROM person_collection_members WHERE person_id = ?",
        (people[2],),
    )
    assert any("idx_person_collection_members_person" in str(row["detail"]) for row in plan)
    # The generic search filter retains its independent frozen ceiling.
    import pytest

    from photo_search.search.retrieval import SearchScope
    with pytest.raises(Exception):
        SearchScope.build(person_ids=list(range(1, 102)))
    assert catalog.read_one("SELECT COUNT(*) AS n FROM persons")["n"] == 160


def test_collection_list_first_page_and_pagination(api_env: ApiEnv) -> None:
    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        empty = await client.get("/api/v1/persons/collections", params={"limit": 1})
        assert empty.status_code == 200
        assert empty.json() == {"collections": [], "has_more": False, "next_cursor": None}
        for name in ("列表甲", "列表乙", "列表丙"):
            created = await client.post("/api/v1/persons/collections", headers=headers, json={"name": name})
            assert created.status_code == 200
        first = await client.get("/api/v1/persons/collections", params={"limit": 2})
        assert first.status_code == 200
        assert [item["name"] for item in first.json()["collections"]] == ["列表甲", "列表乙"]
        assert first.json()["has_more"] is True
        second = await client.get(
            "/api/v1/persons/collections",
            params={"limit": 2, "cursor": first.json()["next_cursor"]},
        )
        assert second.status_code == 200
        assert [item["name"] for item in second.json()["collections"]] == ["列表丙"]
        assert second.json()["has_more"] is False
    api_env.scenario(scenario)


def test_collection_people_use_recent_asset_tie_break_and_new_cursor(api_env: ApiEnv) -> None:
    photos = seed_library(
        api_env,
        {name: [(20, 20, 90, 90)] for name in ("甲1.jpg", "甲2.jpg", "乙1.jpg", "乙2.jpg", "丙1.jpg")},
    )
    catalog = api_env.service.catalog
    first = add_person(catalog, name="甲")
    second = add_person(catalog, name="乙")
    third = add_person(catalog, name="丙")
    empty = add_person(catalog, name="丁")
    for person_id, names in ((first, ("甲1.jpg", "甲2.jpg")), (second, ("乙1.jpg", "乙2.jpg")), (third, ("丙1.jpg",))):
        for name in names:
            add_link(catalog, photos[name]["faces"][0], person_id)
    with catalog.write() as connection:
        for name, created_at in {"甲1.jpg": 10, "甲2.jpg": 20, "乙1.jpg": 10, "乙2.jpg": 30, "丙1.jpg": 40}.items():
            connection.execute("UPDATE assets SET created_at=? WHERE asset_id=?", (created_at, photos[name]["asset_id"]))

    import pytest

    from photo_search.api.errors import ApiError
    from photo_search.persons import collections
    from photo_search.places.revision import read_filter_revision

    created = collections.create_collection(catalog, "最近入库")["collection"]
    collection_id = int(created["collection_id"])
    token = str(created["instance_token"])
    collections.edit_members(catalog, collection_id, action="add", person_ids=[first, second, third, empty], instance_token=token)
    collection_statements: list[str] = []
    catalog.connection.set_trace_callback(collection_statements.append)
    try:
        first_page = collections.people_page(catalog, collection_id, instance_token=token, limit=1, cursor=None)
    finally:
        catalog.connection.set_trace_callback(None)
    collection_query = next(statement for statement in collection_statements if "EFFECTIVE_COUNTS AS" in statement.upper())
    collection_plan = catalog.read_all("EXPLAIN QUERY PLAN " + collection_query)
    assert collection_plan
    print("collection people plan: " + "; ".join(str(row["detail"]) for row in collection_plan if "SEARCH" in str(row["detail"]) or "SCAN" in str(row["detail"]) or "TEMP B-TREE" in str(row["detail"])))
    ids = [person["person_id"] for person in first_page["people"]]
    cursor = first_page["next_cursor"]
    if not first_page["has_more"]:
        cursor = None
    while True:
        if cursor is None:
            break
        page = collections.people_page(catalog, collection_id, instance_token=token, limit=1, cursor=cursor)
        ids.extend(person["person_id"] for person in page["people"])
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    assert ids == [second, first, third, empty]
    current = catalog.read_one("SELECT revision FROM person_collections WHERE collection_id=?", (collection_id,))
    old_cursor = collections._encode(
        "pcp-count1",
        [collection_id, token, int(current["revision"]), read_filter_revision(catalog.connection)],
        [2, first],
    )
    with pytest.raises(ApiError) as stale_prefix:
        collections.people_page(catalog, collection_id, instance_token=token, limit=1, cursor=old_cursor)
    assert stale_prefix.value.status == 409


def test_deleted_collection_id_is_not_reused_for_stale_requests(api_env: ApiEnv) -> None:
    catalog = api_env.service.catalog
    people = [add_person(catalog) for _ in range(4)]

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        first = (await client.post(
            "/api/v1/persons/collections", headers=headers, json={"name": "旧合集"}
        )).json()["collection"]["collection_id"]
        await client.post(
            _collection_url(catalog, first, "/members"), headers=headers,
            json={"action": "add", "person_ids": people[:2]},
        )
        old_cursor = (await client.get(
            _collection_url(catalog, first, "/people"), params={"limit": 1}
        )).json()["next_cursor"]
        assert (await client.delete(
            _collection_url(catalog, first), headers=headers
        )).status_code == 200
        replacement_response = await client.post(
            "/api/v1/persons/collections", headers=headers, json={"name": "新合集"}
        )
        replacement = replacement_response.json()["collection"]["collection_id"]
        assert replacement > first
        await client.post(
            _collection_url(catalog, replacement, "/members"), headers=headers,
            json={"action": "add", "person_ids": people[2:]},
        )
        stale_page = await client.get(
            _collection_url(catalog, replacement, "/people"),
            params={"limit": 1, "cursor": old_cursor},
        )
        assert stale_page.status_code == 409
        stale_delete = await client.delete(
            f"/api/v1/persons/collections/{first}", headers=headers
        )
        assert stale_delete.status_code == 404
        stale_legacy_page = await client.get(f"/api/v1/persons/collections/{replacement}/people")
        assert stale_legacy_page.status_code == 404
        stale_legacy_delete = await client.delete(
            f"/api/v1/persons/collections/{replacement}", headers=headers
        )
        assert stale_legacy_delete.status_code == 404
        assert (await client.get(_collection_url(catalog, replacement))).status_code == 200
    api_env.scenario(scenario)


def test_formal_merge_deduplicates_collection_face_projection(api_env: ApiEnv) -> None:
    catalog = api_env.service.catalog
    seeded = seed_library(api_env, {"merge.jpg": [(20, 20, 90, 90)]})
    source, target = add_person(catalog), add_person(catalog)
    face_id = seeded["merge.jpg"]["faces"][0]
    add_link(catalog, face_id, source)
    from photo_search.persons.service import PersonService

    collection = __import__("photo_search.persons.collections", fromlist=["create_collection", "edit_members", "photo_page"])
    created = collection.create_collection(catalog, "合并后去重")
    collection_id = created["collection"]["collection_id"]
    token = created["collection"]["instance_token"]
    collection.edit_members(catalog, collection_id, action="add", person_ids=[source, target], instance_token=token)
    PersonService.open(catalog).merge(
        target_person_id=target,
        target_expected_revision=1,
        sources=[{"person_id": source, "expected_revision": 1}],
        request_id="collection-face-projection-merge",
    )
    page = collection.photo_page(catalog, api_env.service.media_cache(), collection_id, instance_token=token)
    assert len(page["assets"]) == 1
    assert [face["face_id"] for face in page["assets"][0]["person_faces"]] == [face_id]


def test_repeated_collection_remove_is_noop_without_revision_change(api_env: ApiEnv) -> None:
    catalog = api_env.service.catalog
    person_id = add_person(catalog)
    from photo_search.persons import collections

    created = collections.create_collection(catalog, "重复移出")
    collection_id = created["collection"]["collection_id"]
    token = created["collection"]["instance_token"]
    first_add = collections.edit_members(catalog, collection_id, action="add", person_ids=[person_id], instance_token=token)
    assert first_add["changed"] == 1
    removed = collections.edit_members(catalog, collection_id, action="remove", person_ids=[person_id], instance_token=token)
    assert removed["changed"] == 1
    revision = removed["revision"]
    repeated = collections.edit_members(catalog, collection_id, action="remove", person_ids=[person_id], instance_token=token)
    assert repeated["changed"] == 0
    assert repeated["revision"] == revision
