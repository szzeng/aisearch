"""Shared ``%TEMP%`` harness for the P3-08B face-redetection mapping suites.

It extends the P3-04B correction harness (which already seeds assets, faces, persons, links,
controls and denials without a detector run) with three things this task needs:

* faces of an *older* ``spec_revision`` than the one the pinned processor writes, so a
  cross-generation pair can exist at all;
* a real ``persist_face_document()`` call with a hand-built, geometry-controlled face
  document, so the candidate generation runs through its production entry point instead of a
  test-only shortcut;
* readers for the two new authoritative tables and the mapping write path of
  :mod:`photo_search.persons.mapping`.

No fixture here touches a real photo library, downloads a model or needs a GPU: the geometry
is explicit and the faces of the *old* generation are seeded directly, which is exactly the
state a model upgrade leaves behind.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

from p3_face_correction_fixtures import FaceCorrectionHarness

from photo_search.face import contract as face_contract
from photo_search.face.processor import (
    artifact_id_for,
    build_payload,
    face_id_for,
    persist_face_document,
)

#: The generation the pinned processor writes today.
CURRENT_SPEC = str(face_contract.FACE_SPEC_REVISION)
#: A plausible previous generation: the upgrade path only has to look like *another* revision.
OLD_SPEC = "face-buffalo-l-scrfd-arcface-v0"

#: A 100x100 box with five landmarks; the helpers below shift it on purpose.
BASE_BBOX = (10.0, 10.0, 110.0, 110.0)
BASE_LANDMARKS = (
    (30.0, 40.0),
    (80.0, 40.0),
    (55.0, 60.0),
    (40.0, 85.0),
    (70.0, 85.0),
)


def shifted_bbox(dx: float, dy: float = 0.0) -> list[float]:
    return [
        BASE_BBOX[0] + float(dx),
        BASE_BBOX[1] + float(dy),
        BASE_BBOX[2] + float(dx),
        BASE_BBOX[3] + float(dy),
    ]


def shifted_landmarks(dx: float, dy: float = 0.0) -> list[list[float]]:
    return [[x + float(dx), y + float(dy)] for x, y in BASE_LANDMARKS]


def unit_vector(seed: float = 0.0) -> tuple[float, ...]:
    """A valid L2-normalised 512-d vector; only its shape and norm are load-bearing here."""
    values = [0.0] * int(face_contract.FACE_DIMENSION)
    values[0] = 1.0
    values[1] = float(seed)
    norm = sum(value * value for value in values) ** 0.5
    return tuple(value / norm for value in values)


def make_record(
    *,
    ordinal: int,
    bbox: Sequence[float],
    landmarks: Sequence[Sequence[float]],
    quality_state: str = "OK",
    seed: float = 0.0,
) -> Any:
    from photo_search.face.engine import FaceRecord

    return FaceRecord(
        ordinal=int(ordinal),
        bbox=tuple(float(value) for value in bbox),
        landmarks=tuple((float(x), float(y)) for x, y in landmarks),
        det_score=0.95,
        face_pixels=120.0,
        blur_variance=200.0,
        quality_state=str(quality_state),
        quality_reason=None if quality_state == "OK" else face_contract.QUALITY_SMALL,
        embedding=unit_vector(seed),
    )


class RedetectionHarness(FaceCorrectionHarness):
    """The P3-04B harness plus the cross-generation face document and mapping readers."""

    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self.mapping_serial = 0

    # ------------------------------------------------------------------------- seeding
    def add_face(
        self,
        asset_id: int,
        *,
        ordinal: int = 0,
        quality_state: str = "OK",
        face_pixels: float = 120.0,
        spec_revision: str | None = None,
        bbox: Sequence[float] | None = None,
        landmarks: Sequence[Sequence[float]] | None = None,
        face_id: str | None = None,
    ) -> str:
        """Seed one face row of an explicit generation and geometry."""
        self._face_serial += 1
        resolved = (
            str(face_id)
            if face_id is not None
            else hashlib.sha256(
                f"face-{self._face_serial}-{spec_revision or CURRENT_SPEC}".encode()
            ).hexdigest()
        )
        quality_reason = None if quality_state == "OK" else "SMALL"
        bbox_json = json.dumps({"bbox": [float(value) for value in (bbox or BASE_BBOX)]})
        landmarks_json = json.dumps(
            {
                "landmarks": [
                    [float(x), float(y)] for x, y in (landmarks or BASE_LANDMARKS)
                ]
            }
        )
        with self.catalog.write() as connection:
            connection.execute(
                "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
                "input_revision, location_id, bbox_json, landmarks_json, det_score, "
                "face_pixels, blur_variance, quality_state, quality_reason, artifact_id, "
                "face_revision, created_at, updated_at) "
                "VALUES (?, ?, ?, 'det', ?, ?, 1, ?, ?, 0.95, ?, 200.0, ?, ?, "
                "'artifact', 1, 1, 1)",
                (
                    str(resolved),
                    int(asset_id),
                    str(spec_revision or CURRENT_SPEC),
                    int(ordinal),
                    hashlib.sha256(f"rev-{self._face_serial}".encode()).hexdigest(),
                    bbox_json,
                    landmarks_json,
                    float(face_pixels),
                    str(quality_state),
                    quality_reason,
                ),
            )
        return str(resolved)

    def set_asset_sha(self, asset_id: int, revision: str) -> None:
        with self.catalog.write() as connection:
            connection.execute(
                "UPDATE assets SET sha256 = ? WHERE asset_id = ?",
                (str(revision), int(asset_id)),
            )

    def seed_anchor(
        self,
        asset_id: int,
        *,
        mode: str = "MANUAL_ASSIGNED",
        person_id: int | None = None,
        excluded: Sequence[int] = (),
        ordinal: int = 0,
        bbox: Sequence[float] | None = None,
        landmarks: Sequence[Sequence[float]] | None = None,
        quality_state: str = "OK",
    ) -> str:
        """One human-owned face of the *previous* generation, with an optional person."""
        face_id = self.add_face(
            asset_id,
            ordinal=ordinal,
            spec_revision=OLD_SPEC,
            bbox=bbox,
            landmarks=landmarks,
            quality_state=quality_state,
        )
        if person_id is not None:
            self.add_link(face_id, int(person_id), assignment_kind="MANUAL", confidence=0.0)
        self.set_control(face_id, str(mode))
        for person in excluded:
            self.set_exclusion(face_id, int(person), active=True)
        return face_id

    # ----------------------------------------------------------------- face document
    def write_face_document(
        self,
        asset_id: int,
        records: Sequence[Any],
        *,
        input_revision: str | None = None,
        spec_revision: str | None = None,
        sync_asset: bool = True,
        mutate: Any = None,
    ) -> str:
        """Run the real ``persist_face_document()`` for one synthetic asset."""
        from photo_search.face.engine import FaceDocument

        asset = self.catalog.read_one(
            "SELECT sha256 FROM assets WHERE asset_id = ?", (int(asset_id),)
        )
        revision = str(input_revision or asset["sha256"])
        if input_revision is not None and sync_asset:
            self.set_asset_sha(int(asset_id), revision)
        document = FaceDocument(
            original_size=(640, 480),
            working_size=(640, 480),
            scale=1.0,
            faces=tuple(records),
            elapsed_ms=1,
        )
        job = SimpleNamespace(subject_id=int(asset_id))
        result = SimpleNamespace(input_revision=revision)
        previous = None
        if spec_revision is not None:
            previous = face_contract.FACE_SPEC_REVISION
            face_contract.FACE_SPEC_REVISION = str(spec_revision)
        try:
            # The document is built *and* committed under the same generation, exactly as a
            # real worker of that generation would have produced it.
            payload = build_payload(
                input_revision=revision,
                location_id=1,
                document=document,
                decode_format="JPEG",
            )
            if mutate is not None:
                mutate(payload)
            item = SimpleNamespace(
                ref=SimpleNamespace(
                    artifact_type="structured",
                    artifact_id=artifact_id_for(revision),
                ),
                payload=json.dumps(payload, sort_keys=True).encode("utf-8"),
            )
            with self.catalog.write() as connection:
                written = persist_face_document(
                    connection,
                    job=job,
                    result=result,
                    prepared=[item],
                    now_ms=int(self.clock()),
                )
        finally:
            if previous is not None:
                face_contract.FACE_SPEC_REVISION = previous
        assert written is True
        return revision

    def new_face_id(self, asset_id: int, ordinal: int, bbox: Sequence[float]) -> str:
        asset = self.catalog.read_one(
            "SELECT sha256 FROM assets WHERE asset_id = ?", (int(asset_id),)
        )
        return face_id_for(
            input_revision=str(asset["sha256"]), ordinal=int(ordinal), bbox=list(bbox)
        )

    def write_pair(
        self,
        asset_id: int,
        *,
        dx: float = 0.0,
        dy: float = 0.0,
        landmark_dx: float | None = None,
        landmark_dy: float | None = None,
        quality_state: str = "OK",
    ) -> str:
        """One new-generation face near the base geometry; returns its ``face_id``."""
        bbox = shifted_bbox(dx, dy)
        landmarks = shifted_landmarks(
            dx if landmark_dx is None else landmark_dx,
            dy if landmark_dy is None else landmark_dy,
        )
        record = make_record(
            ordinal=0, bbox=bbox, landmarks=landmarks, quality_state=quality_state
        )
        self.write_face_document(asset_id, [record])
        return self.new_face_id(asset_id, 0, bbox)

    # ------------------------------------------------------------------------ readers
    def groups(self) -> list[Any]:
        return self.catalog.read_all(
            "SELECT * FROM face_redetection_groups ORDER BY old_face_id"
        )

    def mappings(self, old_face_id: str | None = None) -> list[Any]:
        if old_face_id is None:
            return self.catalog.read_all(
                "SELECT * FROM face_redetection_mappings ORDER BY old_face_id, new_face_id"
            )
        return self.catalog.read_all(
            "SELECT * FROM face_redetection_mappings WHERE old_face_id = ? "
            "ORDER BY new_face_id",
            (str(old_face_id),),
        )

    def group(self, old_face_id: str) -> Any:
        return self.catalog.read_one(
            "SELECT * FROM face_redetection_groups WHERE old_face_id = ?", (str(old_face_id),)
        )

    def queue_row(self, asset_id: int) -> Any:
        return self.catalog.read_one(
            "SELECT * FROM face_association_queue WHERE asset_id = ?", (int(asset_id),)
        )

    def mapping_page(self, **options: Any) -> dict[str, Any]:
        from photo_search.persons.read import mapping_page

        return mapping_page(self.catalog, **options)

    def mapping_detail(self, old_face_id: str) -> dict[str, Any]:
        from photo_search.persons.read import mapping_group

        return mapping_group(self.catalog, str(old_face_id))

    # ------------------------------------------------------------------------- writes
    def next_mapping_request(self) -> str:
        self.mapping_serial += 1
        return f"map-{self.mapping_serial}"

    def decide(
        self,
        asset_id: int,
        decisions: Sequence[dict[str, Any]],
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self.service.decide_face_mappings(
            payload=None,
            contract_version="p3-face-redetection-contracts.v1",
            request_id=request_id or self.next_mapping_request(),
            asset_id=int(asset_id),
            decisions=list(decisions),
        )

    def decide_body(self, body: dict[str, Any]) -> dict[str, Any]:
        """The transport-shaped call: the whole body is validated by the mapping module."""
        return self.service.decide_face_mappings(
            payload=body,
            contract_version=body.get("contract_version"),
            request_id=body.get("request_id"),
            asset_id=body.get("asset_id"),
            decisions=body.get("decisions"),
        )

    def group_revision(self, old_face_id: str) -> int:
        row = self.group(str(old_face_id))
        assert row is not None
        return int(row["revision"])

    def mapping_of(self, old_face_id: str, new_face_id: str) -> Any:
        return self.catalog.read_one(
            "SELECT * FROM face_redetection_mappings WHERE old_face_id = ? AND new_face_id = ?",
            (str(old_face_id), str(new_face_id)),
        )

    # --------------------------------------------------------------------- lifecycle
    def reopen(self) -> None:
        """Close and reopen the authority exactly as a process restart would."""
        from photo_search.catalog.db import Catalog
        from photo_search.persons.service import PersonService

        self.catalog.close()
        self.catalog = Catalog.open(
            data_root=self.data_root, clock=self.clock, lease_seconds=60
        )
        self.service = PersonService(self.catalog, clock=self.clock)

    def backup_to(self, target_root: Path) -> Path:
        """A real file-level backup of the authority database into another data root."""
        import shutil

        from photo_search.config import catalog_db_path

        self.catalog.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        destination = catalog_db_path(target_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(catalog_db_path(self.data_root), destination)
        return destination


def confirm_decision(
    old_face_id: str, targets: Sequence[tuple[str, bool]], *, revision: int
) -> dict[str, Any]:
    return {
        "old_face_id": str(old_face_id),
        "expected_review_revision": int(revision),
        "decision": "confirm",
        "targets": [
            {"new_face_id": str(new_face_id), "inherits_person": bool(inherits)}
            for new_face_id, inherits in targets
        ],
    }


def reject_decision(old_face_id: str, *, revision: int) -> dict[str, Any]:
    return {
        "old_face_id": str(old_face_id),
        "expected_review_revision": int(revision),
        "decision": "reject",
        "targets": [],
    }
