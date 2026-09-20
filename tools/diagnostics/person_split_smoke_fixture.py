"""P3-05B real-fixture smoke: detect -> merge -> name -> split -> restart -> undo -> restart.

Everything is written under ``%TEMP%`` and removed afterwards; no real photo library is ever
read or written, and the pinned weights are only *copied* into the temporary data root. The
pictures are public-domain fixtures this project already depends on (InsightFace's own group
photo), so the fixture needs no network access.

The point of this fixture is what unit tests cannot prove: that a real detector run and a
real conservative association produce the authority rows, that a real split of one face of a
real group leaves its photo neighbours alone, and that a *process restart* agrees with
ADR-0008 both after the split and after the compensating undo.

Usage::

    uv run --no-sync python tools/diagnostics/person_split_smoke_fixture.py --device cpu

Exit code 0 means ``problems`` is empty; the JSON summary is the recorded evidence (ids,
modes, revisions and counts only - never a name, a path, a box or a vector).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from photo_search.catalog.db import Catalog  # noqa: E402
from photo_search.face import weights  # noqa: E402
from photo_search.face.service import detect  # noqa: E402
from photo_search.ingestion.service import CatalogService  # noqa: E402
from photo_search.persons.service import PersonService  # noqa: E402
from photo_search.scheduler.projection import face_summary  # noqa: E402

WALL_BASE_MS = 1_700_000_000_000
CONTRACT_VERSION = "p3-person-split-contracts.v1"
GROUP_PHOTO = "group.jpg"


def _stamp(path: Path) -> None:
    moment = (WALL_BASE_MS - 3_600_000) * 1_000_000
    os.utime(path, ns=(moment, moment))


def write_library(library: Path) -> None:
    """One public-domain group photo with several faces in it."""
    import insightface
    from PIL import Image

    library.mkdir(parents=True, exist_ok=True)
    source = Path(insightface.__file__).resolve().parent / "data" / "images" / "t1.jpg"
    Image.open(source).convert("RGB").save(library / GROUP_PHOTO, quality=95)
    _stamp(library / GROUP_PHOTO)


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


def open_repository(data_root: Path, clock: Clock) -> tuple[Any, Catalog, PersonService]:
    """A fresh ``CatalogService``/``Catalog``/``PersonService`` over one data root.

    Each call *reopens* the SQLite database, which is how the fixture models a process
    restart: nothing is cached between the two readings.
    """
    service = CatalogService.open(data_root=data_root, clock=clock, lease_seconds=60)
    catalog: Catalog = service.catalog
    return service, catalog, PersonService(catalog, clock=clock)


def face_rows(catalog: Catalog, relative_path: str) -> list[Any]:
    return catalog.read_all(
        "SELECT f.face_id AS face_id, f.face_pixels AS face_pixels FROM faces AS f "
        "JOIN file_locations AS loc ON loc.asset_id = f.asset_id "
        "WHERE loc.relative_path = ? ORDER BY f.face_pixels DESC, f.face_id",
        (str(relative_path),),
    )


def effective_person_of(catalog: Catalog, face_id: str) -> int | None:
    row = catalog.read_one(
        "SELECT person_id FROM face_person_links WHERE face_id = ? AND decision = 'CONFIRMED'",
        (str(face_id),),
    )
    return None if row is None else int(row["person_id"])


def effective_link_of(catalog: Catalog, face_id: str) -> Any:
    return catalog.read_one(
        "SELECT * FROM face_person_links WHERE face_id = ? AND decision = 'CONFIRMED'",
        (str(face_id),),
    )


def control_revision(catalog: Catalog, face_id: str) -> int:
    row = catalog.read_one(
        "SELECT revision FROM face_assignment_controls WHERE face_id = ?", (str(face_id),)
    )
    return 0 if row is None else int(row["revision"])


def members_of(catalog: Catalog, person_id: int) -> set[str]:
    return {
        str(row["face_id"])
        for row in catalog.read_all(
            "SELECT l.face_id AS face_id FROM face_person_links AS l "
            "WHERE l.person_id = ? AND l.decision = 'CONFIRMED'",
            (int(person_id),),
        )
    }


def person_row(catalog: Catalog, person_id: int) -> Any:
    return catalog.read_one("SELECT * FROM persons WHERE person_id = ?", (int(person_id),))


def run(device: str) -> dict[str, Any]:
    problems: list[dict[str, Any]] = []
    workspace = Path(tempfile.mkdtemp(prefix="p3-05b-smoke-"))
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
        service, catalog, persons = open_repository(data_root, clock)
        try:
            source = service.register_source(display_name="library", root_path=library)
            service.run_scan(int(source["source_id"]))
            detect(catalog, data_root=data_root, device=str(device))
            faces = [str(row["face_id"]) for row in face_rows(catalog, GROUP_PHOTO)]
            if len(faces) < 2:
                return {
                    "status": "failed",
                    "reason": "the group photo did not produce two faces",
                    "problems": [{"step": "detect", "faces": len(faces)}],
                }
            target, sibling = faces[0], faces[1]
            target_person = effective_person_of(catalog, target)
            sibling_person = effective_person_of(catalog, sibling)
            if target_person is None or sibling_person is None:
                return {
                    "status": "failed",
                    "reason": "the association did not group every face of the photo",
                    "problems": [{"step": "associate"}],
                }
            # --- real person creation: merge two detected groups into one -------------
            if int(target_person) != int(sibling_person):
                persons.merge(
                    target_person_id=int(target_person),
                    target_expected_revision=int(
                        person_row(catalog, int(target_person))["revision"]
                    ),
                    sources=[
                        {
                            "person_id": int(sibling_person),
                            "expected_revision": int(
                                person_row(catalog, int(sibling_person))["revision"]
                            ),
                        }
                    ],
                    request_id="smoke-merge-1",
                )
            named = persons.rename(
                person_id=int(target_person),
                name="分组",
                request_id="smoke-name-1",
                expected_revision=int(person_row(catalog, int(target_person))["revision"]),
            )
            source_members_before = members_of(catalog, int(target_person))
            asset_row = catalog.read_one(
                "SELECT asset_id FROM file_locations WHERE relative_path = ?",
                (GROUP_PHOTO,),
            )
            asset_id = int(asset_row["asset_id"])
            filter_before = int(
                catalog.read_one(
                    "SELECT revision FROM catalog_filter_revision WHERE revision_id = 1"
                )["revision"]
            )
            outbox_before = int(
                catalog.read_one("SELECT COUNT(*) AS total FROM index_outbox")["total"]
            )
            siblings_before = {
                face_id: (
                    effective_person_of(catalog, face_id),
                    int(effective_link_of(catalog, face_id)["revision"]),
                    control_revision(catalog, face_id),
                )
                for face_id in faces
                if face_id != target
            }
            # --- split one explicit face out of the named group -----------------------
            answer = persons.split(
                source_person_id=int(target_person),
                contract_version=CONTRACT_VERSION,
                source_expected_revision=int(
                    person_row(catalog, int(target_person))["revision"]
                ),
                faces=[
                    {
                        "face_id": target,
                        "expected_control_revision": control_revision(catalog, target),
                        "expected_assignment_revision": int(
                            effective_link_of(catalog, target)["revision"]
                        ),
                    }
                ],
                request_id="smoke-split-1",
            )
            created = int(answer["new_person_id"])
            after_split = {
                "operation_type": answer["operation_type"],
                "replayed": answer["replayed"],
                "created_unnamed": person_row(catalog, created)["name"] is None,
                "created_members": sorted(members_of(catalog, created)),
                "source_members": sorted(members_of(catalog, int(target_person))),
                "source_named": named["operation_type"] in ("RENAME", "MERGE_BY_NAME"),
                "target_mode": persons.face_assignment(target)["mode"],
                "target_exclusions": persons.face_assignment(target)["active_exclusions"],
                "filter_delta": int(
                    catalog.read_one(
                        "SELECT revision FROM catalog_filter_revision WHERE revision_id = 1"
                    )["revision"]
                )
                - filter_before,
                "queue_cause_for_asset": str(
                    catalog.read_one(
                        "SELECT cause FROM search_projection_queue WHERE asset_id = ?",
                        (asset_id,),
                    )["cause"]
                ),
                "image_outbox_delta": int(
                    catalog.read_one("SELECT COUNT(*) AS total FROM index_outbox")["total"]
                )
                - outbox_before,
            }
        finally:
            service.close()

        # --- restart #1: everything above must be durable --------------------------
        service, catalog, persons = open_repository(data_root, clock)
        try:
            restarted_split = {
                "created_still_resolves": persons.canonical_person_id(created) == created,
                "created_members": sorted(members_of(catalog, created)),
                "source_members": sorted(members_of(catalog, int(target_person))),
                "target_mode": persons.face_assignment(target)["mode"],
                "target_exclusions": persons.face_assignment(target)["active_exclusions"],
                "siblings_unchanged": siblings_before
                == {
                    face_id: (
                        effective_person_of(catalog, face_id),
                        int(effective_link_of(catalog, face_id)["revision"]),
                        control_revision(catalog, face_id),
                    )
                    for face_id in faces
                    if face_id != target
                },
                "source_name_key_present": person_row(catalog, int(target_person))["name_key"]
                is not None,
            }
            # --- compensating undo ---------------------------------------------------
            undone = persons.undo(
                operation_id=str(answer["operation_id"]),
                request_id="smoke-undo-1",
                expected_operation_revision=int(answer["operation_revision"]),
            )
            after_undo = {
                "operation_type": undone["operation_type"],
                "replayed": undone["replayed"],
                "created_redirects_to_source": persons.canonical_person_id(created)
                == int(target_person),
                "created_avatar_cleared": person_row(catalog, created)["avatar_face_id"]
                is None,
                "source_members_restored": sorted(members_of(catalog, int(target_person)))
                == sorted(source_members_before),
                "target_mode": persons.face_assignment(target)["mode"],
                "target_exclusions": persons.face_assignment(target)["active_exclusions"],
                "source_revision": int(person_row(catalog, int(target_person))["revision"]),
                "created_revision": int(person_row(catalog, created)["revision"]),
            }
        finally:
            service.close()

        # --- restart #2: the compensation is durable too ----------------------------
        service, catalog, persons = open_repository(data_root, clock)
        try:
            summary = face_summary(catalog.db_path, weights_present=True) or {}
            restarted_undo = {
                "created_redirects_to_source": persons.canonical_person_id(created)
                == int(target_person),
                "source_members_restored": sorted(members_of(catalog, int(target_person)))
                == sorted(source_members_before),
                "target_mode": persons.face_assignment(target)["mode"],
                "target_exclusions": persons.face_assignment(target)["active_exclusions"],
                "original_state": str(
                    catalog.read_one(
                        "SELECT state FROM person_operations WHERE operation_id = ?",
                        (str(answer["operation_id"]),),
                    )["state"]
                ),
                "operations": int(
                    catalog.read_one("SELECT COUNT(*) AS total FROM person_operations")["total"]
                ),
                "summary": {
                    "named_persons": summary.get("named_persons"),
                    "unnamed_persons": summary.get("unnamed_persons"),
                    "manual_assigned_faces": summary.get("manual_assigned_faces"),
                    "faces_with_active_exclusions": summary.get(
                        "faces_with_active_exclusions"
                    ),
                },
            }
        finally:
            service.close()

        # --- the expectations, stated once -----------------------------------------
        if after_split["operation_type"] != "SPLIT" or after_split["replayed"] is not False:
            problems.append({"step": "split", "got": after_split["operation_type"]})
        if not after_split["created_unnamed"]:
            problems.append({"step": "split", "reason": "the created person carries a name"})
        if not after_split["source_named"]:
            problems.append({"step": "split", "reason": "the source lost its name"})
        if after_split["created_members"] != [target]:
            problems.append({"step": "split", "reason": "created members"})
        if sorted(after_split["source_members"]) != sorted(
            source_members_before - {target}
        ):
            problems.append({"step": "split", "reason": "source members"})
        if after_split["target_mode"] != "MANUAL_ASSIGNED":
            problems.append({"step": "split", "reason": "target control mode"})
        if after_split["target_exclusions"] != [int(target_person)]:
            problems.append({"step": "split", "reason": "target exclusions"})
        if after_split["filter_delta"] != 1:
            problems.append({"step": "split", "reason": "filter revision delta"})
        if after_split["queue_cause_for_asset"] != "PERSON_CHANGED":
            problems.append({"step": "split", "reason": "search queue cause"})
        if after_split["image_outbox_delta"] != 0:
            problems.append({"step": "split", "reason": "image vector outbox written"})
        for key in ("created_still_resolves", "siblings_unchanged", "source_name_key_present"):
            if not restarted_split[key]:
                problems.append({"step": "restart-after-split", "reason": key})
        if restarted_split["created_members"] != [target]:
            problems.append({"step": "restart-after-split", "reason": "created members"})
        if after_undo["operation_type"] != "UNDO" or after_undo["replayed"] is not False:
            problems.append({"step": "undo", "got": after_undo["operation_type"]})
        for key in (
            "created_redirects_to_source",
            "created_avatar_cleared",
            "source_members_restored",
        ):
            if not after_undo[key]:
                problems.append({"step": "undo", "reason": key})
        if after_undo["target_mode"] != "AUTO_ALLOWED":
            problems.append({"step": "undo", "reason": "restored control mode"})
        if after_undo["target_exclusions"]:
            problems.append({"step": "undo", "reason": "denial not lifted"})
        if after_undo["source_revision"] < 3 or after_undo["created_revision"] < 2:
            problems.append({"step": "undo", "reason": "revisions did not move forward"})
        for key in (
            "created_redirects_to_source",
            "source_members_restored",
        ):
            if not restarted_undo[key]:
                problems.append({"step": "restart-after-undo", "reason": key})
        if restarted_undo["target_mode"] != "AUTO_ALLOWED" or restarted_undo[
            "target_exclusions"
        ]:
            problems.append({"step": "restart-after-undo", "reason": "restored control"})
        if restarted_undo["original_state"] != "UNDONE":
            problems.append({"step": "restart-after-undo", "reason": "original operation state"})

        return {
            "status": "ok" if not problems else "failed",
            "contract_version": CONTRACT_VERSION,
            "device": str(device),
            "problems": problems,
            "split": after_split,
            "restart_after_split": restarted_split,
            "undo": after_undo,
            "restart_after_undo": restarted_undo,
        }
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
