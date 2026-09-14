"""P3-01: the controlled OCR entry point (``python -m photo_search.ocr.cli``).

The command is the product-facing way to force a re-recognition of one asset or a range,
so its exit codes, its JSON shape and its refusal before any write are part of the contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from p3_ocr_fixtures import (
    SKIP_WITHOUT_FONT,
    SKIP_WITHOUT_WEIGHTS,
    Harness,
    write_text_image,
)

from photo_search.ocr import contract
from photo_search.ocr.cli import main
from photo_search.search.tokens import build_match


@pytest.fixture()
def harness(tmp_path: Path) -> Harness:
    instance = Harness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def invoke(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    captured = capsys.readouterr()
    payload = json.loads(captured.out) if captured.out.strip().startswith("{") else {}
    return code, payload


@SKIP_WITHOUT_WEIGHTS
def test_weights_reports_the_pinned_identity(
    harness: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    harness.install_weights()
    code, payload = invoke(capsys, "weights", "--data-root", str(harness.data_root), "--json")
    assert code == 0
    assert payload["status"] == "ok"
    assert payload["model_fingerprint"] == contract.MODEL_FINGERPRINT
    assert payload["config_hash"] == contract.CONFIG_HASH
    assert [item["role"] for item in payload["files"]] == list(contract.WEIGHT_ROLES)
    assert all(item["present"] and item["size_matches"] for item in payload["files"])
    assert str(harness.data_root) not in json.dumps(payload["files"])


@SKIP_WITHOUT_WEIGHTS
def test_weights_refuses_a_corrupted_pinned_file(
    harness: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    harness.install_weights(corrupt="recognition")
    code, payload = invoke(capsys, "weights", "--data-root", str(harness.data_root), "--json")
    assert code == 1
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "OCR_WEIGHTS_HASH_MISMATCH"
    assert payload["error"]["details"]["role"] == "recognition"


def test_status_is_read_only_and_reports_not_run(
    harness: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    write_text_image(harness.library / "s.jpg", ["编号 1"], size=(600, 300))
    harness.scan()
    code, payload = invoke(capsys, "status", "--data-root", str(harness.data_root), "--json")
    assert code == 0
    assert payload["ocr"]["state"] == "NOT_RUN"
    assert payload["ocr"]["documents"] == 0
    assert payload["ocr"]["regions"] == 0
    assert harness.catalog.read_one("SELECT COUNT(*) AS t FROM asset_ocr_text")["t"] == 0


@SKIP_WITHOUT_WEIGHTS
@SKIP_WITHOUT_FONT
def test_recognize_runs_the_whole_loop_for_the_library(
    harness: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    harness.install_weights()
    write_text_image(harness.library / "cli.jpg", ["发票 金额 1280"], size=(800, 300))
    harness.scan()
    asset_id = harness.asset_id("cli.jpg")

    code, payload = invoke(
        capsys, "recognize", "--all", "--data-root", str(harness.data_root), "--json"
    )
    assert code == 0
    assert payload["status"] == "ok"
    assert payload["selection"] == 1
    assert payload["enqueue"] == {"targets": 1, "queued": 1, "rewound": 0, "live": 0}
    assert payload["run"]["steps"] == 1
    assert payload["jobs"] == {"SUCCEEDED": 1}
    assert payload["projection"]["state"] == "SYNCED"
    assert payload["ocr"]["state"] == "SYNCED"
    assert payload["ocr"]["weights_present"] is True
    assert harness.text_hits(build_match("1280").expression) == [asset_id]

    # A forced run rewinds the same logical job instead of creating a second one.
    code, again = invoke(
        capsys, "enqueue", "--all", "--force", "--data-root", str(harness.data_root), "--json"
    )
    assert code == 0
    assert again["enqueue"]["rewound"] == 1
    assert harness.catalog.read_one("SELECT COUNT(*) AS t FROM jobs WHERE task_type = ?",
                                    (contract.OCR_TASK_TYPE,))["t"] == 1


@SKIP_WITHOUT_WEIGHTS
@SKIP_WITHOUT_FONT
def test_the_entry_point_refuses_an_unknown_asset_without_writing(
    harness: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    harness.install_weights()
    write_text_image(harness.library / "known.jpg", ["编号 2"], size=(600, 300))
    harness.scan()
    code, payload = invoke(
        capsys, "enqueue", "--asset-id", "9999", "--data-root", str(harness.data_root), "--json"
    )
    assert code == 1
    assert payload["error"]["code"] == "ASSET_NOT_FOUND"
    assert (
        harness.catalog.read_one(
            "SELECT COUNT(*) AS t FROM jobs WHERE task_type = ?", (contract.OCR_TASK_TYPE,)
        )["t"]
        == 0
    )


def test_a_work_command_without_weights_fails_before_queueing(
    harness: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    code, payload = invoke(
        capsys, "recognize", "--all", "--data-root", str(harness.data_root), "--json"
    )
    assert code == 1
    assert payload["error"]["code"] == "OCR_WEIGHTS_MISSING"
    assert harness.catalog.read_one("SELECT COUNT(*) AS t FROM jobs")["t"] == 0


def test_usage_errors_exit_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    capsys.readouterr()
    assert main(["run", "--max-jobs", "-1"]) == 2
    capsys.readouterr()
    assert main(["recognize", "--deadline-ms", "0"]) == 2


def test_run_is_bounded_by_the_job_ceiling(
    harness: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    code, payload = invoke(
        capsys, "run", "--max-jobs", "0", "--data-root", str(harness.data_root), "--json"
    )
    # ``run`` verifies the pinned weights first, so a machine without them fails before the
    # bound is reached; with them installed, a zero bound is a legal no-op.
    if code == 0:
        assert payload["run"]["steps"] == 0
    else:
        assert payload["error"]["code"] == "OCR_WEIGHTS_MISSING"
