"""Build the pinned mapchina administrative package (no photos or model downloads).

uv run --group geo-data python tools/places/prepare_boundaries.py --output /data/geo/china.json
The output can be imported in 地点 → 离线地点数据. Runtime never needs R or rdata.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
import warnings
from pathlib import Path

from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon, mapping

from photo_search.geo.boundaries import FORMAT, parse_package

REVISION = "053136c1c9c01742681bf9224ecad4d8c0bf9df0"
SOURCE = f"https://raw.githubusercontent.com/xmc811/mapchina/{REVISION}/data/china.rda"
CHECKSUM = "5ac3795894d61fe9c01060c5a0f58f4076d3e53c48a441e0f48d54acd3f687be"


def prepare(output: Path, source_file: Path | None = None):
    import pandas as pd
    import rdata

    output.parent.mkdir(parents=True, exist_ok=True)
    source_file = source_file or output.with_suffix(".rda")
    if not source_file.exists():
        with urllib.request.urlopen(SOURCE, timeout=60) as response:
            raw = response.read(10 * 1024 * 1024 + 1)
        if hashlib.sha256(raw).hexdigest() != CHECKSUM:
            raise ValueError("downloaded boundary source checksum differs from the pinned release")
        source_file.write_bytes(raw)
    if hashlib.sha256(source_file.read_bytes()).hexdigest() != CHECKSUM:
        raise ValueError("boundary source checksum differs from the pinned release")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        frame = rdata.read_rda(source_file)["china"]
    features = [{"id": "CN", "kind": "country", "name": "中国", "parent": None}]
    known = {"CN"}
    repaired = 0
    for _, row in frame.iterrows():
        province = "province:" + row.Code_Province
        if province not in known:
            features.append(
                {"id": province, "name": row.Name_Province, "kind": "province", "parent": "CN"}
            )
            known.add(province)
        parent = province
        if not pd.isna(row.Name_Perfecture):
            city = "city:" + row.Code_Perfecture
            if city not in known:
                features.append(
                    {"id": city, "name": row.Name_Perfecture, "kind": "city", "parent": province}
                )
                known.add(city)
            parent = city
        geom = MultiPolygon([Polygon(part[0], part[1:]) for part in row.geometry])
        if not geom.is_valid:
            geom = make_valid(geom)
            repaired += 1
            if geom.geom_type == "GeometryCollection":
                polygons = []
                for part in geom.geoms:
                    if part.geom_type == "Polygon":
                        polygons.append(part)
                    elif part.geom_type == "MultiPolygon":
                        polygons.extend(part.geoms)
                geom = MultiPolygon(polygons)
        key = "district:" + row.Code_County
        if key in known:
            raise ValueError("source contains repeated district codes")
        known.add(key)
        features.append(
            {
                "id": key,
                "name": row.Name_County,
                "kind": "district",
                "parent": parent,
                "geometry": mapping(geom),
            }
        )
    data = {
        "format": FORMAT,
        "metadata": {
            "namespace": "mapchina-cn",
            "version": REVISION[:12],
            "crs": "EPSG:4326",
            "layer": "administrative",
            "source": "https://github.com/xmc811/mapchina",
            "license": "GPL-3.0 (mapchina data)",
            "attribution": "Mingchu Xu / mapchina",
            "source_sha256": CHECKSUM,
            "coverage": f"中国 · {len(frame)} 个区县边界（源版本覆盖）",
            "boundary_year": "来源未标明；历史区划，可能与现行区划不同",
            "geometry_repairs": repaired,
        },
        "features": features,
    }
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    parse_package(raw)
    temporary = output.with_suffix(".partial")
    temporary.write_bytes(raw)
    temporary.replace(output)
    print(
        json.dumps(
            {
                "output": str(output),
                "bytes": len(raw),
                "places": len(features),
                "districts": len(frame),
                "repaired_geometries": repaired,
            },
            ensure_ascii=False,
        )
    )


def prepare_scenic(source_file: Path, output: Path):
    """Convert a saved Nominatim GeoJSON export; never queries a user's photo locations."""
    raw_source = source_file.read_bytes()
    features = []
    for item in json.loads(raw_source):
        geometry = item.get("geojson", {})
        if geometry.get("type") not in ("Polygon", "MultiPolygon"):
            raise ValueError("a scenic area requires a polygon, not a point or guessed radius")
        features.append(
            {
                "id": f"{item['osm_type']}:{item['osm_id']}",
                "kind": "attraction",
                "name": item.get("name") or item["display_name"].split(",")[0],
                "geometry": geometry,
            }
        )
    if not features:
        raise ValueError("no scenic polygon in the export")
    digest = hashlib.sha256(raw_source).hexdigest()
    data = {
        "format": FORMAT,
        "metadata": {
            "namespace": "osm-scenic:" + features[0]["id"],
            "version": digest[:12],
            "crs": "EPSG:4326",
            "layer": "scenic",
            "source": "https://www.openstreetmap.org/copyright",
            "license": "ODbL-1.0",
            "attribution": "© OpenStreetMap contributors",
            "coverage": "、".join(f["name"] for f in features),
            "boundary_year": "OSM 导出快照（不保证现行官方景区边界）",
            "source_sha256": digest,
        },
        "features": features,
    }
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    parse_package(raw)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(raw)
    print(
        json.dumps(
            {"output": str(output), "areas": len(features), "bytes": len(raw)}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-file", type=Path)
    parser.add_argument(
        "--osm-json", type=Path, help="saved Nominatim polygon export for scenic areas"
    )
    args = parser.parse_args()
    if args.osm_json:
        prepare_scenic(args.osm_json, args.output)
    else:
        prepare(args.output, args.source_file)
