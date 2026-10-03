"""P1-03: sharded cache layout, atomic publication and failure records."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from photo_search.catalog.errors import ARTIFACT_WRITE_FAILED, CatalogError
from photo_search.media import cache as cache_module
from photo_search.media.cache import MediaCache, require_sha256
from photo_search.media.contract import PREVIEW_SPEC, THUMBNAIL_SPEC

SHA = "0123456789abcdef" * 4


def make_cache(tmp_path: Path) -> MediaCache:
    return MediaCache(
        thumbnails_root=tmp_path / "data" / "thumbnails",
        previews_root=tmp_path / "data" / "previews",
        failures_root=tmp_path / "data" / "cache" / "media",
    )


def test_paths_are_two_level_sharded_versioned_and_under_the_data_root(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)

    thumbnail = cache.artifact_path(spec=THUMBNAIL_SPEC, sha256=SHA)
    preview = cache.artifact_path(spec=PREVIEW_SPEC, sha256=SHA)

    assert thumbnail == (
        tmp_path
        / "data"
        / "thumbnails"
        / "catalog-thumbnail@1"
        / SHA[:2]
        / SHA[2:4]
        / f"{SHA}.jpg"
    )
    assert preview.parent.parent.parent.name == "catalog-preview@1"
    assert preview != thumbnail
    assert cache.failure_path(spec=THUMBNAIL_SPEC, sha256=SHA).name == f"{SHA}.json"
    assert cache.failure_path(spec=THUMBNAIL_SPEC, sha256=SHA).parent.name == SHA[2:4]


def test_sha256_is_validated_before_it_is_used_as_a_file_name(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    for bad in ("", "abc", "z" * 64, "A" * 64, SHA + "a"):
        with pytest.raises(ValueError):
            require_sha256(bad)
        with pytest.raises(ValueError):
            cache.artifact_path(spec=THUMBNAIL_SPEC, sha256=bad)


def test_publish_is_atomic_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    target = cache.artifact_path(spec=THUMBNAIL_SPEC, sha256=SHA)

    result = cache.publish(target, b"first-payload")

    assert result.path == target
    assert result.bytes_written == len(b"first-payload")
    assert result.replaced_existing is False
    assert target.read_bytes() == b"first-payload"
    assert list(target.parent.glob("*.tmp")) == []
    assert cache.is_published(target) is True

    second = cache.publish(target, b"replacement")
    assert second.replaced_existing is True
    assert target.read_bytes() == b"replacement"


def test_a_failed_replace_never_creates_a_ready_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = make_cache(tmp_path)
    target = cache.artifact_path(spec=THUMBNAIL_SPEC, sha256=SHA)

    def refuse_replace(source: object, destination: object) -> None:
        raise OSError("simulated crash before the atomic replace")

    monkeypatch.setattr(cache_module.os, "replace", refuse_replace)
    with pytest.raises(CatalogError) as error:
        cache.publish(target, b"half-written")

    assert error.value.code == ARTIFACT_WRITE_FAILED
    assert not target.exists()
    assert cache.is_published(target) is False
    assert list(target.parent.glob("*.tmp")) == []


def test_is_published_rejects_a_missing_or_empty_file(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    target = cache.artifact_path(spec=THUMBNAIL_SPEC, sha256=SHA)

    assert cache.is_published(target) is False

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"")
    assert cache.is_published(target) is False

    target.write_bytes(b"x")
    assert cache.is_published(target) is True


def test_failure_records_round_trip_and_are_cleared_on_success(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    record = {"code": "DECODE_FAILED", "message": "truncated", "input_size": 10}

    path = cache.write_failure(spec=THUMBNAIL_SPEC, sha256=SHA, record=record)
    assert path.name == f"{SHA}.json"
    assert cache.read_failure(spec=THUMBNAIL_SPEC, sha256=SHA) == record
    assert cache.read_failure(spec=PREVIEW_SPEC, sha256=SHA) is None

    assert cache.clear_failure(spec=THUMBNAIL_SPEC, sha256=SHA) is True
    assert cache.read_failure(spec=THUMBNAIL_SPEC, sha256=SHA) is None
    assert cache.clear_failure(spec=THUMBNAIL_SPEC, sha256=SHA) is False


def test_a_corrupt_failure_record_is_ignored_instead_of_crashing(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    path = cache.failure_path(spec=THUMBNAIL_SPEC, sha256=SHA)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert cache.read_failure(spec=THUMBNAIL_SPEC, sha256=SHA) is None


def test_temp_files_can_be_listed_and_pruned_by_age(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    target = cache.artifact_path(spec=THUMBNAIL_SPEC, sha256=SHA)
    target.parent.mkdir(parents=True, exist_ok=True)
    stale = target.parent / f"{SHA}.{os.getpid()}-deadbeef.tmp"
    fresh = target.parent / f"{SHA}.{os.getpid()}-feedface.tmp"
    stale.write_bytes(b"partial")
    fresh.write_bytes(b"partial")
    os.utime(stale, (1_000, 1_000))

    assert sorted(path.name for path in cache.iter_temp_files(spec=THUMBNAIL_SPEC)) == sorted(
        [stale.name, fresh.name]
    )

    removed = cache.prune_temp_files(
        spec=THUMBNAIL_SPEC, min_age_ms=60_000, now_ms=2_000_000
    )

    assert [path.name for path in removed] == [stale.name]
    assert not stale.exists()
    assert fresh.exists()


def test_versions_and_inventory_keep_an_older_processing_version_visible(
    tmp_path: Path,
) -> None:
    cache = make_cache(tmp_path)
    older = (
        tmp_path
        / "data"
        / "thumbnails"
        / "catalog-thumbnail@0"
        / SHA[:2]
        / SHA[2:4]
        / f"{SHA}.jpg"
    )
    older.parent.mkdir(parents=True, exist_ok=True)
    older.write_bytes(b"old-version-bytes")
    current = cache.artifact_path(spec=THUMBNAIL_SPEC, sha256=SHA)
    cache.publish(current, b"current-bytes")
    leftover = current.parent / f"{SHA}.123-abc.tmp"
    leftover.write_bytes(b"partial")

    assert cache.version_dirs(kind="thumbnail") == ["catalog-thumbnail@0", "catalog-thumbnail@1"]
    assert older.read_bytes() == b"old-version-bytes"
    inventory = cache.cache_inventory(spec=THUMBNAIL_SPEC)
    assert inventory == {
        "published_files": 1,
        "published_bytes": len(b"current-bytes"),
        "temp_files": 1,
    }
