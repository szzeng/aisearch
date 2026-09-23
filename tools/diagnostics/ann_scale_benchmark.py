"""P4-01 scale benchmark: exact vs ANN, index maintenance, filters and concurrency.

The benchmark owns a throw-away data root inside ``--work-dir`` (a project-local temporary
directory; it never reads or writes a real photo library) and drives the product's own
code rather than a copy of it:

* ``LanceVectorIndex`` (``src/photo_search/indexing/store.py``) writes and reads the
  derived vectors, including the production per-record ``upsert`` path;
* ``IndexCoordinator`` replays ``index_outbox`` and owns ``semantic_ready``;
* ``ArtifactStore.persist_result`` publishes the embedding artifacts exactly as the
  scheduler does (real files, real checksums, real outbox rows);
* ``search/retrieval.py`` (``vector_channel``/``authoritative_page``/``fts_channel``) and
  ``search/service.py`` (``search_assets``) are the production retrieval path;
* ``reciprocal_rank_fusion`` fuses the two channels.

Three honest limitations are recorded in the report instead of being hidden:

1. The 100 000/300 000-row fixture is *materialised* with a bulk Lance write on the
   production schema (``table.add``), because the production per-record path costs tens of
   milliseconds per row; that cost is measured separately (``maintenance.per_record_upsert``)
   and the bulk build time is reported next to it.
2. No pinned model weights are installed on this machine, so the fixture vectors are
   synthetic: a deterministic clustered family (default, a proxy for real embeddings) and a
   uniform-random family (worst case). Ground truth is an exact numpy brute force, which is
   cross-checked against the engine's own unfiltered scan.
3. The ANN index is *not* created by the product today (``LanceVectorIndex`` says so in its
   docstring). The tool creates an IVF_PQ index on the derived table itself and re-runs the
   unchanged production retrieval code against it, so the comparison measures the engine
   parameter, never a modified product path.

Modes::

    ann_scale_benchmark.py --mode scale  --scale 100000,300000 --work-dir tmp/p401
    ann_scale_benchmark.py --mode attribution --attribution-scale 20000 --work-dir tmp/p401
    ann_scale_benchmark.py --mode maintenance --maintenance-scale 1200 --work-dir tmp/p401
    ann_scale_benchmark.py --mode concurrency --concurrency-scale 50000 --work-dir tmp/p401
    ann_scale_benchmark.py --mode all --work-dir tmp/p401 --output test-results/p4-01.json

``--mode drain-worker`` is the internal background half of the concurrency scenario, and
``--mode attribution`` measures the single-variable ANN attribution on fresh tables (a
repeated ``create_index(replace=True)`` on one large table did not finish in bounded time,
which the report records as a pinned-engine anomaly for the C1 route).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np

from photo_search.artifacts.store import ArtifactStore, generation_id_for
from photo_search.catalog.db import Catalog
from photo_search.indexing.store import IndexCoordinator, LanceVectorIndex
from photo_search.jobs import queue as job_queue
from photo_search.scheduler.contracts import (
    CONTRACT_VERSION,
    ArtifactRef,
    JobResult,
    ProcessorSpec,
    canonical_json,
)
from photo_search.search import retrieval
from photo_search.search.service import search_assets
from photo_search.search.tokens import (
    FTS_GENERATION_ID,
    TOKENIZER_VERSION,
    build_match,
    index_text,
)
from photo_search.taken_time import contract as time_contract

#: The production embedding identity of the pinned Chinese-CLIP ViT-B/16 export
#: (``tools/prepare_models/manifests/chinese-clip-vit-b-16.json``): 512-D, cosine, L2.
SPACE_ID = "chinese-clip-vit-b-16:c8ac42d3907c57b0"
PROCESSOR_ID = "chinese-clip-vit-b-16@36e679e65c2a"
DIM = 512
GENERATION_ID = generation_id_for(SPACE_ID, PROCESSOR_ID)
#: LanceDB refuses to train PQ with fewer rows than this.
MIN_ANN_ROWS = 256

#: Filter tiers: ``(label, source_id, city_count, person_ids, person_match, capture_share)``.
#: ``source_id=None`` means "every source"; ``city_count=0`` means "no place condition".
TIERS: tuple[dict[str, Any], ...] = (
    {"label": "wide", "source_id": None, "cities": 0, "persons": (), "match": "any", "share": 0.9},
    {"label": "half", "source_id": 1, "cities": 5, "persons": (), "match": "any", "share": None},
    {"label": "narrow", "source_id": None, "cities": 1, "persons": (), "match": "any", "share": None},
    {"label": "thin", "source_id": None, "cities": 1, "persons": (1, 2), "match": "all", "share": None},
)


# --------------------------------------------------------------------------- utilities
def process_memory_bytes() -> int:
    """Peak working set of this process (Windows) or max RSS elsewhere."""
    if sys.platform == "win32":
        import ctypes
        import ctypes.wintypes as wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(ProcessMemoryCounters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        query = kernel32.K32GetProcessMemoryInfo
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD]
        query.restype = wintypes.BOOL
        handle = wintypes.HANDLE(kernel32.GetCurrentProcess())
        if not query(handle, ctypes.byref(counters), counters.cb):
            raise OSError(f"GetProcessMemoryInfo failed: {ctypes.get_last_error()}")
        return int(counters.PeakWorkingSetSize)
    import resource

    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def summarize(samples: Sequence[float]) -> dict[str, float]:
    values = np.asarray([float(value) for value in samples] or [0.0])
    return {
        "count": int(values.size),
        "median_ms": round(float(np.median(values)), 2),
        "p95_ms": round(float(np.percentile(values, 95)), 2),
        "min_ms": round(float(values.min()), 2),
        "max_ms": round(float(values.max()), 2),
        "mean_ms": round(float(values.mean()), 2),
    }


def directory_bytes(path: Path) -> int:
    if not Path(path).exists():
        return 0
    return sum(item.stat().st_size for item in Path(path).rglob("*") if item.is_file())


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def log(message: str) -> None:
    """Stage progress on stderr, so stdout stays the machine-readable JSON."""
    print(f"[p4-01] {message}", file=sys.stderr, flush=True)


def bounded_query(catalog: Catalog, fn: Any, *, seconds: float) -> tuple[Any, str | None]:
    """Run one SQLite call with a hard time budget.

    A pathological query plan (a correlated ``EXISTS`` chain that degenerates into a scan
    per asset) must be *reported* as a bottleneck, not silently make the benchmark
    unrunnable, so the call is interrupted by SQLite's own progress handler and the
    timeout reason is returned next to a ``None`` result.
    """
    deadline = time.perf_counter() + float(seconds)

    def handler() -> int:
        return 1 if time.perf_counter() > deadline else 0

    connection = catalog.connection
    connection.set_progress_handler(handler, 20_000)
    try:
        return fn(), None
    except sqlite3.OperationalError as error:
        return None, f"TIMEOUT>{seconds}s: {error}"
    finally:
        connection.set_progress_handler(None, 0)


def _chunks(values: Iterable[Any], size: int) -> Iterator[list[Any]]:
    batch: list[Any] = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def machine_facts() -> dict[str, Any]:
    facts: dict[str, Any] = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "processor": os.environ.get("PROCESSOR_IDENTIFIER") or platform.processor(),
        "logical_cpus": os.cpu_count(),
    }
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        facts["total_physical_bytes"] = int(status.ullTotalPhys)
        facts["available_physical_bytes"] = int(status.ullAvailPhys)
    except Exception:  # noqa: BLE001 - machine facts are best-effort
        pass
    try:
        import lancedb
        import pyarrow

        facts["lancedb"] = getattr(lancedb, "__version__", None)
        facts["pyarrow"] = getattr(pyarrow, "__version__", None)
    except Exception:  # noqa: BLE001 - machine facts are best-effort
        pass
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        facts["gpu"] = completed.stdout.strip() or None
    except Exception:  # noqa: BLE001
        facts["gpu"] = None
    return facts


# --------------------------------------------------------------------------- vectors
def synthetic_vectors(
    count: int, *, seed: int, distribution: str, clusters: int = 200, spread: float = 0.45
) -> np.ndarray:
    """Deterministic unit vectors: a clustered family (default) or uniform noise."""
    rng = np.random.default_rng(seed)
    if distribution == "uniform":
        values = rng.standard_normal((count, DIM), dtype=np.float32)
    else:
        centers = rng.standard_normal((clusters, DIM), dtype=np.float32)
        centers /= np.linalg.norm(centers, axis=1, keepdims=True)
        assignment = rng.integers(0, clusters, size=count)
        values = centers[assignment] + spread * rng.standard_normal(
            (count, DIM), dtype=np.float32
        )
    values /= np.linalg.norm(values, axis=1, keepdims=True)
    return values.astype(np.float32)


def boundary_vectors(matrix: np.ndarray, *, count: int, seed: int, noise: float = 0.05) -> np.ndarray:
    """Perturbed copies of stored vectors: their true nearest neighbour is that asset."""
    rng = np.random.default_rng(seed)
    picked = rng.choice(matrix.shape[0], size=min(count, matrix.shape[0]), replace=False)
    values = matrix[picked] + noise * rng.standard_normal((picked.size, DIM), dtype=np.float32)
    values /= np.linalg.norm(values, axis=1, keepdims=True)
    return values.astype(np.float32)


def arrow_batch(
    vectors: np.ndarray,
    *,
    first_asset: int,
    schema: Any,
    pa: Any,
    generation_id: str = GENERATION_ID,
) -> Any:
    count = int(vectors.shape[0])
    asset_ids = np.arange(first_asset, first_asset + count, dtype=np.int64)
    return pa.Table.from_arrays(
        [
            pa.array([f"{generation_id}:{value}" for value in asset_ids], type=pa.string()),
            pa.array(asset_ids, type=pa.int64()),
            pa.array([generation_id] * count, type=pa.string()),
            pa.array([SPACE_ID] * count, type=pa.string()),
            pa.array(["rev-1"] * count, type=pa.string()),
            pa.array(np.zeros(count, dtype=np.int64), type=pa.int64()),
            pa.FixedSizeListArray.from_arrays(
                pa.array(vectors.reshape(-1), type=pa.float32()), DIM
            ),
            pa.array([[1]] * count, type=pa.list_(pa.int64())),
            pa.array(np.ones(count, dtype=bool), type=pa.bool_()),
        ],
        schema=schema,
    )


# --------------------------------------------------------------------------- fixture
@dataclass
class ScaleFixture:
    scale: int
    work: Path
    data_root: Path
    catalog: Catalog
    matrix: np.ndarray
    source_ids: tuple[int, int]
    city_ids: tuple[int, ...]
    attraction_ids: tuple[int, ...]
    person_ids: tuple[int, ...]
    build: dict[str, Any] = field(default_factory=dict)

    @property
    def vectors_dir(self) -> Path:
        return self.data_root / "vectors"

    def scope_ids(self, scope: retrieval.SearchScope) -> list[int]:
        where, parameters = retrieval.scope_condition(self.catalog.connection, scope)
        rows = self.catalog.read_all(
            f"SELECT a.asset_id FROM assets AS a WHERE {where} ORDER BY a.asset_id",
            tuple(parameters),
        )
        return [int(row["asset_id"]) for row in rows]


def build_fixture(
    work: Path,
    *,
    scale: int,
    seed: int,
    distribution: str,
    places: bool = True,
    write_payloads: bool = True,
    reuse: bool = False,
) -> ScaleFixture:
    """Create a real catalog + a real Lance generation of ``scale`` assets."""
    work.mkdir(parents=True, exist_ok=True)
    data_root = work / "data"
    matrix_path = work / "vectors.npy"
    if reuse and matrix_path.exists() and (data_root / "catalog" / "catalog.db").exists():
        return _reuse_fixture(work, scale=scale, distribution=distribution, seed=seed)
    log(f"building the {scale}-asset fixture under {work}")
    if matrix_path.exists():
        matrix = np.load(matrix_path)
    else:
        matrix = synthetic_vectors(scale, seed=seed, distribution=distribution)
        np.save(matrix_path, matrix)

    catalog = Catalog.open(data_root=data_root, lease_seconds=600)
    started = time.perf_counter()
    phases: dict[str, float] = {}

    def phase(name: str, action: Any) -> Any:
        started_at = time.perf_counter()
        result = action()
        phases[name] = round((time.perf_counter() - started_at) * 1000, 1)
        log(f"  fixture phase {name}: {phases[name]} ms")
        return result

    source_ids = phase("sources", lambda: _insert_sources(catalog))
    phase("assets", lambda: _insert_assets(catalog, scale))
    phase("file_locations", lambda: _insert_locations(catalog, scale, source_ids))
    phase("search_documents", lambda: _insert_documents(catalog, scale))
    phase("capture_times", lambda: _insert_capture_times(catalog, scale))
    city_ids, attraction_ids = phase(
        "places", lambda: _insert_places(catalog) if places else ((), ())
    )
    if places:
        phase(
            "place_assignments",
            lambda: _insert_assignments(catalog, scale, city_ids, attraction_ids),
        )
    person_ids = phase("persons", lambda: _insert_persons(catalog, scale))
    artifact_info = phase(
        "index_rows",
        lambda: _insert_index_rows(
            catalog,
            scale,
            data_root=data_root,
            matrix=matrix,
            write_payloads=write_payloads,
        ),
    )
    sqlite_ms = (time.perf_counter() - started) * 1000

    import lancedb
    import pyarrow as pa

    log(f"  fixture: writing the Lance generation ({scale} rows)")
    vectors_root = data_root / "vectors"
    vectors_root.mkdir(parents=True, exist_ok=True)
    database = lancedb.connect(str(vectors_root))
    schema = LanceVectorIndex._schema(pa, DIM)
    from photo_search.indexing.store import _table_name

    table = database.create_table(_table_name(GENERATION_ID), schema=schema)
    started = time.perf_counter()
    step = 25_000
    for start in range(0, scale, step):
        stop = min(start + step, scale)
        table.add(arrow_batch(matrix[start:stop], first_asset=start + 1, schema=schema, pa=pa))
    lance_ms = (time.perf_counter() - started) * 1000
    del table
    del database

    fixture = ScaleFixture(
        scale=scale,
        work=work,
        data_root=data_root,
        catalog=catalog,
        matrix=matrix,
        source_ids=source_ids,
        city_ids=city_ids,
        attraction_ids=attraction_ids,
        person_ids=person_ids,
        build={
            "sqlite_ms": round(sqlite_ms, 1),
            "lance_bulk_add_ms": round(lance_ms, 1),
            "lance_ms_per_row": round(lance_ms / max(scale, 1), 4),
            "matrix_bytes": int(matrix.nbytes),
            "distribution": distribution,
            "seed": seed,
            "assets": scale,
            "artifacts": artifact_info,
            "phases_ms": phases,
            "rows": _row_counts(catalog),
            "sizes_bytes": _size_report(fixture_like_paths(data_root)),
        },
    )
    return fixture


def fixture_like_paths(data_root: Path) -> dict[str, Path]:
    return {
        "catalog_db": data_root / "catalog" / "catalog.db",
        "catalog_dir": data_root / "catalog",
        "vectors": data_root / "vectors",
        "artifacts": data_root / "artifacts",
    }


def _reuse_fixture(work: Path, *, scale: int, distribution: str, seed: int) -> ScaleFixture:
    """Re-open an already materialised fixture (resume after an interrupted run).

    Only the identifiers the fixture derives deterministically are read back, and the
    asset count is verified, so a resumed run measures the same data the first run built.
    """
    data_root = work / "data"
    matrix = np.load(work / "vectors.npy")
    catalog = Catalog.open(data_root=data_root, lease_seconds=600)
    sources = tuple(
        int(row["source_id"])
        for row in catalog.read_all("SELECT source_id FROM source_roots ORDER BY source_id")
    )
    cities = tuple(
        int(row["place_id"])
        for row in catalog.read_all(
            "SELECT place_id FROM places WHERE kind = 'city' ORDER BY place_id"
        )
    )
    attractions = tuple(
        int(row["place_id"])
        for row in catalog.read_all(
            "SELECT place_id FROM places WHERE kind = 'attraction' ORDER BY place_id"
        )
    )
    persons = tuple(
        int(row["person_id"])
        for row in catalog.read_all("SELECT person_id FROM persons ORDER BY person_id")
    )
    counts = _row_counts(catalog)
    if counts["assets"] != scale or matrix.shape[0] != scale or len(sources) != 2:
        raise RuntimeError(
            f"the fixture under {work} is not the expected {scale}-asset shape: {counts}"
        )
    log(f"reusing the materialised {scale}-asset fixture under {work}")
    return ScaleFixture(
        scale=scale,
        work=work,
        data_root=data_root,
        catalog=catalog,
        matrix=matrix,
        source_ids=(sources[0], sources[1]),
        city_ids=cities,
        attraction_ids=attractions,
        person_ids=persons,
        build={
            "reused": True,
            "distribution": distribution,
            "seed": seed,
            "assets": scale,
            "matrix_bytes": int(matrix.nbytes),
            "rows": counts,
            "sizes_bytes": _size_report(fixture_like_paths(data_root)),
        },
    )


def _size_report(paths: dict[str, Path]) -> dict[str, int]:
    report: dict[str, int] = {}
    for name, path in paths.items():
        report[name] = directory_bytes(path) if path.is_dir() else (
            path.stat().st_size if path.exists() else 0
        )
    return report


def _row_counts(catalog: Catalog) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in (
        "assets",
        "file_locations",
        "asset_metadata",
        "search_documents",
        "search_documents_fts",
        "asset_place_assignments",
        "faces",
        "face_person_links",
        "index_entries",
        "index_outbox",
        "index_generations",
    ):
        try:
            counts[table] = int(catalog.read_one(f"SELECT COUNT(*) FROM {table}")[0])
        except Exception:  # noqa: BLE001 - a missing table is reported as absent
            counts[table] = -1
    return counts


def _insert_sources(catalog: Catalog) -> tuple[int, int]:
    with catalog.write() as connection:
        connection.executemany(
            "INSERT INTO source_roots(display_name, root_path, volume_hint, status) "
            "VALUES (?, ?, NULL, 'ONLINE')",
            [("primary", "D:\\synthetic\\primary"), ("archive", "D:\\synthetic\\archive")],
        )
        rows = connection.execute(
            "SELECT source_id FROM source_roots ORDER BY source_id"
        ).fetchall()
    return (int(rows[0]["source_id"]), int(rows[1]["source_id"]))


def _insert_assets(catalog: Catalog, scale: int) -> None:
    with catalog.write() as connection:
        for batch in _chunks(range(1, scale + 1), 20_000):
            connection.executemany(
                "INSERT INTO assets(sha256, byte_size, width, height, format, created_at, state) "
                "VALUES (?, ?, 4032, 3024, 'JPEG', ?, 'ACTIVE')",
                [
                    (
                        f"{asset_id:064x}",
                        2_000_000 + asset_id % 977,
                        1_700_000_000_000 + asset_id,
                    )
                    for asset_id in batch
                ],
            )


def _insert_locations(catalog: Catalog, scale: int, source_ids: tuple[int, int]) -> None:
    with catalog.write() as connection:
        for batch in _chunks(range(1, scale + 1), 20_000):
            connection.executemany(
                "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, size, "
                "mtime_ns, last_seen_scan_id, availability) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, 'PRESENT')",
                [
                    (
                        source_ids[0] if asset_id % 10 < 7 else source_ids[1],
                        f"bucket-{asset_id // 1000:05d}/photo-{asset_id:08d}.jpg",
                        f"bucket-{asset_id // 1000:05d}/photo-{asset_id:08d}.jpg",
                        asset_id,
                        2_000_000 + asset_id % 977,
                        1_700_000_000_000_000_000 + asset_id * 1_000_000,
                    )
                    for asset_id in batch
                ],
            )


def _insert_documents(catalog: Catalog, scale: int) -> None:
    """The derived search documents; the 0006 triggers fill ``search_documents_fts``."""
    fingerprint = hashlib.sha256(f"p4-01-fixture:{FTS_GENERATION_ID}".encode()).hexdigest()
    now = now_ms()
    with catalog.write() as connection:
        for batch in _chunks(range(1, scale + 1), 20_000):
            connection.executemany(
                "INSERT INTO search_documents(asset_id, filename_text, user_text, ocr_text, "
                "caption_text, tag_text, place_text, source_fingerprint, tokenizer_version, "
                "fts_generation_id, updated_at) VALUES (?, ?, '', '', '', '', ?, ?, ?, ?, ?)",
                [
                    (
                        asset_id,
                        index_text(
                            "photo "
                            + f"{asset_id:08d} "
                            + ("beach" if asset_id % 3 == 0 else "mountain")
                        ),
                        index_text("XiangGang" if asset_id % 2 == 0 else "BeiJing"),
                        fingerprint,
                        int(TOKENIZER_VERSION),
                        FTS_GENERATION_ID,
                        now,
                    )
                    for asset_id in batch
                ],
            )


def capture_key(asset_id: int) -> int:
    """One deterministic civil key per asset: 2016-01-01 plus ``asset_id`` hours."""
    year = 2016 + (asset_id // (24 * 365)) % 9
    month = 1 + (asset_id // (24 * 30)) % 12
    day = 1 + asset_id % 28
    hour = (asset_id // 12) % 24
    minute = asset_id % 60
    return time_contract.civil_key(year, month, day, hour, minute)


def _insert_capture_times(catalog: Catalog, scale: int) -> None:
    with catalog.write() as connection:
        for batch in _chunks(range(1, scale + 1), 20_000):
            rows = []
            for asset_id in batch:
                if asset_id % 5 == 0:
                    # The honest "no capture time" tail (ADR-0011 §2).
                    rows.append((asset_id, None, None, "FILE_MTIME", "LOW", None, None))
                else:
                    rows.append(
                        (
                            asset_id,
                            "2016:01:01 00:00:00",
                            capture_key(asset_id),
                            "EXIF_ORIGINAL",
                            "HIGH",
                            480,
                            "TestMake TestModel",
                        )
                    )
            connection.executemany(
                "INSERT INTO asset_metadata(asset_id, taken_at_raw, taken_at_sort, time_source, "
                "time_confidence, timezone_offset, camera) VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )


def _insert_places(catalog: Catalog) -> tuple[tuple[int, ...], tuple[int, ...]]:
    now = now_ms()
    cities: list[int] = []
    attractions: list[int] = []
    place_rows = [(1, "country", "China", "china", None)]
    city_names = [
        "Beijing",
        "Shanghai",
        "Guangzhou",
        "Shenzhen",
        "Hangzhou",
        "Chengdu",
        "Wuhan",
        "Xian",
        "Nanjing",
        "Qingdao",
    ]
    for index, name in enumerate(city_names):
        place_id = 2 + index
        cities.append(place_id)
        place_rows.append((place_id, "city", name, name.lower(), 1))
    next_id = 2 + len(city_names)
    for city in cities:
        for suffix in range(5):
            place_id = next_id
            next_id += 1
            attractions.append(place_id)
            place_rows.append(
                (place_id, "attraction", f"Spot{city}-{suffix}", f"spot{city}-{suffix}", city)
            )
    with catalog.write() as connection:
        connection.executemany(
            "INSERT INTO places(place_id, kind, canonical_name, normalized_name, parent_place_id, "
            "origin, lifecycle, revision, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'USER', 'ACTIVE', 1, ?, ?)",
            [(*row, now, now) for row in place_rows],
        )
        connection.execute(
            "INSERT INTO place_operations(operation_id, request_id, payload_sha256, "
            "operation_type, operation_revision, actor, atomic, status, undo_of_operation_id, "
            "catalog_filter_revision, created_at, committed_at) "
            "VALUES ('op-fixture', 'req-fixture', ?, 'BATCH_ASSIGN', 1, 'LOCAL_USER', 1, "
            "'COMMITTED', NULL, 0, ?, ?)",
            (hashlib.sha256(b"fixture").hexdigest(), now, now),
        )
    return tuple(cities), tuple(attractions)


def _insert_assignments(
    catalog: Catalog, scale: int, cities: tuple[int, ...], attractions: tuple[int, ...]
) -> None:
    now = now_ms()
    with catalog.write() as connection:
        for batch in _chunks(range(1, scale + 1), 20_000):
            connection.executemany(
                "INSERT INTO asset_place_assignments(asset_id, assignment_id, place_id, mode, "
                "origin, manual_lock, resolution_id, revision, record_state, actor, operation_id, "
                "created_at, updated_at) VALUES (?, ?, ?, 'MANUAL_ASSIGNED', 'MANUAL', 1, NULL, 1, "
                "'ACTIVE', 'LOCAL_USER', 'op-fixture', ?, ?)",
                [
                    (
                        asset_id,
                        f"asg-{asset_id}",
                        attractions[(asset_id - 1) % len(attractions)],
                        now,
                        now,
                    )
                    for asset_id in batch
                ],
            )


def _insert_persons(catalog: Catalog, scale: int) -> tuple[int, ...]:
    now = now_ms()
    with catalog.write() as connection:
        connection.executemany(
            "INSERT INTO persons(person_id, name, name_key, avatar_face_id, merged_into, revision, "
            "created_at, updated_at) VALUES (?, ?, ?, NULL, NULL, 1, ?, ?)",
            [
                (1, "Person One", "person one", now, now),
                (2, "Person Two", "person two", now, now),
            ],
        )
        face_rows = []
        link_rows = []
        for asset_id in range(1, scale + 1):
            for ordinal, person_id in enumerate(_face_plan(asset_id)):
                # ``faces.face_id`` is a 64-hex-digit stable id (0010 CHECK constraint).
                face_id = hashlib.sha256(f"face-{asset_id}-{ordinal}".encode()).hexdigest()
                face_rows.append(
                    (
                        face_id,
                        asset_id,
                        "spec-v1",
                        "detector-v1",
                        ordinal,
                        hashlib.sha256(f"{asset_id}-{ordinal}".encode()).hexdigest(),
                        None,
                        json.dumps({"x": 10, "y": 10, "width": 80, "height": 80}),
                        json.dumps({"points": []}),
                        0.9,
                        6400.0,
                        12.0,
                        "OK",
                        "face-artifact",
                        1,
                        now,
                        now,
                    )
                )
                link_rows.append((face_id, person_id, "AUTO", "CONFIRMED", 0.8, None, 1, None, now, now))
        for batch in _chunks(face_rows, 20_000):
            connection.executemany(
                "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
                "input_revision, location_id, bbox_json, landmarks_json, det_score, face_pixels, "
                "blur_variance, quality_state, artifact_id, face_revision, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
        for batch in _chunks(link_rows, 20_000):
            connection.executemany(
                "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
                "confidence, reason, revision, last_operation_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
    return (1, 2)


def _face_plan(asset_id: int) -> tuple[int, ...]:
    """Deterministic person membership per asset (drives the ``all``/``any`` tiers).

    Person 1 covers ``asset_id % 3 == 0`` and person 2 ``asset_id % 4 == 0``, so both people
    share a non-empty 8.3 % of the library - which is what lets the ``all`` tier keep a
    ~1 % intersection with one city instead of collapsing to an empty set.
    """
    plan: list[int] = []
    if asset_id % 3 == 0:
        plan.append(1)
    if asset_id % 4 == 0:
        plan.append(2)
    return tuple(plan)


def _insert_index_rows(
    catalog: Catalog, scale: int, *, data_root: Path, matrix: np.ndarray, write_payloads: bool
) -> dict[str, Any]:
    """The post-replay state: artifact rows/files + CONFIRMED entries + one generation.

    ``index_entries.artifact_id`` has a real foreign key to ``artifacts``, so a faithful
    large fixture cannot invent ids. This writes the *protocol-complete* rows the
    production publish path converges to (one job, one artifact, one canonical payload
    file, one CONFIRMED entry per asset) without replaying one outbox event at a time -
    that per-event cost is measured separately and reported.
    """
    now = now_ms()
    store = ArtifactStore.open(data_root)
    payload_bytes = 0
    with catalog.write() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO index_generations(generation_id, space_id, processor_id, "
            "dimension, state, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, 'READY', 1, "
            "?, ?)",
            (GENERATION_ID, SPACE_ID, PROCESSOR_ID, DIM, now, now),
        )
        for batch in _chunks(range(1, scale + 1), 5_000):
            jobs_rows = []
            artifact_rows = []
            entry_rows = []
            prepared: list[tuple[Path, bytes]] = []
            for asset_id in batch:
                payload = canonical_json(
                    {"vector": [float(value) for value in matrix[asset_id - 1]]}
                ).encode("utf-8")
                digest = hashlib.sha256(payload).hexdigest()
                asset_hash = hashlib.sha256(str(asset_id).encode()).hexdigest()[:16]
                artifact_id = f"fixture-embedding-{asset_hash}"
                input_revision = hashlib.sha256(f"content-{asset_id}".encode()).hexdigest()
                ref = ArtifactRef(
                    artifact_id=artifact_id,
                    artifact_type="embedding",
                    artifact_revision="embedding.v1",
                    media_type="application/json",
                    checksum_sha256=digest,
                    byte_size=len(payload),
                    storage_uri=None,
                    payload=None,
                    space_id=SPACE_ID,
                    dimension=DIM,
                )
                target, storage_uri = store._target(ref)
                if write_payloads:
                    prepared.append((target, payload))
                payload_bytes += len(payload)
                jobs_rows.append(
                    (
                        "asset",
                        str(asset_id),
                        input_revision,
                        "image_embedding",
                        PROCESSOR_ID,
                        "SUCCEEDED",
                        0,
                        1,
                        3,
                        now,
                        now,
                        now,
                    )
                )
                artifact_rows.append(
                    (
                        artifact_id,
                        PROCESSOR_ID,
                        input_revision,
                        "embedding",
                        "embedding.v1",
                        "application/json",
                        digest,
                        len(payload),
                        storage_uri,
                        SPACE_ID,
                        DIM,
                        "PUBLISHED",
                        now,
                        now,
                    )
                )
                entry_rows.append(
                    (SPACE_ID, asset_id, GENERATION_ID, artifact_id, 0, now)
                )
            for target, payload in prepared:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            connection.executemany(
                "INSERT INTO jobs(subject_type, subject_id, input_revision, task_type, "
                "processor_id, state, priority, attempts, max_attempts, run_after, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                jobs_rows,
            )
            job_rows = connection.execute(
                "SELECT job_id, subject_id FROM jobs WHERE subject_id IN ("
                + ", ".join("?" for _ in batch)
                + ") AND task_type = 'image_embedding'",
                tuple(str(asset_id) for asset_id in batch),
            ).fetchall()
            job_by_asset = {int(row["subject_id"]): int(row["job_id"]) for row in job_rows}
            connection.executemany(
                "INSERT INTO artifacts(artifact_id, processor_id, input_revision, artifact_type, "
                "artifact_revision, media_type, checksum_sha256, byte_size, storage_uri, space_id, "
                "dimension, job_id, lifecycle_state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (*row[:11], job_by_asset[int(batch[index])], *row[11:])
                    for index, row in enumerate(artifact_rows)
                ],
            )
            connection.executemany(
                "INSERT INTO index_entries(space_id, asset_id, generation_id, artifact_id, "
                "confirmation_revision, confirmed_at, state) VALUES (?, ?, ?, ?, ?, ?, 'CONFIRMED')",
                entry_rows,
            )
    return {"artifact_payload_bytes": payload_bytes, "payloads_written": bool(write_payloads)}


# --------------------------------------------------------------------------- exact / ANN
def exact_top_k(
    matrix: np.ndarray, scope_ids: Sequence[int], queries: np.ndarray, k: int
) -> list[set[int]]:
    """Exact top-k inside one authoritative candidate set (numpy brute force)."""
    indexes = np.asarray([asset_id - 1 for asset_id in scope_ids], dtype=np.int64)
    subset = matrix[indexes]
    results: list[set[int]] = []
    for query in queries:
        scores = subset @ query.astype(np.float32)
        width = min(k, scores.size)
        top = np.argpartition(-scores, width - 1)[:width] if width < scores.size else np.arange(width)
        ordered = top[np.argsort(-scores[top], kind="stable")]
        results.append({int(indexes[position]) + 1 for position in ordered[:width]})
    return results


def recall(
    reference: Sequence[set[int]], produced: Sequence[set[int]], *, k: int
) -> dict[str, Any]:
    hits = 0
    total = 0
    for expected, actual in zip(reference, produced, strict=True):
        hits += len(expected & actual)
        total += min(k, len(expected))
    return {
        "recall": round(hits / total, 4) if total else None,
        "hits": hits,
        "possible": total,
        "queries": len(reference),
    }


def engine_search(
    index: LanceVectorIndex,
    *,
    vector: Sequence[float],
    limit: int,
    table: Any | None = None,
    generation_id: str = GENERATION_ID,
) -> list[int]:
    """One unfiltered engine query through the production adapter (no candidate walk).

    ``table`` may be an already-open handle from :func:`open_engine_table`; the recall loops
    reuse one handle so they measure the engine's query cost instead of paying the adapter's
    per-call reconnect (that cost is measured separately and is part of the walk numbers).
    """
    handle = table if table is not None else index._table(
        generation_id=generation_id, dimension=DIM, create=False
    )
    if handle is None:
        return []
    rows = handle.search(list(vector)).limit(int(limit)).to_arrow().to_pylist()
    return [int(row["asset_id"]) for row in rows]


def open_engine_table(index: LanceVectorIndex, *, generation_id: str = GENERATION_ID) -> Any | None:
    return index._table(generation_id=generation_id, dimension=DIM, create=False)


def drop_ann_index(fixture: ScaleFixture, *, generation_id: str = GENERATION_ID) -> list[str]:
    """Remove every ANN index from a generation, so an "unindexed" label stays true."""
    import lancedb

    from photo_search.indexing.store import _table_name

    database = lancedb.connect(str(fixture.vectors_dir))
    name = _table_name(generation_id)
    if name not in set(database.list_tables().tables):
        return []
    table = database.open_table(name)
    dropped: list[str] = []
    for existing in table.list_indices():
        index_name = str(getattr(existing, "name", existing))
        table.drop_index(index_name)
        dropped.append(index_name)
    del table
    del database
    return dropped


def production_walk(
    fixture: ScaleFixture,
    *,
    scope: retrieval.SearchScope,
    queries: np.ndarray,
    limit: int,
    index: LanceVectorIndex | None = None,
    warmup: bool = False,
) -> dict[str, Any]:
    """The unchanged production candidate walk (``vector_channel``) for each query.

    ``warmup`` runs one extra, discarded query first and reports it as ``cold_ms``: the
    engine's per-block scan of a large generation is cache-sensitive, and the architecture
    requires cold and warm numbers to be reported apart instead of mixed.
    """
    binding = retrieval.resolve_binding(
        fixture.catalog,
        generation_id=GENERATION_ID,
        space_id=SPACE_ID,
        dimension=DIM,
    )
    index = index or LanceVectorIndex(fixture.vectors_dir)

    def one(query: Any) -> tuple[float, list[int], int, int]:
        started = time.perf_counter()
        block = retrieval.vector_channel(
            fixture.catalog,
            index=index,
            binding=binding,
            query_vector=[float(value) for value in query],
            scope=scope,
            top_k=int(limit),
        )
        elapsed = (time.perf_counter() - started) * 1000
        return (
            elapsed,
            [int(row["asset_id"]) for row in block["hits"]],
            int(block["blocks"]),
            int(block["scanned_assets"]),
        )

    cold_ms = None
    if warmup and len(queries):
        cold_ms = round(one(queries[0])[0], 1)
    samples: list[float] = []
    ranked: list[list[int]] = []
    blocks: list[int] = []
    scanned: list[int] = []
    for query in queries:
        elapsed, ids, block_count, scanned_assets = one(query)
        samples.append(elapsed)
        ranked.append(ids)
        blocks.append(block_count)
        scanned.append(scanned_assets)
    return {
        "timings": summarize(samples),
        "cold_ms": cold_ms,
        "ranked": ranked,
        "blocks": sorted(set(blocks)),
        "scanned_assets": sorted(set(scanned)),
    }


def walk_recall(
    reference: Sequence[set[int]], ranked: Sequence[Sequence[int]], *, k: int
) -> dict[str, Any]:
    return recall(reference, [set(ids[:k]) for ids in ranked], k=k)


def default_partitions(scale: int) -> int:
    return max(1, min(1024, scale // 512))


def build_ann_index(
    fixture: ScaleFixture,
    *,
    partitions: int,
    sub_vectors: int,
    generation_id: str = GENERATION_ID,
) -> dict[str, Any] | None:
    """Create (or replace) the experimental IVF_PQ index on the derived table.

    ``None`` means "this fixture is too small for PQ training" (LanceDB needs at least 256
    rows), which only affects tiny smoke fixtures - the 100 000/300 000 scales always build.
    """
    if fixture.scale < MIN_ANN_ROWS:
        return None
    return _build_ann_index(
        fixture,
        partitions=partitions,
        sub_vectors=sub_vectors,
        generation_id=generation_id,
    )


def _build_ann_index(
    fixture: ScaleFixture,
    *,
    partitions: int,
    sub_vectors: int,
    generation_id: str = GENERATION_ID,
) -> dict[str, Any]:
    import lancedb

    from photo_search.indexing.store import _table_name

    database = lancedb.connect(str(fixture.vectors_dir))
    table = database.open_table(_table_name(generation_id))
    # Explicit drop + create instead of ``replace=True``: on this pinned build a third
    # consecutive replace-based rebuild of a 100 000-row table stopped making progress
    # (and the same first-build path is the one the product would use).
    dropped: list[str] = []
    for existing in table.list_indices():
        name = str(getattr(existing, "name", existing))
        table.drop_index(name)
        dropped.append(name)
    started = time.perf_counter()
    table.create_index(
        metric="cosine",
        num_partitions=int(partitions),
        num_sub_vectors=int(sub_vectors),
        index_type="IVF_PQ",
    )
    build_ms = (time.perf_counter() - started) * 1000
    ready_ms = None
    try:
        started_ready = time.perf_counter()
        table.wait_for_index(["vector_idx"], timeout=timedelta(minutes=30))
        ready_ms = round((time.perf_counter() - started_ready) * 1000, 1)
    except Exception as error:  # noqa: BLE001 - the wait is diagnostic, never fatal
        ready_ms = f"{type(error).__name__}: {error}"
    stats: Any
    try:
        stats = table.index_stats("vector_idx")
    except Exception as error:  # noqa: BLE001 - stats are diagnostic only
        stats = f"{type(error).__name__}: {error}"
    report = {
        "config": {
            "index_type": "IVF_PQ",
            "metric": "cosine",
            "num_partitions": int(partitions),
            "num_sub_vectors": int(sub_vectors),
            "indexed_rows": int(table.count_rows()),
        },
        "build_ms": round(build_ms, 1),
        "wait_for_index_ms": ready_ms,
        "dropped_indices": dropped,
        "indices": [str(item) for item in table.list_indices()],
        "index_stats": None if stats is None else str(stats),
        "size_bytes": directory_bytes(fixture.vectors_dir),
    }
    del table
    del database
    return report


def ann_hits(
    index: LanceVectorIndex, queries: np.ndarray, *, table: Any | None = None
) -> tuple[list[set[int]], list[set[int]]]:
    """Exactly two engine queries per probe vector: one top-20 and one top-50."""
    handle = table if table is not None else open_engine_table(index)
    hits_20 = [set(engine_search(index, vector=query, limit=20, table=handle)) for query in queries]
    hits_50 = [set(engine_search(index, vector=query, limit=50, table=handle)) for query in queries]
    return hits_20, hits_50


def family_recall(
    exact20: Sequence[set[int]],
    exact50: Sequence[set[int]],
    hits20: Sequence[set[int]],
    hits50: Sequence[set[int]],
    *,
    split: int,
) -> dict[str, Any]:
    """Recall split into the random family and the boundary family of the query set."""
    return {
        "random": {
            "recall_at_20": recall(exact20[:split], hits20[:split], k=20),
            "recall_at_50": recall(exact50[:split], hits50[:split], k=50),
        },
        "boundary": {
            "recall_at_20": recall(exact20[split:], hits20[split:], k=20),
            "recall_at_50": recall(exact50[split:], hits50[split:], k=50),
        },
    }


def write_generation_table(
    fixture: ScaleFixture, *, generation_id: str, batch: int = 25_000
) -> dict[str, Any]:
    """Bulk-write the fixture vectors into one *fresh* Lance table.

    The attribution sweep builds one table per configuration: a first ``create_index`` on a
    fresh table is the supported path (repeated ``replace=True`` rebuilds on a 100 000-row
    table did not finish in bounded time; see the report).
    """
    import lancedb
    import pyarrow as pa

    from photo_search.indexing.store import _table_name

    database = lancedb.connect(str(fixture.vectors_dir))
    name = _table_name(generation_id)
    schema = LanceVectorIndex._schema(pa, DIM)
    if name in set(database.list_tables().tables):
        database.drop_table(name)
    table = database.create_table(name, schema=schema)
    started = time.perf_counter()
    for start in range(0, fixture.scale, batch):
        stop = min(start + batch, fixture.scale)
        table.add(
            arrow_batch(
                fixture.matrix[start:stop],
                first_asset=start + 1,
                schema=schema,
                pa=pa,
                generation_id=generation_id,
            )
        )
    rows = int(table.count_rows())
    del table
    del database
    return {"rows": rows, "write_ms": round((time.perf_counter() - started) * 1000, 1)}


def attribution_report(work: Path, *, args: argparse.Namespace, seed: int) -> dict[str, Any]:
    """Single-variable ANN attribution (quantisation / partitions) across distributions."""
    scale = int(args.attribution_scale)
    report: dict[str, Any] = {
        "scale": scale,
        "queries": int(args.variant_queries),
        "note": (
            "each configuration gets its own fresh Lance table: repeated create_index("
            "replace=True) rebuilds on one large table did not finish in bounded time"
        ),
        "distributions": {},
    }
    for distribution in ("clustered", "uniform"):
        fixture = build_fixture(
            work / f"attribution-{distribution}",
            scale=scale,
            seed=seed,
            distribution=distribution,
        )
        try:
            ids = fixture.scope_ids(retrieval.SearchScope.build())
            queries = synthetic_vectors(
                int(args.variant_queries), seed=seed + 101, distribution=distribution
            )
            exact20 = exact_top_k(fixture.matrix, ids, queries, 20)
            exact50 = exact_top_k(fixture.matrix, ids, queries, 50)
            index = LanceVectorIndex(fixture.vectors_dir)
            base = default_partitions(scale)
            entries: list[dict[str, Any]] = []
            for label, partitions, sub_vectors in (
                ("default", base, 64),
                ("finer_quantisation", base, 128),
                ("coarser_quantisation", base, 32),
                ("fewer_partitions", max(1, base // 4), 64),
            ):
                generation = f"{GENERATION_ID}-{label}"
                written = write_generation_table(fixture, generation_id=generation)
                built = build_ann_index(
                    fixture,
                    partitions=partitions,
                    sub_vectors=sub_vectors,
                    generation_id=generation,
                )
                handle = open_engine_table(index, generation_id=generation)
                hits20 = [
                    set(
                        engine_search(
                            index,
                            vector=query,
                            limit=20,
                            table=handle,
                            generation_id=generation,
                        )
                    )
                    for query in queries
                ]
                hits50 = [
                    set(
                        engine_search(
                            index,
                            vector=query,
                            limit=50,
                            table=handle,
                            generation_id=generation,
                        )
                    )
                    for query in queries
                ]
                entries.append(
                    {
                        "label": label,
                        "generation_id": generation,
                        "write": written,
                        "index": built,
                        "recall_at_20": recall(exact20, hits20, k=20),
                        "recall_at_50": recall(exact50, hits50, k=50),
                        "timings": summarize(
                            _timed(
                                lambda query: engine_search(
                                    index,
                                    vector=query,
                                    limit=200,
                                    table=handle,
                                    generation_id=generation,
                                ),
                                queries[: min(20, queries.shape[0])],
                            )
                        ),
                    }
                )
                log(f"attribution {distribution}/{label}: recall@20 recorded")
            report["distributions"][distribution] = {"entries": entries}
        finally:
            fixture.catalog.close()
    return report


def explain_filter_plans(
    fixture: ScaleFixture, *, args: argparse.Namespace
) -> list[dict[str, Any]]:
    """``EXPLAIN QUERY PLAN`` plus a bounded timing for each filter tier.

    The P4-01 report found that two AND combinations take minutes while two comparable ones
    take ~150 ms. A decided repair needs the planner's own account of that, so the plans are
    recorded verbatim next to the timings.
    """
    rows: list[dict[str, Any]] = []
    for tier in TIERS:
        place_ids = fixture.city_ids[: int(tier["cities"])] if tier["cities"] else ()
        capture = (
            None
            if tier["share"] is None
            else _capture_range_for_share(tier["share"], scale=fixture.scale)
        )
        scope = retrieval.SearchScope.build(
            source_id=tier["source_id"],
            place_ids=place_ids,
            person_ids=tier["persons"],
            person_match=tier["match"],
            capture=capture,
        )
        where, parameters = retrieval.scope_condition(fixture.catalog.connection, scope)
        statements = {
            "count": f"SELECT COUNT(*) AS value FROM assets AS a WHERE {where}",
            "page": f"SELECT a.asset_id FROM assets AS a WHERE {where} ORDER BY a.asset_id LIMIT 5",
        }
        for name, sql in statements.items():
            plan = [
                " | ".join(str(value) for value in row)
                for row in fixture.catalog.read_all(f"EXPLAIN QUERY PLAN {sql}", tuple(parameters))
            ]
            started = time.perf_counter()
            timed, timeout = bounded_query(
                fixture.catalog, lambda: len(fixture.catalog.read_all(sql, tuple(parameters))), seconds=args.query_budget_seconds
            )
            rows.append(
                {
                    "tier": tier["label"],
                    "statement": name,
                    "conditions": {
                        "source_id": tier["source_id"],
                        "place_ids": list(place_ids),
                        "person_ids": list(tier["persons"]),
                        "person_match": tier["match"],
                        "capture_from_key": None if capture is None else capture.from_key,
                    },
                    "sql": sql,
                    "ms": round((time.perf_counter() - started) * 1000, 1),
                    "rows": timed,
                    "timeout": timeout,
                    "plan": plan,
                }
            )
            log(
                f"  explain {tier['label']}/{name}: "
                f"{rows[-1]['ms']} ms"
                + (f" {timeout}" if timeout else "")
            )
    return rows


def block_size_sweep(
    fixture: ScaleFixture, *, args: argparse.Namespace
) -> dict[str, Any]:
    """Is the production block size the lever? Same query, identical results, fewer blocks."""
    queries = synthetic_vectors(1, seed=args.seed + 71, distribution=args.distribution)
    scope = retrieval.SearchScope.build()
    index = LanceVectorIndex(fixture.vectors_dir)
    report: dict[str, Any] = {"sizes": [int(value) for value in args.sweep_block_sizes.split(",")]}

    def walk(block_size: int) -> dict[str, Any]:
        started = time.perf_counter()
        block = retrieval.vector_channel(
            fixture.catalog,
            index=index,
            binding=retrieval.resolve_binding(
                fixture.catalog, generation_id=GENERATION_ID, space_id=SPACE_ID, dimension=DIM
            ),
            query_vector=[float(value) for value in queries[0]],
            scope=scope,
            top_k=200,
            block_size=int(block_size),
        )
        return {
            "block_size": int(block_size),
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "blocks": int(block["blocks"]),
            "scanned_assets": int(block["scanned_assets"]),
            "top20": [int(row["asset_id"]) for row in block["hits"][:20]],
        }

    def phase(label: str) -> list[dict[str, Any]]:
        entries = [walk(size) for size in report["sizes"]]
        baseline = entries[0]["top20"]
        for entry in entries:
            entry["same_top20_as_first"] = entry["top20"] == baseline
            entry["phase"] = label
        log(
            f"  sweep {label}: "
            + ", ".join(f"B={item['block_size']} {item['ms']} ms/{item['blocks']} blocks" for item in entries)
        )
        return entries

    dropped = drop_ann_index(fixture)
    report["dropped_vector_index"] = dropped
    report["no_vector_index"] = phase("no_vector_index")

    scalar_state: dict[str, Any] = {}
    try:
        import lancedb

        from photo_search.indexing.store import _table_name

        database = lancedb.connect(str(fixture.vectors_dir))
        table = database.open_table(_table_name(GENERATION_ID))
        started = time.perf_counter()
        table.create_scalar_index("asset_id", index_type="BTREE", replace=True)
        scalar_state = {
            "created_ms": round((time.perf_counter() - started) * 1000, 1),
            "indices": [str(item.name) for item in table.list_indices()],
        }
        del table
        del database
    except Exception as error:  # noqa: BLE001 - a refused scalar index is a finding, not a crash
        scalar_state = {"error": f"{type(error).__name__}: {error}"}
    report["scalar_index"] = scalar_state
    if "error" not in scalar_state:
        report["with_scalar_index"] = phase("with_scalar_index")

    ann = build_ann_index(fixture, **{"partitions": default_partitions(fixture.scale), "sub_vectors": 64})
    report["ann_index"] = ann
    report["with_ann_index"] = phase("with_ann_index")
    return report


def write_batching_report(fixture: ScaleFixture, *, args: argparse.Namespace) -> dict[str, Any]:
    """One ``merge_insert`` per row vs one per batch, on the same derived table."""
    import lancedb
    import pyarrow as pa

    from photo_search.indexing.store import _table_name

    generation = f"{GENERATION_ID}-batching"
    written = write_generation_table(fixture, generation_id=generation)
    database = lancedb.connect(str(fixture.vectors_dir))
    table = database.open_table(_table_name(generation))
    schema = LanceVectorIndex._schema(pa, DIM)

    def record(asset_id: int) -> dict[str, Any]:
        return {
            "record_id": f"{generation}:{asset_id}",
            "asset_id": asset_id,
            "generation_id": generation,
            "space_id": SPACE_ID,
            "artifact_revision": "batch-probe",
            "projection_revision": 0,
            "vector": fixture.matrix[asset_id - 1].tolist(),
            "source_ids": [1],
            "visible": True,
        }

    single = 20
    started = time.perf_counter()
    for offset in range(single):
        table.merge_insert("record_id").when_matched_update_all().when_not_matched_insert_all().execute(
            [record(offset + 1)]
        )
    single_ms = (time.perf_counter() - started) * 1000

    batch = 500
    started = time.perf_counter()
    table.merge_insert("record_id").when_matched_update_all().when_not_matched_insert_all().execute(
        pa.Table.from_pylist([record(1000 + offset) for offset in range(batch)], schema=schema)
    )
    batch_ms = (time.perf_counter() - started) * 1000
    del table
    del database
    return {
        "scratch_rows": written["rows"],
        "single_row_writes": {
            "rows": single,
            "total_ms": round(single_ms, 1),
            "ms_per_row": round(single_ms / single, 2),
        },
        "batched_write": {
            "rows": batch,
            "total_ms": round(batch_ms, 1),
            "ms_per_row": round(batch_ms / batch, 3),
        },
        "speedup": round((single_ms / single) / max(batch_ms / batch, 1e-6), 1),
    }


def design_report(work: Path, *, args: argparse.Namespace, seed: int) -> dict[str, Any]:
    """P4-01B design probes on an already materialised fixture (read-mostly)."""
    scales = [int(value) for value in str(args.scale).split(",") if value.strip()]
    report: dict[str, Any] = {"scale": scales[0], "query_budget_seconds": args.query_budget_seconds}
    fixture = build_fixture(
        work / f"scale-{scales[0]}",
        scale=scales[0],
        seed=seed,
        distribution=args.distribution,
        reuse=True,
    )
    try:
        report["filter_plans"] = explain_filter_plans(fixture, args=args)
        report["block_sweep"] = block_size_sweep(fixture, args=args)
        report["write_batching"] = write_batching_report(fixture, args=args)
    finally:
        fixture.catalog.close()
    return report


def scale_report(
    work: Path,
    *,
    scale: int,
    args: argparse.Namespace,
    seed: int,
) -> dict[str, Any]:
    report: dict[str, Any] = {"scale": scale}
    started_scale = time.perf_counter()
    fixture = build_fixture(
        work / f"scale-{scale}",
        scale=scale,
        seed=seed,
        distribution=args.distribution,
        reuse=args.reuse_fixture,
        write_payloads=not args.no_payload_files,
    )
    try:
        report["fixture"] = fixture.build
        log(f"scale {scale}: fixture ready in {fixture.build.get('sqlite_ms', '?')} ms (sqlite)")
        # One walk query in this process *before* any index work records what a search costs
        # while the derived store is already on disk (a running app re-opens the table per
        # block query, so this is the realistic steady state; a reused fixture starts colder).
        opened_walk = production_walk(
            fixture,
            scope=retrieval.SearchScope.build(),
            queries=boundary_vectors(fixture.matrix, count=1, seed=seed + 13),
            limit=200,
        )
        log(
            f"scale {scale}: first walk after opening the store "
            f"{opened_walk['timings']['median_ms']} ms"
        )
        all_scope = retrieval.SearchScope.build()
        all_ids = fixture.scope_ids(all_scope)
        query_count = int(args.queries)
        queries = synthetic_vectors(
            query_count, seed=seed + 7, distribution=args.distribution
        )
        boundaries = boundary_vectors(fixture.matrix, count=args.boundary_queries, seed=seed + 11)
        probe_queries = np.concatenate([queries, boundaries], axis=0)

        started = time.perf_counter()
        exact_unfiltered = exact_top_k(fixture.matrix, all_ids, probe_queries, 20)
        exact_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        exact_50 = exact_top_k(fixture.matrix, all_ids, probe_queries, 50)
        exact_50_ms = (time.perf_counter() - started) * 1000
        log(f"scale {scale}: exact reference ready ({exact_ms:.0f} ms top20 / {exact_50_ms:.0f} ms top50)")

        index = LanceVectorIndex(fixture.vectors_dir)
        walk_queries = probe_queries[: max(1, int(args.walk_queries))]
        dropped_before = drop_ann_index(fixture)
        log(
            f"scale {scale}: measured with no ANN index (dropped {dropped_before or 'none'})"
        )
        # The engine's own unfiltered scan, compared against brute force *without* an index:
        # this is the "the exact path and the engine agree" evidence the ANN numbers rest on.
        cross_check_queries = probe_queries[: min(8, probe_queries.shape[0])]
        cross_rows: list[dict[str, Any]] = []
        for position, query in enumerate(cross_check_queries):
            engine_ids = engine_search(index, vector=query, limit=20)
            expected = {
                int(index_value) + 1
                for index_value in np.argsort(-(fixture.matrix @ query.astype(np.float32)))[:20]
            }
            overlap = len(set(engine_ids) & expected)
            cross_rows.append(
                {
                    "query": position,
                    "engine_ids": engine_ids,
                    "exact_ids": sorted(expected),
                    "overlap": overlap,
                    "equal": overlap == len(expected),
                }
            )
        log(
            f"scale {scale}: unindexed engine scan vs brute force "
            f"{sum(row['overlap'] for row in cross_rows)}/"
            f"{20 * len(cross_rows)} ids agree"
        )
        log(f"scale {scale}: measuring the production candidate walk (unindexed)")
        unindexed = production_walk(
            fixture,
            scope=all_scope,
            queries=walk_queries,
            limit=200,
            index=index,
            warmup=True,
        )
        unindexed_reference = exact_top_k(fixture.matrix, all_ids, walk_queries, 20)
        unindexed_reference_50 = exact_top_k(fixture.matrix, all_ids, walk_queries, 50)
        global_unindexed = [
            engine_search(index, vector=query, limit=200) for query in probe_queries[:8]
        ]
        global_unindexed_timings = _timed(
            lambda query: engine_search(index, vector=query, limit=200),
            probe_queries[: min(20, probe_queries.shape[0])],
        )
        log(
            f"scale {scale}: unindexed walk {unindexed['timings']['median_ms']} ms for "
            f"{unindexed['blocks']} blocks"
        )

        default_config = {
            "partitions": default_partitions(scale),
            "sub_vectors": 64,
        }
        ann_index = build_ann_index(fixture, **default_config)
        log(f"scale {scale}: ANN index built {ann_index and ann_index['build_ms']} ms")
        if ann_index is None:
            ann_paths: dict[str, Any] = {
                "production_walk_ann": {"skipped": f"fixture below {MIN_ANN_ROWS} rows"},
                "engine_global_ann": {"skipped": f"fixture below {MIN_ANN_ROWS} rows"},
            }
        else:
            walk_ann = production_walk(
                fixture,
                scope=all_scope,
                queries=walk_queries,
                limit=200,
                index=index,
                warmup=True,
            )
            log(
                f"scale {scale}: ANN walk {walk_ann['timings']['median_ms']} ms "
                f"(cold {walk_ann['cold_ms']} ms)"
            )
            handle = open_engine_table(index)
            ann_hits_20, ann_hits_50 = ann_hits(index, probe_queries, table=handle)
            log(f"scale {scale}: {probe_queries.shape[0]} ANN probe queries done")
            engine_ann = {
                "recall_at_20": recall(exact_unfiltered, ann_hits_20, k=20),
                "recall_at_50": recall(exact_50, ann_hits_50, k=50),
            }
            engine_ann_timings = summarize(
                _timed(
                    lambda query: engine_search(index, vector=query, limit=200, table=handle),
                    probe_queries[: min(20, probe_queries.shape[0])],
                )
            )
            families = family_recall(
                exact_unfiltered,
                exact_50,
                ann_hits_20,
                ann_hits_50,
                split=int(query_count),
            )
            # The production adapter opens a fresh LanceDB connection per call; measure that
            # overhead once so a reader can separate it from the engine query cost above.
            open_timings = _timed(lambda _: open_engine_table(index), probe_queries[:5])
            log(f"scale {scale}: adapter open cost {summarize(open_timings)['median_ms']} ms")
            ann_paths = {
                "production_walk_ann": {
                    "timings": walk_ann["timings"],
                    "cold_ms": walk_ann["cold_ms"],
                    "blocks": walk_ann["blocks"],
                    "scanned_assets": walk_ann["scanned_assets"],
                    "recall_at_20": walk_recall(unindexed_reference, walk_ann["ranked"], k=20),
                    "recall_at_50": walk_recall(unindexed_reference_50, walk_ann["ranked"], k=50),
                },
                "engine_global_ann": {
                    **engine_ann,
                    "families": families,
                    "timings": engine_ann_timings,
                    "adapter_open_timings": summarize(open_timings),
                },
            }

        report["retrieval"] = {
            "walk_after_open": {
                "timings": opened_walk["timings"],
                "blocks": opened_walk["blocks"],
                "scanned_assets": opened_walk["scanned_assets"],
                "note": (
                    "one walk query per process, measured before any index work, while the "
                    "derived store already exists on disk"
                ),
            },
            "index_state_before_measurement": {
                "dropped": dropped_before,
                "note": "the unindexed walk was measured with no ANN index present",
            },
            "exact_reference": {
                "method": "numpy brute force over the authoritative candidate set",
                "ms_top20": round(exact_ms, 1),
                "ms_top50": round(exact_50_ms, 1),
                "candidates": len(all_ids),
            },
            "query_tuning_notes": (
                "lancedb 0.38's LanceQueryBuilder exposes no nprobes/refine_factor knob, so the "
                "attribution below is index-level only (num_partitions / num_sub_vectors)."
            ),
            "engine_scan_cross_check": {
                "queries": len(cross_rows),
                "method": "unindexed engine scan vs numpy brute force",
                "all_equal": all(row["equal"] for row in cross_rows),
                "total_overlap": sum(row["overlap"] for row in cross_rows),
                "rows": cross_rows,
            },
            "paths": {
                "production_walk_unindexed": {
                    "timings": unindexed["timings"],
                    "cold_ms": unindexed["cold_ms"],
                    "blocks": unindexed["blocks"],
                    "scanned_assets": unindexed["scanned_assets"],
                    "recall_at_20": walk_recall(unindexed_reference, unindexed["ranked"], k=20),
                    "recall_at_50": walk_recall(
                        unindexed_reference_50, unindexed["ranked"], k=50
                    ),
                },
                **ann_paths,
                "engine_global_unindexed": {
                    "queries": len(global_unindexed),
                    "first_ids": global_unindexed[0][:20] if global_unindexed else [],
                    "timings": summarize(global_unindexed_timings),
                },
            },
            "ann_index": ann_index,
            "ann_variants": (
                "measured by --mode attribution on fresh tables: repeated replace=True "
                "rebuilds on one 100 000-row table did not complete in bounded time"
            ),
            "queries": {
                "random": int(query_count),
                "boundary": int(boundaries.shape[0]),
                "walk_queries": int(walk_queries.shape[0]),
            },
        }
        report["filters"] = filter_report(fixture, args=args, index=index, seed=seed)
        log(f"scale {scale}: filter tiers and the AND-combination check ready")
        report["maintenance"] = maintenance_report(fixture, args=args)
        log(
            f"scale {scale}: maintenance ready "
            f"({report['maintenance']['replay']['replay_ms_per_event']} ms/event)"
        )
        report["elapsed_ms"] = round((time.perf_counter() - started_scale) * 1000, 1)
    finally:
        fixture.catalog.close()
    return report


def _timed(fn: Any, queries: Sequence[Any]) -> list[float]:
    samples: list[float] = []
    for query in queries:
        started = time.perf_counter()
        fn(query)
        samples.append((time.perf_counter() - started) * 1000)
    return samples


def filter_report(
    fixture: ScaleFixture, *, args: argparse.Namespace, index: LanceVectorIndex, seed: int
) -> dict[str, Any]:
    queries = synthetic_vectors(4, seed=seed + 23, distribution=args.distribution)
    tiers: list[dict[str, Any]] = []
    for tier in TIERS:
        place_ids = fixture.city_ids[: int(tier["cities"])] if tier["cities"] else ()
        capture = None
        if tier["share"] is not None:
            capture = _capture_range_for_share(tier["share"], scale=fixture.scale)
        scope = retrieval.SearchScope.build(
            source_id=tier["source_id"],
            place_ids=place_ids,
            person_ids=tier["persons"],
            person_match=tier["match"],
            capture=capture,
        )
        started_tier = time.perf_counter()
        ids, ids_timeout = bounded_query(
            fixture.catalog,
            lambda: fixture.scope_ids(scope),
            seconds=args.query_budget_seconds,
        )
        authority, authority_timeout = bounded_query(
            fixture.catalog,
            lambda: retrieval.authoritative_page(fixture.catalog, scope),
            seconds=args.query_budget_seconds,
        )
        scope_ms = round((time.perf_counter() - started_tier) * 1000, 1)
        log(
            f"  tier {tier['label']}: {len(ids) if ids else ids_timeout} candidates, "
            f"authority {authority['total'] if authority else authority_timeout}, {scope_ms} ms"
        )
        if ids is None or authority is None:
            tiers.append(
                {
                    "label": tier["label"],
                    "conditions": {
                        "source_id": tier["source_id"],
                        "place_ids": list(place_ids),
                        "person_ids": list(tier["persons"]),
                        "person_match": tier["match"],
                    },
                    "candidates": None,
                    "scope_ms": scope_ms,
                    "authority_sql_timeout": authority_timeout,
                    "scope_ids_timeout": ids_timeout,
                    "walk": {"skipped": "the authoritative SQL did not return in budget"},
                }
            )
            continue
        walk_queries = queries[: max(1, int(args.filter_walk_queries))]
        walk = production_walk(fixture, scope=scope, queries=walk_queries, limit=200, index=index)
        exact = exact_top_k(fixture.matrix, ids, walk_queries, 20)
        log(
            f"  tier {tier['label']}: walk {walk['timings']['median_ms']} ms "
            f"for {walk['blocks']} block(s)"
        )
        outside = sorted(
            {
                asset_id
                for ranked in walk["ranked"]
                for asset_id in ranked
                if asset_id not in set(ids)
            }
        )
        tiers.append(
            {
                "label": tier["label"],
                "conditions": {
                    "source_id": tier["source_id"],
                    "place_ids": list(place_ids),
                    "person_ids": list(tier["persons"]),
                    "person_match": tier["match"],
                    "capture": None
                    if capture is None
                    else {"from_key": capture.from_key, "to_key": capture.to_key},
                },
                "candidates": len(ids),
                "selectivity": round(len(ids) / max(fixture.scale, 1), 4),
                "authority": {
                    "total": int(authority["total"]),
                    "returned": int(authority["count"]),
                    "truncated": bool(authority["truncated"]),
                },
                "walk": {
                    "timings": walk["timings"],
                    "blocks": walk["blocks"],
                    "scanned_assets": walk["scanned_assets"],
                    "recall_at_20": walk_recall(exact, walk["ranked"], k=20),
                    "hits_outside_authority": outside,
                    "expected_blocks": math.ceil(len(ids) / 1000) if ids else 0,
                },
            }
        )
        tiers[-1]["scope_ms"] = scope_ms
    combination = _combination_check(fixture, args=args, index=index, seed=seed + 5)
    log("  and-combination check done")
    return {"tiers": tiers, "and_combination": combination}


def _capture_range_for_share(share: float, *, scale: int) -> time_contract.CaptureRange:
    """A half-open capture range that keeps roughly ``share`` of the fixture's civil keys.

    Only the ``EXIF_ORIGINAL`` rows (``asset_id % 5 != 0``) can satisfy a capture range, so
    the reported selectivity of that tier is the share of *those* rows the range keeps.
    """
    keys = [capture_key(asset_id) for asset_id in range(1, int(scale) + 1)]
    keys.sort()
    cut = keys[int(len(keys) * (1.0 - share))]
    return time_contract.CaptureRange(
        from_key=cut, to_key=None, from_text=None, to_text=None
    )


def _combination_check(
    fixture: ScaleFixture, *, args: argparse.Namespace, index: LanceVectorIndex, seed: int
) -> dict[str, Any]:
    """§12.4 item 32: place + person + capture + semantic AND, and projection top-up."""
    city = fixture.city_ids[0]
    person = fixture.person_ids[0]
    capture = _capture_range_for_share(0.8, scale=fixture.scale)
    scope = retrieval.SearchScope.build(
        place_ids=(city,), person_ids=(person,), person_match="any", capture=capture
    )
    budget = float(args.query_budget_seconds)
    ids, ids_timeout = bounded_query(
        fixture.catalog, lambda: fixture.scope_ids(scope), seconds=budget
    )
    authority, authority_timeout = bounded_query(
        fixture.catalog,
        lambda: retrieval.authoritative_page(fixture.catalog, scope),
        seconds=budget,
    )
    if ids is None or authority is None:
        return {
            "scope_ids_timeout": ids_timeout,
            "authority_sql_timeout": authority_timeout,
            "note": "the AND-combination scope did not return a candidate set in budget",
        }
    engine_ids = set(ids)
    query = synthetic_vectors(1, seed=seed, distribution=args.distribution)
    walk = production_walk(fixture, scope=scope, queries=query, limit=200, index=index)

    # Independent SQL cross-check: the same conditions evaluated by hand. The production
    # predicate expands ``place_ids`` to descendants, so the hand-written statement names
    # the child attractions explicitly instead of reusing ``place_condition``.
    descendants = ", ".join(str(value) for value in fixture.attraction_ids[:5])
    hand, hand_timeout = bounded_query(
        fixture.catalog,
        lambda: fixture.catalog.read_one(
            "SELECT COUNT(*) AS value FROM assets AS a WHERE a.asset_id IN ("
            "SELECT ap.asset_id FROM asset_place_assignments AS ap "
            "WHERE ap.mode = 'MANUAL_ASSIGNED' "
            f"AND ap.place_id IN ({descendants})) AND a.asset_id IN "
            "(SELECT f.asset_id FROM faces AS f WHERE f.face_id IN "
            "(SELECT l.face_id FROM face_person_links AS l "
            "WHERE l.person_id = ? AND l.decision = 'CONFIRMED')) "
            "AND a.asset_id IN (SELECT m.asset_id FROM asset_metadata AS m "
            "WHERE m.time_source IN ('EXIF_ORIGINAL', 'EXIF_DIGITIZED') "
            "AND m.taken_at_sort >= ?)",
            (person, capture.from_key),
        ),
        seconds=budget,
    )
    hand_count = None if hand is None else int(hand["value"])

    fts, fts_timeout = bounded_query(
        fixture.catalog,
        lambda: retrieval.fts_channel(fixture.catalog, build_match("beach"), scope, top_k=50),
        seconds=budget,
    )
    fts_ids = [] if fts is None else [int(hit["asset_id"]) for hit in fts]

    topup, topup_timeout = bounded_query(
        fixture.catalog,
        lambda: retrieval.pending_projection_page(
            fixture.catalog,
            binding=retrieval.resolve_binding(
                fixture.catalog, generation_id=GENERATION_ID, space_id=SPACE_ID, dimension=DIM
            ),
            scope=scope,
        ),
        seconds=budget,
    )
    topup = topup or {"count": 0, "reason": "TIMEOUT"}
    walk_ids = set(walk["ranked"][0]) if walk["ranked"] else set()
    missing = [asset_id for asset_id in walk_ids if asset_id not in engine_ids]

    # The projection-lag direction of §12.4 item 32: an asset whose semantic row is not
    # confirmed yet must be named by the top-up page, and must not be claimed as a hit.
    lagged = sorted(ids)[0] if ids else None
    if lagged is not None:
        with fixture.catalog.write() as connection:
            connection.execute(
                "UPDATE index_entries SET state = 'PENDING_REPAIR', confirmed_at = NULL "
                "WHERE asset_id = ? AND generation_id = ?",
                (lagged, GENERATION_ID),
            )
        lag_topup = retrieval.pending_projection_page(
            fixture.catalog,
            binding=retrieval.resolve_binding(
                fixture.catalog, generation_id=GENERATION_ID, space_id=SPACE_ID, dimension=DIM
            ),
            scope=scope,
        )
        lag_walk = production_walk(fixture, scope=scope, queries=query, limit=200, index=index)
        lagged_in_topup = any(
            int(entry["asset_id"]) == lagged for entry in lag_topup["entries"]
        )
        lagged_in_hits = lagged in (set(lag_walk["ranked"][0]) if lag_walk["ranked"] else set())
        with fixture.catalog.write() as connection:
            connection.execute(
                "UPDATE index_entries SET state = 'CONFIRMED', confirmed_at = ? "
                "WHERE asset_id = ? AND generation_id = ?",
                (now_ms(), lagged, GENERATION_ID),
            )
    else:
        lagged_in_topup = lagged_in_hits = False
    return {
        "conditions": {
            "place_id": int(city),
            "person_id": int(person),
            "capture_from_key": capture.from_key,
            "place_descendants": [int(value) for value in fixture.attraction_ids[:5]],
        },
        "scoped_candidates": len(ids),
        "hand_computed_count": hand_count,
        "authority_total": int(authority["total"]),
        "sql_agrees": hand_count == len(ids) == int(authority["total"]),
        "hand_sql_timeout": hand_timeout,
        "fts_timeout": fts_timeout,
        "topup_timeout": topup_timeout,
        "walk_hits": len(walk_ids),
        "walk_hits_outside_authority": missing,
        "fts_hits": len(fts_ids),
        "fts_hits_outside_authority": [
            asset_id for asset_id in fts_ids if asset_id not in engine_ids
        ],
        "pending_projection_entries": int(topup["count"]),
        "pending_projection_reason": str(topup["reason"]),
        "projection_lag": {
            "asset_id": lagged,
            "in_topup": bool(lagged_in_topup),
            "returned_as_hit": bool(lagged_in_hits),
        },
    }


# --------------------------------------------------------------------------- maintenance
def embedding_spec() -> ProcessorSpec:
    digest = hashlib.sha256(b"p4-01-fixture-processor").hexdigest()
    return ProcessorSpec.from_envelope(
        {
            "contract_version": CONTRACT_VERSION,
            "spec_revision": "p4-01-fixture-v1",
            "processor_id": PROCESSOR_ID,
            "processor_kind": "embedding_model",
            "task_type": "image_embedding",
            "config_hash": digest,
            "input_type": "asset",
            "output_type": "embedding",
            "device_policy": "CPU_ALLOWED",
            "runtime_backend": "onnxruntime",
            "runtime_package_id": "onnxruntime-cpu@p4-01",
            "supported_devices": ["cpu"],
            "supported_precisions": ["FP32"],
            "max_batch_size": 1,
            "cancellation_supported": True,
            "resource_measurements": [],
            "model_id": "fixture/chinese-clip",
            "model_revision": "fixture-r1",
            "weights": [{"name": "fixture.onnx", "revision": "fixture-r1", "sha256": digest}],
            "image_preprocess_version": "image-v1",
            "query_preprocess_version": "query-v1",
            "tokenizer_version": "tokenizer-v1",
            "instruction_template_version": "none-v1",
            "normalization_version": "L2-v1",
            "dimension": DIM,
            "metric": "cosine",
            "space_id": SPACE_ID,
            "max_pixels": 4096,
            "max_tokens": 52,
        }
    )


def publish_embedding(
    fixture: ScaleFixture,
    *,
    artifact_id: str,
    asset_id: int,
    vector: Sequence[float],
    now: int,
    input_revision: str | None = None,
) -> dict[str, Any]:
    """Publish one embedding artifact through the real artifact/outbox protocol."""
    spec = embedding_spec()
    revision = input_revision or hashlib.sha256(f"rev-{asset_id}".encode()).hexdigest()
    with fixture.catalog.write() as connection:
        job_queue.enqueue(
            connection,
            subject_type="asset",
            subject_id=str(asset_id),
            input_revision=revision,
            task_type=spec.task_type,
            processor_id=spec.processor_id,
            now_ms=now,
        )
    job = fixture.catalog.read_one(
        "SELECT * FROM jobs WHERE subject_id = ? ORDER BY job_id DESC LIMIT 1", (str(asset_id),)
    )
    if job is None:
        raise RuntimeError(f"the fixture job for asset {asset_id} was not enqueued")
    job = job_queue.Job.from_row(job)
    payload = canonical_json({"vector": [float(value) for value in vector]}).encode("utf-8")
    artifact = {
        "artifact_id": artifact_id,
        "artifact_type": "embedding",
        "artifact_revision": "embedding.v1",
        "media_type": "application/json",
        "checksum_sha256": hashlib.sha256(payload).hexdigest(),
        "byte_size": len(payload),
        "storage_uri": None,
        "payload": {"vector": [float(value) for value in vector]},
        "space_id": SPACE_ID,
        "dimension": DIM,
    }
    result = JobResult.from_envelope(
        {
            "contract_version": CONTRACT_VERSION,
            "job_id": int(job.job_id),
            "lease_token": "fixture-lease",
            "input_revision": revision,
            "task_type": spec.task_type,
            "processor_id": spec.processor_id,
            "state": "SUCCEEDED",
            "resolved_device": "cpu",
            "runtime_provider": "CPUExecutionProvider",
            "precision": "FP32",
            "progress": 1,
            "artifacts": [artifact],
            "error": None,
            "completed_at": now,
        }
    )
    store = ArtifactStore.open(fixture.data_root)
    prepared = [store.prepare(result.artifacts[0])]
    with fixture.catalog.write() as connection:
        store.persist_result(
            connection,
            job=job,
            result=result,
            spec=spec,
            prepared=prepared,
            now_ms=now,
        )
    return {"artifact_id": artifact_id, "job_id": int(job.job_id), "input_revision": revision}


def outbox_counts(fixture: ScaleFixture) -> dict[str, int]:
    rows = fixture.catalog.read_all(
        "SELECT state, COUNT(*) AS total FROM index_outbox GROUP BY state"
    )
    return {str(row["state"]): int(row["total"]) for row in rows}


def bump_filter_revision(fixture: ScaleFixture, *, cause: str) -> int:
    """Move ``catalog_filter_revision``, the value an outbox event's revision comes from.

    A content change of an asset that already has a confirmed vector needs a new
    ``projection_revision``; the place/person services move this same counter
    (``places/revision.py``), so the injection uses the documented writer instead of
    inventing a second revision source.
    """
    with fixture.catalog.write() as connection:
        connection.execute(
            "UPDATE catalog_filter_revision SET revision = revision + 1, committed_at = ?, "
            "cause = ? WHERE revision_id = 1",
            (now_ms(), str(cause)),
        )
        row = connection.execute(
            "SELECT revision FROM catalog_filter_revision WHERE revision_id = 1"
        ).fetchone()
    return int(row["revision"])


def maintenance_report(fixture: ScaleFixture, *, args: argparse.Namespace) -> dict[str, Any]:
    samples = max(1, int(args.upsert_samples))
    index = LanceVectorIndex(fixture.vectors_dir)
    timings: list[float] = []
    started_stage = time.perf_counter()
    for offset in range(samples):
        asset_id = (offset % fixture.scale) + 1
        started = time.perf_counter()
        index.upsert(
            generation={
                "generation_id": GENERATION_ID,
                "space_id": SPACE_ID,
                "dimension": DIM,
            },
            record={
                "record_id": f"{GENERATION_ID}:{asset_id}",
                "asset_id": asset_id,
                "generation_id": GENERATION_ID,
                "space_id": SPACE_ID,
                "artifact_revision": "rev-probe",
                "projection_revision": 0,
                "vector": fixture.matrix[asset_id - 1].tolist(),
                "source_ids": [1],
                "visible": True,
            },
        )
        timings.append((time.perf_counter() - started) * 1000)
        log(
            f"  maintenance upsert sample {offset + 1}/{samples}: "
            f"{timings[-1]:.0f} ms (stage {time.perf_counter() - started_stage:.0f} s)"
        )

    count = max(1, int(args.maintenance_assets))
    first = fixture.scale + 1
    with fixture.catalog.write() as connection:
        for offset in range(count):
            asset_id = first + offset
            connection.execute(
                "INSERT INTO assets(sha256, byte_size, width, height, format, created_at, state) "
                "VALUES (?, ?, 4032, 3024, 'JPEG', ?, 'ACTIVE')",
                (f"{asset_id:064x}", 2_000_000, now_ms()),
            )
            connection.execute(
                "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, size, "
                "mtime_ns, last_seen_scan_id, availability) "
                "VALUES (?, ?, ?, ?, 2000000, 1700000000000000000, NULL, 'PRESENT')",
                (
                    fixture.source_ids[0],
                    f"bucket-extra/photo-{asset_id:08d}.jpg",
                    f"bucket-extra/photo-{asset_id:08d}.jpg",
                    asset_id,
                ),
            )
    vectors = synthetic_vectors(count, seed=args.seed + 999, distribution=args.distribution)
    started = time.perf_counter()
    for offset in range(count):
        log(f"  live publish {offset + 1}/{count}")
        publish_embedding(
            fixture,
            artifact_id=f"p4-01-live-{first + offset}",
            asset_id=first + offset,
            vector=vectors[offset],
            now=now_ms(),
        )
    publish_ms = (time.perf_counter() - started) * 1000
    before = outbox_counts(fixture)

    coordinator = IndexCoordinator(fixture.catalog)
    started = time.perf_counter()
    replayed = 0
    while replayed < count and coordinator.process_next(now_ms=now_ms()):
        replayed += 1
        log(f"  outbox replay {replayed}/{count}")
    replay_ms = (time.perf_counter() - started) * 1000
    after = outbox_counts(fixture)
    ready = sum(
        1
        for offset in range(count)
        if coordinator.semantic_ready(
            space_id=SPACE_ID, asset_id=first + offset, generation_id=GENERATION_ID
        )
    )
    generation = fixture.catalog.read_one(
        "SELECT state, is_active FROM index_generations WHERE generation_id = ?", (GENERATION_ID,)
    )
    return {
        "per_record_upsert": {
            **summarize(timings),
            "rows_per_second": round(1000 / max(statistics.median(timings), 0.001), 1),
            "table_rows": int(fixture.scale),
        },
        "live_publish": {
            "assets": count,
            "publish_ms": round(publish_ms, 1),
            "publish_ms_per_asset": round(publish_ms / count, 2),
            "outbox_before_replay": before,
        },
        "replay": {
            "replayed": int(replayed),
            "replay_ms": round(replay_ms, 1),
            "replay_ms_per_event": round(replay_ms / max(count, 1), 2),
            "events_per_second": round(count / max(replay_ms / 1000, 0.001), 1),
            "outbox_after_replay": after,
            "semantic_ready": ready,
            "generation": None
            if generation is None
            else {"state": str(generation["state"]), "is_active": int(generation["is_active"])},
        },
    }


def maintenance_injections(work: Path, *, args: argparse.Namespace) -> dict[str, Any]:
    """The four documented maintenance injections on a small, fully real fixture."""
    scale = max(30, int(args.injection_assets))
    fixture = build_fixture(
        work / "injections",
        scale=scale,
        seed=args.seed + 3,
        distribution=args.distribution,
    )
    results: dict[str, Any] = {}
    try:
        spec_vector = synthetic_vectors(1, seed=args.seed + 4, distribution=args.distribution)[0]
        # 1. interrupt in the middle of maintenance, restart, compare with the pre-state.
        publish_embedding(
            fixture, artifact_id="inj1-a", asset_id=1, vector=spec_vector, now=now_ms()
        )
        publish_embedding(
            fixture, artifact_id="inj1-b", asset_id=2, vector=spec_vector, now=now_ms()
        )
        crashed = False
        coordinator = IndexCoordinator(fixture.catalog)
        try:
            coordinator.process_next(now_ms=now_ms(), failpoint="after_lance")
        except Exception:  # noqa: BLE001 - the injected crash is the point
            crashed = True
        mid = {
            "outbox": outbox_counts(fixture),
            "rows": len(
                coordinator.index.rows(generation_id=GENERATION_ID, dimension=DIM)
            ),
        }
        restarted = IndexCoordinator(fixture.catalog)
        drained = restarted.process_all(now_ms=now_ms())
        rows_after = restarted.index.rows(generation_id=GENERATION_ID, dimension=DIM)
        asset_ids = sorted(int(row["asset_id"]) for row in rows_after)
        results["interrupt_restart"] = {
            "crashed": crashed,
            "mid_state": mid,
            "drained": int(drained),
            "rows_after": len(rows_after),
            "unique_assets": len(set(asset_ids)),
            "no_logical_duplicates": len(asset_ids) == len(set(asset_ids)),
            "recovered_assets": asset_ids[-2:],
        }

        # 2. Lance written, outbox unconfirmed: replay is idempotent.
        publish_embedding(
            fixture, artifact_id="inj2", asset_id=3, vector=spec_vector, now=now_ms()
        )
        coordinator = IndexCoordinator(fixture.catalog)
        replayed = coordinator.process_all(now_ms=now_ms())
        with fixture.catalog.write() as connection:
            connection.execute(
                "UPDATE index_outbox SET state = 'PENDING' WHERE artifact_id = 'inj2'"
            )
            connection.execute(
                "UPDATE index_entries SET state = 'PENDING_REPAIR', confirmed_at = NULL "
                "WHERE asset_id = 3"
            )
        replayed_twice = IndexCoordinator(fixture.catalog).process_all(now_ms=now_ms())
        rows = IndexCoordinator(fixture.catalog).index.rows(
            generation_id=GENERATION_ID, dimension=DIM
        )
        results["unconfirmed_replay"] = {
            "first_replay": int(replayed),
            "second_replay": int(replayed_twice),
            "rows_for_asset_3": sum(1 for row in rows if int(row["asset_id"]) == 3),
            "outbox": outbox_counts(fixture),
        }

        # 3. Content change: the old vector must be unreachable before the new one lands.
        scope = retrieval.SearchScope.build()
        query = fixture.matrix[0]
        before_hit = _walk_ids(fixture, scope=scope, query=query)
        revision = bump_filter_revision(fixture, cause="CONTENT_CHANGED")
        publish_embedding(
            fixture,
            artifact_id="inj3",
            asset_id=1,
            vector=spec_vector,
            now=now_ms(),
            input_revision=hashlib.sha256(b"changed-content").hexdigest(),
        )
        after_hit = _walk_ids(fixture, scope=scope, query=query)
        entry = fixture.catalog.read_one(
            "SELECT state FROM index_entries WHERE asset_id = 1 AND generation_id = ?",
            (GENERATION_ID,),
        )
        IndexCoordinator(fixture.catalog).process_all(now_ms=now_ms())
        final_hit = _walk_ids(fixture, scope=scope, query=query)
        results["content_change"] = {
            "projection_revision": revision,
            "old_vector_hit_before": 1 in before_hit,
            "old_vector_hit_during": 1 in after_hit,
            "entry_state_during": None if entry is None else str(entry["state"]),
            "hit_after_replay": 1 in final_hit,
        }

        # 4. A location that goes offline must not stay reachable as a ghost vector.
        with fixture.catalog.write() as connection:
            connection.execute(
                "UPDATE file_locations SET availability = 'MISSING' WHERE asset_id = 2"
            )
        present = retrieval.SearchScope.build(availability="present")
        ghost_scope_ids = set(fixture.scope_ids(present))
        ghost_hits = _walk_ids(fixture, scope=present, query=fixture.matrix[1])
        all_rows = IndexCoordinator(fixture.catalog).index.rows(
            generation_id=GENERATION_ID, dimension=DIM
        )
        results["offline_location"] = {
            "asset_2_in_present_scope": 2 in ghost_scope_ids,
            "asset_2_returned": 2 in ghost_hits,
            "vector_row_still_present": any(
                int(row["asset_id"]) == 2 for row in all_rows
            ),
        }
        # 5. The repair audit: measure what one call costs while the fixture is still small
        #    enough to run it (< 2 000 rows). ``audit_and_schedule_repairs`` calls
        #    ``LanceVectorIndex.has`` once per embedding artifact, and ``has`` materialises
        #    the *whole* generation through ``rows()`` - so the shape is N_artifacts x N_rows.
        coordinator = IndexCoordinator(fixture.catalog)
        started = time.perf_counter()
        coordinator.index.rows(generation_id=GENERATION_ID, dimension=DIM)
        rows_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        coordinator.index.has(generation_id=GENERATION_ID, dimension=DIM, asset_id=1)
        has_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        audit = coordinator.audit_and_schedule_repairs(now_ms=now_ms())
        audit_ms = (time.perf_counter() - started) * 1000
        results["repair_audit"] = {
            "fixture_rows": scale,
            "rows_call_ms": round(rows_ms, 2),
            "has_call_ms": round(has_ms, 2),
            "audit_ms": round(audit_ms, 1),
            "audit_ms_per_artifact": round(audit_ms / max(scale, 1), 3),
            "scheduled": len(audit["scheduled"]),
            "requires_reexecution": len(audit["requires_reexecution"]),
            "note": (
                "rows() is O(N) and has() calls it, so one audit pass is O(N_artifacts x N_rows)"
            ),
        }
    finally:
        fixture.catalog.close()
    return results


def _walk_ids(
    fixture: ScaleFixture, *, scope: retrieval.SearchScope, query: Any, limit: int = 200
) -> set[int]:
    binding = retrieval.resolve_binding(
        fixture.catalog, generation_id=GENERATION_ID, space_id=SPACE_ID, dimension=DIM
    )
    block = retrieval.vector_channel(
        fixture.catalog,
        index=LanceVectorIndex(fixture.vectors_dir),
        binding=binding,
        query_vector=[float(value) for value in query],
        scope=scope,
        top_k=limit,
    )
    return {int(row["asset_id"]) for row in block["hits"]}


class DeterministicEncoder:
    """A non-model query encoder: the pinned weights are not installed on this machine."""

    def __init__(self) -> None:
        self._spec = embedding_spec()

    def spec(self) -> ProcessorSpec:
        return self._spec

    def encode_queries(self, texts: Sequence[str], cancellation: Any = None) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            seed = int.from_bytes(hashlib.sha256(str(text).encode()).digest()[:8], "big")
            vector = synthetic_vectors(1, seed=seed, distribution="uniform")[0]
            vectors.append([float(value) for value in vector])
        return vectors


# --------------------------------------------------------------------------- concurrency
def concurrency_report(work: Path, *, args: argparse.Namespace) -> dict[str, Any]:
    scale = int(args.concurrency_scale)
    fixture = build_fixture(
        work / f"concurrent-{scale}",
        scale=scale,
        seed=args.seed + 17,
        distribution=args.distribution,
    )
    try:
        count = max(1, int(args.background_events))
        first = scale + 1
        with fixture.catalog.write() as connection:
            for offset in range(count):
                asset_id = first + offset
                connection.execute(
                    "INSERT INTO assets(sha256, byte_size, width, height, format, created_at, "
                    "state) VALUES (?, 2000000, 4032, 3024, 'JPEG', ?, 'ACTIVE')",
                    (f"{asset_id:064x}", now_ms()),
                )
                connection.execute(
                    "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, "
                    "size, mtime_ns, last_seen_scan_id, availability) "
                    "VALUES (?, ?, ?, ?, 2000000, 1700000000000000000, NULL, 'PRESENT')",
                    (
                        fixture.source_ids[0],
                        f"bucket-bg/photo-{asset_id:08d}.jpg",
                        f"bucket-bg/photo-{asset_id:08d}.jpg",
                        asset_id,
                    ),
                )
        vectors = synthetic_vectors(count, seed=args.seed + 31, distribution=args.distribution)
        for offset in range(count):
            publish_embedding(
                fixture,
                artifact_id=f"p4-01-bg-{first + offset}",
                asset_id=first + offset,
                vector=vectors[offset],
                now=now_ms(),
            )
        pending_before = outbox_counts(fixture)
        fixture.catalog.close()

        worker = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--mode",
                "drain-worker",
                "--work-dir",
                str(work / f"concurrent-{scale}"),
                "--data-root",
                str(work / f"concurrent-{scale}" / "data"),
                "--limit",
                str(count),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        foreground = _foreground_searches(
            work / f"concurrent-{scale}" / "data",
            count=int(args.concurrency_searches),
            args=args,
        )
        stdout, stderr = worker.communicate(timeout=3600)
        background = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else {}
        return {
            "scale": scale,
            "background": {
                "events": count,
                "pending_before": pending_before,
                "worker": background,
                "returncode": worker.returncode,
                "stderr_tail": stderr.strip().splitlines()[-3:] if stderr.strip() else [],
            },
            "foreground": foreground,
            "warm_cache_seconds": float(args.warm_seconds),
        }
    finally:
        fixture.catalog.close()


def _foreground_searches(
    data_root: Path, *, count: int, args: argparse.Namespace
) -> dict[str, Any]:
    catalog = Catalog.open(data_root=data_root)
    try:
        encoder = DeterministicEncoder()
        query_vector = synthetic_vectors(1, seed=args.seed + 41, distribution=args.distribution)[0]
        # Warm the engine and the catalog page cache before the measured window.
        search_assets(
            catalog,
            text="beach",
            encoder=encoder,
            query_vector=[float(value) for value in query_vector],
            space_id=SPACE_ID,
            generation_id=GENERATION_ID,
            dimension=DIM,
            top_k=20,
        )
        time.sleep(float(args.warm_seconds))
        samples: list[float] = []
        for _ in range(max(1, count)):
            started = time.perf_counter()
            payload = search_assets(
                catalog,
                text="beach",
                encoder=encoder,
                query_vector=[float(value) for value in query_vector],
                space_id=SPACE_ID,
                generation_id=GENERATION_ID,
                dimension=DIM,
                top_k=20,
            )
            samples.append((time.perf_counter() - started) * 1000)
        summary = summarize(samples)
        summary["target_p95_ms"] = 1500
        summary["meets_target"] = bool(summary["p95_ms"] <= 1500)
        summary["hits"] = len(payload.get("hits", []))
        return summary
    finally:
        catalog.close()


def drain_worker(args: argparse.Namespace) -> int:
    catalog = Catalog.open(data_root=args.data_root)
    try:
        coordinator = IndexCoordinator(catalog)
        started = time.perf_counter()
        replayed = coordinator.process_all(now_ms=now_ms(), limit=int(args.limit))
        elapsed = (time.perf_counter() - started) * 1000
        print(
            json.dumps(
                {
                    "replayed": int(replayed),
                    "elapsed_ms": round(elapsed, 1),
                    "events_per_second": round(replayed / max(elapsed / 1000, 0.001), 1),
                    "remaining": int(
                        catalog.read_one(
                            "SELECT COUNT(*) FROM index_outbox WHERE state <> 'CONFIRMED'"
                        )[0]
                    ),
                }
            )
        )
    finally:
        catalog.close()
    return 0


# --------------------------------------------------------------------------- cli
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P4-01 ANN/scale benchmark")
    parser.add_argument(
        "--mode",
        default="all",
        choices=(
            "all",
            "scale",
            "attribution",
            "design",
            "maintenance",
            "concurrency",
            "drain-worker",
        ),
    )
    parser.add_argument("--scale", default="100000,300000")
    parser.add_argument("--attribution-scale", type=int, default=20000)
    parser.add_argument(
        "--sweep-block-sizes",
        default="1000,5000,25000,100000",
        help="candidate SCOPE_BLOCK_SIZE values for the design block-size sweep",
    )
    parser.add_argument("--work-dir", type=Path, default=Path("tmp/p4-01-benchmark"))
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--queries", type=int, default=1000)
    parser.add_argument("--boundary-queries", type=int, default=50)
    parser.add_argument("--walk-queries", type=int, default=3)
    parser.add_argument("--filter-walk-queries", type=int, default=1)
    #: At 100 000 rows one production per-record ``merge_insert`` costs tens of seconds (the
    #: engine rewrites the touched fragment and its index segments), so the scale-stage
    #: samples are deliberately small and the extrapolation stays explicit in the report.
    parser.add_argument("--upsert-samples", type=int, default=5)
    parser.add_argument("--maintenance-assets", type=int, default=5)
    parser.add_argument("--injection-assets", type=int, default=200)
    parser.add_argument("--concurrency-scale", type=int, default=30000)
    parser.add_argument("--concurrency-searches", type=int, default=8)
    parser.add_argument("--background-events", type=int, default=200)
    parser.add_argument("--warm-seconds", type=float, default=2.0)
    parser.add_argument("--distribution", default="clustered", choices=("clustered", "uniform"))
    parser.add_argument("--skip-ann-variants", action="store_true")
    parser.add_argument(
        "--variant-queries",
        type=int,
        default=200,
        help="fixed query subsample used by the single-variable ANN attribution sweep",
    )
    parser.add_argument(
        "--query-budget-seconds",
        type=float,
        default=120.0,
        help="time budget for one authoritative filter query before it is reported as slow",
    )
    parser.add_argument(
        "--reuse-fixture",
        action="store_true",
        help="re-open an already materialised scale fixture instead of rebuilding it",
    )
    parser.add_argument(
        "--no-payload-files",
        action="store_true",
        help=(
            "register the protocol-complete artifact rows without materialising their JSON "
            "payload files (the retrieval path never reads them; the bounded-scale tests do)"
        ),
    )
    parser.add_argument("--seed", type=int, default=20260923)
    parser.add_argument("--limit", type=int, default=10_000)
    parser.add_argument("--keep", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = parse_args(argv)
    if args.mode == "drain-worker":
        return drain_worker(args)

    work = Path(args.work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    # `design` probes an *existing* fixture, so it must never trigger the throw-away cleanup.
    if not args.keep and not args.reuse_fixture and args.mode != "design":
        for leftover in work.glob("scale-*"):
            shutil.rmtree(leftover, ignore_errors=True)
        for leftover in work.glob("concurrent-*"):
            shutil.rmtree(leftover, ignore_errors=True)
        for leftover in work.glob("injections"):
            shutil.rmtree(leftover, ignore_errors=True)

    report: dict[str, Any] = {
        "task": "P4-01",
        "status": "ok",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "machine": machine_facts(),
        "work_dir": str(work),
        "space": {
            "space_id": SPACE_ID,
            "processor_id": PROCESSOR_ID,
            "generation_id": GENERATION_ID,
            "dimension": DIM,
            "metric": "cosine",
            "normalization": "L2",
        },
        "args": {key: str(value) for key, value in vars(args).items()},
        "scales": [],
    }
    if args.mode in ("all", "scale"):
        for scale in [int(value) for value in str(args.scale).split(",") if value.strip()]:
            report["scales"].append(scale_report(work, scale=scale, args=args, seed=args.seed))
            write_report(args, report)
    if args.mode in ("all", "attribution"):
        report["attribution"] = attribution_report(work, args=args, seed=args.seed)
        write_report(args, report)
    if args.mode in ("design",):
        report["design"] = design_report(work, args=args, seed=args.seed)
        write_report(args, report)
    if args.mode in ("all", "maintenance"):
        report["maintenance_injections"] = maintenance_injections(work, args=args)
        write_report(args, report)
    if args.mode in ("all", "concurrency"):
        report["concurrency"] = concurrency_report(work, args=args)
        write_report(args, report)
    report["peak_working_set_bytes"] = process_memory_bytes()
    write_report(args, report)
    if args.output is not None:
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "output": str(args.output),
                    "scales": [entry["scale"] for entry in report.get("scales", [])],
                    "modes": sorted(
                        key for key in ("scales", "maintenance_injections", "concurrency")
                        if key in report
                    ),
                }
            )
        )
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def write_report(args: argparse.Namespace, report: dict[str, Any]) -> None:
    if args.output is None:
        return
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(payload + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
