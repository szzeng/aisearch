"""P4-00B: the extraction chain, its observation guards, idempotency and the version gate.

Every photo here is synthetic and lives under ``tmp_path``; the work runs through the real
P2-02 scheduler with the real executor and the real persistence hook, so what these cases
observe is the production state machine - not a helper function's idea of it.
"""

from __future__ import annotations

import json
from pathlib import Path

from p4_time_fixtures import (
    assets_by_basename,
    filter_causes,
    filter_revision,
    row_for,
    run_extraction,
    write_exif_jpeg,
    write_plain_jpeg,
)

from photo_search.artifacts.store import ArtifactStore
from photo_search.catalog.errors import STALE_INPUT_REVISION
from photo_search.jobs import queue as jobs
from photo_search.taken_time import contract
from photo_search.taken_time import exif as exif_module
from photo_search.taken_time import extract as time_extract
from photo_search.taken_time import read as time_read


def payload_for(api_env, asset_id: int) -> dict:
    """The published structured artifact of one asset (the executor's own payload)."""
    catalog = api_env.service.catalog
    row = catalog.read_one("SELECT sha256 FROM assets WHERE asset_id = ?", (int(asset_id),))
    artifact = catalog.read_one(
        "SELECT * FROM artifacts WHERE processor_id = ? AND input_revision LIKE ? "
        "ORDER BY job_id DESC LIMIT 1",
        (time_extract.TAKEN_TIME_PROCESSOR_ID, str(row["sha256"]) + ":%"),
    )
    assert artifact is not None
    store = ArtifactStore.open(catalog.data_root)
    return json.loads(store.read(artifact).decode("utf-8"))


def test_the_source_chain_is_exactly_the_frozen_priority(api_env) -> None:
    write_exif_jpeg(
        api_env.library / "甲 原图.jpg",
        root="2021:05:06 07:08:09",
        original="2022:07:05 18:31:07",
        offset_original="+08:00",
        subsec_original="12345",
        digitized="2022:07:05 18:31:08",
        offset_digitized="+08:00",
        make="TestMake",
        model="TestModel",
    )
    write_exif_jpeg(
        api_env.library / "乙 坏原图.jpg",
        original="2022:02:30 10:00:00",
        digitized="2023:01:02 03:04:05",
        offset_digitized="-05:30",
    )
    write_exif_jpeg(api_env.library / "丙 只有根.jpg", root="2020:12:31 23:59:59", offset_root="+09:00")
    write_exif_jpeg(
        api_env.library / "戊 无偏移.jpg",
        original="2022:07:05 18:31:07",
        offset_digitized=None,
    )
    write_plain_jpeg(api_env.library / "丁 无EXIF.jpg")
    source = api_env.register_source()
    api_env.scan(source["source_id"])

    result = run_extraction(api_env.service.catalog)
    assert result["steps"] == 5
    names = assets_by_basename(api_env.service.catalog)
    assert len(names) == 5

    first = row_for(api_env.service.catalog, names["甲 原图.jpg"])
    assert first is not None
    assert first.time_source == "EXIF_ORIGINAL"
    assert first.time_confidence == "HIGH"
    assert first.timezone_offset == 480
    assert first.taken_at_raw == "2022:07:05 18:31:07"
    assert first.taken_at_sort == contract.civil_key(2022, 7, 5, 18, 31, 7, 123)
    assert first.camera == "TestMake TestModel"
    assert payload_for(api_env, names["甲 原图.jpg"])["location_id"] is not None

    second = row_for(api_env.service.catalog, names["乙 坏原图.jpg"])
    assert second is not None and second.time_source == "EXIF_DIGITIZED"
    assert second.time_confidence == "MEDIUM" and second.timezone_offset == -330
    assert second.taken_at_raw == "2023:01:02 03:04:05"

    third = row_for(api_env.service.catalog, names["丙 只有根.jpg"])
    assert third is not None and third.time_source == "EXIF_ROOT"
    assert third.time_confidence == "LOW" and third.timezone_offset == 540
    assert third.taken_at_sort == contract.civil_key(2020, 12, 31, 23, 59, 59)
    assert contract.label_for(third.time_source) == "file_time"

    # ADR-0011 §11.1: the same wall clock with and without a recorded offset has exactly the
    # same civil key - the offset never participates.
    plain_offset = row_for(api_env.service.catalog, names["戊 无偏移.jpg"])
    assert plain_offset is not None and plain_offset.time_source == "EXIF_ORIGINAL"
    assert plain_offset.timezone_offset is None
    assert plain_offset.time_confidence == "MEDIUM"
    assert plain_offset.taken_at_sort == contract.civil_key(2022, 7, 5, 18, 31, 7)
    assert payload_for(api_env, names["戊 无偏移.jpg"])["taken_at_sort"] == (
        payload_for(api_env, names["甲 原图.jpg"])["taken_at_sort"] - 123
    )

    fourth = row_for(api_env.service.catalog, names["丁 无EXIF.jpg"])
    assert fourth is not None and fourth.time_source == "FILE_MTIME"
    assert fourth.taken_at_sort is None and fourth.timezone_offset is None
    assert fourth.time_confidence == "LOW"
    assert fourth.taken_at_raw.endswith("+00:00") and "T" in fourth.taken_at_raw

    assert filter_causes(api_env.service.catalog) == (
        ["ASSET_ADDED"] * 5 + ["CAPTURE_TIME_CHANGED"] * 5
    )
    block = time_read.state_block(api_env.service.catalog.connection)
    assert block == {
        "state": "READY",
        "assets_total": 5,
        "extracted_assets": 5,
        "capture_time_assets": 3,
    }


