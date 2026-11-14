"""Concurrent startup never exposes a half-resolved model adapter."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from photo_search.api.context import ApiContext
from photo_search.api.settings import ApiSettings
from photo_search.runtime.model_resource_gate import GatedEncoder


@pytest.mark.parametrize("kind", ["encoder", "reranker"])
def test_parallel_resolution_waits_for_one_complete_factory(tmp_path, kind):
    entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
    instance = object()
    calls = []

    def factory():
        calls.append(threading.get_ident())
        entered.set()
        assert release.wait(3)
        return instance

    context = ApiContext(ApiSettings(data_root=tmp_path), **{kind + "_factory": factory})
    resolve = context.query_encoder if kind == "encoder" else context.reranker

    def second():
        second_started.set()
        return resolve()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(resolve)
        try:
            assert entered.wait(2)
            other = pool.submit(second)
            assert second_started.wait(2)
            with pytest.raises(TimeoutError):
                other.result(timeout=0.05)
        finally:
            release.set()
        first_adapter = first.result(timeout=3)
        second_adapter = other.result(timeout=3)
        assert second_adapter is first_adapter
        if kind == "encoder":
            assert isinstance(first_adapter, GatedEncoder)
            assert first_adapter._encoder is instance
        else:
            assert first_adapter is instance
    assert len(calls) == 1
    assert resolve() is first_adapter
    context.close()
    assert resolve() is None


def test_shutdown_during_encoder_initialization_closes_created_adapter_once(tmp_path):
    entered, release = threading.Event(), threading.Event()
    closed = []

    class Adapter:
        def close(self):
            closed.append(True)

    adapter = Adapter()

    def factory():
        entered.set()
        assert release.wait(3)
        return adapter

    context = ApiContext(ApiSettings(data_root=tmp_path), encoder_factory=factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(context.query_encoder)
        try:
            assert entered.wait(2)
            stopped = pool.submit(context.close)
            with pytest.raises(TimeoutError):
                stopped.result(timeout=0.05)
        finally:
            release.set()
        first.result(timeout=3)
        stopped.result(timeout=3)
    context.close()
    assert closed == [True]
    assert context.query_encoder() is None


def test_gpu_cold_start_and_candidate_follow_slot_before_adapter_lock(tmp_path, monkeypatch):
    from photo_search.face import service as face_service

    slot = threading.RLock()
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", slot)
    entered, release, candidate_entered = threading.Event(), threading.Event(), threading.Event()

    class Adapter:
        def close(self):
            pass

    def factory():
        entered.set()
        assert release.wait(3)
        return Adapter()
    factory.requires_gpu_slot = True
    context = ApiContext(ApiSettings(data_root=tmp_path), encoder_factory=factory)

    def candidate():
        with slot, context.model_resource_gate.exclusive(timeout=2):
            context.release_query_encoder_for_candidate()
            candidate_entered.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        query = pool.submit(context.query_encoder)
        try:
            assert entered.wait(2)
            build = pool.submit(candidate)
            assert not candidate_entered.wait(0.1)
        finally:
            release.set()
        assert isinstance(query.result(timeout=3), GatedEncoder)
        build.result(timeout=3)
    assert candidate_entered.is_set()
    context.close()


def test_replaced_encoder_proxy_refuses_access(tmp_path):
    from photo_search.catalog.errors import CatalogError

    class Adapter:
        def encode_queries(self, texts, cancellation=None):
            pytest.fail("a replaced adapter must not receive a query")
        def close(self):
            pass

    context = ApiContext(ApiSettings(data_root=tmp_path), encoder_factory=Adapter)
    proxy = context.query_encoder()
    with context.model_resource_gate.exclusive(timeout=1):
        context.release_query_encoder_for_candidate()
    with pytest.raises(CatalogError) as error:
        proxy.encode_queries(["query"])
    assert error.value.details["reason"] == "MODEL_RESOURCE_BUSY"
    context.close()


def test_search_intent_survives_cold_start_until_first_encode(tmp_path, monkeypatch):
    from photo_search.face import service as face_service
    from photo_search.runtime.background_slot import foreground_model_waiting

    slot = threading.RLock()
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", slot)
    resolved, run_query, encoded = threading.Event(), threading.Event(), threading.Event()
    outcomes = []

    class Adapter:
        requires_gpu_slot = True
        def encode_queries(self, texts, cancellation=None):
            encoded.set()
            return texts
        def close(self):
            pass
    def factory():
        return Adapter()
    factory.requires_gpu_slot = True
    context = ApiContext(ApiSettings(data_root=tmp_path), encoder_factory=factory)
    def search():
        try:
            with context.search_model_intent():
                proxy = context.query_encoder()
                resolved.set()
                assert run_query.wait(3)
                outcomes.append(proxy.encode_queries(["query"]))
        except Exception as error:
            outcomes.append(error)
    worker = threading.Thread(target=search, daemon=True)
    worker.start()
    try:
        assert resolved.wait(3)
        assert foreground_model_waiting()
        assert slot.acquire(timeout=1)
        slot.release()
        run_query.set()
        worker.join(3)
        assert not worker.is_alive() and encoded.is_set()
        assert outcomes == [["query"]]
        assert not foreground_model_waiting()
    finally:
        run_query.set()
        worker.join(3)
        context.close()


def test_search_intent_is_released_on_early_failure(tmp_path, monkeypatch):
    from photo_search.face import service as face_service
    from photo_search.runtime.background_slot import foreground_model_waiting

    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", threading.RLock())
    context = ApiContext(ApiSettings(data_root=tmp_path), encoder_factory=lambda: object())
    with pytest.raises(ValueError):
        with context.search_model_intent():
            assert foreground_model_waiting()
            raise ValueError("before encode")
    assert not foreground_model_waiting()
    context.close()


def test_explicit_cpu_query_does_not_wait_for_gpu_slot(monkeypatch):
    from photo_search.face import service as face_service
    from photo_search.runtime.model_resource_gate import ModelResourceGate

    slot = threading.RLock()
    monkeypatch.setattr(face_service, "BACKGROUND_MODEL_SLOT", slot)
    held, release, ran = threading.Event(), threading.Event(), threading.Event()
    def background():
        with slot:
            held.set()
            assert release.wait(3)
    class CpuEncoder:
        requires_gpu_slot = False
        def encode_queries(self, texts, cancellation=None):
            ran.set()
            return texts
    worker = threading.Thread(target=background, daemon=True)
    worker.start()
    try:
        assert held.wait(2)
        assert GatedEncoder(CpuEncoder(), ModelResourceGate()).encode_queries(["x"]) == ["x"]
        assert ran.is_set()
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()


def test_filter_only_search_does_not_advertise_gpu_intent(monkeypatch):
    from types import SimpleNamespace

    from photo_search.api import routes
    from photo_search.search.contracts import SearchRequest

    def unexpected():
        pytest.fail("filter-only search must not register GPU intent")
    context = SimpleNamespace(
        search_model_intent=unexpected, query_encoder=lambda: None,
        reranker=lambda: None, search_sessions=object(),
    )
    monkeypatch.setattr(routes, "search_request", lambda *_args, **_kwargs: {"ok": True})
    request = SearchRequest(request_id="filter-only", text=None, retrieval_mode="combined")
    assert routes._search_request_worker(None, request, context=context) == {"ok": True}
