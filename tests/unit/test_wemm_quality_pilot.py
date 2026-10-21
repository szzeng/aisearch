"""Synthetic-only safety, resumability and reporting checks; no real model load."""

from __future__ import annotations

import copy
import errno
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tools.evaluation import wemm_pilot_runtime as runtime
from tools.evaluation import wemm_quality_pilot as pilot
from tools.evaluation.wemm_pilot_core import normalize_prefix


class FakeRuntime:
    calls = []
    fail = False

    def __init__(self, model, model_dir, device, dtype, manifest_path):
        self.name = model

    def load(self):
        if self.fail:
            raise RuntimeError("synthetic load failure with private path")
        return {"device": "synthetic", "gpu": "NONE", "not_gpu_evidence": True}

    def measured_encode(self, *, text=None, image=None):
        self.calls.append("text" if text is not None else "image")
        value = sum(text.encode()) if text is not None else sum(image.getpixel((0, 0)))
        values = np.random.default_rng(value).normal(size=(1, 2048)).astype(np.float32)
        return normalize_prefix(values, 2048), .1

    def resources(self):
        return {"gpu_peak_allocated_bytes": None, "gpu_peak_reserved_bytes": None}

    def close(self):
        pass


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(pilot, "ROOT", root)
    monkeypatch.setattr(runtime, "ROOT", root)
    monkeypatch.setattr(pilot, "source_identity", lambda: {"tool": "fixed-synthetic-code"})
    monkeypatch.setattr(pilot, "decoder_identity", lambda: {"decoder": "fixed-synthetic-policy"})
    monkeypatch.setattr(pilot, "package_versions", lambda: {"runtime": "synthetic"})
    monkeypatch.setattr(pilot, "verify_model", lambda *args: {"revision": "fixed-synthetic-lock"})
    for name in ("HF_HOME", "HF_MODULES_CACHE", "TORCH_HOME", "TEMP", "TMP", "UV_CACHE_DIR",
                 "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK"):
        monkeypatch.setenv(name, "before-synthetic-test")
    for model in ("qwen", "wemm"):
        (root / "models" / model).mkdir(parents=True)
    images = root / "input-photos"
    images.mkdir()
    rows = []
    for index in range(30):
        path = images / f"{index}.png"
        Image.new("RGB", (20, 30), (index, index + 1, 255)).save(path)
        rows.append({"asset_id": index, "sha256": runtime.sha256(path), "path": str(path),
                     "selection_stratum": "random" if index < 15 else "problem:cat"})
    sample = {"schema": "public-synthetic-test-v1", "assets": rows, "source_roots": [str(images)]}
    queries = [{"query_id": "q0", "text": "猫"}, {"query_id": "q1", "text": "<script>猫</script>"}]
    source = root / ".tmp/input"
    source.mkdir(parents=True)
    sample_path, query_path = source / "sample.json", source / "queries.json"
    sample_path.write_text(json.dumps(sample), encoding="utf-8")
    query_path.write_text(json.dumps(queries), encoding="utf-8")
    args = pilot.parser().parse_args([
        "smoke", "--out-dir", str(root / ".tmp/output"), "--model", "qwen",
        "--sample-file", str(sample_path), "--queries", str(query_path),
        "--qwen-model-dir", str(root / "models/qwen"),
        "--wemm-model-dir", str(root / "models/wemm"),
    ])
    FakeRuntime.calls = []
    FakeRuntime.fail = False
    return root, args, sample, queries


def _run_both(args):
    first = pilot.encode_run(args, runtime_factory=FakeRuntime)
    second_args = copy.copy(args)
    second_args.model = "wemm"
    second = pilot.encode_run(second_args, runtime_factory=FakeRuntime)
    return first, second


