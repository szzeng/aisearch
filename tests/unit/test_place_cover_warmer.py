from __future__ import annotations

import threading
import time

from photo_search.api import place_cover_warmer
from photo_search.api.place_cover_warmer import PlaceCoverWarmer


class FakeService:
    def __init__(self) -> None:
        self.catalog = object()
        self.data_root = "synthetic-data-root"
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_place_cover_warmer_uses_top_four_covers_per_kind_and_deduplicates(monkeypatch) -> None:
    service = FakeService()
    generated: list[tuple[int, str, int]] = []
    owner_thread = threading.get_ident()
    state_lock = threading.Lock()
    active = 0
    max_active = 0
    albums_by_kind = {
        "city": [
            {"cover_asset_id": asset_id} for asset_id in (1, 2, 3, 4, 5)
        ],
        "province": [
            {"cover_asset_id": asset_id} for asset_id in (4, 6, 7, 8, 9)
        ],
        "district": [{"cover_asset_id": 10}],
        "country": [{"cover_asset_id": 11}],
    }

    monkeypatch.setattr(
        place_cover_warmer.place_albums,
        "albums",
        lambda _catalog, _root, *, kind: {"albums": albums_by_kind[kind]},
    )
    monkeypatch.setattr(
        place_cover_warmer,
        "resolve_present_location",
        lambda _catalog, _asset_id: None,
    )

    def generate(_service, asset_id: int, kind: str) -> None:
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        generated.append((asset_id, kind, threading.get_ident()))
        with state_lock:
            active -= 1

    monkeypatch.setattr(place_cover_warmer, "generate_one_in_worker", generate)
    warmer = PlaceCoverWarmer(lambda: service)
    try:
        assert warmer.request()
        assert warmer.wait_idle(timeout=5)
    finally:
        warmer.close()

    assert sorted(asset_id for asset_id, _, _ in generated) == [1, 2, 3, 4, 6, 7, 8, 10, 11]
    assert all(kind == "thumbnail" for _, kind, _ in generated)
    assert all(thread_id != owner_thread for _, _, thread_id in generated)
    assert max_active == 2
    assert service.closed


def test_place_cover_warmer_coalesces_triggers_during_a_warm_pass(monkeypatch) -> None:
    service = FakeService()
    entered = threading.Event()
    release = threading.Event()
    passes = 0

    def albums(_catalog, _root, *, kind):
        nonlocal passes
        if kind == "city":
            passes += 1
            if passes == 1:
                entered.set()
                assert release.wait(timeout=5)
        return {"albums": []}

    monkeypatch.setattr(place_cover_warmer.place_albums, "albums", albums)
    warmer = PlaceCoverWarmer(lambda: service)
    try:
        assert warmer.request()
        assert entered.wait(timeout=5)
        assert warmer.request()
        assert not warmer.request()
        release.set()
        assert warmer.wait_idle(timeout=5)
    finally:
        release.set()
        warmer.close()

    assert passes == 2
    assert service.closed
