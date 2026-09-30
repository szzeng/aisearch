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
from typing import Any

#: The frozen job protocol this processor speaks (ADR-0003 §1/§2).
CONTRACT_VERSION = "p2-contracts.v1"

OCR_PROCESSOR_ID = "ocr-text@1"
OCR_TASK_TYPE = "image_ocr"
OCR_SPEC_REVISION = "ocr-text-ppocrv4-mobile-v1"
OCR_ARTIFACT_REVISION = "ocr-document.v1"
OCR_OUTPUT_TYPE = "ocr_document.v1"
OCR_INPUT_TYPE = "asset_original"
OCR_MEDIA_TYPE = "application/json"
OCR_SOURCE = "RAPIDOCR_PPOCRV4_MOBILE"
OCR_ENGINE = "rapidocr-onnxruntime"
OCR_RUNTIME_BACKEND = "onnxruntime"

#: OCR executes pinned model weights but publishes a structured document, not a vector.
OCR_PROCESSOR_KIND = "structured_model"
OCR_DEVICE_POLICY = "CPU_ALLOWED"
OCR_SUPPORTED_DEVICES = ("gpu", "cpu")
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
#: Regions are kept down to this recognition score. P3-01 (architecture §8.2) requires low
#: confidence regions to stay visible and queryable, so the engine must not filter them the
#: way its default ``text_score`` of 0.5 would.
MIN_TEXT_SCORE = 0.0

#: Regions at or below this score are the ones the UI must mark as low confidence.
LOW_CONFIDENCE_THRESHOLD = 0.6

#: A region whose box overlaps a kept region by at least this IoU is the same physical text
#: seen twice in the overlap band of two slices.
DUPLICATE_IOU = 0.5

#: Bounded result: one document may carry at most this many regions.
MAX_REGIONS = 2_000

#: Bounded execution: the wall-clock budget of one OCR job on this CPU path.
DEFAULT_DEADLINE_MS = 60_000

#: Coordinates are rounded to this many decimals before they become part of the artifact,
#: so the same pixels always produce the same bytes.
COORDINATE_DECIMALS = 2

#: The frozen engine settings recorded in every artifact payload. They are the values the
#: P0-04 manifest measured for the pinned files, plus the two P3-01 decisions above.
ENGINE_CONFIG: dict[str, Any] = {
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
    "text_score": MIN_TEXT_SCORE,
    "max_side_len": MAX_LONG_EDGE,
    "min_side_len": 30,
    "providers": ["CPUExecutionProvider"],
}


def pinned_entry(role: str) -> dict[str, Any]:
    for entry in PINNED_WEIGHTS:
        if str(entry["role"]) == str(role):
            return dict(entry)
    raise KeyError(role)
