"""Small geometric edge cases for the offline index; no models or downloaded data."""

from __future__ import annotations

import math

import pytest

from photo_search.geo import dataset as geo


def content(points: dict[str, tuple[float, float]]) -> geo.DatasetContent:
    return geo.DatasetContent(
        dataset_id="test", package_id="test", package_version="1", provider="fixture",
        state_on_disk="VALIDATED", manifest={}, files=(), aliases={},
        places={
            key: geo.DatasetPlace(key, "city", key, None, lat, lon, 0, None)
            for key, (lat, lon) in points.items()
        },
    )


@pytest.mark.parametrize(
    ("position", "near", "far"),
    [
        ((0, 179.9), (0, -179.9), (0, 178)),  # Date-line crossing.
        ((89.9, 10), (89.9, -170), (88, 10)),  # Longitude collapses near the pole.
        ((0, 0), (0, 0), (10, 10)),  # Zero coordinates are valid.
    ],
)
def test_spherical_neighborhood_not_flat_longitude(position, near, far) -> None:
    package = content({"near": near, "far": far})
    result = geo.nearby_cities(package, *position, max_candidates=3, max_distance_m=25_000)
    assert [r["dataset_place_id"] for r in result] == ["near"]
    assert result[0]["disposition"] == "SUGGESTION_ONLY"
    assert result[0]["match_basis"] == "NEARBY_CITY_POINT"


def test_distance_boundary_ties_and_empty_dataset() -> None:
    package = content({"b": (0, 1), "a": (0, 1), "outside": (0, 2)})
    distance = geo.haversine_m(0, 0, 0, 1)
    result = geo.nearby_cities(package, 0, 0, max_candidates=2, max_distance_m=distance)
    assert [r["dataset_place_id"] for r in result] == ["a", "b"]
    assert geo.nearby_cities(
        package, 0, 0, max_candidates=3, max_distance_m=math.nextafter(distance, 0)
    ) == []
    assert geo.nearby_cities(content({}), 0, 0, max_candidates=3, max_distance_m=200_000) == []


def test_index_is_reused_and_distant_points_do_not_need_distance_evaluation(monkeypatch) -> None:
    package = content({"near": (30, 120), **{f"far-{i}": (-30, i) for i in range(30)}})
    index = package.city_index
    measured = []
    distance = geo.haversine_m

    def record(*args):
        measured.append(args)
        return distance(*args)

    monkeypatch.setattr(geo, "haversine_m", record)
    for _ in range(2):
        geo.nearby_cities(package, 30, 120, max_candidates=3, max_distance_m=200_000)
    assert package.city_index is index
    assert len(measured) == 2
