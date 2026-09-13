"""P2-03A: the frozen GPS fact branches, with no file system involved.

The numeric rules are the ones ADR-0005 §3/§12 fix: ``(0,0)`` is a real coordinate, an
illegal denominator or a non-finite value is INVALID, an unknown datum keeps the raw
evidence but produces no searchable coordinate, and a missing GPS block is a successful
empty result rather than a failure.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from photo_search.catalog.errors import ARTIFACT_INVALID, GEO_COORDINATE_INVALID, CatalogError
from photo_search.geo import exif as geo_exif
from photo_search.geo.exif import analyze

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = json.loads(
    (ROOT / "docs" / "decisions" / "contracts" / "p2-place-contracts.v1.json").read_text(
        encoding="utf-8"
    )
)

RAW_GPS_KEYS = set(CONTRACT["$defs"]["RawGps"]["required"])
NORMALIZED_KEYS = set(CONTRACT["$defs"]["Wgs84Coordinate"]["required"])


class Rational:
    """A minimal EXIF rational, including the illegal denominator case."""

    def __init__(self, numerator: int, denominator: int) -> None:
        self.numerator = numerator
        self.denominator = denominator


def gps(
    *,
    lat: tuple = (30, 15, 0),
    lat_ref: object = "N",
    lon: tuple = (120, 10, 0),
    lon_ref: object = "E",
    datum: object = "WGS84",
    altitude: object = None,
    altitude_ref: object = None,
    direction: object = None,
    accuracy: object = None,
) -> dict[int, object]:
    fields: dict[int, object] = {
        geo_exif.GPS_LATITUDE_REF: lat_ref,
        geo_exif.GPS_LATITUDE: lat,
        geo_exif.GPS_LONGITUDE_REF: lon_ref,
        geo_exif.GPS_LONGITUDE: lon,
    }
    if datum is not None:
        fields[geo_exif.GPS_MAP_DATUM] = datum
    if altitude is not None:
        fields[geo_exif.GPS_ALTITUDE] = altitude
    if altitude_ref is not None:
        fields[geo_exif.GPS_ALTITUDE_REF] = altitude_ref
    if direction is not None:
        fields[geo_exif.GPS_IMG_DIRECTION] = direction
    if accuracy is not None:
        fields[geo_exif.GPS_HPOSITIONING_ERROR] = accuracy
    return fields


def test_no_gps_block_is_a_successful_empty_result() -> None:
    result = analyze(None)
    assert result.status == "ABSENT"
    assert result.datum_status == "ABSENT"
    assert result.raw_gps is None and result.normalized_wgs84 is None
    assert analyze({}).status == "ABSENT"
    assert len(result.raw_fields_checksum_sha256) == 64


def test_zero_coordinates_are_legal_and_not_a_missing_sentinel() -> None:
    result = analyze(gps(lat=(0, 0, 0), lon=(0, 0, 0)))
    assert result.status == "VALID"
    assert result.normalized_wgs84["latitude"] == 0.0
    assert result.normalized_wgs84["longitude"] == 0.0
    assert result.datum_status == "WGS84"


def test_southern_and_western_hemispheres_are_signed() -> None:
    result = analyze(gps(lat=(33, 51, 36), lat_ref="S", lon=(70, 40, 12), lon_ref="W"))
    assert result.status == "VALID"
    latitude = result.normalized_wgs84["latitude"]
    longitude = result.normalized_wgs84["longitude"]
    assert latitude == pytest.approx(-33.86, abs=1e-6)
    assert longitude == pytest.approx(-70.67, abs=1e-6)


def test_a_missing_datum_is_only_an_assumption() -> None:
    result = analyze(gps(datum=None))
    assert result.status == "VALID"
    assert result.datum_status == "ASSUMED_WGS84"
    assert result.raw_gps["map_datum"] is None


def test_an_unknown_datum_keeps_evidence_but_no_searchable_coordinate() -> None:
    result = analyze(gps(datum="GCJ-02"))
    assert result.status == "UNSUPPORTED_CRS"
    assert result.datum_status == "UNSUPPORTED"
    assert result.raw_gps is not None
    assert result.raw_gps["map_datum"] == "GCJ-02"
    assert result.normalized_wgs84 is None


def test_illegal_denominator_non_finite_and_out_of_range_are_invalid() -> None:
    illegal = analyze(gps(lat=(30, 15, Rational(1, 0))))
    assert illegal.status == "INVALID" and illegal.normalized_wgs84 is None

    infinite = analyze(gps(lat=(float("inf"), 0, 0)))
    assert infinite.status == "INVALID"
    assert analyze(gps(lat=(float("nan"), 0, 0))).status == "INVALID"

    for label, fields in {
        "latitude above 90": gps(lat=(91, 0, 0)),
        "longitude above 180": gps(lon=(181, 0, 0)),
        "negative minutes": gps(lat=(30, -1, 0)),
    }.items():
        result = analyze(fields)
        assert result.status == "INVALID", label
        assert result.normalized_wgs84 is None, label


def test_an_unusable_reference_direction_is_invalid() -> None:
    result = analyze(gps(lat_ref="X"))
    assert result.status == "INVALID"
    # The frozen RawGps shape cannot express an unknown reference, so it is not fabricated.
    assert result.raw_gps is None
    assert result.normalized_wgs84 is None


def test_optional_metadata_is_kept_or_left_null_but_never_invented() -> None:
    result = analyze(
        gps(
            altitude=Rational(15, 1),
            altitude_ref=Rational(1, 1),
            direction=Rational(90, 1),
            accuracy=Rational(5, 1),
        )
    )
    assert result.status == "VALID"
    assert result.raw_gps["altitude_ref"] == "BELOW_SEA_LEVEL"
    assert result.raw_gps["altitude_m"] == -15.0
    assert result.raw_gps["direction_deg"] == 90.0
    assert result.raw_gps["accuracy_m"] == 5.0
    assert result.normalized_wgs84["altitude_m"] == -15.0

    bare = analyze(gps())
    assert bare.raw_gps["altitude_m"] is None
    assert bare.raw_gps["altitude_ref"] is None
    assert bare.raw_gps["direction_deg"] is None
    assert bare.raw_gps["accuracy_m"] is None
    # A bogus direction/accuracy value is dropped rather than reported as "precise".
    odd = analyze(gps(direction=Rational(400, 1), accuracy=Rational(-2, 1)))
    assert odd.raw_gps["direction_deg"] is None and odd.raw_gps["accuracy_m"] is None


def test_the_raw_and_normalised_shapes_match_the_frozen_schema() -> None:
    result = analyze(gps(altitude=Rational(10, 1), altitude_ref=Rational(0, 1)))
    assert set(result.raw_gps) == RAW_GPS_KEYS
    assert set(result.normalized_wgs84) == NORMALIZED_KEYS
    assert result.normalized_wgs84["datum"] == "WGS84"
    assert all(math.isfinite(float(result.normalized_wgs84[key])) for key in ("latitude", "longitude"))
    assert result.raw_gps["latitude_ref"] in ("N", "S")
    assert result.raw_gps["longitude_ref"] in ("E", "W")


def test_the_raw_checksum_changes_with_the_evidence() -> None:
    first = analyze(gps(lat=(30, 15, 0)))
    second = analyze(gps(lat=(30, 15, 30)))
    assert first.raw_fields_checksum_sha256 != second.raw_fields_checksum_sha256
    assert analyze(gps(lat=(30, 15, 0))).raw_fields_checksum_sha256 == (
        first.raw_fields_checksum_sha256
    )


# ---------------------------------------------------------------- payload validation
def test_validate_payload_rejects_a_broken_fact() -> None:
    from photo_search.geo.extract import build_payload, validate_payload

    good = build_payload(input_revision="a" * 64, location_id=1, extraction=analyze(gps()))
    validate_payload(good)

    extra = dict(good, note="hello")
    with pytest.raises(CatalogError) as extra_key:
        validate_payload(extra)
    assert extra_key.value.code == ARTIFACT_INVALID

    absent = build_payload(input_revision="a" * 64, location_id=None, extraction=analyze(None))
    validate_payload(absent)
    with pytest.raises(CatalogError):
        validate_payload(dict(absent, normalized_wgs84={"latitude": 1.0, "longitude": 2.0}))

    non_finite = dict(
        good,
        status="VALID",
        normalized_wgs84={
            "datum": "WGS84",
            "latitude": float("nan"),
            "longitude": 1.0,
            "altitude_m": None,
            "direction_deg": None,
            "accuracy_m": None,
        },
    )
    with pytest.raises(CatalogError) as not_finite:
        validate_payload(non_finite)
    assert not_finite.value.code == GEO_COORDINATE_INVALID

    # SOURCE_UNAVAILABLE is a blocked execution result, never a stored current fact.
    with pytest.raises(CatalogError) as blocked_status:
        validate_payload(dict(absent, status="SOURCE_UNAVAILABLE"))
    assert blocked_status.value.code == ARTIFACT_INVALID


def test_the_gps_processor_spec_is_a_non_model_cpu_processor() -> None:
    from photo_search.geo.extract import gps_processor_spec

    spec = gps_processor_spec()
    assert spec.processor_kind == "non_model" and spec.is_non_model
    assert spec.device_policy == "CPU_PREFERRED"
    assert spec.supported_devices == ("cpu",)
    # No model, weights, dimension, metric or space may be fabricated.
    for key in ("model_id", "weights", "dimension", "metric", "space_id"):
        assert key not in spec.envelope
