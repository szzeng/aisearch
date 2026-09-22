"""P4-00B: the eight D-level fault injections of ADR-0011 §10, one test each.

The card requires more than a reference to a unit test: every item below records the fixture,
the before/after state and the zero-write / rollback evidence it produced. ``D1``/``D2`` work
on a real populated v14 catalog, ``D3``-``D8`` on a scanned synthetic library.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from p4_time_fixtures import (
    assets_by_basename,
    build_v14_catalog,
    enqueue_all,
    filter_causes,
    filter_revision,
    migrations_upto,
    row_for,
    run_extraction,
    snapshot_tables,
    v14_data_root,
    write_exif_jpeg,
    write_plain_jpeg,
)

from photo_search.catalog import schema
from photo_search.catalog.migrate import inspect_database, migrate_database
from photo_search.catalog.sqlite_io import connect_rw, open_readonly
from photo_search.taken_time import contract
from photo_search.taken_time import extract as time_extract
from photo_search.taken_time import read as time_read
from photo_search.taken_time.service import run_queue

PRESERVED_TABLES = (
    "assets",
    "file_locations",
    "catalog_filter_revision",
    "catalog_filter_revision_events",
    "person_operations",
    "person_operation_items",
)


# --------------------------------------------------------------------------------- D1
def test_d1_a_populated_v14_catalog_upgrades_to_v15_without_touching_any_old_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v14_catalog(tmp_path, monkeypatch)
    before = snapshot_tables(path, PRESERVED_TABLES)
    assert inspect_database(path) == 14
    revision_before = before["catalog_filter_revision"][0][1]

    result = migrate_database(path, data_root=v14_data_root(tmp_path))

    assert (result.previous_version, result.schema_version, result.applied) == (14, 15, (15,))
    assert inspect_database(path) == 15
    assert snapshot_tables(path, PRESERVED_TABLES) == before
    assert before["catalog_filter_revision"][0][1] == revision_before
    live = open_readonly(path)
    try:
        schema.verify_schema(live, 15)
        assert list(live.execute("PRAGMA foreign_key_check")) == []
        assert live.execute("SELECT COUNT(*) FROM asset_metadata").fetchone()[0] == 0
        assert int(
            live.execute(
                "SELECT value FROM metadata WHERE key = ?",
                (contract.RULES_VERSION_KEY,),
            ).fetchone()[0]
        ) == contract.RULES_VERSION
    finally:
        live.close()
    backup = open_readonly(result.backup_path)
    try:
        assert schema.read_schema_version(backup) == 14
    finally:
        backup.close()


# --------------------------------------------------------------------------------- D2
def test_d2_a_migration_that_never_commits_leaves_a_complete_v14_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A killed process and a failing script both leave the v14 database intact."""
    path = build_v14_catalog(tmp_path, monkeypatch)
    before = snapshot_tables(path, PRESERVED_TABLES)
    script = (migrations_upto(tmp_path, 15) / "0015_taken_time.sql").read_text(encoding="utf-8")

    # (a) The script runs inside an open transaction that is never committed: closing the
    # connection is what a killed process leaves behind, and SQLite rolls it back.
    connection = connect_rw(path)
    connection.executescript(f"BEGIN IMMEDIATE;\n{script}\n")
    assert connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name = 'asset_metadata'"
    ).fetchone()[0] == 1
    connection.close()
    assert inspect_database(path) == 14
    assert snapshot_tables(path, PRESERVED_TABLES) == before
    check = open_readonly(path)
    try:
        assert "asset_metadata" not in set(schema.business_tables(check))
    finally:
        check.close()

    # (b) A failing script is rolled back by the controlled migration path, with no half table
    # and no leftover parking table.
    directory = migrations_upto(tmp_path, 15)
    broken = directory / "0015_taken_time.sql"
    broken.write_text(script + "\nSELECT * FROM missing_table;\n", encoding="utf-8")
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", directory)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 15)
    from photo_search.catalog.errors import MIGRATION_FAILED, CatalogError

    with pytest.raises(CatalogError) as error:
        migrate_database(path, data_root=v14_data_root(tmp_path))
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, 14))
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 14)
    assert error.value.code == MIGRATION_FAILED
    assert inspect_database(path) == 14
    assert snapshot_tables(path, PRESERVED_TABLES) == before


