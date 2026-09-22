"""P4-00B unit evidence: the frozen vocabulary, grammar, codec and predicate.

The P4-00A contract test already pins the *reference* semantics of ADR-0011. This file pins
the production implementation of the same rules, so a drift between the two - or a silent
widening of the closed sets - fails here.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pytest
from p4_time_fixtures import (
    TAG_DATETIME_ORIGINAL,
    TAG_OFFSET_TIME_ORIGINAL,
    TAG_SUBSEC_TIME_ORIGINAL,
    write_exif_jpeg,
)

from photo_search.catalog.errors import ARTIFACT_INVALID, INVALID_TIME_RANGE, CatalogError
from photo_search.taken_time import contract
from photo_search.taken_time import exif as exif_module
from photo_search.taken_time import extract as time_extract
from photo_search.taken_time import read as time_read


# --------------------------------------------------------------------- the civil key
def test_the_civil_key_is_the_frozen_wall_clock_formula() -> None:
    assert contract.civil_key(1, 1, 1) == 0
    assert contract.civil_key(1, 1, 2) == 86_400_000
    assert contract.civil_key(2022, 7, 5, 18, 31, 7) == (
        (dt.date(2022, 7, 5).toordinal() - 1) * 86_400_000
        + (18 * 3600 + 31 * 60 + 7) * 1000
    )
    assert contract.civil_key(9999, 12, 31, 23, 59, 59, 999) == contract.MAX_SORT_KEY
    # The offset never participates: the same wall clock is the same key either way.
    assert contract.civil_key(2022, 7, 5, 18, 31, 7, 123) == contract.civil_key(
        2022, 7, 5, 18, 31, 7, 123
    )
    with pytest.raises(ValueError):
        contract.civil_key(2022, 2, 30)


def test_the_bound_grammar_accepts_only_the_frozen_spellings() -> None:
    day = contract.parse_bound("2022-01-01")
    assert day == contract.civil_key(2022, 1, 1)
    assert contract.parse_bound("2022-01-01T00:00:00") == day
    assert contract.parse_bound("2022-01-01T00:00:00.5") == day + 500
    assert contract.parse_bound("2022-01-01T00:00:00.50") == day + 500
    assert contract.parse_bound("2022-01-01T00:00:00.500") == day + 500
    assert contract.normalize_bound(day + 500) == "2022-01-01T00:00:00.500"
    assert contract.normalize_bound(contract.parse_bound("0001-01-01")) == (
        "0001-01-01T00:00:00.000"
    )
    assert contract.normalize_bound(contract.parse_bound("9999-12-31T23:59:59.999")) == (
        "9999-12-31T23:59:59.999"
    )


@pytest.mark.parametrize(
    ("raw", "reason"),
    (
        ("2022-01-01T00:00:00Z", contract.REASON_BOUND_HAS_OFFSET),
        ("2022-01-01T00:00:00z", contract.REASON_BOUND_HAS_OFFSET),
        ("2022-01-01T00:00:00+08:00", contract.REASON_BOUND_HAS_OFFSET),
        ("2022-01-01T00:00:00-0530", contract.REASON_BOUND_HAS_OFFSET),
        ("2022/01/01", contract.REASON_BOUND_MALFORMED),
        ("20220101", contract.REASON_BOUND_MALFORMED),
        ("2022-1-1", contract.REASON_BOUND_MALFORMED),
        (" 2022-01-01", contract.REASON_BOUND_MALFORMED),
        ("2022-01-01 ", contract.REASON_BOUND_MALFORMED),
        ("", contract.REASON_BOUND_MALFORMED),
        ("2022-01-01T00:00:00.1234", contract.REASON_BOUND_MALFORMED),
        ("2022-02-30", contract.REASON_BOUND_MALFORMED),
        ("2022-13-01", contract.REASON_BOUND_MALFORMED),
        ("2022-01-01T24:00:00", contract.REASON_BOUND_MALFORMED),
        ("2022-01-01T00:60:00", contract.REASON_BOUND_MALFORMED),
        ("0000-01-01", contract.REASON_BOUND_OUT_OF_RANGE),
    ),
)
def test_every_refused_bound_keeps_its_frozen_reason(raw: str, reason: str) -> None:
    with pytest.raises(CatalogError) as error:
        contract.parse_bound(raw, field=contract.FIELD_FROM)
    assert error.value.code == INVALID_TIME_RANGE
    assert error.value.details == {"reason": reason, "field": contract.FIELD_FROM}


def test_the_range_is_half_open_and_refuses_an_empty_interval() -> None:
    nothing = contract.parse_capture_range()
    assert nothing is None
    one_sided = contract.parse_capture_range("2022-01-01", None)
    assert one_sided is not None and one_sided.from_key is not None
    assert one_sided.to_key is None and one_sided.to_text is None
    assert one_sided.from_text == "2022-01-01T00:00:00.000"
    with pytest.raises(CatalogError) as empty:
        contract.parse_capture_range("2022-01-01", "2022-01-01T00:00:00.000")
    assert empty.value.details == {
        "reason": contract.REASON_RANGE_NOT_ASCENDING,
        "field": contract.FIELD_FROM,
    }
    with pytest.raises(CatalogError) as descending:
        contract.parse_capture_range("2023-01-01", "2022-01-01")
    assert descending.value.details["reason"] == contract.REASON_RANGE_NOT_ASCENDING
    echo = contract.capture_echo(one_sided)
    assert echo == {
        "capture_from": "2022-01-01T00:00:00.000",
        "capture_to_exclusive": None,
    }
    assert contract.capture_echo(None) == {
        "capture_from": None,
        "capture_to_exclusive": None,
    }


# -------------------------------------------------------------- the EXIF time fields
def test_the_reader_walks_the_exif_sub_ifd_and_the_root_ifd(tmp_path: Path) -> None:
    target = write_exif_jpeg(
        tmp_path / "合成 照片.jpg",
        root="2021:05:06 07:08:09",
        original="2022:07:05 18:31:07",
        offset_original="+08:00",
        subsec_original="12345",
        make="TestMake",
        model="TestModel",
    )
    fields = exif_module.read_time_fields(target)
    assert fields.original == "2022:07:05 18:31:07"
    assert fields.original_offset == "+08:00"
    assert fields.original_subsec == "12345"
    assert fields.root == "2021:05:06 07:08:09"
    assert fields.camera == "TestMake TestModel"
    assert exif_module.parse_exif_datetime(fields.original) == contract.civil_key(
        2022, 7, 5, 18, 31, 7
    )
    assert exif_module.parse_offset_minutes(fields.original_offset) == 480
    assert exif_module.parse_subsec_millis(fields.original_subsec) == 123
    assert TAG_DATETIME_ORIGINAL == 0x9003
    assert TAG_OFFSET_TIME_ORIGINAL == 0x9011
    assert TAG_SUBSEC_TIME_ORIGINAL == 0x9291


@pytest.mark.parametrize(
    ("raw", "minutes"),
    (
        ("+08:00", 480),
        ("+0800", 480),
        ("-05:30", -330),
        ("-0530", -330),
        ("+14:00", 840),
        ("-12:00", -720),
        ("+08", 480),
        (None, None),
        ("", None),
        ("8:00", None),
        ("+08:00:00", None),
        ("+15:00", None),
        ("-12:01", None),
        ("nonsense", None),
    ),
)
def test_the_recorded_offset_is_read_or_dropped(raw: object, minutes: int | None) -> None:
    assert exif_module.parse_offset_minutes(raw) == minutes


@pytest.mark.parametrize(
    ("raw", "millis"),
    (("1", 100), ("12", 120), ("123", 123), ("123456", 123), ("abc", 0), (None, 0), ("", 0)),
)
def test_sub_seconds_take_the_first_three_digits(raw: object, millis: int) -> None:
    assert exif_module.parse_subsec_millis(raw) == millis


@pytest.mark.parametrize(
    "raw",
    (
        # The EXIF spelling is accepted with either separator and with surrounding blanks
        # (Pillow hands back exactly what the file declares).
        "2022:07:05 18:31:07",
        "2022:07:05 18:31:07 ",
        "2022:07:05T18:31:07",
    ),
)
def test_the_exif_spelling_is_parsed(raw: str) -> None:
    assert exif_module.parse_exif_datetime(raw) == contract.civil_key(2022, 7, 5, 18, 31, 7)


@pytest.mark.parametrize(
    "raw",
    (
        "2022-07-05 18:31:07",
        "2022:02:30 10:00:00",
        "2022:13:01 10:00:00",
        "2022:07:05 24:00:00",
        "2022:07:05",
        "nonsense",
        "",
        None,
    ),
)
def test_a_bad_exif_calendar_produces_no_key(raw: object) -> None:
    assert exif_module.parse_exif_datetime(raw) is None


def test_the_confidence_matrix_is_the_frozen_one() -> None:
    assert exif_module.confidence_for("EXIF_ORIGINAL", 480) == "HIGH"
    assert exif_module.confidence_for("EXIF_ORIGINAL", None) == "MEDIUM"
    assert exif_module.confidence_for("EXIF_DIGITIZED", 480) == "MEDIUM"
    assert exif_module.confidence_for("EXIF_DIGITIZED", None) == "MEDIUM"
    assert exif_module.confidence_for("EXIF_ROOT", 480) == "LOW"
    assert exif_module.confidence_for("FILE_MTIME", None) == "LOW"
    assert contract.label_for("EXIF_ORIGINAL") == "capture"
    assert contract.label_for("EXIF_ROOT") == "file_time"
    assert contract.participates_in_capture_filter("EXIF_DIGITIZED") is True
    assert contract.participates_in_capture_filter("EXIF_ROOT") is False
    assert contract.sort_bucket("EXIF_ROOT") == "ordered"
    assert contract.sort_bucket("FILE_MTIME") == "tail"


def test_the_camera_is_normalized_and_bounded() -> None:
    assert contract.clamp_camera(None) is None
    assert contract.clamp_camera("   ") is None
    assert contract.clamp_camera("  Canon   EOS\nR6  ") == "Canon EOS R6"
    assert contract.clamp_camera("Can\x00on\tR6") == "Canon R6"
    assert len(contract.clamp_camera("x" * 400) or "") == 128


# ----------------------------------------------------------------- the honesty block
def test_the_state_is_the_pure_function_of_the_three_counts() -> None:
    cases = (
        (0, 0, 0, "READY"),
        (10, 0, 0, "PENDING"),
        (10, 4, 2, "PARTIAL"),
        (10, 10, 1, "READY"),
        (10, 10, 0, "NO_CAPTURE_TIME"),
    )
    for total, extracted, captured, expected in cases:
        block = contract.taken_time_block(
            assets_total=total,
            extracted_assets=extracted,
            capture_time_assets=captured,
            rules_version_matches=True,
        )
        assert block == {
            "state": expected,
            "assets_total": total,
            "extracted_assets": extracted,
            "capture_time_assets": captured,
        }
        assert block["state"] == contract.taken_time_state(total, extracted, captured)
    mismatched = contract.taken_time_block(
        assets_total=10,
        extracted_assets=10,
        capture_time_assets=7,
        rules_version_matches=False,
    )
    assert mismatched == {
        "state": "PENDING",
        "assets_total": 10,
        "extracted_assets": 0,
        "capture_time_assets": 0,
    }


# ------------------------------------------------------------------- the payload shape
def legal_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "contract_version": contract.CONTRACT_VERSION,
        "processor_id": time_extract.TAKEN_TIME_PROCESSOR_ID,
        "asset_id": 1,
        "location_id": 2,
        "taken_at_raw": "2022:07:05 18:31:07",
        "taken_at_sort": contract.civil_key(2022, 7, 5, 18, 31, 7, 123),
        "time_source": "EXIF_ORIGINAL",
        "time_confidence": "HIGH",
        "timezone_offset": 480,
        "camera": "Canon EOS R6",
    }
    payload.update(overrides)
    return payload


def test_the_payload_validation_closes_the_fact_shape() -> None:
    time_extract.validate_payload(legal_payload())
    for broken in (
        legal_payload(contract_version="p3-person-search-contracts.v1"),
        legal_payload(processor_id="another-processor@1"),
        legal_payload(time_source="EXIF_GPS"),
        legal_payload(time_source="SCAN_TIME"),
        legal_payload(time_confidence="VERY_HIGH"),
        legal_payload(time_confidence="LOW"),
        legal_payload(timezone_offset=900),
        legal_payload(timezone_offset=None),
        legal_payload(taken_at_sort=None),
        legal_payload(taken_at_sort=-1),
        legal_payload(taken_at_raw=""),
        legal_payload(camera="x" * 129),
        {key: value for key, value in legal_payload().items() if key != "camera"},
        {**legal_payload(), "taken_at_utc": "2022-07-05T18:31:07Z"},
    ):
        with pytest.raises(CatalogError) as error:
            time_extract.validate_payload(broken)
        assert error.value.code == ARTIFACT_INVALID
    # The file-time row is the exact opposite shape: no civil key, no offset, never HIGH.
    file_time = legal_payload(
        taken_at_raw="2022-07-05T18:31:07.000000+00:00",
        taken_at_sort=None,
        time_source="FILE_MTIME",
        time_confidence="LOW",
        timezone_offset=None,
    )
    time_extract.validate_payload(file_time)
    for broken in (
        {**file_time, "taken_at_sort": 1},
        {**file_time, "timezone_offset": 0},
        {**file_time, "time_confidence": "MEDIUM"},
    ):
        with pytest.raises(CatalogError):
            time_extract.validate_payload(broken)


# -------------------------------------------------------------- the SQLite predicate
def _time_connection(rules_version: int | None) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);"
        "CREATE TABLE assets (asset_id INTEGER PRIMARY KEY);"
        "CREATE TABLE asset_metadata (asset_id INTEGER PRIMARY KEY, taken_at_sort INTEGER, "
        "time_source TEXT);"
    )
    if rules_version is not None:
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            (contract.RULES_VERSION_KEY, str(rules_version)),
        )
    return connection


def test_the_capture_predicate_is_the_single_authoritative_fragment() -> None:
    connection = _time_connection(contract.RULES_VERSION)
    try:
        assert time_read.capture_predicate(connection, None) == ("1", [])
        bare = contract.parse_capture_range("2022-01-01", None)
        sql, parameters = time_read.capture_predicate(connection, bare, alias="a")
        assert "asset_metadata AS m" in sql
        assert "time_source IN (?, ?)" in sql
        assert "m.taken_at_sort >= ?" in sql and "m.taken_at_sort < ?" not in sql
        assert parameters == list(contract.CAPTURE_SOURCES) + [
            contract.parse_bound("2022-01-01")
        ]
        full = contract.parse_capture_range("2022-01-01", "2023-01-01")
        sql, parameters = time_read.capture_predicate(connection, full)
        assert "m.taken_at_sort < ?" in sql
        assert parameters[-2:] == [
            contract.parse_bound("2022-01-01"),
            contract.parse_bound("2023-01-01"),
        ]
        assert time_read.rules_version(connection) == contract.RULES_VERSION
        assert time_read.rules_version_matches(connection) is True
        assert time_read.filter_active(connection) is True
    finally:
        connection.close()


def test_an_untrusted_rule_version_makes_the_condition_unsatisfiable() -> None:
    for stored in (None, 2):
        connection = _time_connection(stored)
        try:
            assert time_read.rules_version_matches(connection) is False
            assert time_read.filter_active(connection) is False
            sql, parameters = time_read.capture_predicate(
                connection, contract.parse_capture_range("2022-01-01", None)
            )
            assert (sql, parameters) == ("0", [])
        finally:
            connection.close()


def test_the_capture_condition_joins_the_same_and_set_as_every_other_condition() -> None:
    """ADR-0011 §6.5: one scope, one predicate - source, person, capture and place together."""
    from photo_search.persons.members import MEMBER_PREDICATE
    from photo_search.search.retrieval import SearchScope, capture_condition, scope_condition

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);"
        "CREATE TABLE assets (asset_id INTEGER PRIMARY KEY);"
        "CREATE TABLE file_locations (asset_id INTEGER, source_id INTEGER, availability TEXT);"
        "CREATE TABLE asset_metadata (asset_id INTEGER PRIMARY KEY, taken_at_sort INTEGER, "
        "time_source TEXT);"
        "CREATE TABLE asset_place_assignments (asset_id INTEGER, mode TEXT, place_id INTEGER);"
        "CREATE TABLE faces (face_id TEXT PRIMARY KEY, asset_id INTEGER);"
        "CREATE TABLE face_person_links (face_id TEXT, person_id INTEGER, decision TEXT, "
        "assignment_kind TEXT, revision INTEGER);"
        "CREATE TABLE catalog_filter_revision (revision_id INTEGER PRIMARY KEY, revision INTEGER);"
        "CREATE TABLE place_suggestion_revision (revision_id INTEGER PRIMARY KEY, "
        "revision INTEGER);"
    )
    connection.execute(
        "INSERT INTO metadata(key, value) VALUES (?, '1')", (contract.RULES_VERSION_KEY,)
    )
    try:
        capture = contract.parse_capture_range("2022-01-01", "2023-01-01")
        scope = SearchScope.build(
            source_id=4,
            availability="present",
            place_state="assigned",
            person_ids=[7],
            capture=capture,
        )
        sql, parameters = scope_condition(connection, scope)
        assert "l.source_id = ?" in sql
        assert "l.availability = 'PRESENT'" in sql
        assert "EXISTS (SELECT 1 FROM asset_metadata AS m" in sql
        assert "ap.mode = 'MANUAL_ASSIGNED'" in sql
        assert MEMBER_PREDICATE in sql
        # The parameter order follows the SQL text: source, person, capture, place.
        assert parameters == [4, 7, *contract.CAPTURE_SOURCES,
                              contract.parse_bound("2022-01-01"),
                              contract.parse_bound("2023-01-01")]
        # The same fragment is what the asset listing applies - one predicate, two surfaces.
        predicate, predicate_parameters = capture_condition(connection, scope)
        assert predicate in sql and predicate_parameters == parameters[2:6]
    finally:
        connection.close()
