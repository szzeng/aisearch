"""P3-06 person-page fixture: a synthetic library plus deterministic person facts.

The person page reads an *existing* catalog, so the only thing a browser smoke needs is a
data root that already carries person data. Producing that data with the real detector would
require the pinned buffalo_l weights, which this machine does not have - and the P3-02 report
records that boundary. This fixture therefore seeds the *authority* rows directly (the same
way the P3-03/P3-04 suites do) on top of a genuinely scanned synthetic photo library, so the
page, the read APIs and the face-crop endpoint all work without a model.

Everything is written under ``%TEMP%`` (or ``--root``); ``--reset`` only removes the directory
this script created. No real photo library, no model download, no network.

Usage::

    uv run --no-sync python tools/diagnostics/persons_page_smoke_fixture.py --reset

The script prints the data root, the source root and the exact start command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from PIL import Image  # noqa: E402

from photo_search.face.contract import (  # noqa: E402
    FACE_DETECTOR_ID,
    FACE_SPEC_REVISION,
)
from photo_search.face.processor import face_id_for  # noqa: E402
from photo_search.ingestion.service import CatalogService  # noqa: E402
from photo_search.persons.contracts import name_key_for  # noqa: E402

WORK_DIRNAME = "PhotoSearchPersonsSmoke"
DATA_DIRNAME = "PhotoSearchData 人物 中文 空格"
LIBRARY_DIRNAME = "照片 库 人物 中文 空格"
WALL_BASE_MS = 1_700_000_000_000

#: ``(relative path, faces)``. A face carries its authoritative box and the person slot it is
#: linked to (``None`` leaves it linked to nobody, as an unreviewed detection that found no
#: confident match would be).
PHOTOS: tuple[tuple[str, tuple[dict[str, Any], ...]], ...] = (
    (
        "合照 001.jpg",
        (
            {"bbox": (40, 40, 160, 160), "person": "dad"},
            {"bbox": (220, 60, 340, 180), "person": "mom"},
            {"bbox": (400, 70, 520, 190), "person": None},
        ),
    ),
    ("单张 002.jpg", ({"bbox": (60, 60, 200, 200), "person": "dad"},)),
    (
        "聚会 003.jpg",
        (
            {"bbox": (30, 30, 140, 140), "person": "unnamed"},
            {"bbox": (200, 40, 310, 150), "person": "unnamed"},
            {"bbox": (380, 50, 490, 160), "person": None},
        ),
    ),
    (
        "街拍 004.jpg",
        (
            {"bbox": (50, 50, 170, 170), "person": None},
            {"bbox": (250, 60, 370, 180), "person": None},
        ),
    ),
    (
        "合影 005.jpg",
        (
            {"bbox": (35, 40, 145, 150), "person": "mom"},
            {"bbox": (210, 45, 320, 155), "person": "unnamed"},
        ),
    ),
    # Twelve one-face photos, each linked to its own unnamed group: enough rows for the
    # page's 10/25 page sizes to show a real "加载更多" continuation.
    *(
        (
            f"补充 {index:02d}.jpg",
            ({"bbox": (60, 60, 200, 200), "person": f"extra{index}"},),
        )
        for index in range(1, 13)
    ),
)


def _stamp(path: Path) -> None:
    moment = (WALL_BASE_MS - 3_600_000) * 1_000_000
    os.utime(path, ns=(moment, moment))


def write_library(library: Path) -> None:
    library.mkdir(parents=True, exist_ok=True)
    for index, (name, _faces) in enumerate(PHOTOS):
        image = Image.new("RGB", (640, 480), (60 + 7 * index, 90, 140))
        pixels = image.load()
        for x in range(640):
            for y in range(480):
                pixels[x, y] = (
                    (60 + 7 * index + x) % 256,
                    (90 + y) % 256,
                    (140 + index * 5 + (x + y) // 3) % 256,
                )
        image.save(library / name, "JPEG", quality=88)
        _stamp(library / name)


def seed_faces(service: CatalogService, library: Path) -> dict[str, Any]:
    rows = service.catalog.read_all(
        "SELECT a.asset_id AS asset_id, a.sha256 AS sha256, l.relative_path AS relative_path, "
        "       l.location_id AS location_id "
        "  FROM assets AS a JOIN file_locations AS l ON l.asset_id = a.asset_id "
        " WHERE l.availability = 'PRESENT' ORDER BY l.relative_path"
    )
    by_path = {str(row["relative_path"]).replace("\\", "/"): row for row in rows}
    faces: list[dict[str, Any]] = []
    for name, entries in PHOTOS:
        row = by_path[name]
        for ordinal, face in enumerate(entries):
            bbox = [float(value) for value in face["bbox"]]
            face_id = face_id_for(
                input_revision=str(row["sha256"]), ordinal=ordinal, bbox=bbox
            )
            with service.catalog.write() as connection:
                connection.execute(
                    "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
                    "input_revision, location_id, bbox_json, landmarks_json, det_score, "
                    "face_pixels, blur_variance, quality_state, quality_reason, artifact_id, "
                    "face_revision, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.91, ?, 210.0, 'OK', NULL, "
                    "'artifact', 1, 1, 1)",
                    (
                        face_id,
                        int(row["asset_id"]),
                        str(FACE_SPEC_REVISION),
                        str(FACE_DETECTOR_ID),
                        ordinal,
                        str(row["sha256"]),
                        int(row["location_id"]),
                        json.dumps(bbox),
                        json.dumps([[bbox[0], bbox[1]], [bbox[2], bbox[1]]]),
                        round((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]), 2),
                    ),
                )
            faces.append({"face_id": face_id, "person": face.get("person")})
    return {"faces": faces, "asset_count": len(rows)}


def seed_people(service: CatalogService, faces: list[dict[str, Any]]) -> dict[str, int]:
    named = {"dad": "爸爸", "mom": "妈妈"}
    people: dict[str, int] = {}
    with service.catalog.write() as connection:
        for slot, name in named.items():
            cursor = connection.execute(
                "INSERT INTO persons(name, name_key, avatar_face_id, merged_into, revision, "
                "created_at, updated_at) VALUES (?, ?, NULL, NULL, 1, 1, 1)",
                (name, name_key_for(name)),
            )
            people[slot] = int(cursor.lastrowid)
        cursor = connection.execute(
            "INSERT INTO persons(name, name_key, avatar_face_id, merged_into, revision, "
            "created_at, updated_at) VALUES (NULL, NULL, NULL, NULL, 1, 1, 1)"
        )
        people["unnamed"] = int(cursor.lastrowid)
        for slot in sorted({face["person"] for face in faces if str(face["person"]).startswith("extra")}):
            cursor = connection.execute(
                "INSERT INTO persons(name, name_key, avatar_face_id, merged_into, revision, "
                "created_at, updated_at) VALUES (NULL, NULL, NULL, NULL, 1, 1, 1)"
            )
            people[str(slot)] = int(cursor.lastrowid)
    with service.catalog.write() as connection:
        for index, face in enumerate(faces):
            slot = face["person"]
            if slot is None:
                continue
            if str(slot).startswith("extra"):
                connection.execute(
                    "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
                    "confidence, reason, revision, last_operation_id, created_at, updated_at) "
                    "VALUES (?, ?, 'AUTO', 'CONFIRMED', 0.81, NULL, 1, NULL, 1, 1)",
                    (str(face["face_id"]), int(people[str(slot)])),
                )
                continue
            connection.execute(
                "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
                "confidence, reason, revision, last_operation_id, created_at, updated_at) "
                "VALUES (?, ?, 'AUTO', 'CONFIRMED', 0.93, NULL, 1, NULL, 1, 1)",
                (str(face["face_id"]), int(people[slot])),
            )
            if slot == "dad" and index == 0:
                connection.execute(
                    "UPDATE persons SET avatar_face_id = ? WHERE person_id = ?",
                    (str(face["face_id"]), int(people["dad"])),
                )
            if slot == "mom" and index == 1:
                connection.execute(
                    "UPDATE persons SET avatar_face_id = ? WHERE person_id = ?",
                    (str(face["face_id"]), int(people["mom"])),
                )
    return people


def lock_one_face(service: CatalogService, faces: list[dict[str, Any]]) -> str:
    """Take one linked face out of every automatic path, exactly like the frozen unassign."""
    target = next(face for face in faces if face["person"] == "unnamed")
    with service.catalog.write() as connection:
        row = connection.execute(
            "SELECT person_id FROM face_person_links WHERE face_id = ? AND decision = 'CONFIRMED'",
            (str(target["face_id"]),),
        ).fetchone()
        person_id = int(row["person_id"])
        connection.execute(
            "DELETE FROM face_person_links WHERE face_id = ? AND decision = 'CONFIRMED'",
            (str(target["face_id"]),),
        )
        connection.execute(
            "INSERT INTO face_assignment_controls(face_id, mode, revision, last_operation_id, "
            "updated_at) VALUES (?, 'MANUAL_UNASSIGNED', 1, NULL, 1)",
            (str(target["face_id"]),),
        )
        connection.execute(
            "INSERT INTO face_exclusions(face_id, person_id, operation_id, active, created_at) "
            "VALUES (?, ?, NULL, 1, 1)",
            (str(target["face_id"]), person_id),
        )
    return str(target["face_id"])


def build(root: Path) -> dict[str, Any]:
    library = root / LIBRARY_DIRNAME
    data_root = root / DATA_DIRNAME
    write_library(library)
    service = CatalogService.open(data_root=data_root)
    try:
        service.register_source(display_name="合成 人物库", root_path=library)
        source = service.list_sources()[0]
        service.run_scan(int(source["source_id"]))
        seeded = seed_faces(service, library)
        people = seed_people(service, seeded["faces"])
        locked = lock_one_face(service, seeded["faces"])
        summary = {
            "root": str(root),
            "data_root": str(data_root),
            "library": str(library),
            "assets": seeded["asset_count"],
            "faces": len(seeded["faces"]),
            "persons": len(people),
            "manual_unassigned_face": locked,
            "digest": hashlib.sha256(json.dumps(people, sort_keys=True).encode()).hexdigest()[:12],
        }
    finally:
        service.close()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(tempfile.gettempdir()) / WORK_DIRNAME,
        help="working directory for the fixture (a %%TEMP%% folder by default)",
    )
    parser.add_argument("--reset", action="store_true", help="remove this script's own root first")
    args = parser.parse_args()

    root = args.root.resolve()
    if args.reset and root.is_dir():
        shutil.rmtree(root)
    summary = build(root)
    summary["start_command"] = (
        f'uv run --no-sync python -m photo_search --port <free-port> '
        f'--data-root "{summary["data_root"]}"'
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