# --------------------------------------------------------------------------------- D3
def test_d3_an_interrupted_extraction_resumes_without_rewriting_or_skipping(api_env) -> None:
    for index in range(4):
        write_exif_jpeg(
            api_env.library / f"中断 {index}.jpg",
            original=f"2022:0{index + 1}:05 18:31:07",
            offset_original="+08:00",
        )
    source = api_env.register_source()
    api_env.scan(source["source_id"])
    # First run: only two of the four jobs are drained, as a killed process would leave it.
    enqueue_all(api_env.service.catalog)
    first = run_queue(api_env.service.catalog, limit=2, worker_id="d3-first")
    assert first["steps"] == 2
    assert first["jobs"] == {"QUEUED": 2, "SUCCEEDED": 2}
    revision_after_first = filter_revision(api_env.service.catalog)

    # Restart: the remaining queue is drained, the finished rows are not touched.
    second = run_extraction(api_env.service.catalog)
    assert second["steps"] == 2
    rows = api_env.service.catalog.read_all(
        "SELECT asset_id, time_source FROM asset_metadata ORDER BY asset_id"
    )
    assert len(rows) == 4
    assert [str(row["time_source"]) for row in rows] == ["EXIF_ORIGINAL"] * 4
    assert filter_revision(api_env.service.catalog) - revision_after_first == 2
    events = api_env.service.catalog.read_all(
        "SELECT cause, changed_count FROM catalog_filter_revision_events ORDER BY revision"
    )
    assert [str(row["cause"]) for row in events] == ["CAPTURE_TIME_CHANGED"] * 4
    # One transaction at most +1, and one changed asset per event.
    assert [int(row["changed_count"]) for row in events] == [1, 1, 1, 1]


# --------------------------------------------------------------------------------- D4
def test_d4_a_file_that_changes_mid_read_is_not_adopted_and_is_re_extracted(
    api_env, monkeypatch
) -> None:
    from photo_search.taken_time import exif as exif_module

    write_exif_jpeg(
        api_env.library / "处理中变化.jpg",
        original="2022:07:05 18:31:07",
        offset_original="+08:00",
    )
    source = api_env.register_source()
    api_env.scan(source["source_id"])
    names = assets_by_basename(api_env.service.catalog)
    asset_id = names["处理中变化.jpg"]
    catalog = api_env.service.catalog
    revision_before = filter_revision(catalog)

    original_read = exif_module.read_time_fields

    def touch_while_reading(path: Path):
        import os

        fields = original_read(path)
        info = os.stat(path)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
        return fields

    monkeypatch.setattr(exif_module, "read_time_fields", touch_while_reading)
    run_extraction(catalog)
    monkeypatch.setattr(exif_module, "read_time_fields", original_read)
    # The execution wrote the honest file-time row and did not adopt the changed position.
    row = row_for(catalog, asset_id)
    assert row is not None and row.time_source == "FILE_MTIME"
    assert filter_revision(catalog) - revision_before == 1
    artifacts = catalog.read_all(
        "SELECT input_revision FROM artifacts WHERE processor_id = ?",
        (time_extract.TAKEN_TIME_PROCESSOR_ID,),
    )
    assert len(artifacts) == 1
    stale_input = str(artifacts[0]["input_revision"])

    # The rescan records the new observation: the input revision moves, so the asset is a new
    # logical job and the re-extraction publishes the EXIF fact - a second artifact row.
    api_env.scan(source["source_id"])
    run_extraction(catalog, force=True)
    again = row_for(catalog, asset_id)
    assert again is not None and again.time_source == "EXIF_ORIGINAL"
    assert filter_revision(catalog) - revision_before == 2
    revisions = [
        str(row["input_revision"])
        for row in catalog.read_all(
            "SELECT input_revision FROM artifacts WHERE processor_id = ? ORDER BY created_at",
            (time_extract.TAKEN_TIME_PROCESSOR_ID,),
        )
    ]
    assert len(revisions) == 2 and revisions[0] == stale_input and revisions[1] != stale_input


