"""P4-01C acceptance: block-size access path, scalar index, point ``has``, batched replay.

The card's decisions are checked at their real seams rather than by reading constants:

* ``SCOPE_BLOCK_SIZE`` is an *access-path* parameter - the merged answer must be identical
  for every block size, and only the number of engine queries may change;
* the ``asset_id`` scalar index is a maintenance artifact with a deterministic fallback, and
  no query depends on it;
* ``has`` is a point query with a materialising fallback;
* batch replay keeps the per-event outbox contract: all-or-nothing per batch, replayed
  batches are idempotent, and the ``after_lance`` crash window is unchanged.

The predicate rewrite (``EXISTS`` -> ``asset_id IN (SELECT ...)``) is checked for *set
equality* against the previous correlated forms on a small hand-built catalog, including the
NULL / duplicate / MANUAL_UNASSIGNED / exclusion / SUGGESTED edge cases, because the point of
that change is the query plan and not a new meaning.
"""

from __future__ import annotations

import hashlib
import importlib.util
import math
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from photo_search.catalog.db import Catalog
from photo_search.indexing.store import ASSET_SCALAR_INDEX, IndexCoordinator, LanceVectorIndex
from photo_search.persons.members import MEMBER_PREDICATE
from photo_search.search import retrieval

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = PROJECT_ROOT / "tools" / "diagnostics" / "ann_scale_benchmark.py"


