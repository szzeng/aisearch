"""Detector measurement invariants, with no model or accelerator loading."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tools.evaluation import budget_object_probe as probe
from tools.evaluation import wemm_pilot_runtime as runtime
from tools.evaluation import wemm_quality_pilot as pilot


def prediction():
    return probe.pack_predictions([.8, .6], [.6, .8], [1, 0],
                                  [[-1, 2, 8, 11], [0, 0, 3, 4]],
                                  {0: "cat", 1: "dog"}, (10, 10))


def test_scores_are_per_class_and_boxes_are_clipped_without_discarding_raw():
    result = prediction()
    assert result["class_scores"] == {"cat": .8, "dog": .6}
    assert [item["label"] for item in result["detections"]] == ["cat", "dog"]
    assert result["detections"][1]["raw_xyxy"] == [-1, 2, 8, 11]
    assert result["detections"][1]["xyxy"] == [0, 2, 8, 10]


@pytest.mark.parametrize("field,value", [
    (0, [float("nan"), .5]), (0, [1.1, .5]), (0, [.5]),
    (1, [float("inf")]), (2, [2]), (2, [.5]), (3, [[5, 5, 4, 6]]),
    (3, [[0, 0, float("nan"), 5]]), (3, [[0, 0, 2]]),
])
def test_malformed_predictions_fail(field, value):
    args = [[.5, .6], [.5], [0], [[0, 0, 2, 2]], {0: "cat", 1: "dog"}, (10, 10)]
    args[field] = value
    with pytest.raises(probe.PilotError):
        probe.pack_predictions(*args)


def test_detection_budget_is_not_silently_truncated():
    with pytest.raises(probe.PilotError, match="SHAPE"):
        probe.pack_predictions([.5], [.5] * 301, [0] * 301, [[0, 0, 2, 2]] * 301,
                               {0: "cat"}, (10, 10))


class FakeRuntime:
    closed = False
    failed = False

    def __init__(self, *args):
        self.count = 0

    def resources(self):
        return {"fake": True, "device": "test-only"}

    def encode(self, image):
        self.count += 1
        if self.failed and self.count == 2:
            raise RuntimeError("injected second image failure")
        return {**prediction(), "detect_ms": 3.2}

    def close(self):
        type(self).closed = True


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(pilot, "ROOT", root)
    monkeypatch.setattr(runtime, "ROOT", root)
    monkeypatch.setattr(probe, "configure_offline", lambda *args: None)
    monkeypatch.setattr(probe, "verify_model", lambda *args: {"model": "fake"})
    monkeypatch.setattr(probe, "decoder_identity", lambda: {"decoder": "fake"})
    monkeypatch.setattr(probe, "package_versions", lambda: {"package": "fake"})
    photos = root / ".tmp/photos"
    photos.mkdir(parents=True)
    assets = []
    for index in range(2):
        path = photos / f"{index}.png"
        Image.new("RGB", (10, 10), (index, 0, 0)).save(path)
        assets.append({"asset_id": f"id-{index}", "path": str(path),
                       "sha256": probe.sha256(path)})
    sample = root / ".tmp/sample.json"
    sample.write_text(json.dumps({"schema": "synthetic-budget-v1", "assets": assets,
                                  "source_identity": {"member": "instances_train2014.json"}}),
                      encoding="utf-8")
    args = probe.parser().parse_args(["--sample-file", str(sample), "--model-dir",
                                     str(root / "models"), "--out-dir", str(root / ".tmp/run"),
                                     "--device", "cpu"])
    FakeRuntime.closed, FakeRuntime.failed = False, False
    return args, assets


def test_complete_run_records_fake_device_sample_identity_and_training_overlap(workspace):
    args, assets = workspace
    result = probe.run(args, runtime_factory=FakeRuntime)
    assert result["status"] == "COMPLETE"
    assert result["resources"] == {"fake": True, "device": "test-only"}
    assert result["sample_sha256"] == probe.sha256(args.sample_file)
    assert [row["asset_id"] for row in result["images"]] == [row["asset_id"] for row in assets]
    assert "diagnostic only" in result["training_overlap_warning"]
    assert FakeRuntime.closed
    assert probe.read_json(args.out_dir / "result.json") == result
    with pytest.raises(probe.PilotError, match="FRESH"):
        probe.run(args, runtime_factory=FakeRuntime)


def test_failure_retains_completed_images_and_actual_failed_phase(workspace):
    args, _ = workspace
    FakeRuntime.failed = True
    result = probe.run(args, runtime_factory=FakeRuntime)
    assert result["status"] == "BLOCKED"
    assert len(result["images"]) == 1
    assert result["failure"]["phase"] == "image:1"
    assert result["failure"]["type"] == "RuntimeError"
    assert FakeRuntime.closed


@pytest.mark.parametrize("mutation,reason", [
    ("private", "PUBLIC_OR_SYNTHETIC"), ("duplicate", "DUPLICATE_SAMPLE_CONTENT"),
    ("duplicate_id", "duplicate asset ID"), ("relative", "ABSOLUTE"), ("size", "BUDGET"),
])
def test_sample_rejections(workspace, mutation, reason):
    args, _ = workspace
    sample = copy.deepcopy(probe.read_json(args.sample_file))
    if mutation == "private":
        sample["schema"] = "wemm-pilot-sample-v1"
    elif mutation == "duplicate":
        sample["assets"][1]["sha256"] = sample["assets"][0]["sha256"]
    elif mutation == "duplicate_id":
        sample["assets"][1]["asset_id"] = sample["assets"][0]["asset_id"]
    elif mutation == "relative":
        sample["assets"][0]["path"] = "relative.jpg"
    else:
        args.max_assets = 1
    args.sample_file.write_text(json.dumps(sample), encoding="utf-8")
    with pytest.raises(ValueError, match=reason):
        probe.run(args, runtime_factory=FakeRuntime)


def test_image_hash_change_refused_before_model_load(workspace):
    args, assets = workspace
    Path(assets[0]["path"]).write_bytes(b"changed")
    with pytest.raises(probe.PilotError, match="IMAGE_CONTENT_CHANGED"):
        probe.run(args, runtime_factory=FakeRuntime)


def test_cpu_half_refused(workspace):
    args, _ = workspace
    args.dtype = "fp16"
    with pytest.raises(probe.PilotError, match="CPU_REQUIRES"):
        probe.run(args, runtime_factory=FakeRuntime)


def test_model_lock_detects_same_size_corruption(tmp_path, monkeypatch):
    directory = tmp_path / "model"
    directory.mkdir()
    path = directory / "config.json"
    path.write_bytes(b"abc")
    lock = {"files": {path.name: {"bytes": 3, "sha256": probe.sha256(path)}}}
    monkeypatch.setattr(probe, "read_json", lambda _: lock)
    assert probe.verify_model(directory) == lock
    path.write_bytes(b"xyz")
    with pytest.raises(probe.PilotError, match="HASH_MISMATCH"):
        probe.verify_model(directory)


def test_empty_outputs_have_zero_detections_and_keep_class_evidence():
    result = probe.pack_predictions([.1], [], np.array([], dtype=np.int64),
                                    np.empty((0, 4)), {0: "cat"}, (5, 5))
    assert result == {"class_scores": {"cat": .1}, "detections": []}