def test_smoke_resume_hashes_inputs_and_cached_vectors(workspace):
    root, args, sample, _ = workspace
    result = pilot.encode_run(args, runtime_factory=FakeRuntime)
    assert result["status"] == "COMPLETE"
    assert result["stability"]["passed"]
    assert len(result["images"]) == 30
    assert FakeRuntime.calls.count("image") == 30
    assert FakeRuntime.calls.count("text") == 3
    FakeRuntime.calls = []
    pilot.encode_run(args, runtime_factory=FakeRuntime)
    assert FakeRuntime.calls == ["text", "image", "text"]  # explicit stability probe only
    vector = args.out_dir / "qwen" / result["images"]["3"]["file"]
    vector.write_bytes(b"damaged")
    with pytest.raises(pilot.PilotError, match="CACHE_VECTOR_HASH_MISMATCH"):
        pilot.encode_run(args, runtime_factory=FakeRuntime)
    assert pilot.read_json(args.out_dir / "qwen/result.json")["status"] == "BLOCKED"
    assert pilot.read_json(args.out_dir / "qwen/progress.json")["failures"]
    assert Path(sample["assets"][3]["path"]).is_file()


def test_resume_refuses_new_query_identity_and_missing_input_without_replacement(workspace):
    _, args, sample, queries = workspace
    pilot.encode_run(args, runtime_factory=FakeRuntime)
    queries[0]["text"] = "狗"
    args.queries.write_text(json.dumps(queries), encoding="utf-8")
    with pytest.raises(pilot.PilotError, match="CACHE_IDENTITY_MISMATCH"):
        pilot.encode_run(args, runtime_factory=FakeRuntime)
    queries[0]["text"] = "猫"
    args.queries.write_text(json.dumps(queries), encoding="utf-8")
    Path(sample["assets"][9]["path"]).write_bytes(b"changed original fixture")
    FakeRuntime.calls = []
    with pytest.raises(pilot.PilotError, match="IMAGE_CONTENT_CHANGED"):
        pilot.encode_run(args, runtime_factory=FakeRuntime)
    assert FakeRuntime.calls == []


def test_encode_needs_completed_same_spec_smoke_and_preserves_load_failure(workspace):
    _, args, _, _ = workspace
    args.command = "encode"
    with pytest.raises(pilot.PilotError, match="ENCODE_REQUIRES_COMPLETED_SMOKE_RUN"):
        pilot.encode_run(args, runtime_factory=FakeRuntime)
    args.command = "smoke"
    FakeRuntime.fail = True
    with pytest.raises(RuntimeError):
        pilot.encode_run(args, runtime_factory=FakeRuntime)
    failure = pilot.read_json(args.out_dir / "qwen/result.json")
    assert failure["status"] == "BLOCKED"
    assert failure["failure"]["code"] == "ENCODING_FAILED"
    assert "private path" not in json.dumps(failure)


def test_output_roots_and_escaping_paths_refused(workspace):
    root, args, sample, _ = workspace
    for path in (root / "tracked-output", root.parent / "outside"):
        with pytest.raises(pilot.PilotError, match="OUTPUT_MUST_BE"):
            pilot.checked_output(path)
    with pytest.raises(pilot.PilotError, match="OUTPUT_OVERLAPS"):
        pilot.checked_output(root / ".tmp/input/report.json", [root / ".tmp/input"])
    args.out_dir = Path(sample["source_roots"][0]) / "results"
    with pytest.raises(pilot.PilotError):
        pilot.encode_run(args, runtime_factory=FakeRuntime)
    assert not args.out_dir.exists()


def test_comparison_no_labels_report_escaping_and_preview_bound(workspace):
    _, args, _, _ = workspace
    _run_both(args)
    compared = pilot.compare_runs(args)
    assert set(compared["rankings"]) == {"qwen512", "qwen2048", "wemm512"}
    assert "judged_precision" not in compared
    assert "fully_annotated_metrics" not in compared
    result = pilot.report(args)
    assert result["preview_count"] == 30
    document = (args.out_dir / "report.html").read_text(encoding="utf-8")
    assert "&lt;script&gt;猫&lt;/script&gt;" in document
    assert "<script>" not in document and "data:image" not in document
    assert "default-src 'none'" in document
    assert "第21至50名" in document


