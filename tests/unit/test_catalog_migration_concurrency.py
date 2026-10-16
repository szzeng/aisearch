"""Catalog startup preflight and migration concurrency use real SQLite schemas."""

from __future__ import annotations

import gc
import os
import threading
import weakref
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from photo_search.catalog import migrate, schema
from photo_search.catalog.sqlite_io import connect_rw, ensure_wal


def _seed_prefix(path: Path, version: int) -> None:
    connection = connect_rw(path)
    try:
        ensure_wal(connection)
        for number, script in schema.migration_files():
            if number > version:
                break
            migrate._apply_migration(connection, number, script)
    finally:
        connection.close()


def test_preflight_pins_version_and_objects_to_one_wal_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Commit migration 6 exactly between the version and schema-object reads."""
    path = tmp_path / "catalog.db"
    _seed_prefix(path, 5)
    writer = connect_rw(path)
    writer.execute("INSERT INTO metadata(key,value) VALUES('snapshot_probe','live')")
    assert Path(f"{path}-wal").exists()
    original_read = schema.read_schema_version
    committed = False

    def read_then_commit(connection):
        nonlocal committed
        version = original_read(connection)
        if not committed:
            committed = True
            script = next(script for number, script in schema.migration_files() if number == 6)
            # Deliberately use a separate raw writer: the assertion must prove a SQLite
            # snapshot, independently of the process-local migration lock.
            migrate._apply_migration(writer, 6, script)
        return version

    monkeypatch.setattr(schema, "read_schema_version", read_then_commit)
    try:
        assert migrate.inspect_database(path) == 5
        assert committed
        assert original_read(writer) == 6
        assert migrate.inspect_database(path) == 6
    finally:
        writer.close()


@pytest.mark.parametrize("previous", [0, 5])
def test_concurrent_same_database_migrators_apply_each_version_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, previous: int
) -> None:
    root = tmp_path / "PhotoSearchData"
    path = root / "catalog" / "catalog.db"
    if previous:
        _seed_prefix(path, previous)
    # On Windows the second spelling must map to the identical lock as the first.
    alias = Path(str(path).swapcase()) if os.name == "nt" else path.parent / ".." / "catalog" / path.name
    at_first_sql = threading.Event()
    second_at_lock = threading.Event()
    release_first = threading.Event()
    original_apply = migrate._apply_migration
    original_lock = migrate._database_lock
    applied: list[int] = []
    second_thread: int | None = None

    def tracked_lock(candidate):
        lock = original_lock(candidate)
        if threading.get_ident() == second_thread:
            second_at_lock.set()
        return lock

    def paused_apply(connection, version, script):
        if version == previous + 1 and not at_first_sql.is_set():
            at_first_sql.set()
            assert release_first.wait(20), "test did not release the first migrator"
        applied.append(version)
        return original_apply(connection, version, script)

    def second_open():
        nonlocal second_thread
        second_thread = threading.get_ident()
        return migrate.migrate_database(alias, data_root=root)

    monkeypatch.setattr(migrate, "_database_lock", tracked_lock)
    monkeypatch.setattr(migrate, "_apply_migration", paused_apply)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(migrate.migrate_database, path, data_root=root)
        try:
            assert at_first_sql.wait(20), "first migrator did not reach its SQL gate"
            lock = original_lock(alias)
            acquired = lock.acquire(blocking=False)
            if acquired:
                lock.release()
            assert not acquired, "same-path protection must already cover preflight and backup"
            second = executor.submit(second_open)
            assert second_at_lock.wait(20), "second migrator did not contend for the same database"
        finally:
            release_first.set()
        first_result = first.result(timeout=20)
        second_result = second.result(timeout=20)

    expected = tuple(range(previous + 1, schema.SUPPORTED_SCHEMA_VERSION + 1))
    assert Counter(applied) == Counter(expected)
    assert first_result.previous_version == previous
    assert first_result.applied == expected
    assert second_result.previous_version == schema.SUPPORTED_SCHEMA_VERSION
    assert second_result.applied == ()
    assert second_result.backup_path is None
    assert migrate.inspect_database(path) == schema.SUPPORTED_SCHEMA_VERSION
    if previous:
        assert first_result.backup_path is not None
        assert migrate.inspect_database(first_result.backup_path) == previous
        assert list(first_result.backup_path.parent.glob("*.db")) == [first_result.backup_path]
    else:
        assert first_result.backup_path is None


def test_distinct_databases_migrate_while_another_migration_is_paused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_root, second_root = tmp_path / "first", tmp_path / "second"
    first_path, second_path = first_root / "catalog.db", second_root / "catalog.db"
    paused = threading.Event()
    release_first = threading.Event()
    original_apply = migrate._apply_migration

    def paused_apply(connection, version, script):
        database = connection.execute("PRAGMA database_list").fetchone()[2]
        if version == 1 and Path(database) == first_path:
            paused.set()
            assert release_first.wait(20), "test did not release the first database"
        return original_apply(connection, version, script)

    monkeypatch.setattr(migrate, "_apply_migration", paused_apply)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(migrate.migrate_database, first_path, data_root=first_root)
        try:
            assert paused.wait(20), "first database did not reach its SQL gate"
            second = executor.submit(migrate.migrate_database, second_path, data_root=second_root)
            assert second.result(timeout=15).schema_version == schema.SUPPORTED_SCHEMA_VERSION
            assert not first.done()
        finally:
            release_first.set()
        assert first.result(timeout=20).schema_version == schema.SUPPORTED_SCHEMA_VERSION


def test_readonly_inspection_waits_for_an_in_progress_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "catalog.db"
    paused = threading.Event()
    inspector_at_lock = threading.Event()
    release_writer = threading.Event()
    inspector_thread: int | None = None
    original_apply = migrate._apply_migration
    original_lock = migrate._database_lock

    def tracked_lock(candidate):
        lock = original_lock(candidate)
        if threading.get_ident() == inspector_thread:
            inspector_at_lock.set()
        return lock

    def paused_apply(connection, version, script):
        if version == 2:
            paused.set()
            assert release_writer.wait(20), "test did not release the writer"
        return original_apply(connection, version, script)

    def inspect():
        nonlocal inspector_thread
        inspector_thread = threading.get_ident()
        return migrate.inspect_database(path)

    monkeypatch.setattr(migrate, "_database_lock", tracked_lock)
    monkeypatch.setattr(migrate, "_apply_migration", paused_apply)
    with ThreadPoolExecutor(max_workers=2) as executor:
        writer = executor.submit(migrate.migrate_database, path, data_root=tmp_path)
        try:
            assert paused.wait(20), "writer did not reach the intermediate version"
            reader = executor.submit(inspect)
            assert inspector_at_lock.wait(20), "inspector did not contend for the database"
        finally:
            release_writer.set()
        assert writer.result(timeout=20).schema_version == schema.SUPPORTED_SCHEMA_VERSION
        assert reader.result(timeout=20) == schema.SUPPORTED_SCHEMA_VERSION


def test_failed_migrator_releases_lock_for_another_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "catalog.db"
    original_apply = migrate._apply_migration
    injected = False

    def fail_before_six(connection, version, script):
        nonlocal injected
        if version == 6 and not injected:
            injected = True
            raise RuntimeError("injected migration interruption")
        return original_apply(connection, version, script)

    monkeypatch.setattr(migrate, "_apply_migration", fail_before_six)
    with pytest.raises(RuntimeError, match="injected migration interruption"):
        migrate.migrate_database(path, data_root=tmp_path)
    assert migrate.inspect_database(path) == 5
    with ThreadPoolExecutor(max_workers=1) as executor:
        recovered = executor.submit(migrate.migrate_database, path, data_root=tmp_path).result(timeout=20)
    assert recovered.previous_version == 5
    assert recovered.schema_version == schema.SUPPORTED_SCHEMA_VERSION
    assert recovered.applied == tuple(range(6, schema.SUPPORTED_SCHEMA_VERSION + 1))


def test_unused_database_locks_are_released(tmp_path: Path) -> None:
    path = tmp_path / "unused.db"
    lock = migrate._database_lock(path)
    reference = weakref.ref(lock)
    assert migrate._database_lock(path) is lock
    del lock
    gc.collect()
    assert reference() is None
    assert not path.exists()
