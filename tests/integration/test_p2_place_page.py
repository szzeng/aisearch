"""P2-03C: the requests the static place page makes, driven through the real local API.

This file does not test a DOM (the repository deliberately ships no Node/npm chain). It
asserts the *page contract*: every call the page issues, in the shape the page issues it,
against the frozen ADR-0005 surface. The page document itself is asserted in
``tests/unit/test_api_frontend.py`` and its interactive behaviour is recorded in
``docs/diagnostics/frontend-smoke-checklist.md``.

Everything lives under ``%TEMP%``; no real photo library, no network, no real dataset.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from p2_dataset_fixtures import (
    BETWEEN_POSITION,
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

#: Coordinate spellings the page must never receive outside the session-protected /geo.
COORDINATE_KEYS = ("latitude", "longitude", "normalized_wgs84", "raw_gps", "coordinate")


async def create_place(
    client: AsyncClient,
    env: Any,
    *,
    kind: str,
    name: str,
    parent: int | None = None,
    aliases: list[dict[str, Any]] | None = None,
    expected: int = 201,
) -> Any:
    """``POST /api/v1/places`` exactly as the create form sends it."""
    response = await client.post(
        "/api/v1/places",
        json={
            "kind": kind,
            "canonical_name": name,
            "parent_place_id": parent,
            "aliases": aliases or [],
        },
        headers=env.post_headers(env.csrf_token),
    )
    if expected == 201:
        assert response.status_code == 201, response.text
        return response.json()
    assert response.status_code == expected, response.text
    return response


def revision_snapshot(env: Any) -> tuple[int, int]:
    connection = env.service.catalog.connection
    return (
        revision_module.read_filter_revision(connection),
        revision_module.read_suggestion_revision(connection),
    )


def install_package(env: Any, name: str = "geo-mini-cn-1", **overrides: Any) -> dict[str, Any]:
    source = env.tmp_path / "package-sources" / name
    build_package(source, **overrides)
    staged = datasets_module.stage_package(env.service.catalog, source)
    validated = datasets_module.validate_dataset(env.service.catalog, str(staged["dataset_id"]))
    return datasets_module.activate_dataset(env.service.catalog, str(validated["dataset_id"]))


def build_scheduler(env: Any) -> engine.Scheduler:
    specs = [geo_extract.gps_processor_spec(), resolver_module.resolver_processor_spec()]
    scheduler = engine.Scheduler(env.service.catalog, specs, lease_seconds=60)
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


def prepare_gps_assets(env: Any, positions: dict[str, tuple[float, float]]) -> list[int]:
    """Scan synthetic GPS JPEGs and run the real GPS + resolver jobs over all of them."""
    for name, position in positions.items():
        write_gps_jpeg(env.library / name, position=position)
    source = env.register_source("library")
    env.scan(int(source["source_id"]))
    rows = env.service.catalog.connection.execute(
        "SELECT a.asset_id AS asset_id, a.sha256 AS sha256 FROM assets AS a "
        "JOIN file_locations AS l ON l.asset_id = a.asset_id ORDER BY l.relative_path"
    ).fetchall()
    assets = [(int(row["asset_id"]), str(row["sha256"])) for row in rows]
    scheduler = build_scheduler(env)

    def enqueue(task_type: str, processor_id: str, asset_id: int, sha: str) -> None:
        with env.service.catalog.write() as connection:
            jobs.enqueue(
                connection,
                subject_type="asset",
                subject_id=str(asset_id),
                input_revision=sha,
                task_type=task_type,
                processor_id=processor_id,
                now_ms=1_000,
            )

    # One task type at a time: the scheduler drains every runnable job it can see, so the
    # GPS execution must not be offered the resolver jobs (and the other way round).
    for asset_id, sha in assets:
        enqueue(geo_extract.GPS_TASK_TYPE, geo_extract.GPS_PROCESSOR_ID, asset_id, sha)
    scheduler.run_until_idle(
        geo_extract.gps_executor(env.service.catalog, clock=env.clock),
        worker_id="w1",
        now_ms=1_100,
    )
    for asset_id, sha in assets:
        enqueue(
            resolver_module.RESOLVER_TASK_TYPE,
            resolver_module.RESOLVER_PROCESSOR_ID,
            asset_id,
            sha,
        )
    scheduler.run_until_idle(
        resolver_module.resolver_executor(
            env.service.catalog, data_root=env.data_root, clock=env.clock
        ),
        worker_id="w1",
        now_ms=1_200,
    )
    return [asset_id for asset_id, _sha in assets]


def collect_keys(payload: Any) -> list[str]:
    """Every key in one JSON document, so a forbidden field can be located by name."""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.append(str(key))
            found.extend(collect_keys(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(collect_keys(item))
    return found


def assert_no_coordinates(payload: Any) -> None:
    keys = collect_keys(payload)
    for key in keys:
        folded = key.casefold()
        for needle in COORDINATE_KEYS:
            assert needle not in folded, f"coordinate field {key} leaked into a page response"
    rendered = repr(payload)
    for spelling in ("C:\\", "D:\\", "\\\\?\\"):
        assert spelling not in rendered, "an absolute local path leaked into a page response"


def test_the_directory_cursor_is_bound_to_the_directory_revision(api_env) -> None:
    """The page's "加载更多" must be refused instead of mixing two directory versions."""

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        for name in ("甲城", "乙城", "丙城"):
            await create_place(client, api_env, kind="city", name=name)
        first = await client.get("/api/v1/places", params={"limit": 2})
        cursor = first.json()["next_cursor"]
        second = await client.get("/api/v1/places", params={"limit": 2, "cursor": cursor})
        # A rename is an authoritative, searchable change (ADR-0005 §11: PLACE_NAME_CHANGED),
        # so it moves the directory revision that the cursor was minted under.
        pages = await client.get("/api/v1/places", params={"limit": 200})
        last = pages.json()["places"][-1]
        await client.patch(
            f"/api/v1/places/{last['place_id']}",
            json={"canonical_name": "丙城新名", "expected_revision": last["revision"]},
            headers=api_env.post_headers(api_env.csrf_token),
        )
        stale = await client.get("/api/v1/places", params={"limit": 2, "cursor": cursor})
        return {"first": first, "second": second, "stale": stale}

    result = api_env.scenario(scenario)
    first = result["first"]
    assert first.status_code == 200, first.text
    body = first.json()
    assert len(body["places"]) == 2
    assert body["has_more"] is True
    assert body["next_cursor"]

    assert result["second"].status_code == 200, result["second"].text
    assert len(result["second"].json()["places"]) == 1

    stale = result["stale"]
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "CATALOG_FILTER_REVISION_STALE"
    assert stale.json()["details"]["current_revision"] == body["catalog_filter_revision"] + 1


