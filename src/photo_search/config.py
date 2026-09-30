"""Application data root, catalog and backup path resolution.

Application data lives outside the install directory: LocalAppData on Windows and
Library/Application Support on macOS. PHOTO_SEARCH_DATA_ROOT and an explicit CLI
path override the platform default.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DATA_ROOT_ENV = "PHOTO_SEARCH_DATA_ROOT"
DATA_ROOT_DIRNAME = "PhotoSearchData"
CATALOG_DIRNAME = "catalog"
CATALOG_FILENAME = "catalog.db"
BACKUPS_DIRNAME = "backups"
#: Architecture §11.1: derived media lives in rebuildable directories of the data root,
#: never inside a source root and never inside the install directory.
THUMBNAILS_DIRNAME = "thumbnails"
PREVIEWS_DIRNAME = "previews"
CACHE_DIRNAME = "cache"
MEDIA_CACHE_DIRNAME = "media"
#: Architecture §11.1: run-time files (including the one-time local session credential)
#: live under the data root, never inside an installation directory or a source root.
RUNTIME_DIRNAME = "runtime"
SESSION_CREDENTIAL_FILENAME = "session.json"
ARTIFACTS_DIRNAME = "artifacts"
VECTORS_DIRNAME = "vectors"
#: ADR-0005 §8: offline name-dataset packages live in a read-only directory inside the
#: application data root. Nothing else may be written there, and the install directory
#: never carries a package.
PLACES_DIRNAME = "places"
DATASETS_DIRNAME = "datasets"


def default_data_root() -> Path:
    """Return the user data root, honouring the diagnostic override."""
    override = os.environ.get(DATA_ROOT_ENV)
    if override:
        return Path(os.path.abspath(override))
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / DATA_ROOT_DIRNAME
    profile = os.environ.get("USERPROFILE")
    candidate = Path(profile) / "AppData" / "Local" if profile else None
    if candidate is None or not candidate.is_dir():
        candidate = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    # The packaged desktop app aliases LocalAppData inside its LocalCache directory, so
    # the canonical path is recorded instead of the alias.
    return Path(os.path.realpath(candidate)) / DATA_ROOT_DIRNAME


def resolve_data_root(data_root: str | Path | None = None) -> Path:
    return Path(data_root) if data_root is not None else default_data_root()


def catalog_dir(data_root: str | Path | None = None) -> Path:
    return resolve_data_root(data_root) / CATALOG_DIRNAME


def catalog_db_path(data_root: str | Path | None = None) -> Path:
    return catalog_dir(data_root) / CATALOG_FILENAME


def backups_dir(data_root: str | Path | None = None) -> Path:
    return resolve_data_root(data_root) / BACKUPS_DIRNAME / CATALOG_DIRNAME


def thumbnails_dir(data_root: str | Path | None = None) -> Path:
    """Root of the rebuildable grid-thumbnail cache (architecture §5.4, §11.1)."""
    return resolve_data_root(data_root) / THUMBNAILS_DIRNAME


def previews_dir(data_root: str | Path | None = None) -> Path:
    """Root of the rebuildable detail-preview cache; may be deleted on its own."""
    return resolve_data_root(data_root) / PREVIEWS_DIRNAME


def media_cache_dir(data_root: str | Path | None = None) -> Path:
    """Structured, rebuildable media bookkeeping that is not a media artifact itself."""
    return resolve_data_root(data_root) / CACHE_DIRNAME / MEDIA_CACHE_DIRNAME


def runtime_dir(data_root: str | Path | None = None) -> Path:
    """Run-time state of the local application (architecture §11.1)."""
    return resolve_data_root(data_root) / RUNTIME_DIRNAME


def artifacts_dir(data_root: str | Path | None = None) -> Path:
    """Durable processor artifacts, outside both the install and source roots."""
    return resolve_data_root(data_root) / ARTIFACTS_DIRNAME


def vectors_dir(data_root: str | Path | None = None) -> Path:
    """Derived LanceDB generations under the application data root."""
    return resolve_data_root(data_root) / VECTORS_DIRNAME


def place_datasets_dir(data_root: str | Path | None = None) -> Path:
    """Root of the imported, read-only offline name-dataset packages."""
    return resolve_data_root(data_root) / PLACES_DIRNAME / DATASETS_DIRNAME


def session_credential_path(data_root: str | Path | None = None) -> Path:
    """Where the launcher records the one-time local session credential."""
    return runtime_dir(data_root) / SESSION_CREDENTIAL_FILENAME
