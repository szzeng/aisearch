"""Bounded, read-only photo memory discovery and deterministic previews."""

from __future__ import annotations

import base64
import datetime as dt
import json
import sqlite3
import threading
import time
from collections import OrderedDict
from typing import Any

from photo_search.api import catalog_reads
from photo_search.api.errors import ApiError
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import CatalogError
from photo_search.catalog.image_policy import image_location_sql
from photo_search.catalog.sqlite_io import open_readonly
from photo_search.catalog.video import photo_asset_sql
from photo_search.favorites import _revision as favorites_revision
from photo_search.places.revision import read_filter_revision
from photo_search.taken_time import contract
from photo_search.taken_time import read as taken_time_read

ALGORITHM_VERSION = "memories-v1"
MAX_CACHE_ENTRIES = 32
MAX_PREVIEW = 24
DAY_CANDIDATE_LIMIT = 240
DAY_TIME_BUCKETS = 4
DAY_TIME_BUCKET_LIMIT = DAY_CANDIDATE_LIMIT // DAY_TIME_BUCKETS
CURSOR_PREFIX = "mem1"


class MemoriesCancelled(Exception):
    pass


class MemoriesYielded(Exception):
    pass


class MemoriesBudgetExceeded(Exception):
    pass


def _service_day() -> dt.date:
    return dt.date.today()


def _day_range(year: int, month: int, day: int) -> tuple[int, int] | None:
    try:
        start = contract.civil_key(year, month, day)
        end_date = dt.date(year, month, day) + dt.timedelta(days=1)
    except ValueError:
        return None
    return start, contract.civil_key(end_date.year, end_date.month, end_date.day)


