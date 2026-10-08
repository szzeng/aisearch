"""Durable, bounded source-detail retries for machine faces awaiting organization."""

from __future__ import annotations

import uuid
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import ASSET_NOT_FOUND, CatalogError
from photo_search.face import contract
from photo_search.jobs import queue as jobs

PAGE_SIZE = 50
_LEGACY = "face-buffalo-l-scrfd-arcface-v1"


def _scope_sql(source_id: int | None) -> tuple[str, tuple[Any, ...]]:
    if source_id is None:
        return "", ()
    return " AND loc.source_id = ?", (int(source_id),)


def _upper_asset_id(catalog: Catalog, source_id: int | None) -> int:
    scope, args = _scope_sql(source_id)
    row = catalog.read_one(
        "SELECT COALESCE(MAX(a.asset_id), 0) AS upper_asset_id FROM assets a "
        "JOIN file_locations loc ON loc.asset_id=a.asset_id AND loc.availability='PRESENT' "
        "WHERE 1=1" + scope,
        args,
    )
    return int(row["upper_asset_id"])


def _eligible_sql() -> str:
    return (
        "SELECT DISTINCT a.asset_id, a.sha256 FROM faces f "
        "JOIN assets a ON a.asset_id=f.asset_id AND a.state='ACTIVE' "
        "JOIN file_locations loc ON loc.asset_id=a.asset_id AND loc.availability='PRESENT' "
        "WHERE f.input_revision=a.sha256 AND f.spec_revision IN (?, ?) "
        "AND (f.quality_state='OK' OR (f.quality_reason='SMALL' AND (a.width>1280 OR a.height>1280))) "
        "AND (NOT EXISTS (SELECT 1 FROM face_person_links c WHERE c.face_id=f.face_id AND c.decision='CONFIRMED') "
        " OR f.quality_reason='SMALL' OR EXISTS (SELECT 1 FROM face_person_links s "
        " WHERE s.face_id=f.face_id AND s.decision='SUGGESTED')) "
        "AND NOT EXISTS (SELECT 1 FROM face_assignment_controls ctl WHERE ctl.face_id=f.face_id "
        " AND ctl.mode IN ('MANUAL_ASSIGNED','MANUAL_UNASSIGNED')) "
        "AND NOT EXISTS (SELECT 1 FROM face_person_links h WHERE h.face_id=f.face_id "
        " AND h.assignment_kind='MANUAL' AND h.decision='CONFIRMED') "
        "AND NOT EXISTS (SELECT 1 FROM face_exclusions x WHERE x.face_id=f.face_id AND x.active=1) "
        "AND NOT EXISTS (SELECT 1 FROM face_redetection_groups g WHERE g.state='OPEN' "
        " AND (g.old_face_id=f.face_id OR EXISTS (SELECT 1 FROM face_redetection_mappings m "
        " WHERE m.old_face_id=g.old_face_id AND m.new_face_id=f.face_id AND m.state<>'STALE'))) "
        "AND NOT EXISTS (SELECT 1 FROM face_detail_auto_assets d WHERE d.asset_id=a.asset_id "
        " AND d.input_revision=a.sha256 AND d.ready=1) "
    )


def _candidate_count(catalog: Catalog, *, source_id: int | None, upper_asset_id: int) -> int:
    scope, args = _scope_sql(source_id)
    row = catalog.read_one(
        "SELECT COUNT(*) AS total FROM (" + _eligible_sql() + " AND a.asset_id <= ?" + scope + ")",
        (
            _LEGACY,
            contract.FACE_SPEC_REVISION,
            int(upper_asset_id),
            *args,
        ),
    )
    return int(row["total"])


def create_batch(catalog: Catalog, *, source_id: int | None = None) -> dict[str, Any]:
    if (
        source_id is not None
        and catalog.read_one("SELECT 1 FROM source_roots WHERE source_id=?", (int(source_id),))
        is None
    ):
        raise CatalogError(ASSET_NOT_FOUND, "the selected photo source does not exist")
    scope_key = -1 if source_id is None else int(source_id)
    upper = _upper_asset_id(catalog, source_id)
    estimated = _candidate_count(catalog, source_id=source_id, upper_asset_id=upper)
    now = int(catalog.now_ms())
    with catalog.write() as connection:
        active = connection.execute(
            "SELECT * FROM face_detail_batches WHERE COALESCE(source_id,-1)=? "
            "AND state IN ('QUEUED','RUNNING','PAUSED') ORDER BY created_at LIMIT 1",
            (scope_key,),
        ).fetchone()
        if active is not None:
            return status(catalog, str(active["batch_id"]))
        batch_id = uuid.uuid4().hex
        connection.execute(
            "INSERT INTO face_detail_batches(batch_id,source_id,upper_asset_id,after_asset_id,state,"
            "total,created_at,updated_at) VALUES(?,?,?,0,'QUEUED',?,?,?)",
            (batch_id, source_id, upper, estimated, now, now),
        )
    return status(catalog, batch_id)


