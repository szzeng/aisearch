from __future__ import annotations

import datetime as dt
import hashlib
import threading
import time

import pytest

from photo_search.api import memories as memories_module
from photo_search.api.catalog_reads import _workers
from photo_search.api.memories import (
    ALGORITHM_VERSION,
    MAX_CACHE_ENTRIES,
    MemoriesBudgetExceeded,
    MemoriesCancelled,
    MemoriesRunner,
    _day_candidates,
    _day_range,
    _month_candidates,
    _range_rows,
    _spread,
)
from photo_search.catalog.db import Catalog
from photo_search.taken_time import contract


@pytest.fixture
def catalog(tmp_path):
    instance = Catalog.open(data_root=tmp_path / "data")
    with instance.write() as connection:
        connection.execute(
            "INSERT INTO source_roots(display_name,root_path,status) VALUES('synthetic','/synthetic','ONLINE')"
        )
    yield instance
    instance.close()


def _asset(catalog, name: str, capture: dt.datetime | None, *, source="EXIF_ORIGINAL",
           present=True, extension="jpg", offline_photo_with_present_video=False) -> int:
    digest = hashlib.sha256(name.encode()).hexdigest()
    with catalog.write() as connection:
        cursor = connection.execute(
            "INSERT INTO assets(sha256,byte_size,created_at,state) VALUES(?,1,1700000000000,'ACTIVE')",
            (digest,),
        )
        asset_id = int(cursor.lastrowid)
        if capture is not None:
            value = (None if source == "FILE_MTIME" else contract.civil_key(
                capture.year, capture.month, capture.day, capture.hour, capture.minute, capture.second
            ))
            confidence = {"EXIF_ORIGINAL": "HIGH", "EXIF_DIGITIZED": "MEDIUM",
                          "EXIF_ROOT": "LOW", "FILE_MTIME": "LOW"}[source]
            connection.execute(
                "INSERT INTO asset_metadata(asset_id,taken_at_sort,time_source,time_confidence) "
                "VALUES(?,?,?,?)", (asset_id, value, source, confidence),
            )
        if present:
            connection.execute(
                "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,availability) "
                "VALUES(1,?,?,?,1,1,'PRESENT')", (f"{name}.{extension}", f"{name}.{extension}", asset_id),
            )
        if offline_photo_with_present_video:
            connection.execute(
                "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,availability) "
                "VALUES(1,?,?,?,1,1,'MISSING')", (f"{name}.jpg", f"{name}.jpg", asset_id),
            )
    return asset_id


def test_memories_dense_day_and_month_keep_later_time_slots_and_dates(catalog):
    late_day_id = 301
    with catalog.write() as connection:
        for asset_id in range(1, 304):
            connection.execute(
                "INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) "
                "VALUES(?,?,1,1700000000000,'ACTIVE')",
                (asset_id, f"{asset_id:064x}"),
            )
            if asset_id <= 300:
                capture = dt.datetime(2025, 10, 4, 8, 0) + dt.timedelta(seconds=asset_id)
            elif asset_id == late_day_id:
                capture = dt.datetime(2025, 10, 4, 20, 30)
            else:
                capture = dt.datetime(2025, 10, asset_id - 295, 14, 0)
            connection.execute(
                "INSERT INTO asset_metadata(asset_id,taken_at_sort,time_source,time_confidence) "
                "VALUES(?,?,'EXIF_ORIGINAL','HIGH')",
                (asset_id, contract.civil_key(capture.year, capture.month, capture.day,
                    capture.hour, capture.minute, capture.second)),
            )
            name = f"dense-{asset_id}.jpg"
            connection.execute(
                "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,availability) "
                "VALUES(1,?,?,?,1,1,'PRESENT')", (name, name, asset_id),
            )

    day_rows = _day_candidates(catalog.connection, 2025, 10, 4)
    assert len(day_rows) <= 240
    selected = _spread(day_rows)
    assert late_day_id in {int(row["asset_id"]) for row in day_rows}
    assert late_day_id in selected

    month_rows = _month_candidates(catalog.connection, 2025, 10)
    month_days = {contract.normalize_bound(int(row["taken_at_sort"]))[:10] for row in month_rows}
    assert len(month_rows) <= 62  # One result per date and EXIF source.
    assert {"2025-10-04", "2025-10-07", "2025-10-08"}.issubset(month_days)


