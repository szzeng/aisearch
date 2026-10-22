"""Candidate identity, strict image handling, and bounded work for offline reranking."""

from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pytest

from tools.evaluation import vector_quality_reranker as probe


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    assets = []
    for asset_id in range(1, 102):
        path = tmp_path / f"{asset_id}.jpg"
        data = str(asset_id).encode()
        path.write_bytes(data)
        assets.append(
            {
                "asset_id": asset_id,
                "path": str(path),
                "sha256": hashlib.sha256(data).hexdigest(),
                "selection_stratum": "random",
            }
        )
    qids = probe.QUERY_IDS
    sample = tmp_path / "sample.json"
    queries = tmp_path / "queries.json"
    comparison = tmp_path / "comparison.json"
    sample.write_text(json.dumps({"assets": assets}), encoding="utf-8")
    queries.write_text(json.dumps([{"query_id": q, "text": q} for q in qids]), encoding="utf-8")
    # A different, non-ID order proves candidates come from retrieval rather than sorting IDs.
    ranking = list(range(101, 0, -1))
    comparison.write_text(
        json.dumps(
            {
                "inputs": [
                    {k: a[k] for k in ("asset_id", "sha256", "selection_stratum")} for a in assets
                ],
                "query_definitions": [{"query_id": q, "text": q} for q in qids],
                "rankings": {
                    "qwen2048": {q: ranking for q in qids},
                    "qwen512": {q: list(reversed(ranking)) for q in qids},
                },
            }
        ),
        encoding="utf-8",
    )
    return sample, queries, comparison


def plan(inputs, **kwargs):
    return probe.make_plan(*inputs, baseline="qwen2048", top_k=100, smoke=False, **kwargs)


def test_plan_uses_the_exact_candidate_prefix_and_no_labels(inputs):
    result = plan(inputs)
    assert len(result["pairs"]) == 800
    assert [p["asset_id"] for p in result["pairs"][:100]] == list(range(101, 1, -1))
    assert result["pairs"][0]["path"] == "101.jpg"
    assert {p["query_id"] for p in result["pairs"]} == set(probe.QUERY_IDS)
    assert all("relevant" not in p for p in result["pairs"])
    probe.verify_plan(result)


def test_smoke_exercises_late_candidates_without_changing_main_plan(inputs):
    result = probe.make_plan(*inputs, baseline="qwen2048", top_k=100, smoke=True)
    assert len(result["pairs"]) == 16
    assert [p["baseline_rank"] for p in result["pairs"][:4]] == [1, 60, 98, 100]
    assert [p["asset_id"] for p in result["pairs"][:4]] == [101, 42, 4, 2]
    probe.verify_plan(result)


@pytest.mark.parametrize("top_k", [0, 101, -1, True, 1.2])
def test_pair_work_cannot_exceed_bound(inputs, top_k):
    with pytest.raises(ValueError, match="TOP_K_OUT_OF_BOUND"):
        probe.make_plan(*inputs, baseline="qwen2048", top_k=top_k, smoke=False)


@pytest.mark.parametrize("mutation", ["candidate", "query", "pixel", "duplicate", "drop"])
def test_edited_plan_cannot_select_easier_examples(inputs, mutation):
    result = plan(inputs)
    if mutation == "candidate":
        result["pairs"][0]["asset_id"] = 1
    elif mutation == "query":
        result["pairs"][0]["query"] = "a completely different request"
    elif mutation == "pixel":
        result["max_pixels"] //= 2
    elif mutation == "duplicate":
        result["pairs"][1] = copy.deepcopy(result["pairs"][0])
    else:
        result["pairs"].pop()
    with pytest.raises(ValueError, match="PLAN_MISMATCH"):
        probe.verify_plan(result)


def test_input_and_image_changes_invalidate_plan(inputs):
    result = plan(inputs)
    comparison = inputs[2]
    before = comparison.read_bytes()
    comparison.write_bytes(before + b" ")
    with pytest.raises(ValueError, match="INPUT_IDENTITY_MISMATCH"):
        probe.verify_plan(result)
    comparison.write_bytes(before)
    (probe.ROOT / "101.jpg").write_bytes(b"replacement photo")
    with pytest.raises(ValueError, match="IMAGE_IDENTITY_MISMATCH"):
        probe.verify_plan(result)


