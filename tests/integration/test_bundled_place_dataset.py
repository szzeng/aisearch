from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest
from p2_dataset_fixtures import build_package

from photo_search.catalog.db import Catalog
from photo_search.ingestion.service import CatalogService
from photo_search.places import bundled_dataset
from photo_search.places import datasets as datasets_module

DATASET_ID = "geonames-asia-cities500-v1-cn-c3c03b41ffe4cbe3"
PLACE_PIN = "release-resource.json"
OWNER = ".photosearch-place-dataset-installer.json"


def make_bundle(root: Path, *, compatible: tuple[int, int] = (18, 18)) -> Path:
    package = root / "place-datasets" / DATASET_ID
    manifest = build_package(
        package,
        dataset_id=DATASET_ID,
        previous_dataset_id="geonames-asia-cities500-v1",
        compatible=compatible,
    )
    (root / "place-datasets" / PLACE_PIN).write_text(
        json.dumps(
            {
                "dataset_id": DATASET_ID,
                "content_sha256": manifest["content_sha256"],
                "manifest_sha256": hashlib.sha256((package / "manifest.json").read_bytes()).hexdigest(),
                "file_count": len(manifest["files"]),
                "uncompressed_bytes": sum(int(item["byte_size"]) for item in manifest["files"]),
            }
        ),
        encoding="utf-8",
    )
    return root


def package_bytes(package: Path) -> dict[str, bytes]:
    return {path.relative_to(package).as_posix(): path.read_bytes() for path in package.rglob("*") if path.is_file()}


def enable_bundled_startup(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(root), raising=False)


def test_first_start_installs_and_activates_missing_predecessor_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = make_bundle(tmp_path / "bundle")
    original = package_bytes(bundle / "place-datasets" / DATASET_ID)
    enable_bundled_startup(monkeypatch, bundle)

    data_root = tmp_path / "PhotoSearchData"
    with CatalogService.open(data_root=data_root) as service:
        row = datasets_module.active_row(service.catalog.connection)
        assert row is not None
        assert row["dataset_id"] == DATASET_ID
        assert row["state"] == "ACTIVE"
        assert row["previous_dataset_id"] is None
        assert service.catalog.read_one("SELECT COUNT(*) FROM places")[0] == 5

    installed = data_root / "places" / "datasets" / DATASET_ID
    assert package_bytes(installed) == original
    assert not (installed / OWNER).exists()


def test_second_start_does_not_reinstall_or_change_active_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = make_bundle(tmp_path / "bundle")
    enable_bundled_startup(monkeypatch, bundle)
    data_root = tmp_path / "PhotoSearchData"
    with CatalogService.open(data_root=data_root) as service:
        before = datasets_module.active_row(service.catalog.connection)
        before_mtime = (data_root / "places" / "datasets" / DATASET_ID / "manifest.json").stat().st_mtime_ns
    with CatalogService.open(data_root=data_root) as service:
        after = datasets_module.active_row(service.catalog.connection)
        assert after["dataset_id"] == before["dataset_id"] == DATASET_ID
        assert service.catalog.read_one("SELECT revision FROM active_geo_dataset")[0] == 1
    assert (data_root / "places" / "datasets" / DATASET_ID / "manifest.json").stat().st_mtime_ns == before_mtime


