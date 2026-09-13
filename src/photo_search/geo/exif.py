"""EXIF GPS parsing and the frozen ADR-0005 §3 status decision.

The rules that matter and are easy to get wrong: ``(0,0)`` is a legal coordinate, not a
missing sentinel; a missing GPS block is a *successful* ``ABSENT`` and never a failure; a
zero denominator, a non-finite number, a wrong reference direction or an out-of-range
value is ``INVALID``; an unknown coordinate reference system keeps the raw evidence but
produces no searchable coordinate. Nothing here guesses a datum.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

GPS_IFD = 0x8825

GPS_LATITUDE_REF = 1
GPS_LATITUDE = 2
GPS_LONGITUDE_REF = 3
GPS_LONGITUDE = 4
GPS_ALTITUDE_REF = 5
GPS_ALTITUDE = 6
GPS_IMG_DIRECTION = 17
GPS_MAP_DATUM = 18
GPS_HPOSITIONING_ERROR = 31

STATUS_VALID = "VALID"
STATUS_ABSENT = "ABSENT"
STATUS_INVALID = "INVALID"
STATUS_UNSUPPORTED_CRS = "UNSUPPORTED_CRS"

DATUM_WGS84 = "WGS84"
DATUM_ASSUMED_WGS84 = "ASSUMED_WGS84"
DATUM_UNSUPPORTED = "UNSUPPORTED"
DATUM_ABSENT = "ABSENT"

_WGS84_SPELLINGS = {"wgs84", "wgs 84", "wgs-84", "wgs_84", "epsg:4326"}
_TAG_NAMES = (
    GPS_LATITUDE_REF,
    GPS_LATITUDE,
    GPS_LONGITUDE_REF,
    GPS_LONGITUDE,
    GPS_ALTITUDE_REF,
    GPS_ALTITUDE,
    GPS_IMG_DIRECTION,
    GPS_MAP_DATUM,
    GPS_HPOSITIONING_ERROR,
)


@dataclass(frozen=True)
class GeoExtraction:
    """The decision for one input plus the exact raw evidence it was made from."""

    status: str
    datum_status: str
    raw_gps: dict[str, Any] | None
    normalized_wgs84: dict[str, Any] | None
    raw_fields_checksum_sha256: str
    evidence: dict[str, Any]


def read_gps_fields(exif: Any) -> dict[int, Any] | None:
    """Return the raw GPS IFD, or ``None`` when the file carries no GPS block at all."""
    if exif is None:
        return None
    try:
        ifd = exif.get_ifd(GPS_IFD)
    except Exception:  # noqa: BLE001 - a broken GPS block is data, not a crash
        return None
    if not ifd:
        return None
    return {int(key): value for key, value in dict(ifd).items()}


def analyze(gps: Mapping[int, Any] | None) -> GeoExtraction:
    """Turn one raw GPS IFD into the frozen status/datum/coordinate decision."""
    if not gps:
        return GeoExtraction(
            status=STATUS_ABSENT,
            datum_status=DATUM_ABSENT,
            raw_gps=None,
            normalized_wgs84=None,
            raw_fields_checksum_sha256=_checksum({}),
            evidence={},
        )

    evidence = {
        str(tag): _jsonable(gps[tag]) for tag in _TAG_NAMES if tag in gps
    }
    checksum = _checksum(evidence)

    latitude_ref = _reference(gps.get(GPS_LATITUDE_REF), allowed=("N", "S"))
    longitude_ref = _reference(gps.get(GPS_LONGITUDE_REF), allowed=("E", "W"))
    altitude_ref = _altitude_reference(gps.get(GPS_ALTITUDE_REF))
    altitude = _number(gps.get(GPS_ALTITUDE))
    direction = _number(gps.get(GPS_IMG_DIRECTION))
    accuracy = _number(gps.get(GPS_HPOSITIONING_ERROR))
    datum_text = _datum_text(gps.get(GPS_MAP_DATUM))

    latitude = _degree_value(gps.get(GPS_LATITUDE))
    longitude = _degree_value(gps.get(GPS_LONGITUDE))

    raw_gps = _raw_gps(
        latitude=latitude,
        latitude_ref=latitude_ref,
        longitude=longitude,
        longitude_ref=longitude_ref,
        datum_text=datum_text,
        altitude=altitude,
        altitude_ref=altitude_ref,
        direction=direction,
        accuracy=accuracy,
    )

    # 1. A field that cannot be interpreted at all is INVALID, whatever the datum says.
    if latitude is None or longitude is None or latitude_ref is None or longitude_ref is None:
        return _result(STATUS_INVALID, _datum_status(datum_text), raw_gps, None, checksum, evidence)

    signed_latitude = latitude if latitude_ref == "N" else -latitude
    signed_longitude = longitude if longitude_ref == "E" else -longitude
    if not (-90.0 <= signed_latitude <= 90.0) or not (-180.0 <= signed_longitude <= 180.0):
        return _result(STATUS_INVALID, _datum_status(datum_text), raw_gps, None, checksum, evidence)

    # 2. An unknown/declared-non-WGS84 datum keeps the raw evidence but yields no
    #    searchable coordinate; GCJ-02/BD-09 are never silently converted.
    if datum_text is not None and not _is_wgs84(datum_text):
        return _result(
            STATUS_UNSUPPORTED_CRS, DATUM_UNSUPPORTED, raw_gps, None, checksum, evidence
        )

    normalized = {
        "datum": "WGS84",
        "latitude": signed_latitude,
        "longitude": signed_longitude,
        "altitude_m": _signed_altitude(altitude, altitude_ref),
        "direction_deg": direction if direction is not None and 0.0 <= direction <= 360.0 else None,
        "accuracy_m": accuracy if accuracy is not None and accuracy >= 0.0 else None,
    }
    datum_status = DATUM_WGS84 if datum_text is not None else DATUM_ASSUMED_WGS84
    return _result(STATUS_VALID, datum_status, raw_gps, normalized, checksum, evidence)


def _result(
    status: str,
    datum_status: str,
    raw_gps: dict[str, Any] | None,
    normalized: dict[str, Any] | None,
    checksum: str,
    evidence: dict[str, Any],
) -> GeoExtraction:
    return GeoExtraction(
        status=status,
        datum_status=datum_status,
        raw_gps=raw_gps,
        normalized_wgs84=normalized,
        raw_fields_checksum_sha256=checksum,
        evidence=evidence,
    )


def _raw_gps(
    *,
    latitude: float | None,
    latitude_ref: str | None,
    longitude: float | None,
    longitude_ref: str | None,
    datum_text: str | None,
    altitude: float | None,
    altitude_ref: str | None,
    direction: float | None,
    accuracy: float | None,
) -> dict[str, Any] | None:
    """The frozen ``RawGps`` object, or ``None`` when it cannot be expressed at all."""
    if latitude is None or longitude is None or latitude_ref is None or longitude_ref is None:
        return None
    return {
        "latitude_dms": _dms(latitude),
        "latitude_ref": latitude_ref,
        "longitude_dms": _dms(longitude),
        "longitude_ref": longitude_ref,
        "map_datum": datum_text,
        "altitude_m": _signed_altitude(altitude, altitude_ref),
        "altitude_ref": altitude_ref,
        "direction_deg": direction if direction is not None and 0.0 <= direction <= 360.0 else None,
        "accuracy_m": accuracy if accuracy is not None and accuracy >= 0.0 else None,
    }


def _dms(value: float) -> list[float]:
    """Render one decimal degree as the three-element degrees/minutes/seconds array."""
    magnitude = abs(float(value))
    degrees = math.floor(magnitude)
    minutes_total = (magnitude - degrees) * 60.0
    minutes = math.floor(minutes_total)
    seconds = (minutes_total - minutes) * 60.0
    return [float(degrees), float(minutes), float(seconds)]


def _degree_value(value: Any) -> float | None:
    """One EXIF GPS degrees/minutes/seconds triple as a positive decimal degree."""
    parts = _sequence(value)
    if parts is None or len(parts) != 3:
        return None
    numbers = [_number(item) for item in parts]
    if any(number is None for number in numbers):
        return None
    degrees, minutes, seconds = (float(number) for number in numbers if number is not None)
    if degrees < 0.0 or minutes < 0.0 or seconds < 0.0:
        return None
    return degrees + minutes / 60.0 + seconds / 3600.0


def _number(value: Any) -> float | None:
    """A finite float for a rational/int/float, or ``None`` for anything unusable."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    numerator = getattr(value, "numerator", None)
    denominator = getattr(value, "denominator", None)
    if isinstance(numerator, int) and isinstance(denominator, int):
        if denominator == 0:
            # An illegal denominator is exactly the "非法分母" case, never a zero.
            return None
        try:
            number = float(numerator) / float(denominator)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    elif isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _sequence(value: Any) -> Sequence[Any] | None:
    if isinstance(value, (str, bytes, bytearray)):
        return None
    if isinstance(value, Sequence):
        return value
    return None


