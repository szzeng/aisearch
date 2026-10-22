"""Frozen sparse storage policy qualified on the public album comparison.

Scores are auxiliary ranking evidence, never probabilities or human facts. We keep
weak top32 scores because official-threshold-only storage lost useful ordering in
calibration. A missing class in a completed record means zero auxiliary score; a
photo without a completed record must remain separately identifiable.
"""

from __future__ import annotations

import math
import struct

from photo_search.catalog.errors import CatalogError
from photo_search.tagging.model import CLASS_COUNT
from photo_search.tagging.runtime import TagPrediction

POLICY_ID = "ram-top32-fp16-rrf1-v1"
MAX_SCORES = 32
FUSION_WEIGHT = 1.0


def sparse_scores(prediction: TagPrediction) -> list[dict[str, int | float | bool]]:
    """Retain original-score top32, then encode fp16 and stable publication order."""
    scores = prediction.scores
    selected = prediction.selected_indices
    if (len(scores) != CLASS_COUNT or any(type(score) not in (int, float)
            or not math.isfinite(score) or not 0 <= score <= 1 for score in scores)
            or len(set(selected)) != len(selected) or any(type(index) is not int
                or not 0 <= index < CLASS_COUNT for index in selected)):
        raise CatalogError("TAGGER_OUTPUT_INVALID", "invalid tag scores or threshold-hit identity")
    selected_set = set(selected)
    chosen = sorted(range(CLASS_COUNT), key=lambda index: (-scores[index], index))[:MAX_SCORES]
    rows = [{"tag_id": index, "score": struct.unpack("<e", struct.pack("<e", scores[index]))[0],
             "above_threshold": index in selected_set} for index in chosen]
    # Quantization can create a score tie; canonical publication sorts tied IDs.
    return sorted(rows, key=lambda row: (-float(row["score"]), int(row["tag_id"])))
