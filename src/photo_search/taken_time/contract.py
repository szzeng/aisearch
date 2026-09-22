"""The frozen P4-00B taken-time vocabulary, civil key, capture range and honesty block.

ADR-0011 is authoritative; this module is its executable form on the production side. It
holds exactly four things and nothing else:

* the closed ``time_source``/``time_confidence`` vocabulary and the frozen source mapping
  table of ADR-0011 §4.1 (label, confidence, whether the row participates in the capture
  *range filter*, which ``capture_desc`` bucket it belongs to);
* the civil sort key of ADR-0011 §3.1 - the wall-clock fields read as if they were UTC,
  anchored at ``0001-01-01T00:00:00.000``, in milliseconds and never negative. The key is
  *not* an instant and the recorded ``timezone_offset`` never takes part in it;
* the capture bound grammar of ADR-0011 §6.2/§6.3 with its four frozen refusal reasons;
* the honesty block of ADR-0011 §8.1, whose ``state`` is a pure function of the three
  counts it carries (a mismatched library rule version normalizes the counts to zero).

The module imports nothing from ``photo_search.search``/``api``, so the retrieval layer and
the transport layer can both depend on it without a cycle.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from photo_search.catalog.errors import INVALID_TIME_RANGE, CatalogError

#: The companion/artifact contract this module implements.
CONTRACT_VERSION = "p4-time-contracts.v1"

#: ADR-0011 §2: the library-level parsing/priority rule version stored in ``metadata``.
RULES_VERSION = 1
RULES_VERSION_KEY = "taken_time_rules_version"

#: ADR-0011 §4.1, in priority order. ``FILE_MTIME`` is the always-available chain tail.
TIME_SOURCES = ("EXIF_ORIGINAL", "EXIF_DIGITIZED", "EXIF_ROOT", "FILE_MTIME")
#: The sources whose rows are labelled ``capture``; only these participate in a range filter.
CAPTURE_SOURCES = ("EXIF_ORIGINAL", "EXIF_DIGITIZED")
#: The sources that carry a civil key and therefore sit in the ordered bucket.
ORDERED_SOURCES = ("EXIF_ORIGINAL", "EXIF_DIGITIZED", "EXIF_ROOT")
TIME_CONFIDENCES = ("HIGH", "MEDIUM", "LOW")
CAPTURE_LABEL = "capture"
FILE_TIME_LABEL = "file_time"

#: The civil origin of ADR-0011 §3.1 - a contract convention, not an instant.
CIVIL_EPOCH_ORDINAL = dt.date(1, 1, 1).toordinal()
MAX_SORT_KEY = 315_537_897_599_999
MIN_OFFSET_MINUTES = -720
MAX_OFFSET_MINUTES = 840

#: ADR-0011 §6.2: the closed refusal reasons of ``INVALID_TIME_RANGE``.
REASON_BOUND_MALFORMED = "capture_bound_malformed"
REASON_BOUND_HAS_OFFSET = "capture_bound_has_offset"
REASON_BOUND_OUT_OF_RANGE = "capture_bound_out_of_range"
REASON_RANGE_NOT_ASCENDING = "capture_range_not_ascending"
TIME_RANGE_REASONS = (
    REASON_BOUND_MALFORMED,
    REASON_BOUND_HAS_OFFSET,
    REASON_BOUND_OUT_OF_RANGE,
    REASON_RANGE_NOT_ASCENDING,
)

#: The two transport spellings of the one capture condition (ADR-0011 §6.1).
FIELD_FROM = "capture_from"
FIELD_TO = "capture_to_exclusive"
CAPTURE_FIELDS = (FIELD_FROM, FIELD_TO)

#: ADR-0011 §8.1: the four ``state`` values of the honesty block.
STATE_PENDING = "PENDING"
STATE_PARTIAL = "PARTIAL"
STATE_READY = "READY"
STATE_NO_CAPTURE_TIME = "NO_CAPTURE_TIME"
TAKEN_TIME_STATES = (STATE_PENDING, STATE_PARTIAL, STATE_READY, STATE_NO_CAPTURE_TIME)

_BOUND_PATTERN = re.compile(
    r"^(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    r"(?:T(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?:\.(?P<fraction>[0-9]{1,3}))?)?$"
)
#: A trailing ``Z``/``z`` or a ``+HH:MM``/``+HHMM`` suffix. ADR-0011 §6.2 refuses these
#: explicitly: a capture bound has no unique timezone, and accepting one would pretend the
#: server converted it.
_OFFSET_SUFFIX = re.compile(r"[+-][0-9]{2}:?[0-9]{2}$")


def label_for(time_source: str) -> str:
    """ADR-0011 §4.4: ``capture`` iff the source really is an EXIF capture field."""
    return CAPTURE_LABEL if str(time_source) in CAPTURE_SOURCES else FILE_TIME_LABEL


def participates_in_capture_filter(time_source: str) -> bool:
    """Only ``label = capture`` rows may ever satisfy a time range condition."""
    return str(time_source) in CAPTURE_SOURCES


def sort_bucket(time_source: str) -> str:
    """ADR-0011 §5.1: the ``capture_desc`` bucket of one source."""
    return "ordered" if str(time_source) in ORDERED_SOURCES else "tail"


# ----------------------------------------------------------------------- the civil key
def civil_key(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
    millis: int = 0,
) -> int:
    """ADR-0011 §3.1: the non-negative wall-clock key (raises ``ValueError`` if invalid)."""
    ordinal = dt.date(int(year), int(month), int(day)).toordinal()
    return (
        (ordinal - CIVIL_EPOCH_ORDINAL) * 86_400_000
        + (int(hour) * 3600 + int(minute) * 60 + int(second)) * 1000
        + int(millis)
    )


def _refuse(reason: str, *, field: str) -> CatalogError:
    return CatalogError(
        INVALID_TIME_RANGE,
        f"the capture bound {field} is not acceptable",
        details={"reason": str(reason), "field": str(field)},
    )


def parse_bound(raw: Any, *, field: str = FIELD_FROM) -> int:
    """One capture bound of ADR-0011 §6.2 as a civil key, or a structured 422 refusal."""
    if not isinstance(raw, str) or not raw:
        raise _refuse(REASON_BOUND_MALFORMED, field=field)
    if raw != raw.strip():
        # Loose/leading-trailing spellings are never interpreted (ADR-0011 §6.2).
        raise _refuse(REASON_BOUND_MALFORMED, field=field)
    if raw.endswith(("Z", "z")) or _OFFSET_SUFFIX.search(raw):
        raise _refuse(REASON_BOUND_HAS_OFFSET, field=field)
    match = _BOUND_PATTERN.fullmatch(raw)
    if match is None:
        raise _refuse(REASON_BOUND_MALFORMED, field=field)
    year = int(match.group("year"))
    if year < 1 or year > 9999:
        # ``0000`` is syntactically fine but outside the frozen civil range.
        raise _refuse(REASON_BOUND_OUT_OF_RANGE, field=field)
    hour = int(match.group("hour") or 0)
    minute = int(match.group("minute") or 0)
    second = int(match.group("second") or 0)
    if hour > 23 or minute > 59 or second > 59:
        raise _refuse(REASON_BOUND_MALFORMED, field=field)
    fraction = match.group("fraction") or ""
    millis = int(fraction.ljust(3, "0")) if fraction else 0
    try:
        return civil_key(
            year,
            int(match.group("month")),
            int(match.group("day")),
            hour,
            minute,
            second,
            millis,
        )
    except ValueError as error:
        # ``2022-02-30``, month 13, ... - not a real calendar day.
        raise _refuse(REASON_BOUND_MALFORMED, field=field) from error


def normalize_bound(key: int) -> str:
    """ADR-0011 §6.3: the fixed-width ``YYYY-MM-DDTHH:MM:SS.mmm`` wall-clock spelling."""
    days, remainder = divmod(int(key), 86_400_000)
    date = dt.date.fromordinal(CIVIL_EPOCH_ORDINAL + days)
    hour, remainder = divmod(remainder, 3_600_000)
    minute, remainder = divmod(remainder, 60_000)
    second, millis = divmod(remainder, 1000)
    return (
        f"{date.year:04d}-{date.month:02d}-{date.day:02d}"
        f"T{int(hour):02d}:{int(minute):02d}:{int(second):02d}.{int(millis):03d}"
    )


@dataclass(frozen=True)
class CaptureRange:
    """One normalized capture condition: half-open, civil-to-civil, capture rows only."""

    from_key: int | None = None
    to_key: int | None = None
    from_text: str | None = None
    to_text: str | None = None

    @property
    def is_set(self) -> bool:
        return self.from_key is not None or self.to_key is not None


def parse_capture_range(
    capture_from: Any = None, capture_to_exclusive: Any = None
) -> CaptureRange | None:
    """Both bounds of ADR-0011 §6.2/§6.3, or ``None`` when neither was given."""
    if capture_from is None and capture_to_exclusive is None:
        return None
    from_key = None if capture_from is None else parse_bound(capture_from, field=FIELD_FROM)
    to_key = (
        None
        if capture_to_exclusive is None
        else parse_bound(capture_to_exclusive, field=FIELD_TO)
    )
    if from_key is not None and to_key is not None and from_key >= to_key:
        # An empty interval is an explicit refusal, never a silently empty result.
        raise _refuse(REASON_RANGE_NOT_ASCENDING, field=FIELD_FROM)
    return CaptureRange(
        from_key=from_key,
        to_key=to_key,
        from_text=None if from_key is None else normalize_bound(from_key),
        to_text=None if to_key is None else normalize_bound(to_key),
    )


def capture_echo(capture: CaptureRange | None) -> dict[str, Any]:
    """The two always-present echo keys of ADR-0011 §8.1/§6.3."""
    return {
        FIELD_FROM: None if capture is None else capture.from_text,
        FIELD_TO: None if capture is None else capture.to_text,
    }


# ------------------------------------------------------------------- the honesty block
def taken_time_state(assets_total: int, extracted_assets: int, capture_time_assets: int) -> str:
    """ADR-0011 §8.1: ``state`` is a pure function of the three counts it carries."""
    if int(assets_total) == 0:
        return STATE_READY
    if int(extracted_assets) == 0:
        return STATE_PENDING
    if int(extracted_assets) < int(assets_total):
        return STATE_PARTIAL
    return STATE_READY if int(capture_time_assets) > 0 else STATE_NO_CAPTURE_TIME


def taken_time_block(
    *,
    assets_total: int,
    extracted_assets: int,
    capture_time_assets: int,
    rules_version_matches: bool,
) -> dict[str, Any]:
    """ADR-0011 §8.1: the four-key block, with the rule-version gate as count normalization.

    When the library's stored ``taken_time_rules_version`` is not this build's, the rows are
    kept but are *not trusted*: both trusted counts are reported as ``0``, which makes
    ``state`` ``PENDING`` (or ``READY`` for an empty library) without ever inventing a second
    input. The same three counts can therefore never yield two different states.
    """
    if rules_version_matches:
        trusted_extracted = max(0, int(extracted_assets))
        trusted_captured = max(0, int(capture_time_assets))
    else:
        trusted_extracted = 0
        trusted_captured = 0
    total = max(0, int(assets_total))
    return {
        "state": taken_time_state(total, trusted_extracted, trusted_captured),
        "assets_total": total,
        "extracted_assets": trusted_extracted,
        "capture_time_assets": trusted_captured,
    }


# --------------------------------------------------------------- normalizing the sources
#: The frozen mapping table of ADR-0011 §4.1, as data (the companion carries the same rows).
SOURCE_RULES: Mapping[str, Mapping[str, Any]] = {
    "EXIF_ORIGINAL": {
        "time_source": "EXIF_ORIGINAL",
        "label": CAPTURE_LABEL,
        "capture_confidence": "HIGH",
        "participates_in_capture_filter": True,
        "sort_bucket": "ordered",
        "fallback_rank": 1,
    },
    "EXIF_DIGITIZED": {
        "time_source": "EXIF_DIGITIZED",
        "label": CAPTURE_LABEL,
        "capture_confidence": "MEDIUM",
        "participates_in_capture_filter": True,
        "sort_bucket": "ordered",
        "fallback_rank": 2,
    },
    "EXIF_ROOT": {
        "time_source": "EXIF_ROOT",
        "label": FILE_TIME_LABEL,
        "capture_confidence": "LOW",
        "participates_in_capture_filter": False,
        "sort_bucket": "ordered",
        "fallback_rank": 3,
    },
    "FILE_MTIME": {
        "time_source": "FILE_MTIME",
        "label": FILE_TIME_LABEL,
        "capture_confidence": "LOW",
        "participates_in_capture_filter": False,
        "sort_bucket": "tail",
        "fallback_rank": 4,
    },
}


def clamp_camera(value: Any) -> str | None:
    """ADR-0011 §8.4: normalize ``Make Model`` (strip/collapse/truncate) or ``None``."""
    if value is None:
        return None
    pieces = [
        "".join(char for char in str(part) if ord(char) >= 32).strip()
        for part in str(value).split()
    ]
    rendered = " ".join(piece for piece in pieces if piece)
    if not rendered:
        return None
    return rendered[:128]


__all__ = [
    "CAPTURE_FIELDS",
    "CAPTURE_LABEL",
    "CAPTURE_SOURCES",
    "CIVIL_EPOCH_ORDINAL",
    "CONTRACT_VERSION",
    "CaptureRange",
    "FIELD_FROM",
    "FIELD_TO",
    "FILE_TIME_LABEL",
    "MAX_OFFSET_MINUTES",
    "MAX_SORT_KEY",
    "MIN_OFFSET_MINUTES",
    "ORDERED_SOURCES",
    "REASON_BOUND_HAS_OFFSET",
    "REASON_BOUND_MALFORMED",
    "REASON_BOUND_OUT_OF_RANGE",
    "REASON_RANGE_NOT_ASCENDING",
    "RULES_VERSION",
    "RULES_VERSION_KEY",
    "SOURCE_RULES",
    "STATE_NO_CAPTURE_TIME",
    "STATE_PARTIAL",
    "STATE_PENDING",
    "STATE_READY",
    "TAKEN_TIME_STATES",
    "TIME_CONFIDENCES",
    "TIME_RANGE_REASONS",
    "TIME_SOURCES",
    "capture_echo",
    "civil_key",
    "clamp_camera",
    "label_for",
    "normalize_bound",
    "parse_bound",
    "parse_capture_range",
    "participates_in_capture_filter",
    "sort_bucket",
    "taken_time_block",
    "taken_time_state",
]
