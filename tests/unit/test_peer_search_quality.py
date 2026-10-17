"""Evidence integrity and statistical semantics for CPU peer-search comparisons."""

from __future__ import annotations

import copy
import hashlib
import sys

import numpy as np
import pytest

from tools.evaluation import peer_search_quality as peer
from tools.evaluation import public_search_quality as public


def plan():
    return {
        "schema": "peer-search-plan-v1", "variants": list(peer.VARIANTS),
        "fusion_inputs": list(peer.CHANNELS), "rrf_k": 60,
        "threshold_channels": list(peer.CHANNELS), "target_retention": .95,
        "borrowed_threshold": .175, "bootstrap_repeats": 10000,
        "seed": 20261016, "wide_aspect_ratio": 1.5,
        "zscore_fit": (
            "global mean and population standard deviation of each channel's "
            "tuning-only query-gallery score matrix"
        ),
    }


@pytest.fixture
def saved_runs(tmp_path):
    images = [
        {"id": f"image-{index}", "sha256": hashlib.sha256(str(index).encode()).hexdigest(),
         "split": "tuning" if index < 3 else "holdout", "captions": [f"照片{index}"],
         "coco": {"width": 900 if index % 2 else 500, "height": 600}}
        for index in range(6)
    ]
    data = {"schema": "public-search-quality-dataset-v1", "images": images}
    _, queries = peer.validate_dataset(data)
    dataset = tmp_path / "dataset.json"
    public.write_json(dataset, data)
    plan_path = tmp_path / "plan.json"
    public.write_json(plan_path, plan())
    rng = np.random.default_rng(7)
    clip = public.normalized(rng.normal(size=(6, 512)))
    qwen = public.normalized(rng.normal(size=(6, 2048)))
    for model in ("clip", "qwen"):
        directory = tmp_path / model
        directory.mkdir()
        if model == "clip":
            vectors = {"center": clip, "letterbox": public.normalized(clip + .01),
                       "queries": public.normalized(clip + rng.normal(0, .01, clip.shape))}
            variants = ("center", "letterbox", "two_view_max")
        else:
            vectors = {"native": qwen,
                       "queries": public.normalized(qwen + rng.normal(0, .01, qwen.shape))}
            variants = ("512", "1024", "2048")
        np.savez_compressed(directory / "vectors.npz", **vectors)
        result = {"status": "ok", "model": model, "dataset_sha256": public.sha256(dataset),
                  "model_spec": {"dimension": 512 if model == "clip" else 2048}, "variants": {}}
        for variant in variants:
            result["variants"][variant] = {}
            for split in ("tuning", "holdout"):
                indexes = [i for i, row in enumerate(images) if row["split"] == split]
                q = vectors["queries"][indexes]
                if model == "qwen":
                    n = int(variant)
                    scores = public.normalized(q[:, :n]) @ public.normalized(
                        vectors["native"][indexes, :n],
                    ).T
                elif variant == "two_view_max":
                    scores = np.maximum(q @ vectors["center"][indexes].T,
                                        q @ vectors["letterbox"][indexes].T)
                else:
                    scores = q @ vectors[variant][indexes].T
                result["variants"][variant][split] = public.ranked_metrics(
                    scores, [queries[i] for i in indexes], [images[i]["id"] for i in indexes],
                )
        public.write_json(directory / "result.json", result)
        public.write_json(directory / "execution.json", {"status": "ok", "exit_code": 0})
    return {"dataset_path": dataset, "clip_run": tmp_path / "clip",
            "qwen_run": tmp_path / "qwen", "plan_path": plan_path, "mode": "exploratory"}


def test_freeze_uses_only_tuning_then_exact_replays_both_splits(saved_runs, tmp_path):
    inputs = peer.load_inputs(**saved_runs)
    assert inputs["identities"]["runtime/python_version"] == sys.version
    assert inputs["identities"]["runtime/numpy_version"] == np.__version__
    scores, queries, images = peer.base_scores(inputs, "tuning")
    expected = peer.fit_calibration(scores, queries, images)
    altered = copy.deepcopy(inputs)
    # A change only to holdout vectors must not change fitted tuning scales/thresholds.
    altered["runs"]["clip"]["vectors"]["queries"][3:] *= -1
    altered_scores, aq, ai = peer.base_scores(altered, "tuning")
    assert peer.fit_calibration(altered_scores, aq, ai) == expected
    frozen = tmp_path / "frozen.json"
    peer.fit(inputs, frozen)
    output = tmp_path / "comparison.json"
    assert peer.evaluate(inputs, frozen, output)["status"] == "ok"
    result = peer.read_json(output)
    assert result["calibration"] == expected
    for split in ("tuning", "holdout"):
        assert result["splits"][split]["baseline_replay"] == "exact rankings matched"
        assert set(result["splits"][split]["variants"]) == set(peer.VARIANTS)
    assert result["mode"] == "exploratory"


