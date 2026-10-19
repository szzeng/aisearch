"""P4-02Y §5/§6/§11: the refresh pipeline, its HTTP entry and the delta-only consumers.

Everything runs on ``%TEMP%`` fixtures through the production scanner, the production
derivation entries and the real HTTP application. The only stand-ins are the deterministic
clock and - for the semantic half - one adapter object that plays both halves of the
ADR-0003 §7 boundary, exactly like P4-02O's stub: no real model, GPU or network.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from conftest import DEFAULT_SESSION_TOKEN, Clock, Usage, WallClock, write_jpeg
from httpx import ASGITransport, AsyncClient

from photo_search.api.settings import ApiSettings
from photo_search.app import create_app
from photo_search.face import contract as face_contract
from photo_search.face import service as face_service
from photo_search.indexing import semantic
from photo_search.ingestion.service import CatalogService
from photo_search.jobs import queue as jobs
from photo_search.ocr import service as ocr_service
from photo_search.refresh.cr2_recovery import (
    _published_document_matches,
    _rewind_once,
    recover_cr2_format_failures,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "tools" / "prepare_models" / "manifests" / "chinese-clip-vit-b-16.json"
DIMENSION = 512
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "testserver"})


class StubPinnedAdapter:
    """One deterministic adapter for both towers of the pinned space (P4-02O's stub shape)."""

    def __init__(self) -> None:
        self.vectors: dict[str, list[float]] = {}
        self.image_calls: list[str] = []
        self.query_calls: list[str] = []
        self._spec = semantic.semantic_spec(MANIFEST, {})

    def spec(self) -> Any:
        return self._spec

    def health(self) -> dict[str, Any]:
        return {
            "loaded": True,
            "spec": {"dimension": DIMENSION},
            "device": {"device": "cpu", "provider": "CPUExecutionProvider", "precision": "FP32"},
            "runtime": {
                "runtime_backend": "onnxruntime",
                "runtime_package": "onnxruntime-cpu",
                "runtime_version": "1.30.0",
                "providers": ["CPUExecutionProvider"],
                "cuda_available": False,
            },
        }

    @staticmethod
    def vector_for(payload: bytes) -> list[float]:
        digest = hashlib.sha256(payload).digest()
        vector = [0.0] * DIMENSION
        vector[int.from_bytes(digest[:4], "big") % DIMENSION] = 1.0
        return vector

    def encode_image(self, path: str) -> list[float]:
        self.image_calls.append(str(path))
        if "broken" in Path(path).name:
            raise RuntimeError("this fixture pretends the image tower failed")
        return self.vector_for(Path(path).read_bytes())

    def encode_queries(self, texts: Sequence[str], cancellation: Any = None) -> list[list[float]]:
        self.query_calls.extend(str(item) for item in texts)
        return [list(self.vectors[str(item)]) for item in texts]


@dataclass
class RefreshEnv:
    """A temp library plus the application over its data root."""

    tmp_path: Path
    data_root: Path
    library: Path
    clock: Clock
    wall: WallClock
    disk: Usage
    settings: ApiSettings
    app: Any
    factory: Callable[[], CatalogService]
    adapter: StubPinnedAdapter | None = None
    session_id: str | None = None
    csrf_token: str | None = None

    # ------------------------------------------------------------------- fixtures
    @classmethod
    def build(
        cls,
        tmp_path: Path,
        *,
        photos: int = 3,
        adapter: StubPinnedAdapter | None = None,
        manifest: Path | None = None,
        refresher_executor: Callable[[Any, Any, Any], Any] | None = None,
        batch_size: int = 128,
        enrich: bool = True,
    ) -> RefreshEnv:
        tmp_path = Path(tmp_path)
        data_root = tmp_path / "PhotoSearchData"
        library = tmp_path / "library"
        clock = Clock()
        wall = WallClock()
        disk = Usage()

        def factory() -> CatalogService:
            return CatalogService.open(
                data_root=data_root,
                clock=clock,
                lease_seconds=60,
                disk_usage=disk,
                wall_clock_ms=wall,
                stability_sleep=wall.advance,
            )

        service = factory()
        try:
            for index in range(photos):
                write_jpeg(library / f"photo-{index:03d}.jpg", noise=index + 1)
            service.register_source(display_name="library", root_path=library)
            service.run_scan(1)
        finally:
            service.close()
        settings = ApiSettings(
            data_root=data_root,
            bind_host="127.0.0.1",
            bind_port=80,
            session_token=DEFAULT_SESSION_TOKEN,
            allowed_hosts=ALLOWED_HOSTS,
        )
        app = create_app(
            settings=settings,
            service_factory=factory,
            encoder_factory=(None if adapter is None else (lambda: adapter)),
            refresh_manifest_path=(None if manifest is None else str(manifest)),
            refresh_batch_size=batch_size,
            refresh_enrich=enrich,
            refresher_executor=refresher_executor,
        )
        return cls(
            tmp_path=tmp_path,
            data_root=data_root,
            library=library,
            clock=clock,
            wall=wall,
            disk=disk,
            settings=settings,
            app=app,
            factory=factory,
            adapter=adapter,
        )

    # ------------------------------------------------------------------ resources
    @property
    def context(self) -> Any:
        return self.app.state.api

    def service(self) -> CatalogService:
        return self.context.service()

    def client(self) -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=self.app),
            base_url="http://127.0.0.1",
            follow_redirects=False,
        )

    def scenario(self, func):
        async def main():
            async with self.client() as client:
                return await func(client)

        return asyncio.run(main())

    async def authenticate(self, client: AsyncClient) -> str:
        if self.session_id and self.csrf_token:
            client.cookies.set("ps_session", self.session_id, domain="127.0.0.1")
            client.cookies.set("ps_csrf", self.csrf_token, domain="127.0.0.1")
            return self.csrf_token
        response = await client.get("/api/v1/session", params={"token": DEFAULT_SESSION_TOKEN})
        assert response.status_code == 200, response.text
        self.session_id = client.cookies.get("ps_session")
        self.csrf_token = str(response.json()["csrf_token"])
        return self.csrf_token

    @staticmethod
    def post_headers(csrf: str) -> dict[str, str]:
        return {"X-CSRF-Token": csrf, "Origin": "http://127.0.0.1"}

    # -------------------------------------------------------------------- refresh
    def start_refresh(self, source_ids: Sequence[int] | None = None) -> dict:
        async def start(client: AsyncClient) -> dict:
            csrf = await self.authenticate(client)
            body: dict[str, Any] = {} if source_ids is None else {"source_ids": list(source_ids)}
            response = await client.post(
                "/api/v1/library/refresh", json=body, headers=self.post_headers(csrf)
            )
            assert response.status_code == 202, response.text
            return response.json()

        return self.scenario(start)

    def refresh(self, *, source_ids: Sequence[int] | None = None, timeout: float = 180.0) -> dict:
        """POST the refresh, wait for it to finish and return the final status."""
        self.start_refresh(source_ids)
        refresher = self.context.refresher()
        assert refresher.wait_idle(timeout=timeout), "the refresh did not finish in time"
        status = refresher.status()
        assert status["state"] in ("COMPLETE", "PARTIAL_FAILED", "FAILED"), status
        return status

    def search(self, query: str, *, limit: int = 10) -> dict:
        return self.search_payload({"request_id": f"p4-02y-{abs(hash(query)) % 100000}",
                                    "text": query, "page_size": limit})

    def search_payload(self, payload: dict[str, Any]) -> dict:
        async def run(client: AsyncClient) -> dict:
            csrf = await self.authenticate(client)
            response = await client.post(
                "/api/v1/search",
                json=payload,
                headers=self.post_headers(csrf),
            )
            assert response.status_code == 200, response.text
            return response.json()

        return self.scenario(run)

    def close(self) -> None:
        self.context.close()


