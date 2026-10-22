"""Judgment, frozen mapping, independent selection and rank-fusion boundaries."""

import copy
import io
import json

import numpy as np
import pytest

from tools.evaluation import album_tag_fusion as fusion
from tools.evaluation.prepare_broad_album_data import Inputs, digest


def test_unknown_never_becomes_negative_or_inflates_known_precision():
    truth = np.array([-1, -1, 1, 0, 1])
    result = fusion.measured(np.arange(5), truth)
    assert result["judged_ap"] == pytest.approx((1 + 2 / 3) / 2)
    assert result["known_precision_at_20"] == pytest.approx(2 / 3)
    assert result["unknown_fraction_at_20"] == .4
    assert result["positive"] == 2 and result["negative"] == 1
    positive_only = fusion.measured(np.arange(3), np.array([-1, 1, 1]))
    assert positive_only["judged_ap"] is positive_only["known_precision_at_20"] is None
    assert fusion.measured(np.arange(2), np.array([-1, -1]))["known_precision_at_20"] is None


def test_actual_top20_unknown_coverage_and_positive_only_recall_are_separate():
    truth = np.array([-1] * 20 + [1] * 10)
    values = fusion.measured(np.arange(30), truth, truth == 1)
    assert values["recall_at_20"] == values["small_recall_at_20"] == 0
    assert values["recall_at_50"] == values["small_recall_at_50"] == 1
    assert values["unknown_fraction_at_20"] == 1
    assert values["judged_ap"] is None and values["negative_retrieval_fraction_at_20"] is None


@pytest.mark.parametrize("order,truth", [(np.array([0, 0]), np.array([1, 0])),
    (np.array([0]), np.array([1, 0])), (np.array([0]), np.array([2]))])
def test_missing_duplicate_and_invalid_judgments_refused(order, truth):
    with pytest.raises(ValueError, match="population"):
        fusion.measured(order, truth)


def test_light_subtypes_sum_within_view_before_max_and_multiconcept_min():
    # A and B are mutually exclusive subtypes: two views must not be mixed.
    probabilities = np.array([[[.6, .1, .3], [.1, .7, .2]], [[.2, .2, .6], [.3, .3, .4]]])
    assert fusion.concept_scores(probabilities, [{"indices": [0, 1]}], "light") == pytest.approx([.8, .6])
    assert fusion.concept_scores(probabilities, [{"indices": [0, 1]}, {"indices": [2]}], "light") == pytest.approx([.3, .6])
    assert fusion.concept_scores(probabilities, [{"indices": []}], "light") is None
    with pytest.raises(ValueError, match="class index"):
        fusion.concept_scores(probabilities, [{"indices": [0, 0]}], "light")


def test_ram_sigmoid_synonyms_max_without_softmax_or_probability_claim():
    scores = np.array([[.8, .7, .9], [.4, .5, .2]])
    assert fusion.concept_scores(scores, [{"indices": [0, 1]}, {"indices": [2]}], "ram") == pytest.approx([.8, .2])


def test_frozen_mapping_does_not_infer_people_from_clothing_or_conflate_crane():
    names = ["neck brace", "persian cat", "hyena", "labrador retriever dog", "crane", "crane"]
    rules = [{"label": name} for name in ["portrait", "cat", "dog", "dog", "bird", "construction"]]
    assert fusion.indices_for("portrait", "light", names, rules) == []
    assert fusion.indices_for("dog", "light", names, rules) == [3]
    assert fusion.indices_for("cat", "light", names, rules) == [1]
    assert fusion.indices_for("crane", "light", names, rules) == []
    assert fusion.indices_for("bird", "light", names, rules) == []


def test_identity_time_queries_fallback_and_compound_nouns_remain_specific():
    assert fusion.photo_concepts("me, Xue'er and Xiaoming photos in August 2023") == []
    assert fusion.photo_concepts("Nandaihe cable car") == ["cable car"]
    assert fusion.photo_concepts("bottled water stacked in warehouse") == ["water bottle", "warehouse"]
    assert fusion.photo_concepts("picking tomatoes") == ["tomato"]


def test_unmapped_or_constant_tags_preserve_baseline_exactly():
    baseline = np.array([.2, .5, .3, .5])
    for method in fusion.METHODS:
        assert fusion.fused_order(baseline, None, method).tolist() == [1, 3, 2, 0]
        assert fusion.fused_order(baseline, np.ones(4), method).tolist() == [1, 3, 2, 0]


