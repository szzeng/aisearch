"""Copy the unpublished scale-only v32 synthetic catalog into the current schema.

This explicit diagnostic tool is not part of application startup. Both paths must be
inside this checkout's .tmp directory, and the destination data root must not exist.
Only the catalog is copied: photos, artifacts, models and vector indexes are not copied.
The input is opened read-only and must match the complete known development schema;
ordinary/upstream v32, incomplete schemas and future versions are refused.

Usage: python tools/diagnostics/clone_legacy_scale_catalog.py --source-db PATH \
    --destination-data-root PATH
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from photo_search.catalog import schema  # noqa: E402
from photo_search.catalog.errors import SCHEMA_INCONSISTENT, CatalogError  # noqa: E402
from photo_search.catalog.migrate import MIN_MIGRATION_HEADROOM_BYTES  # noqa: E402
from photo_search.catalog.sqlite_io import open_readonly  # noqa: E402

_SYNTHETIC_ROOT = REPO_ROOT / ".tmp"
_BASE_TARGET_VERSION = 33
_TARGET_VERSION = schema.SUPPORTED_SCHEMA_VERSION


def _validate_path(path: Path) -> Path:
    resolved = path.resolve()
    boundary = _SYNTHETIC_ROOT.resolve()
    if resolved == boundary or not resolved.is_relative_to(boundary):
        raise ValueError("both paths must be inside the project's synthetic .tmp directory")
    return resolved


def _legacy_objects() -> dict[tuple[str, str], str]:
    # Preserve upstream 0032 as the official history. The unshipped local variant was
    # exactly the official v31 schema followed by the four now-renumbered 0033 indexes.
    scripts = tuple(
        path.read_text(encoding="utf-8")
        for number, path in schema.migration_files()
        if number <= 31 or number == 33
    )
    return dict(schema._compiled_expected_objects(32, scripts))


def _verify_legacy(connection: sqlite3.Connection) -> None:
    if schema.read_schema_version(connection) != 32:
        raise CatalogError(SCHEMA_INCONSISTENT, "source is not the unpublished scale-only v32")
    if schema.schema_objects(connection) != _legacy_objects():
        raise CatalogError(
            SCHEMA_INCONSISTENT,
            "source does not exactly match the unpublished scale-only v32 schema",
        )


def _upgrade_copy(connection: sqlite3.Connection) -> None:
    scripts = [
        (schema.MIGRATIONS_DIR / "0032_person_collection_face_members.sql").read_text(
            encoding="utf-8"
        ),
        "UPDATE metadata SET value='33' WHERE key='schema_version';",
    ]
    for version, migration_path in schema.migration_files():
        if version <= _BASE_TARGET_VERSION:
            continue
        scripts.extend(
            [
                migration_path.read_text(encoding="utf-8"),
                f"UPDATE metadata SET value='{version}' WHERE key='schema_version';",
            ]
        )
    try:
        # executescript starts one transaction; validation and version publication are
        # inside it. A failed DDL/validation never publishes a partially upgraded copy.
        connection.executescript(
            "BEGIN IMMEDIATE;\n" + "\n".join(scripts) + "\n"
        )
        schema.verify_schema(connection, _TARGET_VERSION)
        if list(connection.execute("PRAGMA foreign_key_check")):
            raise CatalogError(SCHEMA_INCONSISTENT, "copied catalog has foreign-key violations")
        check = [row[0] for row in connection.execute("PRAGMA quick_check")]
        if check != ["ok"]:
            raise CatalogError(SCHEMA_INCONSISTENT, "copied catalog failed SQLite quick_check")
        connection.execute(
            "UPDATE metadata SET value=? WHERE key=?",
            (str(_TARGET_VERSION), schema.SCHEMA_VERSION_KEY),
        )
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def clone_legacy_scale_catalog(source_db: Path, destination_data_root: Path) -> dict[str, object]:
    source_path = _validate_path(Path(source_db))
    destination_root = _validate_path(Path(destination_data_root))
    if not source_path.is_file():
        raise ValueError("source catalog must be an existing file")
    if destination_root.exists():
        raise FileExistsError("destination data root must not already exist")
    if schema.SUPPORTED_SCHEMA_VERSION != _TARGET_VERSION:
        raise ValueError("the diagnostic target schema differs from this build")

    # One read transaction pins preflight and backup to the same committed source
    # snapshot, including any committed WAL frames. The source is never opened RW.
    source = open_readonly(source_path)
    created_root = False
    catalog_dir = destination_root / "catalog"
    temporary = catalog_dir / "catalog.db.partial"
    destination = catalog_dir / "catalog.db"
    try:
        source.execute("BEGIN DEFERRED")
        _verify_legacy(source)
        logical_bytes = sum(
            candidate.stat().st_size
            for candidate in (source_path, Path(f"{source_path}-wal"))
            if candidate.exists()
        )
        existing_parent = destination_root.parent
        while not existing_parent.exists():
            existing_parent = existing_parent.parent
        if shutil.disk_usage(existing_parent).free < logical_bytes + MIN_MIGRATION_HEADROOM_BYTES:
            raise OSError("not enough disk space for a complete isolated catalog copy")
        destination_root.mkdir(parents=True, exist_ok=False)
        created_root = True
        catalog_dir.mkdir()
        # Reserve the exact file without replacing anything created by another actor.
        with temporary.open("xb"):
            pass
        target = sqlite3.connect(temporary, isolation_level=None)
        try:
            source.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
            target.execute("PRAGMA foreign_keys=ON")
            _verify_legacy(target)
            _upgrade_copy(target)
        finally:
            target.close()
        # Publish only after the connection closed and all transaction checks passed.
        # Destination root was exclusively created above; rename refuses existing targets
        # on Windows, where these synthetic compatibility fixtures are used.
        temporary.rename(destination)
        return {
            "source_db": str(source_path),
            "source_schema": "unpublished-scale-only-v32",
            "destination_db": str(destination),
            "schema_version": _TARGET_VERSION,
            "catalog_only": True,
        }
    except BaseException:
        if created_root:
            # Remove only our known unpublished files; never recursively remove a tree.
            for suffix in ("", "-journal", "-wal", "-shm"):
                Path(f"{temporary}{suffix}").unlink(missing_ok=True)
            for directory in (catalog_dir, destination_root):
                try:
                    directory.rmdir()
                except OSError:
                    pass
        raise
    finally:
        source.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", required=True, type=Path)
    parser.add_argument("--destination-data-root", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(clone_legacy_scale_catalog(args.source_db, args.destination_data_root), indent=2))


if __name__ == "__main__":
    main()
