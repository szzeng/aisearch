"""Sparse-policy boundary tests; quality numbers come from separate frozen data."""

import json

import numpy as np
import pytest

from tools.evaluation import album_tag_product_policy as policy
from tools.evaluation.prepare_broad_album_data import Inputs, digest


def test_exact_names_reject_ambiguous_translation_and_partial_human_alias():
    names = policy.exact_names(["cat", "arm", "crane", "crane", "mobile phone"], ["猫", "手臂", "鹤", "起重机", "手机"])
    assert policy.route("  CAT  ", names) == policy.route(" 猫 ", names) == 0
    assert policy.route("mobile   phone", names) == 4
    assert policy.route("crane", names) is None
    for text in ("cat in garden", "人手臂", "human arm", "照片里的猫", "手机拍的猫", "cats"):
        assert policy.route(text, names) is None
    assert policy.route("cat", names, has_image=True) is None
    assert policy.route("cat", names, mode="text-only") is None
    with pytest.raises(ValueError, match="lengths"):
        policy.exact_names(["cat"], [])


def test_cross_language_ambiguity_is_also_refused():
    names = policy.exact_names(["one", "two"], ["two", "二"])
    assert policy.route("two", names) is None
    assert policy.route("二", names) == 1


def test_top32_retains_original_order_quantizes_and_encodes_pre_rounding_flags():
    scores = np.array([[.5001, .5, .4999] + [.1] * 37], dtype=np.float32)
    packed = policy.sparsify(scores, np.full(40, .5, dtype=np.float32), "top32_fp16")
    assert packed.indices.dtype == np.uint16 and packed.values.dtype == np.float16
    assert packed.indices[0].tolist() == list(range(32))
    assert packed.values[0, 0] == packed.values[0, 1] == packed.values[0, 2] == .5
    assert packed.threshold_bits.tolist() == [1]  # Compare before fp16 rounding.
    assert packed.counts.tolist() == [32]
    assert packed.column(39).tolist() == [0.]
    assert packed.column(0).tolist() == [.5]
    assert packed.storage()["compact_payload_bytes"] == 133
    assert packed.storage()["estimated_300000_payload_bytes"] == 39900000


def test_official_threshold_is_strict_per_class_and_has_no_forced_label():
    scores = np.array([[.6, .5, .4], [.4, .3, .2]], dtype=np.float32)
    packed = policy.sparsify(scores, np.array([.5, .5, .3]), "official_threshold_top32_fp16")
    assert packed.counts.tolist() == [2, 0]
    assert packed.indices[0, :2].tolist() == [0, 2]
    assert packed.threshold_bits.tolist() == [3, 0]
    assert (packed.indices[1] == 65535).all()
    assert packed.column(1).tolist() == [0., 0.]
    assert packed.storage()["compact_payload_bytes"] == 11


def test_threshold_only_keeps_strongest32_and_ties_resolve_by_class_id():
    packed = policy.sparsify(np.full((2, 40), .6), np.full(40, .1), "official_threshold_top32_fp16")
    assert packed.counts.tolist() == [32, 32]
    assert packed.indices[0].tolist() == list(range(32))
    assert packed.threshold_bits.tolist() == [2**32 - 1, 2**32 - 1]


@pytest.mark.parametrize("scores,thresholds", [(np.array([[np.nan]]), np.array([.5])),
    (np.array([[1.1]]), np.array([.5])), (np.array([[.5]]), np.array([np.inf])),
    (np.array([[.5]]), np.array([.2, .5])), (np.ones((1, 0)), np.ones(0))])
def test_invalid_sparse_inputs_refused(scores, thresholds):
    with pytest.raises(ValueError, match="invalid"):
        policy.sparsify(scores, thresholds, "top32_fp16")


def test_missing_scores_and_complex_queries_preserve_exact_baseline():
    baseline = np.array([.5, .1, .9, .5])
    for method in policy.METHODS:
        assert policy.order(baseline, None, method).tolist() == [2, 0, 3, 1]
        assert policy.order(baseline, np.zeros(4), method).tolist() == [2, 0, 3, 1]
    # All missing slots share the same midrank; file order is not extra evidence.
    tags = np.array([0., 0., 1., 0.])
    permutation = np.array([1, 3, 2, 0])
    original = policy.order(baseline, tags, "rrf_equal")
    remapped = permutation[policy.order(baseline[permutation], tags[permutation], "rrf_equal")]
    # The only tied baseline pair can reverse; their set and score groups remain.
    assert original[0] == remapped[0] == 2
    assert original[-1] == remapped[-1] == 1


def test_fixed_weight_rrf_matches_independent_float64_midrank_oracle():
    from scipy.stats import rankdata

    baseline = np.array([.7, .6, .4, .2], dtype=np.float32)
    tags = np.array([0., .8, 0., 1.], dtype=np.float16)
    for method, weight in policy.METHODS.items():
        base_rank = rankdata(-baseline, method="average").astype(np.float64)
        tag_rank = rankdata(-tags, method="average").astype(np.float64)
        mixed = 1 / (60 + base_rank) + weight / (60 + tag_rank)
        expected = sorted(range(4), key=lambda i: (-mixed[i], -float(baseline[i]), i))
        assert policy.order(baseline, tags, method).tolist() == expected


