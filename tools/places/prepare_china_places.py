"""Extract a frozen OSM input into a small, auditable Chinese admin sidecar.

Preparation only: no catalog, photo, network or installed-package access. Install
the places-build dependency group, then pass a complete local PBF (or test OSM XML).
The raw source stays outside the product package; only administrative areas survive.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import osmium
import shapely
from shapely.errors import GEOSException
from shapely.geometry import Point, mapping, shape
from shapely.strtree import STRtree

SCHEMA = "photo-search-cn-admin-sidecar.v1"
LEVELS = {4: "province", 5: "city", 6: "district"}
SOURCE_URL = "https://download.geofabrik.de/asia/china.html"
LICENSE_URL = "https://www.openstreetmap.org/copyright"
SAMPLES = (
    ("杭州西湖区", 30.273, 120.090, ("浙江省", "杭州市", "西湖区")),
    ("上海黄浦区", 31.218, 121.480, ("上海市", None, "黄浦区")),
    ("深圳南山区", 22.533, 113.929, ("广东省", "深圳市", "南山区")),
    ("重庆渝中区", 29.558, 106.565, ("重庆市", None, "渝中区")),
)


def digest(path: Path, algorithm: str = "sha256") -> str:
    value = hashlib.new(algorithm)
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def chinese_names(tags: dict[str, str]) -> list[str]:
    """Preserve source spelling; do not invent suffixes or transliterate names."""
    names: list[str] = []
    for key in ("name:zh-Hans", "name:zh", "name", "official_name:zh", "short_name:zh"):
        for text in tags.get(key, "").split(";"):
            text = text.strip()
            if text and any("\u3400" <= char <= "\u9fff" for char in text) and text not in names:
                names.append(text)
    return names


def qualified(tags: dict[str, str]) -> bool:
    return (
        tags.get("boundary") == "administrative"
        and tags.get("admin_level") in {"4", "5", "6"}
        and tags.get("type") in {"boundary", "multipolygon"}
    )


def extract(source: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Assemble complete relations, including all required way/node members.

    Areas missing members never silently become smaller valid polygons: PyOsmium
    must assemble the original relation; absent output is recorded as rejected.
    """
    relation_tags: dict[int, dict[str, str]] = {}
    filters = (
        osmium.filter.TagFilter(("boundary", "administrative")),
        osmium.filter.TagFilter(*[("admin_level", str(level)) for level in LEVELS]),
    )
    relations = osmium.FileProcessor(str(source), osmium.osm.RELATION)
    for relation_filter in filters:
        relations.with_filter(relation_filter)
    for relation in relations:
        tags = dict(relation.tags)
        if qualified(tags):
            relation_tags[int(relation.id)] = tags
    factory = osmium.geom.GeoJSONFactory()
    rows: dict[int, dict[str, Any]] = {}
    rejected: dict[int, str] = {}
    processor = (
        osmium.FileProcessor(str(source))
        .with_areas(*filters)
        .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA))
    )
    for area in processor:
        if area.from_way() or area.orig_id() not in relation_tags:
            continue
        relation_id = int(area.orig_id())
        tags = relation_tags[relation_id]
        try:
            geometry = shape(json.loads(factory.create_multipolygon(area)))
            if geometry.is_empty or not geometry.is_valid:
                rejected[relation_id] = "INVALID_GEOMETRY"
                continue
            if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
                rejected[relation_id] = "UNSUPPORTED_GEOMETRY"
                continue
            west, south, east, north = geometry.bounds
            if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
                rejected[relation_id] = "INVALID_WGS84_BOUNDS"
                continue
            names = chinese_names(tags)
            if not names:
                rejected[relation_id] = "NO_CHINESE_NAME"
                continue
            level = int(tags["admin_level"])
            rows[relation_id] = {
                "id": f"relation/{relation_id}",
                "admin_level": level,
                "kind": LEVELS[level],
                "name": names[0],
                "aliases": names[1:],
                "iso3166_2": tags.get("ISO3166-2", ""),
                "bbox": list(geometry.bounds),
                "geometry": mapping(geometry),
            }
        except (RuntimeError, ValueError, GEOSException) as error:
            rejected[relation_id] = f"ASSEMBLY_FAILED:{type(error).__name__}"
    for relation_id in relation_tags.keys() - rows.keys() - rejected.keys():
        rejected[relation_id] = "INCOMPLETE_RELATION"
    return list(rows.values()), [
        {"id": f"relation/{key}", "reason": value} for key, value in sorted(rejected.items())
    ]


