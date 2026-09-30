"""Pinned PP-OCRv6 small weights, processor identity and bounded execution limits."""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: The frozen job protocol this processor speaks (ADR-0003 §1/§2).
CONTRACT_VERSION = "p2-contracts.v1"

OCR_PROCESSOR_ID = "ocr-text@2"
OCR_TASK_TYPE = "image_ocr"
OCR_SPEC_REVISION = "ocr-text-ppocrv6-small-v1"
OCR_ARTIFACT_REVISION = "ocr-document.v1"
OCR_OUTPUT_TYPE = "ocr_document.v1"
OCR_INPUT_TYPE = "asset_original"
OCR_MEDIA_TYPE = "application/json"
OCR_SOURCE = "RAPIDOCR_PPOCRV6_SMALL"
OCR_ENGINE = "rapidocr"
OCR_RUNTIME_BACKEND = "onnxruntime"

#: OCR executes pinned model weights but publishes a structured document, not a vector.
OCR_PROCESSOR_KIND = "structured_model"
OCR_DEVICE_POLICY = "CPU_ALLOWED"
OCR_SUPPORTED_DEVICES = ("gpu", "cpu")
OCR_SUPPORTED_PRECISIONS = ("FP32",)
OCR_MODEL_ID = "rapidocr-ppocrv6-small"
OCR_MODEL_REVISION = "rapidocr@3.9.2"

#: ``<data_root>/models`` is the P0-04 model root; every pinned path is relative to it.
MODELS_DIRNAME = "models"

#: Downloads are hash-checked; the classifier retains the upstream PP-OCRv4 graph.
PINNED_WEIGHTS: tuple[dict[str, Any], ...] = (
    {'role': 'detection', 'path': 'ocr/ppocr-v6-small/PP-OCRv6_det_small.onnx', 'bytes': 9929594, 'sha256': '090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f', 'url': 'https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv6/det/PP-OCRv6_det_small.onnx'},
    {'role': 'classification', 'path': 'ocr/ppocr-v6-small/ch_ppocr_mobile_v2.0_cls_mobile.onnx', 'bytes': 585532, 'sha256': 'e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c', 'url': 'https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx'},
    {'role': 'recognition', 'path': 'ocr/ppocr-v6-small/PP-OCRv6_rec_small.onnx', 'bytes': 21234383, 'sha256': '6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884', 'url': 'https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv6/rec/PP-OCRv6_rec_small.onnx'},
    {'role': 'config', 'path': 'ocr/ppocr-v6-small/config.yaml', 'bytes': 4225, 'sha256': 'e790d4e434a7eaf48d3b659f44eb058c74a3643bb1b37a60fbc9c36bebd854f2'},
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
ENGINE_VERSION = "3.9.2"

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
