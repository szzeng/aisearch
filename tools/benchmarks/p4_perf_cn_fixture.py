"""Deterministic catalog-scale fixture; no real photo library or model inference.

GPS points are sampled inside the installed CN administrative polygons, with a
60/20/20 Shenzhen/other Guangdong/other province distribution. Resolution uses
the real product geometry code; catalog and time facts are explicitly synthetic.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import time
from collections import Counter
from pathlib import Path

from shapely.geometry import Point

from photo_search.catalog.db import Catalog
from photo_search.geo import resolver
from photo_search.geo.cn_admin import PROCESSOR
from photo_search.places import datasets
from tools.benchmarks.p4_ui_timeline_scale import _make_fixture


def create(root: Path, package: Path, count: int, seed: int, *, resume: bool = False) -> dict:
    if count < 100_000:
        raise ValueError("shared timeline fixture requires count >= 100000")
    root.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with Catalog.open(data_root=root / "data") as catalog:
        if resume:
            assert catalog.read_one("SELECT COUNT(*) FROM assets")[0] == count
            assert catalog.read_one("SELECT COUNT(*) FROM asset_geo_facts")[0] == 0
        else:
            _make_fixture(catalog, count)
        print("catalog/time/person fixture ready", flush=True)
        # This deployment package retains a manifest predecessor absent from a
        # fresh catalog. Exercise the product's verified standalone recovery.
        shutil.copytree(package, root / "data/places/datasets" / package.name, dirs_exist_ok=True)
        assert datasets.restore_surviving_dataset(catalog) is not None
        content = resolver.load_active_content(catalog.connection, data_root=root / "data")
        admin = content.cn_admin
        assert admin is not None
        by_name = {row["name"]: key for key, row in admin.rows.items()}
        shenzhen = by_name["深圳市"]
        guangdong = by_name["广东省"]
        others = [key for key, row in admin.rows.items()
                  if row["kind"] == "province" and key != guangdong]
        position = {key: index for index, key in enumerate(admin.ids)}
        ids = {r["external_id"]: int(r["admin_id"]) for r in
               catalog.read_all("SELECT admin_id,external_id FROM cn_admin_ids")}
        locations = {int(r["asset_id"]): int(r["location_id"]) for r in catalog.read_all(
            "SELECT asset_id,MIN(location_id) location_id FROM file_locations GROUP BY asset_id")}
        rng = random.Random(seed)
        counts: Counter = Counter()
        resolved_counts: Counter = Counter()
        geo_ms = []
        first_points = []
        for start in range(1, count + 1, 5000):
            facts, memberships = [], []
            before = time.perf_counter()
            for asset_id in range(start, min(start + 5000, count + 1)):
                bucket = asset_id % 10
                key = shenzhen if bucket < 6 else guangdong if bucket < 8 else rng.choice(others)
                poly = admin.geometries[position[key]]
                prepared = admin.prepared[position[key]]
                w, s, e, n = poly.bounds
                while True:
                    lon, lat = rng.uniform(w, e), rng.uniform(s, n)
                    if not prepared.contains(Point(lon, lat)):
                        continue
                    result = admin.resolve(lat, lon)
                    if result["province"] is None:
                        continue
                    if key == guangdong and result["city"] == shenzhen:
                        continue
                    break
                assert result["province"] in ids
                category = "shenzhen" if bucket < 6 else "other_guangdong" if bucket < 8 else "other_china"
                counts[category] += 1
                resolved_category = (
                    "shenzhen" if result["city"] == shenzhen
                    else "other_guangdong" if result["province"] == guangdong
                    else "other_china"
                )
                resolved_counts[resolved_category] += 1
                digest = f"{asset_id:064x}"
                facts.append((asset_id, f"perf-gps-{asset_id}", locations[asset_id], digest,
                              "a" * 64, lat, lon))
                memberships.append((asset_id, content.dataset_id, f"perf-gps-{asset_id}", digest,
                                    PROCESSOR, ids.get(result["province"]), ids.get(result["city"]),
                                    ids.get(result["district"]), result["reason"]))
                if len(first_points) < 128:
                    first_points.append({"asset_id": asset_id, "latitude": lat, "longitude": lon,
                                         "category": category})
            geo_ms.append((time.perf_counter() - before) * 1000)
            with catalog.write() as conn:
                conn.executemany("""INSERT INTO asset_geo_facts(asset_id,geo_fact_id,location_id,
                    input_revision,fact_revision,status,source,processor_id,raw_fields_checksum_sha256,
                    raw_gps_json,latitude,longitude,datum_status,created_at,updated_at)
                    VALUES(?,?,?,?,1,'VALID','EXIF_GPS','geo-exif-gps@1',?,'{"synthetic":true}',?,?,'WGS84',1,1)""", facts)
                conn.executemany("INSERT INTO cn_admin_resolutions VALUES(?,?,?,?,?,?,?,?,?,1)", memberships)
            if start % 50000 == 1:
                print(f"GPS resolved: {min(start + 4999, count)}/{count}", flush=True)
        # This generator uses the schema of the checked-out implementation. For
        # paired measurements preserve the pre-change fixture, then migrate a
        # backup with the new code; never forge an old schema_version here.
        catalog.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        report = {"assets": count, "locations": len(catalog.read_all("SELECT location_id FROM file_locations")),
                  "seed": seed, "distribution": dict(counts),
                  "distribution_definition": "sampling polygon quotas, not final administrative membership",
                  "resolved_distribution": dict(resolved_counts), "dataset_id": content.dataset_id,
                  "shenzhen_key": f"cn:{ids[shenzhen]}", "guangdong_key": f"cn:{ids[guangdong]}",
                  "fixture_kind": "synthetic catalog metadata; GPS validated/resolved by real polygons; no image files",
                  "geometry_generation_batch_ms": geo_ms, "sample_points": first_points,
                  "elapsed_seconds": time.perf_counter() - started, "schema_version": catalog.schema_version}
    (root / "fixture.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--count", type=int, default=300_000)
    parser.add_argument("--seed", type=int, default=20261015)
    parser.add_argument("--resume-catalog", action="store_true")
    args = parser.parse_args()
    result = create(args.root, args.package, args.count, args.seed, resume=args.resume_catalog)
    print(json.dumps({k: v for k, v in result.items() if k not in {"sample_points", "geometry_generation_batch_ms"}}, ensure_ascii=False))
