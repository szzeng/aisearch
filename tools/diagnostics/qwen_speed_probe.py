"""P4-QWEN-SPEED: exact 300k 2048D retrieval and real Qwen query timing.

The task's 512D and Chinese-CLIP numbers are cited from existing reports. This
tool measures only the new 2048D vector path and Qwen query encoding. Its
``--qwen-only`` mode is run by the disposable Python environment under ``tmp/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tmp/p402f-阶段0/scale-300000-wave/data"
SCRATCH = ROOT / "tmp/p4-qwen-speed/data-2048"
MODEL = ROOT / "tmp/qwen-speed-model"
QWEN_ENV = ROOT / "tmp/qwen-speed-env/Scripts/python.exe"
OUTPUT = ROOT / "test-results/p4-qwen-speed.json"
QUERY_OUTPUT = ROOT / "tmp/p4-qwen-speed/query-encoding.json"
GENERATION_ID = "qwen-speed-synthetic-2048-v1"
SPACE_ID = "qwen-speed-synthetic-2048"
PROCESSOR_ID = "qwen-speed-probe@v1"
TOP_K = 5000
BLOCK_SIZE = 15000
BATCH_SIZE = 5000
QUERIES = ("美女和猫", "海边日落", "两个人一起吃饭")


def table_name(generation_id: str) -> str:
    return "vectors_" + hashlib.sha256(generation_id.encode()).hexdigest()[:24]


def _product_imports() -> tuple[Any, Any, Any, Any, Any, Any]:
    sys.path.insert(0, str(ROOT / "src"))
    import lancedb
    import numpy as np
    import pyarrow as pa

    from photo_search.catalog.db import Catalog
    from photo_search.config import vectors_dir
    from photo_search.indexing.store import LanceVectorIndex
    from photo_search.search.retrieval import SearchScope, resolve_binding, vector_channel

    return lancedb, np, pa, Catalog, vectors_dir, (LanceVectorIndex, SearchScope, resolve_binding, vector_channel)


def clone_catalog(source: Path, destination: Path) -> None:
    if not destination.exists():
        (destination / "catalog").mkdir(parents=True)
        shutil.copy2(source / "catalog" / "catalog.db", destination / "catalog" / "catalog.db")
        # Preserve the fixture's existing 512D vector generation alongside the catalog;
        # artifact payload files are not read by search_candidates/vector_channel.
        shutil.copytree(source / "vectors", destination / "vectors", dirs_exist_ok=True)


def ensure_2048_generation(data_root: Path) -> dict[str, Any]:
    lancedb, np, pa, Catalog, vectors_dir, _ = _product_imports()
    catalog = Catalog.open(data_root=data_root)
    try:
        generation = catalog.read_one(
            "SELECT * FROM index_generations WHERE generation_id = ?", (GENERATION_ID,)
        )
        if generation is not None:
            count = int(
                catalog.read_one(
                    "SELECT COUNT(*) AS n FROM index_entries WHERE generation_id=? "
                    "AND space_id=? AND state='CONFIRMED'",
                    (GENERATION_ID, SPACE_ID),
                )["n"]
            )
            if count != 300000:
                raise RuntimeError(f"existing generation has {count} confirmed entries, expected 300000")
            ids = [int(row["asset_id"]) for row in catalog.read_all(
                "SELECT asset_id FROM index_entries WHERE generation_id=? AND space_id=? "
                "AND state='CONFIRMED' ORDER BY asset_id", (GENERATION_ID, SPACE_ID)
            )]
        else:
            ids = [
                int(row["asset_id"])
                for row in catalog.read_all(
                    "SELECT asset_id FROM index_entries WHERE state='CONFIRMED' "
                    "AND generation_id=(SELECT generation_id FROM index_generations "
                    "WHERE dimension=512 AND is_active=1) ORDER BY asset_id"
                )
            ]
            if len(ids) != 300000:
                raise RuntimeError(f"expected 300000 fixture entries, found {len(ids)}")
            now = time.time_ns() // 1_000_000
            connection = catalog.connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO index_generations "
                    "(generation_id,space_id,processor_id,dimension,state,is_active,created_at,updated_at) "
                    "VALUES (?,?,?,2048,'READY',0,?,?)",
                    (GENERATION_ID, SPACE_ID, PROCESSOR_ID, now, now),
                )
                connection.execute(
                    "INSERT INTO index_entries "
                    "(space_id,asset_id,generation_id,artifact_id,confirmation_revision,confirmed_at,state) "
                    "SELECT ?,asset_id,?,artifact_id,confirmation_revision,confirmed_at,'CONFIRMED' "
                    "FROM index_entries WHERE state='CONFIRMED' AND generation_id="
                    "(SELECT generation_id FROM index_generations WHERE dimension=512 AND is_active=1)",
                    (SPACE_ID, GENERATION_ID),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
    finally:
        catalog.close()

    vector_root = vectors_dir(data_root)
    vector_root.mkdir(parents=True, exist_ok=True)
    database = lancedb.connect(str(vector_root))
    name = table_name(GENERATION_ID)
    if name in set(database.list_tables().tables):
        table = database.open_table(name)
        rows = int(table.count_rows())
        if rows != 300000:
            raise RuntimeError(f"existing Lance generation has {rows} rows, expected 300000")
        return {
            "rows": rows,
            "dimension": 2048,
            "batch_size": BATCH_SIZE,
            "generation_build_seconds": None,
            "vector_table": str(vector_root / f"{name}.lance"),
            "reused_completed_generation": True,
        }

    rng = np.random.default_rng(2048)
    started = time.perf_counter()
    table = None
    for offset in range(0, len(ids), BATCH_SIZE):
        batch_ids = ids[offset : offset + BATCH_SIZE]
        # A broad, stratified first-coordinate distribution avoids concentrating all
        # nearest-neighbour cutoffs in a narrow floating-point tie band. Remaining
        # coordinates are random and the full vector is still unit-normalized.
        cosine = rng.uniform(-1.0, 1.0, len(batch_ids)).astype(np.float32)
        tail = rng.standard_normal((len(batch_ids), 2047), dtype=np.float32)
        tail /= np.linalg.norm(tail, axis=1, keepdims=True)
        tail *= np.sqrt(np.maximum(0.0, 1.0 - cosine * cosine))[:, None]
        vectors = np.empty((len(batch_ids), 2048), dtype=np.float32)
        vectors[:, 0] = cosine
        vectors[:, 1:] = tail
        arrow = pa.table(
            {
                "record_id": [f"{GENERATION_ID}:{asset_id}" for asset_id in batch_ids],
                "asset_id": pa.array(batch_ids, type=pa.int64()),
                "generation_id": [GENERATION_ID] * len(batch_ids),
                "space_id": [SPACE_ID] * len(batch_ids),
                "artifact_revision": ["synthetic-qwen-speed-v1"] * len(batch_ids),
                "projection_revision": pa.array([0] * len(batch_ids), type=pa.int64()),
                "vector": pa.FixedSizeListArray.from_arrays(
                    pa.array(vectors.reshape(-1), type=pa.float32()), 2048
                ),
                "source_ids": pa.array([[] for _ in batch_ids], type=pa.list_(pa.int64())),
                "visible": pa.array([True] * len(batch_ids), type=pa.bool_()),
            }
        )
        if table is None:
            table = database.create_table(name, data=arrow)
        else:
            table.add(arrow)
        del arrow, vectors
        print(f"generated {min(offset + BATCH_SIZE, len(ids))}/{len(ids)}", flush=True)
    return {
        "rows": len(ids),
        "dimension": 2048,
        "batch_size": BATCH_SIZE,
        "vector_distribution": "stratified first-coordinate cosine plus random unit tail",
        "generation_build_seconds": round(time.perf_counter() - started, 3),
        "vector_table": str(vector_root / f"{name}.lance"),
        "reused_completed_generation": False,
    }


def process_peak_ram() -> int:
    import ctypes
    import ctypes.wintypes as wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    query = ctypes.WinDLL("kernel32", use_last_error=True).K32GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    query.restype = wintypes.BOOL
    if not query(wintypes.HANDLE(ctypes.windll.kernel32.GetCurrentProcess()), ctypes.byref(counters), counters.cb):
        raise OSError(f"GetProcessMemoryInfo failed: {ctypes.get_last_error()}")
    return int(counters.PeakWorkingSetSize)


def directory_bytes(path: Path) -> int:
    return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())


def measure_vectors(data_root: Path) -> dict[str, Any]:
    _, np, _, Catalog, vectors_dir, index_types = _product_imports()
    LanceVectorIndex, SearchScope, resolve_binding, vector_channel = index_types
    catalog = Catalog.open(data_root=data_root)
    index = LanceVectorIndex(vectors_dir(data_root))
    fallback_count = 0
    original_fallback = index.search_candidates

    def count_fallback(**kwargs: Any) -> Any:
        nonlocal fallback_count
        fallback_count += 1
        return original_fallback(**kwargs)

    index.search_candidates = count_fallback  # type: ignore[method-assign]
    try:
        binding = resolve_binding(
            catalog, generation_id=GENERATION_ID, space_id=SPACE_ID, dimension=2048
        )
        query = np.random.default_rng(2152).standard_normal(2048, dtype=np.float32)
        query.fill(0.0)
        query[0] = 1.0
        samples: list[float] = []
        last: dict[str, Any] = {}
        for sample in range(6):
            started = time.perf_counter()
            last = vector_channel(
                catalog,
                index=index,
                binding=binding,
                query_vector=query.tolist(),
                scope=SearchScope.build(),
                top_k=TOP_K,
                block_size=BLOCK_SIZE,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            if sample:
                samples.append(elapsed_ms)
            print(f"2048D {'warmup' if sample == 0 else f'{sample}/5'}: {elapsed_ms:.2f} ms", flush=True)
        result = {
            "dimension": 2048,
            "warmup": 1,
            "samples_ms": [round(value, 2) for value in samples],
            "vector_p50_ms": round(statistics.median(samples), 2),
            "peak_ram_bytes": process_peak_ram(),
            "blocks": last["blocks"],
            "scanned": last["scanned_assets"],
            "fallback": fallback_count,
            "truncated": last["truncated"],
            "returned": len(last["hits"]),
            "vector_table_bytes": directory_bytes(vectors_dir(data_root) / f"{table_name(GENERATION_ID)}.lance"),
        }
        expected = {"blocks": 20, "scanned": 300000, "fallback": 0, "truncated": False, "returned": TOP_K}
        if any(result[key] != value for key, value in expected.items()):
            raise RuntimeError(f"2048D exact-search condition failed: {result}")
        return result
    finally:
        catalog.close()


def qwen_only(model_dir: Path) -> dict[str, Any]:
    import torch

    result: dict[str, Any] = {"status": "STARTED", "model_dir": str(model_dir)}
    try:
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA_UNAVAILABLE; CPU execution is prohibited by the task card")
        torch.cuda.set_device(0)
        device = torch.device("cuda:0")
        torch.cuda.reset_peak_memory_stats(device)
        model_root = str(model_dir.resolve())
        if model_root not in sys.path:
            sys.path.insert(0, model_root)
        from scripts.qwen3_vl_embedding import Qwen3VLEmbedder

        started = time.perf_counter()
        embedder = Qwen3VLEmbedder(
            model_name_or_path=model_root,
            dtype=torch.float16,
            attn_implementation="eager",
        )
        load_ms = (time.perf_counter() - started) * 1000
        actual_device = str(embedder.model.device)
        actual_dtype = str(next(embedder.model.parameters()).dtype)
        if not actual_device.startswith("cuda"):
            raise RuntimeError(f"model loaded off CUDA ({actual_device}); refusing CPU/offload results")

        for _ in range(3):
            for text in QUERIES:
                embedder.process([{"text": text}], normalize=True)
            torch.cuda.synchronize(device)

        samples: list[float] = []
        output_dims: set[int] = set()
        mrl_checked = True
        for _ in range(10):
            for text in QUERIES:
                torch.cuda.synchronize(device)
                started = time.perf_counter()
                embedding = embedder.process([{"text": text}], normalize=True)
                torch.cuda.synchronize(device)
                samples.append((time.perf_counter() - started) * 1000)
                output_dims.add(int(embedding.shape[-1]))
                # MRL comparison is a same-forward prefix truncation with re-normalization.
                prefix = torch.nn.functional.normalize(embedding[:, :512], p=2, dim=-1)
                if prefix.shape[-1] != 512 or embedding.shape[-1] != 2048:
                    mrl_checked = False
                del prefix, embedding
        torch.cuda.synchronize(device)
        result.update(
            {
                "status": "MEASURED",
                "model_load_ms": round(load_ms, 2),
                "warmup_per_query": 3,
                "samples_per_query": 10,
                "sample_count": len(samples),
                "query_p50_ms": round(statistics.median(samples), 2),
                "samples_ms": [round(value, 2) for value in samples],
                "query_texts": list(QUERIES),
                "output_dimensions": sorted(output_dims),
                "mrl_512d_same_forward": bool(mrl_checked),
                "device": actual_device,
                "device_name": torch.cuda.get_device_name(device),
                "dtype": actual_dtype,
                "quantization": False,
                "cpu_offload": False,
                "gpu_peak_allocated_bytes": int(torch.cuda.max_memory_allocated(device)),
                "gpu_peak_reserved_bytes": int(torch.cuda.max_memory_reserved(device)),
            }
        )
    except BaseException as error:  # record actual load/inference failure and stop this leg
        result.update(
            {
                "status": "CUDA_OOM" if isinstance(error, torch.cuda.OutOfMemoryError) else "MODEL_OR_DEPENDENCY_FAILURE",
                "error_type": type(error).__name__,
                "error": str(error),
            }
        )
        if isinstance(error, torch.cuda.OutOfMemoryError):
            torch.cuda.empty_cache()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qwen-only", action="store_true")
    parser.add_argument("--qwen-output", type=Path)
    parser.add_argument("--reuse-vector-result", action="store_true")
    parser.add_argument("--rebuild-synthetic-vectors", action="store_true")
    parser.add_argument("--model-dir", type=Path, default=MODEL)
    parser.add_argument("--scratch", type=Path, default=SCRATCH)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.qwen_only:
        payload = json.dumps(qwen_only(args.model_dir), ensure_ascii=True)
        if args.qwen_output:
            args.qwen_output.parent.mkdir(parents=True, exist_ok=True)
            args.qwen_output.write_text(payload + "\n", encoding="utf-8")
        print(payload)
        return 0

    if args.reuse_vector_result:
        previous = json.loads(args.output.read_text(encoding="utf-8"))
        build = previous["build_2048d"]
        vector = previous["vector_2048d"]
    else:
        clone_catalog(FIXTURE, args.scratch)
        if args.rebuild_synthetic_vectors:
            lancedb, _, _, _, vectors_dir, _ = _product_imports()
            vector_db = lancedb.connect(str(vectors_dir(args.scratch)))
            if table_name(GENERATION_ID) in set(vector_db.list_tables().tables):
                vector_db.drop_table(table_name(GENERATION_ID))
        build = ensure_2048_generation(args.scratch)
        vector = measure_vectors(args.scratch)
    QUERY_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    QUERY_OUTPUT.unlink(missing_ok=True)
    child = subprocess.run(
        [
            str(QWEN_ENV), str(Path(__file__).resolve()), "--qwen-only",
            "--model-dir", str(args.model_dir), "--qwen-output", str(QUERY_OUTPUT),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={
            **__import__("os").environ,
            "HF_HOME": str(ROOT / "tmp/qwen-speed-model-hf-cache"),
            "PYTHONIOENCODING": "utf-8",
        },
    )
    try:
        query = json.loads(QUERY_OUTPUT.read_text(encoding="utf-8"))
    except (IndexError, json.JSONDecodeError, OSError):
        query = {
            "status": "QWEN_ENV_PROCESS_FAILED",
            "returncode": child.returncode,
            "stdout_tail": child.stdout[-2000:],
            "stderr_tail": child.stderr[-4000:],
        }
    QUERY_OUTPUT.write_text(json.dumps(query, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    table_bytes = vector["vector_table_bytes"]
    baseline_vector_ms = 3420.894
    baseline_service_ms = 3733.173
    query_p50 = query.get("query_p50_ms") if query.get("status") == "MEASURED" else None
    estimate = None
    if query_p50 is not None:
        estimate = {
            "512d_total_ms": round(baseline_service_ms + query_p50, 2),
            "2048d_total_ms": round(
                baseline_service_ms - baseline_vector_ms + vector["vector_p50_ms"] + query_p50, 2
            ),
            "basis": "existing 512D service minus 512D vector, plus measured Qwen query and selected vector latency",
            "status": "estimated_not_http_e2e",
        }
    result = {
        "task": "P4-QWEN-SPEED",
        "fixture": str(FIXTURE),
        "scratch": str(args.scratch),
        "fixed": {"vectors": 300000, "dimension": 2048, "top_k": TOP_K, "block_size": BLOCK_SIZE, "exact": True},
        "build_2048d": build,
        "existing_baseline": {
            "chinese_clip_query": "see P4-02N real search report; not remeasured",
            "vector_p50_ms_512d": baseline_vector_ms,
            "service_p50_ms_512d": baseline_service_ms,
            "source": "docs/tasks/P4-QWEN-SPEED.md / P4-02Y-V",
        },
        "qwen_query": query,
        "vector_2048d": vector,
        "relative_vector_cost_2048d": round(vector["vector_p50_ms"] / baseline_vector_ms, 3),
        "estimated_search": estimate,
        "answer": {
            "qwen_512d": None if estimate is None else estimate["512d_total_ms"],
            "qwen_2048d": None if estimate is None else estimate["2048d_total_ms"],
            "gpu_8gb_runs": query.get("status") == "MEASURED",
        },
        "model_table_bytes": table_bytes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if child.stderr:
        sys.stderr.write(child.stderr[-4000:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
