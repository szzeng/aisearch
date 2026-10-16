"""Measure text-hit explanations on 300k synthetic metadata rows, without photos/models.

Run with the project interpreter and --output test-results/<run>/text-evidence.json.
The matching JUnit XML accompanies the JSON; unique fixtures stay in the project-local
--work-root (default .tmp/search-text-evidence). Existing results are never overwritten.
First-call timings are not disk-cold: the OS file cache is never flushed.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import random
import sqlite3
import statistics
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from photo_search.search.documents import TEXT_COLUMNS  # noqa: E402
from photo_search.search.retrieval import text_column_matches  # noqa: E402
from photo_search.search.tokens import build_match  # noqa: E402

SEED = 20261016
MATCH = build_match("beach sunset")


class Reader:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def read_all(self, sql, parameters=()):
        return self.connection.execute(sql, parameters).fetchall()


def previous(reader, match, asset_ids):
    """The b619252 two-column implementation; deliberately preserves its omissions."""
    ids = sorted({int(value) for value in asset_ids})
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    hits = {}
    for column in ("filename_text", "place_text"):
        expression = f"{{{column}}} : ({match.expression})"
        rows = reader.read_all(
            "SELECT rowid AS asset_id FROM search_documents_fts "
            f"WHERE search_documents_fts MATCH ? AND rowid IN ({placeholders})",
            (expression, *ids),
        )
        for row in rows:
            hits.setdefault(int(row["asset_id"]), []).append(column)
    return {asset_id: sorted(columns) for asset_id, columns in hits.items()}


def corrected_unoptimized(reader, match, asset_ids):
    """Same intended six-column semantics, retaining the old rowid-IN query plan."""
    ids = sorted(set(asset_ids))
    any_term = " OR ".join(term.render() for term in match.terms)
    placeholders = ",".join("?" for _ in ids)
    hits = {}
    for column in TEXT_COLUMNS:
        expression = f"({match.expression}) AND {{{column}}} : ({any_term})"
        rows = reader.read_all(
            "SELECT rowid AS asset_id FROM search_documents_fts "
            f"WHERE search_documents_fts MATCH ? AND rowid IN ({placeholders})",
            (expression, *ids),
        )
        for row in rows:
            hits.setdefault(int(row["asset_id"]), []).append(column)
    return {key: sorted(value) for key, value in hits.items()}


def target_row(ordinal, long_ocr=False):
    row = ["photo", "", "receipt token " * (500 if long_ocr else 4), "", "", "park"]
    contributions = (
        {0: "beach sunset"}, {2: "beach sunset"}, {0: "beach", 2: "sunset"},
        {5: "beach sunset"}, {1: "beach", 3: "sunset"}, {4: "beach", 5: "sunset"},
    )[ordinal % 6]
    for index, text in contributions.items():
        row[index] += " " + text
    return row, sorted(TEXT_COLUMNS[index] for index in contributions)


def build_fixture(path, *, rows=300_000, candidates=5_000):
    targets = sorted(random.Random(SEED).sample(range(1, rows + 1), candidates))
    target_ordinals = {asset_id: number for number, asset_id in enumerate(targets)}
    partial_ids = [value for value in range(1, rows + 1) if value not in target_ordinals][:2]
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE search_documents(asset_id INTEGER PRIMARY KEY, "
               + ",".join(f"{name} TEXT" for name in TEXT_COLUMNS) + ")")
    db.execute("CREATE VIRTUAL TABLE search_documents_fts USING fts5("
               + ",".join(TEXT_COLUMNS)
               + ",content='search_documents',content_rowid='asset_id',tokenize='unicode61')")
    expected = {}
    for start in range(1, rows + 1, 2_000):
        batch = []
        for asset_id in range(start, min(start + 2_000, rows + 1)):
            if asset_id in target_ordinals:
                values, expected[asset_id] = target_row(target_ordinals[asset_id])
            else:
                values = [f"photo {asset_id}", "", "ordinary archive", "", "", "park"]
            if asset_id == partial_ids[0]:
                values[0], values[2] = "beach", "beach"
            if asset_id == partial_ids[1]:
                values[0], values[2] = "sunset", "sunset"
            batch.append((asset_id, *values))
        db.executemany("INSERT INTO search_documents VALUES (?,?,?,?,?,?,?)", batch)
    db.execute("INSERT INTO search_documents_fts(search_documents_fts) VALUES('rebuild')")
    db.commit()
    return db, targets, partial_ids, expected


def measure(function, reader, ids, samples, expected, match=MATCH):
    timings, result = [], None
    for _ in range(samples + 1):
        started = time.perf_counter()
        result = function(reader, match, ids)
        timings.append((time.perf_counter() - started) * 1000)
        if function is not previous and result != expected:
            raise AssertionError(f"{function.__name__} explanations differ from fixture truth")
    warm = timings[1:]
    return {"first_call_ms": timings[0], "warm_ms": warm, "median_warm_ms": statistics.median(warm),
            "warm_range_ms": [min(warm), max(warm)], "explained_assets": len(result),
            "missing_or_incomplete": sum(result.get(key) != value for key, value in expected.items())}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New project-local JSON output")
    parser.add_argument("--work-root", type=Path, default=ROOT / ".tmp/search-text-evidence")
    args = parser.parse_args(argv)
    args.output, args.work_root = args.output.resolve(), args.work_root.resolve()
    if not all(path.is_relative_to(ROOT) for path in (args.output, args.work_root)):
        parser.error("output and work-root must remain inside the repository")
    if args.output.suffix.lower() != ".json":
        parser.error("output must use the .json extension")
    for path in (args.output, args.output.with_suffix(".xml")):
        if path.exists():
            raise FileExistsError(f"preserve existing evidence: {path}")
    return args


def main(argv=None):
    args = parse_args(argv)
    output, temporary = args.output, args.work_root
    temporary.mkdir(parents=True, exist_ok=True)
    fixture = Path(tempfile.mkdtemp(prefix="text-evidence-", dir=temporary)) / "synthetic.sqlite3"
    report = {"seed": SEED, "rows": 300_000, "candidates": 5_000, "fixture": str(fixture),
              "sqlite_version": sqlite3.sqlite_version, "python": sys.version,
              "production_sha256": hashlib.sha256(inspect.getsource(text_column_matches).encode()).hexdigest(),
              "scope": "Metadata-only FTS explanations. Old version omits OCR/cross-column hits; "
              "compare speed with corrected_unoptimized for equivalent semantics. First-call is not OS-cold.",
              "phases": {}, "exit_code": 1}
    suite = ET.Element("testsuite", name="text-evidence-300k")
    db = None
    try:
        db, ids, partial, expected = build_fixture(fixture)
        for phase in ("short_ocr", "long_ocr"):
            case = ET.SubElement(suite, "testcase", name=phase)
            try:
                if phase == "long_ocr":
                    db.executemany("UPDATE search_documents SET ocr_text=? WHERE asset_id=?",
                                   [(target_row(number, True)[0][2], asset_id)
                                    for number, asset_id in enumerate(ids)])
                    db.execute("INSERT INTO search_documents_fts(search_documents_fts) VALUES('rebuild')")
                    db.commit()
                db.close()
                db = sqlite3.connect(fixture)
                db.row_factory = sqlite3.Row
                results = {}
                for function, samples in ((text_column_matches, 30), (previous, 3),
                                          (corrected_unoptimized, 3)):
                    db.close()
                    db = sqlite3.connect(fixture)
                    db.row_factory = sqlite3.Row
                    reader = Reader(db)
                    results[function.__name__] = measure(function, reader, ids, samples, expected)
                    print(f"{phase} {function.__name__}: "
                          f"{results[function.__name__]['median_warm_ms']:.2f} ms", flush=True)
                check_ids = ids[:12] + partial
                assert text_column_matches(reader, MATCH, check_ids) == {
                    key: expected[key] for key in ids[:12]
                }, "partial matches must never receive explanations"
                plans = {}
                for prefix in ("", "+"):
                    sql = ("EXPLAIN QUERY PLAN SELECT rowid FROM search_documents_fts "
                           f"WHERE search_documents_fts MATCH ? AND {prefix}rowid IN ("
                           + ",".join("?" for _ in ids) + ")")
                    plans[prefix + "rowid"] = [dict(row) for row in reader.read_all(
                        sql, (f"({MATCH.expression}) AND {{ocr_text}}:(\"beach\"* OR \"sunset\"*)", *ids)
                    )]
                results["query_plans"] = plans
                with fixture.open("rb") as stream:
                    results["fixture_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
                report["phases"][phase] = results
            except Exception as error:
                ET.SubElement(case, "failure", message=str(error))
                raise
        report["exit_code"] = 0
    except Exception as error:
        report["error"] = repr(error)
        if not suite.findall("testcase/failure"):
            case = ET.SubElement(suite, "testcase", name="setup")
            ET.SubElement(case, "failure", message=str(error))
        raise
    finally:
        if db is not None:
            db.close()
        suite.set("tests", str(len(suite.findall("testcase"))))
        suite.set("failures", str(len(suite.findall("testcase/failure"))))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        ET.ElementTree(suite).write(output.with_suffix(".xml"), encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    main()
