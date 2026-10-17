"""Integrity checks for the isolated public retrieval comparison."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tarfile
import zipfile

import numpy as np
import pytest
from PIL import Image

from tools.evaluation import public_search_quality as public_quality
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


def test_official_source_splits_have_separate_verified_metadata_caches(tmp_path, monkeypatch):
    ids = {name: f"COCO_val2014_{number:012d}" for number, name in enumerate(("test", "train", "val"), 1)}
    archive = tmp_path / "coco-cn-version1805v1.1.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        members = {f"coco-cn_{split}.txt": key + "\n" for split, key in ids.items()}
        members["imageid.human-written-caption.txt"] = "\n".join(f"{key}#0\t中文描述" for key in ids.values())
        for name, content in members.items():
            raw = content.encode()
            entry = tarfile.TarInfo("coco-cn-version1805v1.1/" + name)
            entry.size = len(raw)
            handle.addfile(entry, io.BytesIO(raw))
    monkeypatch.setattr(public_quality, "CN_SHA", public_quality.sha256(archive))
    remote = io.BytesIO()
    with zipfile.ZipFile(remote, "w", zipfile.ZIP_DEFLATED) as handle:
        for split in ("train2014", "val2014"):
            handle.writestr(f"annotations/captions_{split}.json", json.dumps({
                "licenses": [{"id": 4, "name": "CC-BY"}],
                "images": [{"file_name": key + ".jpg", "license": 4} for key in ids.values()] if split == "val2014" else [],
            }))
    loads = []

    def remote_zip(url):
        loads.append(url)
        return io.BytesIO(remote.getvalue())

    monkeypatch.setattr(public_quality, "RemoteZip", remote_zip)
    # An old cache only knew about test IDs; it must not suppress the train data.
    public_quality.write_json(tmp_path / "coco-image-license-metadata.json", {"images": {ids["test"]: {}}})
    for split in ("test", "train", "val"):
        selected, captions, metadata = public_quality.load_annotations(tmp_path, split)
        assert selected == [ids[split]]
        assert set(metadata["images"]) == {ids[split]}
        assert captions[ids[split]] == ["中文描述"]
        assert metadata["source_split"] == split
    assert len(loads) == 3
    public_quality.load_annotations(tmp_path, "train")
    assert len(loads) == 3
    train_cache = tmp_path / "coco-image-license-metadata-train.json"
    tampered = json.loads(train_cache.read_text(encoding="utf-8"))
    tampered["source_split"] = "test"
    public_quality.write_json(train_cache, tampered)
    with pytest.raises(ValueError, match="source split"):
        public_quality.load_annotations(tmp_path, "train")


def _jpeg_bytes(color):
    output = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(output, format="JPEG")
    return output.getvalue()


def _mock_public_downloads(monkeypatch):
    ids = [f"COCO_val2014_{number:012d}" for number in range(1, 4)]
    contents = dict(zip(ids, [_jpeg_bytes(color) for color in ("red", "green", "blue")], strict=True))
    source_splits, requested = [], []

    def annotations(directory, source_split="test"):
        source_splits.append(source_split)
        return ids, {key: ["独立图片"] for key in ids}, {
            "licenses": {"4": {"id": 4, "name": "CC-BY"}},
            "images": {key: {"file_name": key + ".jpg", "license": 4, "coco_url": f"http://images.cocodataset.org/val2014/{key}.jpg"} for key in ids},
        }

    def fetch_image(url, **kwargs):
        key = url.rsplit("/", 1)[-1][:-4]
        requested.append(key)
        return contents[key]

    monkeypatch.setattr(public_quality, "load_annotations", annotations)
    monkeypatch.setattr(public_quality, "fetch", fetch_image)
    return ids, contents, source_splits, requested


def test_prepare_excludes_prior_ids_before_download_and_records_provenance(tmp_path, monkeypatch):
    ids, contents, source_splits, requested = _mock_public_downloads(monkeypatch)
    excluded = tmp_path / "previous.json"
    public_quality.write_json(excluded, {
        "schema": "public-search-quality-dataset-v1",
        "images": [{"id": ids[0], "sha256": hashlib.sha256(contents[ids[0]]).hexdigest()}],
    })
    result = public_quality.prepare(argparse.Namespace(
        output=tmp_path / "new", count=2, tuning=1, seed=42,
        source_split="train", exclude_dataset=excluded,
    ))
    assert result["status"] == "ok"
    assert source_splits == ["train"]
    assert set(requested) == set(ids[1:])
    dataset = json.loads((tmp_path / "new" / "dataset.json").read_text(encoding="utf-8"))
    assert {row["id"] for row in dataset["images"]} == set(ids[1:])
    assert dataset["source_split"] == "train"
    assert dataset["excluded_dataset"]["sha256"] == public_quality.sha256(excluded)


def test_prepare_refuses_same_content_under_a_different_id(tmp_path, monkeypatch):
    ids, contents, source_splits, _ = _mock_public_downloads(monkeypatch)
    excluded = tmp_path / "previous.json"
    public_quality.write_json(excluded, {
        "schema": "public-search-quality-dataset-v1",
        "images": [{"id": "different-old-id", "sha256": hashlib.sha256(contents[ids[1]]).hexdigest()}],
    })
    # Older Python callers do not have source_split; they keep official test.
    with pytest.raises(ValueError, match="content overlaps"):
        public_quality.prepare(argparse.Namespace(
            output=tmp_path / "new", count=3, tuning=1, seed=42, exclude_dataset=excluded,
        ))
    assert source_splits == ["test"]
    assert not (tmp_path / "new" / "dataset.json").exists()


def test_prepare_legacy_arguments_and_duplicate_content_guard(tmp_path, monkeypatch):
    ids, contents, source_splits, _ = _mock_public_downloads(monkeypatch)
    contents[ids[1]] = contents[ids[0]]
    with pytest.raises(ValueError, match="duplicate content"):
        public_quality.prepare(argparse.Namespace(output=tmp_path / "new", count=3, tuning=1, seed=42))
    assert source_splits == ["test"]
    assert not (tmp_path / "new" / "dataset.json").exists()


def test_prepare_refuses_an_existing_dataset_without_touching_it(tmp_path):
    original = b"preserve the previous selection and evidence"
    (tmp_path / "dataset.json").write_bytes(original)
    with pytest.raises(public_quality.ExistingRunOutputError, match="preserve existing dataset"):
        public_quality.prepare(argparse.Namespace(output=tmp_path))
    assert (tmp_path / "dataset.json").read_bytes() == original
