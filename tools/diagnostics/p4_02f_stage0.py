"""P4-02F stage 0: the six attribution facts of the F2 retrieval shape (pure measurement).

P4-02B measured the 300 000-asset warm text search at service P95 **125 174.8 ms** against
the architecture §12.2 target of ≤ 1 s. P4-02D then attributed the *same-scale* difference
between two publication shapes to the ``asset_id_idx`` coverage. The P4-02F card splits the
remaining work into a **stage 0** (纯测量, six mandatory facts) and a **stage 1** decision
gate; this tool delivers the six facts and nothing else:

1. ``stages``      - one service search split into the authoritative SQLite candidate fetch,
                     every candidate block's Lance round trip (the ``_table`` handle overhead,
                     the engine pass, the Arrow->Python row conversion and the row
                     materialisation kept apart) and the fusion / authoritative re-read /
                     pending top-up stages that surround them;
2. ``blocks``      - the block-size matrix {250, 1000, 5000, 20000}: engine-call count and
                     wall time per size, proving every size returns the *same*
                     ``(distance, asset_id)`` page (the block bound is an access-path
                     parameter, never a recall parameter);
3. ``coverage``    - ``num_indexed_rows``/``num_unindexed_rows``, the Lance table version and
                     the fragment layout of every measured root, plus *one* diagnostic
                     ``replace`` rebuild as the single-variable contrast (opt-in ``--rebuild``);
4. ``overhead``    - the fixed per-``search_candidates``-call cost of the repeated
                     ``lancedb.connect`` / ``open_table`` / schema check, measured directly,
                     and the saving one reused table handle would give per query;
5. ``single-pass`` - one engine query with *no* candidate constraint, as a **diagnostic lower
                     bound only** (it is not the A3 authorisation and it is not a claim of
                     equivalence with the authoritative candidate set);
6. the burst and the wave root side by side in ``stages``, so the P4-02B
                     3 484.4 ms / 8 317.8 ms pair can be attributed to the stage it lands in.
7. ``bounded-blocks`` - the **P4-02M bounded** block-size matrix. P4-02M made
                     ``vector_channel`` ask the engine for ``top_k + 1`` rows per block
                     (``LanceVectorIndex.search_candidates_bounded``, product code) and fall
                     back to the exhaustive ``search_candidates`` call only when a block's
                     boundary tie group was cut, so fact 2's block-size conclusion - measured
                     on the ``limit = len(ids)`` exhaustive path - cannot be reused here.
                     This mode alternates the product's own 5 000-row block with every
                     candidate size and records, per sample, the ``vector_channel`` time, the
                     full ``search_assets`` service time, ``engine_blocks``, the bounded and
                     fallback call counts, the rows every block actually returned and the
                     ``(asset_id, distance)`` page. The service's own ``vector_channel`` is
                     reached through a per-process wrapper that injects the block size; no
                     product file changes and no other mode changes behaviour.

The tool never changes product behaviour: it imports the product's own retrieval entry points
(``search_assets`` / ``vector_channel`` / ``LanceVectorIndex``) and only observes them. The
single state change is the diagnostic ``replace`` rebuild of a scalar index inside a
throw-away ``tmp/`` data root, recorded as its own step and skipped unless ``--rebuild`` is
given.

Usage::

    uv run --no-sync python tools/diagnostics/p4_02f_stage0.py \
        --root "A=tmp/p402d 归因 验证/scale-100000-burst/data" \
        --root "B=tmp/p402f-阶段0/scale-100000-wave/data" \
        --order A,B,B,A --samples 20 \
        --output test-results/p4-02f-stage0-100k.json

    uv run --no-sync python tools/diagnostics/p4_02f_stage0.py \
        --root "W=tmp/p402f-阶段0/scale-300000-wave/data" \
        --modes identity,bounded-blocks --order W --top-k 20 \
        --bounded-candidates 10000,15000,20000,30000,50000 --bounded-rounds 2 \
        --output test-results/p4-02m-bounded-blocks-300k.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ann_scale_benchmark as bench  # noqa: E402
import numpy as np  # noqa: E402
import p4_02d_attribution as prior  # noqa: E402

from photo_search.catalog.db import Catalog  # noqa: E402
from photo_search.config import vectors_dir  # noqa: E402
from photo_search.indexing import maintenance  # noqa: E402
from photo_search.indexing.store import LanceVectorIndex  # noqa: E402
from photo_search.search import service as search_service  # noqa: E402
from photo_search.search.retrieval import (  # noqa: E402
    SearchScope,
    resolve_binding,
    vector_channel,
)
from photo_search.search.service import search_assets  # noqa: E402

DEFAULT_QUERY_TEXT = "西湖 sunset 全家福"
DEFAULT_TOP_K = 20
DEFAULT_BLOCK_SIZES = (250, 1000, 5000, 20000)
#: The product's own block bound (``retrieval.SCOPE_BLOCK_SIZE``), used for the ``stages`` mode.
PRODUCT_BLOCK_SIZE = 5000
#: How many spread-out assets get a direct vector comparison across roots.
PROBE_IDS = 64
#: The product stages this tool times around the two channels. They are patched on the
#: imported ``service`` module (process-global, which is fine for a standalone diagnostic) and
#: every name is optional, so a stage that does not exist is reported as not instrumented
#: instead of silently disappearing from the ledger.
SERVICE_STAGES = (
    "scope_view",
    "fts_channel",
    "reciprocal_rank_fusion",
    "authoritative_page",
    "pending_projection_page",
    "_verified_ids",
    "text_column_matches",
    "_rewrite_hit",
)


def _add(bucket: dict[str, float], key: str, value: float) -> None:
    bucket[key] = bucket.get(key, 0.0) + float(value)


# --------------------------------------------------------------------------- tally
class Stats:
    """Per-sample tally of the product call sites this tool observes."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.sqlite_ms = 0.0
        self.sqlite_calls = 0
        self.candidate_walk_ms = 0.0
        self.candidate_walk_calls = 0
        self.pending_topup_ms = 0.0
        self.pending_topup_calls = 0
        self.stage_ms: dict[str, float] = {}
        self.stage_calls: dict[str, int] = {}
        self.blocks: list[dict[str, Any]] = []
        self.block: dict[str, float] | None = None
        self.table_ms = 0.0
        self.table_calls = 0
        # P4-02M bounded access path. ``bounded_blocks`` holds one record per
        # ``LanceVectorIndex.search_candidates_bounded`` call, so the bounded path's own
        # table/plan/arrow/convert split is measured here instead of being inferred from the
        # residual bucket (that was F2 of the P4-02M review). After P4-02M every record in
        # ``blocks`` is a fallback call, and ``fallback_*`` counts those explicitly.
        self.bounded_calls = 0
        self.bounded_blocks: list[dict[str, Any]] = []
        self.bounded_rows: list[int] = []
        self.bounded_limits: list[int] = []
        self.fallback_calls = 0
        self.fallback_rows: list[int] = []
        self.pending_fallback = False

    def add_stage(self, name: str, milliseconds: float) -> None:
        _add(self.stage_ms, name, milliseconds)
        self.stage_calls[name] = self.stage_calls.get(name, 0) + 1


