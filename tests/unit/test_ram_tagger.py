from __future__ import annotations

import copy
import hashlib
import json
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from photo_search.catalog.errors import CatalogError
from photo_search.tagging import model as packages
from photo_search.tagging import runtime
from tools.prepare_models.export_ram_tagger import compare_scores, top_indices


@pytest.fixture
def package(tmp_path):
    folder = tmp_path / "models" / "taggers" / packages.PACKAGE_NAME
    folder.mkdir(parents=True)
    graph = b"test graph bytes"
    (folder / "model.ts").write_bytes(graph)
    manifest = {
        "schema": packages.SCHEMA,
        "model": {"file": "model.ts", "bytes": len(graph),
                  "sha256": hashlib.sha256(graph).hexdigest()},
        "classes": [{"index": i, "english": f"tag{i}", "chinese": f"标签{i}",
                     "threshold": 0.5} for i in range(4585)],
        "preprocess": copy.deepcopy(packages.PREPROCESS),
        "output": copy.deepcopy(packages.OUTPUT), "runtime": copy.deepcopy(packages.RUNTIME),
        "source": {"repository": "https://github.com/xinyu1205/recognize-anything",
                   "revision": "7cb804a8609e9f4b1a50b7f31436d2df40bb9481",
                   "checkpoint_sha256":
                   "497c178836ba66698ca226c7895317e6e800034be986452dbd2593298d50e87d",
                   "export_lock_sha256": "a" * 64},
    }
    _write(folder, manifest)
    return folder, manifest


def _write(folder, manifest):
    manifest["model_id"] = packages.content_id(manifest)
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def test_valid_package_is_lazy_and_content_bound(package):
    folder, manifest = package
    info = packages.read_model(folder.parents[2])
    assert info.model_id == manifest["model_id"]
    assert len(info.classes) == 4585
    assert info.classes[0].chinese == "标签0"
    info.verify()
    old = info.model_id
    manifest["classes"][0]["threshold"] = 0.6
    _write(folder, manifest)
    assert packages.read_model_directory(folder).model_id != old
    with pytest.raises(CatalogError, match="changed"):
        info.verify()


@pytest.mark.parametrize("change", [
    lambda m: m["model"].update(file="../other.ts"),
    lambda m: m["model"].update(bytes=packages.MAX_MODEL_BYTES + 1),
    lambda m: m["model"].update(sha256="no"),
    lambda m: m["classes"].pop(),
    lambda m: m["classes"][1].update(index=0),
    lambda m: m["classes"][0].update(threshold=float("nan")),
    lambda m: m["classes"][0].update(threshold=1.01),
    lambda m: m["classes"][0].update(threshold=True),
    lambda m: m["classes"][0].update(english=""),
    lambda m: m["runtime"].update(device="cpu"),
    lambda m: m["runtime"].update(batch=2),
    lambda m: m["preprocess"].update(size=[224, 224]),
    lambda m: m["source"].update(revision="unlocked"),
    lambda m: m.update(extra="unrecognized"),
])
def test_manifest_rejects_unsupported_contract(package, change):
    folder, manifest = package
    change(manifest)
    try:
        _write(folder, manifest)
    except ValueError:
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(CatalogError) as caught:
        packages.read_model_directory(folder)
    assert caught.value.code == "TAGGER_MODEL_INVALID"


def test_stale_identity_and_same_size_corruption_refused(package):
    folder, manifest = package
    info = packages.read_model_directory(folder)
    (folder / "model.ts").write_bytes(b"X" * info.model_bytes)
    # Discovery remains cheap, but inference verification always checks actual bytes.
    assert packages.read_model_directory(folder).model_id == info.model_id
    with pytest.raises(CatalogError, match="checksum"):
        info.verify()
    manifest["classes"][0]["chinese"] = "changed"
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(CatalogError):
        packages.read_model_directory(folder)


def test_missing_and_oversized_manifest(tmp_path, package):
    with pytest.raises(CatalogError) as caught:
        packages.read_model(tmp_path / "missing")
    assert caught.value.code == "TAGGER_MODEL_MISSING"
    folder, _ = package
    (folder / "manifest.json").write_bytes(b" " * (packages.MAX_MANIFEST_BYTES + 1))
    with pytest.raises(CatalogError, match="byte limit"):
        packages.read_model_directory(folder)


def test_package_symlink_is_refused(package, monkeypatch):
    folder, _ = package
    from pathlib import Path

    monkeypatch.setattr(Path, "is_symlink", lambda path: path.name == "model.ts")
    with pytest.raises(CatalogError, match="linked"):
        packages.read_model_directory(folder)


@pytest.mark.parametrize("size", [(2, 3), (511, 297), (384, 384)])
def test_preprocessing_exactly_matches_official_torchvision(size):
    import torch
    from torchvision import transforms

    rng = np.random.default_rng(7)
    with Image.fromarray(rng.integers(0, 256, (*size, 3), dtype=np.uint8)) as image:
        expected = transforms.Compose([
            transforms.Resize((384, 384)), transforms.ToTensor(),
            transforms.Normalize(packages.PREPROCESS["mean"], packages.PREPROCESS["std"]),
        ])(image).unsqueeze(0)
        assert torch.equal(runtime.preprocess_image(image, torch), expected)


