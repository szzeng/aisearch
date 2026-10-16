"""Focused personal-album regressions, using only isolated synthetic catalogs."""

from __future__ import annotations

import pytest
from conftest import ApiEnv
from p3_persons_page_fixtures import add_link, add_person, insert_face, seed_library, set_control

from photo_search.api.errors import ApiError
from photo_search.persons import albums, collections
from photo_search.persons.service import PersonService
from photo_search.places.revision import read_filter_revision


def test_case_insensitive_literal_name_alias_search_preserves_identity(api_env: ApiEnv) -> None:
    catalog = api_env.service.catalog
    # Seed historical case-only duplicates; read-only search must not merge them.
    upper = add_person(catalog, name="Alice")
    lower = add_person(catalog, name="alice")
    unicode_person = add_person(catalog, name="Straße张三")
    photos = seed_library(api_env, {
        f"name-member-{index}.jpg": [(20, 20, 90, 90)] for index in range(3)
    })
    for person_id, photo in zip((upper, lower, unicode_person), photos.values(), strict=True):
        add_link(catalog, photo["faces"][0], person_id)
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO person_aliases(person_id, alias, normalized_alias, created_at) VALUES (?, ?, ?, 1)",
            (upper, "OLD FRIEND", "nfc1:OLD FRIEND"),
        )

    async def scenario(client):
        await api_env.authenticate(client)
        for query, expected in (("ALi", [upper, lower]), ("old fri", [upper]),
                                ("STRASSE", [unicode_person]), ("张", [unicode_person]), ("%", [])):
            response = await client.get("/api/v1/persons/browse", params={"q": query, "sort": "photos_desc", "limit": 1})
            assert response.status_code == 200
            page = response.json()
            assert page["total"] == len(expected), (query, page)
            ids = [p["person_id"] for p in page["persons"]]
            while page["has_more"]:
                page = (await client.get("/api/v1/persons/browse", params={
                    "q": query, "sort": "photos_desc", "limit": 1, "cursor": page["next_cursor"],
                })).json()
                ids.extend(p["person_id"] for p in page["persons"])
            assert ids == expected
        # Lookup case folding never merges the two distinct persisted identities.
        assert catalog.read_one("SELECT name_key FROM persons WHERE person_id=?", (upper,))[0] == "nfc1:Alice"
        assert catalog.read_one("SELECT name_key FROM persons WHERE person_id=?", (lower,))[0] == "nfc1:alice"

    api_env.scenario(scenario)


def test_collection_count_order_is_global_distinct_and_stable_across_pages(api_env: ApiEnv) -> None:
    catalog = api_env.service.catalog
    photos = seed_library(api_env, {f"{i}.jpg": [(20, 20, 90, 90), (130, 20, 200, 90)] for i in range(3)})
    small, large, tied = [add_person(catalog) for _ in range(3)]
    add_link(catalog, photos["0.jpg"]["faces"][0], small)
    for photo in photos.values():
        add_link(catalog, photo["faces"][1], large)
    # Two faces in the same photo count as one, rather than overtaking the 3-photo person.
    duplicate = insert_face(catalog, asset_id=photos["0.jpg"]["asset_id"], sha256=photos["0.jpg"]["sha256"], bbox=(220, 20, 280, 90), ordinal=2)
    add_link(catalog, duplicate, large)
    add_link(catalog, photos["1.jpg"]["faces"][0], tied)
    add_link(catalog, photos["2.jpg"]["faces"][0], tied)
    set_control(catalog, photos["2.jpg"]["faces"][0], "MANUAL_UNASSIGNED")
    collection = collections.create_collection(catalog, "排序")["collection"]
    cid, token = collection["collection_id"], collection["instance_token"]
    collections.edit_members(catalog, cid, instance_token=token, action="add", person_ids=[small, large, tied])
    page = collections.people_page(catalog, cid, instance_token=token, limit=1)
    assert page["people"][0]["person_id"] == large
    assert page["people"][0]["asset_count"] == 3
    ids, counts = [], []
    while True:
        ids.extend(p["person_id"] for p in page["people"])
        counts.extend(p["asset_count"] for p in page["people"])
        if not page["has_more"]:
            break
        page = collections.people_page(catalog, cid, instance_token=token, limit=1, cursor=page["next_cursor"])
    assert ids == [large, small, tied]
    assert counts == [3, 1, 1]
    binding = [cid, token, collection["revision"] + 1, read_filter_revision(catalog.connection)]
    legacy = collections._encode("pcp1", binding, small)
    with pytest.raises(ApiError) as rejected:
        collections.people_page(catalog, cid, instance_token=token, cursor=legacy)
    assert rejected.value.code == "INVALID_CURSOR" and rejected.value.status == 409
    malformed = collections._encode("pcp-count1", binding, [True, small])
    with pytest.raises(ApiError) as rejected:
        collections.people_page(catalog, cid, instance_token=token, cursor=malformed)
    assert rejected.value.status == 409


