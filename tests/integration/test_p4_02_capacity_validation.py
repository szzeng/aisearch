"""P4-02B acceptance: the capacity tool's real path, its JSON contract, and the D faults.

The formal 10 000 / 100 000 / 300 000 run is the report's evidence; this file keeps the same
tool honest at a size a test suite can afford. It drives the tool's real CLI entry point over
a throw-away synthetic library inside a ``中文 空格`` directory, and then asserts the facts
the task card makes contractual:

* the scan, the derived search layer and the semantic stage really complete (counts, FTS rows,
  outbox confirmations, ``semantic_ready``, generation state, ``asset_id`` scalar index);
* the query path is measured (service and real HTTP) against the section 12.2 target;
* the five D-level faults keep their invariants (interrupted drain, ``after_lance`` window,
  content change, offline location, confirmation window of a new vector).

Nothing here reads a real photo library: every fixture is synthetic, seeded and rebuilt from
scratch inside pytest's own temporary directory.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = PROJECT_ROOT / "tools" / "diagnostics" / "capacity_validation.py"

#: Small enough for a test suite, large enough that the batch paths really batch.
SCALE = 150
QUERIES = 5


def load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("p4_02b_capacity_tool", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = load_tool()


@pytest.fixture(scope="module")
def capacity_report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """One complete tool run over a Chinese, space-carrying work directory."""
    work = tmp_path_factory.mktemp("p402b") / "P4-02B 容量 验证"
    output = work / "capacity.json"
    code = tool.main(
        [
            "--scales",
            str(SCALE),
            "--injection-scale",
            str(SCALE),
            "--work-dir",
            str(work),
            "--output",
            str(output),
            "--queries",
            str(QUERIES),
            "--warm-seconds",
            "0",
        ]
    )
    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "ok"
    return report


@pytest.fixture(scope="module")
def measurement(capacity_report: dict[str, Any]) -> dict[str, Any]:
    assert len(capacity_report["measurements"]) == 1
    block = capacity_report["measurements"][0]
    assert block.get("status") == "ok", block.get("error")
    return block


def test_the_fixture_is_seeded_and_reproducible(tmp_path: Path) -> None:
    def digests(root: Path) -> list[str]:
        return sorted(
            hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob("*.jpg")
        )

    first = tool.build_library(
        tmp_path / "first", files=40, width=64, height=48, seed=11, buckets=4, variants=4
    )
    second = tool.build_library(
        tmp_path / "second", files=40, width=64, height=48, seed=11, buckets=4, variants=4
    )
    assert digests(tmp_path / "first") == digests(tmp_path / "second")
    assert first["files"] == second["files"] == 40
    assert first["sample_distinct_sha256"] == first["sample_size"] == 32
    # The unique trailer must not break the real decode path.
    assert tool.verify_fixture_decodes(tmp_path / "first")["decoded"] == 3


def test_scan_and_projection_reach_a_searchable_catalog(measurement: dict[str, Any]) -> None:
    scan = measurement["scan"]
    assert scan["state"] == "SUCCEEDED"
    assert scan["enumerated"] == SCALE
    assert scan["counts"]["assets_total"] == SCALE
    assert scan["counts"]["locations_present"] == SCALE
    assert scan["stability_waits"] == 0
    catalog = measurement["catalog"]
    assert catalog["catch_up"]["state"] == "SYNCED"
    assert catalog["catch_up"]["remaining"] == 0
    assert catalog["projection"]["documents"] == SCALE
    assert catalog["projection"]["queued_assets"] == 0
    assert catalog["fts_rows"] == SCALE


def test_semantic_stage_confirms_every_event_and_indexes_the_generation(
    measurement: dict[str, Any],
) -> None:
    semantic = measurement["semantic"]
    assert semantic["execute"]["steps"] >= SCALE
    assert semantic["execute"]["waves"] == len(semantic["waves"]) >= 1
    assert semantic["drain_total"]["applied"] == SCALE
    assert sum(semantic["outbox_before_drain"].values()) == SCALE
    assert semantic["outbox_before_drain"]["PENDING"] == SCALE
    drain = semantic["drain"]
    assert drain["returncode"] == 0
    assert drain["payload"]["status"] == "ok"
    assert drain["payload"]["index"]["after"]["pending"] == 0
    assert drain["payload"]["index"]["after"]["semantic_ready"] == SCALE
    scalar = semantic["scalar_index"][tool.GENERATION_ID]
    assert scalar["index"] == "asset_id_idx"
    assert scalar["state"] in {"created", "present"}
    index = semantic["index"]
    assert index["pending"] == 0
    assert index["semantic_ready"] == SCALE
    assert index["entries"]["CONFIRMED"] == SCALE
    assert index["generations"]["READY"] == 1
    assert index["active_generations"][0]["generation_id"] == tool.GENERATION_ID


def test_query_latency_is_measured_against_the_section_12_2_target(
    measurement: dict[str, Any],
) -> None:
    query = measurement["query"]
    assert query["samples"] == QUERIES
    assert query["service"]["count"] == QUERIES
    assert query["service"]["meets_target"] is True
    assert query["cold_hits"] > 0
    http = query["http"]
    assert http["http_ready"] is True
    assert http["health"] == 200
    assert http["session_exchange"] == 200
    assert http["warm"]["count"] == QUERIES
    assert http["warm"]["meets_target"] is True


def test_background_build_keeps_foreground_latency_within_the_scheduling_target(
    measurement: dict[str, Any],
) -> None:
    contention = measurement["semantic"]["contention"]
    samples = contention["samples"]
    assert samples["count"] >= 1
    assert samples["target_p95_ms"] == 1_500
    assert samples["meets_target"] is True, samples
    assert contention["overlap_ms"] > 0


def test_a_non_ascii_work_directory_and_every_cli_payload_round_trip(
    capacity_report: dict[str, Any], measurement: dict[str, Any]
) -> None:
    semantic = measurement["semantic"]
    for block in (semantic["drain"], semantic["scalar_index_pass"], semantic["status_cli"]):
        assert block["returncode"] == 0
        assert block["payload"].get("status") == "ok", block["stdout_tail"]
    assert "容量" in capacity_report["work_dir"]
    # The tool's own JSON must be ASCII-encodable on a cp936 console.
    json.dumps(capacity_report, ensure_ascii=True)


def test_interrupted_drain_resumes_without_duplicates_or_lost_rows(
    capacity_report: dict[str, Any],
) -> None:
    block = capacity_report["injections"]["interrupted_drain"]
    assert block["interrupted_while_running"] is True
    assert block["confirmed_mid_flight"] > 0
    assert block["pending_before"] == block["assets"]
    # No outbox row is lost by the kill: the mid-flight state still accounts for every event.
    assert sum(block["mid_state"].values()) == block["assets"]
    assert block["resumed"]["applied"] + block["confirmed_mid_flight"] == block["assets"]
    assert block["no_duplicate_rows"] is True
    assert block["engine_rows"] == block["assets"]
    assert block["index_entries"] == block["assets"]
    assert block["all_confirmed"] is True


def test_the_after_lance_crash_window_replays_exactly_once(
    capacity_report: dict[str, Any],
) -> None:
    block = capacity_report["injections"]["after_lance"]
    assert block["injected_crash"] is True
    assert block["outbox_after_crash"]["IN_FLIGHT"] == 1
    assert block["resumed"]["applied"] == 1
    assert block["no_duplicate_row"] is True
    assert sum(block["outbox_after_resume"].values()) == sum(
        block["outbox_after_crash"].values()
    )
    assert block["outbox_after_resume"]["CONFIRMED"] == sum(block["outbox_after_crash"].values())


def test_a_content_change_supersedes_the_old_vector(capacity_report: dict[str, Any]) -> None:
    block = capacity_report["injections"]["content_change"]
    assert block["distance_before_to_old"] == 0.0
    assert block["engine_vector_distance_to_new"] == 0.0
    assert block["engine_vector_distance_to_old"] > 0.5
    assert block["new_vector_rank_1"] is True
    assert block["old_vector_still_first"] is False
    assert block["entry_artifact"] == f"p4-02b-change-new-{block['asset_id']}"
    assert block["entry_confirmation_revision"] == block["filter_revision"]


def test_an_offline_location_keeps_no_ghost_hit(capacity_report: dict[str, Any]) -> None:
    block = capacity_report["injections"]["offline"]
    assert block["hits_before_offline_present"][0] == block["asset_id"]
    assert block["asset_id"] not in block["hits_after_offline_present"]
    assert block["asset_id"] not in block["hits_after_maintenance_present"]
    assert block["no_ghost_hit"] is True
    # The derived row stays in the generation (it is only re-derived on a new index event);
    # what must never happen is a hit through the authoritative search path.
    assert block["engine_row_still_present"] is True


def test_a_confirmed_new_vector_is_searchable_without_ann_maintenance(
    capacity_report: dict[str, Any],
) -> None:
    block = capacity_report["injections"]["pending_vector"]
    assert block["pending_events"] == 1
    assert block["hit_before_confirmation"] is False
    assert block["fused_hits_before_confirmation"] > 0
    assert block["confirmed_events"] == 1
    assert block["hit_after_confirmation"] is True
    assert block["hits_after_confirmation"][0] == block["asset_id"]
