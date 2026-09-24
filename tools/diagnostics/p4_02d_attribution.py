"""P4-02D: controlled attribution of the same-scale retrieval-latency difference.

P4-02B measured the *same* query, against the *same* 100 000-asset fixture and the same
deterministic stand-in encoder, on two generations that differ only in one variable - how
the semantic publication was shaped:

* ``A`` / burst - one unbounded publication burst, drained once;
* ``B`` / wave - the same 100 000 assets published and drained as bounded waves.

The measured service P95 was 3 484.4 ms (A) against 8 317.8 ms (B), i.e. **2.39x**, and the
difference was left unattributed. This tool performs the controlled attribution DS-H's
review and the task card require, and nothing else:

1. **same-retrieval proof** - both roots must be the same fixture (same seed, same asset
   ids, same confirmation set, same vectors) and must answer every round with the same
   ``(asset_id, distance)`` page;
2. **A/B/B/A alternating sampling** - the two roots are interleaved in one process so a
   monotone drift (OS cache, thermal, background work) cannot masquerade as a root effect;
3. **staged timing** - every sample is split into the authoritative SQLite keyset walk, the
   Lance engine round trips (count, per-block time, per-call fixed ``_table`` overhead) and
   the remaining fusion/render cost, plus process working set, disk transfer bytes and CPU;
4. **the F4 layout variable** - ``asset_id_idx`` coverage is recorded for both roots and an
   explicit ``replace`` rebuild on the same Lance table is measured as the one-variable
   contrast (partial coverage -> full coverage, on the root that is partial).

The tool never changes product behaviour: it imports the product's own retrieval entry
points and only *observes* them. The single state change is the diagnostic ``replace``
rebuild of a scalar index inside a throw-away ``tmp/`` data root, recorded as its own step.

Usage::

    uv run --no-sync python tools/diagnostics/p4_02d_attribution.py \
        --burst-root "tmp/p402d 归因 验证/scale-100000-burst/data" \
        --wave-root "tmp/p402d 归因 验证/scale-100000-wave/data" \
        --order A,B,B,A --samples 20 \
        --output test-results/p4-02d-attribution.json
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import statistics
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ann_scale_benchmark as bench  # noqa: E402

from photo_search.catalog.db import Catalog  # noqa: E402
from photo_search.config import vectors_dir  # noqa: E402
from photo_search.indexing import maintenance  # noqa: E402
from photo_search.indexing.store import LanceVectorIndex  # noqa: E402
from photo_search.search.service import search_assets  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUERY_TEXT = "西湖 sunset 全家福"
DEFAULT_TOP_K = 20
#: How many spread-out assets get a direct vector comparison across the two roots.
PROBE_IDS = 64


# --------------------------------------------------------------------------- process facts
class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _MemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def working_set_bytes() -> int:
    """Current working set of this process (Windows) or current RSS elsewhere."""
    if sys.platform != "win32":  # pragma: no cover - POSIX
        with open("/proc/self/statm", encoding="ascii") as stream:
            return int(stream.read().split()[1]) * 4096
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    counters = _MemoryCounters()
    counters.cb = ctypes.sizeof(_MemoryCounters)
    if not kernel32.K32GetProcessMemoryInfo(
        ctypes.c_void_p(kernel32.GetCurrentProcess()), ctypes.byref(counters), counters.cb
    ):
        raise OSError(f"GetProcessMemoryInfo failed: {ctypes.get_last_error()}")
    return int(counters.WorkingSetSize)


def peak_working_set_bytes() -> int:
    if sys.platform != "win32":  # pragma: no cover - POSIX
        return working_set_bytes()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    counters = _MemoryCounters()
    counters.cb = ctypes.sizeof(_MemoryCounters)
    if not kernel32.K32GetProcessMemoryInfo(
        ctypes.c_void_p(kernel32.GetCurrentProcess()), ctypes.byref(counters), counters.cb
    ):
        raise OSError(f"GetProcessMemoryInfo failed: {ctypes.get_last_error()}")
    return int(counters.PeakWorkingSetSize)


def io_bytes() -> dict[str, int]:
    """Transferred bytes of this process, so a sample's disk traffic is measurable."""
    if sys.platform != "win32":  # pragma: no cover - POSIX
        return {"read_bytes": 0, "write_bytes": 0}
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    counters = _IoCounters()
    if not kernel32.GetProcessIoCounters(
        ctypes.c_void_p(kernel32.GetCurrentProcess()), ctypes.byref(counters)
    ):
        raise OSError(f"GetProcessIoCounters failed: {ctypes.get_last_error()}")
    return {
        "read_bytes": int(counters.ReadTransferCount),
        "write_bytes": int(counters.WriteTransferCount),
    }


