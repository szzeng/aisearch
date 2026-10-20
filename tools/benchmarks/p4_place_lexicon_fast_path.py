"""Isolated benchmark for P4-SEARCH-PLACE-LEXICON-FAST-PATH.

Runs the committed implementation and the working-tree implementation against the
same in-memory synthetic catalog. It never opens application data or photo files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import time
import tracemalloc
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from photo_search.catalog.db import Catalog
from photo_search.places import choices as live_choices
from photo_search.places.contracts import normalize_name

ROOT = Path(__file__).resolve().parents[2]


class Counts:
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self.queries = 0
        self.rows = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.catalog, name)

    def read_all(self, sql: str, parameters: Any = ()) -> Any:
        result = self.catalog.read_all(sql, parameters)
        self.queries += 1
        self.rows += len(result)
        return result

    def read_one(self, sql: str, parameters: Any = ()) -> Any:
        result = self.catalog.read_one(sql, parameters)
        self.queries += 1
        self.rows += int(result is not None)
        return result


def make_catalog(osm_count: int = 3013, geo_count: int = 45217,
                 alias_count: int = 26520, link_count: int = 274) -> Catalog:
    import sqlite3
    from pathlib import Path

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE geo_datasets(dataset_id TEXT,provider TEXT,package_version TEXT,state TEXT);
        CREATE TABLE active_geo_dataset(singleton INTEGER,dataset_id TEXT);
        CREATE TABLE cn_admin_ids(admin_id INTEGER,external_id TEXT);
        CREATE TABLE cn_dataset_regions(dataset_id TEXT,admin_id INTEGER,kind TEXT,name TEXT,
                                        aliases_json TEXT,province_id INTEGER,city_id INTEGER);
        CREATE TABLE cn_admin_place_refs(admin_id INTEGER,place_id INTEGER);
        CREATE TABLE geo_dataset_place_refs(dataset_id TEXT,external_id TEXT,place_id INTEGER,provider TEXT,
                                           PRIMARY KEY(dataset_id,provider,external_id),UNIQUE(dataset_id,place_id));
        CREATE TABLE places(place_id INTEGER PRIMARY KEY,kind TEXT,canonical_name TEXT,normalized_name TEXT,
                            parent_place_id INTEGER,lifecycle TEXT);
        CREATE TABLE place_aliases(alias_id INTEGER PRIMARY KEY,place_id INTEGER,text TEXT,normalized TEXT,
                                   lifecycle TEXT);
        CREATE TABLE cn_legacy_links(place_id INTEGER,city_id INTEGER,province_id INTEGER,dataset_id TEXT,
                                     PRIMARY KEY(dataset_id,place_id));
        CREATE TABLE place_external_refs(provider TEXT,external_id TEXT,place_id INTEGER,
                                         PRIMARY KEY(provider,external_id));
        CREATE INDEX idx_place_external_refs_place ON place_external_refs(place_id);
        CREATE INDEX idx_geo_refs_place ON geo_dataset_place_refs(place_id,dataset_id);
        CREATE INDEX idx_places_name ON places(normalized_name);
        CREATE INDEX idx_aliases_place ON place_aliases(place_id,lifecycle);
        CREATE INDEX idx_aliases_norm ON place_aliases(normalized,lifecycle);
        INSERT INTO geo_datasets VALUES('synthetic','geonames','synthetic-v1','ACTIVE');
        INSERT INTO active_geo_dataset VALUES(1,'synthetic');
    """)
    db.executemany("INSERT INTO cn_admin_ids VALUES(?,?)",
                   [(i, f"relation/{i}") for i in range(1, osm_count + 1)])
    db.executemany("INSERT INTO cn_admin_place_refs VALUES(?,?)",
                   [(i, i) for i in range(1, osm_count + 1)])
    db.executemany("INSERT INTO cn_dataset_regions VALUES('synthetic',?,'city',?, '[]', 1,NULL)",
                   [(i, f"OSM City {i}") for i in range(1, link_count + 1)])
    places = []
    refs = []
    for i in range(1, geo_count + 1):
        place_id = osm_count + i
        name = "Legacy Sunset Town" if i == 1 else f"Geo City {i:05d}"
        places.append((place_id, "city", name, normalize_name(name), None, "ACTIVE"))
        refs.append(("synthetic", f"geo:{i:05d}", place_id, "geonames"))
    db.executemany("INSERT INTO places VALUES(?,?,?,?,?,?)", places)
    db.executemany("INSERT INTO geo_dataset_place_refs VALUES(?,?,?,?)", refs)
    db.executemany(
        "INSERT INTO place_external_refs VALUES('geonames',?,?)",
        [(external_id, place_id) for _, external_id, place_id, _ in refs],
    )
    db.executemany("INSERT INTO place_aliases VALUES(?,?,?,?, 'ACTIVE')",
                   [(1, osm_count + 1, "legacy sunset", normalize_name("legacy sunset"))])
    aliases = []
    for i in range(2, alias_count + 1):
        place_num = (i % geo_count) + 1
        text = "Many Match City" if 2 <= i <= 101 else f"Alternate {i:05d}"
        aliases.append((i, osm_count + place_num, text, normalize_name(text), "ACTIVE"))
    if aliases:
        db.executemany("INSERT INTO place_aliases VALUES(?,?,?,?,?)", aliases)
    db.executemany("INSERT INTO cn_legacy_links VALUES(?,?,1,'synthetic')",
                   [(osm_count + i, i) for i in range(1, link_count + 1)])
    return Catalog(db, db_path=Path(":memory:"))


