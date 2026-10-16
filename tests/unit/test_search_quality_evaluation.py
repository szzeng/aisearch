"""Quality numbers must come from the real retrieval path and honest split accounting."""

from __future__ import annotations

import copy
import math

import pytest

from tools.evaluation import search_quality as quality


@pytest.fixture
def corpus() -> dict:
    return {
        "schema_version": 1, "name": "unit-metadata", "kind": "SYNTHETIC",
        "provenance": "synthetic unit cases; no photos", "annotation_rules": "complete",
        "documents": [
            {"asset_id": 1, "filename_text": "receipt.jpg", "ocr_text": "招商银行 AB1234"},
            {"asset_id": 2, "filename_text": "coast.jpg", "place_text": "深圳湾公园"},
            {"asset_id": 3, "filename_text": "near.jpg", "ocr_text": "招商 物业 银行"},
        ],
        "queries": [
            {"query_id": "bank", "text": "招商银行", "family": "bank", "split": "tuning",
             "judgments_complete": True, "relevant": {"1": 3}, "target_ids": [1]},
            {"query_id": "missing", "text": "不存在的号码ZX9988", "family": "missing",
             "split": "tuning", "judgments_complete": True, "relevant": {},
             "no_relevant": True},
            {"query_id": "coast", "text": "深圳湾公园", "family": "coast", "split": "holdout",
             "judgments_complete": True, "relevant": {"2": 3}, "target_ids": [2]},
        ],
    }


def external_rankings(corpus: dict) -> dict:
    return {
        "dataset_sha256": quality.digest(corpus), "kind": "SYNTHETIC_REPLAY",
        "model_id": "NOT_A_MODEL", "model_revision": "unit-test-only",
        "preprocessing": "none", "producer": "unit test", "judgments_used": False,
        "rankings": {"bank": [3, 1, 2], "missing": [2, 3, 1], "coast": [2, 1, 3]},
    }


def test_real_sqlite_fts_and_production_fusion_are_used(corpus, tmp_path, monkeypatch):
    calls = {"fts": [], "fusion": []}
    actual_fts = quality.retrieval.fts_channel
    actual_fusion = quality.fusion.reciprocal_rank_fusion

    def fts_spy(catalog, match, scope, **kwargs):
        calls["fts"].append(match.raw)
        # The schema is migrated and the external-content index has real postings.
        assert catalog.read_one("SELECT COUNT(*) AS n FROM search_documents")["n"] == 3
        return actual_fts(catalog, match, scope, **kwargs)

    def fusion_spy(channels, **kwargs):
        calls["fusion"].append(copy.deepcopy(channels))
        return actual_fusion(channels, **kwargs)

    monkeypatch.setattr(quality.retrieval, "fts_channel", fts_spy)
    monkeypatch.setattr(quality.fusion, "reciprocal_rank_fusion", fusion_spy)
    rankings = external_rankings(corpus)
    result = quality.evaluate(
        corpus, split="tuning", work_dir=tmp_path / "isolated-data",
        config=quality.make_config(corpus, rankings), rankings=rankings,
    )
    assert calls["fts"] == ["招商银行", "不存在的号码ZX9988"]
    assert calls["fusion"] == [
        {"fts": [1], "vector": [3, 1, 2]}, {"fts": [], "vector": [2, 3, 1]},
    ]
    assert result["queries"][0]["ranked_asset_ids"] == [1, 3, 2]
    assert result["queries"][0]["fts_scores"][0]["score"] < 0
    assert result["metrics"]["false_positive_query_rate"] == 1
    assert result["vector_rankings"]["kind"] == "SYNTHETIC_REPLAY"
    assert result["dataset_kind"] == "SYNTHETIC"


def test_holdout_evaluates_only_holdout_queries(corpus, tmp_path, monkeypatch):
    seen = []
    build = quality.tokens.build_match

    def only_holdout(text):
        seen.append(text)
        assert text == "深圳湾公园"
        return build(text)

    monkeypatch.setattr(quality.tokens, "build_match", only_holdout)
    result = quality.evaluate(
        corpus, split="holdout", work_dir=tmp_path / "isolated",
        config=quality.make_config(corpus),
    )
    assert seen == ["深圳湾公园"]
    assert result["metrics"]["queries"] == 1
    assert result["metrics"]["no_relevant_queries"] == 0
    assert result["metrics"]["false_positive_query_rate"] is None
    assert result["queries"][0]["ranked_asset_ids"] == [2]


def test_metric_denominators_missing_targets_and_negatives_stay_separate(corpus):
    positive = quality.score_ranking(corpus["queries"][0], [3, 1])
    assert positive["precision_at_20"] == 1 / 20
    assert positive["returned_precision_at_20"] == 1 / 2
    assert positive["ndcg_at_20"] == pytest.approx(1 / math.log2(3))
    assert positive["recall_at_50"] == 1
    assert positive["best_rank"] == 2
    assert positive["target_hit_at_50"] is True
    missing_target = quality.score_ranking(corpus["queries"][0], [])
    assert missing_target["ndcg_at_20"] == 0
    assert missing_target["recall_at_50"] == 0
    assert missing_target["best_rank"] is None
    negative = quality.score_ranking(corpus["queries"][1], [])
    assert "ndcg_at_20" not in negative
    summary = quality.summarise([positive, negative])
    assert summary["ndcg_at_20_mean"] == round(positive["ndcg_at_20"], 6)
    assert summary["false_positive_query_rate"] == 0
    assert summary["positive_queries"] == summary["no_relevant_queries"] == 1


