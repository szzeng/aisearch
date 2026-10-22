"""Pinned, local-only RAM++ 4585-category album experiment; no production writes.

Use the separate research environment recorded by album_tagging_lock.json. Official
source runs from verified bytes (never stale pyc); the checkpoint uses weights-only
loading. The official generate_tag path determines both scores and selected tags.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.abc
import importlib.metadata
import importlib.util
import json
import platform
import sys
import time
from pathlib import Path

import numpy as np

from tools.evaluation.wemm_pilot_runtime import (
    configure_offline,
    decode_image,
    decoder_identity,
    peak_ram_bytes,
    sha256,
)

ROOT = Path(__file__).resolve().parents[2]
LOCK = Path(__file__).with_name("album_tagging_lock.json")
CLASS_COUNT = 4585
MAX_IMAGES = 2000


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def local_path(path: Path) -> Path:
    path = path.absolute()
    if not path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("experiment paths must stay in the workspace")
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ValueError("linked experiment paths refused")
    return path


def verify_file(path: Path, record: dict) -> None:
    path = local_path(path)
    if not path.is_file() or path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
        raise ValueError("pinned file differs: " + path.name)


def verify_runtime(lock: dict) -> dict:
    actual = {name: platform.python_version() if name == "python" else importlib.metadata.version(name)
              for name in lock["runtime"]}
    if actual != lock["runtime"]:
        raise ValueError("separate RAM++ environment does not match the pinned runtime")
    return actual


def verify_assets(source: Path, model: Path, tokenizer: Path, lock: dict) -> None:
    for name, record in lock["source"]["files"].items():
        verify_file(source / name, record)
    # Reject extra Python modules/configs that could otherwise be auto-imported.
    known = set(lock["source"]["files"])
    for path in (source / "ram").rglob("*"):
        local_path(path)
        if path.is_file() and path.relative_to(source).as_posix() not in known:
            raise ValueError("unverified file in official runtime source")
    verify_file(model, lock["model"])
    for name, record in lock["tokenizer"]["files"].items():
        verify_file(tokenizer / name, record)
    if set(p.name for p in tokenizer.iterdir()) != set(lock["tokenizer"]["files"]):
        raise ValueError("unverified tokenizer files")


class LockedSourceLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Import only locked ram modules, compiling the bytes that were verified."""

    def __init__(self, directory: Path, files: dict):
        self.directory, self.files = directory, files

    def _path(self, name: str) -> tuple[Path, bool]:
        base = name.replace(".", "/")
        for relative, package in ((base + "/__init__.py", True), (base + ".py", False)):
            if relative in self.files:
                return self.directory / relative, package
        raise ImportError("unlocked RAM module refused: " + name)

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "ram" and not fullname.startswith("ram."):
            return None
        resolved, package = self._path(fullname)
        return importlib.util.spec_from_file_location(
            fullname, resolved, loader=self,
            submodule_search_locations=[str(resolved.parent)] if package else None,
        )

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        path, _ = self._path(module.__name__)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.files[path.relative_to(self.directory).as_posix()]["sha256"]:
            raise ImportError("official RAM source changed while importing")
        exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)


def selected_tags(scores: np.ndarray, thresholds: np.ndarray,
                  english: list[str], chinese: list[str]) -> dict:
    if (scores.shape != (CLASS_COUNT,) or thresholds.shape != (CLASS_COUNT,)
            or len(english) != CLASS_COUNT or len(chinese) != CLASS_COUNT
            or not np.isfinite(scores).all() or not np.isfinite(thresholds).all()
            or np.any((scores < 0) | (scores > 1)) or np.any((thresholds < 0) | (thresholds > 1))):
        raise ValueError("invalid category scores, labels or thresholds")
    indices = np.flatnonzero(scores > thresholds).tolist()
    return {"indices": indices, "english": " | ".join(english[i] for i in indices),
            "chinese": " | ".join(chinese[i] for i in indices)}


def load_sample(path: Path, limit: int | None) -> tuple[dict, list[dict]]:
    local_path(path)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("sample exceeds bounded size")
    sample = json.loads(path.read_bytes())
    if sample.get("schema") not in {"wemm-pilot-public-sample-v1", "album-broad-tags-sample-v1",
                                    "broad-album-data-v1"}:
        raise ValueError("explicit public/synthetic album sample required")
    rows = sample.get("assets", [])
    if not 1 <= len(rows) <= MAX_IMAGES or len({r["asset_id"] for r in rows}) != len(rows):
        raise ValueError("invalid or duplicate sample membership")
    if limit is not None and (type(limit) is not int or not 1 <= limit <= len(rows)):
        raise ValueError("limit outside frozen sample")
    rows = rows[:limit]
    for row in rows:
        if sha256(local_path(Path(row["path"]))) != row["sha256"]:
            raise ValueError("sample image bytes changed")
    return sample, rows


