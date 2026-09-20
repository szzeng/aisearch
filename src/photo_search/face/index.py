"""The independent face candidate index and the conservative automatic association.

Two jobs live here, and both are deliberately separated from the authority transaction of
:mod:`photo_search.face.processor`:

**Publishing.** ``FaceIndexCoordinator`` consumes ``face_index_outbox`` and writes the
candidate row into ``face_candidates`` *outside* the authority transaction, then confirms the
entry in SQLite. The crash window between those two steps is therefore real and named
(``failpoint="after_publish"``): a crash leaves a published row and an unconfirmed entry, and
a replay republishes the identical bytes and confirms. Only a confirmed entry is ever used as
a candidate, so "published but unconfirmed" can never silently become a candidate.

**Grouping.** ``reconcile`` turns the faces of one asset into unnamed person groups with the
calibrated thresholds of :mod:`photo_search.face.contract`. It is additive and conservative:

* it never creates a second group for a face that already has an effective link, so a re-run
  of the same content cannot group anything twice;
* it never merges when the best candidate is not clearly better than the best *other* person
  (``NEAR_TIE_MARGIN``), when the similarity is only borderline (``SUGGEST_THRESHOLD``), when
  the face is low quality, or when the target person already appears in the same picture
  (``SAME_IMAGE_CONFLICT``) - each of those becomes a *suggestion* a human has to confirm,
  and a suggestion is never an effective assignment;
* it never creates, changes or deletes anything about a face under human control, and it only
  ever writes ``assignment_kind = 'AUTO'`` rows.

Candidate matching scans the representatives of the active generation (at most
``MAX_REPRESENTATIVES_PER_PERSON`` per person), never every face against every other face.
"""

from __future__ import annotations

import json
import sqlite3
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import (
    ARTIFACT_MISSING,
    INDEX_SUBMISSION_FAILED,
    SPACE_MISMATCH,
    CatalogError,
)
from photo_search.face import contract
from photo_search.face.processor import generation_id_for, queue_candidate

#: Implementation bound for one ``IN (...)`` lookup (mirrors the person/place modules).
_IN_CHUNK = 400


@dataclass(frozen=True)
class Candidate:
    """One published representative: its face, its effective person and its vector."""

    face_id: str
    person_id: int
    asset_id: int
    vector: tuple[float, ...]


@dataclass(frozen=True)
class Decision:
    """What the association decided about one face (also the reconciliation report row)."""

    action: str
    person_id: int | None
    similarity: float | None
    reason: str | None


class _InjectedCrash(RuntimeError):
    pass


def blob_to_vector(blob: bytes, *, dimension: int) -> tuple[float, ...]:
    if len(blob) != 4 * int(dimension):
        raise CatalogError(
            ARTIFACT_MISSING,
            "a stored face vector does not match the pinned dimension",
            details={"dimension": int(dimension), "bytes": len(blob)},
        )
    return tuple(struct.unpack(f"<{int(dimension)}f", blob))


def cosine(first: Sequence[float], second: Sequence[float]) -> float:
    """Both operands are L2-normalised, so the dot product *is* the cosine similarity."""
    total = 0.0
    for left, right in zip(first, second, strict=True):
        total += float(left) * float(right)
    return total


