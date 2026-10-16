"""Pinned model, synchronized measurement and saved-vector comparison checks."""

from __future__ import annotations

import argparse
import copy
from types import SimpleNamespace

import numpy as np
import pytest

from tools.evaluation import peer_search_quality as peer
from tools.evaluation import public_search_quality as public
from tools.evaluation import siglip_search_quality as siglip


def test_model_lock_checks_revision_every_file_and_official_processor(tmp_path):
    directory = tmp_path / "model"
    directory.mkdir()
    for name in siglip.MODEL_FILES:
        (directory / name).write_bytes(b"tiny isolated file for hash verification")
    public.write_json(directory / "config.json", {"model_type": "siglip2"})
    public.write_json(directory / "preprocessor_config.json", {"max_num_patches": 256,
                                                               "patch_size": 16})
    lock = {"model_id": siglip.MODEL_ID, "revision": siglip.REVISION,
            "files": {name: public.sha256(directory / name) for name in siglip.MODEL_FILES}}
    lock_path = tmp_path / "lock.json"
    public.write_json(lock_path, lock)
    assert siglip.verify_model(directory, lock_path)["files"] == lock["files"]
    (directory / "tokenizer.model").write_bytes(b"changed after download")
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        siglip.verify_model(directory, lock_path)
    lock["revision"] = "wrong-revision"
    public.write_json(lock_path, lock)
    with pytest.raises(ValueError, match="fixed SigLIP2"):
        siglip.verify_model(directory, lock_path)


def image_rows(tmp_path):
    rows = []
    for index in range(6):
        path = tmp_path / f"image-{index}.fixture"
        path.write_bytes(f"synthetic hash fixture {index}".encode())
        rows.append({"id": f"image-{index}", "sha256": public.sha256(path), "path": str(path),
                     "split": "tuning" if index < 3 else "holdout", "captions": [f"照片{index}"],
                     "coco": {"width": 900, "height": 600}})
    return rows


def test_image_hashes_are_checked_before_encoding(tmp_path):
    rows = image_rows(tmp_path)
    path = tmp_path / "dataset.json"
    public.write_json(path, {"schema": "public-search-quality-dataset-v1", "images": rows})
    images, queries, digest = siglip.verify_images(path)
    assert len(images) == len(queries) == 6 and len(digest) == 64
    (tmp_path / "image-1.fixture").write_bytes(b"changed")
    with pytest.raises(ValueError, match="public image changed"):
        siglip.verify_images(path)


def test_existing_run_is_refused_before_reading_inputs_or_loading_models(tmp_path, monkeypatch):
    output = tmp_path / "run"
    output.mkdir()
    evidence = output / "execution.json"
    evidence.write_bytes(b"previous evidence")
    monkeypatch.setattr(siglip, "static_identities", lambda _: pytest.fail("input read before guard"))
    with pytest.raises(FileExistsError, match="preserve existing"):
        siglip.run(argparse.Namespace(output=output, dataset=tmp_path / "missing"))
    assert evidence.read_bytes() == b"previous evidence"


def test_cuda_measurement_waits_before_and_after_function(monkeypatch):
    events = []
    clock = iter((10., 10.25))
    torch = SimpleNamespace(cuda=SimpleNamespace(synchronize=lambda: events.append("sync")))
    monkeypatch.setattr(siglip.time, "perf_counter", lambda: next(clock))

    def work():
        events.append("work")
        return "value"

    assert siglip.timed_cuda(torch, work) == ("value", .25)
    assert events == ["sync", "work", "sync"]
    summary = siglip.latency_summary([.001, .002, .003, .004, .100])
    assert summary["first_ms"] == 1
    assert summary["p50_ms"] == 3
    assert summary["p95_ms"] == 100  # Cold/outlier samples must not silently disappear.


def test_features_are_normalized_native_float32_and_zero_or_wrong_shape_rejected():
    class Feature:
        def __init__(self, values):
            self.values = values

        def detach(self):
            return self

        def float(self):
            self.values = self.values.astype(np.float32)
            return self

        def cpu(self):
            return self

        def numpy(self):
            return self.values

    values = np.zeros((1, 768), dtype=np.float64)
    values[0, :2] = [3, 4]
    result = siglip.feature_numpy(Feature(values))
    assert result.dtype == np.float32
    assert result[:2].tolist() == pytest.approx([.6, .8])
    with pytest.raises(ValueError, match="768-dimensional"):
        siglip.feature_numpy(Feature(np.ones((1, 512))))
    with pytest.raises(ValueError, match="invalid embedding"):
        siglip.feature_numpy(Feature(np.zeros((1, 768))))


