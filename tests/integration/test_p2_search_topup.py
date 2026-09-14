"""P2-05: a lagging semantic projection is named, never silently dropped.

The three named cases of the task card live here: a text hit whose vector row is still
unconfirmed, the P2-03 crash window (Lance written, SQLite not confirmed) before and after
the replay, and an old READY generation staying usable while a new one is not confirmed.
"""

from __future__ import annotations

from typing import Any

import pytest
from p2_search_fixtures import (
    DIMENSION,
    SPACE,
    FakeEncoder,
    build_library,
    configured_scheduler,
    embedding_spec,
    submit_vector,
)

from photo_search.artifacts.store import generation_id_for
from photo_search.indexing.store import IndexCoordinator
from photo_search.scheduler.projection import pending_projection_summary
from photo_search.search.service import default_vector_index, search_assets

PHOTOS = ("杭州西湖 照片.jpg", "beach sunset.jpg", "家 合影.jpg")


def test_a_text_hit_is_kept_and_flagged_while_its_vector_row_is_unconfirmed(
    tmp_path: Any,
) -> None:
    library = build_library(tmp_path, PHOTOS)
    try:
        catalog = library.catalog
        spec = embedding_spec()
        scheduler = configured_scheduler(catalog, [spec])
        sunset = library.assets["beach sunset.jpg"]
        home = library.assets["家 合影.jpg"]
        submit_vector(
            catalog, scheduler, spec,
            asset_id=sunset, vector=[1.0, 0.0, 0.0], artifact_id="art-sunset", now_ms=1_100,
        )
        submit_vector(
            catalog, scheduler, spec,
            asset_id=home, vector=[0.0, 1.0, 0.0], artifact_id="art-home", now_ms=1_200,
        )
        # Exactly one of the two events is confirmed: the photo whose vector is still
        # missing must not vanish from the answer.
        assert IndexCoordinator(catalog).process_next(now_ms=2_000) is True
        generation = generation_id_for(SPACE, spec.processor_id)
        result = search_assets(
            catalog,
            text="合影",
            encoder=FakeEncoder(spec, vectors={"合影": [0.0, 1.0, 0.0]}),
            space_id=SPACE,
            generation_id=generation,
            dimension=DIMENSION,
        )
        assert [row["asset_id"] for row in result["channels"]["fts"]] == [home]
        assert [row["asset_id"] for row in result["channels"]["vector"]] == [sunset]
        ranked = {int(row["asset_id"]) for row in result["hits"]}
        assert ranked == {sunset, home}
        home_hit = next(row for row in result["hits"] if row["asset_id"] == home)
        assert home_hit["matched_by"] == {"fts": 1}
        assert home_hit["hit_reason"] == "TEXT"
        assert home_hit["semantic_ready"] is False

        binding = result["binding"]
        assert binding["generation_state"] == "PENDING"
        assert binding["semantic_complete"] is False
        pending = result["pending_semantic"]
        assert [entry["asset_id"] for entry in pending["entries"]] == [home]
        assert pending["entries"][0]["entry_state"] == "PENDING_REPAIR"
        assert pending["entries"][0]["pending_events"] == 1
        assert pending["entries"][0]["in_ranked_hits"] is True
        assert pending["generation_state"] == "PENDING"
    finally:
        library.close()


