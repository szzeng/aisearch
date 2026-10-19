"""One-time recovery of legacy CR2 decode failures during an explicit library build.

This module only rewinds current-profile face/OCR jobs whose exact terminal error was
FORMAT_UNSUPPORTED and whose current source bytes still have a Canon CR2 header. The
durable marker preserves the old error and prevents a second automatic attempt.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from photo_search.artifacts.store import ArtifactStore
from photo_search.media.raw_cr2 import is_cr2


def recover_cr2_format_failures(
    catalog: Any,
    *,
    data_root: str | Path,
    asset_ids: Sequence[int],
    source_ids: Sequence[int | None],
    task_type: str,
    processor_id: str,
    artifact_revision: str,
) -> dict[str, int]:
    """CAS-rewind proven legacy failures and record each attempt durably.

    ``source_ids`` is the selected build scope. ``(None,)`` means all sources; otherwise
    candidates must have a PRESENT location in one of those sources. File headers are read
    only after a matching terminal job and current content revision have been found.
    """
    ids = sorted({int(value) for value in asset_ids})
    result = {
        "cr2_recovery_candidates": 0,
        "cr2_recovery_jobs": 0,
        "cr2_recovery_published_skips": 0,
        "cr2_recovery_unverified_headers": 0,
        "cr2_recovery_identity_mismatches": 0,
        "cr2_recovery_decode_failed_retained": 0,
        "cr2_recovery_already_attempted": 0,
        "cr2_recovery_artifact_inconsistencies": 0,
    }
    if not ids:
        return result
    if not source_ids:
        return result

    placeholders = ",".join("?" for _ in ids)
    scope_clause = ""
    parameters: list[Any] = [str(task_type), str(processor_id), *ids]
    if None not in source_ids:
        selected = sorted({int(value) for value in source_ids})
        if not selected:
            return result
        scope_clause = " AND l.source_id IN (" + ",".join("?" for _ in selected) + ")"
        parameters.extend(selected)

    # Query only old terminal failures for the chosen profile. No per-asset SQL is issued.
    rows = catalog.read_all(
        "SELECT j.job_id,j.subject_type,j.subject_id,j.input_revision,j.task_type,"
        "j.processor_id,j.state,j.last_error_code,j.last_error_message,a.sha256,"
        "l.relative_path,s.root_path,EXISTS(SELECT 1 FROM cr2_job_recoveries r "
        "WHERE r.job_id=j.job_id) AS recovered "
        "FROM jobs j JOIN assets a ON j.subject_type='asset' "
        "AND j.subject_id=CAST(a.asset_id AS TEXT) "
        "JOIN file_locations l ON l.asset_id=a.asset_id AND l.availability='PRESENT' "
        "JOIN source_roots s ON s.source_id=l.source_id "
        f"WHERE j.subject_type='asset' AND j.task_type=? AND j.processor_id=? "
        f"AND a.asset_id IN ({placeholders}) AND j.state IN ('FAILED','BLOCKED') "
        "AND j.last_error_code IN ('FORMAT_UNSUPPORTED','DECODE_FAILED') "
        "AND lower(l.relative_path) LIKE '%.cr2'"
        + scope_clause
        + " ORDER BY j.job_id,l.location_id",
        tuple(parameters),
    )
    if not rows:
        return result

    # Collapse duplicate source locations by job. A matching current revision is necessary
    # before the header probe; DECODE_FAILED is observed but conservatively never rewound.
    by_job: dict[int, Any] = {}
    for row in rows:
        job_id = int(row["job_id"])
        by_job.setdefault(job_id, row)
    candidates: list[Any] = []
    for row in by_job.values():
        if str(row["input_revision"]) != str(row["sha256"]):
            result["cr2_recovery_identity_mismatches"] += 1
            continue
        if int(row["recovered"]):
            result["cr2_recovery_already_attempted"] += 1
            continue
        if str(row["last_error_code"]) == "DECODE_FAILED":
            result["cr2_recovery_decode_failed_retained"] += 1
            continue
        result["cr2_recovery_candidates"] += 1
        path = Path(str(row["root_path"])) / str(row["relative_path"])
        try:
            verified = is_cr2(path)
        except OSError:
            verified = False
        if not verified:
            result["cr2_recovery_unverified_headers"] += 1
            continue
        candidates.append(row)

    if not candidates:
        return result

    candidate_revisions = sorted({str(row["input_revision"]) for row in candidates})
    artifact_slots = ",".join("?" for _ in candidate_revisions)
    artifact_rows = catalog.read_all(
        "SELECT * FROM artifacts WHERE processor_id=? AND input_revision IN ("
        + artifact_slots
        + ") AND artifact_type='structured' AND artifact_revision=? "
        "AND lifecycle_state='PUBLISHED'",
        (str(processor_id), *candidate_revisions, str(artifact_revision)),
    )
    artifact_by_revision: dict[str, list[Any]] = {}
    for artifact in artifact_rows:
        artifact_by_revision.setdefault(str(artifact["input_revision"]), []).append(artifact)

    store = ArtifactStore.open(data_root)
    for row in candidates:
        artifacts = artifact_by_revision.get(str(row["input_revision"]), [])
        if artifacts:
            try:
                for artifact in artifacts:
                    payload = json.loads(store.read(artifact))
                    if not _published_document_matches(
                        payload,
                        task_type=str(row["task_type"]),
                        processor_id=str(row["processor_id"]),
                        input_revision=str(row["input_revision"]),
                        artifact_revision=str(artifact_revision),
                    ):
                        raise ValueError("published document identity did not match")
            except Exception:  # corrupt/missing published content is an integrity issue
                result["cr2_recovery_artifact_inconsistencies"] += 1
            else:
                result["cr2_recovery_published_skips"] += 1
                continue
            continue

        recovered = _rewind_once(catalog, row)
        if recovered:
            result["cr2_recovery_jobs"] += 1
        else:
            # Another worker/build changed the state between classification and CAS.
            result["cr2_recovery_already_attempted"] += 1
    return result


def _published_document_matches(
    payload: Any,
    *,
    task_type: str,
    processor_id: str,
    input_revision: str,
    artifact_revision: str,
) -> bool:
    """Check the document's own identity in addition to the filtered artifact row."""
    if (
        not isinstance(payload, dict)
        or str(payload.get("processor_id")) != str(processor_id)
        or str(payload.get("input_revision")) != str(input_revision)
    ):
        return False

    from photo_search.face import contract as face_contract
    from photo_search.ocr import contract as ocr_contract

    if task_type == face_contract.FACE_TASK_TYPE:
        return (
            str(artifact_revision) == face_contract.FACE_ARTIFACT_REVISION
            and "spec_revision" not in payload
            and "artifact_revision" not in payload
        )
    if task_type == face_contract.FACE_DETAIL_AUTO_TASK_TYPE:
        return (
            str(artifact_revision) == face_contract.FACE_DETAIL_ARTIFACT_REVISION
            and str(payload.get("spec_revision"))
            == face_contract.FACE_DETAIL_SPEC_REVISION
            and str(payload.get("artifact_revision"))
            == face_contract.FACE_DETAIL_ARTIFACT_REVISION
        )
    if task_type == ocr_contract.OCR_TASK_TYPE:
        return "artifact_revision" not in payload
    return False


