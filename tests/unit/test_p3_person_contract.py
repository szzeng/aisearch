"""P3-03 contract rules: the versioned name key, the payload identity and the limits.

ADR-0006 sections 2/5/7/9 is the authority here. These cases are pure: they never need a
catalog, a face row or a model, so a change to the name normalisation, the idempotency
digest or a bound shows up as a one-line failure instead of hiding inside a fixture.
"""

from __future__ import annotations

import hashlib
import sqlite3

import pytest

from photo_search.catalog.errors import (
    MAX_PERSON_BATCH_SIZE,
    PERSON_BATCH_TOO_LARGE,
    PERSON_NAME_INVALID,
    PERSON_NOT_FOUND,
    PERSON_REDIRECT_CYCLE,
    CatalogError,
)
from photo_search.persons import contracts
from photo_search.persons.identity import resolve_canonical_person_id


def test_the_name_key_is_the_frozen_nfc1_prefix() -> None:
    assert contracts.NAME_KEY_VERSION == 1
    assert contracts.NAME_KEY_PREFIX == "nfc1:"
    assert contracts.CONTRACT_VERSION == "p3-person-contracts.v1"
    assert contracts.OPERATION_TYPES == (
        "RENAME",
        "MERGE_BY_NAME",
        "MERGE",
        "BATCH_MERGE",
        "UNDO",
    )


@pytest.mark.parametrize(
    ("raw", "display"),
    [
        ("  \u7238\u7238\t ", "\u7238\u7238"),
        ("A  B", "A B"),
        ("A\t\nB", "A B"),
        ("\u7238\u7238\u3000\u7238\u7238", "\u7238\u7238 \u7238\u7238"),
        ("Alice", "Alice"),
        ("alice", "alice"),
    ],
)
def test_a_name_is_nfc_and_whitespace_normalised_but_never_casefolded(
    raw: str, display: str
) -> None:
    """Unicode NFC plus one collapsed ASCII space, with the case kept exactly as typed."""
    normalized, key = contracts.normalize_person_name(raw)
    assert normalized == display
    assert key == f"nfc1:{display}"


def test_nfc_composition_and_nfkc_compatibility_folding_are_different() -> None:
    decomposed = "e\u0301"
    composed, composed_key = contracts.normalize_person_name(decomposed)
    assert composed == "\u00e9"
    assert composed_key == "nfc1:\u00e9"
    # NFKC would fold the full-width letter onto "A"; the frozen rule keeps them apart.
    full_width, full_width_key = contracts.normalize_person_name("\uff21")
    assert (full_width, full_width_key) == ("\uff21", "nfc1:\uff21")
    assert full_width_key != contracts.normalize_person_name("A")[1]


def test_the_unnamed_placeholder_is_an_ordinary_name_and_never_a_special_case() -> None:
    """The UI placeholder is never *submitted*; the server must not invent a second rule."""
    normalized, key = contracts.normalize_person_name("\u672a\u547d\u540d\u4eba\u7269 001")
    assert normalized == "\u672a\u547d\u540d\u4eba\u7269 001"
    assert key == "nfc1:\u672a\u547d\u540d\u4eba\u7269 001"


@pytest.mark.parametrize("raw", ["", "   ", "\t\n", None, 7, b"dad"])
def test_an_empty_or_non_string_name_is_a_validation_error(raw: object) -> None:
    with pytest.raises(CatalogError) as error:
        contracts.normalize_person_name(raw)
    assert error.value.code == PERSON_NAME_INVALID


def test_an_over_long_name_is_refused_by_the_documented_bound() -> None:
    with pytest.raises(CatalogError) as error:
        contracts.normalize_person_name("a" * (contracts.MAX_PERSON_NAME_CHARS + 1))
    assert error.value.code == PERSON_NAME_INVALID
    assert error.value.details["length"] == contracts.MAX_PERSON_NAME_CHARS + 1
    accepted, _ = contracts.normalize_person_name("a" * contracts.MAX_PERSON_NAME_CHARS)
    assert len(accepted) == contracts.MAX_PERSON_NAME_CHARS


def test_the_operation_id_is_derived_from_the_request_id() -> None:
    expected = hashlib.sha256("person-operation\0request-1".encode("utf-8")).hexdigest()
    assert contracts.operation_id_for("request-1") == expected
    assert contracts.operation_id_for("request-1") == contracts.operation_id_for("request-1")
    assert contracts.operation_id_for("request-1") != contracts.operation_id_for("request-2")


def test_the_payload_digest_is_order_independent_for_the_same_intent() -> None:
    first = contracts.payload_sha256({"type": "MERGE", "sources": [1, 2], "target": 3})
    second = contracts.payload_sha256({"target": 3, "sources": [1, 2], "type": "MERGE"})
    assert first == second
    assert first != contracts.payload_sha256({"type": "MERGE", "sources": [2, 1], "target": 3})
    assert len(first) == 64


@pytest.mark.parametrize("raw", ["", "   ", "bad id", "a" * 65, 12, None])
def test_a_request_id_must_be_bounded_printable_ascii(raw: object) -> None:
    with pytest.raises(CatalogError) as error:
        contracts.check_request_id(raw)
    assert error.value.code == "VALIDATION_ERROR"
    assert contracts.check_request_id("  op-1  ") == "op-1"


