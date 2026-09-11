"""P0-06 stage B: compare the frozen Chinese-CLIP baseline on an authorized sample set.

The quality stage is BLOCKED until the user authorizes a concrete sample source, so this
tool refuses to run without a dataset that records that authorization. It never scans a
photo library by itself: every image path comes from the dataset file.

Reported per split (tuning and holdout are never mixed): nDCG@20, Recall@50, best rank and
the failing queries, plus model identity, per-image and per-query latency and the CUDA/CPU
resource facts of the run.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

# The probe helpers live in the sibling ``tools/runtime`` directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

import numpy as np  # noqa: E402
import onnxruntime  # noqa: E402
from runtime_common import (  # noqa: E402
    REPOSITORY_ROOT,
    default_model_dir,
    gpu_hardware_summary,
    gpu_memory_used_mib,
    measure,
    process_gpu_memory_mib,
    process_working_set_mib,
    write_json,
)

from photo_search.evaluation.dataset import (
    DatasetError,
    EvalDataset,
    EvalQuery,
    load_dataset,
)
from photo_search.evaluation.metrics import score_query, summarise
from photo_search.models.embedding.chinese_clip_onnx import (
    ChineseClipOnnxAdapter,
    RuntimeConfig,
)
from photo_search.runtime.device import available_providers

DEFAULT_RESULT = REPOSITORY_ROOT / "docs/reports/P0-06-model-evaluation.json"


def encode_dataset(adapter: ChineseClipOnnxAdapter, dataset: EvalDataset) -> dict[str, Any]:
    """Encode every authorized image and query with batch=1 and record the wall time."""
    image_vectors: dict[str, np.ndarray] = {}
    started = time.perf_counter()
    for image in dataset.images:
        if not image.path.is_file():
            raise DatasetError(f"authorized image is missing: {image.path}")
        image_vectors[image.id] = np.asarray(adapter.encode_image(image.path), dtype=np.float32)
    image_seconds = time.perf_counter() - started

    query_vectors: dict[str, np.ndarray] = {}
    started = time.perf_counter()
    for query in dataset.queries:
        query_vectors[query.id] = np.asarray(adapter.encode_text(query.text), dtype=np.float32)
    query_seconds = time.perf_counter() - started
    return {
        "image_vectors": image_vectors,
        "query_vectors": query_vectors,
        "image_seconds": image_seconds,
        "query_seconds": query_seconds,
    }


def rank_for_query(
    query_vector: np.ndarray, image_vectors: dict[str, np.ndarray]
) -> list[str]:
    ids = list(image_vectors)
    matrix = np.stack([image_vectors[image_id] for image_id in ids])
    scores = matrix @ query_vector
    return [ids[index] for index in np.argsort(-scores).tolist()]


def evaluate(
    adapter: ChineseClipOnnxAdapter, dataset: EvalDataset, *, repeats: int
) -> dict[str, Any]:
    encoded = encode_dataset(adapter, dataset)
    scores = []
    per_query_latency = []
    for query in dataset.queries:
        vector = encoded["query_vectors"][query.id]
        ranked = rank_for_query(vector, encoded["image_vectors"])
        scores.append(score_query(query.id, query.split, ranked, query.relevant))
        single = measure(
            lambda text=query.text: adapter.encode_text(text), repeats=repeats, warmup=0
        )
        per_query_latency.append(single["p50_ms"])
    first_image = dataset.images[0]
    image_latency = measure(
        lambda: adapter.encode_image(first_image.path), repeats=repeats, warmup=0
    )
    return {
        "metrics": summarise(scores),
        "query_latency_ms": {
            "mean_p50": round(float(np.mean(per_query_latency)), 2),
            "max_p50": round(float(np.max(per_query_latency)), 2),
        },
        "image_latency_ms": image_latency,
        "throughput": {
            "images_per_second": round(len(dataset.images) / encoded["image_seconds"], 3),
            "queries_per_second": round(len(dataset.queries) / encoded["query_seconds"], 3),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=None)
    parser.add_argument("--model-dir", type=Path, default=default_model_dir())
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPOSITORY_ROOT / "tools/prepare_models/manifests/chinese-clip-vit-b-16.json",
    )
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--device", default="cuda", choices=["cuda", "cpu"])
    parser.add_argument("--cpu-baseline", action="store_true")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument(
        "--limit-images",
        type=int,
        default=None,
        help=(
            "evaluate only a deterministic stride sample of the corpus; used for the CPU "
            "comparison run, and recorded in the report so the two are never confused"
        ),
    )
    args = parser.parse_args()

    if args.dataset is None:
        blocked = {
            "status": "BLOCKED",
            "task": "P0-06",
            "stage": "B",
            "reason": (
                "no authorized 2,000-image sample source was provided; the evaluator, data "
                "contract, split rules and metrics are implemented and unit-tested, but the "
                "quality comparison cannot run and P0-06 cannot be marked PASS"
            ),
            "available_providers": list(available_providers()),
        }
        write_json(args.result.resolve(), blocked)
        print(blocked, file=sys.stderr)
        return 2

    result: dict[str, Any] = {
        "status": "running",
        "task": "P0-06",
        "stage": "B",
        "dataset": str(args.dataset.resolve()),
        "device": args.device,
        "runtime": onnxruntime.__version__,
        "hardware": gpu_hardware_summary(),
        "gpu_memory_used_before_load_mib": gpu_memory_used_mib(),
    }
    try:
        dataset = load_dataset(args.dataset.resolve())
        if args.limit_images and len(dataset.images) > args.limit_images:
            stride = len(dataset.images) / args.limit_images
            picked = [
                dataset.images[min(len(dataset.images) - 1, int(index * stride))]
                for index in range(args.limit_images)
            ]
            kept = {image.id for image in picked}
            dataset = EvalDataset(
                name=dataset.name,
                images=tuple(picked),
                queries=tuple(
                    EvalQuery(
                        id=query.id,
                        text=query.text,
                        split=query.split,
                        relevant={
                            image_id: grade
                            for image_id, grade in query.relevant.items()
                            if image_id in kept
                        },
                    )
                    for query in dataset.queries
                    if any(image_id in kept for image_id in query.relevant)
                ),
                annotation_rules=dataset.annotation_rules,
                authorization=dataset.authorization,
                image_split_policy=dataset.image_split_policy,
            )
            result["limited_to_images"] = len(dataset.images)
            result["limited_queries"] = len(dataset.queries)
        result["sample"] = dataset.summary()
        adapter = ChineseClipOnnxAdapter(
            args.model_dir.resolve(),
            args.manifest.resolve(),
            RuntimeConfig(requested_device=args.device),
        )
        adapter.load()
        result["model"] = adapter.spec().as_dict()
        result["device_facts"] = adapter.resolution.as_dict()
        result["evaluation"] = evaluate(adapter, dataset, repeats=args.repeats)
        if args.cpu_baseline:
            cpu_adapter = ChineseClipOnnxAdapter(
                args.model_dir.resolve(),
                args.manifest.resolve(),
                RuntimeConfig(requested_device="cpu"),
            )
            cpu_adapter.load()
            result["cpu_baseline"] = {
                "device_facts": cpu_adapter.resolution.as_dict(),
                "evaluation": evaluate(cpu_adapter, dataset, repeats=max(1, args.repeats // 2)),
            }
        result["resources"] = {
            "gpu_memory_used_after_run_mib": gpu_memory_used_mib(),
            "process_gpu_memory_mib": process_gpu_memory_mib(),
            "working_set_mib": process_working_set_mib(),
        }
        result["status"] = "pass"
        write_json(args.result.resolve(), result)
        print(
            {
                "status": "pass",
                "result": str(args.result.resolve()),
                "sample": result["sample"],
                "metrics": result["evaluation"]["metrics"]["overall"],
                "device": result["device_facts"]["device"],
            }
        )
        return 0
    except Exception as error:
        result["status"] = "fail"
        result["exception_type"] = type(error).__name__
        result["message"] = str(error)
        write_json(args.result.resolve(), result)
        print(json.dumps(result), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
