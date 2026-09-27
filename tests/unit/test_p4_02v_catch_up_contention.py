"""P4-02V: an interactive catch-up degrades under the background drain's write lock.

P4-02U §1/§5 established the mechanism: while the semantic drain builds, it holds the SQLite
single-writer lock ~99% of the time with continuous unavailability up to 32 449.4 ms, so a
legal text search whose projection catch-up waited the 5 s maintenance budget was refused with
``CATALOG_UNREADABLE`` (thread arm 5/6, process arm 4/6 - a violation of architecture §12.2's
"不得因内部争用拒绝合法验证请求"). These tests pin the repair: a bounded wait, then a
``LAGGING`` answer with the affected set still durably queued - and no change to the envelope,
the error codes, or the maintenance budget of any other writer.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

import pytest

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import CATALOG_UNREADABLE, CatalogError
from photo_search.search import documents, service

MAINTENANCE_BUSY_TIMEOUT_MS = 5000


def open_catalog(tmp_path: Path) -> Catalog:
    return Catalog.open(tmp_path / "data" / "catalog" / "catalog.db", lease_seconds=60)


@pytest.fixture()
def catalog(tmp_path: Path) -> Catalog:
    instance = open_catalog(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def queue_one_asset(catalog: Catalog, *, name: str = "holiday.jpg") -> int:
    """One present asset whose derived text is stale - the state a new import leaves behind."""
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO source_roots(display_name, root_path, volume_hint, status) "
            "VALUES ('library', 'C:\\photos', 'C:', 'ONLINE')"
        )
        source_id = int(connection.execute("SELECT MAX(source_id) FROM source_roots").fetchone()[0])
        cursor = connection.execute(
            "INSERT INTO assets(sha256, byte_size, created_at, state) "
            "VALUES (?, 4096, 1000, 'ACTIVE')",
            (hashlib.sha256(f"p4-02v-asset-{name}".encode()).hexdigest(),),
        )
        asset_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, size, "
            "mtime_ns, last_seen_scan_id, availability) "
            "VALUES (?, ?, ?, ?, 4096, 0, NULL, 'PRESENT')",
            (source_id, name, name, asset_id),
        )
    # ``trg_file_locations_search_queue_insert`` queues the derived document itself, exactly
    # like a real import does: the asset is present and its text projection is stale.
    assert documents.queue_depth(catalog.connection) == 1
    return asset_id


def hold_writer_lock(db_path: Path) -> sqlite3.Connection:
    """A second connection that really holds ``BEGIN IMMEDIATE`` - the drain's shape."""
    holder = sqlite3.connect(str(db_path), isolation_level=None)
    holder.execute("PRAGMA busy_timeout = 100")
    holder.execute("BEGIN IMMEDIATE")
    return holder


def release(holder: sqlite3.Connection) -> None:
    holder.execute("ROLLBACK")
    holder.close()


def test_catch_up_answers_lagging_while_the_write_lock_is_held(catalog: Catalog) -> None:
    queue_one_asset(catalog)
    holder = hold_writer_lock(catalog.db_path)
    started = time.perf_counter()
    try:
        result = documents.catch_up(catalog)
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        release(holder)

    assert result["state"] == "LAGGING"
    assert result["processed"] == 0
    assert result["written"] == 0
    assert result["batches"] == 0
    assert result["deferred_batches"] == 1
    assert result["remaining"] == 1
    # The bound is the interactive budget, not the 5 s maintenance budget.
    assert elapsed_ms < 2000
    # Nothing was written and nothing was lost: the asset is still queued and undocumented.
    assert documents.queue_depth(catalog.connection) == 1
    assert documents.projection_summary(catalog.connection)["documents"] == 0

    # Once the drain lets go, the very next catch-up derives it and reports SYNCED.
    healed = documents.catch_up(catalog)
    assert healed["state"] == "SYNCED"
    assert healed["deferred_batches"] == 0
    assert healed["processed"] == 1
    assert documents.queue_depth(catalog.connection) == 0
    assert documents.projection_summary(catalog.connection)["documents"] == 1


def test_the_maintenance_budget_of_the_connection_is_restored(catalog: Catalog) -> None:
    queue_one_asset(catalog)
    before = int(catalog.connection.execute("PRAGMA busy_timeout").fetchone()[0])
    assert before == MAINTENANCE_BUSY_TIMEOUT_MS
    holder = hold_writer_lock(catalog.db_path)
    try:
        documents.catch_up(catalog)
    finally:
        release(holder)
    assert int(catalog.connection.execute("PRAGMA busy_timeout").fetchone()[0]) == before
    # The empty-queue shortcut and the successful path leave it alone as well.
    assert documents.catch_up(catalog)["state"] == "SYNCED"
    assert int(catalog.connection.execute("PRAGMA busy_timeout").fetchone()[0]) == before


def test_a_catalog_error_that_is_not_a_busy_refusal_still_propagates(
    catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue_one_asset(catalog)

    def exploding(*args: object, **kwargs: object) -> dict[str, int]:
        raise CatalogError(CATALOG_UNREADABLE, "catalog connection is closed")

    monkeypatch.setattr(documents, "drain_queue", exploding)
    with pytest.raises(CatalogError) as refusal:
        documents.catch_up(catalog)
    assert str(refusal.value.code) == CATALOG_UNREADABLE
    assert documents.queue_depth(catalog.connection) == 1


def test_a_text_search_serves_a_lagging_page_instead_of_refusing(catalog: Catalog) -> None:
    first = queue_one_asset(catalog, name="holiday.jpg")
    synced = service.search_assets(catalog, text="holiday", top_k=5)
    assert synced["search_projection"]["state"] == "SYNCED"
    assert [hit["asset_id"] for hit in synced["hits"]] == [first]

    second = queue_one_asset(catalog, name="holiday-2.jpg")
    holder = hold_writer_lock(catalog.db_path)
    try:
        lagging = service.search_assets(catalog, text="holiday", top_k=5)
    finally:
        release(holder)

    # The legal search is answered instead of refused (P4-02U: CATALOG_UNREADABLE).
    assert lagging["state"] == "OK"
    assert lagging["search_projection"]["state"] == "LAGGING"
    assert lagging["search_projection"]["queued_assets"] == 1
    assert lagging["search_projection"]["processed_assets"] == 0
    # It serves the projection it has: only the already derived asset is ranked, because the
    # second asset's text is (still) not derived - no fabricated or half-written document.
    assert [hit["asset_id"] for hit in lagging["hits"]] == [first]
    assert documents.projection_summary(catalog.connection)["documents"] == 1
    # Nothing about the envelope changed: same keys, same contract version, same error codes.
    assert set(lagging) == set(synced)
    assert set(lagging["search_projection"]) == set(synced["search_projection"])
    assert lagging["contract_version"] == synced["contract_version"]

    # The deferred asset is not lost: the next catch-up derives it.
    healed = documents.catch_up(catalog)
    assert healed["state"] == "SYNCED"
    assert documents.projection_summary(catalog.connection)["documents"] == 2
    assert [hit["asset_id"] for hit in service.search_assets(catalog, text="holiday", top_k=5)["hits"]] == [
        first,
        second,
    ]