"""P4-02Y-P: convert local GeoNames dumps into the package spec the builder owns.

``tools/places/prepare_geonames.py`` is an explicit preparation tool, not product code: it
never changes the package format, the resolver or the catalog. These tests drive the real
converter over a tiny synthetic raw dump so the country filter, the naming priority, the
alias rule and the hierarchy stay pinned without shipping GeoNames bytes into the repository.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from photo_search.catalog import schema as schema_module
from photo_search.geo import dataset as dataset_module

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = REPO_ROOT / "tools" / "places" / "prepare_geonames.py"

COUNTRY_HEADER = "\t".join(
    [
        "ISO",
        "ISO3",
        "ISO-Numeric",
        "fips",
        "Country",
        "Capital",
        "Area(in sq km)",
        "Population",
        "Continent",
        "tld",
        "CurrencyCode",
        "CurrencyName",
        "Phone",
        "Postal Code Format",
        "Postal Code Regex",
        "Languages",
        "geonameid",
        "neighbours",
        "EquivalentFipsCode",
    ]
)


def _country_row(code: str, name: str, geonameid: int) -> str:
    return "\t".join(
        [
            code,
            code,
            "0",
            "CH",
            name,
            "capital",
            "1000",
            "1000000",
            "AS",
            ".xx",
            "XXX",
            "Currency",
            "000",
            "",
            "",
            "zh",
            str(geonameid),
            "",
            "",
        ]
    )


def _city_row(
    geonameid: int,
    name: str,
    ascii_name: str,
    latitude: float,
    longitude: float,
    code: str,
    population: int,
) -> str:
    return "\t".join(
        [
            str(geonameid),
            name,
            ascii_name,
            "",
            str(latitude),
            str(longitude),
            "P",
            "PPLA",
            code,
            "",
            "01",
            "",
            "",
            "",
            str(population),
            "",
            "10",
            "Asia/Shanghai",
            "2026-09-01",
        ]
    )


def _alternate_row(
    alternate_id: int,
    geonameid: int,
    language: str,
    text: str,
    *,
    preferred: bool = False,
    historic: bool = False,
    from_year: str = "",
    to_year: str = "",
) -> str:
    return "\t".join(
        [
            str(alternate_id),
            str(geonameid),
            language,
            text,
            "1" if preferred else "0",
            "0",
            "0",
            "1" if historic else "0",
            from_year,
            to_year,
        ]
    )


@pytest.fixture()
def tool_module() -> Any:
    spec = importlib.util.spec_from_file_location("p4_02y_p_prepare_geonames", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def raw_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "raw"
    directory.mkdir()
    (directory / "countryInfo.txt").write_text(
        "\n".join(
            [
                COUNTRY_HEADER,
                "# the GeoNames country info export",
                _country_row("CN", "China", 1814991),
                _country_row("JP", "Japan", 1861060),
                _country_row("BT", "Bhutan", 1252634),
                _country_row("SG", "Singapore", 1880251),
                _country_row("MV", "Maldives", 1282024),
                _country_row("MN", "Mongolia", 2029969),
                _country_row("US", "United States", 6252001),
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (directory / "cities500.txt").write_text(
        "\n".join(
            [
                _city_row(1808926, "Hangzhou", "Hangzhou", 30.29365, 120.16142, "CN", 6241971),
                _city_row(1796236, "Shanghai", "Shanghai", 31.22222, 121.45806, "CN", 22315474),
                _city_row(1252416, "Thimphu", "Thimphu", 27.47165, 89.64128, "BT", 0),
                _city_row(1850147, "Tokyo", "Tokyo", 35.6895, 139.69171, "JP", 8336599),
                _city_row(1880252, "Singapore", "Singapore", 1.28967, 103.85007, "SG", 5638700),
                _city_row(1282027, "Male", "Male", 4.1748, 73.50888, "MV", 103693),
                _city_row(1815286, "Chengdu", "Chengdu", 30.66667, 104.06667, "CN", 20937757),
                _city_row(2028462, "Ulaanbaatar", "Ulaanbaatar", 47.90771, 106.88324, "MN", 844818),
                _city_row(
                    5128581, "New York City", "New York City", 40.71427, -74.00597, "US", 8175133
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (directory / "alternateNamesV2.txt").write_text(
        "\n".join(
            [
                _alternate_row(1, 1808926, "zh-Hans", "杭州", preferred=True),
                _alternate_row(2, 1808926, "zh", "杭州"),
                _alternate_row(3, 1808926, "en", "Hangzhou"),
                _alternate_row(4, 1808926, "fr", "Hang-Tcheou"),
                _alternate_row(5, 1796236, "zh", "上海"),
                _alternate_row(6, 1796236, "en", "Shanghai"),
                _alternate_row(7, 1850147, "en", "Tokyo"),
                _alternate_row(8, 1850147, "ja", "東京"),
                _alternate_row(9, 1252416, "zh-Hans", "廷布", preferred=True),
                _alternate_row(10, 1252416, "en", "Thimphu"),
                _alternate_row(11, 5128581, "zh-Hans", "纽约", preferred=True),
                _alternate_row(12, 1814991, "zh-Hans", "中国", preferred=True),
                _alternate_row(13, 1814991, "en", "China"),
                _alternate_row(14, 1861060, "en", "Japan"),
                _alternate_row(15, 999999999, "zh-Hans", "不存在的城市"),
                # The best-tagged copy of a repeated text must win, not the first one seen.
                _alternate_row(16, 1880252, "en", "Singapore"),
                _alternate_row(17, 1880252, "zh", "新加坡"),
                _alternate_row(18, 1880252, "zh-TW", "新加坡"),
                _alternate_row(19, 1880252, "zh-CN", "新加坡", preferred=True),
                _alternate_row(20, 1880252, "zh", "Dānmǎxī"),
                # Traditional spellings tagged as plain "zh" lose to the simplified one.
                _alternate_row(21, 1282027, "en", "Malé"),
                _alternate_row(22, 1282027, "zh", "馬累"),
                _alternate_row(23, 1282027, "zh", "瑪律"),
                _alternate_row(24, 1282027, "zh", "马累"),
                # The base form of the same place beats an unrelated equal-length name.
                _alternate_row(25, 1815286, "en", "Chengdu"),
                _alternate_row(26, 1815286, "zh", "成都"),
                _alternate_row(27, 1815286, "zh", "成都市"),
                _alternate_row(28, 1815286, "zh", "天府"),
                # A historical Chinese name must not win over the current one.
                _alternate_row(29, 2028462, "en", "Ulaanbaatar", preferred=True),
                _alternate_row(
                    30, 2028462, "zh", "库伦", historic=True, from_year="1778", to_year="1924"
                ),
                _alternate_row(31, 2028462, "zh", "乌兰巴托"),
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return directory


def _places(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["dataset_place_id"]): row for row in spec["places"]}


def test_country_filter_and_place_ids(tool_module: Any, raw_dir: Path) -> None:
    spec = tool_module.build_spec(raw_dir, countries=("CN", "JP", "BT", "SG", "MV"))
    places = _places(spec)
    assert spec["coverage"]["country_codes"] == ["BT", "CN", "JP", "MV", "SG"]
    assert spec["dataset_id"] == "geonames-asia-cities500-v1"
    assert {
        "geonames-country-cn",
        "geonames-country-jp",
        "geonames-country-bt",
        "geonames-country-sg",
        "geonames-country-mv",
        "geonames-city-1808926",
        "geonames-city-1796236",
        "geonames-city-1252416",
        "geonames-city-1850147",
    } <= set(places)
    assert all("纽约" != str(row.get("text", "")) for row in spec["aliases"])
    assert places["geonames-city-1252416"]["population"] == 0
    assert places["geonames-city-1808926"]["parent_dataset_place_id"] == "geonames-country-cn"
    assert spec["source"]["snapshot"] == "2026-09-29" or spec["source"]["snapshot"] >= "2020-01-01"


def test_naming_priority(tool_module: Any, raw_dir: Path) -> None:
    places = _places(
        tool_module.build_spec(raw_dir, countries=("CN", "JP", "BT", "SG", "MV", "MN"))
    )
    assert places["geonames-city-1808926"]["display_name"] == "杭州"
    assert places["geonames-city-1796236"]["display_name"] == "上海"
    assert places["geonames-city-1850147"]["display_name"] == "Tokyo"
    assert places["geonames-city-1252416"]["display_name"] == "廷布"
    assert places["geonames-country-cn"]["display_name"] == "中国"
    assert places["geonames-country-jp"]["display_name"] == "Japan"
    # Same text under a better tag wins over the first copy seen in the dump.
    assert places["geonames-city-1880252"]["display_name"] == "新加坡"
    # Simplified spelling wins over the traditional one inside the Chinese class.
    assert places["geonames-city-1282027"]["display_name"] == "马累"
    # The base form of the place wins over an unrelated equal-length name.
    assert places["geonames-city-1815286"]["display_name"] == "成都"
    # A historical Chinese name (flagged in the dump) loses to the current one.
    assert places["geonames-city-2028462"]["display_name"] == "乌兰巴托"


def test_snapshot_can_be_named(tool_module: Any, raw_dir: Path) -> None:
    spec = tool_module.build_spec(raw_dir, countries=("CN",), snapshot="2026-09-15")
    assert spec["source"]["snapshot"] == "2026-09-15"


def test_alias_selection(tool_module: Any, raw_dir: Path) -> None:
    spec = tool_module.build_spec(raw_dir, countries=("CN", "JP", "BT"))
    by_place: dict[str, list[dict[str, str]]] = {}
    for row in spec["aliases"]:
        by_place.setdefault(str(row["dataset_place_id"]), []).append(row)
    hangzhou = by_place["geonames-city-1808926"]
    assert any(row["text"] == "Hangzhou" and row.get("language") == "en" for row in hangzhou)
    assert all(row["text"] != "杭州" for row in hangzhou)
    assert all("Hang-Tcheou" != row["text"] for row in hangzhou)
    assert all(row["language"] in ("zh", "zh-Hans", "en") for row in hangzhou)


def test_alias_cap(tool_module: Any, raw_dir: Path) -> None:
    spec = tool_module.build_spec(raw_dir, countries=("CN", "JP", "BT"), max_aliases_per_place=1)
    counts: dict[str, int] = {}
    for row in spec["aliases"]:
        counts[str(row["dataset_place_id"])] = counts.get(str(row["dataset_place_id"]), 0) + 1
    assert counts and max(counts.values()) == 1


def test_spec_is_deterministic(tool_module: Any, raw_dir: Path) -> None:
    first = tool_module.build_spec(raw_dir, countries=("CN", "JP", "BT"))
    second = tool_module.build_spec(raw_dir, countries=("CN", "JP", "BT"))
    first["build"]["generated_at"] = 0
    second["build"]["generated_at"] = 0
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_existing_builder_accepts_the_spec(tool_module: Any, raw_dir: Path, tmp_path: Path) -> None:
    spec = tool_module.build_spec(raw_dir, countries=("CN", "JP", "BT"))
    package_dir = tmp_path / "package"
    dataset_module.build_package(spec, package_dir)
    content = dataset_module.load_content(
        package_dir, supported_schema_version=int(schema_module.SUPPORTED_SCHEMA_VERSION)
    )
    assert content.dataset_id == "geonames-asia-cities500-v1"
    assert {item.relative_path for item in content.files} >= {
        "places.jsonl",
        "aliases.jsonl",
        "hierarchy.jsonl",
        "LICENSE.txt",
    }
    candidates = dataset_module.nearby_cities(
        content, 30.29365, 120.16142, max_candidates=3, max_distance_m=200_000.0
    )
    assert [row["display_name"] for row in candidates][:1] == ["杭州"]
    assert candidates[0]["ancestor_names"] == ["中国"]
    assert (
        dataset_module.nearby_cities(content, 0.0, 0.0, max_candidates=3, max_distance_m=200_000.0)
        == []
    )
