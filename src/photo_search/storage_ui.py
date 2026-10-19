"""User-triggered storage snapshots and one-shot offline maintenance coordination."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import time
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from photo_search.catalog.sqlite_io import open_readonly
from photo_search.config import catalog_db_path, resolve_data_root, runtime_dir
from photo_search.storage import _safe_file, storage_clean, storage_inspect
from photo_search.storage_maintenance import storage_optimize_vectors, storage_vacuum

REQUEST_TTL_MS = 60 * 60 * 1000
SCOPES = frozenset({"clean:artifacts", "clean:media-cache", "clean:previews", "clean:face-crops", "optimize-vectors", "vacuum"})
OPERATION_ORDER = ("clean:artifacts", "clean:media-cache", "clean:previews", "clean:face-crops", "optimize-vectors", "vacuum")
# User confirmed the P4-STORAGE-UI-A review report passed and explicitly authorized opening.
MAINTENANCE_REVIEWED = True


def _state_path(root: Path, name: str) -> Path:
    runtime = runtime_dir(root)
    try:
        if _is_reparse(runtime.lstat()):
            raise ValueError("runtime directory must not be a reparse point")
    except FileNotFoundError:
        pass
    return runtime / name


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or _is_reparse(info):
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _is_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if _is_reparse(path.parent.lstat()):
        raise ValueError("runtime directory must not be a reparse point")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def offline_request_lock(root: Path):
    """Serialize creation/cancellation of offline model and storage requests."""
    path = _state_path(root, "offline-request-schedule.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _canonical(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ValueError("请输入本机绝对路径")
    # Reject every existing reparse point in the path before resolving it.
    anchor = Path(candidate.anchor)
    current = anchor
    for part in candidate.parts[1:]:
        current = current / part
        try:
            if _is_reparse(current.lstat()):
                raise ValueError("备份目录路径不能包含符号链接或 junction")
        except FileNotFoundError:
            break
    return candidate.resolve(strict=False)


def _registered_sources(root: Path) -> list[Path]:
    db = catalog_db_path(root)
    if not db.is_file():
        return []
    connection = open_readonly(db)
    try:
        return [Path(str(row[0])).resolve(strict=False) for row in connection.execute("SELECT root_path FROM source_roots")]
    finally:
        connection.close()


def _overlap(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def validate_backup_directory(data_root: str | Path, directory: str | Path) -> tuple[Path, int]:
    root = resolve_data_root(data_root).resolve()
    target = _canonical(directory)
    if _overlap(root, target):
        raise ValueError("备份目录不能位于 PhotoSearchData 内，也不能包含 PhotoSearchData")
    for source in _registered_sources(root):
        if _overlap(source, target):
            raise ValueError("备份目录不能与已登记的原图来源重叠")
    target.mkdir(parents=True, exist_ok=True)
    # Recheck newly created components and require a regular writable directory.
    checked = Path(target.anchor)
    for part in target.parts[1:]:
        checked = checked / part
        info = checked.lstat()
        if _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("备份目录不能包含链接或异常路径")
    probe = target / f".photosearch-write-check-{uuid.uuid4().hex}.tmp"
    try:
        with probe.open("x", encoding="ascii") as stream:
            stream.write("ok")
        probe.unlink()
    except OSError as error:
        probe.unlink(missing_ok=True)
        raise ValueError("备份目录不可写") from error
    free = int(shutil.disk_usage(target).free)
    db = catalog_db_path(root)
    required = db.stat().st_size if db.is_file() else 0
    if free < required + 16 * 1024 * 1024:
        raise ValueError("备份目录剩余空间不足")
    return target, free


def backup_settings(data_root: str | Path) -> dict[str, Any]:
    root = resolve_data_root(data_root).resolve()
    configured = _read_json(_state_path(root, "storage-ui-settings.json")) or {}
    default = root.parent / "PhotoSearchBackups"
    return {"directory": str(configured.get("backup_directory") or default), "last_backup": configured.get("last_backup")}


def preview_maintenance(data_root: str | Path) -> dict[str, Any]:
    """Build all file-scope previews from one bounded, lock-protected inventory."""
    root = resolve_data_root(data_root).resolve()
    report = storage_inspect(root, deep=True)
    file_previews: dict[str, Any] = {}
    if report.get("status") != "ok":
        for scope in ("artifacts", "media-cache", "previews", "face-crops"):
            file_previews[scope] = {"status": report.get("status", "error"), "code": report.get("code", "STORAGE_SCAN_FAILED")}
    else:
        directories = report.get("directories", {})
        candidates = report.get("candidate_files_by_scope", {})
        candidate_bytes = report.get("candidate_bytes_by_scope", {})
        for scope in ("artifacts", "media-cache", "previews", "face-crops"):
            if scope == "previews":
                category = directories.get("previews", {})
                count = int(category.get("preview_clear_files", 0))
                size = int(category.get("preview_clear_bytes", 0))
            else:
                count = int(candidates.get(scope, 0))
                size = int(candidate_bytes.get(scope, 0))
            file_previews[scope] = {
                "status": "preview",
                "scopes": [scope],
                "eligible_files": count,
                "eligible_bytes": size,
                "preview_batch_files": min(count, 1_000),
                "partial": bool(report.get("partial")),
                "report": report,
            }
    for name, function in (("vectors", storage_optimize_vectors), ("vacuum", storage_vacuum)):
        try:
            file_previews[name] = function(root)
        except Exception as error:  # noqa: BLE001 - each optional preview reports its own state.
            file_previews[name] = {"status": "error", "reason": type(error).__name__}
    return {"items": file_previews}


def save_backup_directory(data_root: str | Path, directory: str | Path) -> dict[str, Any]:
    root = resolve_data_root(data_root).resolve()
    target, free = validate_backup_directory(root, directory)
    current = backup_settings(root)
    payload = {"backup_directory": str(target), "last_backup": current.get("last_backup")}
    _atomic_json(_state_path(root, "storage-ui-settings.json"), payload)
    return {"status": "ok", "directory": str(target), "free_bytes": free}


def create_catalog_snapshot(data_root: str | Path) -> dict[str, Any]:
    root = resolve_data_root(data_root).resolve()
    settings = backup_settings(root)
    directory, free = validate_backup_directory(root, settings["directory"])
    source = catalog_db_path(root)
    if _safe_file(source, root) is None:
        if source.exists():
            raise ValueError("目录数据库路径不安全")
        raise ValueError("目录数据库尚未创建，无法备份")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    target = directory / f"catalog-{stamp}-{uuid.uuid4().hex[:12]}.db"
    staging = directory / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        with closing(sqlite3.connect(source, timeout=10)) as src:
            with closing(sqlite3.connect(staging, timeout=10)) as dst:
                src.backup(dst, pages=256, sleep=0.02)
                result = str(dst.execute("PRAGMA integrity_check").fetchone()[0])
                if result != "ok":
                    raise ValueError("SQLite 快照完整性检查失败")
                dst.execute("PRAGMA journal_mode=DELETE")
        with closing(sqlite3.connect(f"file:{staging.as_posix()}?mode=ro", uri=True)) as check:
            if str(check.execute("PRAGMA integrity_check").fetchone()[0]) != "ok":
                raise ValueError("发布前 SQLite 完整性检查失败")
        if staging.stat().st_size + 16 * 1024 * 1024 > free:
            raise ValueError("快照完成后备份目录空间不足")
        rechecked, _ = validate_backup_directory(root, directory)
        if rechecked != directory:
            raise ValueError("备份目录在快照期间发生变化")
        # Hard-link publication is atomic and refuses to overwrite an existing name.
        os.link(staging, target)
        staging.unlink()
    finally:
        staging.unlink(missing_ok=True)
    created = datetime.now(timezone.utc).isoformat()
    _atomic_json(
        _state_path(root, "storage-ui-settings.json"),
        {"backup_directory": str(directory), "last_backup": {"path": str(target), "created_at": created, "status": "ok", "bytes": target.stat().st_size}},
    )
    return {"status": "ok", "path": str(target), "created_at": created, "bytes": target.stat().st_size, "scope": "catalog_only"}


def _data_identity(root: Path) -> dict[str, Any]:
    resolved = root.resolve()
    db = catalog_db_path(resolved)
    try:
        info = db.stat()
        # Content changes during normal use are allowed after confirmation. The stable
        # filesystem identity catches replacement/rebuild without invalidating the request
        # whenever a new person or annotation is saved before shutdown.
        database = {"exists": True, "device": info.st_dev, "inode": info.st_ino}
    except OSError:
        database = {"exists": False}
    return {"root": str(resolved).casefold(), "database": database}


def maintenance_request_state(data_root: str | Path) -> dict[str, Any]:
    root = resolve_data_root(data_root).resolve()
    pending = _read_json(_state_path(root, "storage-maintenance-request.json"))
    result = _read_json(_state_path(root, "storage-maintenance-result.json"))
    return {
        "pending": pending,
        "last_result": result,
        "execution_enabled": MAINTENANCE_REVIEWED,
        "review_required": None if MAINTENANCE_REVIEWED else "独立窄审完成前暂不开放离线维护执行。",
    }


def _normalize_operations(value: object) -> list[str]:
    if isinstance(value, str):
        requested = [value]
    elif isinstance(value, list):
        requested = value
    else:
        raise ValueError("请选择至少一项有效维护项目")
    if not requested or any(not isinstance(item, str) or item not in SCOPES for item in requested):
        raise ValueError("维护项目无效")
    if len(requested) != len(set(requested)):
        raise ValueError("维护项目不能重复")
    selected = set(requested)
    return [operation for operation in OPERATION_ORDER if operation in selected]


def queue_maintenance(data_root: str | Path, operations: object, *, confirmed: bool) -> dict[str, Any]:
    root = resolve_data_root(data_root).resolve()
    if not MAINTENANCE_REVIEWED:
        raise ValueError("独立窄审完成前暂不开放离线维护执行")
    if not confirmed:
        raise ValueError("维护项目无效或未确认")
    selected = _normalize_operations(operations)
    with offline_request_lock(root):
        transition = _read_json(_state_path(root, "embedding-offline-transition.json"))
        if transition and int(transition.get("expires_at_ms", 0)) > time.time_ns() // 1_000_000:
            raise ValueError("已有待执行的模型切换或旧索引隔离，不能同时安排存储维护")
        path = _state_path(root, "storage-maintenance-request.json")
        existing = _read_json(path)
        if existing and int(existing.get("expires_at_ms", 0)) > time.time_ns() // 1_000_000:
            raise ValueError("已有一项待执行维护，请先取消")
        request = {"request_id": uuid.uuid4().hex, "operations": selected, "created_at_ms": time.time_ns() // 1_000_000, "expires_at_ms": time.time_ns() // 1_000_000 + REQUEST_TTL_MS, "identity": _data_identity(root), "status": "pending"}
        if len(selected) == 1:
            request["operation"] = selected[0]  # Preserve compatibility with older one-action launchers.
        _atomic_json(path, request)
    return request


def cancel_maintenance(data_root: str | Path) -> dict[str, Any]:
    root = resolve_data_root(data_root).resolve()
    path = _state_path(root, "storage-maintenance-request.json")
    with offline_request_lock(root):
        pending = _read_json(path)
        if pending is None:
            return {"status": "none"}
        path.unlink(missing_ok=True)
    return {"status": "cancelled", "request_id": pending.get("request_id")}


def execute_pending_maintenance(data_root: str | Path) -> dict[str, Any] | None:
    """Consume a pending authorization before action; an interrupted operation never replays."""
    root = resolve_data_root(data_root).resolve()
    path = _state_path(root, "storage-maintenance-request.json")
    consumed = path.with_name(f"storage-maintenance-consuming-{uuid.uuid4().hex}.json")
    try:
        os.replace(path, consumed)
    except FileNotFoundError:
        return None
    request = _read_json(consumed)
    consumed.unlink(missing_ok=True)
    reason = None
    if not MAINTENANCE_REVIEWED:
        reason = "independent_review_required"
    elif not request or request.get("status") != "pending":
        reason = "invalid_request"
    elif int(request.get("expires_at_ms", 0)) < time.time_ns() // 1_000_000:
        reason = "request_expired"
    elif request.get("identity") != _data_identity(root):
        reason = "data_identity_changed"
    else:
        try:
            raw_operations = request.get("operations", request.get("operation"))
            operations = _normalize_operations(raw_operations)
        except ValueError:
            reason = "invalid_request"
    if reason:
        result = {"status": "skipped", "reason": reason, "request_id": (request or {}).get("request_id"), "completed_at": datetime.now(timezone.utc).isoformat()}
    else:
        result_path = _state_path(root, "storage-maintenance-result.json")
        started_at = datetime.now(timezone.utc).isoformat()
        result = {
            "status": "running",
            "operations": operations,
            "items": [],
            "request_id": request["request_id"],
            "started_at": started_at,
            "backup_reminder": "建议先手动备份人工整理数据；本次没有自动备份。",
        }
        if len(operations) == 1:
            result["operation"] = operations[0]
        _atomic_json(result_path, result)
        for operation in operations:
            try:
                item = {**_run_maintenance(root, operation), "operation": operation}
            except Exception as error:  # noqa: BLE001 - one failed item does not suppress the remaining selections.
                item = {"operation": operation, "status": "error", "reason": type(error).__name__, "message": str(error)}
            result["items"].append(item)
            _atomic_json(result_path, result)
        statuses = [str(item.get("status", "error")) for item in result["items"]]
        if all(status == "ok" and not item.get("partial") for status, item in zip(statuses, result["items"], strict=True)):
            result["status"] = "ok"
        elif any(status == "ok" for status in statuses):
            result["status"] = "partial"
        elif any(status in {"error", "failed"} for status in statuses):
            result["status"] = "error"
        elif all(status == "skipped" for status in statuses):
            result["status"] = "skipped"
        else:
            result["status"] = "partial"
        if len(operations) == 1:
            single_item = result["items"][0]
            result.update({key: value for key, value in single_item.items() if key not in {"operation", "status"}})
            result["operation"] = operations[0]
            result["operations"] = operations
        result.update({"request_id": request["request_id"], "completed_at": datetime.now(timezone.utc).isoformat()})
        _atomic_json(result_path, result)
    return result


def _run_maintenance(root: Path, operation: str) -> dict[str, Any]:
    if operation.startswith("clean:"):
        scope = operation.split(":", 1)[1]
        result = storage_clean(root, scopes=(scope,), apply=True, stopped=True)
    elif operation == "optimize-vectors":
        result = storage_optimize_vectors(root, apply=True, stopped=True)
    elif operation == "vacuum":
        result = storage_vacuum(root, apply=True, stopped=True)
    else:  # pragma: no cover - whitelist checked at both trust boundaries.
        return {"status": "error", "reason": "unsupported_operation"}
    return dict(result)
