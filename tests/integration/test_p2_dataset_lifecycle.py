"""P2-03B: the offline package lifecycle over a real catalog and a real data root.

The synthetic packages are built into ``%TEMP%``; the import copies them into the
application data root, and every state change is verified against the catalog rows, the
on-disk files and the materialised local place directory (ADR-0005 §2/§8).
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from p2_dataset_fixtures import (
    HANGZHOU_POSITION,
    MINI_PLACES,
    Harness,
    mini_spec,
    write_gps_jpeg,
)

from photo_search.catalog.errors import (
    PLACE_DATASET_INVALID,
    PLACE_DATASET_MISSING,
    PLACE_NOT_FOUND,
    PLACE_REFERENCED,
    CatalogError,
)
from photo_search.geo import dataset as dataset_module
from photo_search.geo import resolver as resolver_module
from photo_search.places import datasets as datasets_module


@pytest.fixture()
def harness(tmp_path: Path) -> Harness:
    instance = Harness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def package_dir(harness: Harness, dataset_id: str) -> Path:
    return datasets_module.dataset_dir(harness.data_root, dataset_id)


def row_of(harness: Harness, dataset_id: str):
    return datasets_module.dataset_row(harness.catalog.connection, dataset_id)


def test_an_imported_package_is_only_usable_after_validation_and_activation(
    harness: Harness,
) -> None:
    source = harness.source_package("mini-1")

    staged = datasets_module.stage_package(harness.catalog, source)

    assert staged["state"] == "STAGED"
    assert harness.catalog.read_one(
        "SELECT COUNT(*) AS value FROM active_geo_dataset WHERE dataset_id IS NOT NULL"
    )["value"] == 0
    assert datasets_module.resolver_available(harness.catalog.connection) is False
    copied = package_dir(harness, staged["dataset_id"])
    assert (copied / dataset_module.MANIFEST_FILENAME).is_file()
    assert (copied / "places.jsonl").read_bytes() == (source / "places.jsonl").read_bytes()
    # Importing must not create a local place or an external reference yet.
    assert harness.catalog.read_one("SELECT COUNT(*) AS value FROM places")["value"] == 0

    validated = datasets_module.validate_dataset(harness.catalog, staged["dataset_id"])

    assert validated["state"] == "VALIDATED"
    assert validated["validated_at"] is not None
    assert datasets_module.resolver_available(harness.catalog.connection) is False
    assert harness.catalog.read_one("SELECT COUNT(*) AS value FROM places")["value"] == 0

    activated = datasets_module.activate_dataset(harness.catalog, staged["dataset_id"])

    assert activated["state"] == "ACTIVE"
    assert activated["places_mapped"] == len(MINI_PLACES)
    assert datasets_module.resolver_available(harness.catalog.connection) is True
    active = datasets_module.active_row(harness.catalog.connection)
    assert str(active["dataset_id"]) == staged["dataset_id"]
    assert str(active["state"]) == "ACTIVE"
    # Every dataset record became a local place with an external reference, and the
    # country/city hierarchy survived the mapping.
    assert harness.catalog.read_one("SELECT COUNT(*) AS value FROM places")["value"] == len(
        MINI_PLACES
    )
    assert harness.catalog.read_one(
        "SELECT COUNT(*) AS value FROM place_external_refs WHERE provider = 'synthetic-mini'"
    )["value"] == len(MINI_PLACES)
    hangzhou = harness.catalog.read_one(
        "SELECT p.parent_place_id, p.kind FROM places AS p "
        "JOIN place_external_refs AS r ON r.place_id = p.place_id "
        "WHERE r.external_id = 'city-hangzhou'"
    )
    assert str(hangzhou["kind"]) == "city" and int(hangzhou["parent_place_id"]) > 0
    aliases = harness.catalog.read_all(
        "SELECT text, language, source FROM place_aliases ORDER BY text"
    )
    assert {str(row["text"]) for row in aliases} == {"Hangzhou", "Shanghai"}
    assert all(str(row["source"]) == "DATASET" for row in aliases)
    # Activation is a suggestion-side change: it must not move the catalog filter revision.
    assert harness.catalog.read_one(
        "SELECT revision FROM catalog_filter_revision"
    )["revision"] == 0


def test_surviving_read_only_package_can_be_registered_after_catalog_recreation(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = harness.source_package("mini-1")
    expected = package_dir(harness, "geo-mini-cn-1")
    shutil.copytree(source, expected)
    original_files = {
        path.name: path.read_bytes() for path in expected.iterdir() if path.is_file()
    }

    def fail_if_copied(*args, **kwargs) -> None:
        pytest.fail("recovery must not rewrite the surviving package")

    monkeypatch.setattr(datasets_module, "_copy_package_files", fail_if_copied)
    staged = datasets_module.stage_package(harness.catalog, expected)
    datasets_module.validate_dataset(harness.catalog, staged["dataset_id"])
    activated = datasets_module.activate_dataset(harness.catalog, staged["dataset_id"])

    assert activated["state"] == "ACTIVE"
    assert datasets_module.dataset_status(
        harness.catalog.connection, data_root=harness.data_root
    )["state"] == "active"
    assert {path.name: path.read_bytes() for path in expected.iterdir() if path.is_file()} == (
        original_files
    )


def test_importing_the_same_dataset_twice_is_refused(harness: Harness) -> None:
    source = harness.source_package("mini-1")
    datasets_module.stage_package(harness.catalog, source)

    with pytest.raises(CatalogError) as error:
        datasets_module.stage_package(harness.catalog, source)

    assert error.value.code == PLACE_DATASET_INVALID


def test_a_failed_activation_keeps_the_previous_pointer_and_state(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.install("mini-1")
    before = datasets_module.active_row(harness.catalog.connection)
    harness.install("mini-2", activate=False, dataset_id="geo-mini-cn-2")

    def explode(*_args, **_kwargs):
        raise RuntimeError("injected failure after the pointer update")

    monkeypatch.setattr(datasets_module, "materialize_dataset_places", explode)
    with pytest.raises(RuntimeError):
        datasets_module.activate_dataset(harness.catalog, "geo-mini-cn-2")

    monkeypatch.undo()
    after = datasets_module.active_row(harness.catalog.connection)
    assert str(after["dataset_id"]) == str(before["dataset_id"])
    assert str(after["state"]) == "ACTIVE"
    assert str(row_of(harness, "geo-mini-cn-2")["state"]) == "VALIDATED"
    # No half-materialised place and no leaked pointer revision.
    assert harness.catalog.read_one("SELECT COUNT(*) AS value FROM places")["value"] == len(
        MINI_PLACES
    )
    assert harness.catalog.read_one(
        "SELECT revision FROM active_geo_dataset WHERE singleton = 1"
    )["revision"] == 1


def test_activating_a_second_package_retires_the_first_and_rollback_returns_to_it(
    harness: Harness,
) -> None:
    harness.install("mini-1")
    harness.install(
        "mini-2",
        spec=mini_spec(
            dataset_id="geo-mini-cn-2",
            package_version="2",
            previous_dataset_id="geo-mini-cn-1",
        ),
    )

    assert str(datasets_module.active_row(harness.catalog.connection)["dataset_id"]) == (
        "geo-mini-cn-2"
    )
    assert str(row_of(harness, "geo-mini-cn-1")["state"]) == "RETAINED"

    rolled = datasets_module.rollback_dataset(harness.catalog)

    assert rolled["dataset_id"] == "geo-mini-cn-1"
    assert rolled["rolled_back_from"] == "geo-mini-cn-2"
    assert str(row_of(harness, "geo-mini-cn-1")["state"]) == "ACTIVE"
    assert str(row_of(harness, "geo-mini-cn-2")["state"]) == "RETAINED"
    assert str(datasets_module.active_row(harness.catalog.connection)["dataset_id"]) == (
        "geo-mini-cn-1"
    )
    # The dataset place ids are already mapped, so a rollback never duplicates a place.
    assert harness.catalog.read_one("SELECT COUNT(*) AS value FROM places")["value"] == len(
        MINI_PLACES
    )


def test_a_missing_or_tampered_package_blocks_resolution_and_rolls_back(
    harness: Harness,
) -> None:
    harness.install("mini-1")
    harness.install(
        "mini-2",
        spec=mini_spec(
            dataset_id="geo-mini-cn-2",
            package_version="2",
            previous_dataset_id="geo-mini-cn-1",
        ),
    )
    write_gps_jpeg(harness.library / "small.jpg", position=HANGZHOU_POSITION)
    harness.scan()
    asset_id = harness.asset_id("small.jpg")
    harness.run_gps([asset_id])

    # Remove one declared file of the *active* package: resolution blocks, nothing is lost.
    (package_dir(harness, "geo-mini-cn-2") / "places.jsonl").unlink()
    status = datasets_module.dataset_status(
        harness.catalog.connection, data_root=harness.data_root
    )
    assert status["state"] == "missing"
    assert status["reason"] == PLACE_DATASET_MISSING
    assert status["resolver_available"] is False
    with pytest.raises(CatalogError) as error:
        resolver_module.evaluate(
            harness.catalog, asset_id=asset_id, input_revision=harness.sha_of(asset_id),
            data_root=harness.data_root, cache={},
        )
    assert error.value.code == PLACE_DATASET_MISSING

    # A rollback re-verifies the target, not the active package, so it still works.
    rolled = datasets_module.rollback_dataset(harness.catalog)
    assert rolled["dataset_id"] == "geo-mini-cn-1"
    assert datasets_module.dataset_status(
        harness.catalog.connection, data_root=harness.data_root
    )["state"] == "active"


def test_a_tampered_active_package_is_reported_as_invalid(harness: Harness) -> None:
    harness.install("mini-1")
    places = package_dir(harness, "geo-mini-cn-1") / "places.jsonl"
    original = places.read_bytes()
    places.write_bytes(original.replace("杭州".encode(), "杭洲".encode()))

    status = datasets_module.dataset_status(
        harness.catalog.connection, data_root=harness.data_root
    )

    assert status["state"] == "invalid"
    assert status["reason"] == PLACE_DATASET_INVALID
    assert status["resolver_available"] is False
    assert status["active_dataset"] is not None
    assert status["active_dataset"]["problems"][0]["code"] == PLACE_DATASET_INVALID


def test_activating_a_tampered_staged_package_is_refused_and_keeps_the_pointer(
    harness: Harness,
) -> None:
    harness.install("mini-1")
    harness.install("mini-2", activate=False, dataset_id="geo-mini-cn-2")
    # The package was valid at import time; tamper with the *stored* copy afterwards.
    stored = package_dir(harness, "geo-mini-cn-2") / "places.jsonl"
    stored.write_bytes(stored.read_bytes().replace("杭州".encode(), "杭洲".encode()))

    with pytest.raises(CatalogError) as error:
        datasets_module.activate_dataset(harness.catalog, "geo-mini-cn-2")

    assert error.value.code == PLACE_DATASET_INVALID
    assert str(datasets_module.active_row(harness.catalog.connection)["dataset_id"]) == (
        "geo-mini-cn-1"
    )
    assert str(row_of(harness, "geo-mini-cn-2")["state"]) == "VALIDATED"
    assert harness.catalog.read_one(
        "SELECT revision FROM active_geo_dataset WHERE singleton = 1"
    )["revision"] == 1


def test_an_unknown_dataset_is_not_found(harness: Harness) -> None:
    with pytest.raises(CatalogError) as error:
        datasets_module.activate_dataset(harness.catalog, "geo-missing")
    assert error.value.code == PLACE_NOT_FOUND
    with pytest.raises(CatalogError) as cleanup_error:
        datasets_module.cleanup_dataset(harness.catalog, "geo-missing")
    assert cleanup_error.value.code == PLACE_NOT_FOUND


def test_a_referenced_package_cannot_be_cleaned_up(harness: Harness) -> None:
    harness.install("mini-1")

    with pytest.raises(CatalogError) as active_error:
        datasets_module.cleanup_dataset(harness.catalog, "geo-mini-cn-1")
    assert active_error.value.code == PLACE_REFERENCED

    harness.install(
        "mini-2",
        spec=mini_spec(
            dataset_id="geo-mini-cn-2",
            previous_dataset_id="geo-mini-cn-1",
        ),
    )
    # ``mini-2`` is active and names ``mini-1`` as its rollback target.
    with pytest.raises(CatalogError) as rollback_edge:
        datasets_module.cleanup_dataset(harness.catalog, "geo-mini-cn-1")
    assert rollback_edge.value.code == PLACE_REFERENCED


def test_a_resolution_referenced_package_cannot_be_cleaned_up(harness: Harness) -> None:
    harness.install("mini-1")
    write_gps_jpeg(harness.library / "small.jpg", position=HANGZHOU_POSITION)
    harness.scan()
    asset_id = harness.asset_id("small.jpg")
    harness.run_gps([asset_id])
    harness.run_resolver([asset_id])
    staged_only = harness.install("mini-2", activate=False, dataset_id="geo-mini-cn-2")
    assert staged_only["state"] == "VALIDATED"

    with pytest.raises(CatalogError) as error:
        datasets_module.cleanup_dataset(harness.catalog, "geo-mini-cn-1")

    assert error.value.code == PLACE_REFERENCED


def test_an_unreferenced_package_can_be_removed_but_never_reaches_staged_again(
    harness: Harness,
) -> None:
    source = harness.source_package("mini-1")
    staged = datasets_module.stage_package(harness.catalog, source)
    directory = package_dir(harness, staged["dataset_id"])
    assert directory.is_dir()

    removed = datasets_module.cleanup_dataset(harness.catalog, staged["dataset_id"])

    assert removed["state"] == "REMOVED"
    assert not directory.exists()
    assert row_of(harness, staged["dataset_id"]) is None
    assert harness.catalog.read_one(
        "SELECT COUNT(*) AS value FROM geo_dataset_files WHERE dataset_id = ?",
        (staged["dataset_id"],),
    )["value"] == 0

    # A validated package may never slide back to STAGED once it left that state.
    source_again = harness.source_package("mini-2", dataset_id="geo-mini-cn-9")
    again = datasets_module.stage_package(harness.catalog, source_again)
    datasets_module.validate_dataset(harness.catalog, again["dataset_id"])
    with pytest.raises(sqlite3.IntegrityError):
        with harness.catalog.write() as connection:
            connection.execute(
                "UPDATE geo_datasets SET state = 'STAGED' WHERE dataset_id = ?",
                (again["dataset_id"],),
            )


def test_package_content_is_immutable_once_imported(harness: Harness) -> None:
    source = harness.source_package("mini-1")
    staged = datasets_module.stage_package(harness.catalog, source)

    with pytest.raises(sqlite3.IntegrityError):
        with harness.catalog.write() as connection:
            connection.execute(
                "UPDATE geo_datasets SET content_sha256 = ? WHERE dataset_id = ?",
                ("0" * 64, staged["dataset_id"]),
            )


def test_dataset_status_is_unavailable_before_any_import(harness: Harness) -> None:
    status = datasets_module.dataset_status(
        harness.catalog.connection, data_root=harness.data_root
    )

    assert status["state"] == "unavailable"
    assert status["reason"] == "PLACE_DATASET_NOT_INSTALLED"
    assert status["resolver_available"] is False
    assert status["active_dataset"] is None
    assert status["datasets"] == []

    harness.install("mini-1", activate=False)
    installed = datasets_module.dataset_status(
        harness.catalog.connection, data_root=harness.data_root
    )
    assert installed["state"] == "installed"
    assert installed["reason"] == "NO_ACTIVE_DATASET"
    assert len(installed["datasets"]) == 1
    assert installed["datasets"][0]["state"] == "VALIDATED"
    # No local absolute path may appear in the projection.
    assert str(harness.data_root) not in str(installed)