def status(
    catalog: Catalog, batch_id: str | None = None, *, source_id: int | None = None
) -> dict[str, Any]:
    query = "SELECT * FROM face_detail_batches "
    params: tuple[Any, ...] = ()
    if batch_id is not None:
        query += "WHERE batch_id=?"
        params = (str(batch_id),)
    else:
        query += "WHERE COALESCE(source_id,-1)=? ORDER BY created_at DESC,batch_id DESC LIMIT 1"
        params = (-1 if source_id is None else int(source_id),)
    row = catalog.read_one(query, params)
    if row is None:
        return {
            "batch_id": None,
            "state": "IDLE",
            "total": 0,
            "succeeded": 0,
            "skipped": 0,
            "failed": 0,
            "pending": 0,
            "needs_review": 0,
        }
    states = {
        str(item["state"]): int(item["count"])
        for item in catalog.read_all(
            "SELECT state,COUNT(*) AS count FROM face_detail_batch_assets WHERE batch_id=? GROUP BY state",
            (str(row["batch_id"]),),
        )
    }
    processed = sum(states.get(key, 0) for key in ("SUCCEEDED", "SKIPPED", "FAILED"))
    return {
        "batch_id": str(row["batch_id"]),
        "source_id": None if row["source_id"] is None else int(row["source_id"]),
        "state": str(row["state"]),
        "total": int(row["total"]),
        "processed": processed,
        "succeeded": states.get("SUCCEEDED", 0),
        "skipped": states.get("SKIPPED", 0),
        "failed": states.get("FAILED", 0),
        "pending": max(0, int(row["total"]) - processed),
        "needs_review": int(row["needs_review"]),
    }


def preview(catalog: Catalog, *, source_id: int | None = None) -> dict[str, Any]:
    upper = _upper_asset_id(catalog, source_id)
    return {
        "source_id": source_id,
        "total": _candidate_count(catalog, source_id=source_id, upper_asset_id=upper),
    }


def next_page(catalog: Catalog, batch_id: str) -> list[dict[str, Any]]:
    from photo_search.face.detail_policy import published

    now = int(catalog.now_ms())
    selected = []
    created = []
    with catalog.write() as connection:
        batch = connection.execute(
            "SELECT * FROM face_detail_batches WHERE batch_id=?", (batch_id,)
        ).fetchone()
        if batch is None or str(batch["state"]) not in ("QUEUED", "RUNNING"):
            return []
        if (
            connection.execute(
                "SELECT 1 FROM face_detail_batch_assets WHERE batch_id=? AND state IN ('QUEUED','RUNNING') LIMIT 1",
                (batch_id,),
            ).fetchone()
            is not None
        ):
            return []
        scope, args = _scope_sql(batch["source_id"])
        selected = connection.execute(
            _eligible_sql()
            + " AND a.asset_id>? AND a.asset_id<=?"
            + scope
            + " ORDER BY a.asset_id LIMIT ?",
            (
                _LEGACY,
                contract.FACE_SPEC_REVISION,
                int(batch["after_asset_id"]),
                int(batch["upper_asset_id"]),
                *args,
                PAGE_SIZE,
            ),
        ).fetchall()
        for item in selected:
            # A completed manual DETAIL-A artifact is reusable, including NO_FACE.
            existing = published(catalog, int(item["asset_id"]), str(item["sha256"]))
            job_id = (
                None
                if existing is not None
                else jobs.enqueue(
                    connection,
                    subject_type="asset",
                    subject_id=str(item["asset_id"]),
                    input_revision=str(item["sha256"]),
                    task_type=contract.FACE_DETAIL_AUTO_TASK_TYPE,
                    processor_id=contract.FACE_PROCESSOR_ID,
                    now_ms=now,
                    requested_device="auto",
                )
            )
            connection.execute(
                "INSERT INTO face_detail_batch_assets(batch_id,asset_id,input_revision,job_id,state,updated_at) "
                "VALUES(?,?,?,?,'QUEUED',?) ON CONFLICT(batch_id,asset_id) DO NOTHING",
                (batch_id, item["asset_id"], item["sha256"], job_id, now),
            )
            created.append({"asset_id": int(item["asset_id"]), "job_id": job_id})
        if selected:
            connection.execute(
                "UPDATE face_detail_batches SET after_asset_id=?,state='RUNNING',updated_at=?,"
                "total=MAX(total,(SELECT COUNT(*) FROM face_detail_batch_assets WHERE batch_id=?)) WHERE batch_id=?",
                (selected[-1]["asset_id"], now, batch_id, batch_id),
            )
        else:
            pending = connection.execute(
                "SELECT COUNT(*) FROM face_detail_batch_assets WHERE batch_id=? AND state IN ('QUEUED','RUNNING')",
                (batch_id,),
            ).fetchone()[0]
            if not pending:
                count = connection.execute(
                    "SELECT COUNT(*),COALESCE(SUM(state='FAILED'),0) FROM face_detail_batch_assets WHERE batch_id=?",
                    (batch_id,),
                ).fetchone()
                # Selection is rechecked as human edits arrive. Final totals count actual
                # selected photos; the preview was an estimate, never phantom unfinished work.
                connection.execute(
                    "UPDATE face_detail_batches SET state=?,total=?,updated_at=? WHERE batch_id=?",
                    ("PARTIAL_FAILED" if count[1] else "COMPLETE", count[0], now, batch_id),
                )
    return created