def test_labels_are_bound_to_content_and_query_definition(workspace):
    _, args, sample, queries = workspace
    _run_both(args)
    labels = {"provenance": {"kind": "synthetic fully labelled fixture"},
              "input_identities": [{key: row[key] for key in ("asset_id", "sha256")}
                                   for row in sample["assets"]], "query_definitions": queries,
              "queries": {"q0": [{"asset_id": row["asset_id"], "relevant": row["asset_id"] < 5}
                                  for row in sample["assets"]]}}
    args.labels = args.out_dir / "labels.json"
    args.labels.write_text(json.dumps(labels), encoding="utf-8")
    result = pilot.compare_runs(args)
    cat = result["fully_annotated_metrics"]["wemm512"]["q0"]
    assert cat["annotated_positives"] == 5
    assert cat["topk"]["20"]["maximum_possible_precision"] == .25
    assert set(cat["strata"]) == {"random", "problem:cat"}
    assert "average_precision" not in result["fully_annotated_metrics"]["wemm512"]["q1"]
    assert "precision" not in result["judged_precision"]["wemm512"]["q0"]["50"]
    labels["query_definitions"][0]["text"] = "another question"
    args.labels.write_text(json.dumps(labels), encoding="utf-8")
    with pytest.raises(pilot.PilotError, match="LABEL_DATASET_IDENTITY_MISMATCH"):
        pilot.compare_runs(args)


def test_comparison_rejects_decoded_pixel_mismatch(workspace):
    _, args, _, _ = workspace
    _run_both(args)
    path = args.out_dir / "wemm/result.json"
    document = pilot.read_json(path)
    document["images"]["0"]["decoded"]["rgb_sha256"] = "0" * 64
    pilot.atomic_json(path, document)
    with pytest.raises(pilot.PilotError, match="DECODED_PIXELS_MISMATCH"):
        pilot.compare_runs(args)


def test_runtime_requires_pinned_files_before_remote_code(tmp_path, monkeypatch):
    path = tmp_path / "weights"
    path.mkdir()
    config = path / "config.json"
    config.write_text("{}", encoding="utf-8")
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({"files": {"config.json": {
        "sha256": runtime.sha256(config), "bytes": 2}}}), encoding="utf-8")
    monkeypatch.setattr(runtime, "WEMM_LOCK", lock)
    assert runtime.verify_model("wemm", path)["verified_files"]["config.json"]["bytes"] == 2
    (path / "extra.py").write_text("raise Exception('must not execute')", encoding="utf-8")
    with pytest.raises(runtime.PilotError, match="UNPINNED_MODEL_FILE"):
        runtime.verify_model("wemm", path)
    (path / "extra.py").unlink()
    config.write_text("[]", encoding="utf-8")
    with pytest.raises(runtime.PilotError, match="MODEL_FILE_HASH_MISMATCH"):
        runtime.verify_model("wemm", path)


def test_exif_decode_uses_full_upright_rgb_without_modifying_original(tmp_path):
    path = tmp_path / "portrait.jpg"
    image = Image.new("RGB", (40, 20), (11, 22, 33))
    exif = image.getexif()
    exif[274] = 6
    image.save(path, exif=exif)
    before = runtime.sha256(path)
    decoded, facts = runtime.decode_image(path, before)
    try:
        assert decoded.size == (20, 40)
        assert facts["exif_orientation"] == 6
        assert decoded.mode == "RGB"
        assert runtime.sha256(path) == before
    finally:
        decoded.close()


def test_gpu_lock_closes_runtime_before_unlock(workspace):
    root, _, _, _ = workspace
    events = []

    class RuntimeWithLockProbe:
        def close(self):
            with pytest.raises(pilot.PilotError, match="ANOTHER_PILOT"):
                with pilot.gpu_lock([]):
                    pass
            events.append("closed-under-lock")

    with pilot.gpu_lock([], RuntimeWithLockProbe()):
        events.append("running")
    with pilot.gpu_lock([]):
        events.append("released")
    assert events == ["running", "closed-under-lock", "released"]
    assert (root / ".tmp/wemm-pilot-gpu.lock").is_file()


