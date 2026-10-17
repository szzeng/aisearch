"""P2-06A: the frozen search envelopes, their shape refusals and their drift guards.

The protocol is shape-only: unknown fields, wrong types and out-of-range bounds are
structured ``VALIDATION_ERROR`` refusals, and the two envelopes stay in step with the
pydantic models the HTTP layer publishes and the frozen ``$defs/SearchProfile`` document.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from photo_search.api.models import (
    SearchProfileActivationResponse,
    SearchProfileResponse,
    SearchRequestModel,
    SearchResultModel,
)
from photo_search.catalog.errors import CatalogError
from photo_search.scheduler.contracts import CONTRACT_VERSION
from photo_search.search.contracts import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    SearchHit,
    SearchRequest,
    SearchResult,
)
from photo_search.search.profile import DEFAULT_FUSION_CONFIG, SearchProfile
from photo_search.search.retrieval import DEFAULT_TOP_K, MAX_TOP_K
from photo_search.search.tokens import VALIDATION_ERROR

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "docs" / "decisions" / "contracts" / "p2-contracts.v1.json"


def minimal(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "contract_version": CONTRACT_VERSION,
        "request_id": "req-1",
    }
    payload.update(overrides)
    return payload


def refusal(payload: dict[str, object]) -> CatalogError:
    with pytest.raises(CatalogError) as error:
        SearchRequest.from_envelope(payload)
    return error.value


def test_a_minimal_request_takes_the_documented_defaults() -> None:
    request = SearchRequest.from_envelope(minimal())
    assert request.request_id == "req-1"
    assert request.retrieval_mode == "combined"
    assert request.text is None and request.profile_id is None
    assert request.availability == "any" and request.place_state == "any"
    assert request.include_descendants is True
    assert request.top_k == DEFAULT_TOP_K and request.page_size == DEFAULT_PAGE_SIZE
    assert request.place_ids == () and request.cursor is None
    assert request.explicit_binding is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.text = "sunset"  # type: ignore[misc]


def test_unknown_and_missing_fields_are_refused_with_their_names() -> None:
    unexpected = refusal(minimal(surprise="x"))
    assert unexpected.code == VALIDATION_ERROR
    assert unexpected.details["unexpected"] == ["surprise"]
    missing = refusal({"request_id": "req-1"})
    assert missing.details["missing"] == ["contract_version"]
    stale_version = refusal(minimal(contract_version="p2-contracts.v2"))
    assert stale_version.details["expected"] == CONTRACT_VERSION


@pytest.mark.parametrize(
    "overrides",
    [
        {"request_id": ""},
        {"request_id": 7},
        {"dimension": 0},
        {"dimension": "3"},
        {"source_id": 0},
        {"availability": "online"},
        {"place_state": "somewhere"},
        {"place_ids": [0]},
        {"place_ids": "3"},
        {"include_descendants": "yes"},
        {"top_k": 0},
        {"top_k": MAX_TOP_K + 1},
        {"page_size": 0},
        {"page_size": MAX_PAGE_SIZE + 1},
        {"cursor": ""},
        {"space_id": ""},
    ],
)
def test_out_of_range_or_wrongly_typed_fields_are_refused(overrides: dict[str, object]) -> None:
    assert refusal(minimal(**overrides)).code == VALIDATION_ERROR


def test_a_partial_explicit_binding_is_a_caller_shape_refusal() -> None:
    partial = SearchRequest.from_envelope(minimal(space_id="test-space:a1", dimension=3))
    with pytest.raises(CatalogError) as error:
        partial.explicit_binding
    assert error.value.code == VALIDATION_ERROR
    assert error.value.details["missing"] == ["image_generation_id"]
    complete = SearchRequest.from_envelope(
        minimal(space_id="test-space:a1", image_generation_id="image-gen-x", dimension=3)
    )
    assert complete.explicit_binding == ("test-space:a1", "image-gen-x", 3)


def test_place_ids_are_deduplicated_and_the_request_round_trips() -> None:
    request = SearchRequest.from_envelope(
        minimal(text="西湖", place_ids=[3, 1, 3], page_size=5, top_k=7)
    )
    assert request.place_ids == (3, 1)
    assert SearchRequest.from_envelope(request.to_envelope()) == request


@pytest.mark.parametrize("conflict", [
    {"profile_id": "profile"}, {"space_id": "space"},
    {"image_generation_id": "generation"}, {"dimension": 3}, {"rerank": True},
])
def test_text_mode_rejects_semantic_options(conflict: dict[str, object]) -> None:
    error = refusal(minimal(retrieval_mode="text", **conflict))
    assert error.code == VALIDATION_ERROR
    assert error.details["conflicting_fields"] == list(conflict)


def test_text_mode_round_trips_and_old_results_default_to_combined() -> None:
    request = SearchRequest.from_envelope(minimal(text="深圳", retrieval_mode="text"))
    assert SearchRequest.from_envelope(request.to_envelope()) == request
    assert refusal(minimal(retrieval_mode="unknown")).code == VALIDATION_ERROR
    result = dataclasses.replace(frozen_result(), retrieval_mode="text")
    assert SearchResult.from_envelope(result.to_envelope()) == result
    old_result = result.to_envelope()
    old_result.pop("retrieval_mode")
    assert SearchResult.from_envelope(old_result).retrieval_mode == "combined"


def frozen_result() -> SearchResult:
    return SearchResult(
        request_id="req-1",
        search_session_id="sess-1",
        profile_id=None,
        image_generation_id="image-gen-x",
        space_id="test-space:a1",
        dimension=3,
        catalog_filter_revision=4,
        suggestion_revision=0,
        fusion_config=dict(DEFAULT_FUSION_CONFIG),
        query={"text": "sunset", "match_expression": '"sunset"*'},
        filters={"availability": "any"},
        hits=(
            SearchHit(
                asset_id=2,
                rank=1,
                score=0.5,
                matched_by={"fts": 1, "vector": 1},
                hit_reason="TEXT+SEMANTIC",
                matched_text=("filename_text",),
                semantic_ready=True,
            ),
        ),
        page_size=20,
        page_offset=0,
        next_cursor=None,
        has_more=False,
        total_candidates=1,
        candidates_truncated=False,
        channels={"fts": {"candidates": 1, "truncated": False}},
        pending_semantic=None,
        search_projection={"state": "SYNCED"},
        state="OK",
    )


def test_the_result_envelope_round_trips_and_refuses_a_broken_shape() -> None:
    result = frozen_result()
    envelope = result.to_envelope()
    assert SearchResult.from_envelope(envelope) == result
    broken = dict(envelope)
    broken.pop("channels")
    with pytest.raises(CatalogError) as error:
        SearchResult.from_envelope(broken)
    assert error.value.details["missing"] == ["channels"]
    with pytest.raises(CatalogError) as error:
        SearchResult.from_envelope(dict(envelope, unknown_block={}))
    assert error.value.details["unexpected"] == ["unknown_block"]
    with pytest.raises(CatalogError):
        SearchResult.from_envelope(dict(envelope, state="MAYBE"))
    with pytest.raises(CatalogError):
        SearchResult.from_envelope(dict(envelope, hits=[{"asset_id": 1}]))


def test_the_http_models_are_field_for_field_the_frozen_envelopes() -> None:
    assert set(SearchRequestModel.model_fields) == set(SearchRequest.__dataclass_fields__)
    assert set(SearchResultModel.model_fields) == set(SearchResult.__dataclass_fields__)
    hit_model = SearchResultModel.model_fields["hits"].annotation.__args__[0]
    assert set(hit_model.model_fields) == set(SearchHit.__dataclass_fields__)
    assert "profile_id" in SearchProfileResponse.model_fields
    assert "readiness" in SearchProfileActivationResponse.model_fields
    version = SearchRequestModel.model_fields["contract_version"].annotation
    assert list(version.__args__) == [CONTRACT_VERSION]


def test_the_profile_document_matches_the_frozen_schema_field_by_field() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    frozen = schema["$defs"]["SearchProfile"]
    assert frozen["additionalProperties"] is False
    profile = SearchProfile(
        profile_id="profile-1",
        embedding_processor_id="test-embedding@1",
        space_id="test-space:a1",
        image_generation_id="image-gen-x",
        query_preprocess_version="query-v1",
        query_parser_version="parser-v1",
        fts_tokenizer_version="1",
        fts_generation_id="fts-v1",
        reranker_processor_id=None,
        fusion_config=dict(DEFAULT_FUSION_CONFIG),
        runtime_package_id="onnxruntime-cpu@test",
        created_at=1,
    )
    document = profile.to_dict()
    assert set(document) == set(frozen["required"])
    assert set(document) <= set(frozen["properties"])
    assert document["contract_version"] == CONTRACT_VERSION
    assert document["fusion_config"] == DEFAULT_FUSION_CONFIG
    # ``fusion_config`` is the one nested frozen object: same keys, same closed shape.
    nested = frozen["properties"]["fusion_config"]
    assert nested["additionalProperties"] is False
    assert set(document["fusion_config"]) == set(nested["required"])
    # The contract keeps ``fts_tokenizer_version`` a *string*; the DDL mapping is textual.
    assert isinstance(document["fts_tokenizer_version"], str)
    assert frozen["properties"]["fts_tokenizer_version"]["$ref"].endswith("NonEmptyString")
