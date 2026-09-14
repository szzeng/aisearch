"""P2-04: the deterministic tokenizer and its controlled MATCH policy (version 1).

No database, no model and no dependency: this is the pure half of the frozen search text
policy, plus one check that SQLite's own ``unicode61`` tokenizer sees exactly the tokens
this module emits (otherwise a query could never match the document it was derived from).
"""

from __future__ import annotations

import sqlite3

import pytest

from photo_search.catalog.errors import CatalogError
from photo_search.search.tokens import (
    FTS_GENERATION_ID,
    MAX_QUERY_CHARS,
    TOKENIZER_VERSION,
    build_match,
    index_text,
    scan_tokens,
)


def terms(query: str) -> list[str]:
    return build_match(query).term_texts()


def test_chinese_runs_index_one_character_per_token_and_query_as_a_phrase() -> None:
    assert index_text("杭州西湖.jpg") == "杭 州 西 湖 jpg"
    assert terms("西湖") == ['"西 湖"']
    assert terms("杭州西湖") == ['"杭 州 西 湖"']
    # A single ideograph is one token, and a shorter query is a real sub-string match.
    assert terms("湖") == ['"湖"']


def test_latin_and_digits_are_prefix_terms_and_underscores_split() -> None:
    assert index_text("IMG_0001.JPG") == "img 0001 jpg"
    assert terms("IMG_0001") == ['"img"*', '"0001"*']
    assert terms("jpg") == ['"jpg"*']
    assert terms("sunset") == ['"sunset"*']


def test_full_width_and_half_width_normalise_to_the_same_terms() -> None:
    assert index_text("ＩＭＧ＿０００１.JPG") == index_text("img_0001.jpg")
    assert index_text("ＡＢＣ 中文") == "abc 中 文"
    assert terms("ｉｍｇ") == terms("IMG")


def test_mixed_chinese_and_latin_is_anded() -> None:
    assert terms("杭州 jpg") == ['"杭 州"', '"jpg"*']
    assert terms("西湖 2024 照片") == ['"西 湖"', '"2024"*', '"照 片"']


def test_stopwords_do_not_turn_a_query_into_all_words_must_match() -> None:
    # 的 / the are grammar words: they are dropped from the AND, they do not restrict it.
    assert terms("杭州 的 照片") == ['"杭 州"', '"照 片"']
    assert terms("the beach") == ['"beach"*']
    assert terms("beach of the sunset") == ['"beach"*', '"sunset"*']
    # A CJK stopword character also ends a run, so it cannot glue two words together.
    assert terms("杭州的照片") == ['"杭 州"', '"照 片"']


def test_a_query_of_only_stopwords_is_refused_instead_of_returning_nothing() -> None:
    with pytest.raises(CatalogError) as failure:
        build_match("的 the of")
    assert failure.value.code == "VALIDATION_ERROR"
    assert failure.value.details["reason"] == "NO_SEARCHABLE_TERM"


def test_an_explicit_phrase_is_kept_verbatim() -> None:
    assert terms('"西湖 景区"') == ['"西 湖 景 区"']
    assert terms('"sunset beach"') == ['"sunset beach"']
    # Quoting only part of the input leaves the rest on the ordinary policy.
    assert terms('"西湖" 2024') == ['"西 湖"', '"2024"*']


def test_fts_special_characters_never_become_syntax() -> None:
    assert terms("(西湖)") == ['"西 湖"']
    assert terms("x-y") == ['"x"*', '"y"*']
    # ... and a one-letter Latin stopword is dropped like any other grammar word.
    assert terms("a-b") == ['"b"*']
    assert terms("NEAR") == ['"near"*']
    # FTS5 keywords are ordinary words here; AND/OR happen to be grammar words as well.
    assert terms("MATCH AND OR NOT NEAR('a', 'b')") == ['"match"*', '"not"*', '"near"*', '"b"*']
    assert terms("C:\\Users\\me\\photo.jpg") == [
        '"c"*',
        '"users"*',
        '"me"*',
        '"photo"*',
        '"jpg"*',
    ]
    # An unterminated quote is not FTS syntax either: the remainder is plain text.
    assert terms('"西湖') == ['"西 湖"']
    for refused in ('"', "###", "   ", "*"):
        with pytest.raises(CatalogError) as failure:
            build_match(refused)
        assert failure.value.code == "VALIDATION_ERROR"
        assert failure.value.details["reason"] == "NO_SEARCHABLE_TERM"


def test_input_bounds_are_structured_refusals() -> None:
    with pytest.raises(CatalogError) as too_long:
        build_match("a" * (MAX_QUERY_CHARS + 1))
    assert too_long.value.code == "VALIDATION_ERROR"
    assert too_long.value.details["max_chars"] == MAX_QUERY_CHARS

    with pytest.raises(CatalogError) as too_many:
        build_match(" ".join(f"w{index}" for index in range(40)))
    assert too_many.value.code == "VALIDATION_ERROR"
    assert too_many.value.details["reason"] == "TOO_MANY_TERMS"
    assert too_many.value.details["max_terms"] == 32


def test_the_match_expression_only_contains_quoted_operands() -> None:
    for query in ("西湖 jpg", "IMG_0001", '"西湖 景区"', "(a) OR (b)*", "NEAR/2"):
        match = build_match(query)
        for operand in match.expression.split(" AND "):
            assert operand.startswith('"')
            assert operand.endswith('"') or operand.endswith('"*')
        assert match.tokenizer_version == TOKENIZER_VERSION == 1
        assert match.fts_generation_id == FTS_GENERATION_ID == "fts-v1"


def test_sqlite_unicode61_sees_exactly_the_tokens_this_module_emits() -> None:
    texts = [
        "杭州西湖 照片.jpg",
        "IMG_0001.JPG",
        "Beach sunset 2024.jpeg",
        "café crème.JPG",
        "梅 兰 竹 菊 2024年",
    ]
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE probe USING fts5(body, tokenize = 'unicode61')")
        connection.execute("CREATE VIRTUAL TABLE probe_vocab USING fts5vocab(probe, 'row')")
        for text in texts:
            connection.execute("INSERT INTO probe(body) VALUES (?)", (index_text(text),))
            for token in scan_tokens(text):
                found = connection.execute(
                    "SELECT COUNT(*) FROM probe WHERE probe MATCH ?", (f'"{token}"',)
                ).fetchone()[0]
                assert found >= 1, (text, token)
        connection.execute("DELETE FROM probe")
        assert connection.execute("SELECT COUNT(*) FROM probe_vocab").fetchone()[0] == 0
    finally:
        connection.close()
