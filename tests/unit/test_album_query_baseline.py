import json

import numpy as np
import pytest

from tools.evaluation import album_query_baseline as baseline


def test_query_identity_order_and_language_are_not_normalized_away(tmp_path, monkeypatch):
    monkeypatch.setattr(baseline, "ROOT", tmp_path)
    rows = [{"query_id": "cat-zh", "text": "猫"}, {"query_id": "cat-en", "text": "cat"}]
    path = tmp_path / "queries.json"
    path.write_text(json.dumps({"schema": "broad-album-queries-v1", "queries": rows}), encoding="utf-8")
    assert baseline.read_queries(path) == rows
    rows.append(rows[0])
    path.write_text(json.dumps({"schema": "broad-album-queries-v1", "queries": rows}), encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        baseline.read_queries(path)


@pytest.mark.parametrize("text", ["", " ", None, "a" * 257])
def test_invalid_queries_fail_before_loading_model(tmp_path, monkeypatch, text):
    monkeypatch.setattr(baseline, "ROOT", tmp_path)
    path = tmp_path / "queries.json"
    path.write_text(json.dumps({"schema": "broad-album-queries-v1", "queries": [
        {"query_id": "bad", "text": text}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        baseline.read_queries(path)


def test_vector_contract_rejects_wrong_space_and_nonunit_or_invalid_values():
    value = np.zeros((2, 512), np.float32)
    value[:, 0] = 1
    baseline.validate_vectors(value, 2)
    for invalid in (value[:, :256], value * 2, value * np.nan):
        with pytest.raises(ValueError):
            baseline.validate_vectors(invalid, 2)


def test_query_file_changed_during_read_is_rejected_before_model_verification(tmp_path, monkeypatch):
    monkeypatch.setattr(baseline, "ROOT", tmp_path)
    path = tmp_path / "queries.json"
    path.write_text("original", encoding="utf-8")

    def changed_queries(query_path):
        query_path.write_text("replacement", encoding="utf-8")
        return [{"query_id": "cat", "text": "cat"}]

    monkeypatch.setattr(baseline, "read_queries", changed_queries)
    monkeypatch.setattr(baseline, "verify_model", lambda *args: pytest.fail("model must not load"))
    with pytest.raises(ValueError, match="changed while reading"):
        baseline.run(path, tmp_path / "model", tmp_path / "output")
    assert not (tmp_path / "output").exists()
