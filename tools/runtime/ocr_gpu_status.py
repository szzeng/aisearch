"""P0-06 stage A8: record RapidOCR's actual execution provider and minimal latency.

RapidOCR 1.4.4 keeps ``CPUExecutionProvider`` in its own provider list and only decides
whether to *insert* CUDA first, so this probe records the real session providers and the
engine's internal ``use_cuda`` flags instead of assuming "GPU enabled" worked. Whether OCR
should become a hard CUDA path is a P0-06 measurement question, not a default assumption.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime
from rapidocr_onnxruntime import RapidOCR
from runtime_common import (
    RAPIDOCR_MODEL_FILES,
    REPOSITORY_ROOT,
    default_model_root,
    gpu_hardware_summary,
    gpu_memory_used_mib,
    measure,
    ocr_model_dir,
    process_gpu_memory_mib,
    read_json,
    write_json,
)

# Imported after runtime_common so the preparation-module path is already registered.
from verify_face_ocr import OCR_FIXTURES, write_ocr_fixtures

from photo_search.runtime.device import preload_runtime_dlls

DEFAULT_RESULT = REPOSITORY_ROOT / "docs/reports/P0-06-rapidocr-gpu-status.json"
P0_04_RECORD = REPOSITORY_ROOT / "tools/prepare_models/manifests/face-ocr-validation.json"


def build_engine(ocr_dir: Path, use_cuda: bool) -> RapidOCR:
    kwargs: dict[str, Any] = {
        "det_model_path": str(ocr_dir / RAPIDOCR_MODEL_FILES[0]),
        "cls_model_path": str(ocr_dir / RAPIDOCR_MODEL_FILES[1]),
        "rec_model_path": str(ocr_dir / RAPIDOCR_MODEL_FILES[2]),
    }
    if use_cuda:
        kwargs.update(det_use_cuda=True, cls_use_cuda=True, rec_use_cuda=True)
    return RapidOCR(**kwargs)


def session_facts(engine: RapidOCR) -> dict[str, Any]:
    """Actual providers of the three OCR sessions plus the engine's own CUDA flags."""
    facts: dict[str, Any] = {}
    for name in ("text_det", "text_cls", "text_rec"):
        module = getattr(engine, name, None)
        # detection/classification hold ``infer``; recognition holds ``session``
        ort_session = getattr(module, "session", None) or getattr(module, "infer", None)
        inner = getattr(ort_session, "session", None)
        entry: dict[str, Any] = {}
        if ort_session is not None:
            entry["engine_use_cuda"] = bool(getattr(ort_session, "use_cuda", False))
        if inner is not None:
            entry["providers"] = list(inner.get_providers())
            entry["provider_options"] = [
                dict(options) for options in getattr(inner, "_providers", []) if isinstance(options, dict)
            ]
        if entry:
            facts[name] = entry
    return facts


