"""P4-02E: the ``0016`` step of the migration chain on a real populated v15 database.

``0016`` is add-only and index-only: it adds ``idx_jobs_claim_order`` (the covering index of
the bounded claim) plus the two partial indexes that keep the claim's own prefilter statements
from scanning the backlog. It adds no table, column, row, trigger or CHECK, and it backfills
nothing. This file is the evidence that the step is continuous, backed up, lossless for every
pre-existing row, that the script really contains indexes and nothing else, that an interrupted
run rolls back completely, and that an older build refuses the newer catalog without writing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pytest
from p4_time_fixtures import REAL_MIGRATIONS_DIR, migrations_upto

from photo_search.catalog import schema
from photo_search.catalog.errors import MIGRATION_FAILED, SCHEMA_TOO_NEW, CatalogError
from photo_search.catalog.migrate import inspect_database, migrate_database
from photo_search.catalog.sqlite_io import connect_rw, open_readonly
from photo_search.jobs import queue as jobs

SUPPORTED = int(schema.SUPPORTED_SCHEMA_VERSION)
V15 = 15
V16 = 16
CLAIM_ORDER_SQL = "CREATE INDEX idx_jobs_claim_order ON jobs(state, priority, created_at, job_id)"
RETRY_DUE_SQL = "CREATE INDEX idx_jobs_retry_due ON jobs(run_after) WHERE state = 'RETRY_WAIT'"
EXPIRED_LEASE_SQL = (
    "CREATE INDEX idx_jobs_expired_lease ON jobs(lease_expires_at) "
    "WHERE state = 'RUNNING' AND max_attempts <= attempts + 1"
)
EXPECTED_INDEXES = dict(
    [
        ("idx_jobs_claim_order", CLAIM_ORDER_SQL),
        ("idx_jobs_retry_due", RETRY_DUE_SQL),
        ("idx_jobs_expired_lease", EXPIRED_LEASE_SQL),
    ]
)
PRESERVED_TABLES = (
    "assets",
    "file_locations",
    "jobs",
    "scheduler_state",
    "catalog_filter_revision",
    "runtime_capabilities",
)


def data_root(tmp_path: Path) -> Path:
    return tmp_path / "PhotoSearchData"


def metadata_without_version(path: Path) -> dict[str, str]:
    """Every ``metadata`` key except the version counter that migration legitimately bumps."""
    connection = open_readonly(path)
    try:
        return {
            str(row[0]): str(row[1])
            for row in connection.execute("SELECT key, value FROM metadata")
            if str(row[0]) != schema.SCHEMA_VERSION_KEY
        }
    finally:
        connection.close()


def db_path(tmp_path: Path) -> Path:
    return data_root(tmp_path) / "catalog" / "catalog.db"


def snapshot(path: Path, tables: Sequence[str]) -> dict[str, list[tuple]]:
    connection = open_readonly(path)
    try:
        return {
            table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
            for table in tables
        }
    finally:
        connection.close()


def job_columns(path: Path) -> list[tuple[str, str, int]]:
    connection = open_readonly(path)
    try:
        return [
            (str(row[1]), str(row[2]), int(row[3]))
            for row in connection.execute("PRAGMA table_info(jobs)")
        ]
    finally:
        connection.close()


def index_sql(path: Path) -> dict[str, str]:
    connection = open_readonly(path)
    try:
        return {
            str(row[0]): " ".join(str(row[1]).split())
            for row in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'jobs'"
            )
        }
    finally:
        connection.close()


def build_v15_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real v15 catalog with assets, positions, a revision, jobs and scheduler state."""
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, V15))
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V15)
    path = db_path(tmp_path)
    migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO source_roots(display_name, root_path, volume_hint, status) "
            "VALUES ('照片 库', 'C:\\photos 中文', 'C:', 'ONLINE')"
        )
        for index in range(3):
            connection.execute(
                "INSERT INTO assets(sha256, byte_size, created_at, state) "
                "VALUES (?, 4096, ?, 'ACTIVE')",
                (str(index + 1) * 64, 1_000 + index),
            )
            connection.execute(
                "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, "
                "size, mtime_ns, last_seen_scan_id, availability) "
                "VALUES (1, ?, ?, ?, 4096, ?, NULL, 'PRESENT')",
                (
                    f"2024 年/图 {index}.jpg",
                    f"k{index}",
                    index + 1,
                    1_600_000_000_000_000_000 + index,
                ),
            )
        connection.execute(
            "UPDATE scheduler_state SET paused = 1, revision = 7, aging_window_ms = 30000, "
            "updated_at = 99 WHERE state_id = 1"
        )
        connection.execute(
            "UPDATE catalog_filter_revision SET revision = 7, committed_at = 20, "
            "cause = 'PLACE_EFFECTIVE_ASSIGNMENT_CHANGED' WHERE revision_id = 1"
        )
        # Jobs in every state the claim cares about: runnable, live lease, retry backoff and a
        # terminal row. ``0016`` must leave each of them byte-identical.
        connection.executemany(
            "INSERT INTO jobs(subject_type, subject_id, input_revision, task_type, "
            "processor_id, state, priority, attempts, max_attempts, run_after, lease_token, "
            "lease_expires_at, worker_id, progress, created_at, updated_at) "
            "VALUES ('asset', ?, ?, 'geo_extract', 'geo-extract@1', ?, ?, ?, ?, ?, ?, ?, ?, "
            "0.0, ?, ?)",
            [
                ("1", "rev-1", "QUEUED", 0, 0, 3, 500, None, None, None, 500, 500),
                ("2", "rev-2", "QUEUED", 5, 0, 3, 500, None, None, None, 400, 400),
                ("3", "rev-3", "RUNNING", 0, 1, 3, 500, "live-token", 999_999, "w", 300, 300),
                ("4", "rev-4", "RETRY_WAIT", 0, 1, 3, 900, None, None, None, 200, 200),
                ("5", "rev-5", "SUCCEEDED", 0, 1, 3, 100, None, None, None, 100, 100),
            ],
        )
        connection.execute("COMMIT")
    finally:
        connection.close()
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)
    return path


