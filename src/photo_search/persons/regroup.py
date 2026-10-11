"""Explicit selected-face regrouping; one authority transaction, no detector or I/O.

The existing asset-scoped rematch is intentionally not called: it would widen the
selection. This use case shares its candidate reader and conservative decision policy.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from photo_search.catalog.errors import FACE_ASSIGNMENT_CONFLICT, FACE_STATE_CONFLICT
from photo_search.face import contract
from photo_search.face import index as association
from photo_search.persons import face_correction as correction
from photo_search.persons import operations, split
from photo_search.persons.contracts import (
    check_expected_revision,
    check_person_id,
    check_request_id,
    fail,
    operation_id_for,
    payload_sha256,
)

CONTRACT_VERSION = "p4-face-regroup.v1"
OPERATION_TYPE = "FACE_REGROUP"


def require_no_hold(connection: Any, face_ids: list[str]) -> None:
    for face_id in face_ids:
        if connection.execute(
            "SELECT 1 FROM face_redetection_groups g WHERE g.state='OPEN' AND "
            "(g.old_face_id=? OR EXISTS (SELECT 1 FROM face_redetection_mappings m "
            "WHERE m.old_face_id=g.old_face_id AND m.new_face_id=? AND m.state='SUGGESTED'))",
            (face_id, face_id),
        ).fetchone():
            raise fail(
                FACE_STATE_CONFLICT,
                "face awaits mapping review",
                face_id=face_id,
                reason="provisional_hold",
            )


def result(connection: Any, operation: Any) -> dict[str, Any]:
    answer = correction._replay_face(
        connection,
        request_id=str(operation["request_id"]),
        digest=str(operation["payload_sha256"]),
    )
    records = [
        correction.load_control_bundle(row["after_value_json"])["regroup"]
        for row in correction.stored_items(connection, str(operation["operation_id"]))
    ]
    grouped = sum(row["outcome"] in {"NEW_GROUP", "MERGE"} for row in records)
    return {
        **answer,
        "contract_version": CONTRACT_VERSION,
        "grouped_faces": grouped,
        "new_persons": sum(row["outcome"] == "NEW_GROUP" for row in records),
        "remaining_faces": len(records) - grouped,
    }


def _vector(face: Any) -> tuple[float, ...] | None:
    if (
        face["processor_id"] != contract.FACE_PROCESSOR_ID
        or face["space_id"] != contract.FACE_SPACE_ID
        or face["dimension"] != contract.FACE_DIMENSION
        or face["metric"] != "cosine"
        or face["normalization"] != "L2"
        or face["vector_blob"] is None
    ):
        return None
    blob = bytes(face["vector_blob"])
    if (
        len(blob) != 4 * contract.FACE_DIMENSION
        or hashlib.sha256(blob).hexdigest() != face["vector_sha256"]
    ):
        return None
    vector = association.blob_to_vector(blob, dimension=contract.FACE_DIMENSION)
    if not all(math.isfinite(v) for v in vector) or abs(sum(v * v for v in vector) - 1) > 0.001:
        return None
    return vector


def apply(
    catalog: Any,
    *,
    source_person_id: int,
    source_expected_revision: int,
    faces: list[dict[str, Any]],
    contract_version: str,
    request_id: str,
) -> dict[str, Any]:
    if contract_version != CONTRACT_VERSION:
        raise fail("VALIDATION_ERROR", "unsupported regroup contract", field="contract_version")
    request_id = check_request_id(request_id)
    requested_source = check_person_id(source_person_id)
    expected = check_expected_revision(source_expected_revision)
    items = split.check_faces(faces)
    digest = payload_sha256(
        {
            "endpoint": "persons.regroup",
            "contract_version": CONTRACT_VERSION,
            "source_person_id": requested_source,
            "source_expected_revision": expected,
            "faces": items,
        }
    )
    with catalog.write() as connection:
        replay = correction._replay_face(connection, request_id=request_id, digest=digest)
        if replay is not None:
            return result(connection, operations.operation_row(connection, replay["operation_id"]))
        source = correction.canonical_person(connection, requested_source)
        person = connection.execute(
            "SELECT revision FROM persons WHERE person_id=?", (source,)
        ).fetchone()
        if int(person["revision"]) != expected:
            raise fail(FACE_ASSIGNMENT_CONFLICT, "source person changed", reason="group_changed")
        members = split.effective_member_face_ids(connection, source)
        ids = [item["face_id"] for item in items]
        require_no_hold(connection, ids)
        prepared = []
        compatible = {"face-buffalo-l-scrfd-arcface-v1", contract.FACE_SPEC_REVISION}
        from photo_search.face import detail_policy

        if detail_policy.compatibility_verified():
            compatible.update(detail_policy.COMPATIBLE_SPECS)
        for item in items:
            face_id = item["face_id"]
            correction.require_face(connection, face_id)
            correction._require_state(connection, face_id)
            correction.require_expected_revision(
                connection, face_id=face_id, expected=item["expected_control_revision"]
            )
            before = correction.read_control_bundle(connection, face_id)
            link = before["assignment"]
            if (
                face_id not in members
                or link is None
                or link["person_id"] != source
                or link["revision"] != item["expected_assignment_revision"]
            ):
                raise fail(
                    FACE_ASSIGNMENT_CONFLICT,
                    "selected membership changed",
                    face_id=face_id,
                    reason="assignment_revision",
                )
            face = connection.execute(
                "SELECT f.*, e.processor_id,e.space_id,e.dimension,e.metric,e.normalization,"
                "e.vector_blob,e.vector_sha256,a.sha256,a.state AS asset_state "
                "FROM faces f JOIN assets a ON a.asset_id=f.asset_id "
                "LEFT JOIN face_embeddings e ON e.face_id=f.face_id WHERE f.face_id=?",
                (face_id,),
            ).fetchone()
            if (
                face["asset_state"] != "ACTIVE"
                or face["input_revision"] != face["sha256"]
                or face["spec_revision"] not in compatible
            ):
                raise fail(FACE_STATE_CONFLICT, "face generation is unavailable", face_id=face_id)
            index = association.FaceIndexCoordinator(catalog, spec_revision=face["spec_revision"])
            generation = index.active_generation()
            if generation is None or generation["state"] != "READY":
                raise fail(
                    FACE_STATE_CONFLICT, "candidate index is not ready", reason="index_not_ready"
                )
            if (
                connection.execute(
                    "SELECT 1 FROM face_index_entries WHERE face_id=? AND generation_id=? "
                    "AND artifact_id=? AND state='CONFIRMED'",
                    (face_id, index.generation_id, face["artifact_id"]),
                ).fetchone()
                is None
            ):
                raise fail(
                    FACE_STATE_CONFLICT,
                    "selected face publication is incomplete",
                    face_id=face_id,
                    reason="index_not_ready",
                )
            prepared.append((face, before, _vector(face), index))
        # Never mistake an incomplete representative index for an unknown identity.
        generation_ids = [
            association.FaceIndexCoordinator(catalog, spec_revision=spec).generation_id
            for spec in sorted(compatible)
        ]
        marks = ",".join("?" for _ in generation_ids)
        if (
            connection.execute(
                "SELECT 1 FROM face_index_entries WHERE state='PENDING_REPAIR' "
                f"AND generation_id IN ({marks}) LIMIT 1",
                generation_ids,
            ).fetchone()
            or connection.execute(
                "SELECT 1 FROM face_index_outbox WHERE state <> 'CONFIRMED' "
                f"AND generation_id IN ({marks}) LIMIT 1",
                generation_ids,
            ).fetchone()
        ):
            raise fail(FACE_STATE_CONFLICT, "candidate repair is pending", reason="index_not_ready")
        candidates = [c for c in prepared[0][3].candidates() if c.face_id not in ids]
        now = catalog.now_ms()
        operation_id = operation_id_for(request_id)
        revision = correction._open_operation(
            connection,
            operation_id=operation_id,
            request_id=request_id,
            digest=digest,
            operation_type=OPERATION_TYPE,
            target_person_id=None,
            undo_of_operation_id=None,
            now_ms=now,
        )
        # Prepare every selected face before matching: old membership cannot seed a new group.
        for face, before, _, _ in prepared:
            face_id = face["face_id"]
            link = correction._confirmed_links(connection, face_id)[0]
            correction._end_effective_link(
                connection,
                link=link,
                reason="MANUAL_UNASSIGNED",
                operation_id=operation_id,
                now_ms=now,
            )
            correction._write_exclusion(
                connection,
                face_id=face_id,
                person_id=source,
                active=True,
                operation_id=operation_id,
                now_ms=now,
            )
            correction._write_control(
                connection,
                face_id=face_id,
                mode="AUTO_ALLOWED",
                revision=before["control"]["revision"] + 1,
                operation_id=operation_id,
                now_ms=now,
            )
        results = []
        for face, before, vector, _ in prepared:
            face_id, asset_id = face["face_id"], int(face["asset_id"])
            denied = set(correction.active_exclusion_person_ids(connection, face_id))
            usable = [c for c in candidates if c.person_id not in denied]
            occupied = {
                correction.canonical_person(connection, int(row[0]))
                for row in connection.execute(
                    "SELECT l.person_id FROM face_person_links l JOIN faces f ON f.face_id=l.face_id "
                    "WHERE f.asset_id=? AND l.decision='CONFIRMED'",
                    (asset_id,),
                )
            }
            decision = (
                None
                if vector is None
                else association.decide_for_face(
                    quality_state=face["quality_state"],
                    vector=vector,
                    candidates=usable,
                    person_in_asset=occupied,
                )
            )
            outcome = "VECTOR_UNAVAILABLE" if decision is None else decision.action
            target = None if decision is None else decision.person_id
            if outcome == "NEW_GROUP":
                # Existing rematch permits singleton groups. This explicit correction is
                # more conservative: at least one reliable selected peer on another photo.
                peers = [
                    other
                    for other, _, v, _ in prepared
                    if other["face_id"] != face_id
                    and other["asset_id"] != asset_id
                    and other["quality_state"] == "OK"
                    and v is not None
                    and association.cosine(vector, v) >= contract.MERGE_THRESHOLD
                ]
                if peers:
                    target = association._new_person(connection, now_ms=now)
                else:
                    outcome = "UNMATCHED"
            if outcome in {"NEW_GROUP", "MERGE"}:
                floor = max(
                    int(before["assignment"]["revision"]),
                    int(
                        connection.execute(
                            "SELECT COALESCE(MAX(revision),0) FROM face_person_links WHERE face_id=?",
                            (face_id,),
                        ).fetchone()[0]
                    ),
                )
                association._confirm(
                    connection,
                    face_id=face_id,
                    person_id=target,
                    confidence=float(decision.similarity or 0),
                    now_ms=now,
                )
                connection.execute(
                    "UPDATE face_person_links SET revision=MAX(revision,?),last_operation_id=? "
                    "WHERE face_id=? AND person_id=? AND assignment_kind='AUTO' AND decision='CONFIRMED'",
                    (floor + 1, operation_id, face_id, target),
                )
                candidates.append(association.Candidate(face_id, int(target), asset_id, vector))
            elif outcome == "SUGGESTED":
                association._suggest(
                    connection,
                    face_id=face_id,
                    person_id=int(target),
                    similarity=float(decision.similarity),
                    reason=str(decision.reason),
                    now_ms=now,
                )
            after = correction.read_control_bundle(connection, face_id)
            after["regroup"] = {"outcome": outcome}
            results.append({"face_id": face_id, "before": before, "after": after})
        correction._close_face_operation(
            connection,
            operation_id=operation_id,
            operation_type=OPERATION_TYPE,
            operation_revision=revision,
            undo_of_operation_id=None,
            results=results,
            now_ms=now,
            refresh=None,
        )
        answer = result(connection, operations.operation_row(connection, operation_id))
        answer["replayed"] = False
        return answer
