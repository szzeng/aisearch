"""P3-07B fixtures: the scanned search library plus real person authority rows.

Everything lives under pytest's ``tmp_path`` (``%TEMP%``). The photos, their FTS documents,
their vectors and their places all come from the production paths of ``p2_search_fixtures``
and ``p2_search_api_fixtures``; the person authority (persons, faces, links, controls and
exclusions) is seeded as rows exactly as ``p3_person_fixtures.PersonHarness`` does, because
how a face row came to exist is irrelevant to the *search* condition under test -
``test_p3_face_lifecycle.py`` owns the real-detector end-to-end evidence.

The only stand-in is the query encoder, injected through the documented ``encoder_factory``
seam: this build ships no embedding adapter inside the API process.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from p2_search_api_fixtures import SearchEnv, build_search_env
from p2_search_fixtures import assign_place, create_place

from photo_search.persons.contracts import name_key_for
from photo_search.persons.face_correction import CONTRACT_VERSION as FACE_CONTRACT_VERSION
from photo_search.persons.service import PersonService
from photo_search.persons.split import CONTRACT_VERSION as SPLIT_CONTRACT_VERSION


class _FaceSerial:
    """Deterministic, unique 64-hex ``face_id`` values for one fixture catalog."""

    def __init__(self) -> None:
        self._count = 0

    def next(self) -> str:
        self._count += 1
        return hashlib.sha256(f"p3-07b-face-{self._count}".encode()).hexdigest()


def add_face(
    catalog: Any,
    serial: _FaceSerial,
    asset_id: int,
    *,
    ordinal: int = 0,
    quality_state: str = "OK",
) -> str:
    """One seeded face row of an asset (the columns ``p3_face_fixtures`` writes)."""
    from photo_search.face.contract import FACE_SPEC_REVISION

    face_id = serial.next()
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
            "input_revision, location_id, bbox_json, landmarks_json, det_score, "
            "face_pixels, blur_variance, quality_state, quality_reason, artifact_id, "
            "face_revision, created_at, updated_at) "
            "VALUES (?, ?, ?, 'det', ?, ?, 1, '[0,0,10,10]', "
            "'[[1,2],[2,3],[3,4],[4,5],[5,6]]', 0.9, 120.0, 200.0, ?, NULL, 'artifact', "
            "1, 1, 1)",
            (
                face_id,
                int(asset_id),
                str(FACE_SPEC_REVISION),
                int(ordinal),
                hashlib.sha256(f"rev-{face_id}".encode()).hexdigest(),
                str(quality_state),
            ),
        )
    return face_id


def add_person(catalog: Any, *, name: str | None = None) -> int:
    """One person row: unnamed by default, exactly like an automatic group."""
    with catalog.write() as connection:
        cursor = connection.execute(
            "INSERT INTO persons(name, name_key, avatar_face_id, merged_into, revision, "
            "created_at, updated_at) VALUES (?, ?, NULL, NULL, 1, 1, 1)",
            (name, None if name is None else name_key_for(name)),
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
    if decision == "SUGGESTED" and reason is None:
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


def set_control(catalog: Any, face_id: str, mode: str) -> None:
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO face_assignment_controls(face_id, mode, revision, "
            "last_operation_id, updated_at) VALUES (?, ?, 1, 'op-control', 1) "
            "ON CONFLICT(face_id) DO UPDATE SET mode = excluded.mode",
            (str(face_id), str(mode)),
        )


def set_exclusion(catalog: Any, face_id: str, person_id: int, *, active: bool = True) -> None:
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO face_exclusions(face_id, person_id, operation_id, active, "
            "created_at) VALUES (?, ?, 'op-exclude', ?, 1) "
            "ON CONFLICT(face_id, person_id) DO UPDATE SET active = excluded.active",
            (str(face_id), int(person_id), 1 if active else 0),
        )


def set_location_availability(catalog: Any, asset_id: int, availability: str) -> None:
    """Mark one asset's only file location present/missing.

    This writes the same authority row ``catalog.store`` writes on a scan, and is the same
    injection ``test_p2_search_documents.py``/``p3_persons_page_fixtures.py`` use.
    """
    with catalog.write() as connection:
        connection.execute(
            "UPDATE file_locations SET availability = ? WHERE asset_id = ?",
            (str(availability), int(asset_id)),
        )


@dataclass
class PersonSearchEnv:
    """A scanned, vectorised library with persons, plus the operations facade."""

    search: SearchEnv
    serial: _FaceSerial = field(default_factory=_FaceSerial)

    @property
    def catalog(self) -> Any:
        return self.search.catalog

    @property
    def assets(self) -> dict[str, int]:
        return self.search.assets

    @property
    def app(self) -> Any:
        return self.search.app

    @property
    def persons(self) -> PersonService:
        return PersonService(self.catalog, clock=self.catalog.now_ms)

    # --------------------------------------------------------------------- seeding
    def face(self, asset_id: int, *, ordinal: int = 0) -> str:
        return add_face(self.catalog, self.serial, int(asset_id), ordinal=int(ordinal))

    def person(self, name: str | None = None) -> int:
        return add_person(self.catalog, name=name)

    def link(
        self,
        face_id: str,
        person_id: int,
        *,
        decision: str = "CONFIRMED",
        assignment_kind: str = "AUTO",
        revision: int = 1,
    ) -> None:
        add_link(
            self.catalog,
            face_id,
            person_id,
            decision=decision,
            assignment_kind=assignment_kind,
            revision=revision,
        )

    def control(self, face_id: str, mode: str) -> None:
        set_control(self.catalog, face_id, mode)

    def exclusion(self, face_id: str, person_id: int, *, active: bool = True) -> None:
        set_exclusion(self.catalog, face_id, person_id, active=active)

    def place(self, name: str, *, kind: str = "city") -> int:
        return create_place(self.catalog, name, kind=kind)

    def assign(self, asset_id: int, place_id: int, request_id: str) -> None:
        assign_place(
            self.catalog,
            asset_id=int(asset_id),
            place_id=int(place_id),
            request_id=str(request_id),
        )

    def availability(self, asset_id: int, value: str) -> None:
        set_location_availability(self.catalog, int(asset_id), value)

    # ------------------------------------------------------------------ authority
    def member_asset_count(self, person_id: int) -> int:
        """The person's effective ``asset_count`` as the read API computes it."""
        return int(self.persons.get_person(int(person_id))["asset_count"])

    def person_revision(self, person_id: int) -> int:
        row = self.catalog.read_one(
            "SELECT revision FROM persons WHERE person_id = ?", (int(person_id),)
        )
        return int(row["revision"])

    def face_control_revision(self, face_id: str) -> int:
        row = self.catalog.read_one(
            "SELECT revision FROM face_assignment_controls WHERE face_id = ?", (str(face_id),)
        )
        return 0 if row is None else int(row["revision"])

    def face_assignment_revision(self, face_id: str, person_id: int) -> int:
        row = self.catalog.read_one(
            "SELECT revision FROM face_person_links WHERE face_id = ? AND person_id = ?",
            (str(face_id), int(person_id)),
        )
        return int(row["revision"])

    def filter_revision(self) -> int:
        return int(
            self.catalog.read_one(
                "SELECT revision FROM catalog_filter_revision WHERE revision_id = 1"
            )["revision"]
        )

    def filter_causes(self) -> list[str]:
        return [
            str(row["cause"])
            for row in self.catalog.read_all(
                "SELECT cause FROM catalog_filter_revision_events ORDER BY revision"
            )
        ]

    # ------------------------------------------------------------------- operations
    def rename(self, person_id: int, name: str, request_id: str) -> dict[str, Any]:
        return self.persons.rename(
            person_id=int(person_id),
            name=str(name),
            request_id=str(request_id),
            expected_revision=self.person_revision(person_id),
        )

    def merge(self, target: int, sources: list[int], request_id: str) -> dict[str, Any]:
        return self.persons.merge(
            target_person_id=int(target),
            target_expected_revision=self.person_revision(target),
            sources=[
                {"person_id": int(person), "expected_revision": self.person_revision(person)}
                for person in sources
            ],
            request_id=str(request_id),
        )

    def split(self, source: int, faces: list[str], request_id: str) -> dict[str, Any]:
        return self.persons.split(
            source_person_id=int(source),
            contract_version=SPLIT_CONTRACT_VERSION,
            source_expected_revision=self.person_revision(source),
            faces=[
                {
                    "face_id": str(face_id),
                    "expected_control_revision": self.face_control_revision(face_id),
                    "expected_assignment_revision": self.face_assignment_revision(
                        face_id, source
                    ),
                }
                for face_id in faces
            ],
            request_id=str(request_id),
        )

    def face_control(
        self, face_id: str, action: str, *, target_person_id: int | None = None, request_id: str
    ) -> dict[str, Any]:
        return self.persons.set_face_assignment(
            face_id=str(face_id),
            contract_version=FACE_CONTRACT_VERSION,
            action=str(action),
            target_person_id=None if target_person_id is None else int(target_person_id),
            expected_revision=self.face_control_revision(face_id),
            request_id=str(request_id),
        )

    def undo(self, operation_id: str, request_id: str, revision: int) -> dict[str, Any]:
        return self.persons.undo(
            operation_id=str(operation_id),
            request_id=str(request_id),
            expected_operation_revision=int(revision),
        )

    def close(self) -> None:
        self.search.context.close()
        self.search.library.close()


def build_person_search_env(tmp_path: Path) -> PersonSearchEnv:
    """Three scanned photos, one READY generation and the application that serves both."""
    return PersonSearchEnv(search=build_search_env(tmp_path))


__all__ = [
    "PersonSearchEnv",
    "build_person_search_env",
]
