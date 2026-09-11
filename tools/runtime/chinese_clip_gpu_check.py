"""P0-06 stage A: Chinese-CLIP CUDA bring-up, provider evidence, consistency, latency.

One process does all of it: CUDA initialisation is expensive on a power-gated laptop
GPU, so the probe loads the frozen P0-03 image and text towers once on CUDA and once on
CPU and compares them on the same fixed fixture.

Evidence produced:
- the resolved device/provider and the ONNX Runtime DLL/version facts (A1, A2);
- per-node execution providers from ORT profiling, so a declared-CUDA-but-CPU run is
  excluded instead of assumed (A3);
- CPU/GPU FP32 maximum absolute difference, per-vector cosine and Top-K (A4);
- warm latency distributions and VRAM usage (A5).
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime
from PIL import Image
from runtime_common import (
    REPOSITORY_ROOT,
    default_model_dir,
    gpu_hardware_summary,
    gpu_memory_used_mib,
    l2_normalize,
    measure,
    process_gpu_apps,
    process_gpu_memory_mib,
    process_working_set_mib,
    read_json,
    write_json,
)

from photo_search.models.embedding.chinese_clip_onnx import (
    ChineseClipOnnxAdapter,
    RuntimeConfig,
)
from photo_search.runtime.device import runtime_capabilities

QUERIES = ["一只橙色的猫", "海边的日落", "城市夜景", "家人聚餐", "绿色的山林"]
DEFAULT_MANIFEST = REPOSITORY_ROOT / "tools/prepare_models/manifests/chinese-clip-vit-b-16.json"
P0_03_RECORD = REPOSITORY_ROOT / "tools/prepare_models/manifests/chinese-clip-vit-b-16-validation.json"
DEFAULT_RESULT = REPOSITORY_ROOT / "docs/reports/P0-06-chinese-clip-gpu-check.json"
MIN_COSINE = 0.9999
GTX_1660_MIB = 6144


def build_fixture_image() -> Image.Image:
    """The exact P0-03 fixture: a deterministic RGB gradient/checkerboard."""
    y, x = np.mgrid[0:224, 0:224]
    return Image.fromarray(
        np.dstack(
            (
                ((x * 3 + y) % 256).astype(np.uint8),
                ((x + y * 2) % 256).astype(np.uint8),
                (((x // 8) ^ (y // 8)) * 32 % 256).astype(np.uint8),
            )
        )
    ).convert("RGB")


def top_k_order(image_vector: list[float] | np.ndarray, text_vectors: list[Any]) -> list[int]:
    image = np.asarray(image_vector, dtype=np.float32)
    texts = np.asarray(text_vectors, dtype=np.float32)
    return np.argsort(-(l2_normalize(image) @ l2_normalize(texts).T)).tolist()


def compare_vectors(
    reference: list[float] | np.ndarray, candidate: list[float] | np.ndarray
) -> dict[str, Any]:
    left = np.asarray(reference, dtype=np.float32)
    right = np.asarray(candidate, dtype=np.float32)
    normalised_left, normalised_right = l2_normalize(left), l2_normalize(right)
    return {
        "max_abs_difference": float(np.max(np.abs(left - right))),
        "cosine_similarity": float(np.sum(normalised_left * normalised_right)),
        "shape": list(left.shape),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=default_model_dir())
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--gpu-repeats", type=int, default=20)
    parser.add_argument("--cpu-repeats", type=int, default=5)
    parser.add_argument("--gpu-memory-limit-mib", type=int, default=None)
    parser.add_argument("--skip-cpu", action="store_true")
    args = parser.parse_args()

    steps: list[str] = []
    started = time.perf_counter()
    baseline_used = gpu_memory_used_mib()
    result: dict[str, Any] = {
        "status": "running",
        "task": "P0-06",
        "stage": "A",
        "fixture": {"image": "P0-03 deterministic RGB gradient/checkerboard", "queries": QUERIES},
        "thresholds": {"min_cosine_similarity": MIN_COSINE, "top_k_identical": True},
    }
    try:
        model_dir = args.model_dir.resolve()
        manifest = args.manifest.resolve()
        result["environment"] = {
            "python": sys.version.split()[0],
            "onnxruntime": onnxruntime.__version__,
            "capabilities": runtime_capabilities().as_dict(),
            "hardware": gpu_hardware_summary(),
            "gpu_memory_used_before_load_mib": baseline_used,
            "working_set_before_load_mib": process_working_set_mib(),
        }
        steps.append("environment")
        if not result["environment"]["capabilities"]["cuda_available"]:
            raise RuntimeError("CUDAExecutionProvider is not available in this environment")

        with tempfile.TemporaryDirectory(prefix="photo-search-gpu-check-") as raw:
            directory = Path(raw)
            image_path = directory / "p0-03-fixture.png"
            build_fixture_image().save(image_path)
            profile_dir = directory / "profiles"
            profile_dir.mkdir()

            gpu = ChineseClipOnnxAdapter(
                model_dir,
                manifest,
                RuntimeConfig(
                    requested_device="cuda",
                    enable_profiling=True,
                    profile_directory=profile_dir,
                    cuda_memory_limit_bytes=(
                        args.gpu_memory_limit_mib * 1024 * 1024
                        if args.gpu_memory_limit_mib
                        else None
                    ),
                ),
            )
            load_started = time.perf_counter()
            gpu.load()
            result["gpu"] = {
                "load_seconds": round(time.perf_counter() - load_started, 2),
                "spec": gpu.spec().as_dict(),
                "health": gpu.health(),
                "gpu_memory_used_after_load_mib": gpu_memory_used_mib(),
                "process_gpu_memory_after_load_mib": process_gpu_memory_mib(),
                "working_set_after_load_mib": process_working_set_mib(),
            }
            steps.append("gpu_load")

            image_vector = gpu.encode_image(image_path)
            text_vectors = [gpu.encode_text(query) for query in QUERIES]
            result["gpu"]["image_l2_norm"] = float(np.linalg.norm(np.asarray(image_vector)))
            result["gpu"]["image_shape"] = [len(image_vector)]
            result["gpu"]["text_l2_norms"] = [
                float(np.linalg.norm(np.asarray(vector))) for vector in text_vectors
            ]
            result["gpu"]["top_k"] = top_k_order(image_vector, text_vectors)
            steps.append("gpu_inference")

            latency_peak_used = gpu_memory_used_mib()
            image_latency = measure(lambda: gpu.encode_image(image_path), args.gpu_repeats)
            latency_peak_used = max(latency_peak_used or 0, gpu_memory_used_mib() or 0)
            text_latency = measure(lambda: gpu.encode_text(QUERIES[0]), args.gpu_repeats)
            latency_peak_used = max(latency_peak_used or 0, gpu_memory_used_mib() or 0)
            result["gpu"]["provider_usage"] = gpu.provider_usage()
            result["gpu"]["latency"] = {"image": image_latency, "text": text_latency}
            result["gpu"]["gpu_memory_used_peak_mib"] = latency_peak_used
            result["gpu"]["process_gpu_memory_peak_mib"] = process_gpu_memory_mib()
            result["gpu"]["working_set_peak_mib"] = process_working_set_mib()
            result["gpu"]["gpu_processes"] = process_gpu_apps()
            steps.append("gpu_latency")

            providers_used = {
                provider
                for role in result["gpu"]["provider_usage"].values()
                for provider in (role.get("node_providers") or {})
            }
            if providers_used != {"CUDAExecutionProvider"}:
                raise RuntimeError(
                    f"gpu sessions did not run exclusively on CUDA: {sorted(providers_used)}"
                )

            if not args.skip_cpu:
                cpu = ChineseClipOnnxAdapter(
                    model_dir, manifest, RuntimeConfig(requested_device="cpu")
                )
                cpu.load()
                cpu_image = cpu.encode_image(image_path)
                cpu_text = [cpu.encode_text(query) for query in QUERIES]
                steps.append("cpu_inference")
                result["cpu"] = {
                    "spec": cpu.spec().as_dict(),
                    "device": cpu.resolution.as_dict(),
                    "top_k": top_k_order(cpu_image, cpu_text),
                    "image_l2_norm": float(np.linalg.norm(np.asarray(cpu_image))),
                    "consistency": {
                        "image": compare_vectors(image_vector, cpu_image),
                        "text": [
                            compare_vectors(gpu_vector, cpu_vector)
                            for gpu_vector, cpu_vector in zip(text_vectors, cpu_text, strict=True)
                        ],
                    },
                    "latency": {
                        "image": measure(lambda: cpu.encode_image(image_path), args.cpu_repeats),
                        "text": measure(lambda: cpu.encode_text(QUERIES[0]), args.cpu_repeats),
                    },
                }
                cosines = [result["cpu"]["consistency"]["image"]["cosine_similarity"]] + [
                    entry["cosine_similarity"] for entry in result["cpu"]["consistency"]["text"]
                ]
                result["cpu"]["consistency"]["min_cosine_similarity"] = min(cosines)
                result["cpu"]["consistency"]["top_k_identical"] = (
                    result["gpu"]["top_k"] == result["cpu"]["top_k"]
                )
                steps.append("cpu_compare")

                record = read_json(P0_03_RECORD)
                if record:
                    recorded_order = (record.get("top_k") or {}).get("onnx_rank")
                    result["cpu"]["p0_03_record"] = {
                        "path": str(P0_03_RECORD),
                        "recorded_top_k": recorded_order,
                        "cpu_top_k_matches_record": recorded_order == result["cpu"]["top_k"],
                        "recorded_onnx_image_max_abs_difference": (record.get("image") or {}).get(
                            "max_abs_difference"
                        ),
                        "recorded_onnx_text_max_abs_difference": (record.get("text") or {}).get(
                            "max_abs_difference"
                        ),
                    }

                if result["cpu"]["consistency"]["min_cosine_similarity"] < MIN_COSINE:
                    raise ValueError(
                        "CPU/GPU cosine below threshold: "
                        f"{result['cpu']['consistency']['min_cosine_similarity']} < {MIN_COSINE}"
                    )
                if not result["cpu"]["consistency"]["top_k_identical"]:
                    raise ValueError("CPU/GPU Top-K differs")

        result["vram_budget"] = _vram_budget(result, baseline_used)
        result["status"] = "pass"
        result["elapsed_seconds"] = round(time.perf_counter() - started, 1)
        steps.append("result")
        write_json(args.result.resolve(), result)
        print(
            {
                "status": "pass",
                "steps": [*steps, "written"],
                "result": str(args.result.resolve()),
                "device": result["gpu"]["health"]["device"],
                "providers": result["gpu"]["provider_usage"],
                "cpu_min_cosine": (result.get("cpu", {}).get("consistency") or {}).get(
                    "min_cosine_similarity"
                ),
                "top_k": result["gpu"]["top_k"],
                "gpu_latency_ms": {
                    "image_p50": result["gpu"]["latency"]["image"]["p50_ms"],
                    "text_p50": result["gpu"]["latency"]["text"]["p50_ms"],
                },
                "gpu_memory_used_mib": result["gpu"]["gpu_memory_used_peak_mib"],
                "elapsed_seconds": result["elapsed_seconds"],
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


def _vram_budget(result: dict[str, Any], baseline_used: int | None) -> dict[str, Any]:
    """Report the measured worker footprint against the GTX 1660 6 GB target."""
    peak = result["gpu"].get("gpu_memory_used_peak_mib")
    process_peak = result["gpu"].get("process_gpu_memory_peak_mib")
    budget: dict[str, Any] = {
        "target_gpu_memory_mib": GTX_1660_MIB,
        "measurement_scope": (
            "nvidia-smi total GPU memory before/after load and during the latency loop; "
            "per-process usage is reported when the driver exposes it"
        ),
        "desktop_and_other_processes_mib": baseline_used,
        "total_gpu_memory_used_peak_mib": peak,
        "worker_process_gpu_memory_peak_mib": process_peak,
        "note": (
            "Batch is fixed at 1 by the frozen manifest; the text tower is loaded in the "
            "same worker process, so the peak covers both towers and inference workspace."
        ),
    }
    if peak is not None and baseline_used is not None:
        budget["worker_delta_mib"] = peak - baseline_used
    return budget


if __name__ == "__main__":
    raise SystemExit(main())
