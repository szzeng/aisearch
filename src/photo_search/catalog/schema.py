"""Schema version discovery and the frozen core-schema verification contract."""

from __future__ import annotations

import sqlite3
from functools import lru_cache
from pathlib import Path

from photo_search.catalog.errors import SCHEMA_INCONSISTENT, CatalogError

#: Highest schema version this build can write. ``0001`` is the frozen P1-01 identity
#: schema; ``0002`` adds scheduler/runtime facts, ``0003`` adds the recoverable
#: artifact/outbox/index confirmation protocol, ``0004`` adds the ADR-0005 place core
#: (directory, GPS facts, manual control, operations and the two revisions) and ``0005``
#: adds the offline name-dataset registry and the immutable suggestion-only resolution
#: products. ``0006`` adds the P2-04 derived search layer (``search_documents`` plus its
#: external content FTS5 index and the durable reprojection queue), again without touching
#: any frozen core table. ``0007`` adds the P2-06A immutable ``search_profiles`` and the
#: single ``active_search_profile`` pointer. ``0008`` adds the P2-06B append-only
#: ``search_feedback`` audit table (explicit relevance verdicts, idempotent by
#: ``request_id``); it records what a user said and deliberately re-ranks, hides and
#: re-classifies nothing. ``0009`` adds the P3-01 OCR layer: the current OCR document (the
#: normalised search copy plus its region statistics) of every asset, its per-region
#: metadata, and one extra ``search_projection_queue`` cause so an OCR text change joins the
#: existing reprojection path. The original text and the exact coordinates stay in the
#: immutable processor artifact, never in this table.
#: ``0010`` adds the P3-02 face layer: the current detection facts of every asset, the
#: canonical face vector store, the unnamed person groups with their automatic links and
#: suggestions, and an independent face candidate index with its own generations and its
#: own outbox. It shares no identity with the image index (``index_entries`` stays
#: asset-level) and adds no cause to the P2-04 reprojection queue.
#: ``0011`` adds the P3-03 person-knowledge layer: the immutable operation log with its
#: per-person before/after bundles, the search aliases of a named person, and the two human
#: control tables (``face_assignment_controls``/``face_exclusions``) whose *read* order
#: P3-02 already implements. Same-name uniqueness stays the 0010 partial unique index; the
#: only shared table this script replaces is ``search_projection_queue``, whose frozen
#: ``CHECK`` list would otherwise refuse the sixth ``PERSON_CHANGED`` cause.
#: ``0012`` adds the P3-04B single-face correction layer. It adds no fact table: the human
#: locks and denials already exist, and what ADR-0007 §10 needs is a wider *closed* type
#: vocabulary on the one person operation log plus the ``FACE_CONTROL`` item shape. Those
#: two tables are therefore rebuilt row-for-row (including their undo and alias references)
#: with four face operation types and the mutually exclusive ``entity_id``/``entity_key``.
#: ``0013`` adds the P3-05B person split. Like ``0012`` it adds no fact table and no queue
#: cause: the split reuses the shared operation log, the person/face/control/denial tables
#: and the ``PERSON_CHANGED`` cause, and only needs one more operation type (``SPLIT``) plus
#: the third item shape (``SPLIT_GROUP``). The same two tables are rebuilt row-for-row once
#: more, with every P3-03/P3-04 row and alias reference preserved verbatim.
#: ``0014`` adds the P3-08B face-redetection mapping layer of ADR-0010: the review group of
#: one surviving human-owned old face (``face_redetection_groups``) and its candidate pairs
#: with the stable ``mapping_id`` (``face_redetection_mappings``), plus a third rebuild of
#: the shared operation log for the two ``FACE_MAPPING_*`` types and the
#: ``FACE_MAPPING_GROUP`` item shape. No queue cause is added: a confirmed transfer reuses
#: ``PERSON_CHANGED`` like every other human fact, and the image-vector index is untouched.
#: ``0015`` adds the P4-00B taken-time layer of ADR-0011: the derived ``asset_metadata``
#: fact table (the six frozen columns, one row per already-processed asset) and the
#: library-level ``taken_time_rules_version`` key. It is additive only - no fact table, no
#: search document, no queue cause and no vector namespace changes - and it backfills
#: nothing, because the extraction is its own idempotent, resumable task.
#: ``0016`` adds the P4-02E index-only additive of the bounded semantic-backlog claim:
#: ``idx_jobs_claim_order`` (the ``(side, priority)`` class seek of
#: ``jobs.claimable_candidates``) plus the two *partial* indexes
#: ``idx_jobs_retry_due``/``idx_jobs_expired_lease`` that let the claim's own prefilter
#: statements (``requeue_due_retries``/``fail_exhausted_leases``) stop scanning the whole
#: backlog. No table, column, row, CHECK or state vocabulary changes.
# 0018 adds independent CN facets only; the v1 manual/place package projection stays intact.
# 0019 stores source-detail batches; 0020 adds their derived auto policy without replacing
# either the immutable inference product or the historical manual records.
# 0021 adds a sourced OSM identity bridge to the existing assignment Place FK.
# 0022 stores person-album visibility independently from person identity and face ownership.
# 0023 stores per-detection inbox visibility independently from face identity and ownership.
# 0024 stores durable person collections and their stable person-ID membership anchors.
# 0025 upgrades collection identity allocation without changing the v24 contract in place.
# 0026 adds a persistent collection-instance token for requests surviving v24 ID reuse.
# 0027 preserves compact replay/history receipts while pruning expired person snapshots.
# 0028 adds a non-undoable create-person-from-face operation type.
# 0029 adds selected-face atomic regroup; 0030 classifies person ignores; 0031
# classifies per-face inbox ignores while preserving existing hidden rows as TEMPORARY.
# 0032 adds soft-anchored face-level person collection membership.
# 0033 adds ordered photo/source reads and pending-only generation readiness checks.
# 0034 adds asset favorites; 0035 records one-shot recovery of legacy CR2 format failures;
# 0036 adds stable user-maintained ordinary photo collections; 0037 adds independent
# dynamic smart collection rules, human exclusions and rebuildable bounded rank caches;
# 0038 adds trustworthy per-source missing evidence and persistent bulk-disappearance holds;
# 0039 adds asset tombstone barriers that survive the asset row's later removal.
# 0040 preserves exact media-cache identities for safe post-cleanup GC; 0041 stores
# retention authorization policy and restart-visible cleanup runs/items; 0042 adds the
# bounded keyset fence, cross-batch counters, and retry budget for cleanup runs.
SUPPORTED_SCHEMA_VERSION = 42
SCHEMA_VERSION_KEY = "schema_version"

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_OBJECT_TYPES = ("table", "index", "view", "trigger")


