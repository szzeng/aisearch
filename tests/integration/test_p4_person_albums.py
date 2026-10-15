"""Small personal-album loop over synthetic photos and real protected API/transactions."""

from __future__ import annotations

import asyncio
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

from photo_search.catalog.db import Catalog
from photo_search.persons.albums import _inbox_group_cte, people_page
from photo_search.persons.service import PersonService
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


def test_recent_photo_sort_uses_effective_members_and_three_part_cursor(api_env: ApiEnv) -> None:
    photos = seed_library(
        api_env,
        {name: ([(20, 20, 90, 90), (120, 20, 190, 90)] if name == "b1.jpg" else [(20, 20, 90, 90)])
         for name in ("a1.jpg", "a2.jpg", "b1.jpg", "b2.jpg", "t1.jpg", "t2.jpg", "c1.jpg")},
    )
    catalog = api_env.service.catalog
    person_a = add_person(catalog, name="甲")
    person_b = add_person(catalog, name="乙")
    person_tie = add_person(catalog, name="丙")
    person_c = add_person(catalog, name="丁")
    add_person(catalog, name="戊")  # Empty identities stay outside both sort and cursor pages.
    links = {
        person_a: ("a1.jpg", "a2.jpg"),
        person_b: ("b1.jpg", "b2.jpg"),
        person_tie: ("t1.jpg", "t2.jpg"),
        person_c: ("c1.jpg",),
    }
    for person_id, names in links.items():
        for name in names:
            add_link(catalog, photos[name]["faces"][0], person_id)
    add_link(catalog, photos["b1.jpg"]["faces"][1], person_b)
    set_control(catalog, photos["c1.jpg"]["faces"][0], "MANUAL_UNASSIGNED")
    timestamps = {"a1.jpg": 100, "a2.jpg": 200, "b1.jpg": 100, "b2.jpg": 300,
                  "t1.jpg": 100, "t2.jpg": 200, "c1.jpg": 400}
    with catalog.write() as connection:
        for name, created_at in timestamps.items():
            connection.execute("UPDATE assets SET created_at = ? WHERE asset_id = ?", (created_at, photos[name]["asset_id"]))
        connection.execute("UPDATE face_person_links SET updated_at=9999 WHERE person_id=?", (person_a,))

    recent_ids: list[int] = []
    cursor = None
    while True:
        page = people_page(
            catalog, kind="all", q="", limit=2, cursor=cursor,
            sort="photos_recent_desc", visibility="visible",
        )
        recent_ids.extend(person["person_id"] for person in page["persons"])
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    assert recent_ids == [person_b, person_a, person_tie]

    legacy_ids = [
        person["person_id"] for person in people_page(
            catalog, kind="all", q="", limit=10, cursor=None,
            sort="photos_desc", visibility="visible",
        )["persons"]
    ]
    assert legacy_ids == [person_a, person_b, person_tie]
    import pytest

    from photo_search.api.errors import ApiError

    sort_statements: list[str] = []
    catalog.connection.set_trace_callback(sort_statements.append)
    try:
        first_recent = people_page(catalog, kind="all", q="", limit=1, cursor=None, sort="photos_recent_desc")
    finally:
        catalog.connection.set_trace_callback(None)
    sort_query = next(statement for statement in sort_statements if "EFFECTIVE_COUNTS AS" in statement.upper())
    sort_plan = catalog.read_all("EXPLAIN QUERY PLAN " + sort_query)
    assert sort_plan
    sort_details = [str(row["detail"]) for row in sort_plan]
    print("people recent-sort plan: " + "; ".join(detail for detail in sort_details if "SEARCH" in detail or "TEMP B-TREE" in detail))
    with pytest.raises(ApiError):
        people_page(catalog, kind="all", q="", limit=1, cursor=first_recent["next_cursor"], sort="photos_desc")
    first_legacy = people_page(catalog, kind="all", q="", limit=1, cursor=None, sort="photos_desc")
    with pytest.raises(ApiError):
        people_page(catalog, kind="all", q="", limit=1, cursor=first_legacy["next_cursor"], sort="photos_recent_desc")

    async def route_accepts_new_sort(client):
        await api_env.authenticate(client)
        response = await client.get(
            "/api/v1/persons/browse", params={"sort": "photos_recent_desc", "limit": 1}
        )
        assert response.status_code == 200
        assert response.json()["persons"][0]["person_id"] == person_b

    api_env.scenario(route_accepts_new_sort)

    service = PersonService.open(catalog)
    merged = service.merge(
        target_person_id=person_a,
        target_expected_revision=1,
        sources=[{"person_id": person_tie, "expected_revision": 1}],
        request_id="recent-sort-merge",
    )
    merged_page = people_page(catalog, kind="all", q="", limit=1, cursor=None, sort="photos_recent_desc")
    assert (merged_page["persons"][0]["person_id"], merged_page["persons"][0]["asset_count"]) == (person_a, 4)
    undone = service.undo(
        operation_id=merged["operation_id"],
        request_id="recent-sort-merge-undo",
        expected_operation_revision=merged["operation_revision"],
    )
    assert undone["changed"] is True
    restored_page = people_page(catalog, kind="all", q="", limit=10, cursor=None, sort="photos_recent_desc")
    assert [person["person_id"] for person in restored_page["persons"]] == [person_b, person_a, person_tie]

    api_env.add_photo("newest.jpg", noise=42)
    source = catalog.read_one("SELECT source_id FROM source_roots LIMIT 1")
    api_env.scan(int(source["source_id"]))
    newest = catalog.read_one(
        "SELECT a.asset_id, a.sha256, l.location_id FROM assets AS a "
        "JOIN file_locations AS l ON l.asset_id=a.asset_id WHERE l.relative_path=?",
        ("newest.jpg",),
    )
    newest_face = insert_face(
        catalog,
        asset_id=int(newest["asset_id"]),
        sha256=str(newest["sha256"]),
        bbox=(20, 20, 90, 90),
        ordinal=0,
        location_id=int(newest["location_id"]),
    )
    add_link(catalog, newest_face, person_a)
    added_page = people_page(catalog, kind="all", q="", limit=1, cursor=None, sort="photos_recent_desc")
    assert (added_page["persons"][0]["person_id"], added_page["persons"][0]["asset_count"]) == (person_a, 3)


