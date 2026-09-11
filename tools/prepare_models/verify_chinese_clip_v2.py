"""CPU batch=1 numerical and Top-K comparison for P0-03 Chinese-CLIP ONNX."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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
from PIL import Image
from transformers import ChineseCLIPModel, ChineseCLIPProcessor

QUERIES = ["一只橙色的猫", "海边的日落", "城市夜景", "家人聚餐", "绿色的山林"]


def main() -> int:
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
    args = parser.parse_args()
    directory = args.model_dir.resolve()
    steps = []
    try:
        manifest = load_json(args.manifest.resolve())
        for artifact in [
            *manifest["artifacts"]["repository_files"].values(),
            *manifest["artifacts"]["onnx"].values(),
        ]:
            path = directory / artifact["path"]
            if (
                not path.is_file()
                or path.stat().st_size != artifact["bytes"]
                or sha256_file(path) != artifact["sha256"]
            ):
                raise ValueError(f"manifest hash or size mismatch: {path.name}")
        steps.append("manifest_hashes")
        model = ChineseCLIPModel.from_pretrained(directory, local_files_only=True).eval()
        processor = ChineseCLIPProcessor.from_pretrained(directory, local_files_only=True)
        y, x = np.mgrid[0:224, 0:224]
        image = Image.fromarray(
            np.dstack(
                (
                    ((x * 3 + y) % 256).astype(np.uint8),
                    ((x + y * 2) % 256).astype(np.uint8),
                    (((x // 8) ^ (y // 8)) * 32 % 256).astype(np.uint8),
                )
            ),
            "RGB",
        )
        pixels = processor(images=image, return_tensors="pt")["pixel_values"]
        with torch.inference_mode():
            ref_image = model.get_image_features(pixel_values=pixels).numpy()
        rows = []
        refs = []
        with torch.inference_mode():
            for query in QUERIES:
                token = processor.tokenizer(
                    query,
                    padding="max_length",
                    truncation=True,
                    max_length=CONTEXT_LENGTH,
                    return_tensors="pt",
                )
                refs.append(model.get_text_features(**token).numpy())
                rows.append({k: v.numpy() for k, v in token.items()})
        ref_text = np.concatenate(refs)
        image_s = onnxruntime.InferenceSession(
            str(directory / manifest["artifacts"]["onnx"]["image"]["path"]),
            providers=["CPUExecutionProvider"],
        )
        text_s = onnxruntime.InferenceSession(
            str(directory / manifest["artifacts"]["onnx"]["text"]["path"]),
            providers=["CPUExecutionProvider"],
        )
        out_image = image_s.run(["unnorm_image_features"], {"pixel_values": pixels.numpy()})[0]
        out_text = np.concatenate([text_s.run(["unnorm_text_features"], r)[0] for r in rows])

        def compare(a, b):
            na, nb = l2_normalize(a), l2_normalize(b)
            return {
                "shape": list(a.shape),
                "reference_l2_norm": np.linalg.norm(a, axis=1).tolist(),
                "onnx_l2_norm": np.linalg.norm(b, axis=1).tolist(),
                "max_abs_difference": float(np.max(np.abs(a - b))),
                "cosine_similarity": np.sum(na * nb, axis=1).tolist(),
                "normalized_l2_norms": {
                    "reference": np.linalg.norm(na, axis=1).tolist(),
                    "onnx": np.linalg.norm(nb, axis=1).tolist(),
                },
            }

        ir, tr = compare(ref_image, out_image), compare(ref_text, out_text)
        ranks = [
            np.argsort(-(l2_normalize(a) @ l2_normalize(b).T)[0]).tolist()
            for a, b in ((ref_image, ref_text), (out_image, out_text))
        ]
        if (
            max(ir["max_abs_difference"], tr["max_abs_difference"]) > 5e-4
            or min(ir["cosine_similarity"] + tr["cosine_similarity"]) < 0.99999
            or ranks[0] != ranks[1]
        ):
            raise ValueError("reference/ONNX tolerance or Top-K failure")
        result = {
            "status": "pass",
            "fixture": {
                "kind": "script-generated RGB gradient/checkerboard",
                "image_size": [224, 224],
                "queries": QUERIES,
            },
            "providers_available": onnxruntime.get_available_providers(),
            "sessions_used": ["CPUExecutionProvider"],
            "image": ir,
            "text": tr,
            "top_k": {"reference_rank": ranks[0], "onnx_rank": ranks[1], "identical": True},
            "thresholds": {"max_abs_difference": 0.0005, "min_cosine_similarity": 0.99999},
        }
        write_json(args.result.resolve(), result)
        print(result)
        return 0
    except Exception as error:
        print(
            {
                "status": "fail",
                "stage": steps[-1] if steps else "setup",
                "exception_type": type(error).__name__,
                "message": str(error),
                "completed_steps": steps,
            },
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
