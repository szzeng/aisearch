from __future__ import annotations

import json

import pytest

from photo_search.search import perf_diagnostics


@pytest.mark.parametrize("provider", sorted(perf_diagnostics.SUPPORTED_PROVIDERS))
def test_perf_identity_uses_bound_space_for_all_supported_models(provider: str) -> None:
    identity = perf_diagnostics.safe_identity({
        "profile_id": "active-profile",
        "generation_id": "generation-1",
        "space_id": provider + ":space-1",
        "dimension": 512,
    })

    assert identity == {
        "active_profile_id": "active-profile",
        "active_image_generation_id": "generation-1",
        "provider_id": provider,
        "space_id": provider + ":space-1",
        "dimension": 512,
        "encoder_identity": provider,
    }


def test_perf_identity_rejects_unrecognized_or_unbound_spaces() -> None:
    assert perf_diagnostics.safe_identity(None) is None
    assert perf_diagnostics.safe_identity({"space_id": "unknown:space", "dimension": 512}) is None
    assert perf_diagnostics.safe_identity({"space_id": "qwen3-vl-embedding-2b", "dimension": 512}) is None


def test_model_binding_mismatch_is_explicit_without_worker_calls() -> None:
    binding = {"processor_id": "qwen-processor", "space_id": "qwen:space", "dimension": 512}
    encoder = {"processor_id": "qwen-processor", "space_id": "qwen:space", "dimension": 512}

    assert perf_diagnostics.binding_mismatch(binding, encoder) is False
    assert perf_diagnostics.binding_mismatch(binding, {**encoder, "space_id": "clip:space"}) is True
    assert perf_diagnostics.binding_mismatch(binding, {**encoder, "dimension": 768}) is True
    assert perf_diagnostics.binding_mismatch(binding, None) is None


def test_diagnostic_export_metadata_is_allow_listed_and_query_free() -> None:
    encoded = perf_diagnostics.encode_metrics({
        "binding": {"profile_id": "p1", "generation_id": "g1",
                    "space_id": "chinese-clip-vit-b-16:space", "dimension": 512},
        "query_encode_ms": 12.5,
        "worker_pid": 123,
        "original_query": "private beach search",
        "local_path": "D:/private/photo.jpg",
    })
    payload = json.loads(encoded)

    assert payload["model_identity"]["provider_id"] == "chinese-clip-vit-b-16"
    assert payload["query_encode_ms"] == 12.5
    assert payload["worker_pid"] == 123
    assert "original_query" not in payload
    assert "local_path" not in payload
    assert "private beach search" not in encoded


def test_server_timing_contains_only_numeric_stage_values() -> None:
    value = perf_diagnostics.server_timing({"query_encode_ms": 4.25, "bad_ms": "skip"}, 8.5)

    assert "query_encode_ms;dur=4.250" in value
    assert "search_execution_ms;dur=8.500" in value
    assert "bad_ms" not in value


def test_server_gate_requires_explicit_environment_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(perf_diagnostics.PERF_ENV, raising=False)
    assert perf_diagnostics.enabled() is False
    monkeypatch.setenv(perf_diagnostics.PERF_ENV, "true")
    assert perf_diagnostics.enabled() is False
    monkeypatch.setenv(perf_diagnostics.PERF_ENV, "1")
    assert perf_diagnostics.enabled() is True


def test_perf_config_endpoint_is_gated_by_server_environment(api_env, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario(client) -> None:
        await api_env.authenticate(client)
        monkeypatch.delenv(perf_diagnostics.PERF_ENV, raising=False)
        disabled = await client.get("/api/v1/search-perf/config")
        assert disabled.status_code == 200
        assert disabled.json() == {"enabled": False}
        monkeypatch.setenv(perf_diagnostics.PERF_ENV, "1")
        enabled = await client.get("/api/v1/search-perf/config")
        assert enabled.status_code == 200
        assert enabled.json() == {"enabled": True}

    api_env.scenario(scenario)


def test_place_aware_route_adds_diagnostic_headers_only_for_opted_in_requests(api_env, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario(client) -> None:
        csrf = await api_env.authenticate(client)
        payload = {"request_id": "perf-diag-synthetic", "retrieval_mode": "text",
                   "text": "synthetic", "original_query": "synthetic"}
        monkeypatch.setenv(perf_diagnostics.PERF_ENV, "1")
        headers = {**api_env.post_headers(csrf), "X-Search-Perf": "1"}
        measured = await client.post("/api/v1/search/place-aware", json=payload, headers=headers)
        assert measured.status_code == 200, measured.text
        assert "search_execution_ms;dur=" in measured.headers["Server-Timing"]
        meta = json.loads(measured.headers["X-Search-Perf-Meta"])
        assert meta["page_size"] == 20
        assert "synthetic" not in measured.headers["X-Search-Perf-Meta"]
        assert measured.json()["request_id"] == payload["request_id"]

        ordinary = await client.post(
            "/api/v1/search/place-aware", json={**payload, "request_id": "normal-search"},
            headers=api_env.post_headers(csrf),
        )
        assert ordinary.status_code == 200, ordinary.text
        assert "Server-Timing" not in ordinary.headers

    api_env.scenario(scenario)
