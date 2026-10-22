"""Frozen P3-01 OCR identity, pinned-weight table and bounded-execution limits.

This module is deliberately import-cheap: it holds strings and numbers only, so the
read-only API projections may describe the OCR layer without pulling ONNX Runtime, numpy,
OpenCV or Pillow into a process that never runs a model (the same rule the scheduler
projection follows for the runtime package id).

The pinned files are the exact artifacts P0-04 measured and recorded in
``tools/prepare_models/manifests/face-ocr.json``. They are mirrored here - not read from
``tools/`` at runtime, because the frozen application bundle does not ship that directory -
and ``tests/unit/test_p3_ocr_contract.py`` fails if the mirror and the manifest ever
disagree. A build therefore cannot silently start using the wheel's own copies of the same
three graphs: the product only ever opens the files under the user data root, after their
size and SHA-256 match this table.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from photo_search.search.tokens import TOKENIZER_VERSION

#: The frozen job protocol this processor speaks (ADR-0003 §1/§2).
CONTRACT_VERSION = "p2-contracts.v1"

OCR_LEGACY_PROCESSOR_ID = "ocr-text@1"
OCR_V2_CPU_PROCESSOR_ID = "ocr-text-cpu-fp32@2"
OCR_V2_GPU_PROCESSOR_ID = "ocr-text-cuda-fp32@2"
OCR_V3_CPU_PROCESSOR_ID = "ocr-text-cpu-fp32@3"
OCR_V3_GPU_PROCESSOR_ID = "ocr-text-cuda-fp32@3"
OCR_CPU_PROCESSOR_ID = "ocr-text-cpu-fp32@4"
OCR_GPU_PROCESSOR_ID = "ocr-text-cuda-fp32@4"
# Keep the executable and retired identities in one declaration. A future processor
# upgrade must explicitly move an identity from active to retired here.
OCR_ACTIVE_PROCESSOR_IDS = (OCR_CPU_PROCESSOR_ID, OCR_GPU_PROCESSOR_ID)
OCR_RETIRED_PROCESSOR_IDS = (
    OCR_LEGACY_PROCESSOR_ID, OCR_V2_CPU_PROCESSOR_ID, OCR_V2_GPU_PROCESSOR_ID,
    OCR_V3_CPU_PROCESSOR_ID, OCR_V3_GPU_PROCESSOR_ID,
)
OCR_READABLE_PROCESSOR_IDS = (*OCR_RETIRED_PROCESSOR_IDS, *OCR_ACTIVE_PROCESSOR_IDS)
# The auto path starts on CUDA; legacy remains a read-only historical identity.
OCR_PROCESSOR_ID = OCR_GPU_PROCESSOR_ID
OCR_PROCESSOR_IDS = OCR_ACTIVE_PROCESSOR_IDS
OCR_TASK_TYPE = "image_ocr"
OCR_V3_CPU_SPEC_REVISION = "ocr-text-ppocrv4-mobile-cpu-fp32-v3"
OCR_V3_GPU_SPEC_REVISION = "ocr-text-ppocrv4-mobile-cuda-fp32-v3"
OCR_CPU_SPEC_REVISION = "ocr-text-ppocrv4-mobile-cpu-fp32-v4"
OCR_GPU_SPEC_REVISION = "ocr-text-ppocrv4-mobile-cuda-fp32-v4"
OCR_SPEC_REVISION = OCR_GPU_SPEC_REVISION
OCR_ARTIFACT_REVISION = "ocr-document.v1"
OCR_OUTPUT_TYPE = "ocr_document.v1"
OCR_INPUT_TYPE = "asset_original"
OCR_MEDIA_TYPE = "application/json"
OCR_SOURCE = "RAPIDOCR_PPOCRV4_MOBILE"
OCR_ENGINE = "rapidocr-onnxruntime"
OCR_RUNTIME_BACKEND = "onnxruntime"

#: OCR executes pinned model weights but publishes a structured document, not a vector.
OCR_PROCESSOR_KIND = "structured_model"
OCR_DEVICE_POLICY = "GPU_REQUIRED"
OCR_SUPPORTED_DEVICES = ("gpu",)
OCR_CPU_DEVICE_POLICY = "CPU_PREFERRED"
OCR_CPU_SUPPORTED_DEVICES = ("cpu",)
OCR_SUPPORTED_PRECISIONS = ("FP32",)
OCR_MODEL_ID = "rapidocr-ppocrv4-mobile"
OCR_MODEL_REVISION = "rapidocr-onnxruntime@1.4.4"

#: ``<data_root>/models`` is the P0-04 model root; every pinned path is relative to it.
MODELS_DIRNAME = "models"

#: ``(role, relative path, bytes, sha256)`` in the frozen order of the P0-04 manifest.
PINNED_WEIGHTS: tuple[dict[str, Any], ...] = (
    {
        "role": "detection",
        "path": "ocr/ppocr-v4-mobile/ch_PP-OCRv4_det_infer.onnx",
        "bytes": 4_745_517,
        "sha256": "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9",
    },
    {
        "role": "classification",
        "path": "ocr/ppocr-v4-mobile/ch_ppocr_mobile_v2.0_cls_infer.onnx",
        "bytes": 585_532,
        "sha256": "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
    },
    {
        "role": "recognition",
        "path": "ocr/ppocr-v4-mobile/ch_PP-OCRv4_rec_infer.onnx",
        "bytes": 10_857_958,
        "sha256": "48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b",
    },
    {
        "role": "config",
        "path": "ocr/ppocr-v4-mobile/config.yaml",
        "bytes": 1_221,
        "sha256": "bf94a1da4cba828e67b1d61e27cee14d9e7da27c9f272e04048a17e41ae97332",
    },
)

WEIGHT_ROLES = tuple(str(entry["role"]) for entry in PINNED_WEIGHTS)


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


#: Identity of the weight set: the digest of the pinned (role, path, sha256) triples. It is
#: derived from the table above, so it changes the moment a pinned graph changes.
MODEL_FINGERPRINT = hashlib.sha256(
    _canonical(
        [
            {"role": str(entry["role"]), "path": str(entry["path"]), "sha256": str(entry["sha256"])}
            for entry in PINNED_WEIGHTS
        ]
    ).encode("utf-8")
).hexdigest()

#: The frozen processor ``config_hash``: the SHA-256 of the pinned ``config.yaml`` (the
#: detection thresholds, the classifier shape and the recognition shape all come from it).
CONFIG_HASH = str(
    next(entry["sha256"] for entry in PINNED_WEIGHTS if entry["role"] == "config")
)

#: The engine version recorded in every artifact payload.
ENGINE_VERSION = "1.4.4"
OCR_RUNTIME_PACKAGE_ID = "onnxruntime-gpu@1.30.0"


def execution_config(device: str, *, version: int = 4) -> dict[str, Any]:
    """Canonical profile identity; v2/v3 remain frozen for historical readers."""
    normalized = str(device)
    if normalized not in ("cpu", "gpu"):
        raise ValueError("OCR execution device must be cpu or gpu")
    if version not in (2, 3, 4):
        raise ValueError("OCR execution config version must be 2, 3 or 4")
    provider = "CPUExecutionProvider" if normalized == "cpu" else "CUDAExecutionProvider"
    if version == 2:
        deduplication = None
        orientation_retry = None
    elif version == 3:
        deduplication = V3_DEDUPLICATION_CONFIG
        orientation_retry = V3_ORIENTATION_RETRY_CONFIG
    else:
        deduplication = DEDUPLICATION_CONFIG
        orientation_retry = ORIENTATION_RETRY_CONFIG
    engine_config = deepcopy(
        LEGACY_ENGINE_CONFIG if version == 2
        else V3_ENGINE_CONFIG if version == 3
        else ENGINE_CONFIG
    )
    engine_config["providers"] = [provider]
    result = {
        "version": f"ocr-execution-config.v{version}",
        "pinned_config_yaml_sha256": CONFIG_HASH,
        "model_fingerprint": MODEL_FINGERPRINT,
        "engine": OCR_ENGINE,
        "engine_version": ENGINE_VERSION,
        "recognition_and_preprocess": engine_config,
        "output": {
            "artifact_revision": OCR_ARTIFACT_REVISION,
            "output_type": OCR_OUTPUT_TYPE,
            "low_confidence_threshold": 0.6 if version < 4 else LOW_CONFIDENCE_THRESHOLD,
            "coordinate_decimals": 2 if version < 4 else COORDINATE_DECIMALS,
        },
        "provider": provider,
        "precision": "FP32",
        "runtime_package_id": OCR_RUNTIME_PACKAGE_ID,
    }
    if version in (3, 4):
        engine_config["orientation_retry"] = deepcopy(orientation_retry)
        result["output"]["deduplication"] = deepcopy(deduplication)
    return result


def execution_config_hash(device: str, *, version: int = 4) -> str:
    return hashlib.sha256(
        _canonical(execution_config(device, version=version)).encode("utf-8")
    ).hexdigest()



# --------------------------------------------------------------------------- decoding
#: Hard ceiling on the *declared* pixel count of one source image. It matches the P2-07
#: upload ceiling (60 MP): a larger image is refused as a bounded-input failure instead of
#: being decoded and then thrown away.
MAX_DECODE_PIXELS = 60_000_000

#: Long edge of the working image. The pinned detector is fed a bounded, still legible
#: raster (never the 320/448 px thumbnail/preview sizes, which would destroy small text).
MAX_LONG_EDGE = 2_000

#: Above this many working pixels the image is processed as overlapping tiles, so the
#: detector never receives an unbounded canvas and small glyphs keep their pixel height.
SINGLE_TILE_MAX_PIXELS = 1_800_000
SLICE_SIZE = 1_280
SLICE_OVERLAP = 160
#: Safety valve: a bounded number of slices per job. Exceeding it is a structured refusal,
#: never a silent downscale of the requested recognition quality.
MAX_SLICES = 16

# -------------------------------------------------------------------------- recognition
#: Minimum recognition score passed to RapidOCR 1.4.4's native text-result filter for v4.
MIN_TEXT_SCORE = 0.5

#: Regions at or below this score are the ones the UI must mark as low confidence.
LOW_CONFIDENCE_THRESHOLD = 0.6

#: A region whose box overlaps a kept region by at least this IoU is the same physical text
#: seen twice in the overlap band of two slices.
DUPLICATE_IOU = 0.5

# The active profile records every decision used by text-aware slice deduplication.
# Historical v2 identities deliberately omit this new output policy.
DEDUPLICATION_CONFIG: dict[str, Any] = {
    "version": "text-aware-overlap.v1",
    "comparison": "whitespace-elision-if-token-equivalent",
    "comparison_tokenizer_version": TOKENIZER_VERSION,
    "duplicate_iou": DUPLICATE_IOU,
    "substring_coverage": 0.8,
    "line_overlap": 0.8,
    "line_max_thickness_ratio": 1.5,
    "line_center_tolerance": 0.25,
    "line_geometry": "polygon-major-axis-projection",
    "truncation_min_extension": 0.5,
    "truncation_edge_tolerance": 1.0,
    "full_text_confidence_gt": LOW_CONFIDENCE_THRESHOLD,
    "prefer": "longer-contained-text-then-confidence",
    "uncertain_overlap": "keep",
    "tie_break": "normalized-length,confidence,box,text,polygon",
}

ORIENTATION_RETRY_CONFIG: dict[str, Any] = {
    "version": "rotated-low-confidence-original-retry.v1",
    "trigger": "applied_180_and_empty_or_score_lte_threshold",
    "trigger_confidence_threshold": 0.6,
    "retry_orientation": "original_crop",
    "retry_min_confidence": 0.6,
    "retry_min_gain": 0.1,
    "selection": "alternative_score_gt_min_and_gte_classified_score_plus_gain",
    "otherwise": "keep_classified_result",
    "max_extra_recognition_passes": 1,
}
V3_DEDUPLICATION_CONFIG = deepcopy(DEDUPLICATION_CONFIG)
V3_ORIENTATION_RETRY_CONFIG = deepcopy(ORIENTATION_RETRY_CONFIG)

#: Bounded result: one document may carry at most this many regions.
MAX_REGIONS = 2_000

#: Bounded execution: the wall-clock budget of one OCR job on this CPU path.
DEFAULT_DEADLINE_MS = 60_000

#: Coordinates are rounded to this many decimals before they become part of the artifact,
#: so the same pixels always produce the same bytes.
COORDINATE_DECIMALS = 2

#: Active v4 engine settings recorded in every artifact payload. Historical profiles below
#: remain separate so changing this policy cannot redefine old processor identities.
LEGACY_ENGINE_CONFIG: dict[str, Any] = {
    "channel_order": "BGR",
    "det_limit_side_len": 736,
    "det_limit_type": "min",
    "det_thresh": 0.3,
    "det_box_thresh": 0.5,
    "det_unclip_ratio": 1.6,
    "det_use_dilation": True,
    "det_score_mode": "fast",
    "cls_image_shape": [3, 48, 192],
    "cls_thresh": 0.9,
    "rec_image_shape": [3, 48, 320],
    "use_cls": True,
    "use_det": True,
    "use_rec": True,
    "text_score": 0.0,
    "max_side_len": 2_000,
    "min_side_len": 30,
    "providers": ["CPUExecutionProvider"],
}

ENGINE_CONFIG: dict[str, Any] = deepcopy(LEGACY_ENGINE_CONFIG)
ENGINE_CONFIG.update(text_score=MIN_TEXT_SCORE, max_side_len=MAX_LONG_EDGE)
V3_ENGINE_CONFIG: dict[str, Any] = deepcopy(LEGACY_ENGINE_CONFIG)
V3_ENGINE_CONFIG.update(text_score=0.0, max_side_len=MAX_LONG_EDGE)

OCR_V2_CPU_CONFIG_HASH = execution_config_hash("cpu", version=2)
OCR_V2_GPU_CONFIG_HASH = execution_config_hash("gpu", version=2)
OCR_V3_CPU_CONFIG_HASH = execution_config_hash("cpu", version=3)
OCR_V3_GPU_CONFIG_HASH = execution_config_hash("gpu", version=3)
OCR_CPU_CONFIG_HASH = execution_config_hash("cpu", version=4)
OCR_GPU_CONFIG_HASH = execution_config_hash("gpu", version=4)


def pinned_entry(role: str) -> dict[str, Any]:
    for entry in PINNED_WEIGHTS:
        if str(entry["role"]) == str(role):
            return dict(entry)
    raise KeyError(role)
