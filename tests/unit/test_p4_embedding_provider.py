"""P4-Embedding-Arch: provider identity, the unified spec, and the candidate rules.

Nothing here loads a real model: the manifests are the shipped ones (plus small generated
copies), the adapters are deterministic stand-ins handed through the same calls the production
paths use, and the Qwen adapter is only exercised on the parts that run *before* a model is
loaded (identity, pinned-file verification, device resolution, placement auditing, local-only
input). The real CUDA closure lives in ``tools/diagnostics/p4_embedding_provider_smoke.py``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

import pytest
from p2_search_fixtures import build_library

from photo_search.catalog.errors import (
    EMBEDDING_DEVICE_UNAVAILABLE,
    EMBEDDING_MANIFEST_INVALID,
    EMBEDDING_PROVIDER_UNKNOWN,
    EMBEDDING_WEIGHTS_HASH_MISMATCH,
    EMBEDDING_WEIGHTS_MISSING,
    PROCESSOR_MISMATCH,
    SPACE_MISMATCH,
    CatalogError,
)
from photo_search.indexing import semantic
from photo_search.models.embedding import base
from photo_search.models.embedding import cli as embedding_cli
from photo_search.models.embedding.qwen3_vl import (
    Qwen3VLEmbeddingAdapter,
    QwenRuntimeConfig,
    verify_pinned_files,
)
from photo_search.models.embedding.registry import EmbeddingRegistry, registry_for
from photo_search.runtime.device import DevicePolicy, DeviceResolution
from photo_search.scheduler.contracts import canonical_json
from photo_search.search.service import (
    _channel_summary,
    _entries_of,
    strong_match_max_distance,
    strong_match_policy,
)
from photo_search.workers.query_encoder import pinned_embedding_spec

REPO_ROOT = Path(__file__).resolve().parents[2]
CLIP_MANIFEST = REPO_ROOT / "tools" / "prepare_models" / "manifests" / "chinese-clip-vit-b-16.json"
QWEN_MANIFEST = (
    REPO_ROOT / "tools" / "prepare_models" / "manifests" / "qwen3-vl-embedding-2b.json"
)

#: The identity the historical Chinese-CLIP index was published under. These two strings are the
#: compatibility contract of this task: an existing catalog references them by name.
HISTORICAL_PROCESSOR = "chinese-clip-vit-b-16@36e679e65c2a"
HISTORICAL_SPACE = "chinese-clip-vit-b-16:c8ac42d3907c57b0"
QWEN_SPACE_PREFIX = "qwen3-vl-embedding-2b:"
DIMENSION = 512
PHOTOS = ("red.jpg", "green.jpg", "blue.jpg")


def read(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_manifest(tmp_path: Path, document: dict[str, Any], name: str = "manifest.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8", newline="\n")
    return path


def clip_manifest_with(tmp_path: Path, **provider: Any) -> Path:
    """The shipped Chinese-CLIP manifest with an explicit provider block."""
    document = read(CLIP_MANIFEST)
    block = {
        "provider_id": "chinese-clip-vit-b-16",
        "display_name": "Chinese-CLIP ViT-B-16",
        "adapter": "chinese_clip_onnx",
        "model_dir": "models/chinese_clip/vit-b-16",
        "device_policy": "cpu_allowed",
    }
    block.update(provider)
    document["provider"] = block
    return write_manifest(tmp_path, document, "clip.json")


class StubAdapter:
    """A deterministic stand-in for one space (both towers), as the production calls expect."""

    def __init__(self, spec: Any, *, device: str = "cuda") -> None:
        self._spec = spec
        self.device = str(device)
        self.paths: list[str] = []

    def spec(self) -> Any:
        return self._spec

    def health(self) -> dict[str, Any]:
        return {
            "loaded": True,
            "spec": self._spec.envelope,
            "device": {
                "device": self.device,
                "provider": "CUDAExecutionProvider" if self.device == "cuda" else "CPUExecutionProvider",
                "precision": "FP32",
                "cpu_fallback_disabled": True,
            },
            "runtime": {
                "runtime_backend": "onnxruntime",
                "runtime_package": "onnxruntime-gpu",
                "runtime_version": "1.30.0",
                "providers": ["CUDAExecutionProvider", "CPUExecutionProvider"],
                "cuda_available": True,
            },
        }

    def encode_image(self, path: str) -> list[float]:
        self.paths.append(str(path))
        dimension = int(self._spec.envelope["dimension"])
        digest = hashlib.sha256(Path(path).read_bytes()).digest()
        vector = [0.0] * dimension
        vector[int.from_bytes(digest[:4], "big") % dimension] = 1.0
        return vector

    def encode_queries(self, texts: Sequence[str], cancellation: Any = None) -> list[list[float]]:
        dimension = int(self._spec.envelope["dimension"])
        return [[1.0] + [0.0] * (dimension - 1) for _ in texts]


def fake_resolution(device: str = "cuda") -> DeviceResolution:
    return DeviceResolution(
        requested_device=None,
        device=device,
        provider="PyTorchCUDA" if device == "cuda" else "PyTorchCPU",
        policy=DevicePolicy.GPU_REQUIRED,
        is_degradation=False,
        reason="test",
        available_providers=("PyTorchCUDA", "PyTorchCPU"),
        precision="FP16",
        strict_fp32=False,
    )


class FakeCuda:
    def __init__(self, available: bool) -> None:
        self._available = bool(available)
        self.empty_cache_calls = 0

    def is_available(self) -> bool:
        return self._available

    def get_device_name(self, index: int) -> str:
        return "Fake RTX"

    def empty_cache(self) -> None:
        self.empty_cache_calls += 1


class FakeTorch:
    def __init__(self, available: bool = True) -> None:
        self.cuda = FakeCuda(available)
        self.float16 = FakeDtype(True)
        self.float32 = FakeDtype(True)
        self.float16.name = "float16"
        self.float32.name = "float32"
        self.__version__ = "2.8.0+cu128"
        self.allocations: list[str] = []

    def zeros(self, *_: Any, device: str = "cpu", **__: Any) -> Any:
        if device.startswith("cuda") and not self.cuda.is_available():
            raise RuntimeError("CUDA driver is unavailable")
        self.allocations.append(str(device))
        return SimpleNamespace(add_=lambda value: None)


class FakeDtype:
    def __init__(self, floating: bool) -> None:
        self.is_floating_point = bool(floating)


class FakeParameter:
    def __init__(self, device: str, dtype: FakeDtype) -> None:
        self.device = device
        self.dtype = dtype


class FakeModel:
    def __init__(self, parameters: dict[str, FakeParameter]) -> None:
        self._parameters = parameters

    def named_parameters(self) -> list[tuple[str, FakeParameter]]:
        return list(self._parameters.items())


def qwen_adapter(tmp_path: Path, **overrides: Any) -> Qwen3VLEmbeddingAdapter:
    document = read(QWEN_MANIFEST)
    for key, value in overrides.items():
        document[key] = value
    path = write_manifest(tmp_path, document, "qwen.json")
    return Qwen3VLEmbeddingAdapter(
        tmp_path / "model", path, QwenRuntimeConfig(device_policy="gpu_required")
    )


# ------------------------------------------------------------------ identity and the spec
def test_the_historical_clip_manifest_keeps_its_exact_identity() -> None:
    manifest = read(CLIP_MANIFEST)
    identity = base.identity_of(manifest)
    assert identity.provider_id == "chinese-clip-vit-b-16"
    assert identity.adapter == base.CLIP_ADAPTER
    assert identity.processor_id == HISTORICAL_PROCESSOR
    assert identity.space_id == HISTORICAL_SPACE
    assert (identity.dimension, identity.metric, identity.normalization_version) == (512, "cosine", "L2-v1")
    # The pre-P4 digest, recomputed independently here.
    expected = hashlib.sha256(
        canonical_json(
            {
                "model": manifest["model"],
                "embedding": manifest["embedding"],
                "preprocessing": manifest["preprocessing"],
                "tokenizer": manifest["tokenizer"],
                "onnx": manifest["onnx"],
            }
        ).encode("utf-8")
    ).hexdigest()
    assert identity.config_hash == expected
    assert identity.model_revision == "36e679e65c2a2fead755ae21162091293ad37834"
    assert identity.device_policy == "cpu_allowed"


def test_the_legacy_spec_document_is_unchanged() -> None:
    spec = pinned_embedding_spec(CLIP_MANIFEST)
    assert spec.spec_revision == "chinese-clip-vit-b-16-query-v1"
    assert spec.processor_id == HISTORICAL_PROCESSOR
    assert spec.envelope["space_id"] == HISTORICAL_SPACE
    assert spec.envelope["dimension"] == DIMENSION
    assert spec.device_policy == "CPU_ALLOWED"
    assert spec.runtime_backend == "onnxruntime"
    assert spec.runtime_package_id == "onnxruntime@1.30.0"
    assert spec.envelope["max_tokens"] == 52
    assert spec.envelope["max_pixels"] == 224 * 224
    assert [item["name"] for item in spec.envelope["weights"]] == [
        "image_tower.fp32.onnx",
        "text_tower.fp32.onnx",
    ]


def test_the_qwen_manifest_is_its_own_space_at_the_same_dimension() -> None:
    identity = base.identity_of(read(QWEN_MANIFEST))
    assert identity.provider_id == "qwen3-vl-embedding-2b"
    assert identity.adapter == base.QWEN_ADAPTER
    assert identity.processor_id == "qwen3-vl-embedding-2b@9f2f7e710d6d"
    assert identity.space_id.startswith(QWEN_SPACE_PREFIX)
    assert identity.dimension == DIMENSION  # same dimension as Chinese-CLIP, different space
    spec = pinned_embedding_spec(QWEN_MANIFEST)
    assert spec.spec_revision == "qwen3-vl-embedding-2b-query-v1"
    assert spec.device_policy == "GPU_REQUIRED"
    assert spec.runtime_backend == "pytorch"
    assert spec.runtime_package_id == "torch@2.8.0+cu128"
    assert list(spec.supported_devices) == ["gpu"]
    assert list(spec.supported_precisions) == ["FP16"]
    assert spec.envelope["max_tokens"] == 8192
    assert spec.envelope["max_pixels"] == 262144
    assert spec.envelope["space_id"] != HISTORICAL_SPACE
    assert [item["name"] for item in spec.envelope["weights"]][0] == "model.safetensors"


@pytest.mark.parametrize(
    ("family", "mutate", "space_changes", "processor_changes"),
    [
        ("clip", lambda doc: doc["artifacts"]["onnx"]["image"].update({"sha256": "b" * 64}), True, False),
        ("clip", lambda doc: doc["provider"].update({"provider_id": "chinese-clip-vit-b-16-v2"}), True, True),
        ("clip", lambda doc: doc["embedding"].update({"dimension": 1024}), True, False),
        ("clip", lambda doc: doc["preprocessing"].update({"image_size": 336}), True, False),
        ("clip", lambda doc: doc["model"].update({"resolved_revision": "d" * 40}), False, True),
        ("qwen", lambda doc: doc["preprocessing"].update({"max_pixels": 65536}), True, False),
        ("qwen", lambda doc: doc["instruction"].update({"query": "Retrieve a matching photo."}), True, False),
        ("qwen", lambda doc: doc["weights"][0].update({"sha256": "c" * 64}), True, False),
        ("qwen", lambda doc: doc["model"].update({"resolved_revision": "d" * 40}), False, True),
    ],
)
def test_any_identity_change_publishes_a_new_space_or_processor(
    family: str, mutate: Any, space_changes: bool, processor_changes: bool
) -> None:
    """Weights, preprocessing, instruction and revision changes can never reuse an old space."""
    if family == "clip":
        document = read(CLIP_MANIFEST)
        document["provider"] = {
            "provider_id": "chinese-clip-vit-b-16",
            "adapter": "chinese_clip_onnx",
            "model_dir": "models/chinese_clip/vit-b-16",
            "device_policy": "cpu_allowed",
        }
        before = base.identity_of(document)
        mutate(document)
        after = base.identity_of(document)
    else:
        document = read(QWEN_MANIFEST)
        before = base.identity_of(document)
        mutate(document)
        after = base.identity_of(document)
    assert (after.space_id != before.space_id) is space_changes
    assert (after.processor_id != before.processor_id) is processor_changes
    assert (after.space_id, after.processor_id) != (before.space_id, before.processor_id)


def test_an_unknown_or_undeclared_provider_is_refused() -> None:
    with pytest.raises(CatalogError) as refused:
        base.provider_block({"model": {"id": "someone/unknown-model"}})
    assert refused.value.code == EMBEDDING_MANIFEST_INVALID
    with pytest.raises(CatalogError):
        base.provider_block(
            {
                "model": {"id": "OFA-Sys/chinese-clip-vit-base-patch16"},
                "provider": {
                    "provider_id": "x",
                    "adapter": "not_an_adapter",
                    "model_dir": "models/x",
                    "device_policy": "cpu_allowed",
                },
            }
        )
    registry = registry_for(REPO_ROOT / "tmp" / "p4-embedding-arch" / "empty-data-root")
    with pytest.raises(CatalogError) as refused:
        registry.get("does-not-exist")
    assert refused.value.code == EMBEDDING_PROVIDER_UNKNOWN
    with pytest.raises(CatalogError) as refused:
        registry.for_processor("chinese-clip-vit-b-16@000000000000")
    assert refused.value.code == EMBEDDING_PROVIDER_UNKNOWN


def test_a_manifest_only_provider_is_reported_as_not_installed(tmp_path: Path) -> None:
    registry = registry_for(tmp_path)
    clip = registry.get("chinese-clip-vit-b-16")
    assert clip.installed is False
    assert clip.reason == "WEIGHTS_MISSING"
    assert "image_tower.fp32.onnx" in clip.missing_files
    with pytest.raises(CatalogError) as refused:
        registry.require_installed("chinese-clip-vit-b-16")
    assert refused.value.code == EMBEDDING_WEIGHTS_MISSING
    assert set(registry.get("qwen3-vl-embedding-2b").missing_files) >= {"model.safetensors"}


def test_a_data_root_manifest_overrides_the_shipped_one(tmp_path: Path) -> None:
    directory = tmp_path / "models" / "manifests"
    directory.mkdir(parents=True)
    document = read(CLIP_MANIFEST)
    document["provider"] = {
        "provider_id": "chinese-clip-vit-b-16",
        "adapter": "chinese_clip_onnx",
        "model_dir": "models/chinese_clip/vit-b-16",
        "device_policy": "gpu_required",
    }
    (directory / "chinese-clip-vit-b-16.json").write_text(
        json.dumps(document), encoding="utf-8", newline="\n"
    )
    registry = EmbeddingRegistry.discover(tmp_path)
    entry = registry.get("chinese-clip-vit-b-16")
    assert entry.source == "data_root"
    assert entry.device_policy == "gpu_required"


def test_a_strict_model_cannot_be_softened_into_a_cpu_run(tmp_path: Path) -> None:
    assert (
        base.effective_device_policy("gpu_required", DevicePolicy.CPU_ALLOWED)
        is DevicePolicy.GPU_REQUIRED
    )
    assert (
        base.effective_device_policy("cpu_allowed", "cpu_allowed") is DevicePolicy.CPU_ALLOWED
    )
    adapter = qwen_adapter(tmp_path)
    with pytest.raises(CatalogError) as refused:
        adapter._resolve_device(FakeTorch(available=False))
    assert refused.value.code == EMBEDDING_DEVICE_UNAVAILABLE
    assert refused.value.details["reason"] == "CUDA_UNAVAILABLE"
    assert refused.value.details["cpu_fallback"] == "disabled"
    explicit_cpu = Qwen3VLEmbeddingAdapter(
        tmp_path / "model",
        write_manifest(tmp_path, read(QWEN_MANIFEST), "qwen.json"),
        QwenRuntimeConfig(requested_device="cpu", device_policy="cpu_allowed"),
    )
    with pytest.raises(CatalogError) as refused:
        explicit_cpu._resolve_device(FakeTorch(available=True))
    assert refused.value.code == EMBEDDING_DEVICE_UNAVAILABLE


def test_the_qwen_adapter_only_accepts_local_images(tmp_path: Path) -> None:
    adapter = qwen_adapter(tmp_path)
    with pytest.raises(CatalogError) as refused:
        adapter.encode_image("https://huggingface.co/example.jpg")
    assert refused.value.code == EMBEDDING_MANIFEST_INVALID
    assert refused.value.details["reason"] == "REMOTE_INPUT_REJECTED"
    with pytest.raises(CatalogError) as refused:
        adapter.encode_image(tmp_path / "missing.jpg")
    assert refused.value.code == EMBEDDING_WEIGHTS_MISSING
    with pytest.raises(ValueError):
        adapter.encode_text("   ")
    with pytest.raises(RuntimeError):
        adapter.encode_text("a real query")  # not loaded: a query path must not fake it


def test_every_model_parameter_must_be_on_the_dispatch_device(tmp_path: Path) -> None:
    adapter = qwen_adapter(tmp_path)
    adapter._torch = FakeTorch()
    adapter._resolution = fake_resolution("cuda")
    adapter._model = FakeModel({"visual.proj.weight": FakeParameter("cpu", FakeDtype(True))})
    with pytest.raises(CatalogError) as refused:
        adapter._assert_placement()
    assert refused.value.code == EMBEDDING_DEVICE_UNAVAILABLE
    assert refused.value.details["expected"] == "cuda:0"
    adapter._model = FakeModel(
        {"visual.proj.weight": FakeParameter("cuda:0", FakeDtype(True))}
    )
    with pytest.raises(CatalogError) as refused:
        adapter._assert_placement()
    assert refused.value.details["parameter"] == "visual.proj.weight"
    adapter._model = FakeModel(
        {"visual.proj.weight": FakeParameter("cuda:0", adapter._torch.float16)}
    )
    adapter._assert_placement()  # FP16 on the CUDA device is the pinned configuration


def test_the_pinned_files_are_verified_before_any_load(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    document = read(QWEN_MANIFEST)
    for item in document["weights"]:
        payload = f"fake-{item['name']}".encode("utf-8")
        (model_dir / item["name"]).write_bytes(payload)
        item["sha256"] = hashlib.sha256(payload).hexdigest()
        item["bytes"] = len(payload)
    report = verify_pinned_files(model_dir, document)
    assert report["files"] == len(document["weights"])
    (model_dir / "config.json").write_bytes(b"tampered")
    with pytest.raises(CatalogError) as refused:
        verify_pinned_files(model_dir, document)
    assert refused.value.code == EMBEDDING_WEIGHTS_HASH_MISMATCH
    (model_dir / "config.json").unlink()
    with pytest.raises(CatalogError) as refused:
        verify_pinned_files(model_dir, document)
    assert refused.value.code == EMBEDDING_WEIGHTS_MISSING


def test_a_smaller_output_dimension_needs_a_declared_mrl_method(tmp_path: Path) -> None:
    document = read(QWEN_MANIFEST)
    document["embedding"]["mrl"] = ""
    with pytest.raises(CatalogError) as refused:
        Qwen3VLEmbeddingAdapter(
            tmp_path / "model", write_manifest(tmp_path, document, "qwen.json")
        )
    assert refused.value.code == EMBEDDING_MANIFEST_INVALID


def test_a_manifest_cannot_claim_a_larger_output_than_its_native_dimension() -> None:
    document = read(QWEN_MANIFEST)
    document["embedding"]["dimension"] = 4096
    with pytest.raises(CatalogError) as refused:
        base.identity_of(document)
    assert refused.value.code == EMBEDDING_MANIFEST_INVALID


# ----------------------------------------------------------- build/search space isolation
def test_a_build_refuses_an_encoder_of_another_space_with_zero_side_effects(tmp_path: Path) -> None:
    library = build_library(tmp_path, PHOTOS)
    spec = semantic.semantic_spec(CLIP_MANIFEST, {})
    adapter = StubAdapter(spec)
    try:
        with pytest.raises(CatalogError) as refused:
            semantic.build_index(
                library.catalog,
                encoder=adapter,
                manifest_path=CLIP_MANIFEST,
                expected_space_id="another-space:0000000000000000",
                limit=10,
            )
        assert refused.value.code == SPACE_MISMATCH
        with pytest.raises(CatalogError) as refused:
            semantic.build_index(
                library.catalog,
                encoder=adapter,
                manifest_path=CLIP_MANIFEST,
                expected_processor_id="another-processor@000000000000",
                limit=10,
            )
        assert refused.value.code == PROCESSOR_MISMATCH
        assert adapter.paths == []
        for table in ("artifacts", "index_entries", "index_outbox", "index_generations"):
            assert library.catalog.read_one(f"SELECT COUNT(*) AS value FROM {table}")["value"] == 0
        assert library.catalog.read_one("SELECT profile_id FROM active_search_profile")["profile_id"] is None
    finally:
        library.close()


def test_a_candidate_of_another_space_never_moves_the_pointer(tmp_path: Path) -> None:
    library = build_library(tmp_path, PHOTOS)
    try:
        served = semantic.semantic_spec(CLIP_MANIFEST, {})
        served_adapter = StubAdapter(served)
        report = semantic.build_index(
            library.catalog, encoder=served_adapter, manifest_path=CLIP_MANIFEST, limit=10
        )
        activated = semantic.ensure_search_profile(
            library.catalog,
            semantic.semantic_spec(CLIP_MANIFEST, semantic.adapter_runtime_facts(served_adapter)),
            space_id=str(served.envelope["space_id"]),
            generation_id=str(report["generation"]["generation_id"]),
        )
        assert activated["activated"] is True
        assert activated["profile_id"] == activated["active_profile_id"]

        candidate_manifest = clip_manifest_with(tmp_path, provider_id="chinese-clip-vit-b-16-v2")
        candidate_spec = semantic.semantic_spec(candidate_manifest, {})
        assert candidate_spec.envelope["space_id"] != served.envelope["space_id"]
        candidate_adapter = StubAdapter(candidate_spec)
        candidate_report = semantic.build_index(
            library.catalog,
            encoder=candidate_adapter,
            manifest_path=candidate_manifest,
            expected_space_id=str(candidate_spec.envelope["space_id"]),
            limit=10,
        )
        candidate = semantic.ensure_search_profile(
            library.catalog,
            semantic.semantic_spec(
                candidate_manifest, semantic.adapter_runtime_facts(candidate_adapter)
            ),
            space_id=str(candidate_spec.envelope["space_id"]),
            generation_id=str(candidate_report["generation"]["generation_id"]),
        )
        assert candidate["created"] is True
        assert candidate["activated"] is False
        assert candidate["reason"] == "CANDIDATE_NOT_ACTIVATED"
        assert candidate["active_profile_id"] == activated["profile_id"]
        assert library.catalog.read_one(
            "SELECT COUNT(*) AS value FROM search_profiles"
        )["value"] == 2
    finally:
        library.close()


def test_strong_match_uses_representation_specific_thresholds() -> None:
    assert strong_match_policy(HISTORICAL_SPACE) is True
    assert strong_match_max_distance(HISTORICAL_SPACE) == 1.3
    qwen_space = f"{QWEN_SPACE_PREFIX}abc"
    assert strong_match_max_distance(qwen_space) == 1.5
    assert strong_match_policy(qwen_space) is True
    assert strong_match_max_distance("unknown-space:abc") is None
    assert strong_match_policy(None) is False
    payload = {
        "channels": {
            "fts": [],
            "vector": [
                {"asset_id": 1, "distance": 1.49},
                {"asset_id": 2, "distance": 1.5},
                {"asset_id": 3, "distance": 1.500001},
            ],
        },
        "hits": [
            {
                "asset_id": asset_id,
                "score": 1.0 / asset_id,
                "matched_by": {"vector": asset_id},
                "hit_reason": "SEMANTIC",
                "matched_text": (),
                "semantic_ready": True,
            }
            for asset_id in (1, 2, 3)
        ],
        "vector_scan": {"blocks": 1, "scanned_assets": 1, "truncated": False},
        "authoritative": {"total": 1, "count": 1, "truncated": False, "order": "asset_id"},
    }
    clip_summary = _channel_summary(
        payload, match=object(), binding=SimpleNamespace(space_id=HISTORICAL_SPACE), top_k=5000
    )
    qwen_summary = _channel_summary(
        payload, match=object(), binding=SimpleNamespace(space_id=qwen_space), top_k=5000
    )
    unknown_summary = _channel_summary(
        payload, match=object(), binding=SimpleNamespace(space_id="unknown-space:abc"), top_k=5000
    )
    assert clip_summary["vector"]["strong_match_available"] is True
    assert clip_summary["vector"]["strong_match_count"] == 0
    assert qwen_summary["vector"]["strong_match_available"] is True
    assert qwen_summary["vector"]["strong_match_count"] == 2
    assert unknown_summary["vector"]["strong_match_available"] is False
    assert unknown_summary["vector"]["strong_match_count"] == 0
    assert clip_summary["vector"]["candidates"] == qwen_summary["vector"]["candidates"]
    assert clip_summary["vector"]["truncated"] == qwen_summary["vector"]["truncated"]
    assert clip_summary["fts"] == qwen_summary["fts"]
    assert clip_summary["authoritative"] == qwen_summary["authoritative"]
    assert tuple(entry.asset_id for entry in _entries_of(payload)) == (1, 2, 3)
    assert [r["asset_id"] for r in payload["channels"]["vector"]] == [1, 2, 3]


@pytest.fixture(params=[CLIP_MANIFEST, QWEN_MANIFEST], ids=["clip", "qwen"])
def local_model_install(request: Any, tmp_path: Path) -> tuple[Any, Path, str]:
    """Pinned synthetic files exercise the real installer without model downloads."""
    manifest = read(request.param)
    names = base.required_artifact_names(manifest)
    weight = next(name for name in names if name.endswith((".onnx", ".safetensors")))
    source = tmp_path / "source"
    source.mkdir()
    hashes = {}
    for name in names:
        content = b"w" * (4 * 1024 * 1024 + 17) if name == weight else name.encode("utf-8")
        (source / name).write_bytes(content)
        hashes[name] = (len(content), hashlib.sha256(content).hexdigest())
    if "weights" in manifest:
        for item in manifest["weights"]:
            item["bytes"], item["sha256"] = hashes[item["name"]]
    else:
        for item in manifest["artifacts"]["onnx"].values():
            item["bytes"], item["sha256"] = hashes[item["path"]]
        tokenizer = manifest["tokenizer"]
        tokenizer["vocab_sha256"] = hashes[tokenizer["vocab_file"]][1]
    data_root = tmp_path / "data"
    manifest_dir = data_root / "models" / "manifests"
    manifest_dir.mkdir(parents=True)
    provider = str(base.provider_block(manifest)["provider_id"])
    write_manifest(manifest_dir, manifest, provider + ".json")
    args = SimpleNamespace(data_root=data_root, provider=provider, source=source,
                           revision=None, force=False)
    return args, source, weight


def test_prepare_local_repairs_same_size_large_weights_and_skips_intact_files(
    local_model_install: tuple[Any, Path, str], monkeypatch: Any,
) -> None:
    args, source, weight = local_model_install
    first = embedding_cli._prepare_local(args)
    assert first["verification"]["hashes_verified"]
    destination = Path(first["model_dir"]) / weight
    # Corrupt only the final byte, past the former 4 MiB shortcut and chunk boundaries.
    with destination.open("r+b") as stream:
        stream.seek(-1, 2)
        stream.write(b"!")
    assert destination.stat().st_size == (source / weight).stat().st_size
    repaired = embedding_cli._prepare_local(args)
    assert repaired["copied_files"] == [weight]
    assert hashlib.sha256(destination.read_bytes()).digest() == hashlib.sha256(
        (source / weight).read_bytes()
    ).digest()

    def no_copy(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("intact installed files must not be copied again")

    monkeypatch.setattr(embedding_cli.shutil, "copy2", no_copy)
    assert embedding_cli._prepare_local(args)["copied_files"] == []
    args.source = Path(first["model_dir"])
    assert embedding_cli._prepare_local(args)["copied_files"] == []


def test_prepare_local_rejects_corrupt_source_before_touching_installed_model(
    local_model_install: tuple[Any, Path, str],
) -> None:
    args, source, weight = local_model_install
    installed = embedding_cli._prepare_local(args)
    manifest = Path(installed["manifest"])
    destination = Path(installed["model_dir"]) / weight
    before = (manifest.read_bytes(), embedding_cli._file_sha256(destination))
    with (source / weight).open("r+b") as stream:
        stream.seek(-1, 2)
        stream.write(b"!")
    with pytest.raises(CatalogError) as refused:
        embedding_cli._prepare_local(args)
    assert refused.value.code == EMBEDDING_WEIGHTS_HASH_MISMATCH
    assert (manifest.read_bytes(), embedding_cli._file_sha256(destination)) == before
