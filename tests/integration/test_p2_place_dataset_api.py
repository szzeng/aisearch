"""P2-03B: the dataset and suggestion surface of the local API.

The catalog, the synthetic package and the synthetic GPS JPEGs all live in ``%TEMP%``.
The session/Host/Origin/CSRF boundary and the "coordinates only on ``/geo``" rule are
asserted here as well (ADR-0005 §3/§9).
"""

from __future__ import annotations

import json
from typing import Any

from httpx import AsyncClient
from p2_dataset_fixtures import (
    HANGZHOU_POSITION,
    PY_PROVIDER,
    build_package,
    python_capability,
    write_gps_jpeg,
)

from photo_search.geo import extract as geo_extract
from photo_search.geo import resolver as resolver_module
from photo_search.jobs import queue as jobs
from photo_search.places import datasets as datasets_module
from photo_search.places import revision as revision_module
from photo_search.scheduler import engine


async def get_json(client: AsyncClient, url: str, **kwargs: Any) -> dict[str, Any]:
    response = await client.get(url, **kwargs)
    assert response.status_code == 200, response.text
    return {"status": response.status_code, "body": response.json()}


def install_package(api_env, name: str = "geo-mini-cn-1", **overrides: Any) -> dict[str, Any]:
    source = api_env.tmp_path / "package-sources" / name
    build_package(source, **overrides)
    staged = datasets_module.stage_package(api_env.service.catalog, source)
    validated = datasets_module.validate_dataset(
        api_env.service.catalog, str(staged["dataset_id"])
    )
    return datasets_module.activate_dataset(
        api_env.service.catalog, str(validated["dataset_id"])
    )


def build_scheduler(api_env) -> engine.Scheduler:
    specs = [geo_extract.gps_processor_spec(), resolver_module.resolver_processor_spec()]
    scheduler = engine.Scheduler(api_env.service.catalog, specs, lease_seconds=60)
    scheduler.record_capability(python_capability(), now_ms=1_000)
    for spec in specs:
        scheduler.record_qualification(
            processor_id=spec.processor_id,
            spec_revision=spec.spec_revision,
            device="cpu",
            precision=spec.supported_precisions[0],
            provider_name=PY_PROVIDER,
            runtime_package_id=spec.runtime_package_id,
            now_ms=1_000,
        )
    return scheduler


def prepare_asset(api_env, name: str = "hangzhou.jpg") -> int:
    """Scan one synthetic GPS JPEG, then run the real GPS and resolver jobs on it."""
    write_gps_jpeg(api_env.library / name, position=HANGZHOU_POSITION)
    source = api_env.register_source("library")
    api_env.scan(int(source["source_id"]))
    row = api_env.service.catalog.read_one("SELECT asset_id, sha256 FROM assets")
    asset_id = int(row["asset_id"])
    scheduler = build_scheduler(api_env)
    clock = api_env.clock

    def enqueue(task_type: str, processor_id: str) -> None:
        with api_env.service.catalog.write() as connection:
            jobs.enqueue(
                connection,
                subject_type="asset",
                subject_id=str(asset_id),
                input_revision=str(row["sha256"]),
                task_type=task_type,
                processor_id=processor_id,
                now_ms=1_000,
            )

    # One task type at a time: a scheduler drains every runnable job it can see, so the GPS
    # execution must not be offered the resolver job (and vice versa).
    enqueue(geo_extract.GPS_TASK_TYPE, geo_extract.GPS_PROCESSOR_ID)
    scheduler.run_until_idle(
        geo_extract.gps_executor(api_env.service.catalog, clock=clock),
        worker_id="w1",
        now_ms=1_100,
    )
    enqueue(resolver_module.RESOLVER_TASK_TYPE, resolver_module.RESOLVER_PROCESSOR_ID)
    scheduler.run_until_idle(
        resolver_module.resolver_executor(
            api_env.service.catalog, data_root=api_env.data_root, clock=clock
        ),
        worker_id="w1",
        now_ms=1_200,
    )
    return asset_id


def place_id_of(api_env, external_id: str) -> int:
    row = api_env.service.catalog.read_one(
        "SELECT place_id FROM place_external_refs WHERE external_id = ?", (external_id,)
    )
    return int(row["place_id"])


def test_datasets_endpoint_reports_no_package_honestly(api_env) -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        await api_env.authenticate(client)
        return await get_json(client, "/api/v1/places/datasets")

    result = api_env.scenario(scenario)
    body = result["body"]

    assert body["contract_version"] == "p2-place-contracts.v1"
    assert body["state"] == "unavailable"
    assert body["reason"] == "PLACE_DATASET_NOT_INSTALLED"
    assert body["resolver_available"] is False
    assert body["active_dataset"] is None
    assert body["datasets"] == []
    assert body["suggestion_revision"] == 0


