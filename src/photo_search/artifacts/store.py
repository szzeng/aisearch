"""Atomic artifact publication and the SQLite side of the P2-03 outbox protocol.

Executors may only return bounded JSON. Structured and embedding payloads are rendered as
canonical JSON; binary payloads use strict base64. Absolute or worker-chosen storage paths
are rejected. Publication uses a same-directory temporary file, fsync and ``os.replace``;
the resulting relative URI is the only path persisted in SQLite.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import math
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import (
    ARTIFACT_CONFLICT,
    ARTIFACT_INVALID,
    ARTIFACT_MISSING,
    ARTIFACT_WRITE_FAILED,
    SPACE_MISMATCH,
    CatalogError,
)
from photo_search.config import artifacts_dir, resolve_data_root
from photo_search.scheduler.contracts import ArtifactRef, JobResult, ProcessorSpec, canonical_json

TEMP_SUFFIX = ".tmp"
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024


@dataclass
class PreparedArtifact:
    ref: ArtifactRef
    payload: bytes
    target: Path
    storage_uri: str
    temporary: Path | None
    target_preexisted: bool


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def generation_id_for(space_id: str, processor_id: str) -> str:
    """Stable P2-03 generation namespace; P2-06 will bind it into SearchProfile."""
    suffix = hashlib.sha256(f"{space_id}\0{processor_id}".encode()).hexdigest()[:24]
    return f"image-gen-{suffix}"


def _event_id(*, artifact_id: str, generation_id: str, asset_id: int, revision: int) -> str:
    value = f"UPSERT_VECTOR\0{artifact_id}\0{generation_id}\0{asset_id}\0{revision}"
    return f"evt-{hashlib.sha256(value.encode()).hexdigest()}"


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    try:
        return canonical_json(payload).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise CatalogError(ARTIFACT_INVALID, "artifact payload is not finite JSON") from error


def _payload_bytes(ref: ArtifactRef) -> bytes:
    if ref.storage_uri is not None:
        raise CatalogError(
            ARTIFACT_INVALID,
            "executors cannot choose an artifact storage path",
            details={"artifact_id": ref.artifact_id},
        )
    payload = ref.payload
    if payload is None:
        raise CatalogError(
            ARTIFACT_INVALID,
            "an inline artifact payload is required",
            details={"artifact_id": ref.artifact_id},
        )
    if ref.artifact_type == "binary":
        if set(payload) != {"base64"} or not isinstance(payload.get("base64"), str):
            raise CatalogError(
                ARTIFACT_INVALID,
                "a binary artifact payload must contain only a base64 string",
            )
        try:
            data = base64.b64decode(payload["base64"], validate=True)
        except (binascii.Error, ValueError) as error:
            raise CatalogError(ARTIFACT_INVALID, "binary artifact base64 is invalid") from error
    elif ref.artifact_type == "embedding":
        if set(payload) != {"vector"} or not isinstance(payload.get("vector"), list):
            raise CatalogError(
                ARTIFACT_INVALID,
                "an embedding artifact payload must contain only a vector array",
            )
        vector = payload["vector"]
        if len(vector) != ref.dimension or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in vector
        ):
            raise CatalogError(
                ARTIFACT_INVALID,
                "embedding vector values and dimension do not match the artifact contract",
            )
        data = _canonical_bytes({"vector": [float(value) for value in vector]})
    else:
        data = _canonical_bytes(payload)
    if len(data) > MAX_ARTIFACT_BYTES:
        raise CatalogError(
            ARTIFACT_INVALID,
            "artifact payload exceeds the protocol byte limit",
            details={"byte_limit": MAX_ARTIFACT_BYTES},
        )
    if len(data) != ref.byte_size or _digest(data) != ref.checksum_sha256:
        raise CatalogError(
            ARTIFACT_INVALID,
            "artifact checksum or byte_size does not match its payload",
            details={"artifact_id": ref.artifact_id},
        )
    return data


class ArtifactStore:
    """Controlled paths plus short-transaction artifact/outbox persistence."""

    def __init__(self, data_root: str | Path) -> None:
        self.data_root = resolve_data_root(data_root).resolve()
        self.root = artifacts_dir(self.data_root).resolve()

    @classmethod
    def open(cls, data_root: str | Path) -> ArtifactStore:
        return cls(data_root)

    def _target(self, ref: ArtifactRef) -> tuple[Path, str]:
        processor = hashlib.sha256(ref.artifact_id.encode()).hexdigest()[:16]
        extension = ".json" if ref.artifact_type in {"structured", "embedding"} else ".bin"
        relative = Path("artifacts") / "pending" / processor / ref.checksum_sha256[:2] / (
            f"{ref.checksum_sha256}-{processor}{extension}"
        )
        target = (self.data_root / relative).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as error:  # pragma: no cover - every segment is locally derived
            raise CatalogError(ARTIFACT_INVALID, "derived artifact path escaped its root") from error
        return target, relative.as_posix()

    def prepare(self, ref: ArtifactRef) -> PreparedArtifact:
        payload = _payload_bytes(ref)
        target, storage_uri = self._target(ref)
        temporary: Path | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            preexisted = target.exists()
            if preexisted:
                current = target.read_bytes()
                if len(current) != len(payload) or _digest(current) != ref.checksum_sha256:
                    raise CatalogError(
                        ARTIFACT_CONFLICT,
                        "a derived artifact path already contains different bytes",
                        details={"artifact_id": ref.artifact_id},
                    )
                return PreparedArtifact(ref, payload, target, storage_uri, None, True)
            temporary = target.with_name(
                f"{target.name}.{os.getpid()}-{secrets.token_hex(6)}{TEMP_SUFFIX}"
            )
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except CatalogError:
            raise
        except OSError as error:
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass
            raise CatalogError(
                ARTIFACT_WRITE_FAILED,
                "cannot stage a processor artifact",
                details={"reason": type(error).__name__},
            ) from error
        except BaseException:
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass
            raise
        return PreparedArtifact(ref, payload, target, storage_uri, temporary, False)

    def discard(self, prepared: list[PreparedArtifact], *, remove_published: bool = False) -> None:
        for item in prepared:
            if item.temporary is not None:
                try:
                    item.temporary.unlink()
                except OSError:
                    pass
            if remove_published and not item.target_preexisted:
                try:
                    item.target.unlink()
                except OSError:
                    pass

    def _publish_file(self, item: PreparedArtifact) -> None:
        if item.temporary is None:
            return
        try:
            os.replace(item.temporary, item.target)
            item.temporary = None
        except OSError as error:
            raise CatalogError(
                ARTIFACT_WRITE_FAILED,
                "cannot atomically publish a processor artifact",
                details={"reason": type(error).__name__},
            ) from error

    def persist_result(
        self,
        connection: sqlite3.Connection,
        *,
        job: Any,
        result: JobResult,
        spec: ProcessorSpec,
        prepared: list[PreparedArtifact],
        now_ms: int,
    ) -> None:
        """Publish files and write artifact/outbox/repair rows in the caller transaction."""
        from photo_search.retention import assert_subject_retention_unblocked

        assert_subject_retention_unblocked(
            connection,
            subject_type=str(job.subject_type),
            subject_id=str(job.subject_id),
        )
        for item in prepared:
            ref = item.ref
            existing = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (ref.artifact_id,)
            ).fetchone()
            logical = connection.execute(
                "SELECT artifact_id, checksum_sha256 FROM artifacts WHERE processor_id = ? "
                "AND input_revision = ? AND artifact_type = ? AND artifact_revision = ?",
                (spec.processor_id, result.input_revision, ref.artifact_type, ref.artifact_revision),
            ).fetchone()
            if existing is not None or logical is not None:
                candidate = existing if existing is not None else logical
                if (
                    str(candidate["artifact_id"]) != ref.artifact_id
                    or str(candidate["checksum_sha256"]) != ref.checksum_sha256
                ):
                    raise CatalogError(
                        ARTIFACT_CONFLICT,
                        "an artifact logical key already maps to different content",
                        details={"artifact_id": ref.artifact_id},
                    )
                self._publish_file(item)
                continue
            self._validate_spec(ref, spec)
            self._publish_file(item)
            connection.execute(
                """
                INSERT INTO artifacts(
                    artifact_id, processor_id, input_revision, artifact_type,
                    artifact_revision, media_type, checksum_sha256, byte_size,
                    storage_uri, space_id, dimension, job_id, lifecycle_state,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PUBLISHED', ?, ?)
                """,
                (
                    ref.artifact_id,
                    spec.processor_id,
                    result.input_revision,
                    ref.artifact_type,
                    ref.artifact_revision,
                    ref.media_type,
                    ref.checksum_sha256,
                    ref.byte_size,
                    item.storage_uri,
                    ref.space_id,
                    ref.dimension,
                    job.job_id,
                    now_ms,
                    now_ms,
                ),
            )
            if ref.artifact_type == "embedding":
                self._persist_embedding_event(connection, job=job, ref=ref, now_ms=now_ms)

    @staticmethod
    def _validate_spec(ref: ArtifactRef, spec: ProcessorSpec) -> None:
        if ref.artifact_type != "embedding":
            return
        if spec.processor_kind != "embedding_model":
            raise CatalogError(ARTIFACT_INVALID, "a non-embedding processor returned an embedding")
        expected_space = str(spec.envelope.get("space_id") or "")
        expected_dimension = int(spec.envelope.get("dimension") or 0)
        if ref.space_id != expected_space or ref.dimension != expected_dimension:
            raise CatalogError(
                SPACE_MISMATCH,
                "embedding space_id or dimension does not match its processor",
                details={"processor_id": spec.processor_id},
            )

    def _persist_embedding_event(
        self,
        connection: sqlite3.Connection,
        *,
        job: Any,
        ref: ArtifactRef,
        now_ms: int,
    ) -> None:
        if str(job.subject_type) != "asset":
            raise CatalogError(ARTIFACT_INVALID, "embedding artifacts require an asset subject")
        try:
            asset_id = int(job.subject_id)
        except ValueError as error:
            raise CatalogError(ARTIFACT_INVALID, "embedding asset subject_id must be numeric") from error
        if connection.execute("SELECT 1 FROM assets WHERE asset_id = ?", (asset_id,)).fetchone() is None:
            raise CatalogError(ARTIFACT_INVALID, "embedding asset subject does not exist")
        space_id = str(ref.space_id)
        generation_id = generation_id_for(space_id, str(job.processor_id))
        connection.execute(
            "INSERT OR IGNORE INTO index_generations(generation_id, space_id, processor_id, "
            "dimension, state, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'PENDING', 0, ?, ?)",
            (generation_id, space_id, job.processor_id, ref.dimension, now_ms, now_ms),
        )
        generation = connection.execute(
            "SELECT * FROM index_generations WHERE generation_id = ?", (generation_id,)
        ).fetchone()
        if (
            generation is None
            or str(generation["space_id"]) != space_id
            or str(generation["processor_id"]) != str(job.processor_id)
            or int(generation["dimension"]) != int(ref.dimension)
            or str(generation["state"]) == "RETIRED"
        ):
            raise CatalogError(SPACE_MISMATCH, "the target index generation is incompatible")
        projection_revision = _catalog_filter_revision(connection)
        payload = {
            "artifact_id": ref.artifact_id,
            "asset_id": asset_id,
            "generation_id": generation_id,
            "projection_revision": projection_revision,
            "space_id": space_id,
        }
        payload_json = canonical_json(payload)
        event_id = _event_id(
            artifact_id=ref.artifact_id,
            generation_id=generation_id,
            asset_id=asset_id,
            revision=projection_revision,
        )
        connection.execute(
            """
            INSERT INTO index_outbox(
                event_id, event_type, artifact_id, space_id, asset_id, generation_id,
                projection_revision, payload_json, payload_checksum, state, attempts,
                last_error_code, created_at, updated_at
            ) VALUES (?, 'UPSERT_VECTOR', ?, ?, ?, ?, ?, ?, ?, 'PENDING', 0, NULL, ?, ?)
            ON CONFLICT(event_id) DO NOTHING
            """,
            (
                event_id,
                ref.artifact_id,
                space_id,
                asset_id,
                generation_id,
                projection_revision,
                payload_json,
                _digest(payload_json.encode("utf-8")),
                now_ms,
                now_ms,
            ),
        )
        connection.execute(
            """
            INSERT INTO index_entries(
                space_id, asset_id, generation_id, artifact_id,
                confirmation_revision, confirmed_at, state
            ) VALUES (?, ?, ?, ?, ?, NULL, 'PENDING_REPAIR')
            ON CONFLICT(space_id, asset_id, generation_id) DO UPDATE SET
                artifact_id = excluded.artifact_id,
                confirmation_revision = excluded.confirmation_revision,
                confirmed_at = NULL,
                state = 'PENDING_REPAIR'
            """,
            (space_id, asset_id, generation_id, ref.artifact_id, projection_revision),
        )

    def resolve_uri(self, storage_uri: str) -> Path:
        if not storage_uri or ":" in storage_uri or ".." in storage_uri.replace("\\", "/").split("/"):
            raise CatalogError(ARTIFACT_INVALID, "stored artifact URI is not controlled")
        candidate = (self.data_root / Path(storage_uri)).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as error:
            raise CatalogError(ARTIFACT_INVALID, "stored artifact URI escaped its root") from error
        return candidate

    def read(self, row: Any) -> bytes:
        path = self.resolve_uri(str(row["storage_uri"]))
        try:
            data = path.read_bytes()
        except OSError as error:
            raise CatalogError(ARTIFACT_MISSING, "published artifact content is unavailable") from error
        if len(data) != int(row["byte_size"]) or _digest(data) != str(row["checksum_sha256"]):
            raise CatalogError(ARTIFACT_MISSING, "published artifact content failed verification")
        return data

    def prune_temporary(self, *, min_age_ms: int, now_ms: int | None = None) -> list[Path]:
        now = int(time.time_ns() // 1_000_000) if now_ms is None else int(now_ms)
        removed: list[Path] = []
        if not self.root.is_dir():
            return removed
        for path in self.root.rglob(f"*{TEMP_SUFFIX}"):
            try:
                if now - int(path.stat().st_mtime * 1000) < int(min_age_ms):
                    continue
                path.unlink()
                removed.append(path)
            except OSError:
                continue
        return removed

    def reclaim(self, catalog: Any, *, artifact_id: str, now_ms: int) -> bool:
        """Reclaim an unprotected artifact; active/pending references are hard blockers."""
        row = catalog.read_one("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,))
        if row is None:
            raise CatalogError(ARTIFACT_MISSING, "artifact metadata does not exist")
        if str(row["lifecycle_state"]) == "RECLAIMED":
            return False
        pending = catalog.read_one(
            "SELECT 1 FROM index_outbox WHERE artifact_id = ? AND state <> 'CONFIRMED' LIMIT 1",
            (artifact_id,),
        )
        active = catalog.read_one(
            "SELECT 1 FROM index_entries e JOIN index_generations g USING(generation_id) "
            "WHERE e.artifact_id = ? AND g.is_active = 1 LIMIT 1",
            (artifact_id,),
        )
        if pending is not None or active is not None:
            raise CatalogError(
                ARTIFACT_CONFLICT,
                "an unconfirmed outbox or active generation still protects this artifact",
            )
        with catalog.write() as connection:
            connection.execute(
                "UPDATE artifacts SET lifecycle_state = 'RECLAIMED', updated_at = ? "
                "WHERE artifact_id = ?",
                (now_ms, artifact_id),
            )
            connection.execute(
                "UPDATE index_entries SET state = 'PENDING_REPAIR', confirmed_at = NULL "
                "WHERE artifact_id = ?",
                (artifact_id,),
            )
        try:
            self.resolve_uri(str(row["storage_uri"])).unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise CatalogError(ARTIFACT_WRITE_FAILED, "cannot reclaim an artifact file") from error
        return True

    def republish_reclaimed(
        self,
        catalog: Any,
        *,
        artifact_id: str,
        payload: dict[str, Any],
        now_ms: int,
    ) -> Path:
        """Accept deterministic re-execution output without reopening a SUCCEEDED job."""
        row = catalog.read_one("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,))
        if row is None or str(row["lifecycle_state"]) != "RECLAIMED":
            raise CatalogError(ARTIFACT_CONFLICT, "only a reclaimed artifact may be republished")
        ref = ArtifactRef(
            artifact_id=str(row["artifact_id"]),
            artifact_type=str(row["artifact_type"]),
            artifact_revision=str(row["artifact_revision"]),
            media_type=str(row["media_type"]),
            checksum_sha256=str(row["checksum_sha256"]),
            byte_size=int(row["byte_size"]),
            storage_uri=None,
            payload=payload,
            space_id=None if row["space_id"] is None else str(row["space_id"]),
            dimension=None if row["dimension"] is None else int(row["dimension"]),
        )
        prepared = self.prepare(ref)
        try:
            with catalog.write() as connection:
                current = connection.execute(
                    "SELECT lifecycle_state FROM artifacts WHERE artifact_id = ?", (artifact_id,)
                ).fetchone()
                if current is None or str(current["lifecycle_state"]) != "RECLAIMED":
                    raise CatalogError(ARTIFACT_CONFLICT, "artifact repair state changed")
                self._publish_file(prepared)
                connection.execute(
                    "UPDATE artifacts SET lifecycle_state = 'PUBLISHED', updated_at = ? "
                    "WHERE artifact_id = ?",
                    (now_ms, artifact_id),
                )
            self.discard([prepared])
            return prepared.target
        except BaseException:
            self.discard([prepared], remove_published=True)
            raise

    def prune_orphans(
        self,
        catalog: Any,
        *,
        min_age_ms: int,
        now_ms: int | None = None,
    ) -> list[Path]:
        """Delete only aged final files with no published/reference protection."""
        now = int(time.time_ns() // 1_000_000) if now_ms is None else int(now_ms)
        protected = {
            self.resolve_uri(str(row["storage_uri"]))
            for row in catalog.read_all(
                "SELECT DISTINCT a.storage_uri FROM artifacts a "
                "LEFT JOIN index_outbox o ON o.artifact_id = a.artifact_id AND o.state <> 'CONFIRMED' "
                "LEFT JOIN index_entries e ON e.artifact_id = a.artifact_id "
                "WHERE a.lifecycle_state = 'PUBLISHED' OR o.event_id IS NOT NULL OR e.artifact_id IS NOT NULL"
            )
        }
        removed: list[Path] = []
        if not self.root.is_dir():
            return removed
        for path in self.root.rglob("*"):
            if not path.is_file() or path.name.endswith(TEMP_SUFFIX) or path.resolve() in protected:
                continue
            try:
                if now - int(path.stat().st_mtime * 1000) < int(min_age_ms):
                    continue
                path.unlink()
                removed.append(path)
            except OSError:
                continue
        return removed


def _catalog_filter_revision(connection: sqlite3.Connection) -> int:
    """The authoritative filter revision (0004 singleton), with the P2-03 fallback.

    Before the place core landed, P2-03 read a ``metadata`` key that nothing wrote yet;
    the 0004 migration makes the singleton table authoritative and keeps that key only as
    a compatibility fallback for a catalog that still predates it.
    """
    try:
        row = connection.execute(
            "SELECT revision FROM catalog_filter_revision WHERE revision_id = 1"
        ).fetchone()
    except sqlite3.Error:
        row = None
    if row is not None:
        return max(0, int(row[0] if not isinstance(row, sqlite3.Row) else row["revision"]))
    row = connection.execute(
        "SELECT value FROM metadata WHERE key = 'catalog_filter_revision'"
    ).fetchone()
    if row is None:
        return 0
    try:
        return max(0, int(row[0]))
    except (TypeError, ValueError):
        return 0