def test_memories_use_only_active_present_images_with_trusted_capture_date(catalog):
    valid_day = _asset(catalog, "valid-day", dt.datetime(2025, 10, 23, 9, 15))
    _asset(catalog, "valid-month", dt.datetime(2025, 10, 4, 14, 0), source="EXIF_DIGITIZED")
    _asset(catalog, "mtime", dt.datetime(2025, 10, 23), source="FILE_MTIME")
    _asset(catalog, "root", dt.datetime(2025, 10, 23), source="EXIF_ROOT")
    _asset(catalog, "offline", dt.datetime(2025, 10, 23), present=False)
    _asset(catalog, "video", dt.datetime(2025, 10, 23), extension="mp4")
    _asset(catalog, "offline-image-only", dt.datetime(2025, 10, 23), extension="mp4",
           offline_photo_with_present_video=True)

    runner = MemoriesRunner(catalog, max_years=3)
    try:
        first = runner.card_state(catalog, now=dt.date(2026, 10, 23))
        assert first["status"] in {"QUEUED", "RUNNING", "PARTIAL"}
        assert runner.wait_idle()
        result = runner.card_state(catalog, now=dt.date(2026, 10, 23))
        assert result["status"] in {"QUEUED", "RUNNING", "PARTIAL", "MORE"}
        assert [(item["year"], item["selected_count"]) for item in result["cards"]["day"]] == [(2025, 1)]
        assert [(item["year"], item["selected_count"]) for item in result["cards"]["month"]] == [(2025, 2)]
        preview = runner.preview_state(catalog, memory_type="day", year=2025, now=dt.date(2026, 10, 23))
        assert preview["status"] in {"QUEUED", "RUNNING"}
        assert runner.wait_idle()
        ready = runner.preview_state(catalog, memory_type="day", year=2025, now=dt.date(2026, 10, 23))
        assert ready["status"] == "READY"
        assert [item["asset_id"] for item in ready["assets"]] == [valid_day]
        assert ready["all_range"] == {"capture_from": "2025-10-23", "capture_to_exclusive": "2025-10-24"}
        month_preview = runner.preview_state(catalog, memory_type="month", year=2025, now=dt.date(2026, 10, 23))
        assert month_preview["status"] in {"QUEUED", "RUNNING"}
        assert runner.wait_idle()
        month_ready = runner.preview_state(catalog, memory_type="month", year=2025, now=dt.date(2026, 10, 23))
        assert month_ready["status"] == "READY"
        assert month_ready["selected_count"] == result["cards"]["month"][0]["selected_count"]
    finally:
        runner.close()


def test_memories_date_ranges_and_capture_index_are_bounded(catalog):
    leap = _asset(catalog, "leap", dt.datetime(2024, 2, 29, 23, 59))
    connection = catalog.connection
    leap_range = _day_range(2024, 2, 29)
    assert leap_range is not None
    assert _day_range(2025, 2, 29) is None
    rows = _range_rows(connection, *leap_range, 240)
    assert [int(row["asset_id"]) for row in rows] == [leap]

    low, high = leap_range
    sql = (
        "SELECT m.asset_id,m.taken_at_sort FROM asset_metadata AS m "
        "INDEXED BY idx_asset_metadata_capture CROSS JOIN assets AS a ON a.asset_id=m.asset_id "
        "WHERE m.time_source=? AND m.taken_at_sort>=? AND m.taken_at_sort<? "
        "AND a.state='ACTIVE' ORDER BY m.taken_at_sort,m.asset_id LIMIT ?"
    )
    plan = [str(row["detail"]) for row in connection.execute(
        "EXPLAIN QUERY PLAN " + sql, ("EXIF_ORIGINAL", low, high, 240)
    )]
    assert any("idx_asset_metadata_capture" in item for item in plan)
    assert not any("SCAN asset_metadata" in item for item in plan)