def test_dataset_suggestion_and_coverage_endpoints(api_env) -> None:
    install_package(api_env)
    asset_id = prepare_asset(api_env)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        await api_env.authenticate(client)
        datasets = await get_json(client, "/api/v1/places/datasets")
        place = await get_json(client, f"/api/v1/assets/{asset_id}/place")
        geo = await get_json(client, f"/api/v1/assets/{asset_id}/geo")
        suggested = await get_json(
            client,
            "/api/v1/assets",
            params={
                "place_state": "suggested",
                "suggestion_revision": place["body"]["suggestion_revision"],
            },
        )
        stale = await client.get(
            "/api/v1/assets",
            params={"place_state": "suggested", "suggestion_revision": 999},
        )
        coverage = await get_json(client, "/api/v1/places/coverage")
        places = await get_json(client, "/api/v1/places")
        return {
            "datasets": datasets["body"],
            "place": place["body"],
            "geo": geo["body"],
            "suggested": suggested["body"],
            "stale": stale,
            "coverage": coverage["body"],
            "places": places["body"],
        }

    result = api_env.scenario(scenario)

    datasets = result["datasets"]
    assert datasets["state"] == "active"
    assert datasets["resolver_available"] is True
    assert datasets["active_dataset"]["dataset_id"] == "geo-mini-cn-1"
    assert datasets["active_dataset"]["state"] == "ACTIVE"
    assert datasets["active_dataset"]["coverage"]["record_count"] == 5
    assert datasets["active_dataset"]["problems"] == []
    assert len(datasets["datasets"]) == 1
    assert str(api_env.data_root) not in json.dumps(datasets, ensure_ascii=False)

    place = result["place"]
    assert place["asset_id"] == asset_id
    assert place["assignment"] is None
    assert place["suggested"]["state"] == "candidates"
    assert place["suggested"]["requires_confirmation"] is True
    assert place["suggested"]["assumed_datum"] is False
    assert [item["display_name"] for item in place["suggested"]["candidates"]] == [
        "杭州",
        "苏州",
        "上海",
    ]
    assert all(
        item["disposition"] == "SUGGESTION_ONLY" for item in place["suggested"]["candidates"]
    )
    assert place["gps_state"] == "VALID"
    # Coordinates never leave the session-protected /geo endpoint.
    text = json.dumps(place, ensure_ascii=False)
    assert "latitude" not in text and "longitude" not in text
    assert str(api_env.library) not in text
    assert result["geo"]["normalized_wgs84"]["latitude"] == 30.2741

    suggested = result["suggested"]
    assert suggested["place_state"] == "suggested"
    assert suggested["suggestion_revision"] == place["suggestion_revision"]
    assert [item["asset_id"] for item in suggested["assets"]] == [asset_id]
    assert result["stale"].status_code == 409
    assert result["stale"].json()["code"] == "PLACE_SUGGESTION_REVISION_STALE"
    assert result["stale"].json()["details"]["current_revision"] == place["suggestion_revision"]

    coverage = result["coverage"]
    assert coverage["resolver_available"] is True
    assert coverage["place_resolved"] == 1
    assert coverage["places_suggested"] == 1
    assert coverage["places_assigned"] == 0
    assert {item["canonical_name"] for item in result["places"]["places"]} == {
        "中国",
        "杭州",
        "上海",
        "苏州",
        "拉萨",
    }


def test_confirming_a_suggestion_over_the_api_and_the_suggested_filter(api_env) -> None:
    install_package(api_env)
    asset_id = prepare_asset(api_env)
    hangzhou = place_id_of(api_env, "city-hangzhou")

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await api_env.authenticate(client)
        confirmed = await client.put(
            f"/api/v1/assets/{asset_id}/place-assignment",
            json={
                "action": "assign",
                "target_place_id": hangzhou,
                "request_id": "confirm-1",
                "expected_revision": 0,
                "origin": "CONFIRMED_SUGGESTION",
            },
            headers=api_env.post_headers(csrf),
        )
        place = await get_json(client, f"/api/v1/assets/{asset_id}/place")
        suggested = await get_json(
            client, "/api/v1/assets", params={"place_state": "suggested"}
        )
        assigned = await get_json(client, "/api/v1/assets", params={"place_state": "assigned"})
        invalid_origin = await client.put(
            f"/api/v1/assets/{asset_id}/place-assignment",
            json={
                "action": "assign",
                "target_place_id": hangzhou,
                "request_id": "confirm-2",
                "expected_revision": 1,
                "origin": "SOMETHING_ELSE",
            },
            headers=api_env.post_headers(csrf),
        )
        return {
            "confirmed": confirmed,
            "place": place["body"],
            "suggested": suggested["body"],
            "assigned": assigned["body"],
            "invalid_origin": invalid_origin,
        }

    result = api_env.scenario(scenario)

    assert result["confirmed"].status_code == 200, result["confirmed"].text
    body = result["confirmed"].json()
    assert body["items"][0]["assignment"]["origin"] == "CONFIRMED_SUGGESTION"
    assert body["items"][0]["assignment"]["manual_lock"] is True
    assert result["place"]["assignment"]["mode"] == "MANUAL_ASSIGNED"
    assert result["place"]["place"]["canonical_name"] == "杭州"
    assert result["place"]["suggested"]["state"] == "no_candidate"
    assert result["suggested"]["assets"] == []
    assert [item["asset_id"] for item in result["assigned"]["assets"]] == [asset_id]
    assert result["invalid_origin"].status_code == 422