def sync_jobs(catalog: Catalog, batch_id: str) -> None:
    rows = catalog.read_all(
        "SELECT i.asset_id,i.input_revision,i.job_id,j.state AS job_state,j.last_error_code FROM face_detail_batch_assets i "
        "LEFT JOIN jobs j ON j.job_id=i.job_id WHERE i.batch_id=? AND i.state IN ('QUEUED','RUNNING') "
        "ORDER BY i.asset_id LIMIT 50",
        (str(batch_id),),
    )
    now = int(catalog.now_ms())
    with catalog.write() as connection:
        for row in rows:
            state = str(row["job_state"] or "QUEUED")
            ready = (
                connection.execute(
                    "SELECT 1 FROM face_detail_auto_assets WHERE asset_id=? AND input_revision=? AND ready=1",
                    (row["asset_id"], row["input_revision"]),
                ).fetchone()
                is not None
            )
            if ready:
                asset_id = int(row["asset_id"])
                faces = connection.execute(
                    "SELECT face_id FROM faces WHERE asset_id=? AND input_revision=? AND spec_revision=?",
                    (asset_id, row["input_revision"], contract.FACE_DETAIL_SPEC_REVISION),
                ).fetchall()
                face_ids = [str(face["face_id"]) for face in faces]
                if face_ids:
                    marks = ",".join("?" for _ in face_ids)
                    reviewed = connection.execute(
                        f"SELECT COUNT(*) FROM faces f WHERE f.face_id IN ({marks}) AND NOT EXISTS "
                        f"(SELECT 1 FROM face_person_links l WHERE l.face_id=f.face_id AND l.decision='CONFIRMED')",
                        face_ids,
                    ).fetchone()[0]
                else:
                    reviewed = 0
                item_state = "SUCCEEDED"
                face_count, review_count = len(faces), int(reviewed)
                error_code = None
            elif state in ("FAILED", "CANCELLED", "BLOCKED", "RETRY_WAIT"):
                item_state, face_count, review_count = "FAILED", 0, 0
                error_code = str(row["last_error_code"] or state)
            elif state == "RUNNING":
                item_state, face_count, review_count, error_code = "RUNNING", 0, 0, None
            else:
                item_state, face_count, review_count, error_code = "QUEUED", 0, 0, None
            connection.execute(
                "UPDATE face_detail_batch_assets SET state=?,face_count=?,review_count=?,error_code=?,updated_at=? "
                "WHERE batch_id=? AND asset_id=?",
                (
                    item_state,
                    face_count,
                    review_count,
                    error_code,
                    now,
                    str(batch_id),
                    int(row["asset_id"]),
                ),
            )
        connection.execute(
            "UPDATE face_detail_batches SET succeeded=(SELECT COUNT(*) FROM face_detail_batch_assets "
            "WHERE batch_id=? AND state='SUCCEEDED'), skipped=(SELECT COUNT(*) FROM face_detail_batch_assets "
            "WHERE batch_id=? AND state='SKIPPED'), failed=(SELECT COUNT(*) FROM face_detail_batch_assets "
            "WHERE batch_id=? AND state='FAILED'), needs_review=(SELECT COALESCE(SUM(review_count),0) "
            "FROM face_detail_batch_assets WHERE batch_id=?),updated_at=? WHERE batch_id=?",
            (batch_id, batch_id, batch_id, batch_id, now, batch_id),
        )


