"""Validate P0-03 artifacts and compare Chinese-CLIP PyTorch vs CPU ONNX outputs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime
import torch
from chinese_clip_common import (
    CONTEXT_LENGTH,
    default_model_dir,
    l2_normalize,
    load_json,
    sha256_file,
    write_json,
)
from download_and_export_chinese_clip import ImageTower, TextTower
from PIL import Image
from transformers import ChineseCLIPModel, ChineseCLIPProcessor

QUERIES = ["一只橙色的猫", "海边的日落", "城市夜景", "家人聚餐", "绿色的山林"]
MAX_ABS_TOLERANCE = 5e-4
MIN_COSINE = 0.99999


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=default_model_dir())
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("tools/prepare_models/manifests/chinese-clip-vit-b-16.json"),
    )
    parser.add_argument(
        "--result",
        type=Path,
        default=Path("tools/prepare_models/manifests/chinese-clip-vit-b-16-validation.json"),
    )
    return parser.parse_args()


def fixture_image() -> Image.Image:
    y, x = np.mgrid[0:224, 0:224]
    red = ((x * 3 + y) % 256).astype(np.uint8)
    green = ((x + y * 2) % 256).astype(np.uint8)
    blue = (((x // 8) ^ (y // 8)) * 32 % 256).astype(np.uint8)
    return Image.fromarray(np.dstack((red, green, blue)), mode="RGB")


def vector_comparison(reference: np.ndarray, candidate: np.ndarray) -> dict[str, Any]:
    normalized_reference = l2_normalize(reference)
    normalized_candidate = l2_normalize(candidate)
    return {
        "shape": list(reference.shape),
        "reference_l2_norm": np.linalg.norm(reference, axis=1).tolist(),
        "onnx_l2_norm": np.linalg.norm(candidate, axis=1).tolist(),
        "max_abs_difference": float(np.max(np.abs(reference - candidate))),
        "cosine_similarity": np.sum(normalized_reference * normalized_candidate, axis=1).tolist(),
        "normalized": {
            "reference_l2_norm": np.linalg.norm(normalized_reference, axis=1).tolist(),
            "onnx_l2_norm": np.linalg.norm(normalized_candidate, axis=1).tolist(),
        },
    }


def verify_hashes(manifest: dict[str, Any], model_dir: Path) -> None:
    artifacts = list(manifest["artifacts"]["repository_files"].values())
    artifacts.extend(manifest["artifacts"]["onnx"].values())
    for artifact in artifacts:
        path = model_dir / artifact["path"]
        if not path.is_file():
            raise FileNotFoundError(f"manifest artifact is missing: {path}")
        if path.stat().st_size != artifact["bytes"]:
            raise ValueError(f"byte size mismatch for {path.name}")
        if sha256_file(path) != artifact["sha256"]:
            raise ValueError(f"SHA-256 mismatch for {path.name}")


def main() -> int:
    args = parse_args()
    model_dir = args.model_dir.resolve()
    completed_steps: list[str] = []
    try:
        manifest = load_json(args.manifest.resolve())
        verify_hashes(manifest, model_dir)
        completed_steps.append("manifest_hashes")
        providers = onnxruntime.get_available_providers()
        model = ChineseCLIPModel.from_pretrained(model_dir, local_files_only=True).eval()
        processor = ChineseCLIPProcessor.from_pretrained(model_dir, local_files_only=True)
        image_tower, text_tower = ImageTower(model).eval(), TextTower(model).eval()
        pixel_values = processor(images=fixture_image(), return_tensors="pt")["pixel_values"]
        with torch.inference_mode():
            reference_image = image_tower(pixel_values).numpy()
        reference_texts: list[np.ndarray] = []
        token_rows: list[dict[str, np.ndarray]] = []
        with torch.inference_mode():
            for query in QUERIES:
                tokens = processor.tokenizer(
                    query,
                    padding="max_length",
                    truncation=True,
                    max_length=CONTEXT_LENGTH,
                    return_tensors="pt",
                )
                reference_texts.append(
                    text_tower(
                        tokens["input_ids"], tokens["attention_mask"], tokens["token_type_ids"]
                    ).numpy()
                )
                token_rows.append({key: value.numpy() for key, value in tokens.items()})
        reference_text = np.concatenate(reference_texts, axis=0)
        image_session = onnxruntime.InferenceSession(
            str(model_dir / manifest["artifacts"]["onnx"]["image"]["path"]),
            providers=["CPUExecutionProvider"],
        )
        text_session = onnxruntime.InferenceSession(
            str(model_dir / manifest["artifacts"]["onnx"]["text"]["path"]),
            providers=["CPUExecutionProvider"],
        )
        onnx_image = image_session.run(
            [manifest["onnx"]["image"]["output"]], {"pixel_values": pixel_values.numpy()}
        )[0]
        onnx_text = np.concatenate(
            [
                text_session.run([manifest["onnx"]["text"]["output"]], tokens)[0]
                for tokens in token_rows
            ],
            axis=0,
        )
        image_result = vector_comparison(reference_image, onnx_image)
        text_result = vector_comparison(reference_text, onnx_text)
        ref_scores = (l2_normalize(reference_image) @ l2_normalize(reference_text).T)[0]
        onnx_scores = (l2_normalize(onnx_image) @ l2_normalize(onnx_text).T)[0]
        ref_rank, onnx_rank = np.argsort(-ref_scores).tolist(), np.argsort(-onnx_scores).tolist()
        if (
            max(image_result["max_abs_difference"], text_result["max_abs_difference"])
            > MAX_ABS_TOLERANCE
        ):
            raise ValueError("PyTorch/ONNX maximum absolute difference exceeds tolerance")
        if min(image_result["cosine_similarity"] + text_result["cosine_similarity"]) < MIN_COSINE:
            raise ValueError("PyTorch/ONNX cosine similarity is below tolerance")
        if ref_rank != onnx_rank:
            raise ValueError(f"Top-K ranking mismatch: PyTorch={ref_rank}, ONNX={onnx_rank}")
        completed_steps.append("cpu_reference_vs_onnx")
        result = {
            "status": "pass",
            "fixture": {
                "kind": "script-generated RGB gradient/checkerboard",
                "image_size": [224, 224],
                "queries": QUERIES,
            },
            "providers_available": providers,
            "sessions_used": ["CPUExecutionProvider"],
            "image": image_result,
            "text": text_result,
            "top_k": {
                "reference_rank": ref_rank,
                "onnx_rank": onnx_rank,
                "identical": True,
                "reference_scores": ref_scores.tolist(),
                "onnx_scores": onnx_scores.tolist(),
            },
            "thresholds": {
                "max_abs_difference": MAX_ABS_TOLERANCE,
                "min_cosine_similarity": MIN_COSINE,
            },
        }
        write_json(args.result.resolve(), result)
        print(result)
        return 0
    except Exception as error:
        failure = {
            "status": "fail",
            "stage": completed_steps[-1] if completed_steps else "setup",
            "exception_type": type(error).__name__,
            "message": str(error),
            "completed_steps": completed_steps,
        }
        print(failure, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
