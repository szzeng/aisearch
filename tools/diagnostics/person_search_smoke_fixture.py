"""P3-07B real-fixture smoke: the person condition end to end, on a real scanned catalog.

Everything is written under ``%TEMP%`` and removed afterwards: no real photo library is
read, moved or deleted, no model weight is loaded and no network call is made. The pictures
are synthetic JPEGs produced by Pillow, the assets come from the *production* scanner, and
the person authority (persons, faces, links, controls, exclusions) is seeded as rows, so the
condition under test is the production predicate rather than a stand-in.

What this fixture proves that a unit test cannot: that ``person_ids=[p]`` answers exactly the
photos ``GET /api/v1/persons/{p}`` counts on a catalog built by the real scanner, that
``all``/``any`` compose with the other conditions, that a human lock/exclusion/suggestion
never becomes a membership, that a merge invalidates the pre-merge session with the frozen
stale code while a *new* search with the old id still answers correctly, and that a process
restart agrees with all of it.

Usage::

    uv run --no-sync python tools/diagnostics/person_search_smoke_fixture.py

Exit code 0 means ``problems`` is empty; the JSON summary is the recorded evidence (ids,
counts, revisions and codes only - never a name, a path, a box or a vector).
"""

from __future__ import annotations

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

from photo_search.catalog.errors import (  # noqa: E402
    CATALOG_FILTER_REVISION_STALE,
    SEARCH_SESSION_EXPIRED,
    CatalogError,
)
from photo_search.ingestion.service import CatalogService  # noqa: E402
from photo_search.persons.contracts import name_key_for  # noqa: E402
from photo_search.persons.service import PersonService  # noqa: E402
from photo_search.search.contracts import SearchRequest  # noqa: E402
from photo_search.search.service import search_request  # noqa: E402
from photo_search.search.sessions import SearchSessionStore  # noqa: E402

WALL_BASE_MS = 1_700_000_000_000
AGE_MS = 3_600_000
PHOTOS = ("杭州西湖 照片.jpg", "beach sunset.jpg", "IMG_0001.JPG")


def stamp(path: Path) -> None:
    """Give a fixture file an ``mtime`` already outside the scanner's stability window."""
    moment = (WALL_BASE_MS - AGE_MS) * 1_000_000
    os.utime(path, ns=(moment, moment))


def write_library(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for index, name in enumerate(PHOTOS):
        image = Image.new("RGB", (24, 18), (30 + index * 40, 60, 90))
        pixels = image.load()
        for x in range(image.size[0]):
            for y in range(image.size[1]):
                pixels[x, y] = ((30 + index * 7 + x) % 256, (60 + y) % 256, (90 + index) % 256)
        path = root / name
        image.save(path, "JPEG", quality=90)
        stamp(path)


class Clock:
    def __init__(self, start: int = WALL_BASE_MS) -> None:
        self.ms = int(start)

    def __call__(self) -> int:
        return self.ms

    def advance(self, delta_ms: int) -> None:
        self.ms += int(delta_ms)


def add_face(catalog: Any, serial: list[int], asset_id: int, *, ordinal: int = 0) -> str:
    from photo_search.face.contract import FACE_SPEC_REVISION

    serial[0] += 1
    face_id = hashlib.sha256(f"p3-07b-smoke-face-{serial[0]}".encode()).hexdigest()
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
            "input_revision, location_id, bbox_json, landmarks_json, det_score, face_pixels, "
            "blur_variance, quality_state, quality_reason, artifact_id, face_revision, "
            "created_at, updated_at) VALUES (?, ?, ?, 'det', ?, ?, 1, '[0,0,10,10]', "
            "'[[1,2],[2,3],[3,4],[4,5],[5,6]]', 0.9, 120.0, 200.0, 'OK', NULL, 'artifact', "
            "1, 1, 1)",
            (
                face_id,
                int(asset_id),
                str(FACE_SPEC_REVISION),
                int(ordinal),
                hashlib.sha256(f"rev-{face_id}".encode()).hexdigest(),
            ),
        )
    return face_id


def add_person(catalog: Any, name: str | None = None) -> int:
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
    reason: str | None = None,
) -> None:
    if decision == "SUGGESTED" and reason is None:
        reason = "BORDERLINE"
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
            "confidence, reason, revision, last_operation_id, created_at, updated_at) "
            "VALUES (?, ?, 'AUTO', ?, 0.9, ?, 1, NULL, 1, 1)",
            (str(face_id), int(person_id), str(decision), reason),
        )


def set_control(catalog: Any, face_id: str, mode: str) -> None:
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO face_assignment_controls(face_id, mode, revision, "
            "last_operation_id, updated_at) VALUES (?, ?, 1, 'op-control', 1)",
            (str(face_id), str(mode)),
        )


def set_exclusion(catalog: Any, face_id: str, person_id: int) -> None:
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO face_exclusions(face_id, person_id, operation_id, active, "
            "created_at) VALUES (?, ?, 'op-exclude', 1, 1)",
            (str(face_id), int(person_id)),
        )