def baseline_choices() -> Callable[..., list[dict[str, Any]]]:
    source = subprocess.check_output(
        ["git", "show", "814ffa1:src/photo_search/places/choices.py"],
        cwd=ROOT, text=True, encoding="utf-8",
    )
    module = ModuleType("baseline_place_choices")
    module.__dict__["__file__"] = "814ffa1:src/photo_search/places/choices.py"
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module.choices


def signature(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    fields = ("kind", "key", "name", "parent", "aliases", "source", "source_id",
              "dataset_id", "dataset_version", "selection_ref", "place_id", "_parent_key")
    return [tuple(tuple(row.get(k, [])) if k == "aliases" else row.get(k) for k in fields)
            for row in rows]


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int((len(ordered) * q + 0.999999) - 1)))]


def run_once(call: Callable[..., Any], catalog: Catalog, text: str,
             diagnostics: bool) -> tuple[float, int, int, list[dict[str, Any]], dict[str, Any]]:
    counts = None if diagnostics else Counts(catalog)
    metrics: dict[str, Any] | None = {} if diagnostics else None
    started = time.perf_counter()
    rows = call(catalog if diagnostics else counts, text=text, data_root=object(),
                perf_metrics=metrics)
    elapsed = (time.perf_counter() - started) * 1000
    if metrics is not None:
        queries = int(metrics.get("place_sql_queries", 0))
        row_count = int(metrics.get("place_sql_rows", 0))
        return elapsed, queries, row_count, rows, metrics
    assert counts is not None
    return elapsed, counts.queries, counts.rows, rows, {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--baseline-only", action="store_true")
    args = parser.parse_args()
    catalog = make_catalog()
    osm = [{"kind": "city", "key": f"cn:{i}", "name": f"OSM City {i}", "aliases": [],
            "source": "OSM", "source_id": f"relation/{i}", "dataset_id": "synthetic",
            "dataset_version": "synthetic-v1", "selection_ref": f"osm|synthetic|relation/{i}",
            "place_id": i} for i in range(1, 3014)]
    original = live_choices.cn_facets.choices
    original_index = live_choices.albums._index
    live_choices.cn_facets.choices = lambda _catalog: [dict(item) for item in osm]
    verified_ids = {f"geo:{i:05d}" for i in range(1, 45218)}
    synthetic_index = {"city_to_admin1": {source_id: {} for source_id in verified_ids}}
    live_choices.albums._index = lambda *_args: (
        dict(synthetic_index), "synthetic-stamp", "geonames", True,
    )
    try:
        baseline = baseline_choices()
        implementations = {"baseline_814ffa1": baseline}
        if not args.baseline_only:
            implementations["working_tree"] = live_choices.choices
        scenarios = {
            "no_hit": "a quiet sunset",
            "one_hit": "geo city 00400",
            "many_hits": "many match city",
            "linked_alias": "legacy sunset",
        }
        results: dict[str, Any] = {"fixture": {"osm": 3013, "geonames": 45217,
                                                  "aliases": 26520, "legacy_links": 274,
                                                  "samples": args.samples}}
        for scenario, text in scenarios.items():
            signatures: dict[str, list[tuple[Any, ...]]] = {}
            results[scenario] = {"first_observation": {}}
            for impl_name, call in implementations.items():
                first_catalog = make_catalog()
                first_ms, first_queries, first_rows, first_result, _ = run_once(
                    call, first_catalog, text, False
                )
                results[scenario]["first_observation"][impl_name] = {
                    "elapsed_ms": round(first_ms, 3), "queries": first_queries,
                    "python_rows": first_rows, "matched": len(first_result),
                    "signature_sha256": hashlib.sha256(
                        repr(signature(first_result)).encode()
                    ).hexdigest(),
                }
                first_catalog.close()
            for impl_name, call in implementations.items():
                for diagnostic in (False, True):
                    run_times: list[float] = []
                    last_queries = last_rows = 0
                    last_signature: list[tuple[Any, ...]] | None = None
                    last_metrics: dict[str, Any] = {}
                    run_once(call, catalog, text, diagnostic)  # one warmup per case
                    for _ in range(args.samples):
                        elapsed, queries, row_count, rows, metrics = run_once(call, catalog, text, diagnostic)
                        last_metrics = metrics
                        run_times.append(round(elapsed, 3))
                        last_queries, last_rows = queries, row_count
                        current_signature = signature(rows)
                        if last_signature is not None and current_signature != last_signature:
                            raise AssertionError(f"non-deterministic results: {scenario}/{impl_name}/{diagnostic}")
                        last_signature = current_signature
                    tracemalloc.start()
                    _, _, _, _, _ = run_once(call, catalog, text, diagnostic)
                    _, peak = tracemalloc.get_traced_memory()
                    tracemalloc.stop()
                    results.setdefault(scenario, {}).setdefault(impl_name, {})[
                        "diagnostics_on" if diagnostic else "diagnostics_off"
                    ] = {"runs_ms": run_times, "p50_ms": statistics.median(run_times),
                          "p95_ms": percentile(run_times, .95), "queries": last_queries,
                          "python_rows": last_rows, "peak_tracemalloc_bytes": peak,
                          "matched": len(last_signature or []),
                          "signature_sha256": hashlib.sha256(repr(last_signature).encode()).hexdigest(),
                          "metrics": last_metrics}
                    signatures[f"{impl_name}:{diagnostic}"] = last_signature or []
            if "working_tree" in implementations:
                for diagnostic in (False, True):
                    if signatures[f"baseline_814ffa1:{diagnostic}"] != signatures[f"working_tree:{diagnostic}"]:
                        raise AssertionError(f"full result signature mismatch: {scenario}/{diagnostic}")
        plan_sql = """SELECT x.external_id,x.place_id,p.kind,p.canonical_name,p.normalized_name
             FROM geo_dataset_place_refs x JOIN active_geo_dataset a
               ON a.singleton=1 AND a.dataset_id=x.dataset_id
             JOIN places p ON p.place_id=x.place_id AND p.lifecycle='ACTIVE'
            WHERE x.dataset_id=? AND x.provider=?
              AND (instr(?,replace(p.normalized_name,' ',''))>0 OR EXISTS(
                    SELECT 1 FROM place_aliases a WHERE a.place_id=p.place_id
                      AND a.lifecycle='ACTIVE' AND instr(?,replace(a.normalized,' ',''))>0))
            ORDER BY length(p.canonical_name) DESC,p.place_id"""
        plan = [tuple(row) for row in catalog.connection.execute(
            "EXPLAIN QUERY PLAN " + plan_sql,
            ("synthetic", "geonames", "legacysunset", "legacysunset"))]
        results["candidate_sql_plan"] = plan
        output = (ROOT / "test-results" / "P4-SEARCH-PLACE-LEXICON-FAST-PATH"
                  / f"benchmark-{args.samples}-samples.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(output), "candidate_sql_plan": plan,
                          "scenarios": {k: v for k, v in results.items() if isinstance(v, dict) and k != "fixture"}},
                         ensure_ascii=False))
    finally:
        live_choices.cn_facets.choices = original
        live_choices.albums._index = original_index
        catalog.close()


if __name__ == "__main__":
    main()
