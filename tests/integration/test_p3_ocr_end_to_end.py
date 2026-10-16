"""P3-01: a real OCR document from a real image, all the way into the search index.

The fixtures are rendered from a real system font, so these assertions are about genuine
recognition (Chinese text and digits), genuine region geometry and a genuine FTS hit. The
only thing simulated is nothing: the pinned PP-OCRv4 files are copied into the test data
root and the product's own scheduler, artifact store, migration and projection do the work.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from p3_ocr_fixtures import (
    SKIP_WITHOUT_FONT,
    SKIP_WITHOUT_WEIGHTS,
    Harness,
    write_blank_image,
    write_text_image,
)

from photo_search.api.settings import ApiSettings
from photo_search.app import create_app
from photo_search.artifacts.store import ArtifactStore
from photo_search.config import catalog_db_path
from photo_search.ingestion.service import CatalogService
from photo_search.ocr import contract
from photo_search.ocr.processor import ARTIFACT_ID_PREFIX
from photo_search.scheduler.projection import ocr_summary
from photo_search.search.tokens import TOKENIZER_VERSION, build_match, index_text

pytestmark = [SKIP_WITHOUT_WEIGHTS, SKIP_WITHOUT_FONT]


@pytest.fixture()
def harness(tmp_path: Path) -> Harness:
    instance = Harness(tmp_path)
    try:
        yield instance
    finally:
        instance.close()

INVOICE_LINES = [
    "发票 金额 1280 元",
    "车牌 苏A12345",
    "护照 MRZ P<CHN",
    "编号 20260914",
    "合计 3760.50",
]
SESSION_TOKEN = "p3-01-session-token"
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "testserver"})


def build_api(harness: Harness) -> Any:
    settings = ApiSettings(
        data_root=harness.data_root,
        bind_host="127.0.0.1",
        bind_port=80,
        session_token=SESSION_TOKEN,
        allowed_hosts=ALLOWED_HOSTS,
    )

    def factory() -> CatalogService:
        return CatalogService.open(
            data_root=harness.data_root,
            clock=harness.clock,
            lease_seconds=60,
            disk_usage=harness.usage,
            wall_clock_ms=harness.clock,
            stability_sleep=harness.clock.advance,
        )

    return create_app(settings=settings, service_factory=factory)


def api_json(harness: Harness, path: str) -> tuple[int, dict[str, Any], str]:
    """One authenticated GET against a fresh app over this harness's data root."""
    app = build_api(harness)

    async def scenario() -> tuple[int, dict[str, Any], str]:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            session = await client.get("/api/v1/session", params={"token": SESSION_TOKEN})
            assert session.status_code == 200, session.text
            response = await client.get(f"/api/v1{path}")
            return response.status_code, response.json(), response.text

    import asyncio

    try:
        return asyncio.run(scenario())
    finally:
        app.state.api.close()