def test_memories_cache_is_bounded_and_revision_invalidates(catalog):
    runner = MemoriesRunner(catalog, max_years=1)
    try:
        today = dt.date(2026, 10, 23)
        for year in range(1, MAX_CACHE_ENTRIES + 5):
            runner._remember((today.isoformat(), "day", year, 1, 1, contract.RULES_VERSION,
                              ALGORITHM_VERSION), {"memory_type": "day", "year": year, "selected_count": 1})
        assert runner.cache_size == MAX_CACHE_ENTRIES
        before = runner.card_state(catalog, now=today)
        assert before["cache_entries"] <= MAX_CACHE_ENTRIES
        with catalog.write() as connection:
            connection.execute("UPDATE catalog_filter_revision SET revision=revision+1 WHERE revision_id=1")
        after = runner.card_state(catalog, now=today)
        assert after["cards"] == {"day": [], "month": []}
        with catalog.write() as connection:
            connection.execute("UPDATE metadata SET value=CAST(value AS INTEGER)+1 WHERE key='favorites_revision'")
        after_favorite = runner.card_state(catalog, now=today)
        assert after_favorite["cards"] == {"day": [], "month": []}
    finally:
        runner.close()


def test_memories_discovery_autonomously_processes_the_initial_ten_year_window(catalog, monkeypatch):
    years = []
    years_lock = threading.Lock()

    def day_candidates(_connection, year, *_args, **_kwargs):
        with years_lock:
            years.append(int(year))
        return []

    monkeypatch.setattr(memories_module, "_day_candidates", day_candidates)
    monkeypatch.setattr(memories_module, "_month_candidates", lambda *_args, **_kwargs: [])
    runner = MemoriesRunner(catalog)
    try:
        today = dt.date(2026, 10, 23)
        first = runner.card_state(catalog, now=today)
        assert first["status"] in {"QUEUED", "RUNNING", "PARTIAL"}
        assert runner.wait_idle(timeout=5)
        assert years == list(range(2025, 2015, -1))
        result = runner.card_state(catalog, now=today)
        assert result["status"] == "MORE"
        assert result["next_cursor"]
        assert runner._next_year == 2015
        assert years == list(range(2025, 2015, -1))  # Hot state read reused results without scheduling SQL.
    finally:
        runner.close()


def test_memories_reentry_without_cursor_restores_first_window_after_paging(catalog, monkeypatch):
    years = []

    def day_candidates(_connection, year, *_args, **_kwargs):
        years.append(int(year))
        return []

    monkeypatch.setattr(memories_module, "_day_candidates", day_candidates)
    monkeypatch.setattr(memories_module, "_month_candidates", lambda *_args, **_kwargs: [])
    runner = MemoriesRunner(catalog, max_years=1)
    today = dt.date(2026, 10, 23)
    try:
        runner.card_state(catalog, now=today)
        assert runner.wait_idle()
        first_window = runner.card_state(catalog, now=today)
        assert first_window["status"] == "MORE"
        assert first_window["next_cursor"]

        runner.card_state(catalog, cursor=first_window["next_cursor"], now=today)
        assert runner.wait_idle()
        older_window = runner.card_state(catalog, cursor=first_window["next_cursor"], now=today)
        assert runner._window_start_year == 2024
        assert older_window["status"] == "MORE"

        reentered = runner.card_state(catalog, now=today)
        assert runner._window_start_year == 2025
        assert runner._next_year == 2025
        assert reentered["status"] in {"QUEUED", "RUNNING", "MORE"}
    finally:
        runner.close()


