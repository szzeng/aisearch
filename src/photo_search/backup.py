"""Stopped-service, consistent data-root backup and restore."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import SCHEMA_TOO_NEW, CatalogError
from photo_search.catalog.migrate import inspect_database
from photo_search.catalog.schema import SUPPORTED_SCHEMA_VERSION
from photo_search.config import catalog_db_path, resolve_data_root
from photo_search.runtime.data_root_lease import (
    LEASE_FILENAME,
    DataRootLeaseBusy,
    acquire_data_root_lease,
)

MANIFEST_NAME = "backup-manifest.json"
FORMAT_VERSION = 1
MEMBER_ROOTS = (
    "catalog",
    "vectors",
    "artifacts",
    "thumbnails",
    "previews",
    "cache",
    "runtime",
    "places",
)
CopyHook = Callable[[Path, Path], None]


def _fail(code: str, message: str, **details: object) -> CatalogError:
    return CatalogError(code, message, details=details or None)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _publish(staging: Path, target: Path) -> None:
    """Rename a completed directory; Windows may briefly retain a closed native handle."""
    last_error: OSError | None = None
    for _ in range(20):
        try:
            os.rename(staging, target)
            return
        except PermissionError as error:
            last_error = error
            time.sleep(0.05)
    assert last_error is not None
    raise last_error


def _copy(source: Path, target: Path, hook: CopyHook | None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if hook:
        hook(source, target)


def _members(source_root: Path, staging: Path, hook: CopyHook | None) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for root_name in MEMBER_ROOTS:
        root = source_root / root_name
        if not root.exists():
            continue
        if root.is_symlink():
            raise _fail(
                "BACKUP_INVALID", "backup member must not be a symbolic link", member=root_name
            )
        for source in sorted(root.rglob("*")):
            if source.is_symlink():
                raise _fail(
                    "BACKUP_INVALID",
                    "backup member must not contain symbolic links",
                    path=str(source),
                )
            if not source.is_file():
                continue
            relative = source.relative_to(source_root)
            # The OS lock is live process state, not recoverable application data; copying it
            # is both meaningless and rejected by Windows while this backup holds the lease.
            relative_path = relative.as_posix()
            if relative_path == f"runtime/{LEASE_FILENAME}" or relative_path in {
                "catalog/catalog.db-wal",
                "catalog/catalog.db-shm",
                "runtime/storage-maintenance-request.json",
            }:
                continue
            if relative_path.startswith("runtime/storage-maintenance-consuming-"):
                continue
            target = staging / relative
            _copy(source, target, hook)
            result.append(
                {
                    "path": relative.as_posix(),
                    "size": target.stat().st_size,
                    "sha256": _digest(target),
                }
            )
    return result


def _manifest(bundle: Path) -> dict[str, Any]:
    try:
        payload = json.loads((bundle / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise _fail(
            "BACKUP_INVALID", "backup manifest is missing or invalid", bundle=str(bundle)
        ) from error
    if payload.get("format_version") != FORMAT_VERSION or not isinstance(
        payload.get("members"), list
    ):
        raise _fail(
            "BACKUP_INVALID", "backup manifest has an unsupported shape", bundle=str(bundle)
        )
    return payload


def _verify(bundle: Path, members: list[object]) -> None:
    seen: set[str] = set()
    for item in members:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise _fail("BACKUP_INVALID", "backup manifest member is invalid")
        relative = Path(item["path"])
        if (
            not item["path"]
            or relative.is_absolute()
            or ".." in relative.parts
            or item["path"] in seen
        ):
            raise _fail("BACKUP_INVALID", "backup manifest contains an unsafe member path")
        seen.add(item["path"])
        candidate = bundle / relative
        if (
            not candidate.is_file()
            or candidate.stat().st_size != item.get("size")
            or _digest(candidate) != item.get("sha256")
        ):
            raise _fail(
                "BACKUP_INVALID", "backup member does not match its manifest", path=item["path"]
            )


def _consistency_snapshot(root: Path) -> dict[str, object]:
    """Record the durable vector generations and unfinished work at the stopped boundary."""
    database = catalog_db_path(root)
    try:
        with sqlite3.connect(database) as connection:
            checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            assert checkpoint is not None
            if int(checkpoint[0]) != 0:
                raise _fail(
                    "BACKUP_SERVICE_ACTIVE",
                    "refusing backup while SQLite still has active readers or writers",
                )
            generations = [
                {"generation_id": str(row[0]), "state": str(row[1]), "active": bool(row[2])}
                for row in connection.execute(
                    "SELECT generation_id, state, is_active FROM index_generations ORDER BY generation_id"
                )
            ]
            pending_outbox = int(
                connection.execute(
                    "SELECT COUNT(*) FROM index_outbox WHERE state <> 'CONFIRMED'"
                ).fetchone()[0]
            )
            has_retention_barriers = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='asset_retention_barriers'"
            ).fetchone() is not None
            pending_projection_sql = (
                "SELECT COUNT(*) FROM search_projection_queue q WHERE NOT EXISTS ("
                "SELECT 1 FROM asset_retention_barriers b WHERE b.asset_id=q.asset_id)"
                if has_retention_barriers
                else "SELECT COUNT(*) FROM search_projection_queue"
            )
            pending_projection = int(connection.execute(pending_projection_sql).fetchone()[0])
    except sqlite3.Error as error:
        raise _fail(
            "BACKUP_INVALID", "backup catalog failed consistency snapshot", cause=type(error).__name__
        ) from error
    return {
        "generations": generations,
        "pending_index_outbox": pending_outbox,
        "pending_search_projection": pending_projection,
    }


def _schema(root: Path, expected: int | None = None) -> int:
    db_path = catalog_db_path(root)
    if not db_path.is_file():
        raise _fail("BACKUP_INVALID", "backup has no catalog database")
    try:
        version = inspect_database(db_path)
    except sqlite3.Error as error:
        raise _fail(
            "BACKUP_INVALID", "backup catalog failed verification", cause=type(error).__name__
        ) from error
    except CatalogError as error:
        if error.code == SCHEMA_TOO_NEW:
            raise _fail(
                "BACKUP_SCHEMA_MISMATCH", "backup schema is newer than this build", **error.details
            ) from error
        raise _fail(
            "BACKUP_INVALID", "backup catalog failed verification", cause=error.code
        ) from error
    required = int(SUPPORTED_SCHEMA_VERSION if expected is None else expected)
    if version != required:
        raise _fail(
            "BACKUP_SCHEMA_MISMATCH",
            "backup schema does not exactly match this build",
            schema_version=version,
            supported_schema_version=int(SUPPORTED_SCHEMA_VERSION),
        )
    return version


def create_backup(
    *,
    data_root: str | Path | None,
    destination: str | Path,
    stopped: bool = False,
    copy_hook: CopyHook | None = None,
) -> dict[str, object]:
    """Copy quiescent application state, write a manifest, then atomically publish it."""
    source, target = resolve_data_root(data_root), Path(destination)
    if not stopped:
        raise _fail(
            "BACKUP_STOPPED_CONFIRMATION_REQUIRED",
            "refusing backup without explicit stopped-service confirmation",
        )
    if target.exists():
        raise _fail(
            "BACKUP_TARGET_EXISTS", "backup destination already exists", destination=str(target)
        )
    try:
        with acquire_data_root_lease(source):
            version = _schema(source)
            snapshot = _consistency_snapshot(source)
            staging = target.with_name(f".{target.name}.backup-staging-{uuid.uuid4().hex}")
            try:
                staging.mkdir(parents=True)
                members = _members(source, staging, copy_hook)
                payload = {
                    "format_version": FORMAT_VERSION,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "schema_version": version,
                    "consistency": {
                        "mode": "stopped",
                        "operator_confirmed": True,
                        "managed_service_lease": "acquired",
                        "snapshot": snapshot,
                    },
                    "members": members,
                    "excluded": {
                        "source_photos": True,
                        "models": "not included; installed model remains external",
                    },
                }
                (staging / MANIFEST_NAME).write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
                )
                _publish(staging, target)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
    except DataRootLeaseBusy as error:
        raise _fail(
            "BACKUP_SERVICE_ACTIVE",
            "refusing backup while a managed service owns this data root",
            data_root=str(source),
        ) from error
    return {"status": "ok", "backup": str(target), "schema_version": version, "members": members}

def _restored_consistency(manifest: dict[str, Any]) -> dict[str, object]:
    """Disclose only the stopped-service facts actually recorded in the manifest."""
    declaration = manifest.get("consistency")
    valid = (
        isinstance(declaration, dict)
        and declaration.get("mode") == "stopped"
        and declaration.get("operator_confirmed") is True
    )
    if not valid:
        return {
            "status": "unknown",
            "declaration": declaration if isinstance(declaration, dict) else None,
            "verification_scope": (
                "no valid stopped-service declaration was recorded; writer quiescence is unknown"
            ),
        }
    if declaration.get("managed_service_lease") == "acquired":
        scope = (
            "operator stopped-service confirmation plus the recorded managed HTTP service lease; "
            "this does not prove every possible writer was stopped"
        )
    else:
        scope = (
            "operator stopped-service confirmation; no managed HTTP service lease was recorded; "
            "this does not prove every possible writer was stopped"
        )
    return {
        "status": "operator_declared",
        "declaration": declaration,
        "verification_scope": scope,
    }

def restore_backup(
    *, bundle: str | Path, data_root: str | Path, copy_hook: CopyHook | None = None
) -> dict[str, object]:
    """Verify first, then restore to an empty root without invoking migrations."""
    source, target = Path(bundle), resolve_data_root(data_root)
    if target.exists() and any(target.iterdir()):
        raise _fail(
            "RESTORE_TARGET_NOT_EMPTY",
            "restore target must be an empty data root",
            data_root=str(target),
        )
    manifest = _manifest(source)
    _verify(source, manifest["members"])
    if manifest.get("schema_version") != int(SUPPORTED_SCHEMA_VERSION):
        raise _fail(
            "BACKUP_SCHEMA_MISMATCH",
            "manifest schema does not exactly match this build",
            schema_version=manifest.get("schema_version"),
            supported_schema_version=int(SUPPORTED_SCHEMA_VERSION),
        )
    restorable_members = [
        item
        for item in manifest["members"]
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and item["path"] != "runtime/storage-maintenance-request.json"
        and not item["path"].startswith("runtime/storage-maintenance-consuming-")
    ]
    staging = target.with_name(f".{target.name}.restore-staging-{uuid.uuid4().hex}")
    try:
        staging.mkdir(parents=True)
        for item in restorable_members:
            relative = Path(str(item["path"]))
            # Legacy bundles may contain recoverable runtime files. Never resurrect a
            # one-shot destructive authorization from any backup generation.
            if relative.as_posix() == "runtime/storage-maintenance-request.json" or relative.as_posix().startswith("runtime/storage-maintenance-consuming-"):
                continue
            _copy(source / relative, staging / relative, copy_hook)
        _verify(staging, restorable_members)
        version = _schema(staging, int(manifest["schema_version"]))
        if target.exists():
            target.rmdir()
        _publish(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "status": "ok",
        "data_root": str(target),
        "schema_version": version,
        "restored_members": len(restorable_members),
        "consistency": _restored_consistency(manifest),
    }
