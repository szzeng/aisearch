"""Read-only build-selector benchmark on the shared synthetic catalog.

Run ``--implementation before`` against the schema-31 fixture, then ``after`` against
the same fixture after its normal migration. No image file, GPU, HTTP or writer is used.
The frozen old selector and the current selector both classify metadata as synthetic JPEGs;
these timings therefore measure SQL/row materialisation, not decoding or an end-to-end build.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

from photo_search.artifacts.store import generation_id_for
from photo_search.catalog.db import Catalog
from photo_search.catalog.sqlite_io import open_readonly
from photo_search.indexing import semantic
from photo_search.refresh.pipeline import _scoped_asset_page

SPACE = "p4-perf-unbuilt-space"
PROCESSOR = "p4-perf-unbuilt-provider"
VM_INTERVAL = 1000
LEGACY_TARGET_SQL = (
    "SELECT DISTINCT a.asset_id AS asset_id, a.sha256 AS sha256, "
    "f.relative_path AS relative_path, r.root_path AS root_path FROM assets AS a "
    "JOIN file_locations AS f ON f.location_id = ("
    "SELECT l.location_id FROM file_locations AS l WHERE l.asset_id = a.asset_id "
    "AND l.availability = 'PRESENT' ORDER BY l.location_id LIMIT 1) "
    "JOIN source_roots AS r ON r.source_id = f.source_id "
    "WHERE {source_predicate}{cursor_predicate}NOT EXISTS ("
    "SELECT 1 FROM index_entries AS e JOIN artifacts AS ar ON ar.artifact_id = e.artifact_id "
    "WHERE e.asset_id = a.asset_id AND e.space_id = ? AND e.generation_id = ? "
    "AND e.state = 'CONFIRMED' AND ar.input_revision = a.sha256) ORDER BY a.asset_id"
)


class MeteredCatalog(Catalog):
    def reset_read_counts(self) -> None:
        self.calls = self.rows = self.largest_page = 0

    def read_all(self, sql: str, parameters: tuple = ()) -> list:
        rows = super().read_all(sql, parameters)
        self.calls += 1
        self.rows += len(rows)
        self.largest_page = max(self.largest_page, len(rows))
        return rows


def legacy_selection(
    catalog: Catalog, *, limit: int, after_asset_id: int = 0, source_id: int | None = None
) -> dict[str, Any]:
    parameters = (() if source_id is None else (source_id,)) + (after_asset_id,)
    rows = catalog.read_all(
        LEGACY_TARGET_SQL.format(
            source_predicate="" if source_id is None else "f.source_id = ? AND ",
            cursor_predicate="a.asset_id > ? AND ",
        ),
        (*parameters, SPACE, generation_id_for(SPACE, PROCESSOR)),
    )
    targets = []
    checked = 0
    for row in rows:
        checked += 1
        path = Path(str(row["root_path"])) / str(row["relative_path"])
        eligible, _ = semantic.image_eligibility(path)
        if eligible:
            if len(targets) < limit:
                targets.append({"asset_id": int(row["asset_id"]),
                                "input_revision": str(row["sha256"])})
            if len(targets) >= limit:
                break
    return {"checked": checked, "coverage_complete": checked == len(rows), "targets": targets,
            "out_of_scope": {"count": 0, "labels": {}, "sample": []}}


def legacy_scope_page(
    catalog: Catalog, *, source_id: int, after_asset_id: int, limit: int
) -> list[int]:
    return [int(row["asset_id"]) for row in catalog.read_all(
        "SELECT DISTINCT l.asset_id AS asset_id FROM file_locations AS l "
        "WHERE l.availability = 'PRESENT' AND l.asset_id IS NOT NULL "
        "AND l.source_id = ? AND l.asset_id > ? ORDER BY l.asset_id LIMIT ?",
        (source_id, after_asset_id, limit),
    )]


def fingerprint(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def measure(catalog: MeteredCatalog, run: Callable[[], Any], samples: int) -> dict:
    records = []
    first_digest = None
    for number in range(samples):
        catalog.reset_read_counts()
        steps = 0

        def progress() -> int:
            nonlocal steps
            steps += VM_INTERVAL
            return 0

        catalog.connection.set_progress_handler(progress, VM_INTERVAL)
        started = time.perf_counter()
        error = None
        result: Any = None
        try:
            result = run()
        except Exception as caught:  # Record every failed sample; never remove it from the run.
            error = f"{type(caught).__name__}: {caught}"
        elapsed = (time.perf_counter() - started) * 1000
        catalog.connection.set_progress_handler(None, 0)
        digest = None if error else fingerprint(result)
        if first_digest is None and error is None:
            first_digest = digest
        records.append({
            "sample": number, "elapsed_ms": elapsed, "error": error,
            "result_sha256": digest, "same_as_first": digest == first_digest,
            "sql_vm_steps_approx": steps, "read_calls": catalog.calls,
            "rows_materialised": catalog.rows, "largest_read_page": catalog.largest_page,
        })
    times = sorted(row["elapsed_ms"] for row in records)
    return {
        "samples": samples, "errors": sum(row["error"] is not None for row in records),
        "consistent": all(row["same_as_first"] and row["error"] is None for row in records),
        "first_ms": records[0]["elapsed_ms"], "p50_ms": statistics.median(times),
        "p95_ms": times[math.ceil(len(times) * 0.95) - 1], "max_ms": max(times),
        "vm_steps_median_approx": statistics.median(
            row["sql_vm_steps_approx"] for row in records
        ),
        "largest_read_page": max(row["largest_read_page"] for row in records),
        "rows_materialised_median": statistics.median(
            row["rows_materialised"] for row in records
        ),
        "result_sha256": first_digest, "records": records,
    }


def benchmark(db: Path, *, implementation: str, samples: int, page_size: int,
              measurement_context: str = "single driver; host load not controlled") -> dict:
    connection = open_readonly(db)
    catalog = MeteredCatalog(connection, db_path=db, data_root=db.parent.parent)
    try:
        asset_count = int(catalog.read_one("SELECT COUNT(*) FROM assets")[0])
        middle = int(catalog.read_one("SELECT MAX(asset_id) FROM assets")[0]) // 2
        selector = legacy_selection if implementation == "before" else (
            lambda target, **options: semantic.semantic_selection(
                target, space_id=SPACE, processor_id=PROCESSOR, **options
            )
        )
        scoped_page = legacy_scope_page if implementation == "before" else _scoped_asset_page
        scenarios: dict[str, Callable[[], Any]] = {
            "selection_global_first": lambda: selector(catalog, limit=page_size),
            "selection_global_middle": lambda: selector(
                catalog, limit=page_size, after_asset_id=middle
            ),
            # Source 1 owns the first location for its assets in the fixture. This timing
            # comparison excludes the separately tested shared-source correctness repair.
            "selection_source_first": lambda: selector(
                catalog, limit=page_size, source_id=1
            ),
            "source_page_middle": lambda: scoped_page(
                catalog, source_id=1, after_asset_id=middle, limit=page_size
            ),
        }
        result = {
            "implementation": implementation, "database": str(db.resolve()),
            "schema_version": catalog.schema_version, "asset_count": asset_count,
            "page_size": page_size, "vm_counter_interval": VM_INTERVAL,
            "timing_layer": "read-only SQL and row materialisation; synthetic eligibility",
            "measurement_context": measurement_context,
            "first_sample_is_os_cold": False,
            "limitations": "No images, decoding, models, API, writes or injected concurrent requests. "
                            "VM counters are rounded to the callback interval and included in timings.",
            "scenarios": {},
        }
        with patch.object(semantic, "image_eligibility", lambda _: (True, "SYNTHETIC_JPEG")):
            for name, run in scenarios.items():
                result["scenarios"][name] = measure(catalog, run, samples)
                summary = result["scenarios"][name]
                print(json.dumps({"scenario": name, "p50_ms": summary["p50_ms"],
                                  "p95_ms": summary["p95_ms"], "errors": summary["errors"]}),
                      flush=True)
        return result
    finally:
        catalog.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--implementation", choices=("before", "after"), required=True)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--page-size", type=int, default=5000)
    parser.add_argument("--measurement-context", default="single driver; host load not controlled")
    args = parser.parse_args()
    if args.samples < 30 or args.page_size < 1:
        parser.error("at least 30 samples and a positive page size are required")
    report = benchmark(args.db, implementation=args.implementation,
                       samples=args.samples, page_size=args.page_size,
                       measurement_context=args.measurement_context)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    raise SystemExit(0 if all(
        row["consistent"] for row in report["scenarios"].values()
    ) else 1)
