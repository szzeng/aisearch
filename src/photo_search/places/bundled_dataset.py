"""Install the pinned offline place dataset shipped with a frozen Windows bundle."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from photo_search.catalog import schema as schema_module
from photo_search.catalog.db import Catalog
from photo_search.config import place_datasets_dir
from photo_search.geo import dataset as dataset_module
from photo_search.places import datasets as datasets_module

logger = logging.getLogger(__name__)
OWNER_MARKER = ".photosearch-place-dataset-installer.json"


def bundled_place_dataset_root() -> Path | None:
    """Return the frozen bundle resource root; source runs never auto-install data."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(getattr(sys, "_MEIPASS")) / "place-datasets"


def _read_pin(root: Path) -> dict[str, Any]:
    document = json.loads((root / "release-resource.json").read_text(encoding="utf-8"))
    return validate_release_pin(document)


def validate_release_pin(document: dict[str, Any]) -> dict[str, Any]:
    dataset_id = document.get("dataset_id")
    digest = document.get("content_sha256")
    if (
        not isinstance(dataset_id, str)
        or not dataset_id
        or Path(dataset_id).name != dataset_id
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
        or not isinstance(document.get("manifest_sha256"), str)
        or len(str(document["manifest_sha256"])) != 64
        or any(char not in "0123456789abcdef" for char in str(document["manifest_sha256"]))
    ):
        raise ValueError("the bundled place dataset release pin is incomplete")
    return document


def validate_pinned_package(source: Path, pin: dict[str, Any]) -> Any:
    """Fully validate package bytes and the immutable release identity pin."""
    content = dataset_module.load_content(
        source, supported_schema_version=int(schema_module.SUPPORTED_SCHEMA_VERSION)
    )
    manifest_digest = hashlib.sha256((source / dataset_module.MANIFEST_FILENAME).read_bytes()).hexdigest()
    if (
        content.dataset_id != pin["dataset_id"]
        or content.manifest["content_sha256"] != pin["content_sha256"]
        or manifest_digest != pin["manifest_sha256"]
        or len(content.files) != int(pin["file_count"])
        or sum(item.byte_size for item in content.files) != int(pin["uncompressed_bytes"])
    ):
        raise ValueError("the bundled place dataset does not match its release resource pin")
    allowed = {dataset_module.MANIFEST_FILENAME, *(item.relative_path for item in content.files)}
    actual: set[str] = set()
    for path in source.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise ValueError("the bundled place dataset contains a link")
        if path.is_file():
            actual.add(path.relative_to(source).as_posix())
    if actual != allowed:
        raise ValueError("the bundled place dataset contains missing or unlisted files")
    return content


def _owner_record(dataset_id: str) -> dict[str, str]:
    return {"dataset_id": dataset_id, "installer": "photo-search-bundled-place-dataset-v1"}


def _owned_temporary(path: Path, marker: Path, dataset_id: str) -> bool:
    if path.is_symlink() or not path.is_dir() or path.is_junction() or marker.is_symlink() or marker.is_junction():
        return False
    try:
        return json.loads(marker.read_text(encoding="utf-8")) == _owner_record(dataset_id)
    except (OSError, json.JSONDecodeError):
        return False


def _copy_verified_tree(source: Path, target: Path, content: Any) -> None:
    for item in content.files:
        origin = source / item.relative_path
        destination = target / item.relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, destination)
    shutil.copyfile(source / dataset_module.MANIFEST_FILENAME, target / dataset_module.MANIFEST_FILENAME)


def install_bundled_place_dataset(catalog: Catalog, *, bundle_root: Path | None = None) -> bool:
    """Install once into an empty registry/directory; recover and activate via existing logic."""
    if catalog.read_one("SELECT 1 FROM geo_datasets LIMIT 1") is not None:
        return False
    root = bundle_root if bundle_root is not None else bundled_place_dataset_root()
    if root is None:
        return False
    try:
        pin = _read_pin(root)
        target_root = place_datasets_dir(catalog.data_root)
        target_root.mkdir(parents=True, exist_ok=True)
        if target_root.is_symlink() or target_root.is_junction():
            raise ValueError("the user place dataset directory is a reparse point")
        places_root = target_root.parent
        data_root = Path(catalog.data_root).resolve()
        if (
            places_root.is_symlink()
            or places_root.is_junction()
            or not places_root.resolve().is_relative_to(data_root)
        ):
            raise ValueError("the user places directory escapes the selected data root")
        dataset_id = str(pin["dataset_id"])
        final = target_root / dataset_id
        temporary = target_root / f".photosearch-install-{dataset_id}"
        owner_dir = places_root / ".bundled-place-installer"
        owner_dir.mkdir(parents=True, exist_ok=True)
        if owner_dir.is_symlink() or owner_dir.is_junction() or not owner_dir.resolve().is_relative_to(places_root.resolve()):
            raise ValueError("the bundled dataset installer state directory is unsafe")
        marker = owner_dir / f"{dataset_id}.json"
        if temporary.exists():
            if not _owned_temporary(temporary, marker, dataset_id):
                logger.warning("bundled place install found an unowned temporary path; leaving it untouched")
                return False
            shutil.rmtree(temporary)
            marker.unlink(missing_ok=True)
        elif marker.is_symlink() or marker.is_junction():
            logger.warning("bundled place install found an unsafe owner marker; leaving it untouched")
            return False
        elif marker.exists():
            try:
                marker_record = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.warning("bundled place install found an unrecognized owner marker; leaving it untouched")
                return False
            if marker_record != _owner_record(dataset_id):
                logger.warning("bundled place install found a marker not owned by this installer; leaving it untouched")
                return False
            # A crash may happen after writing the ownership marker but before
            # creating the package staging directory. That marker is our only
            # durable evidence that this reserved path belongs to the installer.
            marker.unlink()

        entries = list(target_root.iterdir())
        if final.exists() or final.is_symlink() or entries:
            # Existing packages, conflicts, and unrecognized files are user state.
            logger.warning(
                "bundled place dataset was not installed because existing place package files "
                "need review; existing data was left unchanged"
            )
            datasets_module.restore_surviving_dataset(catalog)
            return False

        source = root / dataset_id
        content = validate_pinned_package(source, pin)

        marker_created_by_this_attempt = False
        temporary_created_by_this_attempt = False
        try:
            with marker.open("x", encoding="utf-8") as stream:
                marker_created_by_this_attempt = True
                json.dump(_owner_record(content.dataset_id), stream, sort_keys=True)
            temporary.mkdir()
            temporary_created_by_this_attempt = True
            _copy_verified_tree(source, temporary, content)
            validate_pinned_package(temporary, pin)
            if final.exists():
                marker.unlink(missing_ok=True)
                return False
            os.rename(temporary, final)
            temporary_created_by_this_attempt = False
            marker.unlink(missing_ok=True)
            datasets_module.restore_surviving_dataset(catalog)
            if catalog.read_one("SELECT 1 FROM geo_datasets WHERE dataset_id = ? AND state = 'ACTIVE'", (content.dataset_id,)) is None:
                raise RuntimeError("the bundled place dataset was published but could not be activated")
            return True
        except Exception:
            if (
                temporary_created_by_this_attempt
                and temporary.exists()
                and _owned_temporary(temporary, marker, content.dataset_id)
            ):
                shutil.rmtree(temporary)
            if marker_created_by_this_attempt:
                marker.unlink(missing_ok=True)
            raise
    except Exception:  # optional package failures must not prevent ordinary browsing
        logger.exception("bundled offline place dataset installation failed; place resolver remains unavailable")
        return False
