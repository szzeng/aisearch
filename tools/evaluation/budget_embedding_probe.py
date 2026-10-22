"""Bounded offline Qwen/SigLIP2/FG-CLIP2 encoding for the 6GB search study.

One local CUDA model per process; no production provider or active space changes.
The same public sample works for all models. Ground-truth labels are never read.
"""

from __future__ import annotations

import argparse
import json
import time
from contextlib import ExitStack
from pathlib import Path

import numpy as np

from tools.evaluation.budget_fgclip_runtime import FgclipRuntime, verify_fgclip
from tools.evaluation.budget_gemma_runtime import GemmaRuntime, verify_gemma
from tools.evaluation.siglip_search_quality import dependency_runtime, latency_summary
from tools.evaluation.wemm_pilot_core import cache_identity, normalize_prefix
from tools.evaluation.wemm_pilot_runtime import (
    QWEN_MANIFEST,
    ModelRuntime,
    configure_offline,
    decode_image,
    decoder_identity,
    package_versions,
    peak_ram_bytes,
    sha256,
    source_identity,
    verify_model,
)
from tools.evaluation.wemm_quality_pilot import (
    atomic_json,
    checked_output,
    gpu_lock,
    load_inputs,
    read_json,
    save_vector,
)

SIGLIP = {
    "siglip-base": ("google/siglip2-base-patch16-naflex",
                    "b53b807d3a2d5e2b3911292f2d69e5341cdc064c", 768, "siglip_model_lock.json"),
    "siglip-so400m": ("google/siglip2-so400m-patch16-naflex",
                      "cc24074f717b612951c2dead130904ab9b65a81e", 1152,
                      "budget_siglip_model_lock.json"),
}
FGCLIP_MODELS = ("fgclip-base", "fgclip-base-adaptive")


def verify_siglip(name: str, directory: Path) -> dict:
    model_id, revision, dimension, filename = SIGLIP[name]
    lock = read_json(Path(__file__).with_name(filename))
    if lock["model_id"] != model_id or lock["revision"] != revision:
        raise ValueError("fixed model lock identity mismatch")
    for path in directory.iterdir():
        if path.is_symlink() or path.is_junction():
            raise ValueError("linked model content refused")
        if path.name not in lock["files"] and not (path.name == ".cache" and path.is_dir()):
            raise ValueError("unverified runtime file beside model: " + path.name)
    for basename, record in lock["files"].items():
        if Path(basename).name != basename:
            raise ValueError("invalid model lock filename")
        expected = record if isinstance(record, str) else record["sha256"]
        path = directory / basename
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f"model content differs: {basename}")
    config = read_json(directory / "config.json")
    processor = read_json(directory / "preprocessor_config.json")
    if (config.get("model_type") != "siglip2"
            or config["text_config"].get("hidden_size", 768) != dimension
            or config["vision_config"].get("hidden_size", 768) != dimension
            or processor.get("max_num_patches") != 256 or processor.get("patch_size") != 16):
        raise ValueError("official architecture/preprocessor differs")
    return lock


def validated_feature(value: np.ndarray, dimension: int) -> np.ndarray:
    values = np.asarray(value, dtype=np.float32)
    if values.shape != (1, dimension):
        raise ValueError("expected exactly one native-dimensional feature")
    return normalize_prefix(values, dimension)[0]


