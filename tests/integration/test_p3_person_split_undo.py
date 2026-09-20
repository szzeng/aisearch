"""P3-05B: compensating a split (ADR-0008 §7) and the frozen conflict matrix.

The compensation is a *new* ``UNDO`` operation over the recorded ``SPLIT_GROUP`` and
``FACE_CONTROL`` snapshots. Every restored revision moves strictly forward, the created
person is never deleted, and any later human edit to one of the two groups or to one
selected face refuses the whole request with zero writes.
"""

from __future__ import annotations

import pytest
from p3_split_fixtures import CONTRACT_VERSION, SplitHarness

from photo_search.catalog.errors import (
    PERSON_IDEMPOTENCY_CONFLICT,
    PERSON_OPERATION_NOT_UNDOABLE,
    PERSON_REDIRECT_CYCLE,
    CatalogError,
)


@pytest.fixture()
def env(tmp_path) -> SplitHarness:
    harness = SplitHarness(tmp_path)
    try:
        yield harness
    finally:
        harness.close()


def state(env: SplitHarness) -> dict[str, object]:
    return {
        "persons": env.catalog.read_all("SELECT * FROM persons ORDER BY person_id"),
        "links": env.links(),
        "controls": env.catalog.read_all(
            "SELECT * FROM face_assignment_controls ORDER BY face_id"
        ),
        "exclusions": env.catalog.read_all(
            "SELECT * FROM face_exclusions ORDER BY face_id, person_id"
        ),
        "operations": env.operations(),
        "items": env.catalog.read_all(
            "SELECT * FROM person_operation_items ORDER BY operation_id, item_index"
        ),
        "filter_revision": env.filter_revision(),
        "queue": env.queue_rows(),
        "index_outbox": env.index_outbox_rows(),
        "embeddings": env.embeddings(),
        "generations": env.generations(),
    }


def undo(env: SplitHarness, answer: dict, *, request_id: str = "undo-1") -> dict:
    return env.undo(str(answer["operation_id"]), request_id, int(answer["operation_revision"]))


def conflict_reasons(error: CatalogError) -> list[str]:
    return [str(item["reason"]) for item in error.details["conflicts"]]


def test_undoing_a_split_restores_the_face_source_and_avatar_and_redirects_created(
    env: SplitHarness,
) -> None:
    seeded = env.seed_group(face_count=3, name="爸爸")
    dad = seeded["person"]
    # Move the *representative* face: the split has to re-pick one, and the undo has to put
    # the original one back.
    target = seeded["faces"][2]
    before = env.bundle(dad)
    before_filter = env.filter_revision()
    answer = env.split(dad, [env.selection(target, assignment=3)], request_id="split-1")
    created = int(answer["new_person_id"])
    assert env.avatar(created) == target
    assert env.avatar(dad) == seeded["faces"][1]

    outcome = undo(env, answer)

    assert outcome["contract_version"] == CONTRACT_VERSION
    assert outcome["operation_type"] == "UNDO"
    assert outcome["undo_of_operation_id"] == answer["operation_id"]
    assert outcome["requested_source_person_id"] == dad
    assert outcome["canonical_source_person_id"] == dad
    assert outcome["new_person_id"] == created
    assert outcome["changed"] is True
    assert outcome["replayed"] is False
    assert outcome["catalog_filter_revision"] == before_filter + 2
    assert [item["entity_type"] for item in outcome["items"]] == [
        "SPLIT_GROUP",
        "SPLIT_GROUP",
        "FACE_CONTROL",
    ]
    assert env.group_item(outcome, "SOURCE")["entity_id"] == dad
    assert env.group_item(outcome, "CREATED")["merged_into"] == dad

    # The face is back where it was: automatic control again, the automatic link restored
    # and the denial lifted - with every revision strictly forward.
    face_item = env.face_item(outcome, target)
    assert face_item["mode"] == "AUTO_ALLOWED"
    assert face_item["assigned_person_id"] == dad
    assert face_item["revision"] == 2
    assert env.control(target)["mode"] == "AUTO_ALLOWED"
    restored = env.effective_link(target)
    assert (int(restored["person_id"]), str(restored["assignment_kind"])) == (dad, "AUTO")
    # 3 (seed) -> 4 (downgraded suggestion) -> 5 (split's human link) -> 6 (restored
    # automatic link). Compensation must advance beyond the live effective assignment,
    # not merely beyond the surviving suggestion row.
    assert int(restored["revision"]) == 6
    assert env.active_exclusions(target) == []
    assert env.members(dad) == set(seeded["faces"])
    assert env.avatar(dad) == target == before["avatar_face_id"]

    # The created person is kept, unnamed and redirected to the source; its id still reads.
    created_row = env.person(created)
    assert created_row["name"] is None and created_row["name_key"] is None
    assert created_row["avatar_face_id"] is None
    assert int(created_row["merged_into"]) == dad
    assert int(created_row["revision"]) == 2
    assert env.canonical(created) == dad
    view = env.view(created)
    assert (view["requested_person_id"], view["canonical_person_id"], view["redirected"]) == (
        created,
        dad,
        True,
    )
    assert env.aliases(created) == []

    # The original split is compensated, not rewritten, and the group fingerprint is back.
    original = env.operation(str(answer["operation_id"]))
    assert str(original["state"]) == "UNDONE"
    assert str(original["undone_by"]) == str(outcome["operation_id"])
    assert env.bundle(dad)["name"] == before["name"]
    assert env.filter_revision() == before_filter + 2
    assert env.filter_cause() == "PERSON_CHANGED"
    assert [(int(row["asset_id"]), str(row["cause"])) for row in env.queue_rows()] == [
        (seeded["asset_id"], "PERSON_CHANGED")
    ]
    assert env.candidate_entries()[target] == "PENDING_REPAIR"