def test_rename_is_live_in_both_inbox_groups_and_person_choices(api_env: ApiEnv) -> None:
    catalog = api_env.service.catalog
    photos = seed_library(api_env, {f"{i}.jpg": [(20, 20, 90, 90)] for i in range(3)})
    person = add_person(catalog)
    add_link(catalog, photos["0.jpg"]["faces"][0], person)
    for i in (1, 2):
        add_link(catalog, photos[f"{i}.jpg"]["faces"][0], person, decision="SUGGESTED")

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        groups = (await client.get("/api/v1/faces/inbox/groups")).json()["cards"]
        assert groups[0]["candidate_name"] is None
        changed = await client.put(f"/api/v1/persons/{person}/label", headers=api_env.post_headers(csrf), json={
            "request_id": "small-fix-rename", "name": "Alice", "expected_revision": 1,
        })
        assert changed.status_code == 200, changed.text
        groups = (await client.get("/api/v1/faces/inbox/groups")).json()["cards"]
        assert groups[0]["candidate_name"] == "Alice"
        face_response = (await client.get(f"/api/v1/assets/{photos['1.jpg']['asset_id']}/faces")).json()
        assert face_response["faces"][0]["suggestions"][0]["name"] == "Alice"
        choices = (await client.get("/api/v1/persons/browse", params={"kind": "all", "sort": "photos_desc"})).json()
        assert choices["persons"][0]["name"] == "Alice"
        assert choices["persons"][0]["person_id"] == person

    api_env.scenario(scenario)


