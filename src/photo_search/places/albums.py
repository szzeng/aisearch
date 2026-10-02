"""Read-only photo albums derived from the current place facts.

This view is intentionally separate from ADR-0005's manual assignment filters. A GPS
nearby-city row is a browsing hint and never becomes an assignment or FTS document.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from photo_search.api.errors import ApiError
from photo_search.catalog.errors import CATALOG_FILTER_REVISION_STALE, INVALID_CURSOR
from photo_search.geo import resolver as resolver_module
from photo_search.places import datasets as datasets_module
from photo_search.places import revision as revision_module
from photo_search.places.contracts import normalize_name

AUTO_MAX_DISTANCE_M = 50_000.0
PAGE_SIZE = 48


def _index(catalog: Any, data_root: Any) -> tuple[dict[str, Any], str | None, str | None, bool]:
    active = datasets_module.active_row(catalog.connection)
    if active is None:
        return {}, None, None, False
    try:
        content = resolver_module.load_active_content(
            catalog.connection, data_root=data_root
        )
    except Exception:
        return {}, f"{active['dataset_id']}:{active['content_sha256']}", str(active["provider"]), False
    return dict(content.province_index), f"{active['dataset_id']}:{active['content_sha256']}", str(active["provider"]), True


def _album_rows(catalog: Any, data_root: Any) -> tuple[list[dict[str, Any]], str | None]:
    province_index, package_revision, provider, package_available = _index(catalog, data_root)
    city_regions = province_index.get("city_to_admin1", {})
    rows = catalog.read_all(
        """SELECT a.asset_id, a.sha256, a.byte_size, a.created_at,
                  CASE WHEN ap.mode = 'MANUAL_ASSIGNED' THEN ap.place_id
                       ELSE pc.local_place_id END AS place_id,
                  CASE WHEN ap.mode = 'MANUAL_ASSIGNED' THEN p.kind
                       ELSE candidate.kind END AS kind,
                  CASE WHEN ap.mode = 'MANUAL_ASSIGNED' THEN p.canonical_name
                       ELSE candidate.canonical_name END AS name,
                  CASE WHEN ap.mode = 'MANUAL_ASSIGNED' THEN p.parent_place_id
                       ELSE candidate.parent_place_id END AS parent_place_id,
                  CASE WHEN ap.mode = 'MANUAL_ASSIGNED' THEN 'MANUAL'
                       ELSE 'GPS_NEARBY_CITY' END AS origin
             FROM assets a
             LEFT JOIN asset_place_assignments ap ON ap.asset_id = a.asset_id AND ap.record_state = 'ACTIVE'
             LEFT JOIN places p ON p.place_id = ap.place_id
             LEFT JOIN asset_geo_facts gf ON gf.asset_id = a.asset_id
             LEFT JOIN active_geo_dataset active ON active.singleton = 1
             LEFT JOIN place_resolutions pr ON pr.resolution_id = (
                 SELECT pr2.resolution_id FROM place_resolutions pr2
                  WHERE pr2.asset_id = a.asset_id AND pr2.dataset_id = active.dataset_id
                    AND pr2.geo_fact_id = gf.geo_fact_id AND pr2.input_revision = a.sha256
                  ORDER BY pr2.created_at DESC, pr2.resolution_id DESC LIMIT 1)
             LEFT JOIN place_resolution_candidates pc
               ON pc.resolution_id = pr.resolution_id AND pc.rank = 1
             LEFT JOIN places candidate ON candidate.place_id = pc.local_place_id
            WHERE EXISTS (SELECT 1 FROM file_locations l WHERE l.asset_id = a.asset_id
                           AND l.availability = 'PRESENT')
              AND (ap.mode = 'MANUAL_ASSIGNED' OR
                   (COALESCE(ap.mode, 'AUTO_ALLOWED') = 'AUTO_ALLOWED'
                    AND gf.status = 'VALID' AND gf.input_revision = a.sha256
                    AND gf.datum_status IN ('WGS84', 'ASSUMED_WGS84')
                    AND pr.status = 'CANDIDATES' AND pc.local_place_id IS NOT NULL
                    AND candidate.lifecycle = 'ACTIVE' AND pc.distance_m <= ?
                    AND ? = 1
                    AND NOT EXISTS (SELECT 1 FROM place_resolution_candidates pc2
                                     WHERE pc2.resolution_id = pr.resolution_id AND pc2.rank = 2
                                       AND pc2.distance_m = pc.distance_m)
                    AND NOT EXISTS (SELECT 1 FROM asset_place_exclusions x
                                     WHERE x.asset_id = a.asset_id
                                       AND x.place_id = pc.local_place_id AND x.active = 1)))
            ORDER BY a.asset_id""",
        (AUTO_MAX_DISTANCE_M, int(package_available)),
    )
    result: list[dict[str, Any]] = []
    external_ids: dict[int, str] = {}
    if provider:
        external_ids = {
            int(row["place_id"]): str(row["external_id"])
            for row in catalog.read_all(
                "SELECT place_id, external_id FROM place_external_refs WHERE provider = ?",
                (provider,),
            )
        }
    for row in rows:
        item = dict(row)
        if item["place_id"] is None:
            continue
        item["place_id"] = int(item["place_id"])
        item["asset_id"] = int(item["asset_id"])
        if item["kind"] == "city":
            external_id = external_ids.get(item["place_id"], "")
            region = city_regions.get(external_id)
            item["region"] = region if isinstance(region, dict) else None
        result.append(item)
    return result, package_revision


def _token(position: int, revisions: tuple[Any, ...]) -> str:
    raw = json.dumps([position, *revisions], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(token: str | None, revisions: tuple[Any, ...]) -> int:
    if not token:
        return 0
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        values = json.loads(raw)
        if values[1:] != list(revisions):
            raise ApiError(CATALOG_FILTER_REVISION_STALE, "the album changed; reload the page", status=409)
        if int(values[0]) < 0:
            raise ValueError
        return int(values[0])
    except ApiError:
        raise
    except (ValueError, TypeError, IndexError, json.JSONDecodeError) as error:
        raise ApiError(INVALID_CURSOR, "the album cursor is not valid") from error


def _key(kind: str, row: dict[str, Any]) -> tuple[str, str] | None:
    if kind == "city" and row["kind"] == "city":
        return ("city", str(row["place_id"]))
    if kind == "country":
        if row["kind"] == "country":
            return ("country", str(row["place_id"]))
        return ("country", str(row["parent_place_id"])) if row["kind"] == "city" and row["parent_place_id"] else None
    if kind == "province" and row.get("region"):
        region = row["region"]
        return ("province", str(region.get("id", ""))) if region.get("id") else None
    return None


def albums(catalog: Any, data_root: Any, *, kind: str = "city", name: str | None = None) -> dict[str, Any]:
    if kind not in ("city", "province", "country"):
        raise ValueError("kind must be city, province or country")
    before_filter = revision_module.read_filter_revision(catalog.connection)
    before_suggestion = revision_module.read_suggestion_revision(catalog.connection)
    rows, package_revision = _album_rows(catalog, data_root)
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = _key(kind, row)
        if key is None:
            continue
        label = row["name"]
        aliases: list[str] = []
        parent = ""
        if kind == "country":
            parent_row = catalog.read_one("SELECT canonical_name FROM places WHERE place_id = ?", (int(key[1]),))
            label = str(parent_row["canonical_name"]) if parent_row else ""
        elif kind == "province":
            region = row["region"]
            label = str(region.get("name", ""))
            aliases = [str(value) for value in region.get("aliases", [])]
        else:
            parent_row = catalog.read_one("SELECT canonical_name FROM places WHERE place_id = ?", (int(row["parent_place_id"]),)) if row["parent_place_id"] else None
            parent = str(parent_row["canonical_name"]) if parent_row else ""
            aliases = [str(alias["text"]) for alias in catalog.read_all("SELECT text FROM place_aliases WHERE place_id = ? AND lifecycle = 'ACTIVE' ORDER BY alias_id", (int(row["place_id"]),))]
        bucket = grouped.setdefault(key, {"kind": kind, "key": key[1], "name": label, "aliases": aliases, "parent": parent, "asset_ids": [], "cover_asset_id": row["asset_id"], "origins": set()})
        bucket["origins"].add(row["origin"])
        if row["asset_id"] not in bucket["asset_ids"]:
            bucket["asset_ids"].append(row["asset_id"])
        if kind == "city" and parent:
            bucket["parent"] = parent
    result = [value for value in grouped.values() if value["asset_ids"]]
    for item in result:
        item["total"] = len(item.pop("asset_ids"))
        item["aliases"] = sorted(set(item["aliases"]))
    if name:
        needle = normalize_name(name)
        result = [item for item in result if needle in normalize_name(item["name"] + " " + item["parent"] + " " + " ".join(item["aliases"]))]
    result.sort(key=lambda value: (value["name"], value["parent"], value["key"]))
    filter_revision = revision_module.read_filter_revision(catalog.connection)
    suggestion_revision = revision_module.read_suggestion_revision(catalog.connection)
    _, current_package_revision, _, _ = _index(catalog, data_root)
    if (filter_revision, suggestion_revision, current_package_revision) != (before_filter, before_suggestion, package_revision):
        raise ApiError(CATALOG_FILTER_REVISION_STALE, "the album changed; reload the page", status=409)
    for item in result:
        origins = item.pop("origins")
        item["origin"] = "MIXED" if len(origins) > 1 else next(iter(origins))
    return {"kind": kind, "catalog_filter_revision": filter_revision, "suggestion_revision": suggestion_revision, "package_revision": package_revision, "albums": result}


def album_assets(catalog: Any, data_root: Any, *, kind: str, key: str, limit: int = PAGE_SIZE, cursor: str | None = None) -> dict[str, Any]:
    collection = albums(catalog, data_root, kind=kind)
    revisions = (collection["catalog_filter_revision"], collection["suggestion_revision"], collection["package_revision"], kind, str(key))
    start = _decode(cursor, revisions)
    selected = next((item for item in collection["albums"] if item["key"] == str(key)), None)
    if selected is None:
        return {"assets": [], "total": 0, "next_cursor": None, "catalog_filter_revision": revisions[0], "suggestion_revision": revisions[1], "package_revision": revisions[2]}
    rows, _ = _album_rows(catalog, data_root)
    ids = [row["asset_id"] for row in rows if _key(kind, row) == (kind, str(key))]
    _, current_package_revision, _, _ = _index(catalog, data_root)
    if (revision_module.read_filter_revision(catalog.connection), revision_module.read_suggestion_revision(catalog.connection), current_package_revision) != revisions[:3]:
        raise ApiError(CATALOG_FILTER_REVISION_STALE, "the album changed; reload the page", status=409)
    page = ids[start : start + max(1, min(int(limit), 100))]
    next_cursor = _token(start + len(page), revisions) if start + len(page) < len(ids) else None
    return {"assets": [{"asset_id": value, "thumbnail_url": f"/api/v1/assets/{value}/thumbnail", "detail_url": f"/api/v1/assets/{value}"} for value in page], "total": len(ids), "next_cursor": next_cursor, "catalog_filter_revision": revisions[0], "suggestion_revision": revisions[1], "package_revision": revisions[2]}