def test_competing_encoder_cannot_overwrite_or_create_run_evidence(workspace):
    _, args, _, _ = workspace
    with pilot.gpu_lock([]):
        with pytest.raises(pilot.PilotError, match="ANOTHER_PILOT"):
            pilot.encode_run(args, runtime_factory=FakeRuntime)
    assert not (args.out_dir / "qwen").exists()


def test_compare_and_report_preserve_input_root_guard_without_explicit_sample(workspace):
    root, args, _, _ = workspace
    _run_both(args)
    source_root = root / ".tmp/private-photo-root"
    for model in ("qwen", "wemm"):
        result_path = args.out_dir / model / "result.json"
        result = pilot.read_json(result_path)
        result["input_roots"] = [str(source_root)]
        pilot.atomic_json(result_path, result)
    pilot.compare_runs(args)
    args.qwen_run_dir = args.out_dir / "qwen"
    args.wemm_run_dir = args.out_dir / "wemm"
    args.comparison_file = args.out_dir / "comparison.json"
    args.sample_file = args.data_root = args.qwen_model_dir = args.wemm_model_dir = None
    args.out_dir = source_root / "attempted-output"
    for operation in (pilot.compare_runs, pilot.report):
        with pytest.raises(pilot.PilotError, match="OUTPUT_OVERLAPS_INPUT_ROOT"):
            operation(args)
    assert not source_root.exists()


def test_output_file_links_refused_without_creating_windows_links(workspace, monkeypatch):
    root, _, _, _ = workspace
    destination = root / ".tmp/output/link.json"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == destination or original(self))
    with pytest.raises(pilot.PilotError, match="OUTPUT_LINK_REFUSED"):
        pilot.atomic_json(destination, {"should_not_write": True})
    assert not destination.exists()


def test_duplicate_json_keys_cannot_replace_query_labels(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"q00":1,"q00":2}', encoding="utf-8")
    with pytest.raises(pilot.PilotError, match="DUPLICATE_JSON_KEY"):
        pilot.read_json(path)


def test_failed_gpu_counter_read_does_not_replace_original_failure():
    class CUDA:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def max_memory_allocated(device):
            raise RuntimeError("invalid device")

    class Torch:
        cuda = CUDA()

    model = runtime.ModelRuntime("wemm", Path("unused"), "cuda:999", "bf16")
    model.torch = Torch()
    assert model.resources()["gpu_counters_status"] == "UNAVAILABLE"


