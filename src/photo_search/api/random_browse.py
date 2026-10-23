"""Bounded read-only random photo sampling with short recent-item exclusion."""
from __future__ import annotations

import random
from typing import Any, Iterable

from photo_search.api.errors import ApiError
from photo_search.api.queries import eligible_random_ids

RANDOM_SAMPLE_INVALID = "RANDOM_SAMPLE_INVALID"
MAX_BATCH_SIZE = 1000
MAX_RECENT_IDS = 1200
WINDOW_IDS = 1000
WINDOW_COUNT = 12
MAX_CANDIDATE_IDS = WINDOW_IDS * WINDOW_COUNT


def validate_ids(values: Iterable[Any], *, limit: int) -> list[int]:
    if not isinstance(values, (list, tuple)):
        raise ApiError(RANDOM_SAMPLE_INVALID, "照片列表参数无效", status=422)
    ids: list[int] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ApiError(RANDOM_SAMPLE_INVALID, "照片列表参数无效", status=422)
        asset_id = int(value)
        if asset_id < 1:
            raise ApiError(RANDOM_SAMPLE_INVALID, "照片列表参数无效", status=422)
        ids.append(asset_id)
        if len(ids) > limit:
            raise ApiError(RANDOM_SAMPLE_INVALID, "照片列表超出数量上限", status=422)
    return ids


def _circular_ranges(start: int, width: int, low: int, high: int) -> tuple[tuple[int, int], ...]:
    end = start + width
    ranges = ((start, min(end, high + 1)),)
    if end > high + 1:
        ranges += ((low, low + end - (high + 1)),)
    return ranges


def _random_global_windows(catalog: Any, rng: Any) -> list[int]:
    first = catalog.read_one("SELECT asset_id FROM assets ORDER BY asset_id LIMIT 1")
    last = catalog.read_one("SELECT asset_id FROM assets ORDER BY asset_id DESC LIMIT 1")
    if first is None or last is None:
        return []
    low, high = int(first["asset_id"]), int(last["asset_id"])
    span = high - low + 1
    # Sample circular ID windows so endpoint assets in contiguous ranges have the same
    # inclusion probability as interior assets. Work stays bounded by the window count.
    if span <= WINDOW_IDS:
        starts = [low]
        width = span
    else:
        width = WINDOW_IDS
        starts = [rng.randint(low, high) for _ in range(WINDOW_COUNT)]
    ids: list[int] = []
    for start in starts:
        for range_start, range_end in _circular_ranges(start, width, low, high):
            ids.extend(int(row["asset_id"]) for row in catalog.read_all(
                "SELECT asset_id FROM assets WHERE asset_id>=? AND asset_id<? "
                "ORDER BY asset_id LIMIT ?",
                (range_start, range_end, width),
            ))
    return ids[:MAX_CANDIDATE_IDS]


def _random_source_windows(catalog: Any, source_id: int, rng: Any) -> list[int]:
    low_row = catalog.read_one(
        "SELECT asset_id FROM file_locations INDEXED BY idx_file_locations_present_source_asset "
        "WHERE source_id=? AND availability='PRESENT' AND asset_id IS NOT NULL "
        "ORDER BY asset_id LIMIT 1", (int(source_id),),
    )
    high_row = catalog.read_one(
        "SELECT asset_id FROM file_locations INDEXED BY idx_file_locations_present_source_asset "
        "WHERE source_id=? AND availability='PRESENT' AND asset_id IS NOT NULL "
        "ORDER BY asset_id DESC LIMIT 1", (int(source_id),),
    )
    if low_row is None or high_row is None:
        return []
    low, high = int(low_row["asset_id"]), int(high_row["asset_id"])
    span = high - low + 1
    # This path starts from the source's PRESENT index. Circular fixed-width ranges avoid
    # endpoint bias and keep duplicate paths from consuming the candidate window.
    width = min(span, WINDOW_IDS)
    starts = [low] if span <= WINDOW_IDS else [rng.randint(low, high) for _ in range(WINDOW_COUNT)]
    ids: list[int] = []
    for start in starts:
        for range_start, range_end in _circular_ranges(start, width, low, high):
            rows = catalog.read_all(
                "SELECT asset_id FROM file_locations INDEXED BY idx_file_locations_present_source_asset "
                "WHERE source_id=? AND availability='PRESENT' AND asset_id IS NOT NULL "
                "AND asset_id>=? AND asset_id<? GROUP BY asset_id ORDER BY asset_id LIMIT ?",
                (int(source_id), range_start, range_end, width),
            )
            ids.extend(int(row["asset_id"]) for row in rows)
    # Locations sharing an asset cannot duplicate it in the candidate output.
    unique: list[int] = []
    seen: set[int] = set()
    for asset_id in ids:
        if asset_id not in seen:
            seen.add(asset_id)
            unique.append(asset_id)
    return unique[:MAX_CANDIDATE_IDS]


def sample_asset_ids(
    catalog: Any, *, source_id: int | None, count: int, recent_ids: Iterable[Any] = (),
    rng: Any | None = None,
) -> dict[str, Any]:
    """Read a bounded candidate pool, avoid recent IDs when possible, and shuffle once."""
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_BATCH_SIZE:
        raise ApiError(RANDOM_SAMPLE_INVALID, "随机数量必须在 1 到 1000 之间", status=422)
    if source_id is not None and (isinstance(source_id, bool) or int(source_id) < 1):
        raise ApiError(RANDOM_SAMPLE_INVALID, "图库范围参数无效", status=422)
    recent = validate_ids(recent_ids, limit=MAX_RECENT_IDS)
    rng = rng or random.SystemRandom()
    with catalog.read_snapshot():
        candidates = (_random_global_windows(catalog, rng) if source_id is None else
                      _random_source_windows(catalog, int(source_id), rng))
        # Recheck the shared photo/PRESENT predicate once per bounded ID chunk and deduplicate
        # the entire pool. Window overlap and multiple source paths cannot repeat an asset.
        unique: list[int] = []
        seen: set[int] = set()
        for asset_id in candidates:
            if asset_id not in seen:
                seen.add(asset_id)
                unique.append(asset_id)
        qualified: list[int] = []
        for offset in range(0, len(unique), MAX_BATCH_SIZE):
            qualified.extend(eligible_random_ids(
                catalog, unique[offset:offset + MAX_BATCH_SIZE], source_id=source_id,
            ))
        unique = qualified
        rng.shuffle(unique)
    recent = list(dict.fromkeys(recent))
    # Older recent IDs are progressively released only when the bounded candidate sample
    # cannot satisfy the request. The selected list is always unique.
    chosen: list[int] = []
    for excluded_count in (len(recent), len(recent) * 3 // 4, len(recent) // 2,
                           len(recent) // 4, 0):
        excluded = set(recent[:excluded_count]) if excluded_count else set()
        chosen = [asset_id for asset_id in unique if asset_id not in excluded][:int(count)]
        if len(chosen) >= int(count):
            break
    return {
        "source_id": source_id,
        "requested_count": int(count),
        "candidate_count": len(unique),
        "asset_ids": chosen,
        "underfilled": len(chosen) < int(count),
    }