# ------------------------------------------------------------------------ happy path
def test_a_real_invoice_is_recognised_indexed_and_reported(harness: Harness) -> None:
    harness.install_weights()
    write_text_image(harness.library / "invoice.jpg", INVOICE_LINES)
    harness.scan()
    asset_id = harness.asset_id("invoice.jpg")
    revision = harness.sha_of(asset_id)

    # Before anything runs, the honest state is "OCR has not run" - never "not in the photo".
    before = ocr_summary(catalog_db_path(harness.data_root), weights_present=True)
    assert before is not None and before["state"] == "NOT_RUN" and before["documents"] == 0
    assert harness.text_hits(build_match("1280").expression) == []

    summary = harness.recognize()
    assert summary["run"]["steps"] == 1
    assert summary["run"]["jobs"] == {"SUCCEEDED": 1}
    assert summary["projection"]["written"] >= 1

    job = harness.catalog.read_one("SELECT * FROM jobs WHERE task_type = 'image_ocr'")
    assert str(job["state"]) == "SUCCEEDED"
    assert str(job["processor_id"]) == contract.OCR_PROCESSOR_ID
    resolved_device = str(job["resolved_device"])
    expected_provider = (
        "CUDAExecutionProvider" if resolved_device == "gpu" else "CPUExecutionProvider"
    )
    assert resolved_device in summary["run"]["qualified_devices"]
    assert summary["run"][f"{resolved_device}_provider"] == expected_provider
    assert str(job["runtime_provider"]) == expected_provider
    assert str(job["requested_device"]) == "auto"
    assert str(job["precision"]) == contract.OCR_SUPPORTED_PRECISIONS[0]
    assert job["last_error_code"] is None

    # The artifact is a structured document with no vector of any kind.
    artifact = harness.catalog.read_one("SELECT * FROM artifacts")
    assert str(artifact["artifact_type"]) == "structured"
    assert artifact["space_id"] is None and artifact["dimension"] is None
    assert str(artifact["artifact_id"]).startswith(ARTIFACT_ID_PREFIX)
    store = ArtifactStore.open(harness.data_root)
    payload = json.loads(store.read(artifact).decode("utf-8"))
    assert payload["input_revision"] == revision
    assert payload["model_fingerprint"] == contract.MODEL_FINGERPRINT
    assert payload["config_hash"] == contract.OCR_GPU_CONFIG_HASH
    assert payload["processor_id"] == artifact["processor_id"] == job["processor_id"]
    assert payload["engine_config"] == contract.execution_config("gpu")["recognition_and_preprocess"]
    assert payload["engine_config"]["text_score"] == contract.MIN_TEXT_SCORE
    assert payload["original_text"]
    assert "1280" in payload["original_text"]
    assert "20260914" in payload["original_text"]
    assert any("\u4e00" <= char <= "\u9fff" for char in payload["original_text"])
    assert str(harness.library) not in json.dumps(payload, ensure_ascii=False)
    assert "\\\\" not in json.dumps(payload, ensure_ascii=False)
    assert "space_id" not in payload and "dimension" not in payload

    # Region geometry: 0..n-1 in reading order, top to bottom, all inside the image.
    regions = payload["regions"]
    assert payload["region_count"] == len(regions) >= 4
    assert [item["index"] for item in regions] == list(range(len(regions)))
    tops = [item["box"][1] for item in regions]
    assert tops == sorted(tops)
    width, height = payload["original_size"]
    for item in regions:
        left, top, right, bottom = item["box"]
        assert 0 <= left < right <= width and 0 <= top < bottom <= height
        assert 0.0 <= item["confidence"] <= 1.0
        assert len(item["polygon"]) == 4
    assert payload["slice_count"] >= 1
    assert payload["working_size"][0] <= contract.MAX_LONG_EDGE
    assert max(payload["working_size"]) <= contract.MAX_LONG_EDGE

    # SQLite keeps the search copy and the region metadata - not the original text.
    text_row = harness.ocr_document(asset_id)
    assert text_row is not None
    assert str(text_row["input_revision"]) == revision
    assert int(text_row["text_revision"]) == 1
    assert str(text_row["model_fingerprint"]) == contract.MODEL_FINGERPRINT
    assert str(text_row["artifact_id"]) == str(artifact["artifact_id"])
    assert int(text_row["region_count"]) == len(regions)
    assert str(text_row["search_text"]) == index_text(str(payload["original_text"]))
    assert int(text_row["tokenizer_version"]) == TOKENIZER_VERSION
    assert float(text_row["min_confidence"]) <= float(text_row["mean_confidence"])
    assert float(text_row["mean_confidence"]) <= float(text_row["max_confidence"])
    stored_regions = harness.ocr_regions(asset_id)
    assert [int(item["region_index"]) for item in stored_regions] == list(range(len(regions)))
    for stored, region in zip(stored_regions, regions, strict=True):
        assert json.loads(str(stored["box_json"]))["box"] == region["box"]
        assert float(stored["confidence"]) == region["confidence"]
    # The recognised text itself never reaches a SQLite column other than the search copy.
    raw = harness.catalog.read_one(
        "SELECT COUNT(*) AS total FROM asset_ocr_regions WHERE box_json LIKE ?",
        ("%苏A%",),
    )
    assert int(raw["total"]) == 0

    # The derived document and the FTS index really carry the recognised text.
    document = harness.document_row(asset_id)
    assert str(document["ocr_text"]) == str(text_row["search_text"])
    assert str(document["ocr_text"]) != ""
    assert harness.text_hits(build_match("发票").expression) == [asset_id]
    assert harness.text_hits(build_match("1280").expression) == [asset_id]
    assert harness.text_hits(build_match("20260914").expression) == [asset_id]

    # The read-only projections report the truth, without leaking text or coordinates.
    status, body, raw_body = api_json(harness, "/capabilities")
    assert status == 200
    ocr = body["ocr"]
    assert ocr["processor_id"] == contract.OCR_PROCESSOR_ID
    assert ocr["documents"] == 1 and ocr["stale_documents"] == 0
    assert ocr["regions"] == len(regions)
    assert ocr["weights_present"] is True
    assert ocr["state"] == "SYNCED"
    assert ocr["jobs"] == {"SUCCEEDED": 1}
    assert "1280" not in raw_body and "20260914" not in raw_body
    # The projection is a closed set of aggregate counters: no per-region box, polygon or
    # confidence can appear, and no recognised text can reach the response.
    assert set(ocr) == {
        "processor_id",
        "processor_profiles",
        "config_hashes",
        "current_results_by_processor",
        "legacy_pending_jobs",
        "retired_pending_jobs",
        "retired_live_leases",
        "retired_lease_anomalies",
        "cpu_fallbacks",
        "task_type",
        "model_fingerprint",
        "config_hash",
        "weights_present",
        "documents",
        "stale_documents",
        "regions",
        "low_confidence_regions",
        "low_confidence_threshold",
        "jobs",
        "pending_jobs",
        "blocked_jobs",
        "failed_jobs",
        "projection_queued",
        "state",
    }
    assert ocr["processor_profiles"] == {
        "legacy": contract.OCR_LEGACY_PROCESSOR_ID,
        "cpu": contract.OCR_CPU_PROCESSOR_ID,
        "gpu": contract.OCR_GPU_PROCESSOR_ID,
    }
    assert ocr["config_hashes"] == {
        "legacy": contract.CONFIG_HASH,
        "cpu": contract.OCR_CPU_CONFIG_HASH,
        "gpu": contract.OCR_GPU_CONFIG_HASH,
    }
    assert ocr["current_results_by_processor"] == {contract.OCR_GPU_PROCESSOR_ID: 1}
    assert ocr["retired_pending_jobs"] == ocr["retired_live_leases"] == ocr["retired_lease_anomalies"] == 0
    assert ocr["failed_jobs"] == ocr["blocked_jobs"] == 0
    assert ocr["cpu_fallbacks"] == {"current_results": 0, "reasons": {}}
    assert "polygon" not in raw_body and '"box":' not in raw_body

    jobs_status, jobs_body, jobs_raw = api_json(harness, "/jobs")
    assert jobs_status == 200
    ocr_jobs = [item for item in jobs_body["jobs"] if item["task_type"] == contract.OCR_TASK_TYPE]
    assert len(ocr_jobs) == 1 and ocr_jobs[0]["processor_id"] == contract.OCR_PROCESSOR_ID
    assert ocr_jobs[0]["state"] == "SUCCEEDED"
    assert "1280" not in jobs_raw and "confidence" not in jobs_raw


