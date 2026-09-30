"""The complete model/processor identity, shared by indexing and queries."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from photo_search.runtime.device import DevicePolicy
from photo_search.scheduler.contracts import CONTRACT_VERSION, ProcessorSpec, canonical_json

DEFAULT_MANIFEST = Path(__file__).with_name("fg-clip2-base.json")
SPEC_REVISION = "fg-clip2-base-v2"
MODEL_SUBDIR = ("models", "fg-clip2-base")


def pinned_embedding_spec(
    manifest_path: str | Path,
    *,
    device_policy: str | DevicePolicy = DevicePolicy.CPU_ALLOWED,
    runtime_package_id: str | None = None,
    runtime_backend: str | None = None,
    supported_devices: Sequence[str] | None = None,
    supported_precisions: Sequence[str] | None = None,
) -> ProcessorSpec:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    digest = hashlib.sha256(canonical_json(manifest).encode()).hexdigest()
    model, embedding = manifest["model"], manifest["embedding"]
    return ProcessorSpec.from_envelope({
        "contract_version": CONTRACT_VERSION, "spec_revision": SPEC_REVISION,
        "processor_id": f"fg-clip2-base@{digest[:16]}", "processor_kind": "embedding_model",
        "task_type": "image_embedding", "config_hash": digest,
        "input_type": "asset", "output_type": "embedding",
        "device_policy": DevicePolicy(device_policy).name,
        "runtime_backend": runtime_backend or "pytorch",
        "runtime_package_id": runtime_package_id or "torch",
        "supported_devices": list(supported_devices or ("gpu", "cpu")),
        "supported_precisions": list(supported_precisions or ("FP16", "FP32")),
        "max_batch_size": 1, "cancellation_supported": False, "resource_measurements": [],
        "model_id": model["id"], "model_revision": model["resolved_revision"],
        "weights": [{"name": f["path"], "revision": model["resolved_revision"],
                     "sha256": f["sha256"]} for f in manifest["files"]],
        "image_preprocess_version": f"fgclip2-upright-{digest[:16]}",
        "query_preprocess_version": f"fgclip2-query-{digest[:16]}",
        "tokenizer_version": model["resolved_revision"],
        "instruction_template_version": "none-v1", "normalization_version": "L2-v1",
        "dimension": int(embedding["dimension"]), "metric": embedding["metric"],
        "space_id": f"fg-clip2-base:{digest[:24]}",
        "max_pixels": int(manifest["preprocessing"]["max_num_patches"]) * 16 * 16,
        "max_tokens": int(manifest["tokenizer"]["max_length"]),
    })


def verify_model_files(directory: Path, manifest: dict) -> None:
    for entry in manifest["files"]:
        path = directory / entry["path"]
        if not path.is_file() or path.stat().st_size != entry["bytes"]:
            raise ValueError(f"model file missing or incomplete: {entry['path']}")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != entry["sha256"]:
            raise ValueError(f"model file checksum mismatch: {entry['path']}")
