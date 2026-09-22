"""ADR-0010 §10: the merge/undo ordering defect family (P3-08B).

P3-07B §6 reproduced a real defect: ``merge(target=high id, sources=[low id])`` committed, but
its compensation hit ``UNIQUE constraint failed: face_person_links.face_id`` because the
restore wrote each person's links in id order. The frozen invariant is that a compensation
removes every effective row of the affected faces *once* and only then writes the recorded
set back, so the result cannot depend on the person id order.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from p3_person_fixtures import PersonHarness

from photo_search.catalog.errors import (
    PERSON_OPERATION_NOT_UNDOABLE,
    CatalogError,
)


@pytest.fixture()
def persons_env(tmp_path: Path) -> PersonHarness:
    harness = PersonHarness(tmp_path)
    try:
        yield harness
    finally:
        harness.close()


def _person_with_face(harness: PersonHarness, name: str, *, ordinal: int) -> tuple[int, str]:
    person = harness.add_person(name=name)
    asset = harness.add_asset(f"{name}-{ordinal}.jpg")
    face = harness.add_face(asset, ordinal=0)
    harness.add_embedding(face)
    harness.add_link(face, person, revision=2)
    return person, face


def _effective(harness: PersonHarness) -> list[tuple[str, int]]:
    rows = harness.catalog.read_all(
        "SELECT face_id, person_id FROM face_person_links WHERE decision = 'CONFIRMED' "
        "ORDER BY face_id"
    )
    return [(str(row["face_id"]), int(row["person_id"])) for row in rows]


def _link_keys(harness: PersonHarness) -> list[tuple[str, int, str, str]]:
    rows = harness.catalog.read_all(
        "SELECT face_id, person_id, assignment_kind, decision FROM face_person_links "
        "ORDER BY face_id, person_id, assignment_kind"
    )
    return [
        (
            str(row["face_id"]),
            int(row["person_id"]),
            str(row["assignment_kind"]),
            str(row["decision"]),
        )
        for row in rows
    ]


def test_merge_undo_is_independent_of_person_id_order(persons_env: PersonHarness) -> None:
    """DEF-001: the original counterexample (target id higher than the source id)."""
    harness = persons_env
    low, low_face = _person_with_face(harness, "低 id", ordinal=1)
    high, high_face = _person_with_face(harness, "高 id", ordinal=2)
    assert low < high
    before = _link_keys(harness)

    merge = harness.merge(high, [(low, 1)], "merge-high-target", target_revision=1)
    assert _effective(harness) == [(low_face, high), (high_face, high)]
    undo = harness.undo(merge["operation_id"], "undo-high-target", merge["operation_revision"])

    assert undo["changed"] is True
    assert _link_keys(harness) == before
    assert _effective(harness) == sorted([(low_face, low), (high_face, high)])
    # Every restored row moved forward; nothing was rewound.
    for row in harness.catalog.read_all("SELECT revision FROM face_person_links"):
        assert int(row["revision"]) >= 2

    # The mirrored order (target id lower than the source) behaves identically.
    merge_back = harness.merge(low, [(high, harness.revision_of(high))], "merge-low-target", target_revision=harness.revision_of(low))
    undo_back = harness.undo(
        merge_back["operation_id"], "undo-low-target", merge_back["operation_revision"]
    )
    assert undo_back["changed"] is True
    assert _effective(harness) == sorted([(low_face, low), (high_face, high)])
    for face_id in (low_face, high_face):
        rows = harness.catalog.read_all(
            "SELECT decision FROM face_person_links WHERE face_id = ? AND decision = 'CONFIRMED'",
            (face_id,),
        )
        assert len(rows) == 1


def test_multi_source_undo_matches_before_snapshot(persons_env: PersonHarness) -> None:
    """DEF-002: two multi-source merges, reverse id orders, both undo exactly."""
    harness = persons_env
    first, first_face = _person_with_face(harness, "一", ordinal=1)
    second, second_face = _person_with_face(harness, "二", ordinal=2)
    third, third_face = _person_with_face(harness, "三", ordinal=3)
    before = _link_keys(harness)

    merge = harness.merge(
        first,
        [(second, 1), (third, 1)],
        "merge-three-into-one",
        target_revision=1,
    )
    harness.undo(merge["operation_id"], "undo-three-into-one", merge["operation_revision"])
    assert _link_keys(harness) == before

    merge_back = harness.merge(
        third,
        [(first, harness.revision_of(first)), (second, harness.revision_of(second))],
        "merge-one-two-into-three",
        target_revision=harness.revision_of(third),
    )
    harness.undo(
        merge_back["operation_id"], "undo-one-two-into-three", merge_back["operation_revision"]
    )
    assert _link_keys(harness) == before
    assert _effective(harness) == sorted(
        [(first_face, first), (second_face, second), (third_face, third)]
    )


def test_undo_is_scoped_to_the_operation(persons_env: PersonHarness) -> None:
    """DEF-003: rows the operation never touched are byte-identical afterwards."""
    harness = persons_env
    target, target_face = _person_with_face(harness, "目标", ordinal=1)
    source, source_face = _person_with_face(harness, "来源", ordinal=2)
    bystander, bystander_face = _person_with_face(harness, "旁观", ordinal=3)
    untouched_before = [
        tuple(row)
        for row in harness.catalog.read_all(
            "SELECT face_id, person_id, assignment_kind, decision, confidence, reason, "
            "created_at FROM face_person_links WHERE face_id = ?",
            (bystander_face,),
        )
    ]
    merge = harness.merge(target, [(source, 1)], "merge-scoped", target_revision=1)
    harness.undo(merge["operation_id"], "undo-scoped", merge["operation_revision"])
    untouched_after = [
        tuple(row)
        for row in harness.catalog.read_all(
            "SELECT face_id, person_id, assignment_kind, decision, confidence, reason, "
            "created_at FROM face_person_links WHERE face_id = ?",
            (bystander_face,),
        )
    ]
    assert untouched_after == untouched_before
    assert _effective(harness) == sorted([(target_face, target), (source_face, source), (bystander_face, bystander)])


def test_undo_keeps_control_and_exclusions(persons_env: PersonHarness) -> None:
    """DEF-004: a ``MANUAL_UNASSIGNED`` face stays unassigned and its denial survives."""
    harness = persons_env
    target = harness.add_person(name="目标")
    target_asset = harness.add_asset("目标.jpg")
    target_face = harness.add_face(target_asset, ordinal=0)
    harness.add_embedding(target_face)
    harness.add_link(target_face, target, revision=2)
    source = harness.add_person(name="来源")
    manual_asset = harness.add_asset("控制.jpg")
    manual = harness.add_face(manual_asset, ordinal=0)
    other = harness.add_person(name="叔叔")
    harness.add_embedding(manual)
    harness.add_link(manual, source, decision="SUGGESTED", revision=2)
    harness.set_control(manual, "MANUAL_UNASSIGNED")
    harness.set_exclusion(manual, other, active=True)
    source_asset = harness.add_asset("来源.jpg")
    source_face = harness.add_face(source_asset, ordinal=0)
    harness.add_embedding(source_face)
    harness.add_link(source_face, source, revision=2)
    before = _link_keys(harness)

    merge = harness.merge(target, [(source, 1)], "merge-control", target_revision=harness.revision_of(target))
    harness.undo(merge["operation_id"], "undo-control", merge["operation_revision"])

    assert _link_keys(harness) == before
    rows = harness.catalog.read_all(
        "SELECT decision, revision FROM face_person_links WHERE face_id = ?", (manual,)
    )
    assert all(str(row["decision"]) != "CONFIRMED" for row in rows)
    assert all(int(row["revision"]) >= 2 for row in rows)
    exclusion = harness.catalog.read_one(
        "SELECT active FROM face_exclusions WHERE face_id = ? AND person_id = ?",
        (manual, other),
    )
    assert exclusion is not None and int(exclusion["active"]) == 1
    control = harness.catalog.read_one(
        "SELECT mode FROM face_assignment_controls WHERE face_id = ?", (manual,)
    )
    assert str(control["mode"]) == "MANUAL_UNASSIGNED"
    restored = harness.catalog.read_one(
        "SELECT person_id FROM face_person_links WHERE face_id = ? AND decision = 'CONFIRMED'",
        (source_face,),
    )
    assert restored is not None and int(restored["person_id"]) == source


def test_later_edit_blocks_merge_undo(persons_env: PersonHarness) -> None:
    """DEF-005: a later human edit turns the compensation into a refusal, not an overwrite."""
    harness = persons_env
    target, target_face = _person_with_face(harness, "目标", ordinal=1)
    source, source_face = _person_with_face(harness, "来源", ordinal=2)
    merge = harness.merge(target, [(source, 1)], "merge-then-edit", target_revision=1)
    from photo_search.persons.service import PersonService

    service = PersonService(harness.catalog, clock=harness.clock)
    service.set_face_assignment(
        face_id=target_face,
        contract_version="p3-face-correction-contracts.v1",
        action="unassign",
        target_person_id=None,
        expected_revision=0,
        request_id="edit-after-merge",
    )
    before = _link_keys(harness)
    with pytest.raises(CatalogError) as error:
        harness.undo(merge["operation_id"], "undo-after-edit", merge["operation_revision"])
    assert error.value.code == PERSON_OPERATION_NOT_UNDOABLE
    assert _link_keys(harness) == before