def test_wemm_preserves_official_template_and_embedding_token(monkeypatch):
    import sys
    from contextlib import nullcontext
    from types import SimpleNamespace

    calls = {}

    def vision(messages, **kwargs):
        calls["vision"] = kwargs
        return None, None, {}

    class Inputs(dict):
        def to(self, device):
            return self

    class Processor:
        token = 248077

        def apply_chat_template(self, messages, **kwargs):
            calls["template"] = kwargs
            return "official"

        def __call__(self, **kwargs):
            calls["processor"] = kwargs
            return Inputs(input_ids=np.array([[7, self.token]]), attention_mask=np.array([[1, 1]]))

    class Tensor:
        def float(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return np.ones((1, 2048), dtype=np.float32)

    monkeypatch.setitem(sys.modules, "qwen_vl_utils", SimpleNamespace(process_vision_info=vision))
    model = runtime.ModelRuntime("wemm", Path("unused"), "cuda:0", "bf16")
    model.torch = SimpleNamespace(inference_mode=nullcontext)
    model.processor = Processor()
    model.model = SimpleNamespace(embedding=lambda **kwargs: Tensor())
    vector = model.encode(text="猫")
    assert vector.shape == (1, 2048)
    assert calls["template"] == {"tokenize": False, "add_generation_prompt": False}
    assert calls["vision"]["image_patch_size"] == 16
    assert "max_pixels" not in calls["processor"]
    model.processor.token = 123
    with pytest.raises(runtime.PilotError, match="WEMM_EMBEDDING_TOKEN_MISSING"):
        model.encode(text="猫")


def test_model_runtime_requires_pinned_vision_preprocessing_package(monkeypatch):
    versions = {"python": "3.12.10", "transformers": "5.2.0", "qwen-vl-utils": "0.0.15"}
    monkeypatch.setattr(runtime, "package_versions", lambda: versions)
    model = runtime.ModelRuntime("wemm", Path("unused"), "cpu", "bf16")
    with pytest.raises(runtime.PilotError, match="EXPERIMENT_RUNTIME_VERSION_MISMATCH"):
        model.load()
    versions["qwen-vl-utils"] = "0.0.14"
    with pytest.raises(runtime.PilotError, match="CUDA_REQUIRED"):
        model.load()


def test_preview_budget_gives_later_model_unique_high_ranks_a_fair_slot():
    rankings = {"qwen2048": {"cat": [1, 2, 3], "dog": [4, 5, 6]},
                "qwen512": {"cat": [1, 3, 2], "dog": [4, 6, 5]},
                "wemm512": {"cat": [7, 1, 2], "dog": [8, 4, 5]}}
    assert pilot.preview_candidates(rankings, 4) == [1, 7, 4, 8]
    assert set(pilot.preview_candidates(rankings, 500)) == set(range(1, 9))
    for invalid in (0, 501, True):
        with pytest.raises(pilot.PilotError, match="PREVIEW_LIMIT"):
            pilot.preview_candidates(rankings, invalid)


def _sharing_error():
    error = PermissionError(errno.EACCES, "synthetic sharing denial")
    error.winerror = 32
    return error


def test_atomic_vector_replace_retries_same_bytes_without_reencoding(workspace, monkeypatch):
    root, _, _, _ = workspace
    monkeypatch.setattr(pilot, "_WINDOWS_REPLACE", True)
    real_replace, calls, sleeps = pilot.os.replace, [], []

    def replace(source, target):
        calls.append((source, source.read_bytes()))
        if len(calls) < 3:
            raise _sharing_error()
        return real_replace(source, target)

    monkeypatch.setattr(pilot.os, "replace", replace)
    monkeypatch.setattr(pilot.time, "sleep", sleeps.append)
    destination = root / ".tmp/retry/vector.npy"
    vector = normalize_prefix(np.ones((1, 2048), dtype=np.float32), 2048)
    digest = pilot.save_vector(destination, vector)
    assert digest == runtime.sha256(destination)
    assert calls[0] == calls[1] == calls[2]
    assert sleeps == [.03, .06]
    np.testing.assert_array_equal(np.load(destination), vector)


def test_windows_reader_sharing_denial_resolves_before_retry(workspace, monkeypatch):
    root, _, _, _ = workspace
    destination = root / ".tmp/retry/state.json"
    pilot.atomic_json(destination, {"version": "old"})
    monkeypatch.setattr(pilot, "_WINDOWS_REPLACE", True)
    if pilot.os.name == "nt":
        # A real Windows handle allows read/write sharing but denies delete-share;
        # os.replace must fail until that reader closes, like an inspecting shell.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.windll.kernel32
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(destination), 0x80000000, 3, None, 3, 0x80, None)
        assert handle != ctypes.c_void_p(-1).value
        closed = []

        def release_reader(delay):
            assert kernel.CloseHandle(handle)
            closed.append(delay)

        monkeypatch.setattr(pilot.time, "sleep", release_reader)
        try:
            pilot.atomic_json(destination, {"version": "new"})
        finally:
            if not closed:
                kernel.CloseHandle(handle)
        assert closed == [.03]
    else:
        # Other operating systems do not have Windows delete-share handles.
        # Exercise the identical retry decision with the Win32 error shape.
        real_replace, calls = pilot.os.replace, []

        def replace(source, target):
            calls.append(source)
            if len(calls) == 1:
                raise _sharing_error()
            return real_replace(source, target)

        monkeypatch.setattr(pilot.os, "replace", replace)
        monkeypatch.setattr(pilot.time, "sleep", lambda delay: None)
        pilot.atomic_json(destination, {"version": "new"})
        assert len(calls) == 2
    assert pilot.read_json(destination) == {"version": "new"}