def test_removed_members_keep_ignore_state_until_explicit_restore(api_env: ApiEnv) -> None:
    catalog = api_env.service.catalog
    photos = seed_library(api_env, {f"{i}.jpg": [(20, 20, 90, 90)] for i in range(2)})
    named, unnamed = add_person(catalog, name="名字"), add_person(catalog)
    for i, person in enumerate((named, unnamed)):
        add_link(catalog, photos[f"{i}.jpg"]["faces"][0], person)
    first, second = [collections.create_collection(catalog, n)["collection"] for n in ("第一组", "第二组")]
    for c in (first, second):
        collections.edit_members(catalog, c["collection_id"], instance_token=c["instance_token"], action="add", person_ids=[named, unnamed], hide_after_add=True)
    service = PersonService.open(catalog)
    service.set_visibility(person_ids=[named], hidden=True, ignore_state="EXCLUDED")
    hidden_before = [tuple(row) for row in catalog.read_all("SELECT * FROM person_hidden ORDER BY person_id")]
    before = {table: [tuple(row) for row in catalog.read_all(f"SELECT * FROM {table}")]
              for table in ("persons", "faces", "face_person_links", "face_assignment_controls", "face_exclusions")}
    cid, token = first["collection_id"], first["instance_token"]
    with pytest.raises(Exception):
        collections.edit_members(catalog, cid, instance_token=token, action="remove", person_ids=[named, 999999])
    assert [tuple(row) for row in catalog.read_all("SELECT * FROM person_hidden ORDER BY person_id")] == hidden_before
    # A failure after member deletion must roll back the removal, keeping ignore facts intact.
    catalog.connection.execute("CREATE TEMP TRIGGER fail_collection_revision BEFORE UPDATE ON person_collections BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(Exception, match="injected"):
        collections.edit_members(catalog, cid, instance_token=token, action="remove", person_ids=[named, unnamed])
    catalog.connection.execute("DROP TRIGGER fail_collection_revision")
    assert [tuple(row) for row in catalog.read_all("SELECT * FROM person_hidden ORDER BY person_id")] == hidden_before
    assert catalog.read_one("SELECT COUNT(*) FROM person_collection_members WHERE collection_id=?", (cid,))[0] == 2
    result = collections.edit_members(catalog, cid, instance_token=token, action="remove", person_ids=[named, unnamed])
    assert result["changed"] == 2
    assert catalog.read_one("SELECT COUNT(*) FROM person_collection_members WHERE collection_id=?", (cid,))[0] == 0
    assert [tuple(row) for row in catalog.read_all("SELECT * FROM person_hidden ORDER BY person_id")] == hidden_before
    for kind, person, ignore_state in (("named", named, "EXCLUDED"), ("unnamed", unnamed, "TEMPORARY")):
        visible = albums.people_page(catalog, kind=kind, q="", limit=40, cursor=None, visibility="visible")
        assert visible["persons"] == []
        hidden = albums.people_page(
            catalog, kind=kind, q="", limit=40, cursor=None, visibility="hidden", ignore_state=ignore_state,
        )
        assert [p["person_id"] for p in hidden["persons"]] == [person]
    assert len(collections.people_page(catalog, second["collection_id"], instance_token=second["instance_token"])["people"]) == 2
    restored = service.set_visibility(person_ids=[named, unnamed], hidden=False)
    assert restored["changed"] == 2 and restored["hidden"] is False
    assert catalog.read_one("SELECT COUNT(*) FROM person_hidden")[0] == 0
    for kind, person in (("named", named), ("unnamed", unnamed)):
        page = albums.people_page(catalog, kind=kind, q="", limit=40, cursor=None, visibility="visible")
        assert [p["person_id"] for p in page["persons"]] == [person]
    assert len(collections.people_page(catalog, second["collection_id"], instance_token=second["instance_token"])["people"]) == 2
    service.set_visibility(person_ids=[named], hidden=True, ignore_state="EXCLUDED")
    assert collections.edit_members(catalog, cid, instance_token=token, action="remove", person_ids=[named])["changed"] == 0
    assert catalog.read_one("SELECT ignore_state FROM person_hidden WHERE person_id=?", (named,))[0] == "EXCLUDED"
    for table, expected in before.items():
        assert [tuple(row) for row in catalog.read_all(f"SELECT * FROM {table}")] == expected


def test_removing_merged_member_preserves_ignores_and_explicit_restore_resolves_target(api_env: ApiEnv) -> None:
    catalog = api_env.service.catalog
    source, target, unrelated = [add_person(catalog) for _ in range(3)]
    collection = collections.create_collection(catalog, "合并成员")["collection"]
    cid, token = collection["collection_id"], collection["instance_token"]
    collections.edit_members(catalog, cid, instance_token=token, action="add", person_ids=[source, target], hide_after_add=True)
    service = PersonService.open(catalog)
    service.set_visibility(person_ids=[source], hidden=True, ignore_state="EXCLUDED")
    service.set_visibility(person_ids=[unrelated], hidden=True)
    hidden_before = [tuple(row) for row in catalog.read_all("SELECT * FROM person_hidden ORDER BY person_id")]
    with catalog.write() as connection:
        connection.execute("UPDATE persons SET merged_into=? WHERE person_id=?", (target, source))
    assert collections.edit_members(catalog, cid, instance_token=token, action="remove", person_ids=[target, unrelated])["changed"] == 2
    assert catalog.read_one("SELECT COUNT(*) FROM person_collection_members WHERE collection_id=?", (cid,))[0] == 0
    assert [tuple(row) for row in catalog.read_all("SELECT * FROM person_hidden ORDER BY person_id")] == hidden_before
    # Explicit restoration through the old ID addresses only the active canonical person.
    restored = service.set_visibility(person_ids=[source], hidden=False)
    assert restored["person_count"] == 1 and restored["changed"] == 1
    assert catalog.read_one("SELECT person_id FROM person_hidden WHERE person_id=?", (target,)) is None
    assert [tuple(row) for row in catalog.read_all("SELECT * FROM person_hidden ORDER BY person_id")] == [
        row for row in hidden_before if row[0] != target
    ]
    assert service.set_visibility(person_ids=[target], hidden=False)["changed"] == 0


def test_collection_reads_follow_correction_merge_and_undo(api_env: ApiEnv) -> None:
    catalog = api_env.service.catalog
    photos = seed_library(api_env, {f"{i}.jpg": [(20, 20, 90, 90)] for i in range(3)})
    source, target = add_person(catalog), add_person(catalog, name="目标")
    for i, person in enumerate((source, source, target)):
        add_link(catalog, photos[f"{i}.jpg"]["faces"][0], person)
    collection = collections.create_collection(catalog, "刷新")["collection"]
    cid, token = collection["collection_id"], collection["instance_token"]
    collections.edit_members(catalog, cid, instance_token=token, action="add", person_ids=[source, target])
    service = PersonService.open(catalog)

    def counts():
        return [(p["person_id"], p["asset_count"]) for p in
                collections.people_page(catalog, cid, instance_token=token)["people"]]

    def asset_ids():
        return {p["asset_id"] for p in collections.photo_page(
            catalog, api_env.service.media_cache(), cid, instance_token=token,
        )["assets"]}

    assert counts() == [(source, 2), (target, 1)]
    service.set_face_assignment(
        face_id=photos["0.jpg"]["faces"][0], contract_version="p3-face-correction-contracts.v1",
        action="unassign", target_person_id=None, expected_revision=0, request_id="collection-refresh-correct",
    )
    assert counts() == [(source, 1), (target, 1)]
    expected_assets = {photos[f"{i}.jpg"]["asset_id"] for i in (1, 2)}
    assert asset_ids() == expected_assets
    revisions = {row["person_id"]: row["revision"] for row in catalog.read_all("SELECT person_id, revision FROM persons")}
    merged = service.merge(
        target_person_id=target, target_expected_revision=revisions[target],
        sources=[{"person_id": source, "expected_revision": revisions[source]}], request_id="collection-refresh-merge",
    )
    assert counts() == [(target, 2)]
    assert asset_ids() == expected_assets
    service.undo(operation_id=merged["operation_id"], expected_operation_revision=merged["operation_revision"],
                 request_id="collection-refresh-undo")
    assert counts() == [(source, 1), (target, 1)]
    assert asset_ids() == expected_assets
    assert catalog.read_one("SELECT COUNT(*) FROM person_collection_members WHERE collection_id=?", (cid,))[0] == 2
