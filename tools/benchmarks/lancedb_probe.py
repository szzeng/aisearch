"""P0-02B probe: prove embedded LanceDB persistence, restart, and CRUD on Windows.

The probe is throw-away evidence, not product code. It creates a fresh LanceDB directory in
a temporary directory, writes a fixed four-dimensional schema, runs a real nearest-neighbour
search, reopens the same directory from a separate Python process, and then performs add,
update, and delete operations with a real search after each one.

Run directly:

    uv run python tools/benchmarks/lancedb_probe.py

It prints a machine-readable JSON record on stdout. Failures produce a non-zero exit code
and a ``failure`` block instead of a PASS claim. The probe defines no product schema, no
migrations, no ANN index, no filters, and never touches a photo library.
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TASK_ID = "P0-02B"
TABLE_NAME = "probe_vectors"
VECTOR_COLUMN = "vector"
VECTOR_DIM = 4

SEED_ROWS: tuple[tuple[int, list[float], str], ...] = (
    (1, [1.0, 0.0, 0.0, 0.0], "alpha"),
    (2, [0.0, 1.0, 0.0, 0.0], "beta"),
    (3, [0.0, 0.0, 1.0, 0.0], "gamma"),
)
QUERY_VECTOR = [0.9, 0.4, 0.1, 0.0]
EXPECTED_AFTER_SEED = [1, 2, 3]

ADDED_ROW: tuple[int, list[float], str] = (4, [0.0, 0.0, 0.0, 1.0], "delta")
EXPECTED_AFTER_ADD = [1, 2, 3, 4]

UPDATED_VECTOR = [0.95, 0.35, 0.05, 0.0]
UPDATED_LABEL = "gamma-moved"
EXPECTED_AFTER_UPDATE = [3, 1, 2, 4]

DELETED_ID = 4
EXPECTED_AFTER_DELETE = [3, 1, 2]


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
        "schema": [],
        "seed_rows": [
            {"id": row_id, "vector": vector, "label": label} for row_id, vector, label in SEED_ROWS
        ],
        "operations": [],
        "searches": [],
        "reopen_checks": [],
        "row_ids": [],
        "labels_after_update": {},
        "stored_labels": {},
        "row_count": None,
        "temp_directory_removed": None,
        "residual_files": [],
        "failure": None,
    }


def _import_dependencies(record: dict[str, Any]) -> tuple[Any, Any]:
    try:
        import lancedb
        import pyarrow as pa
    except Exception as error:
        raise ProbeError("import_lancedb", error) from error
    record["lancedb_version"] = getattr(lancedb, "__version__", None)
    record["pyarrow_version"] = getattr(pa, "__version__", None)
    return lancedb, pa


def _connect(lancedb: Any, database_path: Path) -> Any:
    try:
        return lancedb.connect(str(database_path))
    except Exception as error:
        raise ProbeError("connect", error) from error


def _schema(pa: Any) -> Any:
    return pa.schema(
        [
            pa.field("id", pa.int64(), nullable=False),
            pa.field(VECTOR_COLUMN, pa.list_(pa.float32(), VECTOR_DIM), nullable=False),
            pa.field("label", pa.string(), nullable=False),
        ]
    )


def _rows(entries: Sequence[tuple[int, list[float], str]]) -> list[dict[str, Any]]:
    return [{"id": row_id, "vector": vector, "label": label} for row_id, vector, label in entries]


def _search(table: Any, limit: int) -> list[dict[str, Any]]:
    try:
        results = (
            table.search(QUERY_VECTOR, vector_column_name=VECTOR_COLUMN).limit(limit).to_list()
        )
    except Exception as error:
        raise ProbeError("search", error) from error
    rows = []
    for result in results:
        distance = result.get("_distance")
        rows.append(
            {
                "id": int(result["id"]),
                "label": result["label"],
                "distance": None if distance is None else round(float(distance), 6),
            }
        )
    return rows


def _search_record(
    stage: str, limit: int, rows: Sequence[dict[str, Any]], expected: list[int]
) -> dict[str, Any]:
    ids = [row["id"] for row in rows]
    return {
        "stage": stage,
        "limit": limit,
        "ids": ids,
        "labels": [row["label"] for row in rows],
        "distances": [row["distance"] for row in rows],
        "expected_ids": expected,
        "passed": ids == expected,
    }


def _require(entry: dict[str, Any], stage: str) -> dict[str, Any]:
    if not entry.get("passed"):
        detail = json.dumps(entry, ensure_ascii=False)
        raise ProbeError(stage, AssertionError(f"unexpected result: {detail}"))
    return entry


def _scan(table: Any) -> list[dict[str, Any]]:
    rows = table.to_arrow().to_pylist()
    return [
        {"id": int(row["id"]), "label": row["label"]} for row in sorted(rows, key=lambda r: r["id"])
    ]


def _operation_summary(result: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {"result_type": type(result).__name__}
    for attribute in (
        "num_updated_rows",
        "num_removed_rows",
        "num_inserted_rows",
        "num_deleted_rows",
    ):
        value = getattr(result, attribute, None)
        if value is not None:
            summary[attribute] = int(value)
    return summary


def _reopen_check(
    database_path: Path,
    expect_ids: list[int],
    expect_row_count: int,
    stage: str,
    json_out: Path,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--reopen-check",
        "--database",
        str(database_path),
        "--expect-ids",
        ",".join(str(value) for value in expect_ids),
        "--expect-rows",
        str(expect_row_count),
        "--stage",
        stage,
        "--json-out",
        str(json_out),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", check=False
    )
    record: dict[str, Any] = {
        "stage": stage,
        "process": "separate_python_process",
        "exit_code": completed.returncode,
        "expected_ids": expect_ids,
        "expected_row_count": expect_row_count,
        "actual_ids": None,
        "actual_row_count": None,
        "passed": False,
        "stderr_tail": completed.stderr[-400:] if completed.stderr else "",
    }
    try:
        child = json.loads(json_out.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        record["parse_error"] = f"{type(error).__name__}: {error}"
        return record
    record["actual_ids"] = child.get("ids")
    record["actual_row_count"] = child.get("row_count")
    record["child_status"] = child.get("status")
    record["child_failure"] = child.get("failure")
    record["passed"] = (
        completed.returncode == 0
        and child.get("status") == "pass"
        and record["actual_ids"] == expect_ids
        and record["actual_row_count"] == expect_row_count
    )
    return record


def _run_full_probe(database_path: Path, record: dict[str, Any]) -> None:
    lancedb, pa = _import_dependencies(record)
    record["schema"] = [
        {"name": field.name, "type": str(field.type), "nullable": field.nullable}
        for field in _schema(pa)
    ]

    connection = _connect(lancedb, database_path)
    try:
        table = connection.create_table(TABLE_NAME, data=_rows(SEED_ROWS), schema=_schema(pa))
    except Exception as error:
        raise ProbeError("create_table", error) from error
    record["operations"].append(
        {
            "op": "create_table",
            "rows": [row_id for row_id, _, _ in SEED_ROWS],
            "row_count": table.count_rows(),
        }
    )

    after_seed = _search(table, len(SEED_ROWS))
    record["searches"].append(
        _search_record("after_seed", len(SEED_ROWS), after_seed, EXPECTED_AFTER_SEED)
    )
    _require(record["searches"][-1], "match_after_seed")

    reopen_after_seed = _reopen_check(
        database_path,
        EXPECTED_AFTER_SEED,
        len(SEED_ROWS),
        "after_seed",
        database_path.parent / "reopen_after_seed.json",
    )
    record["reopen_checks"].append(reopen_after_seed)
    _require(reopen_after_seed, "reopen_after_seed")

    table.add(_rows([ADDED_ROW]))
    record["operations"].append(
        {"op": "add", "rows": [ADDED_ROW[0]], "row_count": table.count_rows()}
    )
    after_add = _search(table, len(EXPECTED_AFTER_ADD))
    record["searches"].append(
        _search_record("after_add", len(EXPECTED_AFTER_ADD), after_add, EXPECTED_AFTER_ADD)
    )
    _require(record["searches"][-1], "match_after_add")

    update_result = table.update(
        where=f"id = {SEED_ROWS[-1][0]}",
        values={VECTOR_COLUMN: UPDATED_VECTOR, "label": UPDATED_LABEL},
    )
    record["operations"].append(
        {
            "op": "update",
            "where": f"id = {SEED_ROWS[-1][0]}",
            "values": {"vector": UPDATED_VECTOR, "label": UPDATED_LABEL},
            "row_count": table.count_rows(),
            **_operation_summary(update_result),
        }
    )
    after_update = _search(table, len(EXPECTED_AFTER_UPDATE))
    record["searches"].append(
        _search_record(
            "after_update", len(EXPECTED_AFTER_UPDATE), after_update, EXPECTED_AFTER_UPDATE
        )
    )
    _require(record["searches"][-1], "match_after_update")
    labels_after_update = {row["id"]: row["label"] for row in _scan(table)}
    record["labels_after_update"] = labels_after_update
    if labels_after_update.get(SEED_ROWS[-1][0]) != UPDATED_LABEL:
        raise ProbeError(
            "read_back_update",
            AssertionError(
                f"label for id {SEED_ROWS[-1][0]} is {labels_after_update.get(SEED_ROWS[-1][0])!r}"
            ),
        )

    delete_result = table.delete(f"id = {DELETED_ID}")
    record["operations"].append(
        {
            "op": "delete",
            "where": f"id = {DELETED_ID}",
            "row_count": table.count_rows(),
            **_operation_summary(delete_result),
        }
    )
    after_delete = _search(table, len(EXPECTED_AFTER_DELETE) + 1)
    record["searches"].append(
        _search_record(
            "after_delete", len(EXPECTED_AFTER_DELETE) + 1, after_delete, EXPECTED_AFTER_DELETE
        )
    )
    _require(record["searches"][-1], "match_after_delete")

    scanned = _scan(table)
    record["row_ids"] = [row["id"] for row in scanned]
    record["stored_labels"] = {row["id"]: row["label"] for row in scanned}
    record["row_count"] = len(scanned)
    if record["row_ids"] != sorted(EXPECTED_AFTER_DELETE):
        raise ProbeError(
            "scan_after_delete",
            AssertionError(f"stored ids {record['row_ids']} != {sorted(EXPECTED_AFTER_DELETE)}"),
        )

    if DELETED_ID in record["stored_labels"]:
        raise ProbeError(
            "scan_after_delete",
            AssertionError(f"deleted id {DELETED_ID} is still stored"),
        )

    reopen_after_crud = _reopen_check(
        database_path,
        EXPECTED_AFTER_DELETE,
        len(EXPECTED_AFTER_DELETE),
        "after_crud",
        database_path.parent / "reopen_after_crud.json",
    )
    record["reopen_checks"].append(reopen_after_crud)
    _require(reopen_after_crud, "reopen_after_crud")

    del table
    del connection
    gc.collect()


def _run_reopen_check(
    database_path: Path,
    expect_ids: list[int],
    expect_row_count: int,
    stage: str,
    record: dict[str, Any],
) -> None:
    lancedb, _ = _import_dependencies(record)
    record["expected_ids"] = expect_ids
    record["expected_row_count"] = expect_row_count
    record["stage"] = stage
    connection = _connect(lancedb, database_path)
    try:
        table = connection.open_table(TABLE_NAME)
    except Exception as error:
        raise ProbeError("open_table", error) from error
    rows = _search(table, len(expect_ids) + 1)
    record["ids"] = [row["id"] for row in rows]
    record["labels"] = [row["label"] for row in rows]
    record["row_count"] = table.count_rows()
    passed = record["ids"] == expect_ids and record["row_count"] == expect_row_count
    if not passed:
        raise ProbeError(
            f"reopen_check_{stage}",
            AssertionError(
                f"ids {record['ids']} (expected {expect_ids}), "
                f"rows {record['row_count']} (expected {expect_row_count})"
            ),
        )
    del table
    del connection
    gc.collect()


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
    parser = argparse.ArgumentParser(description="P0-02B embedded LanceDB probe")
    parser.add_argument("--database", type=Path, default=None, help="LanceDB directory to create")
    parser.add_argument(
        "--json-out", type=Path, default=None, help="also write the JSON record here"
    )
    parser.add_argument("--keep-temp", action="store_true", help="keep the temporary directory")
    parser.add_argument("--reopen-check", action="store_true", help="read-only reopen mode")
    parser.add_argument(
        "--expect-ids", type=str, default="", help="comma-separated ids for --reopen-check"
    )
    parser.add_argument(
        "--expect-rows", type=int, default=0, help="expected row count for --reopen-check"
    )
    parser.add_argument(
        "--stage", type=str, default="manual", help="label recorded for --reopen-check"
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
    mode = "reopen_check" if args.reopen_check else "full"
    record = _base_record(mode)
    temp_directory: Path | None = None
    exit_code = 1
    try:
        if args.database is None:
            if args.reopen_check:
                raise ProbeError("prepare", ValueError("--reopen-check requires --database"))
            temp_directory = Path(tempfile.mkdtemp(prefix="photo_search_lancedb_probe_"))
            database_path = temp_directory / "probe.lance"
        else:
            database_path = Path(args.database)
        database_path = database_path.resolve()
        record["database_path"] = str(database_path)
        if args.reopen_check:
            expect_ids = [int(value) for value in args.expect_ids.split(",") if value.strip()]
            _run_reopen_check(database_path, expect_ids, args.expect_rows, args.stage, record)
        else:
            if database_path.exists():
                raise ProbeError(
                    "prepare",
                    FileExistsError(f"refusing to overwrite existing path: {database_path}"),
                )
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