# --------------------------------------------------------------------------------- D5
def test_d5_an_offline_source_falls_back_to_the_recorded_file_time(api_env) -> None:
    library = api_env.library / "离线"
    write_exif_jpeg(
        library / "离线 照片.jpg",
        original="2022:07:05 18:31:07",
        offset_original="+08:00",
        age_ms=5_400_000,
    )
    source = api_env.register_source("offline root", path=library)
    api_env.scan(source["source_id"])
    names = assets_by_basename(api_env.service.catalog)
    asset_id = names["离线 照片.jpg"]
    recorded = api_env.service.catalog.read_one(
        "SELECT mtime_ns FROM file_locations WHERE asset_id = ?", (asset_id,)
    )
    recorded_ns = int(recorded["mtime_ns"])

    import shutil

    # The whole source becomes unreachable: the scanner refuses to rescan it, so the
    # registered position (and its recorded mtime) stays exactly as the last scan observed it.
    shutil.rmtree(library)
    availability = str(
        api_env.service.catalog.read_one(
            "SELECT availability FROM file_locations WHERE asset_id = ?", (asset_id,)
        )["availability"]
    )
    assert availability == "PRESENT"  # the catalog was not re-scanned: the root just vanished

    result = run_extraction(api_env.service.catalog)
    assert result["steps"] == 1
    row = row_for(api_env.service.catalog, asset_id)
    assert row is not None and row.time_source == "FILE_MTIME" and row.taken_at_sort is None
    # No EXIF was adopted from an unreadable position; the file time comes from the recorded
    # observation, so unplugging the disk cannot change the fact.
    from datetime import datetime, timezone

    assert row.taken_at_raw == datetime.fromtimestamp(
        recorded_ns / 1_000_000_000, tz=timezone.utc
    ).isoformat(timespec="microseconds")
    jobs_rows = api_env.service.catalog.read_all(
        "SELECT state FROM jobs WHERE task_type = ?", (time_extract.TAKEN_TIME_TASK_TYPE,)
    )
    assert [str(item["state"]) for item in jobs_rows] == ["SUCCEEDED"]

    # A file that disappears while its root stays reachable is the "position MISSING" half of
    # the same rule (the scanner records MISSING; the extraction answer is identical).
    second_root = api_env.tmp_path / "d5-second"
    write_plain_jpeg(second_root / "缺失 照片.jpg", age_ms=3_600_000)
    second = api_env.register_source("d5 second", path=second_root)
    api_env.scan(second["source_id"])
    second_asset = assets_by_basename(api_env.service.catalog)["缺失 照片.jpg"]
    (second_root / "缺失 照片.jpg").unlink()
    api_env.scan(second["source_id"])
    missing_state = str(
        api_env.service.catalog.read_one(
            "SELECT availability FROM file_locations WHERE asset_id = ?", (second_asset,)
        )["availability"]
    )
    assert missing_state == "MISSING"
    run_extraction(api_env.service.catalog, force=True)
    missing_row = row_for(api_env.service.catalog, second_asset)
    assert missing_row is not None and missing_row.time_source == "FILE_MTIME"