@pytest.fixture
def comparison_run(tmp_path, monkeypatch):
    images = image_rows(tmp_path)
    data = {"schema": "public-search-quality-dataset-v1", "images": images}
    _, queries = peer.validate_dataset(data)
    dataset = tmp_path / "dataset.json"
    public.write_json(dataset, data)
    clip_images = np.eye(6, 512, dtype=np.float32)
    clip_queries = clip_images[[1, 0, 2, 4, 3, 5]]
    qwen_vectors = np.eye(6, 2048, dtype=np.float32)
    inputs = {"images": images, "queries": queries, "identities": {"test": "isolated replay"},
              "runs": {"clip": {"vectors": {"queries": clip_queries,
                                               "center": clip_images, "letterbox": clip_images},
                                 "result": {"variants": {name: {} for name in (
                                     "center", "letterbox", "two_view_max",
                                 )}}},
                       "qwen": {"vectors": {"queries": qwen_vectors, "native": qwen_vectors},
                                 "result": {"variants": {"512": {}, "2048": {}}}}}}
    for split in ("tuning", "holdout"):
        scores, qs, imgs = peer.base_scores(inputs, split)
        for name, source, variant in (("clip_center", "clip", "center"),
                                       ("clip_letterbox", "clip", "letterbox"),
                                       ("clip_two_view_max", "clip", "two_view_max"),
                                       ("qwen512", "qwen", "512")):
            inputs["runs"][source]["result"]["variants"][variant][split] = public.ranked_metrics(
                scores[name], qs, [row["id"] for row in imgs],
            )
        inputs["runs"]["qwen"]["result"]["variants"]["2048"][split] = copy.deepcopy(
            inputs["runs"]["qwen"]["result"]["variants"]["512"][split],
        )
    monkeypatch.setattr(peer, "load_inputs", lambda *args, **kwargs: inputs)
    directory = tmp_path / "siglip"
    directory.mkdir()
    vectors = {"queries": np.eye(6, 768, dtype=np.float32),
               "native": np.eye(6, 768, dtype=np.float32)}
    np.savez_compressed(directory / "vectors.npz", **vectors)
    runtime = {"fixture": "no model or GPU used"}
    result = {"schema": "siglip-search-run-v1", "status": "ok", "mode": siglip.MODE,
              "settings": siglip.SETTINGS, "identities": siglip.static_identities(dataset),
              "runtime": runtime, "runtime_sha256": siglip.json_digest(runtime),
              "vectors_sha256": public.sha256(directory / "vectors.npz"),
              "model_lock": {"model_id": siglip.MODEL_ID, "revision": siglip.REVISION,
                             "files": {name: "0" * 64 for name in siglip.MODEL_FILES}},
              "variants": {"native768": siglip.split_metrics(vectors, images, queries)}}
    public.write_json(directory / "result.json", result)
    public.write_json(directory / "execution.json", {"status": "ok", "exit_code": 0})
    return argparse.Namespace(dataset=dataset, clip_run=tmp_path / "clip",
                              qwen_run=tmp_path / "qwen", plan=tmp_path / "plan.json",
                              exclude_dataset=tmp_path / "prior.json", run_dir=directory,
                              output=tmp_path / "comparison.json")


def test_saved_vectors_compare_known_pairs_and_keep_observed_data_label(comparison_run):
    response = siglip.compare(comparison_run)
    assert response["mode"] == siglip.MODE
    assert response["holdout_top1"]["siglip_native768"] == 1
    assert response["holdout_top1"]["clip_center"] == pytest.approx(1 / 3)
    assert response["holdout_top1"]["qwen2048"] == 1
    result = peer.read_json(comparison_run.output)
    compared = result["splits"]["holdout"]["siglip_vs_baseline"]["clip_center"]
    assert compared["gained_top1_queries"] == 2 and compared["lost_top1_queries"] == 0
    original = comparison_run.output.read_bytes()
    with pytest.raises(FileExistsError):
        siglip.compare(comparison_run)
    assert comparison_run.output.read_bytes() == original


@pytest.mark.parametrize("corruption", ["identity", "runtime", "setting", "exit", "rank", "vector"])
def test_comparison_refuses_corrupted_or_mismatched_evidence(comparison_run, corruption):
    path = comparison_run.run_dir / "result.json"
    result = peer.read_json(path)
    if corruption == "identity":
        result["identities"]["sha256"]["dataset"] = "0" * 64
    elif corruption == "runtime":
        result["runtime"]["changed"] = True
    elif corruption == "setting":
        result["settings"]["max_length"] = 32
    elif corruption == "exit":
        public.write_json(comparison_run.run_dir / "execution.json", {"status": "ok", "exit_code": 1})
    elif corruption == "rank":
        result["variants"]["native768"]["holdout"]["per_query"][0]["rank"] = 2
    else:
        vector_path = comparison_run.run_dir / "vectors.npz"
        vector_path.write_bytes(vector_path.read_bytes() + b"unexpected trailing bytes")
    public.write_json(path, result)
    with pytest.raises(ValueError, match="mismatch|do not reproduce"):
        siglip.compare(comparison_run)
    assert not comparison_run.output.exists()


def test_runtime_fingerprint_changes_if_numpy_version_changes(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset.json"
    dataset.write_bytes(b"{}")
    first = siglip.static_identities(dataset)
    original = siglip.importlib.metadata.version
    monkeypatch.setattr(siglip.importlib.metadata, "version", lambda name:
                        "different" if name == "numpy" else original(name))
    assert siglip.static_identities(dataset) != first