def test_duplicate_and_incomplete_retrieval_are_not_silently_repaired(inputs):
    comparison = inputs[2]
    data = probe.read_json(comparison)
    data["rankings"]["qwen2048"]["cat"][-1] = 101
    comparison.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="RANKING_ASSET_COVERAGE_MISMATCH"):
        plan(inputs)


@pytest.mark.parametrize(
    "field,value", [("sha256", "another image"), ("selection_stratum", "targeted")]
)
def test_same_ids_from_a_different_comparison_sample_are_refused(inputs, field, value):
    comparison = inputs[2]
    data = probe.read_json(comparison)
    data["inputs"][0][field] = value
    comparison.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="COMPARISON_INPUT_IDENTITY_MISMATCH"):
        plan(inputs)


def test_same_query_id_with_different_comparison_text_is_refused(inputs):
    comparison = inputs[2]
    data = probe.read_json(comparison)
    data["query_definitions"][0]["text"] = "a changed query"
    comparison.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="COMPARISON_QUERY_IDENTITY_MISMATCH"):
        plan(inputs)


def test_workspace_escape_rejected(inputs):
    with pytest.raises(ValueError, match="WORKSPACE_PATH_REQUIRED"):
        probe.workspace_path("../outside.jpg")


def test_model_code_is_hash_verified_before_import(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    model = tmp_path / "model"
    model.mkdir()
    source = model / "official.py"
    source.write_bytes(b"original")
    lock = {
        "revision": probe.MODEL_REV,
        "files": {
            "official.py": {"bytes": 8, "sha256": probe.sha256(source)},
        },
    }
    probe.verify_model_files(model, lock)
    source.write_bytes(b"modified")
    with pytest.raises(ValueError, match="MODEL_HASH_MISMATCH"):
        probe.verify_model_files(model, lock)


def test_existing_output_is_preserved_even_after_failed_run(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    output = tmp_path / "prior"
    output.mkdir()
    original = output / "failure.json"
    original.write_text('{"original_failure":true}', encoding="utf-8")
    with pytest.raises(FileExistsError, match="OUTPUT_EXISTS"):
        probe.main(["run", "--plan", "missing.json", "--output", str(output)])
    assert original.read_text(encoding="utf-8") == '{"original_failure":true}'


class FakeProcessor:
    def __init__(self, count=4):
        self.count = count
        self.calls = []
        self.tokenizer = SimpleNamespace(
            pad=lambda data, **kwargs: {
                "input_ids": np.array(data["input_ids"]),
                "attention_mask": np.ones((1, self.count)),
            }
        )

    def apply_chat_template(self, pairs, **kwargs):
        self.calls.append((pairs, kwargs))
        return "official template"

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {"input_ids": [list(range(self.count))], "image_grid_thw": np.array([[1, 16, 16]])}


def test_vision_error_never_falls_back_to_text_only():
    processor = FakeProcessor()
    reranker = SimpleNamespace(processor=processor, max_length=8192)

    def bad_vision(*args, **kwargs):
        raise OSError("truncated image")

    with pytest.raises(OSError, match="truncated image"):
        probe.strict_tokenize(reranker, ["real image pair"], bad_vision)
    assert len(processor.calls) == 1
    assert processor.calls[0][0] == ["real image pair"]


@pytest.mark.parametrize("images,videos", [(None, None), ([], None), ([1, 2], None), ([1], [2])])
def test_only_one_actual_image_is_accepted(images, videos):
    reranker = SimpleNamespace(processor=FakeProcessor(), max_length=8192)
    with pytest.raises(ValueError, match="EXPECTED_EXACTLY_ONE_IMAGE"):
        probe.strict_tokenize(reranker, ["pair"], lambda *a, **kw: (images, videos, {}))


def test_long_input_stops_before_silent_truncation():
    reranker = SimpleNamespace(processor=FakeProcessor(count=9), max_length=8)
    with pytest.raises(ValueError, match="TOKEN_LIMIT_STOP"):
        probe.strict_tokenize(reranker, ["pair"], lambda *a, **kw: ([object()], None, {}))


def test_valid_input_preserves_official_template_and_preprocessed_pixels():
    processor = FakeProcessor()
    reranker = SimpleNamespace(processor=processor, max_length=8192)
    image = object()
    result = probe.strict_tokenize(reranker, ["pair"], lambda *a, **kw: ([image], None, {}))
    assert result["input_ids"].shape == (1, 4)
    assert processor.calls[1]["images"] == [image]
    assert processor.calls[1]["do_resize"] is False
    assert processor.calls[1]["truncation"] is False
