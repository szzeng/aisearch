"""P3-04B: the ADR-0007 mode/exclusion matrix, revision, idempotency and derivation.

Every case runs on a real migrated catalog inside pytest's ``tmp_path`` with only synthetic
faces - no detector weights, no GPU, no real photo library. The point of this file is the
*authority* behaviour of one face correction and its derived catch-up; the transport
contract lives in ``test_p3_face_correction_api.py`` and the migration in
``test_p3_face_correction_migration.py``.
"""

from __future__ import annotations

import pytest
from p3_face_correction_fixtures import FaceCorrectionHarness

from photo_search.catalog.errors import (
    FACE_ASSIGNMENT_CONFLICT,
    FACE_BATCH_TOO_LARGE,
    FACE_IDEMPOTENCY_CONFLICT,
    FACE_NOT_FOUND,
    PERSON_NOT_FOUND,
    CatalogError,
)
from photo_search.persons.contracts import REASON_HUMAN_CONFLICT, REASON_MANUAL_UNASSIGNED


@pytest.fixture()
def env(tmp_path) -> FaceCorrectionHarness:
    harness = FaceCorrectionHarness(tmp_path)
    try:
        yield harness
    finally:
        harness.close()


def test_a_face_without_a_control_row_reads_as_auto_allowed_revision_zero(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    state = env.assignment(seeded["face"])
    assert state["contract_version"] == "p3-face-correction-contracts.v1"
    assert state["mode"] == "AUTO_ALLOWED"
    assert state["revision"] == 0
    # The automatic link is a real current assignment, not a lock: AUTO_ALLOWED may carry one.
    assert state["assigned_person_id"] == seeded["dad"]
    assert state["active_exclusions"] == []
    assert state["suggestions_available"] is False
    assert env.control(seeded["face"]) is None


def test_late_exclusion_before_reconcile_commit_keeps_existing_links(
    env: FaceCorrectionHarness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib
    import struct

    seeded = env.seeded_pair()
    before_links = env.face_links(seeded["face"])
    from photo_search.face.contract import FACE_DIMENSION, FACE_PROCESSOR_ID
    from photo_search.face.index import FaceIndexCoordinator

    generation_id = env.seed_candidate_index([seeded["face"]])
    vector_blob = struct.pack("<f", 1.0) + bytes(4 * (int(FACE_DIMENSION) - 1))
    vector_sha256 = hashlib.sha256(vector_blob).hexdigest()
    with env.catalog.write() as connection:
        connection.execute(
            "UPDATE assets SET sha256=(SELECT input_revision FROM faces WHERE face_id=?) "
            "WHERE asset_id=?",
            (seeded["face"], seeded["asset_id"]),
        )
        connection.execute(
            "UPDATE face_embeddings SET vector_blob=?,vector_sha256=? WHERE face_id=? AND processor_id=?",
            (vector_blob, vector_sha256, seeded["face"], str(FACE_PROCESSOR_ID)),
        )
        connection.execute(
            "INSERT INTO face_candidates(generation_id,face_id,is_representative,quality_state,"
            "vector_blob,vector_sha256,published_at) VALUES (?, ?, 1, 'OK', ?, ?, 1)",
            (generation_id, seeded["face"], vector_blob, vector_sha256),
        )

    original_candidates = FaceIndexCoordinator.candidates

    def candidates_then_exclude(index):
        result = original_candidates(index)
        with env.catalog.write() as connection:
            connection.execute(
                "INSERT INTO face_inbox_hidden(face_id,ignore_state) VALUES (?, 'EXCLUDED')",
                (seeded["face"],),
            )
        return result

    monkeypatch.setattr(FaceIndexCoordinator, "candidates", candidates_then_exclude)

    decisions = env.reconcile_asset(seeded["asset_id"])

    excluded_decision = next(item for item in decisions if item["face_id"] == seeded["face"])
    assert excluded_decision["action"] == "HUMAN_CONTROLLED"
    assert env.face_links(seeded["face"]) == before_links
    assert env.catalog.read_one(
        "SELECT ignore_state FROM face_inbox_hidden WHERE face_id = ?", (seeded["face"],)
    )["ignore_state"] == "EXCLUDED"
    assert seeded["face"] in {
        candidate.face_id for candidate in original_candidates(FaceIndexCoordinator(env.catalog))
    }
    assert env.assignment(seeded["face"])["suggestions_available"] is False
    from photo_search.persons.read import face_page
    person_faces = face_page(env.catalog, limit=10, person_id=seeded["dad"])["faces"]
    assert next(face for face in person_faces if face["face_id"] == seeded["face"])[
        "suggestions_available"
    ] is False


def test_unknown_face_and_unknown_target_are_reported_by_their_own_codes(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    with pytest.raises(CatalogError) as missing:
        env.assignment("0" * 64)
    assert missing.value.code == FACE_NOT_FOUND
    with pytest.raises(CatalogError) as missing_target:
        env.edit(seeded["face"], "assign", expected_revision=0, target_person_id=9999)
    assert missing_target.value.code == PERSON_NOT_FOUND


def test_unassign_ends_the_effective_link_lifts_nothing_and_denies_the_old_person(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    before_filter = env.filter_revision()
    outcome = env.edit(seeded["face"], "unassign", expected_revision=0)

    assert outcome["operation_type"] == "FACE_UNASSIGN"
    assert outcome["changed"] is True
    assert outcome["replayed"] is False
    assert outcome["catalog_filter_revision"] == before_filter + 1
    item = outcome["items"][0]
    assert item["entity_type"] == "FACE_CONTROL"
    assert item["face_id"] == seeded["face"]
    assert item["mode"] == "MANUAL_UNASSIGNED"
    assert item["revision"] == 1
    assert item["assigned_person_id"] is None
    assert item["exclusions"] == {"activated": [seeded["dad"]], "deactivated": []}

    control = env.control(seeded["face"])
    assert control["mode"] == "MANUAL_UNASSIGNED" and control["revision"] == 1
    assert env.effective_link(seeded["face"]) is None
    assert env.active_exclusions(seeded["face"]) == [seeded["dad"]]
    # The machine knowledge is downgraded, never deleted: it is a suggestion now.
    downgraded = [
        row
        for row in env.face_links(seeded["face"])
        if row["assignment_kind"] == "AUTO"
    ]
    assert len(downgraded) == 1
    assert downgraded[0]["decision"] == "SUGGESTED"
    assert downgraded[0]["reason"] == REASON_MANUAL_UNASSIGNED
    # The other face of the same photo is untouched.
    assert env.effective_link(seeded["neighbour"])["person_id"] == seeded["mom"]
    assert env.control(seeded["neighbour"]) is None
    # Exactly one filter revision move, one projection queue row, one degraded candidate.
    assert env.filter_causes() == ["PERSON_CHANGED"]
    assert [(int(row["asset_id"]), str(row["cause"])) for row in env.queue_rows()] == [
        (seeded["asset_id"], "PERSON_CHANGED")
    ]
    assert env.candidate_entries()[seeded["face"]] == "PENDING_REPAIR"
    assert env.index_outbox_rows() == 0


def test_the_same_request_id_replays_before_any_revision_check(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    request_id = env.next_request_id()
    first = env.edit(seeded["face"], "unassign", expected_revision=0, request_id=request_id)
    operations = env.operations()
    filter_after_first = env.filter_revision()

    replay = env.edit(
        seeded["face"], "unassign", expected_revision=0, request_id=request_id
    )
    # The replay answers even though revision 0 is now stale: the gate runs first.
    assert replay["replayed"] is True
    assert replay["operation_id"] == first["operation_id"]
    assert replay["changed"] is True
    assert replay["items"] == first["items"]
    assert replay["catalog_filter_revision"] == first["catalog_filter_revision"]
    assert int(env.operation(first["operation_id"])["catalog_filter_revision"]) == int(
        first["catalog_filter_revision"]
    )
    assert len(env.operations()) == len(operations)
    assert env.filter_revision() == filter_after_first

    with pytest.raises(CatalogError) as conflict:
        env.edit(seeded["face"], "unassign", expected_revision=1, request_id=request_id)
    assert conflict.value.code == FACE_IDEMPOTENCY_CONFLICT
    assert "payload" not in str(conflict.value.details)
    assert len(env.operations()) == len(operations)


def test_a_stale_expected_revision_is_refused_with_the_current_one(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    before = env.library_snapshot()
    with pytest.raises(CatalogError) as conflict:
        env.edit(seeded["face"], "unassign", expected_revision=1)
    assert conflict.value.code == FACE_ASSIGNMENT_CONFLICT
    assert conflict.value.details["expected_revision"] == 1
    assert conflict.value.details["current_revision"] == 0
    assert conflict.value.details["face_id"] == seeded["face"]
    assert env.library_snapshot() == before
    assert env.control(seeded["face"]) is None
    assert env.operations() == []


def test_assign_creates_the_one_manual_confirmed_link_and_replaces_the_machine_one(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    outcome = env.edit(
        seeded["face"], "assign", expected_revision=0, target_person_id=seeded["other"]
    )
    item = outcome["items"][0]
    assert outcome["operation_type"] == "FACE_ASSIGN"
    assert item["mode"] == "MANUAL_ASSIGNED"
    assert item["revision"] == 1
    assert item["assigned_person_id"] == seeded["other"]
    # Replacing 爸爸 keeps its machine fact as a suggestion *and* records the denial.
    assert item["exclusions"] == {"activated": [seeded["dad"]], "deactivated": []}
    links = env.face_links(seeded["face"])
    manual = [row for row in links if row["assignment_kind"] == "MANUAL"]
    assert len(manual) == 1
    assert manual[0]["decision"] == "CONFIRMED"
    assert manual[0]["person_id"] == seeded["other"]
    auto = [row for row in links if row["assignment_kind"] == "AUTO"]
    assert len(auto) == 1 and auto[0]["decision"] == "SUGGESTED"
    assert auto[0]["reason"] == REASON_HUMAN_CONFLICT
    assert env.control(seeded["face"])["mode"] == "MANUAL_ASSIGNED"
    # The operation row keeps the face family shape: no person sources, the target recorded.
    row = env.operation(outcome["operation_id"])
    assert row["type"] == "FACE_ASSIGN"
    assert row["source_person_ids_json"] == "[]"
    assert row["target_person_id"] == seeded["other"]
    item_row = env.face_items(outcome["operation_id"])[0]
    assert item_row["entity_type"] == "FACE_CONTROL"
    assert item_row["entity_id"] is None
    assert item_row["entity_key"] == seeded["face"]
    assert item_row["after_revision"] == 1


def test_assigning_back_a_denied_person_lifts_that_denial_only(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    env.edit(seeded["face"], "assign", expected_revision=0, target_person_id=seeded["other"])
    # A second denial, this time for the neighbour's person, must survive the next change.
    env.set_exclusion(seeded["face"], seeded["mom"], active=True)

    outcome = env.edit(
        seeded["face"], "assign", expected_revision=1, target_person_id=seeded["dad"]
    )
    item = outcome["items"][0]
    assert item["revision"] == 2
    assert item["assigned_person_id"] == seeded["dad"]
    assert item["exclusions"]["activated"] == [seeded["other"]]
    assert item["exclusions"]["deactivated"] == [seeded["dad"]]
    assert env.active_exclusions(seeded["face"]) == sorted([seeded["mom"], seeded["other"]])
    # The lifted denial is a row with active = 0, not a deleted row.
    rows = {int(row["person_id"]): int(row["active"]) for row in env.exclusion_rows(seeded["face"])}
    assert rows[seeded["dad"]] == 0
    link = env.effective_link(seeded["face"])
    assert link["assignment_kind"] == "MANUAL" and link["person_id"] == seeded["dad"]


def test_assigning_the_same_person_twice_is_an_answered_no_op(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    env.edit(seeded["face"], "assign", expected_revision=0, target_person_id=seeded["other"])
    before = env.library_snapshot()
    before_filter = env.filter_revision()
    outcome = env.edit(
        seeded["face"], "assign", expected_revision=1, target_person_id=seeded["other"]
    )
    assert outcome["changed"] is False
    assert outcome["items"][0]["changed"] is False
    assert env.library_snapshot() == before
    assert env.filter_revision() == before_filter
    assert env.control_revision(seeded["face"]) == 1
    # It is still a committed, replayable operation - the request was answered, not dropped.
    assert len(env.operations()) == 2


def test_restore_auto_keeps_every_denial_and_adopts_no_suggestion(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    env.edit(seeded["face"], "assign", expected_revision=0, target_person_id=seeded["other"])
    assert env.active_exclusions(seeded["face"]) == [seeded["dad"]]
    outcome = env.edit(seeded["face"], "restore_auto", expected_revision=1)
    item = outcome["items"][0]
    assert outcome["operation_type"] == "FACE_RESTORE_AUTO"
    assert item["mode"] == "AUTO_ALLOWED"
    assert item["revision"] == 2
    assert item["assigned_person_id"] is None
    assert item["exclusions"] == {"activated": [], "deactivated": []}
    assert env.active_exclusions(seeded["face"]) == [seeded["dad"]]
    assert env.effective_link(seeded["face"]) is None
    suggestions = [
        row for row in env.face_links(seeded["face"]) if row["decision"] == "SUGGESTED"
    ]
    assert suggestions, "the machine suggestion survives restore_auto as a suggestion"
    assert all(row["decision"] != "CONFIRMED" for row in suggestions)


def test_restore_auto_on_an_auto_face_is_a_no_op(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    before = env.library_snapshot()
    outcome = env.edit(seeded["face"], "restore_auto", expected_revision=0)
    assert outcome["changed"] is False
    assert env.control(seeded["face"]) is None
    # The existing automatic assignment is not deleted by a repeated "let the machine decide".
    assert env.effective_link(seeded["face"])["person_id"] == seeded["dad"]
    assert env.library_snapshot() == before


def test_unassign_on_an_already_manual_unassigned_face_is_a_no_op(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    env.edit(seeded["face"], "unassign", expected_revision=0)
    before = env.library_snapshot()
    outcome = env.edit(seeded["face"], "unassign", expected_revision=1)
    assert outcome["changed"] is False
    assert env.control_revision(seeded["face"]) == 1
    assert env.library_snapshot() == before


def test_a_manual_lock_survives_restart_rescan_and_the_candidate_scan(
    env: FaceCorrectionHarness,
) -> None:
    """ADR-0007 §11.1: after "not 爸爸", nothing automatic may bring 爸爸 back."""
    from photo_search.face.index import FaceIndexCoordinator

    seeded = env.seeded_pair()
    env.edit(seeded["face"], "unassign", expected_revision=0)
    excluded_before = env.active_exclusions(seeded["face"])

    # A fresh coordinator over the same catalog: the process may have restarted.
    index = FaceIndexCoordinator(env.catalog)
    candidate_ids = {str(candidate.face_id) for candidate in index.candidates()}
    assert seeded["face"] not in candidate_ids

    # The repair pass republishes the *vector* of that face, which is not a decision about
    # who it is; the human denial stays the authority.
    env.seed_candidate_index([seeded["face"], seeded["neighbour"]])

    # Re-associating the photo is a no-op for the locked face.
    with env.catalog.write() as connection:
        connection.execute(
            "INSERT INTO face_association_queue(asset_id, spec_revision, face_revision, "
            "queued_at) VALUES (?, ?, 1, 1) ON CONFLICT(asset_id) DO UPDATE SET "
            "queued_at = excluded.queued_at",
            (int(seeded["asset_id"]), str(env.effective_spec_revision())),
        )
    outcome = env.reconcile()
    assert outcome["human_controlled"] == 1
    assert outcome["processed"] == 1
    assert outcome["new_groups"] == 0 and outcome["merged"] == 0
    assert env.effective_link(seeded["face"]) is None
    assert env.active_exclusions(seeded["face"]) == excluded_before == [seeded["dad"]]


def test_a_denied_person_is_not_re_adopted_by_the_automatic_path(
    env: FaceCorrectionHarness,
) -> None:
    """ADR-0007 §11.13: AUTO_ALLOWED still refuses an actively denied person."""
    from photo_search.face.contract import FACE_DIMENSION, FACE_PROCESSOR_ID
    from photo_search.face.index import FaceIndexCoordinator

    seeded = env.seeded_pair()
    env.edit(seeded["face"], "assign", expected_revision=0, target_person_id=seeded["other"])
    env.edit(seeded["face"], "restore_auto", expected_revision=1)
    assert env.active_exclusions(seeded["face"]) == [seeded["dad"]]
    assert env.effective_link(seeded["face"]) is None

    # The general person harness uses a one-byte placeholder because its normal tests never
    # run cosine matching. This case deliberately enters the automatic path, so give the
    # target face a dimension-valid synthetic vector (no model or real photo is involved).
    with env.catalog.write() as connection:
        connection.execute(
            "UPDATE face_embeddings SET vector_blob = ? WHERE face_id = ? AND processor_id = ?",
            (bytes(4 * int(FACE_DIMENSION)), seeded["face"], str(FACE_PROCESSOR_ID)),
        )

    with env.catalog.write() as connection:
        connection.execute(
            "INSERT INTO face_association_queue(asset_id, spec_revision, face_revision, "
            "queued_at) VALUES (?, ?, 1, 1) ON CONFLICT(asset_id) DO UPDATE SET "
            "queued_at = excluded.queued_at",
            (int(seeded["asset_id"]), str(env.effective_spec_revision())),
        )
    decisions = env.reconcile_asset(seeded["asset_id"])
    by_face = {decision["face_id"]: decision for decision in decisions}
    # AUTO_ALLOWED means the face is actually free to be grouped again; an active denial
    # filters only the denied canonical person and is not a whole-face MANUAL lock.
    assert by_face[seeded["face"]]["action"] != "HUMAN_CONTROLLED"
    assert by_face[seeded["face"]]["person_id"] != seeded["dad"]
    link = env.effective_link(seeded["face"])
    assert link is None or int(link["person_id"]) != seeded["dad"]
    assert FaceIndexCoordinator(env.catalog).active_generation() is not None


def test_a_face_correction_never_schedules_an_image_vector_refresh(
    env: FaceCorrectionHarness,
) -> None:
    """ADR-0007 §8: only search/face-derived queues move, never image ``index_outbox``."""
    seeded = env.seeded_pair()
    calls: list[dict] = []

    def image_refresh(*_args, **kwargs) -> int:
        calls.append(dict(kwargs))
        return 1

    env.service.refresh = image_refresh
    outcome = env.edit(seeded["face"], "unassign", expected_revision=0)
    assert outcome["changed"] is True
    assert calls == []
    assert env.index_outbox_rows() == 0


def test_an_auto_allowed_face_filters_a_denial_through_a_canonical_merge(
    env: FaceCorrectionHarness,
) -> None:
    """ADR-0007 §4: A->B redirects cannot make an old denial miss canonical B."""
    import hashlib
    import struct

    from photo_search.face.contract import FACE_DIMENSION, FACE_PROCESSOR_ID

    seeded = env.seeded_pair()
    env.edit(seeded["face"], "assign", expected_revision=0, target_person_id=seeded["other"])
    env.edit(seeded["face"], "restore_auto", expected_revision=1)

    candidate_asset = env.add_asset("另一个人物.jpg")
    candidate_face = env.add_face(candidate_asset)
    env.add_embedding(candidate_face)
    env.add_link(
        candidate_face,
        seeded["other"],
        decision="CONFIRMED",
        assignment_kind="AUTO",
        confidence=0.99,
    )
    generation_id = env.seed_candidate_index([seeded["face"], candidate_face])
    vector_blob = struct.pack("<f", 1.0) + bytes(4 * (int(FACE_DIMENSION) - 1))
    with env.catalog.write() as connection:
        connection.execute(
            "UPDATE face_embeddings SET vector_blob = ? WHERE face_id IN (?, ?) "
            "AND processor_id = ?",
            (vector_blob, seeded["face"], candidate_face, str(FACE_PROCESSOR_ID)),
        )
        connection.execute(
            "INSERT INTO face_candidates(generation_id, face_id, is_representative, "
            "quality_state, vector_blob, vector_sha256, published_at) "
            "VALUES (?, ?, 1, 'OK', ?, ?, 1)",
            (
                generation_id,
                candidate_face,
                vector_blob,
                hashlib.sha256(vector_blob).hexdigest(),
            ),
        )

    # The stored denial still names dad, but dad now redirects to other. The automatic
    # matcher must filter the candidate for other through that canonical equivalence.
    env.merge(
        seeded["other"],
        [(seeded["dad"], env.revision_of(seeded["dad"]))],
        "canonical-denial-merge",
        target_revision=env.revision_of(seeded["other"]),
    )
    decisions = env.reconcile_asset(seeded["asset_id"])
    decision = {item["face_id"]: item for item in decisions}[seeded["face"]]
    assert decision["action"] == "NEW_GROUP"
    link = env.effective_link(seeded["face"])
    assert link is not None
    assert int(link["person_id"]) not in {seeded["dad"], seeded["other"]}


def test_a_late_automatic_result_conflicts_instead_of_overwriting_a_lock(
    env: FaceCorrectionHarness,
) -> None:
    """ADR-0007 §4/§11.2: an automatic pass that read revision 0 must not write after 0 -> 1."""
    seeded = env.seeded_pair()
    # The pass observed revision 0 for every face of the photo ...
    stale = {seeded["face"]: 0, seeded["neighbour"]: 0}
    # ... and the human decision lands before it commits.
    env.edit(seeded["face"], "assign", expected_revision=0, target_person_id=seeded["other"])
    links_before = env.face_links(seeded["face"])
    decisions = env.reconcile_asset(seeded["asset_id"], observed_controls=stale)
    by_face = {decision["face_id"]: decision for decision in decisions}
    assert by_face[seeded["face"]]["action"] == "LATE_CONTROL_CONFLICT"
    assert by_face[seeded["face"]]["person_id"] is None
    assert env.face_links(seeded["face"]) == links_before
    assert env.control_revision(seeded["face"]) == 1


def test_a_second_face_may_be_corrected_in_the_same_photo_without_touching_the_first(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    outcome = env.edit(seeded["neighbour"], "unassign", expected_revision=0)
    assert outcome["items"][0]["face_id"] == seeded["neighbour"]
    assert env.effective_link(seeded["face"])["person_id"] == seeded["dad"]
    assert env.control(seeded["face"]) is None
    # The photo stays in 爸爸's album because a face of it is still effectively linked.
    assert env.view(seeded["dad"])["face_count"] == 1
    assert env.view(seeded["mom"])["face_count"] == 0


def test_a_batch_is_validated_whole_before_it_writes_anything(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    third_asset = env.add_asset("另一张.jpg")
    third = env.add_face(third_asset)
    before = env.library_snapshot()
    with pytest.raises(CatalogError) as conflict:
        env.batch(
            [
                {
                    "face_id": seeded["face"],
                    "action": "unassign",
                    "target_person_id": None,
                    "expected_revision": 0,
                },
                {
                    "face_id": third,
                    "action": "assign",
                    "target_person_id": seeded["other"],
                    "expected_revision": 3,
                },
            ]
        )
    assert conflict.value.code == FACE_ASSIGNMENT_CONFLICT
    assert conflict.value.details["face_id"] == third
    assert env.library_snapshot() == before
    assert env.operations() == []
    assert env.control(seeded["face"]) is None


def test_a_duplicate_face_and_an_oversized_batch_are_refused(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    item = {
        "face_id": seeded["face"],
        "action": "unassign",
        "target_person_id": None,
        "expected_revision": 0,
    }
    with pytest.raises(CatalogError) as duplicate:
        env.batch([item, {**item, "action": "restore_auto"}])
    assert duplicate.value.code == "VALIDATION_ERROR"
    assert duplicate.value.details["face_ids"] == [seeded["face"]]
    oversized = [dict(item, face_id=f"{index:064x}") for index in range(501)]
    with pytest.raises(CatalogError) as too_large:
        env.batch(oversized)
    assert too_large.value.code == FACE_BATCH_TOO_LARGE
    assert too_large.value.details["max_batch_size"] == 500
    assert env.operations() == []


def test_a_batch_with_mixed_actions_commits_once_in_a_deterministic_order(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    third_asset = env.add_asset("另一张.jpg")
    third = env.add_face(third_asset)
    env.add_person(name="奶奶")
    before_filter = env.filter_revision()
    outcome = env.batch(
        [
            {
                "face_id": third,
                "action": "assign",
                "target_person_id": seeded["other"],
                "expected_revision": 0,
            },
            {
                "face_id": seeded["face"],
                "action": "unassign",
                "target_person_id": None,
                "expected_revision": 0,
            },
        ]
    )
    assert outcome["operation_type"] == "BATCH_FACE_EDIT"
    assert outcome["changed"] is True
    assert [item["face_id"] for item in outcome["items"]] == sorted([third, seeded["face"]])
    assert outcome["catalog_filter_revision"] == before_filter + 1
    assert sorted(str(row["cause"]) for row in env.queue_rows()) == ["PERSON_CHANGED"] * 2
    assert env.effective_link(seeded["face"]) is None
    assert env.effective_link(third)["person_id"] == seeded["other"]
    assert env.operation(outcome["operation_id"])["type"] == "BATCH_FACE_EDIT"


def test_a_batch_replay_is_idempotent_and_a_different_payload_conflicts(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    request_id = env.next_request_id("batch")
    items = [
        {
            "face_id": seeded["face"],
            "action": "unassign",
            "target_person_id": None,
            "expected_revision": 0,
        }
    ]
    first = env.batch(items, request_id=request_id)
    replay = env.batch(items, request_id=request_id)
    assert replay["replayed"] is True
    assert replay["operation_id"] == first["operation_id"]
    assert replay["operation_type"] == "BATCH_FACE_EDIT"
    assert len(env.operations()) == 1
    # The same key with a reordered but equivalent payload is the same request.
    reordered = env.batch(list(reversed(items)), request_id=request_id)
    assert reordered["replayed"] is True
    with pytest.raises(CatalogError) as conflict:
        env.batch(
            [{**items[0], "action": "restore_auto"}],
            request_id=request_id,
        )
    assert conflict.value.code == FACE_IDEMPOTENCY_CONFLICT


def test_contract_version_action_and_target_shapes_are_refused(
    env: FaceCorrectionHarness,
) -> None:
    seeded = env.seeded_pair()
    with pytest.raises(CatalogError) as version:
        env.edit(
            seeded["face"],
            "unassign",
            expected_revision=0,
            contract_version="p3-person-contracts.v1",
        )
    assert version.value.code == "VALIDATION_ERROR"
    with pytest.raises(CatalogError) as action:
        env.edit(seeded["face"], "guess", expected_revision=0)
    assert action.value.code == "VALIDATION_ERROR"
    with pytest.raises(CatalogError) as target:
        env.edit(seeded["face"], "unassign", expected_revision=0, target_person_id=seeded["dad"])
    assert target.value.code == "VALIDATION_ERROR"
    with pytest.raises(CatalogError) as missing_target:
        env.edit(seeded["face"], "assign", expected_revision=0)
    assert missing_target.value.code == "VALIDATION_ERROR"
    assert env.operations() == []


def test_the_read_only_counts_follow_the_current_authority_facts(
    env: FaceCorrectionHarness,
) -> None:
    """ADR-0007 §9: four integers, from SQLite, with no name, id, box or similarity."""
    seeded = env.seeded_pair()
    third_asset = env.add_asset("第三张.jpg")
    third = env.add_face(third_asset)
    baseline = env.summary()
    assert baseline["manual_assigned_faces"] == 0
    assert baseline["manual_unassigned_faces"] == 0
    assert baseline["faces_with_active_exclusions"] == 0
    assert baseline["active_face_exclusions"] == 0

    env.edit(seeded["face"], "unassign", expected_revision=0)
    env.edit(third, "assign", expected_revision=0, target_person_id=seeded["mom"])
    # Two denials, one of them recorded against a person that a merge has since moved.
    env.edit(third, "assign", expected_revision=1, target_person_id=seeded["dad"])
    env.merge(
        seeded["other"],
        [(seeded["dad"], env.revision_of(seeded["dad"]))],
        "merge-counts",
        target_revision=env.revision_of(seeded["other"]),
    )
    summary = env.summary()
    assert summary["manual_unassigned_faces"] == 1
    assert summary["manual_assigned_faces"] == 1
    # Two faces carry a denial: the unassigned 爸爸 face and the twice-corrected third face.
    assert summary["faces_with_active_exclusions"] == 2
    # Two canonical relations: 爸爸's denial now resolves onto 叔叔 after the merge, and the
    # third face's 妈妈 denial is a different relation - one denial per face, not per person.
    assert summary["active_face_exclusions"] == 2
    assert {
        (str(row["face_id"]), int(row["person_id"]))
        for row in env.catalog.read_all(
            "SELECT face_id, person_id FROM face_exclusions WHERE active = 1"
        )
    } == {(seeded["face"], seeded["dad"]), (third, seeded["mom"])}
    # The counts never turn a suggestion into an assignment or a group into a lock.
    assert summary["manual_links"] == 1
    assert summary["persons"] == summary["named_persons"] + summary["unnamed_persons"]
