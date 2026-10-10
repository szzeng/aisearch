"""Controlled read access to a user's originals (architecture §5.4, §10.3).

The HTTP layer never accepts a path: it accepts an asset id and resolves the *registered*
positions of that asset. Every candidate must survive four independent checks before a
single byte is served:

1. the stored comparison key must itself be a legal source-relative path (``path_key``);
2. the rebuilt absolute path must still be inside the registered source root
   (``is_same_or_ancestor`` on the same case-insensitive key the catalog uses);
3. no component below the root may be a reparse point, and the physical target
   (``realpath``) must still be inside the source root;
4. ``size``/``mtime_ns`` must match the catalog observation, checked with the same guard
   the P1-03 media pipeline uses before and after a decode.

Any failure is reported as a structured error. A caller never receives a best-effort read
of a path that did not pass all four checks.
"""

from __future__ import annotations

import asyncio
import ntpath
import os
import stat
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from photo_search.api.errors import (
    ACCESS_DENIED,
    INVALID_RANGE,
    RANGE_NOT_SATISFIABLE,
    SOURCE_FILE_MISSING,
    SOURCE_OFFLINE,
    ApiError,
)
from photo_search.catalog.errors import FILE_READ_ERROR, INPUT_UNSTABLE, CatalogError
from photo_search.catalog.paths import is_same_or_ancestor, path_key, root_key
from photo_search.media.pipeline import require_unchanged

# Streaming chunk for original bytes; small enough to keep a local UI responsive.
READ_CHUNK_BYTES = 256 * 1024
REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".jpe": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
}

_LOCATION_SQL = """
SELECT l.location_id, l.source_id, l.relative_path, l.path_key, l.size, l.mtime_ns,
       l.availability, s.root_path, s.status AS source_status
  FROM file_locations AS l
  JOIN source_roots AS s ON s.source_id = l.source_id
 WHERE l.asset_id = ?
 ORDER BY CASE l.availability WHEN 'PRESENT' THEN 0 ELSE 1 END, l.location_id
"""


@dataclass(frozen=True)
class ControlledLocation:
    """A registered position that passed every boundary check right now."""

    location_id: int
    source_id: int
    relative_path: str
    root_path: str
    absolute_path: Path
    size: int
    mtime_ns: int


def list_asset_locations(catalog: Any, asset_id: int) -> list[Any]:
    """Every registered position of an asset, ``PRESENT`` first in stable order."""
    return list(catalog.read_all(_LOCATION_SQL, (int(asset_id),)))


def resolve_present_location(catalog: Any, asset_id: int) -> ControlledLocation:
    """Resolve the first usable ``PRESENT`` position, or raise a structured error."""
    rows = list_asset_locations(catalog, asset_id)
    present = [row for row in rows if str(row["availability"]) == "PRESENT"]
    if not present:
        raise ApiError(
            SOURCE_FILE_MISSING,
            "no registered position of this asset is currently marked present",
            details={"asset_id": int(asset_id), "positions": len(rows)},
        )
    problems: list[Exception] = []
    for row in present:
        try:
            return _resolve_row(row)
        except ApiError as error:
            problems.append(error)
        except CatalogError as error:
            problems.append(ApiError(error.code, error.message, details=error.details))
    raise _preferred_problem(problems)


def resolve_present_locations(catalog: Any, asset_id: int) -> list[ControlledLocation]:
    """Every usable ``PRESENT`` position, ascending by ``location_id`` (P4-00B helper).

    ``resolve_present_location`` keeps its frozen P1-04 contract ("the first usable
    position, or a structured refusal"); ADR-0011 §4.2 needs the whole deterministic walk
    instead, because a multi-position asset must take its EXIF evidence from the smallest
    ``location_id`` that really parses. The four boundary checks are the same private
    ``_resolve_row`` used by the single-position entry point - this never re-implements
    them, and a position that fails any of them is simply not part of the walk.
    """
    resolved: list[ControlledLocation] = []
    for row in list_asset_locations(catalog, int(asset_id)):
        if str(row["availability"]) != "PRESENT":
            continue
        try:
            resolved.append(_resolve_row(row))
        except (ApiError, CatalogError):
            continue
    return resolved


def resolve_registered_location(
    catalog: Any, asset_id: int, location_id: int
) -> ControlledLocation:
    """Resolve one exact, online, present location owned by ``asset_id``.

    Local desktop actions must never fall back to another copy of the same asset. This
    therefore selects by both ids, checks current source/location state, then uses the same
    controlled path and unchanged-file checks as original reads.
    """
    rows = list_asset_locations(catalog, int(asset_id))
    row = next((item for item in rows if int(item["location_id"]) == int(location_id)), None)
    if row is None:
        raise ApiError(
            SOURCE_FILE_MISSING,
            "the requested registered position does not belong to this asset",
        )
    if str(row["availability"]) != "PRESENT" or str(row["source_status"]) != "ONLINE":
        raise ApiError(SOURCE_OFFLINE, "the requested file position is not currently available")
    return _resolve_row(row)


