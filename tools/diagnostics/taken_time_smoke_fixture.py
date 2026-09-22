"""P4-00B browser-smoke fixture: a searchable library whose capture times are real.

The recorded browser closure of ADR-0011 §10 needs one data root that answers five things:

* photos whose EXIF *original/digitized* time is known, one of them with a recorded offset
  (``+08:00``) and one without any offset;
* a photo whose only EXIF date is the *root* IFD time (``EXIF_ROOT`` -> shown as 文件时间);
* photos with no EXIF date at all (``FILE_MTIME`` -> the ``capture_desc`` tail bucket);
* a scanned, vectorised, place-assigned library with an activated ``SearchProfile``, so the
  search page behaves exactly like the P2-07 checklist expects;
* the exact expected id sets, recorded through the *production* ``asset_page`` and
  ``search_request`` entry points instead of a hand count.

It never starts the server, never opens a browser and never reads a real photo library.
Everything is synthetic and written under its own work directory; ``--reset`` removes only
that directory.

Usage::

    uv run --no-sync python tools/diagnostics/taken_time_smoke_fixture.py --reset

The capture-time extraction itself runs through the production scheduler (the same service
the CLI uses). ``--rules-version 99`` then flips the library-level rule version *after* the
extraction, which is how the checklist observes the honest ``PENDING`` state: the rows stay
in the catalog, they are simply not trusted and not filtered on (ADR-0011 §2).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tools" / "diagnostics"))

import search_smoke_fixture as search_fixture  # noqa: E402

from photo_search.api.queries import asset_page  # noqa: E402
from photo_search.ingestion.service import CatalogService  # noqa: E402
from photo_search.search.contracts import SearchRequest  # noqa: E402
from photo_search.search.service import search_request  # noqa: E402
from photo_search.search.sessions import SearchSessionStore  # noqa: E402
from photo_search.taken_time import contract  # noqa: E402
from photo_search.taken_time import extract as time_extract  # noqa: E402
from photo_search.taken_time.service import (  # noqa: E402
    enqueue_targets,
    run_queue,
    taken_time_targets,
)

WORK_DIRNAME = "P4-00B-拍摄时间 浏览器验收"
AGE_MS = 3_600_000

#: The EXIF plan, by photo index (``seed_library`` names index 0/1 after the query words).
_ORIGINAL_WITH_OFFSET = 0
_ORIGINAL_WITHOUT_OFFSET = 1
_DIGITIZED_WITH_OFFSET = 2
_ROOT_TIME_ONLY = 3

_PLAN: dict[int, dict[str, str | None]] = {
    _ORIGINAL_WITH_OFFSET: {
        "original": "2022:07:05 18:31:07",
        "offset_original": "+08:00",
        "make": "SmokeMake",
        "model": "SmokeModel",
    },
    _ORIGINAL_WITHOUT_OFFSET: {"original": "2022:07:05 18:31:07"},
    _DIGITIZED_WITH_OFFSET: {
        "digitized": "2021:03:04 05:06:07",
        "offset_digitized": "-05:30",
    },
    _ROOT_TIME_ONLY: {"root": "2019:01:02 03:04:05"},
}


def _stamp(path: Path) -> None:
    moment = (int(time.time() * 1000) - AGE_MS) * 1_000_000
    os.utime(path, ns=(moment, moment))


def _write_jpeg_with_exif(path: Path, *, index: int) -> None:
    """The P2-07 writer plus the frozen EXIF time fields of ADR-0011 §4.1."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (96, 72), (index % 256, (index * 37) % 256, 90))
    pixels = image.load()
    for x in range(image.size[0]):
        for y in range(image.size[1]):
            pixels[x, y] = ((x + index) % 256, (y * 3) % 256, (90 + index) % 256)
    plan = _PLAN.get(int(index))
    if not plan:
        image.save(path, "JPEG", quality=85)
        _stamp(path)
        return
    exif = Image.Exif()
    if plan.get("root"):
        exif[0x0132] = str(plan["root"])
    if plan.get("make"):
        exif[0x010F] = str(plan["make"])
    if plan.get("model"):
        exif[0x0110] = str(plan["model"])
    sub: dict[int, Any] = {}
    for tag, key in ((0x9003, "original"), (0x9004, "digitized"), (0x9011, "offset_original"),
                     (0x9012, "offset_digitized"), (0x9291, "subsec_original"),
                     (0x9292, "subsec_digitized")):
        if plan.get(key):
            sub[tag] = str(plan[key])
    if sub:
        exif[0x8769] = sub
    image.save(path, "JPEG", quality=85, exif=exif)
    _stamp(path)


