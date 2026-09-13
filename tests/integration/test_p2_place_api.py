"""P2-03A: the frozen ``/api/v1`` place surface behind the local session boundary.

Only ``%TEMP%`` fixtures are used. GPS fact rows are seeded directly into the protected
catalog (the extraction path itself is covered by the geo suites), because this file is
about the transport contract: session/Host/Origin/CSRF, the error envelope, the filter
semantics and the fact that exact coordinates only ever leave ``/assets/{id}/geo``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from httpx import AsyncClient

from photo_search.geo.extract import GPS_PROCESSOR_ID
from photo_search.places.contracts import CONTRACT_VERSION

SAFE_DETAIL_KEYS = {
    "asset_ids",
    "place_id",
    "operation_id",
    "dataset_id",
    "expected_revision",
    "current_revision",
    "max_batch_size",
}


def seed_geo_fact(
    env: Any, asset_id: int, *, latitude: float = 30.25, longitude: float = 120.16
) -> str:
    """Write one current VALID fact straight into the protected catalog."""
    sha = str(
        env.service.catalog.read_one(
            "SELECT sha256 FROM assets WHERE asset_id = ?", (int(asset_id),)
        )["sha256"]
    )
    location = int(
        env.service.catalog.read_one(
            "SELECT location_id FROM file_locations WHERE asset_id = ? ORDER BY location_id",
            (int(asset_id),),
        )["location_id"]
    )
    raw = {
        "latitude_dms": [30.0, 15.0, 0.0],
        "latitude_ref": "N",
        "longitude_dms": [120.0, 9.0, 36.0],
        "longitude_ref": "E",
        "map_datum": "WGS84",
        "altitude_m": None,
        "altitude_ref": None,
        "direction_deg": None,
        "accuracy_m": None,
    }
    with env.service.catalog.write() as connection:
        connection.execute(
            "INSERT INTO asset_geo_facts(asset_id, geo_fact_id, location_id, input_revision, "
            "fact_revision, status, source, processor_id, raw_fields_checksum_sha256, "
            "raw_gps_json, latitude, longitude, datum_status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 1, 'VALID', 'EXIF_GPS', ?, ?, ?, ?, ?, 'WGS84', 1, 1)",
            (
                int(asset_id),
                f"geofact-{sha[:24]}-1",
                location,
                sha,
                GPS_PROCESSOR_ID,
                hashlib.sha256(b"{}").hexdigest(),
                json.dumps(raw, separators=(",", ":")),
                float(latitude),
                float(longitude),
            ),
        )
    return sha


def prepare(env: Any, *, photos: int = 3) -> list[int]:
    for index in range(photos):
        env.add_photo(f"photo-{index}.jpg", noise=index + 1)
    source = env.register_source()
    env.scan(int(source["source_id"]))
    rows = env.service.catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id")
    return [int(row["asset_id"]) for row in rows]


async def get_json(client: AsyncClient, url: str, **kwargs: Any) -> dict[str, Any]:
    response = await client.get(url, **kwargs)
    try:
        body = response.json()
    except ValueError:  # pragma: no cover - every JSON endpoint answers JSON here
        body = None
    return {"status": response.status_code, "body": body, "headers": dict(response.headers)}


async def create_place(client: AsyncClient, env: Any, csrf: str, **body: Any) -> dict[str, Any]:
    response = await client.post("/api/v1/places", json=body, headers=env.post_headers(csrf))
    return {"status": response.status_code, "body": response.json()}


def test_place_writes_require_the_session_origin_and_csrf(api_env) -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        anonymous = await client.post(
            "/api/v1/places", json={"kind": "city", "canonical_name": "杭州"}
        )
        anonymous_geo = await get_json(client, "/api/v1/assets/1/geo")
        await api_env.authenticate(client)
        no_csrf = await client.post(
            "/api/v1/places",
            json={"kind": "city", "canonical_name": "杭州"},
            headers={"Origin": "http://127.0.0.1"},
        )
        bad_origin = await client.post(
            "/api/v1/places",
            json={"kind": "city", "canonical_name": "杭州"},
            headers={"X-CSRF-Token": api_env.csrf_token, "Origin": "http://evil.example"},
        )
        bad_batch = await client.post(
            "/api/v1/place-assignments/batch",
            json={"request_id": "x", "items": []},
            headers=api_env.post_headers(api_env.csrf_token),
        )
        return {
            "anonymous_status": anonymous.status_code,
            "anonymous": anonymous.json(),
            "anonymous_geo": anonymous_geo,
            "no_csrf": no_csrf.json(),
            "bad_origin": bad_origin.json(),
            "bad_batch": bad_batch.json(),
        }

    result = api_env.scenario(scenario)
    assert result["anonymous_status"] == 401
    assert result["anonymous"]["code"] == "SESSION_REQUIRED"
    assert result["anonymous_geo"]["status"] == 401
    assert result["anonymous_geo"]["body"]["code"] == "SESSION_REQUIRED"
    assert result["no_csrf"]["code"] == "CSRF_INVALID"
    assert result["bad_origin"]["code"] == "ORIGIN_NOT_ALLOWED"
    # An empty batch is a caller error, not a silent no-op.
    assert result["bad_batch"]["code"] == "VALIDATION_ERROR"


def test_place_directory_endpoints(api_env) -> None:
    assets = prepare(api_env)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await api_env.authenticate(client)
        city = await create_place(
            client,
            api_env,
            csrf,
            kind="city",
            canonical_name="杭州",
            aliases=[{"text": "Hangzhou", "language": "en"}],
        )
        city_id = city["body"]["place_id"]
        lake = await create_place(
            client, api_env, csrf, kind="attraction", canonical_name="西湖", parent_place_id=city_id
        )
        country = await create_place(client, api_env, csrf, kind="country", canonical_name="中国")
        bad_parent = await create_place(
            client, api_env, csrf, kind="city", canonical_name="西湖区", parent_place_id=city_id
        )
        listing = await get_json(client, "/api/v1/places", params={"name": "hangzhou"})
        by_ancestor = await get_json(client, "/api/v1/places", params={"ancestor": city_id})
        no_match = await get_json(client, "/api/v1/places", params={"name": "不存在的名字"})
        reparent = await client.patch(
            f"/api/v1/places/{lake['body']['place_id']}",
            json={"parent_place_id": None, "expected_revision": 1},
            headers=api_env.post_headers(csrf),
        )
        renamed = await client.patch(
            f"/api/v1/places/{city_id}",
            json={"canonical_name": "杭州市", "expected_revision": 1},
            headers=api_env.post_headers(csrf),
        )
        stale = await client.patch(
            f"/api/v1/places/{city_id}",
            json={"canonical_name": "旧名", "expected_revision": 1},
            headers=api_env.post_headers(csrf),
        )
        missing = await client.patch(
            "/api/v1/places/999999",
            json={"canonical_name": "无", "expected_revision": 1},
            headers=api_env.post_headers(csrf),
        )
        coverage = await get_json(client, "/api/v1/places/coverage")
        datasets = await get_json(client, "/api/v1/places/datasets")
        return {
            "city": city,
            "lake": lake,
            "country": country,
            "bad_parent": bad_parent,
            "listing": listing["body"],
            "by_ancestor": by_ancestor["body"],
            "no_match": no_match["body"],
            "reparent": reparent.json(),
            "renamed": renamed.json(),
            "stale": stale.json(),
            "missing": missing.json(),
            "coverage": coverage["body"],
            "datasets": datasets["body"],
            "assets": assets,
        }

    result = api_env.scenario(scenario)
    assert result["city"]["status"] == 201
    assert result["city"]["body"]["contract_version"] == CONTRACT_VERSION
    assert result["city"]["body"]["kind"] == "city"
    assert result["city"]["body"]["aliases"][0]["normalized"] == "hangzhou"
    assert set(result["city"]["body"]) == {
        "contract_version",
        "place_id",
        "kind",
        "canonical_name",
        "aliases",
        "parent_place_id",
        "origin",
        "external_refs",
        "lifecycle",
        "revision",
        "created_at",
        "updated_at",
    }
    assert result["lake"]["body"]["parent_place_id"] == result["city"]["body"]["place_id"]
    # A country comes from a validated dataset, not from a manual create.
    assert result["country"]["status"] == 422
    assert result["country"]["body"]["code"] == "VALIDATION_ERROR"
    # An illegal parent type is the frozen PLACE_PARENT_CYCLE case.
    assert result["bad_parent"]["body"]["code"] == "PLACE_PARENT_CYCLE"
    assert [item["place_id"] for item in result["listing"]["places"]] == [
        result["city"]["body"]["place_id"]
    ]
    assert result["no_match"]["places"] == [] and result["no_match"]["has_more"] is False
    assert [item["place_id"] for item in result["by_ancestor"]["places"]] == [
        result["city"]["body"]["place_id"],
        result["lake"]["body"]["place_id"],
    ]
    assert result["reparent"]["code"] == "VALIDATION_ERROR"
    assert "re-parent" in result["reparent"]["message"]
    assert result["renamed"]["canonical_name"] == "杭州市"
    assert result["renamed"]["revision"] == 2
    assert result["stale"]["code"] == "PLACE_ASSIGNMENT_CONFLICT"
    assert result["stale"]["details"]["current_revision"] == 2
    assert result["missing"]["code"] == "PLACE_NOT_FOUND"
    assert result["coverage"]["assets_total"] == len(result["assets"])
    assert result["coverage"]["resolver_available"] is False
    assert result["coverage"]["place_resolved"] == 0
    assert result["datasets"]["state"] == "unavailable"
    assert result["datasets"]["datasets"] == []
    # No local path may appear in a directory response.
    assert str(api_env.library) not in json.dumps(result["listing"], ensure_ascii=False)


def test_assignment_batch_and_undo_over_the_api(api_env) -> None:
    assets = prepare(api_env)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await api_env.authenticate(client)
        city = (
            await client.post(
                "/api/v1/places",
                json={"kind": "city", "canonical_name": "杭州"},
                headers=api_env.post_headers(csrf),
            )
        ).json()
        place_id = city["place_id"]
        assign = await client.put(
            f"/api/v1/assets/{assets[0]}/place-assignment",
            json={
                "action": "assign",
                "target_place_id": place_id,
                "request_id": "api-assign-1",
                "expected_revision": 0,
            },
            headers=api_env.post_headers(csrf),
        )
        replay = await client.put(
            f"/api/v1/assets/{assets[0]}/place-assignment",
            json={
                "action": "assign",
                "target_place_id": place_id,
                "request_id": "api-assign-1",
                "expected_revision": 0,
            },
            headers=api_env.post_headers(csrf),
        )
        idempotent_conflict = await client.put(
            f"/api/v1/assets/{assets[0]}/place-assignment",
            json={
                "action": "unassign",
                "request_id": "api-assign-1",
                "expected_revision": 1,
            },
            headers=api_env.post_headers(csrf),
        )
        stale = await client.put(
            f"/api/v1/assets/{assets[1]}/place-assignment",
            json={
                "action": "assign",
                "target_place_id": place_id,
                "request_id": "api-assign-2",
                "expected_revision": 5,
            },
            headers=api_env.post_headers(csrf),
        )
        unknown_place = await client.put(
            f"/api/v1/assets/{assets[1]}/place-assignment",
            json={
                "action": "assign",
                "target_place_id": 999999,
                "request_id": "api-assign-3",
                "expected_revision": 0,
            },
            headers=api_env.post_headers(csrf),
        )
        unknown_asset = await client.put(
            "/api/v1/assets/999999/place-assignment",
            json={
                "action": "assign",
                "target_place_id": place_id,
                "request_id": "api-assign-4",
                "expected_revision": 0,
            },
            headers=api_env.post_headers(csrf),
        )
        batch = await client.post(
            "/api/v1/place-assignments/batch",
            json={
                "request_id": "api-batch-1",
                "items": [
                    {
                        "asset_id": asset_id,
                        "action": "assign",
                        "target_place_id": place_id,
                        "expected_revision": 1 if asset_id == assets[0] else 0,
                    }
                    for asset_id in assets
                ],
            },
            headers=api_env.post_headers(csrf),
        )
        too_large = await client.post(
            "/api/v1/place-assignments/batch",
            json={
                "request_id": "api-batch-2",
                "items": [
                    {
                        "asset_id": index + 1,
                        "action": "assign",
                        "target_place_id": place_id,
                        "expected_revision": 0,
                    }
                    for index in range(501)
                ],
            },
            headers=api_env.post_headers(csrf),
        )
        body = batch.json()
        undo = await client.post(
            f"/api/v1/place-operations/{body['operation_id']}/undo",
            json={
                "request_id": "api-undo-1",
                "expected_operation_revision": body["operation_revision"],
            },
            headers=api_env.post_headers(csrf),
        )
        operations = await get_json(client, "/api/v1/place-operations")
        asset_place = await get_json(client, f"/api/v1/assets/{assets[0]}/place")
        return {
            "assign": assign.json(),
            "replay": replay.json(),
            "idempotent_conflict": idempotent_conflict.json(),
            "stale": stale.json(),
            "unknown_place": unknown_place.json(),
            "unknown_asset": unknown_asset.json(),
            "batch": body,
            "too_large": too_large.json(),
            "undo": undo.json(),
            "operations": operations["body"],
            "asset_place": asset_place["body"],
            "place_id": place_id,
        }

    result = api_env.scenario(scenario)
    assert result["assign"]["items"][0]["assignment"]["mode"] == "MANUAL_ASSIGNED"
    assert result["assign"]["changed"] is True and result["assign"]["replayed"] is False
    assert result["replay"]["replayed"] is True
    assert result["replay"]["operation_id"] == result["assign"]["operation_id"]
    assert result["idempotent_conflict"]["code"] == "PLACE_IDEMPOTENCY_CONFLICT"
    assert result["stale"]["code"] == "PLACE_ASSIGNMENT_CONFLICT"
    assert set(result["stale"]["details"]) <= SAFE_DETAIL_KEYS
    assert result["stale"]["details"]["expected_revision"] == 5
    assert result["stale"]["details"]["current_revision"] == 0
    assert result["unknown_place"]["code"] == "PLACE_NOT_FOUND"
    assert result["unknown_asset"]["code"] == "PLACE_NOT_FOUND"
    assert result["batch"]["operation_type"] == "BATCH_ASSIGN"
    assert len(result["batch"]["items"]) == len(assets)
    assert result["too_large"]["code"] == "PLACE_BATCH_TOO_LARGE"
    assert result["too_large"]["details"] == {"max_batch_size": 500}
    assert result["undo"]["operation_type"] == "UNDO"
    assert result["undo"]["undo_of_operation_id"] == result["batch"]["operation_id"]
    assert [item["assignment"]["mode"] for item in result["undo"]["items"]] == [
        "MANUAL_ASSIGNED",
        "AUTO_ALLOWED",
        "AUTO_ALLOWED",
    ]
    assert [item["operation_type"] for item in result["operations"]["operations"]] == [
        "ASSIGN",
        "BATCH_ASSIGN",
        "UNDO",
    ]
    assert result["asset_place"]["assignment"]["mode"] == "MANUAL_ASSIGNED"
    assert result["asset_place"]["place"]["place_id"] == result["place_id"]
    assert result["asset_place"]["gps_state"] == "NOT_EXTRACTED"
    assert result["asset_place"]["suggested"]["state"] == "unavailable"
    assert result["asset_place"]["suggested"]["candidates"] == []
    assert result["asset_place"]["exclusions"] == []


def test_asset_place_filter_and_coordinate_privacy(api_env) -> None:
    assets = prepare(api_env)
    seed_geo_fact(api_env, assets[0])

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await api_env.authenticate(client)
        city = (
            await client.post(
                "/api/v1/places",
                json={"kind": "city", "canonical_name": "杭州"},
                headers=api_env.post_headers(csrf),
            )
        ).json()
        lake = (
            await client.post(
                "/api/v1/places",
                json={
                    "kind": "attraction",
                    "canonical_name": "西湖",
                    "parent_place_id": city["place_id"],
                },
                headers=api_env.post_headers(csrf),
            )
        ).json()
        await client.put(
            f"/api/v1/assets/{assets[0]}/place-assignment",
            json={
                "action": "assign",
                "target_place_id": lake["place_id"],
                "request_id": "filter-1",
                "expected_revision": 0,
            },
            headers=api_env.post_headers(csrf),
        )
        ancestor_page = await get_json(
            client, "/api/v1/assets", params={"place_ids": city["place_id"]}
        )
        exact_page = await get_json(
            client,
            "/api/v1/assets",
            params={"place_ids": city["place_id"], "include_descendants": "false"},
        )
        unassigned = await get_json(
            client, "/api/v1/assets", params={"place_state": "unassigned"}
        )
        manual = await get_json(
            client, "/api/v1/assets", params={"place_state": "manual_unassigned"}
        )
        suggested = await get_json(client, "/api/v1/assets", params={"place_state": "suggested"})
        invalid = await get_json(
            client,
            "/api/v1/assets",
            params={"place_ids": city["place_id"], "place_state": "manual_unassigned"},
        )
        geo = await get_json(client, f"/api/v1/assets/{assets[0]}/geo")
        missing_geo = await get_json(client, f"/api/v1/assets/{assets[1]}/geo")
        detail = await get_json(client, f"/api/v1/assets/{assets[0]}")
        return {
            "ancestor_page": ancestor_page["body"],
            "exact_page": exact_page["body"],
            "unassigned": unassigned["body"],
            "manual": manual["body"],
            "suggested": suggested["body"],
            "invalid": invalid["body"],
            "geo": geo,
            "missing_geo": missing_geo,
            "detail": detail["body"],
            "assets": assets,
            "city_id": city["place_id"],
            "lake_id": lake["place_id"],
        }

    result = api_env.scenario(scenario)
    assert [item["asset_id"] for item in result["ancestor_page"]["assets"]] == [result["assets"][0]]
    assert result["ancestor_page"]["place_ids"] == [result["city_id"]]
    assert result["ancestor_page"]["include_descendants"] is True
    assert result["ancestor_page"]["place_state"] == "any"
    assert result["ancestor_page"]["catalog_filter_revision"] >= 1
    assert result["exact_page"]["assets"] == []
    assert {item["asset_id"] for item in result["unassigned"]["assets"]} == set(result["assets"][1:])
    assert result["manual"]["assets"] == []
    assert result["suggested"]["assets"] == []
    assert result["invalid"]["code"] == "INVALID_PLACE_FILTER"

    # Exact coordinates only ever leave the session-protected /geo endpoint.
    assert result["geo"]["status"] == 200
    fact = result["geo"]["body"]
    assert fact["status"] == "VALID" and fact["datum_status"] == "WGS84"
    assert fact["normalized_wgs84"]["latitude"] == 30.25
    assert fact["privacy"] == {
        "detail_endpoint_only": True,
        "loggable": False,
        "url_serializable": False,
        "feedback_serializable": False,
    }
    assert result["missing_geo"]["status"] == 404
    assert result["missing_geo"]["body"]["code"] == "PLACE_NOT_FOUND"
    assert set(result["missing_geo"]["body"]["details"]) <= SAFE_DETAIL_KEYS

    rendered_detail = json.dumps(result["detail"], ensure_ascii=False)
    assert "30.25" not in rendered_detail and "120.16" not in rendered_detail
    assert "latitude" not in rendered_detail and "longitude" not in rendered_detail
    assert str(api_env.library) not in rendered_detail
    # The general asset row exposes only the coarse state.
    assert result["detail"]["media"]
    rendered_pages = json.dumps(
        [result["ancestor_page"], result["unassigned"]], ensure_ascii=False
    )
    assert "30.25" not in rendered_pages


def test_place_error_codes_and_diagnostics_never_leak_coordinates(api_env) -> None:
    assets = prepare(api_env, photos=1)
    seed_geo_fact(api_env, assets[0], latitude=-33.8688, longitude=151.2093)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        await api_env.authenticate(client)
        geo = await get_json(client, f"/api/v1/assets/{assets[0]}/geo")
        catalog = await get_json(client, "/api/v1/capabilities")
        stats = await get_json(client, "/api/v1/stats")
        # A rejected filter must not echo the coordinates either.
        bad = await get_json(
            client, "/api/v1/assets", params={"place_state": "not-a-state"}
        )
        return {
            "geo": geo["body"],
            "capabilities": catalog["body"],
            "stats": stats["body"],
            "bad": bad["body"],
        }

    result = api_env.scenario(scenario)
    assert result["geo"]["normalized_wgs84"]["latitude"] == -33.8688
    for key in ("capabilities", "stats", "bad"):
        text = json.dumps(result[key], ensure_ascii=False)
        assert "-33.8688" not in text and "151.2093" not in text, key
        assert "latitude" not in text and "longitude" not in text, key
        assert str(api_env.library) not in text, key
    # The read-only diagnostics keep their previous shape: no place payload is added.
    assert "gps" not in json.dumps(result["capabilities"], ensure_ascii=False).lower()
