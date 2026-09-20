"""P3-05B: the split closure of ADR-0008 (shape, eligibility, authority, derived order).

Every case runs on a ``%TEMP%`` catalog: no model, no GPU, no real photo library. The
harness seeds the authority rows directly, so the matrix of "which face may be split" is
tested as a fact about SQLite rather than as a hope about a detector run.
"""

from __future__ import annotations

import pytest
from p3_split_fixtures import CONTRACT_VERSION, SplitHarness

from photo_search.catalog.errors import (
    FACE_ASSIGNMENT_CONFLICT,
    FACE_NOT_FOUND,
    PERSON_BATCH_TOO_LARGE,
    PERSON_IDEMPOTENCY_CONFLICT,
    PERSON_REDIRECT_CYCLE,
    PERSON_REVISION_CONFLICT,
    PERSON_SPLIT_CONFLICT,
    CatalogError,
)

#: The shared caller-shape code of ``CatalogError``; the catalog layer deliberately keeps it
#: as a literal because it is not part of the frozen catalog-code set.
VALIDATION_ERROR = "VALIDATION_ERROR"


@pytest.fixture()
def env(tmp_path) -> SplitHarness:
    harness = SplitHarness(tmp_path)
    try:
        yield harness
    finally:
        harness.close()


def assert_failure(env: SplitHarness, code: str, **kwargs) -> CatalogError:
    with pytest.raises(CatalogError) as error:
        env.split(**kwargs)
    assert error.value.code == code
    return error.value


def library_state(env: SplitHarness) -> dict[str, object]:
    """Everything a refused or replayed split must leave untouched."""
    return {
        "persons": env.catalog.read_all("SELECT * FROM persons ORDER BY person_id"),
        "links": env.links(),
        "controls": env.catalog.read_all(
            "SELECT * FROM face_assignment_controls ORDER BY face_id"
        ),
        "exclusions": env.catalog.read_all("SELECT * FROM face_exclusions ORDER BY face_id, person_id"),
        "operations": env.operations(),
        "items": env.catalog.read_all(
            "SELECT * FROM person_operation_items ORDER BY operation_id, item_index"
        ),
        "filter_revision": env.filter_revision(),
        "queue": env.queue_rows(),
        "index_outbox": env.index_outbox_rows(),
        "candidates": env.candidate_entries(),
    }


