"""Paired SQL-only benchmark for favorites paging on isolated synthetic catalogs.

Run from the repository root with ``PYTHONPATH=.;src``. The generated catalogs live in
an automatically cleaned directory under ``.tmp``; JSON evidence is written separately.
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import sqlite3
import statistics
import subprocess
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from photo_search.app import create_app as _load_application_imports  # noqa: F401
from photo_search.catalog.db import Catalog
from photo_search.catalog.image_policy import photo_asset_sql
from photo_search.favorites import (
    _capture_favorite_rows,
    _capture_known_query,
    _capture_unknown_query,
)

PAGE_SIZE = 50
FAVORITE_COUNTS = (2_000, 10_000, 50_000)
DENSITIES = (1.0, 0.1)
SOURCES = ("EXIF_ORIGINAL", "EXIF_DIGITIZED")
TRUSTED_SQL = "(m.time_source IN ('EXIF_ORIGINAL','EXIF_DIGITIZED') AND m.taken_at_sort IS NOT NULL)"


def _seed_catalog(data_root: Path, asset_count: int, favorite_ids: list[int]) -> Catalog:
    catalog = Catalog.open(data_root=data_root)
    connection = catalog.connection
    with catalog.write() as writer:
        writer.executemany(
            "INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) VALUES(?,?,8,?,'ACTIVE')",
            ((asset_id, f"{asset_id:064x}", asset_id) for asset_id in range(1, asset_count + 1)),
        )
        def metadata_rows():
            for asset_id in range(1, asset_count + 1):
                bucket = asset_id % 100
                if bucket < 80:
                    source, confidence = "EXIF_ORIGINAL", "MEDIUM"
                elif bucket < 95:
                    source, confidence = "EXIF_DIGITIZED", "MEDIUM"
                elif bucket < 98:
                    source, confidence = "EXIF_ROOT", "LOW"
                else:
                    source, confidence = "FILE_MTIME", "LOW"
                sort_value = None if source == "FILE_MTIME" else (asset_id * 104_729) % 315_537_897_599_999 + 1
                yield asset_id, sort_value, source, confidence

        writer.executemany(
            "INSERT INTO asset_metadata(asset_id,taken_at_sort,time_source,time_confidence) VALUES(?,?,?,?)",
            metadata_rows(),
        )
        writer.executemany(
            "INSERT INTO asset_favorites(asset_id,added_at_ms) VALUES(?,?)",
            ((asset_id, asset_id) for asset_id in favorite_ids),
        )
    connection.execute("ANALYZE")
    return catalog


def _time_row(asset_id: int) -> tuple[str, int | None]:
    bucket = asset_id % 100
    if bucket < 80:
        source = "EXIF_ORIGINAL"
    elif bucket < 95:
        source = "EXIF_DIGITIZED"
    elif bucket < 98:
        source = "EXIF_ROOT"
    else:
        source = "FILE_MTIME"
    sort_value = None if source == "FILE_MTIME" else (asset_id * 104_729) % 315_537_897_599_999 + 1
    return source, sort_value


def _oracle(favorite_ids: list[int], order: str) -> list[dict[str, Any]]:
    rows = []
    for asset_id in favorite_ids:
        source, sort_value = _time_row(asset_id)
        trusted = source in SOURCES and sort_value is not None
        rows.append({"asset_id": asset_id, "added_at_ms": asset_id, "trusted": trusted, "sort": sort_value})
    if order == "recent":
        return sorted(rows, key=lambda row: (-row["added_at_ms"], -row["asset_id"]))
    return sorted(rows, key=lambda row: (0, -row["sort"], -row["asset_id"]) if row["trusted"] else (1, 0, -row["asset_id"]))


def _capture_anchor(row: dict[str, Any] | None) -> tuple[int, ...] | None:
    if row is None:
        return None
    return (int(row["trusted"]), int(row["sort"] or 0), int(row["asset_id"]))


def _baseline_capture_page(
    connection: sqlite3.Connection,
    *,
    photo_sql: str,
    photo_params: list[int],
    position: tuple[int, ...] | None,
) -> list[sqlite3.Row]:
    sql = (
        "SELECT f.asset_id,f.added_at_ms,a.sha256,a.byte_size,a.created_at,"
        f"CASE WHEN {TRUSTED_SQL} THEN m.taken_at_sort END AS capture_sort "
        "FROM asset_favorites AS f INDEXED BY idx_asset_favorites_recent "
        "JOIN assets AS a ON a.asset_id=f.asset_id "
        "LEFT JOIN asset_metadata AS m ON m.asset_id=f.asset_id "
        "WHERE a.state='ACTIVE' AND " + photo_sql
    )
    params: list[Any] = list(photo_params)
    if position is not None:
        known, sort_key, asset_id = position
        if known:
            sql += (
                f" AND (({TRUSTED_SQL} AND (m.taken_at_sort < ? OR "
                f"(m.taken_at_sort = ? AND f.asset_id < ?))) OR NOT {TRUSTED_SQL})"
            )
            params.extend((sort_key, sort_key, asset_id))
        else:
            sql += f" AND NOT {TRUSTED_SQL} AND f.asset_id < ?"
            params.append(asset_id)
    sql += f" ORDER BY (NOT {TRUSTED_SQL}), CASE WHEN {TRUSTED_SQL} THEN m.taken_at_sort END DESC, f.asset_id DESC LIMIT ?"
    return connection.execute(sql, (*params, PAGE_SIZE + 1)).fetchall()


def _recent_page(
    connection: sqlite3.Connection,
    *,
    photo_sql: str,
    photo_params: list[int],
    position: tuple[int, ...] | None,
) -> list[sqlite3.Row]:
    sql = (
        "SELECT f.asset_id,f.added_at_ms,a.sha256,a.byte_size,a.created_at "
        "FROM asset_favorites AS f INDEXED BY idx_asset_favorites_recent "
        "JOIN assets AS a ON a.asset_id=f.asset_id WHERE a.state='ACTIVE' AND " + photo_sql
    )
    params: list[Any] = list(photo_params)
    if position is not None:
        sql += " AND (f.added_at_ms,f.asset_id) < (?,?)"
        params.extend(position)
    sql += " ORDER BY f.added_at_ms DESC,f.asset_id DESC LIMIT ?"
    return connection.execute(sql, (*params, PAGE_SIZE + 1)).fetchall()


def _partial_index_capture_page(
    connection: sqlite3.Connection,
    *,
    photo_sql: str,
    photo_params: list[int],
    after: tuple[int, int] | None,
) -> list[sqlite3.Row]:
    conditions = [
        "m.time_source IN ('EXIF_ORIGINAL','EXIF_DIGITIZED')",
        "m.taken_at_sort IS NOT NULL",
        "a.state='ACTIVE'",
        photo_sql,
    ]
    params: list[Any] = list(photo_params)
    if after is not None:
        conditions.append("(m.taken_at_sort,m.asset_id) < (?,?)")
        params.extend(after)
    sql = (
        "SELECT f.asset_id,f.added_at_ms,a.sha256,a.byte_size,a.created_at,"
        "m.taken_at_sort AS capture_sort "
        "FROM asset_metadata AS m INDEXED BY idx_perf_capture_trusted "
        "CROSS JOIN asset_favorites AS f ON f.asset_id=m.asset_id "
        "CROSS JOIN assets AS a ON a.asset_id=f.asset_id WHERE "
        + " AND ".join(conditions)
        + " ORDER BY m.taken_at_sort DESC,m.asset_id DESC LIMIT ?"
    )
    return connection.execute(sql, (*params, PAGE_SIZE + 1)).fetchall()


def _run_metered(
    connection: sqlite3.Connection,
    call: Callable[[], list[sqlite3.Row]],
    *,
    samples: int,
) -> dict[str, Any]:
    call()  # Warm SQLite page cache; this is not an OS-cold measurement.
    elapsed_ms = []
    vm_steps = []
    for _ in range(samples):
        ticks = [0]
        connection.set_progress_handler(lambda: ticks.__setitem__(0, ticks[0] + 1) or 0, 1_000)
        started = time.perf_counter_ns()
        call()
        elapsed_ms.append((time.perf_counter_ns() - started) / 1_000_000)
        connection.set_progress_handler(None, 0)
        vm_steps.append(ticks[0] * 1_000)
    return {
        "samples_ms": elapsed_ms,
        "median_ms": statistics.median(elapsed_ms),
        "max_ms": max(elapsed_ms),
        "median_vm_steps_approx": statistics.median(vm_steps),
        "vm_progress_interval": 1_000,
    }


def _plan(connection: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> list[str]:
    return [str(row["detail"]) for row in connection.execute("EXPLAIN QUERY PLAN " + sql, params)]


def _measure_scenario(favorite_count: int, density: float, *, samples: int) -> dict[str, Any]:
    asset_count = round(favorite_count / density)
    if density == 1.0:
        favorite_ids = list(range(1, asset_count + 1))
    else:
        favorite_ids = sorted(random.Random(23_101 + favorite_count).sample(range(1, asset_count + 1), favorite_count))
    with tempfile.TemporaryDirectory(prefix=f"favorites-{favorite_count}-{int(density * 100)}-", dir=".tmp") as temp, closing(
        _seed_catalog(Path(temp) / "PhotoSearchData", asset_count, favorite_ids)
    ) as catalog:
        connection = catalog.connection
        photo_sql, photo_params = photo_asset_sql(asset_alias="a")
        data: dict[str, Any] = {
            "favorite_count": favorite_count,
            "asset_count": asset_count,
            "favorite_density": density,
            "pages": {},
            "plans": {},
        }
        known_sql, known_params = _capture_known_query(
            "EXIF_ORIGINAL", photo_sql=photo_sql, photo_params=photo_params,
            limit=PAGE_SIZE + 1, after=None,
        )
        unknown_sql, unknown_params = _capture_unknown_query(
            photo_sql=photo_sql, photo_params=photo_params,
            limit=PAGE_SIZE + 1, after_asset_id=None,
        )
        data["plans"]["current_capture_forced"] = _plan(
            connection,
            "SELECT f.asset_id,m.taken_at_sort FROM asset_favorites AS f INDEXED BY idx_asset_favorites_recent "
            "JOIN assets AS a ON a.asset_id=f.asset_id LEFT JOIN asset_metadata AS m ON m.asset_id=f.asset_id "
            "WHERE a.state='ACTIVE' ORDER BY (NOT " + TRUSTED_SQL + "), CASE WHEN " + TRUSTED_SQL +
            " THEN m.taken_at_sort END DESC,f.asset_id DESC LIMIT ?",
            (PAGE_SIZE + 1,),
        )
        data["plans"]["current_capture_no_hint"] = _plan(
            connection,
            "SELECT f.asset_id,m.taken_at_sort FROM asset_favorites AS f "
            "JOIN assets AS a ON a.asset_id=f.asset_id LEFT JOIN asset_metadata AS m ON m.asset_id=f.asset_id "
            "WHERE a.state='ACTIVE' ORDER BY (NOT " + TRUSTED_SQL + "), CASE WHEN " + TRUSTED_SQL+
            " THEN m.taken_at_sort END DESC,f.asset_id DESC LIMIT ?",
            (PAGE_SIZE + 1,),
        )
        data["plans"]["optimized_capture_original"] = _plan(connection, known_sql, known_params)
        digitized_sql, digitized_params = _capture_known_query(
            "EXIF_DIGITIZED", photo_sql=photo_sql, photo_params=photo_params,
            limit=PAGE_SIZE + 1, after=None,
        )
        data["plans"]["optimized_capture_digitized"] = _plan(connection, digitized_sql, digitized_params)
        data["plans"]["optimized_unknown_tail"] = _plan(connection, unknown_sql, unknown_params)
        for name in ("current_capture_forced", "current_capture_no_hint"):
            if not any("USE TEMP B-TREE FOR ORDER BY" in step for step in data["plans"][name]):
                raise AssertionError(f"expected baseline temporary sort absent: {name}")
        for name in ("optimized_capture_original", "optimized_capture_digitized"):
            plan = data["plans"][name]
            if not any("idx_asset_metadata_capture" in step for step in plan):
                raise AssertionError(f"trusted capture index is not used: {name}")
            if any("USE TEMP B-TREE FOR ORDER BY" in step for step in plan):
                raise AssertionError(f"trusted capture query sorts into a temp B-tree: {name}")
        if any("USE TEMP B-TREE FOR ORDER BY" in step for step in data["plans"]["optimized_unknown_tail"]):
            raise AssertionError("unknown capture tail sorts into a temp B-tree")

        for order in ("recent", "capture_desc"):
            oracle = _oracle(favorite_ids, order)
            page_count = (len(oracle) + PAGE_SIZE - 1) // PAGE_SIZE
            deep_page = max(2, min(100, page_count // 2))
            page_anchors = {
                "first": (None, oracle[:PAGE_SIZE + 1]),
                "second": (oracle[PAGE_SIZE - 1], oracle[PAGE_SIZE:PAGE_SIZE * 2 + 1]),
                f"deep_page_{deep_page}": (
                    oracle[deep_page * PAGE_SIZE - 1] if deep_page * PAGE_SIZE <= len(oracle) else None,
                    oracle[deep_page * PAGE_SIZE:deep_page * PAGE_SIZE + PAGE_SIZE + 1],
                ),
            }
            for page_name, (anchor_row, expected_rows) in page_anchors.items():
                if page_name.startswith("deep") and anchor_row is None:
                    continue
                expected_ids = [row["asset_id"] for row in expected_rows[:PAGE_SIZE + 1]]
                if order == "recent":
                    recent_position = None if anchor_row is None else (anchor_row["added_at_ms"], anchor_row["asset_id"])
                    call = lambda pos=recent_position: _recent_page(
                        connection, photo_sql=photo_sql, photo_params=photo_params, position=pos,
                    )
                    measured = _run_metered(connection, call, samples=samples)
                    actual_ids = [int(row["asset_id"]) for row in call()]
                    if actual_ids != expected_ids:
                        raise AssertionError(
                            f"recent order mismatch: {favorite_count=} {density=} {page_name=} "
                            f"anchor={recent_position} got={actual_ids[:5]} expected={expected_ids[:5]}"
                        )
                    data["pages"][f"recent/{page_name}"] = {**measured, "ids_match_oracle": True}
                else:
                    capture_position = _capture_anchor(anchor_row)
                    baseline_call = lambda pos=capture_position: _baseline_capture_page(
                        connection, photo_sql=photo_sql, photo_params=photo_params, position=pos,
                    )
                    optimized_call = lambda pos=capture_position: _capture_favorite_rows(
                        connection, page_limit=PAGE_SIZE, photo_sql=photo_sql,
                        photo_params=photo_params, position=pos,
                    )
                    baseline = _run_metered(connection, baseline_call, samples=samples)
                    optimized = _run_metered(connection, optimized_call, samples=samples)
                    baseline_ids = [int(row["asset_id"]) for row in baseline_call()]
                    optimized_ids = [int(row["asset_id"]) for row in optimized_call()]
                    if baseline_ids != expected_ids or optimized_ids != expected_ids:
                        raise AssertionError(f"capture order mismatch: {favorite_count=} {density=} {page_name=}")
                    data["pages"][f"capture_desc/{page_name}"] = {
                        "baseline": {**baseline, "ids_match_oracle": True},
                        "optimized": {**optimized, "ids_match_oracle": True},
                    }
        if favorite_count == 50_000:
            initial_pages = int(connection.execute("PRAGMA page_count").fetchone()[0])
            page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
            started = time.perf_counter_ns()
            connection.execute(
                "CREATE INDEX idx_perf_capture_trusted ON asset_metadata(taken_at_sort DESC,asset_id DESC) "
                "WHERE time_source IN ('EXIF_ORIGINAL','EXIF_DIGITIZED') AND taken_at_sort IS NOT NULL"
            )
            index_build_ms = (time.perf_counter_ns() - started) / 1_000_000
            after_pages = int(connection.execute("PRAGMA page_count").fetchone()[0])
            candidate = {
                "build_ms": index_build_ms,
                "database_pages_added": after_pages - initial_pages,
                "page_size_bytes": page_size,
                "pages": {},
            }
            capture_oracle = _oracle(favorite_ids, "capture_desc")
            for anchor_name in ("first", "second", "deep_page_50"):
                if anchor_name == "first":
                    anchor_row = None
                    expected_start = 0
                elif anchor_name == "second":
                    anchor_row = capture_oracle[PAGE_SIZE - 1]
                    expected_start = PAGE_SIZE
                else:
                    anchor_row = capture_oracle[50 * PAGE_SIZE - 1]
                    expected_start = 50 * PAGE_SIZE
                after = None if anchor_row is None else (int(anchor_row["sort"]), int(anchor_row["asset_id"]))
                call = lambda pos=after: _partial_index_capture_page(
                    connection, photo_sql=photo_sql, photo_params=photo_params, after=pos,
                )
                measured = _run_metered(connection, call, samples=samples)
                actual = [int(row["asset_id"]) for row in call()]
                expected = [row["asset_id"] for row in capture_oracle[expected_start:expected_start + PAGE_SIZE + 1]]
                if actual != expected:
                    raise AssertionError(f"partial index capture mismatch: {favorite_count=} {density=} {anchor_name=}")
                candidate["pages"][anchor_name] = {**measured, "ids_match_oracle": True}
            candidate["plan"] = _plan(
                connection,
                "SELECT f.asset_id,m.taken_at_sort FROM asset_metadata AS m INDEXED BY idx_perf_capture_trusted "
                "CROSS JOIN asset_favorites AS f ON f.asset_id=m.asset_id "
                "CROSS JOIN assets AS a ON a.asset_id=f.asset_id "
                "WHERE m.time_source IN ('EXIF_ORIGINAL','EXIF_DIGITIZED') AND m.taken_at_sort IS NOT NULL "
                "AND a.state='ACTIVE' ORDER BY m.taken_at_sort DESC,m.asset_id DESC LIMIT ?",
                (PAGE_SIZE + 1,),
            )
            data["partial_index_candidate"] = candidate
        return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("test-results/P4-FAVORITES-UX-B-capture-sort-20261019.json"),
    )
    parser.add_argument("--samples", type=int, default=3)
    args = parser.parse_args()
    Path(".tmp").mkdir(exist_ok=True)
    result = {
        "git_head": subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "page_size": PAGE_SIZE,
        "samples_per_page": args.samples,
        "timing_scope": "SQLite query only, warmed process/page cache, SQL VM progress callback every 1000 opcodes",
        "scenarios": [],
    }
    for count in FAVORITE_COUNTS:
        for density in DENSITIES:
            result["scenarios"].append(_measure_scenario(count, density, samples=args.samples))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({
        "output": str(args.output),
        "scenarios": len(result["scenarios"]),
        "samples_per_page": args.samples,
        "all_oracle_checks_passed": all(
            page.get("ids_match_oracle", True)
            for scenario in result["scenarios"]
            for page in (
                *scenario["pages"].values(),
                *scenario.get("partial_index_candidate", {}).get("pages", {}).values(),
            )
        ),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
