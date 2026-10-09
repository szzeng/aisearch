"""Read-only scheduler/runtime summaries for ``GET /api/v1/capabilities``.

The projection opens the catalog strictly read-only, so a build that cannot use the
database (too new, inconsistent) still reports facts without any write side effect, and a
database that predates the P2-02 migration simply reports "unavailable".
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from photo_search.catalog import schema
from photo_search.catalog.errors import CatalogError
from photo_search.catalog.sqlite_io import open_readonly
from photo_search.face import contract as face_contract
from photo_search.ocr import contract as ocr_contract
from photo_search.persons import contracts as person_contract
from photo_search.persons.identity import resolve_canonical_person_id


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return set(schema.business_tables(connection))


def scheduler_summary(db_path: str | Path) -> dict[str, Any] | None:
    """Pause policy and the current capability health, or ``None`` when not yet present."""
    path = Path(db_path)
    if not path.exists():
        return None
    connection = open_readonly(path)
    try:
        tables = _table_names(connection)
        summary: dict[str, Any] = {
            "scheduler": None,
            "runtime": None,
            "qualification_evidence": 0,
        }
        if "scheduler_state" in tables:
            row = connection.execute(
                "SELECT paused, revision, aging_window_ms, updated_at FROM scheduler_state "
                "WHERE state_id = 1"
            ).fetchone()
            if row is not None:
                summary["scheduler"] = {
                    "state": "paused" if int(row["paused"]) else "running",
                    "revision": int(row["revision"]),
                    "aging_window_ms": int(row["aging_window_ms"]),
                    "updated_at": int(row["updated_at"]),
                }
        if "runtime_capabilities" in tables:
            row = connection.execute(
                "SELECT capability_revision, runtime_package_id, runtime_backend, health_status, "
                "health_checked_at, measured_at FROM runtime_capabilities "
                "ORDER BY recorded_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                summary["runtime"] = {
                    "capability_revision": str(row["capability_revision"]),
                    "runtime_package_id": str(row["runtime_package_id"]),
                    "runtime_backend": str(row["runtime_backend"]),
                    "health_status": str(row["health_status"]),
                    "health_checked_at": int(row["health_checked_at"]),
                    "measured_at": int(row["measured_at"]),
                }
        if "runtime_qualification_evidence" in tables:
            summary["qualification_evidence"] = int(
                connection.execute(
                    "SELECT COUNT(*) FROM runtime_qualification_evidence"
                ).fetchone()[0]
            )
        if summary["scheduler"] is None and summary["runtime"] is None:
            return None
        return summary
    finally:
        connection.close()


def index_summary(db_path: str | Path) -> dict[str, Any] | None:
    """Safe artifact/outbox/index counts; never returns paths, payloads or checksums."""
    path = Path(db_path)
    if not path.exists():
        return None
    connection = open_readonly(path)
    try:
        tables = _table_names(connection)
        required = {"artifacts", "index_outbox", "index_entries", "index_generations"}
        if not required <= tables:
            return None

        def counts(table: str, column: str) -> dict[str, int]:
            return {
                str(row["state"]): int(row["count"])
                for row in connection.execute(
                    f"SELECT {column} AS state, COUNT(*) AS count FROM {table} GROUP BY {column}"
                )
            }

        generations = counts("index_generations", "state")
        active = [
            {
                "generation_id": str(row["generation_id"]),
                "space_id": str(row["space_id"]),
                "state": str(row["state"]),
            }
            for row in connection.execute(
                "SELECT generation_id, space_id, state FROM index_generations "
                "WHERE is_active = 1 ORDER BY generation_id"
            )
        ]
        return {
            "artifacts": counts("artifacts", "lifecycle_state"),
            "outbox": counts("index_outbox", "state"),
            "entries": counts("index_entries", "state"),
            "generations": generations,
            "active_generations": active,
            "semantic_ready": int(
                connection.execute(
                    "SELECT COUNT(*) FROM index_entries WHERE state = 'CONFIRMED'"
                ).fetchone()[0]
            ),
            "pending": int(
                connection.execute(
                    "SELECT COUNT(*) FROM index_outbox WHERE state <> 'CONFIRMED'"
                ).fetchone()[0]
            ),
            "confirmed": int(
                connection.execute(
                    "SELECT COUNT(*) FROM index_outbox WHERE state = 'CONFIRMED'"
                ).fetchone()[0]
            ),
            "pending_repair": int(
                connection.execute(
                    "SELECT COUNT(*) FROM index_entries WHERE state = 'PENDING_REPAIR'"
                ).fetchone()[0]
            ),
        }
    finally:
        connection.close()


def search_projection_summary(db_path: str | Path) -> dict[str, Any] | None:
    """Counts of the derived P2-04 search layer, and the lag a caller can see.

    Read-only and text-free: no search text, no place name and no path is returned - only
    how many documents exist, how many assets are still queued for re-derivation and which
    tokenizer generation wrote them. ``None`` means this catalog predates migration 0006.
    """
    path = Path(db_path)
    if not path.exists():
        return None
    connection = open_readonly(path)
    try:
        tables = _table_names(connection)
        if "search_documents" not in tables or "search_projection_queue" not in tables:
            return None
        row = connection.execute(
            "SELECT COUNT(*) AS documents, "
            "COUNT(DISTINCT tokenizer_version || ':' || fts_generation_id) AS generations "
            "FROM search_documents"
        ).fetchone()
        queued = int(
            connection.execute("SELECT COUNT(*) FROM search_projection_queue").fetchone()[0]
        )
        generation = connection.execute(
            "SELECT tokenizer_version, fts_generation_id FROM search_documents "
            "ORDER BY updated_at DESC, asset_id DESC LIMIT 1"
        ).fetchone()
        return {
            "documents": int(row["documents"]),
            "queued_assets": queued,
            "generations": int(row["generations"]),
            "tokenizer_version": (
                None if generation is None else int(generation["tokenizer_version"])
            ),
            "fts_generation_id": None if generation is None else str(generation["fts_generation_id"]),
            "state": "SYNCED" if queued == 0 else "LAGGING",
        }
    finally:
        connection.close()


def ocr_summary(
    db_path: str | Path, *, weights_present: bool | None = None
) -> dict[str, Any] | None:
    """Truthful OCR readiness of this catalog: covered, stale, queued, blocked.

    Read-only and text-free: no recognised text, no coordinate and no path leaves this
    function - only how many assets carry a current OCR document, how many carry a document
    for an older content revision (the honest "OCR is still catching up" signal, never
    "there is no such text in this image"), and how the OCR jobs are doing.
    ``weights_present`` is the cheap file-level fact the caller may add (the API can look at
    the model root without importing a runtime); ``None`` means it was not checked.
    ``None`` as a whole means this catalog predates migration 0009.
    """
    path = Path(db_path)
    if not path.exists():
        return None
    connection = open_readonly(path)
    try:
        tables = _table_names(connection)
        if "asset_ocr_text" not in tables:
            return None
        documents = int(
            connection.execute(
                "SELECT COUNT(*) AS documents FROM asset_ocr_text AS t "
                "JOIN assets AS a ON a.asset_id = t.asset_id "
                "WHERE t.input_revision = a.sha256"
            ).fetchone()["documents"]
        )
        stored = int(
            connection.execute("SELECT COUNT(*) AS total FROM asset_ocr_text").fetchone()["total"]
        )
        regions = int(
            connection.execute("SELECT COUNT(*) AS total FROM asset_ocr_regions").fetchone()[
                "total"
            ]
        )
        low = int(
            connection.execute(
                "SELECT COUNT(*) AS total FROM asset_ocr_regions WHERE confidence <= ?",
                (float(ocr_contract.LOW_CONFIDENCE_THRESHOLD),),
            ).fetchone()["total"]
        )
        jobs = {
            str(row["state"]): int(row["count"])
            for row in connection.execute(
                "SELECT state, COUNT(*) AS count FROM jobs WHERE task_type = ? GROUP BY state",
                (str(ocr_contract.OCR_TASK_TYPE),),
            )
        }
        projection_queued = 0
        if "search_projection_queue" in tables:
            projection_queued = int(
                connection.execute(
                    "SELECT COUNT(*) AS total FROM search_projection_queue WHERE cause = ?",
                    ("OCR_TEXT_CHANGED",),
                ).fetchone()["total"]
            )
        pending = sum(
            count for state, count in jobs.items() if state in ("QUEUED", "RUNNING", "RETRY_WAIT")
        )
        blocked = int(jobs.get("BLOCKED", 0))
        current_profiles = {
            str(row["processor_id"]): int(row["count"])
            for row in connection.execute(
                "SELECT processor_id, COUNT(*) AS count FROM asset_ocr_text GROUP BY processor_id"
            )
        }
        legacy_pending = int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE task_type = ? AND processor_id = ? "
                "AND state IN ('QUEUED', 'RUNNING', 'RETRY_WAIT')",
                (str(ocr_contract.OCR_TASK_TYPE), str(ocr_contract.OCR_LEGACY_PROCESSOR_ID)),
            ).fetchone()["count"]
        )
        fallback_reasons = {
            str(row["reason"]): int(row["count"])
            for row in connection.execute(
                "SELECT g.last_error_code AS reason, COUNT(*) AS count "
                "FROM asset_ocr_text AS t JOIN jobs AS c ON c.subject_type = 'asset' "
                "AND c.subject_id = CAST(t.asset_id AS TEXT) AND c.input_revision = t.input_revision "
                "AND c.task_type = ? AND c.processor_id = ? "
                "JOIN jobs AS g ON g.subject_type = c.subject_type AND g.subject_id = c.subject_id "
                "AND g.input_revision = c.input_revision AND g.task_type = c.task_type "
                "AND g.processor_id = ? WHERE t.processor_id = ? AND g.last_error_code IN (?, ?, ?) "
                "GROUP BY g.last_error_code",
                (
                    str(ocr_contract.OCR_TASK_TYPE),
                    str(ocr_contract.OCR_CPU_PROCESSOR_ID),
                    str(ocr_contract.OCR_GPU_PROCESSOR_ID),
                    str(ocr_contract.OCR_CPU_PROCESSOR_ID),
                    "RUNTIME_CAPABILITY_UNAVAILABLE",
                    "OCR_GPU_INITIALIZATION_FAILED",
                    "OCR_GPU_EXECUTION_FAILED",
                ),
            )
        }
        stale = stored - documents
        if pending or stale or projection_queued:
            state = "LAGGING"
        elif blocked:
            state = "BLOCKED"
        elif documents:
            state = "SYNCED"
        else:
            state = "NOT_RUN"
        return {
            "processor_id": ocr_contract.OCR_PROCESSOR_ID,
            "processor_profiles": {
                "legacy": ocr_contract.OCR_LEGACY_PROCESSOR_ID,
                "cpu": ocr_contract.OCR_CPU_PROCESSOR_ID,
                "gpu": ocr_contract.OCR_GPU_PROCESSOR_ID,
            },
            "config_hashes": {
                "cpu": ocr_contract.OCR_CPU_CONFIG_HASH,
                "gpu": ocr_contract.OCR_GPU_CONFIG_HASH,
                "legacy": ocr_contract.CONFIG_HASH,
            },
            "current_results_by_processor": current_profiles,
            "legacy_pending_jobs": legacy_pending,
            "cpu_fallbacks": {
                "current_results": sum(fallback_reasons.values()),
                "reasons": fallback_reasons,
            },
            "task_type": ocr_contract.OCR_TASK_TYPE,
            "model_fingerprint": ocr_contract.MODEL_FINGERPRINT,
            "config_hash": ocr_contract.OCR_GPU_CONFIG_HASH,
            "weights_present": weights_present,
            "documents": documents,
            "stale_documents": stale,
            "regions": regions,
            "low_confidence_regions": low,
            "low_confidence_threshold": float(ocr_contract.LOW_CONFIDENCE_THRESHOLD),
            "jobs": jobs,
            "pending_jobs": int(pending),
            "blocked_jobs": blocked,
            "projection_queued": projection_queued,
            "state": state,
        }
    finally:
        connection.close()


def face_summary(
    db_path: str | Path, *, weights_present: bool | None = None
) -> dict[str, Any] | None:
    """Truthful face-readiness of this catalog: analysed, pending, grouped, suggested.

    Read-only and free of personal data: no bbox, no landmark, no vector, no similarity of
    an individual pair and no path leaves this function. What it does expose is the honest
    answer to "has this library been looked at?": how many assets carry faces *for their
    current content revision*, how many were analysed before the content changed (still
    catching up), how many are still queued, and how the independent candidate index and the
    conservative association are doing. ``weights_present`` is the cheap file-level fact the
    caller may add; ``None`` means it was not checked. ``None`` as a whole means this catalog
    predates migration 0010.
    """
    path = Path(db_path)
    if not path.exists():
        return None
    connection = open_readonly(path)
    try:
        tables = _table_names(connection)
        required = {
            "faces",
            "face_embeddings",
            "persons",
            "face_person_links",
            "face_association_queue",
        }
        if not required <= tables:
            return None
        task_type = str(face_contract.FACE_TASK_TYPE)
        processor_id = str(face_contract.FACE_PROCESSOR_ID)
        library_assets = int(
            connection.execute(
                "SELECT COUNT(DISTINCT a.asset_id) AS total FROM assets AS a "
                "JOIN file_locations AS f ON f.asset_id = a.asset_id "
                "AND f.availability = 'PRESENT'"
            ).fetchone()["total"]
        )
        current_faces = int(
            connection.execute(
                "SELECT COUNT(*) AS total FROM faces AS f JOIN assets AS a "
                "ON a.asset_id = f.asset_id WHERE f.input_revision = a.sha256 "
                "AND f.spec_revision = ?",
                (str(face_contract.FACE_SPEC_REVISION),),
            ).fetchone()["total"]
        )
        assets_with_faces = int(
            connection.execute(
                "SELECT COUNT(DISTINCT f.asset_id) AS total FROM faces AS f "
                "JOIN assets AS a ON a.asset_id = f.asset_id WHERE f.input_revision = a.sha256 "
                "AND f.spec_revision = ?",
                (str(face_contract.FACE_SPEC_REVISION),),
            ).fetchone()["total"]
        )
        low_quality = int(
            connection.execute(
                "SELECT COUNT(*) AS total FROM faces AS f JOIN assets AS a "
                "ON a.asset_id = f.asset_id WHERE f.input_revision = a.sha256 "
                "AND f.spec_revision = ? AND f.quality_state <> 'OK'",
                (str(face_contract.FACE_SPEC_REVISION),),
            ).fetchone()["total"]
        )
        analyzed = int(
            connection.execute(
                "SELECT COUNT(*) AS total FROM jobs AS j JOIN assets AS a "
                "ON a.asset_id = CAST(j.subject_id AS INTEGER) "
                "WHERE j.task_type = ? AND j.processor_id = ? AND j.state = 'SUCCEEDED' "
                "AND j.subject_type = 'asset' AND j.input_revision = a.sha256",
                (task_type, processor_id),
            ).fetchone()["total"]
        )
        stale = int(
            connection.execute(
                "SELECT COUNT(*) AS total FROM jobs AS j JOIN assets AS a "
                "ON a.asset_id = CAST(j.subject_id AS INTEGER) "
                "WHERE j.task_type = ? AND j.processor_id = ? AND j.state = 'SUCCEEDED' "
                "AND j.subject_type = 'asset' AND j.input_revision <> a.sha256",
                (task_type, processor_id),
            ).fetchone()["total"]
        )
        degraded = int(
            connection.execute(
                "SELECT COUNT(*) AS total FROM jobs WHERE task_type = ? AND processor_id = ? "
                "AND state = 'SUCCEEDED' AND resolved_device = 'cpu'",
                (task_type, processor_id),
            ).fetchone()["total"]
        )
        jobs = {
            str(row["state"]): int(row["count"])
            for row in connection.execute(
                "SELECT state, COUNT(*) AS count FROM jobs WHERE task_type = ? "
                "AND processor_id = ? GROUP BY state",
                (task_type, processor_id),
            )
        }
        pending = sum(
            count for state, count in jobs.items() if state in ("QUEUED", "RUNNING", "RETRY_WAIT")
        )
        blocked = int(jobs.get("BLOCKED", 0))
        persons = int(
            connection.execute(
                "SELECT COUNT(*) AS total FROM persons WHERE merged_into IS NULL"
            ).fetchone()["total"]
        )
        # P3-03: how much of the group set a human has actually named, and how much of the
        # human knowledge is still waiting for a decision. Counts only - never a name, an
        # alias text or a person id.
        named_persons = int(
            connection.execute(
                "SELECT COUNT(*) AS total FROM persons WHERE merged_into IS NULL "
                "AND name_key IS NOT NULL"
            ).fetchone()["total"]
        )
        unnamed_persons = int(
            connection.execute(
                "SELECT COUNT(*) AS total FROM persons WHERE merged_into IS NULL "
                "AND name_key IS NULL"
            ).fetchone()["total"]
        )
        aliases = (
            int(connection.execute("SELECT COUNT(*) AS total FROM person_aliases").fetchone()["total"])
            if "person_aliases" in tables
            else 0
        )
        pending_confirmation = (
            int(
                connection.execute(
                    "SELECT COUNT(DISTINCT face_id) AS total FROM face_person_links "
                    "WHERE decision = 'SUGGESTED' AND reason IN (?, ?)",
                    (
                        str(person_contract.REASON_MANUAL_UNASSIGNED),
                        str(person_contract.REASON_HUMAN_CONFLICT),
                    ),
                ).fetchone()["total"]
            )
            if "face_assignment_controls" in tables
            else 0
        )
        recent_operations = (
            min(
                20,
                int(
                    connection.execute(
                        "SELECT COUNT(*) AS total FROM person_operations"
                    ).fetchone()["total"]
                ),
            )
            if "person_operations" in tables
            else 0
        )
        # P3-04B single-face correction counts (ADR-0007 §9), all computed from the current
        # SQLite authority and all reduced to plain integers: how much of the face set a
        # human actually *locked*, and how many denials are currently in force. Unnamed
        # groups are never counted as "waiting to be sorted out", a suggestion is never
        # counted as an assignment, the two exclusion counts are distinct faces vs distinct
        # canonical pairs, and a face with several denials is still one face.
        manual_assigned_faces = (
            int(
                connection.execute(
                    "SELECT COUNT(DISTINCT l.face_id) AS total FROM face_person_links AS l "
                    "JOIN face_assignment_controls AS c ON c.face_id = l.face_id "
                    "AND c.mode = 'MANUAL_ASSIGNED' "
                    "WHERE l.assignment_kind = 'MANUAL' AND l.decision = 'CONFIRMED'"
                ).fetchone()["total"]
            )
            if "face_assignment_controls" in tables
            else 0
        )
        manual_unassigned_faces = (
            int(
                connection.execute(
                    "SELECT COUNT(*) AS total FROM face_assignment_controls "
                    "WHERE mode = 'MANUAL_UNASSIGNED'"
                ).fetchone()["total"]
            )
            if "face_assignment_controls" in tables
            else 0
        )
        faces_with_active_exclusions = 0
        active_face_exclusions = 0
        if "face_exclusions" in tables:
            # A denial is one *canonical* (face, person) relation: the row may still name a
            # person that a P3-03 merge moved, and two spellings of the same person are one
            # denial, not two. The walk reuses the single redirect resolver, so a longer
            # merge chain cannot be counted by a one-hop SQL join.
            pairs = {
                (str(row["face_id"]), int(row["person_id"]))
                for row in connection.execute(
                    "SELECT face_id, person_id FROM face_exclusions WHERE active = 1"
                )
            }
            faces_with_active_exclusions = len({face_id for face_id, _ in pairs})
            canonical_pairs: set[tuple[str, int]] = set()
            for face_id, person_id in pairs:
                try:
                    canonical_pairs.add(
                        (face_id, resolve_canonical_person_id(connection, person_id))
                    )
                except CatalogError:
                    # A dangling target cannot be resolved; counting the raw pair keeps the
                    # summary total instead of turning a corrupt row into a failed read.
                    canonical_pairs.add((face_id, person_id))
            active_face_exclusions = len(canonical_pairs)
        confirmed = int(
            connection.execute(
                "SELECT COUNT(*) AS total FROM face_person_links WHERE decision = 'CONFIRMED'"
            ).fetchone()["total"]
        )
        suggested = int(
            connection.execute(
                "SELECT COUNT(*) AS total FROM face_person_links WHERE decision = 'SUGGESTED'"
            ).fetchone()["total"]
        )
        manual = int(
            connection.execute(
                "SELECT COUNT(*) AS total FROM face_person_links WHERE assignment_kind = 'MANUAL'"
            ).fetchone()["total"]
        )
        conflicts = int(
            connection.execute(
                "SELECT COUNT(*) AS total FROM face_person_links WHERE decision = 'SUGGESTED' "
                "AND reason = ?",
                (str(face_contract.REASON_SAME_IMAGE_CONFLICT),),
            ).fetchone()["total"]
        )
        associations_queued = int(
            connection.execute(
                "SELECT COUNT(*) AS total FROM face_association_queue WHERE spec_revision = ?",
                (str(face_contract.FACE_SPEC_REVISION),),
            ).fetchone()["total"]
        )
        index: dict[str, Any] | None = None
        if "face_index_generations" in tables:
            generation = connection.execute(
                "SELECT * FROM face_index_generations WHERE space_id = ? ORDER BY created_at DESC "
                "LIMIT 1",
                (str(face_contract.FACE_SPACE_ID),),
            ).fetchone()
            entries = {
                str(row["state"]): int(row["count"])
                for row in connection.execute(
                    "SELECT state, COUNT(*) AS count FROM face_index_entries GROUP BY state"
                )
            }
            outbox = {
                str(row["state"]): int(row["count"])
                for row in connection.execute(
                    "SELECT state, COUNT(*) AS count FROM face_index_outbox GROUP BY state"
                )
            }
            index = {
                "generation_id": None if generation is None else str(generation["generation_id"]),
                "state": None if generation is None else str(generation["state"]),
                "is_active": bool(generation is not None and int(generation["is_active"])),
                "space_id": None if generation is None else str(generation["space_id"]),
                "dimension": None if generation is None else int(generation["dimension"]),
                "entries": entries,
                "outbox": outbox,
                "pending_candidates": sum(
                    count for state, count in outbox.items() if state != "CONFIRMED"
                ),
            }
        pending_index = 0 if index is None else int(index["pending_candidates"])
        if pending or stale or associations_queued or pending_index:
            state = "LAGGING"
        elif blocked:
            state = "BLOCKED"
        elif analyzed:
            state = "SYNCED"
        else:
            state = "NOT_RUN"
        payload: dict[str, Any] = {
            "processor_id": processor_id,
            "task_type": task_type,
            "spec_revision": str(face_contract.FACE_SPEC_REVISION),
            "space_id": str(face_contract.FACE_SPACE_ID),
            "model_fingerprint": str(face_contract.MODEL_FINGERPRINT),
            "config_hash": str(face_contract.CONFIG_HASH),
            "dimension": int(face_contract.FACE_DIMENSION),
            "weights_present": weights_present,
            "library_assets": library_assets,
            "analyzed_assets": analyzed,
            "pending_assets": max(0, library_assets - analyzed),
            "stale_assets": stale,
            "assets_with_faces": assets_with_faces,
            "faces": current_faces,
            "low_quality_faces": low_quality,
            "persons": persons,
            # P3-03 person-knowledge counts: named vs unnamed active groups, the alias rows,
            # the suggestions a human decision is still holding back, and the size of the
            # recent-operation window. Never a name, an alias text or a person id.
            "named_persons": named_persons,
            "unnamed_persons": unnamed_persons,
            "aliases": aliases,
            "pending_confirmation": pending_confirmation,
            "recent_operations": recent_operations,
            # P3-04B human-control counts: locked faces, faces waiting to be sorted out,
            # faces with at least one active denial, and active canonical denial pairs.
            "manual_assigned_faces": manual_assigned_faces,
            "manual_unassigned_faces": manual_unassigned_faces,
            "faces_with_active_exclusions": faces_with_active_exclusions,
            "active_face_exclusions": active_face_exclusions,
            "confirmed_links": confirmed,
            "suggested_links": suggested,
            "manual_links": manual,
            "same_image_conflicts": conflicts,
            "associations_queued": associations_queued,
            "degraded_jobs": degraded,
            "jobs": jobs,
            "pending_jobs": int(pending),
            "blocked_jobs": blocked,
            "index": index,
            "thresholds": {
                "merge": float(face_contract.MERGE_THRESHOLD),
                "suggest": float(face_contract.SUGGEST_THRESHOLD),
                "near_tie_margin": float(face_contract.NEAR_TIE_MARGIN),
                "min_face_pixels": float(face_contract.MIN_FACE_PIXELS),
                "min_blur_variance": float(face_contract.MIN_BLUR_VARIANCE),
                "low_detection_score": float(face_contract.LOW_DETECTION_SCORE),
            },
            "state": state,
        }
        # P3-08B mapping counts (ADR-0010 §8.3). They are *additive* and only present when the
        # 0014 tables really exist, so a catalog that predates the mapping layer never claims
        # to support it; an existing but empty review queue reports honest zeros.
        if {"face_redetection_groups", "face_redetection_mappings"} <= tables:
            payload.update(_mapping_counts(connection))
        return payload
    finally:
        connection.close()


def _mapping_counts(connection: sqlite3.Connection) -> dict[str, int]:
    """The four read-only review counts of ADR-0010 §8.3 - ids, states and integers only."""

    def one(query: str) -> int:
        return int(connection.execute(query).fetchone()["total"])

    return {
        "open_face_mappings": one(
            "SELECT COUNT(*) AS total FROM face_redetection_groups WHERE state = 'OPEN'"
        ),
        "ambiguous_face_mappings": one(
            "SELECT COUNT(*) AS total FROM face_redetection_groups "
            "WHERE state = 'OPEN' AND candidate_state = 'AMBIGUOUS'"
        ),
        "stale_face_mappings": one(
            "SELECT COUNT(*) AS total FROM face_redetection_mappings WHERE state = 'STALE'"
        ),
        "confirmed_face_mappings": one(
            "SELECT COUNT(*) AS total FROM face_redetection_groups WHERE state = 'CONFIRMED'"
        ),
    }


def pending_projection_summary(db_path: str | Path) -> dict[str, Any] | None:
    """Read-only view of the P2-03 vector projection: what is confirmed, what is late.

    P2-05 needs a truthful answer to "is the image-text index caught up?" before it can
    claim that a photo is (or is not) in semantic recall. Counts only: no artifact path,
    payload, checksum, vector or asset id leaves this function. ``None`` means the catalog
    predates migration 0003, so the facts do not exist yet.
    """
    path = Path(db_path)
    if not path.exists():
        return None
    connection = open_readonly(path)
    try:
        tables = _table_names(connection)
        required = {"index_outbox", "index_entries", "index_generations"}
        if not required <= tables:
            return None

        def counts(table: str, column: str) -> dict[str, int]:
            return {
                str(row["state"]): int(row["count"])
                for row in connection.execute(
                    f"SELECT {column} AS state, COUNT(*) AS count FROM {table} GROUP BY {column}"
                )
            }

        outbox = counts("index_outbox", "state")
        entries = counts("index_entries", "state")
        generations = counts("index_generations", "state")
        active = [
            {
                "generation_id": str(row["generation_id"]),
                "space_id": str(row["space_id"]),
                "dimension": int(row["dimension"]),
                "processor_id": str(row["processor_id"]),
                "state": str(row["state"]),
            }
            for row in connection.execute(
                "SELECT generation_id, space_id, dimension, processor_id, state "
                "FROM index_generations WHERE is_active = 1 ORDER BY generation_id"
            )
        ]
        pending_events = sum(
            count for state, count in outbox.items() if state != "CONFIRMED"
        )
        return {
            "generations": generations,
            "entries": entries,
            "outbox": outbox,
            "active_generations": active,
            "pending_events": int(pending_events),
            "pending_entries": int(entries.get("PENDING_REPAIR", 0)),
            "confirmed_entries": int(entries.get("CONFIRMED", 0)),
            "state": "SYNCED" if pending_events == 0 else "LAGGING",
        }
    finally:
        connection.close()


def search_profile_summary(db_path: str | Path) -> dict[str, Any] | None:
    """The active search profile of this catalog, read-only (P2-06A, architecture §11.3.5).

    ``GET /api/v1/capabilities`` may report which profile is served without opening a
    write path and without leaking a path or a token; ``active_profile_id`` is ``None``
    both when nothing is activated and when this catalog predates migration 0007, and only
    ``None`` for the second case when the whole block is absent. The pending vector
    projection counter is included so a caller can tell a caught-up index from a lagging
    one before it runs a query.
    """
    path = Path(db_path)
    if not path.exists():
        return None
    connection = open_readonly(path)
    try:
        tables = _table_names(connection)
        if "search_profiles" not in tables or "active_search_profile" not in tables:
            return None
        row = connection.execute(
            "SELECT profile_id FROM active_search_profile WHERE state_id = 1"
        ).fetchone()
        profile = None
        if row is not None and row["profile_id"] is not None:
            profile = connection.execute(
                "SELECT profile_id, space_id, image_generation_id, fts_generation_id, "
                "runtime_package_id FROM search_profiles WHERE profile_id = ?",
                (str(row["profile_id"]),),
            ).fetchone()
        active = []
        if "index_generations" in tables:
            active = [
                {
                    "generation_id": str(item["generation_id"]),
                    "space_id": str(item["space_id"]),
                    "state": str(item["state"]),
                }
                for item in connection.execute(
                    "SELECT generation_id, space_id, state FROM index_generations "
                    "WHERE is_active = 1 ORDER BY generation_id"
                )
            ]
        # The ``active_`` prefix keeps these facts apart from the derived projection's own
        # ``fts_generation_id``: that one is the generation the stored documents were
        # actually written with, while this one is what the served profile declares.
        return {
            "active_profile_id": None if profile is None else str(profile["profile_id"]),
            "active_image_generation_id": (
                None if profile is None else str(profile["image_generation_id"])
            ),
            "active_fts_generation_id": (
                None if profile is None else str(profile["fts_generation_id"])
            ),
            "active_runtime_package_id": (
                None if profile is None else str(profile["runtime_package_id"])
            ),
            "active_generations": active,
            "pending_projection": pending_projection_summary(path),
        }
    finally:
        connection.close()
