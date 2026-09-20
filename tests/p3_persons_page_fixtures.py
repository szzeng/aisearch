"""Shared ``%TEMP%`` harness for the P3-06 person-page suites.

The P3-06 page is a *read* surface over the P3-03..P3-05 authority plus one derived media
endpoint, so the harness needs two things this machine can produce without a model:

* **real bytes.** Synthetic JPEGs are written under ``tmp_path``, registered as a source and
  scanned by the real ingestion path, so the frozen P1-03 media pipeline can really publish a
  preview and the face crop has real pixels to cut from.
* **deterministic authority rows.** Face/person/link/control rows are inserted straight into
  the migrated catalog (exactly like the P3-03/P3-04 harnesses), so the read and write
  protocols can be exercised without spending a detector run. The ``face_id`` is still the
  real deterministic identity of the P3-02 pipeline, never an invented string.

Nothing here touches a real photo library, downloads a model, opens a network socket or runs
an inference.
"""

from __future__ import annotations

from typing import Any, Sequence

from conftest import ApiEnv

#: One authoritative face box in orientation-normalised source pixels, as P3-02 stores it.
Box = tuple[float, float, float, float]


def insert_face(
    catalog: Any,
    *,
    asset_id: int,
    sha256: str,
    bbox: Sequence[float],
    ordinal: int,
    location_id: int | None = None,
    quality_state: str = "OK",
) -> str:
    """Insert one *current* face row and return its deterministic ``face_id``."""
    from photo_search.face.contract import FACE_DETECTOR_ID, FACE_SPEC_REVISION
    from photo_search.face.processor import face_id_for

    values = [float(value) for value in bbox]
    face_id = face_id_for(input_revision=str(sha256), ordinal=int(ordinal), bbox=values)
    reason = None if str(quality_state) == "OK" else "SMALL"
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
            "input_revision, location_id, bbox_json, landmarks_json, det_score, face_pixels, "
            "blur_variance, quality_state, quality_reason, artifact_id, face_revision, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.9, ?, 200.0, ?, ?, 'artifact', 1, 1, 1)",
            (
                face_id,
                int(asset_id),
                str(FACE_SPEC_REVISION),
                str(FACE_DETECTOR_ID),
                int(ordinal),
                str(sha256),
                None if location_id is None else int(location_id),
                "[" + ", ".join(repr(value) for value in values) + "]",
                "[[1, 2], [2, 3], [3, 4], [4, 5], [5, 6]]",
                round((values[2] - values[0]) * (values[3] - values[1]), 2),
                str(quality_state),
                reason,
            ),
        )
    return face_id


def add_person(catalog: Any, *, name: str | None = None, avatar_face_id: str | None = None) -> int:
    from photo_search.persons.contracts import name_key_for

    with catalog.write() as connection:
        cursor = connection.execute(
            "INSERT INTO persons(name, name_key, avatar_face_id, merged_into, revision, "
            "created_at, updated_at) VALUES (?, ?, ?, NULL, 1, 1, 1)",
            (
                name,
                None if name is None else name_key_for(name),
                None if avatar_face_id is None else str(avatar_face_id),
            ),
        )
        return int(cursor.lastrowid)


def add_link(
    catalog: Any,
    face_id: str,
    person_id: int,
    *,
    decision: str = "CONFIRMED",
    assignment_kind: str = "AUTO",
    confidence: float = 0.9,
    reason: str | None = None,
    revision: int = 1,
) -> None:
    if str(decision) == "SUGGESTED" and reason is None:
        reason = "BORDERLINE"
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
            "confidence, reason, revision, last_operation_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 1, 1)",
            (
                str(face_id),
                int(person_id),
                str(assignment_kind),
                str(decision),
                float(confidence),
                reason,
                int(revision),
            ),
        )


def set_control(catalog: Any, face_id: str, mode: str, *, revision: int = 1) -> None:
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO face_assignment_controls(face_id, mode, revision, last_operation_id, "
            "updated_at) VALUES (?, ?, ?, 'op-control', 1) "
            "ON CONFLICT(face_id) DO UPDATE SET mode = excluded.mode, "
            "revision = excluded.revision",
            (str(face_id), str(mode), int(revision)),
        )


def set_exclusion(
    catalog: Any, face_id: str, person_id: int, *, active: bool = True
) -> None:
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO face_exclusions(face_id, person_id, operation_id, active, created_at) "
            "VALUES (?, ?, 'op-exclude', ?, 1) "
            "ON CONFLICT(face_id, person_id) DO UPDATE SET active = excluded.active",
            (str(face_id), int(person_id), 1 if active else 0),
        )


def mark_location_missing(catalog: Any, asset_id: int) -> None:
    """Take every registered position of one asset offline *without* rescanning."""
    with catalog.write() as connection:
        connection.execute(
            "UPDATE file_locations SET availability = 'MISSING' WHERE asset_id = ?",
            (int(asset_id),),
        )


def seed_library(
    env: ApiEnv,
    photos: dict[str, list[Box]],
    *,
    size: tuple[int, int] = (320, 240),
) -> dict[str, dict[str, Any]]:
    """Write synthetic photos, scan them for real, then attach the given face rows.

    ``photos`` maps a source-relative path to the authoritative face boxes of that photo.
    The result maps the same path to ``{"asset_id", "sha256", "faces", "location_id"}``.
    """
    for index, name in enumerate(photos):
        env.add_photo(name, size=size, noise=index + 1)
    source = env.register_source("library")
    env.scan(int(source["source_id"]))
    rows = env.service.catalog.read_all(
        "SELECT a.asset_id AS asset_id, a.sha256 AS sha256, l.relative_path AS relative_path, "
        "       l.location_id AS location_id "
        "  FROM assets AS a JOIN file_locations AS l ON l.asset_id = a.asset_id "
        " WHERE l.availability = 'PRESENT' ORDER BY l.relative_path"
    )
    by_path = {
        str(row["relative_path"]).replace("\\", "/"): row
        for row in rows
    }
    seeded: dict[str, dict[str, Any]] = {}
    for name, boxes in photos.items():
        row = by_path[str(name)]
        faces = [
            insert_face(
                env.service.catalog,
                asset_id=int(row["asset_id"]),
                sha256=str(row["sha256"]),
                bbox=box,
                ordinal=ordinal,
                location_id=int(row["location_id"]),
            )
            for ordinal, box in enumerate(boxes)
        ]
        seeded[str(name)] = {
            "asset_id": int(row["asset_id"]),
            "sha256": str(row["sha256"]),
            "location_id": int(row["location_id"]),
            "faces": faces,
        }
    return seeded


__all__ = [
    "add_link",
    "add_person",
    "insert_face",
    "mark_location_missing",
    "seed_library",
    "set_control",
    "set_exclusion",
]