def test_the_migration_chain_is_continuous_and_0016_is_the_claim_index_step() -> None:
    files = schema.migration_files()
    assert [number for number, _ in files] == list(range(1, SUPPORTED + 1))
    assert SUPPORTED >= V16
    names = [path.name for _, path in files]
    assert names[:15] == [
        "0001_initial.sql",
        "0002_scheduler_runtime.sql",
        "0003_artifact_outbox_index.sql",
        "0004_place_core.sql",
        "0005_place_dataset_resolution.sql",
        "0006_search_fts.sql",
        "0007_search_profiles.sql",
        "0008_search_feedback.sql",
        "0009_ocr_documents.sql",
        "0010_face_core.sql",
        "0011_person_knowledge.sql",
        "0012_face_correction_operations.sql",
        "0013_person_split_operations.sql",
        "0014_face_redetection_mapping.sql",
        "0015_taken_time.sql",
    ]
    assert names[15] == "0016_jobs_claim_order.sql"
    assert names[15] == (schema.MIGRATIONS_DIR / "0016_jobs_claim_order.sql").name


def test_0016_contains_indexes_and_nothing_else() -> None:
    """Add-only, index-only: no table, column, row, trigger or vocabulary change."""
    script = (schema.MIGRATIONS_DIR / "0016_jobs_claim_order.sql").read_text(encoding="utf-8")
    without_comments = " ".join(
        line for line in script.splitlines() if not line.strip().startswith("--")
    )
    body = [" ".join(statement.split()) for statement in without_comments.split(";")]
    body = [statement for statement in body if statement]
    assert body, "the migration must contain statements"
    # Exactly the three measured indexes, in order, and nothing else.
    assert body == list(EXPECTED_INDEXES.values()), body
    upper = script.upper()
    for forbidden in (
        "CREATE TABLE",
        "ALTER TABLE",
        "DROP ",
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "CREATE TRIGGER",
        "PRAGMA",
    ):
        assert forbidden not in upper, forbidden


