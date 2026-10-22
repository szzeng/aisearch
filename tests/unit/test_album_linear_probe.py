"""Leakage, threshold, routing and objective tests for frozen-feature heads."""

import copy
import json
import sys
import xml.etree.ElementTree as ET

import numpy as np
import pytest
from scipy.optimize._numdiff import approx_derivative

from tools.evaluation import album_linear_probe as probe
from tools.evaluation.wemm_pilot_core import cache_identity


def assets(start, count, role="train2014"):
    return [{"asset_id": start + i, "sha256": f"{start + i:064x}",
             "flickr_photo_id": str(start + i + 10000), "source_split": role,
             "selection_stratum": "random" if i % 2 else "category:cat"} for i in range(count)]


def split(role, start=1, count=40):
    matrix = np.zeros((count, 4), dtype=np.float32)
    matrix[:, 0] = np.where(np.arange(count) % 2, 1, -1)
    return probe.Split(role, assets(start, count, "val2014" if role == "test" else "train2014"),
                       copy.deepcopy(probe.CLASS_QUERIES), matrix,
                       np.tile(np.array([1, 0, 0, 0], dtype=np.float32), (8, 1)),
                       {"dimension": 4, "encoder": "synthetic-test-only"}, {"role": role})


def all_labels(data):
    return {query: {aid: bool(i % 2) for i, aid in enumerate(data.ids)} for query in probe.CLASS_IDS}


def test_objective_gradient_matches_independent_finite_difference_and_intercept_unpenalized():
    features = np.array([[0.2, 1], [-0.5, 1], [0.9, -0.1]])
    labels, weights = np.array([1.0, 0, 1]), np.array([0.75, 1.5, 0.75])
    theta = np.array([0.2, -0.3, 0.5])
    loss, gradient = probe.logistic_objective(theta, features, labels, weights)
    numeric = approx_derivative(lambda x: probe.logistic_objective(x, features, labels, weights)[0], theta)
    assert np.allclose(gradient, numeric, atol=1e-8)
    logits = features @ theta[:-1] + theta[-1]
    expected = sum(w * (np.logaddexp(0, z) - y * z) for z, y, w in zip(logits, labels, weights, strict=True))
    assert loss == pytest.approx(expected + (0.2 ** 2 + 0.3 ** 2) / 2)


def test_balanced_logistic_learns_separable_data_and_records_prior_change():
    data = np.array([[-1, 0]] * 18 + [[1, 0]] * 2, dtype=np.float32)
    weight, bias, result = probe.fit_logistic(data, np.array([0] * 18 + [1] * 2))
    assert weight[0] > 0 and abs(bias) < 1e-6 and weight[1] == 0
    assert result["class_weights"] == {"positive": 5, "negative": 20 / 36}
    assert result["iterations"] > 0
    assert "NOT a calibrated probability" in probe.CONFIG["score_meaning"]


@pytest.mark.parametrize("labels", [[0, 0], [1, 1], [0, 2], [0, float("nan")]])
def test_invalid_or_one_class_training_refused(labels):
    with pytest.raises(ValueError):
        probe.fit_logistic(np.eye(2), np.array(labels))


def test_calibration_uses_largest_qualified_complete_tie_group():
    # First 20 are all positive; next two tied negatives still permit 20/22.
    scores = np.array([3.0] * 20 + [2.0] * 2 + [1.0] * 3)
    selected = probe.select_threshold(scores, np.array([1] * 20 + [0] * 5))
    assert selected["enabled"] and selected["selected"] == 22
    assert selected["threshold"] == 2 and selected["precision"] == pytest.approx(20 / 22)


def test_calibration_does_not_split_ties_to_invent_precision():
    result = probe.select_threshold(np.ones(30), np.array([1] * 20 + [0] * 10))
    assert result["enabled"] is False and result["threshold"] is None


def test_calibration_can_recover_precision_after_a_bad_prefix():
    labels = np.array([0] * 2 + [1] * 20 + [0] * 3)
    result = probe.select_threshold(np.arange(25, 0, -1), labels)
    assert result["selected"] == 22 and result["precision"] == pytest.approx(20 / 22)


def test_calibration_abstains_when_support_is_too_small_or_no_positive():
    for labels in (np.ones(19), np.zeros(50)):
        result = probe.select_threshold(np.arange(len(labels)), labels)
        assert result["enabled"] is False and result["selected"] == 0