class SiglipRuntime:
    def __init__(self, name: str, directory: Path, dtype: str):
        self.name, self.directory, self.dtype = name, directory, dtype
        self.dimension = SIGLIP[name][2]
        self.torch = self.model = self.processor = None

    def load(self) -> dict:
        import torch
        from transformers import AutoModel, AutoProcessor

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA required; CPU/offload is not implicit")
        self.torch = torch
        torch.cuda.set_device(0)
        torch.cuda.reset_peak_memory_stats()
        self.processor = AutoProcessor.from_pretrained(
            self.directory, local_files_only=True, trust_remote_code=False,
        )
        self.model = AutoModel.from_pretrained(
            self.directory, local_files_only=True, trust_remote_code=False,
            torch_dtype=torch.float16 if self.dtype == "fp16" else torch.float32,
            attn_implementation="eager",
        ).to("cuda:0").eval()
        if (self.model.config.text_config.hidden_size != self.dimension
                or self.model.config.vision_config.hidden_size != self.dimension):
            raise ValueError("resolved native model dimensions differ")
        expected = torch.float16 if self.dtype == "fp16" else torch.float32
        if any(p.device != torch.device("cuda:0") or p.dtype != expected
               for p in self.model.parameters()):
            raise ValueError("implicit dtype/offload refused")
        self.sync()
        return dependency_runtime(torch, self.processor, self.model)

    def sync(self) -> None:
        self.torch.cuda.synchronize()

    def encode(self, *, text: str | None = None, image=None) -> np.ndarray:
        if (text is None) == (image is None):
            raise ValueError("one input required")
        if text is not None:
            inputs = self.processor(text=[text.lower()], padding="max_length", max_length=64,
                                    truncation=True, return_tensors="pt")
        else:
            inputs = self.processor(images=image, max_num_patches=256, patch_size=16,
                                    return_tensors="pt")
        inputs = {key: value.to(device="cuda:0", dtype=(next(self.model.parameters()).dtype
                  if value.is_floating_point() else value.dtype)) for key, value in inputs.items()}
        with self.torch.inference_mode():
            method = self.model.get_text_features if text is not None else self.model.get_image_features
            vector = method(**inputs).detach().float().cpu().numpy()
        return vector

    def resources(self) -> dict:
        return {"peak_ram_bytes": peak_ram_bytes(),
                "gpu_peak_allocated_bytes": self.torch.cuda.max_memory_allocated(),
                "gpu_peak_reserved_bytes": self.torch.cuda.max_memory_reserved()}

    def close(self) -> None:
        self.model = self.processor = None
        if self.torch is not None:
            self.torch.cuda.empty_cache()