def test_the_crash_window_leaves_the_asset_out_of_semantic_recall_until_replay(
    tmp_path: Any,
) -> None:
    library = build_library(tmp_path, PHOTOS)
    try:
        catalog = library.catalog
        spec = embedding_spec()
        scheduler = configured_scheduler(catalog, [spec])
        lake = library.assets["杭州西湖 照片.jpg"]
        submit_vector(
            catalog, scheduler, spec,
            asset_id=lake, vector=[0.0, 1.0, 0.0], artifact_id="art-lake",
        )
        generation = generation_id_for(SPACE, spec.processor_id)
        index = default_vector_index(catalog)
        with pytest.raises(RuntimeError, match="after_lance"):
            IndexCoordinator(catalog).process_next(now_ms=2_000, failpoint="after_lance")
        # The engine holds the row, SQLite does not confirm it: the query must not use it.
        assert index.count(generation_id=generation, dimension=DIMENSION, space_id=SPACE) == 1
        assert (
            catalog.read_one("SELECT COUNT(*) AS value FROM index_outbox WHERE state = 'CONFIRMED'")[
                "value"
            ]
            == 0
        )
        encoder = FakeEncoder(spec, vectors={"西湖": [0.0, 1.0, 0.0]})
        before = search_assets(
            catalog,
            text="西湖",
            encoder=encoder,
            space_id=SPACE,
            generation_id=generation,
            dimension=DIMENSION,
        )
        assert before["channels"]["vector"] == []
        assert [entry["asset_id"] for entry in before["pending_semantic"]["entries"]] == [lake]
        assert before["pending_semantic"]["entries"][0]["entry_state"] == "PENDING_REPAIR"
        assert before["binding"]["semantic_complete"] is False
        assert all(row["semantic_ready"] is False for row in before["hits"])

        # A restart replays the same logical event: no second logical vector row, and the
        # photo is now genuinely in semantic recall.
        assert IndexCoordinator(catalog).process_next(now_ms=2_100) is True
        assert IndexCoordinator(catalog).process_next(now_ms=2_200) is False
        after = search_assets(
            catalog,
            text="西湖",
            encoder=encoder,
            space_id=SPACE,
            generation_id=generation,
            dimension=DIMENSION,
        )
        assert [row["asset_id"] for row in after["channels"]["vector"]] == [lake]
        assert after["pending_semantic"]["count"] == 0
        assert after["binding"]["generation_state"] == "READY"
        assert after["binding"]["semantic_complete"] is True
        assert next(row for row in after["hits"] if row["asset_id"] == lake)["semantic_ready"] is True
        assert index.count(generation_id=generation, dimension=DIMENSION, space_id=SPACE) == 1
    finally:
        library.close()


def test_an_old_ready_generation_stays_usable_while_a_new_one_is_unconfirmed(
    tmp_path: Any,
) -> None:
    library = build_library(tmp_path, PHOTOS)
    try:
        catalog = library.catalog
        old_spec = embedding_spec(processor_id="test-embedding@old")
        new_spec = embedding_spec(processor_id="test-embedding@new")
        scheduler = configured_scheduler(catalog, [old_spec, new_spec])
        lake = library.assets["杭州西湖 照片.jpg"]
        sunset = library.assets["beach sunset.jpg"]
        submit_vector(
            catalog, scheduler, old_spec,
            asset_id=lake, vector=[1.0, 0.0, 0.0], artifact_id="art-old",
        )
        assert IndexCoordinator(catalog).process_next(now_ms=2_000) is True
        old_generation = generation_id_for(SPACE, old_spec.processor_id)
        new_generation = generation_id_for(SPACE, new_spec.processor_id)
        submit_vector(
            catalog, scheduler, new_spec,
            asset_id=sunset, vector=[0.0, 1.0, 0.0], artifact_id="art-new",
        )

        encoder = FakeEncoder(new_spec, vectors={"sunset": [0.0, 1.0, 0.0]})
        served = search_assets(
            catalog,
            text="sunset",
            encoder=encoder,
            space_id=SPACE,
            generation_id=old_generation,
            dimension=DIMENSION,
        )
        assert served["binding"]["generation_state"] == "READY"
        assert served["binding"]["semantic_complete"] is True
        # The old generation answered from its own confirmed vector.
        served_vector = served["channels"]["vector"]
        assert [row["asset_id"] for row in served_vector] == [lake]
        assert served_vector[0]["distance"] == pytest.approx(2.0)
        assert served["query"]["terms"] == ['"sunset"*']

        # The new generation has nothing confirmed yet: its photos are named as pending
        # and the response never claims they are already in semantic recall.
        pending = search_assets(
            catalog,
            text="sunset",
            encoder=encoder,
            space_id=SPACE,
            generation_id=new_generation,
            dimension=DIMENSION,
        )
        assert pending["binding"]["generation_state"] == "PENDING"
        assert pending["binding"]["semantic_complete"] is False
        assert pending["channels"]["vector"] == []
        assert [entry["asset_id"] for entry in pending["pending_semantic"]["entries"]] == [sunset]
        assert pending["pending_semantic"]["reason"] == "GENERATION_NOT_READY"
        assert all(
            "vector" not in row["matched_by"] and row["semantic_ready"] is False
            for row in pending["hits"]
        )
        summary = pending_projection_summary(catalog.db_path)
        assert summary is not None
        assert summary["generations"]["PENDING"] >= 1
        assert summary["pending_entries"] >= 1
    finally:
        library.close()
