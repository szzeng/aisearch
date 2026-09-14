"""P2-05: the two recall channels over a real LanceDB generation.

The photos are scanned by the production scanner, the vectors are published through the
production artifact/outbox/index path, and only the encoder is a deterministic stand-in.
Everything lives under pytest's ``tmp_path`` (``%TEMP%``).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from p2_search_fixtures import (
    DIMENSION,
    SPACE,
    FakeEncoder,
    assign_place,
    build_library,
    configured_scheduler,
    confirm_pending,
    create_place,
    embedding_spec,
    index_vector,
)

from photo_search.artifacts.store import generation_id_for
from photo_search.catalog.errors import PROCESSOR_MISMATCH, SPACE_MISMATCH, CatalogError
from photo_search.search.service import search_assets

PHOTOS = ("杭州西湖 照片.jpg", "beach sunset.jpg", "IMG_0001.JPG")
QUERY = "sunset"
QUERY_VECTOR = [1.0, 0.0, 0.0]


@pytest.fixture()
def env(tmp_path: Any):
    library = build_library(tmp_path, PHOTOS)
    spec = embedding_spec()
    scheduler = configured_scheduler(library.catalog, [spec])
    assets = dict(library.assets)
    index_vector(
        library.catalog,
        scheduler,
        spec,
        asset_id=assets["beach sunset.jpg"],
        vector=[1.0, 0.0, 0.0],
        artifact_id="art-sunset",
    )
    index_vector(
        library.catalog,
        scheduler,
        spec,
        asset_id=assets["杭州西湖 照片.jpg"],
        vector=[0.0, 1.0, 0.0],
        artifact_id="art-lake",
    )
    index_vector(
        library.catalog,
        scheduler,
        spec,
        asset_id=assets["IMG_0001.JPG"],
        vector=[0.9, 0.1, 0.0],
        artifact_id="art-image",
    )
    try:
        yield {
            "library": library,
            "catalog": library.catalog,
            "spec": spec,
            "assets": assets,
            "generation": generation_id_for(SPACE, spec.processor_id),
        }
    finally:
        library.close()


def run_query(env: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    encoder = overrides.pop("encoder", None)
    options: dict[str, Any] = {
        "text": QUERY,
        "space_id": SPACE,
        "generation_id": env["generation"],
        "dimension": DIMENSION,
        "top_k": 10,
    }
    options.update(overrides)
    if encoder is not None:
        options["encoder"] = encoder
    return search_assets(env["catalog"], **options)


def test_both_channels_rank_deterministically_and_fuse_with_rrf(env: dict[str, Any]) -> None:
    encoder = FakeEncoder(env["spec"], vectors={QUERY: QUERY_VECTOR})
    result = run_query(env, encoder=encoder)
    assets = env["assets"]
    sunset = assets["beach sunset.jpg"]
    lake = assets["杭州西湖 照片.jpg"]
    image = assets["IMG_0001.JPG"]

    # The query text is encoded exactly once, however many candidate blocks follow.
    assert encoder.calls == [QUERY]

    assert [row["asset_id"] for row in result["channels"]["fts"]] == [sunset]
    assert [row["asset_id"] for row in result["channels"]["vector"]] == [sunset, image, lake]
    distances = [row["distance"] for row in result["channels"]["vector"]]
    assert distances[0] == pytest.approx(0.0, abs=1e-6)
    assert distances[1] == pytest.approx(0.02, abs=1e-6)
    assert distances[2] == pytest.approx(2.0, abs=1e-6)

    hits = {row["asset_id"]: row for row in result["hits"]}
    assert [row["asset_id"] for row in result["hits"]] == [sunset, image, lake]
    assert hits[sunset]["matched_by"] == {"fts": 1, "vector": 1}
    assert hits[sunset]["hit_reason"] == "TEXT+SEMANTIC"
    assert hits[sunset]["matched_text"] == ["filename_text"]
    assert hits[sunset]["semantic_ready"] is True
    assert hits[image]["matched_by"] == {"vector": 2}
    assert hits[image]["hit_reason"] == "SEMANTIC"
    assert hits[image]["matched_text"] == []
    assert hits[lake]["matched_by"] == {"vector": 3}
    assert result["weights"] == {"fts": 1.0, "vector": 1.0}

    binding = result["binding"]
    assert binding["generation_id"] == env["generation"]
    assert binding["space_id"] == SPACE
    assert binding["dimension"] == DIMENSION
    assert binding["generation_state"] == "READY"
    assert binding["semantic_complete"] is True
    assert result["vector_scan"] == {"blocks": 1, "scanned_assets": 3, "truncated": False}
    assert result["pending_semantic"]["count"] == 0
    assert result["search_projection"]["state"] == "SYNCED"
    assert result["search_projection"]["tokenizer_version"] == 1
    assert result["search_projection"]["fts_generation_id"] == "fts-v1"
    assert result["query"]["match_expression"] == '"sunset"*'
    assert result["state"] == "OK"
    assert result["authoritative"]["total"] == 3

    # The authority stays a pure read: no revision, no vector row and no artifact moves.
    assert result["authoritative"]["asset_ids"] == sorted(assets.values())


def test_the_same_query_twice_is_byte_identical(env: dict[str, Any]) -> None:
    def render(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    # The first text query also catches the scan's reprojection up, so the comparison
    # starts from a settled projection - both runs below are then pure reads.
    warm_up = run_query(env, encoder=FakeEncoder(env["spec"], vectors={QUERY: QUERY_VECTOR}))
    assert warm_up["search_projection"]["state"] == "SYNCED"
    first = run_query(env, encoder=FakeEncoder(env["spec"], vectors={QUERY: QUERY_VECTOR}))
    second = run_query(env, encoder=FakeEncoder(env["spec"], vectors={QUERY: QUERY_VECTOR}))
    assert first["search_projection"]["processed_assets"] == 0
    assert render(first) == render(second)
    # The ranking itself does not drift between runs.
    assert render(first["channels"]) == render(warm_up["channels"])
    assert render(first["hits"]) == render(warm_up["hits"])


def test_a_text_hit_says_whether_the_file_name_or_the_place_matched(env: dict[str, Any]) -> None:
    catalog = env["catalog"]
    sunset = env["assets"]["beach sunset.jpg"]
    place_id = create_place(catalog, "杭州", aliases=[{"text": "Hangzhou", "language": "en"}])
    assign_place(catalog, asset_id=sunset, place_id=place_id, request_id="req-place-1")
    revision_before = catalog.read_one(
        "SELECT revision FROM catalog_filter_revision WHERE revision_id = 1"
    )["revision"]

    # A text-only query needs no GPU and no generation at all.
    result = search_assets(catalog, text="hangzhou", top_k=10)
    assert [row["asset_id"] for row in result["hits"]] == [sunset]
    hit = result["hits"][0]
    assert hit["hit_reason"] == "TEXT"
    assert hit["matched_text"] == ["place_text"]
    assert hit["matched_by"] == {"fts": 1}
    assert result["binding"] is None
    assert result["channels"]["vector"] == []
    assert result["search_projection"]["state"] == "SYNCED"

    revision_after = catalog.read_one(
        "SELECT revision FROM catalog_filter_revision WHERE revision_id = 1"
    )["revision"]
    assert int(revision_before) == int(revision_after)


def test_the_vector_binding_is_refused_structurally(env: dict[str, Any]) -> None:
    catalog = env["catalog"]
    encoder = FakeEncoder(env["spec"], vectors={QUERY: QUERY_VECTOR})
    with pytest.raises(CatalogError) as foreign_space:
        search_assets(
            catalog,
            text=QUERY,
            encoder=encoder,
            space_id="another-space:same-dimension",
            generation_id=env["generation"],
            dimension=DIMENSION,
        )
    assert foreign_space.value.code == SPACE_MISMATCH
    with pytest.raises(CatalogError) as wrong_dimension:
        search_assets(
            catalog,
            text=QUERY,
            encoder=encoder,
            space_id=SPACE,
            generation_id=env["generation"],
            dimension=DIMENSION + 1,
        )
    assert wrong_dimension.value.code == SPACE_MISMATCH
    with pytest.raises(CatalogError) as unknown_generation:
        search_assets(
            catalog,
            text=QUERY,
            encoder=encoder,
            space_id=SPACE,
            generation_id="image-gen-missing",
            dimension=DIMENSION,
        )
    assert unknown_generation.value.code == PROCESSOR_MISMATCH
    assert unknown_generation.value.details["reason"] == "GENERATION_NOT_FOUND"
    # A half-bound vector channel and an impossible top_k are caller-shape refusals.
    with pytest.raises(CatalogError) as partial:
        search_assets(catalog, text=QUERY, encoder=encoder, space_id=SPACE, dimension=DIMENSION)
    assert partial.value.code == "VALIDATION_ERROR"
    with pytest.raises(CatalogError) as bad_top_k:
        search_assets(catalog, text=QUERY, top_k=0)
    assert bad_top_k.value.code == "VALIDATION_ERROR"


def test_a_text_hit_reports_the_place_and_file_name_and_never_leaks_a_path(
    env: dict[str, Any],
) -> None:
    catalog = env["catalog"]
    lake = env["assets"]["杭州西湖 照片.jpg"]
    place_id = create_place(catalog, "西湖")
    assign_place(catalog, asset_id=lake, place_id=place_id, request_id="req-place-2")
    # The place edit queued a reprojection for the lake's confirmed vector; replaying it
    # puts the asset back into semantic recall, so the hit can be explained by both probes.
    confirm_pending(catalog)

    result = search_assets(
        catalog,
        text="西湖",
        encoder=FakeEncoder(env["spec"], vectors={"西湖": [0.0, 1.0, 0.0]}),
        space_id=SPACE,
        generation_id=env["generation"],
        dimension=DIMENSION,
    )
    hit = next(row for row in result["hits"] if row["asset_id"] == lake)
    # The file name and the effective place both match the same query.
    assert hit["matched_text"] == ["filename_text", "place_text"]
    assert hit["hit_reason"] == "TEXT+SEMANTIC"
    rendered = json.dumps(result, ensure_ascii=False)
    assert str(env["library"].root) not in rendered
    assert "latitude" not in rendered
    assert "request_id" not in rendered
