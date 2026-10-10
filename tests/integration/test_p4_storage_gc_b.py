"""P4-STORAGE-GC-B: synthetic LanceDB compaction and SQLite VACUUM checks."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from photo_search.config import catalog_db_path
from photo_search.indexing.store import LanceVectorIndex
from photo_search.runtime.data_root_lease import acquire_data_root_lease
from photo_search.storage_maintenance import storage_optimize_vectors, storage_vacuum

GENERATION = {
    "generation_id": "gc-b-generation",
    "space_id": "gc-b-space",
    "processor_id": "synthetic@1",
    "dimension": 3,
}


def _record(asset_id: int, vector: list[float]) -> dict[str, object]:
    return {
        "record_id": f"{GENERATION['generation_id']}:{asset_id}",
        "asset_id": asset_id,
        "generation_id": GENERATION["generation_id"],
        "space_id": GENERATION["space_id"],
        "artifact_revision": f"artifact-{asset_id}",
        "projection_revision": 1,
        "vector": vector,
        "source_ids": [asset_id],
        "visible": True,
    }


def _catalog(root: Path, *, rows: int = 0, candidate: bool = False) -> Path:
    path = catalog_db_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            "CREATE TABLE index_generations(generation_id TEXT, space_id TEXT, processor_id TEXT, "
            "dimension INTEGER, state TEXT, is_active INTEGER);"
            "CREATE TABLE index_entries(space_id TEXT, asset_id INTEGER, generation_id TEXT, state TEXT);"
            "CREATE TABLE index_outbox(generation_id TEXT, state TEXT);"
            "CREATE TABLE search_profiles(profile_id TEXT, image_generation_id TEXT);"
            "CREATE TABLE active_search_profile(state_id INTEGER, profile_id TEXT, updated_at INTEGER);"
            "INSERT INTO active_search_profile VALUES (1, 'profile-1', 7);"
        )
        connection.execute(
            "INSERT INTO index_generations VALUES (?, ?, ?, ?, 'READY', 1)",
            tuple(GENERATION.values()),
        )
        connection.execute(
            "INSERT INTO search_profiles VALUES ('profile-1', ?)", (GENERATION["generation_id"],)
        )
        connection.executemany(
            "INSERT INTO index_entries VALUES (?, ?, ?, 'CONFIRMED')",
            (
                (GENERATION["space_id"], asset_id, GENERATION["generation_id"])
                for asset_id in range(1, rows + 1)
            ),
        )
        if candidate:
            connection.execute(
                "INSERT INTO index_generations VALUES ('candidate', 'gc-b-space', 'candidate@1', 3, 'PENDING', 0)"
            )
    return path


def _populate(root: Path, batches: tuple[tuple[int, ...], ...]) -> LanceVectorIndex:
    index = LanceVectorIndex(root / "vectors")
    for batch in batches:
        index.upsert_batch(
            generation=GENERATION,
            records=[_record(asset_id, [float(asset_id), 1.0, 0.5]) for asset_id in batch],
        )
    return index


def test_b1_compaction_preserves_rows_vectors_search_and_order(tmp_path: Path) -> None:
    root = tmp_path / "data"
    _catalog(root, rows=4)
    index = _populate(root, ((1, 2), (3,), (4,)))
    before_rows = index.rows(generation_id=GENERATION["generation_id"], dimension=3)
    before = index.search(
        generation_id=GENERATION["generation_id"],
        dimension=3,
        query_vector=[1.0, 1.0, 0.5],
        limit=4,
        space_id=GENERATION["space_id"],
    )

    result = storage_optimize_vectors(root, apply=True, stopped=True)

    after_rows = index.rows(generation_id=GENERATION["generation_id"], dimension=3)
    after = index.search(
        generation_id=GENERATION["generation_id"],
        dimension=3,
        query_vector=[1.0, 1.0, 0.5],
        limit=4,
        space_id=GENERATION["space_id"],
    )
    assert result["status"] == "ok", result
    assert len(before_rows) == len(after_rows) == 4
    assert [(row["asset_id"], row["vector"]) for row in before_rows] == [
        (row["asset_id"], row["vector"]) for row in after_rows
    ]
    assert [(row["asset_id"], row["distance"]) for row in before] == [
        (row["asset_id"], row["distance"]) for row in after
    ]
    assert result["before"]["search"] == result["after"]["search"]
    assert result["stages"][-1]["name"] == "result_verification"


def test_b2_repairs_scalar_index_coverage_only_when_incomplete(tmp_path: Path) -> None:
    root = tmp_path / "data"
    _catalog(root, rows=3)
    index = _populate(root, ((1, 2),))
    index.ensure_scalar_index(generation_id=GENERATION["generation_id"], dimension=3)
    index.upsert_batch(generation=GENERATION, records=[_record(3, [3.0, 1.0, 0.5])])
    result = storage_optimize_vectors(root, apply=True, stopped=True)

    assert result["status"] == "ok", result
    coverage = result["stages"][0]["coverage"]
    assert coverage["state"] == "complete"
    assert coverage["num_unindexed_rows"] == 0
    assert result["stages"][0]["outcome"]["state"] in {"refreshed", "present"}


def test_b3_candidate_busy_and_compaction_interruption_are_safe_to_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data"
    _catalog(root, rows=2, candidate=True)
    index = _populate(root, ((1,), (2,)))
    blocked = storage_optimize_vectors(root)
    assert blocked["status"] == "skipped"
    assert "candidate_generation_building" in blocked["generations"][0]["skip_reasons"]

    with sqlite3.connect(catalog_db_path(root)) as connection:
        connection.execute("DELETE FROM index_generations WHERE generation_id = 'candidate'")
        connection.execute(
            "INSERT INTO index_generations VALUES ('retired-generation', 'gc-b-space', 'old@1', 3, 'RETIRED', 0)"
        )
    retired_generation = {
        "generation_id": "retired-generation",
        "space_id": "gc-b-space",
        "dimension": 3,
    }
    retired_record = _record(88, [0.0, 1.0, 0.5])
    retired_record.update({"record_id": "retired:88", "generation_id": "retired-generation"})
    index.upsert_batch(generation=retired_generation, records=[retired_record])
    retired_table = index._table(generation_id="retired-generation", dimension=3, create=False)
    retired_version = int(retired_table.version)

    service_lease = acquire_data_root_lease(root)
    try:
        lease_busy = storage_optimize_vectors(root, apply=True, stopped=True)
        assert lease_busy["code"] == "DATA_ROOT_BUSY"
    finally:
        service_lease.close()

    from photo_search import storage as storage_module

    scan_lock = storage_module._try_scan_lock(root)
    assert scan_lock is not None
    try:
        busy = storage_optimize_vectors(root, apply=True, stopped=True)
        assert busy["code"] == "STORAGE_SCAN_BUSY"
    finally:
        storage_module._release_scan_lock(scan_lock)

    table = index._table(generation_id=GENERATION["generation_id"], dimension=3, create=False)
    table_type = type(table)
    original = table_type.compact_files
    calls = 0

    def fail_once(self: object, *args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic interruption")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(table_type, "compact_files", fail_once)
    failed = storage_optimize_vectors(root, apply=True, stopped=True)
    assert failed["status"] == "failed"
    interrupted = next(stage for stage in failed["stages"] if stage["name"] == "compact_files")
    assert interrupted["reason"] == "RuntimeError"
    assert int(table.count_rows()) == 2
    retry = storage_optimize_vectors(root, apply=True, stopped=True)
    assert retry["status"] == "ok", retry
    assert int(retired_table.count_rows()) == 1
    assert int(retired_table.version) == retired_version
    retired_diag = next(
        item for item in retry["generations"] if item["generation_id"] == "retired-generation"
    )
    assert "generation_not_active_ready" in retired_diag["skip_reasons"]
    cleanup = next(stage for stage in retry["stages"] if stage["name"] == "cleanup_old_versions")
    assert cleanup["older_than_days"] == 14 and cleanup["delete_unverified"] is False
    assert cleanup["actual_reclaimed_bytes"] is not None


def test_b4_vacuum_preserves_catalog_facts_and_reports_physical_bytes(tmp_path: Path) -> None:
    root = tmp_path / "data"
    path = _catalog(root)
    with sqlite3.connect(path) as connection:
        for table in (
            "persons",
            "person_collections",
            "places",
            "ocr_documents",
            "person_operations",
        ):
            connection.execute(f"CREATE TABLE {table}(id INTEGER PRIMARY KEY, payload TEXT)")
            connection.execute(f"INSERT INTO {table} VALUES (1, 'preserve me')")
        connection.execute("CREATE TABLE disposable(payload BLOB)")
        connection.executemany(
            "INSERT INTO disposable VALUES (?)", ((b"x" * 2048,) for _ in range(2400))
        )
        connection.execute("DELETE FROM disposable")
        fact_rows_before = {
            table: connection.execute(f"SELECT id, payload FROM {table} ORDER BY id").fetchall()
            for table in ("persons", "person_collections", "places", "ocr_documents", "person_operations")
        }

    preview = storage_vacuum(root)
    assert preview["recommended"] == "vacuum"
    result = storage_vacuum(root, apply=True, stopped=True)

    assert result["status"] == "ok", result
    assert result["integrity_check_before"] == result["integrity_check_after"] == "ok"
    assert result["actual_reclaimed_bytes"] > 0
    assert result["before"]["schema_sha256"] == result["after"]["schema_sha256"]
    assert result["before"]["table_counts"] == result["after"]["table_counts"]
    assert result["before"]["active_search_profile"] == result["after"]["active_search_profile"]
    with sqlite3.connect(path) as connection:
        fact_rows_after = {
            table: connection.execute(f"SELECT id, payload FROM {table} ORDER BY id").fetchall()
            for table in fact_rows_before
        }
    assert fact_rows_after == fact_rows_before
