"""P3-08B browser-smoke fixture: a searchable synthetic library plus one redetection review.

The recorded real-browser closure of the task card needs three pages to work on one data root:
``/persons`` (a redetection review with old/new face crops), ``/places`` (a manual place
correction) and ``/search`` (a person+place AND query with a stale old session). The search
page needs an activated ``SearchProfile`` and a diagnostic image generation, which is exactly
what :mod:`tools.diagnostics.search_smoke_fixture` already builds - so this script calls that
fixture first and then seeds the *person* facts on top of it:

* one named person and one unnamed group;
* two human-owned faces of the **previous** generation (one ``MANUAL_ASSIGNED``, one
  ``MANUAL_UNASSIGNED`` with an active denial);
* the current generation's faces written through the real ``persist_face_document()``, so the
  review groups and their candidate rows are produced by production code;
* the pair left ``OPEN`` (never auto-confirmed) so the page has something to decide.

Everything is synthetic and written under ``%TEMP%`` (or ``--root``); ``--reset`` only removes
the directory this script owns, and no real photo library, model or network is touched.

Usage::

    uv run --no-sync python tools/diagnostics/face_mapping_smoke_fixture.py --reset
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tools" / "diagnostics"))

import search_smoke_fixture as search_fixture  # noqa: E402

from photo_search.face import contract as face_contract  # noqa: E402
from photo_search.face.engine import FaceDocument, FaceRecord  # noqa: E402
from photo_search.face.processor import (  # noqa: E402
    artifact_id_for,
    build_payload,
    persist_face_document,
)
from photo_search.ingestion.service import CatalogService  # noqa: E402
from photo_search.persons.contracts import name_key_for  # noqa: E402
from photo_search.places.service import PlaceService  # noqa: E402

WORK_DIRNAME = "PhotoSearchFaceMappingSmoke"
OLD_SPEC = "face-buffalo-l-scrfd-arcface-v0"
WALL_BASE_MS = 1_700_000_000_000

#: One 100x100 box with five landmarks per seeded face; the new generation is shifted slightly
#: so the candidate is a qualified ``UNIQUE_HIGH`` pair.
BBOX = (40.0, 40.0, 140.0, 140.0)
LANDMARKS = ((60.0, 70.0), (110.0, 70.0), (85.0, 90.0), (70.0, 115.0), (100.0, 115.0))


def _bbox(shift: float) -> list[float]:
    return [value + float(shift) for value in BBOX]


def _landmarks(shift: float) -> list[list[float]]:
    return [[x + float(shift), y + float(shift)] for x, y in LANDMARKS]


def _record(*, shift: float, seed: float) -> FaceRecord:
    values = [0.0] * int(face_contract.FACE_DIMENSION)
    values[0] = 1.0
    values[1] = float(seed)
    norm = sum(value * value for value in values) ** 0.5
    return FaceRecord(
        ordinal=0,
        bbox=tuple(_bbox(shift)),
        landmarks=tuple((float(x), float(y)) for x, y in _landmarks(shift)),
        det_score=0.95,
        face_pixels=120.0,
        blur_variance=200.0,
        quality_state="OK",
        quality_reason=None,
        embedding=tuple(value / norm for value in values),
    )


def _asset_rows(service: CatalogService) -> list[Any]:
    return service.catalog.read_all(
        "SELECT a.asset_id AS asset_id, a.sha256 AS sha256, f.relative_path AS relative_path, "
        "       f.location_id AS location_id FROM assets AS a "
        "JOIN file_locations AS f ON f.asset_id = a.asset_id "
        "WHERE f.availability = 'PRESENT' ORDER BY f.relative_path"
    )


def seed_anchor(
    service: CatalogService,
    *,
    asset_id: int,
    sha256: str,
    location_id: int,
    mode: str,
    person_id: int | None = None,
) -> str:
    face_id = hashlib.sha256(f"old-anchor-{asset_id}-{mode}".encode()).hexdigest()
    with service.catalog.write() as connection:
        connection.execute(
            "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
            "input_revision, location_id, bbox_json, landmarks_json, det_score, face_pixels, "
            "blur_variance, quality_state, quality_reason, artifact_id, face_revision, "
            "created_at, updated_at) VALUES (?, ?, ?, 'det', 0, ?, ?, ?, ?, 0.95, 120.0, "
            "200.0, 'OK', NULL, 'artifact', 1, 1, 1)",
            (
                face_id,
                int(asset_id),
                str(OLD_SPEC),
                str(sha256),
                int(location_id),
                json.dumps({"bbox": _bbox(0.0)}),
                json.dumps({"landmarks": _landmarks(0.0)}),
            ),
        )
        connection.execute(
            "INSERT INTO face_assignment_controls(face_id, mode, revision, last_operation_id, "
            "updated_at) VALUES (?, ?, 1, NULL, 1)",
            (face_id, str(mode)),
        )
        if person_id is not None:
            connection.execute(
                "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
                "confidence, reason, revision, last_operation_id, created_at, updated_at) "
                "VALUES (?, ?, 'MANUAL', 'CONFIRMED', 0.0, NULL, 1, NULL, 1, 1)",
                (face_id, int(person_id)),
            )
    return face_id


def write_generation(
    service: CatalogService, *, asset_id: int, sha256: str, location_id: int
) -> str:
    """Write the current generation's face through the real commit path."""
    document = FaceDocument(
        original_size=(640, 480),
        working_size=(640, 480),
        scale=1.0,
        faces=(_record(shift=6.0, seed=0.2),),
        elapsed_ms=1,
    )
    payload = build_payload(
        input_revision=str(sha256),
        location_id=int(location_id),
        document=document,
        decode_format="JPEG",
    )
    item = SimpleNamespace(
        ref=SimpleNamespace(
            artifact_type="structured", artifact_id=artifact_id_for(str(sha256))
        ),
        payload=json.dumps(payload, sort_keys=True).encode("utf-8"),
    )
    with service.catalog.write() as connection:
        written = persist_face_document(
            connection,
            job=SimpleNamespace(subject_id=int(asset_id)),
            result=SimpleNamespace(input_revision=str(sha256)),
            prepared=[item],
            now_ms=WALL_BASE_MS,
        )
    assert written is True
    row = service.catalog.read_one(
        "SELECT face_id FROM faces WHERE asset_id = ? AND spec_revision = ?",
        (int(asset_id), str(face_contract.FACE_SPEC_REVISION)),
    )
    return str(row["face_id"])