# --------------------------------------------------------------------------------- D6
def test_d6_a_multi_position_asset_is_deterministic(api_env) -> None:
    root_one = api_env.tmp_path / "d6-one"
    root_two = api_env.tmp_path / "d6-two"
    write_exif_jpeg(root_one / "同一张.jpg", original="2022:07:05 18:31:07", offset_original="+08:00")
    twin = root_two / "同一张 副本.jpg"
    twin.parent.mkdir(parents=True, exist_ok=True)
    twin.write_bytes((root_one / "同一张.jpg").read_bytes())
    from p4_time_fixtures import stamp

    stamp(twin)
    source_one = api_env.register_source("d6 one", path=root_one)
    source_two = api_env.register_source("d6 two", path=root_two)
    api_env.scan(source_one["source_id"])
    api_env.scan(source_two["source_id"])
    locations = api_env.service.catalog.read_all(
        "SELECT location_id, availability FROM file_locations ORDER BY location_id"
    )
    assert [str(row["availability"]) for row in locations] == ["PRESENT", "PRESENT"]
    asset_id = int(
        api_env.service.catalog.read_one("SELECT MIN(asset_id) AS value FROM assets")["value"]
    )
    # Deleting the lowest position leaves the catalog observation intact; the walk must skip
    # it and take the next PRESENT position, and the fact must be the same either way.
    (root_one / "同一张.jpg").unlink()
    run_extraction(api_env.service.catalog)
    first = row_for(api_env.service.catalog, asset_id)
    assert first is not None and first.time_source == "EXIF_ORIGINAL"
    payload = api_env.service.catalog.read_one(
        "SELECT input_revision FROM artifacts WHERE processor_id = ?",
        (time_extract.TAKEN_TIME_PROCESSOR_ID,),
    )
    assert payload is not None
    # A second run (same recorded observation) reproduces the identical fact - no rewrite, no
    # revision move: determinism, not luck.
    revision_before = filter_revision(api_env.service.catalog)
    run_extraction(api_env.service.catalog, force=True)
    again = row_for(api_env.service.catalog, asset_id)
    assert again == first
    assert filter_revision(api_env.service.catalog) == revision_before


# --------------------------------------------------------------------------------- D7
def test_d7_a_mismatched_rules_version_reports_pending_and_filters_nothing(api_env) -> None:
    write_exif_jpeg(api_env.library / "门禁.jpg", original="2022:07:05 18:31:07", offset_original="+08:00")
    write_plain_jpeg(api_env.library / "门禁 二.jpg")
    source = api_env.register_source()
    api_env.scan(source["source_id"])
    run_extraction(api_env.service.catalog)
    catalog = api_env.service.catalog
    assert time_read.state_block(catalog.connection)["state"] == "READY"
    assert len(catalog.read_all("SELECT 1 FROM asset_metadata")) == 2

    with catalog.write() as connection:
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = ?",
            (str(contract.RULES_VERSION + 1), contract.RULES_VERSION_KEY),
        )
    # The rows stay in the catalog, the status is PENDING with normalized counts, and the
    # capture *predicate* is unsatisfiable.
    assert len(catalog.read_all("SELECT 1 FROM asset_metadata")) == 2
    block = time_read.state_block(catalog.connection)
    assert block == {
        "state": "PENDING",
        "assets_total": 2,
        "extracted_assets": 0,
        "capture_time_assets": 0,
    }
    assert time_read.asset_view(catalog.connection, 1) is None
    sql, parameters = time_read.capture_predicate(
        catalog.connection, contract.parse_capture_range("0001-01-01")
    )
    assert (sql, parameters) == ("0", [])

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        await api_env.authenticate(client)
        ranged = await client.get(
            "/api/v1/assets", params={"limit": 10, "capture_from": "0001-01-01"}
        )
        ordered = await client.get(
            "/api/v1/assets", params={"limit": 10, "order": "capture_desc"}
        )
        detail = await client.get("/api/v1/assets/1")
        return {"ranged": ranged, "ordered": ordered, "detail": detail}

    result = api_env.scenario(scenario)
    assert result["ranged"].status_code == 200
    assert result["ranged"].json()["assets"] == []
    assert result["ranged"].json()["taken_time"]["state"] == "PENDING"
    # ADR-0011 §2 excludes *status and filtering*; the browse order is not a filter, so it
    # still returns every asset exactly once (deterministically) instead of hiding rows.
    ordered_body = result["ordered"].json()
    assert len(ordered_body["assets"]) == 2
    assert "taken_time" not in ordered_body  # no bound was given
    assert result["detail"].json()["taken_time"] is None