def test_inbox_representative_face_projection_is_batched_and_explainable(api_env: ApiEnv) -> None:
    seed_library(
        api_env,
        {f"pending-{index:02d}.jpg": [(20, 20, 90, 90)] for index in range(30)},
    )
    catalog = api_env.service.catalog
    statements: list[str] = []
    catalog.connection.set_trace_callback(statements.append)
    try:
        from photo_search.persons.albums import inbox_group_page

        page = inbox_group_page(catalog, limit=30, cursor=None)
        from photo_search.persons.albums import inbox_summary

        inbox_summary(catalog)
    finally:
        catalog.connection.set_trace_callback(None)
    assert len(page["cards"]) == 30
    representative_queries = [
        statement for statement in statements
        if statement.upper().startswith("SELECT F.FACE_ID") and "F.FACE_ID IN (" in statement.upper()
    ]
    assert len(representative_queries) == 1
    plan = catalog.read_all("EXPLAIN QUERY PLAN " + representative_queries[0])
    assert plan
    group_query = next(statement for statement in statements if "SELECT * FROM CARDS" in statement.upper())
    summary_query = next(statement for statement in statements if "GROUP BY REASON, HIDDEN, LOW_QUALITY" in statement.upper())
    group_plan = catalog.read_all("EXPLAIN QUERY PLAN " + group_query)
    summary_plan = catalog.read_all("EXPLAIN QUERY PLAN " + summary_query)
    assert group_plan and summary_plan
    print("inbox group plan: " + "; ".join(str(row["detail"]) for row in group_plan if "SEARCH" in str(row["detail"]) or "SCAN" in str(row["detail"]) or "TEMP B-TREE" in str(row["detail"])))
    print("inbox summary plan rows: " + str(len(summary_plan)))
    face_ids = [card["face"]["face_id"] for card in page["cards"]]
    from photo_search.persons import albums

    old_projection_start = perf_counter()
    for face_id in face_ids:
        catalog.read_one(
            albums._SELECT_FACES + "WHERE f.face_id = ? AND " + albums._inbox_where("all", "all"),
            (face_id, *albums._VISIBILITY_PARAMETERS),
        )
    old_projection_ms = (perf_counter() - old_projection_start) * 1000
    placeholders = ", ".join("?" for _ in face_ids)
    new_projection_start = perf_counter()
    catalog.read_all(
        albums._SELECT_FACES + "WHERE f.face_id IN (" + placeholders + ") AND " + albums._inbox_where("all", "all"),
        (*face_ids, *albums._VISIBILITY_PARAMETERS),
    )
    new_projection_ms = (perf_counter() - new_projection_start) * 1000
    print(f"synthetic inbox representative projection: {len(face_ids)} rows; old-equivalent {old_projection_ms:.3f} ms / {len(face_ids)} SQL calls; batched {new_projection_ms:.3f} ms / 1 SQL call")
    print("batched representative query plan: " + " | ".join(str(row["detail"]) for row in plan))

    async def event_loop_timing(client):
        await api_env.authenticate(client)
        started = perf_counter()
        health = await client.get("/api/v1/health")
        health_ms = (perf_counter() - started) * 1000
        durations: dict[str, float] = {}

        async def timed(name: str, path: str) -> None:
            start = perf_counter()
            response = await client.get(path)
            durations[name] = (perf_counter() - start) * 1000
            assert response.status_code == 200

        await asyncio.gather(
            timed("groups", "/api/v1/faces/inbox/groups?limit=30"),
            timed("health", "/api/v1/health"),
        )
        assert health.status_code == 200
        print(f"synthetic API timing: health-alone {health_ms:.3f} ms; concurrent groups {durations['groups']:.3f} ms / health {durations['health']:.3f} ms")

    api_env.scenario(event_loop_timing)


