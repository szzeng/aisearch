"""P2-05: SQLite decides membership, and no channel may widen past it.

The place/source/availability conditions are the ADR-0005 §10 semantics; the vector
candidate set is the CONFIRMED entries of the bound generation, so a filtered query can
never answer with a global Top-K that was filtered afterwards (architecture §9.3 item 4).
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
from photo_search.catalog.errors import FILTER_UNSUPPORTED, CatalogError
from photo_search.scheduler.projection import pending_projection_summary
from photo_search.search.service import default_vector_index, search_assets

PHOTOS = ("杭州西湖 照片.jpg", "beach sunset.jpg", "IMG_0001.JPG", "家 合影.jpg")
QUERY = "sunset"
QUERY_VECTOR = [1.0, 0.0, 0.0]


@pytest.fixture()
def env(tmp_path: Any):
    library = build_library(tmp_path, PHOTOS)
    catalog = library.catalog
    spec = embedding_spec()
    scheduler = configured_scheduler(catalog, [spec])
    assets = dict(library.assets)
    index_vector(
        catalog, scheduler, spec,
        asset_id=assets["beach sunset.jpg"], vector=[1.0, 0.0, 0.0], artifact_id="art-a",
    )
    index_vector(
        catalog, scheduler, spec,
        asset_id=assets["杭州西湖 照片.jpg"], vector=[0.0, 1.0, 0.0], artifact_id="art-b",
    )
    index_vector(
        catalog, scheduler, spec,
        asset_id=assets["IMG_0001.JPG"], vector=[0.9, 0.1, 0.0], artifact_id="art-c",
    )
    index_vector(
        catalog, scheduler, spec,
        asset_id=assets["家 合影.jpg"], vector=[0.2, 0.2, 0.9], artifact_id="art-d",
    )
    china = create_place(catalog, "中国", kind="country")
    hangzhou = create_place(catalog, "杭州", parent_place_id=china)
    west_lake = create_place(catalog, "西湖", kind="attraction", parent_place_id=hangzhou)
    beijing = create_place(catalog, "北京")
    assign_place(
        catalog,
        asset_id=assets["杭州西湖 照片.jpg"],
        place_id=west_lake,
        request_id="req-lake",
    )
    assign_place(
        catalog,
        asset_id=assets["IMG_0001.JPG"],
        place_id=hangzhou,
        request_id="req-image",
    )
    try:
        yield {
            "library": library,
            "catalog": catalog,
            "spec": spec,
            "assets": assets,
            "generation": generation_id_for(SPACE, spec.processor_id),
            "places": {
                "china": china,
                "hangzhou": hangzhou,
                "west_lake": west_lake,
                "beijing": beijing,
            },
        }
    finally:
        library.close()


def ids(rows: list[dict[str, Any]]) -> list[int]:
    return [int(row["asset_id"]) for row in rows]


def test_descendants_or_and_state_are_the_adr0005_semantics(env: dict[str, Any]) -> None:
    catalog = env["catalog"]
    assets = env["assets"]
    places = env["places"]
    lake = assets["杭州西湖 照片.jpg"]
    image = assets["IMG_0001.JPG"]
    sunset = assets["beach sunset.jpg"]
    home = assets["家 合影.jpg"]

    subtree = search_assets(catalog, place_ids=[places["hangzhou"]], include_descendants=True)
    assert sorted(ids(subtree["hits"])) == sorted([lake, image])
    assert subtree["hits"][0]["hit_reason"] == "FILTER_ONLY"
    assert subtree["hits"][0]["channels"] == ["filter"]
    assert subtree["filters"]["expanded_place_ids"] == [
        places["hangzhou"],
        places["west_lake"],
    ]

    exact = search_assets(catalog, place_ids=[places["hangzhou"]], include_descendants=False)
    assert ids(exact["hits"]) == [image]

    union = search_assets(catalog, place_ids=[places["hangzhou"], places["beijing"]])
    assert sorted(ids(union["hits"])) == sorted([lake, image])

    unassigned = search_assets(catalog, place_state="unassigned")
    assert sorted(ids(unassigned["hits"])) == sorted([sunset, home])

    # Only a candidate-visibility change moves the suggestion revision; a place with no
    # resolution row and no dataset is deterministically empty, never "everything".
    suggested = search_assets(catalog, place_state="suggested")
    assert suggested["hits"] == []
    with pytest.raises(CatalogError) as refusal:
        search_assets(catalog, place_ids=[places["beijing"]], place_state="suggested")
    assert refusal.value.code == "INVALID_PLACE_FILTER"


def test_a_filtered_semantic_query_never_falls_back_to_a_global_top_k(
    env: dict[str, Any],
) -> None:
    catalog = env["catalog"]
    places = env["places"]
    encoder = FakeEncoder(env["spec"], vectors={QUERY: QUERY_VECTOR})

    # "beach sunset.jpg" is the global nearest neighbour of the query vector, but it has
    # no place at all: a filtered search must not reach it.
    filtered = search_assets(
        catalog,
        text=QUERY,
        encoder=encoder,
        space_id=SPACE,
        generation_id=env["generation"],
        dimension=DIMENSION,
        place_ids=[places["beijing"]],
    )
    assert filtered["channels"]["vector"] == []
    assert filtered["channels"]["fts"] == []
    assert filtered["hits"] == []
    assert filtered["vector_scan"] == {"blocks": 0, "scanned_assets": 0, "truncated": False}
    assert filtered["authoritative"]["total"] == 0

    # A place edit marks the affected confirmed vectors for reprojection (P2-03), so they
    # leave the vector channel until the outbox is replayed. After the replay the same
    # query ranks inside the subtree candidate set - exactly one asset.
    confirm_pending(catalog)
    subtree = search_assets(
        catalog,
        text="照片",
        encoder=FakeEncoder(env["spec"], vectors={"照片": [0.0, 1.0, 0.0]}),
        space_id=SPACE,
        generation_id=env["generation"],
        dimension=DIMENSION,
        place_ids=[places["hangzhou"]],
    )
    lake = env["assets"]["杭州西湖 照片.jpg"]
    image = env["assets"]["IMG_0001.JPG"]
    # The text channel only matched the file name; the semantic channel ranks the whole
    # subtree candidate set (both the attraction and its parent city assignment).
    assert ids(subtree["channels"]["fts"]) == [lake]
    assert sorted(ids(subtree["channels"]["vector"])) == sorted([lake, image])
    assert sorted(ids(subtree["hits"])) == sorted([lake, image])
    assert subtree["vector_scan"]["scanned_assets"] == 2


def test_an_unusable_engine_filter_is_structured_and_never_unfiltered(
    env: dict[str, Any],
) -> None:
    index = default_vector_index(env["catalog"])
    with pytest.raises(CatalogError) as refusal:
        index.search(
            generation_id=env["generation"],
            dimension=DIMENSION,
            query_vector=QUERY_VECTOR,
            limit=5,
            where="no_such_column = 1",
        )
    assert refusal.value.code == FILTER_UNSUPPORTED
    # The same call without the bad filter reads normally, so the refusal is about the
    # filter and not about a broken generation.
    rows = index.search(
        generation_id=env["generation"],
        dimension=DIMENSION,
        query_vector=QUERY_VECTOR,
        limit=5,
    )
    assert len(rows) == 4
    # An empty authoritative candidate set returns nothing: a query with no candidate can
    # never widen into "everything".
    assert (
        index.search_candidates(
            generation_id=env["generation"],
            dimension=DIMENSION,
            query_vector=QUERY_VECTOR,
            asset_ids=[],
            limit=5,
        )
        == []
    )


def test_a_query_is_a_pure_read_and_leaves_the_revisions_alone(env: dict[str, Any]) -> None:
    catalog = env["catalog"]
    # Drain the reprojection the fixture's place edits queued, so the comparison below is
    # about the queries and not about a pending catch-up.
    search_assets(catalog, text=QUERY)
    before = {
        "filter": catalog.read_one(
            "SELECT revision FROM catalog_filter_revision WHERE revision_id = 1"
        )["revision"],
        "suggestion": catalog.read_one(
            "SELECT revision FROM place_suggestion_revision WHERE revision_id = 1"
        )["revision"],
        "outbox": catalog.read_one("SELECT COUNT(*) AS value FROM index_outbox")["value"],
        "entries": catalog.read_one("SELECT COUNT(*) AS value FROM index_entries")["value"],
        "documents": catalog.read_one("SELECT COUNT(*) AS value FROM search_documents")["value"],
        "created_at": [
            int(row["created_at"])
            for row in catalog.read_all("SELECT created_at FROM assets ORDER BY asset_id")
        ],
        "schema": catalog.read_one(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        )["value"],
    }
    search_assets(
        catalog,
        text=QUERY,
        encoder=FakeEncoder(env["spec"], vectors={QUERY: QUERY_VECTOR}),
        space_id=SPACE,
        generation_id=env["generation"],
        dimension=DIMENSION,
    )
    search_assets(catalog, place_ids=[env["places"]["hangzhou"]])
    after = {
        "filter": catalog.read_one(
            "SELECT revision FROM catalog_filter_revision WHERE revision_id = 1"
        )["revision"],
        "suggestion": catalog.read_one(
            "SELECT revision FROM place_suggestion_revision WHERE revision_id = 1"
        )["revision"],
        "outbox": catalog.read_one("SELECT COUNT(*) AS value FROM index_outbox")["value"],
        "entries": catalog.read_one("SELECT COUNT(*) AS value FROM index_entries")["value"],
        "documents": catalog.read_one("SELECT COUNT(*) AS value FROM search_documents")["value"],
        "created_at": [
            int(row["created_at"])
            for row in catalog.read_all("SELECT created_at FROM assets ORDER BY asset_id")
        ],
        "schema": catalog.read_one(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        )["value"],
    }
    assert before == after
    assert int(before["schema"]) == 6


def test_the_read_only_projection_summary_reports_lag_without_paths_or_ids(
    env: dict[str, Any],
) -> None:
    catalog = env["catalog"]
    # A place edit queues the confirmed vectors of the affected assets for reprojection.
    assign_place(
        catalog,
        asset_id=env["assets"]["beach sunset.jpg"],
        place_id=env["places"]["beijing"],
        request_id="req-lag",
    )
    summary = pending_projection_summary(catalog.db_path)
    assert summary is not None
    assert summary["outbox"]["PENDING"] >= 1
    assert summary["pending_events"] >= 1
    assert summary["state"] == "LAGGING"
    # The two place edits marked their affected confirmed vectors for reprojection.
    assert summary["entries"]["CONFIRMED"] >= 1
    assert summary["entries"]["PENDING_REPAIR"] >= 1
    assert summary["active_generations"][0]["generation_id"] == env["generation"]
    rendered = json.dumps(summary, ensure_ascii=False)
    assert str(env["library"].data_root) not in rendered
    assert "storage_uri" not in rendered

    confirm_pending(catalog)
    synced = pending_projection_summary(catalog.db_path)
    assert synced is not None
    assert synced["state"] == "SYNCED"
    assert synced["pending_events"] == 0
