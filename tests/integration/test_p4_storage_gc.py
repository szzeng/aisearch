"""P4-STORAGE-GC-A: bounded accounting and safe derived-file cleanup.

Every path in these tests is a synthetic data root under pytest's isolated tmp directory.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from photo_search import storage as storage_module
from photo_search.config import catalog_db_path
from photo_search.media.cache import MediaCache
from photo_search.media.contract import PREVIEW_SPEC, THUMBNAIL_SPEC
from photo_search.runtime.data_root_lease import acquire_data_root_lease
from photo_search.storage import (
    quick_storage_summary,
    storage_clean,
    storage_clean_retention,
    storage_clean_retention_artifact_metadata,
    storage_clean_retention_media,
    storage_inspect,
)


def _make_artifact(root: Path, *, age_ms: int, suffix: str = ".json", digest: str = "a" * 64) -> tuple[Path, str]:
    processor = "1" * 16
    path = root / "artifacts" / "pending" / processor / digest[:2] / f"{digest}-{processor}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"artifact-payload")
    moment_ns = (time.time_ns() // 1_000_000 - age_ms) * 1_000_000
    os.utime(path, ns=(moment_ns, moment_ns))
    return path, path.relative_to(root).as_posix()


def _catalog(root: Path, refs: tuple[str, ...] = ()) -> None:
    path = catalog_db_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE artifacts(storage_uri TEXT, artifact_id TEXT, lifecycle_state TEXT)")
        connection.executemany(
            "INSERT INTO artifacts(storage_uri, artifact_id, lifecycle_state) VALUES (?, ?, ?)",
            ((uri, f"artifact-{index}", "PUBLISHED" if index == 0 else "RECLAIMED") for index, uri in enumerate(refs)),
        )


def test_a1_artifact_reference_and_age_rules_preserve_all_metadata_states(tmp_path: Path) -> None:
    root = tmp_path / "data"
    published, published_uri = _make_artifact(root, age_ms=10 * 24 * 60 * 60 * 1000, digest="a" * 64)
    reclaimed, reclaimed_uri = _make_artifact(root, age_ms=10 * 24 * 60 * 60 * 1000, suffix=".bin", digest="b" * 64)
    orphan, orphan_uri = _make_artifact(root, age_ms=10 * 24 * 60 * 60 * 1000, digest="c" * 64)
    temp_old, _ = _make_artifact(root, age_ms=2 * 24 * 60 * 60 * 1000, suffix=".json-44-abcdef123456.tmp", digest="d" * 64)
    temp_new, _ = _make_artifact(root, age_ms=1000, suffix=".json-45-fedcba654321.tmp", digest="e" * 64)
    _catalog(root, (published_uri.replace("/", "\\").upper(), reclaimed_uri))
    with sqlite3.connect(catalog_db_path(root)) as connection:
        connection.execute("CREATE TABLE index_outbox(artifact_id TEXT, state TEXT)")
        connection.execute("INSERT INTO index_outbox VALUES ('artifact-1', 'PENDING')")
        before = connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]

    result = storage_clean(root, scopes=("artifacts",), apply=True, stopped=True)

    assert result["status"] == "ok"
    assert published.exists() and reclaimed.exists()
    assert not orphan.exists() and not temp_old.exists() and temp_new.exists()
    with sqlite3.connect(catalog_db_path(root)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == before
        assert connection.execute("SELECT state FROM index_outbox").fetchone()[0] == "PENDING"


@pytest.mark.parametrize(
    ("filename_processor", "temporary"),
    (("2" * 16, False), ("3" * 16, True)),
)
def test_mismatched_artifact_processor_directory_is_never_gc_candidate(
    tmp_path: Path, filename_processor: str, temporary: bool
) -> None:
    root = tmp_path / "data"
    directory_processor = "1" * 16
    digest = "f" * 64
    suffix = ".json-12-abcdef123456.tmp" if temporary else ".json"
    path = (
        root
        / "artifacts"
        / "pending"
        / directory_processor
        / digest[:2]
        / f"{digest}-{filename_processor}{suffix}"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"mismatched processor")
    old = time.time_ns() - 10 * 24 * 60 * 60 * 1_000_000_000
    os.utime(path, ns=(old, old))
    _catalog(root)

    report = storage_inspect(root, deep=True)
    result = storage_clean(root, scopes=("artifacts",), apply=True, stopped=True)

    assert report["candidate_files"] == 0
    assert result["deleted_files"] == 0
    assert path.exists()


def test_a2_offline_source_and_authoritative_rows_are_never_gc_targets(tmp_path: Path) -> None:
    root = tmp_path / "data"
    source = tmp_path / "offline-library" / "original.jpg"
    source.parent.mkdir()
    source.write_bytes(b"source-original")
    _catalog(root)
    with sqlite3.connect(catalog_db_path(root)) as connection:
        connection.execute("CREATE TABLE source_roots(source_id INTEGER, status TEXT)")
        connection.execute("CREATE TABLE file_locations(location_id INTEGER, source_id INTEGER, asset_id INTEGER, availability TEXT)")
        connection.execute("CREATE TABLE persons(person_id INTEGER, name TEXT)")
        connection.execute("CREATE TABLE places(place_id INTEGER, label TEXT)")
        connection.execute("CREATE TABLE person_collections(collection_id INTEGER, name TEXT)")
        connection.execute("CREATE TABLE ocr_facts(asset_id INTEGER, text TEXT)")
        connection.execute("CREATE TABLE face_person_links(face_id INTEGER, person_id INTEGER, decision TEXT)")
        connection.executemany("INSERT INTO source_roots VALUES (?, ?)", ((7, "OFFLINE"), (8, "ONLINE")))
        connection.executemany("INSERT INTO file_locations VALUES (?, ?, 9, ?)", ((1, 7, "MISSING"), (2, 8, "PRESENT")))
        connection.execute("INSERT INTO persons VALUES (3, '人工人物')")
        connection.execute("INSERT INTO places VALUES (4, '人工地点')")
        connection.execute("INSERT INTO person_collections VALUES (5, '合集')")
        connection.execute("INSERT INTO ocr_facts VALUES (9, 'OCR 内容')")
        connection.execute("INSERT INTO face_person_links VALUES (12, 3, 'MANUAL')")
    tables_before = sqlite3.connect(catalog_db_path(root)).execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()

    result = storage_clean(root, scopes=("artifacts", "media-cache"), apply=True, stopped=True)

    with sqlite3.connect(catalog_db_path(root)) as connection:
        assert connection.execute("SELECT availability FROM file_locations").fetchone()[0] == "MISSING"
        assert connection.execute("SELECT COUNT(DISTINCT asset_id) FROM file_locations").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM file_locations").fetchone()[0] == 2
        assert connection.execute("SELECT name FROM persons").fetchone()[0] == "人工人物"
        assert connection.execute("SELECT label FROM places").fetchone()[0] == "人工地点"
        assert connection.execute("SELECT name FROM person_collections").fetchone()[0] == "合集"
        assert connection.execute("SELECT text FROM ocr_facts").fetchone()[0] == "OCR 内容"
        assert connection.execute("SELECT decision FROM face_person_links").fetchone()[0] == "MANUAL"
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall() == tables_before
    assert source.read_bytes() == b"source-original"
    assert result["deleted_files"] == 0


def test_a3_physical_and_logical_accounting_and_media_cache_scopes(tmp_path: Path, api_env) -> None:
    root = tmp_path / "data"
    current_preview = root / "previews" / PREVIEW_SPEC.processor_id / "aa" / "aa" / ("a" * 64 + ".jpg")
    current_thumb = root / "thumbnails" / THUMBNAIL_SPEC.processor_id / "bb" / "bb" / ("b" * 64 + ".jpg")
    old_preview = root / "previews" / "catalog-preview@0" / "cc" / "cc" / ("c" * 64 + ".jpg")
    face_crop = root / "cache" / "face-crops" / "face-crop@2" / "dd" / "dd" / ("d" * 64 + ".jpg")
    for path in (current_preview, current_thumb, old_preview, face_crop):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"cache-content")
    _catalog(root)
    with sqlite3.connect(catalog_db_path(root)) as connection:
        connection.execute("CREATE TABLE face_embeddings(vector_blob BLOB)")
        connection.execute("CREATE TABLE face_candidates(vector_blob BLOB)")
        connection.execute("INSERT INTO face_embeddings VALUES (?)", (b"x" * 128,))
        connection.execute("INSERT INTO face_candidates VALUES (?)", (b"y" * 256,))
    quick = quick_storage_summary(root)
    assert quick["catalog"]["physical_bytes"] == catalog_db_path(root).stat().st_size
    assert quick["catalog"]["logical_blob_breakdown"]["face_embeddings"]["logical_blob_bytes"] == 128
    assert quick["catalog"]["logical_blob_breakdown"]["face_candidates"]["logical_blob_bytes"] == 256

    report = storage_inspect(root, deep=True)
    assert report["total_bytes"] == sum(path.stat().st_size for path in (current_preview, current_thumb, old_preview, face_crop))
    assert report["directories"]["previews"]["safe_cleanup_files"] == 1
    clean = storage_clean(root, scopes=("media-cache", "face-crops"), apply=True, stopped=True)
    assert clean["status"] == "ok" and not old_preview.exists() and not face_crop.exists()
    assert current_preview.exists() and current_thumb.exists()
    explicit = storage_clean(root, scopes=("previews",), apply=True, stopped=True)
    assert explicit["deleted_files"] == 1 and not current_preview.exists() and current_thumb.exists()
    MediaCache.open(root).publish(current_preview, b"regenerated preview")
    assert current_preview.read_bytes() == b"regenerated preview"

    async def read_summary(client):
        await api_env.authenticate(client)
        response = await client.get("/api/v1/storage/summary")
        assert response.status_code == 200
        assert str(api_env.data_root) not in response.text
        return response.json()

    assert api_env.scenario(read_summary)["status"] == "ok"


def test_retention_media_gc_deletes_only_persisted_cache_identities_and_records_bytes(tmp_path: Path) -> None:
    root = tmp_path / "data"
    digest = "e" * 64
    crop_key = "f" * 64
    targeted = [
        root / "thumbnails" / THUMBNAIL_SPEC.processor_id / digest[:2] / digest[2:4] / f"{digest}.jpg",
        root / "previews" / PREVIEW_SPEC.processor_id / digest[:2] / digest[2:4] / f"{digest}.jpg",
        root / "cache" / "face-crops" / "face-crop@2" / crop_key[:2] / crop_key[2:4] / f"{crop_key}.jpg",
    ]
    unrelated = root / "previews" / PREVIEW_SPEC.processor_id / "aa" / "bb" / ("a" * 64 + ".jpg")
    for path in [*targeted, unrelated]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"retention-cache")
    expected_bytes = sum(path.stat().st_size for path in targeted)
    _catalog(root)
    with sqlite3.connect(catalog_db_path(root)) as connection:
        connection.execute("CREATE TABLE assets(asset_id INTEGER PRIMARY KEY, sha256 TEXT)")
        connection.execute(
            "CREATE TABLE asset_retention_barriers("
            "asset_id INTEGER PRIMARY KEY,state TEXT,media_sha256 TEXT,face_crop_keys_json TEXT,"
            "media_cleaned_at_ms INTEGER,artifact_metadata_cleaned_at_ms INTEGER,"
            "media_deleted_files INTEGER DEFAULT 0,media_freed_bytes INTEGER DEFAULT 0)"
        )
        connection.execute(
            "INSERT INTO asset_retention_barriers(asset_id,state,media_sha256,face_crop_keys_json,artifact_metadata_cleaned_at_ms) "
            "VALUES(1,'COMPLETE',?,?,1)",
            (digest, json.dumps([crop_key])),
        )
        connection.execute(
            "INSERT INTO asset_retention_barriers(asset_id,state,media_sha256,face_crop_keys_json,artifact_metadata_cleaned_at_ms) "
            "VALUES(2,'COMPLETE',?, '[]',1)",
            ("d" * 64,),
        )

    preview = storage_clean_retention_media(root, apply=False)
    assert preview["status"] == "preview" and preview["eligible_files"] == 3
    assert all(path.exists() for path in [*targeted, unrelated])
    denied = storage_clean_retention_media(root, apply=True, stopped=False)
    assert denied["code"] == "STOPPED_CONFIRMATION_REQUIRED"

    result = storage_clean_retention_media(root, apply=True, stopped=True, now_ms=1234)

    assert result["status"] == "ok" and result["deleted_files"] == 3
    assert result["freed_bytes"] == expected_bytes
    assert all(not path.exists() for path in targeted)
    assert unrelated.exists()
    with sqlite3.connect(catalog_db_path(root)) as connection:
        done = connection.execute(
            "SELECT media_cleaned_at_ms,media_deleted_files,media_freed_bytes "
            "FROM asset_retention_barriers WHERE asset_id=1"
        ).fetchone()
        assert done == (1234, 3, result["freed_bytes"])
        assert connection.execute(
            "SELECT media_cleaned_at_ms FROM asset_retention_barriers WHERE asset_id=2"
        ).fetchone()[0] == 1234


def test_retention_media_gc_keeps_barrier_pending_when_file_identity_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data"
    digest = "9" * 64
    path = root / "previews" / PREVIEW_SPEC.processor_id / digest[:2] / digest[2:4] / f"{digest}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"new file")
    _catalog(root)
    with sqlite3.connect(catalog_db_path(root)) as connection:
        connection.execute("CREATE TABLE assets(asset_id INTEGER PRIMARY KEY, sha256 TEXT)")
        connection.execute(
            "CREATE TABLE asset_retention_barriers("
            "asset_id INTEGER PRIMARY KEY,state TEXT,media_sha256 TEXT,face_crop_keys_json TEXT,"
            "media_cleaned_at_ms INTEGER,artifact_metadata_cleaned_at_ms INTEGER,"
            "media_deleted_files INTEGER DEFAULT 0,media_freed_bytes INTEGER DEFAULT 0)"
        )
        connection.execute(
            "INSERT INTO asset_retention_barriers(asset_id,state,media_sha256,face_crop_keys_json,artifact_metadata_cleaned_at_ms) "
            "VALUES(1,'COMPLETE',?,'[]',1)",
            (digest,),
        )
    monkeypatch.setattr(storage_module, "_identity_matches", lambda *_args: False)

    result = storage_clean_retention_media(root, apply=True, stopped=True)

    assert result["deleted_files"] == 0 and result["partial"] is True
    assert path.exists()
    with sqlite3.connect(catalog_db_path(root)) as connection:
        assert connection.execute(
            "SELECT media_cleaned_at_ms FROM asset_retention_barriers WHERE asset_id=1"
        ).fetchone()[0] is None


def test_retention_artifact_metadata_gc_removes_only_unreferenced_terminal_work(tmp_path: Path) -> None:
    root = tmp_path / "data"
    path = catalog_db_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            "CREATE TABLE assets(asset_id INTEGER PRIMARY KEY, sha256 TEXT);"
            "CREATE TABLE asset_retention_barriers(asset_id INTEGER PRIMARY KEY,state TEXT,media_sha256 TEXT,"
            "artifact_metadata_cleaned_at_ms INTEGER,artifact_rows_deleted INTEGER DEFAULT 0,jobs_deleted INTEGER DEFAULT 0);"
            "CREATE TABLE jobs(job_id INTEGER PRIMARY KEY,subject_type TEXT,subject_id TEXT,state TEXT);"
            "CREATE TABLE artifacts(artifact_id TEXT PRIMARY KEY,job_id INTEGER REFERENCES jobs(job_id) ON DELETE RESTRICT);"
            "CREATE TABLE index_entries(artifact_id TEXT);"
            "CREATE TABLE face_detail_coordinate_frames(artifact_id TEXT PRIMARY KEY);"
        )
        connection.executemany(
            "INSERT INTO asset_retention_barriers(asset_id,state,media_sha256) VALUES(?,'COMPLETE',?)",
            ((1, "1" * 64), (2, "2" * 64), (3, "3" * 64), (4, "4" * 64)),
        )
        connection.executemany(
            "INSERT INTO jobs(job_id,subject_type,subject_id,state) VALUES(?, 'asset', ?, ?)",
            ((11, "1", "CANCELLED"), (22, "2", "SUCCEEDED"), (33, "3", "RUNNING"),
             (44, "4", "BLOCKED")),
        )
        connection.executemany(
            "INSERT INTO artifacts(artifact_id,job_id) VALUES(?,?)",
            (("artifact-safe", 11), ("artifact-referenced", 22), ("artifact-running", 33),
             ("artifact-blocked", 44)),
        )
        connection.execute("INSERT INTO index_entries VALUES ('artifact-referenced')")
        connection.execute("INSERT INTO face_detail_coordinate_frames VALUES ('artifact-safe')")
    preview = storage_clean_retention_artifact_metadata(root, apply=False)
    assert preview == {"status": "preview", "eligible_assets": 4, "batch_assets": 20}

    result = storage_clean_retention_artifact_metadata(root, apply=True, stopped=True, now_ms=5678)

    assert result["status"] == "ok" and result["completed_assets"] == 1
    assert result["blocked_assets"] == 3 and result["deleted_artifacts"] == 1 and result["deleted_jobs"] == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifacts WHERE artifact_id='artifact-safe'").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM jobs WHERE job_id=11").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM face_detail_coordinate_frames").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM artifacts WHERE artifact_id='artifact-referenced'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM jobs WHERE job_id=33").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM jobs WHERE job_id=44").fetchone()[0] == 1
        assert connection.execute(
            "SELECT artifact_metadata_cleaned_at_ms,artifact_rows_deleted,jobs_deleted "
            "FROM asset_retention_barriers WHERE asset_id=1"
        ).fetchone() == (5678, 1, 1)
        assert connection.execute(
            "SELECT artifact_metadata_cleaned_at_ms FROM asset_retention_barriers WHERE asset_id=2"
        ).fetchone()[0] is None
        connection.executescript(
            "CREATE TABLE future_job_recovery(job_id INTEGER);"
            "INSERT INTO jobs VALUES(55,'asset','5','CANCELLED');"
            "INSERT INTO artifacts VALUES('artifact-unknown-job-ref',55);"
            "INSERT INTO asset_retention_barriers(asset_id,state,media_sha256) VALUES(5,'COMPLETE','5555555555555555555555555555555555555555555555555555555555555555');"
            "INSERT INTO future_job_recovery VALUES(55);"
        )
    unknown_result = storage_clean_retention_artifact_metadata(root, apply=True, stopped=True, now_ms=6789)
    assert unknown_result["blocked_assets"] == 4 and unknown_result["completed_assets"] == 0
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs WHERE job_id=55").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM future_job_recovery WHERE job_id=55").fetchone()[0] == 1


def test_retention_clean_runs_terminal_metadata_then_existing_artifact_gc(tmp_path: Path) -> None:
    root = tmp_path / "data"
    digest = "a" * 64
    processor = "1" * 16
    relative_uri = f"artifacts/pending/{processor}/{digest[:2]}/{digest}-{processor}.json"
    artifact_path = root / relative_uri
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(b"retention-owned-artifact")
    old_ns = time.time_ns() - 10 * 24 * 60 * 60 * 1_000_000_000
    os.utime(artifact_path, ns=(old_ns, old_ns))
    path = catalog_db_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            "CREATE TABLE assets(asset_id INTEGER PRIMARY KEY, sha256 TEXT);"
            "CREATE TABLE asset_retention_barriers("
            "asset_id INTEGER PRIMARY KEY,state TEXT,media_sha256 TEXT,face_crop_keys_json TEXT DEFAULT '[]',"
            "media_cleaned_at_ms INTEGER,artifact_metadata_cleaned_at_ms INTEGER,"
            "media_deleted_files INTEGER DEFAULT 0,media_freed_bytes INTEGER DEFAULT 0,"
            "artifact_rows_deleted INTEGER DEFAULT 0,jobs_deleted INTEGER DEFAULT 0);"
            "CREATE TABLE jobs(job_id INTEGER PRIMARY KEY,subject_type TEXT,subject_id TEXT,state TEXT);"
            "CREATE TABLE artifacts(artifact_id TEXT PRIMARY KEY,job_id INTEGER REFERENCES jobs(job_id) ON DELETE RESTRICT,"
            "storage_uri TEXT,lifecycle_state TEXT);"
            "CREATE TABLE index_outbox(event_id TEXT,artifact_id TEXT,state TEXT);"
            "CREATE TABLE index_entries(artifact_id TEXT);"
        )
        connection.execute(
            "INSERT INTO asset_retention_barriers(asset_id,state,media_sha256) VALUES(1,'COMPLETE',?)",
            (digest,),
        )
        connection.execute("INSERT INTO jobs VALUES(11,'asset','1','CANCELLED')")
        connection.execute(
            "INSERT INTO artifacts VALUES('artifact-1',11,?,'PUBLISHED')", (relative_uri,)
        )

    stamp = time.time_ns() // 1_000_000
    result = storage_clean_retention(root, apply=True, stopped=True, now_ms=stamp)

    assert result["status"] == "ok" and result["deleted_files"] == 1
    assert result["freed_bytes"] == len(b"retention-owned-artifact")
    assert not artifact_path.exists()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        barrier = connection.execute(
            "SELECT artifact_metadata_cleaned_at_ms,media_cleaned_at_ms,artifact_rows_deleted,jobs_deleted "
            "FROM asset_retention_barriers WHERE asset_id=1"
        ).fetchone()
        assert barrier == (stamp, stamp, 1, 1)


def test_a4_lease_identity_recheck_and_failed_delete_are_retryable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "data"
    candidate, _ = _make_artifact(root, age_ms=10 * 24 * 60 * 60 * 1000)
    _catalog(root)
    scan_lock = storage_module._try_scan_lock(root)
    assert scan_lock is not None
    scan_busy = storage_clean(root, scopes=("artifacts",), apply=True, stopped=True)
    assert scan_busy["status"] == "error" and scan_busy["code"] == "STORAGE_SCAN_BUSY"
    storage_module._release_scan_lock(scan_lock)
    lease = acquire_data_root_lease(root)
    try:
        busy = storage_clean(root, scopes=("artifacts",), apply=True, stopped=True)
        assert busy["status"] == "error" and busy["code"] == "DATA_ROOT_BUSY"
        assert candidate.exists()
    finally:
        lease.close()

    original_identity_check = storage_module._identity_matches
    monkeypatch.setattr(storage_module, "_identity_matches", lambda *_args: False)
    changed = storage_clean(root, scopes=("artifacts",), apply=True, stopped=True)
    assert changed["skipped_changed_or_referenced"] == 1 and candidate.exists()
    monkeypatch.setattr(storage_module, "_identity_matches", original_identity_check)

    original_unlink = Path.unlink
    def fail_once(path: Path, *args: object, **kwargs: object) -> None:
        if path == candidate:
            raise PermissionError("synthetic delete denial")
        original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_once)
    failed = storage_clean(root, scopes=("artifacts",), apply=True, stopped=True)
    assert failed["failed_files"] == 1 and candidate.exists()
    monkeypatch.setattr(Path, "unlink", original_unlink)
    retried = storage_clean(root, scopes=("artifacts",), apply=True, stopped=True)
    assert retried["deleted_files"] == 1 and not candidate.exists()

    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"never follow links")
    link = root / "artifacts" / "pending" / ("1" * 16) / "aa" / "junction-placeholder"
    original_walk = storage_module._walk
    def walk_with_reparse(root_path: Path, **kwargs: object):
        yield from original_walk(root_path, **kwargs)
        if root_path == root / "artifacts":
            yield link, "skip_symlink"
    monkeypatch.setattr(storage_module, "_walk", walk_with_reparse)
    report = storage_inspect(root, deep=True)
    assert report["partial"] and report["directories"]["artifacts"]["skip_reasons"]["skip_symlink"] == 1
    assert outside.read_bytes() == b"never follow links"

    no_catalog_root = tmp_path / "no-catalog"
    _make_artifact(no_catalog_root, age_ms=10 * 24 * 60 * 60 * 1000)
    uncertain = storage_inspect(no_catalog_root, deep=True)
    assert uncertain["partial"] and uncertain["candidate_files"] == 0
    assert uncertain["directories"]["artifacts"]["needs_verification_files"] == 1