@pytest.mark.parametrize("changed", ["asset_id", "sha256", "flickr_photo_id"])
def test_all_three_cross_split_leakage_sources_refused(changed):
    left, right = assets(1, 2), assets(10, 2)
    right[0][changed] = left[1][changed]
    with pytest.raises(ValueError, match="leakage"):
        probe.disjoint_samples(left, right)


def test_flickr_source_cross_check_and_internal_duplicates():
    item = assets(1, 1)[0]
    item["coco"] = {"flickr_url": "http://farm.staticflickr.com/2/10001_abcd.jpg"}
    assert probe.flickr_id(item) == "10001"
    item["flickr_photo_id"] = "10002"
    with pytest.raises(ValueError, match="inconsistent"):
        probe.flickr_id(item)
    with pytest.raises(ValueError, match="duplicate"):
        probe.asset_keys(assets(1, 2) + assets(1, 1))


def test_fit_refuses_test_role_and_unknown_training_label():
    train, calibration = split("train"), split("calibration", 101)
    labels, cal_labels = all_labels(train), all_labels(calibration)
    train.role = "test"
    with pytest.raises(ValueError, match="roles"):
        probe.fit_heads(train, calibration, labels, cal_labels)
    train.role = "train"
    labels["cat"][train.ids[0]] = None
    with pytest.raises(ValueError, match="boolean"):
        probe.fit_heads(train, calibration, labels, cal_labels)


def test_fit_score_route_and_test_labels_cannot_change_frozen_predictions():
    train, calibration, test = split("train"), split("calibration", 101), split("test", 201)
    manifest, weights, biases = probe.fit_heads(train, calibration, all_labels(train), all_labels(calibration))
    before = json.dumps(manifest, sort_keys=True)
    prediction = probe.predict(manifest, weights, biases, test)
    assert all(value == "head" for value in prediction["routes"].values())
    assert all(len(ranking) == 40 for ranking in prediction["rankings"]["category_route"].values())
    assert len(prediction["high_confidence"]["cat"]) == 20
    assert prediction["high_confidence_by_scheme"]["cosine"]["cat"] == prediction["high_confidence"]["cat"]
    assert manifest["classes"]["cat"]["cosine_calibration"]["precision"] == 1
    first = probe.evaluate_predictions(prediction, test, all_labels(test))
    opposite = {query: {aid: not value for aid, value in rows.items()} for query, rows in all_labels(test).items()}
    second = probe.evaluate_predictions(prediction, test, opposite)
    assert first["all"]["ranking_metrics"]["linear_head"]["cat"]["AP"] == 1
    assert second["all"]["ranking_metrics"]["linear_head"]["cat"]["AP"] < 0.5
    assert json.dumps(manifest, sort_keys=True) == before


