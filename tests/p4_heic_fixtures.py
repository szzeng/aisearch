"""P4-00C-A fixtures: synthetic HEIC/HEIF files written with the pinned pillow-heif encoder.

Everything here lives under pytest's ``tmp_path`` (``%TEMP%``): no fixture reads, moves or
deletes a real photo library, and no model or GPU is involved.

The one subtlety this module exists for is the *shape* of the orientation evidence. The
pinned encoder writes the same EXIF payload in two different forms:

* handing it the raw ``exif`` **bytes** lets pillow-heif see the orientation, so it writes a
  container ``irot`` property **and** keeps the EXIF tag (libheif then rotates the pixels
  itself on read);
* handing it an ``Image.Exif`` **object** bypasses that plumbing, so the file carries the
  EXIF tag and *no* container property (the pixels then come back unrotated and the EXIF
  tag is the only orientation evidence left).

Both shapes occur in the wild, both are produced here (``container_orientation``), and the
P4-00C-A decoder has to end up with the same upright pixels for both.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PIL import Image

#: Fixed wall-clock origin so an aged fixture file is always outside the stability window.
WALL_BASE_MS = 1_700_000_000_000
QUIET_AGE_MS = 3_600_000

ORIENTATION = 0x0112
EXIF_IFD = 0x8769
GPS_IFD = 0x8825
TAG_DATETIME_ROOT = 0x0132
TAG_MAKE = 0x010F
TAG_MODEL = 0x0110
TAG_DATETIME_ORIGINAL = 0x9003
TAG_DATETIME_DIGITIZED = 0x9004
TAG_OFFSET_TIME = 0x9010
TAG_OFFSET_TIME_ORIGINAL = 0x9011
TAG_OFFSET_TIME_DIGITIZED = 0x9012
TAG_SUBSEC_TIME_ORIGINAL = 0x9291
TAG_SUBSEC_TIME_DIGITIZED = 0x9292

HANGZHOU_POSITION = (30.2741, 120.1551)


def stamp(path: Path, *, age_ms: int = QUIET_AGE_MS) -> Path:
    """Give a fixture file an ``mtime`` that is already outside the stability window."""
    moment = (WALL_BASE_MS - int(age_ms)) * 1_000_000
    os.utime(path, ns=(moment, moment))
    return path


def quadrants(width: int = 320, height: int = 200) -> Image.Image:
    """Four distinct colours plus an off-centre marker, so every mirror/rotation differs."""
    image = Image.new("RGB", (width, height))
    image.paste((230, 20, 20), (0, 0, width // 2, height // 2))
    image.paste((20, 230, 20), (width // 2, 0, width, height // 2))
    image.paste((20, 20, 230), (0, height // 2, width // 2, height))
    image.paste((235, 235, 30), (width // 2, height // 2, width, height))
    image.paste((0, 0, 0), (8, 8, 56, 40))
    return image


def corner_colours(image: Image.Image) -> list[tuple[int, int, int]]:
    width, height = image.size
    return [
        image.getpixel((8, 8))[:3],
        image.getpixel((width - 8, 8))[:3],
        image.getpixel((8, height - 8))[:3],
        image.getpixel((width - 8, height - 8))[:3],
    ]


def block_colour(
    image: Image.Image, centre: tuple[int, int], *, box: int = 6
) -> tuple[int, int, int]:
    half = box // 2
    pixels = [
        image.getpixel((x, y))[:3]
        for x in range(centre[0] - half, centre[0] + half)
        for y in range(centre[1] - half, centre[1] + half)
    ]
    channels = list(zip(*pixels, strict=True))
    return tuple(int(round(sum(channel) / len(channel))) for channel in channels)  # type: ignore[return-value]


def degrees_dms(value: float) -> tuple[float, float, float]:
    degrees = int(value)
    minutes_full = (value - degrees) * 60.0
    minutes = int(minutes_full)
    seconds = (minutes_full - minutes) * 60.0
    return (float(degrees), float(minutes), round(seconds, 6))


def gps_fields(
    position: tuple[float, float] | None = HANGZHOU_POSITION,
    *,
    datum: str | None = "WGS84",
) -> dict[int, Any] | None:
    if position is None:
        return None
    latitude, longitude = position
    fields: dict[int, Any] = {
        1: "N" if latitude >= 0 else "S",
        2: degrees_dms(abs(latitude)),
        3: "E" if longitude >= 0 else "W",
        4: degrees_dms(abs(longitude)),
    }
    if datum is not None:
        fields[18] = datum
    return fields


def build_exif(
    *,
    orientation: int | None = None,
    root: str | None = None,
    original: str | None = None,
    digitized: str | None = None,
    offset_root: str | None = None,
    offset_original: str | None = None,
    offset_digitized: str | None = None,
    subsec_original: str | None = None,
    subsec_digitized: str | None = None,
    make: str | None = None,
    model: str | None = None,
    gps: Mapping[int, Any] | None = None,
) -> Image.Exif:
    """The EXIF block shared by the JPEG and HEIC halves of one comparison."""
    exif = Image.Exif()
    if orientation is not None:
        exif[ORIENTATION] = int(orientation)
    if root is not None:
        exif[TAG_DATETIME_ROOT] = root
    if offset_root is not None:
        exif[TAG_OFFSET_TIME] = offset_root
    if make is not None:
        exif[TAG_MAKE] = make
    if model is not None:
        exif[TAG_MODEL] = model
    sub: dict[int, Any] = {}
    if original is not None:
        sub[TAG_DATETIME_ORIGINAL] = original
    if digitized is not None:
        sub[TAG_DATETIME_DIGITIZED] = digitized
    if offset_original is not None:
        sub[TAG_OFFSET_TIME_ORIGINAL] = offset_original
    if offset_digitized is not None:
        sub[TAG_OFFSET_TIME_DIGITIZED] = offset_digitized
    if subsec_original is not None:
        sub[TAG_SUBSEC_TIME_ORIGINAL] = subsec_original
    if subsec_digitized is not None:
        sub[TAG_SUBSEC_TIME_DIGITIZED] = subsec_digitized
    if sub:
        exif[EXIF_IFD] = sub
    if gps is not None:
        exif[GPS_IFD] = dict(gps)
    return exif


def write_image(
    path: Path,
    *,
    image_format: str,
    image: Image.Image | None = None,
    exif: Image.Exif | None = None,
    container_orientation: bool = True,
    quality: int = 90,
    age_ms: int = QUIET_AGE_MS,
) -> Path:
    """Write one fixture as JPEG or HEIC, optionally without the container orientation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    target = image if image is not None else quadrants()
    if image_format == "JPEG":
        options: dict[str, Any] = {"quality": int(quality)}
        if exif is not None:
            options["exif"] = exif.tobytes()
        target.save(path, "JPEG", **options)
    elif image_format == "HEIF":
        # ``bytes`` lets pillow-heif see the orientation and write the container property;
        # an ``Exif`` object leaves the file with EXIF-only orientation evidence.
        options = {}
        if exif is not None:
            options["exif"] = exif.tobytes() if container_orientation else exif
        target.save(path, format="HEIF", **options)
    else:  # pragma: no cover - the fixtures only use the two formats above
        raise ValueError(f"unsupported fixture format: {image_format!r}")
    return stamp(path, age_ms=age_ms)


