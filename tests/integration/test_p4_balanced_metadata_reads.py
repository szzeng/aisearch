"""Exact metadata aggregates and stored-cover shortcuts over synthetic durable facts."""

from pathlib import Path

import pytest

from photo_search.persons import albums, members  # isort: skip
from photo_search.catalog import store
from photo_search.catalog.db import Catalog
from photo_search.face.detail_policy import POLICY_REVISION
from photo_search.refresh.progress import enrichment_counts
from tools.benchmarks.p4_balanced_metadata import seed


@pytest.fixture()
def catalog(tmp_path: Path):
    instance = Catalog.open(tmp_path / "data/catalog/catalog.db")
    seed(instance, size=36)
    try:
        yield instance
    finally:
        instance.close()


@pytest.mark.parametrize("source_id", [None, 1, 2, 99])
def test_location_counts_keep_duplicates_missing_and_unbound(catalog, source_id):
    all_rows = catalog.read_all("SELECT source_id,asset_id,availability FROM file_locations")
    rows = [row for row in all_rows if source_id is None or row["source_id"] == source_id]
    with catalog.read_snapshot():
        actual = store.catalog_counts(catalog.connection, source_id=source_id)
    assert actual == {
        "sources": 5 if source_id is None else 1,
        "assets": 36 if source_id is None else len({row["asset_id"] for row in rows if row["asset_id"] is not None}),
        "assets_total": 36,
        "locations": len(rows),
        "locations_present": sum(row["availability"] == "PRESENT" for row in rows),
        "locations_missing": sum(row["availability"] == "MISSING" for row in rows),
        "locations_pending_content": sum(row["availability"] == "PRESENT" and row["asset_id"] is None for row in rows),
        "scans": 0,
        "jobs": 108 if source_id is None else 0,
    }


@pytest.mark.parametrize("source_id", [None, 1, 3, 99])
def test_enrichment_counts_deduplicate_tasks_and_keep_revision_rules(catalog, source_id):
    rows = catalog.read_all("SELECT source_id,asset_id FROM file_locations WHERE availability='PRESENT'")
    scope = {row["asset_id"] for row in rows if source_id is None or row["source_id"] == source_id}
    tasks = ("image_face", "image_face_detail_auto")
    jobs = [row for row in catalog.read_all("SELECT * FROM jobs")
            if row["task_type"] in tasks and int(row["subject_id"]) in scope]
    expected = {"handled": set(), "pending": set(), "attempted": set()}
    for job in jobs:
        asset_id = int(job["subject_id"])
        expected["attempted"].add(asset_id)
        if job["input_revision"] != f"{asset_id:064x}":
            continue
        if job["state"] in ("SUCCEEDED", "FAILED", "BLOCKED", "CANCELLED"):
            expected["handled"].add(asset_id)
        if job["state"] in ("QUEUED", "RUNNING", "RETRY_WAIT"):
            expected["pending"].add(asset_id)
    actual = enrichment_counts(catalog, source_id=source_id, task_type=tasks, processor_id="face-model@1")
    assert actual == {key: len(value) for key, value in expected.items()}


def test_fact_table_handled_is_independent_of_job_revision_or_existence(catalog):
    with catalog.write() as connection:
        connection.executemany(
            "INSERT INTO asset_metadata(asset_id,time_source,time_confidence) VALUES(?,'FILE_MTIME','LOW')",
            [(1,), (7,), (13,)],
        )
    assert enrichment_counts(catalog, task_type="taken_time_extract", processor_id="taken-time-exif@1",
                             fact_table="asset_metadata") == {"handled": 2, "pending": 0, "attempted": 0}