def test_a_position_that_changes_while_reading_is_not_adopted(api_env, monkeypatch) -> None:
    write_exif_jpeg(
        api_env.library / "读中变化.jpg",
        original="2022:07:05 18:31:07",
        offset_original="+08:00",
    )
    source = api_env.register_source()
    api_env.scan(source["source_id"])
    names = assets_by_basename(api_env.service.catalog)
    asset_id = names["读中变化.jpg"]

    original_read = exif_module.read_time_fields

    def touch_while_reading(path: Path):
        fields = original_read(path)
        # The post-read observation guard must see the moved mtime and drop this position.
        import os

        info = os.stat(path)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
        return fields

    monkeypatch.setattr(exif_module, "read_time_fields", touch_while_reading)
    run_extraction(api_env.service.catalog)
    monkeypatch.setattr(exif_module, "read_time_fields", original_read)

    row = row_for(api_env.service.catalog, asset_id)
    assert row is not None and row.time_source == "FILE_MTIME"
    assert row.taken_at_sort is None

    # The asset is left for the next extraction: a rescan records the new observation and a
    # forced re-extraction then publishes the real EXIF fact.
    api_env.scan(source["source_id"])
    run_extraction(api_env.service.catalog, force=True)
    again = row_for(api_env.service.catalog, asset_id)
    assert again is not None and again.time_source == "EXIF_ORIGINAL"
    assert again.timezone_offset == 480
    assert filter_causes(api_env.service.catalog) == [
        "ASSET_ADDED", "CAPTURE_TIME_CHANGED", "ASSET_CONTENT_DETACHED",
        "ASSET_LOCATION_BOUND", "CAPTURE_TIME_CHANGED",
    ]


def test_a_missing_position_never_fails_the_asset_and_uses_the_minimum_mtime(api_env) -> None:
    root_one = api_env.tmp_path / "root-one"
    root_two = api_env.tmp_path / "root-two"
    first = write_plain_jpeg(root_one / "同一张.jpg", age_ms=7_200_000)
    second = write_plain_jpeg(root_two / "同一张 副本.jpg", age_ms=3_600_000)
    source_one = api_env.register_source("root one", path=root_one)
    source_two = api_env.register_source("root two", path=root_two)
    api_env.scan(source_one["source_id"])
    api_env.scan(source_two["source_id"])
    rows = api_env.service.catalog.read_all(
        "SELECT asset_id, COUNT(*) AS total FROM file_locations GROUP BY asset_id"
    )
    assert len(rows) == 1 and int(rows[0]["total"]) == 2
    asset_id = int(rows[0]["asset_id"])
    expected_min = min(first.stat().st_mtime_ns, second.stat().st_mtime_ns)
    assert expected_min == first.stat().st_mtime_ns < second.stat().st_mtime_ns

    first.unlink()
    api_env.scan(source_one["source_id"])
    availability = [
        str(row["availability"])
        for row in api_env.service.catalog.read_all(
            "SELECT availability FROM file_locations WHERE asset_id = ? ORDER BY location_id",
            (asset_id,),
        )
    ]
    assert availability == ["MISSING", "PRESENT"]

    run_extraction(api_env.service.catalog)
    row = row_for(api_env.service.catalog, asset_id)
    assert row is not None and row.time_source == "FILE_MTIME"
    payload = payload_for(api_env, asset_id)
    assert payload["taken_at_sort"] is None and payload["timezone_offset"] is None
    # The tail value is the minimum over *every* position, including the now-missing one.
    from datetime import datetime, timezone

    assert row.taken_at_raw == datetime.fromtimestamp(
        expected_min / 1_000_000_000, tz=timezone.utc
    ).isoformat(timespec="microseconds")
    # One asset, extracted, with no capture time at all: the honest state is
    # ``NO_CAPTURE_TIME``, never ``READY``-as-in-"has capture times".
    assert (
        time_read.state_block(api_env.service.catalog.connection)["state"]
        == "NO_CAPTURE_TIME"
    )


