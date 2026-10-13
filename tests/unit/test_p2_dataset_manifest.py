"""P2-03B: the offline package manifest, its file verification and the distance ordering.

Everything runs on a tiny synthetic package built in ``%TEMP%``; no real dataset, no
network and no photo is involved (ADR-0005 §8).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from p2_dataset_fixtures import (
    BETWEEN_POSITION,
    HANGZHOU_POSITION,
    MINI_PLACES,
    SEA_POSITION,
    build_package,
    mini_spec,
)

from photo_search.catalog.errors import PLACE_DATASET_INVALID, CatalogError
from photo_search.catalog.schema import SUPPORTED_SCHEMA_VERSION as SUPPORTED
from photo_search.geo import dataset as dataset_module


def load(directory: Path) -> dataset_module.DatasetContent:
    return dataset_module.load_content(
        directory, supported_schema_version=SUPPORTED
    )


def rewrite_manifest(directory: Path, mutate) -> None:
    path = directory / dataset_module.MANIFEST_FILENAME
    manifest = json.loads(path.read_text(encoding="utf-8"))
    mutate(manifest)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def test_a_built_package_loads_with_its_records_aliases_and_hierarchy(tmp_path: Path) -> None:
    directory = tmp_path / "package"
    manifest = build_package(directory)

    content = load(directory)

    assert content.dataset_id == "geo-mini-cn-1"
    assert content.package_version == "1"
    assert len(content.places) == len(MINI_PLACES)
    assert len(content.city_places) == 4
    assert content.ancestors("city-hangzhou") == ["中国"]
    assert content.aliases["city-hangzhou"][0]["text"] == "Hangzhou"
    assert manifest["coordinate_reference_system"] == "WGS84"
    assert manifest["coverage"]["record_count"] == len(MINI_PLACES)
    assert {item["role"] for item in manifest["files"]} == {
        "PLACES",
        "ALIASES",
        "HIERARCHY",
        "LICENSE",
    }
    assert all(
        not item["relative_path"].startswith(("/", "\\")) for item in manifest["files"]
    )


def test_the_builder_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"

    first_manifest = build_package(first)
    second_manifest = build_package(second)

    assert first_manifest == second_manifest
    for name in ("places.jsonl", "hierarchy.jsonl", "aliases.jsonl", "LICENSE.txt"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_schema_30_place_package_remains_compatible_with_schema_31_catalog() -> None:
    assert dataset_module._legacy_place_schema_compatible(
        compatible_minimum=16,
        compatible_maximum=30,
        supported_schema_version=31,
    )


def test_the_builder_rejects_an_unknown_place_field(tmp_path: Path) -> None:
    spec = mini_spec()
    spec["places"][1]["elevation_m"] = 12.0

    with pytest.raises(CatalogError) as error:

        build_package(tmp_path / "package", spec=spec)

    assert error.value.code == PLACE_DATASET_INVALID


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda m: m["source"].pop("license_url"), id="missing-license-url"),
        pytest.param(lambda m: m["source"].update(attribution=""), id="missing-attribution"),
        pytest.param(lambda m: m.update(coordinate_reference_system="GCJ02"), id="not-wgs84"),
        pytest.param(
            lambda m: m["compatible_catalog_schema"].update(minimum=90, maximum=99),
            id="incompatible-schema",
        ),
        pytest.param(lambda m: m.update(previous_dataset_id=m["dataset_id"]), id="self-previous"),
        pytest.param(lambda m: m["files"][0].update(relative_path="C:/tmp/places.jsonl"), id="absolute-path"),
        pytest.param(lambda m: m["files"][0].update(relative_path="../places.jsonl"), id="parent-path"),
        pytest.param(lambda m: m["files"][0].update(role="VECTOR"), id="unknown-role"),
        pytest.param(lambda m: m.update(state="BROKEN"), id="unknown-state"),
        pytest.param(lambda m: m.update(content_sha256="0" * 64), id="content-checksum"),
        pytest.param(
            lambda m: m["build"].update(parameters_sha256="0" * 64),
            id="parameters-digest",
        ),
    ],
)
def test_a_bad_manifest_is_never_accepted(tmp_path: Path, mutate) -> None:
    directory = tmp_path / "package"
    build_package(directory)
    rewrite_manifest(directory, mutate)

    with pytest.raises(CatalogError) as error:
        load(directory)

    assert error.value.code == PLACE_DATASET_INVALID


def test_a_tampered_or_missing_file_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "package"
    build_package(directory)
    places = directory / "places.jsonl"
    original = places.read_text(encoding="utf-8")

    places.write_text(original + "\n", encoding="utf-8", newline="\n")
    with pytest.raises(CatalogError) as sized:
        load(directory)
    assert sized.value.code == PLACE_DATASET_INVALID

    # Same length, different bytes: the checksum, not the size, has to catch it.
    places.write_text(original.replace("杭州", "杭洲"), encoding="utf-8", newline="\n")
    with pytest.raises(CatalogError) as checksum:
        load(directory)
    assert checksum.value.code == PLACE_DATASET_INVALID

    places.write_text(original, encoding="utf-8", newline="\n")
    (directory / "LICENSE.txt").unlink()
    with pytest.raises(CatalogError) as missing:
        load(directory)
    assert missing.value.code in (PLACE_DATASET_INVALID, "PLACE_DATASET_MISSING")


def test_a_broken_hierarchy_reference_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "package"
    build_package(directory)
    (directory / "hierarchy.jsonl").write_text(
        json.dumps({"child": "city-hangzhou", "parent": "city-atlantis"}) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    rewrite_manifest(directory, lambda m: _resign(m, directory))

    with pytest.raises(CatalogError) as error:
        load(directory)

    assert error.value.code == PLACE_DATASET_INVALID
    assert "unknown place" in error.value.message


def test_a_hierarchy_cycle_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "package"
    build_package(directory)
    (directory / "hierarchy.jsonl").write_text(
        json.dumps({"child": "city-hangzhou", "parent": "city-shanghai"})
        + "\n"
        + json.dumps({"child": "city-shanghai", "parent": "city-hangzhou"})
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    rewrite_manifest(directory, lambda m: _resign(m, directory))

    with pytest.raises(CatalogError) as error:
        load(directory)

    assert error.value.code == PLACE_DATASET_INVALID
    assert "cycle" in error.value.message


def _resign(manifest: dict, directory: Path) -> None:
    """Recompute every file's size/checksum and the content digest after a fixture edit."""
    import hashlib

    files = []
    for item in manifest["files"]:
        data = (directory / item["relative_path"]).read_bytes()
        item = dict(item)
        item["sha256"] = hashlib.sha256(data).hexdigest()
        item["byte_size"] = len(data)
        files.append(item)
    manifest["files"] = files
    declared = [dataset_module.DatasetFile(**item) for item in files]
    manifest["content_sha256"] = dataset_module.declared_content_sha256(declared)


