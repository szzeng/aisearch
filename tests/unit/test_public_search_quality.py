"""Integrity checks for the isolated public retrieval comparison."""

from __future__ import annotations

import io
import sys
import zipfile

import numpy as np
import pytest

from tools.evaluation.public_search_quality import (
    main,
    normalized,
    public_image_path,
    ranked_metrics,
    select_images,
    zip_member,
)


def test_sample_is_reproducible_image_disjoint_and_license_filtered():
    ids = [f"image-{index}" for index in range(20)]
    metadata = {
        "images": {key: {"license": 4 if index < 16 else 1} for index, key in enumerate(ids)},
        "licenses": {"4": {"id": 4, "name": "CC-BY"}},
    }
    captions = {key: ["中文人工描述", "同一照片的另一条描述"] for key in ids}
    sample = select_images(ids, captions, metadata, count=12, tuning=4, seed=42)
    assert sample == select_images(list(reversed(ids)), captions, metadata, count=12, tuning=4, seed=42)
    assert len({row["id"] for row in sample}) == 12
    tuning = {row["id"] for row in sample if row["split"] == "tuning"}
    holdout = {row["id"] for row in sample if row["split"] == "holdout"}
    assert len(tuning) == 4 and len(holdout) == 8 and not tuning & holdout
    assert all(row["license"]["id"] == 4 for row in sample)
    assert not {row["id"] for row in sample} & set(ids[16:])
    with pytest.raises(ValueError, match="eligible"):
        select_images(ids, captions, metadata, count=17, tuning=4, seed=42)


def test_retrieval_scores_known_pair_and_stable_ties_without_cross_split_gallery():
    queries = [{"id": "q1", "image_id": "a"}, {"id": "q2", "image_id": "c"}]
    result = ranked_metrics([[0.9, 0.2, 0.1], [0.5, 0.5, 0.5]], queries, ["a", "b", "c"])
    assert result["recall_at_1"] == .5
    assert result["recall_at_5"] == 1
    assert result["mrr"] == pytest.approx((1 + 1 / 3) / 2)
    assert [row["rank"] for row in result["per_query"]] == [1, 3]
    with pytest.raises(ValueError, match="matrix"):
        ranked_metrics([[float("nan"), 0, 0], [1, 0, 0]], queries, ["a", "b", "c"])


def test_each_mrl_prefix_is_renormalized_and_zero_vectors_refused():
    vector = np.array([[3, 4, 12]], dtype=np.float32)
    assert np.allclose(normalized(vector[:, :2]), [[.6, .8]])
    assert np.linalg.norm(normalized(vector)) == pytest.approx(1)
    assert not np.allclose(normalized(vector)[:, :2], normalized(vector[:, :2]))
    with pytest.raises(ValueError, match="embedding"):
        normalized([[0, 0]])


def test_remote_member_refuses_false_uncompressed_size():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("captions.json", b"A" * 1_000_000)
    stream.seek(0)
    with zipfile.ZipFile(stream) as archive:
        entry = archive.getinfo("captions.json")
    assert zip_member(stream, entry) == b"A" * 1_000_000
    entry.file_size = 1
    with pytest.raises(ValueError, match="size/CRC"):
        zip_member(stream, entry)


def test_public_image_filename_rejects_traversal_and_mismatched_identity(tmp_path):
    key = "COCO_val2014_000000000001"
    assert public_image_path(tmp_path, key + ".jpg", key).parent == tmp_path.resolve()
    for filename in ("../outside.jpg", "..\\outside.jpg", "D:\\outside.jpg"):
        with pytest.raises(ValueError, match="filename"):
            public_image_path(tmp_path, filename, key)
    with pytest.raises(ValueError, match="selected ID"):
        public_image_path(tmp_path, key + ".jpg", "COCO_val2014_000000000002")


@pytest.mark.parametrize("existing_name", ["execution.json", "vectors.npz"])
def test_cli_refuses_occupied_output_without_loading_or_writing(tmp_path, monkeypatch, capsys, existing_name):
    output = tmp_path / "existing-run"
    output.mkdir()
    original = b"previous evidence must remain unchanged"
    (output / existing_name).write_bytes(original)
    loaded = []

    class UnexpectedAdapter:
        def load(self):
            loaded.append(True)
            raise AssertionError("occupied output must be rejected before model loading")

    monkeypatch.setattr("photo_search.models.embedding.factory.build_adapter", lambda *args, **kwargs: UnexpectedAdapter())
    monkeypatch.setattr(sys, "argv", [
        "public_search_quality.py", "run", "--dataset", str(tmp_path / "unused-dataset.json"),
        "--output", str(output), "--model", "clip", "--model-dir", str(tmp_path / "unused-model"),
    ])
    assert main() == 1
    assert loaded == []
    assert [path.name for path in output.iterdir()] == [existing_name]
    assert (output / existing_name).read_bytes() == original
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "preserve existing evidence" in captured.err
