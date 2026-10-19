"""One-shot, identity-bound offline embedding maintenance and profile selection."""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import stat
import time
from pathlib import Path
from typing import Any

from photo_search.config import catalog_db_path, vectors_dir
from photo_search.models.embedding.cli import read_active_profile
from photo_search.models.embedding.management import embedding_management_status
from photo_search.storage_ui import (
    _atomic_json,
    _data_identity,
    _read_json,
    _state_path,
    offline_request_lock,
)

REQUEST_FILE = "embedding-offline-transition.json"
RESULT_FILE = "embedding-offline-transition-result.json"
CONSUMING_FILE = "embedding-offline-transition-consuming.json"
ORPHAN_STATE_FILE = "embedding-orphan-isolation-state.json"
TRANSITION_TTL_MS = 60 * 60 * 1000
MIN_FREE_SPACE_BYTES = 64 * 1024 * 1024


def _request_path(root: Path) -> Path:
    return _state_path(root, REQUEST_FILE)


def transition_status(data_root: str | Path) -> dict[str, Any]:
    root = Path(data_root).resolve()
    return {
        "pending": _read_json(_request_path(root)),
        "last_result": _read_json(_state_path(root, RESULT_FILE)),
        "orphan_operation": _read_json(_state_path(root, ORPHAN_STATE_FILE)),
    }


def cancel_transition(data_root: str | Path) -> dict[str, Any]:
    root = Path(data_root).resolve()
    path = _request_path(root)
    with offline_request_lock(root):
        pending = _read_json(path)
        if pending is None:
            return {"status": "none"}
        path.unlink(missing_ok=True)
        result = {"status": "cancelled", "request_id": pending.get("request_id")}
        _atomic_json(_state_path(root, RESULT_FILE), result)
    return result


def _ensure_no_transition(root: Path) -> None:
    now = time.time_ns() // 1_000_000
    pending = _read_json(_request_path(root))
    if pending and int(pending.get("expires_at_ms", 0)) > now:
        raise ValueError("已有一项待执行的离线模型维护，请先取消")
    maintenance = _read_json(_state_path(root, "storage-maintenance-request.json"))
    if maintenance and int(maintenance.get("expires_at_ms", 0)) > now:
        raise ValueError("已有待执行的存储维护，不能同时安排模型切换或旧索引隔离")


def _store_request(root: Path, request: dict[str, Any]) -> None:
    with offline_request_lock(root):
        _ensure_no_transition(root)
        _atomic_json(_request_path(root), request)


def schedule_profile_switch(
    data_root: str | Path,
    *,
    provider_id: str,
    profile_id: str,
    request_id: str,
    confirmed: bool,
) -> dict[str, Any]:
    root = Path(data_root).resolve()
    if not confirmed:
        raise ValueError("必须由用户明确确认安排停机切换")
    if not request_id or len(request_id) > 128:
        raise ValueError("request_id must contain 1 to 128 characters")
    active = read_active_profile(root)
    db = Path(catalog_db_path(root))
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT profile_id,embedding_processor_id,space_id,image_generation_id "
            "FROM search_profiles WHERE profile_id=?",
            (str(profile_id),),
        ).fetchone()
    if row is None:
        raise ValueError("目标 SearchProfile 不存在")
    candidate_provider = str(provider_id)
    from photo_search.models.embedding.base import provider_id_of_processor

    if provider_id_of_processor(str(row["embedding_processor_id"])) != candidate_provider:
        raise ValueError("目标 profile 与请求的 provider 不匹配")
    now = time.time_ns() // 1_000_000
    request = {
        "kind": "switch_profile",
        "request_id": str(request_id),
        "provider_id": candidate_provider,
        "target_profile": {key: str(row[key]) for key in ("profile_id", "embedding_processor_id", "space_id", "image_generation_id")},
        "active_profile": active,
        "identity": _data_identity(root),
        "data_root": str(root),
        "created_at_ms": now,
        "expires_at_ms": now + TRANSITION_TTL_MS,
        "status": "pending",
    }
    _store_request(root, request)
    return request