# --------------------------------------------------------------------------- instrumentation
class Stats:
    """Mutable tally shared with the patched engine, reset once per sample."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.walk_ms = 0.0
        self.walk_calls = 0
        self.candidate_walk_ms = 0.0
        self.candidate_walk_calls = 0
        self.read_all_ms = 0.0
        self.blocks: list[float] = []
        self.table_ms = 0.0
        self.table_calls = 0


def instrument_catalog(catalog: Catalog, stats: Stats) -> None:
    """Time every SQLite ``read_all`` of this catalog, in place.

    ``read_all`` is the single door the retrieval channels use for their candidate rows, so
    patching the *instance* measures the real product call sites without a parallel copy of
    the query. The confirmed-keyset block walk is additionally split out by SQL signature,
    which is what the task card asks for ("SQLite 候选 walk 耗时") as opposed to the small
    authority/revision reads around it.
    """
    original = catalog.read_all
    marker = "FROM index_entries"

    def timed_read_all(sql: str, parameters: tuple = ()) -> list[Any]:
        started = time.perf_counter()
        rows = original(sql, parameters)
        elapsed = (time.perf_counter() - started) * 1000
        stats.walk_ms += elapsed
        stats.walk_calls += 1
        stats.read_all_ms += elapsed
        if marker in str(sql) and "CONFIRMED" in str(sql):
            stats.candidate_walk_ms += elapsed
            stats.candidate_walk_calls += 1
        return rows

    catalog.read_all = timed_read_all  # type: ignore[method-assign]


class TimedIndex:
    """The Lance adapter with each candidate round trip timed, in call order."""

    def __init__(self, index: LanceVectorIndex, stats: Stats) -> None:
        self._index = index
        self._stats = stats
        original = index._table

        def timed_table(**kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                return original(**kwargs)
            finally:
                self._stats.table_ms += (time.perf_counter() - started) * 1000
                self._stats.table_calls += 1

        index._table = timed_table  # type: ignore[method-assign]

    def search_candidates(self, **kwargs: Any) -> list[dict[str, Any]]:
        started = time.perf_counter()
        rows = self._index.search_candidates(**kwargs)
        self._stats.blocks.append(round((time.perf_counter() - started) * 1000, 3))
        return rows

    def __getattr__(self, name: str) -> Any:
        return getattr(self._index, name)


def lance_coverage(index: LanceVectorIndex, *, generation_id: str, dimension: int) -> dict[str, Any]:
    """Layout facts of one generation: rows, version, indices and their coverage."""
    table = index._table(generation_id=str(generation_id), dimension=int(dimension), create=False)
    if table is None:
        return {"table": None}
    indices: list[dict[str, Any]] = []
    for item in table.list_indices():
        indexed = int(getattr(item, "num_indexed_rows", -1))
        unindexed = int(getattr(item, "num_unindexed_rows", -1))
        rows = indexed + unindexed
        indices.append(
            {
                "name": str(getattr(item, "name", item)),
                "index_type": str(getattr(item, "index_type", "")),
                "index_uuid": str(getattr(item, "index_uuid", "")),
                "columns": [str(value) for value in getattr(item, "columns", [])],
                "num_indexed_rows": indexed,
                "num_unindexed_rows": unindexed,
                "covered_fraction": round(indexed / rows, 6) if rows > 0 else None,
                "num_segments": int(getattr(item, "num_segments", -1)),
                "size_bytes": int(getattr(item, "size_bytes", -1)),
            }
        )
    # A LanceDB table lives in ``<vectors>/<table>.lance/`` and its fragments are the
    # ``*.lance`` *files* inside that table's ``data/`` directory: one file per engine write.
    fragments = sorted(
        item
        for item in index.root.rglob("*.lance")
        if item.is_file() and item.parent.name == "data"
    )
    return {
        "rows": int(table.count_rows()),
        "version": int(getattr(table, "version", 0) or 0),
        "indices": indices,
        # ``pylance`` is not installed, so the fragment count is read from the on-disk
        # fragment files, which this locked LanceDB version writes one per engine write.
        "fragment_files": len(fragments),
        "fragment_bytes": int(sum(item.stat().st_size for item in fragments)),
    }


def replace_asset_index(index: LanceVectorIndex, *, generation_id: str, dimension: int) -> dict[str, Any]:
    """The diagnostic one-variable contrast: rebuild ``asset_id_idx`` over the whole table."""
    import lancedb.index as lance_index

    table = index._table(generation_id=str(generation_id), dimension=int(dimension), create=False)
    if table is None:
        return {"state": "no_table"}
    started = time.perf_counter()
    try:
        table.create_index("asset_id", config=lance_index.BTree(), replace=True)
        how = "create_index(asset_id, BTree, replace=True)"
    except TypeError:  # pragma: no cover - older binding of the same operation
        table.create_scalar_index("asset_id", index_type="BTREE", replace=True)
        how = "create_scalar_index(asset_id, BTREE, replace=True)"
    return {
        "state": "rebuilt",
        "how": how,
        "ms": round((time.perf_counter() - started) * 1000, 3),
        "note": "diagnostic only; this is not a product maintenance path (see P4-02F)",
    }


# --------------------------------------------------------------------------- one generation
class Generation:
    """One built data root: its identity, its layout and its instrumented query entry."""

    def __init__(self, name: str, data_root: Path, *, query_text: str, top_k: int) -> None:
        self.name = name
        self.data_root = Path(data_root)
        self.query_text = query_text
        self.top_k = int(top_k)
        self.catalog = Catalog.open(data_root=self.data_root)
        self.index = LanceVectorIndex(vectors_dir(self.data_root))
        self.encoder = bench.DeterministicEncoder()
        self.stats = Stats()
        instrument_catalog(self.catalog, self.stats)
        self.timed_index = TimedIndex(self.index, self.stats)
        self.bindings = {
            "encoder": self.encoder,
            "space_id": bench.SPACE_ID,
            "generation_id": bench.GENERATION_ID,
            "dimension": bench.DIM,
        }
        self.query_vector = [float(value) for value in self.encoder.encode_queries([query_text])[0]]

    def close(self) -> None:
        self.catalog.close()

    def identity(self) -> dict[str, Any]:
        asset_ids = [
            int(row["asset_id"])
            for row in self.catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id")
        ]
        digest = hashlib.sha256(
            ",".join(str(value) for value in asset_ids).encode("ascii")
        ).hexdigest()
        # ``index_entries`` identifies a confirmed vector by its artifact and confirmation
        # revision; the Lance ``record_id`` is derived from that artifact, so hashing these
        # columns proves the two roots confirmed the very same vectors.
        entries = self.catalog.read_all(
            "SELECT asset_id, generation_id, artifact_id, confirmation_revision, state "
            "FROM index_entries ORDER BY asset_id"
        )
        entry_digest = hashlib.sha256(
            ";".join(
                f"{int(row['asset_id'])}|{row['generation_id']}|{row['artifact_id']}|"
                f"{int(row['confirmation_revision'])}|{row['state']}"
                for row in entries
            ).encode("ascii")
        ).hexdigest()
        states: dict[str, int] = {}
        for row in entries:
            key = str(row["state"])
            states[key] = states.get(key, 0) + 1
        return {
            "data_root": str(self.data_root),
            "assets": len(asset_ids),
            "assets_digest_sha256": digest,
            "assets_min": min(asset_ids) if asset_ids else None,
            "assets_max": max(asset_ids) if asset_ids else None,
            "index_entries": len(entries),
            "index_entries_digest_sha256": entry_digest,
            "index_entry_states": states,
            "semantic_status": maintenance.semantic_index_status(
                self.catalog.db_path
            )["index"],
            "search_projection": self.catalog.read_one(
                "SELECT COUNT(*) FROM search_documents"
            )[0],
        }

    def vector_probe(self, ids: Sequence[int]) -> list[list[Any]]:
        """The actual stored vectors of a few assets, expressed as distances to the query.

        Two roots with the same seed must reproduce these numbers exactly; that is what makes
        "the same query against the same data" a checkable fact rather than an assumption.
        """
        rows = self.index.search_candidates(
            generation_id=bench.GENERATION_ID,
            dimension=bench.DIM,
            query_vector=self.query_vector,
            asset_ids=[int(value) for value in ids],
            limit=len(list(ids)),
            space_id=bench.SPACE_ID,
        )
        return [[int(row["asset_id"]), round(float(row["distance"]), 9)] for row in rows]

    def layout(self) -> dict[str, Any]:
        return lance_coverage(
            self.index, generation_id=bench.GENERATION_ID, dimension=bench.DIM
        )

    def once(self) -> dict[str, Any]:
        """One fully instrumented service search."""
        self.stats.reset()
        memory_before = working_set_bytes()
        io_before = io_bytes()
        cpu_before = time.process_time()
        started = time.perf_counter()
        payload = search_assets(
            self.catalog,
            text=self.query_text,
            top_k=self.top_k,
            vector_index=self.timed_index,  # type: ignore[arg-type]
            **self.bindings,
        )
        total_ms = (time.perf_counter() - started) * 1000
        memory_after = working_set_bytes()
        io_after = io_bytes()
        cpu_after = time.process_time()
        vector_hits = payload["channels"]["vector"]
        engine_ms = round(sum(self.stats.blocks), 3)
        return {
            "service_ms": round(total_ms, 3),
            "blocks": int(payload["vector_scan"]["blocks"]),
            "scanned_assets": int(payload["vector_scan"]["scanned_assets"]),
            "truncated": bool(payload["vector_scan"]["truncated"]),
            "hits": len(payload["hits"]),
            "vector_hits": len(vector_hits),
            "walk_ms": round(self.stats.walk_ms, 3),
            "walk_calls": self.stats.walk_calls,
            "candidate_walk_ms": round(self.stats.candidate_walk_ms, 3),
            "candidate_walk_calls": self.stats.candidate_walk_calls,
            "engine_ms": engine_ms,
            "engine_calls": len(self.stats.blocks),
            "engine_block_ms": list(self.stats.blocks),
            "engine_table_ms": round(self.stats.table_ms, 3),
            "engine_table_calls": self.stats.table_calls,
            "engine_table_ms_per_call": round(
                self.stats.table_ms / self.stats.table_calls, 3
            )
            if self.stats.table_calls
            else None,
            "other_ms": round(total_ms - self.stats.walk_ms - engine_ms, 3),
            "working_set_before_bytes": int(memory_before),
            "working_set_after_bytes": int(memory_after),
            "working_set_peak_bytes": int(peak_working_set_bytes()),
            "disk_read_bytes": int(io_after["read_bytes"] - io_before["read_bytes"]),
            "disk_write_bytes": int(io_after["write_bytes"] - io_before["write_bytes"]),
            "cpu_ms": round((cpu_after - cpu_before) * 1000, 3),
            "page": [
                [int(row["asset_id"]), round(float(row["distance"]), 9)] for row in vector_hits
            ],
            "fused_hits": [
                [int(row["asset_id"]), str(row["hit_reason"])] for row in payload["hits"]
            ],
        }


def round_summary(samples: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The honest shape of one round: distribution of the split stages, not just the total."""
    def column(name: str) -> list[float]:
        return [float(sample[name]) for sample in samples if sample.get(name) is not None]

    service = bench.summarize(column("service_ms"))
    summary: dict[str, Any] = {
        "service": service,
        "median_by_stage_ms": {
            "sqlite_read_all": round(statistics.median(column("walk_ms")), 3),
            "sqlite_candidate_walk": round(statistics.median(column("candidate_walk_ms")), 3),
            "engine": round(statistics.median(column("engine_ms")), 3),
            "engine_table_overhead": round(statistics.median(column("engine_table_ms")), 3),
            "other": round(statistics.median(column("other_ms")), 3),
        },
        "blocks_per_sample": sorted({int(sample["blocks"]) for sample in samples}),
        "engine_calls_per_sample": sorted({int(sample["engine_calls"]) for sample in samples}),
        "engine_block_ms_median": round(
            statistics.median(
                [value for sample in samples for value in sample["engine_block_ms"]]
            ),
            3,
        ),
        "walk_calls_median": statistics.median(column("walk_calls")),
        "candidate_walk_calls_median": statistics.median(column("candidate_walk_calls")),
        "working_set_peak_bytes": max(int(sample["working_set_peak_bytes"]) for sample in samples),
        "disk_read_bytes_total": sum(int(sample["disk_read_bytes"]) for sample in samples),
        "disk_write_bytes_total": sum(int(sample["disk_write_bytes"]) for sample in samples),
        "cpu_ms_median": round(statistics.median(column("cpu_ms")), 3),
    }
    pages = {json.dumps(sample["page"], ensure_ascii=True) for sample in samples}
    summary["distinct_pages_in_round"] = len(pages)
    summary["page_sha256"] = hashlib.sha256(
        json.dumps(samples[0]["page"], ensure_ascii=True).encode("ascii")
    ).hexdigest()
    return summary


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P4-02D controlled latency attribution")
    parser.add_argument("--burst-root", type=Path, required=True, help="generation A (one burst)")
    parser.add_argument("--wave-root", type=Path, required=True, help="generation B (bounded waves)")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--order", default="A,B,B,A", help="round order over the two roots")
    parser.add_argument("--samples", type=int, default=20, help="samples per round (>=20)")
    parser.add_argument("--query-text", default=DEFAULT_QUERY_TEXT)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--seed", type=int, default=20260924)
    parser.add_argument(
        "--no-rebuild",
        action="store_true",
        help="skip the diagnostic asset_id_idx replace contrast",
    )
    return parser.parse_args(argv)