def test_result_cap_only_truncates_final_order_never_filters_label_misses():
    baseline = np.linspace(1, 0, 6001)
    tags = np.zeros(6001)
    tags[-1] = 1
    full = policy.order(baseline, tags, "rrf_equal")
    capped = policy.order(baseline, tags, "rrf_equal", limit=5000)
    assert np.array_equal(capped, full[:5000]) and len(set(capped)) == 5000
    assert 6000 in capped
    for limit in (0, 5001):
        with pytest.raises(ValueError, match="bound"):
            policy.order(baseline, tags, "baseline", limit=limit)
    with pytest.raises(ValueError, match="auxiliary"):
        policy.order(np.ones(2), np.ones(3), "baseline")


def test_policy_and_input_identity_changes_are_refused(tmp_path):
    path = tmp_path / "scores.txt"
    path.write_text("frozen", encoding="utf-8")
    record = {**policy.POLICY, "input_sha256": {"scores.txt": digest(path.read_bytes())}}
    frozen = tmp_path / "policy.json"
    frozen.write_text(json.dumps(record), encoding="utf-8")
    assert policy.verify_policy(Inputs(tmp_path), frozen) == record
    record["top_k"] = 64
    frozen.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        policy.verify_policy(Inputs(tmp_path), frozen)
    record["top_k"] = 32
    frozen.write_text(json.dumps(record), encoding="utf-8")
    path.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="input changed"):
        policy.verify_policy(Inputs(tmp_path), frozen)
    with pytest.raises(ValueError, match="exists"):
        policy.freeze(frozen, tmp_path)


@pytest.mark.parametrize("bad", [None, "model_id", "manifest_sha256", "assets", "classes", "scores_sha256"])
def test_production_scores_require_exact_model_content_and_vocabulary(tmp_path, bad):
    from types import SimpleNamespace

    from photo_search.tagging.model import TagClass

    classes = (TagClass(0, "cat", "猫", .5), TagClass(1, "dog", "狗", .5))
    model = SimpleNamespace(model_id="m", manifest_sha256="a" * 64, classes=classes)
    assets = [{"asset_id": "1", "sha256": "b" * 64}]
    values = np.array([[.25, .75]], dtype=np.float32)
    np.save(tmp_path / "scores.npy", values)
    record = {"schema": "ram-tagger-production-run-v1", "model_id": "m", "manifest_sha256": "a" * 64,
              "assets": assets, "classes": [vars(item) for item in classes], "score_type": "sigmoid_scores",
              "scores_sha256": digest((tmp_path / "scores.npy").read_bytes())}
    if bad:
        record[bad] = [] if bad in {"assets", "classes"} else "changed"
    (tmp_path / "result.json").write_text(json.dumps(record), encoding="utf-8")
    if bad:
        with pytest.raises(ValueError):
            policy.production_scores(Inputs(tmp_path), tmp_path, {"assets": assets}, model)
    else:
        assert np.array_equal(policy.production_scores(Inputs(tmp_path), tmp_path, {"assets": assets}, model), values)


def test_production_evaluation_refuses_dependency_change_during_run(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from photo_search.tagging import model as models

    source = tmp_path / "tools/evaluation/album_tag_product_policy.py"
    files = [source, *[tmp_path / "tools/evaluation" / name for name in (
        "album_tag_fusion.py", "prepare_broad_album_data.py", "album_linear_probe.py", "album_light_tagger.py")],
        *[tmp_path / "src/photo_search/tagging" / name for name in ("policy.py", "model.py", "runtime.py")]]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original", encoding="utf-8")
    monkeypatch.setattr(policy, "ROOT", tmp_path)
    monkeypatch.setattr(policy, "__file__", str(source))
    monkeypatch.setattr(policy, "verify_policy", lambda *args: {})
    data = tmp_path / policy.BASE
    (data / "broad-tags-fusion-r2").mkdir(parents=True)
    (data / "compatibility-final").mkdir(parents=True)
    (data / "broad-tags-fusion-r2/plan.json").write_text('{"vocabularies":{"ram":[]}}')
    (data / "compatibility-final/compatibility.json").write_text("{}")
    monkeypatch.setattr(models, "read_model_directory", lambda *args: SimpleNamespace(
        classes=(), model_id="m", verify=lambda: None))
    def mutate(*args, **kwargs):
        files[-1].write_text("changed during measurement", encoding="utf-8")
        return [], {}
    monkeypatch.setattr(policy, "evaluate_dataset", mutate)
    with pytest.raises(ValueError, match="input changed during"):
        policy.verify_production(tmp_path / "policy.json", tmp_path / "output", tmp_path / "runs",
                                 tmp_path / "package", root=tmp_path)
    assert not (tmp_path / "output/result.json").exists()
