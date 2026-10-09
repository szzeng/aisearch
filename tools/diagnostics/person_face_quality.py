"""Read-only, on-demand face-quality estimates for a catalog SQLite file."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from photo_search.face.contract import FACE_DETAIL_SPEC_REVISION, FACE_SPEC_REVISION
from photo_search.persons.albums import _NO_MEMBER, _NOT_HELD, _VISIBILITY_PARAMETERS, _VISIBLE
from photo_search.persons.members import MEMBER_PREDICATE
from photo_search.persons.quality_policy import TIERS, qualifies


def _percentiles(values: list[float]) -> dict[str, float | None]:
    values.sort()
    if not values:
        return {"p10": None, "p25": None, "p50": None, "p75": None}

    def at(fraction: float) -> float:
        index = (len(values) - 1) * fraction
        lower = int(index)
        upper = min(lower + 1, len(values) - 1)
        weight = index - lower
        return round(values[lower] * (1 - weight) + values[upper] * weight, 4)

    return {"p10": at(.10), "p25": at(.25), "p50": at(.50), "p75": at(.75)}


def collect_quality_report(connection: sqlite3.Connection) -> dict[str, Any]:
    """Summarize current SQL-visible faces and unnamed effective member groups."""
    connection.row_factory = sqlite3.Row
    faces = connection.execute(
        "SELECT f.face_id, f.asset_id, f.spec_revision, f.quality_state, f.quality_reason, "
        "f.det_score, f.face_pixels, f.blur_variance, c.mode, "
        "(c.mode IN ('MANUAL_ASSIGNED','MANUAL_UNASSIGNED') OR EXISTS "
        "(SELECT 1 FROM face_exclusions AS e WHERE e.face_id=f.face_id AND e.active=1)) AS manual_protected "
        "FROM faces AS f LEFT JOIN face_assignment_controls AS c ON c.face_id=f.face_id "
        "JOIN assets AS a ON a.asset_id=f.asset_id WHERE a.state='ACTIVE' AND "
        + _VISIBLE + " AND " + _NOT_HELD + " AND ("
        "EXISTS (SELECT 1 FROM face_person_links AS l JOIN persons AS p ON p.person_id=l.person_id "
        "WHERE l.face_id=f.face_id AND p.merged_into IS NULL AND NOT EXISTS "
        "(SELECT 1 FROM person_hidden AS ph WHERE ph.person_id=p.person_id) AND "
        + MEMBER_PREDICATE + ") OR (" + _NO_MEMBER
        + " AND NOT EXISTS (SELECT 1 FROM face_inbox_hidden AS h WHERE h.face_id=f.face_id)))",
        _VISIBILITY_PARAMETERS,
    ).fetchall()
    reason_counts = Counter(str(row["quality_reason"] or "NONE") for row in faces)
    state_counts = Counter(str(row["quality_state"] or "MISSING") for row in faces)
    incomplete_by_spec = Counter(
        str(row["spec_revision"])
        for row in faces
        if row["quality_state"] is None or any(
            row[field] is None for field in ("det_score", "face_pixels", "blur_variance")
        )
    )
    metrics: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in faces:
        key = str(row["spec_revision"])
        for field in ("det_score", "face_pixels", "blur_variance"):
            value = row[field]
            if value is not None:
                metrics[key][field].append(float(value))
    face_counts: dict[str, int] = {}
    overlaps: dict[str, dict[str, int]] = {}
    for tier in TIERS:
        hidden = 0
        combinations = Counter()
        for row in faces:
            state, det, pixels, blur = (
                row["quality_state"], row["det_score"], row["face_pixels"], row["blur_variance"]
            )
            if state is None or det is None or pixels is None or blur is None:
                continue  # Incomplete evidence stays visible.
            reasons = []
            if state != "OK":
                reasons.append(str(row["quality_reason"] or state))
            if float(det) < tier.det_score:
                reasons.append("LOW_DETECTION_SCORE")
            if float(pixels) < tier.face_pixels:
                reasons.append("SMALL")
            if float(blur) < tier.blur_variance:
                reasons.append("BLURRY")
            if reasons:
                if not bool(row["manual_protected"]):
                    hidden += 1
                combinations["+".join(sorted(set(reasons)))] += 1
        face_counts[tier.name] = hidden
        overlaps[tier.name] = dict(combinations)

    groups = connection.execute(
        "SELECT p.person_id, f.face_id, f.spec_revision, f.quality_state, f.det_score, "
        "f.face_pixels, f.blur_variance, c.mode "
        "FROM persons AS p JOIN face_person_links AS l ON l.person_id=p.person_id "
        "JOIN faces AS f ON f.face_id=l.face_id JOIN assets AS a ON a.asset_id=f.asset_id "
        "LEFT JOIN face_assignment_controls AS c ON c.face_id=f.face_id "
        "WHERE p.name IS NULL AND p.merged_into IS NULL AND a.state='ACTIVE' AND "
        + MEMBER_PREDICATE + " AND NOT EXISTS (SELECT 1 FROM person_hidden AS ph WHERE ph.person_id=p.person_id) AND "
        + _VISIBLE + " AND " + _NOT_HELD
        + " ORDER BY p.person_id, f.face_id",
        _VISIBILITY_PARAMETERS,
    ).fetchall()
    grouped: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in groups:
        grouped[int(row["person_id"])].append(row)
    group_counts: dict[str, dict[str, int]] = {}
    for tier in TIERS:
        complete_groups = 0
        clear_groups = 0
        protected_groups = 0
        for members in grouped.values():
            protected = any(row["mode"] == "MANUAL_ASSIGNED" for row in members)
            has_clear = any(qualifies(
                quality_state=row["quality_state"], det_score=row["det_score"],
                face_pixels=row["face_pixels"], blur_variance=row["blur_variance"], tier=tier,
            ) for row in members)
            if has_clear:
                clear_groups += 1
            elif not protected:
                complete_groups += 1
            if protected:
                protected_groups += 1
        group_counts[tier.name] = {
            "fully_suppressed_unnamed_groups": complete_groups,
            "groups_retaining_at_least_one_clear_face": clear_groups,
            "groups_protected_by_manual_assignment": protected_groups,
        }

    percentiles = {}
    for revision, fields in sorted(metrics.items()):
        processing = (
            "source_detail" if revision == FACE_DETAIL_SPEC_REVISION
            else "ordinary_current" if revision == FACE_SPEC_REVISION
            else "historical_or_unknown"
        )
        percentiles[revision] = {
            "processing_kind": processing,
            **{field: _percentiles(values) for field, values in sorted(fields.items())},
        }
    return {
        "visible_face_count": len(faces),
        "quality_state_counts": dict(state_counts),
        "quality_reason_counts": dict(reason_counts),
        "incomplete_metric_face_counts_by_spec_revision": dict(incomplete_by_spec),
        "tier_suppressed_face_counts": face_counts,
        "overlapping_failure_reasons": overlaps,
        "unnamed_group_impact": group_counts,
        "manual_protected_face_count": sum(bool(row["manual_protected"]) for row in faces),
        "metric_percentiles_by_spec_revision": percentiles,
        "source_detail_spec_revision": FACE_DETAIL_SPEC_REVISION,
        "read_only": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path, help="catalog SQLite path; opened read-only")
    args = parser.parse_args()
    uri = args.catalog.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        print(json.dumps(collect_quality_report(connection), ensure_ascii=False, indent=2))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
