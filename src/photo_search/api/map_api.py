"""Bounded, read-only map membership and point queries (P4-PLACE-MAP-A)."""

from __future__ import annotations

import base64
import json
import math
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from photo_search.api.routes import service_of
from photo_search.taken_time import read as time_read

router = APIRouter(prefix="/api/v1/map", tags=["map"])
MAX_PHOTO_POINTS = 5000
MAX_GRID_CELLS = 1000


def _revision(db: Any) -> int:
    row = db.execute("SELECT revision FROM map_member_revision WHERE singleton=1").fetchone()
    return int(row[0]) if row else 1


def _valid_bbox(
    west: float | None, south: float | None, east: float | None, north: float | None
) -> tuple[float, float, float, float] | None:
    if all(value is None for value in (west, south, east, north)):
        return None
    values = (west, south, east, north)
    if any(value is None or not math.isfinite(float(value)) for value in values):
        raise HTTPException(status_code=422, detail="bbox requires four finite coordinates")
    w, s, e, n = (float(value) for value in values)
    if not (-180 <= w <= 180 and -180 <= e <= 180 and -90 <= s <= 90 and -90 <= n <= 90 and s <= n):
        raise HTTPException(status_code=422, detail="bbox coordinates are out of range")
    if (w, s, e, n) == (-180.0, -90.0, 180.0, 90.0):
        return None
    return w, s, e, n


def _where(bbox: tuple[float, float, float, float] | None) -> tuple[str, list[Any]]:
    parts = [
        "g.status='VALID'",
        "g.datum_status IN ('WGS84','ASSUMED_WGS84')",
        "g.latitude IS NOT NULL",
        "g.longitude IS NOT NULL",
        "g.input_revision=a.sha256",
        "a.state='ACTIVE'",
        "EXISTS (SELECT 1 FROM file_locations l WHERE l.asset_id=a.asset_id AND l.availability='PRESENT')",
    ]
    args: list[Any] = []
    if bbox:
        w, s, e, n = bbox
        parts.extend(["g.latitude>=?", "g.latitude<=?"])
        args.extend([s, n])
        if w <= e:
            parts.extend(["g.longitude>=?", "g.longitude<=?"])
            args.extend([w, e])
        else:
            parts.append("(g.longitude>=? OR g.longitude<=?)")
            args.extend([w, e])
    return " AND ".join(parts), args


def _grid_expression(
    bbox: tuple[float, float, float, float] | None, level: int
) -> tuple[str, list[Any], str, list[Any]]:
    west, south, east, north = bbox or (-180.0, -90.0, 180.0, 90.0)
    width = (east + 360.0 - west) if west > east else east - west
    height = north - south
    scale = 2 ** int(level)
    xexpr = (
        "CASE WHEN ?=0 THEN 0 ELSE MIN(?,CAST(((CASE WHEN ? > ? AND g.longitude < ? "
        "THEN g.longitude+360.0 ELSE g.longitude END - ?)/?)*? AS INTEGER)) END"
    )
    xargs = [width, scale - 1, west, east, west, west, width or 1.0, scale]
    yexpr = "CASE WHEN ?=0 THEN 0 ELSE MIN(?,CAST(((g.latitude-?)/?)*? AS INTEGER)) END"
    yargs = [height, scale - 1, south, height or 1.0, scale]
    return xexpr, xargs, yexpr, yargs


def _read(request: Request):
    return service_of(request).catalog


def _from_sql(bbox: tuple[float, float, float, float] | None) -> str:
    if bbox is None:
        return "assets a JOIN asset_geo_facts AS g INDEXED BY idx_asset_geo_facts_status USING(asset_id)"
    return "assets a JOIN asset_geo_facts AS g USING(asset_id)"


@router.get("/summary")
async def map_summary(request: Request) -> dict[str, Any]:
    db = _read(request)
    with db.read_snapshot() as conn:
        where, args = _where(None)
        source = _from_sql(None)
        row = conn.execute(
            f"SELECT COUNT(*) n, MIN(g.longitude) west, MAX(g.longitude) east, MIN(g.latitude) south, MAX(g.latitude) north FROM {source} WHERE {where}",
            args,
        ).fetchone()
        longitude_bounds = None
        if row[0]:
            longitude_bounds = conn.execute(
                f"""WITH p AS (
                    SELECT DISTINCT CASE WHEN g.longitude=180.0 THEN -180.0 ELSE g.longitude END AS x
                    FROM {source} WHERE {where}
                ), gaps AS (
                    SELECT x, LEAD(x,1,(SELECT MIN(x) FROM p)+360.0) OVER (ORDER BY x) AS nx FROM p
                ) SELECT nx,x FROM gaps ORDER BY nx-x DESC LIMIT 1""",
                args,
            ).fetchone()
        return {
            "total": int(row[0]),
            "bounds": None
            if not row[0]
            else {
                "west": float(longitude_bounds[0]),
                "south": float(row[3]),
                "east": float(longitude_bounds[1]),
                "north": float(row[4]),
            },
            "member_version": _revision(conn),
        }


