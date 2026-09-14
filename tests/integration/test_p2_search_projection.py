"""P2-04: the durable affected set, bounded batched re-derivation and its recovery.

The directory edit happens in a short transaction that only records *which* assets are
affected; the derived text is written later, in bounded batches, from SQLite alone. These
tests inject an interruption in the middle of such a batch and prove the rerun neither
duplicates rows nor loses a new classification (architecture §4.5.3/§9.3).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from photo_search.catalog.db import Catalog
from photo_search.places import directory
from photo_search.places import revision as revision_module
from photo_search.places.filter import place_condition
from photo_search.search import documents


def open_catalog(tmp_path: Path) -> Catalog:
    return Catalog.open(tmp_path / "data" / "catalog" / "catalog.db", lease_seconds=60)


@pytest.fixture()
def catalog(tmp_path: Path) -> Catalog:
    instance = open_catalog(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


@dataclass(frozen=True)
class Seeded:
    assets: list[int]
    country: int
    city: int


def seed(catalog: Catalog, *, assets: int, place_names: tuple[str, ...]) -> Seeded:
    """One source, ``assets`` photos and a country > city chain, all in one transaction."""
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO source_roots(display_name, root_path, volume_hint, status) "
            "VALUES ('library', 'C:\\photos', 'C:', 'ONLINE')"
        )
        asset_ids: list[int] = []
        for index in range(assets):
            cursor = connection.execute(
                "INSERT INTO assets(sha256, byte_size, created_at, state) "
                "VALUES (?, 4096, 1000, 'ACTIVE')",
                (hashlib.sha256(f"asset-{index}".encode()).hexdigest(),),
            )
            asset_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, size, "
                "mtime_ns, last_seen_scan_id, availability) "
                "VALUES (1, ?, ?, ?, 4096, 1, NULL, 'PRESENT')",
                (f"photo-{index}.jpg", f"photo-{index}.jpg", asset_id),
            )
            asset_ids.append(asset_id)
    country = add_place(catalog, kind="country", name=place_names[0])
    city = add_place(catalog, kind="city", name=place_names[1], parent_place_id=country)
    return Seeded(assets=asset_ids, country=country, city=city)


def add_place(
    catalog: Catalog, *, kind: str, name: str, parent_place_id: int | None = None
) -> int:
    with catalog.write() as connection:
        cursor = connection.execute(
            "INSERT INTO places(kind, canonical_name, normalized_name, parent_place_id, origin, "
            "lifecycle, revision, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'USER', 'ACTIVE', 1, 5, 5)",
            (kind, name, name, parent_place_id),
        )
        return int(cursor.lastrowid)


def add_operation(catalog: Catalog, operation_id: str) -> None:
    with catalog.write() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO place_operations(operation_id, request_id, payload_sha256, "
            "operation_type, operation_revision, actor, atomic, status, catalog_filter_revision, "
            "created_at, committed_at) VALUES (?, ?, ?, 'ASSIGN', 1, 'LOCAL_USER', 1, "
            "'COMMITTED', 0, 5, 5)",
            (
                operation_id,
                f"req-{operation_id}",
                hashlib.sha256(operation_id.encode()).hexdigest(),
            ),
        )


def assign(catalog: Catalog, asset_id: int, place_id: int, *, operation_id: str = "op-1") -> None:
    add_operation(catalog, operation_id)
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO asset_place_assignments(asset_id, assignment_id, place_id, mode, origin, "
            "manual_lock, resolution_id, revision, record_state, actor, operation_id, created_at, "
            "updated_at) VALUES (?, ?, ?, 'MANUAL_ASSIGNED', 'MANUAL', 1, NULL, 1, 'ACTIVE', "
            "'LOCAL_USER', ?, 5, 5) ON CONFLICT(asset_id) DO UPDATE SET "
            "place_id = excluded.place_id, revision = asset_place_assignments.revision + 1",
            (int(asset_id), f"asg-{asset_id}", int(place_id), operation_id),
        )


def queued(catalog: Catalog) -> list[int]:
    return [
        int(row["asset_id"])
        for row in catalog.read_all(
            "SELECT asset_id FROM search_projection_queue ORDER BY asset_id"
        )
    ]


def rename(catalog: Catalog, place_id: int, name: str) -> dict[str, object]:
    with catalog.write() as connection:
        view, changed = directory.patch_place(
            connection,
            place_id=int(place_id),
            expected_revision=int(
                connection.execute(
                    "SELECT revision FROM places WHERE place_id = ?", (int(place_id),)
                ).fetchone()["revision"]
            ),
            canonical_name=name,
            now_ms=100,
            refresh=None,
        )
    return {"view": view, "changed": changed}


def hits(catalog: Catalog, query: str) -> list[int]:
    from photo_search.search.query import text_condition
    from photo_search.search.tokens import build_match

    sql, parameters = text_condition(build_match(query))
    return [
        int(row["asset_id"])
        for row in catalog.read_all(
            f"SELECT a.asset_id FROM assets AS a WHERE {sql} ORDER BY a.asset_id",
            tuple(parameters),
        )
    ]


def place_hits(catalog: Catalog, place_ids: list[int]) -> list[int]:
    sql, parameters = place_condition(
        catalog.connection, place_ids=place_ids, include_descendants=True
    )
    return [
        int(row["asset_id"])
        for row in catalog.read_all(
            f"SELECT a.asset_id FROM assets AS a WHERE {sql} ORDER BY a.asset_id",
            tuple(parameters),
        )
    ]


def test_a_rename_persists_the_affected_subtree_and_moves_the_filter_revision_once(
    catalog: Catalog,
) -> None:
    seeded = seed(catalog, assets=3, place_names=("中国", "杭州"))
    first, second, third = seeded.assets
    assign(catalog, first, seeded.country)
    assign(catalog, second, seeded.city, operation_id="op-2")
    assign(catalog, third, seeded.city, operation_id="op-3")
    assert documents.catch_up(catalog)["state"] == "SYNCED"
    assert queued(catalog) == []
    revision_before = revision_module.read_filter_revision(catalog.connection)
    suggestion_before = revision_module.read_suggestion_revision(catalog.connection)

    # One PATCH over the parent place; everything else must happen in that same transaction.
    rename(catalog, seeded.country, "中国新区")

    # The affected set is durable, and it is the whole subtree - not just the assets that
    # are assigned to the renamed place itself.
    assert queued(catalog) == [first, second, third]
    assert revision_module.read_filter_revision(catalog.connection) == revision_before + 1
    assert revision_module.read_suggestion_revision(catalog.connection) == suggestion_before
    assert documents.queue_depth(catalog.connection) == 3

    # The old text is still searchable until the derived batch runs: that is exactly the
    # lag the caller is told about, and the authoritative place filter is right already.
    assert place_hits(catalog, [seeded.country]) == [first, second, third]
    assert hits(catalog, "杭州") == [second, third]

    assert documents.catch_up(catalog)["state"] == "SYNCED"
    assert queued(catalog) == []
    assert hits(catalog, "中国新区") == [first, second, third]
    assert hits(catalog, "杭州") == [second, third]
    assert [str(row["place_text"]) for row in catalog.read_all(
        "SELECT place_text FROM search_documents ORDER BY asset_id"
    )] == [
        "中 国 新 区",
        "中 国 新 区 杭 州",
        "中 国 新 区 杭 州",
    ]
    # A directory edit is a text/projection fact only: no vector work is created, and the
    # insertion order that ``time_desc`` pages by is untouched.
    for table in ("artifacts", "index_outbox", "index_entries", "index_generations"):
        assert catalog.read_one(f"SELECT COUNT(*) AS value FROM {table}")["value"] == 0
    assert [int(row["created_at"]) for row in catalog.read_all(
        "SELECT created_at FROM assets ORDER BY asset_id"
    )] == [1000, 1000, 1000]


def test_an_alias_edit_and_an_assignment_change_queue_only_their_own_assets(
    catalog: Catalog,
) -> None:
    seeded = seed(catalog, assets=3, place_names=("中国", "杭州"))
    first, second, _third = seeded.assets
    assign(catalog, first, seeded.city)
    assign(catalog, second, seeded.city, operation_id="op-2")
    documents.catch_up(catalog)

    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO place_aliases(place_id, text, normalized, language, source, lifecycle, "
            "revision, created_at, updated_at) "
            "VALUES (?, 'Hangzhou', 'hangzhou', 'en', 'USER', 'ACTIVE', 1, 5, 5)",
            (seeded.city,),
        )
    assert queued(catalog) == [first, second]
    documents.catch_up(catalog)
    assert hits(catalog, "hangzhou") == [first, second]

    third = seeded.assets[2]
    assign(catalog, third, seeded.city, operation_id="op-3")
    assert queued(catalog) == [third]
    documents.catch_up(catalog)
    assert hits(catalog, "hangzhou") == [first, second, third]


def test_the_catch_up_is_bounded_and_reports_the_lag_it_leaves(catalog: Catalog) -> None:
    seeded = seed(catalog, assets=5, place_names=("中国", "杭州"))
    for index, asset_id in enumerate(seeded.assets):
        assign(catalog, int(asset_id), seeded.city, operation_id=f"op-{index}")
    documents.catch_up(catalog)
    rename(catalog, seeded.country, "中国新区")

    first_batch = documents.catch_up(catalog, batches=1, batch_size=2)
    assert first_batch["processed"] == 2
    assert first_batch["remaining"] == 3
    assert first_batch["state"] == "LAGGING"

    # The authoritative filter is already correct while the text index is still behind.
    assert place_hits(catalog, [seeded.country]) == sorted(seeded.assets)
    summary = documents.projection_summary(catalog.connection)
    assert summary["queued_assets"] == 3
    assert summary["documents"] == 5
    assert summary["tokenizer_version"] == 1
    assert summary["fts_generation_id"] == "fts-v1"

    final = documents.catch_up(catalog)
    assert final["state"] == "SYNCED" and final["remaining"] == 0
    assert hits(catalog, "中国新区") == sorted(seeded.assets)


def test_an_interrupted_batch_rolls_back_and_the_rerun_has_no_duplicates(
    catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = seed(catalog, assets=5, place_names=("中国", "杭州"))
    for index, asset_id in enumerate(seeded.assets):
        assign(catalog, int(asset_id), seeded.city, operation_id=f"op-{index}")
    documents.catch_up(catalog)
    before = [tuple(row) for row in catalog.read_all(
        "SELECT asset_id, place_text FROM search_documents ORDER BY asset_id"
    )]
    rename(catalog, seeded.country, "中国新区")

    real_sync = documents.sync_document
    calls = {"count": 0}

    def flaky(connection, asset_id, *, now_ms):  # type: ignore[no-untyped-def]
        calls["count"] += 1
        if calls["count"] == 3:
            raise RuntimeError("simulated interruption in the middle of one batch")
        return real_sync(connection, asset_id, now_ms=now_ms)

    monkeypatch.setattr(documents, "sync_document", flaky)
    with pytest.raises(RuntimeError):
        with catalog.write() as connection:
            documents.drain_queue(connection, now_ms=100, limit=documents.DRAIN_BATCH_SIZE)
    monkeypatch.setattr(documents, "sync_document", real_sync)

    # The whole batch rolled back: no half-written text, and the affected set is intact.
    assert queued(catalog) == sorted(seeded.assets)
    assert [tuple(row) for row in catalog.read_all(
        "SELECT asset_id, place_text FROM search_documents ORDER BY asset_id"
    )] == before
    assert hits(catalog, "中国新区") == []

    assert documents.catch_up(catalog)["state"] == "SYNCED"
    assert queued(catalog) == []
    # No duplicate document and no duplicate index entry, and the new name is visible.
    assert catalog.read_one("SELECT COUNT(*) AS value FROM search_documents")["value"] == 5
    assert hits(catalog, "中国新区") == sorted(seeded.assets)
    documents.verify_index(catalog.connection)
    assert documents.stale_document_asset_ids(catalog.connection) == []
    # A second run has nothing left to do and must not rewrite anything.
    assert documents.catch_up(catalog) == {
        "processed": 0,
        "written": 0,
        "remaining": 0,
        "batches": 0,
        "state": "SYNCED",
    }


def test_rebuild_all_only_rewrites_documents_of_a_stale_generation(
    catalog: Catalog,
) -> None:
    seeded = seed(catalog, assets=3, place_names=("中国", "杭州"))
    assign(catalog, seeded.assets[0], seeded.city)
    documents.catch_up(catalog)

    # Nothing changed: a rebuild is a no-op rather than a full rewrite.
    assert documents.rebuild_all(catalog)["written"] == 0
    with catalog.write() as connection:
        # A row written by an older generation: the document identity says so, and the
        # rebuild must re-derive it rather than trust the stored fingerprint.
        connection.execute(
            "UPDATE search_documents SET fts_generation_id = 'fts-v0' "
            "WHERE asset_id = ?",
            (seeded.assets[0],),
        )
    summary = documents.projection_summary(catalog.connection)
    assert summary["stale_documents"] == 1

    result = documents.rebuild_all(catalog)
    assert result["written"] == 1 and result["processed"] == 3
    assert documents.projection_summary(catalog.connection)["stale_documents"] == 0
    row = documents.document_row(catalog.connection, seeded.assets[0])
    assert int(row["tokenizer_version"]) == documents.TOKENIZER_VERSION
    assert str(row["fts_generation_id"]) == documents.FTS_GENERATION_ID
