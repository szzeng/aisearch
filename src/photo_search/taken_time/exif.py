"""Reading the pinned EXIF time fields, header only (ADR-0011 §4.1/§9).

The one trap the contract documents and this module must not fall into: Pillow's
``Image.getexif()`` exposes the *IFD0* tags only, while ``DateTimeOriginal`` (0x9003),
``DateTimeDigitized`` (0x9004), their offsets (0x9011/0x9012), their sub-second spellings
(0x9291/0x9292) and the root ``OffsetTime`` (0x9010) live in the Exif sub-IFD, reachable
only through ``getexif().get_ifd(0x8769)``. A naive root-level ``get`` silently returns
``None`` and the asset would be reported as "file time" even though it declares a capture
time.

Nothing here decodes a pixel: ``Image.open`` parses the container header, and only the
``Exif`` block is read. Every malformed value is *dropped* (returned as ``None``) instead of
being turned into an error, exactly as ADR-0011 §4.1 requires: a bad value only makes the
chain fall through to the next source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from photo_search.taken_time import contract

#: The Exif sub-IFD pointer tag and the frozen time tags (ADR-0011 §9).
EXIF_IFD_TAG = 0x8769
TAG_DATETIME_ORIGINAL = 0x9003
TAG_DATETIME_DIGITIZED = 0x9004
TAG_DATETIME_ROOT = 0x0132
TAG_OFFSET_TIME_ORIGINAL = 0x9011
TAG_OFFSET_TIME_DIGITIZED = 0x9012
TAG_OFFSET_TIME = 0x9010
TAG_SUBSEC_TIME_ORIGINAL = 0x9291
TAG_SUBSEC_TIME_DIGITIZED = 0x9292
TAG_MAKE = 0x010F
TAG_MODEL = 0x0110
SUBSEC_TAGS = (TAG_SUBSEC_TIME_ORIGINAL, TAG_SUBSEC_TIME_DIGITIZED)

#: The EXIF spelling of a date-time: ``YYYY:MM:DD HH:MM:SS`` (colons, not dashes).
_EXIF_DATETIME = re.compile(
    r"^(?P<year>[0-9]{4}):(?P<month>[0-9]{2}):(?P<day>[0-9]{2})"
    r"[ T](?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})$"
)
#: ``+08:00``, ``+0800``, ``-05`` - never a spelling that carries seconds.
_OFFSET = re.compile(r"^(?P<sign>[+-])(?P<hour>[0-9]{2})(?::?(?P<minute>[0-9]{2}))?$")


@dataclass(frozen=True)
class ExifTimeFields:
    """The raw (unparsed) time evidence of one file, plus its camera spelling."""

    original: Any = None
    original_offset: Any = None
    original_subsec: Any = None
    digitized: Any = None
    digitized_offset: Any = None
    digitized_subsec: Any = None
    root: Any = None
    root_offset: Any = None
    make: Any = None
    model: Any = None

    @property
    def camera(self) -> str | None:
        """The normalized ``Make Model`` of ADR-0011 §8.4 (``None`` when both are empty)."""
        parts = [str(value) for value in (self.make, self.model) if value is not None]
        return contract.clamp_camera(" ".join(parts))


def _text(value: Any) -> str | None:
    if value is None:
        return None
    rendered = value.decode("ascii", "ignore") if isinstance(value, bytes) else str(value)
    return rendered if rendered else None


def read_time_fields(path: str | Path) -> ExifTimeFields:
    """Read the frozen EXIF time fields of one file header.

    Raises ``OSError``/``PIL.UnidentifiedImageError`` when the file cannot even be opened as
    an image container; the extraction path treats that as "no EXIF evidence" and falls
    through to the file time instead of failing the asset (ADR-0011 §4.1).
    """
    from PIL import Image

    # The HEIF container is only openable once the single pillow-heif registration point
    # has run (idempotent, and it fails closed by leaving HEIC undecodable) - P4-00C-A.
    from photo_search.media import heif

    heif.ensure_heif_support()
    with Image.open(path) as image:
        exif = image.getexif()
        sub: dict[int, Any] = {}
        try:
            sub = dict(exif.get_ifd(EXIF_IFD_TAG) or {})
        except Exception:  # noqa: BLE001 - a broken sub-IFD is "no evidence", never a crash
            sub = {}
        return ExifTimeFields(
            original=_text(sub.get(TAG_DATETIME_ORIGINAL) or exif.get(TAG_DATETIME_ORIGINAL)),
            original_offset=_text(
                sub.get(TAG_OFFSET_TIME_ORIGINAL) or exif.get(TAG_OFFSET_TIME_ORIGINAL)
            ),
            original_subsec=_text(
                sub.get(TAG_SUBSEC_TIME_ORIGINAL) or exif.get(TAG_SUBSEC_TIME_ORIGINAL)
            ),
            digitized=_text(sub.get(TAG_DATETIME_DIGITIZED) or exif.get(TAG_DATETIME_DIGITIZED)),
            digitized_offset=_text(
                sub.get(TAG_OFFSET_TIME_DIGITIZED) or exif.get(TAG_OFFSET_TIME_DIGITIZED)
            ),
            digitized_subsec=_text(
                sub.get(TAG_SUBSEC_TIME_DIGITIZED) or exif.get(TAG_SUBSEC_TIME_DIGITIZED)
            ),
            root=_text(exif.get(TAG_DATETIME_ROOT)),
            root_offset=_text(exif.get(TAG_OFFSET_TIME) or sub.get(TAG_OFFSET_TIME)),
            make=_text(exif.get(TAG_MAKE)),
            model=_text(exif.get(TAG_MODEL)),
        )


def parse_exif_datetime(raw: Any) -> int | None:
    """One EXIF date-time as a civil key, or ``None`` when it is missing/not a real day."""
    text = _text(raw)
    if text is None:
        return None
    match = _EXIF_DATETIME.fullmatch(text.strip())
    if match is None:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    second = int(match.group("second"))
    if hour > 23 or minute > 59 or second > 59:
        return None
    try:
        return contract.civil_key(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            hour,
            minute,
            second,
            0,
        )
    except ValueError:
        return None


def parse_offset_minutes(raw: Any) -> int | None:
    """One recorded timezone offset in minutes east of UTC, or ``None`` (never a guess).

    ADR-0011 §3.2: only an explicit, integral, in-range offset is accepted; a missing value,
    a non-integer, a spelling that carries seconds and an out-of-range shift all become
    ``None`` - the wall clock is kept as written and the confidence drops to ``MEDIUM`` for
    ``EXIF_ORIGINAL``.
    """
    text = _text(raw)
    if text is None:
        return None
    match = _OFFSET.fullmatch(text.strip())
    if match is None:
        return None
    minutes = int(match.group("hour")) * 60 + int(match.group("minute") or 0)
    if match.group("sign") == "-":
        minutes = -minutes
    if minutes < contract.MIN_OFFSET_MINUTES or minutes > contract.MAX_OFFSET_MINUTES:
        return None
    return minutes


def parse_subsec_millis(raw: Any) -> int:
    """ADR-0011 §3.4: the first three digits of the sub-second spelling, else ``0``."""
    text = _text(raw)
    if text is None:
        return 0
    digits = "".join(char for char in text.strip() if char.isdigit())
    if not digits:
        return 0
    return int(digits[:3].ljust(3, "0"))


def confidence_for(time_source: str, offset_minutes: int | None) -> str:
    """ADR-0011 §4.1: the frozen confidence of one source/offset combination."""
    if str(time_source) == "EXIF_ORIGINAL":
        return "HIGH" if offset_minutes is not None else "MEDIUM"
    return str(contract.SOURCE_RULES[str(time_source)]["capture_confidence"])


__all__ = [
    "EXIF_IFD_TAG",
    "ExifTimeFields",
    "SUBSEC_TAGS",
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
    "confidence_for",
    "parse_exif_datetime",
    "parse_offset_minutes",
    "parse_subsec_millis",
    "read_time_fields",
]
