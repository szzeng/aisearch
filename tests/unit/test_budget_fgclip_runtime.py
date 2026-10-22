"""Refuse mutable/unpinned FG-CLIP2 code before importing model definitions."""

import json
from contextlib import nullcontext
from types import SimpleNamespace

import numpy as np
import pytest

from tools.evaluation import budget_fgclip_runtime as fg


@pytest.fixture
def locked_model(tmp_path, monkeypatch):
    config = {"model_type": "fgclip2", "text_config": {"hidden_size": 768,
                                                        "max_position_embeddings": 64},
              "vision_config": {"hidden_size": 768}}
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (tmp_path / "modeling_fgclip2.py").write_text("# fixed model code\n", encoding="utf-8")
    lock = {"model_id": fg.MODEL_ID, "revision": fg.REVISION,
            "files": {p.name: {"sha256": fg.sha256(p), "bytes": p.stat().st_size}
                      for p in tmp_path.iterdir()}}
    original = fg.read_json
    monkeypatch.setattr(fg, "read_json", lambda p: lock if p == fg.LOCK else original(p))
    return tmp_path, lock


def test_exact_fixed_model_is_accepted(locked_model):
    directory, lock = locked_model
    assert fg.verify_fgclip(directory) == lock


@pytest.mark.parametrize("change", ["content", "size", "extra", "revision", "architecture"])
def test_model_code_or_identity_changes_are_refused(locked_model, change):
    directory, lock = locked_model
    if change == "content":
        path = directory / "modeling_fgclip2.py"
        path.write_text("# other model code\n", encoding="utf-8")
    elif change == "size":
        (directory / "modeling_fgclip2.py").write_text("x", encoding="utf-8")
    elif change == "extra":
        (directory / "extra.py").write_text("x", encoding="utf-8")
    elif change == "revision":
        lock["revision"] = "main"
    else:
        path = directory / "config.json"
        config = json.loads(path.read_text())
        config["text_config"]["max_position_embeddings"] = 196
        path.write_text(json.dumps(config), encoding="utf-8")
        lock["files"][path.name] = {"sha256": fg.sha256(path), "bytes": path.stat().st_size}
    with pytest.raises(ValueError):
        fg.verify_fgclip(directory)


def test_loader_checks_integrity_before_dynamic_import(locked_model, monkeypatch):
    directory, _ = locked_model
    (directory / "modeling_fgclip2.py").write_text("changed", encoding="utf-8")
    runtime = fg.FgclipRuntime(directory, "fp32")
    with pytest.raises(ValueError, match="content differs"):
        runtime.load()
    assert runtime.torch is None


def test_model_lock_path_escape_refused(locked_model):
    directory, lock = locked_model
    lock["files"]["../outside.py"] = {"sha256": "0" * 64, "bytes": 0}
    with pytest.raises(ValueError, match="filename"):
        fg.verify_fgclip(directory)


@pytest.mark.parametrize("adaptive", [False, True])
def test_text_call_preserves_official_short_pooling_and_no_padding_mask(tmp_path, adaptive):
    calls = {}

    class Tensor:
        dtype = "int64"

        def is_floating_point(self):
            return False

        def to(self, **kwargs):
            return self

    class Result:
        def detach(self):
            return self

        def float(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return np.ones((1, 768), dtype=np.float32)

    def tokenize(texts, **kwargs):
        calls["tokenizer"] = texts, kwargs
        return {"input_ids": Tensor()}

    def encode(**kwargs):
        calls["model"] = kwargs
        return Result()

    runtime = fg.FgclipRuntime(tmp_path, "fp32", adaptive=adaptive)
    runtime.torch = SimpleNamespace(inference_mode=nullcontext)
    runtime.tokenizer = tokenize
    runtime.model = SimpleNamespace(parameters=lambda: iter([SimpleNamespace(dtype="float32")]),
                                    get_text_features=encode)
    assert runtime.encode(text="CAT").shape == (1, 768)
    texts, options = calls["tokenizer"]
    assert texts == ["cat"]
    assert options["return_attention_mask"] is False
    assert options["max_length"] == 64 and options["padding"] == "max_length"
    assert set(calls["model"]) == {"input_ids", "walk_type"}
    assert calls["model"]["walk_type"] == "short"


@pytest.mark.parametrize("size,expected", [
    ((16, 128 * 16), 128), ((16, 129 * 16), 256),
    ((16, 256 * 16), 256), ((16, 257 * 16), 576),
    ((16, 576 * 16), 576), ((16, 577 * 16), 784),
    ((16, 784 * 16), 784), ((16, 785 * 16), 1024),
    ((15, 4096), 128), ((31, 128 * 16 + 15), 128), ((10000, 8000), 1024),
])
def test_official_patch_budget_thresholds_floor_and_cap(size, expected):
    assert fg.official_patch_budget(SimpleNamespace(size=size)) == expected


@pytest.mark.parametrize("adaptive,expected", [(False, 256), (True, 1024)])
def test_image_call_uses_selected_budget_and_same_uncropped_image(tmp_path, adaptive, expected):
    calls = {}
    image = SimpleNamespace(size=(800, 600))

    class Result:
        def detach(self):
            return self

        def float(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return np.ones((1, 768), dtype=np.float32)

    def preprocess(**kwargs):
        calls["processor"] = kwargs
        return {}

    runtime = fg.FgclipRuntime(tmp_path, "fp16", adaptive=adaptive)
    runtime.torch = SimpleNamespace(inference_mode=nullcontext)
    runtime.processor = preprocess
    runtime.model = SimpleNamespace(parameters=lambda: iter([SimpleNamespace(dtype="float16")]),
                                    get_image_features=lambda **_: Result())
    assert runtime.encode(image=image).shape == (1, 768)
    assert calls["processor"] == {"images": image, "max_num_patches": expected,
                                  "patch_size": 16, "return_tensors": "pt"}
