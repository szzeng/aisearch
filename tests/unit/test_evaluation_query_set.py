"""Contract tests for the 150-query Chinese evaluation set (no photo library needed)."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

from photo_search.evaluation.dataset import parse_dataset

TOOLS = Path(__file__).resolve().parents[2] / "tools" / "evaluation"
sys.path.insert(0, str(TOOLS))

from build_query_set import (  # noqa: E402
    MIN_POSITIVES,
    _compare,
    _render,
    build,
    matches,
    query_table,
)


def synthetic_corpus(count: int = 40) -> dict:
    images = []
    for index in range(count):
        images.append(
            {
                "id": f"img_{index:05d}",
                "path": f"D:/authorized/sample-{index}.jpg",
                "source": "sample",
                "width": 4000 if index % 2 else 3000,
                "height": 3000 if index % 2 else 4000,
                "orientation": "landscape" if index % 2 else "portrait",
                "aspect": 1.3333 if index % 2 else 0.75,
                "bytes": 4_000_000 if index % 3 else 200_000,
                "brightness": 0.2 + 0.02 * (index % 20),
                "contrast": 0.1 + 0.01 * (index % 25),
                "saturation": 0.1 + 0.02 * (index % 20),
                "grey_ratio": 0.1 * (index % 10),
                "redness": -0.1 + 0.01 * (index % 20),
                "greenness": -0.1 + 0.008 * (index % 22),
                "blueness": -0.1 + 0.01 * (index % 20),
                "warmth": -0.1 + 0.02 * (index % 15),
                "dominant_hue": "red" if index % 2 else "blue",
                "captured_at": "2024-07-01T12:00:00",
                "capture_time_source": "exif",
                "year": 2024,
                "month": (index % 12) + 1,
                "hour": index % 24,
                "weekday": index % 7,
                "face_count": index % 5,
                "max_det_score": 0.5 + 0.01 * (index % 40),
                "ocr_chars": index * 3,
                "ocr_has_digits": index % 4 == 0,
                "ocr_has_latin": index % 6 == 0,
            }
        )
    return {"sources": ["sample"], "count": len(images), "images": images}


def test_query_table_is_150_queries_with_a_50_100_split() -> None:
    queries = query_table()
    assert len(queries) == 150
    splits = Counter(query["split"] for query in queries)
    assert splits == {"tuning": 50, "holdout": 100}
    assert len({query["id"] for query in queries}) == 150
    assert len({query["text"] for query in queries}) == 150
    for query in queries:
        assert query["text"].strip()
        assert query["strict"], f"{query['id']} has no strict rule"
        assert _render(query["strict"])


def test_rule_engine_evaluates_each_operator() -> None:
    image = synthetic_corpus(count=1)["images"][0]
    image.update(
        {
            "hour": 21,
            "month": 7,
            "weekday": 6,
            "year": 2024,
            "brightness": 0.7,
            "face_count": 2,
            "orientation": "landscape",
            "ocr_has_digits": True,
        }
    )
    assert _compare(("ge", "hour", 19), image)
    assert _compare(("in", "month", [6, 7, 8]), image)
    assert _compare(("between", "hour", 20, 22), image)
    assert _compare(("eq", "orientation", "landscape"), image)
    assert _compare(("flag", "ocr_has_digits"), image)
    assert not _compare(("nflag", "ocr_has_digits"), image)
    assert _compare(("and", [("ge", "hour", 19), ("ge", "face_count", 2)]), image)
    assert _compare(("or", [("le", "hour", 4), ("ge", "hour", 19)]), image)
    assert not _compare(("ge", "hour", 23), image)
    assert not _compare(("le", "hour", 23), {**image, "hour": None})
    with pytest.raises(ValueError, match="unknown rule"):
        _compare(("nonsense", "hour", 1), image)


def test_matches_returns_only_matching_images() -> None:
    corpus = synthetic_corpus()
    night = matches(("or", [("ge", "hour", 19), ("le", "hour", 4)]), corpus["images"])
    assert night
    assert all(image["hour"] >= 19 or image["hour"] <= 4 for image in night)


def test_build_emits_a_dataset_the_contract_accepts(tmp_path: Path) -> None:
    corpus = synthetic_corpus()
    dataset, report, problems = build(corpus)
    assert len(report) == 150
    assert dataset["image_split_policy"] == "shared"
    # A 40-image synthetic corpus cannot satisfy every rule, so the round trip is checked on
    # the queries that do carry labels; the real build refuses to write any unlabelled query.
    dataset["queries"] = [entry for entry in dataset["queries"] if entry["relevant"]]
    assert dataset["queries"]
    parsed = parse_dataset(dataset, base_directory=tmp_path)
    assert parsed.image_split_policy == "shared"
    assert len(parsed.images) == len(corpus["images"])
    for query in parsed.queries:
        assert set(query.relevant.values()) <= {1, 2}
    assert problems  # the synthetic corpus is too small and too uniform to satisfy the bounds


def test_build_flags_queries_with_too_few_positive_images() -> None:
    corpus = synthetic_corpus(count=200)
    dataset, report, problems = build(corpus)
    empty = [
        record
        for record in report
        if record["graded_positives"] < MIN_POSITIVES
    ]
    assert empty, "the synthetic corpus should leave some queries without enough matches"
    assert any("positive images" in problem for problem in problems)
