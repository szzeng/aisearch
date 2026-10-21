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
    "qwen3-vl-embedding-2b-1024d-exif-v2",
})


def enabled() -> bool:
    return os.environ.get(PERF_ENV, "").strip() == "1"


def started() -> float:
    return time.perf_counter()


def elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


def add_duration(metrics: dict[str, Any] | None, name: str, duration_ms: float) -> None:
    if metrics is None:
        return
    metrics[name] = round(float(metrics.get(name, 0.0)) + float(duration_ms), 3)


def timed_call(metrics: dict[str, Any] | None, name: str, call: Any, *args: Any,
               **kwargs: Any) -> Any:
    if metrics is None:
        return call(*args, **kwargs)
    started_at = time.perf_counter()
    try:
        return call(*args, **kwargs)
    finally:
        add_duration(metrics, name, (time.perf_counter() - started_at) * 1000)


def sql_trace_callback(metrics: dict[str, Any]) -> Any:
    """Count statement shapes only; SQLite's expanded SQL text is never retained."""
    def trace(statement: str) -> None:
        metrics["catalog_sql_statements"] = int(metrics.get("catalog_sql_statements", 0)) + 1
        keyword = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
        if keyword in {"SELECT", "WITH", "PRAGMA", "EXPLAIN"}:
            metrics["catalog_sql_reads"] = int(metrics.get("catalog_sql_reads", 0)) + 1
    return trace


def phase_tree(metrics: dict[str, Any], total_ms: float) -> dict[str, Any]:
    """Build a non-overlapping accounting view; detailed children are explicitly nested."""
    worker = {
        key: metrics.get(key) for key in
        ("catalog_queue_ms", "catalog_open_ms", "catalog_close_ms")
        if isinstance(metrics.get(key), (int, float))
    }
    preparation = {
        key: metrics.get(key) for key in
        ("place_aware_prepare_ms", "search_prepare_ms", "intent_collect_ms",
         "intent_validate_ms", "natural_intent_ms", "place_choices_ms", "place_index_ms",
         "place_sql_ms", "place_index_exclusive_ms", "place_link_merge_ms",
         "place_name_normalize_ms", "place_stored_key_prepare_ms",
         "place_name_match_ms", "place_candidate_construct_ms", "place_python_other_ms")
        if isinstance(metrics.get(key), (int, float))
    }
    retrieval = {
        key: metrics.get(key) for key in
        ("retrieval_total_ms", "query_encode_ms", "projection_catch_up_ms", "fts_ms",
         "vector_ms", "fusion_ms", "authority_ms", "pending_ms")
        if isinstance(metrics.get(key), (int, float))
    }
    postprocess = {
        key: metrics.get(key) for key in
        ("entries_build_ms", "rerank_ms", "session_build_ms", "page_render_ms",
         "publication_check_ms", "session_register_ms", "response_build_ms",
         "place_aware_finalize_ms")
        if isinstance(metrics.get(key), (int, float))
    }
    # Only sequential top-level spans contribute. Nested intent/place and retrieval
    # measurements are details of their parent spans and are intentionally excluded.
    accounting_names = (
        "catalog_queue_ms", "catalog_open_ms", "place_aware_prepare_ms",
        "search_prepare_ms", "retrieval_total_ms", "entries_build_ms", "rerank_ms",
        "session_build_ms", "page_render_ms", "publication_check_ms",
        "session_register_ms", "response_build_ms", "place_aware_finalize_ms",
        "catalog_close_ms",
    )
    accounted = round(sum(float(metrics[name]) for name in accounting_names
                          if isinstance(metrics.get(name), (int, float))), 3)
    total = round(float(total_ms), 3)
    unaccounted = round(total - accounted, 3)
    return {
        "total_ms": total,
        "catalog_worker": worker,
        "preparation": preparation,
        "retrieval": retrieval,
        "postprocess": postprocess,
        "accounting": {
            "non_overlapping_ms": accounted,
            "unaccounted_ms": unaccounted,
            "coverage_ratio": None if total <= 0 else round(accounted / total, 4),
            "over_accounted": unaccounted < -0.5,
            "not_measured": ["HTTP response serialization and network transfer"],
        },
    }


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
        "processor_id": binding.get("processor_id"),
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
            "search_publish_attempts",
            "catalog_queue_ms", "catalog_open_ms", "catalog_close_ms",
            "catalog_connection_count", "catalog_sql_statements", "catalog_sql_reads",
            "place_aware_prepare_ms", "place_aware_finalize_ms", "intent_collect_ms",
            "intent_validate_ms", "natural_intent_ms", "place_choices_ms",
            "place_choices_call_ms", "place_choices_calls", "place_index_ms",
            "place_index_calls", "place_index_exclusive_ms", "place_sql_ms",
            "place_sql_queries", "place_sql_rows", "place_link_merge_ms",
            "place_name_normalize_ms", "place_stored_key_prepare_ms",
            "place_name_match_ms", "place_candidate_construct_ms", "place_python_other_ms",
            "place_spellings_normalized", "place_spellings_reused_normalized",
            "place_geo_candidate_objects",
            "place_alias_queries", "place_alias_rows", "place_mapping_queries",
            "place_admin_rows", "place_osm_rows", "place_geonames_rows",
            "place_link_rows", "place_full_candidates", "place_matched_candidates",
            "place_choice_runs", "search_execution_ms",
            "search_prepare_ms", "retrieval_total_ms", "retrieval_calls",
            "entries_build_ms", "rerank_ms", "session_build_ms", "page_render_ms",
            "publication_check_ms", "session_register_ms", "response_build_ms",
            "phase_tree",
        }
    }
    identity = safe_identity(payload.get("binding"))
    payload["model_identity"] = identity
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