@pytest.fixture
def frozen_model(tmp_path):
    train, calibration = split("train"), split("calibration", 101)
    manifest, weights, biases = probe.fit_heads(train, calibration, all_labels(train), all_labels(calibration))
    np.savez(tmp_path / "weights.npz", weights=weights, biases=biases)
    manifest["weights_sha256"] = probe.sha((tmp_path / "weights.npz").read_bytes())
    manifest["identity"] = probe.sha(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode())
    (tmp_path / "model.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path, manifest, weights, biases


def test_frozen_archive_reloads_without_pickle_and_preserves_weights(frozen_model):
    directory, manifest, weights, biases = frozen_model
    loaded, w, b = probe.read_model(probe.Reader(), directory)
    assert loaded == manifest
    np.testing.assert_array_equal(w, weights)
    np.testing.assert_array_equal(b, biases)


@pytest.mark.parametrize("change", ["weights", "threshold", "config", "invalid_threshold"])
def test_modified_weight_or_threshold_configuration_cannot_silently_load(frozen_model, change):
    directory, manifest, _, _ = frozen_model
    if change == "weights":
        (directory / "weights.npz").write_bytes(b"changed")
    else:
        if change == "threshold":
            manifest["classes"]["cat"]["calibration"]["threshold"] += 1
        elif change == "invalid_threshold":
            manifest["classes"]["cat"]["calibration"]["threshold"] = float("nan")
        else:
            manifest["config"] = {**manifest["config"], "C": 2}
        (directory / "model.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        probe.read_model(probe.Reader(), directory)


def test_scoring_rejects_changed_model_semantics_and_training_leakage(frozen_model):
    _, manifest, weights, biases = frozen_model
    test = split("test", 201)
    test.spec["encoder"] = "different-semantic-space"
    with pytest.raises(ValueError, match="embedding space"):
        probe.predict(manifest, weights, biases, test)
    test.spec = manifest["space_spec"]
    test.assets[0]["flickr_photo_id"] = manifest["training_assets"][0]["flickr_photo_id"]
    with pytest.raises(ValueError, match="leakage"):
        probe.predict(manifest, weights, biases, test)


def test_unit_query_direction_matches_head_order_and_zero_direction_abstains():
    rng = np.random.default_rng(51)
    data = split("test", 201)
    images = rng.normal(size=(40, 4))
    data.images = (images / np.linalg.norm(images, axis=1, keepdims=True)).astype(np.float32)
    weights, biases = rng.normal(size=(8, 4)), rng.normal(size=8)
    result = probe.indexed_query_equivalence(weights, biases, data)
    for value in result["classes"].values():
        assert value["float64_algebra_max_absolute_error"] < 1e-14
        assert value["float32_reconstructed_logit_max_absolute_error"] < 1e-6
        assert value["same_complete_order"]
    weights[0] = 0
    result = probe.indexed_query_equivalence(weights, biases, data)
    assert result["classes"]["cat"]["status"] == "ZERO_DIRECTION_NOT_REPRESENTABLE_AS_UNIT_QUERY"


def test_exact_alias_routing_preserves_complex_negation_and_metadata_queries():
    base, heads = [3, 1, 2], {query: [1, 2, 3] for query in probe.CLASS_IDS}
    classes = {query: {"calibration": {"enabled": True}} for query in probe.CLASS_IDS}
    for text in ("两只猫", "没有猫", "去年深圳的猫", "cat", "猫狗", "猫在沙发上"):
        assert probe.route_query(text, base, heads, classes) == (base, "base")
    assert probe.route_query(" 猫 ", base, heads, classes) == ([1, 2, 3], "head")
    classes["cat"]["calibration"]["enabled"] = False
    assert probe.route_query("猫", base, heads, classes) == (base, "base")
    classes["cat"]["calibration"]["enabled"] = True
    heads["cat"] = [1, 2]
    with pytest.raises(ValueError, match="preserve"):
        probe.route_query("猫", base, heads, classes)


def test_unknown_labels_are_visible_not_negative_and_strata_separate():
    data = split("test", 100, 4)
    labels = {"cat": {100: True, 101: None, 102: False, 103: True}}
    prediction = {"rankings": {"cosine": {"cat": [100, 101, 102, 103]}},
                  "high_confidence": {"cat": [100, 101]}}
    result = probe.evaluate_predictions(prediction, data, labels,
                                        {"100": {"cat": {"max_box_area_fraction": 0.03}}})
    confidence = result["all"]["confidence_metrics"]["cat"]
    assert confidence["unknown"] == 1 and confidence["judged_precision"] == 1
    assert confidence["full_precision"] is None
    assert result["random"]["ranking_metrics"]["cosine"]["cat"]["unknown_population"] == 1
    assert result["small_subject_diagnostics"]["cat"]["count"] == 1


def test_no_hit_negative_gallery_exposes_false_confident_results():
    result = probe.confidence_metrics([1, 3], {1: False, 2: False, 3: False, 4: False})
    assert result["full_precision"] == 0
    assert result["known_positive_recall"] is None
    assert result["false_positive_rate_on_known_negatives"] == 0.5
    assert result["no_hit_gallery_diagnostic"]["false_confident_results"] == 2


def test_chunked_scoring_equals_direct_without_large_float64_copy():
    rng = np.random.default_rng(5)
    features, weights, biases = rng.normal(size=(8200, 4)).astype(np.float32), rng.normal(size=(3, 4)), rng.normal(size=3)
    assert np.allclose(probe.score_heads(features, weights, biases), features.astype(np.float64) @ weights.T + biases,
                       atol=1e-14, rtol=0)
    features[-1, 1] = np.nan
    with pytest.raises(ValueError, match="nonfinite"):
        probe.score_heads(features, weights, biases)


@pytest.fixture
def cached_split(tmp_path):
    sample = {"assets": assets(1, 4)}
    sample_path = tmp_path / "sample.json"
    sample_path.write_text(json.dumps(sample), encoding="utf-8")
    images = np.zeros((4, 2048), dtype=np.float32)
    images[:, 0] = 1
    queries = np.tile(images[0], (8, 1))
    record = {"schema": "budget-embedding-run-v1", "status": "COMPLETE", "model": "qwen",
              "dtype": "fp16", "native_dimension": 2048, "settings": {"limit": 4, "batch": 1},
              "model_lock": {"revision": "a" * 40}, "packages": {"numpy": "test"},
              "decoder": {"policy": "full-rgb"}, "source_sha256": {"runtime.py": "a" * 64},
              "sample_sha256": probe.sha(sample_path.read_bytes()), "assets": sample["assets"],
              "queries": copy.deepcopy(probe.CLASS_QUERIES), "vectors_sha256": {}}
    spec_keys = ("model", "dtype", "settings", "model_lock", "packages", "decoder", "source_sha256", "native_dimension")
    identities = [{key: row[key] for key in ("asset_id", "sha256")} for row in sample["assets"]]
    record["identity"] = cache_identity({key: record[key] for key in spec_keys}, identities, record["queries"])
    for name, matrix in (("images.npy", images), ("queries.npy", queries)):
        np.save(tmp_path / name, matrix, allow_pickle=False)
        record["vectors_sha256"][name] = probe.sha((tmp_path / name).read_bytes())
    (tmp_path / "result.json").write_text(json.dumps(record), encoding="utf-8")
    return tmp_path, sample_path, record


def test_cached_embedding_identity_and_limit_is_not_space(cached_split):
    directory, sample_path, record = cached_split
    value = probe.load_split(probe.Reader(), directory, sample_path, 1024, "train")
    assert value.images.shape == (4, 1024)
    altered = copy.deepcopy(record)
    altered["settings"]["limit"] = None
    assert probe.space_spec(record, 1024) == probe.space_spec(altered, 1024)
    altered["settings"]["max_pixels"] = 100
    assert probe.space_spec(record, 1024) != probe.space_spec(altered, 1024)


@pytest.mark.parametrize("model,dimension", [("qwen", 512), ("qwen", 1024), ("fgclip-base-adaptive", 768)])
def test_only_known_mrl_512_compatibility_or_original_primary_spaces_are_accepted(cached_split, model, dimension):
    _, _, record = cached_split
    record["model"] = model
    value = probe.space_spec(record, dimension)
    assert value["dimension"] == dimension


@pytest.mark.parametrize("model,dimension", [("qwen", 768), ("qwen", 2048), ("qwen", 256),
                                               ("fgclip-base-adaptive", 512), ("unknown", 512)])
def test_unplanned_dimensions_still_refused(cached_split, model, dimension):
    _, _, record = cached_split
    record["model"] = model
    with pytest.raises(ValueError, match="planned"):
        probe.space_spec(record, dimension)


def test_512_prefix_is_normalized_and_model_space_is_distinct(cached_split):
    directory, sample_path, _ = cached_split
    data512 = probe.load_split(probe.Reader(), directory, sample_path, 512, "train")
    data1024 = probe.load_split(probe.Reader(), directory, sample_path, 1024, "train")
    assert data512.images.shape == (4, 512)
    assert np.allclose(np.linalg.norm(data512.images, axis=1), 1)
    assert data512.spec != data1024.spec


def test_512_fit_records_posthoc_status_without_changing_learning_rule():
    train, calibration = split("train"), split("calibration", 101)
    for data in (train, calibration):
        data.images = np.pad(data.images, ((0, 0), (0, 508)))
        data.query_vectors = np.pad(data.query_vectors, ((0, 0), (0, 508)))
    train.spec = calibration.spec = {"model": "qwen", "dimension": 512}
    manifest, _, _ = probe.fit_heads(train, calibration, all_labels(train), all_labels(calibration))
    assert manifest["study_kind"].startswith("POSTHOC_512_COMPATIBILITY_AFTER_1024_RESULTS")
    assert manifest["config"]["C"] == 1.0
    assert manifest["config"]["target_calibration_precision"] == 0.9
    assert manifest["config"]["minimum_calibration_predictions"] == 20


@pytest.mark.parametrize("field", ["model_lock", "decoder", "source_sha256", "packages", "queries"])
def test_cache_provenance_or_order_tampering_refused(cached_split, field):
    directory, sample_path, record = cached_split
    if field == "queries":
        record[field] = record[field][::-1]
    else:
        record[field]["changed"] = True
    (directory / "result.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        probe.load_split(probe.Reader(), directory, sample_path, 1024, "train")


def test_changed_vectors_and_source_split_refused(cached_split):
    directory, sample_path, _ = cached_split
    with pytest.raises(ValueError, match="official COCO split"):
        probe.load_split(probe.Reader(), directory, sample_path, 1024, "test")
    (directory / "images.npy").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        probe.load_split(probe.Reader(), directory, sample_path, 1024, "train")


def test_labels_identity_unknown_and_duplicate_validation(tmp_path):
    data = split("train", 1, 2)
    document = {"input_identities": [{key: row[key] for key in ("asset_id", "sha256")} for row in data.assets],
                "query_definitions": data.queries,
                "queries": {query: [{"asset_id": aid, "relevant": i == 0} for i, aid in enumerate(data.ids)]
                            for query in probe.CLASS_IDS}}
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(document))
    assert probe.load_labels(probe.Reader(), path, data, training=True)["cat"][1] is True
    document["queries"]["cat"][0]["relevant"] = None
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="boolean"):
        probe.load_labels(probe.Reader(), path, data, training=True)
    assert probe.load_labels(probe.Reader(), path, data, training=False)["cat"][1] is None
    document["queries"]["cat"][1]["asset_id"] = 1
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="membership"):
        probe.load_labels(probe.Reader(), path, data, training=False)


