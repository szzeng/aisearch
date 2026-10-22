"""Measurement guards: malformed features and mismatched fixed model identity."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np
import pytest

from tools.evaluation import budget_embedding_probe as probe
from tools.evaluation.wemm_quality_pilot import load_inputs


def test_album_budget_is_explicit_and_preserves_all_members(tmp_path):
    sample = tmp_path / "sample.json"
    queries = tmp_path / "queries.json"
    assets = [{"asset_id": i, "sha256": f"{i:064x}"} for i in range(1047)]
    items = [{"query_id": str(i), "text": "猫"} for i in range(200)]
    sample.write_text(json.dumps({"assets": assets}), encoding="utf-8")
    queries.write_text(json.dumps(items), encoding="utf-8")
    with pytest.raises(ValueError):
        load_inputs(sample, queries, 2000)
    data, loaded = load_inputs(sample, queries, 2000, album_budget=True)
    assert data["assets"] == assets and loaded == items
    queries.write_text(json.dumps(items + [{"query_id": "extra", "text": "狗"}]), encoding="utf-8")
    with pytest.raises(ValueError):
        load_inputs(sample, queries, 2000, album_budget=True)


@pytest.mark.parametrize("value", [np.zeros((1, 3)), np.array([[1, np.nan, 2]]),
                                  np.array([[1, np.inf, 2]]), np.ones((2, 3)), np.ones((1, 2))])
def test_invalid_features_fail(value):
    with pytest.raises(ValueError):
        probe.validated_feature(value, 3)


def test_native_feature_normalized_in_float32():
    result = probe.validated_feature(np.array([[3, 4, 0]], dtype=np.float16), 3)
    assert result.dtype == np.float32
    np.testing.assert_allclose(result, [0.6, 0.8, 0])


def test_model_changed_bytes_refused(tmp_path, monkeypatch):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(probe, "read_json", lambda _: {
        "model_id": probe.SIGLIP["siglip-base"][0],
        "revision": probe.SIGLIP["siglip-base"][1],
        "files": {"config.json": "0" * 64},
    })
    with pytest.raises(ValueError, match="model content differs"):
        probe.verify_siglip("siglip-base", tmp_path)


def test_wrong_model_revision_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "read_json", lambda _: {
        "model_id": probe.SIGLIP["siglip-base"][0], "revision": "main", "files": {},
    })
    with pytest.raises(ValueError, match="lock identity"):
        probe.verify_siglip("siglip-base", tmp_path)


def test_wrong_preprocessing_refused(tmp_path, monkeypatch):
    config = {"model_type": "siglip2", "text_config": {"hidden_size": 768},
              "vision_config": {"hidden_size": 768}}
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (tmp_path / "preprocessor_config.json").write_text(
        json.dumps({"max_num_patches": 512, "patch_size": 16}), encoding="utf-8")
    original = probe.read_json
    lock = {"model_id": probe.SIGLIP["siglip-base"][0],
            "revision": probe.SIGLIP["siglip-base"][1],
            "files": {p.name: probe.sha256(p) for p in tmp_path.iterdir()}}
    monkeypatch.setattr(probe, "read_json", lambda p: lock if p.name == "siglip_model_lock.json"
                        else original(p))
    with pytest.raises(ValueError, match="preprocessor"):
        probe.verify_siglip("siglip-base", tmp_path)


def test_unpinned_runtime_configuration_refused(tmp_path, monkeypatch):
    (tmp_path / "processor_config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(probe, "read_json", lambda _: {
        "model_id": probe.SIGLIP["siglip-base"][0],
        "revision": probe.SIGLIP["siglip-base"][1], "files": {},
    })
    with pytest.raises(ValueError, match="unverified runtime"):
        probe.verify_siglip("siglip-base", tmp_path)


@pytest.mark.parametrize("encode_fails", [False, True])
@pytest.mark.parametrize("model", ["qwen", *probe.FGCLIP_MODELS])
def test_gpu_ownership_is_retained_until_model_released(tmp_path, monkeypatch, encode_fails, model):
    events = []
    constructed = {}
    source = tmp_path / "sample.json"
    source.write_text("{}", encoding="utf-8")
    assets = [{"asset_id": "a", "path": str(tmp_path / "a.jpg"), "sha256": "1" * 64}]
    monkeypatch.setattr(probe, "load_inputs", lambda *_: (
        {"schema": "synthetic-test", "assets": assets}, [{"query_id": "cat", "text": "猫"}]))
    monkeypatch.setattr(probe, "checked_output", lambda path, *_: path)
    monkeypatch.setattr(probe, "configure_offline", lambda _: None)
    monkeypatch.setattr(probe, "verify_model", lambda *_: {"model": "fake"})
    monkeypatch.setattr(probe, "verify_fgclip", lambda *_: {"model": "fake"})
    monkeypatch.setattr(probe, "read_json", lambda _: {"provider": {}, "embedding": {}})
    monkeypatch.setattr(probe, "atomic_json", lambda p, v: p.write_text(json.dumps(v), encoding="utf-8"))
    monkeypatch.setattr(probe, "package_versions", lambda: {})
    monkeypatch.setattr(probe, "decoder_identity", lambda: {})
    monkeypatch.setattr(probe, "source_identity", lambda: {})
    monkeypatch.setattr(probe, "decode_image", lambda *_: (
        SimpleNamespace(close=lambda: None, size=(800, 600)), {"width": 800, "height": 600}))

    def save(path, value):
        np.save(path, value)
        return probe.sha256(path)

    monkeypatch.setattr(probe, "save_vector", save)

    @contextmanager
    def lock(_):
        events.append("lock")
        try:
            yield
        finally:
            events.append("unlock")

    class Runtime:
        def __init__(self, *_, **kwargs):
            constructed.update(kwargs)

        def patch_budget(self, image):
            assert image.size == (800, 600)
            return 1024 if constructed["adaptive"] else 256

        def load(self):
            return {}

        def sync(self):
            pass

        def encode(self, **_):
            if encode_fails:
                raise RuntimeError("encode failure")
            return np.ones((1, 2048 if model == "qwen" else 768), dtype=np.float32)

        def resources(self):
            return {}

        def close(self):
            events.append("model_released")

    monkeypatch.setattr(probe, "gpu_lock", lock)
    monkeypatch.setattr(probe, "ModelRuntime", Runtime)
    monkeypatch.setattr(probe, "FgclipRuntime", Runtime)
    args = SimpleNamespace(dtype="fp16", model=model, sample=source, queries=source,
                           limit=None, model_dir=tmp_path / "model", out_dir=tmp_path / "run")
    if encode_fails:
        with pytest.raises(RuntimeError, match="encode failure"):
            probe.run(args)
    else:
        probe.run(args)
    assert events == ["lock", "model_released", "unlock"]
    record = json.loads((args.out_dir / "result.json").read_text(encoding="utf-8"))
    assert record["status"] == ("FAILED" if encode_fails else "COMPLETE")
    if model in probe.FGCLIP_MODELS:
        adaptive = model == "fgclip-base-adaptive"
        assert constructed["adaptive"] == adaptive
        settings = record["settings"]["fgclip"]
        assert settings["patches"] == ("adaptive" if adaptive else 256)
        assert settings["patch_budget_policy"] == (
            "official_determine_max_value" if adaptive else "fixed_256")
        assert settings["text_attention_mask"] is False and settings["max_length"] == 64
        assert "siglip" not in record["settings"]
        if not encode_fails:
            assert record["decode_records"][0]["fgclip_max_num_patches"] == (1024 if adaptive else 256)
