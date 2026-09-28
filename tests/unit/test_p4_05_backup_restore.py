from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from photo_search.api.settings import ApiSettings
from photo_search.app import create_app
from photo_search.backup import MANIFEST_NAME, create_backup, restore_backup
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import CatalogError
from photo_search.catalog.schema import SUPPORTED_SCHEMA_VERSION
from photo_search.runtime.data_root_lease import acquire_data_root_lease


def _seed(root: Path) -> None:
    with Catalog.open(data_root=root) as catalog:
        with catalog.write() as connection:
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES ('backup_probe', 'preserved')"
            )
    for relative, content in (
        ("vectors/generation/rows.lance", b"vectors"),
        ("artifacts/pending/a.json", b"artifact"),
        ("thumbnails/a.jpg", b"thumbnail"),
        ("runtime/state.json", b"runtime"),
        ("places/datasets/manifest.json", b"places"),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _raise_after_first() -> object:
    seen = False

    def hook(_: Path, __: Path) -> None:
        nonlocal seen
        if seen:
            raise OSError("injected copy failure")
        seen = True

    return hook


def _rewrite_member_digest(bundle: Path, relative: str) -> None:
    manifest_path = bundle / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    candidate = bundle / relative
    for item in manifest["members"]:
        if item["path"] == relative:
            item["size"] = candidate.stat().st_size
            item["sha256"] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def test_non_sqlite_catalog_is_a_structured_create_refusal(tmp_path: Path) -> None:
    source, bundle = tmp_path / "source", tmp_path / "bundle"
    _seed(source)
    (source / "catalog" / "catalog.db").write_bytes(b"not sqlite")
    with pytest.raises(CatalogError) as error:
        create_backup(data_root=source, destination=bundle, stopped=True)
    assert error.value.code == "BACKUP_INVALID"
    assert not bundle.exists()


def test_non_sqlite_catalog_is_a_structured_restore_refusal(tmp_path: Path) -> None:
    source, bundle, target = tmp_path / "source", tmp_path / "bundle", tmp_path / "target"
    _seed(source)
    create_backup(data_root=source, destination=bundle, stopped=True)
    (bundle / "catalog" / "catalog.db").write_bytes(b"not sqlite")
    _rewrite_member_digest(bundle, "catalog/catalog.db")
    with pytest.raises(CatalogError) as error:
        restore_backup(bundle=bundle, data_root=target)
    assert error.value.code == "BACKUP_INVALID"
    assert not target.exists()


def test_cli_non_sqlite_catalog_emits_structured_json(tmp_path: Path) -> None:
    source, bundle = tmp_path / "source", tmp_path / "bundle"
    _seed(source)
    (source / "catalog" / "catalog.db").write_bytes(b"not sqlite")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "photo_search",
            "backup",
            "create",
            "--data-root",
            str(source),
            "--bundle",
            str(bundle),
            "--stopped",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["error"]["code"] == "BACKUP_INVALID"
    assert result.stderr == ""


def test_backup_manifest_and_empty_restore_preserve_all_members(tmp_path: Path) -> None:
    source, bundle, target = tmp_path / "source", tmp_path / "bundle", tmp_path / "restored"
    _seed(source)
    created = create_backup(data_root=source, destination=bundle, stopped=True)
    manifest = json.loads((bundle / MANIFEST_NAME).read_text(encoding="utf-8"))

    assert created["schema_version"] == SUPPORTED_SCHEMA_VERSION
    assert {item["path"] for item in manifest["members"]} >= {
        "catalog/catalog.db",
        "vectors/generation/rows.lance",
        "artifacts/pending/a.json",
        "thumbnails/a.jpg",
        "runtime/state.json",
        "places/datasets/manifest.json",
    }
    assert manifest["excluded"]["source_photos"] is True
    assert manifest["consistency"] == {
        "mode": "stopped",
        "operator_confirmed": True,
        "managed_service_lease": "acquired",
        "snapshot": {
            "generations": [],
            "pending_index_outbox": 0,
            "pending_search_projection": 0,
        },
    }
    restored = restore_backup(bundle=bundle, data_root=target)
    assert restored["schema_version"] == SUPPORTED_SCHEMA_VERSION
    assert (target / "vectors/generation/rows.lance").read_bytes() == b"vectors"
    with Catalog.open(data_root=target) as catalog:
        assert (
            catalog.read_one("SELECT value FROM metadata WHERE key = 'backup_probe'")["value"]
            == "preserved"
        )


def test_backup_refuses_while_a_managed_service_owns_the_data_root(tmp_path: Path) -> None:
    source, bundle = tmp_path / "source", tmp_path / "bundle"
    _seed(source)
    with acquire_data_root_lease(source):
        with pytest.raises(CatalogError) as error:
            create_backup(data_root=source, destination=bundle, stopped=True)
    assert error.value.code == "BACKUP_SERVICE_ACTIVE"
    assert not bundle.exists()

def test_backup_refuses_while_the_http_service_is_running(tmp_path: Path) -> None:
    source, bundle = tmp_path / "source", tmp_path / "bundle"
    _seed(source)
    app = create_app(settings=ApiSettings.create(data_root=source))
    with TestClient(app):
        with pytest.raises(CatalogError) as error:
            create_backup(data_root=source, destination=bundle, stopped=True)
    assert error.value.code == "BACKUP_SERVICE_ACTIVE"
    assert not bundle.exists()

def test_backup_requires_explicit_stopped_confirmation(tmp_path: Path) -> None:
    source, bundle = tmp_path / "source", tmp_path / "bundle"
    _seed(source)
    with pytest.raises(CatalogError) as error:
        create_backup(data_root=source, destination=bundle)
    assert error.value.code == "BACKUP_STOPPED_CONFIRMATION_REQUIRED"
    assert not bundle.exists()


def test_restore_discloses_the_recorded_consistency_declaration(tmp_path: Path) -> None:
    source, bundle, target = tmp_path / "source", tmp_path / "bundle", tmp_path / "target"
    _seed(source)
    create_backup(data_root=source, destination=bundle, stopped=True)
    restored = restore_backup(bundle=bundle, data_root=target)
    consistency = restored["consistency"]
    assert consistency["status"] == "operator_declared"
    assert consistency["declaration"]["operator_confirmed"] is True
    assert "does not prove every possible writer" in consistency["verification_scope"]


def test_restore_marks_a_legacy_bundle_without_consistency_as_unknown(tmp_path: Path) -> None:
    source, bundle, target = tmp_path / "source", tmp_path / "bundle", tmp_path / "target"
    _seed(source)
    create_backup(data_root=source, destination=bundle, stopped=True)
    manifest_path = bundle / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("consistency")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    restored = restore_backup(bundle=bundle, data_root=target)
    assert restored["consistency"] == {
        "status": "unknown",
        "declaration": None,
        "verification_scope": "no valid stopped-service declaration was recorded; writer quiescence is unknown",
    }


def test_restore_marks_an_empty_consistency_object_as_unknown(tmp_path: Path) -> None:
    source, bundle, target = tmp_path / "source", tmp_path / "bundle", tmp_path / "target"
    _seed(source)
    create_backup(data_root=source, destination=bundle, stopped=True)
    manifest_path = bundle / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["consistency"] = {}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    restored = restore_backup(bundle=bundle, data_root=target)
    assert restored["consistency"] == {
        "status": "unknown",
        "declaration": {},
        "verification_scope": "no valid stopped-service declaration was recorded; writer quiescence is unknown",
    }


def test_restore_cli_does_not_claim_an_unrecorded_http_lease(tmp_path: Path) -> None:
    source, bundle, target = tmp_path / "source", tmp_path / "bundle", tmp_path / "target"
    _seed(source)
    create_backup(data_root=source, destination=bundle, stopped=True)
    manifest_path = bundle / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["consistency"] = {"mode": "stopped", "operator_confirmed": True}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "photo_search",
            "backup",
            "restore",
            "--data-root",
            str(target),
            "--bundle",
            str(bundle),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["consistency"]["status"] == "operator_declared"
    assert payload["consistency"]["declaration"] == {"mode": "stopped", "operator_confirmed": True}
    assert "no managed HTTP service lease was recorded" in payload["consistency"]["verification_scope"]

def test_restore_cli_discloses_consistency_declaration(tmp_path: Path) -> None:
    source, bundle, target = tmp_path / "source", tmp_path / "bundle", tmp_path / "target"
    _seed(source)
    create_backup(data_root=source, destination=bundle, stopped=True)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "photo_search",
            "backup",
            "restore",
            "--data-root",
            str(target),
            "--bundle",
            str(bundle),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert result.stderr == ""
    assert payload["consistency"]["status"] == "operator_declared"
    assert payload["consistency"]["declaration"]["operator_confirmed"] is True

def test_backup_failure_leaves_no_published_bundle(tmp_path: Path) -> None:
    source, bundle = tmp_path / "source", tmp_path / "bundle"
    _seed(source)
    with pytest.raises(OSError, match="injected"):
        create_backup(
            data_root=source, destination=bundle, stopped=True, copy_hook=_raise_after_first()
        )
    assert not bundle.exists()
    assert not list(tmp_path.glob(".bundle.backup-staging-*"))


def test_restore_failure_leaves_no_usable_target(tmp_path: Path) -> None:
    source, bundle, target = tmp_path / "source", tmp_path / "bundle", tmp_path / "target"
    _seed(source)
    create_backup(data_root=source, destination=bundle, stopped=True)
    with pytest.raises(OSError, match="injected"):
        restore_backup(bundle=bundle, data_root=target, copy_hook=_raise_after_first())
    assert not target.exists()
    assert not list(tmp_path.glob(".target.restore-staging-*"))


def test_restore_refuses_schema_mismatch_before_writing_target(tmp_path: Path) -> None:
    source, bundle, target = tmp_path / "source", tmp_path / "bundle", tmp_path / "target"
    _seed(source)
    create_backup(data_root=source, destination=bundle, stopped=True)
    path = bundle / MANIFEST_NAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = SUPPORTED_SCHEMA_VERSION + 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CatalogError) as error:
        restore_backup(bundle=bundle, data_root=target)
    assert error.value.code == "BACKUP_SCHEMA_MISMATCH"
    assert not target.exists()