def test_people_page_total_matches_filters_without_pagination_or_sort(api_env: ApiEnv) -> None:
    data = seed(api_env)
    extra = data["photos"]["待整理.jpg"]
    extra_face = insert_face(
        api_env.service.catalog,
        asset_id=extra["asset_id"],
        sha256=extra["sha256"],
        bbox=(220, 20, 280, 80),
        ordinal=2,
    )
    unnamed = add_person(api_env.service.catalog)
    add_link(api_env.service.catalog, extra_face, unnamed)
    add_person(api_env.service.catalog, name="叔叔")
    PersonService.open(api_env.service.catalog).set_visibility(person_ids=[data["mom"]], hidden=True)

    async def scenario(client):
        await api_env.authenticate(client)
        first_named = await client.get(
            "/api/v1/persons/browse",
            params={
                "kind": "named",
                "visibility": "visible",
                "sort": "photos_desc",
                "limit": 1,
            },
        )
        second_named = await client.get(
            "/api/v1/persons/browse",
            params={
                "kind": "named",
                "visibility": "visible",
                "sort": "photos_desc",
                "limit": 1,
                "cursor": first_named.json()["next_cursor"],
            },
        )
        unnamed_page = await client.get(
            "/api/v1/persons/browse",
            params={"kind": "unnamed", "visibility": "visible", "limit": 1},
        )
        hidden_page = await client.get(
            "/api/v1/persons/browse",
            params={"kind": "all", "visibility": "hidden", "limit": 1},
        )
        searched = await client.get(
            "/api/v1/persons/browse",
            params={"kind": "named", "visibility": "visible", "q": "叔"},
        )
        assert first_named.json()["total"] == second_named.json()["total"] == 1
        assert first_named.json()["has_more"] is False
        assert unnamed_page.json()["total"] == 1
        assert hidden_page.json()["total"] == 1
        assert searched.json()["total"] == 0

    api_env.scenario(scenario)


def test_visibility_state_persists_for_empty_people_without_listing_them(api_env: ApiEnv) -> None:
    catalog = api_env.service.catalog
    person_ids = [add_person(catalog, name=f"人物{i:02d}") for i in range(50)]
    hidden_ids = person_ids[:5]

    async def scenario(client):
        assert (await client.post("/api/v1/persons/visibility", json={"person_ids": hidden_ids, "hidden": True})).status_code == 401
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        no_csrf = await client.post(
            "/api/v1/persons/visibility", json={"person_ids": hidden_ids, "hidden": True}
        )
        assert no_csrf.status_code == 403
        initial_hide = await client.post(
            "/api/v1/persons/visibility",
            headers=headers,
            json={"person_ids": hidden_ids, "hidden": True},
        )
        assert initial_hide.status_code == 200 and initial_hide.json()["changed"] == 5
        reopened = Catalog.open(catalog.db_path)
        try:
            assert reopened.read_one("SELECT COUNT(*) AS n FROM person_hidden")["n"] == 5
        finally:
            reopened.close()
        first = await client.get("/api/v1/persons/browse", params={"visibility": "visible", "limit": 40})
        hidden = await client.get(
            "/api/v1/persons/browse", params={"visibility": "hidden", "kind": "all"}
        )
        assert first.json()["persons"] == [] and not first.json()["has_more"]
        assert hidden.json()["persons"] == [] and hidden.json()["total"] == 0
        old_default = await client.get("/api/v1/persons/browse", params={"limit": 40})
        assert old_default.json()["persons"] == [] and old_default.json()["total"] == 0
        rejected = await client.post(
            "/api/v1/persons/visibility",
            headers=headers,
            json={"person_ids": [person_ids[5], 999999], "hidden": True},
        )
        assert rejected.status_code == 404
        assert catalog.read_one("SELECT COUNT(*) AS n FROM person_hidden")["n"] == 5
        repeated = await client.post(
            "/api/v1/persons/visibility",
            headers=headers,
            json={"person_ids": hidden_ids, "hidden": True},
        )
        assert repeated.status_code == 200 and repeated.json()["changed"] == 0
        restored = await client.post(
            "/api/v1/persons/visibility",
            headers=headers,
            json={"person_ids": hidden_ids, "hidden": False},
        )
        assert restored.status_code == 200 and restored.json()["changed"] == 5
        return await client.get("/api/v1/persons/browse", params={"visibility": "visible"})

    final_page = api_env.scenario(scenario)
    assert final_page.status_code == 200
    assert catalog.read_one("SELECT COUNT(*) AS n FROM person_hidden")["n"] == 0