def test_merge_sources_are_sorted_deduplicated_and_bounded() -> None:
    normalized = contracts.check_merge_sources(
        [
            {"person_id": 9, "expected_revision": 2},
            {"person_id": 4, "expected_revision": 1},
        ]
    )
    assert normalized == [
        {"person_id": 4, "expected_revision": 1},
        {"person_id": 9, "expected_revision": 2},
    ]
    with pytest.raises(CatalogError) as duplicate:
        contracts.check_merge_sources(
            [
                {"person_id": 4, "expected_revision": 1},
                {"person_id": 4, "expected_revision": 1},
            ]
        )
    assert duplicate.value.code == "VALIDATION_ERROR"
    assert duplicate.value.details["person_ids"] == [4]
    with pytest.raises(CatalogError) as empty:
        contracts.check_merge_sources([])
    assert empty.value.code == "VALIDATION_ERROR"


def test_a_merge_batch_over_the_frozen_maximum_is_refused() -> None:
    with pytest.raises(CatalogError) as error:
        contracts.check_merge_sources(
            [
                {"person_id": index + 1, "expected_revision": 1}
                for index in range(MAX_PERSON_BATCH_SIZE + 1)
            ]
        )
    assert error.value.code == PERSON_BATCH_TOO_LARGE
    assert error.value.details["max_batch_size"] == MAX_PERSON_BATCH_SIZE


def test_the_person_view_carries_no_name_key_path_or_vector() -> None:
    row = {
        "person_id": 7,
        "name": "\u7238\u7238",
        "avatar_face_id": None,
        "merged_into": None,
        "revision": 3,
        "created_at": 10,
        "updated_at": 20,
    }
    view = contracts.person_view(
        row,
        requested_person_id=2,
        canonical_person_id=7,
        aliases=[{"alias": "\u8001\u7238", "normalized_alias": "\u8001\u7238"}],
        face_count=4,
        asset_count=3,
    )
    assert view["requested_person_id"] == 2
    assert view["canonical_person_id"] == 7
    assert view["redirected"] is True
    assert view["face_count"] == 4 and view["asset_count"] == 3
    assert set(view) == {
        "contract_version",
        "requested_person_id",
        "canonical_person_id",
        "redirected",
        "person_id",
        "name",
        "avatar_face_id",
        "revision",
        "aliases",
        "face_count",
        "asset_count",
        "created_at",
        "updated_at",
    }
    serialized = contracts.canonical_json(view)
    for forbidden in ("name_key", "vector", "bbox", "landmark", "score", "C:\\"):
        assert forbidden not in serialized


def test_the_frozen_codes_and_pending_reasons_are_the_documented_ones() -> None:
    assert contracts.PENDING_CONFIRMATION_REASONS == ("MANUAL_UNASSIGNED", "HUMAN_CONFLICT")
    assert contracts.CONTROL_MODES == (
        "AUTO_ALLOWED",
        "MANUAL_ASSIGNED",
        "MANUAL_UNASSIGNED",
    )
    assert contracts.WRITE_OPERATION_TYPES == (
        "RENAME",
        "MERGE_BY_NAME",
        "MERGE",
        "BATCH_MERGE",
    )
    assert contracts.CAUSE_PERSON_CHANGED == "PERSON_CHANGED"


# ---------------------------------------------------------------------- redirect resolver
def _persons_table(rows: list[tuple[int, int | None]]) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE persons(person_id INTEGER PRIMARY KEY, name TEXT, name_key TEXT, "
        "avatar_face_id TEXT, merged_into INTEGER, revision INTEGER NOT NULL, "
        "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
    )
    connection.executemany(
        "INSERT INTO persons(person_id, revision, created_at, updated_at, merged_into) "
        "VALUES (?, 1, 1, 1, ?)",
        rows,
    )
    return connection


def test_a_multi_hop_redirect_resolves_to_the_active_person() -> None:
    connection = _persons_table([(1, 2), (2, 3), (3, None), (4, None)])
    try:
        assert resolve_canonical_person_id(connection, 1) == 3
        assert resolve_canonical_person_id(connection, 2) == 3
        assert resolve_canonical_person_id(connection, 3) == 3
        assert resolve_canonical_person_id(connection, 4) == 4
        with pytest.raises(CatalogError) as missing:
            resolve_canonical_person_id(connection, 99)
        assert missing.value.code == PERSON_NOT_FOUND
    finally:
        connection.close()


def test_a_redirect_cycle_is_refused_instead_of_guessed() -> None:
    connection = _persons_table([(1, 2), (2, 1)])
    try:
        with pytest.raises(CatalogError) as error:
            resolve_canonical_person_id(connection, 1)
        assert error.value.code == PERSON_REDIRECT_CYCLE
        assert error.value.details["person_id"] == 1
    finally:
        connection.close()


def test_a_chain_that_does_not_end_at_an_active_person_is_refused() -> None:
    """The visited set and the row-count bound are two guards against a corrupt chain."""
    connection = _persons_table([(1, 2), (2, 3), (3, 2)])
    try:
        with pytest.raises(CatalogError) as error:
            resolve_canonical_person_id(connection, 1)
        assert error.value.code == PERSON_REDIRECT_CYCLE
    finally:
        connection.close()
