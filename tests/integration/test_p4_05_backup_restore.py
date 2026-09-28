"""P4-05 restores production-generated FTS and Lance state without a rescan."""

from __future__ import annotations

from pathlib import Path

from p2_search_fixtures import (
    DIMENSION,
    SPACE,
    FakeEncoder,
    assign_place,
    build_library,
    configured_scheduler,
    create_place,
    embedding_spec,
    index_vector,
)

from photo_search.backup import create_backup, restore_backup
from photo_search.catalog.db import Catalog
from photo_search.search.service import search_assets


def test_empty_restore_keeps_text_and_semantic_pages_identical(tmp_path: Path) -> None:
    library = build_library(tmp_path, ("beach sunset.jpg", "lake.jpg"))
    spec = embedding_spec()
    scheduler = configured_scheduler(library.catalog, [spec])
    assets = library.assets
    index_vector(
        library.catalog,
        scheduler,
        spec,
        asset_id=assets["beach sunset.jpg"],
        vector=[1.0, 0.0, 0.0],
        artifact_id="sunset",
    )
    index_vector(
        library.catalog,
        scheduler,
        spec,
        asset_id=assets["lake.jpg"],
        vector=[0.0, 1.0, 0.0],
        artifact_id="lake",
    )
    place_id = create_place(library.catalog, "杭州")
    assign_place(
        library.catalog,
        asset_id=assets["beach sunset.jpg"],
        place_id=place_id,
        request_id="p4-05-place",
    )
    knowledge_before = {
        "places": [
            tuple(row)
            for row in library.catalog.read_all(
                "SELECT place_id, canonical_name, revision FROM places ORDER BY place_id"
            )
        ],
        "assignments": [
            tuple(row)
            for row in library.catalog.read_all(
                "SELECT asset_id, place_id, mode, revision FROM asset_place_assignments ORDER BY asset_id"
            )
        ],
        "events": [
            tuple(row)
            for row in library.catalog.read_all(
                "SELECT * FROM catalog_filter_revision_events ORDER BY revision"
            )
        ],
    }
    generation = library.catalog.read_one("SELECT generation_id FROM index_generations")
    generation_id = str(generation["generation_id"])
    encoder = FakeEncoder(spec, vectors={"sunset": [1.0, 0.0, 0.0]})
    before = search_assets(
        library.catalog,
        text="sunset",
        space_id=SPACE,
        generation_id=generation_id,
        dimension=DIMENSION,
        encoder=encoder,
        top_k=10,
    )
    bundle, restored_root = tmp_path / "backup", tmp_path / "restored"
    library.close()
    create_backup(data_root=tmp_path / "PhotoSearchData", destination=bundle, stopped=True)
    restore_backup(bundle=bundle, data_root=restored_root)
    with Catalog.open(data_root=restored_root) as restored:
        after = search_assets(
            restored,
            text="sunset",
            space_id=SPACE,
            generation_id=generation_id,
            dimension=DIMENSION,
            encoder=FakeEncoder(spec, vectors={"sunset": [1.0, 0.0, 0.0]}),
            top_k=10,
        )
    assert [row["asset_id"] for row in before["hits"]] == [row["asset_id"] for row in after["hits"]]
    assert before["channels"] == after["channels"]
    with Catalog.open(data_root=restored_root) as restored:
        knowledge_after = {
            "places": [
                tuple(row)
                for row in restored.read_all(
                    "SELECT place_id, canonical_name, revision FROM places ORDER BY place_id"
                )
            ],
            "assignments": [
                tuple(row)
                for row in restored.read_all(
                    "SELECT asset_id, place_id, mode, revision FROM asset_place_assignments ORDER BY asset_id"
                )
            ],
            "events": [
                tuple(row)
                for row in restored.read_all(
                    "SELECT * FROM catalog_filter_revision_events ORDER BY revision"
                )
            ],
        }
    assert knowledge_after == knowledge_before
