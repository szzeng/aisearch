"""Idempotent P2-03 outbox consumer and index repair coordinator.

P4-02C adds exactly two rules to that consumer, both documented on
:data:`CLAIMABLE_EVENT_PREDICATE` and :meth:`IndexCoordinator._schedule_existing`: an event
whose artifact is waiting for re-execution is not claimable, and the repair audit reuses the
pending event that already represents its own work instead of allocating a new revision.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from photo_search.artifacts.store import ArtifactStore
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import (
    ARTIFACT_MISSING,
    FILTER_UNSUPPORTED,
    INDEX_REPAIR_REQUIRED,
    INDEX_SUBMISSION_FAILED,
    RUNTIME_RESOLUTION_MISMATCH,
    SPACE_MISMATCH,
    CatalogError,
)
from photo_search.config import resolve_data_root, vectors_dir

#: The claim predicate shared by both replay paths (:meth:`IndexCoordinator.process_next` and
#: :meth:`IndexCoordinator.process_batch`).
#:
#: An event whose artifact is ``RECLAIMED`` is *waiting for re-execution*. P2-03 writes that
#: lifecycle state only from the repair audit (or an explicit ``reclaim``) after deciding that
#: the published bytes cannot be reused, and ``ArtifactStore.republish_reclaimed`` is the only
#: way back. Such an event can never be applied, so it must not be claimed either: the claim is
#: oldest-first and the batch is all-or-nothing, which means one reclaimed artifact would
#: otherwise pin the head of the outbox and stop the whole drain forever (P4-02A engineering
#: review A2). The row itself is untouched - it stays unconfirmed with its ``last_error_code``,
#: so nothing is dropped, silently confirmed or hidden; the maintenance entry reports it as
#: ``awaiting_reexecution`` and the same event confirms normally once the artifact is
#: republished.
#:
#: This is deliberately *not* a "skip the bad file" rule. An artifact that is still
#: ``PUBLISHED`` while its bytes no longer verify is claimed exactly as before and fails closed
#: with a structured ``ARTIFACT_MISSING``; that refusal is what tells the operator to run the
#: repair audit, and the isolation only starts once the audit has recorded ``RECLAIMED``.
CLAIMABLE_EVENT_PREDICATE = (
    "state IN ('PENDING', 'IN_FLIGHT') AND NOT EXISTS ("
    "SELECT 1 FROM artifacts AS a WHERE a.artifact_id = index_outbox.artifact_id "
    "AND a.lifecycle_state = 'RECLAIMED')"
)


def _table_name(generation_id: str) -> str:
    import hashlib

    return f"vectors_{hashlib.sha256(generation_id.encode()).hexdigest()[:24]}"


#: The one scalar index the candidate-ID block query benefits from. P4-01 measured a
#: 300 000-row generation at 6 618 ms -> 3 602 ms per search once this BTREE index existed
#: (and 92 195 ms -> 19 757 ms from the block-size change alone, P4-01C). It is a
#: *maintenance* artifact only: a missing or refused index merely means the engine scans the
#: generation, which stays correct, so no read path depends on it.
ASSET_SCALAR_INDEX = "asset_id_idx"
ASSET_SCALAR_COLUMN = "asset_id"

#: The columns one candidate-block query reads. The vector column is deliberately excluded:
#: a block may return up to one row per authoritative candidate id (see
#: :meth:`LanceVectorIndex.search_candidates`), and carrying 512 floats per row for rows the
#: caller only ranks by their distance is pure transfer cost. ``_distance`` must be listed -
#: the engine warns (and will stop auto-projecting it) when a projection omits it.
CANDIDATE_ROW_COLUMNS = (
    "record_id",
    "asset_id",
    "generation_id",
    "space_id",
    "projection_revision",
    "visible",
    "_distance",
)


def _candidate_id_condition(ids: Sequence[int]) -> str:
    """Encode sorted, unique authoritative ids without widening their membership.

    Large consecutive candidate blocks otherwise produce thousands of IN literals
    that the engine must plan for every block. A small union of exact consecutive
    runs is equivalent, including every hole. Keep short/sparse sets on IN, and cap
    the disjunction so scattered filters cannot create a deep expression tree.
    """
    if not ids:
        return "false"
    original = "asset_id IN (" + ", ".join(str(value) for value in ids) + ")"
    if len(ids) < 64:
        return original
    terms: list[str] = []
    start = end = ids[0]

    def term(low: int, high: int) -> str:
        if low == high:
            return f"asset_id = {low}"
        return f"(asset_id BETWEEN {low} AND {high})"

    for value in ids[1:]:
        if value == end + 1:
            end = value
            continue
        terms.append(term(start, end))
        if len(terms) >= 64:
            return original
        start = end = value
    terms.append(term(start, end))
    compact = "(" + " OR ".join(terms) + ")"
    return compact if len(compact) < len(original) else original


def _lance_literal(value: str) -> str:
    """One quoted SQL literal for a Lance filter, or a structured refusal.

    The engine filter is SQL text, so an identifier that could change the meaning of the
    expression is refused instead of escaped: this application only ever filters by
    values it generated itself (``space_id``/``generation_id``/``asset_id``).
    """
    text = str(value)
    if not text or "'" in text or "\x00" in text:
        raise CatalogError(
            SPACE_MISMATCH,
            "a derived-store filter value is not usable in the engine filter",
        )
    return f"'{text}'"


class LanceVectorIndex:
    """One fixed-dimension Lance table per generation; no ANN is created here."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @staticmethod
    def _dependencies() -> tuple[Any, Any]:
        # Keep heavy/native imports behind the adapter boundary (ADR-0003 §7).
        import lancedb
        import pyarrow as pa

        return lancedb, pa

    @staticmethod
    def _schema(pa: Any, dimension: int) -> Any:
        return pa.schema(
            [
                pa.field("record_id", pa.string(), nullable=False),
                pa.field("asset_id", pa.int64(), nullable=False),
                pa.field("generation_id", pa.string(), nullable=False),
                pa.field("space_id", pa.string(), nullable=False),
                pa.field("artifact_revision", pa.string(), nullable=False),
                pa.field("projection_revision", pa.int64(), nullable=False),
                pa.field("vector", pa.list_(pa.float32(), dimension), nullable=False),
                pa.field("source_ids", pa.list_(pa.int64()), nullable=False),
                pa.field("visible", pa.bool_(), nullable=False),
            ]
        )

    def _table(self, *, generation_id: str, dimension: int, create: bool) -> Any | None:
        lancedb, pa = self._dependencies()
        self.root.mkdir(parents=True, exist_ok=True)
        database = lancedb.connect(str(self.root))
        name = _table_name(generation_id)
        names = set(database.list_tables().tables)
        if name in names:
            table = database.open_table(name)
            vector_type = table.schema.field("vector").type
            if int(vector_type.list_size) != int(dimension):
                raise CatalogError(SPACE_MISMATCH, "generation dimension differs from its Lance table")
            return table
        if not create:
            return None
        return database.create_table(name, schema=self._schema(pa, int(dimension)))

    def upsert(self, *, generation: Any, record: dict[str, Any]) -> None:
        self.upsert_batch(generation=generation, records=[record])

    def upsert_batch(self, *, generation: Any, records: Sequence[dict[str, Any]]) -> int:
        """Idempotently merge many records for one generation in a single engine write.

        Per-record semantics are exactly :meth:`upsert`'s - the same space/dimension checks,
        the same ``record_id`` merge key, matched rows updated and unmatched rows inserted -
        so a replayed batch is a no-op on already-written rows. P4-01 measured one
        ``merge_insert`` per row at 48.87 ms/row on a 300 000-row generation against
        0.115 ms/row for a single 500-row write (424x), which is why the coordinator drains
        the outbox in batches.
        """
        if not records:
            return 0
        for record in records:
            if str(record["space_id"]) != str(generation["space_id"]):
                raise CatalogError(SPACE_MISMATCH, "record and generation spaces differ")
            if len(record["vector"]) != int(generation["dimension"]):
                raise CatalogError(
                    SPACE_MISMATCH, "record and generation dimensions differ"
                )
        _, pa = self._dependencies()
        table = self._table(
            generation_id=str(generation["generation_id"]),
            dimension=int(generation["dimension"]),
            create=True,
        )
        table.merge_insert("record_id").when_matched_update_all().when_not_matched_insert_all().execute(
            pa.Table.from_pylist(
                [dict(record) for record in records],
                schema=self._schema(pa, int(generation["dimension"])),
            )
        )
        return len(records)

    def rows(self, *, generation_id: str, dimension: int) -> list[dict[str, Any]]:
        table = self._table(generation_id=generation_id, dimension=dimension, create=False)
        if table is None:
            return []
        return list(table.to_arrow().to_pylist())

    def has(self, *, generation_id: str, dimension: int, asset_id: int) -> bool:
        """Whether this generation holds a row for one asset, without materialising it.

        ``rows()`` builds a Python row (vector list included) for *every* row of the
        generation, which made ``audit_and_schedule_repairs`` ``O(artifacts x rows)``: P4-01
        measured 13.6 ms per call at 200 rows. The filtered count is a point query - served
        by the ``asset_id`` scalar index when it exists, a plain engine scan when it does
        not - and the materialising path stays only as the fallback for an engine that
        refuses the filter.
        """
        table = self._table(
            generation_id=str(generation_id), dimension=int(dimension), create=False
        )
        if table is None:
            return False
        try:
            return int(table.count_rows(filter=f"{ASSET_SCALAR_COLUMN} = {int(asset_id)}")) > 0
        except Exception:  # noqa: BLE001 - a refused filter falls back to the exact scan
            return any(
                int(row["asset_id"]) == int(asset_id)
                for row in self.rows(generation_id=generation_id, dimension=dimension)
            )

    def ensure_scalar_index(self, *, generation_id: str, dimension: int) -> dict[str, Any]:
        """Idempotently create this generation's ``asset_id`` scalar index (maintenance).

        Every state below is a *reported* outcome and no caller may turn it into a failure:
        a generation without the index still answers correctly, just slower.
        ``present``/``created``/``refreshed`` all mean the index is usable; ``unavailable`` means the
        engine refused it - or refused to even list its index catalogue - and the
        candidate-ID path stays in its scan mode. The catalogue read is inside the same
        guard on purpose: a maintenance step may never abort the outbox drain because index
        metadata is unreadable (P4-01C review B2). ``_table`` stays outside, so a missing or
        dimension-mismatched table is still a structured catalog failure.

        P4-02D finding F4 / P4-02J: ``create_index`` only covers the rows that exist at that
        moment, and this generation is written in many small batches, so an index built on the
        first batch leaves every later row unindexed and the candidate-block query degrades
        into a whole-generation scan (10x-24x slower on the measured roots). An index that the
        engine reports as incomplete (``num_unindexed_rows > 0``) is therefore rebuilt in
        place; the outcome is reported as ``refreshed`` with the row count it had to cover.
        Rows are never dropped and the answer is unchanged - only the access path changes.
        """
        table = self._table(
            generation_id=str(generation_id), dimension=int(dimension), create=False
        )
        if table is None:
            return {"state": "no_table", "index": ASSET_SCALAR_INDEX}
        try:
            import lancedb.index as lance_index

            catalogue = list(table.list_indices())
            names = {str(getattr(item, "name", item)) for item in catalogue}
            if ASSET_SCALAR_INDEX in names:
                unindexed = 0
                for item in catalogue:
                    if str(getattr(item, "name", item)) == ASSET_SCALAR_INDEX:
                        unindexed = int(getattr(item, "num_unindexed_rows", 0) or 0)
                if unindexed == 0:
                    return {"state": "present", "index": ASSET_SCALAR_INDEX}
                table.create_index(
                    ASSET_SCALAR_COLUMN, config=lance_index.BTree(), replace=True
                )
                return {
                    "state": "refreshed",
                    "index": ASSET_SCALAR_INDEX,
                    "unindexed_rows_before": unindexed,
                }
            table.create_index(ASSET_SCALAR_COLUMN, config=lance_index.BTree())
        except Exception as error:  # noqa: BLE001 - a refused index must not fail a write
            return {
                "state": "unavailable",
                "index": ASSET_SCALAR_INDEX,
                "reason": type(error).__name__,
            }
        return {"state": "created", "index": ASSET_SCALAR_INDEX}

    def count(self, *, generation_id: str, dimension: int, space_id: str) -> int:
        """Count a fully checked generation without loading its vectors into Python."""
        table = self._table(generation_id=generation_id, dimension=dimension, create=False)
        if table is None:
            return 0
        import pyarrow.compute as pc

        # A metadata-only total could hide an unreadable vector payload. Read every
        # column in bounded Arrow batches, preserving the former full-read check
        # without creating a Python list/float object for every vector component.
        expected = int(table.count_rows())
        scanned = 0
        with table.search().to_batches(batch_size=4096) as batches:
            for batch in batches:
                batch.validate(full=True)
                scanned += batch.num_rows
                for column, value in (("space_id", space_id), ("generation_id", generation_id)):
                    matches = pc.fill_null(pc.equal(batch[column], str(value)), False)
                    if batch.num_rows and not pc.all(matches).as_py():
                        raise CatalogError(
                            SPACE_MISMATCH, "a Lance generation contains a foreign identity"
                        )
        if scanned != expected:
            raise CatalogError(INDEX_REPAIR_REQUIRED, "Lance scan and metadata row counts differ")
        return scanned

    def search(
        self,
        *,
        generation_id: str,
        dimension: int,
        query_vector: Sequence[float],
        limit: int,
        where: str | None = None,
        space_id: str | None = None,
        columns: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Nearest rows of one generation, ranked deterministic and space-checked.

        ``where`` is handed to the engine as the documented pre-filter: P0-02C verified
        for this locked version that a filtered search ranks *inside* the filter (the
        global nearest neighbour cannot be returned when the filter excludes it) and that
        an unusable filter column raises instead of degrading into an unfiltered search.

        Nothing here decides a filter: the caller passes the authoritative candidate ids
        of the bound space. Every returned row is checked against the generation and the
        expected space, so two equal-dimension spaces can never be mixed by accident.

        ``columns`` projects the answer down to the fields the caller reads. P4-01C's review
        added it for the candidate-ID path, where one block may now return up to
        ``SCOPE_BLOCK_SIZE`` rows: without the projection each of those rows would carry its
        full vector, which is exactly the transfer cost the block walk must not pay. The
        engine always keeps ``_distance`` in the projection.
        """
        vector = self._query_vector(dimension=dimension, query_vector=query_vector)
        table = self._table(
            generation_id=str(generation_id), dimension=int(dimension), create=False
        )
        if table is None:
            return []
        builder = table.search(vector).limit(int(limit))
        if columns:
            builder = builder.select(list(columns))
        if where:
            builder = builder.where(str(where))
        try:
            raw_rows = builder.to_arrow().to_pylist()
        except CatalogError:
            raise
        except Exception as error:
            # P0-02C: an impossible filter column surfaces as a ValueError from the engine.
            # A refused filter is a structured failure with zero rows - never an unfiltered
            # search - while any other engine failure is an index problem of this build.
            refused_filter = isinstance(error, ValueError) and bool(where)
            raise CatalogError(
                FILTER_UNSUPPORTED if refused_filter else INDEX_REPAIR_REQUIRED,
                (
                    "the vector engine refused the requested pre-filter"
                    if refused_filter
                    else "the vector engine could not read this generation"
                ),
                details={"reason": type(error).__name__},
            ) from error

        rows: list[dict[str, Any]] = []
        spaces: set[str] = set()
        for raw in raw_rows:
            row_space = str(raw["space_id"])
            spaces.add(row_space)
            if str(raw["generation_id"]) != str(generation_id):
                raise CatalogError(
                    SPACE_MISMATCH, "a Lance generation returned a foreign generation row"
                )
            rows.append(
                {
                    "record_id": str(raw["record_id"]),
                    "asset_id": int(raw["asset_id"]),
                    "space_id": row_space,
                    "generation_id": str(raw["generation_id"]),
                    "projection_revision": int(raw["projection_revision"]),
                    "distance": float(raw["_distance"]),
                    "visible": bool(raw["visible"]),
                }
            )
        if space_id is not None:
            if spaces - {str(space_id)}:
                raise CatalogError(SPACE_MISMATCH, "a Lance generation returned a foreign space")
        elif len(spaces) > 1:
            raise CatalogError(
                SPACE_MISMATCH, "one Lance generation holds more than one embedding space"
            )
        rows.sort(key=lambda item: (item["distance"], item["asset_id"]))
        return rows

    def search_candidates(
        self,
        *,
        generation_id: str,
        dimension: int,
        query_vector: Sequence[float],
        asset_ids: Sequence[int],
        limit: int,
        space_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """The architecture §7.4 candidate-ID path: rank only inside a bounded id set.

        An empty candidate set returns nothing without touching the engine: a query with
        no authoritative candidate cannot fall back to "everything".

        The engine is asked for **one row per candidate id**, never for ``limit`` rows: a
        block whose rows all tie on distance would otherwise be truncated by the engine
        *before* the caller's ``(distance, asset_id)`` total order is applied, and the merged
        global top-k would then change with the block size (P4-01C review B1). The block bound
        is therefore applied here, after the deterministic order.
        """
        ids = sorted({int(value) for value in asset_ids})
        if not ids:
            return []
        conditions = [_candidate_id_condition(ids)]
        if space_id is not None:
            conditions.append(f"space_id = {_lance_literal(str(space_id))}")
        rows = self.search(
            generation_id=str(generation_id),
            dimension=int(dimension),
            query_vector=query_vector,
            limit=len(ids),
            where=" AND ".join(conditions),
            space_id=space_id,
            columns=CANDIDATE_ROW_COLUMNS,
        )
        return rows[: int(limit)]

    def search_candidates_bounded(
        self,
        *,
        generation_id: str,
        dimension: int,
        query_vector: Sequence[float],
        asset_ids: Sequence[int],
        top_k: int,
        space_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Rank inside the candidate ids asking the engine for ``top_k + 1`` rows only.

        Returns ``(rows, resolved)``. ``resolved`` is True exactly when ``rows`` already hold
        every row of this candidate set that the caller's ``(distance, asset_id)`` Top-K
        decision can need: the set holds at most ``top_k`` rows at or below its ``top_k``-th
        distance. The engine returns its ``top_k + 1`` *nearest* rows, so a strictly larger
        ``(top_k+1)``-th distance proves that no tie group was cut at the boundary - and with
        a cut tie group the engine's arbitrary intra-tie order cannot rebuild the product's
        ``(distance, asset_id)`` order, which is why the caller then falls back to the
        exhaustive :meth:`search_candidates` for that block (P4-01C review B1).

        The engine is still only asked to rank *inside* the authoritative candidate ids, and
        the fallback is the previous access path unchanged, so a block that needs it produces
        exactly the rows the exhaustive call produces. What changes is the volume: a resolved
        block transfers ``top_k + 1`` rows instead of one row per candidate id.
        """
        ids = sorted({int(value) for value in asset_ids})
        if not ids:
            return [], True
        conditions = [_candidate_id_condition(ids)]
        if space_id is not None:
            conditions.append(f"space_id = {_lance_literal(str(space_id))}")
        wanted = int(top_k)
        rows = self.search(
            generation_id=str(generation_id),
            dimension=int(dimension),
            query_vector=query_vector,
            limit=wanted + 1,
            where=" AND ".join(conditions),
            space_id=space_id,
            columns=CANDIDATE_ROW_COLUMNS,
        )
        if len(rows) <= wanted:
            return rows, True
        return rows, bool(float(rows[wanted]["distance"]) > float(rows[wanted - 1]["distance"]))
    @staticmethod
    def _query_vector(*, dimension: int, query_vector: Sequence[float]) -> list[float]:
        values: list[float] = []
        for value in query_vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CatalogError(
                    RUNTIME_RESOLUTION_MISMATCH,
                    "the resolved query embedding contains a value that is not a number",
                )
            number = float(value)
            if not math.isfinite(number):
                raise CatalogError(
                    RUNTIME_RESOLUTION_MISMATCH,
                    "the resolved query embedding is not finite",
                )
            values.append(number)
        if len(values) != int(dimension):
            raise CatalogError(
                SPACE_MISMATCH,
                "the resolved query embedding does not match the bound dimension",
                details={"expected_dimension": int(dimension), "actual_dimension": len(values)},
            )
        return values


def _mark_entry_pending_repair(
    connection: sqlite3.Connection,
    *,
    artifact: Any,
    generation: Any,
    asset_id: int,
    revision: int,
) -> None:
    """Record that one (space, asset, generation) is waiting for its projection again.

    Shared by both scheduling paths of :meth:`IndexCoordinator._schedule_existing` so that a
    reused event and a freshly allocated one leave the same honest entry state
    (``PENDING_REPAIR``, no ``confirmed_at``, the revision the event carries).
    """
    connection.execute(
        "INSERT INTO index_entries(space_id, asset_id, generation_id, artifact_id, "
        "confirmation_revision, confirmed_at, state) VALUES (?, ?, ?, ?, ?, NULL, 'PENDING_REPAIR') "
        "ON CONFLICT(space_id, asset_id, generation_id) DO UPDATE SET artifact_id = excluded.artifact_id, "
        "confirmation_revision = excluded.confirmation_revision, confirmed_at = NULL, state = 'PENDING_REPAIR'",
        (
            artifact["space_id"],
            asset_id,
            generation["generation_id"],
            artifact["artifact_id"],
            int(revision),
        ),
    )


class IndexCoordinator:
    """Replay pending events; SQLite confirmation alone controls readiness."""

    #: How many outbox events one engine write covers. P4-01 measured a single-row
    #: ``merge_insert`` at 48.87 ms/row on a 300 000-row generation against 0.115 ms/row for
    #: one 500-row write, so draining in batches is what makes maintenance tractable. The
    #: event-by-event semantics do not change: every event is still confirmed individually,
    #: and a batch either writes and confirms all of its events or leaves them all PENDING.
    BATCH_SIZE = 200

    #: Upper bound on the per-transaction timing samples kept for the maintenance entry
    #: (P4-02A). They are pure observability - no protocol step reads them - and the cap keeps
    #: a long drain from growing a process's memory without bound.
    MAX_WRITE_SAMPLES = 4096

    def __init__(
        self,
        catalog: Catalog,
        *,
        data_root: str | Path | None = None,
        artifact_store: ArtifactStore | None = None,
        vector_index: LanceVectorIndex | None = None,
    ) -> None:
        self.catalog = catalog
        root = resolve_data_root(data_root or catalog.db_path.parent.parent)
        self.artifacts = artifact_store or ArtifactStore.open(root)
        self.index = vector_index or LanceVectorIndex(vectors_dir(root))
        #: Last ``ensure_scalar_indexes`` outcome, for diagnostics and tests.
        self.scalar_index_report: dict[str, dict[str, Any]] = {}
        #: ``{"phase", "wait_ms", "hold_ms"}`` per drain transaction: how long the writer
        #: waited for the single-writer lock and how long it then held it (P4-02A evidence).
        self.write_samples: list[dict[str, Any]] = []
        #: Uncapped count of the drain transactions that really ran, per phase. The sample
        #: list above is bounded, so a report must take its *counts* from here.
        self.write_transaction_counts: dict[str, int] = {}
        #: Structured ``busy_timeout`` refusals observed by the drain path. Each entry is a
        #: ``{"phase", "code", "message", "waited_ms"}`` claim that nothing was written.
        self.write_rejections: list[dict[str, Any]] = []
        #: Outbox events this coordinator confirmed. A failed ``process_all`` never returns its
        #: count, so a maintenance caller reads this instead of losing the batches that landed
        #: before a ``busy_timeout`` refusal (P4-02A review A1). Only real confirmations count:
        #: finding an event already ``CONFIRMED`` by somebody else is not one of ours.
        self.applied_events = 0

    @contextmanager
    def _timed_write(self, phase: str) -> Iterator[sqlite3.Connection]:
        """One catalog write transaction of the drain path, timed for maintenance.

        The yielded connection and the commit/rollback behaviour are exactly
        :meth:`Catalog.write`'s; this only measures the acquisition and hold times and turns a
        refusal *before* the body ran into a recorded, retryable fact. ``Catalog.write``
        reports an expired ``busy_timeout`` as ``CATALOG_UNREADABLE`` ("cannot start a catalog
        write transaction: database is locked"); a maintenance caller has to be able to tell
        that transient claim apart from a real protocol failure, so it is captured here
        without changing what is raised.
        """
        started = time.perf_counter()
        entered = False
        acquired = started
        try:
            with self.catalog.write() as connection:
                entered = True
                acquired = time.perf_counter()
                yield connection
        except CatalogError as error:
            if not entered:
                self.write_rejections.append(
                    {
                        "phase": str(phase),
                        "code": str(error.code),
                        "message": str(error.message),
                        "waited_ms": round((time.perf_counter() - started) * 1000, 3),
                    }
                )
            raise
        finally:
            if entered:
                key = str(phase)
                self.write_transaction_counts[key] = self.write_transaction_counts.get(key, 0) + 1
                self._record_write_sample(phase=phase, started=started, acquired=acquired)

    def _record_write_sample(self, *, phase: str, started: float, acquired: float) -> None:
        if len(self.write_samples) < self.MAX_WRITE_SAMPLES:
            finished = time.perf_counter()
            self.write_samples.append(
                {
                    "phase": str(phase),
                    "wait_ms": round((acquired - started) * 1000, 3),
                    "hold_ms": round((finished - acquired) * 1000, 3),
                }
            )

    def process_next(self, *, now_ms: int, failpoint: str | None = None) -> bool:
        """Apply one event. ``after_lance`` simulates the documented crash window.

        The claimed event is the oldest *claimable* one: an event whose artifact is waiting for
        re-execution is left where it is (see :data:`CLAIMABLE_EVENT_PREDICATE`).
        """
        with self._timed_write("claim") as connection:
            event = connection.execute(
                f"SELECT * FROM index_outbox WHERE {CLAIMABLE_EVENT_PREDICATE} "
                "ORDER BY created_at, event_id LIMIT 1"
            ).fetchone()
            if event is None:
                return False
            connection.execute(
                "UPDATE index_outbox SET state = 'IN_FLIGHT', attempts = attempts + 1, "
                "last_error_code = NULL, updated_at = ? WHERE event_id = ?",
                (now_ms, event["event_id"]),
            )
            event_id = str(event["event_id"])

        try:
            # One ``BEGIN IMMEDIATE`` transaction spans the authority re-read, the supersede
            # check, the engine write and the confirmation. The claim above is already
            # committed - which is what keeps the documented ``after_lance`` crash window at
            # IN_FLIGHT - but from here on no other writer can publish a newer projection
            # revision, claim this event or confirm it, so a lower revision can never land on
            # top of a newer one (P4-01C focused review A1: "the old writer must not land
            # last").
            with self._timed_write("apply") as connection:
                current = connection.execute(
                    "SELECT state FROM index_outbox WHERE event_id = ?", (event_id,)
                ).fetchone()
                if current is None:
                    raise CatalogError(INDEX_SUBMISSION_FAILED, "outbox event disappeared")
                if str(current["state"]) == "CONFIRMED":
                    # A second coordinator claimed and confirmed this event before this
                    # transaction began: the work is done and nothing of ours may be written
                    # on top of it.
                    return True
                if str(current["state"]) != "IN_FLIGHT":
                    raise CatalogError(
                        INDEX_SUBMISSION_FAILED, "outbox claim was lost before confirmation"
                    )
                event = self.catalog.read_one(
                    "SELECT * FROM index_outbox WHERE event_id = ?", (event_id,)
                )
                artifact = self.catalog.read_one(
                    "SELECT * FROM artifacts WHERE artifact_id = ?", (event["artifact_id"],)
                )
                generation = self.catalog.read_one(
                    "SELECT * FROM index_generations WHERE generation_id = ?",
                    (event["generation_id"],),
                )
                if artifact is None or generation is None:
                    raise CatalogError(
                        INDEX_SUBMISSION_FAILED, "outbox references are incomplete"
                    )
                self._validate_event_payload(event)
                self._validate_space(event, artifact, generation)
                if self._is_superseded(event):
                    connection.execute(
                        "UPDATE index_outbox SET state = 'CONFIRMED', updated_at = ? "
                        "WHERE event_id = ?",
                        (now_ms, event_id),
                    )
                    self._make_generation_ready(
                        connection, str(event["generation_id"]), now_ms
                    )
                    self.applied_events += 1
                    return True
                record = self._record(event, artifact, generation)
                self.index.upsert(generation=generation, record=record)
                if failpoint == "after_lance":
                    raise _InjectedCrash("after_lance")
                entry = connection.execute(
                    "SELECT artifact_id FROM index_entries WHERE space_id = ? AND asset_id = ? "
                    "AND generation_id = ?",
                    (event["space_id"], event["asset_id"], event["generation_id"]),
                ).fetchone()
                if entry is not None and str(entry["artifact_id"]) == str(event["artifact_id"]):
                    connection.execute(
                        "UPDATE index_entries SET confirmation_revision = ?, confirmed_at = ?, "
                        "state = 'CONFIRMED' WHERE space_id = ? AND asset_id = ? AND generation_id = ?",
                        (
                            event["projection_revision"],
                            now_ms,
                            event["space_id"],
                            event["asset_id"],
                            event["generation_id"],
                        ),
                    )
                connection.execute(
                    "UPDATE index_outbox SET state = 'CONFIRMED', updated_at = ? WHERE event_id = ?",
                    (now_ms, event_id),
                )
                self._make_generation_ready(connection, str(event["generation_id"]), now_ms)
            self.applied_events += 1
            return True
        except _InjectedCrash:
            raise
        except Exception as error:
            with self._timed_write("revert") as connection:
                connection.execute(
                    "UPDATE index_outbox SET state = 'PENDING', last_error_code = ?, updated_at = ? "
                    "WHERE event_id = ? AND state = 'IN_FLIGHT'",
                    (getattr(error, "code", INDEX_SUBMISSION_FAILED), now_ms, event_id),
                )
            if isinstance(error, CatalogError):
                raise
            raise CatalogError(
                INDEX_SUBMISSION_FAILED,
                "derived index submission failed",
                details={"reason": type(error).__name__},
            ) from error

    def process_all(self, *, now_ms: int, limit: int = 10_000) -> int:
        # The one maintenance-side, optional index: creating it here (never on a read path)
        # is what turns the candidate-ID block query from a whole-generation scan into an
        # indexed lookup. "ensure_scalar_indexes" reports its outcome and cannot raise, and it
        # cannot change readiness.
        self.scalar_index_report = self.ensure_scalar_indexes()
        count = 0
        while count < int(limit):
            applied = self.process_batch(
                now_ms=now_ms, max_events=min(self.BATCH_SIZE, int(limit) - count)
            )
            if not applied:
                break
            count += applied
        return count

    def ensure_scalar_indexes(self) -> dict[str, dict[str, Any]]:
        """Best-effort ``asset_id`` scalar index for every non-retired generation."""
        report: dict[str, dict[str, Any]] = {}
        for row in self.catalog.read_all(
            "SELECT generation_id, dimension FROM index_generations "
            "WHERE state <> 'RETIRED' ORDER BY generation_id"
        ):
            generation_id = str(row["generation_id"])
            report[generation_id] = self.index.ensure_scalar_index(
                generation_id=generation_id, dimension=int(row["dimension"])
            )
        return report

    def process_batch(
        self, *, now_ms: int, max_events: int, failpoint: str | None = None
    ) -> int:
        """Apply up to ``max_events`` events with one engine write per generation.

        The observable protocol is the event protocol of :meth:`process_next`: claim under a
        short write transaction, write the derived rows, then confirm every claimed event in
        one write transaction. What changes is only how many events share one engine write.

        Failure semantics are deliberately all-or-nothing per batch: if the engine write or
        the confirmation raises, no event of the batch is confirmed and every claimed event
        goes back to ``PENDING`` with the error code, exactly as the single-event path does.
        ``failpoint="after_lance"`` still simulates the documented crash window after the
        engine write and before any confirmation.

        Concurrency is handled by making the write critical section atomic, not by hoping the
        protocol is single-writer: the claim above is committed (that is what keeps the
        documented ``after_lance`` crash window at ``IN_FLIGHT``), and then **one**
        ``BEGIN IMMEDIATE`` transaction spans the authority re-read, the supersede check, the
        engine write and every confirmation. A second coordinator - or a publisher creating a
        newer projection revision - is serialised behind that transaction, so a *lower*
        revision can never land on top of a newer one (P4-01C focused review A1: "the old
        writer must not land last"). An event another coordinator claimed and confirmed before
        this transaction began is simply not ours any more: it is skipped, never written and
        never reported as a lost claim.

        The batch is bounded by *claimable* events only: an event whose artifact is waiting for
        re-execution (``RECLAIMED``) is not claimed at all, so one reclaimed artifact can no
        longer stop every other event behind it. See :data:`CLAIMABLE_EVENT_PREDICATE` for why
        that is an isolation rule and not a way to skip an unreadable file.
        """
        claimed = int(max_events)
        if claimed < 1:
            return 0
        with self._timed_write("claim") as connection:
            rows = connection.execute(
                f"SELECT event_id FROM index_outbox WHERE {CLAIMABLE_EVENT_PREDICATE} "
                "ORDER BY created_at, event_id LIMIT ?",
                (claimed,),
            ).fetchall()
            if not rows:
                return 0
            event_ids = [str(row["event_id"]) for row in rows]
            placeholders = ", ".join("?" for _ in event_ids)
            connection.execute(
                "UPDATE index_outbox SET state = 'IN_FLIGHT', attempts = attempts + 1, "
                f"last_error_code = NULL, updated_at = ? WHERE event_id IN ({placeholders})",
                (now_ms, *event_ids),
            )
        try:
            with self._timed_write("apply") as connection:
                # ``BEGIN IMMEDIATE`` already holds the single-writer lock: read which of the
                # claimed events are still ours, and treat the rest as somebody else's work.
                ours = {
                    str(row["event_id"])
                    for row in connection.execute(
                        f"SELECT event_id FROM index_outbox WHERE event_id IN ({placeholders}) "
                        "AND state = 'IN_FLIGHT'",
                        tuple(event_ids),
                    ).fetchall()
                }
                if not ours:
                    return 0
                events = [
                    event
                    for event in self.catalog.read_all(
                        f"SELECT * FROM index_outbox WHERE event_id IN ({placeholders}) "
                        "ORDER BY created_at, event_id",
                        tuple(event_ids),
                    )
                    if str(event["event_id"]) in ours
                ]
                writable: dict[str, list[dict[str, Any]]] = {}
                writable_ids: set[str] = set()
                touched: set[str] = set()
                for event in events:
                    artifact = self.catalog.read_one(
                        "SELECT * FROM artifacts WHERE artifact_id = ?",
                        (event["artifact_id"],),
                    )
                    generation = self.catalog.read_one(
                        "SELECT * FROM index_generations WHERE generation_id = ?",
                        (event["generation_id"],),
                    )
                    if artifact is None or generation is None:
                        raise CatalogError(
                            INDEX_SUBMISSION_FAILED, "outbox references are incomplete"
                        )
                    self._validate_event_payload(event)
                    self._validate_space(event, artifact, generation)
                    touched.add(str(event["generation_id"]))
                    if self._is_superseded(event):
                        continue
                    writable_ids.add(str(event["event_id"]))
                    writable.setdefault(str(event["generation_id"]), []).append(
                        self._record(event, artifact, generation)
                    )
                for generation_id, records in writable.items():
                    generation = self.catalog.read_one(
                        "SELECT * FROM index_generations WHERE generation_id = ?",
                        (generation_id,),
                    )
                    if generation is None:
                        raise CatalogError(
                            INDEX_SUBMISSION_FAILED, "outbox references are incomplete"
                        )
                    self.index.upsert_batch(generation=generation, records=records)
                if failpoint == "after_lance":
                    raise _InjectedCrash("after_lance")
                for event in events:
                    event_id = str(event["event_id"])
                    current = connection.execute(
                        "SELECT state FROM index_outbox WHERE event_id = ?", (event_id,)
                    ).fetchone()
                    if current is None or str(current["state"]) != "IN_FLIGHT":
                        raise CatalogError(
                            INDEX_SUBMISSION_FAILED, "outbox claim was lost before confirmation"
                        )
                    if event_id not in writable_ids:
                        connection.execute(
                            "UPDATE index_outbox SET state = 'CONFIRMED', updated_at = ? "
                            "WHERE event_id = ?",
                            (now_ms, event_id),
                        )
                        continue
                    entry = connection.execute(
                        "SELECT artifact_id FROM index_entries WHERE space_id = ? "
                        "AND asset_id = ? AND generation_id = ?",
                        (event["space_id"], event["asset_id"], event["generation_id"]),
                    ).fetchone()
                    if entry is not None and str(entry["artifact_id"]) == str(
                        event["artifact_id"]
                    ):
                        connection.execute(
                            "UPDATE index_entries SET confirmation_revision = ?, "
                            "confirmed_at = ?, state = 'CONFIRMED' WHERE space_id = ? "
                            "AND asset_id = ? AND generation_id = ?",
                            (
                                event["projection_revision"],
                                now_ms,
                                event["space_id"],
                                event["asset_id"],
                                event["generation_id"],
                            ),
                        )
                    connection.execute(
                        "UPDATE index_outbox SET state = 'CONFIRMED', updated_at = ? "
                        "WHERE event_id = ?",
                        (now_ms, event_id),
                    )
                for generation_id in sorted(touched):
                    self._make_generation_ready(connection, generation_id, now_ms)
            self.applied_events += len(events)
            return len(events)
        except _InjectedCrash:
            raise
        except Exception as error:
            with self._timed_write("revert") as connection:
                connection.execute(
                    "UPDATE index_outbox SET state = 'PENDING', last_error_code = ?, "
                    f"updated_at = ? WHERE event_id IN ({placeholders}) AND state = 'IN_FLIGHT'",
                    (getattr(error, "code", INDEX_SUBMISSION_FAILED), now_ms, *event_ids),
                )
            if isinstance(error, CatalogError):
                raise
            raise CatalogError(
                INDEX_SUBMISSION_FAILED,
                "derived index submission failed",
                details={"reason": type(error).__name__},
            ) from error

    @staticmethod
    def _validate_event_payload(event: Any) -> None:
        import hashlib

        payload_json = str(event["payload_json"])
        if hashlib.sha256(payload_json.encode("utf-8")).hexdigest() != str(
            event["payload_checksum"]
        ):
            raise CatalogError(INDEX_SUBMISSION_FAILED, "outbox payload checksum is invalid")
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as error:
            raise CatalogError(INDEX_SUBMISSION_FAILED, "outbox payload JSON is invalid") from error
        expected = {
            "artifact_id": str(event["artifact_id"]),
            "asset_id": int(event["asset_id"]),
            "generation_id": str(event["generation_id"]),
            "projection_revision": int(event["projection_revision"]),
            "space_id": str(event["space_id"]),
        }
        if payload != expected:
            raise CatalogError(INDEX_SUBMISSION_FAILED, "outbox payload does not match its columns")

    @staticmethod
    def _validate_space(event: Any, artifact: Any, generation: Any) -> None:
        values = {str(event["space_id"]), str(artifact["space_id"]), str(generation["space_id"])}
        if len(values) != 1 or int(artifact["dimension"]) != int(generation["dimension"]):
            raise CatalogError(SPACE_MISMATCH, "outbox, artifact and generation spaces differ")

    def _is_superseded(self, event: Any) -> bool:
        row = self.catalog.read_one(
            "SELECT MAX(projection_revision) AS revision FROM index_outbox "
            "WHERE space_id = ? AND asset_id = ? AND generation_id = ?",
            (event["space_id"], event["asset_id"], event["generation_id"]),
        )
        return row is not None and int(row["revision"] or 0) > int(event["projection_revision"])

    def _record(self, event: Any, artifact: Any, generation: Any) -> dict[str, Any]:
        data = self.artifacts.read(artifact)
        try:
            payload = json.loads(data.decode("utf-8"))
            vector = [float(value) for value in payload["vector"]]
        except (UnicodeDecodeError, ValueError, KeyError, TypeError) as error:
            raise CatalogError(ARTIFACT_MISSING, "embedding artifact payload is unreadable") from error
        sources = [
            int(row["source_id"])
            for row in self.catalog.read_all(
                "SELECT DISTINCT source_id FROM file_locations WHERE asset_id = ? "
                "AND availability = 'PRESENT' ORDER BY source_id",
                (event["asset_id"],),
            )
        ]
        asset = self.catalog.read_one("SELECT state FROM assets WHERE asset_id = ?", (event["asset_id"],))
        return {
            "record_id": f"{event['generation_id']}:{int(event['asset_id'])}",
            "asset_id": int(event["asset_id"]),
            "generation_id": str(event["generation_id"]),
            "space_id": str(event["space_id"]),
            "artifact_revision": str(artifact["artifact_revision"]),
            # Filtering facts are re-read now; a late event cannot restore an old projection.
            "projection_revision": int(event["projection_revision"]),
            "vector": vector,
            "source_ids": sources,
            "visible": asset is not None and str(asset["state"]) == "ACTIVE",
        }

    @staticmethod
    def _make_generation_ready(connection: sqlite3.Connection, generation_id: str, now_ms: int) -> None:
        pending = connection.execute(
            "SELECT EXISTS(SELECT 1 FROM index_outbox "
            "WHERE generation_id = ? AND state <> 'CONFIRMED')",
            (generation_id,),
        ).fetchone()[0]
        if int(pending):
            return
        connection.execute(
            "UPDATE index_generations SET state = 'READY', updated_at = ? WHERE generation_id = ? "
            "AND state = 'PENDING'",
            (now_ms, generation_id),
        )
        generation = connection.execute(
            "SELECT space_id, state FROM index_generations WHERE generation_id = ?", (generation_id,)
        ).fetchone()
        if generation is None or str(generation["state"]) != "READY":
            return
        active = connection.execute(
            "SELECT generation_id FROM index_generations WHERE space_id = ? AND is_active = 1",
            (generation["space_id"],),
        ).fetchone()
        if active is None:
            connection.execute(
                "UPDATE index_generations SET is_active = 1, updated_at = ? WHERE generation_id = ?",
                (now_ms, generation_id),
            )

    def semantic_ready(self, *, space_id: str, asset_id: int, generation_id: str) -> bool:
        row = self.catalog.read_one(
            "SELECT e.state AS entry_state, g.state AS generation_state "
            "FROM index_entries e JOIN index_generations g USING(generation_id) "
            "WHERE e.space_id = ? AND e.asset_id = ? AND e.generation_id = ?",
            (space_id, int(asset_id), generation_id),
        )
        return bool(
            row is not None
            and str(row["entry_state"]) == "CONFIRMED"
            and str(row["generation_state"]) == "READY"
        )

    def audit_and_schedule_repairs(self, *, now_ms: int) -> dict[str, list[str]]:
        """Recreate missing confirmations/outbox; report artifacts needing re-execution.

        The audit takes **one** decision per ``(space, asset, generation)`` triple, for the
        triple's newest artifact (ordered by the authority's publish revision - see the ordering
        comment below). A projection is per triple, and one asset can legitimately own several
        embedding artifacts for the same generation: a P4-01C content change publishes a second
        artifact and leaves the first
        ``PUBLISHED``. Scheduling each of those artifacts separately is what P4-02C's engineering
        review measured as an unbounded audit - every pass allocated one more event per artifact
        of the triple, even though the triple's newest unconfirmed event already carried exactly
        that work. Coming back to :meth:`_schedule_existing` for the newest artifact means the
        pending event is *reused*, so a repeated audit over an unchanged state allocates nothing
        while a genuine loss (a deleted confirmation, a missing engine row, a replaced artifact)
        still schedules its repair.

        The decision also refuses to repair a triple whose newest artifact is the one waiting for
        re-execution. Projecting an older artifact there would allocate a revision *above* the
        re-executed one, so the artifact an operator just republished would come back superseded
        and the stale vector would stay reachable while every report reads "confirmed" (P4-02C
        fix-surface review F3). Such a triple is reported through ``requires_reexecution``
        instead, and its repair is the existing recovery entry.
        """
        scheduled: list[str] = []
        reexecute: list[str] = []
        rows = self.catalog.read_all(
            "SELECT a.*, j.subject_type, j.subject_id FROM artifacts a "
            "JOIN jobs j ON j.job_id = a.job_id WHERE a.artifact_type = 'embedding'"
        )
        # Which artifact of a triple is the *current* one is an authority question, not a
        # timestamp one. Every publish stamps its outbox event with the authority's
        # ``catalog_filter_revision`` (the event's ``projection_revision``), and that is exactly
        # the value the drain compares when it decides which projection wins. ``created_at`` has
        # millisecond granularity, so two content versions published inside one millisecond can
        # order the older artifact first by ``artifact_id`` - P4-02C's Sol focused review (F4)
        # measured the audit then allocating the higher revision for the *stale* content, which
        # became the live search row. The lowest revision an artifact owns is its publish
        # revision (a repair event is always allocated above every revision of the triple), so
        # ``MIN`` is the authority order; the remaining keys only break a tie for an artifact
        # whose publish event is no longer in the outbox.
        publish_revision = {
            str(row["artifact_id"]): int(row["revision"])
            for row in self.catalog.read_all(
                "SELECT artifact_id, MIN(projection_revision) AS revision FROM index_outbox "
                "WHERE event_type = 'UPSERT_VECTOR' GROUP BY artifact_id"
            )
        }

        def authority_order(item: dict[str, Any]) -> tuple[int, int, int, str]:
            artifact = item["artifact"]
            artifact_id = str(artifact["artifact_id"])
            return (
                publish_revision.get(artifact_id, -1),
                int(artifact["job_id"]),
                int(artifact["created_at"]),
                artifact_id,
            )

        # Every (space, asset, generation) that still needs its projection, with the artifacts
        # that map onto it. The decision below consumes one entry per triple, never one per row.
        triples: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
        repairable: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
        for artifact in rows:
            generation = self.catalog.read_one(
                "SELECT * FROM index_generations WHERE space_id = ? AND processor_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (artifact["space_id"], artifact["processor_id"]),
            )
            if generation is None or str(artifact["subject_type"]) != "asset":
                continue
            asset_id = int(artifact["subject_id"])
            key = (str(artifact["space_id"]), asset_id, str(generation["generation_id"]))
            item = {"artifact": artifact, "generation": generation, "asset_id": asset_id}
            triples.setdefault(key, []).append(item)
            try:
                self.artifacts.read(artifact)
            except CatalogError:
                reexecute.append(str(artifact["artifact_id"]))
                with self.catalog.write() as connection:
                    connection.execute(
                        "UPDATE artifacts SET lifecycle_state = 'RECLAIMED', updated_at = ? "
                        "WHERE artifact_id = ?",
                        (now_ms, artifact["artifact_id"]),
                    )
                    connection.execute(
                        "UPDATE index_entries SET state = 'PENDING_REPAIR', confirmed_at = NULL "
                        "WHERE artifact_id = ?",
                        (artifact["artifact_id"],),
                    )
                # A corrupt file cannot be republished over safely. Once SQLite records
                # RECLAIMED, remove the bad derived bytes; missing files are already fine.
                try:
                    self.artifacts.resolve_uri(str(artifact["storage_uri"])).unlink()
                except OSError:
                    pass
                continue
            confirmed = self.index.has(
                generation_id=str(generation["generation_id"]),
                dimension=int(generation["dimension"]),
                asset_id=asset_id,
            )
            entry = self.catalog.read_one(
                "SELECT state FROM index_entries WHERE space_id = ? AND asset_id = ? AND generation_id = ?",
                (artifact["space_id"], asset_id, generation["generation_id"]),
            )
            if entry is not None and str(entry["state"]) == "CONFIRMED" and confirmed:
                continue
            repairable.setdefault(key, []).append(item)
        for key in sorted(repairable):
            newest = max(triples[key], key=authority_order)
            if str(newest["artifact"]["artifact_id"]) not in {
                str(item["artifact"]["artifact_id"]) for item in repairable[key]
            }:
                # The newest artifact of this triple is unreadable or already waiting for
                # re-execution, so the honest repair is the recovery entry, not a projection of
                # an older artifact that would outrank it for ever.
                continue
            with self.catalog.write() as connection:
                created = self._schedule_existing(
                    connection,
                    artifact=newest["artifact"],
                    generation=newest["generation"],
                    asset_id=newest["asset_id"],
                    now_ms=now_ms,
                )
            if created:
                scheduled.append(str(newest["artifact"]["artifact_id"]))
        return {"scheduled": scheduled, "requires_reexecution": reexecute}

    def schedule_projection_refresh(
        self,
        connection: sqlite3.Connection,
        *,
        asset_ids: list[int],
        projection_revision: int,
        now_ms: int,
    ) -> int:
        """Queue latest SQLite facts for confirmed vectors inside an authority transaction.

        P2-03A and later manual-knowledge services call this with the same connection that
        increments ``catalog_filter_revision``. Assets without a confirmed vector need no
        event: their first index submission will read the then-current SQLite facts.
        """
        created = 0
        for asset_id in sorted(set(int(value) for value in asset_ids)):
            rows = connection.execute(
                "SELECT a.*, g.generation_id, g.dimension AS generation_dimension, "
                "g.space_id AS generation_space_id, g.processor_id AS generation_processor_id, "
                "g.state AS generation_state, g.is_active AS generation_is_active, "
                "g.created_at AS generation_created_at, g.updated_at AS generation_updated_at "
                "FROM index_entries e JOIN artifacts a ON a.artifact_id = e.artifact_id "
                "JOIN index_generations g ON g.generation_id = e.generation_id "
                "WHERE e.asset_id = ? AND e.state = 'CONFIRMED'",
                (asset_id,),
            ).fetchall()
            for row in rows:
                generation = {
                    "generation_id": row["generation_id"],
                    "space_id": row["generation_space_id"],
                    "processor_id": row["generation_processor_id"],
                    "dimension": row["generation_dimension"],
                    "state": row["generation_state"],
                    "is_active": row["generation_is_active"],
                    "created_at": row["generation_created_at"],
                    "updated_at": row["generation_updated_at"],
                }
                created += int(
                    self._schedule_existing(
                        connection,
                        artifact=row,
                        generation=generation,
                        asset_id=asset_id,
                        now_ms=now_ms,
                        projection_revision=int(projection_revision),
                    )
                )
        return created

    @staticmethod
    def _schedule_existing(
        connection: sqlite3.Connection,
        *,
        artifact: Any,
        generation: Any,
        asset_id: int,
        now_ms: int,
        projection_revision: int | None = None,
    ) -> bool:
        """Queue "project this artifact" for an unchanged (artifact, generation).

        Two callers need opposite things from the revision and both are served here:

        * :meth:`schedule_projection_refresh` passes the authority transaction's own
          ``projection_revision``, and the event has to be created with exactly that revision;
        * :meth:`audit_and_schedule_repairs` passes nothing, and this method allocates the next
          revision. That is the path P4-02A's engineering review (A3) found non-idempotent:
          every audit run allocated ``MAX(projection_revision) + 1`` again, so an outbox that
          could not drain - for example because one artifact had become unreadable - grew by
          one unconfirmed event per artifact per audit run, while the drain kept failing on the
          oldest event. The audit therefore *reuses* the unconfirmed event that already carries
          exactly this work whenever that event is the newest one for the triple, and only
          allocates a new revision when it is not. It calls this method once per triple, for the
          triple's newest artifact (see :meth:`audit_and_schedule_repairs`), so "the newest
          unconfirmed event is this artifact's own" is the same statement as "this triple's
          projection work is already queued".

        A genuine repair still schedules: a deleted confirmation, a missing engine row or a
        replaced artifact leaves either no unconfirmed event or a newer one, so the audit
        behaves exactly as P2-03 documents. Repeated audits over an unchanged state allocate
        nothing and cannot enlarge the backlog.
        """
        import hashlib

        revision: int
        if projection_revision is not None:
            revision = int(projection_revision)
        else:
            newest = int(
                connection.execute(
                    "SELECT COALESCE(MAX(projection_revision), 0) FROM index_outbox "
                    "WHERE space_id = ? AND asset_id = ? AND generation_id = ?",
                    (artifact["space_id"], asset_id, generation["generation_id"]),
                ).fetchone()[0]
            )
            reusable = connection.execute(
                "SELECT projection_revision FROM index_outbox "
                "WHERE space_id = ? AND asset_id = ? AND generation_id = ? "
                "AND event_type = 'UPSERT_VECTOR' AND state <> 'CONFIRMED' "
                "AND artifact_id = ? ORDER BY projection_revision DESC LIMIT 1",
                (
                    artifact["space_id"],
                    asset_id,
                    generation["generation_id"],
                    artifact["artifact_id"],
                ),
            ).fetchone()
            if reusable is not None and int(reusable["projection_revision"]) >= newest:
                # The pending event already is this work: it confirms as soon as the artifact
                # is readable again, so there is nothing to allocate. The entry still records
                # the honest "not yet re-projected" state.
                _mark_entry_pending_repair(
                    connection,
                    artifact=artifact,
                    generation=generation,
                    asset_id=asset_id,
                    revision=int(reusable["projection_revision"]),
                )
                return False
            revision = newest + 1
        payload = {
            "artifact_id": str(artifact["artifact_id"]),
            "asset_id": asset_id,
            "generation_id": str(generation["generation_id"]),
            "projection_revision": revision,
            "space_id": str(artifact["space_id"]),
        }
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        event_id = "evt-" + hashlib.sha256(
            f"REPAIR\0{artifact['artifact_id']}\0{generation['generation_id']}\0{revision}".encode()
        ).hexdigest()
        cursor = connection.execute(
            "INSERT OR IGNORE INTO index_outbox(event_id, event_type, artifact_id, space_id, asset_id, "
            "generation_id, projection_revision, payload_json, payload_checksum, state, attempts, "
            "last_error_code, created_at, updated_at) VALUES (?, 'UPSERT_VECTOR', ?, ?, ?, ?, ?, ?, ?, "
            "'PENDING', 0, NULL, ?, ?)",
            (
                event_id,
                artifact["artifact_id"],
                artifact["space_id"],
                asset_id,
                generation["generation_id"],
                revision,
                payload_json,
                hashlib.sha256(payload_json.encode()).hexdigest(),
                now_ms,
                now_ms,
            ),
        )
        if cursor.rowcount == 0:
            existing = connection.execute(
                "SELECT state FROM index_outbox WHERE space_id = ? AND asset_id = ? "
                "AND generation_id = ? AND projection_revision = ? AND event_type = 'UPSERT_VECTOR'",
                (artifact["space_id"], asset_id, generation["generation_id"], revision),
            ).fetchone()
            if existing is not None and str(existing["state"]) == "CONFIRMED":
                return False
        _mark_entry_pending_repair(
            connection,
            artifact=artifact,
            generation=generation,
            asset_id=asset_id,
            revision=revision,
        )
        return True


class _InjectedCrash(RuntimeError):
    pass
