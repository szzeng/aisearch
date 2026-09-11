"""P0-06 stage A7: InsightFace ``buffalo_l`` detection and recognition CUDA smoke.

Evidence recorded here:
- InsightFace's own detection/recognition sessions report CUDA first and produce a valid
  detection (public-domain sample photo) and a 512-dimension embedding;
- the recognition graph is additionally proven to be assignable to CUDA with the CPU
  fallback disabled;
- the detection graph cannot be assigned 100% to CUDA by ONNX Runtime 1.30, so the exact
  CPU-assigned operators are recorded from a profiled session instead of being hidden.

Person thresholds and accuracy calibration stay with P3; only the runtime is proven.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import onnxruntime
from insightface.app import FaceAnalysis
from runtime_common import (
    FACE_MODEL_FILES,
    REPOSITORY_ROOT,
    default_model_root,
    face_model_dir,
    gpu_hardware_summary,
    gpu_memory_used_mib,
    measure,
    process_gpu_memory_mib,
    process_working_set_mib,
    write_json,
)

DET_SIZE = (640, 640)
DET_THRESHOLD = 0.5
DEFAULT_RESULT = REPOSITORY_ROOT / "docs/reports/P0-06-face-gpu-smoke.json"
CUDA_PROVIDER = "CUDAExecutionProvider"
SAMPLE_FIXTURE = {
    "name": "sample-astronaut",
    "provider": "scikit-image sample data (skimage.data.astronaut)",
    "license": "public-domain sample data shipped with scikit-image; not from the user photo library",
}
FACE_FIXTURES = {
    "neutral": {"size": 512, "skin": (232, 190, 160), "hair": (62, 44, 34)},
    "warm": {"size": 420, "skin": (214, 160, 120), "hair": (28, 24, 28)},
}


def draw_face(spec: dict[str, object]) -> Any:
    from PIL import Image, ImageDraw

    size = int(spec["size"])
    image = Image.new("RGB", (size, size), (238, 238, 240))
    draw = ImageDraw.Draw(image)
    unit = size / 100
    draw.ellipse(
        [20 * unit, 16 * unit, 80 * unit, 96 * unit],
        fill=spec["skin"],
        outline=(120, 90, 70),
        width=max(1, int(unit)),
    )
    draw.chord([16 * unit, 8 * unit, 84 * unit, 56 * unit], start=180, end=360, fill=spec["hair"])
    eye_y = 46 * unit
    for eye_x in (38 * unit, 62 * unit):
        draw.ellipse(
            [eye_x - 7 * unit, eye_y - 4 * unit, eye_x + 7 * unit, eye_y + 4 * unit],
            fill=(250, 250, 250),
            outline=(60, 60, 60),
        )
        draw.ellipse(
            [eye_x - 3 * unit, eye_y - 3 * unit, eye_x + 3 * unit, eye_y + 3 * unit],
            fill=(46, 70, 110),
        )
    draw.arc(
        [40 * unit, 60 * unit, 60 * unit, 78 * unit],
        start=20,
        end=160,
        fill=(150, 60, 60),
        width=max(1, int(2 * unit)),
    )
    return image


def write_fixtures(directory: Path) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for name, spec in FACE_FIXTURES.items():
        path = directory / f"face-{name}.png"
        draw_face(spec).save(path)
        paths[name] = path
    from PIL import Image
    from skimage import data

    sample_path = directory / f"face-{SAMPLE_FIXTURE['name']}.png"
    Image.fromarray(data.astronaut()).save(sample_path)
    paths[SAMPLE_FIXTURE["name"]] = sample_path
    return paths


def strict_cuda_run(model_path: Path, providers: list[Any], input_shape: tuple[int, ...]) -> dict[str, Any]:
    """Create and run a session with the CPU fallback disabled, reporting the outcome."""
    options = onnxruntime.SessionOptions()
    options.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
    try:
        session = onnxruntime.InferenceSession(
            str(model_path), sess_options=options, providers=providers
        )
    except Exception as error:
        return {
            "strict_cuda_session": False,
            "error": f"{type(error).__name__}: {error}",
            "note": "the pinned graph has nodes that only the default CPU EP can take",
        }
    meta = session.get_inputs()[0]
    blob = np.zeros(input_shape, dtype=np.float32)
    started = time.perf_counter()
    outputs = session.run(None, {meta.name: blob})
    return {
        "strict_cuda_session": True,
        "input_name": meta.name,
        "input_shape": list(blob.shape),
        "output_count": len(outputs),
        "output_shapes": [list(output.shape) for output in outputs],
        "seconds": round(time.perf_counter() - started, 3),
        "providers": list(session.get_providers()),
        "note": "no node could fall back to CPU: the graph is entirely CUDA-assigned",
    }


def provider_split(model_path: Path, providers: list[Any], input_shape: tuple[int, ...]) -> dict[str, Any]:
    """Profiled session with the normal provider order: which nodes ran where."""
    profile_directory = Path(tempfile.mkdtemp(prefix="p0-06-face-profile-"))
    options = onnxruntime.SessionOptions()
    options.enable_profiling = True
    options.profile_file_prefix = str(profile_directory / model_path.stem)
    session = onnxruntime.InferenceSession(
        str(model_path), sess_options=options, providers=providers
    )
    meta = session.get_inputs()[0]
    blob = np.zeros(input_shape, dtype=np.float32)
    session.run(None, {meta.name: blob})
    profile_path = session.end_profiling()
    counts: dict[str, int] = {}
    cpu_ops: dict[str, int] = {}
    events = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    for event in events:
        if event.get("cat") != "Node":
            continue
        args = event.get("args") or {}
        provider = args.get("provider")
        if provider is None:
            continue
        counts[provider] = counts.get(provider, 0) + 1
        if provider != CUDA_PROVIDER:
            op_name = str(args.get("op_name", "unknown"))
            cpu_ops[op_name] = cpu_ops.get(op_name, 0) + 1
    Path(profile_path).unlink(missing_ok=True)
    return {
        "providers_requested": [entry if isinstance(entry, str) else entry[0] for entry in providers],
        "node_providers": counts,
        "cpu_node_ops": cpu_ops,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-root", type=Path, default=default_model_root())
    parser.add_argument("--device", default="cuda", choices=["cuda", "cpu"])
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()

    steps: list[str] = []
    baseline_used = gpu_memory_used_mib()
    providers: list[Any] = (
        [CUDA_PROVIDER, "CPUExecutionProvider"] if args.device == "cuda" else ["CPUExecutionProvider"]
    )
    strict_providers: list[Any] = (
        [(CUDA_PROVIDER, {"use_tf32": "0"})] if args.device == "cuda" else ["CPUExecutionProvider"]
    )
    ctx_id = 0 if args.device == "cuda" else -1
    result: dict[str, Any] = {
        "status": "running",
        "task": "P0-06",
        "stage": "A7",
        "device": args.device,
        "providers_requested": providers,
        "runtime": onnxruntime.__version__,
        "available_providers": onnxruntime.get_available_providers(),
        "hardware": gpu_hardware_summary(),
        "gpu_memory_used_before_load_mib": baseline_used,
    }
    try:
        root = args.model_root.resolve()
        face_dir = face_model_dir(root)
        fixtures = write_fixtures(root / "cache" / "p0-06-face-fixtures")
        app = FaceAnalysis(
            name=str(face_dir), allowed_modules=["detection", "recognition"], providers=providers
        )
        app.prepare(ctx_id=ctx_id, det_thresh=DET_THRESHOLD, det_size=DET_SIZE)
        steps.append("face_load")
        result["session_providers"] = {
            task: list(model.session.get_providers())
            for task, model in app.models.items()
            if hasattr(model, "session")
        }

        records = []
        for name, path in fixtures.items():
            image = cv2.imread(str(path))
            started = time.perf_counter()
            faces = app.get(image)
            elapsed = time.perf_counter() - started
            records.append(
                {
                    "fixture": name,
                    "path": str(path),
                    "face_count": len(faces),
                    "seconds": round(elapsed, 3),
                    "faces": [
                        {
                            "bbox": [float(value) for value in face.bbox],
                            "det_score": float(face.det_score),
                            "embedding_shape": list(face.embedding.shape),
                            "embedding_l2_norm": float(np.linalg.norm(face.embedding)),
                        }
                        for face in faces
                    ],
                }
            )
        result["records"] = records
        steps.append("face_inference")

        sample = next(record for record in records if record["fixture"] == SAMPLE_FIXTURE["name"])
        if sample["face_count"] == 0:
            raise ValueError("no face detected in the public-domain sample photo")
        for record in records:
            for face in record["faces"]:
                if face["embedding_shape"][-1] != 512:
                    raise ValueError(f"unexpected embedding shape {face['embedding_shape']}")

        detected = [record for record in records if record["face_count"] > 0]
        warm_path = Path(detected[0]["path"])
        warm_image = cv2.imread(str(warm_path))

        def run_sample() -> None:
            app.get(warm_image)

        result["latency"] = measure(run_sample, args.repeats, warmup=1)
        detection_path = face_dir / FACE_MODEL_FILES["detection"]
        recognition_path = face_dir / FACE_MODEL_FILES["recognition"]
        result["detection_graph"] = {
            **strict_cuda_run(detection_path, strict_providers, (1, 3, DET_SIZE[1], DET_SIZE[0])),
            **provider_split(detection_path, providers, (1, 3, DET_SIZE[1], DET_SIZE[0])),
        }
        result["recognition_graph"] = {
            **strict_cuda_run(recognition_path, strict_providers, (1, 3, 112, 112)),
            **provider_split(recognition_path, providers, (1, 3, 112, 112)),
        }
        steps.append("graph_smoke")

        if args.device == "cuda":
            if not result["recognition_graph"]["strict_cuda_session"]:
                raise ValueError("the recognition graph did not run entirely on CUDA")
            cuda_nodes = result["detection_graph"]["node_providers"].get(CUDA_PROVIDER, 0)
            if cuda_nodes == 0:
                raise ValueError("the detection graph ran no nodes on CUDA")
            if result["records"][-1]["face_count"] == 0 and sample["face_count"] == 0:
                raise ValueError("the detection graph produced no detection")

        result["gpu_memory_used_peak_mib"] = gpu_memory_used_mib()
        result["process_gpu_memory_mib"] = process_gpu_memory_mib()
        result["working_set_mib"] = process_working_set_mib()
        result["status"] = "pass"
        steps.append("result")
        write_json(args.result.resolve(), result)
        print(
            {
                "status": "pass",
                "steps": [*steps, "written"],
                "result": str(args.result.resolve()),
                "face_counts": {record["fixture"]: record["face_count"] for record in records},
                "session_providers": result["session_providers"],
                "sample_det_score": sample["faces"][0]["det_score"] if sample["faces"] else None,
                "sample_embedding_shape": sample["faces"][0]["embedding_shape"] if sample["faces"] else None,
                "latency_p50_ms": result["latency"].get("p50_ms"),
                "detection_strict_cuda": result["detection_graph"]["strict_cuda_session"],
                "detection_node_providers": result["detection_graph"]["node_providers"],
                "detection_cpu_node_ops": result["detection_graph"]["cpu_node_ops"],
                "recognition_strict_cuda": result["recognition_graph"]["strict_cuda_session"],
                "gpu_memory_used_peak_mib": result["gpu_memory_used_peak_mib"],
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


if __name__ == "__main__":
    raise SystemExit(main())