def test_runtime_rejects_non_rgb_input():
    with Image.new("L", (2, 2)) as image, pytest.raises(CatalogError) as caught:
        runtime.preprocess_image(image, None)
    assert caught.value.code == "TAGGER_INPUT_INVALID"


def test_runtime_rejects_declared_large_image_before_allocating_pixels():
    image = Image.Image()
    image._mode = "RGB"
    image._size = (80_000_001, 1)
    with pytest.raises(CatalogError) as caught:
        runtime.preprocess_image(image, None)
    assert caught.value.code == "TAGGER_INPUT_INVALID"


class FakeTensor:
    def __init__(self, scores=None):
        self.data = np.zeros((1, 4585), dtype=np.float32) if scores is None else scores
        self.shape, self.device, self.dtype = self.data.shape, SimpleNamespace(type="cuda"), "fp16"

    def to(self, **kwargs):
        assert kwargs == {"device": "cuda:0", "dtype": "fp16"}
        return self

    def detach(self):
        return self

    float = cpu = detach

    def numpy(self):
        return self.data


@pytest.fixture
def fake_torch():
    return SimpleNamespace(
        __version__="2.8.0+cu128", float16="fp16", inference_mode=nullcontext,
        cuda=SimpleNamespace(is_available=lambda: True, empty_cache=lambda: None),
        jit=SimpleNamespace(optimized_execution=lambda enabled: nullcontext()),
    )


def test_prediction_has_bounded_scores_strict_threshold_and_close(package, monkeypatch, fake_torch):
    info = packages.read_model_directory(package[0])
    output = FakeTensor()
    output.data[0, :3] = [0.9, 0.5, 0.7]
    monkeypatch.setattr(runtime, "preprocess_image", lambda image, torch: FakeTensor())
    tagger = runtime.RamTagger(info, fake_torch, lambda tensor: output)
    prediction = tagger.predict(object())
    assert prediction.model_id == info.model_id
    assert prediction.selected_indices == (0, 2)
    assert len(prediction.scores) == 4585
    tagger.close()
    with pytest.raises(CatalogError) as caught:
        tagger.predict(object())
    assert caught.value.code == "TAGGER_MODEL_CLOSED"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_scores_never_escape_runtime(package, monkeypatch, fake_torch, value):
    info = packages.read_model_directory(package[0])
    output = FakeTensor()
    output.data[0, 0] = value
    monkeypatch.setattr(runtime, "preprocess_image", lambda image, torch: FakeTensor())
    with pytest.raises(CatalogError) as caught:
        runtime.RamTagger(info, fake_torch, lambda tensor: output).predict(object())
    assert caught.value.code == "TAGGER_OUTPUT_INVALID"


@pytest.mark.parametrize("field,value", [
    ("shape", (2, 4585)), ("shape", (1, 4)), ("dtype", "fp32"),
    ("device", SimpleNamespace(type="cpu")),
])
def test_wrong_output_shape_or_actual_device_is_refused(
        package, monkeypatch, fake_torch, field, value):
    info = packages.read_model_directory(package[0])
    output = FakeTensor()
    setattr(output, field, value)
    monkeypatch.setattr(runtime, "preprocess_image", lambda image, torch: FakeTensor())
    with pytest.raises(CatalogError) as caught:
        runtime.RamTagger(info, fake_torch, lambda tensor: output).predict(object())
    assert caught.value.code == "TAGGER_OUTPUT_INVALID"


def test_oom_stops_without_a_second_attempt_on_cpu(package, monkeypatch, fake_torch):
    info = packages.read_model_directory(package[0])
    calls = []

    def fail(tensor):
        calls.append(tensor)
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(runtime, "preprocess_image", lambda image, torch: FakeTensor())
    with pytest.raises(CatalogError) as caught:
        runtime.RamTagger(info, fake_torch, fail).predict(object())
    assert caught.value.code == "TAGGER_INFERENCE_FAILED"
    assert len(calls) == 1


def test_missing_cuda_never_loads_or_falls_back(package, monkeypatch, fake_torch):
    info = packages.read_model_directory(package[0])
    fake_torch.cuda.is_available = lambda: False
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    with pytest.raises(CatalogError) as caught:
        runtime.RamTagger.load(info)
    assert caught.value.code == "TAGGER_DEVICE_UNAVAILABLE"
    with pytest.raises(CatalogError) as caught:
        runtime.RamTagger.load(info, device="cpu")
    assert caught.value.code == "TAGGER_DEVICE_UNAVAILABLE"


def test_load_passes_only_local_verified_graph_to_cuda(package, monkeypatch, fake_torch):
    info = packages.read_model_directory(package[0])
    loaded = []
    graph = SimpleNamespace(eval=lambda: "graph")
    fake_torch.jit.load = lambda path, map_location: loaded.append((path, map_location)) or graph
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    tagger = runtime.RamTagger.load(info)
    assert loaded == [(str(info.model_path), "cuda:0")]
    assert tagger._model == "graph"


