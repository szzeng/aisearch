"""Guard Gemma's numerical precision, selective encoders and media prompts."""

import json
from contextlib import nullcontext
from types import SimpleNamespace

import numpy as np
import pytest

from tools.evaluation import budget_gemma_runtime as gemma


@pytest.fixture
def locked_model(tmp_path, monkeypatch):
    config = {"model_type": "embedding_gemma2", "vision_soft_tokens_per_image": 280}
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (tmp_path / "1_Pooling").mkdir()
    (tmp_path / "1_Pooling/config.json").write_text("{}", encoding="utf-8")
    lock = {"model_id": gemma.MODEL_ID, "revision": gemma.REVISION,
            "files": {p.relative_to(tmp_path).as_posix(): {"sha256": gemma.sha256(p),
                                                          "bytes": p.stat().st_size}
                      for p in tmp_path.rglob("*") if p.is_file()}}
    original = gemma.read_json
    monkeypatch.setattr(gemma, "read_json", lambda p: lock if p == gemma.LOCK else original(p))
    return tmp_path, lock


def test_official_nested_pooling_config_is_accepted(locked_model):
    path, lock = locked_model
    assert gemma.verify_gemma(path) == lock


@pytest.mark.parametrize("mutation", ["changed", "extra", "escape", "revision"])
def test_lock_refuses_mutation(locked_model, mutation):
    path, lock = locked_model
    if mutation == "changed":
        (path / "1_Pooling/config.json").write_text("[]", encoding="utf-8")
    elif mutation == "extra":
        (path / "unknown.py").write_text("", encoding="utf-8")
    elif mutation == "escape":
        lock["files"]["../outside"] = {"bytes": 0, "sha256": "0" * 64}
    else:
        lock["revision"] = "main"
    with pytest.raises(ValueError):
        gemma.verify_gemma(path)


def test_fp16_refused_before_loading_anything(tmp_path):
    with pytest.raises(ValueError, match="FP16 is unsafe"):
        gemma.GemmaRuntime(tmp_path, "fp16")


def test_image_has_no_query_prompt_and_text_uses_search_prompt(tmp_path):
    calls = []

    def encode(items, **options):
        calls.append((items, options))
        return np.ones((1, 768), dtype=np.float32)

    runtime = gemma.GemmaRuntime(tmp_path, "fp32")
    runtime.torch = SimpleNamespace(inference_mode=nullcontext)
    runtime.model = SimpleNamespace(encode=encode)
    picture = object()
    assert runtime.encode(text="猫").shape == (1, 768)
    assert runtime.encode(image=picture).shape == (1, 768)
    assert calls[0][0] == ["猫"]
    assert calls[0][1]["prompt_name"] == "SearchQuery"
    assert calls[1][0] == [{"image": picture}]
    assert calls[1][1]["prompt"] is None
    assert "prompt_name" not in calls[1][1]
    assert all(options["batch_size"] == 1 for _, options in calls)
    with pytest.raises(ValueError):
        runtime.encode(text="猫", image=picture)
