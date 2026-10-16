"""Ordering and summary regression for bounded timeline queries."""

from __future__ import annotations

from datetime import datetime

import pytest

from photo_search.api.timeline import timeline_page, timeline_periods
from photo_search.places import revision
from photo_search.taken_time.contract import parse_bound


def _seed(catalog):
    moments = [
        int(datetime(2023, 12, 31, 23, 59, 59).timestamp() * 1000),
        int(datetime(2024, 1, 1).timestamp() * 1000),
    ]
    civil = [parse_bound("2023-12-31T23:59:59"), parse_bound("2024-01-01")]
    sources = ["EXIF_ORIGINAL", "EXIF_DIGITIZED", "EXIF_ROOT", "FILE_MTIME", None]
    expected = []
    with catalog.write() as db:
        source = db.execute(
            "INSERT INTO source_roots(display_name,root_path,status) VALUES ('synthetic','synthetic://timeline','ONLINE')"
        ).lastrowid
        for asset_id in range(1, 146):
            category = asset_id % len(sources)
            time_source = sources[category]
            created = moments[asset_id % 2]
            capture = civil[asset_id % 2] if category < 3 else None
            db.execute(
                "INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) VALUES (?,?,1,?,'ACTIVE')",
                (asset_id, f"{asset_id:064x}", created),
            )
            if asset_id != 145:  # The legacy zero-location photo remains visible.
                suffix = ".mp4" if asset_id == 144 else ".jpg"
                path = f"photo-{asset_id}{suffix}"
                db.execute(
                    "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,availability) VALUES (?,?,?,?,1,1,'PRESENT')",
                    (source, path, path, asset_id),
                )
            if time_source:
                db.execute(
                    "INSERT INTO asset_metadata(asset_id,taken_at_sort,time_source,time_confidence) VALUES (?,?,?,?)",
                    (asset_id, capture, time_source, "MEDIUM" if category < 2 else "LOW"),
                )
            if asset_id != 144:
                expected.append((asset_id, created, category, capture))
    return expected


def _walk(catalog, **params):
    rows, cursor = [], None
    while True:
        page = timeline_page(catalog, limit=7, cursor=cursor, **params)
        rows.extend(page["assets"])
        cursor = page["next_cursor"]
        if cursor is None:
            return rows