def test_ground_truth_boxes_require_sample_query_and_official_source_binding(tmp_path):
    data = split("test", 100, 2)
    identities = [{key: row[key] for key in ("asset_id", "sha256")} for row in data.assets]
    labels_path, boxes_path = tmp_path / "labels.json", tmp_path / "boxes.json"
    source = {"annotation_sha256": "b" * 64}
    labels_document = {"provenance": {"source_identity": source}, "input_identities": identities,
                       "query_definitions": data.queries,
                       "queries": {query: [{"asset_id": aid, "relevant": query == "cat" and aid == 100}
                                           for aid in data.ids] for query in probe.CLASS_IDS}}
    labels_path.write_text(json.dumps(labels_document))
    boxes = {"input_identities": identities, "query_definitions": data.queries, "source_identity": source,
             "assets": {"100": {"cat": {"max_box_area_fraction": 0.04}}, "101": {}}}
    boxes_path.write_text(json.dumps(boxes))
    assert probe.load_boxes(probe.Reader(), boxes_path, labels_path, data) == boxes["assets"]
    del boxes["assets"]["101"]  # All-negative random photos legitimately have no boxes entry.
    boxes_path.write_text(json.dumps(boxes))
    assert probe.load_boxes(probe.Reader(), boxes_path, labels_path, data) == boxes["assets"]
    boxes["assets"]["100"]["dog"] = {"max_box_area_fraction": 0.1}
    boxes_path.write_text(json.dumps(boxes))
    with pytest.raises(ValueError, match="negative category"):
        probe.load_boxes(probe.Reader(), boxes_path, labels_path, data)
    boxes["assets"]["100"] = {}
    boxes_path.write_text(json.dumps(boxes))
    with pytest.raises(ValueError, match="positive category"):
        probe.load_boxes(probe.Reader(), boxes_path, labels_path, data)
    boxes["assets"]["100"] = {"cat": {"max_box_area_fraction": 0.04}}
    boxes["source_identity"] = {"annotation_sha256": "c" * 64}
    boxes_path.write_text(json.dumps(boxes))
    with pytest.raises(ValueError, match="identities"):
        probe.load_boxes(probe.Reader(), boxes_path, labels_path, data)


def test_reader_detects_inputs_changed_after_computation(tmp_path):
    path = tmp_path / "input.json"
    path.write_text("{}")
    reader = probe.Reader()
    reader.json(path)
    path.write_text('{"changed":true}')
    with pytest.raises(ValueError, match="changed"):
        reader.unchanged()


def test_keyboard_interrupt_emits_failed_junit(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "controlled_output", lambda path: path)
    for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        monkeypatch.setenv(key, "1")
    monkeypatch.setattr(probe.Reader, "raw", lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
    junit = tmp_path / "interrupted.xml"
    monkeypatch.setattr(sys, "argv", ["probe", "benchmark", "--model-dir", str(tmp_path),
                                      "--matrix", "unused.npy", "--out", str(tmp_path / "out.json"),
                                      "--junit", str(junit)])
    with pytest.raises(KeyboardInterrupt):
        probe.main()
    assert ET.parse(junit).getroot().attrib["failures"] == "1"
