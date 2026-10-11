"""CN addon lifecycle, current-input authority and the real application API."""

import asyncio
import threading

import pytest
from p2_search_api_fixtures import build_search_env
from p4_cn_fixtures import geo, install, resolve_batch

from photo_search.api.errors import ApiError
from photo_search.catalog.errors import CatalogError
from photo_search.geo import dataset, resolver
from photo_search.places import albums, cn_facets, datasets
from photo_search.search.documents import catch_up


def seed(api_env, count=4):
    for n in range(count):
        api_env.add_photo(f"日落-{n}.jpg", noise=n + 1)
    source = api_env.register_source("synthetic CN")
    api_env.scan(source["source_id"])
    ids = [
        int(r["asset_id"])
        for r in api_env.service.catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id")
    ]
    for asset_id in ids:
        geo(api_env.service.catalog, asset_id)
    return ids


def test_cn_current_members_manual_exclusion_and_cursor(api_env):
    catalog = api_env.service.catalog
    install(catalog, api_env.tmp_path)
    ids = seed(api_env)
    original = [tuple(r) for r in catalog.read_all("SELECT * FROM asset_geo_facts")]
    original_jobs = [tuple(r) for r in catalog.read_all("SELECT * FROM jobs")]
    assert resolve_batch(catalog, api_env.data_root)["processed"] == 4
    assert resolve_batch(catalog, api_env.data_root, restart=True)["processed"] == 0
    selected = albums.albums(catalog, api_env.data_root, kind="district")["albums"][0]
    assert selected["name"] == "西湖区" and selected["total"] == 4
    first = albums.album_assets(
        catalog, api_env.data_root, kind="district", key=selected["key"], limit=2
    )
    second = albums.album_assets(
        catalog,
        api_env.data_root,
        kind="district",
        key=selected["key"],
        limit=2,
        cursor=first["next_cursor"],
    )
    assert [a["asset_id"] for a in first["assets"] + second["assets"]] == ids

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        city = catalog.read_one(
            "SELECT place_id FROM place_external_refs WHERE external_id='city-shanghai'"
        )["place_id"]
        for asset, action, target in [(ids[0], "unassign", None), (ids[1], "assign", city)]:
            response = await client.put(
                f"/api/v1/assets/{asset}/place-assignment",
                json={
                    "action": action,
                    "target_place_id": target,
                    "expected_revision": 0,
                    "request_id": f"cn-{asset}",
                },
                headers=api_env.post_headers(csrf),
            )
            assert response.status_code == 200, response.text
        assert albums.albums(catalog, api_env.data_root, kind="district")["albums"][0]["total"] == 2
        with pytest.raises(ApiError):
            albums.album_assets(
                catalog,
                api_env.data_root,
                kind="district",
                key=selected["key"],
                cursor=first["next_cursor"],
            )
        province = albums.albums(catalog, api_env.data_root, kind="province")
        assert {a["name"]: a["total"] for a in province["albums"]} == {"浙江省": 2, "上海市": 1}
        # A current active denial conservatively blocks unmapped new provider IDs.
        with catalog.write() as connection:
            connection.execute(
                "INSERT INTO asset_place_exclusions VALUES (?,?,1,1,?,1,1)",
                (
                    ids[2],
                    city,
                    catalog.read_one("SELECT operation_id FROM place_operations LIMIT 1")[
                        "operation_id"
                    ],
                ),
            )
        assert albums.albums(catalog, api_env.data_root, kind="district")["albums"][0]["total"] == 1
        reason = await client.get(f"/api/v1/assets/{ids[2]}/admin-place")
        assert reason.status_code == 200
        assert reason.json()["reason"] == "ACTIVE_EXCLUSION_CONSERVATIVE"
        assert reason.json()["facets"] == []
        manual = cn_facets.asset_projection(catalog, ids[1])
        assert manual["origin"] == "MANUAL"
        assert manual["facets"] == [
            {"kind": "province", "key": manual["facets"][0]["key"], "name": "上海市"}
        ]
        assert cn_facets.asset_projection(catalog, ids[0])["reason"] == "MANUAL_UNASSIGNED"

    api_env.scenario(scenario)
    assert original == [tuple(r) for r in catalog.read_all("SELECT * FROM asset_geo_facts")]
    assert catalog.read_one("SELECT COUNT(*) AS n FROM place_resolutions")["n"] == 0
    assert original_jobs == [tuple(r) for r in catalog.read_all("SELECT * FROM jobs")]


