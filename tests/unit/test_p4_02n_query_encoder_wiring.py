"""P4-02N: the API process's worker-backed query encoder and its startup wiring.

Nothing here loads the real model, starts a process or touches the GPU: the worker client is
replaced through the documented ``client_factory`` seam and the manifest is the one the
repository ships, so these tests hold on a machine with no model, no GPU and no network.
The real-model, real-HTTP closure lives in ``tools/diagnostics/p4_02n_real_search.py``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import photo_search.__main__ as main_module
from photo_search.__main__ import _encoder_wiring
from photo_search.api.context import ApiContext
from photo_search.api.settings import ApiSettings
from photo_search.catalog.errors import (
    RUNTIME_CAPABILITY_UNAVAILABLE,
    RUNTIME_RESOLUTION_MISMATCH,
    SPACE_MISMATCH,
    CatalogError,
)
from photo_search.runtime.device import DevicePolicy
from photo_search.scheduler.contracts import ProcessorSpec
from photo_search.search.embedding import QueryEncoder, check_encoder_binding, encode_query
from photo_search.workers.query_encoder import (
    WorkerQueryEncoder,
    build_worker_query_encoder,
    pinned_embedding_spec,
    resolve_query_encoder,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "tools" / "prepare_models" / "manifests" / "chinese-clip-vit-b-16.json"
VECTOR = [0.01] * 512


def pinned() -> ProcessorSpec:
    return pinned_embedding_spec(MANIFEST)


def worker_spec(**overrides: Any) -> dict[str, Any]:
    """The ONNX adapter's own spec shape, as the worker reports it in ``health``."""
    values: dict[str, Any] = {
        "processor_id": str(pinned().processor_id),
        "space_id": str(pinned().envelope["space_id"]),
        "dimension": int(pinned().envelope["dimension"]),
        "metric": "cosine",
        "normalization": "L2",
        "model_revision": "36e679e65c2a2fead755ae21162091293ad37834",
        "device": "cuda",
        "precision": "FP32",
    }
    values.update(overrides)
    return values


def worker_health(**spec_overrides: Any) -> dict[str, Any]:
    return {
        "loaded": True,
        "spec": worker_spec(**spec_overrides),
        "device": {"device": "cuda", "precision": "FP32", "cpu_fallback_disabled": True},
        "runtime": {
            "runtime_backend": "onnxruntime",
            "runtime_package": "onnxruntime-gpu",
            "runtime_version": "1.30.0",
            "providers": ["CUDAExecutionProvider", "CPUExecutionProvider"],
            "cuda_available": True,
        },
    }


class StubWorker:
    """A stand-in AI worker: the real client seam, no process and no model."""

    def __init__(
        self,
        vector: Any = None,
        *,
        health: dict[str, Any] | None = None,
        image_error: Exception | None = None,
    ) -> None:
        self.vector = list(VECTOR) if vector is None else vector
        self._health = worker_health() if health is None else health
        self._image_error = image_error
        self.texts: list[str] = []
        self.image_calls: list[dict[str, Any]] = []
        self.health_calls = 0
        self.shutdown_calls = 0

    def health(self, timeout: float = 60.0) -> dict[str, Any]:
        self.health_calls += 1
        return self._health

    def encode_text(self, text: str, timeout: float = 30.0) -> dict[str, Any]:
        self.texts.append(text)
        return {"vector": list(self.vector)}

    def encode_image(self, image_path: Path, timeout: float = 60.0) -> dict[str, Any]:
        path = Path(image_path)
        self.image_calls.append(
            {"path": str(path), "existed": path.is_file(), "bytes": path.stat().st_size if path.is_file() else 0}
        )
        if self._image_error is not None:
            raise self._image_error
        return {"vector": list(self.vector)}

    def shutdown(self, timeout: float = 10.0) -> None:
        self.shutdown_calls += 1


class StubImage:
    """A decoded upload: anything with the ``save`` method the upload path hands over."""

    def __init__(self, payload: bytes = b"png-bytes") -> None:
        self.payload = payload
        self.saved: list[str] = []

    def save(self, path: str, fmt: str) -> None:
        self.saved.append(f"{fmt}:{path}")
        Path(path).write_bytes(self.payload)


class StubAdapterWithoutClose:
    """A stand-in adapter that has no ``close``: shutdown must still be safe."""

    def spec(self) -> ProcessorSpec:
        return pinned()

    def encode_queries(self, texts: Any, cancellation: Any = None) -> list[list[float]]:
        return [list(VECTOR) for _ in texts]


