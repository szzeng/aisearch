"""Exact vector candidate filters and honest FTS projection lag, using synthetic data."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import FILTER_UNSUPPORTED, SPACE_MISMATCH, CatalogError
from photo_search.indexing.store import (
    CANDIDATE_ROW_COLUMNS,
    LanceVectorIndex,
    _candidate_id_condition,
)
from photo_search.search import documents, service


def make_index(tmp_path: Path, *, ties: bool = False) -> LanceVectorIndex:
    index = LanceVectorIndex(tmp_path / "vectors")
    index.upsert_batch(
        generation={"generation_id": "synthetic", "space_id": "space", "dimension": 4},
        records=[
            {
                "record_id": f"synthetic:{asset_id}", "asset_id": asset_id,
                "space_id": "space", "generation_id": "synthetic",
                "artifact_revision": "test", "projection_revision": 0,
                "vector": [0.0 if ties else asset_id / 300.0, 1.0, 0.0, 0.0],
                "source_ids": [], "visible": True,
            }
            for asset_id in range(1, 257)
        ],
    )
    return index


@pytest.mark.parametrize("scalar_index", [False, True])
def test_candidate_paths_match_exhaustive_in_oracle_with_holes_and_foreign_space(
    tmp_path: Path, scalar_index: bool,
) -> None:
    index = make_index(tmp_path)
    table = index._table(generation_id="synthetic", dimension=4, create=False)
    table.update(where="asset_id IN (5, 75)", values={"space_id": "foreign"})
    if scalar_index:
        assert index.ensure_scalar_index(generation_id="synthetic", dimension=4)["state"] == "created"
    candidates = [i for i in range(1, 240) if i not in {1, 80, 81, 115, 210}]
    query = [0.0, 1.0, 0.0, 0.0]
    oracle = index.search(
        generation_id="synthetic", space_id="space", dimension=4, query_vector=query,
        limit=len(candidates), columns=CANDIDATE_ROW_COLUMNS,
        where="asset_id IN (" + ", ".join(map(str, candidates)) + ") AND space_id = 'space'",
    )
    # Public methods must normalize caller order and duplicates before compacting.
    supplied = list(reversed(candidates)) + candidates[::7]
    complete = index.search_candidates(
        generation_id="synthetic", space_id="space", dimension=4, query_vector=query,
        asset_ids=supplied, limit=len(candidates),
    )
    assert complete == oracle
    assert {row["asset_id"] for row in complete} == set(candidates) - {5, 75}
    bounded, resolved = index.search_candidates_bounded(
        generation_id="synthetic", space_id="space", dimension=4, query_vector=query,
        asset_ids=supplied, top_k=19,
    )
    assert resolved is True
    assert bounded == oracle[:20]


@pytest.mark.parametrize("scalar_index", [False, True])
def test_compact_candidate_tie_boundary_still_requires_complete_order(
    tmp_path: Path, scalar_index: bool,
) -> None:
    index = make_index(tmp_path, ties=True)
    if scalar_index:
        index.ensure_scalar_index(generation_id="synthetic", dimension=4)
    ids = [i for i in range(1, 230) if i not in {1, 4, 101, 222}]
    arguments = {
        "generation_id": "synthetic", "space_id": "space", "dimension": 4,
        "query_vector": [0.0, 1.0, 0.0, 0.0], "asset_ids": list(reversed(ids)),
    }
    bounded, resolved = index.search_candidates_bounded(**arguments, top_k=9)
    assert resolved is False
    assert len(bounded) == 10
    complete = index.search_candidates(**arguments, limit=9)
    assert [row["asset_id"] for row in complete] == ids[:9]
    all_rows, resolved = index.search_candidates_bounded(**arguments, top_k=len(ids))
    assert resolved is True
    assert [row["asset_id"] for row in all_rows] == ids


@pytest.mark.parametrize("bounded", [False, True])
def test_compact_candidate_filter_rejects_foreign_generation(tmp_path: Path, bounded: bool) -> None:
    index = make_index(tmp_path)
    table = index._table(generation_id="synthetic", dimension=4, create=False)
    table.update(where="asset_id = 5", values={"generation_id": "foreign"})
    with pytest.raises(CatalogError) as error:
        arguments = {
            "generation_id": "synthetic", "space_id": "space", "dimension": 4,
            "query_vector": [0.0, 1.0, 0.0, 0.0], "asset_ids": list(range(1, 200)),
        }
        if bounded:
            index.search_candidates_bounded(**arguments, top_k=20)
        else:
            index.search_candidates(**arguments, limit=20)
    assert error.value.code == SPACE_MISMATCH


def test_compact_candidate_filter_keeps_int64_boundaries_in_lance(tmp_path: Path) -> None:
    index = LanceVectorIndex(tmp_path / "vectors")
    # The locked Lance parser refuses the bare Int64 minimum in the old IN path
    # as well; its unchanged structured refusal has a separate regression below.
    ids = list(range(-(2**63) + 1, -(2**63) + 65)) + list(range(2**63 - 64, 2**63))
    index.upsert_batch(
        generation={"generation_id": "edges", "space_id": "space", "dimension": 2},
        records=[
            {
                "record_id": f"edges:{asset_id}", "asset_id": asset_id,
                "space_id": "space", "generation_id": "edges",
                "artifact_revision": "test", "projection_revision": 0,
                "vector": [0.0, 1.0], "source_ids": [], "visible": True,
            }
            for asset_id in ids + [-(2**63), -(2**63) + 65, 2**63 - 65, 0]
        ],
    )
    result = index.search_candidates(
        generation_id="edges", space_id="space", dimension=2,
        query_vector=[0.0, 1.0], asset_ids=list(reversed(ids)) + ids, limit=len(ids),
    )
    assert [row["asset_id"] for row in result] == ids


def test_int64_minimum_literal_keeps_original_filter_refusal(tmp_path: Path) -> None:
    index = make_index(tmp_path)
    ids = list(range(-(2**63), -(2**63) + 64))
    with pytest.raises(CatalogError) as original_error:
        index.search(
            generation_id="synthetic", space_id="space", dimension=4,
            query_vector=[0.0, 1.0, 0.0, 0.0], limit=64,
            where="asset_id IN (" + ", ".join(map(str, ids)) + ")",
        )
    with pytest.raises(CatalogError) as compact_error:
        index.search_candidates(
            generation_id="synthetic", space_id="space", dimension=4,
            query_vector=[0.0, 1.0, 0.0, 0.0], asset_ids=ids, limit=64,
        )
    assert original_error.value.code == compact_error.value.code == FILTER_UNSUPPORTED


def test_maximum_compact_runs_are_supported_by_lance_without_filling_holes(tmp_path: Path) -> None:
    index = LanceVectorIndex(tmp_path / "vectors")
    ids = [100 * run + offset for run in range(64) for offset in range(20)]
    assert _candidate_id_condition(ids).count("BETWEEN") == 64
    # Every interval has a neighboring excluded row at an equally good distance.
    rows = ids + [100 * run + 20 for run in range(64)]
    index.upsert_batch(
        generation={"generation_id": "runs", "space_id": "space", "dimension": 2},
        records=[
            {
                "record_id": f"runs:{asset_id}", "asset_id": asset_id,
                "space_id": "space", "generation_id": "runs",
                "artifact_revision": "test", "projection_revision": 0,
                "vector": [0.0, 1.0], "source_ids": [], "visible": True,
            }
            for asset_id in rows
        ],
    )
    result = index.search_candidates(
        generation_id="runs", space_id="space", dimension=2,
        query_vector=[0.0, 1.0], asset_ids=list(reversed(ids)), limit=len(ids),
    )
    assert [row["asset_id"] for row in result] == ids


def test_empty_candidates_do_not_open_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    index = LanceVectorIndex(tmp_path / "vectors")

    def forbidden(**kwargs: Any) -> Any:
        pytest.fail("empty authoritative scope must never query the engine")

    monkeypatch.setattr(index, "_table", forbidden)
    arguments = {
        "generation_id": "synthetic", "space_id": "space", "dimension": 4,
        "query_vector": [0.0, 1.0, 0.0, 0.0], "asset_ids": [],
    }
    assert index.search_candidates(**arguments, limit=10) == []
    assert index.search_candidates_bounded(**arguments, top_k=10) == ([], True)


@pytest.fixture()
def text_catalog(tmp_path: Path):
    with Catalog.open(tmp_path / "data/catalog/catalog.db", lease_seconds=60) as catalog:
        with catalog.write() as connection:
            connection.execute(
                "INSERT INTO source_roots(display_name,root_path,volume_hint,status) "
                "VALUES ('synthetic', ?, 'D:', 'ONLINE')", (str(tmp_path / "photos"),),
            )
            connection.execute(
                "INSERT INTO assets(sha256,byte_size,created_at,state) "
                "VALUES (?,4096,1000,'ACTIVE')", (hashlib.sha256(b"synthetic").hexdigest(),),
            )
            connection.execute(
                "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,"
                "mtime_ns,last_seen_scan_id,availability) "
                "VALUES (1,'before.jpg','before.jpg',1,4096,1,NULL,'PRESENT')"
            )
        documents.catch_up(catalog)
        yield catalog


def test_search_reports_new_projection_work_after_catch_up(
    text_catalog: Catalog, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = documents.catch_up

    def racing_catch_up(catalog: Catalog) -> dict[str, Any]:
        result = original(catalog)
        assert result["state"] == "SYNCED"
        with catalog.write() as connection:
            connection.execute(
                "UPDATE file_locations SET relative_path='needle.jpg', path_key='needle.jpg' "
                "WHERE asset_id=1"
            )
        return result

    monkeypatch.setattr(documents, "catch_up", racing_catch_up)
    raced = service.search_assets(text_catalog, text="needle")
    assert raced["hits"] == []
    assert raced["state"] == "OK"
    assert raced["search_projection"]["queued_assets"] == 1
    assert raced["search_projection"]["state"] == "LAGGING"
    assert raced["search_projection"]["processed_assets"] == 0
    monkeypatch.setattr(documents, "catch_up", original)
    recovered = service.search_assets(text_catalog, text="needle")
    assert [row["asset_id"] for row in recovered["hits"]] == [1]
    assert recovered["search_projection"]["state"] == "SYNCED"
    assert recovered["search_projection"]["processed_assets"] == 1


def test_search_without_catch_up_reports_stale_tokenizer_documents(text_catalog: Catalog) -> None:
    with text_catalog.write() as connection:
        connection.execute("UPDATE search_documents SET tokenizer_version = tokenizer_version + 1")
    result = service.search_assets(text_catalog)
    assert result["search_projection"]["queued_assets"] == 0
    assert result["search_projection"]["stale_documents"] == 1
    assert result["search_projection"]["state"] == "LAGGING"
    assert result["search_projection"]["processed_assets"] == 0


def test_projection_state_uses_current_queue_after_other_worker_drains(text_catalog: Catalog) -> None:
    block = service._projection_block(
        text_catalog, match=None, catch_up={"state": "LAGGING", "processed": 7},
    )
    assert block["state"] == "SYNCED"
    assert block["processed_assets"] == 7
