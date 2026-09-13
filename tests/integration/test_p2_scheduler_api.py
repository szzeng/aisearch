"""P2-02 scenario 9: the scheduler surface of ``/api/v1``.

Only the application use cases are exposed: pause/resume write the persisted scheduler
policy through the catalog's single writer, and the read models add the dispatch summary.
Nothing in a response may carry a lease token, a worker id or a local path.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from photo_search.api.settings import ApiSettings
from photo_search.app import create_app
from photo_search.catalog import schema
from photo_search.catalog.errors import SCHEMA_TOO_NEW
from photo_search.jobs import queue as jobs
from photo_search.scheduler import engine
from photo_search.scheduler.contracts import (
    CONTRACT_VERSION,
    ProcessorSpec,
    RuntimeCapabilities,
)

TOKEN = "unit-test-session-token"
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "testserver"})
SHA = "a" * 64
PACKAGE = "photo-search-runtime@1"
PROCESSOR = "geo-extract@1"


def non_model_spec() -> ProcessorSpec:
    return ProcessorSpec.from_envelope(
        {
            "contract_version": CONTRACT_VERSION,
            "spec_revision": "geo-extract-v1",
            "processor_id": PROCESSOR,
            "processor_kind": "non_model",
            "task_type": "geo_extract",
            "config_hash": SHA,
            "input_type": "asset_original",
            "output_type": "geo_fact.v1",
            "device_policy": "CPU_PREFERRED",
            "runtime_backend": "python",
            "runtime_package_id": PACKAGE,
            "supported_devices": ["cpu"],
            "supported_precisions": ["not_applicable"],
            "max_batch_size": 32,
            "cancellation_supported": True,
            "resource_measurements": [],
        }
    )


def python_capability() -> RuntimeCapabilities:
    return RuntimeCapabilities.seal(
        {
            "runtime_package_id": PACKAGE,
            "runtime_backend": "python",
            "runtime_package_name": "photo-search-runtime",
            "runtime_package_version": "1",
            "python_version": "3.12.10",
            "platform": "windows-x86_64",
            "driver_version": None,
            "providers": [
                {
                    "name": "python-stdlib",
                    "device": "cpu",
                    "precisions": ["not_applicable"],
                    "available": True,
                    "health": "healthy",
                    "health_checked_at": 1_000,
                    "evidence_id": None,
                }
            ],
            "health_status": "healthy",
            "health_checked_at": 1_000,
            "measured_at": 1_000,
            "evidence_ids": [],
            "failure_code": None,
        }
    )


def scheduler_for(env: Any) -> engine.Scheduler:
    scheduler = engine.Scheduler(env.service.catalog, [non_model_spec()])
    scheduler.record_capability(python_capability(), now_ms=env.clock.ms)
    spec = non_model_spec()
    scheduler.record_qualification(
        processor_id=spec.processor_id,
        spec_revision=spec.spec_revision,
        device="cpu",
        precision="not_applicable",
        provider_name="python-stdlib",
        runtime_package_id=PACKAGE,
        now_ms=env.clock.ms,
    )
    return scheduler


def enqueue_one(env: Any, *, subject_id: str = "1") -> int:
    with env.service.catalog.write() as connection:
        return jobs.enqueue(
            connection,
            subject_type="asset",
            subject_id=subject_id,
            input_revision="rev-1",
            task_type="geo_extract",
            processor_id=PROCESSOR,
            now_ms=env.clock.ms,
        )


def fake_executor(**overrides: Any):
    def run(request: dict[str, Any]) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "job_id": request["job_id"],
            "lease_token": request["lease_token"],
            "input_revision": request["input_revision"],
            "task_type": request["task_type"],
            "processor_id": request["processor_id"],
            "state": "SUCCEEDED",
            "resolved_device": overrides.get("device") or request["resolved_device"],
            "runtime_provider": overrides.get("provider") or request["runtime_provider"],
            "precision": overrides.get("precision") or request["precision"],
            "progress": 1,
            "artifacts": [],
            "error": None,
            "completed_at": 2_000,
        }

    return run


def test_pause_and_resume_are_idempotent_and_persistent(api_env) -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await api_env.authenticate(client)
        headers = api_env.post_headers(csrf)
        first = await client.post("/api/v1/scheduler/pause", headers=headers)
        second = await client.post("/api/v1/scheduler/pause", headers=headers)
        resumed = await client.post("/api/v1/scheduler/resume", headers=headers)
        again = await client.post("/api/v1/scheduler/resume", headers=headers)
        return {
            "first": first.json(),
            "second": second.json(),
            "resumed": resumed.json(),
            "again": again.json(),
            "codes": [first.status_code, second.status_code, resumed.status_code, again.status_code],
        }

    body = api_env.scenario(scenario)
    assert body["codes"] == [200, 200, 200, 200]
    assert body["first"]["state"] == "paused"
    assert body["first"]["changed"] is True
    assert body["second"]["changed"] is False
    assert body["second"]["revision"] == body["first"]["revision"]
    assert body["resumed"]["state"] == "running" and body["resumed"]["changed"] is True
    assert body["again"]["changed"] is False
    assert body["again"]["revision"] == body["resumed"]["revision"]
    # The persisted row is the authority, not the response body.
    row = api_env.service.catalog.read_one("SELECT paused, revision FROM scheduler_state")
    assert row["paused"] == 0 and row["revision"] == body["resumed"]["revision"]


def test_a_pause_from_the_api_stops_the_scheduler_immediately(api_env) -> None:
    scheduler = scheduler_for(api_env)
    job_id = enqueue_one(api_env)

    async def scenario(client: AsyncClient) -> None:
        csrf = await api_env.authenticate(client)
        response = await client.post(
            "/api/v1/scheduler/pause", headers=api_env.post_headers(csrf)
        )
        assert response.status_code == 200, response.text

    api_env.scenario(scenario)
    assert scheduler.run_once(fake_executor(), worker_id="w1", now_ms=api_env.clock.ms) is False
    assert api_env.service.catalog.read_one(
        "SELECT state FROM jobs WHERE job_id = ?", (job_id,)
    )["state"] == "QUEUED"

    async def resume(client: AsyncClient) -> None:
        csrf = await api_env.authenticate(client)
        response = await client.post(
            "/api/v1/scheduler/resume", headers=api_env.post_headers(csrf)
        )
        assert response.status_code == 200, response.text

    api_env.scenario(resume)
    assert scheduler.run_once(fake_executor(), worker_id="w1", now_ms=api_env.clock.ms) is True
    assert api_env.service.catalog.read_one(
        "SELECT state FROM jobs WHERE job_id = ?", (job_id,)
    )["state"] == "SUCCEEDED"


def test_scheduler_endpoints_enforce_session_origin_csrf_and_host(api_env) -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        anonymous = await client.post("/api/v1/scheduler/pause")
        csrf = await api_env.authenticate(client)
        no_csrf = await client.post(
            "/api/v1/scheduler/pause", headers={"Origin": "http://127.0.0.1"}
        )
        bad_origin = await client.post(
            "/api/v1/scheduler/pause",
            headers=api_env.post_headers(csrf, origin="http://evil.example"),
        )
        bad_host = await client.post(
            "/api/v1/scheduler/pause",
            headers=api_env.post_headers(csrf, host="evil.example"),
        )
        ok = await client.post("/api/v1/scheduler/pause", headers=api_env.post_headers(csrf))
        return {
            "codes": [
                anonymous.status_code,
                no_csrf.status_code,
                bad_origin.status_code,
                bad_host.status_code,
                ok.status_code,
            ],
            "bodies": [
                anonymous.json()["code"],
                no_csrf.json()["code"],
                bad_origin.json()["code"],
                bad_host.json()["code"],
            ],
        }

    body = api_env.scenario(scenario)
    assert body["codes"] == [401, 403, 403, 403, 200]
    assert body["bodies"] == [
        "SESSION_REQUIRED",
        "CSRF_INVALID",
        "ORIGIN_NOT_ALLOWED",
        "HOST_NOT_ALLOWED",
    ]


def test_jobs_projection_carries_the_dispatch_summary_without_credentials(api_env) -> None:
    scheduler = scheduler_for(api_env)
    blocked = enqueue_one(api_env, subject_id="blocked")
    # The blocked job cannot block the same processor's other job, so block it by removing
    # the qualification first: the projection must show why it is blocked.
    with api_env.service.catalog.write() as connection:
        connection.execute("DELETE FROM runtime_qualification_evidence")
    assert scheduler.run_once(fake_executor(), worker_id="w1", now_ms=api_env.clock.ms) is False

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        await api_env.authenticate(client)
        listing = await client.get("/api/v1/jobs")
        detail = await client.get(f"/api/v1/jobs/{blocked}")
        assert listing.status_code == 200, listing.text
        assert detail.status_code == 200, detail.text
        return {"list": listing.json(), "detail": detail.json()}

    body = api_env.scenario(scenario)
    record = body["list"]["jobs"][0]
    assert record["job_id"] == blocked
    assert record["state"] == "BLOCKED"
    assert record["requested_device"] == "auto"
    assert record["blocking_reason"] and record["resolved_device"] is None
    assert body["detail"]["blocking_reason"] == record["blocking_reason"]
    serialised = json.dumps(body, ensure_ascii=False)
    assert "lease_token" not in serialised
    assert "worker_id" not in serialised
    assert str(api_env.data_root) not in serialised
    assert str(api_env.library) not in serialised


def test_jobs_projection_reports_the_resolved_device(api_env) -> None:
    scheduler = scheduler_for(api_env)
    enqueue_one(api_env)
    request = scheduler.claim_next(worker_id="w1", now_ms=api_env.clock.ms)
    assert request is not None
    scheduler.submit(
        engine.JobResult.from_envelope(fake_executor()(request.to_envelope())),
        now_ms=api_env.clock.ms,
    )

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        await api_env.authenticate(client)
        response = await client.get("/api/v1/jobs")
        return response.json()["jobs"][0]

    record = api_env.scenario(scenario)
    assert record["state"] == "SUCCEEDED"
    assert record["resolved_device"] == "cpu"
    assert record["runtime_provider"] == "python-stdlib"
    assert record["precision"] == "not_applicable"
    assert record["runtime_capability_revision"] == python_capability().capability_revision
    assert record["blocking_reason"] is None


def test_capabilities_reports_the_scheduler_and_runtime_health(api_env) -> None:
    scheduler = scheduler_for(api_env)
    scheduler.pause(now_ms=api_env.clock.ms, reason="diagnostic")

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        await api_env.authenticate(client)
        response = await client.get("/api/v1/capabilities")
        assert response.status_code == 200, response.text
        return response.json()

    body = api_env.scenario(scenario)
    assert body["scheduler"]["state"] == "paused"
    assert body["scheduler"]["revision"] >= 2
    assert body["runtime"]["health_status"] == "healthy"
    assert body["runtime"]["runtime_package_id"] == PACKAGE
    assert body["runtime"]["capability_revision"] == python_capability().capability_revision
    assert body["qualification_evidence"] == 1
    serialised = str(body)
    assert str(api_env.data_root) not in serialised
    assert str(api_env.library) not in serialised
    assert "python-stdlib" not in serialised


def _future_database(data_root: Path) -> Path:
    directory = data_root / "catalog"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "catalog.db"
    connection = sqlite3.connect(str(path))
    try:
        connection.executescript(
            "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            f"INSERT INTO metadata(key, value) VALUES('schema_version', "
            f"'{int(schema.SUPPORTED_SCHEMA_VERSION) + 1}');"
            "CREATE TABLE future_only (id INTEGER PRIMARY KEY);"
        )
        connection.commit()
    finally:
        connection.close()
    return path


@pytest.mark.parametrize("path", ["/api/v1/scheduler/pause", "/api/v1/scheduler/resume"])
def test_a_higher_schema_version_refuses_scheduler_writes(tmp_path: Path, path: str) -> None:
    data_root = tmp_path / "PhotoSearchData"
    _future_database(data_root)
    before = sorted(item.name for item in (data_root / "catalog").iterdir())
    app = create_app(
        settings=ApiSettings(data_root=data_root, session_token=TOKEN, allowed_hosts=ALLOWED_HOSTS)
    )

    async def scenario() -> tuple[int, dict[str, Any]]:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            session = await client.get("/api/v1/session", params={"token": TOKEN})
            csrf = session.json()["csrf_token"]
            response = await client.post(
                path,
                headers={"Origin": "http://127.0.0.1", "X-CSRF-Token": csrf},
            )
            return response.status_code, response.json()

    try:
        status, body = asyncio.run(scenario())
    finally:
        app.state.api.close()

    assert status == 409
    assert body["code"] == SCHEMA_TOO_NEW
    assert sorted(item.name for item in (data_root / "catalog").iterdir()) == before
    assert not list((data_root / "catalog").glob("*-wal"))
    assert not list((data_root / "catalog").glob("*-shm"))