def migration_files() -> list[tuple[int, Path]]:
    """Return ``(version, path)`` for every migration script, validating continuity.

    The set must cover every version from ``0001`` up to ``SUPPORTED_SCHEMA_VERSION``. This
    is stricter than "no gaps between the files that happen to be present": a build that
    ships *no* migration scripts at all (seen once in a frozen bundle whose ``.sql`` files
    were not collected) must fail loudly here instead of silently creating an empty catalog
    that only breaks on the first query (ADR-0002 §7.1 item 7).
    """
    files = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    versions = [int(path.name.split("_", 1)[0]) for path in files]
    expected = list(range(1, int(SUPPORTED_SCHEMA_VERSION) + 1))
    if versions != expected:
        raise CatalogError(
            SCHEMA_INCONSISTENT,
            "the migration scripts must cover every version from 0001 to the supported version",
            details={"versions": versions, "expected_versions": expected},
        )
    return [(version, path) for version, path in zip(versions, files, strict=True)]


def normalize_sql(sql: str) -> str:
    return " ".join(sql.split())


def schema_objects(connection: sqlite3.Connection) -> dict[tuple[str, str], str]:
    """Map ``(type, name) -> normalised SQL`` for every user schema object."""
    objects: dict[tuple[str, str], str] = {}
    for row in connection.execute("SELECT type, name, sql FROM sqlite_master"):
        object_type, name, sql = row[0], row[1], row[2]
        if name.startswith("sqlite_") or object_type not in _OBJECT_TYPES:
            continue
        objects[(object_type, name)] = normalize_sql(sql or "")
    return objects


@lru_cache(maxsize=16)
def _compiled_expected_objects(
    version: int, scripts: tuple[str, ...],
) -> tuple[tuple[tuple[str, str], str], ...]:
    """Cache only immutable expected DDL, keyed by version and exact script contents."""
    connection = sqlite3.connect(":memory:")
    try:
        for script in scripts:
            connection.executescript(script)
        return tuple(schema_objects(connection).items())
    finally:
        connection.close()


def expected_objects(version: int) -> dict[tuple[str, str], str]:
    """Read and validate the migration inputs, reusing their compiled expected schema.

    Always check the complete migration list and read the relevant contents: a removed
    script or an edit preserving its size/mtime must not reuse a stale expectation.
    Actual database objects are never cached, and callers receive their own dictionary.
    """
    scripts = tuple(
        path.read_text(encoding="utf-8")
        for file_version, path in migration_files()
        if file_version <= version
    )
    return dict(_compiled_expected_objects(version, scripts))


def verify_schema(connection: sqlite3.Connection, version: int) -> None:
    """Raise ``SCHEMA_INCONSISTENT`` unless the database matches ``version`` exactly."""
    expected = expected_objects(version)
    actual = schema_objects(connection)
    missing = sorted(f"{kind}:{name}" for kind, name in set(expected) - set(actual))
    unexpected = sorted(f"{kind}:{name}" for kind, name in set(actual) - set(expected))
    mismatched = sorted(
        f"{kind}:{name}"
        for (kind, name) in set(expected) & set(actual)
        if expected[(kind, name)] != actual[(kind, name)]
    )
    if missing or unexpected or mismatched:
        raise CatalogError(
            SCHEMA_INCONSISTENT,
            f"catalog schema does not match schema_version {version}",
            details={
                "schema_version": version,
                "missing_objects": missing,
                "unexpected_objects": unexpected,
                "mismatched_objects": mismatched,
            },
        )


def business_tables(connection: sqlite3.Connection) -> list[str]:
    """User tables in the database, excluding SQLite internal tables."""
    return sorted(
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        if not row[0].startswith("sqlite_")
    )


def read_schema_version(connection: sqlite3.Connection) -> int | None:
    """Return ``metadata.schema_version`` as an integer, or ``None`` when unusable."""
    tables = set(business_tables(connection))
    if "metadata" not in tables:
        return None
    row = connection.execute(
        "SELECT value FROM metadata WHERE key = ?", (SCHEMA_VERSION_KEY,)
    ).fetchone()
    if row is None or row[0] is None:
        return None
    try:
        return int(str(row[0]).strip())
    except ValueError:
        return None
