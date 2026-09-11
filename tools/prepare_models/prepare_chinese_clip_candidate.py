r"""P0-06 stage B: download, export and describe a stronger Chinese-CLIP candidate.

The frozen P0-03 baseline stays untouched. This script produces a second, self-contained
model directory plus a manifest in the same schema, so the same adapter and evaluator can run
both models. Only the checkpoint needed for the export is downloaded (the duplicate original
``clip_cn_*.pt`` file is skipped to save disk), and the script records the resolved revision,
the ONNX hashes, the preprocessing and the tokenizer settings.

    uv run python tools/prepare_models/prepare_chinese_clip_candidate.py \
        --model-id OFA-Sys/chinese-clip-vit-large-patch14 \
        --model-dir D:/PhotoSearchData/models/chinese_clip/vit-l-14 \
        --manifest tools/prepare_models/manifests/chinese-clip-vit-l-14.json
"""

from __future__ import annotations

import argparse
import importlib.metadata
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

import torch
from chinese_clip_common import CONTEXT_LENGTH, IMAGE_SIZE, artifact_record, sha256_file, write_json
from huggingface_hub import HfApi, snapshot_download
from transformers import ChineseCLIPModel, ChineseCLIPProcessor

DOWNLOAD_FILES = ("config.json", "preprocessor_config.json", "pytorch_model.bin", "vocab.txt")


class ImageTower(torch.nn.Module):
    def __init__(self, model: ChineseCLIPModel) -> None:
        super().__init__()
        self.model = model

    def forward(self, pixel_values: torch.Tensor) -> torch.Tensor:
        return self.model.get_image_features(pixel_values=pixel_values)


