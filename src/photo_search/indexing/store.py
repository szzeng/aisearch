"""Idempotent P2-03 outbox consumer and index repair coordinator."""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Sequence
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
        ``present``/``created`` both mean the index is usable; ``unavailable`` means the
        engine refused it - or refused to even list its index catalogue - and the
        candidate-ID path stays in its scan mode. The catalogue read is inside the same
        guard on purpose: a maintenance step may never abort the outbox drain because index
        metadata is unreadable (P4-01C review B2). ``_table`` stays outside, so a missing or
        dimension-mismatched table is still a structured catalog failure.
        """
        table = self._table(
            generation_id=str(generation_id), dimension=int(dimension), create=False
        )
        if table is None:
            return {"state": "no_table", "index": ASSET_SCALAR_INDEX}
        try:
            import lancedb.index as lance_index

            names = {str(getattr(item, "name", item)) for item in table.list_indices()}
            if ASSET_SCALAR_INDEX in names:
                return {"state": "present", "index": ASSET_SCALAR_INDEX}
            table.create_index(ASSET_SCALAR_COLUMN, config=lance_index.BTree())
        except Exception as error:  # noqa: BLE001 - a refused index must not fail a write
            return {
                "state": "unavailable",
                "index": ASSET_SCALAR_INDEX,
                "reason": type(error).__name__,
            }
        return {"state": "created", "index": ASSET_SCALAR_INDEX}

    def count(self, *, generation_id: str, dimension: int, space_id: str) -> int:
        rows = self.rows(generation_id=generation_id, dimension=dimension)
        if any(str(row["space_id"]) != str(space_id) for row in rows):
            raise CatalogError(SPACE_MISMATCH, "a Lance generation contains a foreign space")
        return len(rows)

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
        conditions = ["asset_id IN (" + ", ".join(str(value) for value in ids) + ")"]
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


class IndexCoordinator:
    """Replay pending events; SQLite confirmation alone controls readiness."""

    #: How many outbox events one engine write covers. P4-01 measured a single-row
    #: ``merge_insert`` at 48.87 ms/row on a 300 000-row generation against 0.115 ms/row for
    #: one 500-row write, so draining in batches is what makes maintenance tractable. The
    #: event-by-event semantics do not change: every event is still confirmed individually,
    #: and a batch either writes and confirms all of its events or leaves them all PENDING.
    BATCH_SIZE = 200

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

    def process_next(self, *, now_ms: int, failpoint: str | None = None) -> bool:
        """Apply one event. ``after_lance`` simulates the documented crash window."""
        with self.catalog.write() as connection:
            event = connection.execute(
                "SELECT * FROM index_outbox WHERE state IN ('PENDING', 'IN_FLIGHT') "
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
            with self.catalog.write() as connection:
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
            return True
        except _InjectedCrash:
            raise
        except Exception as error:
            with self.catalog.write() as connection:
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
        """
        claimed = int(max_events)
        if claimed < 1:
            return 0
        with self.catalog.write() as connection:
            rows = connection.execute(
                "SELECT event_id FROM index_outbox WHERE state IN ('PENDING', 'IN_FLIGHT') "
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
            with self.catalog.write() as connection:
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
            return len(events)
        except _InjectedCrash:
            raise
        except Exception as error:
            with self.catalog.write() as connection:
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
            "SELECT COUNT(*) FROM index_outbox WHERE generation_id = ? AND state <> 'CONFIRMED'",
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
        """Recreate missing confirmations/outbox; report artifacts needing re-execution."""
        scheduled: list[str] = []
        reexecute: list[str] = []
        rows = self.catalog.read_all(
            "SELECT a.*, j.subject_type, j.subject_id FROM artifacts a "
            "JOIN jobs j ON j.job_id = a.job_id WHERE a.artifact_type = 'embedding'"
        )
        for artifact in rows:
            generation = self.catalog.read_one(
                "SELECT * FROM index_generations WHERE space_id = ? AND processor_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (artifact["space_id"], artifact["processor_id"]),
            )
            if generation is None or str(artifact["subject_type"]) != "asset":
                continue
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
            asset_id = int(artifact["subject_id"])
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
            with self.catalog.write() as connection:
                created = self._schedule_existing(
                    connection, artifact=artifact, generation=generation, asset_id=asset_id, now_ms=now_ms
                )
            if created:
                scheduled.append(str(artifact["artifact_id"]))
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
        import hashlib

        revision = (
            int(projection_revision)
            if projection_revision is not None
            else int(
                connection.execute(
                    "SELECT COALESCE(MAX(projection_revision), 0) + 1 FROM index_outbox "
                    "WHERE space_id = ? AND asset_id = ? AND generation_id = ?",
                    (artifact["space_id"], asset_id, generation["generation_id"]),
                ).fetchone()[0]
            )
        )
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
                revision,
            ),
        )
        return True


class _InjectedCrash(RuntimeError):
    pass