def test_the_record_count_must_match_the_places_file(tmp_path: Path) -> None:
    directory = tmp_path / "package"
    build_package(directory)
    rewrite_manifest(directory, lambda m: _set_record_count(m, 99))

    with pytest.raises(CatalogError) as error:
        load(directory)

    assert error.value.code == PLACE_DATASET_INVALID
    assert "record_count" in error.value.message


def _set_record_count(manifest: dict, value: int) -> None:
    manifest["coverage"]["record_count"] = int(value)


def test_nearby_cities_are_ranked_deterministically(tmp_path: Path) -> None:
    directory = tmp_path / "package"
    build_package(directory)
    content = load(directory)

    at_hangzhou = dataset_module.nearby_cities(
        content, *HANGZHOU_POSITION, max_candidates=3, max_distance_m=200000.0
    )
    assert [item["display_name"] for item in at_hangzhou] == ["杭州", "苏州", "上海"]
    assert [item["rank"] for item in at_hangzhou] == [1, 2, 3]
    assert all(item["disposition"] == "SUGGESTION_ONLY" for item in at_hangzhou)
    assert all(item["match_basis"] == "NEARBY_CITY_POINT" for item in at_hangzhou)
    assert all(item["confidence_basis"] == "DISTANCE_ONLY" for item in at_hangzhou)
    assert at_hangzhou[0]["ancestor_names"] == ["中国"]
    assert at_hangzhou[0]["distance_m"] < at_hangzhou[1]["distance_m"]

    between = dataset_module.nearby_cities(
        content, *BETWEEN_POSITION, max_candidates=2, max_distance_m=200000.0
    )
    assert [item["dataset_place_id"] for item in between] == ["city-suzhou", "city-hangzhou"]

    capped = dataset_module.nearby_cities(
        content, *HANGZHOU_POSITION, max_candidates=1, max_distance_m=200000.0
    )
    assert len(capped) == 1


