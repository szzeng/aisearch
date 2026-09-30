"""Apple GPU selection must remain an explicit, qualified logical GPU path."""
from types import SimpleNamespace

import pytest

from photo_search import config
from photo_search.indexing import semantic
from photo_search.models.embedding.fgclip2 import resolve_torch_device
from photo_search.runtime.device import DevicePolicy, DeviceUnavailableError


def runtime(*, cuda=False, mps=True):
    return SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: cuda),
                           backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)))


def test_apple_gpu_default_and_explicit_requests():
    selected = resolve_torch_device(runtime(), None, DevicePolicy.GPU_REQUIRED)
    assert (selected.device, selected.provider, selected.precision) == ("mps", "torch-mps", "FP16")
    assert not selected.is_degradation
    assert resolve_torch_device(runtime(cuda=True), "auto", DevicePolicy.CPU_ALLOWED).device == "cuda"
    assert resolve_torch_device(runtime(cuda=True), "mps", DevicePolicy.CPU_ALLOWED).device == "mps"
    assert resolve_torch_device(runtime(), "cpu", DevicePolicy.CPU_ALLOWED).is_degradation


@pytest.mark.parametrize("requested,policy,mps", [
    ("cuda", DevicePolicy.CPU_ALLOWED, True),
    ("cpu", DevicePolicy.GPU_REQUIRED, True),
    ("auto", DevicePolicy.CPU_ALLOWED, False),
    ("mps", DevicePolicy.CPU_ALLOWED, False),
])
def test_no_unrequested_cpu_fallback(requested, policy, mps):
    with pytest.raises(DeviceUnavailableError):
        resolve_torch_device(runtime(mps=mps), requested, policy)


def test_macos_data_directory(monkeypatch, tmp_path):
    monkeypatch.delenv(config.DATA_ROOT_ENV, raising=False)
    monkeypatch.setattr(config.sys, "platform", "darwin")
    monkeypatch.setattr(config.Path, "home", lambda: tmp_path)
    assert config.default_data_root() == tmp_path / "Library/Application Support/PhotoSearchData"


def test_mps_build_qualifies_gpu_without_cuda(tmp_path):
    from p2_search_fixtures import build_library

    from photo_search.models.embedding.manifest import DEFAULT_MANIFEST as MANIFEST

    class AppleAdapter:
        def health(self):
            return {"device": {"device": "mps", "provider": "torch-mps", "precision": "FP16"},
                    "runtime": {"runtime_backend": "pytorch", "runtime_package": "torch",
                                "runtime_version": "test", "cuda_available": False,
                                "mps_available": True, "providers": ["torch-mps", "torch-cpu"]}}
        def encode_image(self, _):
            return [1.0] + [0.0] * 767
    library = build_library(tmp_path, ("photo.jpg",))
    try:
        report = semantic.build_index(library.catalog, encoder=AppleAdapter(),
                                      manifest_path=MANIFEST, device="mps")
        assert report["evidence"]["resolved_device"] == "gpu"
        assert report["evidence"]["resolved_provider"] == "torch-mps"
        assert report["run"]["jobs"] == {"SUCCEEDED": 1}
        assert report["drain"]["applied"] == 1
    finally:
        library.close()


@pytest.mark.parametrize("length,walk,padding", [(12, "short", 64), (100, "long", 196)])
def test_query_length_selects_the_trained_text_head(length, walk, padding):
    import json

    from photo_search.models.embedding.fgclip2 import FGClip2Adapter
    from photo_search.models.embedding.manifest import DEFAULT_MANIFEST

    calls = []
    class Tokens(dict):
        def to(self, _):
            return self
    def tokenizer(text, **kwargs):
        if isinstance(text, str):
            return {"input_ids": [1] * length}
        calls.append(kwargs)
        return Tokens(input_ids=[1] * kwargs["max_length"])
    def features(**kwargs):
        assert kwargs["walk_type"] == walk
        assert len(kwargs["input_ids"]) == padding
        return [1.0]
    adapter = object.__new__(FGClip2Adapter)
    adapter.manifest = json.loads(DEFAULT_MANIFEST.read_text())
    adapter._device = "cpu"
    adapter._tokenizer = tokenizer
    adapter._model = SimpleNamespace(get_text_features=features)
    adapter._vector = lambda vector: vector
    assert adapter.encode_text("测试查询") == [1.0]
    assert calls[0]["padding"] == "max_length" and not calls[0]["truncation"]