@pytest.mark.parametrize("basis,bucket", [
    ("library", "all"), ("capture", "all"), ("capture", "capture"),
    ("capture", "unknown"), ("capture", "pending"),
])
def test_timeline_tied_keys_paging_and_exact_anchors(api_env, basis, bucket):
    catalog = api_env.service.catalog
    expected = _seed(catalog)
    ranks = {0: 0, 1: 0, 2: 1, 3: 1, 4: 2}
    if bucket != "all":
        target = {"capture": 0, "unknown": 1, "pending": 2}[bucket]
        expected = [row for row in expected if ranks[row[2]] == target]
    expected.sort(key=lambda row: (
        0 if basis == "library" else ranks[row[2]],
        -(row[3] if basis == "capture" and row[2] < 2 else row[1]), -row[0],
    ))
    params = {"time_basis": basis, "bucket": bucket}
    actual = _walk(catalog, **params)
    assert [row["asset_id"] for row in actual] == [row[0] for row in expected]
    assert len({row["asset_id"] for row in actual}) == len(actual)
    # Anchoring at a tied timestamp must include that exact ID, not rewind its ties.
    for position in (3, len(actual) // 2, len(actual) - 1):
        anchored = timeline_page(catalog, anchor=actual[position]["anchor"], limit=7, **params)
        assert [row["asset_id"] for row in anchored["assets"]] == [
            row["asset_id"] for row in actual[position:position + 7]
        ]


@pytest.mark.parametrize("basis", ["library", "capture"])
@pytest.mark.parametrize("month", [None, 12])
def test_timeline_seek_excludes_next_year_boundary(api_env, basis, month):
    catalog = api_env.service.catalog
    _seed(catalog)
    page = timeline_page(catalog, time_basis=basis, seek_year=2023, seek_month=month, limit=60)
    dated = [row for row in page["assets"] if row["date_state"] in ("capture", "library")]
    assert dated
    assert all(row["display_time"].startswith("2023-") for row in dated)


def test_timeline_last_civil_millisecond_in_december_9999(api_env):
    catalog = api_env.service.catalog
    _seed(catalog)
    with catalog.write() as db:
        db.execute("UPDATE asset_metadata SET taken_at_sort=? WHERE asset_id=1",
                   (parse_bound("9999-12-31T23:59:59.999"),))
    page = timeline_page(catalog, bucket="capture", year=9999, month=12,
                         seek_year=9999, seek_month=12)
    assert [row["asset_id"] for row in page["assets"]] == [1]
    assert page["assets"][0]["display_time"] == "9999-12-31T23:59:59.999"
    assert timeline_periods(catalog, year=9999)["months"] == [{"month": 12, "count": 1}]


def test_capture_source_merge_orders_civil_time_without_offset_conversion(api_env):
    catalog = api_env.service.catalog
    _seed(catalog)
    civil = parse_bound("2030-01-01T00:00:00")
    with catalog.write() as db:
        db.execute("UPDATE asset_metadata SET taken_at_sort=?,timezone_offset=-720 WHERE asset_id=5",
                   (civil,))
        db.execute("UPDATE asset_metadata SET taken_at_sort=?,timezone_offset=840 WHERE asset_id=6",
                   (civil,))
    page = timeline_page(catalog, limit=2)
    assert [row["asset_id"] for row in page["assets"]] == [6, 5]
    assert [row["display_time"] for row in page["assets"]] == [
        "2030-01-01T00:00:00.000+14:00", "2030-01-01T00:00:00.000-12:00",
    ]
    assert timeline_periods(catalog)["years"][0] == {"year": 2030, "count": 2}


def test_timeline_one_scan_summaries_preserve_global_counts_and_selected_months(api_env):
    catalog = api_env.service.catalog
    expected = _seed(catalog)
    counts = {"effective_total": len(expected), "trusted": sum(row[2] < 2 for row in expected),
              "unknown": sum(row[2] in (2, 3) for row in expected),
              "pending": sum(row[2] == 4 for row in expected)}
    for basis in ("library", "capture"):
        eligible = [row for row in expected if basis == "library" or row[2] < 2]
        summary = timeline_periods(catalog, time_basis=basis)
        assert summary["counts"] == counts
        assert summary["years"] == [
            {"year": 2024, "count": sum(row[0] % 2 == 1 for row in eligible)},
            {"year": 2023, "count": sum(row[0] % 2 == 0 for row in eligible)},
        ]
        months = timeline_periods(catalog, time_basis=basis, year=2023)
        assert months["counts"] == counts
        assert months["months"] == [{"month": 12, "count": sum(row[0] % 2 == 0 for row in eligible)}]
        assert timeline_periods(catalog, time_basis=basis, year=2020)["months"] == []
    with catalog.write() as db:
        db.execute("UPDATE metadata SET value='999' WHERE key='taken_time_rules_version'")
    summary = timeline_periods(catalog)
    assert summary["counts"] == {"effective_total": len(expected), "trusted": 0,
                                 "unknown": 0, "pending": len(expected)}
    assert summary["years"] == []
    assert len(_walk(catalog)) == len(expected)


@pytest.mark.parametrize("route", ["assets", "periods"])
def test_timeline_worker_preserves_http_scope_errors(api_env, route):
    async def scenario(client):
        await api_env.authenticate(client)
        invalid = await client.get(f"/api/v1/timeline/{route}", params={"kind": "invalid"})
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "VALIDATION_ERROR"
        assert invalid.json()["details"]["field"] == "kind"
        missing = await client.get(f"/api/v1/timeline/{route}",
                                   params={"kind": "source", "source_id": 999999})
        assert missing.status_code == 404
        assert missing.json()["code"] == "SOURCE_NOT_FOUND"
        # A structured failure must close its snapshot/reader and free the slot.
        valid = await client.get(f"/api/v1/timeline/{route}")
        assert valid.status_code == 200

    api_env.scenario(scenario)


def test_timeline_worker_preserves_stale_cursor_and_reposition_anchor(api_env):
    catalog = api_env.service.catalog
    _seed(catalog)

    async def scenario(client):
        await api_env.authenticate(client)
        first = await client.get("/api/v1/timeline/assets", params={"limit": 7})
        assert first.status_code == 200
        body = first.json()
        anchor = body["assets"][3]
        with catalog.write() as db:
            changed = revision.bump_filter_revision(db, cause="TEST_WORKER_STALE", now_ms=1)
        stale = await client.get("/api/v1/timeline/assets",
                                 params={"cursor": body["next_cursor"], "limit": 7})
        assert stale.status_code == 409
        assert stale.json()["code"] == "CATALOG_FILTER_REVISION_STALE"
        assert stale.json()["details"]["current_catalog_filter_revision"] == changed
        positioned = await client.get("/api/v1/timeline/assets",
                                      params={"anchor": anchor["anchor"], "limit": 7})
        assert positioned.status_code == 200
        assert positioned.json()["assets"][0]["asset_id"] == anchor["asset_id"]
        assert positioned.json()["catalog_filter_revision"] == changed

    api_env.scenario(scenario)
