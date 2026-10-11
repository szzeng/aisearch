from __future__ import annotations

import shutil
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from photo_search.api.media_generation import generate_one_in_worker


def test_same_asset_pre_warm_and_request_share_one_cold_decode(tmp_path) -> None:
    db_path = tmp_path / "catalog.db"
    with sqlite3.connect(db_path):
        pass
    service = SimpleNamespace(
        catalog=SimpleNamespace(db_path=db_path),
        data_root=tmp_path,
        disk_usage=shutil.disk_usage,
        wall_clock_ms=lambda: 1,
    )
    entered = threading.Event()
    release = threading.Event()
    state_lock = threading.Lock()
    published = False
    decode_count = 0

    def generate(_worker_service, _asset_id: int, _kind: str) -> None:
        nonlocal published, decode_count
        with state_lock:
            if published:
                return
            decode_count += 1
        entered.set()
        assert release.wait(timeout=5)
        with state_lock:
            published = True

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            generate_one_in_worker, service, 7, "thumbnail", generator=generate
        )
        assert entered.wait(timeout=5)
        second = executor.submit(
            generate_one_in_worker, service, 7, "thumbnail", generator=generate
        )
        release.set()
        first.result(timeout=5)
        second.result(timeout=5)

    assert decode_count == 1