# --------------------------------------------------------------------------------- D8
def test_d8_a_capture_write_moves_the_revision_and_the_other_orders_are_unchanged(api_env) -> None:
    for index in range(4):
        write_exif_jpeg(
            api_env.library / f"续页 {index}.jpg",
            original=f"2022:0{index + 1}:05 18:31:07",
            offset_original="+08:00",
        )
    source = api_env.register_source()
    api_env.scan(source["source_id"])
    catalog = api_env.service.catalog

    async def first_pages(client: AsyncClient) -> dict[str, Any]:
        await api_env.authenticate(client)
        capture = await client.get(
            "/api/v1/assets", params={"order": "capture_desc", "limit": 2}
        )
        time_desc = await client.get(
            "/api/v1/assets", params={"order": "time_desc", "limit": 2}
        )
        return {"capture": capture.json(), "time_desc": time_desc.json()}

    pages = api_env.scenario(first_pages)
    assert pages["capture"]["next_cursor"] and pages["time_desc"]["next_cursor"]

    run_extraction(catalog)

    async def reuse(client: AsyncClient) -> dict[str, Any]:
        await api_env.authenticate(client)
        stale_capture = await client.get(
            "/api/v1/assets",
            params={
                "order": "capture_desc",
                "limit": 2,
                "cursor": pages["capture"]["next_cursor"],
            },
        )
        time_desc_next = await client.get(
            "/api/v1/assets",
            params={
                "order": "time_desc",
                "limit": 2,
                "cursor": pages["time_desc"]["next_cursor"],
            },
        )
        by_id_next = await client.get("/api/v1/assets", params={"limit": 2, "cursor": None})
        return {
            "stale_capture": stale_capture,
            "time_desc_next": time_desc_next,
            "by_id_next": by_id_next,
        }

    result = api_env.scenario(reuse)
    assert result["stale_capture"].status_code == 409
    assert result["stale_capture"].json()["details"]["reason"] == "CATALOG_FILTER_REVISION_CHANGED"
    # ``time_desc`` (insertion time) and ``asset_id`` keep the exact P1-05 behaviour: the
    # capture write moved none of their facts.
    assert result["time_desc_next"].status_code == 200
    expected_ids = [
        int(row["asset_id"])
        for row in catalog.read_all(
            "SELECT asset_id FROM assets ORDER BY created_at DESC, asset_id DESC"
        )
    ]
    assert [item["asset_id"] for item in pages["time_desc"]["assets"]] == expected_ids[:2]
    assert [item["asset_id"] for item in result["time_desc_next"].json()["assets"]] == (
        expected_ids[2:4]
    )
    assert result["by_id_next"].status_code == 200
    assert len(result["by_id_next"].json()["assets"]) == 2
    # Reopening the capture order (dropping the cursor) still yields every asset once.
    reopened: list[int] = []
    cursor = None
    for _ in range(10):
        params: dict[str, Any] = {"order": "capture_desc", "limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = api_env.scenario(
            lambda client, params=params: _get(client, api_env, params)
        )
        reopened.extend(int(item["asset_id"]) for item in page["assets"])
        cursor = page["next_cursor"]
        if not page["has_more"]:
            break
    assert sorted(reopened) == sorted(assets_by_basename(catalog).values())
    assert filter_causes(catalog) == ["CAPTURE_TIME_CHANGED"] * 4


async def _get(client: AsyncClient, api_env, params: dict[str, Any]) -> dict[str, Any]:
    await api_env.authenticate(client)
    response = await client.get("/api/v1/assets", params=params)
    assert response.status_code == 200, response.text
    return response.json()
