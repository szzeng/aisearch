"""Read-only OCR original-text API coverage using saved synthetic historical artifacts."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from p3_ocr_fixtures import Harness, write_blank_image

from photo_search.api.settings import ApiSettings
from photo_search.app import create_app
from photo_search.artifacts.store import ArtifactStore
from photo_search.ingestion.service import CatalogService
from photo_search.jobs import queue as jobs
from photo_search.ocr import contract
from photo_search.ocr import processor as ocr_processor
from photo_search.scheduler.contracts import canonical_json
from photo_search.search import documents
from photo_search.search.tokens import TOKENIZER_VERSION, index_text

HISTORY = json.loads(
    (Path(__file__).parents[1] / "fixtures/ocr/historical-v1-v2.json").read_text(encoding="utf-8")
)


@pytest.fixture()
def harness(tmp_path: Path) -> Harness:
    instance = Harness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()


def _app(harness: Harness) -> Any:
    token = "p4-ocr-display-session"
    settings = ApiSettings(
        data_root=harness.data_root, bind_host="127.0.0.1", bind_port=80,
        session_token=token,
        allowed_hosts=frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "testserver"}),
    )

    def factory() -> CatalogService:
        return CatalogService.open(
            data_root=harness.data_root, clock=harness.clock, lease_seconds=60,
            disk_usage=harness.usage, wall_clock_ms=harness.clock,
            stability_sleep=harness.clock.advance,
        )

    return create_app(settings=settings, service_factory=factory)


def _seed_historical_current(
    harness: Harness, asset_id: int, processor_id: str,
) -> dict[str, Any]:
    fixture_processor = processor_id
    if processor_id == contract.OCR_CPU_PROCESSOR_ID:
        fixture_processor = contract.OCR_V2_CPU_PROCESSOR_ID
    elif processor_id == contract.OCR_GPU_PROCESSOR_ID:
        fixture_processor = contract.OCR_V2_GPU_PROCESSOR_ID
    payload = deepcopy(next(
        sample["payload"] for sample in HISTORY["artifacts"]
        if sample["processor_id"] == fixture_processor
    ))
    payload["processor_id"] = processor_id
    payload["config_hash"], payload["engine_config"] = ocr_processor._payload_profile(processor_id)
    payload["input_revision"] = harness.sha_of(asset_id)
    payload["location_id"] = harness.catalog.read_one(
        "SELECT location_id FROM file_locations WHERE asset_id=?", (asset_id,),
    )["location_id"]
    ocr_processor.validate_payload(payload)
    ref = ocr_processor.artifact_ref(payload)
    body = canonical_json(payload).encode("utf-8")
    storage_uri = "artifacts/ocr-display-fixture/" + ref["checksum_sha256"] + ".json"
    path = harness.data_root / storage_uri
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    with harness.catalog.write() as connection:
        job_id = jobs.enqueue(
            connection, subject_type="asset", subject_id=str(asset_id),
            input_revision=payload["input_revision"], task_type=contract.OCR_TASK_TYPE,
            processor_id=processor_id, now_ms=harness.clock(),
        )
        connection.execute("UPDATE jobs SET state='SUCCEEDED' WHERE job_id=?", (job_id,))
        connection.execute(
            "INSERT INTO artifacts (artifact_id, processor_id, input_revision, artifact_type, "
            "artifact_revision, media_type, checksum_sha256, byte_size, storage_uri, job_id, "
            "lifecycle_state, created_at, updated_at) VALUES (?, ?, ?, 'structured', ?, "
            "'application/json', ?, ?, ?, ?, 'PUBLISHED', 1, 1)",
            (ref["artifact_id"], processor_id, payload["input_revision"],
             contract.OCR_ARTIFACT_REVISION, ref["checksum_sha256"], len(body), storage_uri, job_id),
        )
        region = payload["regions"][0]
        connection.execute(
            "INSERT INTO asset_ocr_text (asset_id, ocr_document_id, input_revision, location_id, "
            "text_revision, processor_id, spec_revision, model_fingerprint, artifact_id, "
            "region_count, min_confidence, mean_confidence, max_confidence, search_text, "
            "tokenizer_version, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, "
            "1, ?, ?, ?, ?, ?, 1, 1)",
            (asset_id, ocr_processor.ocr_document_id_for(
                payload["input_revision"], 1, contract.OCR_LEGACY_PROCESSOR_ID,
             ), payload["input_revision"], payload["location_id"],
             processor_id,
             ("ocr-text-ppocrv4-mobile-v1" if processor_id == contract.OCR_LEGACY_PROCESSOR_ID
              else contract.OCR_CPU_SPEC_REVISION if processor_id == contract.OCR_CPU_PROCESSOR_ID
              else contract.OCR_GPU_SPEC_REVISION if processor_id == contract.OCR_GPU_PROCESSOR_ID
              else "ocr-text-ppocrv4-mobile-" + ("cpu" if "cpu" in processor_id else "cuda") + "-fp32-v2"),
             payload["model_fingerprint"], ref["artifact_id"], region["confidence"],
             region["confidence"], region["confidence"], index_text(payload["original_text"]),
             TOKENIZER_VERSION),
        )
        connection.execute(
            "INSERT INTO asset_ocr_regions (asset_id, region_index, text_revision, box_json, confidence) "
            "VALUES (?, 0, 1, ?, ?)",
            (asset_id, canonical_json({"box": region["box"]}), region["confidence"]),
        )
        documents.sync_document(connection, asset_id, now_ms=harness.clock())
    artifact = dict(harness.catalog.read_one(
        "SELECT * FROM artifacts WHERE artifact_id=?", (ref["artifact_id"],),
    ))
    assert ArtifactStore.open(harness.data_root).read(artifact) == body
    return artifact


@pytest.mark.parametrize("processor_id", (
    contract.OCR_LEGACY_PROCESSOR_ID,
    contract.OCR_V2_CPU_PROCESSOR_ID,
    contract.OCR_V2_GPU_PROCESSOR_ID,
    contract.OCR_CPU_PROCESSOR_ID,
    contract.OCR_GPU_PROCESSOR_ID,
))
def test_saved_historical_original_text_is_returned_and_bad_bytes_are_local_unavailable(
    harness: Harness, processor_id: str,
) -> None:
    write_blank_image(harness.library / "saved.jpg")
    harness.scan()
    asset_id = harness.asset_id("saved.jpg")
    artifact = _seed_historical_current(harness, asset_id, processor_id)
    app = _app(harness)

    async def scenario() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            session = await client.get("/api/v1/session", params={"token": "p4-ocr-display-session"})
            assert session.status_code == 200
            response = await client.get(f"/api/v1/assets/{asset_id}/ocr-text")
            assert response.status_code == 200
            good = response.json()
            from photo_search.artifacts.store import MAX_ARTIFACT_BYTES
            with harness.catalog.write() as connection:
                connection.execute(
                    "UPDATE artifacts SET byte_size=? WHERE artifact_id=?",
                    (MAX_ARTIFACT_BYTES + 1, artifact["artifact_id"]),
                )
            oversized = await client.get(f"/api/v1/assets/{asset_id}/ocr-text")
            with harness.catalog.write() as connection:
                connection.execute(
                    "UPDATE artifacts SET byte_size=? WHERE artifact_id=?",
                    (artifact["byte_size"], artifact["artifact_id"]),
                )
            current_before = dict(harness.catalog.read_one(
                "SELECT ocr_document_id, text_revision, processor_id, input_revision, artifact_id "
                "FROM asset_ocr_text WHERE asset_id=?", (asset_id,),
            ))
            jobs_before = harness.catalog.read_one("SELECT COUNT(*) AS total FROM jobs")["total"]
            artifact_path = harness.data_root / str(artifact["storage_uri"])
            artifact_path.write_bytes(b"broken")
            damaged = await client.get(f"/api/v1/assets/{asset_id}/ocr-text")
            current_after = dict(harness.catalog.read_one(
                "SELECT ocr_document_id, text_revision, processor_id, input_revision, artifact_id "
                "FROM asset_ocr_text WHERE asset_id=?", (asset_id,),
            ))
            jobs_after = harness.catalog.read_one("SELECT COUNT(*) AS total FROM jobs")["total"]
            with harness.catalog.write() as connection:
                connection.execute("UPDATE assets SET sha256=? WHERE asset_id=?", ("b" * 64, asset_id))
            stale = await client.get(f"/api/v1/assets/{asset_id}/ocr-text")
            return (good, oversized.json(), damaged.json(), stale.json(),
                    current_before == current_after, jobs_before == jobs_after)

    try:
        good, oversized, damaged, stale, pointers_unchanged, jobs_unchanged = asyncio.run(scenario())
    finally:
        app.state.api.close()
    assert set(good) == {"status", "text", "truncated"}
    assert good["status"] == "available"
    assert good["text"] == "OCR historical 1280"
    assert good["truncated"] is False
    assert oversized == {"status": "unavailable", "text": "", "truncated": False}
    assert damaged == {"status": "unavailable", "text": "", "truncated": False}
    assert stale == {"status": "none", "text": "", "truncated": False}
    assert pointers_unchanged and jobs_unchanged, "GET must not rewrite OCR state or enqueue work"


def test_absent_ocr_is_none_and_missing_asset_remains_404(harness: Harness) -> None:
    write_blank_image(harness.library / "empty.jpg")
    harness.scan()
    asset_id = harness.asset_id("empty.jpg")
    app = _app(harness)

    async def scenario() -> tuple[int, dict[str, Any], int]:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            unauthorized = await client.get(f"/api/v1/assets/{asset_id}/ocr-text")
            assert (await client.get("/api/v1/session", params={"token": "p4-ocr-display-session"})).status_code == 200
            empty = await client.get(f"/api/v1/assets/{asset_id}/ocr-text")
            missing = await client.get("/api/v1/assets/999999/ocr-text")
            return unauthorized.status_code, empty.status_code, empty.json(), missing.status_code

    try:
        unauthorized_status, status, payload, missing_status = asyncio.run(scenario())
    finally:
        app.state.api.close()
    assert status == 200
    assert unauthorized_status == 401
    assert payload == {"status": "none", "text": "", "truncated": False}
    assert missing_status == 404


def test_ocr_text_limit_stops_only_at_utf8_character_boundary() -> None:
    from photo_search.api.queries import OCR_TEXT_MAX_BYTES
    from photo_search.api.routes import _bounded_ocr_text

    text, truncated = _bounded_ocr_text("字" * (OCR_TEXT_MAX_BYTES // 3 + 3))
    assert truncated is True
    assert len(text.encode("utf-8")) <= OCR_TEXT_MAX_BYTES
    assert text.encode("utf-8").decode("utf-8") == text
    assert len(text) * 3 == len(text.encode("utf-8"))