# ------------------------------------------------------------------ the happy closure
def test_a_split_creates_one_unnamed_person_and_moves_only_the_selected_face(
    env: SplitHarness,
) -> None:
    seeded = env.seed_group(face_count=3, name="爸爸")
    dad = seeded["person"]
    target, sibling, keeper = seeded["faces"]
    before_filter = env.filter_revision()
    before_outbox = env.index_outbox_rows()
    sibling_links = env.face_links(sibling)
    keper_links = env.face_links(keeper)

    answer = env.split(dad, [env.selection(target, control=0, assignment=3)], request_id="s1")

    assert answer["contract_version"] == CONTRACT_VERSION
    assert answer["operation_type"] == "SPLIT"
    assert answer["operation_revision"] == 1
    assert answer["status"] == "COMMITTED"
    assert answer["undo_of_operation_id"] is None
    assert answer["requested_source_person_id"] == dad
    assert answer["canonical_source_person_id"] == dad
    assert answer["changed"] is True
    assert answer["replayed"] is False
    assert answer["catalog_filter_revision"] == before_filter + 1
    new_person = int(answer["new_person_id"])
    assert new_person != dad

    # The frozen item order: source group, created group, then the sorted face controls.
    assert [item["entity_type"] for item in answer["items"]] == [
        "SPLIT_GROUP",
        "SPLIT_GROUP",
        "FACE_CONTROL",
    ]
    source_item = env.group_item(answer, "SOURCE")
    created_item = env.group_item(answer, "CREATED")
    assert source_item["entity_id"] == dad
    assert source_item["revision"] == 2
    assert env.revision_of(dad) == 2
    assert created_item["entity_id"] == new_person
    assert created_item["revision"] == 1
    assert created_item["merged_into"] is None
    face_item = env.face_item(answer, target)
    assert face_item == {
        "entity_type": "FACE_CONTROL",
        "face_id": target,
        "changed": True,
        "mode": "MANUAL_ASSIGNED",
        "revision": 1,
        "assigned_person_id": new_person,
        "exclusions": {"activated": [dad], "deactivated": []},
    }

    # The new group is unnamed, alias-free and readable; the source keeps its name.
    created_row = env.person(new_person)
    assert created_row["name"] is None and created_row["name_key"] is None
    assert created_row["merged_into"] is None
    assert env.aliases(new_person) == []
    assert env.view(new_person)["name"] is None
    assert env.view(dad)["name"] == "爸爸"
    assert env.members(new_person) == {target}
    assert env.members(dad) == {sibling, keeper}

    # The moved face is a human fact now; the two siblings were not touched at all.
    link = env.effective_link(target)
    assert (int(link["person_id"]), str(link["assignment_kind"])) == (new_person, "MANUAL")
    # The seed link was revision 3; the downgraded machine suggestion took 4, so the new
    # human link is numbered strictly above it (ADR-0008 §4).
    assert int(link["revision"]) == 5
    assert str(link["last_operation_id"]) == str(answer["operation_id"])
    assert env.control(target)["mode"] == "MANUAL_ASSIGNED"
    assert env.control_revision(target) == 1
    assert env.active_exclusions(target) == [dad]
    assert env.face_links(sibling) == sibling_links
    assert env.face_links(keeper) == keper_links

    # One filter bump with the shared cause, one queue row, one candidate repair - and no
    # image-vector outbox row.
    assert env.filter_revision() == before_filter + 1
    assert env.filter_cause() == "PERSON_CHANGED"
    assert [(int(row["asset_id"]), str(row["cause"])) for row in env.queue_rows()] == [
        (seeded["asset_id"], "PERSON_CHANGED")
    ]
    assert env.candidate_entries()[target] == "PENDING_REPAIR"
    assert env.candidate_entries()[sibling] == "CONFIRMED"
    assert env.index_outbox_rows() == before_outbox
    assert [str(row["face_id"]) for row in env.face_outbox()] == [target]


def test_the_source_avatar_is_rechosen_and_the_created_avatar_comes_from_the_new_members(
    env: SplitHarness,
) -> None:
    seeded = env.seed_group(face_count=3)
    dad = seeded["person"]
    target, _sibling, keeper = seeded["faces"]
    # The representative rule is ``face_pixels DESC``, and the harness seeds ascending
    # values, so the source's avatar is the *last* face before the split.
    assert env.avatar(dad) == keeper

    answer = env.split(dad, [env.selection(target, assignment=3)])

    assert env.avatar(int(answer["new_person_id"])) == target
    assert env.avatar(dad) == keeper

    # Now move the representative itself: the source must re-pick inside its own space.
    second = env.split(dad, [env.selection(keeper, assignment=3)], request_id="s2")
    assert env.avatar(dad) == _sibling
    assert env.avatar(int(second["new_person_id"])) == keeper
    source_bundle = env.group_item(second, "SOURCE")
    assert source_bundle["revision"] == 3


def test_a_manual_confirmed_face_becomes_a_human_member_of_the_new_person(
    env: SplitHarness,
) -> None:
    seeded = env.seed_group(face_count=2, assignment_kind="MANUAL", link_revision=41)
    dad = seeded["person"]
    target, sibling = seeded["faces"]
    # An unrelated denial on another person must survive the split untouched.
    other = env.add_person(name="叔叔")
    env.set_exclusion(sibling, other, active=True)

    answer = env.split(dad, [env.selection(target, assignment=41)])

    link = env.effective_link(target)
    assert link["assignment_kind"] == "MANUAL"
    assert int(link["person_id"]) == int(answer["new_person_id"])
    # Ending a MANUAL link deletes that row. Its revision must still remain the floor for
    # the new assignment; otherwise a well-aged human token could silently move backwards.
    assert int(link["revision"]) == 42
    assert {
        (int(row["person_id"]), str(row["assignment_kind"]), str(row["decision"]))
        for row in env.face_links(target)
    } == {(int(answer["new_person_id"]), "MANUAL", "CONFIRMED")}
    assert env.active_exclusions(sibling) == [other]


