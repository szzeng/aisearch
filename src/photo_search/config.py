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
