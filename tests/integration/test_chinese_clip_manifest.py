"""P0-03 manifest contract independent of external model artifact availability."""

from __future__ import annotations

import json
from pathlib import Path


def test_chinese_clip_manifest_declares_frozen_cpu_contract() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "tools/prepare_models/manifests/chinese-clip-vit-b-16.json"
    )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["model"]["id"] == "OFA-Sys/chinese-clip-vit-base-patch16"
    assert len(manifest["model"]["resolved_revision"]) == 40
    assert manifest["embedding"] == {"dimension": 512, "metric": "cosine", "normalization": "L2"}
    assert manifest["tokenizer"]["max_length"] == 52
    assert manifest["onnx"]["batch"] == 1
    assert manifest["runtime"]["device"] == "CPU"
    artifacts = [
        *manifest["artifacts"]["repository_files"].values(),
        *manifest["artifacts"]["onnx"].values(),
    ]
    for artifact in artifacts:
        assert artifact["bytes"] > 0
        assert len(artifact["sha256"]) == 64
