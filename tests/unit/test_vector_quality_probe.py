"""Bounded experimental input, averaging, crop and failure invariants (no GPU)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tools.evaluation import vector_quality_probe as probe
from tools.evaluation import wemm_pilot_runtime as runtime
from tools.evaluation import wemm_quality_pilot as pilot


class FakeRuntime:
    calls = []
    fail_at = None
    closed = False

    def __init__(self, *args):
        self.manifest = probe.read_json(args[-1])

    def load(self):
        return {"synthetic_not_gpu_evidence": True}

    def measured_encode(self, *, text=None, image=None):
        self.calls.append(text if text is not None else image.size)
        if len(self.calls) == self.fail_at:
            raise RuntimeError("synthetic failure")
        seed = sum(text.encode()) if text is not None else image.width + image.height
        vector = np.random.default_rng(seed).normal(size=(1, 2048)).astype(np.float32)
        return probe.normalize_prefix(vector, 2048), .1

    def resources(self):
        return {"synthetic_not_gpu_evidence": True}

    def close(self):
        FakeRuntime.closed = True


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(pilot, "ROOT", root)
    monkeypatch.setattr(runtime, "ROOT", root)
    for name in ("HF_HOME", "HF_MODULES_CACHE", "TORCH_HOME", "TEMP", "TMP", "UV_CACHE_DIR",
                 "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK"):
        monkeypatch.setenv(name, "synthetic-before")
    monkeypatch.setattr(probe, "verify_model", lambda *args: {"lock": "test"})
    monkeypatch.setattr(probe, "source_identity", lambda: {"encoding.py": "same"})
    monkeypatch.setattr(probe, "decoder_identity", lambda: {"decoder": "same"})
    monkeypatch.setattr(probe, "package_versions", lambda: {"packages": "same"})
    photo_dir = root / "photos"
    photo_dir.mkdir()
    assets, rows = [], {}
    for index in range(2):
        path = photo_dir / f"{index}.png"
        Image.new("RGB", (100 + index, 80), (index, 0, 0)).save(path)
        asset = {"asset_id": index, "path": str(path), "sha256": probe.sha256(path)}
        image, facts = probe.decode_image(path, asset["sha256"])
        image.close()
        assets.append(asset)
        rows[index] = (asset, probe.normalize_prefix(np.ones((1, 2048)), 2048),
                       {"decoded": facts, "encode_ms": 8.5})
    baseline = {"cache_identity": "test-baseline"}
    monkeypatch.setattr(probe, "baseline_inputs", lambda *args: (baseline, rows))
    input_dir = root / ".tmp/input"
    input_dir.mkdir(parents=True)
    sample = input_dir / "sample.json"
    sample.write_text(json.dumps({"schema": "public-test-v1", "assets": assets}), encoding="utf-8")
    query = input_dir / "queries.json"
    query.write_text(json.dumps([{"query_id": row[0], "text": row[1]}
                                 for row in probe.CATEGORIES]), encoding="utf-8")
    args = probe.parser().parse_args([
        "query", "--sample-file", str(sample), "--queries", str(query),
        "--model-dir", str(root / "models"), "--baseline-dir", str(root / ".tmp/baseline"),
        "--out-dir", str(root / ".tmp/probe")])
    FakeRuntime.calls, FakeRuntime.fail_at, FakeRuntime.closed = [], None, False
    return args, assets, rows


def test_fixed_queries_keep_contains_out_of_four_variant_mean():
    queries = [{"query_id": row[0], "text": row[1]} for row in probe.CATEGORIES]
    result = probe.query_variants(queries)
    assert len(result) == 40
    assert result[4] == {"query_id": "cat", "variant": "zh_contains", "text": "画面中有猫的照片"}
    queries[0]["text"] = "狗"
    with pytest.raises(probe.PilotError, match="FIXED_EIGHT"):
        probe.query_variants(queries)


@pytest.mark.parametrize("width,height", [(100, 80), (101, 81), (2, 2), (10, 1000)])
def test_crops_have_fixed_edges_cover_corners_without_labels(width, height):
    boxes = probe.crop_boxes(width, height)
    assert boxes[0] == (0, 0, width, height)
    expected = (max(1, width * 7 // 10), max(1, height * 7 // 10))
    assert all((right - left, bottom - top) == expected for left, top, right, bottom in boxes[1:])
    assert all(0 <= left < right <= width and 0 <= top < bottom <= height
               for left, top, right, bottom in boxes)
    assert boxes[-1][-2:] == (width, height)


def test_ensemble_renormalizes_each_selected_prefix_before_mean():
    vectors = np.array([[1., 0., 100.], [0., 2., .1], [2., 0., 10.], [0., 1., .3]])
    result = probe.mean_query(vectors, 2)
    np.testing.assert_allclose(result, [[2 ** -.5, 2 ** -.5]], atol=1e-6)
    wrong = probe.normalize_prefix(probe.mean_query(vectors, 3), 2)
    assert not np.allclose(result, wrong)
    with pytest.raises(probe.PilotError, match="FOUR_VARIANTS"):
        probe.mean_query(vectors[:3], 2)
    with pytest.raises(ValueError, match="zero vector"):
        probe.mean_query(np.array([[1., 0.], [-1., 0.], [0., 1.], [0., -1.]]), 2)


@pytest.mark.parametrize("mode", ["query", "highres", "crop5"])
def test_bounded_stages_preserve_metadata_and_full_native_vectors(workspace, mode):
    args, assets, rows = workspace
    args.mode = mode
    result = probe.run(args, runtime_factory=FakeRuntime)
    assert result["status"] == "COMPLETE" and result["stability"]["passed"]
    assert FakeRuntime.closed
    assert result["spec"]["max_pixels"] == (524288 if mode == "highres" else 262144)
    if mode == "query":
        assert len(result["query_vectors"]) == 40 and len(result["ensembles"]) == 8
        records = result["query_vectors"] + result["ensembles"]
        assert len(FakeRuntime.calls) == 43
    else:
        assert [row["asset_id"] for row in result["images"]] == [a["asset_id"] for a in assets]
        assert all(len(row["views"]) == (5 if mode == "crop5" else 1) for row in result["images"])
        records = [view for row in result["images"] for view in row["views"]]
        assert len(FakeRuntime.calls) == (11 if mode == "crop5" else 4)
        if mode == "crop5":
            assert records[0]["encode_ms"] == 0 and records[0]["original_encode_ms"] == 8.5
            np.testing.assert_array_equal(np.load(args.out_dir / records[0]["file"]), rows[0][1])
    for record in records:
        array = np.load(args.out_dir / record["file"], allow_pickle=False)
        assert array.shape == (1, 2048) and array.dtype == np.float32
        assert probe.sha256(args.out_dir / record["file"]) == record["sha256"]
        assert np.isfinite(array).all() and np.isclose(np.linalg.norm(array), 1)
    with pytest.raises(probe.PilotError, match="FRESH_OUTPUT"):
        probe.run(args, runtime_factory=FakeRuntime)


def test_failure_preserves_completed_work_and_closes_runtime(workspace):
    args, _, _ = workspace
    FakeRuntime.fail_at = 5
    with pytest.raises(RuntimeError, match="synthetic failure"):
        probe.run(args, runtime_factory=FakeRuntime)
    result = probe.read_json(args.out_dir / "result.json")
    assert result["status"] == "BLOCKED" and result["failure"]["phase"] == "query:1"
    assert len(result["query_vectors"]) == 1 and FakeRuntime.closed
    assert (args.out_dir / result["query_vectors"][0]["file"]).is_file()


def test_changed_input_is_refused_before_model_loading(workspace):
    args, assets, _ = workspace
    Path(assets[0]["path"]).write_bytes(b"changed")
    with pytest.raises(probe.PilotError, match="IMAGE_CONTENT_CHANGED"):
        probe.run(args, runtime_factory=FakeRuntime)
    assert not FakeRuntime.calls


def test_cached_whole_image_requires_identical_decoded_pixels(workspace):
    args, _, rows = workspace
    args.mode = "crop5"
    rows[0][2]["decoded"]["rgb_sha256"] = "wrong"
    with pytest.raises(probe.PilotError, match="BASELINE_DECODED_PIXELS"):
        probe.run(args, runtime_factory=FakeRuntime)
    result = probe.read_json(args.out_dir / "result.json")
    assert result["status"] == "BLOCKED" and not result["images"]


@pytest.mark.parametrize("change", ["model", "packages", "decoder", "source", "sample"])
def test_baseline_incompatibility_is_refused(monkeypatch, change):
    spec = {"model": "qwen", "dtype": "fp16", "native_dimension": 2048,
            "model_lock": {"lock": "same"}, "decoder": {"decoder": "same"},
            "packages": {"packages": "same"}, "source_files": {"encoding.py": "same"}}
    monkeypatch.setattr(probe, "source_identity", lambda: {"encoding.py": "same"})
    monkeypatch.setattr(probe, "decoder_identity", lambda: {"decoder": "same"})
    monkeypatch.setattr(probe, "package_versions", lambda: {"packages": "same"})
    asset = {"asset_id": 1, "sha256": "a" * 64}
    baseline = {"model_spec": spec, "inputs": [copy.copy(asset)], "images": {"0": {}}}
    monkeypatch.setattr(probe, "_read_complete_run", lambda _: (baseline, np.ones((1, 2048)), None))
    if change == "source":
        spec["source_files"]["encoding.py"] = "changed"
    elif change == "sample":
        asset["sha256"] = "b" * 64
    else:
        spec[change] = "changed"
    with pytest.raises(probe.PilotError, match="BASELINE_"):
        probe.baseline_inputs(Path("unused"), [asset], {"lock": "same"})
