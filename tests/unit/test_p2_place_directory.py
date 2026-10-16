"""P2-03A: place directory, manual control, idempotency, batch/undo and the revisions.

Every database and tiny synthetic source package lives in pytest's ``%TEMP%`` directory.
No real library or dataset is touched; the GPS protocol is covered by the geo suites.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from p2_search_fixtures import install_source_places

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import (
    CATALOG_FILTER_REVISION_STALE,
    INVALID_PLACE_FILTER,
    MAX_PLACE_BATCH_SIZE,
    PLACE_ASSIGNMENT_CONFLICT,
    PLACE_BATCH_TOO_LARGE,
    PLACE_IDEMPOTENCY_CONFLICT,
    PLACE_NOT_FOUND,
    PLACE_OPERATION_NOT_UNDOABLE,
    PLACE_PARENT_CYCLE,
    PLACE_REFERENCED,
    CatalogError,
)
from photo_search.places import assignments as assignment_module
from photo_search.places import directory
from photo_search.places import filter as filter_module
from photo_search.places import revision as revision_module
from photo_search.places.service import PlaceService, encode_place_cursor

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = json.loads(
    (ROOT / "docs" / "decisions" / "contracts" / "p2-place-contracts.v1.json").read_text(
        encoding="utf-8"
    )
)
REQUIRED = {name: set(spec["required"]) for name, spec in CONTRACT["$defs"].items() if "required" in spec}


def open_catalog(tmp_path: Path) -> Catalog:
    return Catalog.open(tmp_path / "data" / "catalog" / "catalog.db", lease_seconds=60)


def seed_assets(catalog: Catalog, count: int = 3) -> list[int]:
    """Insert ``count`` assets, each with one PRESENT location in a registered source."""
    asset_ids: list[int] = []
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO source_roots(display_name, root_path, volume_hint, status) "
            "VALUES ('library', 'C:\\photos', 'C:', 'ONLINE')"
        )
        for index in range(count):
            sha = hashlib.sha256(f"asset-{index}".encode()).hexdigest()
            cursor = connection.execute(
                "INSERT INTO assets(sha256, byte_size, created_at, state) "
                "VALUES (?, ?, ?, 'ACTIVE')",
                (sha, 1000 + index, 1_000 + index),
            )
            asset_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, size, "
                "mtime_ns, last_seen_scan_id, availability) "
                "VALUES (1, ?, ?, ?, ?, 0, NULL, 'PRESENT')",
                (f"photo-{index}.jpg", f"photo-{index}.jpg", asset_id, 1000 + index),
            )
            asset_ids.append(asset_id)
    return asset_ids


def sha_of(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture()
def catalog(tmp_path: Path) -> Catalog:
    instance = open_catalog(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture()
def service(catalog: Catalog) -> PlaceService:
    """A place service with the production projection refresher (no Lance writes yet)."""
    return PlaceService(catalog)


# ------------------------------------------------------------------ the place directory
def test_place_types_hierarchy_and_same_name_places(catalog: Catalog, service: PlaceService) -> None:
    with catalog.write() as connection:
        china = directory.create_place(
            connection, kind="country", canonical_name="中国", origin="DATASET", now_ms=1
        )
        hangzhou = directory.create_place(
            connection,
            kind="city",
            canonical_name="杭州",
            parent_place_id=china,
            aliases=[{"text": "Hangzhou", "language": "en"}],
            now_ms=2,
        )
        lake = directory.create_place(
            connection,
            kind="attraction",
            canonical_name="西湖",
            parent_place_id=hangzhou,
            now_ms=3,
        )
        # A second, same-named city under the same parent stays a distinct stable id.
        other = directory.create_place(
            connection,
            kind="city",
            canonical_name="杭州",
            parent_place_id=china,
            now_ms=4,
        )
        home = directory.create_place(
            connection, kind="custom", canonical_name="外婆家", now_ms=5
        )
    assert len({china, hangzhou, lake, other, home}) == 5

    view = service.get_place(lake)
    assert set(view) == REQUIRED["Place"]
    assert view["kind"] == "attraction" and view["parent_place_id"] == hangzhou
    assert view["aliases"] == []

    hangzhou_view = service.get_place(hangzhou)
    assert hangzhou_view["aliases"] == [
        {
            "text": "Hangzhou",
            "normalized": "hangzhou",
            "language": "en",
            "source": "USER",
            "lifecycle": "ACTIVE",
        }
    ]
    with catalog.write() as connection:
        chain = directory.ancestor_views(connection, lake)
    assert [item["place_id"] for item in chain] == [china, hangzhou]
    assert [item["canonical_name"] for item in chain] == ["中国", "杭州"]

    # Both 杭州 rows are listed with their ids; nothing was merged.
    with catalog.write() as connection:
        rows = directory.list_places(connection, limit=10, name="杭州")
    assert {int(row["place_id"]) for row in rows} == {hangzhou, other}
    with catalog.write() as connection:
        alias_rows = directory.list_places(connection, limit=10, name="hangzhou")
    assert {int(row["place_id"]) for row in alias_rows} == {hangzhou}


def test_parent_matrix_and_cycles_are_refused(catalog: Catalog) -> None:
    with catalog.write() as connection:
        china = directory.create_place(
            connection, kind="country", canonical_name="中国", origin="DATASET", now_ms=1
        )
        city = directory.create_place(
            connection, kind="city", canonical_name="杭州", parent_place_id=china, now_ms=2
        )
        # A country never has a parent.
        with pytest.raises(CatalogError) as country_parent:
            directory.create_place(
                connection,
                kind="country",
                canonical_name="某国",
                parent_place_id=china,
                now_ms=3,
            )
        assert country_parent.value.code == PLACE_PARENT_CYCLE
        # A city may only hang under a country.
        with pytest.raises(CatalogError) as bad_type:
            directory.create_place(
                connection, kind="city", canonical_name="西湖区", parent_place_id=city, now_ms=4
            )
        assert bad_type.value.code == PLACE_PARENT_CYCLE
        # A custom place may hang under any ACTIVE kind.
        custom = directory.create_place(
            connection, kind="custom", canonical_name="家", parent_place_id=city, now_ms=5
        )
        # A re-parent that would make the node its own ancestor is refused.
        with pytest.raises(CatalogError) as cycle:
            directory.assert_no_cycle(connection, place_id=china, parent_place_id=custom)
        assert cycle.value.code == PLACE_PARENT_CYCLE
        # A retired parent cannot receive children.
        connection.execute(
            "UPDATE places SET lifecycle = 'RETIRED', revision = revision + 1 WHERE place_id = ?",
            (city,),
        )
        with pytest.raises(CatalogError) as retired:
            directory.create_place(
                connection, kind="attraction", canonical_name="灵隐", parent_place_id=city, now_ms=6
            )
        assert retired.value.code == PLACE_REFERENCED


def test_rename_and_alias_edit_keep_the_id_and_bump_once(catalog: Catalog, service: PlaceService) -> None:
    with catalog.write() as connection:
        place_id = directory.create_place(
            connection,
            kind="custom",
            canonical_name="外婆家",
            aliases=[{"text": "grandma", "language": "en"}],
            now_ms=1,
        )
    before = service.get_place(place_id)
    revision_before = revision_module.read_filter_revision(catalog.connection)

    view, changed = service.patch_place(
        place_id=place_id,
        expected_revision=before["revision"],
        canonical_name="外婆家（老店）",
        aliases=[{"text": "grandma", "language": "en"}, {"text": "旧店", "language": "zh"}],
    )
    assert changed is True
    assert view["place_id"] == place_id
    assert view["canonical_name"] == "外婆家（老店）"
    assert {alias["text"] for alias in view["aliases"]} == {"grandma", "旧店"}
    assert view["revision"] == before["revision"] + 1
    assert revision_module.read_filter_revision(catalog.connection) == revision_before + 1

    # An edit that changes nothing is a real no-op: no revision, no filter revision.
    stale, again = service.patch_place(
        place_id=place_id,
        expected_revision=view["revision"],
        canonical_name="外婆家（老店）",
        aliases=[{"text": "grandma", "language": "en"}, {"text": "旧店", "language": "zh"}],
    )
    assert again is False and stale["revision"] == view["revision"]
    assert revision_module.read_filter_revision(catalog.connection) == revision_before + 1

    with pytest.raises(CatalogError) as conflict:
        service.patch_place(place_id=place_id, expected_revision=1, canonical_name="别处")
    assert conflict.value.code == PLACE_ASSIGNMENT_CONFLICT
    assert conflict.value.details["current_revision"] == view["revision"]


def test_retiring_a_referenced_place_is_refused(catalog: Catalog, service: PlaceService) -> None:
    assets = seed_assets(catalog, 1)
    places = install_source_places(catalog)
    city, lake = places["hangzhou"], places["west_lake"]
    with pytest.raises(CatalogError) as child:
        service.retire_place(place_id=city)
    assert child.value.code == PLACE_REFERENCED

    service.write_assignment(
        asset_id=assets[0],
        action="ASSIGN",
        target_place_id=lake,
        request_id="assign-1",
        expected_revision=0,
    )
    with pytest.raises(CatalogError) as referenced:
        service.retire_place(place_id=lake)
    assert referenced.value.code == PLACE_REFERENCED
    assert referenced.value.details["place_id"] == lake

    # After unassigning, the exclusion still protects the place from retirement.
    service.write_assignment(
        asset_id=assets[0],
        action="UNASSIGN",
        target_place_id=None,
        request_id="unassign-1",
        expected_revision=1,
    )
    with pytest.raises(CatalogError) as excluded:
        service.retire_place(place_id=lake)
    assert excluded.value.code == PLACE_REFERENCED

    # A place nothing references retires softly and keeps its stable id.
    with catalog.write() as connection:
        standalone = directory.create_place(
            connection, kind="custom", canonical_name="外婆家", now_ms=5
        )
    retired = service.retire_place(place_id=standalone)
    assert retired["lifecycle"] == "RETIRED"
    assert retired["place_id"] == standalone


def test_the_database_refuses_a_non_manual_control_row(catalog: Catalog) -> None:
    """Only a manual, locked row may carry a place; no automatic path exists in v1."""
    assets = seed_assets(catalog, 1)
    with catalog.write() as connection:
        place_id = directory.create_place(connection, kind="city", canonical_name="杭州", now_ms=1)
        connection.execute(
            "INSERT INTO place_operations(operation_id, request_id, payload_sha256, operation_type, "
            "operation_revision, actor, atomic, status, created_at, committed_at) "
            "VALUES ('op-x', 'req-x', ?, 'ASSIGN', 1, 'LOCAL_USER', 1, 'COMMITTED', 1, 1)",
            (sha_of("x"),),
        )
    with pytest.raises(sqlite3.IntegrityError):
        with catalog.write() as connection:
            connection.execute(
                "INSERT INTO asset_place_assignments(asset_id, assignment_id, place_id, mode, origin, "
                "manual_lock, revision, record_state, actor, operation_id, created_at, updated_at) "
                "VALUES (?, 'asg-bad', ?, 'MANUAL_ASSIGNED', 'NONE', 0, 1, 'ACTIVE', 'LOCAL_USER', "
                "'op-x', 1, 1)",
                (assets[0], place_id),
            )


# ------------------------------------------------------------------- the manual control
def test_assign_unassign_restore_auto_and_late_automatic_results(
    catalog: Catalog, service: PlaceService
) -> None:
    assets = seed_assets(catalog, 1)
    asset_id = assets[0]
    places = install_source_places(catalog)
    city = places["hangzhou"]
    lake = places["west_lake"]

    first = service.write_assignment(
        asset_id=asset_id,
        action="ASSIGN",
        target_place_id=lake,
        request_id="a1",
        expected_revision=0,
    )
    assignment = first["items"][0]["assignment"]
    assert assignment["mode"] == "MANUAL_ASSIGNED"
    assert assignment["place_id"] == lake and assignment["origin"] == "MANUAL"
    assert assignment["manual_lock"] is True and assignment["revision"] == 1
    assert set(assignment) == REQUIRED["PlaceAssignment"]

    # assign A -> B records A as excluded and keeps the lock.
    second = service.write_assignment(
        asset_id=asset_id,
        action="ASSIGN",
        target_place_id=city,
        request_id="a2",
        expected_revision=1,
    )
    assert second["items"][0]["assignment"]["place_id"] == city
    assert second["items"][0]["assignment"]["revision"] == 2
    assert [item["place_id"] for item in service.asset_place(asset_id)["exclusions"]] == [lake]

    # Explicitly assigning the excluded place clears only that exclusion.
    back = service.write_assignment(
        asset_id=asset_id,
        action="ASSIGN",
        target_place_id=lake,
        request_id="a3",
        expected_revision=2,
    )
    assert back["items"][0]["cleared_exclusions"] == [lake]
    # Only the target exclusion was cleared; the other one stays (ADR-0005 §5).
    assert [item["place_id"] for item in service.asset_place(asset_id)["exclusions"]] == [city]

    # unassign keeps the lock and excludes the place it was assigned to.
    released = service.write_assignment(
        asset_id=asset_id,
        action="UNASSIGN",
        target_place_id=None,
        request_id="a4",
        expected_revision=3,
    )
    assert released["items"][0]["assignment"]["mode"] == "MANUAL_UNASSIGNED"
    assert released["items"][0]["assignment"]["manual_lock"] is True
    assert [item["place_id"] for item in service.asset_place(asset_id)["exclusions"]] == [city, lake]

    # A restart changes nothing: the control row is persistent and the lock survives.
    reopened = Catalog.open(catalog.db_path, lease_seconds=60)
    try:
        restarted = PlaceService(reopened)
        after_restart = restarted.asset_place(asset_id)
        assert after_restart["assignment"]["mode"] == "MANUAL_UNASSIGNED"
        assert after_restart["assignment"]["revision"] == 4
        assert [item["place_id"] for item in after_restart["exclusions"]] == [city, lake]

        # restore_auto keeps every exclusion and never adopts a suggestion.
        restored = restarted.write_assignment(
            asset_id=asset_id,
            action="RESTORE_AUTO",
            target_place_id=None,
            request_id="a5",
            expected_revision=4,
        )
        assert restored["items"][0]["assignment"]["mode"] == "AUTO_ALLOWED"
        assert restored["items"][0]["assignment"]["origin"] == "NONE"
        assert restored["items"][0]["assignment"]["manual_lock"] is False
        assert [item["place_id"] for item in restarted.asset_place(asset_id)["exclusions"]] == [
            city,
            lake,
        ]
        assert restarted.asset_place(asset_id)["suggested"]["state"] == "no_resolution"
    finally:
        reopened.close()


def test_idempotent_replay_and_payload_conflict(catalog: Catalog, service: PlaceService) -> None:
    assets = seed_assets(catalog, 1)
    city = install_source_places(catalog)["hangzhou"]
    revision_after_first = None
    first = service.write_assignment(
        asset_id=assets[0],
        action="ASSIGN",
        target_place_id=city,
        request_id="same-request",
        expected_revision=0,
    )
    revision_after_first = first["catalog_filter_revision"]
    replay = service.write_assignment(
        asset_id=assets[0],
        action="ASSIGN",
        target_place_id=city,
        request_id="same-request",
        expected_revision=0,
    )
    assert replay["replayed"] is True
    assert replay["operation_id"] == first["operation_id"]
    assert replay["catalog_filter_revision"] == revision_after_first
    assert service.asset_place(assets[0])["assignment"]["revision"] == 1

    with pytest.raises(CatalogError) as conflict:
        service.write_assignment(
            asset_id=assets[0],
            action="UNASSIGN",
            target_place_id=None,
            request_id="same-request",
            expected_revision=1,
        )
    assert conflict.value.code == PLACE_IDEMPOTENCY_CONFLICT
    assert conflict.value.details == {"operation_id": first["operation_id"]}


def test_batch_is_atomic_with_a_500_bound(catalog: Catalog, service: PlaceService) -> None:
    assets = seed_assets(catalog, 4)
    city = install_source_places(catalog)["hangzhou"]

    with pytest.raises(CatalogError) as empty:
        service.write_batch(items=[], request_id="b0")
    assert empty.value.code == "VALIDATION_ERROR"

    with pytest.raises(CatalogError) as duplicate:
        service.write_batch(
            items=[
                {"asset_id": assets[0], "action": "ASSIGN", "target_place_id": city,
                 "expected_revision": 0},
                {"asset_id": assets[0], "action": "ASSIGN", "target_place_id": city,
                 "expected_revision": 0},
            ],
            request_id="b1",
        )
    assert duplicate.value.code == "VALIDATION_ERROR"

    too_large = [
        {"asset_id": index + 1, "action": "ASSIGN", "target_place_id": city,
         "expected_revision": 0}
        for index in range(MAX_PLACE_BATCH_SIZE + 1)
    ]
    with pytest.raises(CatalogError) as big:
        service.write_batch(items=too_large, request_id="b2")
    assert big.value.code == PLACE_BATCH_TOO_LARGE
    assert big.value.details == {"max_batch_size": MAX_PLACE_BATCH_SIZE}

    # One conflicting item rejects the whole batch: no assignment, no operation history.
    items = [
        {"asset_id": assets[0], "action": "ASSIGN", "target_place_id": city, "expected_revision": 0},
        {"asset_id": assets[1], "action": "ASSIGN", "target_place_id": city, "expected_revision": 7},
    ]
    with pytest.raises(CatalogError) as conflict:
        service.write_batch(items=items, request_id="b3")
    assert conflict.value.code == PLACE_ASSIGNMENT_CONFLICT
    assert conflict.value.details["asset_ids"] == [assets[1]]
    assert catalog.read_one("SELECT COUNT(*) FROM asset_place_assignments")[0] == 0
    assert catalog.read_one("SELECT COUNT(*) FROM place_operations")[0] == 0

    # A batch of four commits in one operation with one filter revision move.
    before = revision_module.read_filter_revision(catalog.connection)
    ok_items = [
        {"asset_id": asset_id, "action": "ASSIGN", "target_place_id": city, "expected_revision": 0}
        for asset_id in assets
    ]
    applied = service.write_batch(items=ok_items, request_id="b4")
    assert applied["operation_type"] == "BATCH_ASSIGN"
    assert applied["changed"] is True
    assert applied["catalog_filter_revision"] == before + 1
    assert catalog.read_one("SELECT COUNT(*) FROM asset_place_assignments")[0] == 4


def test_undo_compensates_or_refuses_the_whole_operation(catalog: Catalog, service: PlaceService) -> None:
    assets = seed_assets(catalog, 2)
    city = install_source_places(catalog)["hangzhou"]
    applied = service.write_batch(
        items=[
            {"asset_id": asset_id, "action": "ASSIGN", "target_place_id": city,
             "expected_revision": 0}
            for asset_id in assets
        ],
        request_id="undo-source",
    )
    revision_before_undo = applied["catalog_filter_revision"]

    undone = service.undo(
        operation_id=applied["operation_id"],
        request_id="undo-1",
        expected_operation_revision=applied["operation_revision"],
    )
    assert undone["operation_type"] == "UNDO"
    assert undone["undo_of_operation_id"] == applied["operation_id"]
    assert undone["catalog_filter_revision"] == revision_before_undo + 1
    for item in undone["items"]:
        # The item had no control row before, so the compensation writes the equivalent
        # AUTO_ALLOWED state with a strictly larger revision.
        assert item["assignment"]["mode"] == "AUTO_ALLOWED"
        assert item["assignment"]["revision"] == 2
        assert item["changed"] is True
    assert service.asset_place(assets[0])["place"] is None
    assert catalog.read_one(
        "SELECT status FROM place_operations WHERE operation_id = ?", (applied["operation_id"],)
    )["status"] == "REVERSED"

    # Repeating the same undo request is an idempotent replay.
    again = service.undo(
        operation_id=applied["operation_id"],
        request_id="undo-1",
        expected_operation_revision=applied["operation_revision"],
    )
    assert again["replayed"] is True and again["operation_id"] == undone["operation_id"]

    # A second, independent undo of the same operation is refused: it is already reversed.
    with pytest.raises(CatalogError) as twice:
        service.undo(
            operation_id=applied["operation_id"],
            request_id="undo-2",
            expected_operation_revision=applied["operation_revision"],
        )
    assert twice.value.code == PLACE_OPERATION_NOT_UNDOABLE

    # A later edit of one target makes the whole batch non-undoable and keeps its history.
    second_op = service.write_batch(
        items=[
            {"asset_id": asset_id, "action": "ASSIGN", "target_place_id": city,
             "expected_revision": 2}
            for asset_id in assets
        ],
        request_id="undo-source-2",
    )
    service.write_assignment(
        asset_id=assets[1],
        action="UNASSIGN",
        target_place_id=None,
        request_id="later-edit",
        expected_revision=3,
    )
    history_before = catalog.read_one("SELECT COUNT(*) FROM asset_place_assignment_history")[0]
    with pytest.raises(CatalogError) as blocked:
        service.undo(
            operation_id=second_op["operation_id"],
            request_id="undo-3",
            expected_operation_revision=second_op["operation_revision"],
        )
    assert blocked.value.code == PLACE_OPERATION_NOT_UNDOABLE
    assert blocked.value.details["asset_ids"] == [assets[1]]
    assert catalog.read_one("SELECT COUNT(*) FROM asset_place_assignment_history")[0] == history_before
    assert catalog.read_one(
        "SELECT status FROM place_operations WHERE operation_id = ?", (second_op["operation_id"],)
    )["status"] == "COMMITTED"


def test_operation_log_is_append_only_and_listed(catalog: Catalog, service: PlaceService) -> None:
    assets = seed_assets(catalog, 1)
    city = install_source_places(catalog)["hangzhou"]
    service.write_assignment(
        asset_id=assets[0],
        action="ASSIGN",
        target_place_id=city,
        request_id="log-1",
        expected_revision=0,
    )
    page = service.list_operations(limit=10)
    assert set(page) == {"operations", "limit", "next_cursor", "has_more"}
    assert len(page["operations"]) == 1
    # The list projection is the frozen PlaceOperation minus its item list, plus the
    # filter revision the operation committed under.
    expected = (REQUIRED["PlaceOperation"] | {"catalog_filter_revision"}) - {"items"}
    assert set(page["operations"][0]) == expected
    with pytest.raises(sqlite3.IntegrityError):
        with catalog.write() as connection:
            connection.execute("DELETE FROM place_operations")


# ------------------------------------------------------------------ the revision matrix
def test_filter_revision_matrix_and_same_transaction_projection_refresh(catalog: Catalog) -> None:
    assets = seed_assets(catalog, 2)
    service = PlaceService(catalog)
    city = install_source_places(catalog)["hangzhou"]
    baseline = revision_module.read_filter_revision(catalog.connection)

    # Seed one CONFIRMED vector for the first asset, exactly the P2-03 shape.
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO index_generations(generation_id, space_id, processor_id, dimension, "
            "state, is_active, created_at, updated_at) "
            "VALUES ('gen-1', 'space-1', 'clip@1', 4, 'READY', 1, 1, 1)"
        )
        connection.execute(
            "INSERT INTO jobs(subject_type, subject_id, input_revision, task_type, processor_id, "
            "state, priority, attempts, max_attempts, run_after, progress, created_at, updated_at) "
            "VALUES ('asset', ?, ?, 'image_embedding', 'clip@1', 'SUCCEEDED', 0, 1, 3, 1, 1.0, 1, 1)",
            (str(assets[0]), sha_of("rev")),
        )
        job_id = int(connection.execute("SELECT MAX(job_id) FROM jobs").fetchone()[0])
        connection.execute(
            "INSERT INTO artifacts(artifact_id, processor_id, input_revision, artifact_type, "
            "artifact_revision, media_type, checksum_sha256, byte_size, storage_uri, space_id, "
            "dimension, job_id, lifecycle_state, created_at, updated_at) "
            "VALUES ('art-1', 'clip@1', ?, 'embedding', 'v1', 'application/json', ?, 4, "
            "'artifacts/pending/aa/bb/cc.json', 'space-1', 4, ?, 'PUBLISHED', 1, 1)",
            (sha_of("rev"), sha_of("bytes"), job_id),
        )
        connection.execute(
            "INSERT INTO index_entries(space_id, asset_id, generation_id, artifact_id, "
            "confirmation_revision, confirmed_at, state) "
            "VALUES ('space-1', ?, 'gen-1', 'art-1', 0, 1, 'CONFIRMED')",
            (assets[0],),
        )

    applied = service.write_assignment(
        asset_id=assets[0],
        action="ASSIGN",
        target_place_id=city,
        request_id="matrix-1",
        expected_revision=0,
    )
    assert applied["catalog_filter_revision"] == baseline + 1
    events = list(
        catalog.read_all(
            "SELECT revision, cause, changed_count, all_assets FROM catalog_filter_revision_events "
            "WHERE revision > ?", (baseline,)
        )
    )
    assert [int(row["revision"]) for row in events] == [baseline + 1]
    assert events[0]["cause"] == revision_module.CAUSE_EFFECTIVE_ASSIGNMENT_CHANGED
    # The affected asset's confirmed vector was queued in the same transaction.
    outbox = list(catalog.read_all("SELECT asset_id, projection_revision, state FROM index_outbox"))
    assert [(int(row["asset_id"]), int(row["projection_revision"])) for row in outbox] == [
        (assets[0], baseline + 1)
    ]

    # An idempotent no-op never moves the revision again.
    service.write_assignment(
        asset_id=assets[0],
        action="ASSIGN",
        target_place_id=city,
        request_id="matrix-2",
        expected_revision=1,
    )
    assert revision_module.read_filter_revision(catalog.connection) == baseline + 1
    assert catalog.read_one(
        "SELECT COUNT(*) FROM catalog_filter_revision_events WHERE revision > ?", (baseline,)
    )[0] == 1

    # The second asset has no confirmed vector, so it needs no outbox event.
    service.write_assignment(
        asset_id=assets[1],
        action="ASSIGN",
        target_place_id=city,
        request_id="matrix-3",
        expected_revision=0,
    )
    assert revision_module.read_filter_revision(catalog.connection) == baseline + 2
    assert catalog.read_one("SELECT COUNT(*) FROM index_outbox")[0] == 1


def test_a_failed_commit_leaves_no_partial_place_state(
    catalog: Catalog, service: PlaceService, monkeypatch: pytest.MonkeyPatch
) -> None:
    assets = seed_assets(catalog, 1)
    city = install_source_places(catalog)["hangzhou"]
    baseline = revision_module.read_filter_revision(catalog.connection)
    events_before = [tuple(row) for row in catalog.read_all(
        "SELECT * FROM catalog_filter_revision_events ORDER BY revision"
    )]

    def boom(*_: Any, **__: Any) -> int:
        raise CatalogError("MIGRATION_FAILED", "injected failure after the authority write")

    monkeypatch.setattr(assignment_module.revision_module, "bump_filter_revision", boom)
    with pytest.raises(CatalogError) as failed:
        service.write_assignment(
            asset_id=assets[0],
            action="ASSIGN",
            target_place_id=city,
            request_id="rollback-1",
            expected_revision=0,
        )
    assert failed.value.code == "MIGRATION_FAILED"
    for table in (
        "asset_place_assignments",
        "asset_place_assignment_history",
        "place_operations",
        "place_operation_items",
        "index_outbox",
    ):
        assert catalog.read_one(f"SELECT COUNT(*) FROM {table}")[0] == 0, table
    assert revision_module.read_filter_revision(catalog.connection) == baseline
    assert [tuple(row) for row in catalog.read_all(
        "SELECT * FROM catalog_filter_revision_events ORDER BY revision"
    )] == events_before


# ------------------------------------------------------------------------- the filtering
def test_place_filters_are_authoritative_and_deduplicated(catalog: Catalog) -> None:
    assets = seed_assets(catalog, 5)
    service = PlaceService(catalog)
    places = install_source_places(catalog)
    china, hangzhou = places["china"], places["hangzhou"]
    lake, other_city = places["west_lake"], places["beijing"]
    with catalog.write() as connection:
        # A second PRESENT location of the same content must not be counted twice.
        connection.execute(
            "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, size, "
            "mtime_ns, last_seen_scan_id, availability) "
            "VALUES (1, 'copy.jpg', 'copy.jpg', ?, 1000, 0, NULL, 'PRESENT')",
            (assets[0],),
        )

    service.write_assignment(
        asset_id=assets[0], action="ASSIGN", target_place_id=lake, request_id="f1",
        expected_revision=0,
    )
    service.write_assignment(
        asset_id=assets[1], action="ASSIGN", target_place_id=other_city, request_id="f2",
        expected_revision=0,
    )
    service.write_assignment(
        asset_id=assets[2], action="UNASSIGN", target_place_id=None, request_id="f3",
        expected_revision=0,
    )

    def matching(place_ids: list[int], *, state: str = "any", descendants: bool = True) -> list[int]:
        sql, parameters = filter_module.place_condition(
            catalog.connection,
            place_ids=place_ids,
            include_descendants=descendants,
            place_state=state,
        )
        rows = catalog.read_all(
            f"SELECT asset_id FROM assets AS a WHERE {sql} ORDER BY asset_id", tuple(parameters)
        )
        return [int(row["asset_id"]) for row in rows]

    # Ancestor filter expands to descendants and de-duplicates by asset.
    assert matching([china]) == [assets[0], assets[1]]
    assert matching([hangzhou]) == [assets[0]]
    assert matching([hangzhou], descendants=False) == []
    assert matching([china], state="assigned") == [assets[0], assets[1]]
    assert matching([]) == assets
    assert matching([], state="assigned") == [assets[0], assets[1]]
    assert matching([], state="unassigned") == [assets[2], assets[3], assets[4]]
    assert matching([], state="manual_unassigned") == [assets[2]]
    assert matching([], state="suggested") == []
    with pytest.raises(CatalogError) as invalid_state:
        matching([hangzhou], state="unassigned")
    assert invalid_state.value.code == INVALID_PLACE_FILTER
    with pytest.raises(CatalogError) as bad_state:
        matching([], state="everywhere")
    assert bad_state.value.code == INVALID_PLACE_FILTER
    with pytest.raises(CatalogError) as unknown:
        matching([9999])
    assert unknown.value.code == PLACE_NOT_FOUND


def test_coverage_and_cursor_binding(catalog: Catalog, service: PlaceService) -> None:
    assets = seed_assets(catalog, 3)
    city = install_source_places(catalog)["hangzhou"]
    service.write_assignment(
        asset_id=assets[0], action="ASSIGN", target_place_id=city, request_id="c1",
        expected_revision=0,
    )
    service.write_assignment(
        asset_id=assets[1], action="UNASSIGN", target_place_id=None, request_id="c2",
        expected_revision=0,
    )
    report = service.coverage()
    assert report["assets_total"] == 3
    assert report["places_assigned"] == 1
    assert report["places_manual_unassigned"] == 1
    assert report["places_unclassified"] == 2
    assert report["place_resolved"] == 0 and report["resolver_available"] is True
    assert report["places_suggested"] == 0

    first = service.list_places(limit=1)
    assert first["has_more"] is True and first["next_cursor"] is not None
    with catalog.write() as connection:
        directory.create_place(connection, kind="custom", canonical_name="外婆家", now_ms=2)
    stale = encode_place_cursor(revision=first["catalog_filter_revision"], place_id=city)
    service.patch_place(place_id=city, expected_revision=1, canonical_name="杭州市")
    with pytest.raises(CatalogError) as expired:
        service.list_places(limit=1, cursor=stale)
    assert expired.value.code == CATALOG_FILTER_REVISION_STALE
