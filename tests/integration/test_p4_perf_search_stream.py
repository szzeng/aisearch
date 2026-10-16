"""Exact search candidates and count policy after bounded SQLite streaming."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from photo_search.catalog.errors import CATALOG_FILTER_REVISION_STALE
from photo_search.search import retrieval, service


class TrackedCursor(sqlite3.Cursor):
    closed = False
    batch_sizes: list[int]

    def fetchmany(self, size: int = 1) -> list[sqlite3.Row]:
        if not hasattr(self, "batch_sizes"):
            self.batch_sizes = []
        result = super().fetchmany(size)
        self.batch_sizes.append(len(result))
        return result

    def close(self) -> None:
        self.closed = True
        super().close()


class Connection(sqlite3.Connection):
    candidate_cursors: list[TrackedCursor]

    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        cursor = self.cursor(TrackedCursor)
        cursor.execute(sql, parameters)
        if sql.startswith("SELECT e.asset_id FROM index_entries AS e"):
            if not hasattr(self, "candidate_cursors"):
                self.candidate_cursors = []
            self.candidate_cursors.append(cursor)
        return cursor


class Catalog:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def read_one(self, sql: str, parameters: Any = ()) -> Any:
        return self.connection.execute(sql, parameters).fetchone()

    def read_all(self, sql: str, parameters: Any = ()) -> list[sqlite3.Row]:
        return self.connection.execute(sql, parameters).fetchall()


def database(path: str | Path = ":memory:", count: int = 12) -> Catalog:
    connection = sqlite3.connect(path, isolation_level=None, factory=Connection)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        "CREATE TABLE assets(asset_id INTEGER PRIMARY KEY);"
        "CREATE TABLE file_locations(asset_id INTEGER, source_id INTEGER, "
        "relative_path TEXT, availability TEXT);"
        "CREATE INDEX idx_file_locations_asset ON file_locations(asset_id);"
        "CREATE TABLE index_entries(asset_id INTEGER, generation_id TEXT, space_id TEXT, state TEXT);"
        "CREATE TABLE eligible(asset_id INTEGER PRIMARY KEY);"
        "CREATE TABLE catalog_filter_revision(revision_id INTEGER PRIMARY KEY,revision INTEGER);"
        "INSERT INTO catalog_filter_revision VALUES(1,1);"
    )
    connection.executemany("INSERT INTO assets VALUES (?)", [(i,) for i in range(1, count + 1)])
    connection.executemany("INSERT INTO eligible VALUES (?)", [(i,) for i in range(1, count + 1)])
    connection.executemany(
        "INSERT INTO index_entries VALUES (?, 'g', 's', 'CONFIRMED')",
        [(i,) for i in range(1, count + 1)],
    )
    return Catalog(connection)


class Index:
    def __init__(self, *, action: Any = None, bounded: bool = False) -> None:
        self.calls: list[list[int]] = []
        self.action = action
        self.fallback_calls = 0
        if bounded:
            self.search_candidates_bounded = self.bounded

    def search_candidates(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.fallback_calls += 1
        self.calls.append(list(kwargs["asset_ids"]))
        if len(self.calls) == 1 and self.action is not None:
            self.action()
        rows = [{"asset_id": i, "distance": float(i % 3)} for i in kwargs["asset_ids"]]
        return sorted(rows, key=lambda row: (row["distance"], row["asset_id"]))[:kwargs["limit"]]

    def bounded(self, **kwargs: Any) -> tuple[list[dict[str, Any]], bool]:
        # Deliberately unresolved boundary ties require the existing exhaustive fallback.
        return [], False


def run(catalog: Catalog, index: Index, **kwargs: Any) -> dict[str, Any]:
    return retrieval.vector_channel(
        catalog, index=index, binding=retrieval.VectorBinding("g", "s", 3, "p", "READY", True),
        query_vector=[1.0, 0.0, 0.0],
        scope=kwargs.pop("scope", retrieval.SearchScope()), top_k=5, **kwargs,
    )


@pytest.mark.parametrize("block_size", [1, 4, 5, 30])
@pytest.mark.parametrize("bounded", [False, True])
def test_single_candidate_statement_preserves_members_ties_and_identity(
    block_size: int, bounded: bool,
) -> None:
    catalog = database()
    connection = catalog.connection
    connection.execute("UPDATE index_entries SET state='PENDING' WHERE asset_id=9")
    connection.execute("UPDATE index_entries SET generation_id='old' WHERE asset_id=10")
    connection.execute("UPDATE index_entries SET space_id='other' WHERE asset_id=11")
    connection.execute("DELETE FROM eligible WHERE asset_id IN (2,7)")
    visits: list[int] = []
    connection.create_function("visit", 1, lambda value: visits.append(value) or 1)
    scope = retrieval.SearchScope(additional_condition_sql=
        "a.asset_id IN (SELECT asset_id FROM eligible WHERE visit(asset_id))")
    try:
        index = Index(bounded=bounded)
        result = run(catalog, index, block_size=block_size, scope=scope)
        expected = [1, 3, 4, 5, 6, 8, 12]
        assert [i for block in index.calls for i in block] == expected
        assert [r["asset_id"] for r in result["hits"]] == sorted(expected, key=lambda i: (i % 3, i))[:5]
        assert result["scanned_assets"] == len(expected)
        assert result["truncated"] is False
        assert sorted(visits) == [1, 3, 4, 5, 6, 8, 9, 10, 11, 12]
        assert len(connection.candidate_cursors) == 1
        cursor = connection.candidate_cursors[0]
        assert cursor.closed and max(cursor.batch_sizes) <= block_size
    finally:
        connection.close()


@pytest.mark.parametrize("count,block_size,max_blocks,truncated,scanned", [
    (0, 3, 1, False, 0), (6, 3, 2, True, 6), (7, 3, 2, True, 6),
    (5, 3, 2, False, 5), (6, 3, 3, False, 6),
])
def test_max_blocks_keeps_existing_truncation_boundary_and_closes_cursor(
    count: int, block_size: int, max_blocks: int, truncated: bool, scanned: int,
) -> None:
    catalog = database(count=count)
    try:
        # Caller-owned transaction must not be committed or rolled back by retrieval.
        catalog.connection.execute("BEGIN")
        result = run(catalog, Index(), block_size=block_size, max_blocks=max_blocks)
        assert result["truncated"] is truncated
        assert result["scanned_assets"] == scanned
        assert catalog.connection.in_transaction
        assert catalog.connection.candidate_cursors[0].closed
        catalog.connection.rollback()
    finally:
        catalog.connection.close()


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_engine_failure_closes_stream_without_committing_caller(failure: type[BaseException]) -> None:
    catalog = database()
    def fail() -> None:
        raise failure("injected engine interruption")
    try:
        catalog.connection.execute("BEGIN")
        with pytest.raises(failure):
            run(catalog, Index(action=fail), block_size=2)
        assert catalog.connection.candidate_cursors[0].closed
        assert catalog.connection.in_transaction
    finally:
        catalog.connection.rollback()
        catalog.connection.close()


def test_wal_write_during_stream_is_seen_by_live_recheck_after_close(tmp_path: Path) -> None:
    path = tmp_path / "stream.db"
    catalog = database(path, count=8)
    catalog.connection.execute("PRAGMA journal_mode=WAL")
    writer = sqlite3.connect(path, isolation_level=None, timeout=1)
    def edit() -> None:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("DELETE FROM eligible WHERE asset_id=8")
        writer.execute("UPDATE index_entries SET state='FAILED' WHERE asset_id=7")
        writer.execute("UPDATE catalog_filter_revision SET revision=2 WHERE revision_id=1")
        writer.commit()
    scope = retrieval.SearchScope(additional_condition_sql="a.asset_id IN (SELECT asset_id FROM eligible)")
    try:
        index = Index(action=edit)
        run(catalog, index, block_size=2, scope=scope)
        # The one statement retains its starting candidate set across all blocks.
        assert [i for block in index.calls for i in block] == list(range(1, 9))
        assert catalog.connection.candidate_cursors[0].closed
        # Final service checks are live again and reject authority drift before publication.
        assert service._verified_ids(catalog, scope, [7, 8]) == {7}
        base = service._AuthorityBase((), 1, 1)
        request = SimpleNamespace(place_state="any")
        assert service._authority_drift(catalog, request, base) == "filter"
        assert service._publication_refusal(catalog, request, base).code == CATALOG_FILTER_REVISION_STALE
        assert not catalog.connection.in_transaction
        assert writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
    finally:
        writer.close()
        catalog.connection.close()


def test_global_count_preserves_legacy_shared_nonimage_missing_and_null_locations() -> None:
    catalog = database(count=8)
    rows = [
        (1, 1, "a.JPG", "PRESENT"), (1, 2, "a.txt", "PRESENT"),
        (2, 1, "b.mp4", "PRESENT"), (3, 1, "c.txt", "MISSING"),
        (4, 1, "d.HEIC", "MISSING"), (4, 2, "d.mp4", "PRESENT"),
        (6, 1, "false.jpg.bak", "PRESENT"), (7, 1, "g.JPEG", "PRESENT"),
        (8, 1, "h.txt", "PRESENT"), (8, 2, "h.png", "PRESENT"),
        (None, 1, "unbound.txt", "PRESENT"),
    ]
    catalog.connection.executemany("INSERT INTO file_locations VALUES (?,?,?,?)", rows)
    try:
        expected = {
            retrieval.SearchScope(): [1, 4, 5, 7, 8],
            retrieval.SearchScope(source_id=1): [1, 4, 7],
            retrieval.SearchScope(source_id=2): [8],
            retrieval.SearchScope(availability="missing"): [5],
            retrieval.SearchScope(availability="present"): [1, 4, 7, 8],
            retrieval.SearchScope(album_asset_ids=()): [],
            retrieval.SearchScope(additional_condition_sql="a.asset_id>=7"): [7, 8],
        }
        for scope, ids in expected.items():
            result = retrieval.authoritative_page(catalog, scope, limit=2)
            assert result["total"] == len(ids)
            assert result["asset_ids"] == ids[:2]
            assert result["truncated"] == (len(ids) > 2)
    finally:
        catalog.connection.close()


@pytest.mark.parametrize("block_size", [0, -1])
def test_invalid_batch_size_cannot_materialize_all_candidates(block_size: int) -> None:
    catalog = database()
    try:
        with pytest.raises(ValueError, match="block_size must be positive"):
            run(catalog, Index(), block_size=block_size)
        assert not hasattr(catalog.connection, "candidate_cursors")
    finally:
        catalog.connection.close()