def test_visibility_filter_precedes_photo_count_order_and_limit(api_env: ApiEnv) -> None:
    data = seed(api_env)
    PersonService.open(api_env.service.catalog).set_visibility(
        person_ids=[data["dad"]], hidden=True
    )
    page = people_page(
        api_env.service.catalog,
        kind="named",
        q="",
        limit=1,
        cursor=None,
        sort="photos_desc",
        visibility="visible",
    )
    assert [person["person_id"] for person in page["persons"]] == [data["mom"]]


def test_visibility_follows_canonical_identity_and_survives_merge_undo(api_env: ApiEnv) -> None:
    catalog = api_env.service.catalog
    service = PersonService.open(catalog)
    source_hidden = add_person(catalog, name="隐藏来源")
    visible_target = add_person(catalog, name="可见目标")
    with catalog.write() as connection:
        connection.execute("INSERT INTO person_hidden(person_id) VALUES (?)", (source_hidden,))
    merged = service.merge(
        target_person_id=visible_target,
        target_expected_revision=1,
        sources=[{"person_id": source_hidden, "expected_revision": 1}],
        request_id="hide-merge-source",
    )
    assert catalog.read_one("SELECT person_id FROM person_hidden WHERE person_id = ?", (source_hidden,))
    visible = people_page(catalog, kind="all", q="", limit=100, cursor=None, visibility="visible")
    hidden = people_page(catalog, kind="all", q="", limit=100, cursor=None, visibility="hidden")
    assert visible["persons"] == []
    assert source_hidden not in {p["person_id"] for p in hidden["persons"]}
    service.set_visibility(person_ids=[source_hidden], hidden=True)
    assert people_page(catalog, kind="all", q="", limit=100, cursor=None, visibility="hidden")["persons"] == []
    service.set_visibility(person_ids=[source_hidden], hidden=False)
    undone = service.undo(
        operation_id=merged["operation_id"],
        request_id="hide-merge-undo",
        expected_operation_revision=merged["operation_revision"],
    )
    assert undone["changed"] is True
    hidden_after_undo = people_page(catalog, kind="all", q="", limit=100, cursor=None, visibility="hidden")
    assert hidden_after_undo["persons"] == []

    hidden_target = add_person(catalog, name="隐藏目标")
    visible_source = add_person(catalog, name="可见来源")
    with catalog.write() as connection:
        connection.execute("INSERT INTO person_hidden(person_id) VALUES (?)", (hidden_target,))
    service.merge(
        target_person_id=hidden_target,
        target_expected_revision=1,
        sources=[{"person_id": visible_source, "expected_revision": 1}],
        request_id="hide-merge-target",
    )
    assert people_page(catalog, kind="all", q="", limit=100, cursor=None, visibility="hidden")["persons"] == []


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
            ["people1", "all", "", read_filter_revision(catalog.connection), 0],
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
    assert first.status_code == page2.status_code == 200
    first_person, second_person = [response.json()["persons"][0] for response in (first, page2)]
    assert (first_person["person_id"], first_person["asset_count"]) == (data["dad"], 2)
    assert (second_person["person_id"], second_person["asset_count"]) == (data["mom"], 2)
    assert [first_person["person_id"], second_person["person_id"]] == [data["dad"], data["mom"]]
    assert scoped.status_code == 200
    assert [person["person_id"] for person in scoped.json()["persons"]] == [data["dad"]]
    assert invalid_sort.status_code == 422
    assert wrong_sort.status_code == 409 and wrong_sort.json()["code"] == "INVALID_CURSOR"
    assert changed.status_code == 200
    assert stale.status_code == 409 and stale.json()["code"] == "INVALID_CURSOR"
    assert old_default.status_code == 200
    assert old_default.json()["persons"][0]["person_id"] == data["dad"]
    assert legacy_default.status_code == 200
    assert legacy_default.json()["persons"][0]["person_id"] == data["dad"]
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


