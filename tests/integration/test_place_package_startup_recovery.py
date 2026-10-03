"""A retained reference package survives catalog/vector reset on application restart."""

from __future__ import annotations

import asyncio
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from p2_dataset_fixtures import MINI_PLACES, build_package

from photo_search.api.settings import ApiSettings
from photo_search.app import create_app
from photo_search.catalog.db import Catalog
from photo_search.config import catalog_dir, place_datasets_dir, vectors_dir
from photo_search.ingestion.service import CatalogService
from photo_search.places import datasets


def stored_package(root: Path, dataset_id: str = "geo-mini-cn-1", **options) -> Path:
    target = place_datasets_dir(root) / dataset_id
    build_package(target, dataset_id=dataset_id, **options)
    return target


def package_snapshot(package: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in package.iterdir()
        if path.is_file()
    }


def test_reset_catalog_and_vectors_then_api_startup_restores_package(tmp_path: Path) -> None:
    root = tmp_path / "data"
    package = stored_package(root)
    original = package_snapshot(package)
    with Catalog.open(data_root=root) as catalog:
        staged = datasets.stage_package(catalog, package)
        datasets.validate_dataset(catalog, staged["dataset_id"])
        datasets.activate_dataset(catalog, staged["dataset_id"])
    vectors_dir(root).mkdir()
    (vectors_dir(root) / "synthetic-index").write_text("derived", encoding="utf-8")
    for target in (catalog_dir(root), vectors_dir(root)):
        assert target.resolve().is_relative_to(tmp_path.resolve())
        shutil.rmtree(target)

    app = create_app(settings=ApiSettings(data_root=root, session_token="synthetic-recovery"))
    # App construction remains free of database writes; lazy service opening restores it.
    assert not catalog_dir(root).exists()

    async def read_state() -> dict:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            session = await client.get("/api/v1/session", params={"token": "synthetic-recovery"})
            assert session.status_code == 200
            response = await client.get("/api/v1/places/datasets")
            assert response.status_code == 200, response.text
            return response.json()

    try:
        state = asyncio.run(read_state())
        assert state["state"] == "active"
        assert state["resolver_available"] is True
        assert state["active_dataset"]["dataset_id"] == "geo-mini-cn-1"
        assert state["active_dataset"]["problems"] == []
        assert state["suggestion_revision"] == 0
    finally:
        app.state.api.close()
    assert package_snapshot(package) == original
    assert not vectors_dir(root).exists()
    with CatalogService.open(data_root=root) as service:
        assert service.catalog.read_one("SELECT revision FROM active_geo_dataset")[0] == 1
        assert service.catalog.read_one("SELECT COUNT(*) FROM places")[0] == len(MINI_PLACES)
        assert service.catalog.read_one("SELECT COUNT(*) FROM assets")[0] == 0
    assert package_snapshot(package) == original


@pytest.mark.parametrize(
    "defect", ["missing", "tampered", "incompatible", "wrong-directory", "missing-predecessor"]
)
def test_defective_package_never_becomes_active(tmp_path: Path, defect: str) -> None:
    root = tmp_path / "data"
    options = {}
    if defect == "incompatible":
        options["compatible"] = (1, 1)
    elif defect == "missing-predecessor":
        options["previous_dataset_id"] = "geo-mini-cn-missing"
    package = stored_package(root, **options)
    if defect == "missing":
        (package / "places.jsonl").unlink()
    elif defect == "tampered":
        path = package / "places.jsonl"
        path.write_bytes(path.read_bytes().replace("杭州".encode(), "杭洲".encode()))
    elif defect == "wrong-directory":
        renamed = package.with_name("wrong-name")
        assert renamed.resolve().is_relative_to(tmp_path.resolve())
        package.rename(renamed)
    with CatalogService.open(data_root=root) as service:
        assert datasets.active_row(service.catalog.connection) is None
        assert service.catalog.read_one("SELECT COUNT(*) FROM geo_datasets")[0] == 0
        assert service.catalog.read_one("SELECT COUNT(*) FROM places")[0] == 0


def test_multiple_surviving_packages_require_explicit_selection(tmp_path: Path) -> None:
    root = tmp_path / "data"
    stored_package(root)
    stored_package(root, "geo-mini-cn-2")
    with CatalogService.open(data_root=root) as service:
        assert datasets.active_row(service.catalog.connection) is None
        assert service.catalog.read_one("SELECT COUNT(*) FROM geo_datasets")[0] == 0


def test_existing_inactive_registry_is_not_automatically_activated(tmp_path: Path) -> None:
    root = tmp_path / "data"
    package = stored_package(root)
    with Catalog.open(data_root=root) as catalog:
        staged = datasets.stage_package(catalog, package)
        datasets.validate_dataset(catalog, staged["dataset_id"])
    with CatalogService.open(data_root=root) as service:
        assert datasets.active_row(service.catalog.connection) is None
        assert service.catalog.read_one("SELECT state FROM geo_datasets")[0] == "VALIDATED"


def test_recovery_failure_rolls_back_registration_pointer_and_places(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data"
    package = stored_package(root)
    original = package_snapshot(package)

    def fail_materialization(*args, **kwargs):
        raise RuntimeError("injected after the active pointer changes")

    with Catalog.open(data_root=root) as catalog:
        with monkeypatch.context() as patch:
            patch.setattr(datasets, "materialize_dataset_places", fail_materialization)
            with pytest.raises(RuntimeError, match="injected"):
                datasets.restore_surviving_dataset(catalog)
        assert catalog.read_one("SELECT COUNT(*) FROM geo_datasets")[0] == 0
        assert catalog.read_one("SELECT dataset_id FROM active_geo_dataset")[0] is None
        assert catalog.read_one("SELECT revision FROM active_geo_dataset")[0] == 0
        assert catalog.read_one("SELECT COUNT(*) FROM places")[0] == 0
    with CatalogService.open(data_root=root) as service:
        assert datasets.active_row(service.catalog.connection)["dataset_id"] == "geo-mini-cn-1"
    assert package_snapshot(package) == original


def test_concurrent_application_openers_recover_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data"
    stored_package(root)
    with Catalog.open(data_root=root):
        pass
    verified = threading.Barrier(2)
    load_content = datasets.dataset_module.load_content

    def simultaneous_verify(*args, **kwargs):
        content = load_content(*args, **kwargs)
        verified.wait(timeout=10)
        return content

    monkeypatch.setattr(datasets.dataset_module, "load_content", simultaneous_verify)

    def open_app() -> str:
        with CatalogService.open(data_root=root) as service:
            return str(datasets.active_row(service.catalog.connection)["dataset_id"])

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: open_app(), range(2)))
    assert results == ["geo-mini-cn-1", "geo-mini-cn-1"]
    with Catalog.open(data_root=root) as catalog:
        assert catalog.read_one("SELECT COUNT(*) FROM geo_datasets")[0] == 1
        assert catalog.read_one("SELECT revision FROM active_geo_dataset")[0] == 1
        assert catalog.read_one("SELECT COUNT(*) FROM places")[0] == len(MINI_PLACES)