def _reference(value: Any, *, allowed: tuple[str, ...]) -> str | None:
    text = _text(value)
    if text is None:
        return None
    upper = text.strip().upper()
    return upper if upper in allowed else None


def _altitude_reference(value: Any) -> str | None:
    number = _number(value)
    if number is None:
        number = _number(_text(value))
    if number is None:
        return None
    if int(number) == 0:
        return "ABOVE_SEA_LEVEL"
    if int(number) == 1:
        return "BELOW_SEA_LEVEL"
    return None


def _signed_altitude(altitude: float | None, reference: str | None) -> float | None:
    if altitude is None:
        return None
    if reference == "BELOW_SEA_LEVEL":
        return -abs(altitude)
    return altitude


def _datum_text(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    stripped = text.strip()
    return stripped or None


def _is_wgs84(text: str) -> bool:
    return text.strip().casefold() in _WGS84_SPELLINGS


def _datum_status(datum_text: str | None) -> str:
    if datum_text is None:
        return DATUM_ASSUMED_WGS84
    return DATUM_WGS84 if _is_wgs84(datum_text) else DATUM_UNSUPPORTED


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8", errors="replace").rstrip("\x00")
        except Exception:  # noqa: BLE001 - defensive only
            return None
    if isinstance(value, str):
        return value.rstrip("\x00")
    return None


def _jsonable(value: Any) -> Any:
    """A JSON-safe rendering of one raw tag, used only for the evidence checksum."""
    if isinstance(value, (bytes, bytearray)):
        return {"bytes": value.hex()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    number = _number(value)
    if number is not None:
        return number
    if isinstance(value, str):
        return value
    numerator = getattr(value, "numerator", None)
    denominator = getattr(value, "denominator", None)
    if numerator is not None and denominator is not None:
        return {"rational": [int(numerator), int(denominator)]}
    return {"repr": type(value).__name__}


def _checksum(evidence: Mapping[str, Any]) -> str:
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
