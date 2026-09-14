"""P3-02: the controlled face entry point (``python -m photo_search.face.cli``).

The command is the product-facing way to force a re-detection of one asset or a range, so its
exit codes, its JSON shape and its refusal *before* any write are part of the contract. The
API capabilities block is asserted here too, because ``face_summary`` is what both readers
serve.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import AsyncClient
from p3_face_fixtures import SKIP_WITHOUT_FACE_WEIGHTS, Harness

from photo_search.face import contract
from photo_search.face.cli import main
from photo_search.face.service import face_targets


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


@SKIP_WITHOUT_FACE_WEIGHTS
def test_weights_reports_the_pinned_identity(
    harness: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    harness.install_weights()
    code, payload = invoke(capsys, "weights", "--data-root", str(harness.data_root), "--json")
    assert code == 0
    assert payload["status"] == "ok"
    assert payload["model_fingerprint"] == contract.MODEL_FINGERPRINT
    assert payload["config_hash"] == contract.CONFIG_HASH
    assert payload["space_id"] == contract.FACE_SPACE_ID
    assert [item["role"] for item in payload["files"]] == list(contract.WEIGHT_ROLES)
    assert all(item["present"] and item["size_matches"] for item in payload["files"])
    assert str(harness.data_root) not in json.dumps(payload["files"])


@SKIP_WITHOUT_FACE_WEIGHTS
def test_weights_refuses_a_corrupted_pinned_file(
    harness: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    harness.install_weights(corrupt="recognition")
    code, payload = invoke(capsys, "weights", "--data-root", str(harness.data_root), "--json")
    assert code == 1
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "FACE_WEIGHTS_HASH_MISMATCH"
    assert payload["error"]["details"]["role"] == "recognition"


def test_status_is_read_only_and_reports_not_run(
    harness: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    harness.scan()
    code, payload = invoke(capsys, "status", "--data-root", str(harness.data_root), "--json")
    assert code == 0
    assert payload["status"] == "ok"
    faces = payload["faces"]
    assert faces["state"] == "NOT_RUN"
    assert faces["processor_id"] == contract.FACE_PROCESSOR_ID
    assert faces["space_id"] == contract.FACE_SPACE_ID
    assert faces["library_assets"] == 6
    assert faces["analyzed_assets"] == 0
    assert faces["faces"] == 0
    assert faces["weights_present"] is False
    assert faces["thresholds"]["merge"] > faces["thresholds"]["suggest"]
    # A read-only projection: no personal data and no local path.
    serialised = json.dumps(payload, ensure_ascii=False)
    assert str(harness.data_root) not in serialised
    assert str(harness.library) not in serialised
    assert "bbox" not in serialised and "embedding" not in serialised


@SKIP_WITHOUT_FACE_WEIGHTS
def test_detect_runs_the_whole_loop_for_the_library(
    harness: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    harness.install_weights()
    harness.scan()
    code, payload = invoke(
        capsys, "detect", "--data-root", str(harness.data_root), "--device", "cpu", "--json"
    )
    assert code == 0
    assert payload["status"] == "ok"
    assert payload["selection"] == 6
    assert payload["enqueue"]["queued"] == 6
    assert payload["run"]["steps"] == 6
    assert payload["run"]["qualified_devices"] == ["cpu"]
    assert payload["index"]["index"]["state"] == "READY"
    # One group for the astronaut (the bright variant merges into it), six for the group photo
    # and one for the portrait: eight unnamed groups, none of them named.
    assert payload["association"]["new_groups"] == 8
    assert payload["association"]["merged"] == 2
    assert payload["association"]["suggested"] == 2
    assert payload["faces"]["state"] == "SYNCED"
    assert payload["faces"]["analyzed_assets"] == 6
    assert payload["faces"]["persons"] == 8
    # The run reports the CPU degradation instead of hiding it.
    assert payload["faces"]["degraded_jobs"] == 6


@SKIP_WITHOUT_FACE_WEIGHTS
def test_the_entry_point_accepts_one_asset_and_force(
    harness: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    harness.install_weights()
    harness.scan()
    asset_id = harness.asset_id("astronaut.png")
    code, payload = invoke(
        capsys,
        "detect",
        "--data-root",
        str(harness.data_root),
        "--asset-id",
        str(asset_id),
        "--device",
        "cpu",
        "--json",
    )
    assert code == 0
    assert payload["selection"] == 1
    assert len(harness.faces(asset_id)) == 1
    code, again = invoke(
        capsys,
        "detect",
        "--data-root",
        str(harness.data_root),
        "--asset-id",
        str(asset_id),
        "--device",
        "cpu",
        "--force",
        "--json",
    )
    assert code == 0 and again["enqueue"]["rewound"] == 1
    assert len(harness.faces(asset_id)) == 1


@SKIP_WITHOUT_FACE_WEIGHTS
def test_the_entry_point_refuses_an_unknown_asset_without_writing(
    harness: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    harness.install_weights()
    harness.scan()
    code, payload = invoke(
        capsys,
        "enqueue",
        "--data-root",
        str(harness.data_root),
        "--asset-id",
        "99999",
        "--json",
    )
    assert code == 1
    assert payload["error"]["code"] == "ASSET_NOT_FOUND"
    assert harness.jobs().get("QUEUED", 0) == 0


@SKIP_WITHOUT_FACE_WEIGHTS
def test_a_work_command_without_weights_fails_before_queueing(
    harness: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    harness.scan()
    code, payload = invoke(
        capsys,
        "enqueue",
        "--data-root",
        str(harness.data_root),
        "--all",
        "--json",
    )
    assert code == 1
    assert payload["error"]["code"] == "FACE_WEIGHTS_MISSING"
    assert harness.jobs() == {}
    assert len(face_targets(harness.catalog)) == 6


def test_usage_errors_exit_two(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert main([]) == 2
    capsys.readouterr()
    assert main(["run", "--data-root", str(tmp_path), "--max-jobs", "-1", "--json"]) == 2
    capsys.readouterr()
    assert (
        main(
            [
                "run",
                "--data-root",
                str(tmp_path),
                "--deadline-ms",
                "0",
                "--json",
            ]
        )
        == 2
    )
    capsys.readouterr()
    with pytest.raises(SystemExit) as error:
        main(["run", "--device", "tpu"])
    assert error.value.code == 2


def test_the_api_capabilities_block_serves_the_face_facts(api_env) -> None:
    """``GET /api/v1/capabilities`` is the read-only door to the same projection."""

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        response = await client.get("/api/v1/capabilities")
        assert response.status_code == 200, response.text
        return response.json()

    body = api_env.scenario(scenario)
    faces = body["faces"]
    assert faces is not None
    assert faces["state"] == "NOT_RUN"
    assert faces["processor_id"] == contract.FACE_PROCESSOR_ID
    assert faces["dimension"] == contract.FACE_DIMENSION
    assert faces["thresholds"]["merge"] == contract.MERGE_THRESHOLD
    assert faces["weights_present"] is False
    serialised = json.dumps(body["faces"], ensure_ascii=False)
    assert str(api_env.data_root) not in serialised
    assert "bbox" not in serialised and "landmark" not in serialised
