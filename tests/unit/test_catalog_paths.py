"""P1-01: Windows comparison keys and source-root boundaries (ADR-0002 §4)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from photo_search.catalog.errors import INVALID_PATH, CatalogError
from photo_search.catalog.paths import is_same_or_ancestor, normalize_root, path_key, root_key


def test_path_key_unifies_separators_and_case() -> None:
    assert path_key("a/b/c.jpg") == "a\\b\\c.jpg"
    assert path_key("A\\B\\C.JPG") == "a\\b\\c.jpg"
    assert path_key("a//b\\c.jpg") == "a\\b\\c.jpg"


def test_path_key_keeps_display_details_but_not_unicode_normalisation() -> None:
    composed = "照片/é.jpg"
    decomposed = "照片/e\u0301.jpg"
    assert path_key(composed) == "照片\\é.jpg"
    assert path_key(decomposed) != path_key(composed)
    assert path_key("照片 目录/子 目录.jpg") == "照片 目录\\子 目录.jpg"


def test_path_key_resolves_inner_segments_only() -> None:
    assert path_key("a/./b/../c.jpg") == "a\\c.jpg"
    assert path_key("a\\b\\..\\c.jpg") == "a\\c.jpg"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "C:\\photos\\a.jpg",
        "C:a.jpg",
        "\\\\server\\share\\a.jpg",
        "//server/share/a.jpg",
        "\\rooted.jpg",
        "/rooted.jpg",
        "..\\escape.jpg",
        "../escape.jpg",
        "a\\..\\..\\b.jpg",
        ".",
        "..",
        "a\\..",
        "a\x00b.jpg",
    ],
)
def test_path_key_rejects_paths_outside_the_source_root(value: str) -> None:
    with pytest.raises(CatalogError) as error:
        path_key(value)
    assert error.value.code == INVALID_PATH


@pytest.mark.skipif(os.name != "nt", reason="Windows drive path spelling")
def test_root_key_is_absolute_case_insensitive_and_trailing_separator_free() -> None:
    assert root_key("C:/Temp/Photos/") == root_key("c:\\temp\\photos")
    assert normalize_root("C:/Temp/Photos/") == "C:\\Temp\\Photos"


def test_ancestor_rule_uses_path_component_boundaries(tmp_path) -> None:
    parent = tmp_path / "Photos"
    child = parent / "2020"
    sibling = tmp_path / "Other"
    shared_prefix = tmp_path / "Photo"
    assert is_same_or_ancestor(parent, parent)
    assert is_same_or_ancestor(parent, child)
    assert not is_same_or_ancestor(child, parent)
    assert not is_same_or_ancestor(parent, sibling)
    assert not is_same_or_ancestor(shared_prefix, tmp_path / "Photos")


def test_empty_source_root_is_rejected() -> None:
    with pytest.raises(CatalogError) as error:
        normalize_root("   ")
    assert error.value.code == INVALID_PATH


def test_native_root_keeps_display_path(tmp_path):
    assert normalize_root(tmp_path / "photos") == str(Path(tmp_path / "photos").resolve())
