from __future__ import annotations

import hashlib
import importlib
import json
import sys

import numpy as np
import pytest

from tools.evaluation import album_tagging_probe as probe


def test_official_threshold_is_strict_and_label_order_preserved():
    count = probe.CLASS_COUNT
    scores, thresholds = np.zeros(count, np.float32), np.full(count, .5, np.float32)
    scores[[0, 3, 21]] = [.5, .5001, .9]
    en, cn = [f"class{i}" for i in range(count)], [f"类别{i}" for i in range(count)]
    assert probe.selected_tags(scores, thresholds, en, cn) == {
        "indices": [3, 21], "english": "class3 | class21", "chinese": "类别3 | 类别21"}


@pytest.mark.parametrize("mutation", ["nan", "over", "under", "shape", "threshold", "labels"])
def test_scores_reject_invalid_output(mutation):
    n = probe.CLASS_COUNT
    scores, thresholds, en, cn = np.zeros(n), np.full(n, .5), ["en"] * n, ["中"] * n
    if mutation == "nan":
        scores[0] = np.nan
    elif mutation == "over":
        scores[0] = 1.1
    elif mutation == "under":
        scores[0] = -.1
    elif mutation == "shape":
        scores = scores[:-1]
    elif mutation == "threshold":
        thresholds[0] = np.inf
    else:
        en = en[:-1]
    with pytest.raises(ValueError):
        probe.selected_tags(scores, thresholds, en, cn)


def test_locked_loader_runs_verified_source_and_rejects_changes(tmp_path):
    directory = tmp_path / "ram"
    directory.mkdir()
    source = directory / "__init__.py"
    source.write_text("VALUE = 'verified'\n", encoding="utf-8")
    loader = probe.LockedSourceLoader(tmp_path, {"ram/__init__.py": {
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}})
    assert "ram" not in sys.modules
    sys.meta_path.insert(0, loader)
    try:
        module = importlib.import_module("ram")
        assert module.VALUE == "verified"
        source.write_text("VALUE = 'unverified'\n", encoding="utf-8")
        with pytest.raises(ImportError, match="changed"):
            importlib.reload(module)
        with pytest.raises(ImportError, match="unlocked"):
            loader.find_spec("ram.unverified")
    finally:
        sys.meta_path.remove(loader)
        sys.modules.pop("ram", None)
    assert not (directory / "__pycache__").exists()


def test_public_sample_hash_membership_and_limit_are_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    image = tmp_path / "public.jpg"
    image.write_bytes(b"public-fixture")
    row = {"asset_id": "test", "path": str(image), "sha256": probe.sha256(image)}
    sample = tmp_path / "sample.json"
    sample.write_text(json.dumps({"schema": "broad-album-data-v1", "assets": [row]}))
    assert probe.load_sample(sample, 1)[1] == [row]
    for limit in (0, 2, True):
        with pytest.raises(ValueError, match="limit"):
            probe.load_sample(sample, limit)
    image.write_bytes(b"changed")
    with pytest.raises(ValueError, match="bytes changed"):
        probe.load_sample(sample, None)


def test_sample_refuses_duplicates_and_unknown_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    sample = tmp_path / "sample.json"
    for document in ({"schema": "private-library", "assets": []},
                     {"schema": "broad-album-data-v1", "assets": [{"asset_id": 1}] * 2}):
        sample.write_text(json.dumps(document))
        with pytest.raises(ValueError):
            probe.load_sample(sample, None)


def test_paths_refuse_parent_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="workspace"):
        probe.local_path(tmp_path / ".." / "outside")


def test_pinned_file_requires_size_and_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    path = tmp_path / "config.json"
    path.write_bytes(b"{}")
    record = {"bytes": 2, "sha256": probe.sha256(path)}
    probe.verify_file(path, record)
    path.write_bytes(b"[]")
    with pytest.raises(ValueError, match="pinned file"):
        probe.verify_file(path, record)


def test_only_exact_official_checkpoint_naming_quirk_is_allowed():
    buffers = {"visual.attn.relative_position_index"}
    probe.validate_checkpoint_keys([*buffers, "reweight_scale"], ["logit_scale"], buffers)
    for missing, unexpected in ((["fc.weight", "reweight_scale"], ["logit_scale"]),
                                (["reweight_scale"], ["logit_scale", "unknown"]),
                                ([], []), (["reweight_scale"], [])):
        with pytest.raises(ValueError, match="checkpoint incomplete"):
            probe.validate_checkpoint_keys(missing, unexpected, buffers)


@pytest.mark.parametrize("changed", ["sample", "runtime", "decoder", "lock"])
def test_run_refuses_identity_changes_even_when_images_stay_valid(tmp_path, monkeypatch, changed):
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    files = {name: tmp_path / name for name in ("sample", "runtime", "lock")}
    for name, path in files.items():
        path.write_text(name)
    monkeypatch.setattr(probe, "LOCK", files["lock"])
    monkeypatch.setattr(probe, "decoder_identity", lambda: {"version": 1})
    result = {"sample_sha256": probe.sha256(files["sample"]),
              "lock_sha256": probe.sha256(files["lock"]), "decoder": {"version": 1},
              "source_files_sha256": {"runtime": probe.sha256(files["runtime"])}}
    probe.verify_run_identity(result, files["sample"])
    if changed == "decoder":
        monkeypatch.setattr(probe, "decoder_identity", lambda: {"version": 2})
    else:
        files[changed].write_text("changed")
    with pytest.raises(ValueError, match="changed during inference"):
        probe.verify_run_identity(result, files["sample"])