def test_merge_shared_photo_is_unique_across_pages_and_undo_restores_membership(api_env: ApiEnv) -> None:
    photos = seed_library(
        api_env,
        {
            "共同合照.jpg": [(20, 20, 90, 90), (150, 30, 220, 100)],
            "人物A.jpg": [(40, 40, 120, 120)],
            "人物B.jpg": [(50, 35, 130, 115)],
        },
    )
    catalog = api_env.service.catalog
    face_a_shared, face_b_shared = photos["共同合照.jpg"]["faces"]
    face_a_solo = photos["人物A.jpg"]["faces"][0]
    face_b_solo = photos["人物B.jpg"]["faces"][0]
    person_a = add_person(catalog, name="人物A", avatar_face_id=face_a_shared)
    person_b = add_person(catalog, name="人物B", avatar_face_id=face_b_shared)
    add_link(catalog, face_a_shared, person_a)
    add_link(catalog, face_a_solo, person_a)
    add_link(catalog, face_b_shared, person_b)
    add_link(catalog, face_b_solo, person_b)
    shared_asset_id = photos["共同合照.jpg"]["asset_id"]
    a_asset_id = photos["人物A.jpg"]["asset_id"]
    b_asset_id = photos["人物B.jpg"]["asset_id"]

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        before_a = (await client.get(f"/api/v1/persons/{person_a}/photos")).json()
        before_b = (await client.get(f"/api/v1/persons/{person_b}/photos")).json()
        assert before_a["person"]["asset_count"] == 2
        assert before_b["person"]["asset_count"] == 2
        assert {item["asset_id"] for item in before_a["assets"]} == {shared_asset_id, a_asset_id}
        assert {item["asset_id"] for item in before_b["assets"]} == {shared_asset_id, b_asset_id}

        merged = await client.post(
            "/api/v1/persons/merge",
            headers=headers,
            json={
                "request_id": "shared-photo-merge",
                "target_person_id": person_a,
                "target_expected_revision": 1,
                "sources": [{"person_id": person_b, "expected_revision": 1}],
            },
        )
        assert merged.status_code == 200

        all_pages = []
        cursor = None
        while True:
            response = await client.get(
                f"/api/v1/persons/{person_a}/photos",
                params={"limit": 1, **({"cursor": cursor} if cursor else {})},
            )
            assert response.status_code == 200
            page = response.json()
            assert page["person"]["asset_count"] == 3
            all_pages.extend(page["assets"])
            if not page["has_more"]:
                assert page["next_cursor"] is None
                break
            assert page["next_cursor"] and page["next_cursor"] != cursor
            cursor = page["next_cursor"]
        merged_ids = [item["asset_id"] for item in all_pages]
        assert len(merged_ids) == len(set(merged_ids)) == 3
        assert set(merged_ids) == {shared_asset_id, a_asset_id, b_asset_id}
        shared = next(item for item in all_pages if item["asset_id"] == shared_asset_id)
        assert {face["face_id"] for face in shared["person_faces"]} == {
            face_a_shared,
            face_b_shared,
        }
        redirected = (await client.get(f"/api/v1/persons/{person_b}/photos")).json()
        assert redirected["person"]["person_id"] == person_a
        assert redirected["person"]["asset_count"] == 3
        assert {item["asset_id"] for item in redirected["assets"]} == set(merged_ids)

        operation = merged.json()
        undone = await client.post(
            f"/api/v1/person-operations/{operation['operation_id']}/undo",
            headers=headers,
            json={
                "request_id": "shared-photo-merge-undo",
                "expected_operation_revision": operation["operation_revision"],
            },
        )
        assert undone.status_code == 200
        after_a = (await client.get(f"/api/v1/persons/{person_a}/photos")).json()
        after_b = (await client.get(f"/api/v1/persons/{person_b}/photos")).json()
        assert after_a["person"]["asset_count"] == 2
        assert after_b["person"]["asset_count"] == 2
        assert {item["asset_id"] for item in after_a["assets"]} == {shared_asset_id, a_asset_id}
        assert {item["asset_id"] for item in after_b["assets"]} == {shared_asset_id, b_asset_id}
        restored_faces = catalog.read_all(
            "SELECT face_id, person_id FROM face_person_links WHERE face_id IN (?, ?, ?, ?) "
            "AND decision = 'CONFIRMED' ORDER BY face_id, person_id",
            (face_a_shared, face_b_shared, face_a_solo, face_b_solo),
        )
        assert {(row["face_id"], int(row["person_id"])) for row in restored_faces} == {
            (face_a_shared, person_a),
            (face_a_solo, person_a),
            (face_b_shared, person_b),
            (face_b_solo, person_b),
        }

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


