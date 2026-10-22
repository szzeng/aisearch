"""Partition, leakage, failure and input identity contracts for the album pilot."""

import io
import json
import sys
import xml.etree.ElementTree as ET

import pytest
from PIL import Image

from tools.evaluation import prepare_album_coco as prep


def population(start=0):
    return [{"asset_id": i, "coco": {"id": i, "width": 40, "height": 30,
             "file_name": f"COCO_train2014_{i:012d}.jpg",
             "flickr_url": f"https://farm8.staticflickr.com/1234/{100000 + i}_abcdef_z.jpg"},
             "coco_categories": list(prep.CATEGORIES.values()), "license": {"id": 4}}
            for i in range(start, start + 200)]


@pytest.fixture
def local_io(monkeypatch):
    monkeypatch.setattr(prep.coco, "controlled_output", lambda p: p.resolve())


def jpeg():
    stream = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(stream, format="JPEG")
    return stream.getvalue()


def test_source_flickr_identity_survives_sizes_and_landing_urls():
    a = {"coco": {"flickr_url": "http://farm1.staticflickr.com/7/12345_deadbeef_z.jpg"}}
    b = {"attribution": {"OriginalURL": "https://live.staticflickr.com/7/12345_deadbeef_o.jpg",
                         "OriginalLandingURL": "https://www.flickr.com/photos/abc/12345/"}}
    assert prep.flickr_id(a) == prep.flickr_id(b) == "12345"
    assert prep.flickr_id({"coco": {"flickr_url": "https://evil.example/12345_ab.jpg"}}) is None
    b["attribution"]["OriginalLandingURL"] = "https://www.flickr.com/photos/abc/6789/"
    with pytest.raises(ValueError, match="conflicting"):
        prep.flickr_id(b)


def test_partition_is_deterministic_disjoint_and_preserves_random_half():
    budgets = {name: (8, 1) for name in prep.SPLITS}
    old = population()[:2]
    result = prep.freeze_partitions(population(), population(1000), old, budgets)
    again = prep.freeze_partitions(list(reversed(population())), list(reversed(population(1000))),
                                   old, dict(reversed(list(budgets.items()))))
    assert result == again
    assert all(len(rows) == 16 and sum(r["selection_stratum"] == "random" for r in rows) == 8
               for rows in result.values())
    assert prep.overlap_report({"prior_0": old, **result})["status"] == "DISJOINT"
    assert all(row["asset_id"] >= 1000 for row in result["test"])


def test_partition_excludes_prior_flickr_even_under_other_coco_id():
    rows = population()
    old = [{"asset_id": "old-open-images", "attribution": {
        "OriginalLandingURL": f"https://www.flickr.com/photos/abc/{100000 + i}/"}} for i in range(100)]
    result = prep.freeze_partitions(rows, population(1000), old, {name: (8, 1) for name in prep.SPLITS})
    assert all(row["asset_id"] >= 100 for name in ("train", "calibration") for row in result[name])


@pytest.mark.parametrize("budgets", [
    {"train": (8, 1)}, {name: (7, 1) for name in prep.SPLITS},
    {name: (800, 100) for name in prep.SPLITS}, {name: (8, True) for name in prep.SPLITS},
])
def test_invalid_partition_budgets_refused(budgets):
    with pytest.raises(ValueError, match="budgets"):
        prep.freeze_partitions(population(), population(1000), [], budgets)


def test_overlap_detects_within_cross_and_prior_content_without_silent_repair():
    a, b = population()[:2]
    a["sha256"] = b["sha256"] = "a" * 64
    report = prep.overlap_report({"train": [a], "test": [b]})
    assert report["status"] == "OVERLAP_REJECTED"
    assert report["collisions"]["sha256"]["a" * 64] == [
        {"partition": "train", "asset_id": 0}, {"partition": "test", "asset_id": 1}]
    assert prep.overlap_report({"train": [a, a]})["status"] == "OVERLAP_REJECTED"
    assert prep.overlap_report({"prior_0": [a], "test": [b]})["status"] == "OVERLAP_REJECTED"
    assert prep.overlap_report({"prior_0": [a], "prior_1": [b]})["status"] == "DISJOINT"


