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
        if str(record["space_id"]) != str(generation["space_id"]):
            raise CatalogError(SPACE_MISMATCH, "record and generation spaces differ")
        vector = record["vector"]
        if len(vector) != int(generation["dimension"]):
            raise CatalogError(SPACE_MISMATCH, "record and generation dimensions differ")
        table = self._table(
            generation_id=str(generation["generation_id"]),
            dimension=int(generation["dimension"]),
            create=True,
        )
        (
            table.merge_insert("record_id")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute([record])
        )

    def rows(self, *, generation_id: str, dimension: int) -> list[dict[str, Any]]:
        table = self._table(generation_id=generation_id, dimension=dimension, create=False)
        if table is None:
            return []
        return list(table.to_arrow().to_pylist())

    def has(self, *, generation_id: str, dimension: int, asset_id: int) -> bool:
        return any(
            int(row["asset_id"]) == int(asset_id)
            for row in self.rows(generation_id=generation_id, dimension=dimension)
        )

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
    ) -> list[dict[str, Any]]:
        """Nearest rows of one generation, ranked deterministic and space-checked.

        ``where`` is handed to the engine as the documented pre-filter: P0-02C verified
        for this locked version that a filtered search ranks *inside* the filter (the
        global nearest neighbour cannot be returned when the filter excludes it) and that
        an unusable filter column raises instead of degrading into an unfiltered search.

        Nothing here decides a filter: the caller passes the authoritative candidate ids
        of the bound space. Every returned row is checked against the generation and the
        expected space, so two equal-dimension spaces can never be mixed by accident.
        """
        vector = self._query_vector(dimension=dimension, query_vector=query_vector)
        table = self._table(
            generation_id=str(generation_id), dimension=int(dimension), create=False
        )
        if table is None:
            return []
        builder = table.search(vector).limit(int(limit))
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
        """
        ids = sorted({int(value) for value in asset_ids})
        if not ids:
            return []
        conditions = ["asset_id IN (" + ", ".join(str(value) for value in ids) + ")"]
        if space_id is not None:
            conditions.append(f"space_id = {_lance_literal(str(space_id))}")
        return self.search(
            generation_id=str(generation_id),
            dimension=int(dimension),
            query_vector=query_vector,
            limit=int(limit),
            where=" AND ".join(conditions),
            space_id=space_id,
        )

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
            event = self.catalog.read_one("SELECT * FROM index_outbox WHERE event_id = ?", (event_id,))
            artifact = self.catalog.read_one(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (event["artifact_id"],)
            )
            generation = self.catalog.read_one(
                "SELECT * FROM index_generations WHERE generation_id = ?",
                (event["generation_id"],),
            )
            if artifact is None or generation is None:
                raise CatalogError(INDEX_SUBMISSION_FAILED, "outbox references are incomplete")
            self._validate_event_payload(event)
            self._validate_space(event, artifact, generation)
            if self._is_superseded(event):
                with self.catalog.write() as connection:
                    connection.execute(
                        "UPDATE index_outbox SET state = 'CONFIRMED', updated_at = ? WHERE event_id = ?",
                        (now_ms, event_id),
                    )
                return True
            record = self._record(event, artifact, generation)
            self.index.upsert(generation=generation, record=record)
            if failpoint == "after_lance":
                raise _InjectedCrash("after_lance")
            with self.catalog.write() as connection:
                current = connection.execute(
                    "SELECT state FROM index_outbox WHERE event_id = ?", (event_id,)
                ).fetchone()
                if current is None or str(current["state"]) != "IN_FLIGHT":
                    raise CatalogError(INDEX_SUBMISSION_FAILED, "outbox claim was lost before confirmation")
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
        count = 0
        while count < int(limit) and self.process_next(now_ms=now_ms):
            count += 1
        return count

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
