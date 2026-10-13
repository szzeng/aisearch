"""Build/reuse a metadata-only 200k catalog and measure timeline SQL/API-core queries.

The output root is project-local by default. No image files, external library, model, or GPU
are used. Re-run with --reuse to measure the existing fixture without rebuilding it.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from photo_search.api.timeline import timeline_page, timeline_periods
from photo_search.catalog.db import Catalog
from photo_search.persons.collections import create_collection, edit_members

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = ROOT / ".tmp" / "P4-UI-TIMELINE-CORE-A" / "scale-data"
COUNT_DEFAULT = 200_000
BATCH = 5_000
BASE_CREATED = 1_577_836_800_000


def _civil_key(year: int, month: int, day: int, hour: int, minute: int, second: int) -> int:
    return ((date(year, month, day).toordinal() - date(1, 1, 1).toordinal()) * 86_400_000
            + (hour * 3600 + minute * 60 + second) * 1000)


def _make_fixture(catalog: Catalog, count: int) -> None:
    if int(catalog.read_one("SELECT COUNT(*) AS n FROM assets")["n"]):
        raise RuntimeError("fixture database is not empty; pass --reuse or choose another --root")
    with catalog.write() as connection:
        now = catalog.now_ms()
        source_ids = []
        for index in range(5):
            source_ids.append(int(connection.execute(
                "INSERT INTO source_roots(display_name, root_path, volume_hint, status) VALUES (?, ?, NULL, 'ONLINE')",
                (f"合成来源 {index + 1}", f"synthetic-root-{index + 1}"),
            ).lastrowid))
        for start in range(1, count + 1, BATCH):
            end = min(count + 1, start + BATCH)
            assets: list[tuple[Any, ...]] = []
            locations: list[tuple[Any, ...]] = []
            metadata: list[tuple[Any, ...]] = []
            for asset_id in range(start, end):
                digest = f"{asset_id:064x}"
                created_at = BASE_CREATED + ((asset_id * 7) % (365 * 8)) * 86_400_000 + (asset_id % 86_400) * 1000
                assets.append((asset_id, digest, 1_048_576, created_at, "ACTIVE"))
                source_id = source_ids[asset_id % len(source_ids)]
                path = f"year-{asset_id % 24:02d}/photo-{asset_id:07d}.jpg"
                locations.append((asset_id, source_id, path, path.casefold(), 1_048_576,
                                  1_600_000_000_000_000_000 + asset_id, None, "PRESENT"))
                if asset_id % 20 == 0:
                    duplicate = f"duplicates/photo-{asset_id:07d}.jpg"
                    locations.append((asset_id, source_ids[(asset_id + 1) % len(source_ids)], duplicate,
                                      duplicate.casefold(), 1_048_576,
                                      1_600_000_000_000_000_000 + asset_id, None, "PRESENT"))
                category = asset_id % 20
                if category < 15:
                    source = "EXIF_ORIGINAL" if asset_id % 2 else "EXIF_DIGITIZED"
                    year = 2000 + (asset_id % 24)
                    month = 1 + ((asset_id // 24) % 12)
                    day = 1 + (asset_id % 27)
                    sort_value = _civil_key(year, month, day, asset_id % 24, asset_id % 60, asset_id % 60)
                    confidence = "MEDIUM"
                    offset = None
                    metadata.append((asset_id, None, sort_value, source, confidence, offset, None))
                elif category < 18:
                    metadata.append((asset_id, None, _civil_key(1999, 1, 2, 3, 4, 5),
                                     "EXIF_ROOT", "LOW", None, None))
                elif category == 18:
                    metadata.append((asset_id, None, None, "FILE_MTIME", "LOW", None, None))
                # category == 19 is deliberately unextracted/pending.
            connection.executemany(
                "INSERT INTO assets(asset_id, sha256, byte_size, created_at, state) VALUES (?, ?, ?, ?, ?)",
                assets,
            )
            connection.executemany(
                "INSERT INTO file_locations(asset_id, source_id, relative_path, path_key, size, mtime_ns, "
                "last_seen_scan_id, availability) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                locations,
            )
            connection.executemany(
                "INSERT INTO asset_metadata(asset_id, taken_at_raw, taken_at_sort, time_source, "
                "time_confidence, timezone_offset, camera) VALUES (?, ?, ?, ?, ?, ?, ?)",
                metadata,
            )
        connection.execute(
            "INSERT INTO persons(person_id, name, name_key, merged_into, revision, created_at, updated_at) "
            "VALUES (1, '大成员人物', 'nfc1:大成员人物', NULL, 1, ?, ?)", (now, now)
        )
        person_face_rows = []
        person_link_rows = []
        membership_count = max(100_000, count // 2)
        location_rows = connection.execute(
            "SELECT asset_id, MIN(location_id) AS location_id FROM file_locations "
            "WHERE asset_id <= ? GROUP BY asset_id", (membership_count,)
        ).fetchall()
        location_by_asset = {int(row["asset_id"]): int(row["location_id"]) for row in location_rows}
        for asset_id in range(1, membership_count + 1):
            face_id = f"{asset_id:064x}"
            digest = f"{asset_id:064x}"
            location_id = location_by_asset[asset_id]
            person_face_rows.append((face_id, asset_id, "timeline-scale-v1", "scale-detector-v1", 0,
                                     digest, location_id, "[0,0,1,1]", "[]", 0.9, 120.0, 1.0,
                                     "OK", None, f"scale-{asset_id}", 1, now, now))
            person_link_rows.append((face_id, 1, "AUTO", "CONFIRMED", 0.9, None, 1, now, now))
            if len(person_face_rows) == BATCH:
                connection.executemany(
                    "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, input_revision, "
                    "location_id, bbox_json, landmarks_json, det_score, face_pixels, blur_variance, quality_state, "
                    "quality_reason, artifact_id, face_revision, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", person_face_rows)
                connection.executemany(
                    "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, confidence, "
                    "reason, revision, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", person_link_rows)
                person_face_rows.clear()
                person_link_rows.clear()
        if person_face_rows:
            connection.executemany(
                "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, input_revision, "
                "location_id, bbox_json, landmarks_json, det_score, face_pixels, blur_variance, quality_state, "
                "quality_reason, artifact_id, face_revision, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", person_face_rows)
            connection.executemany(
                "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, confidence, "
                "reason, revision, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", person_link_rows)
    collection = create_collection(catalog, "大成员合集")["collection"]
    edit_members(catalog, int(collection["collection_id"]), action="add", person_ids=[1],
                 instance_token=str(collection["instance_token"]))


def _measure(label: str, run: Callable[[], dict[str, Any]], count: int = 30) -> dict[str, Any]:
    run()  # warm-up
    samples: list[float] = []
    plans: list[list[str]] = []
    for _ in range(count):
        traces: list[str] = []
        # timeline funcs use catalog.connection, attached by the closure attribute below.
        connection = getattr(run, "connection")
        connection.set_trace_callback(traces.append)
        start = time.perf_counter()
        result = run()
        samples.append((time.perf_counter() - start) * 1000)
        connection.set_trace_callback(None)
        sql = next((item for item in traces if "FROM assets AS a" in item and "ORDER BY" in item), None)
        if sql and not plans:
            plans.extend(str(row["detail"]) for row in connection.execute("EXPLAIN QUERY PLAN " + sql).fetchall())
        if label.startswith("assets_"):
            result["_rows"] = len(result.get("assets", []))
    samples.sort()
    p95 = samples[min(len(samples) - 1, int(0.95 * len(samples)))]
    return {"scenario": label, "samples": count, "p50_ms": round(statistics.median(samples), 2),
            "p95_ms": round(p95, 2), "max_ms": round(max(samples), 2),
            "rows": int(result.get("_rows", 0)), "query_plan": plans}


def _measure_all(catalog: Catalog, samples: int,
                 selected: set[str] | None = None) -> list[dict[str, Any]]:
    scenarios: list[tuple[str, Callable[[], dict[str, Any]]]] = []
    latest = catalog.read_one("SELECT MAX(created_at) AS value FROM assets")
    latest_local = datetime.fromtimestamp(int(latest["value"]) / 1000).astimezone()
    library_year, library_month = latest_local.year, latest_local.month
    params = [
        ("assets_capture", dict(time_basis="capture", bucket="capture")),
        ("assets_library", dict(time_basis="library")),
        ("assets_library_page2", {}),
        ("assets_unknown", dict(time_basis="capture", bucket="unknown")),
        ("assets_pending", dict(time_basis="capture", bucket="pending")),
        ("assets_year_seek", dict(time_basis="capture", seek_year=2012)),
        ("assets_month_seek", dict(time_basis="capture", seek_year=2012, seek_month=6)),
        ("assets_library_year_seek", dict(time_basis="library", seek_year=library_year)),
        ("assets_library_month_seek", dict(time_basis="library", seek_year=library_year, seek_month=library_month)),
        ("periods_global_years", dict(time_basis="capture")),
        ("periods_year_months", dict(time_basis="capture", year=2012)),
        ("periods_library_years", dict(time_basis="library")),
        ("assets_source", dict(kind="source", source_id=1)),
        ("assets_person_100k", dict(kind="person", person_id=1)),
        ("assets_collection_100k", dict(kind="collection", collection_id=1)),
    ]
    for label, args in params:
        if label == "assets_library_page2":
            first = timeline_page(catalog, time_basis="library", limit=50)
            if not first["next_cursor"]:
                raise RuntimeError("fixture has no second library timeline page")
            cursor = first["next_cursor"]
            def run_page2(cursor=cursor):
                return timeline_page(catalog, time_basis="library", cursor=cursor, limit=50)
            run_page2.connection = catalog.connection  # type: ignore[attr-defined]
            scenarios.append((label, run_page2))
        elif label.startswith("periods_"):
            def run_periods(args=args):
                return timeline_periods(catalog, **args)
            run_periods.connection = catalog.connection  # type: ignore[attr-defined]
            scenarios.append((label, run_periods))
        else:
            def run_page(args=args):
                return timeline_page(catalog, limit=50, **args)
            run_page.connection = catalog.connection  # type: ignore[attr-defined]
            scenarios.append((label, run_page))
    if selected is not None:
        known = {label for label, _ in scenarios}
        unknown = selected - known
        if unknown:
            raise ValueError(f"unknown benchmark scenarios: {', '.join(sorted(unknown))}")
        scenarios = [(label, run) for label, run in scenarios if label in selected]
    results: list[dict[str, Any]] = []
    for label, run in scenarios:
        print(f"scenario_start={label} samples={samples}", flush=True)
        result = _measure(label, run, samples)
        print(f"scenario_done={label} p50_ms={result['p50_ms']} p95_ms={result['p95_ms']} max_ms={result['max_ms']}", flush=True)
        results.append(result)
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--count", type=int, default=COUNT_DEFAULT)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--reuse", action="store_true")
    parser.add_argument("--scenarios", help="comma-separated scenario names to run")
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    catalog = Catalog.open(data_root=root)
    try:
        existing = int(catalog.read_one("SELECT COUNT(*) AS n FROM assets")["n"])
        if args.reuse:
            if existing < args.count:
                raise RuntimeError(f"--reuse requested but only {existing} assets exist")
        else:
            _make_fixture(catalog, int(args.count))
        selected = None if not args.scenarios else {
            item.strip() for item in args.scenarios.split(",") if item.strip()
        }
        results = _measure_all(catalog, int(args.samples), selected)
        report = {
            "fixture_root": str(root), "assets": existing or int(args.count),
            "schema_version": catalog.schema_version,
            "metadata_rows": int(catalog.read_one("SELECT COUNT(*) AS n FROM asset_metadata")["n"]),
            "samples_per_scenario": int(args.samples), "sqlite_version": str(catalog.connection.execute("SELECT sqlite_version()").fetchone()[0]),
            "results": results,
        }
        output = root / "timeline-benchmark.json"
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"report={output}")
    finally:
        catalog.close()


if __name__ == "__main__":
    main()