def test_cn_album_paginates_more_than_48_members(api_env):
    catalog = api_env.service.catalog
    install(catalog, api_env.tmp_path)
    ids = seed(api_env, count=50)
    assert resolve_batch(catalog, api_env.data_root)["processed"] == 50
    selected = albums.albums(catalog, api_env.data_root, kind="district")["albums"][0]
    first = albums.album_assets(
        catalog, api_env.data_root, kind="district", key=selected["key"], limit=48
    )
    second = albums.album_assets(
        catalog,
        api_env.data_root,
        kind="district",
        key=selected["key"],
        limit=48,
        cursor=first["next_cursor"],
    )
    actual = [item["asset_id"] for item in first["assets"] + second["assets"]]
    assert first["total"] == second["total"] == 50
    assert len(first["assets"]) == 48 and len(second["assets"]) == 2
    assert actual == ids and len(set(actual)) == 50


def test_cn_source_geometry_and_versioned_package(api_env):
    catalog = api_env.service.catalog
    dataset_id, package = install(catalog, api_env.tmp_path)
    content = dataset.load_content(package, supported_schema_version=18)
    assert content.cn_admin.resolve(30.273, 120.09)["district"] == "relation/3"
    assert content.cn_admin.resolve(30.25, 120.14)["district"] is None  # hole
    assert content.cn_admin.resolve(30.273, 120.2)["district"] is None  # boundary
    assert content.cn_admin.resolve(31.23, 121.47)["city"] is None  # municipality
    assert content.cn_admin.resolve(35.68, 139.69)["province"] is None
    assert content.province_index == {}  # base INDEX semantics preserved
    before = catalog.read_one("SELECT dataset_id,revision FROM active_geo_dataset")
    stored = datasets.dataset_dir(api_env.data_root, dataset_id)
    (stored / "cn-admin/cn-admin.jsonl.gz").write_bytes(b"broken")
    with pytest.raises(CatalogError):
        datasets.activate_dataset(catalog, dataset_id)
    assert tuple(catalog.read_one("SELECT dataset_id,revision FROM active_geo_dataset")) == tuple(
        before
    )


def test_cn_rollback_reuses_identity_and_invalidates_pages(api_env):
    catalog = api_env.service.catalog
    dataset_id, _ = install(catalog, api_env.tmp_path)
    seed(api_env)
    resolve_batch(catalog, api_env.data_root)
    region = albums.albums(catalog, api_env.data_root, kind="district")["albums"][0]
    first = albums.album_assets(
        catalog, api_env.data_root, kind="district", key=region["key"], limit=1
    )
    identity = [tuple(r) for r in catalog.read_all("SELECT * FROM cn_admin_ids")]
    datasets.rollback_dataset(catalog)
    assert albums.albums(catalog, api_env.data_root, kind="district")["albums"] == []
    datasets.activate_dataset(catalog, dataset_id)
    assert identity == [tuple(r) for r in catalog.read_all("SELECT * FROM cn_admin_ids")]
    with pytest.raises(ApiError):
        albums.album_assets(
            catalog,
            api_env.data_root,
            kind="district",
            key=region["key"],
            cursor=first["next_cursor"],
        )
    assert resolve_batch(catalog, api_env.data_root)["processed"] == 0


def test_cn_late_batch_refuses_package_and_fact_changes(api_env):
    catalog = api_env.service.catalog
    install(catalog, api_env.tmp_path)
    ids = seed(api_env)
    content = resolver.load_active_content(catalog.connection, data_root=api_env.data_root)
    run, rows = cn_facets.inputs(catalog, content)
    results = cn_facets.calculate(content, rows)
    with catalog.write() as connection:
        connection.execute(
            "UPDATE asset_geo_facts SET geo_fact_id='new-input' WHERE asset_id=?", (ids[0],)
        )
    result = cn_facets.publish(catalog, content, run, rows, results)
    assert result["processed"] == 3
    run, rows = cn_facets.inputs(catalog, content, restart=True)
    datasets.rollback_dataset(catalog)
    with pytest.raises(ApiError):
        cn_facets.publish(catalog, content, run, rows, cn_facets.calculate(content, rows))


def test_cn_api_place_search_matches_browser_and_content(api_env):
    catalog = api_env.service.catalog
    install(catalog, api_env.tmp_path)
    ids = seed(api_env)
    catch_up(catalog)

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        update = await client.post(
            "/api/v1/places/admin/update", headers=api_env.post_headers(csrf)
        )
        assert update.status_code == 200, update.text
        assert update.json()["processed"] == 4 and update.json()["complete"]
        listing = (await client.get("/api/v1/places/albums", params={"kind": "district"})).json()
        assert listing["albums"][0]["total"] == 4
        pure = await client.post(
            "/api/v1/search/place-aware",
            json={"request_id": "cn-pure", "original_query": "西湖区", "page_size": 2},
            headers=api_env.post_headers(csrf),
        )
        assert pure.status_code == 200, pure.text
        body = pure.json()
        assert [r["asset_id"] for r in body["hits"]] == ids[:2]
        next_page = await client.post(
            "/api/v1/search/place-aware",
            json={
                "request_id": "cn-next",
                "original_query": "西湖区",
                "place_kind": "district",
                "place_key": listing["albums"][0]["key"],
                "content_query": "",
                "page_size": 2,
                "search_session_id": body["search_session_id"],
                "cursor": body["next_cursor"],
            },
            headers=api_env.post_headers(csrf),
        )
        assert next_page.status_code == 200, next_page.text
        assert [r["asset_id"] for r in next_page.json()["hits"]] == ids[2:]
        assert next_page.json()["hits"][0]["rank"] == 3

    api_env.scenario(scenario)


