"""Bounded, read-only map membership and point queries (P4-PLACE-MAP-A)."""

from __future__ import annotations

import base64
import json
import math
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from photo_search.api.routes import service_of
from photo_search.taken_time import read as time_read

router = APIRouter(prefix="/api/v1/map", tags=["map"])
MAX_PHOTO_POINTS = 5000
MAX_GRID_CELLS = 1000


def _provider_config(request: Request):
    from photo_search.map_provider_config import MapProviderConfigError, read

    try:
        return read(request.app.state.api.settings.data_root), None
    except MapProviderConfigError:
        return None, "CONFIG_UNREADABLE"


def _private_response(body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(body, headers={"Cache-Control": "no-store, private"})


@router.get("/provider-settings")
async def provider_settings(request: Request) -> JSONResponse:
    config, error = _provider_config(request)
    if error:
        return _private_response({"configured": False, "provider": "tianditu", "default_layer": "vector", "status": error})
    return _private_response({"configured": bool(config["key"]), "provider": "tianditu",
                              "default_layer": config["default_layer"],
                              "status": "CONFIGURED" if config["key"] else "NOT_CONFIGURED"})


@router.get("/provider-runtime")
async def provider_runtime(request: Request) -> JSONResponse:
    config, error = _provider_config(request)
    if error:
        return _private_response({"status": error})
    if not config["key"]:
        return _private_response({"status": "NOT_CONFIGURED", "provider": "tianditu",
                                  "default_layer": config["default_layer"]})
    return _private_response({"status": "CONFIGURED", "provider": "tianditu", "key": config["key"],
                              "default_layer": config["default_layer"]})


@router.put("/provider-settings")
async def update_provider_settings(request: Request) -> JSONResponse:
    from photo_search.map_provider_config import MapProviderConfigError, write

    try:
        body = await request.json()
        if not isinstance(body, dict) or set(body) - {"key", "default_layer"} or not body:
            raise MapProviderConfigError("invalid settings fields")
        key = body.get("key")
        if "key" in body and (not isinstance(key, str) or not key):
            raise MapProviderConfigError("empty Key cannot replace a saved Key")
        layer = body.get("default_layer")
        config = write(request.app.state.api.settings.data_root, key=key,
                       default_layer=layer if layer is not None else None)
    except MapProviderConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        # Do not echo request values or filesystem details; the Key is sensitive.
        raise HTTPException(status_code=500, detail="map settings could not be saved") from exc
    return _private_response({"configured": bool(config["key"]), "provider": "tianditu",
                              "default_layer": config["default_layer"],
                              "status": "CONFIGURED" if config["key"] else "NOT_CONFIGURED"})


@router.delete("/provider-settings/key")
async def clear_provider_key(request: Request) -> JSONResponse:
    from photo_search.map_provider_config import MapProviderConfigError, write

    try:
        config = write(request.app.state.api.settings.data_root, clear_key=True)
    except MapProviderConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="map settings could not be saved") from exc
    return _private_response({"configured": False, "provider": "tianditu",
                              "default_layer": config["default_layer"], "status": "NOT_CONFIGURED"})


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
    order: str = "asset_id_desc",
) -> dict[str, Any]:
    if order not in {"asset_id_desc", "capture_time_desc"}:
        raise HTTPException(status_code=422, detail="unsupported map asset order")
    if order == "capture_time_desc" and (asset_id is not None or cell_id is not None):
        raise HTTPException(
            status_code=422,
            detail="capture-time browsing is available for the complete viewport only",
        )
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
        catalog_revision_row = conn.execute(
            "SELECT revision FROM catalog_filter_revision WHERE revision_id=1"
        ).fetchone()
        catalog_revision = int(catalog_revision_row[0]) if catalog_revision_row else 0
        time_rules_version = time_read.rules_version(conn)
        fingerprint = json.dumps(
            [bbox, asset_id, cell_id, grid_zoom, size, version]
            if order == "asset_id_desc"
            else [bbox, asset_id, cell_id, grid_zoom, size, version, order,
                  catalog_revision, time_rules_version],
            separators=(",", ":"),
        )
        after = 0
        after_bucket = 1
        after_time: int | None = None
        if cursor:
            try:
                token = json.loads(base64.urlsafe_b64decode(cursor + "=="))
                if token["q"] != fingerprint or int(token["v"]) != version:
                    raise ValueError("stale cursor")
                if order == "capture_time_desc":
                    if (int(token["cv"]) != catalog_revision
                            or token.get("tr") != time_rules_version):
                        raise ValueError("stale capture-time cursor")
                    after_bucket = int(token["b"])
                    after_time = None if token["t"] is None else int(token["t"])
                    after = int(token["a"])
                else:
                    after = int(token["a"])
            except Exception as exc:
                raise HTTPException(
                    status_code=422, detail="invalid or expired map cursor"
                ) from exc
        total = conn.execute(
            f"SELECT COUNT(*) FROM {source} WHERE {where}",
            args,
        ).fetchone()[0]
        if order == "capture_time_desc":
            trusted_time = time_read.rules_version_matches(conn)
            source_with_time = source + " LEFT JOIN asset_metadata AS m USING(asset_id)"
            bucket_sql = (
                "CASE WHEN m.time_source IN ('EXIF_ORIGINAL','EXIF_DIGITIZED') "
                "AND m.taken_at_sort IS NOT NULL THEN 0 ELSE 1 END"
                if trusted_time else "1"
            )
            sort_key_sql = "m.taken_at_sort" if trusted_time else "NULL"
            time_where = where
            time_args = list(args)
            if cursor:
                if after_bucket == 0 and after_time is not None:
                    time_where += (
                        f" AND ({bucket_sql}>0 OR ({bucket_sql}=0 AND "
                        f"({sort_key_sql}<? OR ({sort_key_sql}=? AND a.asset_id<?))))"
                    )
                    time_args.extend([after_time, after_time, after])
                else:
                    time_where += f" AND {bucket_sql}=1 AND a.asset_id<?"
                    time_args.append(after)
            rows = conn.execute(
                f"SELECT a.asset_id,a.created_at,g.latitude,g.longitude,{bucket_sql} AS time_bucket,{sort_key_sql} AS time_sort FROM {source_with_time} WHERE {time_where} ORDER BY time_bucket ASC,time_sort DESC,a.asset_id DESC LIMIT ?",
                [*time_args, size + 1],
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT a.asset_id,a.created_at,g.latitude,g.longitude FROM {source} WHERE {where} AND a.asset_id<? ORDER BY a.asset_id DESC LIMIT ?",
                [*args, after or 9223372036854775807, size + 1],
            ).fetchall()
        has_more = len(rows) > size
        rows = rows[:size]
        assets = [
            {
                "asset_id": int(r[0]),
                "thumbnail": f"/api/v1/assets/{int(r[0])}/thumbnail",
                "detail": f"/#/asset/{int(r[0])}",
                "latitude": float(r[2]),
                "longitude": float(r[3]),
                **(
                    {"time_order": "capture_time_desc",
                     "capture_time": time_read.asset_view(conn, int(r[0]))}
                    if order == "capture_time_desc" else {}
                ),
            }
            for r in rows
        ]
        next_cursor = None
        if has_more and rows:
            token_body = {"q": fingerprint, "v": version, "a": int(rows[-1][0])}
            if order == "capture_time_desc":
                token_body.update({
                    "cv": catalog_revision,
                    "tr": time_rules_version,
                    "b": int(rows[-1][4]),
                    "t": None if rows[-1][5] is None else int(rows[-1][5]),
                })
            next_cursor = base64.urlsafe_b64encode(
                json.dumps(token_body, separators=(",", ":")).encode()
            ).decode().rstrip("=")
        return {
            "assets": assets,
            "total": int(total),
            "next_cursor": next_cursor,
            "member_version": version,
            **(
                {"order": order, "catalog_filter_revision": catalog_revision,
                 "candidate_scope": "complete_current_viewport"}
                if order == "capture_time_desc" else {}
            ),
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