# ----------------------------------------------------------------------- idempotency
def test_the_same_request_id_and_payload_replays_without_a_second_write(
    env: SplitHarness,
) -> None:
    seeded = env.seed_group(face_count=2)
    dad = seeded["person"]
    target = seeded["faces"][0]
    first = env.split(dad, [env.selection(target, assignment=3)], request_id="same")
    snapshot = library_state(env)

    # A replay sends the *original* payload verbatim: the source revision it recorded is part
    # of the digest, so the harness must not re-read the (now advanced) revision.
    replay = env.split(
        dad,
        [env.selection(target, assignment=3)],
        request_id="same",
        expected_revision=1,
    )

    assert replay["replayed"] is True
    assert replay["operation_id"] == first["operation_id"]
    assert replay["operation_revision"] == first["operation_revision"]
    assert replay["new_person_id"] == first["new_person_id"]
    assert replay["catalog_filter_revision"] == first["catalog_filter_revision"]
    assert replay["items"] == first["items"]
    assert library_state(env) == snapshot


def test_the_replay_gate_runs_before_later_revisions_and_missing_entities(
    env: SplitHarness,
) -> None:
    seeded = env.seed_group(face_count=3)
    dad = seeded["person"]
    target, _sibling, _keeper = seeded["faces"]
    first = env.split(dad, [env.selection(target, assignment=3)], request_id="gate")

    # A later human edit moves the source revision, the control revision and the assignment.
    env.rename(dad, "老爸", "gate-rename", env.revision_of(dad))
    # And the very face the payload names disappears from the catalog: the gate must answer
    # the recorded operation instead of FACE_NOT_FOUND.
    env.purge_face(target)

    replay = env.split(
        dad,
        [env.selection(target, assignment=3)],
        request_id="gate",
        expected_revision=1,
    )
    assert replay["replayed"] is True
    assert replay["operation_id"] == first["operation_id"]
    assert replay["new_person_id"] == first["new_person_id"]


def test_a_different_payload_on_the_same_request_id_is_a_zero_write_conflict(
    env: SplitHarness,
) -> None:
    seeded = env.seed_group(face_count=2)
    dad = seeded["person"]
    first, second = seeded["faces"]
    env.split(dad, [env.selection(first, assignment=3)], request_id="dup")
    snapshot = library_state(env)

    with pytest.raises(CatalogError) as error:
        env.split(dad, [env.selection(second, assignment=3)], request_id="dup")

    assert error.value.code == PERSON_IDEMPOTENCY_CONFLICT
    assert str(error.value.details["operation_id"]) in {
        str(row["operation_id"]) for row in snapshot["operations"]
    }
    assert library_state(env) == snapshot


# ------------------------------------------------------------------ eligibility matrix
def test_a_stale_source_control_or_assignment_revision_writes_nothing(
    env: SplitHarness,
) -> None:
    seeded = env.seed_group(face_count=3)
    dad = seeded["person"]
    target = seeded["faces"][0]
    snapshot = library_state(env)

    stale_source = assert_failure(
        env,
        PERSON_REVISION_CONFLICT,
        source_person_id=dad,
        faces=[env.selection(target, assignment=3)],
        request_id="stale-1",
        expected_revision=5,
    )
    assert stale_source.details["expected_revision"] == 5
    assert stale_source.details["current_revision"] == 1
    assert library_state(env) == snapshot

    stale_control = assert_failure(
        env,
        FACE_ASSIGNMENT_CONFLICT,
        source_person_id=dad,
        faces=[env.selection(target, control=2, assignment=3)],
        request_id="stale-2",
    )
    assert stale_control.details["face_id"] == target
    assert library_state(env) == snapshot

    stale_assignment = assert_failure(
        env,
        PERSON_SPLIT_CONFLICT,
        source_person_id=dad,
        faces=[env.selection(target, assignment=2)],
        request_id="stale-3",
    )
    assert stale_assignment.details["reason"] == "assignment_revision"
    assert library_state(env) == snapshot