def test_the_dataset_and_suggestion_endpoints_require_a_session(api_env) -> None:
    install_package(api_env)
    asset_id = prepare_asset(api_env)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        datasets = await client.get("/api/v1/places/datasets")
        place = await client.get(f"/api/v1/assets/{asset_id}/place")
        listed = await client.get("/api/v1/assets", params={"place_state": "suggested"})
        write = await client.put(
            f"/api/v1/assets/{asset_id}/place-assignment",
            json={
                "action": "assign",
                "target_place_id": 1,
                "request_id": "x",
                "expected_revision": 0,
            },
        )
        return {"datasets": datasets, "place": place, "listed": listed, "write": write}

    result = api_env.scenario(scenario)

    for response in result.values():
        assert response.status_code == 401, response.text
        assert response.json()["code"] == "SESSION_REQUIRED"


def test_a_cross_origin_suggestion_write_is_refused(api_env) -> None:
    install_package(api_env)
    asset_id = prepare_asset(api_env)
    hangzhou = place_id_of(api_env, "city-hangzhou")

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await api_env.authenticate(client)
        wrong_origin = await client.put(
            f"/api/v1/assets/{asset_id}/place-assignment",
            json={
                "action": "assign",
                "target_place_id": hangzhou,
                "request_id": "cross-1",
                "expected_revision": 0,
            },
            headers=api_env.post_headers(csrf, origin="http://evil.invalid"),
        )
        no_csrf = await client.put(
            f"/api/v1/assets/{asset_id}/place-assignment",
            json={
                "action": "assign",
                "target_place_id": hangzhou,
                "request_id": "cross-2",
                "expected_revision": 0,
            },
            headers={"Origin": "http://127.0.0.1"},
        )
        return {"wrong_origin": wrong_origin, "no_csrf": no_csrf}

    result = api_env.scenario(scenario)

    assert result["wrong_origin"].status_code == 403
    assert result["wrong_origin"].json()["code"] == "ORIGIN_NOT_ALLOWED"
    assert result["no_csrf"].status_code == 403
    assert result["no_csrf"].json()["code"] == "CSRF_INVALID"
    assert api_env.service.catalog.read_one(
        "SELECT COUNT(*) AS value FROM asset_place_assignments"
    )["value"] == 0


def test_a_broken_active_package_is_reported_but_manual_work_continues(api_env) -> None:
    install_package(api_env)
    asset_id = prepare_asset(api_env)
    (datasets_module.dataset_dir(api_env.data_root, "geo-mini-cn-1") / "places.jsonl").unlink()

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await api_env.authenticate(client)
        datasets = await get_json(client, "/api/v1/places/datasets")
        place = await get_json(client, f"/api/v1/assets/{asset_id}/place")
        created = await client.post(
            "/api/v1/places",
            json={"kind": "custom", "canonical_name": "外婆家"},
            headers=api_env.post_headers(csrf),
        )
        return {"datasets": datasets["body"], "place": place["body"], "created": created}

    result = api_env.scenario(scenario)

    assert result["datasets"]["state"] == "missing"
    assert result["datasets"]["reason"] == "PLACE_DATASET_MISSING"
    assert result["datasets"]["resolver_available"] is False
    assert result["created"].status_code == 201
    # Existing machine products stay readable; a *new* resolution would block instead.
    assert result["place"]["suggested"]["state"] == "candidates"


def test_suggestion_revision_is_independent_of_the_filter_revision(api_env) -> None:
    install_package(api_env)
    asset_id = prepare_asset(api_env)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        await api_env.authenticate(client)
        return await get_json(client, f"/api/v1/assets/{asset_id}/place")

    body = api_env.scenario(scenario)["body"]

    assert body["suggestion_revision"] >= 1
    assert body["catalog_filter_revision"] == 0
    connection = api_env.service.catalog.connection
    assert revision_module.read_filter_revision(connection) == 0
    assert revision_module.read_suggestion_revision(connection) == body["suggestion_revision"]