class FaceIndexCoordinator:
    """Publish and confirm the independent face candidate index."""

    def __init__(
        self, catalog: Catalog, *, spec_revision: str = contract.FACE_SPEC_REVISION
    ) -> None:
        self.catalog = catalog
        self.spec_revision = str(spec_revision)
        self.generation_id = generation_id_for(
            contract.FACE_SPACE_ID, contract.FACE_PROCESSOR_ID
        )

    # ------------------------------------------------------------------ generation
    def generation(self) -> Any:
        return self.catalog.read_one(
            "SELECT * FROM face_index_generations WHERE generation_id = ?",
            (self.generation_id,),
        )

    def active_generation(self) -> Any:
        return self.catalog.read_one(
            "SELECT * FROM face_index_generations WHERE space_id = ? AND is_active = 1",
            (contract.FACE_SPACE_ID,),
        )

    # --------------------------------------------------------------------- consume
    def process_next(self, *, now_ms: int, failpoint: str | None = None) -> bool:
        """Publish and confirm one event. ``after_publish`` simulates the crash window."""
        with self.catalog.write() as connection:
            event = connection.execute(
                "SELECT * FROM face_index_outbox WHERE state IN ('PENDING', 'IN_FLIGHT') "
                "ORDER BY created_at, event_id LIMIT 1"
            ).fetchone()
            if event is None:
                return False
            connection.execute(
                "UPDATE face_index_outbox SET state = 'IN_FLIGHT', attempts = attempts + 1, "
                "last_error_code = NULL, updated_at = ? WHERE event_id = ?",
                (int(now_ms), str(event["event_id"])),
            )
            event_id = str(event["event_id"])
        try:
            event = self.catalog.read_one(
                "SELECT * FROM face_index_outbox WHERE event_id = ?", (event_id,)
            )
            if event is None:
                raise CatalogError(INDEX_SUBMISSION_FAILED, "the face outbox event disappeared")
            self._validate_event(event)
            face = self._publish(event, now_ms=int(now_ms))
            if failpoint == "after_publish":
                raise _InjectedCrash("after_publish")
            with self.catalog.write() as connection:
                current = connection.execute(
                    "SELECT state FROM face_index_outbox WHERE event_id = ?", (event_id,)
                ).fetchone()
                if current is None or str(current["state"]) != "IN_FLIGHT":
                    raise CatalogError(
                        INDEX_SUBMISSION_FAILED,
                        "the face outbox claim was lost before confirmation",
                    )
                entry = connection.execute(
                    "SELECT artifact_id FROM face_index_entries WHERE generation_id = ? "
                    "AND face_id = ?",
                    (str(event["generation_id"]), str(event["face_id"])),
                ).fetchone()
                if entry is not None and str(entry["artifact_id"]) == str(face["artifact_id"]):
                    connection.execute(
                        "UPDATE face_index_entries SET confirmation_revision = ?, "
                        "confirmed_at = ?, state = 'CONFIRMED' WHERE generation_id = ? "
                        "AND face_id = ?",
                        (
                            int(event["projection_revision"]),
                            int(now_ms),
                            str(event["generation_id"]),
                            str(event["face_id"]),
                        ),
                    )
                connection.execute(
                    "UPDATE face_index_outbox SET state = 'CONFIRMED', updated_at = ? "
                    "WHERE event_id = ?",
                    (int(now_ms), event_id),
                )
                self._make_generation_ready(connection, now_ms=int(now_ms))
            return True
        except _InjectedCrash:
            raise
        except Exception as error:
            with self.catalog.write() as connection:
                connection.execute(
                    "UPDATE face_index_outbox SET state = 'PENDING', last_error_code = ?, "
                    "updated_at = ? WHERE event_id = ? AND state = 'IN_FLIGHT'",
                    (getattr(error, "code", INDEX_SUBMISSION_FAILED), int(now_ms), event_id),
                )
            if isinstance(error, CatalogError):
                raise
            raise CatalogError(
                INDEX_SUBMISSION_FAILED,
                "face candidate submission failed",
                details={"reason": type(error).__name__},
            ) from error

    def process_all(self, *, now_ms: int, limit: int = 100_000) -> int:
        count = 0
        while count < int(limit) and self.process_next(now_ms=int(now_ms)):
            count += 1
        return count

    # ------------------------------------------------------------------- internals
    @staticmethod
    def _validate_event(event: Any) -> None:
        import hashlib

        payload_json = str(event["payload_json"])
        if hashlib.sha256(payload_json.encode("utf-8")).hexdigest() != str(
            event["payload_checksum"]
        ):
            raise CatalogError(INDEX_SUBMISSION_FAILED, "face outbox payload checksum is invalid")
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as error:
            raise CatalogError(
                INDEX_SUBMISSION_FAILED, "face outbox payload JSON is invalid"
            ) from error
        expected = {
            "face_id": str(event["face_id"]),
            "generation_id": str(event["generation_id"]),
            "projection_revision": int(event["projection_revision"]),
            "space_id": str(event["space_id"]),
        }
        if payload != expected:
            raise CatalogError(
                INDEX_SUBMISSION_FAILED, "face outbox payload does not match its columns"
            )
        if str(event["space_id"]) != contract.FACE_SPACE_ID:
            raise CatalogError(
                SPACE_MISMATCH, "the face outbox event belongs to a foreign face space"
            )

    def _publish(self, event: Any, *, now_ms: int) -> dict[str, Any]:
        """Write one candidate row outside the authority transaction."""
        face = self.catalog.read_one(
            "SELECT * FROM faces WHERE face_id = ?", (str(event["face_id"]),)
        )
        if face is None:
            raise CatalogError(ARTIFACT_MISSING, "the face of this candidate event does not exist")
        if str(face["spec_revision"]) != self.spec_revision:
            raise CatalogError(
                INDEX_SUBMISSION_FAILED,
                "the face of this candidate event belongs to another detection revision",
            )
        embedding = self.catalog.read_one(
            "SELECT * FROM face_embeddings WHERE face_id = ? AND processor_id = ?",
            (str(event["face_id"]), contract.FACE_PROCESSOR_ID),
        )
        if embedding is None:
            raise CatalogError(ARTIFACT_MISSING, "the face has no canonical vector")
        if str(embedding["space_id"]) != contract.FACE_SPACE_ID:
            raise CatalogError(
                SPACE_MISMATCH, "the canonical face vector belongs to a foreign space"
            )
        blob = bytes(embedding["vector_blob"])
        if int(embedding["dimension"]) != contract.FACE_DIMENSION:
            raise CatalogError(SPACE_MISMATCH, "the canonical face vector has another dimension")
        representative = 1 if self._is_representative(face) else 0
        with self.catalog.write() as connection:
            connection.execute(
                "INSERT INTO face_candidates(generation_id, face_id, is_representative, "
                "quality_state, vector_blob, vector_sha256, published_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(generation_id, face_id) DO UPDATE SET "
                "is_representative = excluded.is_representative, "
                "quality_state = excluded.quality_state, vector_blob = excluded.vector_blob, "
                "vector_sha256 = excluded.vector_sha256, published_at = excluded.published_at",
                (
                    str(event["generation_id"]),
                    str(event["face_id"]),
                    int(representative),
                    str(face["quality_state"]),
                    blob,
                    str(embedding["vector_sha256"]),
                    int(now_ms),
                ),
            )
        return {"artifact_id": str(face["artifact_id"]), "representative": representative}

    def _is_representative(self, face: Any) -> bool:
        """A quality-ok, effectively linked face inside the per-person representative cap."""
        if str(face["quality_state"]) != "OK":
            return False
        link = self.catalog.read_one(
            "SELECT person_id FROM face_person_links WHERE face_id = ? AND decision = 'CONFIRMED'",
            (str(face["face_id"]),),
        )
        if link is None:
            return False
        locked, excluded = blocked_face_links(self.catalog.connection)
        if str(face["face_id"]) in locked or (
            str(face["face_id"]),
            int(link["person_id"]),
        ) in excluded:
            # Human knowledge wins over the representative ranking (ADR-0006 §6).
            return False
        ranked = self.catalog.read_all(
            "SELECT f.face_id FROM face_person_links AS l "
            "JOIN faces AS f ON f.face_id = l.face_id "
            "WHERE l.person_id = ? AND l.decision = 'CONFIRMED' AND f.quality_state = 'OK' "
            "AND f.spec_revision = ? ORDER BY f.face_pixels DESC, f.face_id ASC",
            (int(link["person_id"]), self.spec_revision),
        )
        for index, row in enumerate(ranked):
            if str(row["face_id"]) == str(face["face_id"]):
                return index < int(contract.MAX_REPRESENTATIVES_PER_PERSON)
        return False

    def _make_generation_ready(self, connection: sqlite3.Connection, *, now_ms: int) -> None:
        pending = connection.execute(
            "SELECT COUNT(*) FROM face_index_outbox WHERE generation_id = ? "
            "AND state <> 'CONFIRMED'",
            (self.generation_id,),
        ).fetchone()[0]
        if int(pending):
            return
        connection.execute(
            "UPDATE face_index_generations SET state = 'READY', updated_at = ? "
            "WHERE generation_id = ? AND state = 'PENDING'",
            (int(now_ms), self.generation_id),
        )
        row = connection.execute(
            "SELECT space_id, state FROM face_index_generations WHERE generation_id = ?",
            (self.generation_id,),
        ).fetchone()
        if row is None or str(row["state"]) != "READY":
            return
        active = connection.execute(
            "SELECT generation_id FROM face_index_generations WHERE space_id = ? "
            "AND is_active = 1",
            (row["space_id"],),
        ).fetchone()
        if active is None:
            connection.execute(
                "UPDATE face_index_generations SET is_active = 1, updated_at = ? "
                "WHERE generation_id = ?",
                (int(now_ms), self.generation_id),
            )

    # ------------------------------------------------------------------ candidates
    def candidates(self) -> list[Candidate]:
        """Every representative of the active generation, with its effective person."""
        generation = self.active_generation()
        if generation is None:
            return []
        rows = self.catalog.read_all(
            "SELECT c.face_id AS face_id, l.person_id AS person_id, f.asset_id AS asset_id, "
            "c.vector_blob AS vector_blob FROM face_candidates AS c "
            "JOIN face_person_links AS l ON l.face_id = c.face_id AND l.decision = 'CONFIRMED' "
            "JOIN faces AS f ON f.face_id = c.face_id "
            "WHERE c.generation_id = ? AND c.is_representative = 1 ORDER BY c.face_id",
            (str(generation["generation_id"]),),
        )
        # A human lock or an active human denial is read here too, so a candidate scan can
        # never offer a face the automatic path is forbidden to touch (ADR-0006 §6).
        locked, excluded = blocked_face_links(self.catalog.connection)
        return [
            Candidate(
                face_id=str(row["face_id"]),
                person_id=int(row["person_id"]),
                asset_id=int(row["asset_id"]),
                vector=blob_to_vector(
                    bytes(row["vector_blob"]), dimension=contract.FACE_DIMENSION
                ),
            )
            for row in rows
            if str(row["face_id"]) not in locked
            and (str(row["face_id"]), int(row["person_id"])) not in excluded
        ]

    def index_state(self) -> dict[str, Any]:
        """Read-only counts of the face index (no vector, no coordinate, no path)."""
        generation = self.generation()

        def counts(table: str, column: str) -> dict[str, int]:
            return {
                str(row["state"]): int(row["count"])
                for row in self.catalog.read_all(
                    f"SELECT {column} AS state, COUNT(*) AS count FROM {table} GROUP BY {column}"
                )
            }

        return {
            "generation_id": None if generation is None else str(generation["generation_id"]),
            "state": None if generation is None else str(generation["state"]),
            "is_active": bool(generation is not None and int(generation["is_active"])),
            "dimension": None if generation is None else int(generation["dimension"]),
            "space_id": None if generation is None else str(generation["space_id"]),
            "entries": counts("face_index_entries", "state"),
            "outbox": counts("face_index_outbox", "state"),
            "candidates": int(
                self.catalog.read_one("SELECT COUNT(*) AS total FROM face_candidates")["total"]
            ),
            "representatives": int(
                self.catalog.read_one(
                    "SELECT COUNT(*) AS total FROM face_candidates WHERE is_representative = 1"
                )["total"]
            ),
        }


