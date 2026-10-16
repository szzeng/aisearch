"""Fixed native SigLIP2 NaFlex comparison on already-observed public photo data.

``run`` verifies a pinned local model and public-image hashes, then encodes with the
official processor on CUDA. ``compare`` replays its vectors against saved CLIP/Qwen
runs on CPU. This is an additional fixed comparison, not another blind confirmation.
No product catalog, model download, parameter search, or dependency installation occurs.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import inspect
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

from tools.evaluation import peer_search_quality as peer
from tools.evaluation import public_search_quality as public

ROOT = Path(__file__).resolve().parents[2]
MODEL_ID = "google/siglip2-base-patch16-naflex"
REVISION = "b53b807d3a2d5e2b3911292f2d69e5341cdc064c"
DIMENSION = 768
MODE = "additional_fixed_comparison_on_observed_data"
MODEL_FILES = {
    "README.md", "config.json", "preprocessor_config.json", "special_tokens_map.json",
    "tokenizer_config.json", "tokenizer.json", "tokenizer.model", "model.safetensors",
}
PACKAGES = ("numpy", "pillow", "torch", "torchvision", "transformers", "tokenizers", "safetensors")
SETTINGS = {
    "dimension": 768, "dtype": "float32", "batch_size": 1, "device": "cuda",
    "max_num_patches": 256, "patch_size": 16, "max_length": 64,
    "padding": "max_length", "truncation": True, "query_transform": "caption.lower()",
    "image_transform": "EXIF transpose, RGB, official NaFlex processor; no manual resize/crop",
    "ranking": "L2-normalized native features, cosine similarity",
}
LIMITS = [*peer.LIMITS,
          "Existing baseline outcomes were already seen; this is an added fixed comparison.",
          "Timings include preprocessing, transfers and normalization; CUDA is synchronized.",
          "PyTorch peak memory excludes other processes and CUDA allocations outside its allocator."]


def json_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def static_identities(dataset: Path) -> dict:
    paths = {"dataset": dataset, "tool": Path(__file__), "public_tool": Path(public.__file__),
             "peer_tool": Path(peer.__file__), "pyproject": ROOT / "pyproject.toml",
             "uv_lock": ROOT / "uv.lock"}
    return {"sha256": {key: public.sha256(path) for key, path in paths.items()},
            "python": sys.version,
            "packages": {name: importlib.metadata.version(name) for name in PACKAGES}}


def verify_model(model_dir: Path, lock_path: Path) -> dict:
    lock = peer.read_json(lock_path)
    if (lock.get("model_id") != MODEL_ID or lock.get("revision") != REVISION
            or set(lock.get("files", {})) != MODEL_FILES):
        raise ValueError("model lock is not the fixed SigLIP2 revision/file set")
    actual = {}
    for name, expected in lock["files"].items():
        if Path(name).name != name or len(expected) != 64:
            raise ValueError("invalid model lock filename or SHA256")
        path = model_dir / name
        if not path.is_file() or public.sha256(path) != expected:
            raise ValueError(f"model file fingerprint mismatch: {name}")
        actual[name] = expected
    config = peer.read_json(model_dir / "config.json")
    processor = peer.read_json(model_dir / "preprocessor_config.json")
    if (config.get("model_type") != "siglip2"
            or processor.get("max_num_patches") != 256 or processor.get("patch_size") != 16):
        raise ValueError("model or official processor configuration changed")
    return {"model_id": MODEL_ID, "revision": REVISION, "files": actual,
            "lock_sha256": public.sha256(lock_path)}


def verify_images(dataset: Path) -> tuple[list, list, str]:
    images, queries = peer.validate_dataset(peer.read_json(dataset))
    for row in images:
        path = Path(row["path"])
        if not path.is_file() or public.sha256(path) != row["sha256"]:
            raise ValueError(f"public image changed: {row['id']}")
    return images, queries, json_digest([(row["id"], row["sha256"]) for row in images])


def reserve_output(directory: Path) -> None:
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise FileExistsError("preserve existing evidence; choose an empty run directory")
    directory.mkdir(parents=True, exist_ok=True)


def timed_cuda(torch: Any, function: Callable[[], Any]) -> tuple[Any, float]:
    torch.cuda.synchronize()
    started = time.perf_counter()
    result = function()
    torch.cuda.synchronize()
    return result, time.perf_counter() - started


def latency_summary(seconds: list[float]) -> dict:
    if not seconds or any(not math.isfinite(value) or value < 0 for value in seconds):
        raise ValueError("invalid latency observations")
    return {"count": len(seconds), "sum_seconds": sum(seconds), "first_ms": seconds[0] * 1000,
            "p50_ms": statistics.median(seconds) * 1000,
            "p95_ms": sorted(seconds)[math.ceil(.95 * len(seconds)) - 1] * 1000}


def memory_peak(torch: Any) -> dict:
    return {"peak_allocated_bytes": torch.cuda.max_memory_allocated(),
            "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
            "current_allocated_bytes": torch.cuda.memory_allocated(),
            "current_reserved_bytes": torch.cuda.memory_reserved()}


def feature_numpy(feature: Any) -> np.ndarray:
    values = feature.detach().float().cpu().numpy()
    if values.shape != (1, DIMENSION):
        raise ValueError("SigLIP2 must produce exactly one native 768-dimensional feature")
    return public.normalized(values)[0]


def split_metrics(vectors: dict, images: list, queries: list) -> dict:
    peer.validate_vectors(vectors, {"queries": (len(queries), DIMENSION),
                                    "native": (len(images), DIMENSION)})
    results = {}
    for split in ("tuning", "holdout"):
        qi = [i for i, row in enumerate(queries) if row["split"] == split]
        ii = [i for i, row in enumerate(images) if row["split"] == split]
        scores = vectors["queries"][qi] @ vectors["native"][ii].T
        results[split] = public.ranked_metrics(scores, [queries[i] for i in qi],
                                              [images[i]["id"] for i in ii])
    return results


def dependency_runtime(torch: Any, processor: Any, model: Any) -> dict:
    source_files = {type(obj).__module__: inspect.getfile(type(obj))
                    for obj in (model, processor, processor.tokenizer, processor.image_processor)}
    records = {}
    for name in PACKAGES:
        record = importlib.metadata.distribution(name).read_text("RECORD")
        records[name] = hashlib.sha256(record.encode()).hexdigest() if record else None
    props = torch.cuda.get_device_properties(torch.cuda.current_device())
    return {
        "device": {"name": props.name, "total_memory": props.total_memory,
                   "capability": [props.major, props.minor]},
        "torch_cuda_version": torch.version.cuda, "cudnn_version": torch.backends.cudnn.version(),
        "float32_matmul_precision": torch.get_float32_matmul_precision(),
        "cuda_matmul_allow_tf32": torch.backends.cuda.matmul.allow_tf32,
        "cudnn_allow_tf32": torch.backends.cudnn.allow_tf32,
        "attention_implementation": model.config._attn_implementation,
        "processor_classes": {"processor": type(processor).__name__,
                              "image": type(processor.image_processor).__name__,
                              "tokenizer": type(processor.tokenizer).__name__},
        "implementation_sha256": {key: public.sha256(Path(path))
                                  for key, path in source_files.items()},
        "distribution_record_sha256": records,
        "record_hash_limit": "RECORD metadata hashes identify installed distributions, not every binary.",
    }


def run(args: argparse.Namespace) -> dict:
    directory = args.output.resolve()
    reserve_output(directory)
    start = time.perf_counter()
    identities = static_identities(args.dataset)
    model_lock = verify_model(args.model_dir, args.model_lock)
    images, queries, image_digest = verify_images(args.dataset)
    verification_seconds = time.perf_counter() - start
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    started = time.perf_counter()
    import torch
    from PIL import Image, ImageOps
    from transformers import AutoModel, AutoProcessor
    imports_seconds = time.perf_counter() - started
    if not torch.cuda.is_available():
        raise RuntimeError("this fixed SigLIP2 experiment requires CUDA; no silent CPU fallback")
    started = time.perf_counter()
    torch.cuda.init()
    torch.cuda.synchronize()
    cuda_init_seconds = time.perf_counter() - started
    torch.cuda.reset_peak_memory_stats()

    def load():
        processor = AutoProcessor.from_pretrained(
            args.model_dir, local_files_only=True, trust_remote_code=False,
        )
        model = AutoModel.from_pretrained(
            args.model_dir, local_files_only=True, trust_remote_code=False,
            torch_dtype=torch.float32,
        ).eval().to("cuda")
        if (model.config.model_type != "siglip2"
                or model.config.text_config.hidden_size != DIMENSION
                or model.config.vision_config.hidden_size != DIMENSION
                or next(model.parameters()).dtype != torch.float32):
            raise ValueError("loaded model differs from native float32 SigLIP2 base")
        return processor, model

    (processor, model), load_seconds = timed_cuda(torch, load)
    runtime = dependency_runtime(torch, processor, model)
    peaks = {"load": memory_peak(torch)}
    query_vectors, query_times, image_vectors, image_times = [], [], [], []
    torch.cuda.reset_peak_memory_stats()
    with torch.inference_mode():
        for query in queries:
            def text_feature():
                inputs = processor(text=[query["text"].lower()], padding="max_length",
                                   max_length=64, truncation=True, return_tensors="pt").to("cuda")
                return feature_numpy(model.get_text_features(**inputs))
            vector, duration = timed_cuda(torch, text_feature)
            query_vectors.append(vector)
            query_times.append(duration)
        peaks["text"] = memory_peak(torch)
        torch.cuda.reset_peak_memory_stats()
        for index, row in enumerate(images):
            def image_feature():
                with Image.open(row["path"]) as source:
                    image = ImageOps.exif_transpose(source).convert("RGB")
                    inputs = processor(images=image, max_num_patches=256, patch_size=16,
                                       return_tensors="pt").to("cuda")
                # Keep masks/spatial shapes: NaFlex is not a square CLIP input.
                return feature_numpy(model.get_image_features(**inputs))
            vector, duration = timed_cuda(torch, image_feature)
            image_vectors.append(vector)
            image_times.append(duration)
            if (index + 1) % 64 == 0:
                print(f"encoded {index + 1}/{len(images)} SigLIP2", flush=True)
    peaks["images"] = memory_peak(torch)
    vectors = {"queries": np.asarray(query_vectors, dtype=np.float32),
               "native": np.asarray(image_vectors, dtype=np.float32)}
    metrics = split_metrics(vectors, images, queries)
    if (static_identities(args.dataset) != identities
            or verify_model(args.model_dir, args.model_lock) != model_lock
            or verify_images(args.dataset)[2] != image_digest):
        raise ValueError("input/model/code identity changed during encoding")
    with (directory / "vectors.npz").open("xb") as handle:
        np.savez_compressed(handle, **vectors)
    result = {
        "schema": "siglip-search-run-v1", "status": "ok", "mode": MODE,
        "settings": SETTINGS, "identities": identities, "model_lock": model_lock,
        "verified_images_sha256": image_digest, "vectors_sha256": public.sha256(directory / "vectors.npz"),
        "runtime": runtime, "runtime_sha256": json_digest(runtime),
        "performance": {"verification_seconds": verification_seconds,
                        "imports_seconds": imports_seconds, "cuda_init_seconds": cuda_init_seconds,
                        "load_seconds_cuda_synchronized": load_seconds,
                        "text": latency_summary(query_times), "images": latency_summary(image_times),
                        "phase_memory": peaks},
        "variants": {"native768": metrics}, "limits": LIMITS,
    }
    peer.write_new_json(directory / "result.json", result)
    return {"status": "ok", "result": str(directory / "result.json"), "mode": MODE,
            "holdout_top1": metrics["holdout"]["recall_at_1"], "performance": result["performance"]}


def compare(args: argparse.Namespace) -> dict:
    if args.output.exists():
        raise FileExistsError("preserve existing evidence; choose a new comparison output")
    inputs = peer.load_inputs(args.dataset, args.clip_run, args.qwen_run, args.plan,
                             mode="confirmation", exclude_dataset=args.exclude_dataset)
    result = peer.read_json(args.run_dir / "result.json")
    execution = peer.read_json(args.run_dir / "execution.json")
    if (result.get("schema") != "siglip-search-run-v1" or result.get("status") != "ok"
            or result.get("mode") != MODE or result.get("settings") != SETTINGS
            or result.get("identities") != static_identities(args.dataset)
            or execution.get("status") != "ok" or execution.get("exit_code") != 0
            or result.get("runtime_sha256") != json_digest(result.get("runtime"))
            or result.get("vectors_sha256") != public.sha256(args.run_dir / "vectors.npz")):
        raise ValueError("SigLIP run identity/status/settings mismatch")
    lock = result.get("model_lock", {})
    if (lock.get("model_id") != MODEL_ID or lock.get("revision") != REVISION
            or set(lock.get("files", {})) != MODEL_FILES):
        raise ValueError("SigLIP run has the wrong model identity")
    with np.load(args.run_dir / "vectors.npz", allow_pickle=False) as archive:
        vectors = {key: archive[key] for key in archive.files}
    reproduced = split_metrics(vectors, inputs["images"], inputs["queries"])
    if reproduced != result.get("variants", {}).get("native768"):
        raise ValueError("saved SigLIP vectors do not reproduce their recorded rankings")
    splits = {}
    for split in ("tuning", "holdout"):
        scores, queries, images = peer.base_scores(inputs, split)
        peer.verify_baselines(inputs, scores, queries, images, split)
        qwen = inputs["runs"]["qwen"]["vectors"]
        qi = [i for i, row in enumerate(inputs["queries"]) if row["split"] == split]
        ii = [i for i, row in enumerate(inputs["images"]) if row["split"] == split]
        scores["qwen2048"] = public.normalized(qwen["queries"][qi]) @ public.normalized(
            qwen["native"][ii],
        ).T
        variants = {name: public.ranked_metrics(scores[name], queries, [row["id"] for row in images])
                    for name in ("clip_center", "clip_two_view_max", "qwen512", "qwen2048")}
        if variants["qwen2048"] != inputs["runs"]["qwen"]["result"]["variants"]["2048"][split]:
            raise ValueError("Qwen2048 saved-vector baseline mismatch")
        candidate = reproduced[split]
        comparisons = {name: peer.paired_bootstrap(value["per_query"], candidate["per_query"])
                       for name, value in variants.items()}
        splits[split] = {"baselines": variants, "siglip_native768": candidate,
                         "siglip_vs_baseline": comparisons}
    comparison = {"schema": "siglip-search-comparison-v1", "status": "ok", "mode": MODE,
                  "settings": SETTINGS, "baseline_identities": inputs["identities"],
                  "siglip_result_sha256": public.sha256(args.run_dir / "result.json"),
                  "siglip_execution_sha256": public.sha256(args.run_dir / "execution.json"),
                  "splits": splits, "limits": LIMITS}
    peer.write_new_json(args.output, comparison)
    return {"status": "ok", "result": str(args.output), "mode": MODE,
            "holdout_top1": {**{key: row["recall_at_1"]
                                 for key, row in splits["holdout"]["baselines"].items()},
                             "siglip_native768": reproduced["holdout"]["recall_at_1"]}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    encoder = commands.add_parser("run")
    encoder.add_argument("--dataset", type=Path, required=True)
    encoder.add_argument("--model-dir", type=Path, required=True)
    encoder.add_argument("--model-lock", type=Path, required=True)
    encoder.add_argument("--output", type=Path, required=True)
    replay = commands.add_parser("compare")
    replay.add_argument("--dataset", type=Path, required=True)
    replay.add_argument("--run-dir", type=Path, required=True)
    replay.add_argument("--clip-run", type=Path, required=True)
    replay.add_argument("--qwen-run", type=Path, required=True)
    replay.add_argument("--plan", type=Path, required=True)
    replay.add_argument("--exclude-dataset", type=Path, required=True)
    replay.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = time.perf_counter()
    try:
        result = run(args) if args.command == "run" else compare(args)
    except Exception as error:
        print(json.dumps({"status": "failed", "exit_code": 1,
                          "error_type": type(error).__name__, "message": str(error)},
                         ensure_ascii=False), file=sys.stderr)
        return 1
    result.update({"exit_code": 0, "elapsed_seconds": time.perf_counter() - started,
                   "command": [sys.executable, *sys.argv]})
    if args.command == "run":
        peer.write_new_json(args.output / "execution.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
