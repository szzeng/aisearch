"""P3-03 end to end on the real P3-02 fixture library: group, name, merge, undo, status.

The pictures are the same public-domain fixtures the P3-02 suite uses (``skimage``'s NASA
astronaut, InsightFace's own group photo and tight portrait, plus scripted variants) and every
file lives in ``%TEMP%``. The run exercises the *product* path: real detection and association
produce the unnamed groups, and this task's naming, same-name merge, explicit merge,
compensation and read-only projection are then applied on top of the real rows.

Single-face correction ("this is not 爸爸") is P3-04's write entry point. What P3-03 owns - and
what is asserted here - is that a face a human has already taken out of the automatic pool is
never pulled back in by a merge, and that the merge records exactly that as a suggestion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from p3_face_fixtures import SKIP_WITHOUT_FACE_WEIGHTS, Harness

from photo_search.face.index import FaceIndexCoordinator
from photo_search.face.service import catch_up_index
from photo_search.persons.service import PersonService

pytestmark = SKIP_WITHOUT_FACE_WEIGHTS


@pytest.fixture()
def harness(tmp_path: Path) -> Harness:
    instance = Harness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def processed(h: Harness) -> dict[str, Any]:
    h.install_weights()
    h.scan()
    return h.detect(device="cpu")


def person_of(h: Harness, relative: str) -> int:
    row = h.catalog.read_one(
        "SELECT l.person_id AS person_id FROM face_person_links AS l "
        "JOIN faces AS f ON f.face_id = l.face_id "
        "JOIN file_locations AS loc ON loc.asset_id = f.asset_id "
        "WHERE loc.relative_path = ? AND l.decision = 'CONFIRMED' "
        "ORDER BY l.person_id LIMIT 1",
        (str(relative),),
    )
    assert row is not None, f"{relative} has no effective person"
    return int(row["person_id"])


def face_of(h: Harness, relative: str) -> str:
    return str(h.faces_of(relative)[0]["face_id"])


def service_of(h: Harness) -> PersonService:
    return PersonService(h.catalog, clock=h.clock)


def person_revision(h: Harness, person_id: int) -> int:
    row = h.catalog.read_one(
        "SELECT revision FROM persons WHERE person_id = ?", (int(person_id),)
    )
    return int(row["revision"])


def person_row(h: Harness, person_id: int) -> Any:
    return h.catalog.read_one("SELECT * FROM persons WHERE person_id = ?", (int(person_id),))


def member_faces(h: Harness, person_id: int) -> set[str]:
    return {
        str(row["face_id"])
        for row in h.catalog.read_all(
            "SELECT face_id FROM face_person_links WHERE person_id = ? "
            "AND decision = 'CONFIRMED'",
            (int(person_id),),
        )
    }


def test_the_whole_person_flow_runs_on_real_faces(harness: Harness) -> None:
    processed(harness)
    h = harness
    service = service_of(h)
    groups = h.catalog.read_all("SELECT person_id, revision FROM persons")
    assert len(groups) >= 4
    named_before = int(h.summary()["named_persons"])
    unnamed_before = h.summary()["unnamed_persons"]

    astronaut_person = person_of(h, "astronaut.png")
    portrait_person = person_of(h, "portrait.png")
    assert astronaut_person != portrait_person
    portrait_before = service.get_person(portrait_person)

    # ---------------------------------------------------------------- 1. naming
    named = service.rename(
        person_id=astronaut_person,
        name="\u5b87\u822a\u5458",
        request_id="life-name-1",
        expected_revision=person_revision(h, astronaut_person),
    )
    assert named["operation_type"] == "RENAME"
    view = service.get_person(astronaut_person)
    assert view["name"] == "\u5b87\u822a\u5458"
    assert view["redirected"] is False

    # ------------------------------------------------- 2. same-name save merges
    merged = service.rename(
        person_id=portrait_person,
        name="\u5b87\u822a\u5458",
        request_id="life-name-2",
        expected_revision=person_revision(h, portrait_person),
    )
    assert merged["operation_type"] == "MERGE_BY_NAME"
    assert int(person_row(h, portrait_person)["merged_into"]) == astronaut_person
    assert service.get_person(portrait_person)["redirected"] is True
    assert service.get_person(astronaut_person)["asset_count"] > view["asset_count"]
    # Only one active person owns that name.
    active = h.catalog.read_all(
        "SELECT person_id FROM persons WHERE name_key = 'nfc1:\u5b87\u822a\u5458' "
        "AND merged_into IS NULL"
    )
    assert [int(row["person_id"]) for row in active] == [astronaut_person]

    # ------------------------------------------------- 3. undo the same-name merge
    undone = service.undo(
        operation_id=merged["operation_id"],
        request_id="life-undo-1",
        expected_operation_revision=merged["operation_revision"],
    )
    assert undone["changed"] is True
    assert person_row(h, portrait_person)["merged_into"] is None
    restored_portrait = service.get_person(portrait_person)
    assert restored_portrait["name"] is None
    assert restored_portrait["face_count"] == portrait_before["face_count"]
    assert restored_portrait["asset_count"] == portrait_before["asset_count"]

    # --------------------------- 4. a human-held face is not regrouped by a merge
    held_face = face_of(h, "portrait.png")
    with h.catalog.write() as connection:
        connection.execute(
            "INSERT INTO face_assignment_controls(face_id, mode, revision, last_operation_id, "
            "updated_at) VALUES (?, 'MANUAL_UNASSIGNED', 1, 'op-human', 1)",
            (held_face,),
        )
    members_before_explicit = member_faces(h, astronaut_person)
    explicit = service.merge(
        target_person_id=astronaut_person,
        target_expected_revision=person_revision(h, astronaut_person),
        sources=[
            {
                "person_id": portrait_person,
                "expected_revision": person_revision(h, portrait_person),
            }
        ],
        request_id="life-merge-1",
    )
    assert explicit["operation_type"] == "MERGE"
    link = h.catalog.read_one(
        "SELECT decision, reason FROM face_person_links WHERE face_id = ?", (held_face,)
    )
    assert (str(link["decision"]), str(link["reason"])) == (
        "SUGGESTED",
        "MANUAL_UNASSIGNED",
    )
    # The human-held face is not a member of the target, and the members it did have are.
    assert held_face not in member_faces(h, astronaut_person)
    assert members_before_explicit <= member_faces(h, astronaut_person)
    assert h.summary()["pending_confirmation"] >= 1

    # The independent candidate index degrades the affected faces and catches up again.
    index = FaceIndexCoordinator(h.catalog)
    published = catch_up_index(h.catalog, coordinator=index)["published"]
    assert index.index_state()["outbox"].get("PENDING", 0) == 0
    assert published >= 1

    # ------------------------------------------------------- 5. undo the merge
    restored = service.undo(
        operation_id=explicit["operation_id"],
        request_id="life-undo-2",
        expected_operation_revision=explicit["operation_revision"],
    )
    assert restored["changed"] is True
    assert person_row(h, portrait_person)["merged_into"] is None
    link = h.catalog.read_one(
        "SELECT decision, assignment_kind FROM face_person_links WHERE face_id = ?",
        (held_face,),
    )
    assert str(link["decision"]) == "CONFIRMED"
    control = h.catalog.read_one(
        "SELECT mode FROM face_assignment_controls WHERE face_id = ?", (held_face,)
    )
    assert str(control["mode"]) == "MANUAL_UNASSIGNED"

    # --------------------------------------------------------- 6. read-only status
    summary = h.summary()
    assert summary["named_persons"] == named_before + 1
    # The very same group moved from the unnamed side to the named side.
    assert summary["unnamed_persons"] == unnamed_before - 1
    # RENAME, MERGE_BY_NAME, UNDO, MERGE, UNDO.
    assert summary["recent_operations"] == 5
    assert summary["pending_confirmation"] == 0
    assert "\u5b87\u822a\u5458" not in str(summary)
    assert "nfc1:" not in str(summary)

    # A forced re-detection must not delete or re-group the face a human holds.
    h.detect(device="cpu", force=True)
    assert any(str(item["face_id"]) == held_face for item in h.faces())
    assert (
        h.catalog.read_one(
            "SELECT 1 FROM face_assignment_controls WHERE face_id = ?", (held_face,)
        )
        is not None
    )
