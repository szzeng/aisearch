"""Preserve recognized text across overlapping OCR tiles, using recorded real outputs."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from PIL import Image

from photo_search.ocr.engine import OcrRegion, axis_box, deduplicate, recognize_image
from photo_search.search.tokens import scan_tokens


def region(text, box=(0.0, 0.0, 300.0, 30.0), score=0.99):
    left, top, right, bottom = box
    return OcrRegion(-1, ((left, top), (right, top), (right, bottom), (left, bottom)),
                     box, text, score)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("short,box", [
    ("2026-10-15金额1280", (130.0, 0.0, 300.0, 30.0)),
    ("旅行记录03", (0.0, 0.0, 85.0, 30.0)),
])
def test_a_high_confidence_fragment_cannot_discard_the_full_line(reverse, short, box):
    full = region("旅行记录03日期2026-10-15金额1280", score=0.9989)
    fragment = region(short, box, score=0.9997)
    inputs = [full, fragment]
    assert deduplicate(inputs[::-1] if reverse else inputs) == [full]


@pytest.mark.parametrize("score", [0.3, 0.6])
def test_uncertain_long_text_does_not_suppress_a_trustworthy_fragment(score):
    full = region("旅行记录03日期2026-10-15金额1280", score=score)
    fragment = region("2026-10-15金额1280", (130.0, 0.0, 300.0, 30.0))
    assert set(deduplicate([full, fragment])) == {full, fragment}


@pytest.mark.parametrize("first,second", [
    ("金额1280", "金额1288"),
    ("金额1280", "金额12800"),
    ("订单20261015", "日期20261015"),
    ("invoice number", "invoicenumber"),
    ("12 34", "1234"),
])
def test_conflicting_or_differently_tokenized_text_is_preserved(first, second):
    a, b = region(first, score=0.8), region(second)
    assert set(deduplicate([a, b])) == {a, b}


def test_adjacent_rows_are_not_duplicates_even_when_axis_boxes_overlap():
    a = region("日期2026-10-15", (0.0, 0.0, 300.0, 20.0), score=0.8)
    b = region("日期2026-10-15", (0.0, 6.0, 300.0, 26.0))
    assert set(deduplicate([a, b])) == {a, b}


def test_text_in_another_column_or_another_row_is_not_a_clipped_duplicate():
    full = region("旅行记录03日期2026-10-15金额1280")
    other_column = region("金额1280", (310.0, 0.0, 400.0, 30.0))
    other_row = region("金额1280", (130.0, 35.0, 300.0, 65.0))
    assert set(deduplicate([full, other_column, other_row])) == {
        full, other_column, other_row,
    }


def test_identical_text_keeps_the_best_score_and_original_payload():
    first = region("发票 金额 1280.00", score=0.75)
    best = region("发票 金额 1280.00", (1.0, 1.0, 301.0, 31.0))
    assert deduplicate([first, best]) == [best]
    assert best.text == "发票 金额 1280.00"


@pytest.mark.parametrize("space", [" ", "\t", "\u3000"])
def test_spacing_at_cjk_boundaries_does_not_leave_a_duplicate_suffix(space):
    full = region("旅行记录03日期2026-10-15金额1280", score=0.9989)
    text = f"2026-10-15{space}金额1280"
    fragment = region(text, (130.0, 0.0, 300.0, 30.0), score=0.9997)
    assert scan_tokens(text) == scan_tokens("2026-10-15金额1280")
    assert deduplicate([fragment, full]) == [full]
    assert full.text == "旅行记录03日期2026-10-15金额1280"


def test_suppressed_intermediate_region_cannot_delete_a_third_region():
    full = region("发票编号20261015金额1280")
    prefix = region("发票编号20261015", (0.0, 0.0, 170.0, 30.0), score=0.98)
    middle = region("编号20261015", (35.0, 0.0, 170.0, 30.0), score=0.97)
    expected = [full, middle]
    for permutation in itertools.permutations([full, prefix, middle]):
        assert deduplicate(permutation) == expected


def test_equal_score_equal_box_ties_do_not_depend_on_input_order():
    a = region("金额1280")
    b = region("金额1288")
    c = OcrRegion(-1, ((1.0, 0.0), (300.0, 1.0), (299.0, 30.0), (0.0, 29.0)),
                  a.box, a.text, a.confidence)
    expected = deduplicate([a, b, c])
    for permutation in itertools.permutations([a, b, c]):
        assert deduplicate(permutation) == expected


@pytest.mark.parametrize("fragment", [False, True])
def test_separate_tilted_lines_are_not_duplicates(fragment):
    polygon = ((0.0, 0.0), (300.0, 60.0), (298.0, 70.0), (-2.0, 10.0))
    other = ((150.0, 42.0), (300.0, 72.0), (298.0, 82.0), (148.0, 52.0)) if fragment else (
        tuple((x, y + 12.0) for x, y in polygon)
    )
    a = OcrRegion(-1, polygon, axis_box(polygon), "旅行记录金额1280", 0.99)
    b = OcrRegion(-1, other, axis_box(other), "金额1280" if fragment else a.text, 0.98)
    assert set(deduplicate([a, b])) == {a, b}


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("start", range(4))
def test_a_fragment_of_the_same_tilted_line_is_deduplicated(reverse, start):
    polygon = ((0.0, 0.0), (300.0, 60.0), (298.0, 70.0), (-2.0, 10.0))
    polygon = polygon[start:] + polygon[:start]
    if reverse:
        polygon = polygon[::-1]
    fragment = ((150.0, 30.0), (300.0, 60.0), (298.0, 70.0), (148.0, 40.0))
    a = OcrRegion(-1, polygon, axis_box(polygon), "旅行记录金额1280", 0.98)
    b = OcrRegion(-1, fragment, axis_box(fragment), "金额1280", 0.99)
    assert deduplicate([a, b]) == [a]


def test_a_vertical_line_fragment_is_deduplicated_without_changing_coordinates():
    a = region("旅行记录金额1280", (0.0, 0.0, 30.0, 300.0), score=0.98)
    b = region("金额1280", (0.0, 180.0, 30.0, 300.0), score=0.99)
    assert deduplicate([b, a]) == [a]


def test_degenerate_polygons_are_not_used_to_suppress_text():
    polygon = ((0.0, 0.0),) * 4
    a = OcrRegion(-1, polygon, axis_box(polygon), "旅行记录金额1280", 0.98)
    b = OcrRegion(-1, polygon, axis_box(polygon), "金额1280", 0.99)
    assert set(deduplicate([a, b])) == {a, b}


def test_saved_dense_tiles_keep_the_recognized_prefix_and_geometry():
    data = json.loads((Path(__file__).parents[1] / "fixtures/ocr_dense_recorded.json")
                      .read_text(encoding="utf-8"))

    class RecordedEngine:
        def __init__(self):
            self.tiles = iter(data["tiles"])

        def read(self, _bgr):
            return [[item["polygon"], item["text"], item["confidence"]]
                    for item in next(self.tiles)["raw"]]

    document = recognize_image(RecordedEngine(), Image.new("RGB", tuple(data["size"])))
    assert "旅行记录" not in data["legacy_text"]
    full = [item for item in document.regions if "旅行记录" in item.text]
    assert len(full) == 1
    assert full[0].text == "旅行记录03日期2026-10-15金额1280"
    assert full[0].box == (83.0, 392.0, 1122.0, 444.0)
    assert full[0].confidence == 0.9989294186234474
    assert "旅行记录03日期2026-10-15金额1280" in document.original_text
    assert [item.index for item in document.regions] == list(range(len(document.regions)))