def instrument_catalog(catalog: Catalog, stats: Stats) -> None:
    """Time every SQLite ``read_all`` of this catalog, in place.

    ``read_all`` is the single door the retrieval channels use for their candidate rows, so
    patching the *instance* measures the real product call site without a parallel copy of the
    query. The confirmed-keyset block walk is split out by SQL signature, which is the
    "SQLite 候选获取" fact the task card asks for.
    """
    original = catalog.read_all

    def timed_read_all(sql: str, parameters: tuple = ()) -> list[Any]:
        started = time.perf_counter()
        rows = original(sql, parameters)
        elapsed = (time.perf_counter() - started) * 1000
        stats.sqlite_ms += elapsed
        stats.sqlite_calls += 1
        text = str(sql)
        # The candidate keyset walk and the pending-projection top-up both mention
        # ``index_entries`` and ``CONFIRMED``; only the exact block-query signature may be
        # counted as the walk, or the top-up (which is the more expensive of the two at
        # 300 000 assets) would be mislabelled as candidate fetching.
        if "FROM index_entries AS e JOIN assets AS a" in text and "e.state = 'CONFIRMED'" in text:
            stats.candidate_walk_ms += elapsed
            stats.candidate_walk_calls += 1
        elif "NOT EXISTS (SELECT 1 FROM index_entries" in text:
            stats.pending_topup_ms += elapsed
            stats.pending_topup_calls += 1
        return rows

    catalog.read_all = timed_read_all  # type: ignore[method-assign]


def instrument_service(stats: Stats) -> list[str]:
    """Time the product's own fusion / authority / top-up stages around the channels."""
    instrumented: list[str] = []
    for name in SERVICE_STAGES:
        target = getattr(search_service, name, None)
        if target is None or getattr(target, "__p4_02f_timed__", False):
            continue

        def timed(*args: Any, _stage: str = name, _original: Any = target, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                return _original(*args, **kwargs)
            finally:
                stats.add_stage(_stage, (time.perf_counter() - started) * 1000)

        timed.__p4_02f_timed__ = True  # type: ignore[attr-defined]
        setattr(search_service, name, timed)
        instrumented.append(name)
    catch_up = search_service.search_documents.catch_up
    if not getattr(catch_up, "__p4_02f_timed__", False):
        def timed_catch_up(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                return catch_up(*args, **kwargs)
            finally:
                stats.add_stage("documents.catch_up", (time.perf_counter() - started) * 1000)

        timed_catch_up.__p4_02f_timed__ = True  # type: ignore[attr-defined]
        search_service.search_documents.catch_up = timed_catch_up
        instrumented.append("documents.catch_up")
    return instrumented


# --------------------------------------------------------------------------- Lance proxies
class _ArrowProxy:
    """The Arrow table handed back by ``builder.to_arrow()``, with ``to_pylist`` timed."""

    __slots__ = ("_arrow", "_bucket")

    def __init__(self, arrow: Any, bucket: dict[str, float]) -> None:
        self._arrow = arrow
        self._bucket = bucket

    def to_pylist(self, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return self._arrow.to_pylist(*args, **kwargs)
        finally:
            _add(self._bucket, "convert_ms", (time.perf_counter() - started) * 1000)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._arrow, name)


class _BuilderProxy:
    """The engine query builder, with the chain and the engine pass timed apart."""

    __slots__ = ("_builder", "_bucket")

    def __init__(self, builder: Any, bucket: dict[str, float]) -> None:
        self._builder = builder
        self._bucket = bucket

    def _chain(self, name: str, *args: Any, **kwargs: Any) -> _BuilderProxy:
        started = time.perf_counter()
        try:
            self._builder = getattr(self._builder, name)(*args, **kwargs)
        finally:
            _add(self._bucket, "plan_ms", (time.perf_counter() - started) * 1000)
        return self

    def limit(self, *args: Any, **kwargs: Any) -> _BuilderProxy:
        return self._chain("limit", *args, **kwargs)

    def select(self, *args: Any, **kwargs: Any) -> _BuilderProxy:
        return self._chain("select", *args, **kwargs)

    def where(self, *args: Any, **kwargs: Any) -> _BuilderProxy:
        return self._chain("where", *args, **kwargs)

    def to_arrow(self, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        arrow = self._builder.to_arrow(*args, **kwargs)
        _add(self._bucket, "arrow_ms", (time.perf_counter() - started) * 1000)
        return _ArrowProxy(arrow, self._bucket)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._builder, name)


class _TableProxy:
    """One opened Lance table, with ``search()`` handing back a timed builder."""

    __slots__ = ("_table", "_stats")

    def __init__(self, table: Any, stats: Stats) -> None:
        self._table = table
        self._stats = stats

    def search(self, *args: Any, **kwargs: Any) -> _BuilderProxy:
        bucket = self._stats.block if self._stats.block is not None else {}
        started = time.perf_counter()
        try:
            builder = self._table.search(*args, **kwargs)
        finally:
            _add(bucket, "plan_ms", (time.perf_counter() - started) * 1000)
        if self._stats.block is None:
            self._stats.block = bucket
        return _BuilderProxy(builder, bucket)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._table, name)


class TimedIndex:
    """The Lance adapter with the handle lookup and every candidate round trip timed."""

    def __init__(self, index: LanceVectorIndex, stats: Stats) -> None:
        self._index = index
        self._stats = stats
        self.original_table = index._table

        def timed_table(**kwargs: Any) -> Any:
            started = time.perf_counter()
            table = self.original_table(**kwargs)
            elapsed = (time.perf_counter() - started) * 1000
            stats.table_ms += elapsed
            stats.table_calls += 1
            if stats.block is not None:
                _add(stats.block, "table_ms", elapsed)
            if table is None:
                return None
            bucket = stats.block if stats.block is not None else {}
            if stats.block is None:
                stats.block = bucket
            return _TableProxy(table, stats)

        index._table = timed_table  # type: ignore[method-assign]

    def search_candidates(self, **kwargs: Any) -> list[dict[str, Any]]:
        self._stats.block = {}
        started = time.perf_counter()
        try:
            rows = self._index.search_candidates(**kwargs)
        finally:
            total = (time.perf_counter() - started) * 1000
        bucket = self._stats.block or {}
        record: dict[str, Any] = {
            key: round(float(value), 3) for key, value in bucket.items()
        }
        for key in ("table_ms", "plan_ms", "arrow_ms", "convert_ms"):
            record.setdefault(key, 0.0)
        record["total_ms"] = round(total, 3)
        # Everything the block spent outside the four measured pieces is the product's own
        # Python work: the ``asset_id IN (...)`` string, the dict/sort/space checks.
        record["materialize_ms"] = round(
            max(
                total
                - record["table_ms"]
                - record["plan_ms"]
                - record["arrow_ms"]
                - record["convert_ms"],
                0.0,
            ),
            3,
        )
        record["candidates"] = len(kwargs.get("asset_ids") or ())
        record["limit"] = int(kwargs.get("limit") or 0)
        record["rows"] = len(rows)
        # P4-02M: after a bounded call reported ``resolved=False``, the next
        # ``search_candidates`` call the product makes is its own fallback for that block, so
        # it is counted as a fallback here and nowhere else.
        if self._stats.pending_fallback:
            self._stats.fallback_calls += 1
            self._stats.fallback_rows.append(len(rows))
            self._stats.pending_fallback = False
        self._stats.blocks.append(record)
        self._stats.block = None
        return rows

    def search_candidates_bounded(self, **kwargs: Any) -> tuple[list[dict[str, Any]], bool]:
        """Time the P4-02M bounded product call and count the caller's fallbacks.

        The product method is **not** re-implemented: this only observes
        ``LanceVectorIndex.search_candidates_bounded`` - the same ``top_k + 1`` request the
        product's ``vector_channel`` makes - and records the rows it actually returned and
        whether it reported ``resolved``. That is what turns "the new access path is in use"
        from an assumption into a measured fact, and it also gives the bounded path its own
        table/plan/arrow/convert split (P4-02M review F2).
        """
        self._stats.block = {}
        started = time.perf_counter()
        try:
            rows, resolved = self._index.search_candidates_bounded(**kwargs)
        finally:
            total = (time.perf_counter() - started) * 1000
        bucket = self._stats.block or {}
        record: dict[str, Any] = {
            key: round(float(value), 3) for key, value in bucket.items()
        }
        for key in ("table_ms", "plan_ms", "arrow_ms", "convert_ms"):
            record.setdefault(key, 0.0)
        record["total_ms"] = round(total, 3)
        record["materialize_ms"] = round(
            max(
                total
                - record["table_ms"]
                - record["plan_ms"]
                - record["arrow_ms"]
                - record["convert_ms"],
                0.0,
            ),
            3,
        )
        record["candidates"] = len(kwargs.get("asset_ids") or ())
        record["requested_limit"] = int(kwargs.get("top_k") or 0) + 1
        record["rows"] = len(rows)
        record["resolved"] = bool(resolved)
        self._stats.bounded_blocks.append(record)
        self._stats.bounded_calls += 1
        self._stats.bounded_rows.append(len(rows))
        self._stats.bounded_limits.append(int(record["requested_limit"]))
        self._stats.pending_fallback = not bool(resolved)
        self._stats.block = None
        return rows, bool(resolved)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._index, name)


