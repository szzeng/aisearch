"""One-pass cells must equal independent repeated SQL at every edge shape."""
import random
import sqlite3

import pytest

from photo_search.api.map_api import MAX_GRID_CELLS, _bounded_grid, _grid_expression


@pytest.mark.parametrize("bbox", [None, (73, 18, 135, 54), (170, -40, -170, 40),
                                  (114, 22.6, 114, 22.6), (113.8, 22.4, 114.6, 22.9)])
@pytest.mark.parametrize("level", [0, 5, 14, 20])
def test_streamed_grid_matches_repeated_sql_including_boundaries(bbox, level):
    rng = random.Random(20261015)
    w, s, e, n = bbox or (-180, -90, 180, 90)
    width = (e + 360 - w) if w > e else e - w
    points = [(w, s), (e, n), (w, n), (e, s)]
    for _ in range(6000):
        lon = w + rng.random() * width
        points.append((lon - 360 if lon > 180 else lon, s + rng.random() * (n - s)))
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE g(longitude REAL,latitude REAL)")
        conn.executemany("INSERT INTO g VALUES (?,?)", points)

        def query(zoom):
            x, xa, y, ya = _grid_expression(bbox, zoom)
            return conn.execute(
                f"SELECT ({x}) gx,({y}) gy,COUNT(*),MIN(longitude),MAX(longitude),"
                f"MIN(latitude),MAX(latitude) FROM g GROUP BY gx,gy ORDER BY gx,gy", xa + ya)

        actual, actual_level = _bounded_grid(query(level), level)
        expected_level = level
        while True:
            expected = [list(r) for r in query(expected_level)]
            if len(expected) <= MAX_GRID_CELLS or expected_level == 0:
                break
            expected_level -= 1
        assert (actual, actual_level) == (expected, expected_level)
        assert sum(r[2] for r in actual) == len(points)
    finally:
        conn.close()


def test_exact_count_cache_is_bounded_lru_and_retains_zero_totals():
    from photo_search.api.catalog_reads import MAP_COUNT_CACHE_SIZE, MapCountCache

    cache = MapCountCache()
    for number in range(MAP_COUNT_CACHE_SIZE):
        cache.put((number,), number)
    assert cache.get((0,)) == 0  # A zero count is a cache hit and refreshes its age.
    cache.put((MAP_COUNT_CACHE_SIZE,), 10)
    assert cache.get((1,)) is None
    assert cache.get((0,)) == 0
    for number in range(MAP_COUNT_CACHE_SIZE + 1, MAP_COUNT_CACHE_SIZE * 3):
        cache.put((number,), number)
    assert len(cache._values) == MAP_COUNT_CACHE_SIZE