def test_the_pinned_spec_is_the_space_the_indexing_side_publishes() -> None:
    spec = pinned()
    assert spec.processor_kind == "embedding_model"
    assert spec.processor_id == "chinese-clip-vit-b-16@36e679e65c2a"
    assert spec.envelope["space_id"] == "chinese-clip-vit-b-16:c8ac42d3907c57b0"
    assert spec.envelope["dimension"] == 512
    assert spec.envelope["metric"] == "cosine"
    assert spec.envelope["normalization_version"] == "L2-v1"
    assert spec.envelope["max_tokens"] == 52
    assert spec.envelope["max_pixels"] == 224 * 224
    assert [item["name"] for item in spec.envelope["weights"]] == [
        "image_tower.fp32.onnx",
        "text_tower.fp32.onnx",
    ]
    assert ProcessorSpec.from_envelope(spec.to_envelope()) == spec
    check_encoder_binding(spec, space_id=spec.envelope["space_id"], dimension=512)
    with pytest.raises(CatalogError) as refused:
        check_encoder_binding(spec, space_id="another-space:1", dimension=512)
    assert refused.value.code == SPACE_MISMATCH


def test_the_adapter_answers_the_query_seam_with_the_worker_vector() -> None:
    worker = StubWorker()
    adapter = WorkerQueryEncoder(worker, pinned())
    spec = adapter.spec()
    assert isinstance(spec, ProcessorSpec)
    assert spec == pinned()
    assert adapter.encode_queries(["西湖 sunset"]) == [VECTOR]
    assert worker.texts == ["西湖 sunset"]
    # The search seam checks the encoder binding and the vector before it ranks anything.
    assert encode_query(
        adapter, "西湖 sunset", space_id=str(spec.envelope["space_id"]), dimension=512
    ) == VECTOR


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ({"vector": [1.0, 2.0]}, "dimension"),
        ({}, "returned no"),
        ({"vector": ["1.0"] * 512}, "value"),
    ],
)
def test_the_adapter_refuses_a_malformed_worker_vector(reply: Any, expected: str) -> None:
    worker = StubWorker()
    adapter = WorkerQueryEncoder(worker, pinned())
    worker.encode_text = lambda text, timeout=30.0: reply  # type: ignore[method-assign]
    with pytest.raises(CatalogError) as refused:
        adapter.encode_queries(["x"])
    assert refused.value.code == RUNTIME_RESOLUTION_MISMATCH
    assert expected in refused.value.message


def test_the_image_half_stages_a_decoded_upload_for_the_worker(tmp_path: Path) -> None:
    worker = StubWorker()
    adapter = WorkerQueryEncoder(worker, pinned())
    image = StubImage()
    assert adapter.encode_images([image, str(tmp_path / "plain.png")]) == [VECTOR, VECTOR]
    staged = worker.image_calls[0]
    assert staged["path"].endswith("query.png") and staged["existed"] is True
    assert staged["bytes"] == len(b"png-bytes")
    assert not Path(staged["path"]).exists()  # the staging directory is gone again
    assert worker.image_calls[1]["path"].endswith("plain.png")


def test_a_worker_that_is_not_on_the_manifest_space_is_refused() -> None:
    worker = StubWorker(health=worker_health(space_id="some-other-space:1"))
    with pytest.raises(CatalogError) as refused:
        build_worker_query_encoder(
            model_dir="/tmp/model", manifest_path=MANIFEST, client_factory=lambda *a, **k: worker
        )
    assert refused.value.code == SPACE_MISMATCH
    assert worker.shutdown_calls == 1  # a refused worker is never leaked


def test_a_worker_that_did_not_load_the_model_is_refused() -> None:
    worker = StubWorker(health={"loaded": False, "spec": worker_spec()})
    with pytest.raises(CatalogError) as refused:
        build_worker_query_encoder(
            model_dir="/tmp/model", manifest_path=MANIFEST, client_factory=lambda *a, **k: worker
        )
    assert refused.value.code == RUNTIME_CAPABILITY_UNAVAILABLE
    assert worker.shutdown_calls == 1


