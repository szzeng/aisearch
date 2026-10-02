"""Small resumable GPS and place-resolution catch-up using the existing processors."""

from __future__ import annotations

from typing import Any

from photo_search.catalog.errors import CatalogError
from photo_search.geo import resolver
from photo_search.refresh import pipeline

BATCH_SIZE = 100


def _gps_targets(catalog: Any, limit: int) -> list[int]:
    return [
        int(row["asset_id"])
        for row in catalog.read_all(
            """SELECT a.asset_id FROM assets a
                WHERE EXISTS (SELECT 1 FROM file_locations l WHERE l.asset_id = a.asset_id
                               AND l.availability = 'PRESENT')
                  AND NOT EXISTS (SELECT 1 FROM asset_geo_facts gf
                                   WHERE gf.asset_id = a.asset_id
                                     AND gf.input_revision = a.sha256)
                ORDER BY a.asset_id LIMIT ?""",
            (int(limit),),
        )
    ]


def _resolution_targets(catalog: Any, limit: int) -> list[int]:
    return [
        int(row["asset_id"])
        for row in catalog.read_all(
            """SELECT a.asset_id FROM assets a
                JOIN asset_geo_facts gf ON gf.asset_id = a.asset_id
                 AND gf.input_revision = a.sha256 AND gf.status = 'VALID'
                 AND gf.datum_status IN ('WGS84', 'ASSUMED_WGS84')
                JOIN active_geo_dataset active ON active.singleton = 1 AND active.dataset_id IS NOT NULL
                WHERE EXISTS (SELECT 1 FROM file_locations l WHERE l.asset_id = a.asset_id
                               AND l.availability = 'PRESENT')
                  AND NOT EXISTS (SELECT 1 FROM place_resolutions pr
                                   WHERE pr.asset_id = a.asset_id AND pr.geo_fact_id = gf.geo_fact_id
                                     AND pr.input_revision = a.sha256 AND pr.dataset_id = active.dataset_id
                                     AND pr.processor_id = 'geo-nearby-city@1')
                ORDER BY a.asset_id LIMIT ?""",
            (int(limit),),
        )
    ]


def _remaining(catalog: Any) -> tuple[int, int]:
    gps = catalog.read_one(
        """SELECT COUNT(*) AS value FROM assets a
            WHERE EXISTS (SELECT 1 FROM file_locations l WHERE l.asset_id = a.asset_id AND l.availability = 'PRESENT')
              AND NOT EXISTS (SELECT 1 FROM asset_geo_facts gf WHERE gf.asset_id = a.asset_id AND gf.input_revision = a.sha256)"""
    )
    resolutions = catalog.read_one(
        """SELECT COUNT(*) AS value FROM assets a JOIN asset_geo_facts gf ON gf.asset_id = a.asset_id
             AND gf.input_revision = a.sha256 AND gf.status = 'VALID' AND gf.datum_status IN ('WGS84', 'ASSUMED_WGS84')
             JOIN active_geo_dataset active ON active.singleton = 1 AND active.dataset_id IS NOT NULL
            WHERE EXISTS (SELECT 1 FROM file_locations l WHERE l.asset_id = a.asset_id AND l.availability = 'PRESENT')
              AND NOT EXISTS (SELECT 1 FROM place_resolutions pr WHERE pr.asset_id = a.asset_id
                               AND pr.geo_fact_id = gf.geo_fact_id AND pr.input_revision = a.sha256
                               AND pr.dataset_id = active.dataset_id AND pr.processor_id = 'geo-nearby-city@1')"""
    )
    return int(gps["value"]), int(resolutions["value"])


def run(catalog: Any, data_root: Any, *, batch_size: int = BATCH_SIZE) -> dict[str, Any]:
    """Process one bounded batch; durable processor outputs make repeated calls resumable."""
    limit = max(1, min(int(batch_size), BATCH_SIZE))
    gps_ids = _gps_targets(catalog, limit)
    gps_result = pipeline._gps_stage(catalog, set(gps_ids)) if gps_ids else {
        "state": "SKIPPED_NO_TARGETS", "targets": 0, "steps": 0
    }
    try:
        resolver.load_active_content(catalog.connection, data_root=data_root)
        resolver_available, resolver_reason = True, None
    except CatalogError as error:
        resolver_available, resolver_reason = False, str(error.code)
    resolver_ids = _resolution_targets(catalog, limit) if resolver_available else []
    if resolver_ids:
        resolution_result = pipeline._resolver_stage(catalog, data_root, set(resolver_ids))
    else:
        resolution_result = {"state": "SKIPPED_NO_TARGETS", "targets": 0, "steps": 0}
    remaining_gps, remaining_resolutions = _remaining(catalog)
    return {
        "batch_limit": limit,
        "gps": gps_result,
        "place_resolution": resolution_result,
        "remaining_gps": remaining_gps,
        "remaining_place_resolution": remaining_resolutions,
        "resolver_available": resolver_available,
        "resolver_reason": resolver_reason,
        "complete": remaining_gps == 0 and remaining_resolutions == 0 and resolver_available,
    }
