"""Frozen candidate membership, ranking tails and strict runtime lifecycle."""

import copy
import hashlib
import importlib.util
import json
import sys
import types
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest

from tools.evaluation import album_reranker as probe
from tools.evaluation.wemm_pilot_core import cache_identity


def bilingual():
    queries, splits = [], []
    for group in range(100):
        for language in ("cn", "en"):
            qid = f"{group}-{language}"
            queries.append({"query_id": qid, "text": qid})
            splits.append({"query_id": qid, "group_id": str(group), "language": language,
                           "split": "test" if group < 80 else "calibration"})
    return queries, {"schema": "photobench-plan-v1", "album": 3, "splits": splits,
                     "rerank_subset": sorted(str(i) for i in range(80))[:10]}


def small_plan():
    pairs = [{"query_id": "cat", "query": "猫", "asset_id": aid, "path": f"{aid}.jpg",
              "image_sha256": "a" * 64, "baseline_rank": aid + 1} for aid in range(50)]
    return {"pairs": pairs, "reranked_query_ids": ["cat"],
            "baseline_rankings": {"cat": list(range(80)), "unchanged": list(reversed(range(80)))}}


def test_subset_is_exactly_predeclared_ten_test_bilingual_groups():
    queries, plan = bilingual()
    expected = [row["query_id"] for row in queries if row["query_id"].split("-")[0] in plan["rerank_subset"]]
    assert probe.subset_queries(plan, queries) == expected
    for change in ("calibration", "language", "duplicate", "coverage", "posthoc_subset"):
        bad = copy.deepcopy(plan)
        if change == "calibration":
            bad["splits"][0]["split"] = "calibration"
        elif change == "language":
            bad["splits"][0]["language"] = "en"
        elif change == "duplicate":
            bad["rerank_subset"][1] = "0"
        elif change == "coverage":
            bad["splits"].pop()
        else:
            bad["rerank_subset"][-1] = "70"
        with pytest.raises(ValueError):
            probe.subset_queries(bad, queries)