def test_one_place_can_be_read_back_through_its_ancestor_filter(api_env) -> None:
    """``?ancestor=<id>&limit=1`` is what the page uses to paint a full ancestor chain."""

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        city = await create_place(client, api_env, kind="city", name="杭州")
        spot = await create_place(
            client, api_env, kind="attraction", name="断桥", parent=city["place_id"]
        )
        child = await create_place(
            client, api_env, kind="custom", name="断桥北岸", parent=spot["place_id"]
        )
        own = await client.get(
            "/api/v1/places", params={"ancestor": city["place_id"], "limit": 1}
        )
        subtree = await client.get(
            "/api/v1/places", params={"ancestor": spot["place_id"], "limit": 50}
        )
        unknown = await client.get("/api/v1/places", params={"ancestor": 99999, "limit": 1})
        return {"city": city, "spot": spot, "child": child, "own": own, "subtree": subtree,
                "unknown": unknown}

    result = api_env.scenario(scenario)
    city, spot, child = result["city"], result["spot"], result["child"]

    own = result["own"]
    assert own.status_code == 200, own.text
    rows = own.json()["places"]
    assert len(rows) == 1
    assert rows[0]["place_id"] == city["place_id"]
    assert rows[0]["canonical_name"] == "杭州"
    assert rows[0]["parent_place_id"] is None
    assert rows[0]["revision"] >= 1

    subtree = result["subtree"]
    assert subtree.status_code == 200, subtree.text
    ids = {row["place_id"] for row in subtree.json()["places"]}
    assert ids == {spot["place_id"], child["place_id"]}

    unknown = result["unknown"]
    assert unknown.status_code == 200, unknown.text
    assert unknown.json()["places"] == []


