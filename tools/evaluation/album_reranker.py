"""Freeze PhotoBench candidates without labels, then rerank only their top 50.

The full album and every baseline tail remain present. This bounded experiment
reuses the audited Qwen reranker specification/tokenization and model lock;
it does not install a provider or certify operation on a 6GB card.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import io
import json
import math
import os
import stat
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from contextlib import ExitStack
from pathlib import Path

import numpy as np

from tools.evaluation import vector_quality_reranker as previous
from tools.evaluation.budget_search_metrics import embeddings_from_record
from tools.evaluation.prepare_wemm_coco import controlled_output, write_json
from tools.evaluation.wemm_pilot_core import deterministic_rank, normalize_prefix
from tools.evaluation.wemm_quality_pilot import gpu_lock

ROOT = Path(__file__).resolve().parents[2]
SPEC = {**previous.SPEC, "top_k": 50, "dimension": 1024, "groups": 10,
        "query_count": 20, "tie_policy": "baseline deterministic_rank by asset ID; reranker retains baseline ties",
        "decoder": {"resize": "full original", "jpeg_draft": False, "exif": "normalize", "mode": "RGB", "frame": 0}}


def identity_path(path: Path) -> Path:
    """Restrict experiment inputs/outputs to project-local evidence directories."""
    return controlled_output(path)


def file_record(path: Path) -> dict:
    path = identity_path(path)
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": previous.sha256(path)}


def read_bound(path: Path, cap: int = 64 * 1024 * 1024) -> bytes:
    path = identity_path(path)
    if path.stat().st_size > cap:
        raise ValueError("input exceeds fixed experiment bound")
    return path.read_bytes()


def subset_queries(dataset_plan: dict, queries: list[dict]) -> list[str]:
    if dataset_plan.get("schema") != "photobench-plan-v1" or dataset_plan.get("album") != 3:
        raise ValueError("only the frozen complete PhotoBench album3 is planned")
    subset, splits = dataset_plan["rerank_subset"], dataset_plan["splits"]
    qids = [row["query_id"] for row in queries]
    if (len(qids) != 200 or len(set(qids)) != 200 or len(splits) != len(qids)
            or {row["query_id"] for row in splits} != set(qids)
            or len(subset) != 10 or len(set(subset)) != 10):
        raise ValueError("frozen query/split/subset coverage differs")
    groups = defaultdict(list)
    for row in splits:
        groups[row["group_id"]].append(row)
    test_groups = sorted(group for group, rows in groups.items() if all(row["split"] == "test" for row in rows))
    if subset != test_groups[:10]:
        raise ValueError("rerank subset must be the frozen first ten sorted heldout groups")
    for group in subset:
        rows = groups[group]
        if len(rows) != 2 or {row["language"] for row in rows} != {"cn", "en"} or any(
                row["split"] != "test" for row in rows):
            raise ValueError("rerank subset must contain ten heldout bilingual pairs")
    selected = {row["query_id"] for group in subset for row in groups[group]}
    return [qid for qid in qids if qid in selected]


def prepare_plan(sample_path: Path, queries_path: Path, split_path: Path, run_dir: Path) -> dict:
    paths = {"sample": sample_path, "queries": queries_path, "dataset_plan": split_path,
             "run": run_dir / "result.json", "images": run_dir / "images.npy", "query_vectors": run_dir / "queries.npy"}
    inputs = {name: file_record(path) for name, path in paths.items()}
    raw = {name: read_bound(path) for name, path in paths.items()}
    sample, queries, dataset_plan, record = (json.loads(raw[name]) for name in
                                            ("sample", "queries", "dataset_plan", "run"))
    if (sample.get("schema") != "wemm-pilot-public-sample-v1" or len(sample["assets"]) != 1047
            or record.get("model") != "qwen" or record.get("dtype") != "fp16"
            or record.get("assets") != sample["assets"] or record.get("queries") != queries
            or record.get("sample_sha256") != inputs["sample"]["sha256"]):
        raise ValueError("run differs from complete album/sample/query/model identity")
    matrices = [np.load(io.BytesIO(raw[name]), allow_pickle=False) for name in ("images", "query_vectors")]
    for key, filename in (("images", "images.npy"), ("query_vectors", "queries.npy")):
        if inputs[key]["sha256"] != record["vectors_sha256"][filename]:
            raise ValueError("run vector checksum differs")
    _, identities = embeddings_from_record(record, *matrices)
    ids = [row["asset_id"] for row in identities]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate album asset IDs")
    selected = subset_queries(dataset_plan, queries)
    scores = normalize_prefix(matrices[1], 1024) @ normalize_prefix(matrices[0], 1024).T
    rankings = {query["query_id"]: deterministic_rank(scores[i], ids)
                for i, query in enumerate(queries)}
    assets, qmap = {row["asset_id"]: row for row in sample["assets"]}, {row["query_id"]: row for row in queries}
    pairs = []
    for qid in selected:
        for rank, aid in enumerate(rankings[qid][:50], 1):
            asset = assets[aid]
            path = identity_path(Path(asset["path"]))
            pairs.append({"query_id": qid, "query": qmap[qid]["text"], "asset_id": aid,
                          "path": path.relative_to(ROOT).as_posix(), "image_sha256": asset["sha256"],
                          "baseline_rank": rank})
    if any(file_record(paths[name]) != info for name, info in inputs.items()):
        raise ValueError("input changed during candidate preparation")
    return {"schema": "album-reranker-plan-v1", "spec": SPEC, "inputs": inputs,
            "input_identities": identities, "query_definitions": queries, "reranked_query_ids": selected,
            "baseline_rankings": rankings, "pairs": pairs, "embedding_run_identity": record["identity"],
            "embedding_source_identity": {key: record[key] for key in
                                           ("model", "dtype", "model_lock", "source_sha256", "packages", "decoder")}}


def verify_plan(plan: dict) -> None:
    if plan.get("schema") != "album-reranker-plan-v1" or plan.get("spec") != SPEC:
        raise ValueError("unknown or changed reranker plan")
    paths = {}
    for name, info in plan["inputs"].items():
        path = identity_path(ROOT / info["path"])
        if file_record(path) != info:
            raise ValueError("frozen plan input changed")
        paths[name] = path
    expected = prepare_plan(paths["sample"], paths["queries"], paths["dataset_plan"], paths["run"].parent)
    if plan != expected:
        raise ValueError("reranker candidates or complete baseline differ from recomputed plan")
    checked = set()
    for pair in plan["pairs"]:
        if pair["path"] not in checked:
            if previous.sha256(identity_path(ROOT / pair["path"])) != pair["image_sha256"]:
                raise ValueError("candidate image identity changed")
            checked.add(pair["path"])


def merge_rankings(plan: dict, scored: list[dict]) -> dict[str, list]:
    if len(scored) != len(plan["pairs"]):
        raise ValueError("every frozen candidate must be scored")
    grouped = defaultdict(list)
    for expected, row in zip(plan["pairs"], scored, strict=True):
        if any(row.get(key) != value for key, value in expected.items()):
            raise ValueError("reranker pair identity or order differs")
        if type(row.get("logit")) not in (float, int) or not math.isfinite(row["logit"]):
            raise ValueError("reranker score must be finite")
        grouped[row["query_id"]].append(row)
    result = {qid: list(ranking) for qid, ranking in plan["baseline_rankings"].items()}
    if set(grouped) != set(plan["reranked_query_ids"]):
        raise ValueError("reranker query coverage differs")
    for qid, rows in grouped.items():
        baseline = result[qid]
        if len(rows) != 50 or [row["asset_id"] for row in rows] != baseline[:50]:
            raise ValueError("reranker must score exactly the frozen top50")
        head = sorted(rows, key=lambda row: (-row["logit"], row["baseline_rank"]))
        result[qid] = [row["asset_id"] for row in head] + baseline[50:]
        if len(result[qid]) != len(set(result[qid])) or set(result[qid]) != set(baseline):
            raise ValueError("reranker changed full gallery membership")
    return result


def model_directory_inventory(model_dir: Path, lock: dict) -> dict:
    """Reject every unlocked runtime file and every filesystem link."""
    model_dir = identity_path(model_dir)
    locked = set(lock["files"])
    if any(Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name
           or Path(name).as_posix() != name for name in locked):
        raise ValueError("unsafe model lock member")
    script = model_dir / "scripts/qwen3_vl_reranker.py"
    bytecode = Path(importlib.util.cache_from_source(str(script))).relative_to(model_dir).as_posix()
    caches = {".cache/huggingface/.gitignore", bytecode} | {
        ".cache/huggingface/download/" + name + ".metadata" for name in locked}
    allowed_directories = {parent.as_posix() for name in locked | caches
                           for parent in Path(name).parents if parent != Path(".")}
    found, ignored = set(), []
    pending = [model_dir]
    while pending:
        current = pending.pop()
        for path in sorted(current.iterdir()):
            name = path.relative_to(model_dir).as_posix()
            status = path.lstat()
            if (path.is_symlink() or path.is_junction()
                    or getattr(status, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise ValueError("model filesystem links refused: " + name)
            if stat.S_ISDIR(status.st_mode):
                if name not in allowed_directories:
                    raise ValueError("unlocked model directory: " + name)
                pending.append(path)
            elif stat.S_ISREG(status.st_mode):
                if name in locked:
                    found.add(name)
                elif name in caches:
                    ignored.append({"path": name, "bytes": status.st_size, "sha256": previous.sha256(path)})
                else:
                    raise ValueError("unlocked model file: " + name)
            else:
                raise ValueError("non-regular model file: " + name)
    if found != locked:
        raise ValueError("locked model file missing")
    return {"locked_files": sorted(found), "ignored_cache_files": sorted(ignored, key=lambda row: row["path"])}


def load_locked_module(model_dir: Path, lock: dict):
    """Compile the exact locked source bytes, never a neighboring bytecode cache."""
    name = "scripts/qwen3_vl_reranker.py"
    path = identity_path(model_dir / name)
    raw = path.read_bytes()
    expected = lock["files"][name]
    if len(raw) != expected["bytes"] or hashlib.sha256(raw).hexdigest() != expected["sha256"]:
        raise ValueError("locked runtime source changed")
    module_spec = importlib.util.spec_from_file_location("album_qwen_reranker_frozen", path)
    module = importlib.util.module_from_spec(module_spec)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def pair_evidence(path: Path, rows: list[dict]) -> dict:
    """Bind the serialized raw scores that actually reproduce the result."""
    path = identity_path(path)
    raw = read_bound(path)
    recorded = [json.loads(line) for line in raw.splitlines()]
    if recorded != rows:
        raise ValueError("raw pair evidence differs from scored rows")
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(raw).hexdigest()}


def strict_reranker_class(module, torch):
    """Same audited official path and raw-logit scoring as the earlier probe."""
    class StrictReranker(module.Qwen3VLReranker):
        def tokenize(self, pairs, **kwargs):
            return previous.strict_tokenize(self, pairs, module.process_vision_info)

        @torch.inference_mode()
        def score_pair(self, pair, image):
            messages = self.format_mm_instruction(pair["query"], None, None, None, image, None,
                                                   instruction=SPEC["instruction"])
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
            raw = self.score_linear(hidden).float()
            torch.cuda.synchronize()
            infer_ms = (time.perf_counter() - start) * 1000
            logit = float(raw[0, 0].cpu())
            if not math.isfinite(logit):
                raise ValueError("nonfinite reranker score")
            return {"logit": logit, "tokenize_ms": tokenize_ms, "transfer_ms": transfer_ms,
                    "infer_ms": infer_ms, "token_count": int(inputs["input_ids"].shape[1]),
                    "image_grid": inputs["image_grid_thw"].cpu().tolist()}
    return StrictReranker


def verify_runtime_parameters(reranker, torch) -> None:
    for model in (reranker.model, reranker.score_linear):
        if model.training:
            raise RuntimeError("EVAL_MODE_REQUIRED")
        for parameter in model.parameters():
            if parameter.device.type != "cuda":
                raise RuntimeError("CPU_OFFLOAD_REFUSED")
            if parameter.is_floating_point() and parameter.dtype != torch.float16:
                raise RuntimeError("FIXED_FP16_PARAMETERS_REQUIRED")


class StrictRuntime:
    def __init__(self, directory: Path):
        self.directory, self.reranker, self.torch = directory, None, None

    def load(self) -> dict:
        from tools.evaluation.wemm_pilot_runtime import configure_offline, package_versions
        configure_offline(ROOT / ".tmp/album-pareto/cache/reranker")
        cuda_cache = ROOT / ".tmp/album-pareto/cache/cuda"
        cuda_cache.mkdir(parents=True, exist_ok=True)
        os.environ["CUDA_CACHE_PATH"] = str(cuda_cache)
        import torch
        self.torch = torch
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA_REQUIRED")
        if torch.cuda.mem_get_info()[0] < 6 * 1024**3:
            raise RuntimeError("LESS_THAN_6GIB_FREE_STOP; this experiment does not certify a 6GB card")
        module = load_locked_module(self.directory, previous.read_json(previous.LOCK))
        torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        self.reranker = strict_reranker_class(module, torch)(
            str(self.directory), max_pixels=SPEC["max_pixels"], min_pixels=SPEC["min_pixels"],
            torch_dtype=torch.float16, attn_implementation=SPEC["attention"], local_files_only=True)
        torch.cuda.synchronize()
        verify_runtime_parameters(self.reranker, torch)
        return {"load_ms": (time.perf_counter() - started) * 1000, "packages": package_versions(),
                "gpu": torch.cuda.get_device_name(0), "strict_no_null_fallback": True,
                "use_cache": False, "rope_reset_per_pair": True}

    def score(self, pair: dict, image) -> dict:
        return self.reranker.score_pair(pair, image)

    def resources(self) -> dict:
        from tools.evaluation.wemm_pilot_runtime import peak_ram_bytes
        return {"gpu_peak_allocated_bytes": self.torch.cuda.max_memory_allocated(),
                "gpu_peak_reserved_bytes": self.torch.cuda.max_memory_reserved(), "ram_peak_bytes": peak_ram_bytes()}

    def close(self) -> None:
        self.reranker = None
        gc.collect()
        if self.torch is not None and self.torch.cuda.is_available():
            self.torch.cuda.empty_cache()


def run(plan_path: Path, model_dir: Path, output: Path) -> dict:
    output = identity_path(output)
    output.mkdir(parents=True, exist_ok=False)
    plan_raw = read_bound(plan_path)
    plan_sha = hashlib.sha256(plan_raw).hexdigest()
    plan = json.loads(plan_raw)
    verify_plan(plan)
    model_dir = identity_path(model_dir)
    lock = previous.read_json(previous.LOCK)
    model_directory_inventory(model_dir, lock)
    previous.verify_model_files(model_dir, lock)
    from tools.evaluation.wemm_pilot_runtime import decode_image
    source_paths = [Path(__file__), Path(previous.__file__), previous.LOCK,
                    ROOT / "tools/evaluation/wemm_pilot_runtime.py", ROOT / "tools/evaluation/budget_search_metrics.py",
                    ROOT / "tools/evaluation/wemm_pilot_core.py", ROOT / "tools/evaluation/wemm_quality_pilot.py",
                    ROOT / "tools/evaluation/prepare_wemm_coco.py", ROOT / "tools/evaluation/public_search_quality.py",
                    *sorted((ROOT / "src/photo_search/media").glob("*.py"))]
    sources = {str(path.relative_to(ROOT)): previous.sha256(path) for path in source_paths}
    runtime = StrictRuntime(model_dir)
    rows = []
    pairs_path = output / "pairs.jsonl"
    with gpu_lock([model_dir, plan_path.parent]), ExitStack() as lifecycle:
        lifecycle.callback(runtime.close)
        hardware = runtime.load()
        started = time.perf_counter()
        with pairs_path.open("x", encoding="utf-8") as stream:
            for pair in plan["pairs"]:
                start = time.perf_counter()
                image, decoded = decode_image(identity_path(ROOT / pair["path"]), pair["image_sha256"])
                decode_ms = (time.perf_counter() - start) * 1000
                try:
                    score = runtime.score(pair, image)
                finally:
                    image.close()
                row = {**pair, **score, "decode_ms": decode_ms,
                       "total_ms": (time.perf_counter() - start) * 1000, "decoded": decoded}
                rows.append(row)
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                if len(rows) % 50 == 0:
                    print(json.dumps({"pairs": len(rows), "total": len(plan["pairs"])}), flush=True)
        scoring_wall_ms = (time.perf_counter() - started) * 1000
        first = plan["pairs"][0]
        image, _ = decode_image(identity_path(ROOT / first["path"]), first["image_sha256"])
        try:
            repeat = runtime.score(first, image)
        finally:
            image.close()
        delta = abs(repeat["logit"] - rows[0]["logit"])
        if not math.isfinite(delta) or delta > .02:
            raise ValueError("repeat first pair score unstable")
        resources = runtime.resources()
    rankings = merge_rankings(plan, rows)
    raw_pair_record = pair_evidence(pairs_path, rows)
    inventory = model_directory_inventory(model_dir, lock)
    verify_plan(plan)
    if previous.sha256(plan_path) != plan_sha:
        raise ValueError("candidate plan file changed while scoring")
    if any(previous.sha256(ROOT / name) != digest for name, digest in sources.items()):
        raise ValueError("runtime source changed while scoring")
    result = {"schema": "album-reranker-result-v1", "status": "COMPLETE", "plan_sha256": plan_sha,
              "input_identities": plan["input_identities"], "query_definitions": plan["query_definitions"],
              "rankings": {"qwen1024": plan["baseline_rankings"], "qwen1024_rerank_top50": rankings},
              "reranked_query_ids": plan["reranked_query_ids"], "inputs": plan["inputs"], "spec": SPEC,
              "embedding_run_identity": plan["embedding_run_identity"], "model_lock": lock,
              "model_directory_inventory": inventory, "pair_evidence": raw_pair_record,
              "source_sha256": sources, "hardware": hardware, "resources": resources,
              "pairs": len(rows), "scoring_wall_ms": scoring_wall_ms, "repeat_first_logit_delta": delta,
              "per_query_total_ms": {qid: sum(row["total_ms"] for row in rows if row["query_id"] == qid)
                                     for qid in plan["reranked_query_ids"]},
              "limitations": ["Only the preselected 20 bilingual queries are reranked; other rankings stay baseline.",
                              "Top50 candidate membership is fixed; reranking cannot recover a missing target.",
                              "Current GPU observation, not GTX1660 certification or an API latency guarantee."]}
    write_json(output / "result.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    for name in ("sample", "queries", "dataset-plan", "run-dir", "out"):
        prepare.add_argument(f"--{name}", type=Path, required=True)
    score = commands.add_parser("run")
    for name in ("plan", "model-dir", "out"):
        score.add_argument(f"--{name}", type=Path, required=True)
    for command in (prepare, score):
        command.add_argument("--junit", type=Path, required=True)
    args = parser.parse_args()
    junit, output = identity_path(args.junit), identity_path(args.out)
    if junit.exists() or output.exists():
        raise ValueError("fresh output and JUnit paths are required")
    failure, started = None, time.perf_counter()
    try:
        if args.command == "prepare":
            result = prepare_plan(args.sample, args.queries, args.dataset_plan, args.run_dir)
            write_json(output, result)
            print(json.dumps({"pairs": len(result["pairs"]), "queries": len(result["reranked_query_ids"])}))
        else:
            result = run(args.plan, args.model_dir, output)
            print(json.dumps({"status": result["status"], "pairs": result["pairs"]}))
    except BaseException as exc:
        failure = f"{type(exc).__name__}: {exc}"
        if output.is_dir():
            write_json(output / "failure.json", {"error": failure})
        raise
    finally:
        suite = ET.Element("testsuite", name="album_reranker", tests="1", failures=str(int(failure is not None)),
                           errors="0", time=str(time.perf_counter() - started))
        case = ET.SubElement(suite, "testcase", name=args.command)
        if failure:
            ET.SubElement(case, "failure", message=failure)
        junit.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(suite).write(junit, encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    main()
