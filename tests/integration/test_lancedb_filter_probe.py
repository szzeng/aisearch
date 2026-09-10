"""P0-02C regression: run the LanceDB filter probe for real and check its evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROBE_SCRIPT = PROJECT_ROOT / "tools" / "benchmarks" / "lancedb_filter_probe.py"


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


def test_filters_restrict_candidates_and_unindexed_new_row_is_searchable(tmp_path: Path) -> None:
    database_path = tmp_path / "probe.lance"
    json_out = tmp_path / "probe.json"

    completed = _run_probe("--database", str(database_path), "--json-out", str(json_out))

    assert completed.returncode == 0, completed.stdout + completed.stderr
    record = _load_record(completed)
    assert record["status"] == "pass"
    assert record["failure"] is None
    assert record["lancedb_version"]
    assert record["fast_search_enabled"] is False
    assert record["maintenance_calls"] == []
    assert [field["name"] for field in record["schema"]] == [
        "id",
        "vector",
        "space_id",
        "source_id",
        "visible",
    ]
    assert json.loads(json_out.read_text(encoding="utf-8")) == record
    assert database_path.is_dir()

    queries = {entry["stage"]: entry for entry in record["queries"]}
    assert queries["unfiltered_all"]["ids"] == [11, 12, 1, 14, 2, 13, 3]
    assert queries["filtered_group_a"]["ids"] == [1, 2, 3]
    assert queries["filtered_space_and_source"]["ids"] == [1, 2]
    assert queries["filtered_visible_only"]["ids"] == [11, 12, 1, 2, 13, 3]
    assert queries["filtered_group_b"]["ids"] == [11, 12, 14, 13]
    assert queries["after_add_unfiltered_all"]["ids"] == [11, 21, 12, 1, 14, 2, 13, 3]
    assert queries["after_add_filtered_group_a"]["ids"] == [21, 1, 2, 3]
    assert queries["after_add_filtered_space_and_source"]["ids"] == [21, 1, 2]
    for entry in queries.values():
        assert entry["passed"] is True
        assert entry["ids"] == entry["expected_ids"]

    group_a = queries["filtered_group_a"]
    assert group_a["filter"] == "space_id = 10"
    assert group_a["limit"] == 3
    assert 11 not in group_a["ids"]

    exclusion = record["exclusion_evidence"]
    assert exclusion["global_nearest_id"] == 11
    assert exclusion["excluded_id_present"] is False
    assert exclusion["top_id"] == 1
    assert exclusion["returned_ids"] == [1, 2, 3]

    pre_filter = record["pre_filter_evidence"]
    assert pre_filter["filter"] == "space_id = 10"
    assert pre_filter["limit"] == 3
    assert pre_filter["max_global_rank"] == 6
    assert pre_filter["max_global_rank"] >= pre_filter["limit"]

    assert record["index_state"] == {"after_create": [], "after_add": []}
    assert record["row_count"] == 8
    assert record["added_row"]["id"] == 21
    assert record["temp_directory_removed"] is None
    assert record["residual_files"] == []


def test_temp_mode_leaves_no_probe_files_behind() -> None:
    completed = _run_probe()

    assert completed.returncode == 0, completed.stdout + completed.stderr
    record = _load_record(completed)
    assert record["status"] == "pass"
    assert record["temp_directory_removed"] is True
    assert record["residual_files"] == []
    assert not Path(record["database_path"]).exists()


def test_invalid_filter_fails_loudly_instead_of_returning_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "invalid.lance"

    completed = _run_probe(
        "--database",
        str(database_path),
        "--invalid-filter-check",
    )

    assert completed.returncode != 0
    record = _load_record(completed)
    assert record["status"] == "fail"
    assert record["lancedb_version"]
    assert record["failure"]["stage"] == "invalid_filter"
    assert record["failure"]["exception_type"]
    assert "no_such_column" in record["failure"]["message"]

    invalid = record["invalid_filter"]
    assert invalid["expression"] == "no_such_column = 1"
    assert invalid["raised"] is True
    assert invalid["exception_type"]
    assert invalid["returned_ids"] is None