def _effective_link(connection: sqlite3.Connection, face_id: str) -> Any:
    return connection.execute(
        "SELECT * FROM face_person_links WHERE face_id = ? AND decision = 'CONFIRMED'",
        (str(face_id),),
    ).fetchone()


def human_control_tables(connection: sqlite3.Connection) -> set[str]:
    """The human-control tables that exist in *this* catalog (P3-03's ``0011``)."""
    names = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    return {name for name in ("face_assignment_controls", "face_exclusions") if name in names}


def blocked_face_links(connection: sqlite3.Connection) -> tuple[set[str], set[tuple[str, int]]]:
    """The face links a human decision already refuses (ADR-0006 §6, ADR-0005 §5 in spirit).

    A ``MANUAL_UNASSIGNED`` face must not come back through a merge, and an effective link
    that an active exclusion forbids must not be offered as a representative either. Those
    are the two facts P3-02's ``human_controlled_face_ids`` guards at *write* time; this is
    the same rule at *read* time, so a candidate scan can never resurrect them.
    """
    tables = human_control_tables(connection)
    locked: set[str] = set()
    excluded: set[tuple[str, int]] = set()
    if "face_assignment_controls" in tables:
        locked = {
            str(row["face_id"])
            for row in connection.execute(
                "SELECT face_id FROM face_assignment_controls "
                "WHERE mode = 'MANUAL_UNASSIGNED'"
            ).fetchall()
        }
    if "face_exclusions" in tables:
        excluded = {
            (str(row["face_id"]), int(row["person_id"]))
            for row in connection.execute(
                "SELECT face_id, person_id FROM face_exclusions WHERE active = 1"
            ).fetchall()
        }
    return locked, excluded


