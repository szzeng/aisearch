"""Opt-in, request-local search timing helpers used only by the dev diagnostic UI."""

from __future__ import annotations

import json
import os
import time
from typing import Any

PERF_ENV = "PHOTO_SEARCH_SEARCH_PERF"
SUPPORTED_PROVIDERS = frozenset({
    "chinese-clip-vit-b-16",
    "chinese-clip-vit-b-16-exif-v2",
    "qwen3-vl-embedding-2b",
    "qwen3-vl-embedding-2b-exif-v2",
})


def enabled() -> bool:
    return os.environ.get(PERF_ENV, "").strip() == "1"


def started() -> float:
    return time.perf_counter()


def elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


def server_timing(metrics: dict[str, Any], search_execution_ms: float) -> str:
    """Format bounded numeric metrics in the standard Server-Timing header."""
    values = {**metrics, "search_execution_ms": search_execution_ms}
    return ", ".join(
        f"{name};dur={max(0.0, float(value)):.3f}"
        for name, value in values.items()
        if name.endswith("_ms") and isinstance(value, (int, float))
    )


def safe_identity(binding: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(binding, dict):
        return None
    space_id = str(binding.get("space_id") or "")
    provider_id = space_id.split(":", 1)[0] if ":" in space_id else ""
    if provider_id not in SUPPORTED_PROVIDERS:
        return None
    return {
        "active_profile_id": binding.get("profile_id"),
        "active_image_generation_id": binding.get("generation_id"),
        "provider_id": provider_id,
        "space_id": space_id,
        "dimension": binding.get("dimension"),
        "encoder_identity": provider_id,
    }


def binding_mismatch(binding: dict[str, Any] | None, encoder: dict[str, Any] | None) -> bool | None:
    """Compare the bound index identity with the adapter spec without contacting the worker."""
    if not isinstance(binding, dict) or not isinstance(encoder, dict):
        return None
    return any((
        str(encoder.get("processor_id") or "") != str(binding.get("processor_id") or ""),
        str(encoder.get("space_id") or "") != str(binding.get("space_id") or ""),
        int(encoder.get("dimension") or 0) != int(binding.get("dimension") or 0),
    ))


def encode_metrics(metrics: dict[str, Any]) -> str:
    """Serialize only allow-listed diagnostic facts; never include the query or paths."""
    payload = {
        key: value for key, value in metrics.items()
        if key in {
            "encoder_resolve_ms", "query_encode_ms", "intent_parse_ms",
            "projection_catch_up_ms", "fts_ms", "vector_ms", "fusion_ms",
            "authority_ms", "pending_ms", "top_k", "page_size",
            "scanned_assets", "vector_blocks", "returned_candidates",
            "first_page_hits", "binding",
            "encoder_identity", "model_binding_mismatch", "worker_pid",
            "worker_started_before_request", "worker_started_for_request",
            "search_publish_attempts",
        }
    }
    identity = safe_identity(payload.get("binding"))
    payload["model_identity"] = identity
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
