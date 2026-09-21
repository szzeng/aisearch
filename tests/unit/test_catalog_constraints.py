"""P1-01: identity and value constraints that must live in the database itself."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from photo_search.catalog import store
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import INVALID_STATE_TRANSITION, CatalogError
from photo_search.places import revision as revision_module

SHA_A = "a" * 64
SHA_B = "b" * 64


@pytest.fixture()
def catalog(tmp_path: Path) -> Catalog:
    instance = Catalog.open(tmp_path / "data" / "catalog" / "catalog.db")
    try:
        yield instance
    finally:
        instance.close()


def _insert_source(connection: sqlite3.Connection, name: str = "photos") -> int:
    return store.insert_source(
        connection,
        display_name=name,
        root_path=f"C:\\{name}",
        volume_hint="C:",
        status="ONLINE",
    )


def _insert_location(
    connection: sqlite3.Connection,
    *,
    source_id: int,
    path: str,
    asset_id: int | None = None,
) -> int:
    location_id, _ = store.observe_location(
        connection,
        source_id=source_id,
        scan_id=_insert_scan(connection, source_id),
        relative_path=path,
        path_key=path.lower(),
        size=4,
        mtime_ns=1,
    )
    if asset_id is not None:
        connection.execute(
            "UPDATE file_locations SET asset_id = ? WHERE location_id = ?", (asset_id, location_id)
        )
    return location_id


def _insert_scan(connection: sqlite3.Connection, source_id: int) -> int:
    row = store.find_active_scan(connection, source_id)
    if row is not None:
        return int(row["scan_id"])
    return store.insert_scan_run(connection, source_id=source_id)


def test_asset_sha256_is_the_unique_content_identity(catalog: Catalog) -> None:
    with catalog.write() as connection:
        store.upsert_asset(connection, sha256=SHA_A, byte_size=4, now_ms=1)
    with pytest.raises(sqlite3.IntegrityError):
        with catalog.write() as connection:
            connection.execute(
                "INSERT INTO assets (sha256, byte_size, created_at, state) VALUES (?, 9, 1, 'ACTIVE')",
                (SHA_A,),
            )
    with pytest.raises(sqlite3.IntegrityError):
        with catalog.write() as connection:
            connection.execute(
                "INSERT INTO assets (sha256, byte_size, created_at, state) VALUES (?, 4, 1, 'ACTIVE')",
                ("A" * 64,),
            )
    with pytest.raises(sqlite3.IntegrityError):
        with catalog.write() as connection:
            connection.execute(
                "INSERT INTO assets (sha256, byte_size, created_at, state) VALUES (?, 4, 1, 'ACTIVE')",
                ("z" * 64,),
            )
    with pytest.raises(sqlite3.IntegrityError):
        with catalog.write() as connection:
            connection.execute(
                "INSERT INTO assets (sha256, byte_size, created_at, state) VALUES (?, -1, 1, 'ACTIVE')",
                (SHA_B,),
            )
    with pytest.raises(sqlite3.IntegrityError):
        with catalog.write() as connection:
            connection.execute(
                "INSERT INTO assets (sha256, byte_size, created_at, state) VALUES (?, 4, 1, 'DELETED')",
                (SHA_B,),
            )


def test_asset_size_mismatch_is_reported_not_overwritten(catalog: Catalog) -> None:
    with catalog.write() as connection:
        asset_id = store.upsert_asset(connection, sha256=SHA_A, byte_size=4, now_ms=1)
        assert store.upsert_asset(connection, sha256=SHA_A, byte_size=4, now_ms=2) == asset_id
    with pytest.raises(CatalogError):
        with catalog.write() as connection:
            store.upsert_asset(connection, sha256=SHA_A, byte_size=5, now_ms=3)
    assert catalog.read_one("SELECT byte_size FROM assets WHERE sha256 = ?", (SHA_A,))[0] == 4


def test_location_uniqueness_is_per_source_and_path_key(catalog: Catalog) -> None:
    with catalog.write() as connection:
        first = _insert_source(connection, "one")
        second = _insert_source(connection, "two")
        _insert_location(connection, source_id=first, path="a.jpg")
        _insert_location(connection, source_id=second, path="a.jpg")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO file_locations "
                "(source_id, relative_path, path_key, size, mtime_ns, availability) "
                "VALUES (?, 'A.JPG', 'a.jpg', 4, 1, 'PRESENT')",
                (first,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO file_locations "
                "(source_id, relative_path, path_key, size, mtime_ns, availability) "
                "VALUES (?, 'b.jpg', 'b.jpg', -1, 1, 'PRESENT')",
                (first,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO file_locations "
                "(source_id, relative_path, path_key, size, mtime_ns, availability) "
                "VALUES (?, 'b.jpg', 'b.jpg', 1, 1, 'UNKNOWN')",
                (first,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO file_locations "
                "(source_id, relative_path, path_key, size, mtime_ns, availability) "
                "VALUES (999, 'b.jpg', 'b.jpg', 1, 1, 'PRESENT')"
            )


def test_a_source_has_at_most_one_active_scan_run(catalog: Catalog) -> None:
    with catalog.write() as connection:
        source_id = _insert_source(connection)
        store.insert_scan_run(connection, source_id=source_id, state="RUNNING")
        with pytest.raises(sqlite3.IntegrityError):
            store.insert_scan_run(connection, source_id=source_id, state="QUEUED")
        connection.execute(
            "UPDATE scan_runs SET state = 'SUCCEEDED', finished_at = 1 WHERE source_id = ?",
            (source_id,),
        )
        assert store.insert_scan_run(connection, source_id=source_id) > 1
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE scan_runs SET state = 'NOT_A_STATE' WHERE scan_id = 1")


def test_job_logical_key_has_five_non_null_fields(catalog: Catalog) -> None:
    with catalog.write() as connection:
        connection.execute(
            """
            INSERT INTO jobs (subject_type, subject_id, input_revision, task_type, processor_id,
                              state, priority, attempts, max_attempts, run_after, progress,
                              created_at, updated_at)
            VALUES ('asset', '1', 'rev', 'embed_image', 'model@1', 'QUEUED', 0, 0, 3, 1, 0.0, 1, 1)
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO jobs (subject_type, subject_id, input_revision, task_type, processor_id,
                                  state, priority, attempts, max_attempts, run_after, progress,
                                  created_at, updated_at)
                VALUES ('asset', '1', 'rev', 'embed_image', NULL, 'QUEUED', 0, 0, 3, 1, 0.0, 1, 1)
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO jobs (subject_type, subject_id, input_revision, task_type, processor_id,
                                  state, priority, attempts, max_attempts, run_after, progress,
                                  created_at, updated_at)
                VALUES ('asset', '1', 'rev', 'embed_image', 'model@1', 'QUEUED', 0, 0, 3, 1, 0.0, 1, 1)
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO jobs (subject_type, subject_id, input_revision, task_type, processor_id,
                                  state, priority, attempts, max_attempts, run_after, progress,
                                  created_at, updated_at)
                VALUES ('asset', '2', 'rev', 'embed_image', 'model@1', 'QUEUED', 0, -1, 3, 1, 0.0, 1, 1)
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO jobs (subject_type, subject_id, input_revision, task_type, processor_id,
                                  state, priority, attempts, max_attempts, run_after, progress,
                                  created_at, updated_at)
                VALUES ('asset', '2', 'rev', 'embed_image', 'model@1', 'QUEUED', 0, 0, 0, 1, 0.0, 1, 1)
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO jobs (subject_type, subject_id, input_revision, task_type, processor_id,
                                  state, priority, attempts, max_attempts, run_after, progress,
                                  created_at, updated_at)
                VALUES ('asset', '2', 'rev', 'embed_image', 'model@1', 'QUEUED', 0, 0, 3, 1, 1.5, 1, 1)
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO jobs (subject_type, subject_id, input_revision, task_type, processor_id,
                                  state, priority, attempts, max_attempts, run_after, progress,
                                  created_at, updated_at)
                VALUES ('asset', '2', 'rev', 'embed_image', 'model@1', 'NOT_A_STATE', 0, 0, 3, 1, 0.0, 1, 1)
                """
            )


def test_lease_columns_exist_only_while_running(catalog: Catalog) -> None:
    with catalog.write() as connection:
        connection.execute(
            """
            INSERT INTO jobs (subject_type, subject_id, input_revision, task_type, processor_id,
                              state, priority, attempts, max_attempts, run_after, progress,
                              created_at, updated_at)
            VALUES ('asset', '1', 'rev', 'embed_image', 'model@1', 'RUNNING', 0, 0, 3, 1, 0.0, 1, 1)
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO jobs (subject_type, subject_id, input_revision, task_type, processor_id,
                                  state, priority, attempts, max_attempts, run_after, progress,
                                  created_at, updated_at, lease_token)
                VALUES ('asset', '2', 'rev', 'embed_image', 'model@1', 'QUEUED', 0, 0, 3, 1, 0.0, 1, 1,
                        'token')
                """
            )


def test_foreign_keys_restrict_identity_deletion(catalog: Catalog) -> None:
    with catalog.write() as connection:
        source_id = _insert_source(connection)
        asset_id = store.upsert_asset(connection, sha256=SHA_A, byte_size=4, now_ms=1)
        _insert_location(connection, source_id=source_id, path="a.jpg", asset_id=asset_id)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM source_roots WHERE source_id = ?", (source_id,))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM assets WHERE asset_id = ?", (asset_id,))


def test_observed_scan_must_belong_to_the_same_source(catalog: Catalog) -> None:
    with catalog.write() as connection:
        first = _insert_source(connection, "one")
        second = _insert_source(connection, "two")
        foreign_scan = store.insert_scan_run(connection, source_id=second)
        with pytest.raises(CatalogError) as error:
            store.observe_location(
                connection,
                source_id=first,
                scan_id=foreign_scan,
                relative_path="a.jpg",
                path_key="a.jpg",
                size=1,
                mtime_ns=1,
            )
        assert error.value.code == INVALID_STATE_TRANSITION
        with pytest.raises(CatalogError):
            store.reconcile_missing(connection, source_id=first, scan_id=foreign_scan)
        assert connection.execute("SELECT COUNT(*) FROM file_locations").fetchone()[0] == 0


def test_scan_run_has_no_generic_running_self_loop(catalog: Catalog) -> None:
    with catalog.write() as connection:
        source_id = _insert_source(connection)
        scan_id = store.insert_scan_run(connection, source_id=source_id)
        started = store.set_scan_state(connection, scan_id=scan_id, state="RUNNING", now_ms=1)
        assert started["state"] == "RUNNING"
        assert started["started_at"] == 1
        with pytest.raises(CatalogError) as error:
            store.set_scan_state(connection, scan_id=scan_id, state="RUNNING", now_ms=1)
        assert error.value.code == INVALID_STATE_TRANSITION


# ------------------------------------------------- the one-state read-snapshot guarantee
def test_a_read_snapshot_holds_one_state_across_a_concurrent_commit(catalog: Catalog) -> None:
    """``Catalog.read_snapshot()`` is ONE SQLite snapshot, not a sequence of statements.

    ADR-0009 §4.2 requires several canonical resolutions - and the ``catalog_filter_revision``
    they are anchored to - to come out of one read-only snapshot. ``Catalog.connection`` runs
    in autocommit mode, so this primitive is what makes that true: a commit from a *second*
    connection between two reads inside the block stays invisible until the block exits, while
    the block itself never writes.
    """
    second = Catalog.open(catalog.db_path)
    try:
        before = int(catalog.read_one("SELECT revision FROM catalog_filter_revision")["revision"])
        with catalog.read_snapshot() as connection:
            first = int(
                connection.execute(
                    "SELECT revision FROM catalog_filter_revision WHERE revision_id = 1"
                ).fetchone()["revision"]
            )
            with second.write() as writer:
                revision_module.bump_filter_revision(writer, cause="PERSON_CHANGED", now_ms=1)
            second_read = int(
                connection.execute(
                    "SELECT revision FROM catalog_filter_revision WHERE revision_id = 1"
                ).fetchone()["revision"]
            )
            # A write of this process must not silently join an open read snapshot either:
            # ``search_documents.catch_up`` is the one search-time writer, and it has to run
            # outside the block.
            with pytest.raises(RuntimeError):
                with catalog.write():
                    pass
        after = int(catalog.read_one("SELECT revision FROM catalog_filter_revision")["revision"])
    finally:
        second.close()
    assert first == second_read == before
    assert after == before + 1