def _suggest(
    connection: sqlite3.Connection,
    *,
    face_id: str,
    person_id: int,
    similarity: float,
    reason: str,
    now_ms: int,
) -> None:
    connection.execute(
        "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
        "confidence, reason, revision, last_operation_id, created_at, updated_at) "
        "VALUES (?, ?, 'AUTO', 'SUGGESTED', ?, ?, 1, NULL, ?, ?) "
        "ON CONFLICT(face_id, person_id, assignment_kind) DO UPDATE SET "
        "confidence = excluded.confidence, reason = excluded.reason, "
        "revision = face_person_links.revision + 1, updated_at = excluded.updated_at",
        (str(face_id), int(person_id), float(similarity), str(reason), int(now_ms), int(now_ms)),
    )


def _confirm(
    connection: sqlite3.Connection,
    *,
    face_id: str,
    person_id: int,
    confidence: float,
    now_ms: int,
) -> None:
    connection.execute(
        "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
        "confidence, reason, revision, last_operation_id, created_at, updated_at) "
        "VALUES (?, ?, 'AUTO', 'CONFIRMED', ?, NULL, 1, NULL, ?, ?) "
        "ON CONFLICT(face_id, person_id, assignment_kind) DO UPDATE SET "
        "decision = 'CONFIRMED', confidence = excluded.confidence, reason = NULL, "
        "revision = face_person_links.revision + 1, updated_at = excluded.updated_at",
        (str(face_id), int(person_id), float(confidence), int(now_ms), int(now_ms)),
    )
    connection.execute(
        "DELETE FROM face_person_links WHERE face_id = ? AND person_id <> ? "
        "AND decision = 'SUGGESTED' AND assignment_kind = 'AUTO'",
        (str(face_id), int(person_id)),
    )


