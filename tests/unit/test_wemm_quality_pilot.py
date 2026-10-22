from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import tools.evaluation.wemm_quality_pilot as pilot
from tools.evaluation.wemm_quality_pilot import (
    _within,
    model_files_snapshot,
    read_catalog_snapshot,
    sqlite_readonly_uri,
    write_snapshot_exclusive,
)


def _make_catalog(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE active_search_profile(state_id INTEGER, profile_id TEXT);
        CREATE TABLE search_profiles(profile_id TEXT, embedding_processor_id TEXT,
            space_id TEXT, image_generation_id TEXT);
        CREATE TABLE index_generations(generation_id TEXT, processor_id TEXT,
            dimension INTEGER, state TEXT, is_active INTEGER);
        CREATE TABLE assets(asset_id INTEGER);
        CREATE TABLE index_entries(generation_id TEXT);
        INSERT INTO active_search_profile VALUES (1, 'profile-a');
        INSERT INTO search_profiles VALUES ('profile-a', 'qwen-exif-v2', 'space-a', 'gen-a');
        INSERT INTO index_generations VALUES ('gen-a', 'qwen-exif-v2', 512, 'READY', 1);
        INSERT INTO assets VALUES (1), (2);
        INSERT INTO index_entries VALUES ('gen-a');
        """
    )
    connection.commit()
    connection.close()


def test_read_catalog_snapshot_reads_active_binding_without_mutation(tmp_path: Path) -> None:
    database = tmp_path / "catalog.db"
    _make_catalog(database)

    result = read_catalog_snapshot(database)

    assert result == {
        "available": True,
        "processor_id": "qwen-exif-v2",
        "space_id": "space-a",
        "generation_id": "gen-a",
        "generation_processor_id": "qwen-exif-v2",
        "dimension": 512,
        "generation_state": "READY",
        "generation_active": True,
        "binding_consistent": True,
        "asset_count": 2,
        "generation_vector_count": 1,
        "sqlite_files_unchanged_during_read": True,
    }
    with sqlite3.connect(sqlite_readonly_uri(database), uri=True) as connection:
        assert connection.execute("PRAGMA query_only").fetchone() == (0,)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("INSERT INTO assets VALUES (3)")
    check = sqlite3.connect(database)
    assert check.execute("SELECT count(*) FROM assets").fetchone() == (2,)
    check.close()


def test_readonly_uri_does_not_disable_wal_snapshot_semantics(tmp_path: Path) -> None:
    uri = sqlite_readonly_uri(tmp_path / "catalog with space.db")
    assert "mode=ro" in uri
    assert "immutable=" not in uri
    assert "catalog%20with%20space.db" in uri


def test_active_wal_snapshot_is_visible_and_missing_shm_is_refused(tmp_path: Path) -> None:
    database = tmp_path / "catalog.db"
    _make_catalog(database)
    writer = sqlite3.connect(database)
    assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    writer.execute("INSERT INTO assets VALUES (3)")
    writer.commit()
    assert Path(f"{database}-wal").exists()
    assert Path(f"{database}-shm").exists()

    result = read_catalog_snapshot(database)

    assert result["available"] is True
    assert result["asset_count"] == 3
    assert result["sqlite_files_unchanged_during_read"] is True
    assert "immutable=1" in sqlite_readonly_uri(database, immutable=True)
    writer.close()

    (tmp_path / "catalog.db-wal").write_bytes(b"untrusted incomplete WAL")
    (tmp_path / "catalog.db-shm").unlink(missing_ok=True)
    with pytest.raises(RuntimeError, match="no SHM sidecar"):
        read_catalog_snapshot(database)


def test_model_file_snapshot_only_stats_required_top_level_files(tmp_path: Path) -> None:
    (tmp_path / "model.safetensors").write_bytes(b"weights")
    result = model_files_snapshot(tmp_path, ("model.safetensors", "config.json"))
    assert result == {
        "named_files_present": False,
        "integrity_checked": False,
        "runtime_tested": False,
        "files": {
            "model.safetensors": {"present": True, "bytes": 7},
            "config.json": {"present": False, "bytes": 0},
        },
    }


def test_output_path_must_remain_under_project_pilot_directory(tmp_path: Path) -> None:
    allowed = tmp_path / ".tmp" / "wemm-pilot"
    assert _within(allowed / "nested", allowed)
    assert not _within(tmp_path / "catalog", allowed)


def test_snapshot_write_is_exclusive_and_preserves_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pilot, "REPO_ROOT", tmp_path)
    output_dir = tmp_path / ".tmp" / "wemm-pilot" / "run-a"
    output_dir.mkdir(parents=True)
    output = output_dir / "preflight.json"
    output.write_text("preserve", encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_snapshot_exclusive(output_dir, {"replacement": True})

    assert output.read_text(encoding="utf-8") == "preserve"


def test_snapshot_write_rejects_reparse_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pilot, "REPO_ROOT", tmp_path)
    output_dir = tmp_path / ".tmp" / "wemm-pilot" / "run-b"
    monkeypatch.setattr(pilot, "_has_reparse_component", lambda _: True)

    with pytest.raises(ValueError, match="symlink or junction"):
        write_snapshot_exclusive(output_dir, {"unsafe": True})


def test_snapshot_write_rejects_reparse_leaf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pilot, "REPO_ROOT", tmp_path)
    output_dir = tmp_path / ".tmp" / "wemm-pilot" / "run-c"
    monkeypatch.setattr(pilot, "_has_reparse_component", lambda path: path.name == "preflight.json")

    with pytest.raises(ValueError, match="output must not be a symlink or junction"):
        write_snapshot_exclusive(output_dir, {"unsafe": True})