def test_rename_needs_the_current_revision_and_reparent_is_refused(api_env) -> None:
    """The edit form sends ``expected_revision``; the server refuses a re-parent."""

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        city = await create_place(
            client,
            api_env,
            kind="city",
            name="杭州",
            aliases=[{"text": "Hangzhou", "language": "en"}],
        )
        headers = api_env.post_headers(api_env.csrf_token)
        renamed = await client.patch(
            f"/api/v1/places/{city['place_id']}",
            json={"canonical_name": "杭州市", "expected_revision": city["revision"]},
            headers=headers,
        )
        stale = await client.patch(
            f"/api/v1/places/{city['place_id']}",
            json={"canonical_name": "旧名", "expected_revision": city["revision"]},
            headers=headers,
        )
        reparent = await client.patch(
            f"/api/v1/places/{city['place_id']}",
            json={
                "parent_place_id": city["place_id"],
                "expected_revision": renamed.json()["revision"],
            },
            headers=headers,
        )
        country = await client.post(
            "/api/v1/places",
            json={"kind": "country", "canonical_name": "中国"},
            headers=headers,
        )
        return {"city": city, "renamed": renamed, "stale": stale, "reparent": reparent,
                "country": country}

    result = api_env.scenario(scenario)
    city = result["city"]
    renamed = result["renamed"]
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["canonical_name"] == "杭州市"
    assert renamed.json()["revision"] == city["revision"] + 1
    # A rename never drops the alias set the user had.
    assert [alias["text"] for alias in renamed.json()["aliases"]] == ["Hangzhou"]

    stale = result["stale"]
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "PLACE_ASSIGNMENT_CONFLICT"
    assert stale.json()["details"]["current_revision"] == renamed.json()["revision"]
    assert stale.json()["details"]["expected_revision"] == city["revision"]

    reparent = result["reparent"]
    assert reparent.status_code == 422, reparent.text
    assert reparent.json()["code"] == "VALIDATION_ERROR"
    assert reparent.json()["details"]["field"] == "parent_place_id"

    country = result["country"]
    assert country.status_code == 422, country.text
    assert country.json()["code"] == "VALIDATION_ERROR"


def test_two_same_name_places_stay_distinct_with_their_own_chains(api_env) -> None:
    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        left = await create_place(client, api_env, kind="city", name="甲城")
        right = await create_place(client, api_env, kind="city", name="乙城")
        first = await create_place(
            client, api_env, kind="custom", name="同名地点", parent=left["place_id"]
        )
        second = await create_place(
            client, api_env, kind="custom", name="同名地点", parent=right["place_id"]
        )
        listed = await client.get("/api/v1/places", params={"name": "同名地点", "limit": 50})
        parents = await client.get(
            "/api/v1/places", params={"ancestor": left["place_id"], "limit": 50}
        )
        return {
            "first": first,
            "second": second,
            "listed": listed,
            "parents": parents,
            "left": left,
            "right": right,
        }

    result = api_env.scenario(scenario)
    listed = result["listed"]
    assert listed.status_code == 200, listed.text
    rows = listed.json()["places"]
    assert len(rows) == 2
    assert {row["canonical_name"] for row in rows} == {"同名地点"}
    assert len({row["place_id"] for row in rows}) == 2
    # No automatic merge and no name guessing: the parent id is the disambiguator.
    assert {row["parent_place_id"] for row in rows} == {
        result["left"]["place_id"],
        result["right"]["place_id"],
    }
    for row in rows:
        assert row["kind"] == "custom"
        assert row["lifecycle"] == "ACTIVE"

    parents = result["parents"]
    assert parents.status_code == 200, parents.text
    assert {row["place_id"] for row in parents.json()["places"]} == {
        result["left"]["place_id"],
        result["first"]["place_id"],
    }