def test_evaluation_boxes_keep_both_box_and_mask_area_separate():
    selected = population()[:1]
    annotation = {"categories": [{"id": 17, "name": "cat"}], "annotations": [
        {"image_id": 0, "category_id": 17, "bbox": [1, 2, 10, 12], "area": 60, "iscrowd": 0},
        {"image_id": 500, "category_id": 17, "bbox": [0, 0, 4, 4], "area": 16, "iscrowd": 0}]}
    boxes = prep.evaluation_boxes(annotation, selected)
    assert set(boxes) == {"0"}
    assert boxes["0"]["cat"]["max_box_area_fraction"] == .1
    assert boxes["0"]["cat"]["max_mask_area_fraction"] == .05
    assert selected[0].get("bbox") is None
    annotation["annotations"][0]["bbox"][2] = float("nan")
    with pytest.raises(ValueError, match="box"):
        prep.evaluation_boxes(annotation, selected)


def test_bound_labels_use_eight_coco_classes_and_complete_identity():
    rows = population()[:2]
    rows[1]["coco_categories"] = []
    assets = [{"asset_id": i, "sha256": str(i) * 64} for i in range(2)]
    labels = prep.bound_labels(rows, assets, {"sha256": "source"})
    assert len(labels["queries"]) == 8
    assert labels["queries"]["cat"] == [
        {"asset_id": 0, "relevant": True, "basis": "COCO_INSTANCE_PRESENT"},
        {"asset_id": 1, "relevant": False, "basis": "COCO_CATEGORY_ABSENT"}]
    assert labels["input_identities"] == assets
    assert labels["query_definitions"] == prep.coco.QUERIES[:8]
    assert any("false negatives" in text for text in labels["provenance"]["limitations"])
    with pytest.raises(ValueError, match="identities"):
        prep.bound_labels(rows, list(reversed(assets)), {})
    with pytest.raises(ValueError, match="identities"):
        prep.bound_labels(rows, [{"asset_id": 0}, {"asset_id": 1}], {})


