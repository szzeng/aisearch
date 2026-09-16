"""P3-04B real-fixture smoke: group -> name -> one-face unassign -> assign -> restore -> undo -> restart.

Every picture is a public-domain fixture this project already depends on (``skimage``'s NASA
astronaut, InsightFace's own group photo and tight portrait, plus a scripted astronaut
variant). Everything is written under ``%TEMP%`` and removed afterwards; no real photo
library is ever read or written, and the pinned weights are only *copied* into the temporary
data root.

The point of this fixture is the part unit tests cannot prove: that a real detector run, a
real conservative association, the candidate index and a *process restart* all agree with
ADR-0007 - one corrected face changes, the other faces of the same photo do not, and a
denied person is never re-adopted.

Usage::

    uv run --no-sync python tools/diagnostics/face_correction_smoke_fixture.py --device cpu

Exit code 0 means every step produced the expected authoritative state; the JSON summary is
the recorded evidence (counts, modes and statuses only - never a name, a path or a vector).
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
from photo_search.face.index import FaceIndexCoordinator, reconcile  # noqa: E402
from photo_search.face.service import catch_up_index, detect  # noqa: E402
from photo_search.ingestion.service import CatalogService  # noqa: E402
from photo_search.persons.service import PersonService  # noqa: E402
from photo_search.scheduler.projection import face_summary  # noqa: E402

WALL_BASE_MS = 1_700_000_000_000
CONTRACT_VERSION = "p3-face-correction-contracts.v1"


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


def effective_person_of(catalog: Catalog, face_id: str) -> int | None:
    row = catalog.read_one(
        "SELECT person_id FROM face_person_links WHERE face_id = ? AND decision = 'CONFIRMED'",
        (str(face_id),),
    )
    return None if row is None else int(row["person_id"])


def control_of(service: PersonService, face_id: str) -> dict[str, Any]:
    return service.face_assignment(str(face_id))


def run(device: str) -> dict[str, Any]:
    problems: list[dict[str, Any]] = []
    workspace = Path(tempfile.mkdtemp(prefix="p3-04b-smoke-"))
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
        catalog: Catalog = service.catalog
        try:
            source = service.register_source(display_name="library", root_path=library)
            service.run_scan(int(source["source_id"]))
            detect(catalog, data_root=data_root, device=str(device))
            persons = PersonService(catalog, clock=clock)
            index = FaceIndexCoordinator(catalog)
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
                return int(persons.get_person(int(person_id))["revision"])

            def faces_of_asset(relative: str) -> list[str]:
                return [
                    str(row["face_id"])
                    for row in catalog.read_all(
                        "SELECT f.face_id AS face_id FROM faces AS f "
                        "JOIN file_locations AS loc ON loc.asset_id = f.asset_id "
                        "WHERE loc.relative_path = ? ORDER BY f.ordinal, f.face_id",
                        (str(relative),),
                    )
                ]

            # --- step 1: real detection grouped the library -------------------------
            astronaut_person = person_of("astronaut.png")
            portrait_person = person_of("portrait.png")
            portrait_faces = faces_of_asset("portrait.png")
            expect_initial = (
                int(summary_before.get("faces") or 0) > 0
                and int(summary_before.get("unnamed_persons") or 0) >= 2
            )
            named = persons.rename(
                person_id=astronaut_person,
                name="宇航员",
                request_id="smoke-name-1",
                expected_revision=revision(astronaut_person),
            )
            named_other = persons.rename(
                person_id=portrait_person,
                name="汤姆",
                request_id="smoke-name-2",
                expected_revision=revision(portrait_person),
            )
            # --- step 2: one explicit face is taken out of 汤姆 ----------------------
            target_face = portrait_faces[0]
            others_before = {
                face_id: (
                    control_of(persons, face_id)["mode"],
                    effective_person_of(catalog, face_id),
                )
                for face_id in portrait_faces[1:]
            }
            unassigned = persons.set_face_assignment(
                face_id=target_face,
                contract_version=CONTRACT_VERSION,
                action="unassign",
                target_person_id=None,
                expected_revision=0,
                request_id="smoke-face-unassign",
            )
            state_after_unassign = control_of(persons, target_face)
            effective_after_unassign = effective_person_of(catalog, target_face)
            # --- step 3: the same face is explicitly given to 宇航员 -----------------
            assigned = persons.set_face_assignment(
                face_id=target_face,
                contract_version=CONTRACT_VERSION,
                action="assign",
                target_person_id=astronaut_person,
                expected_revision=int(state_after_unassign["revision"]),
                request_id="smoke-face-assign",
            )
            state_after_assign = control_of(persons, target_face)
            effective_after_assign = effective_person_of(catalog, target_face)
            # --- step 4: hand the face back to the machine --------------------------
            restored = persons.set_face_assignment(
                face_id=target_face,
                contract_version=CONTRACT_VERSION,
                action="restore_auto",
                target_person_id=None,
                expected_revision=int(state_after_assign["revision"]),
                request_id="smoke-face-restore",
            )
            state_after_restore = control_of(persons, target_face)
            effective_after_restore = effective_person_of(catalog, target_face)
            # --- step 5: compensate the restore -------------------------------------
            undone = persons.undo(
                operation_id=restored["operation_id"],
                request_id="smoke-face-undo",
                expected_operation_revision=int(restored["operation_revision"]),
            )
            state_after_undo = control_of(persons, target_face)
            # --- step 6: the automatic path may run again, then the process restarts -
            catch_up_index(catalog, coordinator=index)
            with catalog.write() as connection:
                connection.execute(
                    "INSERT INTO face_association_queue(asset_id, spec_revision, "
                    "face_revision, queued_at) SELECT f.asset_id, f.spec_revision, 1, 1 "
                    "FROM faces AS f JOIN file_locations AS loc ON loc.asset_id = f.asset_id "
                    "WHERE loc.relative_path = 'portrait.png' LIMIT 1 "
                    "ON CONFLICT(asset_id) DO UPDATE SET queued_at = excluded.queued_at"
                )
            reassociation = reconcile(catalog, coordinator=index)
            service.close()
            reopened = Catalog.open(data_root=data_root, clock=clock, lease_seconds=60)
            try:
                restarted = PersonService(reopened, clock=clock)
                state_after_restart = restarted.face_assignment(target_face)
                summary = face_summary(reopened.db_path, weights_present=True) or {}
                restart_queue = reopened.read_one(
                    "SELECT COUNT(*) AS total FROM face_association_queue"
                )
                restart_candidate = reopened.read_one(
                    "SELECT state FROM face_index_entries WHERE face_id = ?", (target_face,)
                )
                others_after = {
                    face_id: (
                        control_of(restarted, face_id)["mode"],
                        effective_person_of(reopened, face_id),
                    )
                    for face_id in portrait_faces[1:]
                }
                effective_after_restart = effective_person_of(reopened, target_face)
            finally:
                reopened.close()

            def expect(condition: bool, code: str, **details: Any) -> None:
                if not condition:
                    problems.append({"code": code, **details})

            unassign_item = unassigned["items"][0]
            assign_item = assigned["items"][0]
            restore_item = restored["items"][0]
            undo_item = undone["items"][0]
            expect(expect_initial, "detection_produced_no_groups")
            expect(named["operation_type"] == "RENAME", "rename_type")
            expect(named_other["operation_type"] == "RENAME", "second_rename_type")
            expect(unassigned["operation_type"] == "FACE_UNASSIGN", "unassign_type")
            expect(unassign_item["mode"] == "MANUAL_UNASSIGNED", "unassign_mode")
            expect(
                state_after_unassign["active_exclusions"] == [portrait_person],
                "unassign_did_not_deny_the_old_person",
                exclusions=state_after_unassign["active_exclusions"],
            )
            expect(
                effective_after_unassign is None,
                "the_unassigned_face_kept_an_effective_link",
                person=effective_after_unassign,
            )
            expect(assigned["operation_type"] == "FACE_ASSIGN", "assign_type")
            expect(assign_item["mode"] == "MANUAL_ASSIGNED", "assign_mode")
            expect(
                assign_item["assigned_person_id"] == astronaut_person,
                "assign_target",
                assigned=assign_item["assigned_person_id"],
            )
            expect(
                effective_after_assign == astronaut_person,
                "assign_did_not_become_effective",
                person=effective_after_assign,
            )
            expect(
                restore_item["mode"] == "AUTO_ALLOWED",
                "restore_mode",
                mode=restore_item["mode"],
            )
            expect(
                state_after_restore["active_exclusions"] == [portrait_person],
                "restore_dropped_a_denial",
                exclusions=state_after_restore["active_exclusions"],
            )
            expect(
                state_after_restore["assigned_person_id"] is None,
                "restore_adopted_a_suggestion",
                assigned=state_after_restore["assigned_person_id"],
            )
            expect(
                effective_after_restore is None,
                "restore_left_an_effective_link",
                person=effective_after_restore,
            )
            expect(undone["operation_type"] == "UNDO", "undo_type")
            expect(undo_item["mode"] == "MANUAL_ASSIGNED", "undo_mode")
            expect(
                undo_item["assigned_person_id"] == astronaut_person,
                "undo_did_not_restore_the_manual_link",
                assigned=undo_item["assigned_person_id"],
            )
            expect(
                int(undo_item["revision"]) > int(restore_item["revision"]),
                "undo_did_not_move_the_revision_forward",
                before=restore_item["revision"],
                after=undo_item["revision"],
            )
            expect(
                others_before == others_after,
                "the_correction_leaked_to_another_face_of_the_same_photo",
                before=others_before,
                after=others_after,
            )
            expect(
                state_after_restart == state_after_undo,
                "the_correction_did_not_survive_the_restart",
                before=state_after_undo,
                after=state_after_restart,
            )
            expect(
                effective_after_restart == astronaut_person,
                "the_restart_lost_the_restored_manual_link",
                person=effective_after_restart,
            )
            expect(
                effective_after_restart != portrait_person,
                "the_denied_person_came_back",
                person=effective_after_restart,
            )
            expect(
                int(summary.get("manual_assigned_faces") or 0) >= 1,
                "manual_assigned_face_count",
                count=summary.get("manual_assigned_faces"),
            )
            expect(
                int(summary.get("faces_with_active_exclusions") or 0) >= 1,
                "active_exclusion_face_count",
                count=summary.get("faces_with_active_exclusions"),
            )
            expect(
                int(restart_queue["total"]) == 0,
                "the_reassociation_left_work_queued",
                queued=restart_queue["total"],
            )
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
                    "second_rename": named_other["operation_type"],
                    "unassign": unassigned["operation_type"],
                    "assign": assigned["operation_type"],
                    "restore_auto": restored["operation_type"],
                    "undo": undone["operation_type"],
                    "replayed": [
                        named["replayed"],
                        named_other["replayed"],
                        unassigned["replayed"],
                        assigned["replayed"],
                        restored["replayed"],
                        undone["replayed"],
                    ],
                },
                "single_face": {
                    "unassign_mode": unassign_item["mode"],
                    "unassign_revision": unassign_item["revision"],
                    "denied_after_unassign": state_after_unassign["active_exclusions"],
                    "assign_mode": assign_item["mode"],
                    "assign_revision": assign_item["revision"],
                    "assign_person_changed": (
                        assign_item["assigned_person_id"] == astronaut_person
                    ),
                    "restore_mode": restore_item["mode"],
                    "restore_kept_denials": state_after_restore["active_exclusions"],
                    "restore_assigned": state_after_restore["assigned_person_id"],
                    "undo_mode": undo_item["mode"],
                    "undo_revision": undo_item["revision"],
                },
                "siblings_unchanged": others_before == others_after,
                "restart": {
                    "mode": state_after_restart["mode"],
                    "revision": state_after_restart["revision"],
                    "assigned_person_id_matches": (
                        state_after_restart["assigned_person_id"]
                        == state_after_undo["assigned_person_id"]
                    ),
                    "denials": state_after_restart["active_exclusions"],
                },
                "reassociation": {
                    "processed": reassociation["processed"],
                    "late_control_conflicts": reassociation["late_control_conflicts"],
                    "human_controlled": reassociation["human_controlled"],
                },
                "candidate_state": (
                    None if restart_candidate is None else str(restart_candidate["state"])
                ),
                "status_counts": {
                    "manual_assigned_faces": summary.get("manual_assigned_faces"),
                    "manual_unassigned_faces": summary.get("manual_unassigned_faces"),
                    "faces_with_active_exclusions": summary.get(
                        "faces_with_active_exclusions"
                    ),
                    "active_face_exclusions": summary.get("active_face_exclusions"),
                },
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