def test_a_multi_position_asset_skips_an_unusable_position(api_env) -> None:
    root_one = api_env.tmp_path / "multi-one"
    root_two = api_env.tmp_path / "multi-two"
    write_exif_jpeg(
        root_one / "同一张.jpg",
        original="2022:07:05 18:31:07",
        offset_original="+08:00",
    )
    # Byte-identical copy: one asset, two registered positions.
    raw = (root_one / "同一张.jpg").read_bytes()
    root_two.mkdir(parents=True, exist_ok=True)
    twin = root_two / "同一张 副本.jpg"
    twin.write_bytes(raw)
    from p4_time_fixtures import stamp

    stamp(twin)
    source_one = api_env.register_source("multi one", path=root_one)
    source_two = api_env.register_source("multi two", path=root_two)
    api_env.scan(source_one["source_id"])
    api_env.scan(source_two["source_id"])
    locations = api_env.service.catalog.read_all(
        "SELECT location_id, source_id FROM file_locations ORDER BY location_id"
    )
    assert len(locations) == 2
    asset_id = int(
        api_env.service.catalog.read_one("SELECT MIN(asset_id) AS value FROM assets")["value"]
    )
    # Make the *lowest* location unusable without touching the catalog observation: the
    # pre-read guard refuses it, so the walk must continue to the next PRESENT position.
    (root_one / "同一张.jpg").unlink()
    run_extraction(api_env.service.catalog)
    row = row_for(api_env.service.catalog, asset_id)
    assert row is not None and row.time_source == "EXIF_ORIGINAL"
    payload = payload_for(api_env, asset_id)
    assert int(payload["location_id"]) == int(locations[1]["location_id"])


def test_the_extraction_is_idempotent_across_a_restart(api_env) -> None:
    write_exif_jpeg(api_env.library / "幂等.jpg", original="2022:07:05 18:31:07", offset_original="+08:00")
    write_plain_jpeg(api_env.library / "幂等 二.jpg")
    source = api_env.register_source()
    api_env.scan(source["source_id"])
    revision_before_extraction = filter_revision(api_env.service.catalog)
    run_extraction(api_env.service.catalog)
    names = assets_by_basename(api_env.service.catalog)
    rows_before = {name: row_for(api_env.service.catalog, asset_id) for name, asset_id in names.items()}
    revision_before = filter_revision(api_env.service.catalog)
    # Exactly one ``CAPTURE_TIME_CHANGED`` per really-changed asset, in one transaction each.
    assert revision_before - revision_before_extraction == 2
    assert filter_causes(api_env.service.catalog)[-2:] == ["CAPTURE_TIME_CHANGED"] * 2

    # A plain re-enqueue adds no new logical job (the five-field key), so nothing runs.
    again = run_extraction(api_env.service.catalog)
    assert again["steps"] == 0
    assert again["enqueued"]["queued"] == 0
    assert again["enqueued"]["existing"] == 2
    # A forced re-read rewinds the same jobs and reproduces exactly the same facts: no row is
    # rewritten and the shared revision does not move.
    forced = run_extraction(api_env.service.catalog, force=True)
    assert forced["steps"] == 2
    assert forced["enqueued"]["rewound"] == 2
    assert filter_revision(api_env.service.catalog) == revision_before
    assert {
        name: row_for(api_env.service.catalog, asset_id) for name, asset_id in names.items()
    } == rows_before
    jobs_rows = api_env.service.catalog.read_all(
        "SELECT state, COUNT(*) AS total FROM jobs WHERE task_type = ? GROUP BY state",
        (time_extract.TAKEN_TIME_TASK_TYPE,),
    )
    assert [(str(row["state"]), int(row["total"])) for row in jobs_rows] == [("SUCCEEDED", 2)]


def test_a_stale_input_revision_is_refused_with_zero_rows(api_env) -> None:
    write_exif_jpeg(api_env.library / "过期.jpg", original="2022:07:05 18:31:07")
    source = api_env.register_source()
    api_env.scan(source["source_id"])
    names = assets_by_basename(api_env.service.catalog)
    asset_id = names["过期.jpg"]
    with api_env.service.catalog.write() as connection:
        jobs.enqueue(
            connection,
            subject_type="asset",
            subject_id=str(asset_id),
            input_revision="f" * 64,
            task_type=time_extract.TAKEN_TIME_TASK_TYPE,
            processor_id=time_extract.TAKEN_TIME_PROCESSOR_ID,
            now_ms=1_000,
        )
    from p4_time_fixtures import build_scheduler

    scheduler = build_scheduler(api_env.service.catalog)
    steps = scheduler.run_until_idle(
        time_extract.taken_time_executor(api_env.service.catalog),
        worker_id="p4-time-test",
        now_ms=1_100,
    )
    assert steps == 1
    assert row_for(api_env.service.catalog, asset_id) is None
    job = api_env.service.catalog.read_one(
        "SELECT state, last_error_code FROM jobs WHERE task_type = ?",
        (time_extract.TAKEN_TIME_TASK_TYPE,),
    )
    assert str(job["state"]) == "FAILED"
    assert str(job["last_error_code"]) == STALE_INPUT_REVISION


