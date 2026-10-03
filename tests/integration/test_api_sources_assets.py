"""P1-04 scenarios 2, 3 and 4: sources, scan requests and asset paging.

All fixtures live under ``tmp_path``; the scans here run against synthetic JPEGs only.
"""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient

from photo_search.catalog import schema
from photo_search.refresh import progress as refresh_progress


def _seed_library(api_env, count: int) -> list[Path]:
    created = []
    for index in range(count):
        created.append(
            api_env.add_photo(
                f"photos/p{index:03d}.jpg",
                size=(64, 48),
                color=((index * 7) % 256, (index * 13) % 256, 90),
            )
        )
    return created


async def _register(client: AsyncClient, api_env, csrf: str, name: str = "library") -> dict:
    response = await client.post(
        "/api/v1/sources",
        json={"display_name": name, "root_path": str(api_env.library)},
        headers=api_env.post_headers(csrf),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_source_summary_is_rootless_lightweight_and_session_protected(api_env, monkeypatch) -> None:
    api_env.service.register_source(
        display_name="中文图库",
        root_path=api_env.library,
        volume_hint="外接盘 A",
    )

    def fail_if_full_progress_is_requested(*args, **kwargs):
        raise AssertionError("lightweight source summary requested full progress")

    monkeypatch.setattr(refresh_progress, "source_progress", fail_if_full_progress_is_requested)

    async def scenario(client: AsyncClient) -> tuple[int, dict]:
        unauthenticated = await client.get("/api/v1/sources/summary")
        await api_env.authenticate(client)
        response = await client.get("/api/v1/sources/summary")
        return unauthenticated.status_code, response.json()

    unauthenticated_status, body = api_env.scenario(scenario)
    assert unauthenticated_status == 401
    assert body == {
        "sources": [
            {
                "source_id": 1,
                "display_name": "中文图库",
                "volume_hint": "外接盘 A",
                "status": "ONLINE",
                "last_successful_scan": None,
            }
        ]
    }
    assert "root_path" not in str(body)
    assert "counts" not in str(body)
    assert "progress" not in str(body)


def test_capabilities_reports_the_contract_without_local_paths(api_env) -> None:
    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        response = await client.get("/api/v1/capabilities")
        assert response.status_code == 200, response.text
        return response.json()

    body = api_env.scenario(scenario)

    assert body["api_version"] == "v1"
    assert body["supported_schema_version"] == schema.SUPPORTED_SCHEMA_VERSION
    assert body["controlled_reads"] is True
    assert body["session_required"] is True
    assert body["loopback_only"] is True
    assert body["media"]["thumbnail"]["processor_id"] == "catalog-thumbnail@2"
    assert body["media"]["thumbnail"]["long_edge"] == 320
    assert body["media"]["preview"]["processor_id"] == "catalog-preview@1"
    assert body["media"]["preview"]["long_edge"] == 1600
    assert "JPEG" in body["supported_input_formats"]
    assert "TIFF" not in body["supported_input_formats"]
    serialised = str(body)
    assert str(api_env.data_root) not in serialised
    assert str(api_env.library) not in serialised
    assert "db_path" not in body


def test_page_capabilities_does_not_initialize_encoder_or_read_coverage(api_env, monkeypatch) -> None:
    from photo_search.api import routes

    def unexpected_work(*args, **kwargs):
        raise AssertionError("page startup performed model or coverage work")

    monkeypatch.setattr(api_env.context, "query_encoder", unexpected_work)
    for name in (
        "scheduler_summary", "index_summary", "search_projection_summary", "search_profile_summary",
        "ocr_summary", "face_summary", "ocr_weights_present", "face_weights_present",
    ):
        monkeypatch.setattr(routes, name, unexpected_work)

    async def scenario(client: AsyncClient) -> dict:
        unauthenticated = await client.get("/api/v1/capabilities/page")
        assert unauthenticated.status_code == 401
        await api_env.authenticate(client)
        response = await client.get("/api/v1/capabilities/page")
        assert response.status_code == 200, response.text
        return response.json()

    body = api_env.scenario(scenario)
    assert body == {
        "app_version": routes.APP_VERSION,
        "api_version": "v1",
        "schema_version": schema.SUPPORTED_SCHEMA_VERSION,
        "supported_schema_version": schema.SUPPORTED_SCHEMA_VERSION,
        "schema_status": "ok",
        "controlled_reads": True,
        "session_required": True,
    }


def test_sources_create_list_and_structured_rejections(api_env) -> None:
    _seed_library(api_env, 3)

    async def scenario(client: AsyncClient) -> dict:
        csrf = await api_env.authenticate(client)
        created = await _register(client, api_env, csrf)
        listed = await client.get("/api/v1/sources")
        fetched = await client.get(f"/api/v1/sources/{created['source_id']}")
        overlap = await client.post(
            "/api/v1/sources",
            json={"display_name": "again", "root_path": str(api_env.library)},
            headers=api_env.post_headers(csrf),
        )
        nested = await client.post(
            "/api/v1/sources",
            json={"display_name": "nested", "root_path": str(api_env.library / "photos")},
            headers=api_env.post_headers(csrf),
        )
        inside_data_root = await client.post(
            "/api/v1/sources",
            json={"display_name": "data", "root_path": str(api_env.tmp_path)},
            headers=api_env.post_headers(csrf),
        )
        not_a_directory = await client.post(
            "/api/v1/sources",
            json={"display_name": "file", "root_path": str(api_env.library / "photos/p000.jpg")},
            headers=api_env.post_headers(csrf),
        )
        blank = await client.post(
            "/api/v1/sources",
            json={"display_name": "", "root_path": str(api_env.library)},
            headers=api_env.post_headers(csrf),
        )
        unknown = await client.get("/api/v1/sources/9999")
        return {
            "created": created,
            "listed": listed.json(),
            "fetched": fetched.json(),
            "overlap": overlap,
            "nested": nested,
            "inside": inside_data_root,
            "not_a_directory": not_a_directory,
            "blank": blank,
            "unknown": unknown,
        }

    result = api_env.scenario(scenario)

    created = result["created"]
    assert created["display_name"] == "library"
    assert created["status"] == "ONLINE"
    assert created["root_path"] == str(api_env.library)
    assert created["counts"]["sources"] == 1

    assert [item["source_id"] for item in result["listed"]["sources"]] == [created["source_id"]]
    assert result["fetched"]["source_id"] == created["source_id"]

    assert result["overlap"].status_code == 409
    assert result["overlap"].json()["code"] == "SOURCE_OVERLAP"
    assert result["nested"].status_code == 409
    assert result["nested"].json()["code"] == "SOURCE_OVERLAP"
    assert result["inside"].status_code == 409
    assert result["inside"].json()["code"] == "DATA_ROOT_INSIDE_SOURCE"
    assert result["not_a_directory"].status_code == 422
    assert result["not_a_directory"].json()["code"] == "INVALID_PATH"
    assert result["blank"].status_code == 422
    assert result["blank"].json()["code"] == "VALIDATION_ERROR"
    assert result["unknown"].status_code == 404
    assert result["unknown"].json()["code"] == "SOURCE_NOT_FOUND"
    # A rejected registration must not leak an absolute path outside the registered roots.
    assert str(api_env.data_root) not in result["inside"].text


def test_scan_request_returns_202_and_completes_in_the_background(api_env) -> None:
    _seed_library(api_env, 4)

    async def request_scan(client: AsyncClient) -> dict:
        csrf = await api_env.authenticate(client)
        source = await _register(client, api_env, csrf)
        response = await client.post(
            f"/api/v1/sources/{source['source_id']}/scan", headers=api_env.post_headers(csrf)
        )
        return {"source": source, "response": response.json(), "status": response.status_code}

    started = api_env.scenario(request_scan)
    assert started["status"] == 202
    body = started["response"]
    assert body["reused"] is False
    assert body["state"] == "QUEUED"
    assert body["scan_id"] >= 1 and body["job_id"] >= 1
    # The request returned before the scan ran; the runner is the only executor.
    assert api_env.context.runner().wait_idle(60.0) is True

    async def after(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        jobs = (await client.get("/api/v1/jobs")).json()
        source = (await client.get(f"/api/v1/sources/{started['source']['source_id']}")).json()
        assets = (await client.get("/api/v1/assets")).json()
        stats = (await client.get("/api/v1/stats")).json()
        return {"jobs": jobs, "source": source, "assets": assets, "stats": stats}

    result = api_env.scenario(after)
    job = result["jobs"]["jobs"][0]
    assert job["job_id"] == body["job_id"]
    assert job["state"] == "SUCCEEDED"
    assert job["attempts"] == 0
    assert job["progress"] == 1.0
    assert job["last_error_code"] is None
    assert job["scan"]["scan_id"] == body["scan_id"]
    assert job["scan"]["state"] == "SUCCEEDED"
    assert "lease_token" not in job and "worker_id" not in job

    assert result["source"]["last_successful_scan"] == body["scan_id"]
    assert result["source"]["counts"]["locations"] == 4
    assert result["assets"]["has_more"] is False
    assert len(result["assets"]["assets"]) == 4
    assert result["stats"]["counts"]["assets"] == 4
    assert result["stats"]["counts"]["locations_present"] == 4
    assert result["stats"]["media"]["thumbnail"]["pending"] == 4


def test_interrupted_scan_is_recovered_as_the_same_scan_run(interrupted_env) -> None:
    api_env = interrupted_env
    _seed_library(api_env, 3)

    async def first_request(client: AsyncClient) -> dict:
        csrf = await api_env.authenticate(client)
        source = await _register(client, api_env, csrf)
        response = await client.post(
            f"/api/v1/sources/{source['source_id']}/scan", headers=api_env.post_headers(csrf)
        )
        return {"source": source, "scan": response.json()}

    started = api_env.scenario(first_request)
    runner = api_env.context.runner()
    assert runner.wait_idle(60.0) is True
    # The interrupted execution left the request RUNNING with its lease unconsumed.
    interrupted = api_env.service.catalog.read_one(
        "SELECT state FROM jobs WHERE job_id = ?", (started["scan"]["job_id"],)
    )
    assert interrupted["state"] == "RUNNING"

    # A killed process is recovered by the next request once the lease has expired.
    api_env.clock.advance(61_000)
    api_env.wall.advance(61_000)

    async def recover(client: AsyncClient) -> dict:
        csrf = await api_env.authenticate(client)
        response = await client.post(
            f"/api/v1/sources/{started['source']['source_id']}/scan",
            headers=api_env.post_headers(csrf),
        )
        return response.json()

    recovered = api_env.scenario(recover)
    assert recovered["reused"] is True
    assert recovered["scan_id"] == started["scan"]["scan_id"]
    assert recovered["job_id"] == started["scan"]["job_id"]
    assert runner.wait_idle(60.0) is True

    scans = api_env.service.catalog.read_all("SELECT scan_id, state FROM scan_runs")
    assert len(scans) == 1
    assert scans[0]["state"] == "SUCCEEDED"
    jobs = api_env.service.catalog.read_all("SELECT job_id, state, attempts FROM jobs")
    assert len(jobs) == 1
    assert jobs[0]["state"] == "SUCCEEDED"
    # Taking over an expired lease consumes exactly one retry attempt (ADR-0002 §6.2).
    assert jobs[0]["attempts"] == 1
    assert api_env.service.counts()["locations_pending_content"] == 0


def test_asset_paging_is_complete_ordered_and_bounded(api_env) -> None:
    _seed_library(api_env, 26)
    api_env.scan(api_env.register_source()["source_id"])

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        pages: list[dict] = []
        cursor: str | None = None
        for _ in range(20):
            params = {"limit": 7}
            if cursor:
                params["cursor"] = cursor
            page = (await client.get("/api/v1/assets", params=params)).json()
            pages.append(page)
            cursor = page["next_cursor"]
            if not page["has_more"]:
                break
        single = await client.get("/api/v1/assets", params={"limit": 1})
        large = await client.get("/api/v1/assets", params={"limit": 5_000})
        zero = await client.get("/api/v1/assets", params={"limit": 0})
        bad_cursor = await client.get("/api/v1/assets", params={"cursor": "!!!!"})
        bad_filter = await client.get("/api/v1/assets", params={"availability": "sometimes"})
        return {
            "pages": pages,
            "single": single.json(),
            "large": large.json(),
            "zero": zero,
            "bad_cursor": bad_cursor,
            "bad_filter": bad_filter,
        }

    result = api_env.scenario(scenario)
    collected = [item for page in result["pages"] for item in page["assets"]]
    identities = [item["asset_id"] for item in collected]
    assert len(identities) == 26
    assert identities == sorted(identities)
    assert len(set(identities)) == 26
    assert result["pages"][-1]["next_cursor"] is None
    for page in result["pages"]:
        assert page["limit"] == 7
        assert len(page["assets"]) <= 7

    assert result["single"]["limit"] == 1
    assert len(result["single"]["assets"]) == 1
    assert result["single"]["next_cursor"]
    assert result["large"]["limit"] == 200
    assert len(result["large"]["assets"]) == 26
    assert result["large"]["has_more"] is False
    assert result["zero"].status_code == 422
    assert result["zero"].json()["code"] == "VALIDATION_ERROR"
    assert result["bad_cursor"].status_code == 422
    assert result["bad_cursor"].json()["code"] == "INVALID_CURSOR"
    assert result["bad_filter"].status_code == 422
    assert result["bad_filter"].json()["code"] == "VALIDATION_ERROR"

    row = result["pages"][0]["assets"][0]
    assert set(row) == {
        "asset_id",
        "sha256",
        "byte_size",
        "created_at",
        "location_count",
        "availability",
        "thumbnail_ready",
        "preview_ready",
        "current_location",
    }
    assert row["availability"] == "present"
    assert row["thumbnail_ready"] is False
    assert row["current_location"]["source_id"] == 1
    assert row["current_location"]["relative_path"].startswith("photos")
    assert str(api_env.library) not in str(row["current_location"])


def test_asset_filters_follow_the_catalog_and_the_cache(api_env) -> None:
    files = _seed_library(api_env, 5)
    source = api_env.register_source()
    api_env.scan(source["source_id"])

    async def scenario(client: AsyncClient) -> dict:
        csrf = await api_env.authenticate(client)
        before = {
            "present": (await client.get("/api/v1/assets", params={"availability": "present"})).json(),
            "missing": (await client.get("/api/v1/assets", params={"availability": "missing"})).json(),
            "with_thumb": (
                await client.get("/api/v1/assets", params={"has_thumbnail": "true"})
            ).json(),
            "without_thumb": (
                await client.get("/api/v1/assets", params={"has_thumbnail": "false"})
            ).json(),
        }
        target = before["present"]["assets"][0]["asset_id"]
        generated = await client.get(f"/api/v1/assets/{target}/thumbnail")
        after = (
            await client.get("/api/v1/assets", params={"has_thumbnail": "true"})
        ).json()
        other_source = (
            await client.get("/api/v1/assets", params={"source_id": source["source_id"]})
        ).json()
        no_source = (await client.get("/api/v1/assets", params={"source_id": 999})).json()
        return {
            "before": before,
            "generated": generated.status_code,
            "after": after,
            "other_source": other_source,
            "no_source": no_source,
            "csrf": csrf,
        }

    result = api_env.scenario(scenario)
    assert len(result["before"]["present"]["assets"]) == 5
    assert result["before"]["missing"]["assets"] == []
    assert result["before"]["with_thumb"]["assets"] == []
    assert len(result["before"]["without_thumb"]["assets"]) == 5
    assert result["generated"] == 200
    assert len(result["after"]["assets"]) == 1
    assert result["after"]["assets"][0]["thumbnail_ready"] is True
    assert len(result["other_source"]["assets"]) == 5
    assert result["no_source"]["assets"] == []

    # Delete one original and rescan: the negative fact is produced only by the scan.
    files[0].unlink()
    api_env.clock.advance(1000)
    api_env.wall.advance(1000)
    api_env.scan(source["source_id"])

    async def after_missing(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        return (await client.get("/api/v1/assets", params={"availability": "missing"})).json()

    missing = api_env.scenario(after_missing)
    assert len(missing["assets"]) == 1
    assert missing["assets"][0]["availability"] == "missing"
    assert missing["assets"][0]["current_location"] is None


def test_asset_detail_and_unknown_ids(api_env) -> None:
    _seed_library(api_env, 2)
    api_env.scan(api_env.register_source()["source_id"])
    asset_id = api_env.first_asset_id()

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        detail = await client.get(f"/api/v1/assets/{asset_id}")
        unknown = await client.get("/api/v1/assets/9999")
        media_unknown = await client.get("/api/v1/assets/9999/thumbnail")
        return {"detail": detail, "unknown": unknown, "media_unknown": media_unknown}

    result = api_env.scenario(scenario)
    assert result["detail"].status_code == 200
    body = result["detail"].json()
    assert body["asset_id"] == asset_id
    assert len(body["sha256"]) == 64
    assert body["location_count"] == 1
    location = body["locations"][0]
    assert location["availability"] == "PRESENT"
    assert location["asset_bound"] is True
    assert location["source_display_name"] == "library"
    assert location["size"] > 0 and location["mtime_ns"] > 0
    assert body["media"]["thumbnail"]["state"] == "pending"
    assert body["media"]["preview"]["state"] == "pending"
    assert str(api_env.library) not in result["detail"].text

    assert result["unknown"].status_code == 404
    assert result["unknown"].json()["code"] == "ASSET_NOT_FOUND"
    assert result["media_unknown"].status_code == 404
    assert result["media_unknown"].json()["code"] == "ASSET_NOT_FOUND"


def test_stats_agrees_with_the_cli_read_model(api_env) -> None:
    _seed_library(api_env, 3)
    api_env.scan(api_env.register_source()["source_id"])

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        return (await client.get("/api/v1/stats")).json()

    body = api_env.scenario(scenario)
    direct = api_env.service.media_report()
    assert body["counts"] == api_env.service.counts()
    assert body["assets_examined"] == direct["assets_examined"] == 3
    assert body["partial"] is False
    for kind, bucket in direct["kinds"].items():
        for field in ("processor_id", "published", "pending", "source_missing", "failed"):
            assert body["media"][kind][field] == bucket[field]
    assert "artifact_root" not in body["media"]["thumbnail"]
    assert str(api_env.data_root) not in str(body)


def test_api_writes_do_not_collide_with_a_running_scan(api_env) -> None:
    """The API thread and the scan worker own two connections in one process.

    WAL plus ``BEGIN IMMEDIATE`` (with the busy timeout) must keep a concurrent
    registration attempt readable and structured instead of turning into an unhandled
    "database is locked" failure.
    """
    for index in range(400):
        api_env.add_photo(
            f"heavy/p{index:04d}.jpg",
            size=(64, 48),
            color=(index % 256, (index * 3) % 256, 30),
        )
    source = api_env.register_source()

    async def scenario(client: AsyncClient) -> dict:
        csrf = await api_env.authenticate(client)
        started = await client.post(
            f"/api/v1/sources/{source['source_id']}/scan", headers=api_env.post_headers(csrf)
        )
        observed: list[tuple[int, str]] = []
        scan_states: list[str] = []
        for _ in range(12):
            duplicate = await client.post(
                "/api/v1/sources",
                json={"display_name": "repeat", "root_path": str(api_env.library)},
                headers=api_env.post_headers(csrf),
            )
            observed.append((duplicate.status_code, duplicate.json().get("code", "")))
            stats = await client.get("/api/v1/stats")
            observed.append((stats.status_code, str(stats.json().get("partial"))))
            jobs = await client.get("/api/v1/jobs")
            state = jobs.json()["jobs"][0]["state"]
            scan_states.append(state)
            observed.append((jobs.status_code, state))
        return {"started": started.status_code, "observed": observed, "scan_states": scan_states}

    result = api_env.scenario(scenario)
    assert result["started"] == 202
    codes = {status for status, _ in result["observed"]}
    assert 500 not in codes, result["observed"]
    assert codes <= {200, 409}
    write_attempts = [code for status, code in result["observed"] if code == "SOURCE_OVERLAP"]
    assert len(write_attempts) == 12
    # Real overlap evidence: the requests were served while the scan was not yet terminal.
    assert set(result["scan_states"]) & {"QUEUED", "RUNNING"}, result["scan_states"]

    assert api_env.context.runner().wait_idle(120.0) is True
    job = api_env.service.catalog.read_one("SELECT state, progress FROM jobs WHERE job_id = 1")
    assert job["state"] == "SUCCEEDED"
    assert api_env.service.counts()["locations_present"] == 400