def _new_person(connection: sqlite3.Connection, *, now_ms: int) -> int:
    cursor = connection.execute(
        "INSERT INTO persons(name, name_key, avatar_face_id, merged_into, revision, "
        "created_at, updated_at) VALUES (NULL, NULL, NULL, NULL, 1, ?, ?)",
        (int(now_ms), int(now_ms)),
    )
    return int(cursor.lastrowid)


def queued_assets(
    catalog: Catalog, *, spec_revision: str = contract.FACE_SPEC_REVISION
) -> list[Any]:
    return catalog.read_all(
        "SELECT * FROM face_association_queue WHERE spec_revision = ? "
        "ORDER BY queued_at, asset_id",
        (str(spec_revision),),
    )


def asset_faces(
    connection: sqlite3.Connection, *, asset_id: int, spec_revision: str
) -> list[Any]:
    return list(
        connection.execute(
            "SELECT f.*, e.vector_blob AS vector_blob, "
            "(SELECT 1 FROM face_person_links AS l WHERE l.face_id = f.face_id "
            " AND l.decision = 'CONFIRMED') AS effective "
            "FROM faces AS f LEFT JOIN face_embeddings AS e ON e.face_id = f.face_id "
            "AND e.processor_id = ? WHERE f.asset_id = ? AND f.spec_revision = ? "
            "ORDER BY f.ordinal, f.face_id",
            (contract.FACE_PROCESSOR_ID, int(asset_id), str(spec_revision)),
        )
    )


def decide_for_face(
    *,
    quality_state: str,
    vector: Sequence[float],
    candidates: Sequence[Candidate],
    person_in_asset: set[int],
    in_asset: Sequence[tuple[int, Sequence[float]]] = (),
) -> Decision:
    """The pure decision function of one face, so the policy is directly testable.

    Similarities are collapsed per *person* first: the runner-up that matters is the best
    score of a different person, never a second face of the same one.

    ``in_asset`` is the faces of the *same* picture that already received a person in this
    pass. Two nearly identical faces in one photo are the mirror/composite case of
    architecture §8.3: the second one is recorded as a conflict suggestion for the first one's
    person instead of being silently merged into it. A low-quality face never takes that path
    either - it is only ever suggested, never a confirmed member.
    """
    if quality_state == "OK":
        for other_person, other_vector in in_asset:
            score = cosine(vector, other_vector)
            if score >= contract.MERGE_THRESHOLD:
                return Decision(
                    "SUGGESTED",
                    int(other_person),
                    score,
                    contract.REASON_SAME_IMAGE_CONFLICT,
                )
    best_by_person: dict[int, float] = {}
    for candidate in candidates:
        score = cosine(vector, candidate.vector)
        if score > best_by_person.get(candidate.person_id, -2.0):
            best_by_person[candidate.person_id] = score
    if not best_by_person:
        if quality_state != "OK":
            return Decision("LOW_QUALITY_UNASSIGNED", None, None, None)
        return Decision("NEW_GROUP", None, None, None)
    ranked = sorted(best_by_person.items(), key=lambda item: (-item[1], item[0]))
    person_id, similarity = int(ranked[0][0]), float(ranked[0][1])
    runner_up = float(ranked[1][1]) if len(ranked) > 1 else None
    if quality_state != "OK":
        if similarity >= contract.SUGGEST_THRESHOLD:
            return Decision("SUGGESTED", person_id, similarity, contract.REASON_LOW_QUALITY)
        return Decision("LOW_QUALITY_UNASSIGNED", None, similarity, None)
    if similarity < contract.SUGGEST_THRESHOLD:
        return Decision("NEW_GROUP", None, similarity, None)
    if similarity < contract.MERGE_THRESHOLD:
        return Decision("SUGGESTED", person_id, similarity, contract.REASON_BORDERLINE)
    if runner_up is not None and similarity - runner_up < contract.NEAR_TIE_MARGIN:
        return Decision("SUGGESTED", person_id, similarity, contract.REASON_NEAR_TIE)
    if person_id in person_in_asset:
        return Decision(
            "SUGGESTED", person_id, similarity, contract.REASON_SAME_IMAGE_CONFLICT
        )
    return Decision("MERGE", person_id, similarity, None)