class TextTower(torch.nn.Module):
    def __init__(self, model: ChineseCLIPModel) -> None:
        super().__init__()
        self.model = model

    def forward(
        self, input_ids: torch.Tensor, attention_mask: torch.Tensor, token_type_ids: torch.Tensor
    ) -> torch.Tensor:
        return self.model.get_text_features(
            input_ids=input_ids, attention_mask=attention_mask, token_type_ids=token_type_ids
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-id", default="OFA-Sys/chinese-clip-vit-large-patch14")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--context-length", type=int, default=CONTEXT_LENGTH)
    parser.add_argument("--image-size", type=int, default=IMAGE_SIZE)
    parser.add_argument("--skip-download", action="store_true")
    args = parser.parse_args()

    directory, manifest_path = args.model_dir.resolve(), args.manifest.resolve()
    steps: list[str] = []
    try:
        directory.mkdir(parents=True, exist_ok=True)
        revision = HfApi().model_info(args.model_id, revision="main").sha
        if not args.skip_download:
            snapshot_download(
                args.model_id,
                revision=revision,
                local_dir=directory,
                allow_patterns=list(DOWNLOAD_FILES),
            )
        if missing := [name for name in DOWNLOAD_FILES if not (directory / name).is_file()]:
            raise FileNotFoundError(f"required files missing: {missing}")
        steps.append("download")

        model = ChineseCLIPModel.from_pretrained(directory, local_files_only=True).eval()
        processor = ChineseCLIPProcessor.from_pretrained(directory, local_files_only=True)
        projection = int(model.config.projection_dim)
        image_processor = processor.image_processor
        size = int(getattr(image_processor, "crop_size", {}) .get("height", args.image_size)) if isinstance(getattr(image_processor, "crop_size", None), dict) else args.image_size
        mean = [float(value) for value in image_processor.image_mean]
        std = [float(value) for value in image_processor.image_std]

        image_path = directory / "image_tower.fp32.onnx"
        text_path = directory / "text_tower.fp32.onnx"
        tokens = processor.tokenizer(
            "一只猫",
            padding="max_length",
            truncation=True,
            max_length=args.context_length,
            return_tensors="pt",
        )
        with torch.inference_mode():
            torch.onnx.export(
                ImageTower(model),
                (torch.zeros(1, 3, size, size),),
                image_path,
                input_names=["pixel_values"],
                output_names=["unnorm_image_features"],
                opset_version=17,
                dynamo=False,
            )
            steps.append("image_export")
            torch.onnx.export(
                TextTower(model),
                (tokens["input_ids"], tokens["attention_mask"], tokens["token_type_ids"]),
                text_path,
                input_names=["input_ids", "attention_mask", "token_type_ids"],
                output_names=["unnorm_text_features"],
                opset_version=17,
                dynamo=False,
            )
            steps.append("text_export")

            image_vector = model.get_image_features(pixel_values=torch.zeros(1, 3, size, size))
            text_vector = model.get_text_features(**tokens)
        manifest = {
            "schema_version": 1,
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "generator": "tools/prepare_models/prepare_chinese_clip_candidate.py",
            "artifact_root_note": "artifact paths are relative to the model directory, which lives outside the repository",
            "model": {
                "id": args.model_id,
                "name": "Chinese-CLIP ViT-L/14 (candidate)",
                "requested_revision": "main",
                "resolved_revision": revision,
                "source_url": f"https://huggingface.co/{args.model_id}",
            },
            "license": {
                "spdx": "Apache-2.0",
                "source_url": f"https://huggingface.co/{args.model_id}/blob/main/LICENSE",
            },
            "embedding": {
                "dimension": projection,
                "metric": "cosine",
                "normalization": "L2",
                "note": "candidate space; the 512-D P0-03 baseline space is separate",
            },
            "preprocessing": {
                "image_size": size,
                "resize": {"shortest_edge": size},
                "center_crop": {"height": size, "width": size},
                "interpolation": "bicubic",
                "channel_order": "RGB",
                "dtype": "float32",
                "mean": mean,
                "std": std,
            },
            "tokenizer": {
                "name": type(processor.tokenizer).__name__,
                "max_length": args.context_length,
                "padding": "max_length",
                "truncation": "right",
                "vocab_file": "vocab.txt",
                "vocab_sha256": sha256_file(directory / "vocab.txt"),
            },
            "onnx": {
                "batch": 1,
                "opset": 17,
                "image": {"inputs": {"pixel_values": [1, 3, size, size]}, "output": "unnorm_image_features"},
                "text": {
                    "inputs": {
                        "input_ids": [1, args.context_length],
                        "attention_mask": [1, args.context_length],
                        "token_type_ids": [1, args.context_length],
                    },
                    "output": "unnorm_text_features",
                },
            },
            "artifacts": {
                "repository_files": {
                    name: artifact_record(
                        directory / name,
                        directory,
                        f"https://huggingface.co/{args.model_id}/resolve/{revision}/{name}",
                    )
                    for name in DOWNLOAD_FILES
                },
                "onnx": {
                    "image": artifact_record(image_path, directory, "generated by this script"),
                    "text": artifact_record(text_path, directory, "generated by this script"),
                },
            },
            "runtime": {
                "backend": "onnxruntime",
                "device": "CUDA",
                "platform": platform.platform(),
                "precision": "FP32",
                "python": platform.python_version(),
                "versions": {
                    name: importlib.metadata.version(name)
                    for name in ("huggingface-hub", "onnx", "onnxruntime-gpu", "torch", "transformers")
                },
            },
            "export_check": {
                "image_vector_shape": list(image_vector.shape),
                "text_vector_shape": list(text_vector.shape),
                "projection_dim": projection,
            },
        }
        if manifest["export_check"]["image_vector_shape"][-1] != projection:
            raise ValueError("image tower output does not match the projection dimension")
        if manifest["export_check"]["text_vector_shape"][-1] != projection:
            raise ValueError("text tower output does not match the projection dimension")
        write_json(manifest_path, manifest)
        steps.append("manifest")
        print(
            {
                "status": "ok",
                "steps": [*steps, "written"],
                "manifest": str(manifest_path),
                "model_dir": str(directory),
                "revision": revision,
                "projection_dim": projection,
                "image_onnx_mb": round(image_path.stat().st_size / 1024**2, 1),
                "text_onnx_mb": round(text_path.stat().st_size / 1024**2, 1),
            }
        )
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