def test_rrf_is_bounded_complete_and_relevance_direction_correct():
    baseline = np.array([1., .5, 0.])
    tags = np.array([0., .5, 1.])
    assert fusion.fused_order(baseline, tags, "tags_only").tolist() == [2, 1, 0]
    assert fusion.fused_order(baseline, tags, "rrf_equal").tolist() == [0, 2, 1]
    assert fusion.fused_order(baseline, tags, "rrf_tag025").tolist() == [0, 1, 2]
    for method in fusion.METHODS:
        assert sorted(fusion.fused_order(baseline, tags, method)) == [0, 1, 2]


def metric_row(group, language, family, values):
    return {"group_id": group, "language": language, "family": family,
            "metrics": {method: {"judged_ap": values.get(method, .5)} for method in fusion.METHODS}}


def test_selection_uses_only_supplied_calibration_and_equal_family_weight():
    rows = [metric_row(str(i), "en", "Body", {"rrf_tag025": .9, "rrf_equal": .8}) for i in range(30)]
    rows += [metric_row("dog", "en", "Animal", {"rrf_tag025": .0, "rrf_equal": .6})]
    result = fusion.select_method(rows)
    assert result["chosen_method"] == "rrf_equal"
    assert result["calibration_family_balanced_ap"]["rrf_tag025"] == pytest.approx(.45)
    assert fusion.select_method([metric_row("x", "cn", "Other", {"tags_only": 1.0})])["chosen_method"] == "baseline"
    assert fusion.select_method([])["chosen_method"] == "baseline"


def test_bilingual_pairs_count_once_and_bootstrap_is_paired_reproducible():
    rows = [metric_row("cat", "en", "Animal", {"rrf_tag025": .9}),
            metric_row("cat", "cn", "Animal", {"rrf_tag025": .7}),
            metric_row("dog", "en", "Animal", {"rrf_tag025": .3})]
    summary = fusion.paired_summary(rows, "rrf_tag025", "judged_ap")
    assert summary["groups"] == 2 and summary["delta"] == pytest.approx(.05)
    assert summary["regressed_groups"] == summary["improved_groups"] == 1
    assert summary == fusion.paired_summary(rows, "rrf_tag025", "judged_ap")


def test_fewer_known_negative_hits_is_not_reported_as_regression():
    rows = [{"group_id": "cat", "family": "Animal", "metrics": {
        "baseline": {"negative_retrieval_fraction_at_20": .5},
        "rrf_equal": {"negative_retrieval_fraction_at_20": .2}}}]
    result = fusion.paired_summary(rows, "rrf_equal", "negative_retrieval_fraction_at_20")
    assert result["delta"] == pytest.approx(-.3)
    assert result["preferred_direction"] == "lower" and result["improved_groups"] == 1 and result["regressed_groups"] == 0


def test_small_subject_subset_requires_positive_boxes_and_excludes_large_subject():
    data = {"dataset": "coco480", "assets": [{"asset_id": i} for i in range(3)],
        "queries": [{"query_id": "cat", "positive_asset_ids": [0, 1], "negative_asset_ids": []}],
        "judgments": [{"class_id": "cat", "asset_id": 0, "instances": [{"iscrowd": 0, "box_area_fraction": .01}]},
                      {"class_id": "cat", "asset_id": 1, "instances": [{"iscrowd": 0, "box_area_fraction": .01},
                                                                                  {"iscrowd": 0, "box_area_fraction": .5}]}]}
    truth, small, _, _ = fusion.truths(data, {"source_query_id": "cat"})
    assert truth.tolist() == [1, 1, -1] and small.tolist() == [True, False, False]