def reconcile(
    catalog: Catalog,
    *,
    coordinator: FaceIndexCoordinator | None = None,
    limit: int = 100_000,
    spec_revision: str = contract.FACE_SPEC_REVISION,
) -> dict[str, Any]:
    """Group every queued asset's faces conservatively, republishing as it goes.

    An asset is only processed once every one of its faces is *confirmed* in the active
    candidate index: an incomplete candidate set would turn "the index had not caught up
    yet" into a brand-new person group, which is exactly the silent wrong answer this task
    must not produce.
    """
    index = coordinator or FaceIndexCoordinator(catalog, spec_revision=str(spec_revision))
    decisions: list[dict[str, Any]] = []
    processed = 0
    deferred = 0
    for row in queued_assets(catalog, spec_revision=str(spec_revision)):
        if processed >= int(limit):
            break
        asset_id = int(row["asset_id"])
        if index.active_generation() is None or not _index_complete(
            catalog, index, asset_id=asset_id
        ):
            deferred += 1
            continue
        outcome = _reconcile_asset(
            catalog, index=index, asset_id=asset_id, spec_revision=str(spec_revision)
        )
        decisions.extend(outcome)
        processed += 1
        if outcome:
            # Republish now, so the *next* asset already sees these groups as candidates.
            index.process_all(now_ms=catalog.now_ms())
    return {
        "processed": processed,
        "deferred": deferred,
        "decisions": decisions,
        "new_groups": sum(1 for item in decisions if item["action"] == "NEW_GROUP"),
        "merged": sum(1 for item in decisions if item["action"] == "MERGE"),
        "suggested": sum(1 for item in decisions if item["action"] == "SUGGESTED"),
        "unassigned": sum(
            1 for item in decisions if item["action"] == "LOW_QUALITY_UNASSIGNED"
        ),
        "human_controlled": sum(
            1 for item in decisions if item["action"] == "HUMAN_CONTROLLED"
        ),
        # ADR-0007 §4: the automatic pass refused these faces because their logical control
        # revision moved after the pass read it. The count is honest about a refusal that
        # wrote nothing, instead of reporting the face as freshly grouped.
        "late_control_conflicts": sum(
            1 for item in decisions if item["action"] == "LATE_CONTROL_CONFLICT"
        ),
    }


def _index_complete(catalog: Catalog, index: FaceIndexCoordinator, *, asset_id: int) -> bool:
    row = catalog.read_one(
        "SELECT COUNT(*) AS total FROM faces AS f WHERE f.asset_id = ? AND f.spec_revision = ? "
        "AND NOT EXISTS (SELECT 1 FROM face_index_entries AS e WHERE e.face_id = f.face_id "
        " AND e.generation_id = ? AND e.state = 'CONFIRMED')",
        (int(asset_id), index.spec_revision, index.generation_id),
    )
    return int(row["total"]) == 0


def _control_revision_gate(
    connection: sqlite3.Connection,
    observed: Mapping[str, int],
    face_ids: Sequence[str],
) -> set[str]:
    """The faces whose logical control revision moved since this pass observed it.

    ADR-0007 §4: an automatic result that was computed against an older logical revision is
    a *stable conflict*, not a licence to overwrite the newer human fact. Comparing inside
    the authority transaction is what makes the check true rather than merely likely, and
    the caller skips exactly these faces - every other face of the photo is still grouped
    normally, because one human decision never widens to its neighbours.
    """
    if not observed:
        return set()
    from photo_search.persons import face_correction

    current = face_correction.logical_control_revisions(connection, list(face_ids))
    return {
        str(face_id)
        for face_id, revision in observed.items()
        if int(current.get(str(face_id), 0)) != int(revision)
    }


def _active_exclusion_persons(
    connection: sqlite3.Connection, face_ids: Sequence[str]
) -> dict[str, set[int]]:
    """Per face, the person ids its active human denials name (still possibly unmerged)."""
    excluded: dict[str, set[int]] = {}
    unique = sorted({str(face_id) for face_id in face_ids})
    for index in range(0, len(unique), _IN_CHUNK):
        chunk = unique[index : index + _IN_CHUNK]
        if not chunk:
            continue
        placeholders = ", ".join("?" for _ in chunk)
        rows = connection.execute(
            f"SELECT face_id, person_id FROM face_exclusions WHERE active = 1 "
            f"AND face_id IN ({placeholders})",
            tuple(chunk),
        ).fetchall()
        for row in rows:
            excluded.setdefault(str(row["face_id"]), set()).add(int(row["person_id"]))
    return excluded