def next_item(catalog: Catalog, batch_id: str) -> dict[str, Any] | None:
    row = catalog.read_one(
        "SELECT asset_id,input_revision,job_id,state FROM face_detail_batch_assets WHERE batch_id=? "
        "AND state IN ('QUEUED','RUNNING') ORDER BY asset_id LIMIT 1",
        (str(batch_id),),
    )
    return (
        None
        if row is None
        else {
            "asset_id": int(row["asset_id"]),
            "input_revision": str(row["input_revision"]),
            "job_id": None if row["job_id"] is None else int(row["job_id"]),
            "state": str(row["state"]),
        }
    )


def skip_if_ineligible(catalog: Catalog, batch_id: str, item: dict[str, Any]) -> bool:
    with catalog.write() as connection:
        eligible = connection.execute(
            _eligible_sql() + " AND a.asset_id=? AND a.sha256=? LIMIT 1",
            (_LEGACY, contract.FACE_SPEC_REVISION, item["asset_id"], item["input_revision"]),
        ).fetchone()
        if eligible is not None:
            return False
        connection.execute(
            "UPDATE face_detail_batch_assets SET state='SKIPPED',error_code='NO_LONGER_ELIGIBLE',updated_at=? "
            "WHERE batch_id=? AND asset_id=?",
            (catalog.now_ms(), batch_id, item["asset_id"]),
        )
    return True


def fail_retry_wait(catalog: Catalog, batch_id: str) -> None:
    now = int(catalog.now_ms())
    with catalog.write() as connection:
        connection.execute(
            "UPDATE face_detail_batch_assets SET state='FAILED',error_code='RETRY_WAIT',updated_at=? "
            "WHERE batch_id=? AND job_id IN (SELECT job_id FROM jobs WHERE state='RETRY_WAIT')",
            (now, str(batch_id)),
        )
        connection.execute(
            "UPDATE face_detail_batches SET failed=(SELECT COUNT(*) FROM face_detail_batch_assets "
            "WHERE batch_id=? AND state='FAILED'),updated_at=? WHERE batch_id=?",
            (str(batch_id), now, str(batch_id)),
        )


def pause(catalog: Catalog, batch_id: str) -> dict[str, Any]:
    with catalog.write() as connection:
        connection.execute(
            "UPDATE face_detail_batches SET state='PAUSED',updated_at=? "
            "WHERE batch_id=? AND state IN ('QUEUED','RUNNING')",
            (int(catalog.now_ms()), str(batch_id)),
        )
    return status(catalog, batch_id)


def resume(catalog: Catalog, batch_id: str, *, retry_failed: bool = False) -> dict[str, Any]:
    now = int(catalog.now_ms())
    with catalog.write() as connection:
        batch = connection.execute(
            "SELECT * FROM face_detail_batches WHERE batch_id=?", (batch_id,)
        ).fetchone()
        if batch is None:
            raise CatalogError(ASSET_NOT_FOUND, "the source-detail batch does not exist")
        if retry_failed:
            connection.execute(
                "UPDATE jobs SET state='QUEUED',attempts=0,progress=0,lease_token=NULL,"
                "lease_expires_at=NULL,worker_id=NULL,run_after=?,last_error_code=NULL,"
                "last_error_message=NULL,blocking_reason=NULL,resolved_device=NULL,"
                "runtime_provider=NULL,precision=NULL,runtime_capability_revision=NULL,"
                "dispatch_spec_revision=NULL,updated_at=? WHERE job_id IN "
                "(SELECT job_id FROM face_detail_batch_assets WHERE batch_id=? AND state='FAILED') "
                "AND state IN ('FAILED','CANCELLED','BLOCKED','RETRY_WAIT')",
                (now, now, batch_id),
            )
            connection.execute(
                "UPDATE face_detail_batch_assets SET state='QUEUED',error_code=NULL,updated_at=? "
                "WHERE batch_id=? AND state='FAILED'",
                (now, batch_id),
            )
        connection.execute(
            "UPDATE face_detail_batches SET state='QUEUED',updated_at=? "
            "WHERE batch_id=? AND state IN ('PAUSED','PARTIAL_FAILED')",
            (now, str(batch_id)),
        )
    return status(catalog, batch_id)


def running_batches(catalog: Catalog) -> list[str]:
    return [
        str(row["batch_id"])
        for row in catalog.read_all(
            "SELECT batch_id FROM face_detail_batches WHERE state IN ('QUEUED','RUNNING') "
            "ORDER BY created_at,batch_id LIMIT 8"
        )
    ]
