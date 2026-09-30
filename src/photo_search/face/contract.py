"""Frozen P3-02 face identity, pinned weights and the *calibrated* grouping thresholds.

This module holds strings and numbers only. It is imported by the read-only API
projection, so nothing here may pull ONNX Runtime, numpy or Pillow into a process that
never runs a model.

Three decisions deserve their reasons written down next to the values:

**The device policy is the ADR-0001 path for a GPU-capable model.** The face detector and
the recogniser are the models ADR-0001 §2 names for CUDA; ``CPU_ALLOWED`` therefore means
"default CUDA, an explicit CPU request is served and reported as a degradation, and a GPU
fault never becomes an implicit CPU run". ``photo_search.face.engine`` resolves the device
before creating a session, disables the CPU fallback for a CUDA session, and records what
the runtime really offered - an unusable CUDA provider is a recorded capability fact, not
a silent downgrade.

**The vectors live in their own space.** ``FACE_SPACE_ID`` is derived from the pinned
recognition graph and can never equal the image/text space of the image encoder, so a face
vector cannot be compared with - or accidentally written into - the photo index.

**The thresholds are calibrated, not conventional.** ``MERGE_THRESHOLD`` and
``SUGGEST_THRESHOLD`` come from ``CALIBRATION`` below: the measured genuine and impostor
similarity distributions of the pinned pipeline (see the P3-02 report §5 for the fixture
inventory and the raw numbers). The architecture explicitly forbids reusing a generic
constant such as 0.45, so the derivation formula is part of the contract:

    gap            = min_genuine - max_impostor
    MERGE          = max_impostor + 0.8 * gap      (0.2061 + 0.8 * 0.4372 = 0.5559 -> 0.56)
    SUGGEST        = max_impostor + 0.2 * gap      (0.2061 + 0.2 * 0.4372 = 0.2936 -> 0.29)

Anything in ``[SUGGEST, MERGE)`` is *not* merged: it becomes a suggestion a human has to
confirm. That is the architecture §8.3 "宁可漏，不能错" direction, and it is why the merge
threshold sits far above the highest measured impostor similarity instead of in the middle
of the gap.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: The frozen job protocol this processor speaks (ADR-0003 §1/§2).
CONTRACT_VERSION = "p2-contracts.v1"

FACE_PROCESSOR_ID = "face-model@1"
FACE_TASK_TYPE = "image_face"
FACE_SPEC_REVISION = "face-buffalo-l-scrfd-arcface-v1"
FACE_DETECTOR_ID = "scrfd-det-10g@1"
FACE_ARTIFACT_REVISION = "face-document.v1"
#: An embedding-model spec must declare ``output_type = "embedding"`` in the frozen
#: contract. The *published* artifact is nevertheless structured, and the reason is
#: recorded in the report §3: an ``embedding`` artifact is asset-keyed and would create an
#: ``index_entries`` row in the image space, which is exactly the identity reuse P3-02
#: forbids. The canonical face vectors are written to ``face_embeddings`` in the same
#: transaction instead.
FACE_OUTPUT_TYPE = "embedding"
FACE_INPUT_TYPE = "asset_original"
FACE_MEDIA_TYPE = "application/json"
FACE_SOURCE = "INSIGHTFACE_BUFFALO_L"
FACE_RUNTIME_BACKEND = "onnxruntime"
FACE_MODEL_ID = "insightface/buffalo_l"
#: The P0-04 pack release the pinned files were extracted from.
FACE_MODEL_REVISION = "buffalo_l@model-zoo-2026-09-11"

FACE_PROCESSOR_KIND = "embedding_model"
#: ADR-0003 §2 / ADR-0001 §2: detection and recognition are the CUDA-default models.
FACE_DEVICE_POLICY = "CPU_ALLOWED"
FACE_SUPPORTED_DEVICES = ("gpu", "cpu")
FACE_SUPPORTED_PRECISIONS = ("FP32",)

#: ``<data_root>/models`` is the P0-04 model root; every pinned path is relative to it.
MODELS_DIRNAME = "models"

#: ``(role, relative path, bytes, sha256)`` exactly as ``tools/prepare_models/manifests/
#: face-ocr.json`` records them. They are mirrored here - not read from ``tools/`` at
#: runtime, because the frozen bundle does not ship that directory - and
#: ``tests/unit/test_p3_face_contract.py`` fails if the mirror and the manifest disagree.
PINNED_WEIGHTS: tuple[dict[str, Any], ...] = (
    {
        "role": "detection",
        "path": "face/buffalo_l/det_10g.onnx",
        "bytes": 16_923_827,
        "sha256": "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91",
    },
    {
        "role": "recognition",
        "path": "face/buffalo_l/w600k_r50.onnx",
        "bytes": 174_383_860,
        "sha256": "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
    },
)

WEIGHT_ROLES = tuple(str(entry["role"]) for entry in PINNED_WEIGHTS)

#: The image-preprocessing generation: bounded source raster, fixed 640x640 detector
#: canvas, ArcFace 5-point similarity alignment to 112x112, RGB, per-channel
#: ``(value - mean) * scale``.
FACE_IMAGE_PREPROCESS_VERSION = "face-scrfd-640-align112-rgb-v1"
#: This processor has no query side and no tokenizer; the frozen embedding spec still
#: requires the fields, so they are declared explicitly as "none" rather than left out.
FACE_QUERY_PREPROCESS_VERSION = "none-v1"
FACE_TOKENIZER_VERSION = "none-v1"
FACE_INSTRUCTION_TEMPLATE_VERSION = "none-v1"
FACE_NORMALIZATION_VERSION = "L2-v1"
FACE_METRIC = "cosine"
FACE_NORMALIZATION = "L2"
FACE_DIMENSION = 512


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


#: Identity of the weight set: the digest of the pinned (role, path, sha256) triples.
MODEL_FINGERPRINT = hashlib.sha256(
    _canonical(
        [
            {"role": str(entry["role"]), "path": str(entry["path"]), "sha256": str(entry["sha256"])}
            for entry in PINNED_WEIGHTS
        ]
    ).encode("utf-8")
).hexdigest()

#: The face space: pinned to the recognition graph, so it can never collide with the
#: image/text space of the photo index, and a re-pinned graph opens a *new* space instead
#: of silently mixing two generations of vectors.
FACE_SPACE_ID = f"insightface-buffalo-l:{str(PINNED_WEIGHTS[1]['sha256'])[:12]}"

#: Prefix of the independent candidate-index generation id (face-keyed, never asset-keyed).
FACE_GENERATION_PREFIX = "face-gen-"

# --------------------------------------------------------------------------- limits
#: Hard ceiling on the *declared* pixel count of one source image; it matches the P2-07
#: upload ceiling and the P3-01 OCR bound, so an oversized image is refused as a bounded
#: input instead of being decoded and then thrown away.
MAX_DECODE_PIXELS = 60_000_000

#: Long edge of the bounded working raster. The detector is fed a fixed 640x640 canvas
#: fitted from this raster (the size P0-04 measured), while alignment crops are taken from
#: the working raster, so a face keeps as much real detail as the picture has.
WORKING_LONG_EDGE = 1_280
DETECT_CANVAS = 640
#: Neutral border value used when a raster smaller than the canvas is pasted at native size.
DETECT_PAD_VALUE = 128

#: The measured SCRFD interface of ``det_10g.onnx``: three strides, two anchors per
#: location, nine outputs (scores, boxes, keypoints).
DETECT_STRIDES = (8, 16, 32)
DETECT_ANCHORS = 2
EXPECTED_STRIDES = 3
DETECT_SCORE_THRESHOLD = 0.5
DETECT_NMS_IOU = 0.4

#: Bounded result: one asset may carry at most this many faces.
MAX_FACES = 200

#: Bounded execution: the wall-clock budget of one face job on the CPU baseline.
DEFAULT_DEADLINE_MS = 120_000

#: Coordinates and embeddings are rounded before they become part of the artifact, so the
#: same pixels always produce the same artifact bytes on the same generation.
COORDINATE_DECIMALS = 2
EMBEDDING_DECIMALS = 7

#: Preprocessing measured by P0-04 for the pinned detection graph (RGB, mean 127.5,
#: scale 1/128) and the recognition graph (112x112, mean 127.5, scale 1/127.5).
DETECT_MEAN = 127.5
DETECT_SCALE = 0.0078125
ALIGN_SIZE = 112
RECOGNITION_MEAN = 127.5
RECOGNITION_SCALE = 0.00784313725490196

#: The ArcFace 5-point template (``insightface`` ``face_align``, 112x112). The alignment
#: is a 4-parameter similarity transform estimated from these points.
ALIGN_TEMPLATE: tuple[tuple[float, float], ...] = (
    (38.2946, 51.6963),
    (73.5318, 51.5014),
    (56.0252, 71.7366),
    (41.5493, 92.3655),
    (70.7299, 92.2041),
)

# ------------------------------------------------------------------------- quality
#: A face below this many source pixels is *detected* but never a candidate representative
#: and never a confident merge. The measured fixtures' smallest face is 75.4 px.
MIN_FACE_PIXELS = 48.0
#: Detector score floor below which a face counts as low quality (the measured minimum on
#: the fixtures is 0.83; the detector itself refuses anything under 0.5).
LOW_DETECTION_SCORE = 0.65
#: Variance of the Laplacian of the aligned 112x112 crop; the measured minimum on the
#: fixtures is 90.7, and a 28-pixel face upscaled to the alignment size stays far below it.
MIN_BLUR_VARIANCE = 24.0

#: The quality reasons a low-quality face can carry. They are also the *only* automatic
#: reasons that keep a face out of the merge pool while still letting it be suggested.
QUALITY_SMALL = "SMALL"
QUALITY_BLURRY = "BLURRY"
QUALITY_LOW_SCORE = "LOW_DETECTION_SCORE"

# ------------------------------------------------------------------- association
#: Highest similarity this build accepts as "a different person" and the lowest it accepts
#: as "the same person", both derived from ``CALIBRATION`` (see the module docstring).
SUGGEST_THRESHOLD = 0.29
MERGE_THRESHOLD = 0.56
#: A guard rather than a calibrated value: no fixture produced a near-tie, so this only
#: decides that an ambiguous best/second-best pair goes to human confirmation.
NEAR_TIE_MARGIN = 0.08
#: How many faces of one person may act as candidates. Architecture §8.3: a person may have
#: several representatives, but never unbounded - this is what keeps candidate matching an
#: incremental scan over representatives instead of an all-pairs comparison.
MAX_REPRESENTATIVES_PER_PERSON = 8
#: The suggestion reasons a human has to look at.
REASON_BORDERLINE = "BORDERLINE"
REASON_LOW_QUALITY = "LOW_QUALITY"
REASON_SAME_IMAGE_CONFLICT = "SAME_IMAGE_CONFLICT"
REASON_NEAR_TIE = "NEAR_TIE"

#: Every automatic assignment this build writes is ``AUTO``; ``MANUAL`` rows belong to the
#: P3-03/P3-04 human-control paths, which this build only ever *reads*.
ASSIGNMENT_AUTO = "AUTO"
ASSIGNMENT_MANUAL = "MANUAL"
#: The human-control tables of architecture §4.2/§8.3.4. They do not exist yet (P3-03/P3-04
#: create them); when they do, the automatic path must treat their rows as authoritative and
#: never create, change or delete anything about such a face.
HUMAN_CONTROL_TABLES = ("face_assignment_controls", "face_exclusions")

#: Measured calibration of this pipeline, recorded so the thresholds above are traceable
#: instead of magic. Fixtures: the 8 real public-domain faces of the P3-02 fixture set
#: (astronaut, six faces of the insightface group photo, one portrait) plus synthetic
#: identity-preserving variants. All numbers are cosine similarity.
CALIBRATION: dict[str, Any] = {
    "measured_at": "2026-09-14",
    "device": "cpu",
    "identities": 8,
    "impostor_pairs": 28,
    "impostor_min": -0.0831,
    "impostor_p05": -0.0608,
    "impostor_p50": 0.0099,
    "impostor_p95": 0.1479,
    "impostor_max": 0.2061,
    "genuine_pipeline_pairs": 40,
    "genuine_pipeline_min": 0.8527,
    "genuine_pipeline_p05": 0.8720,
    "genuine_pipeline_p50": 0.9740,
    "genuine_align_pairs": 32,
    "genuine_align_min": 0.6433,
    "genuine_align_p05": 0.6952,
    "genuine_align_p50": 0.9216,
    "det_score_min": 0.8052,
    "min_face_pixels": 72.55,
    "blur_variance_min": 92.55,
    "min_genuine": 0.6433,
    "max_impostor": 0.2061,
    "gap": 0.4372,
    "merge_threshold": MERGE_THRESHOLD,
    "suggest_threshold": SUGGEST_THRESHOLD,
    "derivation": (
        "merge = max_impostor + 0.8 * gap; suggest = max_impostor + 0.2 * gap; "
        "the near-tie margin is a guard, not a measured value"
    ),
}

#: The frozen engine configuration of the artifact payload. ``config_hash`` is the digest of
#: *this* mapping, so a change in any preprocessing or decision parameter changes the
#: processor identity instead of silently redefining it.
ENGINE_CONFIG: dict[str, Any] = {
    "channel_order": "RGB",
    "detect_canvas": DETECT_CANVAS,
    "detect_pad_value": DETECT_PAD_VALUE,
    "working_long_edge": WORKING_LONG_EDGE,
    "detect_mean": DETECT_MEAN,
    "detect_scale": DETECT_SCALE,
    "detect_score_threshold": DETECT_SCORE_THRESHOLD,
    "detect_nms_iou": DETECT_NMS_IOU,
    "detect_strides": list(DETECT_STRIDES),
    "detect_anchors": DETECT_ANCHORS,
    "align_size": ALIGN_SIZE,
    "align_template": [list(point) for point in ALIGN_TEMPLATE],
    "recognition_mean": RECOGNITION_MEAN,
    "recognition_scale": RECOGNITION_SCALE,
    "max_faces": MAX_FACES,
    "min_face_pixels": MIN_FACE_PIXELS,
    "low_detection_score": LOW_DETECTION_SCORE,
    "min_blur_variance": MIN_BLUR_VARIANCE,
}

CONFIG_HASH = hashlib.sha256(_canonical(ENGINE_CONFIG).encode("utf-8")).hexdigest()


def pinned_entry(role: str) -> dict[str, Any]:
    for entry in PINNED_WEIGHTS:
        if str(entry["role"]) == str(role):
            return dict(entry)
    raise KeyError(role)
