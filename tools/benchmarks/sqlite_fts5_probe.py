"""P0-02A probe: prove SQLite FTS5 works on this Windows CPython with an on-disk database.

The probe is throw-away evidence, not product code. It creates a fresh SQLite database in
a temporary directory, builds one ordinary content table plus one FTS5 index table, writes
two distinguishable rows (one Chinese token, one English token), runs real ``MATCH``
queries, closes and reopens the same database, and repeats the queries.

Run directly:

    uv run python tools/benchmarks/sqlite_fts5_probe.py

It prints a machine-readable JSON record on stdout. FTS5 creation or matching failures
produce a non-zero exit code and a ``failure`` block instead of a PASS claim. The probe
defines no catalog schema, no migrations, no HTTP search, and never touches a photo
library.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TASK_ID = "P0-02A"

CREATE_DOCUMENTS_SQL = (
    "CREATE TABLE probe_documents ("
    "id INTEGER PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL)"
)
CREATE_FTS_SQL = "CREATE VIRTUAL TABLE probe_fts USING fts5(title, body)"
FTS_INTEGRITY_SQL = "INSERT INTO probe_fts(probe_fts) VALUES('integrity-check')"

SEED_ROWS: tuple[tuple[int, str, str], ...] = (
    (1, "海边日落", "中文探针样本甲"),
    (2, "Sunset Beach", "english probe sample"),
)

# One exact Chinese query and one English query, each with the identifiers it must return.
EXPECTED_MATCHES: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("海边日落", (1,)),
    ("Sunset", (2,)),
)

# Boundary observation only. The built-in unicode61 tokenizer keeps a run of CJK
# characters as a single token, so a sub-string is not expected to match. This probe
# therefore claims no Chinese word segmentation, no query escaping, no external-content
# trigger synchronisation, and no HTTP search behaviour.
SUBSTRING_QUERY = "日落"


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


def _base_record() -> dict[str, Any]:
    return {
        "task": TASK_ID,
        "status": "fail",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_executable": sys.executable,
        "sqlite_version": sqlite3.sqlite_version,
        "sqlite_source_id": None,
        "database_path": None,
        "database_size_bytes": None,
        "journal_mode": None,
        "fts5_available": False,
        "fts5_create_sql": CREATE_FTS_SQL,
        "seed_rows": [
            {"id": row_id, "title": title, "body": body} for row_id, title, body in SEED_ROWS
        ],
        "documents": [],
        "steps": [],
        "queries": [],
        "boundary_observations": [],
        "restart_query_results_match": False,
        "temp_directory_removed": None,
        "residual_files": [],
        "failure": None,
    }


def _execute(
    connection: sqlite3.Connection,
    sql: str,
    stage: str,
    parameters: Sequence[Any] = (),
) -> sqlite3.Cursor:
    try:
        return connection.execute(sql, parameters)
    except sqlite3.Error as error:
        raise ProbeError(stage, error) from error


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _open(database_path: Path) -> sqlite3.Connection:
    try:
        return sqlite3.connect(database_path, isolation_level=None)
    except sqlite3.Error as error:
        raise ProbeError("connect", error) from error


def _create_and_seed(connection: sqlite3.Connection) -> None:
    _execute(connection, "BEGIN IMMEDIATE", "begin_transaction")
    try:
        _execute(connection, CREATE_DOCUMENTS_SQL, "create_documents_table")
        _execute(connection, CREATE_FTS_SQL, "create_fts5_table")
        for row_id, title, body in SEED_ROWS:
            _execute(
                connection,
                "INSERT INTO probe_documents (id, title, body) VALUES (?, ?, ?)",
                "seed_documents",
                (row_id, title, body),
            )
            _execute(
                connection,
                "INSERT INTO probe_fts (rowid, title, body) VALUES (?, ?, ?)",
                "seed_fts",
                (row_id, title, body),
            )
        _execute(connection, FTS_INTEGRITY_SQL, "fts_integrity_check")
        _execute(connection, "COMMIT", "commit_transaction")
    except BaseException:
        _rollback(connection)
        raise


def _documents(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = _execute(
        connection,
        "SELECT id, title, body FROM probe_documents ORDER BY id",
        "read_documents",
    ).fetchall()
    return [{"id": int(row[0]), "title": row[1], "body": row[2]} for row in rows]


def _match(connection: sqlite3.Connection, query: str) -> list[int]:
    rows = _execute(
        connection,
        "SELECT rowid FROM probe_fts WHERE probe_fts MATCH ? ORDER BY rowid",
        "match",
        (query,),
    ).fetchall()
    return [int(row[0]) for row in rows]


def _query_pass(connection: sqlite3.Connection, stage: str) -> list[dict[str, Any]]:
    entries = []
    for query, expected in EXPECTED_MATCHES:
        actual = _match(connection, query)
        entries.append(
            {
                "stage": stage,
                "match": query,
                "expected_rowids": list(expected),
                "actual_rowids": actual,
                "passed": actual == list(expected),
            }
        )
    return entries


def _assert_pass(entries: Sequence[dict[str, Any]], stage: str) -> None:
    failures = [entry for entry in entries if not entry["passed"]]
    if failures:
        detail = json.dumps(failures, ensure_ascii=False)
        raise ProbeError(stage, AssertionError(f"unexpected MATCH result: {detail}"))


def _run_probe(database_path: Path, record: dict[str, Any]) -> None:
    recorded_queries = record["queries"]
    connection = _open(database_path)
    try:
        source_id = _execute(connection, "SELECT sqlite_source_id()", "read_source_id").fetchone()
        record["sqlite_source_id"] = source_id[0]
        journal_mode = _execute(
            connection, "PRAGMA journal_mode = WAL", "set_journal_mode"
        ).fetchone()
        record["journal_mode"] = journal_mode[0]
        _create_and_seed(connection)
        record["fts5_available"] = True
        record["steps"].extend(
            [
                {"name": "create_documents_table", "ok": True, "sql": CREATE_DOCUMENTS_SQL},
                {"name": "create_fts5_table", "ok": True, "sql": CREATE_FTS_SQL},
                {
                    "name": "seed_rows",
                    "ok": True,
                    "rowids": [row_id for row_id, _, _ in SEED_ROWS],
                    "integrity_check": FTS_INTEGRITY_SQL,
                },
            ]
        )
        record["documents"] = _documents(connection)
        first_pass = _query_pass(connection, "first_connection")
        recorded_queries.extend(first_pass)
        _assert_pass(first_pass, "match")
        record["boundary_observations"].append(
            {
                "stage": "first_connection",
                "match": SUBSTRING_QUERY,
                "actual_rowids": _match(connection, SUBSTRING_QUERY),
                "note": "sub-string of a stored CJK token; no word segmentation is claimed",
            }
        )
    finally:
        connection.close()

    reopened = _open(database_path)
    try:
        second_pass = _query_pass(reopened, "after_reopen")
    finally:
        reopened.close()

    recorded_queries.extend(second_pass)
    _assert_pass(second_pass, "reopen_match")
    record["steps"].append({"name": "close_and_reopen", "ok": True})
    consistent = [(entry["match"], entry["actual_rowids"]) for entry in first_pass] == [
        (entry["match"], entry["actual_rowids"]) for entry in second_pass
    ]
    record["restart_query_results_match"] = consistent
    if not consistent:
        raise ProbeError(
            "reopen_consistency",
            AssertionError("reopened database returned different MATCH results"),
        )
    record["database_size_bytes"] = database_path.stat().st_size


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P0-02A SQLite FTS5 on-disk probe")
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="database path to create; defaults to a fresh directory under the temp dir",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="optional path that receives the same JSON record as stdout",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="keep the auto-created temporary directory instead of removing it",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = _parse_args(argv)
    record = _base_record()
    temp_directory: Path | None = None
    exit_code = 1
    try:
        if args.database is None:
            temp_directory = Path(tempfile.mkdtemp(prefix="photo_search_fts5_probe_"))
            database_path = temp_directory / "fts5_probe.db"
        else:
            database_path = Path(args.database)
        database_path = database_path.resolve()
        record["database_path"] = str(database_path)
        if database_path.exists():
            raise ProbeError(
                "prepare", FileExistsError(f"refusing to overwrite existing file: {database_path}")
            )
        _run_probe(database_path, record)
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
                shutil.rmtree(temp_directory, ignore_errors=True)
                residual = sorted(str(path) for path in temp_directory.rglob("*"))
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

    payload = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    sys.stdout.write(payload)
    if args.json_out is not None:
        try:
            Path(args.json_out).write_text(payload, encoding="utf-8")
        except OSError as error:
            print(f"warning: could not write --json-out file: {error}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
