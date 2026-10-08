"""Small explicitly synthetic CN polygons and current GPS facts."""

import gzip
import hashlib
import json
from pathlib import Path

from p2_dataset_fixtures import build_package, mini_spec
from shapely.geometry import Polygon, box, mapping

from photo_search.places import datasets
from tools.places.compose_china_places import compose


def prepared_fixture(path: Path, *, duplicate: bool = False, jinan: bool = False) -> Path:
    path.mkdir()
    if jinan:
        shapes = [
            (1, "province", "山东省", ["山东"], None, None, box(116, 35, 118, 38)),
            (2, "city", "济南市", ["济南"], 1, None, box(116.5, 36, 117.5, 37)),
            (3, "district", "历下区", [], 1, 2, box(116.9, 36.5, 117.1, 36.75)),
        ]
    else:
        shapes = [
        (1, "province", "浙江省", ["浙江"], None, None, box(119, 29, 121, 31)),
        (2, "city", "杭州市", ["杭州", "Hangzhou"], 1, None, box(119.5, 29.5, 120.8, 30.8)),
        (
            3,
            "district",
            "西湖区",
            [],
            1,
            2,
            Polygon(
                box(120, 30.1, 120.2, 30.4).exterior.coords,
                [box(120.13, 30.24, 120.15, 30.26).exterior.coords],
            ),
        ),
        (4, "province", "上海市", ["上海", "Shanghai"], None, None, box(121, 31, 122, 32)),
        (
            5,
            "district",
            "西湖区" if duplicate else "黄浦区",
            [],
            4,
            None,
            box(121.2, 31.1, 121.8, 31.6),
        ),
        ]
    rows = [
        {
            "id": f"relation/{key}",
            "admin_level": {"province": 4, "city": 5, "district": 6}[kind],
            "kind": kind,
            "name": name,
            "aliases": aliases,
            "iso3166_2": "CN-XX" if kind == "province" else "",
            "bbox": list(geometry.bounds),
            "geometry": mapping(geometry),
            "parent_province_id": None if province is None else f"relation/{province}",
            "parent_city_id": None if city is None else f"relation/{city}",
        }
        for key, kind, name, aliases, province, city, geometry in shapes
    ]
    data = path / "cn-admin.jsonl.gz"
    with gzip.open(data, "wt", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    report = {
        "schema": "photo-search-cn-admin-sidecar.v1",
        "provider": "osm-admin",
        "coordinate_reference_system": "WGS84",
        "license_id": "ODbL-1.0",
        "source_snapshot": "synthetic-polygons",
        "source_url": "https://example.invalid/synthetic",
        "source_sha256": hashlib.sha256(b"synthetic").hexdigest(),
        "upstream_md5_verified": True,
        "upstream_md5": hashlib.md5(b"synthetic").hexdigest(),
        "data_sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
        "data_byte_size": data.stat().st_size,
        "record_count": len(rows),
        "samples": [{"sample": f"synthetic gate {n}", "passed": True} for n in range(4)],
    }
    (path / "source-report.json").write_text(json.dumps(report), encoding="utf-8")
    (path / "ODbL.txt").write_text(
        "Synthetic polygons for tests only. No real coverage claim.", encoding="utf-8"
    )
    return path


def install(
    catalog, root: Path, *, duplicate: bool = False, legacy_jinan: bool = False
) -> tuple[str, Path]:
    base = root / "base-package"
    if legacy_jinan:
        build_package(
            base,
            spec=mini_spec(
                dataset_id="geo-mini-jinan-1",
                places=(
                    {
                        "dataset_place_id": "country-cn",
                        "kind": "country",
                        "display_name": "中国",
                        "country_code": "CN",
                        "latitude": 35.0,
                        "longitude": 105.0,
                    },
                    {
                        "dataset_place_id": "city-jinan",
                        "kind": "city",
                        "display_name": "济南旧称",
                        "country_code": "CN",
                        "latitude": 36.65,
                        "longitude": 117.0,
                        "parent_dataset_place_id": "country-cn",
                    },
                ),
                aliases=(
                    {"dataset_place_id": "city-jinan", "text": "济南", "language": "zh"},
                ),
            ),
        )
    else:
        build_package(base)
    staged = datasets.stage_package(catalog, base)
    datasets.validate_dataset(catalog, staged["dataset_id"])
    datasets.activate_dataset(catalog, staged["dataset_id"])
    prepared = prepared_fixture(root / "prepared", duplicate=duplicate, jinan=legacy_jinan)
    composite = root / "composite"
    result = compose(base, prepared, composite)
    staged = datasets.stage_package(catalog, composite)
    datasets.validate_dataset(catalog, staged["dataset_id"])
    datasets.activate_dataset(catalog, staged["dataset_id"])
    return result["dataset_id"], composite


def geo(catalog, asset_id: int, lat: float = 30.273, lon: float = 120.09) -> None:
    asset = catalog.read_one("SELECT sha256 FROM assets WHERE asset_id=?", (asset_id,))
    location = catalog.read_one(
        "SELECT location_id FROM file_locations WHERE asset_id=? LIMIT 1", (asset_id,)
    )
    with catalog.write() as connection:
        connection.execute(
            """INSERT INTO asset_geo_facts(asset_id,geo_fact_id,location_id,input_revision,
            fact_revision,status,source,processor_id,raw_fields_checksum_sha256,raw_gps_json,
            latitude,longitude,datum_status,created_at,updated_at)
            VALUES (?,?,?,?,1,'VALID','EXIF_GPS','geo-exif-gps@1',?,'{"synthetic":true}',?,?,'WGS84',1,1)""",
            (
                asset_id,
                f"cn-gps-{asset_id}",
                location["location_id"],
                asset["sha256"],
                "a" * 64,
                lat,
                lon,
            ),
        )


def resolve_batch(catalog, root: Path, *, restart: bool = False) -> dict:
    from photo_search.geo import resolver
    from photo_search.places import cn_facets

    content = resolver.load_active_content(catalog.connection, data_root=root)
    run, rows = cn_facets.inputs(catalog, content, restart=restart)
    return cn_facets.publish(catalog, content, run, rows, cn_facets.calculate(content, rows))