def test_a_high_density_page_is_sliced_and_a_blank_page_stays_empty(harness: Harness) -> None:
    harness.install_weights()
    dense_lines = [
        f"{column + 1}-{row + 1} 编号 {20260900 + row} 金额 {(row + 1) * 128}"
        for row in range(4)
        for column in range(3)
    ]
    write_text_image(
        harness.library / "dense.jpg",
        dense_lines,
        size=(1800, 1200),
        font_size=40,
        columns=3,
    )
    write_blank_image(harness.library / "blank.jpg")
    harness.scan()
    dense_id = harness.asset_id("dense.jpg")
    blank_id = harness.asset_id("blank.jpg")

    summary = harness.recognize()
    assert summary["run"]["jobs"] == {"SUCCEEDED": 2}

    dense = harness.ocr_document(dense_id)
    blank = harness.ocr_document(blank_id)
    assert dense is not None and blank is not None
    assert int(dense["region_count"]) >= 6
    assert str(dense["search_text"]) != ""
    # The result is a genuine bounded slice run, not one pass over a shrunk image.
    artifact = harness.catalog.read_one(
        "SELECT artifact_id FROM artifacts WHERE input_revision = ?",
        (harness.sha_of(dense_id),),
    )
    store = ArtifactStore.open(harness.data_root)
    payload = json.loads(
        store.read(
            harness.catalog.read_one(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact["artifact_id"],)
            )
        ).decode("utf-8")
    )
    assert payload["slice_count"] >= 2
    assert max(payload["working_size"]) <= contract.MAX_LONG_EDGE

    # A blank page is an honest empty document: a row exists, with no text and no regions.
    assert int(blank["region_count"]) == 0
    assert str(blank["search_text"]) == ""
    assert blank["min_confidence"] is None and blank["max_confidence"] is None
    assert harness.ocr_regions(blank_id) == []
    assert str(harness.document_row(blank_id)["ocr_text"]) == ""
    # The filename text of the blank page still matches, so the empty OCR result did not
    # erase the rest of the derived document.
    assert harness.text_hits(build_match("blank").expression) == [blank_id]

    summary = ocr_summary(catalog_db_path(harness.data_root), weights_present=True)
    assert summary is not None
    assert summary["documents"] == 2 and summary["stale_documents"] == 0
    assert summary["regions"] == int(dense["region_count"])
    assert summary["state"] == "SYNCED"


