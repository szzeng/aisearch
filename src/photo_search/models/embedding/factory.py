"""The one worker-side adapter factory: manifest in, loaded adapter out.

The API process never imports this module, and this module never imports a framework at import
time. A worker process is the only place that knows how to turn a manifest into a concrete
adapter, which keeps the "heavy import stays behind the worker boundary" rule of ADR-0003 §7
true for every provider - including the PyTorch one.

The requested device policy is *combined* with the manifest's own policy instead of replacing it,
so a caller's default can never soften a ``gpu_required`` model into a CPU run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from photo_search.catalog.errors import EMBEDDING_PROVIDER_UNKNOWN, CatalogError
from photo_search.models.embedding.base import (
    CLIP_ADAPTER,
    QWEN_ADAPTER,
    effective_device_policy,
    provider_block,
    read_manifest,
)
from photo_search.runtime.device import DevicePolicy


def build_adapter(
    model_dir: str | Path,
    manifest_path: str | Path,
    *,
    device: str | None = None,
    device_policy: str | DevicePolicy | None = None,
    enable_profiling: bool = False,
) -> Any:
    """Construct (but do not load) the adapter one manifest describes."""
    manifest = read_manifest(manifest_path)
    provider = provider_block(manifest)
    policy = effective_device_policy(provider["device_policy"], device_policy)
    adapter = str(provider["adapter"])
    if adapter == CLIP_ADAPTER:
        from photo_search.models.embedding.chinese_clip_onnx import (
            ChineseClipOnnxAdapter,
            RuntimeConfig,
        )

        return ChineseClipOnnxAdapter(
            Path(model_dir),
            Path(manifest_path),
            RuntimeConfig(
                requested_device=device,
                device_policy=policy,
                enable_profiling=bool(enable_profiling),
            ),
        )
    if adapter == QWEN_ADAPTER:
        from photo_search.models.embedding.qwen3_vl import (
            Qwen3VLEmbeddingAdapter,
            QwenRuntimeConfig,
        )

        return Qwen3VLEmbeddingAdapter(
            Path(model_dir),
            Path(manifest_path),
            QwenRuntimeConfig(
                requested_device=device,
                device_policy=policy,
                precision=str(manifest["runtime"]["precision"]),
                attention=str(manifest["runtime"]["attention"]),
            ),
        )
    raise CatalogError(
        EMBEDDING_PROVIDER_UNKNOWN,
        "this build has no adapter for the provider a manifest names",
        details={"adapter": adapter},
    )


def load_adapter(
    model_dir: str | Path,
    manifest_path: str | Path,
    *,
    device: str | None = None,
    device_policy: str | DevicePolicy | None = None,
    enable_profiling: bool = False,
) -> Any:
    """Build one adapter and load its model, or raise the adapter's structured refusal."""
    adapter = build_adapter(
        model_dir,
        manifest_path,
        device=device,
        device_policy=device_policy,
        enable_profiling=enable_profiling,
    )
    adapter.load()
    return adapter


__all__ = ["build_adapter", "load_adapter"]
