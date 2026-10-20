from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.places import choices as place_choices
from photo_search.places.contracts import normalize_name


class _CountingCatalog:
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self.read_calls = 0
        self.rows_read = 0
        self.alias_queries = 0
        self.mapping_queries = 0
        self.read_ms = 0.0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.catalog, name)

    def _read(self, method: str, sql: str, parameters: tuple[Any, ...] = ()) -> Any:
        started = time.perf_counter()
        result = getattr(self.catalog, method)(sql, parameters)
        self.read_ms += (time.perf_counter() - started) * 1000
        self.read_calls += 1
        if method == "read_all":
            self.rows_read += len(result)
        elif result is not None:
            self.rows_read += 1
        normalized = sql.casefold()
        if "place_aliases" in normalized:
            self.alias_queries += 1
        if "cn_admin_place_refs" in normalized:
            self.mapping_queries += 1
        return result

    def read_one(self, sql: str, parameters: tuple[Any, ...] = ()) -> Any:
        return self._read("read_one", sql, parameters)

    def read_all(self, sql: str, parameters: tuple[Any, ...] = ()) -> Any:
        return self._read("read_all", sql, parameters)


def _synthetic_catalog(osm_count: int, geo_count: int) -> Catalog:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        "CREATE TABLE geo_datasets(dataset_id TEXT,provider TEXT,package_version TEXT,state TEXT);"
        "CREATE TABLE active_geo_dataset(singleton INTEGER,dataset_id TEXT);"
        "CREATE TABLE cn_admin_ids(admin_id INTEGER,external_id TEXT);"
        "CREATE TABLE cn_dataset_regions(dataset_id TEXT,admin_id INTEGER);"
        "CREATE TABLE cn_admin_place_refs(admin_id INTEGER,place_id INTEGER);"
        "CREATE TABLE geo_dataset_place_refs(dataset_id TEXT,external_id TEXT,place_id INTEGER,provider TEXT);"
        "CREATE TABLE places(place_id INTEGER,kind TEXT,canonical_name TEXT,normalized_name TEXT,parent_place_id INTEGER,lifecycle TEXT);"
        "CREATE TABLE place_aliases(alias_id INTEGER,place_id INTEGER,text TEXT,normalized TEXT,lifecycle TEXT);"
        "CREATE TABLE cn_legacy_links(place_id INTEGER,city_id INTEGER,province_id INTEGER,dataset_id TEXT);"
        "CREATE TABLE place_external_refs(provider TEXT,external_id TEXT,place_id INTEGER);"
        "INSERT INTO geo_datasets VALUES('synthetic','geonames','test-v1','ACTIVE');"
        "INSERT INTO active_geo_dataset VALUES(1,'synthetic');"
    )
    connection.executemany(
        "INSERT INTO cn_admin_ids VALUES(?,?)",
        [(index, f"osm:{index}") for index in range(1, osm_count + 1)],
    )
    connection.executemany(
        "INSERT INTO cn_admin_place_refs VALUES(?,?)",
        [(index, index) for index in range(1, osm_count + 1)],
    )
    connection.executemany(
        "INSERT INTO places VALUES(?,?,?, ?,NULL,'ACTIVE')",
        [(osm_count + index, "city", f"City {index}", normalize_name(f"City {index}"))
         for index in range(1, geo_count + 1)],
    )
    connection.executemany(
        "INSERT INTO geo_dataset_place_refs VALUES('synthetic',?,?, 'geonames')",
        [(f"geo:{index}", osm_count + index) for index in range(1, geo_count + 1)],
    )
    connection.executemany(
        "INSERT INTO place_aliases VALUES(?,?,?,?, 'ACTIVE')",
        [(index, osm_count + index,
          "sunset" if index == 1 else f"alias-{index}",
          normalize_name("sunset" if index == 1 else f"alias-{index}"))
         for index in range(1, geo_count + 1)],
    )
    return Catalog(connection, db_path=Path(":memory:"))


