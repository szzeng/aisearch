"""Partial annotations and detection fusion must not leak truth into candidates."""

import copy
import json
import math
import sys
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from tools.evaluation import budget_search_metrics as metrics


def test_unknown_is_excluded_only_from_condensed_metric_and_remains_visible_in_full_rank():
    result = metrics.judged_metrics(["unknown", "cat", "other", "cat2"], {
        "unknown": None, "cat": True, "other": False, "cat2": True})
    assert math.isclose(result["AP"], 5 / 6)
    assert result["judged_ranking"] == ["cat", "other", "cat2"]
    assert result["judged_population"] == 3
    assert result["unknown_population"] == 1
    assert result["full_gallery"]["100"]["unknown"] == 1
    assert result["full_gallery"]["20"]["precision"] is None
    assert result["P20"] is None  # A short set cannot claim precision at 20.
    assert result["positive_full_rank_max"] == 4


def test_complete_population_has_exact_precision_and_recall():
    ids = [f"{i:03d}" for i in range(120)]
    result = metrics.judged_metrics(ids, {aid: int(aid) < 30 for aid in ids})
    assert result["AP"] == 1
    assert result["P20"] == result["full_gallery"]["20"]["precision"] == 1
    assert result["P50"] == 0.6
    assert result["Recall100"] == 1


@pytest.mark.parametrize("values", [{"a": None}, {"a": False}])
def test_no_positive_does_not_claim_perfect_average_precision(values):
    result = metrics.judged_metrics(["a"], values)
    assert result["AP"] is None
    assert result["Recall100"] is None


@pytest.mark.parametrize("ranking,labels", [(["a", "a"], {"a": True, "b": False}),
                                           (["a"], {"b": True}), (["a"], {"a": 1})])
def test_incomplete_duplicate_or_nonboolean_truth_is_rejected(ranking, labels):
    with pytest.raises(ValueError):
        metrics.judged_metrics(ranking, labels)


def test_partition_is_stable_bounded_and_never_deletes_a_missed_detection():
    ids = ["a", "b", "c", "d", "e"]
    scores = {"a": 0.1, "b": 0.8, "c": 0.5, "d": 0.2, "e": 0.99}
    assert metrics.stable_detection_partition(ids, scores) == ["b", "c", "e", "a", "d"]
    assert metrics.stable_detection_partition(ids, scores, top_k=3) == ["b", "c", "a", "d", "e"]
    assert ids == ["a", "b", "c", "d", "e"]


@pytest.mark.parametrize("score", [-0.1, 1.1, float("nan"), float("inf")])
def test_invalid_detector_score_is_rejected(score):
    with pytest.raises(ValueError, match="probabilities"):
        metrics.stable_detection_partition(["a"], {"a": score})


def test_fusion_uses_full_top100_before_judged_filter():
    ids = [f"{i:03d}" for i in range(101)]
    scores = {aid: float(aid == "100") for aid in ids}
    # 99 unknowns must not allow the 101st candidate to slip into a truth-filtered top100.
    labels = {aid: None for aid in ids}
    labels.update({"000": False, "100": True})
    fused = metrics.stable_detection_partition(ids, scores, top_k=100)
    assert fused == ids
    assert metrics.judged_metrics(fused, labels)["AP"] == 0.5
    full = metrics.stable_detection_partition(ids, scores)
    assert full[0] == "100"
    assert metrics.judged_metrics(full, labels)["AP"] == 1


def test_add_schemes_preserves_vector_scheme_and_complete_gallery():
    original = {"vector": {"cat": ["b", "a"], "dog": ["a", "b"]}}
    before = copy.deepcopy(original)
    out = metrics.add_detector_schemes(original, {"cat": {"a": 0.8, "b": 0.1},
                                                 "dog": {"a": 0.1, "b": 0.8}}, ["a", "b"])
    assert original == before
    assert len(out) == 4
    assert out["detector_only"]["cat"] == ["a", "b"]
    assert out["vector+detector_partition_all"]["dog"] == ["b", "a"]