def schedule_orphan_isolation(
    data_root: str | Path,
    *,
    provider_id: str,
    request_id: str,
) -> dict[str, Any]:
    root = Path(data_root).resolve()
    if not request_id or len(request_id) > 128:
        raise ValueError("request_id must contain 1 to 128 characters")
    provider = next(
        (item for item in embedding_management_status(root)["providers"] if item["provider_id"] == provider_id),
        None,
    )
    if not provider or provider["index_state"] != "ORPHAN_TABLE":
        raise ValueError("目标 provider 没有可隔离的孤儿向量表")
    table_name = _table_name_for_generation(provider)
    table_root = Path(vectors_dir(root)).resolve()
    table_path = table_root / f"{table_name}.lance"
    facts = _physical_facts(table_path, table_root)
    now = time.time_ns() // 1_000_000
    request = {
        "kind": "isolate_orphan",
        "request_id": str(request_id),
        "provider_id": str(provider_id),
        "generation_id": str(provider["generation_id"]),
        "table_name": table_name,
        "physical_identity": facts,
        "identity": _data_identity(root),
        "data_root": str(root),
        "created_at_ms": now,
        "expires_at_ms": now + TRANSITION_TTL_MS,
        "status": "pending",
    }
    _store_request(root, request)
    return request


def _table_name_for_generation(provider: dict[str, Any]) -> str:
    from photo_search.indexing.store import _table_name

    return _table_name(str(provider["generation_id"]))


def _table_name_for(generation_id: str) -> str:
    from photo_search.indexing.store import _table_name

    return _table_name(str(generation_id))


def _physical_facts(table_path: Path, vectors_root: Path) -> dict[str, Any]:
    try:
        table_path.resolve().relative_to(vectors_root.resolve())
        info = table_path.lstat()
    except (OSError, ValueError) as error:
        raise ValueError("Lance table path is missing or outside the vectors directory") from error
    if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
        raise ValueError("Lance table directory must be a regular directory")
    files: list[dict[str, Any]] = []
    total = 0
    for current, dirs, names in os.walk(table_path, followlinks=False):
        for name in dirs + names:
            path = Path(current) / name
            item = path.lstat()
            if _is_reparse(item):
                raise ValueError("Lance table contains a reparse point")
            if stat.S_ISREG(item.st_mode):
                total += int(item.st_size)
                files.append({
                    "path": path.relative_to(table_path).as_posix(),
                    "size": int(item.st_size),
                    "mtime_ns": int(item.st_mtime_ns),
                    "device": int(item.st_dev),
                    "file_id": int(item.st_ino),
                })
    files.sort(key=lambda item: str(item["path"]))
    return {
        "path": str(table_path.resolve()),
        "device": int(info.st_dev),
        "file_id": int(info.st_ino),
        "mtime_ns": int(info.st_mtime_ns),
        "files": files,
        "bytes": total,
    }


def _is_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _safe_path_present(path: Path, root: Path) -> bool:
    """Check a vectors-relative path with lstat so dangling links cannot look absent."""
    root = root.resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ValueError("PATH_OUTSIDE_VECTORS_ROOT") from error
    current = root
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return False
        except OSError as error:
            raise ValueError("PATH_IDENTITY_UNREADABLE") from error
        if _is_reparse(info):
            raise ValueError("PATH_REPARSE_POINT")
        if current != path and not stat.S_ISDIR(info.st_mode):
            raise ValueError("PATH_PARENT_NOT_DIRECTORY")
    return True


def _orphan_error(request: dict[str, Any], reason: str, message: str) -> dict[str, Any]:
    return {"request_id": request.get("request_id"), "status": "error",
            "reason": reason, "message": str(message)[:400]}