async def confirm_suggestion(
    client: AsyncClient,
    env: Any,
    asset_id: int,
    *,
    target: int,
    expected: int,
    request_id: str | None = None,
) -> Any:
    """``PUT /assets/{id}/place-assignment`` exactly as the page's confirm button sends it."""
    return await client.put(
        f"/api/v1/assets/{asset_id}/place-assignment",
        json={
            "action": "assign",
            "target_place_id": target,
            "request_id": request_id or f"p2-03c-{asset_id}-{target}-{expected}",
            "expected_revision": expected,
            "origin": "CONFIRMED_SUGGESTION",
        },
        headers=env.post_headers(env.csrf_token),
    )


async def first_local_candidate(client: AsyncClient, asset_id: int) -> int:
    body = (await client.get(f"/api/v1/assets/{asset_id}/place")).json()
    for candidate in body["suggested"]["candidates"]:
        if candidate["local_place_id"] is not None:
            return int(candidate["local_place_id"])
    raise AssertionError("the fixture produced no locally mapped candidate")


async def current_assignment(client: AsyncClient, asset_id: int) -> tuple[int, str | None]:
    body = (await client.get(f"/api/v1/assets/{asset_id}/place")).json()
    assignment = body["assignment"]
    if assignment is None:
        return 0, None
    return int(assignment["revision"]), str(assignment["mode"])


async def undo_operation(
    client: AsyncClient, env: Any, operation: dict[str, Any], *, request_id: str
) -> Any:
    return await client.post(
        f"/api/v1/place-operations/{operation['operation_id']}/undo",
        json={
            "request_id": request_id,
            "expected_operation_revision": int(operation["operation_revision"]),
        },
        headers=env.post_headers(env.csrf_token),
    )


def test_the_suggested_page_binds_the_suggestion_revision(api_env) -> None:
    """A continuation computed under an older visible set is refused, never merged."""

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        install_package(api_env)
        assets = prepare_gps_assets(
            api_env, {"a.jpg": HANGZHOU_POSITION, "b.jpg": BETWEEN_POSITION}
        )
        first = await client.get(
            "/api/v1/assets",
            params={"place_state": "suggested", "order": "asset_id", "limit": 1},
        )
        revision = first.json()["suggestion_revision"]
        cursor = first.json()["next_cursor"]
        second = await client.get(
            "/api/v1/assets",
            params={
                "place_state": "suggested",
                "order": "asset_id",
                "limit": 1,
                "cursor": cursor,
                "suggestion_revision": revision,
            },
        )
        target = await first_local_candidate(client, assets[0])
        confirmed = await confirm_suggestion(
            client, api_env, assets[0], target=target, expected=0
        )
        stale = await client.get(
            "/api/v1/assets",
            params={
                "place_state": "suggested",
                "order": "asset_id",
                "limit": 1,
                "cursor": cursor,
                "suggestion_revision": revision,
            },
        )
        remaining = await client.get(
            "/api/v1/assets",
            params={"place_state": "suggested", "order": "asset_id", "limit": 50},
        )
        assignment = await client.get(f"/api/v1/assets/{assets[0]}/place")
        return {
            "first": first,
            "second": second,
            "confirmed": confirmed,
            "stale": stale,
            "remaining": remaining,
            "assignment": assignment,
            "revision": revision,
        }

    result = api_env.scenario(scenario)
    first = result["first"]
    assert first.status_code == 200, first.text
    assert first.json()["has_more"] is True
    assert first.json()["next_cursor"]
    assert result["revision"] >= 1

    second = result["second"]
    assert second.status_code == 200, second.text
    assert len(second.json()["assets"]) == 1
    # The page stores the revision the page was computed under.
    assert second.json()["suggestion_revision"] == result["revision"]

    confirmed = result["confirmed"]
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["changed"] is True
    assert confirmed.json()["replayed"] is False

    body = result["assignment"].json()
    assert body["place"] is not None
    assert body["assignment"]["mode"] == "MANUAL_ASSIGNED"
    assert body["assignment"]["origin"] == "CONFIRMED_SUGGESTION"
    assert body["assignment"]["manual_lock"] is True
    assert body["suggestion_revision"] > result["revision"]

    stale = result["stale"]
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "PLACE_SUGGESTION_REVISION_STALE"
    assert stale.json()["details"]["current_revision"] > result["revision"]

    # A confirmed asset leaves the suggested list; the other one stays.
    remaining = result["remaining"]
    assert remaining.status_code == 200, remaining.text
    assert remaining.json()["suggestion_revision"] == body["suggestion_revision"]
    assert len(remaining.json()["assets"]) == 1


