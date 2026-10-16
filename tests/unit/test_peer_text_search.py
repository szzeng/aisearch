"""Pin the peer-inspired approximation and keep its limitations testable."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tools.evaluation import peer_text_search as peer
from tools.evaluation import search_quality as quality


def test_immich_cjk_bigrams_preserve_names_and_punctuation():
    assert peer.immich_tokens("深圳湾公园 AB1234") == ["深圳", "圳湾", "湾公", "公园", "AB1234"]
    assert peer.immich_tokens("和田，我的世界") == ["和田", "，", "我的", "的世", "世界"]
    assert peer.immich_tokens("한글 カタカナ") == ["한글", "カタ", "タカ", "カナ"]


def test_documented_postgresql_strict_word_example():
    assert peer.strict_word_similarity("word", "two words") == pytest.approx(4 / 7)
    assert peer.strict_word_similarity("cat", "other cat here") == 1
    assert peer.strict_word_similarity("cat", "catalogue") < 1
    assert peer.strict_word_similarity("", "cat") == 0
    assert peer.strict_word_similarity("cat", "") == 0


def test_whole_word_extents_do_not_require_full_document_similarity():
    assert peer.strict_word_similarity("银行", "无关文本 银行 其他文本") == 1
    assert peer.strict_word_similarity("alpha beta", "alpha unrelated beta") < 1
    assert peer.strict_word_similarity("alpha beta", "beta alpha") == 1
    # pg_trgm sets lose order inside an extent: never claim exact phrase semantics.


def test_legacy_replay_exposes_stop_character_tradeoff():
    assert peer.legacy_match("和田").expression == '"田"'
    assert peer.legacy_match("成都的夜景").expression == '"成" AND "夜 景"'
    assert peer.legacy_match('"我的世界"').expression == '"我 的 世 界"'
    assert peer.legacy_match("深圳，海边").expression == '"深 圳 海 边"'


def test_fixed_supplement_has_complete_grouped_labels():
    data = peer.supplement()
    quality.validate_dataset(data)
    assert len(data["documents"]) == 40
    assert len(data["queries"]) == 22
    assert {q["category"] for q in data["queries"]} == {
        "natural", "name", "identifier", "cross_field", "ocr_error", "absent",
    }


def test_real_fts_and_peer_ocr_scope_remain_distinct(tmp_path):
    data = peer.supplement()
    # Keep the full declared corpus/labels; inspecting selected result rows is enough.
    result = peer.evaluate(data, tmp_path / "data")
    variants = result["variants"]
    current = {r["query_id"]: r for r in variants["current"]["queries"]}
    legacy = {r["query_id"]: r for r in variants["legacy_stopchar"]["queries"]}
    ocr_only = {r["query_id"]: r for r in variants["immich_approx_ocr_only"]["queries"]}
    assert current["name-4"]["ranked_asset_ids"] == [9]
    assert legacy["name-4"]["ranked_asset_ids"] == [10, 9]
    assert current["natural-0"]["ranked_asset_ids"] == []
    assert legacy["natural-0"]["ranked_asset_ids"] == [1]
    assert current["cross_field-12"]["ranked_asset_ids"] == [25]
    assert 25 not in ocr_only["cross_field-12"]["ranked_asset_ids"]
    assert len(variants["current"]["categories"]) == 6


def test_refuses_existing_catalog_and_invalid_labels(tmp_path):
    data = peer.supplement()
    with pytest.raises(ValueError, match="new"):
        peer.evaluate(data, tmp_path)
    data["queries"][0]["relevant"] = {"999": 3}
    with pytest.raises(ValueError, match="unknown"):
        peer.evaluate(data, tmp_path / "not-created")


def test_frozen_plan_binds_dataset_and_implementation():
    data = peer.supplement()
    first = peer.policy_plan([data])
    data["queries"][0]["text"] += "变化"
    assert peer.policy_plan([data]) != first
    assert first["threshold"] == 0.5
    assert len(first["tool_sha256"]) == 64


def test_frozen_plan_refuses_changed_dependency_before_running(tmp_path, monkeypatch):
    datasets = [quality.load_json(quality.DEFAULT_DATASET), peer.supplement()]
    plan_path, result_path = tmp_path / "plan.json", tmp_path / "result.json"
    quality.write_new(plan_path, peer.policy_plan(datasets))
    original_read = Path.read_bytes
    token_source = Path(peer.tokens.__file__).resolve()

    def changed_dependency(path):
        original = original_read(path)
        return original + b"\n# changed tokenizer implementation\n" if (
            path.resolve() == token_source
        ) else original

    monkeypatch.setattr(Path, "read_bytes", changed_dependency)
    monkeypatch.setattr(sys, "argv", ["peer-text", "--plan", str(plan_path),
                                     "--result", str(result_path)])
    with pytest.raises(SystemExit) as error:
        peer.main()
    assert error.value.code == 2
    assert not result_path.exists()
