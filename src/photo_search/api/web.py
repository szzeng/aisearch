"""The packaged front-end shell and its static assets (architecture §10.1, §10.3).

The page is served straight out of package data (``photo_search/web/``), so the frozen
onedir bundle ships it unchanged. Neither the shell nor its assets need a session: they
contain no user data and no absolute local path. Every piece of personal data still comes
from the session-protected, read-only ``/api/v1`` endpoints, so this module cannot become
a second way into the library.

A request can only ever name a file *inside* the web package: the path is split on ``/``,
each segment has to match a strict allow-list, and the traversal is resolved through
``importlib.resources`` rather than through the filesystem.
"""

from __future__ import annotations

import ntpath
import re
from importlib.resources import files
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Response

from photo_search.api.errors import WEB_ASSET_NOT_FOUND, ApiError

WEB_PACKAGE = "photo_search.web"
INDEX_NAME = "index.html"
#: The place-curation page (P2-03C). It lives beside the shell, is served from the same
#: package data and carries no user data of its own.
PLACES_NAME = "places.html"

#: The shell must always be revalidated: it names the assets of this exact build.
SHELL_CACHE_CONTROL = "no-cache"
#: Scripts and styles change only with a build, so a short cache is enough.
ASSET_CACHE_CONTROL = "max-age=300"

#: One path segment: a plain file name, never ``.``/``..``, never a separator.
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_MEDIA_TYPES = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ico": "image/x-icon",
    ".json": "application/json",
    ".webmanifest": "application/manifest+json",
    ".woff2": "font/woff2",
    ".txt": "text/plain",
}

router = APIRouter(tags=["front-end"], include_in_schema=False)


def web_root() -> Any:
    """The traversable of the packaged web directory.

    ``importlib.resources`` is the supported way to reach package data, including inside a
    PyInstaller bundle. The filesystem fallback only exists for the (unlikely) case that a
    loader does not implement a resource reader at all.
    """
    try:
        return files(WEB_PACKAGE)
    except (ImportError, ModuleNotFoundError, TypeError):  # pragma: no cover - defensive
        return Path(__file__).resolve().parents[1] / "web"


def media_type_for_name(name: str) -> str:
    """A conservative media type; an unknown extension is never guessed."""
    suffix = ntpath.splitext(str(name))[1].lower()
    return _MEDIA_TYPES.get(suffix, "application/octet-stream")


def read_static(relative: str) -> bytes | None:
    """Read one packaged asset, or ``None`` when the path is not a legal, existing file.

    ``relative`` is a ``/``-separated path below the web package. ``..``, absolute paths,
    drive letters, UNC spellings, backslashes and percent-decoded separators all fail the
    per-segment allow-list before anything touches the filesystem.
    """
    parts = [part for part in str(relative).split("/") if part != ""]
    if not parts:
        return None
    for part in parts:
        if not _SAFE_SEGMENT.match(part) or part in (".", ".."):
            return None
    node = web_root()
    for part in parts:
        node = node.joinpath(part)
    try:
        if not node.is_file():
            return None
        return node.read_bytes()
    except OSError:  # pragma: no cover - defensive: unreadable package data
        return None


def _serve(relative: str, *, cache_control: str) -> Response:
    payload = read_static(relative)
    if payload is None:
        raise ApiError(
            WEB_ASSET_NOT_FOUND,
            "this build does not ship that front-end asset",
            details={"path": str(relative)},
        )
    return Response(
        content=payload,
        media_type=media_type_for_name(relative),
        headers={"Cache-Control": cache_control},
    )


@router.get("/")
async def index() -> Response:
    """The application shell. No session, no user data, no local path."""
    return _serve(INDEX_NAME, cache_control=SHELL_CACHE_CONTROL)


@router.get("/places")
async def places_page() -> Response:
    """The place-curation page. It never shadows ``/api/v1/places``.

    The business API keeps its own prefix, so the two paths can coexist: this route only
    answers the bare ``/places`` document, and every piece of place data still comes from
    the session-protected ``/api/v1`` endpoints.
    """
    return _serve(PLACES_NAME, cache_control=SHELL_CACHE_CONTROL)


@router.get("/web/{path:path}")
async def static_asset(path: str) -> Response:
    """One packaged script, style or image below ``/web/``."""
    # Both pages name assets of this exact build, so they are always revalidated; a
    # script or stylesheet only changes with a build.
    cache_control = (
        SHELL_CACHE_CONTROL
        if path in (INDEX_NAME, PLACES_NAME)
        else ASSET_CACHE_CONTROL
    )
    return _serve(path, cache_control=cache_control)


__all__ = [
    "ASSET_CACHE_CONTROL",
    "INDEX_NAME",
    "PLACES_NAME",
    "SHELL_CACHE_CONTROL",
    "media_type_for_name",
    "read_static",
    "router",
    "web_root",
]
