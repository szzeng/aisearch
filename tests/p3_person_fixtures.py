"""Shared ``%TEMP%`` fixtures for the P3-03 person suites.

Two kinds of harness live here, on purpose:

* :class:`PersonHarness` seeds the *authority* rows directly (assets, faces, persons and
  links) so naming, merging, redirects, idempotency and compensation can be tested without
  spending a detector run on every case. Nothing about the person protocol depends on how a
  face row was produced; :mod:`tests.p3_face_fixtures` covers the real-detector end to end.
* the helpers below keep every seeded row inside pytest's ``tmp_path`` (``%TEMP%``); no
  fixture in this file ever touches a real photo library.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.persons import bundle as bundle_module
from photo_search.persons.service import PersonService

WALL_BASE_MS = 1_700_000_000_000


class Clock:
    def __init__(self, start: int = WALL_BASE_MS) -> None:
        self.ms = int(start)

    def __call__(self) -> int:
        return self.ms

    def advance(self, delta_ms: int) -> None:
        self.ms += int(delta_ms)


class PersonHarness:
    """A migrated catalog with deterministic assets, faces and person groups."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = Path(tmp_path)
        self.data_root = self.tmp_path / "PhotoSearchData"
        self.clock = Clock()
        self.catalog = Catalog.open(
            data_root=self.data_root, clock=self.clock, lease_seconds=60
        )
        self.service = PersonService(self.catalog, clock=self.clock)
        self._asset_serial = 0
        self._face_serial = 0
        with self.catalog.write() as connection:
            connection.execute(
                "INSERT INTO source_roots(display_name, root_path, volume_hint, status) "
                "VALUES ('library', 'C:\\photos', 'C:', 'ONLINE')"
            )

    # ------------------------------------------------------------------ seeding
    def add_asset(self, name: str = "photo.jpg") -> int:
        self._asset_serial += 1
        digest = hashlib.sha256(f"{name}-{self._asset_serial}".encode()).hexdigest()
        with self.catalog.write() as connection:
            cursor = connection.execute(
                "INSERT INTO assets(sha256, byte_size, width, height, format, created_at, "
                "state) VALUES (?, 4096, 640, 480, 'PNG', ?, 'ACTIVE')",
                (digest, WALL_BASE_MS),
            )
            asset_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, "
                "size, mtime_ns, last_seen_scan_id, availability) "
                "VALUES (1, ?, ?, ?, 4096, 7, NULL, 'PRESENT')",
                (f"{self._asset_serial}/{name}", f"{self._asset_serial}/{name}", asset_id),
            )
        return asset_id

    def add_face(
        self,
        asset_id: int,
        *,
        ordinal: int = 0,
        quality_state: str = "OK",
        face_pixels: float = 120.0,
    ) -> str:
        from photo_search.face.contract import FACE_SPEC_REVISION

        self._face_serial += 1
        face_id = hashlib.sha256(f"face-{self._face_serial}".encode()).hexdigest()
        assert len(face_id) == 64
        quality_reason = None if quality_state == "OK" else "SMALL"
        with self.catalog.write() as connection:
            connection.execute(
                "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
                "input_revision, location_id, bbox_json, landmarks_json, det_score, "
                "face_pixels, blur_variance, quality_state, quality_reason, artifact_id, "
                "face_revision, created_at, updated_at) "
                "VALUES (?, ?, ?, 'det', ?, ?, 1, '[0,0,10,10]', "
                "'[[1,2],[2,3],[3,4],[4,5],[5,6]]', 0.9, ?, 200.0, ?, ?, 'artifact', 1, 1, 1)",
                (
                    face_id,
                    int(asset_id),
                    str(FACE_SPEC_REVISION),
                    int(ordinal),
                    hashlib.sha256(f"rev-{self._face_serial}".encode()).hexdigest(),
                    float(face_pixels),
                    str(quality_state),
                    quality_reason,
                ),
            )
        return face_id

    def add_embedding(self, face_id: str, *, space_id: str | None = None) -> None:
        from photo_search.face.contract import (
            FACE_DIMENSION,
            FACE_PROCESSOR_ID,
            FACE_SPACE_ID,
        )

        with self.catalog.write() as connection:
            connection.execute(
                "INSERT INTO face_embeddings(face_id, processor_id, space_id, dimension, "
                "metric, normalization, vector_blob, vector_sha256, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'cosine', 'L2', x'00', ?, 1, 1)",
                (
                    str(face_id),
                    str(FACE_PROCESSOR_ID),
                    str(space_id or FACE_SPACE_ID),
                    int(FACE_DIMENSION),
                    "c" * 64,
                ),
            )

    def add_person(self, *, name: str | None = None) -> int:
        from photo_search.persons.contracts import name_key_for

        with self.catalog.write() as connection:
            cursor = connection.execute(
                "INSERT INTO persons(name, name_key, avatar_face_id, merged_into, revision, "
                "created_at, updated_at) VALUES (?, ?, NULL, NULL, 1, 1, 1)",
                (name, None if name is None else name_key_for(name)),
            )
            return int(cursor.lastrowid)

    def add_link(
        self,
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
        with self.catalog.write() as connection:
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

    def set_control(self, face_id: str, mode: str) -> None:
        with self.catalog.write() as connection:
            connection.execute(
                "INSERT INTO face_assignment_controls(face_id, mode, revision, "
                "last_operation_id, updated_at) VALUES (?, ?, 1, 'op-control', 1) "
                "ON CONFLICT(face_id) DO UPDATE SET mode = excluded.mode",
                (str(face_id), str(mode)),
            )

    def set_exclusion(self, face_id: str, person_id: int, *, active: bool = True) -> None:
        with self.catalog.write() as connection:
            connection.execute(
                "INSERT INTO face_exclusions(face_id, person_id, operation_id, active, "
                "created_at) VALUES (?, ?, 'op-exclude', ?, 1) "
                "ON CONFLICT(face_id, person_id) DO UPDATE SET active = excluded.active",
                (str(face_id), int(person_id), 1 if active else 0),
            )

    def seed_candidate_index(self, face_ids: list[str]) -> str:
        """Create the active face generation and one confirmed entry per face."""
        from photo_search.face.contract import (
            FACE_DIMENSION,
            FACE_PROCESSOR_ID,
            FACE_SPACE_ID,
            FACE_SPEC_REVISION,
        )
        from photo_search.face.processor import generation_id_for

        generation_id = generation_id_for(str(FACE_SPACE_ID), str(FACE_PROCESSOR_ID))
        with self.catalog.write() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO face_index_generations(generation_id, space_id, "
                "processor_id, spec_revision, dimension, metric, normalization, state, "
                "is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'cosine', 'L2', "
                "'READY', 1, 1, 1)",
                (
                    generation_id,
                    str(FACE_SPACE_ID),
                    str(FACE_PROCESSOR_ID),
                    str(FACE_SPEC_REVISION),
                    int(FACE_DIMENSION),
                ),
            )
            for face_id in face_ids:
                connection.execute(
                    "INSERT INTO face_index_entries(generation_id, face_id, artifact_id, "
                    "confirmation_revision, confirmed_at, state) "
                    "VALUES (?, ?, 'artifact', 1, 1, 'CONFIRMED') "
                    "ON CONFLICT(generation_id, face_id) DO UPDATE SET state = 'CONFIRMED', "
                    "confirmed_at = 1",
                    (generation_id, str(face_id)),
                )
        return generation_id

    def candidate_entries(self) -> dict[str, str]:
        return {
            str(row["face_id"]): str(row["state"])
            for row in self.catalog.read_all(
                "SELECT face_id, state FROM face_index_entries"
            )
        }

    # ------------------------------------------------------------------ operations
    def rename(self, person_id: int, name: str, request_id: str, revision: int) -> dict[str, Any]:
        return self.service.rename(
            person_id=int(person_id),
            name=name,
            request_id=str(request_id),
            expected_revision=int(revision),
        )

    def merge(
        self,
        target: int,
        sources: list[tuple[int, int]],
        request_id: str,
        *,
        target_revision: int,
    ) -> dict[str, Any]:
        return self.service.merge(
            target_person_id=int(target),
            target_expected_revision=int(target_revision),
            sources=[
                {"person_id": int(person), "expected_revision": int(revision)}
                for person, revision in sources
            ],
            request_id=str(request_id),
        )

    def undo(self, operation_id: str, request_id: str, revision: int) -> dict[str, Any]:
        return self.service.undo(
            operation_id=str(operation_id),
            request_id=str(request_id),
            expected_operation_revision=int(revision),
        )

    # --------------------------------------------------------------------- readers
    def person(self, person_id: int) -> Any:
        return self.catalog.read_one("SELECT * FROM persons WHERE person_id = ?", (int(person_id),))

    def revision_of(self, person_id: int) -> int:
        return int(self.person(person_id)["revision"])

    def view(self, person_id: int) -> dict[str, Any]:
        return self.service.get_person(int(person_id))

    def bundle(self, person_id: int) -> dict[str, Any]:
        return bundle_module.read_bundle(self.catalog.connection, int(person_id))

    def links(self) -> list[Any]:
        return self.catalog.read_all(
            "SELECT * FROM face_person_links ORDER BY person_id, face_id, assignment_kind"
        )

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

    def queue_rows(self) -> list[Any]:
        return self.catalog.read_all(
            "SELECT asset_id, cause FROM search_projection_queue ORDER BY asset_id"
        )

    def index_outbox_rows(self) -> int:
        return int(self.catalog.read_one("SELECT COUNT(*) AS total FROM index_outbox")["total"])

    def face_outbox_rows(self) -> list[Any]:
        return self.catalog.read_all(
            "SELECT face_id, state FROM face_index_outbox ORDER BY face_id"
        )

    def operations(self) -> list[Any]:
        return self.catalog.read_all(
            "SELECT * FROM person_operations ORDER BY operation_revision"
        )

    def summary(self) -> dict[str, Any]:
        from photo_search.scheduler.projection import face_summary

        return face_summary(self.catalog.db_path, weights_present=True) or {}

    def close(self) -> None:
        self.catalog.close()