def test_the_live_runtime_facts_reach_the_spec() -> None:
    worker = StubWorker()
    adapter = build_worker_query_encoder(
        model_dir="/tmp/model", manifest_path=MANIFEST, client_factory=lambda *a, **k: worker
    )
    envelope = adapter.spec().envelope
    assert worker.health_calls == 1
    assert envelope["runtime_package_id"] == "onnxruntime-gpu@1.30.0"
    assert envelope["runtime_backend"] == "onnxruntime"
    assert envelope["supported_devices"] == ["gpu", "cpu"]
    assert envelope["supported_precisions"] == ["FP32"]
    adapter.close()
    assert worker.shutdown_calls == 1


def test_startup_without_a_model_keeps_the_old_no_encoder_behaviour(tmp_path: Path) -> None:
    missing = resolve_query_encoder(model_dir=tmp_path / "models", manifest_path=MANIFEST)
    assert missing.available is False and missing.factory is None
    assert missing.reason == "MODEL_DIR_MISSING"
    without_manifest = resolve_query_encoder(
        model_dir=tmp_path, manifest_path=tmp_path / "missing.json"
    )
    assert without_manifest.available is False
    assert without_manifest.reason == "MANIFEST_MISSING"
    context = ApiContext(ApiSettings.create(data_root=tmp_path), encoder_factory=missing.factory)
    assert context.query_encoder() is None
    context.close()


def test_startup_wires_the_real_adapter_and_stops_it_on_close(tmp_path: Path) -> None:
    (tmp_path / "model").mkdir()
    worker = StubWorker()
    created: list[Any] = []

    def client_factory(*args: Any, **kwargs: Any) -> StubWorker:
        created.append((args, kwargs))
        return worker

    wiring = resolve_query_encoder(
        model_dir=tmp_path / "model",
        manifest_path=MANIFEST,
        device="cuda",
        client_factory=client_factory,
    )
    assert wiring.available is True and wiring.reason == "READY"
    context = ApiContext(ApiSettings.create(data_root=tmp_path), encoder_factory=wiring.factory)
    encoder = context.query_encoder()
    assert isinstance(encoder, QueryEncoder)
    assert created == [
        (
            (tmp_path / "model", MANIFEST),
            {"device": "cuda", "device_policy": DevicePolicy.CPU_ALLOWED},
        )
    ]
    context.close()
    assert worker.shutdown_calls == 1
    assert context.query_encoder() is None


def test_shutdown_is_safe_for_an_adapter_without_close(tmp_path: Path) -> None:
    context = ApiContext(
        ApiSettings.create(data_root=tmp_path), encoder_factory=lambda: StubAdapterWithoutClose()
    )
    assert context.query_encoder() is not None
    context.close()  # must not raise


def test_the_startup_entry_resolves_the_model_relative_to_the_data_root(tmp_path: Path) -> None:
    args = argparse.Namespace(model_dir=None, model_manifest=None, model_device=None)
    wiring = _encoder_wiring(args, tmp_path)
    assert wiring.reason == "MODEL_DIR_MISSING"
    assert wiring.provider_id == "qwen3-vl-embedding-2b"
    assert Path(wiring.model_dir) == tmp_path / "models" / "qwen3_vl_embedding"
    assert Path(wiring.manifest).name == "qwen3-vl-embedding-2b.json"
    assert json.dumps(wiring.to_dict())  # reportable without any model

    explicit = argparse.Namespace(
        model_dir=tmp_path / "elsewhere", model_manifest=MANIFEST, model_device="cpu"
    )
    other = _encoder_wiring(explicit, tmp_path)
    assert other.reason == "MODEL_DIR_MISSING"
    assert Path(other.model_dir) == tmp_path / "elsewhere"
    assert Path(other.manifest) == MANIFEST


def test_an_existing_active_clip_profile_remains_authoritative(tmp_path: Path, monkeypatch) -> None:
    clip = object()

    class Registry:
        def find(self, provider_id: str):
            assert provider_id == "chinese-clip-vit-b-16"
            return clip

        def default(self):
            raise AssertionError("existing libraries must not consult the new-library default")

    monkeypatch.setattr(main_module, "registry_for", lambda _root: Registry())
    monkeypatch.setattr(main_module, "active_provider_id", lambda _root: "chinese-clip-vit-b-16")
    monkeypatch.setattr(
        main_module,
        "resolve_provider_encoder",
        lambda *, provider, device: SimpleNamespace(provider=provider, device=device),
    )
    wiring = _encoder_wiring(
        argparse.Namespace(model_dir=None, model_manifest=None, model_device=None), tmp_path
    )
    assert wiring.provider is clip