@pytest.mark.parametrize("corruption", ["dataset", "query", "exit", "dimension"])
def test_refuses_runs_from_other_data_or_query_order(saved_runs, corruption):
    path = saved_runs["clip_run"] / ("execution.json" if corruption == "exit" else "result.json")
    value = peer.read_json(path)
    if corruption == "dataset":
        value["dataset_sha256"] = "0" * 64
    elif corruption == "query":
        value["variants"]["center"]["holdout"]["per_query"].reverse()
    elif corruption == "exit":
        value["exit_code"] = 1
    else:
        value["model_spec"]["dimension"] = 128
    public.write_json(path, value)
    with pytest.raises(ValueError):
        peer.load_inputs(**saved_runs)


@pytest.mark.parametrize("corruption", ["shape", "nan", "normalization", "extra", "dtype"])
def test_refuses_bad_vectors(saved_runs, corruption):
    path = saved_runs["clip_run"] / "vectors.npz"
    with np.load(path) as archive:
        values = dict(archive)
    if corruption == "shape":
        values["center"] = values["center"][:-1]
    elif corruption == "nan":
        values["center"][0, 0] = np.nan
    elif corruption == "normalization":
        values["center"] *= 2
    elif corruption == "extra":
        values["unexpected"] = values["center"]
    else:
        values["center"] = values["center"].astype(np.float64)
    np.savez_compressed(path, **values)
    with pytest.raises(ValueError, match="vector|shape"):
        peer.load_inputs(**saved_runs)


def test_reordered_vectors_fail_exact_baseline_replay(saved_runs, tmp_path):
    path = saved_runs["clip_run"] / "vectors.npz"
    with np.load(path) as archive:
        values = dict(archive)
    values["center"][[0, 1]] = values["center"][[1, 0]]
    np.savez_compressed(path, **values)
    inputs = peer.load_inputs(**saved_runs)
    with pytest.raises(ValueError, match="do not reproduce"):
        peer.fit(inputs, tmp_path / "frozen.json")


def test_stale_source_summary_is_detected_even_when_rankings_match(saved_runs, tmp_path):
    path = saved_runs["qwen_run"] / "result.json"
    value = peer.read_json(path)
    value["variants"]["512"]["tuning"]["recall_at_50"] = .1
    public.write_json(path, value)
    inputs = peer.load_inputs(**saved_runs)
    with pytest.raises(ValueError, match="do not reproduce"):
        peer.fit(inputs, tmp_path / "frozen.json")


def test_freeze_refuses_changed_identity_and_tampered_calibration(saved_runs, tmp_path):
    inputs = peer.load_inputs(**saved_runs)
    frozen = tmp_path / "frozen.json"
    peer.fit(inputs, frozen)
    changed = copy.deepcopy(inputs)
    changed["identities"]["tool"] = "0" * 64
    with pytest.raises(ValueError, match="identities changed"):
        peer.evaluate(changed, frozen, tmp_path / "result.json")
    value = peer.read_json(frozen)
    value["calibration"]["zscore"]["clip_center"]["mean"] += .1
    public.write_json(frozen, value)
    with pytest.raises(ValueError, match="calibration was modified"):
        peer.evaluate(inputs, frozen, tmp_path / "result.json")


def test_confirmation_requires_prior_exclusion_and_checks_id_and_hash(saved_runs, tmp_path):
    args = {**saved_runs, "mode": "confirmation"}
    with pytest.raises(ValueError, match="explicit prior"):
        peer.load_inputs(**args)
    with pytest.raises(ValueError, match="overlaps"):
        peer.load_inputs(**args, exclude_dataset=saved_runs["dataset_path"])
    prior = peer.read_json(saved_runs["dataset_path"])
    for row in prior["images"]:
        row["id"] += "-new-name-same-content"
    prior_path = tmp_path / "prior.json"
    public.write_json(prior_path, prior)
    with pytest.raises(ValueError, match="overlaps"):
        peer.load_inputs(**args, exclude_dataset=prior_path)
    for row in prior["images"]:
        row["sha256"] = hashlib.sha256(row["id"].encode()).hexdigest()
    public.write_json(prior_path, prior)
    assert peer.load_inputs(**args, exclude_dataset=prior_path)["mode"] == "confirmation"