def _manually_locked_face_ids(
    connection: sqlite3.Connection, *, asset_id: int
) -> set[str]:
    """Faces whose MANUAL mode forbids every automatic assignment.

    An active exclusion alone is deliberately not a whole-face lock: ADR-0007 §3/§4
    says an ``AUTO_ALLOWED`` face may still be grouped, but never into an excluded canonical
    person. The processor's broader ``human_controlled_face_ids()`` remains correct for
    preserving old detection anchors during re-detection; association needs this narrower
    mode-only view.
    """
    return {
        str(row["face_id"])
        for row in connection.execute(
            "SELECT c.face_id FROM face_assignment_controls AS c "
            "JOIN faces AS f ON f.face_id = c.face_id WHERE f.asset_id = ? "
            "AND c.mode IN ('MANUAL_ASSIGNED', 'MANUAL_UNASSIGNED')",
            (int(asset_id),),
        ).fetchall()
    }


def _canonical_person_map(
    connection: sqlite3.Connection, person_ids: Sequence[int]
) -> dict[int, int]:
    """Map every named person id to its active person, following P3-03 merges once."""
    from photo_search.persons.face_correction import canonical_person

    mapping: dict[int, int] = {}
    for person_id in sorted({int(value) for value in person_ids}):
        try:
            mapping[int(person_id)] = canonical_person(connection, int(person_id))
        except CatalogError:
            # A dangling id cannot be resolved; treating it as its own person keeps the
            # comparison total instead of turning a corrupt row into a crash.
            mapping[int(person_id)] = int(person_id)
    return mapping


