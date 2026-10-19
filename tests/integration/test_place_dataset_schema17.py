"""Schema 17's additive map tables must preserve schema-16 place packages."""

from pathlib import Path
from shutil import copyfile

import pytest
from p2_dataset_fixtures import HANGZHOU_POSITION, build_package
from p4_cn_fixtures import install as install_composite

from photo_search.catalog import schema
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import PLACE_DATASET_INVALID, CatalogError
from photo_search.config import place_datasets_dir
from photo_search.geo import dataset, resolver
from photo_search.ingestion.service import CatalogService
from photo_search.places import datasets


def snapshot(package: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(package).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in package.rglob("*")
        if path.is_file()
    }


def test_schema16_package_survives_catalog_upgrade(tmp_path, monkeypatch):
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
    with CatalogService.open(data_root=root) as service:
        datasets.validate_dataset(service.catalog, "geo-mini-cn-1")
        datasets.activate_dataset(service.catalog, "geo-mini-cn-1")
        before = [
            tuple(row) for row in service.catalog.read_all(
                "SELECT * FROM places ORDER BY place_id"
            )
        ]
        active_revision = service.catalog.read_one("SELECT revision FROM active_geo_dataset")[0]
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
        (18, 18, 21, True),
        (5, 20, 21, True),
        (5, 15, 21, False),
        (22, 22, 21, False),
        (18, 18, 22, True),
        (5, 16, 32, True),
        (18, 18, 32, True),
        (5, 31, 32, True),
        (5, 15, 32, False),
        (33, 33, 32, False),
        (5, 16, 33, True),
        (18, 18, 33, True),
        (5, 31, 33, True),
        (5, 32, 33, True),
        (5, 33, 33, True),
        (5, 15, 33, False),
        (34, 34, 33, False),
        (5, 16, 34, True),
        (18, 18, 34, True),
        (5, 33, 34, True),
        (5, 34, 34, True),
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


def test_future_schema_bridge_requires_review_ceiling_to_advance():
    policy = dataset._legacy_place_schema_compatible
    assert not policy(
        compatible_minimum=18,
        compatible_maximum=18,
        supported_schema_version=21,
        reviewed_through=20,
    )
    assert policy(
        compatible_minimum=18,
        compatible_maximum=18,
        supported_schema_version=21,
        reviewed_through=21,
    )


@pytest.mark.parametrize("supported", [17, 20, 21, 32, 33])
def test_schema_bridge_still_rejects_corrupt_files(tmp_path, supported):
    package = tmp_path / "package"
    build_package(package, compatible=(5, 16))
    places = package / "places.jsonl"
    places.write_bytes(places.read_bytes() + b"\n")
    with pytest.raises(CatalogError) as error:
        dataset.load_content(package, supported_schema_version=supported)
    assert error.value.code == PLACE_DATASET_INVALID
    assert "schema" not in error.value.message


def test_unchanged_composite_package_activates_and_reloads_at_schema21(tmp_path):
    from photo_search.geo import dataset as dataset_module

    root = tmp_path / "data"
    with CatalogService.open(data_root=root) as service:
        dataset_id, _ = install_composite(service.catalog, root, legacy_jinan=True)
        package = datasets.dataset_dir(root, dataset_id)
        original = snapshot(package)
        state = datasets.dataset_status(service.catalog.connection, data_root=root)
        assert state["state"] == "active" and state["resolver_available"]
        content = resolver.load_active_content(
            service.catalog.connection, data_root=root, cache={}
        )
        assert dataset_module.nearby_cities(
            content, 35.6762, 139.6917, max_candidates=5, max_distance_m=50000
        )[0]["display_name"] == "Tokyo"
        assert content.cn_admin.resolve(36.65, 117.0)["district"] == "relation/3"
        with pytest.raises(CatalogError) as error:
            dataset.load_content(package, supported_schema_version=17)
        assert error.value.code == PLACE_DATASET_INVALID
    with CatalogService.open(data_root=root) as reloaded:
        state = datasets.dataset_status(reloaded.catalog.connection, data_root=root)
        assert state["state"] == "active" and state["resolver_available"]
        content = resolver.load_active_content(
            reloaded.catalog.connection, data_root=root, cache={}
        )
        assert dataset_module.nearby_cities(
            content, 35.6762, 139.6917, max_candidates=5, max_distance_m=50000
        )[0]["display_name"] == "Tokyo"
        assert content.cn_admin.resolve(36.65, 117.0)["district"] == "relation/3"
    assert snapshot(package) == original