def _month_range(year: int, month: int) -> tuple[int, int]:
    start = contract.civil_key(year, month, 1)
    end = contract.civil_key(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return start, end


def _revision(connection: sqlite3.Connection) -> tuple[int, int, int, int]:
    return (
        read_filter_revision(connection),
        favorites_revision(connection),
        int(taken_time_read.rules_version(connection) or 0),
        int(taken_time_read.rules_version_matches(connection)),
    )


def _check_trusted(connection: sqlite3.Connection) -> None:
    if not taken_time_read.rules_version_matches(connection):
        raise ApiError("TAKEN_TIME_RULES_STALE", "拍摄日期规则版本不匹配，请先更新拍摄日期", status=409)


def _cursor_encode(day: dt.date, next_year: int, revision: tuple[int, ...]) -> str:
    raw = json.dumps([CURSOR_PREFIX, day.isoformat(), int(next_year), *revision], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _cursor_decode(value: str | None, day: dt.date, revision: tuple[int, ...]) -> int:
    if not value:
        return day.year - 1
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        decoded = json.loads(raw)
        if (len(decoded) != 7 or decoded[0] != CURSOR_PREFIX or decoded[1] != day.isoformat()
                or tuple(int(x) for x in decoded[3:]) != revision):
            raise ValueError("stale cursor")
        year = int(decoded[2])
        if year < 1 or year >= day.year:
            raise ValueError("invalid cursor year")
        return year
    except (ValueError, TypeError, json.JSONDecodeError, base64.binascii.Error) as error:
        raise ApiError("MEMORIES_CURSOR_STALE", "图库或本地日期已变化，请刷新回忆", status=409) from error


def _range_rows(connection: sqlite3.Connection, low: int, high: int, limit: int, *,
                cancelled: threading.Event | None = None, foreground_busy: Any = None) -> list[sqlite3.Row]:
    photo_sql, photo_params = photo_asset_sql(asset_alias="a")
    rows: list[sqlite3.Row] = []
    for source in contract.CAPTURE_SOURCES:
        if cancelled is not None and cancelled.is_set():
            raise MemoriesCancelled()
        if foreground_busy is not None and foreground_busy():
            raise MemoriesYielded()
        deadline = time.perf_counter() + 0.075
        budget_exceeded = threading.Event()

        def stop_after_batch_budget() -> int:
            if cancelled is not None and cancelled.is_set():
                return 1
            if time.perf_counter() >= deadline:
                budget_exceeded.set()
                return 1
            return 0

        connection.set_progress_handler(stop_after_batch_budget, 1000)
        sql = (
            "SELECT m.asset_id,m.taken_at_sort FROM asset_metadata AS m "
            "INDEXED BY idx_asset_metadata_capture CROSS JOIN assets AS a ON a.asset_id=m.asset_id "
            "WHERE m.time_source=? AND m.taken_at_sort>=? AND m.taken_at_sort<? "
            "AND a.state='ACTIVE' AND EXISTS (SELECT 1 FROM file_locations AS p "
            "INDEXED BY idx_file_locations_present_asset WHERE p.asset_id=a.asset_id "
            "AND p.availability='PRESENT' AND " + image_location_sql("p") + ") AND " + photo_sql +
            " ORDER BY m.taken_at_sort,m.asset_id LIMIT ?"
        )
        try:
            rows.extend(connection.execute(sql, (source, low, high, *photo_params, int(limit))).fetchall())
        except sqlite3.OperationalError as error:
            if cancelled is not None and cancelled.is_set():
                raise MemoriesCancelled() from error
            if budget_exceeded.is_set():
                raise MemoriesBudgetExceeded() from error
            if "interrupted" in str(error).casefold():
                raise MemoriesYielded() from error
            raise
        finally:
            connection.set_progress_handler(None, 0)
    rows.sort(key=lambda r: (int(r["taken_at_sort"]), int(r["asset_id"])))
    # Keep the candidate work fixed across both trusted EXIF streams.
    return rows[: int(limit)]


def _month_candidates(connection: sqlite3.Connection, year: int, month: int, *,
                      cancelled: threading.Event | None = None, foreground_busy: Any = None) -> list[sqlite3.Row]:
    low, high = _month_range(year, month)
    days = (dt.date(year + (month == 12), 1 if month == 12 else month + 1, 1) - dt.date(year, month, 1)).days
    photo_sql, photo_params = photo_asset_sql(asset_alias="a")
    rows: dict[int, sqlite3.Row] = {}
    # One indexed, limited range seek per civil day and trusted source. This keeps
    # a burst on one date from consuming the month's candidate budget.
    for source in contract.CAPTURE_SOURCES:
        if cancelled is not None and cancelled.is_set():
            raise MemoriesCancelled()
        if foreground_busy is not None and foreground_busy():
            raise MemoriesYielded()
        ctes: list[str] = []
        params: list[Any] = []
        for day in range(1, days + 1):
            start = contract.civil_key(year, month, day)
            end = high if day == days else contract.civil_key(year, month, day + 1)
            ctes.append(
                f"d{day} AS (SELECT m.asset_id,m.taken_at_sort FROM asset_metadata AS m "
                "INDEXED BY idx_asset_metadata_capture CROSS JOIN assets AS a ON a.asset_id=m.asset_id "
                "WHERE m.time_source=? AND m.taken_at_sort>=? AND m.taken_at_sort<? "
                "AND a.state='ACTIVE' AND EXISTS (SELECT 1 FROM file_locations AS p "
                "INDEXED BY idx_file_locations_present_asset WHERE p.asset_id=a.asset_id "
                "AND p.availability='PRESENT' AND " + image_location_sql("p") + ") AND " + photo_sql +
                " ORDER BY m.taken_at_sort,m.asset_id LIMIT 1)"
            )
            params.extend((source, max(low, start), min(high, end), *photo_params))
        union = " UNION ALL ".join(f"SELECT * FROM d{day}" for day in range(1, days + 1))
        deadline = time.perf_counter() + 0.075
        budget_exceeded = threading.Event()

        def stop_after_batch_budget() -> int:
            if cancelled is not None and cancelled.is_set():
                return 1
            if time.perf_counter() >= deadline:
                budget_exceeded.set()
                return 1
            return 0

        connection.set_progress_handler(stop_after_batch_budget, 1000)
        try:
            for row in connection.execute(f"WITH {','.join(ctes)} {union} ORDER BY taken_at_sort,asset_id", params):
                rows[int(row["asset_id"])] = row
        except sqlite3.OperationalError as error:
            if cancelled is not None and cancelled.is_set():
                raise MemoriesCancelled() from error
            if budget_exceeded.is_set():
                raise MemoriesBudgetExceeded() from error
            if "interrupted" in str(error).casefold():
                raise MemoriesYielded() from error
            raise
        finally:
            connection.set_progress_handler(None, 0)
    return sorted(rows.values(), key=lambda r: (int(r["taken_at_sort"]), int(r["asset_id"])))


def _day_candidates(connection: sqlite3.Connection, year: int, month: int, day: int, *,
                    cancelled: threading.Event | None = None, foreground_busy: Any = None) -> list[sqlite3.Row]:
    span = _day_range(year, month, day)
    if span is None:
        return []
    low, high = span
    rows: dict[int, sqlite3.Row] = {}
    bucket_width = 86_400_000 // DAY_TIME_BUCKETS
    for bucket in range(DAY_TIME_BUCKETS):
        start = low + bucket * bucket_width
        end = high if bucket + 1 == DAY_TIME_BUCKETS else low + (bucket + 1) * bucket_width
        for row in _range_rows(connection, start, end, DAY_TIME_BUCKET_LIMIT,
                               cancelled=cancelled, foreground_busy=foreground_busy):
            rows[int(row["asset_id"])] = row
    return sorted(rows.values(), key=lambda row: (int(row["taken_at_sort"]), int(row["asset_id"])))


def _spread(rows: list[sqlite3.Row], limit: int = MAX_PREVIEW) -> list[int]:
    if len(rows) <= limit:
        return [int(row["asset_id"]) for row in rows]
    # Month memories first cover distinct civil dates, then fill with stable time samples.
    chosen: list[sqlite3.Row] = []
    by_day: OrderedDict[int, list[sqlite3.Row]] = OrderedDict()
    for row in rows:
        by_day.setdefault(int(row["taken_at_sort"]) // 86_400_000, []).append(row)
    day_groups = list(by_day.values())
    if len(day_groups) > 1:
        day_count = min(limit, len(day_groups))
        slots = ([len(day_groups) // 2] if day_count == 1 else
                 [round(index * (len(day_groups) - 1) / (day_count - 1)) for index in range(day_count)])
        for day_index in slots:
            group = day_groups[day_index]
            chosen.append(group[len(group) // 2])
    chosen_ids = {int(item["asset_id"]) for item in chosen}
    for index in range(limit):
        slot = rows[round(index * (len(rows) - 1) / (limit - 1))]
        minute = int(slot["taken_at_sort"]) // 60_000
        if (int(slot["asset_id"]) not in chosen_ids and
                not any(int(item["taken_at_sort"]) // 60_000 == minute for item in chosen)):
            chosen.append(slot)
            chosen_ids.add(int(slot["asset_id"]))
    if len(chosen) < limit:
        chosen.extend(row for row in rows if int(row["asset_id"]) not in chosen_ids)
    return [int(row["asset_id"]) for row in chosen[:limit]]


class MemoriesRunner:
    """One low-priority, cancellable read worker with a one-item coalescing queue."""
    def __init__(self, catalog: Catalog, *, max_years: int = 10):
        self.catalog = catalog
        self.path = catalog.db_path
        self.data_root = catalog.data_root
        self.max_years = max(1, min(int(max_years), 20))
        # Results are valid for their date, revision tuple and algorithm version;
        # a wall-clock TTL only forces needless rescans within the same session.
        self._condition = threading.Condition()
        self._cache: OrderedDict[tuple[Any, ...], Any] = OrderedDict()
        self._pending: tuple[Any, ...] | None = None
        self._active: tuple[Any, ...] | None = None
        self._cancelled: threading.Event | None = None
        self._connection: sqlite3.Connection | None = None
        self._closed = False
        self._revision: tuple[Any, ...] | None = None
        self._today: dt.date | None = None
        self._next_year: int | None = None
        self._window_start_year: int | None = None
        self._window_end_year: int | None = None
        self._status = "IDLE"
        self._error: str | None = None
        self._failed_task: tuple[Any, ...] | None = None
        self._failed_status: str | None = None
        self._thread = threading.Thread(target=self._loop, name="photo-memories-low-priority", daemon=True)
        self._thread.start()

    @property
    def cache_size(self) -> int:
        with self._condition:
            return len(self._cache)

    def wait_idle(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while self._active is not None or self._pending is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def _remember(self, key: tuple[Any, ...], value: Any) -> None:
        with self._condition:
            self._cache[key] = value
            self._cache.move_to_end(key)
            while len(self._cache) > MAX_CACHE_ENTRIES:
                # Keep a complete active 10-year discovery window (at most 20
                # day/month cards); previews are cheaper to recompute and go first.
                preview_key = next((candidate for candidate in self._cache if len(candidate) == 8), None)
                self._cache.pop(preview_key if preview_key is not None else next(iter(self._cache)))

    def _cached(self, key: tuple[Any, ...]) -> Any | None:
        with self._condition:
            value = self._cache.get(key)
            if value is None:
                return None
            self._cache.move_to_end(key)
            return value

    def _enqueue(self, task: tuple[Any, ...], *, priority: bool = False) -> None:
        connection = None
        with self._condition:
            if self._closed or task == self._active or task == self._pending:
                return
            if priority and self._active is not None and self._active[0] == "discover":
                if self._cancelled is not None:
                    self._cancelled.set()
                connection = self._connection
            if self._pending is not None and priority:
                self._pending = task
            elif self._pending is None:
                self._pending = task
            if self._status not in {"RUNNING"}:
                self._status = "QUEUED"
            self._error = None
            self._failed_task = None
            self._failed_status = None
            self._condition.notify_all()
        if connection is not None:
            try:
                connection.interrupt()
            except sqlite3.Error:
                pass
            catalog_reads.wake_foreground_idle(self.catalog)

    def card_state(self, catalog: Any, *, cursor: str | None = None, retry: bool = False,
                   now: dt.date | None = None) -> dict[str, Any]:
        today = now or _service_day()
        with catalog.read_snapshot() as connection:
            _check_trusted(connection)
            revision = _revision(connection)
            start_year = _cursor_decode(cursor, today, revision)
        with self._condition:
            should_start = False
            if self._today != today or self._revision != revision:
                self._today, self._revision = today, revision
                self._cache.clear()
                if (self._pending is not None
                        and (self._pending[1] != today or self._pending[2] != revision)):
                    # A not-yet-started task belongs to the old identity. Drop
                    # it before scheduling the new date/revision work.
                    self._pending = None
                self._next_year = today.year - 1
                self._window_start_year = today.year - 1
                self._window_end_year = max(1, today.year - self.max_years)
                self._status = "IDLE"
                self._error = None
            if (cursor and start_year != self._window_start_year
                    and self._status in {"MORE", "COMPLETE", "IDLE", "ERROR", "CANCELLED", "STALE"}):
                self._next_year = start_year
                self._window_start_year = start_year
                self._window_end_year = max(1, start_year - self.max_years + 1)
                self._status = "IDLE"
            elif (cursor is None and self._status in {"MORE", "COMPLETE", "CANCELLED"}
                  and self._active is None and self._pending is None
                  and self._window_start_year != today.year - 1):
                # Route re-entry starts at the first window even when the prior
                # visit paged into older years. Keep cached cards; only restore
                # the visible window and its discovery frontier.
                self._next_year = today.year - 1
                self._window_start_year = today.year - 1
                self._window_end_year = max(1, today.year - self.max_years)
                self._status = "IDLE"
            should_start = self._status in {"IDLE", "CANCELLED"}
            next_year = self._next_year
            window_start = self._window_start_year
            end_year = self._window_end_year
            work_active = self._active is not None
            work_pending = self._pending is not None
            discovery_failed = self._failed_task is not None and self._failed_task[0] == "discover"
            if not work_active and not work_pending and not discovery_failed:
                if next_year is None:
                    self._status = "COMPLETE"
                elif end_year is not None and next_year >= end_year:
                    self._status = "PARTIAL"
                else:
                    self._status = "MORE"
        if next_year is not None and end_year is not None and next_year >= end_year:
            task = ("discover", today, revision, next_year, end_year)
            with self._condition:
                failed = self._failed_task == task
                if retry and failed:
                    self._failed_task = None
                    self._failed_status = None
            # A status read never advances a running window. The first request,
            # explicit retry, or re-entry after cancellation may start it.
            if retry or (should_start and not failed):
                self._enqueue(task)
        result = {"day": [], "month": []}
        with self._condition:
            entries = list(self._cache.items())
        for key, entry in entries:
            if (len(key) == 7 and key[0] == today.isoformat() and tuple(key[3:6]) == tuple(revision[:3])
                    and key[6] == ALGORITHM_VERSION
                    and window_start is not None and end_year is not None
                    and end_year <= int(key[2]) <= window_start):
                item = entry
                result[item["memory_type"]].append(item)
        for group in result.values():
            group.sort(key=lambda item: int(item["year"]), reverse=True)
        with self._condition:
            status, error = self._status, self._error
            cursor_year = self._next_year if status == "MORE" and self._next_year is not None and self._next_year > 0 else None
        return {"today": today.isoformat(), "cards": result, "status": status, "error": error,
            "cache_entries": self.cache_size, "catalog_filter_revision": revision[0],
            "favorites_revision": revision[1], "taken_time_rules_version": revision[2],
            "next_cursor": None if cursor_year is None else _cursor_encode(today, cursor_year, revision)}

    def preview_state(self, catalog: Any, *, memory_type: str, year: int,
                      now: dt.date | None = None, retry: bool = False) -> dict[str, Any]:
        today = now or _service_day()
        if memory_type not in {"day", "month"} or int(year) < 1 or int(year) >= today.year:
            raise ApiError("MEMORIES_INVALID_REQUEST", "回忆类型或年份无效", status=422)
        with catalog.read_snapshot() as connection:
            _check_trusted(connection)
            revision = _revision(connection)
        key = (today.isoformat(), memory_type, int(year), *revision, ALGORITHM_VERSION)
        value = self._cached(key)
        if value is not None:
            with catalog.read_snapshot() as connection:
                if _revision(connection) != revision:
                    raise ApiError("MEMORIES_REVISION_STALE", "图库正在更新，请刷新回忆", status=409)
            return {"status": "READY", **value}
        task = ("preview", today, revision, memory_type, int(year))
        with self._condition:
            if self._failed_task == task and not retry:
                return {"status": self._failed_status or "ERROR", "error": self._error or "可重试",
                    "memory_type": memory_type, "year": int(year), "selected_count": 0, "assets": []}
            if retry and self._failed_task == task:
                self._failed_task = None
                self._failed_status = None
        self._enqueue(task, priority=True)
        with self._condition:
            status = self._status
        return {"status": status if status in {"RUNNING", "QUEUED", "DEFERRED"} else "QUEUED",
            "memory_type": memory_type, "year": int(year), "selected_count": 0, "assets": []}

    def cancel(self) -> dict[str, Any]:
        with self._condition:
            active = self._active is not None
            pending = self._pending is not None
            self._pending = None
            if self._cancelled is not None:
                self._cancelled.set()
            connection = self._connection
            if active:
                self._status = "CANCELLING"
            elif pending:
                self._status = "CANCELLED"
            elif self._status == "DEFERRED":
                self._status = "CANCELLED"
            self._condition.notify_all()
        if connection is not None:
            try:
                connection.interrupt()
            except sqlite3.Error:
                pass
        catalog_reads.wake_foreground_idle(self.catalog)
        return {"status": self._status}

    def _high_priority_busy(self, connection: sqlite3.Connection) -> bool:
        if catalog_reads.foreground_busy(self.catalog):
            return True
        scan = connection.execute("SELECT 1 FROM scan_runs WHERE state='RUNNING' LIMIT 1").fetchone()
        job = connection.execute("SELECT 1 FROM jobs WHERE state='RUNNING' LIMIT 1").fetchone()
        return scan is not None or job is not None

    def _process(self, task: tuple[Any, ...], cancelled: threading.Event) -> str:
        kind, today, revision = task[:3]
        connection = open_readonly(self.path, immutable=None)
        connection.isolation_level = None
        self._connection = connection
        connection.set_progress_handler(lambda: int(cancelled.is_set()), 1000)
        catalog = Catalog(connection, db_path=self.path, data_root=self.data_root,
                          clock=self.catalog.clock, lease_seconds=self.catalog.lease_seconds)
        try:
            if kind == "discover":
                _, _, _, first_year, last_year = task
                year = int(first_year)
                if cancelled.is_set():
                    return "CANCELLED"
                if not catalog_reads.wait_foreground_idle(self.catalog, cancelled):
                    return "CANCELLED"
                with catalog.read_snapshot() as conn:
                    if self._high_priority_busy(conn):
                        return "DEFERRED"
                    if (_revision(conn) != revision or not taken_time_read.rules_version_matches(conn)
                            or self._today != today or self._revision != revision):
                        return "STALE"
                    day_rows = _day_candidates(conn, year, today.month, today.day,
                        cancelled=cancelled, foreground_busy=lambda: catalog_reads.foreground_busy(self.catalog))
                    day_ids = _spread(day_rows)
                    month_rows = _month_candidates(conn, year, today.month, cancelled=cancelled,
                        foreground_busy=lambda: catalog_reads.foreground_busy(self.catalog))
                    month_ids = _spread(month_rows)
                    if cancelled.is_set():
                        return "CANCELLED"
                    if (_revision(conn) != revision or self._today != today or self._revision != revision):
                        return "STALE"
                    # Serialize publication with cancel(): once cancellation
                    # wins the condition lock, this year cannot be committed.
                    with self._condition:
                        if cancelled.is_set():
                            return "CANCELLED"
                        if day_ids:
                            item = {"memory_type":"day", "year":year,
                                "title":f"{today.year-year}年前的今天", "selected_count":len(day_ids),
                                "cover_asset_id":day_ids[0]}
                            self._remember((today.isoformat(), "day", year, *revision[:3], ALGORITHM_VERSION), item)
                        if month_ids:
                            item = {"memory_type":"month", "year":year,
                                "title":f"{year}年{today.month}月", "selected_count":len(month_ids),
                                "cover_asset_id":month_ids[0]}
                            self._remember((today.isoformat(), "month", year, *revision[:3], ALGORITHM_VERSION), item)
                        self._next_year = year - 1 if year > 1 else None
                return "BATCH_COMPLETE"
            _, _, _, memory_type, year = task
            if not catalog_reads.wait_foreground_idle(self.catalog, cancelled):
                return "CANCELLED"
            with catalog.read_snapshot() as conn:
                if self._high_priority_busy(conn):
                    return "DEFERRED"
                if _revision(conn) != revision or not taken_time_read.rules_version_matches(conn):
                    return "STALE"
                if memory_type == "day":
                    rows = _day_candidates(conn, int(year), today.month, today.day,
                        cancelled=cancelled, foreground_busy=lambda: catalog_reads.foreground_busy(self.catalog))
                else:
                    rows = _month_candidates(conn, int(year), today.month, cancelled=cancelled,
                        foreground_busy=lambda: catalog_reads.foreground_busy(self.catalog))
                ids = _spread(rows)
                if not ids:
                    assets = []
                else:
                    marks = ",".join("?" for _ in ids)
                    favorites = {int(row[0]) for row in conn.execute(
                        f"SELECT asset_id FROM asset_favorites WHERE asset_id IN ({marks})", tuple(ids))}
                    assets = [{"asset_id": value, "is_favorite": value in favorites} for value in ids]
                if _revision(conn) != revision:
                    return "STALE"
            value = {"memory_type":memory_type,"year":int(year),
                "title":(f"{today.year-year}年前的今天" if memory_type == "day" else f"{year}年{today.month}月"),
                "selected_count":len(assets),"assets":assets,
                "all_range":{"capture_from":f"{year:04d}-{today.month:02d}-{today.day:02d}" if memory_type == "day" else f"{year:04d}-{today.month:02d}-01",
                    "capture_to_exclusive":_all_range_end(today, int(year), memory_type)},
                "catalog_filter_revision":revision[0],"favorites_revision":revision[1]}
            self._remember((today.isoformat(), memory_type, int(year), *revision, ALGORITHM_VERSION), value)
            return "COMPLETE"
        finally:
            self._connection = None
            catalog.close()

    def _loop(self) -> None:
        deferred_delay = 0.25
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                task, self._pending = self._pending, None
                self._active = task
                cancelled = threading.Event()
                self._cancelled = cancelled
                self._status = "RUNNING"
            error_message = None
            try:
                status = self._process(task, cancelled)
            except (MemoriesCancelled, sqlite3.OperationalError) as error:
                status = "CANCELLED" if cancelled.is_set() else "ERROR"
                error_message = None if status == "CANCELLED" else str(error)[:240]
            except MemoriesBudgetExceeded:
                status = "BUDGET"
                error_message = "查询达到单批工作预算，结果尚未完成；可手动重试。"
            except MemoriesYielded:
                status = "DEFERRED"
            except CatalogError as error:
                status = "ERROR"
                error_message = error.message[:240]
            except Exception as error:  # noqa: BLE001 - background failures cannot affect API service.
                status = "ERROR"
                error_message = str(error)[:240]
            with self._condition:
                # Read the cancellation event under the same lock used by
                # cancel(). Otherwise a cancel between this read and queueing
                # the next year can be overwritten by the worker's old value.
                was_cancelled = cancelled.is_set()
                self._active = None
                self._cancelled = None
                self._connection = None
                if was_cancelled:
                    status = "CANCELLED"
                self._status = status
                self._error = error_message
                if status == "BATCH_COMPLETE" and task[0] == "discover":
                    if (self._next_year is not None and self._window_end_year
                            and self._next_year >= self._window_end_year):
                        if self._pending is None:
                            next_task = ("discover", task[1], task[2], self._next_year, self._window_end_year)
                            self._pending = next_task
                        self._status = "QUEUED"
                    else:
                        self._status = "MORE" if self._next_year is not None else "COMPLETE"
                    deferred_delay = 0.25
                elif status == "STALE":
                    self._status = "STALE"
                elif (status == "COMPLETE" and task[0] == "preview"
                      and self._pending is None and self._next_year is not None
                      and self._window_end_year is not None
                      and self._next_year >= self._window_end_year
                      and self._today == task[1] and self._revision == task[2]):
                    # A priority preview may interrupt an unfinished discovery year.
                    # Resume that year after the preview rather than losing the window.
                    self._pending = ("discover", task[1], task[2], self._next_year, self._window_end_year)
                    self._status = "QUEUED"
                if status in {"ERROR", "BUDGET"}:
                    self._failed_task = task
                    self._failed_status = status
                elif status not in {"DEFERRED", "CANCELLED", "STALE"}:
                    self._failed_task = None
                    self._failed_status = None
                self._condition.notify_all()
                if status == "DEFERRED" and was_cancelled:
                    self._status = "CANCELLED"
                    status = "CANCELLED"
                if status == "DEFERRED" and task[0] == "discover" and not self._closed:
                    # Yield without spinning; wake early if a preview/cancel/close
                    # changes the queue or state while discovery is deferred.
                    if self._pending is None:
                        self._pending = task
                        self._condition.wait(timeout=deferred_delay)
                        if self._pending == task and self._status != "CANCELLED":
                            self._status = "QUEUED"
                            self._condition.notify_all()
                    deferred_delay = min(deferred_delay * 2, 1.5)

    def close(self, *, timeout: float = 3.0) -> None:
        with self._condition:
            self._closed = True
            if self._cancelled is not None:
                self._cancelled.set()
            connection = self._connection
            self._pending = None
            self._condition.notify_all()
        if connection is not None:
            try:
                connection.interrupt()
            except sqlite3.Error:
                pass
        catalog_reads.wake_foreground_idle(self.catalog)
        self._thread.join(timeout=timeout)


def _all_range_end(today: dt.date, year: int, memory_type: str) -> str:
    if memory_type == "day":
        span = _day_range(year, today.month, today.day)
        if span is None:
            return f"{year:04d}-{today.month:02d}-{today.day:02d}"
        return contract.normalize_bound(span[1])[:10]
    _, end = _month_range(year, today.month)
    return contract.normalize_bound(end)[:10]
