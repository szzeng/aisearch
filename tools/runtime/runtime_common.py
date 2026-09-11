"""Shared helpers for the P0-06 GPU/runtime probes.

The probes intentionally reuse the P0-03/P0-04 preparation helpers so the frozen model
directories, manifests and hashing rules stay in one place.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PREPARE_MODELS_DIR = Path(__file__).resolve().parents[1] / "prepare_models"
if str(PREPARE_MODELS_DIR) not in sys.path:
    sys.path.insert(0, str(PREPARE_MODELS_DIR))

# The probes import the frozen-artifact helpers through this module, and this module owns
# the single ``sys.path`` bootstrap, so the probe scripts never import the preparation
# modules directly (that would depend on a fragile import order).
from chinese_clip_common import (  # noqa: E402
    default_model_dir,
    l2_normalize,
    load_json,
    write_json,
)
from face_ocr_common import (  # noqa: E402
    FACE_MODEL_FILES,
    RAPIDOCR_MODEL_FILES,
    default_model_root,
    download_cache_dir,
    face_model_dir,
    ocr_model_dir,
    sha256_file,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

NVIDIA_SMI = "nvidia-smi"


def nvidia_smi_query(query: str, timeout: float = 60.0) -> list[str]:
    """Return ``nvidia-smi`` CSV rows without a header, or an empty list on failure."""
    completed = subprocess.run(
        [NVIDIA_SMI, f"--query-{query}", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        return []
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def gpu_memory_used_mib() -> int | None:
    rows = nvidia_smi_query("gpu=memory.used")
    if not rows:
        return None
    try:
        return int(float(rows[0]))
    except ValueError:
        return None


def gpu_hardware_summary() -> dict[str, Any]:
    """Read-only hardware facts recorded with every GPU validation run."""
    fields = [
        "name",
        "driver_version",
        "memory.total",
        "memory.used",
        "temperature.gpu",
        "pstate",
        "compute_cap",
    ]
    rows = nvidia_smi_query("gpu=" + ",".join(fields))
    summary: dict[str, Any] = {"nvidia_smi": {}}
    if rows:
        values = [value.strip() for value in rows[0].split(",")]
        if len(values) == len(fields):
            summary["nvidia_smi"] = dict(zip(fields, values, strict=True))
    summary["cuda_umd_version"] = _cuda_umd_version()
    return summary


def _cuda_umd_version() -> str | None:
    try:
        completed = subprocess.run(
            [NVIDIA_SMI], capture_output=True, text=True, timeout=60.0, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"CUDA UMD Version:\s*([0-9][0-9.]*)", completed.stdout)
    return match.group(1) if match else None


def process_gpu_memory_mib(pid: int | None = None) -> int | None:
    """Per-process GPU memory as reported by the driver, when the driver exposes it."""
    target = pid if pid is not None else os.getpid()
    for row in nvidia_smi_query("compute-apps=pid,used_gpu_memory"):
        parts = [part.strip() for part in row.split(",")]
        if len(parts) < 2 or parts[0] != str(target):
            continue
        try:
            return int(float(parts[1]))
        except ValueError:
            return None
    return None


def process_gpu_apps() -> list[dict[str, Any]]:
    apps: list[dict[str, Any]] = []
    for row in nvidia_smi_query("compute-apps=pid,process_name,used_gpu_memory"):
        parts = [part.strip() for part in row.split(",")]
        if len(parts) < 3:
            continue
        try:
            used: int | None = int(float(parts[-1]))
        except ValueError:
            used = None
        apps.append({"pid": parts[0], "name": ",".join(parts[1:-1]), "used_memory_mib": used})
    return apps


class _ProcessMemoryCountersEx(ctypes.Structure):
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


def process_working_set_mib() -> float | None:
    """Current process working set; ``None`` off Windows."""
    if os.name != "nt":
        return None
    counters = _ProcessMemoryCountersEx()
    counters.cb = ctypes.sizeof(_ProcessMemoryCountersEx)
    kernel32 = ctypes.WinDLL("kernel32.dll")
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    # GetProcessMemoryInfo lives in psapi; kernel32 exports the K32 alias since Windows 7.
    getter = getattr(kernel32, "K32GetProcessMemoryInfo", None)
    if getter is None:
        getter = ctypes.WinDLL("psapi.dll").GetProcessMemoryInfo
    getter.restype = ctypes.c_int
    getter.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessMemoryCountersEx), ctypes.c_ulong]
    handle = kernel32.GetCurrentProcess()
    ok = getter(ctypes.c_void_p(handle), ctypes.byref(counters), counters.cb)
    if not ok:
        return None
    return round(counters.WorkingSetSize / (1024 * 1024), 1)


def timing_summary(samples: list[float]) -> dict[str, Any]:
    """Report a stable latency distribution instead of a single best case."""
    if not samples:
        return {"count": 0}
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
    return {
        "count": len(ordered),
        "min_ms": round(min(ordered) * 1000, 2),
        "mean_ms": round(statistics.fmean(ordered) * 1000, 2),
        "p50_ms": round(statistics.median(ordered) * 1000, 2),
        "p95_ms": round(ordered[index] * 1000, 2),
        "max_ms": round(max(ordered) * 1000, 2),
        "stdev_ms": round(statistics.stdev(ordered) * 1000, 2) if len(ordered) > 1 else 0.0,
    }


def measure(callable_: Any, repeats: int, warmup: int = 2) -> dict[str, Any]:
    """Time ``warmup`` + ``repeats`` calls, returning only the steady-state samples."""
    for _ in range(warmup):
        callable_()
    samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        callable_()
        samples.append(time.perf_counter() - started)
    return timing_summary(samples)


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


__all__ = [
    "FACE_MODEL_FILES",
    "RAPIDOCR_MODEL_FILES",
    "REPOSITORY_ROOT",
    "default_model_dir",
    "default_model_root",
    "download_cache_dir",
    "face_model_dir",
    "l2_normalize",
    "gpu_hardware_summary",
    "gpu_memory_used_mib",
    "load_json",
    "measure",
    "ocr_model_dir",
    "process_gpu_apps",
    "process_gpu_memory_mib",
    "process_working_set_mib",
    "read_json",
    "sha256_file",
    "timing_summary",
    "write_json",
]
