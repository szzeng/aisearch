from __future__ import annotations

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import CATALOG_UNREADABLE, CatalogError


def _set_short_busy_timeout(catalog: Catalog, milliseconds: int = 30) -> None:
    catalog.connection.execute(f"PRAGMA busy_timeout = {int(milliseconds)}")


def _prepare_table(catalog: Catalog) -> None:
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES ('contention_probe_ready', 'yes')"
        )


def test_independent_catalog_writers_serialize_without_losing_rows(tmp_path: Path) -> None:
    seed = Catalog.open(data_root=tmp_path)
    _prepare_table(seed)
    seed.close()
    barrier = threading.Barrier(2)

    def insert(value: str) -> None:
        catalog = Catalog.open(data_root=tmp_path)
        try:
            barrier.wait(timeout=2)
            with catalog.write() as connection:
                connection.execute(
                    "INSERT INTO metadata(key, value) VALUES (?, ?)",
                    (f"contention_{value}", value),
                )
        finally:
            catalog.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(insert, "first"), pool.submit(insert, "second")]
        for future in futures:
            future.result(timeout=5)

    check = Catalog.open(data_root=tmp_path)
    try:
        values = [
            str(row[0])
            for row in check.connection.execute(
                "SELECT value FROM metadata WHERE key IN ('contention_first', 'contention_second') "
                "ORDER BY value"
            ).fetchall()
        ]
        assert values == ["first", "second"]
    finally:
        check.close()


def test_begin_retries_after_a_short_external_write_lock(tmp_path: Path) -> None:
    catalog = Catalog.open(data_root=tmp_path)
    _prepare_table(catalog)
    _set_short_busy_timeout(catalog)
    holder_ready = threading.Event()
    release_holder = threading.Event()
    holder_errors: list[str] = []

    def hold_briefly() -> None:
        holder = sqlite3.connect(str(catalog.db_path), isolation_level=None, timeout=0.03)
        try:
            holder.execute("BEGIN IMMEDIATE")
            holder_ready.set()
            if not release_holder.wait(timeout=2):
                raise TimeoutError("test writer was not released")
            holder.execute("ROLLBACK")
        except Exception as error:  # noqa: BLE001 - capture test-thread failures
            holder_errors.append(f"{type(error).__name__}: {error}")
        finally:
            holder.close()

    thread = threading.Thread(target=hold_briefly, name="synthetic-sqlite-holder")
    thread.start()
    try:
        assert holder_ready.wait(timeout=2)
        threading.Timer(0.075, release_holder.set).start()
        started = time.perf_counter()
        with catalog.write() as connection:
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES ('contention_recovered', 'yes')"
            )
        waited = time.perf_counter() - started
        thread.join(timeout=2)

        assert not thread.is_alive()
        assert holder_errors == []
        assert waited >= 0.07
        assert catalog.connection.execute(
            "SELECT value FROM metadata WHERE key='contention_recovered'"
        ).fetchone()[0] == "yes"
    finally:
        release_holder.set()
        thread.join(timeout=2)
        catalog.close()


def test_long_external_lock_exhausts_begin_retries_without_partial_write(
    tmp_path: Path,
) -> None:
    catalog = Catalog.open(data_root=tmp_path)
    _prepare_table(catalog)
    _set_short_busy_timeout(catalog, milliseconds=20)
    holder = sqlite3.connect(str(catalog.db_path), isolation_level=None, timeout=0.02)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(CatalogError) as refused:
            with catalog.write() as connection:
                connection.execute(
                    "INSERT INTO metadata(key, value) VALUES ('contention_partial', 'no')"
                )
        error = refused.value
        assert error.code == CATALOG_UNREADABLE
        assert "cannot start a catalog write transaction" in error.message
        assert "locked" in error.message.lower()
        assert error.details["phase"] == "begin_immediate"
        assert error.details["attempts"] == 3
        assert error.details["process_id"] > 0
        assert error.details["thread_name"] == threading.current_thread().name
        assert catalog.connection.execute(
            "SELECT COUNT(*) FROM metadata WHERE key='contention_partial'"
        ).fetchone()[0] == 0
    finally:
        holder.execute("ROLLBACK")
        holder.close()
        catalog.close()


def test_body_and_commit_failures_roll_back_without_partial_rows(tmp_path: Path) -> None:
    catalog = Catalog.open(data_root=tmp_path)
    try:
        with pytest.raises(RuntimeError, match="injected body failure"):
            with catalog.write() as connection:
                connection.execute(
                    "INSERT INTO metadata(key, value) VALUES ('body_partial', 'no')"
                )
                raise RuntimeError("injected body failure")
        assert catalog.connection.execute(
            "SELECT COUNT(*) FROM metadata WHERE key='body_partial'"
        ).fetchone()[0] == 0

        with pytest.raises(CatalogError) as failed_commit:
            with catalog.write() as connection:
                connection.execute("PRAGMA defer_foreign_keys = ON")
                connection.execute(
                    "INSERT INTO scan_runs(source_id, state) VALUES (999999, 'RUNNING')"
                )
        assert failed_commit.value.code == CATALOG_UNREADABLE
        assert failed_commit.value.details["phase"] == "commit"
        assert catalog.connection.execute(
            "SELECT COUNT(*) FROM scan_runs WHERE source_id=999999"
        ).fetchone()[0] == 0
    finally:
        catalog.close()