def run_search(
    catalog: Any,
    sessions: SearchSessionStore,
    *,
    request_id: str,
    person_ids: list[int] | None = None,
    person_match: str | None = None,
    page_size: int = 50,
    search_session_id: str | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "contract_version": "p2-contracts.v1",
        "request_id": str(request_id),
        "page_size": int(page_size),
    }
    if person_ids is not None:
        body["person_ids"] = [int(value) for value in person_ids]
    if person_match is not None:
        body["person_match"] = str(person_match)
    if search_session_id is not None:
        body["search_session_id"] = str(search_session_id)
    if cursor is not None:
        body["cursor"] = str(cursor)
    return search_request(catalog, SearchRequest.from_envelope(body), sessions=sessions)


def hits_of(page: dict[str, Any]) -> list[int]:
    return [int(row["asset_id"]) for row in page["hits"]]


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="p3-07b-person-search-"))
    problems: list[str] = []
    report: dict[str, Any] = {}
    try:
        library = work / "library"
        data_root = work / "PhotoSearchData"
        write_library(library)
        clock = Clock()
        service = CatalogService.open(
            data_root=data_root,
            clock=clock,
            lease_seconds=60,
            wall_clock_ms=clock,
            stability_sleep=clock.advance,
        )
        try:
            source = service.register_source(display_name="library", root_path=library)
            service.run_scan(int(source["source_id"]))
            catalog = service.catalog
            assets = {
                str(row["relative_path"]).replace("\\", "/").rsplit("/", 1)[-1]: int(
                    row["asset_id"]
                )
                for row in catalog.read_all(
                    "SELECT asset_id, relative_path FROM file_locations ORDER BY location_id"
                )
            }
            if sorted(assets) != sorted(PHOTOS):
                problems.append(f"the scan produced unexpected assets: {sorted(assets)}")

            serial = [0]
            lake = assets["杭州西湖 照片.jpg"]
            sunset = assets["beach sunset.jpg"]
            other = assets["IMG_0001.JPG"]
            person_a = add_person(catalog)
            person_b = add_person(catalog)
            person_c = add_person(catalog)
            face_sunset_a = add_face(catalog, serial, sunset)
            face_lake_a = add_face(catalog, serial, lake)
            face_sunset_b = add_face(catalog, serial, sunset, ordinal=1)
            face_other_b = add_face(catalog, serial, other)
            face_other_a_denied = add_face(catalog, serial, other, ordinal=1)
            face_lake_c_suggested = add_face(catalog, serial, lake, ordinal=1)
            face_other_c_locked = add_face(catalog, serial, other, ordinal=2)
            add_link(catalog, face_sunset_a, person_a)
            add_link(catalog, face_lake_a, person_a)
            add_link(catalog, face_sunset_b, person_b)
            add_link(catalog, face_other_b, person_b)
            add_link(catalog, face_other_a_denied, person_a)
            set_exclusion(catalog, face_other_a_denied, person_a)
            add_link(catalog, face_lake_c_suggested, person_c, decision="SUGGESTED")
            add_link(catalog, face_other_c_locked, person_c)
            set_control(catalog, face_other_c_locked, "MANUAL_UNASSIGNED")

            persons = PersonService(catalog, clock=catalog.now_ms)
            sessions = SearchSessionStore()

            # --- the person hit set is the person read model's asset_count -------------
            counts: dict[str, Any] = {}
            for label, person_id in (("A", person_a), ("B", person_b), ("C", person_c)):
                page = run_search(
                    catalog, sessions, request_id=f"smoke-{label}", person_ids=[person_id]
                )
                expected = int(persons.get_person(person_id)["asset_count"])
                observed = len(hits_of(page))
                counts[label] = {"hit_count": observed, "asset_count": expected}
                if observed != expected:
                    problems.append(
                        f"person {label}: hit set {observed} != asset_count {expected}"
                    )
                for row in page["hits"]:
                    if row["hit_reason"] != "FILTER_ONLY" or row["matched_by"] != {
                        "filter": row["rank"]
                    }:
                        problems.append(f"person {label}: unexpected hit shape {row}")
            if set(hits_of(run_search(catalog, sessions, request_id="a1", person_ids=[person_a]))) != {
                sunset,
                lake,
            }:
                problems.append("person A does not own exactly the sunset and lake photos")
            if set(hits_of(run_search(catalog, sessions, request_id="c1", person_ids=[person_c]))) != set():
                problems.append("a suggestion + a MANUAL_UNASSIGNED face became a membership")

            # --- all / any --------------------------------------------------------------
            any_mode = run_search(
                catalog, sessions, request_id="any", person_ids=[person_a, person_b]
            )
            all_mode = run_search(
                catalog,
                sessions,
                request_id="all",
                person_ids=[person_a, person_b],
                person_match="all",
            )
            single_any = run_search(catalog, sessions, request_id="p1a", person_ids=[person_a])
            single_all = run_search(
                catalog,
                sessions,
                request_id="p1b",
                person_ids=[person_a],
                person_match="all",
            )
            if set(hits_of(any_mode)) != {sunset, lake, other}:
                problems.append(f"any(A,B) = {hits_of(any_mode)}")
            if set(hits_of(all_mode)) != {sunset}:
                problems.append(f"all(A,B) = {hits_of(all_mode)}")
            if hits_of(single_any) != hits_of(single_all):
                problems.append("|P| = 1 is not equivalent between all and any")

            # --- a merge invalidates the session and the old id keeps working ----------
            # The merge target is ``A`` and the source is ``B``. That ordering (target with
            # the lower person id) is the one the P3-03 compensation path supports today; the
            # reverse ordering is a pre-existing defect of the *person undo* family, reported
            # separately and untouched here because P3-07B may not change person semantics.
            first = run_search(
                catalog, sessions, request_id="pre-merge", person_ids=[person_b], page_size=1
            )
            before_revision = int(first["catalog_filter_revision"])
            merged = persons.merge(
                target_person_id=person_a,
                target_expected_revision=int(persons.get_person(person_a)["revision"]),
                sources=[
                    {
                        "person_id": person_b,
                        "expected_revision": int(persons.get_person(person_b)["revision"]),
                    }
                ],
                request_id="smoke-merge",
            )
            after_revision = int(
                catalog.read_one(
                    "SELECT revision FROM catalog_filter_revision WHERE revision_id = 1"
                )["revision"]
            )
            try:
                run_search(
                    catalog,
                    sessions,
                    request_id="pre-merge-next",
                    person_ids=[person_b],
                    page_size=1,
                    search_session_id=str(first["search_session_id"]),
                    cursor=str(first["next_cursor"]),
                )
                problems.append("the pre-merge continuation was not refused")
                stale_code = None
            except CatalogError as refusal:
                stale_code = str(refusal.code)
                if refusal.code != CATALOG_FILTER_REVISION_STALE:
                    problems.append(f"stale continuation answered {refusal.code}")
            if after_revision != before_revision + 1:
                problems.append(
                    f"a merge moved the filter revision by {after_revision - before_revision}"
                )
            old_id_page = run_search(
                catalog, sessions, request_id="post-merge", person_ids=[person_b]
            )
            canonical = int(persons.get_person(person_b)["person_id"])
            if canonical != person_a:
                problems.append("the merge target is not the surviving person")
            if set(hits_of(old_id_page)) != {sunset, lake, other}:
                problems.append(f"the merged person owns {hits_of(old_id_page)}")
            if old_id_page["filters"]["person_ids"] != [person_a]:
                problems.append("the echo does not carry the canonical person id")

            # --- the compensation restores the previous membership --------------------
            persons.undo(
                operation_id=str(merged["operation_id"]),
                request_id="smoke-undo",
                expected_operation_revision=int(merged["operation_revision"]),
            )
            restored = run_search(
                catalog, sessions, request_id="post-undo", person_ids=[person_b]
            )
            if set(hits_of(restored)) != {sunset, other}:
                problems.append(f"the undo did not restore person B: {hits_of(restored)}")

            # --- a restart re-reads the same authority --------------------------------
            service.close()
            service = CatalogService.open(
                data_root=data_root,
                clock=clock,
                lease_seconds=60,
                wall_clock_ms=clock,
                stability_sleep=clock.advance,
            )
            catalog = service.catalog
            restarted_sessions = SearchSessionStore()
            try:
                run_search(
                    catalog,
                    restarted_sessions,
                    request_id="restart-next",
                    person_ids=[person_a],
                    page_size=1,
                    search_session_id=str(first["search_session_id"]),
                    cursor=str(first["next_cursor"]),
                )
                problems.append("a restarted process served a pre-restart cursor")
                restart_code = None
            except CatalogError as refusal:
                restart_code = str(refusal.code)
                if refusal.code != SEARCH_SESSION_EXPIRED:
                    problems.append(f"a pre-restart cursor answered {refusal.code}")
            replayed = run_search(
                catalog, restarted_sessions, request_id="restart-search", person_ids=[person_a]
            )
            if set(hits_of(replayed)) != {sunset, lake}:
                problems.append(f"the restarted process answered {hits_of(replayed)}")

            report = {
                "status": "ok" if not problems else "failed",
                "problems": problems,
                "assets": {"sunset": sunset, "lake": lake, "other": other},
                "persons": {"A": person_a, "B": person_b, "C": person_c},
                "counts": counts,
                "any_ab": sorted(hits_of(any_mode)),
                "all_ab": sorted(hits_of(all_mode)),
                "merge": {
                    "operation_id": str(merged["operation_id"]),
                    "filter_revision_before": before_revision,
                    "filter_revision_after": after_revision,
                    "stale_code": stale_code,
                },
                "undo_hits": sorted(hits_of(restored)),
                "restart": {"stale_code": restart_code, "hits": sorted(hits_of(replayed))},
                "sessions": len(restarted_sessions),
            }
        finally:
            service.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