@router.get("/assets")
async def map_assets(
    request: Request,
    west: float | None = None,
    south: float | None = None,
    east: float | None = None,
    north: float | None = None,
    limit: int = 48,
    cursor: str | None = None,
    asset_id: int | None = None,
    cell_id: str | None = None,
    grid_zoom: int | None = None,
) -> dict[str, Any]:
    bbox = _valid_bbox(west, south, east, north)
    size = max(1, min(100, int(limit)))
    db = _read(request)
    with db.read_snapshot() as conn:
        where, args = _where(bbox)
        source = _from_sql(bbox)
        if asset_id is not None:
            where += " AND a.asset_id=?"
            args.append(int(asset_id))
        if cell_id is not None:
            cell_parts = cell_id.split(":")
            if (
                bbox is None
                or grid_zoom is None
                or not 0 <= int(grid_zoom) <= 20
                or len(cell_parts) != 2
                or not all(part.isdigit() for part in cell_parts)
            ):
                raise HTTPException(status_code=422, detail="cell_id requires valid grid_zoom")
            gx, gy = map(int, cell_id.split(":"))
            xexpr, xargs, yexpr, yargs = _grid_expression(bbox, int(grid_zoom))
            where += f" AND ({xexpr})=? AND ({yexpr})=?"
            args.extend([*xargs, gx, *yargs, gy])
        version = _revision(conn)
        fingerprint = json.dumps(
            [bbox, asset_id, cell_id, grid_zoom, size, version], separators=(",", ":")
        )
        after = 0
        if cursor:
            try:
                token = json.loads(base64.urlsafe_b64decode(cursor + "=="))
                if token["q"] != fingerprint or int(token["v"]) != version:
                    raise ValueError("stale cursor")
                after = int(token["a"])
            except Exception as exc:
                raise HTTPException(
                    status_code=422, detail="invalid or expired map cursor"
                ) from exc
        total = conn.execute(
            f"SELECT COUNT(*) FROM {source} WHERE {where}",
            args,
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT a.asset_id,a.created_at,g.latitude,g.longitude FROM {source} WHERE {where} AND a.asset_id<? ORDER BY a.asset_id DESC LIMIT ?",
            [*args, after or 9223372036854775807, size + 1],
        ).fetchall()
        has_more = len(rows) > size
        rows = rows[:size]
        assets = [
            {
                "asset_id": int(r[0]),
                "capture_time": time_read.asset_view(conn, int(r[0])),
                "thumbnail": f"/api/v1/assets/{int(r[0])}/thumbnail",
                "detail": f"/#/asset/{int(r[0])}",
                "latitude": float(r[2]),
                "longitude": float(r[3]),
            }
            for r in rows
        ]
        next_cursor = None
        if has_more and rows:
            next_cursor = (
                base64.urlsafe_b64encode(
                    json.dumps(
                        {"q": fingerprint, "v": version, "a": int(rows[-1][0])},
                        separators=(",", ":"),
                    ).encode()
                )
                .decode()
                .rstrip("=")
            )
        return {
            "assets": assets,
            "total": int(total),
            "next_cursor": next_cursor,
            "member_version": version,
        }


@router.get("/points")
async def map_points(
    request: Request,
    west: float | None = None,
    south: float | None = None,
    east: float | None = None,
    north: float | None = None,
    zoom: int = 3,
) -> dict[str, Any]:
    bbox = _valid_bbox(west, south, east, north)
    if not 0 <= int(zoom) <= 22:
        raise HTTPException(status_code=422, detail="zoom must be between 0 and 22")
    db = _read(request)
    with db.read_snapshot() as conn:
        where, args = _where(bbox)
        source = _from_sql(bbox)
        total = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {source} WHERE {where}",
                args,
            ).fetchone()[0]
        )
        version = _revision(conn)
        if total <= MAX_PHOTO_POINTS:
            rows = conn.execute(
                f"SELECT a.asset_id,g.longitude,g.latitude FROM {source} WHERE {where} ORDER BY a.asset_id",
                args,
            ).fetchall()
            features = [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [float(r[1]), float(r[2])]},
                    "properties": {"asset_id": int(r[0])},
                }
                for r in rows
            ]
            return {
                "type": "FeatureCollection",
                "features": features,
                "mode": "photos",
                "total": total,
                "member_version": version,
            }
        # Cells divide the requested viewport so city and small-area grids remain useful.
        level = max(0, min(20, int(zoom) + 2))
        xexpr, xargs, yexpr, yargs = _grid_expression(bbox, level)
        while True:
            rows = conn.execute(
                f"SELECT ({xexpr}) gx, ({yexpr}) gy, COUNT(*) n, MIN(g.longitude),MAX(g.longitude),MIN(g.latitude),MAX(g.latitude) FROM {source} WHERE {where} GROUP BY gx,gy ORDER BY gx,gy",
                [*xargs, *yargs, *args],
            ).fetchall()
            if len(rows) <= MAX_GRID_CELLS or level == 0:
                break
            level -= 1
            xexpr, xargs, yexpr, yargs = _grid_expression(bbox, level)
        features = [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [
                        (float(r[3]) + float(r[4])) / 2,
                        (float(r[5]) + float(r[6])) / 2,
                    ],
                },
                "properties": {
                    "cell_id": f"{int(r[0])}:{int(r[1])}",
                    "asset_count": int(r[2]),
                    "cell_bounds": {
                        "west": float(r[3]),
                        "east": float(r[4]),
                        "south": float(r[5]),
                        "north": float(r[6]),
                    },
                    "grid_zoom": level,
                },
            }
            for r in rows
        ]
        return {
            "type": "FeatureCollection",
            "features": features,
            "mode": "grid",
            "total": total,
            "grid_zoom": level,
            "member_version": version,
        }