def write_exif_jpeg(
    path: Path, *, image: Image.Image | None = None, **exif_options: Any
) -> Path:
    exif = build_exif(**exif_options)
    return write_image(path, image_format="JPEG", image=image, exif=exif)


def write_exif_heic(
    path: Path,
    *,
    container_orientation: bool = True,
    image: Image.Image | None = None,
    **exif_options: Any,
) -> Path:
    exif = build_exif(**exif_options)
    return write_image(
        path,
        image_format="HEIF",
        image=image,
        exif=exif,
        container_orientation=container_orientation,
    )


def write_heic(
    path: Path,
    *,
    image: Image.Image | None = None,
    orientation: int | None = None,
    container_orientation: bool = True,
    size: tuple[int, int] = (320, 200),
    **exif_options: Any,
) -> Path:
    """One HEIC photo with exactly the asked-for orientation evidence."""
    exif = build_exif(orientation=orientation, **exif_options)
    target = image if image is not None else quadrants(*size)
    return write_image(
        path,
        image_format="HEIF",
        image=target,
        exif=exif,
        container_orientation=container_orientation,
    )


def write_plain_heic(path: Path, *, size: tuple[int, int] = (48, 36)) -> Path:
    """One HEIC with no EXIF at all."""
    return write_image(path, image_format="HEIF", image=Image.new("RGB", size, (7, 8, 9)))


__all__ = [
    "EXIF_IFD",
    "GPS_IFD",
    "HANGZHOU_POSITION",
    "ORIENTATION",
    "QUIET_AGE_MS",
    "TAG_DATETIME_DIGITIZED",
    "TAG_DATETIME_ORIGINAL",
    "TAG_DATETIME_ROOT",
    "TAG_MAKE",
    "TAG_MODEL",
    "TAG_OFFSET_TIME",
    "TAG_OFFSET_TIME_DIGITIZED",
    "TAG_OFFSET_TIME_ORIGINAL",
    "TAG_SUBSEC_TIME_DIGITIZED",
    "TAG_SUBSEC_TIME_ORIGINAL",
    "WALL_BASE_MS",
    "block_colour",
    "build_exif",
    "corner_colours",
    "degrees_dms",
    "gps_fields",
    "quadrants",
    "stamp",
    "write_exif_heic",
    "write_exif_jpeg",
    "write_heic",
    "write_image",
    "write_plain_heic",
]
