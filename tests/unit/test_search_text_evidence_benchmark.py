"""The synthetic performance fixture must exercise actual explanation omissions."""
from __future__ import annotations

import pytest

from tools.benchmarks.search_text_evidence import (
    MATCH,
    ROOT,
    Reader,
    build_fixture,
    corrected_unoptimized,
    parse_args,
    previous,
    target_row,
)


def test_cli_requires_fresh_output_and_confines_work_to_the_repository(tmp_path):
    output = tmp_path / "new-run.json"
    with pytest.raises(SystemExit):
        parse_args([])
    defaults = parse_args(["--output", str(output)])
    assert defaults.work_root == ROOT / ".tmp/search-text-evidence"
    args = parse_args(["--output", str(output), "--work-root", str(tmp_path / "work")])
    assert args.output == output and args.work_root == tmp_path / "work"
    output.write_text("preserve JSON", encoding="utf-8")
    with pytest.raises(FileExistsError):
        parse_args(["--output", str(output)])
    other_output = tmp_path / "other-run.json"
    other_output.with_suffix(".xml").write_text("preserve JUnit", encoding="utf-8")
    with pytest.raises(FileExistsError):
        parse_args(["--output", str(other_output)])
    with pytest.raises(SystemExit):
        parse_args(["--output", str(tmp_path / "new.json"), "--work-root", str(ROOT.parent)])
    assert output.read_text(encoding="utf-8") == "preserve JSON"
    assert other_output.with_suffix(".xml").read_text(encoding="utf-8") == "preserve JUnit"


def test_fixture_has_exact_candidates_and_partial_match_decoys(tmp_path):
    db, ids, partial, expected = build_fixture(tmp_path / "synthetic.sqlite3", rows=30, candidates=12)
    try:
        assert [row[0] for row in db.execute(
            "SELECT rowid FROM search_documents_fts WHERE search_documents_fts MATCH ? ORDER BY rowid",
            (MATCH.expression,),
        )] == ids
        assert corrected_unoptimized(Reader(db), MATCH, ids + partial) == expected
        legacy = previous(Reader(db), MATCH, ids)
        assert len(legacy) == 4  # filename-only and place-only, twice each.
        assert len(expected) == 12
        assert {tuple(columns) for columns in expected.values()} == {
            ("filename_text",), ("ocr_text",), ("filename_text", "ocr_text"),
            ("place_text",), ("caption_text", "user_text"), ("place_text", "tag_text"),
        }
        assert len(partial) == 2 and not set(partial).intersection(ids)
    finally:
        db.close()


def test_long_ocr_preserves_labels_and_other_fields():
    for ordinal in range(6):
        short, short_truth = target_row(ordinal)
        long, long_truth = target_row(ordinal, long_ocr=True)
        assert short_truth == long_truth
        assert short[:2] + short[3:] == long[:2] + long[3:]
        assert len(long[2].split()) - len(short[2].split()) == 992


def test_fixture_seed_reproduces_ids_and_labels(tmp_path):
    first, first_ids, first_partial, first_truth = build_fixture(
        tmp_path / "first.sqlite3", rows=30, candidates=12
    )
    second, second_ids, second_partial, second_truth = build_fixture(
        tmp_path / "second.sqlite3", rows=30, candidates=12
    )
    try:
        assert (first_ids, first_partial, first_truth) == (second_ids, second_partial, second_truth)
    finally:
        first.close()
        second.close()
