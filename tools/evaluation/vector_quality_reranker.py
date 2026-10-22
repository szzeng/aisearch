"""Bounded, offline Qwen3-VL reranking experiment; never a production provider.

Prepare a frozen plan from an existing pilot comparison, then run it on CUDA.
The fixed official script must be present in the local model directory and pass
its checked-in hash lock. This command never downloads or modifies model files.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import sys
import time
import traceback
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
LOCK = Path(__file__).with_name("vector_quality_reranker_lock.json")
MODEL_REV = "4bd860ac4f15ad1897a214615cccc700f8f71818"
INSTRUCTION = "Retrieve images or text relevant to the user's query."
QUERY_IDS = ("cat", "dog", "bird", "car", "bicycle", "person", "chair", "teddy_bear")
SPEC = {
    "instruction": INSTRUCTION,
    "max_pixels": 1310720,
    "min_pixels": 4096,
    "dtype": "float16",
    "attention": "sdpa",
    "batch_size": 1,
    "ranking_score": "raw_yes_minus_no_logit",
    "model_revision": MODEL_REV,
}


def read_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def workspace_path(value: Path | str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise ValueError("WORKSPACE_PATH_REQUIRED")
    return path


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def save_new(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def make_plan(
    sample_path: Path,
    queries_path: Path,
    comparison_path: Path,
    *,
    baseline: str,
    top_k: int,
    smoke: bool,
) -> dict:
    if baseline not in {"qwen512", "qwen2048"}:
        raise ValueError("BASELINE_REFUSED")
    if type(top_k) is not int or not 1 <= top_k <= 100:
        raise ValueError("TOP_K_OUT_OF_BOUND")
    paths = {
        "sample": workspace_path(sample_path),
        "queries": workspace_path(queries_path),
        "comparison": workspace_path(comparison_path),
    }
    sample, queries, comparison = (read_json(paths[n]) for n in paths)
    assets = {a["asset_id"]: a for a in sample["assets"]}
    qmap = {q["query_id"]: q for q in queries}
    if len(assets) != len(sample["assets"]) or len(qmap) != len(queries):
        raise ValueError("DUPLICATE_INPUT_ID")
    recorded_inputs = {a["asset_id"]: a for a in comparison["inputs"]}
    if len(recorded_inputs) != len(comparison["inputs"]) or set(recorded_inputs) != set(assets):
        raise ValueError("COMPARISON_INPUT_IDENTITY_MISMATCH")
    for asset_id, asset in assets.items():
        recorded = recorded_inputs[asset_id]
        if any(recorded[key] != asset[key] for key in ("sha256", "selection_stratum")):
            raise ValueError("COMPARISON_INPUT_IDENTITY_MISMATCH")
    recorded_queries = {q["query_id"]: q for q in comparison["query_definitions"]}
    if len(recorded_queries) != len(comparison["query_definitions"]) or recorded_queries != qmap:
        raise ValueError("COMPARISON_QUERY_IDENTITY_MISMATCH")
    ranks = [1, 60, 98, 100] if smoke else list(range(1, top_k + 1))
    qids = ["cat", "dog", "car", "chair"] if smoke else list(QUERY_IDS)
    pairs = []
    for qid in qids:
        ordered = comparison["rankings"][baseline][qid]
        if len(ordered) != len(assets) or set(ordered) != set(assets):
            raise ValueError("RANKING_ASSET_COVERAGE_MISMATCH")
        if max(ranks) > len(ordered):
            raise ValueError("INSUFFICIENT_CANDIDATES")
        for rank in ranks:
            asset = assets[ordered[rank - 1]]
            pairs.append(
                {
                    "query_id": qid,
                    "query": qmap[qid]["text"],
                    "asset_id": asset["asset_id"],
                    "path": relative(workspace_path(asset["path"])),
                    "image_sha256": asset["sha256"],
                    "baseline_rank": rank,
                }
            )
    return {
        "schema": "vector-quality-reranker-plan-v1",
        "baseline": baseline,
        "smoke": smoke,
        "top_k": top_k,
        "ranks": ranks,
        "pairs": pairs,
        **SPEC,
        "inputs": {
            key: {"path": relative(path), "sha256": sha256(path)} for key, path in paths.items()
        },
    }


def verify_plan(plan: dict) -> None:
    """Rebuild from frozen inputs so editing candidates cannot silently bias evaluation."""
    paths = {}
    for key in ("sample", "queries", "comparison"):
        entry = plan["inputs"][key]
        paths[key] = workspace_path(entry["path"])
        if sha256(paths[key]) != entry["sha256"]:
            raise ValueError("INPUT_IDENTITY_MISMATCH:" + key)
    expected = make_plan(
        paths["sample"],
        paths["queries"],
        paths["comparison"],
        baseline=plan["baseline"],
        top_k=plan["top_k"],
        smoke=plan["smoke"],
    )
    if plan != expected:
        raise ValueError("PLAN_MISMATCH")
    for pair in plan["pairs"]:
        if sha256(workspace_path(pair["path"])) != pair["image_sha256"]:
            raise ValueError("IMAGE_IDENTITY_MISMATCH")


def verify_model_files(model_dir: Path, lock: dict) -> None:
    if lock["revision"] != MODEL_REV:
        raise ValueError("MODEL_REVISION_MISMATCH")
    for name, expected in lock["files"].items():
        path = workspace_path(model_dir / name)
        if not path.is_relative_to(model_dir):
            raise ValueError("MODEL_FILE_OUTSIDE_ROOT")
        if path.stat().st_size != expected["bytes"] or sha256(path) != expected["sha256"]:
            raise ValueError("MODEL_HASH_MISMATCH:" + name)


def strict_tokenize(reranker, pairs: list, vision_info):
    """Use the official short-input path, but never turn decode errors into NULL text."""
    processor = reranker.processor
    text = processor.apply_chat_template(pairs, tokenize=False, add_generation_prompt=True)
    images, videos, video_kwargs = vision_info(
        pairs,
        image_patch_size=16,
        return_video_kwargs=True,
        return_video_metadata=True,
    )
    if images is None or len(images) != 1 or videos is not None:
        raise ValueError("EXPECTED_EXACTLY_ONE_IMAGE")
    inputs = processor(
        text=text,
        images=images,
        videos=None,
        video_metadata=None,
        truncation=False,
        padding=False,
        do_resize=False,
        **video_kwargs,
    )
    if any(len(ids) > reranker.max_length for ids in inputs["input_ids"]):
        raise ValueError("TOKEN_LIMIT_STOP")
    inputs.update(
        processor.tokenizer.pad(
            {"input_ids": inputs["input_ids"]},
            padding=True,
            return_tensors="pt",
            max_length=reranker.max_length,
        )
    )
    if int(inputs["image_grid_thw"].shape[0]) != 1:
        raise ValueError("IMAGE_GRID_INVALID")
    return inputs


def run_probe(plan_path: Path, model_dir: Path, output: Path) -> None:
    # An output directory belongs to one invocation. No silent resume or overwritten failure.
    output.mkdir(parents=True, exist_ok=False)
    plan, lock = read_json(plan_path), read_json(LOCK)
    verify_plan(plan)
    verify_model_files(model_dir, lock)
    from tools.evaluation.wemm_pilot_runtime import (
        configure_offline,
        decode_image,
        package_versions,
        peak_ram_bytes,
    )

    configure_offline(ROOT / ".tmp/vector-quality/cache/reranker")
    cuda_cache = ROOT / ".tmp/vector-quality/cache/cuda"
    cuda_cache.mkdir(parents=True, exist_ok=True)
    os.environ["CUDA_CACHE_PATH"] = str(cuda_cache)
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA_REQUIRED")
    if torch.cuda.mem_get_info()[0] < 6 * 1024**3:
        raise RuntimeError("LESS_THAN_6GIB_FREE_STOP")
    official_path = model_dir / "scripts/qwen3_vl_reranker.py"
    spec = importlib.util.spec_from_file_location("official_qwen_reranker_frozen", official_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class StrictReranker(module.Qwen3VLReranker):
        def tokenize(self, pairs, **kwargs):
            return strict_tokenize(self, pairs, module.process_vision_info)

        @torch.inference_mode()
        def score_pair(self, pair, image):
            messages = self.format_mm_instruction(
                pair["query"],
                None,
                None,
                None,
                image,
                None,
                instruction=plan["instruction"],
            )
            start = time.perf_counter()
            inputs = self.tokenize([messages])
            tokenize_ms = (time.perf_counter() - start) * 1000
            start = time.perf_counter()
            inputs = inputs.to(self.model.device)
            torch.cuda.synchronize()
            transfer_ms = (time.perf_counter() - start) * 1000
            self.model.rope_deltas = None
            start = time.perf_counter()
            hidden = self.model(**inputs, use_cache=False).last_hidden_state[:, -1]
            # Preserve the ordering before FP16 sigmoid collapses strong scores to identical 1s.
            raw = self.score_linear(hidden).float()
            score = torch.sigmoid(raw)
            torch.cuda.synchronize()
            infer_ms = (time.perf_counter() - start) * 1000
            value, probability = float(raw[0, 0].cpu()), float(score[0, 0].cpu())
            if not math.isfinite(value) or not math.isfinite(probability):
                raise RuntimeError("NONFINITE_SCORE")
            return {
                "logit": value,
                "sigmoid": probability,
                "tokenize_ms": tokenize_ms,
                "transfer_ms": transfer_ms,
                "infer_ms": infer_ms,
                "token_count": int(inputs["input_ids"].shape[1]),
                "image_grid": inputs["image_grid_thw"].cpu().tolist(),
            }

    identity = {
        "schema": "vector-quality-reranker-run-v1",
        "plan_sha256": sha256(plan_path),
        "source_sha256": {
            relative(p): sha256(p)
            for p in [
                Path(__file__),
                LOCK,
                ROOT / "tools/evaluation/wemm_pilot_runtime.py",
                *sorted((ROOT / "src/photo_search/media").glob("*.py")),
            ]
        },
        "official_source_sha256": sha256(official_path),
        "packages": package_versions(),
        "gpu": torch.cuda.get_device_name(0),
        **SPEC,
        "strict_no_null_fallback": True,
        "use_cache": False,
        "rope_reset_per_pair": True,
    }
    save_new(output / "identity.json", identity)
    torch.cuda.reset_peak_memory_stats()
    start = time.perf_counter()
    reranker = StrictReranker(
        str(model_dir),
        max_pixels=plan["max_pixels"],
        min_pixels=plan["min_pixels"],
        torch_dtype=torch.float16,
        attn_implementation="sdpa",
        local_files_only=True,
    )
    torch.cuda.synchronize()
    load_ms = (time.perf_counter() - start) * 1000
    if any(
        p.device.type != "cuda"
        for model in (reranker.model, reranker.score_linear)
        for p in model.parameters()
    ):
        raise RuntimeError("CPU_OFFLOAD_REFUSED")
    rows = []
    started = time.perf_counter()
    with (output / "pairs.jsonl").open("x", encoding="utf-8") as stream:
        for index, pair in enumerate(plan["pairs"]):
            start = time.perf_counter()
            image, decoded = decode_image(workspace_path(pair["path"]), pair["image_sha256"])
            decode_ms = (time.perf_counter() - start) * 1000
            try:
                scored = reranker.score_pair(pair, image)
            finally:
                image.close()
            row = {
                **pair,
                **scored,
                "decode_ms": decode_ms,
                "total_ms": (time.perf_counter() - start) * 1000,
                "decoded": decoded,
            }
            rows.append(row)
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            if (index + 1) % 25 == 0:
                print(json.dumps({"completed": index + 1, "total": len(plan["pairs"])}), flush=True)
    scoring_wall_ms = (time.perf_counter() - started) * 1000
    first = plan["pairs"][0]
    image, _ = decode_image(workspace_path(first["path"]), first["image_sha256"])
    try:
        again = reranker.score_pair(first, image)
    finally:
        image.close()
    delta = abs(again["logit"] - rows[0]["logit"])
    if delta > 0.02:
        raise RuntimeError("REPEAT_SCORE_UNSTABLE")
    result = {
        "status": "COMPLETE",
        "pairs": len(rows),
        "load_ms": load_ms,
        "scoring_wall_ms": scoring_wall_ms,
        "repeat_first_logit_delta": delta,
        "gpu_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "gpu_peak_reserved_bytes": torch.cuda.max_memory_reserved(),
        "ram_peak_bytes": peak_ram_bytes(),
        "identity": identity,
    }
    save_new(output / "result.json", result)
    suite = ET.Element(
        "testsuite",
        name="reranker-runtime-invariants",
        tests="3",
        failures="0",
        errors="0",
        skipped="0",
    )
    for name in (
        "complete-finite-bounded-pairs",
        "all-model-parameters-cuda",
        "repeated-first-pair-stable",
    ):
        ET.SubElement(suite, "testcase", name=name)
    ET.ElementTree(suite).write(output / "invariants.xml", encoding="utf-8", xml_declaration=True)
    print(json.dumps(result, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="cmd", required=True)
    prepare = subs.add_parser("prepare")
    prepare.add_argument("--sample", default=".tmp/wemm-pilot/datasets/sample.json")
    prepare.add_argument("--queries", default=".tmp/wemm-pilot/datasets/queries.json")
    prepare.add_argument("--comparison", default=".tmp/wemm-pilot/runs/full/comparison.json")
    prepare.add_argument("--baseline", choices=["qwen512", "qwen2048"], default="qwen2048")
    prepare.add_argument("--top-k", type=int, default=100)
    prepare.add_argument("--smoke", action="store_true")
    prepare.add_argument("--output", required=True)
    run = subs.add_parser("run")
    run.add_argument("--plan", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--model", default=".tmp/vector-quality/models/Qwen3-VL-Reranker-2B")
    args = parser.parse_args(argv)
    output = workspace_path(args.output)
    if output.exists():
        raise FileExistsError("OUTPUT_EXISTS")
    if args.cmd == "prepare":
        plan = make_plan(
            workspace_path(args.sample),
            workspace_path(args.queries),
            workspace_path(args.comparison),
            baseline=args.baseline,
            top_k=args.top_k,
            smoke=args.smoke,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        save_new(output, plan)
        print(json.dumps({"plan": relative(output), "pairs": len(plan["pairs"])}))
    else:
        try:
            run_probe(workspace_path(args.plan), workspace_path(args.model), output)
        except Exception as exc:
            if output.is_dir() and not (output / "failure.json").exists():
                save_new(
                    output / "failure.json",
                    {
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                )
            raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
