"""Schema version discovery and the frozen core-schema verification contract."""

from __future__ import annotations

import sqlite3
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
SUPPORTED_SCHEMA_VERSION = 9
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


def expected_objects(version: int) -> dict[tuple[str, str], str]:
    """Build the schema this build expects for ``version`` from the migration scripts."""
    connection = sqlite3.connect(":memory:")
    try:
        for file_version, path in migration_files():
            if file_version > version:
                break
            connection.executescript(path.read_text(encoding="utf-8"))
        return schema_objects(connection)
    finally:
        connection.close()


def verify_schema(connection: sqlite3.Connection, version: int) -> None:
    """Raise ``SCHEMA_INCONSISTENT`` unless the database matches ``version`` exactly."""
    expected = expected_objects(version)
    actual = schema_objects(connection)
    missing = sorted(f"{kind}:{name}" for kind, name in set(expected) - set(actual))
    unexpected = sorted(f"{kind}:{name}" for kind, name in set(actual) - set(expected))
    mismatched = sorted(
        f"{kind}:{name}" for (kind, name) in set(expected) & set(actual) if expected[(kind, name)] != actual[(kind, name)]
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
    row = connection.execute("SELECT value FROM metadata WHERE key = ?", (SCHEMA_VERSION_KEY,)).fetchone()
    if row is None or row[0] is None:
        return None
    try:
        return int(str(row[0]).strip())
    except ValueError:
        return None
