"""P3-03 real-fixture smoke: group -> name -> merge -> human hold -> undo -> status.

Every picture is a public-domain fixture this project already depends on (``skimage``'s NASA
astronaut, InsightFace's own group photo and tight portrait, plus two scripted astronaut
variants). Everything is written under ``%TEMP%`` and removed afterwards; no real photo
library is ever read or written, and the pinned weights are only *copied* into the temporary
data root.

Usage::

    uv run --no-sync python tools/diagnostics/person_smoke_fixture.py --device cpu

Exit code 0 means every step produced the expected authoritative state; the JSON summary is
the recorded evidence (counts, statuses and codes only - never a name, a path or a vector).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from PIL import Image  # noqa: E402

from photo_search.catalog.db import Catalog  # noqa: E402
from photo_search.face import weights  # noqa: E402
from photo_search.face.index import FaceIndexCoordinator  # noqa: E402
from photo_search.face.service import catch_up_index, detect  # noqa: E402
from photo_search.ingestion.service import CatalogService  # noqa: E402
from photo_search.persons.service import PersonService  # noqa: E402
from photo_search.scheduler.projection import face_summary  # noqa: E402

WALL_BASE_MS = 1_700_000_000_000


def _stamp(path: Path) -> None:
    import os

    moment = (WALL_BASE_MS - 3_600_000) * 1_000_000
    os.utime(path, ns=(moment, moment))


def _astronaut() -> Image.Image:
    from skimage import data as skimage_data

    return Image.fromarray(skimage_data.astronaut()).convert("RGB")


def _insightface_image(name: str) -> Image.Image:
    import insightface

    path = Path(insightface.__file__).resolve().parent / "data" / "images" / name
    return Image.open(path).convert("RGB")


def write_library(library: Path) -> None:
    library.mkdir(parents=True, exist_ok=True)
    astronaut = _astronaut()
    astronaut.save(library / "astronaut.png")
    _stamp(library / "astronaut.png")
    _insightface_image("t1.jpg").save(library / "group.jpg", quality=95)
    _stamp(library / "group.jpg")
    _insightface_image("Tom_Hanks_54745.png").save(library / "portrait.png")
    _stamp(library / "portrait.png")
    import numpy as np

    values = np.clip(np.asarray(astronaut, dtype=np.float32) * 1.22 + 8.0, 0, 255)
    Image.fromarray(values.astype(np.uint8)).save(library / "astronaut_bright.jpg", quality=92)
    _stamp(library / "astronaut_bright.jpg")


def install_weights(data_root: Path) -> None:
    resolved = weights.resolve()
    target = weights.model_root(data_root)
    for entry in resolved.files:
        destination = target / entry.relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry.absolute_path, destination)


class Clock:
    def __init__(self, start: int = WALL_BASE_MS) -> None:
        self.ms = int(start)

    def __call__(self) -> int:
        return self.ms


def run(device: str) -> dict[str, Any]:
    problems: list[dict[str, Any]] = []
    workspace = Path(tempfile.mkdtemp(prefix="p303-smoke-"))
    try:
        data_root = workspace / "PhotoSearchData"
        library = workspace / "library"
        write_library(library)
        clock = Clock()
        if not weights.weights_present():
            return {
                "status": "skipped",
                "reason": "the pinned buffalo_l weights are not installed",
                "problems": [],
            }
        install_weights(data_root)
        service = CatalogService.open(data_root=data_root, clock=clock, lease_seconds=60)
        try:
            source = service.register_source(display_name="library", root_path=library)
            service.run_scan(int(source["source_id"]))
            catalog: Catalog = service.catalog
            detect(catalog, data_root=data_root, device=str(device))
            persons = PersonService(catalog, clock=clock)
            summary_before = face_summary(catalog.db_path, weights_present=True) or {}

            def person_of(relative: str) -> int:
                row = catalog.read_one(
                    "SELECT l.person_id AS person_id FROM face_person_links AS l "
                    "JOIN faces AS f ON f.face_id = l.face_id "
                    "JOIN file_locations AS loc ON loc.asset_id = f.asset_id "
                    "WHERE loc.relative_path = ? AND l.decision = 'CONFIRMED' "
                    "ORDER BY l.person_id LIMIT 1",
                    (str(relative),),
                )
                if row is None:
                    raise RuntimeError(f"{relative} produced no effective person group")
                return int(row["person_id"])

            def revision(person_id: int) -> int:
                """The revision a client reads: always the *canonical* person's revision."""
                return int(persons.get_person(int(person_id))["revision"])

            astronaut_person = person_of("astronaut.png")
            portrait_person = person_of("portrait.png")
            portrait_faces = [
                str(row["face_id"])
                for row in catalog.read_all(
                    "SELECT face_id FROM face_person_links WHERE person_id = ? "
                    "AND decision = 'CONFIRMED'",
                    (portrait_person,),
                )
            ]
            named = persons.rename(
                person_id=astronaut_person,
                name="宇航员",
                request_id="smoke-name-1",
                expected_revision=revision(astronaut_person),
            )
            merged = persons.rename(
                person_id=portrait_person,
                name="宇航员",
                request_id="smoke-name-2",
                expected_revision=revision(portrait_person),
            )
            undo_same_name = persons.undo(
                operation_id=merged["operation_id"],
                request_id="smoke-undo-same-name",
                expected_operation_revision=merged["operation_revision"],
            )
            held_face = portrait_faces[0]
            with catalog.write() as connection:
                connection.execute(
                    "INSERT INTO face_assignment_controls(face_id, mode, revision, "
                    "last_operation_id, updated_at) VALUES (?, 'MANUAL_UNASSIGNED', 1, "
                    "'op-human', 1)",
                    (held_face,),
                )
            explicit = persons.merge(
                target_person_id=astronaut_person,
                target_expected_revision=revision(astronaut_person),
                sources=[
                    {"person_id": portrait_person, "expected_revision": revision(portrait_person)}
                ],
                request_id="smoke-merge",
            )
            downgraded = catalog.read_one(
                "SELECT decision, reason FROM face_person_links WHERE face_id = ?",
                (held_face,),
            )
            index = FaceIndexCoordinator(catalog)
            catch_up_index(catalog, coordinator=index)
            undo = persons.undo(
                operation_id=explicit["operation_id"],
                request_id="smoke-undo",
                expected_operation_revision=explicit["operation_revision"],
            )
            restored = catalog.read_one(
                "SELECT decision, assignment_kind FROM face_person_links WHERE face_id = ?",
                (held_face,),
            )
            control = catalog.read_one(
                "SELECT mode FROM face_assignment_controls WHERE face_id = ?", (held_face,)
            )
            # The undo degrades the affected candidate entries again; the coordinator is what
            # brings them back to CONFIRMED without ever touching a vector.
            repaired = catch_up_index(catalog, coordinator=index)["published"]
            view = persons.get_person(portrait_person)
            summary = face_summary(catalog.db_path, weights_present=True) or {}

            def expect(condition: bool, code: str, **details: Any) -> None:
                if not condition:
                    problems.append({"code": code, **details})

            expect(named["operation_type"] == "RENAME", "rename_type")
            expect(merged["operation_type"] == "MERGE_BY_NAME", "same_name_merge_type")
            expect(undo_same_name["changed"] is True, "same_name_undo_changed")
            expect(explicit["operation_type"] == "MERGE", "explicit_merge_type")
            expect(
                (str(downgraded["decision"]), str(downgraded["reason"]))
                == ("SUGGESTED", "MANUAL_UNASSIGNED"),
                "human_hold_not_respected",
            )
            expect(undo["operation_type"] == "UNDO", "undo_type")
            expect(str(restored["decision"]) == "CONFIRMED", "undo_did_not_restore_link")
            expect(str(control["mode"]) == "MANUAL_UNASSIGNED", "human_row_lost")
            expect(view["redirected"] is False, "undo_left_a_redirect")
            expect(summary["named_persons"] == 1, "named_person_count")
            expect(summary["recent_operations"] == 5, "recent_operations_count")
            expect(summary["pending_confirmation"] == 0, "pending_confirmation_after_undo")
            return {
                "status": "ok" if not problems else "failed",
                "problems": problems,
                "detected": {
                    "assets": summary_before.get("library_assets"),
                    "faces": summary_before.get("faces"),
                    "unnamed_persons": summary_before.get("unnamed_persons"),
                },
                "operations": {
                    "rename": named["operation_type"],
                    "same_name_merge": merged["operation_type"],
                    "same_name_undo": undo_same_name["operation_type"],
                    "explicit_merge": explicit["operation_type"],
                    "undo": undo["operation_type"],
                    "replayed": [
                        named["replayed"],
                        merged["replayed"],
                        undo_same_name["replayed"],
                        explicit["replayed"],
                        undo["replayed"],
                    ],
                },
                "human_control": {
                    "mode": str(control["mode"]),
                    "downgraded_to": [str(downgraded["decision"]), str(downgraded["reason"])],
                    "restored_to": str(restored["decision"]),
                    "restored_kind": str(restored["assignment_kind"]),
                },
                "status_counts": {
                    "named_persons": summary.get("named_persons"),
                    "unnamed_persons": summary.get("unnamed_persons"),
                    "aliases": summary.get("aliases"),
                    "pending_confirmation": summary.get("pending_confirmation"),
                    "recent_operations": summary.get("recent_operations"),
                },
                "index": index.index_state()["entries"],
                "repaired_after_undo": repaired,
            }
        finally:
            service.close()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="cpu", choices=("auto", "cpu", "gpu"))
    arguments = parser.parse_args(argv)
    result = run(str(arguments.device))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in ("ok", "skipped") else 1


if __name__ == "__main__":  # pragma: no cover - a manual diagnostics entry point
    raise SystemExit(main())