def test_a_suggestion_or_an_unassigned_face_can_never_be_split(env: SplitHarness) -> None:
    seeded = env.seed_group(face_count=2)
    dad = seeded["person"]
    target, sibling = seeded["faces"]
    suggestion = env.add_face(seeded["asset_id"], ordinal=5)
    env.add_embedding(suggestion)
    env.add_suggestion(suggestion, dad)

    reason = assert_failure(
        env,
        PERSON_SPLIT_CONFLICT,
        source_person_id=dad,
        faces=[env.selection(suggestion, assignment=1)],
        request_id="sug-1",
    )
    assert reason.details["reason"] == "face_not_confirmed"

    # MANUAL_UNASSIGNED is refused before anything else.
    env.set_control(target, "MANUAL_UNASSIGNED")
    locked = assert_failure(
        env,
        PERSON_SPLIT_CONFLICT,
        source_person_id=dad,
        faces=[env.selection(target, control=1, assignment=3)],
        request_id="lock-1",
    )
    assert locked.details["reason"] == "face_manual_unassigned"
    assert locked.details["face_id"] == target
    assert sibling not in str(locked.details)


def test_a_face_owned_by_another_person_or_denied_for_the_source_is_refused(
    env: SplitHarness,
) -> None:
    seeded = env.seed_group(face_count=2)
    dad = seeded["person"]
    target, sibling = seeded["faces"]
    other = env.add_person(name="叔叔")

    # Ownership: the effective link points at somebody else.
    with env.catalog.write() as connection:
        connection.execute(
            "UPDATE face_person_links SET person_id = ? WHERE face_id = ?",
            (int(other), str(target)),
        )
    owned = assert_failure(
        env,
        PERSON_SPLIT_CONFLICT,
        source_person_id=dad,
        faces=[env.selection(target, assignment=3)],
        request_id="own-1",
    )
    assert owned.details["reason"] == "face_owned_by_other_person"

    # Put it back and deny that very face for the source instead.
    with env.catalog.write() as connection:
        connection.execute(
            "UPDATE face_person_links SET person_id = ? WHERE face_id = ?",
            (int(dad), str(target)),
        )
    env.set_exclusion(target, dad, active=True)
    denied = assert_failure(
        env,
        PERSON_SPLIT_CONFLICT,
        source_person_id=dad,
        faces=[env.selection(target, assignment=3)],
        request_id="deny-1",
    )
    assert denied.details["reason"] == "face_state_inconsistent"
    assert sibling not in str(denied.details)


def test_split_refuses_to_empty_the_source_and_to_resend_a_malformed_list(
    env: SplitHarness,
) -> None:
    seeded = env.seed_group(face_count=2)
    dad = seeded["person"]
    first, second = seeded["faces"]

    empty = assert_failure(
        env,
        PERSON_SPLIT_CONFLICT,
        source_person_id=dad,
        faces=[env.selection(first, assignment=3), env.selection(second, assignment=3)],
        request_id="empty-1",
    )
    assert empty.details["reason"] == "source_would_be_empty"

    with pytest.raises(CatalogError) as missing:
        env.split(dad, [env.selection("0" * 64, assignment=1)], request_id="missing-1")
    assert missing.value.code == FACE_NOT_FOUND

    assert_failure(
        env,
        VALIDATION_ERROR,
        source_person_id=dad,
        faces=[],
        request_id="bad-1",
    )
    assert_failure(
        env,
        VALIDATION_ERROR,
        source_person_id=dad,
        faces=[env.selection(first, assignment=3), env.selection(first, assignment=3)],
        request_id="bad-2",
    )
    oversized = assert_failure(
        env,
        PERSON_BATCH_TOO_LARGE,
        source_person_id=dad,
        faces=[
            env.selection(f"{index:064x}", assignment=1) for index in range(501)
        ],
        request_id="bad-3",
    )
    assert oversized.details["max_batch_size"] == 500
    assert_failure(
        env,
        VALIDATION_ERROR,
        source_person_id=dad,
        faces=[env.selection(first, assignment=3)],
        request_id="bad-4",
        contract_version="p3-person-contracts.v1",
    )


