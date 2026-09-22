"""P3-08D browser-smoke fixture: a searchable library plus a *paged* person directory.

The recorded real-browser closure of the task card needs one data root that already answers
four things: a plain search page with an activated ``SearchProfile`` (so the search surfaces
behave like the P2-07 checklist expects), at least two active canonical persons, more people
than one page of the picker holds (so "加载更多人物" is a real paged read), and one place
that is *also* owned by a person (so a person × place AND query has a real, checkable hit).

:mod:`tools.diagnostics.search_smoke_fixture` already builds the scanned library, the
diagnostic image generation and the manually assigned place; this script calls it first and
then seeds the person authority on top of it:

* ``爸爸`` - two photos, one of them the place-assigned one;
* ``叔叔`` - one photo, shared with ``爸爸``, so ``any`` and ``all`` differ;
* two unnamed people that both render the *same* label ("未命名人物") - they must stay two
  options with two distinct ids. The authority itself refuses two active canonical persons
  with the same ``name_key`` (P3-03 same-name merge), so an identical *display* label is the
  unnamed placeholder, not a duplicated name;
* enough unnamed people to push the directory past one page of 25.

It also runs the same conditions through the production ``search_request`` entry point and
records the exact hit sets, so the browser observations can be compared against an
authoritative API-level expectation instead of a hand-count.

Everything is synthetic and written under its own work directory (``%TEMP%`` by default);
``--reset`` only removes the directory this script owns. No real photo library, model weight
or network resource is touched, and neither output nor summary ever carries a name, a path,
a box or a vector.

Usage::

    uv run --no-sync python tools/diagnostics/search_person_smoke_fixture.py --reset
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tools" / "diagnostics"))

import search_smoke_fixture as search_fixture  # noqa: E402

from photo_search.face.contract import FACE_SPEC_REVISION  # noqa: E402
from photo_search.ingestion.service import CatalogService  # noqa: E402
from photo_search.persons.contracts import name_key_for  # noqa: E402
from photo_search.persons.service import PersonService  # noqa: E402
from photo_search.search.contracts import SearchRequest  # noqa: E402
from photo_search.search.service import search_request  # noqa: E402
from photo_search.search.sessions import SearchSessionStore  # noqa: E402

WORK_DIRNAME = "PhotoSearchSearchPersonSmoke"
#: The picker holds 25 people per page, so the directory needs a real second page.
FILLER_PERSONS = 26
CONTRACT_VERSION = "p2-contracts.v1"


def add_face(catalog: Any, serial: list[int], asset_id: int, *, ordinal: int = 0) -> str:
    """Seed one authoritative face row (no model, no detection run)."""
    serial[0] += 1
    face_id = hashlib.sha256(f"p3-08d-smoke-face-{serial[0]}".encode()).hexdigest()
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
            "input_revision, location_id, bbox_json, landmarks_json, det_score, face_pixels, "
            "blur_variance, quality_state, quality_reason, artifact_id, face_revision, "
            "created_at, updated_at) VALUES (?, ?, ?, 'det', ?, ?, 1, ?, "
            "'[[1,2],[2,3],[3,4],[4,5],[5,6]]', 0.9, 120.0, 200.0, 'OK', NULL, 'artifact', "
            "1, 1, 1)",
            (
                face_id,
                int(asset_id),
                str(FACE_SPEC_REVISION),
                int(ordinal),
                hashlib.sha256(f"rev-{face_id}".encode()).hexdigest(),
                json.dumps({"bbox": [0.0, 0.0, 10.0, 10.0]}),
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


def add_link(catalog: Any, face_id: str, person_id: int) -> None:
    """One confirmed effective member link, exactly what ``MEMBER_PREDICATE`` counts."""
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
            "confidence, reason, revision, last_operation_id, created_at, updated_at) "
            "VALUES (?, ?, 'MANUAL', 'CONFIRMED', 0.0, NULL, 1, NULL, 1, 1)",
            (str(face_id), int(person_id)),
        )


def build(root: Path) -> dict[str, Any]:
    work_dir = root / WORK_DIRNAME
    search_root = work_dir / "SearchFixture"
    work_dir.mkdir(parents=True, exist_ok=True)
    search_fixture.main(["--work-dir", str(search_root), "--photos", "6", "--reset", "--json"])
    data_root = search_root / search_fixture.DATA_ROOT_DIRNAME
    problems: list[str] = []
    service = CatalogService.open(data_root=data_root)
    try:
        catalog = service.catalog
        assets = [
            int(row["asset_id"])
            for row in catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id")
        ]
        if len(assets) < 3:
            raise SystemExit("the search fixture must produce at least three assets")
        assignment = catalog.read_one(
            "SELECT asset_id, place_id FROM asset_place_assignments ORDER BY asset_id LIMIT 1"
        )
        if assignment is None or assignment["place_id"] is None:
            raise SystemExit("the search fixture must have assigned one place to one asset")
        place_asset = int(assignment["asset_id"])
        place_id = int(assignment["place_id"])
        partner = next(value for value in assets if value != place_asset)

        serial = [0]
        dad = add_person(catalog, "爸爸")
        uncle = add_person(catalog, "叔叔")
        # Two unnamed people: both render the placeholder "未命名人物", so the picker has to
        # keep them apart by their ids. A real duplicated name cannot exist here - the person
        # authority has a unique active ``name_key`` and merges on a repeated name (P3-03).
        same_label_first = add_person(catalog)
        same_label_second = add_person(catalog)
        add_link(catalog, add_face(catalog, serial, place_asset), dad)
        add_link(catalog, add_face(catalog, serial, partner), dad)
        add_link(catalog, add_face(catalog, serial, partner, ordinal=1), uncle)
        add_link(catalog, add_face(catalog, serial, assets[2]), same_label_first)
        add_link(catalog, add_face(catalog, serial, assets[3]), same_label_second)
        for _ in range(FILLER_PERSONS):
            add_person(catalog)

        persons = PersonService(catalog, clock=catalog.now_ms)
        sessions = SearchSessionStore()

        def run(
            request_id: str,
            *,
            person_ids: Sequence[int] | None = None,
            person_match: str | None = None,
            place_ids: Sequence[int] | None = None,
            page_size: int = 50,
        ) -> tuple[list[int], dict[str, Any]]:
            body: dict[str, Any] = {
                "contract_version": CONTRACT_VERSION,
                "request_id": str(request_id),
                "page_size": int(page_size),
            }
            if person_ids:
                body["person_ids"] = [int(value) for value in person_ids]
            if person_match is not None:
                body["person_match"] = str(person_match)
            if place_ids:
                body["place_ids"] = [int(value) for value in place_ids]
            page = search_request(catalog, SearchRequest.from_envelope(body), sessions=sessions)
            return [int(hit["asset_id"]) for hit in page["hits"]], page

        dad_hits, dad_page = run("fixture-dad", person_ids=[dad])
        any_hits, _ = run("fixture-any", person_ids=[dad, uncle])
        all_hits, _ = run("fixture-all", person_ids=[dad, uncle], person_match="all")
        dad_place_hits, dad_place_page = run(
            "fixture-dad-place", person_ids=[dad], place_ids=[place_id]
        )
        no_person_hits, _ = run("fixture-none")

        expected = {
            "dad": sorted(dad_hits),
            "any_dad_uncle": sorted(any_hits),
            "all_dad_uncle": sorted(all_hits),
            "dad_and_place": sorted(dad_place_hits),
            "no_person_condition": sorted(no_person_hits),
        }
        if expected["dad_and_place"] != [place_asset]:
            problems.append(f"dad+place answered {expected['dad_and_place']}")
        if not set(expected["all_dad_uncle"]) <= set(expected["any_dad_uncle"]):
            problems.append("all is not a subset of any")
        if expected["all_dad_uncle"] == expected["any_dad_uncle"]:
            problems.append("the fixture cannot tell any from all")
        if int(persons.get_person(dad)["asset_count"]) != len(expected["dad"]):
            problems.append("the person read model and the search hit set disagree")
        if dad_page["filters"]["person_ids"] != [dad]:
            problems.append("the echo does not carry the canonical person id")
        if dad_place_page["filters"]["place_ids"] != [place_id]:
            problems.append("the echo does not carry the place id")

        summary = {
            "status": "ok" if not problems else "failed",
            "problems": problems,
            "work_dir": str(work_dir),
            "library": str(search_root / search_fixture.LIBRARY_DIRNAME),
            "data_root": str(data_root),
            "assets": sorted(assets),
            "persons": {
                "dad": dad,
                "uncle": uncle,
                "same_label": [same_label_first, same_label_second],
                "same_label_text": "未命名人物",
                "total": int(
                    catalog.read_one(
                        "SELECT COUNT(*) AS total FROM persons WHERE merged_into IS NULL"
                    )["total"]
                ),
            },
            "place": {"place_id": place_id, "asset_id": place_asset},
            "filters": {"person_ids": [dad], "person_match": "any", "place_ids": [place_id]},
            "expected": expected,
            "person_read": {
                "dad_asset_count": int(persons.get_person(dad)["asset_count"]),
                "uncle_asset_count": int(persons.get_person(uncle)["asset_count"]),
            },
            "start_command": (
                "uv run --no-sync python -m photo_search --port <free-port> "
                f'--data-root "{data_root}"'
            ),
            "pages": ["/search", "/persons"],
            "note": "synthetic fixture only; no real photo library is read",
        }
    finally:
        service.close()
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(tempfile.gettempdir()) / "PhotoSearchSearchPersonSmokeRoot",
        help="working directory for the fixture (a %%TEMP%% folder by default)",
    )
    parser.add_argument("--reset", action="store_true", help="remove this script's own root first")
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="also write the JSON summary to this file (evidence for the task report)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    root = args.root.resolve()
    if args.reset and root.is_dir():
        shutil.rmtree(root, ignore_errors=True)
    summary = build(root)
    payload = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload + "\n", encoding="utf-8", newline="\n")
    print(payload)
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
