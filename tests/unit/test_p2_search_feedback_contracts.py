"""P2-06B: the feedback envelopes and the rerank fields of the frozen search envelopes.

The protocol stays shape-only: an unknown field, a wrong type, an out-of-range rank and an
unbounded note are structured ``VALIDATION_ERROR`` refusals, and every envelope stays in
step with the pydantic models the HTTP layer publishes.
"""

from __future__ import annotations

from typing import Any

import pytest

from photo_search.api.models import (
    FeedbackListResponseModel,
    FeedbackRecordModel,
    FeedbackRequestModel,
    FeedbackResponseModel,
    SearchRequestModel,
    SearchResultModel,
)
from photo_search.catalog.errors import CatalogError
from photo_search.scheduler.contracts import CONTRACT_VERSION
from photo_search.search.contracts import (
    DEFAULT_FEEDBACK_PAGE_SIZE,
    MAX_FEEDBACK_PAGE_SIZE,
    MAX_NOTE_CHARS,
    RELEVANCE_VALUES,
    FeedbackPage,
    FeedbackRecord,
    FeedbackRequest,
    FeedbackResponse,
    SearchRequest,
    SearchResult,
)
from photo_search.search.tokens import VALIDATION_ERROR


def minimal(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "request_id": "fb-1",
        "search_session_id": "session-1",
        "asset_id": 2,
        "rank": 1,
        "relevance": "RELEVANT",
    }
    payload.update(overrides)
    return payload


def refusal(payload: dict[str, Any]) -> CatalogError:
    with pytest.raises(CatalogError) as error:
        FeedbackRequest.from_envelope(payload)
    return error.value


def test_a_minimal_verdict_takes_its_defaults_and_round_trips() -> None:
    request = FeedbackRequest.from_envelope(minimal())
    assert request.note is None
    assert request.relevance in RELEVANCE_VALUES
    assert request.payload_identity() == ("session-1", 2, 1, "RELEVANT", None)
    assert FeedbackRequest.from_envelope(request.to_envelope()) == request


def test_unknown_missing_and_version_mismatched_bodies_are_refused() -> None:
    unexpected = refusal(minimal(surprise="x"))
    assert unexpected.code == VALIDATION_ERROR
    assert unexpected.details["unexpected"] == ["surprise"]
    missing = refusal({"contract_version": CONTRACT_VERSION, "request_id": "fb-1"})
    assert set(missing.details["missing"]) == {
        "search_session_id",
        "asset_id",
        "rank",
        "relevance",
    }
    stale = refusal(minimal(contract_version="p2-contracts.v2"))
    assert stale.details["expected"] == CONTRACT_VERSION


@pytest.mark.parametrize(
    "overrides",
    [
        {"request_id": ""},
        {"request_id": 7},
        {"request_id": "x" * 65},
        {"search_session_id": ""},
        {"search_session_id": "x" * 129},
        {"asset_id": 0},
        {"asset_id": "2"},
        {"rank": 0},
        {"rank": "1"},
        {"relevance": "MAYBE"},
        {"relevance": "relevant"},
        {"note": 5},
        {"note": "x" * (MAX_NOTE_CHARS + 1)},
    ],
)
def test_out_of_range_or_wrongly_typed_fields_are_refused(overrides: dict[str, Any]) -> None:
    assert refusal(minimal(**overrides)).code == VALIDATION_ERROR


def test_the_note_ceiling_is_inclusive_and_bound_to_the_ddl_constant() -> None:
    request = FeedbackRequest.from_envelope(minimal(note="x" * MAX_NOTE_CHARS))
    assert request.note is not None and len(request.note) == MAX_NOTE_CHARS
    assert MAX_NOTE_CHARS == 500
    assert DEFAULT_FEEDBACK_PAGE_SIZE == 50 and MAX_FEEDBACK_PAGE_SIZE == 200


def test_the_response_and_page_envelopes_round_trip_and_refuse_broken_shapes() -> None:
    record = FeedbackRecord(
        feedback_id=1,
        request_id="fb-1",
        search_session_id="session-1",
        asset_id=2,
        rank=1,
        relevance="NOT_RELEVANT",
        note="no",
        created_at=1_000,
    )
    response = FeedbackResponse(feedback=record, replayed=True)
    assert FeedbackResponse.from_envelope(response.to_envelope()) == response
    page = FeedbackPage(feedback=(record,), limit=50, next_cursor="fb1:1", has_more=True)
    assert FeedbackPage.from_envelope(page.to_envelope()) == page

    with pytest.raises(CatalogError) as missing:
        FeedbackResponse.from_envelope({"contract_version": CONTRACT_VERSION})
    assert set(missing.value.details["missing"]) == {"replayed", "feedback"}
    with pytest.raises(CatalogError) as extra:
        FeedbackPage.from_envelope(dict(page.to_envelope(), unexpected=1))
    assert extra.value.details["unexpected"] == ["unexpected"]
    with pytest.raises(CatalogError):
        FeedbackRecord.from_envelope(dict(record.to_envelope(), relevance="MAYBE"))
    with pytest.raises(CatalogError):
        FeedbackPage.from_envelope(dict(page.to_envelope(), feedback=[{"feedback_id": 1}]))


def test_the_http_models_are_field_for_field_the_feedback_envelopes() -> None:
    assert set(FeedbackRequestModel.model_fields) == set(FeedbackRequest.__dataclass_fields__)
    assert set(FeedbackRecordModel.model_fields) == set(FeedbackRecord.__dataclass_fields__)
    assert set(FeedbackResponseModel.model_fields) == set(FeedbackResponse.__dataclass_fields__)
    assert set(FeedbackListResponseModel.model_fields) == set(FeedbackPage.__dataclass_fields__)


def test_the_search_envelopes_grew_only_additively_for_rerank() -> None:
    """The P2-06A fields are untouched; the new ones are optional and off by default."""
    assert "rerank" in SearchRequest.__dataclass_fields__
    assert (
        SearchRequest.from_envelope(
            {"contract_version": CONTRACT_VERSION, "request_id": "req-1"}
        ).rerank
        is False
    )
    assert set(SearchRequestModel.model_fields) == set(SearchRequest.__dataclass_fields__)
    assert set(SearchResultModel.model_fields) == set(SearchResult.__dataclass_fields__)
    result = SearchResult(
        request_id="req-1",
        search_session_id="session-1",
        profile_id=None,
        image_generation_id=None,
        space_id=None,
        dimension=None,
        catalog_filter_revision=0,
        suggestion_revision=0,
        fusion_config={"method": "RRF"},
        query=None,
        filters={},
        hits=(),
        page_size=20,
        page_offset=0,
        next_cursor=None,
        has_more=False,
        total_candidates=0,
        candidates_truncated=False,
        channels={},
        pending_semantic=None,
        search_projection={},
        state="OK",
    )
    assert result.rerank_state == "NOT_REQUESTED" and result.reranked is None
    assert SearchResult.from_envelope(result.to_envelope()) == result
    with pytest.raises(CatalogError):
        SearchResult.from_envelope(dict(result.to_envelope(), rerank_state="MAYBE"))