def test_cli_emits_structured_success_and_schema_refusal(tmp_path: Path) -> None:
    source, bundle, target = tmp_path / "source", tmp_path / "bundle", tmp_path / "target"
    _seed(source)
    created = subprocess.run(
        [
            sys.executable,
            "-m",
            "photo_search",
            "backup",
            "create",
            "--data-root",
            str(source),
            "--bundle",
            str(bundle),
            "--stopped",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert created.returncode == 0
    assert json.loads(created.stdout)["status"] == "ok"
    payload = json.loads((bundle / MANIFEST_NAME).read_text(encoding="utf-8"))
    payload["schema_version"] = SUPPORTED_SCHEMA_VERSION - 1
    (bundle / MANIFEST_NAME).write_text(json.dumps(payload), encoding="utf-8")
    restored = subprocess.run(
        [
            sys.executable,
            "-m",
            "photo_search",
            "backup",
            "restore",
            "--data-root",
            str(target),
            "--bundle",
            str(bundle),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert restored.returncode == 1
    assert json.loads(restored.stdout)["error"]["code"] == "BACKUP_SCHEMA_MISMATCH"
    assert not target.exists()


@pytest.mark.parametrize("version", [SUPPORTED_SCHEMA_VERSION - 1, SUPPORTED_SCHEMA_VERSION + 1])
def test_restore_rejects_a_real_lower_or_higher_schema_catalog(
    tmp_path: Path, version: int
) -> None:
    source, bundle, target = tmp_path / "source", tmp_path / "bundle", tmp_path / "target"
    _seed(source)
    create_backup(data_root=source, destination=bundle, stopped=True)
    database = bundle / "catalog" / "catalog.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE metadata SET value = ? WHERE key = 'schema_version'", (str(version),)
    )
    connection.commit()
    connection.close()
    manifest_path = bundle / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = version
    for item in manifest["members"]:
        if item["path"].startswith("catalog/catalog.db"):
            candidate = bundle / item["path"]
            item["size"] = candidate.stat().st_size
            item["sha256"] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(CatalogError) as error:
        restore_backup(bundle=bundle, data_root=target)
    assert error.value.code == "BACKUP_SCHEMA_MISMATCH"
    assert not target.exists()
