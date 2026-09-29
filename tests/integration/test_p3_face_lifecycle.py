"""P3-02: the real face lifecycle, its recovery boundaries and its privacy rules.

Everything here runs the *product* path on real pinned weights and real fixture faces in
``%TEMP%``: register -> scan -> scheduler job -> structured artifact -> current face facts ->
independent candidate index -> conservative association -> read-only status. The named fault
injections of the task card live in the second half of the file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from p3_face_fixtures import SKIP_WITHOUT_FACE_WEIGHTS, Harness

from photo_search.catalog.errors import (
    FACE_WEIGHTS_HASH_MISMATCH,
    FACE_WEIGHTS_MISSING,
    INPUT_UNSTABLE,
    STALE_INPUT_REVISION,
    STALE_LEASE,
    CatalogError,
)
from photo_search.face import contract
from photo_search.face.engine import FaceEngineCache
from photo_search.face.index import FaceIndexCoordinator, _InjectedCrash
from photo_search.face.processor import face_executor
from photo_search.face.service import run_queue
from photo_search.scheduler.contracts import JobResult
from photo_search.scheduler.engine import Scheduler

pytestmark = SKIP_WITHOUT_FACE_WEIGHTS

EXPECTED_ASSETS = 6
EXPECTED_FACES = 12
EXPECTED_PERSONS = 8


@pytest.fixture()
def harness(tmp_path: Path) -> Harness:
    instance = Harness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def processed(h: Harness, *, device: str = "cpu") -> dict[str, Any]:
    h.install_weights()
    h.scan()
    return h.detect(device=device)


def person_of(h: Harness, relative: str) -> int:
    row = h.catalog.read_one(
        "SELECT l.person_id AS person_id FROM face_person_links AS l "
        "JOIN faces AS f ON f.face_id = l.face_id "
        "JOIN file_locations AS loc ON loc.asset_id = f.asset_id "
        "WHERE loc.relative_path = ? AND l.decision = 'CONFIRMED'",
        (relative,),
    )
    assert row is not None, f"{relative} has no confirmed person"
    return int(row["person_id"])


# ------------------------------------------------------------------ the happy path
def test_the_real_pipeline_groups_one_person_and_keeps_strangers_apart(harness: Harness) -> None:
    processed(harness)
    assert len(harness.targets()) == EXPECTED_ASSETS
    assert len(harness.faces()) == EXPECTED_FACES
    grouped = harness.person_files()
    assert len(grouped) == EXPECTED_PERSONS
    astronaut_person = person_of(harness, "astronaut.png")
    assert set(grouped[astronaut_person]) == {
        "astronaut.png",
        "astronaut_bright.jpg",
        "composite.png",
    }
    # The six people of the group photo are six different groups, none of them the astronaut.
    group_assets = {harness.asset_id("group.jpg")}
    group_rows = harness.catalog.read_all(
        "SELECT DISTINCT l.person_id AS person_id, f.asset_id AS asset_id FROM face_person_links AS l "
        "JOIN faces AS f ON f.face_id = l.face_id WHERE l.decision = 'CONFIRMED'"
    )
    strangers = {
        int(row["person_id"]) for row in group_rows if int(row["asset_id"]) in group_assets
    }
    assert len(strangers) == 6
    assert astronaut_person not in strangers
    assert person_of(harness, "portrait.png") not in strangers


def test_the_index_and_the_status_are_caught_up_after_the_closed_loop(harness: Harness) -> None:
    processed(harness)
    index = harness.index_state()
    assert index["state"] == "READY" and index["is_active"] is True
    assert index["space_id"] == contract.FACE_SPACE_ID
    assert index["dimension"] == contract.FACE_DIMENSION
    assert set(index["entries"]) == {"CONFIRMED"}
    assert index["entries"]["CONFIRMED"] == EXPECTED_FACES
    # Only the effectively linked, high-quality faces are representatives.
    assert 0 < index["representatives"] < index["candidates"]
    summary = harness.summary()
    assert summary["state"] == "SYNCED"
    assert summary["library_assets"] == EXPECTED_ASSETS
    assert summary["analyzed_assets"] == EXPECTED_ASSETS
    assert summary["assets_with_faces"] == EXPECTED_ASSETS
    assert summary["faces"] == EXPECTED_FACES
    assert summary["persons"] == EXPECTED_PERSONS
    assert summary["associations_queued"] == 0
    assert summary["pending_jobs"] == 0


def test_the_status_is_truthful_before_and_between_the_two_halves(harness: Harness) -> None:
    harness.install_weights()
    harness.scan()
    before = harness.summary()
    assert before["state"] == "NOT_RUN"
    assert before["analyzed_assets"] == 0
    assert before["faces"] == 0
    assert before["library_assets"] == EXPECTED_ASSETS

    # Facts only: the faces exist, but neither the candidate index nor the grouping has run.
    harness.enqueue()
    harness.run_faces()
    between = harness.summary()
    assert between["state"] == "LAGGING"
    assert between["analyzed_assets"] == EXPECTED_ASSETS
    assert between["faces"] == EXPECTED_FACES
    assert between["persons"] == 0
    assert between["associations_queued"] == EXPECTED_ASSETS
    assert between["index"]["pending_candidates"] == EXPECTED_FACES

    # An asset whose faces are not confirmed yet is deferred, never grouped blindly.
    deferred = harness.associate()
    assert deferred["processed"] == 0 and deferred["deferred"] == EXPECTED_ASSETS
    harness.index()
    finished = harness.associate()
    assert finished["processed"] == EXPECTED_ASSETS
    assert harness.summary()["state"] == "SYNCED"


def test_a_rerun_creates_no_second_face_person_or_artifact(harness: Harness) -> None:
    processed(harness)
    before = {
        "faces": len(harness.faces()),
        "persons": len(harness.persons()),
        "links": len(harness.links()),
        "artifacts": int(
            harness.catalog.read_one("SELECT COUNT(*) AS total FROM artifacts")["total"]
        ),
        "artifact_ids": sorted(
            str(row["artifact_id"])
            for row in harness.catalog.read_all("SELECT artifact_id FROM artifacts")
        ),
    }
    result = harness.detect(force=True)
    assert result["enqueue"]["rewound"] == EXPECTED_ASSETS
    after = {
        "faces": len(harness.faces()),
        "persons": len(harness.persons()),
        "links": len(harness.links()),
        "artifacts": int(
            harness.catalog.read_one("SELECT COUNT(*) AS total FROM artifacts")["total"]
        ),
        "artifact_ids": sorted(
            str(row["artifact_id"])
            for row in harness.catalog.read_all("SELECT artifact_id FROM artifacts")
        ),
    }
    assert after == before
    # The logical job rows are reused too: a forced re-run rewinds, it never duplicates.
    assert int(
        harness.catalog.read_one(
            "SELECT COUNT(*) AS total FROM jobs WHERE task_type = ?",
            (contract.FACE_TASK_TYPE,),
        )["total"]
    ) == EXPECTED_ASSETS


def test_low_quality_and_duplicate_faces_are_only_suggestions(harness: Harness) -> None:
    processed(harness)
    suggestions = {
        (str(row["reason"]), str(row["decision"]), str(row["assignment_kind"]))
        for row in harness.links()
        if str(row["decision"]) == "SUGGESTED"
    }
    assert (contract.REASON_LOW_QUALITY, "SUGGESTED", "AUTO") in suggestions
    assert (contract.REASON_SAME_IMAGE_CONFLICT, "SUGGESTED", "AUTO") in suggestions
    # The low-quality face is not a confirmed member of any group.
    small = harness.faces_of("astronaut_small.jpg")
    assert small and str(small[0]["quality_state"]) == "LOW"
    link = harness.catalog.read_one(
        "SELECT decision FROM face_person_links WHERE face_id = ?", (str(small[0]["face_id"]),)
    )
    assert link is None or str(link["decision"]) == "SUGGESTED"
    # Every automatic row this build wrote is AUTO: the human kinds belong to P3-03/P3-04.
    assert {str(row["assignment_kind"]) for row in harness.links()} == {"AUTO"}


# ------------------------------------------------------------------- recovery (D)
def test_the_candidate_index_crash_window_is_repairable(harness: Harness) -> None:
    harness.install_weights()
    harness.scan()
    harness.enqueue()
    harness.run_faces()
    index = FaceIndexCoordinator(harness.catalog)
    with pytest.raises(_InjectedCrash):
        index.process_next(now_ms=harness.clock(), failpoint="after_publish")
    # The candidate row was published, but SQLite has not confirmed it yet.
    row = harness.catalog.read_one(
        "SELECT state FROM face_index_entries WHERE generation_id = ? ORDER BY face_id LIMIT 1",
        (index.generation_id,),
    )
    assert str(row["state"]) == "PENDING_REPAIR"
    assert int(
        harness.catalog.read_one("SELECT COUNT(*) AS total FROM face_candidates")["total"]
    ) == 1
    assert harness.associate()["deferred"] == EXPECTED_ASSETS

    # A replay republishes the identical bytes and confirms the entry, idempotently.
    # The crashed event was never confirmed, so the replay handles it *and* the remaining ones.
    published = index.process_all(now_ms=harness.clock())
    assert published == EXPECTED_FACES
    assert int(
        harness.catalog.read_one("SELECT COUNT(*) AS total FROM face_candidates")["total"]
    ) == EXPECTED_FACES
    states = {
        str(item["state"])
        for item in harness.catalog.read_all("SELECT state FROM face_index_entries")
    }
    assert states == {"CONFIRMED"}
    assert harness.associate()["processed"] == EXPECTED_ASSETS


def test_a_late_result_writes_nothing_and_the_lease_is_taken_over(harness: Harness) -> None:
    harness.install_weights()
    harness.scan()
    harness.enqueue()
    # Recording the capability evidence is what a real run does before the first claim; a
    # zero-bounded run does exactly that and drains nothing.
    run_queue(harness.catalog, data_root=harness.data_root, device="cpu", limit=0)
    scheduler = Scheduler(harness.catalog, [harness.spec()], lease_seconds=60)
    cache = FaceEngineCache()
    executor = face_executor(harness.catalog, data_root=harness.data_root, engine_cache=cache)
    request = scheduler.claim_next(worker_id="w", now_ms=harness.clock())
    assert request is not None
    claimed_asset = int(request.subject_id)
    result = executor(request.to_envelope())
    assert result["state"] == "SUCCEEDED"

    harness.clock.advance(61_000)
    with pytest.raises(CatalogError) as error:
        scheduler.submit(JobResult.from_envelope(result), now_ms=harness.clock())
    assert error.value.code == STALE_LEASE
    assert harness.faces() == []
    assert harness.links() == []
    assert int(
        harness.catalog.read_one("SELECT COUNT(*) AS total FROM artifacts")["total"]
    ) == 0

    # The expired lease is taken over (one lost execution), and the retry commits correctly.
    takeover = scheduler.claim_next(worker_id="w2", now_ms=harness.clock())
    assert takeover is not None and int(takeover.job_id) == int(request.job_id)
    retry = executor(takeover.to_envelope())
    assert retry["state"] == "SUCCEEDED"
    job = scheduler.submit(JobResult.from_envelope(retry), now_ms=harness.clock())
    assert str(job.state) == "SUCCEEDED"
    assert len(harness.faces(claimed_asset)) == 1


def test_a_content_change_after_the_job_was_bound_writes_nothing(harness: Harness) -> None:
    harness.install_weights()
    harness.scan()
    harness.enqueue()
    # The bytes change after the catalog observed them: the executor refuses to recognise a
    # different picture and blocks the job instead of writing a fact about the old revision.
    target = harness.file_for("portrait.png")
    from PIL import Image

    Image.new("RGB", (300, 300), (12, 34, 56)).save(target)
    harness.run_faces()
    assert harness.faces(harness.asset_id("portrait.png")) == []
    # The other assets are unaffected: only the changed one blocks.
    assert len(harness.faces()) == EXPECTED_FACES - 1
    job = harness.catalog.read_one(
        "SELECT state, last_error_code FROM jobs WHERE subject_id = ? AND task_type = ?",
        (str(harness.asset_id("portrait.png")), contract.FACE_TASK_TYPE),
    )
    assert str(job["state"]) == "BLOCKED"
    assert str(job["last_error_code"]) in (INPUT_UNSTABLE, "FILE_READ_ERROR")

    # A re-scan re-binds the asset to the new content; a job for the *old* revision is now a
    # stale submission and must not write anything for the new picture.
    harness.scan()
    fresh = harness.enqueue([{"asset_id": harness.asset_id("portrait.png"), "input_revision": "0" * 64}])
    assert fresh["queued"] == 1
    harness.run_faces()
    assert harness.faces(harness.asset_id("portrait.png")) == []
    stale_job = harness.catalog.read_one(
        "SELECT state, last_error_code FROM jobs WHERE subject_id = ? AND input_revision = ? "
        "AND task_type = ?",
        (str(harness.asset_id("portrait.png")), "0" * 64, contract.FACE_TASK_TYPE),
    )
    assert str(stale_job["last_error_code"]) == STALE_INPUT_REVISION


def test_missing_or_corrupted_weights_are_structured_refusals(harness: Harness) -> None:
    harness.scan()
    harness.enqueue()
    harness.install_weights(omit="recognition")
    # The controlled entry point proves the weights before it queues anything, so a broken
    # model root is one structured failure rather than a queue full of blocked jobs.
    from photo_search.face import weights as weights_module

    with pytest.raises(CatalogError) as missing:
        weights_module.resolve_and_verify(harness.data_root)
    assert missing.value.code == FACE_WEIGHTS_MISSING
    result = harness.run_faces()
    assert result["cpu"]["usable"] is False
    assert result["cpu"]["error"]["code"] == FACE_WEIGHTS_MISSING
    assert harness.faces() == []
    assert harness.jobs().get("BLOCKED") == EXPECTED_ASSETS

    harness.install_weights(corrupt="detection")
    result = harness.run_faces()
    assert result["cpu"]["error"]["code"] == FACE_WEIGHTS_HASH_MISMATCH
    assert harness.faces() == []


def test_a_disappearing_source_blocks_without_writing_facts(harness: Harness) -> None:
    harness.install_weights()
    harness.scan()
    harness.enqueue()
    harness.file_for("astronaut.png").unlink()
    harness.run_faces()
    assert harness.faces(harness.asset_id("astronaut.png")) == []
    # Only the vanished source blocks; every other asset still produced its faces.
    assert len(harness.faces()) == EXPECTED_FACES - 1
    job = harness.catalog.read_one(
        "SELECT state, last_error_code FROM jobs WHERE subject_id = ? AND task_type = ?",
        (str(harness.asset_id("astronaut.png")), contract.FACE_TASK_TYPE),
    )
    assert str(job["state"]) == "BLOCKED"

def test_a_position_the_catalog_calls_unavailable_blocks_without_writing_facts(
    harness: Harness,
) -> None:
    """Even on disk, a position the catalog knows is gone is never opened hopefully."""
    fresh = Harness(harness.tmp_path / "offline")
    try:
        fresh.install_weights()
        fresh.scan()
        asset_id = fresh.asset_id("portrait.png")
        revision = str(
            fresh.catalog.read_one(
                "SELECT sha256 FROM assets WHERE asset_id = ?", (asset_id,)
            )["sha256"]
        )
        with fresh.catalog.write() as connection:
            connection.execute(
                "UPDATE file_locations SET availability = 'MISSING' WHERE asset_id = ?",
                (asset_id,),
            )
        fresh.enqueue([{"asset_id": asset_id, "input_revision": revision}])
        fresh.run_faces()
        assert fresh.faces(asset_id) == []
        job = fresh.catalog.read_one(
            "SELECT state, last_error_code, blocking_reason FROM jobs WHERE subject_id = ? "
            "AND task_type = ?",
            (str(asset_id), contract.FACE_TASK_TYPE),
        )
        assert str(job["state"]) == "BLOCKED"
        assert str(job["last_error_code"]) == "SOURCE_FILE_MISSING"
        assert str(job["blocking_reason"]) == "SOURCE_UNAVAILABLE"
        # Only that one asset was queued, and it wrote nothing at all.
        assert fresh.faces() == []
        assert fresh.jobs().get("BLOCKED") == 1
    finally:
        fresh.close()


def test_the_face_index_never_touches_the_image_index(harness: Harness) -> None:
    processed(harness)
    assert int(
        harness.catalog.read_one("SELECT COUNT(*) AS total FROM index_generations")["total"]
    ) == 0
    assert int(
        harness.catalog.read_one("SELECT COUNT(*) AS total FROM index_entries")["total"]
    ) == 0
    assert int(harness.catalog.read_one("SELECT COUNT(*) AS total FROM index_outbox")["total"]) == 0
    generation = harness.catalog.read_one("SELECT space_id FROM face_index_generations")
    assert str(generation["space_id"]) == contract.FACE_SPACE_ID
    assert str(generation["space_id"]) != "clip:1"


def test_the_cpu_run_is_reported_as_a_degradation(harness: Harness) -> None:
    processed(harness, device="cpu")
    jobs = harness.catalog.read_all(
        "SELECT requested_device, resolved_device, runtime_provider FROM jobs "
        "WHERE task_type = ?",
        (contract.FACE_TASK_TYPE,),
    )
    assert {str(row["resolved_device"]) for row in jobs} == {"cpu"}
    assert {str(row["requested_device"]) for row in jobs} == {"cpu"}
    summary = harness.summary()
    assert summary["degraded_jobs"] == EXPECTED_ASSETS
    assert "degraded" not in json.dumps(summary["thresholds"])


def test_an_explicit_gpu_request_never_falls_back_to_cpu(harness: Harness) -> None:
    from photo_search.runtime.device import available_providers

    harness.install_weights()
    harness.scan()
    harness.enqueue(device="gpu")
    result = harness.run_faces(device="gpu")
    if "CUDAExecutionProvider" in available_providers() and result["gpu"]["usable"]:
        # A machine whose CUDA really works: the job runs on the GPU and says so.
        jobs = harness.catalog.read_all(
            "SELECT resolved_device FROM jobs WHERE task_type = ?",
            (contract.FACE_TASK_TYPE,),
        )
        assert {str(row["resolved_device"]) for row in jobs} == {"gpu"}
        return
    # Otherwise the GPU is a *recorded* capability fact and the job blocks: never a CPU run.
    assert harness.faces() == []
    assert harness.jobs().get("BLOCKED") == EXPECTED_ASSETS
    row = harness.catalog.read_one(
        "SELECT blocking_reason FROM jobs WHERE task_type = ? LIMIT 1",
        (contract.FACE_TASK_TYPE,),
    )
    assert str(row["blocking_reason"]) == "RUNTIME_CAPABILITY_UNAVAILABLE"


def test_the_status_projection_never_leaks_a_coordinate_or_a_path(harness: Harness) -> None:
    processed(harness)
    summary = harness.summary()
    serialised = json.dumps(summary, ensure_ascii=False)
    assert str(harness.data_root) not in serialised
    assert str(harness.library) not in serialised
    assert "bbox" not in serialised and "landmark" not in serialised
    assert "embedding" not in serialised and "vector" not in serialised
    # Counts and thresholds are what the projection is for.
    assert summary["thresholds"]["merge"] > summary["thresholds"]["suggest"]


def test_human_knowledge_wins_over_the_automatic_path(harness: Harness) -> None:
    """The reserved read order of the human tables has to hold on every commit.

    P3-03 delivers ``face_assignment_controls``/``face_exclusions`` (migration 0011), so this
    case now seeds the real tables instead of creating two stand-ins: the columns it writes
    are exactly the architecture-required ones, which is the compatibility the P3-03
    migration had to keep.
    """
    processed(harness)
    face = harness.faces_of("astronaut.png")[0]
    face_id = str(face["face_id"])
    person_id = person_of(harness, "astronaut.png")
    with harness.catalog.write() as connection:
        # The two human tables of architecture §4.2/§8.3.4, plus a manual link.
        connection.execute(
            "INSERT INTO face_assignment_controls(face_id, mode, revision, last_operation_id, "
            "updated_at) VALUES (?, 'MANUAL_UNASSIGNED', 1, 'op-1', 1)",
            (face_id,),
        )
        # The P3-04 transaction replaces the automatic effective link with the manual one: one
        # face has exactly one effective person, so the automatic row goes first.
        connection.execute(
            "DELETE FROM face_person_links WHERE face_id = ? AND assignment_kind = 'AUTO'",
            (face_id,),
        )
        connection.execute(
            "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
            "confidence, reason, revision, last_operation_id, created_at, updated_at) "
            "VALUES (?, ?, 'MANUAL', 'CONFIRMED', 1.0, NULL, 1, 'op-1', 1, 1)",
            (face_id, person_id),
        )
    with harness.catalog.write() as connection:
        connection.execute(
            "INSERT INTO face_association_queue(asset_id, spec_revision, face_revision, queued_at) "
            "VALUES (?, ?, 99, 1)",
            (harness.asset_id("astronaut.png"), contract.FACE_SPEC_REVISION),
        )
    outcome = harness.associate()
    assert outcome["human_controlled"] >= 1
    link = harness.catalog.read_one(
        "SELECT assignment_kind, decision FROM face_person_links WHERE face_id = ? "
        "AND assignment_kind = 'MANUAL'",
        (face_id,),
    )
    assert (str(link["assignment_kind"]), str(link["decision"])) == ("MANUAL", "CONFIRMED")
    # A re-detection must not delete a face a human row owns.
    harness.detect(force=True)
    assert any(str(item["face_id"]) == face_id for item in harness.faces())
    assert harness.catalog.read_one(
        "SELECT 1 FROM face_person_links WHERE face_id = ? AND assignment_kind = 'MANUAL'",
        (face_id,),
    ) is not None