def test_ignored_assignment_visibility_group_cursor_and_undo(api_env: ApiEnv) -> None:
    photos = seed_library(api_env, {
        "ignored-shared.jpg": [(20, 20, 90, 90), (130, 20, 200, 90)],
        "ignored-second.jpg": [(30, 30, 100, 100)],
        "ignored-loose.jpg": [(40, 40, 110, 110)],
    })
    catalog = api_env.service.catalog
    candidate = add_person(catalog, name="候选甲")
    other_candidate = add_person(catalog, name="候选乙")
    target = add_person(catalog, name="目标人物")
    first, coface = photos["ignored-shared.jpg"]["faces"]
    second = photos["ignored-second.jpg"]["faces"][0]
    loose = photos["ignored-loose.jpg"]["faces"][0]
    add_link(catalog, first, candidate, decision="SUGGESTED", confidence=.9)
    add_link(catalog, second, candidate, decision="SUGGESTED", confidence=.8)
    add_link(catalog, coface, other_candidate, decision="SUGGESTED", confidence=.7)
    ignored_ids = [first, second, coface, loose]

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        hidden = await client.post(
            "/api/v1/faces/inbox/visibility", headers=headers,
            json={"face_ids": ignored_ids, "hidden": True, "ignore_state": "TEMPORARY"},
        )
        assert hidden.status_code == 200

        flat = (await client.get("/api/v1/faces/inbox/hidden", params={"limit": 1})).json()
        assert flat["has_more"]
        initial = (await client.get("/api/v1/faces/inbox/hidden/groups", params={"limit": 1})).json()
        assert initial["has_more"] and initial["cards"][0]["type"] == "candidate_group"
        candidate_card = initial["cards"][0]
        assert candidate_card["photo_count"] == 2
        assert candidate_card["face"]["face_id"] in {first, second}
        original_group_page = (await client.get(
            f"/api/v1/faces/inbox/hidden/groups/{candidate_card['group_key']}",
            params={"limit": 1},
        )).json()
        assert original_group_page["has_more"] and len(original_group_page["faces"]) == 1

        body = {
            "contract_version": "p3-face-correction-contracts.v1",
            "request_id": "ignored-assignment",
            "action": "assign",
            "expected_revision": 0,
            "target_person_id": target,
        }
        changed = await client.put(
            f"/api/v1/faces/{first}/assignment", headers=headers, json=body,
        )
        assert changed.status_code == 200
        assert catalog.read_one(
            "SELECT ignore_state FROM face_inbox_hidden WHERE face_id=?", (first,)
        )["ignore_state"] == "TEMPORARY"
        target_album = (await client.get(f"/api/v1/persons/{target}/photos")).json()
        assert any(
            face["face_id"] == first
            for photo in target_album["assets"]
            for face in photo["person_faces"]
        )

        assert first not in {face["face_id"] for face in (await client.get(
            "/api/v1/faces/inbox/hidden"
        )).json()["faces"]}
        assert first not in {
            card["face"]["face_id"] for card in (await client.get(
                "/api/v1/faces/inbox/hidden/groups"
            )).json()["cards"]
        }
        remaining_group = await client.get(
            f"/api/v1/faces/inbox/hidden/groups/{candidate_card['group_key']}"
        )
        assert remaining_group.status_code == 200
        assert remaining_group.json()["faces"] == []
        downgraded = (await client.get("/api/v1/faces/inbox/hidden/groups")).json()
        candidate_single = next(
            card for card in downgraded["cards"] if card["face"]["face_id"] == second
        )
        assert candidate_single["type"] == "single_face" and candidate_single["photo_count"] == 1
        assert coface in {card["face"]["face_id"] for card in downgraded["cards"]}
        assert loose in {face["face_id"] for face in (await client.get(
            "/api/v1/faces/inbox/hidden"
        )).json()["faces"]}
        stale_group_cursor = await client.get(
            "/api/v1/faces/inbox/hidden/groups",
            params={"limit": 1, "cursor": initial["next_cursor"]},
        )
        assert stale_group_cursor.status_code == 409
        # The legacy flat cursor remains an ordinary face-id keyset cursor.
        flat_next = await client.get("/api/v1/faces/inbox/hidden", params={
            "limit": 1, "cursor": flat["next_cursor"],
        })
        assert flat_next.status_code == 200

        operation = changed.json()
        undone = await client.post(
            f"/api/v1/person-operations/{operation['operation_id']}/undo",
            headers=headers,
            json={"request_id": "undo-ignored-assignment", "expected_operation_revision": operation["operation_revision"]},
        )
        assert undone.status_code == 200
        restored = (await client.get("/api/v1/faces/inbox/hidden")).json()
        assert first in {face["face_id"] for face in restored["faces"]}
        restored_groups = (await client.get("/api/v1/faces/inbox/hidden/groups")).json()
        restored_candidate = next(
            card for card in restored_groups["cards"]
            if card["type"] == "candidate_group" and card["group_key"] == candidate_card["group_key"]
        )
        assert restored_candidate["photo_count"] == 2
        assert catalog.read_one(
            "SELECT ignore_state FROM face_inbox_hidden WHERE face_id=?", (first,)
        )["ignore_state"] == "TEMPORARY"
        assert coface in {face["face_id"] for face in (await client.get(
            "/api/v1/faces/inbox/hidden"
        )).json()["faces"]}

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


