"""P4-02B formal capacity validation: 10 000 / 100 000 / 300 000 assets.

Every scale is built as a throw-away synthetic library plus a throw-away data root inside
``--work-dir`` (a project-local temporary directory). No real photo library is ever read,
written or scanned, and no model weights are required.

The stages are driven through the product's own code, never through a copy of it:

* ``scan`` - ``CatalogService.run_scan`` (streaming enumeration, content confirmation,
  stability policy, EXIF/mtime capture-time extraction);
* ``catalog`` - the derived P2-04 search layer and its FTS index through
  ``search.documents`` (the same catch-up the API performs), plus the P3 media cache
  (thumbnails) when it is not skipped;
* ``semantic`` - real ``jobs`` rows, the real ``Scheduler`` with a diagnostic
  ``RuntimeCapabilities``/qualification, real ``ArtifactStore.prepare``/``persist_result``
  publications (real files, real checksums, real ``index_outbox`` rows) and the production
  maintenance entry ``python -m photo_search.indexing.cli drain`` for the confirmation and
  the ``asset_id`` scalar index;
* ``query`` - ``search.service.search_assets`` and the real HTTP ``POST /api/v1/search``
  through the ASGI application;
* ``injections`` - the P2-03/P2-04/P4-01C fault set on a small fixture (interrupted drain,
  ``after_lance`` crash window, content change, offline asset, pending-projection top-up).

Honest limitations, recorded in the report instead of being hidden:

1. No pinned Chinese-CLIP (and no face/OCR) weights are installed on this machine, so an
   embedding is produced by a deterministic stand-in executor: same protocol, same files,
   same outbox, no model. Real-model throughput, VRAM peaks, the GTX 1660 minimum
   configuration and the face/OCR stage times are therefore reported ``BLOCKED``.
2. The query side measures the real retrieval path but encodes the query text with the same
   deterministic stand-in, so the latency numbers contain the retrieval/fusion/SQLite cost
   and not the text-tower cost.
3. The ANN index is not created by the product (P4-01B decision B1), so the exact scan is
   the only retrieval strategy measured here; the section 12.3 ANN Recall 0.95 stays
   unverified.

Usage::

    uv run --no-sync python tools/diagnostics/capacity_validation.py \
        --scales 10000,100000,300000 --work-dir "tmp/p402b 容量 验证" \
        --output test-results/p4-02b-capacity.json
    uv run --no-sync python tools/diagnostics/capacity_validation.py --pilot
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ann_scale_benchmark as bench  # noqa: E402
from scan_scale_probe import process_memory_bytes  # noqa: E402

from photo_search.catalog.db import Catalog  # noqa: E402
from photo_search.config import catalog_db_path, vectors_dir  # noqa: E402
from photo_search.indexing import maintenance  # noqa: E402
from photo_search.indexing.store import IndexCoordinator  # noqa: E402
from photo_search.ingestion.service import CatalogService  # noqa: E402
from photo_search.jobs import queue as job_queue  # noqa: E402
from photo_search.scheduler import engine  # noqa: E402
from photo_search.scheduler.contracts import (  # noqa: E402
    CONTRACT_VERSION,
    RuntimeCapabilities,
    canonical_json,
)
from photo_search.search import documents as search_documents  # noqa: E402
from photo_search.search.service import search_assets  # noqa: E402

#: The pinned processor identity of ``ann_scale_benchmark`` (real space/dimension of the
#: Chinese-CLIP ViT-B/16 export); only the encoder behind it is a stand-in.
SPACE_ID = bench.SPACE_ID
PROCESSOR_ID = bench.PROCESSOR_ID
GENERATION_ID = bench.GENERATION_ID
DIM = bench.DIM

#: ``bench.embedding_spec()`` declares this runtime package and a CPU-only device policy.
RUNTIME_PACKAGE = "onnxruntime-cpu@p4-01"
PROVIDER = "CPUExecutionProvider"
PRECISION = "FP32"

#: Synthetic JPEG size. Small enough that 300 000 files stay inside a few GiB, large enough
#: that the decode path does real work.
DEFAULT_IMAGE_SIZE = "320x240"
#: Files are stamped this far into the past so a scan never waits for the quiet period.
AGE_MS = 3_600_000
#: Query text and sample count of the latency measurement.
DEFAULT_QUERY_TEXT = "西湖 sunset 全家福"
DEFAULT_QUERIES = 40
DEFAULT_WARM_SECONDS = 1.0
#: Outbox events one production drain transaction covers (``IndexCoordinator.BATCH_SIZE``).
DRAIN_BATCH = IndexCoordinator.BATCH_SIZE
#: Assets of the small fixture the D-level injections run on.
DEFAULT_INJECTION_SCALE = 1_500

REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- utilities
def now_ms() -> int:
    return time.time_ns() // 1_000_000


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", file=sys.stderr, flush=True)


def ms_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def directory_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:  # pragma: no cover - a vanished temp file is not a measurement
            continue
    return total


def current_working_set_bytes() -> int:
    """Working set of this process right now (Windows) or current RSS elsewhere."""
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
        return int(counters.WorkingSetSize)
    with open("/proc/self/statm", encoding="ascii") as stream:  # pragma: no cover - POSIX
        pages = int(stream.read().split()[1])
    return pages * os.sysconf("SC_PAGE_SIZE")


class MemorySampler:
    """Samples the current working set in a thread, so a stage peak is measurable."""

    def __init__(self, interval: float = 0.05) -> None:
        self.interval = float(interval)
        self.before = 0
        self.peak = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.peak = max(self.peak, current_working_set_bytes())
            except OSError:  # pragma: no cover - sampling is best-effort
                return
            self._stop.wait(self.interval)

    def __enter__(self) -> MemorySampler:
        self.before = current_working_set_bytes()
        self.peak = self.before
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.peak = max(self.peak, current_working_set_bytes())

    def report(self) -> dict[str, int]:
        return {
            "working_set_before_bytes": int(self.before),
            "working_set_peak_bytes": int(self.peak),
            "working_set_delta_bytes": int(self.peak - self.before),
            "process_peak_working_set_bytes": int(process_memory_bytes()),
        }


@contextmanager
def stage(name: str) -> Iterator[dict[str, Any]]:
    """Time one stage and fold its wall time plus memory delta into its report block."""
    sampler = MemorySampler()
    payload: dict[str, Any] = {}
    started = time.perf_counter()
    log(f"{name}: start")
    with sampler:
        yield payload
    payload["ms"] = ms_since(started)
    payload.update(sampler.report())
    log(f"{name}: {payload['ms']} ms")


# --------------------------------------------------------------------------- fixture
def _variant_jpeg(width: int, height: int, index: int) -> bytes:
    """One deterministic, cheaply compressible JPEG used as the base of many fixtures."""
    rng = np.random.default_rng(90_000 + int(index))
    top = rng.integers(0, 256, size=3)
    bottom = rng.integers(0, 256, size=3)
    ratio = np.linspace(0.0, 1.0, int(height), dtype=np.float32)[:, None]
    column = (top[None, :] * (1.0 - ratio) + bottom[None, :] * ratio).astype(np.uint8)
    canvas = np.repeat(column[:, None, :], int(width), axis=1)
    left = int(rng.integers(0, max(int(width) - 32, 1)))
    upper = int(rng.integers(0, max(int(height) - 32, 1)))
    canvas[upper : upper + 32, left : left + 32] = rng.integers(0, 256, size=3)
    buffer = io.BytesIO()
    Image.fromarray(canvas).save(buffer, "JPEG", quality=80)
    return buffer.getvalue()


def build_library(
    root: Path,
    *,
    files: int,
    width: int,
    height: int,
    seed: int,
    buckets: int = 256,
    variants: int = 64,
) -> dict[str, Any]:
    """A seeded synthetic library: distinct bytes per file, valid JPEG content.

    Every file is one of ``variants`` encoded JPEGs plus a unique ASCII trailer, so the
    content hashes differ (distinct assets), the decoder still sees a well-formed JPEG and
    the fixture cost stays a memory copy instead of one JPEG encode per file.
    """
    root.mkdir(parents=True, exist_ok=True)
    base = [_variant_jpeg(width, height, index + seed) for index in range(max(1, int(variants)))]
    stamp = (int(time.time() * 1000) - AGE_MS) * 1_000_000
    per_bucket = max((int(files) + int(buckets) - 1) // int(buckets), 1)
    total_bytes = 0
    digests: set[str] = set()
    for index in range(int(files)):
        bucket = root / f"bucket-{index // per_bucket:04d}"
        bucket.mkdir(exist_ok=True)
        payload = base[index % len(base)] + f"\n#P4-02B-{index:08d}".encode("ascii")
        path = bucket / f"photo-{index:08d}.jpg"
        path.write_bytes(payload)
        os.utime(path, ns=(stamp, stamp))
        total_bytes += len(payload)
        if index < 32:
            digests.add(hashlib.sha256(payload).hexdigest())
    return {
        "files": int(files),
        "bytes": int(total_bytes),
        "bytes_per_file": round(total_bytes / max(int(files), 1), 1),
        "buckets": int(buckets),
        "variants": len(base),
        "sample_distinct_sha256": len(digests),
        "sample_size": min(32, int(files)),
    }


def verify_fixture_decodes(library: Path) -> dict[str, Any]:
    """The trailing marker must not break the real decode path."""
    sample = sorted(library.rglob("photo-*.jpg"))[:3]
    sizes: list[list[int]] = []
    for path in sample:
        with Image.open(path) as image:
            image.load()
            sizes.append([int(image.size[0]), int(image.size[1])])
    return {"decoded": len(sizes), "sizes": sizes}


# --------------------------------------------------------------------------- capability
def capability(now: int) -> RuntimeCapabilities:
    """A healthy CPU capability for the diagnostic runtime package (no real driver)."""
    return RuntimeCapabilities.seal(
        {
            "runtime_package_id": RUNTIME_PACKAGE,
            "runtime_backend": "onnxruntime",
            "runtime_package_name": RUNTIME_PACKAGE,
            "runtime_package_version": "1",
            "python_version": "3.12.10",
            "platform": "windows-x86_64",
            "driver_version": None,
            "providers": [
                {
                    "name": PROVIDER,
                    "device": "cpu",
                    "precisions": [PRECISION],
                    "available": True,
                    "health": "healthy",
                    "health_checked_at": int(now),
                    "evidence_id": None,
                }
            ],
            "health_status": "healthy",
            "health_checked_at": int(now),
            "measured_at": int(now),
            "evidence_ids": [],
            "failure_code": None,
        }
    )


def vector_for(asset_id: int, *, seed: int, distribution: str = "clustered") -> list[float]:
    """The deterministic stand-in vector of one asset (no model, no GPU)."""
    values = bench.synthetic_vectors(
        1, seed=int(seed) * 1_000_003 + int(asset_id), distribution=distribution
    )
    return [float(value) for value in values[0]]


def embedding_artifact(artifact_id: str, vector: Sequence[float]) -> dict[str, Any]:
    payload = {"vector": [float(value) for value in vector]}
    content = canonical_json(payload).encode("utf-8")
    return {
        "artifact_id": str(artifact_id),
        "artifact_type": "embedding",
        "artifact_revision": "embedding.v1",
        "media_type": "application/json",
        "checksum_sha256": hashlib.sha256(content).hexdigest(),
        "byte_size": len(content),
        "storage_uri": None,
        "payload": payload,
        "space_id": SPACE_ID,
        "dimension": DIM,
    }


def embedding_executor(
    *, seed: int, distribution: str = "clustered"
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """A deterministic stand-in for the embedding worker (protocol-identical)."""

    def run(request: dict[str, Any]) -> dict[str, Any]:
        asset_id = int(request["subject_id"])
        vector = vector_for(asset_id, seed=seed, distribution=distribution)
        return {
            "contract_version": CONTRACT_VERSION,
            "job_id": int(request["job_id"]),
            "lease_token": str(request["lease_token"]),
            "input_revision": str(request["input_revision"]),
            "task_type": str(request["task_type"]),
            "processor_id": str(request["processor_id"]),
            "state": "SUCCEEDED",
            "resolved_device": str(request["resolved_device"]),
            "runtime_provider": str(request["runtime_provider"]),
            "precision": str(request["precision"]),
            "progress": 1.0,
            "artifacts": [embedding_artifact(f"p4-02b-embedding-{asset_id}", vector)],
            "error": None,
            "completed_at": now_ms(),
        }

    return run


def revision_for(asset_id: int, tag: str = "") -> str:
    """The content revision a job/artifact claims; ``tag`` keeps injections distinct."""
    return hashlib.sha256(f"p4-02b-rev-{int(asset_id)}{tag}".encode()).hexdigest()


def enqueue_jobs(catalog: Catalog, *, assets: Sequence[int], now: int) -> dict[str, Any]:
    """Queue one embedding job per asset through the product's own job queue."""
    spec = bench.embedding_spec()
    chunk = 1_000
    enqueued = 0
    started = time.perf_counter()
    for offset in range(0, len(assets), chunk):
        with catalog.write() as connection:
            for asset_id in assets[offset : offset + chunk]:
                job_queue.enqueue(
                    connection,
                    subject_type="asset",
                    subject_id=str(int(asset_id)),
                    input_revision=revision_for(int(asset_id)),
                    task_type=spec.task_type,
                    processor_id=spec.processor_id,
                    now_ms=int(now),
                )
                enqueued += 1
    return {"enqueued": enqueued, "ms": ms_since(started)}


