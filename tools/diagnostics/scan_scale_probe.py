"""P1-02 scale probe: streaming scan cost and memory on a synthetic fixture tree.

The probe only ever writes inside a temporary directory it creates itself (library
fixtures plus a throwaway data root) and never reads a real photo library. It reports, as
one JSON document:

* wall time of the execution claim (including ``reset_execution_coverage``), of the
  streamed enumeration and of the content-confirmation pass;
* peak working set of the process, so memory can be compared across file counts;
* the scan's own counters (enumerated files, confirmed files, stability waits).

Usage::

    uv run python tools/diagnostics/scan_scale_probe.py --files 20000 --json
    uv run python tools/diagnostics/scan_scale_probe.py --files 5000 --files 20000 --json
    uv run python tools/diagnostics/scan_scale_probe.py --files 20000 --recovery --json

``--recovery`` additionally measures a second execution of the *same* logical scan_run
(the P1-01 follow-up about ``reset_execution_coverage`` at realistic catalog sizes): the
first execution is interrupted after one confirmed file, the lease is allowed to expire
and the resume re-enumerates the whole library before it replaces the execution coverage
markers of every position.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from photo_search.ingestion.scanner import ScanInterrupted
from photo_search.ingestion.service import CatalogService

#: Content size per synthetic file; small enough to keep fixture creation cheap while
#: still giving the hasher real work.
DEFAULT_FILE_BYTES = 4_096


def process_memory_bytes() -> int:
    """Peak working set of this process.

    On Windows this is ``PeakWorkingSetSize`` from ``GetProcessMemoryInfo``, which is what
    a user sees in Task Manager. Elsewhere it falls back to the reported max RSS.
    """
    if sys.platform == "win32":
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


def build_fixture(root: Path, *, files: int, file_bytes: int, directories: int) -> int:
    """Create ``files`` synthetic files spread over ``directories`` subdirectories."""
    root.mkdir(parents=True, exist_ok=True)
    per_directory = max((files + directories - 1) // directories, 1)
    written = 0
    for index in range(files):
        bucket = root / f"bucket-{index // per_directory:04d}"
        bucket.mkdir(exist_ok=True)
        payload = (f"photo-{index:08d}-".encode("ascii") * 64)[:file_bytes]
        (bucket / f"photo-{index:08d}.jpg").write_bytes(payload)
        written += 1
    return written


def measure(
    *,
    files: int,
    file_bytes: int,
    directories: int,
    keep: bool,
    workspace: Path | None,
    recovery: bool = False,
) -> dict[str, Any]:
    base = Path(tempfile.mkdtemp(prefix="photo-search-scale-", dir=workspace))
    try:
        library = base / "library"
        data_root = base / "data"
        started = time.perf_counter()
        written = build_fixture(
            library, files=files, file_bytes=file_bytes, directories=directories
        )
        fixture_ms = int(round((time.perf_counter() - started) * 1000))

        with CatalogService.open(data_root=data_root, lease_seconds=1) as service:
            source_id = service.register_source(display_name="scale", root_path=library)["source_id"]
            baseline_bytes = process_memory_bytes()
            scan_started = time.perf_counter()
            interrupted_ms: int | None = None
            if recovery:
                try:
                    service.run_scan(source_id, interrupt_after=1)
                except ScanInterrupted:
                    pass
                interrupted_ms = int(round((time.perf_counter() - scan_started) * 1000))
                # The killed execution keeps its lease until it expires (ADR-0002 §5.3).
                time.sleep(1.25)
            outcome = service.run_scan(source_id)
            scan_ms = int(round((time.perf_counter() - scan_started) * 1000))
            peak_bytes = process_memory_bytes()
            counts = outcome["counts"]
            report = {
                "files": written,
                "file_bytes": file_bytes,
                "directories": directories,
                "fixture_ms": fixture_ms,
                "scan_ms": scan_ms,
                "enumerate_ms": outcome["enumeration_ms"],
                "process_ms": outcome["processing_ms"],
                "claim_and_reset_coverage_ms": outcome["prepare_ms"],
                "peak_working_set_bytes": peak_bytes,
                "working_set_before_scan_bytes": baseline_bytes,
                "working_set_delta_bytes": peak_bytes - baseline_bytes,
                "bytes_per_file_delta": round((peak_bytes - baseline_bytes) / max(written, 1), 2),
                "files_per_second": round(written / max(scan_ms, 1) * 1000, 1),
                "enumerated": outcome["enumerated"],
                "locations_present": counts["locations_present"],
                "assets_total": counts["assets_total"],
                "locations_missing": counts["locations_missing"],
                "stability_waits": outcome["stability_waits"],
                "stability_wait_ms": outcome["stability_wait_ms"],
                "state": outcome["state"],
                "kept": keep,
                "temp_root": str(base) if keep else None,
            }
            if recovery:
                report["recovery"] = True
                report["interrupted_scan_ms"] = interrupted_ms
                report["resume_scan_ms"] = scan_ms - int(interrupted_ms or 0) - 1250
                report["resume_prepare_ms"] = outcome["prepare_ms"]
        return report
    finally:
        if not keep:
            shutil.rmtree(base, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, action="append", default=None)
    parser.add_argument("--file-bytes", type=int, default=DEFAULT_FILE_BYTES)
    parser.add_argument("--directories", type=int, default=64)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument(
        "--recovery",
        action="store_true",
        help="also measure a resume execution of the same scan_run (coverage reset cost)",
    )
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    sizes = args.files or [5_000]
    reports = [
        measure(
            files=size,
            file_bytes=args.file_bytes,
            directories=args.directories,
            keep=args.keep,
            workspace=args.workspace,
            recovery=args.recovery,
        )
        for size in sizes
    ]
    if args.json:
        print(json.dumps({"status": "ok", "reports": reports}, ensure_ascii=False))
    else:
        for report in reports:
            print(
                f"{report['files']:>7} files  scan {report['scan_ms']:>6} ms  "
                f"enum {report['enumerate_ms']:>6} ms  process {report['process_ms']:>6} ms  "
                f"claim+reset {report['claim_and_reset_coverage_ms']:>4} ms  "
                f"peak {report['peak_working_set_bytes'] / 1048576:>8.1f} MiB  "
                f"delta {report['working_set_delta_bytes'] / 1048576:>6.1f} MiB"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