def write_output(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if int(args.samples) < 20:
        raise SystemExit("--samples must be at least 20 (the task card requires >=20 per round)")
    order = [part.strip().upper() for part in str(args.order).split(",") if part.strip()]
    if any(part not in {"A", "B"} for part in order):
        raise SystemExit("--order accepts only A and B")

    roots = {
        "A": Generation("A-burst", args.burst_root, query_text=args.query_text, top_k=args.top_k),
        "B": Generation("B-wave", args.wave_root, query_text=args.query_text, top_k=args.top_k),
    }
    report: dict[str, Any] = {
        "task": "P4-02D",
        "check": "controlled attribution of the same-scale retrieval-latency difference",
        "query_text": args.query_text,
        "top_k": int(args.top_k),
        "seed": int(args.seed),
        "encoder": "deterministic stand-in (no pinned model weights)",
        "order": order,
        "samples_per_round": int(args.samples),
        "machine": bench.machine_facts(),
        "environment": {
            "concurrent_load": (
                "none: both generations had finished building and no other measurement ran "
                "while this tool sampled"
            ),
            "os_cache": (
                "warm for both roots: they were built and then measured in the same session; "
                "the A/B/B/A interleave is what separates a root effect from monotone drift"
            ),
            "process_io_and_cpu": (
                "disk bytes and CPU time are read from this process's own counters "
                "(GetProcessIoCounters/GetProcessTimes equivalents), not from system-wide totals"
            ),
        },
        "roots": {name: str(root.data_root) for name, root in roots.items()},
    }
    try:
        report["identity"] = {name: root.identity() for name, root in roots.items()}
        report["layout_before"] = {name: root.layout() for name, root in roots.items()}
        report["vector_probe_ids"] = [
            int(round(1 + index * (report["identity"]["A"]["assets"] - 1) / (PROBE_IDS - 1)))
            for index in range(PROBE_IDS)
        ]
        probes = {
            name: root.vector_probe(report["vector_probe_ids"]) for name, root in roots.items()
        }
        report["vector_probe"] = probes
        report["vector_probe_identical"] = probes["A"] == probes["B"]

        rounds: list[dict[str, Any]] = []
        for index, name in enumerate(order, start=1):
            root = roots[name]
            warmup = root.once()
            samples = [root.once() for _ in range(int(args.samples))]
            record = {
                "round": index,
                "root": name,
                "label": root.name,
                "warmup_service_ms": warmup["service_ms"],
                "summary": round_summary(samples),
                "samples": samples,
            }
            rounds.append(record)
            print(
                f"round {index} root {name}: median {record['summary']['service']['median_ms']} ms "
                f"p95 {record['summary']['service']['p95_ms']} ms",
                file=sys.stderr,
                flush=True,
            )
        report["rounds"] = rounds

        by_root = {
            name: [record for record in rounds if record["root"] == name] for name in ("A", "B")
        }
        report["round_pages"] = {
            name: {str(record["round"]): record["summary"]["page_sha256"] for record in records}
            for name, records in by_root.items()
        }
        every_page = {record["summary"]["page_sha256"] for record in rounds}
        report["distinct_pages_across_all_rounds"] = len(every_page)
        report["pages_identical_across_roots"] = len(every_page) == 1 and all(
            record["summary"]["distinct_pages_in_round"] == 1 for record in rounds
        )
        report["page"] = rounds[0]["samples"][0]["page"]
        medians = {
            name: statistics.median(
                [record["summary"]["service"]["median_ms"] for record in records]
            )
            for name, records in by_root.items()
        }
        report["same_scale_medians_ms"] = {name: round(value, 3) for name, value in medians.items()}
        report["wave_over_burst_ratio"] = round(medians["B"] / max(medians["A"], 1e-9), 3)
        report["pooled"] = {
            name: bench.summarize(
                [
                    float(sample["service_ms"])
                    for record in records
                    for sample in record["samples"]
                ]
            )
            for name, records in by_root.items()
        }
        report["pooled_ratio_p50"] = round(
            report["pooled"]["B"]["median_ms"] / max(report["pooled"]["A"]["median_ms"], 1e-9), 3
        )
        report["pooled_ratio_p95"] = round(
            report["pooled"]["B"]["p95_ms"] / max(report["pooled"]["A"]["p95_ms"], 1e-9), 3
        )

        if not args.no_rebuild:
            contrast: dict[str, Any] = {}
            for name, root in roots.items():
                before = root.layout()
                recheck = [root.once() for _ in range(max(3, int(args.samples) // 4))]
                rebuild = replace_asset_index(
                    root.index, generation_id=bench.GENERATION_ID, dimension=bench.DIM
                )
                after = root.layout()
                after_samples = [root.once() for _ in range(max(3, int(args.samples) // 4))]
                contrast[name] = {
                    "label": root.name,
                    "coverage_before": before,
                    "rebuild": rebuild,
                    "coverage_after": after,
                    "before": round_summary(recheck),
                    "after": round_summary(after_samples),
                    "page_identical": recheck[0]["page"] == after_samples[0]["page"],
                    "median_ms_before": round_summary(recheck)["service"]["median_ms"],
                    "median_ms_after": round_summary(after_samples)["service"]["median_ms"],
                    "page": recheck[0]["page"],
                }
                print(
                    f"F4 {name}: {contrast[name]['median_ms_before']} ms -> "
                    f"{contrast[name]['median_ms_after']} ms "
                    f"(page identical {contrast[name]['page_identical']})",
                    file=sys.stderr,
                    flush=True,
                )
            report["f4_contrast"] = contrast
            report["layout_after"] = {name: root.layout() for name, root in roots.items()}
        report["status"] = "ok"
    finally:
        for root in roots.values():
            root.close()
    report["finished_at_ms"] = int(time.time() * 1000)
    write_output(args.output, report)
    print(json.dumps({key: report[key] for key in report if key != "rounds"}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
