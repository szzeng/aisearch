"""Regression coverage for API path redaction, including Windows error renderings."""

from __future__ import annotations

import pytest

from photo_search.api.errors import Redactor


@pytest.mark.parametrize(
    "value",
    [
        r"cannot read C:\照片 库 中文\子目录\a.jpg",
        r"cannot read C:\\照片 库 中文\\子目录\\a.jpg",
        "cannot read C:/照片 库 中文/子目录/a.jpg",
        r"cannot read C:/照片 库 中文\子目录/a.jpg",
        r"cannot read c:\照片 库 中文\子目录\a.jpg",
        r"cannot read \\?\C:\照片 库 中文\子目录\a.jpg",
    ],
)
def test_redactor_replaces_windows_root_spellings(value: str) -> None:
    redactor = Redactor(source_roots=[r"C:\照片 库 中文"])

    result = redactor.text(value)

    assert "照片 库 中文" not in result
    assert "<source_root>" in result
    assert "cannot read" in result


def test_redactor_keeps_logical_pairs_and_prefers_the_nested_root() -> None:
    redactor = Redactor(source_roots=[r"D:\Photos", r"D:\Photos\Sub"])

    assert redactor.pairs == ((r"D:\Photos\Sub", "<source_root>"), (r"D:\Photos", "<source_root>"))
    assert redactor.text(r"D:\Photos\Sub\a.jpg") == r"<source_root>\a.jpg"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (r"decoder failed at C:\Other\b.jpg", "<local_path>"),
        (r"decoder failed at \\server\share\a.jpg", "<local_path>"),
        (r"decoder failed at \\?\UNC\server\share\a.jpg", "<local_path>"),
    ],
)
def test_redactor_replaces_unregistered_windows_paths(value: str, expected: str) -> None:
    result = Redactor(source_roots=[r"C:\Photos"]).text(value)

    assert expected in result
    assert "decoder failed" in result
    assert "Other" not in result
    assert "server" not in result


def test_redactor_preserves_non_path_text_and_recurses_through_details() -> None:
    redactor = Redactor(source_roots=[r"C:\Photos"])
    value = {
        "[SCHEMA_TOO_NEW]": {
            "message": r"catalog schema_version 2 at C:\Other\b.jpg",
            "relative": r"子目录 中文\a.jpg",
            "count": 12,
        }
    }

    assert redactor.text("a:b RUNNING 12") == "a:b RUNNING 12"
    assert redactor.details(value) == {
        "[SCHEMA_TOO_NEW]": {
            "message": "catalog schema_version 2 at <local_path>",
            "relative": r"子目录 中文\a.jpg",
            "count": 12,
        }
    }


def test_redactor_empty_and_unconfigured_behavior_is_unchanged() -> None:
    unconfigured = Redactor()

    assert unconfigured.text(None) == ""
    assert unconfigured.text("") == ""
    assert unconfigured.text(r"C:\Other\b.jpg") == r"C:\Other\b.jpg"


def test_error_redactor_uses_only_existing_catalog_and_falls_back():
    from types import SimpleNamespace

    from photo_search.api.context import ApiContext

    context = object.__new__(ApiContext)
    context.settings = SimpleNamespace(data_root=r"D:\AppData")
    context._service = None
    # Even a cold or broken catalog must not be opened by an error response.
    context.service = lambda: (_ for _ in ()).throw(AssertionError("service was opened"))
    assert context.error_redactor().text(r"D:\AppData\catalog.db") == r"<data_root>\catalog.db"
    assert context.error_redactor().text(r"E:\Unregistered\photo.jpg") == "<local_path>"

    class Catalog:
        def read_all(self, sql):
            assert sql == "SELECT root_path FROM source_roots"
            return [{"root_path": r"D:\Photos"}]

    context._service = SimpleNamespace(catalog=Catalog())
    assert context.error_redactor().text(r"D:\Photos\photo.jpg") == r"<source_root>\photo.jpg"

    class BrokenCatalog:
        def read_all(self, sql):
            raise OSError("unavailable")

    context._service = SimpleNamespace(catalog=BrokenCatalog())
    assert context.error_redactor().text(r"D:\AppData\catalog.db") == r"<data_root>\catalog.db"
    assert context.error_redactor().text(r"E:\Other\photo.jpg") == "<local_path>"
