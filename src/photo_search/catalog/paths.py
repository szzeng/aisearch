"""Windows path comparison keys (ADR-0002 §4).

Display spellings are preserved in ``root_path`` / ``relative_path``; identity and
uniqueness only ever use the comparison keys built here.
"""

from __future__ import annotations

import ntpath
import os
from pathlib import Path

from photo_search.catalog.errors import INVALID_PATH, CatalogError


def normalize_root(path: str | Path) -> str:
    """Return an absolute path using the host filesystem separators."""
    text = str(path)
    if not text.strip():
        raise CatalogError(INVALID_PATH, "source root path must not be empty")
    absolute = os.path.abspath(text)
    if os.name != "nt":
        return os.path.normpath(absolute)
    normalized = ntpath.normpath(absolute)
    drive, tail = ntpath.splitdrive(normalized)
    if tail in ("\\", "/") and drive:
        return drive + "\\"
    return normalized


def root_key(path: str | Path) -> str:
    """Case-insensitive comparison key for an absolute source root path."""
    return ntpath.normcase(normalize_root(path))


def is_same_or_ancestor(ancestor: str | Path, descendant: str | Path) -> bool:
    """True when ``ancestor`` is ``descendant`` itself or one of its path ancestors."""
    outer = root_key(ancestor)
    inner = root_key(descendant)
    if outer == inner:
        return True
    prefix = outer if outer.endswith("\\") else outer + "\\"
    return inner.startswith(prefix)


def path_key(relative_path: str) -> str:
    """Build the frozen comparison key for a source-relative path.

    The key never contains a drive or root path, never follows reparse points and is not
    Unicode-normalised, so a source root can be remapped later without rewriting keys.
    """
    if not isinstance(relative_path, str) or not relative_path:
        raise CatalogError(INVALID_PATH, "relative path must not be empty")
    if "\x00" in relative_path:
        raise CatalogError(INVALID_PATH, "relative path must not contain NUL")
    text = relative_path.replace("/", "\\")
    if ntpath.isabs(text) or text.startswith("\\"):
        raise CatalogError(
            INVALID_PATH,
            "relative path must not be absolute, UNC or rooted",
            details={"relative_path": relative_path},
        )
    if ntpath.splitdrive(text)[0]:
        raise CatalogError(
            INVALID_PATH,
            "relative path must not contain a drive or UNC share",
            details={"relative_path": relative_path},
        )
    normalized = ntpath.normpath(text)
    if normalized in (".", "..") or normalized.startswith("..\\") or ntpath.isabs(normalized):
        raise CatalogError(
            INVALID_PATH,
            "relative path escapes its source root",
            details={"relative_path": relative_path},
        )
    if ntpath.splitdrive(normalized)[0]:
        raise CatalogError(
            INVALID_PATH,
            "relative path must not contain a drive or UNC share",
            details={"relative_path": relative_path},
        )
    return ntpath.normcase(normalized)