def test_inbox_face_visibility_is_atomic_persistent_and_scoped_to_selected_faces(api_env: ApiEnv) -> None:
    seeded = seed_library(api_env, {
        "shared.jpg": [(20, 20, 80, 80), (110, 20, 170, 80), (200, 20, 260, 80)],
        "another.jpg": [(30, 30, 100, 100)],
    })
    catalog = api_env.service.catalog
    candidate = add_person(catalog, name="候选甲")
    hidden_a, hidden_b, visible_same_photo = seeded["shared.jpg"]["faces"]
    visible_other_photo = seeded["another.jpg"]["faces"][0]
    add_link(catalog, hidden_a, candidate, decision="SUGGESTED", confidence=0.9)
    add_link(catalog, hidden_b, candidate, decision="SUGGESTED", confidence=0.8)
    add_link(catalog, visible_other_photo, candidate, decision="SUGGESTED", confidence=0.9)
    before_links = catalog.read_all(
        "SELECT face_id, person_id, decision FROM face_person_links ORDER BY face_id, person_id, decision"
    )
    ids_to_hide = [hidden_a, hidden_b]

    async def scenario(client):
        assert (await client.get("/api/v1/faces/inbox/hidden")).status_code == 401
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        initial = (await client.get("/api/v1/faces/inbox/groups", params={"kind": "suggested"})).json()
        group = next(card for card in initial["cards"] if card["type"] == "candidate_group")
        assert group["photo_count"] == 2
        group_faces = (await client.get(
            f"/api/v1/faces/inbox/groups/{group['group_key']}", params={"kind": "suggested", "limit": 30}
        )).json()["faces"]
        assert {face["face_id"] for face in group_faces} == {
            hidden_a, hidden_b, visible_other_photo
        }
        assert (await client.get("/api/v1/faces/inbox/summary")).json()["total"] == 4

        invalid = await client.post(
            "/api/v1/faces/inbox/visibility", headers=headers,
            json={"face_ids": [hidden_a, "f" * 64], "hidden": True},
        )
        assert invalid.status_code == 422
        assert catalog.read_one("SELECT COUNT(*) AS n FROM face_inbox_hidden")["n"] == 0
        no_csrf = await client.post(
            "/api/v1/faces/inbox/visibility", json={"face_ids": ids_to_hide, "hidden": True}
        )
        assert no_csrf.status_code == 403

        hidden = await client.post(
            "/api/v1/faces/inbox/visibility", headers=headers,
            json={"face_ids": ids_to_hide, "hidden": True},
        )
        assert hidden.status_code == 200 and hidden.json()["changed"] == 2
        repeated = await client.post(
            "/api/v1/faces/inbox/visibility", headers=headers,
            json={"face_ids": ids_to_hide, "hidden": True},
        )
        assert repeated.status_code == 200 and repeated.json()["changed"] == 0
        ignored_page = (await client.get("/api/v1/faces/inbox/hidden", params={"limit": 1})).json()
        assert ignored_page["has_more"] and len(ignored_page["faces"]) == 1
        ignored_next = (await client.get(
            "/api/v1/faces/inbox/hidden",
            params={"limit": 1, "cursor": ignored_page["next_cursor"]},
        )).json()
        assert {face["face_id"] for face in ignored_page["faces"] + ignored_next["faces"]} == set(ids_to_hide)

        excluded = await client.post(
            "/api/v1/faces/inbox/visibility", headers=headers,
            json={"face_ids": [hidden_a], "hidden": True, "ignore_state": "EXCLUDED"},
        )
        assert excluded.status_code == 200 and excluded.json()["changed"] == 1
        assert excluded.json()["ignore_state"] == "EXCLUDED"
        hidden_faces = (await client.get("/api/v1/faces/inbox/hidden")).json()["faces"]
        excluded_face = next(face for face in hidden_faces if face["face_id"] == hidden_a)
        temporary_face = next(face for face in hidden_faces if face["face_id"] == hidden_b)
        assert excluded_face["ignore_state"] == "EXCLUDED"
        assert excluded_face["suggestions"] == []
        assert temporary_face["ignore_state"] == "TEMPORARY"
        assert temporary_face["suggestions"][0]["person_id"] == candidate
        restored_exclusion = await client.post(
            "/api/v1/faces/inbox/visibility", headers=headers,
            json={"face_ids": [hidden_a], "hidden": False},
        )
        assert restored_exclusion.status_code == 200 and restored_exclusion.json()["changed"] == 1
        restored_page = (await client.get("/api/v1/faces/inbox/groups")).json()
        restored_group = next(card for card in restored_page["cards"] if card["type"] == "candidate_group")
        restored_faces = (await client.get(
            f"/api/v1/faces/inbox/groups/{restored_group['group_key']}",
            params={"kind": "suggested", "limit": 30},
        )).json()["faces"]
        assert next(face for face in restored_faces if face["face_id"] == hidden_a)["suggestions"][0]["person_id"] == candidate
        hidden_temporary_again = await client.post(
            "/api/v1/faces/inbox/visibility", headers=headers,
            json={"face_ids": [hidden_a], "hidden": True, "ignore_state": "TEMPORARY"},
        )
        assert hidden_temporary_again.status_code == 200

        reopened = Catalog.open(catalog.db_path)
        try:
            assert reopened.read_one("SELECT COUNT(*) AS n FROM face_inbox_hidden")["n"] == 2
        finally:
            reopened.close()
        remaining_summary = (await client.get("/api/v1/faces/inbox/summary")).json()
        assert remaining_summary["total"] == 2
        remaining = (await client.get("/api/v1/faces/inbox/groups")).json()
        assert all(card["type"] == "single_face" for card in remaining["cards"])
        remaining_ids = {card["face"]["face_id"] for card in remaining["cards"]}
        assert remaining_ids == {visible_other_photo, visible_same_photo}
        current_links = catalog.read_all(
            "SELECT face_id, person_id, decision FROM face_person_links ORDER BY face_id, person_id, decision"
        )
        assert [tuple(row) for row in current_links] == [tuple(row) for row in before_links]

        restored = await client.post(
            "/api/v1/faces/inbox/visibility", headers=headers,
            json={"face_ids": ids_to_hide, "hidden": False},
        )
        assert restored.status_code == 200 and restored.json()["changed"] == 2
        assert (await client.get("/api/v1/faces/inbox/hidden")).json()["faces"] == []
        after_restore = (await client.get("/api/v1/faces/inbox/groups")).json()
        group_after_restore = next(card for card in after_restore["cards"] if card["type"] == "candidate_group")
        assert group_after_restore["photo_count"] == 2
        assert [tuple(row) for row in catalog.read_all(
            "SELECT face_id, person_id, decision FROM face_person_links ORDER BY face_id, person_id, decision"
        )] == [tuple(row) for row in before_links]

        all_hidden = [hidden_a, hidden_b, visible_same_photo, visible_other_photo]
        hidden_again = await client.post(
            "/api/v1/faces/inbox/visibility", headers=headers,
            json={"face_ids": all_hidden, "hidden": True},
        )
        assert hidden_again.status_code == 200 and hidden_again.json()["changed"] == 4
        snapshot = tuple(catalog.connection.iterdump())
        first_cards = (await client.get(
            "/api/v1/faces/inbox/hidden/groups", params={"limit": 1}
        )).json()
        assert first_cards["has_more"] and len(first_cards["cards"]) == 1
        candidate_group = first_cards["cards"][0]
        assert candidate_group["type"] == "candidate_group"
        assert candidate_group["photo_count"] == 2
        assert candidate_group["face"]["suggestions"][0]["person_id"] == candidate
        second_cards = (await client.get(
            "/api/v1/faces/inbox/hidden/groups",
            params={"limit": 1, "cursor": first_cards["next_cursor"]},
        )).json()
        assert len(second_cards["cards"]) == 1
        assert second_cards["cards"][0]["type"] == "single_face"
        assert second_cards["cards"][0]["face"]["face_id"] == visible_same_photo
        group_page = (await client.get(
            f"/api/v1/faces/inbox/hidden/groups/{candidate_group['group_key']}",
            params={"limit": 1},
        )).json()
        group_faces = list(group_page["faces"])
        while group_page["has_more"]:
            group_page = (await client.get(
                f"/api/v1/faces/inbox/hidden/groups/{candidate_group['group_key']}",
                params={"limit": 1, "cursor": group_page["next_cursor"]},
            )).json()
            group_faces.extend(group_page["faces"])
        assert {face["face_id"] for face in group_faces} == {hidden_a, hidden_b, visible_other_photo}
        assert tuple(catalog.connection.iterdump()) == snapshot

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
        assert left_person not in cards
        assert cards[right_person]["avatar_face_id"] == data["right"]
        page = (await client.get(f"/api/v1/persons/{left_person}/photos")).json()
        assert page["assets"] == []

    api_env.scenario(scenario)
