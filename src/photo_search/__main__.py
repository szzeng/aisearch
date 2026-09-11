"""Local development entry point and the P0-05 offline demo."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import uvicorn
from PIL import Image

from photo_search.workers.client import AiWorkerClient


def _default_model_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "PhotoSearchData" / "models" / "chinese_clip" / "vit-b-16"


def _default_manifest() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "tools" / "prepare_models" / "manifests" / "chinese-clip-vit-b-16.json"
    return Path.cwd() / "tools" / "prepare_models" / "manifests" / "chinese-clip-vit-b-16.json"


def _demo_search(model_dir: Path, manifest: Path) -> int:
    """Run a deterministic local fixture through the managed image/text worker."""

    with tempfile.TemporaryDirectory(prefix="photo-search-demo-") as raw_directory:
        directory = Path(raw_directory)
        colors = {"red": (220, 40, 40), "green": (40, 180, 70), "blue": (45, 80, 210)}
        paths: list[tuple[str, Path]] = []
        for name, color in colors.items():
            path = directory / f"{name}.png"
            Image.new("RGB", (256, 192), color).save(path)
            paths.append((name, path))
        with AiWorkerClient(model_dir, manifest) as worker:
            health = worker.health()
            query = worker.encode_text("红色的图片")
            image_results = [(name, worker.encode_image(path)) for name, path in paths]
        query_vector = np.asarray(query["vector"], dtype=np.float32)
        scores = [
            {"id": name, "cosine": float(query_vector @ np.asarray(result["vector"], dtype=np.float32))}
            for name, result in image_results
        ]
        scores.sort(key=lambda row: row["cosine"], reverse=True)
        print(json.dumps({
            "status": "ok", "query": "红色的图片", "processor": query["spec"],
            "health": health, "query_l2_norm": float(np.linalg.norm(query_vector)),
            "images": [{"id": name, "l2_norm": float(np.linalg.norm(result["vector"]))}
                       for name, result in image_results], "top_k": scores,
        }, ensure_ascii=False))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Personal local photo search")
    subparsers = parser.add_subparsers(dest="command")
    demo = subparsers.add_parser("demo-search", help="run offline Chinese-CLIP CPU fixture search")
    demo.add_argument("--model-dir", type=Path, default=_default_model_dir())
    demo.add_argument("--manifest", type=Path, default=_default_manifest())
    args = parser.parse_args()
    if args.command == "demo-search":
        raise SystemExit(_demo_search(args.model_dir, args.manifest))
    uvicorn.run("photo_search.app:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()