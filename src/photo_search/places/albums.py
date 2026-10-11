"""Read-only photo albums derived from the current place facts.

This view is intentionally separate from ADR-0005's manual assignment filters. A GPS
nearby-city row is a browsing hint and never becomes an assignment or FTS document.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from photo_search.api.errors import ApiError
from photo_search.catalog import schema as schema_module
from photo_search.catalog.errors import CATALOG_FILTER_REVISION_STALE, INVALID_CURSOR
from photo_search.catalog.video import photo_asset_sql
from photo_search.geo import dataset as dataset_module
from photo_search.places import cn_facets
from photo_search.places import datasets as datasets_module
from photo_search.places import revision as revision_module
from photo_search.places.contracts import normalize_name

AUTO_MAX_DISTANCE_M = 50_000.0
PAGE_SIZE = 48
_INDEX_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


def _index(catalog: Any, data_root: Any) -> tuple[dict[str, Any], str | None, str | None, bool]:
    active = datasets_module.active_row(catalog.connection)
    if active is None:
        return {}, None, None, False
    pointer_revision = catalog.read_one("SELECT revision FROM active_geo_dataset WHERE singleton=1")["revision"]
    gps_revision = catalog.read_one("SELECT revision FROM map_member_revision WHERE singleton=1")["revision"]
    stamp = f"{active['dataset_id']}:{active['content_sha256']}:{pointer_revision}:{gps_revision}"
    try:
        directory = datasets_module.dataset_dir(data_root, str(active["dataset_id"]))
        cache_key = (str(directory), str(active["content_sha256"]))
        cached = _INDEX_CACHE.get(cache_key)
        if cached is None:
            # The registry accepted this immutable package with full validation.
            # Browse verifies bytes and reads only the original GeoNames INDEX;
            # it never loads administrative geometries or recomputes point membership.
            manifest = dataset_module.validate_manifest(dataset_module.read_manifest(directory), supported_schema_version=schema_module.SUPPORTED_SCHEMA_VERSION)
            if manifest["dataset_id"] != active["dataset_id"] or manifest["content_sha256"] != active["content_sha256"]:
                raise ValueError("package identity changed")
            files = dataset_module.verify_files(directory, manifest["files"])
            if dataset_module.declared_content_sha256(files) != active["content_sha256"]:
                raise ValueError("package content changed")
            original = next((f for f in files if f.role == "INDEX" and not f.relative_path.startswith("cn-admin/")), None)
            cached = dataset_module._parse_province_index(directory, original.relative_path) if original else {}
            _INDEX_CACHE.clear()
            _INDEX_CACHE[cache_key] = cached
    except Exception:
        return {}, stamp, str(active["provider"]), False
    return dict(cached), stamp, str(active["provider"]), True


def _album_rows(catalog: Any, data_root: Any, *, suppress_cn: bool = True) -> tuple[list[dict[str, Any]], str | None]:
    province_index, package_revision, provider, package_available = _index(catalog, data_root)
    city_regions = province_index.get("city_to_admin1", {})
    photo_sql, photo_parameters = photo_asset_sql()
    rows = catalog.read_all(
        f"""SELECT a.asset_id, a.sha256, a.byte_size, a.created_at,
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
            WHERE ({photo_sql})
              AND EXISTS (SELECT 1 FROM file_locations l WHERE l.asset_id = a.asset_id
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
              AND (?=0 OR (NOT EXISTS(SELECT 1 FROM cn_legacy_links cl
                      WHERE cl.dataset_id=active.dataset_id AND cl.place_id=ap.place_id)
                  AND NOT (COALESCE(ap.mode,'AUTO_ALLOWED')='AUTO_ALLOWED'
                      AND EXISTS(SELECT 1 FROM cn_base_china_places cp
                           WHERE cp.dataset_id=active.dataset_id AND cp.place_id=pc.local_place_id))))
            ORDER BY a.asset_id""",
        (*photo_parameters, AUTO_MAX_DISTANCE_M, int(package_available), int(suppress_cn)),
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


def _chunks(values: list[int], size: int = 500) -> list[list[int]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _sort_albums(values: list[dict[str, Any]]) -> None:
    """Rank albums by match count, then by newest catalog addition and stable label."""
    values.sort(
        key=lambda value: (
            -int(value.get("total", 0)),
            -int(value.get("_latest_added_at", 0)),
            str(value.get("name", "")),
            str(value.get("parent", "")),
            str(value.get("key", "")),
        )
    )
    for value in values:
        value.pop("_latest_added_at", None)


def _group_rows(
    catalog: Any, kind: str, rows: list[dict[str, Any]]
) -> dict[tuple[str, str], dict[str, Any]]:
    """Group one ordered member read, fetching place labels/aliases in batches."""
    place_ids = sorted({int(row["place_id"]) for row in rows if row["place_id"] is not None})
    parent_ids = sorted(
        {
            int(row["parent_place_id"])
            for row in rows
            if row["parent_place_id"] is not None and kind == "city"
        }
    )
    if kind == "country":
        parent_ids = sorted(
            {
                int(row["parent_place_id"])
                for row in rows
                if row["kind"] == "city" and row["parent_place_id"] is not None
            }
        )
        parent_ids.extend(value for value in place_ids if value not in parent_ids)
    names: dict[int, str] = {}
    aliases: dict[int, list[str]] = {}
    for batch in _chunks(sorted(set(parent_ids))):
        placeholders = ",".join("?" for _ in batch)
        names.update(
            {
                int(row["place_id"]): str(row["canonical_name"])
                for row in catalog.read_all(
                    f"SELECT place_id, canonical_name FROM places WHERE place_id IN ({placeholders})",
                    tuple(batch),
                )
            }
        )
    if kind == "city":
        for batch in _chunks(place_ids):
            placeholders = ",".join("?" for _ in batch)
            for row in catalog.read_all(
                f"SELECT place_id, text FROM place_aliases WHERE place_id IN ({placeholders}) AND lifecycle = 'ACTIVE' ORDER BY place_id, alias_id",
                tuple(batch),
            ):
                aliases.setdefault(int(row["place_id"]), []).append(str(row["text"]))

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = _key(kind, row)
        if key is None:
            continue
        region = row.get("region")
        label = names.get(int(key[1]), "") if kind == "country" else str(row["name"])
        parent = ""
        row_aliases: list[str] = []
        if kind == "city":
            parent = names.get(int(row["parent_place_id"]), "") if row["parent_place_id"] else ""
            row_aliases = aliases.get(int(row["place_id"]), [])
        elif kind == "province":
            label = str(region.get("name", ""))
            row_aliases = [str(value) for value in region.get("aliases", [])]
        bucket = grouped.setdefault(
            key,
            {
                "kind": kind,
                "key": key[1],
                "name": label,
                "aliases": row_aliases,
                "parent": parent,
                "asset_ids": [],
                "asset_id_set": set(),
                "cover_asset_id": int(row["asset_id"]),
                "_latest_added_at": int(row["created_at"] or 0),
                "origins": set(),
            },
        )
        bucket["_latest_added_at"] = max(
            int(bucket["_latest_added_at"]), int(row["created_at"] or 0)
        )
        bucket["origins"].add(row["origin"])
        asset_id = int(row["asset_id"])
        if asset_id not in bucket["asset_id_set"]:
            bucket["asset_id_set"].add(asset_id)
            bucket["asset_ids"].append(asset_id)
        if kind == "city" and parent:
            bucket["parent"] = parent
    return grouped


def albums(catalog: Any, data_root: Any, *, kind: str = "city", name: str | None = None) -> dict[str, Any]:
    if kind not in ("city", "province", "country", "district"):
        raise ValueError("kind must be city, province or country")
    if kind == "district":
        result = cn_facets.collection(catalog, kind=kind, name=name)
        _sort_albums(result["albums"])
        return result
    before_filter = revision_module.read_filter_revision(catalog.connection)
    before_suggestion = revision_module.read_suggestion_revision(catalog.connection)
    rows, package_revision = _album_rows(catalog, data_root, suppress_cn=kind != "country")
    grouped = _group_rows(catalog, kind, rows)
    result = [value for value in grouped.values() if value["asset_ids"]]
    for item in result:
        item["total"] = len(item.pop("asset_ids"))
        item.pop("asset_id_set", None)
        item["aliases"] = sorted(set(item["aliases"]))
    if name:
        needle = normalize_name(name)
        result = [item for item in result if needle in normalize_name(item["name"] + " " + item["parent"] + " " + " ".join(item["aliases"]))]
    filter_revision = revision_module.read_filter_revision(catalog.connection)
    suggestion_revision = revision_module.read_suggestion_revision(catalog.connection)
    _, current_package_revision, _, _ = _index(catalog, data_root)
    if (filter_revision, suggestion_revision, current_package_revision) != (before_filter, before_suggestion, package_revision):
        raise ApiError(CATALOG_FILTER_REVISION_STALE, "the album changed; reload the page", status=409)
    for item in result:
        origins = item.pop("origins")
        item["origin"] = "MIXED" if len(origins) > 1 else next(iter(origins))
    if kind in ("city", "province"):
        chinese = cn_facets.collection(catalog, kind=kind, name=name)
        if (chinese["catalog_filter_revision"], chinese["suggestion_revision"], chinese["package_revision"]) != (before_filter, before_suggestion, package_revision):
            raise ApiError(CATALOG_FILTER_REVISION_STALE, "the albums changed; reload", status=409)
        result.extend(chinese["albums"])
    _sort_albums(result)
    return {"kind": kind, "catalog_filter_revision": filter_revision, "suggestion_revision": suggestion_revision, "package_revision": package_revision, "albums": result}


def album_assets(catalog: Any, data_root: Any, *, kind: str, key: str, limit: int = PAGE_SIZE, cursor: str | None = None) -> dict[str, Any]:
    if str(key).startswith("cn:") or kind == "district":
        return cn_facets.page(catalog, kind=kind, key=key, limit=limit, cursor=cursor)
    before_filter = revision_module.read_filter_revision(catalog.connection)
    before_suggestion = revision_module.read_suggestion_revision(catalog.connection)
    rows, package_revision = _album_rows(catalog, data_root, suppress_cn=kind != "country")
    revisions = (before_filter, before_suggestion, package_revision, kind, str(key))
    start = _decode(cursor, revisions)
    grouped = _group_rows(catalog, kind, rows)
    selected = grouped.get((kind, str(key)))
    if selected is None:
        ids: list[int] = []
    else:
        ids = selected["asset_ids"]
    _, current_package_revision, _, _ = _index(catalog, data_root)
    if (revision_module.read_filter_revision(catalog.connection), revision_module.read_suggestion_revision(catalog.connection), current_package_revision) != revisions[:3]:
        raise ApiError(CATALOG_FILTER_REVISION_STALE, "the album changed; reload the page", status=409)
    page = ids[start : start + max(1, min(int(limit), 100))]
    next_cursor = _token(start + len(page), revisions) if start + len(page) < len(ids) else None
    return {"assets": [{"asset_id": value, "thumbnail_url": f"/api/v1/assets/{value}/thumbnail", "detail_url": f"/api/v1/assets/{value}"} for value in page], "total": len(ids), "next_cursor": next_cursor, "catalog_filter_revision": revisions[0], "suggestion_revision": revisions[1], "package_revision": revisions[2]}


def album_member_ids(catalog: Any, data_root: Any, *, kind: str, key: str, include_ids: bool = True) -> tuple[list[int], dict[str, Any]]:
    """Resolve one selected album through the exact membership reader used by browsing."""
    if str(key).startswith("cn:") or kind == "district":
        before = cn_facets.revisions(catalog)
        sql, params = cn_facets.members_sql(kind, key=key)
        ids = [int(row["asset_id"]) for row in catalog.read_all(f"SELECT asset_id FROM ({sql}) ORDER BY asset_id", params)] if include_ids else []
        choices = cn_facets.choices(catalog, kind=kind)
        selected = [dict(item, total=len(ids), origin=None) for item in choices if item["key"] == key]
        if cn_facets.revisions(catalog) != before:
            raise ApiError(CATALOG_FILTER_REVISION_STALE, "the administrative album changed", status=409)
        return ids, {"albums": selected, "catalog_filter_revision": before[0], "suggestion_revision": before[1], "package_revision": before[2]}
    collection = albums(catalog, data_root, kind=kind)
    selected = next((item for item in collection["albums"] if item["key"] == str(key)), None)
    if selected is None:
        return [], collection
    rows, _ = _album_rows(catalog, data_root, suppress_cn=kind != "country")
    ids = [int(row["asset_id"]) for row in rows if _key(kind, row) == (kind, str(key))]
    return ids, collection