def attach_hierarchy(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Require a unique containing parent; retain real missing-city hierarchies."""
    provinces = [
        row
        for row in rows
        if row["admin_level"] == 4
        and any(code.strip().startswith("CN-") for code in row["iso3166_2"].split(";"))
    ]
    province_shapes = [shape(row["geometry"]) for row in provinces]
    province_tree = STRtree(province_shapes)
    accepted = [dict(row, parent_province_id=None, parent_city_id=None) for row in provinces]
    rejected = []
    children = []
    for row in rows:
        if row["admin_level"] == 4:
            continue
        geometry = shape(row["geometry"])
        parents = [
            provinces[int(index)]
            for index in province_tree.query(geometry)
            if province_shapes[int(index)].covers(geometry)
        ]
        if len(parents) != 1:
            rejected.append({"id": row["id"], "reason": "NO_UNIQUE_CN_PROVINCE"})
            continue
        children.append(dict(row, parent_province_id=parents[0]["id"], parent_city_id=None))
    cities = [row for row in children if row["admin_level"] == 5]
    city_shapes = [shape(row["geometry"]) for row in cities]
    city_tree = STRtree(city_shapes)
    for row in children:
        if row["admin_level"] == 6:
            geometry = shape(row["geometry"])
            parents = [
                cities[int(index)]
                for index in city_tree.query(geometry)
                if cities[int(index)]["parent_province_id"] == row["parent_province_id"]
                and city_shapes[int(index)].covers(geometry)
            ]
            if len(parents) > 1:
                rejected.append({"id": row["id"], "reason": "AMBIGUOUS_CITY_PARENT"})
                continue
            if parents:
                row["parent_city_id"] = parents[0]["id"]
        accepted.append(row)
    return sorted(accepted, key=lambda row: (row["admin_level"], row["id"])), rejected


def validate_samples(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    geometries = [shape(row["geometry"]) for row in rows]
    tree = STRtree(geometries)
    by_id = {row["id"]: row for row in rows}
    results = []
    for label, latitude, longitude, expected in SAMPLES:
        point = Point(longitude, latitude)
        candidates = [
            rows[int(index)]
            for index in tree.query(point)
            if geometries[int(index)].contains(point)
        ]
        levels = {
            level: [row for row in candidates if row["admin_level"] == level] for level in LEVELS
        }
        selected = {
            level: values[0] if len(values) == 1 else None for level, values in levels.items()
        }
        district = selected[6]
        passed = district is not None and all(
            name is None
            or (
                selected[level] is not None
                and name in [selected[level]["name"], *selected[level]["aliases"]]
            )
            for level, name in zip(LEVELS, expected, strict=True)
        )
        if district is not None:
            passed = passed and district["parent_province_id"] == (
                selected[4]["id"] if selected[4] else None
            )
            if expected[1] is not None:
                passed = passed and district["parent_city_id"] == (
                    selected[5]["id"] if selected[5] else None
                )
        results.append(
            {
                "sample": label,
                "latitude": latitude,
                "longitude": longitude,
                "passed": bool(passed),
                "matches": [
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "parent_province": by_id.get(row["parent_province_id"], {}).get("name"),
                        "parent_city": by_id.get(row["parent_city_id"], {}).get("name"),
                    }
                    for row in candidates
                ],
            }
        )
    return results


def build(
    source: Path,
    destination: Path,
    *,
    source_url: str,
    expected_md5: str | None = None,
    expected_sha256: str | None = None,
    expected_byte_size: int | None = None,
) -> dict[str, Any]:
    if not source.is_file() or source.suffix not in {".pbf", ".osm"}:
        raise ValueError("source must be a complete local .osm.pbf or test .osm file")
    if (
        source.suffix == ".pbf"
        and expected_md5 is None
        and (expected_sha256 is None or expected_byte_size is None)
    ):
        raise ValueError("PBF inputs require the published upstream MD5 or pinned SHA256 and size")
    if expected_md5 is not None and digest(source, "md5") != expected_md5.lower():
        raise ValueError("source does not match the upstream MD5")
    if expected_byte_size is not None and source.stat().st_size != expected_byte_size:
        raise ValueError("source does not match the expected byte size")
    source_hash = digest(source)
    if expected_sha256 is not None and source_hash != expected_sha256.lower():
        raise ValueError("source does not match the pinned SHA256")
    # Do not mix new output with a previous source; caller chooses a fresh directory.
    destination.mkdir(parents=True, exist_ok=False)
    rows, rejected = extract(source)
    rows, hierarchy_rejected = attach_hierarchy(rows)
    rejected.extend(hierarchy_rejected)
    data_file = destination / "cn-admin.jsonl.gz"
    with data_file.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
        for row in rows:
            stream.write(
                (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            )
    report = {
        "schema": SCHEMA,
        "provider": "osm-admin",
        "coordinate_reference_system": "WGS84",
        "source_url": source_url,
        "source_snapshot": source.name,
        "source_sha256": source_hash,
        "source_byte_size": source.stat().st_size,
        "upstream_md5_verified": expected_md5 is not None,
        "upstream_md5": expected_md5,
        "pinned_sha256": expected_sha256,
        "expected_byte_size": expected_byte_size,
        "license_id": "ODbL-1.0",
        "license_url": LICENSE_URL,
        "attribution": "© OpenStreetMap contributors",
        "builder": {"osmium": "4.3.1", "shapely": shapely.__version__},
        "levels": dict(Counter(row["kind"] for row in rows)),
        "record_count": len(rows),
        "rejected": rejected,
        "samples": validate_samples(rows),
        "data_file": data_file.name,
        "data_sha256": digest(data_file),
        "data_byte_size": data_file.stat().st_size,
    }
    (destination / "source-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (destination / "ODbL.txt").write_text(
        f"© OpenStreetMap contributors\nODbL 1.0\n{LICENSE_URL}\n{source_url}\n",
        encoding="utf-8",
        newline="\n",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-url", default=SOURCE_URL)
    parser.add_argument("--expected-md5")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--expected-byte-size", type=int)
    args = parser.parse_args()
    report = build(
        args.source,
        args.output,
        source_url=args.source_url,
        expected_md5=args.expected_md5,
        expected_sha256=args.expected_sha256,
        expected_byte_size=args.expected_byte_size,
    )
    print(
        json.dumps(
            {key: report[key] for key in ("record_count", "levels", "data_byte_size", "samples")},
            ensure_ascii=False,
        )
    )
    return 0 if all(sample["passed"] for sample in report["samples"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