def test_top32_has_deterministic_boundary_ties_and_comparison():
    scores = np.full((1, 4585), 0.25, dtype=np.float32)
    assert top_indices(scores[0]).tolist() == list(range(32))
    report = compare_scores(scores, scores.copy(), np.full(4585, 0.5))
    assert report["max_abs"] == 0
    assert report["per_image"][0]["top32_boundary_tie"]
    changed = scores.copy()
    changed[0, 100] = 0.9
    report = compare_scores(scores, changed, np.full(4585, 0.5))
    assert report["top32_min_overlap"] == 31
    assert report["selected_flips"] == 1


def test_export_package_is_invisible_until_successful_validation(tmp_path, package):
    from tools.prepare_models.export_ram_tagger import staged_package

    data_root = tmp_path / "export-data"
    source, _ = package
    destination = data_root / "models" / "taggers" / packages.PACKAGE_NAME
    with staged_package(data_root) as staging:
        for name in ("manifest.json", "model.ts"):
            (staging / name).write_bytes((source / name).read_bytes())
        packages.read_model_directory(staging).verify()
        with pytest.raises(CatalogError) as missing:
            packages.read_model(data_root)
        assert missing.value.code == "TAGGER_MODEL_MISSING"
    packages.read_model(data_root).verify()
    assert destination.exists()
    assert list(destination.parent.iterdir()) == [destination]


def test_failed_export_does_not_publish_half_qualified_package(tmp_path, package):
    from tools.prepare_models.export_ram_tagger import staged_package

    data_root = tmp_path / "failed-export"
    source, _ = package
    with pytest.raises(ValueError, match="source changed"):
        with staged_package(data_root) as staging:
            for name in ("manifest.json", "model.ts"):
                (staging / name).write_bytes((source / name).read_bytes())
            packages.read_model_directory(staging).verify()
            raise ValueError("source changed")
    with pytest.raises(CatalogError) as missing:
        packages.read_model(data_root)
    assert missing.value.code == "TAGGER_MODEL_MISSING"
    assert list((data_root / "models" / "taggers").iterdir()) == []


def test_export_never_overwrites_an_existing_package(package):
    from tools.prepare_models.export_ram_tagger import staged_package

    folder, _ = package
    original = (folder / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError, match="already exists"):
        with staged_package(folder.parents[2]):
            pytest.fail("export should not start")
    assert (folder / "manifest.json").read_bytes() == original
    packages.read_model_directory(folder).verify()


def test_concurrent_package_publication_refuses_overwrite(tmp_path):
    from tools.prepare_models.export_ram_tagger import staged_package

    data_root = tmp_path / "concurrent-export"
    destination = data_root / "models" / "taggers" / packages.PACKAGE_NAME
    with pytest.raises(FileExistsError, match="appeared"):
        with staged_package(data_root) as staging:
            (staging / "manifest.json").write_bytes(b"new")
            destination.mkdir()
            (destination / "manifest.json").write_bytes(b"existing")
    assert (destination / "manifest.json").read_bytes() == b"existing"
    assert list(destination.parent.iterdir()) == [destination]


def test_export_rename_failure_cleans_unpublished_staging(tmp_path, monkeypatch):
    from pathlib import Path

    from tools.prepare_models.export_ram_tagger import staged_package

    data_root = tmp_path / "failed-rename"

    def reject_rename(source, destination):
        raise PermissionError("publish blocked")

    monkeypatch.setattr(Path, "rename", reject_rename)
    with pytest.raises(PermissionError, match="publish blocked"):
        with staged_package(data_root) as staging:
            (staging / "manifest.json").write_bytes(b"not published")
    assert list((data_root / "models" / "taggers").iterdir()) == []


def test_changed_package_after_load_releases_graph_before_gpu_gate_can_reopen(package, monkeypatch, fake_torch):
    import weakref

    info = packages.read_model_directory(package[0])
    calls, graph_refs = [], []

    class Graph:
        def eval(self):
            return self

    def load(path, map_location):
        graph = Graph()
        graph_refs.append(weakref.ref(graph))
        return graph

    def verify(self):
        calls.append("verify")
        if len(calls) == 2:
            raise CatalogError("TAGGER_MODEL_INVALID", "package changed during load")

    def empty_cache():
        assert graph_refs[0]() is None
        calls.append("released")

    fake_torch.jit.load = load
    fake_torch.cuda.empty_cache = empty_cache
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(packages.TaggerModel, "verify", verify)
    with pytest.raises(CatalogError, match="changed") as caught:
        runtime.RamTagger.load(info)
    # Keep the caught exception alive, as a caller logging it would do.
    assert caught.value.code == "TAGGER_MODEL_INVALID"
    assert calls == ["verify", "verify", "released"]
    assert graph_refs[0]() is None
