from __future__ import annotations

import threading

from photo_search.api.runner import ScanRunner


def test_scan_runner_notifies_cover_warmer_after_successful_queue_drains() -> None:
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    release_second = threading.Event()
    warmed = threading.Event()
    executed: list[int] = []

    def execute(_service, source_id: int):
        executed.append(source_id)
        if source_id == 1:
            first_started.set()
            assert release_first.wait(timeout=5)
        else:
            second_started.set()
            assert release_second.wait(timeout=5)
        return {"state": "SUCCEEDED"}

    runner = ScanRunner(
        lambda: object(),
        executor=execute,
        on_idle_after_success=warmed.set,
        name="test-place-cover-warm-trigger",
    )
    try:
        assert runner.submit(1)
        assert first_started.wait(timeout=5)
        assert runner.submit(2)
        release_first.set()
        assert second_started.wait(timeout=5)
        assert not warmed.is_set()
        release_second.set()
        assert runner.wait_idle(timeout=5)
        assert warmed.wait(timeout=5)
    finally:
        release_first.set()
        release_second.set()
        runner.close()

    assert executed == [1, 2]