def test_an_asset_without_positions_is_left_unextracted(api_env) -> None:
    with api_env.service.catalog.write() as connection:
        connection.execute(
            "INSERT INTO assets(sha256, byte_size, created_at, state) VALUES (?, 1, 1, 'ACTIVE')",
            ("a" * 64,),
        )
    asset_id = int(
        api_env.service.catalog.read_one("SELECT MAX(asset_id) AS value FROM assets")["value"]
    )
    result = run_extraction(api_env.service.catalog, asset_ids=[asset_id])
    assert result["steps"] == 1
    assert row_for(api_env.service.catalog, asset_id) is None
    assert filter_causes(api_env.service.catalog) == []


def test_a_mismatched_rules_version_makes_every_row_untrusted(api_env) -> None:
    write_exif_jpeg(api_env.library / "规则版本.jpg", original="2022:07:05 18:31:07", offset_original="+08:00")
    source = api_env.register_source()
    api_env.scan(source["source_id"])
    run_extraction(api_env.service.catalog)
    names = assets_by_basename(api_env.service.catalog)
    asset_id = names["规则版本.jpg"]
    assert row_for(api_env.service.catalog, asset_id) is not None
    assert time_read.state_block(api_env.service.catalog.connection)["state"] == "READY"

    with api_env.service.catalog.write() as connection:
        connection.execute(
            "UPDATE metadata SET value = '2' WHERE key = ?", (contract.RULES_VERSION_KEY,)
        )
    assert time_read.rules_version(api_env.service.catalog.connection) == 2
    block = time_read.state_block(api_env.service.catalog.connection)
    assert block == {
        "state": "PENDING",
        "assets_total": 1,
        "extracted_assets": 0,
        "capture_time_assets": 0,
    }
    # The row is still stored, but it is not published and not filtered on.
    assert row_for(api_env.service.catalog, asset_id) is not None
    assert time_read.asset_view(api_env.service.catalog.connection, asset_id) is None
    sql, parameters = time_read.capture_predicate(
        api_env.service.catalog.connection, contract.parse_capture_range("2022-01-01")
    )
    assert (sql, parameters) == ("0", [])

    with api_env.service.catalog.write() as connection:
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = ?",
            (str(contract.RULES_VERSION), contract.RULES_VERSION_KEY),
        )
    assert time_read.asset_view(api_env.service.catalog.connection, asset_id) is not None


def test_the_cli_reports_the_same_truth(api_env, capsys) -> None:
    from photo_search.taken_time import cli

    write_exif_jpeg(api_env.library / "命令行.jpg", original="2022:07:05 18:31:07", offset_original="+08:00")
    source = api_env.register_source()
    api_env.scan(source["source_id"])
    assert cli.main(["extract", "--data-root", str(api_env.data_root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["status"] == "ok"
    assert payload["enqueue"]["queued"] == 1
    assert payload["run"]["steps"] == 1
    assert payload["taken_time"]["block"]["state"] == "READY"
    assert payload["taken_time"]["jobs"] == {"SUCCEEDED": 1}
    assert cli.main(["status", "--data-root", str(api_env.data_root), "--json"]) == 0
    status = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert status["taken_time"]["capture_time_assets"] == 1


def test_an_undecodable_file_falls_back_instead_of_failing(api_env) -> None:
    api_env.add_text_file("坏图.jpg", "not an image at all")
    write_exif_jpeg(api_env.library / "正常.jpg", original="2022:07:05 18:31:07")
    source = api_env.register_source()
    api_env.scan(source["source_id"])
    assert len(assets_by_basename(api_env.service.catalog)) == 2
    result = run_extraction(api_env.service.catalog)
    assert result["steps"] == 2
    rows = api_env.service.catalog.read_all(
        "SELECT time_source FROM asset_metadata ORDER BY asset_id"
    )
    assert [str(row["time_source"]) for row in rows] == ["FILE_MTIME", "EXIF_ORIGINAL"]
    jobs_rows = api_env.service.catalog.read_all(
        "SELECT state, COUNT(*) AS total FROM jobs WHERE task_type = ? GROUP BY state",
        (time_extract.TAKEN_TIME_TASK_TYPE,),
    )
    assert [(str(row["state"]), int(row["total"])) for row in jobs_rows] == [("SUCCEEDED", 2)]