def test_materialize_decodes_hashes_and_keeps_ground_truth_outside_inputs(tmp_path, local_io, monkeypatch):
    row = {**population()[0], "selection_stratum": "random"}
    monkeypatch.setattr(prep.coco, "fetch_complete_image", lambda *a: jpeg())
    result = prep.materialize(row, tmp_path, "train2014")
    assert result["input_status"] == "READY_FOR_HASH_CHECK"
    assert result["sha256"] == prep.hashlib.sha256(jpeg()).hexdigest()
    assert "coco_categories" not in result and "category_counts" not in result and "bbox" not in result
    assert prep.materialize(row, tmp_path, "train2014", result) == result
    (tmp_path / row["coco"]["file_name"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        prep.materialize(row, tmp_path, "train2014", result)


def test_download_failures_have_fixed_member_and_bounded_history(tmp_path, local_io, monkeypatch):
    row = {**population()[0], "selection_stratum": "category:cat"}
    attempts = []
    def fail(*args):
        attempts.append(args[0])
        raise OSError("offline")
    monkeypatch.setattr(prep.coco, "fetch_complete_image", fail)
    monkeypatch.setattr(prep.time, "sleep", lambda _: None)
    result = prep.materialize(row, tmp_path, "train2014")
    assert result["asset_id"] == row["asset_id"]
    assert result["input_status"] == "FAILED" and "sha256" not in result
    assert len(result["failure_history"]) == len(attempts) == 3 and len(set(attempts)) == 1
    monkeypatch.setattr(prep.coco, "fetch_complete_image", lambda *a: jpeg())
    retry = prep.materialize(row, tmp_path, "train2014", result)
    assert retry["failure_history"] == result["failure_history"]
    assert retry["input_status"] == "READY_FOR_HASH_CHECK"


def test_cached_wrong_dimensions_and_source_name_are_rejected(tmp_path, local_io):
    row = {**population()[0], "selection_stratum": "random"}
    (tmp_path / row["coco"]["file_name"]).write_bytes(jpeg())
    row["coco"]["width"] = 41
    assert prep.materialize(row, tmp_path, "train2014")["input_status"] == "FAILED"
    with pytest.raises(ValueError, match="filename"):
        prep.materialize(row, tmp_path, "val2014")


def test_source_hash_and_prior_image_content_are_verified(tmp_path, local_io):
    path = tmp_path / "annotations.json"
    path.write_bytes(b"source")
    expected = prep.sha256(path)
    assert prep.source_identity(path, expected, "instances.json")["sha256"] == expected
    with pytest.raises(ValueError, match="hash"):
        prep.source_identity(path, "0" * 64, "instances.json")
    sample = tmp_path / "sample.json"
    sample.write_text(json.dumps({"schema": "wemm-pilot-public-sample-v1", "assets": [
        {"asset_id": 0, "path": str(path), "sha256": expected}]}), encoding="utf-8")
    groups, identities = prep.load_prior_samples([sample])
    assert len(groups["prior_0"]) == identities[0]["assets"] == 1
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="changed"):
        prep.load_prior_samples([sample])


@pytest.mark.parametrize("outcome", ["complete", "duplicate", "incomplete"])
def test_prepare_only_publishes_bound_labels_after_all_partition_checks(
        tmp_path, local_io, monkeypatch, outcome):
    train_path, val_path = tmp_path / "train.json", tmp_path / "val.json"
    for path in (train_path, val_path):
        path.write_text('{"categories": [], "annotations": []}', encoding="utf-8")
    monkeypatch.setattr(prep.coco, "ANNOTATION_SHA256", prep.sha256(train_path))
    monkeypatch.setattr(prep, "VAL_SHA256", prep.sha256(val_path))
    monkeypatch.setattr(prep, "download_validation_annotations", lambda _: val_path)
    monkeypatch.setattr(prep, "load_prior_samples", lambda _: ({}, []))
    monkeypatch.setattr(prep.coco, "load_population", lambda _: [])
    parts = {name: [{**population(i * 1000)[0], "selection_stratum": "random"}]
             for i, name in enumerate(prep.SPLITS)}
    monkeypatch.setattr(prep, "freeze_partitions", lambda *a: parts)
    def fake_materialize(row, *args):
        result = {**row, "input_status": "READY_FOR_HASH_CHECK", "bytes": 10,
                  "sha256": f"{row['asset_id']:064x}"}
        if outcome == "duplicate":
            result["sha256"] = "0" * 64
        if outcome == "incomplete" and row["asset_id"] == 1000:
            result.pop("sha256")
            result["input_status"] = "FAILED"
        return result
    monkeypatch.setattr(prep, "materialize", fake_materialize)
    output = tmp_path / "output"
    result = prep.prepare(output, train_path, [])
    if outcome == "complete":
        assert result["status"] == "READY"
        for name in prep.SPLITS:
            labels = json.loads((output / name / "labels.json").read_text(encoding="utf-8"))
            boxes = json.loads((output / name / "evaluation-boxes.json").read_text(encoding="utf-8"))
            assert boxes["input_identities"] == labels["input_identities"]
            assert boxes["query_definitions"] == labels["query_definitions"]
    else:
        assert result["status"] == "INCOMPLETE_OR_OVERLAP"
        assert all(not (output / name / "labels.json").exists() for name in prep.SPLITS)
        assert all(not (output / name / "evaluation-boxes.json").exists() for name in prep.SPLITS)
        if outcome == "duplicate":
            assert result["overlap_status"] == "OVERLAP_REJECTED"
        else:
            assert result["failures"][0]["asset_id"] == 1000
    assert all((output / name / "sample.json").exists() for name in prep.SPLITS)


@pytest.mark.parametrize("exception", [RuntimeError("test failure"), KeyboardInterrupt(), SystemExit(2)])
def test_cli_records_failure_junit_even_for_interrupts(tmp_path, local_io, monkeypatch, exception):
    evidence = tmp_path / "run.json"
    monkeypatch.setattr(sys, "argv", ["prepare", "--out-dir", str(tmp_path),
        "--train-annotations", "unused", "--prior-sample", "unused", "--evidence", str(evidence)])
    def fail(*args):
        raise exception
    monkeypatch.setattr(prep, "prepare", fail)
    with pytest.raises(type(exception)):
        prep.main()
    assert json.loads(evidence.read_text(encoding="utf-8"))["status"] == "FAILED"
    suite = ET.parse(evidence.with_suffix(".xml")).getroot()
    assert suite.attrib["failures"] == "1" and suite.find("testcase/failure") is not None