def test_multiple_processors_preserve_overlapping_current_states(catalog):
    with catalog.write() as connection:
        connection.executemany(
            "INSERT INTO jobs(subject_type,subject_id,input_revision,task_type,processor_id,state,"
            "run_after,created_at,updated_at) VALUES('asset',?,?,'stage-test',?,?,1,1,1)",
            [("1", f"{1:064x}", "a", "SUCCEEDED"), ("1", "old", "a", "RETRY_WAIT"),
             ("1", f"{1:064x}", "b", "QUEUED"), ("2", "old", "a", "SUCCEEDED"),
             ("3", f"{3:064x}", "other", "SUCCEEDED")],
        )
    assert enrichment_counts(catalog, task_type="stage-test", processor_id=("a", "b")) == {
        "handled": 1, "pending": 1, "attempted": 2,
    }


def test_face_and_photo_count_differ_for_two_effective_faces_in_one_asset(catalog):
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO faces(face_id,asset_id,spec_revision,detector_id,ordinal,input_revision,"
            "bbox_json,landmarks_json,det_score,face_pixels,blur_variance,quality_state,artifact_id,"
            "face_revision,created_at,updated_at) SELECT ?,asset_id,spec_revision,detector_id,1,input_revision,"
            "bbox_json,landmarks_json,det_score,face_pixels,blur_variance,quality_state,artifact_id,"
            "face_revision,created_at,updated_at FROM faces WHERE face_id=?", (f"{500:064x}", f"{2:064x}"),
        )
        connection.execute(
            "INSERT INTO face_person_links(face_id,person_id,assignment_kind,decision,confidence,"
            "revision,created_at,updated_at) VALUES(?,1,'MANUAL','CONFIRMED',1,1,1,1)", (f"{500:064x}",),
        )
    valid = {i for i in range(1, 37) if i % 11 and i % 17}
    assert members.member_counts(catalog.connection, 1) == (len(valid) + 1, len(valid))
    assert members.member_counts(catalog.connection, 999) == (0, 0)


def test_valid_stored_avatar_wins_even_when_quality_is_worse(catalog):
    with catalog.write() as connection:
        connection.execute("UPDATE faces SET quality_state='LOW',quality_reason='SMALL' WHERE face_id=?", (f"{2:064x}",))
    row = catalog.read_one("SELECT * FROM persons WHERE person_id=1")
    before = catalog.connection.total_changes
    assert albums._album_summary(catalog.connection, row)["avatar_face_id"] == f"{2:064x}"
    assert catalog.connection.total_changes == before


@pytest.mark.parametrize("invalid", ["unset", "unassigned", "excluded", "suggested", "superseded"])
def test_invalid_stored_avatar_falls_back_without_rewriting_human_choice(catalog, invalid):
    with catalog.write() as connection:
        if invalid == "unset":
            connection.execute("UPDATE persons SET avatar_face_id=NULL WHERE person_id=1")
        elif invalid == "unassigned":
            connection.execute("INSERT INTO face_assignment_controls(face_id,mode,revision,updated_at) VALUES(?,'MANUAL_UNASSIGNED',1,1)", (f"{2:064x}",))
        elif invalid == "excluded":
            connection.execute("INSERT INTO face_exclusions(face_id,person_id,active,created_at) VALUES(?,1,1,1)", (f"{2:064x}",))
        elif invalid == "suggested":
            connection.execute("UPDATE face_person_links SET decision='SUGGESTED',reason='BORDERLINE' WHERE face_id=?", (f"{2:064x}",))
        else:
            connection.execute("INSERT INTO face_detail_auto_assets(asset_id,input_revision,artifact_id,policy_revision,ready,updated_at) VALUES(2,?,'synthetic',?,1,1)", (f"{2:064x}", POLICY_REVISION))
    row = catalog.read_one("SELECT * FROM persons WHERE person_id=1")
    before = catalog.connection.total_changes
    assert albums._album_summary(catalog.connection, row)["avatar_face_id"] == f"{1:064x}"
    assert catalog.connection.total_changes == before
    assert catalog.read_one("SELECT avatar_face_id FROM persons WHERE person_id=1")[0] == row["avatar_face_id"]