def test_rerank_changes_only_top50_preserves_full_tail_members_and_ties():
    plan = small_plan()
    rows = [{**pair, "logit": float(pair["asset_id"] // 2)} for pair in plan["pairs"]]
    result = probe.merge_rankings(plan, rows)
    assert result["cat"][:4] == [48, 49, 46, 47]
    assert result["cat"][50:] == plan["baseline_rankings"]["cat"][50:]
    assert result["unchanged"] == plan["baseline_rankings"]["unchanged"]
    assert set(result["cat"]) == set(plan["baseline_rankings"]["cat"])


@pytest.mark.parametrize("change", ["missing", "duplicate", "reordered", "changed_query", "nonfinite"])
def test_incomplete_or_modified_candidate_scores_refused(change):
    plan = small_plan()
    rows = [{**pair, "logit": 0.0} for pair in plan["pairs"]]
    if change == "missing":
        rows.pop()
    elif change == "duplicate":
        rows[-1] = rows[0]
    elif change == "reordered":
        rows.reverse()
    elif change == "changed_query":
        rows[0]["query"] = "dog"
    else:
        rows[0]["logit"] = float("nan")
    with pytest.raises(ValueError):
        probe.merge_rankings(plan, rows)


@pytest.fixture
def frozen_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "identity_path", lambda path: path.resolve())
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    assets = [{"asset_id": f"image-{i:04}", "sha256": "a" * 64,
               "path": str(tmp_path / f"image-{i:04}.jpg")} for i in range(1047)]
    queries, plan = bilingual()
    sample = {"schema": "wemm-pilot-public-sample-v1", "assets": assets}
    for filename, value in (("sample.json", sample), ("queries.json", queries), ("dataset-plan.json", plan)):
        (tmp_path / filename).write_text(json.dumps(value), encoding="utf-8")
    record = {"schema": "budget-embedding-run-v1", "status": "COMPLETE", "model": "qwen",
              "dtype": "fp16", "native_dimension": 2048, "assets": assets, "queries": queries,
              "sample_sha256": probe.previous.sha256(tmp_path / "sample.json"),
              "settings": {"batch_size": 1}, "model_lock": {"revision": "r"},
              "packages": {"numpy": "fixture"}, "decoder": {"orientation": "exif"},
              "source_sha256": {"fixture.py": "a" * 64}, "vectors_sha256": {}}
    for name, count in (("images.npy", 1047), ("queries.npy", 200)):
        matrix = np.zeros((count, 2048), dtype=np.float32)
        matrix[:, 0] = 1
        np.save(tmp_path / name, matrix, allow_pickle=False)
        record["vectors_sha256"][name] = probe.previous.sha256(tmp_path / name)
    identities = [{key: row[key] for key in ("asset_id", "sha256")} for row in assets]
    record["identity"] = cache_identity({key: record[key] for key in
        ("model", "dtype", "settings", "model_lock", "packages", "decoder", "source_sha256", "native_dimension")},
        identities, queries)
    (tmp_path / "result.json").write_text(json.dumps(record), encoding="utf-8")
    return tmp_path


def prepare(directory):
    return probe.prepare_plan(directory / "sample.json", directory / "queries.json",
                              directory / "dataset-plan.json", directory)


def test_plan_has_no_label_input_and_binds_all_matrices_queries_and_members(frozen_inputs):
    plan = prepare(frozen_inputs)
    assert len(plan["pairs"]) == 1000 and len(plan["query_definitions"]) == 200
    assert len(plan["input_identities"]) == 1047
    assert set(plan["inputs"]) == {"sample", "queries", "dataset_plan", "run", "images", "query_vectors"}
    assert plan["baseline_rankings"]["0-cn"][:3] == ["image-0000", "image-0001", "image-0002"]
    # Raw positive/negative labels never exist in this fixture or candidate input contract.
    bad = copy.deepcopy(plan)
    bad["pairs"][0]["asset_id"] = "image-0500"
    with pytest.raises(ValueError, match="recomputed"):
        probe.verify_plan(bad)


@pytest.mark.parametrize("file", ["images.npy", "queries.json", "sample.json"])
def test_changed_input_bytes_rejected_before_any_model_load(frozen_inputs, file):
    plan = prepare(frozen_inputs)
    (frozen_inputs / file).write_bytes(b"changed")
    with pytest.raises(ValueError, match="input changed"):
        probe.verify_plan(plan)


def test_strict_scoring_keeps_raw_logit_resets_rope_and_disables_cache(monkeypatch):
    import torch
    monkeypatch.setattr(torch.cuda, "synchronize", lambda: None)
    observed = {}
    class Inputs(dict):
        def to(self, device):
            return self
    class Model:
        device = "cpu"
        rope_deltas = "stale"
        def __call__(self, **kwargs):
            observed["cache"] = kwargs["use_cache"]
            observed["rope"] = self.rope_deltas
            return types.SimpleNamespace(last_hidden_state=torch.ones((1, 2, 1)))
    class Official:
        def format_mm_instruction(self, *args, **kwargs):
            observed["instruction"] = kwargs["instruction"]
            return "messages"
    runtime = probe.strict_reranker_class(types.SimpleNamespace(Qwen3VLReranker=Official), torch)()
    runtime.model = Model()
    runtime.score_linear = lambda hidden: hidden * 20.5
    runtime.tokenize = lambda _: Inputs(input_ids=torch.tensor([[1, 2]]), image_grid_thw=torch.tensor([[1, 2, 2]]))
    result = runtime.score_pair({"query": "cat"}, object())
    assert result["logit"] == 20.5 and result["token_count"] == 2
    assert observed == {"cache": False, "rope": None, "instruction": probe.SPEC["instruction"]}


def test_failed_runtime_load_closes_before_gpu_lock_releases(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "identity_path", lambda p: p.resolve())
    monkeypatch.setattr(probe, "verify_plan", lambda _: None)
    monkeypatch.setattr(probe.previous, "verify_model_files", lambda *args: None)
    monkeypatch.setattr(probe, "model_directory_inventory", lambda *args: {})
    trace = []
    @contextmanager
    def lock(_):
        trace.append("lock")
        try:
            yield
        finally:
            trace.append("unlock")
    class Runtime:
        def __init__(self, path):
            pass
        def load(self):
            trace.append("load")
            raise RuntimeError("load failed")
        def close(self):
            trace.append("close")
    monkeypatch.setattr(probe, "gpu_lock", lock)
    monkeypatch.setattr(probe, "StrictRuntime", Runtime)
    plan = tmp_path / "plan.json"
    plan.write_text("{}")
    with pytest.raises(RuntimeError, match="load failed"):
        probe.run(plan, tmp_path, tmp_path / "result")
    assert trace == ["lock", "load", "close", "unlock"]


@pytest.mark.parametrize("change", ["cpu", "float32", "training"])
def test_runtime_refuses_cpu_mixed_precision_or_training_mode(change):
    parameter = types.SimpleNamespace(device=types.SimpleNamespace(type="cuda"), dtype="fp16",
                                      is_floating_point=lambda: True)
    model = types.SimpleNamespace(training=False, parameters=lambda: [parameter])
    runtime = types.SimpleNamespace(model=model, score_linear=model)
    torch = types.SimpleNamespace(float16="fp16")
    probe.verify_runtime_parameters(runtime, torch)
    if change == "cpu":
        parameter.device.type = "cpu"
    elif change == "float32":
        parameter.dtype = "fp32"
    else:
        model.training = True
    with pytest.raises(RuntimeError):
        probe.verify_runtime_parameters(runtime, torch)


def test_interruption_produces_failed_junit(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "identity_path", lambda p: p.resolve())
    monkeypatch.setattr(probe, "prepare_plan", lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
    junit = tmp_path / "run.xml"
    monkeypatch.setattr(sys, "argv", ["rerank", "prepare", "--sample", "s", "--queries", "q",
        "--dataset-plan", "p", "--run-dir", "r", "--out", str(tmp_path / "out.json"), "--junit", str(junit)])
    with pytest.raises(KeyboardInterrupt):
        probe.main()
    assert ET.parse(junit).getroot().attrib["failures"] == "1"


@pytest.fixture
def locked_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "identity_path", lambda path: path.absolute())
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    directory = tmp_path / "model"
    files = {"config.json": b"{}", "scripts/qwen3_vl_reranker.py": b"VALUE = 7\n"}
    lock = {"files": {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                      for name, data in files.items()}}
    for name, data in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return directory, lock


def test_model_inventory_accepts_only_exact_locked_files_and_nonruntime_cache(locked_directory):
    directory, lock = locked_directory
    cache = directory / ".cache/huggingface/download/scripts/qwen3_vl_reranker.py.metadata"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"download metadata")
    bytecode = Path(importlib.util.cache_from_source(str(directory / "scripts/qwen3_vl_reranker.py")))
    bytecode.parent.mkdir()
    bytecode.write_bytes(b"not executable because locked source is compiled directly")
    inventory = probe.model_directory_inventory(directory, lock)
    assert inventory["locked_files"] == sorted(lock["files"])
    assert {row["path"] for row in inventory["ignored_cache_files"]} == {
        cache.relative_to(directory).as_posix(), bytecode.relative_to(directory).as_posix()}
    module = probe.load_locked_module(directory, lock)
    assert module.VALUE == 7
    assert module.__file__ == str(directory / "scripts/qwen3_vl_reranker.py")
    assert module.__spec__.name == "album_qwen_reranker_frozen"


@pytest.mark.parametrize("extra", ["processor_config.json", "adapter_config.json", "model.bin",
                                  "chat_template.json", "scripts/shim.py",
                                  ".cache/huggingface/download/unknown.json.metadata",
                                  "scripts/__pycache__/unknown.cpython-312.pyc"])
def test_model_inventory_refuses_unlocked_loader_inputs(locked_directory, extra):
    directory, lock = locked_directory
    path = directory / extra
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"unlocked")
    with pytest.raises(ValueError, match="unlocked model"):
        probe.model_directory_inventory(directory, lock)