def validate_checkpoint_keys(missing: list[str], unexpected: list[str], buffers: set[str]) -> None:
    """Allow only the frozen official checkpoint's documented naming mismatch.

    Official RAM++ has used reweight_scale since its first release, whereas this
    checkpoint stores logit_scale. Its official strict=False loader intentionally
    or accidentally uses the constructor constant. Preserve that baseline rather
    than silently making up a rename; any other missing parameter still fails.
    """
    if set(missing) - buffers != {"reweight_scale"} or unexpected != ["logit_scale"]:
        raise ValueError(f"checkpoint incomplete: missing={missing}; unexpected={unexpected}")


def verify_run_identity(result: dict, sample: Path) -> None:
    if sha256(sample) != result["sample_sha256"]:
        raise ValueError("sample manifest changed during inference")
    if (any(sha256(ROOT / name) != expected for name, expected in result["source_files_sha256"].items())
            or sha256(LOCK) != result["lock_sha256"] or decoder_identity() != result["decoder"]):
        raise ValueError("probe/runtime/decoder/lock changed during inference")


def load_model(source: Path, weights_path: Path, tokenizer: Path, dtype: str, lock: dict):
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required; CPU/offload is never implicit")
    if any(name == "ram" or name.startswith("ram.") for name in sys.modules):
        raise RuntimeError("RAM already imported outside the verified loader")
    loader = LockedSourceLoader(source, lock["source"]["files"])
    sys.meta_path.insert(0, loader)
    from ram import get_transform
    from ram.models.ram_plus import RAM_plus
    from ram.models.swin_transformer import interpolate_relative_pos_embed

    # Constructor and inference remain official. Reproduce the official Swin-L
    # checkpoint mapping but expose missing keys and require weights-only loading.
    model = RAM_plus(image_size=384, vit="swin_l", text_encoder_type=str(tokenizer), stage="eval")
    checkpoint = torch.load(weights_path, map_location="cpu", weights_only=True)
    state = checkpoint["model"]
    del checkpoint
    for key in list(state):
        if "relative_position_bias_table" in key:
            state[key] = interpolate_relative_pos_embed(state[key], (2 * 12 - 1) ** 2, param_name=key)
        elif "relative_position_index" in key or "attn_mask" in key:
            del state[key]
        elif "vision_multi" in key:
            state[key.replace("vision_multi", "tagging_head")] = state.pop(key)
    ignored_scale = float(state["logit_scale"].item())
    constructor_scale = float(model.reweight_scale.item())
    if ignored_scale != 2.630937099456787 or constructor_scale != 2.6592600345611572:
        raise ValueError("official checkpoint/default scale differs from documented pinned values")
    missing, unexpected = model.load_state_dict(state, strict=False)
    del state
    allowed = {name for name, _ in model.named_buffers()
               if "relative_position_index" in name or "attn_mask" in name}
    validate_checkpoint_keys(missing, unexpected, allowed)
    precision = torch.float16 if dtype == "fp16" else torch.float32
    model = model.eval().to(device="cuda:0", dtype=precision)
    if any(parameter.device != torch.device("cuda:0") or parameter.dtype != precision
           for parameter in model.parameters()):
        raise ValueError("model dtype/device differs from explicit runtime")
    return model, get_transform(image_size=384), {"missing_keys": missing,
                                                "unexpected": unexpected,
                                                "official_scale_compatibility": {
                                                    "ignored_checkpoint_logit_scale": ignored_scale,
                                                    "used_constructor_reweight_scale": constructor_scale,
                                                    "policy": "match official strict=False loader; no rename"},
                                                "parameter_count": sum(p.numel() for p in model.parameters())}


