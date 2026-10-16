"""Exact set properties for compact vector candidate filters."""

from __future__ import annotations

import random
import sqlite3

import pytest

from photo_search.indexing.store import _candidate_id_condition


def selected_by_sql(ids: list[int]) -> set[int]:
    minimum, maximum = -(2**63), 2**63 - 1
    neighbors = {value + step for value in ids for step in (-1, 0, 1)}
    universe = {value for value in neighbors if minimum <= value <= maximum} | {-1, 0, 1}
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE candidates(asset_id INTEGER PRIMARY KEY)")
        connection.executemany("INSERT INTO candidates VALUES (?)", [(v,) for v in universe])
        return {
            int(row[0])
            for row in connection.execute(
                "SELECT asset_id FROM candidates WHERE " + _candidate_id_condition(ids)
            )
        }


@pytest.mark.parametrize(
    "ids",
    [
        [],
        [1],
        list(range(63)),
        list(range(64)),
        [i for i in range(300) if i not in {0, 7, 108, 159, 299}],
        list(range(0, 1000, 2)),
        [100 * run + offset for run in range(64) for offset in range(5)],
        [100 * run + offset for run in range(65) for offset in range(5)],
        list(range(-(2**63), -(2**63) + 64)),
        list(range(2**63 - 64, 2**63)),
        list(range(-40, 40)),
    ],
)
def test_predicate_keeps_exact_membership_including_holes_and_int64_edges(ids: list[int]) -> None:
    assert selected_by_sql(ids) == set(ids)


def test_random_candidate_sets_match_sql_membership() -> None:
    randomizer = random.Random(20261015)
    for _ in range(100):
        selected: set[int] = set()
        for _ in range(randomizer.randrange(1, 90)):
            start = randomizer.randrange(-10000, 10000)
            selected.update(range(start, start + randomizer.randrange(1, 100)))
        ids = sorted(selected)
        assert selected_by_sql(ids) == selected


def test_short_sparse_and_too_many_runs_keep_the_in_path() -> None:
    for ids in (
        list(range(63)),
        list(range(0, 1000, 2)),
        [100 * run + offset for run in range(64) for offset in range(5)],
        [100 * run + offset for run in range(65) for offset in range(5)],
    ):
        assert _candidate_id_condition(ids) == "asset_id IN (" + ", ".join(map(str, ids)) + ")"


def test_compact_predicate_has_bounded_runs_and_keeps_grouping() -> None:
    ids = [100 * run + offset for run in range(64) for offset in range(20)]
    condition = _candidate_id_condition(ids)
    assert condition.count("BETWEEN") == 64
    assert condition.count(" OR ") == 63
    assert condition.startswith("(") and condition.endswith(")")
    assert len(condition) < len("asset_id IN (" + ", ".join(map(str, ids)) + ")")