def test_box_observations_only_count_known_positive_and_keep_unknown_box_group():
    result = metrics.box_rank_observations(["a", "b", "c"], {"a": None, "b": True, "c": True},
                                           {"b": {"cat": {"max_box_area_fraction": 0.01,
                                                            "has_occluded": True, "has_depiction": False}}}, "cat")
    assert result["small_le_5pct"]["items"][0]["full_rank"] == 2
    assert result["occluded"]["count"] == 1
    assert result["box_unknown"]["items"] == [{"asset_id": "c", "full_rank": 3}]


def test_evaluation_requires_exact_bytes_queries_and_full_rankings():
    ids = [{"asset_id": "a", "sha256": "1" * 64}, {"asset_id": "b", "sha256": "2" * 64}]
    queries = [{"query_id": "cat", "text": "猫"}]
    labels = {"input_identities": ids, "query_definitions": queries, "provenance": {"limitations": []},
              "queries": {"cat": [{"asset_id": "a", "relevant": True}, {"asset_id": "b", "relevant": None}]}}
    out = metrics.evaluate_rankings({"x": {"cat": ["b", "a"]}}, labels, ids, queries)
    assert out["results"]["x"]["cat"]["AP"] == 1
    for wrong_ids, wrong_queries in [(ids[::-1], queries), (ids, [{"query_id": "cat", "text": "小猫"}])]:
        with pytest.raises(ValueError, match="different"):
            metrics.evaluate_rankings({"x": {"cat": ["a", "b"]}}, labels, wrong_ids, wrong_queries)
    with pytest.raises(ValueError, match="complete"):
        metrics.evaluate_rankings({"x": {"cat": ["a"]}}, labels, ids, queries)


def embedding_record(model, dim):
    record = {"schema": "budget-embedding-run-v1", "status": "COMPLETE", "model": model, "dtype": "fp16",
            "native_dimension": dim, "identity": "identity", "model_lock": {"fixed": True},
            "sample_sha256": "sample", "assets": [{"asset_id": "a", "sha256": "sha"}],
            "queries": [{"query_id": "cat", "text": "猫"}], "settings": {"fixed": True},
            "packages": {"fixed": True}, "decoder": {"fixed": True}, "source_sha256": {"fixed": True}}
    record["identity"] = metrics.cache_identity(
        {key: record[key] for key in ("model", "dtype", "settings", "model_lock", "packages", "decoder",
                                      "source_sha256", "native_dimension")}, record["assets"], record["queries"])
    return record


def test_only_qwen_uses_mrl_prefix_and_native_vectors_must_be_valid():
    vector = np.ones((1, 768), dtype=np.float32) / np.sqrt(np.float32(768))
    record = embedding_record("siglip-base", 768)
    schemes, _ = metrics.embeddings_from_record(record, vector, vector)
    assert set(schemes) == {"siglip-base-fp16-768"}
    qwen = np.ones((1, 2048), dtype=np.float32) / np.sqrt(np.float32(2048))
    schemes, _ = metrics.embeddings_from_record(embedding_record("qwen", 2048), qwen, qwen)
    assert set(schemes) == {"qwen-fp16-512", "qwen-fp16-1024", "qwen-fp16-2048"}
    for bad in (vector * 2, vector.astype(np.float64), np.zeros_like(vector), vector[:, :512]):
        with pytest.raises(ValueError, match="invalid native"):
            metrics.embeddings_from_record(record, bad, vector)
    with pytest.raises(ValueError, match="MRL"):
        metrics.embeddings_from_record(embedding_record("siglip-base", 512), vector[:, :512], vector[:, :512])
    record["dtype"] = "fp32"
    with pytest.raises(ValueError, match="cache identity"):
        metrics.embeddings_from_record(record, vector, vector)
    fg, _ = metrics.embeddings_from_record(embedding_record("fgclip-base", 768), vector, vector)
    assert set(fg) == {"fgclip-base-fp16-768"}
    adaptive_fg, _ = metrics.embeddings_from_record(embedding_record("fgclip-base-adaptive", 768), vector, vector)
    assert set(adaptive_fg) == {"fgclip-base-adaptive-fp16-768"}
    gemma_record = embedding_record("embeddinggemma2", 768)
    gemma_record["dtype"] = "fp32"
    gemma_record["identity"] = metrics.cache_identity(
        {key: gemma_record[key] for key in ("model", "dtype", "settings", "model_lock", "packages", "decoder",
                                            "source_sha256", "native_dimension")},
        gemma_record["assets"], gemma_record["queries"])
    gemma, _ = metrics.embeddings_from_record(gemma_record, vector, vector)
    assert set(gemma) == {"embeddinggemma2-fp32-256", "embeddinggemma2-fp32-512", "embeddinggemma2-fp32-768"}