def test_permanent_replace_denial_is_bounded_and_cleanup_cannot_hide_it(workspace, monkeypatch):
    root, _, _, _ = workspace
    destination = root / ".tmp/retry/state.json"
    pilot.atomic_json(destination, {"existing": "evidence"})
    before = destination.read_bytes()
    original, calls, sleeps = _sharing_error(), [], []

    def replace(source, target):
        calls.append(source)
        raise original

    real_unlink = Path.unlink

    def unlink(path, **kwargs):
        if path.suffix == ".tmp":
            raise PermissionError("cleanup must not replace original failure")
        return real_unlink(path, **kwargs)

    monkeypatch.setattr(pilot, "_WINDOWS_REPLACE", True)
    monkeypatch.setattr(pilot.os, "replace", replace)
    monkeypatch.setattr(pilot.time, "sleep", sleeps.append)
    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(PermissionError) as caught:
        pilot.atomic_json(destination, {"new": "unpublished"})
    assert caught.value is original
    assert len(calls) == 6 and sum(sleeps) == pytest.approx(.93)
    assert destination.read_bytes() == before


@pytest.mark.parametrize("windows,error", [
    (False, _sharing_error()), (True, FileNotFoundError("missing source")),
    (True, PermissionError(errno.EACCES, "no Win32 sharing error")),
])
def test_nonretryable_replace_errors_propagate_without_sleep(workspace, monkeypatch, windows, error):
    root, _, _, _ = workspace
    calls, sleeps = [], []

    def replace(source, target):
        calls.append(source)
        raise error

    monkeypatch.setattr(pilot, "_WINDOWS_REPLACE", windows)
    monkeypatch.setattr(pilot.os, "replace", replace)
    monkeypatch.setattr(pilot.time, "sleep", sleeps.append)
    with pytest.raises(type(error)) as caught:
        pilot.atomic_json(root / ".tmp/retry/state.json", {})
    assert caught.value is error
    assert len(calls) == 1 and not sleeps


def test_run_failure_records_numeric_os_error_without_private_path(workspace):
    _, args, _, _ = workspace

    class PermissionRuntime(FakeRuntime):
        def load(self):
            error = PermissionError(errno.EACCES, "denied", "private-original-photo.jpg")
            error.winerror = 32
            raise error

    with pytest.raises(PermissionError):
        pilot.encode_run(args, runtime_factory=PermissionRuntime)
    result = pilot.read_json(args.out_dir / "qwen/result.json")
    assert result["status"] == "BLOCKED"
    assert result["failure"]["errno"] == errno.EACCES
    assert result["failure"]["winerror"] == 32
    assert "private-original-photo" not in json.dumps(result)


def test_locked_failure_history_preserves_original_failure_and_blocked_result(workspace, monkeypatch):
    _, args, _, _ = workspace
    original_error = RuntimeError("original encoding failure")

    class FailedRuntime(FakeRuntime):
        def load(self):
            raise original_error

    original_write = pilot.atomic_json

    def write(path, value):
        if path.name == "progress.json":
            raise _sharing_error()
        return original_write(path, value)

    monkeypatch.setattr(pilot, "atomic_json", write)
    with pytest.raises(RuntimeError) as caught:
        pilot.encode_run(args, runtime_factory=FailedRuntime)
    assert caught.value is original_error
    result = pilot.read_json(args.out_dir / "qwen/result.json")
    assert result["status"] == "BLOCKED"
    assert result["failure"]["type"] == "RuntimeError"
    assert result["failure_history_write_error"]["winerror"] == 32
    failure_id = result["failure"]["failure_id"]
    monkeypatch.setattr(pilot, "atomic_json", original_write)
    resumed = pilot.encode_run(args, runtime_factory=FakeRuntime)
    assert resumed["historical_failure_count"] == 1
    progress = pilot.read_json(args.out_dir / "qwen/progress.json")
    assert progress["failures"][0]["failure_id"] == failure_id
    # A subsequent successful resume must neither drop nor duplicate that event.
    assert pilot.encode_run(args, runtime_factory=FakeRuntime)["historical_failure_count"] == 1
