"""Concurrent startup never exposes a half-resolved model adapter."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from photo_search.api.context import ApiContext
from photo_search.api.settings import ApiSettings


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
        assert first.result(timeout=3) is instance
        assert other.result(timeout=3) is instance
    assert len(calls) == 1
    assert resolve() is instance
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
