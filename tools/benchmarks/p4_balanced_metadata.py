"""Small synthetic paired metadata reads; no media, inference, HTTP or cold-cache claims.

The baseline loads the task's saved source files independently. Every paired output is
compared in full; timing alternates order. This is measurement JUnit, not product pytest.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from photo_search.persons import albums, members  # isort: skip
from photo_search.catalog import store
from photo_search.catalog.db import Catalog
from photo_search.catalog.sqlite_io import open_readonly
from photo_search.face.contract import FACE_DETECTOR_ID, FACE_SPEC_REVISION
from photo_search.refresh import progress

FILES = (
    "catalog/store.py", "refresh/progress.py", "persons/members.py",
    "persons/read.py", "persons/albums.py",
)
CASES = ("catalog_global", "catalog_source1", "source_cards_5", "face_enrichment",
         "member_counts", "album_stored", "album_fallback")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_hash(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def source_hashes(baseline: Path) -> dict[str, dict[str, str]]:
    return {relative: {"baseline": file_hash(baseline / "src/photo_search" / relative),
                       "current": file_hash(Path("src/photo_search") / relative)} for relative in FILES}


def load_baseline(root: Path) -> dict[str, Any]:
    loaded = {}
    for relative in FILES:
        name = "balanced_baseline_" + relative.replace("/", "_").replace(".py", "")
        spec = importlib.util.spec_from_file_location(name, root / "src/photo_search" / relative)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        loaded[relative] = module
    loaded["persons/read.py"].member_counts = loaded["persons/members.py"].member_counts
    loaded["persons/albums.py"].person_summary = loaded["persons/read.py"].person_summary
    return loaded


def seed(catalog: Catalog, *, size: int) -> None:
    """Write only generated facts into a fresh, fully migrated isolated database."""
    if catalog.read_one("SELECT COUNT(*) AS n FROM assets")["n"]:
        raise ValueError("use a fresh empty synthetic catalog")
    with catalog.write() as connection:
        connection.executemany(
            "INSERT INTO source_roots(source_id,display_name,root_path,status) VALUES(?,?,?,'ONLINE')",
            [(i, f"synthetic-{i}", f"synthetic-only-{i}") for i in range(1, 6)],
        )
        connection.executemany(
            "INSERT INTO assets(asset_id,sha256,byte_size,created_at) VALUES(?,?,1,1)",
            [(i, f"{i:064x}") for i in range(1, size + 1)],
        )
        locations = [
            (1 + (i - 1) % 5, f"{i}.jpg", f"{i}.jpg", i,
             "MISSING" if i % 13 == 0 else "PRESENT") for i in range(1, size + 1)
        ]
        locations += [(1, f"copy-{i}.jpg", f"copy-{i}.jpg", i, "PRESENT")
                      for i in range(10, size + 1, 10)]
        locations += [(i, "unbound.jpg", "unbound.jpg", None, "PRESENT") for i in range(1, 6)]
        connection.executemany(
            "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,availability) "
            "VALUES(?,?,?,?,1,1,?)", locations,
        )
        job_rows = []
        for i in range(1, size + 1):
            for task, processor in (("image_face", "face-model@1"), ("image_face_detail_auto", "face-model@1"),
                                    ("geo_extract", "geo-exif-gps@1")):
                revision = f"{i:064x}" if i % 7 else "older-content"
                state = ("SUCCEEDED", "QUEUED", "FAILED", "BLOCKED", "CANCELLED")[i % 5]
                job_rows.append((str(i), revision, task, processor, state))
        connection.executemany(
            "INSERT INTO jobs(subject_type,subject_id,input_revision,task_type,processor_id,state,"
            "run_after,created_at,updated_at) VALUES('asset',?,?,?,?,?,1,1,1)", job_rows,
        )
        connection.execute("INSERT INTO persons(person_id,revision,created_at,updated_at) VALUES(1,1,1,1)")
        connection.executemany(
            "INSERT INTO faces(face_id,asset_id,spec_revision,detector_id,ordinal,input_revision,"
            "bbox_json,landmarks_json,det_score,face_pixels,blur_variance,quality_state,artifact_id,"
            "face_revision,created_at,updated_at) "
            "VALUES(?,?,?,?,0,?,'[0,0,64,64]','[[1,2],[2,3],[3,4],[4,5],[5,6]]',"
            "0.95,4096,200,'OK','synthetic-balanced',1,1,1)",
            [(f"{i:064x}", i, FACE_SPEC_REVISION, FACE_DETECTOR_ID, f"{i:064x}")
             for i in range(1, size + 1)],
        )
        connection.executemany(
            "INSERT INTO face_person_links(face_id,person_id,assignment_kind,decision,confidence,"
            "revision,created_at,updated_at) VALUES(?,1,'AUTO','CONFIRMED',0.9,1,1,1)",
            [(f"{i:064x}",) for i in range(1, size + 1)],
        )
        connection.executemany(
            "INSERT INTO face_assignment_controls(face_id,mode,revision,updated_at) "
            "VALUES(?,'MANUAL_UNASSIGNED',1,1)", [(f"{i:064x}",) for i in range(11, size + 1, 11)],
        )
        connection.executemany(
            "INSERT INTO face_exclusions(face_id,person_id,active,created_at) VALUES(?,1,1,1)",
            [(f"{i:064x}",) for i in range(17, size + 1, 17)],
        )
        connection.execute("UPDATE persons SET avatar_face_id=? WHERE person_id=1", (f"{2:064x}",))


def source_cards(catalog: Catalog, counts: Any, stages: Any) -> list[dict[str, Any]]:
    with catalog.read_snapshot():
        return [{"counts": counts.catalog_counts(catalog.connection, source_id=i),
                 "progress": stages.source_progress(catalog, source_id=i, binding=None,
                                                    available={"face": True, "ocr": False})}
                for i in range(1, 6)]


def measure(root: Path, baseline: Path, *, size: int, samples: int,
            database: Path | None = None, selected: list[str] | None = None) -> dict[str, Any]:
    if samples < 1 or size < 2:
        raise ValueError("samples must be positive and synthetic size must be at least two")
    if selected is not None and (not selected or set(selected) - set(CASES)):
        raise ValueError("select at least one known measurement case")
    root.mkdir(parents=True, exist_ok=True)
    if (root / "paired.json").exists():
        raise ValueError("choose a fresh output directory; existing evidence is never overwritten")
    hashes = source_hashes(baseline)
    script_hash = file_hash(Path(__file__))
    before = load_baseline(baseline)
    if database is None:
        database = root / "data/catalog/catalog.db"
        if database.exists():
            raise ValueError("choose a fresh synthetic catalog path")
        catalog = Catalog.open(database)
        seed(catalog, size=size)
        catalog.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    else:
        # Only a previously prepared, isolated synthetic fixture may be supplied.
        if not database.is_file() or Path(str(database) + "-wal").exists():
            raise ValueError("expected a checkpointed synthetic fixture with no WAL")
        catalog = Catalog(open_readonly(database, immutable=True), db_path=database,
                          data_root=database.parent.parent)
    try:
        data_before = file_hash(database)
        changes_before = catalog.connection.total_changes
        size = int(catalog.read_one("SELECT COUNT(*) AS n FROM assets")["n"])
        person = dict(catalog.read_one("SELECT * FROM persons WHERE person_id=1"))
        fallback = {**person, "avatar_face_id": None}
        # Hold only the input row in Python; never persist or cache the selected cover.
        person["avatar_face_id"] = before["persons/albums.py"]._album_summary(catalog.connection, fallback)["avatar_face_id"]
        cases = {
            "catalog_global": (lambda: before["catalog/store.py"].catalog_counts(catalog.connection),
                               lambda: store.catalog_counts(catalog.connection)),
            "catalog_source1": (lambda: before["catalog/store.py"].catalog_counts(catalog.connection, source_id=1),
                                lambda: store.catalog_counts(catalog.connection, source_id=1)),
            "source_cards_5": (lambda: source_cards(catalog, before["catalog/store.py"], before["refresh/progress.py"]),
                               lambda: source_cards(catalog, store, progress)),
            "face_enrichment": (lambda: before["refresh/progress.py"].enrichment_counts(catalog, task_type=("image_face", "image_face_detail_auto"), processor_id="face-model@1"),
                                lambda: progress.enrichment_counts(catalog, task_type=("image_face", "image_face_detail_auto"), processor_id="face-model@1")),
            "member_counts": (lambda: before["persons/members.py"].member_counts(catalog.connection, 1),
                              lambda: members.member_counts(catalog.connection, 1)),
            "album_stored": (lambda: before["persons/albums.py"]._album_summary(catalog.connection, person),
                             lambda: albums._album_summary(catalog.connection, person)),
            "album_fallback": (lambda: before["persons/albums.py"]._album_summary(catalog.connection, fallback),
                               lambda: albums._album_summary(catalog.connection, fallback)),
        }
        results = {}
        for name, functions in cases.items():
            if selected and name not in selected:
                continue
            values = [[], []]
            expected = functions[0]()
            assert functions[1]() == expected, name
            for iteration in range(samples):
                for index in (0, 1) if iteration % 2 == 0 else (1, 0):
                    start = time.perf_counter()
                    actual = functions[index]()
                    values[index].append((time.perf_counter() - start) * 1000)
                    assert actual == expected, name
            results[name] = {"samples_ms": values, "median_ms": list(map(statistics.median, values)),
                             "p95_ms": [sorted(value)[max(0, (len(value) * 95 + 99) // 100 - 1)] for value in values],
                             "output_sha256": digest(expected), "full_output_equal": True}
        assert file_hash(database) == data_before
        assert source_hashes(baseline) == hashes
        assert file_hash(Path(__file__)) == script_hash
        assert catalog.connection.total_changes == changes_before
        return {"size": size, "samples": samples, "order": "alternating AB/BA", "cache": "warm; not OS cold",
                "data_path": str(database.resolve()), "data_sha256": data_before,
                "total_changes_delta": catalog.connection.total_changes - changes_before,
                "measurement_script_sha256": script_hash,
                "source_sha256": hashes, "results": results}
    finally:
        catalog.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--size", type=int, default=2000)
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--database", type=Path, help="existing isolated synthetic fixture; opened immutable")
    parser.add_argument("--cases", nargs="+", choices=CASES, help="selected case names")
    args = parser.parse_args()
    if args.samples < 1 or args.size < 2:
        parser.error("samples must be positive and synthetic size must be at least two")
    try:
        result = measure(args.root, args.baseline, size=args.size, samples=args.samples,
                         database=args.database, selected=args.cases)
    except Exception as error:
        args.root.mkdir(parents=True, exist_ok=True)
        # A preparation/oracle failure is diagnostic evidence, never a successful
        # zero-case report. Preserve previous files even on an invalid repeat run.
        failure_path = args.root / "measurement-failed.xml"
        if not failure_path.exists():
            suite = ET.Element("testsuite", name="balanced_metadata_measurement", tests="1", failures="0", errors="1", skipped="0")
            case = ET.SubElement(suite, "testcase", name="prepare_or_measure")
            ET.SubElement(case, "error", type=type(error).__name__, message=str(error))
            ET.ElementTree(suite).write(failure_path, encoding="utf-8", xml_declaration=True)
        raise
    (args.root / "paired.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    suite = ET.Element("testsuite", name="balanced_metadata_measurement", tests=str(len(result["results"])), failures="0", errors="0", skipped="0")
    for name in result["results"]:
        ET.SubElement(suite, "testcase", name=name, classname="paired_output_equivalence")
    ET.ElementTree(suite).write(args.root / "measurement.xml", encoding="utf-8", xml_declaration=True)
    print(json.dumps({name: entry["median_ms"] for name, entry in result["results"].items()}))


if __name__ == "__main__":
    main()
