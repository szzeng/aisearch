"""Fixed RRF preserves full candidate membership and verifies legacy vector caches."""

import hashlib
import json

import numpy as np
import pytest

from tools.evaluation import budget_rank_fusion as fusion


def test_rrf_matches_manual_formula_and_numeric_id_ties():
    left, right = [10, 2, 8], [8, 2, 10]
    assert fusion.weighted_rrf(left, right) == [8, 10, 2]
    assert fusion.weighted_rrf(left, right, 2) == [10, 2, 8]
    assert left == [10, 2, 8] and right == [8, 2, 10]


def test_equal_rankings_are_identity_and_every_candidate_survives():
    values = ["z", "a", "y", "b"]
    for weight in (1, 2):
        assert fusion.weighted_rrf(values, values, weight) == values
        result = fusion.weighted_rrf(values, values[::-1], weight)
        assert len(result) == len(values) and set(result) == set(values)


@pytest.mark.parametrize("weight", [0, 3, True, 1.0, float("nan")])
def test_unplanned_weights_are_refused(weight):
    with pytest.raises(ValueError):
        fusion.weighted_rrf(["a"], ["a"], weight)


@pytest.mark.parametrize("left,right", [([], []), ([1, 1], [1, 2]), ([1], [2]),
                                         ([1, 2], [1]), (list(range(1001)), list(range(1001)))])
def test_incomplete_duplicate_or_oversize_inputs_are_refused(left, right):
    with pytest.raises(ValueError):
        fusion.weighted_rrf(left, right)


@pytest.fixture
def legacy_run(tmp_path, monkeypatch):
    monkeypatch.setattr(fusion, "ROOT", tmp_path)
    sample = {"assets": [{"asset_id": 1, "sha256": "a" * 64, "selection_stratum": "random"}]}
    queries = [{"query_id": "cat", "text": "猫"}]
    spec = {"model": "qwen", "native_dimension": 2048, "fixed": True}
    inputs = sample["assets"]
    identity = {"cache_identity": fusion.cache_identity(spec, inputs, queries),
                "model_spec": spec, "inputs": inputs, "queries": queries}
    vector = np.zeros((1, 2048), dtype=np.float32)
    vector[0, 0] = 1
    path = tmp_path / "vector.npy"
    np.save(path, vector, allow_pickle=False)
    vector_record = {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    record = {**identity, "schema": "wemm-pilot-run-v1", "status": "COMPLETE", "stability": {"passed": True},
              "images": {"0": vector_record}, "query_vectors": {"0": vector_record}}
    (tmp_path / "result.json").write_text(json.dumps(record), encoding="utf-8")
    (tmp_path / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
    return tmp_path, sample, queries, record


def test_legacy_native_cache_is_reused_with_exact_identity(legacy_run):
    directory, sample, queries, _ = legacy_run
    reader = fusion.Reader()
    ranks, provenance = fusion.load_legacy_qwen(reader, directory, sample, queries)
    assert ranks == {"qwen-fp16-1024": {"cat": [1]}, "qwen-fp16-2048": {"cat": [1]}}
    assert provenance["kind"] == "original_native2048_cache"
    assert "vector.npy" in reader.verify_unchanged()


@pytest.mark.parametrize("mutation", ["vector_bytes", "identity", "query", "sample", "extra_vector", "status"])
def test_legacy_cache_corruption_or_wrong_provenance_is_refused(legacy_run, mutation):
    directory, sample, queries, record = legacy_run
    if mutation == "vector_bytes":
        (directory / "vector.npy").write_bytes(b"changed")
    elif mutation == "identity":
        record["model_spec"]["fixed"] = False
    elif mutation == "query":
        queries = [{"query_id": "cat", "text": "dog"}]
    elif mutation == "sample":
        sample = {"assets": [{**sample["assets"][0], "sha256": "b" * 64}]}
    elif mutation == "extra_vector":
        record["images"]["1"] = record["images"]["0"]
    else:
        record["status"] = "FAILED"
    (directory / "result.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError):
        fusion.load_legacy_qwen(fusion.Reader(), directory, sample, queries)


def test_reader_detects_changes_after_calculation(legacy_run):
    directory, _, _, _ = legacy_run
    reader = fusion.Reader()
    path = directory / "identity.json"
    reader.json(path)
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        reader.verify_unchanged()