def test_duplicate_ranks_cannot_inflate_scores(corpus):
    with pytest.raises(ValueError, match="duplicate"):
        quality.score_ranking(corpus["queries"][0], [1, 1])


def test_rejected_no_result_queries_are_not_successful_abstentions(corpus):
    rejected = {**quality.score_ranking(corpus["queries"][1], []),
                "query_error": "VALIDATION_ERROR"}
    summary = quality.summarise([rejected])
    assert summary["no_relevant_queries"] == summary["rejected_queries"] == 1
    assert summary["accepted_no_relevant_queries"] == 0
    assert summary["false_positive_query_rate"] is None


@pytest.mark.parametrize("field,value,error", [
    ("family", "bank", "leaks"),
    ("text", "招商银行", "leaks"),
    ("judgments_complete", False, "partial"),
    ("relevant", {"99": 3}, "unknown"),
    ("relevant", {"2": 0}, "grades"),
    ("relevant", {}, "no_relevant"),
    ("target_ids", [3], "targets"),
])
def test_bad_labels_and_leaking_query_families_are_rejected(corpus, field, value, error):
    corpus["queries"][-1][field] = value
    with pytest.raises(ValueError, match=error):
        quality.validate_dataset(corpus)


@pytest.mark.parametrize("mutation,error", [
    ({"judgments_used": True}, "without relevance"),
    ({"dataset_sha256": "different"}, "different dataset"),
    ({"model_revision": ""}, "model_revision"),
    ({"kind": "unspecified"}, "kind"),
    ({"rankings": {"bank": [1]}}, "every query"),
    ({"rankings": {"bank": [1, 1], "missing": [], "coast": []}}, "duplicate"),
    ({"rankings": {"bank": [99], "missing": [], "coast": []}}, "unknown"),
])
def test_external_rankings_need_identity_independence_and_complete_coverage(corpus, mutation, error):
    rankings = external_rankings(corpus)
    rankings.update(mutation)
    with pytest.raises(ValueError, match=error):
        quality.validate_rankings(corpus, rankings)


def test_frozen_config_refuses_changed_corpus_or_model_run(corpus, tmp_path):
    config = quality.make_config(corpus)
    corpus["documents"][0]["ocr_text"] += " changed"
    with pytest.raises(ValueError, match="frozen"):
        quality.evaluate(corpus, split="holdout", config=config, work_dir=tmp_path / "fresh")
    assert not (tmp_path / "fresh").exists()


def test_existing_data_directory_is_never_opened(corpus, tmp_path):
    marker = tmp_path / "untouched.txt"
    marker.write_text("real directories must not be touched", encoding="utf-8")
    with pytest.raises(ValueError, match="refusing"):
        quality.evaluate(corpus, split="tuning", config=quality.make_config(corpus),
                         work_dir=tmp_path)
    assert marker.read_text(encoding="utf-8") == "real directories must not be touched"
    assert not (tmp_path / "catalog").exists()


def test_bundled_fixture_has_explicit_synthetic_complete_labels_and_both_splits():
    dataset = quality.load_json(quality.DEFAULT_DATASET)
    quality.validate_dataset(dataset)
    assert dataset["kind"] == "SYNTHETIC"
    assert len(dataset["documents"]) > 50
    for split in ("tuning", "holdout"):
        queries = [row for row in dataset["queries"] if row["split"] == split]
        assert len(queries) >= 10
        assert any(query["no_relevant"] for query in queries)
        assert any('"' in query["text"] for query in queries)


def test_cli_holdout_requires_frozen_config_and_cannot_override_it(monkeypatch, tmp_path):
    import sys

    base = ["search_quality.py", "--split", "holdout", "--result", str(tmp_path / "out.json")]
    for extra in ([], ["--config", "fake.json", "--fts-weight", "2"]):
        monkeypatch.setattr(sys, "argv", base + extra)
        with pytest.raises(SystemExit) as error:
            quality.main()
        assert error.value.code == 2
    assert not (tmp_path / "out.json").exists()


def test_evidence_files_are_never_overwritten(tmp_path):
    evidence = tmp_path / "report.json"
    quality.write_new(evidence, {"first": True})
    with pytest.raises(FileExistsError):
        quality.write_new(evidence, {"first": False})
    assert quality.load_json(evidence) == {"first": True}


@pytest.mark.parametrize("kwargs", [{"fts_weight": 0}, {"vector_weight": float("nan")},
                                    {"top_k": 20}, {"top_k": 5001}])
def test_invalid_fusion_settings_are_rejected(corpus, kwargs):
    with pytest.raises(ValueError):
        quality.make_config(corpus, **kwargs)