def test_synthetic_place_choices_batching_preserves_order_and_aliases(monkeypatch, capsys) -> None:
    osm_count, geo_count = 400, 5000
    catalog = _synthetic_catalog(osm_count, geo_count)
    monkeypatch.setattr(
        place_choices.cn_facets,
        "choices",
        lambda _catalog: [
            {"kind": "city", "key": f"cn:{index}", "name": f"OSM {index}", "aliases": []}
            for index in range(1, osm_count + 1)
        ],
    )
    measured = _CountingCatalog(catalog)
    durations: list[float] = []
    last_result: list[dict[str, Any]] = []
    last_counts: dict[str, int] = {}
    for _ in range(3):
        measured.read_calls = measured.rows_read = 0
        measured.alias_queries = measured.mapping_queries = 0
        measured.read_ms = 0.0
        started = time.perf_counter()
        last_result = place_choices.choices(measured, text="sunset")
        durations.append(round((time.perf_counter() - started) * 1000, 3))
        last_counts = {
            "read_calls": measured.read_calls,
            "rows_read": measured.rows_read,
            "alias_queries": measured.alias_queries,
            "mapping_queries": measured.mapping_queries,
            "catalog_read_ms": round(measured.read_ms, 3),
        }
    assert len(last_result) == 1
    assert last_result[0]["aliases"] == ["sunset"]
    assert last_counts["alias_queries"] <= 13  # ceil(5000 / 400), bounded parameter chunks
    assert last_counts["mapping_queries"] == 1
    assert last_counts["read_calls"] < 25
    catalog.connection.execute(
        "INSERT INTO place_aliases VALUES(?,?,?,?, 'ACTIVE')",
        (geo_count + 1, osm_count + 1, "sunset second spelling", normalize_name("sunset second spelling")),
    )
    catalog.connection.execute(
        "INSERT INTO place_aliases VALUES(?,?,?,?, 'RETIRED')",
        (geo_count + 2, osm_count + 1, "retired spelling", normalize_name("retired spelling")),
    )
    all_candidates = place_choices.choices(measured)
    assert len(all_candidates) == osm_count + geo_count
    assert [item["key"] for item in all_candidates[:2]] == ["cn:1", "cn:2"]
    expected_geo_ids = [
        str(row[0]) for row in catalog.connection.execute(
            "SELECT place_id FROM places WHERE kind='city' ORDER BY length(canonical_name) DESC,place_id"
        ).fetchall()
    ]
    assert [item["key"] for item in all_candidates[osm_count:]] == expected_geo_ids
    matched_geo = next(item for item in all_candidates[osm_count:] if item["key"] == "401")
    assert matched_geo["aliases"] == ["sunset", "sunset second spelling"]
    report = json.dumps({
        "scenario": {"osm_candidates": osm_count, "geonames_candidates": geo_count},
        "runs_ms": durations,
        "p50_ms": sorted(durations)[1],
        "p95_ms": sorted(durations)[-1],
        "final_counts": last_counts,
        "matched_candidates": len(last_result),
    }, sort_keys=True)
    artifact = Path("test-results/P4-SEARCH-PIPELINE-PERF-A")
    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / "place-choices-optimized.json").write_text(report + "\n", encoding="utf-8")
    catalog.close()
    capsys.readouterr()
    print(report)


def test_text_choices_keep_linked_aliases_and_identity_with_light_candidates(monkeypatch) -> None:
    catalog = _synthetic_catalog(2, 2)
    catalog.connection.execute(
        "INSERT INTO cn_dataset_regions VALUES('synthetic',1)"
    )
    catalog.connection.execute(
        "INSERT INTO cn_legacy_links VALUES(3,1,NULL,'synthetic')"
    )
    catalog.connection.execute(
        "UPDATE place_aliases SET text='SunSet',normalized='sunset' WHERE place_id=3"
    )
    catalog.connection.execute(
        "UPDATE place_aliases SET text='ＡＢＣ',normalized='abc' WHERE place_id=4"
    )
    monkeypatch.setattr(
        place_choices.cn_facets,
        "choices",
        lambda _catalog: [
            {"kind": "city", "key": f"cn:{index}", "name": f"OSM {index}", "aliases": []}
            for index in (1, 2)
        ],
    )
    metrics: dict[str, Any] = {}
    linked = place_choices.choices(catalog, text="sunset", perf_metrics=metrics)
    assert [item["key"] for item in linked] == ["cn:1"]
    assert linked[0]["aliases"] == ["City 1", "SunSet"]

    normalized = place_choices.choices(catalog, text="abc")
    assert [(item["key"], item["aliases"]) for item in normalized] == [("4", ["ＡＢＣ"])]

    complete = place_choices.choices(catalog, include_parent_identity=True)
    assert [item["key"] for item in complete] == ["cn:1", "cn:2", "4"]
    assert complete[0]["selection_ref"] == "osm|synthetic|osm:1"
    assert complete[1]["dataset_version"] == "test-v1"
    assert metrics["place_full_candidates"] == 3
    assert metrics["place_matched_candidates"] == 1
    assert metrics["place_candidate_construct_ms"] >= 0
    assert metrics["place_name_normalize_ms"] >= 0
    assert metrics["place_name_match_ms"] >= 0
    assert metrics["place_link_merge_ms"] >= 0
    assert metrics["place_python_other_ms"] >= 0
    assert metrics["place_geo_candidate_objects"] == 0
    catalog.close()