def resolve_optional_location(catalog: Any, asset_id: int) -> ControlledLocation | None:
    """The same resolution, but ``None`` instead of an error for the status header."""
    try:
        return resolve_present_location(catalog, asset_id)
    except (ApiError, CatalogError):
        return None


def _resolve_row(row: Any) -> ControlledLocation:
    source_id = int(row["source_id"])
    root = str(row["root_path"])
    relative_path = str(row["relative_path"])
    # 1. The stored key must be a legal source-relative path. This is the frozen rule the
    #    catalog itself uses, so a row that fails it can never be turned into a path.
    try:
        path_key(relative_path)
    except CatalogError as error:
        raise ApiError(
            ACCESS_DENIED,
            "the registered relative path is not a legal source-relative path",
            details={"location_id": int(row["location_id"]), "reason": error.code},
        ) from error
    parts = _relative_parts(relative_path)
    if not parts:
        raise ApiError(
            ACCESS_DENIED,
            "the registered relative path resolves to its source root",
            details={"location_id": int(row["location_id"])},
        )
    absolute = Path(root).joinpath(*parts)
    # 2. Boundary: the rebuilt path must stay strictly inside the registered root.
    if not is_same_or_ancestor(root, absolute) or root_key(absolute) == root_key(root):
        raise ApiError(
            ACCESS_DENIED,
            "the registered position escapes its source root",
            details={"location_id": int(row["location_id"]), "source_id": source_id},
        )
    # 3a. No reparse point below the root (a junction or link swapped in after the scan).
    _reject_reparse_points(root, parts, location_id=int(row["location_id"]))
    # 3b. Physical containment: even if a link slipped through, the resolved target has to
    #     stay inside the resolved root.
    real_root = os.path.realpath(root)
    try:
        real_target = os.path.realpath(absolute)
    except OSError as error:  # pragma: no cover - defensive
        raise ApiError(
            FILE_READ_ERROR,
            f"cannot resolve the registered position ({type(error).__name__})",
            details={"location_id": int(row["location_id"])},
        ) from error
    if not is_same_or_ancestor(real_root, real_target) or root_key(real_target) == root_key(real_root):
        raise ApiError(
            ACCESS_DENIED,
            "the resolved target is outside its source root",
            details={"location_id": int(row["location_id"]), "source_id": source_id},
        )
    # 4. The observation must still match the catalog exactly.
    require_unchanged(absolute, row)
    if not absolute.is_file():
        raise ApiError(
            SOURCE_FILE_MISSING,
            "the registered position is not a regular file",
            details={"location_id": int(row["location_id"])},
        )
    return ControlledLocation(
        location_id=int(row["location_id"]),
        source_id=source_id,
        relative_path=relative_path,
        root_path=root,
        absolute_path=absolute,
        size=int(row["size"]),
        mtime_ns=int(row["mtime_ns"]),
    )


def _reject_reparse_points(root: str, parts: Sequence[str], *, location_id: int) -> None:
    current = Path(root)
    for part in parts:
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError as error:
            raise ApiError(
                SOURCE_FILE_MISSING,
                "the registered position is not reachable",
                details={"location_id": location_id},
            ) from error
        except OSError as error:
            raise ApiError(
                FILE_READ_ERROR,
                f"cannot inspect the registered position ({type(error).__name__})",
                details={"location_id": location_id},
            ) from error
        attributes = int(getattr(info, "st_file_attributes", 0))
        if attributes & REPARSE_FLAG or stat.S_ISLNK(info.st_mode):
            raise ApiError(
                ACCESS_DENIED,
                "a component of the registered position is a reparse point or a link",
                details={"location_id": location_id},
            )


def _relative_parts(relative_path: str) -> list[str]:
    text = str(relative_path).replace("/", "\\")
    normalized = ntpath.normpath(text)
    return [part for part in normalized.split("\\") if part not in ("", ".")]


def _preferred_problem(problems: Sequence[Exception]) -> Exception:
    for code in (SOURCE_FILE_MISSING, ACCESS_DENIED, INPUT_UNSTABLE, FILE_READ_ERROR):
        for problem in problems:
            if getattr(problem, "code", None) == code:
                return problem
    return ApiError(
        SOURCE_FILE_MISSING,
        "no registered position of this asset is readable",
        details={"positions": len(problems)},
    )