def test_batch_confirm_is_atomic_bounded_and_replay_safe(api_env) -> None:
    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        install_package(api_env)
        assets = prepare_gps_assets(
            api_env, {"a.jpg": HANGZHOU_POSITION, "b.jpg": BETWEEN_POSITION}
        )
        target = await first_local_candidate(client, assets[0])
        headers = api_env.post_headers(api_env.csrf_token)
        items = [
            {
                "asset_id": asset_id,
                "action": "assign",
                "target_place_id": target,
                "expected_revision": 0,
            }
            for asset_id in assets
        ]
        before = revision_snapshot(api_env)
        batch = await client.post(
            "/api/v1/place-assignments/batch",
            json={"request_id": "p2-03c-batch-1", "items": items},
            headers=headers,
        )
        after = revision_snapshot(api_env)
        replay = await client.post(
            "/api/v1/place-assignments/batch",
            json={"request_id": "p2-03c-batch-1", "items": items},
            headers=headers,
        )
        replayed_state = revision_snapshot(api_env)
        conflict = await client.post(
            "/api/v1/place-assignments/batch",
            json={
                "request_id": "p2-03c-batch-2",
                "items": [
                    {
                        "asset_id": assets[1],
                        "action": "assign",
                        "target_place_id": target,
                        "expected_revision": 0,
                    }
                ],
            },
            headers=headers,
        )
        after_conflict = revision_snapshot(api_env)
        too_large = await client.post(
            "/api/v1/place-assignments/batch",
            json={
                "request_id": "p2-03c-batch-3",
                "items": [
                    {
                        "asset_id": 100000 + index,
                        "action": "unassign",
                        "target_place_id": None,
                        "expected_revision": 0,
                    }
                    for index in range(501)
                ],
            },
            headers=headers,
        )
        return {
            "before": before,
            "after": after,
            "batch": batch,
            "replay": replay,
            "replayed_state": replayed_state,
            "conflict": conflict,
            "after_conflict": after_conflict,
            "too_large": too_large,
        }

    result = api_env.scenario(scenario)
    batch = result["batch"]
    assert batch.status_code == 200, batch.text
    body = batch.json()
    assert body["operation_type"] == "BATCH_ASSIGN"
    assert body["changed"] is True
    assert body["replayed"] is False
    assert len(body["items"]) == 2
    # One atomic batch moves the authoritative revision once.
    assert result["after"][0] == result["before"][0] + 1

    replay = result["replay"]
    assert replay.status_code == 200, replay.text
    assert replay.json()["operation_id"] == body["operation_id"]
    assert replay.json()["replayed"] is True
    assert result["replayed_state"] == result["after"]

    conflict = result["conflict"]
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "PLACE_ASSIGNMENT_CONFLICT"
    assert conflict.json()["details"]["current_revision"] == 1
    assert result["after_conflict"] == result["after"]

    too_large = result["too_large"]
    assert too_large.status_code == 422, too_large.text
    assert too_large.json()["code"] == "PLACE_BATCH_TOO_LARGE"
    assert too_large.json()["details"]["max_batch_size"] == 500