def prepare_scheduler(catalog: Catalog, *, now: int) -> engine.Scheduler:
    """The production scheduler bound to the diagnostic processor."""
    spec = bench.embedding_spec()
    scheduler = engine.Scheduler(catalog, [spec], lease_seconds=3_600)
    scheduler.record_capability(capability(now), now_ms=now)
    scheduler.record_qualification(
        processor_id=spec.processor_id,
        spec_revision=spec.spec_revision,
        device="cpu",
        precision=PRECISION,
        provider_name=PROVIDER,
        runtime_package_id=RUNTIME_PACKAGE,
        now_ms=now,
    )
    return scheduler


class Fixture:
    """The two attributes ``ann_scale_benchmark`` publication helpers need."""

    def __init__(self, catalog: Catalog, data_root: Path) -> None:
        self.catalog = catalog
        self.data_root = Path(data_root)


def cli_environment() -> dict[str, str]:
    """The child environment of every product-CLI call (UTF-8 pipes, project-local cache)."""
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment.setdefault("UV_CACHE_DIR", str(REPO_ROOT / ".uv-cache"))
    return environment


def spawn_cli(args: Sequence[str]) -> subprocess.Popen[bytes]:
    """Start ``python -m photo_search.indexing.cli`` without waiting for it."""
    return subprocess.Popen(
        [sys.executable, "-m", "photo_search.indexing.cli", *[str(value) for value in args]],
        cwd=str(REPO_ROOT),
        env=cli_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def cli_result(
    process: subprocess.Popen[bytes], *, started: float, stdout: bytes, stderr: bytes
) -> dict[str, Any]:
    """The parsed one-document answer of a finished CLI call."""
    text_out = stdout.decode("utf-8", errors="replace")
    text_err = stderr.decode("utf-8", errors="replace")
    lines = [line for line in text_out.strip().splitlines() if line.strip()]
    payload: dict[str, Any] = {}
    if lines:
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError:
            payload = {"status": "decode_error"}
    return {
        "ms": ms_since(started),
        "returncode": int(process.returncode if process.returncode is not None else -1),
        "payload": payload,
        "stdout_tail": lines[-1][:400] if lines else None,
        "stderr_tail": text_err.strip().splitlines()[-3:] if text_err.strip() else [],
    }


def run_cli(args: Sequence[str], *, timeout: float = 24 * 3_600) -> dict[str, Any]:
    """Run the product CLI to completion and parse its one JSON document."""
    started = time.perf_counter()
    process = spawn_cli(args)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:  # pragma: no cover - a hung CLI is reported, not hidden
        process.kill()
        stdout, stderr = process.communicate()
    return cli_result(process, started=started, stdout=stdout, stderr=stderr)


def semantic_status(data_root: Path) -> dict[str, Any]:
    return maintenance.semantic_index_status(catalog_db_path(data_root))


def scale_directory(scale: int, args: argparse.Namespace) -> str:
    """The per-scale tree name inside ``--work-dir``.

    ``--label`` keeps two otherwise identical generations of the *same* scale in one work
    directory, which is what a controlled A/B comparison needs (P4-02D): without it the
    second build would overwrite the first. The label changes nothing about the fixture,
    the seed, the query or the write shape - only where the tree is written.
    """
    label = getattr(args, "label", None)
    suffix = "" if not label else f"-{label}"
    return f"scale-{scale}{suffix}"


# --------------------------------------------------------------------------- stages
def capacity_scale(
    report: dict[str, Any], work: Path, scale: int, args: argparse.Namespace
) -> dict[str, Any]:
    """One full capacity scale through the product path (fills ``report`` in place)."""
    base = work / scale_directory(scale, args)
    library = base / "library"
    data_root = base / "data"
    measured_at = now_ms()
    report.update(
        {
        "scale": int(scale),
        "work_dir": str(base) if args.keep else None,
        "image_size": f"{args.image_width}x{args.image_height}",
        "seed": int(args.seed),
        }
    )

    with stage("fixture") as fixture_report:
        fixture_report.update(
            build_library(
                library,
                files=scale,
                width=args.image_width,
                height=args.image_height,
                seed=args.seed,
            )
        )
        fixture_report["decode_check"] = verify_fixture_decodes(library)
        fixture_report["library_bytes"] = directory_bytes(library)
    report["fixture"] = fixture_report

    with stage("scan") as scan_report:
        with CatalogService.open(data_root=data_root) as service:
            source_id = int(
                service.register_source(display_name="p4-02b", root_path=library)["source_id"]
            )
            started = time.perf_counter()
            outcome = service.run_scan(source_id)
            scan_report["scan_ms"] = ms_since(started)
            scan_report["state"] = str(outcome["state"])
            scan_report["enumerated"] = int(outcome["enumerated"])
            scan_report["enumerate_ms"] = outcome["enumeration_ms"]
            scan_report["process_ms"] = outcome["processing_ms"]
            scan_report["prepare_ms"] = outcome["prepare_ms"]
            scan_report["stability_waits"] = outcome["stability_waits"]
            scan_report["counts"] = dict(outcome["counts"])
            scan_report["source_id"] = source_id
    assets = int(scan_report["counts"]["assets_total"])
    scan_report["assets_total"] = assets
    scan_report["assets_per_second"] = round(
        assets / max(float(scan_report["scan_ms"]), 1.0) * 1000, 1
    )
    report["scan"] = scan_report

    catalog = Catalog.open(data_root=data_root)
    try:
        with stage("catalog") as catalog_report:
            queue_before = search_documents.queue_depth(catalog.connection)
            batches = max(1, assets // search_documents.DRAIN_BATCH_SIZE + 2)
            started = time.perf_counter()
            catch_up = search_documents.catch_up(catalog, batches=batches)
            catch_up_ms = ms_since(started)
            catalog_report["queue_before"] = int(queue_before)
            catalog_report["catch_up_ms"] = catch_up_ms
            catalog_report["catch_up"] = dict(catch_up)
            catalog_report["documents_per_second"] = round(
                assets / max(catch_up_ms, 1.0) * 1000, 1
            )
            catalog_report["projection"] = search_documents.projection_summary(
                catalog.connection
            )
            catalog_report["fts_rows"] = int(
                catalog.read_one("SELECT COUNT(*) FROM search_documents_fts")[0]
            )
        report["catalog"] = catalog_report
    finally:
        catalog.close()

    if not args.skip_thumbnails:
        with stage("thumbnails") as thumbnail_report:
            with CatalogService.open(data_root=data_root) as service:
                summary = service.generate_thumbnail()
            thumbnail_report["generated"] = int(summary["generated"])
            thumbnail_report["failed"] = int(summary["failed"])
            thumbnail_report["unsupported"] = int(summary["unsupported"])
            thumbnail_report["bytes_written"] = int(summary["bytes_written"])
            thumbnail_report["bytes_per_artifact"] = round(
                int(summary["bytes_written"]) / max(int(summary["generated"]), 1)
            )
        thumbnail_report["thumbnails_per_second"] = round(
            int(thumbnail_report["generated"]) / max(float(thumbnail_report["ms"]), 1.0) * 1000,
            1,
        )
        report["thumbnails"] = thumbnail_report

    catalog = Catalog.open(data_root=data_root)
    try:
        asset_ids = [
            int(row["asset_id"])
            for row in catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id")
        ]
        report["assets"] = len(asset_ids)
        if args.semantic:
            report["semantic"] = semantic_stage(
                catalog, data_root=data_root, asset_ids=asset_ids, args=args, now=measured_at
            )
        if args.queries:
            report["query"] = query_stage(catalog, data_root=data_root, args=args)
    finally:
        catalog.close()

    report["storage"] = {
        "library_bytes": directory_bytes(library),
        "catalog_bytes": catalog_db_path(data_root).stat().st_size
        if catalog_db_path(data_root).exists()
        else 0,
        "artifacts_bytes": directory_bytes(data_root / "artifacts"),
        "thumbnails_bytes": directory_bytes(data_root / "thumbnails"),
        "vectors_bytes": directory_bytes(vectors_dir(data_root)),
        "data_root_bytes": directory_bytes(data_root),
    }
    report["semantic_status"] = semantic_status(data_root)
    if not args.keep:
        shutil.rmtree(base, ignore_errors=True)
    return report


def semantic_stage(
    catalog: Catalog,
    *,
    data_root: Path,
    asset_ids: Sequence[int],
    args: argparse.Namespace,
    now: int,
) -> dict[str, Any]:
    """The semantic stage: real jobs, real scheduler, real artifacts, production drain.

    The work is released in **bounded waves** (``--publish-wave``), and every wave is drained
    by the production entry before the next one is published: that is the steady-state shape
    of the AI worker (continuous publication) plus the maintenance entry, and it is the only
    shape in which the claim query stays cheap - ``jobs.claimable_candidates`` materialises
    and sorts the *whole* runnable backlog per claim, so one 300 000-event burst turns the
    stage quadratic. ``--burst-sample`` keeps one deliberately unbounded burst at the front of
    the run so that cost stays measured instead of assumed.
    """
    report: dict[str, Any] = {"encoder": "deterministic-stand-in (no pinned model weights)"}
    ordered = [int(value) for value in asset_ids]
    wave_size = max(1, int(args.publish_wave))
    burst = min(len(ordered), max(0, int(args.burst_sample)))
    sequences: list[tuple[str, list[int]]] = []
    if burst:
        sequences.append(("burst", ordered[:burst]))
    rest = ordered[burst:]
    sequences.extend(
        ("wave", rest[offset : offset + wave_size])
        for offset in range(0, len(rest), wave_size)
    )
    scheduler = prepare_scheduler(catalog, now=now)
    executor = embedding_executor(seed=args.seed)
    waves: list[dict[str, Any]] = []
    steps = 0
    for index, (kind, batch) in enumerate(sequences):
        enqueue = enqueue_jobs(catalog, assets=batch, now=now)
        execute_started = time.perf_counter()
        steps += scheduler.run_until_idle(
            executor,
            worker_id="p4-02b",
            now_ms=now,
            max_steps=len(batch) + 16,
        )
        execute_ms = ms_since(execute_started)
        outbox = bench.outbox_counts(Fixture(catalog, data_root))
        drain_started = time.perf_counter()
        worker = spawn_cli(
            [
                "drain",
                "--data-root",
                str(data_root),
                "--json",
                "--limit",
                str(len(batch) + 16),
                "--batch-size",
                str(args.drain_batch),
            ]
        )
        if index == len(sequences) - 1:
            # The final drain is the largest one still open while the user searches, so this
            # is where the section 12.2 contention target is measured.
            report["contention"] = foreground_during_drain(catalog, worker=worker, args=args)
        stdout, stderr = worker.communicate()
        drain = cli_result(worker, started=drain_started, stdout=stdout, stderr=stderr)
        payload = drain["payload"] if isinstance(drain.get("payload"), dict) else {}
        waves.append(
            {
                "kind": kind,
                "assets": len(batch),
                "enqueue_ms": enqueue["ms"],
                "execute_ms": execute_ms,
                "execute_assets_per_second": round(
                    len(batch) / max(execute_ms, 1.0) * 1000, 1
                ),
                "outbox_before_drain": outbox,
                "drain_ms": drain["ms"],
                "applied": int(payload.get("applied") or 0),
                "batches": int(payload.get("batches") or 0),
                "returncode": drain["returncode"],
                "lock_wait_ms": payload.get("lock_wait_ms"),
                "write_hold_ms": payload.get("write_hold_ms"),
                "scalar_index": payload.get("scalar_index"),
                "index_after": payload.get("index", {}).get("after") if isinstance(payload.get("index"), dict) else None,
            }
        )
        report["drain"] = drain
    total_execute = round(sum(float(wave["execute_ms"]) for wave in waves), 1)
    total_drain = round(sum(float(wave["drain_ms"]) for wave in waves), 1)
    applied_total = sum(int(wave["applied"]) for wave in waves)
    report["execute"] = {
        "steps": int(steps),
        "ms": total_execute,
        "assets_per_second": round(len(ordered) / max(total_execute, 1.0) * 1000, 1),
        "jobs": scheduler.counts(),
        "waves": len(waves),
        "wave_size": wave_size,
        "burst_sample": burst,
    }
    report["waves"] = waves
    report["outbox_before_drain"] = waves[0]["outbox_before_drain"] if waves else {}
    report["drain_total"] = {
        "applied": int(applied_total),
        "ms": total_drain,
        "ms_per_event": round(total_drain / max(applied_total, 1), 3),
        "events_per_second": round(applied_total / max(total_drain, 1.0) * 1000, 1),
    }
    # ``ensure_scalar_indexes`` runs before the first engine write, so the very first drain
    # can only report ``no_table``. A second pass over an empty outbox is what really creates
    # the ``asset_id`` scalar index, and its cost is part of the maintenance budget.
    index_pass = run_cli(["drain", "--data-root", str(data_root), "--json", "--limit", "0"])
    report["scalar_index_pass"] = index_pass
    report["scalar_index"] = (
        index_pass["payload"].get("scalar_index") if isinstance(index_pass.get("payload"), dict) else None
    )
    report["status_cli"] = run_cli(["status", "--data-root", str(data_root), "--json"])
    report["index"] = maintenance.semantic_index_status(catalog_db_path(data_root))["index"]
    return report


def foreground_during_drain(
    catalog: Catalog, *, worker: subprocess.Popen[bytes], args: argparse.Namespace
) -> dict[str, Any]:
    """Foreground text searches issued while the production drain of this scale runs.

    This is the section 12.2 "background build while the user searches" target (P95 <= 1.5 s).
    The samples stop when the drain exits, so the reported sample count is the honest overlap
    of the two activities and not a padded idle measurement.
    """
    encoder = bench.DeterministicEncoder()
    bindings = {
        "encoder": encoder,
        "space_id": SPACE_ID,
        "generation_id": GENERATION_ID,
        "dimension": DIM,
    }
    cap_samples = max(5, int(args.queries) * 5)
    started = time.perf_counter()
    samples: list[float] = []
    first = True
    while first or (worker.poll() is None and len(samples) < cap_samples):
        first = False
        begin = time.perf_counter()
        search_assets(catalog, text=args.query_text, top_k=20, **bindings)
        samples.append((time.perf_counter() - begin) * 1000)
    overlap_ms = ms_since(started)
    summary = bench.summarize(samples)
    summary["target_p95_ms"] = 1_500
    summary["meets_target"] = bool(
        summary["p95_ms"] is not None and float(summary["p95_ms"]) <= 1_500
    )
    return {
        "samples": summary,
        "overlap_ms": overlap_ms,
        "drain_still_running_when_sampling_stopped": worker.poll() is None,
        "sample_cap": cap_samples,
        "encoder": "deterministic-stand-in (no pinned model weights)",
    }


# --------------------------------------------------------------------------- queries
def query_stage(catalog: Catalog, *, data_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    """Cold start plus warm service-level latency of one normal text search."""
    report: dict[str, Any] = {"text": args.query_text, "samples": int(args.queries)}
    encoder = bench.DeterministicEncoder()
    bindings = {
        "encoder": encoder,
        "space_id": SPACE_ID,
        "generation_id": GENERATION_ID,
        "dimension": DIM,
    }

    def once() -> tuple[float, dict[str, Any]]:
        started = time.perf_counter()
        payload = search_assets(catalog, text=args.query_text, top_k=20, **bindings)
        return (time.perf_counter() - started) * 1000, payload

    cold_ms, cold_payload = once()
    report["cold_service_ms"] = round(cold_ms, 1)
    report["cold_hits"] = len(cold_payload.get("hits", []))
    log(f"query.cold: {report['cold_service_ms']} ms")
    time.sleep(float(args.warm_seconds))
    warm: list[float] = []
    for sample in range(max(1, int(args.queries))):
        elapsed, _payload = once()
        warm.append(elapsed)
        log(f"query.service[{sample + 1}]: {round(elapsed, 1)} ms")
    summary = bench.summarize(warm)
    summary["target_p95_ms"] = 1_000
    summary["meets_target"] = bool(
        summary["p95_ms"] is not None and float(summary["p95_ms"]) <= 1_000
    )
    report["service"] = summary
    pending = cold_payload.get("pending_projection")
    report["projection"] = {
        "pending": int(pending) if isinstance(pending, int) else None,
        "hits": len(cold_payload.get("hits", [])),
    }
    if not args.no_http:
        report["http"] = http_query_stage(data_root, args=args)
    return report


def http_query_stage(data_root: Path, *, args: argparse.Namespace) -> dict[str, Any]:
    """Cold plus warm P50/P95 of the real HTTP entry point (real session and CSRF path)."""
    import httpx
    import uvicorn

    from photo_search.api.session import CSRF_HEADER
    from photo_search.api.settings import ApiSettings, generate_session_token
    from photo_search.app import create_app

    port = free_port()
    token = generate_session_token()
    settings = ApiSettings.create(
        data_root=data_root, bind_host="127.0.0.1", bind_port=port, session_token=token
    )
    application = create_app(
        settings=settings, encoder_factory=lambda: bench.DeterministicEncoder()
    )
    config = uvicorn.Config(application, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.perf_counter() + 30
        while not server.started and time.perf_counter() < deadline:
            time.sleep(0.05)
        with httpx.Client(base_url=base, timeout=60.0) as client:
            health = client.get("/api/v1/health")
            exchange = client.get("/api/v1/session", params={"token": token})
            csrf = exchange.json().get("csrf_token") if exchange.status_code == 200 else None
            headers = {"origin": base, CSRF_HEADER: str(csrf)}
            body = {
                "contract_version": "p2-contracts.v1",
                "request_id": "p4-02b-warm-1",
                "text": args.query_text,
                "space_id": SPACE_ID,
                "image_generation_id": GENERATION_ID,
                "dimension": DIM,
                "top_k": 20,
            }
            cold_started = time.perf_counter()
            first = client.post("/api/v1/search", json=body, headers=headers)
            cold_ms = (time.perf_counter() - cold_started) * 1000
            time.sleep(float(args.warm_seconds))
            samples: list[float] = []
            for index in range(max(1, int(args.queries))):
                attempt = {**body, "request_id": f"p4-02b-warm-{index + 2}"}
                started = time.perf_counter()
                response = client.post("/api/v1/search", json=attempt, headers=headers)
                samples.append((time.perf_counter() - started) * 1000)
                log(f"query.http[{index + 1}]: {round(samples[-1], 1)} ms")
                if response.status_code != 200:
                    return {
                        "http_ready": True,
                        "session_exchange": exchange.status_code,
                        "error_status": response.status_code,
                        "error_body": response.text[:400],
                    }
            summary = bench.summarize(samples)
            summary["target_p95_ms"] = 1_000
            summary["meets_target"] = bool(
                summary["p95_ms"] is not None and float(summary["p95_ms"]) <= 1_000
            )
            return {
                "http_ready": True,
                "health": health.status_code,
                "session_exchange": exchange.status_code,
                "cold_ms": round(cold_ms, 1),
                "warm": summary,
                "hits": len(first.json().get("hits", [])) if first.status_code == 200 else None,
                "encoder": "deterministic-stand-in (no pinned model weights)",
            }
    finally:
        server.should_exit = True
        thread.join(timeout=30)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# --------------------------------------------------------------- measurement-only modes
def reuse_query_scale(
    report: dict[str, Any], data_root: Path, args: argparse.Namespace
) -> dict[str, Any]:
    """Measure the query side against an **already built** generation.

    The 30 万 query cost is minutes per search (see the report), so the full three-scale run
    cannot afford a 40 + 40 sample query stage there. This mode reuses the generation a real
    run built and measures only the query side, the storage inventory and the semantic status,
    so the numbers stay real and the sample count stays honest (``--queries``).
    """
    catalog = Catalog.open(data_root=data_root)
    try:
        asset_ids = [
            int(row["asset_id"])
            for row in catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id")
        ]
        report.update(
            {
                "scale": len(asset_ids),
                "mode": "reuse-data-root",
                "data_root": str(data_root),
                "seed": int(args.seed),
            }
        )
        with stage("query") as block:
            block.update(query_stage(catalog, data_root=data_root, args=args))
        report["query"] = block
        report["assets"] = len(asset_ids)
        report["semantic_status"] = semantic_status(data_root)
        report["index"] = maintenance.semantic_index_status(catalog_db_path(data_root))["index"]
    finally:
        catalog.close()
    report["storage"] = {
        "catalog_bytes": catalog_db_path(data_root).stat().st_size
        if catalog_db_path(data_root).exists()
        else 0,
        "artifacts_bytes": directory_bytes(data_root / "artifacts"),
        "thumbnails_bytes": directory_bytes(data_root / "thumbnails"),
        "vectors_bytes": directory_bytes(vectors_dir(data_root)),
        "data_root_bytes": directory_bytes(data_root),
    }
    return report


# --------------------------------------------------------------------------- injections
def injection_fixture(work: Path, args: argparse.Namespace) -> tuple[Path, list[int]]:
    """A small synthetic library plus scan for the D-level injections."""
    label = getattr(args, "label", None)
    suffix = "" if not label else f"-{label}"
    base = work / f"injections-{args.injection_scale}{suffix}"
    library = base / "library"
    data_root = base / "data"
    if not catalog_db_path(data_root).exists():
        build_library(
            library,
            files=int(args.injection_scale),
            width=args.image_width,
            height=args.image_height,
            seed=args.seed + 7,
            buckets=64,
        )
        with CatalogService.open(data_root=data_root) as service:
            source_id = int(
                service.register_source(
                    display_name="p4-02b-inject", root_path=library
                )["source_id"]
            )
            service.run_scan(source_id)
    catalog = Catalog.open(data_root=data_root)
    try:
        assets = [
            int(row["asset_id"])
            for row in catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id")
        ]
    finally:
        catalog.close()
    return data_root, assets


def injection_interrupted_drain(
    data_root: Path, assets: Sequence[int], *, args: argparse.Namespace
) -> dict[str, Any]:
    """A drain killed mid-flight resumes into exactly one row per asset (P2-03 §7.2)."""
    catalog = Catalog.open(data_root=data_root)
    try:
        fixture = Fixture(catalog, data_root)
        # Leave at least a few assets unpublished: the crash-window and pending-projection
        # injections need a fresh artifact, and the outbox key is per projection revision.
        keep_free = 4
        limit = max(1, min(len(assets) - keep_free, 900))
        subset = [int(value) for value in assets[:limit]]
        for asset_id in subset:
            bench.publish_embedding(
                fixture,
                artifact_id=f"p4-02b-interrupt-{asset_id}",
                asset_id=asset_id,
                vector=vector_for(asset_id, seed=args.seed),
                now=now_ms(),
                input_revision=revision_for(asset_id),
            )
        pending_before = outbox_pending(catalog)
        environment = dict(os.environ)
        environment["PYTHONIOENCODING"] = "utf-8"
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "photo_search.indexing.cli",
                "drain",
                "--data-root",
                str(data_root),
                "--json",
                "--limit",
                str(len(subset)),
                "--batch-size",
                "10",
            ],
            cwd=str(REPO_ROOT),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.perf_counter() + 120
        while time.perf_counter() < deadline and outbox_confirmed(catalog) == 0:
            time.sleep(0.05)
        time.sleep(0.2)
        interrupted = process.poll() is None
        process.terminate()
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:  # pragma: no cover - a stuck child is reported
            process.kill()
        mid_confirmed = outbox_confirmed(catalog)
        mid = outbox_counts(catalog)
        resumed = maintenance.drain_semantic_outbox(catalog, limit=len(subset) + 16)
        rows = engine_rows(catalog)
        entries = int(
            catalog.read_one(
                "SELECT COUNT(*) FROM index_entries WHERE generation_id = ?", (GENERATION_ID,)
            )[0]
        )
        return {
            "assets": len(subset),
            "interrupted_while_running": bool(interrupted),
            "confirmed_mid_flight": int(mid_confirmed),
            "pending_before": pending_before,
            "mid_state": mid,
            "resumed": {
                "applied": int(resumed["applied"]),
                "batches": int(resumed["batches"]),
                "write_rejections": int(resumed["write_rejections"]["count"]),
            },
            "outbox_after": outbox_counts(catalog),
            "index_entries": entries,
            "engine_rows": len(rows),
            "no_duplicate_rows": bool(len(rows) == len(subset)),
            "all_confirmed": bool(outbox_pending(catalog) == 0),
        }
    finally:
        catalog.close()


def injection_after_lance(
    data_root: Path, assets: Sequence[int], *, args: argparse.Namespace
) -> dict[str, Any]:
    """The documented Lance-written/outbox-unconfirmed window replays idempotently."""
    catalog = Catalog.open(data_root=data_root)
    try:
        fixture = Fixture(catalog, data_root)
        target = unpublished_asset(catalog, assets)
        bench.publish_embedding(
            fixture,
            artifact_id=f"p4-02b-after-lance-{target}",
            asset_id=target,
            vector=vector_for(target, seed=args.seed + 3),
            now=now_ms(),
            input_revision=revision_for(target, "-after-lance"),
        )
        coordinator = IndexCoordinator(catalog)
        crashed = False
        try:
            coordinator.process_batch(now_ms=now_ms(), max_events=1, failpoint="after_lance")
        except Exception as error:  # noqa: BLE001 - the injected crash is the point
            crashed = type(error).__name__ == "_InjectedCrash"
        counts_after_crash = outbox_counts(catalog)
        rows_after_crash = len(engine_rows(catalog))
        resumed = maintenance.drain_semantic_outbox(catalog, limit=8)
        rows_after_resume = len(engine_rows(catalog))
        return {
            "asset_id": target,
            "injected_crash": bool(crashed),
            "outbox_after_crash": counts_after_crash,
            "engine_rows_after_crash": rows_after_crash,
            "resumed": {
                "applied": int(resumed["applied"]),
                "awaiting_reexecution": int(resumed["awaiting_reexecution"]["count"]),
            },
            "outbox_after_resume": outbox_counts(catalog),
            "engine_rows_after_resume": rows_after_resume,
            "no_duplicate_row": bool(rows_after_crash == rows_after_resume),
        }
    finally:
        catalog.close()


def injection_content_change(
    data_root: Path, assets: Sequence[int], *, args: argparse.Namespace
) -> dict[str, Any]:
    """A content change must supersede the old vector (P4-01C second-round semantics)."""
    catalog = Catalog.open(data_root=data_root)
    try:
        fixture = Fixture(catalog, data_root)
        target = int(assets[len(assets) // 2])
        # The interrupted-drain injection confirmed this asset's first vector already.
        old = vector_for(target, seed=args.seed)
        new = vector_for(target, seed=args.seed + 12)
        before = engine_row(catalog, target)
        revision = bench.bump_filter_revision(fixture, cause="p4-02b-content-change")
        bench.publish_embedding(
            fixture,
            artifact_id=f"p4-02b-change-new-{target}",
            asset_id=target,
            vector=new,
            now=now_ms(),
            input_revision=revision_for(target, "-change-new"),
        )
        maintenance.drain_semantic_outbox(catalog, limit=8)
        by_new = search_by_vector(catalog, new)
        by_old = search_by_vector(catalog, old)
        entry = catalog.read_one(
            "SELECT artifact_id, confirmation_revision FROM index_entries "
            "WHERE space_id = ? AND asset_id = ? AND generation_id = ?",
            (SPACE_ID, target, GENERATION_ID),
        )
        row = engine_row(catalog, target)
        return {
            "asset_id": target,
            "filter_revision": int(revision),
            "distance_before_to_old": None if before is None else distance(before["vector"], old),
            "new_vector_rank_1": bool(by_new and by_new[0] == target),
            "old_vector_top_hit": by_old[0] if by_old else None,
            "old_vector_still_first": bool(by_old and by_old[0] == target),
            "entry_artifact": None if entry is None else str(entry["artifact_id"]),
            "entry_confirmation_revision": None
            if entry is None
            else int(entry["confirmation_revision"]),
            "engine_vector_distance_to_new": None
            if row is None
            else distance(row["vector"], new),
            "engine_vector_distance_to_old": None
            if row is None
            else distance(row["vector"], old),
        }
    finally:
        catalog.close()


def injection_offline(
    data_root: Path, assets: Sequence[int], *, args: argparse.Namespace
) -> dict[str, Any]:
    """An offline location must not keep a searchable ghost vector."""
    catalog = Catalog.open(data_root=data_root)
    try:
        target = int(assets[3])
        # The interrupted-drain injection published and confirmed this asset's vector.
        vector = vector_for(target, seed=args.seed)
        before_any = search_by_vector(catalog, vector)
        before_present = search_by_vector(catalog, vector, availability="present")
        with catalog.write() as connection:
            connection.execute(
                "UPDATE file_locations SET availability = 'MISSING' WHERE asset_id = ?",
                (target,),
            )
        after_any = search_by_vector(catalog, vector)
        after_present = search_by_vector(catalog, vector, availability="present")
        maintenance.drain_semantic_outbox(catalog, limit=8)
        maintained_present = search_by_vector(catalog, vector, availability="present")
        row = engine_row(catalog, target)
        return {
            "asset_id": target,
            "hits_before_offline_any": before_any,
            "hits_before_offline_present": before_present,
            "hits_after_offline_any": after_any,
            "hits_after_offline_present": after_present,
            "hits_after_maintenance_present": maintained_present,
            "engine_row_still_present": row is not None,
            "engine_row_visible": None if row is None else bool(row["visible"]),
            "no_ghost_hit": bool(
                target not in after_present and target not in maintained_present
            ),
        }
    finally:
        catalog.close()


def injection_pending_vector(
    data_root: Path, assets: Sequence[int], *, args: argparse.Namespace
) -> dict[str, Any]:
    """The confirmation window of one new vector, before and after the production drain.

    The product keeps no ANN index (P4-01B B1), so there is no separate "index maintenance"
    step to wait for: a *confirmed* row is part of the exact scanned generation immediately.
    What this injection measures is therefore the remaining window - an artifact that is
    published but whose outbox event is not confirmed yet is deliberately not part of the
    semantic generation (P2-03 §7.2), while the asset itself stays browsable and reachable
    through the text channel.
    """
    catalog = Catalog.open(data_root=data_root)
    try:
        fixture = Fixture(catalog, data_root)
        target = unpublished_asset(catalog, assets)
        vector = vector_for(target, seed=args.seed + 31)
        bench.publish_embedding(
            fixture,
            artifact_id=f"p4-02b-pending-{target}",
            asset_id=target,
            vector=vector,
            now=now_ms(),
            input_revision=revision_for(target, "-pending"),
        )
        pending = outbox_pending(catalog)
        before_hits = search_by_vector(catalog, vector)
        before_text = search_assets(
            catalog,
            text=args.query_text,
            encoder=bench.DeterministicEncoder(),
            space_id=SPACE_ID,
            generation_id=GENERATION_ID,
            dimension=DIM,
            top_k=20,
        )
        confirmed = maintenance.drain_semantic_outbox(catalog, limit=8)
        after_hits = search_by_vector(catalog, vector)
        payload = search_assets(
            catalog,
            text=args.query_text,
            encoder=bench.DeterministicEncoder(),
            space_id=SPACE_ID,
            generation_id=GENERATION_ID,
            dimension=DIM,
            top_k=20,
        )
        return {
            "asset_id": target,
            "pending_events": pending,
            "hits_before_confirmation": before_hits[:3],
            "hit_before_confirmation": bool(before_hits and before_hits[0] == target),
            "fused_hits_before_confirmation": len(before_text.get("hits", [])),
            "confirmed_events": int(confirmed["applied"]),
            "hits_after_confirmation": after_hits[:3],
            "hit_after_confirmation": bool(after_hits and after_hits[0] == target),
            "fused_hits_after_confirmation": len(payload.get("hits", [])),
        }
    finally:
        catalog.close()


def search_by_vector(
    catalog: Catalog, vector: Sequence[float], *, availability: str = "any"
) -> list[int]:
    payload = search_assets(
        catalog,
        query_vector=[float(value) for value in vector],
        space_id=SPACE_ID,
        generation_id=GENERATION_ID,
        dimension=DIM,
        top_k=5,
        availability=str(availability),
    )
    return [int(hit["asset_id"]) for hit in payload.get("hits", [])]


def engine_rows(catalog: Catalog) -> list[dict[str, Any]]:
    row = catalog.read_one(
        "SELECT dimension FROM index_generations WHERE generation_id = ?", (GENERATION_ID,)
    )
    if row is None:
        return []
    return list(
        IndexCoordinator(catalog).index.rows(
            generation_id=GENERATION_ID, dimension=int(row["dimension"])
        )
    )


def engine_row(catalog: Catalog, asset_id: int) -> dict[str, Any] | None:
    for row in engine_rows(catalog):
        if int(row["asset_id"]) == int(asset_id):
            return row
    return None


def distance(vector: Sequence[float], other: Sequence[float]) -> float:
    """Cosine distance of two L2-normalised stand-in vectors."""
    left = np.asarray([float(value) for value in vector], dtype=np.float32)
    right = np.asarray([float(value) for value in other], dtype=np.float32)
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right)) or 1.0
    return round(float(1.0 - float(np.dot(left, right)) / denominator), 6)


def outbox_counts(catalog: Catalog) -> dict[str, int]:
    return {
        str(row["state"]): int(row["total"])
        for row in catalog.read_all(
            "SELECT state, COUNT(*) AS total FROM index_outbox GROUP BY state"
        )
    }


def outbox_pending(catalog: Catalog) -> int:
    return int(
        catalog.read_one("SELECT COUNT(*) FROM index_outbox WHERE state <> 'CONFIRMED'")[0]
    )


def outbox_confirmed(catalog: Catalog) -> int:
    return int(
        catalog.read_one("SELECT COUNT(*) FROM index_outbox WHERE state = 'CONFIRMED'")[0]
    )


def unpublished_asset(catalog: Catalog, assets: Sequence[int]) -> int:
    """The newest asset that has no job yet, so an injection cannot collide on the outbox key."""
    used = {
        int(row["subject_id"])
        for row in catalog.read_all(
            "SELECT DISTINCT subject_id FROM jobs WHERE subject_type = 'asset'"
        )
    }
    for asset_id in reversed([int(value) for value in assets]):
        if asset_id not in used:
            return asset_id
    return int(assets[-1])


def injections(work: Path, args: argparse.Namespace) -> dict[str, Any]:
    data_root, assets = injection_fixture(work, args)
    report: dict[str, Any] = {
        "scale": len(assets),
        "data_root": str(data_root),
        "encoder": "deterministic-stand-in (no pinned model weights)",
    }
    with stage("injection.interrupted_drain") as block:
        block.update(injection_interrupted_drain(data_root, assets, args=args))
    report["interrupted_drain"] = block
    with stage("injection.after_lance") as block:
        block.update(injection_after_lance(data_root, assets, args=args))
    report["after_lance"] = block
    with stage("injection.content_change") as block:
        block.update(injection_content_change(data_root, assets, args=args))
    report["content_change"] = block
    with stage("injection.offline") as block:
        block.update(injection_offline(data_root, assets, args=args))
    report["offline"] = block
    with stage("injection.pending_vector") as block:
        block.update(injection_pending_vector(data_root, assets, args=args))
    report["pending_vector"] = block
    return report


# --------------------------------------------------------------------------- cli
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P4-02B capacity validation")
    parser.add_argument("--scales", default="10000,100000,300000")
    parser.add_argument("--work-dir", type=Path, default=Path("tmp") / "p402b-capacity")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--label",
        default=None,
        help=(
            "suffix for the per-scale tree name inside --work-dir (e.g. 'wave'/'burst'), so "
            "two shapes of the same scale can be kept side by side (P4-02D)"
        ),
    )
    parser.add_argument("--image-size", default=DEFAULT_IMAGE_SIZE)
    parser.add_argument("--seed", type=int, default=20260924)
    parser.add_argument("--queries", type=int, default=DEFAULT_QUERIES)
    parser.add_argument("--query-text", default=DEFAULT_QUERY_TEXT)
    parser.add_argument("--warm-seconds", type=float, default=DEFAULT_WARM_SECONDS)
    parser.add_argument("--drain-batch", type=int, default=DRAIN_BATCH)
    parser.add_argument(
        "--publish-wave",
        type=int,
        default=5_000,
        help="assets published and drained per bounded wave (0/1 = one burst)",
    )
    parser.add_argument(
        "--burst-sample",
        type=int,
        default=20_000,
        help="assets of the deliberately unbounded first burst (0 = none)",
    )
    parser.add_argument("--injection-scale", type=int, default=DEFAULT_INJECTION_SCALE)
    parser.add_argument("--keep", action="store_true", help="keep the throw-away trees")
    parser.add_argument("--skip-thumbnails", action="store_true")
    parser.add_argument("--no-semantic", dest="semantic", action="store_false")
    parser.add_argument("--no-queries", dest="queries", action="store_false")
    parser.add_argument("--no-http", action="store_true")
    parser.add_argument("--skip-injections", action="store_true")
    parser.add_argument(
        "--reuse-data-root",
        type=Path,
        default=None,
        help="measure only the query side of an already built generation at this data root",
    )
    parser.add_argument(
        "--injections-only",
        action="store_true",
        help="skip the capacity scales and run the D-level injections alone",
    )
    parser.add_argument("--pilot", action="store_true", help="small scales, all stages, keep trees")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.pilot:
        args.scales = "800,2000"
        args.queries = min(int(args.queries), 8)
        args.keep = True
        args.injection_scale = min(int(args.injection_scale), 400)
    width, height = (int(part) for part in str(args.image_size).lower().split("x", 1))
    args.image_width = width
    args.image_height = height
    return args


def write_output(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "status": "ok",
        "task": "P4-02B",
        "started_at_ms": now_ms(),
        "work_dir": str(work),
        "scales": [int(part) for part in str(args.scales).split(",") if part.strip()],
        "machine": bench.machine_facts(),
        "blocked": [
            "real-model throughput / VRAM peaks / GTX 1660 minimum configuration: no pinned "
            "model weights are installed and this machine has no GTX 1660",
            "face and OCR stage times: no pinned face/OCR weights are installed",
            "section 12.3 ANN Recall 0.95: the product does not create an ANN index (P4-01B B1)",
        ],
        "measurements": [],
    }
    write_output(args.output, report)
    if args.injections_only:
        log("injections: begin")
        report["injections"] = injections(work, args)
        write_output(args.output, report)
        report["finished_at_ms"] = now_ms()
        write_output(args.output, report)
        print(json.dumps({"status": "ok", "injections": report["injections"]}, ensure_ascii=True))
        return 0
    if args.reuse_data_root is not None:
        measurement: dict[str, Any] = {
            "scale": int(report["scales"][0]) if report["scales"] else 0,
            "status": "ok",
        }
        report["measurements"].append(measurement)
        try:
            reuse_query_scale(measurement, Path(args.reuse_data_root), args)
        except Exception as error:  # noqa: BLE001 - report the failure honestly
            measurement["status"] = "error"
            measurement["error"] = f"{type(error).__name__}: {error}"
            log(f"reuse query: {measurement['error']}")
        report["finished_at_ms"] = now_ms()
        write_output(args.output, report)
        print(json.dumps(report, ensure_ascii=True))
        return 0
    for scale in report["scales"]:
        log(f"scale {scale}: begin")
        measurement: dict[str, Any] = {"scale": int(scale), "status": "ok"}
        report["measurements"].append(measurement)
        try:
            capacity_scale(measurement, work, int(scale), args)
        except Exception as error:  # noqa: BLE001 - one failed scale must not lose the rest
            measurement["status"] = "error"
            measurement["error"] = f"{type(error).__name__}: {error}"
            log(f"scale {scale}: {measurement['error']}")
        write_output(args.output, report)
    if not args.skip_injections:
        log("injections: begin")
        try:
            report["injections"] = injections(work, args)
        except Exception as error:  # noqa: BLE001 - report the injection failure honestly
            report["injections"] = {
                "status": "error",
                "error": f"{type(error).__name__}: {error}",
            }
            log(f"injections: {report['injections']['error']}")
        write_output(args.output, report)
    report["finished_at_ms"] = now_ms()
    write_output(args.output, report)
    if args.json:
        print(json.dumps(report, ensure_ascii=True))
        return 0
    for measurement in report["measurements"]:
        if measurement.get("status") != "ok":
            print(f"{measurement['scale']:>8}  {measurement.get('error')}")
            continue
        scan = measurement["scan"]
        semantic = measurement.get("semantic", {})
        contention = semantic.get("contention", {}).get("samples", {})
        print(
            f"{measurement['scale']:>8}  scan {scan['scan_ms']:>9} ms "
            f"({scan['assets_per_second']:>8} img/s)  "
            f"catalog {measurement['catalog']['catch_up_ms']:>9} ms  "
            f"execute {semantic.get('execute', {}).get('ms', 0):>9} ms  "
            f"drain {semantic.get('drain', {}).get('ms', 0):>9} ms  "
            f"contention p95 {contention.get('p95_ms')} ms "
            f"({contention.get('count', 0)} samples)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