def reconcile_asset(
    catalog: Catalog,
    *,
    asset_id: int,
    coordinator: FaceIndexCoordinator | None = None,
    spec_revision: str = contract.FACE_SPEC_REVISION,
    observed_controls: Mapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Group one already-queued asset's faces, with the ADR-0007 §4 control gate.

    ``observed_controls`` is the logical revision this pass saw when it claimed the asset;
    ``None`` means "read it now". Supplying an older mapping is how a late result is
    reproduced without a second process.
    """
    index = coordinator or FaceIndexCoordinator(catalog, spec_revision=str(spec_revision))
    return _reconcile_asset(
        catalog,
        index=index,
        asset_id=int(asset_id),
        spec_revision=str(spec_revision),
        observed_controls=observed_controls,
    )


def _reconcile_asset(
    catalog: Catalog,
    *,
    index: FaceIndexCoordinator,
    asset_id: int,
    spec_revision: str,
    observed_controls: Mapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    from photo_search.persons import face_correction

    candidates = index.candidates()
    faces = asset_faces(catalog.connection, asset_id=asset_id, spec_revision=str(spec_revision))
    face_ids = [str(face["face_id"]) for face in faces]
    # ADR-0007 §4: the logical control revision that was observed when this pass claimed the
    # asset. Re-reading it inside the authority transaction is what proves an automatic
    # result is not overwriting a human decision that landed in the meantime.
    observed = (
        face_correction.logical_control_revisions(catalog.connection, face_ids)
        if observed_controls is None
        else {str(key): int(value) for key, value in observed_controls.items()}
    )
    decisions: list[dict[str, Any]] = []
    changed: list[str] = []
    #: Faces of this picture that already received a person *in this pass*: the mirror and
    #: composite check compares against them, which is the only way to notice two faces of one
    #: person inside a single photo.
    assigned_in_asset: list[tuple[int, tuple[float, ...]]] = []
    with catalog.write() as connection:
        # Both human facts are read again *inside* the write transaction: the control and
        # denial tables are the authority, and the candidate set above was only a snapshot.
        late = _control_revision_gate(connection, observed, face_ids)
        protected = _manually_locked_face_ids(connection, asset_id=asset_id)
        excluded_by_face = _active_exclusion_persons(connection, face_ids)
        canonical_of_person = _canonical_person_map(
            connection,
            [int(candidate.person_id) for candidate in candidates]
            + [
                int(person)
                for persons in excluded_by_face.values()
                for person in persons
            ],
        )
        person_in_asset = {
            int(row["person_id"])
            for row in connection.execute(
                "SELECT DISTINCT l.person_id FROM face_person_links AS l "
                "JOIN faces AS f ON f.face_id = l.face_id "
                "WHERE f.asset_id = ? AND l.decision = 'CONFIRMED'",
                (int(asset_id),),
            ).fetchall()
        }
        for face in faces:
            face_id = str(face["face_id"])
            if face_id in late:
                # A human decision moved this face's logical revision after this pass read
                # it: the computed result is stale, so nothing about the face is written.
                decisions.append(
                    {
                        "face_id": face_id,
                        "action": "LATE_CONTROL_CONFLICT",
                        "person_id": None,
                        "similarity": None,
                        "reason": None,
                    }
                )
                continue
            if face_id in protected:
                # Human knowledge wins: the automatic path does not touch this face at all.
                decisions.append(
                    {
                        "face_id": face_id,
                        "action": "HUMAN_CONTROLLED",
                        "person_id": None,
                        "similarity": None,
                        "reason": None,
                    }
                )
                continue
            if face["effective"] is not None:
                # Already grouped (a re-run of the same content): never a second group.
                continue
            blob = face["vector_blob"]
            if blob is None:
                continue
            vector = blob_to_vector(bytes(blob), dimension=contract.FACE_DIMENSION)
            # ADR-0007 §4: an AUTO_ALLOWED face still refuses the persons a human denied
            # for it - including a person the denial was recorded against before a merge.
            denied = {
                canonical_of_person.get(person, int(person))
                for person in excluded_by_face.get(face_id, ())
            }
            usable = [
                candidate
                for candidate in candidates
                if canonical_of_person.get(int(candidate.person_id), int(candidate.person_id))
                not in denied
            ]
            decision = decide_for_face(
                quality_state=str(face["quality_state"]),
                vector=vector,
                candidates=usable,
                person_in_asset=person_in_asset,
                in_asset=assigned_in_asset,
            )
            now_ms = catalog.now_ms()
            if decision.action == "NEW_GROUP":
                person_id = _new_person(connection, now_ms=now_ms)
                _confirm(
                    connection,
                    face_id=face_id,
                    person_id=person_id,
                    confidence=0.0,
                    now_ms=now_ms,
                )
                person_in_asset.add(int(person_id))
                assigned_in_asset.append((int(person_id), vector))
                decisions.append(
                    {
                        "face_id": face_id,
                        "action": "NEW_GROUP",
                        "person_id": person_id,
                        "similarity": decision.similarity,
                        "reason": None,
                    }
                )
                changed.append(face_id)
            elif decision.action == "MERGE":
                person_id = int(decision.person_id or 0)
                _confirm(
                    connection,
                    face_id=face_id,
                    person_id=person_id,
                    confidence=float(decision.similarity or 0.0),
                    now_ms=now_ms,
                )
                person_in_asset.add(person_id)
                assigned_in_asset.append((person_id, vector))
                decisions.append(
                    {
                        "face_id": face_id,
                        "action": "MERGE",
                        "person_id": person_id,
                        "similarity": decision.similarity,
                        "reason": None,
                    }
                )
                changed.append(face_id)
            elif decision.action == "SUGGESTED":
                person_id = int(decision.person_id or 0)
                _suggest(
                    connection,
                    face_id=face_id,
                    person_id=person_id,
                    similarity=float(decision.similarity or 0.0),
                    reason=str(decision.reason),
                    now_ms=now_ms,
                )
                decisions.append(
                    {
                        "face_id": face_id,
                        "action": "SUGGESTED",
                        "person_id": person_id,
                        "similarity": decision.similarity,
                        "reason": decision.reason,
                    }
                )
            else:
                decisions.append(
                    {
                        "face_id": face_id,
                        "action": decision.action,
                        "person_id": None,
                        "similarity": decision.similarity,
                        "reason": None,
                    }
                )
        connection.execute(
            "DELETE FROM face_association_queue WHERE asset_id = ? AND spec_revision = ?",
            (int(asset_id), str(spec_revision)),
        )
        # An assignment change changes which faces are representatives, so the affected
        # candidate rows are downgraded to PENDING_REPAIR and republished by the caller - the
        # same "human knowledge first, machine product catches up" semantics P2-03A
        # established for the image projection.
        for face_id in changed:
            row = connection.execute(
                "SELECT artifact_id FROM faces WHERE face_id = ?", (face_id,)
            ).fetchone()
            if row is None:
                continue
            queue_candidate(
                connection,
                face_id=face_id,
                generation_id=index.generation_id,
                artifact_id=str(row["artifact_id"]),
                now_ms=catalog.now_ms(),
            )
    return decisions
