"""P3-07B unit evidence: the frozen person filter shape, scope and continuation digest.

These cases stay at the transport-free layer: the ``SearchRequest`` envelope, the
authoritative ``SearchScope`` predicate and the pydantic model the HTTP layer publishes.
The end-to-end matrix (canonical resolution, the AND combinations, sessions and the real
fixture) lives in ``tests/integration/test_p3_person_search_filter.py``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from photo_search.api.errors import status_for_code
from photo_search.api.models import SearchRequestModel
from photo_search.catalog.errors import INVALID_PERSON_FILTER, CatalogError
from photo_search.persons.members import MEMBER_PREDICATE, member_counts
from photo_search.search.contracts import MAX_PERSON_IDS, SearchRequest
from photo_search.search.retrieval import (
    SearchScope,
    person_condition,
    scope_condition,
    scope_view,
)
from photo_search.search.sessions import query_hash
from photo_search.search.tokens import VALIDATION_ERROR

ROOT = Path(__file__).resolve().parents[2]
COMPANION_PATH = ROOT / "docs" / "decisions" / "contracts" / "p3-person-search-contracts.v1.json"
COMPANION = json.loads(COMPANION_PATH.read_text(encoding="utf-8"))


def envelope(**overrides: object) -> dict[str, object]:
    return {"contract_version": "p2-contracts.v1", "request_id": "req-1", **overrides}


def refusal(payload: dict[str, object]) -> CatalogError:
    with pytest.raises(CatalogError) as error:
        SearchRequest.from_envelope(payload)
    return error.value


# ------------------------------------------------------------------ request shape
def test_absent_and_empty_person_conditions_are_legal_and_mean_no_condition() -> None:
    for overrides in ({}, {"person_ids": []}, {"person_ids": [], "person_match": "any"}):
        request = SearchRequest.from_envelope(envelope(**overrides))
        assert request.person_ids == ()
        assert request.person_match == "any"
    # ``person_match: any`` without ids is the same statement as no person field at all.
    assert SearchRequest.from_envelope(envelope(person_match="any")).person_ids == ()


def test_person_match_all_without_ids_is_the_frozen_invalid_person_filter() -> None:
    for overrides in ({"person_match": "all"}, {"person_ids": [], "person_match": "all"}):
        error = refusal(envelope(**overrides))
        assert error.code == INVALID_PERSON_FILTER
        assert error.details == {
            "reason": "person_match_all_requires_ids",
            "person_match": "all",
        }
    assert SearchRequest.from_envelope(
        envelope(person_ids=[7], person_match="all")
    ).person_ids == (7,)


@pytest.mark.parametrize(
    "person_ids",
    ["3", 3, {"0": 3}, [0], [-1], [True], [1.5], ["3"], [None], [3, 3]],
    ids=[
        "string",
        "number",
        "object",
        "zero",
        "negative",
        "boolean",
        "float",
        "numeric-string",
        "null",
        "raw-duplicate",
    ],
)
def test_person_ids_shape_errors_are_validation_errors(person_ids: object) -> None:
    error = refusal(envelope(person_ids=person_ids))
    assert error.code == VALIDATION_ERROR


def test_the_person_id_bound_is_exactly_one_hundred_and_names_the_ceiling() -> None:
    assert MAX_PERSON_IDS == 100
    assert COMPANION["$defs"]["MaxPersonFilterIds"]["const"] == MAX_PERSON_IDS
    allowed = SearchRequest.from_envelope(
        envelope(person_ids=list(range(1, MAX_PERSON_IDS + 1)))
    )
    assert len(allowed.person_ids) == MAX_PERSON_IDS
    error = refusal(envelope(person_ids=list(range(1, MAX_PERSON_IDS + 2))))
    assert error.code == INVALID_PERSON_FILTER
    assert error.details == {
        "reason": "person_filter_too_large",
        "max_person_ids": MAX_PERSON_IDS,
    }
    assert status_for_code(INVALID_PERSON_FILTER) == 422


def test_an_unknown_person_match_value_and_an_unknown_field_are_validation_errors() -> None:
    assert refusal(envelope(person_match="some")).code == VALIDATION_ERROR
    assert refusal(envelope(person_match="ANY")).code == VALIDATION_ERROR
    unexpected = refusal(envelope(surprise=True))
    assert unexpected.code == VALIDATION_ERROR
    assert unexpected.details["unexpected"] == ["surprise"]


def test_the_person_condition_round_trips_and_stays_inside_the_frozen_envelope() -> None:
    request = SearchRequest.from_envelope(
        envelope(text="sunset", person_ids=[3, 7], person_match="all")
    )
    assert request.person_ids == (3, 7) and request.person_match == "all"
    assert SearchRequest.from_envelope(request.to_envelope()) == request
    assert request.contract_version == "p2-contracts.v1"


def test_the_transport_model_refuses_non_integers_and_leaves_the_bound_to_the_envelope() -> None:
    adapter = TypeAdapter(SearchRequestModel)
    for person_ids in ([True], [1.0], ["3"], [None], "3", 3, {"0": 3}):
        with pytest.raises(ValidationError):
            adapter.validate_python({"request_id": "r", "person_ids": person_ids})
    # Over the frozen ceiling the transport model must *not* refuse: the answer has to be
    # the frozen ``INVALID_PERSON_FILTER`` naming ``max_person_ids``, not a shape error.
    model = adapter.validate_python(
        {"request_id": "r", "person_ids": list(range(1, MAX_PERSON_IDS + 2))}
    )
    assert len(model.person_ids) == MAX_PERSON_IDS + 1
    with pytest.raises(CatalogError) as error:
        SearchRequest.from_envelope(model.model_dump(exclude_none=True))
    assert error.value.code == INVALID_PERSON_FILTER


# ------------------------------------------------------------------ scope predicate
def test_the_person_predicate_reuses_the_single_member_definition() -> None:
    any_scope = SearchScope.build(person_ids=[9, 3])
    sql, parameters = person_condition(any_scope)
    assert MEMBER_PREDICATE in sql
    assert "face_person_links AS l" in sql and "JOIN faces AS f" in sql
    assert "l.person_id IN (?, ?)" in sql
    # One person sub-query for ``any``; the member fragment itself brings its own
    # ``NOT EXISTS`` clauses, so the count below is of the outer per-person queries.
    assert sql.count("FROM face_person_links AS l") == 1
    assert parameters == [3, 9]

    all_scope = SearchScope.build(person_ids=[9, 3], person_match="all")
    sql, parameters = person_condition(all_scope)
    assert sql.count("FROM face_person_links AS l") == 2
    assert " AND " in sql and " OR " not in sql
    # ``all`` is one sub-query per person, each carrying its own equality on
    # ``l.person_id``; the shared IN-list is what distinguishes ``any`` above. (P4-01C
    # rewrote both forms from a correlated EXISTS to ``asset_id IN (SELECT ...)`` for the
    # query plan, so the *shape* check moved to the per-person equality.)
    assert sql.count("l.person_id = ?") == 2
    assert "l.person_id IN (" not in sql
    assert parameters == [3, 9]

    # A scope with no person condition is the neutral predicate, never "no face".
    assert person_condition(SearchScope.build()) == ("1", [])


def test_the_scope_refuses_the_two_person_conditions_it_owns_the_code_for() -> None:
    with pytest.raises(CatalogError) as error:
        SearchScope.build(person_match="all")
    assert error.value.code == INVALID_PERSON_FILTER
    assert error.value.details["reason"] == "person_match_all_requires_ids"
    with pytest.raises(CatalogError) as error:
        SearchScope.build(person_ids=list(range(1, MAX_PERSON_IDS + 2)))
    assert error.value.details["reason"] == "person_filter_too_large"
    with pytest.raises(CatalogError) as error:
        SearchScope.build(person_match="SOME")
    assert error.value.code == VALIDATION_ERROR


def test_the_filters_echo_always_carries_the_two_frozen_person_keys() -> None:
    connection = _echo_fixture()
    try:
        empty = scope_view(connection, SearchScope.build())
        assert empty["person_ids"] == [] and empty["person_match"] == "any"
        scoped = scope_view(connection, SearchScope.build(person_ids=[7, 3]))
        assert scoped["person_ids"] == [3, 7] and scoped["person_match"] == "any"
        # The echo key set is what ADR-0009 §7.1 fixed plus - from P4-00B (ADR-0011 §8.2) -
        # exactly the two capture keys (``binding`` is added by the service, so it is checked
        # there). The P3 companion itself keeps its eleven keys; only the live echo grew.
        required = set(COMPANION["$defs"]["SearchFiltersEcho"]["required"])
        assert set(scoped) | {"binding"} == required | {
            "capture_from",
            "capture_to_exclusive",
        }
        assert scoped["capture_from"] is None and scoped["capture_to_exclusive"] is None
    finally:
        connection.close()


def test_the_person_predicate_and_the_member_count_are_the_same_set() -> None:
    connection = _member_fixture()
    try:
        scope = SearchScope.build(person_ids=[3])
        where, parameters = scope_condition(connection, scope)
        rows = connection.execute(
            f"SELECT a.asset_id FROM assets AS a WHERE {where} ORDER BY a.asset_id",
            tuple(parameters),
        ).fetchall()
        faces, assets = member_counts(connection, 3)
        assert [int(row["asset_id"]) for row in rows] == [2, 4]
        assert len(rows) == assets
        assert faces == 2
    finally:
        connection.close()


# ------------------------------------------------------------- continuation digest
def _digest(**overrides: object) -> str:
    base: dict[str, object] = {
        "text": "sunset",
        "source_id": None,
        "availability": "any",
        "place_ids": (),
        "include_descendants": True,
        "place_state": "any",
        "top_k": 200,
        "page_size": 20,
    }
    return query_hash(**{**base, **overrides})  # type: ignore[arg-type]


def test_the_continuation_digest_binds_the_canonical_person_condition() -> None:
    plain = _digest()
    any_scope = _digest(person_ids=(3,), person_match="any")
    all_scope = _digest(person_ids=(3,), person_match="all")
    other_person = _digest(person_ids=(3, 9), person_match="any")
    reordered = _digest(person_ids=(3,), person_match="any")
    assert plain != any_scope
    assert any_scope == reordered
    assert any_scope != all_scope
    assert any_scope != other_person
    # A different place/source condition still splits the digest, as before.
    assert plain != _digest(place_ids=(2,))
    assert plain != _digest(source_id=4)


# ------------------------------------------------------------------------ fixtures
def _echo_fixture() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        "CREATE TABLE catalog_filter_revision (revision_id INTEGER PRIMARY KEY, revision INTEGER);"
        "CREATE TABLE place_suggestion_revision (revision_id INTEGER PRIMARY KEY, revision INTEGER);"
        "INSERT INTO catalog_filter_revision VALUES (1, 5);"
        "INSERT INTO place_suggestion_revision VALUES (1, 2);"
    )
    return connection


def _member_fixture() -> sqlite3.Connection:
    """Two photos, four faces: one lock, one exclusion, one suggestion, one other person."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        "CREATE TABLE assets (asset_id INTEGER PRIMARY KEY);"
        "CREATE TABLE file_locations (asset_id INTEGER, source_id INTEGER, availability TEXT, relative_path TEXT);"
        "CREATE TABLE faces (face_id TEXT PRIMARY KEY, asset_id INTEGER NOT NULL, "
        "spec_revision TEXT DEFAULT 'ordinary', input_revision TEXT DEFAULT 'fixture');"
        "CREATE TABLE face_person_links (face_id TEXT, person_id INTEGER, decision TEXT, "
        "assignment_kind TEXT DEFAULT 'AUTO');"
        "CREATE TABLE face_detail_auto_assets(asset_id INTEGER, input_revision TEXT, ready INTEGER, policy_revision TEXT);"
        "CREATE TABLE face_redetection_groups(old_face_id TEXT, state TEXT);"
        "CREATE TABLE face_assignment_controls (face_id TEXT PRIMARY KEY, mode TEXT);"
        "CREATE TABLE face_exclusions (face_id TEXT, person_id INTEGER, active INTEGER);"
        "CREATE TABLE catalog_filter_revision (revision_id INTEGER PRIMARY KEY, revision INTEGER);"
        "CREATE TABLE place_suggestion_revision (revision_id INTEGER PRIMARY KEY, revision INTEGER);"
        "INSERT INTO assets VALUES (1), (2), (3), (4);"
        "INSERT INTO catalog_filter_revision VALUES (1, 1);"
        "INSERT INTO place_suggestion_revision VALUES (1, 0);"
    )
    connection.executemany(
        "INSERT INTO faces(face_id, asset_id) VALUES (?, ?)",
        [("f1", 1), ("f2", 2), ("f3", 3), ("f4", 2), ("f5", 4), ("f6", 4)],
    )
    connection.executemany(
        "INSERT INTO face_person_links(face_id, person_id, decision) VALUES (?, ?, ?)",
        [
            ("f1", 3, "CONFIRMED"),
            ("f2", 3, "CONFIRMED"),  # asset 2: the only effective member of person 3
            ("f3", 3, "CONFIRMED"),
            ("f4", 3, "SUGGESTED"),  # a suggestion never counts
            ("f5", 9, "CONFIRMED"),  # somebody else
            ("f6", 3, "CONFIRMED"),  # asset 4: the second effective member
        ],
    )
    connection.execute(
        "INSERT INTO face_assignment_controls(face_id, mode) VALUES ('f1', 'MANUAL_UNASSIGNED')"
    )
    connection.execute(
        "INSERT INTO face_exclusions(face_id, person_id, active) VALUES ('f3', 3, 1)"
    )
    return connection