def build(root: Path) -> dict[str, Any]:
    work_dir = root / WORK_DIRNAME
    search_root = work_dir / "SearchFixture"
    work_dir.mkdir(parents=True, exist_ok=True)
    search_fixture.main(
        ["--work-dir", str(search_root), "--photos", "6", "--reset", "--json"]
    )
    data_root = search_root / search_fixture.DATA_ROOT_DIRNAME
    service = CatalogService.open(data_root=data_root)
    try:
        rows = _asset_rows(service)
        assert len(rows) >= 2, "the search fixture must have produced at least two assets"
        primary, secondary = rows[0], rows[1]
        with service.catalog.write() as connection:
            dad = int(
                connection.execute(
                    "INSERT INTO persons(name, name_key, avatar_face_id, merged_into, revision, "
                    "created_at, updated_at) VALUES ('爸爸', ?, NULL, NULL, 1, 1, 1)",
                    (name_key_for("爸爸"),),
                ).lastrowid
            )
            uncle = int(
                connection.execute(
                    "INSERT INTO persons(name, name_key, avatar_face_id, merged_into, revision, "
                    "created_at, updated_at) VALUES ('叔叔', ?, NULL, NULL, 1, 1, 1)",
                    (name_key_for("叔叔"),),
                ).lastrowid
            )
        primary_anchor = seed_anchor(
            service,
            asset_id=int(primary["asset_id"]),
            sha256=str(primary["sha256"]),
            location_id=int(primary["location_id"]),
            mode="MANUAL_ASSIGNED",
            person_id=dad,
        )
        with service.catalog.write() as connection:
            connection.execute(
                "INSERT INTO face_exclusions(face_id, person_id, operation_id, active, "
                "created_at) VALUES (?, ?, NULL, 1, 1)",
                (primary_anchor, uncle),
            )
        secondary_anchor = seed_anchor(
            service,
            asset_id=int(secondary["asset_id"]),
            sha256=str(secondary["sha256"]),
            location_id=int(secondary["location_id"]),
            mode="MANUAL_UNASSIGNED",
        )
        primary_new = write_generation(
            service,
            asset_id=int(primary["asset_id"]),
            sha256=str(primary["sha256"]),
            location_id=int(primary["location_id"]),
        )
        secondary_new = write_generation(
            service,
            asset_id=int(secondary["asset_id"]),
            sha256=str(secondary["sha256"]),
            location_id=int(secondary["location_id"]),
        )
        # The place of the primary asset is the one the search fixture already assigned, so a
        # person+place AND query on it has a real hit.
        places = PlaceService(service.catalog)
        place_state = places.asset_place(int(primary["asset_id"]))
        place_row = service.catalog.read_one(
            "SELECT place_id, canonical_name FROM places ORDER BY place_id LIMIT 1"
        )
        if place_state.get("assignment") is None and place_row is not None:
            # The search fixture assigns its place to whichever asset it indexed first; the
            # person+place AND step needs the *same* asset, so the primary one is assigned
            # explicitly through the real write path.
            places.write_assignment(
                asset_id=int(primary["asset_id"]),
                action="ASSIGN",
                target_place_id=int(place_row["place_id"]),
                request_id="face-mapping-smoke-place",
                expected_revision=0,
            )
            place_state = places.asset_place(int(primary["asset_id"]))
        groups = [
            {
                "old_face_id": str(row["old_face_id"]),
                "asset_id": int(row["asset_id"]),
                "candidate_state": str(row["candidate_state"]),
                "state": str(row["state"]),
                "revision": int(row["revision"]),
            }
            for row in service.catalog.read_all(
                "SELECT * FROM face_redetection_groups ORDER BY old_face_id"
            )
        ]
        summary = {
            "work_dir": str(work_dir),
            "library": str(search_root / search_fixture.LIBRARY_DIRNAME),
            "data_root": str(data_root),
            "persons": {"dad": dad, "uncle": uncle},
            "assets": {
                "primary": int(primary["asset_id"]),
                "secondary": int(secondary["asset_id"]),
            },
            "anchors": {
                "manual_assigned": primary_anchor,
                "manual_unassigned": secondary_anchor,
            },
            "new_faces": {"primary": primary_new, "secondary": secondary_new},
            "place": {
                "assigned_asset": int(primary["asset_id"]),
                "place_id": (
                    None
                    if place_state.get("assignment") is None
                    else int(place_state["assignment"]["place_id"])
                ),
                "assignment": place_state.get("assignment"),
            },
            "groups": groups,
            "start_command": (
                "uv run --no-sync python -m photo_search --port <free-port> "
                f'--data-root "{data_root}"'
            ),
            "pages": ["/persons", "/places", "/search"],
        }
    finally:
        service.close()
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(tempfile.gettempdir()) / "PhotoSearchFaceMappingSmokeRoot",
        help="working directory for the fixture (a %%TEMP%% folder by default)",
    )
    parser.add_argument("--reset", action="store_true", help="remove this script's own root first")
    args = parser.parse_args(list(argv) if argv is not None else None)
    root = args.root.resolve()
    if args.reset and root.is_dir():
        shutil.rmtree(root)
    summary = build(root)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
