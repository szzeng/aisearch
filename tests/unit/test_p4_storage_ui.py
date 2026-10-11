"""P4-STORAGE-UI-A: synthetic snapshots, path guards and one-shot maintenance."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from photo_search.backup import MANIFEST_NAME, create_backup, restore_backup
from photo_search.catalog.db import Catalog
from photo_search.config import catalog_db_path
from photo_search.runtime.data_root_lease import acquire_data_root_lease
from photo_search.storage_ui import (
    backup_settings,
    create_catalog_snapshot,
    execute_pending_maintenance,
    queue_maintenance,
    save_backup_directory,
)


def _data(root: Path) -> Path:
    data_root = root / "PhotoSearchData"
    with Catalog.open(data_root=data_root) as catalog:
        with catalog.write() as connection:
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES ('storage_ui_probe', '人工整理状态')"
            )
            connection.execute(
                "INSERT INTO persons(person_id, name, name_key, revision, created_at, updated_at) "
                "VALUES (81001, '人工整理人物', 'storage-ui-person', 1, 1, 1)"
            )
            connection.execute(
                "INSERT INTO person_collections(collection_id, name, name_key, revision, created_at, updated_at, instance_token) "
                "VALUES (82001, '人工整理合集', 'storage-ui-collection', 1, 1, 1, '82001820018200182001820018200182')"
            )
            connection.execute(
                "INSERT INTO person_collection_members(collection_id, person_id) VALUES (82001, 81001)"
            )
            connection.execute(
                "INSERT INTO places(place_id, kind, canonical_name, normalized_name, origin, lifecycle, revision, created_at, updated_at) "
                "VALUES (83001, 'custom', '人工整理地点', '人工整理地点', 'USER', 'ACTIVE', 1, 1, 1)"
            )
    return data_root


def test_manual_catalog_snapshots_are_consistent_and_never_rotate(tmp_path: Path) -> None:
    root = _data(tmp_path)
    backup_dir = tmp_path / "PhotoSearchBackups"
    assert save_backup_directory(root, backup_dir)["status"] == "ok"
    first = create_catalog_snapshot(root)
    second = create_catalog_snapshot(root)

    assert first["status"] == second["status"] == "ok"
    assert first["path"] != second["path"]
    assert Path(str(first["path"])).is_file()
    assert Path(str(second["path"])).is_file()
    for path in (first["path"], second["path"]):
        with sqlite3.connect(f"file:{Path(str(path)).as_posix()}?mode=ro", uri=True) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("SELECT value FROM metadata WHERE key='storage_ui_probe'").fetchone()[0] == "人工整理状态"
            assert connection.execute("SELECT name FROM persons WHERE person_id=81001").fetchone()[0] == "人工整理人物"
            assert connection.execute("SELECT name FROM person_collections WHERE collection_id=82001").fetchone()[0] == "人工整理合集"
            assert connection.execute("SELECT person_id FROM person_collection_members WHERE collection_id=82001").fetchone()[0] == 81001
            assert connection.execute("SELECT canonical_name FROM places WHERE place_id=83001").fetchone()[0] == "人工整理地点"
    assert backup_settings(root)["last_backup"]["path"] == second["path"]


def test_backup_location_rejects_data_root_and_registered_source(tmp_path: Path) -> None:
    root = _data(tmp_path)
    with Catalog.open(data_root=root) as catalog:
        with catalog.write() as connection:
            connection.execute(
                "INSERT INTO source_roots(display_name, root_path, status) VALUES ('source', ?, 'ONLINE')",
                (str(tmp_path / "photos"),),
            )
    (tmp_path / "photos").mkdir()
    for invalid in (root / "backups", tmp_path / "photos" / "nested", tmp_path):
        with pytest.raises(ValueError):
            save_backup_directory(root, invalid)


def test_confirmed_maintenance_is_consumed_once_and_never_creates_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _data(tmp_path)
    monkeypatch.setattr("photo_search.storage_ui.MAINTENANCE_REVIEWED", True)
    request = queue_maintenance(root, "vacuum", confirmed=True)
    called: list[str] = []

    def run(data_root: Path, operation: str) -> dict[str, object]:
        called.append(operation)
        assert data_root == root.resolve()
        return {"status": "skipped", "reason": "synthetic_no_space_gain"}

    monkeypatch.setattr("photo_search.storage_ui._run_maintenance", run)
    result = execute_pending_maintenance(root)
    assert result and result["status"] == "skipped"
    assert result["request_id"] == request["request_id"]
    assert result["backup_reminder"]
    assert called == ["vacuum"]
    assert execute_pending_maintenance(root) is None
    assert not (tmp_path / "PhotoSearchBackups").exists()


def test_real_synthetic_gc_path_never_creates_an_implicit_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _data(tmp_path)
    monkeypatch.setattr("photo_search.storage_ui.MAINTENANCE_REVIEWED", True)
    backup_dir = tmp_path / "PhotoSearchBackups"
    save_backup_directory(root, backup_dir)
    queue_maintenance(root, "clean:artifacts", confirmed=True)
    result = execute_pending_maintenance(root)
    assert result and result["status"] == "ok"
    assert result["deleted_files"] == 0
    assert list(backup_dir.glob("catalog-*.db")) == []


def test_locked_data_root_request_is_consumed_without_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _data(tmp_path)
    monkeypatch.setattr("photo_search.storage_ui.MAINTENANCE_REVIEWED", True)
    queue_maintenance(root, "clean:artifacts", confirmed=True)
    lease = acquire_data_root_lease(root)
    try:
        result = execute_pending_maintenance(root)
    finally:
        lease.close()
    assert result and result["status"] == "error"
    assert result["code"] == "DATA_ROOT_BUSY"
    assert execute_pending_maintenance(root) is None


def test_catalog_replacement_invalidates_pending_maintenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _data(tmp_path)
    monkeypatch.setattr("photo_search.storage_ui.MAINTENANCE_REVIEWED", True)
    queue_maintenance(root, "vacuum", confirmed=True)
    database = catalog_db_path(root)
    replacement = database.with_name("replacement.db")
    replacement.write_bytes(database.read_bytes())
    replacement.replace(database)
    monkeypatch.setattr(
        "photo_search.storage_ui._run_maintenance",
        lambda *_: pytest.fail("changed catalog identity must not execute"),
    )
    result = execute_pending_maintenance(root)
    assert result and result["status"] == "skipped"
    assert result["reason"] == "data_identity_changed"
    assert execute_pending_maintenance(root) is None


def test_unreviewed_startup_gate_consumes_any_pending_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _data(tmp_path)
    monkeypatch.setattr("photo_search.storage_ui.MAINTENANCE_REVIEWED", False)
    request_path = root / "runtime" / "storage-maintenance-request.json"
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(
        json.dumps({"status": "pending", "operation": "clean:artifacts", "request_id": "synthetic", "expires_at_ms": 9_999_999_999_999, "identity": {"root": str(root.resolve()).casefold(), "database": {"exists": True, "device": catalog_db_path(root).stat().st_dev, "inode": catalog_db_path(root).stat().st_ino}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("photo_search.storage_ui._run_maintenance", lambda *_: pytest.fail("unreviewed operation ran"))
    result = execute_pending_maintenance(root)
    assert result and result["reason"] == "independent_review_required"
    assert execute_pending_maintenance(root) is None


def test_restore_never_resurrects_legacy_one_shot_authorization(tmp_path: Path) -> None:
    source = _data(tmp_path / "src")
    legacy = tmp_path / "legacy-bundle"
    create_backup(data_root=source, destination=legacy, stopped=True)
    stale = legacy / "runtime" / "storage-maintenance-request.json"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text('{"operation":"clean:artifacts","status":"pending"}', encoding="utf-8")
    manifest_path = legacy / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["members"].append(
        {"path": "runtime/storage-maintenance-request.json", "size": stale.stat().st_size, "sha256": hashlib.sha256(stale.read_bytes()).hexdigest()}
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    restored = tmp_path / "restored"
    restore_backup(bundle=legacy, data_root=restored)
    assert not (restored / "runtime" / "storage-maintenance-request.json").exists()


def test_backup_contract_reports_catalog_only_snapshot(tmp_path: Path) -> None:
    root = _data(tmp_path)
    outcome = create_catalog_snapshot(root)
    assert outcome["scope"] == "catalog_only"
    assert "原图" not in str(outcome)
    assert catalog_db_path(root).exists()


def test_batch_maintenance_runs_selected_operations_in_order_and_records_each_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _data(tmp_path)
    monkeypatch.setattr("photo_search.storage_ui.MAINTENANCE_REVIEWED", True)
    queued = queue_maintenance(
        root,
        ["vacuum", "clean:previews", "clean:artifacts"],
        confirmed=True,
    )
    assert queued["operations"] == ["clean:artifacts", "clean:previews", "vacuum"]
    assert "operation" not in queued
    called: list[str] = []

    def run(data_root: Path, operation: str) -> dict[str, object]:
        called.append(operation)
        assert data_root == root.resolve()
        if operation == "clean:previews":
            checkpoint = json.loads((root / "runtime" / "storage-maintenance-result.json").read_text(encoding="utf-8"))
            assert [item["operation"] for item in checkpoint["items"]] == ["clean:artifacts"]
            raise RuntimeError("synthetic preview cleanup failure")
        return {"status": "ok", "freed_bytes": 0}

    monkeypatch.setattr("photo_search.storage_ui._run_maintenance", run)
    result = execute_pending_maintenance(root)
    assert result and result["status"] == "partial"
    assert called == ["clean:artifacts", "clean:previews", "vacuum"]
    assert [(item["operation"], item["status"]) for item in result["items"]] == [
        ("clean:artifacts", "ok"),
        ("clean:previews", "error"),
        ("vacuum", "ok"),
    ]
    assert execute_pending_maintenance(root) is None


def test_batch_maintenance_rejects_empty_unknown_and_duplicate_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _data(tmp_path)
    monkeypatch.setattr("photo_search.storage_ui.MAINTENANCE_REVIEWED", True)
    for operations in ([], ["vacuum", "unknown"], ["vacuum", "vacuum"]):
        with pytest.raises(ValueError):
            queue_maintenance(root, operations, confirmed=True)


def test_single_batch_item_preserves_partial_aggregate_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _data(tmp_path)
    monkeypatch.setattr("photo_search.storage_ui.MAINTENANCE_REVIEWED", True)
    queue_maintenance(root, ["clean:artifacts"], confirmed=True)
    monkeypatch.setattr(
        "photo_search.storage_ui._run_maintenance",
        lambda *_: {"status": "ok", "partial": True, "deleted_files": 1},
    )
    result = execute_pending_maintenance(root)
    assert result and result["status"] == "partial"
    assert result["operation"] == "clean:artifacts"
    assert result["items"][0]["partial"] is True


def test_batch_result_dom_renders_actual_per_item_cleanup_metrics() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    script = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
const nodes = {
  "storage-maintenance-confirm": {dataset:{}, disabled:false},
  "storage-maintenance-review-gate": {textContent:""},
};
const context = {document:{readyState:"loading", addEventListener(){}, getElementById:id=>nodes[id], querySelectorAll:()=>[]}};
let code = fs.readFileSync("src/photo_search/web/app.js", "utf8");
code = code.replace('  if (document.readyState === "loading") {',
  '  globalThis.storageUI = {renderMaintenance, els};\n  if (document.readyState === "loading") {');
vm.createContext(context); vm.runInContext(code, context);
context.storageUI.els.storageMaintenancePreview = {textContent:"", className:"", hidden:false};
context.storageUI.renderMaintenance({execution_enabled:true, last_result:{
  status:"partial", operations:["clean:artifacts","optimize-vectors","vacuum"], items:[
    {operation:"clean:artifacts",status:"ok",deleted_files:2,freed_bytes:1024},
    {operation:"optimize-vectors",status:"ok",stages:[{name:"cleanup_old_versions",actual_reclaimed_bytes:128}]},
    {operation:"vacuum",status:"ok",actual_reclaimed_bytes:256},
  ]
}});
const rendered = context.storageUI.els.storageMaintenancePreview.textContent;
assert.match(rendered,/孤立产物：ok；删除 2 个文件，实际释放 1\.0 KiB/);
assert.match(rendered,/向量索引：ok；阶段实际释放 128 B/);
assert.match(rendered,/SQLite VACUUM：ok；实际释放 256 B/);
'''
    result = subprocess.run(
        [node, "-e", script], cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
