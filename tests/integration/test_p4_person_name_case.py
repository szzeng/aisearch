"""Case-insensitive name saves with synthetic authority, API and undo evidence."""

from pathlib import Path

import pytest
from conftest import ApiEnv
from p3_person_fixtures import PersonHarness
from p3_persons_page_fixtures import add_link, add_person, seed_library

from photo_search.catalog.errors import CatalogError
from photo_search.persons import operations


@pytest.fixture()
def h(tmp_path: Path):
    harness = PersonHarness(tmp_path)
    try:
        yield harness
    finally:
        harness.close()


@pytest.mark.parametrize(
    ("existing", "submitted"),
    [("Alice", "alice"), ("alice", "ALICE"), ("Straße", "STRASSE"),
     ("Élodie 张三", "  e\u0301LODIE\t张三  "), ("Σ", "ς")],
)
def test_case_variants_merge_into_existing_person_and_replay(h, existing, submitted):
    target = h.add_person(name=existing)
    source = h.add_person()
    face = h.add_face(h.add_asset())
    h.add_link(face, source, assignment_kind="MANUAL")
    before_filter = h.filter_revision()

    merged = h.rename(source, submitted, "case-merge", 1)

    assert merged["operation_type"] == "MERGE_BY_NAME"
    assert h.view(source)["canonical_person_id"] == target
    assert h.person(target)["name"] == existing
    assert h.person(target)["name_key"] == "nfc1:" + existing
    link = h.links()[0]
    assert (link["person_id"], link["assignment_kind"], link["decision"]) == (
        target, "MANUAL", "CONFIRMED",
    )
    assert h.filter_revision() == before_filter + 1
    # Replay precedes resolution/revision, even after the target is renamed.
    h.rename(target, "Different", "later-rename", h.revision_of(target))
    before = tuple(h.catalog.connection.iterdump())
    replay = h.rename(source, submitted, "case-merge", 1)
    assert replay["replayed"] and replay["operation_id"] == merged["operation_id"]
    assert tuple(h.catalog.connection.iterdump()) == before
    with pytest.raises(CatalogError) as error:
        h.rename(source, submitted.swapcase(), "case-merge", 1)
    assert error.value.code == "PERSON_IDEMPOTENCY_CONFLICT"
    assert tuple(h.catalog.connection.iterdump()) == before


def test_own_case_change_updates_display_and_exact_save_is_noop(h):
    person = h.add_person(name="Alice")
    face = h.add_face(h.add_asset())
    h.add_link(face, person)
    changed = h.service.rename(
        person_id=person, name="ALICE", request_id="case-display",
        expected_revision=1, merge_by_name=False,
    )
    assert changed["operation_type"] == "RENAME" and changed["changed"]
    assert h.person(person)["name"] == "ALICE"
    assert h.person(person)["name_key"] == "nfc1:ALICE"
    assert h.view(person)["face_count"] == 1
    before_filter = h.filter_revision()
    queues = [tuple(row) for row in h.queue_rows()]
    noop = h.rename(person, " ALICE ", "exact-noop", 2)
    assert not noop["changed"] and h.revision_of(person) == 2
    assert h.filter_revision() == before_filter
    assert [tuple(row) for row in h.queue_rows()] == queues
    undone = h.undo(changed["operation_id"], "undo-display", changed["operation_revision"])
    assert undone["changed"] and h.revision_of(person) == 3
    assert h.person(person)["name"] == "Alice"
    assert h.person(person)["name_key"] == "nfc1:Alice"


@pytest.mark.parametrize("submitted, expected_index", [("alice", 1), ("aLiCe", 0)])
def test_legacy_duplicates_have_deterministic_target_and_do_not_mask_self(
    h, submitted, expected_index,
):
    others = [h.add_person(name="Alice"), h.add_person(name="alice")]
    source = h.add_person(name="ALICE")
    before = tuple(h.catalog.connection.iterdump())
    with pytest.raises(CatalogError) as error:
        h.service.rename(
            person_id=source, name=submitted, request_id="legacy-collision",
            expected_revision=1, merge_by_name=False,
        )
    assert error.value.details == {
        "reason": "NAME_IN_USE", "target_person_id": others[expected_index],
    }
    assert tuple(h.catalog.connection.iterdump()) == before
    # Saving the existing exact spelling must still offer another case-only duplicate.
    with pytest.raises(CatalogError) as error:
        h.service.rename(
            person_id=source, name="ALICE", request_id="legacy-self",
            expected_revision=1, merge_by_name=False,
        )
    assert error.value.details["target_person_id"] == others[0]
    assert tuple(h.catalog.connection.iterdump()) == before