def test_model_inventory_refuses_unknown_directories_and_missing_lock_file(locked_directory):
    directory, lock = locked_directory
    (directory / "unexpected").mkdir()
    with pytest.raises(ValueError, match="unlocked model directory"):
        probe.model_directory_inventory(directory, lock)
    (directory / "unexpected").rmdir()
    (directory / "config.json").unlink()
    with pytest.raises(ValueError, match="locked model file missing"):
        probe.model_directory_inventory(directory, lock)


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_model_inventory_refuses_links_before_following(locked_directory, monkeypatch, kind):
    directory, lock = locked_directory
    # Simulate the platform link predicate; no host link privilege is needed.
    linked = directory / "scripts"
    monkeypatch.setattr(Path, "is_" + kind, lambda path: path == linked)
    with pytest.raises(ValueError, match="filesystem links refused"):
        probe.model_directory_inventory(directory, lock)


def test_locked_source_loader_refuses_changed_bytes(locked_directory):
    directory, lock = locked_directory
    (directory / "scripts/qwen3_vl_reranker.py").write_bytes(b"raise AssertionError('must not execute')")
    with pytest.raises(ValueError, match="locked runtime source changed"):
        probe.load_locked_module(directory, lock)


def test_pair_evidence_binds_bytes_and_rejects_changed_scores(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "identity_path", lambda path: path.resolve())
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    path = tmp_path / "pairs.jsonl"
    rows = [{"query_id": "cat", "asset_id": "image", "logit": 1.0}]
    path.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    record = probe.pair_evidence(path, rows)
    assert record == {"path": "pairs.jsonl", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    path.write_text(json.dumps({**rows[0], "logit": 2.0}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="raw pair evidence differs"):
        probe.pair_evidence(path, rows)
