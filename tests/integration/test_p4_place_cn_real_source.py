"""Opt-in actual public package gate; catalog/assets remain fully synthetic."""

import json
import os
import time
from pathlib import Path

import pytest
from p4_cn_fixtures import geo

from photo_search.geo import dataset, resolver
from photo_search.places import albums, cn_facets, datasets

SAMPLES = (
    ("杭州西湖区", 30.273, 120.090, ("浙江省", "杭州市", "西湖区")),
    ("上海黄浦区", 31.218, 121.480, ("上海市", None, "黄浦区")),
    ("深圳南山区", 22.533, 113.929, ("广东省", "深圳市", "南山区")),
    ("重庆渝中区", 29.558, 106.565, ("重庆市", None, "渝中区")),
)


def test_actual_cn_package_activation_samples_tokyo_and_batch(api_env):
    package_arg = os.environ.get("P4_CN_COMPOSITE")
    base_arg = os.environ.get("P4_CN_BASE")
    if not package_arg or not base_arg:
        pytest.skip("set P4_CN_COMPOSITE/P4_CN_BASE to approved local public packages")
    catalog = api_env.service.catalog
    for package in (Path(base_arg), Path(package_arg)):
        staged = datasets.stage_package(catalog, package)
        datasets.validate_dataset(catalog, staged["dataset_id"])
        datasets.activate_dataset(catalog, staged["dataset_id"])
    content = resolver.load_active_content(catalog.connection, data_root=api_env.data_root)
    actual = []
    for label, lat, lon, expected in SAMPLES:
        result = content.cn_admin.resolve(lat, lon)
        names = [
            content.cn_admin.rows[result[k]]["name"] if result[k] else None
            for k in ("province", "city", "district")
        ]
        assert names == list(expected), (label, names)
        actual.append({"sample": label, "names": names, "passed": True})
    tokyo = dataset.nearby_cities(content, 35.681, 139.767, max_candidates=3, max_distance_m=50000)
    assert tokyo and content.places[tokyo[0]["dataset_place_id"]].country_code == "JP"
    for n in range(100):
        api_env.add_photo(f"cn-real-geometry-{n}.jpg", noise=n + 1)
    source = api_env.register_source("synthetic assets with public CN coordinates")
    api_env.scan(source["source_id"])
    ids = [
        int(r["asset_id"])
        for r in catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id")
    ]
    for n, asset_id in enumerate(ids):
        sample = SAMPLES[n % 4]
        geo(catalog, asset_id, sample[1], sample[2])
    before = time.perf_counter()
    run, rows = cn_facets.inputs(catalog, content)
    results = cn_facets.calculate(content, rows)
    outcome = cn_facets.publish(catalog, content, run, rows, results)
    elapsed = time.perf_counter() - before
    assert outcome["processed"] == 100
    assert (
        sum(
            r["total"] for r in albums.albums(catalog, api_env.data_root, kind="district")["albums"]
        )
        == 100
    )
    target = Path("test-results/p4-cn-real-source.json")
    target.write_text(
        json.dumps(
            {
                "dataset_id": content.dataset_id,
                "samples": actual,
                "tokyo": tokyo[0]["display_name"],
                "actual_geometry_batch": 100,
                "batch_seconds": elapsed,
                "photos_per_second": 100 / elapsed,
                "excludes_cold_package_load": True,
                "full_library_resolution_measured": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
