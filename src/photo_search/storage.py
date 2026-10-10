"""Read-only storage accounting and stopped-service, file-level garbage collection.

The module deliberately knows only derived files. It never deletes source photos, catalog
rows, vector rows, or files referenced by any artifact metadata state.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from photo_search.catalog.sqlite_io import open_readonly
from photo_search.config import catalog_db_path, resolve_data_root
from photo_search.media.contract import PREVIEW_PROCESSOR_ID, THUMBNAIL_PROCESSOR_ID

ARTIFACT_ORPHAN_AGE_MS = 7 * 24 * 60 * 60 * 1000
TEMPORARY_AGE_MS = 24 * 60 * 60 * 1000
MAX_SCAN_FILES = 500_000
MAX_SCAN_DIRECTORIES = 20_000
DELETE_BATCH_SIZE = 1_000
_MANAGED_ARTIFACT = re.compile(r"^[0-9a-f]{64}-([0-9a-f]{16})\.(?:json|bin)$")
_MANAGED_TEMP = re.compile(r"^([0-9a-f]{64}-[0-9a-f]{16}\.(?:json|bin))-\d+-[0-9a-f]{12}\.tmp$")
_MEDIA_VERSION = re.compile(r"^(?:catalog-thumbnail|catalog-preview)@\d+$")
_CROP_VERSION = re.compile(r"^face-crop@\d+$")
_MEDIA_FILE = re.compile(r"^[0-9a-f]{64}\.jpg$")
_MEDIA_TEMP = re.compile(r"^[0-9a-f]{64}-\d+-[0-9a-f]{12}\.tmp$")

STORAGE_DIRS = ("vectors", "artifacts", "thumbnails", "previews", "cache", "places", "backups")


@dataclass(frozen=True)
class FileIdentity:
    size: int
    mtime_ns: int
    device: int
    inode: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> FileIdentity:
        return cls(value.st_size, value.st_mtime_ns, value.st_dev, value.st_ino)


@dataclass(frozen=True)
class Candidate:
    path: Path
    scope: str
    identity: FileIdentity
    relative_uri: str | None = None


def _is_managed_preview(path: Path, root: Path) -> bool:
    rel = path.relative_to(root).parts
    return (
        len(rel) == 5 and rel[0] == "previews" and _MEDIA_VERSION.fullmatch(rel[1])
        and rel[1].startswith("catalog-preview@") and _MEDIA_FILE.fullmatch(rel[4])
        and rel[2] == rel[4][:2] and rel[3] == rel[4][2:4]
    )


def _file_bytes(path: Path) -> int | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    is_reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return info.st_size if stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode) and not is_reparse else None


def _last_scan_path(root: Path) -> Path:
    return root / "runtime" / "storage-inspection.json"


def _try_scan_lock(root: Path):
    """Return a process-wide scan lock; the marker file itself may persist unlocked."""
    runtime = root / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    runtime_info = runtime.lstat()
    if not stat.S_ISDIR(runtime_info.st_mode) or stat.S_ISLNK(runtime_info.st_mode) or getattr(runtime_info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        return None
    lock_path = runtime / "storage-inspection.lease"
    if lock_path.exists():
        lock_info = lock_path.lstat()
        if not stat.S_ISREG(lock_info.st_mode) or stat.S_ISLNK(lock_info.st_mode) or getattr(lock_info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            return None
    stream = lock_path.open("a+b")
    try:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover - Windows is the supported product target.
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except OSError:
        stream.close()
        return None


def _release_scan_lock(stream: Any) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()


def quick_storage_summary(data_root: str | Path | None) -> dict[str, Any]:
    """Return a cheap storage overview without walking data directories."""
    root = resolve_data_root(data_root).resolve()
    db = catalog_db_path(root)
    db_info = _safe_file(db, root)
    files: dict[str, int | None] = {}
    for suffix in ("", "-wal", "-shm"):
        label = "catalog.db" + suffix
        files[label] = _file_bytes(Path(f"{db}{suffix}"))
    catalog: dict[str, Any] = {"exists": db_info is not None, "physical_bytes": sum(v or 0 for v in files.values()), "files": files}
    if db_info is not None:
        connection = None
        try:
            connection = open_readonly(db)
            page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
            page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
            freelist = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
            tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            counts: dict[str, int] = {}
            for table in ("assets", "file_locations", "artifacts", "index_outbox", "index_generations", "persons", "person_collections", "face_embeddings", "face_candidates"):
                if table in tables:
                    try:
                        counts[table] = int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                    except Exception:  # noqa: BLE001 - counters are optional diagnostics.
                        continue
            blob_sizes: dict[str, dict[str, int]] = {}
            for table, column in (("face_embeddings", "vector_blob"), ("face_candidates", "vector_blob")):
                if table in tables and column in {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}:
                    row = connection.execute(f'SELECT COUNT(*), COALESCE(SUM(length("{column}")),0) FROM "{table}"').fetchone()
                    blob_sizes[table] = {"records": int(row[0]), "logical_blob_bytes": int(row[1])}
            catalog.update({"page_count": page_count, "page_size": page_size, "freelist_count": freelist, "logical_page_bytes": page_count * page_size, "freelist_reusable_bytes": freelist * page_size, "counts": counts, "logical_blob_breakdown": blob_sizes, "partial": False})
        except Exception as error:  # noqa: BLE001 - report inaccessible details as partial.
            catalog.update({"partial": True, "reason": type(error).__name__})
        finally:
            if connection is not None:
                connection.close()
    previous = None
    last_path = _last_scan_path(root)
    if _safe_directory(last_path.parent, root) and _safe_file(last_path, root) is not None:
        try:
            previous = json.loads(last_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"status": "ok", "catalog": catalog, "last_deep_scan": previous, "bytes_basis": "regular-file st_size; sparse/compressed filesystem allocation may differ", "note": "freelist is reusable catalog space, not bytes immediately returned to the operating system"}


def _safe_file(path: Path, root: Path) -> os.stat_result | None:
    """lstat a regular in-root file and reject symlink/reparse points on every segment."""
    try:
        relative = path.relative_to(root)
        current = root
        for part in relative.parts:
            current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                return None
        return info if stat.S_ISREG(info.st_mode) else None
    except (OSError, ValueError):
        return None


def _safe_directory(path: Path, root: Path) -> bool:
    """Confirm a directory path contains no symbolic link or Windows reparse point."""
    try:
        current = root
        info = current.lstat()
        for part in path.relative_to(root).parts:
            current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                return False
        return stat.S_ISDIR(info.st_mode)
    except (OSError, ValueError):
        return False


def _artifact_candidate(path: Path, root: Path, now_ms: int, min_age_ms: int) -> Candidate | None:
    rel = path.relative_to(root).as_posix()
    parts = path.relative_to(root).parts
    if len(parts) != 5 or parts[0:2] != ("artifacts", "pending") or not re.fullmatch(r"[0-9a-f]{16}", parts[2]):
        return None
    safe = _safe_file(path, root)
    if safe is None or now_ms - safe.st_mtime_ns // 1_000_000 < min_age_ms:
        return None
    if path.name.endswith(".tmp"):
        match = _MANAGED_TEMP.fullmatch(path.name)
        artifact_match = _MANAGED_ARTIFACT.fullmatch(match.group(1)) if match else None
        if (
            match is None
            or artifact_match is None
            or artifact_match.group(1) != parts[2]
            or path.parent.name != match.group(1)[:2]
        ):
            return None
        target = path.with_name(match.group(1))
        target_rel = target.relative_to(root).as_posix()
        return Candidate(path, "artifacts", FileIdentity.from_stat(safe), target_rel)
    match = _MANAGED_ARTIFACT.fullmatch(path.name)
    if match is None or match.group(1) != parts[2]:
        return None
    if path.parent.name != path.name[:2]:
        return None
    return Candidate(path, "artifacts", FileIdentity.from_stat(safe), rel)


def _is_media_candidate(path: Path, root: Path, now_ms: int) -> Candidate | None:
    safe = _safe_file(path, root)
    if safe is None:
        return None
    rel = path.relative_to(root).parts
    age = now_ms - safe.st_mtime_ns // 1_000_000
    if path.name.endswith(".tmp"):
        if age < TEMPORARY_AGE_MS:
            return None
        media_root_matches = len(rel) == 5 and ((rel[0] == "thumbnails" and rel[1].startswith("catalog-thumbnail@")) or (rel[0] == "previews" and rel[1].startswith("catalog-preview@")))
        if media_root_matches and _MEDIA_VERSION.fullmatch(rel[1]) and _MEDIA_TEMP.fullmatch(rel[4]) and rel[2] == rel[4][:2] and rel[3] == rel[4][2:4]:
            return Candidate(path, "media-cache", FileIdentity.from_stat(safe))
        return None
    if (len(rel) == 6 and rel[0:2] == ("cache", "face-crops") and _CROP_VERSION.fullmatch(rel[2])
        and rel[3] == rel[5][:2] and rel[4] == rel[5][2:4] and _MEDIA_FILE.fullmatch(rel[5])):
        return Candidate(path, "face-crops", FileIdentity.from_stat(safe))
    if (len(rel) == 5 and rel[0] == "thumbnails" and rel[1].startswith("catalog-thumbnail@") and _MEDIA_VERSION.fullmatch(rel[1])
        and rel[1] != THUMBNAIL_PROCESSOR_ID and _MEDIA_FILE.fullmatch(rel[4]) and rel[2] == rel[4][:2] and rel[3] == rel[4][2:4]):
        return Candidate(path, "media-cache", FileIdentity.from_stat(safe))
    if (len(rel) == 5 and rel[0] == "previews" and rel[1].startswith("catalog-preview@") and _MEDIA_VERSION.fullmatch(rel[1])
        and rel[1] != PREVIEW_PROCESSOR_ID and _MEDIA_FILE.fullmatch(rel[4]) and rel[2] == rel[4][:2] and rel[3] == rel[4][2:4]):
        return Candidate(path, "media-cache", FileIdentity.from_stat(safe))
    return None


def _walk(root: Path, *, max_files: int = MAX_SCAN_FILES, progress: Callable[[int], None] | None = None):
    """Iterative scandir walk; never follows links and stops at a fixed file budget."""
    stack = [root]
    visited = 0
    directories = 0
    while stack and visited < max_files:
        directory = stack.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if visited >= max_files:
                        break
                    visited += 1
                    try:
                        if entry.is_symlink() or getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                            yield Path(entry.path), "skip_symlink"
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            directories += 1
                            if directories > MAX_SCAN_DIRECTORIES:
                                yield Path(entry.path), "scan_limit"
                            else:
                                stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            yield Path(entry.path), None
                    except OSError:
                        yield Path(entry.path), "read_error"
                    if progress is not None and visited % 1000 == 0:
                        progress(visited)
        except OSError:
            yield directory, "read_error"
    if stack or visited >= max_files:
        yield root, "scan_limit"


def _connection(root: Path):
    path = catalog_db_path(root)
    if _safe_file(path, root) is None:
        return None
    try:
        connection = open_readonly(path)
    except Exception:  # noqa: BLE001 - inability to prove references protects every artifact.
        return None
    try:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='artifacts'").fetchone()
    except sqlite3.Error:
        connection.close()
        return None
    if exists is None:
        connection.close()
        return None
    return connection


def _is_referenced(connection: Any, uri: str | None) -> bool:
    if connection is None or uri is None:
        return True
    row = connection.execute(
        "SELECT 1 FROM artifacts WHERE lower(replace(storage_uri, char(92), '/')) = lower(?) LIMIT 1",
        (uri,),
    ).fetchone()
    return row is not None


def inspect_storage(data_root: str | Path | None, *, max_files: int = MAX_SCAN_FILES, progress: Callable[[int], None] | None = None, now_ms: int | None = None) -> tuple[dict[str, Any], list[Candidate]]:
    """Boundedly inventory derived directories and return internal deletion candidates."""
    root = resolve_data_root(data_root).resolve()
    now = time.time_ns() // 1_000_000 if now_ms is None else int(now_ms)
    connection = _connection(root)
    categories: dict[str, dict[str, Any]] = {name: {"files": 0, "bytes": 0, "safe_cleanup_files": 0, "safe_cleanup_bytes": 0, "needs_verification_files": 0, "needs_verification_bytes": 0, "protected_files": 0, "protected_bytes": 0, "preview_clear_files": 0, "preview_clear_bytes": 0, "skip_reasons": {}} for name in STORAGE_DIRS}
    candidates: list[Candidate] = []
    candidate_files_by_scope: dict[str, int] = {}
    candidate_bytes_by_scope: dict[str, int] = {}
    candidates_kept_by_scope: dict[str, int] = {}
    total_files = total_bytes = examined = 0
    partial = False
    try:
        for name in STORAGE_DIRS:
            directory = root / name
            try:
                directory.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                categories[name]["skip_reasons"]["read_error"] = 1
                partial = True
                continue
            if not _safe_directory(directory, root):
                categories[name]["skip_reasons"]["root_symlink"] = 1
                partial = True
                continue
            for path, skip in _walk(directory, max_files=max(0, max_files - examined), progress=progress):
                examined += 1
                if skip:
                    partial = True
                    reasons = categories[name]["skip_reasons"]
                    reasons[skip] = reasons.get(skip, 0) + 1
                    continue
                safe = _safe_file(path, root)
                if safe is None:
                    partial = True
                    reasons = categories[name]["skip_reasons"]
                    reasons["identity_or_read_error"] = reasons.get("identity_or_read_error", 0) + 1
                    continue
                size = int(safe.st_size)
                total_files += 1
                total_bytes += size
                bucket = categories[name]
                bucket["files"] += 1
                bucket["bytes"] += size
                if name == "previews" and _is_managed_preview(path, root):
                    bucket["preview_clear_files"] += 1
                    bucket["preview_clear_bytes"] += size
                candidate = _artifact_candidate(path, root, now, ARTIFACT_ORPHAN_AGE_MS) if name == "artifacts" else None
                reference_uncertain = bool(name == "artifacts" and candidate is not None and connection is None)
                if candidate:
                    try:
                        if _is_referenced(connection, candidate.relative_uri):
                            candidate = None
                    except sqlite3.Error:
                        candidate = None
                        reference_uncertain = True
                if candidate is None and name == "artifacts" and path.name.endswith(".tmp"):
                    candidate = _artifact_candidate(path, root, now, TEMPORARY_AGE_MS)
                    reference_uncertain = bool(candidate is not None and connection is None)
                    if candidate:
                        try:
                            if _is_referenced(connection, candidate.relative_uri):
                                candidate = None
                        except sqlite3.Error:
                            candidate = None
                            reference_uncertain = True
                if reference_uncertain:
                    partial = True
                    reasons = categories[name]["skip_reasons"]
                    reasons["artifact_references_unavailable"] = reasons.get("artifact_references_unavailable", 0) + 1
                if candidate is None and name in {"thumbnails", "previews", "cache"}:
                    candidate = _is_media_candidate(path, root, now)
                if candidate:
                    candidate_files_by_scope[candidate.scope] = candidate_files_by_scope.get(candidate.scope, 0) + 1
                    candidate_bytes_by_scope[candidate.scope] = candidate_bytes_by_scope.get(candidate.scope, 0) + size
                    if candidates_kept_by_scope.get(candidate.scope, 0) < DELETE_BATCH_SIZE:
                        candidates.append(candidate)
                        candidates_kept_by_scope[candidate.scope] = candidates_kept_by_scope.get(candidate.scope, 0) + 1
                    bucket["safe_cleanup_files"] += 1
                    bucket["safe_cleanup_bytes"] += size
                else:
                    is_unknown = name == "artifacts" and (reference_uncertain or not (_MANAGED_ARTIFACT.fullmatch(path.name) or _MANAGED_TEMP.fullmatch(path.name)))
                    if name in {"thumbnails", "previews", "cache"}:
                        parts = path.relative_to(root).parts
                        known_media = (
                            (name == "thumbnails" and len(parts) >= 2 and parts[1] == THUMBNAIL_PROCESSOR_ID)
                            or (name == "previews" and len(parts) >= 2 and parts[1] == PREVIEW_PROCESSOR_ID)
                            or (name == "cache" and len(parts) >= 2 and parts[1] in {"face-crops", "media"})
                        )
                        is_unknown = not known_media
                    field = "needs_verification" if is_unknown else "protected"
                    bucket[f"{field}_files"] += 1
                    bucket[f"{field}_bytes"] += size
        if examined >= max_files:
            partial = True
        result = {"status": "ok", "completed_at_ms": now, "directories": categories, "total_files": total_files, "total_bytes": total_bytes, "candidate_files": sum(candidate_files_by_scope.values()), "candidate_bytes": sum(candidate_bytes_by_scope.values()), "candidate_files_by_scope": candidate_files_by_scope, "candidate_bytes_by_scope": candidate_bytes_by_scope, "examined_files": examined, "max_files": max_files, "partial": partial, "bytes_basis": "regular-file st_size; sparse/compressed filesystem allocation may differ", "note": "Catalog BLOB values are logical breakdown only and are not added to directory totals. Cleanup candidates are retained in bounded per-scope batches."}
    finally:
        if connection is not None:
            connection.close()
    return result, candidates


def _persist_scan(root: Path, result: dict[str, Any]) -> None:
    runtime = root / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    if runtime.is_symlink() or getattr(runtime.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        raise OSError("runtime directory is a reparse point")
    path = _last_scan_path(root)
    if path.exists() and (path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode)):
        raise OSError("storage scan summary path is not a regular file")
    temporary = runtime / f"storage-inspection-{os.getpid()}-{os.urandom(4).hex()}.tmp"
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temporary, path)


def storage_inspect(data_root: str | Path | None, *, deep: bool = False) -> dict[str, Any]:
    if not deep:
        return quick_storage_summary(data_root)
    root = resolve_data_root(data_root).resolve()
    stream = _try_scan_lock(root)
    if stream is None:
        return {"status": "busy", "code": "STORAGE_SCAN_BUSY"}
    try:
        report, _ = inspect_storage(root)
        _persist_scan(root, report)
        return report
    finally:
        _release_scan_lock(stream)


def _identity_matches(path: Path, root: Path, expected: FileIdentity) -> bool:
    current = _safe_file(path, root)
    return current is not None and FileIdentity.from_stat(current) == expected


def _checkpoint_stopped_catalog(root: Path) -> None:
    path = catalog_db_path(root)
    if _safe_file(path, root) is None:
        return
    import sqlite3

    try:
        with sqlite3.connect(path, timeout=1.0) as connection:
            result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if result is not None and int(result[0]) != 0:
                raise RuntimeError("catalog_busy")
    except sqlite3.Error as error:
        raise RuntimeError("catalog_unavailable") from error


def storage_clean(data_root: str | Path | None, *, scopes: tuple[str, ...], apply: bool = False, stopped: bool = False, now_ms: int | None = None) -> dict[str, Any]:
    """Preview or execute a fresh, identity-checked file GC under the shared lease."""
    root = resolve_data_root(data_root).resolve()
    allowed = {"artifacts", "media-cache", "previews", "face-crops"}
    if not scopes or set(scopes) - allowed:
        raise ValueError("scope must contain one or more supported storage scopes")
    if apply and not stopped:
        return {"status": "error", "code": "STOPPED_CONFIRMATION_REQUIRED", "deleted_files": 0, "freed_bytes": 0}
    def select(candidates: list[Candidate]) -> list[Candidate]:
        selected = [item for item in candidates if item.scope in scopes]
        if "previews" in scopes:
            preview_root = root / "previews"
            if _safe_directory(preview_root, root):
                for path, reason in _walk(preview_root):
                    if reason:
                        continue
                    safe = _safe_file(path, root)
                    if safe is not None and _is_managed_preview(path, root) and all(item.path != path for item in selected):
                        if sum(1 for item in selected if item.scope == "previews") >= DELETE_BATCH_SIZE:
                            break
                        selected.append(Candidate(path, "previews", FileIdentity.from_stat(safe)))
        return selected

    if not apply:
        report, candidates = inspect_storage(root, now_ms=now_ms)
        selected = select(candidates)
        estimated_files = 0
        estimated_bytes = 0
        for scope in scopes:
            if scope == "previews":
                estimated_files += report["directories"]["previews"]["preview_clear_files"]
                estimated_bytes += report["directories"]["previews"]["preview_clear_bytes"]
            else:
                estimated_files += report["candidate_files_by_scope"].get(scope, 0)
                estimated_bytes += report["candidate_bytes_by_scope"].get(scope, 0)
        return {"status": "preview", "scopes": list(scopes), "eligible_files": estimated_files, "eligible_bytes": estimated_bytes, "preview_batch_files": len(selected), "partial": report["partial"], "report": report}

    from photo_search.runtime.data_root_lease import DataRootLeaseBusy, acquire_data_root_lease

    try:
        lease = acquire_data_root_lease(root)
    except DataRootLeaseBusy:
        return {"status": "error", "code": "DATA_ROOT_BUSY", "deleted_files": 0, "freed_bytes": 0}
    scan_stream = _try_scan_lock(root)
    if scan_stream is None:
        lease.close()
        return {"status": "error", "code": "STORAGE_SCAN_BUSY", "deleted_files": 0, "freed_bytes": 0}
    try:
        try:
            _checkpoint_stopped_catalog(root)
        except RuntimeError as error:
            code = "DATA_ROOT_BUSY" if str(error) == "catalog_busy" else "CATALOG_UNAVAILABLE"
            return {"status": "error", "code": code, "deleted_files": 0, "freed_bytes": 0}
        scan_report, candidates = inspect_storage(root, now_ms=now_ms)
        selected = select(candidates)
        removed = failures = freed = 0
        skipped = 0
        connection = _connection(root)
        try:
            for batch_start in range(0, len(selected), DELETE_BATCH_SIZE):
                for candidate in selected[batch_start : batch_start + DELETE_BATCH_SIZE]:
                    if not _identity_matches(candidate.path, root, candidate.identity):
                        skipped += 1
                        continue
                    if candidate.scope == "artifacts":
                        try:
                            if _is_referenced(connection, candidate.relative_uri):
                                skipped += 1
                                continue
                        except sqlite3.Error:
                            skipped += 1
                            continue
                    try:
                        candidate.path.unlink()
                        removed += 1
                        freed += candidate.identity.size
                    except OSError:
                        failures += 1
        finally:
            if connection is not None:
                connection.close()
        remaining = 0
        for scope in scopes:
            total = (
                scan_report["directories"]["previews"]["preview_clear_files"]
                if scope == "previews"
                else scan_report["candidate_files_by_scope"].get(scope, 0)
            )
            batch = sum(1 for item in selected if item.scope == scope)
            remaining += max(0, int(total) - batch)
        return {"status": "ok", "scopes": list(scopes), "deleted_files": removed, "freed_bytes": freed, "skipped_changed_or_referenced": skipped, "failed_files": failures, "remaining_estimate": remaining, "partial": skipped > 0 or failures > 0 or remaining > 0}
    finally:
        lease.close()
        _release_scan_lock(scan_stream)
