"""P3-03: idempotency, human control, redirects, candidate repair and compensation.

This is the named fault/behaviour half of the task card: a replayed request, a conflicting
replay, a stale revision, a redirect cycle, a face a human is holding, a merge that must not
resurrect it, the independent candidate index catching up, and an undo that either restores
everything or refuses the whole request.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from p3_person_fixtures import PersonHarness

from photo_search.catalog.errors import CatalogError
from photo_search.face.contract import FACE_PROCESSOR_ID, FACE_SPACE_ID
from photo_search.persons.contracts import name_key_for


@pytest.fixture()
def persons_env(tmp_path: Path) -> PersonHarness:
    harness = PersonHarness(tmp_path)
    try:
        yield harness
    finally:
        harness.close()


def group_with_faces(h: PersonHarness, name: str | None, faces: int) -> tuple[int, list[str]]:
    person = h.add_person(name=name)
    face_ids = []
    for index in range(faces):
        asset = h.add_asset(f"{name or 'unnamed'}-{index}.jpg")
        face = h.add_face(asset)
        h.add_embedding(face)
        h.add_link(face, person)
        face_ids.append(face)
    return person, face_ids


# ------------------------------------------------------------------------ idempotency
def test_a_replayed_request_returns_the_recorded_operation(persons_env: PersonHarness) -> None:
    h = persons_env
    person, _ = group_with_faces(h, None, 1)
    revision = h.revision_of(person)
    first = h.rename(person, "\u5988\u5988", "req-same", revision)

    # The same intent, spelled with stray whitespace: the normalised payload is identical.
    second = h.rename(person, "\u5988\u5988 ", "req-same", revision)

    assert second["replayed"] is True
    assert second["operation_id"] == first["operation_id"]
    assert second["operation_revision"] == first["operation_revision"]
    assert len(h.operations()) == 1


def test_the_same_request_id_with_another_payload_is_a_conflict_with_zero_writes(
    persons_env: PersonHarness,
) -> None:
    h = persons_env
    person, _ = group_with_faces(h, None, 1)
    revision = h.revision_of(person)
    h.rename(person, "\u5988\u5988", "req-x", revision)
    before = h.bundle(person)
    operations = [str(row["operation_id"]) for row in h.operations()]

    with pytest.raises(CatalogError) as error:
        h.rename(person, "\u7238\u7238", "req-x", revision)

    assert error.value.code == "PERSON_IDEMPOTENCY_CONFLICT"
    assert h.bundle(person) == before
    assert [str(row["operation_id"]) for row in h.operations()] == operations


def test_the_gate_runs_before_the_revision_check(persons_env: PersonHarness) -> None:
    """A replay must not become a conflict just because the state moved on afterwards."""
    h = persons_env
    person, _ = group_with_faces(h, None, 1)
    first = h.rename(person, "\u5988\u5988", "req-y", h.revision_of(person))
    h.rename(person, "\u7238\u7238", "req-z", h.revision_of(person))

    replay = h.rename(person, "\u5988\u5988", "req-y", 1)

    assert replay["replayed"] is True
    assert replay["operation_id"] == first["operation_id"]
    assert replay["status"] == "COMMITTED"


def test_a_stale_revision_is_refused_and_writes_nothing(persons_env: PersonHarness) -> None:
    h = persons_env
    person, _ = group_with_faces(h, None, 1)
    before = h.bundle(person)

    with pytest.raises(CatalogError) as error:
        h.rename(person, "\u5988\u5988", "req-stale", h.revision_of(person) + 3)

    assert error.value.code == "PERSON_REVISION_CONFLICT"
    assert error.value.details["person_id"] == person
    assert error.value.details["expected_revision"] == 4
    assert h.bundle(person) == before
    assert h.operations() == []


def test_a_redirect_cycle_is_reported_to_the_caller(persons_env: PersonHarness) -> None:
    h = persons_env
    first, _ = group_with_faces(h, None, 1)
    second = h.add_person()
    with h.catalog.write() as connection:
        connection.execute(
            "UPDATE persons SET merged_into = ? WHERE person_id = ?", (second, first)
        )
        connection.execute(
            "UPDATE persons SET merged_into = ? WHERE person_id = ?", (first, second)
        )

    with pytest.raises(CatalogError) as error:
        h.view(first)
    assert error.value.code == "PERSON_REDIRECT_CYCLE"
    with pytest.raises(CatalogError) as rename_error:
        h.rename(first, "\u5988\u5988", "req-cycle", 1)
    assert rename_error.value.code == "PERSON_REDIRECT_CYCLE"
    assert h.operations() == []


# --------------------------------------------------------------------- human control
def test_a_manual_unassigned_face_is_never_regrouped_by_a_merge(
    persons_env: PersonHarness,
) -> None:
    h = persons_env
    target, target_faces = group_with_faces(h, "\u7238\u7238", 1)
    source, source_faces = group_with_faces(h, None, 1)
    h.set_control(source_faces[0], "MANUAL_UNASSIGNED")

    h.merge(
        target,
        [(source, h.revision_of(source))],
        "req-locked",
        target_revision=h.revision_of(target),
    )

    link = h.catalog.read_one(
        "SELECT decision, reason, assignment_kind FROM face_person_links "
        "WHERE face_id = ?",
        (source_faces[0],),
    )
    assert (str(link["decision"]), str(link["reason"])) == ("SUGGESTED", "MANUAL_UNASSIGNED")
    view = h.view(target)
    assert view["face_count"] == 1
    assert view["asset_count"] == 1
    assert h.summary()["pending_confirmation"] == 1
    # The human row itself is untouched by the machine operation.
    control = h.catalog.read_one(
        "SELECT mode FROM face_assignment_controls WHERE face_id = ?", (source_faces[0],)
    )
    assert str(control["mode"]) == "MANUAL_UNASSIGNED"
    assert target_faces[0] in [
        str(row["face_id"])
        for row in h.catalog.read_all(
            "SELECT face_id FROM face_person_links WHERE person_id = ? "
            "AND decision = 'CONFIRMED'",
            (target,),
        )
    ]


def test_an_active_exclusion_downgrades_the_merged_link(persons_env: PersonHarness) -> None:
    h = persons_env
    target, _ = group_with_faces(h, "\u7238\u7238", 1)
    source, source_faces = group_with_faces(h, None, 1)
    h.set_exclusion(source_faces[0], target)

    h.merge(
        target,
        [(source, h.revision_of(source))],
        "req-excluded",
        target_revision=h.revision_of(target),
    )

    link = h.catalog.read_one(
        "SELECT decision, reason FROM face_person_links WHERE face_id = ?", (source_faces[0],)
    )
    assert (str(link["decision"]), str(link["reason"])) == ("SUGGESTED", "HUMAN_CONFLICT")
    # The denial row itself is preserved, not deleted.
    exclusion = h.catalog.read_one(
        "SELECT active FROM face_exclusions WHERE face_id = ? AND person_id = ?",
        (source_faces[0], target),
    )
    assert int(exclusion["active"]) == 1
    assert h.summary()["pending_confirmation"] == 1


# -------------------------------------------------------------- identity and projections
def test_an_old_id_resolves_to_the_canonical_group_and_counts_photos_by_asset(
    persons_env: PersonHarness,
) -> None:
    h = persons_env
    shared_asset = h.add_asset("together.jpg")
    first_face = h.add_face(shared_asset, ordinal=0)
    second_face = h.add_face(shared_asset, ordinal=1)
    target, _ = group_with_faces(h, "\u7238\u7238", 1)
    source = h.add_person()
    h.add_link(first_face, source)
    h.add_link(second_face, source)

    h.merge(
        target,
        [(source, h.revision_of(source))],
        "req-count",
        target_revision=h.revision_of(target),
    )

    view = h.view(source)
    assert view["requested_person_id"] == source
    assert view["canonical_person_id"] == target
    assert view["redirected"] is True
    assert view["person_id"] == target
    assert view["face_count"] == 3
    # Two faces of the same photo are still one photo.
    assert view["asset_count"] == 2


def test_the_target_keeps_its_avatar_and_only_gains_one_inside_its_own_space(
    persons_env: PersonHarness,
) -> None:
    h = persons_env
    target = h.add_person(name="\u7238\u7238")
    source = h.add_person()
    asset = h.add_asset()
    foreign_face = h.add_face(asset, ordinal=0, face_pixels=900.0)
    space_face = h.add_face(asset, ordinal=1, face_pixels=90.0)
    h.add_embedding(foreign_face, space_id="some-other-space:1")
    h.add_embedding(space_face)
    h.add_link(foreign_face, source)
    h.add_link(space_face, source)

    h.merge(
        target,
        [(source, h.revision_of(source))],
        "req-avatar",
        target_revision=h.revision_of(target),
    )

    assert str(h.person(target)["avatar_face_id"]) == space_face
    assert str(FACE_SPACE_ID) in str(
        h.catalog.read_one(
            "SELECT space_id FROM face_embeddings WHERE face_id = ?", (space_face,)
        )["space_id"]
    )
    assert FACE_PROCESSOR_ID is not None

    # An existing avatar is never replaced by a merge.
    other_asset = h.add_asset("later.jpg")
    other_face = h.add_face(other_asset, face_pixels=500.0)
    h.add_embedding(other_face)
    third = h.add_person()
    h.add_link(other_face, third)
    h.merge(
        target,
        [(third, h.revision_of(third))],
        "req-avatar-keep",
        target_revision=h.revision_of(target),
    )
    assert str(h.person(target)["avatar_face_id"]) == space_face


def test_a_merge_degrades_the_affected_candidates_and_leaves_the_image_index_alone(
    persons_env: PersonHarness,
) -> None:
    h = persons_env
    target, target_faces = group_with_faces(h, "\u7238\u7238", 1)
    source, source_faces = group_with_faces(h, None, 1)
    faces = target_faces + source_faces
    h.seed_candidate_index(faces)
    assert h.index_outbox_rows() == 0

    h.merge(
        target,
        [(source, h.revision_of(source))],
        "req-candidates",
        target_revision=h.revision_of(target),
    )

    assert h.candidate_entries() == {face: "PENDING_REPAIR" for face in faces}
    assert {str(row["state"]) for row in h.face_outbox_rows()} == {"PENDING"}
    # The image index and its outbox are untouched: the two namespaces stay apart.
    assert h.index_outbox_rows() == 0
    assert (
        h.catalog.read_one("SELECT COUNT(*) AS total FROM index_entries")["total"] == 0
    )


def test_face_summary_reports_the_person_knowledge_counts(persons_env: PersonHarness) -> None:
    h = persons_env
    named, _ = group_with_faces(h, None, 1)
    group_with_faces(h, None, 1)
    h.rename(named, "\u5988\u5988", "req-summary", h.revision_of(named))

    summary = h.summary()

    assert summary["named_persons"] == 1
    assert summary["unnamed_persons"] == 1
    assert summary["persons"] == 2
    assert summary["recent_operations"] == 1
    assert summary["aliases"] == 0
    serialized = str(summary)
    assert "\u5988\u5988" not in serialized
    assert name_key_for("\u5988\u5988") not in serialized


def test_an_exclusion_naming_a_merged_person_still_resolves_to_the_target(
    persons_env: PersonHarness,
) -> None:
    """An exclusion written against a *source* id must survive that source being merged."""
    h = persons_env
    target, _ = group_with_faces(h, "\u7238\u7238", 1)
    source, source_faces = group_with_faces(h, None, 1)
    h.set_exclusion(source_faces[0], source)

    h.merge(
        target,
        [(source, h.revision_of(source))],
        "req-exclusion-redirect",
        target_revision=h.revision_of(target),
    )

    link = h.catalog.read_one(
        "SELECT decision, reason FROM face_person_links WHERE face_id = ?", (source_faces[0],)
    )
    assert (str(link["decision"]), str(link["reason"])) == ("SUGGESTED", "HUMAN_CONFLICT")
