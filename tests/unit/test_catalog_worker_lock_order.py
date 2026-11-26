"""Exercise the real pool locks and Future callbacks in a watchdog-protected child."""

import faulthandler
import subprocess
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from photo_search.api import catalog_reads


def _run_order(case: str) -> None:
    before_registration = threading.Event()
    allow_registration = threading.Event()
    waiter_at_pool_lock = threading.Event()
    finish_work = threading.Event()
    completed_on_submit = case.startswith("completed-")
    close_in_gap = case == "close-with-queued-future"
    finish_in_gap = case == "finish-during-registration"

    class GatedExecutor(ThreadPoolExecutor):
        def submit(self, _query, /, *_args, **_kwargs):
            def query():
                finish_work.wait()
                return 17

            future = super().submit(query)
            if completed_on_submit:
                assert future.result() == 17
            register_callback = future.add_done_callback

            def observe_registration(callback):
                before_registration.set()
                allow_registration.wait()
                print(f"register: done={future.done()} pool_lock_owned={pool_lock._is_owned()}",
                      flush=True)

                def observe_completion(done):
                    print(f"callback: thread={threading.current_thread().name} "
                          f"pool_lock_owned={pool_lock._is_owned()}", flush=True)
                    callback(done)

                register_callback(observe_completion)

            future.add_done_callback = observe_registration
            self.submitted_future = future
            return future

    root = Path.cwd()
    owner = SimpleNamespace(db_path=root / "unused.db", data_root=root, clock=None, lease_seconds=30)
    with patch.object(catalog_reads, "ThreadPoolExecutor", GatedExecutor):
        pool = catalog_reads._CatalogWorkers(owner)
    owner._catalog_workers = pool
    assert pool._executor._max_workers == catalog_reads.CATALOG_WORKERS == 2
    pool_lock = pool._lock

    class ObservedPoolLock:
        def __enter__(self):
            if threading.current_thread() is waiter and not waiter_at_pool_lock.is_set():
                assert pool._activity._is_owned()
                available = pool_lock.acquire(blocking=False)
                if available:
                    pool_lock.release()
                print(f"waiter: activity_owned=True pool_lock_busy={not available}", flush=True)
                waiter_at_pool_lock.set()
            return pool_lock.__enter__()

        def __exit__(self, *exc):
            return pool_lock.__exit__(*exc)

    pool._lock = ObservedPoolLock()
    submitted = []
    idle = []
    submitter = threading.Thread(
        target=lambda: submitted.append(pool.submit(lambda _catalog: 17, (), {}, projection=False)),
        name="catalog-submit",
    )
    waiter = threading.Thread(
        target=lambda: idle.append(catalog_reads.wait_foreground_idle(owner, threading.Event())),
        name="foreground-idle",
    )

    faulthandler.dump_traceback_later(6, exit=True)
    if completed_on_submit:
        finish_work.set()
    if close_in_gap:
        # Occupy both real executor threads so close can cancel the queued submission.
        occupied = threading.Barrier(3)

        def hold_worker():
            occupied.wait()
            finish_work.wait()

        for _ in range(2):
            ThreadPoolExecutor.submit(pool._executor, hold_worker)
        occupied.wait()
    submitter.start()
    before_registration.wait()
    overlaps_registration = case in ("completed-with-waiter", "pending-with-waiter")
    if overlaps_registration:
        waiter.start()
        waiter_at_pool_lock.wait()
    if finish_in_gap:
        assert catalog_reads.foreground_busy(owner)
        assert not pool._executor.submitted_future.done()
        finish_work.set()
        assert pool._executor.submitted_future.result() == 17
    if close_in_gap:
        queued = pool._executor.submitted_future
        assert not queued.running() and not queued.done()
        assert catalog_reads.foreground_busy(owner)
        cancellation_observed = threading.Event()
        Future.add_done_callback(queued, lambda _done: cancellation_observed.set())
        closer = threading.Thread(target=pool.close, name="catalog-close")
        closer.start()
        cancellation_observed.wait()
        assert queued.cancelled()
        finish_work.set()
        closer.join()
        assert submitter.is_alive() and not submitted
        print("close: queued Future cancelled before callback registration", flush=True)
    allow_registration.set()
    submitter.join()
    assert len(submitted) == 1
    future, request = submitted[0]
    if close_in_gap:
        assert future.cancelled() and request.cancelled.is_set()
    elif not completed_on_submit and not finish_in_gap:
        if not overlaps_registration:
            waiter.start()
            waiter_at_pool_lock.wait()
        assert catalog_reads.foreground_busy(owner)
        assert pool._requests == {future: request}
        assert not future.done() and not idle
        finish_work.set()
    elif case == "completed-before-waiter":
        waiter.start()

    if waiter.ident is not None:
        waiter.join()
        assert idle == [True]
    if not close_in_gap:
        assert future.result() == 17
    pool.close()
    assert not catalog_reads.foreground_busy(owner)
    assert not pool._requests
    assert not (root / "unused.db").exists()
    faulthandler.cancel_dump_traceback_later()
    print("COMPLETE: no active requests; pool closed", flush=True)


@pytest.mark.parametrize("case", [
    "completed-with-waiter",
    "pending-with-waiter",
    "completed-without-waiter",
    "completed-before-waiter",
    "worker-completion",
    "finish-during-registration",
    "close-with-queued-future",
])
def test_completion_callback_and_foreground_idle_lock_order(case, tmp_path):
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), case],
        cwd=tmp_path, capture_output=True, text=True, timeout=20, check=False,
    )
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "COMPLETE: no active requests; pool closed" in result.stdout
    assert result.stdout.count("callback: thread=") == 1
    if case in ("pending-with-waiter", "worker-completion"):
        assert "callback: thread=catalog-worker" in result.stdout
    else:
        assert "callback: thread=catalog-submit pool_lock_owned=False" in result.stdout
    if case == "close-with-queued-future":
        assert "close: queued Future cancelled before callback registration" in result.stdout


if __name__ == "__main__":
    _run_order(sys.argv[1])
