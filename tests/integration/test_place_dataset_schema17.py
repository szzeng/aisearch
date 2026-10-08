"""Schema 17's additive map tables must preserve schema-16 place packages."""

from pathlib import Path
from shutil import copyfile

import pytest
from p2_dataset_fixtures import HANGZHOU_POSITION, build_package

from photo_search.catalog import schema
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import PLACE_DATASET_INVALID, CatalogError
from photo_search.config import place_datasets_dir
from photo_search.geo import dataset, resolver
from photo_search.ingestion.service import CatalogService
from photo_search.places import datasets


def snapshot(package: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in package.iterdir()
        if path.is_file()
    }


def test_schema16_active_package_survives_catalog_upgrade(tmp_path, monkeypatch):
    root = tmp_path / "data"
    package = place_datasets_dir(root) / "geo-mini-cn-1"
    build_package(package, compatible=(5, 16))
    original = snapshot(package)
    old_migrations = tmp_path / "schema16-migrations"
    old_migrations.mkdir()
    for version, migration in schema.migration_files():
        if version <= 16:
            copyfile(migration, old_migrations / migration.name)
    with monkeypatch.context() as patch:
        patch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 16)
        patch.setattr(schema, "MIGRATIONS_DIR", old_migrations)
        with Catalog.open(data_root=root) as catalog:
            datasets.stage_package(catalog, package)
            datasets.validate_dataset(catalog, "geo-mini-cn-1")
            datasets.activate_dataset(catalog, "geo-mini-cn-1")
            before = [
                tuple(row) for row in catalog.read_all("SELECT * FROM places ORDER BY place_id")
            ]
            active_revision = catalog.read_one("SELECT revision FROM active_geo_dataset")[0]
    with CatalogService.open(data_root=root) as service:
        state = datasets.dataset_status(service.catalog.connection, data_root=root)
        assert state["state"] == "active" and state["resolver_available"]
        assert schema.read_schema_version(service.catalog.connection) == schema.SUPPORTED_SCHEMA_VERSION
        assert (
            service.catalog.read_one("SELECT revision FROM active_geo_dataset")[0]
            == active_revision
        )
        assert [
            tuple(row) for row in service.catalog.read_all("SELECT * FROM places ORDER BY place_id")
        ] == before
        content = resolver.load_active_content(service.catalog.connection, data_root=root, cache={})
        assert (
            dataset.nearby_cities(
                content, *HANGZHOU_POSITION, max_candidates=5, max_distance_m=50000
            )[0]["display_name"]
            == "杭州"
        )
        datasets.activate_dataset(service.catalog, "geo-mini-cn-1")
    assert snapshot(package) == original


def test_schema16_package_recovers_into_empty_schema17_catalog(tmp_path):
    root = tmp_path / "data"
    package = place_datasets_dir(root) / "geo-mini-cn-1"
    build_package(package, compatible=(5, 16))
    original = snapshot(package)
    with CatalogService.open(data_root=root) as service:
        state = datasets.dataset_status(service.catalog.connection, data_root=root)
        assert state["state"] == "active" and state["resolver_available"]
    assert snapshot(package) == original


@pytest.mark.parametrize(
    "minimum,maximum,supported,accepted",
    [
        (5, 16, 17, True),
        (16, 16, 17, True),
        (5, 17, 17, True),
        (5, 16, 16, True),
        (5, 15, 17, False),
        (18, 20, 17, False),
        (5, 16, 18, True),
        (5, 17, 18, True),
        (5, 15, 18, False),
        (5, 17, 19, True),
        (18, 18, 19, True),
        (5, 15, 19, False),
        (5, 16, 20, True),
        (5, 17, 20, True),
        (18, 18, 20, True),
        (5, 19, 20, True),
        (5, 15, 20, False),
        (21, 21, 20, False),
        (5, 20, 21, False),
    ],
)
def test_schema_bridge_is_explicit_and_bounded(tmp_path, minimum, maximum, supported, accepted):
    manifest = build_package(tmp_path / "package", compatible=(minimum, maximum))
    if accepted:
        result = dataset.validate_manifest(manifest, supported_schema_version=supported)
        assert result["compatible_catalog_schema"] == {"minimum": minimum, "maximum": maximum}
    else:
        with pytest.raises(CatalogError) as error:
            dataset.validate_manifest(manifest, supported_schema_version=supported)
        assert error.value.code == PLACE_DATASET_INVALID


@pytest.mark.parametrize("supported", [17, 20])
def test_schema_bridge_still_rejects_corrupt_files(tmp_path, supported):
    package = tmp_path / "package"
    build_package(package, compatible=(5, 16))
    places = package / "places.jsonl"
    places.write_bytes(places.read_bytes() + b"\n")
    with pytest.raises(CatalogError) as error:
        dataset.load_content(package, supported_schema_version=supported)
    assert error.value.code == PLACE_DATASET_INVALID
    assert "schema" not in error.value.message
