"""Application data root, catalog and backup path resolution.

The data root semantics follow the P0-04 verified behaviour: application data lives
outside the install directory, normally under ``%LOCALAPPDATA%\\PhotoSearchData``, and
under the packaged desktop app's LocalCache view when the app redirects
``LOCALAPPDATA``. ``PHOTO_SEARCH_DATA_ROOT`` is a test/diagnostic override only; it
never changes where a normal run keeps its data.
"""

from __future__ import annotations

import os
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


def default_data_root() -> Path:
    """Return the user data root, honouring the diagnostic override."""
    override = os.environ.get(DATA_ROOT_ENV)
    if override:
        return Path(os.path.abspath(override))
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


def session_credential_path(data_root: str | Path | None = None) -> Path:
    """Where the launcher records the one-time local session credential."""
    return runtime_dir(data_root) / SESSION_CREDENTIAL_FILENAME