def _rewind_once(catalog: Any, row: Any) -> bool:
    """Atomically retain old failure evidence, mark the attempt and queue one fresh job."""
    now_ms = int(catalog.now_ms())
    key = (
        str(row["subject_type"]),
        str(row["subject_id"]),
        str(row["input_revision"]),
        str(row["task_type"]),
        str(row["processor_id"]),
    )
    with catalog.write() as connection:
        state = str(row["state"])
        error_code = str(row["last_error_code"])
        cursor = connection.execute(
            "INSERT INTO cr2_job_recoveries(job_id,subject_type,subject_id,input_revision,"
            "task_type,processor_id,source_state,source_error_code,source_error_message,"
            "recovered_at) SELECT job_id,subject_type,subject_id,input_revision,task_type,"
            "processor_id,state,last_error_code,last_error_message,? FROM jobs "
            "WHERE job_id=? AND subject_type=? AND subject_id=? AND input_revision=? "
            "AND task_type=? AND processor_id=? AND state=? AND last_error_code=? "
            "AND lease_token IS NULL AND lease_expires_at IS NULL AND worker_id IS NULL "
            "AND EXISTS(SELECT 1 FROM assets a WHERE a.asset_id=CAST(jobs.subject_id AS INTEGER) "
            "AND a.sha256=jobs.input_revision) "
            "AND NOT EXISTS(SELECT 1 FROM cr2_job_recoveries r WHERE r.job_id=jobs.job_id)",
            (now_ms, int(row["job_id"]), *key, state, error_code),
        )
        if cursor.rowcount != 1:
            return False
        updated = connection.execute(
            "UPDATE jobs SET state='QUEUED',attempts=0,progress=0.0,run_after=?,"
            "lease_token=NULL,lease_expires_at=NULL,worker_id=NULL,last_error_code=NULL,"
            "last_error_message=NULL,blocking_reason=NULL,resolved_device=NULL,"
            "runtime_provider=NULL,precision=NULL,runtime_capability_revision=NULL,"
            "dispatch_spec_revision=NULL,updated_at=? WHERE job_id=? AND subject_type=? "
            "AND subject_id=? AND input_revision=? AND task_type=? AND processor_id=? "
            "AND state=? AND last_error_code=? AND lease_token IS NULL "
            "AND lease_expires_at IS NULL AND worker_id IS NULL "
            "AND EXISTS(SELECT 1 FROM assets a WHERE a.asset_id=CAST(jobs.subject_id AS INTEGER) "
            "AND a.sha256=jobs.input_revision)",
            (now_ms, now_ms, int(row["job_id"]), *key, state, error_code),
        )
        if updated.rowcount != 1:
            # Roll back the inserted recovery record with the transaction.
            raise RuntimeError("CR2 recovery CAS lost after marker insertion")
    return True


__all__ = ["recover_cr2_format_failures"]
