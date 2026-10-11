from __future__ import annotations

import runpy
from pathlib import Path

from p2_search_fixtures import build_library, write_jpeg

from photo_search.api.queries import _asset_chunk
from photo_search.catalog.video import is_video_path
from photo_search.ingestion.scanner import (
    SCAN_MODE_FAST,
    ScanInterrupted,
    execute_scan,
    iter_observations,
)
from photo_search.search.retrieval import SearchScope, scope_condition


def test_video_extensions_are_skipped_by_fast_scan_and_old_video_reconciles(tmp_path: Path) -> None:
    library = build_library(tmp_path, ("photo.jpg",))
    try:
        video = write_jpeg(library.root / "new.MOV", index=4)
        before = (video.read_bytes(), video.stat().st_size, video.stat().st_mtime_ns)
        assert {item.relative_path for item in iter_observations(library.root)} == {"photo.jpg"}
        assert is_video_path(r"folder\old.Mp4")
        assert not is_video_path("directory.mp4/photo.jpg")
        execute_scan(library.service, library.source_id, mode=SCAN_MODE_FAST)
        assert library.catalog.read_one(
            "SELECT 1 FROM file_locations WHERE relative_path='new.MOV'"
        ) is None

        photo_id = library.assets["photo.jpg"]
        # Model a previously registered video position bound to the existing asset.
        with library.catalog.write() as connection:
            connection.execute(
                "UPDATE file_locations SET relative_path='old.mp4', path_key='old.mp4' "
                "WHERE asset_id=?", (photo_id,)
            )
        legacy = library.root / "old.mp4"
        (library.root / "photo.jpg").rename(legacy)
        write_jpeg(library.root / "other.jpg", index=8)
        try:
            execute_scan(
                library.service, library.source_id, mode=SCAN_MODE_FAST, interrupt_after=0
            )
        except ScanInterrupted:
            pass
        else:
            raise AssertionError("injected scan interruption should not reconcile missing paths")
        assert library.catalog.read_one(
            "SELECT availability FROM file_locations WHERE relative_path='old.mp4'"
        )["availability"] == "PRESENT"
        library.clock.advance(61_000)
        first = execute_scan(library.service, library.source_id, mode=SCAN_MODE_FAST)
        location = library.catalog.read_one(
            "SELECT availability FROM file_locations WHERE relative_path='old.mp4'"
        )
        assert first.state == "SUCCEEDED"
        assert location["availability"] == "MISSING"
        second = execute_scan(library.service, library.source_id, mode=SCAN_MODE_FAST)
        assert second.state == "SUCCEEDED"
        assert library.catalog.read_one(
            "SELECT availability FROM file_locations WHERE relative_path='old.mp4'"
        )["availability"] == "MISSING"
        assert (video.read_bytes(), video.stat().st_size, video.stat().st_mtime_ns) == before
        diagnostic = runpy.run_path(
            str(Path(__file__).resolve().parents[2] / "tools/diagnostics/video_asset_probe.py")
        )
        report = diagnostic["probe"](library.catalog.db_path)
        source = report["sources"][0]
        assert source["locations"] == 1
        assert source["missing"] == 1
        assert source["video_only_assets"] == 1
    finally:
        library.close()


def test_photo_qualification_preserves_missing_photos_and_hides_video_before_paging(tmp_path: Path) -> None:
    library = build_library(tmp_path, ("photo.jpg", "other.jpg"))
    try:
        photo_id = library.assets["photo.jpg"]
        other_id = library.assets["other.jpg"]
        with library.catalog.write() as connection:
            connection.execute(
                "UPDATE file_locations SET relative_path='video.mp4', path_key='video.mp4', "
                "availability='MISSING' WHERE asset_id=?", (photo_id,)
            )
            connection.execute(
                "UPDATE file_locations SET relative_path='missing.jpg', path_key='missing.jpg', "
                "availability='MISSING' WHERE asset_id=?", (other_id,)
            )
        scope = SearchScope.build(availability="any")
        where, params = scope_condition(library.catalog.connection, scope)
        found = [int(row["asset_id"]) for row in library.catalog.read_all(
            f"SELECT a.asset_id FROM assets a WHERE {where} ORDER BY a.asset_id", params
        )]
        assert found == [other_id]
        missing_scope = SearchScope.build(availability="missing")
        missing_where, missing_params = scope_condition(library.catalog.connection, missing_scope)
        assert [int(row["asset_id"]) for row in library.catalog.read_all(
            f"SELECT a.asset_id FROM assets a WHERE {missing_where}", missing_params
        )] == [other_id]
        page = _asset_chunk(
            library.catalog, after=None, size=10, source_id=None, availability="any"
        )
        assert [int(row["asset_id"]) for row in page] == [other_id]

        # Same content has a photo path in the main source and a video path in another source.
        source2_root = tmp_path / "empty"
        source2_root.mkdir()
        source2 = library.service.register_source(display_name="video-only", root_path=source2_root)
        with library.catalog.write() as connection:
            connection.execute(
                "UPDATE file_locations SET relative_path='shared.jpg', path_key='shared.jpg', "
                "availability='PRESENT' WHERE asset_id=?", (photo_id,)
            )
            connection.execute(
                "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, size, "
                "mtime_ns, availability) VALUES(?,?,?,?,?,?,?)",
                (int(source2["source_id"]), "shared.MP4", "shared.mp4", photo_id, 1, 1, "MISSING"),
            )
        mixed_scope = SearchScope.build(source_id=library.source_id)
        mixed_where, mixed_params = scope_condition(library.catalog.connection, mixed_scope)
        mixed = [int(row["asset_id"]) for row in library.catalog.read_all(
            f"SELECT a.asset_id FROM assets a WHERE {mixed_where}", mixed_params
        )]
        assert photo_id in mixed
        video_only_scope = SearchScope.build(source_id=int(source2["source_id"]))
        video_where, video_params = scope_condition(library.catalog.connection, video_only_scope)
        assert library.catalog.read_all(
            f"SELECT a.asset_id FROM assets a WHERE {video_where}", video_params
        ) == []
        first_page = _asset_chunk(
            library.catalog, after=None, size=1, source_id=library.source_id, availability="any"
        )
        assert len(first_page) == 1
        next_page = _asset_chunk(
            library.catalog, after=int(first_page[0]["asset_id"]), size=1,
            source_id=library.source_id, availability="any",
        )
        assert len(next_page) == 1
        assert {int(first_page[0]["asset_id"]), int(next_page[0]["asset_id"])} == {photo_id, other_id}
        assert _asset_chunk(
            library.catalog, after=int(next_page[0]["asset_id"]), size=1,
            source_id=library.source_id, availability="any",
        ) == []
    finally:
        library.close()
