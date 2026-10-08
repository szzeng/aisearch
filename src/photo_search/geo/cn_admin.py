"""Versioned, read-only OSM administrative facets (ADR-0016).

These identities never enter the frozen Place enum or manual assignment protocol.
The existing manifest lists/checksums the addon as INDEX files; its own manifest
owns the source, geometry and facet contract, independently of GeoNames.
"""

from __future__ import annotations

import gzip
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shapely.geometry import Point, shape
from shapely.prepared import prep
from shapely.strtree import STRtree

from photo_search.catalog.errors import PLACE_DATASET_INVALID, CatalogError

CONTRACT = "cn-admin-facets.v1"
SCHEMA = "photo-search-cn-admin-addon.v1"
MANIFEST = "cn-admin/manifest.v1.json"
PROCESSOR = "geo-cn-admin@1"
KINDS = {"province": 4, "city": 5, "district": 6}


def invalid(message: str) -> CatalogError:
    return CatalogError(PLACE_DATASET_INVALID, message)


@dataclass(frozen=True)
class AdminContent:
    rows: dict[str, dict[str, Any]]
    geometries: tuple[Any, ...]
    prepared: tuple[Any, ...]
    tree: STRtree
    ids: tuple[str, ...]
    source: dict[str, Any]

    def resolve(self, latitude: float, longitude: float) -> dict[str, str | None]:
        if not (math.isfinite(latitude) and math.isfinite(longitude)):
            raise ValueError("coordinates must be finite")
        point = Point(longitude, latitude)
        matches: dict[str, list[str]] = {kind: [] for kind in KINDS}
        touched: set[str] = set()
        for index in self.tree.query(point):
            key = self.ids[int(index)]
            kind = self.rows[key]["kind"]
            if self.prepared[int(index)].contains(point):
                matches[kind].append(key)
            elif self.geometries[int(index)].covers(point):
                touched.add(kind)
        chosen = {
            kind: values[0] if len(values) == 1 and kind not in touched else None
            for kind, values in matches.items()
        }
        province, city, district = (chosen[kind] for kind in KINDS)
        # Never infer a missing/ambiguous higher level from a lower polygon.
        if province is None:
            city = district = None
        if city and self.rows[city]["parent_province_id"] != province:
            city = district = None
        if district:
            row = self.rows[district]
            if row["parent_province_id"] != province or row["parent_city_id"] != city:
                district = None
        if "city" in touched or len(matches["city"]) > 1:
            district = None
        ambiguous = touched or any(len(values) > 1 for values in matches.values())
        return {
            "province": province,
            "city": city,
            "district": district,
            "reason": "BOUNDARY_OR_OVERLAP"
            if ambiguous
            else ("MATCHED" if district else "UPPER_ONLY" if province else "UNKNOWN"),
        }