@pytest.mark.parametrize("existing_state", ["active", "staged", "validated", "retained"])
def test_existing_registered_dataset_is_never_overwritten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_state: str) -> None:
    bundle = make_bundle(tmp_path / "bundle")
    enable_bundled_startup(monkeypatch, bundle)
    data_root = tmp_path / "PhotoSearchData"
    existing = data_root / "places" / "datasets" / "existing-user-package"
    build_package(existing, dataset_id="existing-user-package")
    with Catalog.open(data_root=data_root) as catalog:
        row = datasets_module.stage_package(catalog, existing)
        if existing_state != "staged":
            datasets_module.validate_dataset(catalog, row["dataset_id"])
        if existing_state == "active":
            datasets_module.activate_dataset(catalog, row["dataset_id"])
        elif existing_state == "retained":
            catalog.connection.execute(
                "UPDATE geo_datasets SET state = 'RETAINED', retained_at = ? WHERE dataset_id = ?",
                (catalog.now_ms(), row["dataset_id"]),
            )
    existing_before = package_bytes(existing)

    with CatalogService.open(data_root=data_root) as service:
        assert service.catalog.read_one("SELECT dataset_id FROM active_geo_dataset")[0] == (
            "existing-user-package" if existing_state == "active" else None
        )
        assert service.catalog.read_one("SELECT COUNT(*) FROM geo_datasets")[0] == 1
    assert package_bytes(existing) == existing_before
    assert not (data_root / "places" / "datasets" / DATASET_ID).exists()