def load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("p4_01_benchmark", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


benchmark = load_tool()


def build(tmp_path: Path, scale: int, *, name: str, seed: int) -> Any:
    return benchmark.build_fixture(
        tmp_path / name, scale=scale, seed=seed, distribution="clustered"
    )


def walk_with_block(fixture: Any, *, block_size: int, top_k: int = 20) -> dict[str, Any]:
    """The production candidate walk with an explicit block bound."""
    query = benchmark.synthetic_vectors(1, seed=5, distribution="clustered")
    return retrieval.vector_channel(
        fixture.catalog,
        index=LanceVectorIndex(fixture.vectors_dir),
        binding=retrieval.resolve_binding(
            fixture.catalog,
            generation_id=benchmark.GENERATION_ID,
            space_id=benchmark.SPACE_ID,
            dimension=benchmark.DIM,
        ),
        query_vector=[float(value) for value in query[0]],
        scope=retrieval.SearchScope.build(),
        top_k=top_k,
        block_size=block_size,
    )


# --------------------------------------------------------------- block size is access path
def test_block_size_is_an_access_path_parameter(tmp_path: Path) -> None:
    fixture = build(tmp_path, 600, name="scale-600", seed=20260923)
    try:
        assert retrieval.SCOPE_BLOCK_SIZE >= 5000
        baseline: list[int] | None = None
        for block_size in (retrieval.SCOPE_BLOCK_SIZE, 1000, 100_000):
            block = walk_with_block(fixture, block_size=block_size)
            assert block["scanned_assets"] == 600
            assert block["blocks"] == min(
                math.ceil(600 / block_size), retrieval.MAX_VECTOR_BLOCKS
            )
            ids = [int(row["asset_id"]) for row in block["hits"]]
            if baseline is None:
                baseline = ids
            else:
                # The merge is exact per block, so a smaller block count cannot change the
                # answer - only how many whole-generation scans produced it.
                assert ids == baseline
        assert baseline is not None and len(baseline) == 20
    finally:
        fixture.catalog.close()


def test_a_block_of_tied_distances_cannot_be_decided_by_the_engine_cut(
    tmp_path: Path,
) -> None:
    """P4-01C focused review B1: ties must not let the block size choose the answer.

    With every row at the same distance, an engine-side ``limit`` truncates *before* the
    caller's ``(distance, asset_id)`` order exists, so a 1 000-row block and a 5 000-row block
    would return different asset ids for the same query. The candidate path therefore asks
    the engine for one row per candidate id and applies the block bound afterwards.
    """

    scale = 30
    fixture = build(tmp_path, scale, name="scale-30-ties", seed=31)
    try:
        index = LanceVectorIndex(fixture.vectors_dir)
        identical = [1.0] + [0.0] * (benchmark.DIM - 1)
        for asset_id in range(scale, 0, -1):
            index.upsert(
                generation={
                    "generation_id": benchmark.GENERATION_ID,
                    "space_id": benchmark.SPACE_ID,
                    "dimension": benchmark.DIM,
                },
                record={
                    "record_id": f"{benchmark.GENERATION_ID}:{asset_id}",
                    "asset_id": asset_id,
                    "generation_id": benchmark.GENERATION_ID,
                    "space_id": benchmark.SPACE_ID,
                    "artifact_revision": "rev-tied",
                    "projection_revision": 0,
                    "vector": identical,
                    "source_ids": [fixture.source_ids[0]],
                    "visible": True,
                },
            )

        binding = retrieval.resolve_binding(
            fixture.catalog,
            generation_id=benchmark.GENERATION_ID,
            space_id=benchmark.SPACE_ID,
            dimension=benchmark.DIM,
        )
        observed: list[tuple[list[int], int]] = []
        for block_size in (5, 8, 1000, 5000):
            block = retrieval.vector_channel(
                fixture.catalog,
                index=index,
                binding=binding,
                query_vector=identical,
                scope=retrieval.SearchScope.build(),
                top_k=2,
                block_size=block_size,
            )
            ids = [int(row["asset_id"]) for row in block["hits"]]
            assert block["truncated"] is False
            observed.append((ids, int(block["blocks"])))

        # Every block size saw every candidate (the walk is complete) ...
        assert [blocks for _, blocks in observed] == [6, 4, 1, 1]
        # ... and the answer is the same fully ordered top-2, not whatever the engine cut off.
        assert {tuple(ids) for ids, _ in observed} == {(1, 2)}
        assert {round(float(row["distance"]), 6) for row in
                retrieval.vector_channel(
                    fixture.catalog,
                    index=index,
                    binding=binding,
                    query_vector=identical,
                    scope=retrieval.SearchScope.build(),
                    top_k=2,
                    block_size=5,
                )["hits"]} == {0.0}
    finally:
        fixture.catalog.close()


# --------------------------------------------------------------------- scalar index states
def test_ensure_scalar_index_reports_every_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = build(tmp_path, 300, name="scale-300", seed=7)
    try:
        index = LanceVectorIndex(fixture.vectors_dir)
        assert index.ensure_scalar_index(
            generation_id="no-such-generation", dimension=benchmark.DIM
        )["state"] == "no_table"

        first = index.ensure_scalar_index(
            generation_id=benchmark.GENERATION_ID, dimension=benchmark.DIM
        )
        assert first["state"] in {"created", "unavailable"}
        again = index.ensure_scalar_index(
            generation_id=benchmark.GENERATION_ID, dimension=benchmark.DIM
        )
        assert again["state"] in {"present", "unavailable"}
        if first["state"] == "created":
            assert again["state"] == "present"
            table = index._table(
                generation_id=benchmark.GENERATION_ID,
                dimension=benchmark.DIM,
                create=False,
            )
            assert ASSET_SCALAR_INDEX in {str(item.name) for item in table.list_indices()}

        # A refused index is reported, never raised: the candidate path stays correct.
        other = f"{benchmark.GENERATION_ID}-declined"
        benchmark.write_generation_table(fixture, generation_id=other)
        table = index._table(generation_id=other, dimension=benchmark.DIM, create=False)
        assert table is not None

        def refuse(self: Any, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("refused by the engine")

        monkeypatch.setattr(type(table), "create_index", refuse)
        declined = index.ensure_scalar_index(generation_id=other, dimension=benchmark.DIM)
        assert declined["state"] == "unavailable"
        assert declined["reason"] == "RuntimeError"

        # P4-01C focused review B2: an unreadable index *catalogue* is reported, never raised.
        def refuse_catalogue(self: Any, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("index metadata unavailable")

        monkeypatch.setattr(type(table), "list_indices", refuse_catalogue)
        unreadable = index.ensure_scalar_index(
            generation_id=benchmark.GENERATION_ID, dimension=benchmark.DIM
        )
        assert unreadable["state"] == "unavailable"
        assert unreadable["reason"] == "RuntimeError"
    finally:
        fixture.catalog.close()


def test_a_refused_index_catalogue_never_blocks_the_outbox_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P4-01C focused review B2: maintenance must still drain when the catalogue is refused."""

    fixture = build(tmp_path, 30, name="scale-30-refused", seed=37)
    try:
        index = LanceVectorIndex(fixture.vectors_dir)
        table = index._table(
            generation_id=benchmark.GENERATION_ID,
            dimension=benchmark.DIM,
            create=False,
        )
        assert table is not None

        def refuse_catalogue(self: Any, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("index metadata unavailable")

        monkeypatch.setattr(type(table), "list_indices", refuse_catalogue)
        vectors = benchmark.synthetic_vectors(1, seed=41, distribution="clustered")
        benchmark.publish_embedding(
            fixture,
            artifact_id="p4-01c-refused-catalogue",
            asset_id=1,
            vector=vectors[0],
            now=benchmark.now_ms(),
            input_revision=hashlib.sha256(b"refused-catalogue").hexdigest(),
        )
        assert benchmark.outbox_counts(fixture) == {"PENDING": 1}

        coordinator = IndexCoordinator(fixture.catalog)
        assert coordinator.process_all(now_ms=benchmark.now_ms()) == 1
        assert benchmark.outbox_counts(fixture) == {"CONFIRMED": 1}
        assert coordinator.scalar_index_report
        assert all(
            report["state"] in {"unavailable", "no_table"}
            for report in coordinator.scalar_index_report.values()
        )
    finally:
        fixture.catalog.close()


# ------------------------------------------------------------------------- has() as a point
def test_has_is_a_point_query_with_a_correct_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = build(tmp_path, 300, name="scale-300", seed=9)
    try:
        index = LanceVectorIndex(fixture.vectors_dir)
        assert index.has(
            generation_id=benchmark.GENERATION_ID, dimension=benchmark.DIM, asset_id=7
        )
        assert not index.has(
            generation_id=benchmark.GENERATION_ID, dimension=benchmark.DIM, asset_id=10_000
        )

        calls: list[int] = []
        original_rows = LanceVectorIndex.rows

        def counted(self: Any, **kwargs: Any) -> Any:
            calls.append(1)
            return original_rows(self, **kwargs)

        monkeypatch.setattr(LanceVectorIndex, "rows", counted)
        assert index.has(
            generation_id=benchmark.GENERATION_ID, dimension=benchmark.DIM, asset_id=7
        )
        # The fast path never materialises the generation.
        assert calls == []

        table = index._table(
            generation_id=benchmark.GENERATION_ID, dimension=benchmark.DIM, create=False
        )
        assert table is not None

        def refuse_count(self: Any, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("filter refused")

        monkeypatch.setattr(type(table), "count_rows", refuse_count)
        assert index.has(
            generation_id=benchmark.GENERATION_ID, dimension=benchmark.DIM, asset_id=7
        )
        assert not index.has(
            generation_id=benchmark.GENERATION_ID, dimension=benchmark.DIM, asset_id=10_000
        )
        # A refused filter falls back to the exact materialising scan, once per call.
        assert calls == [1, 1]
    finally:
        fixture.catalog.close()


# --------------------------------------------------------------------- batched replay (C1)
def test_batched_replay_is_idempotent_and_survives_the_crash_window(tmp_path: Path) -> None:
    fixture = build(tmp_path, 40, name="scale-40", seed=11)
    try:
        base = fixture.scale
        added = 25
        vectors = benchmark.synthetic_vectors(added, seed=13, distribution="clustered")
        with fixture.catalog.write() as connection:
            for offset in range(added):
                asset_id = base + 1 + offset
                connection.execute(
                    "INSERT INTO assets(sha256, byte_size, width, height, format, "
                    "created_at, state) VALUES (?, 2000000, 4032, 3024, 'JPEG', ?, 'ACTIVE')",
                    (f"{asset_id:064x}", benchmark.now_ms()),
                )
                connection.execute(
                    "INSERT INTO file_locations(source_id, relative_path, path_key, "
                    "asset_id, size, mtime_ns, last_seen_scan_id, availability) "
                    "VALUES (?, ?, ?, ?, 2000000, 1700000000000000000, NULL, 'PRESENT')",
                    (
                        fixture.source_ids[0],
                        f"batch/photo-{asset_id:08d}.jpg",
                        f"batch/photo-{asset_id:08d}.jpg",
                        asset_id,
                    ),
                )
        for offset in range(added):
            benchmark.publish_embedding(
                fixture,
                artifact_id=f"p4-01c-{base + 1 + offset}",
                asset_id=base + 1 + offset,
                vector=vectors[offset],
                now=benchmark.now_ms(),
            )
        assert benchmark.outbox_counts(fixture) == {"PENDING": added}

        batches: list[int] = []
        original_batch = LanceVectorIndex.upsert_batch

        def counted(self: Any, *, generation: Any, records: Any) -> Any:
            batches.append(len(records))
            return original_batch(self, generation=generation, records=records)

        coordinator = IndexCoordinator(fixture.catalog)
        with pytest.MonkeyPatch.context() as patched:
            patched.setattr(LanceVectorIndex, "upsert_batch", counted)
            assert coordinator.process_all(now_ms=benchmark.now_ms()) == added
        # ONE engine write covered every event: no per-row write happened at all.
        assert batches == [added]
        assert benchmark.outbox_counts(fixture) == {"CONFIRMED": added}
        assert coordinator.scalar_index_report
        for offset in range(added):
            assert coordinator.semantic_ready(
                space_id=benchmark.SPACE_ID,
                asset_id=base + 1 + offset,
                generation_id=benchmark.GENERATION_ID,
            )
        rows_before = coordinator.index.rows(
            generation_id=benchmark.GENERATION_ID, dimension=benchmark.DIM
        )
        assert len(rows_before) == base + added

        # The documented crash window: the engine write happened, the confirmation did not.
        benchmark.bump_filter_revision(fixture, cause="CONTENT_CHANGED")
        extra = benchmark.synthetic_vectors(5, seed=17, distribution="clustered")
        for offset in range(5):
            benchmark.publish_embedding(
                fixture,
                artifact_id=f"p4-01c-crash-{offset}",
                asset_id=base + 1 + offset,
                vector=extra[offset],
                now=benchmark.now_ms(),
                input_revision=hashlib.sha256(f"crash-{offset}".encode()).hexdigest(),
            )
        assert benchmark.outbox_counts(fixture) == {"CONFIRMED": added, "PENDING": 5}
        restarted = IndexCoordinator(fixture.catalog)
        with pytest.raises(RuntimeError, match="after_lance"):
            restarted.process_batch(
                now_ms=benchmark.now_ms(), max_events=5, failpoint="after_lance"
            )
        assert benchmark.outbox_counts(fixture) == {"CONFIRMED": added, "IN_FLIGHT": 5}
        for offset in range(5):
            assert not restarted.semantic_ready(
                space_id=benchmark.SPACE_ID,
                asset_id=base + 1 + offset,
                generation_id=benchmark.GENERATION_ID,
            )

        # Replaying the batch confirms it and never duplicates a logical row.
        assert restarted.process_all(now_ms=benchmark.now_ms()) == 5
        assert benchmark.outbox_counts(fixture) == {"CONFIRMED": added + 5}
        rows_after = restarted.index.rows(
            generation_id=benchmark.GENERATION_ID, dimension=benchmark.DIM
        )
        assert len(rows_after) == base + added
        assert len({int(row["asset_id"]) for row in rows_after}) == base + added
        for offset in range(5):
            assert restarted.semantic_ready(
                space_id=benchmark.SPACE_ID,
                asset_id=base + 1 + offset,
                generation_id=benchmark.GENERATION_ID,
            )
    finally:
        fixture.catalog.close()


def test_a_concurrent_reclaim_cannot_turn_a_batch_into_a_false_failure(
    tmp_path: Path,
) -> None:
    """P4-01C focused review A1: a second coordinator re-claiming one event is not a failure.

    ``process_next`` may re-claim an ``IN_FLIGHT`` event (that is the documented crash-recovery
    path, there is no claim token). The interleaving point used here is the only legal one
    left after the review: the batch's claim transaction has committed and its engine-write
    transaction has not started yet, so a second coordinator really can step in and confirm
    one of the claimed events. The batch must then leave that event alone, still finish its
    own event exactly once, and never report a false ``INDEX_SUBMISSION_FAILED``.
    """

    fixture = build(tmp_path, 20, name="scale-20-reclaim", seed=43)
    try:
        base = fixture.scale
        added = 2
        vectors = benchmark.synthetic_vectors(added, seed=47, distribution="clustered")
        with fixture.catalog.write() as connection:
            for offset in range(added):
                asset_id = base + 1 + offset
                connection.execute(
                    "INSERT INTO assets(sha256, byte_size, width, height, format, "
                    "created_at, state) VALUES (?, 2000000, 4032, 3024, 'JPEG', ?, 'ACTIVE')",
                    (f"{asset_id:064x}", benchmark.now_ms()),
                )
                connection.execute(
                    "INSERT INTO file_locations(source_id, relative_path, path_key, "
                    "asset_id, size, mtime_ns, last_seen_scan_id, availability) "
                    "VALUES (?, ?, ?, ?, 2000000, 1700000000000000000, NULL, 'PRESENT')",
                    (
                        fixture.source_ids[0],
                        f"reclaim/photo-{asset_id:08d}.jpg",
                        f"reclaim/photo-{asset_id:08d}.jpg",
                        asset_id,
                    ),
                )
        for offset in range(added):
            benchmark.publish_embedding(
                fixture,
                artifact_id=f"p4-01c-reclaim-{base + 1 + offset}",
                asset_id=base + 1 + offset,
                vector=vectors[offset],
                now=benchmark.now_ms(),
                input_revision=hashlib.sha256(f"reclaim-{offset}".encode()).hexdigest(),
            )
        assert benchmark.outbox_counts(fixture) == {"PENDING": added}

        coordinator = IndexCoordinator(fixture.catalog)
        original_write = Catalog.write
        steal: dict[str, Any] = {"writes": 0, "claimed": None}

        @contextmanager
        def stealing_write(self: Any) -> Any:
            steal["writes"] += 1
            if steal["writes"] == 2 and steal["claimed"] is None:
                # Write #1 was the batch's claim (already committed); write #2 is its
                # engine-write/confirmation transaction, which has not begun yet.
                steal["claimed"] = IndexCoordinator(fixture.catalog).process_next(
                    now_ms=benchmark.now_ms()
                )
            with original_write(self) as connection:
                yield connection

        with pytest.MonkeyPatch.context() as patched:
            patched.setattr(type(fixture.catalog), "write", stealing_write)
            applied = coordinator.process_batch(now_ms=benchmark.now_ms(), max_events=added)

        assert steal["claimed"] is True
        # The batch applied the one event that was still its own; the second coordinator
        # applied the other, so the event is never written or confirmed twice.
        assert applied == added - 1
        assert benchmark.outbox_counts(fixture) == {"CONFIRMED": added}
        for offset in range(added):
            assert coordinator.semantic_ready(
                space_id=benchmark.SPACE_ID,
                asset_id=base + 1 + offset,
                generation_id=benchmark.GENERATION_ID,
            )
        rows = coordinator.index.rows(
            generation_id=benchmark.GENERATION_ID, dimension=benchmark.DIM
        )
        assert len({int(row["asset_id"]) for row in rows}) == len(rows)
    finally:
        fixture.catalog.close()


def test_a_stale_projection_revision_can_never_land_after_a_newer_one(
    tmp_path: Path,
) -> None:
    """P4-01C focused review A1 (second round): the old writer must not land last.

    The review showed that when a newer projection revision appears between a writer's
    supersede check and its engine write, the *older* revision can be written last: SQLite
    reports revision 2 as confirmed and ``semantic_ready`` while the derived row still holds
    revision 1 for that asset. The supersede check, the engine write and the confirmation now
    share one ``BEGIN IMMEDIATE`` transaction, so that window does not exist. This test tries
    to slip a newer revision in at exactly that point, requires the attempt to be refused, and
    then proves the newer revision still ends up in the derived row.
    """

    fixture = build(tmp_path, 20, name="scale-20-stale", seed=53)
    try:
        older = benchmark.synthetic_vectors(1, seed=59, distribution="clustered")[0]
        newer = benchmark.synthetic_vectors(1, seed=61, distribution="clustered")[0]
        benchmark.bump_filter_revision(fixture, cause="CONTENT_CHANGED")
        benchmark.publish_embedding(
            fixture,
            artifact_id="p4-01c-stale-old",
            asset_id=1,
            vector=older,
            now=benchmark.now_ms(),
            input_revision=hashlib.sha256(b"stale-old").hexdigest(),
        )
        assert benchmark.outbox_counts(fixture) == {"PENDING": 1}

        coordinator = IndexCoordinator(fixture.catalog)
        original_batch = LanceVectorIndex.upsert_batch
        state: dict[str, Any] = {
            "interleaving": False,
            "slipped_in": None,
            "second_applied": None,
            "blocked": None,
        }

        def slip_in_newer_revision() -> bool:
            """Publish revision 2 from *inside* the writer's check→write window."""
            probe = sqlite3.connect(
                str(fixture.catalog.db_path), isolation_level=None, timeout=0.2
            )
            try:
                try:
                    probe.execute("BEGIN IMMEDIATE")
                except sqlite3.OperationalError:
                    # The cross-process model: the writer's transaction locks us out.
                    state["blocked"] = True
                else:
                    state["blocked"] = False
                    probe.execute("ROLLBACK")
            finally:
                probe.close()
            try:
                benchmark.bump_filter_revision(fixture, cause="CONTENT_CHANGED")
            except RuntimeError:
                # In-process: a nested write is refused because the write transaction that
                # spans the supersede check is already open.
                return False
            benchmark.publish_embedding(
                fixture,
                artifact_id="p4-01c-stale-new",
                asset_id=1,
                vector=newer,
                now=benchmark.now_ms(),
                input_revision=hashlib.sha256(b"stale-new").hexdigest(),
            )
            state["second_applied"] = IndexCoordinator(fixture.catalog).process_batch(
                now_ms=benchmark.now_ms(), max_events=2
            )
            return True

        def hooked(self: Any, *, generation: Any, records: Any) -> Any:
            # The engine write is the last step before the confirmation: this is the exact
            # point the review used to let the older revision land last.
            if not state["interleaving"]:
                # Set before the attempt: the second coordinator's own engine write re-enters
                # this hook, and it must not try to interleave again.
                state["interleaving"] = True
                state["slipped_in"] = slip_in_newer_revision()
            return original_batch(self, generation=generation, records=records)

        with pytest.MonkeyPatch.context() as patched:
            patched.setattr(LanceVectorIndex, "upsert_batch", hooked)
            applied = coordinator.process_batch(now_ms=benchmark.now_ms(), max_events=1)

        assert state["blocked"] is True
        assert state["slipped_in"] is False
        assert state["second_applied"] is None
        assert applied == 1

        # The newer revision, published afterwards as it would be in production, always wins.
        benchmark.bump_filter_revision(fixture, cause="CONTENT_CHANGED")
        benchmark.publish_embedding(
            fixture,
            artifact_id="p4-01c-stale-new",
            asset_id=1,
            vector=newer,
            now=benchmark.now_ms(),
            input_revision=hashlib.sha256(b"stale-new").hexdigest(),
        )
        assert (
            IndexCoordinator(fixture.catalog).process_batch(
                now_ms=benchmark.now_ms(), max_events=2
            )
            == 1
        )
        assert benchmark.outbox_counts(fixture) == {"CONFIRMED": 2}
        entry = fixture.catalog.read_one(
            "SELECT state, confirmation_revision FROM index_entries "
            "WHERE space_id = ? AND asset_id = 1 AND generation_id = ?",
            (benchmark.SPACE_ID, benchmark.GENERATION_ID),
        )
        assert str(entry["state"]) == "CONFIRMED"
        assert int(entry["confirmation_revision"]) == 2
        row = next(
            item
            for item in coordinator.index.rows(
                generation_id=benchmark.GENERATION_ID, dimension=benchmark.DIM
            )
            if int(item["asset_id"]) == 1
        )
        assert int(row["projection_revision"]) == int(entry["confirmation_revision"]) == 2
        stored = [float(value) for value in row["vector"]]
        assert sum(a * b for a, b in zip(stored, newer, strict=True)) > sum(
            a * b for a, b in zip(stored, older, strict=True)
        )
        assert coordinator.semantic_ready(
            space_id=benchmark.SPACE_ID, asset_id=1, generation_id=benchmark.GENERATION_ID
        )
    finally:
        fixture.catalog.close()


# ----------------------------------------------------------- rewritten predicates (D2)
def _predicate_catalog() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        "CREATE TABLE assets (asset_id INTEGER PRIMARY KEY);"
        "CREATE TABLE file_locations (location_id INTEGER PRIMARY KEY, source_id INTEGER, "
        "asset_id INTEGER, availability TEXT, relative_path TEXT NOT NULL DEFAULT 'photo.jpg');"
        "CREATE INDEX idx_file_locations_asset ON file_locations(asset_id);"
        "CREATE TABLE faces (face_id TEXT PRIMARY KEY, asset_id INTEGER NOT NULL, "
        "spec_revision TEXT NOT NULL DEFAULT 'original', input_revision TEXT NOT NULL DEFAULT 'r1');"
        "CREATE TABLE face_detail_auto_assets(asset_id INTEGER, input_revision TEXT, "
        "ready INTEGER, policy_revision TEXT);"
        "CREATE TABLE face_redetection_groups(old_face_id TEXT, state TEXT);"
        "CREATE TABLE face_person_links (face_id TEXT, person_id INTEGER, decision TEXT, "
        "assignment_kind TEXT, revision INTEGER);"
        "CREATE TABLE face_assignment_controls (face_id TEXT PRIMARY KEY, mode TEXT);"
        "CREATE TABLE face_exclusions (face_id TEXT, person_id INTEGER, active INTEGER);"
    )
    for asset_id in range(1, 13):
        connection.execute("INSERT INTO assets(asset_id) VALUES (?)", (asset_id,))
    connection.executemany(
        "INSERT INTO file_locations(location_id, source_id, asset_id, availability) "
        "VALUES (?, ?, ?, ?)",
        [
            (1, 1, 1, "PRESENT"),
            (2, 1, 2, "PRESENT"),
            (3, 2, 3, "PRESENT"),
            (4, 1, 5, "MISSING"),
            (5, 1, None, "PRESENT"),  # a location row that belongs to no asset
            (6, 1, 6, "PRESENT"),
            (7, 2, 7, "PRESENT"),
            (8, 1, 9, "PRESENT"),
            (9, 1, 9, "PRESENT"),  # duplicate location of the same asset
            (10, 2, 10, "MISSING"),
        ],
    )
    connection.executemany(
        "INSERT INTO faces(face_id, asset_id) VALUES (?, ?)",
        [
            ("f1", 1),
            ("f2", 2),
            ("f3", 3),
            ("f4", 4),
            ("f5", 4),
            ("f6", 5),
            ("f7", 7),
            ("f9", 9),
            ("f10", 10),
        ],
    )
    connection.executemany(
        "INSERT INTO face_person_links(face_id, person_id, decision, assignment_kind, "
        "revision) VALUES (?, ?, ?, ?, ?)",
        [
            ("f1", 3, "CONFIRMED", "AUTO", 1),
            ("f2", 3, "CONFIRMED", "AUTO", 1),  # excluded by a MANUAL_UNASSIGNED control
            ("f3", 4, "CONFIRMED", "AUTO", 1),
            ("f4", 3, "CONFIRMED", "AUTO", 1),
            ("f5", 4, "CONFIRMED", "AUTO", 1),
            ("f6", 4, "CONFIRMED", "AUTO", 1),  # excluded for person 4
            ("f7", 3, "SUGGESTED", "AUTO", 1),  # never a member
            ("f9", 3, "CONFIRMED", "MANUAL", 2),
            ("f10", 3, "CONFIRMED", "AUTO", 1),
        ],
    )
    connection.execute(
        "INSERT INTO face_assignment_controls(face_id, mode) VALUES ('f2', "
        "'MANUAL_UNASSIGNED')"
    )
    connection.execute(
        "INSERT INTO face_exclusions(face_id, person_id, active) VALUES ('f6', 4, 1)"
    )
    return connection


def test_the_rewritten_predicates_select_the_same_photos() -> None:
    connection = _predicate_catalog()
    try:
        reference_source = (
            "EXISTS (SELECT 1 FROM file_locations AS l WHERE l.asset_id = a.asset_id "
            "AND l.source_id = ?)"
        )
        reference_person = (
            "EXISTS (SELECT 1 FROM face_person_links AS l JOIN faces AS f "
            "ON f.face_id = l.face_id WHERE f.asset_id = a.asset_id "
            f"AND l.person_id {{person}} AND {MEMBER_PREDICATE})"
        )

        def selected(sql: str, parameters: list[Any]) -> list[int]:
            rows = connection.execute(
                f"SELECT a.asset_id FROM assets AS a WHERE {sql} ORDER BY a.asset_id",
                tuple(parameters),
            ).fetchall()
            return [int(row["asset_id"]) for row in rows]

        for source_id in (1, 2):
            where, parameters = retrieval.scope_condition(
                connection, retrieval.SearchScope.build(source_id=source_id)
            )
            assert selected(where, parameters) == selected(reference_source, [source_id])
        assert selected(
            *retrieval.scope_condition(connection, retrieval.SearchScope.build(source_id=1))
        ) == [1, 2, 5, 6, 9]

        for availability, expected in (
            ("present", [1, 2, 3, 6, 7, 9]),
            ("missing", [4, 5, 8, 10, 11, 12]),
        ):
            where, parameters = retrieval.scope_condition(
                connection, retrieval.SearchScope.build(availability=availability)
            )
            assert selected(where, parameters) == expected

        for person_ids, match in (
            ([3], "any"),
            ([4], "any"),
            ([3, 4], "any"),
            ([3, 4], "all"),
        ):
            where, parameters = retrieval.scope_condition(
                connection,
                retrieval.SearchScope.build(person_ids=person_ids, person_match=match),
            )
            if match == "all":
                reference = " AND ".join(
                    reference_person.format(person="= ?") for _ in person_ids
                )
            else:
                reference = reference_person.format(
                    person="IN (" + ", ".join("?" for _ in person_ids) + ")"
                )
            assert selected(where, parameters) == selected(reference, list(person_ids))

        # The member predicate still governs: MANUAL_UNASSIGNED, exclusions and SUGGESTED.
        assert selected(
            *retrieval.scope_condition(
                connection, retrieval.SearchScope.build(person_ids=[3])
            )
        ) == [1, 4, 9, 10]
        assert selected(
            *retrieval.scope_condition(
                connection, retrieval.SearchScope.build(person_ids=[4])
            )
        ) == [3, 4]

        # Everything ANDs: source + availability + person together.
        where, parameters = retrieval.scope_condition(
            connection,
            retrieval.SearchScope.build(
                source_id=1, availability="present", person_ids=[3]
            ),
        )
        assert selected(where, parameters) == [1, 9]
    finally:
        connection.close()