def load(directory: Path, files: tuple[Any, ...]) -> AdminContent | None:
    declared = {item.relative_path: item for item in files}
    if MANIFEST not in declared:
        if any(name.startswith("cn-admin/") for name in declared):
            raise invalid("CN addon has no versioned manifest")
        return None
    try:
        manifest = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
        if set(manifest) != {"schema", "contract_version", "crs", "data", "report", "license"}:
            raise invalid("CN addon manifest has unknown or missing fields")
        if (manifest["schema"], manifest["contract_version"], manifest["crs"]) != (
            SCHEMA,
            CONTRACT,
            "WGS84",
        ):
            raise invalid("unsupported CN addon contract or CRS")
        for key in ("data", "report", "license"):
            name = manifest[key]
            if (
                not isinstance(name, str)
                or name not in declared
                or not name.startswith("cn-admin/")
            ):
                raise invalid("CN addon references an undeclared file")
            role = "LICENSE" if key == "license" else "INDEX"
            if declared[name].role != role:
                raise invalid("CN addon file role mismatch")
        report = json.loads((directory / manifest["report"]).read_text(encoding="utf-8"))
        if (
            report["schema"],
            report["provider"],
            report["coordinate_reference_system"],
            report["license_id"],
        ) != ("photo-search-cn-admin-sidecar.v1", "osm-admin", "WGS84", "ODbL-1.0"):
            raise invalid("CN addon source metadata mismatch")
        data = declared[manifest["data"]]
        if (data.sha256, data.byte_size) != (report["data_sha256"], report["data_byte_size"]):
            raise invalid("CN addon data disagrees with its source report")
        rows: dict[str, dict[str, Any]] = {}
        geometries = []
        with gzip.open(directory / manifest["data"], "rt", encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if set(row) != {
                    "id",
                    "admin_level",
                    "kind",
                    "name",
                    "aliases",
                    "iso3166_2",
                    "bbox",
                    "geometry",
                    "parent_province_id",
                    "parent_city_id",
                }:
                    raise invalid("unsupported CN administrative row")
                key = row["id"]
                if (
                    not isinstance(key, str)
                    or not key.startswith("relation/")
                    or not key[9:].isdigit()
                    or key in rows
                ):
                    raise invalid("invalid or repeated CN administrative identity")
                if row["kind"] not in KINDS or row["admin_level"] != KINDS[row["kind"]]:
                    raise invalid("CN administrative level mismatch")
                if not isinstance(row["name"], str) or not 1 <= len(row["name"]) <= 200:
                    raise invalid("invalid CN name")
                if not isinstance(row["aliases"], list) or any(
                    not isinstance(a, str) or not a for a in row["aliases"]
                ):
                    raise invalid("invalid CN aliases")
                geometry = shape(row.pop("geometry"))
                west, south, east, north = geometry.bounds
                if (
                    geometry.is_empty
                    or not geometry.is_valid
                    or geometry.geom_type not in {"Polygon", "MultiPolygon"}
                    or not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90)
                    or list(geometry.bounds) != row["bbox"]
                ):
                    raise invalid("invalid CN administrative geometry or bbox")
                rows[key] = row
                geometries.append(geometry)
        if len(rows) != report["record_count"] or not rows:
            raise invalid("CN administrative record count mismatch")
        ids = tuple(rows)
        shapes = dict(zip(ids, geometries, strict=True))
        for key, row in rows.items():
            province, city = row["parent_province_id"], row["parent_city_id"]
            if row["kind"] == "province":
                if (
                    province is not None
                    or city is not None
                    or not any(
                        code.strip().startswith("CN-") for code in row["iso3166_2"].split(";")
                    )
                ):
                    raise invalid("invalid CN province parent or country reference")
            else:
                if (
                    province not in rows
                    or rows[province]["kind"] != "province"
                    or not shapes[province].covers(shapes[key])
                ):
                    raise invalid("invalid CN province containment")
                if city is not None and (
                    row["kind"] != "district"
                    or city not in rows
                    or rows[city]["kind"] != "city"
                    or rows[city]["parent_province_id"] != province
                    or not shapes[city].covers(shapes[key])
                ):
                    raise invalid("invalid CN city containment")
        return AdminContent(
            rows,
            tuple(geometries),
            tuple(prep(g) for g in geometries),
            STRtree(geometries),
            ids,
            report,
        )
    except CatalogError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise invalid("CN addon cannot be decoded") from error


def legacy_links(content: Any) -> dict[str, dict[str, str | None]]:
    """Proven display equivalence only: source names AND source point containment.

    Uses the GeoNames city point, never the asset GPS. No suffix guessing, nearest
    city, renamed manual place or district mapping is introduced.
    """
    admin = content.cn_admin
    result = {}
    if admin is None:
        return result
    for key, place in content.places.items():
        if place.kind != "city" or place.country_code != "CN":
            continue
        names = {place.display_name, *(a["text"] for a in content.aliases.get(key, ()))}
        match = admin.resolve(place.latitude, place.longitude)
        city, province = match["city"], match["province"]
        target = city or province
        if target and names.intersection(
            {admin.rows[target]["name"], *admin.rows[target]["aliases"]}
        ):
            result[key] = {"province": province, "city": city}
    return result
