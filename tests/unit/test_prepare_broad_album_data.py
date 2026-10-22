"""Ground-truth and cache identity boundaries for broad photo-album experiments."""

import io
import json

import numpy as np
import pytest

from tools.evaluation import prepare_broad_album_data as prep


def hierarchy():
    return {"LabelName": "entity", "Subcategory": [
        {"LabelName": "animal", "Subcategory": [{"LabelName": "cat"}, {"LabelName": "dog"}]},
        {"LabelName": "pet", "Subcategory": [{"LabelName": "cat"}]},
    ]}


def human(image, label, positive):
    return {"ImageID": image, "LabelName": label, "Confidence": str(int(positive))}


def test_sound_dag_implications_never_turn_missing_or_sibling_labels_into_negatives():
    parents, children = prep.hierarchy_relations(hierarchy())
    names = {name: name for name in ("entity", "animal", "pet", "cat", "dog", "tree")}
    rows, conflicts = prep.object_judgments({"p", "n", "child-negative", "parent-positive"}, names, [
        human("p", "cat", True), human("n", "animal", False),
        human("child-negative", "cat", False), human("parent-positive", "animal", True),
    ], [], parents, children)
    known = {(r["asset_id"], r["class_id"]): r["relevant"] for r in rows}
    assert {key[1] for key, value in known.items() if key[0] == "p" and value} == {"entity", "animal", "pet", "cat"}
    assert {key[1] for key, value in known.items() if key[0] == "n" and value is False} == {"animal", "cat", "dog"}
    assert ("p", "dog") not in known and ("n", "pet") not in known
    assert ("child-negative", "animal") not in known
    assert ("parent-positive", "cat") not in known
    assert not conflicts and all(key[1] != "tree" for key in known)


def test_conflicting_human_and_box_evidence_remains_unknown_with_provenance():
    parents, children = prep.hierarchy_relations(hierarchy())
    rows, conflicts = prep.object_judgments({"x"}, {"animal": "animal", "cat": "cat"},
        [human("x", "animal", False)], [{"ImageID": "x", "LabelName": "cat"}], parents, children)
    assert len(rows) == len(conflicts) == 2
    assert all(row["relevant"] is None and len(row["basis"]) == 2 for row in rows)
    queries = prep.summarize_queries({"cat": "Cat"}, [{"asset_id": "x"}, {"asset_id": "unlabeled"}], rows)
    assert queries[0]["positive_asset_ids"] == queries[0]["negative_asset_ids"] == []
    assert queries[0]["unknown_count"] == 2


def test_split_support_does_not_change_population_or_invent_judgments():
    queries = prep.summarize_queries({"cat": "Cat"}, [{"asset_id": x} for x in "abcd"], [
        {"asset_id": "a", "class_id": "cat", "relevant": True},
        {"asset_id": "c", "class_id": "cat", "relevant": False},
    ], {"a": "calibration", "b": "calibration", "c": "test", "d": "test"})
    assert queries[0]["support_by_split"] == {
        "calibration": {"positive": 1, "negative": 0, "unknown": 1},
        "test": {"positive": 0, "negative": 1, "unknown": 1},
    }


def test_hierarchy_cycle_refused():
    with pytest.raises(ValueError, match="cyclic"):
        prep.hierarchy_relations({"LabelName": "a", "Subcategory": [
            {"LabelName": "b", "Subcategory": [{"LabelName": "a"}]}]})


def test_original_source_lock_and_workspace_boundary_are_required(tmp_path):
    reader = prep.Inputs(tmp_path)
    path = tmp_path / "labels.csv"
    path.write_bytes(b"original")
    reader.locked(path, {"sha256": prep.digest(b"original"), "bytes": 8})
    assert reader.key(path) == "labels.csv"
    with pytest.raises(ValueError, match="lock"):
        reader.locked(path, {"sha256": prep.digest(b"changed!"), "bytes": 8})
    path.write_bytes(b"changed!")
    with pytest.raises(ValueError, match="changed during"):
        reader.raw(path)
    with pytest.raises(ValueError, match="outside workspace"):
        reader.path(tmp_path.parent / "unrelated")
    with pytest.raises(ValueError, match="already exists"):
        prep.prepare(tmp_path, tmp_path)


def cached(tmp_path, *, matrix=None, reverse=False, source="sample"):
    reader = prep.Inputs(tmp_path)
    assets = [{"asset_id": "a", "sha256": "a" * 64}, {"asset_id": "b", "sha256": "b" * 64}]
    (tmp_path / "sample.json").write_text(source, encoding="utf-8")
    reader.raw("sample.json")
    cache = tmp_path / "cache"
    cache.mkdir()
    stream = io.BytesIO()
    np.save(stream, np.ones((2, 3), dtype=np.float32) if matrix is None else matrix)
    raw = stream.getvalue()
    (cache / "images.npy").write_bytes(raw)
    record = {"sample_sha256": prep.digest(b"sample"), "assets": assets[::-1] if reverse else assets,
              "vectors_sha256": {"images.npy": prep.digest(raw)}, "native_dimension": 3,
              "model": "synthetic", "identity": {"space_id": "fixture"}}
    (cache / "result.json").write_text(json.dumps(record), encoding="utf-8")
    return reader, assets


def test_cache_identity_and_order_cannot_be_silently_repaired(tmp_path):
    reader, assets = cached(tmp_path, reverse=True)
    with pytest.raises(ValueError, match="rows differ"):
        prep.cache_reference(reader, "cache", "sample.json", assets)


@pytest.mark.parametrize("matrix", [np.ones((1, 3)), np.full((2, 3), np.nan), np.ones((2, 3), dtype=int)])
def test_invalid_embedding_cache_is_not_reused(tmp_path, matrix):
    reader, assets = cached(tmp_path, matrix=matrix)
    with pytest.raises(ValueError, match="shape or values"):
        prep.cache_reference(reader, "cache", "sample.json", assets)


def test_cache_sample_mismatch_refused(tmp_path):
    reader, assets = cached(tmp_path, source="other sample")
    with pytest.raises(ValueError, match="sample identity"):
        prep.cache_reference(reader, "cache", "sample.json", assets)


def test_cache_reference_preserves_order_and_query_scope(tmp_path):
    reader, assets = cached(tmp_path)
    record = prep.cache_reference(reader, "cache", "sample.json", assets)
    assert record["row_by_asset"] == [{"asset_id": "a", "row": 0}, {"asset_id": "b", "row": 1}]
    assert "encode new category texts separately" in record["query_cache_scope"]
    (tmp_path / "cache" / "images.npy").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="changed during"):
        prep.cache_reference(reader, "cache", "sample.json", assets)