def test_text_choices_preserve_all_same_name_geo_candidates(monkeypatch) -> None:
    catalog = _synthetic_catalog(0, 2)
    catalog.connection.execute(
        "UPDATE places SET canonical_name='Shared City',normalized_name='shared city'"
    )
    monkeypatch.setattr(place_choices.cn_facets, "choices", lambda _catalog: [])
    metrics: dict[str, Any] = {}
    matches = place_choices.choices(catalog, text="shared city", perf_metrics=metrics)
    assert [item["source_id"] for item in matches] == ["geo:1", "geo:2"]
    assert [item["selection_ref"] for item in matches] == [
        "geonames|synthetic|geo:1", "geonames|synthetic|geo:2",
    ]
    assert metrics["place_full_candidates"] == 2
    assert metrics["place_geo_candidate_objects"] == 2
    catalog.close()


def test_stored_place_normalization_matches_compact_search_semantics(monkeypatch) -> None:
    spellings = [" Ａ  B  C ", "Straße", "İstanbul", "西湖\t风景区"]
    for spelling in spellings:
        normalized = normalize_name(spelling)
        assert place_choices._compact(spelling) == normalized.replace(" ", "")

    catalog = _synthetic_catalog(0, len(spellings))
    for index, spelling in enumerate(spellings, start=1):
        catalog.connection.execute(
            "UPDATE places SET canonical_name=?,normalized_name=? WHERE place_id=?",
            (spelling, normalize_name(spelling), index),
        )
    monkeypatch.setattr(place_choices.cn_facets, "choices", lambda _catalog: [])
    for index, spelling in enumerate(spellings, start=1):
        query = f"trip photos from {spelling}"
        rows = place_choices.choices(catalog, text=query)
        assert [row["source_id"] for row in rows] == [f"geo:{index}"]
    catalog.close()


def test_text_prefilter_returns_only_matches_and_diagnostics_do_not_change_results(monkeypatch) -> None:
    catalog = _synthetic_catalog(3, 100)
    catalog.connection.executemany(
        "INSERT INTO place_aliases VALUES(?,?,?,?, 'ACTIVE')",
        [(10_000 + index, 4 + index, "Many Match City", "many match city")
         for index in range(100)],
    )
    monkeypatch.setattr(
        place_choices.cn_facets,
        "choices",
        lambda _catalog: [
            {"kind": "city", "key": f"cn:{i}", "name": f"OSM City {i}", "aliases": []}
            for i in range(1, 4)
        ],
    )
    plain = place_choices.choices(catalog, text="many match city")
    metrics: dict[str, Any] = {}
    diagnosed = place_choices.choices(catalog, text="many match city", perf_metrics=metrics)

    assert [(row["key"], row["aliases"]) for row in diagnosed] == [
        (row["key"], row["aliases"]) for row in plain
    ]
    assert len(plain) == 100
    assert metrics["place_geonames_rows"] == 100
    assert metrics["place_sql_rows"] < 700
    assert metrics["place_sql_queries"] < 15
    catalog.close()


def test_text_prefilter_preserves_legacy_alias_and_suppresses_geo_duplicate(monkeypatch) -> None:
    catalog = _synthetic_catalog(2, 2)
    catalog.connection.execute("INSERT INTO cn_dataset_regions VALUES('synthetic',1)")
    catalog.connection.execute("INSERT INTO cn_legacy_links VALUES(3,1,NULL,'synthetic')")
    monkeypatch.setattr(
        place_choices.cn_facets,
        "choices",
        lambda _catalog: [
            {"kind": "city", "key": f"cn:{i}", "name": f"OSM City {i}", "aliases": []}
            for i in (1, 2)
        ],
    )

    matches = place_choices.choices(catalog, text="sunset")
    assert [item["key"] for item in matches] == ["cn:1"]
    assert matches[0]["aliases"] == ["City 1", "sunset"]
    assert place_choices.choices(catalog, text="  ") == place_choices.choices(catalog)
    catalog.close()