def test_detector_must_bind_both_manifest_and_per_image_bytes():
    identities = [{"asset_id": "a", "sha256": "original"}]
    record = {"schema": "budget-object-probe-v1", "status": "COMPLETE", "cache_identity": "fixed",
              "spec": {"model": "fixed"}, "inputs": identities, "sample_sha256": "sample",
              "images": [{"asset_id": "a", "sha256": "original", "class_scores": {"cat": 0.8, "dog": 0.1}}]}
    record["cache_identity"] = metrics.cache_identity(
        {**record["spec"], "sample_sha256": "sample"}, identities,
        [{"operation": "fixed-coco80-object-detection"}])
    assert metrics.detector_from_record(record, identities, "sample", identities) == {
        "cat": {"a": 0.8}, "dog": {"a": 0.1}}
    with pytest.raises(ValueError, match="identity"):
        metrics.detector_from_record(record, identities, "changed", identities)
    record["spec"]["dtype"] = "changed"
    with pytest.raises(ValueError, match="cache identity"):
        metrics.detector_from_record(record, identities, "sample", identities)
    del record["spec"]["dtype"]
    record["images"][0]["sha256"] = "changed"
    with pytest.raises(ValueError, match="bytes"):
        metrics.detector_from_record(record, identities, "sample", identities)


def test_box_source_identity_and_membership_required_while_missing_boxes_allowed():
    labels = {"provenance": {"source_identity": {"boxes.csv": {"sha256": "official"}}}}
    identities = [{"asset_id": "a", "sha256": "original"}, {"asset_id": "b", "sha256": "other"}]
    doc = {"source_sha256": "official", "assets": {"a": {"cat": {"area": 0.2}}}}
    assert metrics.verified_box_evidence(doc, labels, identities) == doc["assets"]
    for bad in ({**doc, "source_sha256": "changed"},
                {**doc, "assets": {"outside": {}}}, {**doc, "assets": {"a": {"human": {}}}}):
        with pytest.raises(ValueError):
            metrics.verified_box_evidence(bad, labels, identities)


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit])
def test_interrupted_cli_writes_failed_junit_not_false_pass(tmp_path, monkeypatch, exception):
    candidate = tmp_path / "ranking.json"
    candidate.write_text(json.dumps({"rankings": {}, "input_identities": [], "query_definitions": []}),
                         encoding="utf-8")
    labels = tmp_path / "labels.json"
    labels.write_text("{}", encoding="utf-8")
    junit = tmp_path / "result.xml"
    output = tmp_path / "result.json"
    monkeypatch.setattr(metrics, "controlled_output", lambda path: path)

    def interrupt(*args):
        raise exception("cancelled")

    monkeypatch.setattr(metrics, "evaluate_rankings", interrupt)
    monkeypatch.setattr(sys, "argv", ["metrics", "--rankings", str(candidate), "--labels", str(labels),
                                     "--out", str(output), "--junit", str(junit)])
    with pytest.raises(exception):
        metrics.main()
    tree = ET.parse(junit)
    assert tree.getroot().attrib["failures"] == "1"
    assert tree.find("testcase/failure") is not None
    assert not output.exists()
