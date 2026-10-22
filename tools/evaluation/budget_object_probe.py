"""Fixed RT-DETRv2 R18 offline measurements on public/synthetic images only.

This is not a production provider. Class scores are uncalibrated machine evidence,
not user tags. No download, catalog access, remote Python, or CPU fallback occurs.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import numpy as np

from tools.evaluation.wemm_pilot_core import cache_identity, deterministic_rank
from tools.evaluation.wemm_pilot_runtime import (
    PilotError,
    configure_offline,
    decode_image,
    decoder_identity,
    package_versions,
    peak_ram_bytes,
    sha256,
)
from tools.evaluation.wemm_quality_pilot import atomic_json, checked_output, gpu_lock, read_json

MODEL_LOCK = Path(__file__).with_name("budget_object_model_lock.json")


def verify_model(directory: Path) -> dict:
    lock = read_json(MODEL_LOCK)
    for name, entry in lock["files"].items():
        path = directory / name
        if not path.is_file() or path.stat().st_size != entry["bytes"]:
            raise PilotError("MODEL_FILE_SIZE_MISMATCH:" + name)
        if sha256(path) != entry["sha256"]:
            raise PilotError("MODEL_FILE_HASH_MISMATCH:" + name)
    # These are the only runtime-readable model files; refuse injected config/code.
    if any(path.name not in lock["files"] for path in directory.iterdir()):
        raise PilotError("UNPINNED_MODEL_FILE")
    return lock


def load_sample(path: Path, maximum: int) -> dict:
    sample = read_json(path)
    schema = sample.get("schema", "")
    if schema != "wemm-pilot-public-sample-v1" and not schema.startswith("synthetic-"):
        raise PilotError("PUBLIC_OR_SYNTHETIC_SAMPLE_REQUIRED")
    assets = sample.get("assets", [])
    if not 1 <= maximum <= 1000 or not 1 <= len(assets) <= maximum:
        raise PilotError("SAMPLE_SIZE_OUTSIDE_BUDGET")
    ids, hashes = [], set()
    for row in assets:
        asset_id, digest = row.get("asset_id"), row.get("sha256", "")
        if (type(asset_id) not in (int, str) or asset_id == ""
                or not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)):
            raise PilotError("SAMPLE_IDENTITY_INVALID")
        if digest in hashes:
            raise PilotError("DUPLICATE_SAMPLE_CONTENT")
        image_path = Path(row.get("path", ""))
        if not image_path.is_absolute() or "://" in str(image_path):
            raise PilotError("ABSOLUTE_LOCAL_IMAGE_REQUIRED")
        ids.append(asset_id)
        hashes.add(digest)
    deterministic_rank(np.zeros(len(ids)), ids)
    return sample


def pack_predictions(class_scores, scores, labels, boxes, id2label, size) -> dict:
    """Validate model outputs and retain raw boxes plus clipped display boxes."""
    classes = {int(key): value for key, value in id2label.items()}
    maxima = np.asarray(class_scores, dtype=np.float64)
    confidence = np.asarray(scores, dtype=np.float64)
    indices = np.asarray(labels)
    regions = np.asarray(boxes, dtype=np.float64)
    if (set(classes) != set(range(len(classes))) or maxima.shape != (len(classes),)
            or confidence.ndim != 1 or not 0 <= len(confidence) <= 300
            or indices.shape != confidence.shape or indices.dtype.kind not in "iu"
            or regions.shape != (len(confidence), 4)):
        raise PilotError("DETECTION_OUTPUT_SHAPE_INVALID")
    if (not np.isfinite(maxima).all() or not np.isfinite(confidence).all()
            or not np.isfinite(regions).all() or (maxima < 0).any() or (maxima > 1).any()
            or (confidence < 0).any() or (confidence > 1).any()
            or any(int(index) not in classes for index in indices)
            or (regions[:, 2:] < regions[:, :2]).any()):
        raise PilotError("DETECTION_OUTPUT_INVALID")
    width, height = size
    clipped = np.clip(regions, [0, 0, 0, 0], [width, height, width, height])
    detections = [
        {"label_id": int(indices[i]), "label": classes[int(indices[i])],
         "score": float(confidence[i]), "raw_xyxy": regions[i].tolist(),
         "xyxy": clipped[i].tolist()}
        for i in sorted(range(len(confidence)), key=lambda i: (-confidence[i], int(indices[i]), i))
    ]
    return {"class_scores": {classes[i]: float(maxima[i]) for i in range(len(classes))},
            "detections": detections}


class ObjectRuntime:
    def __init__(self, directory: Path, device: str, dtype: str):
        import torch
        from transformers import RTDetrImageProcessor, RTDetrV2ForObjectDetection

        self.torch = torch
        self.device = torch.device(device)
        if self.device.type == "cuda":
            if not torch.cuda.is_available():
                raise PilotError("CUDA_UNAVAILABLE_NO_FALLBACK")
            torch.cuda.set_device(self.device)
            self.device = torch.device("cuda", torch.cuda.current_device())
            torch.cuda.reset_peak_memory_stats(self.device)
        elif self.device.type != "cpu" or dtype != "fp32":
            raise PilotError("CPU_REQUIRES_EXPLICIT_FP32")
        self.dtype = torch.float32 if dtype == "fp32" else torch.float16
        started = time.perf_counter()
        self.processor = RTDetrImageProcessor.from_pretrained(directory, local_files_only=True)
        self.model = RTDetrV2ForObjectDetection.from_pretrained(
            directory, local_files_only=True, torch_dtype=self.dtype,
            use_safetensors=True).to(self.device).eval()
        self.synchronize()
        if any(tensor.device != self.device for tensor in
               (*self.model.parameters(), *self.model.buffers())):
            raise PilotError("PARAMETER_DEVICE_MISMATCH_NO_OFFLOAD")
        if (self.model.config.num_queries != 300 or len(self.model.config.id2label) != 80
                or not self.model.config.disable_custom_kernels
                or self.processor.size != {"height": 640, "width": 640}):
            raise PilotError("PINNED_MODEL_CONFIG_MISMATCH")
        self.load_seconds = time.perf_counter() - started

    def synchronize(self):
        if self.device.type == "cuda":
            self.torch.cuda.synchronize(self.device)

    def encode(self, image) -> dict:
        self.synchronize()
        begin = time.perf_counter()
        inputs = self.processor(images=image, return_tensors="pt")
        inputs = {name: tensor.to(device=self.device,
                                  dtype=self.dtype if tensor.is_floating_point() else tensor.dtype)
                  for name, tensor in inputs.items()}
        self.synchronize()
        prepared = time.perf_counter()
        with self.torch.inference_mode():
            outputs = self.model(**inputs)
        self.synchronize()
        inferred = time.perf_counter()
        maxima = outputs.logits.float().sigmoid().amax(dim=1)[0].cpu().numpy()
        raw = self.processor.post_process_object_detection(
            outputs, threshold=0.0, target_sizes=[(image.height, image.width)])[0]
        prediction = pack_predictions(maxima, raw["scores"].float().cpu().numpy(),
                                      raw["labels"].cpu().numpy(),
                                      raw["boxes"].float().cpu().numpy(),
                                      self.model.config.id2label, image.size)
        self.synchronize()
        ended = time.perf_counter()
        return {**prediction, "prepare_ms": (prepared - begin) * 1000,
                "forward_ms": (inferred - prepared) * 1000,
                "postprocess_ms": (ended - inferred) * 1000,
                "detect_ms": (ended - begin) * 1000}

    def resources(self) -> dict:
        result = {"device": str(self.device), "dtype": str(self.dtype),
                  "load_seconds": self.load_seconds,
                  "peak_ram_bytes": peak_ram_bytes(),
                  "parameter_devices": sorted({str(p.device) for p in self.model.parameters()}),
                  "parameter_count": sum(p.numel() for p in self.model.parameters())}
        if self.device.type == "cuda":
            props = self.torch.cuda.get_device_properties(self.device)
            result.update(gpu_name=props.name, compute_capability=[props.major, props.minor],
                          total_vram_bytes=props.total_memory,
                          peak_allocated_bytes=self.torch.cuda.max_memory_allocated(self.device),
                          peak_reserved_bytes=self.torch.cuda.max_memory_reserved(self.device))
        return result

    def close(self):
        self.model = None
        if self.device.type == "cuda":
            self.torch.cuda.empty_cache()


def run(args: argparse.Namespace, *, runtime_factory=ObjectRuntime) -> dict:
    sample = load_sample(args.sample_file, args.max_assets)
    assets = sample["assets"]
    roots = [args.model_dir.resolve(), *(Path(row["path"]).parent for row in assets)]
    output = checked_output(args.out_dir, roots)
    if output.exists() and any(output.iterdir()):
        raise PilotError("FRESH_OUTPUT_DIRECTORY_REQUIRED")
    if args.device == "cpu" and args.dtype != "fp32":
        raise PilotError("CPU_REQUIRES_EXPLICIT_FP32")
    with gpu_lock(roots):
        configure_offline(output / "runtime-cache")
        lock = verify_model(args.model_dir)
        for row in assets:
            if sha256(Path(row["path"])) != row["sha256"]:
                raise PilotError("IMAGE_CONTENT_CHANGED")
        spec = {"model_lock": lock, "device": args.device, "dtype": args.dtype,
                "batch_size": 1, "max_detections": 300, "score_threshold": 0.0,
                "processor_size": [640, 640], "decoder": decoder_identity(),
                "packages": package_versions(),
                "source_sha256": sha256(Path(__file__))}
        result = {"schema": "budget-object-probe-v1", "status": "RUNNING", "spec": spec,
                  "cache_identity": cache_identity(
                      {**spec, "sample_sha256": sha256(args.sample_file)}, assets,
                      [{"operation": "fixed-coco80-object-detection"}]),
                  "sample_sha256": sha256(args.sample_file),
                  "inputs": [{"asset_id": row["asset_id"], "sha256": row["sha256"]}
                             for row in assets],
                  "source_identity": sample.get("source_identity"), "images": [],
                  "quality_status": "EXPLORATORY_MACHINE_EVIDENCE_NOT_USER_TAGS",
                  "training_overlap_warning": (
                      "COCO train2014 overlaps detector training; diagnostic only"
                      if "train2014" in json.dumps(sample.get("source_identity", {}))
                      else "Training overlap not established; data provenance must be reviewed"),
                  "failure": None}
        runtime, phase = None, "load"
        try:
            runtime = runtime_factory(args.model_dir, args.device, args.dtype)
            result["loaded_resources"] = runtime.resources()
            for index, row in enumerate(assets):
                phase = "image:" + str(index)
                begin = time.perf_counter()
                image, facts = decode_image(Path(row["path"]), row["sha256"])
                decode_ms = (time.perf_counter() - begin) * 1000
                try:
                    prediction = runtime.encode(image)
                finally:
                    image.close()
                result["images"].append({"asset_id": row["asset_id"], "sha256": row["sha256"],
                                         "decoded": facts, "decode_ms": decode_ms, **prediction})
                if (index + 1) % 25 == 0:
                    atomic_json(output / "progress.json", {"completed": index + 1,
                                                           "total": len(assets)})
            result["resources"] = runtime.resources()
            result["status"] = "COMPLETE"
        except Exception as error:
            result.update(status="BLOCKED", failure={"phase": phase, "type": type(error).__name__,
                                                       "message": str(error)[:1000]})
        finally:
            if runtime is not None:
                runtime.close()
            atomic_json(output / "result.json", result)
        return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--sample-file", type=Path, required=True)
    result.add_argument("--model-dir", type=Path, required=True)
    result.add_argument("--out-dir", type=Path, required=True)
    result.add_argument("--device", choices=("cpu", "cuda:0"), required=True)
    result.add_argument("--dtype", choices=("fp32", "fp16"), default="fp32")
    result.add_argument("--max-assets", type=int, default=400)
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        result = run(parser().parse_args(argv))
        print(json.dumps({"status": result["status"], "completed": len(result["images"]),
                          "failure": result["failure"]}, ensure_ascii=False))
        return 0 if result["status"] == "COMPLETE" else 2
    except Exception as error:
        print(json.dumps({"status": "BLOCKED", "type": type(error).__name__,
                          "message": str(error)[:1000]}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
