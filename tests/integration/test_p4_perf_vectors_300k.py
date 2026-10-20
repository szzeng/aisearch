"""Bounded vector health/merge regressions; only synthetic rows, no model inference."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from photo_search.catalog.errors import SPACE_MISMATCH, CatalogError
from photo_search.indexing import selection
from photo_search.indexing.store import IndexCoordinator, LanceVectorIndex
from photo_search.search import retrieval


def vector_index(tmp_path: Path, count: int = 7) -> LanceVectorIndex:
    index = LanceVectorIndex(tmp_path / "vectors")
    index.upsert_batch(
        generation={"generation_id": "synthetic", "space_id": "synthetic-space", "dimension": 4},
        records=[
            {
                "record_id": f"synthetic:{asset_id}", "asset_id": asset_id,
                "space_id": "synthetic-space", "generation_id": "synthetic",
                "artifact_revision": "test", "projection_revision": 0,
                "vector": [1.0, 0.0, 0.0, 0.0], "source_ids": [], "visible": True,
            }
            for asset_id in range(1, count + 1)
        ],
    )
    return index


def test_count_does_not_materialize_vectors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    index = vector_index(tmp_path)

    def forbidden(**kwargs: Any) -> Any:
        pytest.fail("health must not materialize every vector in Python")

    monkeypatch.setattr(index, "rows", forbidden)
    assert index.count(generation_id="synthetic", dimension=4, space_id="synthetic-space") == 7
    assert index.count(generation_id="missing", dimension=4, space_id="synthetic-space") == 0
    with pytest.raises(CatalogError) as error:
        index.count(generation_id="synthetic", dimension=4, space_id="other")
    assert error.value.code == SPACE_MISMATCH


@pytest.mark.parametrize("column,value", [("space_id", "foreign"), ("generation_id", "foreign")])
def test_count_rejects_any_foreign_row(tmp_path: Path, column: str, value: str) -> None:
    index = vector_index(tmp_path)
    table = index._table(generation_id="synthetic", dimension=4, create=False)
    table.update(where="asset_id = 7", values={column: value})
    with pytest.raises(CatalogError) as error:
        index.count(generation_id="synthetic", dimension=4, space_id="synthetic-space")
    assert error.value.code == SPACE_MISMATCH


def test_health_fully_checks_above_old_limit(tmp_path: Path) -> None:
    index = vector_index(tmp_path, count=20_001)
    report = selection.lance_health(
        index.root, generation_id="synthetic", dimension=4,
        space_id="synthetic-space", expected_rows=20_001,
    )
    assert report["state"] == "healthy"
    assert report["checked"] == "full"
    assert report["rows"] == 20_001
    mismatch = selection.lance_health(
        index.root, generation_id="synthetic", dimension=4,
        space_id="synthetic-space", expected_rows=20_002,
    )
    assert mismatch["state"] == "unhealthy"
    assert mismatch["reason"] == "ROW_COUNT_MISMATCH"
    wrong_dimension = selection.lance_health(
        index.root, generation_id="synthetic", dimension=5,
        space_id="synthetic-space", expected_rows=20_001,
    )
    assert wrong_dimension["state"] == "unhealthy"


def test_unreadable_health_count_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = vector_index(tmp_path)
    table = index._table(generation_id="synthetic", dimension=4, create=False)

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise OSError("synthetic corrupt count metadata")

    monkeypatch.setattr(type(table), "count_rows", refuse)
    report = selection.lance_health(
        index.root, generation_id="synthetic", dimension=4,
        space_id="synthetic-space", expected_rows=7,
    )
    assert report["state"] == "unhealthy"
    assert report["reason"] == "OSError"


def test_health_reads_payload_instead_of_trusting_metadata_count(tmp_path: Path) -> None:
    index = vector_index(tmp_path)
    table = index._table(generation_id="synthetic", dimension=4, create=False)
    assert table.count_rows() == 7
    data_files = list(index.root.glob("*.lance/data/*.lance"))
    assert data_files
    # The manifest remains valid, but its physical batch payload is truncated.
    # A metadata count alone would incorrectly call this generation healthy.
    data_files[0].write_bytes(data_files[0].read_bytes()[:64])
    assert table.count_rows() == 7
    report = selection.lance_health(
        index.root, generation_id="synthetic", dimension=4,
        space_id="synthetic-space", expected_rows=7,
    )
    assert report["state"] == "unhealthy"


@pytest.mark.parametrize("block_size", [1, 7, 50, 1000])
@pytest.mark.parametrize("bounded", [False, True])
def test_global_merge_matches_complete_oracle_with_ties(
    monkeypatch: pytest.MonkeyPatch, block_size: int, bounded: bool,
) -> None:
    # Simulate the SQL's unique ordered CONFIRMED candidate stream. Filtering is
    # independently covered by the real catalog/Lance block-contract integration suite.
    ids = list(range(2, 603, 2))
    rows = {i: {"asset_id": i, "distance": float((i * 17) % 11)} for i in ids}
    top_k = 23

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        "CREATE TABLE assets(asset_id INTEGER PRIMARY KEY);"
        "CREATE TABLE index_entries(asset_id INTEGER, generation_id TEXT, space_id TEXT, state TEXT);"
    )
    connection.executemany("INSERT INTO assets VALUES (?)", [(i,) for i in ids])
    connection.executemany(
        "INSERT INTO index_entries VALUES (?, 'synthetic', 'space', 'CONFIRMED')", [(i,) for i in ids]
    )

    class Catalog:
        def __init__(self) -> None:
            self.connection = connection

    class Index:
        def search_candidates(self, **kwargs: Any) -> list[dict[str, Any]]:
            return sorted(
                [rows[i] for i in kwargs["asset_ids"]],
                key=lambda row: (row["distance"], row["asset_id"]),
            )[:kwargs["limit"]]

    index = Index()
    if bounded:
        def bounded_search(**kwargs: Any) -> tuple[list[dict[str, Any]], bool]:
            result = index.search_candidates(**kwargs, limit=kwargs["top_k"] + 1)
            return result, len(result) <= top_k or result[-1]["distance"] > result[-2]["distance"]
        index.search_candidates_bounded = bounded_search  # type: ignore[attr-defined]
    monkeypatch.setattr(retrieval, "scope_condition", lambda *a, **k: ("1", []))
    try:
        result = retrieval.vector_channel(
            Catalog(), index=index,
            binding=retrieval.VectorBinding("synthetic", "space", 4, "synthetic", "READY", True),
            query_vector=[1.0, 0.0, 0.0, 0.0], scope=retrieval.SearchScope.build(),
            top_k=top_k, block_size=block_size,
        )
        expected = sorted(rows.values(), key=lambda row: (row["distance"], row["asset_id"]))[:top_k]
        assert result["hits"] == expected
        assert result["scanned_assets"] == len(ids)
        assert result["truncated"] is False
    finally:
        connection.close()


@pytest.mark.parametrize("pending_state", ["PENDING", "IN_FLIGHT", "FAILED"])
def test_ready_waits_for_every_nonconfirmed_state(pending_state: str) -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        "CREATE TABLE index_outbox(generation_id TEXT, state TEXT, event_type TEXT, asset_id INTEGER);"
        "CREATE TABLE asset_retention_barriers(asset_id INTEGER PRIMARY KEY, state TEXT NOT NULL);"
        "CREATE TABLE index_generations(generation_id TEXT, space_id TEXT, state TEXT, "
        "is_active INTEGER, updated_at INTEGER);"
        "INSERT INTO index_generations VALUES ('g', 'space', 'PENDING', 0, 0);"
    )
    try:
        connection.executemany(
            "INSERT INTO index_outbox(generation_id,state,event_type,asset_id) VALUES (?, ?, ?, ?)",
            [("other", "PENDING", "UPSERT_VECTOR", 1),
             ("g", "CONFIRMED", "UPSERT_VECTOR", 2),
             ("g", pending_state, "UPSERT_VECTOR", 3)],
        )
        IndexCoordinator._make_generation_ready(connection, "g", 1)
        assert connection.execute("SELECT state FROM index_generations").fetchone()[0] == "PENDING"
        connection.execute("UPDATE index_outbox SET state='CONFIRMED' WHERE generation_id='g'")
        IndexCoordinator._make_generation_ready(connection, "g", 2)
        row = connection.execute("SELECT state, is_active FROM index_generations").fetchone()
        assert tuple(row) == ("READY", 1)
    finally:
        connection.close()
