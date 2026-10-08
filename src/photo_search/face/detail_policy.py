"""Derived source-detail policy; immutable inference identities remain unchanged."""

from __future__ import annotations

import sqlite3
from typing import Any

from photo_search.face import contract

POLICY_REVISION = "source-detail-auto@1"
VERIFIED_MODEL_FINGERPRINT = "62b5343df26ed097211c936a328de882090d0a1af5dde937c05e2aa3d7b1e74d"
VERIFIED_ORDINARY_CONFIG_HASH = "955550ce5eaf3bb1d6e6ce1cb212011ee3994f7cbc96553a55191d1b6f29347b"
VERIFIED_DETAIL_CONFIG_HASH = "49ac9be9fd62d25b3f6d652edea14a7368bb8ab4eaeb93ee90a308dee648b004"


def compatibility_verified() -> bool:
    from photo_search.face.processor import face_processor_spec

    return (
        contract.MODEL_FINGERPRINT == VERIFIED_MODEL_FINGERPRINT
        and face_processor_spec().config_hash == VERIFIED_ORDINARY_CONFIG_HASH
        and face_processor_spec(source_detail=True).config_hash == VERIFIED_DETAIL_CONFIG_HASH
        and contract.FACE_SPEC_REVISION == "face-buffalo-l-scrfd-arcface-v2"
        and contract.FACE_DETAIL_SPEC_REVISION == "face-buffalo-l-scrfd-arcface-source-crop-v1"
    )


# Only this exact preprocessing/recognizer family may share representatives. The public
# real-model evidence is recorded in P4-PERSON-DETAIL-B-compatibility.json.
COMPATIBLE_SPECS = (
    "face-buffalo-l-scrfd-arcface-v1",
    "face-buffalo-l-scrfd-arcface-v2",
    "face-buffalo-l-scrfd-arcface-source-crop-v1",
)


def superseded_machine_sql(face: str = "f") -> str:
    """Keep history and human facts; exclude only a completed older machine projection."""
    return (
        f"({face}.spec_revision <> '{contract.FACE_DETAIL_SPEC_REVISION}' "
        f"AND EXISTS (SELECT 1 FROM face_detail_auto_assets d WHERE d.asset_id={face}.asset_id "
        f"AND d.input_revision={face}.input_revision AND d.ready=1 "
        f"AND d.policy_revision='{POLICY_REVISION}') "
        "AND NOT EXISTS (SELECT 1 FROM face_assignment_controls ctl "
        f"WHERE ctl.face_id={face}.face_id AND ctl.mode IN ('MANUAL_ASSIGNED','MANUAL_UNASSIGNED')) "
        f"AND NOT EXISTS (SELECT 1 FROM face_exclusions x WHERE x.face_id={face}.face_id AND x.active=1) "
        f"AND NOT EXISTS (SELECT 1 FROM face_person_links h WHERE h.face_id={face}.face_id "
        "AND h.assignment_kind='MANUAL' AND h.decision='CONFIRMED') "
        f"AND NOT EXISTS (SELECT 1 FROM face_redetection_groups g WHERE g.old_face_id={face}.face_id "
        "AND g.state='OPEN'))"
    )


def authorize(
    connection: sqlite3.Connection,
    *,
    asset_id: int,
    input_revision: str,
    artifact_id: str,
    now_ms: int,
) -> None:
    connection.execute(
        "INSERT INTO face_detail_auto_assets(asset_id,input_revision,artifact_id,policy_revision,ready,updated_at) "
        "VALUES(?,?,?,?,0,?) ON CONFLICT(asset_id) DO UPDATE SET "
        "input_revision=excluded.input_revision,artifact_id=excluded.artifact_id,"
        "policy_revision=excluded.policy_revision,updated_at=excluded.updated_at,"
        "ready=CASE WHEN face_detail_auto_assets.artifact_id=excluded.artifact_id "
        "AND face_detail_auto_assets.input_revision=excluded.input_revision "
        "THEN face_detail_auto_assets.ready ELSE 0 END",
        (asset_id, input_revision, artifact_id, POLICY_REVISION, now_ms),
    )


