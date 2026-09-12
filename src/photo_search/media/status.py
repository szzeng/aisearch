"""Read-only derived-media status: what is ready, what is missing, what failed and why.

This is the honest observability entry the later API/UI tasks need to answer "is this
asset browsable", "does the cache need rebuilding" and "why does this file have no
thumbnail" without re-decoding anything. It never writes to the catalog and never creates
an artifact.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import FORMAT_UNSUPPORTED
from photo_search.media.cache import MediaCache
from photo_search.media.contract import KINDS, MediaSpec, spec_for
from photo_search.media.pipeline import STATE_UNSUPPORTED

_ASSET_SQL = """
SELECT a.asset_id, a.sha256
  FROM assets AS a
 WHERE EXISTS (
       SELECT 1 FROM file_locations AS l
        WHERE l.asset_id = a.asset_id{source_filter})
 ORDER BY a.asset_id
"""

_LOCATION_SQL = """
SELECT l.relative_path, l.availability, s.root_path
  FROM file_locations AS l
  JOIN source_roots AS s ON s.source_id = l.source_id
 WHERE l.asset_id = ?
 ORDER BY l.location_id
"""

#: The five honest answers one asset can give for one artifact kind (architecture §5.4).
MEDIA_STATE_READY = "ready"
MEDIA_STATE_PENDING = "pending"
MEDIA_STATE_SOURCE_MISSING = "source_missing"
MEDIA_STATE_FAILED = "failed"
MEDIA_STATE_UNSUPPORTED = "unsupported"


def asset_media_states(
    catalog: Any,
    cache: MediaCache,
    *,
    asset_id: int,
    sha256: str,
    kinds: tuple[str, ...] = KINDS,
) -> dict[str, dict[str, Any]]:
    """Per-kind artifact state of one asset, using the same rule as ``media_status``.

    This is the single decision used by the CLI status report, ``GET /api/v1/stats`` and
    the asset detail view, so a caller can never see "ready" where the report counts
    "failed" (or the other way round).
    """
    states: dict[str, dict[str, Any]] = {}
    reachable: bool | None = None
    for kind in kinds:
        spec = spec_for(kind)
        state: dict[str, Any] = {
            "kind": spec.kind,
            "processor_id": spec.processor_id,
            "long_edge": spec.long_edge,
            "state": MEDIA_STATE_PENDING,
            "code": None,
            "record": None,
        }
        artifact = cache.artifact_path(spec=spec, sha256=sha256)
        if cache.is_published(artifact):
            state["state"] = MEDIA_STATE_READY
            states[spec.kind] = state
            continue
        record = cache.read_failure(spec=spec, sha256=sha256)
        if record is not None:
            code = str(record.get("code"))
            state["code"] = code
            state["record"] = record
            state["state"] = (
                MEDIA_STATE_UNSUPPORTED
                if code == FORMAT_UNSUPPORTED or str(record.get("state")) == STATE_UNSUPPORTED
                else MEDIA_STATE_FAILED
            )
            states[spec.kind] = state
            continue
        if reachable is None:
            reachable = _any_location_reachable(catalog, int(asset_id))
        state["state"] = MEDIA_STATE_PENDING if reachable else MEDIA_STATE_SOURCE_MISSING
        states[spec.kind] = state
    return states


def media_status(
    catalog: Any,
    cache: MediaCache,
    *,
    kinds: tuple[str, ...] = KINDS,
    source_id: int | None = None,
    asset_limit: int | None = None,
    include_failures: bool = False,
    failure_limit: int = 20,
    prune_temp: bool = False,
    min_temp_age_ms: int = 600_000,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Count published/pending/failed artifacts per kind and enumerate failure reasons."""
    now = int(time.time_ns() // 1_000_000) if now_ms is None else int(now_ms)
    specs: list[MediaSpec] = [spec_for(kind) for kind in kinds]
    source_filter = "" if source_id is None else " AND l.source_id = ?"
    parameters: tuple[Any, ...] = () if source_id is None else (int(source_id),)
    assets = catalog.read_all(_ASSET_SQL.format(source_filter=source_filter), parameters)
    examined = assets if asset_limit is None else assets[: max(int(asset_limit), 0)]

    counts: dict[str, dict[str, Any]] = {
        spec.kind: {
            "processor_id": spec.processor_id,
            "long_edge": spec.long_edge,
            "artifact_root": str(cache.artifact_root(spec)),
            "published": 0,
            "pending": 0,
            "source_missing": 0,
            "failed": 0,
            "unsupported": 0,
            "failure_codes": {},
            "published_files": 0,
            "published_bytes": 0,
            "temp_files": 0,
            "stale_versions": [
                version
                for version in cache.version_dirs(kind=spec.kind)
                if version != spec.processor_id
            ],
        }
        for spec in specs
    }
    failures: list[dict[str, Any]] = []
    pruned: list[str] = []

    for row in examined:
        asset_id = int(row["asset_id"])
        sha256 = str(row["sha256"])
        # The per-kind state is decided once, by the same helper the API uses. Reachability
        # is only probed when an asset has something left to generate, so an offline source
        # is reported as ``source_missing`` instead of being re-listed as pending.
        states = asset_media_states(
            catalog, cache, asset_id=asset_id, sha256=sha256, kinds=tuple(spec.kind for spec in specs)
        )
        for spec in specs:
            bucket = counts[spec.kind]
            state = states[spec.kind]
            if state["state"] == MEDIA_STATE_READY:
                bucket["published"] += 1
                continue
            if state["state"] in (MEDIA_STATE_FAILED, MEDIA_STATE_UNSUPPORTED):
                code = str(state["code"])
                record = state["record"] or {}
                bucket["failure_codes"][code] = int(bucket["failure_codes"].get(code, 0)) + 1
                bucket[str(state["state"])] += 1
                if include_failures and len(failures) < max(int(failure_limit), 0):
                    failures.append(
                        {
                            "asset_id": asset_id,
                            "sha256": sha256,
                            "kind": spec.kind,
                            "code": code,
                            "state": record.get("state"),
                            "message": record.get("message"),
                            "source_format": record.get("format"),
                            "created_at": record.get("created_at"),
                        }
                    )
                continue
            bucket[str(state["state"])] += 1

    for spec in specs:
        bucket = counts[spec.kind]
        inventory = cache.cache_inventory(spec=spec)
        bucket.update(inventory)
        if prune_temp:
            removed = cache.prune_temp_files(
                spec=spec, min_age_ms=int(min_temp_age_ms), now_ms=now
            )
            bucket["temp_files"] = max(int(bucket["temp_files"]) - len(removed), 0)
            bucket["temp_files_pruned"] = len(removed)
            pruned.extend(str(path) for path in removed)

    return {
        "status": "ok",
        "kinds": counts,
        "assets_examined": len(examined),
        "assets_total": len(assets),
        "partial": len(examined) != len(assets),
        "source_id": None if source_id is None else int(source_id),
        "failures": failures,
        "failure_limit": int(failure_limit),
        "pruned_temp_files": pruned,
    }


def _any_location_reachable(catalog: Any, asset_id: int) -> bool:
    """True when some ``PRESENT`` position of the asset is readable right now."""
    for row in catalog.read_all(_LOCATION_SQL, (asset_id,)):
        if str(row["availability"]) != "PRESENT":
            continue
        try:
            if (Path(str(row["root_path"])) / str(row["relative_path"])).is_file():
                return True
        except OSError:  # pragma: no cover - defensive: an unreadable root is not reachable
            continue
    return False