def test_undo_compensates_and_refuses_after_a_later_edit(api_env) -> None:
    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        install_package(api_env)
        assets = prepare_gps_assets(api_env, {"a.jpg": HANGZHOU_POSITION})
        asset_id = assets[0]
        target = await first_local_candidate(client, asset_id)

        confirmed = await confirm_suggestion(client, api_env, asset_id, target=target, expected=0)
        assert confirmed.status_code == 200, confirmed.text
        first_operation = confirmed.json()
        undone = await undo_operation(
            client, api_env, first_operation, request_id="p2-03c-undo-1"
        )
        after_undo = await client.get(f"/api/v1/assets/{asset_id}/place")
        suggested_again = await client.get(
            "/api/v1/assets",
            params={"place_state": "suggested", "order": "asset_id", "limit": 50},
        )

        # A second confirmation, then a *later* edit of the same asset.
        revision, _mode = await current_assignment(client, asset_id)
        second = await confirm_suggestion(
            client,
            api_env,
            asset_id,
            target=target,
            expected=revision,
            request_id="p2-03c-confirm-2",
        )
        second_operation = second.json()
        assert second.status_code == 200, second.text
        other_id = int(api_env.service.catalog.read_one(
            "SELECT place_id FROM geo_dataset_place_refs WHERE external_id='city-suzhou'"
        )["place_id"])
        revision, _mode = await current_assignment(client, asset_id)
        later = await client.put(
            f"/api/v1/assets/{asset_id}/place-assignment",
            json={
                "action": "assign",
                "target_place_id": other_id,
                "request_id": "p2-03c-later-edit",
                "expected_revision": revision,
            },
            headers=api_env.post_headers(api_env.csrf_token),
        )
        assert later.status_code == 200, later.text
        later_operation = later.json()
        refused = await undo_operation(
            client, api_env, second_operation, request_id="p2-03c-undo-2"
        )
        wrong_revision = await client.post(
            f"/api/v1/place-operations/{later_operation['operation_id']}/undo",
            json={"request_id": "p2-03c-undo-3", "expected_operation_revision": 999},
            headers=api_env.post_headers(api_env.csrf_token),
        )
        good_undo = await undo_operation(
            client, api_env, later_operation, request_id="p2-03c-undo-4"
        )
        conflict = await client.post(
            f"/api/v1/place-operations/{later_operation['operation_id']}/undo",
            json={
                "request_id": "p2-03c-undo-4",
                "expected_operation_revision": int(later_operation["operation_revision"]) + 1,
            },
            headers=api_env.post_headers(api_env.csrf_token),
        )
        return {
            "first_operation": first_operation,
            "undone": undone,
            "after_undo": after_undo,
            "suggested_again": suggested_again,
            "second_operation": second_operation,
            "later": later,
            "refused": refused,
            "wrong_revision": wrong_revision,
            "good_undo": good_undo,
            "conflict": conflict,
        }

    result = api_env.scenario(scenario)

    first_operation = result["first_operation"]
    assert first_operation["operation_type"] == "ASSIGN"
    undone = result["undone"]
    assert undone.status_code == 200, undone.text
    assert undone.json()["operation_type"] == "UNDO"
    assert undone.json()["undo_of_operation_id"] == first_operation["operation_id"]
    assert undone.json()["changed"] is True

    # The compensation restores the pre-assignment semantics and the visible suggestion.
    after_undo = result["after_undo"].json()
    assert after_undo["place"] is None
    assert after_undo["assignment"] is None or after_undo["assignment"]["mode"] == "AUTO_ALLOWED"
    assert after_undo["assignment"] is None or after_undo["assignment"]["manual_lock"] is False
    again = result["suggested_again"]
    assert again.status_code == 200, again.text
    assert [row["asset_id"] for row in again.json()["assets"]] != []

    second_operation = result["second_operation"]
    assert result["later"].status_code == 200, result["later"].text
    assert second_operation["operation_id"] != first_operation["operation_id"]

    refused = result["refused"]
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "PLACE_OPERATION_NOT_UNDOABLE"
    assert refused.json()["details"]["operation_id"] == second_operation["operation_id"]

    wrong_revision = result["wrong_revision"]
    assert wrong_revision.status_code == 409, wrong_revision.text
    assert wrong_revision.json()["code"] == "PLACE_OPERATION_NOT_UNDOABLE"

    good_undo = result["good_undo"]
    assert good_undo.status_code == 200, good_undo.text
    assert good_undo.json()["operation_type"] == "UNDO"

    conflict = result["conflict"]
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "PLACE_IDEMPOTENCY_CONFLICT"