@dataclass(frozen=True)
class ByteRange:
    """One inclusive byte range of an entity."""

    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def parse_range(header: str | None, *, size: int) -> ByteRange | None:
    """Parse one RFC 7233 ``bytes=`` range; ``None`` means "send the whole entity"."""
    if not header:
        return None
    text = str(header).strip()
    if not text.lower().startswith("bytes="):
        # An unsupported range unit must be ignored, not rejected (RFC 7233 §3.1).
        return None
    spec = text[len("bytes=") :].strip()
    if not spec or "," in spec:
        raise ApiError(
            RANGE_NOT_SATISFIABLE,
            "only a single byte range is supported",
            details={"range": text, "size": int(size)},
            headers={"Content-Range": f"bytes */{int(size)}"},
        )
    first, _, last = spec.partition("-")
    if size <= 0:
        raise ApiError(
            RANGE_NOT_SATISFIABLE,
            "an empty entity has no satisfiable byte range",
            details={"range": text},
            headers={"Content-Range": "bytes */0"},
        )
    if not first:
        suffix = _parse_integer(last, field="suffix", raw=spec)
        if suffix <= 0:
            raise ApiError(
                RANGE_NOT_SATISFIABLE,
                "a suffix range must be positive",
                details={"range": text},
                headers={"Content-Range": f"bytes */{int(size)}"},
            )
        return ByteRange(start=max(0, int(size) - suffix), end=int(size) - 1)
    start = _parse_integer(first, field="start", raw=spec)
    if start >= int(size):
        raise ApiError(
            RANGE_NOT_SATISFIABLE,
            "the range starts beyond the end of the entity",
            details={"range": text, "size": int(size)},
            headers={"Content-Range": f"bytes */{int(size)}"},
        )
    if not last:
        return ByteRange(start=start, end=int(size) - 1)
    end = _parse_integer(last, field="end", raw=spec)
    if end < start:
        raise ApiError(
            INVALID_RANGE,
            "the range end must not precede its start",
            details={"range": text},
        )
    return ByteRange(start=start, end=min(end, int(size) - 1))


def _parse_integer(text: str, *, field: str, raw: str) -> int:
    try:
        value = int(str(text))
    except (TypeError, ValueError) as error:
        raise ApiError(
            INVALID_RANGE, f"the range {field} is not an integer", details={"range": raw}
        ) from error
    if value < 0:
        raise ApiError(
            INVALID_RANGE, f"the range {field} must not be negative", details={"range": raw}
        )
    return value


def safe_display_filename(relative_path: str, fallback: str) -> str:
    """The last path element, with separators and control characters removed."""
    text = str(relative_path).replace("\\", "/")
    name = PurePosixPath(text).name or ""
    cleaned = "".join(
        "_" if (char in '/\\"' or ord(char) < 0x20 or ord(char) == 0x7F) else char
        for char in name
    ).strip()
    if not cleaned or cleaned in (".", ".."):
        return fallback
    return cleaned


def content_disposition(filename: str) -> str:
    """``inline`` with an ASCII fallback plus the RFC 5987 UTF-8 spelling."""
    ascii_name = "".join(char if ord(char) < 0x80 and char not in '"\\' else "_" for char in filename)
    if not ascii_name:
        ascii_name = "download"
    encoded = quote(filename, safe="")
    return f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


def media_type_for(relative_path: str) -> str:
    suffix = ntpath.splitext(str(relative_path))[1].lower()
    return _MEDIA_TYPES.get(suffix, "application/octet-stream")


def artifact_etag(processor_id: str, sha256: str) -> str:
    """A version-and-content fingerprint; identical bytes always produce the same tag."""
    return f'"{processor_id}:{sha256}"'


def original_etag(sha256: str, size: int, mtime_ns: int) -> str:
    """Content identity plus the observation the bytes were checked against."""
    return f'"{sha256}:{int(size)}:{int(mtime_ns)}"'


async def stream_file(path: Path, *, start: int = 0, length: int | None = None) -> AsyncIterator[bytes]:
    """Yield ``length`` bytes from ``start`` without blocking the event loop."""
    remaining = None if length is None else int(length)
    with open(path, "rb") as stream:
        if start:
            stream.seek(int(start))
        try:
            while remaining is None or remaining > 0:
                request = READ_CHUNK_BYTES if remaining is None else min(READ_CHUNK_BYTES, remaining)
                chunk = await asyncio.to_thread(stream.read, request)
                if not chunk:
                    break
                if remaining is not None:
                    remaining -= len(chunk)
                yield chunk
        finally:
            await asyncio.to_thread(stream.close)
