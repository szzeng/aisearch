"""Bounded build reads, shared-source identities and the existing selection counters."""

from pathlib import Path

import pytest
from p2_search_fixtures import build_library, stamp

from photo_search.indexing import semantic
from photo_search.refresh.pipeline import _scoped_asset_page


def test_selection_keeps_read_pages_bounded_across_unsupported_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = tuple(f"photo-{index}.jpg" for index in range(8))
    library = build_library(tmp_path, names)
    try:
        monkeypatch.setattr(semantic, "SELECTION_PAGE_SIZE", 2)
        opened: list[str] = []
        sizes: list[int] = []
        original_read = library.catalog.read_all

        def read(sql: str, parameters: tuple = ()) -> list:
            rows = original_read(sql, parameters)
            if "FROM file_locations AS f INDEXED BY" in sql:
                sizes.append(len(rows))
            return rows

        def eligible(path: Path) -> tuple[bool, str]:
            opened.append(path.name)
            return int(path.stem.rsplit("-", 1)[1]) >= 5, "SYNTHETIC_UNSUPPORTED"

        monkeypatch.setattr(library.catalog, "read_all", read)
        monkeypatch.setattr(semantic, "image_eligibility", eligible)
        first = semantic.semantic_selection(
            library.catalog, space_id="new-space", processor_id="new-provider", limit=2
        )
        assert [item["asset_id"] for item in first["targets"]] == [
            library.assets[names[5]], library.assets[names[6]]
        ]
        assert first["checked"] == 7
        assert first["coverage_complete"] is False
        assert first["out_of_scope"]["count"] == 5
        assert first["out_of_scope"]["labels"] == {"SYNTHETIC_UNSUPPORTED": 5}
        assert opened == list(names[:7])  # SQL lookahead must not decode the eighth image.
        assert len(sizes) > 1 and max(sizes) <= 3

        second = semantic.semantic_selection(
            library.catalog, space_id="new-space", processor_id="new-provider",
            after_asset_id=first["targets"][-1]["asset_id"], limit=1,
        )
        assert second["checked"] == 1 and second["coverage_complete"] is True
        assert second["targets"][0]["asset_id"] == library.assets[names[7]]
        assert second["out_of_scope"]["count"] == 0

        all_targets = semantic.semantic_selection(
            library.catalog, space_id="new-space", processor_id="new-provider"
        )
        assert all_targets["checked"] == 8 and all_targets["coverage_complete"] is True
        assert len(all_targets["targets"]) == 3
    finally:
        library.close()


def test_selection_zero_limit_and_empty_scope_preserve_counter_semantics(tmp_path: Path) -> None:
    library = build_library(tmp_path, ("first.jpg", "second.jpg"))
    try:
        zero = semantic.semantic_selection(
            library.catalog, space_id="new-space", processor_id="new-provider", limit=0
        )
        assert zero["targets"] == []
        assert zero["checked"] == 1 and zero["coverage_complete"] is False
        empty = semantic.semantic_selection(
            library.catalog, space_id="new-space", processor_id="new-provider",
            source_id=library.source_id + 100, limit=0,
        )
        assert empty["targets"] == []
        assert empty["checked"] == 0 and empty["coverage_complete"] is True
    finally:
        library.close()


def test_shared_source_selection_and_scope_pages_use_its_present_locations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = build_library(tmp_path, ("first.jpg", "second.jpg", "third.jpg"))
    try:
        second_root = tmp_path / "second-source"
        second_root.mkdir()
        for name, original in (
            ("copy-a.jpg", "first.jpg"), ("copy-a-again.jpg", "first.jpg"),
            ("copy-b.jpg", "second.jpg"),
        ):
            path = second_root / name
            path.write_bytes((library.root / original).read_bytes())
            stamp(path)
        source_id = int(library.service.register_source(
            display_name="second", root_path=second_root
        )["source_id"])
        library.service.run_scan(source_id)
        opened: list[Path] = []

        def eligible(path: Path) -> tuple[bool, str]:
            opened.append(path)
            return True, "JPEG"

        monkeypatch.setattr(semantic, "image_eligibility", eligible)
        result = semantic.semantic_selection(
            library.catalog, space_id="new-space", processor_id="new-provider",
            source_id=source_id,
        )
        expected = [library.assets["first.jpg"], library.assets["second.jpg"]]
        assert [row["asset_id"] for row in result["targets"]] == expected
        assert result["checked"] == 2 and result["coverage_complete"] is True
        assert len(opened) == 2 and all(path.parent == second_root for path in opened)
        assert _scoped_asset_page(
            library.catalog, source_id=source_id, after_asset_id=0, limit=1
        ) == expected[:1]
        assert _scoped_asset_page(
            library.catalog, source_id=source_id, after_asset_id=expected[0], limit=1
        ) == expected[1:]
        with library.catalog.write() as connection:
            connection.execute(
                "UPDATE file_locations SET availability='MISSING' "
                "WHERE source_id=? AND asset_id=?", (source_id, expected[1]),
            )
        assert _scoped_asset_page(
            library.catalog, source_id=source_id, after_asset_id=0, limit=10
        ) == expected[:1]
        assert len(_scoped_asset_page(
            library.catalog, source_id=None, after_asset_id=0, limit=10
        )) == 3
        assert semantic.semantic_targets(
            library.catalog, space_id="new-space", processor_id="new-provider",
            source_id=source_id,
        ) == [{"asset_id": expected[0], "input_revision": result["targets"][0]["input_revision"]}]
    finally:
        library.close()