def test_array_checksum_nonfinite_and_missing_compatibility_refused(tmp_path, monkeypatch):
    stream = io.BytesIO()
    np.save(stream, np.array([[float("nan")]]))
    path = tmp_path / "array.npy"
    path.write_bytes(stream.getvalue())
    reader = Inputs(tmp_path)
    with pytest.raises(ValueError, match="checksum"):
        fusion.array(reader, path, "0" * 64)
    with pytest.raises(ValueError, match="values"):
        fusion.array(reader, path, digest(stream.getvalue()))
    monkeypatch.setattr(fusion, "space_spec", lambda record, dim: {"model": "fixture"})
    proof = {"schema": "category-head-compatibility-v1", "status": "PASS", "dimension": 512,
        "decoded_rgb_equal": True, "image_comparison": {"passed": True, "max_abs_error": 0.},
        "text_max_abs_error": 0., "source": {"space_spec": {"model": "fixture"}}, "space_id": "safe", "processor_id": "safe"}
    fusion.verify_compatibility(proof, {}, {"space_id": "safe", "processor_id": "safe"})
    for change in ({"status": "FAIL"}, {"decoded_rgb_equal": False}, {"text_max_abs_error": float("nan")}):
        with pytest.raises(ValueError, match="compatibility"):
            fusion.verify_compatibility({**proof, **change}, {})
    with pytest.raises(ValueError, match="space differs"):
        fusion.verify_compatibility(proof, {}, {"space_id": "wrong", "processor_id": "safe"})


def test_frozen_policy_cannot_be_overwritten(tmp_path):
    plan = tmp_path / "plan.json"
    policy = fusion.frozen_policy(plan)
    raw = policy.read_bytes()
    assert fusion.frozen_policy(plan).read_bytes() == raw
    changed = copy.deepcopy(fusion.EVALUATION_POLICY)
    changed["selection"] = "choose on test"
    policy.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="policy differs"):
        fusion.frozen_policy(plan)


def test_light_view_counts_are_bound_to_their_actual_asset_rows(tmp_path):
    assets = [{"asset_id": name, "sha256": name * 64} for name in "ab"]
    plan = {"datasets": {"fixture": {"manifest_sha256": "f" * 64}}, "vocabularies": {"light": ["one", "two"]}}
    record = {"schema": "album-tagging-run-v1", "manifest_sha256": "f" * 64, "assets": assets,
        "classes": ["one", "two"], "class_ids": [0, 1],
        "model_lock": {"photoprism_commit": fusion.COMMIT,
            "files": {name: {"url": url, "sha256": sha, "bytes": size} for name, (url, sha, size) in fusion.FILES.items()},
            "source_checkpoint_revision": "1c56a76355000c79568559d34ba3fa24416b5107"},
        "preprocess": fusion.PREPROCESS, "smoke_limit": 0, "score_type": "probabilities",
        "per_image_ms": [{"asset_id": "b", "view_count": 2}, {"asset_id": "a", "view_count": 1}]}
    for filename, matrix, key in [("scores.npy", np.full((2, 2), .5), "scores_sha256"),
                                   ("view_logits.npy", np.ones((2, 2, 2)), "view_logits_sha256")]:
        stream = io.BytesIO()
        np.save(stream, matrix)
        (tmp_path / filename).write_bytes(stream.getvalue())
        record[key] = digest(stream.getvalue())
    (tmp_path / "result.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="view-count rows"):
        fusion.tag_inputs(Inputs(tmp_path), tmp_path, "light", plan, {"dataset": "fixture", "assets": assets})
    for change in ({"smoke_limit": 2}, {"preprocess": {}}, {"model_lock": {}}, {"score_type": "logits"}):
        with pytest.raises(ValueError, match="lock differs"):
            fusion.verify_tagger_identity({**record, **change}, "light")


def test_ram_checkpoint_revision_cannot_be_substituted_under_same_vocabulary():
    expected = {"model": {"revision": "pinned", "sha256": "a" * 64}, "source": {"revision": "original"}}
    record = {"model_lock": expected, "score_type": "sigmoid_scores", "settings": {"image_size": 384, "limit": None}}
    fusion.verify_tagger_identity(record, "ram", expected)
    for altered in ({**expected, "source": {"revision": "new"}},
                    {**expected, "model": {"revision": "different", "sha256": "b" * 64}}):
        with pytest.raises(ValueError, match="lock differs"):
            fusion.verify_tagger_identity({**record, "model_lock": altered}, "ram", expected)


def test_any_changed_input_or_source_prevents_final_identity_claim(tmp_path):
    source = tmp_path / "algorithm.py"
    source.write_bytes(b"original algorithm")
    reader = Inputs(tmp_path)
    reader.raw(source)
    source.write_bytes(b"different algorithm")
    with pytest.raises(ValueError, match="changed during"):
        reader.unchanged()
