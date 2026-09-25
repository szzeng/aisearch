"""P4-02J: the ``asset_id`` scalar index must cover every written row.

P4-02D finding F4 measured the product state: ``ensure_scalar_index`` returned ``present`` for
any existing index and never rebuilt it, while the generation is written in many small batches
- so on a wave-built root the index covered only the rows of the first batch (10 000 -> 5 000,
300 000 -> 5 000) and every candidate block degraded into a whole-generation scan.

These tests drive the product adapter directly: write rows, let the maintenance entry build
the index, append more rows (which is what a later wave does), and assert the next maintenance
pass repairs the coverage without changing the answer.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from photo_search.indexing.store import ASSET_SCALAR_INDEX, LanceVectorIndex

GENERATION_ID = "p4-02j-coverage"
SPACE_ID = "p4-02j-space"
DIMENSION = 8


def _records(first: int, last: int) -> list[dict[str, Any]]:
    return [
        {
            "record_id": f"{GENERATION_ID}:{value}",
            "asset_id": value,
            "generation_id": GENERATION_ID,
            "space_id": SPACE_ID,
            "artifact_revision": "a1",
            "projection_revision": 1,
            "vector": [float(value % 5)] * DIMENSION,
            "source_ids": [],
            "visible": True,
        }
        for value in range(first, last + 1)
    ]


def _coverage(index: LanceVectorIndex) -> tuple[int, int]:
    table = index._table(generation_id=GENERATION_ID, dimension=DIMENSION, create=False)
    assert table is not None
    for item in table.list_indices():
        if str(getattr(item, "name", item)) == ASSET_SCALAR_INDEX:
            return (
                int(getattr(item, "num_indexed_rows", -1)),
                int(getattr(item, "num_unindexed_rows", -1)),
            )
    raise AssertionError("the scalar index is missing")


def _page(index: LanceVectorIndex, query: list[float], asset_ids: list[int]) -> list[list[Any]]:
    rows = index.search_candidates(
        generation_id=GENERATION_ID,
        dimension=DIMENSION,
        query_vector=query,
        asset_ids=asset_ids,
        limit=5,
        space_id=SPACE_ID,
    )
    return [[int(row["asset_id"]), round(float(row["distance"]), 9)] for row in rows]


def test_a_later_wave_is_repaired_by_the_next_maintenance_pass(tmp_path: Path) -> None:
    root = tmp_path / "vectors"
    index = LanceVectorIndex(root)
    generation = {"generation_id": GENERATION_ID, "dimension": DIMENSION, "space_id": SPACE_ID}
    try:
        # Wave 1: the maintenance entry builds the index while only the first rows exist.
        index.upsert_batch(generation=generation, records=_records(1, 300))
        first = index.ensure_scalar_index(generation_id=GENERATION_ID, dimension=DIMENSION)
        assert first["state"] in {"created", "unavailable"}
        if first["state"] == "unavailable":  # pragma: no cover - engine refused the index
            return
        indexed, unindexed = _coverage(index)
        assert (indexed, unindexed) == (300, 0)

        # Wave 2 (and 3): later rows land outside the existing index - the P4-02D shape.
        index.upsert_batch(generation=generation, records=_records(301, 500))
        assert _coverage(index) == (300, 200)
        page_before = _page(index, [0.0] * DIMENSION, list(range(1, 501)))

        repaired = index.ensure_scalar_index(generation_id=GENERATION_ID, dimension=DIMENSION)
        assert repaired["state"] == "refreshed"
        assert repaired["unindexed_rows_before"] == 200
        assert _coverage(index) == (500, 0)

        # The access path changed; the answer must not.
        assert _page(index, [0.0] * DIMENSION, list(range(1, 501))) == page_before

        # An index that already covers every row is left alone (no rebuild budget spent).
        assert index.ensure_scalar_index(
            generation_id=GENERATION_ID, dimension=DIMENSION
        ) == {"state": "present", "index": ASSET_SCALAR_INDEX}
        assert _coverage(index) == (500, 0)
    finally:
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)


def test_a_generation_without_the_index_is_still_created_once(tmp_path: Path) -> None:
    root = tmp_path / "vectors"
    index = LanceVectorIndex(root)
    generation = {"generation_id": GENERATION_ID, "dimension": DIMENSION, "space_id": SPACE_ID}
    try:
        index.upsert_batch(generation=generation, records=_records(1, 120))
        created = index.ensure_scalar_index(generation_id=GENERATION_ID, dimension=DIMENSION)
        assert created["state"] in {"created", "unavailable"}
        if created["state"] == "created":
            assert _coverage(index) == (120, 0)
    finally:
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