def _orphan_isolation_preflight(
    root: Path, request: dict[str, Any], *, allow_missing_source: bool = False
) -> dict[str, Any]:
    """Revalidate the same data-root, catalog, generation and physical-table binding everywhere."""
    from photo_search.artifacts.store import generation_id_for
    from photo_search.indexing.store import _table_name
    from photo_search.models.embedding.registry import registry_for

    root = Path(root).resolve()
    requested_root = Path(str(request.get("data_root") or "")).resolve(strict=False)
    if str(requested_root).casefold() != str(root).casefold():
        raise ValueError("DATA_ROOT_CHANGED")
    if request.get("identity") != _data_identity(root):
        raise ValueError("DATA_IDENTITY_CHANGED")
    generation_id = str(request.get("generation_id") or "")
    provider_id = str(request.get("provider_id") or "")
    try:
        provider = registry_for(root).get(provider_id)
    except Exception as error:  # noqa: BLE001 - an unresolved provider cannot authorize a move.
        raise ValueError("PROVIDER_IDENTITY_UNAVAILABLE") from error
    expected_generation = generation_id_for(provider.identity.space_id, provider.identity.processor_id)
    if generation_id != expected_generation:
        raise ValueError("GENERATION_IDENTITY_CHANGED")
    table_name = _table_name(generation_id)
    if str(request.get("table_name") or "") != table_name:
        raise ValueError("TABLE_NAME_MISMATCH")
    physical_identity = request.get("physical_identity")
    if not isinstance(physical_identity, dict) or not physical_identity.get("file_id"):
        raise ValueError("AUTHORIZED_PHYSICAL_IDENTITY_MISSING")

    db_path = Path(catalog_db_path(root))
    if db_path.is_file():
        try:
            connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            try:
                tables = {str(row[0]) for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
                if "index_generations" in tables and connection.execute(
                    "SELECT 1 FROM index_generations WHERE generation_id=? LIMIT 1", (generation_id,)
                ).fetchone():
                    raise ValueError("GENERATION_REFERENCED")
                if "search_profiles" in tables and connection.execute(
                    "SELECT 1 FROM search_profiles WHERE image_generation_id=? LIMIT 1", (generation_id,)
                ).fetchone():
                    raise ValueError("PROFILE_REFERENCES_GENERATION")
            finally:
                connection.close()
        except ValueError:
            raise
        except sqlite3.Error as error:
            raise ValueError("CATALOG_UNREADABLE") from error

    vectors_root = Path(vectors_dir(root)).resolve()
    source = vectors_root / f"{table_name}.lance"
    source_present = _safe_path_present(source, vectors_root)
    if not source_present:
        if not allow_missing_source:
            raise ValueError("SOURCE_TABLE_MISSING")
        source_identity = None
    else:
        try:
            source_identity = _physical_facts(source, vectors_root)
        except ValueError as error:
            raise ValueError("TABLE_IDENTITY_CHANGED") from error
        if source_identity != physical_identity:
            raise ValueError("TABLE_IDENTITY_CHANGED")
    return {"generation_id": generation_id, "table_name": table_name,
            "vectors_root": vectors_root, "source": source,
            "source_identity": source_identity, "source_present": source_present}


def _same_moved_physical_identity(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    actual = dict(actual)
    expected = dict(expected)
    # A same-filesystem rename preserves object/file ids and metadata; only the root path changes.
    actual.pop("path", None)
    expected.pop("path", None)
    return actual == expected


def execute_pending_transition(data_root: str | Path, lease: Any) -> dict[str, Any] | None:
    """Consume one offline action while retaining the exact caller-owned data-root lease."""
    if getattr(lease, "_stream", None) is None:
        raise RuntimeError("offline embedding maintenance requires a live DataRootLease")
    root = Path(data_root).resolve()
    path = _request_path(root)
    consumed = _state_path(root, CONSUMING_FILE)
    if path.exists():
        try:
            os.replace(path, consumed)
        except FileNotFoundError:
            pass
    request = _read_json(consumed)
    if request is None:
        return None
    resuming_consumed = request.get("status") == "consuming"
    if request.get("status") != "consuming" and int(request.get("expires_at_ms", 0)) < time.time_ns() // 1_000_000:
        result = {"request_id": request.get("request_id"), "status": "error", "reason": "REQUEST_EXPIRED"}
        _atomic_json(_state_path(root, RESULT_FILE), result)
        consumed.unlink(missing_ok=True)
        return result
    request["status"] = "consuming"
    _atomic_json(consumed, request)
    result_path = _state_path(root, RESULT_FILE)
    result: dict[str, Any] = {"request_id": request.get("request_id"), "status": "running", "kind": request.get("kind")}
    _atomic_json(result_path, result)
    try:
        if request.get("status") not in {"pending", "consuming"}:
            result["reason"] = "INVALID_REQUEST"
        elif request.get("identity") != _data_identity(root):
            result["reason"] = "DATA_IDENTITY_CHANGED"
        elif request.get("kind") == "isolate_orphan":
            result = _recover_or_isolate_orphan(root, request)
        elif request.get("kind") == "switch_profile":
            if resuming_consumed:
                result = _recover_or_switch_profile(root, lease, request)
            else:
                request["phase_started_ms"] = time.time_ns() // 1_000_000
                _atomic_json(consumed, request)
                result = _switch_profile(root, lease, request)
        else:
            result["reason"] = "UNKNOWN_TRANSITION"
    except Exception as error:  # noqa: BLE001 - journal and original index remain for manual recovery.
        result = {"request_id": request.get("request_id"), "status": "error", "reason": type(error).__name__, "message": str(error)[:400]}
    result["completed_at_ms"] = time.time_ns() // 1_000_000
    _atomic_json(result_path, result)
    consumed.unlink(missing_ok=True)
    return result


def _recover_or_switch_profile(root: Path, lease: Any, request: dict[str, Any]) -> dict[str, Any]:
    target = request.get("target_profile") or {}
    active = read_active_profile(root)
    if active and active.get("profile_id") == target.get("profile_id"):
        return {"request_id": request.get("request_id"), "status": "activated", "recovered_after_restart": True, "active_profile": active}
    if active != request.get("active_profile"):
        return {"request_id": request.get("request_id"), "status": "error", "reason": "ACTIVE_PROFILE_CHANGED", "active_profile": active}
    if request.get("phase_started_ms"):
        return {"request_id": request.get("request_id"), "status": "error", "reason": "INTERRUPTED_BEFORE_COMMIT", "active_profile": active}
    return _switch_profile(root, lease, request)


def _recover_or_isolate_orphan(root: Path, request: dict[str, Any]) -> dict[str, Any]:
    from photo_search.config import vectors_dir

    vectors_root = Path(vectors_dir(root)).resolve()
    request_id = str(request.get("request_id") or "")
    operation_id = hashlib.sha256(request_id.encode()).hexdigest()[:20]
    base = vectors_root / "_isolated" / operation_id
    backup_root, isolated_root = base / "backup", base / "isolated"
    journal = _read_json(_state_path(root, ORPHAN_STATE_FILE))
    source = vectors_root / f"{request.get('table_name', '')}.lance"
    backup = backup_root / source.name
    isolated = isolated_root / source.name
    if not journal and not base.exists():
        return _isolate_orphan(root, request)
    if not journal or journal.get("request_id") != request_id:
        return _orphan_error(request, "RECOVERY_NEEDS_MANUAL_REVIEW", "operation journal does not match the request")
    if journal.get("stage") not in {"BACKUP_VERIFIED", "MOVE_STARTED", "ISOLATED"}:
        return _orphan_error(request, "RECOVERY_NEEDS_MANUAL_REVIEW", "backup stage is incomplete; original table was not moved")
    try:
        checked = _orphan_isolation_preflight(root, request, allow_missing_source=True)
        if any(journal.get(key) != request.get(key) for key in (
            "kind", "data_root", "identity", "provider_id", "generation_id", "table_name", "physical_identity"
        )):
            raise ValueError("operation journal binding differs from the original authorization")
        if not _safe_path_present(backup, vectors_root) or not backup.is_dir():
            raise ValueError("verified backup is missing")
        source_present = bool(checked["source_present"])
        isolated_present = _safe_path_present(isolated, vectors_root)
        backup_hash = _hash_tree(backup)
        if not backup_hash or backup_hash != journal.get("backup_sha256"):
            raise ValueError("backup hash differs from the durable verification record")
        if source_present and not isolated_present:
            if checked["source_identity"] != request["physical_identity"]:
                raise ValueError("source physical identity changed after authorization")
            if _hash_tree(source) != backup_hash:
                raise ValueError("source and verified backup differ")
            _verify_lance_copy(name=checked["table_name"], source_root=vectors_root, copy_root=backup_root)
            if journal.get("stage") == "BACKUP_VERIFIED":
                journal["stage"] = "MOVE_STARTED"
                _atomic_json(_state_path(root, ORPHAN_STATE_FILE), journal)
            os.replace(source, isolated)
            isolated_facts = _physical_facts(isolated, vectors_root)
            if not _same_moved_physical_identity(isolated_facts, request["physical_identity"]):
                raise ValueError("moved table physical identity differs from the authorization")
        elif not source_present and isolated_present and journal.get("stage") in {"MOVE_STARTED", "ISOLATED"}:
            isolated_facts = _physical_facts(isolated, vectors_root)
            if not _same_moved_physical_identity(isolated_facts, request["physical_identity"]):
                raise ValueError("isolated table physical identity differs from the authorization")
            if _hash_tree(isolated) != backup_hash:
                raise ValueError("isolated table and verified backup differ")
            _verify_lance_copy(name=checked["table_name"], source_root=isolated_root, copy_root=backup_root)
        else:
            raise ValueError("source/isolation state is ambiguous")
        journal.update({"stage": "ISOLATED", "status": "ok", "backup": str(backup),
                        "isolated_path": str(isolated), "recovered_after_restart": True})
        _atomic_json(_state_path(root, ORPHAN_STATE_FILE), journal)
        return {"request_id": request_id, "status": "ok", "operation": journal, "recovered_after_restart": True}
    except Exception as error:  # noqa: BLE001 - ambiguity is surfaced; all files stay in place.
        return _orphan_error(request, "RECOVERY_NEEDS_MANUAL_REVIEW", str(error))


def _isolate_orphan(root: Path, request: dict[str, Any]) -> dict[str, Any]:
    try:
        checked = _orphan_isolation_preflight(root, request)
    except ValueError as error:
        return _orphan_error(request, str(error), str(error))
    generation_id, name = checked["generation_id"], checked["table_name"]
    vectors_root, source, current = checked["vectors_root"], checked["source"], checked["source_identity"]
    total_bytes = int(current["bytes"])
    if int(shutil.disk_usage(vectors_root).free) < total_bytes + MIN_FREE_SPACE_BYTES:
        return {"request_id": request.get("request_id"), "status": "error", "reason": "INSUFFICIENT_SPACE", "required_bytes": total_bytes + MIN_FREE_SPACE_BYTES}
    operation_id = hashlib.sha256(str(request["request_id"]).encode()).hexdigest()[:20]
    base = vectors_root / "_isolated" / operation_id
    backup_root = base / "backup"
    isolated_root = base / "isolated"
    if _safe_path_present(base, vectors_root):
        return {"request_id": request.get("request_id"), "status": "error", "reason": "DESTINATION_EXISTS"}
    backup_root.mkdir(parents=True)
    isolated_root.mkdir(parents=True)
    state_path = _state_path(root, ORPHAN_STATE_FILE)
    state = {"request_id": request.get("request_id"), "kind": "isolate_orphan",
             "data_root": request.get("data_root"), "identity": request.get("identity"),
             "provider_id": request.get("provider_id"), "physical_identity": request.get("physical_identity"),
             "table_name": name, "generation_id": generation_id, "status": "running", "stage": "BACKUP_COPYING"}
    _atomic_json(state_path, state)
    backup = backup_root / source.name
    try:
        shutil.copytree(source, backup, copy_function=shutil.copy2)
        state["stage"] = "BACKUP_COPIED"
        _atomic_json(state_path, state)
        if _hash_tree(source) != _hash_tree(backup):
            raise ValueError("backup byte verification failed")

        _verify_lance_copy(name=name, source_root=vectors_root, copy_root=backup_root)
        state["backup_sha256"] = _hash_tree(backup)
        state["stage"] = "BACKUP_VERIFIED"
        _atomic_json(state_path, state)
        checked_again = _orphan_isolation_preflight(root, request)
        if checked_again["source_identity"] != current or _hash_tree(source) != state["backup_sha256"]:
            raise ValueError("source table identity changed after backup")
        state["stage"] = "MOVE_STARTED"
        _atomic_json(state_path, state)
        os.replace(source, isolated_root / source.name)
        moved_identity = _physical_facts(isolated_root / source.name, vectors_root)
        if not _same_moved_physical_identity(moved_identity, request["physical_identity"]):
            raise ValueError("moved table physical identity differs from the authorization")
        state.update({"stage": "ISOLATED", "status": "ok", "backup": str(backup), "isolated_path": str(isolated_root / source.name), "verified_bytes": total_bytes})
        _atomic_json(state_path, state)
        return {"request_id": request.get("request_id"), "status": "ok", "operation": state}
    except Exception as error:  # noqa: BLE001 - the untouched original remains authoritative on failure.
        state.update({"status": "error", "reason": type(error).__name__, "message": str(error)[:400]})
        _atomic_json(state_path, state)
        return {"request_id": request.get("request_id"), "status": "error", "operation": state}


def _hash_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        info = path.lstat()
        if _is_reparse(info):
            raise ValueError("backup tree contains a reparse point")
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def _verify_lance_copy(*, name: str, source_root: Path | None, copy_root: Path) -> None:
    import lancedb

    copied = lancedb.connect(str(copy_root)).open_table(name)
    if copied.count_rows() < 0 or not str(copied.schema):
        raise ValueError("backup Lance table metadata is invalid")
    if source_root is not None:
        source = lancedb.connect(str(source_root)).open_table(name)
        if int(source.count_rows()) != int(copied.count_rows()) or str(source.schema) != str(copied.schema):
            raise ValueError("backup Lance schema/row verification failed")


def _switch_profile(root: Path, lease: Any, request: dict[str, Any]) -> dict[str, Any]:
    from argparse import Namespace

    from photo_search.models.embedding.cli import _select_profile

    target = request.get("target_profile") or {}
    active = read_active_profile(root)
    if active != request.get("active_profile"):
        return {"request_id": request.get("request_id"), "status": "error", "reason": "ACTIVE_PROFILE_CHANGED"}
    db = Path(catalog_db_path(root))
    try:
        with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT profile_id,embedding_processor_id,space_id,image_generation_id "
                "FROM search_profiles WHERE profile_id=?",
                (str(target.get("profile_id") or ""),),
            ).fetchone()
    except sqlite3.Error:
        row = None
    if row is None or tuple(str(value) for value in row) != tuple(
        str(target.get(key) or "")
        for key in ("profile_id", "embedding_processor_id", "space_id", "image_generation_id")
    ):
        return {"request_id": request.get("request_id"), "status": "error", "reason": "TARGET_PROFILE_CHANGED"}
    try:
        selected = _select_profile(
            Namespace(data_root=root, stopped=True, profile=str(target.get("profile_id") or ""), _data_root_lease=lease),
        )
    except Exception as error:  # noqa: BLE001 - pre-commit refusals leave the current profile intact.
        return {"request_id": request.get("request_id"), "status": "error", "reason": type(error).__name__, "message": str(error)[:400], "active_profile": read_active_profile(root)}
    return {"request_id": request.get("request_id"), "status": "activated", "selection": selected, "active_profile": read_active_profile(root)}


__all__ = [
    "ORPHAN_STATE_FILE",
    "REQUEST_FILE",
    "RESULT_FILE",
    "execute_pending_transition",
    "cancel_transition",
    "schedule_orphan_isolation",
    "schedule_profile_switch",
    "transition_status",
]
