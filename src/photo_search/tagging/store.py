"""Bounded SQLite projection of optional, immutable machine-label results.

Call writes inside the catalog's single-writer transaction. Inference and artifact file
staging happen before this module is called; leases and retention fences remain owned by
the existing jobs queue. Scores only rerank the caller's bounded existing candidates,
never retrieve additional assets or become authoritative filters.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import struct
from collections.abc import Sequence
from typing import Any

from photo_search.catalog.errors import (
    ARTIFACT_CONFLICT,
    ARTIFACT_INVALID,
    STALE_INPUT_REVISION,
    CatalogError,
)
from photo_search.jobs import queue as jobs
from photo_search.scheduler.contracts import canonical_json

TASK_TYPE = "image_tagging"
PAYLOAD_SCHEMA = "photo-search-tags-v1"
ARTIFACT_REVISION = "tags.v1"
MAX_SCORES = 64
MAX_QUERY_RESULTS = 5000
SCORE_ENTRY = struct.Struct("<HeB")
_SHA = re.compile(r"[0-9a-f]{64}")


def processor_id_for(model_id: str, policy_id: str) -> str:
    content = canonical_json({"model_id": model_id, "policy_id": policy_id})
    return "ram-tags:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def artifact_id_for(input_revision: str, processor_id: str) -> str:
    content = canonical_json({"input_revision": input_revision, "processor_id": processor_id})
    return "image-tags:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _invalid(message: str) -> CatalogError:
    return CatalogError(ARTIFACT_INVALID, message)


def _write_required(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        raise RuntimeError("machine-tag writes require the catalog writer transaction")


def get_model(connection: sqlite3.Connection, processor_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM machine_tag_models WHERE processor_id=?", (processor_id,),
    ).fetchone()
    return None if row is None else dict(row)


def require_revision(connection: sqlite3.Connection, expected: tuple[str, int]) -> None:
    """Compare one frozen identity; callers own transaction/snapshot boundaries."""
    current = get_model(connection, expected[0])
    if current is None or int(current["revision"]) != expected[1]:
        raise CatalogError("INVALID_CURSOR", "物体识别结果已更新，请重新搜索",
                           details={"reason": "CATEGORY_RANK_CHANGED"})


def register_model(
    connection: sqlite3.Connection, *, model_id: str, policy_id: str,
    manifest_sha256: str, class_count: int, max_scores: int, now_ms: int,
) -> dict[str, Any]:
    """Register one immutable model/policy identity before enqueuing its first job."""
    _write_required(connection)
    if (not isinstance(model_id, str) or not 1 <= len(model_id) <= 256
            or not isinstance(policy_id, str) or not 1 <= len(policy_id) <= 128
            or not isinstance(manifest_sha256, str) or not _SHA.fullmatch(manifest_sha256)
            or type(class_count) is not int or not 1 <= class_count <= 65535
            or type(max_scores) is not int or not 1 <= max_scores <= min(MAX_SCORES, class_count)
            or type(now_ms) is not int or now_ms < 0):
        raise _invalid("invalid machine-tag model or sparse-policy identity")
    processor_id = processor_id_for(model_id, policy_id)
    identity = {"model_id": model_id, "policy_id": policy_id,
                "manifest_sha256": manifest_sha256, "class_count": class_count,
                "max_scores": max_scores}
    existing = get_model(connection, processor_id)
    if existing is not None:
        if any(existing[key] != value for key, value in identity.items()):
            raise CatalogError(ARTIFACT_CONFLICT, "registered tag model identity is immutable")
        return existing
    cursor = connection.execute(
        "INSERT INTO machine_tag_models(processor_id,model_id,policy_id,manifest_sha256,"
        "class_count,max_scores,created_at) VALUES(?,?,?,?,?,?,?)",
        (processor_id, model_id, policy_id, manifest_sha256, class_count, max_scores, now_ms),
    )
    # Normally empty. Reconcile pre-existing jobs once at registration, never per poll.
    connection.execute(
        "INSERT INTO machine_tag_job_counts(model_key,state,job_count) "
        "SELECT ?,state,COUNT(*) FROM jobs WHERE processor_id=? AND task_type=? GROUP BY state",
        (int(cursor.lastrowid), processor_id, TASK_TYPE),
    )
    return get_model(connection, processor_id)  # type: ignore[return-value]


def status(connection: sqlite3.Connection, processor_id: str) -> dict[str, Any] | None:
    """Read fixed-size counters; completed_count describes stored content, not visibility."""
    model = get_model(connection, processor_id)
    if model is None:
        return None
    model["job_counts"] = {
        str(row[0]): int(row[1]) for row in connection.execute(
            "SELECT state,job_count FROM machine_tag_job_counts WHERE model_key=?",
            (model["model_key"],),
        )
    }
    return model


def _validated_payload(payload: Any, model: dict[str, Any]) -> list[tuple[int, float, int]]:
    if (not isinstance(payload, dict) or set(payload) != {
            "schema", "model_id", "policy_id", "input_revision", "scores"}
            or payload["schema"] != PAYLOAD_SCHEMA
            or payload["model_id"] != model["model_id"]
            or payload["policy_id"] != model["policy_id"]
            or not isinstance(payload["input_revision"], str)
            or not _SHA.fullmatch(payload["input_revision"])
            or not isinstance(payload["scores"], list)
            or len(payload["scores"]) > model["max_scores"]):
        raise _invalid("machine-tag payload does not match its model and sparse policy")
    scores: list[tuple[int, float, int]] = []
    seen: set[int] = set()
    for row in payload["scores"]:
        if (not isinstance(row, dict) or set(row) != {"tag_id", "score", "above_threshold"}
                or type(row["tag_id"]) is not int or not 0 <= row["tag_id"] < model["class_count"]
                or row["tag_id"] in seen or type(row["score"]) not in (float, int)
                or not math.isfinite(row["score"]) or not 0 <= row["score"] <= 1
                or type(row["above_threshold"]) is not bool):
            raise _invalid("invalid, duplicate or unbounded machine-tag score")
        score = float(row["score"])
        if struct.unpack("e", struct.pack("e", score))[0] != score:
            raise _invalid("machine-tag scores must already be quantized to the frozen fp16 policy")
        seen.add(row["tag_id"])
        scores.append((row["tag_id"], score, int(row["above_threshold"])))
    if scores != sorted(scores, key=lambda row: (-row[1], row[0])):
        raise _invalid("machine-tag sparse entries must use score DESC, tag_id ASC order")
    return scores


def persist_tag_result(
    connection: sqlite3.Connection, *, job: Any, result: Any,
    prepared: Sequence[Any], now_ms: int,
) -> bool:
    """Publish a validated projection in the artifact/job submission transaction.

    The caller first persists the prepared artifact through ArtifactStore. Validating
    the lease again here also protects direct callers and late/out-of-process workers.
    Any exception must abort the surrounding single-writer transaction.
    """
    _write_required(connection)
    if str(job.task_type) != TASK_TYPE:
        return False
    if str(result.state) != jobs.STATE_SUCCEEDED:
        return False
    current_job = jobs.validate_submission(
        connection, job_id=int(result.job_id), lease_token=str(result.lease_token),
        input_revision=str(result.input_revision), processor_id=str(result.processor_id),
        now_ms=int(now_ms),
    )
    if (current_job.job_id != job.job_id or current_job.task_type != TASK_TYPE
            or current_job.subject_type != "asset" or str(result.task_type) != TASK_TYPE
            or not current_job.subject_id.isdecimal()
            or str(int(current_job.subject_id)) != current_job.subject_id):
        raise _invalid("machine-tag result requires its canonical asset job")
    model = get_model(connection, current_job.processor_id)
    if model is None:
        raise _invalid("machine-tag model is not registered")
    if len(prepared) != 1:
        raise _invalid("a machine-tag result must contain exactly one structured artifact")
    item = prepared[0]
    if (str(item.ref.artifact_type) != "structured"
            or str(item.ref.artifact_revision) != ARTIFACT_REVISION
            or str(item.ref.artifact_id) != artifact_id_for(
                current_job.input_revision, current_job.processor_id)):
        raise _invalid("machine-tag artifact identity does not match its job")
    try:
        payload = json.loads(item.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _invalid("machine-tag payload is not valid UTF-8 JSON") from error
    scores = _validated_payload(payload, model)
    asset_id = int(current_job.subject_id)
    asset = connection.execute("SELECT sha256 FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
    if (asset is None or str(asset[0]) != current_job.input_revision
            or payload["input_revision"] != current_job.input_revision):
        raise CatalogError(STALE_INPUT_REVISION, "machine tags refer to stale or deleted content")
    payload_hash = hashlib.sha256(item.payload).hexdigest()
    published = connection.execute(
        "SELECT checksum_sha256,job_id,lifecycle_state FROM artifacts WHERE artifact_id=?",
        (item.ref.artifact_id,),
    ).fetchone()
    if (published is None or published["checksum_sha256"] != payload_hash
            or int(published["job_id"]) != current_job.job_id
            or published["lifecycle_state"] != "PUBLISHED"):
        raise _invalid("machine-tag projection requires its atomically published artifact")
    previous = connection.execute(
        "SELECT input_revision,payload_sha256 FROM machine_tag_results "
        "WHERE model_key=? AND asset_id=?", (model["model_key"], asset_id),
    ).fetchone()
    if previous is not None:
        if tuple(previous) == (current_job.input_revision, payload_hash):
            return False
        # Content identities are immutable. A different deterministic result for the
        # same model/content must use a new policy, not silently replace saved evidence.
        if previous["input_revision"] == current_job.input_revision:
            raise CatalogError(ARTIFACT_CONFLICT, "machine tags changed for an immutable input")
        connection.execute(
            "DELETE FROM machine_tag_results WHERE model_key=? AND asset_id=?",
            (model["model_key"], asset_id),
        )
    connection.execute(
        "INSERT INTO machine_tag_results(model_key,asset_id,input_revision,job_id,artifact_id,"
        "payload_sha256,score_count,scores,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (model["model_key"], asset_id, current_job.input_revision, current_job.job_id,
         item.ref.artifact_id, payload_hash, len(scores),
         b"".join(SCORE_ENTRY.pack(*entry) for entry in scores), int(now_ms)),
    )
    return True


def scores_for_assets(
    connection: sqlite3.Connection, *, processor_id: str,
    asset_ids: Sequence[int], tag_id: int,
) -> tuple[dict[int, float], set[int]]:
    """Read sparse evidence only for <=5000 existing, authoritative candidates.

    Return (scores, covered_ids). A processed asset without the queried sparse tag
    contributes to coverage but has no score; neither absence is a proven negative.
    Read the model revision and this function inside the same catalog read snapshot.
    """
    if (len(asset_ids) > MAX_QUERY_RESULTS
            or any(type(value) is not int or value <= 0 for value in asset_ids)):
        raise ValueError("tag lookup requires at most 5000 positive integer asset ids")
    model = get_model(connection, processor_id)
    if model is None:
        return {}, set()
    if type(tag_id) is not int or not 0 <= tag_id < model["class_count"]:
        raise ValueError("tag query class is outside the registered vocabulary")
    unique_ids = list(dict.fromkeys(asset_ids))
    scores: dict[int, float] = {}
    covered: set[int] = set()
    for start in range(0, len(unique_ids), 500):
        batch = unique_ids[start:start + 500]
        slots = ",".join("?" for _ in batch)
        rows = connection.execute(
            "SELECT r.asset_id,r.scores,r.score_count FROM machine_tag_results AS r "
            "JOIN assets AS a ON a.asset_id=r.asset_id AND a.sha256=r.input_revision "
            f"WHERE r.model_key=? AND r.asset_id IN ({slots}) "
            "AND NOT EXISTS(SELECT 1 FROM asset_retention_barriers AS b WHERE b.asset_id=a.asset_id)",
            (model["model_key"], *batch),
        )
        for row in rows:
            raw = row["scores"]
            if (not isinstance(raw, bytes) or len(raw) != int(row["score_count"]) * SCORE_ENTRY.size
                    or int(row["score_count"]) > model["max_scores"]):
                raise _invalid("stored machine-tag sparse payload is inconsistent")
            covered.add(int(row["asset_id"]))
            for stored_tag, score, above_threshold in SCORE_ENTRY.iter_unpack(raw):
                if (stored_tag >= model["class_count"] or not math.isfinite(score)
                        or not 0 <= score <= 1 or above_threshold not in (0, 1)):
                    raise _invalid("stored machine-tag sparse entry is invalid")
                if stored_tag == tag_id:
                    scores[int(row["asset_id"])] = float(score)
    return scores, covered