def test_v15_to_v16_is_continuous_backed_up_and_lossless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v15_catalog(tmp_path, monkeypatch)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, V16))
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V16)
    assert inspect_database(path) == V15
    before = snapshot(path, PRESERVED_TABLES)
    metadata_before = metadata_without_version(path)
    columns_before = job_columns(path)
    indexes_before = set(index_sql(path))
    result = migrate_database(path, data_root=data_root(tmp_path))
    assert result.previous_version == V15 and result.schema_version == V16
    assert result.applied == (16,)
    assert result.backup_path is not None
    backup = open_readonly(result.backup_path)
    try:
        assert schema.read_schema_version(backup) == V15
        assert set(index_sql(result.backup_path)).isdisjoint(EXPECTED_INDEXES)
    finally:
        backup.close()
    live = open_readonly(path)
    try:
        assert schema.read_schema_version(live) == V16
        schema.verify_schema(live, V16)
        assert list(live.execute("PRAGMA foreign_key_check")) == []
    finally:
        live.close()
    added = index_sql(path)
    assert indexes_before <= set(added)
    for name, expected in EXPECTED_INDEXES.items():
        assert added[name] == expected, added[name]
    assert job_columns(path) == columns_before
    assert snapshot(path, PRESERVED_TABLES) == before
    assert metadata_without_version(path) == metadata_before
    # The chain continues to whatever this build supports, then settles.
    again = migrate_database(path, data_root=data_root(tmp_path))
    assert again.applied == () and again.previous_version == V16


def test_the_new_indexes_are_the_ones_the_claim_uses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v15_catalog(tmp_path, monkeypatch)
    migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    try:
        filter_sql, bound_filter = jobs._processor_filter(["geo-extract@1"])
        for state, condition in jobs._CLAIM_SIDES:
            statements = (
                (
                    jobs._claim_priority_sql(condition, filter_sql, after_cursor=False),
                    (state, 1_000, *bound_filter),
                ),
                (
                    jobs._claim_priority_sql(condition, filter_sql, after_cursor=True),
                    (state, 0, 1_000, *bound_filter),
                ),
                (
                    jobs._claim_class_sql(condition, filter_sql),
                    (1_000, 60_000, state, 0, 1_000, *bound_filter, 256),
                ),
            )
            for sql, bound in statements:
                plan = [
                    str(row[3])
                    for row in connection.execute(f"EXPLAIN QUERY PLAN {sql}", bound).fetchall()
                ]
                joined = " | ".join(plan)
                assert jobs._CLAIM_INDEX in joined, (state, joined)
                assert "TEMP B-TREE" not in joined, (state, joined)
        # The two *partial* indexes are asserted by shape in the migration tests above. Their
        # plan behaviour needs a backlog and statistics this v15 fixture does not have, so that
        # guard lives in ``test_p4_02e_backlog_claim.py``
        # (``test_the_prefilters_of_the_claim_use_their_partial_indexes``), where it also proves
        # the fixture can tell "index present" from "index dropped" (P4-02E review finding N1).
    finally:
        connection.close()


def test_an_interrupted_0016_rolls_back_completely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v15_catalog(tmp_path, monkeypatch)
    before = snapshot(path, PRESERVED_TABLES)
    directory = migrations_upto(tmp_path, V16)
    broken = directory / "0016_jobs_claim_order.sql"
    broken.write_text(
        broken.read_text(encoding="utf-8") + "\nSELECT * FROM table_that_does_not_exist;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V16)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", directory)
    with pytest.raises(CatalogError) as error:
        migrate_database(path, data_root=data_root(tmp_path))
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)
    assert error.value.code == MIGRATION_FAILED
    assert inspect_database(path) == V15
    assert snapshot(path, PRESERVED_TABLES) == before
    assert set(index_sql(path)).isdisjoint(EXPECTED_INDEXES)


def test_an_older_build_refuses_the_newer_catalog_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v15_catalog(tmp_path, monkeypatch)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, V16))
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V16)
    migrate_database(path, data_root=data_root(tmp_path))
    assert inspect_database(path) == V16
    before_bytes = path.read_bytes()
    before = snapshot(path, PRESERVED_TABLES)
    # The build that supported 15 must refuse a 16 catalog without writing.
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V15)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, V15))
    with pytest.raises(CatalogError) as too_new:
        inspect_database(path)
    assert too_new.value.code == SCHEMA_TOO_NEW
    assert too_new.value.details == {"schema_version": V16, "supported_schema_version": V15}
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)
    assert path.read_bytes() == before_bytes
    assert snapshot(path, PRESERVED_TABLES) == before
