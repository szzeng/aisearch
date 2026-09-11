"""Local development entry point and the offline Chinese-CLIP demo."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
from pathlib import Path

import uvicorn

from photo_search.ingestion.cli import add_catalog_parser, run_catalog_command
from photo_search.runtime.device import DevicePolicy
from photo_search.workers.client import AiWorkerClient

#: A cold CUDA start on a power-gated laptop GPU can take minutes; validation runs use
#: generous deadlines so a slow start is never mistaken for a hung worker.
COLD_START_TIMEOUT_SECONDS = 900.0


def _default_model_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "PhotoSearchData" / "models" / "chinese_clip" / "vit-b-16"


def _default_manifest() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "tools" / "prepare_models" / "manifests" / "chinese-clip-vit-b-16.json"
    return Path.cwd() / "tools" / "prepare_models" / "manifests" / "chinese-clip-vit-b-16.json"


def _demo_search(
    model_dir: Path,
    manifest: Path,
    device: str | None,
    device_policy: DevicePolicy,
    output: Path | None,
) -> int:
    """Run the deterministic fixture search inside the managed AI worker."""
    with AiWorkerClient(
        model_dir, manifest, device=device, device_policy=device_policy
    ) as worker:
        health = worker.health(timeout=COLD_START_TIMEOUT_SECONDS)
        demo = worker.demo_search(timeout=COLD_START_TIMEOUT_SECONDS)
        usage = worker.provider_usage(timeout=COLD_START_TIMEOUT_SECONDS)
    report = {
        "status": "ok",
        "device": demo["device"],
        "runtime": health.get("runtime"),
        "provider_usage": usage["sessions"],
        "query": demo["query"],
        "query_l2_norm": demo["query_l2_norm"],
        "images": demo["images"],
        "top_k": demo["top_k"],
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    print(json.dumps(report, ensure_ascii=False))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Personal local photo search")
    subparsers = parser.add_subparsers(dest="command")
    demo = subparsers.add_parser("demo-search", help="run the offline Chinese-CLIP fixture search")
    demo.add_argument("--model-dir", type=Path, default=_default_model_dir())
    demo.add_argument("--manifest", type=Path, default=_default_manifest())
    demo.add_argument(
        "--device",
        default="cuda",
        help="cuda (default, GPU required) or cpu (explicit reported degradation)",
    )
    demo.add_argument("--device-policy", default=DevicePolicy.CPU_ALLOWED.value)
    demo.add_argument(
        "--output",
        type=Path,
        default=None,
        help="write the JSON report to this path as UTF-8 (console output still prints)",
    )
    add_catalog_parser(subparsers)
    args = parser.parse_args()
    if args.command == "catalog":
        raise SystemExit(run_catalog_command(args))
    if args.command == "demo-search":
        raise SystemExit(
            _demo_search(
                args.model_dir,
                args.manifest,
                args.device,
                DevicePolicy(args.device_policy),
                args.output,
            )
        )
    uvicorn.run("photo_search.app:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
