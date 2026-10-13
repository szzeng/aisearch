"""Narrow adapters for user-requested Windows desktop actions on registered photos."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from photo_search.api.errors import ACCESS_DENIED, ApiError
from photo_search.api.files import ControlledLocation
from photo_search.catalog.image_policy import IMAGE_EXTENSIONS


def _system_open(path: Path) -> None:
    """Ask Windows to open this already-validated file with its registered association."""
    starter = getattr(os, "startfile", None)
    if starter is None:  # pragma: no cover - exercised only on non-Windows hosts
        raise OSError("Windows file associations are unavailable")
    starter(str(path))


def _explorer_reveal(path: Path) -> None:
    """Ask Explorer to select the file, passing it as one inert argument."""
    subprocess.Popen(["explorer.exe", f"/select,{path}"], shell=False)


def perform_local_action(location: ControlledLocation, action: str) -> None:
    """Request one of the two fixed Windows operations; never accept a program/path from UI."""
    path = location.absolute_path
    if action == "open":
        if path.suffix.casefold() not in IMAGE_EXTENSIONS:
            raise ApiError(ACCESS_DENIED, "this registered file type cannot be opened as an image")
        _system_open(path)
        return
    if action == "reveal":
        _explorer_reveal(path)
        return
    raise ApiError(ACCESS_DENIED, "this local action is not supported")
