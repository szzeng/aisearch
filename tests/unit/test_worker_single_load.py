"""Worker startup loads each provider once through the public loaded factory."""
from __future__ import annotations

from pathlib import Path
from queue import Queue
from typing import Any
from unittest.mock import Mock

import pytest

from photo_search.catalog.errors import EMBEDDING_DEVICE_UNAVAILABLE, CatalogError
from photo_search.models.embedding import factory
from photo_search.workers import ai_worker


class CountingAdapter:
    def __init__(self, error: Exception | None = None) -> None:
        self.load_calls = 0
        self.error = error
        self.resolution = Mock()
        self.resolution.as_dict.return_value = {"device": "cuda", "precision": "FP32"}

    def load(self) -> None:
        self.load_calls += 1
        if self.error is not None:
            raise self.error

    def health(self) -> dict[str, Any]:
        return {"loaded": self.load_calls == 1, "runtime": {"runtime_backend": "stub"}}

    def encode_text(self, text: str) -> list[float]:
        assert self.load_calls == 1
        assert text == "synthetic query"
        return [0.6, 0.8]

    def spec(self) -> Any:
        result = Mock()
        result.as_dict.return_value = {"space_id": "synthetic-space", "dimension": 2}
        return result


def run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    adapter: CountingAdapter,
    *,
    provider: str = "clip",
    requests: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], Queue, Mock]:
    build = Mock(return_value=adapter)
    monkeypatch.setattr(factory, "build_adapter", build)
    monkeypatch.setattr(ai_worker.media_heif, "runtime_fact", lambda: {"available": False})
    monkeypatch.setattr(ai_worker, "prepare_framework_stack", lambda _: {"provider": provider})
    incoming: Queue = Queue()
    outgoing: Queue = Queue()
    for request in requests or [{"action": "shutdown"}]:
        incoming.put(request)
    ai_worker.run_worker(
        incoming,
        outgoing,
        str(tmp_path / "models"),
        str(tmp_path / f"{provider}.json"),
        str(tmp_path / "worker.stderr"),
        device="cuda",
        device_policy="gpu_required",
    )
    replies = []
    while not outgoing.empty():
        replies.append(outgoing.get_nowait())
    return replies, incoming, build


@pytest.mark.parametrize("provider", ["clip", "qwen"])
def test_worker_loads_once_before_health_and_inference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: str
) -> None:
    adapter = CountingAdapter()
    replies, incoming, build = run(
        monkeypatch,
        tmp_path,
        adapter,
        provider=provider,
        requests=[
            {"action": "health"},
            {"action": "encode_text", "text": "synthetic query"},
            {"action": "shutdown"},
            {"action": "encode_text", "text": "must remain queued after shutdown"},
        ],
    )
    assert adapter.load_calls == 1
    build.assert_called_once_with(
        tmp_path / "models", tmp_path / f"{provider}.json",
        device="cuda", device_policy=ai_worker.DevicePolicy.GPU_REQUIRED,
        enable_profiling=True,
    )
    assert [reply["status"] for reply in replies] == ["ok", "ok", "ok"]
    assert replies[0]["result"]["loaded"] is True
    assert replies[1]["result"]["vector"] == [0.6, 0.8]
    assert replies[2]["completed_steps"] == ["model_loaded", "shutdown"]
    assert incoming.qsize() == 1


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("missing synthetic weights"),
        CatalogError(EMBEDDING_DEVICE_UNAVAILABLE, "synthetic GPU failure", details={"reason": "TEST_FAILURE"}),
    ],
)
def test_load_failure_is_reported_once_before_requests_are_consumed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception
) -> None:
    adapter = CountingAdapter(error)
    replies, incoming, build = run(monkeypatch, tmp_path, adapter)
    assert adapter.load_calls == 1
    assert build.call_count == 1
    assert incoming.qsize() == 1
    assert len(replies) == 1
    assert replies[0]["status"] == "error"
    assert replies[0]["stage"] == "startup"
    assert replies[0]["exception_type"] == type(error).__name__
    assert replies[0]["completed_steps"] == []
    assert replies[0]["details"]["cpu_fallback"] == "disabled"
    if isinstance(error, CatalogError):
        assert replies[0]["details"]["code"] == EMBEDDING_DEVICE_UNAVAILABLE
        assert replies[0]["details"]["reason"] == "TEST_FAILURE"


def test_request_failure_does_not_reload_adapter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    adapter = CountingAdapter()
    replies, _, _ = run(
        monkeypatch, tmp_path, adapter,
        requests=[{"action": "unknown"}, {"action": "health"}, {"action": "shutdown"}],
    )
    assert adapter.load_calls == 1
    assert [reply["status"] for reply in replies] == ["error", "ok", "ok"]
    assert replies[0]["stage"] == "unknown"
    assert replies[0]["exception_type"] == "ProtocolError"
    assert replies[1]["result"]["loaded"] is True


def test_loaded_factory_contract_is_preserved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    adapter = CountingAdapter()
    build = Mock(return_value=adapter)
    monkeypatch.setattr(factory, "build_adapter", build)
    actual = factory.load_adapter(
        tmp_path / "models", tmp_path / "manifest.json", device="cpu",
        device_policy="cpu_preferred", enable_profiling=False,
    )
    assert actual is adapter and adapter.load_calls == 1
    build.assert_called_once_with(
        tmp_path / "models", tmp_path / "manifest.json", device="cpu",
        device_policy="cpu_preferred", enable_profiling=False,
    )