def test_a_sea_position_and_a_gulf_position_have_no_candidates(tmp_path: Path) -> None:
    directory = tmp_path / "package"
    build_package(directory)
    content = load(directory)

    assert (
        dataset_module.nearby_cities(
            content, *SEA_POSITION, max_candidates=3, max_distance_m=200000.0
        )
        == []
    )
    assert (
        dataset_module.nearby_cities(content, 0.0, 0.0, max_candidates=3, max_distance_m=200000.0)
        == []
    )


def test_the_haversine_distance_matches_a_known_value() -> None:
    # Hangzhou -> Shanghai is about 165 km; the exact value is stable for the fixture.
    distance = dataset_module.haversine_m(30.2741, 120.1551, 31.2304, 121.4737)
    assert 160_000.0 < distance < 172_000.0
    assert dataset_module.haversine_m(30.0, 120.0, 30.0, 120.0) == pytest.approx(0.0)


def test_two_cities_may_share_a_name_and_stay_distinguishable(tmp_path: Path) -> None:
    same_name = {
        "dataset_place_id": "city-hangzhou-2",
        "kind": "city",
        "display_name": "杭州",
        "country_code": "CN",
        "latitude": 30.30,
        "longitude": 120.20,
        "parent_dataset_place_id": "country-cn",
    }
    directory = tmp_path / "package"
    build_package(
        directory, spec=mini_spec(dataset_id="geo-same-name", places=(*MINI_PLACES, same_name))
    )

    candidates = dataset_module.nearby_cities(
        load(directory), 30.29, 120.18, max_candidates=10, max_distance_m=50_000.0
    )

    hangzhous = [item for item in candidates if item["display_name"] == "杭州"]
    assert len(hangzhous) == 2
    assert {item["dataset_place_id"] for item in hangzhous} == {
        "city-hangzhou",
        "city-hangzhou-2",
    }
    # The ranking is (distance, dataset_place_id): never ambiguous for the same package.
    assert [item["rank"] for item in candidates] == list(range(1, len(candidates) + 1))
    assert [item["distance_m"] for item in candidates] == sorted(
        item["distance_m"] for item in candidates
    )
    # A duplicate test of the same input proves the ordering itself, not hash seeding.
    again = dataset_module.nearby_cities(
        load(directory), 30.29, 120.18, max_candidates=10, max_distance_m=50_000.0
    )
    assert again == candidates


def test_the_distance_limit_is_inclusive(tmp_path: Path) -> None:
    directory = tmp_path / "package"
    build_package(directory)
    content = load(directory)
    exact = dataset_module.haversine_m(30.2741, 120.1551, 31.2304, 121.4737)

    inside = dataset_module.nearby_cities(
        content, 30.2741, 120.1551, max_candidates=10, max_distance_m=exact
    )
    outside = dataset_module.nearby_cities(
        content, 30.2741, 120.1551, max_candidates=10, max_distance_m=exact - 1.0
    )

    assert "上海" in [item["display_name"] for item in inside]
    assert "上海" not in [item["display_name"] for item in outside]
