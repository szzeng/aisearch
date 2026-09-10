"""P0-02A regression: run the SQLite FTS5 probe for real and check its evidence."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROBE_SCRIPT = PROJECT_ROOT / "tools" / "benchmarks" / "sqlite_fts5_probe.py"


def _run_probe(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PROBE_SCRIPT), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _load_record(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return json.loads(completed.stdout)


def test_probe_builds_queries_and_reopens_the_same_database(tmp_path: Path) -> None:
    database_path = tmp_path / "fts5_probe.db"
    json_out = tmp_path / "fts5_probe.json"

    completed = _run_probe("--database", str(database_path), "--json-out", str(json_out))

    assert completed.returncode == 0, completed.stdout + completed.stderr
    record = _load_record(completed)
    assert record["status"] == "pass"
    assert record["fts5_available"] is True
    assert record["restart_query_results_match"] is True
    assert record["failure"] is None
    assert database_path.is_file()
    assert json.loads(json_out.read_text(encoding="utf-8")) == record

    stages = {entry["stage"] for entry in record["queries"]}
    assert stages == {"first_connection", "after_reopen"}
    for entry in record["queries"]:
        assert entry["passed"] is True
        assert entry["actual_rowids"] == entry["expected_rowids"]

    matches = {entry["match"]: entry["actual_rowids"] for entry in record["queries"]}
    assert matches["海边日落"] == [1]
    assert matches["Sunset"] == [2]


def test_temp_mode_leaves_no_probe_files_behind() -> None:
    completed = _run_probe()

    assert completed.returncode == 0, completed.stdout + completed.stderr
    record = _load_record(completed)
    assert record["status"] == "pass"
    assert record["temp_directory_removed"] is True
    assert record["residual_files"] == []
    assert not Path(record["database_path"]).exists()


def test_unusable_database_path_fails_without_claiming_pass(tmp_path: Path) -> None:
    unusable_path = tmp_path / "missing-parent" / "fts5_probe.db"

    completed = _run_probe("--database", str(unusable_path))

    assert completed.returncode != 0
    record = _load_record(completed)
    assert record["status"] == "fail"
    assert record["sqlite_version"] == sqlite3.sqlite_version
    assert record["failure"]["stage"] == "connect"
    assert record["failure"]["exception_type"]
    assert record["failure"]["message"]
    assert not unusable_path.exists()
