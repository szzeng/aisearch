"""SQL selection shared by image enrichment jobs."""
from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from photo_search.catalog.db import Catalog


def available_assets(
    catalog: Catalog, *, asset_ids: Sequence[int] | None = None,
    source_id: int | None = None, limit: int | None = None,
) -> list[dict[str, Any]]:
    if limit is not None and limit <= 0:
        return []
    conditions = []
    parameters: list[Any] = []
    if asset_ids is not None:
        ids = sorted({int(value) for value in asset_ids})
        if not ids:
            return []
        conditions.append("a.asset_id IN (SELECT value FROM json_each(?))")
        parameters.append(json.dumps(ids))
    location = "f.asset_id = a.asset_id AND f.availability = 'PRESENT'"
    if source_id is not None:
        location += " AND f.source_id = ?"
        parameters.append(int(source_id))
    conditions.append(f"EXISTS (SELECT 1 FROM file_locations f WHERE {location})")
    sql = "SELECT a.asset_id, a.sha256 FROM assets a WHERE " + " AND ".join(conditions)
    sql += " ORDER BY a.asset_id"
    if limit is not None:
        sql += " LIMIT ?"
        parameters.append(int(limit))
    return [
        {"asset_id": int(row["asset_id"]), "input_revision": str(row["sha256"])}
        for row in catalog.read_all(sql, tuple(parameters))
    ]
