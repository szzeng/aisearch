"""Paired read-only FTS summary measurements on an existing synthetic catalog."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from photo_search.catalog.sqlite_io import open_readonly
from photo_search.search import documents


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--baseline-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=30)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    if not args.catalog.resolve().is_relative_to(repo / ".tmp"):
        parser.error("use only a project-local synthetic catalog under .tmp")
    if args.samples < 1:
        parser.error("samples must be positive")
    spec = importlib.util.spec_from_file_location("baseline_fts_documents", args.baseline_source)
    assert spec is not None and spec.loader is not None
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    paths = [args.catalog, args.baseline_source, Path(documents.__file__), Path(__file__)]
    wal = args.catalog.with_name(args.catalog.name + "-wal")
    if wal.exists():
        paths.append(wal)
    fingerprints = {str(p): digest(p) for p in paths}
    result = {
        "layer": "projection_summary only; warm SQLite cache; synthetic metadata",
        "command": sys.argv,
        "before_sha256": fingerprints,
        "samples_ms": {"before": [], "after": []},
        "status": "FAILED",
    }
    suite = ET.Element("testsuite", name="balanced_fts_summary_benchmark", tests="1")
    case = ET.SubElement(suite, "testcase", name="paired_summary_and_immutable_inputs")
    started = time.perf_counter()
    connection = open_readonly(args.catalog)
    code = 0
    try:
        expected = baseline.projection_summary(connection)
        assert documents.projection_summary(connection) == expected
        result["summary"] = expected
        modes = {"before": baseline.projection_summary, "after": documents.projection_summary}
        for sample in range(args.samples):
            for name in (("before", "after") if sample % 2 == 0 else ("after", "before")):
                start = time.perf_counter()
                actual = modes[name](connection)
                elapsed = (time.perf_counter() - start) * 1000
                assert actual == expected
                result["samples_ms"][name].append(elapsed)
        result["statistics"] = {
            name: {
                "n": len(values), "median_ms": statistics.median(values),
                "p95_ms": sorted(values)[math.ceil(len(values) * .95) - 1],
                "max_ms": max(values),
            }
            for name, values in result["samples_ms"].items()
        }
        result["after_sha256"] = {str(p): digest(p) for p in paths}
        assert result["after_sha256"] == fingerprints
        result["status"] = "PASS"
    except Exception as error:
        result["error"] = repr(error)
        ET.SubElement(case, "failure", message=repr(error)).text = repr(error)
        code = 1
    finally:
        connection.close()
        suite.set("failures", str(code))
        suite.set("errors", "0")
        suite.set("time", str(time.perf_counter() - started))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        ET.ElementTree(suite).write(args.output.with_suffix(".xml"), encoding="utf-8",
                                    xml_declaration=True)
    print(json.dumps({"status": result["status"], "statistics": result.get("statistics")}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