def test_memories_reentry_without_cursor_restores_first_window_after_page_cancel(catalog, monkeypatch):
    second_year_started = threading.Event()
    release_second_year = threading.Event()
    years = []

    def day_candidates(_connection, year, *_args, cancelled=None, **_kwargs):
        years.append(int(year))
        if year == 2024:
            second_year_started.set()
            assert release_second_year.wait(3)
            if cancelled is not None and cancelled.is_set():
                raise MemoriesCancelled()
        return []

    monkeypatch.setattr(memories_module, "_day_candidates", day_candidates)
    monkeypatch.setattr(memories_module, "_month_candidates", lambda *_args, **_kwargs: [])
    runner = MemoriesRunner(catalog, max_years=1)
    today = dt.date(2026, 10, 23)
    try:
        runner.card_state(catalog, now=today)
        assert runner.wait_idle()
        first_window = runner.card_state(catalog, now=today)
        assert first_window["next_cursor"]

        runner.card_state(catalog, cursor=first_window["next_cursor"], now=today)
        assert second_year_started.wait(2)
        assert runner.cancel()["status"] == "CANCELLING"
        release_second_year.set()
        assert runner.wait_idle()
        assert runner._status == "CANCELLED"

        reentered = runner.card_state(catalog, now=today)
        assert runner._window_start_year == 2025
        assert runner._next_year == 2025
        assert reentered["status"] in {"QUEUED", "RUNNING"}
    finally:
        release_second_year.set()
        runner.close()


def test_memories_deferred_discovery_resumes_with_backoff(catalog, monkeypatch):
    busy_checks = 0
    years = []

    def busy_once(_connection):
        nonlocal busy_checks
        busy_checks += 1
        return busy_checks == 1

    def day_candidates(_connection, year, *_args, **_kwargs):
        years.append(int(year))
        return []

    monkeypatch.setattr(MemoriesRunner, "_high_priority_busy", staticmethod(busy_once))
    monkeypatch.setattr(memories_module, "_day_candidates", day_candidates)
    monkeypatch.setattr(memories_module, "_month_candidates", lambda *_args, **_kwargs: [])
    runner = MemoriesRunner(catalog, max_years=2)
    try:
        runner.card_state(catalog, now=dt.date(2026, 10, 23))
        assert runner.wait_idle(timeout=5)
        assert years == [2025, 2024]
        assert busy_checks == 3
    finally:
        runner.close()


def test_memories_preview_preempts_then_resumes_the_unfinished_year(catalog, monkeypatch):
    _asset(catalog, "priority-preview", dt.datetime(2025, 10, 23, 12, 0))
    discovery_started = threading.Event()
    preview_started = threading.Event()
    release_preview = threading.Event()
    call_lock = threading.Lock()
    calls = 0
    original = memories_module._day_candidates

    def interruptible_candidates(connection, year, *args, cancelled=None, **kwargs):
        nonlocal calls
        with call_lock:
            calls += 1
            current = calls
        if current == 1:
            discovery_started.set()
            assert cancelled is not None and cancelled.wait(3)
            raise MemoriesCancelled()
        if current == 2:
            preview_started.set()
            assert release_preview.wait(3)
        return original(connection, year, *args, cancelled=cancelled, **kwargs)

    monkeypatch.setattr(memories_module, "_day_candidates", interruptible_candidates)
    runner = MemoriesRunner(catalog, max_years=1)
    today = dt.date(2026, 10, 23)
    try:
        runner.card_state(catalog, now=today)
        assert discovery_started.wait(2)
        assert runner.preview_state(catalog, memory_type="day", year=2025, now=today)["status"] in {
            "QUEUED", "RUNNING"
        }
        assert preview_started.wait(2)
        assert runner.card_state(catalog, now=today)["status"] == "RUNNING"
        release_preview.set()
        assert runner.wait_idle(timeout=5)
        assert calls == 3  # interrupted discovery, priority preview, resumed discovery
        assert runner.preview_state(catalog, memory_type="day", year=2025, now=today)["status"] == "READY"
        result = runner.card_state(catalog, now=today)
        assert [(item["year"], item["selected_count"]) for item in result["cards"]["day"]] == [(2025, 1)]
    finally:
        release_preview.set()
        runner.close()