def run_fixtures(engine: RapidOCR, fixtures: dict[str, Path]) -> list[dict[str, Any]]:
    records = []
    for name, path in fixtures.items():
        raw, elapsed = engine(str(path))
        boxes = [
            {"text": item[1], "score": float(item[2])} for item in (raw or [])
        ]
        records.append(
            {
                "fixture": name,
                "box_count": len(boxes),
                "text": " ".join(box["text"] for box in boxes),
                "boxes": boxes,
                "elapsed_seconds": float(np.sum(elapsed)) if elapsed is not None else None,
            }
        )
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-root", type=Path, default=default_model_root())
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--preload-dlls",
        default="yes",
        choices=["yes", "no"],
        help=(
            "preload the CUDA/cuDNN DLLs before building the CUDA-enabled engine; the "
            "product worker preloads them, and without it ONNX Runtime cannot load the "
            "CUDA provider at all"
        ),
    )
    args = parser.parse_args()

    steps: list[str] = []
    result: dict[str, Any] = {
        "status": "running",
        "task": "P0-06",
        "stage": "A8",
        "engine": "rapidocr-onnxruntime",
        "runtime": onnxruntime.__version__,
        "available_providers": onnxruntime.get_available_providers(),
        "hardware": gpu_hardware_summary(),
        "gpu_memory_used_before_load_mib": gpu_memory_used_mib(),
        "fixture_spec": OCR_FIXTURES,
        "note": (
            "RapidOCR 1.4.4 keeps CPUExecutionProvider in its own provider list; the recorded "
            "session providers are the authoritative evidence of what it actually used"
        ),
    }
    try:
        root = args.model_root.resolve()
        ocr_dir = ocr_model_dir(root)
        fixtures = write_ocr_fixtures(root / "cache" / "p0-06-ocr-fixtures")
        gpu_fixtures = {name: fixtures[name] for name in ("cjk", "ascii")}

        if args.preload_dlls == "yes":
            result["preload"] = preload_runtime_dlls()
            steps.append("preload_dlls")
        gpu_engine = build_engine(ocr_dir, use_cuda=True)
        result["gpu"] = {
            "session_facts": session_facts(gpu_engine),
            "records": run_fixtures(gpu_engine, gpu_fixtures),
        }
        steps.append("gpu_run")
        sample = gpu_fixtures["cjk"]
        result["gpu"]["latency"] = measure(lambda: gpu_engine(str(sample)), args.repeats, warmup=1)
        result["gpu"]["gpu_memory_used_peak_mib"] = gpu_memory_used_mib()
        result["gpu"]["process_gpu_memory_mib"] = process_gpu_memory_mib()

        cpu_engine = build_engine(ocr_dir, use_cuda=False)
        result["cpu"] = {
            "session_facts": session_facts(cpu_engine),
            "records": run_fixtures(cpu_engine, gpu_fixtures),
        }
        steps.append("cpu_run")
        result["cpu"]["latency"] = measure(lambda: cpu_engine(str(sample)), args.repeats, warmup=1)

        providers = {
            provider
            for entry in result["gpu"]["session_facts"].values()
            for provider in entry.get("providers", [])
        }
        result["gpu_active"] = "CUDAExecutionProvider" in providers
        record = read_json(P0_04_RECORD)
        if record:
            recorded = {
                entry["fixture"]: entry["text"] for entry in record["ocr"]["records"]
            }
            result["p0_04_record"] = {
                "path": str(P0_04_RECORD),
                "recorded_text": recorded,
                "gpu_text_matches_record": {
                    entry["fixture"]: recorded.get(entry["fixture"]) == entry["text"]
                    for entry in result["gpu"]["records"]
                },
                "cpu_text_matches_record": {
                    entry["fixture"]: recorded.get(entry["fixture"]) == entry["text"]
                    for entry in result["cpu"]["records"]
                },
            }
        result["status"] = "pass"
        result["elapsed_seconds"] = round(time.perf_counter(), 1)
        steps.append("result")
        write_json(args.result.resolve(), result)
        print(
            {
                "status": "pass",
                "steps": [*steps, "written"],
                "result": str(args.result.resolve()),
                "gpu_session_facts": result["gpu"]["session_facts"],
                "gpu_text": [entry["text"] for entry in result["gpu"]["records"]],
                "cpu_text": [entry["text"] for entry in result["cpu"]["records"]],
                "gpu_latency_p50_ms": result["gpu"]["latency"].get("p50_ms"),
                "cpu_latency_p50_ms": result["cpu"]["latency"].get("p50_ms"),
                "gpu_active": result["gpu_active"],
            }
        )
        return 0
    except Exception as error:
        result["status"] = "fail"
        result["stage"] = steps[-1] if steps else "setup"
        result["exception_type"] = type(error).__name__
        result["message"] = str(error)
        result["completed_steps"] = steps
        write_json(args.result.resolve(), result)
        print(
            {
                "status": "fail",
                "stage": result["stage"],
                "exception_type": result["exception_type"],
                "message": result["message"],
                "completed_steps": steps,
            },
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