def test_multiple_unregistered_packages_are_preserved_for_manual_review(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    bundle = make_bundle(tmp_path / "bundle")
    data_root = tmp_path / "PhotoSearchData"
    package_root = data_root / "places" / "datasets"
    first = package_root / "user-package-a"
    second = package_root / "user-package-b"
    build_package(first, dataset_id="user-package-a")
    build_package(second, dataset_id="user-package-b")
    before = package_bytes(first), package_bytes(second)
    with Catalog.open(data_root=data_root) as catalog:
        assert not bundled_dataset.install_bundled_place_dataset(catalog, bundle_root=bundle / "place-datasets")
        assert catalog.read_one("SELECT 1 FROM geo_datasets") is None
    assert (package_bytes(first), package_bytes(second)) == before
    assert "need review" in caplog.text
    assert not (package_root / DATASET_ID).exists()


def test_interrupted_owned_copy_is_cleaned_and_retried(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path / "bundle")
    data_root = tmp_path / "PhotoSearchData"
    package_root = data_root / "places" / "datasets"
    package_root.mkdir(parents=True)
    temporary = package_root / f".photosearch-install-{DATASET_ID}"
    temporary.mkdir()
    (temporary / "partial").write_bytes(b"interrupted")
    owner = data_root / "places" / ".bundled-place-installer"
    owner.mkdir()
    (owner / f"{DATASET_ID}.json").write_text(
        json.dumps({"dataset_id": DATASET_ID, "installer": "photo-search-bundled-place-dataset-v1"}),
        encoding="utf-8",
    )
    with Catalog.open(data_root=data_root) as catalog:
        assert bundled_dataset.install_bundled_place_dataset(catalog, bundle_root=bundle / "place-datasets")
        assert catalog.read_one("SELECT state FROM geo_datasets WHERE dataset_id = ?", (DATASET_ID,))[0] == "ACTIVE"
    assert not temporary.exists()


def test_marker_only_crash_residue_retries_install_in_the_same_start(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path / "bundle")
    data_root = tmp_path / "PhotoSearchData"
    owner = data_root / "places" / ".bundled-place-installer"
    owner.mkdir(parents=True)
    marker = owner / f"{DATASET_ID}.json"
    marker.write_text(
        json.dumps({"dataset_id": DATASET_ID, "installer": "photo-search-bundled-place-dataset-v1"}),
        encoding="utf-8",
    )
    with Catalog.open(data_root=data_root) as catalog:
        assert bundled_dataset.install_bundled_place_dataset(catalog, bundle_root=bundle / "place-datasets")
        assert catalog.read_one("SELECT state FROM geo_datasets WHERE dataset_id = ?", (DATASET_ID,))[0] == "ACTIVE"
    assert not marker.exists()


def test_unowned_owner_marker_collision_is_preserved(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path / "bundle")
    data_root = tmp_path / "PhotoSearchData"
    owner = data_root / "places" / ".bundled-place-installer"
    owner.mkdir(parents=True)
    marker = owner / f"{DATASET_ID}.json"
    marker.write_text("user-owned marker", encoding="utf-8")
    original = marker.read_bytes()
    with Catalog.open(data_root=data_root) as catalog:
        assert not bundled_dataset.install_bundled_place_dataset(catalog, bundle_root=bundle / "place-datasets")
        assert catalog.read_one("SELECT 1 FROM geo_datasets") is None
    assert marker.read_bytes() == original
    assert not (data_root / "places" / "datasets" / DATASET_ID).exists()


def test_owner_marker_write_failure_is_cleaned_and_next_start_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = make_bundle(tmp_path / "bundle")
    data_root = tmp_path / "PhotoSearchData"
    original_dump = json.dump

    def fail_after_marker_creation(payload, stream, **kwargs):
        if stream.name.endswith(f"{DATASET_ID}.json"):
            stream.write("{")
            raise OSError("simulated marker write failure")
        return original_dump(payload, stream, **kwargs)

    with Catalog.open(data_root=data_root) as catalog:
        monkeypatch.setattr(bundled_dataset.json, "dump", fail_after_marker_creation)
        assert not bundled_dataset.install_bundled_place_dataset(catalog, bundle_root=bundle / "place-datasets")
        marker = data_root / "places" / ".bundled-place-installer" / f"{DATASET_ID}.json"
        assert not marker.exists()
        assert catalog.read_one("SELECT 1 FROM geo_datasets") is None
        monkeypatch.setattr(bundled_dataset.json, "dump", original_dump)
        assert bundled_dataset.install_bundled_place_dataset(catalog, bundle_root=bundle / "place-datasets")


def test_copy_failure_leaves_no_final_package_and_retry_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = make_bundle(tmp_path / "bundle")
    data_root = tmp_path / "PhotoSearchData"
    original_copy = shutil.copyfile
    calls = 0

    def interrupt_copy(source: str | Path, destination: str | Path, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated interrupted copy")
        return original_copy(source, destination, *args, **kwargs)

    with Catalog.open(data_root=data_root) as catalog:
        monkeypatch.setattr(bundled_dataset.shutil, "copyfile", interrupt_copy)
        assert not bundled_dataset.install_bundled_place_dataset(catalog, bundle_root=bundle / "place-datasets")
        assert catalog.read_one("SELECT 1 FROM geo_datasets") is None
        assert not (data_root / "places" / "datasets" / DATASET_ID).exists()
        monkeypatch.setattr(bundled_dataset.shutil, "copyfile", original_copy)
        assert bundled_dataset.install_bundled_place_dataset(catalog, bundle_root=bundle / "place-datasets")


@pytest.mark.parametrize("defect", ["tampered", "schema", "manifest"])
def test_invalid_or_incompatible_bundle_is_refused_without_registration(tmp_path: Path, defect: str) -> None:
    bundle = make_bundle(tmp_path / "bundle", compatible=(1, 1) if defect == "schema" else (18, 18))
    if defect == "tampered":
        path = bundle / "place-datasets" / DATASET_ID / "places.jsonl"
        path.write_text(path.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
    elif defect == "manifest":
        path = bundle / "place-datasets" / DATASET_ID / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["source"]["attribution"] = "modified after pinning"
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    data_root = tmp_path / "PhotoSearchData"
    with Catalog.open(data_root=data_root) as catalog:
        assert not bundled_dataset.install_bundled_place_dataset(catalog, bundle_root=bundle / "place-datasets")
        assert catalog.read_one("SELECT 1 FROM geo_datasets") is None
        assert not (data_root / "places" / "datasets" / DATASET_ID).exists()


def test_unowned_temporary_directory_is_preserved(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path / "bundle")
    data_root = tmp_path / "PhotoSearchData"
    package_root = data_root / "places" / "datasets"
    package_root.mkdir(parents=True)
    temporary = package_root / f".photosearch-install-{DATASET_ID}"
    temporary.mkdir()
    sentinel = temporary / "user-file.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with Catalog.open(data_root=data_root) as catalog:
        assert not bundled_dataset.install_bundled_place_dataset(catalog, bundle_root=bundle / "place-datasets")
    assert sentinel.read_text(encoding="utf-8") == "keep"