def test_memories_completed_cache_has_no_wall_clock_expiry_and_lru_protects_cards(catalog):
    runner = MemoriesRunner(catalog)
    try:
        today = dt.date(2026, 10, 23).isoformat()
        for year in range(2025, 2015, -1):
            runner._remember((today, "day", year, 0, 0, contract.RULES_VERSION,
                              ALGORITHM_VERSION), {"memory_type": "day", "year": year})
            runner._remember((today, "month", year, 0, 0, contract.RULES_VERSION,
                              ALGORITHM_VERSION), {"memory_type": "month", "year": year})
        for year in range(2025, 2012, -1):
            runner._remember((today, "day", year, 0, 0, 0, 1, ALGORITHM_VERSION), {"assets": []})
        time.sleep(1.05)
        assert runner.cache_size == MAX_CACHE_ENTRIES
        assert all(runner._cached((today, kind, year, 0, 0, contract.RULES_VERSION,
                                  ALGORITHM_VERSION)) is not None
                   for year in range(2025, 2015, -1) for kind in ("day", "month"))
    finally:
        runner.close()


def test_memories_worker_waits_for_foreground_catalog_read(catalog, monkeypatch):
    foreground_started = threading.Event()
    release_foreground = threading.Event()
    background_query_started = threading.Event()

    def foreground_query(_worker):
        foreground_started.set()
        assert release_foreground.wait(3)
        return "foreground-finished"

    def tracked_range(*args, **kwargs):
        background_query_started.set()
        return []

    monkeypatch.setattr(memories_module, "_range_rows", tracked_range)
    pool = _workers(catalog)
    future, _request = pool.submit(foreground_query, (), {}, projection=False)
    assert foreground_started.wait(2)
    runner = MemoriesRunner(catalog, max_years=1)
    try:
        runner.card_state(catalog, now=dt.date(2026, 10, 23))
        assert not background_query_started.wait(0.15)
        release_foreground.set()
        assert future.result(timeout=2) == "foreground-finished"
        assert runner.wait_idle()
        assert background_query_started.is_set()
    finally:
        release_foreground.set()
        runner.close()