def published(catalog: Any, asset_id: int, input_revision: str) -> Any:
    return catalog.read_one(
        "SELECT artifact_id FROM artifacts WHERE processor_id=? AND input_revision=? "
        "AND artifact_revision=? AND lifecycle_state='PUBLISHED' LIMIT 1",
        (contract.FACE_PROCESSOR_ID, input_revision, contract.FACE_DETAIL_ARTIFACT_REVISION),
    )


def restore(catalog: Any, *, asset_id: int, input_revision: str) -> bool:
    """Promote an existing manual/detail result without changing its inference product."""
    document = published(catalog, asset_id, input_revision)
    if document is None:
        return False
    prepare_coordinate_frames(catalog, asset_id=asset_id)
    from photo_search.face.index import queue_candidate
    from photo_search.face.processor import _ensure_generation

    with catalog.write() as connection:
        asset = connection.execute(
            "SELECT sha256 FROM assets WHERE asset_id=?", (asset_id,)
        ).fetchone()
        if asset is None or str(asset[0]) != input_revision:
            return False
        now = catalog.now_ms()
        authorize(
            connection,
            asset_id=asset_id,
            input_revision=input_revision,
            artifact_id=str(document["artifact_id"]),
            now_ms=now,
        )
        generation = _ensure_generation(
            connection, now_ms=now, spec_revision=contract.FACE_DETAIL_SPEC_REVISION
        )
        rows = connection.execute(
            "SELECT face_id,face_revision FROM faces WHERE asset_id=? AND input_revision=? "
            "AND spec_revision=? AND artifact_id=?",
            (asset_id, input_revision, contract.FACE_DETAIL_SPEC_REVISION, document["artifact_id"]),
        ).fetchall()
        for row in rows:
            queue_candidate(
                connection,
                face_id=str(row["face_id"]),
                generation_id=generation,
                artifact_id=str(document["artifact_id"]),
                now_ms=now,
            )
        connection.execute(
            "INSERT INTO face_detail_association_queue(asset_id,spec_revision,face_revision,queued_at) "
            "VALUES(?,?,?,?) ON CONFLICT(asset_id,spec_revision) DO NOTHING",
            (
                asset_id,
                contract.FACE_DETAIL_SPEC_REVISION,
                max((int(row["face_revision"]) for row in rows), default=1),
                now,
            ),
        )
        if not rows:
            connection.execute(
                "UPDATE face_index_generations SET state='READY' WHERE generation_id=?",
                (generation,),
            )
    return True


def prepare_coordinate_frames(catalog: Any, *, asset_id: int) -> None:
    """Verify old document frames outside the authority transaction; never read originals."""
    from photo_search.catalog.errors import CatalogError
    from photo_search.media.face_crop import parse_bbox, recorded_face_frame

    rows = catalog.read_all(
        "SELECT f.* FROM faces f JOIN assets a ON a.asset_id=f.asset_id AND a.sha256=f.input_revision "
        "WHERE f.asset_id=? AND f.face_id=(SELECT MIN(other.face_id) FROM faces other "
        "WHERE other.asset_id=f.asset_id AND other.artifact_id=f.artifact_id) "
        "AND NOT EXISTS(SELECT 1 FROM face_detail_coordinate_frames frame "
        "WHERE frame.artifact_id=f.artifact_id AND frame.input_revision=f.input_revision)",
        (asset_id,),
    )
    for face in rows:
        artifact = catalog.read_one(
            "SELECT * FROM artifacts WHERE artifact_id=?", (face["artifact_id"],)
        )
        try:
            frame = recorded_face_frame(
                catalog.data_root,
                face,
                artifact,
                box=parse_bbox(face["bbox_json"]),
                sha256=str(face["input_revision"]),
            )
        except CatalogError:
            continue  # An unverifiable historical frame stays under the manual hold.
        if frame is not None:
            with catalog.write() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO face_detail_coordinate_frames(artifact_id,input_revision,width,height) "
                    "VALUES(?,?,?,?)",
                    (face["artifact_id"], face["input_revision"], *frame),
                )