class _BlockSizeOverride:
    """Inject ``block_size`` into the product service's own ``vector_channel`` call.

    ``search/service.py`` calls ``vector_channel`` without a block size, so the product
    always uses ``retrieval.SCOPE_BLOCK_SIZE``; its default argument is bound at definition
    time, which is why the constant itself cannot be patched. This wrapper replaces *this
    process's* reference to the imported function for the duration of one measurement and
    restores it afterwards, so only the block size differs from the product call - that is
    the single parameter under study.
    """

    def __init__(self, block_size: int) -> None:
        self.block_size = int(block_size)
        self.original: Any = None
        self.last_block: dict[str, Any] | None = None

    def __enter__(self) -> _BlockSizeOverride:
        self.original = search_service.vector_channel

        def patched(catalog: Any, **kwargs: Any) -> Any:
            kwargs["block_size"] = self.block_size
            block = self.original(catalog, **kwargs)
            self.last_block = block
            return block

        search_service.vector_channel = patched
        return self

    def __exit__(self, *exc: Any) -> bool:
        search_service.vector_channel = self.original
        return False


# --------------------------------------------------------------------------- one root
class Generation:
    """One built data root: its identity, its layout and its instrumented query entries."""

    def __init__(self, label: str, data_root: Path, *, stats: Stats, top_k: int) -> None:
        self.label = label
        self.data_root = Path(data_root)
        self.top_k = int(top_k)
        self.catalog = Catalog.open(data_root=self.data_root)
        self.index = LanceVectorIndex(vectors_dir(self.data_root))
        self.encoder = bench.DeterministicEncoder()
        self.stats = stats
        instrument_catalog(self.catalog, stats)
        self.timed_index = TimedIndex(self.index, stats)
        self.binding = resolve_binding(
            self.catalog,
            generation_id=bench.GENERATION_ID,
            space_id=bench.SPACE_ID,
            dimension=bench.DIM,
        )
        self.query_vector = [
            float(value)
            for value in self.encoder.encode_queries([DEFAULT_QUERY_TEXT])[0]
        ]

    def close(self) -> None:
        self.catalog.close()

    # --------------------------------------------------------------- identity / layout
    def identity(self) -> dict[str, Any]:
        asset_ids = [
            int(row["asset_id"])
            for row in self.catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id")
        ]
        digest = hashlib.sha256(
            ",".join(str(value) for value in asset_ids).encode("ascii")
        ).hexdigest()
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
            "label": self.label,
            "data_root": str(self.data_root),
            "assets": len(asset_ids),
            "assets_digest_sha256": digest,
            "assets_min": min(asset_ids) if asset_ids else None,
            "assets_max": max(asset_ids) if asset_ids else None,
            "index_entries": len(entries),
            "index_entries_digest_sha256": entry_digest,
            "index_entry_states": states,
            "semantic_status": maintenance.semantic_index_status(self.catalog.db_path)["index"],
            "search_projection": self.catalog.read_one(
                "SELECT COUNT(*) FROM search_documents"
            )[0],
        }

    def layout(self) -> dict[str, Any]:
        return prior.lance_coverage(
            self.index, generation_id=bench.GENERATION_ID, dimension=bench.DIM
        )

    def vector_probe(self, ids: list[int]) -> list[list[Any]]:
        rows = self.index.search_candidates(
            generation_id=bench.GENERATION_ID,
            dimension=bench.DIM,
            query_vector=self.query_vector,
            asset_ids=ids,
            limit=len(ids),
            space_id=bench.SPACE_ID,
        )
        return [[int(row["asset_id"]), round(float(row["distance"]), 9)] for row in rows]

    # --------------------------------------------------------------- one service search
    def once(self) -> dict[str, Any]:
        self.stats.reset()
        memory_before = prior.working_set_bytes()
        io_before = prior.io_bytes()
        cpu_before = time.process_time()
        started = time.perf_counter()
        payload = search_assets(
            self.catalog,
            text=DEFAULT_QUERY_TEXT,
            top_k=self.top_k,
            vector_index=self.timed_index,  # type: ignore[arg-type]
            encoder=self.encoder,
            space_id=bench.SPACE_ID,
            generation_id=bench.GENERATION_ID,
            dimension=bench.DIM,
        )
        total_ms = (time.perf_counter() - started) * 1000
        io_after = prior.io_bytes()
        cpu_after = time.process_time()
        blocks = self.stats.blocks
        engine_ms = sum(record["total_ms"] for record in blocks)
        vector_hits = payload["channels"]["vector"]
        return {
            "service_ms": round(total_ms, 3),
            "blocks": int(payload["vector_scan"]["blocks"]),
            "scanned_assets": int(payload["vector_scan"]["scanned_assets"]),
            "truncated": bool(payload["vector_scan"]["truncated"]),
            "hits": len(payload["hits"]),
            "vector_hits": len(vector_hits),
            "fts_hits": len(payload["channels"]["fts"]),
            "sqlite_ms": round(self.stats.sqlite_ms, 3),
            "sqlite_calls": self.stats.sqlite_calls,
            "sqlite_candidate_walk_ms": round(self.stats.candidate_walk_ms, 3),
            "sqlite_candidate_walk_calls": self.stats.candidate_walk_calls,
            "sqlite_pending_topup_ms": round(self.stats.pending_topup_ms, 3),
            "sqlite_pending_topup_calls": self.stats.pending_topup_calls,
            "engine_ms": round(engine_ms, 3),
            "engine_calls": len(blocks),
            "engine_table_ms": round(sum(r["table_ms"] for r in blocks), 3),
            "engine_plan_ms": round(sum(r["plan_ms"] for r in blocks), 3),
            "engine_arrow_ms": round(sum(r["arrow_ms"] for r in blocks), 3),
            "engine_convert_ms": round(sum(r["convert_ms"] for r in blocks), 3),
            "engine_materialize_ms": round(sum(r["materialize_ms"] for r in blocks), 3),
            "stage_ms": {key: round(value, 3) for key, value in self.stats.stage_ms.items()},
            "stage_calls": dict(self.stats.stage_calls),
            "other_ms": round(total_ms - self.stats.sqlite_ms - engine_ms, 3),
            "engine_block_ms": [record["total_ms"] for record in blocks],
            "engine_block_arrow_ms": [record["arrow_ms"] for record in blocks],
            "block_candidates": [record["candidates"] for record in blocks],
            "block_rows": [record["rows"] for record in blocks],
            "working_set_before_bytes": int(memory_before),
            "working_set_peak_bytes": int(prior.peak_working_set_bytes()),
            "disk_read_bytes": int(io_after["read_bytes"] - io_before["read_bytes"]),
            "cpu_ms": round((cpu_after - cpu_before) * 1000, 3),
            "page": [
                [int(row["asset_id"]), round(float(row["distance"]), 9)] for row in vector_hits
            ],
            "fused_hits": [
                [int(row["asset_id"]), str(row["hit_reason"])] for row in payload["hits"]
            ],
        }

    # --------------------------------------------------------------- block-size matrix
    def block_matrix(self, *, block_sizes: list[int], samples: int) -> dict[str, Any]:
        scope = SearchScope.build()
        results: list[dict[str, Any]] = []
        for size in block_sizes:
            records: list[dict[str, Any]] = []
            for _ in range(samples):
                self.stats.reset()
                started = time.perf_counter()
                block = vector_channel(
                    self.catalog,
                    index=self.timed_index,  # type: ignore[arg-type]
                    binding=self.binding,
                    query_vector=self.query_vector,
                    scope=scope,
                    top_k=self.top_k,
                    block_size=int(size),
                )
                total_ms = (time.perf_counter() - started) * 1000
                records.append(
                    {
                        "ms": round(total_ms, 3),
                        "engine_ms": round(
                            sum(record["total_ms"] for record in self.stats.blocks), 3
                        ),
                        "walk_ms": round(self.stats.candidate_walk_ms, 3),
                        "blocks": int(block["blocks"]),
                        "scanned_assets": int(block["scanned_assets"]),
                        "truncated": bool(block["truncated"]),
                        "block_ms": [record["total_ms"] for record in self.stats.blocks],
                        "page": [
                            [int(row["asset_id"]), round(float(row["distance"]), 9)]
                            for row in block["hits"]
                        ],
                    }
                )
            pages = {json.dumps(record["page"], ensure_ascii=True) for record in records}
            results.append(
                {
                    "block_size": int(size),
                    "blocks": records[0]["blocks"],
                    "scanned_assets": records[0]["scanned_assets"],
                    "truncated": records[0]["truncated"],
                    "samples": samples,
                    "ms_median": round(statistics.median([r["ms"] for r in records]), 3),
                    "ms_all": [r["ms"] for r in records],
                    "engine_ms_median": round(
                        statistics.median([r["engine_ms"] for r in records]), 3
                    ),
                    "walk_ms_median": round(statistics.median([r["walk_ms"] for r in records]), 3),
                    "per_block_ms_median": round(
                        statistics.median([value for r in records for value in r["block_ms"]]), 3
                    ),
                    "per_block_ms_p95": round(
                        float(
                            sorted([value for r in records for value in r["block_ms"]])[
                                max(
                                    0,
                                    int(
                                        0.95
                                        * len(
                                            [
                                                value
                                                for r in records
                                                for value in r["block_ms"]
                                            ]
                                        )
                                    )
                                    - 1,
                                )
                            ]
                        ),
                        3,
                    ),
                    "page": records[0]["page"],
                    "distinct_pages": len(pages),
                }
            )
        baseline = results[0]["page"]
        for record in results:
            record["page_matches_smallest_block"] = record["page"] == baseline
        return {
            "sizes": [int(size) for size in block_sizes],
            "samples_per_size": int(samples),
            "results": results,
            "pages_identical": all(record["page_matches_smallest_block"] for record in results),
        }

    # --------------------------------------------------------------- fixed overhead
    def overhead(self, *, reps: int, ids: list[int]) -> dict[str, Any]:
        sample_ids = sorted({int(value) for value in ids})[:PRODUCT_BLOCK_SIZE]
        self.stats.reset()
        empty = self.timed_index.search_candidates(
            generation_id=bench.GENERATION_ID,
            dimension=bench.DIM,
            query_vector=self.query_vector,
            asset_ids=[],
            limit=self.top_k,
            space_id=bench.SPACE_ID,
        )
        small = self.timed_index.search_candidates(
            generation_id=bench.GENERATION_ID,
            dimension=bench.DIM,
            query_vector=self.query_vector,
            asset_ids=sample_ids[:50],
            limit=self.top_k,
            space_id=bench.SPACE_ID,
        )
        full = self.timed_index.search_candidates(
            generation_id=bench.GENERATION_ID,
            dimension=bench.DIM,
            query_vector=self.query_vector,
            asset_ids=sample_ids,
            limit=self.top_k,
            space_id=bench.SPACE_ID,
        )
        handle_calls: list[float] = []
        schema_calls: list[float] = []
        cached = self.timed_index.original_table(
            generation_id=bench.GENERATION_ID, dimension=bench.DIM, create=False
        )
        for _ in range(int(reps)):
            started = time.perf_counter()
            self.timed_index.original_table(
                generation_id=bench.GENERATION_ID, dimension=bench.DIM, create=False
            )
            handle_calls.append((time.perf_counter() - started) * 1000)
            started = time.perf_counter()
            cached.schema.field("vector").type
            schema_calls.append((time.perf_counter() - started) * 1000)
        handle_median = statistics.median(handle_calls) if handle_calls else 0.0
        return {
            "reps": int(reps),
            "handle_ms_per_call": [round(value, 3) for value in handle_calls],
            "handle_ms_median": round(handle_median, 3),
            "handle_ms_min": round(min(handle_calls), 3) if handle_calls else None,
            "handle_ms_max": round(max(handle_calls), 3) if handle_calls else None,
            "schema_field_ms_median": round(
                statistics.median(schema_calls), 4
            )
            if schema_calls
            else None,
            "empty_candidates_block": self.stats.blocks[-3],
            "fifty_candidates_block": self.stats.blocks[-2],
            "full_block": self.stats.blocks[-1],
            "empty_rows": len(empty),
            "fifty_rows": len(small),
            "full_rows": len(full),
        }

    # ------------------------------------------------- numpy flat-exact reference
    def numpy_flat_exact(self, *, samples: int) -> dict[str, Any]:
        """Pure NumPy reference: read the frozen generation, then ``matrix @ q`` + Top-K.

        Read-only and deliberately outside the product: it reuses the very fixture the other
        modes measure, takes only the product's Top-K with ``argpartition`` (never a full
        sort) and reports the matrix read and the pure query separately. It is a bottleneck
        yardstick only - it authorises no A3/ANN work and adds no product index.
        """
        import pyarrow as pa  # the heavy engine import stays inside this diagnostic

        top_k = int(self.top_k)
        if top_k < 1:
            raise SystemExit("--top-k must be at least 1")
        accessor = getattr(self.timed_index, "original_table", None) or self.index._table
        ready = time.perf_counter()
        table = accessor(generation_id=bench.GENERATION_ID, dimension=bench.DIM, create=False)
        if table is None:
            raise SystemExit("the root has no vector table for the frozen generation")
        arrow = table.to_arrow()
        asset_ids = arrow["asset_id"].to_numpy(zero_copy_only=False)
        flat = pa.compute.list_flatten(arrow["vector"].combine_chunks())
        matrix = np.ascontiguousarray(
            flat.to_numpy(zero_copy_only=False).reshape(len(asset_ids), bench.DIM),
            dtype=np.float32,
        )
        read_ms = (time.perf_counter() - ready) * 1000
        del arrow, flat
        stride = max(1, len(asset_ids) // 1000)
        sampled = np.linalg.norm(matrix[::stride], axis=1)
        query = np.asarray(self.query_vector, dtype=np.float32)
        times: list[float] = []
        order: list[int] = []
        for _ in range(max(1, int(samples))):
            started = time.perf_counter()
            scores = matrix @ query
            candidates = np.argpartition(-scores, top_k - 1)[:top_k]
            order = [int(value) for value in candidates[np.argsort(-scores[candidates])]]
            times.append((time.perf_counter() - started) * 1000)
        numpy_page = [
            [int(asset_ids[row]), round(float(matrix[row] @ query), 9)] for row in order
        ]
        engine_page = self.index.search(
            generation_id=bench.GENERATION_ID,
            dimension=bench.DIM,
            query_vector=self.query_vector,
            limit=top_k,
        )
        numpy_ids = [int(asset_ids[row]) for row in order]
        engine_ids = [int(row["asset_id"]) for row in engine_page]
        return {
            "note": (
                "bottleneck yardstick only: a contiguous NumPy matrix is NOT a product index "
                "and this mode authorises no A3/ANN work"
            ),
            "rows": int(len(asset_ids)),
            "dimension": int(bench.DIM),
            "dtype": "float32",
            "matrix_mib": round(matrix.nbytes / 1048576, 2),
            "read_ms": round(read_ms, 3),
            "samples": int(samples),
            "top_k": top_k,
            "query_ms_median": round(statistics.median(times), 3),
            "query_ms_min": round(min(times), 3),
            "query_ms_all": [round(value, 3) for value in times],
            "vector_norm_sample": {
                "rows": int(len(sampled)),
                "min": round(float(sampled.min()), 6),
                "max": round(float(sampled.max()), 6),
            },
            "query_norm": round(float(np.linalg.norm(query)), 6),
            "identical_order_to_engine": numpy_ids == engine_ids,
            "overlap_ids": len(set(numpy_ids) & set(engine_ids)),
            "numpy_page": numpy_page,
            "engine_page": [
                [int(row["asset_id"]), round(float(row["distance"]), 9)] for row in engine_page
            ],
        }


    def single_pass(self, *, samples: int) -> dict[str, Any]:
        times: list[float] = []
        page: list[list[Any]] = []
        rows_count = 0
        for _ in range(int(samples)):
            started = time.perf_counter()
            rows = self.index.search(
                generation_id=bench.GENERATION_ID,
                dimension=bench.DIM,
                query_vector=self.query_vector,
                limit=self.top_k,
            )
            times.append((time.perf_counter() - started) * 1000)
            page = [[int(row["asset_id"]), round(float(row["distance"]), 9)] for row in rows]
            rows_count = len(rows)
        self.stats.reset()
        constrained = vector_channel(
            self.catalog,
            index=self.timed_index,  # type: ignore[arg-type]
            binding=self.binding,
            query_vector=self.query_vector,
            scope=SearchScope.build(),
            top_k=self.top_k,
        )
        constrained_page = [
            [int(row["asset_id"]), round(float(row["distance"]), 9)]
            for row in constrained["hits"]
        ]
        unconstrained_ids = {int(item[0]) for item in page}
        constrained_ids = {int(item[0]) for item in constrained_page}
        return {
            "note": (
                "diagnostic lower bound only: an unconstrained single engine pass is NOT the "
                "A3 authorisation and says nothing about equivalence with the authoritative "
                "candidate set"
            ),
            "samples": int(samples),
            "ms_median": round(statistics.median(times), 3),
            "ms_all": [round(value, 3) for value in times],
            "rows": rows_count,
            "page": page,
            "constrained_page": constrained_page,
            "same_page": page == constrained_page,
            "overlap_ids": len(unconstrained_ids & constrained_ids),
            "only_in_unconstrained": sorted(unconstrained_ids - constrained_ids)[:20],
        }

    # ------------------------------------------- P4-02M bounded block-size matrix
    def bounded_vector_once(self, *, block_size: int) -> dict[str, Any]:
        """One ``vector_channel`` sample on the P4-02M bounded access path."""
        self.stats.reset()
        started = time.perf_counter()
        block = vector_channel(
            self.catalog,
            index=self.timed_index,  # type: ignore[arg-type]
            binding=self.binding,
            query_vector=self.query_vector,
            scope=SearchScope.build(),
            top_k=self.top_k,
            block_size=int(block_size),
        )
        total_ms = (time.perf_counter() - started) * 1000
        return {
            "ms": round(total_ms, 3),
            "engine_ms": round(sum(record["total_ms"] for record in self.stats.blocks), 3),
            "bounded_engine_ms": round(
                sum(record["total_ms"] for record in self.stats.bounded_blocks), 3
            ),
            "walk_ms": round(self.stats.candidate_walk_ms, 3),
            "blocks": int(block["blocks"]),
            "scanned_assets": int(block["scanned_assets"]),
            "truncated": bool(block["truncated"]),
            "bounded_calls": int(self.stats.bounded_calls),
            "fallback_calls": int(self.stats.fallback_calls),
            "bounded_rows": [int(value) for value in self.stats.bounded_rows],
            "bounded_limits": [int(value) for value in self.stats.bounded_limits],
            "fallback_rows": [int(value) for value in self.stats.fallback_rows],
            "page": [
                [int(row["asset_id"]), round(float(row["distance"]), 9)]
                for row in block["hits"]
            ],
        }

    def bounded_service_once(self, *, block_size: int) -> dict[str, Any]:
        """One full ``search_assets`` sample whose vector channel carries ``block_size``."""
        self.stats.reset()
        with _BlockSizeOverride(block_size) as override:
            started = time.perf_counter()
            payload = search_assets(
                self.catalog,
                text=DEFAULT_QUERY_TEXT,
                top_k=self.top_k,
                vector_index=self.timed_index,  # type: ignore[arg-type]
                encoder=self.encoder,
                space_id=bench.SPACE_ID,
                generation_id=bench.GENERATION_ID,
                dimension=bench.DIM,
            )
            total_ms = (time.perf_counter() - started) * 1000
            block = override.last_block
        scan = payload.get("vector_scan") or {}
        hits = [] if block is None else list(block["hits"])
        return {
            "ms": round(total_ms, 3),
            "blocks": int(scan.get("blocks", 0)),
            "scanned_assets": int(scan.get("scanned_assets", 0)),
            "truncated": bool(scan.get("truncated", False)),
            "bounded_calls": int(self.stats.bounded_calls),
            "fallback_calls": int(self.stats.fallback_calls),
            "bounded_rows": [int(value) for value in self.stats.bounded_rows],
            "page": [
                [int(row["asset_id"]), round(float(row["distance"]), 9)] for row in hits
            ],
            "ranked_hits": [int(row["asset_id"]) for row in payload["hits"]],
        }

    def bounded_matrix(
        self, *, candidates: list[int], baseline: int, rounds: int
    ) -> dict[str, Any]:
        """Alternate the product block size with each candidate size, on the bounded path.

        The product's own 5 000-row block is measured *between* every pair of candidate
        measurements, so a monotone drift (cache, thermal) moves the control as well and
        cannot masquerade as a size effect. ``vector`` and full-service samples are taken
        back to back for the same size, and both pages are compared against the 5 000-row
        baseline item by item.
        """
        baseline = int(baseline)
        candidates = [int(value) for value in candidates if int(value) != baseline]
        sequence: list[int] = []
        for _ in range(max(1, int(rounds))):
            sequence.append(baseline)
            for size in candidates:
                sequence.append(size)
                sequence.append(baseline)
        warmup_vector = self.bounded_vector_once(block_size=baseline)
        warmup_service = self.bounded_service_once(block_size=baseline)
        collected: dict[int, dict[str, list[dict[str, Any]]]] = {}
        timeline: list[dict[str, Any]] = []
        for size in sequence:
            vector = self.bounded_vector_once(block_size=size)
            service = self.bounded_service_once(block_size=size)
            bucket = collected.setdefault(size, {"vector": [], "service": []})
            bucket["vector"].append(vector)
            bucket["service"].append(service)
            timeline.append(
                {
                    "block_size": size,
                    "vector_ms": vector["ms"],
                    "service_ms": service["ms"],
                    "engine_blocks": vector["blocks"],
                    "bounded_calls": vector["bounded_calls"],
                    "fallback_calls": vector["fallback_calls"],
                }
            )
            print(
                f"bounded {size}: vector {vector['ms']} ms / service {service['ms']} ms / "
                f"blocks {vector['blocks']} / bounded {vector['bounded_calls']} / "
                f"fallback {vector['fallback_calls']} / rows {vector['bounded_rows'][:3]}",
                file=sys.stderr,
                flush=True,
            )
        baseline_page = json.dumps(
            collected[baseline]["vector"][0]["page"], ensure_ascii=True
        )
        baseline_service_page = json.dumps(
            collected[baseline]["service"][0]["page"], ensure_ascii=True
        )
        results: list[dict[str, Any]] = []
        for size in [baseline, *candidates]:
            vector_runs = collected[size]["vector"]
            service_runs = collected[size]["service"]
            vector_ms = [run["ms"] for run in vector_runs]
            service_ms = [run["ms"] for run in service_runs]
            bounded_rows = [value for run in vector_runs for value in run["bounded_rows"]]
            fallback_rows = [value for run in vector_runs for value in run["fallback_rows"]]
            results.append(
                {
                    "block_size": size,
                    "blocks": int(vector_runs[0]["blocks"]),
                    "engine_blocks": int(vector_runs[0]["blocks"]),
                    "scanned_assets": int(vector_runs[0]["scanned_assets"]),
                    "truncated": bool(vector_runs[0]["truncated"]),
                    "service_blocks": int(service_runs[0]["blocks"]),
                    "service_truncated": bool(service_runs[0]["truncated"]),
                    "samples": len(vector_runs),
                    "top_k": int(self.top_k),
                    "vector_ms_median": round(statistics.median(vector_ms), 3),
                    "vector_ms_all": vector_ms,
                    "service_ms_median": round(statistics.median(service_ms), 3),
                    "service_ms_all": service_ms,
                    "engine_ms_median": round(
                        statistics.median([run["engine_ms"] for run in vector_runs]), 3
                    ),
                    "bounded_engine_ms_median": round(
                        statistics.median([run["bounded_engine_ms"] for run in vector_runs]), 3
                    ),
                    "walk_ms_median": round(
                        statistics.median([run["walk_ms"] for run in vector_runs]), 3
                    ),
                    "bounded_calls_all": [run["bounded_calls"] for run in vector_runs],
                    "fallback_calls_all": [run["fallback_calls"] for run in vector_runs],
                    "bounded_requested_limit": sorted(
                        {value for run in vector_runs for value in run["bounded_limits"]}
                    ),
                    "block_rows_first_sample": vector_runs[0]["bounded_rows"],
                    "block_rows_distinct": sorted(set(bounded_rows)),
                    "block_rows_median": round(statistics.median(bounded_rows), 3)
                    if bounded_rows
                    else None,
                    "fallback_rows_distinct": sorted(set(fallback_rows)),
                    "vector_page": vector_runs[0]["page"],
                    "vector_pages_stable_within_size": len(
                        {json.dumps(run["page"], ensure_ascii=True) for run in vector_runs}
                    )
                    == 1,
                    "vector_page_matches_baseline": json.dumps(
                        vector_runs[0]["page"], ensure_ascii=True
                    )
                    == baseline_page,
                    "service_page_matches_baseline": json.dumps(
                        service_runs[0]["page"], ensure_ascii=True
                    )
                    == baseline_service_page,
                    "service_pages_stable_within_size": len(
                        {json.dumps(run["page"], ensure_ascii=True) for run in service_runs}
                    )
                    == 1,
                    "ranked_hits": service_runs[0]["ranked_hits"],
                }
            )
        return {
            "baseline_block_size": baseline,
            "candidate_block_sizes": candidates,
            "rounds": max(1, int(rounds)),
            "interleaved_order": sequence,
            "top_k": int(self.top_k),
            "warmup": {"vector_ms": warmup_vector["ms"], "service_ms": warmup_service["ms"]},
            "timeline": timeline,
            "results": results,
            "baseline_page": collected[baseline]["vector"][0]["page"],
            "all_pages_match_baseline": all(
                record["vector_page_matches_baseline"]
                and record["service_page_matches_baseline"]
                for record in results
            ),
            "all_truncated_false": all(
                not record["truncated"] and not record["service_truncated"]
                for record in results
            ),
            "bounded_calls_positive_everywhere": all(
                min(record["bounded_calls_all"]) > 0 for record in results
            ),
        }


def round_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """The honest shape of one round: distribution of every stage, plus its share."""

    def column(name: str) -> list[float]:
        return [float(sample[name]) for sample in samples]

    service = bench.summarize(column("service_ms"))
    engine = statistics.median(column("engine_ms"))
    total = service["median_ms"]
    stages = {
        "sqlite_read_all": statistics.median(column("sqlite_ms")),
        "sqlite_candidate_walk": statistics.median(column("sqlite_candidate_walk_ms")),
        "sqlite_pending_topup": statistics.median(column("sqlite_pending_topup_ms")),
        "sqlite_other_reads": round(
            statistics.median(column("sqlite_ms"))
            - statistics.median(column("sqlite_candidate_walk_ms"))
            - statistics.median(column("sqlite_pending_topup_ms")),
            3,
        ),
        "engine_total": engine,
        "engine_table_handle": statistics.median(column("engine_table_ms")),
        "engine_plan": statistics.median(column("engine_plan_ms")),
        "engine_arrow_pass": statistics.median(column("engine_arrow_ms")),
        "engine_row_conversion": statistics.median(column("engine_convert_ms")),
        "engine_materialization": statistics.median(column("engine_materialize_ms")),
        "other_fusion_and_authority": statistics.median(column("other_ms")),
    }
    block_ms = [value for sample in samples for value in sample["engine_block_ms"]]
    stage_medians: dict[str, float] = {}
    stage_calls: dict[str, int] = {}
    for sample in samples:
        for key, value in sample["stage_ms"].items():
            stage_medians.setdefault(key, 0.0)
        for key, value in sample["stage_calls"].items():
            stage_calls[key] = max(stage_calls.get(key, 0), int(value))
    for key in stage_medians:
        stage_medians[key] = round(
            statistics.median([float(sample["stage_ms"].get(key, 0.0)) for sample in samples]), 3
        )
    pages = {json.dumps(sample["page"], ensure_ascii=True) for sample in samples}
    fused = {json.dumps(sample["fused_hits"], ensure_ascii=True) for sample in samples}
    return {
        "service": service,
        "median_by_stage_ms": {key: round(value, 3) for key, value in stages.items()},
        "median_share_percent": {
            key: round(100.0 * value / max(total, 1e-9), 2) for key, value in stages.items()
        },
        "stage_medians_ms": stage_medians,
        "stage_calls": stage_calls,
        "blocks_per_sample": sorted({int(sample["blocks"]) for sample in samples}),
        "engine_calls_per_sample": sorted({int(sample["engine_calls"]) for sample in samples}),
        "block_ms_median": round(statistics.median(block_ms), 3) if block_ms else None,
        "block_ms_p95": round(
            float(sorted(block_ms)[max(0, int(0.95 * len(block_ms)) - 1)]), 3
        )
        if block_ms
        else None,
        "block_ms_min": round(min(block_ms), 3) if block_ms else None,
        "block_ms_max": round(max(block_ms), 3) if block_ms else None,
        "block_rows_median": statistics.median(
            [value for sample in samples for value in sample["block_rows"]]
        )
        if block_ms
        else None,
        "working_set_peak_bytes": max(sample["working_set_peak_bytes"] for sample in samples),
        "disk_read_bytes_total": sum(sample["disk_read_bytes"] for sample in samples),
        "cpu_ms_median": round(statistics.median(column("cpu_ms")), 3),
        "distinct_pages_in_round": len(pages),
        "distinct_fused_hits_in_round": len(fused),
        "page_sha256": hashlib.sha256(
            json.dumps(samples[0]["page"], ensure_ascii=True).encode("ascii")
        ).hexdigest(),
        "fused_sha256": hashlib.sha256(
            json.dumps(samples[0]["fused_hits"], ensure_ascii=True).encode("ascii")
        ).hexdigest(),
    }


def parse_roots(values: list[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"--root wants LABEL=PATH, got {value!r}")
        label, path = value.split("=", 1)
        label = label.strip()
        if not label or label in roots:
            raise SystemExit(f"--root labels must be unique and non-empty, got {label!r}")
        roots[label] = Path(path.strip())
    return roots


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="P4-02F stage 0 (pure measurement) attribution tool"
    )
    parser.add_argument("--root", action="append", required=True, help="LABEL=PATH data root")
    parser.add_argument(
        "--modes",
        default="identity,stages,blocks,overhead,single-pass,numpy-flat-exact",
        help=(
            "comma list of identity,stages,blocks,overhead,single-pass,numpy-flat-exact,"
            "bounded-blocks"
        ),
    )
    parser.add_argument("--order", default=None, help="sampling order over the labels, e.g. A,B,B,A")
    parser.add_argument("--samples", type=int, default=20, help="service samples per round")
    parser.add_argument("--block-sizes", default=",".join(str(v) for v in DEFAULT_BLOCK_SIZES))
    parser.add_argument("--block-samples", type=int, default=3)
    parser.add_argument(
        "--bounded-candidates",
        default="10000,15000,20000,30000,50000",
        help="candidate block sizes of the bounded-blocks mode (P4-02M path)",
    )
    parser.add_argument(
        "--bounded-baseline",
        type=int,
        default=PRODUCT_BLOCK_SIZE,
        help="the product block size every candidate of bounded-blocks is compared against",
    )
    parser.add_argument("--bounded-rounds", type=int, default=2)
    parser.add_argument("--single-pass-samples", type=int, default=5)
    parser.add_argument("--flat-exact-samples", type=int, default=3)
    parser.add_argument("--overhead-reps", type=int, default=30)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument(
        "--rebuild-root",
        default=None,
        help="LABEL whose asset_id_idx gets the diagnostic replace contrast (runs last)",
    )
    parser.add_argument("--rebuild-samples", type=int, default=3)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args(argv)


def write_output(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    modes = [part.strip() for part in str(args.modes).split(",") if part.strip()]
    unknown = set(modes) - {
        "identity",
        "stages",
        "blocks",
        "overhead",
        "single-pass",
        "numpy-flat-exact",
        "bounded-blocks",
    }
    if unknown:
        raise SystemExit(f"unknown modes: {sorted(unknown)}")
    roots = parse_roots(args.root)
    order = (
        [part.strip() for part in str(args.order).split(",") if part.strip()]
        if args.order
        else list(roots)
    )
    if any(label not in roots for label in order):
        raise SystemExit(f"--order must name known labels, got {order}")
    block_sizes = [int(part) for part in str(args.block_sizes).split(",") if part.strip()]

    stats = Stats()
    report: dict[str, Any] = {
        "task": "P4-02F",
        "stage": "0 (pure measurement / attribution)",
        "check": "the six attribution facts of the F2 candidate-block retrieval path",
        "query_text": DEFAULT_QUERY_TEXT,
        "top_k": int(args.top_k),
        "encoder": "deterministic stand-in (no pinned model weights)",
        "modes": modes,
        "order": order,
        "samples_per_round": int(args.samples),
        "machine": bench.machine_facts(),
        "environment": {
            "concurrent_load": (
                "none: every measured root had finished building and no other measurement ran "
                "while this tool sampled"
            ),
            "os_cache": (
                "warm: the roots were built and then measured in the same session; the "
                "interleaved order is what separates a root effect from monotone drift"
            ),
            "process_io_and_cpu": (
                "disk bytes and CPU time come from this process's own counters "
                "(GetProcessIoCounters / GetProcessTimes), not from system-wide totals"
            ),
            "service_stage_instrumentation": (
                "the product's own module-level fts/fusion/authority/top-up functions are "
                "wrapped in this process only; their wall time overlaps the sqlite and engine "
                "buckets and is an attribution view, not a third resource"
            ),
        },
        "roots": {label: str(path) for label, path in roots.items()},
    }
    generations = {
        label: Generation(label, path, stats=stats, top_k=int(args.top_k))
        for label, path in roots.items()
    }
    try:
        report["service_stages_instrumented"] = instrument_service(stats)
        if "identity" in modes:
            report["identity"] = {
                label: generation.identity() for label, generation in generations.items()
            }
            probe_ids = [
                int(round(1 + index * (report["identity"][order[0]]["assets"] - 1) / (PROBE_IDS - 1)))
                for index in range(PROBE_IDS)
            ]
            probe = {
                label: generation.vector_probe(probe_ids)
                for label, generation in generations.items()
            }
            report["vector_probe_ids"] = probe_ids
            report["vector_probe"] = probe
            report["vector_probe_identical"] = len(
                {json.dumps(value, ensure_ascii=True) for value in probe.values()}
            ) == 1
        report["layout_before"] = {
            label: generation.layout() for label, generation in generations.items()
        }

        if "stages" in modes:
            rounds: list[dict[str, Any]] = []
            for index, label in enumerate(order, start=1):
                generation = generations[label]
                warmup = generation.once()
                samples = [generation.once() for _ in range(int(args.samples))]
                record = {
                    "round": index,
                    "label": label,
                    "warmup_service_ms": warmup["service_ms"],
                    "summary": round_summary(samples),
                    "samples": samples,
                }
                rounds.append(record)
                print(
                    f"round {index} {label}: median {record['summary']['service']['median_ms']} ms "
                    f"p95 {record['summary']['service']['p95_ms']} ms "
                    f"blocks {record['summary']['blocks_per_sample']}",
                    file=sys.stderr,
                    flush=True,
                )
            report["rounds"] = rounds
            by_label = {
                label: [record for record in rounds if record["label"] == label]
                for label in roots
            }
            every_page = {record["summary"]["page_sha256"] for record in rounds}
            report["distinct_pages_across_all_rounds"] = len(every_page)
            report["pages_identical_across_roots"] = len(every_page) == 1 and all(
                record["summary"]["distinct_pages_in_round"] == 1
                and record["summary"]["distinct_fused_hits_in_round"] == 1
                for record in rounds
            )
            report["page"] = rounds[0]["samples"][0]["page"]
            report["round_medians_ms"] = {
                label: round(
                    statistics.median(
                        [record["summary"]["service"]["median_ms"] for record in records]
                    ),
                    3,
                )
                for label, records in by_label.items()
            }
            report["pooled"] = {
                label: bench.summarize(
                    [
                        float(sample["service_ms"])
                        for record in records
                        for sample in record["samples"]
                    ]
                )
                for label, records in by_label.items()
            }
            if len(order) >= 2:
                first, second = order[0], order[1]
                report["first_to_second"] = {
                    "labels": [first, second],
                    "round_median_ratio": round(
                        report["round_medians_ms"][second]
                        / max(report["round_medians_ms"][first], 1e-9),
                        3,
                    ),
                    "pooled_p50_ratio": round(
                        report["pooled"][second]["median_ms"]
                        / max(report["pooled"][first]["median_ms"], 1e-9),
                        3,
                    ),
                    "pooled_p95_ratio": round(
                        report["pooled"][second]["p95_ms"]
                        / max(report["pooled"][first]["p95_ms"], 1e-9),
                        3,
                    ),
                }

        if "blocks" in modes:
            report["block_matrix"] = {
                label: generations[label].block_matrix(
                    block_sizes=block_sizes, samples=int(args.block_samples)
                )
                for label in order
            }
            for label, matrix in report["block_matrix"].items():
                print(
                    f"blocks {label}: "
                    + ", ".join(
                        f"{record['block_size']}->{record['ms_median']} ms/{record['blocks']} calls"
                        for record in matrix["results"]
                    ),
                    file=sys.stderr,
                    flush=True,
                )

        if "overhead" in modes:
            report["overhead"] = {}
            for label in order:
                generation = generations[label]
                ids = [
                    int(row["asset_id"])
                    for row in generation.catalog.read_all(
                        "SELECT asset_id FROM index_entries WHERE state = 'CONFIRMED' "
                        "ORDER BY asset_id LIMIT ?",
                        (PRODUCT_BLOCK_SIZE,),
                    )
                ]
                report["overhead"][label] = generation.overhead(
                    reps=int(args.overhead_reps), ids=ids
                )

        if "single-pass" in modes:
            report["single_pass"] = {
                label: generations[label].single_pass(samples=int(args.single_pass_samples))
                for label in order
            }

        if "bounded-blocks" in modes:
            report["bounded_blocks"] = {
                label: generations[label].bounded_matrix(
                    candidates=[
                        int(part)
                        for part in str(args.bounded_candidates).split(",")
                        if part.strip()
                    ],
                    baseline=int(args.bounded_baseline),
                    rounds=int(args.bounded_rounds),
                )
                for label in order
            }
            for label, matrix in report["bounded_blocks"].items():
                print(
                    f"bounded-blocks {label}: "
                    + ", ".join(
                        f"{record['block_size']}->{record['vector_ms_median']} ms vector / "
                        f"{record['service_ms_median']} ms service"
                        for record in matrix["results"]
                    ),
                    file=sys.stderr,
                    flush=True,
                )

        if "numpy-flat-exact" in modes:
            report["numpy_flat_exact"] = {
                label: generations[label].numpy_flat_exact(
                    samples=int(args.flat_exact_samples)
                )
                for label in order
            }

        if args.rebuild_root:
            label = str(args.rebuild_root)
            if label not in generations:
                raise SystemExit(f"--rebuild-root must name a known label, got {label}")
            generation = generations[label]
            before_cover = generation.layout()
            before = [generation.once() for _ in range(int(args.rebuild_samples))]
            rebuild = prior.replace_asset_index(
                generation.index, generation_id=bench.GENERATION_ID, dimension=bench.DIM
            )
            after_cover = generation.layout()
            after = [generation.once() for _ in range(int(args.rebuild_samples))]
            report["rebuild_contrast"] = {
                "label": label,
                "coverage_before": before_cover,
                "rebuild": rebuild,
                "coverage_after": after_cover,
                "before": round_summary(before),
                "after": round_summary(after),
                "median_ms_before": round_summary(before)["service"]["median_ms"],
                "median_ms_after": round_summary(after)["service"]["median_ms"],
                "page_identical": before[0]["page"] == after[0]["page"]
                and before[0]["fused_hits"] == after[0]["fused_hits"],
            }
            print(
                f"rebuild {label}: {report['rebuild_contrast']['median_ms_before']} ms -> "
                f"{report['rebuild_contrast']['median_ms_after']} ms "
                f"(page identical {report['rebuild_contrast']['page_identical']})",
                file=sys.stderr,
                flush=True,
            )
        report["layout_after"] = {
            label: generation.layout() for label, generation in generations.items()
        }
        report["status"] = "ok"
    finally:
        for generation in generations.values():
            generation.close()
    report["finished_at_ms"] = int(time.time() * 1000)
    write_output(args.output, report)
    print(json.dumps({key: report[key] for key in report if key not in {"rounds"}}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