def run(args: argparse.Namespace) -> dict:
    if args.dtype not in ("fp16", "fp32") or (args.model == "qwen" and args.dtype != "fp16"):
        raise ValueError("Qwen uses fixed fp16; SigLIP allows explicit fp16/fp32")
    if args.model == "embeddinggemma2" and args.dtype != "fp32":
        raise ValueError("EmbeddingGemma2 FP16 is unsafe; use explicit FP32")
    if getattr(args, "album_budget", False):
        sample, queries = load_inputs(args.sample, args.queries, 2000, album_budget=True)
    else:
        sample, queries = load_inputs(args.sample, args.queries, 1000)
    if sample.get("schema") != "wemm-pilot-public-sample-v1" and not str(
            sample.get("schema", "")).startswith("synthetic-"):
        raise ValueError("this probe requires public or synthetic data")
    if args.limit is not None and not 1 <= args.limit <= len(sample["assets"]):
        raise ValueError("limit outside frozen sample")
    assets = sample["assets"][:args.limit]
    input_roots = [args.model_dir.resolve(), args.sample.parent.resolve(), args.queries.parent.resolve(),
                   *(Path(row["path"]).resolve().parent for row in assets)]
    directory = checked_output(args.out_dir, input_roots)
    # A run never resumes with partially changed identity or overwrites prior failures.
    directory.mkdir(parents=True, exist_ok=False)
    configure_offline(directory / "runtime-cache")
    result = {"schema": "budget-embedding-run-v1", "status": "RUNNING", "model": args.model,
              "dtype": args.dtype, "assets": assets, "queries": queries,
              "images_ms": [], "queries_ms": [], "decode_ms": [], "decode_records": [],
              "settings": {"limit": args.limit, "batch": 1, "device": "cuda:0"}}
    runtime = None
    start = time.perf_counter()
    try:
        with gpu_lock(input_roots), ExitStack() as lifecycle:
            if args.model == "qwen":
                lock = verify_model("qwen", args.model_dir)
                manifest = read_json(QWEN_MANIFEST)
                manifest["provider"]["provider_id"] = "qwen-pilot-budget-native2048"
                manifest["embedding"]["dimension"] = 2048
                manifest_path = directory / "experimental-manifest.json"
                atomic_json(manifest_path, manifest)
                runtime = ModelRuntime("qwen", args.model_dir, "cuda:0", "fp16", manifest_path)
                dimension = 2048
                result["settings"]["qwen_manifest"] = manifest
            elif args.model == "embeddinggemma2":
                lock = verify_gemma(args.model_dir)
                runtime = GemmaRuntime(args.model_dir, args.dtype)
                dimension = 768
                result["settings"]["gemma"] = {
                    "audio_encoder": False, "vision_soft_tokens_per_image": 280,
                    "query_prompt_name": "SearchQuery", "image_prompt": None,
                    "attention": "eager", "dtype": "fp32",
                }
            elif args.model in FGCLIP_MODELS:
                lock = verify_fgclip(args.model_dir)
                adaptive = args.model == "fgclip-base-adaptive"
                runtime = FgclipRuntime(args.model_dir, args.dtype, adaptive=adaptive)
                dimension = 768
                result["settings"]["fgclip"] = {
                    "patches": "adaptive" if adaptive else 256,
                    "patch_budget_policy": "official_determine_max_value" if adaptive else "fixed_256",
                    "adaptive_patch_limits": [128, 256, 576, 784, 1024] if adaptive else None,
                    "adaptive_size_basis": "decoded_width//16 * decoded_height//16" if adaptive else None,
                    "patch_size": 16, "max_length": 64,
                    "text_transform": "lower", "walk_type": "short", "attention": "eager",
                    "text_attention_mask": False,
                    "official_code_pinned": True,
                }
            else:
                lock = verify_siglip(args.model, args.model_dir)
                runtime = SiglipRuntime(args.model, args.model_dir, args.dtype)
                dimension = SIGLIP[args.model][2]
                result["settings"]["siglip"] = {
                    "patches": 256, "patch_size": 16, "max_length": 64,
                    "text_transform": "lower", "attention": "eager",
                }
            lifecycle.callback(runtime.close)
            result.update(native_dimension=dimension, model_lock=lock,
                          packages=package_versions(), decoder=decoder_identity(),
                          source_sha256={**source_identity(), __file__: sha256(Path(__file__)),
                                         "budget_fgclip_runtime.py": sha256(
                                             Path(__file__).with_name("budget_fgclip_runtime.py")),
                                         "budget_gemma_runtime.py": sha256(
                                             Path(__file__).with_name("budget_gemma_runtime.py"))},
                          sample_sha256=sha256(args.sample), queries_sha256=sha256(args.queries))
            result["identity"] = cache_identity(
                {key: result[key] for key in ("model", "dtype", "settings", "model_lock", "packages",
                                             "decoder", "source_sha256", "native_dimension")},
                [{"asset_id": r["asset_id"], "sha256": r["sha256"]} for r in assets], queries,
            )
            phase = time.perf_counter()
            result["runtime"] = runtime.load()
            result["load_seconds"] = time.perf_counter() - phase
            query_vectors, image_vectors = [], []
            for query in queries:
                runtime.sync()
                phase = time.perf_counter()
                vector = validated_feature(runtime.encode(text=query["text"]), dimension)
                runtime.sync()
                result["queries_ms"].append((time.perf_counter() - phase) * 1000)
                query_vectors.append(vector)
            for index, row in enumerate(assets):
                runtime.sync()
                phase = time.perf_counter()
                image, record = decode_image(Path(row["path"]), row["sha256"])
                result["decode_ms"].append((time.perf_counter() - phase) * 1000)
                try:
                    vector = validated_feature(runtime.encode(image=image), dimension)
                    runtime.sync()
                    result["images_ms"].append((time.perf_counter() - phase) * 1000)
                    if args.model in FGCLIP_MODELS:
                        record["fgclip_max_num_patches"] = runtime.patch_budget(image)
                    result["decode_records"].append(record)
                    image_vectors.append(vector)
                finally:
                    image.close()
                if (index + 1) % 50 == 0:
                    print(f"{args.model}: {index + 1}/{len(assets)}", flush=True)
            result["vectors_sha256"] = {
                "images.npy": save_vector(directory / "images.npy", np.asarray(image_vectors)),
                "queries.npy": save_vector(directory / "queries.npy", np.asarray(query_vectors)),
            }
            result["resources"] = runtime.resources()
            result["image_latency"] = latency_summary([x / 1000 for x in result["images_ms"]])
            result["status"] = "COMPLETE"
    except Exception as exc:
        result.update(status="FAILED", failure={"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        result["wall_seconds"] = time.perf_counter() - start
        atomic_json(directory / "result.json", result)
    return {key: result[key] for key in ("status", "model", "dtype", "image_latency", "resources")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True,
                        choices=("qwen", *SIGLIP, *FGCLIP_MODELS, "embeddinggemma2"))
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--sample", required=True, type=Path)
    parser.add_argument("--queries", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--dtype", choices=("fp16", "fp32"), default="fp16")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--album-budget", action="store_true",
                        help="Explicit whole-album experiment budget: <=2000 images, <=200 queries")
    print(json.dumps(run(parser.parse_args()), ensure_ascii=False))


if __name__ == "__main__":
    main()
