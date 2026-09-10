"""P0-02B regression: run the LanceDB probe for real and check its evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROBE_SCRIPT = PROJECT_ROOT / "tools" / "benchmarks" / "lancedb_probe.py"


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


def test_probe_searches_crud_and_reopens_from_another_process(tmp_path: Path) -> None:
    database_path = tmp_path / "probe.lance"
    json_out = tmp_path / "probe.json"

    completed = _run_probe("--database", str(database_path), "--json-out", str(json_out))

    assert completed.returncode == 0, completed.stdout + completed.stderr
    record = _load_record(completed)
    assert record["status"] == "pass"
    assert record["failure"] is None
    assert record["lancedb_version"]
    assert record["vector_dim"] == 4
    assert [field["name"] for field in record["schema"]] == ["id", "vector", "label"]
    assert record["schema"][1]["type"] == "fixed_size_list<item: float>[4]"
    assert json.loads(json_out.read_text(encoding="utf-8")) == record
    assert database_path.is_dir()

    searches = {entry["stage"]: entry for entry in record["searches"]}
    assert searches["after_seed"]["ids"] == [1, 2, 3]
    assert searches["after_add"]["ids"] == [1, 2, 3, 4]
    assert searches["after_update"]["ids"] == [3, 1, 2, 4]
    assert searches["after_delete"]["ids"] == [3, 1, 2]
    assert searches["after_delete"]["labels"] == ["gamma-moved", "alpha", "beta"]
    for entry in searches.values():
        assert entry["passed"] is True
        assert entry["ids"] == entry["expected_ids"]

    operations = record["operations"]
    assert [entry["op"] for entry in operations] == ["create_table", "add", "update", "delete"]
    assert [entry["row_count"] for entry in operations] == [3, 4, 4, 3]
    assert operations[3]["num_deleted_rows"] == 1

    reopen_checks = {entry["stage"]: entry for entry in record["reopen_checks"]}
    assert set(reopen_checks) == {"after_seed", "after_crud"}
    for entry in reopen_checks.values():
        assert entry["passed"] is True, entry
        assert entry["exit_code"] == 0
        assert entry["process"] == "separate_python_process"
    assert reopen_checks["after_seed"]["actual_ids"] == [1, 2, 3]
    assert reopen_checks["after_crud"]["actual_ids"] == [3, 1, 2]

    assert record["labels_after_update"]["3"] == "gamma-moved"
    assert record["labels_after_update"]["4"] == "delta"
    assert record["row_ids"] == [1, 2, 3]
    assert record["row_count"] == 3
    assert record["stored_labels"]["3"] == "gamma-moved"
    assert "4" not in record["stored_labels"]


def test_temp_mode_leaves_no_probe_files_behind() -> None:
    completed = _run_probe()

    assert completed.returncode == 0, completed.stdout + completed.stderr
    record = _load_record(completed)
    assert record["status"] == "pass"
    assert record["temp_directory_removed"] is True
    assert record["residual_files"] == []
    assert not Path(record["database_path"]).exists()


def test_unusable_database_path_fails_without_claiming_pass(tmp_path: Path) -> None:
    blocked_file = tmp_path / "blocked.txt"
    blocked_file.write_text("not a directory", encoding="ascii")

    completed = _run_probe("--database", str(blocked_file / "probe.lance"))

    assert completed.returncode != 0
    record = _load_record(completed)
    assert record["status"] == "fail"
    assert record["python_version"]
    assert record["lancedb_version"]
    assert record["failure"]["stage"] == "connect"
    assert record["failure"]["exception_type"]
    assert record["failure"]["message"]
