"""Actual SQLite phrase/prefix evidence, including OCR and cross-column matches."""

from __future__ import annotations

import sqlite3

import pytest

from photo_search.search.documents import TEXT_COLUMNS
from photo_search.search.retrieval import text_column_matches
from photo_search.search.tokens import build_match, index_text


class TextCatalog:
    def __init__(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            "CREATE VIRTUAL TABLE search_documents_fts USING fts5("
            + ", ".join(TEXT_COLUMNS) + ")"
        )

    def add(self, asset_id: int, **values: str) -> None:
        self.connection.execute(
            "INSERT INTO search_documents_fts(rowid, " + ", ".join(TEXT_COLUMNS)
            + ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (asset_id, *(index_text(values.get(column, "")) for column in TEXT_COLUMNS)),
        )

    def read_all(self, sql: str, parameters: tuple) -> list[sqlite3.Row]:
        return self.connection.execute(sql, parameters).fetchall()


@pytest.fixture
def catalog():
    value = TextCatalog()
    try:
        yield value
    finally:
        value.connection.close()


@pytest.mark.parametrize("column", TEXT_COLUMNS)
def test_reports_each_supported_text_source(catalog: TextCatalog, column: str) -> None:
    catalog.add(1, **{column: "和田 发票 AB1234"})
    catalog.add(2, **{column: "福田 发票 AB1234"})
    assert text_column_matches(catalog, build_match("和田"), [1, 2]) == {1: [column]}


def test_cross_column_query_reports_only_contributing_columns(catalog: TextCatalog) -> None:
    catalog.add(1, filename_text="beach.jpg", ocr_text="sunset", place_text="深圳")
    catalog.add(2, filename_text="beach.jpg", ocr_text="unrelated")
    catalog.add(3, filename_text="sunset beach.jpg", ocr_text="sunset")
    assert text_column_matches(catalog, build_match("beach sunset"), [1, 2, 3, 3]) == {
        1: ["filename_text", "ocr_text"],
        3: ["filename_text", "ocr_text"],
    }


def test_phrase_must_stay_in_one_column_and_ids_remain_restricted(catalog: TextCatalog) -> None:
    catalog.add(1, filename_text="beach.jpg", ocr_text="sunset")
    catalog.add(2, ocr_text="beach sunset")
    catalog.add(3, place_text="beach sunset")
    assert text_column_matches(catalog, build_match('"beach sunset"'), [1, 2]) == {
        2: ["ocr_text"],
    }
    assert text_column_matches(catalog, build_match("sunset"), []) == {}


def test_prefix_and_escaped_query_evidence_uses_actual_fts_semantics(catalog: TextCatalog) -> None:
    catalog.add(1, ocr_text="AB1234 \" MATCH; 深圳湾", filename_text="receipt.jpg")
    assert text_column_matches(catalog, build_match("AB12 MATCH 深圳湾"), [1]) == {
        1: ["ocr_text"],
    }
    assert text_column_matches(catalog, build_match('"AB12"'), [1]) == {}