def build(
    work_dir: Path, *, photos: int, rules_version: int, reset: bool = False
) -> dict[str, Any]:
    library = work_dir / search_fixture.LIBRARY_DIRNAME
    data_root = work_dir / search_fixture.DATA_ROOT_DIRNAME
    existing = (data_root / "catalog" / "catalog.db").is_file()
    original_writer = search_fixture._write_jpeg
    problem = ""
    search_report: dict[str, Any] = {}
    reused = bool(existing) and not reset
    if not reused:
        arguments = [
            "--work-dir",
            str(work_dir),
            "--photos",
            str(max(6, int(photos))),
            "--json",
        ]
        if reset:
            arguments.append("--reset")
        try:
            search_fixture._write_jpeg = _write_jpeg_with_exif
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = search_fixture.main(arguments)
            if code != 0:
                problem = "the search fixture could not be built"
            search_report = json.loads(buffer.getvalue())
        finally:
            search_fixture._write_jpeg = original_writer

    if problem:
        return {"status": "failed", "problems": [problem], "work_dir": str(work_dir)}

    problems: list[str] = []
    notes: list[str] = []
    service = CatalogService.open(data_root=data_root)
    try:
        catalog = service.catalog
        if reused:
            # A second run on the same work directory only re-extracts and/or moves the rule
            # version: it never re-seeds the photo library (that would create new content
            # hashes and a new place/profile generation).
            search_report = {
                **search_report,
                "assets": {
                    str(row["relative_path"]).replace("\\", "/"): int(row["asset_id"])
                    for row in catalog.read_all(
                        "SELECT a.asset_id AS asset_id, l.relative_path AS relative_path "
                        "FROM assets AS a JOIN file_locations AS l ON l.asset_id = a.asset_id "
                        "ORDER BY a.asset_id, l.location_id"
                    )
                },
                "indexed_assets": [],
                "place": None,
                "profile": None,
            }
        enqueue_targets(
            catalog,
            taken_time_targets(catalog),
            now_ms=catalog.now_ms(),
        )
        run = run_queue(catalog, worker_id="taken-time-smoke")
        succeeded = int(
            catalog.read_one(
                "SELECT COUNT(*) AS total FROM jobs WHERE task_type = ? AND state = 'SUCCEEDED'",
                (time_extract.TAKEN_TIME_TASK_TYPE,),
            )["total"]
        )
        if succeeded < len(search_report["assets"]):
            problems.append("the extraction did not succeed for every asset")
        names = {
            str(rel).replace("\\", "/").rsplit("/", 1)[-1]: int(asset_id)
            for rel, asset_id in search_report["assets"].items()
        }
        stored_version = time_read_rules_version(catalog)
        trusted = stored_version == contract.RULES_VERSION
        state_before = time_read_state(catalog)
        labels = {
            asset_id: (None if view is None else str(view["label"]))
            for asset_id, view in (
                (int(row["asset_id"]), _detail_time(catalog, int(row["asset_id"])))
                for row in catalog.read_all("SELECT asset_id FROM asset_metadata ORDER BY asset_id")
            )
        }
        keys = {
            int(row["asset_id"]): (
                None if row["taken_at_sort"] is None else int(row["taken_at_sort"])
            )
            for row in catalog.read_all("SELECT asset_id, taken_at_sort FROM asset_metadata")
        }
        capture_assets = sorted(
            asset_id for asset_id, label in labels.items() if label == "capture"
        )
        file_time_assets = sorted(
            asset_id for asset_id, label in labels.items() if label == "file_time"
        )
        tail_assets = sorted(
            asset_id for asset_id, key in keys.items() if key is None
        )
        expected: dict[str, Any]
        if trusted:
            if state_before != {
                "state": "READY",
                "assets_total": len(names),
                "extracted_assets": len(names),
                "capture_time_assets": len(capture_assets),
            }:
                problems.append(f"unexpected capture state: {state_before}")
            ranged = asset_page(
                catalog,
                service.media_cache(),
                limit=50,
                capture_from="2022-01-01",
                capture_to_exclusive="2023-01-01",
            )
            ordered = asset_page(catalog, service.media_cache(), limit=50, order="capture_desc")
            empty_range = asset_page(
                catalog,
                service.media_cache(),
                limit=50,
                capture_from="2000-01-01",
                capture_to_exclusive="2001-01-01",
            )
            sessions = SearchSessionStore()
            search_page = search_request(
                catalog,
                SearchRequest.from_envelope(
                    {
                        "contract_version": "p2-contracts.v1",
                        "request_id": "smoke-search",
                        "page_size": 20,
                        "capture_from": "2022-01-01",
                        "capture_to_exclusive": "2023-01-01",
                    }
                ),
                sessions=sessions,
            )
            offset_asset = names["西湖 sunset.jpg"]
            no_offset_asset = names["beach sunset.jpg"]
            detail_probe = {
                str(offset_asset): _detail_time(catalog, offset_asset),
                str(no_offset_asset): _detail_time(catalog, no_offset_asset),
            }
            # The tail bucket is the *file-time* rows, ordered by ``asset_id`` descending
            # (ADR-0011 §5.1), so the set and the descending order are both checked.
            tail_returned = [
                int(item["asset_id"]) for item in ordered["assets"][-len(tail_assets):]
            ]
            if sorted(tail_returned) != tail_assets or tail_returned != sorted(
                tail_returned, reverse=True
            ):
                problems.append("the capture order does not end with the file-time rows")
            if ranged["taken_time"] is None:
                problems.append("the ranged listing lost its honesty block")
            if {hit["asset_id"] for hit in search_page["hits"]} != {
                int(item["asset_id"]) for item in ranged["assets"]
            }:
                problems.append("the search condition and the listing disagree")
            if detail_probe[str(offset_asset)] is None or (
                detail_probe[str(no_offset_asset)] is None
            ):
                problems.append("the two offset probes have no taken-time object")
            else:
                if detail_probe[str(offset_asset)]["timezone_offset_minutes"] != 480:
                    problems.append("the recorded +08:00 offset is not published")
                if detail_probe[str(no_offset_asset)]["timezone_offset_minutes"] is not None:
                    problems.append("an offset-less photo claims an offset")
            expected = {
                "capture_range_2022": sorted(
                    int(item["asset_id"]) for item in ranged["assets"]
                ),
                "capture_desc_order": [int(item["asset_id"]) for item in ordered["assets"]],
                "capture_desc_tail": [
                    int(item["asset_id"]) for item in ordered["assets"][-len(tail_assets):]
                ],
                "file_time_assets": file_time_assets,
                "empty_range_assets": [
                    int(item["asset_id"]) for item in empty_range["assets"]
                ],
                "search_hits_2022": [int(hit["asset_id"]) for hit in search_page["hits"]],
                "echo": search_page["filters"],
                "detail": detail_probe,
                "stale_code": "CATALOG_FILTER_REVISION_STALE",
            }
        else:
            notes.append(
                "this fixture was already left in the untrusted state by an earlier run, so "
                "its capture expectations are recorded as empty and not checked"
            )
            expected = {
                "capture_range_2022": [],
                "capture_desc_order": [],
                "capture_desc_tail": [],
                "file_time_assets": file_time_assets,
                "empty_range_assets": [],
                "search_hits_2022": [],
                "echo": {},
                "detail": {},
                "stale_code": "CATALOG_FILTER_REVISION_STALE",
            }

        if stored_version != int(rules_version):
            with catalog.write() as connection:
                connection.execute(
                    "UPDATE metadata SET value = ? WHERE key = ?",
                    (str(int(rules_version)), contract.RULES_VERSION_KEY),
                )
            notes.append(
                "the library rule version was moved on purpose after the extraction "
                f"({stored_version} -> {int(rules_version)}): a mismatch makes capture queries "
                "report PENDING and match nothing until the extraction runs again"
            )
        final_state = time_read_state(catalog)
        final_expected = (
            "READY" if int(rules_version) == contract.RULES_VERSION else "PENDING"
        )
        if final_state["state"] != final_expected:
            problems.append(
                f"the final capture state is {final_state['state']}, expected {final_expected}"
            )

        summary = {
            "status": "ok" if not problems else "failed",
            "problems": problems,
            "notes": notes,
            "work_dir": str(work_dir),
            "library": str(library),
            "data_root": str(data_root),
            "rules_version": int(rules_version),
            "reused_existing_fixture": reused,
            "extraction_run": run,
            "state_block": time_read_state(catalog),
            "assets": search_report["assets"],
            "names": names,
            "capture_plan": {
                "original_with_offset": names["西湖 sunset.jpg"],
                "original_without_offset": names["beach sunset.jpg"],
                "digitized_with_offset": names["p002.jpg"],
                "root_time_only": names["p003.jpg"],
                "capture_assets": capture_assets,
                "file_time_assets": file_time_assets,
                "tail_assets": tail_assets,
            },
            "expected": expected,
            "indexed_assets": search_report["indexed_assets"],
            "place": search_report["place"],
            "profile": search_report["profile"],
            "start_command": (
                "uv run --no-sync python -m photo_search --port <free-port> "
                f'--data-root "{data_root}"'
            ),
            "pages": ["/", "/search"],
            "privacy_needles": [
                "taken_at_raw",
                "2022:07:05 18:31:07",
                str(data_root),
                str(library),
            ],
            "multipart_refusal_command": (
                "curl -X POST /api/v1/search/image -F image=@<file> "
                "-F capture_from=2022-01-01  ->  422 VALIDATION_ERROR "
                "(the multipart closed set has no capture field)"
            ),
            "note": "synthetic fixture only; no real photo library is read",
        }
    finally:
        service.close()
    return summary


def _detail_time(catalog: Any, asset_id: int) -> dict[str, Any] | None:
    from photo_search.taken_time import read as time_read

    return time_read.asset_view(catalog.connection, int(asset_id))


def time_read_state(catalog: Any) -> dict[str, Any]:
    from photo_search.taken_time import read as time_read

    return time_read.state_block(catalog.connection)


def time_read_rules_version(catalog: Any) -> int | None:
    from photo_search.taken_time import read as time_read

    return time_read.rules_version(catalog.connection)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(tempfile.gettempdir()) / "PhotoSearchTakenTimeSmokeRoot",
    )
    parser.add_argument("--photos", type=int, default=8)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument(
        "--rules-version",
        type=int,
        default=contract.RULES_VERSION,
        help="the library rule version to leave behind (99 makes capture queries report PENDING)",
    )
    parser.add_argument("--report", type=Path, default=None)
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    work_dir = root / WORK_DIRNAME
    if args.reset and work_dir.is_dir():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    summary = build(
        work_dir,
        photos=int(args.photos),
        rules_version=int(args.rules_version),
        reset=bool(args.reset),
    )
    payload = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload + "\n", encoding="utf-8", newline="\n")
    print(payload)
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