def test_image_split_and_content_identity_refuse_leakage():
    data = {"schema": "public-search-quality-dataset-v1", "images": [
        {"id": "one", "sha256": "a" * 64, "split": "tuning", "captions": ["一"],
         "coco": {"width": 300, "height": 200}},
        {"id": "two", "sha256": "a" * 64, "split": "holdout", "captions": ["二"],
         "coco": {"width": 300, "height": 200}},
    ]}
    with pytest.raises(ValueError, match="content hash"):
        peer.validate_dataset(data)


def test_caption_string_is_not_silently_split_into_character_queries(saved_runs):
    data = peer.read_json(saved_runs["dataset_path"])
    data["images"][0]["captions"] = "不能把一句描述误拆成每个字"
    with pytest.raises(ValueError, match="nonempty captions"):
        peer.validate_dataset(data)


def test_threshold_retains_at_least_target_fraction_with_boundary_and_ties():
    values = np.arange(100) / 100
    assert peer.retained_threshold(values) == .05
    assert (values >= peer.retained_threshold(values)).mean() == .95
    assert peer.retained_threshold(np.ones(63)) == 1
    assert peer.retained_threshold(np.array([.1, .2, .3])) == .1
    with pytest.raises(ValueError):
        peer.retained_threshold(np.array([np.nan]))
    queries = [{"image_id": "a"}, {"image_id": "b"}]
    result = peer.threshold_diagnostic(np.array([[.1, .9], [.8, .3]]), queries,
                                       [{"id": "a"}, {"id": "b"}], .2)
    assert result["known_pair_target_retention"] == .5
    assert result["candidate_count_mean"] == 1.5
    assert "precision" not in set(result)


def test_rrf_uses_ranks_and_zscore_uses_frozen_tuning_scale():
    scores = {"clip_center": np.array([[.1, .8, .5]]),
              "qwen512": np.array([[10., 8., 3.]])}
    calibration = {"zscore": {"clip_center": {"mean": .4, "std": .2},
                               "qwen512": {"mean": 4., "std": 2.}}}
    fused = peer.add_fusions(scores, calibration)
    assert fused["equal_rrf"][0].tolist() == pytest.approx([
        1 / 63 + 1 / 61, 1 / 61 + 1 / 62, 1 / 62 + 1 / 63,
    ])
    assert fused["equal_zscore"][0].tolist() == pytest.approx([.75, 2., 0.])
    assert peer.stable_ranks(np.ones((1, 3))).tolist() == [[1, 2, 3]]


def test_bootstrap_keeps_captions_grouped_and_reports_regressions():
    before = [{"id": "a#0", "image_id": "a", "rank": 2},
              {"id": "a#1", "image_id": "a", "rank": 3}]
    after = [{**row, "rank": 1} for row in before]
    result = peer.paired_bootstrap(before, after, repeats=1000)
    assert result["image_groups"] == 1
    assert result["top1_95_percent_interval_pp"] == [100., 100.]
    assert result["gained_top1_queries"] == 2
    reversed_result = peer.paired_bootstrap(after, before, repeats=1000)
    assert reversed_result["lost_top1_queries"] == 2
    with pytest.raises(ValueError, match="identity"):
        peer.paired_bootstrap(before, list(reversed(after)), repeats=10)


def test_plan_rejects_parameter_sweep_and_output_is_never_overwritten(saved_runs, tmp_path):
    changed = plan()
    changed["rrf_k"] = 20
    with pytest.raises(ValueError, match="predeclared"):
        peer.validate_plan(changed)
    inputs = peer.load_inputs(**saved_runs)
    output = tmp_path / "frozen.json"
    peer.fit(inputs, output)
    original = output.read_bytes()
    with pytest.raises(FileExistsError):
        peer.fit(inputs, output)
    assert output.read_bytes() == original


def test_cli_preserves_existing_evidence_without_loading_inputs(tmp_path, monkeypatch, capsys):
    output = tmp_path / "evidence.json"
    output.write_text("old evidence", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "peer_search_quality.py", "fit", "--dataset", "missing", "--clip-run", "missing",
        "--qwen-run", "missing", "--plan", "missing", "--mode", "exploratory",
        "--output", str(output),
    ])
    assert peer.main() == 1
    assert output.read_text(encoding="utf-8") == "old evidence"
    assert "preserve existing evidence" in capsys.readouterr().err
