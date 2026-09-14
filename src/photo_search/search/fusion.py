"""Reciprocal-rank fusion of the text and image-text recall channels.

Architecture §9.2 fixes the first version: each channel contributes a *rank*, the fused
score is ``Σ weight_i / (60 + rank_i)``, the weights start equal, and a raw BM25 score, a
vector distance and any future face score are never added to each other. The whole module
is a pure function of the per-channel rankings, so the deterministic retrieval comparison
can recompute every score by hand.

Ties are fully determined: score descending, then the number of channels that found the
asset descending, then ``asset_id`` ascending. Nothing here reads the database, so a
caller can replay one ranking without a catalog.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from photo_search.catalog.errors import CatalogError
from photo_search.search.tokens import VALIDATION_ERROR

#: The RRF constant frozen by architecture §9.2.
RRF_K = 60
#: The two recall channels of this task, and their first-version (equal) weights.
FTS_CHANNEL = "fts"
VECTOR_CHANNEL = "vector"
DEFAULT_CHANNEL_WEIGHTS: dict[str, float] = {FTS_CHANNEL: 1.0, VECTOR_CHANNEL: 1.0}


@dataclass(frozen=True)
class FusionHit:
    """One fused result: the asset, its RRF score and each channel's rank for it."""

    asset_id: int
    score: float
    matched_by: dict[str, int]

    @property
    def channels(self) -> tuple[str, ...]:
        return tuple(sorted(self.matched_by))

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": int(self.asset_id),
            "score": float(self.score),
            "matched_by": {name: int(rank) for name, rank in sorted(self.matched_by.items())},
            "channels": list(self.channels),
        }


def channel_ranks(asset_ids: Sequence[int]) -> dict[int, int]:
    """``asset_id -> rank`` for one channel; ranks start at 1 and the first hit wins."""
    ranks: dict[int, int] = {}
    for index, value in enumerate(asset_ids):
        ranks.setdefault(int(value), index + 1)
    return ranks


def reciprocal_rank_fusion(
    channels: Mapping[str, Sequence[int]],
    *,
    weights: Mapping[str, float] | None = None,
    k: int = RRF_K,
    limit: int | None = None,
) -> list[FusionHit]:
    """Fuse per-channel rankings into one deterministic order (architecture §9.2).

    An asset found by both channels appears once; ``matched_by`` keeps both ranks so a
    caller can explain the hit. The summation order over channels is fixed (sorted by
    channel name) so repeated runs produce bit-identical scores.
    """
    constant = int(k)
    if constant < 1:
        raise CatalogError(VALIDATION_ERROR, "the RRF constant must be at least 1")
    resolved = dict(DEFAULT_CHANNEL_WEIGHTS if weights is None else weights)
    for name in sorted(resolved):
        weight = float(resolved[name])
        if not math.isfinite(weight) or weight < 0:
            raise CatalogError(
                VALIDATION_ERROR,
                "a channel weight must be a finite non-negative number",
                details={"channel": str(name)},
            )

    scores: dict[int, float] = {}
    matched: dict[int, dict[str, int]] = {}
    for name in sorted(channels):
        weight = float(resolved.get(name, 1.0))
        for asset_id, rank in channel_ranks(channels[name]).items():
            scores[asset_id] = scores.get(asset_id, 0.0) + weight / (float(constant) + float(rank))
            matched.setdefault(asset_id, {})[str(name)] = int(rank)

    hits = [
        FusionHit(asset_id=asset_id, score=scores[asset_id], matched_by=matched[asset_id])
        for asset_id in scores
    ]
    hits.sort(key=lambda hit: (-hit.score, -len(hit.matched_by), hit.asset_id))
    if limit is not None:
        hits = hits[: max(0, int(limit))]
    return hits
