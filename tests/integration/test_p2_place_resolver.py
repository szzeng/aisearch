"""P2-03B: the suggestion-only nearby-city resolution through the real scheduler.

Every asset is a synthetic GPS JPEG in ``%TEMP%``; the package is the tiny synthetic CN
city set. The suite covers the numeric branches, the candidate visibility rules, the
independence of the two revisions and the fact that no machine path ever writes a manual
assignment (ADR-0005 §4/§5/§11).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from p2_dataset_fixtures import (
    BETWEEN_POSITION,
    HANGZHOU_POSITION,
    SEA_POSITION,
    Harness,
    mini_spec,
    write_gps_jpeg,
)

from photo_search.catalog.errors import PLACE_DATASET_MISSING, CatalogError
from photo_search.geo import resolver as resolver_module
from photo_search.places import assignments as assignment_module
from photo_search.places import datasets as datasets_module
from photo_search.places import revision as revision_module
from photo_search.places import suggestions as suggestion_module
from photo_search.scheduler.contracts import JobResult


@pytest.fixture()
def harness(tmp_path: Path) -> Harness:
    instance = Harness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def prepare_asset(harness: Harness, name: str, position, *, datum: str | None = "WGS84") -> int:
    write_gps_jpeg(harness.library / name, position=position, datum=datum)
    harness.scan()
    asset_id = harness.asset_id(name)
    harness.run_gps([asset_id])
    return asset_id


def place_id_of(harness: Harness, external_id: str) -> int:
    row = harness.catalog.read_one(
        "SELECT place_id FROM place_external_refs WHERE external_id = ?", (external_id,)
    )
    return int(row["place_id"])


def assign(harness: Harness, asset_id: int, place_id: int, *, request_id: str, origin: str | None = None):
    with harness.catalog.write() as connection:
        return assignment_module.apply_request(
            connection,
            items=[
                {
                    "asset_id": int(asset_id),
                    "action": "ASSIGN",
                    "target_place_id": int(place_id),
                    "expected_revision": 0,
                    "origin": origin,
                }
            ],
            request_id=request_id,
            now_ms=int(harness.clock()),
        )


def unassign(harness: Harness, asset_id: int, *, request_id: str) -> dict:
    with harness.catalog.write() as connection:
        return assignment_module.apply_request(
            connection,
            items=[
                {
                    "asset_id": int(asset_id),
                    "action": "UNASSIGN",
                    "target_place_id": None,
                    "expected_revision": 1,
                }
            ],
            request_id=request_id,
            now_ms=int(harness.clock()),
        )


def resolution_rows(harness: Harness) -> list:
    return harness.catalog.read_all(
        "SELECT resolution_id, asset_id, status, dataset_id, suggestion_revision, "
        "control_revision_seen, geo_fact_id FROM place_resolutions ORDER BY resolution_id"
    )


def test_a_valid_gps_fact_produces_ranked_suggestion_only_candidates(harness: Harness) -> None:
    harness.install("mini-1")
    asset_id = prepare_asset(harness, "hangzhou.jpg", HANGZHOU_POSITION)
    filter_before = revision_module.read_filter_revision(harness.catalog.connection)

    assert harness.run_resolver([asset_id]) == 1

    rows = resolution_rows(harness)
    assert len(rows) == 1
    assert str(rows[0]["status"]) == "CANDIDATES"
    assert str(rows[0]["dataset_id"]) == "geo-mini-cn-1"
    assert int(rows[0]["control_revision_seen"]) == 0
    candidates = harness.catalog.read_all(
        "SELECT rank, candidate_id, display_name, local_place_id, disposition, match_basis "
        "FROM place_resolution_candidates ORDER BY rank"
    )
    assert [str(row["display_name"]) for row in candidates] == ["杭州", "苏州", "上海"]
    assert [int(row["rank"]) for row in candidates] == [1, 2, 3]
    assert all(str(row["disposition"]) == "SUGGESTION_ONLY" for row in candidates)
    assert all(str(row["match_basis"]) == "NEARBY_CITY_POINT" for row in candidates)
    assert all(row["local_place_id"] is not None for row in candidates)
    assert int(candidates[0]["local_place_id"]) == place_id_of(harness, "city-hangzhou")
    # Suggestion revision moves; the authoritative filter revision does not.
    connection = harness.catalog.connection
    assert revision_module.read_suggestion_revision(connection) == 1
    assert revision_module.read_filter_revision(connection) == filter_before
    # The machine product is published as a structured artifact.
    artifact = harness.catalog.read_one(
        "SELECT artifact_type, artifact_revision FROM artifacts WHERE processor_id = ?",
        (resolver_module.RESOLVER_PROCESSOR_ID,),
    )
    assert str(artifact["artifact_type"]) == "structured"
    assert str(artifact["artifact_revision"]) == "place-resolution.v1"
    # No machine path may write the manual control row.
    assert harness.catalog.read_one(
        "SELECT COUNT(*) AS value FROM asset_place_assignments"
    )["value"] == 0
    view = suggestion_module.suggestion_view(connection, asset_id)
    assert view["state"] == "candidates"
    assert view["requires_confirmation"] is True
    assert view["assumed_datum"] is False
    assert [item["display_name"] for item in view["candidates"]] == ["杭州", "苏州", "上海"]


def test_barren_geo_statuses_produce_a_successful_empty_resolution(harness: Harness) -> None:
    harness.install("mini-1")
    without_gps = prepare_asset(harness, "nogrps.jpg", None)
    sea = prepare_asset(harness, "sea.jpg", SEA_POSITION)
    far = prepare_asset(harness, "origin.jpg", (0.0, 0.0))

    assert harness.run_resolver([without_gps, sea, far]) == 3

    rows = {int(row["asset_id"]): row for row in resolution_rows(harness)}
    assert set(rows) == {without_gps, sea, far}
    assert all(str(row["status"]) == "NO_CANDIDATE" for row in rows.values())
    assert harness.catalog.read_one(
        "SELECT COUNT(*) AS value FROM place_resolution_candidates"
    )["value"] == 0
    # An empty visible set never moves the suggestion revision.
    assert revision_module.read_suggestion_revision(harness.catalog.connection) == 0
    for asset_id in rows:
        view = suggestion_module.suggestion_view(harness.catalog.connection, asset_id)
        assert view["state"] == "no_candidate"
        assert view["candidates"] == []


def test_an_assumed_wgs84_fact_only_ever_yields_a_pending_suggestion(harness: Harness) -> None:
    harness.install("mini-1")
    asset_id = prepare_asset(harness, "assumed.jpg", HANGZHOU_POSITION, datum=None)
    fact = harness.catalog.read_one(
        "SELECT status, datum_status FROM asset_geo_facts WHERE asset_id = ?", (asset_id,)
    )
    assert str(fact["status"]) == "VALID" and str(fact["datum_status"]) == "ASSUMED_WGS84"

    harness.run_resolver([asset_id])

    view = suggestion_module.suggestion_view(harness.catalog.connection, asset_id)
    assert view["state"] == "candidates"
    assert view["assumed_datum"] is True
    assert view["requires_confirmation"] is True
    assert harness.catalog.read_one(
        "SELECT COUNT(*) AS value FROM asset_place_assignments"
    )["value"] == 0


def test_invalid_and_unsupported_crs_facts_are_empty_results(harness: Harness) -> None:
    harness.install("mini-1")
    out_of_range = prepare_asset(harness, "invalid.jpg", (200.0, 120.0))
    unsupported = prepare_asset(harness, "gcj02.jpg", HANGZHOU_POSITION, datum="GCJ02")
    facts = {
        asset_id: str(
            harness.catalog.read_one(
                "SELECT status FROM asset_geo_facts WHERE asset_id = ?", (asset_id,)
            )["status"]
        )
        for asset_id in (out_of_range, unsupported)
    }
    assert facts == {out_of_range: "INVALID", unsupported: "UNSUPPORTED_CRS"}

    assert harness.run_resolver([out_of_range, unsupported]) == 2

    statuses = {
        int(row["asset_id"]): str(row["status"]) for row in resolution_rows(harness)
    }
    assert statuses == {out_of_range: "NO_CANDIDATE", unsupported: "NO_CANDIDATE"}
    assert harness.catalog.read_one(
        "SELECT COUNT(*) AS value FROM place_resolution_candidates"
    )["value"] == 0
    assert revision_module.read_suggestion_revision(harness.catalog.connection) == 0


def test_multiple_candidates_are_ranked_by_distance_and_ties_are_stable(harness: Harness) -> None:
    harness.install("mini-1")
    asset_id = prepare_asset(harness, "between.jpg", BETWEEN_POSITION)

    harness.run_resolver([asset_id])

    rows = harness.catalog.read_all(
        "SELECT dataset_place_id, distance_m FROM place_resolution_candidates ORDER BY rank"
    )
    assert [str(row["dataset_place_id"]) for row in rows] == [
        "city-suzhou",
        "city-hangzhou",
        "city-shanghai",
    ]
    distances = [float(row["distance_m"]) for row in rows]
    assert distances == sorted(distances)


def test_a_replayed_resolution_is_idempotent(harness: Harness) -> None:
    harness.install("mini-1")
    asset_id = prepare_asset(harness, "hangzhou.jpg", HANGZHOU_POSITION)
    harness.run_resolver([asset_id])
    first = resolution_rows(harness)
    revision_after_first = revision_module.read_suggestion_revision(harness.catalog.connection)

    harness.run_resolver([asset_id], now_ms=1_400)

    assert resolution_rows(harness) == first
    assert harness.catalog.read_one(
        "SELECT COUNT(*) AS value FROM place_resolution_candidates"
    )["value"] == 3
    assert (
        revision_module.read_suggestion_revision(harness.catalog.connection)
        == revision_after_first
    )


def test_a_manual_assignment_wins_and_still_moves_both_revisions(harness: Harness) -> None:
    harness.install("mini-1")
    asset_id = prepare_asset(harness, "hangzhou.jpg", HANGZHOU_POSITION)
    harness.run_resolver([asset_id])
    filter_before = revision_module.read_filter_revision(harness.catalog.connection)
    suzhou = place_id_of(harness, "city-suzhou")

    result = assign(harness, asset_id, suzhou, request_id="manual-1")

    assert result["changed"] is True
    row = harness.catalog.read_one(
        "SELECT mode, origin, place_id, manual_lock FROM asset_place_assignments "
        "WHERE asset_id = ?",
        (asset_id,),
    )
    assert str(row["mode"]) == "MANUAL_ASSIGNED" and str(row["origin"]) == "MANUAL"
    assert int(row["place_id"]) == suzhou and int(row["manual_lock"]) == 1
    assert revision_module.read_filter_revision(harness.catalog.connection) == filter_before + 1
    # A manual lock hides the machine suggestions for this asset.
    assert revision_module.read_suggestion_revision(harness.catalog.connection) == 2
    view = suggestion_module.suggestion_view(harness.catalog.connection, asset_id)
    assert view["state"] == "no_candidate"
    assert view["candidates"] == []
    assert view["excluded_candidate_count"] == 3


def test_a_late_resolution_never_overrides_a_manual_lock(harness: Harness) -> None:
    harness.install("mini-1")
    asset_id = prepare_asset(harness, "hangzhou.jpg", HANGZHOU_POSITION)
    scheduler = harness.scheduler()
    harness.enqueue(
        asset_id, resolver_module.RESOLVER_PROCESSOR_ID, resolver_module.RESOLVER_TASK_TYPE
    )
    request = scheduler.claim_next(worker_id="w1", now_ms=1_100)
    assert request is not None
    # The result is computed *before* the user's edit...
    raw = resolver_module.resolver_executor(
        harness.catalog, data_root=harness.data_root, clock=harness.clock
    )(request.to_envelope())
    assert raw["state"] == "SUCCEEDED" and len(raw["artifacts"]) == 1
    hangzhou = place_id_of(harness, "city-hangzhou")
    assign(harness, asset_id, hangzhou, request_id="manual-late")
    revision_before = revision_module.read_suggestion_revision(harness.catalog.connection)

    # ...and only then commits, so it is stored as a machine product without touching the lock.
    scheduler.submit(JobResult.from_envelope(raw), now_ms=1_200)

    assert len(resolution_rows(harness)) == 1
    row = harness.catalog.read_one(
        "SELECT mode, place_id, revision FROM asset_place_assignments WHERE asset_id = ?",
        (asset_id,),
    )
    assert str(row["mode"]) == "MANUAL_ASSIGNED" and int(row["place_id"]) == hangzhou
    assert int(row["revision"]) == 1
    # Nothing was ever visible for this asset (no resolution before the lock, and the lock
    # hides everything after), so the visible set did not change and the revision stays.
    assert (
        revision_module.read_suggestion_revision(harness.catalog.connection)
        == revision_before
    )
    assert harness.catalog.read_one(
        "SELECT control_revision_seen FROM place_resolutions"
    )["control_revision_seen"] == 1


def test_unassign_keeps_the_lock_and_only_leaves_the_other_candidates(harness: Harness) -> None:
    harness.install("mini-1")
    asset_id = prepare_asset(harness, "hangzhou.jpg", HANGZHOU_POSITION)
    harness.run_resolver([asset_id])
    hangzhou = place_id_of(harness, "city-hangzhou")
    assign(harness, asset_id, hangzhou, request_id="manual-1")

    unassign(harness, asset_id, request_id="manual-2")

    row = harness.catalog.read_one(
        "SELECT mode, place_id, manual_lock FROM asset_place_assignments WHERE asset_id = ?",
        (asset_id,),
    )
    assert str(row["mode"]) == "MANUAL_UNASSIGNED" and row["place_id"] is None
    assert int(row["manual_lock"]) == 1
    excluded = harness.catalog.read_all(
        "SELECT place_id, active FROM asset_place_exclusions WHERE asset_id = ?", (asset_id,)
    )
    assert {int(item["place_id"]) for item in excluded} == {hangzhou}
    assert all(int(item["active"]) == 1 for item in excluded)
    view = suggestion_module.suggestion_view(harness.catalog.connection, asset_id)
    # Hangzhou is locked out, but the remaining nearby cities are still only suggestions.
    assert [item["local_place_id"] for item in view["candidates"]] == [
        place_id_of(harness, "city-suzhou"),
        place_id_of(harness, "city-shanghai"),
    ]
    assert harness.catalog.read_one(
        "SELECT COUNT(*) AS value FROM asset_place_assignments WHERE mode = 'MANUAL_ASSIGNED'"
    )["value"] == 0


def test_a_confirmed_suggestion_stays_after_a_rescan_and_a_dataset_switch(harness: Harness) -> None:
    harness.install("mini-1")
    asset_id = prepare_asset(harness, "hangzhou.jpg", HANGZHOU_POSITION)
    harness.run_resolver([asset_id])
    hangzhou = place_id_of(harness, "city-hangzhou")

    assign(harness, asset_id, hangzhou, request_id="confirm-1", origin="CONFIRMED_SUGGESTION")

    row = harness.catalog.read_one(
        "SELECT mode, origin, place_id FROM asset_place_assignments WHERE asset_id = ?",
        (asset_id,),
    )
    assert str(row["mode"]) == "MANUAL_ASSIGNED"
    assert str(row["origin"]) == "CONFIRMED_SUGGESTION"
    assert int(row["place_id"]) == hangzhou

    harness.scan()
    harness.install(
        "mini-2",
        spec=mini_spec(
            dataset_id="geo-mini-cn-2",
            package_version="2",
            previous_dataset_id="geo-mini-cn-1",
        ),
    )
    datasets_module.rollback_dataset(harness.catalog)
    again = harness.catalog.read_one(
        "SELECT mode, origin, place_id FROM asset_place_assignments WHERE asset_id = ?",
        (asset_id,),
    )
    assert str(again["mode"]) == "MANUAL_ASSIGNED"
    assert str(again["origin"]) == "CONFIRMED_SUGGESTION"
    assert int(again["place_id"]) == hangzhou


def test_resolution_blocks_without_a_dataset_and_the_manual_paths_stay_usable(
    harness: Harness,
) -> None:
    asset_id = prepare_asset(harness, "hangzhou.jpg", HANGZHOU_POSITION)
    revision_before = revision_module.read_suggestion_revision(harness.catalog.connection)

    assert harness.run_resolver([asset_id]) == 1

    job = harness.catalog.read_one(
        "SELECT state, blocking_reason FROM jobs WHERE processor_id = ?",
        (resolver_module.RESOLVER_PROCESSOR_ID,),
    )
    assert str(job["state"]) == "BLOCKED"
    assert str(job["blocking_reason"]) == PLACE_DATASET_MISSING
    assert resolution_rows(harness) == []
    assert (
        revision_module.read_suggestion_revision(harness.catalog.connection)
        == revision_before
    )
    assert harness.catalog.read_one(
        "SELECT COUNT(*) AS value FROM asset_place_assignments"
    )["value"] == 0


def test_manual_places_and_assignment_work_with_a_missing_package(harness: Harness) -> None:
    from p2_legacy_place_fixtures import seed_legacy_assignment

    from photo_search.ingestion.service import CatalogService
    from photo_search.places import directory
    from photo_search.places.service import PlaceService

    asset_id = prepare_asset(harness, "hangzhou.jpg", HANGZHOU_POSITION)
    with harness.catalog.write() as connection:
        place_id = directory.create_place(
            connection,
            kind="custom",
            canonical_name="外婆家",
            parent_place_id=None,
            origin="USER",
            aliases=(),
            now_ms=int(harness.clock()),
        )

    before_revision = revision_module.read_filter_revision(harness.catalog.connection)
    with pytest.raises(CatalogError) as error:
        assign(harness, asset_id, place_id, request_id="manual-no-dataset")
    assert error.value.code == "PLACE_NOT_FOUND"
    assert harness.catalog.read_one("SELECT COUNT(*) FROM place_operations")[0] == 0
    assert harness.catalog.read_one("SELECT COUNT(*) FROM asset_place_assignments")[0] == 0
    assert revision_module.read_filter_revision(harness.catalog.connection) == before_revision

    # A pre-source-gate USER assignment is a different input from a new ASSIGN request.
    harness.service.close()
    seed_legacy_assignment(harness.data_root, asset_id=asset_id, place_id=place_id)
    harness.service = CatalogService.open(data_root=harness.data_root, clock=harness.clock)
    assert harness.catalog.read_one(
        "SELECT mode FROM asset_place_assignments WHERE asset_id = ?", (asset_id,)
    )["mode"] == "MANUAL_ASSIGNED"
    assert harness.catalog.read_one(
        "SELECT COUNT(*) AS value FROM place_resolutions"
    )["value"] == 0
    service = PlaceService(harness.catalog, clock=harness.clock)
    replay_revision = revision_module.read_filter_revision(harness.catalog.connection)
    replayed = service.write_assignment(
        asset_id=asset_id, action="ASSIGN", target_place_id=place_id, expected_revision=0,
        request_id=f"legacy-place-operation-{asset_id}",
    )
    assert replayed["operation_id"] == f"legacy-place-operation-{asset_id}"
    assert harness.catalog.read_one("SELECT COUNT(*) FROM place_operations")[0] == 1
    assert harness.catalog.read_one("SELECT COUNT(*) FROM asset_place_assignment_history")[0] == 1
    assert revision_module.read_filter_revision(harness.catalog.connection) == replay_revision
    cancelled = service.write_assignment(
        asset_id=asset_id, action="UNASSIGN", target_place_id=None, expected_revision=1,
        request_id="legacy-cancel",
    )
    assert cancelled["changed"] is True
    restored = service.undo(
        operation_id=cancelled["operation_id"], request_id="legacy-cancel-undo",
        expected_operation_revision=cancelled["operation_revision"],
    )
    assert restored["changed"] is True
    row = harness.catalog.read_one(
        "SELECT place_id, mode, manual_lock FROM asset_place_assignments WHERE asset_id=?",
        (asset_id,),
    )
    assert tuple(row) == (place_id, "MANUAL_ASSIGNED", 1)


def test_an_interrupted_resolution_transaction_rolls_back_completely(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.install("mini-1")
    asset_id = prepare_asset(harness, "hangzhou.jpg", HANGZHOU_POSITION)
    scheduler = harness.scheduler()
    harness.enqueue(
        asset_id, resolver_module.RESOLVER_PROCESSOR_ID, resolver_module.RESOLVER_TASK_TYPE
    )

    monkeypatch.setattr(
        suggestion_module, "visible_signature", lambda *_a, **_k: (_ for _ in ()).throw(
            CatalogError("ARTIFACT_INVALID", "injected failure")
        )
    )
    with pytest.raises(CatalogError):
        scheduler.run_until_idle(
            resolver_module.resolver_executor(
                harness.catalog, data_root=harness.data_root, clock=harness.clock
            ),
            worker_id="w1",
            now_ms=1_200,
        )

    monkeypatch.undo()
    assert resolution_rows(harness) == []
    assert harness.catalog.read_one(
        "SELECT COUNT(*) AS value FROM place_resolution_candidates"
    )["value"] == 0
    assert revision_module.read_suggestion_revision(harness.catalog.connection) == 0
    # The job keeps its lease: nothing was committed, so a re-claim may retry it.
    job = harness.catalog.read_one(
        "SELECT state FROM jobs WHERE processor_id = ?", (resolver_module.RESOLVER_PROCESSOR_ID,)
    )
    assert str(job["state"]) == "RUNNING"


def test_a_cpu_only_machine_resolves_and_assigns(harness: Harness) -> None:
    harness.install("mini-1")
    asset_id = prepare_asset(harness, "hangzhou.jpg", HANGZHOU_POSITION)
    capability = harness.catalog.read_one(
        "SELECT normalized_json FROM runtime_capabilities ORDER BY measured_at DESC LIMIT 1"
    )
    assert "gpu" not in str(capability["normalized_json"])

    harness.run_resolver([asset_id])
    result = assign(harness, asset_id, place_id_of(harness, "city-hangzhou"), request_id="cpu-1")

    assert result["changed"] is True
    assert len(resolution_rows(harness)) == 1


def test_a_resolution_can_be_published_with_no_artifact_at_all(harness: Harness) -> None:
    """A qualified empty-artifact result must still be a legal, commit-able JobResult."""
    harness.install("mini-1")
    asset_id = prepare_asset(harness, "hangzhou.jpg", HANGZHOU_POSITION)
    scheduler = harness.scheduler()
    harness.enqueue(
        asset_id, resolver_module.RESOLVER_PROCESSOR_ID, resolver_module.RESOLVER_TASK_TYPE
    )
    request = scheduler.claim_next(worker_id="w1", now_ms=1_100)
    assert request is not None
    raw = resolver_module.resolver_executor(
        harness.catalog, data_root=harness.data_root, clock=harness.clock
    )(request.to_envelope())
    raw["artifacts"] = []

    job = scheduler.submit(JobResult.from_envelope(raw), now_ms=1_200)

    assert str(job.state) == "SUCCEEDED"
    assert harness.catalog.read_one(
        "SELECT COUNT(*) AS value FROM artifacts WHERE processor_id = ?",
        (resolver_module.RESOLVER_PROCESSOR_ID,),
    )["value"] == 0
    # No artifact means no machine product: the caller can re-run the same input later.
    assert resolution_rows(harness) == []
