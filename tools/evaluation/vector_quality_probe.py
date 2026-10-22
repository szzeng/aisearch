"""Bounded Qwen-only experiments; never changes a production vector space.

Run one stage per fresh ignored output directory. Query variants use the existing
image space; highres changes the image budget; crop5 retains the original whole
image plus four label-independent crops, each 70% of each original edge.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from tools.evaluation.wemm_pilot_core import cache_identity, normalize_prefix
from tools.evaluation.wemm_pilot_runtime import (
    QWEN_MANIFEST,
    ModelRuntime,
    PilotError,
    configure_offline,
    decode_image,
    decoder_identity,
    package_versions,
    sha256,
    source_identity,
    verify_model,
)
from tools.evaluation.wemm_quality_pilot import (
    _read_complete_run,
    _vector_record,
    atomic_json,
    checked_output,
    gpu_lock,
    load_inputs,
    read_json,
)

CATEGORIES = (
    ("cat", "猫", "一张猫的照片", "cat", "a photo of a cat"),
    ("dog", "狗", "一张狗的照片", "dog", "a photo of a dog"),
    ("bird", "鸟", "一张鸟的照片", "bird", "a photo of a bird"),
    ("car", "汽车", "一张汽车的照片", "car", "a photo of a car"),
    ("bicycle", "自行车", "一张自行车的照片", "bicycle", "a photo of a bicycle"),
    ("person", "人", "一张人的照片", "person", "a photo of a person"),
    ("chair", "椅子", "一张椅子的照片", "chair", "a photo of a chair"),
    ("teddy_bear", "玩具熊", "一张玩具熊的照片", "teddy bear", "a photo of a teddy bear"),
)
VARIANTS = ("zh", "zh_photo", "en", "en_photo", "zh_contains")


def query_variants(queries: list[dict]) -> list[dict]:
    """Freeze five literal expressions; no generated translations or tuning."""
    supplied = {row["query_id"]: row["text"] for row in queries}
    result = []
    for category, *texts in CATEGORIES:
        if supplied.get(category) != texts[0]:
            raise PilotError("FIXED_EIGHT_CATEGORIES_REQUIRED")
        texts.append("画面中有" + texts[0] + "的照片")
        for variant, text in zip(VARIANTS, texts, strict=True):
            result.append({"query_id": category, "variant": variant, "text": text})
    return result


def mean_query(vectors: np.ndarray, dimension: int = 2048) -> np.ndarray:
    """Normalize each prefix, mean equally, normalize again at the chosen width.

    For a 512D ensemble call with dimension=512 on the four original vectors;
    truncating the already averaged 2048D vector is a different experiment.
    """
    normalized = normalize_prefix(vectors, dimension)
    if len(normalized) != 4:
        raise PilotError("ENSEMBLE_REQUIRES_FOUR_VARIANTS")
    return normalize_prefix(normalized.astype(np.float64).mean(axis=0, keepdims=True), dimension)


def crop_boxes(width: int, height: int) -> list[tuple[int, int, int, int]]:
    """Whole image then TL, TR, BL, BR; 70% edge length, 40% shared span."""
    if type(width) is not int or type(height) is not int or min(width, height) < 2:
        raise PilotError("CROP_REQUIRES_DIMENSIONS_AT_LEAST_TWO")
    crop_width, crop_height = max(1, width * 7 // 10), max(1, height * 7 // 10)
    return [(0, 0, width, height), (0, 0, crop_width, crop_height),
            (width - crop_width, 0, width, crop_height),
            (0, height - crop_height, crop_width, height),
            (width - crop_width, height - crop_height, width, height)]


def baseline_inputs(directory: Path, assets: list[dict], lock: dict) -> tuple[dict, dict]:
    baseline, vectors, _ = _read_complete_run(directory)
    spec = baseline["model_spec"]
    if (spec.get("model") != "qwen" or spec.get("dtype") != "fp16"
            or spec.get("native_dimension") != 2048 or spec.get("model_lock") != lock
            or spec.get("decoder") != decoder_identity()
            or spec.get("packages") != package_versions()):
        raise PilotError("BASELINE_MODEL_OR_DECODER_MISMATCH")
    # The pilot CLI had an I/O-only repair; compare its unchanged encoding helpers.
    for name, digest in source_identity().items():
        if name.endswith("wemm_quality_pilot.py"):
            continue
        if spec.get("source_files", {}).get(name) != digest:
            raise PilotError("BASELINE_ENCODING_SOURCE_MISMATCH")
    rows = {row["asset_id"]: (row, vectors[i:i + 1], baseline["images"][str(i)])
            for i, row in enumerate(baseline["inputs"])}
    for asset in assets:
        previous = rows.get(asset["asset_id"])
        if previous is None or previous[0]["sha256"] != asset["sha256"]:
            raise PilotError("BASELINE_SAMPLE_MISMATCH")
    return baseline, rows


def run(args: argparse.Namespace, *, runtime_factory=ModelRuntime) -> dict:
    sample, queries = load_inputs(args.sample_file, args.queries, args.max_assets)
    if sample.get("schema") == "wemm-pilot-sample-v1":
        raise PilotError("THIS_PROBE_REQUIRES_PUBLIC_OR_SYNTHETIC_DATA")
    assets = sample["assets"]
    variants = query_variants(queries)
    roots = [args.model_dir.resolve(), args.baseline_dir.resolve(),
             *(Path(row["path"]).resolve().parent for row in assets)]
    directory = checked_output(args.out_dir, roots)
    with gpu_lock(roots):
        if directory.exists() and any(directory.iterdir()):
            raise PilotError("FRESH_OUTPUT_DIRECTORY_REQUIRED")
        configure_offline(checked_output(directory / "runtime-cache", roots))
        lock = verify_model("qwen", args.model_dir)
        baseline, baseline_rows = baseline_inputs(args.baseline_dir, assets, lock)
        for row in assets:
            if sha256(Path(row["path"])) != row["sha256"]:
                raise PilotError("IMAGE_CONTENT_CHANGED")
        native = read_json(QWEN_MANIFEST)
        native["provider"]["provider_id"] = "qwen-vector-probe-" + args.mode
        native["embedding"]["dimension"] = 2048
        pixels = 524288 if args.mode == "highres" else 262144
        native["preprocessing"]["max_pixels"] = pixels
        spec = {"mode": args.mode, "max_pixels": pixels, "dimension": 2048,
                "manifest": native, "dtype": "fp16", "device": args.device,
                "crop_edge_fraction": .7 if args.mode == "crop5" else None,
                "baseline_identity": baseline["cache_identity"],
                "source_files": {**source_identity(),
                                 "tools/evaluation/vector_quality_probe.py": sha256(Path(__file__))},
                "decoder": decoder_identity(), "packages": package_versions()}
        identities = [{key: row.get(key) for key in ("asset_id", "sha256", "selection_stratum")}
                      for row in assets]
        identity = cache_identity(spec, identities, variants)
        manifest_path = directory / "experimental-manifest.json"
        atomic_json(manifest_path, native)
        result = {"schema": "vector-quality-probe-v1", "status": "RUNNING",
                  "cache_identity": identity, "spec": spec, "inputs": identities,
                  "queries": variants, "query_vectors": [], "images": [], "ensembles": [],
                  "failure": None, "quality_status": "EXPLORATORY_NOT_PRODUCTION"}
        atomic_json(directory / "identity.json", {"cache_identity": identity, "spec": spec,
                                                  "inputs": identities, "queries": variants})
        runtime = runtime_factory("qwen", args.model_dir, args.device, "fp16", manifest_path)
        phase = "load"
        started = time.perf_counter()
        try:
            result["runtime"] = runtime.load()
            result["load_ms"] = (time.perf_counter() - started) * 1000
            phase = "stability"
            before, before_ms = runtime.measured_encode(text="猫")
            image, facts = decode_image(Path(assets[0]["path"]), assets[0]["sha256"])
            try:
                visual, visual_ms = runtime.measured_encode(image=image)
            finally:
                image.close()
            after, after_ms = runtime.measured_encode(text="猫")
            cosine, delta = float(np.sum(before * after)), float(np.max(np.abs(before - after)))
            result["stability"] = {"cosine": cosine, "max_absolute_delta": delta,
                                   "before_ms": before_ms, "after_ms": after_ms,
                                   "image_ms": visual_ms, "passed": cosine >= .99999 and delta <= .0001}
            if not result["stability"]["passed"]:
                raise PilotError("TEXT_IMAGE_TEXT_STABILITY_FAILED")
            if args.mode == "query":
                values = []
                for index, query in enumerate(variants):
                    phase = "query:" + str(index)
                    value, elapsed = runtime.measured_encode(text=query["text"])
                    values.append(value)
                    result["query_vectors"].append(_vector_record(
                        directory, f"query-{index:02d}", value, elapsed, **query))
                    atomic_json(directory / "progress.json", result)
                for index, (category, *_) in enumerate(CATEGORIES):
                    offset = index * len(VARIANTS)
                    value = mean_query(np.concatenate(values[offset:offset + 4]))
                    result["ensembles"].append(_vector_record(
                        directory, "ensemble-" + category, value, 0,
                        query_id=category, variant="mean4", dimension=2048,
                        formula="L2(mean(L2(each native vector))); recompute for each lower dimension"))
            else:
                for index, asset in enumerate(assets):
                    phase = "image:" + str(index)
                    decode_start = time.perf_counter()
                    image, facts = decode_image(Path(asset["path"]), asset["sha256"])
                    decode_ms = (time.perf_counter() - decode_start) * 1000
                    try:
                        boxes = crop_boxes(image.width, image.height) if args.mode == "crop5" else [
                            (0, 0, image.width, image.height)]
                        records = []
                        for view, box in enumerate(boxes):
                            phase = f"image:{index}:view:{view}"
                            metadata: dict[str, Any] = {"box": list(box), "view": view}
                            if args.mode == "crop5" and view == 0:
                                _, value, previous = baseline_rows[asset["asset_id"]]
                                if previous.get("decoded") != facts:
                                    raise PilotError("BASELINE_DECODED_PIXELS_MISMATCH")
                                elapsed = 0
                                metadata.update(reused_from=baseline["cache_identity"],
                                                original_encode_ms=previous["encode_ms"])
                            elif args.mode == "highres" and index == 0:
                                value, elapsed = visual, visual_ms
                                metadata["reused_from_stability"] = True
                            else:
                                cropped = image.crop(box)
                                try:
                                    value, elapsed = runtime.measured_encode(image=cropped)
                                finally:
                                    cropped.close()
                            records.append(_vector_record(
                                directory, f"image-{index:04d}-{view}", value, elapsed, **metadata))
                        result["images"].append({"asset_id": asset["asset_id"], "decoded": facts,
                                                 "decode_ms": decode_ms, "views": records})
                    finally:
                        image.close()
                    atomic_json(directory / "progress.json", result)
            result["status"] = "COMPLETE"
        except Exception as error:
            result.update(status="BLOCKED", failure={"phase": phase, "type": type(error).__name__,
                          "code": str(error) if isinstance(error, PilotError) else "ENCODING_FAILED",
                          **{key: getattr(error, key) for key in ("errno", "winerror")
                             if isinstance(getattr(error, key, None), int)}})
            raise
        finally:
            result["resources"] = runtime.resources()
            result["load_and_work_ms"] = (time.perf_counter() - started) * 1000
            runtime.close()
            atomic_json(directory / "result.json", result)
        return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("mode", choices=("query", "highres", "crop5"))
    for name in ("sample-file", "queries", "model-dir", "baseline-dir", "out-dir"):
        result.add_argument("--" + name, type=Path, required=True)
    result.add_argument("--device", default="cuda:0")
    result.add_argument("--max-assets", type=int, default=500)
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        result = run(parser().parse_args(argv))
    except Exception as error:
        print(json.dumps({"status": "BLOCKED", "type": type(error).__name__,
                          "code": str(error) if isinstance(error, PilotError) else "PROBE_FAILED"}))
        return 2
    print(json.dumps({"status": result["status"], "mode": result["spec"]["mode"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