def test_memories_repeated_requests_coalesce_into_one_worker_task(catalog, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocked_candidates(*_args, **_kwargs):
        started.set()
        assert release.wait(3)
        return []

    monkeypatch.setattr(memories_module, "_day_candidates", blocked_candidates)
    runner = MemoriesRunner(catalog, max_years=1)
    today = dt.date(2026, 10, 23)
    try:
        runner.card_state(catalog, now=today)
        assert started.wait(2)
        for _ in range(100):
            runner.card_state(catalog, now=today)
        with runner._condition:
            assert runner._active is not None
            assert runner._pending is None
            assert runner._thread.is_alive()
        assert sum(thread.name == "photo-memories-low-priority" for thread in threading.enumerate()) == 1
    finally:
        release.set()
        runner.close()


def test_memories_budget_exhaustion_is_incomplete_and_waits_for_manual_retry(catalog, monkeypatch):
    calls = 0

    def budget_once(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise MemoriesBudgetExceeded()
        return []

    monkeypatch.setattr(memories_module, "_day_candidates", budget_once)
    runner = MemoriesRunner(catalog, max_years=1)
    today = dt.date(2026, 10, 23)
    try:
        runner.card_state(catalog, now=today)
        assert runner.wait_idle()
        incomplete = runner.card_state(catalog, now=today)
        assert incomplete["status"] == "BUDGET"
        assert incomplete["cards"] == {"day": [], "month": []}
        assert "尚未完成" in incomplete["error"]
        assert calls == 1
        with runner._condition:
            assert runner._active is None and runner._pending is None

        runner.card_state(catalog, now=today, retry=True)
        assert runner.wait_idle()
        assert calls == 2
    finally:
        runner.close()


def test_memories_old_revision_snapshot_is_not_visible_after_revision_change(catalog, monkeypatch):
    _asset(catalog, "revision-race", dt.datetime(2025, 10, 23, 12, 0))
    old_batch_started = threading.Event()
    release_old_batch = threading.Event()
    current_batch_started = threading.Event()
    release_current_batch = threading.Event()
    call_lock = threading.Lock()
    call_count = 0
    original = memories_module._day_candidates

    def delayed_candidates(*args, **kwargs):
        nonlocal call_count
        with call_lock:
            call_count += 1
            current_call = call_count
        if current_call == 1:
            old_batch_started.set()
            assert release_old_batch.wait(3)
        elif current_call == 2:
            current_batch_started.set()
            assert release_current_batch.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(memories_module, "_day_candidates", delayed_candidates)
    runner = MemoriesRunner(catalog, max_years=1)
    try:
        runner.card_state(catalog, now=dt.date(2026, 10, 23))
        assert old_batch_started.wait(2)
        with catalog.write() as connection:
            connection.execute("UPDATE catalog_filter_revision SET revision=revision+1 WHERE revision_id=1")
        while_new_revision_old_batch_active = runner.card_state(catalog, now=dt.date(2026, 10, 23))
        assert while_new_revision_old_batch_active["cards"] == {"day": [], "month": []}
        release_old_batch.set()
        assert current_batch_started.wait(2)
        # A result computed after the current revision advances is not published.
        assert not any(key[0] == "2026-10-23" and key[3] == 0 for key in runner._cache)
        while_new_batch_active = runner.card_state(catalog, now=dt.date(2026, 10, 23))
        assert while_new_batch_active["cards"] == {"day": [], "month": []}
        release_current_batch.set()
        assert runner.wait_idle(timeout=3)
        # Current-revision results are available only after that revision's own
        # batch completes; old cached cards were never mixed into its response.
        refreshed = runner.card_state(catalog, now=dt.date(2026, 10, 23))
        assert [(item["year"], item["selected_count"]) for item in refreshed["cards"]["day"]] == [(2025, 1)]
        assert refreshed["catalog_filter_revision"] == 1
    finally:
        release_old_batch.set()
        release_current_batch.set()
        runner.close()


def test_memories_revision_change_replaces_stale_pending_discovery(catalog, monkeypatch):
    _asset(catalog, "pending-revision-race", dt.datetime(2025, 10, 23, 12, 0))
    today = dt.date(2026, 10, 23)
    processed_years = []
    original = memories_module._day_candidates

    def track_year(*args, **kwargs):
        processed_years.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(memories_module, "_day_candidates", track_year)
    runner = MemoriesRunner(catalog, max_years=1)
    try:
        with catalog.read_snapshot() as connection:
            old_revision = memories_module._revision(connection)
        with runner._condition:
            runner._today = today
            runner._revision = old_revision
            runner._window_start = 2025
            runner._window_end = 2025
            runner._next_year = 2025
            runner._status = "QUEUED"
            runner._pending = ("discover", today, old_revision, 2025, 2025)
            with catalog.write() as connection:
                connection.execute(
                    "UPDATE catalog_filter_revision SET revision=revision+1 WHERE revision_id=1"
                )

            refreshed = runner.card_state(catalog, now=today)
            assert refreshed["catalog_filter_revision"] == old_revision[0] + 1
            assert runner._pending is not None
            assert runner._pending[2][0] == old_revision[0] + 1

        assert runner.wait_idle(timeout=3)
        final = runner.card_state(catalog, now=today)
        assert final["catalog_filter_revision"] == old_revision[0] + 1
        assert [(item["year"], item["selected_count"]) for item in final["cards"]["day"]] == [(2025, 1)]
        assert processed_years == [2025]
    finally:
        runner.close()


def test_memories_cancel_stops_batch_and_reentry_retries(catalog, monkeypatch):
    started = threading.Event()
    original = memories_module._range_rows

    def cancellable_range(*args, cancelled=None, **kwargs):
        started.set()
        if cancelled is None or not cancelled.wait(3):
            raise AssertionError("test cancellation was not delivered")
        raise MemoriesCancelled()

    monkeypatch.setattr(memories_module, "_range_rows", cancellable_range)
    runner = MemoriesRunner(catalog, max_years=1)
    today = dt.date(2026, 10, 23)
    try:
        runner.preview_state(catalog, memory_type="day", year=2025, now=today)
        assert started.wait(2)
        assert runner.cancel()["status"] == "CANCELLING"
        assert runner.wait_idle()
        assert runner._status == "CANCELLED"

        monkeypatch.setattr(memories_module, "_range_rows", original)
        retried = runner.preview_state(catalog, memory_type="day", year=2025, now=today)
        assert retried["status"] in {"QUEUED", "RUNNING"}
        assert runner.wait_idle()
        assert runner.preview_state(catalog, memory_type="day", year=2025, now=today)["status"] == "READY"
    finally:
        runner.close()


def test_memories_cancelled_discovery_resumes_on_next_card_request(catalog, monkeypatch):
    started = threading.Event()
    original = memories_module._day_candidates
    calls = 0

    def cancel_first_batch(*args, cancelled=None, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            assert cancelled is not None and cancelled.wait(3)
            raise MemoriesCancelled()
        return original(*args, cancelled=cancelled, **kwargs)

    monkeypatch.setattr(memories_module, "_day_candidates", cancel_first_batch)
    runner = MemoriesRunner(catalog, max_years=1)
    today = dt.date(2026, 10, 23)
    try:
        runner.card_state(catalog, now=today)
        assert started.wait(2)
        assert runner.cancel()["status"] == "CANCELLING"
        assert runner.wait_idle()
        assert runner._status == "CANCELLED"

        resumed = runner.card_state(catalog, now=today)
        assert resumed["status"] in {"QUEUED", "RUNNING"}
        assert runner.wait_idle()
        completed_batch = runner.card_state(catalog, now=today)
        assert completed_batch["status"] in {"PARTIAL", "MORE", "COMPLETE"}
        assert calls > 1
    finally:
        runner.close()


def test_memories_cancel_after_last_query_prevents_year_publication_and_progress(catalog, monkeypatch):
    _asset(catalog, "cancel-after-query", dt.datetime(2025, 10, 23, 12, 0))
    month_returning = threading.Event()
    release_month = threading.Event()
    original_month = memories_module._month_candidates

    def pause_after_month(*args, **kwargs):
        rows = original_month(*args, **kwargs)
        month_returning.set()
        assert release_month.wait(3)
        return rows

    monkeypatch.setattr(memories_module, "_month_candidates", pause_after_month)
    runner = MemoriesRunner(catalog, max_years=2)
    today = dt.date(2026, 10, 23)
    try:
        runner.card_state(catalog, now=today)
        assert month_returning.wait(2)
        assert runner.cancel()["status"] == "CANCELLING"
        release_month.set()
        assert runner.wait_idle()
        assert runner._status == "CANCELLED"
        assert runner._next_year == 2025
        assert not any(key[0] == today.isoformat() and len(key) == 7 for key in runner._cache)
    finally:
        release_month.set()
        runner.close()


def test_memories_cancel_after_year_publication_prevents_auto_advance(catalog, monkeypatch):
    batch_published = threading.Event()
    release_worker = threading.Event()
    years = []

    def candidates(_connection, year, *_args, **_kwargs):
        years.append(int(year))
        return []

    monkeypatch.setattr(memories_module, "_day_candidates", candidates)
    monkeypatch.setattr(memories_module, "_month_candidates", candidates)
    runner = MemoriesRunner(catalog, max_years=2)
    original_process = runner._process

    def pause_after_publish(task, cancelled):
        status = original_process(task, cancelled)
        if task[0] == "discover" and status == "BATCH_COMPLETE":
            batch_published.set()
            assert release_worker.wait(3)
        return status

    monkeypatch.setattr(runner, "_process", pause_after_publish)
    today = dt.date(2026, 10, 23)
    try:
        runner.card_state(catalog, now=today)
        assert batch_published.wait(2)
        assert runner._next_year == 2024
        assert runner.cancel()["status"] == "CANCELLING"
        release_worker.set()
        assert runner.wait_idle()
        assert runner._status == "CANCELLED"
        assert runner._pending is None
        assert runner._next_year == 2024
        assert years.count(2025) == 2  # day and month for one completed year only
        assert 2024 not in years
    finally:
        release_worker.set()
        runner.close()


def test_memories_cancel_without_work_preserves_completed_discovery(catalog):
    runner = MemoriesRunner(catalog, max_years=1)
    try:
        today = dt.date(2026, 10, 23)
        runner.card_state(catalog, now=today)
        assert runner.wait_idle()
        with runner._condition:
            runner._next_year = None
            runner._status = "COMPLETE"
        assert runner.cancel()["status"] == "COMPLETE"
        result = runner.card_state(catalog, now=today)
        assert result["status"] == "COMPLETE"
        assert result["cards"] == {"day": [], "month": []}
    finally:
        runner.close()


def test_memories_frontend_does_not_report_cancelled_or_more_as_empty():
    source = memories_module.__file__.replace("api\\memories.py", "web\\app.js")
    app = open(source, encoding="utf-8").read()
    assert '"CANCELLING", "CANCELLED", "STALE"' in app
    assert 'memory.status === "MORE" ? "最近检查的年份中，当前分类暂未找到回忆；还可以继续查看更早年份。"' in app
    assert '"已检查全部可用年份，当前分类没有可展示的回忆。' in app
    assert 'memory.status === "DEFERRED" ? 900 : (count ? 600 : 250)' in app
    assert 'String(body.today || "")' in app
    assert 'if (!state.memories.loading) loadMemoryCards(false);' in app
    assert 'state.memories.requestVersion += 1;\n      state.memories.previewRequestVersion += 1;\n      state.memories.loading = false;' in app
    assert 'state.currentView !== "memories" || memory.previewTarget' in app
    assert 'if (memory.pollTimer) window.clearTimeout(memory.pollTimer);\n    memory.pollTimer = null;' in app
    assert 'state.memories.requestVersion += 1;\n    state.memories.loading = false;' in app
    assert 'if (validPreview) {\n      state.memories.requestVersion += 1;\n      state.memories.loading = false;' in app
    assert 'if (failure.status === 409)' in app
    assert 'memory.cards = {day: [], month: []};' in app


def test_memories_close_interrupts_and_joins_active_batch(catalog, monkeypatch):
    started = threading.Event()
    original = memories_module._range_rows

    def blocked_range(*args, cancelled=None, **kwargs):
        started.set()
        assert cancelled is not None and cancelled.wait(3)
        raise MemoriesCancelled()

    monkeypatch.setattr(memories_module, "_range_rows", blocked_range)
    runner = MemoriesRunner(catalog, max_years=1)
    runner.card_state(catalog, now=dt.date(2026, 10, 23))
    assert started.wait(2)
    runner.close(timeout=2)
    assert not runner._thread.is_alive()
    monkeypatch.setattr(memories_module, "_range_rows", original)
