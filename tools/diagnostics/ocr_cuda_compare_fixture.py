"""Warm CPU/CUDA comparison of the pinned OCR path using synthetic fixtures only."""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

from ocr_smoke_fixture import build_library, install_pinned_weights
from p4_02y_l_workdir import new_work_dir, prepare_work_dir

from photo_search.ocr.engine import OcrEngineCache, recognize_image
from photo_search.ocr.processor import _decode, ocr_processor_spec
from photo_search.runtime.device import runtime_capabilities


def _gpu_snapshot() -> str | None:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def _document_payload(document: Any) -> dict[str, Any]:
    return {
        "original_text": document.original_text,
        "regions": [region.as_dict() for region in document.regions],
        "slice_count": document.slice_count,
        "working_size": list(document.working_size),
    }


def _run_device(
    *, device: str, data_root: Path, images: dict[str, Any], rounds: int
) -> dict[str, Any]:
    cache = OcrEngineCache()
    started = time.perf_counter()
    engine = cache.engine(data_root, resolved_device=device, orientation_retry=True)
    profile = ocr_processor_spec(device)
    startup_ms = (time.perf_counter() - started) * 1000.0
    warmup_ms: dict[str, float] = {}
    for name, image in images.items():
        warm_started = time.perf_counter()
        recognize_image(engine, image)
        warmup_ms[name] = (time.perf_counter() - warm_started) * 1000.0

    by_image: dict[str, list[float]] = {name: [] for name in images}
    round_totals: list[float] = []
    payloads: dict[str, dict[str, Any]] = {}
    memory_before = _gpu_snapshot()
    for _ in range(rounds):
        round_started = time.perf_counter()
        for name, image in images.items():
            image_started = time.perf_counter()
            document = recognize_image(engine, image)
            elapsed_ms = (time.perf_counter() - image_started) * 1000.0
            by_image[name].append(elapsed_ms)
            payloads[name] = _document_payload(document)
        round_totals.append((time.perf_counter() - round_started) * 1000.0)
    memory_after = _gpu_snapshot()
    return {
        "processor_id": profile.processor_id,
        "spec_revision": profile.spec_revision,
        "config_hash": profile.config_hash,
        "orientation_retry": engine.orientation_retry,
        "resolved_device": engine.resolved_device,
        "runtime_provider": engine.runtime_provider,
        "session_providers": {key: list(value) for key, value in engine.session_providers.items()},
        "precision": engine.precision,
        "startup_ms": round(startup_ms, 2),
        "warmup_ms": {key: round(value, 2) for key, value in warmup_ms.items()},
        "round_total_ms": [round(value, 2) for value in round_totals],
        "median_round_total_ms": round(statistics.median(round_totals), 2),
        "image_ms": {
            key: [round(value, 2) for value in values] for key, values in by_image.items()
        },
        "median_image_ms": {
            key: round(statistics.median(values), 2) for key, values in by_image.items()
        },
        "memory_gpu_before": memory_before,
        "memory_gpu_after": memory_after,
        "documents": payloads,
    }


def _compare(cpu: dict[str, Any], gpu: dict[str, Any]) -> dict[str, Any]:
    per_image: dict[str, Any] = {}
    equivalent = True
    for name, cpu_document in cpu["documents"].items():
        gpu_document = gpu["documents"][name]
        cpu_regions = cpu_document["regions"]
        gpu_regions = gpu_document["regions"]
        same_text = cpu_document["original_text"] == gpu_document["original_text"]
        same_count = len(cpu_regions) == len(gpu_regions)
        max_coordinate_delta = 0.0
        max_confidence_delta = 0.0
        if same_count:
            for left, right in zip(cpu_regions, gpu_regions):
                if left["text"] != right["text"]:
                    same_text = False
                for key in ("box", "polygon"):
                    left_values = [value for point in left[key] for value in point] if key == "polygon" else left[key]
                    right_values = [value for point in right[key] for value in point] if key == "polygon" else right[key]
                    if len(left_values) != len(right_values):
                        max_coordinate_delta = float("inf")
                    else:
                        max_coordinate_delta = max(
                            max_coordinate_delta,
                            *(abs(float(a) - float(b)) for a, b in zip(left_values, right_values)),
                        )
                max_confidence_delta = max(
                    max_confidence_delta,
                    abs(float(left["confidence"]) - float(right["confidence"])),
                )
        accepted = (
            same_text
            and same_count
            and max_coordinate_delta <= 1.0
            and max_confidence_delta <= 0.03
        )
        equivalent = equivalent and accepted
        per_image[name] = {
            "equivalent": accepted,
            "same_text": same_text,
            "same_region_count": same_count,
            "max_coordinate_delta_px": max_coordinate_delta,
            "max_confidence_delta": round(max_confidence_delta, 8),
        }
    cpu_ms = float(cpu["median_round_total_ms"])
    gpu_ms = float(gpu["median_round_total_ms"])
    return {
        "equivalent": equivalent,
        "tolerance": {"coordinates_px": 1.0, "confidence": 0.03, "text": "exact"},
        "per_image": per_image,
        "cpu_to_gpu_speedup": round(cpu_ms / gpu_ms, 3) if gpu_ms > 0 else None,
        "meets_2x_gate": gpu_ms > 0 and cpu_ms / gpu_ms >= 2.0,
    }


def run(work_dir: Path, *, rounds: int = 3) -> dict[str, Any]:
    if rounds < 3:
        raise ValueError("at least three measured rounds are required")
    work_dir = prepare_work_dir(work_dir)
    library = work_dir / "synthetic-library"
    data_root = work_dir / "PhotoSearchData"
    paths = build_library(library)
    install_pinned_weights(data_root)
    images = {name: _decode(path).image for name, path in paths.items()}
    cpu = _run_device(device="cpu", data_root=data_root, images=images, rounds=rounds)
    gpu = _run_device(device="gpu", data_root=data_root, images=images, rounds=rounds)
    runtime = runtime_capabilities()
    return {
        "status": "ok",
        "task": "P4-02Y-L",
        "work_dir": str(work_dir),
        "rounds": rounds,
        "fixtures": {name: str(path) for name, path in paths.items()},
        "runtime": {
            "package": f"{runtime.runtime_package}@{runtime.runtime_version}",
            "providers": list(runtime.providers),
            "cuda_available": runtime.cuda_available,
        },
        "cpu": cpu,
        "gpu": gpu,
        "comparison": _compare(cpu, gpu),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=None,
                        help="an empty project OCR temp or system temp directory")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("test-results/p4-02y-l-cuda-compare.json"))
    args = parser.parse_args()
    work_dir = args.work_dir if args.work_dir is not None else new_work_dir("compare")
    result = run(work_dir.resolve(), rounds=args.rounds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": result["status"],
                "cpu_median_ms": result["cpu"]["median_round_total_ms"],
                "gpu_median_ms": result["gpu"]["median_round_total_ms"],
                "speedup": result["comparison"]["cpu_to_gpu_speedup"],
                "equivalent": result["comparison"]["equivalent"],
                "meets_2x_gate": result["comparison"]["meets_2x_gate"],
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0 if result["comparison"]["equivalent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