# --------------------------------------------------------------- failure and restart
def test_a_failure_inside_the_transaction_rolls_the_whole_split_back(
    env: SplitHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from photo_search.persons import split as split_module

    seeded = env.seed_group(face_count=3)
    dad = seeded["person"]
    target = seeded["faces"][0]
    snapshot = library_state(env)

    def explode(*args, **kwargs):
        raise RuntimeError("injected derived failure")

    monkeypatch.setattr(split_module.operation_module, "publish_derived", explode)
    with pytest.raises(RuntimeError):
        env.split(dad, [env.selection(target, assignment=3)], request_id="boom-1")
    monkeypatch.undo()
    assert library_state(env) == snapshot

    def explode_avatar(*args, **kwargs):
        raise RuntimeError("injected person failure")

    monkeypatch.setattr(split_module, "_best_member_face", explode_avatar)
    with pytest.raises(RuntimeError):
        env.split(dad, [env.selection(target, assignment=3)], request_id="boom-2")
    monkeypatch.undo()
    assert library_state(env) == snapshot

    # The rolled-back request_id is not consumed: the same key now succeeds.
    answer = env.split(dad, [env.selection(target, assignment=3)], request_id="boom-1")
    assert answer["replayed"] is False
    assert env.revision_of(dad) == 2


def test_a_split_survives_a_restart_and_the_created_id_keeps_resolving(
    env: SplitHarness,
) -> None:
    seeded = env.seed_group(face_count=2)
    dad = seeded["person"]
    target = seeded["faces"][0]
    answer = env.split(dad, [env.selection(target, assignment=3)], request_id="restart-1")
    new_person = int(answer["new_person_id"])

    env.reopen()

    assert env.canonical(new_person) == new_person
    assert env.members(new_person) == {target}
    assert env.members(dad) == {seeded["faces"][1]}
    assert env.control(target)["mode"] == "MANUAL_ASSIGNED"
    assert env.active_exclusions(target) == [dad]
    replay = env.split(
        dad,
        [env.selection(target, assignment=3)],
        request_id="restart-1",
        expected_revision=1,
    )
    assert replay["replayed"] is True and replay["items"] == answer["items"]

    undone = env.undo(answer["operation_id"], "restart-2", answer["operation_revision"])
    assert undone["operation_type"] == "UNDO"
    env.reopen()
    assert env.canonical(new_person) == dad
    assert env.members(dad) == {target, seeded["faces"][1]}
    assert env.control(target)["mode"] == "AUTO_ALLOWED"


def test_an_old_source_id_is_split_into_its_canonical_person(env: SplitHarness) -> None:
    """ADR-0008 §2: a merged-away id is still a legal source, and it stays *requested*."""
    seeded = env.seed_group(face_count=2, name="爸爸")
    dad = seeded["person"]
    target = seeded["faces"][0]
    old = env.add_person()  # an unnamed group that is merged into the named one
    env.merge(dad, [(old, env.revision_of(old))], "fold-old-1", target_revision=1)
    assert env.canonical(old) == dad

    answer = env.split(
        old,
        [env.selection(target, assignment=3)],
        request_id="old-id-1",
        expected_revision=env.revision_of(dad),
    )

    assert answer["requested_source_person_id"] == old
    assert answer["canonical_source_person_id"] == dad
    assert env.group_item(answer, "SOURCE")["entity_id"] == dad
    assert env.group_item(answer, "CREATED")["entity_id"] == answer["new_person_id"]
    # The recorded ids are the ones the caller named, never a canonical id.
    assert str(env.operation(str(answer["operation_id"]))["source_person_ids_json"]) == (
        f"[{old}]"
    )
    assert env.members(dad) == {seeded["faces"][1]}


def test_a_broken_source_redirect_chain_keeps_its_own_code(env: SplitHarness) -> None:
    seeded = env.seed_group(face_count=2)
    dad = seeded["person"]
    other = env.add_person()
    with env.catalog.write() as connection:
        connection.execute(
            "UPDATE persons SET merged_into = ? WHERE person_id = ?", (int(other), int(dad))
        )
        connection.execute(
            "UPDATE persons SET merged_into = ? WHERE person_id = ?", (int(dad), int(other))
        )
    snapshot = library_state(env)

    with pytest.raises(CatalogError, match="PERSON_REDIRECT_CYCLE") as error:
        env.split(
            dad, [env.selection(seeded["faces"][0], assignment=3)], request_id="cycle-1"
        )

    assert error.value.code == PERSON_REDIRECT_CYCLE
    assert library_state(env) == snapshot