def test_a_missing_package_reports_itself_and_manual_curation_keeps_working(api_env) -> None:
    """Historical USER state survives; new assignments still require source provenance."""
    from p2_legacy_place_fixtures import seed_legacy_assignment

    from photo_search.ingestion.service import CatalogService
    from photo_search.places.service import PlaceService

    write_gps_jpeg(api_env.library / "plain.jpg", position=None)
    source = api_env.register_source("library")
    api_env.scan(int(source["source_id"]))
    asset_id = api_env.first_asset_id()
    city = PlaceService(api_env.service.catalog, clock=api_env.clock).create_place(
        kind="custom", canonical_name="历史家庭地点",
    )
    api_env.service.close()
    seed_legacy_assignment(api_env.data_root, asset_id=asset_id, place_id=city["place_id"])
    api_env.service = CatalogService.open(data_root=api_env.data_root, clock=api_env.clock)

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)

        before = await client.get("/api/v1/places/datasets")
        new_place = await create_place(client, api_env, kind="custom", name="新家庭地点")
        revision_before = revision_module.read_filter_revision(api_env.service.catalog.connection)
        assigned = await client.put(
            f"/api/v1/assets/{asset_id}/place-assignment",
            json={
                "action": "assign",
                "target_place_id": new_place["place_id"],
                "request_id": "p2-03c-manual-1",
                "expected_revision": 1,
            },
            headers=api_env.post_headers(api_env.csrf_token),
        )
        assert assigned.status_code == 404, assigned.text
        assert assigned.json()["code"] == "PLACE_NOT_FOUND"
        assert api_env.service.catalog.read_one("SELECT COUNT(*) FROM place_operations")[0] == 1
        assert api_env.service.catalog.read_one(
            "SELECT COUNT(*) FROM asset_place_assignment_history"
        )[0] == 1
        assert revision_module.read_filter_revision(
            api_env.service.catalog.connection
        ) == revision_before
        historical_members = await client.get(
            "/api/v1/assets", params={"place_ids": city["place_id"]},
        )
        assert historical_members.status_code == 200, historical_members.text
        assert [item["asset_id"] for item in historical_members.json()["assets"]] == [asset_id]
        installed = install_package(api_env)
        active = await client.get("/api/v1/places/datasets")

        package_dir = datasets_module.dataset_dir(
            api_env.data_root, str(installed["dataset_id"])
        )
        (package_dir / "places.jsonl").unlink()
        broken = await client.get("/api/v1/places/datasets")
        still_assigned = await client.get(f"/api/v1/assets/{asset_id}/place")
        directory = await client.get("/api/v1/places", params={"limit": 50})
        suggested = await client.get(
            "/api/v1/assets", params={"place_state": "suggested", "limit": 10}
        )
        created_after = await create_place(client, api_env, kind="custom", name="坏包后新建")
        return {
            "before": before,
            "assigned": assigned,
            "city": city,
            "installed": installed,
            "active": active,
            "broken": broken,
            "still_assigned": still_assigned,
            "directory": directory,
            "suggested": suggested,
            "created_after": created_after,
        }

    result = api_env.scenario(scenario)

    before = result["before"]
    assert before.status_code == 200, before.text
    assert before.json()["state"] == "unavailable"
    assert before.json()["reason"] == "PLACE_DATASET_NOT_INSTALLED"
    assert before.json()["resolver_available"] is False
    assert before.json()["active_dataset"] is None
    assert before.json()["datasets"] == []

    assert result["assigned"].status_code == 404, result["assigned"].text

    active = result["active"]
    assert active.status_code == 200, active.text
    assert active.json()["state"] == "active"
    assert active.json()["resolver_available"] is True
    assert active.json()["active_dataset"]["dataset_id"] == result["installed"]["dataset_id"]
    assert active.json()["active_dataset"]["state"] == "ACTIVE"

    broken = result["broken"]
    assert broken.status_code == 200, broken.text
    assert broken.json()["state"] == "missing"
    assert broken.json()["reason"] == "PLACE_DATASET_MISSING"
    assert broken.json()["resolver_available"] is False
    assert broken.json()["active_dataset"]["problems"][0]["code"] == "PLACE_DATASET_MISSING"

    # The manual result and the directory survive a broken package untouched.
    place_body = result["still_assigned"].json()
    assert place_body["place"]["place_id"] == result["city"]["place_id"]
    assert place_body["assignment"]["mode"] == "MANUAL_ASSIGNED"
    directory = result["directory"]
    assert directory.status_code == 200, directory.text
    assert result["city"]["place_id"] in {
        row["place_id"] for row in directory.json()["places"]
    }
    assert result["created_after"]["canonical_name"] == "坏包后新建"
    # No package means a deterministically empty suggestion set, never an error or a guess.
    suggested = result["suggested"]
    assert suggested.status_code == 200, suggested.text
    assert suggested.json()["assets"] == []


