"""Offline PhotoPrism S2 baseline: pinned weights, complete scores, CPU only.

This is a Pillow reproduction of PhotoPrism's prepared-input geometry, not a
bit-exact Go/libvips or end-to-end PhotoPrism benchmark. ImageNet softmax scores
are not independent object-presence probabilities. No production data is written.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

from tools.evaluation.prepare_wemm_coco import ROOT, controlled_output, write_json
from tools.evaluation.wemm_pilot_runtime import (
    decode_image,
    decoder_identity,
    peak_ram_bytes,
    sha256,
)

COMMIT = "265ce19e51f911ec8d6a1a531987530908a36273"
RAW = f"https://raw.githubusercontent.com/photoprism/photoprism/{COMMIT}/"
FILES = {
    "efficientformerv2_s2.onnx": (
        "https://dl.photoprism.app/onnx/models/efficientformerv2_s2.onnx",
        "7d6288ad89473c2ba11974c9c24a96e07c4e3ee48c531081f108a6380dc8d0ab", 50790993),
    "labels.txt": (RAW + "internal/ai/classify/labels.txt",
                   "7137cf8c32076dfd6369ce6b08f2e26207164a385141a61aff0ae7c4a8fcdc13", 11067),
    "rules.go": (RAW + "internal/ai/classify/rules.go",
                 "91fcc3202aede821912eba8f4020f8539247840aa08bf193339ed92a80e58497", 142775),
    "input-geometry.go": (RAW + "internal/photoprism/mediafile_label_inputs.go",
                          "8175c448d0502ba0cb023908b35fe063813684c22860c511040473f2b3353d1a", 5753),
    "model-contract.go": (RAW + "internal/ai/classify/models.go",
                          "f6bf62d563bf1842c6cc4faf152ead3ed4cafdcda164974952faba3dc9824b8d", 6126),
    "photoprism-LICENSE": (RAW + "LICENSE",
                           "f703a5ce3a8b0f68d3b19327bac8e881d537b80732f7deebb8f0ba2fb63bc982", 33824),
}
PREPROCESS = {
    "policy": "photoprism-s2-prepared-geometry-pillow-v1",
    "geometry_source": RAW + "internal/photoprism/mediafile_label_inputs.go",
    "interpolation": "Pillow-bicubic; not pixel-identical to Go/libvips",
    "normalization_source": RAW + "internal/ai/classify/models.go",
    "layout": "NCHW", "color": "RGB", "shape": [1, 3, 224, 224],
    "mean_255": [123.675, 116.28, 103.53], "std_255": [58.395, 57.12, 57.375],
    "pooling": "per-class maximum of per-view softmax; not normalized across classes",
    "threshold": None,
}


def workspace_file(path: Path, *, maximum: int) -> Path:
    path = path.resolve()
    if not path.is_relative_to(ROOT) or not path.is_file():
        raise ValueError("input must be a workspace file")
    if path.stat().st_size > maximum:
        raise ValueError("input exceeds size bound")
    return path


def verify_files(directory: Path) -> dict:
    result = {}
    for name, (url, expected, size) in FILES.items():
        path = workspace_file(directory / name, maximum=size)
        if path.stat().st_size != size or sha256(path) != expected:
            raise ValueError(f"pinned asset mismatch: {name}")
        result[name] = {"url": url, "sha256": expected, "bytes": size}
    return {"photoprism_commit": COMMIT, "model_license": "Apache-2.0",
            "mapping_license": "PhotoPrism AGPL-3.0; downloaded research input only",
            "source_checkpoint_revision": "1c56a76355000c79568559d34ba3fa24416b5107",
            "files": result}


def prepare(directory: Path) -> dict:
    """Only this explicit command downloads; inference has no network path."""
    directory = controlled_output(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, (url, expected, size) in FILES.items():
        path = controlled_output(directory / name)
        if path.exists():
            if path.stat().st_size != size or sha256(path) != expected:
                raise ValueError("existing asset differs; refusing to overwrite")
            continue
        partial = controlled_output(directory / (name + ".partial"))
        with partial.open("xb") as target, urllib.request.urlopen(url, timeout=60) as source:
            remaining = size
            while block := source.read(min(1024 * 1024, remaining + 1)):
                remaining -= len(block)
                if remaining < 0:
                    raise ValueError("download exceeds pinned size")
                target.write(block)
        if partial.stat().st_size != size or sha256(partial) != expected:
            raise ValueError("downloaded asset checksum differs")
        partial.rename(path)
    lock = verify_files(directory)
    write_json(directory / "model-lock.json", lock)
    return lock


def parse_mapping(labels_text: str, rules_source: str) -> tuple[list[str], list[dict]]:
    labels = labels_text.strip().splitlines()
    # ImageNet has distinct synsets with the same display name (crane/maillot).
    # Class identity is the output index; never collapse duplicate display names.
    if len(labels) != 1000 or any(not x.strip() for x in labels):
        raise ValueError("expected canonical 1000-class vocabulary")
    pattern = (r'\t("(?:[^"\\]|\\.)*"): \{\s*Label:\s*("(?:[^"\\]|\\.)*"),'
               r'\s*Threshold:\s*([0-9.]+),\s*Priority:\s*(-?\d+),'
               r'\s*Categories: \[\]string\{([^}]*)\},\s*\},')
    rules = {}
    for name, label, threshold, priority, categories in re.findall(pattern, rules_source):
        key = json.loads(name)
        if key in rules:
            raise ValueError("duplicate mapping key")
        rules[key] = {"label": json.loads(label), "threshold": float(threshold),
                      "priority": int(priority), "categories": json.loads("[" + categories + "]")}
    # Verify every source rule parsed: a format change must not silently use defaults.
    if len(rules) != len(re.findall(r'^\t".*": \{', rules_source, re.MULTILINE)) or not rules:
        raise ValueError("unsupported official mapping format")
    mapping = []
    for index, name in enumerate(labels):
        rule = rules.get(name.lower(), {"label": "", "threshold": .1, "priority": 0, "categories": []})
        mapping.append({"index": index, "name": name, **rule,
                        "source": "photoprism-rule" if name.lower() in rules else "photoprism-default"})
    return labels, mapping


def regions(width: int, height: int) -> list[list[int]]:
    if width <= 0 or height <= 0:
        raise ValueError("invalid image dimensions")
    side = min(width, height)
    left, top = (width - side) // 2, (height - side) // 2
    result = [[left, top, left + side, top + side]]
    if width != height and side >= 224:
        crop_w, crop_h = min(width, height * 4 // 3), min(height, width * 4 // 3)
        left, top = (width - crop_w) // 2, (height - crop_h) // 2
        result.append([left, top, left + crop_w, top + crop_h])
    return result


def tensors(image: Image.Image) -> tuple[list[np.ndarray], list[list[int]]]:
    if image.mode != "RGB":
        raise ValueError("decoder must provide RGB")
    boxes = regions(*image.size)
    result = []
    mean = np.asarray(PREPROCESS["mean_255"], np.float32)
    std = np.asarray(PREPROCESS["std_255"], np.float32)
    for box in boxes:
        with image.crop(box) as crop, crop.resize((224, 224), Image.Resampling.BICUBIC) as resized:
            pixels = (np.asarray(resized, dtype=np.float32) - mean) / std
        result.append(np.ascontiguousarray(pixels.transpose(2, 0, 1)[None]))
    return result, boxes


def pool_logits(logits: np.ndarray) -> np.ndarray:
    values = np.asarray(logits, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 1000 or values.shape[0] not in (1, 2):
        raise ValueError("unexpected model output shape")
    if not np.isfinite(values).all():
        raise ValueError("nonfinite model output")
    weights = np.exp(values - values.max(axis=1, keepdims=True))
    return (weights / weights.sum(axis=1, keepdims=True)).max(axis=0).astype(np.float32)


def validate_session(session) -> dict:
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if (len(inputs) != 1 or inputs[0].name != "input" or inputs[0].shape != [1, 3, 224, 224]
            or inputs[0].type != "tensor(float)" or len(outputs) != 1
            or outputs[0].name != "logits" or outputs[0].shape != [1, 1000]
            or outputs[0].type != "tensor(float)"):
        raise ValueError("ONNX contract differs")
    metadata = session.get_modelmeta().custom_metadata_map
    if metadata.get("photoprism.logits") != "true":
        raise ValueError("logit semantics missing")
    if session.get_providers() != ["CPUExecutionProvider"]:
        raise ValueError("CPU-only run required")
    return metadata


def read_manifest(path: Path, limit: int) -> tuple[dict, list[dict], str]:
    path = workspace_file(path, maximum=32 * 1024 * 1024)
    raw = path.read_bytes()
    document = json.loads(raw)
    assets = document.get("assets", [])
    if document.get("schema") != "broad-album-data-v1" or not 1 <= len(assets) <= 5000:
        raise ValueError("unsupported bounded manifest")
    if len({str(x["asset_id"]) for x in assets}) != len(assets):
        raise ValueError("duplicate asset identity")
    if not 0 <= limit <= len(assets):
        raise ValueError("invalid smoke limit")
    selected = assets[:limit] if limit else assets
    for asset in selected:
        workspace_file(Path(asset["path"]), maximum=128 * 1024 * 1024)
        if not re.fullmatch("[0-9a-f]{64}", asset["sha256"]):
            raise ValueError("invalid image hash")
    return document, selected, hashlib.sha256(raw).hexdigest()


def source_identity() -> dict[str, str]:
    paths = [Path(__file__), Path(__file__).with_name("wemm_pilot_runtime.py"),
             Path(__file__).with_name("prepare_wemm_coco.py")]
    return {str(path.resolve().relative_to(ROOT)).replace("\\", "/"): sha256(path)
            for path in paths}


def run(manifest: Path, model_dir: Path, output: Path, *, limit: int = 0, threads: int = 2) -> dict:
    if not 1 <= threads <= 8:
        raise ValueError("CPU threads must be between 1 and 8")
    sources_before, decoder_before = source_identity(), decoder_identity()
    document, assets, manifest_sha = read_manifest(manifest, limit)
    lock = verify_files(model_dir)
    labels, mapping = parse_mapping((model_dir / "labels.txt").read_text(encoding="utf-8"),
                                    (model_dir / "rules.go").read_text(encoding="utf-8"))
    output = controlled_output(output)
    output.mkdir(parents=True, exist_ok=False)
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads, options.inter_op_num_threads = threads, 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    load_started = time.perf_counter()
    session = ort.InferenceSession(str(model_dir / "efficientformerv2_s2.onnx"),
                                  sess_options=options, providers=["CPUExecutionProvider"])
    metadata = validate_session(session)
    load_ms = (time.perf_counter() - load_started) * 1000
    warm = time.perf_counter()
    warm_logits = session.run(["logits"], {"input": np.zeros((1, 3, 224, 224), np.float32)})[0]
    pool_logits(warm_logits)
    warmup_ms = (time.perf_counter() - warm) * 1000
    scores = np.empty((len(assets), 1000), np.float32)
    view_logits = np.zeros((len(assets), 2, 1000), np.float32)
    per_image = []
    started = time.perf_counter()
    for index, asset in enumerate(assets):
        t0 = time.perf_counter()
        image, decoded = decode_image(Path(asset["path"]), asset["sha256"])
        t1 = time.perf_counter()
        try:
            inputs, boxes = tensors(image)
        finally:
            image.close()
        t2 = time.perf_counter()
        logits = np.concatenate([session.run(["logits"], {"input": v})[0] for v in inputs])
        scores[index] = pool_logits(logits)
        view_logits[index, :len(inputs)] = logits
        t3 = time.perf_counter()
        per_image.append({"asset_id": asset["asset_id"], "decode_ms": (t1-t0)*1000,
                          "preprocess_ms": (t2-t1)*1000, "inference_ms": (t3-t2)*1000,
                          "total_ms": (t3-t0)*1000, "view_count": len(inputs),
                          "regions": boxes, "decoded": decoded})
        if (index + 1) % 100 == 0:
            print(json.dumps({"completed": index + 1, "total": len(assets)}), flush=True)
    elapsed_ms = (time.perf_counter() - started) * 1000
    if (sha256(manifest.resolve()) != manifest_sha or verify_files(model_dir) != lock
            or source_identity() != sources_before or decoder_identity() != decoder_before):
        raise ValueError("run inputs changed")
    np.save(output / "scores.npy", scores, allow_pickle=False)
    np.save(output / "view_logits.npy", view_logits, allow_pickle=False)
    result = {
        "schema": "album-tagging-run-v1", "dataset": document["dataset"],
        "manifest_sha256": manifest_sha, "manifest_path": str(manifest.resolve()),
        "sample_sha256": manifest_sha, "smoke_limit": limit,
        "assets": [{k: row[k] for k in ("asset_id", "path", "sha256")} for row in assets],
        "classes": labels, "class_ids": list(range(1000)), "class_mapping": mapping,
        "mapping_source": FILES["rules.go"][0],
        "scores_path": str(output / "scores.npy"), "scores_sha256": sha256(output / "scores.npy"),
        "score_type": "probabilities", "score_semantics": PREPROCESS["pooling"],
        "view_logits_path": str(output / "view_logits.npy"),
        "view_logits_sha256": sha256(output / "view_logits.npy"),
        "view_logits_padding": "zero; ignore view slots >= per_image_ms.view_count",
        "model_lock": lock, "graph_metadata": metadata, "preprocess": PREPROCESS,
        "decoder": decoder_before, "per_image_ms": per_image,
        "resources": {"device": "cpu", "providers": session.get_providers(),
                      "intra_op_threads": threads, "peak_ram_bytes": peak_ram_bytes(),
                      "model_load_ms": load_ms, "warmup_ms": warmup_ms,
                      "measured_images_ms": elapsed_ms, "hardware": platform.processor(),
                      "packages": {"onnxruntime": ort.__version__,
                                   **{name: importlib.metadata.version(name)
                                      for name in ("numpy", "pillow")}},
                      "python": platform.python_version(), "hardware_target_verified": False},
        "source_sha256": sources_before,
        "limitations": ["ImageNet single-label training; not 1000 independent object classifiers",
                        "Official mapping is heuristic; no threshold or top1 relabeling applied",
                        "Pillow input pixels differ from PhotoPrism thumbnail/rendering pipeline",
                        "No GTX1660 or 300k capacity measurement"],
    }
    write_json(output / "result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("--output", type=Path, required=True)
    p = commands.add_parser("run")
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--model-dir", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.output)
    else:
        result = run(args.manifest, args.model_dir, args.output, limit=args.limit, threads=args.threads)
        print(json.dumps({"images": len(result["assets"]), "resources": result["resources"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