def query_vector_for(path: Path) -> list[float]:
    return StubPinnedAdapter.vector_for(path.read_bytes())


def asset_of(env: RefreshEnv, relative_path: str) -> int:
    row = env.service().catalog.read_one(
        "SELECT asset_id FROM file_locations WHERE relative_path = ?", (relative_path,)
    )
    assert row is not None, relative_path
    return int(row["asset_id"])


@pytest.fixture()
def env(tmp_path: Path):
    value = RefreshEnv.build(tmp_path, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        yield value
    finally:
        value.close()


@pytest.fixture()
def env_text_only(tmp_path: Path):
    value = RefreshEnv.build(tmp_path, adapter=None, manifest=None)
    try:
        yield value
    finally:
        value.close()


# --------------------------------------------------------------------- §14.20/§14.22 API
def test_source_build_is_queued_while_a_daily_refresh_is_running(tmp_path: Path) -> None:
    gate = threading.Event()
    entered = threading.Event()
    calls = []

    def executor(service: Any, status: Any, source_ids: Any) -> dict:
        calls.append((source_ids, status.snapshot()["mode"]))
        if len(calls) == 1:
            entered.set()
            assert gate.wait(10)
        status.finish(state="COMPLETE", elapsed_ms=0)
        return {}

    env = RefreshEnv.build(tmp_path, refresher_executor=executor)
    try:
        # Register a new synthetic library while the existing library's daily run waits.
        async def scenario(client: AsyncClient) -> None:
            csrf = await env.authenticate(client)
            headers = env.post_headers(csrf)
            first = await client.post("/api/v1/library/refresh", json={}, headers=headers)
            assert first.status_code == 202
            assert entered.wait(5)
            library = tmp_path / "new-library"
            write_jpeg(library / "new.jpg", noise=42)
            registered = await client.post(
                "/api/v1/sources",
                json={"display_name": "new-library", "root_path": str(library)},
                headers=headers,
            )
            assert registered.status_code == 201, registered.text
            source_id = registered.json()["source_id"]
            payload = {"mode": "build", "source_ids": [source_id]}
            queued = await client.post("/api/v1/library/refresh", json=payload, headers=headers)
            assert queued.status_code == 202
            body = queued.json()
            assert body["accepted"] is True and body["queued"] is True
            assert body["pending_builds"] == [[source_id]]
            assert body["refresh_id"] == first.json()["refresh_id"]
            duplicate = await client.post("/api/v1/library/refresh", json=payload, headers=headers)
            assert duplicate.json()["accepted"] is False
            assert duplicate.json()["queued"] is True
            observed = (await client.get("/api/v1/library/refresh")).json()
            assert observed["pending_builds"] == [[source_id]]
            assert observed["running"] is True
            renamed = await client.patch(
                f"/api/v1/sources/{source_id}", json={"display_name": "renamed-library"},
                headers=headers,
            )
            assert renamed.status_code == 200
            assert renamed.json()["display_name"] == "renamed-library"
            assert env.context.refresher().status()["pending_builds"] == [[source_id]]
            assert calls == [(None, "refresh")]
            gate.set()

        env.scenario(scenario)
        assert env.context.refresher().wait_idle(timeout=10)
        assert calls == [(None, "refresh"), ([2], "build")]
        assert env.context.refresher().status()["pending_builds"] == []
    finally:
        gate.set()
        env.close()


def test_a_double_click_never_starts_a_second_refresh(tmp_path: Path) -> None:
    gate = threading.Event()
    entered = threading.Event()
    adapter = StubPinnedAdapter()

    def executor(service: Any, status: Any, source_ids: Any) -> dict:
        entered.set()
        assert gate.wait(timeout=60), "the test never released the refresh"
        from photo_search.refresh.pipeline import refresh_library

        return refresh_library(
            service,
            status,
            source_ids=source_ids,
            encoder_factory=lambda: adapter,
            manifest_path=MANIFEST,
        )

    env = RefreshEnv.build(
        tmp_path, adapter=adapter, manifest=MANIFEST, refresher_executor=executor
    )
    try:
        async def scenario(client: AsyncClient) -> dict:
            csrf = await env.authenticate(client)
            started = await client.post(
                "/api/v1/library/refresh", json={}, headers=env.post_headers(csrf)
            )
            assert entered.wait(timeout=30), "the refresh worker never started"
            duplicate = await client.post(
                "/api/v1/library/refresh", json={}, headers=env.post_headers(csrf)
            )
            status = await client.get("/api/v1/library/refresh")
            return {"started": started, "duplicate": duplicate, "status": status}

        result = env.scenario(scenario)
        first = result["started"].json()
        assert result["started"].status_code == 202
        assert first["accepted"] is True
        assert first["running"] is True
        assert first["state"] in ("SCANNING", "INDEXING", "SEARCH_READY", "ENRICHING")
        assert first["source_ids"] is None
        assert first["batch_size"] == 128
        # Counts and stage names only: the routing id lists stay internal (P4-02Y §4).
        assert "content_delta" not in first["counters"]
        assert "location_delta" not in first["counters"]

        second = result["duplicate"].json()
        assert result["duplicate"].status_code == 202
        assert second["accepted"] is False
        assert second["refresh_id"] == first["refresh_id"]
        assert second["running"] is True
        assert result["status"].json()["refresh_id"] == first["refresh_id"]
        scans = 0
    finally:
        gate.set()
        assert env.context.refresher().wait_idle(timeout=180)
        scans = len(env.service().catalog.read_all("SELECT scan_id FROM scan_runs"))
        env.close()
    # A duplicate click never started a second pass: exactly one scan_run beyond the initial
    # complete scan of the fixture.
    assert scans == 2


def test_the_refresh_entry_is_additive_next_to_the_full_scan(env: RefreshEnv) -> None:
    """The 202 answer and the read-only status keep their documented shape."""

    async def scenario(client: AsyncClient) -> dict:
        csrf = await env.authenticate(client)
        started = await client.post(
            "/api/v1/library/refresh", json={}, headers=env.post_headers(csrf)
        )
        status = await client.get("/api/v1/library/refresh")
        return {"started": started, "status": status}

    result = env.scenario(scenario)
    assert result["started"].status_code == 202
    first = result["started"].json()
    assert first["accepted"] is True
    assert first["running"] is True
    assert first["state"] in ("SCANNING", "INDEXING", "SEARCH_READY", "ENRICHING")
    assert first["source_ids"] is None
    assert first["batch_size"] == 128
    assert "content_delta" not in first["counters"]
    assert "location_delta" not in first["counters"]
    assert result["status"].status_code == 200
    assert result["status"].json()["refresh_id"] == first["refresh_id"]

    refresher = env.context.refresher()
    assert refresher.wait_idle(timeout=180)
    # The additive entry did not disturb the frozen complete-verification entry.
    assert first["state"] in ("SCANNING", "INDEXING", "SEARCH_READY", "ENRICHING")
    assert len(env.service().catalog.read_all("SELECT scan_id FROM scan_runs")) == 2
    assert env.service().catalog.read_one(
        "SELECT COUNT(*) AS n FROM jobs WHERE task_type = 'catalog_scan'"
    )["n"] == 2


def test_refresh_status_is_idle_before_the_first_run(env: RefreshEnv) -> None:
    async def scenario(client: AsyncClient) -> dict:
        await env.authenticate(client)
        response = await client.get("/api/v1/library/refresh")
        assert response.status_code == 200
        return response.json()

    body = env.scenario(scenario)
    assert body["state"] == "IDLE"
    assert body["refresh_id"] is None
    assert body["running"] is False
    assert body["batch_size"] == 128
    assert body["counters"]["hash_targets"] == 0


def test_the_sources_page_wires_the_refresh_entry(env: RefreshEnv) -> None:
    """§14.22, asset level: the page carries the button and the client calls the product API.

    A browser-driven check is not available in this environment (the same ``BROWSER_TIMING
    BLOCKED`` limit P4-02W recorded); this asserts what the served shell and script really
    contain - the additive product entry and the stage names the client renders.
    """

    async def scenario(client: AsyncClient) -> dict[str, str]:
        await env.authenticate(client)
        shell = await client.get("/")
        script = await client.get("/web/app.js")
        assert shell.status_code == 200 and script.status_code == 200
        return {"shell": shell.text, "script": script.text}

    served = env.scenario(scenario)
    assert 'id="refresh-library"' in served["shell"]
    assert 'id="refresh-status"' in served["shell"]
    assert "同步全部变化" in served["shell"]
    assert '"/library/refresh"' in served["script"]
    assert "SEARCH_READY" in served["script"]
    assert "ENRICHING" in served["script"]
    assert "COMPLETE" in served["script"]
    # The complete-verification entry keeps its own wording and endpoint.
    assert "完整校验扫描" in served["script"]
    assert "/scan" in served["script"]


def test_the_refresh_requires_a_session_and_a_csrf_token(env: RefreshEnv) -> None:
    async def scenario(client: AsyncClient) -> dict:
        return {
            "anonymous": await client.post("/api/v1/library/refresh", json={}),
            "status_anonymous": await client.get("/api/v1/library/refresh"),
        }

    result = env.scenario(scenario)
    assert result["anonymous"].status_code == 401
    assert result["status_anonymous"].status_code == 401


# ------------------------------------------------------------- §14.15 SEARCH_READY truth
def test_the_first_refresh_makes_the_library_searchable_and_reaches_search_ready(
    env: RefreshEnv,
) -> None:
    env.adapter.vectors["canary"] = query_vector_for(env.library / "photo-000.jpg")

    status = env.refresh()

    assert status["state"] == "COMPLETE"
    assert status["search_ready"] is True
    assert status["search_ready_basis"] == "semantic-confirmed"
    assert status["stages"]["semantic"]["state"] == "OK"
    assert status["stages"]["semantic"]["encoded"] == 3
    assert status["stages"]["semantic"]["unconfirmed_content"] == 0
    assert status["counters"]["hash_targets"] == 0
    assert status["counters"]["unchanged_reused"] == 3

    found = env.search("canary")
    assert [int(item["asset_id"]) for item in found["hits"]][:1] == [
        asset_of(env, "photo-000.jpg")
    ]


def test_a_new_photo_is_searchable_when_the_refresh_says_search_ready(env: RefreshEnv) -> None:
    assert env.refresh()["search_ready"] is True
    write_jpeg(env.library / "fresh arrival.jpg", noise=77)
    env.adapter.vectors["fresh arrival"] = query_vector_for(env.library / "fresh arrival.jpg")

    status = env.refresh()

    assert status["search_ready"] is True
    assert status["search_ready_basis"] == "semantic-confirmed"
    assert status["counters"]["hash_targets"] == 1
    assert status["counters"]["content_delta_count"] == 1
    assert status["stages"]["semantic"]["encoded"] == 1
    assert status["stages"]["semantic"]["unconfirmed_content"] == 0
    found = env.search("fresh arrival")
    assert [int(item["asset_id"]) for item in found["hits"]][:1] == [
        asset_of(env, "fresh arrival.jpg")
    ]


def test_a_build_without_a_semantic_channel_never_claims_search_ready(
    env_text_only: RefreshEnv,
) -> None:
    """No model means no searchable content in this build: say so instead of pretending.

    The scan and the projection still do their job - the photo is catalogued, named and
    correctly bound - but ``search_ready`` stays false and the run ends as ``FAILED`` with the
    reason, because a user pressing "刷新图库" cannot search that photo yet.
    """
    write_jpeg(env_text_only.library / "brand new name.jpg", noise=31)

    status = env_text_only.refresh()

    assert status["search_ready"] is False
    assert status["state"] == "FAILED"
    assert status["stages"]["semantic"]["state"] == "SKIPPED_UNAVAILABLE"
    assert status["stages"]["semantic"]["reason"] == "MANIFEST_UNCONFIGURED"
    assert status["stages"]["semantic"]["required"] == 1
    assert status["counters"]["hash_targets"] == 1
    assert status["stages"]["thumbnail"]["generated"] == 1
    assert status["counters"]["content_delta_count"] == 1


# ------------------------------------------------------------------------ §14.11/§14.12
def test_a_no_op_refresh_does_no_ai_work_and_keeps_the_generation(env: RefreshEnv) -> None:
    first = env.refresh()
    assert first["state"] == "COMPLETE"
    catalog = env.service().catalog
    snapshot = _state(catalog, env)
    encoded_before = len(env.adapter.image_calls)

    outcome = env.refresh()

    assert outcome["state"] == "COMPLETE"
    assert outcome["search_ready"] is True
    assert outcome["counters"]["enumerated"] == 3
    assert outcome["counters"]["unchanged_reused"] == 3
    assert outcome["counters"]["hash_targets"] == 0
    assert outcome["counters"]["hash_bytes"] == 0
    assert outcome["counters"]["content_delta_count"] == 0
    assert outcome["counters"]["location_delta_count"] == 0
    assert outcome["counters"]["new_assets"] == 0
    assert outcome["stages"]["semantic"]["state"] == "SKIPPED_NO_TARGETS"
    assert outcome["stages"]["semantic"]["encoded"] == 0
    assert outcome["stages"]["thumbnail"]["state"] == "SKIPPED_NO_TARGETS"
    assert outcome["stages"]["thumbnail"]["generated"] == 0
    assert outcome["stages"]["taken_time"]["state"] == "SKIPPED_NO_TARGETS"
    assert outcome["stages"]["taken_time"]["new_jobs"] == 0
    assert outcome["stages"]["gps"]["new_jobs"] == 0
    assert outcome["stages"]["face"]["state"] in ("SKIPPED_UNAVAILABLE", "SKIPPED_NO_TARGETS")
    assert outcome["stages"]["ocr"]["state"] in ("SKIPPED_UNAVAILABLE", "SKIPPED_NO_TARGETS")
    assert len(env.adapter.image_calls) == encoded_before
    assert _state(catalog, env) == snapshot


def _state(catalog: Any, env: RefreshEnv) -> dict[str, Any]:
    spec = env.adapter.spec() if env.adapter is not None else None
    return {
        "assets": catalog.read_one("SELECT COUNT(*) AS n FROM assets")["n"],
        "locations": catalog.read_one("SELECT COUNT(*) AS n FROM file_locations")["n"],
        # The refresh's own scan job is per run by design; every other job count is the
        # derived work the acceptance has to see as unchanged.
        "jobs": catalog.read_one(
            "SELECT COUNT(*) AS n FROM jobs WHERE task_type <> 'catalog_scan'"
        )["n"],
        "entries": catalog.read_one(
            "SELECT COUNT(*) AS n FROM index_entries WHERE state = 'CONFIRMED'"
        )["n"],
        "profile": catalog.read_one(
            "SELECT profile_id FROM active_search_profile WHERE state_id = 1"
        )["profile_id"],
        "generation": None
        if spec is None
        else semantic.generation_summary(
            catalog,
            space_id=str(spec.envelope["space_id"]),
            processor_id=str(spec.processor_id),
        ),
    }


def test_a_no_op_refresh_keeps_the_search_results(env: RefreshEnv) -> None:
    env.refresh()
    env.adapter.vectors["stable"] = query_vector_for(env.library / "photo-001.jpg")
    before = env.search("stable")["hits"]

    env.refresh()

    after = env.search("stable")["hits"]
    assert before and [item["asset_id"] for item in before] == [
        item["asset_id"] for item in after
    ]


# ---------------------------------------------------------- §14.12 delta-only processors
def test_a_small_delta_day_runs_every_stage_on_the_delta_only(env: RefreshEnv) -> None:
    env.refresh()
    # A library that is already in daily use has its thumbnails: the delta refresh must
    # reuse the published ones instead of regenerating them.
    env.service().generate_thumbnail()
    for index in range(5):
        write_jpeg(env.library / f"new-{index}.jpg", noise=200 + index)
    os.rename(env.library / "photo-002.jpg", env.library / "renamed-002.jpg")
    (env.library / "photo-001.jpg").unlink()
    missing_asset = asset_of(env, "photo-001.jpg")
    renamed_asset = asset_of(env, "photo-002.jpg")
    env.adapter.vectors["day delta"] = query_vector_for(env.library / "new-0.jpg")
    jobs_before = _processor_jobs(env)

    status = env.refresh()

    assert status["state"] == "COMPLETE"
    assert status["search_ready"] is True
    assert status["counters"]["enumerated"] == 7
    assert status["counters"]["unchanged_reused"] == 1
    assert status["counters"]["hash_targets"] == 6
    assert status["counters"]["new_assets"] == 5
    assert status["counters"]["rebound_assets"] == 1
    assert status["counters"]["locations_missing"] == 2
    assert status["counters"]["content_delta_count"] == 6
    assert status["counters"]["location_delta_count"] >= 7

    # Only the genuinely new content is encoded again; the rename reuses its embedding and
    # the deleted position keeps its asset.
    assert status["stages"]["semantic"]["encoded"] == 5
    assert status["stages"]["semantic"]["unconfirmed_content"] == 0
    assert status["stages"]["thumbnail"]["generated"] == 5
    assert status["stages"]["thumbnail"]["already_published"] == 1
    # taken-time's input revision carries the recorded observations, so it is a
    # location-delta consumer: the five new photos, the renamed position *and* the deleted
    # one (whose availability changed) get a new logical job.
    assert status["stages"]["taken_time"]["new_jobs"] == 7
    # GPS is a content-delta consumer keyed on the content hash: the five new photos and the
    # renamed one (which never had a GPS job) get one job each, the deleted asset none.
    assert status["stages"]["gps"]["new_jobs"] == 6
    assert status["stages"]["gps"]["state"] == "OK"
    jobs_after = _processor_jobs(env)
    assert jobs_after["image_embedding"] - jobs_before["image_embedding"] == 5
    assert jobs_after["geo_extract"] - jobs_before.get("geo_extract", 0) == 6
    assert (
        jobs_after["taken_time_extract"] - jobs_before.get("taken_time_extract", 0) == 7
    )
    row = env.service().catalog.read_one(
        "SELECT availability, asset_id FROM file_locations WHERE relative_path = 'photo-001.jpg'"
    )
    assert str(row["availability"]) == "MISSING"
    assert int(row["asset_id"]) == missing_asset
    assert asset_of(env, "renamed-002.jpg") == renamed_asset
    assert env.search("day delta")["hits"]

    # A second refresh of the same state must not repeat any of it.
    repeat = env.refresh()
    assert repeat["stages"]["semantic"]["encoded"] == 0
    assert repeat["stages"]["thumbnail"]["generated"] == 0
    assert repeat["stages"]["taken_time"]["new_jobs"] == 0
    assert repeat["stages"]["gps"]["new_jobs"] == 0
    assert _processor_jobs(env) == jobs_after


def _processor_jobs(env: RefreshEnv) -> dict[str, int]:
    """Per-task job counts without the refresh's own scan jobs (one per source per run)."""
    rows = env.service().catalog.read_all(
        "SELECT task_type, COUNT(*) AS n FROM jobs WHERE task_type <> 'catalog_scan' "
        "GROUP BY task_type ORDER BY task_type"
    )
    return {str(row["task_type"]): int(row["n"]) for row in rows}


def test_the_enrichment_stages_never_walk_the_whole_library(env: RefreshEnv) -> None:
    # The fixture's initial complete scan already confirmed every binding, so the first
    # refresh has no content delta: the enrichment stages have nothing to do even though the
    # semantic index itself still has to be built once.
    opening = env.refresh()
    assert opening["stages"]["semantic"]["encoded"] == 3
    assert opening["stages"]["thumbnail"]["state"] == "SKIPPED_NO_TARGETS"
    assert opening["stages"]["thumbnail"]["assets"] == 0
    assert opening["stages"]["taken_time"]["state"] == "SKIPPED_NO_TARGETS"
    assert opening["stages"]["gps"]["state"] == "SKIPPED_NO_TARGETS"
    assert opening["stages"]["face"]["state"] in ("SKIPPED_UNAVAILABLE", "SKIPPED_NO_TARGETS")

    write_jpeg(env.library / "one-more.jpg", noise=61)

    delta = env.refresh()

    assert delta["counters"]["enumerated"] == 4
    assert delta["stages"]["thumbnail"]["assets"] == 1
    assert delta["stages"]["taken_time"]["targets"] == 1
    assert delta["stages"]["gps"]["targets"] == 1
    assert delta["stages"]["semantic"]["encoded"] == 1


def test_cr2_format_failure_is_recovered_once_and_original_error_is_retained(
    env: RefreshEnv,
) -> None:
    catalog = env.service().catalog
    asset = catalog.read_one(
        "SELECT a.asset_id,l.location_id,l.relative_path FROM assets a "
        "JOIN file_locations l ON l.asset_id=a.asset_id WHERE l.availability='PRESENT' "
        "ORDER BY a.asset_id LIMIT 1"
    )
    assert asset is not None
    asset_id = int(asset["asset_id"])
    original = env.library / str(asset["relative_path"])
    cr2_path = original.with_suffix(".cr2")
    if cr2_path.exists():
        cr2_path.unlink()
    original.rename(cr2_path)
    content = b"II*\x00\x08\x00\x00\x00CR\x02\x00synthetic-cr2"
    cr2_path.write_bytes(content)
    revision = hashlib.sha256(content).hexdigest()
    with catalog.write() as connection:
        connection.execute(
            "UPDATE assets SET sha256=?,byte_size=? WHERE asset_id=?",
            (revision, len(content), asset_id),
        )
        relative = cr2_path.relative_to(env.library).as_posix()
        connection.execute(
            "UPDATE file_locations SET relative_path=?,path_key=? WHERE location_id=?",
            (relative, relative.casefold(), int(asset["location_id"])),
        )
        job_id = jobs.enqueue(
            connection,
            subject_type="asset",
            subject_id=str(asset_id),
            input_revision=revision,
            task_type=face_contract.FACE_TASK_TYPE,
            processor_id=face_contract.FACE_PROCESSOR_ID,
            now_ms=catalog.now_ms(),
        )
        connection.execute(
            "UPDATE jobs SET state='FAILED',attempts=1,last_error_code='FORMAT_UNSUPPORTED',"
            "last_error_message='legacy CR2 format unsupported' WHERE job_id=?",
            (job_id,),
        )

    # A source revision can change after candidate classification. The transactional CAS
    # must recheck the current asset SHA before writing a durable marker or requeueing.
    stale_job = catalog.read_one("SELECT * FROM jobs WHERE job_id=?", (job_id,))
    assert stale_job is not None
    with catalog.write() as connection:
        connection.execute("UPDATE assets SET sha256=? WHERE asset_id=?", ("f" * 64, asset_id))
    assert _rewind_once(catalog, stale_job) is False
    assert catalog.read_one(
        "SELECT 1 FROM cr2_job_recoveries WHERE job_id=?", (job_id,)
    ) is None
    assert catalog.read_one("SELECT state FROM jobs WHERE job_id=?", (job_id,))["state"] == "FAILED"

    with catalog.write() as connection:
        connection.execute("UPDATE assets SET sha256=? WHERE asset_id=?", (revision, asset_id))

    first = recover_cr2_format_failures(
        catalog,
        data_root=env.data_root,
        asset_ids=[asset_id],
        source_ids=[1],
        task_type=face_contract.FACE_TASK_TYPE,
        processor_id=face_contract.FACE_PROCESSOR_ID,
        artifact_revision=face_contract.FACE_ARTIFACT_REVISION,
    )
    assert first["cr2_recovery_jobs"] == 1
    assert catalog.read_one("SELECT state FROM jobs WHERE job_id=?", (job_id,))["state"] == "QUEUED"
    saved = catalog.read_one("SELECT * FROM cr2_job_recoveries WHERE job_id=?", (job_id,))
    assert saved["source_error_code"] == "FORMAT_UNSUPPORTED"
    assert saved["source_error_message"] == "legacy CR2 format unsupported"

    # A later failure on the same logical job is not automatically resurrected again.
    with catalog.write() as connection:
        connection.execute(
            "UPDATE jobs SET state='FAILED',attempts=1,last_error_code='FORMAT_UNSUPPORTED',"
            "last_error_message='still unsupported' WHERE job_id=?",
            (job_id,),
        )
    second = recover_cr2_format_failures(
        catalog,
        data_root=env.data_root,
        asset_ids=[asset_id],
        source_ids=[1],
        task_type=face_contract.FACE_TASK_TYPE,
        processor_id=face_contract.FACE_PROCESSOR_ID,
        artifact_revision=face_contract.FACE_ARTIFACT_REVISION,
    )
    assert second["cr2_recovery_jobs"] == 0
    assert second["cr2_recovery_already_attempted"] == 1
    assert catalog.read_one("SELECT state FROM jobs WHERE job_id=?", (job_id,))["state"] == "FAILED"


def test_asset_target_queries_and_face_job_lookup_are_bounded(env: RefreshEnv, monkeypatch) -> None:
    catalog = env.service().catalog
    asset_ids = [
        int(row["asset_id"])
        for row in catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id LIMIT 2")
    ]
    assert asset_ids
    calls: list[tuple[str, tuple[Any, ...]]] = []
    original_read_all = catalog.read_all

    def capture(sql: str, parameters: Sequence[Any] = ()):
        calls.append((sql, tuple(parameters)))
        return original_read_all(sql, parameters)

    monkeypatch.setattr(catalog, "read_all", capture)
    for targeter in (face_service.face_targets, ocr_service.ocr_targets):
        selected = targeter(catalog, asset_ids=asset_ids)
        assert [row["asset_id"] for row in selected] == asset_ids
        sql, parameters = calls[-1]
        assert "a.asset_id IN" in sql
        assert tuple(asset_ids) == parameters[: len(asset_ids)]
        assert "LIMIT" not in sql or "a.asset_id IN" in sql

    assert face_service.face_targets(catalog, asset_ids=[]) == []
    assert len(calls) == 2  # empty scopes issue no SQL

    targets = [
        {"asset_id": asset_ids[0], "input_revision": "a" * 64},
        {"asset_id": asset_ids[1], "input_revision": "b" * 64},
    ]
    with catalog.write() as connection:
        for target in targets:
            jobs.enqueue(
                connection,
                subject_type="asset",
                subject_id=str(target["asset_id"]),
                input_revision=str(target["input_revision"]),
                task_type=face_contract.FACE_TASK_TYPE,
                processor_id=face_contract.FACE_PROCESSOR_ID,
                now_ms=catalog.now_ms(),
            )
    found = face_service._job_ids_for_targets(
        catalog,
        targets,
        task_type=face_contract.FACE_TASK_TYPE,
        processor_id=face_contract.FACE_PROCESSOR_ID,
    )
    assert len(found) == 2
    assert len(calls) == 3  # one batched lookup, not one read per asset
    assert "subject_id IN" in calls[-1][0]


def test_cr2_published_document_requires_matching_profile_identity() -> None:
    face_doc = {
        "processor_id": face_contract.FACE_PROCESSOR_ID,
        "input_revision": "a" * 64,
    }
    assert _published_document_matches(
        face_doc,
        task_type=face_contract.FACE_TASK_TYPE,
        processor_id=face_contract.FACE_PROCESSOR_ID,
        input_revision="a" * 64,
        artifact_revision=face_contract.FACE_ARTIFACT_REVISION,
    )
    assert not _published_document_matches(
        {**face_doc, "processor_id": "other-face-profile"},
        task_type=face_contract.FACE_TASK_TYPE,
        processor_id=face_contract.FACE_PROCESSOR_ID,
        input_revision="a" * 64,
        artifact_revision=face_contract.FACE_ARTIFACT_REVISION,
    )
    assert not _published_document_matches(
        {
            **face_doc,
            "spec_revision": face_contract.FACE_DETAIL_SPEC_REVISION,
            "artifact_revision": face_contract.FACE_DETAIL_ARTIFACT_REVISION,
        },
        task_type=face_contract.FACE_TASK_TYPE,
        processor_id=face_contract.FACE_PROCESSOR_ID,
        input_revision="a" * 64,
        artifact_revision=face_contract.FACE_ARTIFACT_REVISION,
    )


def test_a_failing_face_or_ocr_processor_never_blocks_search_ready(env: RefreshEnv) -> None:
    status = env.refresh()
    assert status["search_ready"] is True
    for name in ("face", "ocr"):
        payload = status["stages"][name]
        assert payload["state"] in ("SKIPPED_UNAVAILABLE", "OK", "SKIPPED_NO_TARGETS"), payload
    assert status["state"] == "COMPLETE"
    assert status["errors"] == [] or all(
        entry["code"] == "RUNTIME_CAPABILITY_UNAVAILABLE" for entry in status["errors"]
    )


def test_one_refused_photo_never_blocks_the_rest(env: RefreshEnv) -> None:
    """Card §12: a single bad photo must not stop the other new assets or the search path."""
    assert env.refresh()["search_ready"] is True
    write_jpeg(env.library / "good-new.jpg", noise=88)
    write_jpeg(env.library / "broken-new.jpg", noise=89)
    env.adapter.vectors["good new"] = query_vector_for(env.library / "good-new.jpg")

    status = env.refresh()

    assert status["search_ready"] is True
    assert status["state"] == "PARTIAL_FAILED"
    assert status["stages"]["semantic"]["required"] == 2
    assert status["stages"]["semantic"]["confirmed"] == 1
    assert status["stages"]["semantic"]["failed_assets"] == 1
    assert status["stages"]["semantic"]["unconfirmed_content"] == 0
    assert status["stages"]["thumbnail"]["generated"] == 2
    hits = env.search("good new")["hits"]
    assert [int(item["asset_id"]) for item in hits][:1] == [asset_of(env, "good-new.jpg")]
    # The refused photo keeps its catalogued identity and a durable job to retry.
    assert asset_of(env, "broken-new.jpg") > 0
    assert status["counters"]["hash_targets"] == 2
    assert "semantic-content" in status["failed_stages"]


def test_a_non_image_change_is_out_of_scope_not_a_failure(env: RefreshEnv) -> None:
    """A changed document in the source tree is not an image the index is about (P4-02Q)."""
    assert env.refresh()["search_ready"] is True
    notes = env.library / "notes.txt"
    notes.write_text("not a photo", encoding="utf-8")
    os.utime(notes, ns=((1_700_000_000_000 - 3_600_000) * 1_000_000,) * 2)

    status = env.refresh()

    assert status["search_ready"] is True
    assert status["state"] == "COMPLETE"
    assert status["counters"]["new_assets"] == 0
    assert status["stages"]["semantic"]["required"] == 0
    assert status["stages"]["semantic"]["out_of_scope"]["labels"] == {}


# ------------------------------------------------------------- §14.17/§14.18 sources
def test_a_source_scoped_refresh_leaves_the_other_source_alone(tmp_path: Path) -> None:
    env = RefreshEnv.build(tmp_path, photos=2, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        second = tmp_path / "second-library"
        write_jpeg(second / "other-0.jpg", noise=5)
        other = env.service().register_source(display_name="second", root_path=second)
        env.refresh()
        write_jpeg(env.library / "first-extra.jpg", noise=9)
        write_jpeg(second / "other-extra.jpg", noise=11)
        scans_before = len(env.service().catalog.read_all("SELECT scan_id FROM scan_runs"))

        status = env.refresh(source_ids=[1])

        assert status["counters"]["sources_requested"] == 1
        assert status["counters"]["sources_scanned"] == 1
        assert status["counters"]["hash_targets"] == 1
        scans = env.service().catalog.read_all(
            "SELECT scan_id, source_id FROM scan_runs ORDER BY scan_id"
        )
        assert len(scans) == scans_before + 1
        assert int(scans[-1]["source_id"]) == 1
        other_row = env.service().catalog.read_one(
            "SELECT COUNT(*) AS n FROM file_locations WHERE source_id = ? AND asset_id IS NULL",
            (int(other["source_id"]),),
        )
        assert int(other_row["n"]) == 0
        assert (
            env.service().catalog.read_one(
                "SELECT COUNT(*) AS n FROM file_locations WHERE relative_path = 'other-extra.jpg'"
            )["n"]
            == 0
        )
    finally:
        env.close()


def test_a_global_refresh_reports_a_cross_source_move_as_location_changes(tmp_path: Path) -> None:
    adapter = StubPinnedAdapter()
    env = RefreshEnv.build(tmp_path, photos=2, adapter=adapter, manifest=MANIFEST)
    try:
        second = tmp_path / "second-library"
        second.mkdir()
        env.service().register_source(display_name="second", root_path=second)
        initial = env.refresh()
        assert initial["counters"]["sources_scanned"] == 2

        source_path = env.library / "photo-000.jpg"
        moved_asset = asset_of(env, "photo-000.jpg")
        os.rename(source_path, second / source_path.name)

        status = env.refresh()

        assert status["counters"]["sources_requested"] == 2
        assert status["counters"]["sources_scanned"] == 2
        assert status["counters"]["locations_added"] == 1
        assert status["counters"]["locations_missing"] == 1
        assert status["counters"]["new_assets"] == 0
        assert status["counters"]["rebound_assets"] == 1
        rows = env.service().catalog.read_all(
            "SELECT source_id, relative_path, availability, asset_id "
            "FROM file_locations WHERE relative_path = 'photo-000.jpg' ORDER BY source_id"
        )
        assert [(int(row["source_id"]), str(row["availability"]), int(row["asset_id"])) for row in rows] == [
            (1, "MISSING", moved_asset),
            (2, "PRESENT", moved_asset),
        ]
    finally:
        env.close()


def test_an_offline_source_does_not_stop_the_others(tmp_path: Path) -> None:
    env = RefreshEnv.build(tmp_path, photos=2, adapter=StubPinnedAdapter(), manifest=MANIFEST)
    try:
        second = tmp_path / "second-library"
        write_jpeg(second / "other-0.jpg", noise=5)
        env.service().register_source(display_name="second", root_path=second)
        env.refresh()

        offline = tmp_path / "library-offline"
        os.rename(env.library, offline)
        write_jpeg(second / "other-extra.jpg", noise=13)
        try:
            status = env.refresh()
        finally:
            os.rename(offline, env.library)

        assert status["counters"]["sources_failed"] == 1
        assert status["counters"]["sources_scanned"] == 1
        assert status["counters"]["hash_targets"] == 1
        assert [entry["code"] for entry in status["errors"]] == ["SOURCE_OFFLINE"]
        # The offline source produced no MISSING fact.
        rows = env.service().catalog.read_all(
            "SELECT availability FROM file_locations WHERE source_id = 1"
        )
        assert {str(row["availability"]) for row in rows} == {"PRESENT"}
        assert (
            env.service().catalog.read_one(
                "SELECT COUNT(*) AS n FROM file_locations WHERE relative_path = 'other-extra.jpg'"
            )["n"]
            == 1
        )
        assert status["search_ready"] is True
    finally:
        env.close()


# ----------------------------------------------------------------- §14.19/§14.21 recovery
def test_an_interrupted_scan_is_continued_by_the_next_refresh(env: RefreshEnv) -> None:
    from photo_search.ingestion.scanner import SCAN_MODE_FAST, ScanInterrupted, execute_scan

    env.adapter.vectors["canary"] = query_vector_for(env.library / "photo-000.jpg")
    assert env.refresh()["state"] == "COMPLETE"
    catalog = env.service().catalog
    first_assets = int(catalog.read_one("SELECT COUNT(*) AS n FROM assets")["n"])

    # A refresh whose scan is killed mid-content-pass leaves exactly the durable facts of the
    # completed positions; nothing negative and nothing half-bound.
    write_jpeg(env.library / "arrival.jpg", noise=41)
    env.adapter.vectors["arrival"] = query_vector_for(env.library / "arrival.jpg")
    with pytest.raises(ScanInterrupted):
        execute_scan(env.service(), 1, mode=SCAN_MODE_FAST, interrupt_after=0)
    assert (
        catalog.read_one(
            "SELECT COUNT(*) AS n FROM file_locations WHERE availability = 'PRESENT' "
            "AND asset_id IS NULL"
        )["n"]
        == 1
    )
    assert catalog.read_one("SELECT COUNT(*) AS n FROM assets")["n"] == first_assets

    env.clock.advance(61_000)
    status = env.refresh()

    assert status["state"] == "COMPLETE"
    assert status["search_ready"] is True
    assert status["counters"]["hash_targets"] == 1
    assert status["stages"]["semantic"]["encoded"] == 1
    assert int(catalog.read_one("SELECT COUNT(*) AS n FROM assets")["n"]) == first_assets + 1
    assert catalog.read_one(
        "SELECT COUNT(*) AS n FROM file_locations WHERE availability = 'PRESENT' "
        "AND asset_id IS NULL"
    )["n"] == 0
    assert catalog.read_one("SELECT COUNT(*) AS n FROM search_projection_queue")["n"] == 0
    assert env.search("arrival")["hits"]


def test_a_repeat_refresh_after_completion_changes_nothing(env: RefreshEnv) -> None:
    assert env.refresh()["state"] == "COMPLETE"
    catalog = env.service().catalog
    before = _processor_jobs(env)

    again = env.refresh()

    assert again["counters"]["hash_targets"] == 0
    assert again["stages"]["semantic"]["encoded"] == 0
    assert _processor_jobs(env) == before
    assert (
        catalog.read_one(
            "SELECT COUNT(*) AS n FROM file_locations WHERE availability = 'PRESENT' "
            "AND asset_id IS NULL"
        )["n"]
        == 0
    )


# ------------------------------------------------------------------------- §14.21 search
def test_a_refresh_in_flight_never_refuses_a_legal_search(tmp_path: Path) -> None:
    """P4-02V keeps holding: the refresh's own writes must not turn a search into an error."""
    adapter = StubPinnedAdapter()
    releases = {"count": 0}
    entered = threading.Event()

    def executor(service: Any, status: Any, source_ids: Any) -> dict:
        from photo_search.ingestion.scanner import SCAN_MODE_FAST, execute_scan
        from photo_search.refresh.pipeline import refresh_library

        refresh_library(
            service, status, source_ids=source_ids, encoder_factory=lambda: adapter,
            manifest_path=MANIFEST, enrich=False,
        )
        entered.set()
        # Keep the writer busy with extra fast scans while the test searches.
        for _ in range(6):
            if releases["count"]:
                break
            execute_scan(service, 1, mode=SCAN_MODE_FAST)
        return status.snapshot()

    env = RefreshEnv.build(
        tmp_path, photos=60, adapter=adapter, manifest=MANIFEST, refresher_executor=executor,
        enrich=False,
    )
    try:
        assert env.refresh()["state"] == "COMPLETE"
        adapter.vectors["canary"] = query_vector_for(env.library / "photo-000.jpg")

        async def scenario(client: AsyncClient) -> list[dict]:
            csrf = await env.authenticate(client)
            await client.post(
                "/api/v1/library/refresh", json={}, headers=env.post_headers(csrf)
            )
            assert entered.wait(timeout=30), "the refresh worker never started"
            observed: list[dict] = []
            for _ in range(12):
                response = await client.post(
                    "/api/v1/search",
                    json={"request_id": "p4-02y-inflight", "text": "canary", "page_size": 5},
                    headers=env.post_headers(csrf),
                )
                observed.append({"status": response.status_code, "body": response.json()})
            releases["count"] = 1
            return observed

        observed = env.scenario(scenario)
        assert env.context.refresher().wait_idle(timeout=180)
        assert observed, "no search was issued"
        assert {item["status"] for item in observed} == {200}
        states = {str(item["body"]["state"]) for item in observed}
        assert states <= {"OK", "LAGGING"}, states
        assert not [
            item
            for item in observed
            if str(item["body"].get("state")) not in ("OK", "LAGGING")
        ]
        assert all(item["body"]["hits"] for item in observed if item["body"]["state"] == "OK")
    finally:
        releases["count"] = 1
        env.context.refresher().wait_idle(timeout=180)
        env.close()
