"""Read-only display quality thresholds for personal face organization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class QualityTier:
    name: str
    det_score: float
    face_pixels: float
    blur_variance: float


TIERS = (
    QualityTier("loose", 0.65, 48, 24),
    QualityTier("balanced", 0.70, 56, 40),
    QualityTier("strict", 0.75, 64, 60),
)
DISPLAY_TIER = TIERS[1]
POLICY_REVISION = "person-quality-display-v1"


def qualifies(
    *, quality_state: str | None, det_score: float | None,
    face_pixels: float | None, blur_variance: float | None,
    tier: QualityTier = DISPLAY_TIER,
) -> bool:
    """Missing persisted evidence is kept visible; known failing evidence is flagged."""
    values = (det_score, face_pixels, blur_variance)
    if quality_state is None or any(value is None for value in values):
        return True
    return (
        quality_state == "OK"
        and float(det_score) >= tier.det_score
        and float(face_pixels) >= tier.face_pixels
        and float(blur_variance) >= tier.blur_variance
    )


def qualifies_sql(alias: str = "f", tier: QualityTier = DISPLAY_TIER) -> str:
    """SQL predicate: incomplete legacy evidence stays visible for conservative fallback."""
    missing = " OR ".join(f"{alias}.{column} IS NULL" for column in (
        "quality_state", "det_score", "face_pixels", "blur_variance"
    ))
    return (
        f"({missing} OR ({alias}.quality_state = 'OK' "
        f"AND {alias}.det_score >= {tier.det_score} "
        f"AND {alias}.face_pixels >= {tier.face_pixels} "
        f"AND {alias}.blur_variance >= {tier.blur_variance}))"
    )


def fails_sql(alias: str = "f", tier: QualityTier = DISPLAY_TIER) -> str:
    """Known inadequate evidence; callers separately protect manual-control faces."""
    return f"NOT {qualifies_sql(alias, tier)}"


def reason_sql(alias: str = "f", tier: QualityTier = DISPLAY_TIER) -> str:
    """Independent display classification, leaving stored quality/organization reasons intact."""
    return (
        f"CASE WHEN {fails_sql(alias, tier)} THEN 'DISPLAY_LOW_QUALITY' ELSE NULL END"
    )


def classify_row(row: Any, tier: QualityTier = DISPLAY_TIER) -> str | None:
    return None if qualifies(
        quality_state=row["quality_state"], det_score=row["det_score"],
        face_pixels=row["face_pixels"], blur_variance=row["blur_variance"], tier=tier,
    ) else "DISPLAY_LOW_QUALITY"