def test_the_undo_never_touches_the_image_vector_index_or_the_face_space(
    env: SplitHarness,
) -> None:
    seeded = env.seed_group(face_count=2)
    dad = seeded["person"]
    embeddings = env.embeddings()
    generations = env.generations()
    outbox = env.index_outbox_rows()

    answer = env.split(dad, [env.selection(seeded["faces"][0], assignment=3)])
    undo(env, answer)

    assert env.index_outbox_rows() == outbox
    assert env.embeddings() == embeddings
    assert env.generations() == generations


def test_replaying_an_undo_answers_the_recorded_envelope_without_writing(
    env: SplitHarness,
) -> None:
    seeded = env.seed_group(face_count=2)
    dad = seeded["person"]
    answer = env.split(dad, [env.selection(seeded["faces"][0], assignment=3)])
    first = undo(env, answer, request_id="undo-same")
    snapshot = state(env)

    replay = env.undo(str(answer["operation_id"]), "undo-same", int(answer["operation_revision"]))

    assert replay["replayed"] is True
    assert replay["operation_id"] == first["operation_id"]
    assert replay["items"] == first["items"]
    assert replay["catalog_filter_revision"] == first["catalog_filter_revision"]
    assert state(env) == snapshot


def test_a_second_undo_and_an_undo_of_an_undo_are_refused_with_zero_writes(
    env: SplitHarness,
) -> None:
    seeded = env.seed_group(face_count=2)
    dad = seeded["person"]
    answer = env.split(dad, [env.selection(seeded["faces"][0], assignment=3)])
    second = undo(env, answer, request_id="undo-a")
    snapshot = state(env)

    with pytest.raises(CatalogError) as again:
        env.undo(str(answer["operation_id"]), "undo-b", int(answer["operation_revision"]))
    assert again.value.code == PERSON_OPERATION_NOT_UNDOABLE
    assert "status" not in again.value.details
    assert state(env) == snapshot

    with pytest.raises(CatalogError) as chained:
        env.undo(str(second["operation_id"]), "undo-c", int(second["operation_revision"]))
    assert chained.value.code == PERSON_OPERATION_NOT_UNDOABLE
    assert "expected_revision" in chained.value.details
    assert state(env) == snapshot

    with pytest.raises(CatalogError) as stale:
        env.undo(str(answer["operation_id"]), "undo-d", int(answer["operation_revision"]) + 1)
    assert stale.value.code == PERSON_OPERATION_NOT_UNDOABLE
    assert state(env) == snapshot

    with pytest.raises(CatalogError) as unknown:
        env.undo("f" * 64, "undo-e", 1)
    assert unknown.value.code == "PERSON_NOT_FOUND"
    assert state(env) == snapshot


def test_a_split_undo_refuses_a_non_contiguous_item_sequence(
    env: SplitHarness,
) -> None:
    seeded = env.seed_group(face_count=2)
    answer = env.split(
        seeded["person"],
        [env.selection(seeded["faces"][0], assignment=3)],
        request_id="item-gap-split",
    )
    with env.catalog.write() as connection:
        connection.execute(
            "UPDATE person_operation_items SET item_index = 3 "
            "WHERE operation_id = ? AND item_index = 2",
            (str(answer["operation_id"]),),
        )
    snapshot = state(env)

    with pytest.raises(CatalogError) as error:
        env.undo(
            str(answer["operation_id"]),
            "item-gap-undo",
            int(answer["operation_revision"]),
        )

    assert error.value.code == PERSON_OPERATION_NOT_UNDOABLE
    assert error.value.details["reason"] == "item_order"
    assert state(env) == snapshot