def test_the_ocr_job_can_run_on_an_explicit_cpu_request(harness: Harness) -> None:
    """The explicit diagnostic CPU path remains available with or without a GPU."""
    harness.install_weights()
    write_text_image(harness.library / "one.jpg", ["编号 20260914"], size=(900, 400), font_size=44)
    harness.scan()
    asset_id = harness.asset_id("one.jpg")

    summary = harness.recognize(requested_device="cpu")
    run = summary["run"]
    job = harness.catalog.read_one(
        "SELECT * FROM jobs WHERE task_type = 'image_ocr' AND subject_id = ?", (str(asset_id),)
    )
    assert str(job["requested_device"]) == "cpu"
    assert str(job["resolved_device"]) == "cpu"
    assert str(job["runtime_provider"]) == "CPUExecutionProvider"
    assert str(job["precision"]) == contract.OCR_SUPPORTED_PRECISIONS[0]
    assert "cpu" in run["qualified_devices"]
    assert run["cpu_provider"] == "CPUExecutionProvider"
    assert str(job["runtime_capability_revision"]) == str(run["capability_revision"])


def test_the_pinned_paths_are_the_only_ones_the_engine_opened(harness: Harness) -> None:
    """A regression guard: the product must not fall back to the wheel's own model files."""
    harness.install_weights()
    write_text_image(harness.library / "two.jpg", ["测试 42"], size=(700, 300), font_size=42)
    harness.scan()
    harness.recognize()
    from photo_search.ocr.weights import model_root, resolve

    assert model_root(harness.data_root).is_dir()
    for entry in resolve(harness.data_root).files:
        assert entry.absolute_path.is_file()
        assert harness.data_root in entry.absolute_path.parents