def test_merge_undo_restores_legacy_spelling_links_and_manual_denials(h):
    target, source = h.add_person(name="Alice"), h.add_person(name="alice")
    faces = [h.add_face(h.add_asset(f"{i}.jpg")) for i in range(3)]
    for face in faces:
        h.add_link(face, source, assignment_kind="MANUAL")
    h.set_control(faces[0], "MANUAL_UNASSIGNED")
    h.set_exclusion(faces[1], target)
    controls = [tuple(row) for row in h.catalog.read_all("SELECT * FROM face_assignment_controls")]
    exclusions = [tuple(row) for row in h.catalog.read_all("SELECT * FROM face_exclusions")]
    merged = h.rename(source, "alice", "legacy-merge", 1)
    links = {row["face_id"]: row for row in h.links()}
    assert links[faces[0]]["reason"] == "MANUAL_UNASSIGNED"
    assert links[faces[1]]["reason"] == "HUMAN_CONFLICT"
    assert links[faces[2]]["decision"] == "CONFIRMED"
    assert h.view(source)["asset_count"] == 1
    h.undo(merged["operation_id"], "legacy-undo", merged["operation_revision"])
    assert h.person(source)["merged_into"] is None
    assert h.person(target)["name_key"] == "nfc1:Alice"
    assert h.person(source)["name_key"] == "nfc1:alice"
    assert h.revision_of(source) == 3
    # A target with no restored person fields may keep its merge revision; never rewind.
    assert h.revision_of(target) >= 2
    assert all(row["person_id"] == source and row["decision"] == "CONFIRMED"
               and row["assignment_kind"] == "MANUAL" for row in h.links())
    assert [tuple(row) for row in h.catalog.read_all("SELECT * FROM face_assignment_controls")] == controls
    assert [tuple(row) for row in h.catalog.read_all("SELECT * FROM face_exclusions")] == exclusions


def test_case_merge_later_edit_blocks_undo_and_stale_save_writes_nothing(h):
    target, source = h.add_person(name="Alice"), h.add_person()
    merged = h.rename(source, "ALICE", "merge", 1)
    h.rename(target, "New", "later", 2)
    before = tuple(h.catalog.connection.iterdump())
    with pytest.raises(CatalogError) as error:
        h.undo(merged["operation_id"], "blocked-undo", merged["operation_revision"])
    assert error.value.code == "PERSON_OPERATION_NOT_UNDOABLE"
    assert tuple(h.catalog.connection.iterdump()) == before
    with pytest.raises(CatalogError) as error:
        h.rename(source, "NEW", "stale", 1)
    assert error.value.code == "PERSON_REVISION_CONFLICT"
    assert tuple(h.catalog.connection.iterdump()) == before


def test_case_merge_failure_rolls_back_all_authority_and_journal(h, monkeypatch):
    h.add_person(name="Alice")
    source = h.add_person(name="Old")
    face = h.add_face(h.add_asset())
    h.add_link(face, source)
    before = tuple(h.catalog.connection.iterdump())

    def reject(*args, **kwargs):
        raise RuntimeError("injected close failure")

    monkeypatch.setattr(operations, "_close_operation", reject)
    with pytest.raises(RuntimeError, match="injected close failure"):
        h.rename(source, "ALICE", "rollback", 1)
    assert tuple(h.catalog.connection.iterdump()) == before


@pytest.mark.parametrize("existing, submitted", [("Ａlice", "Alice"), ("Alice", "Alic")])
def test_compatibility_and_fuzzy_names_stay_separate(h, existing, submitted):
    target, source = h.add_person(name=existing), h.add_person()
    result = h.rename(source, submitted, "separate", 1)
    assert result["operation_type"] == "RENAME"
    assert h.person(source)["merged_into"] is None
    assert h.person(target)["name"] == existing


def test_alias_and_merged_source_names_do_not_own_active_names(h):
    target, old = h.add_person(name="Target"), h.add_person(name="Alice")
    h.merge(target, [(old, 1)], "old-merge", target_revision=1)
    person = h.add_person()
    result = h.rename(person, "ALICE", "alias-is-not-identity", 1)
    assert result["operation_type"] == "RENAME"
    assert h.view(person)["canonical_person_id"] == person
    assert h.person(target)["name"] == "Target"


def test_personal_label_case_collision_then_explicit_merge_and_undo(api_env: ApiEnv):
    photo = seed_library(api_env, {"synthetic.jpg": [(20, 20, 90, 90)]})["synthetic.jpg"]
    catalog = api_env.service.catalog
    target, source = add_person(catalog, name="Alice"), add_person(catalog, name="alice")
    add_link(catalog, photo["faces"][0], target)

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        before = tuple(catalog.connection.iterdump())
        collision = await client.put(
            f"/api/v1/persons/{source}/label", headers=headers,
            json={"request_id": "ui-case", "name": "ALICE", "expected_revision": 1},
        )
        assert collision.status_code == 409
        assert collision.json()["details"] == {"reason": "NAME_IN_USE", "target_person_id": target}
        assert tuple(catalog.connection.iterdump()) == before
        response = await client.post(
            "/api/v1/persons/merge", headers=headers,
            json={"request_id": "ui-explicit", "target_person_id": target,
                  "target_expected_revision": 1, "sources": [{"person_id": source, "expected_revision": 1}]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        browse = (await client.get("/api/v1/persons/browse", params={"q": "ALICE"})).json()
        assert [p["person_id"] for p in browse["persons"]] == [target]
        undo = await client.post(
            f"/api/v1/person-operations/{body['operation_id']}/undo", headers=headers,
            json={"request_id": "ui-undo", "expected_operation_revision": body["operation_revision"]},
        )
        assert undo.status_code == 200, undo.text
        # The legacy name endpoint uses exactly the same Unicode collision lookup.
        legacy = await client.put(
            f"/api/v1/persons/{source}/name", headers=headers,
            json={"request_id": "legacy-api", "name": "aLiCe", "expected_revision": 3},
        )
        assert legacy.status_code == 200, legacy.text
        assert legacy.json()["operation_type"] == "MERGE_BY_NAME"

    api_env.scenario(scenario)