def test_an_undo_request_id_bound_to_a_different_payload_is_refused(
    env: SplitHarness,
) -> None:
    first_group = env.seed_group(face_count=2, name="爸爸", asset_name="合照 一.jpg")
    second_group = env.seed_group(face_count=2, name="妈妈", asset_name="合照 二.jpg")
    first = env.split(
        first_group["person"], [env.selection(first_group["faces"][0], assignment=3)], request_id="r1"
    )
    second = env.split(
        second_group["person"],
        [env.selection(second_group["faces"][0], assignment=3)],
        request_id="r2",
    )
    # The same undo key is first used for the *other* split, so the second attempt is a
    # reused key with a different payload.
    undo(env, first, request_id="shared")
    snapshot = state(env)

    with pytest.raises(CatalogError) as conflict:
        env.undo(str(second["operation_id"]), "shared", int(second["operation_revision"]))

    assert conflict.value.code == PERSON_IDEMPOTENCY_CONFLICT
    assert state(env) == snapshot


@pytest.mark.parametrize(
    ("later_fact", "reason"),
    [
        ("rename_source", "group_changed"),
        ("rename_created", "group_changed"),
        ("merge_source", "group_redirected"),
        ("merge_created", "group_redirected"),
        ("second_split", "group_changed"),
        ("new_confirmed_member", "effective_members_changed"),
        ("face_assign", "face_control_changed"),
        ("face_missing", "face_missing"),
    ],
)
def test_a_later_edit_of_a_group_or_a_selected_face_refuses_the_undo(
    env: SplitHarness, later_fact: str, reason: str
) -> None:
    seeded = env.seed_group(face_count=3)
    dad = seeded["person"]
    target, sibling, keeper = seeded["faces"]
    answer = env.split(dad, [env.selection(target, assignment=3)], request_id="conflict-1")
    created = int(answer["new_person_id"])
    other = env.add_person(name="叔叔")

    if later_fact == "rename_source":
        env.rename(dad, "老爸", "later-1", env.revision_of(dad))
    elif later_fact == "rename_created":
        env.rename(created, "新名字", "later-2", env.revision_of(created))
    elif later_fact == "merge_source":
        env.merge(other, [(dad, env.revision_of(dad))], "later-3", target_revision=env.revision_of(other))
    elif later_fact == "merge_created":
        env.merge(other, [(created, env.revision_of(created))], "later-4", target_revision=env.revision_of(other))
    elif later_fact == "second_split":
        env.split(dad, [env.selection(sibling, assignment=3)], request_id="later-5")
    elif later_fact == "new_confirmed_member":
        newcomer = env.add_face(seeded["asset_id"], ordinal=7)
        env.add_embedding(newcomer)
        env.add_link(newcomer, dad, decision="CONFIRMED", assignment_kind="AUTO", revision=1)
    elif later_fact == "face_assign":
        env.edit(target, "assign", expected_revision=1, target_person_id=other)
    else:
        env.purge_face(target)
    snapshot = state(env)

    with pytest.raises(CatalogError) as error:
        undo(env, answer, request_id="conflict-undo")

    assert error.value.code == PERSON_OPERATION_NOT_UNDOABLE
    assert reason in conflict_reasons(error.value)
    assert state(env) == snapshot
    assert keeper not in str(error.value.details)


def test_a_suggestion_only_or_unrelated_change_never_blocks_the_undo(
    env: SplitHarness,
) -> None:
    seeded = env.seed_group(face_count=3)
    dad = seeded["person"]
    target, sibling, keeper = seeded["faces"]
    answer = env.split(dad, [env.selection(target, assignment=3)], request_id="soft-1")
    created = int(answer["new_person_id"])
    unrelated = env.add_person(name="叔叔")

    # A machine suggestion that no human confirmed, plus an edit of an unrelated person.
    env.add_suggestion(keeper, created)
    env.rename(unrelated, "大伯", "soft-2", env.revision_of(unrelated))

    outcome = undo(env, answer, request_id="soft-3")

    assert outcome["operation_type"] == "UNDO"
    assert env.members(dad) == {target, sibling, keeper}
    assert env.control(target) is not None and env.control(target)["mode"] == "AUTO_ALLOWED"
    assert env.active_exclusions(target) == []
    assert env.canonical(created) == dad
    # The untouched suggestion is still there: an undo lifts only what the split wrote.
    assert {
        (int(row["person_id"]), str(row["decision"])) for row in env.face_links(keeper)
    } >= {(dad, "CONFIRMED"), (created, "SUGGESTED")}


def test_a_broken_redirect_chain_keeps_its_own_frozen_code(env: SplitHarness) -> None:
    seeded = env.seed_group(face_count=2)
    dad = seeded["person"]
    answer = env.split(dad, [env.selection(seeded["faces"][0], assignment=3)])
    created = int(answer["new_person_id"])
    with env.catalog.write() as connection:
        connection.execute("UPDATE persons SET merged_into = ? WHERE person_id = ?", (created, dad))
        connection.execute("UPDATE persons SET merged_into = ? WHERE person_id = ?", (dad, created))
    snapshot = state(env)

    with pytest.raises(CatalogError) as error:
        undo(env, answer, request_id="cycle-1")

    assert error.value.code == PERSON_REDIRECT_CYCLE
    assert state(env) == snapshot
