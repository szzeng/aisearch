"""Isolated mixed-queue claims and read-only catalog measurement (P4-PERF-300K-CN-B).

No worker is executed and no model qualification is asserted: CPU specs/capability are
explicit synthetic scheduler inputs. Timers enclose real product calls; independent
Python ordering/state oracles, SQL plans and JSON/JUnit writing are outside the timers.
The first claim includes the initial due-retry/exhausted-lease cleanup; later calls
measure the progressing queue, not repeated restoration of that cleanup burst.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.catalog.sqlite_io import open_readonly
from photo_search.ingestion.service import CatalogService
from photo_search.jobs import queue
from photo_search.media.cache import MediaCache

# Initialize the person package before queries, matching the application bootstrap.
# Direct queries-first import hits the existing persons/albums circular import.
from photo_search.persons import albums as _albums  # noqa: F401
from photo_search.refresh import progress
from photo_search.scheduler.contracts import CONTRACT_VERSION, ProcessorSpec, RuntimeCapabilities
from photo_search.scheduler.engine import Scheduler

NOW = 1_790_244_000_000
WINDOW = 60_000
PACKAGE = "benchmark-python@1"
PROCESSORS = ("bench-geo@1", "bench-thumb@1", "bench-text@1", "bench-legacy@1")
TASKS = ("bench_geo", "bench_thumb", "bench_text", "bench_legacy")
SPARSE = "bench-sparse@1"
FIELDS = tuple(queue.JOB_FIELDS)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [item["ms"] for item in samples[1:]]
    return {
        "first_call_ms": samples[0]["ms"], "samples": samples,
        "repetitions": len(durations), "median_ms": statistics.median(durations),
        "p95_ms": sorted(durations)[math.ceil(len(durations) * .95) - 1],
        "max_ms": max(durations),
        "unexpected_errors": sum(not item["oracle_ok"] for item in samples),
        "expected_error_samples": sum(item.get("expected_error", False) for item in samples),
    }


def junit(path: Path, report: dict[str, Any]) -> None:
    """Measurement/oracle cases, deliberately not presented as pytest test counts."""
    cases = []
    if report.get("fatal_error"):
        cases.append(("fatal_error", {"ms": 0, "oracle_ok": False, "error": report["fatal_error"]}))
    for scenario in report["results"]:
        for index, sample in enumerate(scenario["samples"]):
            cases.append((f"{scenario['scenario']}.sample_{index:02d}", sample))
        if "final_state_oracle" in scenario:
            cases.append((f"{scenario['scenario']}.full_state",
                          {"ms": 0, "oracle_ok": scenario["final_state_oracle"]["matches"]}))
    suite = ET.Element("testsuite", name="measurement_oracles_not_pytest",
                       tests=str(len(cases)), failures=str(sum(not x[1]["oracle_ok"] for x in cases)),
                       errors="0", skipped="0", time=str(sum(x[1]["ms"] for x in cases) / 1000))
    for name, sample in cases:
        case = ET.SubElement(suite, "testcase", classname="p4_perf_jobs_measurement", name=name,
                             time=str(sample["ms"] / 1000))
        if not sample["oracle_ok"]:
            ET.SubElement(case, "failure", message="measurement oracle mismatch").text = json.dumps(sample)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def spec(processor: str, task: str) -> ProcessorSpec:
    return ProcessorSpec.from_envelope({
        "contract_version": CONTRACT_VERSION, "spec_revision": "synthetic-claim-v1",
        "processor_id": processor, "processor_kind": "non_model", "task_type": task,
        "config_hash": "b" * 64, "input_type": "asset_original", "output_type": "geo_fact.v1",
        "device_policy": "CPU_PREFERRED", "runtime_backend": "python",
        "runtime_package_id": PACKAGE, "supported_devices": ["cpu"],
        "supported_precisions": ["not_applicable"], "max_batch_size": 1,
        "cancellation_supported": True, "resource_measurements": [],
    })


def prepare_jobs(root: Path, count: int) -> Catalog:
    dbpath = root / "data/catalog/catalog.db"
    if dbpath.exists():
        raise FileExistsError(f"refusing to replace an existing benchmark: {dbpath}")
    catalog = Catalog.open(data_root=root / "data")
    columns = ("job_id", "subject_type", "subject_id", "input_revision", "task_type",
               "processor_id", "state", "priority", "attempts", "max_attempts", "run_after",
               "lease_token", "lease_expires_at", "worker_id", "progress", "created_at", "updated_at")
    sql = f"INSERT INTO jobs ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})"
    states = ["QUEUED"] * 11 + ["RETRY_WAIT"] * 3 + ["RUNNING"] * 3 + [
        "BLOCKED", "SUCCEEDED", "CANCELLED"]
    batch = []
    for index in range(count):
        slot = index % 20
        group = (index // 20) % 10
        kind = 0 if group < 3 else 1 if group < 6 else 2 if group < 8 else 3
        processor, task = PROCESSORS[kind], TASKS[kind]
        created = NOW - ((index * 137) % 2_000_000)
        # 0.1% sparse registry; deliberately newer than the unrelated backlog.
        # Its states cycle too, so sparse is not accidentally all QUEUED.
        if index % 1000 == (index // 1000) % 20:
            processor, task, created = SPARSE, "bench_sparse", NOW - (index % 1000)
        state = states[slot]
        run_after = NOW + 60_000 if slot in (8, 9, 10, 13) else NOW - 60_000
        expiry = NOW + 60_000 if slot == 16 else NOW - 1 if state == "RUNNING" else None
        attempts = 2 if slot == 15 else 1 if state == "RETRY_WAIT" else 0
        batch.append((index + 1, "benchmark", str(index + 1), "synthetic-v1", task,
                      processor, state, (0, 10, 50)[(index // 200) % 3], attempts, 3,
                      run_after, f"seed-{index}" if state == "RUNNING" else None,
                      expiry, "old-worker" if state == "RUNNING" else None,
                      1.0 if state == "SUCCEEDED" else 0.0, created, created))
        if len(batch) == 5000 or index + 1 == count:
            with catalog.write() as conn:
                conn.executemany(sql, batch)
            batch.clear()
    return catalog


def normalized_row(row: Any) -> dict[str, Any]:
    value = {key: row[key] for key in FIELDS}
    value["lease_token"] = bool(value["lease_token"])
    return value


def state_hash(rows: list[dict[str, Any]]) -> str:
    result = hashlib.sha256()
    for row in rows:
        result.update(json.dumps(row, sort_keys=True, separators=(",", ":")).encode())
        result.update(b"\n")
    return result.hexdigest()


def actual_hash(catalog: Catalog) -> str:
    return state_hash([normalized_row(row) for row in catalog.connection.execute(
        f"SELECT {queue.JOB_COLUMNS} FROM jobs ORDER BY job_id")])


def jobs_benchmark(root: Path, count: int, repetitions: int, report: dict[str, Any], output: Path) -> None:
    started = time.perf_counter()
    prepared = root / "prepared.json"
    if prepared.exists():
        preparation = json.loads(prepared.read_text(encoding="utf-8"))
        if preparation["rows"] != count:
            raise ValueError("prepared job count differs")
        catalog = Catalog.open(data_root=root / "data")
        if actual_hash(catalog) != preparation["initial_state_sha256"]:
            raise ValueError("prepared fixture has changed; refusing benchmark reuse")
        report["preparation"] = preparation
    else:
        catalog = prepare_jobs(root, count)
        report["fixture_creation_ms"] = (time.perf_counter() - started) * 1000
    rows = [normalized_row(row) for row in catalog.connection.execute(
        f"SELECT {queue.JOB_COLUMNS} FROM jobs ORDER BY job_id")]
    report["jobs_fixture"] = {
        "rows": count, "now_ms": NOW, "aging_window_ms": WINDOW,
        "state_counts": dict(Counter(row["state"] for row in rows)),
        "processor_counts": dict(Counter(row["processor_id"] for row in rows)),
        "priority_counts": dict(Counter(row["priority"] for row in rows)),
        "joint_distribution": [dict(processor_id=p, task_type=t, state=s, run_after_relation=d,
                                    lease_relation=lease, priority=pr, count=n)
                               for (p, t, s, d, lease, pr), n in sorted(Counter(
                                   (r["processor_id"], r["task_type"], r["state"],
                                    "due" if r["run_after"] <= NOW else "future",
                                    "none" if r["lease_expires_at"] is None else
                                    "expired" if r["lease_expires_at"] <= NOW else "live",
                                    r["priority"]) for r in rows).items())],
        "initial_state_sha256": state_hash(rows),
        "model_execution": "none; synthetic CPU scheduler qualifications only",
    }
    specs = [spec(p, t) for p, t in zip(PROCESSORS[:3], TASKS[:3], strict=True)]
    specs += [spec(SPARSE, "bench_sparse"), spec("bench-absent@1", "bench_absent")]
    setup = Scheduler(catalog, specs)
    capability = RuntimeCapabilities.seal({
        "runtime_package_id": PACKAGE, "runtime_backend": "python",
        "runtime_package_name": "benchmark-python", "runtime_package_version": "1",
        "python_version": "3.12.10", "platform": "windows-x86_64", "driver_version": None,
        "providers": [{"name": "python-stdlib", "device": "cpu", "precisions": ["not_applicable"],
                       "available": True, "health": "healthy", "health_checked_at": NOW,
                       "evidence_id": None}],
        "health_status": "healthy", "health_checked_at": NOW, "measured_at": NOW,
        "evidence_ids": [], "failure_code": None,
    })
    setup.record_capability(capability, now_ms=NOW)
    for item in specs:
        setup.record_qualification(processor_id=item.processor_id, spec_revision=item.spec_revision,
                                   device="cpu", precision="not_applicable", provider_name="python-stdlib",
                                   runtime_package_id=PACKAGE, now_ms=NOW)
    scenarios = [("claim_mixed_registered", specs[:3], False),
                 ("claim_sparse_registry", specs[3:4], False),
                 ("claim_absent_registry", specs[4:], False),
                 ("claim_paused_error", specs[:3], True)]
    tokens: set[str] = set()
    for name, selected, paused in scenarios:
        scheduler = Scheduler(catalog, selected, lease_seconds=60)
        allowed = {item.processor_id for item in selected}
        tasks = {item.task_type for item in selected}
        if paused:
            scheduler.pause(now_ms=NOW)
        expected = rows
        before = state_hash(expected)
        due = exhausted = 0
        if not paused:
            for row in expected:
                if row["processor_id"] not in allowed:
                    continue
                if row["state"] == "RETRY_WAIT" and row["run_after"] <= NOW:
                    row.update(state="QUEUED", updated_at=NOW)
                    due += 1
                if (row["state"] == "RUNNING" and row["lease_expires_at"] <= NOW
                        and row["attempts"] + 1 >= row["max_attempts"]):
                    row.update(state="FAILED", attempts=row["attempts"] + 1, lease_token=False,
                               lease_expires_at=None, worker_id=None, last_error_code="LEASE_EXPIRED",
                               last_error_message="lease expired before completion; no retry budget left",
                               updated_at=NOW)
                    exhausted += 1
        eligible = sorted((r for r in expected if r["processor_id"] in allowed
                           and r["task_type"] in tasks and (
                               (r["state"] == "QUEUED" and r["run_after"] <= NOW) or
                               (r["state"] == "RUNNING" and r["lease_expires_at"] <= NOW))),
                          key=lambda r: (-(r["priority"] + (NOW - r["created_at"]) // WINDOW),
                                         r["created_at"], r["job_id"]))
        samples = []
        print(f"scenario_start={name}", flush=True)
        for index in range(repetitions + 1):
            wanted = None if paused or index >= len(eligible) else eligible[index]
            start = time.perf_counter()
            request = None
            error = None
            try:
                request = scheduler.claim_next(worker_id="benchmark-worker", now_ms=NOW)
            except Exception as exc:
                error = {"type": type(exc).__name__, "code": getattr(exc, "code", None), "message": str(exc)}
            elapsed = (time.perf_counter() - start) * 1000
            actual_id = None if request is None else request.job_id
            wanted_id = None if wanted is None else wanted["job_id"]
            ok = (error is not None and error["code"] == "SCHEDULER_PAUSED") if paused else (
                error is None and actual_id == wanted_id)
            if wanted is not None and error is None and actual_id == wanted_id:
                wanted.update(attempts=wanted["attempts"] + int(wanted["state"] == "RUNNING"),
                              state="RUNNING", lease_token=True, lease_expires_at=NOW + 60000,
                              worker_id="benchmark-worker", updated_at=NOW, resolved_device="cpu",
                              runtime_provider="python-stdlib", precision="not_applicable",
                              runtime_capability_revision=capability.capability_revision,
                              dispatch_spec_revision="synthetic-claim-v1", blocking_reason=None)
                actual = normalized_row(queue.get_job(catalog.connection, actual_id).as_dict())
                ok = ok and actual == wanted and request.lease_token not in tokens
                tokens.add(request.lease_token)
            samples.append({"ms": elapsed, "job_id": actual_id, "expected_job_id": wanted_id,
                            "error": error, "expected_error": paused, "oracle_ok": bool(ok)})
        actual = actual_hash(catalog)
        expected_digest = state_hash(expected)
        result = {"scenario": name, "registered_processors": sorted(allowed),
                  "first_call_preprocessing": {"due_retries": due, "exhausted_leases": exhausted},
                  "eligible_before_claims": len(eligible), "before_sha256": before,
                  **summarize(samples), "final_state_oracle": {
                      "actual_sha256": actual, "expected_sha256": expected_digest,
                      "matches": actual == expected_digest, "columns": list(FIELDS),
                      "lease_token_comparison": "presence plus fresh unique returned tokens"}}
        report["results"].append(result)
        save(output, report)
        print(f"{name}: first={samples[0]['ms']:.2f}, median={result['median_ms']:.2f}, "
              f"p95={result['p95_ms']:.2f} ms; errors={result['unexpected_errors']}; "
              f"state_match={actual == expected_digest}", flush=True)
        if paused:
            scheduler.resume(now_ms=NOW)
    catalog.close()


def reads_benchmark(data: Path, repetitions: int, report: dict[str, Any], output: Path) -> None:
    from photo_search.api.queries import asset_page

    dbpath = data / "catalog/catalog.db"
    catalog = Catalog(open_readonly(dbpath), db_path=dbpath, data_root=data)
    catalog.connection.execute("PRAGMA query_only=ON")
    service = CatalogService(catalog, data_root=data)
    cache = MediaCache.open(data)
    # Independent fixture membership/order oracle; not measured and no product predicate reuse.
    metadata = [tuple(row) for row in catalog.read_all(
        "SELECT a.asset_id,a.created_at,m.taken_at_sort FROM assets a "
        "LEFT JOIN asset_metadata m ON m.asset_id=a.asset_id ORDER BY a.asset_id")]
    source_locations = [tuple(row) for row in catalog.read_all(
        "SELECT asset_id,source_id,relative_path,availability FROM file_locations ORDER BY location_id")]
    if any(row[0] is None for row in source_locations):
        raise ValueError("this fixture oracle requires all locations to have bound assets")
    photo_ids = {int(r[0]) for r in source_locations if str(r[2]).lower().endswith(".jpg")}
    if len(photo_ids) != len(metadata):
        raise ValueError("fixture oracle expects every synthetic asset to have a JPG location")
    sources = service.list_sources()
    independent_sources = [dict(row) for row in catalog.read_all(
        "SELECT source_id,display_name,root_path,volume_hint,status,last_successful_scan "
        "FROM source_roots ORDER BY source_id")]
    if not sources:
        raise ValueError("source-list scale measurement needs sources")
    source_id = int(sources[0]["source_id"])
    source_ids = {int(r[0]) for r in source_locations if int(r[1]) == source_id}
    orders = {
        "asset_id": sorted(metadata, key=lambda r: r[0]),
        "time_desc": sorted(metadata, key=lambda r: (-r[1], -r[0])),
        "capture_desc": sorted(metadata, key=lambda r: (r[2] is None, -(r[2] or 0), -r[0])),
    }
    report["catalog_fixture"] = {"data_root": str(data), "schema_version": catalog.schema_version,
                                 "assets": len(metadata), "locations": len(source_locations),
                                 "source_ids": [s["source_id"] for s in sources],
                                 "metadata_identity_sha256": digest(metadata),
                                 "locations_identity_sha256": digest(source_locations),
                                 "query_only": True, "media_files": "absent in synthetic fixture"}
    scenarios = []
    for order, ordered in orders.items():
        first = asset_page(catalog, cache, limit=50, order=order)
        for page, cursor in ((1, None), (2, first["next_cursor"])):
            wanted = [r[0] for r in ordered[(page - 1) * 50:page * 50]]
            scenarios.append((f"gallery_{order}_page{page}",
                              lambda order=order, cursor=cursor: asset_page(
                                  catalog, cache, limit=50, order=order, cursor=cursor),
                              lambda result, wanted=wanted: [r["asset_id"] for r in result["assets"]] == wanted))
    wanted = [r[0] for r in orders["asset_id"] if r[0] in source_ids][:50]
    scenarios.append(("gallery_source_page1", lambda: asset_page(catalog, cache, limit=50, source_id=source_id),
                      lambda result: [r["asset_id"] for r in result["assets"]] == wanted))
    scenarios.append(("source_summary_list", service.list_sources,
                      lambda result: result == independent_sources))

    def source_details():
        result = []
        for source in service.list_sources():
            row = service.get_source(source["source_id"])
            row["progress"] = progress.source_progress(catalog, source_id=source["source_id"],
                                                       binding=None, available={"face": False, "ocr": False})
            result.append(row)
        return result

    def source_oracle(result):
        for row in result:
            locations = [r for r in source_locations if r[1] == row["source_id"]]
            counts = row["counts"]
            if not (counts["assets"] == len({r[0] for r in locations}) and
                    counts["locations"] == len(locations) and counts["assets_total"] == len(metadata) and
                    counts["locations_present"] == sum(r[3] == "PRESENT" for r in locations) and
                    row["progress"]["stages"]["semantic"]["state"] == "UNAVAILABLE"):
                return False
        return len(result) == len(sources)

    scenarios.append(("source_list_counts_progress_no_model_binding", source_details, source_oracle))
    for name, call, oracle in scenarios:
        print(f"scenario_start={name}", flush=True)
        samples = []
        first_digest = None
        first_result = None
        for index in range(repetitions + 1):
            start = time.perf_counter()
            error = None
            result = None
            try:
                result = call()
            except Exception as exc:
                error = {"type": type(exc).__name__, "message": str(exc)}
            elapsed = (time.perf_counter() - start) * 1000
            current_digest = digest(result)
            if index == 0:
                first_digest, first_result = current_digest, result
            samples.append({"ms": elapsed, "sha256": current_digest, "error": error,
                            "oracle_ok": error is None and oracle(result) and current_digest == first_digest})
        traces: list[str] = []
        catalog.connection.set_trace_callback(traces.append)
        try:
            call()
        finally:
            catalog.connection.set_trace_callback(None)
        plans = []
        for sql in dict.fromkeys(traces):
            if sql.lstrip().upper().startswith(("SELECT", "WITH")):
                plans.append({"sql": sql, "plan": [str(row[3]) for row in
                              catalog.connection.execute("EXPLAIN QUERY PLAN " + sql)]})
        measured = {"scenario": name, **summarize(samples), "first_result": first_result, "plans": plans}
        report["results"].append(measured)
        save(output, report)
        print(f"{name}: median={measured['median_ms']:.2f}, p95={measured['p95_ms']:.2f} ms; "
              f"errors={measured['unexpected_errors']}", flush=True)
    catalog.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("prepare", "jobs", "reads", "all"), default="all")
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--catalog-data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--jobs-count", type=int, default=300000)
    parser.add_argument("--measurement-context", default="")
    args = parser.parse_args()
    if args.samples < 30:
        parser.error("at least 30 repetitions required")
    temporary_root = Path(__file__).resolve().parents[2] / ".tmp"
    for value in (args.work_root, args.catalog_data):
        if not value.resolve().is_relative_to(temporary_root):
            parser.error("benchmark data must resolve inside the project's .tmp directory")
    report = {"layer": "product Scheduler.claim_next and catalog/API query core; no HTTP/browser/model execution",
              "first_call_note": "first observed; shared OS/process caches, not disk-cold; gallery cursor setup precedes timing",
              "timing_note": "perf_counter around product call only; oracle/hash/plans/serialization excluded",
              "measurement_context": args.measurement_context,
              "python": sys.version, "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "command": sys.argv, "results": []}
    started = time.perf_counter()
    try:
        if args.mode == "prepare":
            catalog = prepare_jobs(args.work_root.resolve(), args.jobs_count)
            preparation = {"rows": args.jobs_count, "initial_state_sha256": actual_hash(catalog),
                           "creation_seconds": time.perf_counter() - started}
            save(args.work_root.resolve() / "prepared.json", preparation)
            report["preparation"] = preparation
            catalog.close()
        if args.mode in ("jobs", "all"):
            jobs_benchmark(args.work_root.resolve(), args.jobs_count, args.samples, report, args.output)
        if args.mode in ("reads", "all"):
            reads_benchmark(args.catalog_data.resolve(), args.samples, report, args.output)
    except (Exception, KeyboardInterrupt) as error:
        report["fatal_error"] = f"{type(error).__name__}: {error}"
        print(report["fatal_error"], flush=True)
    report["elapsed_seconds"] = time.perf_counter() - started
    report["passed"] = not report.get("fatal_error") and all(
        not r["unexpected_errors"] and r.get("final_state_oracle", {}).get("matches", True)
        for r in report["results"])
    save(args.output, report)
    junit(args.junit, report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