def test_page_responses_carry_no_coordinates_and_writes_need_csrf(api_env) -> None:
    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        install_package(api_env)
        assets = prepare_gps_assets(api_env, {"a.jpg": HANGZHOU_POSITION})
        asset_id = assets[0]

        directory = await client.get("/api/v1/places", params={"limit": 50})
        datasets = await client.get("/api/v1/places/datasets")
        place_state = await client.get(f"/api/v1/assets/{asset_id}/place")
        operations = await client.get("/api/v1/place-operations", params={"limit": 50})
        geo = await client.get(f"/api/v1/assets/{asset_id}/geo")

        before = revision_snapshot(api_env)
        no_csrf = await client.post(
            "/api/v1/place-assignments/batch",
            json={
                "request_id": "p2-03c-no-csrf",
                "items": [
                    {
                        "asset_id": asset_id,
                        "action": "unassign",
                        "target_place_id": None,
                        "expected_revision": 0,
                    }
                ],
            },
            headers={"Origin": "http://127.0.0.1"},
        )
        no_origin = await client.put(
            f"/api/v1/assets/{asset_id}/place-assignment",
            json={
                "action": "unassign",
                "target_place_id": None,
                "request_id": "p2-03c-no-origin",
                "expected_revision": 0,
            },
            headers={"X-CSRF-Token": api_env.csrf_token},
        )
        after = revision_snapshot(api_env)
        operations_after = await client.get("/api/v1/place-operations", params={"limit": 50})
        return {
            "directory": directory,
            "datasets": datasets,
            "place_state": place_state,
            "operations": operations,
            "geo": geo,
            "no_csrf": no_csrf,
            "no_origin": no_origin,
            "before": before,
            "after": after,
            "operations_after": operations_after,
            "asset_id": asset_id,
        }

    result = api_env.scenario(scenario)

    for key in ("directory", "datasets", "place_state", "operations"):
        response = result[key]
        assert response.status_code == 200, response.text
        assert_no_coordinates(response.json())

    geo = result["geo"]
    assert geo.status_code == 200, geo.text
    # The check above is not vacuous: the same fact does carry coordinates on /geo.
    keys = collect_keys(geo.json())
    assert "latitude" in keys and "longitude" in keys

    no_csrf = result["no_csrf"]
    assert no_csrf.status_code == 403, no_csrf.text
    assert no_csrf.json()["code"] == "CSRF_INVALID"
    no_origin = result["no_origin"]
    assert no_origin.status_code == 403, no_origin.text
    assert no_origin.json()["code"] == "ORIGIN_NOT_ALLOWED"

    # Both refusals wrote nothing at all.
    assert result["after"] == result["before"]
    assert result["operations_after"].json()["operations"] == []