def run(args: argparse.Namespace) -> dict:
    source, model_path, tokenizer = map(local_path, (args.source_dir, args.model, args.tokenizer_dir))
    out = local_path(args.out_dir)
    lock = json.loads(LOCK.read_bytes())
    runtime = verify_runtime(lock)
    verify_assets(source, model_path, tokenizer, lock)
    _, rows = load_sample(args.sample, args.limit)
    out.mkdir(parents=True, exist_ok=False)
    configure_offline(out / "runtime-cache")
    result = {"schema": "album-ram-plus-run-v1", "status": "RUNNING", "assets": rows,
              "lock_sha256": sha256(LOCK), "source_sha256": sha256(Path(__file__)),
              "source_files_sha256": {
                  path.relative_to(ROOT).as_posix(): sha256(path)
                  for path in (Path(__file__).resolve(),
                               Path(__file__).with_name("wemm_pilot_runtime.py").resolve())},
              "sample_sha256": sha256(args.sample), "dataset_manifest_sha256": sha256(args.sample),
              "model_lock": lock, "runtime": runtime, "score_type": "sigmoid_scores",
              "decoder": decoder_identity(), "settings": {"batch": 1, "dtype": args.dtype,
                      "image_size": 384, "device": "cuda:0", "limit": args.limit},
              "decode_preprocess_upload_ms": [], "inference_ms": [], "predictions": [],
              "limitations": ["4585 fixed vocabulary; not arbitrary open-world recognition.",
                              "Scores are sigmoid outputs, not calibrated probabilities.",
                              "Official class thresholds are unchanged, not fitted on this album.",
                              "Current GPU timings do not certify GTX1660 or 300k indexing."]}
    started = time.perf_counter()
    try:
        import torch

        torch.cuda.set_device(0)
        torch.cuda.reset_peak_memory_stats()
        model, transform, checkpoint = load_model(source, model_path, tokenizer, args.dtype, lock)
        result["load_seconds"] = time.perf_counter() - started
        result["checkpoint"] = checkpoint
        result["gpu"] = {"name": torch.cuda.get_device_name(),
                         "total_bytes": torch.cuda.get_device_properties(0).total_memory}
        english, chinese = model.tag_list.tolist(), model.tag_list_chinese.tolist()
        thresholds = model.class_threshold.cpu().numpy().astype(np.float32)
        result["classes"] = [{"index": i, "english": en, "chinese": cn, "threshold": float(t)}
                                for i, (en, cn, t) in enumerate(zip(english, chinese, thresholds, strict=True))]
        captured = []

        def capture_scores(module, inputs, output):
            # Preserve the same dtype and sigmoid operation as official generate_tag.
            captured.append(torch.sigmoid(output.squeeze(-1)).detach().float().cpu().numpy())

        handle = model.fc.register_forward_hook(capture_scores)
        scores = []
        for row in rows:
            phase = time.perf_counter()
            image, decode_record = decode_image(Path(row["path"]), row["sha256"])
            try:
                tensor = transform(image).unsqueeze(0).to(device="cuda:0", dtype=next(model.parameters()).dtype)
            finally:
                image.close()
            torch.cuda.synchronize()
            result["decode_preprocess_upload_ms"].append((time.perf_counter() - phase) * 1000)
            captured.clear()
            phase = time.perf_counter()
            with torch.inference_mode():
                actual_en, actual_cn = model.generate_tag(tensor)
            torch.cuda.synchronize()
            result["inference_ms"].append((time.perf_counter() - phase) * 1000)
            if len(captured) != 1 or captured[0].shape != (1, CLASS_COUNT):
                raise ValueError("official head output shape/call count differs")
            score = captured[0][0]
            selected = selected_tags(score, thresholds, english, chinese)
            if [selected["english"]] != actual_en or [selected["chinese"]] != actual_cn:
                raise ValueError("recorded scores disagree with official selected tags")
            result["predictions"].append({"asset_id": row["asset_id"], "tags": selected, "decode": decode_record})
            scores.append(score)
            if len(scores) % 50 == 0:
                print(json.dumps({"completed": len(scores), "total": len(rows)}), flush=True)
        handle.remove()
        np.save(out / "scores.npy", np.stack(scores), allow_pickle=False)
        result["scores_sha256"] = sha256(out / "scores.npy")
        result["resources"] = {"gpu_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                               "gpu_peak_reserved_bytes": torch.cuda.max_memory_reserved(),
                               "peak_ram_bytes": peak_ram_bytes()}
        verify_assets(source, model_path, tokenizer, lock)
        load_sample(args.sample, args.limit)
        verify_run_identity(result, args.sample)
        result["status"] = "PASS"
    except Exception as error:
        result.update(status="FAIL", error=str(error), error_type=type(error).__name__)
        raise
    finally:
        result["elapsed_seconds"] = time.perf_counter() - started
        write_json(out / "result.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--tokenizer-dir", required=True, type=Path)
    parser.add_argument("--sample", required=True, type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--dtype", choices=("fp16", "fp32"), default="fp16")
    result = run(parser.parse_args())
    print(json.dumps({"status": result["status"], "images": len(result["assets"]),
                      "elapsed_seconds": result["elapsed_seconds"], "resources": result["resources"]}))


if __name__ == "__main__":
    main()
