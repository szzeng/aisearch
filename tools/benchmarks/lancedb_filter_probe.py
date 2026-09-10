"""P0-02C probe: prove LanceDB candidate filtering and unindexed-new-item retrieval.

The probe is throw-away evidence, not product code. It creates a fresh LanceDB directory in
a temporary folder, writes an explicit schema with two filterable metadata columns plus a
boolean visibility flag, runs real filtered nearest-neighbour searches, and then adds a new
vector that no maintenance or index build has ever seen.

Run directly:

    uv run python tools/benchmarks/lancedb_filter_probe.py

Modes:

* default        - full filtered-search scenario; exits 0 only when every assertion holds.
* --invalid-filter-check - issues a search with a filter that names a missing column. The
  probe exits non-zero either way and records whether LanceDB rejected the filter or
  silently returned rows for it.

Failures produce a ``failure`` block with the real stage, exception type, and message. The
probe defines no product schema, migrations, indexes, filters, or HTTP search, and never
touches a photo library.
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import shutil
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TASK_ID = "P0-02C"
TABLE_NAME = "probe_vectors"
VECTOR_COLUMN = "vector"
VECTOR_DIM = 4

# id, vector, space_id, source_id, visible
ROWS: tuple[tuple[int, list[float], int, int, bool], ...] = (
    (1, [0.90, 0.30, 0.00, 0.00], 10, 100, True),
    (2, [0.60, 0.80, 0.00, 0.00], 10, 100, True),
    (3, [0.00, 0.00, 1.00, 0.00], 10, 200, True),
    (11, [1.00, 0.05, 0.00, 0.00], 20, 100, True),
    (12, [0.95, 0.20, 0.20, 0.00], 20, 200, True),
    (13, [0.10, 0.00, 0.00, 1.00], 20, 200, True),
    (14, [0.70, 0.70, 0.00, 0.00], 20, 100, False),
)

ADDED_ROW: tuple[int, list[float], int, int, bool] = (21, [0.95, 0.15, 0.05, 0.00], 10, 100, True)

QUERY_VECTOR = [1.0, 0.0, 0.0, 0.0]
FAST_SEARCH_ENABLED = False
INVALID_FILTER = "no_such_column = 1"

# stage, where-clause, limit, expected ids
QUERIES_BEFORE_ADD: tuple[tuple[str, str | None, int, tuple[int, ...]], ...] = (
    ("unfiltered_all", None, 7, (11, 12, 1, 14, 2, 13, 3)),
    ("filtered_group_a", "space_id = 10", 3, (1, 2, 3)),
    ("filtered_space_and_source", "space_id = 10 AND source_id = 100", 3, (1, 2)),
    ("filtered_visible_only", "visible = true", 6, (11, 12, 1, 2, 13, 3)),
    ("filtered_group_b", "space_id = 20", 4, (11, 12, 14, 13)),
)
QUERIES_AFTER_ADD: tuple[tuple[str, str | None, int, tuple[int, ...]], ...] = (
    ("after_add_unfiltered_all", None, 8, (11, 21, 12, 1, 14, 2, 13, 3)),
    ("after_add_filtered_group_a", "space_id = 10", 4, (21, 1, 2, 3)),
    ("after_add_filtered_space_and_source", "space_id = 10 AND source_id = 100", 3, (21, 1, 2)),
)
# The pre-filter evidence query must return a row that a global top-`limit` window could
# never contain; otherwise filtering could have happened after an unfiltered top-k.
PRE_FILTER_STAGE = "filtered_group_a"


class ProbeError(RuntimeError):
    """Failure with the stage that raised it, so the JSON record stays honest."""

    def __init__(self, stage: str, error: BaseException) -> None:
        super().__init__(f"{stage}: {type(error).__name__}: {error}")
        self.stage = stage
        self.error = error

    def as_dict(self) -> dict[str, str]:
        return {
            "stage": self.stage,
            "exception_type": type(self.error).__name__,
            "message": str(self.error),
        }


def _base_record(mode: str) -> dict[str, Any]:
    return {
        "task": TASK_ID,
        "mode": mode,
        "status": "fail",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "lancedb_version": None,
        "pyarrow_version": None,
        "database_path": None,
        "table": TABLE_NAME,
        "vector_column": VECTOR_COLUMN,
        "vector_dim": VECTOR_DIM,
        "query_vector": QUERY_VECTOR,
        "filter_semantics": "LanceDB LanceQueryBuilder.where() pre-filter",
        "fast_search_enabled": FAST_SEARCH_ENABLED,
        "maintenance_calls": [],
        "schema": [],
        "rows": _rows_as_dicts(ROWS),
        "added_row": _row_as_dict(ADDED_ROW),
        "index_state": {},
        "queries": [],
        "exclusion_evidence": None,
        "pre_filter_evidence": None,
        "invalid_filter": None,
        "row_count": None,
        "temp_directory_removed": None,
        "residual_files": [],
        "failure": None,
    }


def _row_as_dict(entry: tuple[int, list[float], int, int, bool]) -> dict[str, Any]:
    row_id, vector, space_id, source_id, visible = entry
    return {
        "id": row_id,
        "vector": vector,
        "space_id": space_id,
        "source_id": source_id,
        "visible": visible,
    }


def _rows_as_dicts(
    entries: Sequence[tuple[int, list[float], int, int, bool]],
) -> list[dict[str, Any]]:
    return [_row_as_dict(entry) for entry in entries]


def _import_dependencies(record: dict[str, Any]) -> tuple[Any, Any]:
    try:
        import lancedb
        import pyarrow as pa
    except Exception as error:
        raise ProbeError("import_lancedb", error) from error
    record["lancedb_version"] = getattr(lancedb, "__version__", None)
    record["pyarrow_version"] = getattr(pa, "__version__", None)
    return lancedb, pa


def _schema(pa: Any) -> Any:
    return pa.schema(
        [
            pa.field("id", pa.int64(), nullable=False),
            pa.field(VECTOR_COLUMN, pa.list_(pa.float32(), VECTOR_DIM), nullable=False),
            pa.field("space_id", pa.int64(), nullable=False),
            pa.field("source_id", pa.int64(), nullable=False),
            pa.field("visible", pa.bool_(), nullable=False),
        ]
    )


def _connect(lancedb: Any, database_path: Path) -> Any:
    try:
        return lancedb.connect(str(database_path))
    except Exception as error:
        raise ProbeError("connect", error) from error


def _create_table(connection: Any, pa: Any) -> Any:
    try:
        return connection.create_table(TABLE_NAME, data=_rows_as_dicts(ROWS), schema=_schema(pa))
    except Exception as error:
        raise ProbeError("create_table", error) from error


def _index_names(table: Any) -> list[str]:
    try:
        return [getattr(index, "name", str(index)) for index in table.list_indices()]
    except Exception as error:
        raise ProbeError("list_indices", error) from error


def _search(table: Any, limit: int, where: str | None) -> list[dict[str, Any]]:
    builder = table.search(QUERY_VECTOR, vector_column_name=VECTOR_COLUMN)
    if where is not None:
        builder = builder.where(where)
    try:
        results = builder.limit(limit).to_list()
    except Exception as error:
        raise ProbeError("search", error) from error
    rows = []
    for result in results:
        distance = result.get("_distance")
        rows.append(
            {
                "id": int(result["id"]),
                "space_id": int(result["space_id"]),
                "source_id": int(result["source_id"]),
                "visible": bool(result["visible"]),
                "distance": None if distance is None else round(float(distance), 6),
            }
        )
    return rows


def _global_rank_map(table: Any, total: int) -> dict[int, int]:
    return {row["id"]: index for index, row in enumerate(_search(table, total, None))}


def _query_entry(
    table: Any,
    stage: str,
    where: str | None,
    limit: int,
    expected: tuple[int, ...],
    rank_map: dict[int, int],
) -> dict[str, Any]:
    rows = _search(table, limit, where)
    ids = [row["id"] for row in rows]
    return {
        "stage": stage,
        "filter": where,
        "limit": limit,
        "ids": ids,
        "distances": [row["distance"] for row in rows],
        "expected_ids": list(expected),
        "global_ranks": [rank_map.get(row_id) for row_id in ids],
        "passed": ids == list(expected),
    }


def _require(entry: dict[str, Any], stage: str) -> dict[str, Any]:
    if not entry.get("passed"):
        detail = json.dumps(entry, ensure_ascii=False)
        raise ProbeError(stage, AssertionError(f"unexpected result: {detail}"))
    return entry


def _run_queries(
    table: Any,
    record: dict[str, Any],
    specs: Sequence[tuple[str, str | None, int, tuple[int, ...]]],
    phase: str,
) -> list[dict[str, Any]]:
    total = table.count_rows()
    rank_map = _global_rank_map(table, total)
    entries = [
        _query_entry(table, stage, where, limit, expected, rank_map)
        for stage, where, limit, expected in specs
    ]
    for entry in entries:
        _require(entry, f"{phase}:{entry['stage']}")
    record["queries"].extend(entries)
    return entries


def _run_full_probe(database_path: Path, record: dict[str, Any]) -> None:
    lancedb, pa = _import_dependencies(record)
    record["schema"] = [
        {"name": field.name, "type": str(field.type), "nullable": field.nullable}
        for field in _schema(pa)
    ]

    connection = _connect(lancedb, database_path)
    table = _create_table(connection, pa)
    record["index_state"]["after_create"] = _index_names(table)
    record["row_count"] = table.count_rows()

    before_add = _run_queries(table, record, QUERIES_BEFORE_ADD, "before_add")

    pre_filter = next(entry for entry in before_add if entry["stage"] == PRE_FILTER_STAGE)
    ranks = [rank for rank in pre_filter["global_ranks"] if rank is not None]
    pre_filter_evidence = {
        "stage": PRE_FILTER_STAGE,
        "filter": pre_filter["filter"],
        "limit": pre_filter["limit"],
        "returned_ids": pre_filter["ids"],
        "global_ranks": pre_filter["global_ranks"],
        "max_global_rank": max(ranks) if ranks else None,
        "conclusion": (
            "a global top-limit window could not contain the last result, so the filter "
            "restricted the candidate set before ranking"
        ),
    }
    record["pre_filter_evidence"] = pre_filter_evidence
    if not ranks or max(ranks) < pre_filter["limit"]:
        raise ProbeError(
            "pre_filter_evidence",
            AssertionError(
                "filtered results are consistent with post-filtering a global top-k window"
            ),
        )

    excluded_id = 11
    exclusion_evidence = {
        "global_nearest_id": excluded_id,
        "filter": "space_id = 10",
        "top_id": pre_filter["ids"][0],
        "returned_ids": pre_filter["ids"],
        "excluded_id_present": excluded_id in pre_filter["ids"],
    }
    record["exclusion_evidence"] = exclusion_evidence
    if exclusion_evidence["excluded_id_present"]:
        raise ProbeError(
            "exclusion_evidence",
            AssertionError(f"filtered search returned excluded id {excluded_id}"),
        )
    if exclusion_evidence["top_id"] != 1:
        raise ProbeError(
            "exclusion_evidence",
            AssertionError(f"expected in-group nearest id 1, got {exclusion_evidence['top_id']}"),
        )

    try:
        table.add(_rows_as_dicts([ADDED_ROW]))
    except Exception as error:
        raise ProbeError("add_new_row", error) from error
    record["index_state"]["after_add"] = _index_names(table)
    record["row_count"] = table.count_rows()

    after_add = _run_queries(table, record, QUERIES_AFTER_ADD, "after_add")
    new_id = ADDED_ROW[0]
    for entry in after_add:
        if new_id not in entry["ids"]:
            raise ProbeError(
                f"unindexed_new_item:{entry['stage']}",
                AssertionError(f"new id {new_id} missing from {entry['filter'] or 'no filter'}"),
            )

    del table
    del connection
    gc.collect()


def _run_invalid_filter_check(database_path: Path, record: dict[str, Any]) -> None:
    lancedb, pa = _import_dependencies(record)
    connection = _connect(lancedb, database_path)
    table = _create_table(connection, pa)
    record["index_state"]["after_create"] = _index_names(table)
    record["row_count"] = table.count_rows()

    try:
        results = (
            table.search(QUERY_VECTOR, vector_column_name=VECTOR_COLUMN)
            .where(INVALID_FILTER)
            .limit(3)
            .to_list()
        )
    except Exception as error:
        record["invalid_filter"] = {
            "expression": INVALID_FILTER,
            "raised": True,
            "exception_type": type(error).__name__,
            "message": str(error),
            "returned_ids": None,
        }
        raise ProbeError("invalid_filter", error) from error

    returned = [int(row["id"]) for row in results]
    record["invalid_filter"] = {
        "expression": INVALID_FILTER,
        "raised": False,
        "exception_type": None,
        "message": None,
        "returned_ids": returned,
    }
    raise ProbeError(
        "invalid_filter",
        AssertionError(f"invalid filter was silently ignored and returned ids {returned}"),
    )


def _cleanup(directory: Path) -> list[str]:
    residual: list[str] = []
    for _ in range(5):
        shutil.rmtree(directory, ignore_errors=True)
        if not directory.exists():
            return []
        residual = sorted(str(path) for path in directory.rglob("*"))
        gc.collect()
        time.sleep(0.3)
    return residual or [str(directory)]


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P0-02C LanceDB filter probe")
    parser.add_argument("--database", type=Path, default=None, help="LanceDB directory")
    parser.add_argument("--json-out", type=Path, default=None, help="also write JSON here")
    parser.add_argument("--keep-temp", action="store_true", help="keep the temp dir")
    parser.add_argument(
        "--invalid-filter-check",
        action="store_true",
        help="run only the invalid-filter failure path",
    )
    return parser.parse_args(argv)


def _write_payload(record: dict[str, Any], json_out: Path | None) -> None:
    payload = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    sys.stdout.write(payload)
    if json_out is not None:
        try:
            Path(json_out).write_text(payload, encoding="utf-8")
        except OSError as error:
            print(f"warning: could not write --json-out file: {error}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = _parse_args(argv)
    mode = "invalid_filter_check" if args.invalid_filter_check else "full"
    record = _base_record(mode)
    temp_directory: Path | None = None
    exit_code = 1
    try:
        if args.database is None:
            temp_directory = Path(tempfile.mkdtemp(prefix="photo_search_lancedb_filter_"))
            database_path = temp_directory / "probe.lance"
        else:
            database_path = Path(args.database)
        database_path = database_path.resolve()
        record["database_path"] = str(database_path)
        if database_path.exists():
            raise ProbeError(
                "prepare",
                FileExistsError(f"refusing to overwrite existing path: {database_path}"),
            )
        if args.invalid_filter_check:
            _run_invalid_filter_check(database_path, record)
        else:
            _run_full_probe(database_path, record)
        record["status"] = "pass"
        exit_code = 0
    except ProbeError as error:
        record["status"] = "fail"
        record["failure"] = error.as_dict()
        exit_code = 1
    except Exception as error:  # noqa: BLE001 - any unexpected error must not report PASS
        record["status"] = "fail"
        record["failure"] = {
            "stage": "unexpected",
            "exception_type": type(error).__name__,
            "message": str(error),
        }
        exit_code = 1
    finally:
        if temp_directory is not None:
            if args.keep_temp:
                record["temp_directory_removed"] = False
                record["residual_files"] = sorted(str(path) for path in temp_directory.rglob("*"))
            else:
                residual = _cleanup(temp_directory)
                record["temp_directory_removed"] = not residual
                record["residual_files"] = residual
                if residual and exit_code == 0:
                    record["status"] = "fail"
                    record["failure"] = {
                        "stage": "cleanup",
                        "exception_type": "OSError",
                        "message": "temporary probe files remained after cleanup",
                    }
                    exit_code = 1
    _write_payload(record, args.json_out)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