def test_text_prefilter_uses_only_index_verified_historical_fallback(monkeypatch) -> None:
    catalog = _synthetic_catalog(0, 1)
    catalog.connection.execute("DELETE FROM geo_dataset_place_refs WHERE external_id='geo:1'")
    catalog.connection.execute(
        "INSERT INTO place_external_refs VALUES('geonames','geo:1',1)"
    )
    monkeypatch.setattr(place_choices.cn_facets, "choices", lambda _catalog: [])
    monkeypatch.setattr(
        place_choices,
        "_read_index",
        lambda *_args: ({"city_to_admin1": {"geo:1": "admin:1"}}, "stamp", "geonames", True),
    )

    verified = place_choices.choices(catalog, text="geo city 1", data_root=object())
    assert [item["source_id"] for item in verified] == ["geo:1"]

    monkeypatch.setattr(
        place_choices,
        "_read_index",
        lambda *_args: ({"city_to_admin1": {}}, "stamp", "geonames", True),
    )
    assert place_choices.choices(catalog, text="geo city 1", data_root=object()) == []
    catalog.close()


def test_text_prefilter_keeps_verified_fallback_link_alias(monkeypatch) -> None:
    catalog = _synthetic_catalog(2, 2)
    catalog.connection.execute("DELETE FROM geo_dataset_place_refs WHERE place_id=3")
    catalog.connection.execute(
        "INSERT INTO place_external_refs VALUES('geonames','geo:1',3)"
    )
    catalog.connection.execute("INSERT INTO cn_dataset_regions VALUES('synthetic',1)")
    catalog.connection.execute("INSERT INTO cn_legacy_links VALUES(3,1,NULL,'synthetic')")
    monkeypatch.setattr(place_choices.cn_facets, "choices", lambda _catalog: [
        {"kind": "city", "key": "cn:1", "name": "OSM City 1", "aliases": []},
    ])
    monkeypatch.setattr(
        place_choices,
        "_read_index",
        lambda *_args: ({"city_to_admin1": {"geo:1": "admin:1"}}, "stamp", "geonames", True),
    )

    matches = place_choices.choices(catalog, text="sunset", data_root=object())
    assert [item["key"] for item in matches] == ["cn:1"]
    assert matches[0]["aliases"] == ["City 1", "sunset"]
    catalog.close()


def test_diagnostic_candidate_count_excludes_each_duplicate_linked_fallback_ref(monkeypatch) -> None:
    catalog = _synthetic_catalog(1, 1)
    catalog.connection.execute("DELETE FROM geo_dataset_place_refs WHERE place_id=2")
    catalog.connection.executemany(
        "INSERT INTO place_external_refs VALUES('geonames',?,2)",
        [("geo:1",), ("geo:1-alt",)],
    )
    catalog.connection.execute("INSERT INTO cn_dataset_regions VALUES('synthetic',1)")
    catalog.connection.execute("INSERT INTO cn_legacy_links VALUES(2,1,NULL,'synthetic')")
    monkeypatch.setattr(place_choices.cn_facets, "choices", lambda _catalog: [
        {"kind": "city", "key": "cn:1", "name": "OSM City 1", "aliases": []},
    ])
    monkeypatch.setattr(
        place_choices,
        "_read_index",
        lambda *_args: ({"city_to_admin1": {"geo:1": "admin:1", "geo:1-alt": "admin:1"}},
                        "stamp", "geonames", True),
    )

    metrics = {}
    results = place_choices.choices(catalog, text="sunset", data_root=object(), perf_metrics=metrics)

    assert [item["key"] for item in results] == ["cn:1"]
    assert metrics["place_full_candidates"] == 1
    catalog.close()


def test_text_prefilter_observes_alias_edits_and_active_dataset_switch(monkeypatch) -> None:
    catalog = _synthetic_catalog(0, 2)
    monkeypatch.setattr(place_choices.cn_facets, "choices", lambda _catalog: [])
    assert [row["source_id"] for row in place_choices.choices(catalog, text="sunset")] == ["geo:1"]

    catalog.connection.execute(
        "UPDATE place_aliases SET text='dusk',normalized='dusk' WHERE place_id=1"
    )
    assert place_choices.choices(catalog, text="sunset") == []
    assert [row["source_id"] for row in place_choices.choices(catalog, text="dusk")] == ["geo:1"]

    catalog.connection.execute(
        "INSERT INTO geo_datasets VALUES('next','geonames','next-v1','ACTIVE')"
    )
    catalog.connection.execute("UPDATE active_geo_dataset SET dataset_id='next'")
    catalog.connection.execute(
        "INSERT INTO places VALUES(3,'city','New Kyoto','new kyoto',NULL,'ACTIVE')"
    )
    catalog.connection.execute(
        "INSERT INTO geo_dataset_place_refs VALUES('next','geo:new',3,'geonames')"
    )
    assert [row["source_id"] for row in place_choices.choices(catalog, text="new kyoto")] == ["geo:new"]
    assert place_choices.choices(catalog, text="dusk") == []
    catalog.close()