def test_place_aware_search_routes_verified_legacy_alias_to_cn_album(api_env):
    catalog = api_env.service.catalog
    install(catalog, api_env.tmp_path, legacy_jinan=True)
    ids = seed(api_env, count=4)
    legacy_place_id = int(
        catalog.read_one(
            "SELECT place_id FROM place_external_refs WHERE external_id='city-jinan'"
        )["place_id"]
    )
    link = catalog.read_one(
        "SELECT city_id FROM cn_legacy_links WHERE place_id=?", (legacy_place_id,)
    )
    assert link is not None and link["city_id"] is not None

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        for asset_id in ids:
            assigned = await client.put(
                f"/api/v1/assets/{asset_id}/place-assignment",
                json={
                    "action": "assign",
                    "target_place_id": legacy_place_id,
                    "expected_revision": 0,
                    "request_id": f"legacy-jinan-{asset_id}",
                },
                headers=headers,
            )
            assert assigned.status_code == 200, assigned.text
        listing = await client.get("/api/v1/places/albums", params={"kind": "city"})
        assert listing.status_code == 200, listing.text
        album = next(
            item
            for item in listing.json()["albums"]
            if item["key"] == f"cn:{link['city_id']}"
        )
        response = await client.post(
            "/api/v1/search/place-aware",
            json={
                "request_id": "legacy-jinan-search",
                "original_query": "济南的照片",
                "page_size": 10,
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
        return album, response.json()

    album, result = api_env.scenario(scenario)
    assert album["name"] == "济南市"
    assert album["total"] == len(ids)
    assert result["place_scope"]["key"] == album["key"]
    assert result["place_scope"]["name"] == "济南市"
    assert result["total_candidates"] == album["total"]
    assert [hit["asset_id"] for hit in result["hits"]] == ids
    assert result["content_query"] is None


def test_cn_content_search_with_bound_synthetic_encoder(tmp_path):
    env = build_search_env(tmp_path)
    try:
        install(env.catalog, tmp_path)
        chosen = int(env.assets["beach sunset.jpg"])
        for asset_id in env.assets.values():
            geo(
                env.catalog,
                int(asset_id),
                30.273 if asset_id == chosen else 31.23,
                120.09 if asset_id == chosen else 121.47,
            )
        resolve_batch(env.catalog, env.data_root)
        env.encoder._vectors["日落"] = [1.0, 0.0, 0.0]

        async def scenario(client):
            csrf = await env.authenticate(client)
            response = await client.post(
                "/api/v1/search/place-aware",
                json={
                    "request_id": "cn-bound-content",
                    "original_query": "西湖区的日落",
                    **env.explicit_binding(),
                },
                headers=env.post_headers(csrf),
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["place_scope"]["kind"] == "district"
            assert [r["asset_id"] for r in body["hits"]] == [chosen]
            assert env.encoder.calls == ["日落"]

        env.scenario(scenario)
    finally:
        env.context.close()
        env.library.close()


def test_cn_same_name_requires_parent_choice(api_env):
    install(api_env.service.catalog, api_env.tmp_path, duplicate=True)
    ids = seed(api_env, count=2)
    with api_env.service.catalog.write() as connection:
        connection.execute(
            "UPDATE asset_geo_facts SET latitude=31.4, longitude=121.5 WHERE asset_id=?",
            (ids[1],),
        )
    resolve_batch(api_env.service.catalog, api_env.data_root)

    async def scenario(client):
        csrf = await api_env.authenticate(client)
        response = await client.post(
            "/api/v1/search/place-aware",
            json={"request_id": "cn-ambiguous", "original_query": "西湖区"},
            headers=api_env.post_headers(csrf),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["needs_scope_choice"] is True
        choices = [c for c in body["scope_choices"] if c["type"] == "place"]
        assert len(choices) == 2
        candidates = [choice["place"] for choice in choices]
        assert len({
            (candidate["parent"], candidate["source"], candidate["selection_ref"])
            for candidate in candidates
        }) == 2
        plans = [choice["intent_plan"] for choice in choices]
        assert len({(plan["place_kind"], str(plan["place_key"])) for plan in plans}) == 2
        assert len({tuple((plan.get("place_selection") or {}).items()) for plan in plans}) == 2

        selected_results = []
        for index, (plan, expected_asset) in enumerate(zip(plans, ids, strict=True)):
            selected = await client.post(
                "/api/v1/search/place-aware",
                json={"request_id": f"cn-selected-{index}", "original_query": "西湖区",
                      "intent_plan": plan},
                headers=api_env.post_headers(csrf),
            )
            assert selected.status_code == 200, selected.text
            selected_body = selected.json()
            assert selected_body["place_scope"]["key"] == str(plan["place_key"])
            selected_results.append([int(row["asset_id"]) for row in selected_body["hits"]])
        assert selected_results == [[ids[0]], [ids[1]]]

    api_env.scenario(scenario)


def test_cn_generation_fence_and_gps_cursor_expiry(api_env):
    catalog = api_env.service.catalog
    install(catalog, api_env.tmp_path)
    ids = seed(api_env)
    content = resolver.load_active_content(catalog.connection, data_root=api_env.data_root)
    run, rows = cn_facets.inputs(catalog, content)
    results = cn_facets.calculate(content, rows)
    cn_facets.start(catalog, content.dataset_id)
    with pytest.raises(ApiError):
        cn_facets.publish(catalog, content, run, rows, results)
    assert catalog.read_one("SELECT COUNT(*) AS n FROM cn_admin_resolutions")["n"] == 0
    resolve_batch(catalog, api_env.data_root)
    region = cn_facets.collection(catalog, kind="district")["albums"][0]
    page = cn_facets.page(catalog, kind="district", key=region["key"], limit=1, cursor=None)
    with catalog.write() as connection:
        connection.execute(
            "UPDATE asset_geo_facts SET geo_fact_id='changed-gps' WHERE asset_id=?", (ids[0],)
        )
    with pytest.raises(ApiError):
        cn_facets.page(
            catalog, kind="district", key=region["key"], limit=1, cursor=page["next_cursor"]
        )
    assert cn_facets.collection(catalog, kind="district")["albums"][0]["total"] == 3
    assert cn_facets.asset_projection(catalog, ids[0])["reason"] == "NO_CURRENT_RESOLUTION"


def test_cn_rollback_verifies_outside_transaction_and_fences_switch(api_env, monkeypatch):
    catalog = api_env.service.catalog
    dataset_id, _ = install(catalog, api_env.tmp_path)
    original = datasets._verify_stored

    def verify(connection, row, root):
        assert not connection.in_transaction
        content = original(connection, row, root)
        with catalog.write() as writable:
            writable.execute("UPDATE active_geo_dataset SET revision=revision+1 WHERE singleton=1")
        return content

    monkeypatch.setattr(datasets, "_verify_stored", verify)
    with pytest.raises(CatalogError):
        datasets.rollback_dataset(catalog)
    assert catalog.read_one("SELECT dataset_id FROM active_geo_dataset")["dataset_id"] == dataset_id


def test_cn_dataset_disk_verification_does_not_block_api(api_env, monkeypatch):
    install(api_env.service.catalog, api_env.tmp_path)
    owner = threading.get_ident()
    started, release = threading.Event(), threading.Event()
    original = datasets.render_status_snapshot

    def verify(snapshot, **kwargs):
        assert threading.get_ident() != owner
        started.set()
        assert release.wait(5)
        return original(snapshot, **kwargs)

    monkeypatch.setattr(datasets, "render_status_snapshot", verify)

    async def scenario(client):
        await api_env.authenticate(client)
        pending = asyncio.create_task(client.get("/api/v1/places/datasets"))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            health = await asyncio.wait_for(client.get("/api/v1/health"), 1)
            assert health.status_code == 200 and not pending.done()
        finally:
            release.set()
        response = await pending
        assert response.status_code == 200, response.text

    api_env.scenario(scenario)


def test_cn_status_does_not_reparse_registered_geometry(api_env, monkeypatch):
    catalog = api_env.service.catalog
    dataset_id, _ = install(catalog, api_env.tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("status must not load geometry")

    monkeypatch.setattr(dataset, "load_content", forbidden)
    assert datasets.dataset_status(catalog.connection, data_root=api_env.data_root)[
        "resolver_available"
    ]
    stored = datasets.dataset_dir(api_env.data_root, dataset_id)
    (stored / "cn-admin/cn-admin.jsonl.gz").write_bytes(b"broken")
    result = datasets.dataset_status(catalog.connection, data_root=api_env.data_root)
    assert not result["resolver_available"] and result["state"] == "invalid"
